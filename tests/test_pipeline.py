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

from project_config import DATA_ROOT, PP_ROOT, PROJECT_ROOT
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

    # ⚠️ **`try`로 감싸는 것이 요점이다.** 아래 `pytest.fail()`은 `yield` 앞이라,
    # 단계가 하나라도 실패하면 teardown에 도달하지 못한다 — 그러면 이 실행이
    # 실제 `data/pp_data`에 만든 산출물이 **그대로 남는다.** 스텝 스크립트는
    # 경로를 `DATA_ROOT / "..."`로 직접 조립해서 임시 경로로 돌릴 수도
    # 없다. 실제로 잔여물 63개를 찾아 치웠다(1.26.124).
    #
    # **정리가 필요한 때는 바로 일이 잘못됐을 때다.** 잘 끝난 실행은 어차피
    # 스스로 치운다.
    try:
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
    finally:
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
    """클러스터마다 실제 차량이 배정되고 기록된다 (docs/구현/FLEET.md)."""
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
    """step4가 실행 지표를 kpi_summary에 한 줄로 기록한다 (docs/분석/KPI.md)."""
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

# ---------------------------------------------------------------- --skip-api

def test_skip_api_inherits_the_latest_snapshot(tmp_path, monkeypatch):
    """`--skip-api`는 **가장 최근 실행의 재고를 물려받는다.**

    API 수집이 만드는 세 파일은 실행 라벨마다 따로다. 그래서 예전에는 `--skip-api`를
    켠 채 **새 실행 이름**을 쓰면 파일이 없어 다음 단계가 바로 멈췄다 — 웹 폼의
    "API 수집 생략"도 같았다.
    """
    import project_config
    import run_pipeline

    monkeypatch.setattr(project_config, "PP_ROOT", tmp_path)
    for subdir, name in project_config.API_SNAPSHOTS:
        folder = tmp_path / subdir
        folder.mkdir(parents=True)
        (folder / name.format(now="어제")).write_text("x", encoding="utf-8")

    assert project_config.snapshot_labels() == ("어제",)
    assert run_pipeline.inherit_snapshot("오늘") == 3

    for path in project_config.snapshot_paths("오늘"):
        assert path.exists(), f"{path.name}을 물려받지 못했다"

    # 이미 다 있으면 아무것도 하지 않는다(덮어쓰지 않는다).
    assert run_pipeline.inherit_snapshot("오늘") == 0


def test_skip_api_refuses_when_there_is_nothing_to_inherit(tmp_path, monkeypatch):
    """물려받을 것이 없으면 **시작 전에** 멈춘다 — 한참 뒤에 파일 없다고 죽지 않는다."""
    import project_config
    import run_pipeline

    monkeypatch.setattr(project_config, "PP_ROOT", tmp_path)
    assert run_pipeline.inherit_snapshot("오늘") == -1


def test_snapshot_labels_skips_half_finished_runs(tmp_path, monkeypatch):
    """세 파일이 다 있는 라벨만 후보다.

    반쯤 있는 라벨을 물려받으면 그 다음 단계에서 멈춘다 — 그러면 고친 의미가 없다.
    """
    import project_config

    monkeypatch.setattr(project_config, "PP_ROOT", tmp_path)
    for index, (subdir, name) in enumerate(project_config.API_SNAPSHOTS):
        folder = tmp_path / subdir
        folder.mkdir(parents=True)
        (folder / name.format(now="온전한")).write_text("x", encoding="utf-8")
        if index == 0:      # 첫 파일만 있는 라벨
            (folder / name.format(now="반쪽")).write_text("x", encoding="utf-8")

    assert project_config.snapshot_labels() == ("온전한",)


