"""SQLite 저장소(db.py) 테스트.

핵심 관심사는 세 가지다.
1) 파일명에 있던 라벨이 run_label 컬럼으로 제대로 옮겨지는가
2) 같은 실행을 다시 저장해도 중복이 쌓이지 않는가(멱등성)
3) 라벨을 생략하면 최신 실행을 돌려주는가 (웹 API의 "수정시각 최신 파일" 휴리스틱 대체)

실데이터를 건드리지 않도록 모든 테스트가 tmp_path의 별도 DB 파일을 쓴다.
"""
from pathlib import Path

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


def _record_at(conn, run_label: str, created_at: str) -> None:
    """기록 시각을 못박아 실행을 남긴다. `record_run()`은 `datetime('now')`라
    같은 초에 두 건을 넣으면 선후가 안 갈린다."""
    conn.execute("INSERT INTO runs (run_label, created_at) VALUES (?, ?)",
                 (run_label, created_at))


def test_최신_실행은_사전순이_아니라_기록시각으로_고른다(conn):
    """**실험 라벨은 어떤 날짜 라벨도 사전순으로 이긴다** — `'o' > '2'`.

    그래서 실험을 한 번 돌리고 나면 그 뒤로 계획을 아무리 돌려도 `MAX(run_label)`
    은 영영 실험을 가리킨다. 실측에서 여섯 테이블이 그 상태였고, 라벨 없이 연
    `/orders`가 **실험을 현장 지시서로** 내고 있었다(1.26.125).
    """
    db.save_frame(conn, "pick_drop", _pick_drop(n=3),
                  run_label="obs-cmp-1520", duration="_05_10")
    db.save_frame(conn, "pick_drop", _pick_drop(n=2),
                  run_label="2026-09-05 10", duration="_05_10")
    _record_at(conn, "obs-cmp-1520", "2026-09-01 19:00:17")
    _record_at(conn, "2026-09-05 10", "2026-09-05 10:22:03")   # 나중에 돈 계획

    assert db.latest_label(conn, "pick_drop") == "2026-09-05 10"
    assert set(db.load_frame(conn, "pick_drop")["run_label"]) == {"2026-09-05 10"}


def test_기록이_없는_라벨은_사전순으로_물러난다(conn):
    """`runs`에 없는 옛 자료뿐이면 예전 동작 그대로여야 한다 — 조용히 비면 안 된다."""
    db.save_frame(conn, "pick_drop", _pick_drop(n=3),
                  run_label="2026-01-01 09", duration="_05_10")
    db.save_frame(conn, "pick_drop", _pick_drop(n=2),
                  run_label="2026-05-21 18", duration="_05_10")

    assert db.latest_label(conn, "pick_drop") == "2026-05-21 18"


def test_기록이_있는_라벨이_없는_라벨보다_최신이다(conn):
    """시각을 모르는 것을 최신으로 치면 옛 자료가 새 실행을 밀어낸다."""
    db.save_frame(conn, "pick_drop", _pick_drop(n=3),
                  run_label="zzz-옛자료", duration="_05_10")
    db.save_frame(conn, "pick_drop", _pick_drop(n=2),
                  run_label="2026-09-05 10", duration="_05_10")
    _record_at(conn, "2026-09-05 10", "2026-09-05 10:22:03")

    assert db.latest_label(conn, "pick_drop") == "2026-09-05 10"


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


def test_record_run_keeps_the_kind_a_person_pinned(conn):
    """재적재가 **사람이 못박은 종류를 지우지 않는다.**

    나머지 값은 파이프라인이 다시 만들지만 종류는 사람만 안다. 짐작과 다르게
    정정한 실행(`obs-cmp-*`은 라벨로는 실험인데 운영으로 고쳐 둠)을
    `tools/csv_to_db.py`처럼 `kind` 없이 재적재하면 조용히 짐작으로 되돌아갔다.
    """
    db.ensure_run(conn, "obs-cmp-1520", period="25년 11월")
    db.set_run_kind(conn, "obs-cmp-1520", "plan")

    db.record_run(conn, run_label="obs-cmp-1520", period="25년 11월", duration="_15_20")

    # DB 실제 값으로 본다 — list_runs()는 NULL이면 라벨로 짐작해 덮으므로
    # 그것만 보면 지워진 것을 못 잡는다(이 버그가 숨어 있던 이유).
    kind = conn.execute("SELECT kind FROM runs WHERE run_label = ?",
                        ("obs-cmp-1520",)).fetchone()[0]
    assert kind == "plan"


