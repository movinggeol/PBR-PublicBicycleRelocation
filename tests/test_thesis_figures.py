"""논문 그림 생성 도구 검증 (`tools/make_thesis_figures.py`).

🔴 **354줄짜리인데 시험이 하나도 없었다.** 그런데 이 도구가 도는 때는
**논문 제출 직전 한 번**이다 — 그때 처음 깨지면 고칠 시간이 없다.

지키려는 규칙:
  1. **원천이 없어도 죽지 않는다.** 실험 CSV는 `.gitignore`에 걸린 `data/`가
     아니라 `experiments/`에 있지만, 다른 PC에는 없을 수 있다. 없으면
     *무엇이 없어서 건너뛰는지* 말하고 나머지를 계속 그려야 한다 — 그림 한
     장 때문에 여섯 장이 다 안 나오면 안 된다.
  2. **모르는 이름을 주면 아무것도 만들지 않고** 1로 끝난다. 오타 하나에
     그림이 반만 생기면 어느 것이 낡았는지 알 수 없다.
  3. **구조도(4-1)는 원천 없이도 그려진다** — 심사자가 가장 먼저 찾는
     그림이라 자료에 기대면 안 된다. 식만 그리는 3-2도 같다(2026-09-18).
  4. `FIGURES` 등록표와 실제 함수가 **어긋나지 않는다**. 함수만 만들고
     등록을 잊으면 그 그림은 조용히 안 나온다.

⚠️ 시험은 **저장 위치를 임시 폴더로 돌린다** — 안 그러면
`docs/연구/초안/그림`에 진짜 그림을 덮어쓴다.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "tools" / "make_thesis_figures.py"


def load_tool():
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    spec = importlib.util.spec_from_file_location("make_thesis_figures", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tool():
    return load_tool()


@pytest.fixture
def 격리(tool, tmp_path, monkeypatch):
    """저장 위치와 원천 위치를 임시 폴더로 돌린다.

    `ROOT`까지 함께 돌리는 이유는 도구가 안내 문구에서
    `path.relative_to(ROOT)`를 쓰기 때문이다 — 저장 위치만 옮기면 그 줄에서
    `ValueError`가 난다.
    """
    그림 = tmp_path / "그림"
    그림.mkdir()
    monkeypatch.setattr(tool, "ROOT", tmp_path)
    monkeypatch.setattr(tool, "OUT", 그림)
    monkeypatch.setattr(tool, "EXP", tmp_path / "없는-실험폴더")
    return 그림


def run_cli(tool, monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["make_thesis_figures.py", *argv])
    return tool.main()


def test_모르는_그림_이름이면_아무것도_만들지_않는다(tool, 격리, monkeypatch, capsys):
    assert run_cli(tool, monkeypatch, "--only", "6-1,없는그림") == 1
    out = capsys.readouterr().out
    assert "모르는 그림" in out
    assert list(격리.iterdir()) == [], "이름이 틀렸는데 일부를 만들었다"


def test_원천이_없어도_죽지_않고_무엇이_없는지_말한다(tool, 격리, monkeypatch, capsys):
    """실험 CSV가 없는 PC에서도 나머지는 나와야 한다."""
    assert run_cli(tool, monkeypatch, "--only", "6-1,6-2,6-3,5-1") == 0
    out = capsys.readouterr().out
    assert out.count("건너뜀") == 4
    assert "12개월 반복 결과" in out and "z 격자" in out
    assert list(격리.iterdir()) == []


def test_구조도는_원천_없이도_그려진다(tool, 격리, monkeypatch, capsys):
    """4-1은 자료가 아니라 글을 그린 것이라 어느 PC에서든 나와야 한다."""
    assert run_cli(tool, monkeypatch, "--only", "4-1") == 0
    capsys.readouterr()
    만든것 = [p.name for p in 격리.iterdir()]
    assert 만든것 == ["그림4-1_파이프라인.png"]
    assert (격리 / "그림4-1_파이프라인.png").stat().st_size > 0


def test_재배치량_완화는_원천_없이_운영_함수로_그려진다(tool, 격리, monkeypatch, capsys):
    """3-2는 식만 그린다 — 자료가 없어도 나와야 하고, 식을 옮겨 쓰지 않는다.

    그림이 `compute_rebal_qty()`를 부르지 않고 식을 따로 쓰면 운영 코드가 바뀌어도
    옛 식을 그린다. 원고의 '격차 5대→4대, 8대→6대, 실효 상한 9대'가 운영 함수에서
    그대로 나오는지 함께 본다.
    """
    import inspect

    import pandas as pd
    from pipeline.step0_collect.calculate_target_qty import compute_rebal_qty

    assert "compute_rebal_qty" in inspect.getsource(tool.fig_3_2)
    stats = pd.DataFrame({"mu": [5.0, 8.0, 55.6], "sigma": 0.0, "stock": 0.0,
                          "parking_lot": 1000.0})
    assert compute_rebal_qty(stats)["rebal_qty"].tolist() == [4, 6, 9]

    assert run_cli(tool, monkeypatch, "--only", "3-2") == 0
    capsys.readouterr()
    assert [p.name for p in 격리.iterdir()] == ["그림3-2_재배치량_완화.png"]


def test_DB가_비면_자료_그림은_건너뛰고_무엇이_없는지_말한다(tool, 격리, monkeypatch, capsys):
    """4-2·4-3·5-2·5-3·8-1·8-2는 DB의 대여이력·순수요·스냅샷을 읽는다. 시험 DB는 비어 있다."""
    assert run_cli(tool, monkeypatch, "--only", "4-2,4-3,5-2,5-3,8-1,8-2") == 0
    out = capsys.readouterr().out
    assert out.count("건너뜀") == 6
    assert list(격리.iterdir()) == []


def test_회차_구조_개념도는_원천_없이_그려진다(tool, 격리, monkeypatch, capsys):
    """2-1은 개념도다 — 자료가 없어도 나와야 한다."""
    assert run_cli(tool, monkeypatch, "--only", "2-1") == 0
    capsys.readouterr()
    assert [p.name for p in 격리.iterdir()] == ["그림2-1_회차구조.png"]


def test_남의_계산을_다시_구현하지_않는다(tool):
    """8-1·8-2는 원고 표와 같은 수를 말해야 한다 — 원천 스크립트의 상수·함수를 불러 쓴다.

    보고서 값을 그림 쪽에 다시 옮겨 적거나 거점 선정을 다시 구현하면 <표 8-3>·8.4.2와
    어긋나도 아무도 모른다.
    """
    import inspect
    src_81, src_82 = inspect.getsource(tool.fig_8_1), inspect.getsource(tool.fig_8_2)
    assert "survey_crosscheck.py" in src_81 and "REPORT_DAILY" in src_81
    assert "21632" not in src_81, "보고서 일별 값을 그림 쪽에 다시 적었다"
    for name in ("endpoint_counts", "demand_scores", "rank_sites", "candidates"):
        assert f"hub.{name}" in src_82, f"거점 선정의 {name}을 다시 구현했다"


def test_전부_돌려도_완주한다(tool, 격리, monkeypatch, capsys):
    """새 PC에서 `python tools/make_thesis_figures.py`를 그냥 친 상황."""
    assert run_cli(tool, monkeypatch) == 0
    out = capsys.readouterr().out
    assert "수치를 본문 표에 옮겨 적지 마십시오" in out, "마지막 경고가 빠졌다"
    assert (격리 / "그림4-1_파이프라인.png").exists()


def test_파일_이름이_그림_번호를_따른다(tool):
    """`fig_6_1`이 `그림6-2_…`로 저장하면 본문이 엉뚱한 그림을 가리킨다.

    함수를 복사해 새 그림을 만들면서 `save()`의 이름만 안 고치는 실수라,
    그림이 **덮어써져도** 아무도 모른다 — 그림 수가 그대로이기 때문이다.
    """
    import inspect
    import re

    for key, fn in tool.FIGURES.items():
        저장이름 = re.findall(r'save\(fig,\s*"([^"]+)"', inspect.getsource(fn))
        assert len(저장이름) == 1, f"{fn.__name__}이 save를 {len(저장이름)}번 부른다"
        assert 저장이름[0].startswith(f"그림{key}_"), (
            f"{fn.__name__}은 그림 {key}인데 {저장이름[0]!r}로 저장한다")


def test_등록표와_실제_함수가_어긋나지_않는다(tool):
    """함수만 만들고 등록을 잊으면 그 그림은 조용히 안 나온다."""
    함수들 = {name for name in dir(tool)
              if name.startswith("fig_") and callable(getattr(tool, name))}
    등록된 = {fn.__name__ for fn in tool.FIGURES.values()}
    assert 함수들 == 등록된, f"등록표에 없는 함수: {함수들 - 등록된}"

    # 열쇠는 그림 번호 그대로여야 파일 이름과 맞는다 (fig_6_1 → "6-1")
    for key, fn in tool.FIGURES.items():
        assert fn.__name__ == "fig_" + key.replace("-", "_")


def test_저장은_OUT_아래_png로_떨어진다(tool, 격리, capsys):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1])
    tool.save(fig, "시험-1", "시험용")

    assert (격리 / "시험-1.png").exists()
    assert "시험용" in capsys.readouterr().out
