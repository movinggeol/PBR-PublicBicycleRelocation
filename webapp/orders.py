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

from project_config import (
    DEPOT_ID, DEPOT_LAT, DEPOT_LON, DEPOT_NAME, TARGET_QTY_UPPER_RATIO,
)
from webapp import store


def _int(value, default: int = 0) -> int:
    """결측(None/NaN)은 기본값. `int(nan)`은 ValueError로 지시서 전체를 죽인다(1.26.271)."""
    try:
        return default if pd.isna(value) else int(value)
    except (TypeError, ValueError):
        return default


def _float(value, default: float = 0.0) -> float:
    """결측(None/NaN)은 기본값. 화면에 `nan`이 찍히지 않게 한다(1.26.271)."""
    try:
        return default if pd.isna(value) else float(value)
    except (TypeError, ValueError):
        return default

# 화면에 그대로 쓰는 말. pick/drop은 현장 용어가 아니다.
ACTION_LABELS = {"pick": "싣기", "drop": "내리기", "return": "차고지 복귀"}


def load_frames(run_label: Optional[str], duration: Optional[str]) -> dict:
    """지시서·대조가 쓰는 두 표를 **한 번만** 읽는다 (1.26.262).

    반환: `{"plan": vrp_plan, "candidates": pick_drop}`. 예전에는 함수마다
    따로 읽어 `/orders/live` 한 번에 `vrp_plan`을 3번, `pick_drop`을 5번
    읽었다(`build`·`build_live→build`·`planned_work`·이름·좌표). 라우트가
    여기서 한 번 읽어 아래 함수들에 `frames=`로 넘긴다 — 안 넘기면 각자
    읽으므로 예전 호출도 그대로 돈다.
    """
    plan, _ = store.load("vrp_plan", run_label=run_label, duration=duration)
    candidates, _ = store.load("pick_drop", run_label=run_label, duration=duration)
    return {"plan": plan, "candidates": candidates}


def _station_names(candidates: pd.DataFrame) -> dict:
    """station_id → 대여소 이름. 후보 목록(pick_drop)에서 가져온다."""
    if candidates.empty or "station_name" not in candidates:
        return {}
    return dict(zip(candidates["station_id"], candidates["station_name"]))


def _station_coords(candidates: pd.DataFrame) -> dict:
    """station_id → (위도, 경도). 기사가 지도 앱에 넣을 좌표다 (TODO 20).

    이름과 같은 표(`pick_drop`)에서 가져온다 — 좌표는 이미 거기 있고, 따로
    수집하거나 역지오코딩할 것이 없다.

    **차고지(depot)만 이 표에 없다.** 후보는 '작업이 필요한 대여소'라서
    차고지가 낄 이유가 없는데, 지시서에는 복귀 구간으로 등장한다. 이름을
    `DEPOT_NAME`으로 채우는 것과 같은 이유로 좌표도 상수에서 채운다.
    """
    coords = {DEPOT_ID: (DEPOT_LAT, DEPOT_LON)}
    if candidates.empty or not {"lat", "lon"} <= set(candidates.columns):
        return coords
    for station_id, lat, lon in zip(candidates["station_id"], candidates["lat"],
                                    candidates["lon"]):
        if pd.notna(lat) and pd.notna(lon):
            coords[station_id] = (float(lat), float(lon))
    return coords


