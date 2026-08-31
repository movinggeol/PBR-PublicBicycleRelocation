"""재고 시계열 수집 회귀 테스트 (docs/구현/COLLECTOR.md).

지키는 것:
  - **창 가드** — 휴일·창 밖에는 API를 부르지 않는다. 스케줄러는 공휴일을 모르므로
    이 가드가 뚫리면 광복절 재고가 평일 데이터에 섞인다.
  - **틱 격자 반올림** — 09:00:03과 09:09:58이 각각 09:00·09:10로 정렬돼야
    날짜가 다른 같은 시각끼리 대조된다.
  - **멱등 저장** — 같은 틱을 다시 저장해도 행이 쌓이지 않는다.
  - **결측 기록** — 실패도 로그에 남아야 '데이터 없음'과 '재고 0'을 구분한다.

API는 부르지 않는다(monkeypatch로 대체). 실호출 검증은 docs/구현/TESTING.md 참고.
"""
import sys
from datetime import datetime, time
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import db
import tools.collect_stock as collector

# 2026-08-24 월요일(평일) / 2026-08-22 토요일 / 2026-05-05 화요일이지만 어린이날.
# 마지막 것이 핵심이다 — 스케줄러는 '화요일'이라 깨우고, 거르는 것은 스크립트뿐이다.
WEEKDAY = datetime(2026, 8, 24)
SATURDAY = datetime(2026, 8, 22)
PUBLIC_HOLIDAY = datetime(2026, 5, 5)

WINDOW = (time(9, 0), time(17, 0))
INTERVAL = 10


def sample_frame(stocks=(3, 0, 12)) -> pd.DataFrame:
    """tashu.fetch_stations()가 돌려주는 형태."""
    return pd.DataFrame({
        "station_id": ["ST0001", "ST0002", "ST0003"],
        "station_name": ["타슈 관제센터", "탄방동 한사랑병원", "둔산동 시청"],
        "parking_info": ["10대용*1 / 10", "10대용*1 / 10", "20대용*1 / 20"],
        "lat": [36.3504, 36.3484, 36.3601],
        "lon": [127.3845, 127.3900, 127.3850],
        "stock": list(stocks),
    })


@pytest.fixture
def history_dir(tmp_path, monkeypatch):
    """CSV 백업·로그가 실제 data/ 를 건드리지 않게 임시 경로로 돌린다."""
    path = tmp_path / "재고이력"
    monkeypatch.setattr(collector, "HISTORY_DIR", path)
    return path


@pytest.fixture
def fake_api(monkeypatch):
    """API 호출을 대체하고, 몇 번 불렸는지 기록한다."""
    calls = []

    def fetch():
        calls.append(1)
        return sample_frame()

    monkeypatch.setattr(collector.tashu, "fetch_stations", fetch)
    return calls


# ---- 틱 격자 ----

def test_틱은_가장_가까운_격자로_반올림된다():
    start = WINDOW[0]
    assert collector.tick_of(datetime(2026, 8, 24, 9, 0, 3), start, INTERVAL) \
        == datetime(2026, 8, 24, 9, 0)
    assert collector.tick_of(datetime(2026, 8, 24, 9, 9, 58), start, INTERVAL) \
        == datetime(2026, 8, 24, 9, 10)
    assert collector.tick_of(datetime(2026, 8, 24, 9, 4), start, INTERVAL) \
        == datetime(2026, 8, 24, 9, 0)
    assert collector.tick_of(datetime(2026, 8, 24, 9, 6), start, INTERVAL) \
        == datetime(2026, 8, 24, 9, 10)


def test_하루_기대_틱은_양_끝을_포함한다():
    # 09:00부터 17:00까지 10분 간격이면 49틱(양 끝 포함)이다.
    assert collector.expected_ticks(*WINDOW, INTERVAL) == 49
    assert collector.expected_ticks(time(9, 0), time(17, 0), 30) == 17


def test_수집_창_형식이_잘못되면_알려준다():
    with pytest.raises(SystemExit):
        collector.parse_window("9시부터")
    with pytest.raises(SystemExit):
        collector.parse_window("17:00-09:00")


# ---- 창 가드 ----

def test_평일_창_안이면_수집한다():
    _, allowed, reason = collector.window_state(
        WEEKDAY.replace(hour=10, minute=0), *WINDOW, INTERVAL)
    assert allowed and reason == ""


def test_주말은_거른다():
    _, allowed, reason = collector.window_state(
        SATURDAY.replace(hour=10), *WINDOW, INTERVAL)
    assert not allowed
    assert "휴일" in reason


def test_공휴일은_평일이어도_거른다():
    # 어린이날(화). 스케줄러는 요일만 보고 깨우므로 여기서 막지 못하면 섞인다.
    _, allowed, reason = collector.window_state(
        PUBLIC_HOLIDAY.replace(hour=10), *WINDOW, INTERVAL)
    assert not allowed
    assert "휴일" in reason


