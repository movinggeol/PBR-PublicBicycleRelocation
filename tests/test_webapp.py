"""웹 대시보드 스모크 테스트.

라이브러리 업그레이드로 라우트가 통째로 깨지는 사고를 잡는 것이 목적이다.
(실제로 Starlette 1.x에서 TemplateResponse 시그니처가 바뀌어 전 페이지가
500이 된 적이 있다 — 버전관리 1.2.1)

산출물(data/)이 있든 없든 통과해야 하므로, 데이터에 의존하는 API는
"500이 아닐 것"까지만 검증한다.
"""
import re
import time
from pathlib import Path

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


def test_웹에서_띄운_실행은_계획으로_못박힌다(client, monkeypatch):
    """실행 화면에서 띄운 것은 **틀림없이 운영 계획**이므로 종류를 선언한다.

    선언하지 않으면 `runs.kind`가 NULL로 남고 화면이 라벨로 짐작하는데, 그
    짐작은 새 이름 규칙이 생길 때마다 틀린다 — `obs-cmp-1520`이 첫 화면
    헤드라인에 '마지막 계획'으로 올라온 것이 그 사고였다(1.26.107).
    파이프라인 자신은 계획인지 실험인지 알 수 없다. **띄우는 쪽만 안다.**
    """
    captured = _capture_start(monkeypatch)
    res = client.post("/runs", data={"now": "2026-09-04 09"}, follow_redirects=False)

    assert res.status_code == 303
    args = captured[0]
    assert args[args.index("--run-kind") + 1] == "plan", \
        "웹 실행이 종류를 선언하지 않으면 화면이 다시 짐작에 기댄다"


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
        # ⚠️ `/kpi`는 **자료를 심어** 따로 검사한다 — 아래
        # `test_kpi_table_is_sortable_when_there_are_runs`. 이 건너뛰기에만
        # 기대면 그 화면은 어디서도 검사되지 않는다(실제로 그랬다, 1.26.124).
        pytest.skip(f"{path}에 아직 표가 없다")
    assert "data-sortable" in body, f"{path}의 표에 정렬이 안 걸려 있다"


def test_kpi_table_is_sortable_when_there_are_runs(client, monkeypatch):
    """`/kpi`는 실행이 없으면 표가 없어 위 검사가 **늘 건너뛴다.**

    로컬에서도 CI에서도 건너뛰었으므로, *"속성이 빠지면 조용히 기능만 사라진다"* 는
    그 보호가 이 화면에는 없었다. 자료를 심어 표가 그려지는 상태로 만들어 잰다.
    """
    import pandas as pd

    import db
    from webapp import store

    # 열을 손으로 나열하지 않는다 — 지표가 늘면 그 목록이 낡고, 라우트는
    # `stations` 같은 열이 없으면 KeyError로 죽는다(실제로 겪었다).
    row = {field: 1.0 for field in db.KPI_FIELDS}
    row.update({"run_label": "2026-08-24 17", "duration": "_05_10",
                "computed_at": "2026-08-24 17:12:14"})
    monkeypatch.setattr(store, "kpi", lambda *a, **k: pd.DataFrame([row]))

    body = client.get("/kpi").text.split("</style>", 1)[1]

    assert "<table" in body, "자료를 심었는데도 표가 없다 — 이 검사가 다시 공허해졌다"
    assert 'id="kpi-runs-table"' in body, "실행 목록 표가 그려지지 않았다"
    assert "data-sortable" in body, "/kpi의 표에 정렬이 안 걸려 있다"


def test_grouped_output_table_is_not_sortable(client):
    """분류 이름이 첫 행에만 붙는 표에는 정렬을 걸면 안 된다.

    행을 흩으면 분류 이름 없는 행만 남는다(쪽 넘기기에서 덩어리를 나눈 것과 같은 이유).
    """
    html = client.get("/run").text
    latest = html[html.index("최신 산출물"):html.index("저장된 실행")]

    # ⚠️ 이 검사는 **실제 산출물이 있어야** 성립한다. 산출물이 하나도 없으면
    #    화면이 "아직 산출물이 없습니다"만 그리므로 표 자체가 없다 —
    #    그때 실패로 보고하면 *자료가 없는 것*을 *회귀*로 잘못 읽는다
    #    (1.26.141에서 `PBR_DATA_ROOT` 격리를 켜자 실제로 그랬다).
    if "아직 산출물이 없습니다" in latest:
        pytest.skip("산출물이 없어 최신 산출물 표가 그려지지 않았다")

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


def test_감시_스레드가_사용자의_실행_이력에_쓰지_않는다(monkeypatch):
    """테스트가 사용자 데이터를 건드리면 안 된다 (1.26.149).

    🔴 실제로 샜다. `start_job()`이 띄우는 감시 스레드는 **테스트보다 오래
    산다** — monkeypatch가 `_save_registry`를 원복한 뒤 그 스레드가 깨어나
    *진짜* 함수를 부르고, 사용자의 `data/webapp/runs.json`에 인자도 로그도 없는
    0초짜리 가짜 작업이 박힌다. 실측으로 기록 15건 중 **11건이 가짜**였고,
    그것이 `typical_elapsed()`를 0으로 만들어 화면 안내까지 망가뜨렸다.

    여기서 지키는 것은 **격리가 실제로 걸려 있는가**다 — `conftest.py`의
    `isolate_job_registry`가 경로를 tmp로 돌려놨는지 본다.
    """
    real = Path(__file__).resolve().parents[1] / "data" / "webapp"
    assert not str(jobs.REGISTRY_FILE).startswith(str(real)), (
        f"실행 이력이 실제 경로를 가리킨다: {jobs.REGISTRY_FILE}")
    assert not str(jobs.LOG_DIR).startswith(str(real)), (
        f"로그가 실제 경로를 가리킨다: {jobs.LOG_DIR}")

    class FakeProc:
        def wait(self):
            return 0

    monkeypatch.setattr(jobs.subprocess, "Popen", lambda command, **kw: FakeProc())
    job = jobs.start_job([])
    for _ in range(50):                      # 감시 스레드가 저장할 때까지
        if not job.is_running:
            break
        time.sleep(0.02)

    assert jobs.REGISTRY_FILE.exists(), "임시 경로에는 써야 한다(격리는 벙어리가 아니다)"
    assert not (real / "runs.json").read_text(encoding="utf-8").count(job.id), \
        "사용자의 실행 이력에 테스트 작업이 들어갔다"


def test_예상_소요는_0분이어도_숨지_않는다(client, monkeypatch):
    """0은 거짓이 아니다 — `{% if %}`로 거르면 조용히 하드코딩 문구로 돌아간다.

    🔴 이것이 위 누수의 **두 번째 증상**이었다. 가짜 11건이 전부 0초라
    `typical_elapsed()`가 0.0을 냈고, 템플릿의 `{% if typical_minutes %}`에서
    0이 거짓이라 화면은 말없이 *"보통 2~4분"* 으로 되돌아갔다 — `home.html`이
    *"예상 소요는 사람이 적지 않는다"* 고 적어 둔 그 장치가 무력해진 것이다.

    이력을 고쳐도 이 자리는 따로 고쳐야 한다. 안 그러면 다음에 0이 나올 때
    같은 방식으로 또 숨는다.
    """
    monkeypatch.setattr(jobs, "typical_elapsed", lambda limit=20: 0.0)

    body = client.get("/guide").text
    assert "보통 0.0분" in body, "0분이 하드코딩 문구에 가려졌다"
    assert "보통 2~4분" not in body, "기록이 있는데 하드코딩 문구가 나온다"


