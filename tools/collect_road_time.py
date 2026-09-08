"""TMAP 실도로 소요시간을 **매일 같은 구간으로** 반복 수집한다 (1.26.39).

## 왜 파이프라인 실행분으로는 안 되는가

`road_leg`에는 이미 파이프라인이 지도를 그리며 받은 실측이 쌓인다. 그런데 그것으로는
**"이동시간 모형이 계절·요일에 안정적인가"를 물을 수 없다.** 계획이 매일 바뀌면
구간도 매일 바뀌므로, 계수가 흔들렸을 때 그것이

    ① 교통 상황이 달라져서인지        ← 알고 싶은 것
    ② 잰 구간이 달라져서인지          ← 잡음

를 구분할 방법이 없기 때문이다. 그래서 **고정 패널**을 쓴다 — 한 번 정한 구간
목록을 매일 그대로 다시 잰다. 그러면 날짜 사이의 차이는 오직 교통 상황 차이다.

## 패널 설계

- **차고지에서 시작해 차고지로 돌아온다.** 실제 경로에서 차고지 왕복이 총
  이동거리의 61~65%를 차지하므로(EXPERIMENTS.md 8장), 차고지 구간이 빠진 패널은
  실제를 대표하지 못한다.
- **거리 구간을 골고루 채운다.** 5-F장에서 확인했듯 배율은 거리에 크게 의존하므로
  (0.5km 미만 유효 5.6km/h vs 10km 이상 23.1km/h), 짧은 구간만 모이면 고정비
  계수가, 긴 구간만 모이면 속도 계수가 못 잡힌다. 목표 거리를 순환시키며
  **그 거리에 가장 가까운 대여소**를 다음 지점으로 고른다.
- **한 사슬 = TMAP 호출 1회.** 경유지 30개 상한 안에 들도록 사슬을 끊으면
  routeSequential30(한도가 100과 따로 잡힌다)로 처리돼 쿼터를 아낀다.

패널은 `data/road_panel.json`에 남고, **있으면 다시 만들지 않는다** —
수집 도중에 패널이 바뀌면 그때까지 쌓은 것이 비교 불가능해진다.

## 시각 처리

`startTime`은 **그 시각의 교통량**을 정한다. 파이프라인과 똑같이
`start_time_for(duration)`(다음 평일 + 회차 첫 시각)을 쓴다 — 우리가 검증하려는
것이 파이프라인이 실제로 소비하는 값이기 때문이다. 따라서 이 수집기는
**하루 중 아무 때나 돌려도 된다**(재고 수집기와 달리 창 가드가 없다).

⚠️ 금·토·일에 돌리면 셋 다 '다음 월요일'을 가리켜 같은 값이 나온다. 요일을
고르게 모으려면 **평일에 돌려라**(설치 스크립트가 월~금으로 잡는다).

## 실행

    python tools/collect_road_time.py --status          # 쌓인 현황
    python tools/collect_road_time.py --dry-run         # 호출 계획만 확인
    python tools/collect_road_time.py                   # 오늘치 수집
    python tools/collect_road_time.py --if-needed       # 모자란 회차만 (스케줄러용)
    python tools/collect_road_time.py --durations _05_10,_10_15
    python tools/collect_road_time.py --rebuild-panel   # ⚠️ 패널을 새로 만든다

저장은 `road_leg`, `run_label`은 `roadprobe-YYYY-MM-DD`다. 같은 날 다시 돌리면
그 날짜 행을 지우고 다시 넣는다(멱등).

## `--if-needed` — 켜져 있는 시간대에 한 번만 (1.26.105)

원래 스케줄은 **평일 03:30 한 번**이었다. 새벽에 PC를 켜 두지 않는 환경에서는
`StartWhenAvailable`이 아침에 뒤늦게 깨우는데, 그마저 놓치면 그날을 통째로 잃는다
(실제로 09-03이 그렇게 비었다).

그래서 **하루에 여러 번 깨우되 필요한 때만 호출**하게 했다. 이 옵션은 셋을 본다.

  1. **주말이면 아무것도 안 한다.** `start_time_for()`가 '다음 평일'을 쓰므로
     금·토·일은 셋 다 '다음 월요일'을 가리킨다 — 토요일에 받으면 금요일과 같은
     교통량을 '다른 날'로 세게 된다. 로그온 트리거는 주말에도 깨므로 여기서 막는다.
  2. **이미 채운 회차는 건너뛴다.** 회차마다 100구간(사슬 5 × 20구간)이 다 있으면
     다시 부르지 않는다. 네 회차가 다 차 있으면 **TMAP을 한 번도 부르지 않고** 끝난다.
  3. **모자란 회차만 채운다.** 한도 소진·네트워크 실패로 잘린 회차가 있으면 그것만
     다시 부른다 — 다 받은 회차를 지우고 새로 받지 않는다(호출을 두 배로 쓰게 된다).

즉 **같은 날 몇 번을 깨워도 TMAP 호출은 하루 20건을 넘지 않는다.** 사람이 손으로
돌릴 때(옵션 없이)는 예전처럼 전부 다시 받는다 — 일부러 새로 재려는 의도로 본다.
"""
import argparse
import hashlib
import json
import math
import random
import sys
import time
from datetime import date as date_module
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
from dotenv import load_dotenv

