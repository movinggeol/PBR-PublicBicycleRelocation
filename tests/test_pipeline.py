"""파이프라인 스모크 테스트 (step0 → step1 → step2 → step4).

합성 데이터로 전 단계를 실제 실행해 산출물의 존재와 스키마를 확인한다.
API 키가 필요한 step0/tashu_api.py와 step3/main.py(TMAP)는 제외한다.

실데이터를 건드리지 않도록 실행마다 고유한 now/period 라벨을 쓰고,
끝나면 그 라벨이 붙은 파일만 지운다.
"""
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from project_config import PP_ROOT, PROJECT_ROOT
from tools.make_sample_data import generate

DURATION = "_05_10"
LABEL = f"smoketest-{os.getpid()}"

# 실행 순서 = 데이터 의존 순서
STAGES = [
    Path("step0_collect") / "extract_parking_lot.py",
    Path("step0_collect") / "api_to_info.py",
    Path("step0_collect") / "raw_to_net.py",
    Path("step0_collect") / "calculate_target_qty.py",
    Path("step1_cluster") / "top_st_clustering.py",
    Path("step1_cluster") / "st_visualization.py",
    Path("step2_optimize") / "ilp.py",
    Path("step2_optimize") / "vrp.py",
    Path("step4_metrics") / "imbalance.py",
]


def _out(relative: str) -> Path:
    return PP_ROOT / relative.format(label=LABEL, duration=DURATION)


@pytest.fixture(scope="module")
def smoke_db(tmp_path_factory):
    """이중 기록이 실제 DB(data/bike_system.db)를 오염시키지 않도록 별도 파일을 쓴다."""
    return tmp_path_factory.mktemp("db") / "smoke.db"


