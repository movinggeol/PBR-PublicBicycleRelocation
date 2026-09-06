"""TMAP 엔드포인트 선택과 폴백 검증 (docs/구현/steps/step3_visualization.md).

실제 API는 일일 한도가 있는 유료 서비스라 자동 테스트에서 부르지 않는다.
대신 `requests.post`를 가로채 **어느 엔드포인트로 보냈는지**만 본다 —
검증하려는 것이 응답 내용이 아니라 선택·폴백 규칙이기 때문이다.

지키려는 규칙:
  1. 경유지가 30 이하면 routeSequential30을 먼저 쓴다(한도가 따로 잡혀 아낄 수 있다)
  2. 30을 넘으면 처음부터 routeSequential100 — 쪼개서 두 번 부르면 쿼터를 더 쓴다
  3. 30이 일일 한도를 소진하면 100으로 자동 전환하고, 그 뒤로는 30을 다시 부르지 않는다
  4. 둘 다 소진되면 TmapQuotaExceeded (호출한 쪽이 직선 경로로 대체한다)
"""
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "step3_map" / "module.py"


def load_module():
    """step3 모듈을 경로로 직접 읽는다.

    환경변수를 import 시점에 읽으므로 테스트마다 새로 로드해야 한다.
    """
    spec = importlib.util.spec_from_file_location("step3_module", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, status_code, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload or {"features": []}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


QUOTA_BODY = '{"error":{"code":"QUOTA_EXCEEDED","message":"Limit Exceeded"}}'


def fake_post(sent, quota_exceeded=()):
    """호출된 URL을 sent에 기록한다. quota_exceeded에 든 이름은 429로 응답."""
    def _post(url, **kwargs):
        sent.append(url)
        if any(name in url for name in quota_exceeded):
            return FakeResponse(429, QUOTA_BODY)
        return FakeResponse(200, payload={"features": [{"ok": url}]})
    return _post


def _args(via_count):
    point = {"name": "s", "X": 127.3, "Y": 36.3}
    via = [{"viaPointId": str(i), "viaPointName": str(i), "viaX": 127.3, "viaY": 36.3}
           for i in range(via_count)]
    return point, point, via


@pytest.mark.parametrize("via_count, expected", [
    (0, "routeSequential30"),
    (30, "routeSequential30"),
    (31, "routeSequential100"),
    (100, "routeSequential100"),
])
def test_smallest_endpoint_that_fits_is_chosen(via_count, expected):
    """한 번에 담기는 가장 작은 엔드포인트를 고른다."""
    module = load_module()
    assert module.pick_endpoint(via_count).name == expected


def test_falls_back_to_100_when_30_is_exhausted():
    """30이 일일 한도를 소진하면 같은 요청을 100으로 다시 보낸다."""
    module = load_module()
    sent = []
    module.requests.post = fake_post(sent, quota_exceeded=("routeSequential30",))

    result = module.call_tmap_sequential(*_args(5), headers={})

    assert len(sent) == 2, "30으로 한 번, 100으로 한 번이어야 한다"
    assert sent[0].endswith("routeSequential30")
    assert sent[1].endswith("routeSequential100")
    assert result["features"][0]["ok"].endswith("routeSequential100")


def test_exhausted_endpoint_is_not_retried():
    """한 번 소진된 엔드포인트는 다음 요청에서 건너뛴다 (하루가 지나야 풀린다)."""
    module = load_module()
    sent = []
    module.requests.post = fake_post(sent, quota_exceeded=("routeSequential30",))

    module.call_tmap_sequential(*_args(5), headers={})   # 여기서 30이 소진된다
    sent.clear()
    module.call_tmap_sequential(*_args(5), headers={})

    assert sent == [module.ENDPOINTS[1].url], "두 번째 요청은 100으로만 가야 한다"
    assert module.pick_endpoint(5).name == "routeSequential100"


def test_all_endpoints_exhausted_raises():
    """둘 다 소진되면 예외 — 호출한 쪽이 직선 경로로 대체한다."""
    module = load_module()
    sent = []
    module.requests.post = fake_post(sent, quota_exceeded=("routeSequential",))

    with pytest.raises(module.TmapQuotaExceeded):
        module.call_tmap_sequential(*_args(5), headers={})

    assert module.available_endpoints() == []


def test_fixed_url_does_not_fall_back(monkeypatch):
    """PBR_TMAP_URL로 엔드포인트를 지정하면 폴백하지 않는다(수동 검증용 탈출구)."""
    monkeypatch.setenv("PBR_TMAP_URL",
                       "https://apis.openapi.sk.com/tmap/routes/routeSequential30")
    module = load_module()
    sent = []
    module.requests.post = fake_post(sent, quota_exceeded=("routeSequential30",))

    with pytest.raises(module.TmapQuotaExceeded):
        module.call_tmap_sequential(*_args(5), headers={})

    assert len(sent) == 1, "지정한 엔드포인트로 한 번만 시도해야 한다"


def test_chunking_uses_the_chosen_endpoint_limit():
    """경유지 40개는 100으로 한 번에 간다 — 30으로 쪼개면 쿼터를 두 번 쓴다."""
    module = load_module()
    sent = []
    module.requests.post = fake_post(sent)

    module.call_tmap_chunked(*_args(40), headers={})

    assert len(sent) == 1
    assert sent[0].endswith("routeSequential100")


def test_reset_clears_exhausted_endpoints():
    """호출 수 초기화는 소진 표시도 함께 지운다(다음 실행을 오염시키지 않도록)."""
    module = load_module()
    sent = []
    module.requests.post = fake_post(sent, quota_exceeded=("routeSequential30",))
    module.call_tmap_sequential(*_args(5), headers={})

    module.reset_call_count()

    assert module.call_count() == 0
    assert module.pick_endpoint(5).name == "routeSequential30"


def test_budget_stops_calls_before_quota_is_spent(monkeypatch):
    """호출 예산을 다 쓰면 HTTP 요청 전에 멈춘다 (쿼터 소모 0)."""
    monkeypatch.setenv("PBR_TMAP_MAX_CALLS", "0")
    module = load_module()
    sent = []
    module.requests.post = fake_post(sent)

    with pytest.raises(module.TmapBudgetExceeded):
        module.call_tmap_sequential(*_args(5), headers={})

    assert sent == [], "예산 초과 시 요청을 보내면 안 된다"


# ---------------- 경로 지도의 팝업 (수정안 15) ----------------
#
# 지도에서 지점을 누르면 뜨는 창이다. 사람이 현장에서 읽는 글이므로 파이썬 자료구조가
# 그대로 새어 나오면 안 된다.

MAIN_PATH = Path(__file__).resolve().parents[1] / "step3_map" / "main.py"


def load_main():
    """main.py를 경로로 읽는다.

    스크립트로 돌 때는 제 폴더가 sys.path에 들어가 `import module`이 되지만,
    테스트에서 경로로 읽을 때는 우리가 넣어 줘야 한다.
    """
    import sys

    folder = str(MAIN_PATH.parent)
    if folder not in sys.path:
        sys.path.insert(0, folder)
    spec = importlib.util.spec_from_file_location("step3_main", MAIN_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record(order=1, round_=1, cluster=3, action="Drop", qty=5, load=2):
    return {"cluster": cluster, "order": order, "round": round_,
            "action": action, "qty": qty, "load": load}


def test_팝업에_대괄호가_새어_나오지_않는다():
    """한 번만 들른 대여소에서 리스트가 그대로 찍혔다 — 방문 대부분이 이 경우다."""
    html = load_main().visit_popup([record()], "ST0123", 10, lambda o: "00:12:30")

    assert "[" not in html and "]" not in html
    assert "'" not in html                      # 리스트를 찍으면 따옴표도 함께 나온다
    assert "ST0123" in html and "00:12:30" in html


def test_한_번만_들른_대여소에는_방문_회차를_적지_않는다():
    """'1번째 방문'은 뜻이 없는 줄이다 — 여러 번 들를 때만 필요하다."""
    once = load_main().visit_popup([record()], "ST0123", 10, lambda o: "-")
    assert "방문 회차" not in once


def test_여러_번_들르면_회차를_구분해_이어_붙인다():
    main = load_main()
    html = main.visit_popup([record(order=2, round_=1), record(order=7, round_=2)],
                            "ST0123", 10, lambda o: f"{o}분")

    assert main.VISIT_SEPARATOR in html
    assert "1번째 방문" in html and "2번째 방문" in html
    assert "[" not in html


# ─────────────── 커서 요약 (수정안 25번) ───────────────

def test_커서_요약은_ID가_아니라_이름을_보여준다():
    """예전 요약은 `클러스터 3 · ST0123 · 방문 5`였다 — 기사가 아는 것은 이름이다."""
    tip = load_main().visit_tooltip([record(order=5, action="Pick", qty=6)], "한밭수목원")

    assert "한밭수목원" in tip
    assert "ST0123" not in tip, "ID를 앞세우면 커서를 대도 어디인지 알 수 없다"


def test_커서_요약에_무엇을_몇_대_할지가_들어간다():
    """누르기 전에 알고 싶은 것은 '무엇을 몇 대'다."""
    tip = load_main().visit_tooltip([record(order=5, action="Pick", qty=6)], "한밭수목원")

    assert "5번째" in tip and "Pick" in tip and "6대" in tip


def test_두_번_들르면_요약도_두_줄이_된다():
    tip = load_main().visit_tooltip(
        [record(order=3, action="Drop", qty=4), record(order=9, action="Pick", qty=2)],
        "시청역")

    assert "3번째" in tip and "9번째" in tip
    assert tip.count("<br>") >= 2


def test_커서_요약은_팝업보다_짧다():
    """요약과 자세한 내용이 같으면 둘로 나눈 뜻이 없다.

    좌표·적재량·누적 시간처럼 따져 볼 것은 팝업에만 둔다.
    """
    main = load_main()
    recs = [record(order=5, action="Pick", qty=6)]
    tip = main.visit_tooltip(recs, "한밭수목원")
    popup = main.visit_popup(recs, "ST0123", 10, lambda o: "00:12:30")

    assert len(tip) < len(popup)
    assert "현재 적재량" not in tip and "누적 도착시간" not in tip


# ─────────── TMAP 실측 저장 (1.23.2) ───────────
# 이것이 없으면 VEHICLE_SPEED_KMPH가 맞는지 검증할 방법이 없다.
# 1.23.1에서 vrp_plan.cum_sec을 실측으로 착각해 틀린 결론을 냈다.

def _pts():
    return [{"id": "ST0001", "lat": 36.35, "lon": 127.30},
            {"id": "ST0010", "lat": 36.36, "lon": 127.35},
            {"id": "ST0020", "lat": 36.37, "lon": 127.40}]


def test_누적_소요를_구간별로_풀어_낸다():
    """TMAP은 누적 초를 준다. 앞 값과 빼야 그 구간의 실측이 된다."""
    rows = load_main()._road_legs(3, _pts(), [0, 300, 900])

    assert [r["road_sec"] for r in rows] == [300.0, 600.0]
    assert [r["leg"] for r in rows] == [0, 1]
    assert rows[0]["from_id"] == "ST0001" and rows[0]["to_id"] == "ST0010"


def test_직선거리를_함께_남긴다():
    """ILP는 계획 시점에 도로거리를 모르고 직선거리만 안다.

    배워야 할 것은 **직선거리 → 실제 도로 소요**의 관계이므로 둘이 같은 행에
    있어야 한다. 직선거리가 없으면 정답표로 쓸 수 없다.
    """
    rows = load_main()._road_legs(3, _pts(), [0, 300, 900])

    assert all(r["straight_km"] > 0 for r in rows)
    assert all(r["road_sec"] > 0 for r in rows)


def test_실측이_없으면_아무것도_남기지_않는다():
    """TMAP 한도에 걸리면 직선으로 낮춰 그린다 — 그때는 실측이 없다.

    추정치를 실측인 척 남기면 정답표가 오염된다. 그것이 1.23.1의 실패였다.
    """
    main = load_main()
    assert main._road_legs(3, _pts(), []) == []
    assert main._road_legs(3, _pts(), [None, None, None]) == []
    assert main._road_legs(3, _pts(), [0]) == []


def test_같은_자리를_두_번_들러도_0초_구간은_버린다():
    """누적이 그대로면 이동이 없었던 것이다 — 0km/h 표본이 되면 안 된다."""
    rows = load_main()._road_legs(3, _pts(), [0, 300, 300])
    assert [r["road_sec"] for r in rows] == [300.0]


def test_road_leg가_DB_스키마에_있다():
    """저장할 곳이 없으면 save_output이 조용히 경고만 남기고 넘어간다."""
    import db

    assert "road_leg" in db.TABLES
    assert "road_leg" in db.SCHEMA
    for column in ("straight_km", "road_sec", "from_id", "to_id"):
        assert column in db.SCHEMA


# ── 요청 파라미터 (1.26.5) ────────────────────────────────────────────
#
# 1.26.4까지 startTime이 "201709121938"(2017년 저녁)로 **고정**돼 있어서
# 새벽 회차에도 퇴근 러시아워 교통량이 적용됐다. 배율이 1.53배로 과대추정됐고,
# 고친 뒤 1.32배가 됐다. 같은 일이 다시 생기면 안 된다.

def _capture_payload(module, monkeypatch):
    """실제로 보낸 payload를 잡아 둔다."""
    box = {}

    def _post(url, json=None, **kwargs):
        box["url"] = url
        box["payload"] = json
        return FakeResponse(200)

    monkeypatch.setattr(module.requests, "post", _post)
    return box


def test_출동_시각이_회차마다_다르다(monkeypatch):
    """`startTime`은 **그 시각의 교통량**을 정한다 — 회차마다 달라야 한다.

    searchOption이 교통최적이므로 고정값을 쓰면 모든 회차가 같은 교통 상황으로
    계산된다. 실측에서 새벽 회차가 20.5% 과대추정되고 있었다.
    """
    module = load_module()
    box = _capture_payload(module, monkeypatch)
    start, end, via = _args(3)

    seen = {}
    for duration in ("_05_10", "_10_15", "_15_20"):
        module.call_tmap_sequential(start, end, via,
                                    start_time=module.start_time_for(duration))
        seen[duration] = box["payload"]["startTime"]

    assert len(set(seen.values())) == 3, f"회차가 같은 시각을 쓴다: {seen}"
    assert seen["_05_10"][8:10] == "05", "출동 시각이 창의 첫 시각이 아니다"
    assert seen["_10_15"][8:10] == "10"
    assert seen["_15_20"][8:10] == "15"


def test_출동_날짜는_평일이다():
    """주말은 교통량이 다르다 — 토·일이 나오면 안 된다."""
    from datetime import datetime

    module = load_module()
    for day in range(1, 29):            # 2월 한 달을 훑는다
        stamp = module.start_time_for("_10_15", datetime(2026, 2, day, 9, 0))
        when = datetime.strptime(stamp, "%Y%m%d%H%M")
        assert when.weekday() < 5, f"{stamp}는 주말이다"


def test_모르는_회차는_예전_기본값으로_물러선다():
    """형식이 다른 값이 와도 죽지 않는다 — 지도는 그려져야 한다."""
    module = load_module()
    assert module.start_time_for("이상한값") == module.FALLBACK_START_TIME
    assert module.start_time_for(None) == module.FALLBACK_START_TIME


def test_차종과_경로_옵션이_설정을_따른다(monkeypatch):
    """`carType=4`(대형화물차)는 근거 없는 값이었다.

    타슈 재배치 차량은 소형 트럭·밴이고, 재배치는 요금이 아니라 **시간**이
    목적이므로 searchOption은 최단시간(2)이다.
    """
    module = load_module()
    box = _capture_payload(module, monkeypatch)
    start, end, via = _args(3)

    module.call_tmap_sequential(start, end, via)

    assert box["payload"]["carType"] == "1", "승용차가 아니다"
    assert box["payload"]["searchOption"] == "2", "최단시간이 아니다"


def test_속도는_보내지_않는다(monkeypatch):
    """**TMAP에는 평균 속력 파라미터가 없다.**

    실측 배율이 우리 가정(25 km/h)에 오염되지 않았다는 것이 이 측정의 값어치다.
    누군가 속도를 넣으려 하면 그 성질이 깨지므로 여기서 막는다.
    """
    module = load_module()
    box = _capture_payload(module, monkeypatch)
    start, end, via = _args(3)

    module.call_tmap_sequential(start, end, via)

    keys = {k.lower() for k in box["payload"]}
    assert not any("speed" in k for k in keys), \
        f"속도를 보내고 있다: {box['payload'].keys()}"


# ── 출발·도착 핀의 이름 없는 마커 (axe aria-command-name, 1.26.126) ──────
#
# Leaflet은 마커에 키보드 접근성으로 role="button"을 자동으로 붙이는데,
# folium.Icon(출발·도착 핀)의 아이콘은 AwesomeMarkers가 <div>로 그려서
# Leaflet의 alt 옵션이 못 붙는다(<img>에서만 먹는다 — 실측, Marker(alt=...)를
# 줘도 DOM에 안 나타났다). 방문 순서 원(DivIcon)은 안에 숫자가 그대로 보여
# 이름이 있으므로 겪지 않는다. 이미 붙여 둔 tooltip 글을 aria-label로 옮기는
# 스크립트를 make_vrp_map()이 심어 두는지 여기서 본다 — 실제로 브라우저에서
# 읽히는지는 axe-core로 따로 확인했다(문서 참고).

def test_출발_도착_핀에_이름을_붙이는_스크립트가_심긴다(tmp_path):
    """awesome-marker 핀은 글자 없는 아이콘뿐이라 이름이 비어 있었다.

    tooltip 글을 그대로 aria-label로 옮기면 화면에 보이는 것과 다른 말을
    지어내지 않으면서 이름이 생긴다.
    """
    main = load_main()
    main.call_tmap_chunked = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("테스트에는 TMAP이 없다"))
    main.now = "pytest-map-test"
    main.result_path = str(tmp_path / "map{duration} ({now}).html")

    pick_drop = pd.DataFrame([
        {"station_id": "ST0001", "lat": 36.36, "lon": 127.35, "station_name": "테스트대여소"},
    ])
    vrp_plan = pd.DataFrame([
        {"cluster": 1, "from_id": "DEPOT", "from_lat": 36.35, "from_lon": 127.30,
         "to_id": "ST0001", "to_lat": 36.36, "to_lon": 127.35, "action": "pick", "qty": 3},
        {"cluster": 1, "from_id": "ST0001", "from_lat": 36.36, "from_lon": 127.35,
         "to_id": "DEPOT", "to_lat": 36.35, "to_lon": 127.30, "action": "return", "qty": 0},
    ])
    depot = {"id": "DEPOT", "name": "테스트 차고지", "lat": 36.35, "lon": 127.30}

    main.make_vrp_map(depot, pick_drop, vrp_plan, "_test",
                      {"appKey": "fake"}, "http://fake")

    saved = Path(main.result_path.format(duration="_test", now=main.now))
    html = saved.read_text(encoding="utf-8")

    assert "window.addEventListener('load'" in html, \
        "마커보다 앞서 실행되면 eachLayer가 undefined를 읽는다(실측) — load를 기다려야 한다"
    assert "role') !== 'button'" in html
    assert "getTooltip" in html, "화면에 없는 말을 짓지 않고 이미 붙은 풍선 글을 그대로 쓴다"
    assert "el.textContent.trim()" in html, \
        "방문 순서 원(DivIcon)은 이미 숫자가 보여 이름이 있다 — 덮어쓰면 안 된다"
