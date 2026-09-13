"""파이프라인 스모크 테스트 (step0 → step1 → step2 → step4).

합성 데이터로 전 단계를 실제 실행해 산출물의 존재와 스키마를 확인한다.
API 키가 필요한 step0/tashu_api.py와 step3/main.py(TMAP)는 제외한다.
(모든 step 폴더는 1.26.168부터 `pipeline/` 아래에 있다.)

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
    Path("pipeline/step0_collect") / "extract_parking_lot.py",
    Path("pipeline/step0_collect") / "api_to_info.py",
    Path("pipeline/step0_collect") / "raw_to_net.py",
    Path("pipeline/step0_collect") / "calculate_target_qty.py",
    Path("pipeline/step1_cluster") / "top_st_clustering.py",
    Path("pipeline/step1_cluster") / "st_visualization.py",
    Path("pipeline/step2_optimize") / "ilp.py",
    Path("pipeline/step2_optimize") / "vrp.py",
    Path("pipeline/step4_metrics") / "imbalance.py",
]


# 🔴 **산출물 뿌리는 `pipeline_run`이 임시 경로로 갈아끼운다** (1.26.188).
# 그전에는 이 모듈의 시험이 **진짜 `data/pp_data`에 산출물을 쌓았다** — 실패하면
# 그대로 남아 실제로 잔여물 63개를 치운 적이 있다(1.26.124). 기본값을 진짜
# 경로로 두는 것은 픽스처 없이 `_out()`을 부르는 실수가 있으면 드러나게 하려는
# 것이다(그런 호출자는 지금 없다).
_PP_ROOT: Path = PP_ROOT


def _out(relative: str) -> Path:
    return _PP_ROOT / relative.format(label=LABEL, duration=DURATION)


@pytest.fixture(scope="module")
def smoke_db(tmp_path_factory):
    """이중 기록이 실제 DB(data/bike_system.db)를 오염시키지 않도록 별도 파일을 쓴다."""
    return tmp_path_factory.mktemp("db") / "smoke.db"


@pytest.fixture(scope="module")
def pipeline_run(tmp_path_factory, smoke_db):
    """합성 데이터를 만들고 파이프라인을 한 번 실행한다.

    🔴 **산출물은 진짜 `data/`가 아니라 임시 경로에 쌓인다** (1.26.188).
    예전에는 `PBR_DB_PATH`만 격리하고 `PBR_DATA_ROOT`는 두지 않아 이 시험이
    사용자의 `data/pp_data`에 파일을 만들었고, 실패하면 그대로 남았다.

    ⚠️ **그때 댄 이유는 이제 낡았다** — *"스텝 스크립트는 경로를 `DATA_ROOT`로
    직접 조립해서 임시 경로로 돌릴 수도 없다."* 1.26.141이 44곳을 `DATA_ROOT`
    기준으로 바꿨고, `test_CSV를_전부_지워도_DB만으로_다시_돈다`가
    `PBR_DATA_ROOT`만으로 파이프라인을 완주시켜 **실증했다**(1.26.185).
    """
    global _PP_ROOT
    raw_path = tmp_path_factory.mktemp("raw") / "합성_대여이력.csv"
    data_root = tmp_path_factory.mktemp("data")
    _PP_ROOT = data_root / "pp_data"

    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               PBR_DATA_ROOT=str(data_root), PBR_DB_PATH=str(smoke_db))

    # 합성 데이터도 하위 프로세스로 만든다 — `generate()`를 여기서 부르면
    # import 시점에 굳은 `DATA_ROOT`(=진짜 `data/`)에 대여소 파일이 쓰인다.
    지음 = subprocess.run(
        [sys.executable, "tools/make_sample_data.py",
         "--now", LABEL, "--period", LABEL, "--stations", "70",
         "--days", "12", "--rentals-per-day", "500", "--raw-file", str(raw_path)],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    if 지음.returncode != 0:
        pytest.fail("합성 데이터 생성 실패:" + 지음.stdout + 지음.stderr)

    results = []

    # 예전에는 여기서 `try/finally`로 **진짜 `data/`에 남은 잔여물**을 치웠다.
    # 이제 산출물이 임시 경로에만 쌓이므로 치울 것이 없다 — pytest가 tmp를
    # 알아서 걷는다. **치우는 것보다 애초에 안 만드는 것이 낫다.**
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


def test_csv_to_db_tool_imports_outputs(pipeline_run, tmp_path, monkeypatch):
    """CSV → DB 적재 도구도 같은 결과를 낸다(이관 검증·복구 경로).

    파이프라인이 방금 만든 CSV를 적재하므로 컬럼이 하나라도 어긋나면 여기서 잡힌다.
    """
    import db
    import tools.csv_to_db as csv_to_db
    from tools.csv_to_db import import_outputs

    # 이 도구는 자기 모듈의 `PP_ROOT`(=진짜 `data/`)를 본다. 픽스처가 산출물을
    # 임시 경로로 옮겼으므로(1.26.188) 그쪽을 보게 한다.
    monkeypatch.setattr(csv_to_db, "PP_ROOT", _PP_ROOT)

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
    "pipeline.step0_collect.calculate_target_qty",
    "pipeline.step0_collect.raw_to_net",
    "pipeline.step1_cluster.top_st_clustering",
    "pipeline.step2_optimize.ilp",
    "pipeline.step2_optimize.vrp",
    "pipeline.step4_metrics.imbalance",
]


@pytest.mark.parametrize("module", STEP_MODULES)
def test_step_모듈을_폴더_이름으로_import할_수_있다(module):
    """`from pipeline.step2_optimize import vrp`가 되는가 (1.26.154, 1.26.168).

    실험 **48개**가 `sys.path`에 step 폴더를 밀어 넣는 여섯 줄을 각자 이고
    있다. 이유는 단 둘이었다 — `vrp.py`가 `from ilp import ...`, 그리고
    `top_st_clustering.py`가 `from adjust_module import ...`로 **형제 모듈을
    맨 이름으로** 부른다. 그러면 그 폴더가 `sys.path`에 있을 때만 import된다.

    step 폴더에는 `__init__.py`가 없지만 파이썬 3.3+의 네임스페이스 패키지라
    **폴더 이름으로 부르는 것 자체는 원래 됐다** — 위 두 줄만 막고 있었다.
    1.26.168에서 step 폴더들을 `pipeline/` 아래로 모으면서 패키지 경로에
    `pipeline.` 접두어가 붙었다.

    ⚠️ **직접 실행도 계속 돼야 한다.** `run_pipeline.py`는 이 파일들을
    `python pipeline/step2_optimize/vrp.py`로 띄운다(스크립트 경로). 그때는
    폴더가 `sys.path[0]`이라 맨 이름 import가 맞고, 패키지 경로는 없다. 그래서
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
    for script in ("pipeline/step2_optimize/vrp.py", "pipeline/step1_cluster/top_st_clustering.py"):
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


