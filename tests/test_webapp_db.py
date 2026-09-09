"""웹 API의 DB 조회 검증 (DB_PLAN 3단계).

지금까지 웹 API는 "파일 수정시각이 가장 최근인 것"을 최신으로 삼았다.
이제 DB의 run_label로 조회하고 과거 실행분도 지정할 수 있어야 한다.

모든 테스트가 PBR_DB_PATH로 임시 DB를 가리켜 실제 data/bike_system.db를
건드리지 않는다.
"""
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import db
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


def test_station_feature_shape(client):
    props = client.get("/api/stations").json()["features"][0]["properties"]

    assert set(props) == {"station_id", "station_name", "rebal_qty", "type", "cluster", "stock"}
    assert props["type"] in ("pick", "drop")


def test_metrics_envelope_reports_source_and_label(client):
    body = client.get("/api/metrics").json()

    assert body["source"] == "db"
    assert body["run_label"] == NEW
    assert body["count"] == len(body["rows"]) == 6


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
