"""시간 길이는 "5분 36초"로 쓴다 — "5.6분" 같은 소수 분을 화면 · 로그에 내지 않는다 (2026-10-06).

사용자 지시: *"예상시간은 5.6분처럼 쓰지 말고, 5분 30초처럼 써라."* 0.6분이 몇 초인지 읽는 사람이 다시 셈해야
하는 글이 홈(실행 예상) · 작업지시서(예상 · 누적) · 차량 화면 · KPI 표 · 실행 로그에 흩어져 있었다.

지키려는 것은 둘이다.
  1. **글을 만드는 곳이 한 벌이다** — `project_config.format_seconds/minutes/estimate`. 웹은 Jinja 필터
     (`dur` · `secs` · `est`)로, 실행 폼의 스크립트는 같은 반올림 규칙으로 쓴다.
  2. **소수 분이 되살아나지 않는다** — 템플릿 · 파이프라인 출력에서 옛 꼴을 찾는다.
값(CSV · DB · 비교에 쓰는 숫자)은 그대로 분 · 초 숫자다 — 바뀌는 것은 사람이 읽는 글뿐이다.
"""
import re
from pathlib import Path

import pytest

from project_config import format_estimate, format_minutes, format_seconds

_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("seconds, text", [
    (336, "5분 36초"),          # 5.6분
    (330, "5분 30초"),
    (11226, "3시간 7분 6초"),   # 187.1분 — 작업지시서의 최장 작업
    (7200, "2시간"),            # 0인 칸은 적지 않는다
    (3605, "1시간 5초"),
    (30, "30초"),
    (0, "0초"),
    (0.4, "0초"),
    (59.6, "1분"),              # 반올림이 칸을 넘긴다
    (2.5, "3초"),               # 반은 올린다 — 스크립트의 Math.round와 같다(round()는 짝수 쪽으로 간다)
])
def test_초를_시간_분_초로_쓴다(seconds, text):
    assert format_seconds(seconds) == text


def test_부호와_빈값():
    assert format_seconds(-90) == "−1분 30초"
    assert format_seconds(90, signed=True) == "+1분 30초"
    assert format_seconds(0, signed=True) == "0초", "0에 부호를 붙이면 '평균보다 많다'로 읽힌다"
    assert format_seconds(None) == "—"
    assert format_seconds(float("nan")) == "—"
    assert format_seconds("abc") == "—"


def test_템플릿에서_값이_없어도_죽지_않는다():
    """`{{ sheet.minutes }}`는 값이 없으면 빈 글이었는데 `| dur`는 jinja2의 '정의 안 된 값'을 float()로 바꾸다 예외를 냈다
    (작업지시서 화면 전체가 500 — `test_대조_링크가_보던_차량을_달고_간다`가 잡았다). 없는 값은 '—'다."""
    import jinja2

    env = jinja2.Environment()
    env.filters["dur"] = format_minutes
    env.filters["est"] = format_estimate
    assert env.from_string("{{ s.minutes | dur }}").render(s={}) == "—"
    assert env.from_string("{{ x | est }}").render() == "—"


def test_분을_받아도_같은_글이다():
    assert format_minutes(5.6) == "5분 36초"
    assert format_minutes(187.1) == "3시간 7분 6초"
    assert format_minutes(5.5) == "5분 30초"
    assert format_minutes(-1.5, signed=True) == "−1분 30초"
    assert format_minutes(None) == "—"


@pytest.mark.parametrize("seconds, text", [
    (42, "40초"),               # 1분 미만은 5초 단위
    (57.5, "1분"),
    (336, "5분 40초"),          # 그 위는 10초 단위 — 예상은 실행마다 수십 초 흔들린다
    (375.8, "6분 20초"),
    (496.0, "8분 20초"),
    (None, "—"),
])
def test_예상은_10초_단위로_반올림한다(seconds, text):
    assert format_estimate(seconds) == text


