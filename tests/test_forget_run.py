"""실행 삭제 도구 검증 (`tools/forget_run.py`).

🔴 **이 도구에는 시험이 하나도 없었다.** 하는 일은 `run_label`을 가진 테이블
열세 곳에서 `DELETE`를 돌리는 것이고 **되돌릴 수 없다.**

2026-09-14에 실제 DB(1.37GB)에서 시험 라벨 열여섯 개를 지워야 했을 때, TODO가
이 도구를 쓰라고 하는데도 **지켜 주는 것이 없어 손으로 짠 스크립트를 썼다** —
그래서 이 도구가 마지막에 찍어 주는 잔여 파일 목록을 못 받았고 CSV를 따로
세어야 했다. 다음에는 규약대로 쓸 수 있게 여기서 못박는다.

지키려는 규칙:
  1. `--dry-run`이 센 수와 **실제로 지운 수가 같다**. 예행이 실제와 다르면
     세어 보는 의미가 없다 — `merge_stock`에는 있던 시험이 여기엔 없었다.
  2. `--yes` 없이는 **한 행도** 안 지운다.
  3. 라벨은 **정확히 일치**한다. `2026-09-13`으로 `2026-09-13 23`이 지워지면
     하루치 계획이 통째로 날아간다.
  4. 계획을 지워도 **`road_leg`의 수집분은 남는다** — 게이트 A가 판정할
     자료이고, 이 도구의 삭제 대상 목록에 실제로 들어 있다.
  5. **`stock_history`는 건드리지 못한다.** `run_label`이 없어서인데, 설계상
     맞더라도 못박아 두지 않으면 컬럼 하나 붙는 날 126만 행이 위험해진다.
  6. 산출물 파일은 **지우지 않고 알려만 준다.**
"""
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "tools" / "forget_run.py"


def load_tool():
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    spec = importlib.util.spec_from_file_location("forget_run", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tool():
    return load_tool()


def seed_plan(label, kind="plan", stations=3):
    """계획 하나를 심는다 — `runs` + `station_stock` + `rebalance_plan`."""
    import db

    stock = pd.DataFrame({
        "station_id": [f"ST{i:04d}" for i in range(stations)],
        "stock": [5 + i for i in range(stations)],
    })
    db.save_output("station_stock", stock, run_label=label,
                   period="25년 11월", duration="_05_10")
    with db.session() as conn:
        db.record_run(conn, label, period="25년 11월", duration="_05_10",
                      kind=kind)
    return stations


def seed_road(label="roadprobe-2099-01-01", legs=4):
    """도로 관측을 심는다 — 게이트 A가 판정할 자료.

    ⚠️ **날짜를 2099로 둔 것은 일부러다.** 처음에는 오늘 날짜를 썼는데,
    실제 DB에 같은 이름의 **진짜 수집분(400구간)** 이 있었다. 격리가
    듣는 한 문제는 없지만, 격리가 한 번 새는 날 이 시험은 게이트 A가
    판정할 자료를 지운다 — 있을 수 없는 날짜면 그 사고가 불가능하다.
    """
    import db

    with db.session() as conn:
        db.record_run(conn, label, duration="_05_10", kind="probe")
        for leg in range(legs):
            conn.execute(
                "INSERT INTO road_leg (run_label, duration, cluster, leg,"
                " straight_km, road_sec) VALUES (?, '_05_10', 1, ?, 1.0, 300)",
                (label, leg))
        conn.commit()
    return legs


def seed_stock_history(ticks=5):
    """재고 이력 — `run_label`이 없어 이 도구가 닿지 못하는 자리."""
    import db

    with db.session() as conn:
        for i in range(ticks):
            conn.execute(
                "INSERT INTO stock_history (observed_at, station_id, stock)"
                " VALUES (?, 'ST0000', ?)",
                (f"2026-09-14 1{i}:00", i))
        conn.commit()
    return ticks


def count(table, label=None):
    import db

    with db.session() as conn:
        if label is None:
            return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        return conn.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE run_label = ?',
            (label,)).fetchone()[0]


def run_cli(tool, monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["forget_run.py", *argv])
    return tool.main()


# ---------------------------------------------------------------------------
# ① 예행이 실제와 같은 수를 말하는가 — 이 도구를 믿을 수 있는지가 여기 달렸다
# ---------------------------------------------------------------------------

def test_예행이_실제로_지우는_수와_같다(tool, monkeypatch, capsys):
    seed_plan("sweep-99")

    assert run_cli(tool, monkeypatch, "sweep-99", "--dry-run") == 0
    예행 = capsys.readouterr().out
    예행합계 = int(예행.split("합계")[1].split("행")[0].strip())
    assert "지우지 않았습니다" in 예행

    남은_행 = count("station_stock", "sweep-99") + count("runs", "sweep-99")
    assert 남은_행 == 예행합계, "예행 뒤에 자료가 이미 줄었다"

    assert run_cli(tool, monkeypatch, "sweep-99", "--yes") == 0
    실제 = capsys.readouterr().out
    실제합계 = int(실제.split("행을 지웠습니다")[0].strip().split("\n")[-1])
    assert 실제합계 == 예행합계
    assert "확인: 남은 행 없음" in 실제


