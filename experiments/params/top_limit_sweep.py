"""후보 상한(`TOP_STATION_LIMIT`)을 바꾸면 어떻게 되나 — **근거 없는 관행값** (TODO P2).

🔴 **이 스크립트의 결품 값은 '계획 기준'이다 — 상한을 고르는 데 쓰지 마라**
(2026-08-31). 아래 `stockout()`이 `moved = rebal_qty`, 즉 **계획이 전부
집행된다고 가정**한다. 군집·ILP·VRP를 돌리지 않으므로 시간 예산도 차량 수도
반발하지 않아, **상한을 올릴수록 무조건 좋아 보인다.** 1.18.4에서 step4를
집행 기준으로 고친 것과 같은 종류의 결함이 여기 남아 있었다.

집행 기준으로 다시 재면 **결론이 뒤집힌다** — 상한 40이 50보다 결품·이동거리·
예산 초과 모두 낫다(EXPERIMENTS.md 12장). **상한을 고를 때는
`experiments/params/limit_fleet_grid.py`를 써라.** 이 스크립트가 여전히 쓸모
있는 것은 **후보 수·필요 차량 추정**(집행과 무관한 계획 규모)이다.

1.21.7에서 드러난 것: **계획 규모를 정하는 것은 작업 문턱이 아니라 이 상한이다.**
문턱 2의 자격자가 pick 76~89곳·drop 208~298곳인데 각 50곳으로 잘리므로, 문턱을
바꿔도 후보가 그대로다. 그런데 **이 50이라는 값에는 근거가 없다.**

그리고 이 값은 예산 초과의 상류에 있다.

    상한 ↓ → 후보 ↓ → 작업량 ↓ → 이동시간 ↓ → 예산 초과 ↓
                    ↘ 손대지 않는 대여소 ↑ → 결품 ↑
    상한 ↑ → 후보가 도시 전역으로 퍼진다 → 군집이 흩어진다 → 예산 초과 ↑

**양쪽 다 나빠질 수 있는 값**이라 최적점이 중간에 있다. 재 본 적이 없다.

묻는 것은 셋이다.

    ① 후보·작업량이 얼마나 바뀌나
    ② 결품이 어떻게 되나          ← 편익 (판정 기준)
    ③ 이동·소요·예산 초과는       ← 비용

**판정은 결품으로 한다.** 개선률은 `target_qty`가 분모라 후보가 다르면 비교할 수 없다.

실행:
    python experiments/params/top_limit_sweep.py
    python experiments/params/top_limit_sweep.py --limits 20,30,50,70,100
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
for _folder in ("step2_optimize", "step4_metrics"):
    sys.path.insert(0, str(ROOT / _folder))

import numpy as np
import pandas as pd

import db
from project_config import (
    CLUSTER_IMBALANCE_ALLOWANCE, DROP_TIME_SEC, PICK_TIME_SEC, REBAL_MIN_QTY,
    TIME_BUDGET_MINUTES, TRAVEL_MIN_PER_STATION, VEHICLES_PER_ROUND,
    duration_hours,
)

WINDOWS = ("_05_10", "_10_15", "_15_20")


def load_plan(run_label: str, duration: str) -> pd.DataFrame:
    with db.session() as conn:
        return pd.read_sql(
            "SELECT station_id, lat, lon, stock, parking_lot, rebal_qty"
            " FROM pick_drop WHERE run_label = ? AND duration = ?",
            conn, params=[run_label, duration])


def load_all(run_label: str, duration: str) -> pd.DataFrame:
    """상한 **이전**의 전체 대여소. 상한을 넓히려면 잘리기 전 자료가 필요하다."""
    with db.session() as conn:
        plan = pd.read_sql(
            "SELECT station_id, stock, parking_lot, rebal_qty FROM rebalance_plan"
            " WHERE run_label = ? AND duration = ?", conn, params=[run_label, duration])
        info = pd.read_sql(
            "SELECT station_id, lat, lon FROM station_info"
            " WHERE run_label = (SELECT MAX(run_label) FROM station_info)", conn)
    return plan.merge(info.drop_duplicates("station_id"), on="station_id", how="left")


def select(plan: pd.DataFrame, limit: int) -> pd.DataFrame:
    """step1의 `select_top_unbalanced_st()`와 **같은 규칙**.

    문턱 → 작업량 내림차순 상위 `limit`곳 → **양쪽 모두** cut_point까지 누적 절단.
    마지막 절단이 핵심이다 — 옮길 수 있는 양은 min(총 pick, 총 drop)이므로
    많은 쪽을 그대로 두면 갈 곳 없는 작업이 계획에 남는다.
    """
    st = plan[plan["rebal_qty"].abs() > REBAL_MIN_QTY]
    pick = st[st["rebal_qty"] < 0].sort_values("rebal_qty").head(limit)
    drop = st[st["rebal_qty"] > 0].sort_values("rebal_qty", ascending=False).head(limit)
    if pick.empty or drop.empty:
        return pd.DataFrame()

    cut = min(abs(pick["rebal_qty"].sum()), drop["rebal_qty"].sum())
    pick = pick[pick["rebal_qty"].cumsum().abs() <= cut]
    drop = drop[drop["rebal_qty"].cumsum() <= cut]
    return pd.concat([pick, drop], ignore_index=True)


def haversine_km(lat1, lon1, lat2, lon2):
    radius = 6371.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = (np.sin((p2 - p1) / 2) ** 2
         + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2)
    return 2 * radius * np.arcsin(np.sqrt(a))


def spread_km(points: pd.DataFrame) -> float:
    """후보가 얼마나 퍼져 있나 — depot에서의 평균 거리.

    1.21.7에서 '최근접 이웃 거리'는 대여소 수의 대리 변수라 쓸모없었다. 여기서는
    **상한을 넓히면 후보가 외곽으로 번지는지**를 보려는 것이므로 depot 기준이 맞다.
    """
    from project_config import DEPOT_LAT, DEPOT_LON

    coords = points.dropna(subset=["lat", "lon"])
    if coords.empty:
        return float("nan")
    return float(haversine_km(DEPOT_LAT, DEPOT_LON,
                              coords["lat"].to_numpy(), coords["lon"].to_numpy()).mean())


def estimate(candidates: pd.DataFrame) -> dict:
    bikes = float(candidates.loc[candidates["rebal_qty"] > 0, "rebal_qty"].sum())
    work_min = bikes * (PICK_TIME_SEC + DROP_TIME_SEC) / 60.0
    travel_min = len(candidates) * TRAVEL_MIN_PER_STATION
    needed = (work_min + travel_min) * CLUSTER_IMBALANCE_ALLOWANCE / TIME_BUDGET_MINUTES
    return {
        "후보": len(candidates),
        "옮길대수": int(bikes),
        "필요차량": min(max(1, int(np.ceil(needed))), VEHICLES_PER_ROUND),
        "depot거리": spread_km(candidates),
    }


def stockout(plan: pd.DataFrame, candidates: pd.DataFrame, duration: str) -> float:
    """step4의 함수를 그대로 쓴다. 후보에 못 든 대여소는 재고가 그대로다."""
    import imbalance as kpi_mod                     # noqa: E402  (step4)

    net = kpi_mod.load_net_demand()
    if net.empty:
        return float("nan")
    moved = (candidates.set_index("station_id")["rebal_qty"]
             if not candidates.empty else pd.Series(dtype=float))
    merged = net.merge(plan[["station_id", "stock", "parking_lot"]],
                       on="station_id", how="inner")
    if merged.empty:
        return float("nan")
    merged["moved"] = merged["station_id"].map(moved).fillna(0)
    after = kpi_mod._stockout_hours(merged, merged["stock"] + merged["moved"],
                                    merged["parking_lot"], duration_hours(duration))
    return float(after.mean())


def main() -> int:
    parser = argparse.ArgumentParser(description="후보 상한 스윕")
    parser.add_argument("--limits", default="20,30,50,70,100")
    parser.add_argument("--run-label", help="실행 라벨 (기본: 최신)")
    args, _ = parser.parse_known_args()

    limits = [int(v) for v in args.limits.split(",") if v.strip()]
    with db.session() as conn:
        # 사전순 MAX를 쓰지 않는다 — 실험 라벨이 날짜 라벨을 이긴다(1.26.127).
        label = args.run_label or db.latest_label(conn, "rebalance_plan",
                                                  kinds=("plan",))
    if not label:
        print("rebalance_plan이 비어 있습니다.")
        return 1

    print(f"실행 '{label}' · 현행 상한 50 · 문턱 {REBAL_MIN_QTY}")
    import imbalance as kpi_mod                     # noqa: E402  (step4)
    kpi_mod.use_run_day_type(label)                 # 오늘 달력이 아니라 그 실행의 요일로
    print()

    rows, 빈회차 = [], []
    for duration in WINDOWS:
        plan = load_all(label, duration)
        if plan.empty:
            빈회차.append(duration)      # 말없이 건너뛰지 않는다
            continue
        for limit in limits:
            candidates = select(plan, limit)
            if candidates.empty:
                continue
            rows.append({"시간대": duration, "상한": limit, **estimate(candidates),
                         "결품h": stockout(plan, candidates, duration)})

    if not rows:
        print(f"이 실행에는 {', '.join(WINDOWS)} 자료가 없습니다.")
        with db.session() as conn:
            pairs = conn.execute(
                "SELECT duration, run_label FROM rebalance_plan"
                f" WHERE duration IN ({','.join('?' * len(WINDOWS))})"
                " GROUP BY duration, run_label ORDER BY duration", WINDOWS).fetchall()
        if pairs:
            print("\n이 회차를 가진 실행:")
            for duration, run_label in pairs:
                print(f"  {duration}  --run-label \"{run_label}\"")
        return 1

    frame = pd.DataFrame(rows)
    for duration, group in frame.groupby("시간대"):
        print(f"=== {duration} ===")
        print(f"{'상한':>4} {'후보':>5} {'옮길대수':>8} {'필요차량':>8} "
              f"{'depot거리km':>11} {'결품h':>8}")
        best = group["결품h"].min()
        for _, r in group.iterrows():
            mark = "  ← 현행" if r["상한"] == 50 else ""
            if abs(r["결품h"] - best) < 1e-9:
                mark += "  ★최저"
            print(f"{r['상한']:4d} {r['후보']:5d} {r['옮길대수']:8d} "
                  f"{r['필요차량']:8d} {r['depot거리']:11.1f} {r['결품h']:8.3f}{mark}")
        print()

    capacity_check(frame)

    if 빈회차:
        print(f"⚠️  이 실행에 자료가 없는 회차: {', '.join(빈회차)}")
        print(f"    위 표는 {len(WINDOWS) - len(빈회차)}/{len(WINDOWS)} 회차만 봤습니다 —"
              " **한 회차만 보고 판단하지 마십시오.**\n")

    print("판정")
    print("  결품이 가장 낮은 상한이 있으면  → 그쪽으로 옮길 근거가 된다.")
    print("  **단, 필요 차량이 보유량에 잘리면 그 결품은 '차가 더 있다면'의 값이다.**")
    print("  **한 실행으로 판단하지 않는다** — z·γ처럼 여러 달로 재확인할 것.")
    return 0


def capacity_check(frame: pd.DataFrame) -> None:
    """결품 개선이 **집행 가능한 것인지** 본다.

    상한을 넓히면 후보가 늘고 결품 추정치는 좋아진다. 그런데 필요 차량이 보유량을
    넘으면 `min(..., VEHICLES_PER_ROUND)`에서 잘려 **군집당 일감이 늘어난다** —
    1.21.6에서 예산 초과를 만들던 조건이다. 그 경우 개선은 "차가 더 있다면"이라는
    가정 위에 있으므로, 파라미터로 얻은 것이 아니다.
    """
    print(f"집행 가능한가 (보유 회차당 상한 {VEHICLES_PER_ROUND}대)")
    print(f"{'시간대':8} {'상한':>4} {'필요차량':>8} {'차량당 대여소':>12}  판정")
    for _, row in frame.iterrows():
        vehicles = int(row["필요차량"])
        per_vehicle = row["후보"] / vehicles if vehicles else float("nan")
        capped = vehicles >= VEHICLES_PER_ROUND
        verdict = "보유량에 잘림 → 예산 초과 위험" if capped else "집행 가능"
        print(f"{row['시간대']:8} {int(row['상한']):4d} {vehicles:8d} "
              f"{per_vehicle:12.1f}  {verdict}")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
