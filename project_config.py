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

# ---- 요일 구분 (평일/휴일) ----
# **휴일 = 주말 ∪ 공휴일**이다. 평일과 휴일은 수요 구조가 다르므로 한 통계로
# 섞지 않는다. 실측(12개월): `_10_15`·`_15_20`에서 대여소의 33~37%가 두 구분에서
# **부호가 반대**였다(평일엔 채워야 할 곳이 휴일엔 빼 와야 할 곳). 섞어서 평균 내면
# 서로 상쇄돼 작업 대상에서 빠진다. 근거: experiments/weekend_profile.py
#
# 그래서 시간대(duration)와 같은 급의 실행 설정으로 둔다 — 한 번의 실행은
# 평일 계획이거나 휴일 계획이지, 둘을 합친 무언가가 아니다.
# 'all'을 두지 않은 것도 같은 이유다(섞는 선택지를 아예 만들지 않는다).
DAY_TYPES = ("weekday", "holiday")
DAY_TYPE_LABELS = {"weekday": "평일", "holiday": "휴일"}

# 'auto'는 계획 대상일(--target-date, 기본 오늘)을 달력으로 판정한다.
# 실행 시점에 따라 알아서 갈리므로 운영에서는 이쪽이 기본이다.
DAY_TYPE_AUTO = "auto"
DEFAULT_DAY_TYPE = os.getenv("PBR_DAY_TYPE", DAY_TYPE_AUTO)

_holiday_cache: dict = {}


def korean_holidays(*years):
    """대한민국 공휴일 달력(holidays 패키지, public 카테고리).

    대체공휴일·임시공휴일까지 포함한다. 달력을 손으로 관리하지 않으려고 패키지를
    쓴다 — 규칙이 해마다 바뀐다(2026년만 해도 22일이고 제헌절이 되살아났다).

    **연도를 반드시 명시해서 만든다.** 연도 없이 만들면 holidays가 조회 시점에
    지연 확장하는데, 그 경로에서 내부 연도가 실수로 넘어가 TypeError가 난다.
    연도별로 한 번만 만들어 캐시한다.
    """
    import holidays

    wanted = tuple(sorted({int(y) for y in years}))
    if wanted not in _holiday_cache:
        _holiday_cache[wanted] = holidays.SouthKorea(years=list(wanted))
    return _holiday_cache[wanted]


def is_public_holiday(date) -> bool:
    """법정공휴일인가(주말은 포함하지 않는다)."""
    import pandas as pd

    stamp = pd.Timestamp(date)
    return stamp.date() in korean_holidays(stamp.year)


def is_holiday(date) -> bool:
    """**휴일인가 = 주말이거나 공휴일인가.**"""
    import pandas as pd

    stamp = pd.Timestamp(date)
    return stamp.dayofweek >= 5 or stamp.date() in korean_holidays(stamp.year)


def target_stamp(target_date=None):
    """계획 대상일을 Timestamp로. 비어 있으면 오늘.

    빈 문자열을 그대로 pd.Timestamp에 넘기면 NaT가 되어 연도가 NaN이 된다.
    여기서 한 번에 걸러 둔다.
    """
    import pandas as pd

    if not target_date:
        return pd.Timestamp.today().normalize()
    stamp = pd.Timestamp(target_date)
    if pd.isna(stamp):
        raise ValueError(f"계획 대상일을 읽을 수 없습니다: {target_date!r} (YYYY-MM-DD)")
    return stamp


def resolve_day_type(target_date=None) -> str:
    """계획 대상일이 평일인지 휴일인지 판정한다(기본: 오늘)."""
    return "holiday" if is_holiday(target_stamp(target_date)) else "weekday"


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

# 차량 이동 속도(km/h). **ILP와 VRP가 반드시 같은 값을 써야 한다** —
# 두 단계가 다른 속도로 계산하면 ILP가 최소 비용이라고 고른 조합이 VRP에서는
# 최소가 아니게 된다. 1.13.2에서 25로 통일(VRP가 30이었음).
# 도심 주행·정차를 감안한 보수적 값이며, 현장 실측이 나오면 이 상수만 바꾸면 된다.
VEHICLE_SPEED_KMPH = float(os.getenv("PBR_VEHICLE_SPEED_KMPH", "25"))

