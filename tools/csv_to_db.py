"""기존 CSV 산출물을 SQLite(bike_system.db)로 적재한다 (DB_PLAN.md 1단계).

파일명에 박혀 있던 {now}/{period}/{duration} 라벨을 컬럼으로 옮긴다.
step 스크립트는 아직 CSV를 쓰므로, 이 도구로 DB에 밀어 넣어 두면
웹 API와 실행 간 비교를 DB 기준으로 시험해볼 수 있다.

실행:
    python tools/csv_to_db.py                       # project_config 기본 라벨
    python tools/csv_to_db.py --now "2026-05-21 18" --period "25년 11월" --duration "_05_10"
    python tools/csv_to_db.py --list                # 적재된 실행 이력만 확인
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

import db
from project_config import PP_ROOT, duration_list, get_runtime_config

# 테이블 -> (pp_data 기준 경로 템플릿, 스코프 종류)
#   run      : {now} 만 사용
#   period   : {period} 만 사용
#   duration : {now} + {duration}
SOURCES = [
    ("station_info", "대여소 정보/st_info ({now}).csv", "run"),
    ("parking_lot", "대여소별 주차대수/대여소별_주차대수 ({now}).csv", "run"),
    ("net_demand", "순수요/st_net_daily ({period}).csv", "period"),
    ("rebalance_plan", "재배치 정보/rebal_qty{duration} ({now}).csv", "duration"),
    ("pick_drop", "ILP/후보/top{duration} ({now}).csv", "duration"),
    ("ilp_plan", "ILP/ILP_plan{duration} ({now}).csv", "duration"),
    ("vrp_plan", "VRP/VRP_plan{duration} ({now}).csv", "duration"),
    ("metrics", "성능 지표/verification{duration} ({now}).csv", "duration"),
    ("route_summary", "성능 지표/route_summary{duration} ({now}).csv", "duration"),
]


def import_outputs(conn, now: str, period: str, durations, raw_file: Optional[str] = None) -> dict:
    """CSV 산출물을 DB에 적재하고 {테이블: 행 수}를 돌려준다. 없는 파일은 건너뛴다."""
    db.record_run(conn, run_label=now, period=period,
                  duration=",".join(durations), raw_file=raw_file)

    loaded: dict = {}
    for table, template, scope in SOURCES:
        targets = durations if scope == "duration" else [None]

        for duration in targets:
            path = PP_ROOT / template.format(now=now, period=period, duration=duration or "")
            if not path.is_file():
                continue

            frame = pd.read_csv(path, encoding="utf-8", low_memory=False)
            if scope == "period":
                rows = db.save_frame(conn, table, frame, period=period)
                key = table
            elif scope == "run":
                rows = db.save_frame(conn, table, frame, run_label=now)
                key = table
            else:
                rows = db.save_frame(conn, table, frame, run_label=now, duration=duration)
                key = f"{table}{duration}"

            loaded[key] = rows

    return loaded


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(add_help=True, description="CSV 산출물 -> SQLite 적재")
    parser.add_argument("--list", action="store_true", help="적재된 실행 이력만 출력")
    args, _ = parser.parse_known_args()

    config = get_runtime_config()

    with db.connect() as conn:
        db.init_schema(conn)

        if args.list:
            runs = db.list_runs(conn)
            print(runs.to_string(index=False) if not runs.empty else "적재된 실행 이력이 없습니다.")
            return 0

        durations = duration_list(config)
        loaded = import_outputs(conn, now=config.now, period=config.period,
                                durations=durations, raw_file=config.raw_file)

        if not loaded:
            print(f"적재할 CSV를 찾지 못했습니다. (now={config.now!r}, period={config.period!r})")
            print(f"확인 경로: {PP_ROOT}")
            return 1

        print(f"DB: {db.DB_PATH}")
        print(f"실행 라벨: {config.now}  (period={config.period}, duration={','.join(durations)})")
        for name, rows in loaded.items():
            print(f"  {name:28s} {rows:>7,} 행")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
