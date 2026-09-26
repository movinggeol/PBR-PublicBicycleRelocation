"""원천 대여이력 CSV를 SQLite(rental_history)에 적재한다 (DB_PLAN 4단계).

적재해 두면 step0의 `raw_to_net.py`·`api_to_info.py`가 CSV 전체 스캔 대신
DB에서 읽는다(두 스크립트가 매 실행마다 같은 파일을 통째로 읽던 문제).

실행:
    python tools/load_rentals.py                      # project_config 기본값
    python tools/load_rentals.py --period "25년 11월" --raw-file "data/raw_data/....csv"
    python tools/load_rentals.py --split-by-month     # 1년치 파일을 월별로 나눠 적재
    python tools/load_rentals.py --dir "data/raw_data/<월별 원본 폴더>"   # 월별 파일 여럿
    python tools/load_rentals.py --status             # 적재 현황만 확인

여러 달이 든 병합 파일은 --split-by-month 로 넣으세요. 계절이 다른 달을 섞어
평균을 내면 목표 재고(target_qty)가 엉뚱해집니다.

🔴 **월별 원본이 있으면 `--dir`이 정답이다.** 병합본 `타슈 대여이력(25.04~26.03).csv`는
IQR 이상치 필터가 제자리에 덮어써 **달마다 14~16% 깎여 있었다**(1.26.129의 결함이
남긴 흔적 — 긴 이용이 빠졌다, 2026-09-26 확인). 병합본은 다시 쓰지 마라.

`--dir`은 파일명의 `(YY년MM월)`로 기간을 정하되 **내용과 맞춰 본다** — 포털의
`(25년12월)` 파일에는 2025년 1월 자료가 들어 있었다. 어긋난 파일은 건너뛰고
끝에 따로 알린다(나머지 달은 계속 넣는다).
"""
from __future__ import annotations

import argparse
import re
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


# 월별 원본 파일명의 기간 표기 — '… 정보(25년04월).csv', '…(25년02월)_시스템 장애로 ….csv'
_MONTH_FILE_RE = re.compile(r"\((\d{2})년\s?(\d{2})월\)")


def month_files(folder: Path) -> list:
    """폴더의 월별 원본을 (기간, 경로)로 모은다. 이름에 달이 없으면 뺀다."""
    found = []
    for path in sorted(Path(folder).glob("*.csv")):
        if (m := _MONTH_FILE_RE.search(path.name)):
            found.append((f"{m.group(1)}년 {m.group(2)}월", path))
    return sorted(found, key=lambda item: item[0])


def load_directory(folder: Path, chunksize: int = 100_000, db_path=None, only=None):
    """월별 원본을 달마다 적재한다. (적재한 {기간: 행 수}, 건너뛴 [(파일, 이유)]).

    `only`에 기간 목록을 주면 그 달만 넣는다 — 도중에 끊긴 적재를 이어 넣을 때 쓴다.
    """
    loaded, skipped = {}, []
    # 인덱스를 **둔 채** 넣는다(manage_indexes=False). 달마다 지우고 다시 지으면 수백만
    # 행 인덱스를 달마다 두 번 짓고, 그 한 문장이 쓰기 잠금을 오래 쥐어 재고 수집기가
    # 틱을 잃는다(2026-09-26 실제로 하나 잃었다 — db.bulk_load_rentals 4번).
    for period, path in month_files(folder):
        if only and period not in only:
            continue
        try:
            result = db.bulk_load_rentals(path, period=period, db_path=db_path,
                                          chunksize=chunksize, manage_indexes=False)
        except ValueError as err:          # 파일 이름과 내용이 다르다
            skipped.append((path.name, str(err)))
            print(f"  ⚠️ 건너뜀 {period}: {err}")
            continue
        loaded.update(result)
        print(f"  {period}  {result.get(period, 0):>10,}행  ({path.name})", flush=True)
    return loaded, skipped


def main() -> int:
    parser = argparse.ArgumentParser(add_help=True, description="대여이력 CSV -> SQLite 적재")
    parser.add_argument("--status", action="store_true", help="적재 현황만 출력")
    parser.add_argument("--chunksize", type=int, default=100_000, help="한 번에 처리할 행 수")
    parser.add_argument("--split-by-month", action="store_true",
                        help="대여일시에서 월을 뽑아 period를 행마다 정한다(병합 파일용)")
    parser.add_argument("--only", default="",
                        help='--dir과 함께: 이 기간만 넣는다(콤마 구분, 예: "26년 01월,26년 02월")')
    parser.add_argument("--dir", default="",
                        help="월별 원본 파일이 든 폴더 — 파일명의 (YY년MM월)로 기간을 정하고 내용과 맞춰 본다")
    args, _ = parser.parse_known_args()

    if args.status:
        return show_status()

    if args.dir:
        folder = Path(args.dir)
        if not folder.is_dir():
            print(f"폴더가 없습니다: {folder}")
            return 1
        months = [m.strip() for m in args.only.split(",") if m.strip()]
        print(f"적재 시작: {folder.name}  (월별 파일 {len(month_files(folder))}개)")
        started = time.monotonic()
        loaded, skipped = load_directory(folder, chunksize=args.chunksize,
                                         only=months or None)
        elapsed = time.monotonic() - started
        print(f"\n완료: {len(loaded)}개 달 · {sum(loaded.values()):,}행 / {elapsed:.0f}초")
        if skipped:
            print(f"\n🔴 건너뛴 파일 {len(skipped)}개 — 이름과 내용이 다르다:")
            for name, reason in skipped:
                print(f"  - {name}")
        print(f"\nDB: {db.active_db_path()}")
        print("다음: python tools/rebuild_net_demand.py   # 순수요를 새 자료로 다시 만든다")
        return 0

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
    print('  python "pipeline/step0_collect/raw_to_net.py" --period "25년 11월"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
