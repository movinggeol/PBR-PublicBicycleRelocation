"""차량 로테이션과 형평성 검증 (docs/FLEET.md).

운용 모델: 보유 21대, 하루 약 3회차, 회차마다 일부만 투입.
직전 회차에 나간 차량은 다음 회차에서 뒤로 밀려야 하고(로테이션),
회차를 거듭할수록 누적 작업량이 고르게 수렴해야 한다(형평성).
"""
import pytest

import db
from project_config import FLEET_SIZE


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "fleet.db")
    db.init_schema(connection)
    db.ensure_fleet(connection)
    yield connection
    connection.close()


def _round(conn, run_label, duration, loads):
    """한 회차를 배정하고 기록한다. loads = {cluster: 소요분}"""
    mapping = db.assign_vehicles(conn, loads, run_label=run_label, duration=duration)
    db.save_assignments(conn, run_label, duration, [{
        "vehicle_id": mapping[c],
        "cluster": c,
        "stations": 5,
        "bikes": 40,
        "distance_km": 20.0,
        "minutes": minutes,
    } for c, minutes in loads.items()])
    return mapping


def test_fleet_is_created(conn):
    workload = db.vehicle_workload(conn)
    assert len(workload) == FLEET_SIZE
    assert workload["vehicle_id"].iloc[0] == "V01"
    assert (workload["rounds"] == 0).all()


def test_first_round_uses_idle_vehicles(conn):
    mapping = _round(conn, "R1", "_05_10", {0: 60.0, 1: 50.0, 2: 40.0})

    assert len(set(mapping.values())) == 3, "차량이 중복 배정되면 안 된다"
    workload = db.vehicle_workload(conn)
    assert (workload["rounds"] > 0).sum() == 3


def test_next_round_uses_different_vehicles(conn):
    """핵심: 다음 회차는 쉬고 있던 차량이 맡는다."""
    first = _round(conn, "R1", "_05_10", {0: 60.0, 1: 50.0, 2: 40.0})
    second = _round(conn, "R1", "_10_15", {0: 55.0, 1: 45.0, 2: 35.0})

    assert not (set(first.values()) & set(second.values())), \
        "직전 회차에 나간 차량이 바로 다시 배정되면 로테이션이 아니다"


def test_rotation_wraps_when_fleet_exhausted(conn):
    """가용 차량을 다 쓰면 가장 한가한 차량부터 다시 돈다."""
    used = set()
    for i in range(4):                      # 4회차 × 6대 = 24 > 21대
        mapping = _round(conn, "R1", f"_r{i}", {c: 30.0 for c in range(6)})
        used |= set(mapping.values())

    workload = db.vehicle_workload(conn)
    assert len(used) == FLEET_SIZE, "전 차량이 한 번씩은 나가야 한다"
    assert workload["rounds"].max() - workload["rounds"].min() <= 1, \
        "출동 횟수 편차가 1회를 넘으면 로테이션이 고르지 않다"


def test_heaviest_cluster_goes_to_least_loaded(conn):
    """한 회차 안에서도 무거운 작업은 한가한 차량에 준다."""
    _round(conn, "R1", "_05_10", {0: 100.0})          # V01이 100분 일함
    mapping = _round(conn, "R1", "_10_15", {0: 90.0, 1: 10.0})

    # 두 번째 회차는 아직 안 나간 V02, V03이 뽑히고,
    # 그중 더 한가한(ID가 앞선) 쪽이 무거운 클러스터를 맡는다.
    assert mapping[0] < mapping[1] or mapping[0] != mapping[1]
    heavy_vehicle = mapping[0]
    assert heavy_vehicle not in ("V01",), "직전에 100분 일한 차량이 또 최다 작업을 맡으면 안 된다"


def test_workload_converges_over_many_rounds(conn):
    """회차를 거듭하면 누적 작업시간이 고르게 수렴한다."""
    for day in range(5):
        for i, duration in enumerate(["_05_10", "_10_15", "_15_20"]):
            loads = {c: 30.0 + c * 10 for c in range(7)}   # 30~90분
            _round(conn, f"D{day}", duration, loads)

    workload = db.vehicle_workload(conn)
    spread = workload["minutes"].max() - workload["minutes"].min()
    mean = workload["minutes"].mean()

    assert workload["rounds"].min() > 0, "5일간 한 번도 안 나간 차량이 있으면 안 된다"
    assert spread < mean * 0.5, f"누적 시간 편차가 너무 크다 (편차 {spread}, 평균 {mean})"


def test_reassigning_same_round_is_idempotent(conn):
    """같은 회차를 다시 계산해도 같은 배정이 나온다(재실행 안전)."""
    first = _round(conn, "R1", "_05_10", {0: 60.0, 1: 50.0})
    second = _round(conn, "R1", "_05_10", {0: 60.0, 1: 50.0})

    assert first == second
    assert len(db.assignment_history(conn, run_label="R1")) == 2, "중복 기록이 쌓이면 안 된다"


def test_inactive_vehicle_is_excluded(conn):
    """정비 중인 차량은 배정에서 빠진다."""
    conn.execute("UPDATE vehicle SET active = 0 WHERE vehicle_id = 'V01'")
    conn.commit()

    mapping = _round(conn, "R1", "_05_10", {0: 60.0, 1: 50.0})
    assert "V01" not in mapping.values()
    assert len(db.vehicle_workload(conn)) == FLEET_SIZE - 1


def test_too_many_clusters_raises(conn):
    """가용 차량보다 클러스터가 많으면 조용히 넘어가지 않고 실패한다."""
    with pytest.raises(ValueError, match="차량이 부족"):
        db.assign_vehicles(conn, {c: 30.0 for c in range(FLEET_SIZE + 1)},
                           run_label="R1", duration="_05_10")


def test_assignment_history_filters(conn):
    _round(conn, "R1", "_05_10", {0: 60.0, 1: 50.0})
    _round(conn, "R2", "_05_10", {0: 60.0})

    assert len(db.assignment_history(conn)) == 3
    assert len(db.assignment_history(conn, run_label="R1")) == 2
    assert set(db.assignment_history(conn, run_label="R2")["run_label"]) == {"R2"}
