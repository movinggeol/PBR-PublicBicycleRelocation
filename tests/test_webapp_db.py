"""웹 API의 DB 조회 검증 (DB_PLAN 3단계).

지금까지 웹 API는 "파일 수정시각이 가장 최근인 것"을 최신으로 삼았다.
이제 DB의 run_label로 조회하고 과거 실행분도 지정할 수 있어야 한다.

모든 테스트가 PBR_DB_PATH로 임시 DB를 가리켜 실제 data/bike_system.db를
건드리지 않는다.
"""
import re
from urllib.parse import quote

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import db
from project_config import DURATION_LABELS, DURATIONS
from webapp.app import app

OLD, NEW = "2026-01-01 09", "2026-05-21 18"
DURATION = "_05_10"


def _pick_drop(n, cluster=0):
    return pd.DataFrame({
        "station_id": [f"ST{i:04d}" for i in range(n)],
        "station_name": [f"대여소{i}" for i in range(n)],
        "lat": [36.30 + i * 0.01 for i in range(n)],
        "lon": [127.32 + i * 0.01 for i in range(n)],
        "parking_lot": [10] * n,
        "stock": [5] * n,
        "target_qty": [7.5] * n,
        "rebal_qty": [3 if i % 2 else -3 for i in range(n)],
        "mu": [1.0] * n,
        "sigma": [0.5] * n,
        "cluster": [cluster] * n,
    })


def _metrics(n, rate):
    return pd.DataFrame({
        "station_id": [f"ST{i:04d}" for i in range(n)],
        "station_name": [f"대여소{i}" for i in range(n)],
        "lat": [36.3] * n, "lon": [127.3] * n,
        "cluster": [0] * n, "stock": [5] * n,
        "mu": [1.0] * n, "sigma": [0.5] * n,
        "target_qty": [7.0] * n, "rebal_qty": [2] * n, "new_stock": [7] * n,
        "bf_imbalance": [2.0] * n, "af_imbalance": [0.0] * n,
        "improvement": [2.0] * n, "improvement_rate": [rate] * n,
    })


@pytest.fixture(autouse=True)
def isolate_csv_fallback(tmp_path, monkeypatch):
    """CSV 폴백이 실제 data/pp_data를 보지 않도록 빈 폴더를 가리킨다.

    사용자가 실데이터를 넣어 두면 '산출물이 없을 때 404'를 검사하는 테스트가
    폴백에서 진짜 파일을 찾아 200을 돌려주며 실패한다. 테스트는 사용자 데이터
    유무에 좌우되면 안 된다.
    """
    from webapp import catalog

    empty = tmp_path / "empty_pp"
    empty.mkdir(exist_ok=True)
    monkeypatch.setattr(catalog, "PP_ROOT", empty)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """임시 DB에 두 번의 실행분을 심고 API 클라이언트를 준다."""
    monkeypatch.setenv("PBR_DB_PATH", str(tmp_path / "api.db"))

    with db.session() as conn:
        db.record_run(conn, OLD, period="25년 10월", duration=DURATION)
        db.record_run(conn, NEW, period="25년 11월", duration=DURATION)
        db.save_frame(conn, "pick_drop", _pick_drop(4), run_label=OLD, duration=DURATION)
        db.save_frame(conn, "pick_drop", _pick_drop(6), run_label=NEW, duration=DURATION)
        db.save_frame(conn, "metrics", _metrics(4, 0.60), run_label=OLD, duration=DURATION)
        db.save_frame(conn, "metrics", _metrics(6, 0.85), run_label=NEW, duration=DURATION)

    with TestClient(app) as c:
        yield c


def test_stations_returns_latest_run(client):
    """라벨을 생략하면 최신 실행분(파일 수정시각이 아니라 run_label 기준)."""
    body = client.get("/api/stations").json()

    assert body["type"] == "FeatureCollection"
    assert body["source"] == "db"
    assert body["run_label"] == NEW
    assert len(body["features"]) == 6      # 최신 실행분의 대여소 수


def test_stations_can_select_past_run(client):
    """과거 실행분을 콕 집어 조회할 수 있다 — CSV 시절에는 불가능했던 기능."""
    body = client.get("/api/stations", params={"run_label": OLD}).json()

    assert body["run_label"] == OLD
    assert len(body["features"]) == 4


def test_좌표가_없는_대여소는_geometry_null로_낸다(tmp_path, monkeypatch):
    """NaN 좌표를 `float()`로 그대로 넣으면 JSON 직렬화(allow_nan=False)에서 500 (1.26.262).

    GeoJSON은 `geometry: null`을 허용한다 — 대여소를 빼 버리면 있는 것을 없다고
    하는 셈이라 남기되 자리만 비운다.
    """
    monkeypatch.setenv("PBR_DB_PATH", str(tmp_path / "nan.db"))
    frame = _pick_drop(3)
    frame.loc[1, ["lat", "lon"]] = None
    with db.session() as conn:
        db.record_run(conn, "좌표없음", period="25년 11월", duration=DURATION)
        db.save_frame(conn, "pick_drop", frame, run_label="좌표없음", duration=DURATION)

    with TestClient(app) as c:
        res = c.get("/api/stations", params={"run_label": "좌표없음"})
    assert res.status_code == 200
    features = res.json()["features"]
    assert len(features) == 3
    assert features[1]["geometry"] is None
    assert features[0]["geometry"]["type"] == "Point"



def test_이름이_없는_대여소도_stations_API가_산다(tmp_path, monkeypatch):
    """1.26.262가 좌표 NaN만 막았다 — 이름 NaN은 그대로 JSON으로 가 500 (1.26.271)."""
    monkeypatch.setenv("PBR_DB_PATH", str(tmp_path / "noname.db"))
    frame = _pick_drop(3)
    frame.loc[1, "station_name"] = None
    with db.session() as conn:
        db.record_run(conn, "이름없음", period="25년 11월", duration=DURATION)
        db.save_frame(conn, "pick_drop", frame, run_label="이름없음", duration=DURATION)

    with TestClient(app) as c:
        res = c.get("/api/stations", params={"run_label": "이름없음"})
    assert res.status_code == 200
    names = [f["properties"]["station_name"] for f in res.json()["features"]]
    assert names[1] == "" and names[0] == "대여소0"


def test_station_feature_shape(client):
    props = client.get("/api/stations").json()["features"][0]["properties"]

    assert set(props) == {"station_id", "station_name", "rebal_qty", "type", "cluster", "stock"}
    assert props["type"] in ("pick", "drop")


def test_metrics_envelope_reports_source_and_label(client):
    body = client.get("/api/metrics").json()

    assert body["source"] == "db"
    assert body["run_label"] == NEW
    assert body["count"] == len(body["rows"]) == 6


