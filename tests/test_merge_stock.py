"""두 PC 재고 시계열 병합 회귀 테스트 (docs/구현/COLLECTOR.md 11장).

지키는 것:
  - **먼저 수집한 것이 이긴다** — 로컬에 이미 있는 틱은 병합이 덮어쓰지 않는다.
    수집기 본체(`INSERT OR REPLACE`)와 정반대라, 한쪽을 고치다 다른 쪽 규칙을
    옮겨 붙이면 A PC 값이 B PC 값으로 조용히 바뀐다.
  - **비어 있는 틱은 채운다** — 그게 병합의 존재 이유다.
  - **CSV·DB 두 원천을 모두 읽는다** — CSV는 가볍고, DB는 마스터까지 온다.
  - **마스터 결손을 알린다** — CSV로만 옮기면 이름·좌표가 없는 날이 생긴다.
    말없이 넘어가면 나중에 좌표 없는 날짜를 분석에 넣게 된다.

DB는 conftest의 isolate_db가 임시 경로로 돌린다. API는 부르지 않는다.
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import db
import tools.merge_stock as merge

WEEKDAY = "2026-08-24"        # 월요일
SUNDAY = "2026-08-23"         # 일요일 — B PC만 관측하는 휴일


def sample_frame(stocks=(3, 0, 12)) -> pd.DataFrame:
    """tashu.fetch_stations()가 돌려주는 형태 (마스터 컬럼 포함)."""
    return pd.DataFrame({
        "station_id": ["ST0001", "ST0002", "ST0003"],
        "station_name": ["타슈 관제센터", "탄방동 한사랑병원", "둔산동 시청"],
        "parking_info": ["10대용*1 / 10", "10대용*1 / 10", "20대용*1 / 20"],
        "lat": [36.3504, 36.3484, 36.3601],
        "lon": [127.3845, 127.3900, 127.3850],
        "stock": list(stocks),
    })


def write_csv(directory: Path, day: str, ticks: dict) -> Path:
    """B PC의 일별 CSV 백업을 흉내 낸다: {'09:00': (재고, 재고, 재고), ...}."""
    directory.mkdir(parents=True, exist_ok=True)
    rows = []
    for clock, stocks in ticks.items():
        for station, stock in zip(("ST0001", "ST0002", "ST0003"), stocks):
            rows.append({"observed_at": f"{day} {clock}",
                         "station_id": station, "stock": stock})
    path = directory / f"stock_{day}.csv"
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8")
    return path


def make_source_db(path: Path, day: str, ticks: dict, master=True) -> Path:
    """B PC의 DB를 흉내 낸다. 스키마는 db.SCHEMA를 그대로 쓴다."""
    conn = sqlite3.connect(path)
    try:
        db.init_schema(conn)
        for clock, stocks in ticks.items():
            db.save_stock_snapshot(conn, f"{day} {clock}", sample_frame(stocks))
        if master:
            db.save_stock_master(conn, day, sample_frame())
    finally:
        conn.close()
    return path


def stock_at(observed_at: str, station: str = "ST0001"):
    with db.session() as conn:
        row = conn.execute(
            "SELECT stock FROM stock_history WHERE observed_at = ? AND station_id = ?",
            (observed_at, station)).fetchone()
    return row[0] if row else None


# ---- 먼저 수집한 것이 이긴다 ----

def test_이미_있는_틱은_덮어쓰지_않는다(tmp_path):
    """A PC가 이미 잰 값이 B PC 값으로 바뀌면 안 된다.

    수집기 본체는 INSERT OR REPLACE라 반대로 동작한다 — 그 규칙을 여기 옮겨
    붙이면 이 테스트가 깨진다.
    """
    with db.session() as conn:
        db.save_stock_snapshot(conn, f"{WEEKDAY} 09:00", sample_frame((3, 0, 12)))

    source = tmp_path / "b_pc"
    write_csv(source, WEEKDAY, {"09:00": (99, 99, 99)})
    history, _, _, _ = merge.load_source(source)
    with db.session() as conn:
        added = merge.merge_history(conn, merge.normalize(history))

    assert added == 0
    assert stock_at(f"{WEEKDAY} 09:00") == 3     # A PC 값 그대로


def test_비어_있는_틱은_채운다(tmp_path):
    """병합의 존재 이유. A가 09:00만 있고 B가 08:00을 가져오면 08:00이 들어온다."""
    with db.session() as conn:
        db.save_stock_snapshot(conn, f"{WEEKDAY} 09:00", sample_frame())

    source = tmp_path / "b_pc"
    write_csv(source, WEEKDAY, {"08:00": (7, 7, 7), "09:00": (99, 99, 99)})
    history, _, _, _ = merge.load_source(source)
    with db.session() as conn:
        added = merge.merge_history(conn, merge.normalize(history))

    assert added == 3                            # 08:00 세 대여소만
    assert stock_at(f"{WEEKDAY} 08:00") == 7
    assert stock_at(f"{WEEKDAY} 09:00") == 3     # 겹친 틱은 그대로


def test_휴일_데이터도_그대로_들어온다(tmp_path):
    """수집기의 창 가드는 '지금 받아올까'를 판단할 뿐, 병합에는 적용되지 않는다.

    B PC가 휴일을 맡는 것이 이번 구성의 목적이므로, 여기서 거르면 안 된다.
    """
    source = tmp_path / "b_pc"
    write_csv(source, SUNDAY, {"22:00": (5, 5, 5)})
    history, _, _, _ = merge.load_source(source)
    with db.session() as conn:
        added = merge.merge_history(conn, merge.normalize(history))

    assert added == 3
    assert stock_at(f"{SUNDAY} 22:00") == 5


# ---- 원천 읽기 ----

def test_DB_파일이면_마스터까지_들여온다(tmp_path):
    source = make_source_db(tmp_path / "b.db", SUNDAY, {"22:00": (5, 5, 5)})
    history, master, _, label = merge.load_source(source)

    assert "DB 파일" in label
    assert not master.empty
    with db.session() as conn:
        merge.merge_history(conn, merge.normalize(history))
        assert merge.merge_master(conn, master) == 3
        assert db.has_stock_master(conn, SUNDAY)


def test_DB_파일의_TMAP_고정패널도_합쳐진다(tmp_path):
    """road_leg도 재고와 같은 규칙으로 옮겨진다 — 먼저 수집한 것이 이긴다.

    양쪽 PC가 각자 쌓는 관측이라 성격이 같다(1.26.62). 파이프라인 부산물은
    담지 않고 `roadprobe%` 라벨만 합친다.
    """
    import sqlite3
    source = make_source_db(tmp_path / "c.db", SUNDAY, {"22:00": (5, 5, 5)})
    with sqlite3.connect(source) as conn:
        db.init_schema(conn)
        conn.executemany(
            "INSERT INTO road_leg (run_label, duration, cluster, leg,"
            " from_id, to_id, road_sec) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [("roadprobe-2026-09-01", "_05_10", 1, 0, "ST1", "ST2", 300),
             ("2026-08-28 파이프라인", "_05_10", 1, 0, "ST1", "ST2", 300)])
        conn.commit()

    _history, _master, road, _label = merge.load_source(source)
    assert len(road) == 1                      # 파이프라인 부산물은 빠진다

    with db.session() as conn:
        assert merge.merge_road(conn, road) == 1
        assert merge.merge_road(conn, road) == 0   # 다시 넣어도 안 늘어난다


def test_CSV_묶음은_폴더째_읽는다(tmp_path):
    source = tmp_path / "b_pc"
    write_csv(source, WEEKDAY, {"08:00": (1, 1, 1)})
    write_csv(source, SUNDAY, {"22:00": (2, 2, 2)})
    (source / "collect_log.csv").write_text("logged_at,observed_at\n", encoding="utf-8")

    history, master, _, label = merge.load_source(source)

    assert "CSV 2개" in label                    # collect_log.csv는 섞이지 않는다
    assert master.empty                          # CSV에는 마스터가 없다
    assert len(merge.normalize(history)) == 6


def test_원천이_없거나_형식이_다르면_알려준다(tmp_path):
    with pytest.raises(merge.MergeError):
        merge.load_source(tmp_path / "없는폴더")

    empty = tmp_path / "빈폴더"
    empty.mkdir()
    with pytest.raises(merge.MergeError):
        merge.load_source(empty)

    other = tmp_path / "남의.db"
    sqlite3.connect(other).close()
    with pytest.raises(merge.MergeError, match="stock_history"):
        merge.load_source(other)


def test_읽기_전용으로_열어_원본_DB를_건드리지_않는다(tmp_path):
    """옮겨온 DB는 증거물이다. 병합이 원본에 쓰면 두 PC 데이터가 서로 오염된다."""
    source = make_source_db(tmp_path / "b.db", SUNDAY, {"22:00": (5, 5, 5)})
    before = source.stat().st_mtime_ns
    merge.load_source(source)
    assert source.stat().st_mtime_ns == before


# ---- 정리 ----

def test_원천_안의_중복은_첫_줄을_남긴다(tmp_path):
    """CSV 백업은 append라 한 틱을 손으로 다시 돌리면 같은 키가 두 줄이 된다."""
    source = tmp_path / "b_pc"
    path = write_csv(source, WEEKDAY, {"08:00": (7, 7, 7)})
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{WEEKDAY} 08:00,ST0001,99\n")

    history, _, _, _ = merge.load_source(source)
    cleaned = merge.normalize(history)

    assert len(cleaned) == 3
    assert cleaned.set_index("station_id").loc["ST0001", "stock"] == 7


def test_읽을_수_없는_행은_버린다(tmp_path):
    source = tmp_path / "b_pc"
    path = write_csv(source, WEEKDAY, {"08:00": (7, 7, 7)})
    with path.open("a", encoding="utf-8") as handle:
        handle.write("어제 오전,ST0009,3\n")      # 시각을 못 읽음
        handle.write(f"{WEEKDAY} 08:10,ST0009,\n")  # 재고가 빔

    cleaned = merge.normalize(merge.load_source(source)[0])
    assert len(cleaned) == 3


# ---- 보고 ----

def test_마스터가_없는_날을_알려준다(tmp_path):
    """CSV로만 옮기면 반드시 생긴다. 말없이 넘어가면 좌표 없는 날을 분석에 넣게 된다."""
    source = tmp_path / "b_pc"
    write_csv(source, SUNDAY, {"22:00": (5, 5, 5)})
    history = merge.normalize(merge.load_source(source)[0])

    with db.session() as conn:
        merge.merge_history(conn, history)
        assert merge.missing_master_days(conn, history) == [SUNDAY]
        db.save_stock_master(conn, SUNDAY, sample_frame())
        assert merge.missing_master_days(conn, history) == []


def test_요약은_날짜별_틱과_시간대를_보여준다(tmp_path):
    source = tmp_path / "b_pc"
    write_csv(source, WEEKDAY, {"08:00": (1, 1, 1), "08:10": (2, 2, 2)})
    write_csv(source, SUNDAY, {"22:00": (3, 3, 3)})
    history = merge.normalize(merge.load_source(source)[0])

    days = merge.summarize_days(history)
    assert days["날짜"].tolist() == [SUNDAY, WEEKDAY]
    assert days.set_index("날짜").loc[WEEKDAY, "틱"] == 2
    assert "08:00~22:00" in merge.describe_window(history)
    assert "휴일 1일" in merge.describe_window(history)   # 일요일이 섞였음을 알린다


# ---- CLI ----

def test_모의_실행은_저장하지_않는다(tmp_path, capsys):
    source = tmp_path / "b_pc"
    write_csv(source, WEEKDAY, {"08:00": (7, 7, 7)})

    assert merge.main([str(source), "--dry-run"]) == 0
    assert "모의" in capsys.readouterr().out
    assert stock_at(f"{WEEKDAY} 08:00") is None


def test_CLI가_병합하고_결과를_보고한다(tmp_path, capsys):
    with db.session() as conn:
        db.save_stock_snapshot(conn, f"{WEEKDAY} 09:00", sample_frame())

    source = tmp_path / "b_pc"
    write_csv(source, WEEKDAY, {"08:00": (7, 7, 7), "09:00": (99, 99, 99)})

    assert merge.main([str(source)]) == 0
    output = capsys.readouterr().out
    assert "새로 채움 : 3행" in output
    assert "이미 있음 : 3행" in output
    assert "이름·좌표가 없는 날" in output       # CSV에는 마스터가 없다
    assert stock_at(f"{WEEKDAY} 09:00") == 3


def test_원천이_잘못되면_1로_끝난다(tmp_path, capsys):
    assert merge.main([str(tmp_path / "없음")]) == 1
    assert "[실패]" in capsys.readouterr().out