@pytest.fixture(scope="module")
def pipeline_run(tmp_path_factory, smoke_db):
    """합성 데이터를 만들고 파이프라인을 한 번 실행한다."""
    raw_path = tmp_path_factory.mktemp("raw") / "합성_대여이력.csv"
    generate(now=LABEL, period=LABEL, stations=70, days=12,
             rentals_per_day=500, raw_path=raw_path)

    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               PBR_DB_PATH=str(smoke_db))
    results = []

    for script in STAGES:
        completed = subprocess.run(
            [sys.executable, str(script),
             "--now", LABEL, "--period", LABEL,
             "--duration", DURATION, "--raw-file", str(raw_path)],
            cwd=PROJECT_ROOT, env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        results.append((script, completed))
        if completed.returncode != 0:
            pytest.fail(
                f"{script} 실패 (exit {completed.returncode})\n"
                f"--- stdout ---\n{completed.stdout[-2000:]}\n"
                f"--- stderr ---\n{completed.stderr[-2000:]}"
            )

    yield results

    # 이 실행이 만든 파일만 정리한다.
    for path in PP_ROOT.rglob(f"*{LABEL}*"):
        if path.is_file():
            path.unlink()


def test_all_stages_succeed(pipeline_run):
    assert len(pipeline_run) == len(STAGES)
    assert all(c.returncode == 0 for _, c in pipeline_run)


@pytest.mark.parametrize("relative", [
    "대여소별 주차대수/대여소별_주차대수 ({label}).csv",
    "대여소 정보/st_info ({label}).csv",
    "순수요/st_net_daily ({label}).csv",
    "재배치 정보/rebal_qty{duration} ({label}).csv",
    "ILP/후보/top{duration} ({label}).csv",
    "ILP/visualization/clusterd_map{duration} ({label}).html",
    "ILP/ILP_plan{duration} ({label}).csv",
    "VRP/VRP_plan{duration} ({label}).csv",
    "성능 지표/verification{duration} ({label}).csv",
    "성능 지표/route_summary{duration} ({label}).csv",
    "성능 지표/visualization/imbalance_map{duration} ({label}).html",
])
def test_output_exists(pipeline_run, relative):
    path = _out(relative)
    assert path.is_file(), f"산출물 없음: {path}"
    assert path.stat().st_size > 0, f"산출물이 비어 있음: {path}"


def test_pick_drop_candidates_schema(pipeline_run):
    """step1 후보 파일에 다음 단계가 요구하는 컬럼과 Pick·Drop이 모두 있다."""
    df = pd.read_csv(_out("ILP/후보/top{duration} ({label}).csv"), encoding="utf-8")
    for col in ["station_id", "lat", "lon", "rebal_qty", "cluster", "stock", "target_qty"]:
        assert col in df.columns, f"{col} 컬럼 없음"
    assert (df["rebal_qty"] < 0).any(), "Pick 대상이 없다"
    assert (df["rebal_qty"] > 0).any(), "Drop 대상이 없다"
    assert df["station_id"].is_unique


def test_ilp_plan_respects_supply(pipeline_run):
    """ILP 이동량이 Pick 대여소의 공급 가능량을 넘지 않는다."""
    top = pd.read_csv(_out("ILP/후보/top{duration} ({label}).csv"), encoding="utf-8")
    plan = pd.read_csv(_out("ILP/ILP_plan{duration} ({label}).csv"), encoding="utf-8")
    assert not plan.empty and (plan["qty"] > 0).all()

    supply = (-top.set_index("station_id")["rebal_qty"].clip(upper=0))
    moved = plan.groupby("pick_station_id")["qty"].sum()
    for station_id, qty in moved.items():
        assert qty <= supply[station_id], f"{station_id} 공급량 초과: {qty} > {supply[station_id]}"


def test_vrp_plan_has_time_columns(pipeline_run):
    """1.2.0에서 추가한 거리·시간 컬럼이 실제로 채워진다(step4 경로요약의 입력)."""
    df = pd.read_csv(_out("VRP/VRP_plan{duration} ({label}).csv"), encoding="utf-8")
    for col in ["cluster", "from_id", "to_id", "action", "qty",
                "distance_km", "travel_sec", "work_sec", "cum_sec"]:
        assert col in df.columns, f"{col} 컬럼 없음"
    assert set(df["action"]) <= {"pick", "drop", "return"}
    assert (df["cum_sec"] >= 0).all()


def test_vrp_assigns_real_vehicles(pipeline_run, smoke_db):
    """클러스터마다 실제 차량이 배정되고 기록된다 (docs/FLEET.md)."""
    import db

    plan = pd.read_csv(_out("VRP/VRP_plan{duration} ({label}).csv"), encoding="utf-8")
    assert "vehicle_id" in plan.columns
    assert plan["vehicle_id"].notna().all(), "배정되지 않은 구간이 있다"

    # 클러스터 1개 = 차량 1대 (중복 배정 금지)
    pairs = plan[["cluster", "vehicle_id"]].drop_duplicates()
    assert pairs["cluster"].is_unique
    assert pairs["vehicle_id"].is_unique

    with db.session(smoke_db) as conn:
        history = db.assignment_history(conn, run_label=LABEL)
        assert len(history) == plan["cluster"].nunique()
        assert (history["minutes"] > 0).all()
        assert (history["bikes"] > 0).all()

        workload = db.vehicle_workload(conn)
        assert (workload["rounds"] > 0).sum() == len(history)


def test_assigned_bikes_match_ilp_plan(pipeline_run, smoke_db):
    """기록된 '처리 대수'가 실제로 옮긴 자전거 수와 같아야 한다.

    pick과 drop의 qty를 모두 더하면 한 대를 두 번 세어 2배가 된다(과거 결함).
    ILP 계획 대수가 정답이다.
    """
    import db

    ilp_total = pd.read_csv(
        _out("ILP/ILP_plan{duration} ({label}).csv"), encoding="utf-8")["qty"].sum()

    with db.session(smoke_db) as conn:
        assigned_total = db.assignment_history(conn, run_label=LABEL)["bikes"].sum()

    assert assigned_total == ilp_total, \
        f"배정 기록 {assigned_total}대 vs ILP 계획 {ilp_total}대"


def test_route_summary_schema(pipeline_run):
    df = pd.read_csv(_out("성능 지표/route_summary{duration} ({label}).csv"), encoding="utf-8")
    for col in ["cluster", "방문수", "처리대수", "총이동거리_km", "총소요시간_분"]:
        assert col in df.columns, f"{col} 컬럼 없음"
    assert (df["총이동거리_km"] > 0).all()


def test_improvement_is_positive(pipeline_run):
    """재배치가 불균형을 실제로 줄인다(개선량 음수 없음)."""
    df = pd.read_csv(_out("성능 지표/verification{duration} ({label}).csv"), encoding="utf-8")
    assert (df["improvement"] >= 0).all()
    assert df["improvement_rate"].mean() > 0


@pytest.mark.parametrize("table", [
    "parking_lot", "station_info", "net_demand", "rebalance_plan",
    "pick_drop", "ilp_plan", "vrp_plan", "metrics", "route_summary",
])
def test_stages_write_to_database(pipeline_run, smoke_db, table):
    """각 단계가 CSV와 함께 DB에도 기록한다 (DB_PLAN 2단계 이중 기록).

    tashu_api(station_stock)는 API 키가 필요해 이 테스트 범위 밖이다.
    """
    import db

    with db.session(smoke_db) as conn:
        stored = db.load_frame(conn, table)
        assert not stored.empty, f"{table} 테이블이 비어 있다(이중 기록 누락)"


def test_database_matches_csv(pipeline_run, smoke_db):
    """이중 기록된 DB 내용이 CSV와 일치한다."""
    import db

    checks = [
        ("pick_drop", "ILP/후보/top{duration} ({label}).csv"),
        ("ilp_plan", "ILP/ILP_plan{duration} ({label}).csv"),
        ("vrp_plan", "VRP/VRP_plan{duration} ({label}).csv"),
        ("metrics", "성능 지표/verification{duration} ({label}).csv"),
        ("route_summary", "성능 지표/route_summary{duration} ({label}).csv"),
    ]
    with db.session(smoke_db) as conn:
        for table, relative in checks:
            csv_rows = len(pd.read_csv(_out(relative), encoding="utf-8"))
            stored = db.load_frame(conn, table, run_label=LABEL, duration=DURATION)
            assert len(stored) == csv_rows, f"{table}: DB {len(stored)}행 vs CSV {csv_rows}행"
            assert set(stored["run_label"]) == {LABEL}


def test_kpi_summary_written(pipeline_run, smoke_db):
    """step4가 실행 지표를 kpi_summary에 한 줄로 기록한다 (docs/KPI.md)."""
    import db

    with db.session(smoke_db) as conn:
        rows = db.load_kpi(conn, run_label=LABEL, duration=DURATION)

    assert len(rows) == 1, "실행 1건 = 1행이어야 한다"
    row = rows.iloc[0]

    for column in ["stations", "clusters", "vehicles_used", "bikes_moved",
                   "avg_improvement_rate", "target_met_ratio",
                   "total_distance_km", "max_cluster_minutes",
                   "time_budget_met", "improvement_per_km"]:
        assert pd.notna(row[column]), f"{column}이 비어 있다"

    assert 0 <= row["avg_improvement_rate"] <= 1
    assert 0 <= row["time_budget_met"] <= 1
    assert row["total_distance_km"] > 0

    # 결품 시뮬레이션 (KPI.md 4단계) — 재배치 후가 전보다 나빠지면 안 된다
    assert pd.notna(row["stockout_hours_before"]), "결품 시뮬레이션이 실행되지 않았다"
    assert row["stockout_hours_after"] <= row["stockout_hours_before"] + 1e-9

    # 다른 산출물과 숫자가 맞아야 한다
    plan = pd.read_csv(_out("VRP/VRP_plan{duration} ({label}).csv"), encoding="utf-8")
    assert row["bikes_moved"] == plan[plan["action"] == "pick"]["qty"].sum()
    assert row["clusters"] == plan["cluster"].nunique()


def test_run_registry_populated(pipeline_run, smoke_db):
    """실행 라벨이 runs 테이블에 기록되고, 라벨 생략 조회가 최신을 찾는다."""
    import db

    with db.session(smoke_db) as conn:
        assert db.list_runs(conn)["run_label"].tolist() == [LABEL]
        assert db.latest_label(conn, "metrics") == LABEL
        assert not db.load_frame(conn, "metrics").empty      # 웹 API가 쓸 경로


def test_csv_to_db_tool_imports_outputs(pipeline_run, tmp_path):
    """CSV → DB 적재 도구도 같은 결과를 낸다(이관 검증·복구 경로).

    파이프라인이 방금 만든 CSV를 적재하므로 컬럼이 하나라도 어긋나면 여기서 잡힌다.
    """
    import db
    from tools.csv_to_db import import_outputs

    with db.session(tmp_path / "imported.db") as conn:
        loaded = import_outputs(conn, now=LABEL, period=LABEL, durations=[DURATION])

        assert {"station_info", "parking_lot", "net_demand",
                f"rebalance_plan{DURATION}", f"pick_drop{DURATION}",
                f"ilp_plan{DURATION}", f"vrp_plan{DURATION}",
                f"metrics{DURATION}", f"route_summary{DURATION}"} <= set(loaded)
        assert all(rows > 0 for rows in loaded.values())

        csv_rows = len(pd.read_csv(_out("ILP/후보/top{duration} ({label}).csv"), encoding="utf-8"))
        assert len(db.load_frame(conn, "pick_drop", run_label=LABEL)) == csv_rows
