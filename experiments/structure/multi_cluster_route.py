"""차량 1대가 군집 여러 개를 이어 도는 구조는 값어치가 있나 — 차고지 왕복을 줄여 본다.

현행 설계는 **차량 1대 = 군집 1개 + depot 복귀**다(1.13.2 사용자 결정,
docs/구현/FLEET.md). 여러 군집을 순차 처리하는 구조는 그때 검토하고 채택하지
않았는데, 최근 회차당 투입 대수 실험에서 **차고지 왕복이 총 이동거리의 상당
부분을 차지한다**는 것이 드러나 재검토할 값어치가 생겼다
(docs/기록/TODO.md P4-2, docs/연구/초안/6장_실험_성능평가.md 6.12절).

    현행   [depot → 군집A 작업 → depot]  +  [depot → 군집B 작업 → depot]
    연쇄   [depot → 군집A 작업 → 군집B 작업 → depot]

**연쇄가 얻는 것**은 depot 왕복 한 번(A→depot→B가 A→B로 줄어든다)과 차량 한 대다.
**잃는 것**은 그 차량의 소요시간이다 — 두 군집 몫을 혼자 하므로 시간 예산
(기본 120분)을 넘기기 쉬워진다. 이 실험은 그 맞교환의 크기를 잰다.

**같은 조건으로 맞춘 것**
  · 같은 후보·같은 군집화·같은 ILP 계획 (연쇄는 **경로 단계만** 바꾼다)
  · 같은 경로 엔진 — 양쪽 다 파이프라인의 `vrp.greedy_route()`를 그대로 부른다
  · 같은 거리(Haversine)·같은 적재 용량·같은 작업시간

⚠️ **연쇄는 군집을 다시 나누지 않는다.** ILP가 군집마다 총 pick = 총 drop을
맞춰 두었으므로, 두 군집의 노드를 한 통에 부어도 수급은 그대로 맞는다. 즉
"군집 경계를 지우는" 실험이 아니라 **"차량이 depot에 안 들르고 다음 군집으로
바로 간다"** 는 실험이다 — 군집화 자체를 바꾸는 것은 별개 실험이다.

사용법:
    python experiments/structure/multi_cluster_route.py --period "25년 11월"
    python experiments/structure/multi_cluster_route.py --period "26년 03월" --chain-size 3
    python experiments/structure/multi_cluster_route.py --pair-mode smallest --out result.csv
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments" / "baseline"))

import baseline_compare as bc  # noqa: E402

from project_config import (  # noqa: E402
    DEFAULT_PERIOD, DEFAULT_WARMUP_DAYS, DEPOT_LAT, DEPOT_LON,
    TIME_BUDGET_MINUTES, VEHICLES_PER_ROUND,
)

haversine_km = bc.ilp_mod.haversine_km


def cluster_nodes(moves, coords, cluster):
    """한 군집의 ILP 계획을 greedy_route가 받는 노드 사전으로 만든다.

    파이프라인(`vrp.run_vrp_plan`)이 하는 것과 **같은 방식**이다 — 여기서 다르게
    만들면 비교가 성립하지 않는다.
    """
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
    return nodes


def centroid(nodes):
    """군집 노드의 무게중심 — 군집끼리 얼마나 가까운지 재는 데만 쓴다."""
    lats = [v["lat"] for v in nodes.values()]
    lons = [v["lon"] for v in nodes.values()]
    return sum(lats) / len(lats), sum(lons) / len(lons)


def make_chains(pools, size, mode):
    """군집들을 `size`개씩 묶는다. 반환: [[군집id, ...], ...]

    mode='nearest'  — 아직 안 묶인 군집 중 아무거나 집어, 그 중심에서 가장 가까운
                      군집들을 붙인다. 지리적으로 인접한 것끼리 묶인다.
    mode='smallest' — 작업량(자전거 수)이 적은 군집부터 집는다. TODO의 표현
                      ("인접한 **소규모** 군집 두 개")에 가장 가까운 해석이다.
                      집은 뒤 붙이는 것은 nearest와 같이 가까운 순이다.

    ⚠️ 어느 쪽도 최적 묶기가 아니다 — 최적 묶기는 그 자체로 또 하나의 조합
    문제다. 이 실험의 목적은 "연쇄가 값어치 있나"를 보는 것이지 최적 묶기를
    찾는 것이 아니므로, 단순한 규칙 두 개로 **하한**을 잰다.
    """
    centers = {c: centroid(n) for c, n in pools.items()}
    loads = {c: sum(v["qty"] for (sid, t), v in n.items() if t == "pick")
             for c, n in pools.items()}

    remaining = set(pools)
    chains = []
    while remaining:
        if mode == "smallest":
            seed = min(remaining, key=lambda c: (loads[c], c))
        else:
            seed = min(remaining)          # 재현성을 위해 결정적으로 고른다
        remaining.discard(seed)

        chain = [seed]
        while len(chain) < size and remaining:
            here = centers[chain[-1]]
            nearest = min(remaining,
                          key=lambda c: (haversine_km(here[0], here[1],
                                                      centers[c][0], centers[c][1]), c))
            chain.append(nearest)
            remaining.discard(nearest)
        chains.append(chain)
    return chains


def route_frame(rows):
    """greedy_route 결과 목록을 DataFrame으로. 빈 경우도 컬럼을 맞춰 준다."""
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=["cluster", "action", "qty", "distance_km",
                                     "cum_sec", "from_id", "to_id"])
    return frame


def route_minutes(rows):
    """greedy_route 결과 한 건의 소요시간(분). 빈 경로는 0."""
    return max((r["cum_sec"] for r in rows), default=0.0) / 60


def make_chains_within_budget(pools, moves, coords, size, mode, budget_min):
    """예산을 지키는 선에서만 묶는다 — 묶어 보고 넘치면 되돌린다.

    위의 `make_chains`는 예산을 보지 않고 무조건 묶으므로, 거리는 크게 줄지만
    소요시간이 예산을 넘긴다(실측: 초과 3건 → 22건). 실제로 채택하려면
    **예산 안에 들어오는 묶음만** 받아야 한다.

    규칙은 단순하다 — 후보 군집을 하나 집고, 가까운 것부터 붙여 보되 붙인
    결과가 예산을 넘으면 그 붙이기를 취소하고 그 묶음을 닫는다. 최적 묶기가
    아니라 **탐욕적 하한**이다(위 make_chains의 주석과 같은 이유).
    """
    centers = {c: centroid(n) for c, n in pools.items()}
    loads = {c: sum(v["qty"] for (sid, t), v in n.items() if t == "pick")
             for c, n in pools.items()}

    remaining = set(pools)
    chains = []
    while remaining:
        if mode == "smallest":
            seed = min(remaining, key=lambda c: (loads[c], c))
        else:
            seed = min(remaining)
        remaining.discard(seed)

        chain = [seed]
        while len(chain) < size and remaining:
            here = centers[chain[-1]]
            nearest = min(remaining,
                          key=lambda c: (haversine_km(here[0], here[1],
                                                      centers[c][0], centers[c][1]), c))
            # 붙여 보고 예산을 넘으면 취소한다. greedy_route는 노드 사전을
            # 소모하므로 시험용으로 매번 새로 만들어 넣는다.
            trial = {}
            for cluster in chain + [nearest]:
                trial.update(cluster_nodes(moves, coords, cluster))
            if route_minutes(bc.quiet(bc.vrp_mod.greedy_route, trial, -1)) > budget_min:
                break
            chain.append(nearest)
            remaining.discard(nearest)
        chains.append(chain)
    return chains


def summarize(routes, label):
    """이동거리·차고지 왕복 비중·최장 소요·예산 초과·처리 대수."""
    if routes.empty:
        return {"방법": label, "차량": 0, "총거리_km": 0.0, "차고지_km": 0.0,
                "차고지_비중": 0.0, "최장_분": 0.0, "예산초과": 0, "대수": 0}

    minutes = routes.groupby("cluster")["cum_sec"].max() / 60

    # 차고지 왕복 = depot에서 나가는 첫 구간 + depot으로 돌아오는 구간.
    # 6.12절이 인용하는 "차고지 왕복 비중"과 같은 정의다.
    depot_out = routes[routes["from_id"] == bc.vrp_mod.DEPOT_ID]["distance_km"].sum()
    depot_back = routes[routes["action"] == "return"]["distance_km"].sum()
    total_km = float(routes["distance_km"].sum())
    depot_km = float(depot_out + depot_back)

    return {
        "방법": label,
        "차량": int(routes["cluster"].nunique()),
        "총거리_km": round(total_km, 2),
        "차고지_km": round(depot_km, 2),
        "차고지_비중": round(depot_km / total_km * 100, 1) if total_km else 0.0,
        "최장_분": round(float(minutes.max()), 1),
        "예산초과": int((minutes > TIME_BUDGET_MINUTES).sum()),
        "대수": int(routes[routes["action"] == "pick"]["qty"].sum()),
    }


def run_duration(net, st_info, warmup, duration, args, step1, solver):
    print("\n" + "=" * 92)
    print(f"< {duration} >  기간 {args.period} · {args.day_type} · 씨앗 {args.seed}"
          f" · 묶음 {args.chain_size}개 ({args.pair_mode})")
    print("=" * 92)

    base = bc.build_candidates(net, st_info, duration, None, warmup,
                               args.warmup_days, step1)
    if base.empty:
        print("[건너뜀] 재배치 대상이 없습니다 (Pick 또는 Drop 후보 없음)")
        return []

    # 군집화·ILP는 파이프라인과 **완전히 같다** — 바꾸는 것은 경로 단계뿐이다.
    clustered = bc.quiet(step1.make_clustering, base.copy(),
                         random_state=args.seed).copy()
    clustered = bc.quiet(step1.adjust_clustering, clustered).copy()

    frame = clustered.copy()
    frame["drop_qty"] = frame["rebal_qty"].clip(lower=0).astype(int)
    frame["pick_qty"] = (-frame["rebal_qty"].clip(upper=0)).astype(int)

    moves = []
    for cluster in frame["cluster"].unique():
        for row in bc.quiet(bc.ilp_mod.solve_cluster_moves,
                            frame[frame["cluster"] == cluster], solver):
            moves.append({"cluster": cluster, **row})
    moves = pd.DataFrame(moves)
    if moves.empty:
        print("[건너뜀] ILP가 옮길 자전거를 찾지 못했습니다")
        return []

    coords = clustered.set_index("station_id")[["lat", "lon"]]
    pools = {int(c): cluster_nodes(moves, coords, c)
             for c in sorted(moves["cluster"].unique())}

    print(f"작업 대상 {len(base)}곳 · 군집 {len(pools)}개 · "
          f"ILP 이동 {int(moves['qty'].sum())}대")

    # ---- 현행: 군집 하나당 차량 하나 (노드 사전을 소모하므로 매번 새로 만든다)
    current = []
    for cluster in pools:
        nodes = cluster_nodes(moves, coords, cluster)
        current.extend(bc.quiet(bc.vrp_mod.greedy_route, nodes, cluster))
    current = route_frame(current)

    # ---- 연쇄: 묶인 군집들의 노드를 한 통에 부어 차량 하나가 처리한다
    def route_chains(chains, label):
        rows = []
        for index, chain in enumerate(chains):
            merged = {}
            for cluster in chain:
                merged.update(cluster_nodes(moves, coords, cluster))
            rows.extend(bc.quiet(bc.vrp_mod.greedy_route, merged, index))
        return summarize(route_frame(rows), label)

    naive = route_chains(make_chains(pools, args.chain_size, args.pair_mode),
                         f"연쇄 ({args.chain_size}개씩, 예산 무시)")
    guarded_chains = make_chains_within_budget(pools, moves, coords, args.chain_size,
                                               args.pair_mode, TIME_BUDGET_MINUTES)
    guarded = route_chains(guarded_chains,
                           f"연쇄 (예산 {TIME_BUDGET_MINUTES:.0f}분 지킴)")

    rows = [summarize(current, "현행 (1대 = 군집 1개)"), naive, guarded]
    for row in rows:
        row["duration"] = duration

    print(f"\n{'방법':<26}{'차량':>5}{'총거리 km':>11}{'차고지 km':>11}"
          f"{'차고지 비중':>12}{'최장 분':>9}{'예산초과':>9}{'처리 대수':>10}")
    for row in rows:
        print(f"{row['방법']:<26}{row['차량']:>5d}{row['총거리_km']:>11.2f}"
              f"{row['차고지_km']:>11.2f}{row['차고지_비중']:>11.1f}%"
              f"{row['최장_분']:>9.1f}{row['예산초과']:>9d}{row['대수']:>10d}")

    before = rows[0]
    for after in rows[1:]:
        km_gain = before["총거리_km"] - after["총거리_km"]
        km_pct = km_gain / before["총거리_km"] * 100 if before["총거리_km"] else 0.0
        print(f"  {after['방법']:<26} 거리 {km_gain:+7.2f}km ({km_pct:+5.1f}%) · "
              f"차량 {after['차량'] - before['차량']:+d}대 · "
              f"최장 {after['최장_분'] - before['최장_분']:+6.1f}분 · "
              f"예산초과 {after['예산초과'] - before['예산초과']:+d}건")

        if after["대수"] != before["대수"]:
            print(f"  ⚠️ 처리 대수가 달라졌습니다 ({before['대수']} → {after['대수']}) — "
                  "같은 ILP 계획을 썼으므로 같아야 정상입니다. 확인이 필요합니다.")

    merged_count = sum(1 for c in guarded_chains if len(c) > 1)
    print(f"  (예산을 지키며 묶인 것은 {len(guarded_chains)}개 묶음 중 {merged_count}개)")

    # 왜 못 묶는가 — 현행 군집 하나가 이미 예산의 몇 %를 쓰고 있는지 본다.
    per_cluster = (current.groupby("cluster")["cum_sec"].max() / 60).sort_values()
    share = per_cluster / TIME_BUDGET_MINUTES * 100
    room = (per_cluster < TIME_BUDGET_MINUTES / 2).sum()
    print(f"  현행 군집 소요: 중앙 {per_cluster.median():.0f}분"
          f"(예산의 {share.median():.0f}%) · 최소 {per_cluster.min():.0f}분"
          f" · 최대 {per_cluster.max():.0f}분")
    print(f"  → 예산의 절반(={TIME_BUDGET_MINUTES / 2:.0f}분) 미만인 군집은"
          f" {len(per_cluster)}개 중 {room}개뿐이다 — 둘을 붙일 여유가 여기서 갈린다.")

    return rows


def main():
    parser = argparse.ArgumentParser(
        description="차량 1대가 여러 군집을 이어 도는 구조의 값어치 측정")
    parser.add_argument("--period", default=DEFAULT_PERIOD)
    parser.add_argument("--duration", default="_05_10,_10_15,_15_20")
    parser.add_argument("--day-type", default="weekday", choices=["weekday", "holiday"])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--run-label", default="")
    parser.add_argument("--warmup-days", type=int, default=DEFAULT_WARMUP_DAYS)
    parser.add_argument("--warmup-period", default="")
    parser.add_argument("--chain-size", type=int, default=2,
                        help="차량 한 대가 맡을 군집 수 (기본 2)")
    parser.add_argument("--pair-mode", default="nearest", choices=["nearest", "smallest"],
                        help="묶는 규칙 — nearest는 가까운 것끼리, smallest는 작은 것부터")
    parser.add_argument("--out", default="")
    args, _ = parser.parse_known_args()

    if args.chain_size < 2:
        raise SystemExit("--chain-size는 2 이상이어야 합니다 (1이면 현행과 같습니다).")

    args.day_type = bc.normalize_day_type(args.day_type)
    durations = [d.strip() for d in args.duration.split(",") if d.strip()]

    step1 = bc.load_step1()
    solver = bc.ilp_mod.build_solver()
    net, st_info, warmup = bc.load_inputs(args.period, args.run_label, args.day_type,
                                          args.warmup_days, args.warmup_period)

    rows = []
    for duration in durations:
        rows.extend(run_duration(net, st_info, warmup, duration, args, step1, solver))

    if not rows:
        raise SystemExit("결과가 없습니다.")

    frame = pd.DataFrame(rows)

    print("\n" + "=" * 92)
    print("합계 (세 회차)")
    print("=" * 92)
    for label, part in frame.groupby("방법", sort=False):
        print(f"{label:<24} 차량 {int(part['차량'].sum()):>3d}대 · "
              f"총거리 {part['총거리_km'].sum():>8.2f}km · "
              f"차고지 {part['차고지_km'].sum():>7.2f}km"
              f"({part['차고지_km'].sum() / part['총거리_km'].sum() * 100:>4.1f}%) · "
              f"예산초과 {int(part['예산초과'].sum()):>2d}건")

    print("\n읽는 법")
    print("  · **군집화·ILP는 양쪽이 완전히 같다.** 다른 것은 차량이 군집 사이에서")
    print("    depot에 들르느냐 뿐이라, 차이가 곧 연쇄 구조의 몫이다.")
    print("  · 거리가 줄고 차량이 주는 대신 **최장 소요시간이 는다** — 예산 초과가")
    print("    늘었다면 그것이 이 구조의 비용이다.")
    print("  · 묶는 규칙(nearest/smallest)은 단순 규칙이라 **최적 묶기가 아니다.**")
    print("    여기서 나온 이득은 하한이다 — 더 잘 묶으면 더 줄어들 수 있다.")
    print(f"  · 시간 예산 {TIME_BUDGET_MINUTES:.0f}분·회차당 차량 상한"
          f" {VEHICLES_PER_ROUND}대 기준이다.")

    if args.out:
        frame.to_csv(args.out, index=False, encoding="utf-8")
        print(f"\n결과를 저장했습니다: {args.out}")


if __name__ == "__main__":
    main()
