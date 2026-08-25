"""기상청 날씨 원천 — 파일과 API를 한 곳에서 읽는다.

**타슈를 tashu.py 하나로 부르는 것과 같은 이유다.** 날씨는 두 경로로 들어온다.

    과거 자료  기상자료개방포털(data.kma.go.kr) 종관기상관측(ASOS) 시간자료 CSV
    현재 자료  기상청 API 허브(apihub.kma.go.kr) `kma_sfctm2` — 한 시각씩

두 경로가 각자 컬럼과 결측 규칙을 정하면, 학습이 본 '비'와 오늘 화면이 보여 주는
'비'가 조용히 다른 것을 가리키게 된다. 규약을 여기서 하나로 맞춘다.

**받아 오기만 한다.** 무엇에 쓸지(피처로 넣을지, 화면에 띄울지)는 부르는 쪽이 정한다.

읽어 오는 컬럼 (COLUMNS):

    time   관측 시각(정시, KST)
    temp   기온 °C
    rain   그 1시간 동안의 강수량 mm  (겨울 3시간 누적은 아래에서 펴 준다)
    wind   풍속 m/s
    humid  습도 %
    snow   적설 cm (그 시각에 쌓여 있는 높이)

빈칸의 뜻이 컬럼마다 다르다 — 자세한 것은 `_fill_missing()`에 적어 두었다.
받는 방법과 측정 결과는 docs/WEATHER.md에 있다.
"""
from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv

from project_config import DEFAULT_WEATHER_FILE, PROJECT_ROOT, duration_hours

# 대전 종관기상관측(ASOS) 지점 번호. 도시 하나를 한 관측소로 대표한다 —
# 대여소별로 날씨를 쪼개지 않는 이유는 계절 배율과 같다(며칠치로 나누면 잡음만 커진다).
STATION_ID = 133

API_URL = "https://apihub.kma.go.kr/api/typ01/url/kma_sfctm2.php"
API_KEY_ENV = "APIHUN_KMA_TYPE01_KEY"

COLUMNS = ("time", "temp", "rain", "wind", "humid", "snow")

# 포털 CSV의 헤더 문구는 다운로드 옵션에 따라 조금씩 다르다('기온(°C)', '평균기온(°C)' …).
# 그래서 정확히 맞히지 않고 **부분 일치**로 찾는다.
HEADER_HINTS = {
    "time": ("일시", "관측시각", "시각", "tm"),
    "temp": ("기온",),
    "rain": ("강수",),
    "wind": ("풍속",),
    "humid": ("습도",),
    "snow": ("적설",),
}

# 포털 CSV는 보통 CP949다. 대여이력에서 BOM에 한 번 데었으므로 utf-8-sig까지 본다.
ENCODINGS = ("cp949", "utf-8-sig", "utf-8")

# 강수량이 **3시간 누적으로만** 기록되는 달. 기상청 규약이다
# ("11월~익년 3월까지는 3시간 강수량, 4~10월은 1시간 강수량").
WINTER_MONTHS = (11, 12, 1, 2, 3)

# apihub 응답의 결측 표시. 값이 아니라 '없음'이다.
API_MISSING = -9.0

# apihub `kma_sfctm2` 한 줄의 자리 번호 (help=1로 확인).
API_FIELDS = {"time": 0, "stn": 1, "wind": 3, "temp": 11, "humid": 13,
              "rain": 15, "rain_day": 16, "snow": 21}


class WeatherError(RuntimeError):
    """자료를 못 읽었거나 API 호출이 실패했다. 부르는 쪽이 사람에게 보여 줄 문구를 담는다."""


# ---------------------------------------------------------------- 파일(포털 CSV)

def _read_one(path: Path) -> pd.DataFrame:
    """CSV 한 장을 인코딩을 짚어 가며 읽는다."""
    last = None
    for encoding in ENCODINGS:
        try:
            return pd.read_csv(path, encoding=encoding)
        except UnicodeDecodeError as err:
            last = err
    raise WeatherError(f"날씨 파일의 인코딩을 알 수 없습니다: {path.name} ({last})")


def _rename(frame: pd.DataFrame, source: str) -> pd.DataFrame:
    """헤더 문구를 규약 컬럼으로 옮긴다. 없는 컬럼은 만들지 않는다."""
    mapping = {}
    for column in frame.columns:
        text = str(column).strip().lower()
        for name, hints in HEADER_HINTS.items():
            if name in mapping.values():
                continue
            if any(hint.lower() in text for hint in hints):
                mapping[column] = name
                break
    renamed = frame.rename(columns=mapping)[list(mapping.values())]
    if "time" not in renamed.columns:
        raise WeatherError(f"날씨 파일에 관측 시각 컬럼이 없습니다: {source}")
    return renamed