def test_저장된_실행_표의_바로_보기는_화면으로_보낸다(client):
    """사람이 보는 표가 `/api/metrics?…` JSON으로 보내고 있었다 (1.26.264)."""
    html = client.get("/run").text
    assert f"/kpi?run_label={NEW}" in html.replace("%20", " ") or "/kpi?run_label=" in html
    assert "/orders?run_label=" in html
    assert '/api/metrics?run_label=' not in html, "지표 링크가 아직 JSON으로 간다"


def test_metrics_past_run_differs(client):
    """실행별로 다른 결과가 나온다(비교의 기반)."""
    new_rows = client.get("/api/metrics").json()["rows"]
    old_rows = client.get("/api/metrics", params={"run_label": OLD}).json()["rows"]

    assert new_rows[0]["improvement_rate"] == pytest.approx(0.85)
    assert old_rows[0]["improvement_rate"] == pytest.approx(0.60)


def test_unknown_run_label_returns_404(client):
    res = client.get("/api/metrics", params={"run_label": "없는실행"})
    assert res.status_code == 404


def test_pipeline_runs_lists_history(client):
    body = client.get("/api/pipeline-runs").json()

    assert body["count"] == 2
    assert [r["run_label"] for r in body["rows"]] == [NEW, OLD]   # 최신순
    assert body["rows"][0]["period"] == "25년 11월"


def test_index_shows_run_history(client):
    """실행 이력이 대시보드에 보인다."""
    html = client.get("/run").text

    assert "저장된 실행" in html
    assert NEW in html


def test_missing_table_returns_404(client):
    """DB에도 CSV에도 없는 산출물은 404 (500이 아니다)."""
    assert client.get("/api/plans/vrp").status_code == 404
    assert client.get("/api/route-summary").status_code == 404


# ---------------- 차량 운용 (docs/구현/FLEET.md) ----------------

@pytest.fixture
def fleet_client(tmp_path, monkeypatch):
    """차량 배정 이력이 있는 상태의 클라이언트."""
    monkeypatch.setenv("PBR_DB_PATH", str(tmp_path / "fleet_api.db"))

    with db.session() as conn:
        db.ensure_fleet(conn)
        db.record_run(conn, NEW, period="25년 11월", duration=DURATION)
        # V03만 시간 예산(120분)을 넘기도록 둔다.
        for duration, rows in [("_05_10", [("V01", 60.0), ("V02", 80.0)]),
                               ("_10_15", [("V03", 150.0)])]:
            db.save_assignments(conn, NEW, duration, [{
                "vehicle_id": v, "cluster": i, "stations": 5,
                "bikes": 40, "distance_km": 20.0, "minutes": minutes,
            } for i, (v, minutes) in enumerate(rows)])

    with TestClient(app) as c:
        yield c


def test_vehicles_page_renders(fleet_client):
    html = fleet_client.get("/vehicles").text

    assert "차량 운용" in html
    assert "V01" in html
    assert "작업량 차이" in html


def test_vehicles_api_lists_whole_fleet(fleet_client):
    body = fleet_client.get("/api/vehicles").json()

    assert body["fleet_size"] == 21
    assert body["count"] == 21, "출동하지 않은 차량도 0으로 나와야 한다"
    worked = [r for r in body["rows"] if r["rounds"] > 0]
    assert {r["vehicle_id"] for r in worked} == {"V01", "V02", "V03"}


def test_vehicle_assignments_api_filters(fleet_client):
    everything = fleet_client.get("/api/vehicles/assignments").json()
    assert everything["count"] == 3

    one = fleet_client.get("/api/vehicles/assignments",
                           params={"vehicle_id": "V01"}).json()
    assert one["count"] == 1
    assert one["rows"][0]["vehicle_id"] == "V01"


def test_vehicles_page_without_data(client):
    """배정 이력이 없어도 화면이 뜬다(빈 상태 안내)."""
    res = client.get("/vehicles")
    assert res.status_code == 200
    assert "차량 운용" in res.text


def test_time_budget_flags_overrun(fleet_client):
    """시간 예산을 넘긴 작업이 화면에 표시된다."""
    html = fleet_client.get("/vehicles").text

    assert "시간 예산 준수" in html
    assert "over-budget" in html, "초과 행이 강조되지 않았다"
    # 3건 중 2건만 예산 내 → 67%
    assert "67%" in html
    assert "1건이 시간 예산을 넘었습니다" in html


def test_DB에_없으면_CSV로_물러서지_않는다(tmp_path, monkeypatch):
    """**DB가 정본이다 (1.26.165).** 옆에 CSV가 있어도 404다.

    예전에는 `run_label`도 `duration`도 없을 때 CSV 폴백이 돌았다. 고르는 방법이
    **파일 수정시각**이라 실험 산출물(`obs-cmp-…`)을 계획 자리에 내놓을 수
    있었는데, DB 경로는 그것을 막으려고 `kinds=("plan",)`를 쓴다(1.26.125) —
    폴백에는 그 장치가 없었다. 실측으로 `metrics`·`route_summary`·`ilp_plan`
    세 표에서 폴백이 고르는 파일이 실제로 `obs-cmp-1520`이었다.

    빈 DB + 읽을 수 있는 CSV를 함께 두어, **CSV가 있어도 안 읽는지** 본다.
    """
    monkeypatch.setenv("PBR_DB_PATH", str(tmp_path / "dur.db"))

    from webapp import catalog

    pp = tmp_path / "pp"
    (pp / "성능 지표").mkdir(parents=True)
    _metrics(4, 0.60).to_csv(pp / "성능 지표" / "verification_05_10 (라벨).csv",
                             index=False, encoding="utf-8")
    monkeypatch.setattr(catalog, "PP_ROOT", pp)

    with TestClient(app) as c:
        # 옛 폴백이라면 200 + source="csv"였을 자리다
        assert c.get("/api/metrics").status_code == 404
        # 시간대를 지정한 요청도 그대로 404
        assert c.get("/api/metrics", params={"duration": "없는시간대"}).status_code == 404
        assert c.get("/api/plans/vrp", params={"duration": "_10_15"}).status_code == 404


# ------------------------------------------- 실험이 계획 자리에 오면 안 된다

EXPERIMENT = "obs-cmp-1520"      # 실험 라벨. 사전순으로 어떤 날짜 라벨도 이긴다


def _record(conn, run_label: str, created_at: str, kind=None) -> None:
    conn.execute("INSERT INTO runs (run_label, duration, created_at, kind)"
                 " VALUES (?, ?, ?, ?)", (run_label, DURATION, created_at, kind))


