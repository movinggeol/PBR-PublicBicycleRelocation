"""현장 앱(`/m`) — 기사가 폰으로 드는 화면 (1.26.307).

관제 화면과 독립된 네 화면(오늘 · 경로 · 진행 · 내 정보)과 '내 차량' 저장을 지킨다.
체크 기록은 브라우저 localStorage에만 있어 여기서는 못 잰다 — 스크립트 동작은
브라우저로 확인했다(docs/기록/버전관리.md 1.26.307). 여기서는 서버가 넘기는 재료와
규약(색 별칭 층 · 토큰 한 벌 · 입구)을 본다.
"""
import json
import re
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import db
import project_config
from project_config import DEPOT_ID, DEPOT_LAT, DEPOT_LON
from webapp import mobile_view
from webapp.app import app

TEMPLATES = Path(__file__).resolve().parents[1] / "webapp" / "templates"
LABEL = "2026-09-30 현장"
DURATION = "_05_10"


# ─────────────────────────── 순수 계산 ───────────────────────────

def test_회차_시각은_코드에서_읽고_모르면_지어내지_않는다():
    assert mobile_view.duration_window("_05_10") == (5, 10)
    assert mobile_view.duration_window("_20_05") == (20, 5)
    assert mobile_view.duration_window("아무거나") is None
    assert mobile_view.duration_window(None) is None


def test_완료_예정은_올림이고_자정을_넘긴다():
    """계획값으로 완료를 앞당겨 약속하지 않는다 — 123.2분이면 07:04다."""
    assert mobile_view.clock(5, 123.2) == "07:04"
    assert mobile_view.clock(5) == "05:00"
    assert mobile_view.clock(20, 600) == "06:00"


def test_소요_시간_표기():
    assert mobile_view.hours_minutes(92.4) == "1시간 33분"
    assert mobile_view.hours_minutes(40) == "40분"
    assert mobile_view.hours_minutes(120) == "2시간"
    assert mobile_view.hours_minutes(None) == "—"
    assert mobile_view.hours_minutes(float("nan")) == "—"
    # 타일은 숫자·단위를 쪼개 쓴다 — 통째 글자가 360px에서 칸 밖으로 넘쳤다(실측).
    assert mobile_view.hours_minutes_parts(130) == [(2, "시간"), (10, "분")]
    assert mobile_view.hours_minutes_parts(120) == [(2, "시간")]
    assert mobile_view.hours_minutes_parts(0) == [(0, "분")]
    assert mobile_view.hours_minutes_parts(None) == []


def test_회차_단추는_짧은_이름을_쓴다():
    for duration, label in project_config.DURATION_LABELS.items():
        short = mobile_view.short_name(duration)
        assert short and short in label
    assert mobile_view.short_name("_99_99") == "_99_99"


def test_안내_주소는_좌표가_없으면_만들지_않는다():
    """빈 좌표로 주소를 만들면 엉뚱한 곳으로 안내된다(orders.py 좌표 규약과 같은 이유)."""
    url = mobile_view.nav_url({"name": "대전역, 동광장", "lat": 36.33, "lon": 127.43})
    assert url.startswith("https://map.kakao.com/link/to/")
    assert url.endswith(",36.33,127.43")
    assert "," not in url.split("/link/to/")[1].rsplit(",", 2)[0], "이름의 쉼표가 구분자와 섞였다"
    assert mobile_view.nav_url({"name": "x", "lat": None, "lon": 127.4}) is None
    assert mobile_view.nav_url({"name": "x", "lat": float("nan"), "lon": 127.4}) is None


def test_스크립트의_안내_주소와_같은_규칙이다():
    """서버(`nav_url`)는 첫 값, 스크립트(`navUrl`)는 체크를 따라 고친 값이다 — 한 규칙이어야 한다."""
    base = (TEMPLATES / "m_base.html").read_text(encoding="utf-8")
    assert "https://map.kakao.com/link/to/" in base
    assert 'replace(/,/g, " ")' in base, "스크립트가 이름의 쉼표를 바꾸지 않는다"
    assert "encodeURIComponent(name)" in base


def _sheet(stops):
    return {"stops": [{"station_id": s[0], "action": s[1], "qty": s[2]} for s in stops]}