def test_record_run_takes_an_explicit_kind(conn):
    """넘긴 종류는 이긴다 — 지키는 것은 '모를 때'뿐이다."""
    db.ensure_run(conn, "R9", period="25년 11월")
    db.set_run_kind(conn, "R9", "plan")

    db.record_run(conn, run_label="R9", period="25년 11월", kind="probe")

    kind = conn.execute("SELECT kind FROM runs WHERE run_label = ?",
                        ("R9",)).fetchone()[0]
    assert kind == "probe"


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


def test_backtest_rows_are_replaced_not_duplicated(conn):
    """같은 시간대·월쌍·요일을 다시 재면 덮어쓴다.

    백테스트는 파이프라인 실행과 무관한 기록이라 run_label이 없다. 축은
    (시간대, 학습월, 검증월, 요일)이고, 다시 재면 최신 값만 남아야 한다.
    """
    def row(**over):
        base = {"duration": "_05_10", "train_period": "25년 10월",
                "test_period": "25년 11월", "day_type": "weekday",
                "mae": 3.0, "mae_zero": 5.0, "coverage": 0.95}
        base.update(over)
        return base

    assert db.save_backtest(conn, [row(), row(duration="_10_15")]) == 2
    db.save_backtest(conn, [row(mae=2.0)])

    out = db.load_backtest(conn, day_type="weekday")
    assert len(out) == 2, "덮어쓰지 않고 쌓였다"
    assert float(out[out.duration == "_05_10"]["mae"].iloc[0]) == 2.0


def test_backtest_keeps_weekday_and_holiday_apart(conn):
    """평일과 휴일은 절대 섞지 않는다 — 조회도 한쪽만 돌려준다."""
    common = {"duration": "_05_10", "train_period": "25년 10월",
              "test_period": "25년 11월", "mae": 3.0}
    db.save_backtest(conn, [dict(common, day_type="weekday"),
                            dict(common, day_type="holiday", mae=9.0)])

    assert len(db.load_backtest(conn, day_type="weekday")) == 1
    assert len(db.load_backtest(conn, day_type="holiday")) == 1
    assert len(db.load_backtest(conn)) == 2, "구분을 안 주면 전부"


def test_backtest_leaves_unmeasured_columns_null(conn):
    """못 구한 값은 빼고 넘긴다 — 0으로 채우면 '아주 정확하다'로 읽힌다."""
    db.save_backtest(conn, [{"duration": "_20_05", "train_period": "25년 10월",
                             "test_period": "25년 11월", "day_type": "weekday",
                             "mae": 3.0}])

    out = db.load_backtest(conn)
    assert pd.isna(out["rmse"].iloc[0]) and pd.isna(out["z_for_95"].iloc[0])



def test_띄운_쪽이_선언한_실행_종류를_읽는다(conn, monkeypatch):
    """`PBR_RUN_KIND`가 있으면 그 값이 `runs.kind`에 못박힌다.

    step 스크립트는 자기가 계획인지 실험인지 **알 수 없다** — 같은 파이프라인이
    둘 다 만들기 때문이다(실험은 `--now`에 실험 라벨을 주고 돌린 것뿐이다).
    아는 것은 띄우는 쪽뿐이라 `run_pipeline --run-kind`가 환경변수로 내려보낸다.
    """
    monkeypatch.setenv("PBR_RUN_KIND", "experiment")
    db.ensure_run(conn, "2026-09-04 09", period="25년 11월")

    kind = conn.execute("SELECT kind FROM runs WHERE run_label = ?",
                        ("2026-09-04 09",)).fetchone()[0]
    assert kind == "experiment"


def test_선언이_없으면_짐작에_맡긴다(conn, monkeypatch):
    """🔴 **기본값을 plan으로 박지 않는다.**

    선언을 잊은 실험이 '계획'으로 확정되면 라벨 짐작보다 **나빠진다** —
    짐작은 `obs-cmp-*`를 실험으로 맞히는데, 확정된 'plan'은 그 짐작을 이겨
    실험이 계획 화면 헤드라인에 올라온다. 선언이 없으면 NULL로 두는 것이 옳다.
    """
    monkeypatch.delenv("PBR_RUN_KIND", raising=False)
    db.ensure_run(conn, "obs-cmp-1520", period="25년 11월")

    assert conn.execute("SELECT kind FROM runs WHERE run_label = ?",
                        ("obs-cmp-1520",)).fetchone()[0] is None
    # 짐작은 그대로 살아 있다 — 화면에서는 실험으로 보인다.
    runs = db.list_runs(conn)
    assert runs.set_index("run_label")["kind"]["obs-cmp-1520"] == "experiment"


