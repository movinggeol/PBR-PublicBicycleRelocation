"""웹 대시보드 스모크 테스트.

라이브러리 업그레이드로 라우트가 통째로 깨지는 사고를 잡는 것이 목적이다.
(실제로 Starlette 1.x에서 TemplateResponse 시그니처가 바뀌어 전 페이지가
500이 된 적이 있다 — 버전관리 1.2.1)

산출물(data/)이 있든 없든 통과해야 하므로, 데이터에 의존하는 API는
"500이 아닐 것"까지만 검증한다.
"""
import pytest
from fastapi.testclient import TestClient

from webapp.app import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("path", ["/", "/maps", "/data", "/api/docs"])
def test_pages_render(client, path):
    """주요 페이지가 200으로 렌더링된다(템플릿 시그니처 회귀 감지)."""
    res = client.get(path)
    assert res.status_code == 200, f"{path} -> {res.status_code}"


def test_index_has_run_form(client):
    res = client.get("/")
    assert "파이프라인 실행" in res.text
    assert 'action="/runs"' in res.text


def test_favicon_no_content(client):
    assert client.get("/favicon.ico").status_code == 204


@pytest.mark.parametrize("path", [
    "/files/../../../etc/passwd",
    "/files/../../project_config.py",
    "/preview/../../requirements.txt",
    "/view/../../README.md",
])
def test_path_traversal_blocked(client, path):
    """data/ 밖 파일은 서빙되지 않는다."""
    assert client.get(path).status_code == 404


def test_non_allowed_suffix_blocked(client):
    """허용 확장자(.html/.csv) 외에는 서빙되지 않는다."""
    assert client.get("/files/webapp/app.py").status_code == 404


def test_unknown_job_404(client):
    assert client.get("/runs/없는작업").status_code == 404
    assert client.get("/api/runs/없는작업").status_code == 404


def test_cancel_unknown_job_404(client):
    assert client.post("/runs/없는작업/cancel").status_code == 404


@pytest.mark.parametrize("path", [
    "/api/stations", "/api/plans/ilp", "/api/plans/vrp", "/api/metrics",
])
def test_data_apis_do_not_crash(client, path):
    """산출물이 없으면 404, 있으면 200. 500(크래시)이 나서는 안 된다."""
    res = client.get(path)
    assert res.status_code in (200, 404), f"{path} -> {res.status_code}"
