"""시험이 하나도 없던 DB 도구 셋 (`show_schema` · `rebuild_net_demand` · `train_demand_model`).

2026-09-14에 저장소 전체를 훑어 **테스트에 이름조차 나오지 않는 모듈 넷**을
찾았다. 하나(`make_thesis_figures`)는 따로 다루고, 나머지 셋이 여기다.

셋 다 성격이 다르지만 **지키려는 것은 같다** — 자료가 없거나 모자랄 때
*무엇이 없어서인지 말하고 깨끗이 끝나는가*. 이 도구들은 드물게, 그리고
대개 **급할 때** 돌아간다(제출 직전, 다른 PC, 휴일 계획을 처음 만들 때).
그때 스택 추적만 뱉으면 원인을 쫓을 시간이 없다.

  · `show_schema`        — 읽기 전용. 문서와 실제가 어긋날 때 사실을 보여 준다.
  · `rebuild_net_demand` — 전 기간 순수요를 다시 계산한다(하위 프로세스를 띄운다).
  · `train_demand_model` — 분위수 모델을 학습해 `.pkl`로 저장한다.

⚠️ `train_demand_model`은 모델이 없으면 `calculate_target_qty`가 **조용히**
`mu + z·sigma`로 돌아간다 — 그래서 *"학습이 안 됐다"* 를 분명히 말하는 것이
이 도구에서 특히 중요하다.
"""
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_tool(name):
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    spec = importlib.util.spec_from_file_location(
        name, PROJECT_ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_cli(tool, monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["tool.py", *argv])
    return tool.main()


def seed_net_demand(period, dates, stations=4):
    """순수요를 심는다 — 시간대별 net_00~net_23."""
    import db

    rows = []
    for date in dates:
        for i in range(stations):
            row = {"date": date, "station_id": f"ST{i:04d}"}
            row.update({f"net_{h:02d}": (i - 2) * (h % 3 - 1) for h in range(24)})
            rows.append(row)
    db.save_output("net_demand", pd.DataFrame(rows), period=period)


# ═══════════════════════════════════════════════ show_schema

@pytest.fixture
def schema_tool():
    return load_tool("show_schema")


def test_스키마는_빈_DB에서도_표를_보여준다(schema_tool, monkeypatch, capsys):
    assert run_cli(schema_tool, monkeypatch) == 0
    out = capsys.readouterr().out
    assert "테이블" in out and "행 수" in out
    assert "정본 정의는 db.py의 SCHEMA 상수다" in out, "어느 쪽이 정본인지 안 밝혔다"


def test_없는_테이블을_물으면_1로_끝난다(schema_tool, monkeypatch, capsys):
    assert run_cli(schema_tool, monkeypatch, "--table", "없는표") == 1
    assert "'없는표' 테이블이 없습니다" in capsys.readouterr().out


def test_테이블_하나만_골라_컬럼까지_펼친다(schema_tool, monkeypatch, capsys):
    assert run_cli(schema_tool, monkeypatch, "--table", "runs", "--columns") == 0
    out = capsys.readouterr().out
    assert "run_label" in out and "created_at" in out
    assert "station_stock" not in out, "하나만 물었는데 다른 표까지 보여줬다"


def test_PBR_DB_PATH가_가리키는_DB를_본다(schema_tool, monkeypatch, tmp_path, capsys):
    """문서가 약속한 것이다 — `sqlite3`를 직접 열지 않고 `db.session()`을 거친다."""
    import db

    별도 = tmp_path / "다른.db"
    monkeypatch.setenv("PBR_DB_PATH", str(별도))
    with db.session() as conn:
        conn.execute("CREATE TABLE 여기만_있는_표 (run_label TEXT)")
        conn.commit()

    assert run_cli(schema_tool, monkeypatch) == 0
    assert "여기만_있는_표" in capsys.readouterr().out


# ═══════════════════════════════════════════════ rebuild_net_demand

@pytest.fixture
def rebuild_tool():
    return load_tool("rebuild_net_demand")


def test_적재된_대여이력이_없으면_무엇을_하라고_알려준다(rebuild_tool, monkeypatch, capsys):
    assert run_cli(rebuild_tool, monkeypatch) == 1
    out = capsys.readouterr().out
    assert "load_rentals.py" in out, "다음에 무엇을 할지 안 알려줬다"


def test_예행은_순수요를_건드리지_않는다(rebuild_tool, monkeypatch, capsys):
    """되돌릴 수 없는 재계산 전에 대상만 세어 보는 길이 있어야 한다."""
    import db

    seed_net_demand("25년 11월", ["2025-11-03", "2025-11-08"])
    with db.session() as conn:
        before = conn.execute("SELECT COUNT(*) FROM net_demand").fetchone()[0]

    assert run_cli(rebuild_tool, monkeypatch,
                   "--period", "25년 11월", "--dry-run") == 0
    out = capsys.readouterr().out
    assert "dry-run" in out and "25년 11월" in out
    assert "재계산 완료" not in out, "예행인데 끝났다고 말했다"

    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM net_demand").fetchone()[0] == before


def test_예행이_평일과_휴일을_갈라_센다(rebuild_tool, monkeypatch, capsys):
    """이 도구가 있는 이유가 '휴일이 빠진 순수요'라 그 수가 보여야 한다."""
    seed_net_demand("25년 11월", ["2025-11-03", "2025-11-04", "2025-11-08"])

    assert run_cli(rebuild_tool, monkeypatch,
                   "--period", "25년 11월", "--dry-run") == 0
    assert "2/1" in capsys.readouterr().out, "평일 2일·휴일 1일로 안 갈렸다"


# ═══════════════════════════════════════════════ train_demand_model

@pytest.fixture
def train_tool():
    return load_tool("train_demand_model")


def test_기간이_모자라면_이유를_말하고_1로_끝난다(train_tool, monkeypatch, capsys):
    """직전 달 → 이번 달 쌍이 필요하다는 것을 사람이 알아야 한다."""
    seed_net_demand("25년 11월", ["2025-11-03", "2025-11-04"])

    assert run_cli(train_tool, monkeypatch) == 1
    out = capsys.readouterr().out
    assert "기간이 1개뿐이라 학습할 수 없습니다" in out
    assert "직전 달" in out


def test_예행은_모델_파일을_만들지_않는다(train_tool, monkeypatch, tmp_path, capsys):
    """모델이 생기면 다음 실행부터 파이프라인이 그것을 쓴다 — 예행이 그러면 안 된다."""
    seed_net_demand("25년 11월", ["2025-11-03", "2025-11-04", "2025-11-05"])
    seed_net_demand("25년 12월", ["2025-12-01", "2025-12-02", "2025-12-03"])
    모델 = tmp_path / "models" / "target_quantile.pkl"

    run_cli(train_tool, monkeypatch, "--dry-run", "--out", str(모델))
    capsys.readouterr()
    assert not 모델.exists(), "예행인데 모델을 저장했다"
