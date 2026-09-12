"""실험 — 중심 통계를 **평균에서 중앙값으로** 바꾸면 결품이 주나 (TODO P4).

## 무엇을 재나

목표 재고는 지금 `mu + z·sigma`, 즉 **평균 + z·표준편차**다. 순수요 분포는
대여소마다 치우쳐 있고 이상치도 있어(docs/분석/DEMAND_DISTRIBUTION.md),
**중앙값 + z·MAD** 가 더 나을 수 있다는 것이 오래 열려 있던 후보다.

    현행   target_qty = mean(demand)   + z · std(demand)
    대안   target_qty = median(demand) + z · (1.4826 · MAD(demand))

## 🔴 1.4826을 곱하는 이유 — **자를 같게 두려고**

MAD를 그대로 쓰면 정규분포에서 sigma의 **0.6745배**라, 같은 z를 넣어도 목표가
작아진다. 그러면 이 실험은 *"중앙값이 나은가"* 가 아니라 *"z를 낮추면 나은가"*
를 재게 된다 — 이미 답이 있는 질문이고(11·18장), 두 변화가 섞여 어느 쪽 몫인지
가릴 수 없다. **1.4826 = 1/0.6745** 를 곱하면 MAD가 sigma의 추정량이 되어
**z의 뜻이 양쪽에서 같아진다.**

⚠️ `--raw-mad`로 배율 없이도 잴 수 있다. 다만 그 결과는 "중심 통계의 몫"이
아니라 "중심 통계 + 더 작은 z"의 합이다 — 해석할 때 섞지 마라.

## 🔴 모집단 중립성 — 11·17·18장에서 세 번 데인 함정

중심 통계를 바꾸면 `target_qty`가 바뀌고 → `rebal_qty`가 바뀌고 → **작업 대상
집합이 바뀐다.** 결품은 그 집합 위의 *평균*이라(합계 / (대여소수 × 일수))
분모가 같이 흔들린다. 그러면 **자가 승자를 정한다.**

litmus: **재배치 '전' 결품이 방법에 따라 움직이면 그 비교는 무효다.** 아무것도
하지 않은 상태의 값이 방법에 따라 달라질 수는 없다.

그래서 `z_fixedpop_grid.py`와 같은 방식으로 **모집단을 고정**한다.

    평균 후보    = 현행(평균) 방식이 고른 집합
    중앙값 후보  = 대안(중앙값) 방식이 고른 집합
    전체 대여소  = station_info 전부         ← **가장 중립적이다**

셋이 같은 승자를 가리키면 모집단과 무관하다. 갈리면 그 자체가 결론이다.

## 무엇을 건드리지 않았나

**하류는 운영 코드를 그대로 태운다.** 바꾸는 것은 `stats`의 `mu`·`sigma` 두
열에 무엇을 담느냐뿐이고, `compute_rebal_qty`·`select_top_unbalanced_st`·
군집화·ILP·VRP·결품 계산은 전부 파이프라인 함수를 그대로 부른다. 계절 배율
(`apply_warmup`)도 같은 함수를 쓴다 — 측정 코드가 운영 코드와 갈리면 측정이
거짓말을 한다(DEMAND_DISTRIBUTION 5장에서 실제로 겪었다).

사용법:
    python experiments/params/center_stat_grid.py
    python experiments/params/center_stat_grid.py --period "26년 03월" --z-grid 1.65,1.99,2.33
    python experiments/params/center_stat_grid.py --raw-mad --out result.csv
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import demand_model  # noqa: E402
from pipeline.step0_collect.calculate_target_qty import duration_columns  # noqa: E402

# 정규분포에서 MAD × 1.4826 이 sigma의 일치추정량이다(1 / 0.6745).
MAD_TO_SIGMA = 1.4826

# 기본 z 격자 — 18장이 쓴 좁은 격자와 같다(양쪽을 같은 자로 재려고).
Z_GRID = [1.65, 1.80, 1.99, 2.10, 2.33]


def load_baseline():
    path = ROOT / "experiments" / "baseline" / "baseline_compare.py"
    spec = importlib.util.spec_from_file_location("baseline_compare", path)
    bc = importlib.util.module_from_spec(spec)
    sys.modules["baseline_compare"] = bc
    spec.loader.exec_module(bc)
    return bc


# ------------------------------------------------------------------ 중심 통계

def build_stats_median(bc, net_daily, st_initial_qty, duration,
                       warmup_net, warmup_days, mad_scale):
    """`target_mod.build_stats`를 그대로 베끼되 **평균·표준편차만** 중앙값·MAD로 바꾼다.

    열 이름은 `mu`·`sigma` 그대로 둔다 — 하류(`apply_warmup`,
    `compute_rebal_qty`)가 그 이름으로 읽으므로, 이름을 바꾸면 하류까지
    갈라져 비교가 성립하지 않는다.
    """
    net_temp = net_daily.copy()
    hours = duration_columns(duration)
    column = f"sum{duration}"
    net_temp[column] = net_temp[hours].sum(axis=1)

    grouped = net_temp.groupby("station_id")[column]
    center = grouped.median()
    # MAD = median(|x - median|). 대여소마다 자기 중앙값으로 잰다.
    spread = grouped.apply(lambda s: float(np.median(np.abs(s - np.median(s)))))

    stats = pd.DataFrame({"mu": center, "sigma": spread * mad_scale})
    stats = stats.fillna(0)
    stats = stats.merge(st_initial_qty, how="left", on="station_id")
    stats = stats[~stats["stock"].isna()]

    daily = pd.DataFrame({
        "station_id": net_temp["station_id"].values,
        "date": net_temp["날짜"].values,
        "demand": net_temp[column].values,
    })

    ratio = None
    if warmup_net is not None and not warmup_net.empty:
        recent = warmup_net.copy()
        recent[column] = recent[hours].sum(axis=1)
        ratio = demand_model.season_ratio(daily, pd.DataFrame({
            "station_id": recent["station_id"].values,
            "date": recent["날짜"].values,
            "demand": recent[column].values,
        }), warmup_days)

    return demand_model.apply_warmup(stats, ratio), daily, ratio


def build_candidates_median(bc, net, st_info, duration, z, warmup,
                            warmup_days, step1, mad_scale):
    """`bc.build_candidates`와 같은 절차 — `build_stats`만 중앙값판으로 갈아 낀다."""
    stats, _daily, _ratio = build_stats_median(
        bc, net, st_info[["station_id", "parking_lot", "stock"]], duration,
        (warmup if not warmup.empty else None), warmup_days, mad_scale)

    rebal = bc.quiet(bc.target_mod.compute_rebal_qty, stats, z=z)

    buffer = io.StringIO()
    rebal.to_csv(buffer, index=False, encoding="utf-8")
    buffer.seek(0)
    return bc.quiet(step1.select_top_unbalanced_st, buffer, duration, st_info)


# ------------------------------------------------------------------ 측정

def measure(bc, args) -> pd.DataFrame:
    step1 = bc.load_step1()
    solver = bc.ilp_mod.build_solver()
    net, st_info, warmup = bc.load_inputs(
        args.period, args.run_label, args.day_type, args.warmup_days, "")
    print(f"[스냅샷] station_info run_label = '{args.run_label or '최신'}'"
          f" ({len(st_info)}곳)", flush=True)
    print(f"[MAD 배율] ×{args.mad_scale}"
          f"{'  ⚠️ 배율 없음 — z의 뜻이 양쪽에서 다르다' if args.mad_scale == 1.0 else ''}",
          flush=True)

    rows = []
    for duration in args.durations:
        arms = {}       # (방법, z) -> 후보
        for z in args.z_grid:
            mean_c = bc.build_candidates(net, st_info, duration, z, warmup,
                                         args.warmup_days, step1)
            med_c = build_candidates_median(bc, net, st_info, duration, z, warmup,
                                            args.warmup_days, step1, args.mad_scale)
            if not mean_c.empty:
                arms[("평균", z)] = mean_c
            if not med_c.empty:
                arms[("중앙값", z)] = med_c
        if not arms:
            continue

        # 모집단 셋 — 각 방법이 기준 z에서 고른 집합, 그리고 전체
        base = args.pop_z
        pops = {"전체대여소": st_info}
        if ("평균", base) in arms:
            pops[f"평균후보(z={base})"] = arms[("평균", base)]
        if ("중앙값", base) in arms:
            pops[f"중앙값후보(z={base})"] = arms[("중앙값", base)]

        for (method, z), cand in arms.items():
            for seed in args.seeds:
                _c, routes = bc.plan_with_clusters(
                    cand.copy(), step1, solver, adjust=True, seed=seed)
                delta = bc.executed_delta(routes)
                stats = bc.route_stats(routes)
                row = {"duration": duration, "방법": method, "z": z, "seed": seed,
                       "candidates": int(len(cand)),
                       "km": stats.get("km"), "over": stats.get("over"),
                       "bikes": stats.get("bikes")}
                # 모집단을 일부러 옮겨 가며 잰다 — 분모 감시의 거짓 경보를 막는다.
                for name, pop in pops.items():
                    bc.reset_population_guard()
                    before, after = bc.stockout(net, pop, delta, duration)
                    row[f"before::{name}"] = before
                    row[f"after::{name}"] = after
                rows.append(row)
        print(f"  {duration} 완료", flush=True)
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ 보고

def report(df) -> None:
    """🔴 **회차를 섞지 않는다.**

    처음에 회차를 뭉쳐 평균 낸 표를 냈다가 지웠다. 두 가지가 한꺼번에 틀린다.

    1. **litmus가 거짓 경보를 낸다.** 재배치 '전' 결품은 회차마다 다른 것이
       당연한데, 회차를 섞어 최소~최대를 재면 그 차이가 '모집단이 흔들렸다'로
       보인다. 실제로 전부 🔴로 떴다 — 실험이 아니라 **자가 틀린 것**이었다.
    2. **이 저장소가 금지한 표가 된다** — *"시간대·요일 구분을 섞어서 평균 낸
       표가 하나도 없다"*(THESIS 제출 전 체크리스트).
    """
    if df.empty:
        print("측정된 것이 없습니다.")
        return
    pops = [c.split("::", 1)[1] for c in df.columns if c.startswith("after::")]

    print("\n" + "=" * 96)
    print("모집단·회차별 결품 시간(h) — 씨앗 평균, 낮을수록 좋다")
    print("=" * 96)

    verdicts = {}
    for pop in pops:
        after, before = f"after::{pop}", f"before::{pop}"
        print(f"\n[{pop}]")
        for duration, part in df.groupby("duration"):
            pivot = part.pivot_table(index="z", columns="방법", values=after)

            # 🔴 litmus — **같은 회차 안에서** 재배치 전 값이 방법에 따라 움직이면 무효다.
            base = part[before]
            spread = float(base.max() - base.min())
            mark = "OK" if spread < 1e-6 else "🔴 무효"

            print(f"  < {duration} >   재배치 전 {base.min():.4f}"
                  f" (편차 {spread:.6f}) → {mark}")
            print("    " + pivot.round(4).to_string().replace("\n", "\n    "))
            if spread >= 1e-6:
                print("    ⚠️ 이 회차의 모집단이 흔들렸다 — 승부를 가리지 마라.")
                continue

            if pivot.shape[1] == 2:
                best = pivot.min()
                winner = best.idxmin()
                gap = float(best.max() - best.min())
                # 잡음 눈금 — 같은 (방법, z)의 씨앗 간 표준편차 중 가장 큰 것.
                noise = float(part.groupby(["방법", "z"])[after].std().max() or 0.0)
                snr = gap / noise if noise > 0 else float("inf")
                print(f"    → {winner} 우세 {gap * 3600:.0f}초"
                      f" (잡음 {noise * 3600:.0f}초 · 신호/잡음 {snr:.1f}"
                      f"{' ⚠️ 2 미만 = 잡음' if snr < 2 else ''})")
                verdicts.setdefault(pop, []).append((duration, winner, snr))

    print("\n" + "-" * 96)
    print("모집단별 판정 — 셋이 같은 승자를 가리켜야 결론이다")
    for pop, items in verdicts.items():
        names = {w for _d, w, _s in items}
        solid = [d for d, _w, s in items if s >= 2]
        print(f"  {pop:<20} {'·'.join(f'{d}:{w}' for d, w, _s in items)}"
              f"   → {'일치' if len(names) == 1 else '갈림'}"
              f" · 신호/잡음 2 이상: {len(solid)}/{len(items)}회차")

    print("\n" + "-" * 96)
    print("작업 대상 수 — 중심 통계가 후보 집합을 얼마나 바꾸는가 (회차별)")
    print(df.pivot_table(index=["duration", "z"], columns="방법",
                         values="candidates", aggfunc="mean").round(1).to_string())

    print("\n읽는 법")
    print("  · **모집단 셋이 같은 승자를 가리켜야** 결론이다. 갈리면 결론은")
    print("    '중심 통계는 무엇을 모집단으로 보느냐에 달렸다'이고 기본값을 바꿀 근거가 없다.")
    print("  · 차이가 씨앗 간 표준편차보다 작으면 잡음이다 — 신호대잡음 2를 넘겨야 한다.")
    print("  · MAD는 0이 되기 쉽다(대부분 0인 대여소). sigma=0이면 목표가 중앙값 그 자체다.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="중심 통계(평균 vs 중앙값)를 같은 자로 비교한다")
    parser.add_argument("--period", default="")
    parser.add_argument("--durations", default="_05_10,_10_15,_15_20")
    parser.add_argument("--day-type", default="weekday")
    parser.add_argument("--run-label", default="")
    parser.add_argument("--warmup-days", type=int, default=0)
    parser.add_argument("--z-grid", default="")
    parser.add_argument("--pop-z", type=float, default=1.99,
                        help="모집단을 정할 기준 z (기본 1.99 — 현행 운영값)")
    parser.add_argument("--seeds", default="42")
    parser.add_argument("--raw-mad", action="store_true",
                        help="MAD에 1.4826을 곱하지 않는다 (z의 뜻이 달라진다)")
    parser.add_argument("--out", default="")
    parser.add_argument("--from-csv", default="",
                        help="이미 잰 CSV를 다시 보고만 한다(재측정 없음)")
    args = parser.parse_args()

    if args.from_csv:
        report(pd.read_csv(args.from_csv))
        return 0

    from project_config import DEFAULT_PERIOD, DEFAULT_WARMUP_DAYS
    if not args.period:
        args.period = DEFAULT_PERIOD
    if not args.warmup_days:
        args.warmup_days = DEFAULT_WARMUP_DAYS

    args.durations = [d.strip() for d in args.durations.split(",") if d.strip()]
    args.seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    args.z_grid = ([float(z) for z in args.z_grid.split(",") if z.strip()]
                   if args.z_grid else list(Z_GRID))
    args.mad_scale = 1.0 if args.raw_mad else MAD_TO_SIGMA

    bc = load_baseline()
    df = measure(bc, args)
    report(df)

    if args.out and not df.empty:
        df.to_csv(args.out, index=False, encoding="utf-8-sig")
        print(f"\n저장: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
