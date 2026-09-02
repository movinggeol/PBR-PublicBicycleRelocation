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
    res = client.get("/run")
    assert "계획 실행" in res.text
    assert 'action="/runs"' in res.text


def test_index_has_vehicle_count_fields(client):
    """차량 대수를 웹에서 조정할 수 있어야 한다(기본 21대 / 회차당 10대)."""
    res = client.get("/run")
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
    html = client.get("/run").text
    latest = html[html.index("최신 산출물"):html.index("저장된 실행")]

    assert "data-page-item" in latest, "최신 산출물 표를 못 찾았다 — 테스트가 낡았다"
    assert "data-sortable" not in latest, "분류 묶음 표에 정렬이 걸렸다"


def test_index_offers_only_periods_we_have(client, monkeypatch, tmp_path):
    """순수요 기간은 계산해 둔 달만 고르게 한다 — 없는 달은 step0에서 멈춘다."""
    for label in ("25년 11월", "26년 03월"):
        (tmp_path / f"st_net_daily ({label}).csv").write_text("x", encoding="utf-8")
    monkeypatch.setattr(project_config, "NET_DEMAND_DIR", tmp_path)

    html = client.get("/run").text
    assert 'name="period"' in html and "<select" in html
    assert 'value="26년 03월"' in html, "가진 달이 목록에 없다"
    assert 'value="25년 12월"' not in html, "없는 달이 목록에 있다"


def test_index_has_duration_checkboxes(client):
    """시간대는 네 창 중에서 고른다(직접 적게 두면 오타가 step4까지 흘러간다)."""
    html = client.get("/run").text
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
    res = client.get("/run")
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

    html = client.get("/run").text

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

    html = client.get("/run").text

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


# ───────────────── 도움말 풍선 고정 (수정안 22번) ─────────────────

def test_pinned_tooltip_has_a_close_button(client):
    """설명을 클릭해 고정하면 x를 눌러야 사라진다 — 그 장치가 실려 있는가."""
    html = client.get("/kpi").text
    assert "tipclose" in html, "닫기 단추가 없으면 고정한 풍선을 못 닫는다"
    assert "data-pinned" in html, "고정 상태 표시가 있어야 한다"
    # 고정 중에는 클릭을 받아야 x를 누를 수 있다(평소에는 pointer-events:none).
    assert "#tipbox[data-pinned] { pointer-events: auto" in html


def test_only_explanation_labels_are_pinnable(client):
    """고정은 `.tip` 딱지에서만 한다.

    내비 링크와 화면 전환 단추에도 data-tip이 붙어 있다. 아무 data-tip에서나
    클릭을 가로채면 누르는 순간 고정만 되고 **페이지 이동이 막힌다**.
    """
    html = client.get("/run").text
    assert 'classList.contains("tip")' in html, (
        "고정 대상을 .tip으로 좁히지 않으면 내비 링크가 죽는다")
    # 내비 링크가 여전히 data-tip을 달고 있는지 — 전제가 무너지면 이 테스트도 무의미하다
    assert 'href="/kpi"' in html and "data-tip=" in html


# ───────── 수정안 30·31·32 (1.23.9) ─────────

def test_대조_단추가_본문과_겹치지_않는다(client):
    """`.actions`가 hero 밖에서도 flex여야 하고, 뒤따르는 설명글이 위로 겹치면 안 된다.

    1.22.0에서 단추를 hero 밖에 두면서 생긴 문제다 — `.section-lead`의
    `margin-top: -6px`이 글자를 단추 위로 끌어올렸다(수정안 30번).
    """
    html = client.get("/orders").text

    assert ".actions { display: flex" in html, "hero 밖 .actions에 flex가 없다"
    assert ".actions + .section-lead" in html, "단추 뒤 설명글의 여백 규칙이 없다"


def test_표_팝업이_계단처럼_비껴_뜬다(client):
    """여러 개를 열면 완전히 겹쳐 새 창이 떴는지 알 수 없었다 (수정안 31번)."""
    html = client.get("/kpi").text

    assert "function cascade(" in html
    assert "function openCount(" in html
    # 사용자가 끌어 옮긴 창은 그 자리를 지켜야 한다
    assert "if (!moved) cascade(panel, depth)" in html


def test_KPI_타일에_용어_설명이_붙는다():
    """상단 타일에는 설명이 없어 무슨 지표인지 알 수 없었다 (수정안 32번).

    아래 표 머리글과 **같은 문구**를 써야 한다 — 갈리면 같은 지표를 두 가지로
    설명하게 된다. 그래서 **문구를 라우트에서 만든다**는 것을 직접 확인한다
    (KPI 자료가 없는 DB에서는 타일 자체가 그려지지 않으므로 HTML로는 못 잰다).
    """
    import inspect

    from webapp import app as webapp_app

    source = inspect.getsource(webapp_app.kpi_page)
    shared = "목표 재고까지 모자란 양을 계획이 몇 % 메웠는지입니다."
    assert shared in source, "타일 설명이 표 머리글과 다른 문구다"
    for label in ("한 번에 닿는 범위", "km당 개선", "시간 예산 준수"):
        assert label in source


def test_KPI_타일_설명이_표_머리글과_같은_문구다():
    """두 곳이 갈리면 같은 지표를 다르게 설명하게 된다.

    타일 쪽 문구는 라우트가, 표 머리글은 템플릿이 갖고 있다. **둘을 맞대어**
    확인한다 — KPI 자료가 없는 DB에서는 화면에 아무것도 안 그려지므로
    HTML만 봐서는 이 규칙을 지킬 수 없다.
    """
    import inspect
    from pathlib import Path

    from webapp import app as webapp_app

    shared = "목표 재고까지 모자란 양을 계획이 몇 % 메웠는지입니다."
    template = Path(webapp_app.__file__).parent / "templates" / "kpi.html"
    assert shared in inspect.getsource(webapp_app.kpi_page)
    assert shared in template.read_text(encoding="utf-8")


