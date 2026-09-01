"""순회거리 어림 셋 중 무엇이 실제에 가까운가 (1.26.53).

## 왜 이것부터 재나

`wanted_vehicles()`는 회차마다 **차를 몇 대 내보낼지**를 정한다. 그런데 현행 식은
이동시간을 **대여소 수**에만 비례시켜(`대여소 수 × 12.5분`) 모여 있는 20곳과
흩어진 20곳을 구분하지 못한다. 소요시간을 지배하는 것은 대여소 수(r=0.80)가 아니라
이동 거리(r=0.966)인데도 그렇다.

1.26.10에서 **BHH 면적 근사**로 거리를 넣어 봤더니 결품·물량·거리는 좋아졌는데
**예산 초과가 28건 → 62건으로 2.2배**가 됐다. 진단은 *"BHH가 균일분포를 가정하는데
실제 후보는 도로·생활권을 따라 뭉치므로 낙관적으로 어림한다"* 였다 — 그래서 K를
너무 작게 뽑았다는 것이다(EXPERIMENTS.md 5-H장).

**그 진단이 맞는지 이 스크립트가 확인한다.** K를 바꾸는 값비싼 실험을 또 돌리기
전에, 어림값 자체가 실제와 얼마나 어긋나는지부터 재는 것이 순서다. 실제와의
비율이 1보다 작으면 낙관적(= K를 적게 뽑는다), 크면 비관적이다.

## 무엇과 견주나

정답은 **실제 계획이 만든 이동거리**다. 다만 두 가지를 갈라 봐야 한다.

    군집 안 이동   ← 어림이 맞히려는 대상 (depot 왕복 제외)
    차고지 왕복    ← 어림이 따로 더하는 항 (총 이동의 61~65%다)

그래서 실제 경로에서 depot이 걸린 구간을 빼고 **군집 안 이동만** 뽑아 견준다.

실행:
    python experiments/diagnostic/tour_length_estimate.py
    python experiments/diagnostic/tour_length_estimate.py --periods "25년 11월"
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments" / "baseline"))

import pandas as pd

import baseline_compare as bc
from project_config import VEHICLE_SPEED_KMPH

def resolve_run_label(label=None):
    """재고 스냅샷 라벨을 정한다 — **못 찾으면 멈춘다.**

    `db.load_frame()`은 라벨이 비면 `latest_label()`로 **말없이 최신**을 쓴다.
    그러면 같은 명령이 다른 날 다른 재고로 돌고 결과에 그 사실이 남지 않는다
    (1.26.58에서 `gamma_sweep.py`가, 그 전에 `z_sweep.py`가 이 결함으로 걸렸다).
    판정 기준은 "인자가 있는가"가 아니라 **"못 찾았을 때 멈추는가"** 다.
    """
    import db
    with db.session() as conn:
        available = [r[0] for r in conn.execute(
            "SELECT DISTINCT run_label FROM station_info ORDER BY 1")]
    if not available:
        raise SystemExit("station_info가 비어 있습니다. 파이프라인을 한 번 돌리십시오.")
    chosen = label or available[-1]
    if chosen not in available:
        raise SystemExit(f"station_info에 '{chosen}' 실행이 없습니다."
                         f" --run-label 로 고르십시오: {available}")
    print(f"[스냅샷] station_info run_label = '{chosen}'"
          f"{' (기본: 최신)' if not label else ''}")
    return chosen


PERIODS = ("25년 09월", "25년 11월", "26년 01월", "26년 03월")
DURATIONS = ("_05_10", "_10_15", "_15_20")
# "현행"은 대조군이다 - 지금 wanted_vehicles()가 쓰는 `대여소 수 x 12.5분`을
# 같은 자(거리)로 환산해 나란히 놓는다. **이것이 빠지면 "무엇보다 나은가"를
# 물을 수 없다** - 새 어림끼리만 견주면 다 같이 못해도 하나가 이긴다.
METHODS = ("현행", "bhh", "nn", "mst")


def intra_cluster_km(routes: pd.DataFrame) -> float:
    """실제 경로에서 **차고지가 걸리지 않은** 구간의 거리 합.

    어림이 맞히려는 것은 군집 안 이동이고, depot 왕복은 어림식이 따로 더한다.
    섞어서 견주면 왕복이 총 이동의 61~65%라 어림의 성적이 그쪽에 묻힌다.
    """
    depot = bc.vrp_mod.DEPOT_ID
    inside = routes[(routes["from_id"] != depot) & (routes["to_id"] != depot)]
    return float(inside["distance_km"].sum())


def estimate_km(base, method: str, step1) -> float:
    """어림 하나가 내놓는 '후보 집합 전체 순회거리'(km)."""
    if method == "현행":
        # 현행 식은 시간을 준다: 대여소 수 x TRAVEL_MIN_PER_STATION.
        # 같은 자로 견주려면 거리로 되돌려야 한다(상수 속도 가정).
        return (len(base) * step1.TRAVEL_MIN_PER_STATION / 60.0
                * VEHICLE_SPEED_KMPH)
    return step1.total_tour_km(base, method)


def main() -> int:
    parser = argparse.ArgumentParser(description="순회거리 어림 셋을 실제와 견준다")
    parser.add_argument("--periods", default=",".join(PERIODS))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--run-label", default=None,
                        help="재고 스냅샷을 고정할 실행 라벨 (기본: 최신)")
    parser.add_argument("--out", default=None,
                        help="회차별 표를 CSV로 남긴다 (오래 걸리므로 결과를 잃지 않게)")
    args = parser.parse_args()

    periods = [p.strip() for p in args.periods.split(",") if p.strip()]
    run_label = resolve_run_label(args.run_label)
    step1 = bc.load_step1()
    solver = bc.ilp_mod.build_solver()

    rows = []
    for period in periods:
        net, st_info, warm = bc.load_inputs(period, run_label, "weekday", 0, "")
        for duration in DURATIONS:
            base = bc.build_candidates(net, st_info, duration, None, warm, 0, step1)
            if base.empty:
                continue
            _, routes = bc.plan_with_clusters(base.copy(), step1, solver,
                                              adjust=True, seed=args.seed)
            if routes.empty:
                continue

            actual = intra_cluster_km(routes)
            row = {"기간": period, "회차": duration, "후보": len(base),
                   "군집": int(routes["cluster"].nunique()), "실제km": round(actual, 1)}
            for method in METHODS:
                row[f"{method}_km"] = round(estimate_km(base, method, step1), 1)
            rows.append(row)
            print(f"  {period} {duration}: 후보 {len(base)} · 군집 {row['군집']}"
                  f" · 실제 {actual:.1f}km")

    if not rows:
        print("잴 자료가 없습니다.")
        return 1

    frame = pd.DataFrame(rows)
    if args.out:
        frame.to_csv(args.out, index=False, encoding="utf-8-sig")
        print(f"\n표를 남겼습니다 -> {args.out}")

    print("\n" + "=" * 92)
    print("어림 대 실제 (군집 안 이동만 · depot 왕복 제외)")
    print("=" * 92)
    print(frame.to_string(index=False))

    print("\n" + "=" * 92)
    print("보정 상수를 곱했을 때 남는 오차")
    print("=" * 92)
    print("어림값이 실제의 몇 배인지는 문제가 아니다 - **일정하기만 하면 상수로")
    print("흡수된다.** 그래서 각 어림에 최적 보정계수를 곱한 뒤 남는 오차로 견준다.")
    print()

    summary = []
    for method in METHODS:
        ratio = frame["실제km"] / frame[f"{method}_km"]
        factor = float(ratio.mean())
        error = ((frame[f"{method}_km"] * factor - frame["실제km"]).abs()
                 / frame["실제km"])
        summary.append({
            "어림": method,
            "보정계수": round(factor, 3),
            "MAPE": round(float(error.mean()) * 100, 1),
            "최악": round(float(error.max()) * 100, 1),
            # 척도가 다른 어림끼리는 표준편차로 견주면 안 된다(작게 어림하는 쪽이
            # 자동으로 이긴다). 변동계수로 본다.
            "CV%": round(float(ratio.std(ddof=0) / ratio.mean()) * 100, 1),
        })
    table = pd.DataFrame(summary)
    print(table.to_string(index=False))

    print("\n판정")
    best = table.loc[table["MAPE"].idxmin()]
    current = table[table["어림"] == "현행"].iloc[0]
    print(f"  · 가장 정확한 어림: **{best['어림']}**"
          f" (MAPE {best['MAPE']}% · 최악 {best['최악']}% · CV {best['CV%']}%)")
    if best["어림"] == "현행":
        print("  · **거리를 보는 어림 셋이 모두 현행(대여소 수)보다 못하다.**")
        print("    '식이 거리를 안 봐서 틀린다'는 진단이 이 자료에서는 지지되지 않는다 -")
        print("    군집 전에 기하로 거리를 어림하는 것이 대여소 수를 세는 것보다 낫지 않다.")
    else:
        print(f"  · 현행 대비 MAPE {current['MAPE']}% → {best['MAPE']}%."
              f" 다음은 이 어림으로 K를 뽑아 예산 초과가 나빠지지 않는지 보는 것이다"
              f" (5-H에서 BHH가 결품은 이겼지만 초과 2.2배로 막혔다).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
