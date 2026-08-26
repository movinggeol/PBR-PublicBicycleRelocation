"""결품 지도 테스트 — 실측 재고를 어떻게 읽는가.

지키는 것은 세 가지다.

1. **재배치로 고칠 수 있는 곳만 골라낸다** — '늘 빔'은 채워도 곧 비므로
   재배치 대상이 아니다. 섞어 세면 작업 대상이 부풀려진다.
2. **관측이 없는 시간을 '결품 없음'으로 읽지 않는다** — 수집 창이 평일
   09~17시라 야간은 아예 모른다. 모르는 것과 문제없는 것은 다르다.
3. **집계일 뿐 모델이 아니다** — 며칠치로도 돌아가야 한다.
"""
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_tool():
    path = ROOT / "tools" / "stockout_map.py"
    spec = importlib.util.spec_from_file_location("stockout_map", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def frame(rows):
    """(station_id, stock) 목록을 관측 프레임으로."""
    data = []
    for sid, stocks in rows:
        for i, stock in enumerate(stocks):
            data.append({"station_id": sid, "station_name": sid,
                         "stock": stock, "hour": 9 + i % 8,
                         "observed_at": pd.Timestamp("2026-08-25 09:00")
                                        + pd.Timedelta(minutes=10 * i)})
    out = pd.DataFrame(data)
    out["date"] = out["observed_at"].dt.date
    return out


def test_늘_빈_곳은_재배치_대상에서_뺀다():
    """관측 내내 0이면 채워도 곧 빈다 — 배치·증설 문제이지 재배치가 아니다."""
    tool = load_tool()
    stat = tool.classify(frame([("EMPTY", [0, 0, 0, 0]),
                                ("MOVER", [0, 2, 0, 3]),
                                ("FULL", [5, 4, 6, 5])]))
    kind = dict(zip(stat["station_id"], stat["구분"]))

    assert kind["EMPTY"] == "늘 빔"
    assert kind["MOVER"] == "오가는 곳"
    assert kind["FULL"] == "늘 있음"


def test_시각별_결품은_오가는_곳만_센다():
    """'늘 빔'을 섞으면 결품률이 구조적으로 부풀려진다."""
    tool = load_tool()
    data = frame([("EMPTY", [0] * 4), ("MOVER", [0, 2, 0, 2])])
    risk = tool.hourly_risk(data, movers={"MOVER"})

    assert not risk.empty
    assert risk["결품비율"].max() <= 100
    # MOVER만 세면 절반이 결품이다. EMPTY까지 세면 75%가 된다.
    assert risk["관측"].sum() == 4


def test_관측이_없는_시간대를_문제없음으로_읽지_않는다():
    """수집 창 밖은 '결품 없음'이 아니라 **모르는 것**이다."""
    tool = load_tool()
    data = frame([("A", [1, 0, 1, 0])])       # 9~12시만 관측
    cov = tool.coverage(data)

    night = cov[cov["시간대"] == "_20_05"].iloc[0]
    assert night["관측된시간"] == 0
    assert "관측 없음" in night["판정"]


def test_며칠치로도_돌아간다():
    """모델이 아니라 집계다 — 자료가 적어도 크래시하지 않아야 한다."""
    tool = load_tool()
    stat = tool.classify(frame([("A", [0, 1])]))
    assert len(stat) == 1
    assert 0 <= stat["빈비율"].iloc[0] <= 1
