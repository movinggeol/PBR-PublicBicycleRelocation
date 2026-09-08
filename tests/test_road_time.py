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
    """`data/`에 두면 PC마다 다른 패널이 생겨 전제가 무너진다.

    ⚠️ 폴더 **이름**으로 재지 마라 — `PBR_DATA_ROOT`로 데이터 경로를 옮기면
    이름이 `data`가 아니게 된다(1.26.141에서 실제로 이 테스트가 깨졌다).
    묻고 있는 것은 이름이 아니라 **위치**다: 패널은 git이 나르는 `tools/`에
    있고, 옛 자리는 git이 안 나르는 데이터 폴더 아래에 있다.
    """
    from project_config import DATA_ROOT

    assert collector.PANEL_PATH.parent.name == "tools"
    assert DATA_ROOT not in collector.PANEL_PATH.parents
    assert DATA_ROOT in collector.LEGACY_PANEL_PATH.parents


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


# ---------------------------------------------------------------- 이어받기(--if-needed)
#
# 새벽 03:30 한 번이던 스케줄을 "켜져 있을 만한 시각 여럿 + 로그온"으로 바꾸면서
# 필요해진 판단이다(1.26.105). 하루에 여러 번 깨우므로, **이미 받은 날 다시
# 부르지 않는 것**이 곧 TMAP 한도를 지키는 일이 된다.

def _leg_row(**over):
    row = {
        "cluster": 0, "leg": 0, "from_id": "A", "to_id": "B",
        "from_lat": 36.3, "from_lon": 127.3, "to_lat": 36.35, "to_lon": 127.35,
        "straight_km": 5.0, "road_sec": 600.0,
        "observed_at": "2026-09-03 09:00", "start_time": "202609040500",
    }
    row.update(over)
    return row


def _save(collector, label, duration, count):
    import db
    frame = pd.DataFrame([_leg_row(leg=i) for i in range(count)])
    return db.save_output("road_leg", frame, run_label=label, duration=duration)


def test_회차_하나가_다_찼을_때의_구간_수(collector):
    """사슬 21지점 = 구간 20개. 지금 패널(5사슬)이면 회차당 100구간이다."""
    chains = [{"points": [None] * 21} for _ in range(5)]
    assert collector.legs_per_duration(chains) == 100


def test_다_받은_날은_부를_회차가_없다(collector):
    """이미 채운 날 다시 깨워도 TMAP을 부르지 않아야 한다 — 안 그러면 하루에
    여러 번 깨우는 스케줄이 그대로 한도 초과가 된다."""
    label = collector.PROBE_PREFIX + "2026-09-04"
    durations = ["_05_10", "_10_15"]
    for d in durations:
        _save(collector, label, d, 100)

    assert collector.pending_durations(durations, label, 100) == []


def test_잘린_회차만_다시_받는다(collector):
    """한도 소진으로 끊긴 회차만 채운다. 다 받은 회차를 지우고 새로 받으면
    호출을 두 배로 쓰게 된다."""
    label = collector.PROBE_PREFIX + "2026-09-04"
    _save(collector, label, "_05_10", 100)      # 다 받음
    _save(collector, label, "_10_15", 40)       # 중간에 끊김
    durations = ["_05_10", "_10_15", "_15_20"]  # 마지막은 아예 없음

    assert collector.pending_durations(durations, label, 100) == ["_10_15", "_15_20"]


def test_이어받기는_돌려_놓은_순서를_지킨다(collector):
    """rotate_durations()가 정한 차례가 '이번에 잘려도 되는 회차'의 순서다.
    여기서 다시 정렬하면 그 배려가 없어진다."""
    label = collector.PROBE_PREFIX + "2026-09-04"
    rotated = ["_15_20", "_20_05", "_05_10", "_10_15"]
    _save(collector, label, "_05_10", 100)

    assert collector.pending_durations(rotated, label, 100) == [
        "_15_20", "_20_05", "_10_15"]


def test_다른_날짜는_서로_간섭하지_않는다(collector):
    """어제 다 받았다고 오늘을 건너뛰면 안 된다."""
    yesterday = collector.PROBE_PREFIX + "2026-09-03"
    today = collector.PROBE_PREFIX + "2026-09-04"
    _save(collector, yesterday, "_05_10", 100)

    assert collector.pending_durations(["_05_10"], today, 100) == ["_05_10"]