# ---- 단계 간 배선이 DB로 옮겨졌다 (1.26.166) ----

def test_앞_단계를_DB에서_읽고_없으면_CSV로_물러선다(tmp_path, monkeypatch):
    """파이프라인이 **자기가 만든 것을 자기가 되읽는** 자리의 규약.

    🔴 CSV는 산출물이면서 **단계 간 배선**이기도 했다 — 그래서 이중 기록을
    걷으려 하면 파이프라인이 먼저 끊겼다(1.26.163). 이제 `db.read_step_output()`
    하나를 거친다. **두 방향을 모두 지킨다**:

      - DB에 있으면 CSV가 없어도 읽는다 (배선이 DB로 옮겨졌다)
      - DB가 비면 CSV로 물러선다 (원천만 있는 환경도 돌아야 한다)
    """
    import db
    import pandas as pd

    monkeypatch.setenv("PBR_DB_PATH", str(tmp_path / "wire.db"))

    표 = pd.DataFrame({"station_id": ["ST1", "ST2"], "lat": [36.3, 36.4],
                       "lon": [127.3, 127.4], "rebal_qty": [5, -5],
                       "cluster": [0, 0]})

    # ① DB에만 있는 경우 — 없는 CSV 경로를 줘도 읽힌다
    with db.session() as conn:
        db.save_frame(conn, "pick_drop", 표, run_label="배선", duration="_05_10")
    frame, source = db.read_step_output("pick_drop", tmp_path / "없는파일.csv",
                                        run_label="배선", duration="_05_10")
    assert source == "db" and len(frame) == 2

    # ② DB에 없는 경우 — CSV로 물러선다
    csv = tmp_path / "폴백.csv"
    표.to_csv(csv, index=False, encoding="utf-8")
    frame, source = db.read_step_output("pick_drop", csv,
                                        run_label="없는라벨", duration="_05_10")
    assert source == "csv" and len(frame) == 2

    # ③ 둘 다 없으면 빈 프레임 — 부르는 쪽이 '건너뜀'을 정한다(예외 아님)
    frame, source = db.read_step_output("pick_drop", tmp_path / "없다.csv",
                                        run_label="없는라벨", duration="_05_10")
    assert source == "none" and frame.empty

    # ④ 🔴 **헤더조차 없는 파일도 '비었다'이지 크래시가 아니다** (1.26.185).
    #    이 함수의 docstring이 *"여기서 예외를 던지면 한쪽 후보만 있는 시간대가
    #    크래시가 된다"* 고 약속해 놓고, `read_csv`가 EmptyDataError를 던졌다.
    #    실제로 ilp.py가 그런 파일을 만들어 vrp가 죽었다 — vrp의
    #    `if ilp_plan.empty: 건너뜀` 가드는 **도달조차 못 했다.**
    빈파일 = tmp_path / "헤더없음.csv"
    빈파일.write_text("\n", encoding="utf-8")
    frame, source = db.read_step_output("pick_drop", 빈파일,
                                        run_label="없는라벨", duration="_05_10")
    assert frame.empty, "헤더 없는 파일에서 예외가 나면 회차 하나가 크래시가 된다"


