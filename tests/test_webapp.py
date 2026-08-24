"""웹 대시보드 스모크 테스트.

라이브러리 업그레이드로 라우트가 통째로 깨지는 사고를 잡는 것이 목적이다.
(실제로 Starlette 1.x에서 TemplateResponse 시그니처가 바뀌어 전 페이지가
500이 된 적이 있다 — 버전관리 1.2.1)

산출물(data/)이 있든 없든 통과해야 하므로, 데이터에 의존하는 API는
"500이 아닐 것"까지만 검증한다.
"""
import re

import pytest
from fastapi.testclient import TestClient

import project_config
from project_config import DURATIONS, FLEET_SIZE, VEHICLES_PER_ROUND
from webapp import app as app_module
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
    assert "계획 실행" in res.text
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


@pytest.mark.parametrize("path", ["/", "/data", "/maps", "/kpi", "/vehicles"])
def test_data_tables_are_sortable(client, path):
    """표가 있는 화면은 열 정렬을 켠다.

    정렬은 base.html의 공용 스크립트가 `table[data-sortable]`을 찾아 거는 방식이라,
    템플릿에서 속성이 빠지면 조용히 기능만 사라진다.
    """
    html = client.get(path).text
    body = html.split("</style>", 1)[1]      # CSS 안의 선택자는 세지 않는다
    if "<table" not in body:
        # 산출물이 없는 환경(빈 DB·빈 data/)에서는 표 자체가 안 그려진다.
        pytest.skip(f"{path}에 아직 표가 없다")
    assert "data-sortable" in body, f"{path}의 표에 정렬이 안 걸려 있다"


def test_grouped_output_table_is_not_sortable(client):
    """분류 이름이 첫 행에만 붙는 표에는 정렬을 걸면 안 된다.

    행을 흩으면 분류 이름 없는 행만 남는다(쪽 넘기기에서 덩어리를 나눈 것과 같은 이유).
    """
    html = client.get("/").text
    latest = html[html.index("최신 산출물"):html.index("저장된 실행")]

    assert "data-page-item" in latest, "최신 산출물 표를 못 찾았다 — 테스트가 낡았다"
    assert "data-sortable" not in latest, "분류 묶음 표에 정렬이 걸렸다"


def test_index_offers_only_periods_we_have(client, monkeypatch, tmp_path):
    """순수요 기간은 계산해 둔 달만 고르게 한다 — 없는 달은 step0에서 멈춘다."""
    for label in ("25년 11월", "26년 03월"):
        (tmp_path / f"st_net_daily ({label}).csv").write_text("x", encoding="utf-8")
    monkeypatch.setattr(project_config, "NET_DEMAND_DIR", tmp_path)

    html = client.get("/").text
    assert 'name="period"' in html and "<select" in html
    assert 'value="26년 03월"' in html, "가진 달이 목록에 없다"
    assert 'value="25년 12월"' not in html, "없는 달이 목록에 있다"


def test_index_has_duration_checkboxes(client):
    """시간대는 네 창 중에서 고른다(직접 적게 두면 오타가 step4까지 흘러간다)."""
    html = client.get("/").text
    for duration in DURATIONS:
        assert f'value="{duration}"' in html, f"{duration}이 폼에 없다"
    assert 'type="checkbox" name="duration"' in html


def test_chosen_durations_are_passed_as_one_argument(client, monkeypatch):
    """고른 시간대는 콤마 하나로 묶여 전달된다. 순서는 하루 흐름 순이다."""
    captured = _capture_start(monkeypatch)
    res = client.post("/runs",
                      data={"duration": ["_15_20", "_05_10"]},
                      follow_redirects=False)

    assert res.status_code == 303
    args = captured[0]
    assert args[args.index("--duration") + 1] == "_05_10,_15_20"


def test_unknown_duration_is_rejected(client, monkeypatch):
    """맨 앞 밑줄이 빠진 값처럼 목록에 없는 시간대는 띄우기 전에 거른다."""
    _reject_start(monkeypatch)
    res = client.post("/runs", data={"duration": "10_15"}, follow_redirects=False)

    assert res.status_code == 400
    assert "시간대는" in res.text


