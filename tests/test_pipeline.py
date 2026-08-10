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
    Path("step0 (raw데이터 처리)") / "extract_parking_lot.py",
    Path("step0 (raw데이터 처리)") / "api_to_info.py",
    Path("step0 (raw데이터 처리)") / "raw_to_net.py",
    Path("step0 (raw데이터 처리)") / "calculate_target_qty.py",
    Path("step1 (작업대상 선정 및 클러스터링)") / "1.top_st_clustering.py",
    Path("step1 (작업대상 선정 및 클러스터링)") / "st_visualization.py",
    Path("step2 (ilp, vrp)") / "ilp.py",
    Path("step2 (ilp, vrp)") / "vrp.py",
    Path("step4 (성과 지표)") / "imbalance.py",
]


def _out(relative: str) -> Path:
    return PP_ROOT / relative.format(label=LABEL, duration=DURATION)


@pytest.fixture(scope="module")
def pipeline_run(tmp_path_factory):
    """합성 데이터를 만들고 파이프라인을 한 번 실행한다."""
    raw_path = tmp_path_factory.mktemp("raw") / "합성_대여이력.csv"
    generate(now=LABEL, period=LABEL, stations=70, days=12,
             rentals_per_day=500, raw_path=raw_path)

    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
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


def test_outputs_load_into_database(pipeline_run, tmp_path):
    """실제 산출물이 DB 스키마에 그대로 들어간다 (DB_PLAN 1단계 검증).

    합성 데이터가 아니라 파이프라인이 방금 만든 CSV를 적재하므로,
    컬럼이 하나라도 어긋나면 여기서 잡힌다.
    """
    import db
    from tools.csv_to_db import import_outputs

    with db.connect(tmp_path / "smoke.db") as conn:
        db.init_schema(conn)
        loaded = import_outputs(conn, now=LABEL, period=LABEL, durations=[DURATION])

        # CSV로 확인한 산출물이 모두 적재되어야 한다.
        assert {"station_info", "parking_lot", "net_demand",
                f"rebalance_plan{DURATION}", f"pick_drop{DURATION}",
                f"ilp_plan{DURATION}", f"vrp_plan{DURATION}",
                f"metrics{DURATION}", f"route_summary{DURATION}"} <= set(loaded)
        assert all(rows > 0 for rows in loaded.values())

        # 행 수가 원본 CSV와 일치한다.
        csv_rows = len(pd.read_csv(_out("ILP/후보/top{duration} ({label}).csv"), encoding="utf-8"))
        stored = db.load_frame(conn, "pick_drop", run_label=LABEL, duration=DURATION)
        assert len(stored) == csv_rows
        assert set(stored["run_label"]) == {LABEL}

        # 라벨을 생략해도 최신 실행분을 찾을 수 있다(웹 API가 쓸 경로).
        assert not db.load_frame(conn, "metrics").empty
        assert db.list_runs(conn)["run_label"].tolist() == [LABEL]
