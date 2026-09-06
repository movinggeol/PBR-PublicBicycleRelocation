"""단계별 계산 로직의 단위 테스트 (docs/기록/TODO.md P3-11).

기존 테스트는 **스모크 수준**이었습니다 — 합성 데이터로 파이프라인을 끝까지 돌려
산출물이 나오는지, 스키마가 맞는지를 봤습니다. 그래서 "돌긴 도는데 값이 틀린" 종류의
회귀는 잡지 못했습니다.

이 파일은 **계산 자체**를 봅니다. 세 곳이 대상입니다.

  1. 목표 재고·재배치량 공식  (`calculate_target_qty.compute_rebal_qty`, `build_stats`)
  2. 군집 조정 목적함수        (`adjust_module.compute_objective`)
  3. VRP 적재·시간 제약        (`vrp.greedy_route`), ILP 수급 제약 (`ilp.solve_cluster_moves`)

수식의 근거는 [docs/분석/FORMULATION.md](../docs/분석/FORMULATION.md)에 있습니다.
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
    12개월 백테스트로 정한 1.99다 (docs/분석/EXPERIMENTS.md 1장).
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
    """
    frame = target_qty.compute_rebal_qty(
        _stats(mu=[999.0, 0.0], sigma=[0.0, 0.0],
               stock=[0, 999], parking_lot=[9999, 9999]), z=2.0)

    assert frame["rebal_qty"].abs().max() <= VEHICLE_CAPACITY
    assert frame["rebal_qty"].tolist() == [VEHICLE_CAPACITY, -VEHICLE_CAPACITY]


def test_rebal_qty_never_fills_a_van_in_the_real_range(target_qty):
    """⚠️ 위 시험은 **실무에 없는 규모**(999대)로 잰다 — 그래서 통과한다.

    `10·tanh(x/10)`은 10에 점근할 뿐이라, floor를 거쳐 10이 나오려면
    float64에서 tanh가 정확히 1.0이 되는 지점, 곧 원값이 **190대 이상**이어야
    한다. 실산출물 10,740행의 |target−stock| 최댓값은 55.6대이고 문턱을 넘은
    대여소는 **0곳**이다 — 실제 |rebal_qty|는 정확히 9에서 끊긴다
    (분포 {4:25, 5:135, 6:129, 7:70, 8:66, 9:99}).

    즉 `VEHICLE_CAPACITY = 10`을 읽고 "한 대여소에서 10대까지 계획된다"고
    이해하면 틀린다. 위 시험만 있으면 그 오해가 **검사를 통과한 상태로**
    남는다(1.26.129에서 구역 B 검토가 잡았다).

    하드캡(clip)으로 바꾸면 계획 작업량이 12.9% 늘어난다 — 관제센터 확인이
    "통상 7대, 많이 실어야 10대"이므로 바꾸지 않는다. 여기서 지키는 것은
    **그 사실이 코드와 로그에 드러나 있는가**다.
    """
    현실적_최대_필요량 = 56.0        # 실산출물 관측 최댓값(55.6)보다 살짝 위
    frame = target_qty.compute_rebal_qty(
        _stats(mu=[현실적_최대_필요량, 0.0], sigma=[0.0, 0.0],
               stock=[0, 현실적_최대_필요량], parking_lot=[9999, 9999]), z=0.0)

    assert frame["rebal_qty"].abs().max() == VEHICLE_CAPACITY - 1, \
        "실무 범위에서 용량을 꽉 채우게 됐다면 tanh를 바꾼 것이다 — 문서와 로그도 함께 고쳐라"


def test_실효_최댓값을_로그로_알린다():
    """문서를 읽어야만 피할 수 있는 함정은 도구가 스스로 알리게 한다.

    `VEHICLE_CAPACITY`만 보고 10을 기대하지 않도록, 저장할 때마다 그 실행에서
    실제로 나온 한 대여소 최대 계획량을 찍는다.
    """
    path = PROJECT_ROOT / "step0_collect" / "calculate_target_qty.py"
    body = "\n".join(l for l in path.read_text(encoding="utf-8").splitlines()
                     if not l.lstrip().startswith("#"))
    assert "한 대여소 최대 계획량" in body, "실효 최댓값을 알리지 않는다"
    assert "닿지 않습니다" in body, "용량에 닿지 못한다는 사실을 말하지 않는다"