def test_끝난_실행의_사라진_로그를_아직이라고_말하지_않는다(tmp_path):
    """'아직'은 곧 온다는 뜻이다 — 9일 전 끝난 실행에는 거짓말이다 (1.26.149).

    `_prune()`이 오래된 로그를 지우므로 **끝난 실행의 로그는 없을 수 있다.**
    그런데 문구가 하나뿐이라 성공한 실행도 *"로그가 아직 없습니다"* 라고 답했다.
    `interrupted`는 더 나쁘다 — 화면이 *"아래 로그로 판단하세요"* 라고 안내한다.
    """
    돌는중 = jobs.Job(id="달리는중", status="running")
    끝난것 = jobs.Job(id="끝난것", status="success")

    assert "아직" in jobs.read_log_tail(돌는중), "실행 중이면 곧 생기는 게 맞다"
    끝난_문구 = jobs.read_log_tail(끝난것)
    assert "아직" not in 끝난_문구, f"끝난 실행에 '아직'이라고 말한다: {끝난_문구}"
    assert "정리" in 끝난_문구, "왜 없는지 밝혀야 한다"


def test_이력에서_잘린_실행의_로그도_함께_지운다(tmp_path, monkeypatch):
    """레코드만 자르면 로그가 무한히 쌓인다 (1.26.149).

    실측 건당 약 360KB라 100건이면 35MB가 **이력에서 열 수도 없는** 파일로
    남는다. 이력에서 사라진 로그는 지워도 잃을 것이 없다.
    """
    monkeypatch.setattr(jobs, "LOG_DIR", tmp_path)
    monkeypatch.setattr(jobs, "MAX_HISTORY", 3)
    jobs._jobs.clear()

    for i in range(5):
        job = jobs.Job(id=f"2026090{i}-000000-000", status="success")
        jobs._jobs[job.id] = job
        job.log_path.write_text("로그" * 10, encoding="utf-8")

    jobs._prune()

    남은_이력 = set(jobs._jobs)
    남은_로그 = {p.stem.replace("run_", "") for p in tmp_path.glob("*.log")}
    assert len(남은_이력) == 3
    assert 남은_로그 == 남은_이력, (
        f"이력과 로그가 어긋난다 — 이력 {sorted(남은_이력)} / 로그 {sorted(남은_로그)}")


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
    from project_config import available_periods, latest_period

    # ⚠️ `latest_period()`는 자료가 없으면 **대비값**을 돌려준다 — 그 값으로
    #    화면을 검사하면 *자료 부재*를 *회귀*로 잘못 읽는다. 물어야 할 것은
    #    "보유한 기간이 있는가"이므로 `available_periods()`로 가른다
    #    (1.26.141에서 `PBR_DATA_ROOT` 격리를 켜자 실제로 걸렸다).
    if not available_periods():
        pytest.skip("순수요 자료가 없어 기간 선택지가 비어 있다")

    period = latest_period()
    html = client.get("/run").text
    assert f'<option value="{period}"' in html
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
    /runs/{id}의 'pipeline/step1_cluster/top_st_clustering.py'처럼 뒤가 잘려도 알아볼 수
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


def test_지도_안내문을_찾는_길이_감싸는_요소에_기대지_않는다():
    """**자리를 가정하면 마크업이 바뀔 때 조용히 죽는다.**

    예전 스크립트는 `frame.nextElementSibling`이 안내문이라고 가정했다.
    그 뒤 /maps가 iframe을 `<figure>`로 감싸면서 바로 다음 형제가
    `<figcaption>`이 되었고, 검사에서 조용히 빠져나가 **안내가 한 번도
    뜨지 않았다** — 인터넷이 끊기면 흰 상자 세 개만 남았다. 같은 파일을
    /view로 열면 정상이라 더 안 보였다.

    두 화면 모두 iframe과 안내문을 짝으로 갖고 있어야 하고, 스크립트는
    형제 자리가 아니라 **찾아서** 짝을 지어야 한다.
    """
    from pathlib import Path

    from webapp import app as webapp_app

    templates = Path(webapp_app.__file__).parent / "templates"
    base_js = (templates / "base.html").read_text(encoding="utf-8")

    # 두 화면 다 짝이 있어야 한다 — maps.html이 빠져 있어서 놓쳤다.
    for name in ("view.html", "maps.html"):
        html = (templates / name).read_text(encoding="utf-8")
        assert "data-map-frame" in html, f"{name}에 판정 대상 iframe이 없다"
        assert "data-map-fallback" in html, f"{name}에 안내문이 없다"

    # 인접 형제만 보고 포기하면 안 된다.
    assert "querySelector" in base_js, (
        "안내문을 형제 자리로만 찾고 있다 — 감싸는 요소가 바뀌면 죽는다")
    idx = base_js.index("data-map-frame")
    scope = base_js[idx:idx + 900]
    assert "closest(" in scope or "querySelector" in scope, (
        "지도 안내문을 찾는 자리에서 탐색이 아니라 자리 가정을 쓰고 있다")


# ─────────── 400% 확대에서 낱말이 쪼개지지 않는지 (1.26.114) ───────────

def test_내비_단추_라벨이_접히지_않는다():
    """`white-space: nowrap`이 옆의 `.util` 링크에는 있고 단추에는 없었다.

    1280px을 **400% 확대**하면 CSS 폭이 320px이 된다(WCAG 1.4.10이 보는 폭).
    거기서 이 단추만 눌려 라벨 "넓게"가 **한 글자씩 세로로** 섰다 — 폭 13px,
    두 줄. 자리가 모자라서가 아니었다(내비 자식 합 176px / 320px): 글자가
    접히니 상자가 44 → 54px로 커져 내비 한 줄의 높이까지 어긋났다.

    가로 스크롤 검사로는 못 잡는다 — 줄바꿈이 넘침을 흡수해 버리기 때문이다.
    """
    from pathlib import Path

    from webapp import app as webapp_app

    css = (Path(webapp_app.__file__).parent / "templates" / "base.html").read_text(
        encoding="utf-8")

    block = css[css.index(".theme-toggle {"):]
    block = block[:block.index("}")]
    assert "white-space: nowrap" in block, (
        "내비 단추가 접힐 수 있다 — 좁은 폭에서 라벨이 글자 단위로 세로로 선다")
    assert "flex-shrink: 0" in block, (
        "flex가 단추를 글자 폭 아래로 줄일 수 있다")


def test_설명표_코드가_필요할_때만_끊긴다():
    """`word-break: break-all`은 **다음 줄에 통째로 들어갈 토큰도** 갈랐다.

    400% 확대(320px)에서 `_05_10`·`--day-type`이 두 동강 났다(실측).
    `overflow-wrap: anywhere`는 먼저 토큰째 다음 줄로 내리고, 그래도 안
    맞을 때만 쪼갠다 — 넘침은 여전히 막으면서 멀쩡한 토큰은 살린다.
    """
    from pathlib import Path

    from webapp import app as webapp_app

    css = (Path(webapp_app.__file__).parent / "templates" / "base.html").read_text(
        encoding="utf-8")

    i = css.index(".table-prose code {")
    rule = css[i:css.index("}", i)]
    assert "overflow-wrap: anywhere" in rule, "긴 코드가 열을 넘칠 수 있다"
    assert "break-all" not in rule, (
        "break-all은 다음 줄에 들어갈 토큰까지 글자 단위로 가른다")


# ─────────── axe-core 자동 스캔이 잡은 것 (1.26.126) ───────────
#
# 8개 화면·데스크톱/모바일 양쪽을 axe-core(wcag2a/aa·wcag21aa)로 훑어 나온
# 것이다. 이전 접근성 축(넘침·터치 타깃·명암비·400% 확대)은 전부 사람이
# Playwright로 눈으로 본 것이었고, 도구로 자동 스캔한 것은 이번이 처음이다.

