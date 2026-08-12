"""SQLite 저장소(db.py) 테스트.

핵심 관심사는 세 가지다.
1) 파일명에 있던 라벨이 run_label 컬럼으로 제대로 옮겨지는가
2) 같은 실행을 다시 저장해도 중복이 쌓이지 않는가(멱등성)
3) 라벨을 생략하면 최신 실행을 돌려주는가 (웹 API의 "수정시각 최신 파일" 휴리스틱 대체)

실데이터를 건드리지 않도록 모든 테스트가 tmp_path의 별도 DB 파일을 쓴다.
"""
import pandas as pd
import pytest

import db


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "test.db")
    db.init_schema(connection)
    yield connection
    connection.close()


def _pick_drop(n=3, cluster=0):
    return pd.DataFrame({
        "station_id": [f"ST{i:04d}" for i in range(n)],
        "station_name": [f"대여소{i}" for i in range(n)],
        "lat": [36.3 + i * 0.01 for i in range(n)],
        "lon": [127.3 + i * 0.01 for i in range(n)],
        "parking_lot": [10] * n,
        "stock": [5] * n,
        "target_qty": [7.5] * n,
        "rebal_qty": [3, -3, 4][:n],
        "mu": [1.0] * n,
        "sigma": [0.5] * n,
        "cluster": [cluster] * n,
    })


def test_schema_creates_expected_tables(conn):
    names = {row[0] for row in
             conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"runs", "station_info", "net_demand", "pick_drop",
            "ilp_plan", "vrp_plan", "metrics", "route_summary",
            "rental_history"} <= names


def test_wal_mode_enabled(conn):
    """파이프라인이 쓰는 중에도 대시보드가 읽을 수 있어야 한다."""
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_save_adds_scope_columns(conn):
    """파일명 라벨이 컬럼으로 들어간다."""
    db.save_frame(conn, "pick_drop", _pick_drop(), run_label="R1", duration="_05_10")
    out = db.load_frame(conn, "pick_drop", run_label="R1")

    assert set(out["run_label"]) == {"R1"}
    assert set(out["duration"]) == {"_05_10"}
    assert len(out) == 3


def test_save_is_idempotent(conn):
    """같은 실행을 두 번 저장해도 행이 늘지 않는다."""
    for _ in range(2):
        db.save_frame(conn, "pick_drop", _pick_drop(), run_label="R1", duration="_05_10")

    assert len(db.load_frame(conn, "pick_drop", run_label="R1")) == 3


def test_runs_are_isolated(conn):
    """실행이 다르면 서로 덮어쓰지 않는다."""
    db.save_frame(conn, "pick_drop", _pick_drop(n=3), run_label="R1", duration="_05_10")
    db.save_frame(conn, "pick_drop", _pick_drop(n=2), run_label="R2", duration="_05_10")

    assert len(db.load_frame(conn, "pick_drop", run_label="R1")) == 3
    assert len(db.load_frame(conn, "pick_drop", run_label="R2")) == 2


def test_durations_are_isolated(conn):
    """같은 실행 안에서 시간대별로 분리 저장된다."""
    db.save_frame(conn, "pick_drop", _pick_drop(n=3), run_label="R1", duration="_05_10")
    db.save_frame(conn, "pick_drop", _pick_drop(n=2), run_label="R1", duration="_10_15")

    assert len(db.load_frame(conn, "pick_drop", run_label="R1", duration="_05_10")) == 3
    assert len(db.load_frame(conn, "pick_drop", run_label="R1", duration="_10_15")) == 2
    assert len(db.load_frame(conn, "pick_drop", run_label="R1")) == 5


def test_load_without_label_returns_latest(conn):
    """라벨을 생략하면 최신 실행분을 준다(파일 수정시각 휴리스틱 대체)."""
    db.save_frame(conn, "pick_drop", _pick_drop(n=3), run_label="2026-01-01 09", duration="_05_10")
    db.save_frame(conn, "pick_drop", _pick_drop(n=2), run_label="2026-05-21 18", duration="_05_10")

    assert db.latest_label(conn, "pick_drop") == "2026-05-21 18"
    assert set(db.load_frame(conn, "pick_drop")["run_label"]) == {"2026-05-21 18"}


def test_load_empty_table_returns_empty_frame(conn):
    assert db.load_frame(conn, "pick_drop").empty


def test_korean_columns_are_renamed(conn):
    """CSV의 한글 컬럼이 ASCII 컬럼으로 저장된다."""
    summary = pd.DataFrame({
        "cluster": [0, 1],
        "방문수": [6, 7],
        "처리대수": [38, 42],
        "총이동거리_km": [28.1, 35.11],
        "총이동시간_분": [56.2, 70.2],
        "총작업시간_분": [19.0, 21.0],
        "총소요시간_분": [75.2, 91.2],
    })
    db.save_frame(conn, "route_summary", summary, run_label="R1", duration="_05_10")

    out = db.load_frame(conn, "route_summary", run_label="R1")
    assert {"visits", "bikes", "distance_km", "travel_min", "work_min", "total_min"} <= set(out.columns)
    assert out["distance_km"].sum() == pytest.approx(63.21)


