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
받는 방법과 측정 결과는 docs/분석/WEATHER.md에 있다.
"""
from __future__ import annotations

import math
import os
import shlex
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

# 단기예보(육상예보) 문. 관측(kma_sfctm2)과 별개로 활용신청해야 열린다(2026-08-29).
FORECAST_URL = "https://apihub.kma.go.kr/api/typ01/url/fct_afs_dl.php"

# 예보구역코드. `fct_shrt_reg.php`(예보구역코드 조회, 별도 API)로 확인했다 —
# 대전(C급=구·군 단위), 1990-01-01부터 유효. 상위 구역인 `11C20400`
# (대전·세종·충남중부내륙)보다 좁게 대전 하나만 잡은 것이다.
FORECAST_REG_ID = "11C20401"

# 강수유무코드(PREP). '0' 외에는 어떤 형태로든 강수가 예보됐다는 뜻이다.
FORECAST_RAIN_CODES = {"1": "비", "2": "비/눈", "3": "눈", "4": "소나기"}

# 단기예보 격자(수치예보). `fct_afs_dl.php`(문장 예보, 강수확률·유무만)와 달리
# 강수량(mm)을 준다 — RAIN_MM과 같은 자리에 바로 쓸 수 있다. 관측과는 별개로
# 활용신청해야 열린다(2026-08-29 추가). 전국을 5km 격자 149×253칸으로 덮는다.
GRID_URL = "https://apihub.kma.go.kr/api/typ01/cgi-bin/url/nph-dfs_shrt_grd"
GRID_NX, GRID_NY = 149, 253
GRID_MISSING = -99.0  # 격자 밖(바다·해외)

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

# '비 온 날'로 볼 강수량 문턱(창 합계 mm). 약한 비는 이용에 거의 영향이 없다.
# **이 값을 쓰는 곳이 셋이다**(예측 피처·측정·화면). 한 곳에서만 바꿔야
# 학습이 본 '비'와 화면이 말하는 '비'가 같은 것을 가리킨다.
RAIN_MM = 1.0

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


def fetch_forecast(tmfc1=None, tmfc2=None, reg: str = FORECAST_REG_ID,
                   timeout: float = 30) -> pd.DataFrame:
    """단기예보(육상예보) 문을 apihub에서 받아 표로 편다.

    관측(`kma_sfctm2`)과 **별개로 활용신청해야** 열린다(2026-08-29 확인).
    `tmfc1`·`tmfc2`(발표시각 범위, `YYYYMMDDHH`)를 생략하면 **가장 최근 발표**
    하나만 돌려준다. 한 번 발표에 **12시간 간격으로 최대 7개**(NE 0~6, 오늘
    남은 시간부터 최대 3.5일 앞)를 준다.

    ⚠️ **관측과 다르다** — 강수량(mm)이 아니라 **강수확률(%)·강수유무코드**뿐이다.
    `RAIN_MM`(관측 쪽 mm 문턱)과 같은 자리에 쓰려면 `rain_type != "0"`(어떤
    형태로든 강수 예보)이나 `rain_prob` 문턱 중 하나를 새로 정해야 한다 —
    아직 판정하지 않았다(계획에 자동 반영하기 전에 재는 것이 이 저장소의 규칙,
    docs/분석/WEATHER.md 5장).

    반환 컬럼: `issued_at`(발표시각) · `valid_at`(발효시각, 예보 대상 시각) ·
    `temp` · `rain_prob`(%) · `sky_code` · `rain_type`(코드, `FORECAST_RAIN_CODES`
    참고) · `text`(예보문)
    """
    params = {"reg": reg, "tmfc1": tmfc1 or "0", "tmfc2": tmfc2 or "0",
             "disp": "0", "help": "0", "authKey": _api_key()}
    try:
        response = requests.get(FORECAST_URL, params=params, timeout=timeout)
    except requests.RequestException as err:
        raise WeatherError(f"기상청 API에 연결하지 못했습니다: {type(err).__name__}") from err

    response.encoding = "euc-kr"
    if response.status_code != 200:
        raise WeatherError(f"기상청 API 호출 실패: {response.status_code} {response.text[:200]}")
    if '"status"' in response.text:  # 활용신청 안 됨 등은 자료 대신 JSON 알림으로 온다
        raise WeatherError(f"기상청 API가 자료 대신 알림을 보냈습니다: {response.text[:200]}")

    lines = [line for line in response.text.splitlines()
            if line.strip() and not line.startswith("#")]
    if not lines:
        return pd.DataFrame(columns=["issued_at", "valid_at", "temp", "rain_prob",
                                     "sky_code", "rain_type", "text"])

    rows = []
    for line in lines:
        # WF(예보문, 마지막 칸)는 따옴표로 감싼 구(句)라 공백으로 나누면 깨진다 —
        # shlex가 따옴표 안 공백은 살리고 바깥은 그대로 나눠 준다.
        fields = shlex.split(line)
        rows.append({
            "issued_at": fields[1], "valid_at": fields[2],
            "temp": fields[12], "rain_prob": fields[13],
            "sky_code": fields[14], "rain_type": fields[15], "text": fields[16],
        })

    frame = pd.DataFrame(rows)
    frame["issued_at"] = pd.to_datetime(frame["issued_at"], format="%Y%m%d%H%M")
    frame["valid_at"] = pd.to_datetime(frame["valid_at"], format="%Y%m%d%H%M")
    frame["temp"] = pd.to_numeric(frame["temp"], errors="coerce")
    frame["rain_prob"] = pd.to_numeric(frame["rain_prob"], errors="coerce")
    return frame


# --------------------------------------------------------- 격자예보(정량, apihub)

def latlon_to_grid(lat: float, lon: float) -> tuple[int, int]:
    """위경도를 단기예보 5km 격자 좌표(nx, ny)로 바꾼다.

    기상청이 공개한 변환식(Lambert Conformal Conic)을 그대로 옮긴 것이다 — 대전
    관제센터 좌표로 실측 기온·강수량과 대조해 확인했다(2026-08-29,
    docs/분석/WEATHER.md). 격자 밖(바다·해외)인지는 `fetch_grid()`가 돌려주는
    값(`np.nan`)으로 판단한다.
    """
    re_ = 6371.00877 / 5.0  # 지구 반경(km) / 격자 간격(km)
    slat1, slat2 = math.radians(30.0), math.radians(60.0)
    olon, olat = math.radians(126.0), math.radians(38.0)
    xo, yo = 43, 136

    sn = math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5)
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(sn)
    sf = math.tan(math.pi * 0.25 + slat1 * 0.5)
    sf = math.pow(sf, sn) * math.cos(slat1) / sn
    ro = re_ * sf / math.pow(math.tan(math.pi * 0.25 + olat * 0.5), sn)

    ra = re_ * sf / math.pow(math.tan(math.pi * 0.25 + math.radians(lat) * 0.5), sn)
    theta = math.radians(lon) - olon
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= sn

    x = ra * math.sin(theta) + xo + 0.5
    y = ro - ra * math.cos(theta) + yo + 0.5
    return int(x), int(y)


def fetch_grid(tmfc, tmef, var: str = "PCP", timeout: float = 30) -> np.ndarray:
    """한 시각·한 변수의 전국 격자를 apihub에서 받는다.

    `tmfc`(발표시각)·`tmef`(발효시각)는 `YYYYMMDDHH`(KST, 시 단위) 문자열이나
    datetime. 반환은 `(GRID_NY, GRID_NX)` 배열이고 `[0]`행이 남쪽이다(대전 좌표로
    확인). 격자 밖 칸은 `np.nan`. 좌표를 값으로 바로 꺼내려면 `grid_value()`를 써라.

    ⚠️ **한 번에 시각 하나·변수 하나만 온다** — `fetch_forecast()`처럼 기간을
    묶어 받을 수 없다(2026-08-29 확인, 과거 최소 19개월치는 그대로 조회된다).
    """
    def _stamp(value):
        return value if isinstance(value, str) else value.strftime("%Y%m%d%H")

    params = {"tmfc": _stamp(tmfc), "tmef": _stamp(tmef), "vars": var, "authKey": _api_key()}
    try:
        response = requests.get(GRID_URL, params=params, timeout=timeout)
    except requests.RequestException as err:
        raise WeatherError(f"기상청 API에 연결하지 못했습니다: {type(err).__name__}") from err

    response.encoding = "euc-kr"
    if response.status_code != 200:
        raise WeatherError(f"기상청 API 호출 실패: {response.status_code} {response.text[:200]}")
    if '"status"' in response.text[:200]:  # 활용신청 안 됨 등은 자료 대신 JSON 알림으로 온다
        raise WeatherError(f"기상청 API가 자료 대신 알림을 보냈습니다: {response.text[:200]}")

    values = [float(v) for line in response.text.splitlines() if line.strip()
              for v in line.split(",") if v.strip()]
    expected = GRID_NX * GRID_NY
    if len(values) != expected:
        raise WeatherError(f"격자 크기가 예상과 다릅니다: {len(values)} (기대 {expected})")

    grid = np.array(values, dtype=float).reshape(GRID_NY, GRID_NX)
    grid[grid <= GRID_MISSING] = np.nan
    return grid


def grid_value(grid: np.ndarray, lat: float, lon: float):
    """격자에서 위경도 한 점의 값을 꺼낸다. 격자 밖(바다·해외)이면 None."""
    nx, ny = latlon_to_grid(lat, lon)
    value = grid[ny - 1, nx - 1]
    return None if np.isnan(value) else float(value)
