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

지키려는 것 일곱:
  ① 순수 픽업에서 다회 왕복이 실제로 일어나고 **모든 자전거가 회수된다**
  ② `greedy_route()`가 호출 측 dict를 소모하는 함정(함정 12)에 안 걸린다
  ③ 임계치를 바꿔도 **모집단이 흔들리지 않는다**
  ④ 세대 층화가 **세대 안에서** 분위를 나누고, 사전 등록한 세 갈래로만 판정한다(로드맵 F)
  ⑤ 통합 겹침이 회차 **시작순·합집합**으로 누적되고, 사전 등록한 경계·평평함 기준으로만 읽는다(로드맵 D)
  ⑥ 우측 절단의 되살아남을 **추적 창 뒤에서만** 세고, 사전 등록한 경계로만 읽는다(로드맵 E)
  ⑦ 양성 대조가 **관측 창을 맞추고** 자료 끝을 넘는 관측을 빼며, 사전 등록한 갈래로만 판정한다(로드맵 G)
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


# ─────────────────────────────────────────── ④ 세대 층화 (고장수거_로드맵 F)

def _cohort(generation, trips, gone):
    """`cohort_usage()`와 같은 모양의 코호트 표를 손으로 만든다."""
    return pd.DataFrame({
        "bike_no": [f"{generation}-{i:04d}" for i in range(len(trips))],
        "trips": list(trips),
        "survived": [not flag for flag in gone],
        "세대": generation,
    })


def _graded(generation, *, reverse=False):
    """이용량 1~100회 100대. 분위마다 40·30·20·10·0%가 사라진다(H2 방향).

    reverse=True면 거꾸로 — 많이 쓰인 쪽이 사라진다(H1 방향).
    """
    gone = []
    for quintile in range(5):
        k = 8 - 2 * quintile
        gone.extend(i < k for i in range(20))
    if reverse:
        gone = gone[::-1]
    return _cohort(generation, range(1, 101), gone)


def test_세대_층화는_세대_안에서_5분위를_나눈다(bc):
    """전체 기준으로 자르면 덜 쓰이는 세대가 아래 분위에 몰려 세대 효과가 다시 섞인다."""
    frame = pd.concat([
        _cohort("DJ2", range(1, 51), [i < 10 for i in range(50)]),
        _cohort("DJ3", range(1001, 1051), [i < 10 for i in range(50)]),
    ], ignore_index=True)

    for generation in ("DJ2", "DJ3"):
        counts = bc.generation_test(frame, generation)["표"]["대수"].tolist()
        assert counts == [10] * 5, (
            f"{generation}의 분위가 세대 안에서 나뉘지 않았다: {counts}"
            " — 전체 기준 분위면 DJ2가 아래 분위에만 몰린다")


def test_세_조건을_다_채워야_지지로_센다(bc):
    """단조 감소·방향·유의 셋의 AND — 유의해도 방향이 반대면 H1 쪽이다."""
    supportive = bc.generation_test(_graded("DJ3"), "DJ3")
    assert supportive["단조감소"] and supportive["방향"]
    assert supportive["p"] < bc.SIGNIFICANCE, f"p = {supportive['p']}"
    assert supportive["지지"]

    reversed_ = bc.generation_test(_graded("DJ3", reverse=True), "DJ3")
    assert reversed_["p"] < bc.SIGNIFICANCE, "반대 방향도 차이 자체는 유의하게 만들었다"
    assert not reversed_["방향"] and not reversed_["단조감소"]
    assert not reversed_["지지"], "많이 쓰인 쪽이 사라지는데 H2 지지로 셌다"


def test_세대_판정은_사전_등록한_세_갈래로만_말한다(bc):
    yes = {"세대": "DJ2", "지지": True}
    no = {"세대": "DJ3", "지지": False}

    assert "세대와 독립" in bc.generation_verdict([yes, {"세대": "DJ3", "지지": True}])
    limited = bc.generation_verdict([yes, no])
    assert "DJ2" in limited and "한정" in limited
    assert "철회" in bc.generation_verdict([dict(yes, 지지=False), no])


def test_유의하지_않음을_차이_없음으로_쓰지_않는다(bc):
    """세대로 나누면 표본이 절반이다 — 못 가린 것을 '같다'로 쓰면 안 된다."""
    assert "차이가 없다는 뜻이 아니라" in bc.describe_p(0.2)
    assert "유의하다" in bc.describe_p(1e-5)
    assert "검정할 수 없다" in bc.describe_p(float("nan"))