# ---- 차량 운용 (docs/FLEET.md) ----
# 보유 차량은 21대지만 한 회차에 전부 투입하지 않는다. 하루 약 3회차를 돌리며
# 회차마다 일부만 나가고 나머지는 다음 회차를 맡는 로테이션 방식이다.
# 두 대수 모두 웹 실행 폼에서 바꿀 수 있다
# (--fleet-size → PBR_FLEET_SIZE, --vehicles-per-round → PBR_VEHICLES_PER_ROUND).
MAX_FLEET_SIZE = 99                # VEHICLE_ID_FORMAT이 두 자리 고정이라 V99가 상한
DEFAULT_FLEET_SIZE = 21            # 보유 차량 총 대수 기본값 (웹 폼 기본값도 이 값)
DEFAULT_VEHICLES_PER_ROUND = 10    # 한 회차 투입 대수(상한) 기본값


def normalize_vehicle_count(value, label: str = "차량 대수") -> int:
    """차량 대수를 1~MAX_FLEET_SIZE 범위의 정수로 정규화한다.

    범위를 벗어나거나 숫자가 아니면 ValueError. 웹 폼·CLI·환경변수가
    같은 규칙으로 검증하고 같은 문구를 보여주도록 한 곳에 모아 둔다.
    """
    message = f"{label}는 1~{MAX_FLEET_SIZE} 사이의 정수여야 합니다 (입력: {value})."
    try:
        count = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(message) from None
    if not 1 <= count <= MAX_FLEET_SIZE:
        raise ValueError(message)
    return count


def normalize_fleet_size(value) -> int:
    """보유 차량 대수."""
    return normalize_vehicle_count(value, "차량 대수")


def normalize_per_round(value) -> int:
    """한 회차 투입 대수(상한). 보유 대수와의 비교는 호출부에서 한다."""
    return normalize_vehicle_count(value, "회차당 투입 대수")


FLEET_SIZE = normalize_fleet_size(os.getenv("PBR_FLEET_SIZE", DEFAULT_FLEET_SIZE))
# 보유 대수보다 많이 투입할 수는 없다. 대수를 10대 미만으로 줄이면 회차 투입
# 상한도 함께 내려간다(안 그러면 step1이 만든 클러스터에 배정할 차가 모자라 step2가 죽는다).
VEHICLES_PER_ROUND = min(
    normalize_per_round(os.getenv("PBR_VEHICLES_PER_ROUND", DEFAULT_VEHICLES_PER_ROUND)),
    FLEET_SIZE,
)
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
# GAMMA는 두 번의 실데이터 실험으로 정했다(둘 다 25년 11월, 3회차 전부 확인).
#   10 → 1000  : 거리 항이 balance에 묻혀 군집이 흩어졌다. 올리자 최장 소요
#                121.0→110.6분, 예산 초과 1건→0건, 총 이동거리 866→814km.
#   1000 → 3000: z를 1.99로 올려 작업량이 늘자 _15_20이 137분으로 예산을 넘었다.
#                3000에서 최장 소요(전 회차) 137.0→115.8분, 예산 초과 1건→0건,
#                총 이동거리 870→854km. 대가는 처리 대수 756→748(-1%).
# 주의: γ=2000은 γ=1000보다 나빴다(160.9분). 군집 조정이 탐욕적 국소 탐색이라
# 목적함수 지형이 γ에 대해 매끄럽지 않다 — 중간값을 보간해 추정하면 안 된다.
# 근거: docs/EXPERIMENTS.md 4장, docs/steps/step1_clustering.md의 '거리 가중치' 절
CLUSTER_ALPHA = float(os.getenv("PBR_CLUSTER_ALPHA", "1"))
CLUSTER_BETA = float(os.getenv("PBR_CLUSTER_BETA", "100"))
CLUSTER_GAMMA = float(os.getenv("PBR_CLUSTER_GAMMA", "3000"))

# ---- step0 목표 재고 안전계수 ----
# target_qty = mu + z·sigma 의 z. 값이 클수록 수요가 몰리는 날까지 덮지만
# 그만큼 채워야 할 대수가 늘어난다.
#
# 1.65는 "정규분포에서 95%"라는 이유로 쓰였으나, 12개월 백테스트에서 실제 커버리지가
# 91.7~92.8%에 그쳤다. 순수요 분포의 꼬리가 정규분포보다 두껍기 때문이다.
# z=1.99로 올리면 평균 94.9%가 되고, 대가는 처리 상한 +30%다. 다만 회차마다 대가가
# 다르다 — pick 가능량이 이미 병목인 _05_10은 작업량이 늘지 않는다(-1.8%).
# 근거·재현: docs/EXPERIMENTS.md 1장, python experiments/z_sweep.py
TARGET_Z = float(os.getenv("PBR_TARGET_Z", "1.99"))


def vehicle_ids(size: int = None) -> list:
    """차량 식별자 목록(V01, V02, ...)."""
    return [VEHICLE_ID_FORMAT.format(i) for i in range(1, (size or FLEET_SIZE) + 1)]


