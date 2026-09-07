"""원천 대여이력 CSV를 SQLite(rental_history)에 적재한다 (DB_PLAN 4단계).

적재해 두면 step0의 `raw_to_net.py`·`api_to_info.py`가 CSV 전체 스캔 대신
DB에서 읽는다(두 스크립트가 매 실행마다 같은 파일을 통째로 읽던 문제).

실행:
    python tools/load_rentals.py                      # project_config 기본값
    python tools/load_rentals.py --period "25년 11월" --raw-file "data/raw_data/....csv"
    python tools/load_rentals.py --split-by-month     # 1년치 파일을 월별로 나눠 적재
    python tools/load_rentals.py --status             # 적재 현황만 확인

여러 달이 든 병합 파일은 --split-by-month 로 넣으세요. 계절이 다른 달을 섞어
평균을 내면 목표 재고(target_qty)가 엉뚱해집니다.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

import db
from project_config import get_runtime_config


def show_status() -> int:
    with db.session() as conn:
        try:
            summary = pd.read_sql(
                "SELECT period, COUNT(*) AS rows,"
                "       MIN(rent_at) AS first_rent, MAX(rent_at) AS last_rent"
                " FROM rental_history GROUP BY period ORDER BY period", conn)
        except Exception as err:
            print(f"조회 실패: {err}")
            return 1

    if summary.empty:
        print("적재된 대여이력이 없습니다.")
    else:
        print(summary.to_string(index=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(add_help=True, description="대여이력 CSV -> SQLite 적재")
    parser.add_argument("--status", action="store_true", help="적재 현황만 출력")
    parser.add_argument("--chunksize", type=int, default=100_000, help="한 번에 처리할 행 수")
    parser.add_argument("--split-by-month", action="store_true",
                        help="대여일시에서 월을 뽑아 period를 행마다 정한다(병합 파일용)")
    args, _ = parser.parse_known_args()

    if args.status:
        return show_status()

    config = get_runtime_config()
    raw_path = config.raw_path

    if not raw_path.is_file():
        print(f"원천 CSV가 없습니다: {raw_path}")
        print("--raw-file 로 경로를 지정하거나 data/raw_data/에 파일을 두세요.")
        return 1

    if args.split_by_month:
        print(f"적재 시작: {raw_path.name}  (월별 분리)")
    else:
        print(f"적재 시작: {raw_path.name}  (period={config.period!r})")

    started = time.monotonic()
    loaded = db.bulk_load_rentals(
        raw_path, period=None if args.split_by_month else config.period,
        chunksize=args.chunksize, split_by_month=args.split_by_month)
    elapsed = time.monotonic() - started
    total = sum(loaded.values())

    print(f"\n완료: {total:,}행 / {elapsed:.1f}초"
          + (f" ({total / elapsed:,.0f} 행/초)" if elapsed > 0 else ""))
    if len(loaded) > 1:
        print("\n기간별 적재:")
        for label in sorted(loaded):
            print(f"  {label}  {loaded[label]:>10,}행")
    # 지금 실제로 열린 DB를 찍는다 — db.DB_PATH는 PBR_DB_PATH를 모른다(1.26.143).
    print(f"\nDB: {db.active_db_path()}")
    print("\n이제 step0가 CSV 대신 DB에서 읽습니다:")
    print('  python "step0_collect/raw_to_net.py" --period "25년 11월"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