# ---- 빈 ILP 계획은 크래시가 아니라 건너뜀이다 (1.26.185) ----

def test_빈_ILP_계획도_헤더를_갖춘_표로_저장된다(tmp_path, monkeypatch):
    """🔴 **빈 계획은 정상이다** — 한쪽 후보만 있는 시간대에서 늘 생긴다.
    파이프라인 규약도 *"크래시가 아니라 건너뛴다"* 고 못박고 있다.

    그런데 `pd.DataFrame([])`를 그냥 저장하면 **헤더조차 없는 2바이트 파일**이
    나오고, 되읽는 쪽이 `EmptyDataError`로 죽었다. 2026-09-13에 실측했다 —
    `ILP_plan_05_10 (dbonly).csv`가 2바이트였고 vrp가 거기서 멈췄다.

    여기서 지키는 것은 **헤더가 있다**는 것 하나다. 그래야 vrp의 건너뛰기
    가드까지 **도달한다.**
    """
    import importlib
    import pandas as pd

    monkeypatch.setenv("PBR_DB_PATH", str(tmp_path / "빈계획.db"))
    monkeypatch.setenv("PBR_DATA_ROOT", str(tmp_path / "data"))
    ilp = importlib.import_module("pipeline.step2_optimize.ilp")

    저장 = tmp_path / "빈계획.csv"
    monkeypatch.setattr(ilp, "ilp_plan_path", str(저장).replace("{", "{{"))

    # 후보가 한쪽뿐이라 옮길 것이 없는 입력 — rebal_qty가 전부 음수라
    # pick만 있고 drop이 없다(`solve_cluster_moves`가 바로 []를 돌려준다).
    metrics = pd.DataFrame({
        "station_id": ["ST0001", "ST0002"], "cluster": [0, 0],
        "rebal_qty": [-5, -3],
        "lat": [36.35, 36.36], "lon": [127.38, 127.39],
    })
    ilp.run_ilp_plan(metrics, "_05_10", ilp.build_solver())

    assert 저장.exists(), "빈 계획이어도 파일은 남아야 한다"
    내용 = 저장.read_text(encoding="utf-8")
    assert 내용.strip(), "2바이트 빈 파일을 만들면 되읽는 쪽이 죽는다"

    다시 = pd.read_csv(저장)          # EmptyDataError가 나면 여기서 터진다
    assert 다시.empty
    assert list(다시.columns) == ilp.ILP_PLAN_COLUMNS


