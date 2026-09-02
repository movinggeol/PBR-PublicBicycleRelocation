"""TMAP 실도로 소요시간 고정 패널 수집기 검증 (docs/분석/EXPERIMENTS.md 9장).

실제 API는 일일 한도가 있는 유료 서비스라 부르지 않는다. TMAP 호출만 가로채고,
**이 수집기가 존재하는 이유가 지켜지는지**를 본다.

지키려는 규칙:
  1. 패널은 **결정적**이다 — 같은 대여소 목록이면 매번 같은 사슬이 나온다.
     흔들리면 '같은 구간을 매일 다시 잰다'는 전제가 무너져 수집 자체가 무의미해진다.
  2. 사슬은 **차고지에서 시작해 차고지로 끝난다** — 차고지 왕복이 실제 이동거리의
     61~65%라 이것이 빠진 패널은 실제를 대표하지 못한다.
  3. 거리 구간이 **고르게** 찬다 — 짧은 구간만 모이면 고정비 계수가, 긴 구간만
     모이면 속도 계수가 잡히지 않는다.
  4. 누적 시간의 **차분**이 구간 실측이고, `start_time`(어느 시각의 교통량인가)이
     함께 남는다 — 이것이 없으면 나중에 회차별로 나눠 볼 수 없다.
"""
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "tools" / "collect_road_time.py"


