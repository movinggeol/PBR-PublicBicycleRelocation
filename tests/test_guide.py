"""사용 안내 페이지 테스트.

안내가 **설정값을 하드코딩하지 않는지**를 주로 본다. 차량 대수나 시간 예산을
바꿨는데 안내만 옛날 숫자로 남아 있으면, 없느니만 못한 문서가 된다.
"""
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from project_config import (
    FLEET_SIZE, TIME_BUDGET_MINUTES, VEHICLE_CAPACITY, VEHICLE_SPEED_KMPH,
    VEHICLES_PER_ROUND,
)
from webapp.app import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_guide_renders(client):
    res = client.get("/guide")

    assert res.status_code == 200
    assert "사용 안내" in res.text


def test_guide_shows_live_settings(client):
    """설정값은 코드에서 읽어 와야 한다 — 안내에 숫자를 박아 두면 곧 거짓말이 된다."""
    html = client.get("/guide").text

    assert f"{FLEET_SIZE}대" in html, "보유 차량 대수가 안 보인다"
    assert f"최대 {VEHICLES_PER_ROUND}대" in html
    assert f"{VEHICLE_CAPACITY}대" in html
    # 이동시간 문장은 스위치를 따른다 (1.26.272) — 실도로 모형이 기본이 된 뒤에도
    # 안내는 "25 km/h 직선거리"를 말하고 있었다.
    import project_config
    if project_config.USE_ROAD_MODEL:
        assert f"{int(round(project_config.ROAD_SPEED_KMPH_WEEKDAY))} km/h" in html
        assert f"고정 {round(project_config.ROAD_FIXED_SEC_WEEKDAY / 60, 1)}분" in html
        assert "실측 도로 시간의 약 76%" not in html, "꺼진 모형의 문장이 남았다"
    else:
        assert f"{int(round(VEHICLE_SPEED_KMPH))} km/h" in html
    assert f"{int(round(TIME_BUDGET_MINUTES))}분" in html


def test_guide_travel_time_sentence_follows_the_switch(client, monkeypatch):
    """`USE_ROAD_MODEL`을 양쪽으로 두고 안내가 각자 맞는 문장을 내는지 본다 (1.26.272)."""
    from webapp import app as app_module

    monkeypatch.setattr(app_module, "USE_ROAD_MODEL", True)
    on = client.get("/guide").text
    assert "실도로 계수" in on and "실측 도로 시간의 약 76%" not in on

    monkeypatch.setattr(app_module, "USE_ROAD_MODEL", False)
    off = client.get("/guide").text
    assert "실측 도로 시간의 약 76%" in off and "실도로 계수" not in off
    assert f"{int(round(VEHICLE_SPEED_KMPH))} km/h" in off


def test_expected_runtime_comes_from_past_runs(client, monkeypatch):
    """예상 소요는 사람이 적는 것이 아니라 **지난 실행의 단계별 기록**에서 나온다.

    예전 안내에는 '보통 5~10분'이 박혀 있었는데, 실측은 시간대 하나에 2분 남짓이었다.
    지금은 계수 셋(수집·전처리·시간대당)으로 넘어와, 고른 시간대 수에 맞춰
    셈한다(1.26.214) — 여기서는 **시간대 하나** 기준 값이 화면에 오르는지 본다.
    """
    from webapp import jobs

    monkeypatch.setattr(jobs, "estimate_model",
                        lambda limit=jobs.ESTIMATE_WINDOW: {
                            "수집": 18.0, "전처리": 12.0, "시간대당": 102.0, "표본": 3})

    for path in ("/guide", "/"):
        html = client.get(path).text
        assert "2.2분" in html, f"{path}에 지난 실행 기준 소요가 안 보인다"
        assert "5~10분" not in html, f"{path}에 옛 예상 소요가 남아 있다"


def test_guide_covers_every_run_form_field(client):
    """실행 폼의 모든 항목이 안내에 설명돼 있어야 한다.

    폼에 칸을 새로 넣고 안내를 안 고치면 여기서 걸린다.
    """
    form = client.get("/run").text
    guide = client.get("/guide").text

    for label in ["실행 이름", "순수요 기간", "시간대", "요일 구분",
                  "보유 차량 대수", "회차당 투입 상한", "원천 대여 이력 CSV",
                  "API 수집 생략", "EDA 생략"]:
        assert label in form, f"실행 폼에 '{label}'이 없다 — 테스트가 낡았다"
        assert label in guide, f"'{label}'이 사용 안내에 빠졌다"


def test_guide_is_reachable_from_every_page(client):
    """어느 화면에서나 안내로 갈 수 있어야 한다(상단 내비)."""
    for path in ["/", "/kpi", "/vehicles", "/maps", "/data"]:
        assert 'href="/guide"' in client.get(path).text, f"{path}에서 안내 링크가 없다"


def test_guide_does_not_light_up_a_workflow_step(client):
    """안내는 작업 흐름(실행/결과/데이터) 밖이라 어느 단계도 현재 위치가 아니다."""
    html = client.get("/guide").text

    steps = html.split('class="flow-steps"')[1].split("</div>")[0]
    assert 'aria-current="page"' not in steps


def test_input_examples_match_the_real_format(client):
    """폼·안내에 적힌 예시가 실제로 통하는 값이어야 한다.

    시간대는 맨 앞 밑줄까지가 값이다(`_10_15`). 예시를 `10_15`로 적어 두면
    그대로 입력한 사용자가 step4의 `duration_hours()`에서 크래시를 본다.
    """
    import re

    # ⚠️ **틀린 것이 없다고 통과시키면 안 된다.** 예시가 하나도 없어도 아래
    # 반복문은 그냥 지나가서, 예시를 통째로 지워도 이 검사는 조용히 통과한다.
    # 그러면 "예시가 실제로 통하는 값인지 지킨다"는 이 시험의 약속이 빈다 —
    # 검사가 공허해지는 것과 결함이 없는 것은 다르다(1.26.119에서 겪었다).
    seen = 0
    for path in ("/", "/guide", "/kpi"):
        html = client.get(path).text
        for shown in re.findall(r"(?<![\w_])(\d{2}_\d{2})(?!\w)", html):
            pytest.fail(f"{path}의 시간대 예시 '{shown}'에 맨 앞 밑줄이 빠졌다")
        seen += len(re.findall(r"(?<!\w)_\d{2}_\d{2}(?!\w)", html))

    assert seen, "세 화면 어디에도 시간대 예시가 없다 — 검사할 것이 없으면 검사가 아니다"


def test_period_example_matches_the_real_format(client):
    """순수요 기간 예시는 파일명·DB에 쓰이는 '25년 11월' 표기여야 한다."""
    from project_config import period_label

    stamp_example = period_label("2025-11-01")
    for path in ("/", "/guide"):
        html = client.get(path).text
        assert stamp_example in html or re.search(r"\d{2}년 \d{2}월", html), \
            f"{path}에 기간 표기 예시가 없다"