import db
from project_config import (
    DATA_ROOT, DEPOT_ID, DEPOT_LAT, DEPOT_LON, DEPOT_NAME, DURATIONS, PROJECT_ROOT,
    is_holiday,
)

sys.path.insert(0, str(ROOT / "step3_map"))
import module as tmap_mod                                    # noqa: E402
from module import (                                          # noqa: E402
    TmapBudgetExceeded, TmapQuotaExceeded, call_tmap_sequential,
    extract_cumulative_times, start_time_for,
)

# 패널은 **커밋으로 나른다.** `data/`는 .gitignore에 걸려 있어, 거기 두면 PC마다
# 자기 DB로 패널을 새로 만든다 — 그러면 두 PC가 **다른 구간**을 재게 되어
# "매일 같은 구간을 다시 잰다"는 이 수집기의 전제가 통째로 무너진다.
# 실제로 패널에 쓰인 대여소 하나만 달라도 사슬 전체가 바뀌는 것을 확인했다(1.26.52).
PANEL_PATH = PROJECT_ROOT / "tools" / "road_panel.json"

# 1.26.52 이전에 쓰던 자리. 여기에 있으면 옮겨 준다.
LEGACY_PANEL_PATH = DATA_ROOT / "road_panel.json"

# 이 수집기가 남기는 run_label의 앞머리. 파이프라인 실행분과 섞이지 않도록
# 접두어로 가른다 — 분석 스크립트가 이 값으로 골라 낸다.
PROBE_PREFIX = "roadprobe-"

# 사슬 하나에 넣을 구간(hop) 수. 지점은 이보다 하나 많고, 경유지는 하나 적다.
# 20이면 경유지 19개라 routeSequential30 상한(30) 안에 든다.
HOPS_PER_CHAIN = 20

# 사슬 수. 5개 × 20구간 = 회차당 100구간, 회차 4개면 하루 400구간·20호출이다.
CHAIN_COUNT = 5

# 목표 거리(km)를 순환시켜 거리 구간을 고르게 채운다. 5-F장의 거리 구간
# (0~0.5 / 0.5~1 / 1~2 / 2~5 / 5~10 / 10~20)의 대표값이다.
TARGET_KM_CYCLE = (0.3, 0.75, 1.5, 3.5, 7.5, 15.0)

PANEL_SEED = 42


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    """두 점 사이 거리(km). step2·step3과 같은 공식."""
    radius = 6371.0
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dp = p2 - p1
    dl = math.radians(float(lon2) - float(lon1))
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(h)))


# ---------------------------------------------------------------- 패널

def load_stations() -> pd.DataFrame:
    """대여소 좌표 목록. 같은 대여소가 여러 실행에 있으면 하나만 남긴다."""
    with db.session() as conn:
        frame = pd.read_sql(
            "SELECT station_id, station_name, lat, lon FROM station_info", conn)
    frame = frame.dropna(subset=["lat", "lon"])
    frame = frame[frame["station_id"] != DEPOT_ID]
    # station_id 순으로 정렬한 뒤 첫 행만 남긴다 — 실행 순서에 좌우되지 않게 한다.
    frame = frame.sort_values("station_id").drop_duplicates("station_id")
    return frame.reset_index(drop=True)


