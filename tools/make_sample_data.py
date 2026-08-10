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

from project_config import DEFAULT_NOW, DEFAULT_PERIOD, PROJECT_ROOT, ensure_output_dirs

STOCK_FILE = "data/pp_data/대여소별 재고/대여소별_자전거대수 ({now}).csv"
DEFAULT_RAW = "data/raw_data/합성_대여이력.csv"


def generate(
    now: str = DEFAULT_NOW,
    period: str = DEFAULT_PERIOD,
    stations: int = 90,
    days: int = 20,
    rentals_per_day: int = 700,
    raw_path: Optional[Path] = None,
    seed: int = 7,
) -> dict:
    """합성 재고 CSV와 대여이력 CSV를 만들고 경로를 돌려준다.

    05~10시에 편향된 흐름을 만들어 Pick/Drop 불균형이 실제로 생기도록 한다.
    (앞쪽 절반 대여소는 유출, 뒤쪽 절반은 유입)
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
        "stock": rng.integers(0, 25, stations),
    })

    stock_path = PROJECT_ROOT / STOCK_FILE.format(now=now)
    stock_path.parent.mkdir(parents=True, exist_ok=True)
    stock_df.to_csv(stock_path, encoding="utf-8", index=False)

    source_pool = station_ids[: stations // 2]
    sink_pool = station_ids[stations // 2:]
    coord = dict(zip(station_ids, zip(lat, lon)))

    rows = []
    bike_no = 0
    for day in pd.bdate_range("2025-11-03", periods=days):
        for _ in range(rentals_per_day):
            bike_no += 1
            peak = rng.random() < 0.6
            hour = int(rng.integers(5, 10)) if peak else int(rng.integers(10, 24))

            if peak:
                src, dst = rng.choice(source_pool), rng.choice(sink_pool)
            else:
                src, dst = rng.choice(station_ids, 2, replace=False)

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
    raw_path = Path(raw_path) if raw_path else PROJECT_ROOT / DEFAULT_RAW
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
    print(f'  python run_pipeline.py --skip-api --skip-eda --now "{info["now"]}" '
          f'--period "{info["period"]}" --raw-file "{info["raw_path"]}"')
    print("  (단, --skip-api는 extract_parking_lot·api_to_info도 건너뛰므로")
    print("   개별 단계 실행이 필요하다. tests/test_pipeline.py 참고)")


if __name__ == "__main__":
    main()
