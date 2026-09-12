"""고장 추정 자전거 수거(`experiments/structure/broken_collect.py`)를 지킨다.

**이 파일이 있는 이유는 수거가 거의 안 돌던 코드 경로를 쓰기 때문이다.**

`greedy_route()`의 '작업 불가 → depot 복귀' 분기는 파이프라인 입력에서는
**구조상 실행될 수 없다** — ILP가 군집마다 총 pick = 총 drop을 맞춰 주므로
적재가 막힐 일이 없다. 실데이터 15개 실행·1,224행에 `return` 행이 **0건**이었고,
살아 있던 호출부는 대조군 B1 하나뿐이었다(vrp.py:144-153의 주석).

수거는 **순수 pick만** 준다. 그래서 적재가 차면 반드시 그 분기로 들어가
depot에 비우고 다시 나간다(다회 왕복). 즉 **이 기능이 그 경로에 처음으로
체중을 싣는다.** 여기가 조용히 틀리면 이동거리·출동 횟수가 통째로 틀리고,
그 숫자가 배치 임계치 판정을 정한다.

지키려는 것 셋:
  ① 순수 픽업에서 다회 왕복이 실제로 일어나고 **모든 자전거가 회수된다**
  ② `greedy_route()`가 호출 측 dict를 소모하는 함정(함정 12)에 안 걸린다
  ③ 임계치를 바꿔도 **모집단이 흔들리지 않는다**
"""
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_module():
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    spec = importlib.util.spec_from_file_location(
        "broken_collect",
        PROJECT_ROOT / "experiments" / "structure" / "broken_collect.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bc():
    return load_module()


def _bikes(n, *, station_span=1, start="2025-09-01"):
    """고장 추정 자전거 n대. station_span곳에 고루 흩어 놓는다."""
    stamps = pd.date_range(start, periods=n, freq="h")
    return pd.DataFrame({
        "bike_no": [f"DJ3-{i:04d}" for i in range(n)],
        "station_id": [f"ST{i % station_span:04d}" for i in range(n)],
        # 대전 시내 범위 안에서 대여소마다 조금씩 떨어뜨린다
        "lat": [36.34 + (i % station_span) * 0.004 for i in range(n)],
        "lon": [127.38 + (i % station_span) * 0.004 for i in range(n)],
        "broken_at": stamps,
    })


# ─────────────────────────────────────────── ① 다회 왕복

def test_pure_pickup_returns_to_depot_when_full(bc):
    """적재를 넘기면 depot에 비우고 다시 나가야 한다.

    이 분기(vrp.py의 `if not candidates:`)는 재배치 입력에서는 실행될 수 없어
    실데이터에 흔적이 0건이었다. 순수 픽업에서는 **반드시** 돌아야 한다.
    """
    stats = bc.route_once(_bikes(25, station_span=25), capacity=10)

    assert stats["picked"] == 25, "25대를 전부 실어야 한다"
    assert stats["returns"] >= 2, (
        f"25대를 용량 10으로 나르려면 중간 복귀가 최소 2번 필요한데"
        f" {stats['returns']}번뿐이다 — 적재 제약이 무시되고 있다")
    assert stats["km"] > 0 and stats["sec"] > 0


def test_every_bike_is_collected_regardless_of_capacity(bc):
    """용량을 바꿔도 **처리 대수는 같아야 한다** — 용량은 몇 번 나가느냐만 바꾼다."""
    bikes = _bikes(30, station_span=30)
    for capacity in (5, 7, 10):
        stats = bc.route_once(bikes, capacity=capacity)
        assert stats["picked"] == 30, f"용량 {capacity}에서 {stats['picked']}대만 실었다"


def test_single_bike_still_makes_a_round_trip(bc):
    """1대뿐이어도 depot을 나갔다 돌아온다 — 실측에서 466곳 중 286곳이 1대였다."""
    stats = bc.route_once(_bikes(1, station_span=1), capacity=10)
    assert stats["picked"] == 1
    assert stats["km"] > 0, "왕복 거리가 0이면 depot 복귀가 빠진 것이다"


# ─────────────────────────────────────────── ② 소모 함정

def test_greedy_route_really_consumes_its_input(bc):
    """함정 12가 **아직 실재하는지** 직접 고정한다.

    이걸 먼저 박아 두는 이유: 아래 `route_once` 테스트는 노드를 매 호출마다
    새로 만드는 현재 구현에서는 **저절로 통과한다**(공허하다). 소모 자체를
    여기서 고정해 두면, 누군가 노드를 캐싱하도록 고쳤을 때 왜 위험한지가
    테스트로 남는다.
    """
    from pipeline.step2_optimize import vrp as vrp_mod

    nodes = {("ST0500", "pick"): {"qty": 3, "lat": 36.35, "lon": 127.38}}
    vrp_mod.greedy_route(nodes, cluster=0)

    assert nodes[("ST0500", "pick")]["qty"] == 0, (
        "greedy_route가 더는 입력을 소모하지 않는다면 broken_collect의"
        " 방어 복사 주석을 함께 고쳐야 한다")


def test_route_once_is_repeatable(bc):
    """같은 입력을 두 번 넣으면 **같은 답**이 나와야 한다.

    🔴 현재 구현에서는 노드를 매번 새로 만들어 저절로 통과한다. 이 테스트가
    잡으려는 것은 **미래의 최적화**다 — 대여소가 출동마다 겹치므로 노드를
    미리 만들어 캐싱하고 싶어지는데, 그러면 두 번째 출동부터 빈 경로를 받는다.
    위 `test_greedy_route_really_consumes_its_input`과 짝이다.
    """
    bikes = _bikes(15, station_span=15)
    first = bc.route_once(bikes, capacity=10)
    second = bc.route_once(bikes, capacity=10)

    assert first == second, (
        "두 번째 호출이 달라졌다 — 노드 dict가 소모되고 있다"
        f"\n  1회차 {first}\n  2회차 {second}")
    assert second["picked"] == 15, "두 번째 호출이 빈 경로를 받았다"


# ─────────────────────────────────────────── ③ 모집단 고정

@pytest.mark.parametrize("rule,value", [
    ("count", 5), ("count", 10), ("count", 50), ("days", 1), ("days", 7),
])
def test_population_is_fixed_across_policies(bc, rule, value):
    """임계치는 *언제* 수거하는지만 바꾼다 — *몇 대*가 달라지면 안 된다.

    결품·방치는 집합 위의 평균이라 분모가 흔들리면 **서로 다른 자로 잰 값**이
    된다. 이 저장소가 세 번 걸린 함정이다(EXPERIMENTS 17·18장).
    """
    bikes = _bikes(40, station_span=20)
    result = bc.simulate(bikes, rule, value, capacity=10)
    assert result["처리"] == 40, f"{rule}={value}에서 {result['처리']}대만 처리했다"


def test_leftover_tail_is_collected_not_dropped(bc):
    """임계치에 못 미친 자투리를 버리면 큰 임계치가 공짜로 유리해진다."""
    bikes = _bikes(12, station_span=12)
    # 100대 임계치는 12대로 절대 안 채워진다 — 그래도 전부 수거돼야 한다
    result = bc.simulate(bikes, "count", 100, capacity=10)
    assert result["처리"] == 12
    assert result["출동"] == 1, "자투리 강제 수거가 1회 있어야 한다"


def test_raising_the_threshold_trades_idle_time_for_distance(bc):
    """맞바꿈이 실제로 성립하는지 — 방향이 반대면 시뮬레이션이 틀린 것이다."""
    bikes = _bikes(60, station_span=30)
    tight = bc.simulate(bikes, "count", 5, capacity=10)
    loose = bc.simulate(bikes, "count", 30, capacity=10)

    assert loose["방치_대일"] > tight["방치_대일"], "모아 두면 방치가 늘어야 한다"
    assert loose["대당km"] <= tight["대당km"], "모아 나가면 대당 거리가 줄어야 한다"
    assert loose["출동"] < tight["출동"]


# ─────────────────────────────────────────── 대상 도출

def test_operational_stations_are_excluded(bc):
    """관제센터·정비대기로 반납된 자전거는 **이미 들어간 것**이라 수거 대상이 아니다."""
    assert "ST0001" in bc.OPS_STATIONS, "depot(정비대기)은 제외 대상이다"
    assert "ST1220" in bc.OPS_STATIONS, "타슈관제센터는 공개 대여소가 아니다"


def test_detect_broken_excludes_ops_and_keeps_the_rest(bc, tmp_path):
    """탐지가 운영시설 반납분을 빼고, 사라지지 않은 자전거는 남기지 않는지."""
    import sqlite3

    path = tmp_path / "t.db"
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE rental_history (
        bike_no TEXT, rent_at TEXT, return_at TEXT,
        return_station TEXT, return_lat REAL, return_lon REAL)""")
    rows = [
        # 기준기간에만 보이고 추적기간에 없다 → 고장 추정 (일반 대여소)
        ("DJ3-0001", "2025-09-05 10:00:00", "2025-09-05 10:12:00",
         "ST0500", 36.35, 127.38),
        # 같은 조건이지만 운영시설로 반납 → 제외돼야 한다
        ("DJ3-0002", "2025-09-06 10:00:00", "2025-09-06 10:12:00",
         "ST1220", 36.40, 127.31),
        # 추적기간에도 나타난다 → 생존, 대상 아님
        ("DJ3-0003", "2025-09-07 10:00:00", "2025-09-07 10:12:00",
         "ST0501", 36.36, 127.39),
        ("DJ3-0003", "2026-02-07 10:00:00", "2026-02-07 10:12:00",
         "ST0501", 36.36, 127.39),
    ]
    con.executemany("INSERT INTO rental_history VALUES (?,?,?,?,?,?)", rows)
    con.commit()

    found = bc.detect_broken(con, ["2025-09"], ["2026-01", "2026-02", "2026-03"])
    con.close()

    assert list(found["bike_no"]) == ["DJ3-0001"], (
        f"운영시설 반납분이나 생존 자전거가 섞였다: {list(found['bike_no'])}")
    assert found["broken_at"].dtype.kind == "M", "발생 시각이 시간 타입이어야 한다"
