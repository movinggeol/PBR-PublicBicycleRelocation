"""계획을 세우는 화면에 **날씨**를 띄운다 — 지금 관측과 계획 대상일 예보 둘.

왜 필요한가: 비가 오면 그날 실제 재배치 필요량이 **평소의 40~55%** 로 떨어진다
(docs/분석/WEATHER.md ②). 그런데 계획은 지난달 통계로 세우므로 그 사실을 모른다.
그대로 내보내면 오지 않을 수요를 채우러 기사가 나간다.

**계획을 바꾸지는 않는다.** 작업지시서와 같은 성향이다 — 화면은 사실과 그 뜻만
보여 주고, 오늘 어떻게 할지는 사람이 정한다. 자동으로 계획량을 줄였다가 예보가
빗나가면 결품이 나는데, 그 판단은 현장 몫이다.

**둘을 함께 보여 주는 이유** — 계획은 **하루 앞서** 세워 다음 날 쓴다
(docs/분석/WEATHER.md 1-3장). 그러니 정작 맞아야 하는 것은 '지금'이 아니라
**계획 대상일**이다. 관측(`kma_sfctm2`)은 지금을 말하고, 예보는 그날을 말한다.

예보는 두 원천을 겹쳐 쓴다.

    fct_afs_dl.php        12시간 단위 문장 — 강수확률(%)·강수형태·기온. 호출 1번.
    nph-dfs_shrt_grd      5km 격자 수치예보 — **강수량(mm)**. 시각마다 호출 1번.

⚠️ **격자는 비가 예보된 날에만 부른다.** 시각당 한 번씩 부르는 API라 하루치가
10여 번이 되는데, 맑은 날에는 그 값이 전부 0이라 부를 이유가 없다. 문장 예보가
강수를 말할 때만 mm을 확인한다 — 흔한 경우(맑음)는 호출 1번으로 끝난다.

**왜 mm까지 보나** — 강수확률만으로는 수요 변화를 못 맞힌다는 것을 쟀다
(1.26.17: 비 온 날 개선 +1.2%). 같은 자리에 격자 강수량을 넣으면 +13.1%로
올라간다(1.26.18). 화면이 *"비 올 확률 60%"* 라고만 말하면 기사가 판단할
근거가 얇다 — **얼마나 오는지**가 실제로 이용을 가른다.
"""
from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Optional

import pandas as pd

import project_config
import weather

# 화면을 열 때마다 외부 API를 때리지 않는다. 관측은 정시마다 갱신되므로 10분이면
# 충분하고, 여러 사람이 동시에 화면을 열어도 호출은 그대로 한 번이다.
CACHE_SECONDS = 600

# 예보는 하루 두 번(12시간 단위) 갱신되므로 관측보다 길게 잡는다. 격자까지
# 부르는 날에는 호출이 10여 번이라 캐시가 더 중요하다.
FORECAST_CACHE_SECONDS = 1800

# 격자 강수량을 확인할 시각. 운영 시간대(_05_10 ~ _15_20)를 덮는다 — 야간은
# 계획을 세우지 않으므로 부르지 않는다.
FORECAST_HOURS = tuple(range(5, 21))

# 문장 예보가 이 확률 이상이거나 강수형태를 말하면 격자(mm)까지 확인한다.
# 낮추면 맑은 날에도 10여 번을 부르고, 높이면 약한 비를 놓친다.
FORECAST_GRID_PROB = 30

# 격자 발표를 최신부터 몇 개까지 훑어볼지. 하나가 비어 있을 수 있어 여유를 두되
# (발표 직후에는 아직 격자가 안 올라온다), 무한정 거슬러 올라가면 낡은 예보를 쓴다.
GRID_ISSUE_TRIES = 3

# 비 오는 날 실제 필요량이 평소의 몇 %였는지 (docs/분석/WEATHER.md ②의 -45~-61%).
# 화면 문구에 쓰는 값이라 여기 한 번만 적는다.
RAINY_NEED_SHARE = "40~55%"

_CACHE: dict = {"at": 0.0, "data": None}
_FORECAST_CACHE: dict = {"at": 0.0, "key": None, "data": None}


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
    _FORECAST_CACHE.update(at=0.0, key=None, data=None)


# ------------------------------------------------------------------ 예보(계획 대상일)