def test_missing_period_is_rejected(client, monkeypatch, tmp_path):
    """계산해 둔 순수요가 없는 달로는 실행하지 않는다."""
    (tmp_path / "st_net_daily (25년 11월).csv").write_text("x", encoding="utf-8")
    monkeypatch.setattr(project_config, "NET_DEMAND_DIR", tmp_path)
    _reject_start(monkeypatch)

    res = client.post("/runs", data={"period": "25년 12월"}, follow_redirects=False)

    assert res.status_code == 400
    assert "순수요가 없습니다" in res.text


def test_index_has_day_type_select(client):
    """평일/휴일을 웹에서 고를 수 있어야 한다(기본은 오늘로 자동 판정)."""
    res = client.get("/")
    assert 'name="day_type"' in res.text
    assert "평일" in res.text and "휴일" in res.text
    assert 'value="auto"' in res.text


def test_day_type_is_passed_to_pipeline(client, monkeypatch):
    """폼의 요일 구분이 run_pipeline의 --day-type으로 전달된다."""
    captured = _capture_start(monkeypatch)
    res = client.post("/runs", data={"day_type": "holiday"}, follow_redirects=False)

    assert res.status_code == 303
    args = captured[0]
    assert args[args.index("--day-type") + 1] == "holiday"


def test_invalid_day_type_is_rejected(client, monkeypatch):
    """평일/휴일 외의 값은 파이프라인을 띄우기 전에 거른다 ('all'을 포함해서)."""
    _reject_start(monkeypatch)
    res = client.post("/runs", data={"day_type": "all"}, follow_redirects=False)

    assert res.status_code == 400
    assert "요일 구분" in res.text