# ── 메인 화면(현황판) — 수정안 33 ──────────────────────────────────────

def test_home_is_not_the_run_form(client):
    """'/'는 현황판이고 실행 폼은 '/run'이다.

    폼을 '/'에 두면 처음 들어온 사람이 **아무 맥락 없이 입력칸부터** 만난다.
    두 화면이 다시 합쳐지면 이 테스트가 잡는다.
    """
    home = client.get("/").text
    run = client.get("/run").text

    assert 'action="/runs"' not in home, "메인에 실행 폼이 있다 — /run으로 가야 한다"
    assert 'action="/runs"' in run, "/run에 실행 폼이 없다"
    assert "마지막 계획" in home, "메인에 요약이 없다"


def test_home_does_not_light_up_a_flow_step(client):
    """메인은 작업 흐름 밖이다 — '1 실행'을 켜면 실행 화면에 있는 것처럼 보인다."""
    home = client.get("/").text
    run = client.get("/run").text

    assert 'href="/run" aria-current="page"' not in home, \
        "메인에서 '1 실행'이 현재 위치로 켜져 있다"
    assert 'aria-current="page"' in run, "/run에서 '1 실행'이 안 켜진다"


def test_home_marks_experiment_runs(client, monkeypatch):
    """실험용 실행(sweep- 등)이 '마지막 계획'으로 뜨면 **운영 계획으로 오해**한다.

    DB에는 파라미터 스윕 결과가 함께 쌓이므로 가장 최근 것이 실험일 수 있다.
    """
    import pandas as pd
    from webapp import store

    fake = pd.DataFrame([{
        "run_label": "sweep-21", "duration": "_05_10",
        "computed_at": "2026-08-24 17:12:14", "bikes_moved": 100,
        "vehicles_used": 5, "total_distance_km": 200.0,
        "stockout_hours_before": 2.0, "stockout_hours_after": 1.0,
        "max_cluster_minutes": 110.0, "time_budget_minutes": 120.0,
    }])
    monkeypatch.setattr(store, "kpi", lambda *a, **k: fake)

    html = client.get("/").text
    assert "실험용 실행" in html, "실험 라벨인데 표시가 없다"


# ── 작업지시서 조치 제안 — 수정안 39 ──────────────────────────────────

def test_blocked_rows_get_an_action():
    """'불가'만 알려 주고 끝내면 현장에서 판단이 사람 몫으로 남는다.

    같은 군집 안에서만 대안을 찾는다 — 차량 1대가 군집 1개를 맡으므로
    군집을 벗어나면 그 회차에 갈 수 없는 곳이다(docs/구현/FLEET.md).
    """
    from webapp.orders import _suggest_actions

    rows = [
        {"station_name": "가", "cluster": 1, "action": "pick",
         "need": 10, "possible": 0, "status": "불가"},
        {"station_name": "나", "cluster": 1, "action": "pick",
         "need": 5, "possible": 12, "status": "가능"},
        {"station_name": "다", "cluster": 2, "action": "drop",
         "need": 8, "possible": 2, "status": "넘침"},
    ]
    _suggest_actions(rows)

    assert "나" in rows[0]["advice"], "같은 군집의 여유 대여소를 짚어야 한다"
    assert rows[1]["advice"] == "", "'가능'한 행에는 조치가 붙지 않는다"
    # 같은 군집에 대안이 없으면 대안 없이 무엇을 할지 알려 준다
    assert rows[2]["advice"], "'넘침'인데 조치가 비어 있다"
    assert "군집" in rows[2]["advice"] or "차고지" in rows[2]["advice"]


def test_advice_does_not_cross_clusters():
    """다른 군집의 대여소를 대안으로 내놓으면 **갈 수 없는 곳**을 시킨다."""
    from webapp.orders import _suggest_actions

    rows = [
        {"station_name": "가", "cluster": 1, "action": "pick",
         "need": 10, "possible": 0, "status": "불가"},
        {"station_name": "먼곳", "cluster": 9, "action": "pick",
         "need": 1, "possible": 50, "status": "가능"},
    ]
    _suggest_actions(rows)

    assert "먼곳" not in rows[0]["advice"], "다른 군집을 대안으로 내놨다"


# ── 순수요 기간 기본값 — 수정안 34 ────────────────────────────────────

def test_period_default_is_the_latest_month(client):
    """기본값은 **가장 최근 달**이다.

    '직전 달'은 기본값이 될 수 없다 — 12개월 중 3개월에서만 존재한다(실측).
    가장 최근 달은 자료가 어떻든 항상 있다.
    """
    from project_config import latest_period

    html = client.get("/run").text
    assert f'<option value="{latest_period()}"' in html
    assert f'{latest_period()}"\n              selected' in html \
        or "selected" in html, "기본 선택이 없다"


def test_year_ago_hint_only_when_it_exists(monkeypatch):
    """1년 전 같은 달은 **있을 때만** 안내한다.

    자료에 구멍이 있어(25년 02·03·12월) 없는 경우가 흔하다. 없는 달을 권하면
    step0가 멈춘다.
    """
    from webapp import app as app_module

    monkeypatch.setattr(app_module, "latest_period", lambda: "26년 03월")
    assert app_module._year_ago_period(["25년 03월", "26년 03월"]) == "25년 03월"
    assert app_module._year_ago_period(["25년 04월", "26년 03월"]) is None, \
        "자료에 없는 달을 권했다"