def test_yes_없이는_한_행도_안_지운다(tool, monkeypatch, capsys):
    stations = seed_plan("sweep-99")

    assert run_cli(tool, monkeypatch, "sweep-99") == 1
    assert "--yes를 붙이세요" in capsys.readouterr().out
    assert count("station_stock", "sweep-99") == stations
    assert count("runs", "sweep-99") == 1


# ---------------------------------------------------------------------------
# ② 라벨을 넓게 잡아 남의 것을 지우지 않는가
# ---------------------------------------------------------------------------

def test_라벨은_정확히_일치해야_한다(tool, monkeypatch, capsys):
    """`2026-09-13`으로 `2026-09-13 23`이 지워지면 하루치 계획이 날아간다."""
    stations = seed_plan("2026-09-13 23")

    assert run_cli(tool, monkeypatch, "2026-09-13", "--yes") == 1
    assert "지울 것이 없습니다" in capsys.readouterr().out
    assert count("station_stock", "2026-09-13 23") == stations


def test_계획을_지워도_도로_관측은_남는다(tool, monkeypatch, capsys):
    """`road_leg`는 삭제 대상 목록에 들어 있다 — 게이트 A 자료다."""
    seed_plan("sweep-99")
    legs = seed_road()

    assert run_cli(tool, monkeypatch, "sweep-99", "--yes") == 0
    capsys.readouterr()
    assert count("road_leg", "roadprobe-2099-01-01") == legs
    assert count("runs", "roadprobe-2099-01-01") == 1


def test_재고_이력은_건드리지_못한다(tool, monkeypatch, capsys):
    """`stock_history`에는 `run_label`이 없다 — 126만 행이 걸린 자리다."""
    seed_plan("sweep-99")
    ticks = seed_stock_history()

    import db
    with db.session() as conn:
        assert "stock_history" not in tool.scoped_tables(conn)

    assert run_cli(tool, monkeypatch, "sweep-99", "--yes") == 0
    capsys.readouterr()
    assert count("stock_history") == ticks


# ---------------------------------------------------------------------------
# ③ 목록을 코드에 박지 않았는가 — 테이블이 늘어도 빠뜨리면 안 된다
# ---------------------------------------------------------------------------

def test_새_테이블이_생겨도_DB에_물어봐_찾는다(tool):
    import db

    with db.session() as conn:
        conn.execute("CREATE TABLE 나중에_생긴_표 (run_label TEXT, 값 INTEGER)")
        conn.commit()
        tables = tool.scoped_tables(conn)

    assert "나중에_생긴_표" in tables, "목록을 코드에 박아 두면 조용히 빠뜨린다"
    assert "runs" in tables


def test_종류로_한꺼번에_지운다(tool, monkeypatch, capsys):
    seed_plan("sweep-99", kind="plan")
    seed_road("roadprobe-2099-01-01")

    assert run_cli(tool, monkeypatch, "--kind", "probe", "--yes") == 0
    capsys.readouterr()
    assert count("road_leg", "roadprobe-2099-01-01") == 0
    assert count("station_stock", "sweep-99") == 3, "종류가 다른 계획까지 지웠다"


def test_없는_라벨이면_1로_끝난다(tool, monkeypatch, capsys):
    seed_plan("sweep-99")

    assert run_cli(tool, monkeypatch, "없는-라벨", "--yes") == 1
    out = capsys.readouterr().out
    assert "지울 것이 없습니다" in out
    assert "--list로 확인" in out


# ---------------------------------------------------------------------------
# ④ 산출물 파일 — 지우지 않고 알려만 준다
# ---------------------------------------------------------------------------

def test_산출물_파일은_지우지_않고_알려만_준다(tool, monkeypatch, tmp_path, capsys):
    seed_plan("sweep-99")
    데이터 = tmp_path / "pp_data"
    데이터.mkdir()
    남을_파일 = 데이터 / "top_05_10 (sweep-99).csv"
    남을_파일.write_text("station_id\nST0000\n", encoding="utf-8")
    monkeypatch.setattr(tool, "DATA_ROOT", 데이터)
    monkeypatch.setattr(tool, "PROJECT_ROOT", tmp_path)

    assert run_cli(tool, monkeypatch, "sweep-99", "--yes") == 0
    out = capsys.readouterr().out
    assert "지우지 않습니다" in out
    assert "top_05_10 (sweep-99).csv" in out
    assert 남을_파일.exists(), "파일을 지웠다 — 라벨이 겹치면 남의 것을 지운다"
