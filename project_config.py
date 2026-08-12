"""Shared runtime configuration for the public-bike rebalancing pipeline."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parent
DATA_ROOT = PROJECT_ROOT / "data"
PP_ROOT = DATA_ROOT / "pp_data"

DEFAULT_NOW = os.getenv("PBR_NOW", "2026-05-21 18")
DEFAULT_PERIOD = os.getenv("PBR_PERIOD", "25년 11월")
DEFAULT_DURATION = os.getenv("PBR_DURATION", "_05_10")
DEFAULT_RAW_FILE = os.getenv(
    "PBR_RAW_FILE",
    "data/raw_data/대전시 공영자전거 타슈 대여이력 정보(25년11월).csv",
)

# ---- 운영 상수 (step2 vrp, step3 지도에서 공유) ----
DEPOT_ID = "ST0001"          # 타슈 관제센터 (이용자 대상 대여소 아님)
DEPOT_NAME = "타슈 관제센터"
DEPOT_LAT = 36.406607
DEPOT_LON = 127.306457
VEHICLE_CAPACITY = 10        # 차량 최대 적재 대수 (대전교통공사 확인값, 버전관리 1.0.1)

# ---- 차량 운용 (docs/FLEET.md) ----
# 보유 차량은 21대지만 한 회차에 전부 투입하지 않는다. 하루 약 3회차를 돌리며
# 회차마다 일부만 나가고 나머지는 다음 회차를 맡는 로테이션 방식이다.
FLEET_SIZE = int(os.getenv("PBR_FLEET_SIZE", "21"))            # 보유 차량 총 대수
VEHICLES_PER_ROUND = int(os.getenv("PBR_VEHICLES_PER_ROUND", "10"))  # 한 회차 투입 대수(상한)
VEHICLE_ID_FORMAT = "V{:02d}"                                   # V01 ~ V21

# 한 회차 작업이 끝나야 하는 시한(분).
# target_qty는 특정 시간 창(예: 05~10시)의 수요를 전제로 계산되므로, 작업이 늦어지면
# 자전거가 '필요했던 시각이 지난 뒤'에 도착한다. 현재는 제약이 아니라 사후 점검 기준이다.
# (docs/FLEET.md, docs/KPI.md)
TIME_BUDGET_MINUTES = float(os.getenv("PBR_TIME_BUDGET_MINUTES", "120"))

# ---- step1 군집 조정 목적함수 가중치 ----
# score = ALPHA·balance² + BETA·size분산 + GAMMA·거리합
#   balance : 군집별 rebal_qty 합 (대 단위)
#   size    : 군집 크기 편차
#   거리합  : 각 대여소 ~ 메도이드 맨해튼 거리 (위경도 '도' 단위, 1도 ≈ 111km)
# 거리 항의 값이 작아(1~2) balance(수백~수만)에 묻히기 쉬우므로 GAMMA를 크게 잡는다.
#
# GAMMA=1000은 실데이터 실험으로 정한 값이다(25년 11월, 3회차). 기존 10에서는
# 거리 항이 balance에 묻혀 군집이 흩어졌고, 그 결과 이동거리가 길어져
# 시간 예산을 넘는 회차가 생겼다. 1000으로 올리자 3회차 모두에서
# 최장 소요 121.0→110.6분, 예산 초과 1건→0건, 총 이동거리 866→814km(-6%)로
# 개선됐다. 대가는 balance 최대 5→6, 처리 대수 684→681로 미미하다.
# 근거: docs/steps/step1_clustering.md의 '거리 가중치' 절
CLUSTER_ALPHA = float(os.getenv("PBR_CLUSTER_ALPHA", "1"))
CLUSTER_BETA = float(os.getenv("PBR_CLUSTER_BETA", "100"))
CLUSTER_GAMMA = float(os.getenv("PBR_CLUSTER_GAMMA", "1000"))


def vehicle_ids(size: int = None) -> list:
    """차량 식별자 목록(V01, V02, ...)."""
    return [VEHICLE_ID_FORMAT.format(i) for i in range(1, (size or FLEET_SIZE) + 1)]


@dataclass(frozen=True)
class RuntimeConfig:
    now: str = DEFAULT_NOW
    period: str = DEFAULT_PERIOD
    duration: str = DEFAULT_DURATION
    raw_file: str = DEFAULT_RAW_FILE

    @property
    def raw_path(self) -> Path:
        return PROJECT_ROOT / self.raw_file


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--now", default=None, help="분석 시점. 예: 2026-05-21 18")
    parser.add_argument("--period", default=None, help="순수요 입력 기간. 예: 25년 11월")
    parser.add_argument("--duration", default=None, help="시간대 구간. 예: _05_10")
    parser.add_argument(
        "--raw-file",
        default=None,
        help="원천 대여 이력 CSV 경로(프로젝트 루트 기준)",
    )
    return parser


def get_runtime_config(argv: Optional[list[str]] = None) -> RuntimeConfig:
    """Read CLI overrides, then environment variables, then project defaults.

    parse_known_args is intentional: scripts can import this module without
    rejecting arguments that belong to another pipeline step.
    """

    args, _ = _parser().parse_known_args(argv)
    return RuntimeConfig(
        now=args.now or DEFAULT_NOW,
        period=args.period or DEFAULT_PERIOD,
        duration=args.duration or DEFAULT_DURATION,
        raw_file=args.raw_file or DEFAULT_RAW_FILE,
    )


def duration_list(config: RuntimeConfig) -> Tuple[str, ...]:
    """Return one or more duration labels.

    --duration accepts comma-separated values for batch execution.
    """

    return tuple(item.strip() for item in config.duration.split(",") if item.strip())


def ensure_output_dirs() -> None:
    """Create the folders used by pipeline stages when they are missing."""

    for relative in (
        "대여소별 재고",
        "대여소별 주차대수",
        "대여소 정보",
        "순수요",
        "재배치 정보",
        "ILP/후보",
        "ILP/visualization",
        "VRP/visualization",
        "성능 지표/visualization",
    ):
        (PP_ROOT / relative).mkdir(parents=True, exist_ok=True)