@pytest.mark.parametrize("hour,minute", [(8, 50), (17, 20), (23, 0)])
def test_창_밖은_거른다(hour, minute):
    _, allowed, reason = collector.window_state(
        WEEKDAY.replace(hour=hour, minute=minute), *WINDOW, INTERVAL)
    assert not allowed
    assert "창" in reason


def test_휴일에는_API를_부르지_않는다(history_dir, fake_api):
    code = collector.run_tick(PUBLIC_HOLIDAY.replace(hour=10), *WINDOW, INTERVAL)
    assert code == 0            # 건너뛰기는 실패가 아니다
    assert fake_api == []       # 호출 자체가 없어야 한다
    assert not history_dir.exists()


def test_force는_휴일에도_수집한다(history_dir, fake_api):
    code = collector.run_tick(PUBLIC_HOLIDAY.replace(hour=10), *WINDOW, INTERVAL,
                              force=True)
    assert code == 0
    assert len(fake_api) == 1


# ---- 휴일 수집 (두 번째 PC 구성, docs/구현/COLLECTOR.md 11장) ----

def test_휴일_포함이면_휴일에도_수집한다(history_dir, fake_api):
    for stamp in (SATURDAY.replace(hour=10), PUBLIC_HOLIDAY.replace(hour=10)):
        code = collector.run_tick(stamp, *WINDOW, INTERVAL, include_holidays=True)
        assert code == 0
    assert len(fake_api) == 2


def test_휴일_포함은_창_가드까지_풀지는_않는다(history_dir, fake_api):
    """`--force`와의 차이가 여기다. 둘 다 풀리면 등록한 창이 무의미해져
    아무 시각에나 틱이 들어오고, 격자 대조가 어긋난다."""
    _, allowed, reason = collector.window_state(
        SATURDAY.replace(hour=23), *WINDOW, INTERVAL, include_holidays=True)
    assert not allowed
    assert "창" in reason

    code = collector.run_tick(SATURDAY.replace(hour=23), *WINDOW, INTERVAL,
                              include_holidays=True)
    assert code == 0            # 건너뛰기는 실패가 아니다
    assert fake_api == []       # 창 밖이라 부르지 않는다


def test_기본값은_여전히_휴일을_거른다(history_dir, fake_api):
    """옵션을 안 주면 지금까지와 똑같아야 한다 — A PC 동작이 바뀌면 안 된다."""
    assert collector.run_tick(PUBLIC_HOLIDAY.replace(hour=10),
                              *WINDOW, INTERVAL) == 0
    assert fake_api == []


# ---- 휴일 전용 (B PC가 휴일만 맡는 구성) ----

def test_휴일만_수집이면_평일을_거른다(history_dir, fake_api):
    code = collector.run_tick(WEEKDAY.replace(hour=10), *WINDOW, INTERVAL,
                              holidays_only=True)
    assert code == 0            # 건너뛰기는 실패가 아니다
    assert fake_api == []
    assert not history_dir.exists()


def test_휴일만_수집은_주말과_공휴일을_모두_잡는다(history_dir, fake_api):
    """**공휴일이 핵심이다.** 어린이날은 화요일이라 토·일 트리거로는 못 잡는다 —
    그래서 스케줄러를 7일로 깨우고 평일을 이 가드가 거른다."""
    for stamp in (SATURDAY.replace(hour=10), PUBLIC_HOLIDAY.replace(hour=10)):
        assert collector.run_tick(stamp, *WINDOW, INTERVAL,
                                  holidays_only=True) == 0
    assert len(fake_api) == 2


def test_휴일만_수집도_창_가드는_지킨다(history_dir, fake_api):
    _, allowed, reason = collector.window_state(
        SATURDAY.replace(hour=23), *WINDOW, INTERVAL, holidays_only=True)
    assert not allowed
    assert "창" in reason
    assert collector.run_tick(SATURDAY.replace(hour=23), *WINDOW, INTERVAL,
                              holidays_only=True) == 0
    assert fake_api == []


def test_평일을_거를_때_이유를_알려준다():
    _, allowed, reason = collector.window_state(
        WEEKDAY.replace(hour=10), *WINDOW, INTERVAL, holidays_only=True)
    assert not allowed
    assert "평일" in reason      # '휴일'이라고 하면 정반대로 읽힌다


def test_휴일만_수집이_휴일_포함보다_우선한다(history_dir, fake_api):
    """CLI는 상호 배타로 막지만, 함수를 직접 부르는 쪽에서도 모순되면 안 된다."""
    assert collector.run_tick(WEEKDAY.replace(hour=10), *WINDOW, INTERVAL,
                              include_holidays=True, holidays_only=True) == 0
    assert fake_api == []