def _two_runs(conn):
    """계획을 먼저, 실험을 **나중에** 돌린 상태. 시각순으로도 실험이 최신이다."""
    db.save_frame(conn, "pick_drop", _pick_drop(4), run_label=NEW, duration=DURATION)
    db.save_frame(conn, "pick_drop", _pick_drop(2), run_label=EXPERIMENT, duration=DURATION)
    _record(conn, NEW, "2026-08-27 23:13:43")            # kind 미선언 → 짐작
    _record(conn, EXPERIMENT, "2026-09-01 19:00:17")     # kind 미선언 → 짐작


def test_라벨을_안_주면_실험이_아니라_계획을_준다(tmp_path, monkeypatch):
    """실험도 같은 테이블에 쌓인다. 운영 화면이 그것을 계획으로 내놓으면,
    라벨 없이 연 `/orders`가 **실험을 현장 지시서로** 낸다(1.26.125에서 실측).

    ⚠️ 시각순 정렬만으로는 안 걸린다 — 실험이 정말로 더 나중에 돌았기 때문이다.
    """
    monkeypatch.setenv("PBR_DB_PATH", str(tmp_path / "kinds.db"))
    from webapp import store

    with db.session() as conn:
        _two_runs(conn)

    frame, source = store.load("pick_drop")

    assert source == "db"
    assert set(frame["run_label"]) == {NEW}, "실험을 계획으로 내놓았다"


def test_실험을_콕_집으면_그대로_보여_준다(tmp_path, monkeypatch):
    """거르는 것은 **기본값일 때**뿐이다. 실험 회차를 들여다보는 길을 막으면 안 된다."""
    monkeypatch.setenv("PBR_DB_PATH", str(tmp_path / "kinds2.db"))
    from webapp import store

    with db.session() as conn:
        _two_runs(conn)

    frame, _ = store.load("pick_drop", run_label=EXPERIMENT)

    assert set(frame["run_label"]) == {EXPERIMENT}


def test_계획이_하나도_없으면_실험이라도_준다(tmp_path, monkeypatch):
    """자료가 있는데 없다고 답하는 쪽이 더 나쁘다."""
    monkeypatch.setenv("PBR_DB_PATH", str(tmp_path / "kinds3.db"))
    from webapp import store

    with db.session() as conn:
        db.save_frame(conn, "pick_drop", _pick_drop(2),
                      run_label=EXPERIMENT, duration=DURATION)
        _record(conn, EXPERIMENT, "2026-09-01 19:00:17")

    frame, _ = store.load("pick_drop")

    assert set(frame["run_label"]) == {EXPERIMENT}


def test_작업지시서_목록은_계획을_앞에_두되_실험을_지우지_않는다(tmp_path, monkeypatch):
    """화면은 `targets[0]`을 기본값으로 쓴다 — 그 자리가 계획이어야 한다.

    그렇다고 실험을 목록에서 빼면 실험 회차의 지시서를 열어 볼 길이 막힌다.
    """
    monkeypatch.setenv("PBR_DB_PATH", str(tmp_path / "kinds4.db"))
    from webapp import store

    plan = pd.DataFrame({
        "cluster": [0], "from_id": ["ST0001"],
        "from_lat": [36.4], "from_lon": [127.3],
        "to_id": ["ST0002"], "to_lat": [36.3], "to_lon": [127.3],
        "action": ["pick"], "qty": [5],
        "distance_km": [1.0], "travel_sec": [120.0],
        "work_sec": [150.0], "cum_sec": [270.0],
    })
    with db.session() as conn:
        db.save_frame(conn, "vrp_plan", plan, run_label=NEW, duration=DURATION)
        db.save_frame(conn, "vrp_plan", plan, run_label=EXPERIMENT, duration=DURATION)
        _record(conn, NEW, "2026-08-27 23:13:43")
        _record(conn, EXPERIMENT, "2026-09-01 19:00:17")

    targets = store.plan_targets()

    assert targets.iloc[0]["run_label"] == NEW, "기본값이 실험이면 현장에 나간다"
    assert EXPERIMENT in set(targets["run_label"]), "실험을 목록에서 지우면 안 된다"
    # 저장된 kind가 없어도 짐작이 걸려야 한다 (NaN은 참이라 `or`로 쓰면 안 걸린다)
    assert targets.set_index("run_label").loc[EXPERIMENT, "kind"] == "experiment"


# ─────────────────────── 실행 목록과 고르기 (1.26.283) ───────────────────────
# 2026-09-23 실데이터의 모양을 줄여 옮긴다. **라벨 사전순과 기록 시각순이 거꾸로**이고,
# `brokenmix4-2603`은 종류가 실험으로 못박혀 있지만 라벨 짐작(`classify_run_label`)으로는
# '계획'이다 — 조인 결함이 드러나는 조합이 바로 이것이었다. 종류가 비어 있는 실행은
# 실데이터처럼 선언 없이 두고 짐작에 맡긴다(`sweep-*`→실험, `roadprobe-*`→수집).

PLAN_NEW = "2026-09-22 휴일 전회차"     # 가장 최근 계획 — 네 회차
PLAN_OLD = "2026-08-11 real"           # 옛 계획(선언 없음 → 짐작 '계획') — 세 회차
MIXED = "brokenmix4-2603"               # 실험으로 못박힘 · 라벨 짐작은 '계획' — 네 회차
SWEEP = "sweep-21"                      # 선언 없음 → 짐작 '실험' · 사전순 맨 위 — 세 회차
PROBE = "roadprobe-2026-09-02"          # 도로 수집 — 경로 없음

# (라벨, 선언한 종류, runs.created_at, 경로가 있는 회차)
RUNS = [
    (PLAN_NEW, "plan", "2026-09-22 15:06:39", DURATIONS),
    (MIXED, "experiment", "2026-09-14 16:26:17", DURATIONS),
    (PROBE, None, "2026-09-02 08:34:03", ()),
    (SWEEP, None, "2026-08-24 17:02:47", DURATIONS[:3]),
    (PLAN_OLD, None, "2026-08-12 10:17:05", DURATIONS[:3]),
]


def _route():
    """경로 한 줄짜리 `vrp_plan` — 위 시험(`plan`)과 같은 모양에 차량·순번을 더했다.

    지금 step3은 `vehicle_id`를 늘 적는다. 칸이 비면 `orders.build`의 `groupby`가 그
    행을 버려 지시서가 0장이 된다 — 실제로 낼 수 없는 상태라 채워 둔다.
    """
    return pd.DataFrame({
        "seq": [0], "vehicle_id": ["V01"],
        "cluster": [0], "from_id": ["ST0001"],
        "from_lat": [36.4], "from_lon": [127.3],
        "to_id": ["ST0002"], "to_lat": [36.3], "to_lon": [127.3],
        "action": ["pick"], "qty": [5],
        "distance_km": [1.0], "travel_sec": [120.0],
        "work_sec": [150.0], "cum_sec": [270.0],
    })


