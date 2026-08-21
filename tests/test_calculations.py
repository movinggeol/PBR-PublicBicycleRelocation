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
    CLUSTER_ALPHA, CLUSTER_BETA, CLUSTER_GAMMA, PROJECT_ROOT, TARGET_Z, VEHICLE_CAPACITY,
)

STEP0 = PROJECT_ROOT / "step0_collect"
STEP1 = PROJECT_ROOT / "step1_cluster"
STEP2 = PROJECT_ROOT / "step2_optimize"


def _load(path, name):
    """step 모듈을 경로로 직접 읽는다.

    테스트마다 독립된 이름으로 올려 import 캐시를 공유하지 않게 한다.
    `1.top_st_clustering.py`는 숫자로 시작해 애초에 일반 import가 안 된다.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def target_qty():
    return _load(STEP0 / "calculate_target_qty.py", "_calc_target")


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
        cluster, pulp.PULP_CBC_CMD(msg=False, timeLimit=60, gapRel=0.02))

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
        cluster, pulp.PULP_CBC_CMD(msg=False, timeLimit=60)) == []


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


def test_balanced_input_never_needs_a_mid_route_return(step2, capsys):
    """**수급이 맞으면 중간 복귀 분기는 실행될 수 없다** — ILP가 그렇게 맞춰 준다.

    임의 시점에 `남은 drop = 남은 pick + 적재량`이므로
      · 적재가 꽉 차 못 실으면 -> 남은 drop > 0 (내릴 곳이 있다)
      · 적재가 0이라 못 내리면 -> 남은 drop = 남은 pick (있으면 실을 수 있다)
    이라서 후보가 비는 상태가 생기지 않는다.

    이 불변식 때문에 `vrp_plan`에 `return` 행이 0건이고, **마지막 depot 복귀가
    빠져 있다는 사실이 오래 드러나지 않았다** (docs/TODO.md 1-1).
    ILP가 수급을 맞추지 않게 바뀌면 이 테스트가 먼저 깨져야 한다.
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

        assert not any(r["action"] == "return" for r in route), \
            "수급이 맞는데 중간 복귀가 났다 — 적재 논리가 바뀌었는지 확인하라"
        assert sum(node["qty"] for node in nodes.values()) == 0, "작업이 남았다"
        assert route[-1]["action"] != "return", \
            "마지막 행이 복귀다 — 최종 복귀가 구현됐다면 TODO 1-1과 문서를 갱신하라"
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
