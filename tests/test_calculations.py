"""단계별 계산 로직의 단위 테스트 (docs/TODO.md P3-11).

기존 테스트는 **스모크 수준**이었습니다 — 합성 데이터로 파이프라인을 끝까지 돌려
산출물이 나오는지, 스키마가 맞는지를 봤습니다. 그래서 "돌긴 도는데 값이 틀린" 종류의
회귀는 잡지 못했습니다.

이 파일은 **계산 자체**를 봅니다. 세 곳이 대상입니다.

  1. 목표 재고·재배치량 공식  (`calculate_target_qty.compute_rebal_qty`, `build_stats`)
  2. 군집 조정 목적함수        (`adjust_module.compute_objective`)
  3. VRP 적재·시간 제약        (`vrp.greedy_route`), ILP 수급 제약 (`ilp.solve_cluster_moves`)

수식의 근거는 [docs/FORMULATION.md](../docs/FORMULATION.md)에 있습니다.
**공식을 바꾸면 이 파일이 먼저 깨져야 합니다.**
"""
import importlib.util
import sys

import numpy as np
import pandas as pd
import pytest

from project_config import (
    CLUSTER_ALPHA, CLUSTER_BETA, CLUSTER_GAMMA, DEPOT_ID, PROJECT_ROOT, TARGET_Z,
    VEHICLE_CAPACITY,
)

STEP0 = PROJECT_ROOT / "step0_collect"
STEP1 = PROJECT_ROOT / "step1_cluster"
STEP2 = PROJECT_ROOT / "step2_optimize"