def test_rebal_qty_is_target_minus_stock_in_every_case(target_qty):
    """작업량은 `target_qty − stock` 하나다 — 부호가 pick/drop을 말한다.

    1.26.129까지 `cond_pos × pick_mask`로 네 갈래를 갈라 놓고 **네 갈래가 전부
    같은 식**을 쓰고 있었다(12줄). 주석만 pick/drop으로 갈라져 있어서, 읽는
    사람은 경우마다 다른 계산을 한다고 오해한다.
    """
    frame = target_qty.compute_rebal_qty(
        # mu 양수/음수 × 재고 과잉/부족 네 경우를 모두 담는다.
        _stats(mu=[4.0, 4.0, -4.0, -4.0], sigma=[0.0, 0.0, 0.0, 0.0],
               stock=[0, 20, 2, 30], parking_lot=[100, 100, 100, 100]), z=0.0)

    # 경우를 가르지 않고 한 식으로 다시 계산해 본다 (차이 → tanh 포화 → 0 방향 정수화).
    직접 = (frame["target_qty"] - frame["stock"]).to_numpy()
    눌린 = VEHICLE_CAPACITY * np.tanh(직접 / VEHICLE_CAPACITY)
    기대 = np.where(눌린 >= 0, np.floor(눌린), np.ceil(눌린)).astype(int)
    assert frame["rebal_qty"].tolist() == 기대.tolist()


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
    docs/분석/EXPERIMENTS.md 4장)
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
    대조군 실험처럼 한 대가 넓은 범위를 훑을 때만 쓴다 — docs/기록/TODO.md 1-1.
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
    (docs/분석/FORMULATION.md 5장).
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
    **방법 간 비교가 불가능**해진다 (docs/분석/EXPERIMENTS.md 5장, docs/기록/TODO.md 1-2).
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
    (docs/기록/TODO.md 1-1).
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
    `PULP_CBC_CMD`가 사라지므로 솔버가 바뀐다 (docs/기록/TODO.md).
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
    그날 새 환경에서 설치하면 step2가 통째로 깨진다 (docs/기록/TODO.md P2-B).
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

    with pytest.raises(SystemExit, match=r"pip install pulp\[cbc\]"):
        ilp.build_solver()


def test_both_cbc_solvers_give_the_same_plan(step2):
    """`PULP_CBC_CMD`와 `COIN_CMD`가 **같은 이동 계획**을 내야 한다.

    PuLP 4.0으로 넘어가면 솔버가 바뀌는데, 그때 계획이 달라지면 **문서의 모든
    수치가 무효가 된다.** `build_solver()`의 폴백은 "돌아가기는 한다"만 지킬 뿐
    답이 같은지는 지키지 않아, 이 테스트로 못박는다.

    ⚠️ **답이 같을 것이 당연하지 않다.** 이 ILP는 수송문제라 최적해가 여럿인
    경우가 흔하고, 솔버가 다르면 **같은 목적값의 다른 꼭짓점**을 고를 수 있다.
    실제로 인공 예제(x+y 최소화)에서는 두 솔버가 다른 해를 냈다. 파이프라인
    자료에서는 네 회차 모두 글자 그대로 같았지만(1.26.98), 그것은 **측정된
    사실이지 보장이 아니다** — 그래서 검사로 남긴다.

    cbcbox가 없으면 건너뛴다(149MB라 기본 설치가 아니다).
    """
    ilp, _vrp = step2
    cbcbox = pytest.importorskip("cbcbox", reason="cbcbox 미설치 (pip install cbcbox)")

    legacy = getattr(ilp.pulp, "PULP_CBC_CMD", None)
    if legacy is None:
        pytest.skip("PuLP 4.0 — 비교할 구 솔버가 없다")

    cluster = pd.DataFrame([
        {"station_id": "A", "pick_qty": 6, "drop_qty": 0, "lat": 36.35, "lon": 127.38},
        {"station_id": "B", "pick_qty": 4, "drop_qty": 0, "lat": 36.36, "lon": 127.40},
        {"station_id": "C", "pick_qty": 0, "drop_qty": 7, "lat": 36.34, "lon": 127.39},
        {"station_id": "D", "pick_qty": 0, "drop_qty": 3, "lat": 36.37, "lon": 127.41},
    ])

    def plan_with(solver):
        moves = ilp.solve_cluster_moves(cluster.copy(), solver)
        return sorted((m["pick_station_id"], m["drop_station_id"], m["qty"])
                      for m in moves)

    old = plan_with(legacy(msg=False, timeLimit=60, gapRel=0.0))
    new = plan_with(ilp.pulp.COIN_CMD(path=cbcbox.cbc_bin_path(), msg=False,
                                      timeLimit=60, gapRel=0.0))

    assert old == new, (
        "두 CBC가 다른 계획을 냈다 — PuLP 4.0 전환이 문서의 수치를 바꾼다."
        f" PULP_CBC_CMD={old} / COIN_CMD={new}")


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


def test_geo_wanted_vehicles_reacts_to_spread(step1):
    """`geo=True`는 대여소 수가 같아도 **흩어진 정도**로 필요 대수를 바꿔야 한다.

    legacy(`geo=False`, 기본값)는 대여소 수에만 비례해 거리를 전혀 보지 않는다
    (docs/기록/TODO.md 2-1, EXPERIMENTS.md 5-G ㉰). geo는 이 맹점을 고치는
    다음 단계이므로, 같은 개수·같은 처리 대수라도 좌표가 넓게 퍼져 있으면
    더 많은 차량을 요구해야 한다.
    """
    import project_config

    def candidates(n, bikes, lat, lon):
        half = n // 2
        each = bikes / half
        return pd.DataFrame({
            "station_id": [f"ST{i:04d}" for i in range(n)],
            "rebal_qty": [each] * half + [-each] * half,
            "lat": lat, "lon": lon,
        })

    depot_lat, depot_lon = project_config.DEPOT_LAT, project_config.DEPOT_LON
    tight_lat = [depot_lat + 0.001 * i for i in range(60)]
    tight_lon = [depot_lon + 0.001 * i for i in range(60)]
    spread_lat = [depot_lat + 0.05 * i for i in range(60)]
    spread_lon = [depot_lon + 0.05 * i for i in range(60)]

    tight = candidates(60, 200, tight_lat, tight_lon)
    spread = candidates(60, 200, spread_lat, spread_lon)

    legacy_tight = step1.wanted_vehicles(tight, geo=False)
    legacy_spread = step1.wanted_vehicles(spread, geo=False)
    assert legacy_tight == legacy_spread, \
        "legacy는 거리를 안 보므로 뭉침·흩어짐이 같은 값을 내야 한다"

    geo_tight = step1.wanted_vehicles(tight, geo=True)
    geo_spread = step1.wanted_vehicles(spread, geo=True)
    assert geo_spread > geo_tight, \
        "흩어진 회차는 이동거리가 커지므로 더 많은 차량이 필요해야 한다"
    assert geo_tight >= 1

    assert step1.wanted_vehicles(
        pd.DataFrame(columns=["rebal_qty", "lat", "lon"]), geo=True) == 1, \
        "대상이 없으면 geo도 1대로 떨어져야 한다"


