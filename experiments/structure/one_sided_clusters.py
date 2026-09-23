"""조정 뒤 남는 **한쪽짜리 군집**(수거만·배송만)을 이웃에 붙이면 결품이 나아지나 (1.26.282).

왜 재나 — 원고 3.5.2가 *"K-Medoids가 1~2곳짜리 한쪽 군집을 만들고, 조정이 일부는 이웃에
흡수시키지만 남는다"* 고 적었다(2026-09-18 관찰, TODO). 한쪽짜리 군집에서는 ILP가 짝을 지을
상대가 없어 **빈 계획**을 낸다 — 그 군집의 대여소는 작업 대상으로 뽑혔는데 아무도 손대지 않는다.
고칠지(작은 군집을 강제로 합칠지), 8장 한계로만 둘지 정하지 않았고 **결품에 주는 영향은 재지
않았다.** 이 스크립트가 그것을 잰다.

무엇을 묻나
  ① 얼마나 자주 남나 — 회차 × 씨앗마다 한쪽짜리 군집 수 · 그 안의 대여소 · 그 계획량(대)
  ② 붙이면 결품이 나아지나 — 한쪽짜리 군집의 대여소를 **짝이 될 수 있는 가장 가까운 대여소**
     (부호가 반대이고 다른 군집에 있는 것)의 군집으로 옮기고, 같은 ILP·VRP로 다시 푼다
  ③ 대가 — 최장 소요시간 · 예산 초과 · 총 이동거리. 받은 군집이 커지므로 길어진다

판정 (재기 전에 정한다)
  · 편익은 **결품 시간**(같은 모집단 = 작업 대상 전체)이다. 개선률은 쓰지 않는다.
  · **여러 씨앗·세 회차의 합**으로 본다. 한 조합에서 이긴 것은 요철일 수 있다.
  · 결품이 조합 평균으로 0.01h 넘게 줄고 예산 초과가 늘지 않으면 → 파이프라인에 넣을 만하다.
    그렇지 않으면 8장 한계로 두고 이 표를 근거로 적는다.

붙이는 규칙이 하나뿐이라는 것은 한계다 — 군집 중심으로 붙이는 것, 조정 목적함수에 벌점을
넣는 것은 재지 않았다. 여기서 편익이 없으면 더 정교한 규칙도 크게 다르지 않을 것이라는 것이
판단의 근거다(한쪽짜리 군집의 계획량 자체가 상한이다).

    python experiments/structure/one_sided_clusters.py
    python experiments/structure/one_sided_clusters.py --periods "25년 11월,26년 03월" --seeds 42,7,13
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "baseline"))

import project_config  # noqa: E402,F401  — 콘솔 인코딩을 먼저 맞춘다(— · 이모지)
import baseline_compare as bc  # noqa: E402

CANONICAL = "2026-08-11 real"


def one_sided(clustered: pd.DataFrame) -> list:
    """수거만(모두 음수) 또는 배송만(모두 양수) 있는 군집 번호."""
    out = []
    for cluster, part in clustered.groupby("cluster"):
        qty = part["rebal_qty"]
        if (qty > 0).all() or (qty < 0).all():
            out.append(cluster)
    return out


def _km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def merge_one_sided(clustered: pd.DataFrame) -> pd.DataFrame:
    """한쪽짜리 군집의 대여소마다, **부호가 반대이고 다른 군집에 있는** 가장 가까운 대여소의
    군집으로 옮긴다. 짝이 될 상대 곁으로 보내야 ILP가 계획을 낼 수 있다."""
    frame = clustered.copy()
    lonely = set(one_sided(frame))
    if not lonely:
        return frame
    others = frame[~frame["cluster"].isin(lonely)]
    for idx, row in frame[frame["cluster"].isin(lonely)].iterrows():
        partners = others[np.sign(others["rebal_qty"]) == -np.sign(row["rebal_qty"])]
        if partners.empty:
            continue
        dist = _km(row["lat"], row["lon"], partners["lat"].to_numpy(), partners["lon"].to_numpy())
        frame.at[idx, "cluster"] = partners["cluster"].iloc[int(np.argmin(dist))]
    return frame


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--periods", default="25년 11월")
    parser.add_argument("--duration", default="_05_10,_10_15,_15_20")
    parser.add_argument("--seeds", default="42,7,13")
    parser.add_argument("--day-type", default="weekday", choices=["weekday", "holiday"])
    parser.add_argument("--run-label", default=CANONICAL, help=f"재고 스냅샷 (기본 정본 {CANONICAL})")
    args, _ = parser.parse_known_args()

    periods = [p.strip() for p in args.periods.split(",") if p.strip()]
    durations = [d.strip() for d in args.duration.split(",") if d.strip()]
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]

    step1 = bc.load_step1()
    project_config.align_day_type(args.day_type, bc.ilp_mod, bc.vrp_mod, bc.kpi_mod, step1)
    solver = bc.ilp_mod.build_solver()

    rows = []
    for period in periods:
        bc.reset_population_guard()
        net, st_info, warmup = bc.load_inputs(period, args.run_label, args.day_type, 0, "")
        for duration in durations:
            base = bc.build_candidates(net, st_info, duration, None, warmup, 0, step1)
            if base.empty:
                print(f"  [건너뜀] {period} {duration}: 작업 대상 없음")
                continue
            for seed in seeds:
                seen = {}

                def record(clustered, seen=seen):
                    lonely = one_sided(clustered)
                    part = clustered[clustered["cluster"].isin(lonely)]
                    seen.update(k=clustered["cluster"].nunique(), lonely=len(lonely),
                                stations=len(part), bikes=int(part["rebal_qty"].abs().sum()),
                                ids=part["station_id"].tolist())
                    return clustered

                cand_a, routes_a = bc.plan_with_clusters(base.copy(), step1, solver,
                                                         adjust=True, seed=seed, post=record)
                cand_b, routes_b = bc.plan_with_clusters(
                    base.copy(), step1, solver, adjust=True, seed=seed,
                    post=merge_one_sided)
                # 모집단은 둘 다 **작업 대상 전체**(`base`)다 — 붙여도 대상은 같다.
                delta_b = bc.executed_delta(routes_b)
                before, after_a = bc.stockout(net, base, bc.executed_delta(routes_a), duration)
                _, after_b = bc.stockout(net, base, delta_b, duration)
                # 붙인 대여소 가운데 **실제로 손댄 곳** — 받은 군집이 이미 같은 쪽으로 기울어
                # 있으면 붙여도 짝이 모자라 그대로 남는다.
                served = sum(1 for sid in seen["ids"] if delta_b.get(sid, 0) != 0)
                stat_a, stat_b = bc.route_stats(routes_a), bc.route_stats(routes_b)
                rows.append({
                    "period": period, "duration": duration, "seed": seed,
                    "K": seen["k"], "한쪽군집": seen["lonely"], "그안대여소": seen["stations"],
                    "그안계획량": seen["bikes"], "붙여서_손댄곳": served,
                    "결품_전": before, "결품_현행": after_a, "결품_붙임": after_b,
                    "옮김_현행": stat_a["bikes"], "옮김_붙임": stat_b["bikes"],
                    "최장_현행": stat_a["max_min"], "최장_붙임": stat_b["max_min"],
                    "초과_현행": stat_a["over"], "초과_붙임": stat_b["over"],
                    "km_현행": stat_a["km"], "km_붙임": stat_b["km"],
                })
                r = rows[-1]
                print(f"  {period} {duration} 씨앗 {seed:>2}: K={r['K']:>2} · 한쪽 {r['한쪽군집']}개"
                      f"({r['그안대여소']}곳 · {r['그안계획량']}대 → 붙이면 {r['붙여서_손댄곳']}곳 손댐) · 결품 {r['결품_현행']:.3f} → "
                      f"{r['결품_붙임']:.3f}h · 옮김 {r['옮김_현행']} → {r['옮김_붙임']}대 · "
                      f"최장 {r['최장_현행']:.0f} → {r['최장_붙임']:.0f}분 · 초과 {r['초과_현행']} → {r['초과_붙임']}")

    if not rows:
        print("결과가 없습니다.")
        return 1
    frame = pd.DataFrame(rows)
    has = frame[frame["한쪽군집"] > 0]
    gain = (frame["결품_현행"] - frame["결품_붙임"])
    print("\n" + "=" * 88)
    print(f"조합 {len(frame)}개 (기간 {len(periods)} × 회차 {len(durations)} × 씨앗 {len(seeds)})"
          f" · 스냅샷 '{args.run_label}' · {args.day_type}")
    print(f"  한쪽짜리 군집이 남은 조합   {len(has)}/{len(frame)}"
          f" · 군집 {int(frame['한쪽군집'].sum())}개 · 대여소 {int(frame['그안대여소'].sum())}곳"
          f" · 계획량 {int(frame['그안계획량'].sum())}대"
          f" · 붙이면 손대는 곳 {int(frame['붙여서_손댄곳'].sum())}곳")
    print(f"  결품 (조합 평균)            현행 {frame['결품_현행'].mean():.4f}h → 붙임 "
          f"{frame['결품_붙임'].mean():.4f}h  (차 {-gain.mean():+.4f}h ·"
          f" 나아진 조합 {int((gain > 1e-9).sum())} · 나빠진 조합 {int((gain < -1e-9).sum())})")
    print(f"  옮긴 대수 합                {int(frame['옮김_현행'].sum())} → {int(frame['옮김_붙임'].sum())}대")
    print(f"  예산 초과 합                {int(frame['초과_현행'].sum())} → {int(frame['초과_붙임'].sum())}건")
    print(f"  최장 소요 평균              {frame['최장_현행'].mean():.1f} → {frame['최장_붙임'].mean():.1f}분")
    print(f"  총 이동거리 합              {frame['km_현행'].sum():.1f} → {frame['km_붙임'].sum():.1f}km")
    print("\n판정 기준(미리 정함): 결품이 조합 평균으로 0.01h 넘게 줄고 예산 초과가 늘지 않으면 넣을 만하다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