def _spread_winter_rain(frame: pd.DataFrame) -> pd.DataFrame:
    """겨울 강수량(3시간 누적)을 그 3시간에 고르게 나눈다.

    11~3월에는 03·06·09…시에만 값이 있고 그 값은 **직전 3시간 합**이다. 그대로 두면
    5시간 창(`_05_10` = 05~09시)에 03~06시 누적이 통째로 딸려 들어와, 창 밖에서 온
    비가 창 안에 있는 것으로 보인다. 실측에서 겨울 넉 달은 강수 기록이 100% 3의
    배수 시각에 있었다.

    나누는 조건은 세 가지가 모두 맞을 때뿐이다 — 겨울이고, 3의 배수 시각이고,
    **앞 두 시각이 빈칸**일 것. 11월 8~9일처럼 시간 단위로 기록된 구간이 섞여 있어서,
    달만 보고 나누면 멀쩡한 1시간 값을 쪼개게 된다.
    """
    if "rain" not in frame.columns or frame.empty:
        return frame

    rain = pd.to_numeric(frame["rain"], errors="coerce")
    time = frame["time"]
    hour, month = time.dt.hour, time.dt.month
    contiguous = (time - time.shift(2)) == timedelta(hours=2)
    mark = (month.isin(WINTER_MONTHS) & (hour % 3 == 0) & rain.notna()
            & rain.shift(1).isna() & rain.shift(2).isna() & contiguous)

    values = rain.to_numpy(dtype=float).copy()
    for position in np.flatnonzero(mark.to_numpy()):
        values[position - 2:position + 1] = values[position] / 3.0
    frame = frame.copy()
    frame["rain"] = values
    return frame


def _fill_missing(frame: pd.DataFrame) -> pd.DataFrame:
    """빈칸을 메운다. **컬럼마다 뜻이 달라서 한 규칙으로 채우면 안 된다.**

    - 강수·적설의 빈칸은 "안 왔다"는 뜻이다 → 0. 이걸 결측으로 지우면 비 안 온
      시간이 통째로 사라져 자료의 대부분을 잃는다(실측: 13,848시간 중 강수는 1,555시간).
    - 기온·습도·풍속의 빈칸은 **진짜 결측**이다(장비 문제 등) → 0으로 채우면 한겨울
      기온이 되어 버린다. 앞뒤 시각으로 잇는다.
    """
    frame = frame.copy()
    for name in ("rain", "snow"):
        if name in frame.columns:
            frame[name] = pd.to_numeric(frame[name], errors="coerce").fillna(0.0)
    for name in ("temp", "wind", "humid"):
        if name in frame.columns:
            series = pd.to_numeric(frame[name], errors="coerce")
            frame[name] = series.interpolate(limit_direction="both")
    return frame


def load_hourly(source=None) -> pd.DataFrame:
    """시간 단위 관측 자료를 규약 컬럼으로 읽는다.

    `source`는 파일 하나여도 되고 **디렉터리여도 된다**(그 안의 CSV를 모두 이어
    붙인다 — 포털은 해가 바뀌면 파일을 나눠 준다). 생략하면
    `project_config.DEFAULT_WEATHER_FILE`을 본다.

    자료가 없으면 빈 DataFrame을 돌려준다 — **날씨는 있으면 좋고 없어도 되는
    입력이다.** 없다고 파이프라인을 멈추면 안 된다.
    """
    path = Path(source) if source else Path(DEFAULT_WEATHER_FILE)
    if not path.is_absolute():
        path = PROJECT_ROOT / path

    if path.is_dir():
        files = sorted(path.glob("*.csv"))
    elif path.exists():
        files = [path]
    else:
        files = []
    if not files:
        return pd.DataFrame(columns=list(COLUMNS))

    frames = [_rename(_read_one(file), file.name) for file in files]
    frame = pd.concat(frames, ignore_index=True)
    frame["time"] = pd.to_datetime(frame["time"], errors="coerce")
    frame = frame.dropna(subset=["time"]).sort_values("time")
    frame = frame.drop_duplicates(subset=["time"], keep="last").reset_index(drop=True)

    frame = _spread_winter_rain(frame)
    frame = _fill_missing(frame)
    for name in COLUMNS:
        if name not in frame.columns:
            frame[name] = np.nan
    return frame[list(COLUMNS)]


def window_frame(hourly: pd.DataFrame, duration: str) -> pd.DataFrame:
    """시간 자료를 **5시간 창** 단위로 접는다 — 파이프라인이 계획을 세우는 단위다.

        강수            그 창의 **합**   (5시간 동안 온 총량)
        기온·풍속·습도  그 창의 **평균**
        적설            그 창의 **최대** (쌓인 높이라 더하면 안 된다)

    `hours`는 그 창에서 실제로 관측된 시각 수다. `len(duration_hours(duration))`보다
    적으면 빠진 시각이 있었다는 뜻이니(`_20_05`는 9시간이다) 부르는 쪽에서 거를 수
    있게 남긴다.

    `_20_05`처럼 자정을 넘기는 창은 **창이 시작한 날짜**로 묶는다 — 20시와 새벽 4시가
    같은 밤이기 때문이다.
    """
    if hourly.empty:
        return pd.DataFrame(
            columns=["date", "rain", "temp", "wind", "humid", "snow", "hours"])

    hours = duration_hours(duration)
    start, end = int(duration.split("_")[1]), int(duration.split("_")[2])

    frame = hourly[hourly["time"].dt.hour.isin(hours)].copy()
    frame["date"] = frame["time"].dt.normalize()
    if start > end:  # 자정을 넘긴 창의 새벽 몫은 전날 밤에 붙인다
        frame.loc[frame["time"].dt.hour < end, "date"] -= timedelta(days=1)

    folded = frame.groupby("date").agg(
        rain=("rain", "sum"),
        temp=("temp", "mean"),
        wind=("wind", "mean"),
        humid=("humid", "mean"),
        snow=("snow", "max"),
        hours=("time", "size"),
    ).reset_index()
    folded["date"] = folded["date"].dt.strftime("%Y-%m-%d")
    return folded


