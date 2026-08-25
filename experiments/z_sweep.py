"""실험 A — `z`를 얼마로 둘 것인가 (docs/분석/EXPERIMENTS.md 1장).

`target_qty = mu + z·sigma`의 `z`는 "정규분포에서 95%를 덮는 값"이라는 이유로
1.65가 쓰여 왔다. 백테스트(1.11.0)에서 실제 커버리지가 91.7~92.8%로 나와,
이 가정이 맞지 않는다는 것이 드러났다.

이 스크립트는 두 가지를 함께 잰다. 하나만 보면 결론을 낼 수 없기 때문이다.

  1. 편익 — z를 올리면 커버리지가 얼마나 오르는가 (11개 월쌍 백테스트)
  2. 비용 — z를 올리면 작업량이 얼마나 늘어나는가
           (실제 st_info로 rebal_qty를 다시 계산)

실행:
    python experiments/z_sweep.py
    python experiments/z_sweep.py --duration _05_10 --period "25년 11월"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import numpy as np
import pandas as pd

import db
from backtest_demand import DURATIONS, consecutive_pairs, daily_window_demand

Z_GRID = [1.28, 1.65, 1.80, 1.99, 2.10, 2.33, 2.58]
MAX_CAPACITY = 10


# ---------- 1. 편익: z별 커버리지 ----------

def coverage_by_z(net: dict, pairs: list, duration: str) -> pd.DataFrame:
    """월쌍마다 학습 달의 mu/sigma로 검증 달을 덮는 비율을 z별로 잰다."""
    rows = []
    for train_period, test_period in pairs:
        train = daily_window_demand(net[train_period], duration)
        test = daily_window_demand(net[test_period], duration)

        stats = train.groupby("station_id")["demand"].agg(mu="mean", sigma="std").fillna(0)
        merged = test.merge(stats, on="station_id", how="inner")
        if merged.empty:
            continue

        row = {"학습": train_period, "검증": test_period}
        for z in Z_GRID:
            row[z] = float((merged["demand"] <= merged["mu"] + z * merged["sigma"]).mean())
        rows.append(row)
    return pd.DataFrame(rows)


# ---------- 2. 비용: z별 작업량 ----------

def rebal_qty_by_z(stats: pd.DataFrame, z: float) -> pd.DataFrame:
    """calculate_target_qty.calculate_rebal_qty와 같은 계산 (z만 바꿔가며)."""
    out = stats.copy()
    positive = out["mu"] >= 0

    out.loc[positive, "target_qty"] = out.loc[positive, "mu"] + z * out.loc[positive, "sigma"]
    out.loc[~positive, "target_qty"] = out.loc[~positive, "stock"] + out.loc[~positive, "mu"]
    out["target_qty"] = out["target_qty"].clip(lower=0, upper=out["parking_lot"] * 1.5)

    raw = out["target_qty"] - out["stock"]
    eased = MAX_CAPACITY * np.tanh(raw / MAX_CAPACITY)
    out["rebal_qty"] = np.where(eased >= 0, np.floor(eased), np.ceil(eased)).astype(int)
    return out


def workload(frame: pd.DataFrame) -> dict:
    """step1이 실제로 후보로 삼는 기준(|rebal_qty| > 2)으로 작업량을 센다."""
    drop = frame[frame["rebal_qty"] > 2]
    pick = frame[frame["rebal_qty"] < -2]
    return {
        "drop_대여소": len(drop),
        "drop_필요": int(drop["rebal_qty"].sum()),
        "pick_대여소": len(pick),
        "pick_가능": int(-pick["rebal_qty"].sum()),
        # 실제 처리량은 둘 중 작은 쪽으로 묶인다 (회수한 만큼만 배분 가능)
        "처리_상한": int(min(drop["rebal_qty"].sum(), -pick["rebal_qty"].sum())),
        "목표재고_합": float(frame["target_qty"].sum()),
    }


def load_station_stats(conn, period: str, duration: str, run_label: str) -> pd.DataFrame:
    """rebalance_plan과 동일한 입력(mu/sigma + parking_lot/stock)을 재구성한다."""
    from backtest_demand import window_hours

    net = db.load_frame(conn, "net_demand", period=period)
    columns = [f"net_{h:02d}" for h in window_hours(duration) if f"net_{h:02d}" in net.columns]
    net["window"] = net[columns].sum(axis=1)

    stats = net.groupby("station_id")["window"].agg(mu="mean", sigma="std").fillna(0).reset_index()

    info = db.load_frame(conn, "station_info", run_label=run_label)
    stats = stats.merge(info[["station_id", "parking_lot", "stock"]], on="station_id", how="left")
    return stats[~stats["stock"].isna()]


def main() -> int:
    parser = argparse.ArgumentParser(description="z 스윕 — 커버리지와 작업량의 맞바꿈")
    parser.add_argument("--duration", help="시간대 하나만")
    parser.add_argument("--period", default="25년 11월", help="비용 계산에 쓸 기간")
    parser.add_argument("--run-label", default="2026-08-11 real", help="st_info 실행 라벨")
    args, _ = parser.parse_known_args()

    durations = [args.duration] if args.duration else DURATIONS

    with db.session() as conn:
        periods = [r[0] for r in conn.execute("SELECT DISTINCT period FROM net_demand").fetchall()]
        net = {p: db.load_frame(conn, "net_demand", period=p) for p in periods}
        cost_input = {d: load_station_stats(conn, args.period, d, args.run_label) for d in durations}

    pairs = consecutive_pairs(periods)
    print(f"기간 {len(periods)}개 · 연속 월쌍 {len(pairs)}개\n")

    benefit_rows, cost_rows = [], []

    for duration in durations:
        frame = coverage_by_z(net, pairs, duration)
        if frame.empty:
            continue

        print(f"=== {duration} · 커버리지(%) ===")
        display = (frame.set_index(["학습", "검증"])[Z_GRID] * 100).round(1)
        print(display.to_string())
        means = display.mean()
        print("  평균:", "  ".join(f"z={z}:{means[z]:.1f}%" for z in Z_GRID))
        # 95%를 처음 넘기는 z
        reached = [z for z in Z_GRID if means[z] >= 95.0]
        print(f"  → 평균 95% 달성 최소 z: {reached[0] if reached else '격자 내 없음'}")
        # 최악의 달에도 90%를 넘기는 z
        worst = display.min()
        safe = [z for z in Z_GRID if worst[z] >= 90.0]
        print(f"  → 최악 월쌍도 90% 이상인 최소 z: {safe[0] if safe else '격자 내 없음'}"
              f" (현재 z=1.65의 최악값 {worst[1.65]:.1f}%)\n")

        for z in Z_GRID:
            benefit_rows.append({"duration": duration, "z": z,
                                 "평균커버리지": means[z], "최악커버리지": worst[z]})

        stats = cost_input[duration]
        for z in Z_GRID:
            cost_rows.append({"duration": duration, "z": z,
                              **workload(rebal_qty_by_z(stats, z))})

    if not benefit_rows:
        print("계산할 자료가 없습니다.")
        return 1

    benefit = pd.DataFrame(benefit_rows)
    cost = pd.DataFrame(cost_rows)

    print("=== 비용: z별 작업량 (기준 " + args.period + ") ===")
    for duration in durations:
        part = cost[cost["duration"] == duration].set_index("z")
        if part.empty:
            continue
        base = part.loc[1.65]
        view = part[["drop_대여소", "drop_필요", "pick_대여소", "pick_가능", "처리_상한"]].copy()
        view["처리상한_증감"] = (part["처리_상한"] - base["처리_상한"]).astype(int)
        print(f"\n[{duration}]")
        print(view.to_string())

    print("\n=== 종합: 편익과 비용 ===")
    merged = benefit.merge(cost, on=["duration", "z"])
    summary = merged.groupby("z").agg(
        평균커버리지=("평균커버리지", "mean"),
        최악커버리지=("최악커버리지", "mean"),
        drop필요=("drop_필요", "sum"),
        pick가능=("pick_가능", "sum"),
        처리상한=("처리_상한", "sum")).round(1)
    base = summary.loc[1.65]
    summary["커버리지_증감"] = (summary["평균커버리지"] - base["평균커버리지"]).round(1)
    summary["처리상한_증감%"] = ((summary["처리상한"] / base["처리상한"] - 1) * 100).round(1)
    print(summary.to_string())

    print("\n해석 — 회차마다 z의 대가가 다르다:")
    for duration in durations:
        part = cost[cost["duration"] == duration].set_index("z")
        if part.empty:
            continue
        base, high = part.loc[1.65], part.loc[1.99]
        bound = "pick" if base["drop_필요"] > base["pick_가능"] else "drop"
        delta = (high["처리_상한"] / base["처리_상한"] - 1) * 100 if base["처리_상한"] else 0
        print(f"  {duration}: z=1.65에서 {bound}이 병목"
              f" (drop {int(base['drop_필요'])} vs pick {int(base['pick_가능'])})"
              f" → z=1.99에서 처리 상한 {delta:+.1f}%")
    print("  drop 필요량은 z에 비례해 늘지만 pick 가능량은 거의 그대로다.")
    print("  이미 drop이 pick을 넘어선 회차는 z를 올려도 작업량이 늘지 않고 우선순위만 바뀐다.")
    print("  pick 여력이 남는 회차는 그만큼 실제 작업이 늘어난다 — 시간 예산 확인 필요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
