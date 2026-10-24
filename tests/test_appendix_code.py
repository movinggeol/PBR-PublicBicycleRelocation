"""부록 B 발췌 도구 검증 (`tools/make_appendix_code.py`).

인자를 받지 않던 때는 `--help`에도 도움말 대신 부록을 다시 썼다(2026-10-24에 찾음). 코드와 부록이 같으면
해가 없지만, 잘못 부른 한 번이 부록을 조용히 바꿀 수 있다. 시험은 출력 경로를 임시 파일로 돌려
진짜 부록을 건드리지 않는다.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "tools" / "make_appendix_code.py"


@pytest.fixture
def tool(tmp_path, monkeypatch):
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    spec = importlib.util.spec_from_file_location("make_appendix_code", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = tmp_path / "부록_핵심코드.md"
    monkeypatch.setattr(module, "OUT", str(out))   # 절대 경로라 ROOT / OUT이 이 파일이 된다
    return module, out


def test_도움말은_부록을_다시_쓰지_않는다(tool, capsys):
    module, out = tool
    with pytest.raises(SystemExit) as stop:
        module.main(["--help"])
    assert stop.value.code == 0
    assert "usage" in capsys.readouterr().out
    assert not out.exists()


def test_모르는_인자를_주면_쓰지_않고_멈춘다(tool):
    module, out = tool
    with pytest.raises(SystemExit) as stop:
        module.main(["--bogus"])
    assert stop.value.code == 2
    assert not out.exists()


def test_인자가_없으면_부록을_쓴다(tool):
    module, out = tool
    assert module.main([]) == 0
    assert out.read_text(encoding="utf-8").startswith("# 부록 B 핵심 코드")
