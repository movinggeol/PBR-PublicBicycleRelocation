"""버전 번호가 또 겹치지 않는지 — `tools/check_consistency.py`를 검증한다.

버전 번호는 손으로 매긴다. 두 세션이 나란히 일하면 같은 번호대를 각자 소진하고,
그 사실은 한참 뒤 정합성 점검에서야 드러난다. **네 번 났다**(1.25.3 · 1.26.45 ·
1.26.47/48 · 1.26.68~74). 규칙은 1.25.3 때 이미 문서에 적혔지만 지켜지지 않았다.

그래서 규칙을 검사로 옮겼다. 여기서 지키려는 것: **검사기가 실제로 걸러 내는가.**
현재 문서를 통과시키는 것만으로는 부족하다 — 통과만 하고 아무것도 못 잡는
검사기는 규칙이 없는 것과 같기 때문이다.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def checker():
    spec = importlib.util.spec_from_file_location(
        "check_consistency", PROJECT_ROOT / "tools" / "check_consistency.py")
    module = importlib.util.module_from_spec(spec)
    # dataclass가 정의될 때 sys.modules에서 자기 모듈을 찾으므로 먼저 등록한다.
    sys.modules["check_consistency"] = module
    spec.loader.exec_module(module)
    return module


def _problems(checker, text, tmp_path):
    """임시 버전 이력을 물려 검사 결과만 받는다."""
    fake = tmp_path / "버전관리.md"
    fake.write_text(text, encoding="utf-8")
    original = checker.CHANGELOG
    checker.CHANGELOG = fake
    try:
        return checker.check_version_numbers()
    finally:
        checker.CHANGELOG = original


def test_지금_버전이력은_통과한다(checker):
    """실제 문서가 규칙을 만족해야 한다 — 아니면 검사기를 켤 수 없다."""
    assert checker.check_version_numbers() == []


def test_새_번호가_겹치면_잡는다(checker, tmp_path):
    """네 번 났던 그 상황 — 두 세션이 같은 번호를 쓴다."""
    text = "## 1.26.95 - 하나\n\n본문\n\n## 1.26.95 - 둘\n\n본문\n"
    problems = _problems(checker, text, tmp_path)

    assert len(problems) == 1
    assert "1.26.95" in problems[0]


def test_새_겹침은_주석을_달아도_잡는다(checker, tmp_path):
    """예외가 아닌 번호는 설명 한 줄로 정당해지지 않는다 — 번호를 밀어야 한다.

    이게 없으면 다섯 번째 겹침도 주석만 붙이면 통과해 검사가 무의미해진다.
    """
    text = ("## 1.26.95 - 하나\n\n> 번호 겹침: 설명\n\n"
            "## 1.26.95 - 둘\n\n> 번호 겹침: 설명\n")
    problems = _problems(checker, text, tmp_path)

    assert len(problems) == 1
    assert "다음 번호로 미세요" in problems[0]


def test_남겨_두기로_한_겹침은_통과시킨다(checker, tmp_path):
    """1.26.88의 판단 — 밀면 다른 문서의 참조가 깨져 주석으로 갈음했다."""
    text = ("## 1.26.47 - 하나\n\n> 번호 겹침: 설명\n\n"
            "## 1.26.47 - 둘\n\n> 번호 겹침: 설명\n")

    assert _problems(checker, text, tmp_path) == []


def test_예외라도_주석이_없으면_잡는다(checker, tmp_path):
    """예외 목록이 '설명 없는 겹침'까지 삼켜 버리면 안 된다."""
    text = ("## 1.26.47 - 주석이 없다\n\n본문\n\n"
            "## 1.26.47 - 이쪽만 있다\n\n> 번호 겹침: 설명\n")
    problems = _problems(checker, text, tmp_path)

    assert len(problems) == 1
    assert "주석이 없습니다" in problems[0]


def test_최신이_맨_위가_아니면_잡는다(checker, tmp_path):
    """버전관리.md 6행이 '최신 버전이 맨 위'라고 선언한다."""
    text = "## 1.26.88 - 낮은 쪽\n\n본문\n\n## 1.26.89 - 높은 쪽\n\n본문\n"
    problems = _problems(checker, text, tmp_path)

    assert len(problems) == 1
    assert "최신이 맨 위" in problems[0]


def test_정상적인_내림차순은_조용하다(checker, tmp_path):
    text = "## 1.26.89 - 최신\n\n본문\n\n## 1.26.88 - 이전\n\n본문\n"

    assert _problems(checker, text, tmp_path) == []


def test_예외가_낀_자리의_정렬은_넘어간다(checker, tmp_path):
    """겹침을 남겨 둔 탓에 생긴 뒤엉킴까지 정렬 위반으로 세면 거짓 경보다."""
    text = ("## 1.26.48 - 하나\n\n> 번호 겹침: 설명\n\n"
            "## 1.26.47 - 하나\n\n> 번호 겹침: 설명\n\n"
            "## 1.26.48 - 둘\n\n> 번호 겹침: 설명\n\n"
            "## 1.26.47 - 둘\n\n> 번호 겹침: 설명\n")

    assert _problems(checker, text, tmp_path) == []