def _seed_runs(conn):
    """파이프라인처럼 회차마다 `ensure_run`을 부른다 — `runs.duration`에는 첫 회차만 남는다.

    `created_at`은 `ensure_run`이 지금 시각으로 박으므로 실데이터 순서로 고쳐 둔다.
    지표의 `computed_at`은 실행을 기록한 뒤 회차마다 1분씩 늦게 둔다(실측에서
    `kpi_summary.computed_at`은 `runs.created_at` 몇 분 뒤, 회차끼리는 초 단위다).
    """
    for label, kind, created_at, durations in RUNS:
        for d in (durations or DURATIONS[:1]):
            db.ensure_run(conn, label, duration=d, kind=kind)
        for i, d in enumerate(durations):
            db.save_frame(conn, "vrp_plan", _route(), run_label=label, duration=d)
            db.save_kpi(conn, label, d, {"stations": 10, "clusters": 1,
                                         "vehicles_used": 1, "bikes_moved": 5})
            conn.execute("UPDATE kpi_summary SET computed_at = ?"
                         " WHERE run_label = ? AND duration = ?",
                         (str(pd.Timestamp(created_at) + pd.Timedelta(minutes=1 + i)),
                          label, d))
        conn.execute("UPDATE runs SET created_at = ? WHERE run_label = ?",
                     (created_at, label))
    conn.commit()


@pytest.fixture
def runs_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PBR_DB_PATH", str(tmp_path / "runs.db"))
    monkeypatch.delenv("PBR_RUN_KIND", raising=False)
    with db.session() as conn:
        _seed_runs(conn)
    with TestClient(app) as c:
        yield c


def _between(html: str, start: str, end: str) -> str:
    i = html.index(start)
    return html[i:html.index(end, i + len(start))]


def test_회차가_여럿인_실험은_모든_회차가_실험으로_한데_묶인다(runs_client):
    """`plan_targets`가 `runs`를 (실행, **회차**)로 붙이고 있었다 (1.26.283).

    `runs`의 기본 키는 `run_label`이고 `duration`에는 첫 회차 하나만 남는다. 그래서 첫
    회차가 아닌 행은 `created_at`·`kind`가 비었고(실측 72행 중 46행), 라벨 짐작이 '계획'인
    `brokenmix4-2603`의 뒤 세 회차가 **계획으로** 목록 앞쪽에 끼고 최신 계획의 회차는
    0번과 뒤쪽으로 갈렸다. 실행으로만 붙이면 한 실행의 회차가 같은 종류·시각을 받는다.
    """
    from webapp import store

    targets = store.plan_targets()

    assert set(targets.loc[targets["run_label"] == MIXED, "kind"]) == {"experiment"}, \
        "실험으로 못박은 실행의 뒤 회차가 라벨 짐작으로 '계획'이 됐다"
    assert targets["created_at"].notna().all(), "첫 회차가 아닌 행의 기록 시각이 비었다"
    pairs = list(zip(targets["run_label"], targets["duration"]))
    expected = ([(PLAN_NEW, d) for d in DURATIONS] + [(PLAN_OLD, d) for d in DURATIONS[:3]]
                + [(MIXED, d) for d in DURATIONS] + [(SWEEP, d) for d in DURATIONS[:3]])
    assert pairs == expected, f"계획 먼저·최신순·회차는 하루 순서여야 한다: {pairs}"
    assert pairs[0] == (PLAN_NEW, DURATIONS[0]), "기본값(targets[0])은 최신 계획의 첫 회차다"


def test_회차_없이_연_실행은_그_실행의_첫_회차로_채운다(runs_client):
    """1.26.262의 폴백이 '목록에서 처음 나온 회차'를 골랐다 — 조인 결함으로 목록이 회차순이
    아니어서 `/orders?run_label=brokenmix4-2603`에 `_10_15`가 골라졌다(실측, 1.26.283)."""
    from webapp import app as app_module

    assert app_module._orders_context(MIXED, None)["duration"] == DURATIONS[0]
    assert app_module._orders_context(PLAN_NEW, None)["duration"] == DURATIONS[0]


def _work(vehicle: str, cluster: int = 0) -> dict:
    """배정 한 행 — `db.save_assignments`가 받는 모양."""
    return {"vehicle_id": vehicle, "cluster": cluster, "stations": 5,
            "bikes": 20, "distance_km": 10.0, "minutes": 60.0}


def test_하루_순서는_문자열이_아니라_설정의_DURATIONS를_따른다(runs_client, monkeypatch):
    """'첫 회차'·'늦은 회차'를 문자열 순서에 기대지 않는다 (1.26.283 검토).

    지금 네 창은 사전순과 하루 순서가 같아서, 지시서 기본값은 SQL의 `duration ASC`
    (문자열)에 기대고 폴백·실행 칩은 `DURATIONS`에 기대던 것 — **첫 회차의 정의가 둘** —
    을 어떤 시험도 못 잡았다. 폴백을 옛 코드로 되돌려도, '최근 작업'의 회차 순서를
    문자열로 바꿔도 전부 통과했다(검토의 변이 시험). 하루가 20시에 시작하도록
    `DURATIONS`를 돌려 두 순서를 갈라 놓고 본다. 회차 코드는 그대로라 계산이 실제로
    낼 수 있는 값이다.
    """
    from webapp import app as app_module
    from webapp import store

    rotated = DURATIONS[-1:] + DURATIONS[:-1]       # 20~05시가 하루의 처음
    assert sorted(rotated) != list(rotated), "두 순서가 갈려야 이 시험이 뜻이 있다"
    monkeypatch.setattr(store, "DURATIONS", rotated)

    targets = store.plan_targets()
    mine = list(targets.loc[targets["run_label"] == PLAN_NEW, "duration"])
    assert mine == list(rotated), f"한 실행 안의 회차가 설정의 하루 순서가 아니다: {mine}"

    # 기본값·라벨만 준 주소·실행 칩이 **같은** 첫 회차를 고른다
    assert app_module._orders_context(None, None)["duration"] == rotated[0], \
        "기본값이 문자열 순서의 첫 회차를 골랐다"
    assert app_module._orders_context(PLAN_NEW, None)["duration"] == rotated[0]
    plans = _between(runs_client.get("/orders").text, '<span class="label">계획</span>', "</div>")
    assert f"run_label={quote(PLAN_NEW)}&duration={rotated[0]}" in plans, \
        f"실행 칩이 다른 첫 회차를 넘긴다: {plans}"

    # '최근 작업'의 한 실행 안 늦은 회차도 설정을 따른다 — 문자열로는 `_20_05`가 가장 늦다
    with db.session() as conn:
        db.ensure_fleet(conn)
        for d in (rotated[0], rotated[-1]):
            db.save_assignments(conn, PLAN_NEW, d, [_work("V01")])
    row = store.vehicle_workload().set_index("vehicle_id").loc["V01"]
    assert (row["last_run"], row["last_duration"]) == (PLAN_NEW, rotated[-1]), \
        "한 실행 안의 '늦은 회차'를 문자열 순서로 골랐다"


