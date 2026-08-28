"""Shared runtime configuration for the public-bike rebalancing pipeline."""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


def _force_utf8_output() -> None:
    """출력 인코딩을 UTF-8로 못 박는다.

    윈도우 파이썬은 stdout이 콘솔이 아니라 **파이프·파일이면** 로캘 코드페이지
    (한국어 윈도우에서 cp949)로 인코딩한다. 그러면 안내 문구의 '—' 한 글자에
    스크립트가 UnicodeEncodeError로 죽는다 — `python tools/backtest_demand.py > log`
    처럼 로그를 남기려는 순간 터진다(1.19.0에서 파이프라인이, 1.19.3에서 도구가
    실제로 그렇게 멈췄다).

    **모든 step·tool이 project_config를 거치므로 여기서 한 번에 막는다.**
    웹(webapp/jobs.py)과 run_pipeline은 환경변수로도 같은 방어를 한다.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream and getattr(stream, "encoding", "").lower() not in ("utf-8", "utf8"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:      # 리다이렉트된 특수 스트림 등 — 막지 못해도 죽지는 않는다
            pass


_force_utf8_output()

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_ROOT = PROJECT_ROOT / "data"
PP_ROOT = DATA_ROOT / "pp_data"

DEFAULT_NOW = os.getenv("PBR_NOW", "2026-05-21 18")

# ---- 시간대 (duration) ----
# 하루를 5시간 창 넷으로 자른다. **맨 앞 밑줄까지가 값**이다(`_10_15`) — 예시를
# `10_15`로 적으면 그대로 입력한 사용자가 step4 duration_hours()에서 크래시를 본다.
# 창마다 수요 방향이 반대라 섞어서 평균 내지 않는다(docs/분석/KPI.md).
# `_20_05`는 자정을 넘긴다 — 시간 목록을 만드는 곳은 duration_hours() 하나다.
DURATIONS = ("_05_10", "_10_15", "_15_20", "_20_05")
DURATION_LABELS = {
    "_05_10": "05~10시 (출근)",
    "_10_15": "10~15시 (낮)",
    "_15_20": "15~20시 (퇴근)",
    "_20_05": "20~05시 (야간)",
}
DEFAULT_DURATION = os.getenv("PBR_DURATION", "_05_10")

# ---- 순수요 기간 ----
# 파일명·DB에 쓰는 표기는 '25년 11월'이다(period_label 참고).
# 기본값은 **보유한 순수요 중 가장 최근 달**이다 — 달이 바뀔 때마다 사람이 고쳐
# 넣게 하면 곧 낡은 달로 계획을 세우게 된다. 하나도 없는 새 저장소에서는
# 아래 대비값을 쓴다(폼에 빈칸을 보여 주는 것보다 낫다).
FALLBACK_PERIOD = "25년 11월"
NET_DEMAND_DIR = PP_ROOT / "순수요"
_PERIOD_FILE_RE = re.compile(r"^st_net_daily \((\d{2})년 (\d{2})월\)\.csv$")


def available_periods() -> Tuple[str, ...]:
    """순수요를 이미 계산해 둔 기간 목록(오래된 순). 없으면 빈 튜플.

    **CSV를 본다.** DB에도 같은 내용이 있지만 CSV가 아직 정본이고(docs/구현/DB_PLAN.md),
    project_config가 db를 import하면 순환이 된다.
    """
    found = []
    try:
        entries = list(NET_DEMAND_DIR.iterdir())
    except OSError:
        return ()
    for path in entries:
        if (m := _PERIOD_FILE_RE.match(path.name)):
            found.append((int(m.group(1)), int(m.group(2)), f"{m.group(1)}년 {m.group(2)}월"))
    return tuple(label for _, _, label in sorted(found))


def latest_period(fallback: str = FALLBACK_PERIOD) -> str:
    """보유한 순수요 중 가장 최근 달. 하나도 없으면 대비값."""
    periods = available_periods()
    return periods[-1] if periods else fallback


# import 시점에 한 번 정한다 — CLI 기본값이라 상수여야 한다.
# **웹 폼은 요청마다 latest_period()/available_periods()를 다시 부른다**;
# 서버를 띄워 둔 채 새 달을 계산해도 선택지에 바로 나와야 하기 때문이다.
DEFAULT_PERIOD = os.getenv("PBR_PERIOD") or latest_period()

# ---- 요일 구분 (평일/휴일) ----
# **휴일 = 주말 ∪ 공휴일**이다. 평일과 휴일은 수요 구조가 다르므로 한 통계로
# 섞지 않는다. 실측(12개월): `_10_15`·`_15_20`에서 대여소의 33~37%가 두 구분에서
# **부호가 반대**였다(평일엔 채워야 할 곳이 휴일엔 빼 와야 할 곳). 섞어서 평균 내면
# 서로 상쇄돼 작업 대상에서 빠진다. 근거: experiments/structure/weekend_profile.py
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
    try:
        import holidays
    except ImportError as err:
        # 의존성이 빠진 환경(대개 .venv가 아닌 시스템 python)에서 실행한 경우.
        # 파묻힌 ImportError 대신 무엇을 하면 되는지 알려 준다.
        raise ModuleNotFoundError(
            "공휴일 달력을 쓰려면 holidays 패키지가 필요합니다.\n"
            r"  가상환경으로 실행:  .\.venv\Scripts\python.exe -m webapp" "\n"
            "  또는 지금 환경에 설치:  python -m pip install -r requirements.txt"
        ) from err

    wanted = tuple(sorted({int(y) for y in years}))
    if not wanted:
        # 연도 없이 부르면 빈 달력이 나와 '공휴일이 하나도 없다'가 된다.
        # 조용히 틀리느니 여기서 막는다.
        raise ValueError("korean_holidays()는 연도를 하나 이상 받아야 합니다.")
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


def period_label(date) -> str:
    """날짜 → 기간 라벨('26년 03월'). period·warmup_period의 표기를 한 곳에서 만든다.

    db.month_label()이 이 함수를 쓴다 — 같은 규칙이 두 곳에 있으면 어긋난다.
    """
    stamp = target_stamp(date)
    return f"{stamp.year % 100:02d}년 {stamp.month:02d}월"


# ---- 계절 수준 보정 (warmup) ----
# 계절이 바뀌는 달에는 지난달 통계가 못 따라간다(2월→3월 수요 1.5배).
# 계획 대상 달의 **첫 N일 실적**으로 도시 전체 배율 하나를 구해 mu·sigma에 곱한다.
# 배율을 대여소별로 추정하지 않는 이유: 며칠치로 나누면 잡음만 커지고, 계절 효과는
# 도시 전체에 같은 방향으로 오기 때문이다. 근거: docs/분석/EXPERIMENTS.md 3장.
# 0이면 끈다.
DEFAULT_WARMUP_DAYS = int(os.getenv("PBR_WARMUP_DAYS", "14"))

DEFAULT_RAW_FILE = os.getenv(
    "PBR_RAW_FILE",
    "data/raw_data/대전시 공영자전거 타슈 대여이력 정보(25년11월).csv",
)

# 날씨 원천(기상자료개방포털 ASOS 시간자료, 대전 지점 133).
# **파일 하나여도 되고 디렉터리여도 된다** — 포털은 해가 바뀌면 파일을 나눠 주므로
# 기본값은 디렉터리이고, weather.load_hourly()가 그 안의 CSV를 모두 이어 붙인다.
# 읽는 곳은 루트 weather.py 하나다(받는 방법·측정 결과는 docs/분석/WEATHER.md).
DEFAULT_WEATHER_FILE = os.getenv(
    "PBR_WEATHER_FILE",
    "data/raw_data/날씨",
)

# Pick 쪽 결품 증가를 경고할 문턱 (Drop 쪽 이득에 대한 몫).
#
# **Pick 대여소가 조금 나빠지는 것은 설계상 정상이다.** 재고를 빼내는 곳이니
# 당연하고, 특히 `mu`가 크게 음수인 대여소는 `target_qty`가 0으로 잘려 거의 다
# 실어 간다 — 자전거가 다시 흘러드니 맞는 판단이지만, 그 대여소가 유난히 붐빈
# 하루에 결품이 한두 시간 생긴다(z가 허용한 꼬리).
#
# 그래서 '양수면 경고'는 **늘 뜨는 거짓 경보**였다. 실측(26년 03월 `_05_10`):
# Pick +1h vs Drop -2915h = 0.03%. 그 경고 때문에 target_qty를 의심하고 조사했는데
# 아무 문제가 없었다(1.19.6). Drop 이득의 5%를 넘을 때만 알린다.
PICK_HARM_WARN_SHARE = float(os.getenv("PBR_PICK_HARM_WARN_SHARE", "0.05"))

# ---- 지도 배경 타일 ----
# **세 지도(군집·경로·재고)가 반드시 같은 값을 써야 한다.** 예전에는 스크립트마다
# 따로 적어 두어 같은 실행의 산출물끼리 배경이 달랐다(사용자 지적, 수정안 2번).
#
# 기본값은 folium 기본값과 같은 **OpenStreetMap**이다. 한동안 CartoDB positron을
# 썼는데, 그건 경고가 떠서 피한 것이었다. folium 0.20에서 다시 재 보니
# **경고가 없고**(옛 버전은 `{s}.tile.openstreetmap.org` 서브도메인 URL을 써서
# OSM 정책 경고를 받았다) 지금은 `tile.openstreetmap.org` 한 호스트를 쓴다.
# 타일도 정상(200)이다.
MAP_TILES = os.getenv("PBR_MAP_TILES", "OpenStreetMap")

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

# ── 실도로 이동시간 모형 (1.26.7, docs/분석/EXPERIMENTS.md 5-D·5-F장) ──
#
# 직선거리 ÷ 25 km/h는 **실도로의 76%밖에 안 된다**(TMAP 실측 1.32배).
# 그런데 배율은 상수가 아니다 — 짧은 구간일수록 크다.
#
#   0~0.5km  유효 5.6 km/h        (신호·회전이 시간을 지배한다)
#   5~10km   유효 22.0 km/h       (도로 속도가 지배한다)
#
# 그래서 **고정비 + 거리비례**로 잡는다. 물리적으로도 이 편이 맞다.
#
#   이동시간(초) = ROAD_FIXED_SEC + 직선km x 3600 / ROAD_SPEED_KMPH
#
# 실측(구간 239개, 5겹 교차검증): 표본 밖 MAE 237.9초 → **138.7초(-42%)**.
# 계수는 겹마다 272~282초·27.0~28.1 km/h로 **매우 안정적**이다.
#
# ⚠️ **기본은 꺼져 있다(USE_ROAD_MODEL=False).** 켜면 문서의 모든 수치
# (대조군 비교·z·γ 실험)가 그 위에서 나온 값과 달라진다. 재현성을 잃는 대가가
# 크고, 근거가 아직 **하루치 한 번**이다. 여러 날 쌓인 뒤에 기본값을 정한다.
# 켜려면 `PBR_USE_ROAD_MODEL=1`.
USE_ROAD_MODEL = os.getenv("PBR_USE_ROAD_MODEL", "").strip().lower() in (
    "1", "true", "yes", "on")
ROAD_FIXED_SEC = float(os.getenv("PBR_ROAD_FIXED_SEC", "275"))
ROAD_SPEED_KMPH = float(os.getenv("PBR_ROAD_SPEED_KMPH", "27.3"))


def travel_seconds(km: float, speed_kmph: float = None) -> float:
    """직선거리(km) → 이동시간(초). **모든 단계가 이 함수 하나를 쓴다.**

    ILP와 VRP가 서로 다른 식을 쓰면 ILP가 고른 조합이 VRP에서는 최소가 아니게
    된다 — 1.13.2 이전에 속도가 25/30으로 갈려 실제로 겪었다.

    `USE_ROAD_MODEL`이 꺼져 있으면 예전 그대로 `km / speed * 3600`이다.
    """
    if speed_kmph is None:
        speed_kmph = VEHICLE_SPEED_KMPH
    if USE_ROAD_MODEL:
        return ROAD_FIXED_SEC + km * 3600.0 / ROAD_SPEED_KMPH
    return km / speed_kmph * 3600.0


# 자전거 1대를 싣고/내리는 데 걸리는 시간(초). VRP의 작업시간 계산에 쓴다.
# ⚠️ **현장 확인이 안 된 가정값이다** (docs/기록/TODO.md 2-1). 실측이 나오면 여기만 바꾼다.
# 소요시간의 20~30%가 이 값에서 나오므로 시간 예산 판정에 직접 영향을 준다.
PICK_TIME_SEC = float(os.getenv("PBR_PICK_TIME_SEC", "30"))
DROP_TIME_SEC = float(os.getenv("PBR_DROP_TIME_SEC", "30"))

# ---- 차량 운용 (docs/구현/FLEET.md) ----
# 하루 약 3회차를 돌리며, 회차마다 나가는 대수는 **그 회차의 작업량이 정한다**
# (step1의 wanted_vehicles). 두 대수 모두 웹 실행 폼에서 바꿀 수 있다
# (--fleet-size → PBR_FLEET_SIZE, --vehicles-per-round → PBR_VEHICLES_PER_ROUND).
MAX_FLEET_SIZE = 99                # VEHICLE_ID_FORMAT이 두 자리 고정이라 V99가 상한
# 보유 차량 총 대수 기본값 (웹 폼 기본값도 이 값).
# **현장 실측이 아니라 본 연구의 설계값이다** — 회차당 실제 소요가 12~16대이고,
# 그 위에 로테이션 여유를 둔 것이다(근거·실측표는 docs/구현/FLEET.md).
# 현장 대수가 확인되면 이 값만 바꾸면 된다. `--fleet-size`로도 바꾼다.
DEFAULT_FLEET_SIZE = 21

# 한 회차 투입 대수의 **상한**. 실제 대수가 아니다 — 작업량 추정이 이 아래에서
# 정한다. 1.19.1에서 10 → 보유 대수로 열었다(사용자 결정, 2026-08-24).
#
# 근거: 같은 재고로 상한만 바꿔 재 봤더니 (26년 03월 평일, 3회차, 복귀 포함)
#   10대 → 120분 예산 초과 18/30(60%), 총 1,283 km
#   12대 → 9/36(25%),                  총 1,297 km
#   15~18대 → **1/49(2%)**,            총 1,417 km
# 차를 늘려도 총 이동거리는 10%만 늘었다 — 군집이 작아져 안에서 도는 거리가
# 줄고 depot 왕복만 늘기 때문이다. 반면 예산 초과는 60% → 2%로 떨어졌다.
# 대가는 출동 횟수다(3회차면 차량당 2.3회). 현장 인력이 모자라면 이 값을 줄여라 —
# 줄이면 추정이 상한에 걸리고, step1이 몇 대가 모자란지 경고한다.
DEFAULT_VEHICLES_PER_ROUND = DEFAULT_FLEET_SIZE


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
# (docs/구현/FLEET.md, docs/분석/KPI.md)
TIME_BUDGET_MINUTES = float(os.getenv("PBR_TIME_BUDGET_MINUTES", "120"))

# 시간 예산을 **제약으로 걸 것인가**. 켜면 VRP가 예산을 넘기는 작업 앞에서 멈추고
# depot으로 돌아온다(남은 작업은 미집행으로 남는다).
#
# 실측(sweep-21, 3회차): 초과 4건 → **0건**, 최장 196분 → 117분.
# 대가는 미집행 25대(계획 793대의 3.2%)이고 **결품은 대여소·일 평균 +0.011h(40초)**.
# `_10_15`·`_15_20`은 미집행 0대로 순서만 바뀌어 시간이 줄었다.
# 근거·재현: experiments/baseline/budget_enforce.py
#
# ⚠️ **기본은 꺼 둔다.** 계획의 성격이 바뀌는 변경이라(못 옮기는 대수가 생긴다)
# 현장 확인 뒤에 기본값을 정한다. 켜려면 `--enforce-time-budget` 또는
# `PBR_ENFORCE_TIME_BUDGET=1`.
ENFORCE_TIME_BUDGET = os.getenv("PBR_ENFORCE_TIME_BUDGET", "").strip().lower() in (
    "1", "true", "yes", "on")

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
#
# 2026-08-27 재실험(4개월 x 3회차 x 씨앗 2개 = 24개 조합)에서 **3000을 유지**하기로
# 했다. γ는 재배치 물량과 시간 예산을 **동시에** 지배한다:
#   γ=1000  : 결품 -24%(24/24 조합에서 최소)이나 **예산 초과 0건인 조합이 0개**
#   γ=12000 : 예산 준수는 오르나 결품 +74%(후보의 47%를 손도 못 댄다)
# 두 목표를 동시에 개선하는 γ는 없고, 맞교환(swap) 연산을 넣어도 γ>=1000에서는
# 채택되지 않아 중간 지대를 알고리즘으로 만들 수 없다.
# **γ 선택은 파라미터 조정이 아니라 운영 정책의 선택이다.**
# 재현: python experiments/params/gamma_sweep.py
# 근거: docs/분석/EXPERIMENTS.md 4장, docs/구현/steps/step1_clustering.md의 '거리 가중치' 절
CLUSTER_ALPHA = float(os.getenv("PBR_CLUSTER_ALPHA", "1"))
CLUSTER_BETA = float(os.getenv("PBR_CLUSTER_BETA", "100"))
CLUSTER_GAMMA = float(os.getenv("PBR_CLUSTER_GAMMA", "3000"))

# ---- step1 작업 대상 선정·군집 조정 ----
# 1.18.8까지 step1 코드에 숫자로 박혀 있던 값들이다. 다른 운영 상수와 달리
# 환경변수로 바꿀 수 없었고, 논문 3장의 기호표에도 근거 없이 등장했다.
# **셋 다 실험으로 정한 값이 아니라 관행값이다** — 바꾸려면 z·γ처럼 재실험할 것.

# 재배치 대상으로 볼 최소 작업량. |rebal_qty|가 이 값 이하면 손대지 않는다.
# 1~2대를 옮기러 차를 보내는 것은 이동 비용이 편익을 넘는다는 판단.
#
# ⚠️ **실측(1.21.7): 이 값은 사실상 작동하지 않는다.** 문턱 2에서 자격을 갖춘
# 대여소가 pick 76~89곳·drop 208~298곳인데, 아래 TOP_STATION_LIMIT(각 50곳)이
# 먼저 자른다. 그래서 문턱을 1~4로 바꿔도 후보·작업량·결품이 **전부 같다**.
# 문턱이 실제로 후보를 고르기 시작하는 것은 5부터다.
# 재현: experiments/params/min_qty_sweep.py
REBAL_MIN_QTY = int(os.getenv("PBR_REBAL_MIN_QTY", "2"))

# Pick·Drop 각각 상위 몇 곳까지 볼 것인가(작업량 내림차순).
# 이 컷 뒤에 다시 '적은 쪽까지만' 누적합으로 자르므로 실제 대상은 더 적다.
#
# **계획 규모를 실제로 정하는 것은 이 값이다**(위 REBAL_MIN_QTY가 아니라).
#
# 실측(1.21.8, sweep-21 3회차): 결품은 상한이 클수록 **단조롭게** 좋아진다
# (50 → 100에서 3.4~5.2% 감소). 그런데 **집행할 차가 모자란다.**
#
#   상한  50 : 필요 차량 17~18대 → 보유 21대 안에 들어온다      ✅
#   상한  70 : 필요 24.7대 → 21대로 잘림, 차량당 대여소 5.1→6.4곳
#   상한 100 : 필요 31.5대 → 21대로 잘림, 차량당 8.3곳          ← 예산 초과 조건
#
# 즉 **50은 우연이 아니라 보유 차량 21대와 맞물린 값**이다. 근거가 문서에 없었을 뿐,
# 여기서 필요 차량이 보유량 안에 딱 들어온다. 넓히면 계획만 커지고 집행이 안 된다.
# **결품을 더 줄이려면 상한이 아니라 차량이 병목이다** — 파라미터가 아니라 운영 결정.
# 재현: experiments/params/top_limit_sweep.py
TOP_STATION_LIMIT = int(os.getenv("PBR_TOP_STATION_LIMIT", "50"))

# ---- 목표 재고 상한 (거치대 대비 배수) ----
# 목표 재고는 거치대 수를 그대로 상한으로 쓰지 않고 **거치대 x 1.5**까지 허용한다.
# 자전거는 거치대 밖에도 세울 수 있고, 거치대가 적은 대여소(5대)에서 상한을 거치대에
# 딱 맞추면 수요를 아예 못 담기 때문이다.
# ⚠️ **현장 확인이 안 된 값이다.** 얼마까지 세워도 되는지는 운영 규칙에 달렸다.
# 웹의 실시간 재고 대조도 이 값으로 "더 내려놓을 수 있는가"를 판정한다 —
# 계획과 집행이 다른 기준을 쓰면 현장에서 어긋난다.
TARGET_QTY_UPPER_RATIO = float(os.getenv("PBR_TARGET_QTY_UPPER_RATIO", "1.5"))

# ---- 회차당 필요 차량 추정 (1.19.1) ----
# 군집 1개 = 차량 1대이므로, **군집 수는 이번 회차의 작업량이 정한다.**
# 한 대가 감당할 수 있는 양을 시간으로 따진다:
#
#   추정 총 소요 = 처리 대수 x (싣기 + 내리기)        <- 정확히 계산된다
#                + 대여소 수 x TRAVEL_MIN_PER_STATION  <- 실측 계수
#   필요 대수    = ceil(추정 총 소요 x 불균형 여유 / 시간 예산)
#
# 1.19.1 이전에는 `ceil(대상 수 / 7)`이었다. 7이라는 값에 근거가 없었고,
# 실데이터에서는 늘 회차당 투입 상한(10)에 걸려 **사실상 10대 고정**이었다.
#
# **이동 계수는 실측이다** (26년 03월 평일·복귀 포함, 같은 재고로 K를 바꿔 3회):
#   이동분/곳 = 10.2 ~ 15.7, 평균 12.5. **K를 바꿔도 거의 변하지 않았다** —
#   군집을 쪼개면 depot 왕복이 늘지만 군집 안 이동이 그만큼 줄어 상쇄된다
#   (총 소요 1252분@K=10 -> 1278분@K=12 -> 1426분@K=18).
TRAVEL_MIN_PER_STATION = float(os.getenv("PBR_TRAVEL_MIN_PER_STATION", "12.5"))

# 시간 예산은 평균이 아니라 **가장 오래 걸린 차량**으로 판정한다. 같은 실측에서
# 최장/평균이 1.28 ~ 1.51이었다. 평균만 맞추면 절반이 예산을 넘는다.
CLUSTER_IMBALANCE_ALLOWANCE = float(os.getenv("PBR_CLUSTER_IMBALANCE", "1.4"))

# 군집 조정(greedy) 반복 상한과 종료·재조정 기준.
#   ADJUST_MAX_ITER        : 대여소 이동 시도 횟수 상한
#   ADJUST_BALANCE_OK      : 모든 군집의 |수급 합|이 이 값 이하면 만족하고 끝낸다
#   ADJUST_BALANCE_LIMIT   : 이 값을 넘는 군집은 크기와 무관하게 재조정 대상에 넣는다
ADJUST_MAX_ITER = int(os.getenv("PBR_ADJUST_MAX_ITER", "200"))
ADJUST_BALANCE_OK = int(os.getenv("PBR_ADJUST_BALANCE_OK", "3"))
ADJUST_BALANCE_LIMIT = int(os.getenv("PBR_ADJUST_BALANCE_LIMIT", "5"))

# ---- step0 목표 재고 안전계수 ----
# target_qty = mu + z·sigma 의 z. 값이 클수록 수요가 몰리는 날까지 덮지만
# 그만큼 채워야 할 대수가 늘어난다.
#
# 1.65는 "정규분포에서 95%"라는 이유로 쓰였으나, 12개월 백테스트에서 실제 커버리지가
# 91.7~92.8%에 그쳤다. 순수요 분포의 꼬리가 정규분포보다 두껍기 때문이다.
# z=1.99로 올리면 평균 94.9%가 되고, 대가는 처리 상한 +30%다. 다만 회차마다 대가가
# 다르다 — pick 가능량이 이미 병목인 _05_10은 작업량이 늘지 않는다(-1.8%).
# 근거·재현: docs/분석/EXPERIMENTS.md 1장, python experiments/params/z_sweep.py
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
    # 계절 수준 보정 — 계획 대상 달의 첫 N일 실적으로 배율을 구한다.
    warmup_period: str = ""     # 빈 값이면 target_date의 달을 쓴다
    warmup_days: int = DEFAULT_WARMUP_DAYS

    @property
    def raw_path(self) -> Path:
        return PROJECT_ROOT / self.raw_file

    @property
    def day_label(self) -> str:
        """출력 메시지용 한국어 표기(평일/휴일)."""
        return DAY_TYPE_LABELS.get(self.day_type, self.day_type)

    @property
    def warmup_label(self) -> str:
        """보정에 쓸 기간. 지정이 없으면 계획 대상일이 속한 달."""
        return self.warmup_period or period_label(self.target_date)

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
    parser.add_argument(
        "--warmup-period",
        default=None,
        help="계절 보정에 쓸 기간(기본: 계획 대상일의 달). 그 달 첫 N일 실적을 쓴다",
    )
    parser.add_argument(
        "--warmup-days",
        type=int,
        default=None,
        help=f"보정에 쓸 일수 (기본 {DEFAULT_WARMUP_DAYS}, 0이면 끔)",
    )
    return parser


def require_columns(frame, columns, source: str):
    """단계 간에 주고받는 표에 필수 컬럼이 다 있는지 확인한다.

    단계들은 CSV 파일로 통신하므로 컬럼 이름이 하나만 어긋나도 뒤 단계가
    엉뚱한 자리에서 KeyError로 죽거나 — 더 나쁘게는 — 조용히 틀린 값을 낸다.
    **읽는 쪽에서 즉시 멈추게** 해서 어느 산출물이 잘못됐는지 바로 알려 준다.

    source: 사람이 읽을 파일 설명(경로나 산출물 이름). 오류 메시지에 그대로 나온다.
    """
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise SystemExit(
            f"[{source}] 필수 컬럼이 없습니다: {missing}. "
            f"있는 컬럼: {list(frame.columns)[:12]}. "
            f"앞 단계 산출물이 오래됐거나 실행 라벨이 어긋났는지 확인하세요.")
    if frame.empty:
        print(f"[안내] {source}: 행이 없습니다.")
    return frame


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
        warmup_period=args.warmup_period or os.getenv("PBR_WARMUP_PERIOD", ""),
        warmup_days=(args.warmup_days if args.warmup_days is not None
                     else DEFAULT_WARMUP_DAYS),
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


def duration_hours(duration: str) -> list:
    """시간대 문자열(`_05_10`)을 그 창에 드는 시각 목록으로.

    **시간 목록을 만드는 곳은 여기 하나다.** `_20_05`처럼 자정을 넘기는 창이 있어
    단순한 range로는 안 되는데, 같은 규칙이 step0·step4·demand_model·tools에
    네 번 복제돼 있었다. 한 곳이라도 다르게 고치면 계획을 세운 창과 채점한 창이
    조용히 어긋난다.
    """
    start, end = int(duration.split("_")[1]), int(duration.split("_")[2])
    return list(range(start, end)) if start < end else         list(range(start, 24)) + list(range(0, end))


def duration_list(config: RuntimeConfig) -> Tuple[str, ...]:
    """Return one or more duration labels.

    --duration accepts comma-separated values for batch execution.
    """

    return tuple(item.strip() for item in config.duration.split(",") if item.strip())


def normalize_durations(value) -> str:
    """시간대 입력을 검사해 콤마 표기 하나로 되돌린다.

    체크박스 여러 개(리스트)도, `_05_10,_10_15` 같은 콤마 문자열도 받는다.
    **DURATIONS에 없는 값은 되돌린다** — 오타가 step4 duration_hours()까지
    흘러가면 크래시가 된다(1.17.3에서 실제로 겪었다).
    중복은 지우고 순서는 DURATIONS를 따른다 — 하루 흐름 순서로 돌아야 한다.
    """
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    else:
        items = [str(item).strip() for item in (value or ())]
    items = [item for item in items if item]

    if (unknown := [item for item in items if item not in DURATIONS]):
        raise ValueError(
            f"시간대는 {', '.join(DURATIONS)} 중에서 고릅니다"
            f" (모르는 값: {', '.join(unknown)})."
        )
    chosen = set(items)
    return ",".join(duration for duration in DURATIONS if duration in chosen)


def normalize_period(value: str) -> str:
    """순수요 기간 입력을 검사한다.

    표기가 맞는지 보고, 계산해 둔 기간을 알 수 있으면 그 안에 있는지까지 본다 —
    없는 달을 넣으면 step0가 파일을 못 찾고 멈춘다. 새 저장소(목록이 빈 경우)에는
    표기만 본다.
    """
    period = (value or "").strip()
    if not period:
        return ""
    if not re.fullmatch(r"\d{2}년 \d{2}월", period):
        raise ValueError("순수요 기간은 '25년 11월' 표기로 적습니다.")
    if (periods := available_periods()) and period not in periods:
        raise ValueError(
            f"'{period}'의 순수요가 없습니다. 있는 기간: {', '.join(periods)}")
    return period


# API 수집 단계(step0의 api 묶음)가 만드는 스냅샷. **실행 라벨마다 따로**다.
# (하위 폴더, 파일명 틀) — `--skip-api`가 무엇을 물려받아야 하는지 여기서 읽는다.
API_SNAPSHOTS = (
    ("대여소별 재고", "대여소별_자전거대수 ({now}).csv"),
    ("대여소별 주차대수", "대여소별_주차대수 ({now}).csv"),
    ("대여소 정보", "st_info ({now}).csv"),
)

_SNAPSHOT_LABEL_RE = re.compile(r"^(?:.*) \((.+)\)\.csv$")


def snapshot_paths(label: str) -> Tuple[Path, ...]:
    """어떤 실행 라벨의 재고 스냅샷 파일 경로들."""
    return tuple((PP_ROOT / subdir / name.format(now=label))
                 for subdir, name in API_SNAPSHOTS)


def snapshot_labels() -> Tuple[str, ...]:
    """스냅샷이 **세 개 다** 있는 실행 라벨 목록(최근 수정 순).

    `--skip-api`로 물려받을 수 있는 후보다. 하나라도 빠진 라벨은 쓸 수 없으므로
    아예 후보에서 뺀다 — 반쯤 있는 라벨을 물려받으면 다음 단계에서 멈춘다.
    """
    subdir, name = API_SNAPSHOTS[0]
    found = []
    try:
        entries = list((PP_ROOT / subdir).iterdir())
    except OSError:
        return ()
    for path in entries:
        if not (m := _SNAPSHOT_LABEL_RE.match(path.name)):
            continue
        label = m.group(1)
        if all(p.exists() for p in snapshot_paths(label)):
            found.append((path.stat().st_mtime, label))
    return tuple(label for _, label in sorted(found, reverse=True))


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