def test_실행_폼_스크립트가_같은_규칙으로_셈한다():
    """체크박스를 누를 때 스크립트가 다시 셈한다 — 서버와 규칙이 다르면 누르는 순간 글이 바뀐다."""
    body = (_ROOT / "webapp" / "templates" / "index.html").read_text(encoding="utf-8")
    assert "var step = seconds < 60 ? 5 : 10;" in body
    assert 'if (h) parts.push(h + "시간");' in body
    assert "toFixed(1)) + \"분\"" not in body, "옛 소수 분 글이 남았다"


# 소수 분이 되살아나는 꼴. 정수 분(설정값 '120분', '12분 전')은 대상이 아니다.
_TEMPLATE_DECIMAL = re.compile(r"(?<!age_)(minutes|_min)\s*\}\}\s*(?:<[^>]*>\s*)*분")
_SCRIPT_DECIMAL = re.compile(r"toFixed\(1\)[^;\n]*분")
_PY_DECIMAL = re.compile(r"\.1f\}\s*분")


def test_템플릿이_분_숫자에_분을_붙이지_않는다():
    """`{{ stop.minutes }}분`은 "12.3분"이 된다 — `{{ stop.minutes | dur }}`를 쓴다."""
    found = []
    for path in sorted((_ROOT / "webapp" / "templates").glob("*.html")):
        text = path.read_text(encoding="utf-8")
        found += [f"{path.name}: {m.group(0)}" for m in _TEMPLATE_DECIMAL.finditer(text)]
        found += [f"{path.name}: {m.group(0)}" for m in _SCRIPT_DECIMAL.finditer(text)]
    assert not found, "소수 분을 쓰는 자리가 남았다:\n" + "\n".join(found)


def test_정렬되는_표의_시간_칸은_숫자로_정렬한다():
    """"3시간 7분 6초"는 숫자가 셋이라 정렬 스크립트(base.html)가 **글자 열**로 본다 — "10분"이 "9분"보다
    앞에 온다. 정렬 스크립트가 먼저 읽는 `data-sort`에 분 숫자를 단다."""
    tpl = _ROOT / "webapp" / "templates"
    vehicles = (tpl / "vehicles.html").read_text(encoding="utf-8")
    kpi = (tpl / "kpi.html").read_text(encoding="utf-8")
    assert 'data-sort="{{ v.minutes' in vehicles, "차량별 누적 시간이 글자로 정렬된다"
    assert 'data-sort="{{ a.minutes' in vehicles, "배정 이력의 작업 시간이 글자로 정렬된다"
    assert 'data-sort="{{ r.max_cluster_minutes' in kpi, "실행 표의 최장 작업이 글자로 정렬된다"
    assert "cell.dataset.sort" in (tpl / "base.html").read_text(encoding="utf-8")


def test_파이썬_출력이_소수_분을_쓰지_않는다():
    """실행 로그(웹의 실행 화면이 그대로 보여 준다)의 "187.1분" 같은 글."""
    found = []
    for folder in ("webapp", "pipeline", "tools"):
        for path in sorted((_ROOT / folder).rglob("*.py")):
            for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if _PY_DECIMAL.search(line):
                    found.append(f"{path.relative_to(_ROOT)}:{no}: {line.strip()}")
    assert not found, "소수 분을 찍는 줄이 남았다:\n" + "\n".join(found)


def test_그래프의_값_글도_시간_분_초다():
    from webapp import charts

    svg = charts.deviation_hbar(["V01", "V02"], [100.0, 80.0], title="t", unit="분",
                                fmt=format_minutes)
    assert "+10분" in svg and "−10분" in svg and "1시간 30분" in svg
    assert not re.search(r"\d\.\d+분", svg), "막대 글에 소수 분이 남았다"
    old = charts.deviation_hbar(["V01", "V02"], [100.0, 80.0], title="t", unit="분")
    assert "+10.0분" in old, "fmt를 안 주면 예전 글 그대로다(다른 단위의 그래프)"

    line = charts.line(["a", "b"], [187.1, 150.0], title="최장 작업", unit="분", fmt=format_minutes)
    assert "2시간 30분" in line and "150.00분" not in line
