"""대여이력 DB 적재와 step0 전환 검증 (DB_PLAN 4단계).

가장 중요한 것은 **CSV로 계산한 결과와 DB로 계산한 결과가 완전히 같은가**이다.
저장소를 바꾸는 작업이므로 값이 달라지면 이관 자체가 실패다.

모든 테스트가 임시 DB를 쓴다(conftest.py의 autouse fixture + 명시적 경로).
산출물도 임시 자료 폴더(`PBR_DATA_ROOT`)에 쌓인다(2026-10-08 점검 — 그 전에는 사용자의
`data/pp_data`에 `rentaltest-*`를 만들고 teardown에서 치웠다).
"""
import os
import subprocess
import sys

import pandas as pd
import pytest

import db
from project_config import PROJECT_ROOT

PERIOD = f"rentaltest-{os.getpid()}"
NOW = PERIOD
DURATION = "_05_10"

# `sample`이 채운다 — 이 모듈의 하위 프로세스가 쓰는 임시 자료 폴더다.
_DATA_ROOT = None


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    """합성 대여이력 CSV와 재고 CSV를 만든다 — 진짜 `data/`가 아니라 임시 폴더에."""
    global _DATA_ROOT
    raw_path = tmp_path_factory.mktemp("raw") / "합성_대여이력.csv"
    _DATA_ROOT = tmp_path_factory.mktemp("data")
    # 하위 프로세스로 만든다 — `generate()`를 여기서 부르면 import 시점에 굳은
    # `DATA_ROOT`(=진짜 `data/`)에 대여소 파일이 쓰인다. 이때 생기는 `station_stock`은
    # 버리는 DB로 보낸다(아래 동일성 검증은 저마다 제 DB를 쓴다).
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               PBR_DATA_ROOT=str(_DATA_ROOT),
               PBR_DB_PATH=str(tmp_path_factory.mktemp("gen") / "gen.db"))
    made = subprocess.run(
        [sys.executable, "tools/make_sample_data.py",
         "--now", NOW, "--period", PERIOD, "--stations", "40",
         "--days", "6", "--rentals-per-day", "200", "--raw-file", str(raw_path)],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    if made.returncode != 0:
        pytest.fail("합성 데이터 생성 실패:" + made.stdout + made.stderr)
    return raw_path


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


def test_rental_periods_lists_only_loaded_periods(loaded_db):
    """`rental_periods()`는 DB에 실제로 적재된 기간만 담는다 (1.26.184).

    `/run` 폼이 이 목록으로 "원천 CSV는 안 씁니다"를 판단하므로, 적재 안 된
    기간이 섞이면 실제로는 CSV가 필요한데 안 쓴다고 잘못 알린다.
    """
    with db.session(loaded_db) as conn:
        periods = db.rental_periods(conn)

    assert PERIOD in periods
    assert f"{PERIOD}-없는기간" not in periods


def test_rental_periods_empty_before_any_load(tmp_path):
    """대여이력이 하나도 없는 새 DB는 빈 집합을 준다(예외가 아니다)."""
    with db.session(tmp_path / "empty.db") as conn:
        assert db.rental_periods(conn) == frozenset()


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
               PBR_DATA_ROOT=str(_DATA_ROOT), PBR_DB_PATH=str(db_path))
    completed = subprocess.run(
        [sys.executable, script, "--now", NOW, "--period", PERIOD,
         "--duration", DURATION, "--raw-file", str(raw_path)],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        pytest.fail(f"{script} 실패\n{completed.stdout[-1500:]}\n{completed.stderr[-1500:]}")


PARKING = "pipeline/step0_collect/extract_parking_lot.py"


@pytest.mark.parametrize("script, output, prerequisites", [
    ("pipeline/step0_collect/raw_to_net.py", "순수요/st_net_daily ({period}).csv", []),
    # api_to_info는 주차대수 산출물이 먼저 있어야 한다
    ("pipeline/step0_collect/api_to_info.py", "대여소 정보/st_info ({now}).csv", [PARKING]),
])
def test_csv_and_db_paths_produce_identical_output(
        sample, tmp_path_factory, script, output, prerequisites):
    """**핵심 검증**: 저장소를 바꿔도 계산 결과가 한 행도 달라지지 않아야 한다.

    같은 스크립트를 CSV 경로(빈 DB)와 DB 경로(적재된 DB)로 각각 실행해 산출물을 비교한다.
    """
    target = _DATA_ROOT / "pp_data" / output.format(period=PERIOD, now=NOW)

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


# ---------------------------------------------------------------- 월별 원본 (2026-09-26)
# 공공데이터포털의 월별 원본을 다시 받아 보니 두 가지가 있었다.
#  ① 달마다 인코딩이 다르다 — 20개 중 10개가 BOM 없는 cp949(적재기는 utf-8-sig 고정)
#  ② `(25년12월)` 파일 안에 2025년 1월 자료가 들어 있었다 — 이름을 믿으면 1월이 12월로 둔갑한다

def _write_rentals(path, stamps, encoding):
    frame = pd.DataFrame({
        "자전거번호": [f"DJ3-{i:04d}" for i in range(len(stamps))],
        "대여일시": stamps,
        "대여_대여소ID": ["ST0001"] * len(stamps),
        "반납일시": stamps,
        "반납_대여소ID": ["ST0002"] * len(stamps),
    })
    frame.to_csv(path, index=False, encoding=encoding)
    return path


def test_sniff_encoding_tells_cp949_from_utf8(tmp_path):
    utf = _write_rentals(tmp_path / "u.csv", ["2025-01-01 05:00:00"], "utf-8-sig")
    cp = _write_rentals(tmp_path / "c.csv", ["2025-01-01 05:00:00"], "cp949")
    assert db.sniff_csv_encoding(utf) == "utf-8-sig"
    assert db.sniff_csv_encoding(cp) == "cp949"


def test_bulk_load_reads_cp949_month(tmp_path):
    """원본의 절반이 cp949다 — 적재기가 첫 줄에서 죽으면 안 된다."""
    stamps = [f"2025-01-{d:02d} 08:00:00" for d in range(1, 11)]
    path = _write_rentals(tmp_path / "cp.csv", stamps, "cp949")
    loaded = db.bulk_load_rentals(path, period="25년 01월",
                                  db_path=tmp_path / "cp.db", chunksize=4)
    assert loaded == {"25년 01월": 10}


def test_bulk_load_refuses_file_whose_rows_are_another_month(tmp_path):
    """이름이 12월인데 내용이 1월이면 거부한다 — **이미 들어 있는 12월을 지우지 않고**."""
    db_path = tmp_path / "m.db"
    december = _write_rentals(tmp_path / "dec.csv", ["2025-12-05 08:00:00"] * 3, "utf-8-sig")
    db.bulk_load_rentals(december, period="25년 12월", db_path=db_path)

    january_named_december = _write_rentals(
        tmp_path / "bad.csv", ["2025-01-05 08:00:00"] * 5, "cp949")
    with pytest.raises(ValueError, match="25년 01월"):
        db.bulk_load_rentals(january_named_december, period="25년 12월", db_path=db_path)

    with db.session(db_path) as conn:
        assert db.rental_count(conn, "25년 12월") == 3


def test_load_directory_skips_mislabeled_file_and_keeps_going(tmp_path):
    """폴더 적재는 어긋난 파일 하나 때문에 멈추지 않고, 건너뛴 것을 따로 돌려준다."""
    from tools.load_rentals import load_directory

    folder = tmp_path / "raw"
    folder.mkdir()
    _write_rentals(folder / "정보(25년01월).csv", ["2025-01-02 08:00:00"] * 4, "cp949")
    _write_rentals(folder / "정보(25년12월).csv", ["2025-01-02 08:00:00"] * 4, "cp949")
    _write_rentals(folder / "정보(26년01월).csv", ["2026-01-02 08:00:00"] * 2, "utf-8-sig")

    loaded, skipped = load_directory(folder, db_path=tmp_path / "d.db")

    assert loaded == {"25년 01월": 4, "26년 01월": 2}
    assert [name for name, _ in skipped] == ["정보(25년12월).csv"]


def test_connection_waits_for_write_lock_instead_of_failing(tmp_path):
    """재고 수집기는 재시도가 없다 — 잠금에서 5초 만에 죽으면 그 틱을 영영 잃는다.

    2026-09-26 대여이력 재적재 중 11:00 틱이 그렇게 사라졌다.
    """
    with db.session(tmp_path / "t.db") as conn:
        waited_ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert waited_ms == db.BUSY_TIMEOUT_SEC * 1000
    assert db.BUSY_TIMEOUT_SEC >= 30


def test_load_directory_keeps_indexes(tmp_path, monkeypatch):
    """폴더 적재는 인덱스를 **지우지 않는다** — 수백만 행 표에서 달마다 다시 지으면
    그 한 문장이 쓰기 잠금을 오래 쥔다(재고 수집기가 틱을 잃는다)."""
    dropped = []
    real_connect = db.connect

    def spying_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(
            lambda sql: dropped.append(sql) if sql.lstrip().upper().startswith("DROP INDEX") else None)
        return conn

    monkeypatch.setattr(db, "connect", spying_connect)
    from tools.load_rentals import load_directory

    folder = tmp_path / "raw"
    folder.mkdir()
    _write_rentals(folder / "정보(25년01월).csv", ["2025-01-02 08:00:00"] * 4, "cp949")
    _write_rentals(folder / "정보(25년12월).csv", ["2025-01-02 08:00:00"] * 4, "cp949")
    db_path = tmp_path / "i.db"

    load_directory(folder, db_path=db_path)

    assert dropped == [], f"폴더 적재가 인덱스를 지웠다: {dropped}"
    with db.session(db_path) as conn:
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='rental_history'")}
    assert set(db._RENTAL_INDEXES) <= names