def _add_old_plans(conn, count: int = 8) -> list:
    """쪽이 둘 이상 되게 옛 계획을 더 둔다(7월 초, 경로 없음) — 라벨 목록을 돌려준다."""
    labels = []
    for i in range(count):
        label = f"2026-07-0{i + 1} 옛계획"
        db.ensure_run(conn, label, duration=DURATIONS[0], kind="plan")
        conn.execute("UPDATE runs SET created_at = ? WHERE run_label = ?",
                     (f"2026-07-0{i + 1} 09:00:00", label))
        labels.append(label)
    conn.commit()
    return labels


def test_저장된_실행은_최신순_전부이고_회차를_모아_적는다(runs_client):
    """`/run` '저장된 실행'이 사전 역순 앞 10행뿐이었다 (1.26.283).

    실측 29개 중 앞 10행은 `sweep-*`·`roadprobe-*`·`brokenmix4-*`이고 계획은 0건 — 설명문이
    "종류를 여기서 고치라"는 표인데 계획 8건을 포함한 19건에는 닿을 길이 없었다. '시간대'
    칸은 `runs.duration`(첫 회차)만이라 네 회차 실행을 `_05_10`으로 적었다.
    전부 내려보내고 화면이 10행씩 쪽으로 끊는다. 종류로 묶지는 않는다(순수 최신순).

    회차 목록은 `store.saved_runs()` 한 벌이라 `/api/pipeline-runs`의 `durations`와 화면이
    같은 값이다(1.26.283 검토 — 처음에는 화면만 목록을 계산하고 API는 첫 회차 하나였다).
    """
    with db.session() as conn:
        _add_old_plans(conn)     # 예전에는 여기서 잘렸다

    body = runs_client.get("/api/pipeline-runs").json()
    labels = [r["run_label"] for r in body["rows"]]
    assert labels[:5] == [PLAN_NEW, MIXED, PROBE, SWEEP, PLAN_OLD], \
        f"기록 시각 내림차순이 아니다(사전 역순이면 sweep-21이 맨 위): {labels[:5]}"
    rounds = {r["run_label"]: r["durations"] for r in body["rows"]}
    assert rounds[PLAN_NEW] == list(DURATIONS), "경로가 있는 회차를 모두 하루 순서로"
    assert rounds[SWEEP] == list(DURATIONS[:3])
    assert rounds[PROBE] == [DURATIONS[0]], "경로가 없으면 runs.duration으로 물러선다"

    section = _between(runs_client.get("/run").text, 'id="saved-runs"', "<h2>실행 이력</h2>")
    assert 'data-pager="10"' in section, "저장된 실행 표에 쪽 넘기기가 없다"
    assert section.count("data-page-item=") == len(labels) == 13, \
        "서버가 앞 몇 행만 보내면 나머지 실행에는 닿을 길이 없다"
    for label in labels:
        assert f"<b>{label}</b>" in section, f"{label}이 표에 없다"
    # 네 회차 실행은 첫 회차 이름 + 나머지 수로 적는다
    assert f"{DURATION_LABELS[DURATIONS[0]]} 외 {len(DURATIONS) - 1}" in section
    # 경로가 없는 수집 실행은 `runs.duration`으로 물러선다
    probe_row = _between(section, f"<b>{PROBE}</b>", "</tr>")
    assert DURATION_LABELS[DURATIONS[0]] in probe_row and " 외 " not in probe_row
    # 화면의 시간대 칸은 API의 `durations`에서 그대로 나온다 — 행마다 대조한다
    for label, ds in rounds.items():
        cell = _between(_between(section, f"<b>{label}</b>", "</tr>"),
                        'data-label="시간대"', "</td>")
        expected = (f"{DURATION_LABELS[ds[0]]}" + (f" 외 {len(ds) - 1}" if len(ds) > 1 else "")
                    if ds else "—")
        assert cell.split(">", 1)[1].strip() == expected, f"{label}: 화면과 API가 갈렸다"


def test_종류를_바꾼_행이_든_쪽으로_돌아온다(runs_client):
    """'저장된 실행'을 10행씩 끊은 뒤로(1.26.283) 2쪽 이후 행의 종류를 바꾸면 되돌아온
    화면이 1쪽이라 방금 고친 행이 숨었다 — 검토에서 재현(쪽 상태 '1 / 2쪽', 그 행
    `is_visible()=False`). 이 표는 종류를 **여기서 고치라고** 둔 표다. 되돌아가는
    주소에 고친 실행을 싣고, 화면은 그 행에 `data-pager-focus`를 달아 쪽 넘기기가
    그 행이 든 쪽을 연다(쪽을 여는 것은 base.html 스크립트 — 브라우저로 확인).
    """
    with db.session() as conn:
        oldest = _add_old_plans(conn)[0]      # 13행 중 마지막 — 2쪽

    moved = runs_client.post(f"/runs/{quote(oldest)}/kind", data={"kind": "experiment"},
                             follow_redirects=False)
    assert moved.status_code == 303
    location = moved.headers["location"]
    assert location == f"/run?kind_changed={quote(oldest, safe='')}#saved-runs", location

    section = _between(runs_client.get(location.split("#")[0]).text,
                       'id="saved-runs"', "<h2>실행 이력</h2>")
    assert section.count("data-pager-focus") == 1, "표식은 고친 행 하나에만 단다"
    row = re.search(r'<tr data-page-item="(\d+)" data-pager-focus>\s*<td[^>]*><b>([^<]+)</b>',
                    section)
    assert row and row.group(2) == oldest, "표식이 고친 행이 아닌 곳에 붙었다"
    assert int(row.group(1)) >= 10, "시험이 2쪽 행을 고치지 않았다 — 뜻이 없다"
    assert 'value="experiment" selected' in _between(section, f"<b>{oldest}</b>", "</tr>"), \
        "종류가 저장되지 않았다"
    # 주소에 없는 라벨이면 아무 행에도 안 붙는다 — 예전처럼 1쪽이다
    plain = runs_client.get("/run", params={"kind_changed": "없는 실행"}).text
    assert "data-pager-focus" not in _between(plain, 'id="saved-runs"', "<h2>실행 이력</h2>")