def load_collector():
    """tools/collect_road_time.py를 경로로 직접 읽는다."""
    for path in (PROJECT_ROOT, PROJECT_ROOT / "step3_map"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    spec = importlib.util.spec_from_file_location("collect_road_time", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def collector():
    return load_collector()


def fake_stations(count=200):
    """대전 도심 부근에 격자로 흩뿌린 가짜 대여소."""
    rows = []
    for i in range(count):
        rows.append({
            "station_id": f"ST{i + 100:04d}",
            "station_name": f"가짜대여소{i}",
            "lat": 36.30 + (i % 20) * 0.012,
            "lon": 127.30 + (i // 20) * 0.012,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 패널

def test_panel_is_deterministic(collector):
    """같은 입력이면 같은 패널이 나와야 한다 — 매일 같은 구간을 재기 위해서다."""
    stations = fake_stations()
    first = collector.build_panel(stations)
    second = collector.build_panel(stations)
    assert first == second


def test_panel_starts_and_ends_at_depot(collector):
    """사슬은 차고지 왕복을 포함한다 (총 이동거리의 61~65%를 차지한다)."""
    from project_config import DEPOT_ID

    for chain in collector.build_panel(fake_stations()):
        points = chain["points"]
        assert points[0]["id"] == DEPOT_ID
        assert points[-1]["id"] == DEPOT_ID
        assert len(points) == collector.HOPS_PER_CHAIN + 1


def test_panel_visits_each_station_once(collector):
    """같은 대여소를 두 번 넣으면 그만큼 거리 구간 표본이 줄어든다."""
    chains = collector.build_panel(fake_stations())
    visited = [p["id"] for chain in chains for p in chain["points"][1:-1]]
    assert len(visited) == len(set(visited))


def test_panel_covers_distance_buckets(collector):
    """거리 구간이 한쪽으로 쏠리면 계수 둘 중 하나가 잡히지 않는다."""
    summary = collector.panel_summary(collector.build_panel(fake_stations()))
    filled = summary[summary["구간수"] > 0]
    # 5-F장의 여섯 구간 중 적어도 다섯은 비어 있으면 안 된다.
    assert len(filled) >= 5


def test_panel_fits_cheaper_endpoint(collector):
    """경유지가 30을 넘으면 routeSequential100으로 넘어가 쿼터를 더 쓴다."""
    for chain in collector.build_panel(fake_stations()):
        via_count = len(chain["points"]) - 2
        assert via_count <= 30


# ---------------------------------------------------------------- 측정

def geojson_from(elapsed: list) -> dict:
    """누적 초 목록을 TMAP GeoJSON 모양으로 되돌린다 (선분 time은 차분)."""
    features = []
    for index, value in enumerate(elapsed):
        features.append({"geometry": {"type": "Point", "coordinates": [127.3, 36.3]},
                         "properties": {"pointType": "S" if index == 0 else "V"}})
        if index:
            features.append({"geometry": {"type": "LineString"},
                             "properties": {"time": value - elapsed[index - 1]}})
    return {"features": features}


def test_measure_chain_uses_differences_and_records_start_time(collector, monkeypatch):
    """구간 실측은 누적의 **차분**이고, 어느 시각의 교통량인지가 함께 남는다."""
    chain = {"chain": 0, "points": [
        {"id": "A", "name": "가", "lat": 36.30, "lon": 127.30},
        {"id": "B", "name": "나", "lat": 36.32, "lon": 127.32},
        {"id": "C", "name": "다", "lat": 36.35, "lon": 127.35},
    ]}
    monkeypatch.setattr(collector, "call_tmap_sequential",
                        lambda *a, **k: geojson_from([0, 300, 800]))

    rows = collector.measure_chain(chain, "_10_15", "202609011000", {})

    assert [r["road_sec"] for r in rows] == [300.0, 500.0]
    assert [r["leg"] for r in rows] == [0, 1]
    assert {r["start_time"] for r in rows} == {"202609011000"}
    assert all(r["straight_km"] > 0 for r in rows)


def test_measure_chain_drops_zero_gaps(collector, monkeypatch):
    """같은 자리를 두 번 지나면 0초가 나온다 — 모형이 배울 것이 없어 버린다."""
    chain = {"chain": 0, "points": [
        {"id": "A", "name": "가", "lat": 36.30, "lon": 127.30},
        {"id": "A", "name": "가", "lat": 36.30, "lon": 127.30},
        {"id": "B", "name": "나", "lat": 36.35, "lon": 127.35},
    ]}
    monkeypatch.setattr(collector, "call_tmap_sequential",
                        lambda *a, **k: geojson_from([0, 0, 600]))

    rows = collector.measure_chain(chain, "_10_15", "202609011000", {})
    assert len(rows) == 1
    assert rows[0]["road_sec"] == 600.0


def test_measure_chain_discards_incomplete_timing(collector, monkeypatch):
    """시간 정보가 없는 응답은 통째로 버린다 — 반쪽 자료가 계수를 흔든다."""
    chain = {"chain": 0, "points": [
        {"id": "A", "name": "가", "lat": 36.30, "lon": 127.30},
        {"id": "B", "name": "나", "lat": 36.35, "lon": 127.35},
    ]}
    monkeypatch.setattr(collector, "call_tmap_sequential",
                        lambda *a, **k: {"features": [
                            {"geometry": {"type": "Point", "coordinates": [127.3, 36.3]},
                             "properties": {"pointType": "S"}},
                            {"geometry": {"type": "LineString"}, "properties": {}},
                        ]})

    assert collector.measure_chain(chain, "_10_15", "202609011000", {}) == []


# ---------------------------------------------------------------- 저장

def test_saved_rows_keep_start_time(collector):
    """road_leg에 start_time이 실제로 남아야 회차별로 나눠 볼 수 있다."""
    import db

    frame = pd.DataFrame([{
        "cluster": 0, "leg": 0, "from_id": "A", "to_id": "B",
        "from_lat": 36.3, "from_lon": 127.3, "to_lat": 36.35, "to_lon": 127.35,
        "straight_km": 5.0, "road_sec": 600.0,
        "observed_at": "2026-08-31 03:30", "start_time": "202609011000",
    }])
    saved = db.save_output("road_leg", frame,
                           run_label=collector.PROBE_PREFIX + "2026-08-31",
                           duration="_10_15")
    assert saved == 1

    with db.session() as conn:
        got = pd.read_sql("SELECT run_label, duration, start_time FROM road_leg", conn)
    assert got.loc[0, "start_time"] == "202609011000"
    assert got.loc[0, "run_label"].startswith(collector.PROBE_PREFIX)


def test_probe_label_separates_from_pipeline_rows(collector):
    """분석이 패널과 파이프라인 실행분을 가를 수 있어야 한다 (구간 성격이 다르다)."""
    assert collector.PROBE_PREFIX.endswith("-")
    assert not collector.PROBE_PREFIX[0].isdigit()   # 파이프라인 라벨은 날짜로 시작한다


# ---------------------------------------------------------------- 패널 이식성

def test_panel_lives_where_git_carries_it(collector):
    """`data/`에 두면 PC마다 다른 패널이 생겨 전제가 무너진다."""
    assert collector.PANEL_PATH.parent.name == "tools"
    assert collector.LEGACY_PANEL_PATH.parent.name == "data"


def test_panel_changes_when_a_used_station_disappears(collector):
    """대여소 구성이 다르면 패널이 통째로 바뀐다 — 그래서 커밋으로 날라야 한다."""
    stations = fake_stations()
    base = collector.build_panel(stations)
    used = sorted({p["id"] for c in base for p in c["points"]
                   if p["id"] != collector.DEPOT_ID})

    shrunk = stations[stations["station_id"] != used[0]].reset_index(drop=True)
    assert collector.panel_digest(collector.build_panel(shrunk)) \
        != collector.panel_digest(base)


def test_digest_ignores_coordinate_formatting(collector):
    """지문은 '어느 지점을 어떤 순서로'만 본다 — 좌표 표기는 PC마다 다를 수 있다."""
    import copy

    chains = collector.build_panel(fake_stations())
    tweaked = copy.deepcopy(chains)
    for chain in tweaked:
        for point in chain["points"]:
            point["lat"] = round(point["lat"], 4)
    assert collector.panel_digest(tweaked) == collector.panel_digest(chains)


def test_legacy_panel_is_migrated_not_rebuilt(collector, tmp_path, monkeypatch):
    """옛 자리에 있던 패널은 **그대로** 옮겨야 한다 — 다시 만들면 구간이 바뀐다."""
    import json

    chains = collector.build_panel(fake_stations())
    legacy = tmp_path / "data" / "road_panel.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"chains": chains}, ensure_ascii=False),
                      encoding="utf-8")
    monkeypatch.setattr(collector, "LEGACY_PANEL_PATH", legacy)
    monkeypatch.setattr(collector, "PANEL_PATH", tmp_path / "tools" / "road_panel.json")

    moved = collector.load_panel()
    assert collector.panel_digest(moved) == collector.panel_digest(chains)
    assert (tmp_path / "tools" / "road_panel.json").is_file()


def test_saved_panel_carries_its_digest(collector, tmp_path, monkeypatch):
    """파일만 보고도 두 PC를 대조할 수 있어야 한다."""
    import json

    chains = collector.build_panel(fake_stations())
    target = tmp_path / "road_panel.json"
    monkeypatch.setattr(collector, "PANEL_PATH", target)
    collector.save_panel(chains)
    assert json.loads(target.read_text(encoding="utf-8"))["digest"] \
        == collector.panel_digest(chains)


def test_drift_check_flags_legs_outside_the_panel(collector):
    """패널이 도중에 바뀐 채 섞이는 것이 가장 나쁘다 — 눈에 보여야 한다."""
    import copy

    import db
    import pandas as pd

    chains = collector.build_panel(fake_stations())
    points = chains[0]["points"]
    rows = pd.DataFrame([{
        "cluster": 0, "leg": i,
        "from_id": points[i]["id"], "to_id": points[i + 1]["id"],
        "from_lat": points[i]["lat"], "from_lon": points[i]["lon"],
        "to_lat": points[i + 1]["lat"], "to_lon": points[i + 1]["lon"],
        "straight_km": 1.0, "road_sec": 120.0,
        "observed_at": "2026-08-31 03:30", "start_time": "202609010500",
    } for i in range(3)])
    db.save_output("road_leg", rows,
                   run_label=collector.PROBE_PREFIX + "2026-08-31", duration="_05_10")

    assert collector.warn_if_panel_drifted(chains) is False

    changed = copy.deepcopy(chains)
    changed[0]["points"][1]["id"] = "ST9999"
    assert collector.warn_if_panel_drifted(changed) is True

# ------------------------------------------------- 회차 순서 돌리기

def test_한도가_소진돼도_늘_같은_회차만_잘리지_않는다(collector):
    """collect()는 TMAP 한도가 소진되면 그 자리에서 멈춘다. 순서가 고정이면
    잘리는 자리도 고정이라 마지막 회차만 표본이 계속 모자란다 — 실제로
    roadprobe-2026-09-01이 `_20_05`를 통째로 잃었다(300구간 / 400구간).

    평일만 도는 스케줄에서도 네 회차가 고르게 마지막에 서야 한다. mod 4로
    도는데 금→월이 3일을 건너뛰므로, 여기서 실제로 세어 확인한다."""
    from collections import Counter
    from datetime import date, timedelta

    durations = ["_05_10", "_10_15", "_15_20", "_20_05"]
    last = Counter()
    day, seen = date(2026, 9, 4), 0
    while seen < 20:
        if day.weekday() < 5:                      # 수집기는 평일만 깨운다
            order = collector.rotate_durations(durations, day.isoformat())
            last[order[-1]] += 1
            seen += 1
        day += timedelta(days=1)

    assert set(last) == set(durations), f"마지막에 서 보지 못한 회차가 있다: {last}"
    assert max(last.values()) - min(last.values()) <= 1, (
        f"잘리는 자리가 한쪽으로 쏠린다: {last}")


def test_회차_순서는_같은_날이면_같다(collector):
    """재실행해도 같은 순서여야 한다 — 하루치를 두 번 받아도 라벨이 같으므로,
    순서가 흔들리면 어느 회차가 들어왔는지 재현할 수 없다."""
    durations = ["_05_10", "_10_15", "_15_20", "_20_05"]
    first = collector.rotate_durations(durations, "2026-09-07")
    again = collector.rotate_durations(durations, "2026-09-07")
    assert first == again
    assert sorted(first) == sorted(durations), "회차가 사라지거나 늘었다"


def test_사람이_고른_순서는_돌리지_않는다(collector):
    """--durations로 직접 고른 경우는 그 순서가 의도다. 하나만 고른 경우에도
    돌릴 것이 없다."""
    assert collector.rotate_durations(["_20_05"], "2026-09-07") == ["_20_05"]
    assert collector.rotate_durations([], "2026-09-07") == []