def build_panel(stations: pd.DataFrame) -> list:
    """차고지에서 시작해 차고지로 끝나는 사슬 CHAIN_COUNT개를 만든다.

    각 걸음마다 목표 거리를 하나 꺼내 **그 거리에 가장 가까운 미사용 대여소**를
    고른다. 그래서 사슬 안의 구간 거리가 목표 순환을 따라 고르게 퍼진다.
    """
    rng = random.Random(PANEL_SEED)
    depot = {"id": DEPOT_ID, "name": DEPOT_NAME, "lat": DEPOT_LAT, "lon": DEPOT_LON}

    pool = stations.to_dict("records")
    used = set()
    chains = []

    for chain_idx in range(CHAIN_COUNT):
        # 사슬마다 목표 순환의 시작 위치를 달리해 같은 패턴이 겹치지 않게 한다.
        offset = rng.randrange(len(TARGET_KM_CYCLE))
        points = [dict(depot)]
        current = depot

        for step in range(HOPS_PER_CHAIN - 1):
            target = TARGET_KM_CYCLE[(offset + step) % len(TARGET_KM_CYCLE)]
            best, best_gap = None, None
            for row in pool:
                if row["station_id"] in used:
                    continue
                gap = abs(haversine_km(current["lat"], current["lon"],
                                       row["lat"], row["lon"]) - target)
                if best_gap is None or gap < best_gap:
                    best, best_gap = row, gap
            if best is None:
                break
            used.add(best["station_id"])
            current = {"id": best["station_id"], "name": best["station_name"],
                       "lat": float(best["lat"]), "lon": float(best["lon"])}
            points.append(current)

        points.append(dict(depot))          # 차고지 복귀 구간을 반드시 포함한다
        chains.append({"chain": chain_idx, "points": points})

    return chains


def panel_digest(chains: list) -> str:
    """패널의 지문. **두 PC가 같은 구간을 재고 있는지 한 줄로 대조하는 수단이다.**

    방문 순서대로 이어 붙인 대여소 ID만 해싱한다 — 좌표는 소수점 표기가 PC마다
    다를 수 있고, 우리가 같아야 하는 것은 **어느 지점을 어떤 순서로 지나는가**다.
    """
    ids = "".join(point["id"] for chain in chains for point in chain["points"])
    return hashlib.sha1(ids.encode("utf-8")).hexdigest()[:12]


def save_panel(chains: list) -> None:
    PANEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    PANEL_PATH.write_text(json.dumps({
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "seed": PANEL_SEED,
        "digest": panel_digest(chains),
        "chains": chains,
    }, ensure_ascii=False, indent=1), encoding="utf-8")


def load_panel(rebuild: bool = False) -> list:
    """패널을 읽는다. 없으면 만든다. `rebuild=True`면 다시 만든다.

    **만드는 것은 최후의 수단이다.** 패널은 커밋으로 나르는 것이 정상이고,
    새로 만들면 그 PC만의 패널이 생겨 다른 PC와 대조할 수 없게 된다.
    """
    if PANEL_PATH.exists() and not rebuild:
        return json.loads(PANEL_PATH.read_text(encoding="utf-8"))["chains"]

    if LEGACY_PANEL_PATH.exists() and not rebuild:
        chains = json.loads(LEGACY_PANEL_PATH.read_text(encoding="utf-8"))["chains"]
        save_panel(chains)
        print(f"패널을 커밋되는 자리로 옮겼습니다 → {PANEL_PATH}")
        return chains

    stations = load_stations()
    if len(stations) < HOPS_PER_CHAIN * CHAIN_COUNT:
        raise SystemExit(
            f"대여소가 {len(stations)}곳뿐입니다. station_info를 먼저 채우세요"
            f" (python run_pipeline.py 또는 tools/csv_to_db.py).")
    chains = build_panel(stations)
    save_panel(chains)
    print(f"⚠️ 패널을 **새로** 만들었습니다 → {PANEL_PATH}")
    print(f"   지문 {panel_digest(chains)} · 이 파일을 커밋해 다른 PC와 맞추십시오.")
    print("   (다른 PC가 이미 수집 중이라면 그쪽 패널을 가져와야 합니다 —")
    print("    패널이 다르면 구간이 달라져 함께 분석할 수 없습니다.)")
    return chains


