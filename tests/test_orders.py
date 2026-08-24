"""작업지시서와 실시간 재고 대조 테스트.

지키는 것은 두 가지다.

1. **지시서와 대조 화면이 같은 수량을 말한다** — 대조가 계획 요구량(`rebal_qty`)이
   아니라 실제 지시량(`vrp_plan.qty`)을 봐야 한다.
2. **집행 가능 판정이 계획과 같은 기준을 쓴다** — 내려놓을 수 있는 상한이
   `parking_lot × TARGET_QTY_UPPER_RATIO`로, 목표 재고를 자를 때와 같은 값이다.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tashu
from project_config import TARGET_QTY_UPPER_RATIO
from webapp import orders
from webapp.app import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


VRP = pd.DataFrame([
    # seq, cluster, vehicle, from, to, action, qty, 거리·시간
    (0, 0, "V01", "ST0001", "ST0010", "pick", 6, 3.0, 100.0),
    (1, 0, "V01", "ST0010", "ST0020", "drop", 6, 2.0, 300.0),
    (2, 0, "V01", "ST0020", "ST0001", "return", 0, 4.0, 500.0),
    (0, 1, "V02", "ST0001", "ST0030", "pick", 4, 5.0, 200.0),
    (1, 1, "V02", "ST0030", "ST0001", "return", 0, 5.0, 400.0),
], columns=["seq", "cluster", "vehicle_id", "from_id", "to_id", "action", "qty",
            "distance_km", "cum_sec"])

CANDIDATES = pd.DataFrame([
    ("ST0010", "가나 대여소", 10, 20, -6, 0),
    ("ST0020", "다라 대여소", 10, 1, 6, 0),
    ("ST0030", "마바 대여소", 8, 15, -4, 1),
], columns=["station_id", "station_name", "parking_lot", "stock", "rebal_qty", "cluster"])


@pytest.fixture
def planned(monkeypatch):
    """store.load를 가짜 산출물로 바꾼다 — DB·CSV 없이 조립만 검사한다."""
    def fake_load(table, run_label=None, duration=None):
        if table == "vrp_plan":
            return VRP.copy(), "db"
        if table == "pick_drop":
            return CANDIDATES.copy(), "db"
        return pd.DataFrame(), "none"

    monkeypatch.setattr(orders.store, "load", fake_load)


def test_order_sheet_follows_the_visit_order(planned):
    """지시서는 방문 순서대로 나오고, 차에 남는 대수를 이어서 센다."""
    sheets = orders.build("R", "_05_10")

    assert [s["vehicle_id"] for s in sheets] == ["V01", "V02"]
    first = sheets[0]
    assert [stop["action_label"] for stop in first["stops"]] == ["싣기", "내리기", "차고지 복귀"]
    assert [stop["load_after"] for stop in first["stops"]] == [6, 0, 0]
    assert first["stations"] == 2, "차고지 복귀는 들른 곳으로 세지 않는다"
    assert first["bikes"] == 6, "옮긴 대수는 pick 기준이다(pick+drop이면 2배가 된다)"
    assert first["minutes"] == pytest.approx(500 / 60, abs=0.1), "복귀까지가 소요시간이다"
    assert first["returns"] is True


def test_order_sheet_marks_plans_without_a_return(planned, monkeypatch):
    """복귀가 없는 구버전 산출물은 그렇다고 알려 준다(1.19.1 이전)."""
    old = VRP[VRP["action"] != "return"]

    def fake_load(table, run_label=None, duration=None):
        if table == "vrp_plan":
            return old.copy(), "db"
        if table == "pick_drop":
            return CANDIDATES.copy(), "db"
        return pd.DataFrame(), "none"

    monkeypatch.setattr(orders.store, "load", fake_load)
    assert all(not sheet["returns"] for sheet in orders.build("R", "_05_10"))


def test_comparison_uses_the_instructed_quantity_not_the_requested_one(planned):
    """대조는 계획 요구량이 아니라 **지시서에 적힌 수량**을 본다.

    ILP가 수급을 맞추느라 요구량보다 적게 배정할 수 있다. 둘이 다르면
    대조 화면과 기사가 든 종이가 어긋난다.
    """
    work = orders.planned_work("R", "_05_10")

    row = work[work["station_id"] == "ST0010"].iloc[0]
    assert row["need"] == 6 and row["action"] == "pick"
    assert row["parking_lot"] == 10, "거치대 수는 후보 목록에서 붙여 온다"
    assert "return" not in set(work["action"]), "복귀는 작업이 아니다"


def _live(**stock):
    return pd.DataFrame({"station_id": list(stock), "stock": list(stock.values())})


def test_pick_is_limited_by_what_is_actually_there(planned):
    """싣기는 지금 있는 만큼만 된다. 0대면 불가, 모자라면 부족."""
    work = orders.planned_work("R", "_05_10")

    empty = orders.compare_stock(work, _live(ST0010=0, ST0020=1, ST0030=15))
    row = empty[empty.station_id == "ST0010"].iloc[0]
    assert row["status"] == "불가" and row["possible"] == 0

    short = orders.compare_stock(work, _live(ST0010=2, ST0020=1, ST0030=15))
    row = short[short.station_id == "ST0010"].iloc[0]
    assert row["status"] == "부족" and row["possible"] == 2
    assert row["delta"] == 2 - 20, "계획 때 재고와의 차이를 보여준다"


def test_drop_is_limited_by_the_same_ceiling_the_plan_used(planned):
    """내리기 상한은 거치대 × TARGET_QTY_UPPER_RATIO — 목표 재고를 자른 기준과 같다.

    계획과 집행이 다른 기준을 쓰면 현장에서 어긋난다.
    """
    work = orders.planned_work("R", "_05_10")
    ceiling = int(10 * TARGET_QTY_UPPER_RATIO)      # ST0020은 거치대 10대

    over = orders.compare_stock(work, _live(ST0010=20, ST0020=ceiling, ST0030=15))
    row = over[over.station_id == "ST0020"].iloc[0]
    assert row["status"] == "불가", "상한에 닿으면 더 내려놓을 수 없다"

    tight = orders.compare_stock(work, _live(ST0010=20, ST0020=ceiling - 2, ST0030=15))
    row = tight[tight.station_id == "ST0020"].iloc[0]
    assert row["status"] == "넘침" and row["possible"] == 2

    fine = orders.compare_stock(work, _live(ST0010=20, ST0020=1, ST0030=15))
    assert (fine["status"] == "가능").all()


def test_station_missing_from_the_api_is_flagged_not_guessed(planned):
    """API에 없는 대여소는 '확인 불가'다 — 재고를 0으로 넘겨짚지 않는다."""
    work = orders.planned_work("R", "_05_10")
    compared = orders.compare_stock(work, _live(ST0010=20, ST0020=1))

    row = compared[compared.station_id == "ST0030"].iloc[0]
    assert row["status"] == "확인 불가"
    assert pd.isna(row["live_stock"]) and pd.isna(row["possible"])
    assert orders.summarize(compared)["unknown"] == 1


def test_orders_page_renders(client):
    assert client.get("/orders").status_code == 200


def test_live_check_calls_the_api_once_and_only_when_asked(client, monkeypatch):
    """재고 대조는 **누를 때만** 외부 API를 부른다.

    화면을 열 때마다 때리면 정작 출발 직전에 제한에 걸릴 수 있다.
    """
    calls = []

    def fake_fetch(timeout=30):
        calls.append(timeout)
        return _live(ST0010=5)

    monkeypatch.setattr(tashu, "fetch_stations", fake_fetch)

    assert client.get("/orders").status_code == 200
    assert calls == [], "지시서만 볼 때는 API를 부르지 않는다"

    assert client.get("/orders/live").status_code == 200
    assert len(calls) == 1


def test_api_failure_shows_a_message_instead_of_a_500(client, monkeypatch):
    """API가 죽어도 화면은 살아 있어야 한다 — 출발 직전에 500을 보면 곤란하다."""
    def boom(timeout=30):
        raise tashu.TashuError("TASHU_API_KEY가 .env에 없습니다.")

    monkeypatch.setattr(tashu, "fetch_stations", boom)

    res = client.get("/orders/live")
    assert res.status_code == 200
    assert "TASHU_API_KEY" in res.text