def _blocks_for(forecast: pd.DataFrame, target: date) -> pd.DataFrame:
    """대상일 하루를 덮는 12시간 구간 둘을 고른다.

    발효시각(TM_EF)이 **정오면 그날 00~12시**, **자정이면 전날 12~24시**다
    (`experiments/structure/forecast_impact.py`에서 실측으로 확인한 규약).
    그래서 대상일 D의 오후 구간은 `D+1 00:00`으로 온다.
    """
    if forecast.empty:
        return forecast
    stamp = pd.Timestamp(target)
    wanted = {stamp + pd.Timedelta(hours=12), stamp + pd.Timedelta(days=1)}
    picked = forecast[forecast["valid_at"].isin(wanted)]
    # 같은 구간이 여러 발표에 걸쳐 오면 **가장 늦은 발표**가 최신이다.
    return picked.sort_values("issued_at").drop_duplicates("valid_at", keep="last")


def _latest_issue(now: Optional[pd.Timestamp] = None) -> list:
    """아직 오지 않은 발표를 부르지 않도록, **지난 발표시각**을 최신순으로 준다.

    격자예보는 하루 8번(02·05·08·11·14·17·20·23시) 발표된다. 그런데 대상일의
    전날 23시를 고정으로 쓰면 **낮에 물었을 때 아직 없는 발표**를 부르게 된다 —
    응답이 200이지만 격자가 통째로 결측(-99)이라 조용히 빈손이 된다(실측 확인,
    2026-08-30). 그래서 지금 시각 기준으로 이미 나온 발표만 최신순으로 시도한다.
    """
    now = now if now is not None else pd.Timestamp.now()
    stamps = []
    for back in (0, 1):                       # 오늘·어제 발표까지만 본다
        day = now.normalize() - pd.Timedelta(days=back)
        for hour in (23, 20, 17, 14, 11, 8, 5, 2):
            issued = day + pd.Timedelta(hours=hour)
            if issued <= now:
                stamps.append(issued)
    return sorted(stamps, reverse=True)[:GRID_ISSUE_TRIES]


def _grid_rain_mm(target: date, now: Optional[pd.Timestamp] = None) -> Optional[dict]:
    """대상일의 시간별 격자 강수량(mm)을 모아 합·최대를 낸다.

    시각 하나가 실패하면 그 시각만 건너뛴다 — 몇 시간을 실제로 받았는지
    `hours`에 남겨 **얼마나 얇은 근거인지 숨기지 않는다.** 전부 실패하면 None.

    발표시각은 최신부터 훑어 **값이 나오는 첫 발표**를 쓴다(`_latest_issue()`).
    """
    stamp = pd.Timestamp(target)
    for issued in _latest_issue(now):
        total, peak, got = 0.0, 0.0, 0
        for hour in FORECAST_HOURS:
            try:
                grid = weather.fetch_grid(issued.strftime("%Y%m%d%H"),
                                          stamp.strftime("%Y%m%d") + f"{hour:02d}",
                                          var="PCP")
            except weather.WeatherError:
                continue
            except Exception:                  # 예상 못 한 응답 형식 등
                continue
            value = weather.grid_value(grid, project_config.DEPOT_LAT,
                                       project_config.DEPOT_LON)
            if value is None:
                continue
            total += value
            peak = max(peak, value)
            got += 1
        if got:
            return {"rain_mm": round(total, 1), "rain_mm_peak": round(peak, 1),
                    "hours": got, "hours_asked": len(FORECAST_HOURS),
                    "issued_at": issued.strftime("%Y-%m-%d %H:%M")}
    return None


def _forecast_state(rain_mm: Optional[float], rain_type: Optional[str],
                    rain_prob: Optional[float]) -> str:
    """예보의 강수 상태. **mm이 있으면 mm이 이긴다** — 확률은 '얼마나'를 모른다."""
    if rain_mm is not None:
        return "rain_expected" if rain_mm >= weather.RAIN_MM else "drizzle_expected"
    if rain_type and rain_type != "0":
        return "rain_expected"
    if rain_prob is not None:
        return "maybe_rain" if rain_prob >= FORECAST_GRID_PROB else "dry_expected"
    return "unknown"


def _forecast_headline(state: str, when: str) -> str:
    return {
        "rain_expected": f"{when} 비가 예보돼 있습니다",
        "drizzle_expected": f"{when} 약한 비가 예보돼 있습니다",
        "maybe_rain": f"{when} 비가 올 수 있습니다",
        "dry_expected": f"{when} 비 예보는 없습니다",
        "unknown": f"{when} 예보를 읽지 못했습니다",
    }[state]


