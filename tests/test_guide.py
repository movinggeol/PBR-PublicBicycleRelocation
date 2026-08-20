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
    assert f"{int(round(VEHICLE_SPEED_KMPH))} km/h" in html
    assert f"{int(round(TIME_BUDGET_MINUTES))}분" in html


def test_guide_covers_every_run_form_field(client):
    """실행 폼의 모든 항목이 안내에 설명돼 있어야 한다.

    폼에 칸을 새로 넣고 안내를 안 고치면 여기서 걸린다.
    """
    form = client.get("/").text
    guide = client.get("/guide").text

    for label in ["실행 이름", "순수요 기간", "시간대", "요일 구분",
                  "보유 차량 대수", "회차당 투입 대수", "원천 대여 이력 CSV",
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

    for path in ("/", "/guide", "/kpi"):
        html = client.get(path).text
        for shown in re.findall(r"(?<![\w_])(\d{2}_\d{2})(?!\w)", html):
            pytest.fail(f"{path}의 시간대 예시 '{shown}'에 맨 앞 밑줄이 빠졌다")


def test_period_example_matches_the_real_format(client):
    """순수요 기간 예시는 파일명·DB에 쓰이는 '25년 11월' 표기여야 한다."""
    from project_config import period_label

    stamp_example = period_label("2025-11-01")
    for path in ("/", "/guide"):
        html = client.get(path).text
        assert stamp_example in html or re.search(r"\d{2}년 \d{2}월", html), \
            f"{path}에 기간 표기 예시가 없다"
