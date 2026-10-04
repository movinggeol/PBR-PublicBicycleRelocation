"""다음 회차 예상(`webapp/upcoming_view.py`, 1.26.319) 테스트.

임시 DB(conftest의 `PBR_DB_PATH`)에 재고 한 틱 · 계획 한 개 · 순수요 하루를 넣고, 식이 ML 14번 베이스라인 ③과
같은 답을 내는지, 옛 재고와 없는 계획에서 목록을 내지 않고 물러나는지 본다.
"""
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db
from webapp import upcoming_view as uv

LAST = "2026-09-29 13:00"          # 화요일(평일) — 1시간 뒤 14:00은 10~15시 회차


def _seed(conn, *, last=LAST, day_type="weekday"):
    conn.execute("INSERT INTO runs (run_label, period, duration, created_at, day_type, kind) "
                 "VALUES ('p1', '26년 03월', '_10_15', '2026-09-29 10:00:00', ?, 'plan')", (day_type,))
    for sid, target in (("A", 10.0), ("B", 2.0), ("C", 5.0)):
        conn.execute("INSERT INTO rebalance_plan (run_label, duration, station_id, target_qty) "
                     "VALUES ('p1', '_10_15', ?, ?)", (sid, target))
    cols = [f"net_{h:02d}" for h in range(24)]
    for sid, net13 in (("A", 3.0), ("B", -1.0), ("C", 0.0)):
        values = [net13 if h == 13 else 0.0 for h in range(24)]
        conn.execute(f"INSERT INTO net_demand (period, date, station_id, {', '.join(cols)}) "
                     f"VALUES ('26년 03월', '2026-03-03', ?, {', '.join('?' * 24)})", (sid, *values))
    for sid, stock in (("A", 3), ("B", 12), ("C", 5)):
        conn.execute("INSERT INTO stock_history (observed_at, station_id, stock) VALUES (?, ?, ?)", (last, sid, stock))
        conn.execute("INSERT INTO stock_station_master (observed_on, station_id, station_name) "
                     "VALUES ('2026-09-29', ?, ?)", (sid, f"대여소{sid}"))
    conn.commit()


@pytest.mark.parametrize("hour, name", [(4, "_20_05"), (5, "_05_10"), (14, "_10_15"), (19, "_15_20"), (23, "_20_05")])
def test_시각을_회차로_가른다(hour, name):
    assert uv.duration_of(hour) == name


def test_평균_흐름을_10분_칸으로_나눠_더한다():
    mu = pd.DataFrame([[0.0] * 13 + [6.0] + [0.0] * 10], index=["A"], columns=range(24))
    # 13:30부터 1시간 = 13시의 남은 세 칸(3) + 14시 세 칸(0)
    assert uv.expected_outflow(mu, pd.Timestamp("2026-09-29 13:30"), 1)["A"] == pytest.approx(3.0)


def test_수거와_배송을_식대로_고른다():
    with db.session() as conn:
        _seed(conn)
    ctx = uv.context(1, now=datetime(2026, 9, 29, 13, 5))
    assert ctx["error"] is None and ctx["stale"] is None
    assert (ctx["duration"], ctx["day_type"], ctx["plan_label"]) == ("_10_15", "평일", "p1")
    # A: 3 − 3 = 0 → 격차 +10 배송 · B: 12 + 1 = 13 → 격차 −11 수거 · C: 격차 0 → 대상 아님
    assert [r["station_id"] for r in ctx["drop"]] == ["A"] and ctx["drop"][0]["gap"] == pytest.approx(10.0)
    assert [r["station_id"] for r in ctx["pick"]] == ["B"] and ctx["pick"][0]["gap"] == pytest.approx(-11.0)
    assert ctx["drop"][0]["name"] == "대여소A"


def test_옛_재고로는_예상하지_않는다():
    with db.session() as conn:
        _seed(conn)
    ctx = uv.context(1, now=datetime(2026, 9, 29, 15, 0))
    assert ctx["stale"] == 120 and ctx["pick"] == [] and ctx["drop"] == []


def test_맞는_계획이_없으면_목록을_내지_않는다():
    with db.session() as conn:
        _seed(conn, day_type="holiday")       # 평일 10~15시 계획이 없다
    ctx = uv.context(1, now=datetime(2026, 9, 29, 13, 5))
    assert "계획이 아직 없어" in ctx["error"] and ctx["drop"] == []


def test_빈_DB와_엉뚱한_지평에서도_화면이_뜬다():
    from fastapi.testclient import TestClient
    from webapp.app import app

    response = TestClient(app).get("/upcoming?hours=7")
    assert response.status_code == 200
    assert "수집한 재고가 없습니다" in response.text
    assert 'href="/upcoming?hours=1" aria-current="page"' in response.text, "열지 않은 지평은 1시간으로 물러난다"
