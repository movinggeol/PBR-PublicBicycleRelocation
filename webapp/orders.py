"""작업지시서와 실시간 재고 대조.

계획을 세우는 것과 **현장에 내보내는 것**은 다른 일이다. 지도는 경로를 보여주지만
기사가 손에 들 것은 "몇 번째로 어디에 들러 몇 대를 싣고 내리는가"의 목록이다.

또 계획을 세운 시점과 차가 출발하는 시점 사이에 재고가 바뀐다. 그래서 계획 대상
대여소만 타슈 API로 다시 조회해 **집행 가능한지**를 사람이 판단할 수 있게 한다.
판정은 표시만 하고 계획을 자동으로 바꾸지 않는다 — 현장 상황을 아는 것은 사람이다.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from project_config import DEPOT_ID, DEPOT_NAME, TARGET_QTY_UPPER_RATIO
from webapp import store

# 화면에 그대로 쓰는 말. pick/drop은 현장 용어가 아니다.
ACTION_LABELS = {"pick": "싣기", "drop": "내리기", "return": "차고지 복귀"}


def _station_names(run_label: Optional[str], duration: Optional[str]) -> dict:
    """station_id → 대여소 이름. 후보 목록(pick_drop)에서 가져온다."""
    frame, _ = store.load("pick_drop", run_label=run_label, duration=duration)
    if frame.empty or "station_name" not in frame:
        return {}
    return dict(zip(frame["station_id"], frame["station_name"]))


def build(run_label: Optional[str] = None, duration: Optional[str] = None) -> list:
    """차량별 작업지시서를 만든다.

    반환: [{vehicle_id, cluster, stations, bikes, distance_km, minutes, stops: [...]}]
    차량 배정이 없는 구버전 산출물에서는 클러스터 번호로 대신 묶는다.
    """
    plan, _ = store.load("vrp_plan", run_label=run_label, duration=duration)
    if plan.empty:
        return []

    names = _station_names(run_label, duration)
    names[DEPOT_ID] = DEPOT_NAME

    # 방문 순서는 seq다. 없으면(구버전) 저장된 순서를 그대로 믿는다.
    if "seq" in plan:
        plan = plan.sort_values(["cluster", "seq"])
    group_key = "vehicle_id" if "vehicle_id" in plan else "cluster"

    orders = []
    for key, rows in plan.groupby(group_key, sort=True):
        stops, load = [], 0
        for _, row in rows.iterrows():
            action = row["action"]
            qty = int(row.get("qty", 0) or 0)
            if action == "pick":
                load += qty
            elif action == "drop":
                load -= qty
            stops.append({
                "no": len(stops) + 1,
                "station_id": row["to_id"],
                "station_name": names.get(row["to_id"], row["to_id"]),
                "action": action,
                "action_label": ACTION_LABELS.get(action, action),
                "qty": qty,
                "load_after": load,
                "distance_km": round(float(row.get("distance_km", 0) or 0), 2),
                "minutes": round(float(row.get("cum_sec", 0) or 0) / 60, 1),
            })

        work = rows[rows["action"] != "return"]
        picked = rows[rows["action"] == "pick"]["qty"].sum() if "qty" in rows else 0
        orders.append({
            "vehicle_id": key if group_key == "vehicle_id" else "",
            "cluster": int(rows["cluster"].iloc[0]),
            "stations": int(work["to_id"].nunique()),
            "bikes": int(picked),
            "distance_km": round(float(rows["distance_km"].sum()), 2),
            "minutes": round(float(rows["cum_sec"].max()) / 60, 1),
            "returns": bool((rows["action"] == "return").any()),
            "stops": stops,
        })
    return orders


def planned_work(run_label: Optional[str] = None,
                 duration: Optional[str] = None) -> pd.DataFrame:
    """이번 계획이 **실제로 지시하는** 대여소별 작업량.

    `pick_drop.rebal_qty`(요구량)가 아니라 `vrp_plan.qty`(지시량)를 쓴다 —
    ILP가 수급을 맞추느라 요구량보다 적게 배정할 수 있고, 기사가 손에 드는
    지시서는 후자다. 둘이 다르면 대조 화면과 지시서가 어긋난다.

    같은 대여소가 싣기·내리기 양쪽에 나올 수 있으므로 행이 둘이 된다
    (VRP의 노드 키가 (대여소, 동작)인 것과 같은 이유).
    """
    plan, _ = store.load("vrp_plan", run_label=run_label, duration=duration)
    if plan.empty:
        return pd.DataFrame()

    work = plan[plan["action"] != "return"]
    if work.empty:
        return pd.DataFrame()

    # 방문 순서는 seq다. 없으면(구버전) 저장된 순서를 그대로 믿는다 —
    # build_orders()와 같은 규칙이어야 지시서와 대조표의 줄 순서가 맞는다.
    aggs = {"need": ("qty", "sum"), "cluster": ("cluster", "first")}
    if "seq" in work:
        aggs["seq"] = ("seq", "min")
    grouped = work.groupby(["to_id", "action"], as_index=False).agg(**aggs)
    grouped = grouped.rename(columns={"to_id": "station_id"})

    # 기사는 군집 한 장을 들고 그 안을 순서대로 돈다. groupby가 흩뜨린 줄을
    # (군집 > 군집 내 방문 순서)로 되돌린다. 이 순서가 화면·인쇄에 그대로 간다.
    sort_keys = ["cluster", "seq"] if "seq" in grouped else ["cluster", "station_id"]
    grouped = grouped.sort_values(sort_keys).reset_index(drop=True)

    # 대여소 이름·거치대·계획 시점 재고는 후보 목록에 있다.
    candidates, _ = store.load("pick_drop", run_label=run_label, duration=duration)
    if not candidates.empty:
        keep = [c for c in ("station_id", "station_name", "parking_lot", "stock")
                if c in candidates]
        grouped = grouped.merge(candidates[keep], on="station_id", how="left")

    for column, default in (("station_name", ""), ("parking_lot", 0), ("stock", 0)):
        if column not in grouped:
            grouped[column] = default
    grouped["station_name"] = grouped["station_name"].fillna("")
    grouped[["parking_lot", "stock"]] = (
        grouped[["parking_lot", "stock"]].fillna(0).astype(int))
    return grouped


def compare_stock(planned: pd.DataFrame, live: pd.DataFrame) -> pd.DataFrame:
    """계획이 본 재고와 **지금 재고**를 나란히 놓고 집행 가능 여부를 판정한다.

    **저장하지 않는 순수 계산이다** — 테스트가 규칙만 따로 검사한다.

    판정 규칙(계획을 세울 때 쓴 기준과 같은 값을 쓴다):

    - **싣기**: 지금 재고만큼만 실을 수 있다.
      재고가 0이면 **불가**, 지시량보다 적으면 **부족**.
    - **내리기**: 거치대 x TARGET_QTY_UPPER_RATIO를 넘기면 **넘침**.
      계획이 목표 재고를 그 선에서 잘랐으므로 집행 기준도 같아야 한다.
    - 그 밖에는 **가능**. 재고가 계획과 달라졌어도 작업 자체는 성립한다.

    작업량이 `REBAL_MIN_QTY` 이하인 대여소는 애초에 계획에 들어오지 않는다
    (step1의 작업 대상 선정). 여기서 다시 거르지 않는다.
    """
    columns = ["station_id", "station_name", "cluster", "seq", "parking_lot",
               "planned_stock", "live_stock", "delta", "need", "action",
               "action_label", "possible", "status", "note"]
    if planned.empty:
        return pd.DataFrame(columns=columns)

    live_stock = (dict(zip(live["station_id"], live["stock"]))
                  if not live.empty else {})

    rows = []
    for _, st in planned.iterrows():
        action = st["action"]
        need = int(st["need"])
        planned_stock = int(st.get("stock", 0) or 0)
        parking_lot = int(st.get("parking_lot", 0) or 0)
        ceiling = parking_lot * TARGET_QTY_UPPER_RATIO

        now = live_stock.get(st["station_id"])
        if now is None:
            rows.append({
                "station_id": st["station_id"],
                "station_name": st.get("station_name", ""),
                "cluster": st.get("cluster"),
                "seq": st.get("seq"),
                "parking_lot": parking_lot,
                "planned_stock": planned_stock,
                "live_stock": None, "delta": None,
                "need": need, "action": action,
                "action_label": ACTION_LABELS[action],
                "possible": None, "status": "확인 불가",
                "note": "타슈 API에 이 대여소가 없습니다",
            })
            continue

        now = int(now)
        if action == "pick":
            possible = min(need, now)
            if now == 0:
                status, note = "불가", "재고가 0대라 실을 것이 없습니다"
            elif possible < need:
                status, note = "부족", f"{need}대 중 {possible}대만 실을 수 있습니다"
            else:
                status, note = "가능", ""
        else:
            room = int(ceiling - now)
            possible = max(0, min(need, room))
            if room <= 0:
                status = "불가"
                note = (f"이미 {now}대로 상한({parking_lot}대 x "
                        f"{TARGET_QTY_UPPER_RATIO:g} = {ceiling:.0f}대)을 넘었습니다")
            elif possible < need:
                status, note = "넘침", f"{need}대 중 {possible}대까지만 내려놓을 수 있습니다"
            else:
                status, note = "가능", ""

        rows.append({
            "station_id": st["station_id"],
            "station_name": st.get("station_name", ""),
            "cluster": st.get("cluster"),
            "seq": st.get("seq"),
            "parking_lot": parking_lot,
            "planned_stock": planned_stock,
            "live_stock": now,
            "delta": now - planned_stock,
            "need": need,
            "action": action,
            "action_label": ACTION_LABELS[action],
            "possible": possible,
            "status": status,
            "note": note,
        })

    frame = pd.DataFrame(rows, columns=columns)
    # '확인 불가' 행의 None 하나 때문에 열 전체가 실수가 되면 화면에 2.0대로 찍힌다.
    for column in ("live_stock", "delta", "possible", "cluster", "seq"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int64")
    # planned의 순서를 그대로 물려받지만, 여기서 한 번 더 못박는다 —
    # 기사가 든 종이(군집 > 군집 내 순서)와 화면이 어긋나면 안 된다.
    frame = frame.sort_values(["cluster", "seq"], na_position="last")
    return frame.reset_index(drop=True)


def summarize(compared: pd.DataFrame) -> dict:
    """대조 결과 한 줄 요약. 화면 맨 위에 쓴다."""
    if compared.empty:
        return {"total": 0, "ok": 0, "warn": 0, "blocked": 0, "unknown": 0}
    counts = compared["status"].value_counts().to_dict()
    return {
        "total": len(compared),
        "ok": counts.get("가능", 0),
        "warn": counts.get("부족", 0) + counts.get("넘침", 0),
        "blocked": counts.get("불가", 0),
        "unknown": counts.get("확인 불가", 0),
    }