def test_geo_wanted_vehicles_follows_project_config_flag(step1, monkeypatch):
    """`geo` 인자를 생략하면 `project_config.WANTED_VEHICLES_GEO`를 따라야 한다.

    이 플래그는 함수 안에서 **호출 시점에** `project_config.WANTED_VEHICLES_GEO`를
    읽는다(모듈 상수를 그대로 import하면 몽키패치가 반영되지 않는다 — 같은
    저장소의 `USE_ROAD_MODEL` + `travel_seconds()` 패턴을 따른 것이다).
    """
    import project_config

    df = pd.DataFrame({
        "station_id": ["ST0001", "ST0002"],
        "rebal_qty": [5.0, -5.0],
        "lat": [project_config.DEPOT_LAT, project_config.DEPOT_LAT + 0.1],
        "lon": [project_config.DEPOT_LON, project_config.DEPOT_LON + 0.1],
    })

    monkeypatch.setattr(project_config, "WANTED_VEHICLES_GEO", False)
    assert step1.wanted_vehicles(df) == step1.wanted_vehicles(df, geo=False)

    monkeypatch.setattr(project_config, "WANTED_VEHICLES_GEO", True)
    assert step1.wanted_vehicles(df) == step1.wanted_vehicles(df, geo=True)


def test_select_top_unbalanced_st_is_column_order_independent(step1, tmp_path):
    """`select_top_unbalanced_st()`는 입력 CSV의 **컬럼 순서**가 바뀌어도 같은 값을 내야 한다.

    예전 코드는 병합 결과를 위치(`iloc[:, [0,7,8,9,3,4,5,6,1,2]]`)로 골랐다
    (docs/기록/TODO.md P3-0). step0가 항상 같은 순서로 써서 우연히 맞았을
    뿐이라, 컬럼 순서가 바뀌면 **조용히 엉뚱한 값**을 쓰게 된다. 이제는
    이름으로 고르므로 순서를 뒤섞어도 같은 결과가 나와야 한다.
    """
    # pick·drop 물량이 같아야 둘 다 살아남는다 — select_top_unbalanced_st()가
    # cut_point(= min(pick 총량, drop 총량))로 큰 쪽을 깎기 때문이다.
    rebal = pd.DataFrame({
        "station_id": ["ST0001", "ST0002", "ST0003"],
        "mu": [1.0, -2.0, 0.5],
        "sigma": [0.5, 0.3, 0.2],
        "parking_lot": [20, 15, 10],
        "stock": [5, 12, 8],
        "target_qty": [10.0, 3.0, 6.0],
        "rebal_qty": [5.0, -5.0, -2.0],
    })
    st_info = pd.DataFrame({
        "station_id": ["ST0001", "ST0002", "ST0003"],
        "station_name": ["가", "나", "다"],
        "lat": [36.1, 36.2, 36.3],
        "lon": [127.1, 127.2, 127.3],
        "parking_lot": [99, 99, 99],   # st_info 쪽 값 — rebal 쪽(20/15/10)이 이겨야 한다
        "stock": [99, 99, 99],
    })

    def run(rebal_df, st_df, name):
        path = tmp_path / f"rebal_{name}.csv"
        rebal_df.to_csv(path, index=False, encoding="utf-8")
        return step1.select_top_unbalanced_st(str(path), "_05_10", st_df)

    normal = run(rebal, st_info, "normal")

    shuffled_rebal = rebal[["sigma", "rebal_qty", "station_id", "target_qty",
                            "parking_lot", "mu", "stock"]]
    shuffled_st = st_info[["stock", "lon", "station_id", "parking_lot",
                           "lat", "station_name"]]
    shuffled = run(shuffled_rebal, shuffled_st, "shuffled")

    assert list(normal.columns) == [
        "station_id", "station_name", "lat", "lon", "parking_lot", "stock",
        "target_qty", "rebal_qty", "mu", "sigma"]
    assert normal.equals(shuffled), "입력 컬럼 순서를 뒤섞으면 결과가 달라진다"

    # rebal_qty가 REBAL_MIN_QTY(기본 2) 이하인 ST0003은 걸러져야 한다
    assert set(normal["station_id"]) == {"ST0001", "ST0002"}
    # parking_lot·stock은 st_info가 아니라 rebal 쪽 값을 써야 한다
    row = normal.set_index("station_id").loc["ST0001"]
    assert row["parking_lot"] == 20 and row["stock"] == 5


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