def test_1열_그리드도_minmax_0으로_접는다():
    """`.data-grid`를 1열로 접을 때 `1fr`만 쓰면 좁은 화면에서 페이지가 밀린다.

    `1fr`은 최소 크기가 auto(=min-content)라, 표의 min-content 폭이 트랙을
    컨테이너 밖으로 밀어낸다. `.table-wrap`의 overflow-x가 있어도 소용없다 —
    자기 폭이 먼저 확정돼야 스크롤하기 때문이다.

    실측: 390px에서 /orders가 553px, /data가 542px로 새 나가 **페이지 전체**가
    가로로 스크롤됐다(2열 규칙에는 `minmax(0, 1fr)`이 이미 들어 있었다).
    """
    import re
    from pathlib import Path

    from webapp import app as webapp_app

    css = (Path(webapp_app.__file__).parent / "templates" / "base.html").read_text(
        encoding="utf-8")

    narrow = re.search(r"@media \(max-width: 900px\) \{ \.data-grid \{([^}]*)\}", css)
    assert narrow, ".data-grid의 1열 규칙을 찾지 못했다"
    assert "minmax(0" in narrow.group(1), (
        f"1열 규칙이 minmax(0, 1fr)이 아니다: {narrow.group(1).strip()}")


# ── 모바일 모드 (TODO 20) ──────────────────────────────────────────────

def test_모바일_토글이_모든_화면에_있다(client):
    """토글은 base.html에 있으므로 어느 화면에서나 같은 자리에 있어야 한다."""
    for path in ("/", "/run", "/kpi", "/vehicles", "/orders", "/maps", "/data", "/guide"):
        html = client.get(path).text
        assert 'id="view-toggle"' in html, f"{path}에 모바일 토글이 없다"
        assert 'aria-pressed' in html, f"{path}의 토글에 눌림 상태가 없다"


def test_모바일_규칙은_한_벌만_유지한다():
    """폭(@media)과 버튼(data-view) 양쪽에서 켜지지만 **규칙 본문은 한 벌**이다.

    CSS의 `@media`는 버튼으로 끌 수 없다. 그래서 폭 판정을 스크립트가 하고
    `<html data-narrow>`를 붙였다 뗀다 — CSS는 그 속성 하나만 본다. 규칙을
    두 벌로 적으면 시간이 지나며 한쪽만 고치게 되고, 버튼으로 켠 화면과
    좁은 화면이 서로 달라진다.
    """
    import re
    from pathlib import Path

    from webapp import app as webapp_app

    css = (Path(webapp_app.__file__).parent / "templates" / "base.html").read_text(
        encoding="utf-8")

    # 표를 눕히는 규칙이 [data-narrow]에만 걸려 있는지 — @media 안에 같은
    # 선택자를 또 두면 두 벌이 된다.
    assert "[data-narrow] table.m-cards" in css
    media_blocks = re.findall(r"@media \(max-width: \d+px\) \{(.*?)\n    \}", css, re.S)
    for block in media_blocks:
        assert "m-cards" not in block, (
            "표 카드 규칙이 @media 안에도 있다 — 두 벌이 되면 한쪽만 고치게 된다")


def test_카드로_눕는_표는_모든_칸에_라벨이_있다():
    """`.m-cards` 표의 `<td>`는 전부 `data-label`을 갖는다.

    카드로 누우면 머리글 줄이 사라지므로, 각 칸이 무슨 값인지는 `data-label`이
    유일한 단서다. 하나라도 빠지면 그 칸만 라벨 없이 값만 떠서 무슨 숫자인지
    알 수 없다. 빈 라벨(`data-label=""`)은 **의도적으로** 값만 넓게 쓰는
    칸이므로 허용한다(순번·대여소처럼 그 자체로 무엇인지 아는 칸).
    """
    import re
    from pathlib import Path

    from webapp import app as webapp_app

    templates = Path(webapp_app.__file__).parent / "templates"
    checked = 0
    for path in templates.glob("*.html"):
        text = path.read_text(encoding="utf-8")
        for table in re.findall(r'<table[^>]*class="[^"]*m-cards[^"]*"[^>]*>(.*?)</table>',
                                text, re.S):
            body = re.search(r"<tbody>(.*?)</tbody>", table, re.S)
            if not body:
                continue
            for cell in re.findall(r"<td\b[^>]*>", body.group(1)):
                assert "data-label" in cell, (
                    f"{path.name}: 라벨 없는 칸이 있다 — {cell.strip()[:70]}")
                checked += 1
    assert checked > 0, "m-cards 표를 하나도 찾지 못했다(선택자가 바뀌었나?)"


def test_모든_표_머리글에_scope가_있다():
    """머리글에 `scope`가 없으면 스크린리더가 셀과 머리글을 못 잇는다.

    93개 전부 빠져 있었다(1.26.94). `<thead>` 안이면 `col`, 본문 행의 첫
    칸이면 `row`다 — 값이 뒤바뀌면 없느니만 못하므로 위치까지 함께 본다.
    """
    import re
    from pathlib import Path

    from webapp import app as webapp_app

    templates = Path(webapp_app.__file__).parent / "templates"
    checked = 0
    for path in sorted(templates.glob("*.html")):
        text = path.read_text(encoding="utf-8")
        if "<th" not in text:
            continue
        # thead 구간을 표시해 두고, 각 <th>가 그 안인지 밖인지로 기대값을 정한다
        in_head = False
        for m in re.finditer(r"</?thead\b|<th\b[^>]*>", text):
            tok = m.group(0)
            if tok.startswith("<thead"):
                in_head = True
            elif tok.startswith("</thead"):
                in_head = False
            else:
                want = 'scope="col"' if in_head else 'scope="row"'
                assert want in tok, (
                    f"{path.name}: {want}가 없다 — {tok[:70]}")
                checked += 1
    assert checked > 90, f"머리글을 {checked}개밖에 못 찾았다(선택자가 바뀌었나?)"