def test_인자로_준_종류가_선언을_이긴다(conn, monkeypatch):
    """수집기는 자기가 probe인 것을 안다 — 환경 선언보다 그쪽이 구체적이다."""
    monkeypatch.setenv("PBR_RUN_KIND", "plan")
    db.ensure_run(conn, "roadprobe-2026-09-04", kind="probe")

    assert conn.execute("SELECT kind FROM runs WHERE run_label = ?",
                        ("roadprobe-2026-09-04",)).fetchone()[0] == "probe"


def test_모르는_종류를_선언하면_조용히_넘기지_않는다(monkeypatch):
    """오타로 선언한 종류가 NULL로 떨어지면 짐작으로 되돌아가는데, **그
    되돌아감이 보이지 않는다.** 그래서 읽는 자리에서 막는다."""
    monkeypatch.setenv("PBR_RUN_KIND", "plaan")
    with pytest.raises(ValueError):
        db.declared_run_kind()


# ── 지금 열린 DB 경로를 사람에게 보여줄 때 (구역 D 검토, 1.26.143) ──


def test_열린_DB_경로를_한_곳에서_푼다(monkeypatch, tmp_path):
    """`DB_PATH`는 import 시점에 굳는 **기본값**이라 `PBR_DB_PATH`를 모른다.

    그것을 화면에 찍으면 **어디에 넣었는지 거짓말하는 안내문**이 된다. 1.26.51에
    실제로 겪어 `transfer_run.py`가 자기 안에 같은 함수를 만들어 막았는데,
    `csv_to_db`·`load_rentals`·`export_collected` 셋은 그대로 남아 있었다 —
    한 도구가 배운 것이 옆 도구에 닿지 않으면 같은 거짓말이 계속 남는다.
    """
    target = tmp_path / "다른.db"
    monkeypatch.setenv("PBR_DB_PATH", str(target))

    assert db.active_db_path() == target
    assert db.active_db_path() != db.DB_PATH, "기본값을 그대로 돌려주고 있다"

    # `connect()`가 실제로 여는 곳과 **같아야** 한다 — 다르면 안내문이 또 거짓이 된다.
    conn = db.connect()
    try:
        opened = conn.execute("PRAGMA database_list").fetchone()[2]
    finally:
        conn.close()
    assert Path(opened) == db.active_db_path()

    # 인자로 준 경로가 환경변수를 이긴다 (connect()와 같은 우선순위).
    직접 = tmp_path / "직접.db"
    assert db.active_db_path(직접) == 직접


def test_환경변수가_없으면_기본값이다(monkeypatch):
    monkeypatch.delenv("PBR_DB_PATH", raising=False)
    assert db.active_db_path() == db.DB_PATH


def test_도구들이_DB_경로를_짐작하지_않는다():
    """세 도구가 `db.DB_PATH`나 하드코딩 문자열을 찍고 있었다.

    특히 `export_collected.py`는 `db.db_path() if hasattr(db, 'db_path') else
    'data/bike_system.db'`였는데 **`db.db_path`는 존재한 적이 없어** 폴백이
    언제나 탔다 — `PBR_DB_PATH`가 없어도 사실이 아니라 짐작을 찍고 있었다.
    """
    root = Path(__file__).resolve().parents[1]
    for name in ("csv_to_db.py", "load_rentals.py", "export_collected.py",
                 "transfer_run.py"):
        code = (root / "tools" / name).read_text(encoding="utf-8")
        body = "\n".join(l for l in code.splitlines()
                         if not l.lstrip().startswith("#"))
        assert "db.DB_PATH" not in body, f"{name}이 import 시점 기본값을 찍는다"
        assert "hasattr(db, 'db_path')" not in body, f"{name}에 없는 함수를 보는 폴백이 남아 있다"
        assert "active_db_path" in body, f"{name}이 열린 DB 경로를 쓰지 않는다"
