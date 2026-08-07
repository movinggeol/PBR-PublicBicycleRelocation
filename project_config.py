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

