"""step0~step4 전체 파이프라인 실행기.

각 단계 파일을 프로젝트 루트에서 subprocess로 호출합니다.
공통 설정(--now/--period/--duration/--raw-file)은 그대로 하위 스크립트에
전달되며, 각 스크립트는 project_config를 통해 이를 읽습니다.

기본 실행:
    python run_pipeline.py

실행 목록만 확인:
    python run_pipeline.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from project_config import (
    DEFAULT_FLEET_SIZE, DEFAULT_VEHICLES_PER_ROUND, ensure_output_dirs,
    normalize_fleet_size, normalize_per_round,
)


# 이 파일이 있는 디렉터리가 프로젝트 루트입니다.
# 단계 파일들은 data/... 상대 경로를 사용하므로 모든 subprocess의
# 현재 작업 디렉터리(cwd)를 이 경로로 지정해야 합니다.
ROOT = Path(__file__).resolve().parent


# 데이터 의존성이 있는 순서대로 단계 파일을 그룹화합니다.
# 앞 단계의 산출물이 다음 단계의 입력이 되므로 순서를 바꾸면 안 됩니다.
STAGES = {
    "api": [
        Path("step0 (raw데이터 처리)") / "tashu_api.py",
        Path("step0 (raw데이터 처리)") / "extract_parking_lot.py",
        Path("step0 (raw데이터 처리)") / "api_to_info.py",
    ],
    "eda": [
        Path("step0(전처리 및 EDA)") / "concat_1year_file.py",
        Path("step0(전처리 및 EDA)") / "EDA.py",
    ],
    "preprocess": [
        Path("step0 (raw데이터 처리)") / "raw_to_net.py",
        Path("step0 (raw데이터 처리)") / "calculate_target_qty.py",
    ],
    "selection": [
        Path("step1 (작업대상 선정 및 클러스터링)") / "1.top_st_clustering.py",
        Path("step1 (작업대상 선정 및 클러스터링)") / "st_visualization.py",
    ],
    "optimization": [
        Path("step2 (ilp, vrp)") / "ilp.py",
        Path("step2 (ilp, vrp)") / "vrp.py",
    ],
    "visualization": [
        Path("step3 (결과 시각화)") / "main.py",
    ],
    "evaluation": [
        Path("step4 (성과 지표)") / "imbalance.py",
    ],
}


def parse_args() -> argparse.Namespace:
    """통합 실행기에서 사용할 명령행 옵션을 정의합니다."""

    parser = argparse.ArgumentParser(
        description="Run the complete public-bike rebalancing pipeline."
    )

    # 분석 시점과 입력 파일 옵션입니다.
    # 각 하위 스크립트에 전달해 공통 설정으로 사용할 수 있도록 합니다.
    parser.add_argument("--now", help="분석 시점. 예: 2026-05-21 18")
    parser.add_argument("--period", help="순수요 입력 기간. 예: 25년 11월")
    parser.add_argument("--duration", help="시간대 구간. 예: _05_10")
    parser.add_argument("--raw-file", help="원천 CSV 경로(프로젝트 루트 기준)")

    # 차량 대수는 다른 공통 설정과 달리 CLI 인자가 아니라 환경변수로 하위 단계에
    # 전달한다 — project_config가 모듈 import 시점에 읽는 상수라서, 각 단계
    # 프로세스의 환경에 심어야 반영된다.
    parser.add_argument(
        "--fleet-size",
        type=int,
        help=f"보유 차량 대수 (기본 {DEFAULT_FLEET_SIZE}). 회차 투입 상한도 이 값을 넘지 않는다",
    )
    parser.add_argument(
        "--vehicles-per-round",
        type=int,
        help=f"한 회차 투입 대수 상한 (기본 {DEFAULT_VEHICLES_PER_ROUND})."
             " step1의 클러스터 수 상한이 된다",
    )

    # API와 EDA는 이미 산출물이 있는 경우 선택적으로 생략할 수 있습니다.
    parser.add_argument("--skip-api", action="store_true", help="API 수집 생략")
    parser.add_argument("--skip-eda", action="store_true", help="EDA 생략")

    # 기본은 실패 즉시 중단입니다.
    # 디버깅이나 일부 결과 확보가 필요할 때만 계속 실행 옵션을 사용합니다.
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="한 단계가 실패해도 다음 단계를 계속 실행",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="실제 실행 없이 실행 목록만 출력",
    )
    return parser.parse_args()


def build_command(script: Path, args: argparse.Namespace) -> list[str]:
    """현재 Python 환경으로 하위 스크립트를 실행할 명령을 만듭니다."""

    # sys.executable을 사용하면 가상환경을 활성화한 경우에도
    # 현재 사용 중인 동일한 Python 환경으로 모든 단계를 실행합니다.
    command = [sys.executable, str(script)]

    # 값이 지정된 옵션만 전달합니다.
    # 기존 스크립트가 옵션을 사용하지 않더라도 향후 공통 설정 연결 시
    # 동일한 통합 실행 명령을 그대로 사용할 수 있습니다.
    for option, value in (
        ("--now", args.now),
        ("--period", args.period),
        ("--duration", args.duration),
        ("--raw-file", args.raw_file),
    ):
        if value:
            command.extend([option, value])
    return command


def build_env(args: argparse.Namespace) -> dict:
    """하위 단계에 물려줄 환경변수를 만듭니다.

    차량 대수는 project_config가 import 시점에 읽는 상수이므로 명령행이 아니라
    환경변수(PBR_FLEET_SIZE / PBR_VEHICLES_PER_ROUND)로 전달합니다.
    """

    env = dict(os.environ)
    if args.fleet_size is not None:
        env["PBR_FLEET_SIZE"] = str(normalize_fleet_size(args.fleet_size))
    if args.vehicles_per_round is not None:
        env["PBR_VEHICLES_PER_ROUND"] = str(normalize_per_round(args.vehicles_per_round))
    return env


def selected_scripts(args: argparse.Namespace) -> Iterable[Path]:
    """옵션에 맞는 단계 파일을 의존성 순서대로 반환합니다."""

    groups: list[str] = []

    if not args.skip_api:
        groups.append("api")
    if not args.skip_eda:
        groups.append("eda")

    # 전처리는 API/EDA 이후에 실행되어야 순수요와 재배치량을 계산할 수 있습니다.
    groups.extend(
        ["preprocess", "selection", "optimization", "visualization", "evaluation"]
    )

    for group in groups:
        yield from STAGES[group]


def main() -> int:
    """전체 파이프라인을 실행하고 실패 상태를 반환합니다."""

    args = parse_args()
    scripts = list(selected_scripts(args))

    # 잘못된 차량 대수는 단계를 하나라도 돌리기 전에 걸러냅니다.
    try:
        env = build_env(args)
        fleet = normalize_fleet_size(env.get("PBR_FLEET_SIZE", DEFAULT_FLEET_SIZE))
        per_round = min(
            normalize_per_round(env.get("PBR_VEHICLES_PER_ROUND", DEFAULT_VEHICLES_PER_ROUND)),
            fleet,
        )
    except ValueError as err:
        print(f"설정 오류: {err}")
        return 2

    print("=== Public Bike Rebalancing Pipeline ===")
    if args.fleet_size is not None or args.vehicles_per_round is not None:
        # 회차 투입 상한은 보유 대수로 잘리므로, 실제 적용되는 값을 보여줍니다.
        print(f"보유 차량 {fleet}대 · 회차당 투입 상한 {per_round}대로 실행합니다.")
    for index, script in enumerate(scripts, start=1):
        print(f"[{index}/{len(scripts)}] {script}")

    # dry-run은 파일 존재 여부와 실행 순서만 확인할 때 사용합니다.
    if args.dry_run:
        return 0

    # 산출물 폴더가 없어 저장에 실패하는 일을 예방합니다.
    ensure_output_dirs()

    failures: list[tuple[Path, int]] = []

    for index, script in enumerate(scripts, start=1):
        full_path = ROOT / script

        # 파일이 없으면 실행할 수 없으므로 오류 목록에 기록합니다.
        if not full_path.exists():
            print(f"파일이 없습니다: {script}")
            failures.append((script, 2))
            if not args.continue_on_error:
                return 2
            continue

        command = build_command(script, args)
        print(f"\n[{index}/{len(scripts)}] 실행: {' '.join(command)}")

        # cwd를 ROOT로 고정해야 data/ 상대 경로가 모든 단계에서 동일합니다.
        completed = subprocess.run(command, cwd=ROOT, env=env)

        if completed.returncode == 0:
            print(f"완료: {script}")
            continue

        print(f"실패: {script} (exit code={completed.returncode})")
        failures.append((script, completed.returncode))

        # 뒤 단계는 앞 단계 산출물에 의존하므로 기본적으로 즉시 중단합니다.
        if not args.continue_on_error:
            print("파이프라인을 중단합니다.")
            return completed.returncode

    if failures:
        print("\n실패한 단계:")
        for script, code in failures:
            print(f"- {script}: {code}")
        return 1

    print("\n모든 단계가 완료되었습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

