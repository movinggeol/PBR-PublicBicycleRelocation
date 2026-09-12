"""성과 지표(kpi_summary) 저장·조회와 /kpi 화면 검증 (docs/분석/KPI.md).

목적은 흩어져 있던 지표를 실행 1건 = 1행으로 모아 실행 간 비교를 쉽게 하는 것이다.
"""
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import db
from webapp.app import app

OLD, NEW = "2026-01-01 09", "2026-05-21 18"


def _metrics(rate, **overrides):
    base = {
        "stations": 87, "clusters": 10, "vehicles_used": 10, "bikes_moved": 307,
        "avg_improvement_rate": rate, "pick_improvement_rate": rate - 0.15,
        "drop_improvement_rate": rate + 0.12, "target_met_ratio": 0.06,
        "total_distance_km": 304.9, "max_cluster_minutes": 110.6,
        "avg_cluster_minutes": 91.7, "time_budget_minutes": 120.0,
        "time_budget_met": 1.0, "vehicle_load_gap": 50.3,
        "improvement_per_km": 2.08, "cluster_max_imbalance": 6,
    }
    base.update(overrides)
    return base


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "kpi.db")
    db.init_schema(connection)
    yield connection
    connection.close()


def test_save_and_load(conn):
    db.save_kpi(conn, NEW, "_05_10", _metrics(0.65))
    rows = db.load_kpi(conn)

    assert len(rows) == 1
    assert rows["run_label"].iloc[0] == NEW
    assert rows["avg_improvement_rate"].iloc[0] == pytest.approx(0.65)
    assert rows["computed_at"].notna().all()


def test_save_is_idempotent(conn):
    """같은 실행·회차를 다시 저장하면 덮어쓴다(행이 늘지 않는다)."""
    db.save_kpi(conn, NEW, "_05_10", _metrics(0.65))
    db.save_kpi(conn, NEW, "_05_10", _metrics(0.71))

    rows = db.load_kpi(conn)
    assert len(rows) == 1
    assert rows["avg_improvement_rate"].iloc[0] == pytest.approx(0.71)


def test_partial_metrics_leave_nulls(conn):
    """계산하지 못한 지표는 빼고 넘기면 NULL로 남는다(4·5단계용 컬럼)."""
    db.save_kpi(conn, NEW, "_05_10", {"avg_improvement_rate": 0.65})

    row = db.load_kpi(conn).iloc[0]
    assert row["avg_improvement_rate"] == pytest.approx(0.65)
    assert pd.isna(row["stockout_hours_before"])
    assert pd.isna(row["demand_mae"])


def test_unknown_field_is_ignored(conn):
    """스키마에 없는 키를 넘겨도 깨지지 않는다."""
    db.save_kpi(conn, NEW, "_05_10", {"avg_improvement_rate": 0.65, "없는지표": 123})
    assert len(db.load_kpi(conn)) == 1


def test_filters(conn):
    db.save_kpi(conn, OLD, "_05_10", _metrics(0.60))
    db.save_kpi(conn, NEW, "_05_10", _metrics(0.65))
    db.save_kpi(conn, NEW, "_10_15", _metrics(0.71))

    assert len(db.load_kpi(conn)) == 3
    assert len(db.load_kpi(conn, run_label=NEW)) == 2
    assert len(db.load_kpi(conn, duration="_05_10")) == 2
    assert len(db.load_kpi(conn, run_label=NEW, duration="_10_15")) == 1


def test_latest_run_comes_first(conn):
    db.save_kpi(conn, OLD, "_05_10", _metrics(0.60))
    db.save_kpi(conn, NEW, "_05_10", _metrics(0.65))

    assert db.load_kpi(conn)["run_label"].tolist() == [NEW, OLD]


def test_compare_runs_with_sql(conn):
    """지표를 한 줄로 모은 목적: 실행 간 비교가 쿼리 하나가 된다."""
    db.save_kpi(conn, OLD, "_05_10", _metrics(0.60, total_distance_km=350.0))
    db.save_kpi(conn, NEW, "_05_10", _metrics(0.65, total_distance_km=304.9))

    compared = pd.read_sql(
        "SELECT run_label, avg_improvement_rate, total_distance_km"
        " FROM kpi_summary WHERE duration = '_05_10' ORDER BY run_label", conn)

    assert compared["avg_improvement_rate"].tolist() == pytest.approx([0.60, 0.65])
    assert compared["total_distance_km"].tolist() == pytest.approx([350.0, 304.9])


