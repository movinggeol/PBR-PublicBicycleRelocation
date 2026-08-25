"""지금 DB에 무엇이 들어 있는지 본다 — 테이블·컬럼·행 수.

문서([DB_SCHEMA.md](../docs/구현/DB_SCHEMA.md))는 **있어야 할 것**을 적고, 이 도구는
**실제로 있는 것**을 보여 준다. 둘이 어긋날 때(마이그레이션이 안 돈 DB, 옛 산출물이
섞인 DB) 어느 쪽이 사실인지 확인하는 용도다.

원래 루트에 `db_test.py`로 있던 일회성 스크립트다. 저장소 규약은 **일회성 스크립트를
루트에 두지 않는 것**이라 1.20.7에서 여기로 옮기며 다듬었다 — `sqlite3`를 직접 열지
않고 `db.session()`을 거치므로 `PBR_DB_PATH`도 그대로 듣는다.

실행:
    python tools/show_schema.py                 # 테이블 목록과 행 수
    python tools/show_schema.py --columns       # 컬럼까지 펼쳐서
    python tools/show_schema.py --table runs    # 한 테이블만
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db


def table_names(conn, only: str | None) -> list:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall()
    names = [row[0] for row in rows if not row[0].startswith("sqlite_")]
    if only:
        names = [name for name in names if name == only]
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description="DB에 실제로 들어 있는 것 보기")
    parser.add_argument("--table", help="이 테이블만 본다")
    parser.add_argument("--columns", action="store_true", help="컬럼까지 펼친다")
    args = parser.parse_args()

    with db.session() as conn:
        names = table_names(conn, args.table)
        if not names:
            print("테이블이 없습니다." if not args.table
                  else f"'{args.table}' 테이블이 없습니다.")
            return 1

        print(f"{'테이블':28} {'행 수':>12}")
        for name in names:
            count = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            print(f"{name:28} {count:12,}")

            if args.columns:
                for column in conn.execute(f"PRAGMA table_info({name})"):
                    # (cid, name, type, notnull, default, pk)
                    flags = []
                    if column[5]:
                        flags.append("PK")
                    if column[3]:
                        flags.append("NOT NULL")
                    mark = f"  [{', '.join(flags)}]" if flags else ""
                    print(f"    {column[1]:24} {column[2] or '-':10}{mark}")
                print()

        print(f"\n테이블 {len(names)}개. 정본 정의는 db.py의 SCHEMA 상수다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
