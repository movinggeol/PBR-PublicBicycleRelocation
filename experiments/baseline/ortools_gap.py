"""greedy VRP는 최적해에서 얼마나 떨어져 있나 — OR-Tools로 갭을 잰다.

현행 경로는 greedy 휴리스틱이고 **최적성 보장이 없다**. 논문 심사에서 가장 먼저
나오는 지적이므로, 대체하지 말고 **갭을 재서 근거로 삼는다**
([RELATED_WORK.md](../docs/연구/RELATED_WORK.md) 4장).

    갭이 작다 → greedy를 쓴 것이 정당화된다
    갭이 크다 → 한계가 정량화된다

어느 쪽이든 답이 된다.

**같은 조건으로 맞춘 것**
  · 같은 노드 집합 (ILP가 정한 대여소별 pick/drop 수량)
  · 같은 적재 용량(10대) — 대여소를 나눠 처리하는 것도 양쪽 모두 허용한다
    (자전거 1대 단위 노드. 안 쪼개면 실행 불가능한 인스턴스가 생긴다 — split_nodes 참고)
  · 같은 거리(Haversine)
  · **양쪽 다 depot으로 돌아온다** (2026-08-27 정정)

⚠️ **한 번 어긋났던 자리다 — 고치면 반드시 함께 고쳐라.**
1.19.1이 `greedy_route()`에 depot 복귀를 넣었는데 이 스크립트가 따라가지 않아,
**복귀를 포함한 greedy와 복귀를 뺀 OR-Tools를 견주고 있었다.** 갭이 9.7% →
51~59%로 뛰어 마치 경로가 크게 나빠진 것처럼 보였다. 경로는 그대로였고
**재는 자가 어긋난 것이다.**

사용법:
    python experiments/baseline/ortools_gap.py --period "25년 11월" --duration "_05_10"
    python experiments/baseline/ortools_gap.py --period "25년 11월" --limit-sec 30

설치: pip install ortools   (파이프라인 의존성이 아니다 — 이 실험 전용)
"""
import argparse
import sys
from pathlib import Path

import pandas as pd
import pulp

sys.path.insert(0, str(Path(__file__).resolve().parent))

import baseline_compare as bc  # noqa: E402

try:
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
except ImportError:  # pragma: no cover
    raise SystemExit("ortools가 없습니다. `pip install ortools` 후 다시 실행하세요.")

from project_config import (  # noqa: E402
    DEPOT_LAT, DEPOT_LON, TIME_BUDGET_MINUTES, VEHICLE_CAPACITY, VEHICLE_SPEED_KMPH,
)

haversine_km = bc.ilp_mod.haversine_km


def split_nodes(moves, coords, cluster, chunk=1):
    """ILP 계획을 (좌표, 적재 증감) 노드 목록으로 편다.

    **자전거 `chunk`대마다 노드 하나로 쪼갠다(기본 1대).** 대여소 하나를 노드 하나로
    두면 '전부 아니면 전무' 방문이 되어 **실행 불가능한 인스턴스가 생긴다.**
    예: 적재 10대에 pick {10, 10, 7}, drop {9, 9, 9}이면 어떤 순서로도 적재량이
    0~10을 벗어난다. 반면 현행 greedy는 한 대여소를 나눠 처리하고 나중에 다시 올 수
    있으므로 이런 제약이 없다 — **조건을 맞추려면 쪼개야 한다.**

    같은 대여소에서 나온 노드끼리는 거리가 0이라 쪼개도 최적 거리는 달라지지 않는다.
    노드 수만 늘어난다(회차당 60~80개 수준).
    """
    part = moves[moves["cluster"] == cluster]
    nodes = []
    for sid, qty in part.groupby("pick_station_id")["qty"].sum().items():
        remaining = int(qty)
        while remaining > 0:
            take = min(remaining, chunk)
            nodes.append((coords.loc[sid, "lat"], coords.loc[sid, "lon"], take))
            remaining -= take
    for sid, qty in part.groupby("drop_station_id")["qty"].sum().items():
        remaining = int(qty)
        while remaining > 0:
            take = min(remaining, chunk)
            nodes.append((coords.loc[sid, "lat"], coords.loc[sid, "lon"], -take))
            remaining -= take
    return nodes