def test_풍선이_비어_있을_때는_보조기기에서_숨는다():
    """`#tipbox`는 role="tooltip"인 채 **늘 DOM에 있다**(opacity:0일 뿐

    display:none이 아니다). 뜨기 전에는 이름 없는 빈 툴팁으로 노출됐다
    (axe aria-tooltip-name). show()/hide()가 aria-hidden을 켜고 끈다.
    """
    from pathlib import Path

    from webapp import app as webapp_app

    html = (Path(webapp_app.__file__).parent / "templates" / "base.html").read_text(
        encoding="utf-8")

    i = html.index('<div id="tipbox"')
    tag = html[i:html.index(">", i)]
    assert 'aria-hidden="true"' in tag, "빈 채로 시작할 때도 보조기기에 노출된다"

    assert 'box.removeAttribute("aria-hidden")' in html, "떠 있을 때 다시 보이게 해야 한다"
    assert 'box.setAttribute("aria-hidden", "true")' in html, "닫힐 때 다시 감춰야 한다"


def test_문장_속_링크는_색만으로_말하지_않는다():
    """`.muted`·`.hint`·`.empty`는 설명문 한가운데 링크가 섞여 있다.

    기본 `a`는 :hover에서만 밑줄이 붙는데(옆의 평범한 글과 색만 다르다), 이
    세 곳은 문장 속 인라인 링크라 hover 전에는 색맹이면 못 알아본다
    (axe link-in-text-block). /run·/kpi·/guide·/api·/maps(figcaption)에
    실제로 이런 링크가 있다.
    """
    from pathlib import Path

    from webapp import app as webapp_app

    css = (Path(webapp_app.__file__).parent / "templates" / "base.html").read_text(
        encoding="utf-8")

    i = css.index(".muted a, .hint a, .empty a")
    rule = css[i:css.index("}", i)]
    assert "text-decoration: underline" in rule


# ───────────────────── 싣기·내리기 색 (1.26.107) ─────────────────────

def test_싣기_내리기_색이_두_테마_모두에서_읽힌다():
    """싣기·내리기는 **범주색**이고, 표의 글자를 칠하므로 본문 4.5:1이 필요하다.

    두 바탕을 다 재는 이유는 대조 표가 판정에 따라 행 배경을 바꾸기 때문이다
    (`tr.bad`=--critical-soft, `tr.warn`=--warning-soft). 흰 바탕만 보면
    통과로 착각한다 — 실제로 가장 빡빡한 것은 붉은 바탕이다."""
    css = _css()
    light = css[css.index(":root"):css.index("@media (prefers-color-scheme: dark)")]
    dark = css[css.index('[data-theme="dark"]'):]
    dark = dark[:dark.index("}")]

    for label, block in (("라이트", light), ("다크", dark)):
        tok = _tokens(block)
        for ink in ("pick-ink", "drop-ink"):
            assert ink in tok, f"{label} 블록에 --{ink}가 없다"
            for bg in ("canvas", "parchment", "warning-soft", "critical-soft"):
                ratio = _contrast(tok[ink], tok[bg])
                assert ratio >= 4.5, (
                    f"{label}: --{ink}({tok[ink]})가 --{bg}({tok[bg]}) 위에서 "
                    f"{ratio:.2f}:1 — 4.5:1이 필요하다")


def test_싣기_내리기에_예약된_색을_빌려_쓰지_않는다():
    """`--good-ink`는 상태 전용이고 `--blue`는 상호작용 전용이다
    (docs/구현/DESIGN.md). 범주를 그 색으로 칠하면 두 가지가 어긋난다 —
    규약이 깨지고, 무엇보다 **지도는 파랑이 싣기**라 같은 파랑이 두 화면에서
    반대 작업을 뜻하게 된다(1.26.107에서 실제로 그랬다)."""
    css = _css("orders.html")
    assert "td.pick { color: var(--pick-ink)" in css, "싣기가 범주색을 안 쓴다"
    assert "td.drop { color: var(--drop-ink)" in css, "내리기가 범주색을 안 쓴다"
    assert "td.drop { color: var(--blue)" not in css, "내리기가 상호작용색을 빌려 쓴다"
    assert "td.pick { color: var(--good-ink)" not in css, "싣기가 상태색을 빌려 쓴다"


def test_지도와_웹이_싣기_내리기를_같은_색상으로_칠한다():
    """색 값 자체는 다르다 — 지도는 **채운 원**이라 순색을 쓰고 웹은 **글자**라
    명암비를 맞춘 값을 쓴다. 같아야 하는 것은 **어느 쪽이 따뜻한 색인가**다.
    지도에서 싣기가 파랑인데 웹에서 내리기가 파랑이면 기사가 반대로 간다."""
    import mapviz

    css = _css()
    light = _tokens(css[css.index(":root"):css.index("@media (prefers-color-scheme: dark)")])

    def _hue_is_warm(hex_color: str) -> bool:
        h = hex_color.lstrip("#")
        r, _, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        return r > b

    assert _hue_is_warm(mapviz.DROP_COLOR), "지도의 내리기가 따뜻한 색이 아니다"
    assert not _hue_is_warm(mapviz.PICK_COLOR), "지도의 싣기가 차가운 색이 아니다"
    assert _hue_is_warm(light["drop-ink"]), "웹의 내리기가 지도와 반대 계열이다"
    assert not _hue_is_warm(light["pick-ink"]), "웹의 싣기가 지도와 반대 계열이다"


def test_불균형_지도가_색을_직접_박지_않는다():
    """세 지도와 웹이 한 벌을 쓰게 하는 것이 mapviz.py의 존재 이유다.
    `'red'`/`'blue'`를 파일에 박으면 한쪽만 고쳐지고 다시 갈라진다."""
    from pathlib import Path

    import mapviz

    src = (Path(mapviz.__file__).parent / "pipeline" / "step4_metrics" / "imbalance.py").read_text(
        encoding="utf-8")
    # ⚠️ 함수 이름을 틀리게 적으면 **조용히 파일 전체를 훑는다.** 예전에는
    # 없는 이름(`make_imbalance_map`)을 찾고 있어서 `if ... else 0` 때문에
    # 범위 좁히기가 한 번도 동작하지 않았다 — 통과는 했지만 우연이었다.
    marker = "def demand_satisfaction_map"
    assert marker in src, f"{marker}를 찾지 못했다 — 함수 이름이 바뀌었나?"
    body = src[src.index(marker):]
    assert "color = 'red'" not in body and 'color = "red"' not in body, \
        "불균형 지도가 색을 직접 박고 있다"
    assert "DROP_COLOR" in body and "PICK_COLOR" in body, \
        "불균형 지도가 mapviz의 색을 안 쓴다"
    assert "Drop —" not in body and "Pick —" not in body, \
        "범례가 아직 영어 용어를 쓴다"


# ───────────────── 조용히 거짓말하지 않는다 (1.26.107) ─────────────────

def test_문턱을_넘은_타일_값에_색_규칙이_있다():
    """home.html은 예산을 넘으면 `class="value bad"`를 붙인다. 그런데 CSS에는
    `.delta.bad`만 있고 `.value.bad`가 **없었다** — 122.8분(예산 120분)이
    평범한 검은 글씨로 떴다(1.26.107). 클래스를 붙이는 쪽과 칠하는 쪽이
    갈리면 화면은 경고했다고 믿으면서 아무것도 안 한다."""
    assert 'class="value {% if last.max_minutes > last.budget %}bad{% endif %}"' \
        in _css("home.html"), "홈이 초과 표시를 안 붙인다"
    assert ".tile .value.bad" in _css(), "초과 타일 값을 칠하는 규칙이 없다"


