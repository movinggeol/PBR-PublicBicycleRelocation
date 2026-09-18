"""결원 스트레스 실험의 수렴 판정 (`experiments/structure/fleet_outage_stress.py`).

🔴 **미수렴을 값으로 지어내던 결함** (2026-09-18 발견). 창 안에서 수렴하지 않으면
`len(after) + 1`을 수렴 회차로 셌다. 12회차·6회차 복구에서 그 값은 7이고, EXPERIMENTS
25장의 "결원 5대 수렴 7.0회차"는 **씨앗 다섯 모두 미수렴**이었다. 30회차로 다시 재니
실제로 7회차째 수렴해 값은 우연히 맞았지만, 12회차 실험은 그것을 관측하지 못했다.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location(
        "fleet_outage_stress", ROOT / "experiments/structure/fleet_outage_stress.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_창_안에서_수렴하지_않으면_값을_지어내지_않는다():
    fo = load()
    assert fo.converge_at([30.0, 40.0], [80.0, 60.0, 45.0]) is None
    assert fo.converge_at([30.0, 40.0], [80.0, 39.0, 20.0]) == 2


def test_미수렴은_평균에서_빼고_따로_알린다(capsys):
    fo = load()
    기록 = ([{"회차": r, "결원중": True, "실패": False, "필요K": 12, "표준편차": 30.0} for r in range(2)]
          + [{"회차": r, "결원중": False, "실패": False, "필요K": 12, "표준편차": 80.0} for r in range(2, 4)])
    fo.summarize({5: [{"실패": 0, "기록": 기록}]}, rounds=4, recover_at=2)
    out = capsys.readouterr().out
    assert "1/1" in out, "미수렴 씨앗 수를 적지 않았다"
    assert "수렴하지 않은 씨앗이 1개" in out
    assert " 3.0" not in out, "미수렴을 창+1(=3)로 셌다"
