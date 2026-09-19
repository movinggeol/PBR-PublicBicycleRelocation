"""논문 화면 캡처 도구 (`tools/capture_thesis_screens.py`).

브라우저를 띄우는 시험은 하지 않는다(Playwright는 검증 도구라 requirements.txt에 없다).
지키는 것은 **찍기 전의 판단**이다:
  1. 파일 이름이 그림 번호를 따른다 — 원고의 `![그림 4-8](…그림4-8_…)`와 어긋나면 엉뚱한 화면이 실린다.
  2. 모르는 번호면 아무것도 하지 않고 1로 끝난다.
  3. 외부 API를 부르는 4-9는 `--live` 없이 찍지 않는다 — 서버에 닿기 전에 건너뛴다.
  4. 대시보드가 없으면 무엇을 띄울지 말하고 1로 끝난다.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tool(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "capture_thesis_screens", ROOT / "tools/capture_thesis_screens.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "OUT", tmp_path)
    return module


def test_파일_이름이_그림_번호를_따르고_원고와_맞는다(tool):
    items = tool.plan()
    assert [i["key"] for i in items] == ["4-4", "4-6", "4-7", "4-8", "4-9"]
    chapter = (ROOT / "docs/연구/논문/4장_시스템설계.md").read_text(encoding="utf-8")
    for item in items:
        assert item["name"].startswith(f"그림{item['key']}_")
        assert f"{item['name']}.png" in chapter, f"원고가 {item['name']}을 가리키지 않는다"


def test_모르는_번호면_아무것도_하지_않는다(tool, tmp_path, capsys):
    assert tool.main(["--only", "4-4,4-5"]) == 1
    assert "모르는 그림" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


def test_실시간_대조는_live_없이_서버에_닿지_않는다(tool, monkeypatch, capsys):
    monkeypatch.setattr(tool, "server_up", lambda *a, **k: pytest.fail("서버를 불렀다"))
    assert tool.main(["--only", "4-9"]) == 0
    assert "--live" in capsys.readouterr().out


def test_대시보드가_없으면_띄울_명령을_말한다(tool, capsys):
    assert tool.main(["--only", "4-6", "--base", "http://127.0.0.1:9"]) == 1
    assert "python -m webapp" in capsys.readouterr().out