def test_실행_칩은_계획_먼저_최신순이고_실험은_다음_줄에_남는다(runs_client):
    """`/kpi`·`/vehicles` 칩이 사전 역순이라 앞 12개가 실험이고 최신 계획은 14번째였다.
    폰에서 접히지도 않아 390px에서 칩만 716px였다 (1.26.283).

    계획 먼저·최신순으로 세우고, 실험은 **지우지 않고**(1.26.125) 다음 줄에 둔다. 두
    화면은 실험끼리 견주는 것이 본래 용도라 넓은 화면에서 실험을 접지는 않는다.
    """
    from webapp import store

    assert list(store.plan_runs()["run_label"]) == [PLAN_NEW, PLAN_OLD, MIXED, SWEEP], \
        "계획 먼저·최신순이 아니거나 수집 실행이 섞였다"

    for path in ("/kpi", "/vehicles"):
        html = runs_client.get(path).text
        picker = _between(html, '<details class="m-fold no-print" open>', "</details>")
        plans = _between(picker, '<span class="label">계획</span>', "</div>")
        others = _between(picker, '<span class="label">실험</span>', "</div>")
        assert plans.index(PLAN_NEW) < plans.index(PLAN_OLD), f"{path}: 계획이 최신순이 아니다"
        assert MIXED not in plans and SWEEP not in plans, f"{path}: 계획 줄에 실험이 섞였다"
        assert MIXED in others and SWEEP in others, f"{path}: 실험 칩이 사라졌다(1.26.125)"
        assert PROBE not in picker, f"{path}: 수집 실행이 칩으로 떴다(1.26.107)"
        # 줄마다 이름표가 맨 앞이다 — '[전체] 계획 …'이면 '계획'과 '실험'의 줄이 안 맞고
        # 좁은 화면에서 '전체 계획'이 한 말로 읽혔다(1.26.283 검토). '전체'는 제 줄에 둔다.
        for bar in picker.split('<div class="filterbar">')[1:]:
            assert bar.lstrip().startswith('<span class="label">'), \
                f"{path}: 이름표가 줄 맨 앞에 없다: {bar[:60]}"
        assert ">전체</a>" in _between(picker, '<span class="label">실행</span>', "</div>")
        assert ">전체</a>" not in plans, f"{path}: '전체'가 계획 줄에 섞였다"


def test_지표_표는_최근_실행이_위이고_한_실행_안은_하루_순서다(runs_client):
    """`/kpi` 표가 `db.load_kpi`의 사전 역순이라 `sweep-21`부터 시작했고, 최신 계획의 첫
    행은 72행 중 40번째였다(390px에서 약 9,300px 아래). 같은 화면의 히어로는 기록 시각으로
    '최신'을 골라 한 화면에 '최신'이 둘이었다 (1.26.283). `/api/kpi`도 같은 순서다."""
    rows = runs_client.get("/api/kpi").json()["rows"]
    pairs = [(r["run_label"], r["duration"]) for r in rows]
    expected = ([(PLAN_NEW, d) for d in DURATIONS] + [(MIXED, d) for d in DURATIONS]
                + [(SWEEP, d) for d in DURATIONS[:3]] + [(PLAN_OLD, d) for d in DURATIONS[:3]])
    assert pairs == expected, f"지표 행이 최근 실행 순서가 아니다: {pairs[:4]}"

    html = runs_client.get("/kpi").text
    table = _between(html, 'id="kpi-runs-table"', "</table>")
    assert 'id="kpi-runs-table" data-row-limit="12"' in html, "72행 표가 접히지 않는다"
    first_row = _between(table, "<tbody>", "</tr>")
    assert PLAN_NEW in first_row, "표 첫 행이 히어로의 '가장 최근 실행'과 다르다"


def test_같은_라벨을_다시_돌려도_최신은_한_기준이다(runs_client):
    """'최신'의 기준이 두 벌이었다 (1.26.283 검토).

    지표 표·`/api/kpi`·히어로·추세 그래프는 `kpi_summary.computed_at`, 실행 칩·지시서
    기본값·'최근 작업'은 `runs.created_at`이었다. 같은 라벨을 다시 돌리면 둘이 갈린다 —
    `ensure_run`은 `INSERT OR IGNORE`라 기록 시각이 처음 값으로 남고 `save_kpi`는 계산
    시각을 지금으로 덮는다. 웹 폼의 기본 라벨이 'YYYY-MM-DD HH'라 한 시간 안에 두 번
    돌리면 실제로 생기고, 그러면 `/kpi` 한 화면에서 표 첫 행과 첫 칩이 다른 실행이었다
    (검토 재현). 웹 전체가 `runs.created_at` 하나를 쓴다(`db.latest_label`과 같은 기준).
    """
    from webapp import app as app_module
    from webapp import kpi_view, store

    with db.session() as conn:
        db.ensure_fleet(conn)
        db.save_assignments(conn, PLAN_NEW, DURATIONS[0], [_work("V01")])
        # 옛 계획을 **같은 라벨로** 다시 돌린다 — 파이프라인이 부르는 순서 그대로
        for d in DURATIONS[:3]:
            db.ensure_run(conn, PLAN_OLD, duration=d)
            db.save_kpi(conn, PLAN_OLD, d, {"stations": 10, "clusters": 1,
                                            "vehicles_used": 1, "bikes_moved": 5})
        db.save_assignments(conn, PLAN_OLD, DURATIONS[0], [_work("V01")])
        at = dict(conn.execute("SELECT run_label, MAX(computed_at) FROM kpi_summary"
                               " GROUP BY run_label").fetchall())
    assert at[PLAN_OLD] > at[PLAN_NEW], "다시 돌린 옛 계획의 계산 시각이 더 늦어야 두 기준이 갈린다"

    rows = store.kpi()
    assert runs_client.get("/api/kpi").json()["rows"][0]["run_label"] == PLAN_NEW, \
        "지표 표가 계산 시각으로 '최신'을 골랐다 — 실행 칩과 다른 실행이다"
    assert app_module._kpi_labels_newest_first(rows)[0] == PLAN_NEW, "히어로의 '최신'이 갈렸다"
    assert kpi_view._runs_in_order(rows)[-1] == PLAN_NEW, "추세 그래프 맨 오른쪽이 갈렸다"
    assert store.plan_runs()["run_label"].iloc[0] == PLAN_NEW
    assert app_module._orders_context(None, None)["run_label"] == PLAN_NEW
    vehicles = {r["vehicle_id"]: r for r in runs_client.get("/api/vehicles").json()["rows"]}
    assert vehicles["V01"]["last_run"] == PLAN_NEW

    html = runs_client.get("/kpi").text
    first_row = _between(_between(html, 'id="kpi-runs-table"', "</table>"), "<tbody>", "</tr>")
    plans = _between(html, '<span class="label">계획</span>', "</div>")
    first_chip = re.search(r">([^<>]+)</a>", plans).group(1)
    assert PLAN_NEW in first_row and first_chip == PLAN_NEW, \
        f"한 화면에 '최신'이 둘이다: 표 첫 행 vs 첫 칩 {first_chip}"


