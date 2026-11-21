"""통합 모형 대비 분해 손실: 군집 경계 없이 K대가 함께 푼 경로와 견준다 (2026-11-21, 사전 등록).

왜 재나
  제안 방법은 회차를 군집화 → ILP → 그리디 세 단계로 나눠 푼다. 원고와 발표 자료는 그 대가를
  "전체 최적성을 잃고, 손실 크기는 재지 않았다"로 남겼다(docs/연구/발표_기술선택.md 1절 · 11절 1번).
  통합 문제(수거 · 배송이 있는 다차량 경로 문제)는 정확히 풀 수 없으므로 OR-Tools로 회차 전체를
  한 번에 풀어 **손실의 하한**을 잰다. OR-Tools 해도 최적이 아니라서 여기 나온 통합의 이득은 하한이다.
  이미 잰 것(5-B장 물량 손실, 5-G장 인접 군집 교환, 8 · 27장 연쇄 주행, 24장 군집 안 경로 갭)은 다시 재지 않는다.

무엇을 견주나 (모든 해를 `route_metrics()` 하나로 다시 잰다)
  D     분해(현행): baseline_compare.plan_with_clusters 그대로
  D-OR  분해 + 군집별 OR-Tools 경로(같은 군집 · 같은 수량에서 경로만 다시 푼다: '경로 몫')
  I-b   통합, 같은 물량: D의 대여소별 수량을 K대가 군집 경계 없이 함께 나른다.
        목적 = 거리(m) + 120분을 넘는 1초마다 1,000m. D와 D-OR 가운데 이 목적이 낮은 해에서 출발한다.
  D-B   분해, 예산 제약: 그리디를 120분에서 끊는다(greedy_route(time_budget_sec)). 물량은 군집별 min(수거, 배송).
  I-c   통합, 예산 안 물량 최대: 후보 전체의 수거 · 배송을 1대 단위 선택 노드로 두고(빠뜨리면 1대당 1,000km 벌점)
        차마다 120분을 넘지 못하게 한다. D-B에서 싣고 돌아온 몫을 덜어 낸 해에서 출발한다.
  (a)   경계 상한 U = min(후보 수거 합, 후보 배송 합). 분해 물량 V_D = 군집별 min(수거, 배송)의 합.
  모든 통합 해: K대(K = D의 군집 수), 적재 0 ~ 10대, 차고지 출발 · 복귀, 출발 · 도착 적재 0.

판정 (사전 등록, 사례 합계. scratchpad 노트 4_integrated.md)
  C1 물량: V_Ic 합 >= 1.05 x V_D 합 (예산을 지키면서 현행보다 5% 넘게 더 옮긴다)
  C2 거리: km_Ib 합 <= 0.90 x km_D 합 이고 초과_Ib 합 <= 초과_D 합
  C3 예산: 초과_Ib 합 <= 2/3 x 초과_D 합 이고 km_Ib 합 <= 1.10 x km_D 합 (초과_D가 0이면 서지 않는다)
  하나라도 서고 그 기준이 달마다도 서면 '분해 손실이 크다'. 아니면 '분해 손실이 작다(이 해법이 찾은 범위에서)'.

시간 정의
  구간마다 travel_seconds(직선 km)(같은 대여소끼리 이어진 구간은 0), 자전거 1대마다 PICK/DROP_TIME_SEC.
  그리디 행의 cum_sec는 거리 0인 구간에도 고정비를 물려 이 평가보다 조금 길 수 있다(D_over_pipeline으로 함께 적는다).
  120분 초과는 1초 여유를 두고 센다(OR-Tools 시간 행렬을 0.1초 단위로 내림해 생기는 차이를 흡수한다).

재현
  python experiments/baseline/integrated_gap.py --periods "25년 11월,26년 03월" --seeds 42,7,13 --limit-sec 60 \\
      --out experiments/baseline/integrated_gap_261121.csv
  python experiments/baseline/integrated_gap.py --periods "25년 11월" --durations _05_10 --seeds 42 \\
      --limit-sec 300 --cluster-sec 10 --out experiments/baseline/integrated_gap_261121_300s.csv
"""
import argparse
import contextlib
import io
import sys
import time
import weakref
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import project_config  # noqa: E402,F401  (콘솔 인코딩을 먼저 맞춘다)
from project_config import (  # noqa: E402
    DEPOT_ID, DEPOT_LAT, DEPOT_LON, DROP_TIME_SEC, PICK_TIME_SEC, TIME_BUDGET_MINUTES,
    VEHICLE_CAPACITY, haversine_km, normalize_day_type, travel_seconds,
)
from experiments._shared import load_baseline, resolve_run_label  # noqa: E402