def test_요일로_평일_휴일을_가른다(collector):
    """`--day-type`을 안 주면 그 날짜의 실제 요일로 자동 판정한다(1.26.158).

    예전에는 주말이면 통째로 건너뛰었다 — `start_time_for()`가 '다음 평일'만
    썼기 때문이다. 이제는 `day_type`을 받아 휴일이면 '다음 토·일·공휴일'을
    만들 수 있으므로, 주말도 **휴일 계수로** 잰다. `is_holiday_date()`는 그
    자동 판정에 쓰는 판별자다."""
    assert collector.is_holiday_date("2026-09-05") is True    # 토
    assert collector.is_holiday_date("2026-09-06") is True    # 일
    assert collector.is_holiday_date("2026-09-04") is False   # 금
    assert collector.is_holiday_date("2026-09-07") is False   # 월
    assert collector.is_holiday_date("2026-10-09") is True    # 한글날(평일 공휴일)


# ---------------------------------------------------------------- 모형 재추정의 day_type 필터

def _model_module():
    """experiments/params/road_time_model.py를 경로로 직접 읽는다."""
    path = PROJECT_ROOT / "experiments" / "params" / "road_time_model.py"
    spec = importlib.util.spec_from_file_location("road_time_model", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_모형_재추정은_평일과_휴일을_안_섞는다(collector):
    """`road_time_model.load_legs()`가 `runs.day_type`으로 패널분을 갈라야 한다.

    이 저장소는 "평일과 휴일은 절대 섞지 마라"를 전역 규약으로 두는데,
    휴일 도로 수집(1.26.158)을 더하면서 이 필터가 없으면 모형 재추정만
    그 규약을 어기게 된다 — 휴일 계수가 평일 회귀에 조용히 섞여 든다.
    """
    import db

    model = _model_module()

    with db.session() as conn:
        db.ensure_run(conn, "roadprobe-2026-09-04", kind="probe", day_type="weekday")
        db.ensure_run(conn, "roadprobe-holiday-2026-09-05", kind="probe", day_type="holiday")

    weekday_frame = pd.DataFrame([_leg_row(leg=i, straight_km=5.0, road_sec=600.0)
                                  for i in range(25)])
    holiday_frame = pd.DataFrame([_leg_row(leg=i, straight_km=5.0, road_sec=900.0)
                                  for i in range(25)])
    db.save_output("road_leg", weekday_frame,
                   run_label="roadprobe-2026-09-04", duration="_10_15")
    db.save_output("road_leg", holiday_frame,
                   run_label="roadprobe-holiday-2026-09-05", duration="_10_15")

    weekday_only = model.load_legs(include_pipeline=False, panel_only=False,
                                   day_type="weekday")
    holiday_only = model.load_legs(include_pipeline=False, panel_only=False,
                                   day_type="holiday")

    assert set(weekday_only["run_label"]) == {"roadprobe-2026-09-04"}
    assert set(holiday_only["run_label"]) == {"roadprobe-holiday-2026-09-05"}
    assert (weekday_only["road_sec"] == 600.0).all()
    assert (holiday_only["road_sec"] == 900.0).all()


def test_day_type_기록이_없는_옛_수집분도_평일로_읽는다(collector):
    """🔴 **`runs.day_type`만 믿으면 옛 수집분이 통째로 빠진다** (1.26.161).

    그 컬럼은 1.26.159에서 처음 기록하기 시작했다. 그 전 6일(08-31~09-08)은
    NULL이라 `== "weekday"` 비교가 어느 쪽에도 넣지 않았고, **판정용 표본이
    7일에서 1일로 줄었다** — `--status`는 7일이라 하는데 모형은 1일이라
    두 도구가 다른 답을 하고 있었다. 하필 채택 판정을 코앞에 둔 자리다.

    라벨이 사실을 알고 있다: 휴일분만 `roadprobe-holiday-`를 단다.
    """
    import db

    model = _model_module()

    # day_type을 **일부러 기록하지 않는다** — 1.26.159 이전 상태를 만든다.
    with db.session() as conn:
        db.ensure_run(conn, "roadprobe-2026-09-04", kind="probe")
        db.ensure_run(conn, "roadprobe-holiday-2026-09-05", kind="probe")

    for label in ("roadprobe-2026-09-04", "roadprobe-holiday-2026-09-05"):
        db.save_output("road_leg",
                       pd.DataFrame([_leg_row(leg=i) for i in range(25)]),
                       run_label=label, duration="_10_15")

    with db.session() as conn:
        남은_기록 = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE run_label LIKE 'roadprobe-%'"
            " AND day_type IS NOT NULL").fetchone()[0]
    assert 남은_기록 == 0, "이 테스트는 day_type이 비어 있는 상태를 재현해야 한다"

    weekday_only = model.load_legs(include_pipeline=False, panel_only=False,
                                   day_type="weekday")
    holiday_only = model.load_legs(include_pipeline=False, panel_only=False,
                                   day_type="holiday")

    assert set(weekday_only["run_label"]) == {"roadprobe-2026-09-04"}, (
        "day_type 기록이 없는 옛 평일 수집분이 빠졌다")
    assert set(holiday_only["run_label"]) == {"roadprobe-holiday-2026-09-05"}