def test_배정_이력을_말없이_자르지_않는다():
    """상한을 두는 것은 맞다(실행마다 쌓인다). 자른 것을 **안 말하는 것**이
    틀렸다 — 86건 중 60건만 보여 주고 표가 그냥 끝났다(1.26.107).

    ⚠️ **말하는 것만으로는 모자랐다**(1.26.116). 200건에서 자르고 "N건 더
    있습니다"라고만 적으니, 실행이 12건만 쌓여도 그 안내가 늘 참이 되고
    나머지에는 닿을 길이 없었다. 지금 지키는 것은 *"잘렸다고 말하는가"* 가
    아니라 **"나머지로 가는 길이 있는가"** 다.
    """
    from pathlib import Path

    from webapp import app as webapp_app

    src = Path(webapp_app.__file__).read_text(encoding="utf-8")
    assert ".head(60)" not in src and ".head(ASSIGNMENT_LIMIT)" not in src, \
        "옛 상한이 남아 있다"
    assert "ASSIGNMENTS_PER_PAGE" in src, "라우트가 쪽 크기를 안 정한다"

    html = _css("vehicles.html")
    assert "전체 {{ assignments_total }}건" in html, "전체가 몇 건인지 안 말한다"
    assert "page={{ page + 1 }}" in html and "page={{ page - 1 }}" in html, \
        "나머지로 가는 길(쪽 넘기기)이 없다"


def test_실행_종류를_라벨_하드코딩으로_짐작하지_않는다():
    """예전에는 웹 라우트 안의 목록으로 실험 여부를 짐작했고, 목록에 없던
    `obs-cmp-1520`이 경고 없이 첫 화면 헤드라인에 올라왔다(1.26.107)."""
    from pathlib import Path

    from webapp import app as webapp_app

    src = Path(webapp_app.__file__).read_text(encoding="utf-8")
    assert '"g3000"' not in src and '"z199"' not in src, \
        "라우트에 라벨 하드코딩 목록이 남아 있다"
    assert "store.run_kind(" in src, "실행 종류를 저장소 계층에서 안 읽는다"


def test_웹이_db를_직접_열지_않는다():
    """`webapp/`에서 `db.py`를 아는 곳은 `store.py` 하나여야 한다 —
    "새 데이터 API는 store.load()를 써라"(pbr-pipeline 규약). 라우트가
    직접 `db.session()`을 열면 그 계층이 있는 이유가 없어진다(1.26.110)."""
    from pathlib import Path

    from webapp import app as webapp_app

    # ⚠️ `kpi_view.py`는 **알려진 예외**다. 1.19.3(2026-08-24)부터 `db.load_backtest`·
    #    `db.load_frame`을 직접 부르는데, `store.py`에 대응하는 조회가 없어서
    #    옮기려면 저장소 계층에 함수를 새로 내야 한다. 이번(1.26.110)은 **새로
    #    생긴 위반만** 되돌렸고 옛것은 손대지 않았다 — 목록에 남겨 두는 이유는
    #    "재 보고 남겨 둔 것"과 "못 본 것"을 구분하기 위해서다(TODO P3).
    KNOWN = {"store.py", "kpi_view.py"}

    webapp_dir = Path(webapp_app.__file__).parent
    offenders = []
    for path in sorted(webapp_dir.glob("*.py")):
        if path.name in KNOWN:
            continue
        for num, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]     # 주석 속 `db.py` 언급은 봐준다
            if re.search(r"(^|\s)import db(\s|$)", code) or "db.session(" in code:
                offenders.append(f"{path.name}:{num}: {line.strip()}")

    assert not offenders, "store.py 말고도 db를 직접 쓰는 곳이 있다:\n" + "\n".join(offenders)


def test_계획이_아닌_실행은_계획_필터에_안_뜬다():
    """도로 시간 수집기도 `runs`에 행을 남긴다. 필터 칩으로 내면 계획인 척
    섞여 있다가 눌러 보면 지표도 배정도 없는 빈 표만 나온다(1.26.107)."""
    import db

    assert db.classify_run_label("roadprobe-2026-09-03") == "probe"
    assert db.classify_run_label("obs-cmp-1520") == "experiment"
    assert db.classify_run_label("2026-08-27 23") == "plan"

    from pathlib import Path

    from webapp import app as webapp_app

    src = Path(webapp_app.__file__).read_text(encoding="utf-8")
    assert src.count("store.plan_runs()") == 2, \
        "/kpi·/vehicles의 필터가 계획만 고르지 않는다"


def test_실행_종류는_못박으면_짐작을_이긴다():
    """짐작은 컬럼이 생기기 전 행을 위한 폴백일 뿐이다. 라벨 규칙이 안 통하는
    이름을 쓰더라도 `kind`를 적어 두면 그것이 정답이어야 한다."""
    import db

    with db.session() as conn:
        db.ensure_run(conn, "아무이름-없는규칙", kind="probe")
        rows = db.list_runs(conn)
        got = rows.loc[rows["run_label"] == "아무이름-없는규칙", "kind"].iloc[0]
        assert got == "probe", f"못박은 종류를 안 쓴다: {got}"

        # 짐작이라면 'plan'이 나왔을 이름이다 — 폴백이 이기면 안 된다.
        assert db.classify_run_label("아무이름-없는규칙") == "plan"


# ───────────── 작업지시서 차량 필터 — 잘못된 값의 안내 (2026-09-04) ─────────────

def _fake_sheets():
    return [
        {"vehicle_id": "V01", "cluster": 1, "stops": []},
        {"vehicle_id": "V02", "cluster": 2, "stops": []},
    ]


def test_없는_차량으로_고르면_그_이름으로_빈_상태를_말한다(monkeypatch):
    """`vehicle=`이 그 실행에 없는 이름(오타·재배정·옛 QR코드)이면 sheets는
    비지만, 그렇다고 '계획이 아예 없다'고 말하면 안 된다 — 실제로는 다른
    차량 지시서가 있다. `selected_vehicle`을 무효화하지 않고 그대로 넘겨야
    orders.html이 '\"{vehicle}\"의 지시서가 없습니다 · 전체 보기'를 고를 수
    있다. 예전에는 `vehicle in names`로 걸러 None으로 떨어뜨렸다가, 화면이
    '경로가 없습니다 — 계획을 끝까지 실행하세요'라는 틀린 안내를 냈다."""
    from webapp import app as app_module, orders, store

    monkeypatch.setattr(store, "plan_targets", lambda: __import__("pandas").DataFrame())
    monkeypatch.setattr(orders, "build", lambda *a, **k: _fake_sheets())

    ctx = app_module._orders_context("obs-cmp-1520", "_15_20", "V99없는차량")
    assert ctx["selected_vehicle"] == "V99없는차량", (
        "유효하지 않다고 selected_vehicle을 지우면 안 된다 — 화면이 판단한다")
    assert ctx["sheets"] == [], "없는 차량인데 다른 차량 지시서가 섞여 나온다"
    assert ctx["sheet_names"] == ["V01", "V02"], "차량 목록 자체는 그대로 있어야 한다"


def test_없는_차량_안내_문구가_화면에_실제로_뜬다(monkeypatch):
    """위 컨텍스트가 실제로 orders.html에서 어떤 문장으로 렌더링되는지까지 본다."""
    from webapp import app as app_module, orders, store
    from webapp.app import templates

    monkeypatch.setattr(store, "plan_targets", lambda: __import__("pandas").DataFrame())
    monkeypatch.setattr(orders, "build", lambda *a, **k: _fake_sheets())

    ctx = app_module._orders_context("obs-cmp-1520", "_15_20", "V99없는차량")
    html = templates.get_template("orders.html").render(
        request=_FakeRequest(), job_labels={}, **ctx)

    assert "V99없는차량”의 지시서가 없습니다" in html or \
        "“V99없는차량”의 지시서가 없습니다" in html, \
        "없는 차량 이름으로 된 안내가 화면에 없다"
    assert "경로가 없습니다" not in html, (
        "여전히 '계획이 아예 없다'는 틀린 안내가 뜬다")