def test_코호트에_없는_세대는_지지하지_않고_죽지도_않는다(bc):
    result = bc.generation_test(_graded("DJ3"), "DJ2")
    assert result["대수"] == 0 and not result["지지"]


def test_단조이고_방향이_맞아도_유의하지_않으면_지지하지_않는다(bc):
    """p 조건을 따로 고정한다 — 세대로 나누면 표본이 절반이라 이 경우가 실제로 나올 수 있다.

    위 반대 방향 사례는 단조 감소부터 깨져 **p 조건이 빠져도 통과한다**(처음 짠 시험이
    그랬다). 분위마다 10대 중 4·3·2·1·0대가 사라지되 **분위 안에서 많이 쓰인 쪽**이
    사라지게 해 효과를 약하게 만든다(p ≈ 0.12).
    """
    gone = []
    for k in (4, 3, 2, 1, 0):
        gone.extend(i >= 10 - k for i in range(10))
    result = bc.generation_test(_cohort("DJ2", range(1, 51), gone), "DJ2")

    assert result["단조감소"] and result["방향"], "전제가 깨졌다 — 단조·방향은 맞아야 한다"
    assert result["p"] >= bc.SIGNIFICANCE, f"전제가 깨졌다 — 유의하지 않아야 한다: {result}"
    assert not result["지지"], "유의하지 않은데 지지로 셌다"


# ─────────────────────────────────────────── ⑤ 통합 겹침 (고장수거_로드맵 D)

def _visits(plan):
    """{회차: [대여소, …]} → `vrp_plan`에서 읽은 것과 같은 모양."""
    return pd.DataFrame([(duration, station) for duration, stations in plan.items()
                         for station in stations], columns=["duration", "station_id"])


def test_누적_겹침은_회차_시작순으로_쌓이고_마지막이_합집합이다(bc):
    """누적은 시작 시각순(_05_10 → _10_15 → _15_20)이라야 곡선의 모양을 읽는다.

    DB에서 읽은 순서가 섞여 와도 같은 표가 나와야 하고, 두 회차가 함께 들른 대여소를
    두 번 세면 누적이 부풀려진다 — 마지막 누적 행은 합집합과 같아야 한다.
    """
    broken = _bikes(10, station_span=5)                  # ST0000~ST0004에 2대씩
    plan = {"_15_20": ["ST0004"], "_05_10": ["ST0000", "ST0001"],
            "_10_15": ["ST0001", "ST0002"]}              # 일부러 섞인 순서
    table = bc.overlap_table(_visits(plan), broken)

    assert list(table["회차"]) == ["_05_10", "_10_15", "_15_20"]
    assert list(table["겹친대수"]) == [4, 4, 2]
    assert list(table["누적대수"]) == [4, 6, 8], "두 회차가 함께 들른 ST0001을 두 번 셌다"
    assert table["누적비율"].is_monotonic_increasing
    assert table["누적비율"].iloc[-1] == pytest.approx(80.0)


def test_통합_판정은_사전_등록한_경계와_평평함_기준으로만_말한다(bc):
    """35%·60%는 '혼합'에 넣고, 평평함은 '3회차 증분 ≤ 2회차 증분의 절반'이다(결과 보기 전에 정함)."""
    assert "곁가지" in bc.union_verdict(34.9)
    assert "혼합" in bc.union_verdict(35.0) and "혼합" in bc.union_verdict(60.0)
    assert "결론" in bc.union_verdict(60.1)

    def curve(cumulative, rounds=("_05_10", "_10_15", "_15_20")):
        return pd.DataFrame({"회차": list(rounds), "누적비율": cumulative})

    assert "평평" in bc.flattening(curve([20.0, 30.0, 35.0])), "증분 10 → 5는 절반이라 평평이다"
    assert "가파르" in bc.flattening(curve([20.0, 30.0, 35.1]))
    assert "평평" in bc.flattening(curve([20.0, 20.0, 20.0]))
    assert "가파르" in bc.flattening(curve([20.0, 20.0, 21.0])), \
        "2회차에 안 늘다 3회차에 늘면 평평이 아니다"
    assert "판정하지 않는다" in bc.flattening(curve([26.0, 30.0], rounds=("_05_10", "_20_05"))), \
        "야간 창이 섞인 조합(34장의 26.0%)으로 곡선을 읽으면 안 된다"