def test_ilp_plan_drops_redundant_hour_column(conn):
    """'hour'는 duration과 같은 값이라 저장하지 않는다."""
    plan = pd.DataFrame({
        "hour": ["_05_10"] * 2,
        "cluster": [0, 0],
        "pick_station_id": ["ST0001", "ST0002"],
        "drop_station_id": ["ST0003", "ST0004"],
        "qty": [3, 4],
        "travel_time_sec": [120.0, 200.0],
    })
    db.save_frame(conn, "ilp_plan", plan, run_label="R1", duration="_05_10")

    out = db.load_frame(conn, "ilp_plan", run_label="R1")
    assert "hour" not in out.columns
    assert set(out["duration"]) == {"_05_10"}


def test_vrp_plan_preserves_visit_order(conn):
    """방문 순서는 자연키가 없으므로 seq로 보존되어야 한다."""
    plan = pd.DataFrame({
        "cluster": [0, 0, 0],
        "from_id": ["ST0001", "ST0002", "ST0003"],
        "from_lat": [36.4, 36.3, 36.3], "from_lon": [127.3, 127.3, 127.4],
        "to_id": ["ST0002", "ST0003", "ST0002"],   # 같은 대여소 재방문
        "to_lat": [36.3, 36.3, 36.3], "to_lon": [127.3, 127.4, 127.3],
        "action": ["pick", "drop", "drop"],
        "qty": [5, 3, 2],
        "distance_km": [1.0, 2.0, 1.5], "travel_sec": [120.0, 240.0, 180.0],
        "work_sec": [150.0, 90.0, 60.0], "cum_sec": [270.0, 600.0, 840.0],
    })
    db.save_frame(conn, "vrp_plan", plan, run_label="R1", duration="_05_10")

    out = db.load_frame(conn, "vrp_plan", run_label="R1").sort_values("seq")
    assert len(out) == 3, "같은 대여소 재방문이 중복 키로 뭉개지면 안 된다"
    assert list(out["action"]) == ["pick", "drop", "drop"]


def test_net_demand_scoped_by_period(conn):
    """순수요는 원천 기간(period)에만 의존한다."""
    net = pd.DataFrame({
        "날짜": ["2025-11-03", "2025-11-04"],
        "station_id": ["ST0001", "ST0001"],
        **{f"net_{h:02d}": [h, h + 1] for h in range(24)},
    })
    db.save_frame(conn, "net_demand", net, period="25년 11월")

    out = db.load_frame(conn, "net_demand", period="25년 11월")
    assert "date" in out.columns and "날짜" not in out.columns
    assert set(out["period"]) == {"25년 11월"}
    assert len(out) == 2


def test_run_registry(conn):
    db.record_run(conn, "R1", period="25년 11월", duration="_05_10", raw_file="a.csv")
    db.record_run(conn, "R2", period="25년 12월", duration="_05_10")
    # 같은 라벨 재기록은 덮어쓴다
    db.record_run(conn, "R2", period="25년 12월", duration="_05_10,_10_15")

    runs = db.list_runs(conn)
    assert list(runs["run_label"]) == ["R2", "R1"]      # 최신순
    assert runs.loc[runs["run_label"] == "R2", "duration"].iloc[0] == "_05_10,_10_15"


def test_unknown_table_raises(conn):
    with pytest.raises(KeyError):
        db.save_frame(conn, "없는테이블", pd.DataFrame(), run_label="R1")


def test_missing_scope_value_raises(conn):
    with pytest.raises(ValueError):
        db.save_frame(conn, "pick_drop", _pick_drop(), run_label="R1")   # duration 누락


def test_compare_runs_with_sql(conn):
    """run_label 도입의 목적: 실행 간 비교가 쿼리 한 줄이 된다."""
    for label, rate in [("R1", 0.6), ("R2", 0.8)]:
        metrics = pd.DataFrame({
            "station_id": ["ST0001", "ST0002"],
            "station_name": ["가", "나"],
            "lat": [36.3, 36.4], "lon": [127.3, 127.4],
            "cluster": [0, 0], "stock": [5, 6],
            "mu": [1.0, 1.0], "sigma": [0.5, 0.5],
            "target_qty": [7.0, 8.0], "rebal_qty": [2, 2],
            "new_stock": [7, 8],
            "bf_imbalance": [2.0, 2.0], "af_imbalance": [0.0, 0.0],
            "improvement": [2.0, 2.0], "improvement_rate": [rate, rate],
        })
        db.save_frame(conn, "metrics", metrics, run_label=label, duration="_05_10")

    compared = pd.read_sql(
        "SELECT run_label, AVG(improvement_rate) AS avg_rate"
        " FROM metrics GROUP BY run_label ORDER BY run_label", conn)

    assert list(compared["run_label"]) == ["R1", "R2"]
    assert compared["avg_rate"].tolist() == pytest.approx([0.6, 0.8])