def build(run_label: Optional[str] = None, duration: Optional[str] = None,
          frames: Optional[dict] = None) -> list:
    """차량별 작업지시서를 만든다.

    반환: [{vehicle_id, cluster, stations, bikes, distance_km, minutes, stops: [...]}]
    차량 배정이 없는 구버전 산출물에서는 클러스터 번호로 대신 묶는다.
    """
    frames = frames or load_frames(run_label, duration)
    plan = frames["plan"]
    if plan.empty:
        return []

    names = _station_names(frames["candidates"])
    names[DEPOT_ID] = DEPOT_NAME
    coords = _station_coords(frames["candidates"])

    # 방문 순서는 seq다. 없으면(구버전) 저장된 순서를 그대로 믿는다.
    if "seq" in plan:
        plan = plan.sort_values(["cluster", "seq"])
    group_key = "vehicle_id" if "vehicle_id" in plan else "cluster"

    orders = []
    for key, rows in plan.groupby(group_key, sort=True):
        stops, load = [], 0
        for _, row in rows.iterrows():
            action = row["action"]
            qty = _int(row.get("qty"))
            if action == "pick":
                load += qty
            elif action == "drop":
                load -= qty
            # 좌표는 기사가 지도 앱에 넣는 값이다. 없으면(구버전 산출물 등)
            # None으로 두고 화면이 단추를 만들지 않는다 — 빈 좌표를 복사하면
            # 엉뚱한 곳으로 안내된다.
            point = coords.get(row["to_id"])
            stops.append({
                "no": len(stops) + 1,
                "station_id": row["to_id"],
                "station_name": names.get(row["to_id"], row["to_id"]),
                "lat": point[0] if point else None,
                "lon": point[1] if point else None,
                "action": action,
                "action_label": ACTION_LABELS.get(action, action),
                "qty": qty,
                "load_after": load,
                "distance_km": round(_float(row.get("distance_km")), 2),
                "minutes": round(_float(row.get("cum_sec")) / 60, 1),
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
                 duration: Optional[str] = None,
                 frames: Optional[dict] = None) -> pd.DataFrame:
    """이번 계획이 **실제로 지시하는** 대여소별 작업량.

    `pick_drop.rebal_qty`(요구량)가 아니라 `vrp_plan.qty`(지시량)를 쓴다 —
    ILP가 수급을 맞추느라 요구량보다 적게 배정할 수 있고, 기사가 손에 드는
    지시서는 후자다. 둘이 다르면 대조 화면과 지시서가 어긋난다.

    같은 대여소가 싣기·내리기 양쪽에 나올 수 있으므로 행이 둘이 된다
    (VRP의 노드 키가 (대여소, 동작)인 것과 같은 이유).
    """
    frames = frames or load_frames(run_label, duration)
    plan = frames["plan"]
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
    candidates = frames["candidates"]
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
               "action_label", "possible", "status", "note", "advice"]
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
        elif parking_lot <= 0:
            # 거치대 수를 모르면 상한을 셀 수 없다 (1.26.262). 예전에는 그대로
            # 계산해 *"이미 N대로 상한(0대 x 1.5 = 0대)을 넘었습니다"* 라는
            # 판정이 나왔다 — 모르는 것을 '불가'로 못박은 것이다.
            possible = None
            status, note = "확인 불가", "거치대 수를 몰라 내려놓을 상한을 계산할 수 없습니다"
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

    _suggest_actions(rows)

    frame = pd.DataFrame(rows, columns=columns)
    # '확인 불가' 행의 None 하나 때문에 열 전체가 실수가 되면 화면에 2.0대로 찍힌다.
    for column in ("live_stock", "delta", "possible", "cluster", "seq"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int64")
    # planned의 순서를 그대로 물려받지만, 여기서 한 번 더 못박는다 —
    # 기사가 든 종이(군집 > 군집 내 순서)와 화면이 어긋나면 안 된다.
    frame = frame.sort_values(["cluster", "seq"], na_position="last")
    return frame.reset_index(drop=True)


def _suggest_actions(rows: list) -> None:
    """'불가'·'부족'·'넘침' 행에 **무엇을 하라**를 채운다 (수정안 39).

    지금까지는 가능/불가만 알려 주고 끝이라, 현장에서 막히면 판단이 사람 몫이었다.
    **계획을 다시 세우지는 않는다** — 그건 재실행이 할 일이고, 여기서는 기사가
    그 자리에서 고를 수 있는 선택지만 짚는다.

    같은 군집 안에서 찾는 이유: 차량 1대가 군집 1개를 맡으므로
    (docs/구현/FLEET.md), 군집을 벗어나면 그 회차에 갈 수 없는 곳이다.
    """
    by_cluster = {}
    for row in rows:
        by_cluster.setdefault(row.get("cluster"), []).append(row)

    for row in rows:
        status = row["status"]
        if status == "가능":
            row["advice"] = ""
            continue
        if status == "확인 불가":
            row["advice"] = "현장에서 직접 확인하세요"
            continue

        action = row["action"]
        need = int(row["need"] or 0)
        possible = int(row["possible"] or 0)
        short = need - possible          # 못 채우는 양
        # 같은 군집에서 **같은 동작**에 여유가 있는 곳. 여유는 `_spare()`가
        # 지금 재고로 잰다 — `possible`은 여유를 말하지 않는다(아래 참고).
        spare = [(r, _spare(r)) for r in by_cluster.get(row.get("cluster"), [])
                 if r is not row and r["action"] == action]
        spare = sorted([(r, s) for r, s in spare if s > 0],
                       key=lambda pair: pair[1], reverse=True)

        if action == "pick":
            # 실을 것이 모자란다 -> 같은 군집에서 **더 실을 수 있는 곳**
            if spare:
                best, extra = spare[0]
                row["advice"] = (f"{best['station_name']}에서 {min(short, extra)}대 더 실어 "
                                 f"메우세요 (여유 {extra}대)")
            else:
                row["advice"] = (f"{short}대가 빕니다. 같은 군집에 여유가 없으니 "
                                 f"내려놓을 곳에서 그만큼 덜 내리세요")
        else:
            # 내려놓을 자리가 없다 -> 같은 군집에서 **더 받을 수 있는 곳**
            if spare:
                best, extra = spare[0]
                row["advice"] = (f"{best['station_name']}에 {min(short, extra)}대 더 내려놓으세요 "
                                 f"(여유 {extra}대)")
            else:
                row["advice"] = (f"{short}대가 남습니다. 같은 군집에 자리가 없으니 "
                                 f"차고지로 가져가거나 실을 곳에서 그만큼 덜 실으세요")


def _spare(row: dict) -> int:
    """그 대여소가 지시량 **너머로** 더 받아 줄 수 있는 대수. 모르면 0.

    🔴 **`possible`로 여유를 재면 안 된다** (1.26.262). `compare_stock()`의
    `possible`은 `min(need, 지금 재고)`라 **지시량을 절대 넘지 않는다** — 그런데
    예전 코드는 `possible > need`인 이웃을 찾았다. 그 조건은 참이 될 수 없어
    *"○○에서 N대 더 실어 메우세요"* 라는 안내가 **한 번도 나온 적이 없고**,
    이웃에 12대가 있어도 늘 *"같은 군집에 여유가 없으니"* 로 떨어졌다. 시험은
    손으로 `possible: 12, need: 5`를 적어 넣어 통과시키고 있었다 — 실제 계산이
    만들 수 없는 상태로 검사한 것이다.

    여유는 지시량이 아니라 **지금 재고**에서 나온다.
      · 싣기:   지금 재고 − 지시량        (남는 자전거)
      · 내리기: 상한 − 지금 재고 − 지시량  (남는 자리, 상한은 계획과 같은 기준)
    재고를 못 읽은 곳(`확인 불가`)은 여유를 모르므로 0이다.
    """
    now = row.get("live_stock")
    if now is None or (isinstance(now, float) and pd.isna(now)):
        return 0
    need = int(row.get("need") or 0)
    if row["action"] == "pick":
        return max(0, int(now) - need)
    ceiling = int(row.get("parking_lot") or 0) * TARGET_QTY_UPPER_RATIO
    return max(0, int(ceiling - int(now)) - need)


_LIVE_FIELDS = ("planned_stock", "live_stock", "delta", "possible", "status", "note", "advice")


def build_live(run_label: Optional[str], duration: Optional[str],
               compared: pd.DataFrame, sheets: Optional[list] = None) -> list:
    """지금 재고와 대조한 결과만으로도 지시서 구실을 하게 만든다.

    `build()`가 만드는 지시서(순서·이동거리·누적시간·싣고 남는 수·차고지
    복귀)는 그대로 두고, 정거장마다 `compare_stock()`의 결과(계획 때·지금·
    차이·가능·판정·비고)를 얹는다. 대조 표를 따로 만들어 지시서 옆에 두면
    두 화면을 오가며 대조해야 한다 — 한 장에 다 있어야 기사가 그 한 장만
    들고 나갈 수 있다.

    `sheets`를 주면 그것에 얹는다(제자리에서 고친다) — 라우트가 이미 만든
    지시서를 다시 만들 이유가 없다. 안 주면 `build()`로 만든다.

    차고지 복귀 구간은 대여소가 아니므로 대조 대상이 아니다(값이 없다).
    """
    if sheets is None:
        sheets = build(run_label, duration)
    if not sheets:
        return sheets

    by_key = {(r["station_id"], r["action"]): r for r in store.records(compared)}

    for sheet in sheets:
        for stop in sheet["stops"]:
            match = by_key.get((stop["station_id"], stop["action"]))
            for field in _LIVE_FIELDS:
                stop[field] = match.get(field) if match else None
    return sheets


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