def panel_summary(chains: list) -> pd.DataFrame:
    """패널 구간의 거리 분포. 거리 구간이 고르게 찼는지 눈으로 확인한다."""
    rows = []
    for chain in chains:
        pts = chain["points"]
        for i in range(len(pts) - 1):
            rows.append(haversine_km(pts[i]["lat"], pts[i]["lon"],
                                     pts[i + 1]["lat"], pts[i + 1]["lon"]))
    frame = pd.DataFrame({"km": rows})
    edges = [0, 0.5, 1, 2, 5, 10, 20, 999]
    labels = ["0~0.5", "0.5~1", "1~2", "2~5", "5~10", "10~20", "20+"]
    frame["구간"] = pd.cut(frame["km"], edges, labels=labels)
    return frame.groupby("구간", observed=False).agg(
        구간수=("km", "size"), 평균km=("km", "mean")).reset_index()


# ---------------------------------------------------------------- 수집

def measure_chain(chain: dict, duration: str, start_time: str, headers: dict) -> list:
    """사슬 하나를 TMAP에 물어 구간별 실측 행을 만든다.

    `routeSequential`은 **경유지를 준 순서대로** 지나므로, 돌아온 누적 시간의
    차분이 곧 우리가 정의한 구간의 실도로 소요다(step3의 `_road_legs`와 같은 논리).
    """
    pts = chain["points"]
    start = {"name": pts[0]["name"], "X": str(pts[0]["lon"]), "Y": str(pts[0]["lat"])}
    end = {"name": pts[-1]["name"], "X": str(pts[-1]["lon"]), "Y": str(pts[-1]["lat"])}
    via = [{"viaPointId": p["id"], "viaPointName": p["name"],
            "viaX": str(p["lon"]), "viaY": str(p["lat"])} for p in pts[1:-1]]

    geo = call_tmap_sequential(start, end, via, start_time=start_time, headers=headers)
    elapsed = extract_cumulative_times(geo)["elapsed_sec"]
    if not elapsed or any(e is None for e in elapsed):
        print(f"  ⚠ 사슬 {chain['chain']}: 구간 시간이 비어 있어 버립니다.")
        return []

    observed_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = []
    for i in range(min(len(pts), len(elapsed)) - 1):
        gap = float(elapsed[i + 1]) - float(elapsed[i])
        if gap <= 0:
            continue
        a, b = pts[i], pts[i + 1]
        rows.append({
            "cluster": int(chain["chain"]),      # 패널에서는 '사슬 번호'다
            "leg": i,
            "from_id": a["id"], "to_id": b["id"],
            "from_lat": a["lat"], "from_lon": a["lon"],
            "to_lat": b["lat"], "to_lon": b["lon"],
            "straight_km": round(haversine_km(a["lat"], a["lon"],
                                              b["lat"], b["lon"]), 4),
            "road_sec": round(gap, 1),
            "observed_at": observed_at,
            "start_time": start_time,
        })
    return rows


def rotate_durations(durations: list, anchor: str) -> list:
    """회차 순서를 날짜로 돌린다.

    한도가 소진되면 collect()가 **그 자리에서 멈춘다**(`return saved`). 순서가
    늘 같으면 잘리는 자리도 늘 같아서, 마지막 회차만 관측이 계속 모자란다 —
    실제로 `roadprobe-2026-09-01`이 앞의 셋만 100구간씩 받고 `_20_05`를 통째로
    잃었다(300구간 / 400구간). 열흘을 채워도 그 창만 표본이 적으면 사전 등록
    기준(회차별 변동계수)을 회차별로 견줄 수 없다.

    **날짜 기준이라 같은 날 다시 돌리면 같은 순서다(멱등).** 평일만 도는
    스케줄에서도 네 회차가 고르게 마지막에 선다(평일 20회 기준 5·5·5·5).
    """
    if len(durations) < 2:
        return durations
    shift = date_module.fromisoformat(anchor).toordinal() % len(durations)
    return durations[shift:] + durations[:shift]


