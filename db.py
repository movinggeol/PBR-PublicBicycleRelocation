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

from project_config import (DATA_ROOT, FLEET_SIZE, holiday_mask,
                            period_label, vehicle_ids)

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
    # TMAP이 준 **실제 도로 소요시간**. 파이프라인의 추정치가 아니라 정답표다
    # (1.23.2). step3이 지도를 그리며 받은 값을 여기에 남긴다 —
    # 이것이 없으면 VEHICLE_SPEED_KMPH가 맞는지 검증할 방법이 없다.
    "road_leg": TableSpec(scope=("run_label", "duration")),
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
    day_type    TEXT,     -- weekday | holiday. 산출물 파일명에는 안 들어가므로 여기 남긴다
    kind        TEXT,     -- plan | experiment | probe (db.RUN_KINDS). NULL이면 라벨로 짐작한다
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
-- TMAP 실측 구간 (정답표). 파이프라인 추정치인 vrp_plan.travel_sec와 **다른 것**이다.
-- straight_km은 우리가 쓰는 직선거리, road_sec은 TMAP이 준 실제 소요시간이다.
-- 둘의 관계를 배우는 것이 ILP가 정말 필요로 하는 예측이다
-- (ILP 시점에는 도로거리를 모르고 직선거리만 안다).
CREATE TABLE IF NOT EXISTS road_leg (
    run_label    TEXT NOT NULL,
    duration     TEXT NOT NULL,
    cluster      INTEGER NOT NULL,
    leg          INTEGER NOT NULL,
    from_id      TEXT,
    to_id        TEXT,
    from_lat     REAL,
    from_lon     REAL,
    to_lat       REAL,
    to_lon       REAL,
    straight_km  REAL,
    road_sec     REAL,
    observed_at  TEXT,
    -- TMAP에 넘긴 `startTime`(YYYYMMDDHHMM). **이 값이 교통량을 정한다** —
    -- 같은 구간도 시각이 다르면 다른 답이 온다(1.26.4에서 실제로 겪었다).
    -- observed_at은 '언제 호출했나', start_time은 '언제의 교통량인가'로 서로 다르다.
    start_time   TEXT,
    PRIMARY KEY (run_label, duration, cluster, leg)
);

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
-- 실행 간 비교를 쿼리 하나로 만든다. (docs/분석/KPI.md)
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
    stockout_hours_after  REAL,      -- VRP가 **실제로 옮긴 양** 기준 (1.18.4~)
    stockout_hours_plan   REAL,      -- 계획량(rebal_qty)이 전부 집행됐다고 본 값
    -- 결품의 반대쪽. 재고 = 거치대라 **반납을 못 받는** 시간이다 (1.26.101).
    -- 결품만 보면 "채우면 좋다"가 되는데, 채워서 포화가 늘면 반납이 막힌다.
    saturation_hours_before REAL,
    saturation_hours_after  REAL,
    -- 순유출 대비 실제로 내준 비율. **순수요 기준이라 총 대여 대비가 아니다**
    -- (net_demand가 대여-반납이라 상쇄된 뒤의 값이다). 결품과 같은 하한 지표.
    demand_fulfill_before REAL,
    demand_fulfill_after  REAL,
    demand_mae            REAL,

    -- C. 운영 / D. 효율 (1.19.3, docs/분석/KPI.md)
    travel_time_ratio     REAL,      -- 이동시간 / 총 소요시간 (낮을수록 좋다)
    empty_distance_ratio  REAL,      -- 빈 차로 달린 거리 비율 (차고지 왕복 포함)
    depot_returns         INTEGER,   -- 복귀 행 수. 1.19.1부터 클러스터당 1건이 정상
    bikes_per_minute      REAL,      -- 분당 처리 대수 (작업 밀도)
    stations_total        INTEGER,   -- 이번 실행이 수집한 전체 대여소 수
    station_coverage      REAL,      -- 작업 대상 / 전체 대여소

    -- 목표까지의 격차 (1.19.7). target_met_ratio를 읽을 때 반드시 함께 본다.
    gap_median            REAL,      -- |목표 - 재고| 중앙값
    gap_max               REAL,      -- 그 최댓값 (적재 용량과 견줘 볼 것)
    reachable_ratio       REAL,      -- 격차 <= 적재 용량인 대여소 비율
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
-- 수요 예측 백테스트 (1.19.3, docs/분석/KPI.md E장).
-- 한 달로 만든 mu가 **다음 달**을 얼마나 맞히는지. tools/backtest_demand.py가 채운다.
-- 파이프라인 실행과 무관한 기록이라 run_label이 없다 — 축은 (시간대, 월쌍, 요일)이다.
-- 같은 축을 다시 재면 덮어쓴다. z·min_demand는 그때 쓴 설정이라 함께 남긴다.
CREATE TABLE IF NOT EXISTS demand_backtest (
    duration     TEXT NOT NULL,
    train_period TEXT NOT NULL,       -- mu를 만든 달
    test_period  TEXT NOT NULL,       -- 맞히려 한 달
    day_type     TEXT NOT NULL,       -- weekday | holiday (섞지 않는다)
    computed_at  TEXT NOT NULL,
    z            REAL,                -- 커버리지 판정에 쓴 계수
    min_demand   REAL,                -- 작업 대상 임계(0이면 전체 대여소)
    warmup_days  INTEGER,
    stations     INTEGER,
    mae          REAL,
    rmse         REAL,
    bias         REAL,                -- 양수면 과소예측
    mae_zero     REAL,                -- 기준선: 늘 0이라고 예측
    mae_global   REAL,                -- 기준선: 전체 평균으로 예측
    coverage     REAL,                -- mu + z*sigma가 실제를 덮은 비율
    z_for_95     REAL,                -- 95%를 덮으려면 필요한 z
    PRIMARY KEY (duration, train_period, test_period, day_type)
);

