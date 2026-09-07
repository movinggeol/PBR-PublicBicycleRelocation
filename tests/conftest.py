"""pytest 공통 설정."""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def isolate_db(tmp_path, monkeypatch):
    """모든 테스트가 임시 DB를 쓰도록 강제한다.

    웹 API가 DB를 조회하게 되면서(DB_PLAN 3단계), 라우트를 한 번 부르기만 해도
    실제 data/bike_system.db가 생성된다. 테스트는 사용자 데이터를 건드리면 안 되므로
    기본값을 임시 경로로 덮어쓴다. 개별 테스트가 다른 경로를 원하면 다시 setenv 하면 된다.
    """
    monkeypatch.setenv("PBR_DB_PATH", str(tmp_path / "isolated.db"))


@pytest.fixture(autouse=True)
def isolate_job_registry(tmp_path, monkeypatch):
    """실행 이력(`runs.json`)과 로그도 임시 경로로 격리한다 (1.26.149).

    🔴 **DB만 격리하고 이 파일을 빼 두었더니 실제로 샜다.** `start_job()`은
    감시 스레드(`_watch`)를 띄우는데 그 스레드는 **테스트보다 오래 산다** —
    monkeypatch가 `_save_registry`를 원복한 뒤에 스레드가 깨어나 *진짜*
    `_save_registry()`를 부르고, 사용자의 `data/webapp/runs.json`에 가짜 작업이
    박힌다. 실측(2026-09-07): 기록 15건 중 **11건이 이 경로로 들어온 가짜**였다
    (인자 없음·로그 없음·0초).

    그 11건은 화면까지 망가뜨렸다 — `typical_elapsed()`가 0초를 돌려주고,
    템플릿의 `{% if typical_minutes %}`에서 **0은 거짓**이라 안내가 말없이
    하드코딩 문구로 되돌아갔다(`test_예상_소요는_0분이어도_숨지_않는다` 참고).

    `_jobs`도 비운다 — 모듈을 import하는 순간 `_load_registry()`가 실제 파일을
    이미 읽어 두기 때문이다. 끝나면 원래 내용을 되돌려, 이 픽스처 자체가
    이력을 지우지 않게 한다.
    """
    try:
        from webapp import jobs
    except ImportError:      # webapp을 안 쓰는 테스트(의존성 없는 환경 포함)
        return

    registry = tmp_path / "webapp"
    monkeypatch.setattr(jobs, "WEBAPP_DATA", registry)
    monkeypatch.setattr(jobs, "LOG_DIR", registry / "logs")
    monkeypatch.setattr(jobs, "REGISTRY_FILE", registry / "runs.json")

    original = dict(jobs._jobs)
    jobs._jobs.clear()
    try:
        yield
    finally:
        jobs._jobs.clear()
        jobs._jobs.update(original)