def test_route_extras_counts_empty_running_and_returns(step4, tmp_path, monkeypatch):
    """공차 이동은 **도착 전 적재량**으로 판단한다 (docs/분석/KPI.md D장).

    차고지에서 첫 대여소로 가는 구간과 복귀 구간은 늘 빈 차다. 도착 후 적재량으로
    세면 첫 구간이 '실은 채로 달렸다'가 되어 비율이 낮게 나온다.
    """
    routes = pd.DataFrame([
        # depot -> A(싣기 5): 빈 차로 10km
        (0, "pick", 5, 10.0, 600.0, 150.0, 750.0),
        # A -> B(내리기 5): 실은 채로 4km
        (0, "drop", 5, 4.0, 240.0, 150.0, 1140.0),
        # B -> depot: 빈 차로 6km
        (0, "return", 0, 6.0, 360.0, 0.0, 1500.0),
    ], columns=["cluster", "action", "qty", "distance_km", "travel_sec",
                "work_sec", "cum_sec"])

    path = tmp_path / "vrp.csv"
    routes.to_csv(path, index=False, encoding="utf-8")
    monkeypatch.setattr(step4, "vrp_plan_file", str(tmp_path / "vrp.csv"))
    monkeypatch.setattr(step4, "now", "")

    extras = step4.route_extras("")

    assert extras["depot_returns"] == 1, "1.19.1부터 클러스터당 복귀 1건이 정상이다"
    # (10 + 6) / 20 = 0.8
    assert extras["empty_distance_ratio"] == pytest.approx(0.8)
    # 이동 1200초 / 총 1500초
    assert extras["travel_time_ratio"] == pytest.approx(0.8)
    # pick 5대 / 25분
    assert extras["bikes_per_minute"] == pytest.approx(5 / 25)


def test_route_extras_is_silent_when_it_cannot_measure(step4, tmp_path, monkeypatch):
    """산출물이 없거나 구버전이면 지표를 **빼고** 넘긴다 — 0으로 지어내지 않는다."""
    monkeypatch.setattr(step4, "vrp_plan_file", str(tmp_path / "없는파일.csv"))
    monkeypatch.setattr(step4, "now", "")
    assert step4.route_extras("") == {}

    old = pd.DataFrame({"cluster": [0], "action": ["pick"], "qty": [1]})
    old.to_csv(tmp_path / "vrp.csv", index=False, encoding="utf-8")
    monkeypatch.setattr(step4, "vrp_plan_file", str(tmp_path / "vrp.csv"))
    assert step4.route_extras("") == {}, "거리·시간 컬럼이 없는 구버전"


def test_station_coverage_needs_the_collected_station_list(step4, tmp_path, monkeypatch):
    """전체 대여소 수를 모르면 커버리지를 내지 않는다."""
    monkeypatch.setattr(step4, "st_info_file", str(tmp_path / "없는파일.csv"))
    monkeypatch.setattr(step4, "now", "")
    assert step4.station_coverage(87) == {}

    pd.DataFrame({"station_id": [f"ST{i:04d}" for i in range(100)]}).to_csv(
        tmp_path / "st.csv", index=False, encoding="utf-8")
    monkeypatch.setattr(step4, "st_info_file", str(tmp_path / "st.csv"))

    assert step4.station_coverage(87) == {"stations_total": 100,
                                         "station_coverage": pytest.approx(0.87)}


def test_plan_quantity_is_squashed_toward_the_truck_capacity(target_qty):
    """계획량은 격차 그대로가 아니라 `Q·tanh(격차/Q)`로 눌린다.

    **이 압축 때문에 '목표 도달'은 실행 품질을 재지 못한다.** 격차가 8대만 넘어도
    도달이 구조적으로 불가능하다 — 아무리 잘 집행해도 오르지 않는 값이라
    1.19.7에서 헤드라인에서 내렸다(docs/분석/KPI.md).

    tanh 자체는 문서화된 설계다(FORMULATION 3장). 여기서는 **그 성질**을 못 박아,
    나중에 누가 clip으로 바꾸면 이 테스트가 먼저 알려 주도록 한다.
    """
    import project_config

    gaps = np.array([3, 5, 7, 8, 10, 20, 50], dtype=float)
    squashed = target_qty.MAX_CAPACITY * np.tanh(gaps / target_qty.MAX_CAPACITY)

    assert target_qty.MAX_CAPACITY == project_config.VEHICLE_CAPACITY, \
        "계획량 제한이 차량 적재 용량과 따로 놀면 안 된다"

    planned = np.floor(squashed).astype(int)
    # 격차 7까지는 1대 차이로 따라붙지만, 8부터는 벌어지기 시작한다.
    assert (gaps[2] - planned[2]) <= 1, "격차 7은 도달 가능 범위여야 한다"
    assert (gaps[4] - planned[4]) >= 2, "격차 10은 한 번에 못 메운다"
    # 아무리 격차가 커도 적재 용량을 넘지 않는다(점근).
    assert planned.max() < target_qty.MAX_CAPACITY