def test_지시서_머리글은_접히고_값은_안_접힌다():
    """지시서 표는 2열 카드 안(514px)이라 긴 머리글 하나가 표 전체의
    최소폭을 밀어 올렸다 — '싣고 남는 수'가 81px을 잡아 7열 합 526px로
    카드보다 12px 넓었다(1.26.94).

    base.html은 `th, td`를 통째로 nowrap으로 두는데, 여기서는 **머리글만**
    풀어 준다. 값(td)까지 풀면 '6.37 km'가 두 줄로 갈라진다.
    """
    from pathlib import Path

    from webapp import app as webapp_app

    css = (Path(webapp_app.__file__).parent / "templates" / "orders.html").read_text(
        encoding="utf-8")

    assert ".sheet thead th { white-space: normal;" in css, (
        "지시서 머리글이 접히지 않는다 — 표가 카드 밖으로 새어 나간다")
    assert ".sheet tbody td { white-space: normal" not in css, (
        "값까지 접으면 숫자가 두 줄로 갈라진다")


def test_실행_폼의_터치_타깃이_44px을_지킨다():
    """44px 규약을 표 안의 링크에만 적용하고 폼은 빠뜨렸었다 — 실측이
    체크박스 22px(라벨 포함)·입력칸 38px·셀렉트 40px이었다(1.26.94).

    `/run`은 모바일 작업 흐름의 1단계다. 체크박스 네모 자체는 16px로 두고
    **감싼 라벨**이 누를 면을 갖는다(표 안 링크에서 쓴 것과 같은 수법).
    """
    from pathlib import Path

    from webapp import app as webapp_app

    css = (Path(webapp_app.__file__).parent / "templates" / "base.html").read_text(
        encoding="utf-8")

    narrow = css[css.index("[data-narrow]"):]
    assert "[data-narrow] .checks label { min-height: 44px;" in narrow, (
        "체크박스를 감싼 라벨이 44px을 채우지 않는다")
    assert "[data-narrow] .field select { padding: 12px 13px; min-height: 44px; }" in narrow, (
        "폼 입력칸이 44px을 채우지 않는다")


def test_인쇄는_모바일_모드를_무시한다():
    """모바일 모드를 켠 채 인쇄해도 종이는 표 그대로여야 한다.

    작업지시서는 차량 한 대가 한 장이다. 카드로 누운 채 인쇄되면 한 대가
    여러 장으로 흩어져 현장에 그대로 못 낸다.
    """
    from pathlib import Path

    from webapp import app as webapp_app

    css = (Path(webapp_app.__file__).parent / "templates" / "base.html").read_text(
        encoding="utf-8")

    print_block = css[css.index("@media print"):]
    assert "table.m-cards td" in print_block
    assert "display: table-cell" in print_block, "인쇄에서 칸이 표로 돌아오지 않는다"
    assert "table-header-group" in print_block, "인쇄에서 머리글이 돌아오지 않는다"

    # 빈 라벨 칸은 화면 규칙의 가중치가 (0,3,3)으로 더 높다. 인쇄에서 함께
    # 되돌리지 않으면 **첫 칸만** block으로 남아 표 밖으로 떨어진다(실측).
    assert 'td[data-label=""]' in print_block, (
        "빈 라벨 칸이 인쇄에서 되돌려지지 않는다 — 순번·대여소가 표 밖으로 떨어진다")

    # 화면 전용 조작부(정렬 줄 등)를 감추는 규칙은 base.html에 있어야 한다.
    # orders.html에만 두면 다른 화면에서는 인쇄에 그대로 나온다(실측).
    assert ".no-print" in print_block, "no-print 규칙이 공용 인쇄 블록에 없다"


def test_카드_모드_정렬은_데스크톱과_같은_함수를_쓴다():
    """모바일 정렬 줄은 머리글의 정렬 함수를 그대로 부른다.

    카드로 누우면 머리글이 숨겨져 누를 곳이 없어진다. 정렬 줄을 따로 만들되
    **정렬 규칙까지 따로 만들면** 같은 열을 같은 방향으로 정렬했는데 데스크톱과
    모바일의 결과가 달라진다. `th.__pbrSort`를 노출해 재사용한다.
    """
    from pathlib import Path

    from webapp import app as webapp_app

    js = (Path(webapp_app.__file__).parent / "templates" / "base.html").read_text(
        encoding="utf-8")

    assert "th.__pbrSort = sort;" in js, "정렬 함수가 노출되지 않았다"
    assert "th.__pbrSort()" in js, "정렬 줄이 그 함수를 부르지 않는다"
    # 정렬 줄이 자기만의 비교 로직을 갖고 있으면 두 벌이 된다.
    bar = js[js.index("카드 모드 정렬 줄"):]
    bar = bar[:bar.index("})();")]
    assert "localeCompare" not in bar, "정렬 줄이 자체 비교 로직을 갖고 있다"
    assert "sort(function" not in bar, "정렬 줄이 자체 정렬을 하고 있다"


# ── /kpi 표 열 접기 ─────────────────────────────────────────────────

def test_kpi_표는_핵심_열만_기본이고_단추로_전체_열을_편다():
    """15열 표가 한눈에 안 들어와 실행·회차·개선률·이동거리·최장 작업만 남기고
    나머지는 .col-more로 접었다 — 단추와 대상 표가 실제로 템플릿에 같이 있어야
    접었다 펴는 스크립트가 붙을 자리가 있다.

    /kpi를 직접 호출하지 않는다 — 이 표는 rows가 있을 때만 렌더링되는데,
    이 테스트 모듈은 data/ 유무와 무관하게 통과해야 한다(파일 맨 위 설명).
    """
    from pathlib import Path

    from webapp import app as webapp_app

    kpi_html = (Path(webapp_app.__file__).parent / "templates" / "kpi.html").read_text(
        encoding="utf-8")

    assert "data-col-toggle" in kpi_html, "열 접기 단추가 없다"
    assert 'id="kpi-runs-table"' in kpi_html
    assert "col-more" in kpi_html, "접을 부가 열이 표시돼 있지 않다"
    # 핵심 열(개선률·이동거리·최장 작업)의 <th>에는 col-more가 없어야 한다
    for core in ("개선률", "이동거리", "최장 작업"):
        th = re.search(rf'<th[^>]*>[^<]*<span[^>]*>{core}<', kpi_html)
        assert th, f"{core} 열 머리글을 찾지 못했다"
        assert "col-more" not in th.group(0), f"핵심 열({core})이 접히게 돼 있다"