def test_작업지시서는_실행을_고르고_그_실행의_회차를_고른다(runs_client):
    """계획 칩이 (실행, 회차)마다 하나라 실데이터에서 72개였다 (1.26.283).

    1400px에서 칩 묶음이 488px라 첫 지시서가 y=1011로 첫 화면 밖이었고, 같은 계획의
    회차가 흩어져 있었으며 칩과 지시서 머리에는 `_05_10` 같은 코드가 찍혔다.
    실행 → 회차 두 단으로 고르고, 실험은 **지우지 않고** 접는다(고른 것이 실험이면 편다).
    """
    html = runs_client.get("/orders").text
    picker = _between(html, '<details class="m-fold no-print" open>', "차량별 지시서")

    plans = _between(picker, '<span class="label">계획</span>', "</div>")
    plan_hrefs = re.findall(r'href="([^"]+)"', plans)
    assert len(plan_hrefs) == 2, f"계획 줄의 칩은 계획 실행 수(2)여야 한다: {plan_hrefs}"
    assert all("&duration=" + DURATIONS[0] in h for h in plan_hrefs), \
        f"실행 칩은 그 실행의 첫 회차를 늘 함께 넘긴다(1.26.262): {plan_hrefs}"
    assert all("vehicle=" not in h for h in plan_hrefs), "실행을 바꾸는데 차량을 들고 갔다"

    more = _between(picker, '<details class="run-more"', "</details>")
    assert more.startswith('<details class="run-more">'), "계획을 보는데 실험 묶음이 펴져 있다"
    assert MIXED in more and SWEEP in more, "실험을 목록에서 지우면 안 된다(1.26.125)"

    rounds = _between(picker, '<span class="label">회차</span>', "</div>")
    names = re.findall(r">([^<>]+)</a>", rounds)
    assert names == [DURATION_LABELS[d] for d in DURATIONS], f"회차는 사람 이름으로: {names}"
    assert "/orders/live" not in rounds, "회차를 바꾸는 클릭이 외부 API를 부르면 안 된다"
    # 회차 줄은 고른 실행이 선 줄 **바로 아래**다 — 늘 접은 실험 뒤에 두면, 실험을 폈을 때
    # 계획 칩과 그 회차 칩 사이에 실험 칩 18개가 끼었다(1.26.283 검토, 768px 실측).
    at = picker.index('<span class="label">회차</span>')
    assert picker.index('<span class="label">계획</span>') < at \
        < picker.index('<details class="run-more"'), "계획을 골랐는데 회차 줄이 실험 뒤에 섰다"
    assert '<span class="label">회차</span>' not in more
    # 지시서 머리도 코드가 아니라 이름이다 — 종이에 그대로 남는다
    assert f"{PLAN_NEW} · {DURATION_LABELS[DURATIONS[0]]}</span>" in html
    assert f"{PLAN_NEW} · {DURATIONS[0]}</span>" not in html

    # 실험을 고르면 실험 묶음이 펴져 있고 그 실행의 회차가 둘째 줄에 선다
    html = runs_client.get("/orders", params={"run_label": MIXED,
                                              "duration": DURATIONS[2]}).text
    picker = _between(html, '<details class="m-fold no-print" open>', "차량별 지시서")
    assert '<details class="run-more" open>' in picker, "고른 실험이 접힌 묶음 안에 숨었다"
    rounds = _between(picker, '<span class="label">회차</span>', "</div>")
    current = re.search(r'aria-current="true">([^<]+)</a>', rounds)
    assert current and current.group(1) == DURATION_LABELS[DURATIONS[2]]
    # 실험을 고르면 회차 줄은 접은 묶음 안, 실험 줄 바로 아래다
    more = _between(picker, '<details class="run-more"', "</details>")
    assert more.index('<span class="label">실험</span>') < more.index('<span class="label">회차</span>')
    assert picker.count('<span class="label">회차</span>') == 1, "회차 줄은 한 자리에만 선다"


def test_경로가_있는_수집_실행은_실험이라_부르지_않는다(runs_client):
    """접어 둔 묶음이 계획이 아닌 것 **전부**를 '실험' 줄에 세웠다 (1.26.283 검토).

    수집(`probe`)으로 못박은 실행에 경로가 있으면 요약이 '실험 3개'가 되고 그 칩이
    '실험' 이름표 줄에 섰다 — 판정하지 않은 것을 다른 이름으로 부른 셈이다(점검 기록
    4장). 실데이터에는 지금 0건이지만 종류는 사람이 화면에서 바꿀 수 있다(1.26.107).
    종류마다 줄을 나누고 이름은 `kind_labels` 한 벌을 쓴다. 지우지는 않는다(1.26.125).
    """
    from webapp.app import RUN_KIND_LABELS

    probe = "2026-09-10 수집표시"           # 라벨 짐작은 '계획' — 못박은 종류가 이긴다
    with db.session() as conn:
        db.ensure_run(conn, probe, duration=DURATIONS[0], kind="probe")
        db.save_frame(conn, "vrp_plan", _route(), run_label=probe, duration=DURATIONS[0])
        conn.execute("UPDATE runs SET created_at = ? WHERE run_label = ?",
                     ("2026-09-10 08:00:00", probe))
        conn.commit()

    exp_label = f'<span class="label">{RUN_KIND_LABELS["experiment"]}</span>'
    probe_label = f'<span class="label">{RUN_KIND_LABELS["probe"]}</span>'
    picker = _between(runs_client.get("/orders").text,
                      '<details class="m-fold no-print" open>', "차량별 지시서")
    more = _between(picker, '<details class="run-more"', "</details>")
    summary = _between(more, "<summary", "</summary>")
    assert f"{RUN_KIND_LABELS['experiment']} 2개" in summary, summary
    assert f"{RUN_KIND_LABELS['probe']} 1개" in summary, summary
    assert probe not in _between(more, exp_label, "</div>"), "수집 실행이 '실험' 줄에 섰다"
    assert probe in _between(more, probe_label, "</div>"), "수집 실행이 목록에서 사라졌다"
    assert probe not in _between(picker, '<span class="label">계획</span>', "</div>")

    # 고르면 묶음이 펴지고 회차 줄은 그 줄 바로 아래다
    picker = _between(runs_client.get("/orders", params={"run_label": probe}).text,
                      '<details class="m-fold no-print" open>', "차량별 지시서")
    more = _between(picker, '<details class="run-more"', "</details>")
    assert '<details class="run-more" open>' in picker
    after = more.split(probe_label, 1)[1].split("</div>", 1)[1]
    assert re.match(r'\s*<div class="filterbar">\s*<span class="label">회차</span>', after), \
        f"회차 줄이 고른 실행의 줄 바로 아래가 아니다: {after[:200]}"