def test_pick_side_harm_warns_only_when_it_is_material(step4):
    """Pick 쪽 결품 증가는 **몫이 클 때만** 경고한다.

    재고를 빼내는 곳이니 조금 나빠지는 것은 설계상 정상이다. `mu`가 크게 음수인
    대여소는 `target_qty`가 0으로 잘려 거의 다 실어 가고, 그 대여소가 유난히 붐빈
    하루에 결품이 한두 시간 생긴다(z가 허용한 꼬리).

    실측(26년 03월 `_05_10`): Pick +1h vs Drop −2915h = 0.03%. 예전 조건
    (`pick_delta > 0`)은 여기서도 경고를 띄웠고, 그 거짓 경보 때문에 아무 문제 없는
    `target_qty`를 의심하고 조사했다.
    """
    import project_config

    threshold = project_config.PICK_HARM_WARN_SHARE
    assert 0 < threshold < 1, "문턱은 비율이다"

    # 실측 사례 — 경고하지 않아야 한다
    assert step4.pick_harm_share(1, -2915) < threshold

    # Drop 이득이 미미한데 Pick만 나빠지면 알려야 한다
    assert step4.pick_harm_share(50, -100) > threshold
    # Drop 쪽 이득이 아예 없으면 몫을 따질 것 없이 알린다
    assert step4.pick_harm_share(3, 0) == 1.0
    # 나빠진 것이 없으면 잠잠하다
    assert step4.pick_harm_share(0, 0) == 0.0
    assert step4.pick_harm_share(0, -100) == 0.0


def test_all_three_maps_share_one_tile_setting():
    """군집·경로·재고 지도가 **같은 배경 타일**을 써야 한다.

    스크립트마다 타일 이름을 따로 적어 두면 같은 실행의 산출물끼리 배경이 달라진다
    (사용자 지적, 수정안 2번). 기본값은 folium 기본값과 같은 OpenStreetMap이다 —
    한동안 CartoDB를 쓴 것은 옛 folium이 OSM 서브도메인 URL을 써서 경고를 받았기
    때문이고, 지금은 경고가 없다.
    """
    import project_config

    assert project_config.MAP_TILES == "OpenStreetMap", "기본 배경이 바뀌었다"

    for relative in ("step1_cluster/st_visualization.py",
                     "step3_map/main.py",
                     "step4_metrics/imbalance.py"):
        source = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
        assert "tiles=MAP_TILES" in source, f"{relative}가 공통 설정을 쓰지 않는다"
        # 주석에 남은 설명은 봐주되, 코드에 타일 이름을 박은 것은 막는다.
        code = [line.split("#")[0] for line in source.splitlines()]
        assert not any("CartoDB" in line or "Stamen" in line for line in code), \
            f"{relative}에 타일 이름이 박혀 있다"


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


# ---------------- 시간 예산을 제약으로 걸 때 (1.21.6) ----------------

def test_예산을_주면_넘기기_전에_멈춘다(step2):
    """예산을 주면 **복귀 여유까지 계산해** 멈춰야 한다.

    돌아올 시간을 안 빼고 멈추면 '예산 안에 끝났는데 차고지에는 못 오는' 계획이 된다.
    """
    depot_far = {
        ("ST1", "pick"): {"qty": 5, "lat": 36.50, "lon": 127.50},
        ("ST2", "drop"): {"qty": 5, "lat": 36.60, "lon": 127.60},
    }
    _, vrp = step2
    unlimited = vrp.greedy_route(dict(depot_far), 0)
    assert unlimited, "예산이 없으면 끝까지 간다"

    # 아주 짧은 예산이면 첫 작업조차 못 간다(복귀분을 감안하므로).
    stopped = vrp.greedy_route(dict(depot_far), 0, time_budget_sec=60)
    assert len(stopped) < len(unlimited)


def test_예산_강제는_기본으로_꺼져_있다():
    """계획의 성격이 바뀌는 변경이라 **현장 확인 전에는 켜지 않는다.**

    켜면 못 옮기는 대수가 생긴다(실측 3.2%). 기본값이 조용히 바뀌면 지난 실행과
    비교가 성립하지 않는다.
    """
    import project_config

    assert project_config.ENFORCE_TIME_BUDGET is False


# ───────── 군집 조정 성능 최적화의 안전망 (1.23.8) ─────────
# 넘파이로 바꿔 8.6배 빨라졌다(374초 → 43초). **결과가 같아야만** 값어치가 있다.

def test_메도이드는_군집_안의_실제_지점이다(adjust):
    """medoid는 중심'점'이지 평균이 아니다 — 반드시 소속 대여소 중 하나여야 한다.

    넘파이로 바꾸면서 좌표를 한 번만 꺼내게 했는데, 라벨과 좌표의 짝이
    어긋나면 **엉뚱한 군집의 점**이 중심으로 잡힌다. 그러면 거리 항이
    조용히 틀리고 군집 조정이 이상한 방향으로 간다.
    """
    frame = _clusters(rebal=[1, -1, 1, -1], cluster=[0, 0, 1, 1],
                      lat=[36.0, 36.1, 37.0, 37.1],
                      lon=[127.0, 127.1, 128.0, 128.1])

    medoids = adjust.compute_medoids(frame)

    for cluster in (0, 1):
        point = medoids.loc[cluster]
        members = frame[frame["cluster"] == cluster]
        assert ((members["lat"] == point["lat"])
                & (members["lon"] == point["lon"])).any(), (
            f"군집 {cluster}의 메도이드가 소속 대여소가 아니다")