# ─────────────────────────────────────────── ⑥ 우측 절단 (고장수거_로드맵 E)

def _trip(bike, day, station="ST0500"):
    """대여 한 건 — `rental_history`의 탐지에 쓰는 열만."""
    return (bike, f"{day} 10:00:00", f"{day} 10:12:00", station, 36.35, 127.38)


def test_되살아남은_추적_창_뒤에_다시_나타난_자전거만_센다(bc, tmp_path):
    """소멸 판정 뒤 **검증 창에 대여 기록이 생긴** 자전거만 되살아났다고 센다.

    추적 창에 나타난 자전거는 소멸이 아니고, 운영시설로 들어간 자전거는 대상이 아니다.
    🔴 창을 앞으로 옮기면 뒤에 자료가 있다 — 마지막 위치를 **추적 창 끝까지**에서 찾지
    않으면, 정비대기로 들어갔다가 나중에 돌아온 자전거(G)가 코호트에 섞인다.
    검증 창이 추적 창과 겹치면 추적 창에 나타난 자전거를 되살아났다고 세므로 막는다.
    """
    import sqlite3

    con = sqlite3.connect(tmp_path / "t.db")
    con.execute("""CREATE TABLE rental_history (
        bike_no TEXT, rent_at TEXT, return_at TEXT,
        return_station TEXT, return_lat REAL, return_lon REAL)""")
    rows = [
        _trip("DJ3-A", "2025-04-03"), _trip("DJ3-A", "2025-11-20"),          # 소멸 → 되살아남
        _trip("DJ3-B", "2025-04-04"),                                          # 소멸 → 안 돌아옴
        _trip("DJ3-C", "2025-04-05"), _trip("DJ3-C", "2025-08-01"),          # 추적에 나타남
        _trip("DJ3-D", "2025-04-06", station="ST0001"),                        # 정비대기 반납
        _trip("DJ3-G", "2025-04-07", station="ST0001"), _trip("DJ3-G", "2025-11-02"),  # 정비 뒤 복귀
        _trip("DJ3-F", "2025-07-01"), _trip("DJ3-F", "2025-08-01"), _trip("DJ3-F", "2025-09-01"),
    ]
    con.executemany("INSERT INTO rental_history VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    follow = ["2025-07", "2025-08", "2025-09"]

    unbounded = bc.detect_broken(con, ["2025-04"], follow)
    broken = bc.detect_broken(con, ["2025-04"], follow, until_month="2025-09")
    report = bc.revival_table(con, broken, follow, ["2025-10", "2025-11", "2025-12"])

    assert "DJ3-G" in set(unbounded["bike_no"]), "전제 — 끝을 두지 않으면 나중 반납이 섞인다"
    assert sorted(broken["bike_no"]) == ["DJ3-A", "DJ3-B"], (
        f"추적 창 끝까지의 기록으로 판정하지 않았다: {sorted(broken['bike_no'])}")
    assert report["대상"] == 2 and report["되살아남"] == 1
    assert report["비율"] == pytest.approx(50.0)
    assert report["빈_추적달"] == [] and "2025-12" in report["빈_검증달"]

    with pytest.raises(SystemExit):
        bc.revival_table(con, broken, follow, ["2025-09", "2025-10"])
    con.close()


def test_되살아남_판정은_사전_등록한_경계로만_말한다(bc):
    """10%·30%는 가운데 갈래에 넣고, 추적 3개월이 아니거나 추적 창에 빈 달이 있으면 판정하지 않는다."""
    follow = ["2025-07", "2025-08", "2025-09"]

    def verdict(percent, follow_months=follow, empty=()):
        return bc.revival_verdict({"비율": percent, "빈_추적달": list(empty)}, follow_months)

    assert "사소" in verdict(9.9)
    assert "상한" in verdict(10.0) and "상한" in verdict(30.0)
    assert "다시 써야" in verdict(30.1)
    assert "판정하지 않는다" in verdict(50.0, follow_months=follow[:2]), "1·2개월은 민감도로만 본다"
    assert "판정하지 않는다" in verdict(5.0, empty=["2025-08"]), "빈 달이 있으면 소멸이 부풀려진다"
    assert "판정하지 않는다" in verdict(float("nan"))


# ─────────────────────────────────────────── ⑦ 양성 대조 (고장수거_로드맵 G)

def test_양성대조는_창을_맞추고_자료_끝을_넘는_관측을_뺀다(bc, tmp_path):
    """정비대기 반납 뒤 **창 안에** 다시 빌려졌는지만 센다(로드맵 G).

    🔴 반납 시점이 제각각이라 창을 안 맞추면 늦게 반납된 자전거가 저절로 '안 돌아온 것'이 된다 —
    창이 자료 끝을 넘는 관측은 **양쪽 모두** 빼야 한다(E에서 본 우측 절단).

    운영시설(`ST1220`) **반납 기록**은 기준점이 될 수 없지만 그 자전거의 **일반 반납**은 대조가
    된다. 운영 거점을 한 번 거쳤다고 자전거를 통째로 빼면 대조군이 *"한 번도 정비를 안 거친
    자전거"* 가 되어 통과 쪽으로 기울기 때문이다 — 불리한 쪽을 고른다.
    """
    import sqlite3

    con = sqlite3.connect(tmp_path / "t.db")
    con.execute("""CREATE TABLE rental_history (
        bike_no TEXT, rent_at TEXT, return_at TEXT,
        return_station TEXT, return_lat REAL, return_lon REAL)""")
    rows = [
        _trip("DJ3-A", "2025-04-01", station="ST0001"), _trip("DJ3-A", "2025-04-11"),  # 처치 · 재등장
        _trip("DJ3-B", "2025-04-02", station="ST0001"),                                  # 처치 · 안 돌아옴
        _trip("DJ3-C", "2026-03-20", station="ST0001"),                                  # 처치 · 창이 넘친다
        _trip("DJ3-D", "2025-04-03"), _trip("DJ3-D", "2025-05-01"),                      # 대조 · 재등장
        _trip("DJ3-E", "2025-04-04"),                                                    # 대조 · 안 돌아옴
        _trip("DJ3-F", "2025-08-04"),                                                    # 처치가 없는 달
        _trip("DJ3-H", "2025-04-05", station="ST1220"), _trip("DJ3-H", "2025-04-20"),  # 운영시설 반납은 기준점이 아니다
        _trip("DJ3-G", "2026-03-31"),                                                    # 자료 끝
    ]
    con.executemany("INSERT INTO rental_history VALUES (?,?,?,?,?,?)", rows)
    con.commit()

    report = bc.positive_control(con, horizon_days=90)
    con.close()

    assert report["처치"] == 2 and report["처치_재등장"] == 1, (
        f"창이 자료 끝을 넘는 처치 관측을 안 뺐다: {report['처치']}대")
    # 대조는 D·E·H 셋이다. H의 `ST1220` 반납이 기준점으로 쓰였다면 그 뒤 04-20 대여가 재등장으로
    # 잡혀 2대가 된다 — 재등장이 1대라는 것이 곧 운영시설 반납을 기준점에서 뺐다는 증거다.
    assert report["대조"] == 3 and report["대조_재등장"] == 1, (
        f"운영시설 반납이 기준점으로 쓰였거나 처치가 없는 달이 섞였다:"
        f" {report['대조']}대 · 재등장 {report['대조_재등장']}대")
    assert report["잘린_관측"] >= 2, "자료 끝을 넘는 관측을 빼지 않았다"
    assert list(report["월별"]["달"].unique()) == ["2025-04"]


def test_양성대조_판정은_사전_등록한_갈래로만_말한다(bc):
    """표본이 모자라면 못 한 검증으로 남기고, 합쳐서만 낮은 경우도 통과로 세지 않는다."""
    def verdict(처치, 처치율, 대조율, p, 비교한_달=4, 뒤집힌_달=0):
        return bc.control_verdict({"처치": 처치, "처치율": 처치율, "대조율": 대조율, "p": p,
                                   "비교한_달": 비교한_달, "뒤집힌_달": 뒤집힌_달})

    assert "판정하지 않는다" in verdict(29, 10.0, 80.0, 1e-9), "표본이 모자라면 유의해도 판정하지 않는다"
    assert "통과" in verdict(30, 10.0, 80.0, 1e-9)
    assert "뒤집힌" in verdict(100, 10.0, 80.0, 1e-9, 뒤집힌_달=2), "합쳐서만 낮으면 통과가 아니다"
    assert "오히려 더 돌아온다" in verdict(100, 90.0, 80.0, 1e-9)
    assert "못 가렸다" in verdict(100, 70.0, 80.0, 0.2)
