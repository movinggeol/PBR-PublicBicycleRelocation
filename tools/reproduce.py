"""데이터도 API 키도 없이 저장소를 통째로 재현한다 — 심사·리뷰용 **단일 명령**.

    python tools/reproduce.py

왜 명령 하나여야 하는가. README에는 오래도록 이런 절차가 적혀 있었다.

    python tools/make_sample_data.py --now "데모"
    python run_pipeline.py --skip-api --skip-eda --skip-map --now "데모"

**이 절차는 합성 데이터를 쓰지 않는다.** 두 가지가 겹쳐 있었다.

  ① `--skip-api`는 재고 스냅샷을 **직전 실행에서 물려받는다**(inherit_snapshot).
     그래서 방금 만든 합성 재고 90곳이 아니라 **실데이터 1,361곳**이 들어갔다.
  ② `--period`·`--raw-file`을 안 주면 `project_config`의 기본값이 쓰이는데,
     그 기본값은 `data/raw_data/…(25년11월).csv` — **저장소에 없는 106MB 실파일**이다.

실데이터를 가진 PC에서는 ①·② 모두 **오류 없이 통과했고**, 깨끗이 복제한 PC에서는
②에서 죽었다. 즉 재현 절차가 *"되는 곳에서는 딴 걸 하고, 안 되는 곳에서는 죽는"*
상태였다(2026-08-31에 실제로 확인). 절차를 문서에 글로 적어 두면 코드가 바뀔 때
같이 낡는다 — 그래서 **실행 가능한 하나의 파일**로 옮기고 테스트를 붙였다.

이 스크립트가 지키는 것 셋:

  · **격리** — DB는 기본적으로 별도 파일(`data/재현.db`)을 쓴다. 실 DB를 건드리지 않는다.
  · **자기 검증** — 파이프라인이 정말 합성 대여소를 봤는지 **대여소 수를 대조한다.**
    위 ①은 조용히 틀렸기 때문에, 조용히 틀릴 수 없게 만드는 것이 핵심이다.
  · **정리** — 만든 산출물을 지운다(`--keep`으로 남길 수 있다).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from project_config import DATA_ROOT, PROJECT_ROOT  # noqa: E402
from tools.make_sample_data import generate                 # noqa: E402

# 실행 라벨. 실데이터 라벨과 겹치지 않게 이름을 못 박는다 — 겹치면 사용자의
# 산출물을 덮어쓰고, 마지막의 정리 단계가 그것을 지운다.
LABEL = "재현 데모"
DURATIONS = "_05_10,_10_15,_15_20"
DEFAULT_DB = DATA_ROOT / "재현.db"

PP_ROOT = DATA_ROOT / "pp_data"
STOCK_CSV = PP_ROOT / "대여소별 재고" / f"대여소별_자전거대수 ({LABEL}).csv"
INFO_CSV = PP_ROOT / "대여소 정보" / f"st_info ({LABEL}).csv"


def run_pipeline(raw_path: Path, db_path: Path, durations: str) -> None:
    """합성 재고 CSV로 step0~step4를 돈다.

    `--skip-fetch`이지 `--skip-api`가 아니다 — 라이브 API 호출만 빼고 나머지
    수집 단계는 **방금 만든 합성 재고 CSV를 읽어** 돌게 한다. `--skip-api`를 쓰면
    직전 실행의 실데이터 스냅샷을 물려받아 합성 데이터가 통째로 무시된다.
    """
    command = [
        sys.executable, "run_pipeline.py",
        "--skip-fetch", "--skip-eda", "--skip-map",
        "--now", LABEL, "--period", LABEL,
        "--duration", durations,
        "--raw-file", str(raw_path),
        "--day-type", "weekday",
    ]
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               PBR_DB_PATH=str(db_path))
    print("\n$ " + " ".join(command[1:]) + "\n")
    completed = subprocess.run(command, cwd=PROJECT_ROOT, env=env)
    if completed.returncode != 0:
        raise SystemExit(f"파이프라인이 실패했습니다 (exit {completed.returncode})")


def verify(expected_stations: int) -> None:
    """파이프라인이 **정말 합성 데이터를 봤는지** 대여소 수로 대조한다.

    이 검증이 이 스크립트의 존재 이유다. 옛 절차의 결함은 오류가 아니라
    **조용한 대체**였다 — 합성 90곳을 만들고 실데이터 1,361곳을 돌렸는데
    아무 데서도 티가 나지 않았다. 수를 맞춰 보면 즉시 드러난다.
    """
    if not INFO_CSV.exists():
        raise SystemExit(f"대여소 정보 산출물이 없습니다: {INFO_CSV}")

    actual = len(pd.read_csv(INFO_CSV, encoding="utf-8-sig"))
    if actual != expected_stations:
        raise SystemExit(
            "\n".join([
                "합성 데이터가 쓰이지 않았습니다 — 재현이 성립하지 않습니다.",
                f"  만든 합성 대여소 : {expected_stations}곳",
                f"  파이프라인이 본 것: {actual}곳  ({INFO_CSV.name})",
                "  `--skip-api`(전부 건너뛰기)가 직전 실행의 실데이터 스냅샷을",
                "  물려받았을 때 이렇게 됩니다. 이 스크립트는 `--skip-fetch`를 씁니다.",
            ]))
    print(f"\n[검증] 파이프라인이 합성 대여소 {actual}곳을 그대로 봤습니다.")


def report(db_path: Path, durations: str) -> None:
    """무엇이 나왔는지 보여 준다 — 결품 시간이 실제로 줄었는지까지."""
    import db as db_mod

    with db_mod.session(str(db_path)) as conn:
        kpi = pd.read_sql(
            "SELECT duration, stockout_hours_before, stockout_hours_after,"
            "       bikes_moved, total_distance_km"
            "  FROM kpi_summary WHERE run_label = ?", conn, params=[LABEL])

    if kpi.empty:
        print("\n[주의] kpi_summary가 비었습니다 — 지표 단계를 확인하세요.")
        return

    print("\n" + "=" * 68)
    print(f"재현 결과 ({LABEL})  — 결품 시간은 낮을수록 좋습니다")
    print("=" * 68)
    print(f"{'회차':<10}{'결품 전':>10}{'결품 후':>10}{'처리 대수':>12}{'이동 km':>12}")
    for row in kpi.itertuples():
        before = row.stockout_hours_before
        after = row.stockout_hours_after
        print(f"{row.duration:<10}"
              f"{'-' if before is None else f'{before:.2f}h':>10}"
              f"{'-' if after is None else f'{after:.2f}h':>10}"
              f"{row.bikes_moved or 0:>12}"
              f"{(row.total_distance_km or 0):>12.1f}")
    print("=" * 68)
    print(f"DB       : {db_path}")
    print(f"산출물   : {PP_ROOT}  (라벨 '{LABEL}')")
    print("웹으로 보려면:")
    print(f'  set PBR_DB_PATH={db_path}  &&  python -m webapp')


def cleanup(db_path: Path, raw_path: Path) -> None:
    """이 실행이 만든 것만 지운다. 실데이터 라벨은 건드리지 않는다."""
    removed = 0
    for path in PP_ROOT.rglob(f"*{LABEL}*"):
        if path.is_file():
            path.unlink()
            removed += 1
    for path in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm"), raw_path):
        if path.exists():
            path.unlink()
            removed += 1
    print(f"\n[정리] 파일 {removed}개를 지웠습니다 (--keep으로 남길 수 있습니다).")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="합성 데이터로 파이프라인 전체를 재현한다 (데이터·API 키 불필요)")
    parser.add_argument("--stations", type=int, default=90, help="합성 대여소 수")
    parser.add_argument("--days", type=int, default=28,
                        help="합성 기간(일). 평일·휴일이 모두 들어가도록 4주가 기본")
    parser.add_argument("--duration", default=DURATIONS, help="돌릴 회차")
    parser.add_argument("--db", default=str(DEFAULT_DB),
                        help=f"쓸 DB 경로 (기본 {DEFAULT_DB.name} — 실 DB를 건드리지 않는다)")
    parser.add_argument("--keep", action="store_true", help="산출물과 DB를 지우지 않는다")
    args = parser.parse_args()

    db_path = Path(args.db)
    raw_path = DATA_ROOT / "raw_data" / f"합성_대여이력 ({LABEL}).csv"
    raw_path.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 68)
    print("PBR 재현 — 합성 데이터로 step0~step4를 돕니다")
    print("=" * 68)

    generate(now=LABEL, period=LABEL, stations=args.stations, days=args.days,
             raw_path=raw_path)

    try:
        run_pipeline(raw_path, db_path, args.duration)
        verify(args.stations)
        report(db_path, args.duration)
    finally:
        if args.keep:
            print(f"\n[보존] 산출물과 DB를 남겼습니다: {db_path}")
        else:
            cleanup(db_path, raw_path)


if __name__ == "__main__":
    main()
