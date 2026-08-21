"""실행 로그에서 진행 단계를 뽑아내는 부분에 대한 테스트.

run_pipeline이 로그 맨 앞에 전체 단계 목록을 찍고, 단계마다
'실행:' → '완료:'/'실패:'를 찍는다는 규약에 기대고 있다.
그 규약이 깨지면 여기서 먼저 걸린다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from webapp.app import pipeline_progress

# 실제 로그에는 윈도우 경로가 들어가므로 역슬래시를 그대로 둔다.
PLAN = "\n".join([
    "=== Public Bike Rebalancing Pipeline ===",
    "요일 구분: 평일 (계획 대상일 2026-08-18 기준)",
    r"[1/3] step0_collect\raw_to_net.py",
    r"[2/3] step2_optimize\ilp.py",
    r"[3/3] step4_metrics\imbalance.py",
])


def test_lists_stages_before_anything_runs():
    """계획 블록만 있으면 전부 '대기'다."""
    steps = pipeline_progress(PLAN)

    assert [s["name"] for s in steps] == ["raw_to_net.py", "ilp.py", "imbalance.py"]
    assert {s["status"] for s in steps} == {"pending"}


def test_shows_human_readable_stage_names():
    """화면에는 파일 이름이 아니라 무엇을 하는 단계인지가 나와야 한다."""
    labels = [s["label"] for s in pipeline_progress(PLAN)]

    assert labels == ["순수요 계산", "이동량 최적화 (ILP)", "성과 지표 계산"]


def test_marks_running_and_done():
    log = "\n".join([
        PLAN,
        "[1/3] 실행: python raw_to_net.py",
        r"완료: step0_collect\raw_to_net.py",
        "[2/3] 실행: python ilp.py",
    ])

    assert [s["status"] for s in pipeline_progress(log)] == ["done", "running", "pending"]


def test_marks_failure():
    log = "\n".join([
        PLAN,
        "[1/3] 실행: python raw_to_net.py",
        r"완료: step0_collect\raw_to_net.py",
        "[2/3] 실행: python ilp.py",
        r"실패: step2_optimize\ilp.py (exit code=1)",
    ])

    assert [s["status"] for s in pipeline_progress(log)] == ["done", "failed", "pending"]


def test_marks_missing_file():
    log = "\n".join([PLAN, r"파일이 없습니다: step2_optimize\ilp.py"])

    assert pipeline_progress(log)[1]["status"] == "missing"


def test_plan_block_is_read_only_once():
    """계획 블록이 두 번 찍혀도 단계가 늘어나선 안 된다."""
    assert len(pipeline_progress(PLAN + "\n" + PLAN)) == 3


def test_is_empty_when_log_has_no_plan():
    """로그 형식이 바뀌면 조용히 빈 목록을 준다 — 화면은 로그만 보여주면 된다."""
    assert pipeline_progress("아무 내용이나") == []
    assert pipeline_progress("") == []


def _stage_names(*flags):
    """주어진 옵션으로 run_pipeline이 고르는 단계 파일 이름 목록."""
    import run_pipeline

    argv = sys.argv
    try:
        sys.argv = ["run_pipeline.py", *flags]
        args = run_pipeline.parse_args()
    finally:
        sys.argv = argv
    return [path.name for path in run_pipeline.selected_scripts(args)]


def test_skip_map_drops_only_step3():
    """--skip-map은 TMAP 지도(step3)만 빼고 성과 지표(step4)는 남겨야 한다.

    step3는 TMAP 실도로 경로를 받아 오므로 API 키가 필요하다. 키가 없는 환경
    (합성 데이터 시연·CI)에서 나머지를 마저 돌리려고 만든 옵션이므로, step4까지
    함께 사라지면 쓸모가 없다.
    """
    full = _stage_names("--skip-api", "--skip-eda")
    without_map = _stage_names("--skip-api", "--skip-eda", "--skip-map")

    assert "main.py" in full, "기본 실행에는 step3 지도가 들어 있어야 한다"
    assert "main.py" not in without_map
    assert "imbalance.py" in without_map, "지표 단계까지 사라지면 안 된다"
    assert set(full) - set(without_map) == {"main.py"}
