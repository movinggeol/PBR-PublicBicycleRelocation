"""작업 문턱(`REBAL_MIN_QTY`)을 바꾸면 어떻게 되나 — **관행값 검증** (TODO P3).

`REBAL_MIN_QTY = 2`는 "|rebal_qty|가 2 이하면 손대지 않는다"는 문턱이다.
근거는 *"1~2대를 옮기러 차를 보내는 것은 이동 비용이 편익을 넘는다"* 인데,
**project_config가 스스로 밝히듯 실험으로 정한 값이 아니라 관행값**이다.

이 값은 파이프라인 전체에 파급된다.

    문턱 ↑ → 후보 대여소 ↓ → 이동시간 ↓ → 필요 차량 ↓ → 예산 초과 ↓
                          ↘ 손대지 않는 대여소 ↑ → 결품 ↑

즉 **효과와 비용의 맞바꿈**이고, 어느 쪽으로 기울었는지 재 본 적이 없다. 특히
1.21.6에서 예산 초과의 원인이 '멀리 흩어진 후보'로 밝혀졌으므로, **후보를 고르는
문턱**이 예산 문제의 상류에 있다.

묻는 것은 셋이다.

    ① 문턱을 올리면 후보와 작업량이 얼마나 주나
    ② 결품이 얼마나 나빠지나        ← 편익
    ③ 이동·소요시간이 얼마나 주나    ← 비용

**판정은 결품으로 한다.** 개선률은 `target_qty`가 분모라 문턱이 다르면 비교할 수
없다(EXPERIMENTS 4장에서 배운 것).

실행:
    python experiments/params/min_qty_sweep.py
    python experiments/params/min_qty_sweep.py --thresholds 1,2,3,4,5
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
for _folder in ("step0_collect", "step4_metrics"):
    sys.path.insert(0, str(ROOT / _folder))

import numpy as np
import pandas as pd

import db
from project_config import (
    PICK_TIME_SEC, DROP_TIME_SEC, TIME_BUDGET_MINUTES, TOP_STATION_LIMIT,
    TRAVEL_MIN_PER_STATION, VEHICLES_PER_ROUND, CLUSTER_IMBALANCE_ALLOWANCE,
    duration_hours,
)

WINDOWS = ("_05_10", "_10_15", "_15_20")


def load_plan(run_label: str, duration: str) -> pd.DataFrame:
    """그 실행의 대여소별 재배치량. 문턱 이전의 **원본**이 필요하다."""
    with db.session() as conn:
        return pd.read_sql(
            "SELECT station_id, stock, parking_lot, target_qty, rebal_qty"
            " FROM rebalance_plan WHERE run_label = ? AND duration = ?",
            conn, params=[run_label, duration])


def select_candidates(plan: pd.DataFrame, threshold: int) -> pd.DataFrame:
    """step1의 `select_top_unbalanced_st()`와 **같은 규칙**으로 후보를 고른다.

    문턱을 넘는 pick·drop을 각각 상위 N곳까지 보고, **적은 쪽까지만** 누적합으로
    자른다 — 옮길 수 있는 양은 min(총 pick, 총 drop)이기 때문이다.
    """
    pick = plan[plan["rebal_qty"] < -threshold].sort_values("rebal_qty")
    drop = plan[plan["rebal_qty"] > threshold].sort_values("rebal_qty", ascending=False)
    pick = pick.head(TOP_STATION_LIMIT)
    drop = drop.head(TOP_STATION_LIMIT)
    if pick.empty or drop.empty:
        return pd.DataFrame()

    pick_total = float(-pick["rebal_qty"].sum())
    drop = drop[drop["rebal_qty"].cumsum() <= pick_total]
    if drop.empty:
        return pd.DataFrame()
    return pd.concat([pick, drop], ignore_index=True)


def estimate(candidates: pd.DataFrame) -> dict:
    """후보만으로 추정할 수 있는 것 — 작업량·필요 차량(step1과 같은 식)."""
    bikes = float(candidates.loc[candidates["rebal_qty"] > 0, "rebal_qty"].sum())
    work_min = bikes * (PICK_TIME_SEC + DROP_TIME_SEC) / 60.0
    travel_min = len(candidates) * TRAVEL_MIN_PER_STATION
    needed = (work_min + travel_min) * CLUSTER_IMBALANCE_ALLOWANCE / TIME_BUDGET_MINUTES
    return {
        "후보": len(candidates),
        "옮길대수": int(bikes),
        "추정분": work_min + travel_min,
        "필요차량": min(max(1, int(np.ceil(needed))), VEHICLES_PER_ROUND),
    }


def stockout(plan: pd.DataFrame, candidates: pd.DataFrame, duration: str) -> float:
    """그 문턱으로 계획했을 때의 결품. **step4의 함수를 그대로 쓴다.**

    후보에 들지 못한 대여소는 손대지 않으므로 재고가 그대로다 — 그것이 문턱을
    올렸을 때의 대가다.
    """
    import imbalance as kpi_mod                     # noqa: E402  (step4)

    net = kpi_mod.load_net_demand()
    if net.empty:
        return float("nan")

    moved = candidates.set_index("station_id")["rebal_qty"] if not candidates.empty \
        else pd.Series(dtype=float)
    merged = net.merge(plan[["station_id", "stock", "parking_lot"]],
                       on="station_id", how="inner")
    if merged.empty:
        return float("nan")

    merged["moved"] = merged["station_id"].map(moved).fillna(0)
    hours = duration_hours(duration)
    after = kpi_mod._stockout_hours(merged, merged["stock"] + merged["moved"],
                                    merged["parking_lot"], hours)
    return float(after.mean())


def main() -> int:
    parser = argparse.ArgumentParser(description="작업 문턱 스윕")
    parser.add_argument("--thresholds", default="1,2,3,4,5")
    parser.add_argument("--run-label", help="실행 라벨 (기본: 최신)")
    args, _ = parser.parse_known_args()

    thresholds = [int(t) for t in args.thresholds.split(",") if t.strip()]
    with db.session() as conn:
        # **사전순 MAX를 쓰지 않는다** — 실험 라벨(`obs-cmp-…`)이 `'o' > '2'`라
        # 어떤 날짜 라벨도 이겨서, 계획 대신 실험을 말없이 집었다(1.26.127).
        label = args.run_label or db.latest_label(conn, "rebalance_plan",
                                                  kinds=("plan",))
    if not label:
        print("rebalance_plan이 비어 있습니다. 파이프라인을 돌리세요.")
        return 1

    print(f"실행 '{label}' · 현행 문턱 2")
    import imbalance as kpi_mod                     # noqa: E402  (step4)
    kpi_mod.use_run_day_type(label)                 # 오늘 달력이 아니라 그 실행의 요일로
    print()

    rows, 빈회차 = [], []
    for duration in WINDOWS:
        plan = load_plan(label, duration)
        if plan.empty:
            # **말없이 건너뛰지 않는다.** 한 실행이 세 회차를 다 갖고 있는 일은
            # 드물어서, 조용히 넘기면 회차 하나짜리 표를 보고 판정하게 된다 —
            # *"한 회차만 보고 판단하지 말 것"* 이 이 저장소의 규약이다.
            빈회차.append(duration)
            continue
        for threshold in thresholds:
            candidates = select_candidates(plan, threshold)
            if candidates.empty:
                continue
            summary = estimate(candidates)
            rows.append({"시간대": duration, "문턱": threshold, **summary,
                         "결품h": stockout(plan, candidates, duration)})

    if not rows:
        # **없다고만 말하면 다음 수를 둘 수 없다.** 어느 회차가 비었고 어느
        # 실행에 그 회차가 있는지 함께 보여 준다(1.26.39에서 배운 것).
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
        else:
            print("어느 실행에도 없습니다 — 그 회차로 파이프라인을 먼저 돌리십시오.")
        return 1

    frame = pd.DataFrame(rows)
    for duration, group in frame.groupby("시간대"):
        print(f"=== {duration} ===")
        print(f"{'문턱':>4} {'후보':>5} {'옮길대수':>8} {'추정분':>8} "
              f"{'필요차량':>8} {'결품h':>8}")
        for _, r in group.iterrows():
            mark = "  ← 현행" if r["문턱"] == 2 else ""
            print(f"{r['문턱']:4d} {r['후보']:5d} {r['옮길대수']:8d} "
                  f"{r['추정분']:8.0f} {r['필요차량']:8d} {r['결품h']:8.3f}{mark}")
        print()

    diagnose(label)

    if 빈회차:
        print(f"⚠️  이 실행에 자료가 없는 회차: {', '.join(빈회차)}")
        print(f"    위 표는 {len(WINDOWS) - len(빈회차)}/{len(WINDOWS)} 회차만 봤습니다 —"
              " **한 회차만 보고 판단하지 마십시오.**")
        print("    다른 회차를 보려면 그 회차를 돌린 실행을 --run-label로 주십시오.\n")

    print("판정")
    print("  문턱을 올려 결품이 거의 안 나빠지면  → 올릴 만하다(차량·시간이 준다).")
    print("  결품이 뚜렷이 나빠지면              → 지금이 맞다.")
    print("  **결품으로만 판정한다** — 개선률은 target_qty가 분모라 문턱이 다르면")
    print("  비교 자체가 성립하지 않는다.")
    return 0


def diagnose(label: str) -> None:
    """문턱이 **실제로 후보를 고르고 있는지** 본다.

    `TOP_STATION_LIMIT`(각 50곳)이 먼저 자르면 문턱을 바꿔도 후보가 그대로다.
    그러면 이 스윕의 결과가 전부 같게 나오는데, 그것은 '문턱이 무관하다'가 아니라
    **'문턱이 작동하지 않는다'** 는 뜻이다. 둘을 구분해야 한다.
    """
    with db.session() as conn:
        plan = pd.read_sql(
            "SELECT duration, rebal_qty FROM rebalance_plan WHERE run_label = ?",
            conn, params=[label])
    if plan.empty:
        return

    print("문턱이 실제로 후보를 고르고 있나 (자격자 수 vs 상한"
          f" {TOP_STATION_LIMIT}곳)")
    print(f"{'시간대':8} {'문턱':>4} {'pick자격':>8} {'drop자격':>8}  판정")
    for duration, group in plan.groupby("duration"):
        if duration not in WINDOWS:
            continue
        for threshold in (2, 5):
            picks = int((group["rebal_qty"] < -threshold).sum())
            drops = int((group["rebal_qty"] > threshold).sum())
            capped = picks > TOP_STATION_LIMIT and drops > TOP_STATION_LIMIT
            verdict = "상한이 먼저 자른다 → 문턱 무력" if capped else "문턱이 작동"
            print(f"{duration:8} {threshold:4d} {picks:8d} {drops:8d}  {verdict}")
    print()


if __name__ == "__main__":
    raise SystemExit(main())