def solve_ortools(nodes, limit_sec):
    """depot에서 출발해 모든 노드를 처리하고 **depot으로 돌아오는** 최소 이동거리 경로.

    ⚠️ **2026-08-27 정정 — 여기가 틀려서 갭이 5배로 부풀어 있었다.**

    1.19.1 이전의 `greedy_route()`는 작업을 마치면 그 자리에서 멈췄고, 그래서 이
    함수도 '비용 0짜리 가상 종점'을 두어 복귀 없는 경로를 풀었다. **양쪽을 맞춘
    것이었다.**

    그런데 1.19.1이 greedy에 depot 복귀를 넣었다. 이 함수는 따라가지 않았다.
    그 결과 **복귀를 포함한 greedy와 복귀를 뺀 OR-Tools를 견주게 됐고**, 갭이
    9.7% → 51~59%로 뛰었다. 경로가 나빠진 것이 아니라 **재는 자가 어긋난 것이다.**

    지금은 종점을 depot(0번 노드)으로 두어 양쪽 모두 복귀를 포함한다.
    """
    points = [(DEPOT_LAT, DEPOT_LON, 0)] + list(nodes)
    size = len(points)

    def meters(i, j):
        return int(round(haversine_km(points[i][0], points[i][1],
                                      points[j][0], points[j][1]) * 1000))

    # 출발도 도착도 depot(0번) — 복귀를 포함한 순환 경로다.
    manager = pywrapcp.RoutingIndexManager(size, 1, 0)
    routing = pywrapcp.RoutingModel(manager)

    transit = routing.RegisterTransitCallback(
        lambda a, b: meters(manager.IndexToNode(a), manager.IndexToNode(b)))
    routing.SetArcCostEvaluatorOfAllVehicles(transit)

    demand = routing.RegisterUnaryTransitCallback(
        lambda a: points[manager.IndexToNode(a)][2])
    routing.AddDimensionWithVehicleCapacity(
        demand, 0, [VEHICLE_CAPACITY], True, "Capacity")

    # 적재 제약이 있는 문제에서는 초기해 전략에 따라 해를 아예 못 찾는다.
    # 하나가 실패하면 다음 전략으로 넘어간다.
    strategies = (
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION,
        routing_enums_pb2.FirstSolutionStrategy.LOCAL_CHEAPEST_INSERTION,
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC,
        routing_enums_pb2.FirstSolutionStrategy.AUTOMATIC,
    )
    for strategy in strategies:
        params = pywrapcp.DefaultRoutingSearchParameters()
        params.first_solution_strategy = strategy
        params.local_search_metaheuristic = (
            routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH)
        params.time_limit.FromSeconds(limit_sec)

        solution = routing.SolveWithParameters(params)
        if solution is not None:
            return solution.ObjectiveValue() / 1000.0     # km
    return None


