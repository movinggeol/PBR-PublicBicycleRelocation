"""지도 범례·팔레트 시안(`experiments/diagnostic/mapviz_shared.py`) 검증.

세 지도(step1 군집·step3 경로·step4 재고 현황)의 범례를 한 함수로 모으고
군집 팔레트를 색맹 안전 8색(Okabe & Ito, 2008)으로 바꾼 시안이다. 아직
프로덕션을 대체하지 않지만, **채택 여부를 판단하려면 시안이 먼저 맞아야
한다.** 여기서 지키는 것은 둘이다:

1. **군집마다 색이 달라야 한다** — 처음 시안은 8색을 순환하며 어둡게만
   만들었는데, 검정(#000000)은 어두워지지 않아 **군집 7과 15가 똑같은
   검정**으로 나왔다(실측: RGB 거리 0). 실제 산출물이 18개 군집을 그리므로
   지도에서 두 군집이 한 색이면 범례가 설명하는 구분이 화면에 없는 것이다.
2. **색만으로 뜻을 전하지 않는다**(docs/구현/DESIGN.md) — 범례 상자에는
   배지와 함께 **글자 설명**이 반드시 있어야 한다.
"""
import importlib.util
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHARED_PATH = PROJECT_ROOT / "experiments" / "diagnostic" / "mapviz_shared.py"

# 실제 산출물의 군집 수. `top*.csv`가 18개 군집을 만든다 — 8색 팔레트를
# 두 바퀴 넘게 돌리는 값이라, 순환 규칙이 무너지면 여기서 걸린다.
CLUSTER_COUNT = 18


@pytest.fixture(scope="module")
def shared():
    spec = importlib.util.spec_from_file_location("mapviz_shared", SHARED_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rgb(hex_color: str):
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def test_군집마다_색이_다르다(shared):
    """18개 군집이 모두 다른 색이어야 한다.

    ⚠️ 검정은 어둡게 만들 수 없다 — 0에 무엇을 곱해도 0이다. 처음 시안은
    `_darken`만 써서 군집 7과 15가 둘 다 #000000이었다.
    """
    colors = [shared.cluster_color(i) for i in range(CLUSTER_COUNT)]

    duplicates = {c for c in colors if colors.count(c) > 1}
    assert not duplicates, (
        f"군집 색이 겹친다: {sorted(duplicates)} — "
        f"지도에서 두 군집이 한 색이면 범례의 구분이 화면에 없다")


def test_색이_모두_유효한_16진수다(shared):
    """folium에 넘기는 값이라 `#rrggbb` 꼴이어야 한다."""
    for i in range(CLUSTER_COUNT):
        color = shared.cluster_color(i)
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", color), f"군집 {i}의 색이 이상하다: {color}"


def test_첫_여덟은_색맹_안전색_그대로다(shared):
    """군집이 8개 이하면 Okabe-Ito 원본 색을 손대지 않고 쓴다 —
    밝기를 흔드는 순간 색맹 안전 보장이 깨진다."""
    for i in range(8):
        assert shared.cluster_color(i) == shared._OKABE_ITO[i]


def test_이웃한_순번은_눈에_띄게_다르다(shared):
    """바로 앞 순번과 색이 붙어 있으면 군집 경계가 안 보인다."""
    for i in range(1, CLUSTER_COUNT):
        a, b = _rgb(shared.cluster_color(i - 1)), _rgb(shared.cluster_color(i))
        distance = sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5
        assert distance > 60, (
            f"군집 {i-1}과 {i}의 색이 너무 가깝다(거리 {distance:.1f})")


def test_범례는_색과_글자를_함께_낸다(shared):
    """색만으로 뜻을 전하지 않는다(docs/구현/DESIGN.md).

    배지(색)만 있고 설명이 없으면, 색을 구분하지 못하는 사람에게는
    범례가 빈 상자다.
    """
    html = shared.legend_html(
        "범례 — 군집",
        [(shared.swatch_circle("#E69F00", "0"), "군집 0"),
         (shared.swatch_line("#56B4E9", dashed=True), "군집 1 경로")],
        note="원 크기는 재배치 수량입니다.")

    assert "범례 — 군집" in html
    assert "군집 0" in html and "군집 1 경로" in html, "글자 설명이 빠졌다"
    assert "원 크기는 재배치 수량입니다." in html
    # 지도 위에 떠 있어야 한다 — Leaflet 타일보다 위.
    assert "position: fixed" in html and "z-index: 9999" in html


def test_범례는_한국어_제목을_그대로_쓴다(shared):
    """화면의 나머지가 한국어다. step4는 영어 "Legend"였고 step1은 범례가
    아예 없어서, 같은 도구가 아닌 것처럼 보였다."""
    html = shared.legend_html("범례 — 재고 현황", [(shared.swatch_circle("red"), "Drop — 부족 해소")])

    assert "범례 — 재고 현황" in html
    assert "Legend" not in html
