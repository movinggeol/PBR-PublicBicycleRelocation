"""타슈 재고 시계열 수집기 — 평일 09~17시, 10분 간격 (docs/구현/COLLECTOR.md).

**한 번 실행 = 한 틱.** 창 밖이면 아무 것도 하지 않고 끝난다. Windows 작업
스케줄러는 요일만 알고 **공휴일을 모르므로**, 휴일을 거르는 실질적 방어선은
여기 창 가드다.

받아 오는 일 자체는 루트의 tashu.py가 한다 — step0 수집·웹의 실시간 재고 대조와
같은 클라이언트를 써야 컬럼 규약(x_pos가 위도)이 두 곳으로 갈리지 않는다.

    python tools/collect_stock.py            # 한 틱 (스케줄러가 부르는 형태)
    python tools/collect_stock.py --status   # 수집 현황만 (API 호출 안 함)
    python tools/collect_stock.py --loop     # 창이 끝날 때까지 상주

등록·일시정지·중지는 scripts/collector.ps1이 담당한다.
"""
from __future__ import annotations

import argparse
import csv
import ctypes
import sys
import time
from datetime import date, datetime, time as clock, timedelta
from pathlib import Path
from typing import Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

import db
import tashu
from project_config import DATA_ROOT, is_holiday

DEFAULT_WINDOW = "09:00-17:00"
DEFAULT_INTERVAL = 10

# 원천 관측이라 pp_data(산출물)가 아니라 raw_data에 둔다.
# 파이프라인 산출물이 아니므로 ensure_output_dirs()에 얹지 않고 여기서 만든다.
HISTORY_DIR = DATA_ROOT / "raw_data" / "재고이력"
LOG_NAME = "collect_log.csv"
LOG_HEADER = ("logged_at", "observed_at", "status", "stations", "detail")

# SetThreadExecutionState: 시스템 유휴 타이머를 리셋한다.
ES_SYSTEM_REQUIRED = 0x00000001


# ---- 창·격자 ----

TASK_NAME = "PBR재고수집"