try:
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
except ImportError:  # pragma: no cover
    raise SystemExit("ortools가 없습니다. requirements.txt로 설치하십시오:\n"
                     "  .\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt")

DAY_ROUNDS = ("_05_10", "_10_15", "_15_20")   # 원고의 낮 세 회차(실험 조건)
BUDGET_SEC = TIME_BUDGET_MINUTES * 60
OVER_TOL_SEC = 1.0                 # 초과를 셀 때의 여유(시간 행렬 내림 오차)
OVERTIME_M_PER_SEC = 1000          # I-b · D-OR: 120분을 넘는 1초 = 거리 1,000m
SKIP_PENALTY_M = 1_000_000         # I-c: 1대를 빠뜨리면 1,000km
DS = 10                            # OR-Tools 시간 단위: 0.1초


# ---------------------------------------------------------------- 평가 (모든 해가 같은 자)

def route_metrics(stops, day_type):
    """정차 목록 하나의 거리(km) · 시간(초) · 옮겨 끝낸 대수.

    stops: [(대여소, 'pick'|'drop', 대수, 위도, 경도), ...] 차고지 출발과 복귀는 넣지 않는다.
    같은 대여소끼리 이어진 구간은 이동시간 0이다(1대 단위 노드를 이어 처리해도 정차 한 번).
    """
    km = 0.0
    sec = 0.0
    picked = dropped = 0
    cur_sid, cur_lat, cur_lon = DEPOT_ID, DEPOT_LAT, DEPOT_LON
    for sid, action, qty, lat, lon in stops:
        d = float(haversine_km(cur_lat, cur_lon, lat, lon))
        km += d
        if sid != cur_sid:
            sec += travel_seconds(d, day_type=day_type)
        sec += qty * (PICK_TIME_SEC if action == "pick" else DROP_TIME_SEC)
        if action == "pick":
            picked += qty
        else:
            dropped += qty
        cur_sid, cur_lat, cur_lon = sid, lat, lon
    if cur_sid != DEPOT_ID:
        d = float(haversine_km(cur_lat, cur_lon, DEPOT_LAT, DEPOT_LON))
        km += d
        sec += travel_seconds(d, day_type=day_type)
    return {"km": km, "sec": sec, "moved": min(picked, dropped),
            "picked": picked, "dropped": dropped}


def solution_metrics(routes, day_type):
    """경로 여럿(차 한 대 = 목록 하나)의 합계. 빈 경로는 쓰지 않은 차다."""
    rows = [route_metrics(r, day_type) for r in routes if r]
    if not rows:
        return {"km": 0.0, "moved": 0, "carried": 0, "over": 0, "max_min": 0.0,
                "total_min": 0.0, "vehicles": 0, "overtime_sec": 0.0}
    secs = [r["sec"] for r in rows]
    return {
        "km": sum(r["km"] for r in rows),
        "moved": sum(r["moved"] for r in rows),
        "carried": sum(r["picked"] - r["dropped"] for r in rows),
        "over": int(sum(s > BUDGET_SEC + OVER_TOL_SEC for s in secs)),
        "max_min": max(secs) / 60,
        "total_min": sum(secs) / 60,
        "vehicles": len(rows),
        "overtime_sec": sum(max(0.0, s - BUDGET_SEC) for s in secs),
    }


def soft_objective(metrics):
    """I-b · D-OR의 목적(거리 m + 초과 1초당 1,000m). 출발해를 고를 때 쓴다."""
    return metrics["km"] * 1000 + OVERTIME_M_PER_SEC * metrics["overtime_sec"]


# ---------------------------------------------------------------- 그리디 행 ↔ 정차 목록

