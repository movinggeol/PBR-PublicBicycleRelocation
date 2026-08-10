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