def test_계획_지문은_정거장이_바뀌면_바뀐다():
    """같은 라벨로 다시 돌린 계획의 옛 체크가 새 계획에 붙지 않게 하는 열쇠다."""
    a = mobile_view.plan_fingerprint([_sheet([("ST1", "pick", 3)])], ["V01"])
    assert a == mobile_view.plan_fingerprint([_sheet([("ST1", "pick", 3)])], ["V01"])
    assert a != mobile_view.plan_fingerprint([_sheet([("ST2", "pick", 3)])], ["V01"])
    assert a != mobile_view.plan_fingerprint([_sheet([("ST1", "pick", 4)])], ["V01"])
    assert a != mobile_view.plan_fingerprint([_sheet([("ST1", "pick", 3)])], ["V02"])


def test_지도_스크립트는_folium과_같은_판이다():
    """새 종류의 의존이 아니라는 약속 — folium을 올려 판이 바뀌면 여기서 알린다."""
    from folium import folium as folium_module

    urls = " ".join(str(u) for _, u in getattr(folium_module, "_default_js", []))
    assert f"leaflet@{mobile_view.LEAFLET_VERSION}/" in urls, (
        f"folium의 Leaflet 판이 {mobile_view.LEAFLET_VERSION}이 아니다: {urls[:200]}")


def test_출처_표기는_지도_위가_아니라_아래_한_줄이다():
    """사용자 요청(2026-09-30): 지도 위 'Leaflet | © OpenStreetMap contributors'를 없앤다.
    OSM 타일은 출처 표기가 조건이라 지우지 않고 지도 아래로 옮긴다 — 문구는 MAP_TILES에서 푼 값."""
    base = (TEMPLATES / "m_base.html").read_text(encoding="utf-8")
    assert "attributionControl: false" in base, "지도 위 출처 칸이 다시 떴다"
    assert '"m-credit"' in base and "plan.tiles.attribution" in base, "지도 아래 출처 줄이 없다"


def test_지도_바탕은_MAP_TILES_하나를_folium_규칙으로_푼다():
    """pbr-pipeline 함정 4 — 세 지도와 같은 값. 템플릿에 타일 주소를 박지 않는다."""
    import folium

    tiles = mobile_view.tile_source()
    assert tiles["url"] == folium.TileLayer(project_config.MAP_TILES).tiles
    for path in TEMPLATES.glob("m_*.html"):
        text = path.read_text(encoding="utf-8")
        assert "tile.openstreetmap.org" not in text, f"{path.name}에 타일 주소가 박혔다"


# ─────────────────────────── 실데이터 모양의 표본 ───────────────────────────

# ─────────────────────────── 실도로 선 (1.26.308) ───────────────────────────
# 사용자 지적(2026-09-30): "길을 일직선으로 뚫고 다니게 하면 안 된다 — 실제 경로의 좌표대로".