def routes_from_rows(rows: pd.DataFrame):
    """greedy_route() 행을 군집별 정차 목록으로. 군집 번호 순서를 지킨다."""
    out = {}
    if rows.empty:
        return out
    work = rows[rows["action"].isin(["pick", "drop"]) & (rows["qty"] > 0)]
    for cluster, part in work.groupby("cluster", sort=True):
        out[cluster] = [(r.to_id, r.action, int(r.qty), float(r.to_lat), float(r.to_lon))
                        for r in part.itertuples()]
    return out


def cluster_nodes(rows: pd.DataFrame):
    """그리디 행에서 군집별 노드 사전을 되살린다(ILP 수량과 같다: 5-B장, ILP → VRP 손실 0)."""
    nodes = {}
    work = rows[rows["action"].isin(["pick", "drop"])]
    for cluster, part in work.groupby("cluster", sort=True):
        one = {}
        for (sid, action), g in part.groupby(["to_id", "action"]):
            one[(sid, action)] = {"qty": int(g["qty"].sum()), "lat": float(g["to_lat"].iloc[0]),
                                  "lon": float(g["to_lon"].iloc[0])}
        nodes[cluster] = one
    return nodes


def trim_carry_back(stops):
    """실은 채 돌아온 몫을 마지막 수거부터 덜어 낸다.

    덜어 내도 적재가 음수가 되지 않는다: 마지막 수거 뒤에는 배송만 있으므로 그 뒤 적재는 늘 끝 적재(= 덜어 낼 양) 이상이고,
    앞 수거로 넘어가도 같은 논리가 이어진다. 정차를 빼면 삼각 부등식으로 거리 · 시간이 줄 뿐이라 예산도 지킨다.
    """
    load = sum(q if a == "pick" else -q for _s, a, q, _la, _lo in stops)
    out = [list(s) for s in stops]
    for i in range(len(out) - 1, -1, -1):
        if load <= 0:
            break
        if out[i][1] == "pick":
            take = min(out[i][2], load)
            out[i][2] -= take
            load -= take
    return [tuple(s) for s in out if s[2] > 0]


# ---------------------------------------------------------------- OR-Tools 1대 단위 모형

class UnitModel:
    """대여소의 수거 · 배송을 자전거 1대 단위 노드로 편 모형(0번 = 차고지).

    1대 단위로 펴는 까닭은 ortools_gap.split_nodes()와 같다: 대여소를 한 노드로 두면 '전부 아니면 전무'가 되어
    실행 불가능한 사례가 생긴다. 같은 대여소 노드끼리는 거리 · 이동시간이 0이다.
    """

    def __init__(self, demand: dict, coords: dict, day_type: str):
        sid, lat, lon, sign = [DEPOT_ID], [DEPOT_LAT], [DEPOT_LON], [0]
        self.pool = {}
        for (station, action), qty in sorted(demand.items()):
            for _ in range(int(qty)):
                self.pool.setdefault((station, action), []).append(len(sid))
                sid.append(station)
                lat.append(coords[station][0])
                lon.append(coords[station][1])
                sign.append(1 if action == "pick" else -1)
        self.sid, self.lat, self.lon, self.sign = sid, lat, lon, sign
        self.action = ["depot"] + ["pick" if s > 0 else "drop" for s in sign[1:]]
        self.size = len(sid)

        la = np.asarray(lat, dtype=float)
        lo = np.asarray(lon, dtype=float)
        km = haversine_km(la[:, None], lo[:, None], la[None, :], lo[None, :])
        codes = pd.factorize(pd.Series(sid, dtype=object))[0]
        same = codes[:, None] == codes[None, :]
        travel = np.where(same, 0.0, travel_seconds(km, day_type=day_type))
        service = np.array([0.0] + [PICK_TIME_SEC if s > 0 else DROP_TIME_SEC for s in sign[1:]])
        self.dist_m = np.rint(km * 1000).astype(np.int64)
        # i에서 j로 가는 시간 = i에서의 작업 + i → j 이동. 내림이라 OR-Tools가 보는 시간은 참값 이하다.
        self.time_ds = np.floor((travel + service[:, None]) * DS).astype(np.int64)
        np.fill_diagonal(self.time_ds, 0)

    def to_unit_routes(self, routes):
        """정차 목록들을 노드 번호 경로로 바꾼다. 남은(배정 안 된) 노드 수도 돌려준다."""
        pool = {k: list(v) for k, v in self.pool.items()}
        out = []
        for stops in routes:
            r = []
            for station, action, qty, _la, _lo in stops:
                for _ in range(qty):
                    r.append(pool[(station, action)].pop())
            out.append(r)
        return out, sum(len(v) for v in pool.values())

    def to_stops(self, unit_route):
        """노드 번호 경로를 정차 목록으로(같은 대여소 · 같은 일이 이어지면 한 정차로 합친다)."""
        stops = []
        for n in unit_route:
            key = (self.sid[n], self.action[n])
            if stops and (stops[-1][0], stops[-1][1]) == key:
                s = stops[-1]
                stops[-1] = (s[0], s[1], s[2] + 1, s[3], s[4])
            else:
                stops.append((key[0], key[1], 1, self.lat[n], self.lon[n]))
        return stops