# ------------------------------------------------------------------- API(apihub)

def _api_key() -> str:
    load_dotenv(PROJECT_ROOT / ".env")
    key = os.getenv(API_KEY_ENV)
    if not key:
        raise WeatherError(f"{API_KEY_ENV}가 .env에 없습니다.")
    return key


def _value(fields: list, name: str):
    """자리에서 값을 꺼낸다. -9/-9.0은 값이 아니라 '없음'이므로 None으로 돌려준다."""
    position = API_FIELDS[name]
    if position >= len(fields):
        return None
    try:
        value = float(fields[position])
    except ValueError:
        return None
    return None if value <= API_MISSING else value


def fetch_hour(when=None, stn: int = STATION_ID, timeout: float = 30) -> dict:
    """한 시각의 관측값을 apihub에서 받아 온다.

    반환: {'time', 'stn', 'temp', 'wind', 'humid', 'rain', 'rain_day', 'snow'}
    (결측은 None)

    ⚠️ **겨울(11~3월)의 `rain`은 3의 배수 시각에만 값이 있다** — 기상청이 그 계절을
    3시간 누적으로만 준다. 그 사이 시각은 None이지 0이 아니다. 하루 누적이 필요하면
    `rain_day`(그날 00시부터의 누적)를 봐라.

    `when`을 생략하면 API가 **가장 최근 관측**을 돌려준다.
    """
    params = {"stn": str(stn), "help": "0", "authKey": _api_key()}
    if when is not None:
        stamp = when if isinstance(when, str) else when.strftime("%Y%m%d%H%M")
        params["tm"] = stamp

    try:
        response = requests.get(API_URL, params=params, timeout=timeout)
    except requests.RequestException as err:
        raise WeatherError(f"기상청 API에 연결하지 못했습니다: {type(err).__name__}") from err

    response.encoding = "euc-kr"
    if response.status_code != 200:
        raise WeatherError(f"기상청 API 호출 실패: {response.status_code} {response.text[:200]}")
    if '"status"' in response.text:  # 활용신청 안 됨 등은 자료 대신 JSON 알림으로 온다
        raise WeatherError(f"기상청 API가 자료 대신 알림을 보냈습니다: {response.text[:200]}")

    rows = [line for line in response.text.splitlines()
            if line.strip() and not line.startswith("#")]
    if not rows:
        raise WeatherError("기상청 API 응답에 관측값이 없습니다.")

    fields = rows[0].split()
    return {
        "time": pd.to_datetime(fields[0], format="%Y%m%d%H%M", errors="coerce"),
        "stn": int(fields[1]),
        "temp": _value(fields, "temp"),
        "wind": _value(fields, "wind"),
        "humid": _value(fields, "humid"),
        "rain": _value(fields, "rain"),
        "rain_day": _value(fields, "rain_day"),
        "snow": _value(fields, "snow"),
    }


def fetch_range(start, end, stn: int = STATION_ID, max_calls: int = 48) -> pd.DataFrame:
    """`start`부터 `end`까지 **한 시각씩** 받아 이어 붙인다.

    기간을 한 번에 주는 API(`kma_sfctm3`)는 apihub에서 **따로 활용신청을 해야** 열린다
    (신청 전에는 403 '활용신청이 필요한 API입니다'). 신청 전에도 쓸 수 있게 시각별
    호출을 묶어 두지만, 호출 수가 시간 수만큼 늘기 때문에 `max_calls`로 막아 둔다.
    **과거 몇 달치를 이걸로 받지 마라** — 그건 포털 CSV가 할 일이다.
    """
    start = pd.to_datetime(start).floor("h")
    end = pd.to_datetime(end).floor("h")
    stamps = pd.date_range(start, end, freq="h")
    if len(stamps) > max_calls:
        raise WeatherError(
            f"{len(stamps)}시간은 한 번에 받기에 많습니다(상한 {max_calls}). "
            "긴 기간은 기상자료개방포털 CSV를 쓰거나 kma_sfctm3 활용신청을 하세요.")

    rows = [fetch_hour(stamp, stn=stn) for stamp in stamps]
    frame = pd.DataFrame(rows)
    return frame.drop(columns=["stn", "rain_day"], errors="ignore")
