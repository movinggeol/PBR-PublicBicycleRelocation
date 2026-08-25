"""대조군 비교 — 제안 방법이 단순한 방법보다 정말 나은가.

지금까지 프로젝트의 모든 수치는 **"재배치 전 → 후"** 자기 비교였다. 그래서
"그냥 부족한 데부터 채우면 되지 않나"에 답하는 숫자가 하나도 없었다
(docs/연구/THESIS.md 3장). 이 스크립트가 그 표를 만든다.

    P   제안 방법        군집(K-Medoids + 불균형 조정) → ILP → VRP
    B0  무재배치         아무 것도 하지 않는다 (하한선)
    B1  그리디           군집·ILP 없이 차량이 가까운 곳부터 계속 훑는다
    B2  평균 목표재고    z = 0 (안전재고 없음), 이후는 P와 같다
    B3  지리 균등 군집   K-Medoids만 쓰고 불균형 조정을 하지 않는다, 이후는 P와 같다

**모든 방법을 같은 자로 잰다.** 판정 기준은 결품 시간이다 — 개선률·목표 도달률은
`target_qty`를 분모로 삼아 z가 다른 B2와는 비교조차 할 수 없다(docs/분석/KPI.md).

**'실제로 옮긴 대수'로 평가한다.** ILP는 군집 안에서 min(pick, drop)만큼만 옮기므로
계획량(rebal_qty)이 전부 집행되지는 않는다. 계획으로 재면 군집·ILP를 건너뛴 B1도
같은 점수가 나와 비교가 성립하지 않는다(이 실험이 그것을 드러냈고, 1.18.4에서
파이프라인의 step4도 집행 기준으로 바뀌었다). `--plan-basis`로 계획 기준도 함께 본다.

측정 코드는 운영 코드를 그대로 부른다 — build_stats·compute_rebal_qty·
select_top_unbalanced_st·make_clustering·adjust_clustering·solve_cluster_moves·
greedy_route·_stockout_hours 전부 파이프라인의 함수다. 측정이 제 방식대로 계산하면
측정이 거짓말을 한다(docs/분석/DEMAND_DISTRIBUTION.md 5장에서 실제로 겪었다).

사용법:
    python experiments/baseline/baseline_compare.py --period "25년 11월"
    python experiments/baseline/baseline_compare.py --period "26년 03월" --duration "_05_10"
    python experiments/baseline/baseline_compare.py --methods P,B1 --seed 7
"""
import argparse
import contextlib
import importlib.util
import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pulp

ROOT = Path(__file__).resolve().parents[2]      # experiments/<분류>/ 아래에 있다
sys.path.insert(0, str(ROOT))
for _folder in ("step0_collect", "step1_cluster",
                "step2_optimize", "step4_metrics"):
    sys.path.insert(0, str(ROOT / _folder))

import db                                   # noqa: E402
import calculate_target_qty as target_mod   # noqa: E402  (step0)
import ilp as ilp_mod                       # noqa: E402  (step2)
import vrp as vrp_mod                       # noqa: E402  (step2)
import imbalance as kpi_mod                 # noqa: E402  (step4)
from project_config import (                # noqa: E402
    DEFAULT_PERIOD, DEFAULT_WARMUP_DAYS, TIME_BUDGET_MINUTES, VEHICLES_PER_ROUND,
    normalize_day_type, select_day_type,
)

METHODS = ("P", "B0", "B1", "B2", "B3")
LABELS = {
    "P": "P  제안 (군집+조정→ILP→VRP)",
    "B0": "B0 무재배치",
    "B1": "B1 그리디 (군집·ILP 없음)",
    "B2": "B2 평균 목표재고 (z=0)",
    "B3": "B3 지리 균등 군집 (조정 없음)",
}

# 대여소 정보 컬럼 순서 — select_top_unbalanced_st가 위치(iloc)로 고르므로
# st_info ({now}).csv와 같은 순서를 지켜야 한다.
ST_INFO_COLUMNS = ["station_id", "station_name", "lat", "lon", "parking_lot",
                   "stock", "rent_count", "return_count", "total_use_min", "total_use_km"]


