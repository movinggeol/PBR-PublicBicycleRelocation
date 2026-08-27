"""`γ`(군집 거리 가중치)를 여러 달·여러 씨앗으로 훑어 기본값을 정한다.

`experiments/baseline/gamma_recheck.py`는 **한 달**을 재는 도구다. 이 스크립트는
그것으로 답이 안 나는 자리를 맡는다 — **γ는 비단조**여서(과거에 γ=2000이 γ=1000보다
나빴다) 한 달의 곡선을 믿으면 국소적인 요철을 기본값으로 굳히게 된다.

    J = α·(군집별 수급 불균형)² + β·(군집 크기 편차)² + γ·(군집 내 거리)

**왜 γ인가 — 예산 초과의 원인이 거리이기 때문이다.**
소요시간의 60~75%가 이동이고, 초과 군집은 45~56km로 정상(30km)보다 흩어져 있다
(상관 r=0.966). 경로(VRP)를 완벽하게 풀어도 흩어진 군집은 못 고친다 —
OR-Tools로 갭을 전부 회수해도 군집당 1.7분이었다
([EXPERIMENTS.md](../../docs/분석/EXPERIMENTS.md) 6장). **손댈 곳은 군집화다.**

판정 규칙 (미리 정해 두고 시작한다):
  · **여러 달·여러 씨앗의 합계로 본다.** 한 달에서 이긴 γ는 요철일 수 있다.
  · 편익은 **결품 시간**, 비용은 **최장 소요시간·총 이동거리**다.
  · 개선률·목표 도달률은 쓰지 않는다 — target_qty가 분모라 γ와 무관하게 흔들린다.
  · **이긴 조합 수를 함께 센다.** 평균만 보면 한 조합의 큰 차이에 끌려간다.

사용법:
    python experiments/params/gamma_sweep.py
    python experiments/params/gamma_sweep.py --gammas 3000,8000 --periods "25년 11월,26년 03월"
    python experiments/params/gamma_sweep.py --seeds 42,7,13 --day-type holiday
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "baseline"))

import baseline_compare as bc  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="γ 다월·다씨앗 스윕")
    parser.add_argument("--periods", default="25년 09월,25년 11월,26년 01월,26년 03월",
                        help="계절이 다른 달을 섞어라 — 여름·가을·겨울·환절기")
    parser.add_argument("--gammas", default="1000,3000,5000,8000,12000")
    parser.add_argument("--seeds", default="42,7")
    parser.add_argument("--duration", default="_05_10,_10_15,_15_20")
    parser.add_argument("--day-type", default="weekday", choices=["weekday", "holiday"])
    parser.add_argument("--out", default="")
    args, _ = parser.parse_known_args()

    periods = [p.strip() for p in args.periods.split(",") if p.strip()]
    gammas = [float(g) for g in args.gammas.split(",") if g.strip()]
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    durations = [d.strip() for d in args.duration.split(",") if d.strip()]
    day_type = bc.normalize_day_type(args.day_type)

    step1 = bc.load_step1()
    solver = bc.ilp_mod.build_solver()
    baseline_gamma = step1.CLUSTER_GAMMA

    total_runs = len(periods) * len(gammas) * len(seeds) * len(durations)
    print(f"γ {len(gammas)}개 × 달 {len(periods)}개 × 씨앗 {len(seeds)}개"
          f" × 회차 {len(durations)}개 = {total_runs}회 실행")
    print(f"현행 기본값 γ = {baseline_gamma:.0f}\n")

    rows = []
    for period in periods:
        net, st_info, warmup = bc.load_inputs(period, "", day_type, 0, "")
        for duration in durations:
            base = bc.build_candidates(net, st_info, duration, None, warmup, 0, step1)
            if base.empty:
                print(f"  [건너뜀] {period} {duration}: 재배치 대상 없음")
                continue
            for gamma in gammas:
                step1.CLUSTER_GAMMA = gamma
                for seed in seeds:
                    candidates, routes = bc.plan_with_clusters(
                        base.copy(), step1, solver, adjust=True, seed=seed)
                    delta = bc.executed_delta(routes)
                    before, after = bc.stockout(net, candidates, delta, duration)
                    stats = bc.route_stats(routes)
                    rows.append({"period": period, "duration": duration,
                                 "gamma": gamma, "seed": seed,
                                 "stockout_before": before, "stockout_after": after,
                                 **stats})
            done = [r for r in rows if r["period"] == period and r["duration"] == duration]
            best = min(done, key=lambda r: r["stockout_after"])
            print(f"  {period} {duration}: γ={best['gamma']:.0f}이 결품 최소"
                  f" ({best['stockout_after']:.3f}h)")

    step1.CLUSTER_GAMMA = baseline_gamma      # 원래대로 돌려 놓는다
    if not rows:
        raise SystemExit("결과가 없습니다.")

    frame = pd.DataFrame(rows)

    # ── 합계 ──────────────────────────────────────────────────────────
    agg = frame.groupby("gamma").agg(
        결품평균=("stockout_after", "mean"),
        최장분평균=("max_min", "mean"),
        최장분최대=("max_min", "max"),
        예산초과=("over", "sum"),
        총이동km=("km", "sum"),
        처리대수=("bikes", "sum"),
    ).reset_index()

    # ── 이긴 조합 수: (달·회차·씨앗)마다 결품이 가장 낮은 γ ──────────
    key = ["period", "duration", "seed"]
    winners = frame.loc[frame.groupby(key)["stockout_after"].idxmin()]
    win_count = winners["gamma"].value_counts()
    agg["이긴조합"] = agg["gamma"].map(win_count).fillna(0).astype(int)
    combos = len(winners)

    print(f"\n{'=' * 92}")
    print(f"합계 — {len(periods)}개월 × {len(durations)}회차 × 씨앗 {len(seeds)}개"
          f" ({combos}개 조합) · {day_type}")
    print("=" * 92)
    print(f"{'γ':>8}{'결품 평균':>11}{'최장분 평균':>13}{'최장분 최대':>13}"
          f"{'예산초과':>10}{'총이동km':>11}{'처리대수':>10}{'이긴조합':>10}")
    for row in agg.itertuples():
        mark = "  ← 현행" if row.gamma == baseline_gamma else ""
        print(f"{row.gamma:>8.0f}{row.결품평균:>11.3f}{row.최장분평균:>13.1f}"
              f"{row.최장분최대:>13.1f}{row.예산초과:>10d}{row.총이동km:>11.1f}"
              f"{row.처리대수:>10d}{row.이긴조합:>10d}{mark}")

    cur = agg[agg["gamma"] == baseline_gamma]
    best_stock = agg.loc[agg["결품평균"].idxmin()]
    best_over = agg.loc[agg["예산초과"].idxmin()]

    print(f"\n판정")
    if cur.empty:
        print(f"  · 현행 γ={baseline_gamma:.0f}이 후보에 없어 비교할 수 없다.")
    else:
        c = cur.iloc[0]
        print(f"  · 결품 최소: γ={best_stock.gamma:.0f}"
              f" ({best_stock.결품평균:.3f}h, 현행 {c.결품평균:.3f}h,"
              f" {(best_stock.결품평균 - c.결품평균) / c.결품평균 * 100:+.1f}%)")
        print(f"  · 예산초과 최소: γ={best_over.gamma:.0f}"
              f" ({int(best_over.예산초과)}건, 현행 {int(c.예산초과)}건)")
        print(f"  · 현행 γ={baseline_gamma:.0f}이 이긴 조합: {int(c.이긴조합)}/{combos}")

    print("\n읽는 법")
    print("  · **γ는 비단조다.** 곡선이 매끄러울 것이라 보고 중간값을 보간하지 마라.")
    print("  · 결품이 편익, 최장분·예산초과가 비용이다. 한쪽만 보면 극단이 답이 된다.")
    print("  · '이긴 조합'이 적은 γ는 평균이 좋아도 한두 조합에 끌려간 것이다.")
    print("  · 바꾸려면 **다른 달에서도 같은 방향**인지 확인할 것.")

    if args.out:
        frame.to_csv(args.out, index=False, encoding="utf-8")
        print(f"\n결과를 저장했습니다: {args.out}")


if __name__ == "__main__":
    main()
