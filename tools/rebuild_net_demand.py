"""적재된 모든 기간의 순수요(net_demand)를 다시 계산한다.

`raw_to_net.py`가 평일만 남기던 시절(1.14.0 이전)에 만들어진 순수요에는 휴일이
없다. 휴일 계획을 쓰려면 기간마다 한 번씩 다시 돌려야 하는데, 12개월을 손으로
치기 번거로워 묶어 둔다.

실행:
    python tools/rebuild_net_demand.py            # DB에 적재된 전 기간
    python tools/rebuild_net_demand.py --period "25년 11월"   # 하나만
    python tools/rebuild_net_demand.py --dry-run  # 대상만 확인
    python tools/rebuild_net_demand.py --fill-idle   # 저장된 표에 이용이 없는 날만 0으로 채운다 (1.26.409)

`--fill-idle`은 대여이력을 다시 읽지 않는다 — 저장된 순수요 표를 (그 표의 날짜 × 대여소)로 펴서
DB와 CSV에 다시 쓴다(`project_config.fill_idle_days`, 사용자 결정 2026-11-05 "표본에 넣어라").
`raw_to_net`이 같은 함수를 쓰므로 다시 계산한 표와 같다. 덮기 전의 CSV는 `순수요/채우기_전/`에 한 번만
남긴다(이미 있으면 그대로 둔다 — 두 번 돌려도 원본이 덮이지 않게). 꽉 찬 표는 건드리지 않는다.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db
from project_config import NET_DEMAND_DIR, PROJECT_ROOT, fill_idle_days, holiday_mask

RAW_TO_NET = PROJECT_ROOT / "pipeline" / "step0_collect" / "raw_to_net.py"


def loaded_periods() -> list:
    """rental_history에 적재된 기간 목록."""
    with db.session() as conn:
        return db.distinct_values(conn, "rental_history", "period")


def day_counts(period: str) -> tuple:
    """해당 기간 net_demand의 (평일 일수, 휴일 일수)."""
    with db.session() as conn:
        frame = db.load_frame(conn, "net_demand", period=period)
    if frame.empty:
        return (0, 0)
    dates = frame["date"].drop_duplicates()
    mask = holiday_mask(dates)
    return (int((~mask).sum()), int(mask.sum()))


def fill_idle_periods(periods: list, dry_run: bool = False) -> list:
    """저장된 순수요 표에 이용이 없는 (날짜, 대여소)를 0으로 채운다. 실패한 기간 목록을 돌려준다."""
    failures = []
    backup_dir = NET_DEMAND_DIR / "채우기_전"
    print(f"{'기간':12} {'이전 행':>10} {'이후 행':>10} {'채운 행':>8}")
    for period in periods:
        with db.session() as conn:
            frame = db.load_frame(conn, "net_demand", period=period)
        if frame.empty:
            print(f"{period:12} 표가 없습니다")
            continue
        filled = fill_idle_days(frame, date_column="date")
        added = len(filled) - len(frame)
        print(f"{period:12} {len(frame):>10,} {len(filled):>10,} {added:>8,}"
              + ("  (dry-run)" if dry_run and added else ""))
        if dry_run or not added:
            continue

        body = filled.drop(columns=["period"])
        csv_path = NET_DEMAND_DIR / f"st_net_daily ({period}).csv"
        if csv_path.is_file():
            backup_dir.mkdir(parents=True, exist_ok=True)
            if not (backup_dir / csv_path.name).exists():
                csv_path.replace(backup_dir / csv_path.name)
        body.rename(columns={"date": "날짜"}).to_csv(csv_path, encoding="utf-8", index=False)
        if db.save_output("net_demand", body, period=period) != len(body):
            failures.append(period)
            print(f"{period:12} DB 기록 실패 — 위 경고를 보십시오")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period", action="append",
                        help="특정 기간만 (여러 번 지정 가능). 생략하면 전 기간")
    parser.add_argument("--dry-run", action="store_true", help="대상만 출력")
    parser.add_argument("--fill-idle", action="store_true",
                        help="대여이력을 다시 읽지 않고 저장된 표에 이용이 없는 날만 0으로 채운다")
    args = parser.parse_args()

    if args.fill_idle:
        with db.session() as conn:
            stored = db.distinct_values(conn, "net_demand", "period")
        periods = args.period or stored
        if not periods:
            print("저장된 순수요가 없습니다.")
            return 1
        failures = fill_idle_periods(periods, dry_run=args.dry_run)
        if failures:
            print(f"\n실패한 기간: {', '.join(failures)}")
            return 1
        return 0

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
