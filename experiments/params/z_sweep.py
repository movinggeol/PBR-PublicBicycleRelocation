"""실험 A — `z`를 얼마로 둘 것인가 (docs/분석/EXPERIMENTS.md 1장).

`target_qty = mu + z·sigma`의 `z`는 "정규분포에서 95%를 덮는 값"이라는 이유로
1.65가 쓰여 왔다. 백테스트(1.11.0)에서 실제 커버리지가 91.7~92.8%로 나와,
이 가정이 맞지 않는다는 것이 드러났다.

이 스크립트는 두 가지를 함께 잰다. 하나만 보면 결론을 낼 수 없기 때문이다.

  1. 편익 — z를 올리면 커버리지가 얼마나 오르는가 (11개 월쌍 백테스트)
  2. 비용 — z를 올리면 작업량이 얼마나 늘어나는가
           (실제 st_info로 rebal_qty를 다시 계산)

**평일과 휴일은 절대 섞지 않는다**(프로젝트 규약). `--day-type`으로 한쪽만 골라
잰다. 기본은 `weekday`인데, 이 스크립트가 처음 만들어질 때(1.11.x) 요일 옵션이
없어 사실상 평일+휴일을 섞어 재고 있었기 때문이다 — 그때 나온 `z=1.99`는
**평일 기준 값으로 다시 확인해야 하는 값**이었다(1.26.39).

실행:
    python experiments/params/z_sweep.py
    python experiments/params/z_sweep.py --day-type holiday
    python experiments/params/z_sweep.py --duration _05_10 --period "25년 11월"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]      # experiments/<분류>/ 아래에 있다
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import numpy as np
import pandas as pd

import db
from backtest_demand import DURATIONS, consecutive_pairs, daily_window_demand
from project_config import DAY_TYPES, normalize_day_type, select_day_type

Z_GRID = [1.28, 1.65, 1.80, 1.99, 2.10, 2.33, 2.58]
MAX_CAPACITY = 10


def limit_days(net: dict, days: int, seed: int) -> dict:
    """각 기간을 무작위 `days`일로 줄인다 — **표본 크기를 맞춘 대조군용**.

    휴일은 달마다 8~13일뿐이고 평일은 17~23일이다. 휴일 커버리지가 낮게 나올 때
    그것이 '휴일 수요가 유별나서'인지 '학습 표본이 작아 mu·sigma가 흔들려서'인지
    구분하려면, **평일을 같은 일수로 줄여** 같은 조건에서 재야 한다(1.26.39).
    """
    rng = np.random.default_rng(seed)
    out = {}
    for period, frame in net.items():
        unique = np.sort(frame["date"].unique())
        if len(unique) <= days:
            out[period] = frame
            continue
        keep = rng.choice(unique, size=days, replace=False)
        out[period] = frame[frame["date"].isin(keep)]
    return out


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


def load_station_stats(conn, period: str, duration: str, run_label: str,
                       day_type: str) -> pd.DataFrame:
    """rebalance_plan과 동일한 입력(mu/sigma + parking_lot/stock)을 재구성한다."""
    from backtest_demand import window_hours

    net = select_day_type(db.load_frame(conn, "net_demand", period=period),
                          "date", day_type)
    columns = [f"net_{h:02d}" for h in window_hours(duration) if f"net_{h:02d}" in net.columns]
    net["window"] = net[columns].sum(axis=1)

    stats = net.groupby("station_id")["window"].agg(mu="mean", sigma="std").fillna(0).reset_index()

    # 라벨을 안 주면 **계획 실행 중에서** 고른다 — 종류를 안 가리면 파라미터
    # 스윕 스냅샷(`sweep-10`, 재고 −10.4%)을 집는다 (1.26.132).
    info = db.load_frame(conn, "station_info",
                         **({"run_label": run_label} if run_label
                            else {"kinds": ("plan",)}))
    if info.empty:
        # 예전에는 없는 라벨이어도 빈 병합이 통과해 **비용 표가 통째로 0**으로
        # 나왔다(1.26.39에서 발견). 조용히 틀린 답을 내느니 멈춘다.
        available = [r[0] for r in conn.execute(
            "SELECT DISTINCT run_label FROM station_info ORDER BY 1")]
        raise SystemExit(
            f"station_info에 '{run_label}' 실행이 없습니다."
            f" --run-label 로 고르십시오: {available}")
    stats = stats.merge(info[["station_id", "parking_lot", "stock"]], on="station_id", how="left")
    return stats[~stats["stock"].isna()]


def main() -> int:
    parser = argparse.ArgumentParser(description="z 스윕 — 커버리지와 작업량의 맞바꿈")
    parser.add_argument("--duration", help="시간대 하나만")
    parser.add_argument("--period", default="25년 11월", help="비용 계산에 쓸 기간")
    # 기본값을 박아 두면 DB가 바뀌었을 때 조용히 빈 표가 된다 — 최신 실행을 쓴다.
    parser.add_argument("--run-label", default=None,
                        help="st_info 실행 라벨 (기본: station_info의 최신 실행)")
    parser.add_argument("--day-type", default="weekday", choices=list(DAY_TYPES),
                        help="평일/휴일 (기본 weekday). 섞어서 재지 않는다")
    parser.add_argument("--sample-days", type=int, default=None,
                        help="각 기간을 이만큼의 날로 줄인다 (표본 크기 대조군)")
    parser.add_argument("--repeats", type=int, default=5,
                        help="--sample-days를 쓸 때 뽑기를 몇 번 반복해 평균 낼지")
    args, _ = parser.parse_known_args()

    durations = [args.duration] if args.duration else DURATIONS
    day_type = normalize_day_type(args.day_type)

    with db.session() as conn:
        run_label = args.run_label or conn.execute(
            "SELECT MAX(run_label) FROM station_info").fetchone()[0]
        periods = [r[0] for r in conn.execute("SELECT DISTINCT period FROM net_demand").fetchall()]
        net = {p: select_day_type(db.load_frame(conn, "net_demand", period=p),
                                  "date", day_type)
               for p in periods}
        cost_input = {d: load_station_stats(conn, args.period, d, run_label,
                                            day_type) for d in durations}

    # 휴일이 없는 기간이 섞이면 월쌍이 조용히 어긋난다 — 먼저 걸러 낸다.
    if (empty := [p for p, frame in net.items() if frame.empty]):
        print(f"'{day_type}' 자료가 없는 기간: {', '.join(sorted(empty))}")
        print("  tools/rebuild_net_demand.py 로 순수요를 다시 만드십시오"
              " (1.14.0 이전 산출물에는 휴일이 없습니다).\n")
        net = {p: f for p, f in net.items() if not f.empty}
        periods = sorted(net)

    pairs = consecutive_pairs(periods)
    print(f"요일 {day_type} · 기간 {len(periods)}개 · 연속 월쌍 {len(pairs)}개"
          f" · st_info '{run_label}'")
    sample = {p: int(net[p]['date'].nunique()) for p in sorted(net)}
    print(f"기간별 표본 일수: {sample}")
    if args.sample_days:
        print(f"⚠ 표본 크기 대조군 — 각 기간을 {args.sample_days}일로 줄여"
              f" {args.repeats}회 평균 냅니다. 비용 표는 줄이지 않은 자료입니다.")
    print()

    benefit_rows, cost_rows = [], []

    for duration in durations:
        if args.sample_days:
            draws = [coverage_by_z(limit_days(net, args.sample_days, seed),
                                   pairs, duration)
                     for seed in range(args.repeats)]
            draws = [d for d in draws if not d.empty]
            frame = (pd.concat(draws).groupby(["학습", "검증"], as_index=False).mean()
                     if draws else pd.DataFrame())
        else:
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

    print(f"=== 비용: z별 작업량 (기준 {args.period} · {day_type}) ===")
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