-- 재고 시계열 (1.20.0, docs/구현/COLLECTOR.md).
-- tools/collect_stock.py가 평일 09~17시에 10분마다 한 틱씩 쌓는다.
-- 파이프라인 실행과 무관한 관측 기록이라 run_label이 없다 — 축은 (시각, 대여소)다.
-- day_type·duration을 컬럼으로 두지 않는 이유: observed_at에서 파생되는 값이고,
-- 요일 판정은 project_config.holiday_mask() 하나가 독점해야 하기 때문이다.
CREATE TABLE IF NOT EXISTS stock_history (
    observed_at TEXT NOT NULL,       -- 'YYYY-MM-DD HH:MM'. 틱 격자에 맞춰 반올림한다
    station_id  TEXT NOT NULL,
    stock       INTEGER,
    fetched_at  TEXT,                -- 실제 호출 시각(진단용). 격자와 몇 초 어긋난다
    PRIMARY KEY (observed_at, station_id)
);

-- 대여소 이름·좌표는 틱마다 반복하지 않고 **하루 한 번**만 남긴다.
-- 1,374행 × 49틱마다 같은 문자열을 넣으면 용량이 몇 배가 된다.
-- station_stock에 얹지 않는 이유가 두 가지다:
--   1. latest_label()이 run_label의 **사전순** MAX라 'collect-…'가 파이프라인
--      실행 라벨을 밀어낸다(숫자로 시작하는 라벨보다 항상 크다).
--   2. runs 이력이 수집일마다 한 줄씩 늘어 실행 이력 화면이 흐려진다.
CREATE TABLE IF NOT EXISTS stock_station_master (
    observed_on   TEXT NOT NULL,     -- 'YYYY-MM-DD' 수집일
    station_id    TEXT NOT NULL,
    station_name  TEXT,
    parking_info  TEXT,
    lat           REAL,
    lon           REAL,
    PRIMARY KEY (observed_on, station_id)
);

-- 결품 지표 보정 계수 (1.26.1, docs/분석/KPI.md).
-- step4의 결품은 순수요로 **복원**한 값이라 재고 0에서 잘려 결품을 낮춰 잡는다.
-- 관측(stock_history)과 맞대어 그 격차를 계수로 남긴다.
--
-- **이 표가 있는 이유가 곧 이 설계의 요지다.** 수집을 멈추면 관측은 사라지지만
-- 계수는 남는다 — 언제 무엇으로 잰 보정인지를 함께 적어 두므로, 나중에도
-- "며칠치 관측으로 얻은 계수인가"를 밝히며 인용할 수 있다.
-- 관측이 없다고 지표가 무너지지 않게 하는 것이 목적이다.
CREATE TABLE IF NOT EXISTS stockout_calibration (
    measured_at TEXT NOT NULL,       -- 계수를 잰 시각
    duration    TEXT NOT NULL,       -- 회차(_05_10 등)
    day_type    TEXT NOT NULL,       -- weekday / holiday
    ratio       REAL,                -- 실측 / 복원. 1보다 크면 복원이 낮춰 잡은 것
    observed    REAL,                -- 관측 결품(h)
    simulated   REAL,                -- 복원 결품(h)
    days        INTEGER,             -- 근거가 된 온전한 날 수
    stations    INTEGER,             -- 대여소 수
    note        TEXT,                -- 관측 창 등 단서
    PRIMARY KEY (measured_at, duration, day_type)
);

