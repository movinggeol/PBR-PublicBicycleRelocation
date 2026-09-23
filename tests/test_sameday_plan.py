"""`tools/sameday_plan.py` — 회차 시작 시각에 계획을 세우는 무인 도구 (1.26.278).

이 도구는 추석처럼 **아무도 없는 날** 예약 작업으로 돈다. 그래서 틀려도 아무도 모른다 —
여기서 지키는 것은 넷이다.

  · 늦게 깨면 세우지 않는다(출발 재고가 회차 시작의 상태가 아니게 된다)
  · 휴일 계획을 평일에 세우지 않는다
  · TMAP을 부르지 않고, 스냅샷은 라이브로 뜬다
  · 스케줄러에 넘기는 인자는 ASCII다(인코딩이 끼어들 자리를 없앤다)
"""
import importlib.util
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def plan():
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    spec = importlib.util.spec_from_file_location(
        "sameday_plan", PROJECT_ROOT / "tools" / "sameday_plan.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_라벨과_작업_이름(plan):
    assert plan.label_for(date(2026, 9, 25), "_05_10", "holiday") == "2026-09-25 05 휴일 동시각"
    assert plan.label_for(date(2026, 9, 23), "_10_15", "weekday", "check") \
        == "2026-09-23 10 평일 동시각 check"
    assert plan.task_name(date(2026, 9, 25), "_20_05") == "PBR-sameday-20260925-20"
    assert plan.task_name(date(2026, 9, 25), "_20_05").isascii()


@pytest.mark.parametrize("hhmm, inside", [
    ("04:59", False), ("05:00", True), ("05:03", True), ("05:20", True), ("05:21", False),
])
def test_회차_시작에서_허용한_분_안에서만_세운다(plan, hhmm, inside):
    """StartWhenAvailable로 몇 시간 뒤에 깨어나도 **그때는 세우지 않는다** — 그 재고는
    회차 시작의 상태가 아니다."""
    now = datetime.fromisoformat(f"2026-09-25 {hhmm}")
    assert plan.within_window(now, "_05_10", 20) is inside


def test_밤_회차는_20시부터다(plan):
    assert plan.within_window(datetime(2026, 9, 25, 20, 3), "_20_05", 20)
    assert not plan.within_window(datetime(2026, 9, 25, 5, 3), "_20_05", 20)


def test_파이프라인_명령은_TMAP을_부르지_않고_스냅샷을_라이브로_뜬다(plan):
    command = plan.pipeline_command("python", "2026-09-25 05 휴일 동시각", "_05_10",
                                    "holiday", "26년 03월", "plan")
    assert "--skip-map" in command                  # 도로 한도는 집 PC 몫이다
    assert "--skip-api" not in command              # 물려받으면 출발 재고가 또 어긋난다
    assert command[command.index("--run-kind") + 1] == "plan"
    assert command[command.index("--day-type") + 1] == "holiday"


def test_스케줄러에_넘기는_것은_ASCII다(plan):
    """🔴 한글은 파이썬 안에서만 만든다. 작업 이름·인자에 한글이 섞이면 PowerShell과
    작업 스케줄러 사이에서 인코딩이 끼어든다."""
    script = plan.install_script(
        [("PBR-sameday-20260925-05", datetime(2026, 9, 25, 5, 3), "_05_10")], "holiday")
    assert "run --duration _05_10 --day-type holiday" in script
    assert "2026-09-25T05:03:00" in script
    assert script.isascii()
    with pytest.raises(ValueError):
        plan.install_script([], "holiday", extra="--tag 점검")


class _Clock(datetime):
    fixed = datetime(2026, 9, 23, 5, 3)            # 수요일 — 평일이다

    @classmethod
    def now(cls, tz=None):
        return cls.fixed


def test_휴일_계획을_평일에_세우지_않는다(plan, monkeypatch, tmp_path):
    monkeypatch.setattr(plan, "datetime", _Clock)
    monkeypatch.setattr(plan, "LOG_DIR", tmp_path)
    monkeypatch.setattr(plan.subprocess, "run",
                        lambda *a, **k: pytest.fail("평일인데 파이프라인을 띄웠다"))

    code = plan.main(["run", "--duration", "_05_10", "--day-type", "holiday"])

    assert code == 0
    assert "휴일이 아니다" in next(tmp_path.glob("*.log")).read_text(encoding="utf-8")


def test_늦게_깨면_세우지_않는다(plan, monkeypatch, tmp_path):
    class Late(_Clock):
        fixed = datetime(2026, 9, 25, 9, 40)        # 추석이지만 05시 회차에는 늦었다

    monkeypatch.setattr(plan, "datetime", Late)
    monkeypatch.setattr(plan, "LOG_DIR", tmp_path)
    monkeypatch.setattr(plan.subprocess, "run",
                        lambda *a, **k: pytest.fail("늦었는데 파이프라인을 띄웠다"))

    code = plan.main(["run", "--duration", "_05_10"])

    assert code == 0
    assert "넘겼다" in next(tmp_path.glob("*.log")).read_text(encoding="utf-8")