def load_step1():
    """step1 군집 모듈을 불러온다 (파일명이 숫자로 시작해 일반 import가 안 된다)."""
    path = ROOT / "step1_cluster" / "top_st_clustering.py"
    spec = importlib.util.spec_from_file_location("top_st_clustering", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def quiet(func, *args, **kwargs):
    """파이프라인 함수는 콘솔 출력이 많다 — 실험 표를 가리지 않게 삼킨다."""
    with contextlib.redirect_stdout(io.StringIO()):
        return func(*args, **kwargs)


# ---------------------------------------------------------------- 입력

def load_inputs(period, run_label, day_type, warmup_days, warmup_period):
    """순수요·대여소 정보를 DB에서 읽는다."""
    with db.session() as conn:
        net = db.load_frame(conn, "net_demand", period=period)
        info = db.load_frame(conn, "station_info",
                             **({"run_label": run_label} if run_label else {}))
        warmup = pd.DataFrame()
        if warmup_days > 0 and warmup_period and warmup_period != period:
            warmup = db.load_frame(conn, "net_demand", period=warmup_period)

    if net.empty:
        raise SystemExit(f"순수요가 없습니다 (기간 {period}). tools/load_rentals.py로 적재하세요.")
    if info.empty:
        raise SystemExit("대여소 정보가 없습니다. 파이프라인을 한 번 돌려 station_info를 채우세요.")

    net = select_day_type(net.rename(columns={"date": "날짜"}), "날짜", day_type)
    if not warmup.empty:
        warmup = select_day_type(warmup.rename(columns={"date": "날짜"}), "날짜", day_type)

    missing = [c for c in ST_INFO_COLUMNS if c not in info.columns]
    if missing:
        raise SystemExit(f"station_info에 컬럼이 없습니다: {missing}")
    return net, info[ST_INFO_COLUMNS].copy(), warmup


def build_candidates(net, st_info, duration, z, warmup, warmup_days, step1):
    """목표재고 → 재배치량 → 작업 대상 선정. 전부 운영 코드를 그대로 부른다."""
    stats, _daily, _ratio = quiet(
        target_mod.build_stats, net, st_info[["station_id", "parking_lot", "stock"]],
        duration, warmup_net=(warmup if not warmup.empty else None),
        warmup_days=warmup_days, verbose=False)

    rebal = quiet(target_mod.compute_rebal_qty, stats, z=z)

    # select_top_unbalanced_st는 CSV 경로를 받는다 — 메모리 버퍼로 대신한다.
    buffer = io.StringIO()
    rebal.to_csv(buffer, index=False, encoding="utf-8")
    buffer.seek(0)
    return quiet(step1.select_top_unbalanced_st, buffer, duration, st_info)


# ---------------------------------------------------------------- 방법별 계획

def plan_with_clusters(candidates, step1, solver, adjust, seed):
    """군집 → ILP → VRP. adjust=False면 K-Medoids 결과를 그대로 쓴다(B3)."""
    clustered = quiet(step1.make_clustering, candidates.copy(), random_state=seed).copy()
    if adjust:
        clustered = quiet(step1.adjust_clustering, clustered).copy()

    frame = clustered.copy()
    frame["drop_qty"] = frame["rebal_qty"].clip(lower=0).astype(int)
    frame["pick_qty"] = (-frame["rebal_qty"].clip(upper=0)).astype(int)

    moves = []
    for cluster in frame["cluster"].unique():
        for row in quiet(ilp_mod.solve_cluster_moves,
                         frame[frame["cluster"] == cluster], solver):
            moves.append({"cluster": cluster, **row})
    moves = pd.DataFrame(moves)
    if moves.empty:
        return clustered, pd.DataFrame()

    coords = clustered.set_index("station_id")[["lat", "lon"]]
    rows = []
    for cluster in moves["cluster"].unique():
        part = moves[moves["cluster"] == cluster]
        nodes = {}
        for sid, qty in part.groupby("pick_station_id")["qty"].sum().items():
            nodes[(sid, "pick")] = {"qty": int(qty),
                                    "lat": coords.loc[sid, "lat"],
                                    "lon": coords.loc[sid, "lon"]}
        for sid, qty in part.groupby("drop_station_id")["qty"].sum().items():
            nodes[(sid, "drop")] = {"qty": int(qty),
                                    "lat": coords.loc[sid, "lat"],
                                    "lon": coords.loc[sid, "lon"]}
        rows.extend(quiet(vrp_mod.greedy_route, nodes, cluster))
    return clustered, pd.DataFrame(rows)


def plan_greedy(candidates):
    """B1 — 군집도 ILP도 없이, 차량이 depot에서 가장 가까운 작업지를 계속 고른다.

    같은 대수·같은 적재 용량·같은 경로 엔진(greedy_route)을 쓴다. 다른 것은
    **묶지 않고 짝짓지 않는다**는 것뿐이라, 차이가 곧 군집+ILP의 몫이다.
    한 대가 전체를 훑을 수는 없으므로 차량마다 시간 예산에서 멈춘다.
    """
    nodes = {}
    for row in candidates.itertuples():
        qty = int(abs(row.rebal_qty))
        if qty <= 0:
            continue
        kind = "drop" if row.rebal_qty > 0 else "pick"
        nodes[(row.station_id, kind)] = {"qty": qty, "lat": row.lat, "lon": row.lon}

    rows = []
    for vehicle in range(VEHICLES_PER_ROUND):
        rows.extend(quiet(vrp_mod.greedy_route, nodes, vehicle,
                          TIME_BUDGET_MINUTES * 60))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 평가

# VRP가 실제로 옮긴 양을 재고 증감으로 바꾸는 계산은 **step4의 함수를 그대로 쓴다**
# (1.18.4에서 파이프라인이 같은 기준을 쓰게 되면서 옮겨 갔다).
executed_delta = kpi_mod.executed_delta


def stockout(net, candidates, delta, duration):
    """결품 시간(대여소·일 평균)을 재배치 전후로 잰다. step4와 같은 함수를 쓴다."""
    hours = kpi_mod.duration_hours(duration)
    stations = candidates[["station_id", "stock", "parking_lot"]].copy()
    stations["delta"] = stations["station_id"].map(delta).fillna(0)

    merged = net.merge(stations, on="station_id", how="inner")
    if merged.empty:
        return None, None

    before = kpi_mod._stockout_hours(merged, merged["stock"],
                                     merged["parking_lot"], hours)
    after = kpi_mod._stockout_hours(merged, merged["stock"] + merged["delta"],
                                    merged["parking_lot"], hours)
    days = merged["날짜"].nunique()
    count = merged["station_id"].nunique()
    denominator = max(count * days, 1)
    return float(before.sum() / denominator), float(after.sum() / denominator)


def route_stats(routes):
    """이동거리·최장 소요시간·예산 초과 건수·처리 대수(pick 기준)."""
    if routes.empty:
        return {"bikes": 0, "km": 0.0, "max_min": 0.0, "over": 0, "vehicles": 0}
    minutes = routes.groupby("cluster")["cum_sec"].max() / 60
    picked = routes[routes["action"] == "pick"]["qty"].sum()
    return {
        "bikes": int(picked),
        "km": float(routes["distance_km"].sum()),
        "max_min": float(minutes.max()),
        "over": int((minutes > TIME_BUDGET_MINUTES).sum()),
        "vehicles": int(routes["cluster"].nunique()),
    }


# ---------------------------------------------------------------- 실행

def run_duration(net, st_info, warmup, duration, args, step1, solver):
    print("\n" + "=" * 92)
    print(f"< {duration} >  기간 {args.period} · {args.day_type} · 씨앗 {args.seed}")
    print("=" * 92)

    base = build_candidates(net, st_info, duration, None, warmup,
                            args.warmup_days, step1)
    if base.empty:
        print("[건너뜀] 재배치 대상이 없습니다 (Pick 또는 Drop 후보 없음)")
        return []

    print(f"작업 대상 {len(base)}곳 "
          f"(Pick {(base['rebal_qty'] < 0).sum()} / Drop {(base['rebal_qty'] > 0).sum()})")

    results = []
    for name in args.methods:
        if name == "B0":
            candidates, routes = base, pd.DataFrame()
        elif name == "B1":
            candidates, routes = base, plan_greedy(base)
        elif name == "B2":
            zero = build_candidates(net, st_info, duration, 0.0, warmup,
                                    args.warmup_days, step1)
            if zero.empty:
                print(f"[건너뜀] {name}: z=0에서는 재배치 대상이 없습니다")
                continue
            candidates, routes = plan_with_clusters(zero, step1, solver,
                                                    adjust=True, seed=args.seed)
        elif name == "B3":
            candidates, routes = plan_with_clusters(base.copy(), step1, solver,
                                                    adjust=False, seed=args.seed)
        else:
            candidates, routes = plan_with_clusters(base.copy(), step1, solver,
                                                    adjust=True, seed=args.seed)

        delta = executed_delta(routes)
        before, after = stockout(net, candidates, delta, duration)
        row = {"duration": duration, "method": name, "seed": args.seed,
               "period": args.period, "day_type": args.day_type,
               "stations": int(len(candidates)),
               "stockout_before": before, "stockout_after": after,
               **route_stats(routes)}

        if args.plan_basis:
            plan_delta = candidates.set_index("station_id")["rebal_qty"]
            _b, plan_after = stockout(net, candidates, plan_delta, duration)
            row["plan_after"] = plan_after

        results.append(row)
        print(f"  {LABELS[name]:<34} 처리 {row['bikes']:>4d}대"
              f"  결품 {before:.2f}h → {after:.2f}h")

    return results


def show(frame, plan_basis):
    for duration, part in frame.groupby("duration", sort=False):
        base = part[part["method"] == "B0"]
        origin = float(base["stockout_after"].iloc[0]) if not base.empty else None

        print("\n" + "-" * 92)
        print(f"[{duration}]  대조군 비교 — 판정 기준은 결품 시간 (대여소·일 평균)")
        print("-" * 92)
        header = (f"{'방법':<34}{'처리대수':>8}{'이동km':>9}{'최장분':>8}"
                  f"{'초과':>5}{'결품h':>8}{'감소':>8}{'감소율':>8}")
        if plan_basis:
            header += f"{'계획기준':>9}"
        print(header)

        for row in part.itertuples():
            drop = origin - row.stockout_after if origin is not None else float("nan")
            rate = (drop / origin * 100) if origin else float("nan")
            line = (f"{LABELS[row.method]:<34}{row.bikes:>8d}{row.km:>9.1f}"
                    f"{row.max_min:>8.1f}{row.over:>5d}"
                    f"{row.stockout_after:>8.2f}{drop:>8.2f}{rate:>7.1f}%")
            if plan_basis:
                plan = getattr(row, "plan_after", float("nan"))
                line += f"{plan:>9.2f}"
            print(line)

    print("\n" + "=" * 92)
    print("읽는 법")
    print("  · 결품h = 재배치 후 대여소·일 평균 결품 시간. **낮을수록 좋다.**")
    print("  · 감소 = B0(무재배치) 대비 줄어든 결품 시간. 이 값이 재배치의 실제 편익이다.")
    print("  · 초과 = 시간 예산을 넘긴 차량 수. P는 예산을 사후 점검만 하므로 초과가 날 수 있고,")
    print("    B1은 예산 안에서 멈추므로 초과가 0인 대신 일을 덜 한다 — 함께 봐야 한다.")
    if plan_basis:
        print("  · 계획기준 = rebal_qty가 전부 집행됐다고 가정한 결품 시간(step4의 계산 방식).")
        print("    '결품h'와의 차이가 곧 **계획과 집행의 격차**다.")
    print("  · 시간대(duration)가 다르면 수요 구조가 반대다 — 섞어서 평균 내지 마라.")
    print("=" * 92)


def main():
    parser = argparse.ArgumentParser(description="대조군 비교 실험")
    parser.add_argument("--period", default=DEFAULT_PERIOD, help='순수요 기간 (예: "25년 11월")')
    parser.add_argument("--duration", default="_05_10,_10_15,_15_20", help="시간대 (콤마 구분)")
    parser.add_argument("--day-type", default="weekday", choices=["weekday", "holiday"])
    parser.add_argument("--run-label", default="", help="대여소 정보를 가져올 실행 라벨 (기본: 최신)")
    parser.add_argument("--warmup-period", default="", help="계절 보정에 쓸 기간 (기본: 사용 안 함)")
    parser.add_argument("--warmup-days", type=int, default=DEFAULT_WARMUP_DAYS)
    parser.add_argument("--seed", type=int, default=42, help="K-Medoids 씨앗 (변동성 측정용)")
    parser.add_argument("--methods", default=",".join(METHODS))
    parser.add_argument("--plan-basis", action="store_true",
                        help="계획량(rebal_qty)이 전부 집행됐다고 본 결품 시간도 함께 낸다")
    parser.add_argument("--out", default="", help="결과를 CSV로 저장할 경로")
    args, _ = parser.parse_known_args()

    args.day_type = normalize_day_type(args.day_type)
    args.methods = [m.strip().upper() for m in args.methods.split(",") if m.strip()]
    unknown = [m for m in args.methods if m not in METHODS]
    if unknown:
        raise SystemExit(f"알 수 없는 방법: {unknown} (가능: {', '.join(METHODS)})")

    step1 = load_step1()
    solver = ilp_mod.build_solver()   # 파이프라인과 같은 솔버 설정
    net, st_info, warmup = load_inputs(args.period, args.run_label, args.day_type,
                                       args.warmup_days, args.warmup_period)

    rows = []
    for duration in [d.strip() for d in args.duration.split(",") if d.strip()]:
        rows.extend(run_duration(net, st_info, warmup, duration, args, step1, solver))

    if not rows:
        raise SystemExit("비교할 결과가 없습니다.")

    frame = pd.DataFrame(rows)
    show(frame, args.plan_basis)

    if args.out:
        frame.to_csv(args.out, index=False, encoding="utf-8")
        print(f"\n결과를 저장했습니다: {args.out}")


if __name__ == "__main__":
    main()