def _backfill_tool():
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "tools" / "backfill_road_path.py"
    spec = importlib.util.spec_from_file_location("backfill_road_path_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_도로_선_한_줄은_조각을_잇고_이음매의_점을_한_번만_둔다():
    row = db.road_path_row(3, [[[36.1, 127.1], [36.2, 127.2]], [[36.2, 127.2], [36.3, 127.3]]])
    assert row["cluster"] == 3 and row["points"] == 3 and row["source"] == "tmap"
    assert json.loads(row["path"]) == [[36.1, 127.1], [36.2, 127.2], [36.3, 127.3]]


def test_경로_지도_HTML에서_군집별_실도로_선만_읽는다():
    """folium이 쓴 모양 그대로 읽는지 — folium을 올려 모양이 바뀌면 여기서 깨진다.
    점선은 TMAP을 못 받아 직선으로 낮춘 선이라 빼야 한다."""
    import folium

    m = folium.Map(location=[36.35, 127.38], zoom_start=12)
    road = folium.FeatureGroup(name="Cluster 3").add_to(m)
    folium.PolyLine([[36.30, 127.30], [36.31, 127.32]], color="#56B4E9").add_to(road)
    folium.PolyLine([[36.31, 127.32], [36.33, 127.35]], color="#56B4E9").add_to(road)
    straight = folium.FeatureGroup(name="Cluster 4").add_to(m)
    folium.PolyLine([[36.40, 127.40], [36.45, 127.45]], dash_array="8,6").add_to(straight)
    folium.LayerControl().add_to(m)

    paths = _backfill_tool().paths_from_html(m.get_root().render())
    assert set(paths) == {3}, "직선(점선) 군집이 실도로로 들어왔다"
    assert paths[3] == [[[36.30, 127.30], [36.31, 127.32]], [[36.31, 127.32], [36.33, 127.35]]]


def test_선이_방문_대여소를_비껴가면_다른_계획의_지도다():
    tool = _backfill_tool()
    segments = [[[36.300, 127.300], [36.300, 127.310]]]
    assert tool.far_stops(segments, [("A", 36.3005, 127.305)]) == []          # 약 55m
    assert tool.far_stops(segments, [("B", 36.310, 127.305)]) == ["B"]        # 약 1.1km


def test_폰에_보낼_선은_덜어도_모양과_끝점을_지킨다():
    """한 회차 17대면 저장된 점이 수만 개다 — 곧은 길의 중간 점은 덜고, 꺾이는 점은 남긴다."""
    straight = [[36.30 + i * 1e-4, 127.30] for i in range(51)]
    bent = straight + [[36.305, 127.30 + i * 1e-4] for i in range(1, 51)]
    kept = mobile_view._road_path(json.dumps(bent))
    assert len(kept) < 10, len(kept)
    assert kept[0] == (36.3, 127.3) and kept[-1] == (36.305, 127.305)
    assert (36.305, 127.3) in kept, "꺾이는 점을 덜었다"


def test_오늘_화면은_차고지_왕복을_잘라_낸다():
    path = [[36.40, 127.30], [36.35, 127.33], [36.33, 127.35], [36.32, 127.36], [36.40, 127.30]]
    work = [{"lat": 36.33, "lon": 127.35}, {"lat": 36.32, "lon": 127.36}]
    assert mobile_view.core_span(path, work) == [2, 3]
    assert mobile_view.core_span(None, work) is None


def test_실도로_선이_없으면_직선을_긋지_않는다():
    """🔴 대여소 좌표를 이어 선을 만들면 건물·강을 뚫는 경로가 된다 — 선은 `v.path`에서만."""
    base = (TEMPLATES / "m_base.html").read_text(encoding="utf-8")
    block = base[base.index("function drawMap"):base.index("return { plan: plan")]
    assert "v.path" in block
    assert "pts.push([s.lat, s.lon])" not in block, "대여소 좌표로 선을 잇고 있다"


def test_step3가_받은_도로_선을_DB에_남긴다():
    """step3는 TMAP을 불러야 돌아서 여기서 실행하지 않는다 — 저장 호출이 있는지만 본다."""
    src = (Path(__file__).resolve().parents[1] / "pipeline" / "step3_map" / "main.py").read_text(
        encoding="utf-8")
    assert "db.road_path_row(c, segments" in src
    assert 'db.save_output("road_path"' in src and "db.replace_road_paths(" in src
    assert "road_path" in db.TABLES


def _vrp():
    """차량 둘. V02는 수거 4·배송 3이라 `min(수거, 배송)`이 수거 합과 다르다."""
    rows = [
        # seq, 차량, 군집, 도착, 동작, 수량, 누적초
        (0, "V01", 0, "ST1001", "pick", 5, 600),
        (1, "V01", 0, "ST1002", "drop", 3, 1200),
        (2, "V01", 0, "ST1003", "drop", 2, 1800),
        (3, "V01", 0, DEPOT_ID, "return", 0, 2700),
        (4, "V02", 1, "ST1004", "pick", 4, 500),
        (5, "V02", 1, "ST1005", "drop", 3, 1100),
        (6, "V02", 1, DEPOT_ID, "return", 0, 7500),
    ]
    coords = {f"ST100{i}": (36.30 + i * 0.01, 127.35 + i * 0.01) for i in range(1, 6)}
    coords[DEPOT_ID] = (DEPOT_LAT, DEPOT_LON)
    frame = []
    for seq, vid, cl, to, action, qty, cum in rows:
        frame.append({
            "seq": seq, "vehicle_id": vid, "cluster": cl,
            "from_id": DEPOT_ID, "from_lat": DEPOT_LAT, "from_lon": DEPOT_LON,
            "to_id": to, "to_lat": coords[to][0], "to_lon": coords[to][1],
            "action": action, "qty": qty, "distance_km": 1.5,
            "travel_sec": 300.0, "work_sec": 150.0, "cum_sec": float(cum),
        })
    return pd.DataFrame(frame)


def _pick_drop():
    ids = [f"ST100{i}" for i in range(1, 6)]
    return pd.DataFrame({
        "station_id": ids,
        "station_name": ["대여소1", "대여소, 둘", "대여소3", "대여소4", "대여소5"],
        "lat": [36.31, 36.32, 36.33, 36.34, 36.35],
        "lon": [127.36, 127.37, 127.38, 127.39, 127.40],
        "parking_lot": [10, 10, 12, 8, 10],
        # 한 곳은 재고가 비었다 — 화면·JSON에 nan이 새면 안 된다.
        "stock": [9, 1, float("nan"), 7, 2],
        "target_qty": [5.0] * 5, "rebal_qty": [-4, 3, 2, -3, 3],
        "mu": [1.0] * 5, "sigma": [0.5] * 5, "cluster": [0, 0, 0, 1, 1],
    })


def _route_summary():
    return pd.DataFrame({
        "cluster": [0, 1], "visits": [3, 2], "bikes": [5, 4],
        "distance_km": [6.0, 4.5], "travel_min": [30.0, 110.0],
        "work_min": [15.0, 15.0], "total_min": [45.0, 125.0],
    })


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PBR_DB_PATH", str(tmp_path / "field.db"))
    with db.session() as conn:
        db.ensure_run(conn, LABEL, duration=DURATION, kind="plan")
        db.save_frame(conn, "vrp_plan", _vrp(), run_label=LABEL, duration=DURATION)
        db.save_frame(conn, "pick_drop", _pick_drop(), run_label=LABEL, duration=DURATION)
        db.save_frame(conn, "route_summary", _route_summary(), run_label=LABEL, duration=DURATION)
        db.save_kpi(conn, LABEL, DURATION, {
            "stations": 5, "clusters": 2, "vehicles_used": 2, "bikes_moved": 8,
            "stockout_hours_before": 1.85, "stockout_hours_after": 0.39,
            "total_distance_km": 10.5, "time_budget_met": 0.5, "time_budget_minutes": 120.0,
        })
        conn.commit()
    return TestClient(app)


def _plan(html: str) -> dict:
    raw = re.search(r'<script type="application/json" id="m-plan">(.*?)</script>', html, re.S)
    assert raw, "스크립트가 읽을 계획 한 벌이 없다"
    # 🔴 JSON의 NaN은 브라우저 JSON.parse가 거절한다 — 엄격하게 읽는다.
    return json.loads(raw.group(1), parse_constant=lambda c: pytest.fail(f"JSON에 {c}"))


def _leaks(html: str) -> list:
    body = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    return re.findall(r".{20}(?:\bNone\b|\bnan\b|\bNaN\b|Undefined).{10}", body)


def test_네_화면이_뜨고_값이_새지_않는다(client):
    for path in ("/m", "/m/route", "/m/progress", "/m/me",
                 "/m/route?vehicle=V02", "/m/route?vehicle=없는차", "/m?run_label=없음"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert not _leaks(r.text), f"{path}: {_leaks(r.text)[:2]}"


def test_빈_DB에서도_네_화면이_뜬다(tmp_path, monkeypatch):
    monkeypatch.setenv("PBR_DB_PATH", str(tmp_path / "empty.db"))
    c = TestClient(app)
    for path in ("/m", "/m/route", "/m/progress", "/m/me"):
        r = c.get(path)
        assert r.status_code == 200, path
        assert not _leaks(r.text), path
    assert 'href="/run"' in c.get("/m").text, "빈 상태가 계획을 세우러 갈 길을 말하지 않는다"


def test_계획_한_벌은_지시서와_같은_숫자다(client):
    plan = _plan(client.get("/m").text)
    assert plan["run_label"] == LABEL and plan["duration"] == DURATION
    assert plan["start"] == "05:00"
    v1, v2 = plan["vehicles"]
    assert (v1["id"], v2["id"]) == ("V01", "V02")
    # '옮길 자전거'는 `/orders`와 같은 값(실을 대수 합)이다.
    assert v2["bikes"] == 4
    # 🔴 완료율 분모는 min(수거, 배송) — 수거만 세면 싣고 못 내린 1대가 '옮겼다'가 된다.
    assert v2["moved_plan"] == 3
    assert plan["totals"]["moved_plan"] == 5 + 3
    # 체크의 단위는 방문지다 — 차고지 복귀는 세지 않는다.
    assert plan["totals"]["stops"] == 5
    # 이동·작업 분은 route_summary에서, 완료 예정은 올림.
    assert (v2["travel_min"], v2["work_min"]) == (110.0, 15.0)
    assert v2["finish"] == mobile_view.clock(5, v2["minutes"])
    assert plan["finish"] == mobile_view.clock(5, max(v1["minutes"], v2["minutes"]))
    # 경로 색은 기존 지도와 같은 군집 색이다.
    from mapviz import cluster_color
    assert v1["color"] == cluster_color(0) and v2["color"] == cluster_color(1)
    # 재고가 비었던 대여소는 None(JSON null)이다.
    stop3 = next(s for s in v1["stops"] if s["id"] == "ST1003")
    assert stop3["stock"] is None and stop3["lot"] == 12


def test_실도로_선이_있는_차량만_선을_받는다(client):
    path = [[DEPOT_LAT, DEPOT_LON], [36.34, 127.39], [36.35, 127.40], [DEPOT_LAT, DEPOT_LON]]
    db.replace_road_paths(pd.DataFrame([db.road_path_row(1, [path])]), LABEL, DURATION, [1])
    plan = _plan(client.get("/m").text)
    v1, v2 = plan["vehicles"]
    assert v1["path"] is None, "선이 없는 차량에 선을 지어냈다"
    assert v2["path"] and v2["path"][0] == [round(DEPOT_LAT, 5), round(DEPOT_LON, 5)]
    assert plan["totals"]["roads"] == 1


def test_내_차량이_없으면_회차_전체를_보인다(client):
    html = client.get("/m").text
    assert "출동 차량" in html and "회차 전체입니다" in html
    html = client.get("/m/route").text
    assert "어느 차량의 경로를 볼까요?" in html


def test_내_차량을_고르면_그_차로_열린다(client):
    client.cookies.set("pbr_field_vehicle", "V02", path="/m")
    html = client.get("/m").text
    assert re.search(r'class="m-btn small" href="/m/route">', html), "내 경로 안내 단추가 없다"
    # 내 차량이 목록 맨 위다.
    rows = re.findall(r'<li data-vehicle="([^"]+)">', html)
    assert rows[0] == "V02", rows
    html = client.get("/m/route").text
    assert "대여소4" in html and "대여소1" not in html, "경로 안내가 다른 차량을 그렸다"
    # 스크립트가 없어도 첫 대여소로 안내가 된다.
    assert 'href="https://map.kakao.com/link/to/' in html


def test_남의_경로를_볼_때는_밝힌다(client):
    client.cookies.set("pbr_field_vehicle", "V02", path="/m")
    html = client.get("/m/route?vehicle=V01").text
    assert "다른 차량을 보는 중" in html


def test_이_회차에_없는_차량은_비우지_않고_늘어놓는다(client):
    """`/orders`의 selected_vehicle과 같은 이유 — 조용히 버리면 '계획이 없다'로 읽힌다."""
    html = client.get("/m/route?vehicle=V09").text
    assert "V09" in html and "배정이 없습니다" in html
    assert 'data-vehicle="V01"' in html and 'data-vehicle="V02"' in html


def test_진행_현황은_계획_지표를_계획_지표라고_적는다(client):
    html = client.get("/m/progress").text
    assert "이 기기에서 체크한 기록으로 셉니다" in html
    assert "0.39<small>h</small>" in html and "78.9% 감소" in html   # kpi_view.stockout_cut_pct와 같은 값
    assert "50<small>%</small>" in html and "예산 120분" in html
    assert "오늘 현장에서 잰 값이 아닙니다" in html
    # 시안의 '총 이용 횟수·신고 건수'는 자료가 없어 싣지 않는다.
    assert "이용 횟수" not in html and "신고" not in html


def test_탭은_주소에_있던_실행만_싣고_다닌다(client):
    """base.html 3단 내비와 같은 규칙 — 고른 값을 늘 박으면 새 계획이 나와도 옛 계획에 묶인다."""
    html = client.get("/m").text
    nav = html[html.index('<nav class="m-tabs"'):]
    assert 'href="/m/route"' in nav and "run_label" not in nav[:nav.index("</nav>")]
    html = client.get(f"/m?run_label={LABEL}&duration={DURATION}").text
    nav = html[html.index('<nav class="m-tabs"'):]
    assert "/m/route?run_label=" in nav[:nav.index("</nav>")]


# ─────────────────────────── 내 차량 저장 ───────────────────────────

def test_내_차량은_쿠키로_현장_앱_안에만_둔다(client):
    r = client.post("/m/vehicle", data={"vehicle": "V07", "next": "/m"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/m"
    cookie = r.headers["set-cookie"]
    assert "pbr_field_vehicle=V07" in cookie and "Path=/m" in cookie


def test_내_차량을_비우면_지운다(client):
    r = client.post("/m/vehicle", data={"vehicle": "", "next": "/m/me"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/m/me?saved=1"
    assert 'pbr_field_vehicle=""' in r.headers["set-cookie"] or "Max-Age=0" in r.headers["set-cookie"]


def test_돌아갈_곳은_현장_앱_안으로만(client):
    for bad in ("https://evil.example", "//evil.example", "/maps", "/m/../run", "/mx"):
        r = client.post("/m/vehicle", data={"vehicle": "V07", "next": bad}, follow_redirects=False)
        assert r.headers["location"].startswith("/m/me"), bad


def test_차량_이름은_ASCII만_받는다(client):
    for bad in ("<script>", "군집 3", "V" * 25):
        r = client.post("/m/vehicle", data={"vehicle": bad}, follow_redirects=False)
        assert r.status_code == 400, bad
    # 손으로 고친 쿠키도 무시한다.
    client.cookies.set("pbr_field_vehicle", "<b>x</b>", path="/m")
    assert "&lt;b&gt;" not in client.get("/m").text


# ─────────────────────────── 입구 · 독립 · 규약 ───────────────────────────

def test_관제_화면_어디서나_현장_앱으로_갈_수_있다(client):
    for path in ("/", "/kpi", "/orders", "/guide"):
        html = client.get(path).text
        assert re.search(r'<a class="util" href="/m" data-field-app', html), path
        # 기존 '모바일' 미리보기 단추는 그대로다(덮어쓰지 않는다).
        assert 'id="view-toggle"' in html, path
    assert 'class="card link" href="/m" data-field-app' in client.get("/").text


def test_넓은_창의_입구는_기기_프레임으로_연다():
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    block = base[base.index('a[data-field-app]'):]
    block = block[:block.index("});\n    });")]
    assert "NARROW.matches" in block, "실제 폭이 아니라 선택값으로 판정하면 폰 안에 폰이 뜬다"
    assert '"/device?path="' in block


def test_기기_프레임이_현장_앱을_받는다(client):
    r = client.get("/device?path=/m")
    assert r.status_code == 200 and 'src="/m"' in r.text


def test_현장_앱은_관제_화면을_상속하지_않는다():
    for path in TEMPLATES.glob("m_*.html"):
        text = path.read_text(encoding="utf-8")
        assert '{% extends "base.html" %}' not in text, path.name


def test_관제_화면과_현장_앱의_색_토큰은_한_벌이다(client):
    """토큰 조각(`_tokens.html`)을 둘이 함께 읽는다 — 렌더된 `:root` 블록이 같아야 한다."""
    def root_block(html):
        i = html.index(":root {")
        return html[i:html.index("}", i) + 1]
    assert root_block(client.get("/").text) == root_block(client.get("/m").text)


def test_현장_앱의_규칙은_별칭_층만_읽는다():
    """2026-09-30 사용자 결정: 앱 색은 나중에 바꿀 수 있게 둔다.

    앱 규칙이 `--blue` 같은 관제 토큰이나 색 값을 직접 쓰면 `--m-*` 블록만 고쳐서는 앱 색이
    다 안 바뀐다. 별칭 블록 밖에서는 `var(--m-*)`·`var(--c)`(데이터 색)·`var(--r-*)`(모서리)만
    허용한다. 지도 표지의 인라인 색(`--c: #…`)은 경로·작업 **데이터**라 CSS 규칙이 아니다.
    """
    allowed = re.compile(r"^(m-|c$|c,|r-)")
    for path in sorted(TEMPLATES.glob("m_*.html")):
        text = path.read_text(encoding="utf-8")
        for style in re.findall(r"<style>(.*?)</style>|{% block style_extra %}(.*?){% endblock %}",
                                text, re.S):
            css = "".join(style)
            css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)       # 주석 속 설명의 색 값은 규칙이 아니다
            css = css.replace('{% include "_tokens.html" %}', "")
            # 별칭 블록 하나는 빼고 본다 — 관제 토큰을 가리키는 유일한 곳이다.
            css = re.sub(r":root \{\s*--m-accent:.*?\n    \}", "", css, flags=re.S)
            css = re.sub(r"@supports not \(background: color-mix\(in srgb, red 50%, transparent\)\)", "", css)
            for name in re.findall(r"var\(--([a-z0-9-]+)", css):
                assert allowed.match(name), f"{path.name}: 규칙이 --{name}을 직접 쓴다 — --m-* 별칭을 거쳐라"
            hexes = re.findall(r"(?<![\w-])#[0-9a-fA-F]{3,8}\b", css)
            assert not hexes, f"{path.name}: 규칙에 색 값이 박혔다 {hexes[:3]}"


def test_별칭은_모두_토큰을_가리킨다():
    base = (TEMPLATES / "m_base.html").read_text(encoding="utf-8")
    block = re.search(r":root \{\s*(--m-accent:.*?)\n    \}", base, re.S).group(1)
    aliases = dict(re.findall(r"--(m-[a-z0-9-]+):\s*([^;]+);", block))
    assert len(aliases) >= 20
    for name, value in aliases.items():
        assert value.startswith("var(--") or value.endswith("px"), f"--{name}: {value}"


# ─────────────── 작업 중 재판정 `/m/live` (1.26.333) ───────────────

@pytest.fixture
def live(monkeypatch):
    """타슈 API를 가짜로 — 부른 횟수를 센다. 60초 캐시는 테스트마다 비운다."""
    import tashu
    from webapp import app as webapp_app

    monkeypatch.setitem(webapp_app._field_live, "frame", None)
    monkeypatch.setitem(webapp_app._field_live, "at", 0.0)
    calls = []

    def fetch(timeout=30):
        calls.append(timeout)
        return pd.DataFrame({"station_id": ["ST1001", "ST1002", "ST1003"], "stock": [2, 1, 3]})

    monkeypatch.setattr(tashu, "fetch_stations", fetch)
    return calls


def _judge(client, **params):
    q = {"run_label": LABEL, "duration": DURATION, "vehicle": "V01", **params}
    return client.get("/m/live", params=q)


def test_경로_화면을_열기만_해서는_타슈_API를_부르지_않는다(client, live):
    html = client.get("/m/route?vehicle=V01").text
    assert live == [], "화면을 여는 것만으로 외부 API를 불렀다"
    assert 'id="live-check"' in html and "/orders/live?" in html, \
        "재판정 단추(스크립트가 없으면 관제 화면의 대조로 가는 링크)가 없다"


def test_재판정은_남은_곳만_적재를_이어_센다(client, live):
    res = _judge(client)
    assert res.status_code == 200
    data = res.json()
    by_no = {r["no"]: r for r in data["stops"]}
    # ST1001에 2대뿐 → 2대만 싣고, ST1002(3대)엔 2대, ST1003(2대)엔 0대.
    assert [by_no[n]["status"] for n in (1, 2, 3)] == ["부족", "부족", "불가"]
    assert data["summary"]["total"] == 3 and data["summary"]["blocked"] == 1

    # 1번을 끝냈다면 계획대로 5대를 실었다고 본다 — 뒤 두 곳은 그대로 된다.
    data = _judge(client, done="1").json()
    by_no = {r["no"]: r for r in data["stops"]}
    assert by_no[1]["status"] == "완료" and data["done"] == [1]
    assert by_no[2]["status"] == by_no[3]["status"] == "가능"


def test_기사_여럿이_눌러도_60초_안에는_한_번만_받는다(client, live):
    for _ in range(3):
        assert _judge(client).status_code == 200
    assert len(live) == 1, f"타슈 API를 {len(live)}번 불렀다"
    assert "checked_at" in _judge(client).json()


def test_계획이_바뀌었거나_차량이_없거나_API가_죽으면_말로_답한다(client, live, monkeypatch):
    assert _judge(client, fp="옛지문").status_code == 409
    assert _judge(client, vehicle="V99").status_code == 404
    assert live == [], "판정할 수 없는 요청에 API를 불렀다"

    import tashu

    def boom(timeout=30):
        raise tashu.TashuError("TASHU_API_KEY가 .env에 없습니다.")

    monkeypatch.setattr(tashu, "fetch_stations", boom)
    res = _judge(client)
    assert res.status_code == 502 and "TASHU_API_KEY" in res.json()["error"]
