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
  · **양쪽 다 depot으로 돌아오지 않는다** — 현행 greedy가 그렇기 때문이다(아래 참고)

**함께 재는 것 — 빠져 있는 depot 복귀**
현행 `greedy_route()`는 작업이 끝나면 그 자리에서 멈춘다. 마지막 depot 복귀 구간이
거리·시간에 **들어 있지 않다**. 복귀를 기록하는 분기는 '적재가 막혀 후보가 없을 때'
하나뿐인데 ILP 입력에서는 실행될 수 없어(총 pick = 총 drop), vrp_plan의 'return' 행이
**0건**이다. 즉 항상 빠진다. 그래서 시간 예산 120분 판정이 낙관적이다.
그 누락분이 얼마인지 함께 낸다.

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
    """depot에서 출발해 모든 노드를 처리하는 최소 이동거리 경로 (복귀 없음).

    마지막에 비용 0짜리 가상 종점을 두어 '복귀하지 않는 경로'로 만든다 —
    현행 greedy와 같은 조건으로 맞추기 위해서다.
    """
    points = [(DEPOT_LAT, DEPOT_LON, 0)] + list(nodes) + [(DEPOT_LAT, DEPOT_LON, 0)]
    size = len(points)
    end = size - 1

    def meters(i, j):
        if i == end or j == end:          # 가상 종점은 어디서든 비용 0
            return 0
        return int(round(haversine_km(points[i][0], points[i][1],
                                      points[j][0], points[j][1]) * 1000))

    manager = pywrapcp.RoutingIndexManager(size, 1, [0], [end])
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
              f"{'갭':>9}{'복귀 누락 km':>13}{'greedy 분':>11}{'복귀 포함 분':>13}")

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

            # 마지막 위치에서 depot까지 — 현행 계산에 빠져 있는 구간
            last = route.iloc[-1]
            back_km = haversine_km(last["to_lat"], last["to_lon"], DEPOT_LAT, DEPOT_LON)
            back_min = back_km / VEHICLE_SPEED_KMPH * 60

            best_km = solve_ortools(nodes, args.limit_sec)
            if best_km is None:
                # 해를 못 찾은 클러스터는 갭을 뺀다 — 억지로 채우면 평균이 거짓말을 한다.
                print(f"{cluster:>8d}{len(nodes):>6d}{greedy_km:>12.2f}"
                      f"{'해 못 찾음':>13}{'':>9}{back_km:>13.2f}{greedy_min:>11.1f}"
                      f"{greedy_min + back_min:>13.1f}")
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
            print(f"{cluster:>8d}{len(nodes):>6d}{greedy_km:>12.2f}{best_km:>13.2f}"
                  f"{gap:>8.1f}%{back_km:>13.2f}{greedy_min:>11.1f}"
                  f"{greedy_min + back_min:>13.1f}")

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
        print(f"          depot 복귀 누락 {part['return_km'].sum():.1f}km"
              f" · 예산 초과 {over_now}건 → 복귀 포함 시 {over_with}건")

    print("\n읽는 법")
    print("  · 갭은 **이동거리** 기준이다. 작업시간(대당 30초)은 경로와 무관하게 같다.")
    print("  · OR-Tools 값도 시간 제한 안에서 찾은 해이지 증명된 최적해가 아니다 —")
    print("    갭은 '적어도 이만큼은 손해'라는 하한으로 읽어라.")
    print("  · **복귀 누락은 갭과 별개의 문제다.** 현행 계산이 마지막 depot 복귀를")
    print("    빼고 있어 시간 예산 판정이 낙관적이다.")

    if args.out:
        frame.to_csv(args.out, index=False, encoding="utf-8")
        print(f"\n결과를 저장했습니다: {args.out}")


if __name__ == "__main__":
    main()