def legs_per_duration(chains: list) -> int:
    """회차 하나가 다 찼을 때의 구간 수. 지금 패널로는 5사슬 × 20구간 = 100."""
    return sum(max(len(chain["points"]) - 1, 0) for chain in chains)


def collected_legs(run_label: str) -> dict:
    """그 라벨로 이미 저장된 회차별 구간 수."""
    with db.session() as conn:
        frame = pd.read_sql(
            "SELECT duration, COUNT(*) AS n FROM road_leg WHERE run_label = ?"
            " GROUP BY duration", conn, params=(run_label,))
    return {row.duration: int(row.n) for row in frame.itertuples()}


def pending_durations(durations: list, run_label: str, expected: int) -> list:
    """아직 다 못 채운 회차만 **원래 순서 그대로** 남긴다.

    순서를 지키는 이유: `rotate_durations()`가 날짜로 돌려 놓은 차례가 곧
    '이번에 잘려도 되는 회차'의 순서다. 여기서 다시 정렬하면 그 배려가 없어진다.
    """
    done = collected_legs(run_label)
    return [d for d in durations if done.get(d, 0) < expected]


def is_holiday_date(date_text: str) -> bool:
    """휴일(주말∪공휴일)이면 참. `--day-type`을 안 준 스케줄러가 그 날짜의
    요일 성격으로 평일/휴일 회귀를 자동으로 고르는 데 쓴다(1.26.158).
    """
    return is_holiday(date_module.fromisoformat(date_text))


def collect(chains: list, durations: list, run_label: str, headers: dict,
            dry_run: bool = False, day_type: str = "weekday") -> int:
    """회차 × 사슬을 돌며 수집한다. 저장한 구간 수를 돌려준다."""
    saved = 0
    # 이 라벨은 **계획이 아니다.** 못박아 두지 않으면 웹의 계획 목록에 계획인
    # 척 섞여, 눌러도 지표도 경로도 없는 빈 표만 나온다(1.26.107). 저장보다
    # 먼저 적는다 — save_output()이 만드는 runs 행에 종류가 처음부터 붙게.
    # day_type도 함께 적는다 — 평일·휴일 계수를 나중에 절대 섞지 않으려면
    # "그 회귀가 어느 쪽을 쟀는가"가 run_label이 아니라 DB에 남아야 한다.
    if not dry_run:
        with db.session() as conn:
            db.ensure_run(conn, run_label, kind="probe", day_type=day_type)

    for duration in durations:
        start_time = start_time_for(duration, day_type=day_type)
        print(f"\n[{duration}] 교통량 기준 시각 {start_time}"
              f" · 사슬 {len(chains)}개")
        if dry_run:
            continue

        rows = []
        for chain in chains:
            try:
                got = measure_chain(chain, duration, start_time, headers)
            except TmapQuotaExceeded as exc:
                print(f"  ⚠ 일일 한도 소진 — 여기서 멈춥니다. ({exc})")
                if rows:
                    saved += db.save_output("road_leg", pd.DataFrame(rows),
                                            run_label=run_label, duration=duration)
                return saved
            except TmapBudgetExceeded as exc:
                print(f"  ⚠ 호출 예산 소진 — 여기서 멈춥니다. ({exc})")
                if rows:
                    saved += db.save_output("road_leg", pd.DataFrame(rows),
                                            run_label=run_label, duration=duration)
                return saved
            except RuntimeError as exc:
                print(f"  ⚠ 사슬 {chain['chain']}: 호출 실패 ({exc}) → 건너뜁니다.")
                continue
            print(f"  사슬 {chain['chain']}: {len(got)}구간")
            rows.extend(got)
            time.sleep(0.5)          # 순간적인 속도 제한을 피한다

        if rows:
            saved += db.save_output("road_leg", pd.DataFrame(rows),
                                    run_label=run_label, duration=duration)
    return saved


