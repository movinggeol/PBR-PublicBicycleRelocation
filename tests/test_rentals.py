"""대여이력 DB 적재와 step0 전환 검증 (DB_PLAN 4단계).

가장 중요한 것은 **CSV로 계산한 결과와 DB로 계산한 결과가 완전히 같은가**이다.
저장소를 바꾸는 작업이므로 값이 달라지면 이관 자체가 실패다.

모든 테스트가 임시 DB를 쓴다(conftest.py의 autouse fixture + 명시적 경로).
"""
import os
import subprocess
import sys

import pandas as pd
import pytest

import db
from project_config import PP_ROOT, PROJECT_ROOT
from tools.make_sample_data import generate

PERIOD = f"rentaltest-{os.getpid()}"
NOW = PERIOD
DURATION = "_05_10"


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    """합성 대여이력 CSV와 재고 CSV를 만든다."""
    raw_path = tmp_path_factory.mktemp("raw") / "합성_대여이력.csv"
    generate(now=NOW, period=PERIOD, stations=40, days=6,
             rentals_per_day=200, raw_path=raw_path)
    yield raw_path
    for path in PP_ROOT.rglob(f"*{PERIOD}*"):
        if path.is_file():
            path.unlink()


@pytest.fixture(scope="module")
def loaded_db(sample, tmp_path_factory):
    """대여이력을 적재한 DB 경로."""
    db_path = tmp_path_factory.mktemp("db") / "rentals.db"
    loaded = db.bulk_load_rentals(sample, period=PERIOD, db_path=db_path, chunksize=500)
    assert loaded[PERIOD] > 0
    return db_path


def test_bulk_load_stores_all_rows(sample, loaded_db):
    csv_rows = len(pd.read_csv(sample, encoding="utf-8"))
    with db.session(loaded_db) as conn:
        assert db.rental_count(conn, PERIOD) == csv_rows


def test_bulk_load_is_idempotent(sample, loaded_db):
    """같은 기간을 다시 적재해도 행이 늘지 않는다."""
    with db.session(loaded_db) as conn:
        before = db.rental_count(conn, PERIOD)

    db.bulk_load_rentals(sample, period=PERIOD, db_path=loaded_db, chunksize=500)

    with db.session(loaded_db) as conn:
        assert db.rental_count(conn, PERIOD) == before


def test_indexes_exist_after_load(loaded_db):
    """대량 적재 중 지웠던 인덱스가 끝나고 복구된다."""
    with db.session(loaded_db) as conn:
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_rental_period", "idx_rental_rent_at", "idx_rental_station"} <= names


def test_datetime_is_normalized(loaded_db):
    """문자열 범위 조회가 되도록 형식이 통일된다."""
    with db.session(loaded_db) as conn:
        sample_at = conn.execute(
            "SELECT rent_at FROM rental_history WHERE period = ? LIMIT 1", (PERIOD,)
        ).fetchone()[0]
        ranged = conn.execute(
            "SELECT COUNT(*) FROM rental_history"
            " WHERE period = ? AND rent_at BETWEEN ? AND ?",
            (PERIOD, "2025-11-01 00:00:00", "2025-12-01 00:00:00")).fetchone()[0]

    pd.Timestamp(sample_at)                       # 파싱 가능해야 한다
    assert len(sample_at) == 19                   # 'YYYY-MM-DD HH:MM:SS'
    assert ranged > 0                             # 범위 조회가 실제로 걸린다


def test_period_isolation(sample, loaded_db):
    """다른 기간을 적재해도 기존 기간이 남아 있다."""
    other = f"{PERIOD}-other"
    db.bulk_load_rentals(sample, period=other, db_path=loaded_db, chunksize=500)

    with db.session(loaded_db) as conn:
        assert db.rental_count(conn, PERIOD) > 0
        assert db.rental_count(conn, other) > 0
        conn.execute("DELETE FROM rental_history WHERE period = ?", (other,))


