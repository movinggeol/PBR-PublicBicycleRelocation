"""테스트 DB 격리가 픽스처 **스코프와 무관하게** 듣는지 지킨다 (1.26.189).

🔴 **함수 스코프 autouse만으로는 모듈 스코프 픽스처를 못 막았다.** pytest는
넓은 스코프의 픽스처를 먼저 세운다 — 모듈 픽스처가 도는 순간에는 함수 스코프
`isolate_db`가 아직 `PBR_DB_PATH`를 바꾸기 전이다. 그래서 `test_day_type.py`의
`prepared`와 `test_rentals.py`의 `sample`이 이 프로세스에서 부른
`make_sample_data.generate()`가 **사용자의 `data/bike_system.db`에 썼다**
(실행 `daytype-*`·`rentaltest-*`와 그 `station_stock` 행, 2026-09-14 발견).

재현은 실제 DB를 건드리지 않고 했다 — 바깥 환경의 `PBR_DB_PATH`를 표지 DB로
두고 두 파일을 돌리니 **표지 DB에 두 라벨이 찍혔다**(실행 2건, `station_stock`
70행·40행). 함수 스코프 격리 안에서 썼다면 테스트마다의 임시 DB로 갔어야 할
행이다.

이 파일의 모듈 스코프 픽스처는 **아무것도 쓰지 않고 경로만 본다** — 격리가
빠지면 실제 DB 경로를 보고 실패하므로, 쓰기 없이 누수를 잡는다.
"""
import pytest

import db


@pytest.fixture(scope="module")
def module_scope_db_path():
    """모듈 스코프 픽스처가 세워지는 순간의 DB 경로 — `generate()`가 쓰던 자리."""
    return db.active_db_path()


def test_모듈_스코프_픽스처도_세션_임시_DB를_본다(module_scope_db_path, tmp_path_factory):
    """세션 격리가 빠지면 이 경로는 실제 DB(또는 바깥 환경값)가 된다."""
    basetemp = tmp_path_factory.getbasetemp().resolve()
    assert basetemp in module_scope_db_path.resolve().parents, (
        f"모듈 스코프 픽스처가 임시 DB 밖을 본다: {module_scope_db_path} — "
        "conftest의 세션 격리(isolate_db_session)가 빠졌다")


def test_함수_스코프는_여전히_테스트마다_새_DB를_받는다(tmp_path):
    """세션 격리를 더해도 테스트마다 빈 DB를 주는 약속은 그대로다."""
    assert db.active_db_path() == tmp_path / "isolated.db"
