"""재현 절차가 **정말 합성 데이터로 도는지** 지킨다.

이 파일이 있는 이유는 실패가 조용했기 때문이다. README에 오래 실려 있던 절차

    python tools/make_sample_data.py --now "데모"
    python run_pipeline.py --skip-api --skip-eda --skip-map --now "데모"

는 합성 대여소 90곳을 만들어 놓고 **실데이터 1,361곳을 돌렸다**(2026-08-31 확인).
`--skip-api`가 직전 실행의 스냅샷을 물려받기 때문인데, 오류가 나지 않으니
실데이터를 가진 PC에서는 아무도 눈치챌 수 없었다. 깨끗한 복제본에서는 기본
원천 CSV(저장소에 없는 106MB 실파일)를 찾다가 죽었다.

지키려는 규칙: **재현 절차는 문서의 글이 아니라 실행되는 코드여야 하고,
자기가 무엇을 봤는지 스스로 확인해야 한다.**
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_reproduce():
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    spec = importlib.util.spec_from_file_location(
        "_reproduce", PROJECT_ROOT / "tools" / "reproduce.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def repro():
    return load_reproduce()


def test_uses_skip_fetch_not_skip_api(repro):
    """`--skip-api`로 되돌리면 실데이터 스냅샷을 물려받아 재현이 깨진다."""
    import inspect

    source = inspect.getsource(repro.run_pipeline)
    assert '"--skip-fetch"' in source
    assert '"--skip-api"' not in source, (
        "--skip-api는 직전 실행의 재고 스냅샷을 물려받는다 — 합성 데이터가 무시된다")


def test_passes_period_and_raw_file(repro):
    """둘 다 넘기지 않으면 저장소에 없는 실데이터 기본값을 찾아간다."""
    import inspect

    source = inspect.getsource(repro.run_pipeline)
    assert '"--period"' in source and '"--raw-file"' in source


def test_verify_rejects_a_station_count_mismatch(repro, monkeypatch, tmp_path):
    """대여소 수가 어긋나면 **반드시 멈춰야 한다** — 조용히 넘어가면 의미가 없다."""
    import pandas as pd

    fake = tmp_path / "st_info.csv"
    pd.DataFrame({"station_id": [f"ST{i:04d}" for i in range(1361)]}).to_csv(
        fake, index=False, encoding="utf-8-sig")
    monkeypatch.setattr(repro, "INFO_CSV", fake)

    with pytest.raises(SystemExit) as err:
        repro.verify(90)
    assert "90" in str(err.value) and "1361" in str(err.value)


def test_verify_accepts_a_matching_count(repro, monkeypatch, tmp_path):
    import pandas as pd

    fake = tmp_path / "st_info.csv"
    pd.DataFrame({"station_id": [f"ST{i:04d}" for i in range(90)]}).to_csv(
        fake, index=False, encoding="utf-8-sig")
    monkeypatch.setattr(repro, "INFO_CSV", fake)
    repro.verify(90)      # 예외가 없으면 통과


def test_skip_fetch_keeps_the_offline_collect_stages():
    """`--skip-fetch`는 라이브 호출만 뺀다 — 재고 CSV를 읽는 두 단계는 남아야 한다.

    남지 않으면 합성 재고가 `st_info`로 옮겨지지 못하고, 뒤 단계가 실데이터
    산출물을 집는다.
    """
    import run_pipeline

    argv = sys.argv
    try:
        sys.argv = ["run_pipeline.py", "--skip-fetch", "--skip-eda", "--skip-map"]
        args = run_pipeline.parse_args()
    finally:
        sys.argv = argv

    names = [p.name for p in run_pipeline.selected_scripts(args)]
    assert "tashu_api.py" not in names, "라이브 API 호출은 빠져야 한다"
    assert "extract_parking_lot.py" in names
    assert "api_to_info.py" in names


def test_default_db_is_not_the_real_one(repro):
    """재현이 사용자의 실 DB를 건드리면 안 된다."""
    import db as db_mod

    assert repro.DEFAULT_DB != Path(db_mod.DB_PATH)


@pytest.mark.slow
def test_end_to_end(tmp_path):
    """실제로 한 번 돌려 본다 — 이 테스트가 곧 README의 재현 절차다."""
    completed = subprocess.run(
        [sys.executable, "tools/reproduce.py",
         "--duration", "_05_10", "--stations", "60", "--days", "14",
         "--db", str(tmp_path / "repro.db")],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        env={**__import__("os").environ, "PYTHONUTF8": "1",
             "PYTHONIOENCODING": "utf-8"},
    )
    assert completed.returncode == 0, (
        f"재현이 실패했습니다\n--- stdout ---\n{completed.stdout[-3000:]}\n"
        f"--- stderr ---\n{completed.stderr[-2000:]}")
    assert "합성 대여소 60곳" in completed.stdout