def test_CLI는_두_요일_옵션을_함께_받지_않는다():
    with pytest.raises(SystemExit):
        collector.build_parser().parse_args(
            ["--include-holidays", "--holidays-only"])


# ---- 수집 ----

def test_한_틱을_수집하면_DB_CSV_로그에_남는다(history_dir, fake_api):
    code = collector.run_tick(WEEKDAY.replace(hour=10, minute=0, second=3),
                              *WINDOW, INTERVAL)
    assert code == 0

    with db.session() as conn:
        frame = db.load_stock_history(conn)
    assert len(frame) == 3
    assert frame["observed_at"].unique().tolist() == ["2026-08-24 10:00"]
    assert frame["fetched_at"].notna().all()

    backup = history_dir / "stock_2026-08-24.csv"
    assert backup.exists()
    assert len(pd.read_csv(backup)) == 3

    log = pd.read_csv(history_dir / collector.LOG_NAME)
    assert log["status"].iloc[-1] == "성공"
    assert log["stations"].iloc[-1] == 3


def test_API_실패는_로그에_남고_종료코드가_1이다(history_dir, monkeypatch):
    """결측과 '재고 0'을 나중에 구분하려면 실패가 기록으로 남아야 한다."""
    def boom():
        raise collector.tashu.TashuError("타슈 API 호출 실패: 500")

    monkeypatch.setattr(collector.tashu, "fetch_stations", boom)
    code = collector.run_tick(WEEKDAY.replace(hour=10), *WINDOW, INTERVAL)

    assert code == 1
    log = pd.read_csv(history_dir / collector.LOG_NAME)
    assert log["status"].iloc[-1] == "실패"
    assert "500" in log["detail"].iloc[-1]

    with db.session() as conn:
        assert db.load_stock_history(conn).empty


def test_dry_run은_저장하지_않는다(history_dir, fake_api):
    code = collector.run_tick(WEEKDAY.replace(hour=10), *WINDOW, INTERVAL,
                              dry_run=True)
    assert code == 0
    assert len(fake_api) == 1
    with db.session() as conn:
        assert db.load_stock_history(conn).empty
    assert not history_dir.exists()


def test_마스터는_하루_한_번만_남는다(history_dir, fake_api):
    collector.run_tick(WEEKDAY.replace(hour=9), *WINDOW, INTERVAL)
    collector.run_tick(WEEKDAY.replace(hour=9, minute=10), *WINDOW, INTERVAL)

    with db.session() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM stock_station_master").fetchone()[0]
        assert rows == 3                       # 대여소 3곳 × 하루 1회
        assert db.has_stock_master(conn, "2026-08-24")
        assert not db.has_stock_master(conn, "2026-08-25")
        # 시계열 쪽은 틱마다 쌓인다
        assert len(db.load_stock_history(conn)) == 6


def test_수집은_station_stock과_runs를_건드리지_않는다(history_dir, fake_api):
    """파이프라인 실행 이력을 오염시키면 웹의 '최근 실행'이 수집 틱을 가리킨다."""
    collector.run_tick(WEEKDAY.replace(hour=10), *WINDOW, INTERVAL)
    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM station_stock").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


# ---- 저장소 ----

def test_같은_틱을_다시_저장하면_덮어쓴다():
    with db.session() as conn:
        db.save_stock_snapshot(conn, "2026-08-24 10:00", sample_frame((3, 0, 12)))
        db.save_stock_snapshot(conn, "2026-08-24 10:00", sample_frame((5, 0, 12)))
        frame = db.load_stock_history(conn)

    assert len(frame) == 3          # 중복이 쌓이지 않는다
    assert int(frame.loc[frame["station_id"] == "ST0001", "stock"].iloc[0]) == 5


def test_end에_날짜만_줘도_그날이_통째로_들어온다():
    """문자열 비교라 '2026-08-24'는 '00:00'으로 읽힌다 — 그날 오후가 통째로 빠진다."""
    with db.session() as conn:
        db.save_stock_snapshot(conn, "2026-08-24 09:00", sample_frame())
        db.save_stock_snapshot(conn, "2026-08-24 16:50", sample_frame())
        db.save_stock_snapshot(conn, "2026-08-25 09:00", sample_frame())
        frame = db.load_stock_history(conn, start="2026-08-24", end="2026-08-24")

    assert sorted(frame["observed_at"].unique()) == \
        ["2026-08-24 09:00", "2026-08-24 16:50"]