# ───────────── /vehicles '최근 작업' — 기록에 있는 한 배정 (1.26.283) ─────────────

LATEST_PLAN = "2026-09-22 새계획"
OLD_EXPERIMENT = "zz-옛실험"          # 사전순으로 맨 위 — 예전 `MAX(run_label)`이 고르던 것
LATE_EXPERIMENT = "sweep-늦은실험"    # 계획보다 **나중에** 돈 실험


def test_최근_작업은_기록에_실제로_있는_한_배정이다(tmp_path, monkeypatch):
    """`db.vehicle_workload`가 실행과 회차를 **각각** 사전순 MAX로 뽑아 붙였다 (1.26.283).

    실측에서 21대가 전부 `sweep-21 _20_05`였는데 `sweep-21`에는 `_20_05` 회차가 없었다 —
    기록에 없는 조합을 '최근'이라 적은 것이다(점검 기록 4장 "지어내지 않는다"). 게다가
    사전순이라 실제 최신도 아니었다. `store.vehicle_workload`가 `runs.created_at` →
    라벨 → 하루 순서(`DURATIONS`)로 차량마다 한 행을 뽑아 덮는다. 종류로 거르지 않고
    (표 전체가 실험까지 합친 누적이다) 실험이면 `last_kind`로 밝힌다.
    """
    monkeypatch.setenv("PBR_DB_PATH", str(tmp_path / "latest.db"))
    early, late = DURATIONS[0], DURATIONS[2]

    def work(vehicle, cluster=0):
        return {"vehicle_id": vehicle, "cluster": cluster, "stations": 5,
                "bikes": 20, "distance_km": 10.0, "minutes": 60.0}

    with db.session() as conn:
        db.ensure_fleet(conn)
        for label, kind, created_at in [(OLD_EXPERIMENT, "experiment", "2026-08-01 10:00:00"),
                                        (LATEST_PLAN, "plan", "2026-09-22 15:06:39"),
                                        (LATE_EXPERIMENT, "experiment", "2026-09-23 09:00:00")]:
            db.ensure_run(conn, label, kind=kind)
            conn.execute("UPDATE runs SET created_at = ? WHERE run_label = ?", (created_at, label))
        # V01: 옛 실험 _10_15 · 새 계획 _05_10과 _15_20 → 예전 값은 (zz-옛실험, _15_20) — 없는 조합
        db.save_assignments(conn, OLD_EXPERIMENT, DURATIONS[1], [work("V01"), work("V02", 1)])
        db.save_assignments(conn, LATEST_PLAN, early, [work("V01")])
        db.save_assignments(conn, LATEST_PLAN, late, [work("V01"), work("V04", 1)])
        # V04: 계획 뒤에 돈 실험에도 나갔다 — 가장 최근은 그 실험이다
        db.save_assignments(conn, LATE_EXPERIMENT, early, [work("V04")])

    with TestClient(app) as c:
        rows = {r["vehicle_id"]: r for r in c.get("/api/vehicles").json()["rows"]}
        history = c.get("/api/vehicles/assignments").json()["rows"]
        html = c.get("/vehicles").text

    real = {(h["vehicle_id"], h["run_label"], h["duration"]) for h in history}
    for vehicle in ("V01", "V02", "V04"):
        pick = (vehicle, rows[vehicle]["last_run"], rows[vehicle]["last_duration"])
        assert pick in real, f"기록에 없는 조합을 최근 작업이라 적었다: {pick}"

    assert (rows["V01"]["last_run"], rows["V01"]["last_duration"]) == (LATEST_PLAN, late), \
        "사전순 MAX가 아니라 기록 시각 → 하루의 늦은 회차로 골라야 한다"
    assert rows["V01"]["last_kind"] == "plan"
    assert (rows["V02"]["last_run"], rows["V02"]["last_duration"]) == (OLD_EXPERIMENT, DURATIONS[1])
    assert (rows["V04"]["last_run"], rows["V04"]["last_kind"]) == (LATE_EXPERIMENT, "experiment"), \
        "종류로 거르지 않는다 — 표는 실험까지 합친 누적이다"
    assert rows["V03"]["last_run"] is None and rows["V03"]["last_duration"] is None, \
        "한 번도 안 나간 차량에 최근 작업을 지어냈다"

    # 화면은 같은 값을 그 차량의 그 회차 지시서로 잇고, 회차를 사람 이름으로 적는다.
    # **차량마다** 화면 칸을 API 값에 대 본다 — 처음에는 V01 하나만 보고, 마지막 단언은
    # 옛 공백 꼴(`zz-옛실험 _15_20`)이라 템플릿이 바뀐 뒤로는 늘 참이었다(1.26.283 검토).
    table = _between(html, 'id="workload-table"', "</table>")
    cells = dict(re.findall(r'<td data-label=""><b>([^<]+)</b></td>.*?'
                            r'<td class="muted" data-label="최근 작업">(.*?)</td>',
                            table, flags=re.S))
    assert set(cells) == set(rows), "표의 차량과 API의 차량이 다르다"
    from webapp.app import RUN_KIND_LABELS as kind_names
    for vehicle, row in rows.items():
        cell = cells[vehicle]
        if row["last_run"] is None:
            assert cell.strip() == "—", f"{vehicle}: 출동한 적 없는데 최근 작업을 적었다: {cell}"
            continue
        href = (f"/orders?run_label={quote(row['last_run'])}"
                f"&duration={row['last_duration']}&vehicle={vehicle}")
        text = f"{row['last_run']} · {DURATION_LABELS[row['last_duration']]}"
        assert f'href="{href}"' in cell and f">{text}</a>" in cell, \
            f"{vehicle}: 화면 칸이 API 값({row['last_run']}, {row['last_duration']})과 다르다: {cell}"
        if row["last_kind"] != "plan":
            assert kind_names[row["last_kind"]] in cell, f"{vehicle}: 실험임을 밝히지 않았다"
    # 예전의 사전순 MAX 조합(V01 = 옛 실험 + 가장 늦은 회차 코드)이 새 꼴로도 없어야 한다
    assert f"{OLD_EXPERIMENT} · {DURATION_LABELS[late]}" not in html, "예전의 사전순 MAX 조합이 남았다"

    # 아래 배정 이력 표의 회차도 같은 이름이다 — 한 화면에서 코드와 이름이 섞였다
    for h in history:
        assert (f'data-label="회차" data-sort="{h["duration"]}">'
                f'{DURATION_LABELS[h["duration"]]}</td>') in html, \
            f"배정 이력의 회차가 사람 이름이 아니다: {h['duration']}"
