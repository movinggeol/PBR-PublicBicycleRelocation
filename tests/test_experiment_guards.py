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


# ---------------------------------------------------------------- 공통 모집단

def _net(station_ids, days=2):
    """결품 계산에 필요한 최소 순수요 표.

    `_stockout_hours`는 `net_05`~`net_09` 같은 시간별 컬럼을 본다. A만 수요가
    커서 결품이 나고 나머지는 나지 않도록 만든다 — 그래야 모집단을 넓혔을 때
    평균이 내려가는 것을 확인할 수 있다.
    """
    rows = []
    for day in range(days):
        for sid in station_ids:
            row = {"station_id": sid, "날짜": f"2025-11-0{day + 1}"}
            for hour in range(5, 10):
                row[f"net_{hour:02d}"] = 5 if sid == "A" else -1
            rows.append(row)
    return pd.DataFrame(rows)


def _stations(station_ids, stock=3, parking_lot=20):
    return pd.DataFrame({"station_id": list(station_ids),
                         "stock": stock, "parking_lot": parking_lot})


def test_stockout_denominator_follows_the_population_not_the_plan(bc):
    """같은 계획이라도 **모집단이 다르면 다른 값**이 나온다는 것을 고정한다.

    이것이 1.26.56에서 고친 결함의 본질이다. `stockout()`은 주어진 집합 위의
    *평균*이므로, 방법마다 자기 후보를 넘기면 분모가 방법마다 달라진다.
    B2(z=0)만 후보가 다른 집합이었고, 그래서 논문 6.3의 B2 행은 다른 자로
    잰 값이었다 — **재배치 전 값부터** 2.63 대 2.00으로 어긋났다.
    """
    net = _net(["A", "B", "C", "D"])
    좁은_집합 = _stations(["A"])
    넓은_집합 = _stations(["A", "B", "C", "D"])

    좁게 = bc.stockout(net, 좁은_집합, {}, "_05_10")
    넓게 = bc.stockout(net, 넓은_집합, {}, "_05_10")

    assert 좁게[0] is not None and 넓게[0] is not None
    assert 좁게 != 넓게, (
        "모집단이 달라도 같은 값이 나온다면 이 테스트가 지키려는 성질이 사라진 것이다")


def test_all_methods_are_scored_on_one_population(bc):
    """run_duration이 방법마다 다른 모집단으로 점수를 매기지 않는지 본다.

    `stockout()` 호출부가 `candidates`(방법마다 다름)가 아니라 `population`
    (회차마다 하나)을 넘겨야 한다. 되돌리면 B2가 다시 혼자 다른 자로 잰다.
    """
    import inspect

    source = inspect.getsource(bc.run_duration)
    assert "stockout(net, population," in source, (
        "결품 점수는 공통 모집단 위에서 매겨야 한다")
    assert "stockout(net, candidates," not in source, (
        "방법의 자기 후보 집합으로 점수를 매기면 분모가 방법마다 달라진다")