# ---------------- 결품 시뮬레이션 (KPI.md 4단계) ----------------

@pytest.fixture(scope="module")
def step4():
    """step4 모듈을 경로로 직접 읽는다(테스트마다 독립된 이름으로 올린다)."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "pipeline" / "step4_metrics" / "imbalance.py"
    spec = importlib.util.spec_from_file_location("_imbalance", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_duration_hours(step4):
    assert step4.duration_hours("_05_10") == [5, 6, 7, 8, 9]
    assert step4.duration_hours("_20_05") == [20, 21, 22, 23, 0, 1, 2, 3, 4]


def test_stockout_counts_empty_hours(step4):
    """재고 10대에 시간당 순수요 4대면 3시간째부터 바닥난다."""
    net = pd.DataFrame([{f"net_{h:02d}": 4 for h in range(24)}])
    hours = [5, 6, 7, 8, 9]

    out = step4._stockout_hours(net, pd.Series([10.0]), pd.Series([30]), hours)

    # 10 → 6 → 2 → 0 → 0 → 0 : 5시간 중 3시간 결품
    assert out.iloc[0] == 3


def test_stockout_zero_when_supply_is_enough(step4):
    """수요를 감당할 재고가 있으면 결품이 없다."""
    net = pd.DataFrame([{f"net_{h:02d}": 1 for h in range(24)}])
    out = step4._stockout_hours(net, pd.Series([50.0]), pd.Series([60]), [5, 6, 7, 8, 9])
    assert out.iloc[0] == 0


def test_stockout_capped_by_capacity(step4):
    """반납이 많아도 거치대 수를 넘겨 쌓이지 않는다(순수요 음수)."""
    net = pd.DataFrame([{f"net_{h:02d}": -100 for h in range(24)}])
    out = step4._stockout_hours(net, pd.Series([5.0]), pd.Series([10]), [5, 6])
    assert out.iloc[0] == 0        # 가득 차 있으니 결품 아님


def test_stockout_never_goes_negative(step4):
    """없는 자전거는 빌릴 수 없다 — 재고가 음수로 내려가지 않는다."""
    net = pd.DataFrame([{f"net_{h:02d}": 999 for h in range(24)}])
    hours = [5, 6, 7]
    out = step4._stockout_hours(net, pd.Series([1.0]), pd.Series([30]), hours)
    assert out.iloc[0] == len(hours)   # 전 시간 결품이지 그 이상은 없다


def test_saturation_counts_full_hours(step4):
    """포화 시간 — 거치대가 가득 차 **반납을 못 받는** 시간을 센다 (1.26.101).

    결품만 보면 "채우면 좋다"가 되는데, 채워서 포화가 늘면 반납이 막힌다.
    두 지표는 서로 반대 방향이라 함께 봐야 한다.
    """
    # 거치대 10, 시작 5, 매시 20대씩 반납(순수요 -20) → 첫 시간부터 가득
    net = pd.DataFrame([{f"net_{h:02d}": -20 for h in range(24)}])
    out = step4._simulate_stock(net, pd.Series([5.0]), pd.Series([10.0]), [5, 6])

    assert out["saturated"].iloc[0] == 2
    assert out["stockout"].iloc[0] == 0        # 가득 찬 것은 결품이 아니다


def test_simulate_stock_collects_what_the_clips_threw_away(step4):
    """양쪽 clip에서 **잘려 나간 양**이 곧 못 빌린 수·못 세운 수다.

    이 값들은 예전에는 `clip()` 안에서 사라졌다. KPI.md 3-B가 미구현으로
    남겨 둔 순수요 충족률·포화 시간이 정확히 이 두 값이다.
    """
    # 거치대 10, 시작 3.  +5 → 재고 -2를 0으로 자름(못 빌린 2)
    #                     -20 → 재고 20을 10으로 자름(못 세운 10)
    net = pd.DataFrame({"net_05": [5.0], "net_06": [-20.0]})
    out = step4._simulate_stock(net, pd.Series([3.0]), pd.Series([10.0]), [5, 6])

    assert out["unmet"].iloc[0] == 2.0        # 3대뿐인데 5대를 빌리려 했다
    assert out["refused"].iloc[0] == 10.0     # 20대를 세우려 했으나 자리가 10
    assert out["outflow"].iloc[0] == 5.0      # 충족률의 분모(순유출만 센다)


def test_stockout_hours_is_unchanged_by_the_refactor(step4):
    """`_stockout_hours`는 **껍데기가 됐어도 값이 같아야** 한다 (1.26.101).

    실험 스크립트 넷이 이 이름으로 부른다(baseline_compare · budget_enforce ·
    min_qty_sweep · top_limit_sweep). 값이 달라지면 그 표들이 전부 무효가 된다.
    """
    net = pd.DataFrame([{f"net_{h:02d}": 4 for h in range(24)}])
    hours = [5, 6, 7, 8, 9]
    initial, capacity = pd.Series([10.0]), pd.Series([30])

    thin = step4._stockout_hours(net, initial, capacity, hours)
    core = step4._simulate_stock(net, initial, capacity, hours)["stockout"]

    assert thin.equals(core)
    assert thin.iloc[0] == 3          # 10 → 6 → 2 → 0 → 0 → 0
    assert thin.dtype == core.dtype   # 정수 Series여야 한다(호출부가 합산한다)


def test_new_kpi_fields_are_registered_for_saving(step4):
    """계산해도 `KPI_FIELDS`에 없으면 **조용히 버려진다**."""
    import db

    for field in ("saturation_hours_before", "saturation_hours_after",
                  "demand_fulfill_before", "demand_fulfill_after"):
        assert field in db.KPI_FIELDS, f"{field}가 KPI_FIELDS에 없다"
        assert field in db.SCHEMA, f"{field} 컬럼이 스키마에 없다"


# ---------------- 웹 화면 ----------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PBR_DB_PATH", str(tmp_path / "kpi_api.db"))

    with db.session() as conn:
        db.record_run(conn, OLD, period="25년 10월", duration="_05_10")
        db.record_run(conn, NEW, period="25년 11월", duration="_05_10")
        db.save_kpi(conn, OLD, "_05_10", _metrics(
            0.60, target_met_ratio=0.03,
            stockout_hours_before=2.50, stockout_hours_after=0.90))
        db.save_kpi(conn, NEW, "_05_10", _metrics(
            0.65, target_met_ratio=0.06,
            stockout_hours_before=2.30, stockout_hours_after=0.50))

    with TestClient(app) as c:
        yield c


def test_kpi_page_renders(client):
    html = client.get("/kpi").text

    assert "성과 지표" in html
    assert "65%" in html                      # 최신 개선률
    assert "계획 달성률" in html               # 지표 성격 안내
    assert "목표 도달 비율" in html


def test_kpi_page_shows_stockout(client):
    """결품 시간이 전→후로 표시되고, 시뮬레이션임을 밝힌다."""
    html = client.get("/kpi").text

    assert "결품 시간" in html
    assert "2.3h" in html or "2.3" in html
    assert "시뮬레이션" in html


def test_kpi_page_shows_delta(client):
    """직전 실행 대비 증감이 보인다 — 설정을 바꿔가며 비교하는 게 목적이다."""
    html = client.get("/kpi").text
    assert "▲" in html or "▼" in html


def test_kpi_api(client):
    body = client.get("/api/kpi").json()

    assert body["count"] == 2
    assert body["rows"][0]["run_label"] == NEW      # 최신순
    assert body["rows"][0]["avg_improvement_rate"] == pytest.approx(0.65)


def test_kpi_api_filters(client):
    body = client.get("/api/kpi", params={"run_label": OLD}).json()

    assert body["count"] == 1
    assert body["rows"][0]["avg_improvement_rate"] == pytest.approx(0.60)


def test_kpi_page_without_data(tmp_path, monkeypatch):
    """지표가 없어도 화면이 뜬다."""
    monkeypatch.setenv("PBR_DB_PATH", str(tmp_path / "empty.db"))
    with TestClient(app) as c:
        res = c.get("/kpi")
    assert res.status_code == 200
    assert "기록된 지표가 없습니다" in res.text


def test_kpi_page_survives_null_metrics(tmp_path, monkeypatch):
    """계산 못 한 지표(NULL)가 섞여도 화면이 뜬다.

    `save_kpi`는 빠진 지표를 NULL로 남기는 것이 규약인데(위
    test_partial_metrics_leave_nulls), /kpi가 그 값을 그대로 round에 넣어
    화면 전체가 500으로 죽었다 — 표에는 '—'로 나와야 한다.
    """
    monkeypatch.setenv("PBR_DB_PATH", str(tmp_path / "null.db"))

    with db.session() as conn:
        db.record_run(conn, NEW, period="25년 11월", duration="_10_15")
        db.save_kpi(conn, NEW, "_10_15", {"avg_improvement_rate": 0.5})

    with TestClient(app) as c:
        res = c.get("/kpi")

    assert res.status_code == 200
    assert "—" in res.text
    assert ">None<" not in res.text, "결측이 'None'으로 새어 나왔다"


# ───────── 지표의 이름 (1.26.157) ─────────
#
# 29장이 분모를 총 대여로 바꿔 재 보고 **순서가 뒤집히는 것**을 확인한 뒤,
# 7장 고찰이 *"지표는 그대로 두되 이름을 '순수요 충족률'로 정확히 한다"* 로
# 정리했다. 그 결정이 코드·참조 문서에는 닿지 않아 옛 이름이 남아 있었다.

# 지표를 **정의하거나 가르치는** 자리 — 여기에는 옛 이름이 있으면 안 된다.
_이름을_가르치는_자리 = [
    "pipeline/step4_metrics/imbalance.py",
    "docs/분석/KPI.md",
    "docs/구현/steps/step4_metrics.md",
]


@pytest.mark.parametrize("경로", _이름을_가르치는_자리)
def test_충족률을_옛_이름으로_가르치지_않는다(경로):
    """`수요 충족률`은 분모를 **총 대여**로 읽히게 한다 — 실제는 순수요다.

    분모가 2.3~5.4배 다르고 `_15_20`은 대소까지 뒤집힌다(0.378 → 0.489,
    EXPERIMENTS 29장). 이름 하나가 인용을 틀리게 만드는 자리라 회귀로 못박는다.

    ⚠️ **역사 기록과 29장 본문은 대상이 아니다.** 버전관리·TODO는 *그때*
    무엇을 문제로 봤는지를 남기는 자리고, 29장은 *"'수요 충족률'이 아니라
    '순수요 충족률'이다"* 라는 **주장 자체**라 옛 이름을 인용해야 한다.
    """
    from pathlib import Path

    본문 = (Path(__file__).resolve().parents[1] / 경로).read_text(encoding="utf-8")
    남은 = [줄 for 줄 in 본문.splitlines()
            if "수요 충족률" in 줄
            and "순수요 충족률" not in 줄
            and "이름이 '수요 충족률'이었으나" not in 줄]  # 옛 이름을 경고로 인용한다

    assert not 남은, (
        f"{경로}가 아직 옛 이름으로 가르친다 — 분모가 순수요인데 "
        f"'총 대여 대비'로 읽힌다(7장 고찰 · EXPERIMENTS 29장):\n  "
        + "\n  ".join(남은))


# ---- 입력 컬럼 순서가 달라도 같은 것을 집는다 (1.26.164) ----

def _후보_프레임(**추가):
    """step1 후보의 최소 형태. 추가 컬럼은 앞에 붙여 자리를 민다."""
    import pandas as pd

    본문 = {
        "station_id": ["ST0001", "ST0002"],
        "station_name": ["가", "나"],
        "lat": [36.35, 36.36], "lon": [127.38, 127.39],
        "parking_lot": [20, 20],
        "stock": [2, 18],
        "target_qty": [10, 10],
        "rebal_qty": [8, -8],
        "mu": [3.0, 3.0], "sigma": [1.0, 1.0],
        "cluster": [0, 1],
    }
    return pd.DataFrame({**추가, **본문})


def test_demand_satisfaction은_컬럼_순서가_아니라_이름으로_고른다(step4):
    """🔴 DB에서 읽으면 앞에 스코프 컬럼이 붙어 **자리가 밀린다.**

    예전 `demand_satisfaction()`은 `iloc[:, [0,1,2,3,10,5,8,9,6,7]]`로 **자리**를
    집었다 — CSV만 받던 시절의 순서를 굳힌 것이다. 단계 간 전달을 DB로 옮기자
    `run_label`·`duration` 둘이 앞에 붙어 모든 자리가 2씩 밀렸고, `cluster`를
    집을 자리가 `mu`를 집어 지도 그리기가 KeyError로 죽었다(실제로 겪었다).

    같은 내용을 **컬럼 순서만 다르게** 두 번 넣어 결과가 같은지 본다.
    """
    csv_모양 = step4.demand_satisfaction(_후보_프레임())
    db_모양 = step4.demand_satisfaction(
        _후보_프레임(run_label=["L", "L"], duration=["_05_10", "_05_10"]))

    assert list(csv_모양.columns) == list(db_모양.columns)
    assert csv_모양["cluster"].tolist() == [0, 1]        # mu(3.0)를 집으면 안 된다
    assert db_모양["cluster"].tolist() == [0, 1]
    assert csv_모양["improvement"].tolist() == db_모양["improvement"].tolist()
