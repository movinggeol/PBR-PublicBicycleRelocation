"""한글 변환의 인용 번호 매기기(tools/hwp/refs.py).

refs.py는 한글 프로그램 없이 도는 순수 계산이라 따로 시험한다.
"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def refs():
    spec = importlib.util.spec_from_file_location("hwp_refs", ROOT / "tools" / "hwp" / "refs.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_괄호형_인용을_처음_나온_순서의_번호로_바꾼다(refs):
    r = refs.Refs()
    assert r.cite("두 정식화를 제시하였다(Raviv, 2013).") == "두 정식화를 제시하였다[1]."
    assert r.cite("다시 보면(Raviv, 2013)") == "다시 보면[1]"


def test_라틴_확장_글자가_든_저자도_번호로_바꾼다(refs):
    """🔴 회귀(1.26.433) — 이름 규칙이 A-Za-z뿐이어서 Demšar의 인용이 글자로 남았다."""
    r = refs.Refs()
    assert r.cite("권장되는 방법이다(Demšar, 2006).") == "권장되는 방법이다[1]."
    assert r.cite("Demšar(2006)는 두 검정을 권한다.") == "Demšar[1]는 두 검정을 권한다."
    assert r.unknown == []


def test_곱셈_기호는_이름으로_세지_않는다(refs):
    r = refs.Refs()
    text = "19개월 × 시드 셋(2026)"
    assert r.cite(text) == text