def test_군집_라벨이_0부터_이어지지_않아도_맞는다(adjust):
    """조정 과정에서 군집이 비면 라벨에 구멍이 생긴다(0, 2, 5 …).

    예전 코드는 `medoid.loc[c]`로 라벨을 직접 찾았다. 넘파이로 바꾸며
    조회 방식이 바뀌었으므로, **위치 기반으로 잘못 찾지 않는지** 지킨다.
    """
    frame = _clusters(rebal=[2, -2, 3, -3], cluster=[0, 0, 7, 7],
                      lat=[36.0, 36.2, 38.0, 38.2],
                      lon=[127.0, 127.2, 129.0, 129.2])

    medoids = adjust.compute_medoids(frame)
    assert set(medoids.index) == {0, 7}

    # 거리 항이 계산되고 유한해야 한다 — 라벨을 잘못 찾으면 KeyError나 NaN이 난다
    score = adjust.compute_objective(frame, K=2, alpha=1, beta=1, gamma=3000)
    assert score == pytest.approx(score) and score >= 0


def test_한_대여소짜리_군집도_처리한다(adjust):
    """군집에 하나만 남으면 자기 자신이 메도이드이고 거리 항은 0이다."""
    frame = _clusters(rebal=[5, -5, 0], cluster=[0, 0, 1],
                      lat=[36.0, 36.1, 37.0], lon=[127.0, 127.1, 128.0])

    medoids = adjust.compute_medoids(frame)

    assert medoids.loc[1, "lat"] == pytest.approx(37.0)
    assert medoids.loc[1, "lon"] == pytest.approx(128.0)


# ── 결품 보정 계수 — 수정안 37 ────────────────────────────────────────

def test_calibration_survives_when_collection_stops(tmp_path, monkeypatch):
    """수집이 멈춰도 **보정 계수는 남아야 한다.**

    이 표를 둔 이유가 그것이다 — 관측(stock_history)은 수집을 켜 둔 동안만
    쌓이지만, 거기서 얻은 보정비는 나중에도 인용할 수 있어야 한다.
    """
    import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    with db.session() as conn:
        db.init_schema(conn)
        db.save_stockout_calibration(conn, [{
            "measured_at": "2026-08-28 09:39", "duration": "_10_15",
            "day_type": "weekday", "ratio": 1.158, "observed": 2.01,
            "simulated": 1.73, "days": 3, "stations": 681,
            "note": "관측 창 08~18시",
        }])
        # 관측을 통째로 비워도 계수는 남는다
        conn.execute("DELETE FROM stock_history")
        conn.commit()

        got = db.latest_stockout_calibration(conn)

    assert len(got) == 1, "수집을 멈추자 계수까지 사라졌다"
    assert got.iloc[0]["ratio"] == pytest.approx(1.158)
    assert got.iloc[0]["days"] == 3, "근거가 며칠치인지가 함께 남아야 한다"


def test_calibration_keeps_history_not_overwrite(tmp_path, monkeypatch):
    """계수는 **덮어쓰지 않고 쌓는다.**

    과거 값을 지우면 "그때는 무엇으로 재서 그 수치를 썼나"를 되짚을 수 없다 —
    논문에 인용한 값이 조용히 바뀌면 안 된다.
    """
    import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    base = {"duration": "_10_15", "day_type": "weekday", "observed": 2.0,
            "simulated": 1.7, "stations": 681, "note": ""}
    with db.session() as conn:
        db.init_schema(conn)
        db.save_stockout_calibration(conn, [
            {**base, "measured_at": "2026-08-28 09:39", "ratio": 1.16, "days": 3},
        ])
        db.save_stockout_calibration(conn, [
            {**base, "measured_at": "2026-09-30 09:00", "ratio": 1.22, "days": 30},
        ])
        total = conn.execute("SELECT COUNT(*) FROM stockout_calibration").fetchone()[0]
        latest = db.latest_stockout_calibration(conn)

    assert total == 2, "옛 계수가 덮여 사라졌다"
    assert latest.iloc[0]["days"] == 30, "가장 최근 계수를 골라야 한다"


# ── 실도로 이동시간 모형 (1.26.7) ─────────────────────────────────────

def test_ilp와_vrp가_같은_이동시간_함수를_쓴다():
    """두 단계가 갈리면 **ILP가 고른 조합이 VRP에서는 최소가 아니게 된다.**

    1.13.2 이전에 속도가 ILP 25 / VRP 30으로 갈려 실제로 겪었다. 지금은
    project_config.travel_seconds() 하나만 쓴다 — 그 규약을 여기서 지킨다.
    """
    import project_config as pc

    ilp = _load("step2_optimize/ilp.py", "ilp_mod")
    vrp = _load("step2_optimize/vrp.py", "vrp_mod")

    for km in (0.0, 0.25, 1.0, 3.7, 12.0):
        expected = pc.travel_seconds(km)
        assert ilp.km_to_travel_seconds(km) == pytest.approx(expected)
        assert vrp._travel_sec(km) == pytest.approx(expected)