@dataclass(frozen=True)
class RuntimeConfig:
    now: str = DEFAULT_NOW
    period: str = DEFAULT_PERIOD
    duration: str = DEFAULT_DURATION
    raw_file: str = DEFAULT_RAW_FILE
    # day_type은 항상 해석된 값(weekday|holiday)이다 — 'auto'는 여기까지 오지 않는다.
    day_type: str = "weekday"
    target_date: str = ""       # 계획 대상일(YYYY-MM-DD). 빈 값이면 오늘

    @property
    def raw_path(self) -> Path:
        return PROJECT_ROOT / self.raw_file

    @property
    def day_label(self) -> str:
        """출력 메시지용 한국어 표기(평일/휴일)."""
        return DAY_TYPE_LABELS.get(self.day_type, self.day_type)

    @property
    def day_reason(self) -> str:
        """왜 이 요일 구분인지 한 줄로 — 로그에 남겨 나중에 재현할 수 있게 한다."""
        stamp = target_stamp(self.target_date)
        if is_public_holiday(stamp):
            name = korean_holidays(stamp.year).get(stamp.date())
            return f"{stamp:%Y-%m-%d} {name} → 휴일"
        weekday_name = "월화수목금토일"[stamp.dayofweek]
        return f"{stamp:%Y-%m-%d}({weekday_name}) → {self.day_label}"


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
    parser.add_argument(
        "--day-type",
        default=None,
        choices=(*DAY_TYPES, DAY_TYPE_AUTO),
        help=f"요일 구분. {' | '.join(DAY_TYPES)} | {DAY_TYPE_AUTO}"
             f" (기본 {DEFAULT_DAY_TYPE}). auto는 --target-date를 달력으로 판정한다",
    )
    parser.add_argument(
        "--target-date",
        default=None,
        help="계획 대상일(YYYY-MM-DD, 기본 오늘). --day-type auto의 판정 기준",
    )
    return parser


def get_runtime_config(argv: Optional[list[str]] = None) -> RuntimeConfig:
    """Read CLI overrides, then environment variables, then project defaults.

    parse_known_args is intentional: scripts can import this module without
    rejecting arguments that belong to another pipeline step.
    """

    args, _ = _parser().parse_known_args(argv)
    target_date = args.target_date or os.getenv("PBR_TARGET_DATE", "")
    return RuntimeConfig(
        now=args.now or DEFAULT_NOW,
        period=args.period or DEFAULT_PERIOD,
        duration=args.duration or DEFAULT_DURATION,
        raw_file=args.raw_file or DEFAULT_RAW_FILE,
        day_type=normalize_day_type(args.day_type or DEFAULT_DAY_TYPE, target_date),
        target_date=target_date,
    )


def normalize_day_type(value, target_date=None) -> str:
    """요일 구분 값을 검증하고 'auto'를 실제 값으로 풀어 준다.

    반환값은 항상 weekday 또는 holiday다 — 하위 코드가 'auto'를 볼 일이 없다.
    """
    day_type = str(value).strip().lower()
    if day_type == DAY_TYPE_AUTO:
        return resolve_day_type(target_date)
    if day_type not in DAY_TYPES:
        raise ValueError(
            f"요일 구분은 {' 또는 '.join(DAY_TYPES)}"
            f"(또는 {DAY_TYPE_AUTO}) 여야 합니다 (입력: {value}).")
    return day_type


def holiday_mask(dates):
    """날짜 시리즈 → 휴일이면 True (주말 ∪ 공휴일).

    한 건씩 달력을 조회하면 느리므로, 데이터에 있는 연도만 펼쳐 집합으로 만든 뒤
    벡터 연산으로 판정한다.
    """
    import pandas as pd

    stamps = pd.to_datetime(dates)
    years = {int(y) for y in stamps.dt.year.dropna().unique()}
    public = set(korean_holidays(*years)) if years else set()
    return (stamps.dt.dayofweek >= 5) | stamps.dt.date.isin(public)


def select_day_type(frame, date_column: str, day_type: str):
    """날짜 컬럼을 보고 평일 또는 휴일 행만 남긴다.

    `duration_list()`와 같은 급의 헬퍼다 — 어느 단계에서 걸러도 규칙이 같아야
    하므로 한 곳에 둔다. **휴일 = 주말 ∪ 공휴일**이며, 공휴일은 holidays 패키지를
    따른다(대체공휴일·임시공휴일 포함).
    """
    mask = holiday_mask(frame[date_column])
    return frame[mask if normalize_day_type(day_type) == "holiday" else ~mask]


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

