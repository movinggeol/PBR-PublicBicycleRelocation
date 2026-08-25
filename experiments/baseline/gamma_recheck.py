"""`γ = 3000`을 다른 달에서 다시 확인한다.

현재 근거는 **25년 11월 한 달뿐**이다([EXPERIMENTS.md](../docs/분석/EXPERIMENTS.md) 4장).
그런데 γ에 대한 결과는 **비단조**여서(γ=2000이 γ=1000보다 나빴다) 두 점을 재고 사이를
보간할 수 없고, 계절이 다른 달에서 직접 돌려 봐야 한다.

    J = α·(군집별 수급 불균형)² + β·(군집 크기 편차)² + γ·(군집 내 거리)

γ를 올리면 군집이 지리적으로 뭉쳐 이동거리·소요시간이 줄지만, 수급이 맞지 않아
처리 대수가 준다. **편익(결품 감소)과 비용(거리·시간)을 함께** 봐야 멈출 곳이 보인다.

판정 규칙 (미리 정해 두고 시작한다):
  · 한 회차만 보고 판단하지 않는다 — 3회차 전부의 합계로 본다.
  · 편익은 결품 시간, 비용은 총 이동거리와 최장 소요시간으로 본다.
  · 개선률·목표 도달률은 쓰지 않는다(γ와 무관하게 target_qty가 분모라 흔들린다).

사용법:
    python experiments/baseline/gamma_recheck.py --period "26년 03월"
    python experiments/baseline/gamma_recheck.py --period "25년 11월" --gammas 1000,2000,3000,5000
"""
import argparse
import sys
from pathlib import Path

import pandas as pd
import pulp

sys.path.insert(0, str(Path(__file__).resolve().parent))

import baseline_compare as bc  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="γ 재확인")
    parser.add_argument("--period", default="26년 03월")
    parser.add_argument("--gammas", default="1000,2000,3000,5000")
    parser.add_argument("--duration", default="_05_10,_10_15,_15_20")
    parser.add_argument("--day-type", default="weekday", choices=["weekday", "holiday"])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--run-label", default="")
    parser.add_argument("--out", default="")
    args, _ = parser.parse_known_args()

    gammas = [float(g) for g in args.gammas.split(",") if g.strip()]
    durations = [d.strip() for d in args.duration.split(",") if d.strip()]
    args.day_type = bc.normalize_day_type(args.day_type)

    step1 = bc.load_step1()
    solver = bc.ilp_mod.build_solver()   # 파이프라인과 같은 솔버 설정
    net, st_info, warmup = bc.load_inputs(args.period, args.run_label,
                                          args.day_type, 0, "")

    rows = []
    for gamma in gammas:
        # adjust_clustering이 모듈 전역에서 읽으므로 여기서 바꿔 끼운다.
        step1.CLUSTER_GAMMA = gamma
        print(f"\n{'=' * 88}\nγ = {gamma:.0f}\n{'=' * 88}")

        for duration in durations:
            base = bc.build_candidates(net, st_info, duration, None, warmup, 0, step1)
            if base.empty:
                print(f"  [건너뜀] {duration}: 재배치 대상 없음")
                continue

            candidates, routes = bc.plan_with_clusters(base.copy(), step1, solver,
                                                       adjust=True, seed=args.seed)
            delta = bc.executed_delta(routes)
            before, after = bc.stockout(net, candidates, delta, duration)
            stats = bc.route_stats(routes)
            rows.append({"gamma": gamma, "duration": duration,
                         "stockout_before": before, "stockout_after": after,
                         **stats})
            print(f"  {duration}  처리 {stats['bikes']:>4d}대"
                  f"  {stats['km']:>6.1f}km  최장 {stats['max_min']:>5.1f}분"
                  f"  초과 {stats['over']}건  결품 {before:.2f}h → {after:.2f}h")

    if not rows:
        raise SystemExit("결과가 없습니다.")

    frame = pd.DataFrame(rows)
    total = frame.groupby("gamma").agg(
        처리대수=("bikes", "sum"),
        총이동km=("km", "sum"),
        최장분=("max_min", "max"),
        예산초과=("over", "sum"),
        결품합=("stockout_after", "sum"),
    ).reset_index()
    base_sum = frame.groupby("gamma")["stockout_before"].sum().iloc[0]
    total["결품감소"] = base_sum - total["결품합"]

    print(f"\n{'=' * 88}")
    print(f"3회차 합계 — 기간 {args.period} · {args.day_type} · 씨앗 {args.seed}")
    print("=" * 88)
    print(f"{'γ':>8}{'처리대수':>10}{'총이동km':>11}{'최장분':>9}{'예산초과':>10}"
          f"{'결품 합':>10}{'결품 감소':>11}")
    for row in total.itertuples():
        print(f"{row.gamma:>8.0f}{row.처리대수:>10d}{row.총이동km:>11.1f}"
              f"{row.최장분:>9.1f}{row.예산초과:>10d}{row.결품합:>10.2f}{row.결품감소:>11.2f}")

    print("\n읽는 법")
    print("  · γ를 올리면 군집이 뭉쳐 거리·시간이 줄지만 수급이 어긋나 처리 대수가 준다.")
    print("  · 결품 감소가 편익, 총이동km·최장분이 비용이다. 한쪽만 보면 언제나 극단이 답이 된다.")
    print("  · **곡선이 매끄러울 것이라고 가정하지 마라** — 과거에 γ=2000이 γ=1000보다 나빴다.")
    print("  · 한 달의 결과로 기본값을 바꾸지 마라. 다른 달에서도 같은 방향인지 확인할 것.")

    if args.out:
        frame.to_csv(args.out, index=False, encoding="utf-8")
        print(f"\n결과를 저장했습니다: {args.out}")


if __name__ == "__main__":
    main()