def test_기본값은_예전_그대로다():
    """`USE_ROAD_MODEL`이 꺼져 있으면 **문서의 모든 수치가 그대로 나와야 한다.**

    켜는 순간 대조군 비교·z·γ 실험이 전부 다른 값이 된다. 기본이 조용히
    바뀌는 것을 막는다.
    """
    import project_config as pc

    assert pc.USE_ROAD_MODEL is False, "기본이 켜져 있다 — 문서 수치가 무효가 된다"
    for km in (0.5, 2.0, 10.0):
        assert pc.travel_seconds(km) == pytest.approx(km / 25.0 * 3600.0)


def test_실도로_모형은_짧은_구간에_고정비를_붙인다(monkeypatch):
    """짧은 구간의 시간을 지배하는 것은 거리가 아니라 **신호·회전**이다.

    실측: 0.5km 미만 유효속도 5.6 km/h, 10km 이상 23.1 km/h.
    상수 속도로는 이 차이를 못 담는다.
    """
    import importlib

    import project_config as pc

    monkeypatch.setenv("PBR_USE_ROAD_MODEL", "1")
    importlib.reload(pc)
    try:
        assert pc.USE_ROAD_MODEL is True
        short = pc.travel_seconds(0.3)
        long_leg = pc.travel_seconds(10.0)

        # 짧은 구간: 고정비가 지배한다
        assert short > pc.ROAD_FIXED_SEC
        assert 0.3 / (short / 3600) < 10, "짧은 구간인데 유효속도가 너무 빠르다"
        # 긴 구간: 도로 속도가 지배한다
        assert 10.0 / (long_leg / 3600) > 20, "긴 구간인데 유효속도가 너무 느리다"
    finally:
        monkeypatch.delenv("PBR_USE_ROAD_MODEL", raising=False)
        importlib.reload(pc)


def test_모형을_켜도_거리는_그대로다(monkeypatch):
    """이 모형은 **시간 추정만** 바꾼다 — 경로도 물량도 건드리지 않는다.

    실측에서 총 이동거리·처리 대수·결품이 소수점까지 같았다. 드러나는 것은
    '예산 초과 4건 → 21건'뿐이고, 그것은 새로 생긴 문제가 아니라 원래 있던 것이다.
    """
    import importlib

    import project_config as pc

    off = [pc.travel_seconds(k) for k in (1.0, 5.0)]
    monkeypatch.setenv("PBR_USE_ROAD_MODEL", "1")
    importlib.reload(pc)
    try:
        on = [pc.travel_seconds(k) for k in (1.0, 5.0)]
        assert all(b > a for a, b in zip(off, on)), "켜면 시간이 늘어야 한다"
    finally:
        monkeypatch.delenv("PBR_USE_ROAD_MODEL", raising=False)
        importlib.reload(pc)


# ── 군집 조정 최적화 (1.26.8) ─────────────────────────────────────────

def test_목적함수는_캐시_유무에_상관없이_같다():
    """**결과가 달라지면 최적화가 아니라 다른 알고리즘이다.**

    캐시는 손대지 않은 군집의 항을 재사용한다. 합산 순서를 `np.unique(labels)`로
    고정해 부동소수 덧셈 순서가 바뀌지 않게 했다 — 마지막 자리가 흔들리면
    이동 채택 여부가 달라질 수 있다. 그래서 `==`로(근사가 아니라) 검사한다.
    """
    import numpy as np

    adjust = _load("step1_cluster/adjust_module.py", "adjust_cache")
    rng = np.random.default_rng(7)

    for _ in range(120):
        n = int(rng.integers(8, 60))
        K = int(rng.integers(2, min(8, n)))
        labels = rng.integers(0, K, n)
        coords = 36.3 + rng.random((n, 2)) * 0.2
        qty = rng.integers(-30, 30, n).astype(float)

        plain = adjust._objective_parts(labels, coords, qty, K, 1, 100, 3000)
        cache = {}
        first = adjust._objective_parts(labels, coords, qty, K, 1, 100, 3000,
                                        cache=cache)
        again = adjust._objective_parts(labels, coords, qty, K, 1, 100, 3000,
                                        cache=cache)
        assert plain == first == again, "캐시가 값을 바꿨다"


def test_이동_시도가_원본을_건드리지_않는다():
    """채택되지 않으면 입력 프레임이 **그대로** 돌아와야 한다.

    후보를 시험하려고 라벨 배열을 제자리에서 바꾸므로, 되돌리는 것을 빠뜨리면
    다음 후보가 오염된 상태에서 평가된다.
    """
    import numpy as np
    import pandas as pd

    adjust = _load("step1_cluster/adjust_module.py", "adjust_move")

    frame = pd.DataFrame({
        "cluster": [0, 0, 1, 1],
        "lat": [36.30, 36.31, 36.40, 36.41],
        "lon": [127.30, 127.31, 127.40, 127.41],
        "rebal_qty": [5.0, -5.0, 7.0, -7.0],
        "station_name": list("가나다라"),
    })
    before = frame.copy()

    # 점수를 절대 못 내리는 문턱을 주면 아무것도 채택되지 않는다
    out, moved = adjust.try_move_node(frame, frame.iloc[[0]], 1, -1e18,
                                      2, 1, 100, 3000)

    assert moved is False
    pd.testing.assert_frame_equal(out, before)
    pd.testing.assert_frame_equal(frame, before), "입력 프레임이 오염됐다"


