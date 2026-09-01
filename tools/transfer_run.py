"""파이프라인 **실행 라벨 하나**를 다른 PC로 옮긴다 (1.26.51).

## 왜 필요한가

`data/`는 `.gitignore`에 걸려 있어 커밋이 나르지 않는다. 그래서 두 PC의
`bike_system.db`가 서로 다른 실행을 갖게 되는데, 보통은 문제가 아니다 —
[두_PC_작업.md](../docs/구현/두_PC_작업.md)가 적었듯 파이프라인 산출물은 다시
만들면 되기 때문이다.

**딱 하나, 다시 만들 수 없는 것이 있다.** `station_info.stock`은 `tashu.py`가
**실행하는 순간 라이브 API에서 받은 재고**다(`parking_count` → `stock`).
2026-08-11에 돌린 실행의 재고는 그날로 끝났고, 오늘 다시 돌리면 오늘 재고가
들어온다. 그런데 [DECISIONS.md](../docs/분석/DECISIONS.md) 6-B가 실험의 **정본
스냅샷**을 특정 실행 라벨로 못박았으므로, **그 라벨이 없는 PC는 실험을 하나도
재현할 수 없다.**

값을 정본으로 정해 놓고 그것을 나를 수단이 없는 상태 — 그 구멍을 메우는 도구다.

**git으로 나르지 않는 것은 실측에 근거한 결정이다**(2026-09-01). DB를 커밋하면
커밋 1회당 약 404MB(압축률 31%)로 현재 `.git` 6.6MB가 60배가 되고, GitHub
100MB 상한에 걸리며, SQLite는 3바이트만 바뀌어도 블롭을 통째로 새로 저장한다.
근거와 표는 docs/구현/두_PC_작업.md 4-2장.

## 무엇을 나르나

`run_label`로 묶이는 **모든 테이블**을 담는다(`runs`부터 `kpi_summary`까지 14개).
`net_demand`는 **담지 않는다** — 기간(`period`) 스코프이고 원천 대여이력에서
`tools/rebuild_net_demand.py`로 다시 만들 수 있다. 원천 CSV는 어차피 양쪽에
있어야 한다.

## 규칙 — 있는 라벨은 덮지 않는다

이미 그 라벨이 있으면 **멈춘다.** 실험 결과가 붙어 있는 실행을 말없이 덮어쓰면
어느 쪽 숫자를 본 것인지 알 수 없게 된다. 정말 덮으려면 `--overwrite`를 준다.
(`tools/merge_stock.py`가 *"먼저 수집한 것이 이긴다"*로 재고 틱을 지키는 것과
같은 성격의 방어다.)

## 실행

    # 내보내는 PC에서 — 산출물은 data/transfer/ 에 둔다(data/가 이미 gitignore).
    python tools/transfer_run.py --list
    python tools/transfer_run.py --export "2026-08-11 real" --out data/transfer/run_20260811_real.db

    # 받는 PC에서 (USB·클라우드로 파일을 옮긴 뒤)
    python tools/transfer_run.py --import data/transfer/run_20260811_real.db --dry-run
    python tools/transfer_run.py --import run_20260811.db
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

import db

# run_label로 묶이는 테이블 전부. db.TABLES에 없는 것(runs·kpi_summary·
# vehicle_assignment)도 실행에 딸린 자료라 함께 나른다.
#
# ⚠️ **road_leg는 뺀다.** 라벨 스코프이긴 하지만 성격이 다르다 — TMAP 실측은
# 양쪽 PC가 각자 쌓는 관측치이고, 파이프라인 실행을 옮긴다고 함께 가야 할
# 이유가 없다. 섞으면 '어느 PC에서 잰 것인가'가 사라진다.
RUN_TABLES = (
    "runs",
    "station_info",
    "parking_lot",
    "station_stock",
    "rebalance_plan",
    "pick_drop",
    "ilp_plan",
    "vrp_plan",
    "metrics",
    "route_summary",
    "kpi_summary",
    "vehicle_assignment",
)


def active_db_path() -> Path:
    """지금 실제로 열리는 DB 경로.

    `db.DB_PATH`는 import 시점에 굳는 **기본값**이라, `PBR_DB_PATH`로 다른 DB를
    쓰고 있어도 그대로 기본 경로를 가리킨다. 그것을 그대로 찍으면 **어디에
    넣었는지 거짓말하는 안내문**이 된다(실제로 그랬다). `db.connect()`와 같은
    우선순위로 다시 푼다.
    """
    return Path(os.getenv("PBR_DB_PATH") or db.DB_PATH)


def available_labels(conn) -> pd.DataFrame:
    """이 DB에 있는 실행 라벨과 딸린 행 수."""
    rows = []
    for label, period, duration in conn.execute(
            "SELECT run_label, period, duration FROM runs ORDER BY run_label"):
        total = 0
        for table in RUN_TABLES:
            if table == "runs":
                continue
            try:
                total += conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE run_label = ?",
                    (label,)).fetchone()[0]
            except sqlite3.OperationalError:
                continue
        rows.append({"실행 라벨": label, "period": period,
                     "duration": duration, "행": total})
    return pd.DataFrame(rows)


def export_run(conn, label: str, out_path: Path) -> dict:
    """라벨 하나에 딸린 행을 새 SQLite 파일에 담는다. {테이블: 행 수}."""
    if not conn.execute("SELECT 1 FROM runs WHERE run_label = ?",
                        (label,)).fetchone():
        labels = available_labels(conn)
        raise SystemExit(
            f"'{label}' 실행이 이 DB에 없습니다.\n"
            + (labels.to_string(index=False) if not labels.empty else "  (실행 없음)"))

    if out_path.exists():
        raise SystemExit(f"{out_path}가 이미 있습니다. 지우거나 다른 이름을 주십시오.")

    counts: dict = {}
    with sqlite3.connect(out_path) as target:
        # 스키마는 정본(db.SCHEMA)을 그대로 쓴다 — 받는 쪽과 컬럼이 어긋나지 않게.
        db.init_schema(target)
        for table in RUN_TABLES:
            try:
                frame = pd.read_sql(
                    f"SELECT * FROM {table} WHERE run_label = ?", conn, params=(label,))
            except (sqlite3.OperationalError, pd.errors.DatabaseError):
                continue
            if frame.empty:
                continue
            frame.to_sql(table, target, if_exists="append", index=False)
            counts[table] = len(frame)
        target.commit()
    return counts


def import_run(conn, source_path: Path, overwrite: bool, dry_run: bool) -> dict:
    """내보낸 파일을 이 DB에 넣는다. {테이블: 행 수}."""
    if not source_path.is_file():
        raise SystemExit(f"파일이 없습니다: {source_path}")

    # 원본은 **읽기 전용**으로 연다 — merge_stock.py와 같은 방침이다.
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    try:
        labels = [r[0] for r in source.execute("SELECT run_label FROM runs")]
        if not labels:
            raise SystemExit(f"{source_path}에 실행이 없습니다.")

        for label in labels:
            exists = conn.execute("SELECT 1 FROM runs WHERE run_label = ?",
                                  (label,)).fetchone()
            if exists and not overwrite:
                raise SystemExit(
                    f"'{label}'이 이미 이 DB에 있습니다. 말없이 덮지 않습니다 —\n"
                    f"  정말 덮으려면 --overwrite를 주십시오.")

        counts: dict = {}
        for table in RUN_TABLES:
            try:
                frame = pd.read_sql(f"SELECT * FROM {table}", source)
            except (sqlite3.OperationalError, pd.errors.DatabaseError):
                continue
            if frame.empty:
                continue
            counts[table] = len(frame)
            if dry_run:
                continue
            for label in labels:
                conn.execute(f"DELETE FROM {table} WHERE run_label = ?", (label,))
            frame.to_sql(table, conn, if_exists="append", index=False)
        if not dry_run:
            conn.commit()
        return counts
    finally:
        source.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="파이프라인 실행 라벨을 PC 간에 옮긴다")
    parser.add_argument("--list", action="store_true", help="이 DB의 실행 라벨을 보여준다")
    parser.add_argument("--export", metavar="라벨", help="내보낼 실행 라벨")
    parser.add_argument("--out", metavar="경로", help="내보낼 파일 (기본: run_<라벨>.db)")
    parser.add_argument("--import", dest="import_path", metavar="경로",
                        help="받아들일 파일")
    parser.add_argument("--overwrite", action="store_true",
                        help="같은 라벨이 이미 있어도 덮어쓴다")
    parser.add_argument("--dry-run", action="store_true", help="넣지 않고 내용만 확인")
    args, _ = parser.parse_known_args()

    with db.session() as conn:
        if args.list or not (args.export or args.import_path):
            frame = available_labels(conn)
            print(f"DB: {active_db_path()}")
            print(frame.to_string(index=False) if not frame.empty
                  else "적재된 실행이 없습니다.")
            return 0

        if args.export:
            safe = args.export.replace(" ", "_").replace(":", "")
            out = Path(args.out) if args.out else Path(f"run_{safe}.db")
            counts = export_run(conn, args.export, out)
            print(f"'{args.export}' → {out}")
            for name, rows in counts.items():
                print(f"  {name:22s} {rows:>8,} 행")
            print(f"\n합계 {sum(counts.values()):,}행."
                  f" 받는 PC에서: python tools/transfer_run.py --import {out.name}")
            return 0

        counts = import_run(conn, Path(args.import_path), args.overwrite, args.dry_run)
        head = "확인만 했습니다 (넣지 않음)" if args.dry_run else "적재했습니다"
        print(f"{args.import_path} → {active_db_path()}  {head}")
        for name, rows in counts.items():
            print(f"  {name:22s} {rows:>8,} 행")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
