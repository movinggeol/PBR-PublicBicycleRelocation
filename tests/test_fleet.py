"""차량 로테이션과 형평성 검증 (docs/구현/FLEET.md).

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


def test_shrinking_fleet_removes_extra_vehicles(conn, monkeypatch):
    """보유 대수를 줄이면 남는 차량이 배정에서 빠진다 (웹 폼의 '차량 대수').

    실제로는 --fleet-size가 PBR_FLEET_SIZE로 전달되어 단계 프로세스의
    FLEET_SIZE가 바뀐다. 여기서는 그 상황을 상수 교체로 흉내 낸다.
    """
    monkeypatch.setattr(db, "FLEET_SIZE", 5)
    db.sync_fleet(conn)

    workload = db.vehicle_workload(conn)
    assert workload["vehicle_id"].tolist() == ["V01", "V02", "V03", "V04", "V05"]

    mapping = _round(conn, "R1", "_05_10", {c: 30.0 for c in range(5)})
    assert set(mapping.values()) <= {"V01", "V02", "V03", "V04", "V05"}

    with pytest.raises(ValueError, match="차량이 부족"):
        db.assign_vehicles(conn, {c: 30.0 for c in range(6)},
                           run_label="R1", duration="_10_15")


def test_growing_fleet_restores_and_adds(conn):
    """다시 늘리면 축소로 뺐던 차량은 돌아오고, 정비 차량은 그대로 빠져 있다."""
    conn.execute("UPDATE vehicle SET active = 0, note = '정비 입고' WHERE vehicle_id = 'V03'")
    conn.commit()

    db.sync_fleet(conn, 5)
    db.sync_fleet(conn, 25)

    active = db.vehicle_workload(conn)["vehicle_id"].tolist()
    assert "V25" in active, "늘린 만큼 차량이 추가되어야 한다"
    assert "V21" in active, "축소로 뺐던 차량은 복귀해야 한다"
    assert "V03" not in active, "정비로 뺀 차량까지 살아나면 안 된다"
    assert len(active) == 24


def test_read_path_does_not_resize_fleet(conn):
    """조회용 ensure_fleet은 대수를 되돌리지 않는다(웹 화면이 실행 설정을 덮지 않게)."""
    db.sync_fleet(conn, 5)
    db.ensure_fleet(conn, FLEET_SIZE)

    assert len(db.vehicle_workload(conn)) == 5


def test_assignment_history_filters(conn):
    _round(conn, "R1", "_05_10", {0: 60.0, 1: 50.0})
    _round(conn, "R2", "_05_10", {0: 60.0})

    assert len(db.assignment_history(conn)) == 3
    assert len(db.assignment_history(conn, run_label="R1")) == 2
    assert set(db.assignment_history(conn, run_label="R2")["run_label"]) == {"R2"}


def test_배정_이력은_한_쪽만_읽는다(conn):
    """이력은 실행을 거듭할수록 무한히 쌓인다 — **읽는 양이 따라 늘면 안 된다.**

    예전에는 표 전체를 DataFrame으로 읽어 온 뒤 화면단에서 앞 200행만 잘랐다.
    잘린 나머지는 닿을 길이 없었고, 상한을 올리는 것은 미루기일 뿐이었다
    (실측: 실행 1건이 평균 17.2행이라 200은 12회 실행분이다).
    """
    for r in range(6):
        _round(conn, f"2026-09-{r + 1:02d} 09", "_05_10",
               {c: 90.0 for c in range(10)})

    total = db.count_assignments(conn)
    assert total == 60, "6회차 x 10대"

    page = db.assignment_history(conn, limit=25)
    assert len(page) == 25, "상한을 SQL에 걸어 그만큼만 읽어야 한다"

    second = db.assignment_history(conn, limit=25, offset=25)
    assert len(second) == 25
    # 쪽이 겹치지 않아야 한다 — 겹치면 어떤 행은 두 번, 어떤 행은 한 번도 안 보인다.
    first_keys = {(r.run_label, r.duration, r.vehicle_id) for r in page.itertuples()}
    second_keys = {(r.run_label, r.duration, r.vehicle_id) for r in second.itertuples()}
    assert not (first_keys & second_keys)


def test_쪽을_모두_넘기면_한_행도_빠지지_않는다(conn):
    """쪽 나눔의 값어치는 **전부에 닿는 것**이다. 자르고 '더 있습니다'라고만
    적으면 나머지는 영영 못 본다(그것이 고치기 전 동작이었다)."""
    for r in range(4):
        _round(conn, f"2026-09-{r + 1:02d} 09", "_05_10",
               {c: 90.0 for c in range(7)})

    total = db.count_assignments(conn)
    seen = set()
    per_page = 10
    for page in range(-(-total // per_page)):
        rows = db.assignment_history(conn, limit=per_page, offset=page * per_page)
        seen |= {(r.run_label, r.duration, r.vehicle_id) for r in rows.itertuples()}

    assert len(seen) == total, "쪽을 다 넘겼는데 못 본 행이 있다"


def test_건수는_같은_조건으로_센다(conn):
    """세는 쪽과 읽는 쪽의 조건이 갈리면 쪽 수가 실제와 어긋난다."""
    _round(conn, "2026-09-01 09", "_05_10", {c: 90.0 for c in range(5)})
    _round(conn, "2026-09-02 09", "_05_10", {c: 90.0 for c in range(5)})

    assert db.count_assignments(conn) == 10
    assert db.count_assignments(conn, run_label="2026-09-01 09") == 5
    assert len(db.assignment_history(conn, run_label="2026-09-01 09")) == 5
