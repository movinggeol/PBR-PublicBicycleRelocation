"""성과 지표(kpi_summary) 저장·조회와 /kpi 화면 검증 (docs/KPI.md).

목적은 흩어져 있던 지표를 실행 1건 = 1행으로 모아 실행 간 비교를 쉽게 하는 것이다.
"""
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import db
from webapp.app import app

OLD, NEW = "2026-01-01 09", "2026-05-21 18"


def _metrics(rate, **overrides):
    base = {
        "stations": 87, "clusters": 10, "vehicles_used": 10, "bikes_moved": 307,
        "avg_improvement_rate": rate, "pick_improvement_rate": rate - 0.15,
        "drop_improvement_rate": rate + 0.12, "target_met_ratio": 0.06,
        "total_distance_km": 304.9, "max_cluster_minutes": 110.6,
        "avg_cluster_minutes": 91.7, "time_budget_minutes": 120.0,
        "time_budget_met": 1.0, "vehicle_load_gap": 50.3,
        "improvement_per_km": 2.08, "cluster_max_imbalance": 6,
    }
    base.update(overrides)
    return base


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "kpi.db")
    db.init_schema(connection)
    yield connection
    connection.close()


def test_save_and_load(conn):
    db.save_kpi(conn, NEW, "_05_10", _metrics(0.65))
    rows = db.load_kpi(conn)

    assert len(rows) == 1
    assert rows["run_label"].iloc[0] == NEW
    assert rows["avg_improvement_rate"].iloc[0] == pytest.approx(0.65)
    assert rows["computed_at"].notna().all()


def test_save_is_idempotent(conn):
    """같은 실행·회차를 다시 저장하면 덮어쓴다(행이 늘지 않는다)."""
    db.save_kpi(conn, NEW, "_05_10", _metrics(0.65))
    db.save_kpi(conn, NEW, "_05_10", _metrics(0.71))

    rows = db.load_kpi(conn)
    assert len(rows) == 1
    assert rows["avg_improvement_rate"].iloc[0] == pytest.approx(0.71)


def test_partial_metrics_leave_nulls(conn):
    """계산하지 못한 지표는 빼고 넘기면 NULL로 남는다(4·5단계용 컬럼)."""
    db.save_kpi(conn, NEW, "_05_10", {"avg_improvement_rate": 0.65})

    row = db.load_kpi(conn).iloc[0]
    assert row["avg_improvement_rate"] == pytest.approx(0.65)
    assert pd.isna(row["stockout_hours_before"])
    assert pd.isna(row["demand_mae"])


def test_unknown_field_is_ignored(conn):
    """스키마에 없는 키를 넘겨도 깨지지 않는다."""
    db.save_kpi(conn, NEW, "_05_10", {"avg_improvement_rate": 0.65, "없는지표": 123})
    assert len(db.load_kpi(conn)) == 1


def test_filters(conn):
    db.save_kpi(conn, OLD, "_05_10", _metrics(0.60))
    db.save_kpi(conn, NEW, "_05_10", _metrics(0.65))
    db.save_kpi(conn, NEW, "_10_15", _metrics(0.71))

    assert len(db.load_kpi(conn)) == 3
    assert len(db.load_kpi(conn, run_label=NEW)) == 2
    assert len(db.load_kpi(conn, duration="_05_10")) == 2
    assert len(db.load_kpi(conn, run_label=NEW, duration="_10_15")) == 1


def test_latest_run_comes_first(conn):
    db.save_kpi(conn, OLD, "_05_10", _metrics(0.60))
    db.save_kpi(conn, NEW, "_05_10", _metrics(0.65))

    assert db.load_kpi(conn)["run_label"].tolist() == [NEW, OLD]


def test_compare_runs_with_sql(conn):
    """지표를 한 줄로 모은 목적: 실행 간 비교가 쿼리 하나가 된다."""
    db.save_kpi(conn, OLD, "_05_10", _metrics(0.60, total_distance_km=350.0))
    db.save_kpi(conn, NEW, "_05_10", _metrics(0.65, total_distance_km=304.9))

    compared = pd.read_sql(
        "SELECT run_label, avg_improvement_rate, total_distance_km"
        " FROM kpi_summary WHERE duration = '_05_10' ORDER BY run_label", conn)

    assert compared["avg_improvement_rate"].tolist() == pytest.approx([0.60, 0.65])
    assert compared["total_distance_km"].tolist() == pytest.approx([350.0, 304.9])


# ---------------- 웹 화면 ----------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PBR_DB_PATH", str(tmp_path / "kpi_api.db"))

    with db.session() as conn:
        db.record_run(conn, OLD, period="25년 10월", duration="_05_10")
        db.record_run(conn, NEW, period="25년 11월", duration="_05_10")
        db.save_kpi(conn, OLD, "_05_10", _metrics(0.60, target_met_ratio=0.03))
        db.save_kpi(conn, NEW, "_05_10", _metrics(0.65, target_met_ratio=0.06))

    with TestClient(app) as c:
        yield c


def test_kpi_page_renders(client):
    html = client.get("/kpi").text

    assert "성과 지표" in html
    assert "65%" in html                      # 최신 개선률
    assert "계획 달성률" in html               # 지표 성격 안내
    assert "목표 도달 비율" in html


def test_kpi_page_shows_delta(client):
    """직전 실행 대비 증감이 보인다 — 설정을 바꿔가며 비교하는 게 목적이다."""
    html = client.get("/kpi").text
    assert "▲" in html or "▼" in html


def test_kpi_api(client):
    body = client.get("/api/kpi").json()

    assert body["count"] == 2
    assert body["rows"][0]["run_label"] == NEW      # 최신순
    assert body["rows"][0]["avg_improvement_rate"] == pytest.approx(0.65)


def test_kpi_api_filters(client):
    body = client.get("/api/kpi", params={"run_label": OLD}).json()

    assert body["count"] == 1
    assert body["rows"][0]["avg_improvement_rate"] == pytest.approx(0.60)


def test_kpi_page_without_data(tmp_path, monkeypatch):
    """지표가 없어도 화면이 뜬다."""
    monkeypatch.setenv("PBR_DB_PATH", str(tmp_path / "empty.db"))
    with TestClient(app) as c:
        res = c.get("/kpi")
    assert res.status_code == 200
    assert "기록된 지표가 없습니다" in res.text