def test_채택되면_그_대여소만_옮겨진다():
    """받아들인 이동은 **한 칸만** 바꾼다."""
    import pandas as pd

    adjust = _load("step1_cluster/adjust_module.py", "adjust_move2")

    frame = pd.DataFrame({
        "cluster": [0, 0, 1, 1],
        "lat": [36.30, 36.31, 36.40, 36.41],
        "lon": [127.30, 127.31, 127.40, 127.41],
        "rebal_qty": [5.0, -5.0, 7.0, -7.0],
        "station_name": list("가나다라"),
    })

    # 문턱을 아주 크게 주면 첫 후보가 곧바로 채택된다
    out, moved = adjust.try_move_node(frame, frame.iloc[[0]], 1, 1e18,
                                      2, 1, 100, 3000)

    assert moved is True
    assert out.loc[0, "cluster"] == 1
    assert list(out["cluster"])[1:] == [0, 1, 1], "다른 행까지 바뀌었다"


# ---------------------------------------------------------------- 순회거리 어림

def _grid(n, span=0.02, lat0=36.30, lon0=127.30):
    """정사각 격자 위의 좌표 n개. 뭉침·흩어짐을 span으로 조절한다."""
    import math

    side = math.ceil(math.sqrt(n))
    rows = [{"station_id": f"ST{i:04d}",
             "lat": lat0 + span * (i % side),
             "lon": lon0 + span * (i // side),
             "rebal_qty": 5 if i % 2 else -5}
            for i in range(n)]
    return pd.DataFrame(rows)


def test_tour_estimates_grow_with_spread(step1):
    """어떤 어림이든 흩어지면 길어져야 한다 — 아니면 K를 정하는 데 쓸 수 없다."""
    tight, spread = _grid(36, span=0.002), _grid(36, span=0.02)
    for method in step1.GEO_METHODS:
        assert step1.total_tour_km(spread, method) \
            > step1.total_tour_km(tight, method), f"{method}가 흩어짐에 반응하지 않는다"


def test_mst_is_shorter_than_nn_tour(step1):
    """MST는 TSP의 하계, NN 순회는 상계 쪽이다 — 뒤집히면 구현이 틀린 것이다."""
    frame = _grid(40)
    assert step1.total_tour_km(frame, "mst") < step1.total_tour_km(frame, "nn")


def test_nn_tour_closes_the_loop(step1):
    """돌아오는 구간을 빠뜨리면 실제보다 짧게 어림한다."""
    # 한 변 1도인 정사각형 네 점: 최근접 순회는 네 변을 모두 지나야 한다.
    square = pd.DataFrame({
        "station_id": list("ABCD"),
        "lat": [36.30, 36.30, 36.31, 36.31],
        "lon": [127.30, 127.31, 127.31, 127.30],
        "rebal_qty": [5, -5, 5, -5],
    })
    side = step1._pairwise_km(square["lat"].to_numpy(), square["lon"].to_numpy())[0][3]
    tour = step1.total_tour_km(square, "nn")
    assert tour > 3 * side, "닫는 구간이 빠지면 세 변 길이에 그친다"


def test_tour_estimates_are_deterministic(step1):
    """같은 입력이면 같은 값 — K가 실행마다 흔들리면 계획이 재현되지 않는다."""
    frame = _grid(50)
    for method in step1.GEO_METHODS:
        assert step1.total_tour_km(frame, method) \
            == step1.total_tour_km(frame, method)


def test_tour_estimate_rejects_unknown_method(step1):
    """오타가 조용히 BHH로 떨어지면 어느 어림으로 쟀는지 알 수 없게 된다."""
    with pytest.raises(ValueError, match="모르는"):
        step1.total_tour_km(_grid(10), "nearest")


def test_tiny_inputs_do_not_blow_up(step1):
    """대여소가 0~1곳인 회차가 실제로 있다 — 건너뛰기 가드가 여기 기댄다."""
    for method in step1.GEO_METHODS:
        assert step1.total_tour_km(_grid(1), method) == 0.0
        assert step1.total_tour_km(pd.DataFrame(columns=["lat", "lon"]), method) == 0.0


def test_bhh_path_is_unchanged_by_the_refactor(step1):
    """1.26.53 리팩터링이 기존 BHH 값을 바꾸면 5-H장 수치가 통째로 무효가 된다."""
    import numpy as np

    frame = _grid(40)
    lat = frame["lat"].to_numpy()
    lon = frame["lon"].to_numpy()
    mid = np.radians(lat.mean())
    width = (lon.max() - lon.min()) * 111.0 * np.cos(mid)
    height = (lat.max() - lat.min()) * 111.0
    expected = 0.7124 * np.sqrt(len(frame) * max(abs(width) * abs(height), 0.01))
    assert step1.total_tour_km(frame, "bhh") == pytest.approx(expected)