def registered_args() -> Optional[dict]:
    """작업 스케줄러에 **실제로 등록된** 창·간격을 되읽는다. 없으면 None.

    이게 없으면 `--status`가 파라미터 **기본값**(09:00-17:00)으로 결측을 세서,
    창을 넓혀 등록해 둔 뒤에도 옛 기준으로 보고한다 — 07~22시로 등록했는데
    "하루 49틱 기대"라고 말하는 식이다. 실제로 그랬다(1.26.55).

    `scripts/collector.ps1`의 `Get-RegisteredArgs`가 하는 일과 **같은 것**이다.
    두 경로가 다른 답을 내면 어느 쪽을 믿어야 할지 알 수 없으므로 여기도 둔다.
    """
    import re
    import subprocess

    try:
        done = subprocess.run(
            ["schtasks", "/query", "/tn", TASK_NAME, "/xml", "ONE"],
            capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None

    text = ""
    for encoding in ("utf-16", "utf-8", "cp949"):
        try:
            text = done.stdout.decode(encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if "--window" not in text:
        return None

    found = {}
    if (m := re.search(r"--window\s+(\S+)", text)):
        found["window"] = m.group(1)
    if (m := re.search(r"--interval\s+(\d+)", text)):
        found["interval"] = int(m.group(1))
    return found or None


def parse_window(text: str) -> Tuple[clock, clock]:
    """'09:00-17:00' → (09:00, 17:00)."""
    try:
        start_text, end_text = text.split("-")
        start = datetime.strptime(start_text.strip(), "%H:%M").time()
        end = datetime.strptime(end_text.strip(), "%H:%M").time()
    except ValueError:
        raise SystemExit(f"수집 창을 읽을 수 없습니다: {text!r} (예: 09:00-17:00)")
    if start >= end:
        raise SystemExit(f"수집 창의 끝이 시작보다 늦어야 합니다: {text!r}")
    return start, end


def tick_of(stamp: datetime, start: clock, interval: int) -> datetime:
    """가장 가까운 틱 격자로 반올림한다.

    스케줄러는 09:00:03처럼 몇 초 늦게 깨우고, 부하가 걸리면 09:09:58에 깨우기도
    한다. 격자에 맞춰 두지 않으면 **날짜가 다른 같은 시각끼리 대조가 어긋난다** —
    시간대별 프로파일을 내는 것이 목적이므로 이 정렬이 데이터의 값어치를 정한다.
    """
    anchor = datetime.combine(stamp.date(), start)
    step = timedelta(minutes=interval)
    return anchor + round((stamp - anchor) / step) * step


def window_state(stamp: datetime, start: clock, end: clock, interval: int,
                 *, include_holidays: bool = False, holidays_only: bool = False
                 ) -> Tuple[Optional[datetime], bool, str]:
    """(틱, 수집해도 되는가, 건너뛰는 이유).

    요일 가드는 **어느 날에 받아올지**만 정한다. 셋 다 창 가드는 그대로 지킨다 —
    `--force`처럼 둘 다 풀어 버리면 등록한 창이 무의미해져 아무 시각에나 틱이
    들어오고 격자 대조가 깨진다.

    | 옵션 | 평일 | 휴일 | 쓰는 곳 |
    | --- | --- | --- | --- |
    | (없음) | ✅ | ❌ | A PC — 지금까지의 동작 |
    | `include_holidays` | ✅ | ✅ | 한 대로 전부 |
    | `holidays_only` | ❌ | ✅ | **B PC — 휴일만 맡는다** |

    `holidays_only`가 이기므로 둘을 같이 줘도 모순되지 않는다(argparse가 애초에
    막지만, 함수만 직접 부르는 경우를 위해 여기서도 정한다).

    ⚠️ 이것은 **관측을 남기는 범위**지 분석의 `--day-type`이 아니다. 평일과 휴일을
    섞어 통계를 내지 말라는 규약은 그대로다 — 거를 때는
    `db.load_stock_history(day_type=...)`을 쓴다(3장).
    """
    holiday = is_holiday(stamp.date())
    if holidays_only:
        if not holiday:
            return None, False, "평일(휴일만 수집)"
    elif holiday and not include_holidays:
        return None, False, "휴일(주말·공휴일)"
    tick = tick_of(stamp, start, interval)
    lower = datetime.combine(tick.date(), start)
    upper = datetime.combine(tick.date(), end)
    if not lower <= tick <= upper:
        return tick, False, f"수집 창({start:%H:%M}~{end:%H:%M}) 밖"
    return tick, True, ""


def expected_ticks(start: clock, end: clock, interval: int) -> int:
    """하루에 기대되는 틱 수(양 끝 포함)."""
    span = datetime.combine(date.min, end) - datetime.combine(date.min, start)
    return int(span.total_seconds() // 60 // interval) + 1


# ---- 절전 ----

def keep_awake() -> None:
    """시스템 유휴 타이머를 리셋해 창 동안 PC가 절전에 들지 않게 한다.

    한 번 부르면 타이머가 처음으로 돌아가므로, 10분마다 도는 틱이 절전 임계값을
    계속 밀어낸다. **임계값이 간격과 비슷하면(배터리 기본값 10분) 이것만으로는
    못 막는다** — 그때는 전원을 꽂아야 한다 (docs/구현/COLLECTOR.md 7장).

    부가 기능이라 실패해도 수집은 그대로 진행한다.
    """
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(ES_SYSTEM_REQUIRED)
    except (AttributeError, OSError):
        pass


# ---- 기록 ----

def write_log(observed_at: str, status: str, stations: int, detail: str = "") -> None:
    """수집 로그 한 줄. **결측을 구분하기 위해 실패도 반드시 남긴다.**

    이게 없으면 나중에 '이 시각은 API가 죽어서 없다'와 '재고가 정말 0이었다'를
    구분할 수 없다.
    """
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    path = HISTORY_DIR / LOG_NAME
    is_new = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if is_new:
            writer.writerow(LOG_HEADER)
        writer.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                         observed_at, status, stations, detail])


def append_csv_backup(tick: datetime, frame: pd.DataFrame) -> Path:
    """일별 CSV 백업(정본은 DB). 파일 하나가 하루치다."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    path = HISTORY_DIR / f"stock_{tick:%Y-%m-%d}.csv"
    rows = frame[["station_id", "stock"]].copy()
    rows.insert(0, "observed_at", tick.strftime("%Y-%m-%d %H:%M"))
    rows.to_csv(path, mode="a", header=not path.exists(),
                index=False, encoding="utf-8")
    return path


# ---- 수집 ----

def collect_once(tick: datetime, *, dry_run: bool = False) -> int:
    """한 틱을 받아 저장하고 저장한 행 수를 돌려준다. 실패는 TashuError로 올라간다."""
    frame = tashu.fetch_stations()
    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    observed_at = tick.strftime("%Y-%m-%d %H:%M")

    if dry_run:
        print(f"[모의] {observed_at} · {len(frame)}곳 — 저장하지 않았습니다.")
        return len(frame)

    with db.session() as conn:
        rows = db.save_stock_snapshot(conn, observed_at, frame, fetched_at)
        # 이름·좌표는 하루 한 번만. 그날 첫 틱이 실패했으면 다음 틱이 대신 남긴다.
        observed_on = tick.strftime("%Y-%m-%d")
        if not db.has_stock_master(conn, observed_on):
            db.save_stock_master(conn, observed_on, frame)

    append_csv_backup(tick, frame)
    return rows


def run_tick(stamp: datetime, start: clock, end: clock, interval: int,
             *, force: bool = False, dry_run: bool = False,
             include_holidays: bool = False, holidays_only: bool = False) -> int:
    """창 가드 → 수집 → 로그. 종료 코드를 돌려준다(0 성공·건너뜀, 1 실패)."""
    tick, allowed, reason = window_state(stamp, start, end, interval,
                                         include_holidays=include_holidays,
                                         holidays_only=holidays_only)
    if not allowed and not force:
        print(f"[건너뜀] {reason} — {stamp:%Y-%m-%d %H:%M}")
        return 0
    if tick is None:                      # --force로 휴일에 수집하는 경우
        tick = tick_of(stamp, start, interval)

    keep_awake()
    observed_at = tick.strftime("%Y-%m-%d %H:%M")
    try:
        rows = collect_once(tick, dry_run=dry_run)
    except tashu.TashuError as err:
        write_log(observed_at, "실패", 0, str(err))
        print(f"[실패] {observed_at} — {err}")
        return 1

    if dry_run:
        return 0                          # 안내는 collect_once가 이미 했다
    write_log(observed_at, "성공", rows)
    print(f"[수집] {observed_at} · {rows}곳")
    return 0


def run_loop(start: clock, end: clock, interval: int, *, dry_run: bool = False,
             include_holidays: bool = False, holidays_only: bool = False) -> int:
    """창이 끝날 때까지 상주하며 틱마다 수집한다."""
    now = datetime.now()
    opening = datetime.combine(now.date(), start)
    # 오늘 수집할 날이 아니면 기다릴 이유가 없다.
    collects_today = (is_holiday(now.date()) if holidays_only
                      else include_holidays or not is_holiday(now.date()))
    if collects_today and now < opening:
        wait = (opening - now).total_seconds()
        if wait > 3600:
            print(f"[대기 안 함] 수집 창 시작까지 {wait / 3600:.1f}시간 남았습니다.")
            return 0
        print(f"[대기] {start:%H:%M} 시작까지 {wait / 60:.0f}분")
        time.sleep(wait)

    while True:
        stamp = datetime.now()
        tick, allowed, reason = window_state(stamp, start, end, interval,
                                             include_holidays=include_holidays,
                                             holidays_only=holidays_only)
        if not allowed:
            print(f"[종료] {reason} — {stamp:%Y-%m-%d %H:%M}")
            return 0
        run_tick(stamp, start, end, interval, dry_run=dry_run,
                 include_holidays=include_holidays,
                 holidays_only=holidays_only)
        remaining = (tick + timedelta(minutes=interval) - datetime.now()).total_seconds()
        if remaining > 0:
            time.sleep(remaining)


# ---- 현황 ----

def coverage(start: clock, end: clock, interval: int) -> pd.DataFrame:
    """날짜별 수집 현황. 컬럼: 날짜, 틱, 기대, 결측, 상태.

    **로그가 아니라 DB의 기대 격자와 대조한다** — 절전으로 놓친 틱은 스크립트
    자체가 안 돌아 로그에도 안 남기 때문이다.
    """
    with db.session() as conn:
        ticks = db.stock_history_ticks(conn)
    columns = ["날짜", "틱", "기대", "결측", "상태"]
    if ticks.empty:
        return pd.DataFrame(columns=columns)

    stamps = pd.to_datetime(ticks["observed_at"])
    per_day = stamps.dt.strftime("%Y-%m-%d").value_counts().sort_index()
    target = expected_ticks(start, end, interval)
    today = date.today()
    now = datetime.now().time()

    rows = []
    for day, count in per_day.items():
        stamp = datetime.strptime(day, "%Y-%m-%d").date()
        missing = max(target - int(count), 0)
        if stamp == today and now < end:
            status = "수집 중"
        elif missing == 0:
            status = "온전"
        else:
            status = "결측"
        rows.append(dict(zip(columns, [day, int(count), target, missing, status])))
    return pd.DataFrame(rows)


def print_status(start: clock, end: clock, interval: int,
                 source: str = "") -> int:
    """사람이 읽는 수집 현황. API를 부르지 않는다."""
    target = expected_ticks(start, end, interval)
    print(f"PBR 재고 수집 현황 (창 {start:%H:%M}~{end:%H:%M} · 간격 {interval}분"
          f" · 하루 {target}틱)")
    if source:
        # 어느 기준으로 결측을 셌는지 밝힌다. 창이 틀리면 아래 표가 통째로 틀린다.
        print(f"  기준     : {source}")

    table = coverage(start, end, interval)
    log_path = HISTORY_DIR / LOG_NAME
    if table.empty:
        print("  누적     : 아직 수집한 데이터가 없습니다.")
        print(f"  로그     : {log_path}")
        return 0

    total_ticks = int(table["틱"].sum())
    print(f"  누적     : {len(table)}일 · {total_ticks}틱")
    print(f"  기간     : {table['날짜'].iloc[0]} ~ {table['날짜'].iloc[-1]}")

    intact = table.loc[table["상태"] == "온전", "날짜"].tolist()
    tail = f" (최근 {', '.join(intact[-5:])})" if intact else ""
    print(f"  온전한 날: {len(intact)}일{tail}")
    print("             ↳ 실험에 쓸 수 있는 날입니다.")
    print()
    print(table.to_string(index=False))

    if log_path.exists():
        last = pd.read_csv(log_path).tail(1)
        if not last.empty:
            row = last.iloc[0]
            detail = row["detail"] if pd.notna(row["detail"]) else ""
            print(f"\n  최근 로그: {row['observed_at']} · {row['status']}"
                  f" · {row['stations']}곳 {detail}")
    return 0


# ---- 진입점 ----

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="타슈 대여소 재고를 시계열로 수집한다 (docs/구현/COLLECTOR.md).")
    parser.add_argument("--window", default=DEFAULT_WINDOW,
                        help=f"수집 창. 기본 {DEFAULT_WINDOW}")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL,
                        help=f"틱 간격(분). 기본 {DEFAULT_INTERVAL}")
    parser.add_argument("--once", action="store_true",
                        help="한 틱만 수집하고 종료(기본 동작). 스케줄러가 쓰는 형태")
    parser.add_argument("--loop", action="store_true",
                        help="창이 끝날 때까지 상주하며 반복")
    parser.add_argument("--force", action="store_true",
                        help="창 밖·휴일에도 수집한다")
    days = parser.add_mutually_exclusive_group()
    days.add_argument("--include-holidays", action="store_true",
                      help="평일에 더해 휴일에도 수집한다(창은 그대로 지킨다)")
    days.add_argument("--holidays-only", action="store_true",
                      help="휴일에만 수집하고 평일은 건너뛴다. "
                           "두 번째 PC가 휴일을 맡는 구성용(COLLECTOR.md 11장)")
    parser.add_argument("--dry-run", action="store_true",
                        help="호출만 하고 저장하지 않는다")
    parser.add_argument("--status", action="store_true",
                        help="수집 현황만 출력한다(API를 부르지 않는다)")
    return parser


def resolve_window(args, argv: Optional[Sequence[str]]) -> Tuple[str, int, str]:
    """`--status`가 쓸 창·간격을 정한다. (창, 간격, 출처) 를 돌려준다.

    우선순위는 **사용자가 직접 준 값 → 등록된 작업 → 기본값**이다. 기본값으로
    떨어졌으면 그 사실을 말해야 한다 — 틀린 기준으로 센 결측을 맞는 것처럼
    보여 주는 것이 가장 나쁘다.
    """
    given = set(argv if argv is not None else sys.argv[1:])
    gave_window = "--window" in given
    gave_interval = "--interval" in given
    if gave_window and gave_interval:
        return args.window, args.interval, "직접 지정"

    if (found := registered_args()):
        window = args.window if gave_window else found.get("window", args.window)
        interval = args.interval if gave_interval else found.get("interval", args.interval)
        return window, interval, f"등록된 작업 '{TASK_NAME}'"

    return args.window, args.interval, "기본값(등록된 작업을 찾지 못했습니다)"


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.status:
        # 수집 창은 등록할 때 정해진다. 기본값으로 세면 결측이 통째로 어긋난다.
        window, interval, source = resolve_window(args, argv)
        if interval <= 0:
            raise SystemExit(f"틱 간격은 1분 이상이어야 합니다: {interval}")
        start, end = parse_window(window)
        return print_status(start, end, interval, source)

    start, end = parse_window(args.window)
    if args.interval <= 0:
        raise SystemExit(f"틱 간격은 1분 이상이어야 합니다: {args.interval}")

    if args.loop:
        return run_loop(start, end, args.interval, dry_run=args.dry_run,
                        include_holidays=args.include_holidays,
                        holidays_only=args.holidays_only)
    return run_tick(datetime.now(), start, end, args.interval,
                    force=args.force, dry_run=args.dry_run,
                    include_holidays=args.include_holidays,
                    holidays_only=args.holidays_only)


if __name__ == '__main__':
    # 종료 코드를 그대로 넘긴다 — 작업 스케줄러의 '마지막 실행 결과'가 이 값이다.
    raise SystemExit(main())