# ---------------------------------------------------------------- 현황

def _warn_if_stalled(last_label: str) -> None:
    """마지막 수집이 오래됐거나 **스케줄이 꺼져 있으면** 알린다 (1.26.140).

    ⚠️ 이 함수가 없을 때 실제로 겪은 일: `PBR도로시간수집` 작업이 `pause`로
    **비활성화된 채 닷새**(09-03~09-07) 방치됐는데, `--status`는 DB만 보고
    *"수집 일수 2일"* 만 찍었다. **자료가 안 쌓이는 것과 수집기가 안 도는 것은
    화면에서 구분되어야 한다** — 전자는 기다리면 되고 후자는 사람이 켜야 한다.
    재고 수집기가 1.26.121에서 같은 것을 배웠는데 이쪽에는 안 옮겨 왔다.
    """
    import datetime as _dt
    import subprocess

    tail = last_label[len(PROBE_PREFIX):]
    tail = tail.removeprefix("holiday-")
    try:
        last = _dt.date.fromisoformat(tail)
    except (ValueError, IndexError):
        return
    gap = (_dt.date.today() - last).days
    if gap >= 2:
        print(f"\n[!] 마지막 수집이 {last} — {gap}일째 새 자료가 없습니다.")

    # 스케줄러는 윈도우 전용이다. 없거나 못 읽으면 조용히 넘어간다.
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-ScheduledTask -TaskName 'PBR도로시간수집' -ErrorAction Stop).State"],
            capture_output=True, text=True, timeout=20, encoding="utf-8")
    except (OSError, subprocess.SubprocessError):
        return
    state = (out.stdout or "").strip()
    if state == "Disabled":
        print("    스케줄이 **일시정지(Disabled)** 상태입니다 — 고장이 아니라 꺼져 있습니다.")
        print("    다시 켜기: .{sep}scripts{sep}road_collector.ps1 resume".format(sep=chr(92)))
    elif state and state != "Ready":
        print(f"    스케줄 상태: {state}")
    elif not state:
        print("    스케줄이 등록되어 있지 않습니다 — "
              ".{sep}scripts{sep}road_collector.ps1 install".format(sep=chr(92)))


