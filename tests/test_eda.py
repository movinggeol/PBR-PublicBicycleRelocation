"""EDA 그래프 생성 검증 (P3-6).

`month_graph`가 이름과 달리 `print()`만 하던 것을 실제 그래프로 바꿨다(1.26.77).
여기서 지키는 것은 셋이다:

1. **화면에 띄우지 않는다** — 파이프라인이 subprocess로 돌리므로 `plt.show()`가
   들어오면 창이 뜬 채 **파이프라인 전체가 멈춘다**(사람이 닫을 때까지).
2. **글꼴 경로를 하드코딩하지 않는다** — 참고 코드에 있던
   `C:/Windows/Fonts/malgun.ttf`를 그대로 쓰면 리눅스·CI에서 죽는다.
3. **자료가 없어도 죽지 않는다** — EDA는 파이프라인의 선택 단계다.
"""
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

EDA_PATH = Path(__file__).resolve().parents[1] / "step0_eda" / "EDA.py"


def _source() -> str:
    return EDA_PATH.read_text(encoding="utf-8")


def _code_only() -> str:
    """주석·docstring을 걷어낸 **실행되는 코드**만 돌려준다.

    이 파일의 docstring에는 "`plt.show()`를 부르면 멈춘다", "`C:/Windows/Fonts/…`를
    쓰면 죽는다" 같은 **하지 말라는 예시**가 적혀 있다. 원문을 그대로 훑으면
    그 설명이 걸려 테스트가 헛짚는다(실제로 그랬다).
    """
    import tokenize

    kept = []
    with open(EDA_PATH, "rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type == tokenize.COMMENT:
                continue
            if tok.type == tokenize.STRING and tok.line.strip().startswith(tok.string[:3]):
                continue        # 홀로 선 문자열 = docstring
            kept.append(tok.string)
    # ⚠️ 토큰을 그냥 이어 붙이면 `plt.show()`가 `plt . show ( )`로 벌어져
    #    문자열 검색이 헛돈다 — 공백을 전부 걷어낸다(실제로 놓쳤다).
    return "".join("".join(token.split()) for token in kept)


@pytest.fixture
def rentals():
    """두 달치 가짜 대여이력. 달마다 일수가 다르게 만든다 —
    하루 평균으로 재지 않으면 이 차이가 그래프를 왜곡한다."""
    stamps = []
    for day in range(1, 29):            # 2월: 28일
        stamps += [f"2026-02-{day:02d} 08:30:00"] * 3
    for day in range(1, 32):            # 3월: 31일
        stamps += [f"2026-03-{day:02d} 18:10:00"] * 3
    return pd.DataFrame({"대여일시": pd.to_datetime(stamps)})


def test_그래프를_화면에_띄우지_않는다():
    """`plt.show()`가 들어오면 파이프라인이 그 자리에서 멈춘다.

    run_pipeline.py가 EDA.py를 subprocess로 돌리므로, 창이 뜨면 사람이 닫아
    줄 때까지 **다음 단계가 시작되지 않는다.** 배치에서는 파일로 남겨야 한다.
    """
    code = _code_only()
    source = _source()

    assert "plt.show" not in code, "plt.show()는 파이프라인을 멈춘다"
    assert 'matplotlib.use("Agg")' in source, "창 없는 백엔드를 고정하지 않았다"
    # use()는 pyplot을 들이기 **전에** 불러야 효과가 있다.
    assert source.index('matplotlib.use("Agg")') < source.index("import matplotlib.pyplot"), \
        "matplotlib.use()가 pyplot import보다 뒤에 있다 — 백엔드가 안 바뀐다"


def test_글꼴_경로를_하드코딩하지_않는다():
    """참고 코드(experiments/learning/matplotlib_month_graph.py)에 있던
    `C:/Windows/Fonts/malgun.ttf`를 그대로 가져오면 리눅스·CI에서 죽는다."""
    code = _code_only().lower()

    assert "windows/fonts" not in code and "\\\\fonts\\\\" not in code, \
        "글꼴 경로가 하드코딩됐다 — 그 파일이 없는 환경에서 죽는다"
    # ⚠️ `.ttf`만으로 찾으면 안 된다 — `font_manager.fontManager.ttflist`가
    #    걸린다(설치된 글꼴을 훑는 정상 API다). 확장자로 **끝나는 문자열
    #    리터럴**만 잡는다.
    assert not re.search(r'["\'][^"\']*\.tt[fc]["\']', code), \
        "글꼴 파일을 경로로 직접 가리키고 있다"


def test_세_그래프를_파일로_남긴다(rentals, tmp_path, monkeypatch):
    """월별·시간대별·요일별 PNG가 실제로 만들어진다."""
    import importlib

    eda = importlib.import_module("step0_eda.EDA") if "step0_eda" in sys.modules else None
    if eda is None:
        spec = importlib.util.spec_from_file_location("eda_module", EDA_PATH)
        eda = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(eda)

    out = tmp_path / "EDA"
    monkeypatch.setattr(eda, "EDA_DIR", out)

    eda._use_korean_font()
    month = eda.month_graph(rentals.copy())
    hour = eda.hour_graph(rentals.copy())
    weekday = eda.weekday_graph(rentals.copy())
    assert month and hour and weekday

    made = sorted(p.name for p in out.glob("*.png"))
    assert made == ["시간대별_대여량.png", "요일별_대여량.png", "월별_대여량.png"], made
    for png in out.glob("*.png"):
        assert png.stat().st_size > 1000, f"{png.name}이 비어 있다"


def test_HTML도_함께_낸다(rentals, tmp_path, monkeypatch):
    """PNG는 문서용, HTML은 화면용이다.

    `charts.py`가 첫머리에 적어 둔 대로 **이미지는 다크 모드에서 흰 판이 뜨고
    커서를 대도 값을 못 읽는다.** 그래서 화면에서 볼 것은 인라인 SVG로 낸다.
    """
    import importlib

    spec = importlib.util.spec_from_file_location("eda_html", EDA_PATH)
    eda = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(eda)

    out = tmp_path / "EDA"
    monkeypatch.setattr(eda, "EDA_DIR", out)
    eda._use_korean_font()

    path = eda.write_html(
        eda.month_graph(rentals.copy()),
        eda.hour_graph(rentals.copy()),
        eda.weekday_graph(rentals.copy()),
        span="2026-02-01 ~ 2026-03-31", rows=len(rentals))

    html = path.read_text(encoding="utf-8")
    assert html.count("<svg") == 3, "그래프 세 장이 아니다"
    assert "viz-divider" in html, "회차 경계선이 없다"
    assert "viz-bar warn" in html, "주말 강조가 없다"
    # 색만으로 뜻을 나르지 않는다 — 글자 설명이 함께 있어야 한다.
    assert "붉은 선 = 회차 경계" in html and "붉은 막대 = 주말" in html
    # 커서를 대면 값이 뜬다(charts.py 규약).
    assert 'data-tip=' in html
    # 다크 모드를 따라간다 — 이것이 PNG로는 안 되는 것이다.
    assert "prefers-color-scheme: dark" in html


def test_HTML은_charts를_쓴다():
    """그리는 규칙이 웹 화면과 갈리지 않게 `webapp/charts.py`를 재사용한다.

    여기서 SVG를 직접 조립하기 시작하면 격자·눈금·커서 설명이 두 벌이 되고,
    한쪽만 고치게 된다.
    """
    code = _code_only()

    assert "fromwebappimportcharts" in code, "charts.py를 쓰지 않는다"
    assert "<svg" not in code, "EDA가 SVG를 직접 조립하고 있다 — charts.py를 쓸 것"


def test_자료가_없으면_건너뛴다(tmp_path, monkeypatch):
    """EDA는 선택 단계다. 빈 자료에 죽으면 파이프라인 전체가 멈춘다."""
    import importlib

    spec = importlib.util.spec_from_file_location("eda_empty", EDA_PATH)
    eda = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(eda)

    monkeypatch.setattr(eda, "EDA_DIR", tmp_path / "EDA")
    empty = pd.DataFrame({"대여일시": pd.to_datetime([])})

    assert eda.month_graph(empty) is None, "빈 자료에서 그래프를 만들려 했다"


def test_EDA_산출물_폴더가_규약에_등록됐다():
    """`ensure_output_dirs()`가 만들지 않으면 첫 실행에서 저장이 실패한다."""
    import inspect

    import project_config

    assert '"EDA"' in inspect.getsource(project_config.ensure_output_dirs)


# ── 이상치 제거가 원본을 깎던 문제 (구역 B 검토, 1.26.129) ────────
#
# `concat_1year_file.preprocessing()`이 읽은 파일에 그대로 다시 썼다.
# IQR은 **잘라 낸 뒤 다시 재면 좁아지므로 멱등이 아니다** — 같은 파일에
# 반복 적용하면 계속 깎인다(50만 행 표본에서 4회에 15%). 그런데 이 단계는
# `--skip-eda` 없이 `python run_pipeline.py`를 치면 매번 실행되고, 대상은
# 1.5GB짜리 원천 이력이며 백업이 없다.


def _concat_module():
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "step0_eda" / "concat_1year_file.py"
    spec = importlib.util.spec_from_file_location("_concat_1year_file", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _skewed_frame(n: int = 4000) -> pd.DataFrame:
    """꼬리가 두꺼운 이용시간·이용거리. IQR 울타리가 실제로 무언가를 자른다."""
    import numpy as np

    rng = np.random.default_rng(20260906)
    minutes = rng.lognormal(mean=2.6, sigma=0.9, size=n)
    km = rng.lognormal(mean=0.7, sigma=0.8, size=n)
    return pd.DataFrame({"이용시간(분)": minutes, "이용거리(km)": km})


def test_이상치_제거가_원본을_건드리지_않는다(tmp_path):
    module = _concat_module()

    source = tmp_path / "원본.csv"
    cleaned = tmp_path / "정리본.csv"
    _skewed_frame().to_csv(source, index=False, encoding="utf-8")
    원본_바이트 = source.read_bytes()

    module.preprocessing(str(source), str(cleaned))

    assert source.read_bytes() == 원본_바이트, "이상치 제거가 원본을 덮어썼다"
    assert cleaned.exists(), "정리 결과를 남기지 않았다"
    assert len(pd.read_csv(cleaned)) < len(pd.read_csv(source)), \
        "아무것도 자르지 않았다면 이 시험이 무엇을 지키는지 알 수 없다"


def test_여러_번_돌려도_같은_결과가_나온다(tmp_path):
    """멱등성. 원본이 그대로 남으므로 입력이 늘 같고, 결과도 같아야 한다."""
    module = _concat_module()

    source = tmp_path / "원본.csv"
    _skewed_frame().to_csv(source, index=False, encoding="utf-8")

    행수 = []
    for i in range(3):
        out = tmp_path / f"정리본{i}.csv"
        module.preprocessing(str(source), str(out))
        행수.append(len(pd.read_csv(out)))

    assert len(set(행수)) == 1, f"돌릴 때마다 결과가 달라진다: {행수}"


def test_입력과_같은_파일에_쓰려_하면_멈춘다(tmp_path):
    """멱등을 깨는 유일한 방법이므로 **큰 소리로** 막는다."""
    module = _concat_module()

    source = tmp_path / "원본.csv"
    _skewed_frame(200).to_csv(source, index=False, encoding="utf-8")

    with pytest.raises(SystemExit) as err:
        module.preprocessing(str(source), str(source))
    assert "같은 파일" in str(err.value)
    assert len(pd.read_csv(source)) == 200, "막았는데도 원본이 바뀌었다"
