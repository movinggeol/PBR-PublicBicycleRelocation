"""SQLite 저장소 공통 모듈 (DB_PLAN.md 1단계).

파이프라인 산출물을 `data/bike_system.db` 한 파일에 모아 관리한다.
지금 파일명에 박혀 있는 `{now}` 라벨을 **run_label 컬럼**으로 옮기는 것이 핵심이며,
그 결과 "지난 실행 대비 개선률" 같은 비교가 SQL 한 줄이 된다.

사용 예:
    import db
    with db.connect() as conn:
        db.init_schema(conn)
        db.record_run(conn, "2026-05-21 18", period="25년 11월", duration="_05_10")
        db.save_frame(conn, "pick_drop", df, run_label="2026-05-21 18", duration="_05_10")
        latest = db.load_frame(conn, "pick_drop")      # 라벨 생략 시 최신 실행

이식성: 다른 DB로 옮길 때 바꿔야 하는 곳은 connect() 하나다.
pandas의 to_sql/read_sql은 sqlite3 연결과 SQLAlchemy 엔진 모두에서 동일하게 동작하므로,
PostgreSQL 전환 시점에 connect()만 엔진 팩토리로 교체하면 나머지 코드는 그대로 쓴다.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, Optional, Sequence, Tuple

import pandas as pd

from project_config import DATA_ROOT, FLEET_SIZE, VEHICLES_PER_ROUND, vehicle_ids

# 기본 DB 위치. 환경변수 PBR_DB_PATH로 바꿀 수 있다
# (테스트가 실제 DB를 건드리지 않도록 별도 파일을 가리키는 데 쓴다).
DB_PATH = DATA_ROOT / "bike_system.db"

# 순수요는 24개 시간대 컬럼을 그대로 보존한다(현재 계산 코드가 wide 형태를 기대).
NET_COLUMNS = [f"net_{h:02d}" for h in range(24)]

# 원천 대여이력 CSV(한글 컬럼) ↔ rental_history 테이블(ASCII 컬럼)
RENTAL_COLUMNS = {
    "자전거번호": "bike_no",
    "대여일시": "rent_at",
    "대여_대여소ID": "rent_station",
    "대여_대여소명": "rent_station_name",
    "대여_X좌표": "rent_lat",      # API와 마찬가지로 X가 위도다
    "대여_Y좌표": "rent_lon",
    "반납일시": "return_at",
    "반납_대여소ID": "return_station",
    "반납_X좌표": "return_lat",
    "반납_Y좌표": "return_lon",
    "이용시간(분)": "use_min",
    "이용거리(km)": "use_km",
}
RENTAL_COLUMNS_REVERSED = {v: k for k, v in RENTAL_COLUMNS.items()}

# 문자열 비교로 기간 조회가 되도록 저장 시 통일하는 형식
DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class TableSpec:
    """테이블별 저장 규칙.

    scope   : 같은 실행을 다시 저장할 때 먼저 지울 기준 컬럼(멱등성 보장)
    rename  : CSV의 한글 컬럼 → DB의 ASCII 컬럼
    drop    : 저장 시 버릴 컬럼(다른 컬럼과 중복되는 값)
    """
    scope: Tuple[str, ...]
    rename: Dict[str, str] = field(default_factory=dict)
    drop: Tuple[str, ...] = ()


TABLES: Dict[str, TableSpec] = {
    # TASHU API 스냅샷 원본(거치대 설명 문자열 포함)
    "station_stock": TableSpec(scope=("run_label",)),
    "station_info": TableSpec(
        scope=("run_label",),
        rename={"총 이용시간(분)": "total_use_min", "총 이용거리(km)": "total_use_km"},
    ),
    "parking_lot": TableSpec(scope=("run_label",)),
    # 순수요는 원천 데이터 기간(period)에만 의존하므로 run_label이 아니라 period로 묶는다.
    "net_demand": TableSpec(scope=("period",), rename={"날짜": "date"}),
    "rebalance_plan": TableSpec(scope=("run_label", "duration")),
    "pick_drop": TableSpec(scope=("run_label", "duration")),
    # ilp_plan의 'hour'는 duration과 같은 값이라 중복 저장하지 않는다.
    "ilp_plan": TableSpec(scope=("run_label", "duration"), drop=("hour",)),
    "vrp_plan": TableSpec(scope=("run_label", "duration")),
    "metrics": TableSpec(scope=("run_label", "duration")),
    "route_summary": TableSpec(
        scope=("run_label", "duration"),
        rename={"방문수": "visits", "처리대수": "bikes", "총이동거리_km": "distance_km",
                "총이동시간_분": "travel_min", "총작업시간_분": "work_min",
                "총소요시간_분": "total_min"},
    ),
}

_NET_DDL = ",\n    ".join(f"{c} INTEGER" for c in NET_COLUMNS)

SCHEMA = f"""
-- 실행 이력. 각 산출물 테이블의 run_label이 여기를 가리킨다.
CREATE TABLE IF NOT EXISTS runs (
    run_label   TEXT PRIMARY KEY,
    period      TEXT,
    duration    TEXT,
    raw_file    TEXT,
    day_type    TEXT,     -- weekday | weekend. 산출물 파일명에는 안 들어가므로 여기 남긴다
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS station_stock (
    run_label     TEXT NOT NULL,
    station_id    TEXT NOT NULL,
    station_name  TEXT,
    parking_info  TEXT,
    lat           REAL,
    lon           REAL,
    stock         INTEGER,
    PRIMARY KEY (run_label, station_id)
);

CREATE TABLE IF NOT EXISTS station_info (
    run_label      TEXT NOT NULL,
    station_id     TEXT NOT NULL,
    station_name   TEXT,
    lat            REAL,
    lon            REAL,
    parking_lot    INTEGER,
    stock          INTEGER,
    rent_count     INTEGER,
    return_count   INTEGER,
    total_use_min  REAL,
    total_use_km   REAL,
    PRIMARY KEY (run_label, station_id)
);

CREATE TABLE IF NOT EXISTS parking_lot (
    run_label    TEXT NOT NULL,
    station_id   TEXT NOT NULL,
    lat          REAL,
    lon          REAL,
    parking_lot  INTEGER,
    PRIMARY KEY (run_label, station_id)
);

CREATE TABLE IF NOT EXISTS net_demand (
    period      TEXT NOT NULL,
    date        TEXT NOT NULL,
    station_id  TEXT NOT NULL,
    {_NET_DDL},
    PRIMARY KEY (period, date, station_id)
);

CREATE TABLE IF NOT EXISTS rebalance_plan (
    run_label   TEXT NOT NULL,
    duration    TEXT NOT NULL,
    station_id  TEXT NOT NULL,
    mu          REAL,
    sigma       REAL,
    parking_lot INTEGER,
    stock       INTEGER,
    target_qty  REAL,
    rebal_qty   INTEGER,
    PRIMARY KEY (run_label, duration, station_id)
);

CREATE TABLE IF NOT EXISTS pick_drop (
    run_label     TEXT NOT NULL,
    duration      TEXT NOT NULL,
    station_id    TEXT NOT NULL,
    station_name  TEXT,
    lat           REAL,
    lon           REAL,
    parking_lot   INTEGER,
    stock         INTEGER,
    target_qty    REAL,
    rebal_qty     INTEGER,
    mu            REAL,
    sigma         REAL,
    cluster       INTEGER,
    PRIMARY KEY (run_label, duration, station_id)
);

CREATE TABLE IF NOT EXISTS ilp_plan (
    run_label        TEXT NOT NULL,
    duration         TEXT NOT NULL,
    cluster          INTEGER,
    pick_station_id  TEXT NOT NULL,
    drop_station_id  TEXT NOT NULL,
    qty              INTEGER,
    travel_time_sec  REAL,
    PRIMARY KEY (run_label, duration, pick_station_id, drop_station_id)
);

-- 방문 순서는 같은 대여소를 여러 번 지날 수 있어 자연키가 없다. 행 순서를 seq로 보존.
CREATE TABLE IF NOT EXISTS vrp_plan (
    run_label    TEXT NOT NULL,
    duration     TEXT NOT NULL,
    seq          INTEGER NOT NULL,
    cluster      INTEGER,
    vehicle_id   TEXT,
    from_id      TEXT,
    from_lat     REAL,
    from_lon     REAL,
    to_id        TEXT,
    to_lat       REAL,
    to_lon       REAL,
    action       TEXT,
    qty          INTEGER,
    distance_km  REAL,
    travel_sec   REAL,
    work_sec     REAL,
    cum_sec      REAL,
    PRIMARY KEY (run_label, duration, seq)
);

CREATE TABLE IF NOT EXISTS metrics (
    run_label         TEXT NOT NULL,
    duration          TEXT NOT NULL,
    station_id        TEXT NOT NULL,
    station_name      TEXT,
    lat               REAL,
    lon               REAL,
    cluster           INTEGER,
    stock             INTEGER,
    mu                REAL,
    sigma             REAL,
    target_qty        REAL,
    rebal_qty         INTEGER,
    new_stock         INTEGER,
    bf_imbalance      REAL,
    af_imbalance      REAL,
    improvement       REAL,
    improvement_rate  REAL,
    PRIMARY KEY (run_label, duration, station_id)
);

CREATE TABLE IF NOT EXISTS route_summary (
    run_label    TEXT NOT NULL,
    duration     TEXT NOT NULL,
    cluster      INTEGER NOT NULL,
    visits       INTEGER,
    bikes        INTEGER,
    distance_km  REAL,
    travel_min   REAL,
    work_min     REAL,
    total_min    REAL,
    PRIMARY KEY (run_label, duration, cluster)
);

-- 차량 마스터. 정비 등으로 빠지면 active=0으로 두면 배정에서 제외된다.
CREATE TABLE IF NOT EXISTS vehicle (
    vehicle_id  TEXT PRIMARY KEY,
    active      INTEGER NOT NULL DEFAULT 1,
    note        TEXT
);

-- 회차(run_label + duration)별 차량 배정과 그 회차에 실제로 한 작업량.
-- 로테이션은 이 누적치를 보고 정하므로, 형평성 추적의 원장 역할을 한다.
CREATE TABLE IF NOT EXISTS vehicle_assignment (
    run_label    TEXT NOT NULL,
    duration     TEXT NOT NULL,
    vehicle_id   TEXT NOT NULL,
    cluster      INTEGER NOT NULL,
    stations     INTEGER,   -- 방문 대여소 수
    bikes        INTEGER,   -- 처리한 자전거 대수
    distance_km  REAL,
    minutes      REAL,      -- 이동 + 작업 소요시간
    PRIMARY KEY (run_label, duration, vehicle_id)
);

CREATE INDEX IF NOT EXISTS idx_assignment_vehicle ON vehicle_assignment(vehicle_id);

-- 실행 1건(run_label + duration) = 1행. 흩어져 있는 지표를 한 줄로 모아
-- 실행 간 비교를 쿼리 하나로 만든다. (docs/KPI.md)
CREATE TABLE IF NOT EXISTS kpi_summary (
    run_label             TEXT NOT NULL,
    duration              TEXT NOT NULL,
    computed_at           TEXT NOT NULL,
    -- 규모
    stations              INTEGER,   -- 작업 대상 대여소 수
    clusters              INTEGER,
    vehicles_used         INTEGER,
    bikes_moved           INTEGER,   -- 실제로 옮긴 자전거 수
    -- A. 계획 (계획이 목표를 얼마나 채웠나)
    avg_improvement_rate  REAL,
    pick_improvement_rate REAL,
    drop_improvement_rate REAL,
    target_met_ratio      REAL,      -- 재배치 후 불균형 <= 1인 대여소 비율
    -- C. 운영 (현장에서 실행 가능한가)
    total_distance_km     REAL,
    max_cluster_minutes   REAL,
    avg_cluster_minutes   REAL,
    time_budget_minutes   REAL,      -- 판정 기준(바뀔 수 있으므로 함께 기록)
    time_budget_met       REAL,      -- 예산 안에 끝난 클러스터 비율
    vehicle_load_gap      REAL,      -- 회차 내 최대-최소 소요시간
    -- D. 효율 (투입 대비 산출)
    improvement_per_km    REAL,      -- 1km 이동으로 줄인 불균형 대수
    -- E. 품질
    cluster_max_imbalance INTEGER,
    -- B. 실측 — KPI.md 4·5단계에서 채운다(지금은 NULL)
    stockout_hours_before REAL,
    stockout_hours_after  REAL,
    demand_mae            REAL,
    PRIMARY KEY (run_label, duration)
);

-- 원천 대여이력(대용량). 원본 CSV 12개 컬럼을 그대로 미러링한다.
-- 대여소명·좌표는 station_info와 중복이지만, api_to_info가 이 값을 집계해
-- 대여소 정보를 만들기 때문에 여기 있어야 결과가 달라지지 않는다.
CREATE TABLE IF NOT EXISTS rental_history (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    period             TEXT NOT NULL,
    bike_no            TEXT,
    rent_at            TEXT NOT NULL,
    rent_station       TEXT NOT NULL,
    rent_station_name  TEXT,
    rent_lat           REAL,
    rent_lon           REAL,
    return_at          TEXT,
    return_station     TEXT,
    return_lat         REAL,
    return_lon         REAL,
    -- NUMERIC 친화도: 정수 값은 정수로 보존한다. REAL로 두면 원본이 정수인
    -- '이용시간(분)'이 실수로 바뀌어 CSV 경로와 산출물 dtype이 달라진다.
    use_min            NUMERIC,
    use_km             NUMERIC
);

CREATE INDEX IF NOT EXISTS idx_rental_period ON rental_history(period);
CREATE INDEX IF NOT EXISTS idx_rental_rent_at ON rental_history(rent_at);
CREATE INDEX IF NOT EXISTS idx_rental_station ON rental_history(rent_station);
CREATE INDEX IF NOT EXISTS idx_pick_drop_cluster ON pick_drop(run_label, duration, cluster);
CREATE INDEX IF NOT EXISTS idx_metrics_run ON metrics(run_label, duration);
"""


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """DB에 연결한다. 다른 DB로 옮길 때 교체할 지점은 이 함수 하나다.

    경로 우선순위: 인자 → 환경변수 PBR_DB_PATH → 기본값(data/bike_system.db).
    WAL 모드를 켜면 파이프라인이 쓰는 중에도 웹 대시보드가 읽을 수 있다.
    """
    path = Path(db_path or os.getenv("PBR_DB_PATH") or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# 컬럼 자동 추가에서 제외하는 테이블.
# rental_history는 _ensure_rental_schema()가 '지우고 다시 만들기'로 다룬다.
# 여기에 빈 컬럼을 붙이면 재적재가 필요한 상태를 정상으로 착각하게 된다.
MIGRATION_EXCLUDED = ("rental_history",)

_EXPECTED_COLUMNS: Optional[Dict[str, list]] = None


def expected_columns() -> Dict[str, list]:
    """SCHEMA가 정의하는 테이블별 컬럼 정보. `{테이블: [PRAGMA table_info 행, ...]}`

    DDL을 직접 파싱하지 않고 임시 메모리 DB에 스키마를 만들어 SQLite에게 물어본다 —
    SCHEMA 문자열이 바뀌어도 따로 손볼 곳이 없다. 결과는 한 번만 계산해 재사용한다.
    """
    global _EXPECTED_COLUMNS
    if _EXPECTED_COLUMNS is None:
        probe = sqlite3.connect(":memory:")
        try:
            probe.executescript(SCHEMA)
            tables = [row[0] for row in probe.execute(
                "SELECT name FROM sqlite_master"
                " WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
            _EXPECTED_COLUMNS = {
                table: list(probe.execute(f"PRAGMA table_info({table})"))
                for table in tables
            }
        finally:
            probe.close()
    return _EXPECTED_COLUMNS


def migrate_schema(conn: sqlite3.Connection) -> list:
    """이미 있는 테이블에 빠진 컬럼을 채운다. 추가한 `테이블.컬럼` 목록을 돌려준다.

    `CREATE TABLE IF NOT EXISTS`는 기존 테이블을 고쳐 주지 않는다. 그래서 나중에
    SCHEMA에 컬럼을 추가하면, 예전에 DB를 만든 사용자에게는 그 컬럼이 없어
    `no such column`으로 기록이 실패한다(실제로 kpi_summary에서 일어났다).

    **컬럼 추가만 자동으로 한다.** 이름 변경·삭제·타입 변경은 데이터를 잃을 수 있어
    사람이 판단할 일이므로 손대지 않는다.
    """
    added, blocked = [], []

    for table, columns in expected_columns().items():
        if table in MIGRATION_EXCLUDED:
            continue
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue                      # 아직 없는 테이블은 CREATE가 만든다

        for _, name, decl_type, notnull, default, _pk in columns:
            if name in existing:
                continue
            # SQLite의 ADD COLUMN 제약: NOT NULL은 기본값이 있어야 붙일 수 있다.
            if notnull and default is None:
                blocked.append(f"{table}.{name}")
                continue
            clause = f"{name} {decl_type}"
            if default is not None:
                clause += f" DEFAULT {default}"
            if notnull:
                clause += " NOT NULL"
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {clause}")
            added.append(f"{table}.{name}")

    if added:
        conn.commit()
        print(f"[안내] DB 스키마에 컬럼을 추가했습니다: {', '.join(added)}")
    if blocked:
        # 조용히 넘어가면 예전과 똑같이 'no such column'으로 실패하므로 반드시 알린다.
        print(f"[경고] 기본값이 없는 NOT NULL 컬럼은 자동 추가할 수 없습니다: "
              f"{', '.join(blocked)}. 해당 테이블을 다시 만들거나 마이그레이션이 필요합니다.")
    return added


def init_schema(conn: sqlite3.Connection) -> None:
    """테이블과 인덱스를 만들고(이미 있으면 그대로), 빠진 컬럼을 채운다."""
    conn.executescript(SCHEMA)
    migrate_schema(conn)
    conn.commit()


@contextmanager
def session(db_path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    """연결을 열고 스키마를 보장한 뒤, 끝나면 커밋하고 **닫는다**.

    sqlite3 연결의 `with` 문은 트랜잭션만 관리하고 연결을 닫지 않으므로
    (자원 누수) 이 헬퍼를 쓴다.
    """
    conn = connect(db_path)
    try:
        init_schema(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _spec(table: str) -> TableSpec:
    if table not in TABLES:
        raise KeyError(f"알 수 없는 테이블: {table} (가능: {', '.join(TABLES)})")
    return TABLES[table]


def _scope_values(spec: TableSpec, run_label: Optional[str],
                  period: Optional[str], duration: Optional[str]) -> Dict[str, str]:
    supplied = {"run_label": run_label, "period": period, "duration": duration}
    values = {}
    for column in spec.scope:
        value = supplied[column]
        if value is None:
            raise ValueError(f"{column} 값이 필요합니다.")
        values[column] = value
    return values


def save_frame(conn: sqlite3.Connection, table: str, df: pd.DataFrame,
               run_label: Optional[str] = None, period: Optional[str] = None,
               duration: Optional[str] = None) -> int:
    """DataFrame을 테이블에 저장한다.

    같은 스코프(run_label/period[+duration])의 기존 행은 먼저 지우므로,
    같은 실행을 다시 저장해도 중복이 쌓이지 않는다. 저장한 행 수를 돌려준다.
    """
    spec = _spec(table)
    scope = _scope_values(spec, run_label, period, duration)

    frame = df.drop(columns=[c for c in spec.drop if c in df.columns])
    frame = frame.rename(columns=spec.rename)

    # 방문 순서 테이블은 행 순서 자체가 정보다.
    if table == "vrp_plan" and "seq" not in frame.columns:
        frame = frame.reset_index(drop=True)
        frame.insert(0, "seq", range(len(frame)))

    for column, value in scope.items():
        frame.insert(0, column, value)

    where = " AND ".join(f"{c} = ?" for c in scope)
    conn.execute(f"DELETE FROM {table} WHERE {where}", tuple(scope.values()))
    frame.to_sql(table, conn, if_exists="append", index=False)
    conn.commit()
    return len(frame)


def load_frame(conn: sqlite3.Connection, table: str,
               run_label: Optional[str] = None, period: Optional[str] = None,
               duration: Optional[str] = None) -> pd.DataFrame:
    """테이블을 읽는다.

    run_label/period를 생략하면 가장 최근 실행분을 돌려준다.
    (파일 수정시각 휴리스틱 대신 라벨 정렬을 쓴다.)
    """
    spec = _spec(table)
    label_column = "period" if "period" in spec.scope else "run_label"
    label_value = period if label_column == "period" else run_label

    if label_value is None:
        label_value = latest_label(conn, table)
        if label_value is None:
            return pd.DataFrame()

    conditions = [f"{label_column} = ?"]
    params = [label_value]
    if "duration" in spec.scope and duration is not None:
        conditions.append("duration = ?")
        params.append(duration)

    query = f"SELECT * FROM {table} WHERE {' AND '.join(conditions)}"
    return pd.read_sql(query, conn, params=params)


def latest_label(conn: sqlite3.Connection, table: str) -> Optional[str]:
    """해당 테이블에 저장된 가장 최근 run_label(또는 period)."""
    spec = _spec(table)
    column = "period" if "period" in spec.scope else "run_label"
    row = conn.execute(f"SELECT MAX({column}) FROM {table}").fetchone()
    return row[0] if row else None


def record_run(conn: sqlite3.Connection, run_label: str, period: Optional[str] = None,
               duration: Optional[str] = None, raw_file: Optional[str] = None,
               day_type: Optional[str] = None) -> None:
    """실행 메타데이터를 기록한다(같은 라벨이면 덮어쓴다)."""
    conn.execute(
        "INSERT OR REPLACE INTO runs"
        " (run_label, period, duration, raw_file, day_type, created_at)"
        " VALUES (?, ?, ?, ?, ?, datetime('now', 'localtime'))",
        (run_label, period, duration, raw_file, day_type),
    )
    conn.commit()


def ensure_run(conn: sqlite3.Connection, run_label: str, period: Optional[str] = None,
               duration: Optional[str] = None, raw_file: Optional[str] = None,
               day_type: Optional[str] = None) -> None:
    """실행 행이 없으면 만들고, 새로 알게 된 값만 채운다.

    단계 스크립트는 저마다 아는 정보가 다르다(순수요 단계는 period만, 최적화 단계는
    duration만 안다). 이미 채워진 값을 덮어쓰지 않고 누적하기 위해 COALESCE를 쓴다.
    전체 값을 확정해 덮어써야 할 때는 record_run()을 쓴다.
    """
    conn.execute(
        "INSERT OR IGNORE INTO runs (run_label, created_at)"
        " VALUES (?, datetime('now', 'localtime'))",
        (run_label,),
    )
    # day_type만 인자가 우선이다(COALESCE의 순서가 반대인 것에 주의).
    # 나머지는 '먼저 기록된 값을 지킨다'가 맞지만, day_type은 **산출물의 성격을
    # 규정**한다 — 같은 라벨을 다른 요일 구분으로 다시 돌리면 산출물이 덮어써지므로
    # 기록도 따라가야 한다. 안 그러면 주말 산출물에 '평일'이라고 적혀 남는다.
    conn.execute(
        "UPDATE runs SET period = COALESCE(period, ?),"
        "                duration = COALESCE(duration, ?),"
        "                raw_file = COALESCE(raw_file, ?),"
        "                day_type = COALESCE(?, day_type)"
        " WHERE run_label = ?",
        (period, duration, raw_file, day_type, run_label),
    )
    conn.commit()


def save_output(table: str, df: pd.DataFrame, run_label: Optional[str] = None,
                period: Optional[str] = None, duration: Optional[str] = None,
                db_path: Optional[Path] = None, day_type: Optional[str] = None) -> int:
    """단계 산출물을 DB에 기록한다 (CSV·DB 이중 기록 전환기용).

    아직 **CSV가 정본**이므로 DB 기록이 실패해도 파이프라인을 멈추지 않는다.
    대신 경고를 남겨 문제를 감추지 않는다. 회귀는 테스트가 잡는다
    (tests/test_pipeline.py의 DB 적재 검증).

    반환값은 저장한 행 수, 실패 시 0.
    """
    try:
        with session(db_path) as conn:
            if run_label:
                ensure_run(conn, run_label, period=period, duration=duration,
                           day_type=day_type)
            rows = save_frame(conn, table, df, run_label=run_label,
                              period=period, duration=duration)
        return rows
    except Exception as err:   # DB는 아직 보조 저장소 — 파이프라인을 막지 않는다
        print(f"[경고] DB 기록 실패 ({table}): {type(err).__name__}: {err}")
        return 0


def list_runs(conn: sqlite3.Connection) -> pd.DataFrame:
    """실행 이력을 최신순으로 돌려준다."""
    return pd.read_sql("SELECT * FROM runs ORDER BY run_label DESC", conn)


# ---------------- 성과 지표 (docs/KPI.md) ----------------

# kpi_summary에 저장할 수 있는 컬럼(키·시각 제외). 넘겨받은 dict에서 이것만 골라 쓴다.
KPI_FIELDS = (
    "stations", "clusters", "vehicles_used", "bikes_moved",
    "avg_improvement_rate", "pick_improvement_rate", "drop_improvement_rate",
    "target_met_ratio", "total_distance_km", "max_cluster_minutes",
    "avg_cluster_minutes", "time_budget_minutes", "time_budget_met",
    "vehicle_load_gap", "improvement_per_km", "cluster_max_imbalance",
    "stockout_hours_before", "stockout_hours_after", "demand_mae",
)


def save_kpi(conn: sqlite3.Connection, run_label: str, duration: str,
             metrics: Dict[str, object]) -> None:
    """실행 1건의 지표를 기록한다(같은 실행·회차면 덮어쓴다).

    metrics의 키 중 KPI_FIELDS에 있는 것만 저장하므로, 계산하지 못한 지표는
    그냥 빼고 넘기면 된다(해당 컬럼은 NULL).
    """
    known = {k: metrics[k] for k in KPI_FIELDS if k in metrics}
    columns = ["run_label", "duration", "computed_at", *known]
    placeholders = ", ".join(["?", "?", "datetime('now', 'localtime')"]
                             + ["?"] * len(known))
    conn.execute(
        f"INSERT OR REPLACE INTO kpi_summary ({', '.join(columns)})"
        f" VALUES ({placeholders})",
        (run_label, duration, *known.values()),
    )
    conn.commit()


def load_kpi(conn: sqlite3.Connection, run_label: Optional[str] = None,
             duration: Optional[str] = None) -> pd.DataFrame:
    """지표를 읽는다(최신 실행순). 인자를 주면 그 실행·회차로 좁힌다."""
    conditions, params = [], []
    if run_label:
        conditions.append("run_label = ?")
        params.append(run_label)
    if duration:
        conditions.append("duration = ?")
        params.append(duration)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return pd.read_sql(
        f"SELECT * FROM kpi_summary {where} ORDER BY run_label DESC, duration ASC",
        conn, params=params)


# ---------------- 차량 운용 (로테이션과 형평성) ----------------

'''차량 대수를 줄여서 배정에서 뺀 차량에 남기는 표시.
정비(active=0, 사용자가 적은 note)와 구분해야 대수를 되돌릴 때 정비 차량까지
살아나지 않는다.'''
FLEET_SHRINK_NOTE = "보유 대수 축소로 제외"


def ensure_fleet(conn: sqlite3.Connection, size: int = None) -> None:
    """차량 마스터에 없는 차량을 채운다(있으면 그대로 둔다). V01 ~ V{size}.

    **읽기 경로용이다.** 대수를 실제로 맞추는 것은 sync_fleet이다 — 웹 대시보드가
    화면을 열 때마다 직전 실행이 정한 보유 대수를 되돌리면 안 되기 때문이다.
    """
    for vehicle_id in vehicle_ids(size or FLEET_SIZE):
        conn.execute(
            "INSERT OR IGNORE INTO vehicle (vehicle_id, active) VALUES (?, 1)",
            (vehicle_id,))
    conn.commit()


def sync_fleet(conn: sqlite3.Connection, size: int = None) -> None:
    """차량 마스터를 보유 대수(size)에 맞춘다. 파이프라인 실행 경로에서 부른다.

    대수를 **늘리면** 새 차량이 추가되고, **줄이면** 범위를 벗어난 차량이
    active=0으로 빠진다(행은 남긴다 — 형평성 이력이 지워지면 안 된다).
    다시 늘리면 축소로 뺐던 차량만 복귀하고, 정비로 빼 둔 차량은 그대로 둔다.
    """
    ids = vehicle_ids(size or FLEET_SIZE)
    ensure_fleet(conn, size)

    placeholders = ",".join("?" * len(ids))
    conn.execute(
        f"UPDATE vehicle SET active = 0, note = ?"
        f" WHERE active = 1 AND vehicle_id NOT IN ({placeholders})",
        [FLEET_SHRINK_NOTE, *ids])
    conn.execute(
        f"UPDATE vehicle SET active = 1, note = NULL"
        f" WHERE active = 0 AND note = ? AND vehicle_id IN ({placeholders})",
        [FLEET_SHRINK_NOTE, *ids])
    conn.commit()


def vehicle_workload(conn: sqlite3.Connection, only_active: bool = True) -> pd.DataFrame:
    """차량별 누적 작업량. 한 번도 안 나간 차량도 0으로 포함한다.

    컬럼: vehicle_id, active, rounds, bikes, distance_km, minutes, last_run, last_duration
    """
    where = "WHERE v.active = 1" if only_active else ""
    return pd.read_sql(f"""
        SELECT v.vehicle_id,
               v.active,
               COUNT(a.vehicle_id)                AS rounds,
               COALESCE(SUM(a.bikes), 0)          AS bikes,
               ROUND(COALESCE(SUM(a.distance_km), 0), 2) AS distance_km,
               ROUND(COALESCE(SUM(a.minutes), 0), 1)     AS minutes,
               MAX(a.run_label)                   AS last_run,
               MAX(a.duration)                    AS last_duration
        FROM vehicle v
        LEFT JOIN vehicle_assignment a ON a.vehicle_id = v.vehicle_id
        {where}
        GROUP BY v.vehicle_id, v.active
        ORDER BY v.vehicle_id
    """, conn)


def assign_vehicles(conn: sqlite3.Connection, cluster_loads: Dict[int, float],
                    run_label: str, duration: str) -> Dict[int, str]:
    """클러스터에 차량을 배정한다. 반환: {cluster: vehicle_id}

    로테이션 규칙:
      1. 누적 소요시간이 적은 차량부터 뽑는다(동률이면 출동 횟수 → ID 순).
         → 직전 회차에 나간 차량은 누적이 늘어 뒤로 밀리므로 자연히 교대가 된다.
      2. 뽑은 차량 중 **가장 한가한 차량에 가장 무거운 클러스터**를 준다.
         → 한 회차 안에서도, 회차를 거듭해도 부하가 고르게 수렴한다.

    같은 회차를 다시 계산하면 그 회차의 기존 배정은 제외하고 계산하므로
    재실행해도 결과가 같다(멱등).
    """
    # 이 프로세스의 FLEET_SIZE(=이번 실행의 --fleet-size)에 마스터를 맞춘다.
    sync_fleet(conn)

    # 이 회차의 기존 배정은 누적에서 빼야 재실행 시 같은 결과가 나온다.
    workload = pd.read_sql("""
        SELECT v.vehicle_id,
               COALESCE(SUM(CASE WHEN a.run_label IS NOT NULL THEN a.minutes END), 0) AS minutes,
               COUNT(a.vehicle_id) AS rounds
        FROM vehicle v
        LEFT JOIN vehicle_assignment a
               ON a.vehicle_id = v.vehicle_id
              AND NOT (a.run_label = ? AND a.duration = ?)
        WHERE v.active = 1
        GROUP BY v.vehicle_id
        ORDER BY minutes ASC, rounds ASC, v.vehicle_id ASC
    """, conn, params=[run_label, duration])

    if workload.empty:
        raise RuntimeError("운용 가능한 차량이 없습니다(vehicle 테이블 확인).")

    needed = len(cluster_loads)
    available = len(workload)
    if needed > available:
        raise ValueError(
            f"클러스터 {needed}개에 배정할 차량이 부족합니다(가용 {available}대). "
            f"클러스터 수를 줄이거나 차량을 추가하세요.")

    picked = workload["vehicle_id"].tolist()[:needed]

    # 무거운 클러스터 ↔ 한가한 차량 (picked는 이미 한가한 순)
    ordered_clusters = sorted(cluster_loads, key=lambda c: cluster_loads[c], reverse=True)
    return dict(zip(ordered_clusters, picked))


def save_assignments(conn: sqlite3.Connection, run_label: str, duration: str,
                     rows: Sequence[dict]) -> int:
    """회차 배정 결과를 기록한다(같은 회차를 다시 저장하면 대체)."""
    conn.execute("DELETE FROM vehicle_assignment WHERE run_label = ? AND duration = ?",
                 (run_label, duration))
    conn.executemany(
        "INSERT INTO vehicle_assignment"
        " (run_label, duration, vehicle_id, cluster, stations, bikes, distance_km, minutes)"
        " VALUES (:run_label, :duration, :vehicle_id, :cluster, :stations, :bikes,"
        "         :distance_km, :minutes)",
        [dict(row, run_label=run_label, duration=duration) for row in rows])
    conn.commit()
    return len(rows)


def assignment_history(conn: sqlite3.Connection, vehicle_id: Optional[str] = None,
                       run_label: Optional[str] = None) -> pd.DataFrame:
    """배정 이력(최신순). 차량이나 실행으로 좁힐 수 있다."""
    conditions, params = [], []
    if vehicle_id:
        conditions.append("vehicle_id = ?")
        params.append(vehicle_id)
    if run_label:
        conditions.append("run_label = ?")
        params.append(run_label)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return pd.read_sql(
        f"SELECT * FROM vehicle_assignment {where}"
        " ORDER BY run_label DESC, duration ASC, vehicle_id ASC", conn, params=params)


# ---------------- 대여이력 (대용량 원천 데이터) ----------------

_RENTAL_INDEXES = {
    "idx_rental_period": "CREATE INDEX IF NOT EXISTS idx_rental_period ON rental_history(period)",
    "idx_rental_rent_at": "CREATE INDEX IF NOT EXISTS idx_rental_rent_at ON rental_history(rent_at)",
    "idx_rental_station": "CREATE INDEX IF NOT EXISTS idx_rental_station ON rental_history(rent_station)",
}


def _ensure_rental_schema(conn: sqlite3.Connection) -> None:
    """rental_history가 현재 컬럼 구성과 다르면 다시 만든다.

    이 테이블은 원천 CSV에서 언제든 다시 적재할 수 있으므로, 구버전 스키마가
    남아 있을 때 알 수 없는 컬럼 오류를 내기보다 재생성하는 편이 안전하다.
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(rental_history)")}
    if not existing:
        return
    expected = set(RENTAL_COLUMNS.values()) | {"id", "period"}
    if existing != expected:
        print("[안내] rental_history 스키마가 달라 다시 만듭니다(원천에서 재적재 필요).")
        conn.execute("DROP TABLE rental_history")
        conn.executescript(SCHEMA)
        conn.commit()


def month_label(timestamp) -> str:
    """대여일시 → 'YY년 MM월' (project_config의 period 형식과 같다)."""
    return f"{timestamp.year % 100:02d}년 {timestamp.month:02d}월"


def bulk_load_rentals(csv_path: Path, period: Optional[str] = None,
                      db_path: Optional[Path] = None, chunksize: int = 100_000,
                      split_by_month: bool = False) -> Dict[str, int]:
    """원천 대여이력 CSV를 rental_history에 적재한다.

    수백만 행 규모를 염두에 두고 세 가지를 신경 쓴다.
    1. 청크 단위로 읽고 넣어 메모리에 전체를 올리지 않는다.
    2. 적재 전에 인덱스를 지우고 끝난 뒤 다시 만든다
       (인덱스가 걸린 채로 대량 삽입하면 매 행마다 B-Tree를 갱신해 훨씬 느리다).
    3. 같은 period를 다시 적재하면 기존 행을 지우고 넣는다(멱등).

    split_by_month=True면 대여일시에서 월을 뽑아 period를 행마다 정한다.
    1년치 병합 파일을 넣고 한 달씩 분석할 때 쓴다 — 계절이 다른 달을 섞어
    평균을 내면 목표 재고가 엉뚱해지기 때문이다.

    반환값은 {period: 적재 행 수}.
    """
    csv_path = Path(csv_path)
    if not csv_path.is_file():
        raise FileNotFoundError(f"원천 CSV가 없습니다: {csv_path}")
    if not split_by_month and not period:
        raise ValueError("period를 지정하거나 split_by_month=True를 쓰세요.")

    conn = connect(db_path)
    try:
        init_schema(conn)
        _ensure_rental_schema(conn)

        for name in _RENTAL_INDEXES:
            conn.execute(f"DROP INDEX IF EXISTS {name}")
        if not split_by_month:
            conn.execute("DELETE FROM rental_history WHERE period = ?", (period,))
        conn.commit()

        loaded: Dict[str, int] = {}
        cleared: set = set()

        for chunk in pd.read_csv(csv_path, encoding="utf-8-sig", low_memory=False,
                                 chunksize=chunksize):
            frame = _normalize_rentals(chunk, period, split_by_month)

            if split_by_month:
                # 각 월의 기존 데이터를 처음 만났을 때 한 번만 지운다(멱등).
                for label in frame["period"].dropna().unique():
                    if label not in cleared:
                        conn.execute("DELETE FROM rental_history WHERE period = ?", (label,))
                        cleared.add(label)

            frame.to_sql("rental_history", conn, if_exists="append", index=False)
            for label, count in frame["period"].value_counts().items():
                loaded[label] = loaded.get(label, 0) + int(count)

        for statement in _RENTAL_INDEXES.values():
            conn.execute(statement)
        conn.commit()
        return loaded
    finally:
        conn.close()


def _normalize_rentals(chunk: pd.DataFrame, period: Optional[str],
                       split_by_month: bool = False) -> pd.DataFrame:
    """CSV 한 청크를 테이블 컬럼 구성으로 바꾼다."""
    available = {k: v for k, v in RENTAL_COLUMNS.items() if k in chunk.columns}
    missing = set(RENTAL_COLUMNS) - set(available)
    if "대여일시" in missing or "대여_대여소ID" in missing:
        raise ValueError(f"원천 CSV에 필수 컬럼이 없습니다: {sorted(missing)}")

    frame = chunk[list(available)].rename(columns=available)

    # 문자열 비교로 기간 조회가 되도록 형식을 통일한다.
    parsed_rent = pd.to_datetime(frame["rent_at"], errors="coerce")
    for column in ("rent_at", "return_at"):
        if column in frame.columns:
            parsed = parsed_rent if column == "rent_at" else pd.to_datetime(
                frame[column], errors="coerce")
            frame[column] = parsed.dt.strftime(DATETIME_FORMAT)

    labels = parsed_rent.map(lambda t: month_label(t) if pd.notna(t) else None) \
        if split_by_month else period
    frame.insert(0, "period", labels)
    return frame.astype(object).where(pd.notna(frame), None)


def rental_count(conn: sqlite3.Connection, period: str) -> int:
    """해당 기간에 적재된 대여이력 행 수."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM rental_history WHERE period = ?", (period,)).fetchone()
    except sqlite3.OperationalError:
        return 0
    return row[0] if row else 0


def read_rental_source(period: str, csv_path: Optional[Path] = None,
                       db_path: Optional[Path] = None,
                       columns: Optional[Sequence[str]] = None) -> Tuple[pd.DataFrame, str]:
    """대여이력을 **원본 CSV의 한글 컬럼명 그대로** 돌려준다.

    DB에 해당 기간이 적재돼 있으면 DB에서, 없으면 CSV에서 읽는다.
    한글 컬럼으로 되돌려주는 이유는 기존 step0 계산 로직을 손대지 않기 위해서다
    (전환기 장치 — CSV 기록을 걷어낼 때 계산 로직도 ASCII 컬럼으로 정리한다).

    columns를 주면 그 컬럼만 읽는다. 60만 행 규모에서는 12개를 다 읽는 것과
    필요한 4~9개만 읽는 것의 차이가 크므로, 호출부가 쓰는 컬럼만 지정하는 것이 좋다.

    반환: (DataFrame, 출처) — 출처는 "db" | "csv" | "none"
    """
    requested = list(columns) if columns else list(RENTAL_COLUMNS)
    unknown = [c for c in requested if c not in RENTAL_COLUMNS]
    if unknown:
        raise KeyError(f"대여이력에 없는 컬럼: {unknown}")

    conn = connect(db_path)
    try:
        init_schema(conn)
        if rental_count(conn, period) > 0:
            selected = ", ".join(RENTAL_COLUMNS[c] for c in requested)
            frame = pd.read_sql(
                f"SELECT {selected} FROM rental_history WHERE period = ? ORDER BY id",
                conn, params=[period])
            frame = frame.rename(columns=RENTAL_COLUMNS_REVERSED)
            return frame[requested], "db"
    finally:
        conn.close()

    if csv_path is None:
        return pd.DataFrame(), "none"

    # utf-8-sig: BOM이 있으면 벗기고, 없으면 일반 utf-8로 읽는다.
    # 공공데이터 CSV는 BOM이 붙어 오는 경우가 많은데, 그냥 utf-8로 읽으면
    # 첫 컬럼명에 '﻿'가 붙어 컬럼을 못 찾는다.
    frame = pd.read_csv(csv_path, encoding="utf-8-sig", low_memory=False, usecols=requested)
    return frame[requested], "csv"      # usecols는 파일 순서를 따르므로 다시 정렬
