"""K 운영 뒤 도구 둘: 손잡이 다시 고르기(pick_model_check) · pick_rule별 사후 채점(pick_rule_score) (1.26.380)."""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def check():
    return _load("pick_model_check")


@pytest.fixture(scope="module")
def score():
    return _load("pick_rule_score")


def test_k_고르기는_56장과_같다(check):
    # 90%를 넘는 k 가운데 가장 큰 것
    assert check.choose_k({0.5: 1.0, 0.7: 0.93, 0.8: 0.88, 0.9: 0.6}) == 0.7
    # 닿는 k가 없으면 가장 많이 채우는 k
    assert check.choose_k({0.7: 0.8, 0.8: 0.85, 0.9: 0.5}) == 0.8
    assert check.K_GRID == (0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95)
    assert check.K_FILL == 0.90


def test_k_격자는_실험과_같다(check):
    sys.path.insert(0, str(ROOT / "experiments" / "structure"))
    spec =importlib.util.spec_from_file_location("pick_mix_t", ROOT / "experiments" / "structure" / "pick_mix.py")
    mix = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mix)
    assert tuple(mix.K_GRID) == check.K_GRID
    assert mix.K_FILL == check.K_FILL


def _grid():
    """대여소 셋, 회차 시작 t = 05:00, 뒤 5시간."""
    idx = pd.date_range("2026-10-21 04:50", periods=32, freq="10min")
    a = np.full(32, 10.0)                  # 늘 10대 — 4대 빼도 남는다
    b = np.r_[np.full(10, 6.0), np.full(22, 3.0)]   # 3대까지 내려간다 — 4대 빼면 빈다
    c = np.r_[np.full(5, 2.0), np.zeros(27)]        # 배송지 — 비어 간다
    return pd.DataFrame({"A": a, "B": b, "C": c}, index=idx)


def test_사후_채점은_53장의_자로_센다(score):
    grid = _grid()
    t = pd.Timestamp("2026-10-21 05:00")
    cap = pd.Series({"A": 20.0, "B": 20.0, "C": 20.0})
    lists = {"계획": pd.Series({"A": -4.0, "C": 4.0}), "같은 회차의 현행": pd.Series({"B": -4.0, "C": 4.0})}
    rows = score.score_plan(grid, t, lists, cap)
    got = {(r["목록"], r["지평"]): r for r in rows}
    assert set(got) == {("계획", 18), ("계획", 30), ("같은 회차의 현행", 18), ("같은 회차의 현행", 30)}
    assert got[("계획", 30)]["수거무해"] == 1          # A: 최저 10 − 4 ≥ 1
    assert got[("같은 회차의 현행", 30)]["수거무해"] == 0  # B: 최저 3 − 4 < 1
    assert got[("계획", 30)]["배송적중"] == 1          # C: t에 2대 → 창 안에 0
    frame = pd.DataFrame([{"run_label": "x", "duration": "_05_10", "pick_rule": "K0.7", **r} for r in rows])
    out = score.summarize(frame).set_index(["목록", "지평"])
    assert out.loc[("계획", 30), "수거무해율"] == 1.0
    assert out.loc[("같은 회차의 현행", 30), "수거무해율"] == 0.0
    assert out.loc[("계획", 30), "계획"] == 1


def test_사후_채점은_창이_모자라면_세지_않는다(score):
    grid = _grid().iloc[:20]                # 5시간이 안 찼다
    t = pd.Timestamp("2026-10-21 05:00")
    cap = pd.Series({"A": 20.0, "B": 20.0, "C": 20.0})
    assert score.score_plan(grid, t, {"계획": pd.Series({"A": -4.0})}, cap) == []
    assert score.score_plan(grid, pd.Timestamp("2026-10-21 03:00"), {"계획": pd.Series({"A": -4.0})}, cap) == []