def test_열_접기_스크립트가_없으면_전부_보인다():
    """쪽 넘기기·정렬과 같은 원칙 — 스크립트가 꺼져 있으면 부가 열도 그대로
    보여야 한다. .col-more를 감추는 CSS 규칙마다 선택자에 .cols-collapsed가
    걸려 있는지 본다 — 없으면 스크립트 없이도(즉 늘) 숨는 규칙이라는 뜻이다."""
    import re
    from pathlib import Path

    from webapp import app as webapp_app

    css = (Path(webapp_app.__file__).parent / "templates" / "base.html").read_text(
        encoding="utf-8")

    rules = re.findall(r"([^\n{]*\.col-more[^\n{]*)\{\s*display:\s*none", css)
    assert rules, ".col-more를 감추는 규칙을 찾지 못했다"
    for selector in rules:
        assert "cols-collapsed" in selector, (
            f".col-more를 늘 숨기는 규칙이 있다(cols-collapsed 밖): {selector.strip()}")


# ── /vehicles 누적 작업 시간 막대그래프 ─────────────────────────────

def test_차량별_누적_막대그래프가_작업량_많은_순으로_정렬된다():
    """표는 이미 있지만 행이 21개라 누가 몰렸는지 한눈에 안 보였다.

    hbar()는 받은 순서 그대로 그린다(정렬은 부르는 쪽 책임, test_charts.py에서
    확인) — 그러니 vehicles_page가 실제로 minutes 내림차순으로 정렬해 넘기는지는
    여기서 소스를 봐야 한다. 이 정렬을 빠뜨리면 vehicle_id 순서(표의 기본
    정렬)로 그려져 '몰린 차량이 위'가 거짓말이 된다.
    """
    import inspect

    from webapp import app as webapp_app

    source = inspect.getsource(webapp_app.vehicles_page)
    assert 'sort_values("minutes", ascending=False)' in source, (
        "차량별 누적을 minutes 내림차순으로 정렬하지 않고 그래프에 넘긴다")


def test_vehicles_화면에_막대그래프_자리가_있다():
    """/vehicles를 직접 호출하지 않는다 — ensure_fleet()이 빈 DB에도 차량
    21대를 채워 두어 workload 자체는 늘 비지 않으므로(회차 0건이면 balance만
    None), 이 테스트 모듈의 '데이터 유무와 무관히 통과' 전제와 맞지 않는다.
    템플릿에 그래프 카드 자리가 실제로 있는지만 정적으로 본다."""
    from pathlib import Path

    from webapp import app as webapp_app

    html = (Path(webapp_app.__file__).parent / "templates" / "vehicles.html").read_text(
        encoding="utf-8")

    assert "workload_svg" in html, "막대그래프를 넣을 자리가 템플릿에 없다"


# ── /vehicles 표 행 접기 ────────────────────────────────────────────

def test_차량별_누적_표는_상위_몇_행만_펴_둔다():
    """막대그래프가 '어디에 몰렸나'를 이미 답하므로 표는 세부 조회용이다.
    21행을 다 펼치면 그 아래 '회차별 배정 이력'까지 스크롤이 멀어진다."""
    from pathlib import Path

    from webapp import app as webapp_app

    html = (Path(webapp_app.__file__).parent / "templates" / "vehicles.html").read_text(
        encoding="utf-8")

    assert "data-row-limit" in html, "행 접기 표시가 없다"
    limit = re.search(r'data-row-limit="(\d+)"', html)
    assert limit and int(limit.group(1)) < 21, (
        "보유 대수(21)보다 크거나 같으면 접히지 않는다")


def test_행_접기_표는_서버가_이미_정렬해_보낸다():
    """DB는 vehicle_id 순으로 준다(db.vehicle_workload의 ORDER BY). 그대로
    접으면 V01~V08이라는 아무 뜻 없는 여덟 대가 펴진다 — 화면의 '상위 8대'가
    거짓말이 되고, 위 막대그래프와 순서도 어긋난다."""
    import inspect

    from webapp import app as webapp_app

    source = inspect.getsource(webapp_app.vehicles_page)
    assert '"workload": store.records(sorted_wl)' in source, (
        "표에 정렬 안 된 workload를 그대로 넘기고 있다")


def test_행_접기_스크립트가_없으면_전부_보인다():
    """열 접기·쪽 넘기기와 같은 원칙이다. 접기는 스크립트가 tr에 [hidden]을
    걸어서 하므로, CSS에 tr을 그냥 숨기는 규칙이 있으면 안 된다 — 카드 모드
    보정 규칙에도 [hidden]이 걸려 있는지 본다."""
    import re as _re
    from pathlib import Path

    from webapp import app as webapp_app

    css = (Path(webapp_app.__file__).parent / "templates" / "base.html").read_text(
        encoding="utf-8")

    rules = _re.findall(r"([^\n{]*m-cards tr[^\n{]*)\{\s*display:\s*none", css)
    assert rules, "카드 모드에서 접힌 행을 숨기는 규칙을 찾지 못했다"
    for selector in rules:
        assert "[hidden]" in selector, (
            f"스크립트 없이도 행이 숨는 규칙이다: {selector.strip()}")


