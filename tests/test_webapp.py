"""웹 대시보드 스모크 테스트.

라이브러리 업그레이드로 라우트가 통째로 깨지는 사고를 잡는 것이 목적이다.
(실제로 Starlette 1.x에서 TemplateResponse 시그니처가 바뀌어 전 페이지가
500이 된 적이 있다 — 버전관리 1.2.1)

산출물(data/)이 있든 없든 통과해야 하므로, 데이터에 의존하는 API는
"500이 아닐 것"까지만 검증한다.
"""
import pytest
from fastapi.testclient import TestClient

from project_config import FLEET_SIZE, VEHICLES_PER_ROUND
from webapp import jobs
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


def test_index_has_vehicle_count_fields(client):
    """차량 대수를 웹에서 조정할 수 있어야 한다(기본 21대 / 회차당 10대)."""
    res = client.get("/")
    assert 'name="fleet_size"' in res.text
    assert 'name="vehicles_per_round"' in res.text
    assert f'value="{FLEET_SIZE}"' in res.text
    assert f'value="{VEHICLES_PER_ROUND}"' in res.text


def _capture_start(monkeypatch) -> list:
    """jobs.start_job을 가로채 실제 파이프라인 대신 인자만 받아 둔다."""
    captured = []

    def fake_start(args):
        captured.append(list(args))
        return jobs.Job(id="테스트작업", args=list(args))

    monkeypatch.setattr(jobs, "start_job", fake_start)
    return captured


def test_vehicle_counts_are_passed_to_pipeline(client, monkeypatch):
    """폼의 두 대수가 run_pipeline 인자로 전달된다."""
    captured = _capture_start(monkeypatch)
    res = client.post("/runs",
                      data={"fleet_size": "15", "vehicles_per_round": "6"},
                      follow_redirects=False)

    assert res.status_code == 303
    args = captured[0]
    assert args[args.index("--fleet-size") + 1] == "15"
    assert args[args.index("--vehicles-per-round") + 1] == "6"


def _reject_start(monkeypatch) -> None:
    def fail(args):
        raise AssertionError("잘못된 입력으로 파이프라인이 실행되면 안 된다")

    monkeypatch.setattr(jobs, "start_job", fail)


@pytest.mark.parametrize("field", ["fleet_size", "vehicles_per_round"])
@pytest.mark.parametrize("value", ["0", "100", "열다섯", "3.5"])
def test_invalid_vehicle_count_is_rejected(client, monkeypatch, field, value):
    """범위를 벗어나거나 숫자가 아니면 파이프라인을 띄우지 않는다."""
    _reject_start(monkeypatch)
    res = client.post("/runs", data={field: value}, follow_redirects=False)

    assert res.status_code == 400
    assert "정수여야 합니다" in res.text


def test_per_round_over_fleet_is_rejected(client, monkeypatch):
    """회차당 투입 대수가 보유 대수보다 많으면 조용히 자르지 않고 되돌린다."""
    _reject_start(monkeypatch)
    res = client.post("/runs",
                      data={"fleet_size": "5", "vehicles_per_round": "10"},
                      follow_redirects=False)

    assert res.status_code == 400
    assert "보유 차량 대수" in res.text


def test_index_has_day_type_select(client):
    """평일/주말을 웹에서 고를 수 있어야 한다(기본 평일)."""
    res = client.get("/")
    assert 'name="day_type"' in res.text
    assert "평일" in res.text and "주말" in res.text


def test_day_type_is_passed_to_pipeline(client, monkeypatch):
    """폼의 요일 구분이 run_pipeline의 --day-type으로 전달된다."""
    captured = _capture_start(monkeypatch)
    res = client.post("/runs", data={"day_type": "weekend"}, follow_redirects=False)

    assert res.status_code == 303
    args = captured[0]
    assert args[args.index("--day-type") + 1] == "weekend"


def test_invalid_day_type_is_rejected(client, monkeypatch):
    """평일/주말 외의 값은 파이프라인을 띄우기 전에 거른다 ('all'을 포함해서)."""
    _reject_start(monkeypatch)
    res = client.post("/runs", data={"day_type": "all"}, follow_redirects=False)

    assert res.status_code == 400
    assert "요일 구분" in res.text


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
