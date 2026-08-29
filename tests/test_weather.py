"""날씨 원천 회귀 테스트 (docs/분석/WEATHER.md).

지키는 것:
  - **빈칸의 뜻이 컬럼마다 다르다** — 강수·적설의 빈칸은 0(안 왔다)이고,
    기온·풍속·습도의 빈칸은 진짜 결측이다. 한 규칙으로 채우면 비 안 온 시간이
    통째로 사라지거나, 장비가 멎은 시각이 한겨울 기온이 된다.
  - **겨울 강수는 3시간 누적** — 11~3월은 03·06·09시에만 값이 있고 그 값은 직전
    3시간 합이다. 펴 주지 않으면 창(_05_10) 밖에서 온 비가 창 안으로 딸려 들어온다.
  - **창 접기** — 강수는 합, 기온·바람은 평균, 적설은 최대. 자정을 넘기는 창은
    시작한 날짜로 묶는다.
  - **자료가 없어도 죽지 않는다** — 날씨는 있으면 좋고 없어도 되는 입력이다.

API는 부르지 않는다(응답 문자열을 직접 넣어 파싱만 본다). 실호출 검증은
docs/구현/TESTING.md 참고.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import weather


def write_csv(directory: Path, name: str, rows: list, encoding: str = "cp949") -> Path:
    """포털 CSV와 같은 헤더로 파일을 만든다(지점·지점명 컬럼 포함)."""
    header = "지점,지점명,일시,기온(°C),강수량(mm),풍속(m/s),습도(%),적설(cm)"
    path = directory / name
    path.write_text("\n".join([header, *rows]) + "\n", encoding=encoding)
    return path


def hour_rows(day: str, values: dict, hours=range(24)) -> list:
    """하루치 정시 관측. values[시각] = (기온, 강수, 풍속, 습도, 적설), 없으면 빈칸."""
    rows = []
    for hour in hours:
        temp, rain, wind, humid, snow = values.get(hour, (10.0, "", 1.0, 50.0, ""))
        rows.append(f"133,대전,{day} {hour:02d}:00,{temp},{rain},{wind},{humid},{snow}")
    return rows


def test_없는_경로는_빈_표를_돌려준다(tmp_path):
    """날씨가 없다고 파이프라인을 멈추면 안 된다 — 선택적 입력이다."""
    frame = weather.load_hourly(tmp_path / "없는폴더")
    assert frame.empty
    assert list(frame.columns) == list(weather.COLUMNS)


def test_강수_빈칸은_0이고_기온_빈칸은_이어_붙인다(tmp_path):
    rows = hour_rows("2025-06-01", {
        3: (20.0, 2.5, 1.0, 60.0, ""),      # 비가 온 시각
        4: ("", "", 1.0, 60.0, ""),          # 기온만 결측
    }, hours=range(6))
    write_csv(tmp_path, "기상자료.csv", rows)

    frame = weather.load_hourly(tmp_path)
    assert frame["rain"].isna().sum() == 0
    assert frame.loc[frame["time"].dt.hour == 0, "rain"].item() == 0.0   # 빈칸 = 안 왔다
    assert frame.loc[frame["time"].dt.hour == 3, "rain"].item() == 2.5
    # 기온은 0으로 채우지 않는다 — 앞(3시 20.0)과 뒤(5시 10.0)를 이어 15.0이 나온다.
    assert frame.loc[frame["time"].dt.hour == 4, "temp"].item() == pytest.approx(15.0)


def test_겨울_3시간_누적_강수를_세_시간에_나눈다(tmp_path):
    """11~3월은 3의 배수 시각에만 값이 있고 그것이 직전 3시간 합이다."""
    rows = hour_rows("2025-01-10", {6: (0.0, 3.0, 1.0, 60.0, "")}, hours=range(9))
    write_csv(tmp_path, "겨울.csv", rows)

    frame = weather.load_hourly(tmp_path)
    by_hour = frame.set_index(frame["time"].dt.hour)["rain"]
    assert by_hour[4] == pytest.approx(1.0)
    assert by_hour[5] == pytest.approx(1.0)
    assert by_hour[6] == pytest.approx(1.0)
    assert frame["rain"].sum() == pytest.approx(3.0)   # 총량은 그대로다


def test_여름_1시간_강수는_그대로_둔다(tmp_path):
    """4~10월은 시간 단위 기록이다. 나누면 있지도 않은 비가 앞 시각에 생긴다."""
    rows = hour_rows("2025-06-10", {6: (20.0, 3.0, 1.0, 60.0, "")}, hours=range(9))
    write_csv(tmp_path, "여름.csv", rows)

    frame = weather.load_hourly(tmp_path)
    by_hour = frame.set_index(frame["time"].dt.hour)["rain"]
    assert by_hour[6] == pytest.approx(3.0)
    assert by_hour[5] == 0.0


def test_겨울이어도_앞_시각에_값이_있으면_나누지_않는다(tmp_path):
    """11월 초처럼 시간 단위 구간이 섞여 있다 — 달만 보고 나누면 멀쩡한 값을 쪼갠다."""
    rows = hour_rows("2025-11-08", {
        4: (10.0, 0.5, 1.0, 60.0, ""),
        5: (10.0, 0.5, 1.0, 60.0, ""),
        6: (10.0, 3.0, 1.0, 60.0, ""),
    }, hours=range(9))
    write_csv(tmp_path, "십일월.csv", rows)

    frame = weather.load_hourly(tmp_path)
    by_hour = frame.set_index(frame["time"].dt.hour)["rain"]
    assert by_hour[6] == pytest.approx(3.0)
    assert by_hour[5] == pytest.approx(0.5)


def test_여러_파일을_이어_붙이고_중복은_지운다(tmp_path):
    """포털은 해가 바뀌면 파일을 나눠 준다. 겹치는 시각은 한 번만 남아야 한다."""
    write_csv(tmp_path, "2025.csv", hour_rows("2025-12-31", {}, hours=[22, 23]))
    write_csv(tmp_path, "2026.csv",
              hour_rows("2025-12-31", {23: (5.0, "", 1.0, 50.0, "")}, hours=[23])
              + hour_rows("2026-01-01", {}, hours=[0]))

    frame = weather.load_hourly(tmp_path)
    assert len(frame) == 3
    assert frame["time"].is_monotonic_increasing
    assert frame.loc[frame["time"] == pd.Timestamp("2025-12-31 23:00"), "temp"].item() == 5.0


def test_utf8_파일도_읽는다(tmp_path):
    write_csv(tmp_path, "utf8.csv", hour_rows("2025-06-01", {}, hours=[0]),
              encoding="utf-8-sig")
    assert len(weather.load_hourly(tmp_path)) == 1


def test_창을_접을_때_강수는_합_기온은_평균(tmp_path):
    rows = hour_rows("2025-06-02", {
        5: (10.0, 1.0, 1.0, 50.0, ""),
        6: (20.0, 2.0, 3.0, 50.0, ""),
    }, hours=range(5, 10))
    write_csv(tmp_path, "창.csv", rows)

    folded = weather.window_frame(weather.load_hourly(tmp_path), "_05_10")
    row = folded.iloc[0]
    assert row["date"] == "2025-06-02"
    assert row["rain"] == pytest.approx(3.0)          # 5시간 총량
    assert row["temp"] == pytest.approx((10 + 20 + 10 + 10 + 10) / 5)
    assert row["hours"] == 5


def test_자정을_넘긴_창은_시작한_날짜로_묶는다(tmp_path):
    """_20_05의 새벽 4시는 전날 밤과 같은 회차다."""
    rows = (hour_rows("2025-06-02", {20: (10.0, 1.0, 1.0, 50.0, "")}, hours=range(20, 24))
            + hour_rows("2025-06-03", {1: (10.0, 2.0, 1.0, 50.0, "")}, hours=range(0, 5)))
    write_csv(tmp_path, "야간.csv", rows)

    folded = weather.window_frame(weather.load_hourly(tmp_path), "_20_05")
    assert list(folded["date"]) == ["2025-06-02"]
    assert folded["rain"].item() == pytest.approx(3.0)
    assert folded["hours"].item() == 9                # 20~23시 + 0~4시


def test_적설은_더하지_않고_최대를_쓴다(tmp_path):
    """쌓인 높이라 더하면 다섯 배가 된다."""
    rows = hour_rows("2025-01-20", {
        5: (-2.0, "", 1.0, 60.0, 3.0),
        6: (-2.0, "", 1.0, 60.0, 5.0),
    }, hours=range(5, 10))
    write_csv(tmp_path, "눈.csv", rows)

    folded = weather.window_frame(weather.load_hourly(tmp_path), "_05_10")
    assert folded["snow"].item() == pytest.approx(5.0)


def test_빈_자료로_창을_접어도_죽지_않는다():
    folded = weather.window_frame(pd.DataFrame(columns=list(weather.COLUMNS)), "_05_10")
    assert folded.empty
    assert "rain" in folded.columns


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code
        self.encoding = None


API_ROW = ("202511030900 133  36  0.6  -9 -9.0   -9 1022.3 1030.8  2   1.9   5.5"
           "  -2.8  55.0   5.0   -9.0   -9.0   -9.0   -9.0   -9.0   -9.0   -9.0")


def test_API_응답에서_값을_꺼내고_결측은_None(monkeypatch):
    """-9는 값이 아니라 '없음'이다. 0으로 읽으면 비 안 온 날이 폭우가 된다."""
    monkeypatch.setenv(weather.API_KEY_ENV, "테스트키")
    monkeypatch.setattr(weather.requests, "get",
                        lambda *a, **k: FakeResponse("#헤더\n" + API_ROW + "\n"))

    observed = weather.fetch_hour("202511030900")
    assert observed["temp"] == pytest.approx(5.5)
    assert observed["humid"] == pytest.approx(55.0)
    assert observed["wind"] == pytest.approx(0.6)
    # -9는 숫자가 아니라 '없음'이라 None으로 온다. 그것이 무강수인지 자료없음인지는
    # 부르는 쪽이 계절·시각을 보고 판단한다(webapp/weather_view.py).
    assert observed["rain"] is None
    assert observed["time"] == pd.Timestamp("2025-11-03 09:00")


def test_키가_없으면_사람이_읽을_문구로_알린다(monkeypatch):
    monkeypatch.delenv(weather.API_KEY_ENV, raising=False)
    monkeypatch.setattr(weather, "_api_key",
                        lambda: (_ for _ in ()).throw(
                            weather.WeatherError(f"{weather.API_KEY_ENV}가 .env에 없습니다.")))
    with pytest.raises(weather.WeatherError, match="없습니다"):
        weather.fetch_hour()


def test_활용신청_안내가_오면_자료로_착각하지_않는다(monkeypatch):
    """403이 아니라 200에 JSON 알림으로 오는 경우가 있다."""
    monkeypatch.setenv(weather.API_KEY_ENV, "테스트키")
    body = '{ "result" : { "status" : 403, "message" : "활용신청이 필요한 API 입니다." } }'
    monkeypatch.setattr(weather.requests, "get", lambda *a, **k: FakeResponse(body))
    with pytest.raises(weather.WeatherError, match="알림"):
        weather.fetch_hour()


def test_긴_기간을_시각별_호출로_받지_않는다(monkeypatch):
    """한 시각씩 부르는 API다 — 몇 달치를 이걸로 받으면 호출이 수천 번이 된다."""
    monkeypatch.setenv(weather.API_KEY_ENV, "테스트키")
    with pytest.raises(weather.WeatherError, match="상한"):
        weather.fetch_range("2025-01-01 00:00", "2025-01-31 23:00")


FORECAST_BODY = (
    "#START7777\n"
    "# 헤더 설명\n"
    '11C20401 202608291100 202608291200 A02  0 133 2 xel********* 장민준     SW 1 W   30  70 DB04    1 "흐리고 가끔 비"\n'
    '11C20401 202608291100 202608300000 A02  1 133 2 xel********* 장민준     W  1 NW  23  80 DB04    0 "흐림"\n'
    "#7777END\n"
)


def test_예보문의_따옴표_구는_공백으로_안_깨진다(monkeypatch):
    """WF(예보문)가 `"흐리고 가끔 비"`처럼 따옴표 안에 공백을 품는다.

    2026-08-29에 단기육상예보(`fct_afs_dl.php`)를 활용신청받아 실제로 확인한
    형식이다 — 관측(`kma_sfctm2`)과 별개로 활용신청해야 한다.
    """
    monkeypatch.setenv(weather.API_KEY_ENV, "테스트키")
    monkeypatch.setattr(weather.requests, "get",
                        lambda *a, **k: FakeResponse(FORECAST_BODY))

    forecast = weather.fetch_forecast()
    assert len(forecast) == 2
    assert forecast.iloc[0]["text"] == "흐리고 가끔 비"
    assert forecast.iloc[0]["valid_at"] == pd.Timestamp("2026-08-29 12:00")
    assert forecast.iloc[0]["temp"] == 30
    assert forecast.iloc[0]["rain_prob"] == 70
    assert forecast.iloc[0]["rain_type"] == "1"
    # 강수유무코드 '0'(없음)도 그대로 남는다 — 판정은 부르는 쪽의 몫이다.
    assert forecast.iloc[1]["rain_type"] == "0"


def test_예보도_관측과_같은_방식으로_활용신청_안내를_구분한다(monkeypatch):
    monkeypatch.setenv(weather.API_KEY_ENV, "테스트키")
    body = '{ "result" : { "status" : 403, "message" : "활용신청이 필요한 API 입니다." } }'
    monkeypatch.setattr(weather.requests, "get", lambda *a, **k: FakeResponse(body))
    with pytest.raises(weather.WeatherError, match="알림"):
        weather.fetch_forecast()


def test_예보_자료가_없으면_빈_표를_돌려준다(monkeypatch):
    monkeypatch.setenv(weather.API_KEY_ENV, "테스트키")
    monkeypatch.setattr(weather.requests, "get",
                        lambda *a, **k: FakeResponse("#START7777\n#7777END\n"))
    forecast = weather.fetch_forecast()
    assert forecast.empty
    assert list(forecast.columns) == [
        "issued_at", "valid_at", "temp", "rain_prob", "sky_code", "rain_type", "text"]


# ---------------- 계획 화면의 '지금 날씨' (webapp/weather_view.py) ----------------
#
# 지키는 것:
#   - **화면을 그리는 요청에서 외부 API를 부르지 않는다** — 폼이 늦게 뜨면 안 된다.
#   - **API가 죽어도 화면은 산다** — 날씨는 있으면 좋고 없어도 되는 정보다.
#   - **-9를 '모름'으로 읽지 않는다** — 여름 내내 '모름'만 뜨게 된다.

from webapp import weather_view


def observation(**over) -> dict:
    base = {"time": pd.Timestamp("2026-07-05 05:00"), "stn": 133, "temp": 23.1,
            "wind": 0.0, "humid": 97.0, "rain": None, "rain_day": None, "snow": None}
    base.update(over)
    return base


def test_비가_오면_눈에_띄게_알리고_필요량을_말해_준다():
    described = weather_view.describe(observation(rain=9.4, rain_day=9.7))

    assert described["raining"] is True
    assert described["state"] == "raining"
    assert weather_view.RAINY_NEED_SHARE in described["note"]


def test_약한_비는_비_온_날로_치지_않는다():
    """문턱은 weather.RAIN_MM 하나다 — 학습·측정·화면이 같은 '비'를 가리켜야 한다."""
    described = weather_view.describe(observation(rain=0.2))
    assert described["raining"] is False


def test_여름의_결측은_무강수로_읽는다():
    """API는 비가 안 오면 -9를 준다(실측). 이걸 '모름'으로 읽으면 늘 모름만 뜬다."""
    described = weather_view.describe(observation())
    assert described["state"] == "dry"


def test_겨울의_3시간_사이_시각만_모른다고_말한다():
    """11~3월은 3시간마다만 강수가 온다 — 그 사이 시각의 -9는 진짜 모름이다."""
    between = weather_view.describe(observation(time=pd.Timestamp("2026-01-10 07:00")))
    on_mark = weather_view.describe(observation(time=pd.Timestamp("2026-01-10 06:00")))

    assert between["state"] == "unknown"
    assert on_mark["state"] == "dry"


def test_지금은_그쳤어도_오늘_비가_왔으면_알린다():
    described = weather_view.describe(observation(rain=None, rain_day=12.0))
    assert described["state"] == "rained_today" and described["raining"] is True


def test_API가_죽어도_화면은_산다(monkeypatch):
    weather_view.reset_cache()
    monkeypatch.setattr(weather_view.weather, "fetch_hour",
                        lambda *a, **k: (_ for _ in ()).throw(
                            weather.WeatherError("키가 없습니다")))

    described = weather_view.current()
    assert described["available"] is False and described["raining"] is False
    assert "키가 없습니다" in described["error"]


def test_화면을_여러_번_열어도_API는_한_번만_부른다(monkeypatch):
    """정시마다 갱신되는 값이라 10분 캐시로 충분하다."""
    weather_view.reset_cache()
    calls = []
    monkeypatch.setattr(weather_view.weather, "fetch_hour",
                        lambda *a, **k: calls.append(1) or observation(rain=5.0))

    weather_view.current()
    weather_view.current()
    assert len(calls) == 1

    weather_view.current(force=True)          # 새로고침은 다시 부른다
    assert len(calls) == 2
    weather_view.reset_cache()


def test_실행_화면은_외부_API를_부르지_않고_뜬다(monkeypatch):
    """폼을 그리는 요청 안에서 날씨를 부르면, API가 느릴 때 폼 자체가 늦게 뜬다."""
    from fastapi.testclient import TestClient

    from webapp.app import app

    called = []
    monkeypatch.setattr(weather_view.weather, "fetch_hour",
                        lambda *a, **k: called.append(1) or observation())

    page = TestClient(app).get("/run")
    assert page.status_code == 200
    assert 'id="weather"' in page.text        # 자리는 있고
    assert not called                          # 부르지는 않았다


def test_날씨_API는_실패해도_200으로_답한다(monkeypatch):
    """화면이 이걸로 죽으면 안 된다 — 실패도 available=false로 알린다."""
    from fastapi.testclient import TestClient

    from webapp.app import app

    weather_view.reset_cache()
    monkeypatch.setattr(weather_view.weather, "fetch_hour",
                        lambda *a, **k: (_ for _ in ()).throw(
                            weather.WeatherError("기상청 API 호출 실패: 500")))

    response = TestClient(app).get("/api/weather")
    assert response.status_code == 200
    assert response.json()["available"] is False
    weather_view.reset_cache()