# ---- CSV를 다 지워도 DB만으로 돈다 (1.26.167) ----

def test_CSV를_전부_지워도_DB만으로_다시_돈다(tmp_path):
    """🔴 **이 저장소의 배선이 정말 DB로 옮겨졌는지 재는 시험이다.**

    한 번 완주시켜 DB와 CSV를 모두 만든 뒤 **산출물 CSV를 전부 지우고** 다시
    돌린다. 배선이 파일에 남아 있으면 그 단계가 *"입력이 없습니다"* 로 조용히
    건너뛰므로, 건너뛴 줄이 하나도 없어야 한다.

    실제로 이 시험이 두 곳을 찾아냈다(1.26.167): `step3_map/main.py`가 파일만
    보고 있었고, `run_pipeline.inherit_snapshot()`이 DB를 안 봐서 **단계가
    시작되기도 전에** 멈췄다. 그전까지는 단계별 읽기 함수만 확인했을 뿐,
    CSV를 실제로 지우고 완주시켜 본 적이 없었다.

    원천(합성 대여이력)은 남긴다 — 그건 입력이지 단계 간 배선이 아니다.

    🔴 **1차 실행도 `--skip-fetch`로 돈다**(1.26.185). 예전에는 이 줄이 없어
    **라이브 타슈 API를 불렀다** — 그래서 (1) `TASHU_API_KEY`가 없는 CI에서는
    아예 못 돌고, (2) 합성 대여소 40곳과 **실재 대여소 1,374곳**이 섞여
    계획이 설 때도 안 설 때도 있었다. 2026-09-13에 같은 날 한 번은 통과하고
    한 번은 실패하는 것을 보고 찾았다. 배선을 재는 시험이 **바깥 세계의
    지금 재고**에 기대고 있었던 것이다.
    """
    raw = tmp_path / "합성.csv"
    data_root = tmp_path / "data"
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               PBR_DATA_ROOT=str(data_root), PBR_DB_PATH=str(tmp_path / "d.db"))

    # 🔴 **합성 데이터도 하위 프로세스로 만든다.** `generate()`를 여기서 직접
    #    부르면 이 프로세스가 import할 때 굳은 `DATA_ROOT`(=진짜 `data/`)에
    #    대여소 파일을 쓴다 — 아래 실행은 tmp를 보므로 **못 찾고**, 덤으로
    #    사용자의 `data/`를 더럽힌다. 원천 CSV만 tmp로 간 탓에 겉보기엔
    #    격리된 듯 보였다(1.26.185).
    지음 = subprocess.run(
        [sys.executable, "tools/make_sample_data.py",
         "--now", "dbonly", "--period", "dbonly", "--stations", "40",
         "--days", "8", "--rentals-per-day", "300", "--raw-file", str(raw)],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    assert 지음.returncode == 0, (
        "합성 데이터 생성 실패:" + 지음.stdout + 지음.stderr)
    공통 = ["--now", "dbonly", "--period", "dbonly",
           "--duration", DURATION, "--raw-file", str(raw)]

    def 돌린다(*추가):
        return subprocess.run(
            [sys.executable, "run_pipeline.py",
             "--skip-eda", "--skip-map", "--skip-fetch", *공통, *추가],
            cwd=PROJECT_ROOT, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace")

    첫판 = 돌린다()
    assert 첫판.returncode == 0, f"1차 실행 실패:\n{첫판.stdout[-1500:]}"

    산출물 = sorted((data_root / "pp_data").rglob("*.csv"))
    assert 산출물, "1차 실행이 CSV를 하나도 안 만들었다 — 시험이 성립하지 않는다"
    for path in 산출물:
        path.unlink()

    둘째판 = 돌린다("--skip-api")
    assert 둘째판.returncode == 0, f"CSV 없이 재실행 실패:\n{둘째판.stdout[-1500:]}"

    출력 = 둘째판.stdout + 둘째판.stderr
    assert "물려받을 재고 스냅샷이 없습니다" not in 출력, "스냅샷 가드가 DB를 못 봤다"
    건너뜀 = [줄 for 줄 in 출력.splitlines() if "입력이 없습니다" in 줄]
    assert not 건너뜀, f"아직 파일에 매인 단계가 있다:\n" + "\n".join(건너뜀)


# ---- 빈 산출물은 크래시가 아니다 — 같은 부류 쓸기 (1.26.188) ----

def test_빈_경로도_헤더를_갖춘_표로_저장된다(tmp_path, monkeypatch):
    """ilp와 **같은 결함이 vrp에도 있었다**(1.26.188).

    `results`가 비면 `pd.DataFrame([])`라 컬럼이 통째로 사라진다. 빈 경로는
    실제로 나올 수 있다 — **시간 예산이 전부를 잘라 내면** `greedy_route()`가
    첫 방문에서 멈춰 한 행도 못 만든다(`budget_enforce.py`가 그 실험이다).
    여기서는 그 상황을 `greedy_route`를 비우는 것으로 만든다.
    """
    import importlib
    import pandas as pd

    monkeypatch.setenv("PBR_DB_PATH", str(tmp_path / "빈경로.db"))
    monkeypatch.setenv("PBR_DATA_ROOT", str(tmp_path / "data"))
    vrp = importlib.import_module("pipeline.step2_optimize.vrp")

    저장 = tmp_path / "빈경로.csv"
    monkeypatch.setattr(vrp, "vrp_plan_file", str(저장).replace("{", "{{"))
    monkeypatch.setattr(vrp, "greedy_route", lambda *a, **k: [])   # 예산이 다 잘랐다
    monkeypatch.setattr(vrp.db, "read_step_output", lambda *a, **k: (pd.DataFrame({
        "station_id": ["ST0001", "ST0002"],
        "lat": [36.35, 36.36], "lon": [127.38, 127.39],
    }), "db"))

    ilp_plan = pd.DataFrame({
        "cluster": [0], "pick_station_id": ["ST0001"],
        "drop_station_id": ["ST0002"], "qty": [3], "travel_time_sec": [60.0],
    })
    vrp.run_vrp_plan(ilp_plan, "_05_10")

    다시 = pd.read_csv(저장)          # EmptyDataError가 나면 여기서 터진다
    assert 다시.empty
    for 컬럼 in ("cluster", "to_id", "action", "qty"):
        assert 컬럼 in 다시.columns, f"빈 경로에서 '{컬럼}'이 사라지면 step4가 KeyError를 낸다"


def test_step4도_헤더_없는_파일을_비었다로_읽는다(tmp_path, monkeypatch):
    """🔴 `db.read_step_output()`은 1.26.185에서 고쳤는데 **판박이인 step4의
    `load_step_output()`만 남아 있었다**(1.26.188).

    두 함수의 docstring이 **같은 약속**을 적고 있다 — *"여기서 예외를 던지면
    한쪽 후보만 있는 시간대가 크래시가 된다."* 한쪽만 고치면 그 약속은 반만
    지켜진다.
    """
    import importlib
    import pandas as pd

    monkeypatch.setenv("PBR_DB_PATH", str(tmp_path / "step4.db"))
    monkeypatch.setenv("PBR_DATA_ROOT", str(tmp_path / "data"))
    imbalance = importlib.import_module("pipeline.step4_metrics.imbalance")

    빈파일 = tmp_path / "헤더없음.csv"
    빈파일.write_bytes(b"")

    frame = imbalance.load_step_output("vrp_plan", str(빈파일),
                                       duration="_05_10", run_label="없는라벨")
    assert isinstance(frame, pd.DataFrame) and frame.empty