def solve_units(model: UnitModel, vehicles: int, initial, limit_sec: int, *,
                optional: bool = False, hard_budget: bool = False, soft_budget: bool = False,
                progress: list | None = None):
    """K대 1-상품 수거 · 배송 경로를 푼다. initial(노드 번호 경로)에서 출발한다.

    progress에 리스트를 주면 해를 찾을 때마다 (탐색 시작 뒤 초, 목적값)을 덧붙인다(73장의 계산 시간).
    주지 않으면 콜백을 걸지 않아 68장과 똑같이 푼다.

    반환: (노드 경로 목록, 출발해 목적값, 최종 목적값, 상태 문구)
    """
    manager = pywrapcp.RoutingIndexManager(model.size, vehicles, 0)
    routing = pywrapcp.RoutingModel(manager)
    dist = routing.RegisterTransitMatrix(model.dist_m.tolist())
    routing.SetArcCostEvaluatorOfAllVehicles(dist)

    load = routing.RegisterUnaryTransitVector([int(s) for s in model.sign])
    routing.AddDimensionWithVehicleCapacity(load, 0, [VEHICLE_CAPACITY] * vehicles, True, "Load")
    load_dim = routing.GetDimensionOrDie("Load")

    horizon = int(BUDGET_SEC * DS) if hard_budget else int(24 * 3600 * DS)
    tt = routing.RegisterTransitMatrix(model.time_ds.tolist())
    routing.AddDimension(tt, 0, horizon, True, "Time")
    time_dim = routing.GetDimensionOrDie("Time")

    for v in range(vehicles):
        load_dim.CumulVar(routing.End(v)).SetRange(0, 0)      # 실은 채 돌아오지 않는다
        if soft_budget:
            time_dim.SetCumulVarSoftUpperBound(routing.End(v), int(BUDGET_SEC * DS),
                                               OVERTIME_M_PER_SEC // DS)
    if optional:
        for node in range(1, model.size):
            routing.AddDisjunction([manager.NodeToIndex(node)], SKIP_PENALTY_M)

    clock = {"t0": None}
    if progress is not None:
        ref = weakref.ref(routing)      # OR-Tools 예제처럼 약한 참조로 순환 참조를 피한다

        def _record():
            r = ref()
            if r is not None and clock["t0"] is not None:
                progress.append((time.time() - clock["t0"], int(r.CostVar().Value())))
        routing.AddAtSolutionCallback(_record)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromSeconds(int(limit_sec))
    routing.CloseModelWithParameters(params)

    start_obj = None
    init = None
    if initial is not None:
        padded = list(initial) + [[] for _ in range(vehicles - len(initial))]
        init = routing.ReadAssignmentFromRoutes(
            [[manager.NodeToIndex(n) for n in r] for r in padded], True)
    clock["t0"] = time.time()
    if init is not None:
        start_obj = int(init.ObjectiveValue())
        solution = routing.SolveFromAssignmentWithParameters(init, params)
        status = "출발해에서"
    else:
        solution = routing.SolveWithParameters(params)
        status = "출발해 거부 → 처음부터" if initial is not None else "처음부터"
    if solution is None:
        return None, start_obj, None, status + " · 해 없음"

    routes = []
    for v in range(vehicles):
        index = routing.Start(v)
        r = []
        index = solution.Value(routing.NextVar(index))
        while not routing.IsEnd(index):
            r.append(manager.IndexToNode(index))
            index = solution.Value(routing.NextVar(index))
        routes.append(r)
    return routes, start_obj, int(solution.ObjectiveValue()), status


# ---------------------------------------------------------------- 한 사례

def run_instance(bc, step1, solver, base, period, duration, seed, args):
    t0 = time.time()
    day_type = args.day_type
    coords = {r.station_id: (float(r.lat), float(r.lon)) for r in base.itertuples()}
    cand_pick = int((-base["rebal_qty"].clip(upper=0)).sum())
    cand_drop = int(base["rebal_qty"].clip(lower=0).sum())

    # D: 분해(현행)
    _clustered, rows = bc.plan_with_clusters(base.copy(), step1, solver, adjust=True, seed=seed)
    d_routes = routes_from_rows(rows)
    clusters = list(d_routes)
    d = solution_metrics(list(d_routes.values()), day_type)
    pipeline = bc.route_stats(rows)
    K = len(clusters)

    # D-OR: 군집마다 경로만 다시 푼다(같은 목적: 거리 + 초과 벌점)
    dor_routes = []
    for cluster in clusters:
        stops = d_routes[cluster]
        demand = {}
        for sid, action, qty, _la, _lo in stops:
            demand[(sid, action)] = demand.get((sid, action), 0) + qty
        m = UnitModel(demand, coords, day_type)
        init, left = m.to_unit_routes([stops])
        assert left == 0, "군집 안 노드가 출발해에 다 들어가야 한다"
        sol, _s, _e, _st = solve_units(m, 1, init, args.cluster_sec, soft_budget=True)
        dor_routes.append(m.to_stops(sol[0]) if sol else stops)
    dor = solution_metrics(dor_routes, day_type)

    # I-b: 통합, 같은 물량
    demand_b = {}
    for stops in d_routes.values():
        for sid, action, qty, _la, _lo in stops:
            demand_b[(sid, action)] = demand_b.get((sid, action), 0) + qty
    mb = UnitModel(demand_b, coords, day_type)
    start_name, start_routes = ("D", list(d_routes.values()))
    if soft_objective(dor) < soft_objective(d):
        start_name, start_routes = ("D-OR", dor_routes)
    init_b, left_b = mb.to_unit_routes(start_routes)
    assert left_b == 0, "같은 물량 모형의 노드가 출발해에 다 들어가야 한다"
    sol_b, ib_start_obj, ib_end_obj, ib_status = solve_units(mb, K, init_b, args.limit_sec, soft_budget=True)
    ib_routes = [mb.to_stops(r) for r in sol_b] if sol_b else start_routes
    ib = solution_metrics(ib_routes, day_type)

    # D-B: 분해, 예산 제약(같은 군집 · 같은 ILP, 그리디를 120분에서 끊는다)
    nodes = cluster_nodes(rows)
    db_rows = []
    for cluster in clusters:
        copied = {k: dict(v) for k, v in nodes[cluster].items()}     # 함정 12: 값 사전까지 복사
        db_rows.extend(bc.quiet(bc.vrp_mod.greedy_route, copied, cluster, BUDGET_SEC))
    db_routes = routes_from_rows(pd.DataFrame(db_rows))
    dbm = solution_metrics(list(db_routes.values()), day_type)

    # I-c: 통합, 예산 안 물량 최대(후보 전체가 선택 노드)
    demand_c = {}
    for r in base.itertuples():
        q = int(r.rebal_qty)
        if q < 0:
            demand_c[(r.station_id, "pick")] = -q
        elif q > 0:
            demand_c[(r.station_id, "drop")] = q
    mc = UnitModel(demand_c, coords, day_type)
    trimmed = [trim_carry_back(s) for s in db_routes.values()]
    init_c, _left_c = mc.to_unit_routes(trimmed)
    start_c = solution_metrics(trimmed, day_type)
    sol_c, ic_start_obj, ic_end_obj, ic_status = solve_units(mc, K, init_c, args.limit_sec,
                                                             optional=True, hard_budget=True)
    ic_routes = [mc.to_stops(r) for r in sol_c] if sol_c else trimmed
    ic = solution_metrics(ic_routes, day_type)

    row = {
        "period": period, "duration": duration, "seed": seed, "K": K,
        "cand_n": int(len(base)), "cand_pick": cand_pick, "cand_drop": cand_drop,
        "U": min(cand_pick, cand_drop), "boundary_loss": min(cand_pick, cand_drop) - d["moved"],
        "D_moved": d["moved"], "D_km": d["km"], "D_over": d["over"], "D_max_min": d["max_min"],
        "D_total_min": d["total_min"], "D_vehicles": d["vehicles"],
        "D_over_pipeline": pipeline["over"], "D_moved_pipeline": pipeline["bikes_done"],
        "DOR_km": dor["km"], "DOR_over": dor["over"], "DOR_max_min": dor["max_min"],
        "Ib_start": start_name, "Ib_status": ib_status, "Ib_start_obj": ib_start_obj, "Ib_end_obj": ib_end_obj,
        "Ib_moved": ib["moved"], "Ib_km": ib["km"], "Ib_over": ib["over"], "Ib_max_min": ib["max_min"],
        "Ib_total_min": ib["total_min"], "Ib_vehicles": ib["vehicles"],
        "DB_moved": dbm["moved"], "DB_km": dbm["km"], "DB_carried": dbm["carried"], "DB_over": dbm["over"],
        "Ic_status": ic_status, "Ic_start_moved": start_c["moved"],
        "Ic_moved": ic["moved"], "Ic_km": ic["km"], "Ic_over": ic["over"], "Ic_max_min": ic["max_min"],
        "Ic_vehicles": ic["vehicles"], "Ic_carried": ic["carried"],
        "limit_sec": args.limit_sec, "cluster_sec": args.cluster_sec, "elapsed_sec": time.time() - t0,
    }
    print(f"  [{period} {duration} 씨앗 {seed}] K {K} · U {row['U']} · V_D {d['moved']} · "
          f"D {d['km']:.1f}km 초과 {d['over']} · D-OR {dor['km']:.1f}km 초과 {dor['over']} · "
          f"I-b {ib['km']:.1f}km 초과 {ib['over']} 차 {ib['vehicles']}({start_name}) · "
          f"D-B {dbm['moved']}대 · I-c {ic['moved']}대 초과 {ic['over']} · {row['elapsed_sec']:.0f}초", flush=True)
    return row


# ---------------------------------------------------------------- 판정

def verdict(frame: pd.DataFrame) -> dict:
    """사전 등록 C1 ~ C3를 합계로 본다."""
    t = frame.sum(numeric_only=True)
    c1 = bool(t["Ic_moved"] >= 1.05 * t["D_moved"])
    c2 = bool(t["Ib_km"] <= 0.90 * t["D_km"] and t["Ib_over"] <= t["D_over"])
    c3 = bool(t["D_over"] > 0 and t["Ib_over"] <= (2 / 3) * t["D_over"] and t["Ib_km"] <= 1.10 * t["D_km"])
    return {"C1": c1, "C2": c2, "C3": c3}


def summarize(frame: pd.DataFrame) -> None:
    t = frame.sum(numeric_only=True)
    print("\n" + "=" * 96)
    print(f"합계 ({len(frame)}개 사례)")
    print("=" * 96)
    print(f"(a) 경계 상한 U {int(t['U'])}대 · 분해 물량 V_D {int(t['D_moved'])}대 · 경계 손실 {int(t['boundary_loss'])}대 "
          f"({t['boundary_loss'] / t['U'] * 100:.1f}%)")
    print(f"거리     D {t['D_km']:.1f}km · D-OR {t['DOR_km']:.1f}km ({(t['DOR_km'] / t['D_km'] - 1) * 100:+.1f}%) · "
          f"I-b {t['Ib_km']:.1f}km ({(t['Ib_km'] / t['D_km'] - 1) * 100:+.1f}%)")
    print(f"120분 초과 차  D {int(t['D_over'])} (파이프라인 cum_sec 기준 {int(t['D_over_pipeline'])}) · "
          f"D-OR {int(t['DOR_over'])} · I-b {int(t['Ib_over'])}")
    print(f"쓴 차    D {int(t['D_vehicles'])} · I-b {int(t['Ib_vehicles'])} · I-c {int(t['Ic_vehicles'])}")
    print(f"예산 안 물량  D-B {int(t['DB_moved'])}대 (싣고 돌아옴 {int(t['DB_carried'])}) · "
          f"I-c {int(t['Ic_moved'])}대 · 현행 V_D {int(t['D_moved'])}대 → I-c / V_D = {t['Ic_moved'] / t['D_moved']:.3f}, "
          f"I-c / D-B = {t['Ic_moved'] / t['DB_moved']:.3f}")
    total = verdict(frame)
    months = {p: verdict(part) for p, part in frame.groupby("period", sort=False)}
    print("\n판정 (사전 등록)")
    for key in ("C1", "C2", "C3"):
        each = " · ".join(f"{p} {'O' if v[key] else 'X'}" for p, v in months.items())
        print(f"  {key}: 합계 {'O' if total[key] else 'X'} | 달마다 {each}")
    big = [k for k in ("C1", "C2", "C3") if total[k] and all(v[k] for v in months.values())]
    if big:
        print(f"  → 분해 손실이 크다 ({', '.join(big)})")
    else:
        print("  → 분해 손실이 작다 (이 해법이 찾은 범위에서. OR-Tools 해는 하한이다)")
    print("\n회차별 합계")
    cols = ["U", "D_moved", "boundary_loss", "D_km", "DOR_km", "Ib_km", "D_over", "DOR_over", "Ib_over",
            "DB_moved", "Ic_moved"]
    print(frame.groupby(["period", "duration"], sort=False)[cols].sum().round(1).to_string())


def main():
    parser = argparse.ArgumentParser(description="통합 모형 대비 분해 손실 (사전 등록 2026-11-21)")
    parser.add_argument("--periods", default="25년 11월,26년 03월", help="순수요 기간(쉼표 구분)")
    parser.add_argument("--durations", default=",".join(DAY_ROUNDS))
    parser.add_argument("--seeds", default="42,7,13")
    parser.add_argument("--day-type", default="weekday", choices=["weekday", "holiday"])
    parser.add_argument("--run-label", default="", help="재고 스냅샷(기본: 정본)")
    parser.add_argument("--limit-sec", type=int, default=60, help="I-b · I-c 탐색 시간(초)")
    parser.add_argument("--cluster-sec", type=int, default=2, help="D-OR 군집당 탐색 시간(초)")
    parser.add_argument("--out", default="", help="사례별 결과 CSV(사례마다 덮어써 중간 결과를 남긴다)")
    parser.add_argument("--summary-only", default="", help="이미 만든 CSV를 읽어 판정만 다시 찍는다")
    args, _ = parser.parse_known_args()

    if args.summary_only:
        summarize(pd.read_csv(args.summary_only))
        return

    args.day_type = normalize_day_type(args.day_type)
    periods = [p.strip() for p in args.periods.split(",") if p.strip()]
    durations = [d.strip() for d in args.durations.split(",") if d.strip()]
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]

    with contextlib.redirect_stdout(io.StringIO()):
        bc = load_baseline()
    step1 = bc.load_step1()
    bc.align_day_type(args.day_type, bc.ilp_mod, bc.vrp_mod, bc.kpi_mod, step1)
    solver = bc.ilp_mod.build_solver()
    label = resolve_run_label(args.run_label or None)

    rows = []
    for period in periods:
        net, st_info, warmup = bc.load_inputs(period, label, args.day_type, 0, "")
        for duration in durations:
            base = bc.build_candidates(net, st_info, duration, None, warmup, 0, step1)
            if base.empty:
                print(f"[건너뜀] {period} {duration}: 작업 대상 없음")
                continue
            for seed in seeds:
                rows.append(run_instance(bc, step1, solver, base, period, duration, seed, args))
                if args.out:
                    pd.DataFrame(rows).to_csv(args.out, index=False, encoding="utf-8")
    if not rows:
        raise SystemExit("결과가 없습니다.")
    summarize(pd.DataFrame(rows))
    if args.out:
        print(f"\n결과를 저장했습니다: {args.out}")


if __name__ == "__main__":
    main()