def test_pipeline_runs_unbuffered_so_progress_is_live(monkeypatch, tmp_path):
    """진행 표시가 실시간이어야 한다.

    출력을 파일로 넘기면 파이썬이 블록 버퍼링을 해서, run_pipeline이 찍는
    '[3/11] 실행:'이 몇 KB씩 몰려 나온다 — 3초마다 새로고침하는 화면이
    늘 실제보다 뒤처져 보인다.
    """
    captured = {}

    class FakeProc:
        def wait(self):
            return 0

    def fake_popen(command, **kwargs):
        captured.update(kwargs)
        return FakeProc()

    monkeypatch.setattr(jobs, "LOG_DIR", tmp_path)
    monkeypatch.setattr(jobs.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(jobs, "_save_registry", lambda: None)
    jobs.start_job([])

    env = captured["env"]
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8", "로그의 '—' 한 글자에 단계가 죽는다"


def test_typical_elapsed_is_the_median_of_successful_runs(monkeypatch):
    """예상 소요는 성공한 실행의 중앙값이다.

    평균이 아닌 이유: 중간에 오래 멈췄던 한 건이 평균을 통째로 끌어올린다.
    실패·중단된 실행과 시각이 없는 실행은 표본에서 뺀다.
    """
    def job(job_id, status, started, finished):
        return jobs.Job(id=job_id, status=status,
                        started_at=started, finished_at=finished)

    samples = [
        job("3", "success", "2026-08-24 10:00:00", "2026-08-24 10:02:00"),   # 120초
        job("2", "success", "2026-08-24 09:00:00", "2026-08-24 09:04:00"),   # 240초
        job("1", "failed", "2026-08-24 08:00:00", "2026-08-24 08:30:00"),    # 제외
        job("0", "success", "2026-08-24 07:00:00", ""),                      # 제외
    ]
    monkeypatch.setattr(jobs, "list_jobs", lambda: samples)

    assert jobs.typical_elapsed() == 180.0

    monkeypatch.setattr(jobs, "list_jobs", lambda: [])
    assert jobs.typical_elapsed() is None, "기록이 없으면 지어내지 않는다"


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



# ---------------- 최신 산출물 쪽 넘기기 ----------------

def _fake_groups(count):
    """산출물 카탈로그 흉내 — 분류마다 2건씩. (실데이터 유무와 무관하게 돌려야 한다)"""
    return [{
        "title": f"분류 {i}",
        "entries": [{"name": f"a{i}.csv", "relpath": f"x/a{i}.csv",
                     "mtime": "2026-08-20 10:00", "mtime_raw": 0, "size_kb": 1},
                    {"name": f"b{i}.html", "relpath": f"x/b{i}.html",
                     "mtime": "2026-08-20 10:00", "mtime_raw": 0, "size_kb": 1}],
    } for i in range(count)]


def test_latest_outputs_are_paged_by_category(client, monkeypatch):
    """최신 산출물은 **분류 단위로** 쪽이 나뉘어야 한다.

    분류 이름은 그 분류의 첫 행에만 붙으므로, 한 분류의 행들이 같은
    `data-page-item` 값으로 묶이지 않으면 이름 없는 행으로 시작하는 쪽이 생긴다.
    """
    monkeypatch.setattr(app_module.catalog, "latest_outputs",
                        lambda *a, **k: _fake_groups(7))

    html = client.get("/").text

    assert 'data-pager="5"' in html, "쪽 넘김 컨테이너가 없다"
    keys = re.findall(r'<tr data-page-item="(\d+)">', html)
    assert keys == [str(i) for i in range(7) for _ in range(2)], \
        "한 분류의 두 행이 같은 값으로 묶이지 않았다"


def test_paging_does_not_drop_rows_server_side(client, monkeypatch):
    """쪽 나눔은 화면에서만 한다 — 서버가 행을 잘라 보내면 안 된다.

    조작부를 만드는 것은 스크립트라, 스크립트가 없는 환경에서는 전부 보이는
    예전 동작 그대로여야 한다.
    """
    monkeypatch.setattr(app_module.catalog, "latest_outputs",
                        lambda *a, **k: _fake_groups(13))

    html = client.get("/").text

    assert len(re.findall(r'<tr data-page-item="\d+">', html)) == 26
    assert "분류 12" in html, "마지막 쪽에 갈 분류가 응답에서 빠졌다"
    assert 'class="pager"' not in html, \
        "조작부는 스크립트가 만든다 — 서버가 그려 보내면 안 된다"
    assert "hidden" not in re.findall(r'<tr data-page-item="\d+"[^>]*>', html)[-1], \
        "서버가 미리 숨기면 스크립트 없는 환경에서 내용이 사라진다"


@pytest.mark.parametrize("path, scanner", [("/data", "list_csvs"), ("/maps", "list_maps")])
def test_file_lists_are_paged_by_row(client, monkeypatch, path, scanner):
    """`/data`·`/maps`는 **행 단위**로 끊는다.

    여기서는 분류 이름이 표 밖(`<h2>`)에 있어 행으로 잘라도 이름 없는 쪽이 생기지
    않는다. 분류마다 카드가 따로라 페이저도 카드마다 붙는다.
    """
    monkeypatch.setattr(app_module.catalog, scanner, lambda: _fake_groups(3))

    html = client.get(path).text

    assert html.count('data-pager="5"') == 3, "카드마다 페이저 컨테이너가 있어야 한다"
    # 카드 하나가 2행이므로 행마다 다른 키를 받는다(덩어리 = 행).
    assert re.findall(r'<tr data-page-item="(\d+)">', html) == ["0", "1"] * 3


@pytest.mark.parametrize("path", ["/data", "/maps"])
def test_file_lists_send_every_row(client, monkeypatch, path):
    """쪽 나눔은 화면에서만 한다 — 서버가 뒤쪽 파일을 빼고 보내면 안 된다."""
    groups = [{"title": "많은 분류", "entries": [
        {"name": f"f{i}.csv", "relpath": f"x/f{i}.csv", "mtime": "2026-08-20 10:00",
         "mtime_raw": 0, "size_kb": 1} for i in range(15)]}]
    for scanner in ("list_csvs", "list_maps"):
        monkeypatch.setattr(app_module.catalog, scanner, lambda: groups)

    html = client.get(path).text

    assert len(re.findall(r'<tr data-page-item="\d+">', html)) == 15
    assert "f14.csv" in html, "마지막 쪽에 갈 파일이 응답에서 빠졌다"