def status() -> int:
    with db.session() as conn:
        frame = pd.read_sql(
            "SELECT run_label, duration, COUNT(*) AS 구간,"
            " MIN(start_time) AS 기준시각,"
            " ROUND(SUM(straight_km), 1) AS 직선km,"
            " ROUND(SUM(road_sec) / 60.0, 1) AS 실도로분"
            " FROM road_leg WHERE run_label LIKE ?"
            " GROUP BY run_label, duration ORDER BY run_label, duration",
            conn, params=(PROBE_PREFIX + "%",))
        other = pd.read_sql(
            "SELECT COUNT(*) AS n, COUNT(DISTINCT run_label) AS runs,"
            # start_time이 비어 있으면 1.26.4 이전 파라미터로 부른 값이다.
            " SUM(CASE WHEN start_time IS NULL THEN 1 ELSE 0 END) AS 옛파라미터"
            " FROM road_leg WHERE run_label NOT LIKE ?",
            conn, params=(PROBE_PREFIX + "%",))

    if frame.empty:
        print("고정 패널 수집분이 아직 없습니다.")
    else:
        print(frame.to_string(index=False))
        days = frame["run_label"].nunique()
        print(f"\n수집 일수 {days}일 · 총 {int(frame['구간'].sum())}구간")
        print("※ 요일·계절 안정성을 보려면 최소 10일(가급적 서로 다른 요일)이 필요합니다.")
        # run_label의 사전순 max는 날짜순이 아니다 — "roadprobe-holiday-…"가
        # "roadprobe-2026-…"보다 문자열로 항상 뒤에 온다('h' > 숫자). 접두어를
        # 뗀 날짜만 비교해야 진짜 최신을 고른다.
        latest = max(frame["run_label"],
                    key=lambda label: label[len(PROBE_PREFIX):].removeprefix("holiday-"))
        _warn_if_stalled(latest)

    print(f"\n파이프라인 실행분(패널 아님): {int(other['n'][0])}구간"
          f" / 실행 {int(other['runs'][0])}건")

    # 옛 파라미터분 경고 — start_time이 NULL이면 1.26.4 이전에 받은 값이다.
    # 그때는 startTime이 2017년 저녁(퇴근 러시아워)으로 고정돼 있었고 carType도
    # 대형화물차였다. **표에 표식이 없어 섞어 평균 내기 쉽다** - 실제로 겪었다
    # (2026-09-02, EXPERIMENTS.md 5-D장 '자료를 가려내는 법').
    legacy = int(other["옛파라미터"][0] or 0)
    if legacy:
        print(f"\n[!] 그중 {legacy}구간은 start_time이 비어 있습니다"
              " - 1.26.4 이전 파라미터로 받은 값입니다.")
        print("    (startTime이 2017년 저녁 고정 · carType=대형화물차)")
        print("    배율이 1.55~1.58로 높게 나오므로 **패널분과 섞어 평균 내지"
              " 마십시오.**")
        print("    거르는 법: WHERE start_time IS NOT NULL")
    # 옛 자리(data/)에 있으면 load_panel이 커밋되는 자리로 옮겨 준다.
    if not (PANEL_PATH.exists() or LEGACY_PANEL_PATH.exists()):
        print(f"\n[경고] 패널 파일이 없습니다: {PANEL_PATH}")
        print("      수집을 한 번 돌리면 만들어집니다. 다른 PC가 이미 수집 중이라면")
        print("      그쪽 파일을 가져오십시오 - 패널이 다르면 함께 분석할 수 없습니다.")
        return 0

    chains = load_panel()
    print(f"\n패널 {PANEL_PATH}")
    print(f"지문 {panel_digest(chains)}"
          f"  <- 다른 PC와 이 값이 같아야 함께 분석할 수 있습니다")
    print(panel_summary(chains).to_string(index=False))
    warn_if_panel_drifted(chains)
    return 0


