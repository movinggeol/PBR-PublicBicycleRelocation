"""검증·데모용 합성 데이터 생성기.

실제 타슈 원천 CSV와 tashu_api.py 산출물의 스키마를 그대로 재현하므로,
API 키나 실데이터 없이도 step0~step4와 웹 대시보드를 돌려볼 수 있다.

단독 실행(기본 설정으로 데모 데이터 생성):
    python tools/make_sample_data.py
    python tools/make_sample_data.py --now "데모 2026" --stations 120

테스트에서 사용:
    from tools.make_sample_data import generate
    generate(now="smoketest-...", raw_path=tmp_path / "raw.csv")

주의: 기본 now 값은 project_config의 기본값과 같으므로 실데이터가 있는 환경에서는
--now로 구분되는 라벨을 주는 것이 안전하다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from project_config import (
    DATA_ROOT, DEFAULT_NOW, DEFAULT_PERIOD, PROJECT_ROOT, ensure_output_dirs, is_holiday,
)

# ⚠️ `data/`를 붙이지 마라 — 경로는 `DATA_ROOT` 기준이다(1.26.141).
STOCK_FILE = "pp_data/대여소별 재고/대여소별_자전거대수 ({now}).csv"
DEFAULT_RAW = "raw_data/합성_대여이력.csv"


# 하루 3회차 운용(docs/구현/FLEET.md)에 맞춰, 회차마다 방향이 다른 흐름을 만든다.
#   (시작시, 끝시, 방향)  방향 +1 = 앞쪽→뒤쪽 대여소, -1 = 반대
#
# **평일과 휴일의 방향을 다르게 둔다.** 실데이터에서 `_10_15`·`_15_20`은 대여소의
# 33~37%가 평일과 휴일에 부호가 반대였다(experiments/structure/weekend_profile.py).
# 합성 데이터도 그 구조를 흉내 내야 "섞으면 상쇄된다"를 테스트할 수 있다.
FLOW_WINDOWS = {
    "weekday": [
        (5, 10, +1),    # 출근: 주거지 → 도심
        (10, 15, -1),   # 낮: 완만한 역류
        (15, 20, -1),   # 퇴근: 도심 → 주거지
    ],
    "holiday": [
        (5, 10, -1),    # 출근 흐름이 없다 (실데이터에서 평일의 57% 수준)
        (10, 15, +1),   # 낮 나들이 — 평일과 반대 방향
        (15, 20, +1),   # 귀가 — 평일과 반대 방향
    ],
}


def generate(
    now: str = DEFAULT_NOW,
    period: str = DEFAULT_PERIOD,
    stations: int = 90,
    days: int = 28,        # 4주 = 평일 20일 + 휴일 8일 (휴일 경로도 검증해야 한다)
    rentals_per_day: int = 700,
    raw_path: Optional[Path] = None,
    seed: int = 7,
) -> dict:
    """합성 재고 CSV와 대여이력 CSV를 만들고 경로를 돌려준다.

    시간대마다 방향이 다른 흐름을 만들어 회차별로 Pick/Drop 불균형이 생기게 한다
    (출근엔 도심으로 몰리고 퇴근엔 주거지로 돌아오는 형태).
    **휴일은 방향이 평일과 다르다** — 두 요일 구분을 섞으면 안 된다는 것을
    합성 데이터에서도 재현하기 위해서다.
    재고는 거치대 수에 비례해 넓게 흩어 두어 한쪽 후보만 나오는 일이 없게 한다.
    """
    rng = np.random.default_rng(seed)
    ensure_output_dirs()

    station_ids = [f"ST{i:04d}" for i in range(1, stations + 1)]
    lat = 36.30 + rng.random(stations) * 0.15
    lon = 127.32 + rng.random(stations) * 0.15
    racks = rng.integers(1, 4, stations)

    stock_df = pd.DataFrame({
        "station_id": station_ids,
        "station_name": [f"대여소{i}" for i in range(1, stations + 1)],
        # extract_parking_lot.py의 파서가 기대하는 "설명 / 10대용*N, 5대용*1" 형식
        "parking_info": [f"대여소{i} 앞 / 10대용*{r}, 5대용*1"
                         for i, r in zip(range(1, stations + 1), racks)],
        "lat": lat,
        "lon": lon,
        # 거치대 수에 비례해 0~90% 사이로 넓게 흩어 둔다. 재고가 한쪽으로 쏠리면
        # Pick 후보만 나오고 Drop 후보가 없어 재배치가 성립하지 않는다.
        "stock": (racks * 10 + 5) * rng.uniform(0.0, 0.9, stations),
    }).astype({"stock": int})

    stock_path = DATA_ROOT / STOCK_FILE.format(now=now)
    stock_path.parent.mkdir(parents=True, exist_ok=True)
    stock_df.to_csv(stock_path, encoding="utf-8", index=False)

    source_pool = station_ids[: stations // 2]
    sink_pool = station_ids[stations // 2:]
    coord = dict(zip(station_ids, zip(lat, lon)))

    rows = []
    bike_no = 0
    window_share = 0.85 / 3                     # 15%는 그 외 시간대에 흩뿌린다
    # bdate_range(평일만)가 아니라 date_range를 쓴다 — 주말이 없으면
    # --day-type weekend 경로를 검증할 수 없다.
    for day in pd.date_range("2025-11-03", periods=days):
        windows = FLOW_WINDOWS["holiday" if is_holiday(day) else "weekday"]
        for _ in range(rentals_per_day):
            bike_no += 1
            draw = rng.random()

            window = None
            for index, spec in enumerate(windows):
                if draw < window_share * (index + 1):
                    window = spec
                    break

            if window is None:
                hour = int(rng.integers(20, 24))
                src, dst = rng.choice(station_ids, 2, replace=False)
            else:
                start, end, direction = window
                hour = int(rng.integers(start, end))
                origin, target = (source_pool, sink_pool) if direction > 0 else (sink_pool, source_pool)
                src, dst = rng.choice(origin), rng.choice(target)

            start = day + pd.Timedelta(hours=hour, minutes=int(rng.integers(0, 60)))
            dur = int(rng.integers(5, 45))

            rows.append({
                "자전거번호": f"BK{bike_no % 3700:04d}",
                "대여일시": start.strftime("%Y-%m-%d %H:%M:%S"),
                "대여_대여소ID": src,
                "대여_대여소명": f"대여소{int(src[2:])}",
                "대여_X좌표": coord[src][0],
                "대여_Y좌표": coord[src][1],
                "반납일시": (start + pd.Timedelta(minutes=dur)).strftime("%Y-%m-%d %H:%M:%S"),
                "반납_대여소ID": dst,
                "반납_X좌표": coord[dst][0],
                "반납_Y좌표": coord[dst][1],
                "이용시간(분)": dur,
                "이용거리(km)": round(dur * rng.uniform(0.1, 0.3), 2),
            })

    raw = pd.DataFrame(rows)
    raw_path = Path(raw_path) if raw_path else DATA_ROOT / DEFAULT_RAW
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw.to_csv(raw_path, encoding="utf-8", index=False)

    return {
        "now": now,
        "period": period,
        "stations": stations,
        "rentals": len(raw),
        "stock_path": stock_path,
        "raw_path": raw_path,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="합성 데이터 생성")
    parser.add_argument("--now", default=DEFAULT_NOW, help="분석 시점 라벨")
    parser.add_argument("--period", default=DEFAULT_PERIOD, help="순수요 기간 라벨")
    parser.add_argument("--stations", type=int, default=90, help="대여소 수")
    parser.add_argument("--days", type=int, default=20, help="평일 수")
    parser.add_argument("--raw-file", default=None, help="대여이력 CSV 저장 경로")
    args = parser.parse_args()

    info = generate(
        now=args.now, period=args.period, stations=args.stations,
        days=args.days, raw_path=Path(args.raw_file) if args.raw_file else None,
    )

    print(f"대여소 {info['stations']}곳, 대여이력 {info['rentals']:,}건 생성")
    print(" 재고 CSV :", info["stock_path"])
    print(" 원천 CSV :", info["raw_path"])
    print()
    print("이어서 실행하려면:")
    print(f'  python run_pipeline.py --skip-fetch --skip-eda --skip-map --now "{info["now"]}" '
          f'--period "{info["period"]}" --raw-file "{info["raw_path"]}"')
    print()
    print("  ⚠️ --skip-api를 쓰지 마십시오. 그것은 직전 실행의 재고 스냅샷을")
    print("     물려받아 방금 만든 합성 데이터를 조용히 무시합니다 (2026-08-31 확인).")
    print("     --skip-fetch는 라이브 타슈 API 호출만 건너뜁니다.")
    print()
    print("  더 쉬운 길: python tools/reproduce.py  (생성·실행·검증·정리를 한 번에)")


if __name__ == "__main__":
    main()
