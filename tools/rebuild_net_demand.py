"""적재된 모든 기간의 순수요(net_demand)를 다시 계산한다.

`raw_to_net.py`가 평일만 남기던 시절(1.14.0 이전)에 만들어진 순수요에는 휴일이
없다. 휴일 계획을 쓰려면 기간마다 한 번씩 다시 돌려야 하는데, 12개월을 손으로
치기 번거로워 묶어 둔다.

실행:
    python tools/rebuild_net_demand.py            # DB에 적재된 전 기간
    python tools/rebuild_net_demand.py --period "25년 11월"   # 하나만
    python tools/rebuild_net_demand.py --dry-run  # 대상만 확인
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db
from project_config import PROJECT_ROOT, holiday_mask

RAW_TO_NET = PROJECT_ROOT / "step0 (raw데이터 처리)" / "raw_to_net.py"


def loaded_periods() -> list:
    """rental_history에 적재된 기간 목록."""
    with db.session() as conn:
        return [row[0] for row in conn.execute(
            "SELECT DISTINCT period FROM rental_history ORDER BY period")]


def day_counts(period: str) -> tuple:
    """해당 기간 net_demand의 (평일 일수, 휴일 일수)."""
    with db.session() as conn:
        frame = db.load_frame(conn, "net_demand", period=period)
    if frame.empty:
        return (0, 0)
    dates = frame["date"].drop_duplicates()
    mask = holiday_mask(dates)
    return (int((~mask).sum()), int(mask.sum()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period", action="append",
                        help="특정 기간만 (여러 번 지정 가능). 생략하면 전 기간")
    parser.add_argument("--dry-run", action="store_true", help="대상만 출력")
    args = parser.parse_args()

    periods = args.period or loaded_periods()
    if not periods:
        print("적재된 대여이력이 없습니다. tools/load_rentals.py로 먼저 적재하세요.")
        return 1

    print(f"대상 기간 {len(periods)}개\n")
    print(f"{'기간':12} {'이전(평일/휴일)':>16} {'이후(평일/휴일)':>16}")

    failures = []
    for period in periods:
        before = day_counts(period)
        if args.dry_run:
            print(f"{period:12} {f'{before[0]}/{before[1]}':>16} {'(dry-run)':>16}")
            continue

        done = subprocess.run(
            [sys.executable, str(RAW_TO_NET), "--period", period],
            cwd=PROJECT_ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        if done.returncode != 0:
            failures.append(period)
            print(f"{period:12} 실패\n{done.stderr[-800:]}")
            continue

        after = day_counts(period)
        print(f"{period:12} {f'{before[0]}/{before[1]}':>16} "
              f"{f'{after[0]}/{after[1]}':>16}")

    if failures:
        print(f"\n실패한 기간: {', '.join(failures)}")
        return 1
    if not args.dry_run:
        print("\n전 기간 재계산 완료. 이제 --day-type holiday로 휴일 계획을 만들 수 있습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