def test_행_접기가_정렬_뒤에도_다시_적용된다():
    """정렬하면 행 순서가 바뀐다. 처음 고른 행을 그대로 두면 정렬해 놓고도
    아까 숨은 행이 계속 숨어, 새 순서의 상위 N행이 안 보인다(쪽 넘기기가
    __pbrPagerRefresh를 다시 부르는 것과 같은 이유)."""
    from pathlib import Path

    from webapp import app as webapp_app

    js = (Path(webapp_app.__file__).parent / "templates" / "base.html").read_text(
        encoding="utf-8")

    assert "__pbrRowsRefresh" in js
    # 정렬이 끝난 자리 = 쪽 넘기기를 다시 부르는 곳(정의부가 아니라 호출부)
    call = js.index("box.__pbrPagerRefresh()")
    assert "__pbrRowsRefresh" in js[call:call + 400], (
        "정렬 뒤에 행 접기를 다시 적용하지 않는다")


# ── 접기 상태 기억 ──────────────────────────────────────────────────

def test_접기_상태를_기억한다():
    """열을 펴 놓고 링크를 눌렀다 돌아오면 다시 접혀 있었다. 테마·모바일 모드와
    같은 pbr-* 열쇠를 쓴다."""
    from pathlib import Path

    from webapp import app as webapp_app

    js = (Path(webapp_app.__file__).parent / "templates" / "base.html").read_text(
        encoding="utf-8")

    assert "pbr-fold-" in js, "접기 상태를 저장하지 않는다"
    assert "pbrRecall" in js and "pbrRemember" in js
    # 열·행 접기가 둘 다 쓰는가
    assert '"col-" +' in js and '"row-" +' in js


def test_저장이_막혀도_접기는_동작한다():
    """프라이빗 모드에서는 localStorage 접근 자체가 예외를 던진다. 테마가
    try/catch로 감싼 것과 같은 이유 — 기억이 안 될 뿐 기능은 돌아야 한다."""
    import re as _re
    from pathlib import Path

    from webapp import app as webapp_app

    js = (Path(webapp_app.__file__).parent / "templates" / "base.html").read_text(
        encoding="utf-8")

    for fn in ("pbrRecall", "pbrRemember"):
        body = js[js.index("function " + fn):]
        body = body[:body.index("\n  }") + 4]
        assert "try {" in body and "catch" in body, f"{fn}이 저장 실패를 안 막는다"


def test_정렬은_일부러_기억하지_않는다():
    """남이 보낸 링크를 열었을 때 내가 예전에 걸어 둔 정렬이 얹히면, 보낸 사람이
    말한 순서와 다른 화면을 보게 된다. 접기('얼마나 보여줄까')와 달리 정렬은
    '무엇을 말하느냐'를 바꾸므로 남기지 않는다."""
    from pathlib import Path

    from webapp import app as webapp_app

    js = (Path(webapp_app.__file__).parent / "templates" / "base.html").read_text(
        encoding="utf-8")

    start = js.index('document.querySelectorAll("table[data-sortable]")')
    sort_block = js[start:js.index("__pbrSortable", start)]
    assert "pbrRemember" not in sort_block and "localStorage" not in sort_block, (
        "정렬이 저장되고 있다")


# ── 명암비 (WCAG 2.1 AA) ────────────────────────────────────────────

def _luminance(hex_color: str) -> float:
    """WCAG 상대 휘도."""
    h = hex_color.lstrip("#")
    parts = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    parts = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in parts]
    return 0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]