def test_read_source_returns_original_columns(sample, loaded_db):
    """DB에서 읽어도 원본 CSV와 같은 컬럼 구성이어야 기존 계산 코드가 그대로 돈다."""
    csv_frame = pd.read_csv(sample, encoding="utf-8")
    db_frame, source = db.read_rental_source(PERIOD, csv_path=sample, db_path=loaded_db)

    assert source == "db"
    assert set(db_frame.columns) == set(csv_frame.columns)
    assert len(db_frame) == len(csv_frame)


def test_split_by_month_assigns_period_per_row(sample, tmp_path):
    """병합 파일을 월별로 나눠 적재한다 — 계절이 다른 달을 섞지 않기 위해서다."""
    loaded = db.bulk_load_rentals(sample, db_path=tmp_path / "split.db",
                                  chunksize=500, split_by_month=True)

    assert loaded, "적재된 기간이 없다"
    assert all(label.endswith("월") for label in loaded), f"기간 라벨 형식 오류: {list(loaded)}"

    with db.session(tmp_path / "split.db") as conn:
        for label, count in loaded.items():
            assert db.rental_count(conn, label) == count
        # 각 기간의 대여일시가 실제로 그 달인지
        row = conn.execute(
            "SELECT period, rent_at FROM rental_history LIMIT 1").fetchone()
        assert db.month_label(pd.Timestamp(row[1])) == row[0]


def test_read_source_falls_back_to_csv(sample, tmp_path):
    """적재되지 않은 기간은 CSV로 폴백한다."""
    frame, source = db.read_rental_source(
        "적재되지-않은-기간", csv_path=sample, db_path=tmp_path / "empty.db")

    assert source == "csv"
    assert not frame.empty


def _run_step0(script: str, raw_path, db_path) -> None:
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               PBR_DB_PATH=str(db_path))
    completed = subprocess.run(
        [sys.executable, script, "--now", NOW, "--period", PERIOD,
         "--duration", DURATION, "--raw-file", str(raw_path)],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        pytest.fail(f"{script} 실패\n{completed.stdout[-1500:]}\n{completed.stderr[-1500:]}")


PARKING = "step0_collect/extract_parking_lot.py"


@pytest.mark.parametrize("script, output, prerequisites", [
    ("step0_collect/raw_to_net.py", "순수요/st_net_daily ({period}).csv", []),
    # api_to_info는 주차대수 산출물이 먼저 있어야 한다
    ("step0_collect/api_to_info.py", "대여소 정보/st_info ({now}).csv", [PARKING]),
])
def test_csv_and_db_paths_produce_identical_output(
        sample, tmp_path_factory, script, output, prerequisites):
    """**핵심 검증**: 저장소를 바꿔도 계산 결과가 한 행도 달라지지 않아야 한다.

    같은 스크립트를 CSV 경로(빈 DB)와 DB 경로(적재된 DB)로 각각 실행해 산출물을 비교한다.
    """
    target = PP_ROOT / output.format(period=PERIOD, now=NOW)

    # 1) CSV 경로 — DB가 비어 있어 폴백한다
    empty_db = tmp_path_factory.mktemp("csvpath") / "empty.db"
    for step in prerequisites:
        _run_step0(step, sample, empty_db)
    _run_step0(script, sample, empty_db)
    from_csv = pd.read_csv(target, encoding="utf-8")

    # 2) DB 경로 — 대여이력이 적재된 DB
    loaded = tmp_path_factory.mktemp("dbpath") / "loaded.db"
    db.bulk_load_rentals(sample, period=PERIOD, db_path=loaded, chunksize=500)
    for step in prerequisites:
        _run_step0(step, sample, loaded)
    _run_step0(script, sample, loaded)
    from_db = pd.read_csv(target, encoding="utf-8")

    pd.testing.assert_frame_equal(from_csv, from_db)