def warn_if_panel_drifted(chains: list) -> bool:
    """쌓인 자료가 **지금 패널과 다른 구간**인지 본다. 어긋나면 True.

    패널이 바뀌면 그 전후 자료를 함께 회귀에 넣을 수 없다 - 계수의 변동이
    교통 때문인지 구간이 바뀌어서인지 다시 알 수 없게 되기 때문이다.
    조용히 섞이는 것이 가장 나쁘므로 눈에 보이게 알린다.
    """
    expected = {(int(chain["chain"]), leg,
                 chain["points"][leg]["id"], chain["points"][leg + 1]["id"])
                for chain in chains
                for leg in range(len(chain["points"]) - 1)}

    with db.session() as conn:
        stored = pd.read_sql(
            "SELECT DISTINCT run_label, cluster, leg, from_id, to_id FROM road_leg"
            " WHERE run_label LIKE ?", conn, params=(PROBE_PREFIX + "%",))
    if stored.empty:
        return False

    drifted = sorted({
        row.run_label for row in stored.itertuples()
        if (int(row.cluster), int(row.leg), row.from_id, row.to_id) not in expected})
    if not drifted:
        print("쌓인 수집분이 모두 이 패널과 일치합니다.")
        return False

    print(f"\n[!] 지금 패널과 다른 구간으로 모은 날이 있습니다: {', '.join(drifted)}")
    print("    패널이 도중에 바뀌었다는 뜻입니다. 그 전후를 한 회귀에 넣지 마십시오 -")
    print("    계수가 흔들려도 교통 때문인지 구간이 바뀌어서인지 가릴 수 없습니다.")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="TMAP 실도로 소요시간 반복 수집")
    parser.add_argument("--status", action="store_true", help="쌓인 현황만 보여준다")
    parser.add_argument("--dry-run", action="store_true", help="호출 계획만 확인")
    parser.add_argument("--rebuild-panel", action="store_true",
                        help="⚠️ 패널을 새로 만든다 (기존 수집분과 비교 불가해진다)")
    parser.add_argument("--durations", default=",".join(DURATIONS),
                        help=f"수집할 회차. 기본 {','.join(DURATIONS)}")
    parser.add_argument("--date", default=None,
                        help="run_label에 쓸 날짜(YYYY-MM-DD). 기본 오늘")
    parser.add_argument("--max-calls", type=int, default=None,
                        help="이번 실행의 TMAP 호출 상한. 기본은 필요한 만큼")
    parser.add_argument("--if-needed", action="store_true",
                        help="모자란 회차만 채운다. 다 찼으면 호출 없이 끝낸다"
                             " (스케줄러용 — 하루 여러 번 깨워도 안전하다)."
                             " day-type을 안 주면 --date의 요일로 자동 판정한다"
                             " (평일이면 평일, 주말·공휴일이면 휴일 계수를 잰다)")
    parser.add_argument("--day-type", choices=("weekday", "holiday"), default=None,
                        help="어느 쪽 교통량을 잴지. 기본은 --date의 실제 요일로"
                             " 판정한다(평일 날짜 → weekday, 주말·공휴일 → holiday)."
                             " 평일·휴일은 절대 섞지 않는다 — 회귀에 넣기 전에"
                             " runs.day_type으로 갈라라 (1.26.158)")
    args = parser.parse_args()

    if args.status:
        return status()

    load_dotenv(PROJECT_ROOT / ".env")
    import os
    api_key = os.getenv("API_KEY")
    if not api_key and not args.dry_run:
        print("[중단] .env에 TMAP API_KEY가 없습니다.")
        return 1
    headers = {"appKey": api_key, "Content-Type": "application/json"}

    durations = [d.strip() for d in args.durations.split(",") if d.strip()]
    if (unknown := [d for d in durations if d not in DURATIONS]):
        print(f"[중단] 모르는 회차 {unknown} — {DURATIONS} 중에서 고르세요.")
        return 1

    # --durations로 직접 고른 경우에는 돌리지 않는다 — 사람이 정한 순서가 의도다.
    if args.durations == ",".join(DURATIONS):
        durations = rotate_durations(
            durations, args.date or datetime.now().strftime("%Y-%m-%d"))

    chains = load_panel(rebuild=args.rebuild_panel)

    date = args.date or datetime.now().strftime("%Y-%m-%d")
    # day-type을 안 주면 그 날짜의 실제 요일 성격으로 정한다 — **자정 넘겨 도는
    # --if-needed 스케줄러가 오늘이 평일인지 휴일인지 사람 없이 판단해야 한다.**
    day_type = args.day_type or ("holiday" if is_holiday_date(date) else "weekday")
    run_label = PROBE_PREFIX + ("holiday-" if day_type == "holiday" else "") + date

    if args.if_needed:
        expected = legs_per_duration(chains)
        remaining = pending_durations(durations, run_label, expected)
        if not remaining:
            print(f"[건너뜀] {run_label}은 이미 회차 {len(durations)}개가"
                  f" 각 {expected}구간씩 차 있습니다. TMAP을 부르지 않았습니다.")
            return 0
        if len(remaining) < len(durations):
            done = [d for d in durations if d not in remaining]
            print(f"[이어받기] 이미 채운 회차 {done} 는 건너뜁니다.")
        durations = remaining

    need = len(chains) * len(durations)
    tmap_mod.MAX_CALLS = args.max_calls or need
    tmap_mod.reset_call_count()

    print(f"고정 패널 {len(chains)}사슬 × 회차 {len(durations)}개"
          f" = TMAP 호출 {need}건 (상한 {tmap_mod.MAX_CALLS}) · {day_type}")
    print(f"패널 지문 {panel_digest(chains)}")
    print(f"저장 라벨: {run_label}")
    print(panel_summary(chains).to_string(index=False))

    saved = collect(chains, durations, run_label, headers, dry_run=args.dry_run,
                    day_type=day_type)
    if args.dry_run:
        print("\n(모의 실행 — 호출하지 않았습니다)")
        return 0

    print(f"\n실제 호출 {tmap_mod.call_count()}건 · road_leg에 {saved}구간 저장")
    return 0 if saved else 1


if __name__ == "__main__":
    raise SystemExit(main())