# ---------------- 스키마 마이그레이션 ----------------
#
# CREATE TABLE IF NOT EXISTS는 기존 테이블을 고쳐 주지 않는다. SCHEMA에 컬럼을
# 추가하면 예전에 DB를 만든 사용자에게는 그 컬럼이 없어 기록이 실패했다
# (kpi_summary에서 실제로 일어났다). init_schema가 이제 빠진 컬럼을 채운다.

# 1.9.3 시절 kpi_summary — 이후 추가된 stockout_hours_*·demand_mae가 없다.
_OLD_KPI_DDL = """
CREATE TABLE kpi_summary (
    run_label            TEXT NOT NULL,
    duration             TEXT NOT NULL,
    computed_at          TEXT NOT NULL,
    stations             INTEGER,
    avg_improvement_rate REAL,
    PRIMARY KEY (run_label, duration)
)
"""


def _legacy_db(tmp_path, ddl=_OLD_KPI_DDL):
    """구버전 스키마가 든 DB를 만든다(마이그레이션 전 상태)."""
    connection = db.connect(tmp_path / "legacy.db")
    connection.execute("DROP TABLE IF EXISTS kpi_summary")
    connection.executescript(ddl)
    connection.commit()
    return connection


def test_migration_adds_missing_columns(tmp_path):
    """구버전 DB에 새 컬럼이 채워지고, 기존 데이터는 남는다."""
    connection = _legacy_db(tmp_path)
    connection.execute(
        "INSERT INTO kpi_summary (run_label, duration, computed_at, stations,"
        " avg_improvement_rate) VALUES ('R1', '_05_10', '2026-08-12', 80, 0.7)")
    connection.commit()

    added = db.migrate_schema(connection)

    assert "kpi_summary.stockout_hours_before" in added
    assert "kpi_summary.demand_mae" in added

    columns = {row[1] for row in connection.execute("PRAGMA table_info(kpi_summary)")}
    assert set(db.KPI_FIELDS) <= columns, "KPI_FIELDS 전부가 컬럼으로 있어야 한다"

    kept = connection.execute("SELECT stations, avg_improvement_rate FROM kpi_summary").fetchone()
    assert kept == (80, 0.7), "마이그레이션이 기존 행을 지우면 안 된다"
    connection.close()


def test_save_kpi_works_after_migration(tmp_path):
    """구버전 DB에서도 init_schema를 거치면 지표가 기록된다(회귀: no such column)."""
    connection = _legacy_db(tmp_path)
    db.init_schema(connection)

    db.save_kpi(connection, "R1", "_05_10",
                {"stations": 80, "stockout_hours_before": 12.5, "demand_mae": 1.2})

    row = db.load_kpi(connection).iloc[0]
    assert row["stockout_hours_before"] == 12.5
    assert row["demand_mae"] == 1.2
    connection.close()


def test_migration_is_idempotent(conn):
    """최신 스키마에서는 추가할 것이 없다(두 번 돌려도 마찬가지)."""
    assert db.migrate_schema(conn) == []
    assert db.migrate_schema(conn) == []


def test_migration_skips_rental_history(tmp_path):
    """rental_history는 빈 컬럼을 붙이지 않는다 — 재적재가 정답인 테이블이다."""
    connection = db.connect(tmp_path / "legacy2.db")
    connection.executescript("""
        CREATE TABLE rental_history (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            period  TEXT NOT NULL,
            rent_at TEXT NOT NULL
        )
    """)
    connection.commit()

    added = db.migrate_schema(connection)

    assert not [item for item in added if item.startswith("rental_history.")], \
        "빈 컬럼을 붙이면 재적재가 필요한 상태를 정상으로 착각하게 된다"
    connection.close()


def test_expected_columns_covers_every_table(conn):
    """SCHEMA가 만드는 테이블이 전부 마이그레이션 대상에 잡히는지."""
    created = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}

    assert created == set(db.expected_columns())
    assert "rental_history" in created, "제외 테이블도 목록에는 있어야 한다"


def test_not_null_column_without_default_is_reported(tmp_path, monkeypatch, capsys):
    """자동 추가할 수 없는 컬럼은 조용히 넘어가지 않고 경고로 알린다.

    SQLite는 기본값 없는 NOT NULL 컬럼을 ADD COLUMN으로 붙이지 못한다.
    말없이 건너뛰면 마이그레이션 이전과 똑같이 'no such column'으로 실패한다.
    """
    connection = db.connect(tmp_path / "blocked.db")
    connection.executescript("CREATE TABLE 시험 (a TEXT)")
    connection.commit()

    # PRAGMA table_info 형식: (cid, name, type, notnull, dflt_value, pk)
    monkeypatch.setattr(db, "expected_columns", lambda: {
        "시험": [(0, "a", "TEXT", 0, None, 0),
                 (1, "b", "TEXT", 1, None, 0),      # NOT NULL, 기본값 없음 → 불가
                 (2, "c", "INTEGER", 1, "0", 0)],   # NOT NULL + 기본값 → 가능
    })

    added = db.migrate_schema(connection)
    출력 = capsys.readouterr().out

    assert added == ["시험.c"]
    assert "시험.b" in 출력 and "경고" in 출력
    connection.close()