def _forecast_note(state: str) -> str:
    if state == "rain_expected":
        return (f"비 오는 날 실제 재배치 필요량은 평소의 {RAINY_NEED_SHARE} 수준이었습니다"
                " — 계획량을 그대로 내보내면 오지 않을 수요를 채우러 가게 됩니다."
                " 계획은 이 사실을 모르니 현장에서 감안하세요.")
    if state == "drizzle_expected":
        return ("예보된 양이 적습니다. 약한 비는 이용에 거의 영향이 없었습니다"
                f" — 문턱은 {weather.RAIN_MM}mm입니다.")
    if state == "maybe_rain":
        return ("강수확률만 있고 예보 강수량은 확인하지 못했습니다."
                " **확률은 '얼마나 올지'를 말하지 않습니다** — 나가기 전에 다시 보세요.")
    if state == "unknown":
        return "예보를 못 받았습니다. 계획은 평소대로 내보내되 현장에서 하늘을 보세요."
    return "평소대로 계획을 내보내면 됩니다."


def describe_forecast(blocks: pd.DataFrame, target: date,
                      grid: Optional[dict]) -> dict:
    """예보를 화면이 쓸 모양으로 옮긴다(순수 계산 — API를 부르지 않는다)."""
    today = date.today()
    when = {0: "오늘", 1: "내일", 2: "모레"}.get((target - today).days,
                                                target.strftime("%m월 %d일"))

    rain_prob = rain_type = temp = issued_at = None
    if not blocks.empty:
        # 하루 두 구간 중 **더 궂은 쪽**을 대표로 쓴다 — 오전만 맑아도 오후에
        # 비가 오면 그날 계획은 영향을 받는다.
        rain_prob = float(blocks["rain_prob"].max()) if blocks["rain_prob"].notna().any() else None
        types = [t for t in blocks["rain_type"] if t and t != "0"]
        rain_type = types[0] if types else "0"
        temp = float(blocks["temp"].max()) if blocks["temp"].notna().any() else None
        issued_at = blocks["issued_at"].max().strftime("%Y-%m-%d %H:%M")

    rain_mm = grid["rain_mm"] if grid else None
    state = _forecast_state(rain_mm, rain_type, rain_prob)
    return {
        "available": not blocks.empty or grid is not None,
        "target_date": target.strftime("%Y-%m-%d"),
        "when": when,
        "issued_at": issued_at,
        "rain_prob": rain_prob,
        "rain_type": weather.FORECAST_RAIN_CODES.get(rain_type or "0"),
        "temp": temp,
        "rain_mm": rain_mm,
        "rain_mm_peak": grid["rain_mm_peak"] if grid else None,
        "grid_hours": grid["hours"] if grid else None,
        "grid_issued_at": grid["issued_at"] if grid else None,
        "state": state,
        "raining": state in ("rain_expected", "maybe_rain"),
        "headline": _forecast_headline(state, when),
        "note": _forecast_note(state),
    }


def forecast(target: Optional[date] = None, force: bool = False) -> dict:
    """계획 대상일 예보. 실패해도 **예외를 올리지 않는다** — 화면이 죽으면 안 된다.

    `target`을 생략하면 **내일**이다 — 계획을 하루 앞서 세우기 때문이다.
    """
    target = target or (date.today() + timedelta(days=1))
    key = target.isoformat()
    now = time.monotonic()
    if (not force and _FORECAST_CACHE["data"] is not None
            and _FORECAST_CACHE["key"] == key
            and now - _FORECAST_CACHE["at"] < FORECAST_CACHE_SECONDS):
        return _FORECAST_CACHE["data"]

    try:
        blocks = _blocks_for(weather.fetch_forecast(), target)
    except weather.WeatherError as err:
        blocks, described = pd.DataFrame(), {"available": False, "raining": False,
                                             "error": str(err)}
    except Exception as err:
        blocks, described = pd.DataFrame(), {
            "available": False, "raining": False,
            "error": f"예보를 읽지 못했습니다({type(err).__name__})."}
    else:
        described = None

    if described is None:
        # 맑은 날에는 격자를 부르지 않는다 — 전부 0인 값을 열 번 넘게 받을 이유가 없다.
        prob = blocks["rain_prob"].max() if not blocks.empty else None
        types = {t for t in blocks["rain_type"]} - {"0"} if not blocks.empty else set()
        worth_checking = bool(types) or (prob is not None and prob >= FORECAST_GRID_PROB)
        grid = _grid_rain_mm(target) if worth_checking else None
        described = describe_forecast(blocks, target, grid)

    _FORECAST_CACHE.update(at=now, key=key, data=described)
    return described
