"""실험 하네스가 **조용히 틀린 답을 내지 않는지** 검증한다.

실험 스크립트는 파이프라인이 아니라 테스트가 잘 닿지 않는 자리인데, 여기서 나온
숫자가 그대로 논문에 실린다. 실제로 두 번 물렸다.

  · `z_sweep.py`의 `--run-label` 기본값이 DB에 없는 라벨이라 **오류 없이 작업량이
    전부 0인 비용 표**가 나왔다 (1.26.39).
  · 라벨이 없을 때 *"파이프라인을 한 번 돌리세요"* 라고 안내했는데, 이 상황에서는
    **틀린 처방**이다 — 다시 돌리면 오늘 재고가 들어와 정본 스냅샷이 복원되지
    않는다 (1.26.54).

지키려는 규칙: **없는 것은 없다고 말하고, 처방은 상황에 맞아야 한다.**
"""
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_baseline():
    for path in (PROJECT_ROOT, PROJECT_ROOT / "experiments" / "baseline"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    spec = importlib.util.spec_from_file_location(
        "baseline_compare", PROJECT_ROOT / "experiments" / "baseline" / "baseline_compare.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bc():
    return load_baseline()


def test_missing_label_does_not_advise_rerunning_the_pipeline(bc):
    """정본 스냅샷은 다시 돌려서 복원되지 않는다 — 그 길로 보내면 안 된다."""
    message = bc.missing_run_message("2026-08-11 real", ["2026-08-27 23"])
    assert "2026-08-11 real" in message
    assert "transfer_run.py" in message, "가져올 방법을 알려 줘야 한다"
    assert "2026-08-27 23" in message, "이 DB에 무엇이 있는지 보여 줘야 한다"
    assert "파이프라인을 한 번 돌려" not in message, \
        "라벨이 지정된 경우 파이프라인 재실행은 틀린 처방이다"


def test_empty_db_still_advises_running_the_pipeline(bc):
    """라벨을 지정하지 않았는데 비었으면 그때는 파이프라인이 맞는 처방이다."""
    message = bc.missing_run_message("", [])
    assert "파이프라인" in message
    assert "transfer_run" not in message


def test_load_inputs_names_the_missing_label(bc, monkeypatch, tmp_path):
    """조용히 빈 결과를 내지 말고 멈춰야 한다 (z_sweep에서 실제로 겪은 결함)."""
    import db

    net = pd.DataFrame({
        "station_id": ["ST0001"] * 2, "date": ["2025-11-03", "2025-11-04"],
        **{f"net_{h:02d}": [1, 2] for h in range(24)},
    })
    db.save_output("net_demand", net, period="25년 11월")
    db.save_output("station_info", pd.DataFrame({
        "station_id": ["ST0001"], "station_name": ["가"], "lat": [36.3],
        "lon": [127.3], "parking_lot": [10], "stock": [5],
    }), run_label="있는라벨", period="25년 11월", duration="_05_10")

    with pytest.raises(SystemExit) as err:
        bc.load_inputs("25년 11월", "없는라벨", "weekday", 0, "")
    text = str(err.value)
    assert "없는라벨" in text
    assert "있는라벨" in text, "쓸 수 있는 라벨을 알려 줘야 다음 수를 둔다"


def test_load_inputs_accepts_an_existing_label(bc):
    """정상 경로가 막히면 안 된다."""
    import db

    net = pd.DataFrame({
        "station_id": ["ST0001"] * 2, "date": ["2025-11-03", "2025-11-04"],
        **{f"net_{h:02d}": [1, 2] for h in range(24)},
    })
    db.save_output("net_demand", net, period="25년 11월")
    db.save_output("station_info", pd.DataFrame({
        "station_id": ["ST0001"], "station_name": ["가"], "lat": [36.3],
        "lon": [127.3], "parking_lot": [10], "stock": [5],
    }), run_label="있는라벨", period="25년 11월", duration="_05_10")

    _, info, _ = bc.load_inputs("25년 11월", "있는라벨", "weekday", 0, "")
    assert list(info["station_id"]) == ["ST0001"]