def _load(path, name):
    """step 모듈을 경로로 직접 읽는다.

    테스트마다 독립된 이름으로 올려 import 캐시를 공유하지 않게 한다.
    `top_st_clustering.py`는 숫자로 시작해 애초에 일반 import가 안 된다.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def target_qty():
    return _load(STEP0 / "calculate_target_qty.py", "_calc_target")


@pytest.fixture(scope="module")
def step1():
    sys.path.insert(0, str(STEP1))
    return _load(STEP1 / "top_st_clustering.py", "_top_st")


@pytest.fixture(scope="module")
def adjust():
    sys.path.insert(0, str(STEP1))
    return _load(STEP1 / "adjust_module.py", "_adjust")


@pytest.fixture(scope="module")
def step2():
    sys.path.insert(0, str(STEP2))          # vrp가 ilp를 import 한다
    return _load(STEP2 / "ilp.py", "ilp"), _load(STEP2 / "vrp.py", "_vrp")


def _stats(mu, sigma, stock, parking_lot):
    return pd.DataFrame({
        "station_id": [f"ST{i:04d}" for i in range(len(mu))],
        "mu": mu, "sigma": sigma, "stock": stock, "parking_lot": parking_lot,
    })


# ---------------------------------------------------------------- 1. 목표 재고

def test_target_uses_mu_plus_z_sigma_when_demand_flows_out(target_qty):
    """mu >= 0(순유출)이면 목표 재고 = mu + z·sigma.

    그 시간대에 빠져나갈 양을 미리 채워 두는 것이다. z는 관행값 1.65가 아니라
    12개월 백테스트로 정한 1.99다 (docs/EXPERIMENTS.md 1장).
    """
    frame = target_qty.compute_rebal_qty(
        _stats(mu=[10.0], sigma=[2.0], stock=[5], parking_lot=[100]), z=2.0)

    assert frame["target_qty"].iloc[0] == pytest.approx(10.0 + 2.0 * 2.0)


def test_target_uses_stock_plus_mu_when_demand_flows_in(target_qty):
    """mu < 0(순유입 — 반납이 더 많다)이면 목표 재고 = stock + mu.

    들어올 양만큼 미리 비워 둔다. 이 분기를 mu + z·sigma로 바꾸면 반납이 몰리는
    대여소가 포화된다.
    """
    frame = target_qty.compute_rebal_qty(
        _stats(mu=[-4.0], sigma=[3.0], stock=[20], parking_lot=[100]), z=2.0)

    assert frame["target_qty"].iloc[0] == pytest.approx(20 - 4.0)


def test_target_is_capped_by_docks_and_floored_at_zero(target_qty):
    """목표 재고는 [0, 1.5 × 거치대 수]로 자른다."""
    frame = target_qty.compute_rebal_qty(
        _stats(mu=[500.0, -900.0], sigma=[0.0, 0.0],
               stock=[5, 5], parking_lot=[10, 10]), z=2.0)

    assert frame["target_qty"].iloc[0] == pytest.approx(15.0)     # 10 × 1.5
    assert frame["target_qty"].iloc[1] == pytest.approx(0.0)


def test_rebal_qty_is_bounded_by_vehicle_capacity(target_qty):
    """재배치량은 tanh로 적재 용량(10대)까지만 잡힌다.

    한 대여소에 작업이 몰려 차량 한 대로 감당 못 하는 계획이 나오지 않게 하는 장치다.
    `10·tanh(x/10)`은 10에 점근하므로 **한 대여소 최대치는 정확히 10대**(꽉 찬 한 차)다.
    """
    frame = target_qty.compute_rebal_qty(
        _stats(mu=[999.0, 0.0], sigma=[0.0, 0.0],
               stock=[0, 999], parking_lot=[9999, 9999]), z=2.0)

    assert frame["rebal_qty"].abs().max() <= VEHICLE_CAPACITY
    assert frame["rebal_qty"].tolist() == [VEHICLE_CAPACITY, -VEHICLE_CAPACITY]


def test_rebal_qty_rounds_toward_zero(target_qty):
    """정수화는 **0 방향**이다 — 양수는 floor, 음수는 ceil.

    반올림하면 계획이 실제보다 커진다. 옮길 수 있는 양보다 많이 잡는 쪽으로
    치우치면 안 된다.
    """
    frame = target_qty.compute_rebal_qty(
        _stats(mu=[3.7, 0.0], sigma=[0.0, 0.0],
               stock=[0, 4], parking_lot=[100, 100]), z=0.0)

    assert frame["rebal_qty"].iloc[0] == 3      # +3.7 → 3
    assert frame["rebal_qty"].iloc[1] == -3     # -3.7 → -3 (0 방향)
    assert frame["rebal_qty"].dtype == np.int64 or frame["rebal_qty"].dtype == np.int32


def test_raising_z_raises_the_target(target_qty):
    """z를 올리면 목표 재고가 올라간다 — 안전재고의 정의다."""
    low = target_qty.compute_rebal_qty(
        _stats(mu=[5.0], sigma=[3.0], stock=[5], parking_lot=[100]), z=1.65)
    high = target_qty.compute_rebal_qty(
        _stats(mu=[5.0], sigma=[3.0], stock=[5], parking_lot=[100]), z=TARGET_Z)

    assert high["target_qty"].iloc[0] > low["target_qty"].iloc[0]


def test_build_stats_sums_only_the_hours_in_the_duration(target_qty):
    """mu·sigma는 **그 시간대의 컬럼만** 더해서 낸다."""
    frame = pd.DataFrame({
        "station_id": ["ST0001", "ST0001"],
        "날짜": ["2026-08-17", "2026-08-18"],
        **{f"net_{hour:02d}": [1, 3] for hour in range(24)},
    })
    stock = pd.DataFrame({"station_id": ["ST0001"], "parking_lot": [20], "stock": [5]})

    stats, _daily, ratio = target_qty.build_stats(
        frame, stock, "_05_10", warmup_net=None, warmup_days=0, verbose=False)

    # 05~09시 = 5시간. 하루는 5×1=5, 다른 하루는 5×3=15 → 평균 10
    assert stats["mu"].iloc[0] == pytest.approx(10.0)
    assert ratio is None, "warmup 자료가 없으면 계절 배율은 없다"


def test_build_stats_wraps_past_midnight(target_qty):
    """`_20_05`처럼 자정을 넘는 시간대는 이어서 돈다 (20~23시 + 00~04시 = 9시간)."""
    frame = pd.DataFrame({
        "station_id": ["ST0001"],
        "날짜": ["2026-08-17"],
        **{f"net_{hour:02d}": [1] for hour in range(24)},
    })
    stock = pd.DataFrame({"station_id": ["ST0001"], "parking_lot": [20], "stock": [5]})

    stats, _daily, _ratio = target_qty.build_stats(
        frame, stock, "_20_05", warmup_net=None, warmup_days=0, verbose=False)

    assert stats["mu"].iloc[0] == pytest.approx(9.0)


# ---------------------------------------------------------------- 2. 군집 조정

def _clusters(rebal, cluster, lat=None, lon=None):
    size = len(rebal)
    return pd.DataFrame({
        "station_id": [f"ST{i:04d}" for i in range(size)],
        "rebal_qty": rebal, "cluster": cluster,
        "lat": lat if lat is not None else [36.0] * size,
        "lon": lon if lon is not None else [127.0] * size,
    })


def test_objective_is_zero_when_everything_is_balanced(adjust):
    """수급이 맞고 크기가 고르고 모두 같은 지점이면 목적함수는 0이다."""
    frame = _clusters(rebal=[5, -5, 5, -5], cluster=[0, 0, 1, 1])

    score = adjust.compute_objective(frame, K=2, alpha=1, beta=100, gamma=3000)

    assert score == pytest.approx(0.0)


def test_imbalanced_clusters_cost_more(adjust):
    """군집 안의 수급이 어긋나면 목적함수가 커진다 — 조정이 이걸 줄이는 방향으로 간다."""
    balanced = _clusters(rebal=[5, -5, 5, -5], cluster=[0, 0, 1, 1])
    skewed = _clusters(rebal=[5, 5, -5, -5], cluster=[0, 0, 1, 1])

    assert (adjust.compute_objective(skewed, 2, 1, 100, 3000)
            > adjust.compute_objective(balanced, 2, 1, 100, 3000))


def test_balance_term_is_squared_sum_per_cluster(adjust):
    """불균형 항 = Σ_k (군집 k의 rebal_qty 합)². alpha만 켜서 값을 직접 확인한다."""
    frame = _clusters(rebal=[3, 0, -4, 0], cluster=[0, 0, 1, 1])

    score = adjust.compute_objective(frame, K=2, alpha=1, beta=0, gamma=0)

    assert score == pytest.approx(3**2 + (-4)**2)


def test_gamma_prices_the_spread_of_a_cluster(adjust):
    """gamma는 군집이 지리적으로 흩어진 정도에 값을 매긴다.

    gamma가 0이면 흩어져도 점수가 같고, 켜면 흩어진 쪽이 비싸진다.
    (gamma=10~150에서 거리 항이 불균형 항에 묻혀 이동이 안 일어났던 이유 —
    docs/EXPERIMENTS.md 4장)
    """
    tight = _clusters(rebal=[5, -5], cluster=[0, 0],
                      lat=[36.00, 36.01], lon=[127.0, 127.0])
    spread = _clusters(rebal=[5, -5], cluster=[0, 0],
                       lat=[36.00, 36.50], lon=[127.0, 127.0])

    assert (adjust.compute_objective(spread, 1, 1, 0, 0)
            == pytest.approx(adjust.compute_objective(tight, 1, 1, 0, 0)))
    assert (adjust.compute_objective(spread, 1, 1, 0, CLUSTER_GAMMA)
            > adjust.compute_objective(tight, 1, 1, 0, CLUSTER_GAMMA))


def test_pipeline_weights_are_the_documented_ones():
    """운영 가중치가 문서(FORMULATION 4.3)와 같은지 지킨다."""
    assert (CLUSTER_ALPHA, CLUSTER_BETA, CLUSTER_GAMMA) == (1.0, 100.0, 3000.0)


# ---------------------------------------------------------------- 3. VRP·ILP 제약

def _nodes(items):
    """[(station_id, 'pick'|'drop', qty, lat, lon)] → greedy_route가 받는 dict."""
    return {(sid, kind): {"qty": qty, "lat": lat, "lon": lon}
            for sid, kind, qty, lat, lon in items}


def _replay_load(route):
    """경로를 되짚어 매 시점의 적재량을 낸다. depot 복귀에서는 0으로 비운다."""
    load, history = 0, []
    for row in route:
        if row["action"] == "pick":
            load += row["qty"]
        elif row["action"] == "drop":
            load -= row["qty"]
        else:                       # return — depot에서 비운다
            load = 0
        history.append(load)
    return history


def test_vrp_never_exceeds_capacity_or_goes_negative(step2):
    """적재량은 항상 0 이상, 적재 용량 이하다.

    차량이 없는 자전거를 내리거나(음수) 실을 수 있는 것보다 많이 싣는(초과) 계획은
    현장에서 집행할 수 없다.
    """
    _ilp, vrp = step2
    nodes = _nodes([
        ("ST0001", "pick", 8, 36.30, 127.38),
        ("ST0002", "pick", 9, 36.31, 127.39),
        ("ST0003", "drop", 7, 36.32, 127.40),
        ("ST0004", "drop", 10, 36.33, 127.41),
    ])

    loads = _replay_load(vrp.greedy_route(nodes, cluster=0))

    assert min(loads) >= 0, "적재량이 음수가 됐다 — 없는 자전거를 내렸다"
    assert max(loads) <= VEHICLE_CAPACITY, "적재 용량을 넘겼다"


def test_vrp_finishes_all_work_when_supply_matches_demand(step2):
    """수급이 맞으면 모든 작업을 끝낸다 (남은 qty 0)."""
    _ilp, vrp = step2
    nodes = _nodes([
        ("ST0001", "pick", 6, 36.30, 127.38),
        ("ST0002", "drop", 6, 36.31, 127.39),
    ])

    route = vrp.greedy_route(nodes, cluster=0)

    assert all(node["qty"] == 0 for node in nodes.values())
    assert sum(r["qty"] for r in route if r["action"] == "pick") == 6
    assert sum(r["qty"] for r in route if r["action"] == "drop") == 6


def test_vrp_keeps_pick_and_drop_of_the_same_station_apart(step2):
    """같은 대여소가 pick·drop 양쪽에 있어도 유실되지 않는다.

    노드 키가 `(station_id, type)`인 이유다. `station_id`만 쓰면 한쪽이 덮어써진다
    (1.0.3에서 실제로 있었던 버그).
    """
    _ilp, vrp = step2
    nodes = _nodes([
        ("ST0001", "pick", 5, 36.30, 127.38),
        ("ST0001", "drop", 5, 36.30, 127.38),
    ])

    vrp.greedy_route(nodes, cluster=0)

    assert nodes[("ST0001", "pick")]["qty"] == 0
    assert nodes[("ST0001", "drop")]["qty"] == 0


def test_vrp_stops_inside_the_time_budget_when_one_is_given(step2):
    """`time_budget_sec`를 주면 예산 안에서 멈춘다.

    **파이프라인은 이 인자를 주지 않는다**(시간 예산은 현재 사후 점검이다).
    대조군 실험처럼 한 대가 넓은 범위를 훑을 때만 쓴다 — docs/TODO.md 1-1.
    """
    _ilp, vrp = step2
    items = [(f"ST{i:04d}", "pick" if i % 2 == 0 else "drop", 5,
              36.0 + i * 0.05, 127.0 + i * 0.05) for i in range(12)]

    unlimited = vrp.greedy_route(_nodes(items), cluster=0)
    limited = vrp.greedy_route(_nodes(items), cluster=0, time_budget_sec=20 * 60)

    assert unlimited, "예산이 없으면 계속 돈다"
    assert max((r["cum_sec"] for r in limited), default=0) <= 20 * 60
    assert len(limited) < len(unlimited), "예산이 걸리면 덜 돈다"


def test_ilp_respects_supply_demand_and_moves_the_feasible_maximum(step2):
    """ILP는 공급·수요를 넘지 않고, 옮길 수 있는 최대치만큼 옮긴다.

    총 이동량 강제 제약이 없으면 '아무 것도 옮기지 않는 해'가 최적이 된다
    (docs/FORMULATION.md 5장).
    """
    import pulp

    ilp, _vrp = step2
    cluster = pd.DataFrame({
        "station_id": ["P1", "P2", "D1"],
        "pick_qty": [4, 5, 0],
        "drop_qty": [0, 0, 6],
        "lat": [36.30, 36.31, 36.32],
        "lon": [127.38, 127.39, 127.40],
    })

    rows = ilp.solve_cluster_moves(
        cluster, ilp.build_solver(time_limit=60))

    moved = pd.DataFrame(rows)
    assert moved["qty"].sum() == 6, "min(공급 9, 수요 6) = 6대를 옮겨야 한다"
    supplied = moved.groupby("pick_station_id")["qty"].sum()
    assert (supplied <= pd.Series({"P1": 4, "P2": 5})[supplied.index]).all()
    assert moved.groupby("drop_station_id")["qty"].sum().max() <= 6


def test_ilp_returns_nothing_when_one_side_is_missing(step2):
    """Pick만 있고 Drop이 없으면 옮길 곳이 없다 — 크래시가 아니라 빈 계획이다."""
    import pulp

    ilp, _vrp = step2
    cluster = pd.DataFrame({
        "station_id": ["P1"], "pick_qty": [4], "drop_qty": [0],
        "lat": [36.30], "lon": [127.38],
    })

    assert ilp.solve_cluster_moves(
        cluster, ilp.build_solver(time_limit=60)) == []


# ---------------------------------------------------------------- 4. 집행 기준 평가

@pytest.fixture(scope="module")
def step4():
    return _load(PROJECT_ROOT / "step4_metrics" / "imbalance.py", "_imbalance_calc")


def test_executed_delta_signs_pick_negative_and_drop_positive(step4):
    """싣기(pick)는 재고를 줄이고 내리기(drop)는 늘린다. depot 복귀는 무시한다."""
    routes = pd.DataFrame([
        {"to_id": "A", "action": "pick", "qty": 5},
        {"to_id": "B", "action": "drop", "qty": 3},
        {"to_id": "ST0001", "action": "return", "qty": 0},
        {"to_id": "B", "action": "drop", "qty": 2},
    ])

    delta = step4.executed_delta(routes)

    assert delta["A"] == -5
    assert delta["B"] == 5, "같은 대여소를 여러 번 방문하면 합산해야 한다"
    assert "ST0001" not in delta.index, "depot 복귀는 재고 증감이 아니다"


def test_executed_delta_is_empty_without_routes(step4):
    assert step4.executed_delta(pd.DataFrame()).empty


def test_stockout_uses_what_was_moved_not_what_was_planned(step4, tmp_path, monkeypatch):
    """결품 지표는 **계획량이 아니라 실제로 옮긴 양**으로 재야 한다.

    계획량으로 재면 계획이 같고 집행만 다른 방법들의 점수가 전부 같아져
    **방법 간 비교가 불가능**해진다 (docs/EXPERIMENTS.md 5장, docs/TODO.md 1-2).
    """
    # 하루 5시간 동안 시간당 2대씩 빠져나가는 대여소. 재고 0에서 시작한다.
    net = pd.DataFrame({
        "station_id": ["ST0001"],
        "날짜": ["2026-08-17"],
        **{f"net_{hour:02d}": [2] for hour in range(24)},
    })
    candidates = tmp_path / "top.csv"
    pd.DataFrame({"station_id": ["ST0001"], "parking_lot": [20]}).to_csv(
        candidates, index=False, encoding="utf-8")

    monkeypatch.setattr(step4, "load_net_demand", lambda: net.copy())
    monkeypatch.setattr(step4, "file_path", str(candidates))
    # 계획은 10대를 채우라고 했지만 실제로는 4대만 내렸다.
    monkeypatch.setattr(step4, "load_vrp_plan", lambda duration: pd.DataFrame(
        [{"to_id": "ST0001", "action": "drop", "qty": 4}]))

    imbalance_df = pd.DataFrame({
        "station_id": ["ST0001"], "stock": [0], "rebal_qty": [10],
    })
    result = step4.stockout_simulation("_05_10", imbalance_df)

    # 결품은 그 시간의 수요를 뺀 **뒤** 재고가 0 이하인 시간을 센다.
    #   재고 0  → 5시간 모두 결품
    #   재고 4  → 2 → 0 → 0 → 0 → 0  = 4시간 결품 (실제로 옮긴 양)
    #   재고 10 → 8 → 6 → 4 → 2 → 0  = 1시간 결품 (계획대로 다 옮겼다면)
    assert result["stockout_hours_before"] == 5.0
    assert result["stockout_hours_after"] == 4.0
    assert result["stockout_hours_plan"] == 1.0
    assert result["stockout_hours_after"] > result["stockout_hours_plan"], \
        "계획 기준은 편익을 과대평가한다 — 두 값이 같으면 집행량을 안 쓰고 있는 것이다"


def test_stockout_falls_back_to_the_plan_when_no_route_exists(step4, tmp_path, monkeypatch):
    """VRP 결과가 없는 구버전 산출물에서는 계획량으로 물러선다(크래시 금지)."""
    net = pd.DataFrame({
        "station_id": ["ST0001"],
        "날짜": ["2026-08-17"],
        **{f"net_{hour:02d}": [2] for hour in range(24)},
    })
    candidates = tmp_path / "top.csv"
    pd.DataFrame({"station_id": ["ST0001"], "parking_lot": [20]}).to_csv(
        candidates, index=False, encoding="utf-8")

    monkeypatch.setattr(step4, "load_net_demand", lambda: net.copy())
    monkeypatch.setattr(step4, "file_path", str(candidates))
    monkeypatch.setattr(step4, "load_vrp_plan", lambda duration: pd.DataFrame())

    result = step4.stockout_simulation(
        "_05_10", pd.DataFrame({"station_id": ["ST0001"], "stock": [0], "rebal_qty": [10]}))

    assert result["stockout_hours_after"] == result["stockout_hours_plan"] == 1.0


def test_balanced_input_returns_to_depot_exactly_once(step2, capsys):
    """**수급이 맞으면 중간 복귀는 없고, 마지막에 딱 한 번 depot으로 돌아온다.**

    임의 시점에 `남은 drop = 남은 pick + 적재량`이므로
      · 적재가 꽉 차 못 실으면 -> 남은 drop > 0 (내릴 곳이 있다)
      · 적재가 0이라 못 내리면 -> 남은 drop = 남은 pick (있으면 실을 수 있다)
    이라서 중간에 후보가 비는 상태는 생기지 않는다. ILP가 수급을 맞추지 않게
    바뀌면 이 테스트가 먼저 깨져야 한다.

    **1.19.1 이전에는 마지막 복귀도 없었다.** 그 불변식 탓에 `vrp_plan`에 `return`
    행이 0건이었고, 총 이동거리가 33% 과소 추정이라는 사실이 오래 묻혀 있었다
    (docs/TODO.md 1-1).
    """
    import random

    _ilp, vrp = step2
    rng = random.Random(0)

    for _ in range(30):
        total = rng.randint(10, 60)
        picks, drops = [], []
        for bucket in (picks, drops):
            left = total
            while left > 0:
                take = min(left, rng.randint(1, 15))
                bucket.append(take)
                left -= take

        nodes = {}
        for i, qty in enumerate(picks):
            nodes[(f"P{i}", "pick")] = {"qty": qty, "lat": 36.3 + rng.random() * 0.2,
                                        "lon": 127.3 + rng.random() * 0.2}
        for i, qty in enumerate(drops):
            nodes[(f"D{i}", "drop")] = {"qty": qty, "lat": 36.3 + rng.random() * 0.2,
                                        "lon": 127.3 + rng.random() * 0.2}

        route = vrp.greedy_route(nodes, cluster=0)

        returns = [i for i, r in enumerate(route) if r["action"] == "return"]
        assert returns == [len(route) - 1], \
            "복귀는 맨 마지막 한 번뿐이어야 한다 — 중간 복귀가 났다면 적재 논리를 보라"
        assert sum(node["qty"] for node in nodes.values()) == 0, "작업이 남았다"
        assert route[-1]["to_id"] == DEPOT_ID, "마지막 도착지가 차고지가 아니다"
        assert route[-1]["qty"] == 0 and route[-1]["work_sec"] == 0, \
            "복귀 구간에서 자전거를 옮기면 안 된다"
    capsys.readouterr()


def test_unbalanced_input_does_hit_the_return_branch(step2, capsys):
    """반대로 수급이 안 맞으면 그 분기가 실제로 걸린다.

    대조군 B1(군집·ILP 없이 한 대가 훑는 방식)이 이 경로를 쓴다 —
    죽은 코드처럼 보여도 지우면 안 되는 이유다.
    """
    _ilp, vrp = step2
    nodes = {
        ("P1", "pick"): {"qty": 3, "lat": 36.30, "lon": 127.38},
        ("D1", "drop"): {"qty": 3, "lat": 36.31, "lon": 127.39},
        ("D2", "drop"): {"qty": 9, "lat": 36.32, "lon": 127.40},   # 받을 자전거가 없다
    }

    route = vrp.greedy_route(nodes, cluster=0)

    assert any(r["action"] == "return" for r in route)
    assert nodes[("D2", "drop")]["qty"] > 0, "공급이 부족하면 일부는 남는다"
    capsys.readouterr()


# ---------------------------------------------------------------- 5. ILP·VRP 방어

def test_ilp_rounds_solver_values_instead_of_truncating(step2, monkeypatch, capsys):
    """솔버가 4.999999999를 돌려줘도 자전거를 잃지 않는다.

    정수변수라도 솔버·버전에 따라 값이 미세하게 어긋날 수 있는데, `int()`는 0 방향으로
    잘라 **조용히 대수를 깎는다.** 현재 CBC는 정확한 값을 주지만 PuLP 4.0에서
    `PULP_CBC_CMD`가 사라지므로 솔버가 바뀐다 (docs/TODO.md).
    """
    import pulp

    ilp, _vrp = step2
    cluster = pd.DataFrame({
        "station_id": ["P1", "P2", "D1", "D2"],
        "pick_qty": [5, 5, 0, 0],
        "drop_qty": [0, 0, 6, 4],
        "lat": [36.30, 36.31, 36.32, 36.33],
        "lon": [127.38, 127.39, 127.40, 127.41],
    })

    original = pulp.value
    monkeypatch.setattr(ilp.pulp, "value",
                        lambda v: (lambda r: r - 1e-9 if r else r)(original(v)))

    rows = ilp.solve_cluster_moves(cluster, ilp.build_solver(time_limit=60))

    assert sum(r["qty"] for r in rows) == 10, "10대를 옮겨야 하는데 절단으로 깎였다"
    assert "계획 합계가 강제 이동량과 다릅니다" not in capsys.readouterr().out


def test_vrp_rejects_duplicate_stations(step2, tmp_path, monkeypatch):
    """후보 파일에 대여소가 중복되면 좌표 조회가 Series를 돌려줘 거리 계산이 망가진다.

    조용히 틀린 결과를 내느니 즉시 멈춘다.
    """
    _ilp, vrp = step2
    candidates = tmp_path / "top.csv"
    pd.DataFrame({
        "station_id": ["ST0001", "ST0001", "ST0002"],
        "lat": [36.30, 36.30, 36.31], "lon": [127.38, 127.38, 127.39],
    }).to_csv(candidates, index=False, encoding="utf-8")
    monkeypatch.setattr(vrp, "metrics_file", str(candidates))

    plan = pd.DataFrame([{"cluster": 0, "pick_station_id": "ST0001",
                          "drop_station_id": "ST0002", "qty": 3}])

    with pytest.raises(SystemExit, match="중복된 대여소"):
        vrp.run_vrp_plan(plan, "_05_10")


def test_vrp_rejects_stations_missing_from_the_candidate_file(step2, tmp_path, monkeypatch):
    """실행 라벨이 어긋나 ILP 계획의 대여소가 후보 파일에 없으면 즉시 멈춘다."""
    _ilp, vrp = step2
    candidates = tmp_path / "top.csv"
    pd.DataFrame({"station_id": ["ST0001"], "lat": [36.30], "lon": [127.38]}).to_csv(
        candidates, index=False, encoding="utf-8")
    monkeypatch.setattr(vrp, "metrics_file", str(candidates))

    plan = pd.DataFrame([{"cluster": 0, "pick_station_id": "ST0001",
                          "drop_station_id": "ST9999", "qty": 3}])

    with pytest.raises(SystemExit, match="후보 파일에 없습니다"):
        vrp.run_vrp_plan(plan, "_05_10")


def test_work_time_constants_come_from_project_config(step2):
    """작업시간은 다른 운영 상수와 같이 project_config 한 곳에서 읽는다.

    1.18.6 이전에는 vrp.py에 30.0으로 박혀 있어 환경변수로 조정할 수 없었다.
    """
    import project_config

    _ilp, vrp = step2
    assert vrp.PICK_TIME_SEC is project_config.PICK_TIME_SEC
    assert vrp.DROP_TIME_SEC is project_config.DROP_TIME_SEC
    assert vrp.VEHICLE_SPEED_KMPH is project_config.VEHICLE_SPEED_KMPH


def test_solver_factory_survives_pulp4_removing_the_legacy_solver(step2, monkeypatch):
    """`PULP_CBC_CMD`가 사라져도 `COIN_CMD`로 넘어간다.

    PuLP 4.0에서 `PULP_CBC_CMD`가 없어지는데 `requirements.txt`는 하한 고정이라,
    그날 새 환경에서 설치하면 step2가 통째로 깨진다 (docs/TODO.md P2-B).
    """
    ilp, _vrp = step2

    monkeypatch.delattr(ilp.pulp, "PULP_CBC_CMD", raising=False)
    calls = []

    class FakeCoin:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def available(self):
            return True

    monkeypatch.setattr(ilp.pulp, "COIN_CMD", FakeCoin)

    solver = ilp.build_solver(time_limit=60, gap_rel=0.01)

    assert isinstance(solver, FakeCoin)
    assert calls[0]["timeLimit"] == 60 and calls[0]["gapRel"] == 0.01


def test_solver_factory_says_what_to_install_when_nothing_is_available(step2, monkeypatch):
    """CBC가 하나도 없으면 **무엇을 설치해야 하는지 알려 주고 멈춘다.**

    AttributeError로 죽으면 원인을 찾는 데 시간이 든다.
    """
    ilp, _vrp = step2

    monkeypatch.delattr(ilp.pulp, "PULP_CBC_CMD", raising=False)
    monkeypatch.setattr(ilp.pulp, "COIN_CMD",
                        lambda **kwargs: type("X", (), {"available": lambda self: False})())
    monkeypatch.setitem(sys.modules, "cbcbox", None)   # import 시 ImportError

    with pytest.raises(SystemExit, match="pip install pulp\[cbc\]"):
        ilp.build_solver()


def test_pipeline_uses_the_solver_factory_everywhere(step2):
    """솔버 설정이 흩어지면 실험과 파이프라인이 다른 조건으로 풀게 된다."""
    import inspect

    ilp, _vrp = step2
    source = inspect.getsource(ilp)
    body = source[source.index("if __name__"):]
    assert "build_solver(" in body
    assert "pulp.PULP_CBC_CMD(" not in body, "main에서 솔버를 직접 만들지 마라"


# ---------------------------------------------------------------- 6. 단계 간 계약

def test_require_columns_names_what_is_missing_and_where():
    """단계 간 표에 컬럼이 빠지면 **즉시, 어디가 문제인지 밝히며** 멈춘다.

    단계들이 CSV로 통신하므로 이름이 하나만 어긋나도 뒤 단계가 엉뚱한 자리에서
    죽거나 조용히 틀린 값을 낸다.
    """
    from project_config import require_columns

    frame = pd.DataFrame({"station_id": ["ST0001"], "lat": [36.3]})

    with pytest.raises(SystemExit) as caught:
        require_columns(frame, ["station_id", "lat", "lon", "rebal_qty"], "step1 후보 _05_10")

    message = str(caught.value)
    assert "lon" in message and "rebal_qty" in message
    assert "step1 후보 _05_10" in message, "어느 산출물인지 알려 줘야 한다"


def test_require_columns_passes_the_frame_through():
    from project_config import require_columns

    frame = pd.DataFrame({"a": [1]})
    assert require_columns(frame, ["a"], "테스트") is frame


def test_vehicle_count_follows_the_workload(step1):
    """**차량 수는 이번 회차의 작업량이 정한다.**

    1.19.1 이전에는 `ceil(대상 수 / 7)`이었고, 7에 근거가 없었을뿐더러 실데이터에서는
    늘 회차당 상한(10)에 걸려 **사실상 10대 고정**이었다(실측 3회차 모두 희망 12~13).

    추정식은 시간이다: 처리 대수 × (싣기+내리기) + 대여소 수 × 이동계수,
    거기에 불균형 여유를 곱해 시간 예산으로 나눈다.
    """
    import project_config

    def candidates(stations, bikes):
        """대여소 stations곳, 처리 대수 bikes대인 후보 집합(pick/drop 균형)."""
        half = stations // 2
        each = bikes / half
        return pd.DataFrame({
            "station_id": [f"ST{i:04d}" for i in range(half * 2)],
            "rebal_qty": [each] * half + [-each] * half,
        })

    light = step1.wanted_vehicles(candidates(20, 60))
    heavy = step1.wanted_vehicles(candidates(90, 320))

    assert light < heavy, "작업이 많은 회차에 더 많은 차량이 나가야 한다"
    assert light >= 1

    # 실측(26년 03월 _05_10: 91곳·320대)에서 18대면 예산 초과 0건이었다.
    assert 15 <= heavy <= 19, f"실측이 요구한 대수와 크게 어긋난다: {heavy}"

    # 계수를 손대면 추정도 따라 움직여야 한다 — 상수가 코드에 박혀 있으면 안 된다.
    slower = step1.wanted_vehicles(candidates(90, 320))
    assert slower == heavy
    assert project_config.TRAVEL_MIN_PER_STATION > 0
    assert project_config.CLUSTER_IMBALANCE_ALLOWANCE >= 1

    assert step1.wanted_vehicles(pd.DataFrame(columns=["rebal_qty"])) == 1, \
        "대상이 없으면 1대로 떨어져야 한다(0으로 나누면 안 된다)"


def test_step1_thresholds_come_from_project_config():
    """작업 대상 임계·상위 컷·조정 반복이 코드에 박혀 있으면 안 된다.

    1.18.8 이전에는 `> 2`, `iloc[:50]`, `target_cluster_size=7`이 그대로 박혀 있어
    환경변수로 바꿀 수 없었고, 논문 3장 기호표에 근거 없이 등장했다.
    (`target_cluster_size`는 1.19.1에서 아예 사라졌다 — 아래 참고.)
    """
    import project_config

    source = (PROJECT_ROOT / "step1_cluster" / "top_st_clustering.py").read_text(encoding="utf-8")
    assert "iloc[:50" not in source, "상위 컷이 코드에 박혀 있다"
    assert "rebal_qty']) > 2]" not in source, "작업 대상 임계가 코드에 박혀 있다"
    assert "MAX_ITER = 200" not in source, "조정 반복 상한이 코드에 박혀 있다"
    assert (project_config.REBAL_MIN_QTY,
            project_config.TOP_STATION_LIMIT) == (2, 50), "기본값이 바뀌었다"
    assert (project_config.ADJUST_MAX_ITER, project_config.ADJUST_BALANCE_OK,
            project_config.ADJUST_BALANCE_LIMIT) == (200, 3, 5), "기본값이 바뀌었다"
    assert not hasattr(project_config, "TARGET_CLUSTER_SIZE"), \
        "군집 크기 상수가 되살아났다 — 차량 수는 작업량으로 정한다(wanted_vehicles)"


def test_save_message_shows_the_z_actually_used(target_qty, tmp_path, monkeypatch, capsys):
    """저장 로그에 **실제로 쓴 z**가 찍혀야 한다.

    z를 안 넘기면 compute_rebal_qty가 TARGET_Z로 채우는데, 로그는 넘겨받은
    인자를 그대로 찍어 'mu + None·sigma'가 됐다. z 실험 중에 어떤 값으로
    돌았는지 로그만 봐서는 알 수 없었다(계산 자체는 맞았다).
    """
    import project_config

    monkeypatch.setattr(target_qty, "out_file_path", str(tmp_path / "rebal{duration} ({now})"))
    monkeypatch.setattr(target_qty.db, "save_output", lambda *a, **k: None)

    target_qty.calculate_rebal_qty(
        _stats(mu=[10.0], sigma=[2.0], stock=[5], parking_lot=[100]),
        duration="_05_10", now="테스트")

    message = capsys.readouterr().out
    assert f"mu + {project_config.TARGET_Z}·sigma" in message
    assert "None" not in message


def test_duration_input_is_checked_against_the_real_list():
    """시간대는 네 창이 전부다. 맨 앞 밑줄이 빠진 값은 여기서 걸러야 한다.

    1.17.3 이전에는 폼 예시가 `10_15`였고, 그대로 입력한 사용자가 한참 뒤
    step4 duration_hours()에서 크래시를 봤다.
    """
    import project_config

    assert project_config.DURATIONS == ("_05_10", "_10_15", "_15_20", "_20_05")
    assert set(project_config.DURATION_LABELS) == set(project_config.DURATIONS)

    # 리스트(체크박스)도 콤마 문자열도 받고, 순서는 늘 하루 흐름 순으로 되돌린다.
    assert project_config.normalize_durations(["_15_20", "_05_10"]) == "_05_10,_15_20"
    assert project_config.normalize_durations("_10_15,_10_15") == "_10_15"
    assert project_config.normalize_durations([]) == ""

    for bad in ("10_15", "_05_09", "all"):
        with pytest.raises(ValueError, match="시간대는"):
            project_config.normalize_durations(bad)


def test_period_default_follows_the_data_we_have(tmp_path, monkeypatch):
    """순수요 기간 기본값은 **가진 것 중 가장 최근 달**이다.

    달이 바뀔 때마다 사람이 상수를 고쳐 넣게 두면 곧 낡은 달로 계획하게 된다.
    """
    import project_config

    monkeypatch.setattr(project_config, "NET_DEMAND_DIR", tmp_path)
    assert project_config.available_periods() == ()
    assert project_config.latest_period() == project_config.FALLBACK_PERIOD

    for label in ("26년 01월", "25년 11월", "26년 03월"):
        (tmp_path / f"st_net_daily ({label}).csv").write_text("x", encoding="utf-8")
    (tmp_path / "st_net_daily (엉뚱한 이름).csv").write_text("x", encoding="utf-8")

    assert project_config.available_periods() == ("25년 11월", "26년 01월", "26년 03월")
    assert project_config.latest_period() == "26년 03월"

    assert project_config.normalize_period("25년 11월") == "25년 11월"
    assert project_config.normalize_period("  ") == ""
    with pytest.raises(ValueError, match="순수요가 없습니다"):
        project_config.normalize_period("25년 12월")
    with pytest.raises(ValueError, match="표기"):
        project_config.normalize_period("2025-11")


def test_candidate_glob_does_not_catch_other_outputs():
    """후보 파일 글롭이 `top_center*.csv` 같은 다른 산출물을 잡으면 안 된다.

    `top*.csv`로 두면 mtime이 더 최신인 엉뚱한 파일을 `/api/stations`가 읽는다.
    """
    import fnmatch

    from webapp.store import CSV_FALLBACK

    _subdir, pattern = CSV_FALLBACK["pick_drop"]
    assert fnmatch.fnmatch("top_05_10 (2026-08-11 real).csv", pattern)
    assert not fnmatch.fnmatch("top_center_05_10 (2026-08-11 real).csv", pattern)