def main():
    parser = argparse.ArgumentParser(description="greedy VRP의 최적성 갭 측정")
    parser.add_argument("--period", default="25년 11월")
    parser.add_argument("--duration", default="_05_10,_10_15,_15_20")
    parser.add_argument("--day-type", default="weekday", choices=["weekday", "holiday"])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--run-label", default="")
    parser.add_argument("--limit-sec", type=int, default=15, help="클러스터당 탐색 시간(초)")
    parser.add_argument("--chunk", type=int, default=1,
                        help="노드 하나가 담는 자전거 대수 (기본 1 — 쪼갤수록 정확하지만 느리다)")
    parser.add_argument("--out", default="")
    args, _ = parser.parse_known_args()

    args.day_type = bc.normalize_day_type(args.day_type)
    durations = [d.strip() for d in args.duration.split(",") if d.strip()]

    step1 = bc.load_step1()
    solver = bc.ilp_mod.build_solver()   # 파이프라인과 같은 솔버 설정
    net, st_info, warmup = bc.load_inputs(args.period, args.run_label,
                                          args.day_type, 0, "")

    rows = []
    for duration in durations:
        base = bc.build_candidates(net, st_info, duration, None, warmup, 0, step1)
        if base.empty:
            print(f"[건너뜀] {duration}: 재배치 대상 없음")
            continue

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
            continue

        coords = clustered.set_index("station_id")[["lat", "lon"]]

        print(f"\n{'=' * 88}\n[{duration}]  greedy vs OR-Tools (클러스터당 {args.limit_sec}초)\n{'=' * 88}")
        print(f"{'클러스터':>8}{'노드':>6}{'greedy km':>12}{'OR-Tools km':>13}"
              f"{'갭':>9}{'절감 km':>10}{'greedy 분':>11}{'예산':>7}")

        for cluster in sorted(moves["cluster"].unique()):
            nodes = split_nodes(moves, coords, cluster, args.chunk)

            # greedy — 파이프라인과 같은 함수
            pool = {}
            part = moves[moves["cluster"] == cluster]
            for sid, qty in part.groupby("pick_station_id")["qty"].sum().items():
                pool[(sid, "pick")] = {"qty": int(qty), "lat": coords.loc[sid, "lat"],
                                       "lon": coords.loc[sid, "lon"]}
            for sid, qty in part.groupby("drop_station_id")["qty"].sum().items():
                pool[(sid, "drop")] = {"qty": int(qty), "lat": coords.loc[sid, "lat"],
                                       "lon": coords.loc[sid, "lon"]}
            route = pd.DataFrame(bc.quiet(bc.vrp_mod.greedy_route, pool, cluster))
            if route.empty:
                continue
            greedy_km = float(route["distance_km"].sum())
            greedy_min = float(route["cum_sec"].max()) / 60

            # greedy_km·greedy_min은 **복귀를 포함한** 값이다(1.19.1).
            # 그 전에는 여기서 복귀 누락분을 따로 셌다 — 이제 셀 것이 없다.
            back_km = 0.0
            back_min = 0.0

            best_km = solve_ortools(nodes, args.limit_sec)
            if best_km is None:
                # 해를 못 찾은 클러스터는 갭을 뺀다 — 억지로 채우면 평균이 거짓말을 한다.
                print(f"{cluster:>8d}{len(nodes):>6d}{greedy_km:>12.2f}"
                      f"{'해 못 찾음':>13}{'':>9}{'':>10}{greedy_min:>11.1f}"
                      f"{'':>7}")
                rows.append({"duration": duration, "cluster": int(cluster),
                             "nodes": len(nodes), "greedy_km": greedy_km,
                             "ortools_km": None, "gap_pct": None,
                             "return_km": back_km, "greedy_min": greedy_min,
                             "with_return_min": greedy_min + back_min})
                continue

            gap = (greedy_km - best_km) / best_km * 100 if best_km else float("nan")
            rows.append({"duration": duration, "cluster": int(cluster),
                         "nodes": len(nodes), "greedy_km": greedy_km,
                         "ortools_km": best_km, "gap_pct": gap,
                         "return_km": back_km, "greedy_min": greedy_min,
                         "with_return_min": greedy_min + back_min})
            over = "초과" if greedy_min > TIME_BUDGET_MINUTES else ""
            print(f"{cluster:>8d}{len(nodes):>6d}{greedy_km:>12.2f}{best_km:>13.2f}"
                  f"{gap:>8.1f}%{greedy_km - best_km:>10.2f}{greedy_min:>11.1f}"
                  f"{over:>7}")

    if not rows:
        raise SystemExit("결과가 없습니다.")

    frame = pd.DataFrame(rows)
    print(f"\n{'=' * 88}")
    print("요약")
    print("=" * 88)
    for duration, part in frame.groupby("duration", sort=False):
        over_now = (part["greedy_min"] > TIME_BUDGET_MINUTES).sum()
        over_with = (part["with_return_min"] > TIME_BUDGET_MINUTES).sum()
        solved = part.dropna(subset=["gap_pct"])
        missing = len(part) - len(solved)
        note = f" (해 못 찾은 클러스터 {missing}개 제외)" if missing else ""
        print(f"[{duration}]  갭 평균 {solved['gap_pct'].mean():.1f}%"
              f" · 최대 {solved['gap_pct'].max():.1f}%"
              f" · 총거리 greedy {solved['greedy_km'].sum():.1f}km"
              f" vs OR-Tools {solved['ortools_km'].sum():.1f}km{note}")
        saved = solved['greedy_km'].sum() - solved['ortools_km'].sum()
        big = (solved['gap_pct'] > 20).sum()
        big_saved = (solved[solved['gap_pct'] > 20]['greedy_km'].sum()
                     - solved[solved['gap_pct'] > 20]['ortools_km'].sum())
        share = big_saved / saved * 100 if saved else 0.0
        print(f"          절감 여지 {saved:.1f}km · 예산 초과 {over_now}건")
        print(f"          갭 20%↑ 클러스터 {big}개가 절감분의 {share:.0f}%를 차지")

    print("\n읽는 법")
    print("  · 갭은 **이동거리** 기준이다. 작업시간(대당 30초)은 경로와 무관하게 같다.")
    print("  · OR-Tools 값도 시간 제한 안에서 찾은 해이지 증명된 최적해가 아니다 —")
    print("    갭은 '적어도 이만큼은 손해'라는 하한으로 읽어라.")
    print("  · **양쪽 모두 depot 복귀를 포함한다** (2026-08-27 정정). 그 전에는")
    print("    OR-Tools만 복귀를 빼고 풀어 갭이 5배로 부풀어 있었다.")
    print("  · 마지막 줄이 선택적 재최적화의 근거다 — 갭이 큰 몇 개만 다시 풀면")
    print("    절감분의 대부분을 훨씬 싸게 얻는다.")

    if args.out:
        frame.to_csv(args.out, index=False, encoding="utf-8")
        print(f"\n결과를 저장했습니다: {args.out}")


if __name__ == "__main__":
    main()