def test_있는_차량으로_고르면_그_한_장만_남는다(monkeypatch):
    """정상 경로도 같이 지킨다 — 있는 차량이면 그 한 장만 남고 이름이 그대로다."""
    from webapp import app as app_module, orders, store

    monkeypatch.setattr(store, "plan_targets", lambda: __import__("pandas").DataFrame())
    monkeypatch.setattr(orders, "build", lambda *a, **k: _fake_sheets())

    ctx = app_module._orders_context("obs-cmp-1520", "_15_20", "V01")
    assert ctx["selected_vehicle"] == "V01"
    assert [s["vehicle_id"] for s in ctx["sheets"]] == ["V01"]


class _FakeRequest:
    """템플릿의 `request.url.path`만 읽는 최소 스텁."""
    class _URL:
        path = "/orders"
    url = _URL()
    headers = {}


def test_배정_이력_쪽_넘기기가_전부에_닿는다(client, monkeypatch):
    """자르고 "N건 더 있습니다"라고만 적으면 나머지에 닿을 길이 없다.

    실행이 12건만 쌓여도 그 안내가 **늘 참**이 되던 자리다(1.26.116).
    상한을 올리는 것은 미루기이므로 쪽으로 끊어 전부에 닿게 했다.
    """
    import pandas as pd

    from webapp import app as app_module
    from webapp import store

    rows = [{"run_label": f"2026-09-{r + 1:02d} 09", "duration": "_05_10",
             "vehicle_id": f"V{i:02d}", "cluster": i, "stations": 5, "bikes": 30,
             "distance_km": 12.0, "minutes": 90.0}
            for r in range(4) for i in range(20)]

    def fake_count(vehicle_id=None, run_label=None):
        return len(rows)

    def fake_history(vehicle_id=None, run_label=None, limit=None, offset=0):
        chunk = rows[offset:offset + limit] if limit else rows
        return pd.DataFrame(chunk)

    monkeypatch.setattr(store, "vehicle_assignment_count", fake_count)
    monkeypatch.setattr(store, "vehicle_assignments", fake_history)

    per_page = app_module.ASSIGNMENTS_PER_PAGE
    pages = -(-len(rows) // per_page)
    assert pages > 1, "쪽이 하나뿐이면 이 시험이 아무것도 안 지킨다"

    seen = 0
    for page in range(1, pages + 1):
        res = client.get(f"/vehicles?page={page}")
        assert res.status_code == 200
        seen += res.text.count('data-label="회차"')
        if page < pages:
            assert f"page={page + 1}" in res.text, "다음 쪽으로 가는 길이 없다"
    assert seen == len(rows), "쪽을 다 넘겼는데 못 본 행이 있다"


def test_범위를_벗어난_쪽은_오류_대신_접힌다(client, monkeypatch):
    """주소창의 숫자를 손으로 고치는 사람이 있다. 500을 주지 않는다."""
    for page in ("0", "-3", "9999"):
        res = client.get(f"/vehicles?page={page}")
        assert res.status_code == 200


# ───────── 구역 C 검토 (1.26.146) — 화면이 말하는 범위와 실제 범위 ─────────

def test_대조_화면도_보던_차량_한_대를_그대로_본다(monkeypatch):
    """한 대만 보다가 '지금 재고와 대조하기'를 누르면 그 한 대여야 한다.

    🔴 `/orders`는 `vehicle=`을 받는데 `/orders/live`는 **안 받았다.** 그래서
    V01 한 장을 보던 기사가 대조를 누르면 14장이 통째로 돌아왔다 — 1.26.107이
    *"한 회차 17대면 세로 39,000px·휴대폰 47화면"* 이라며 서버 필터를 넣은
    바로 그 자리인데, 뒤에 붙은 `/orders/live`만 따라가지 않았다.

    덤으로 외부 API도 필요한 만큼만 부른다: 실측(2026-08-27 23 계획)에서
    V01에 필요한 대여소는 7곳인데 82곳을 조회하고 있었다(12배). 이 화면의
    주석이 *"출발 직전에 정작 필요할 때 제한에 걸릴 수 있다"* 고 경계하는
    바로 그 호출이다.
    """
    from webapp import app as app_module, orders, store
    import pandas as pd

    monkeypatch.setattr(store, "plan_targets", lambda: pd.DataFrame())
    monkeypatch.setattr(orders, "build", lambda *a, **k: _fake_sheets())

    ctx = app_module._orders_context("실행1", "_05_10", "V01")
    assert len(ctx["sheets"]) == 1, "필터가 걸린 상태를 먼저 확인한다"

    # 라우트가 vehicle을 받아 넘기는가 — 함수 시그니처로 못박는다.
    import inspect
    params = inspect.signature(app_module.orders_live).parameters
    assert "vehicle" in params, (
        "/orders/live가 vehicle을 안 받는다 — 한 대만 보다 대조를 누르면 "
        "전체로 되돌아간다")


def test_대조_링크가_보던_차량을_달고_간다():
    """컨텍스트만 맞아도 소용없다 — **화면의 링크**가 차량을 달고 가야 한다.

    사람이 누르는 것은 링크지 함수가 아니다. 필터를 켠 채 렌더링해서
    `/orders/live` 링크에 `vehicle=`이 실제로 붙는지 본다.
    """
    from webapp.app import templates

    ctx = {
        "targets": [], "run_label": "실행1", "duration": "_05_10",
        "sheets": _fake_sheets()[:1], "sheet_names": ["V01", "V02"],
        "selected_vehicle": "V01", "capacity": 10, "depot_name": "차고지",
        "upper_ratio": 0.9, "min_qty": 3,
    }
    html = templates.get_template("orders.html").render(
        request=_FakeRequest(), job_labels={}, **ctx)

    live_links = [line for line in html.splitlines() if "/orders/live" in line]
    assert live_links, "대조 링크 자체가 없다"
    assert all("vehicle=" in l for l in live_links), (
        "대조 링크가 vehicle을 안 달고 간다 — 누르면 전체 14장으로 되돌아간다:\n"
        + "\n".join(l.strip()[:100] for l in live_links if "vehicle=" not in l))


def test_예산_타일은_전체_실행을_센다(monkeypatch):
    """*"전체 실행"* 이라 적었으면 전체를 세야 한다 (1.26.146).

    🔴 `budget`을 **한 쪽**(50건)으로 계산해 놓고 화면은 "전체 실행"이라
    밝히고 있었다. 실측: 전체 86회차 중 9건 초과인데, 1쪽은 *"50회차 중
    45회차가 완료 · 5건 초과"*, 2쪽은 *"36회차 중 32회차 · 4건 초과"* 라
    답했다. 준수율도 쪽을 넘기면 90% → 89%로 바뀐다.

    바로 옆 두 타일(출동한 차량·작업량 차이)은 전체를 쓴다 — 한 줄에 선
    타일 셋의 기준이 서로 달랐다. 1.26.116이 이 표에 쪽 나눔을 넣을 때
    요약 타일을 함께 옮기지 않은 자리다.

    여기서 지키는 것은 **쪽을 넘겨도 타일이 안 흔들리는가**다.
    """
    import pandas as pd
    from webapp import app as app_module, store

    # 86건 중 9건 초과 — 실제 자료와 같은 모양으로 만든다.
    총 = 86
    초과 = 9
    분 = [130.0] * 초과 + [90.0] * (총 - 초과)
    전체 = pd.DataFrame({
        "run_label": ["실행1"] * 총,
        "vehicle_id": [f"V{i%14:02d}" for i in range(총)],
        "minutes": 분,
    })

    def fake_assignments(run_label=None, limit=None, offset=0, **kw):
        part = 전체 if limit is None else 전체.iloc[offset:offset + limit]
        return part.reset_index(drop=True)

    monkeypatch.setattr(store, "vehicle_assignments", fake_assignments)
    monkeypatch.setattr(store, "vehicle_assignment_count", lambda **kw: 총)
    monkeypatch.setattr(store, "vehicle_workload", lambda: pd.DataFrame())
    monkeypatch.setattr(store, "plan_runs", lambda: pd.DataFrame())
    monkeypatch.setattr(store, "records", lambda f: [])

    from fastapi.testclient import TestClient
    from webapp.app import app as fastapi_app

    with TestClient(fastapi_app) as c:
        본 = []
        for page in (1, 2):
            res = c.get(f"/vehicles?page={page}")
            assert res.status_code == 200
            m = re.search(r"(\d+)회차 중 (\d+)회차가", res.text)
            assert m, f"{page}쪽에서 예산 문구를 못 찾았다"
            본.append((int(m.group(1)), int(m.group(2))))

    assert 본[0] == 본[1], (
        f"쪽을 넘기니 예산 타일이 바뀐다: 1쪽 {본[0]}, 2쪽 {본[1]} — "
        "화면은 '전체 실행'이라고 적어 두었다")
    assert 본[0][0] == 총, (
        f"'전체 실행'이라 적고 {본[0][0]}회차만 세고 있다 (전체는 {총}회차)")


def test_kpi의_최신은_라벨이_아니라_시각으로_고른다():
    """`run_label`은 사람이 적는 이름이라 정렬 기준이 못 된다 (1.26.146).

    `db.load_kpi`가 `ORDER BY run_label DESC`라, 화면의 '최신'이 사전순
    맨 위였다. 같은 함정을 `_runs_newest_first()`는 이미 알고 `computed_at`을
    쓰는데 `/kpi`만 안 쓰고 있었다.

    ⚠️ **지금 자료에서는 두 순서가 우연히 같다** — 그래서 화면은 맞게
    보인다. 하지만 `2026-05-21 18`은 라벨이 5월인데 실제 계산은 08-25로,
    **라벨과 시각이 갈리는 자료가 이미 있다.** 라벨이 `sweep-`으로 시작하면
    숫자 라벨보다 위로 가서 옛 실험이 헤드라인에 오른다.

    여기서 잡는 것은 그 경우다 — 우연에 기대지 않게 만든다.
    """
    import pandas as pd
    from webapp import app as app_module

    rows = pd.DataFrame({
        "run_label": ["2026-08-27 23", "sweep-z-01"],
        "duration": ["_05_10", "_05_10"],
        # 계획이 실험보다 엿새 뒤에 돌았다
        "computed_at": ["2026-09-07 10:00:00", "2026-09-01 09:00:00"],
    })

    최신 = app_module._kpi_labels_newest_first(rows)
    assert 최신[0] == "2026-08-27 23", (
        f"'{최신[0]}'을 최신이라 골랐다 — 라벨 사전순으로는 sweep-가 위지만 "
        "실제로 나중에 돌린 것은 2026-08-27 23이다")


# ── 계획이 얼마나 오래됐는지 (1.26.156) ────────────────────────────────
# 홈이 **12일 된 계획**을 아무 말 없이 '마지막 계획'으로 띄우고 있었다
# (2026-09-08 실측: `obs-cmp-1520`, 절대 시각만 적혀 있었다). 지도는 이미
# 낡음을 말하는데(1.26.122) 계획에는 그 장치가 없었다.

def test_계획의_나이를_사람_말로_적는다():
    """절대 시각만 적으면 읽는 사람이 오늘 날짜와 빼기를 해야 한다."""
    from webapp import store

    assert store.age_note("2026-09-08 09:30", now="2026-09-08 10:00")["text"] == "방금"
    assert store.age_note("2026-09-08 04:00", now="2026-09-08 10:00")["text"] == "6시간 전"
    assert store.age_note("2026-09-01 10:00", now="2026-09-08 10:00")["text"] == "7일 전"


def test_하루가_지나면_낡았다고_판정한다():
    """경계는 실측에서 골랐다 — 하루 지나면 대여소 55~64%의 재고가 달라진다."""
    from webapp import store

    fresh = store.age_note("2026-09-08 00:00", now="2026-09-08 10:00")
    stale = store.age_note("2026-09-06 10:00", now="2026-09-08 10:00")
    assert fresh["stale"] is False
    assert stale["stale"] is True


def test_판정할_수_없으면_모른다고_한다():
    """🔴 **짐작해서 말하지 않는다.**

    시각을 못 읽었는데 '방금'이라 답하면 낡은 계획을 최신이라 말하게 된다 —
    낡음을 알리려고 만든 장치가 거짓말을 하는 셈이다. 지도 쪽과 같은 규약으로
    `None`(모름)을 내고, 화면은 아무 말도 안 한다.
    """
    from webapp import store

    assert store.age_note(None) is None
    assert store.age_note("시각 아님") is None
    # 시계가 어긋나 미래로 찍힌 경우도 짐작하지 않는다.
    assert store.age_note("2026-09-09 10:00", now="2026-09-08 10:00") is None


def _plan_kpi(computed_at: str):
    """홈이 카드를 그릴 만큼의 최소 KPI 한 줄.

    ⚠️ 실제 DB에 기대면 안 된다 — `conftest.isolate_db`가 모든 테스트를
    **빈 임시 DB**로 돌려서 카드 자체가 안 나온다(처음에 그렇게 썼다가
    "2시간 전이 없다"는 엉뚱한 실패를 봤다).
    """
    import pandas as pd
    return pd.DataFrame([{
        "run_label": "계획-A", "duration": "_10_15",
        "computed_at": computed_at,
        "bikes_moved": 100, "vehicles_used": 3,
        "total_distance_km": 12.0,
        "stockout_hours_before": 2.0, "stockout_hours_after": 1.0,
        "max_cluster_minutes": 90.0, "time_budget_minutes": 120.0,
    }])


def test_낡은_계획이면_화면이_경고하고_확인할_길을_준다(client, monkeypatch):
    """**경고만 하고 길을 안 주면 읽는 사람이 할 수 있는 게 없다.**"""
    from webapp import store

    monkeypatch.setattr(store, "kpi", lambda *a, **k: _plan_kpi("2026-08-27 23:16"))
    monkeypatch.setattr(store, "age_note",
                        lambda when, **kw: {"hours": 288.0, "text": "12일 전",
                                            "stale": True})

    body = client.get("/").text
    assert "12일 전" in body
    assert "세운 것입니다" in body, "낡았는데 경고가 없다"
    assert "지금 재고와 대조하기" in body, "확인할 길을 안 알려 준다"


def test_갓_세운_계획에는_경고를_붙이지_않는다(client, monkeypatch):
    """늘 빨간 경고는 사람이 무시하기 시작한다."""
    from webapp import store

    monkeypatch.setattr(store, "kpi", lambda *a, **k: _plan_kpi("2026-09-08 08:00"))
    monkeypatch.setattr(store, "age_note",
                        lambda when, **kw: {"hours": 2.0, "text": "2시간 전",
                                            "stale": False})

    body = client.get("/").text
    assert "2시간 전" in body
    assert "세운 것입니다" not in body, "갓 세운 계획에 낡음 경고가 붙었다"


# ── 재고 수집 현황 화면 (1.26.158) ────────────────────────────────────
# `stock_history`(62.7만 행)는 이 프로젝트의 **유일한 실측**인데 웹에 화면이
# 하나도 없었다. 그 사이 수집이 두 번 조용히 멈췄고(1.26.103·1.26.140) 둘 다
# 사람이 터미널을 열어야만 알 수 있었다.

def _collect_ctx(**over):
    """수집 화면이 쓸 값 한 벌.

    ⚠️ 실제 DB에 기대면 안 된다 — `conftest.isolate_db`가 모든 테스트를
    **빈 임시 DB**로 돌려서 화면이 빈 상태로 떨어진다(1.26.156에서 같은
    함정에 두 번째로 걸렸다).
    """
    base = {
        "window": "07:00-22:00", "interval": 10,
        "source": "등록된 작업 'PBR재고수집'",
        "rows": [{"날짜": "2026-08-31", "틱": 77, "기대": 91, "결측": 14,
                  "구간": 4, "덮은 시간": "07:00~22:00", "상태": "결측"},
                 {"날짜": "2026-09-08", "틱": 91, "기대": 91, "결측": 0,
                  "구간": 1, "덮은 시간": "07:00~22:00", "상태": "온전"}],
        "total_ticks": 168, "expected": 91, "days": 2, "stations": 1376,
        "span": "2026-08-31 ~ 2026-09-08", "last_seen": "2026-09-08",
        "intact": ["2026-09-08"], "split_days": 1,
        "stalled": {"days": 0, "stalled": False}, "error": None,
    }
    base.update(over)
    return base


def test_수집_화면이_어느_기준으로_셌는지_밝힌다(client, monkeypatch):
    """🔴 **판정을 화면에서 다시 하지 않는다.**

    같은 규칙을 두 곳에 적으면 화면과 터미널이 다른 답을 한다 — 이 저장소는
    그 사고를 이미 겪었다(1.26.55 창 · 1.26.121 "온전한 날"). 숫자는 전부
    `tools/collect_stock.py`가 내고, 화면은 **그 기준을 밝히기만** 한다.
    """
    from webapp import collect_view

    monkeypatch.setattr(collect_view, "context", lambda: _collect_ctx())
    body = client.get("/collect").text
    assert "07:00-22:00" in body
    assert "PBR재고수집" in body, "어느 기준으로 셌는지 화면이 안 밝힌다"


def test_기본값으로_떨어지면_표가_어긋날_수_있다고_말한다(client, monkeypatch):
    """창을 못 읽고 기본값으로 세면 **결측이 통째로 틀린다**(1.26.55)."""
    from webapp import collect_view

    monkeypatch.setattr(
        collect_view, "context",
        lambda: _collect_ctx(window="09:00-17:00",
                             source="기본값 — 등록된 작업을 찾지 못했습니다"))
    body = client.get("/collect").text
    assert "등록된 수집 작업을 찾지 못했습니다" in body
    assert "어긋납니다" in body, "기본값인데 표를 믿어도 되는 것처럼 보인다"


def test_오래_멈췄으면_화면이_먼저_말한다(client, monkeypatch):
    """수집이 두 번 멈췄고 둘 다 며칠 뒤에야 알았다(1.26.103·1.26.140)."""
    from webapp import collect_view

    monkeypatch.setattr(
        collect_view, "context",
        lambda: _collect_ctx(last_seen="2026-09-01",
                             stalled={"days": 7, "stalled": True}))
    body = client.get("/collect").text
    assert "7일째 새 관측이 없습니다" in body
    assert "resume" in body, "다시 켜는 방법을 안 알려 준다"


def test_주말_이틀은_멈춘_것으로_보지_않는다():
    """수집은 평일만 돈다 — 하루로 두면 **월요일 아침마다 거짓 경보**가 뜬다."""
    from webapp import collect_view

    assert collect_view._stalled_note("2026-09-04", today="2026-09-07")["days"] == 3
    assert collect_view._stalled_note("2026-09-06", today="2026-09-07")["stalled"] is False


def test_멈췄는지_판정할_수_없으면_모른다고_한다():
    """짐작해서 '정상'이라 답하면 멈춤을 알리려던 장치가 거짓말을 한다."""
    from webapp import collect_view

    assert collect_view._stalled_note("날짜 아님") is None
    assert collect_view._stalled_note("2026-09-09", today="2026-09-08") is None


def test_온전한_날이_세_가지_뜻임을_화면이_밝힌다(client, monkeypatch):
    """🔴 같은 이름이 셋을 뜻한다(1.26.153).

    밝히지 않으면 **0일**을 보고 *"쓸 자료가 없다"* 고 읽는다 — 실제로 그
    오독이 있었다(1.26.120).
    """
    from webapp import collect_view

    monkeypatch.setattr(collect_view, "context", lambda: _collect_ctx())
    body = client.get("/collect").text
    assert "dense_days()" in body
    assert "duration_complete_days()" in body
    assert "등록된 창의 100%" in body


def test_결측은_아직_판정이_아니라고_말한다(client, monkeypatch):
    """수집기가 두 환경에서 돌고 이 표는 이쪽 DB만 센다(1.26.120에 오독했다)."""
    from webapp import collect_view

    monkeypatch.setattr(collect_view, "context", lambda: _collect_ctx())
    body = client.get("/collect").text
    assert "아직 판정이 아닙니다" in body
    assert "merge_stock.py" in body


def test_구간이_여러_개면_수집_실패가_아니라_PC_꺼짐이라_말한다(client, monkeypatch):
    """둘은 대응이 완전히 다르다.

    ⚠️ `td`에 `.bad`를 붙여도 색이 안 바뀐다(규칙이 `.tile .value.bad` 등뿐).
    **글자로 말해야** 전달된다 — 1.26.107이 겪은 그 함정이다.
    """
    from webapp import collect_view

    monkeypatch.setattr(collect_view, "context", lambda: _collect_ctx())
    body = client.get("/collect").text
    assert "PC 꺼짐" in body
    assert "수집기 고장이 아닙니다" in body


def test_수집이_없어도_화면이_죽지_않고_길을_준다(client):
    """빈 상태에 **나갈 길**을 둔다(이 저장소의 빈 상태 규약).

    `conftest.isolate_db`가 빈 DB를 주므로 이것이 곧 실제 빈 상태다.
    """
    res = client.get("/collect")
    assert res.status_code == 200
    assert "collector.ps1 install" in res.text


def test_수집기를_못_읽어도_대시보드가_500이_되지_않는다(client, monkeypatch):
    """도구가 없다고 화면 전체가 죽으면 안 된다."""
    from webapp import collect_view

    monkeypatch.setattr(collect_view, "_tool", lambda: None)
    res = client.get("/collect")
    assert res.status_code == 200
    assert "읽지 못했습니다" in res.text


# ─────────── 소리로 듣는 화면 (2026-09-10) ───────────
#
# axe-core는 **속성이 있는가**를 본다. 여기서 지키는 것은 그다음 축이다 —
# 실제로 읽히는 순서와, 바뀐 것을 알려 주는가. 사람이 NVDA로 확인할 대본은
# docs/구현/스크린리더_점검.md 에 있고, 이 테스트는 그중 **기계로 지킬 수
# 있는 것**만 못박는다.

@pytest.mark.parametrize("path", [
    "/", "/run", "/guide", "/kpi", "/vehicles", "/maps", "/data",
    "/collect", "/orders",
])
def test_제목_레벨을_건너뛰지_않는다(client, path):
    """제목으로 훑는 사람에게 레벨 건너뜀은 **빈 계단**이다.

    h1 다음에 h3이 오면 "중간에 뭔가 있는데 못 들었나" 싶어진다. 눈으로
    보면 글자 크기로 위계가 보이지만, 소리에는 레벨 숫자밖에 없다.
    """
    html = client.get(path).text
    levels = [int(m) for m in re.findall(r"<h([1-6])[ >]", html)]
    assert levels.count(1) == 1, f"{path}: h1이 {levels.count(1)}개다 (정확히 하나여야 한다)"
    건너뜀 = [(a, b) for a, b in zip(levels, levels[1:]) if b > a + 1]
    assert not 건너뜀, f"{path}: 레벨을 건너뛴다 {건너뜀} — 전체 위계 {levels}"


def test_오류_화면도_제목_위계를_지킨다(client):
    """오류 화면은 평소 안 보이므로 위계가 틀어져도 눈에 안 띈다."""
    res = client.get("/view/없는파일.html", headers={"Accept": "text/html"})
    levels = [int(m) for m in re.findall(r"<h([1-6])[ >]", res.text)]
    건너뜀 = [(a, b) for a, b in zip(levels, levels[1:]) if b > a + 1]
    assert not 건너뜀, f"오류 화면이 레벨을 건너뛴다 {건너뜀} — {levels}"


def test_체크박스_묶음에_이름이_붙어_있다(client):
    """입력이 여럿인 묶음은 `for`로 이을 상대가 없다.

    role=group + 이름이 없으면 "_05_10 확인란"만 읽히고 **무엇을 고르는
    중인지**가 안 들린다. 눈으로는 위에 적힌 "시간대"가 보이지만 소리에는
    그 연결이 없다.
    """
    html = client.get("/run").text
    assert 'role="group" aria-labelledby="lbl-duration"' in html, \
        "시간대 체크박스 묶음에 그룹 이름이 없다"
    assert 'id="lbl-duration"' in html, "aria-labelledby가 가리킬 상대가 없다"
    assert 'role="group" aria-label="건너뛸 단계"' in html, \
        "생략 옵션 묶음에 그룹 이름이 없다"


def test_나중에_채워지는_상자는_알려진다(client):
    """날씨·예보는 페이지가 그려진 뒤 스크립트가 채운다.

    화면을 보는 사람에겐 상자가 나타나는 것이 곧 신호지만, 소리로 듣는
    사람에겐 라이브 리전이 없으면 **비 예보가 통째로 전달되지 않는다.**
    """
    html = client.get("/run").text
    for box in ("forecast", "weather"):
        assert re.search(rf'<div id="{box}"[^>]*role="status"', html), \
            f"#{box}가 채워져도 보조기기에 알려지지 않는다"


def test_진행_화면이_지금_어느_단계인지_말한다(client, monkeypatch):
    """3초마다 통째로 새로고침되는 화면이다.

    눈으로 보면 어디가 바뀌었는지 한눈에 알지만, 소리로 들으면 새로고침은
    **문서를 처음부터 다시 읽는 일**이다. 지금 어느 단계인지 한 줄로 짚어
    주지 않으면 진행 여부를 알 길이 없다.
    """
    class 진행중:
        id = "테스트실행"; status = "running"; is_running = True
        started_at = "2026-09-10 21:00"; finished_at = None; elapsed = 42.0
        args: list[str] = []; kind = "plan"; run_label = "테스트실행"
        returncode = None; error = None

    로그 = ("[1/3] pipeline/step0_collect/tashu_api.py\n"
           "[2/3] pipeline/step1_cluster/top_st_clustering.py\n"
           "[3/3] pipeline/step2_optimize/ilp.py\n"
           "[1/3] 실행: pipeline/step0_collect/tashu_api.py\n"
           "완료: pipeline/step0_collect/tashu_api.py\n"
           "[2/3] 실행: pipeline/step1_cluster/top_st_clustering.py\n")

    monkeypatch.setattr(jobs, "get_job", lambda jid: 진행중())
    monkeypatch.setattr(jobs, "read_log", lambda job: 로그)
    monkeypatch.setattr(jobs, "read_log_tail", lambda job, *a, **k: 로그)

    html = client.get("/runs/테스트실행").text
    m = re.search(r'<p role="status"[^>]*>(.*?)</p>', html, re.S)
    assert m, "진행 상황을 알리는 라이브 리전이 없다"
    말 = " ".join(m.group(1).split())
    assert "3단계 중 1단계 완료" in 말, f"완료 개수를 안 말한다: {말!r}"
    assert "지금은" in 말, f"현재 단계를 안 말한다: {말!r}"


# ── 성과 지표 화면의 배치 (1.26.175) ─────────────────────────────────

def _kpi_rows():
    """성과 지표 표가 그려질 만큼의 KPI 두 줄.

    ⚠️ 실제 DB에 기대면 안 된다 — `conftest.isolate_db`가 모든 테스트를 **빈
    임시 DB**로 돌려서 `{% if rows %}`가 표를 통째로 지운다. 1.26.156·158에
    이어 **세 번째**로 같은 함정에 걸렸다.
    """
    import pandas as pd
    return pd.DataFrame([
        {"run_label": "2026-08-27 23", "duration": "_10_15",
         "computed_at": "2026-08-27 23:16", "stations": 245, "clusters": 16,
         "vehicles_used": 14, "bikes_moved": 257, "avg_improvement_rate": 0.70,
         "target_met_ratio": 0.42, "reachable_ratio": 0.58, "gap_median": 6,
         "gap_max": 55, "total_distance_km": 451.0, "max_cluster_minutes": 136.0,
         "time_budget_minutes": 120.0, "time_budget_met": 0.9,
         "improvement_per_km": 1.35, "stockout_hours_before": 1.77,
         "stockout_hours_after": 0.53},
        {"run_label": "2026-08-26 23", "duration": "_05_10",
         "computed_at": "2026-08-26 23:23", "stations": 249, "clusters": 15,
         "vehicles_used": 15, "bikes_moved": 249, "avg_improvement_rate": 0.71,
         "target_met_ratio": 0.44, "reachable_ratio": 0.60, "gap_median": 5,
         "gap_max": 48, "total_distance_km": 468.0, "max_cluster_minutes": 110.0,
         "time_budget_minutes": 120.0, "time_budget_met": 1.0,
         "improvement_per_km": 1.21, "stockout_hours_before": 1.70,
         "stockout_hours_after": 0.60},
    ])


def test_글자_열이_남는_폭을_통째로_삼키지_않는다(client, monkeypatch):
    """🔴 실측에서 `실행` 600px · `회차` 318px인데 정작 읽어야 할 개선률은
    70px이었다(1400px 화면).

    숫자 열에는 `width: 1%`가 있어 제 내용만큼만 차지하는데 글자 열에는 그런
    장치가 없어, 남는 폭 900px을 둘이 나눠 가졌다. 열 이름과 값 사이가 휑하게
    벌어져 눈으로 이어지지 않는다(DESIGN.md '표'가 `width: 1%`를 둔 이유가
    바로 이것이다).
    """
    from webapp import store
    monkeypatch.setattr(store, "kpi", lambda *a, **k: _kpi_rows())

    body = client.get("/kpi").text
    assert '<th scope="col" class="tight">실행</th>' in body
    assert 'class="tight"><span class="tip" data-tip="계획한 시간대입니다' in body


def test_기본_화면에_투입과_산출이_함께_보인다(client, monkeypatch):
    """개선률만 보이면 *"얼마를 들여 얻은 값인가"* 를 알 수 없다.

    `차량`(투입)과 `옮긴 대수`(산출)는 접힌 열에 있어 **모든 열 보기**를 눌러야
    나왔다. 남는 폭을 나눠 가질 열이 필요하기도 했다 — 둘을 올려 기본 화면이
    일곱 열로 고르게 찬다.
    """
    from webapp import store
    monkeypatch.setattr(store, "kpi", lambda *a, **k: _kpi_rows())

    body = client.get("/kpi").text
    assert '<th scope="col" class="num"><span class="tip" data-tip="실제로 출동한 차량 수입니다.">차량</span></th>' in body
    assert '<th scope="col" class="num"><span class="tip" data-tip="옮긴 자전거 총 대수입니다.">옮긴 대수</span></th>' in body


def test_절이_다섯인_화면에_차례가_붙는다(client):
    """끝까지 내려가면 앞에 무엇이 있었는지 기억나지 않는다.

    ⚠️ **항목을 손으로 적지 않는다** — 스크립트가 본문 `h2`에서 읽는다.
    차례를 템플릿에 적어 두면 절을 고칠 때 반드시 한쪽이 낡는다.
    """
    body = client.get("/kpi").text
    assert "data-toc=" in body, "차례를 붙일 자리가 없다"
    # 절 이름이 템플릿에 박혀 있으면 안 된다(h2에서 읽어야 한다).
    marker = body[body.index("data-toc="):body.index("data-toc=") + 200]
    assert "추세" not in marker and "효과와 비용" not in marker
