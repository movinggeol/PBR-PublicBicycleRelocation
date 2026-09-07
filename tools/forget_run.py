"""실행 하나를 DB에서 **완전히 지운다**. 되돌릴 수 없다.

## 왜 웹 화면이 아니라 여기 있나

한 DB에 운영 계획·실험·수집 기록이 함께 쌓인다. 실험을 여러 번 돌리고 나면
계획 목록이 견줘 볼 일 없는 라벨로 채워진다 — 그것을 걷어낼 길이 필요하다.

그런데 실행 하나는 **테이블 13곳**에 흩어져 있다(`run_label`을 가진 테이블
전부). 지우면 지표도, 경로도, 대여소별 계획도 함께 사라지고 되돌릴 수 없다.
웹 화면의 단추 한 번으로 할 일이 아니라서 도구로 뺐다. 화면에서는 종류만
바꿀 수 있고(운영/실험/수집), 그것만으로도 계획 목록은 깨끗해진다.

⚠️ **먼저 `--dry-run`으로 세어 보라.** 무엇이 몇 행 지워지는지 보여 준다.

⚠️ **CSV는 건드리지 않는다.** 산출물 파일은 `data/pp_data/**`에 그대로 남고,
   웹의 데이터·지도 화면은 파일을 읽으므로 거기서는 계속 보인다. 파일까지
   치우려면 이 도구가 마지막에 찍어 주는 목록을 보고 사람이 지운다 —
   파일 삭제는 라벨이 겹치는 순간 되돌릴 수 없이 남의 것을 지운다.

> 재현: `python tools/forget_run.py --list`

## 쓰는 법

    python tools/forget_run.py --list                    # 어떤 실행이 있나
    python tools/forget_run.py "sweep-21" --dry-run      # 몇 행이 지워지나
    python tools/forget_run.py "sweep-21"                # 정말 지운다
    python tools/forget_run.py --kind probe --dry-run    # 수집 기록 전부
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import db
from project_config import DATA_ROOT


def scoped_tables(conn) -> list:
    """`run_label`로 범위가 갈리는 테이블 이름. **DB에 물어본다.**

    목록을 코드에 박아 두면 테이블이 늘 때 조용히 빠뜨린다 — 그러면 '지웠다'
    고 해 놓고 어딘가에 남는다.
    """
    names = []
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    for (table,) in rows:
        columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
        if "run_label" in columns:
            names.append(table)
    return names


def count_rows(conn, tables: list, labels: list) -> dict:
    counts = {}
    marks = ",".join("?" * len(labels))
    for table in tables:
        n = conn.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE run_label IN ({marks})',
            labels).fetchone()[0]
        if n:
            counts[table] = n
    return counts


def leftover_files(labels: list) -> list:
    """지운 실행의 이름이 붙은 산출물 파일. **지우지 않고 알려만 준다.**"""
    found = []
    for label in labels:
        found.extend(sorted((DATA_ROOT).rglob(f"*({label}).*")))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="실행 하나를 DB에서 지운다 (되돌릴 수 없음)")
    parser.add_argument("run_label", nargs="*", help="지울 실행 이름")
    parser.add_argument("--kind", choices=db.RUN_KINDS,
                        help="이 종류의 실행을 전부 (예: probe)")
    parser.add_argument("--list", action="store_true", help="실행 목록만 보여준다")
    parser.add_argument("--dry-run", action="store_true", help="세어만 보고 지우지 않는다")
    parser.add_argument("--yes", action="store_true", help="확인 없이 진행")
    args = parser.parse_args()

    with db.session() as conn:
        runs = db.list_runs(conn)

        if args.list or (not args.run_label and not args.kind):
            if runs.empty:
                print("기록된 실행이 없습니다.")
                return 0
            print(f"{'실행 이름':28s} {'종류':10s} 기록 시각")
            for row in runs.itertuples():
                print(f"{row.run_label:28s} {str(row.kind):10s} {row.created_at}")
            if not args.list:
                print("\n지울 실행 이름을 인자로 주세요. --dry-run으로 먼저 세어 보세요.")
            return 0

        labels = list(args.run_label)
        if args.kind:
            labels += runs.loc[runs["kind"] == args.kind, "run_label"].tolist()
        labels = sorted(set(labels))

        known = set(runs["run_label"]) if not runs.empty else set()
        tables = scoped_tables(conn)
        counts = count_rows(conn, tables, labels)

        # `counts`의 열쇠는 **테이블 이름**이지 라벨이 아니다. 예전에는
        # `l not in {t for t in counts}`가 한 항 더 붙어 있었는데, 라벨과
        # 테이블 이름을 견주는 것이라 늘 참이어서 아무것도 거르지 않았다.
        unknown = [l for l in labels if l not in known]
        if not counts:
            print(f"지울 것이 없습니다: {', '.join(labels)}")
            if unknown:
                print("  (이름이 정확한지 --list로 확인하세요)")
            return 1

        print(f"지울 실행 {len(labels)}건: {', '.join(labels)}\n")
        total = 0
        for table, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {table:22s} {n:>8}행")
            total += n
        print(f"  {'합계':22s} {total:>8}행")

        files = leftover_files(labels)
        if files:
            print(f"\n산출물 파일 {len(files)}개는 **지우지 않습니다** (사람이 판단할 일):")
            for path in files[:8]:
                print(f"  - {path.relative_to(PROJECT_ROOT)}")
            if len(files) > 8:
                print(f"  ... 그 밖에 {len(files) - 8}개")

        if args.dry_run:
            print("\n--dry-run이라 지우지 않았습니다.")
            return 0

        if not args.yes:
            print(f"\n⚠️ 되돌릴 수 없습니다. {total}행을 정말 지우려면 --yes를 붙이세요.")
            return 1

        marks = ",".join("?" * len(labels))
        for table in counts:
            conn.execute(f'DELETE FROM "{table}" WHERE run_label IN ({marks})', labels)
        conn.commit()
        print(f"\n{total}행을 지웠습니다.")

        # 정말 비었는지 **다시 세어 확인한다.** '지웠다'는 말만 믿지 않는다.
        left = count_rows(conn, tables, labels)
        if left:
            print(f"⚠️ 남은 행이 있습니다: {left}")
            return 1
        print("확인: 남은 행 없음.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