def test_지도_다시_그리기가_실패를_종료_코드로_말한다(monkeypatch, capsys):
    """도구가 **실패했는데 0으로 끝나면** 부르는 쪽이 성공으로 읽는다.

    `tools/redraw_maps.py`는 회차마다 예외를 잡아 "건너뜁니다"만 찍고 넘어간
    뒤, 마지막에 늘 0을 돌려주었다. 12쌍이 전부 깨져도 "지도 0장을 다시
    그렸습니다."에 `$?`=0이라, 화면을 읽는 사람에게만 경고가 보이고
    CI·래퍼·`&&` 사슬에는 성공으로 보였다.
    """
    import tools.redraw_maps as redraw_maps

    if not redraw_maps.available():
        pytest.skip("다시 그릴 산출물이 없다")

    # 자식이 전부 실패하는 상황
    def 늘_실패(*args, **kwargs):
        raise RuntimeError("흉내 낸 실패")

    monkeypatch.setattr(redraw_maps, "redraw", 늘_실패)
    monkeypatch.setattr(sys, "argv", ["redraw_maps.py", "--all"])

    code = redraw_maps.main()
    out = capsys.readouterr().out

    assert code != 0, "전부 실패했는데 성공(0)으로 끝난다"
    assert "실패했습니다" in out, "무엇이 실패했는지 안 적는다"


def test_지도_다시_그리기_dry_run은_성공이다(monkeypatch):
    """세어만 보는 것은 실패가 아니다 — 위 시험이 0을 무조건 막지 않는지 함께 본다."""
    import tools.redraw_maps as redraw_maps

    if not redraw_maps.available():
        pytest.skip("다시 그릴 산출물이 없다")

    monkeypatch.setattr(sys, "argv", ["redraw_maps.py", "--all", "--dry-run"])
    assert redraw_maps.main() == 0


# ───────── step 모듈을 패키지로 import할 수 있는가 (1.26.154) ─────────

STEP_MODULES = [
    "step0_collect.calculate_target_qty",
    "step0_collect.raw_to_net",
    "step1_cluster.top_st_clustering",
    "step2_optimize.ilp",
    "step2_optimize.vrp",
    "step4_metrics.imbalance",
]


@pytest.mark.parametrize("module", STEP_MODULES)
def test_step_모듈을_폴더_이름으로_import할_수_있다(module):
    """`from step2_optimize import vrp`가 되는가 (1.26.154).

    실험 **48개**가 `sys.path`에 step 폴더를 밀어 넣는 여섯 줄을 각자 이고
    있다. 이유는 단 둘이었다 — `vrp.py`가 `from ilp import ...`, 그리고
    `top_st_clustering.py`가 `from adjust_module import ...`로 **형제 모듈을
    맨 이름으로** 부른다. 그러면 그 폴더가 `sys.path`에 있을 때만 import된다.

    step 폴더에는 `__init__.py`가 없지만 파이썬 3.3+의 네임스페이스 패키지라
    **폴더 이름으로 부르는 것 자체는 원래 됐다** — 위 두 줄만 막고 있었다.

    ⚠️ **직접 실행도 계속 돼야 한다.** `run_pipeline.py`는 이 파일들을
    `python step2_optimize/vrp.py`로 띄운다(스크립트 경로). 그때는 폴더가
    `sys.path[0]`이라 맨 이름 import가 맞고, 패키지 경로는 없다. 그래서
    둘 다 되게 두고 어느 쪽이 실패하든 다른 쪽으로 넘어가게 했다.
    """
    import importlib

    mod = importlib.import_module(module)
    assert mod is not None


def test_step_모듈은_직접_실행도_된다():
    """파이프라인이 부르는 방식(스크립트 경로)이 안 깨졌는지 본다.

    위 테스트를 통과시키려고 맨 이름 import를 지우면 이쪽이 깨진다 —
    `run_pipeline.py`가 쓰는 것은 이 경로다. 둘은 함께 지켜야 한다.
    """
    for script in ("step2_optimize/vrp.py", "step1_cluster/top_st_clustering.py"):
        # run_pipeline.py와 **같은 형태**로 부른다: `python <경로>`. 이때
        # 파이썬이 그 폴더를 sys.path[0]에 놓으므로 맨 이름 import가 성립한다.
        # `--help`로 세워 둔다 — 실제 계산까지 돌리면 자료가 있어야 한다.
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / script), "--help"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(PROJECT_ROOT), timeout=180,
        )
        assert result.returncode == 0, (
            f"{script}를 스크립트로 띄울 수 없다 — 파이프라인이 이 경로를 쓴다:\n"
            f"{(result.stderr or '')[-600:]}")
