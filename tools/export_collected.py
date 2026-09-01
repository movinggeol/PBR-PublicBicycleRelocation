"""수집기가 모은 자료를 **내보낸다** — 재고 시계열·TMAP 실측 (docs/구현/DB_이관.md).

## 왜 필요한가

`transfer_run.py`는 **파이프라인 실행 라벨**을 나른다. 그런데 수집기가 모으는
자료는 성격이 다르다 — 실행에 묶이지 않고 **시간에 묶여** 계속 쌓인다.

    stock_history  10분마다 쌓이는 재고 시계열  (수집기 collect_stock.py)
    road_leg       고정 패널 TMAP 실측         (수집기 collect_road_time.py)

**두 PC가 각자 쌓으므로 합쳐야 한다.** 받는 쪽 도구는 이미 있었다
(`merge_stock.py` — *"먼저 수집한 것이 이긴다"*로 남의 관측을 덮지 않는다).
**없던 것은 내보내는 쪽**이다. 그동안은 `data/raw_data/재고이력/`의 일별 CSV를
손으로 골라 복사해야 했고, `road_leg`는 **아예 나를 방법이 없었다.**

## 무엇을 담나

기간을 잘라 **한 SQLite 파일**에 담는다. 받는 쪽은 `merge_stock.py`가 읽는다.

  · `stock_history` — 재고 시계열 (`--from`/`--to`로 자른다)
  · `road_leg`      — TMAP 실측 (`--road`를 줄 때만)
  · `stock_station_master` — **그날의** 대여소 이름·좌표. 재고만 옮기면 받는
                      PC에서 "이름·좌표가 없는 날"이 되므로 함께 담는다
                      (`merge_stock.py`가 이 표를 읽는다).

## 왜 CSV가 아니라 DB 파일인가

일별 CSV(`stock_YYYY-MM-DD.csv`)에는 **재고만 있고 마스터가 없다.** 한쪽만
관측한 날짜가 있으면 그날 대여소 이름·좌표를 알 수 없다(merge_stock.py가
경고한다). DB 파일로 담으면 마스터가 함께 가고, `road_leg`도 같이 실린다.

## 실행

    # 내보내는 PC에서
    python tools/export_collected.py --out data/transfer/collected.db
    python tools/export_collected.py --from 2026-08-25 --to 2026-08-31 --out data/transfer/w35.db
    python tools/export_collected.py --road --out data/transfer/collected.db

    # 받는 PC에서 (merge_stock.py가 받는다)
    python tools/merge_stock.py data/transfer/collected.db --dry-run
    python tools/merge_stock.py data/transfer/collected.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

import db


def summarize(conn) -> None:
    """무엇이 얼마나 쌓여 있는지 — 내보내기 전에 확인용."""
    print(f"DB: {db.db_path() if hasattr(db, 'db_path') else 'data/bike_system.db'}")
    row = conn.execute(
        "SELECT COUNT(*), MIN(date(observed_at)), MAX(date(observed_at))"
        " FROM stock_history").fetchone()
    print(f"  stock_history : {row[0]:,}행  {row[1]} ~ {row[2]}")
    road = conn.execute(
        "SELECT COUNT(*) FROM road_leg WHERE run_label LIKE 'roadprobe%'").fetchone()[0]
    other = conn.execute(
        "SELECT COUNT(*) FROM road_leg WHERE run_label NOT LIKE 'roadprobe%'").fetchone()[0]
    print(f"  road_leg      : 고정 패널 {road:,}행 · 파이프라인 부산물 {other:,}행")

    days = pd.read_sql(
        "SELECT date(observed_at) AS 날짜, COUNT(DISTINCT observed_at) AS 틱"
        " FROM stock_history GROUP BY 1 ORDER BY 1", conn)
    if not days.empty:
        print("\n날짜별 수집 틱:")
        print(days.to_string(index=False))


def export(conn, out_path: Path, date_from: str, date_to: str,
           with_road: bool) -> dict:
    """기간을 잘라 새 SQLite 파일에 담는다. {테이블: 행 수}."""
    if out_path.exists():
        raise SystemExit(f"{out_path}가 이미 있습니다. 지우거나 다른 이름을 주십시오.")

    where, params = [], []
    if date_from:
        where.append("date(observed_at) >= ?")
        params.append(date_from)
    if date_to:
        where.append("date(observed_at) <= ?")
        params.append(date_to)
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    stock = pd.read_sql(f"SELECT * FROM stock_history{clause}", conn, params=params)
    if stock.empty:
        raise SystemExit(
            f"내보낼 재고 시계열이 없습니다"
            f"{f' ({date_from or 'ˆ'} ~ {date_to or '∞'})' if where else ''}."
            " --list 로 쌓인 기간을 확인하십시오.")

    # 마스터를 함께 담는다 — 재고만 옮기면 받는 PC에서 이름·좌표가 빈다.
    # ⚠️ 표는 `stock_station_master`다(`parking_lot`이 아니다) — merge_stock.py가
    #    읽는 것이 이쪽이고, 날짜별로 그날의 이름·좌표를 갖는다.
    mclause, mparams = "", []
    if date_from or date_to:
        conds = []
        if date_from:
            conds.append("observed_on >= ?"); mparams.append(date_from)
        if date_to:
            conds.append("observed_on <= ?"); mparams.append(date_to)
        mclause = " WHERE " + " AND ".join(conds)
    master = pd.read_sql(
        f"SELECT * FROM stock_station_master{mclause}", conn, params=mparams)

    road = pd.DataFrame()
    if with_road:
        road = pd.read_sql(
            "SELECT * FROM road_leg WHERE run_label LIKE 'roadprobe%'", conn)

    counts: dict = {}
    with sqlite3.connect(out_path) as target:
        db.init_schema(target)              # 스키마는 정본을 그대로 쓴다
        stock.to_sql("stock_history", target, if_exists="append", index=False)
        counts["stock_history"] = len(stock)
        if not master.empty:
            master.to_sql("stock_station_master", target,
                          if_exists="append", index=False)
            counts["stock_station_master"] = len(master)
        if not road.empty:
            road.to_sql("road_leg", target, if_exists="append", index=False)
            counts["road_leg"] = len(road)
        target.commit()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="수집기 자료(재고 시계열·TMAP 실측)를 내보낸다")
    parser.add_argument("--list", action="store_true",
                        help="쌓인 자료를 보여준다 (내보내지 않는다)")
    parser.add_argument("--out", metavar="경로", help="내보낼 파일")
    parser.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD",
                        help="이 날짜부터 (생략하면 처음부터)")
    parser.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD",
                        help="이 날짜까지 (생략하면 끝까지)")
    parser.add_argument("--road", action="store_true",
                        help="TMAP 고정 패널(road_leg)도 함께 담는다")
    args, _ = parser.parse_known_args()

    with db.session() as conn:
        if args.list or not args.out:
            summarize(conn)
            if not args.out:
                print("\n내보내려면 --out 을 주십시오."
                      " 예: --out data/transfer/collected.db")
            return 0

        out = Path(args.out)
        counts = export(conn, out, args.date_from, args.date_to, args.road)
        span = f"{args.date_from or '처음'} ~ {args.date_to or '끝'}"
        print(f"수집 자료({span}) → {out}")
        for name, rows in counts.items():
            print(f"  {name:16s} {rows:>9,} 행")
        print(f"\n합계 {sum(counts.values()):,}행."
              f" 받는 PC에서: python tools/merge_stock.py {out.name}")
        if not args.road:
            print("  (TMAP 실측도 옮기려면 --road 를 주십시오)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
