"""step0~step4 전체 파이프라인 실행기.

각 단계 파일을 프로젝트 루트에서 subprocess로 호출합니다.
공통 설정(--now/--period/--duration/--raw-file/--day-type/--target-date/
--warmup-period/--warmup-days)은 그대로 하위 스크립트에 전달되며, 각 스크립트는
project_config를 통해 이를 읽습니다. 차량 대수·회차 상한·씨앗·시간 예산 강제·실행
종류(--fleet-size/--vehicles-per-round/--seed/--enforce-time-budget/--run-kind)는
예외로 환경변수(PBR_FLEET_SIZE·PBR_VEHICLES_PER_ROUND·PBR_CLUSTER_SEED·
PBR_ENFORCE_TIME_BUDGET·PBR_RUN_KIND)로 전달합니다 — project_config가 import 시점의
상수로 읽기 때문입니다(build_env 참고).

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

# 실행 종류의 정본은 db.py다 — 여기서 목록을 또 적으면 갈린다. 이 파일은 단계를
# subprocess로 띄우기만 하지 DB를 직접 쓰지 않으므로, 필요한 것은 이름 하나뿐이다.
from db import RUN_KINDS


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
        Path("pipeline/step0_collect") / "tashu_api.py",
    ],
    "api": [
        Path("pipeline/step0_collect") / "extract_parking_lot.py",
        Path("pipeline/step0_collect") / "api_to_info.py",
    ],
    "eda": [
        Path("pipeline/step0_eda") / "concat_1year_file.py",
        Path("pipeline/step0_eda") / "EDA.py",
    ],
    "preprocess": [
        Path("pipeline/step0_collect") / "raw_to_net.py",
        Path("pipeline/step0_collect") / "calculate_target_qty.py",
    ],
    "selection": [
        Path("pipeline/step1_cluster") / "top_st_clustering.py",
        Path("pipeline/step1_cluster") / "st_visualization.py",
    ],
    "optimization": [
        Path("pipeline/step2_optimize") / "ilp.py",
        Path("pipeline/step2_optimize") / "vrp.py",
    ],
    "visualization": [
        Path("pipeline/step3_map") / "main.py",
    ],
    "evaluation": [
        Path("pipeline/step4_metrics") / "imbalance.py",
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

    # 이 실행이 운영 계획인지 실험인지는 **띄우는 쪽만 안다.** 같은 파이프라인이
    # 둘 다 만들기 때문이다(실험은 --now에 실험 라벨을 주고 돌린 것뿐이다).
    # 선언하면 DB의 runs.kind에 못박혀 화면이 짐작하지 않는다.
    #
    # ⚠️ **기본값을 plan으로 두지 않는다.** 선언을 잊은 실험이 '계획'으로
    # 확정되면 라벨 짐작(obs-cmp-*를 실험으로 맞힌다)보다 나빠진다. 선언이
    # 없으면 예전처럼 NULL로 두고 짐작에 맡긴다.
    parser.add_argument(
        "--run-kind", choices=RUN_KINDS,
        help="이 실행의 종류. 웹 실행 폼은 언제나 plan으로 띄운다."
             " 실험 격자를 돌릴 때 experiment를 주면 계획 화면에서 갈린다."
             " 주지 않으면 라벨로 짐작한다(옛 동작)",
    )

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
    # 실행 종류도 같은 방식으로 내려보낸다 — db.ensure_run()이 이것을 읽어
    # runs.kind에 못박는다. 선언이 없으면 심지 않는다(짐작에 맡긴다).
    if getattr(args, "run_kind", None):
        env["PBR_RUN_KIND"] = args.run_kind
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


# step3 `module.MAX_CALLS`의 기본값. 파이프라인은 step3를 **한 프로세스**로 띄우므로 이 값이
# 곧 실행 전체의 TMAP 예산이다. 모듈을 import하지 않고 적는다 — step3 폴더를 sys.path에
# 넣어야 해서다. 두 값이 갈리지 않게 시험이 대조한다.
STEP3_DEFAULT_MAX_CALLS = 35


def tmap_notice(env: dict) -> list[str]:
    """경로 지도(step3)가 낄 때 먼저 찍는 안내 — 오늘 도로 수집 상태와 이번 TMAP 예산 (1.26.222).

    `tools/redraw_maps.py`와 **같은 판단**을 쓴다(1.26.219). 예전에는 `--skip-map` 없는
    파이프라인에 이 안내가 없어, 웹 실행 폼(지도를 늘 그린다)으로 계획 한 번이 최대 35건을
    써도 로그에 그날 수집 상태가 안 남았다. 이 PC의 DB만 보므로 다른 PC가 같은 키로
    수집하는지는 모른다고 말한다. **안내일 뿐이라 무엇이 실패해도 파이프라인을 막지 않는다.**
    """
    try:
        from tools.redraw_maps import road_collection_today, road_status_lines

        budget = int(env.get("PBR_TMAP_MAX_CALLS") or STEP3_DEFAULT_MAX_CALLS)
        lines = road_status_lines(road_collection_today(), budget,
                                  adjust="PBR_TMAP_MAX_CALLS로")
    except Exception as err:                              # noqa: BLE001
        return [f"[안내] TMAP 예산 안내를 만들지 못했습니다: {type(err).__name__}: {err}"]
    return ["경로 지도(step3)가 TMAP을 부릅니다 — 군집 하나에 호출 하나이고,"
            " 끝까지 못 부를 회차는 그리지 않고 건너뜁니다.",
            *(f"  {line}" for line in lines)]


# `API_SNAPSHOTS` 세 파일에 대응하는 DB 표. 순서는 뜻이 없고 **셋 다** 있어야 한다
# (반쯤 있는 라벨을 물려받으면 다음 단계에서 멈춘다 — snapshot_labels()와 같은 규약).
SNAPSHOT_TABLES = ("station_stock", "parking_lot", "station_info")


def _snapshot_in_db(label: str) -> bool:
    """그 라벨의 재고 스냅샷이 **DB에 세 표 다** 있는가.

    DB를 못 열면 False다 — 없다고 답하는 쪽이 안전하다(파일 경로로 넘어간다).
    """
    try:
        import db

        with db.session() as conn:
            for table in SNAPSHOT_TABLES:
                if db.load_frame(conn, table, run_label=label).empty:
                    return False
        return True
    except Exception:
        return False


# 합성 자료를 만드는 시험·도구가 쓰는 라벨 앞머리. 그 재고는 **가짜 대여소**
# (`대여소1` …)라, 물려받으면 실제 계획이 합성 재고로 선다 — 회사환경에서
# `snapshot_labels()`의 1순위가 `smoketest-20236`(대여소 70곳)이던 적이 있다
# (2026-09-14, 1.26.188 전 시험이 진짜 `data/`에 쓰던 시절의 잔여물).
# ⚠️ 라벨 짐작(`db.classify_run_label`)으로는 못 가른다 — `smoketest-*`도
# `sweep-21`(실자료 1,376곳)도 똑같이 '실험'으로 짐작된다.
#   smoketest-  tests/test_pipeline.py      daytype-  tests/test_day_type.py
#   rentaltest- tests/test_rentals.py       재현       tools/reproduce.py
#   데모        tools/make_sample_data.py 사용 예시
SYNTHETIC_LABEL_PREFIXES = ("smoketest-", "daytype-", "rentaltest-", "재현", "데모")


def _run_kinds(labels) -> dict:
    """라벨별 실행 종류. `runs.kind`가 있으면 그것, 없으면 라벨로 짐작한다.

    DB를 못 열면 짐작만 쓴다 — 종류는 **순서**를 정할 뿐 후보를 버리지 않으므로,
    모른다고 멈출 이유가 없다.
    """
    import db

    stored = {}
    try:
        with db.session() as conn:
            stored = dict(conn.execute("SELECT run_label, kind FROM runs").fetchall())
    except Exception:
        pass
    return {label: stored.get(label) or db.classify_run_label(label) for label in labels}


def donor_labels(label: str, kind: str | None = None) -> list:
    """`--skip-api`가 재고를 물려받을 후보를 **고를 순서대로** 돌려준다 (1.26.218).

    ① **합성 자료 라벨은 언제나 뺀다**(`SYNTHETIC_LABEL_PREFIXES`).
    ② **계획으로 선언된 실행(`kind="plan"`)이면 계획 실행을 앞에 둔다.** 웹 실행
       폼의 *API 수집 생략*이 그렇다 — 파일 시각만 보면 가장 최근의 격자 스윕이
       운영 계획을 밀어내고 기증자가 된다. 실험은 버리지 않고 **뒤로 보낸다**(계획이
       하나도 없는 PC에서 멈추면 안 된다). 같은 무리 안에서는 최근 순서를 지킨다.

    🔴 **선언이 없거나 실험이면 예전처럼 가장 최근 순서다.** 격자 실험이 그 순서에
    기대고 있다 — `sweep-10`을 수집한 뒤 12·14·16·18·21을 `--skip-api`로 돌려
    *"같은 재고를 물려받는다"*(EXPERIMENTS.md, 1.19.5 차량 상한 스윕의 재현 명령 —
    `--run-kind` 없음). 계획을 늘 앞에 두면 그 사슬이 **조용히 운영 계획의 재고**를
    받는다. 처음에 그렇게 짰다가 그 재현 명령을 읽고 좁혔다.
    """
    candidates = [other for other in snapshot_labels()
                  if other != label and not other.startswith(SYNTHETIC_LABEL_PREFIXES)]
    if kind != "plan":
        return candidates
    kinds = _run_kinds(candidates)
    return ([c for c in candidates if kinds[c] == "plan"]
            + [c for c in candidates if kinds[c] != "plan"])


def inherit_snapshot(label: str, kind: str | None = None) -> int:
    """`--skip-api`로 건너뛴 재고 스냅샷을 **가장 최근 실행에서 물려받는다.**

    API 수집 단계가 만드는 세 파일은 실행 라벨마다 따로다. 그래서 `--skip-api`를
    켠 채 **새 실행 이름**을 쓰면 파일이 없어서 다음 단계(calculate_target_qty)가
    바로 멈췄다 — 웹 폼의 "API 수집 생략"도 같았고, 안내만 보고는 알 수 없었다.

    물려받은 라벨을 반드시 찍는다. 어느 시점 재고로 세운 계획인지가 로그에
    남아야 한다 — 조용히 물려받으면 출처를 알 수 없는 계획이 된다.

    반환: 복사한 파일 수. 이미 다 있으면 0.

    ⚠️ **파일과 DB를 함께 본다 (1.26.167).** 예전에는 파일만 봐서, DB에 스냅샷이
    있어도 CSV가 없으면 *"물려받을 스냅샷이 없습니다"* 로 **단계가 시작되기도 전에**
    멈췄다. 배선이 DB로 옮겨진 뒤로는(1.26.164·166) 그 판정이 사실과 어긋난다.

    🔴 **'가장 최근'이 곧 '물려받을 것'은 아니다 (1.26.218).** 파일 수정 시각
    1순위를 그대로 받았더니 회사환경에서는 시험이 남긴 **합성 재고**(70곳)가
    기증자였다 — 로그에 라벨 한 줄이 찍힐 뿐 결과 표로는 모른다. 고르는 규칙은
    `donor_labels()`에 있고, `kind`(이 실행의 `--run-kind`)가 순서를 바꾼다.
    """
    missing = [p for p in snapshot_paths(label) if not p.exists()]
    if not missing:
        return 0

    if _snapshot_in_db(label):
        # 파일은 없지만 DB에 그 라벨의 스냅샷이 있다 — 각 단계가 DB에서 읽으므로
        # 물려받을 것이 없다. 파일을 만들지 않는 것이 맞다(원본을 늘리지 않는다).
        print(f"[안내] --skip-api: '{label}'의 재고 스냅샷을 DB에서 씁니다 (CSV 없음).")
        return 0

    donors = donor_labels(label, kind)
    if not donors:
        print(f"설정 오류: --skip-api를 켰지만 물려받을 재고 스냅샷이 없습니다.")
        print(f"  '{label}' 라벨의 파일이 없고, 다른 실행의 스냅샷도 없습니다.")
        synthetic = [other for other in snapshot_labels()
                     if other.startswith(SYNTHETIC_LABEL_PREFIXES)]
        if synthetic:
            print(f"  (시험·합성 자료 라벨 {len(synthetic)}개는 가짜 재고라 후보에서 뺐습니다:"
                  f" {', '.join(synthetic[:3])}{' …' if len(synthetic) > 3 else ''})")
        print(f"  --skip-api를 빼고 한 번 수집하세요.")
        return -1

    donor = donors[0]
    donor_kind = _run_kinds([donor])[donor]
    print(f"[안내] --skip-api: '{donor}'의 재고 스냅샷을 물려받습니다"
          f" (요청 라벨 '{label}'에 파일이 없음 · 기증자 종류 {donor_kind}).")
    if kind == "plan" and donor_kind != "plan":
        print(f"  ⚠ 계획 실행 중에는 물려받을 스냅샷이 없어 실험 실행의 재고를 씁니다.")
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

    # 경로 지도가 끼면 TMAP 한도를 쓴다 — dry-run에서도 보여야 부르기 전에 판단한다.
    if not args.skip_map:
        print()
        for line in tmap_notice(env):
            print(line)

    # dry-run은 파일 존재 여부와 실행 순서만 확인할 때 사용합니다.
    if args.dry_run:
        return 0

    # 산출물 폴더가 없어 저장에 실패하는 일을 예방합니다.
    ensure_output_dirs()

    # API 수집을 건너뛰면 재고 스냅샷을 물려받습니다(없으면 여기서 멈춥니다).
    # 실행 종류를 함께 넘긴다 — 계획으로 선언된 실행만 계획의 재고를 먼저 고른다(1.26.218).
    if args.skip_api and inherit_snapshot(args.now or resolved.now,
                                          getattr(args, "run_kind", None)) < 0:
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