def test_요일_구분으로_거를_수_있다():
    with db.session() as conn:
        db.save_stock_snapshot(conn, "2026-08-24 10:00", sample_frame())   # 월
        db.save_stock_snapshot(conn, "2026-08-22 10:00", sample_frame())   # 토
        db.save_stock_snapshot(conn, "2026-05-05 10:00", sample_frame())   # 어린이날
        weekday = db.load_stock_history(conn, day_type="weekday")
        holiday = db.load_stock_history(conn, day_type="holiday")

    assert weekday["observed_at"].unique().tolist() == ["2026-08-24 10:00"]
    # 휴일 = 주말 ∪ 공휴일. 어린이날이 빠지면 요일 판정이 갈린 것이다.
    assert sorted(holiday["observed_at"].unique()) == \
        ["2026-05-05 10:00", "2026-08-22 10:00"]


def test_대여소를_지정해_읽을_수_있다():
    with db.session() as conn:
        db.save_stock_snapshot(conn, "2026-08-24 10:00", sample_frame())
        frame = db.load_stock_history(conn, stations=["ST0002"])
    assert frame["station_id"].unique().tolist() == ["ST0002"]


def test_틱별_집계는_결측_판정의_원재료다():
    with db.session() as conn:
        db.save_stock_snapshot(conn, "2026-08-24 09:00", sample_frame())
        db.save_stock_snapshot(conn, "2026-08-24 09:10", sample_frame((1, 2, 3)))
        ticks = db.stock_history_ticks(conn)

    assert ticks["observed_at"].tolist() == ["2026-08-24 09:00", "2026-08-24 09:10"]
    assert ticks["stations"].tolist() == [3, 3]


# ---- 현황 ----

def test_현황은_데이터가_없어도_동작한다(history_dir, capsys):
    assert collector.print_status(*WINDOW, INTERVAL) == 0
    assert "아직 수집한 데이터가 없습니다" in capsys.readouterr().out


def test_현황은_기대_격자와_대조해_결측을_센다(history_dir):
    with db.session() as conn:
        for minute in (0, 10, 20):
            db.save_stock_snapshot(conn, f"2026-08-24 09:{minute:02d}", sample_frame())

    table = collector.coverage(*WINDOW, INTERVAL)
    row = table.iloc[0]
    assert row["날짜"] == "2026-08-24"
    assert row["틱"] == 3
    assert row["기대"] == 49
    assert row["결측"] == 46     # 로그가 아니라 격자와 대조한다(절전은 로그도 안 남긴다)


# ---------------------------------------------------------------- 창 기준 해석

def test_status_uses_the_registered_window_not_the_default(monkeypatch):
    """등록된 창이 07~22시인데 기본값(09~17시)으로 결측을 세면 **표가 통째로 틀린다.**

    실제로 그랬다 — 창을 넓혀 등록해 둔 뒤에도 `--status`가 "하루 49틱 기대"라고
    보고했다(1.26.55). `scripts/collector.ps1`은 등록된 인자를 되읽어 옳게
    보고하는데 파이썬 경로에만 그 보정이 없었다. **두 경로가 다른 답을 내면
    어느 쪽을 믿어야 할지 알 수 없다.**
    """
    monkeypatch.setattr(collector, "registered_args",
                        lambda: {"window": "07:00-22:00", "interval": 10})
    args = collector.build_parser().parse_args(["--status"])
    window, interval, source = collector.resolve_window(args, ["--status"])
    assert window == "07:00-22:00"
    assert interval == 10
    assert "등록된 작업" in source

    start, end = collector.parse_window(window)
    assert collector.expected_ticks(start, end, interval) == 91


def test_explicit_arguments_beat_the_registered_task(monkeypatch):
    """손으로 준 값이 이겨야 한다 — 옛 창으로 대조해 보는 용도가 있다."""
    monkeypatch.setattr(collector, "registered_args",
                        lambda: {"window": "07:00-22:00", "interval": 10})
    argv = ["--status", "--window", "09:00-17:00", "--interval", "10"]
    args = collector.build_parser().parse_args(argv)
    window, interval, source = collector.resolve_window(args, argv)
    assert window == "09:00-17:00"
    assert source == "직접 지정"


def test_missing_task_says_it_fell_back_to_defaults(monkeypatch):
    """기본값으로 떨어졌으면 **그 사실을 말해야 한다** — 조용히 틀린 기준으로 세면 안 된다."""
    monkeypatch.setattr(collector, "registered_args", lambda: None)
    args = collector.build_parser().parse_args(["--status"])
    window, interval, source = collector.resolve_window(args, ["--status"])
    assert window == collector.DEFAULT_WINDOW
    assert "기본값" in source


def test_registered_args_survives_a_missing_scheduler(monkeypatch):
    """스케줄러가 없는 환경(다른 OS·CI)에서 죽으면 --status 자체가 막힌다."""
    def boom(*a, **k):
        raise OSError("schtasks 없음")

    monkeypatch.setattr("subprocess.run", boom)
    assert collector.registered_args() is None