CREATE INDEX IF NOT EXISTS idx_rental_station ON rental_history(rent_station);
CREATE INDEX IF NOT EXISTS idx_pick_drop_cluster ON pick_drop(run_label, duration, cluster);
CREATE INDEX IF NOT EXISTS idx_metrics_run ON metrics(run_label, duration);
CREATE INDEX IF NOT EXISTS idx_stock_history_station ON stock_history(station_id, observed_at);
"""


def active_db_path(db_path: Optional[Path] = None) -> Path:
    """**지금 실제로 열리는** DB 경로. 사람에게 보여줄 때는 반드시 이것을 쓴다.

    ⚠️ `DB_PATH`는 import 시점에 굳는 **기본값**이라, `PBR_DB_PATH`로 다른 DB를
    쓰고 있어도 그대로 기본 경로를 가리킨다. 그것을 화면에 찍으면 **어디에
    넣었는지 거짓말하는 안내문**이 된다 — 실제로 그랬고(1.26.51),
    `transfer_run.py`가 자기 안에 같은 함수를 만들어 막았다. 그런데 `csv_to_db`·
    `load_rentals`·`export_collected` 셋은 그대로 남아 있었다(1.26.143).
    한 도구가 배운 것이 옆 도구에 닿지 않으면 같은 거짓말이 계속 남는다.

    우선순위는 `connect()`와 **같아야 한다** — 아래에서 이 함수를 그대로 쓴다.
    """
    return Path(db_path or os.getenv("PBR_DB_PATH") or DB_PATH)


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """DB에 연결한다. 다른 DB로 옮길 때 교체할 지점은 이 함수 하나다.

    경로 우선순위: 인자 → 환경변수 PBR_DB_PATH → 기본값(data/bike_system.db).
    WAL 모드를 켜면 파이프라인이 쓰는 중에도 웹 대시보드가 읽을 수 있다.
    """
    path = active_db_path(db_path)
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
               duration: Optional[str] = None,
               kinds: Optional[Sequence[str]] = None) -> pd.DataFrame:
    """테이블을 읽는다.

    run_label/period를 생략하면 가장 최근 실행분을 돌려준다.
    `kinds`를 주면 그 종류의 실행 중에서 고른다(`latest_label` 참고) — 운영
    화면이 실험 결과를 계획으로 내놓지 않게 하는 장치다.

    **여기서 종류를 받는 이유**는 스코프 컬럼(`run_label` 대 `period`)을 고르는
    곳이 여기 하나이기 때문이다. 부르는 쪽에서 라벨을 미리 정해 넘기면 기간
    스코프 테이블에 실행 라벨을 넘기는 실수를 하기 쉽다.
    """
    spec = _spec(table)
    label_column = "period" if "period" in spec.scope else "run_label"
    label_value = period if label_column == "period" else run_label

    if label_value is None:
        label_value = latest_label(conn, table, kinds=kinds)
        if label_value is None:
            return pd.DataFrame()

    conditions = [f"{label_column} = ?"]
    params = [label_value]
    if "duration" in spec.scope and duration is not None:
        conditions.append("duration = ?")
        params.append(duration)

    query = f"SELECT * FROM {table} WHERE {' AND '.join(conditions)}"
    return pd.read_sql(query, conn, params=params)


def latest_label(conn: sqlite3.Connection, table: str,
                 kinds: Optional[Sequence[str]] = None) -> Optional[str]:
    """해당 테이블에 저장된 가장 최근 run_label(또는 period).

    **run_label은 `runs.created_at`으로 고른다 — 사전순 MAX가 아니다.**
    이 저장소는 *"문자열 정렬이 곧 최신순"* 을 규약으로 삼아 왔는데(라벨이
    `2026-08-27 23` 꼴이면 성립한다), **실험 라벨이 그 규약 밖에 있다** —
    `obs-cmp-…`는 `'o' > '2'`라 어떤 날짜 라벨도 이긴다. 그래서 실험을 한 번
    돌리고 나면 그 뒤로 계획을 아무리 돌려도 `MAX(run_label)`은 영영 실험을
    가리킨다(1.26.125에서 실측했다 — 여섯 테이블이 그 상태였다).

    라벨이 `runs`에 없으면(옛 자료) 시각을 모르므로 가장 오래된 것으로 친다.
    전부 모르면 사전순 tiebreak이 남아 **예전 동작 그대로**다.

    `kinds`를 주면 **그 종류의 실행 중에서** 고른다. 저장된 `runs.kind`가 없으면
    (선언 없이 돈 실행 — 1.26.114) `classify_run_label()`로 짐작해 판정한다.
    맞는 것이 하나도 없으면 **조용히 비우지 않고** 종류를 무시한 최신을 준다 —
    자료가 있는데 없다고 답하는 쪽이 더 나쁘다.

    기간(period) 스코프 테이블에는 종류가 없으므로 `kinds`를 무시한다.
    """
    spec = _spec(table)
    if "period" in spec.scope:
        row = conn.execute(f"SELECT MAX(period) FROM {table}").fetchone()
        return row[0] if row else None

    rows = conn.execute(
        f"SELECT t.run_label, r.kind FROM (SELECT DISTINCT run_label FROM {table}) t"
        " LEFT JOIN runs r ON r.run_label = t.run_label"
        " ORDER BY COALESCE(r.created_at, '') DESC, t.run_label DESC").fetchall()
    if not rows:
        return None
    if kinds:
        for label, kind in rows:
            if (kind or classify_run_label(label)) in kinds:
                return label
    return rows[0][0]


def read_step_output(table: str, csv_path, run_label: Optional[str] = None,
                     period: Optional[str] = None, duration: Optional[str] = None,
                     ) -> Tuple[pd.DataFrame, str]:
    """앞 단계 산출물을 **DB에서 먼저** 읽고, 없으면 그 CSV로 물러선다.

    🔴 **왜 있나 (1.26.166).** 파이프라인은 자기가 만든 산출물을 다음 단계에서
    `read_csv`로 되읽고 있었다 — CSV가 산출물이면서 **단계 간 배선**이기도 해서,
    이중 기록을 걷으려 하면 파이프라인이 먼저 끊겼다(1.26.163 조사). step4·step1은
    1.26.164에서 옮겼고, 이 함수는 그 방식을 step0·step2까지 넓히면서 **한 곳으로
    모은 것**이다.

    ⚠️ **CSV 폴백은 남긴다.** 원천 CSV가 있고 DB가 비어 있는 환경(새로 받은 저장소,
    DB 도입 이전 산출물)에서도 파이프라인이 돌아야 한다. `read_rental_source()`가
    대여이력에 대해 하는 것과 같은 규약이다.

    반환: (DataFrame, 출처) — 출처는 "db" | "csv" | "none".
    부르는 쪽이 비었는지 보고 건너뛸지 정한다 — 여기서 예외를 던지면 한쪽 후보만
    있는 시간대가 크래시가 된다.
    """
    frame = pd.DataFrame()
    try:
        with session() as conn:
            frame = load_frame(conn, table, run_label=run_label,
                               period=period, duration=duration)
    except Exception as err:
        print(f"[경고] {table} DB 조회 실패: {type(err).__name__}: {err}")

    if not frame.empty:
        return frame, "db"

    # 🔴 폴백은 **경로**만 받는다. 메모리 버퍼를 넘기면 `Path()`가
    # `TypeError: ... not 'StringIO'`로 죽는데, 그 문구로는 무엇이 잘못됐는지
    # 알 수 없다 — 2026-09-12에 실험 하네스가 이 길로 죽었다. 부르는 쪽이
    # 자료를 직접 들고 있다면 이 함수를 거치지 말고 그것을 써야 한다.
    if hasattr(csv_path, 'read'):
        raise TypeError(
            f"{table}: 폴백 경로 자리에 파일 객체가 왔습니다. "
            "자료를 직접 들고 있다면 read_step_output을 거치지 말고 "
            "그 자료를 쓰십시오 — 이 함수는 DB를 먼저 보므로 "
            "건네준 자료가 조용히 무시됩니다.")

    path = Path(csv_path)
    if path.is_file():
        return pd.read_csv(path, encoding="utf-8", low_memory=False), "csv"
    return pd.DataFrame(), "none"


def record_run(conn: sqlite3.Connection, run_label: str, period: Optional[str] = None,
               duration: Optional[str] = None, raw_file: Optional[str] = None,
               day_type: Optional[str] = None, kind: Optional[str] = None) -> None:
    """실행 메타데이터를 기록한다(같은 라벨이면 덮어쓴다).

    ⚠️ **`kind`만은 덮어쓰지 않는다.** 나머지 값은 파이프라인이 다시 계산해
    낼 수 있지만 종류는 **사람이 화면에서 못박은 것**이라 다시 만들어 낼 길이
    없다. `kind=None`으로 덮으면 `list_runs()`가 라벨 짐작으로 되돌아가,
    짐작과 다르게 정정해 둔 실행이 조용히 원래대로 돌아간다 —
    `tools/csv_to_db.py`가 `kind` 없이 재적재하면 실제로 그렇게 됐다.
    `ensure_run()`이 COALESCE로 지키는 것과 같은 규칙을 여기서도 지킨다.
    """
    conn.execute(
        "INSERT OR REPLACE INTO runs"
        " (run_label, period, duration, raw_file, day_type, kind, created_at)"
        " VALUES (?, ?, ?, ?, ?,"
        "         COALESCE(?, (SELECT kind FROM runs WHERE run_label = ?)),"
        "         datetime('now', 'localtime'))",
        (run_label, period, duration, raw_file, day_type, kind, run_label),
    )
    conn.commit()


def ensure_run(conn: sqlite3.Connection, run_label: str, period: Optional[str] = None,
               duration: Optional[str] = None, raw_file: Optional[str] = None,
               day_type: Optional[str] = None, kind: Optional[str] = None) -> None:
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
    # 부르는 쪽이 종류를 말하지 않았으면 **띄운 쪽이 선언한 것**을 쓴다.
    # step 스크립트는 자기가 계획인지 실험인지 알 수 없다 — 같은 파이프라인이
    # 둘 다 만들기 때문이다(실험은 `--now`에 실험 라벨을 주고 돌린다). 아는 것은
    # 띄우는 쪽뿐이라 run_pipeline이 `PBR_RUN_KIND`로 내려보낸다(1.26.114).
    # 선언이 없으면 예전처럼 NULL로 두고 `list_runs()`의 라벨 짐작에 맡긴다 —
    # 여기서 'plan'을 기본값으로 박으면 **선언을 잊은 실험이 계획으로 확정**되어
    # 짐작보다 나빠진다(짐작은 obs-cmp-*를 실험으로 맞힌다).
    if kind is None:
        kind = declared_run_kind()
    # day_type만 인자가 우선이다(COALESCE의 순서가 반대인 것에 주의).
    # 나머지는 '먼저 기록된 값을 지킨다'가 맞지만, day_type은 **산출물의 성격을
    # 규정**한다 — 같은 라벨을 다른 요일 구분으로 다시 돌리면 산출물이 덮어써지므로
    # 기록도 따라가야 한다. 안 그러면 휴일 산출물에 '평일'이라고 적혀 남는다.
    conn.execute(
        "UPDATE runs SET period = COALESCE(period, ?),"
        "                duration = COALESCE(duration, ?),"
        "                raw_file = COALESCE(raw_file, ?),"
        "                day_type = COALESCE(?, day_type),"
        # kind는 **부르는 쪽이 알 때만** 넘어온다(수집기는 'probe'를 안다).
        # 먼저 못박힌 종류를 나중 단계가 지우지 않게 COALESCE 순서를 지킨다.
        "                kind = COALESCE(kind, ?)"
        " WHERE run_label = ?",
        (period, duration, raw_file, day_type, kind, run_label),
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


# ---------------- 실행의 종류 (1.26.107) ----------------
#
# 한 DB에 세 가지가 섞여 쌓인다. 이것을 가르지 않아서 화면이 여러 번 거짓말했다:
#
#   plan       파이프라인이 낸 재배치 계획. 화면이 보여 주려는 것.
#   experiment 파라미터 스윕·대조군처럼 **계획 모양이지만 운영이 아닌** 실행.
#   probe      계획이 아예 아닌 기록(도로 시간 수집 등). 지표도 경로도 없다.
#
# 예전에는 종류를 **웹 라우트 안의 하드코딩 목록**으로 짐작했다. 목록에 없던
# `obs-cmp-1520`이 첫 화면 헤드라인에 "마지막 계획"으로 경고 없이 올라왔고,
# `roadprobe-*`는 계획 필터에 계획인 척 섞여 눌러도 빈 표만 나왔다.
RUN_KINDS = ("plan", "experiment", "probe")


def declared_run_kind() -> Optional[str]:
    """띄운 쪽이 선언한 실행 종류(`PBR_RUN_KIND`). 선언이 없으면 None.

    **step 스크립트는 자기가 계획인지 실험인지 알 수 없다.** 같은 파이프라인이
    둘 다 만들기 때문이다 — 실험은 `--now`에 실험 라벨을 주고 돌린 것일 뿐,
    코드 경로는 똑같다. 아는 것은 **띄우는 쪽**뿐이라 그쪽이 선언하게 하고
    (`run_pipeline.py --run-kind`, 웹 실행 폼은 언제나 plan), 여기서는 그 선언을
    읽기만 한다.

    모르는 값이 들어오면 **조용히 넘기지 않고** 예외를 낸다. 오타로 선언한 종류가
    NULL로 떨어지면 짐작으로 되돌아가는데, 그 되돌아감이 보이지 않는다.
    """
    declared = os.getenv("PBR_RUN_KIND", "").strip()
    if not declared:
        return None
    if declared not in RUN_KINDS:
        raise ValueError(
            f"PBR_RUN_KIND는 {' · '.join(RUN_KINDS)} 중 하나여야 합니다"
            f" (받은 값: {declared!r}).")
    return declared

# 컬럼이 생기기 **전에** 쌓인 행을 위한 짐작. 앞으로 만드는 실행은 kind를
# 직접 넣으므로 여기 기대지 않는다 — 목록을 늘려 가며 버티는 것이 원래 문제였다.
_LEGACY_PROBE_PREFIXES = ("roadprobe-", "stockprobe-")
_LEGACY_EXPERIMENT_MARKS = (
    "sweep", "test", "테스트", "obs-cmp", "baseline", "ablation",
    "g1000", "g2000", "g3000", "g5000", "z165", "z199",
)


def classify_run_label(run_label: str) -> str:
    """라벨만 보고 실행 종류를 짐작한다. **`kind`가 비어 있을 때만 쓴다.**"""
    label = str(run_label).lower()
    if any(label.startswith(p) for p in _LEGACY_PROBE_PREFIXES):
        return "probe"
    if any(mark in label for mark in _LEGACY_EXPERIMENT_MARKS):
        return "experiment"
    return "plan"


def run_day_type(conn: sqlite3.Connection, run_label: str) -> Optional[str]:
    """그 실행이 **어느 요일 구분으로 계획됐는지**. 기록이 없으면 None.

    분석 쪽이 이것을 안 읽으면 `get_runtime_config()`의 기본값을 쓰는데, 그
    기본값은 `auto` — **오늘 달력**이다. 그래서 일요일에 돌린 문턱 스윕이
    평일 계획을 **휴일 순수요로 채점하고** 있었다(1.26.127). 같은 스크립트가
    월요일에는 다른 답을 냈다.

    `pipeline/step4_metrics/imbalance.py`의 `load_net_demand()`가 *"계획과 같은 요일
    구분만 남긴다"* 고 적어 둔 바로 그 실패다 — 정답은 여기 저장돼 있었다.
    """
    row = conn.execute("SELECT day_type FROM runs WHERE run_label = ?",
                       (run_label,)).fetchone()
    return row[0] if row and row[0] else None


def set_run_kind(conn: sqlite3.Connection, run_label: str, kind: str) -> None:
    """실행 종류를 못박는다. 라벨 규칙에 기대지 않는 유일한 방법이다."""
    if kind not in RUN_KINDS:
        raise ValueError(f"모르는 실행 종류: {kind!r} (가능: {', '.join(RUN_KINDS)})")
    conn.execute("UPDATE runs SET kind = ? WHERE run_label = ?", (kind, run_label))
    conn.commit()


def list_runs(conn: sqlite3.Connection) -> pd.DataFrame:
    """실행 이력을 최신순으로. `kind`가 비어 있으면 라벨로 채워서 돌려준다.

    화면이 종류를 다시 짐작하지 않게 **여기서 한 번만** 채운다 — 라우트마다
    따로 짐작하면 화면마다 다른 답이 나온다(그래서 겪은 일이 1.26.107이다).
    """
    frame = pd.read_sql("SELECT * FROM runs ORDER BY run_label DESC", conn)
    if frame.empty:
        return frame
    if "kind" not in frame.columns:
        frame["kind"] = None
    guessed = frame["run_label"].map(classify_run_label)
    frame["kind"] = frame["kind"].where(frame["kind"].notna(), guessed)
    return frame


# ---------------- 성과 지표 (docs/분석/KPI.md) ----------------

# kpi_summary에 저장할 수 있는 컬럼(키·시각 제외). 넘겨받은 dict에서 이것만 골라 쓴다.
KPI_FIELDS = (
    "stations", "clusters", "vehicles_used", "bikes_moved",
    "avg_improvement_rate", "pick_improvement_rate", "drop_improvement_rate",
    "target_met_ratio", "total_distance_km", "max_cluster_minutes",
    "avg_cluster_minutes", "time_budget_minutes", "time_budget_met",
    "vehicle_load_gap", "improvement_per_km", "cluster_max_imbalance",
    "stockout_hours_before", "stockout_hours_after", "stockout_hours_plan",
    "saturation_hours_before", "saturation_hours_after",
    "demand_fulfill_before", "demand_fulfill_after",
    "demand_mae",
    # 운영·효율 지표 (1.19.3, docs/분석/KPI.md C·D장)
    "travel_time_ratio", "empty_distance_ratio", "depot_returns",
    "bikes_per_minute", "stations_total", "station_coverage",
    # 목표까지의 격차와 '한 번에 닿는 범위' (1.19.7)
    "reachable_ratio", "gap_median", "gap_max",
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


BACKTEST_FIELDS = (
    "z", "min_demand", "warmup_days", "stations", "mae", "rmse", "bias",
    "mae_zero", "mae_global", "coverage", "z_for_95",
)


def save_backtest(conn: sqlite3.Connection, rows: list) -> int:
    """백테스트 결과를 기록한다(같은 시간대·월쌍·요일이면 덮어쓴다).

    rows의 각 항목은 duration/train_period/test_period/day_type을 반드시 갖고,
    나머지는 BACKTEST_FIELDS 중 있는 것만 저장한다 — 못 구한 값은 NULL로 남는다.
    """
    saved = 0
    for row in rows:
        known = {k: row[k] for k in BACKTEST_FIELDS if k in row and row[k] is not None}
        columns = ["duration", "train_period", "test_period", "day_type",
                   "computed_at", *known]
        placeholders = ", ".join(["?", "?", "?", "?", "datetime('now', 'localtime')"]
                                 + ["?"] * len(known))
        conn.execute(
            f"INSERT OR REPLACE INTO demand_backtest ({', '.join(columns)})"
            f" VALUES ({placeholders})",
            (row["duration"], row["train_period"], row["test_period"],
             row["day_type"], *known.values()),
        )
        saved += 1
    conn.commit()
    return saved


def load_backtest(conn: sqlite3.Connection,
                  day_type: Optional[str] = None) -> pd.DataFrame:
    """백테스트 결과를 읽는다(시간대, 검증 월 순)."""
    where = "WHERE day_type = ?" if day_type else ""
    params = [day_type] if day_type else []
    return pd.read_sql(
        f"SELECT * FROM demand_backtest {where}"
        " ORDER BY duration ASC, test_period ASC", conn, params=params)


# ---- 재고 시계열 (docs/구현/COLLECTOR.md) ----
# TABLES/save_frame 규약을 쓰지 않는다 — 그쪽은 run_label 스코프 산출물 전용이고,
# 여기 축은 (시각, 대여소)다. demand_backtest와 같은 자리다.


def _day_bounds(value: Optional[str], *, end: bool) -> Optional[str]:
    """'YYYY-MM-DD'만 주면 그날 전체를 덮도록 시각까지 채운다.

    이걸 안 하면 end='2026-08-24'가 '2026-08-24 00:00'으로 비교돼 그날이 통째로
    빠진다 — 문자열 비교라 조용히 틀린다.
    """
    if not value:
        return None
    return f"{value} 23:59" if end and len(value) == 10 else value


def save_stock_snapshot(conn: sqlite3.Connection, observed_at: str,
                        frame: pd.DataFrame,
                        fetched_at: Optional[str] = None) -> int:
    """한 틱의 재고를 기록한다. 저장한 행 수를 돌려준다.

    같은 (시각, 대여소)면 덮어쓰므로 **재실행이 안전하다** — 스케줄러가 중복
    실행해도, 나중에 손으로 다시 돌려도 행이 쌓이지 않는다.
    """
    if frame.empty:
        return 0
    stocks = pd.to_numeric(frame["stock"], errors="coerce").fillna(0).astype(int)
    rows = [(observed_at, str(station), int(stock), fetched_at)
            for station, stock in zip(frame["station_id"], stocks)]
    conn.executemany(
        "INSERT OR REPLACE INTO stock_history"
        " (observed_at, station_id, stock, fetched_at) VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    return len(rows)


def save_stock_master(conn: sqlite3.Connection, observed_on: str,
                      frame: pd.DataFrame) -> int:
    """그날의 대여소 이름·좌표를 남긴다(하루 한 번). 신설·폐지 추적도 겸한다."""
    if frame.empty:
        return 0
    columns = ("station_name", "parking_info", "lat", "lon")
    rows = []
    for record in frame.to_dict("records"):
        rows.append((observed_on, str(record["station_id"]),
                     *(record.get(column) for column in columns)))
    conn.executemany(
        "INSERT OR REPLACE INTO stock_station_master"
        " (observed_on, station_id, station_name, parking_info, lat, lon)"
        " VALUES (?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    return len(rows)


def has_stock_master(conn: sqlite3.Connection, observed_on: str) -> bool:
    """그날 마스터를 이미 남겼는가. 하루 첫 틱을 판단하는 데 쓴다."""
    row = conn.execute(
        "SELECT 1 FROM stock_station_master WHERE observed_on = ? LIMIT 1",
        (observed_on,)).fetchone()
    return row is not None


def load_stock_history(conn: sqlite3.Connection, start: Optional[str] = None,
                       end: Optional[str] = None,
                       stations: Optional[Sequence[str]] = None,
                       day_type: Optional[str] = None) -> pd.DataFrame:
    """재고 시계열을 읽는다. start/end는 'YYYY-MM-DD' 또는 'YYYY-MM-DD HH:MM'.

    day_type을 주면 평일 또는 휴일만 남긴다. **판정은 holiday_mask() 하나를 쓴다** —
    요일 규칙이 두 곳으로 갈리면 저장된 값과 계산이 조용히 어긋난다.
    """
    conditions, params = [], []
    if (lower := _day_bounds(start, end=False)):
        conditions.append("observed_at >= ?")
        params.append(lower)
    if (upper := _day_bounds(end, end=True)):
        conditions.append("observed_at <= ?")
        params.append(upper)
    if stations:
        conditions.append(f"station_id IN ({', '.join('?' * len(stations))})")
        params.extend(stations)

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    frame = pd.read_sql(
        f"SELECT observed_at, station_id, stock, fetched_at FROM stock_history{where}"
        " ORDER BY observed_at ASC, station_id ASC", conn, params=params)

    if day_type and not frame.empty:
        mask = holiday_mask(frame["observed_at"])
        frame = frame[mask if day_type == "holiday" else ~mask].reset_index(drop=True)
    return frame


def save_stockout_calibration(conn: sqlite3.Connection, rows: list) -> int:
    """결품 보정 계수를 남긴다 (1.26.1).

    **덮어쓰지 않고 쌓는다.** 계수는 관측이 늘수록 달라지는데, 과거 값을 지우면
    "그때는 무엇으로 재서 그 수치를 썼나"를 되짚을 수 없다. 논문에 인용한 값이
    조용히 바뀌는 것을 막는다.
    """
    if not rows:
        return 0
    conn.executemany(
        "INSERT OR REPLACE INTO stockout_calibration"
        " (measured_at, duration, day_type, ratio, observed, simulated,"
        "  days, stations, note)"
        " VALUES (:measured_at, :duration, :day_type, :ratio, :observed,"
        "         :simulated, :days, :stations, :note)", rows)
    conn.commit()
    return len(rows)


def latest_stockout_calibration(conn: sqlite3.Connection,
                                day_type: str = "weekday") -> pd.DataFrame:
    """회차마다 **가장 최근** 보정 계수 한 줄씩.

    수집이 멈춰도 마지막 계수가 남는다 — 그것이 이 표를 둔 이유다.
    쓰는 쪽은 `days`(근거가 된 날 수)를 함께 보고 신뢰도를 판단해야 한다.
    """
    return pd.read_sql(
        "SELECT c.* FROM stockout_calibration c"
        " JOIN (SELECT duration, MAX(measured_at) AS m"
        "         FROM stockout_calibration WHERE day_type = ?"
        "        GROUP BY duration) t"
        "   ON c.duration = t.duration AND c.measured_at = t.m"
        " WHERE c.day_type = ?"
        " ORDER BY c.duration", conn, params=(day_type, day_type))


def stock_history_ticks(conn: sqlite3.Connection, start: Optional[str] = None,
                        end: Optional[str] = None) -> pd.DataFrame:
    """틱별 수집 행 수. 컬럼: observed_at, stations.

    결측 판정의 원재료다 — **절전으로 놓친 틱은 수집 로그에도 안 남으므로**
    (스크립트 자체가 안 돌았다) 기대 격자와 이 결과를 대조해야 알 수 있다.
    """
    conditions, params = [], []
    if (lower := _day_bounds(start, end=False)):
        conditions.append("observed_at >= ?")
        params.append(lower)
    if (upper := _day_bounds(end, end=True)):
        conditions.append("observed_at <= ?")
        params.append(upper)
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    return pd.read_sql(
        f"SELECT observed_at, COUNT(*) AS stations FROM stock_history{where}"
        " GROUP BY observed_at ORDER BY observed_at ASC", conn, params=params)


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


def _assignment_filter(vehicle_id: Optional[str], run_label: Optional[str]) -> tuple:
    """배정 이력을 좁히는 조건. 세는 쪽과 읽는 쪽이 **같은 조건**을 써야 한다 —
    갈리면 '몇 건 더 있다'가 실제 쪽 수와 어긋난다."""
    conditions, params = [], []
    if vehicle_id:
        conditions.append("vehicle_id = ?")
        params.append(vehicle_id)
    if run_label:
        conditions.append("run_label = ?")
        params.append(run_label)
    return (f"WHERE {' AND '.join(conditions)}" if conditions else ""), params


def count_assignments(conn: sqlite3.Connection, vehicle_id: Optional[str] = None,
                      run_label: Optional[str] = None) -> int:
    """조건에 맞는 배정 이력 **전체 건수**. 쪽 수를 셀 때 쓴다.

    행을 다 읽어 `len()`을 재면 상한을 두는 뜻이 없어진다 — 세는 것은 DB가 한다.
    """
    where, params = _assignment_filter(vehicle_id, run_label)
    return conn.execute(
        f"SELECT COUNT(*) FROM vehicle_assignment {where}", params).fetchone()[0]


def assignment_history(conn: sqlite3.Connection, vehicle_id: Optional[str] = None,
                       run_label: Optional[str] = None, limit: Optional[int] = None,
                       offset: int = 0) -> pd.DataFrame:
    """배정 이력. 차량이나 실행으로 좁히고, `limit`/`offset`으로 쪽을 끊는다.

    ⚠️ **상한은 SQL에 건다.** 예전에는 표 전체를 DataFrame으로 읽어 온 뒤
    화면단에서 앞 200행만 잘랐는데, 그러면 실행이 쌓일수록 읽는 양이 계속 늘고
    잘린 나머지는 **닿을 길이 없었다.** 상한을 올리는 것은 미루기일 뿐이다
    (실측: 실행 1건이 평균 17.2행이라 200은 12회 실행분이다, 1.26.116).

    ⚠️ **정렬은 `run_label` 사전순이다.** `created_at`이 아니다 — 이 저장소는
    `list_runs()`부터 같은 규약을 쓰므로 여기만 바꾸면 화면끼리 순서가 갈린다.
    다만 사전순이라 `obs-cmp-*`가 `2026-*`보다 앞에 온다는 것은 알고 있어야
    한다(쪽 나눔은 전부를 훑으므로 빠지는 행은 없다).
    """
    where, params = _assignment_filter(vehicle_id, run_label)
    window = ""
    if limit is not None:
        window = " LIMIT ? OFFSET ?"
        params = params + [int(limit), int(offset)]
    return pd.read_sql(
        f"SELECT * FROM vehicle_assignment {where}"
        " ORDER BY run_label DESC, duration ASC, vehicle_id ASC" + window,
        conn, params=params)


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
    """대여일시 → 'YY년 MM월'. 표기 규칙은 project_config가 갖는다(중복 방지)."""
    return period_label(timestamp)


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
