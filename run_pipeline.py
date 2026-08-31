"""step0~step4 전체 파이프라인 실행기.

각 단계 파일을 프로젝트 루트에서 subprocess로 호출합니다.
공통 설정(--now/--period/--duration/--raw-file/--day-type/--target-date/
--warmup-period/--warmup-days)은 그대로 하위 스크립트에 전달되며, 각 스크립트는
project_config를 통해 이를 읽습니다. 차량 대수만 예외로 환경변수로 전달합니다
(project_config가 import 시점의 상수로 읽기 때문 — build_env 참고).

기본 실행:
    python run_pipeline.py

실행 목록만 확인:
    python run_pipeline.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable

from project_config import (
    CLUSTER_SEED, DAY_TYPE_AUTO, DAY_TYPES, DEFAULT_DAY_TYPE, DEFAULT_FLEET_SIZE,
    DEFAULT_VEHICLES_PER_ROUND, DEFAULT_WARMUP_DAYS, ensure_output_dirs,
    get_runtime_config, normalize_fleet_size, normalize_per_round,
    snapshot_labels, snapshot_paths,
)


# 이 파일이 있는 디렉터리가 프로젝트 루트입니다.
# 단계 파일들은 data/... 상대 경로를 사용하므로 모든 subprocess의
# 현재 작업 디렉터리(cwd)를 이 경로로 지정해야 합니다.
ROOT = Path(__file__).resolve().parent


# 데이터 의존성이 있는 순서대로 단계 파일을 그룹화합니다.
# 앞 단계의 산출물이 다음 단계의 입력이 되므로 순서를 바꾸면 안 됩니다.
STAGES = {
    # 라이브 타슈 API를 부르는 **유일한** 단계. 나머지 둘은 그 결과 CSV만 읽으므로
    # 키 없이도 돈다 — 그래서 따로 뗐다(`--skip-fetch`, 1.26.56).
    "fetch": [
        Path("step0_collect") / "tashu_api.py",
    ],
    "api": [
        Path("step0_collect") / "extract_parking_lot.py",
        Path("step0_collect") / "api_to_info.py",
    ],
    "eda": [
        Path("step0_eda") / "concat_1year_file.py",
        Path("step0_eda") / "EDA.py",
    ],
    "preprocess": [
        Path("step0_collect") / "raw_to_net.py",
        Path("step0_collect") / "calculate_target_qty.py",
    ],
    "selection": [
        Path("step1_cluster") / "top_st_clustering.py",
        Path("step1_cluster") / "st_visualization.py",
    ],
    "optimization": [
        Path("step2_optimize") / "ilp.py",
        Path("step2_optimize") / "vrp.py",
    ],
    "visualization": [
        Path("step3_map") / "main.py",
    ],
    "evaluation": [
        Path("step4_metrics") / "imbalance.py",
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
    parser.add_argument("--day-type", choices=(*DAY_TYPES, DAY_TYPE_AUTO),
                        help=f"요일 구분 (기본 {DEFAULT_DAY_TYPE}). 평일과 휴일은 섞지 않는다."
                             " auto는 --target-date를 달력으로 판정")
    parser.add_argument("--target-date",
                        help="계획 대상일(YYYY-MM-DD, 기본 오늘). auto 판정의 기준")

    # 계절 수준 보정(warmup). project_config가 정의한 인자를 그대로 받아 하위 단계에
    # 넘긴다 — 여기서 받지 않으면 argparse가 '알 수 없는 인자'로 거절해 버린다.
    parser.add_argument("--warmup-period",
                        help="계절 보정에 쓸 기간(기본: 계획 대상일의 달)")
    parser.add_argument("--warmup-days", type=int,
                        help=f"보정에 쓸 일수 (기본 {DEFAULT_WARMUP_DAYS}, 0이면 끔)")

    # 차량 대수는 다른 공통 설정과 달리 CLI 인자가 아니라 환경변수로 하위 단계에
    # 전달한다 — project_config가 모듈 import 시점에 읽는 상수라서, 각 단계
    # 프로세스의 환경에 심어야 반영된다.
    parser.add_argument(
        "--fleet-size",
        type=int,
        help=f"보유 차량 대수 (기본 {DEFAULT_FLEET_SIZE}). 회차 투입 상한도 이 값을 넘지 않는다",
    )
    parser.add_argument(
        "--enforce-time-budget",
        action="store_true",
        help="시간 예산을 제약으로 건다. VRP가 예산을 넘기는 작업 앞에서 멈추고"
             " depot으로 돌아온다(남은 작업은 미집행). 기본은 사후 점검만",
    )
    parser.add_argument(
        "--seed",
        type=int,
        help=f"K-Medoids 초기화 씨앗 (기본 {CLUSTER_SEED})."
             " 같은 입력을 여러 씨앗으로 돌려 결과의 흔들림을 재는 용도이고,"
             " 표를 실을 때 씨앗 1회로 판단하지 않기 위한 손잡이다",
    )
    parser.add_argument(
        "--vehicles-per-round",
        type=int,
        help=f"한 회차 투입 대수 상한 (기본 {DEFAULT_VEHICLES_PER_ROUND})."
             " step1의 클러스터 수 상한이 된다",
    )

    # API와 EDA는 이미 산출물이 있는 경우 선택적으로 생략할 수 있습니다.
    parser.add_argument("--skip-api", action="store_true", help="API 수집 생략")
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="라이브 타슈 API 호출(tashu_api.py)만 생략하고, 이미 있는 재고 CSV로"
             " 나머지 수집 단계를 돌린다. 합성 데이터 재현에 쓴다"
             " (--skip-api와 달리 직전 실행 스냅샷을 물려받지 않는다)",
    )
    parser.add_argument("--skip-eda", action="store_true", help="EDA 생략")
    parser.add_argument("--skip-map", action="store_true",
                        help="step3 TMAP 지도 생략 (TMAP 키가 없을 때)")

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
        ("--day-type", args.day_type),
        ("--target-date", args.target_date),
        ("--warmup-period", args.warmup_period),
    ):
        if value:
            command.extend([option, value])

    # --warmup-days는 0이 '보정을 끈다'는 뜻이라 값으로 판정하면 안 된다.
    if args.warmup_days is not None:
        command.extend(["--warmup-days", str(args.warmup_days)])
    return command


def build_env(args: argparse.Namespace) -> dict:
    """하위 단계에 물려줄 환경변수를 만듭니다.

    차량 대수는 project_config가 import 시점에 읽는 상수이므로 명령행이 아니라
    환경변수(PBR_FLEET_SIZE / PBR_VEHICLES_PER_ROUND)로 전달합니다.
    """

    env = dict(os.environ)

    # 윈도우 파이썬은 stdout이 콘솔이 아니라 **파이프·파일이면** 로캘 코드페이지
    # (한국어 윈도우에서 cp949)로 인코딩한다. 그러면 로그 문구의 '—' 한 글자에
    # 단계가 통째로 죽는다(실제로 calculate_target_qty가 그렇게 멈췄다).
    # 웹은 jobs.py가 이미 같은 방어를 하고 있다 — CLI에도 똑같이 건다.
    env.setdefault("PYTHONIOENCODING", "utf-8")

    if args.fleet_size is not None:
        env["PBR_FLEET_SIZE"] = str(normalize_fleet_size(args.fleet_size))
    if args.vehicles_per_round is not None:
        env["PBR_VEHICLES_PER_ROUND"] = str(normalize_per_round(args.vehicles_per_round))
    if getattr(args, "enforce_time_budget", False):
        env["PBR_ENFORCE_TIME_BUDGET"] = "1"
    if getattr(args, "seed", None) is not None:
        env["PBR_CLUSTER_SEED"] = str(args.seed)
    return env


def selected_scripts(args: argparse.Namespace) -> Iterable[Path]:
    """옵션에 맞는 단계 파일을 의존성 순서대로 반환합니다."""

    groups: list[str] = []

    if not args.skip_api:
        # --skip-fetch는 **라이브 API 호출만** 건너뛴다. 뒤의 두 단계는 재고 CSV를
        # 읽을 뿐이라, 합성 데이터로 만든 CSV를 그대로 물려 돌릴 수 있다.
        # --skip-api(전부 건너뛰기)와 달리 **직전 실행의 스냅샷을 물려받지 않는다** —
        # 그래서 합성 데이터로 돌린다고 해 놓고 실데이터가 섞여 드는 일이 없다.
        if not args.skip_fetch:
            groups.append("fetch")
        groups.append("api")
    if not args.skip_eda:
        groups.append("eda")

    # 전처리는 API/EDA 이후에 실행되어야 순수요와 재배치량을 계산할 수 있습니다.
    groups.extend(["preprocess", "selection", "optimization"])

    # step3는 TMAP 실도로 경로를 받아 오므로 API 키가 필요합니다. 키가 없는
    # 환경(합성 데이터 시연·CI)에서 나머지 단계를 마저 돌리려면 건너뜁니다 —
    # 지도는 산출물일 뿐이고 지표(step4)는 VRP 결과만으로 계산됩니다.
    if not args.skip_map:
        groups.append("visualization")

    groups.append("evaluation")

    for group in groups:
        yield from STAGES[group]


def inherit_snapshot(label: str) -> int:
    """`--skip-api`로 건너뛴 재고 스냅샷을 **가장 최근 실행에서 물려받는다.**

    API 수집 단계가 만드는 세 파일은 실행 라벨마다 따로다. 그래서 `--skip-api`를
    켠 채 **새 실행 이름**을 쓰면 파일이 없어서 다음 단계(calculate_target_qty)가
    바로 멈췄다 — 웹 폼의 "API 수집 생략"도 같았고, 안내만 보고는 알 수 없었다.

    물려받은 라벨을 반드시 찍는다. 어느 시점 재고로 세운 계획인지가 로그에
    남아야 한다 — 조용히 물려받으면 출처를 알 수 없는 계획이 된다.

    반환: 복사한 파일 수. 이미 다 있으면 0.
    """
    missing = [p for p in snapshot_paths(label) if not p.exists()]
    if not missing:
        return 0

    donors = [other for other in snapshot_labels() if other != label]
    if not donors:
        print(f"설정 오류: --skip-api를 켰지만 물려받을 재고 스냅샷이 없습니다.")
        print(f"  '{label}' 라벨의 파일이 없고, 다른 실행의 스냅샷도 없습니다.")
        print(f"  --skip-api를 빼고 한 번 수집하세요.")
        return -1

    donor = donors[0]
    print(f"[안내] --skip-api: '{donor}'의 재고 스냅샷을 물려받습니다"
          f" (요청 라벨 '{label}'에 파일이 없음).")
    print(f"  ⚠ 그 시점 재고로 계획을 세웁니다. 지금 재고로 세우려면 --skip-api를 빼세요.")

    copied = 0
    for source, target in zip(snapshot_paths(donor), snapshot_paths(label)):
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        print(f"  {source.name} -> {target.name}")
        copied += 1
    return copied


def format_elapsed(seconds: float) -> str:
    """경과 시간을 사람이 읽는 말로. 1분을 넘으면 분·초로 끊는다."""
    if seconds < 60:
        return f"{seconds:.1f}초"
    return f"{int(seconds // 60)}분 {seconds % 60:.0f}초"


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
    # 요일 구분은 auto일 수 있으므로 **해석된 값**을 보여준다.
    # 로그만 보고 "왜 이 계획이 나왔나"를 알 수 있어야 한다.
    resolved = get_runtime_config(
        ([f"--day-type={args.day_type}"] if args.day_type else [])
        + ([f"--target-date={args.target_date}"] if args.target_date else []))
    print(f"요일 구분: {resolved.day_label} ({resolved.day_reason})")
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

    # API 수집을 건너뛰면 재고 스냅샷을 물려받습니다(없으면 여기서 멈춥니다).
    if args.skip_api and inherit_snapshot(args.now or resolved.now) < 0:
        return 2

    failures: list[tuple[Path, int]] = []
    # 단계별 소요를 재 둔다 — "얼마나 걸리나"를 짐작으로 적으면 안내 문구가
    # 곧 거짓말이 된다(웹 사용 안내의 예상 소요가 실제로 그랬다).
    timings: list[tuple[Path, float]] = []
    started_all = time.perf_counter()

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
        started = time.perf_counter()
        completed = subprocess.run(command, cwd=ROOT, env=env)
        elapsed = time.perf_counter() - started
        timings.append((script, elapsed))

        if completed.returncode == 0:
            print(f"완료: {script} ({format_elapsed(elapsed)})")
            continue

        print(f"실패: {script} (exit code={completed.returncode})")
        failures.append((script, completed.returncode))

        # 뒤 단계는 앞 단계 산출물에 의존하므로 기본적으로 즉시 중단합니다.
        if not args.continue_on_error:
            print("파이프라인을 중단합니다.")
            return completed.returncode

    if timings:
        print()
        print("=== 단계별 소요 시간 ===")
        for script, elapsed in timings:
            print(f"  {format_elapsed(elapsed):>10}  {script}")
        total = time.perf_counter() - started_all
        print(f"  {'합계':>9} {format_elapsed(total):>10}"
              f"  ({len(timings)}단계, 시간대 {args.duration or resolved.duration})")

    if failures:
        print("\n실패한 단계:")
        for script, code in failures:
            print(f"- {script}: {code}")
        return 1

    print("\n모든 단계가 완료되었습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

