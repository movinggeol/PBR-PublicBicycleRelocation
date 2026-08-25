"""계획을 세우는 화면에 **지금 날씨**를 띄운다.

왜 필요한가: 비가 오면 그날 실제 재배치 필요량이 **평소의 40~55%** 로 떨어진다
(docs/분석/WEATHER.md ②). 그런데 계획은 지난달 통계로 세우므로 그 사실을 모른다.
그대로 내보내면 오지 않을 수요를 채우러 기사가 나간다.

**계획을 바꾸지는 않는다.** 작업지시서와 같은 성향이다 — 화면은 사실과 그 뜻만
보여 주고, 오늘 어떻게 할지는 사람이 정한다. 자동으로 계획량을 줄였다가 예보가
빗나가면 결품이 나는데, 그 판단은 현장 몫이다.

여기서 쓰는 것은 **관측**이다(기상청 API 허브 `kma_sfctm2`). 예보는 활용신청이
따로 필요하다 — 열리면 `weather.py`에 예보 클라이언트를 붙이고 이 화면이 '지금'
대신 '오늘 예보'를 말하게 하면 된다.
"""
from __future__ import annotations

import time
from typing import Optional

import weather

# 화면을 열 때마다 외부 API를 때리지 않는다. 관측은 정시마다 갱신되므로 10분이면
# 충분하고, 여러 사람이 동시에 화면을 열어도 호출은 그대로 한 번이다.
CACHE_SECONDS = 600

# 비 오는 날 실제 필요량이 평소의 몇 %였는지 (docs/분석/WEATHER.md ②의 -45~-61%).
# 화면 문구에 쓰는 값이라 여기 한 번만 적는다.
RAINY_NEED_SHARE = "40~55%"

_CACHE: dict = {"at": 0.0, "data": None}


def _headline(state: str) -> str:
    return {
        "raining": "지금 비가 오고 있습니다",
        "rained_today": "오늘 비가 왔습니다",
        "dry": "지금 비는 오지 않습니다",
        "unknown": "지금 강수 여부를 알 수 없습니다",
    }[state]


def _note(state: str) -> str:
    if state in ("raining", "rained_today"):
        return (f"비 오는 날 실제 재배치 필요량은 평소의 {RAINY_NEED_SHARE} 수준이었습니다"
                " — 계획량을 그대로 내보내면 오지 않을 수요를 채우러 가게 됩니다."
                " 계획은 이 사실을 모르니 현장에서 감안하세요.")
    if state == "unknown":
        return ("겨울(11~3월)에는 기상청이 강수량을 3시간마다만 줍니다."
                " 그 사이 시각에는 값이 비어 있어 '비가 안 왔다'와 구분되지 않습니다.")
    return "평소대로 계획을 내보내면 됩니다."


def _state(observed: dict) -> str:
    """강수 상태.

    **API의 `-9`(값 없음)는 대부분 '비가 안 왔다'는 뜻이다.** 실측으로 확인했다 —
    비가 오던 시각을 조회하면 실제 강수량이 오고(2026-07-05 05시 9.4mm), 안 오던
    시각은 시간·일강수가 모두 -9였다. 그래서 여름에 `-9`를 '모름'으로 보여 주면
    화면이 늘 '모름'만 말하게 된다.

    **겨울(11~3월)만 예외다.** 그때는 기상청이 강수를 3시간마다만 주므로, 그 사이
    시각의 -9는 진짜로 '아직 모른다'일 수 있다. 그 경우에만 모른다고 말한다.
    """
    rain, rain_day = observed.get("rain"), observed.get("rain_day")
    if rain is not None:
        return "raining" if rain >= weather.RAIN_MM else "dry"
    if rain_day is not None:
        return "rained_today" if rain_day >= weather.RAIN_MM else "dry"

    stamp = observed.get("time")
    winter = stamp is not None and stamp.month in weather.WINTER_MONTHS
    return "unknown" if winter and stamp.hour % 3 != 0 else "dry"


def describe(observed: dict) -> dict:
    """관측값 한 줄을 화면이 쓸 모양으로 옮긴다(순수 계산)."""
    state = _state(observed)
    stamp = observed.get("time")
    return {
        "available": True,
        "observed_at": stamp.strftime("%Y-%m-%d %H:%M") if stamp is not None else None,
        "temp": observed.get("temp"),
        "wind": observed.get("wind"),
        "humid": observed.get("humid"),
        "rain": observed.get("rain"),
        "rain_day": observed.get("rain_day"),
        "state": state,
        "raining": state in ("raining", "rained_today"),
        "headline": _headline(state),
        "note": _note(state),
    }


def current(force: bool = False) -> dict:
    """지금 날씨. 실패해도 **예외를 올리지 않는다** — 화면이 죽으면 안 된다."""
    now = time.monotonic()
    if not force and _CACHE["data"] is not None and now - _CACHE["at"] < CACHE_SECONDS:
        return _CACHE["data"]

    try:
        described = describe(weather.fetch_hour())
    except weather.WeatherError as err:
        described = {"available": False, "error": str(err), "raining": False}
    except Exception as err:                  # 예상 못 한 응답 형식 등
        described = {"available": False, "raining": False,
                     "error": f"날씨를 읽지 못했습니다({type(err).__name__})."}

    _CACHE.update(at=now, data=described)
    return described


def reset_cache() -> None:
    """테스트와 '새로고침' 버튼이 쓴다."""
    _CACHE.update(at=0.0, data=None)