def _contrast(fg: str, bg: str) -> float:
    a, b = _luminance(fg), _luminance(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def _tokens(block: str) -> dict:
    """`--이름: 값;` 을 모아 온다."""
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;", block))


def _css(name: str = "base.html") -> str:
    from pathlib import Path

    from webapp import app as webapp_app

    return (Path(webapp_app.__file__).parent / "templates" / name).read_text(
        encoding="utf-8")


def test_흐린_글자색이_라이트_모드에서_읽힌다():
    """--ink-4는 .hint·.sub·.empty·.step-note·눈금처럼 **뜻이 있는 글자**를
    칠한다. 장식이 아니므로 WCAG AA(본문 4.5:1)를 지켜야 한다.

    Apple의 2차 회색 #86868b는 라이트에서 흰 바탕 3.62:1, 오프화이트 3.33:1로
    미달이었다(1.26.102). 두 바탕 모두에서 재는 이유는 오프화이트가 더 빡빡해
    흰 바탕만 보면 통과로 착각하기 때문이다."""
    css = _css()
    light = css[css.index(":root"):css.index("@media (prefers-color-scheme: dark)")]
    tok = _tokens(light)

    ink4 = tok["ink-4"]
    for bg_name in ("canvas", "parchment"):
        ratio = _contrast(ink4, tok[bg_name])
        assert ratio >= 4.5, (
            f"--ink-4({ink4})가 --{bg_name}({tok[bg_name]}) 위에서 {ratio:.2f}:1 — "
            "4.5:1이 필요하다")


def test_파랑_위의_글자가_두_테마_모두에서_읽힌다():
    """--on-blue는 '파랑 위에 놓이는 글자' 토큰이고, --blue를 채움색으로 쓰는
    단추·현재 위치에만 쓰인다.

    다크의 #2997ff는 Apple이 **글자색**으로 정의한 파랑이라 그 위에 흰 글자를
    얹으면 3.02:1이었다(1.26.102). 파랑을 어둡게 해서는 못 푼다 — 흰 글자가
    통과할 만큼 어둡게 하면 이번엔 링크 글자가 어두운 바탕에서 깨진다."""
    css = _css()
    light = css[css.index(":root"):css.index("@media (prefers-color-scheme: dark)")]
    dark = css[css.index('[data-theme="dark"]'):]
    dark = dark[:dark.index("}")]

    for label, block in (("라이트", light), ("다크", dark)):
        tok = _tokens(block)
        ratio = _contrast(tok["on-blue"], tok["blue"])
        assert ratio >= 4.5, (
            f"{label}: --on-blue({tok['on-blue']})가 --blue({tok['blue']}) 위에서 "
            f"{ratio:.2f}:1 — 4.5:1이 필요하다")


def test_예산_초과_행의_글자는_흐리지_않다():
    """붉은 바탕(--critical-soft) 위에서 --ink-3은 4.42:1로 미달이다. 바탕을
    밝혀 맞추면 경고 색이 흐려지므로 반대로 간다 — **읽어야 하는 행**이라
    글자를 진하게 한다."""
    css = _css()
    assert "tr.over-budget .muted" in css, "예산 초과 행의 흐린 글자를 안 살리고 있다"
    rule = css[css.index("tr.over-budget .muted"):]
    rule = rule[:rule.index("}")]
    assert "--ink-2" in rule, "초과 행 글자를 --ink-2로 진하게 하지 않았다"


# ── 인쇄 ────────────────────────────────────────────────────────────

def test_인쇄하면_접어_둔_것이_전부_펴진다():
    """화면의 접기·쪽 넘기기는 좁은 화면을 위한 편의지 내용을 줄이는 것이
    아니다. 그런데 종이에는 스크롤도 단추도 없어서, 접힌 채로 인쇄하면 남은
    것을 볼 방법이 아예 없다.

    실측(1.26.102): /vehicles 21대 중 8대, /kpi 15열 중 5열, /data 156행 중
    50행만 찍히고 있었다. 화면에는 단추가 있으니 아무도 눈치채지 못했다."""
    css = _css()
    printed = css[css.index("@media print"):]

    for needle, what in (
        ("table.rows-collapsed tbody tr[hidden]", "행 접기"),
        ("table.cols-collapsed .col-more", "열 접기"),
        ("[data-page-item][hidden]", "쪽 넘기기"),
    ):
        assert needle in printed, f"인쇄에서 {what}를 펴지 않는다"


def test_인쇄하면_화면_골격이_빠진다():
    """내비 세 줄(44+53+52px)이 첫 장 위를 먹고, 종이에서 누를 수 없는 필터
    칩·쪽 단추가 그대로 찍혔다. `.no-print`를 템플릿마다 붙이게 두면 빠뜨린다 —
    실제로 orders.html에만 9개 있었고 나머지 화면은 0개였다(1.26.102).
    그래서 **선택자로** 건다."""
    css = _css()
    printed = css[css.index("@media print"):]

    for sel in ("header.global-nav", "nav.flow-nav", "nav.group-nav", "footer.site",
                ".filterbar", ".pager", ".col-toggle-wrap", ".row-more-wrap"):
        assert sel in printed, f"인쇄에서 {sel}를 감추지 않는다"


# ── 표 팝업이 좁은 화면을 넘지 않는다 ───────────────────────────────

def test_표_팝업은_좁은_화면에서_자리를_고정하지_않는다():
    """좁은 화면의 표 팝업은 아래에서 올라오는 판이라 자리를 CSS가 잡는다
    (left:0; right:0). pin()이 right를 auto로 바꾸면 그 묶음이 풀려 폭이 내용에
    맞춰 늘어난다 — 실측(1.26.102): 390px 화면에서 판이 1072px이 되고 창도 판도
    가로로 밀리지 않아 **25열 중 16열을 볼 방법이 없었다.**

    drag()는 이미 같은 이유로 좁은 화면을 건너뛰었다 — open()만 빠져 있었다."""
    js = _css()

    assert "NARROW_PANEL" in js, "좁은 화면 판정을 공유하지 않는다"
    open_fn = js[js.index("function open() {"):]
    open_fn = open_fn[:open_fn.index("function close()")]
    assert "NARROW_PANEL" in open_fn, (
        "좁은 화면에서도 자리를 고정하고 있다(pin/clamp)")
    assert "clamp(panel)" in open_fn


# ── 잘림 규칙을 문장에 쓰지 않는다 ──────────────────────────────────

def test_설명_문장에는_파일명용_잘림_클래스를_쓰지_않는다():
    """.step-file은 파일명 전용이다 — nowrap + ellipsis라 한 줄을 넘으면 잘린다.
    /runs/{id}의 'step1_cluster/top_st_clustering.py'처럼 뒤가 잘려도 알아볼 수
    있는 값에는 맞지만, /guide가 같은 클래스를 **설명 문장**에 써서 여덟 줄이
    말줄임표로 잘려 있었다(1.26.99). 처음 쓰는 사람을 위한 화면인데 정작
    설명이 안 보였다."""
    from pathlib import Path

    from webapp import app as webapp_app

    guide = (Path(webapp_app.__file__).parent / "templates" / "guide.html").read_text(
        encoding="utf-8")

    assert "step-file" not in guide, (
        "설명 문장에 파일명용 잘림 클래스(.step-file)를 쓰고 있다 — .step-note를 써라")
    assert "step-note" in guide, "설명용 클래스가 안 쓰이고 있다"


def test_설명용_클래스는_줄을_접는다():
    """.step-note는 .step-file과 한 글자만 다른 이름이라, 규칙까지 베껴 오면
    고친 의미가 없다. 접히는지(normal)와 한국어 낱말이 안 쪼개지는지를 본다."""
    import re as _re
    from pathlib import Path

    from webapp import app as webapp_app

    css = (Path(webapp_app.__file__).parent / "templates" / "base.html").read_text(
        encoding="utf-8")

    block = _re.search(r"\.step-note\s*\{([^}]*)\}", css)
    assert block, ".step-note 규칙이 없다"
    body = block.group(1)
    assert "white-space: normal" in body, "설명이 한 줄로 눌린다"
    assert "text-overflow" not in body, "설명을 말줄임표로 자르고 있다"
    assert "keep-all" in body, "한국어 낱말이 줄 끝에서 쪼개진다"

    # 파일명 쪽은 그대로여야 한다 — 이쪽까지 풀면 /runs/{id} 배치가 무너진다
    fileblock = _re.search(r"\.step-file\s*\{([^}]*)\}", css)
    assert fileblock and "nowrap" in fileblock.group(1), (
        "파일명 잘림 규칙까지 같이 풀렸다")


# ── 긴 파일 경로의 이름 안내 ────────────────────────────────────────

def test_긴_경로는_파일_이름을_따로_알린다():
    """원천 CSV 경로가 칸보다 88px 길어, 좁은 화면에서는 'data/raw_data/…'만
    보이고 어느 달 자료인지 말해 주는 이름이 밀려 있었다(1.26.99).

    값을 고칠 수 있는 칸이라 서버가 미리 적어 둘 수 없다 — 고치는 순간
    어긋난다. 스크립트가 지금 값에서 뽑고, 넘칠 때만 보여준다."""
    from pathlib import Path

    from webapp import app as webapp_app

    templates = Path(webapp_app.__file__).parent / "templates"
    index = (templates / "index.html").read_text(encoding="utf-8")
    js = (templates / "base.html").read_text(encoding="utf-8")

    assert 'id="f-raw-name"' in index, "파일 이름을 적을 자리가 없다"
    assert "hidden" in index[index.index('id="f-raw-name"'):
                             index.index('id="f-raw-name"') + 40], (
        "처음부터 보이면 안 된다 — 넘칠 때만 뜬다")

    assert 'getElementById("f-raw-name")' in js
    # 넘칠 때만 — 데스크톱에서 같은 말을 두 번 하지 않는다
    assert "scrollWidth > input.clientWidth" in js, (
        "넘침을 재지 않고 늘 보여주고 있다")
    # 값이 바뀌면 따라간다
    assert 'input.addEventListener("input"' in js, "값을 고쳐도 안 따라간다"
    # 윈도우 역슬래시 경로에서도 이름을 뽑는다
    assert r"split(/[\\/]/)" in js, (
        "역슬래시 경로에서 이름을 못 뽑는다 — 윈도우 경로가 통째로 이름이 된다")


# ── 빈 상태 규약 ────────────────────────────────────────────────────

def test_빈_상태는_무엇이_없는지와_어떻게_채우는지를_같이_말한다():
    """'아직 실행한 작업이 없습니다'처럼 사실만 적은 안내는 처음 온 사람에게
    다음 걸음을 알려 주지 않는다. .empty 문단마다 링크가 하나는 있어야 한다
    — 대부분 /run이고, 필터 때문에 빈 자리는 필터를 푸는 링크다."""
    from pathlib import Path

    from webapp import app as webapp_app

    templates = Path(webapp_app.__file__).parent / "templates"
    missing = []
    for path in sorted(templates.glob("*.html")):
        html = path.read_text(encoding="utf-8")
        # <p class="empty"> ... </p> 한 덩어리씩
        for block in re.findall(r'<p class="empty">(.*?)</p>', html, re.S):
            if "<a " not in block:
                missing.append(f"{path.name}: {' '.join(block.split())[:50]}")

    assert not missing, "빠져나갈 링크가 없는 빈 상태가 있다:\n" + "\n".join(missing)


def test_그래프가_빌_때도_자리와_이유가_남는다():
    """워크로드 그래프를 통째로 감추면 '왜 안 보이지'를 화면에서 알 수 없다.
    카드는 늘 두고 안쪽만 그래프↔안내로 갈린다."""
    from pathlib import Path

    from webapp import app as webapp_app

    html = (Path(webapp_app.__file__).parent / "templates" / "vehicles.html").read_text(
        encoding="utf-8")

    card = html[html.index("누적 작업 시간"):]
    card = card[:card.index("</div>") + 6]
    assert "{% else %}" in card, "그래프가 없을 때의 안내가 카드 안에 없다"
    # 카드 자체가 if로 감싸여 사라지면 안 된다
    before = html[:html.index('<div class="card viz-card">')]
    assert not before.rstrip().endswith("{% if workload_svg %}"), (
        "그래프 카드가 통째로 사라지게 돼 있다")


# ── /view 지도 iframe 실패 안내 ──────────────────────────────────────

def test_지도_iframe이_안_뜨면_안내문으로_바뀐다():
    """folium 지도는 CDN에서 Leaflet을 받는다 — 인터넷이 끊기면 iframe 안이
    빈 채로 남는데, 로컬 HTML 자체는 200으로 열려서 서버는 실패를 모른다.
    그래서 판정은 스크립트가 브라우저에서 한다: base.html에 그 로직이
    실제로 있는지, view.html에 판정 대상(iframe)과 안내문이 짝으로 있는지 본다."""
    from pathlib import Path

    from webapp import app as webapp_app

    templates = Path(webapp_app.__file__).parent / "templates"
    view_html = (templates / "view.html").read_text(encoding="utf-8")
    base_js = (templates / "base.html").read_text(encoding="utf-8")

    assert "data-map-frame" in view_html
    assert "data-map-fallback" in view_html
    assert "hidden" in view_html[view_html.index("data-map-fallback"):
                                  view_html.index("data-map-fallback") + 60], (
        "안내문이 처음부터 보이면 안 된다 — 지도가 뜨는 보통 경우에도 뜬다")

    assert "contentWindow" in base_js and ".L)" in base_js, (
        "Leaflet 전역(window.L) 존재로 성패를 판정하는 로직이 없다")
    assert "frame.hidden = true" in base_js and "fallback.hidden = false" in base_js
