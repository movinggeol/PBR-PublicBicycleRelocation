"""그래프 테스트 — 그림이 아니라 **규칙**을 검사한다.

색이 맞는지는 눈으로 볼 수 없으니, 대신 규칙을 코드로 지킨다
(규칙 전문은 docs/구현/DESIGN.md '그래프').

- 계열이 하나면 선도 하나다. 축이 둘인 그래프를 만들지 않는다.
- 색을 SVG에 박지 않는다 — CSS 변수를 상속해야 다크 모드가 저절로 갈린다.
- 값 표시는 골라서 붙인다. 점마다 숫자를 적지 않는다.
- 좌표가 뷰박스를 넘지 않는다(글자가 잘리거나 카드에 스크롤이 생긴다).
- 발산 척도는 0이 무채색이고, 양·음이 서로 다른 색조다.
"""
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from webapp import charts, kpi_view


def _bounds(svg: str) -> tuple:
    box = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
    assert box, "viewBox가 없다 — 반응형으로 줄어들지 않는다"
    width, height = int(box.group(1)), int(box.group(2))
    coords = re.findall(r'(?:x|cx)="(-?[\d.]+)"[^>]*?(?:y|cy)="(-?[\d.]+)"', svg)
    return width, height, [(float(x), float(y)) for x, y in coords]


# ---------------------------------------------------------------- 꺾은선

def test_line_draws_one_series_and_labels_only_the_endpoint():
    svg = charts.line(["A", "B", "C", "D"], [1.0, 3.0, 2.0, 5.0],
                      title="테스트", unit="분")

    assert svg.count("<polyline") == 1, "계열이 하나면 선도 하나다(축 둘 금지)"
    assert svg.count('class="viz-value"') == 1, "값은 끝점 하나만 적는다"
    assert svg.count('class="viz-dot"') == 4
    assert svg.count("data-tip=") == 4, "점마다 커서 설명이 있어야 한다"
    assert 'tabindex="0"' in svg, "키보드 초점으로도 값을 읽을 수 있어야 한다"


def test_line_skips_missing_values_without_breaking():
    """못 잰 지표는 None이다 — 0으로 넘겨짚으면 그래프가 거짓말을 한다."""
    svg = charts.line(["A", "B", "C"], [1.0, None, 3.0], title="테스트")

    assert svg.count('class="viz-dot"') == 2
    assert "<polyline" in svg


def test_line_needs_two_points():
    assert "실행이 2건 이상" in charts.line(["A"], [1.0], title="테스트")
    assert "실행이 2건 이상" in charts.line(["A", "B"], [1.0, None], title="테스트")


def test_chart_coordinates_stay_inside_the_box():
    """좌표가 뷰박스를 넘으면 글자가 잘리거나 카드에 작은 스크롤이 생긴다."""
    for svg in (
        charts.line(["A", "B", "C"], [0.0, 100.0, 50.0], title="테스트"),
        charts.scatter([{"x": 1, "y": 1, "label": "a", "tip": "a"},
                        {"x": 9, "y": 9, "label": "b", "tip": "b"}],
                       x_key="x", y_key="y", x_label="가로", y_label="세로"),
        charts.heatmap(["월", "화"], ["00", "01", "02"], [[1, -1, 0], [2, 0, None]]),
        charts.hbar(["v01", "v02", "v03"], [123.4, 45.0, 0.0],
                    title="테스트", unit="분"),
    ):
        width, height, coords = _bounds(svg)
        outside = [(x, y) for x, y in coords
                   if x < 0 or y < 0 or x > width or y > height]
        assert not outside, f"뷰박스({width}x{height}) 밖 좌표: {outside[:3]}"


def test_colors_come_from_css_variables_not_hex():
    """색을 SVG에 박으면 다크 모드에서 그대로 남는다.

    docs/구현/DESIGN.md 규칙 1: 색은 반드시 토큰으로 쓴다.
    """
    svg = (charts.line(["A", "B"], [1.0, 2.0], title="T")
           + charts.heatmap(["월"], ["00", "01"], [[5, -5]])
           + charts.scale_legend(5.0, "대")
           + charts.hbar(["A", "B"], [1.0, 2.0], title="T"))

    assert not re.search(r'#[0-9a-fA-F]{3,6}', svg), "hex 색이 SVG에 박혀 있다"
    assert "var(--viz-pos-" in svg and "var(--viz-neg-" in svg


# ---------------------------------------------------------------- 가로 막대

def test_hbar_draws_one_bar_per_label_in_the_given_order():
    """정렬은 부르는 쪽이 한다 — 여기서는 받은 순서 그대로 그려야 한다."""
    svg = charts.hbar(["v03", "v01", "v02"], [30.0, 10.0, 20.0],
                      title="테스트", unit="분")

    assert svg.count('class="viz-bar"') == 3
    assert svg.count("data-tip=") == 3, "막대마다 커서 설명이 있어야 한다"
    assert 'tabindex="0"' in svg, "키보드 초점으로도 값을 읽을 수 있어야 한다"
    # 받은 순서(v03, v01, v02) 그대로 — 정렬해 버리면 안 된다. data-tip에도
    # 같은 라벨이 나오므로 y축 눈금(viz-tick) 글자만 짚는다.
    ticks = re.findall(r'class="viz-tick"[^>]*>(v0[123])<', svg)
    assert ticks == ["v03", "v01", "v02"]


def test_hbar_labels_every_bar_since_bars_are_few_and_categorical():
    """선 그래프의 '값은 골라서'는 점이 많은 꺾은선 얘기다.

    막대는 항목 하나하나가 뜻이 달라(차량 ID) 다 적어야 읽힌다 — 점 몇 개만
    골라 적으면 나머지 차량이 몇 분 일했는지 알 수 없다.
    """
    svg = charts.hbar(["a", "b", "c"], [1.0, 2.0, 3.0], title="T", unit="분")

    assert svg.count('class="viz-value"') == 3


def test_hbar_needs_no_points_message_when_empty():
    assert "그릴 자료" in charts.hbar([], [], title="테스트")


def test_hbar_handles_all_zero_without_dividing_by_zero():
    svg = charts.hbar(["a", "b"], [0.0, 0.0], title="테스트")

    assert svg.count('class="viz-bar"') == 2


# ---------------------------------------------------------------- 발산 척도

def test_zero_is_neutral_and_the_two_arms_differ():
    """0은 무채색, 양·음은 서로 다른 색조여야 한다.

    가운데에 색이 있으면 '아무 일도 없음'이 값처럼 읽히고, 두 극이 같은 계열이면
    방향을 말할 수 없다.
    """
    assert charts._diverging_class(0, 10) == "viz-zero"
    assert charts._diverging_class(5, 10).startswith("viz-pos")
    assert charts._diverging_class(-5, 10).startswith("viz-neg")
    # 척도가 0이면(전부 0인 자료) 나눗셈을 하지 않는다
    assert charts._diverging_class(3, 0) == "viz-zero"


def test_diverging_steps_grow_with_magnitude():
    levels = [charts._diverging_class(v, 100) for v in (1, 30, 60, 100)]
    assert levels == ["viz-pos-1", "viz-pos-2", "viz-pos-3", "viz-pos-4"]
    assert charts._diverging_class(200, 100) == "viz-pos-4", "척도 밖은 마지막 단계로"


def test_heatmap_leaves_a_gap_between_cells_instead_of_a_border():
    """칸을 테두리로 나누지 않는다 — 면이 드러난 2px 틈으로 나눈다."""
    svg = charts.heatmap(["월"], ["00", "01"], [[1, -1]], cell=26)

    assert 'width="24"' in svg and 'height="24"' in svg
    assert "stroke" not in svg.split("<rect")[1], "칸에 테두리를 그렸다"


def test_scale_legend_is_always_available_for_a_heatmap():
    """색만으로 값을 읽게 두지 않는다 — 척도 범례가 함께 나와야 한다."""
    legend = charts.scale_legend(12.5, "대")

    assert "쌓임" in legend and "빠져나감" in legend
    assert legend.count("<i ") == charts._STEPS * 2 + 1, "양 팔 + 가운데"
    assert charts.scale_legend(0) == "", "그릴 값이 없으면 범례도 없다"


def test_scale_legend_says_what_to_do_not_just_the_number():
    """범례만 따로 봐도 **할 일**을 알아야 한다.

    본문은 "채워 줘야 함/빼내야 함"으로 설명하는데 범례는 그 말을 쓰지 않아,
    같은 화면이 같은 것을 두 어휘로 부르고 있었다. 범례는 히트맵 바로 밑에
    있어 본문보다 먼저 눈에 들어온다.
    """
    legend = charts.scale_legend(147.0, "대")

    assert "빼내야 함" in legend, "쌓이는 쪽에 할 일이 없다"
    assert "채워 줘야 함" in legend, "빠져나가는 쪽에 할 일이 없다"
    # 그 숫자가 합계인지 최댓값인지 — 램프의 양 끝값임을 밝힌다
    assert "147대까지" in legend, "값이 척도의 끝이라는 것을 밝히지 않았다"


def test_scale_legend_explains_the_neutral_middle():
    """0 근처를 면 색으로 물러나게 한 것이 이 척도의 설계다(DESIGN.md).

    그런데 그 뜻을 글자로 적은 곳이 없어서, 가운데가 '자료 없음'인지
    '값이 0'인지 범례만으로는 알 수 없었다.
    """
    legend = charts.scale_legend(12.5, "대")

    assert "0 근처" in legend
    assert 'class="viz-scale-note"' in legend


def test_scale_legend_keeps_the_ramp_in_one_piece():
    """색 사다리 9칸이 좁은 화면에서 두 줄로 잘리면 척도가 아니다.

    `.viz-scale`이 flex-wrap이라 칸을 낱개로 두면 가운데에서 줄이 바뀐다 —
    한 덩어리로 감싸 통째로 줄을 바꾸게 한다(실측: 390px에서 한 줄 유지).
    """
    legend = charts.scale_legend(12.5, "대")
    ramp = re.search(r'<span class="viz-ramp">(.*?)</span>', legend, re.S)

    assert ramp, "램프를 감싼 요소가 없다 — 좁은 화면에서 사다리가 잘린다"
    assert ramp.group(1).count("<i ") == charts._STEPS * 2 + 1, (
        "칸이 램프 밖에 흩어져 있다")


# ---------------------------------------------------------------- 조립

def _kpi(**over):
    base = {
        "run_label": "R1", "duration": "_05_10", "computed_at": "2026-01-01 00:00:00",
        "stations": 10, "total_distance_km": 100.0,
        "avg_improvement_rate": 0.5, "improvement_per_km": 1.0,
        "max_cluster_minutes": 100.0,
        "stockout_hours_before": 2.0, "stockout_hours_after": 1.0,
    }
    base.update(over)
    return base


def test_trend_orders_runs_oldest_first():
    """실행 라벨은 사람이 붙인 이름이라 사전순으로 줄 세우면 시간과 어긋난다."""
    rows = pd.DataFrame([
        _kpi(run_label="나중", computed_at="2026-02-01 00:00:00",
             stockout_hours_after=0.5),
        _kpi(run_label="먼저", computed_at="2026-01-01 00:00:00",
             stockout_hours_after=1.5),
    ])
    series = {s["title"]: s for s in kpi_view.trends(rows)}

    assert series["결품 시간"]["labels"] == ["먼저", "나중"]
    assert series["결품 시간"]["values"] == [1.5, 0.5]


def test_trend_drops_metrics_it_cannot_draw():
    """값이 한 점뿐인 지표는 그래프를 만들지 않는다 — 선이 안 그려진다."""
    rows = pd.DataFrame([
        _kpi(run_label="A", computed_at="2026-01-01 00:00:00", travel_time_ratio=0.7),
        _kpi(run_label="B", computed_at="2026-01-02 00:00:00", travel_time_ratio=None),
    ])
    titles = [s["title"] for s in kpi_view.trends(rows)]

    assert "결품 시간" in titles
    assert "이동 시간 비중" not in titles, "한 점짜리 지표가 그래프로 나왔다"


def test_trend_weights_rounds_by_station_count():
    """회차마다 대여소 수가 달라 단순평균을 내면 작은 회차가 같은 무게를 갖는다."""
    rows = pd.DataFrame([
        _kpi(duration="_05_10", stations=90, avg_improvement_rate=0.9),
        _kpi(duration="_10_15", stations=10, avg_improvement_rate=0.1),
        _kpi(run_label="R2", computed_at="2026-02-01 00:00:00"),
    ])
    series = {s["title"]: s for s in kpi_view.trends(rows)}

    # (0.9*90 + 0.1*10) / 100 = 0.82 -> 82%
    assert series["평균 개선률"]["values"][0] == pytest.approx(82.0)


def test_cost_benefit_prefers_stockout_and_says_so():
    rows = pd.DataFrame([_kpi(), _kpi(duration="_10_15")])
    result = kpi_view.cost_benefit(rows)

    assert result["y_label"] == "결품 감소 (시간)"
    assert [p["y"] for p in result["points"]] == [1.0, 1.0]
    assert result["points"][0]["label"] == "05_10", "시간대는 색이 아니라 글자로 구분한다"


def test_cost_benefit_falls_back_without_mixing_two_meanings():
    """결품을 못 잰 기록만 있으면 개선률로 물러서되, 축 이름을 바꿔 밝힌다."""
    rows = pd.DataFrame([
        _kpi(stockout_hours_before=None, stockout_hours_after=None),
        _kpi(duration="_10_15", stockout_hours_before=None,
             stockout_hours_after=None, avg_improvement_rate=0.4),
    ])
    result = kpi_view.cost_benefit(rows)

    assert result["y_label"] == "평균 개선률 (%)"
    assert [p["y"] for p in result["points"]] == [50.0, 40.0]


def test_cost_benefit_reports_what_it_dropped():
    """조용히 빼지 않는다 — 몇 건을 못 그렸는지 밝힌다."""
    rows = pd.DataFrame([_kpi(), _kpi(duration="_10_15", total_distance_km=None)])
    result = kpi_view.cost_benefit(rows)

    assert len(result["points"]) == 1
    assert "1건" in result["note"]


# ---------------- 그래프 카드의 배치 규약 (1.21.1) ----------------

BASE_TEMPLATE = Path(__file__).resolve().parents[1] / "webapp" / "templates" / "base.html"


def test_표는_카드가_아니라_창으로_뜬다():
    """표를 카드 안에서 펴면 그래프가 밀려나고, **둘을 나란히 볼 수 없다.**

    그래서 떠 있는 창으로 낸다(1.21.3). 창은 화면에 고정되어 카드 높이를 건드리지
    않으므로, 그래프를 보면서 표를 맞대어 볼 수 있다.
    """
    css = BASE_TEMPLATE.read_text(encoding="utf-8")
    block = re.search(r"\.table-panel\s*\{[^}]*\}", css)

    assert block, ".table-panel 규칙이 없다"
    assert "position: fixed" in block.group(0)
    assert "max-height" in block.group(0), "창이 화면보다 길어지면 안 된다"


def test_창은_세로_중앙_가로는_살짝_우측에_뜬다():
    """본문이 왼쪽이라 오른쪽에 띄우되, **화면 끝에 붙이지는 않는다**(1.21.5).

    끝에 붙이면 넓은 모니터에서 본문과 너무 멀어져 시선이 크게 튄다. 정중앙이
    `translateX(-50%)`이므로 `-10%`는 창 너비의 40%만큼 오른쪽으로 민 자리다.

    처음 자리를 `transform`으로 잡으므로 끌기 전에는 `style.left`가 비어 있다.
    그 값을 그대로 읽으면 0으로 보여 창이 왼쪽 위로 튄다 — 그래서 스크립트는
    **실제로 그려진 자리**(getBoundingClientRect)에서 좌표를 잡는다.
    """
    css = BASE_TEMPLATE.read_text(encoding="utf-8")
    block = re.search(r"\.table-panel\s*\{[^}]*\}", css)

    assert block, ".table-panel 규칙이 없다"
    rule = block.group(0)
    assert "top: 50%" in rule, "세로 중앙이 아니다"

    shift = re.search(r"transform:\s*translate\((-?[\d.]+)%,\s*-50%\)", rule)
    assert shift, "가로 위치를 translate로 잡고 있지 않다"
    # -50%가 정중앙. 그보다 큰 값이어야 오른쪽이고, 0을 넘으면 화면 밖으로 나간다.
    assert -50 < float(shift.group(1)) <= 0, "중앙보다 살짝 오른쪽이 아니다"

    # 좌표 전환은 pin() 한 곳에서만 한다 — 두 곳으로 갈리면 한쪽만 고쳐진다.
    assert "function pin(" in css
    assert "panel.style.right" in css, "right를 풀지 않으면 left가 안 먹는다"


def test_창_머리는_끌_수_있어야_한다():
    """머리를 끌어 옮길 수 있어야 그래프 옆에 놓고 볼 수 있다.

    손잡이라는 것이 **커서로 드러나야** 하고(`cursor: grab`), 끄는 동안 글자가
    선택되면 안 된다(`user-select: none`). 포인터 이벤트가 스크롤로 새지 않도록
    `touch-action: none`도 필요하다.
    """
    css = BASE_TEMPLATE.read_text(encoding="utf-8")
    block = re.search(r"\.table-panel-head\s*\{[^}]*\}", css)

    assert block, ".table-panel-head 규칙이 없다"
    for rule in ("cursor: grab", "user-select: none", "touch-action: none"):
        assert rule in block.group(0), f"끌 수 있는 머리에 {rule}이 없다"

    # 끄는 동작은 pointer 이벤트 하나로 마우스·터치를 함께 받는다.
    assert "pointerdown" in css and "setPointerCapture" in css


def test_스크롤되는_표는_머리글이_붙어_있다():
    """무슨 열인지 모르는 숫자는 읽을 수 없다."""
    css = BASE_TEMPLATE.read_text(encoding="utf-8")
    block = re.search(r"\.table-panel thead th\s*\{[^}]*\}", css)

    assert block, ".table-panel thead th 규칙이 없다"
    assert "position: sticky" in block.group(0)
    # 머리글이 비쳐 보이면 아래 행과 겹쳐 읽힌다 — 창과 같은 배경을 깔아야 한다.
    assert "var(--surface)" in block.group(0)


def test_그래프_그리드는_카드를_늘리지_않는다():
    """'표로 보기'를 편 카드 때문에 **옆 카드에 빈 칸이 생기면 안 된다.**

    CSS Grid 항목은 기본이 `stretch`라, 한 카드에서 `<details>`를 펴면 같은 행의
    다른 카드가 그 높이만큼 함께 늘어난다. 내용은 그대로인데 아래가 텅 빈 채로
    커지므로 화면이 어수선해진다. `align-items: start`가 그걸 막는다.
    """
    css = BASE_TEMPLATE.read_text(encoding="utf-8")
    block = re.search(r"\.viz-grid2\s*\{[^}]*\}", css)

    assert block, ".viz-grid2 규칙을 찾지 못했다"
    assert "align-items: start" in block.group(0), (
        "그래프 그리드에 align-items: start가 없다 — '표로 보기'를 펴면 옆 카드가 늘어난다")


# ── 세로 막대 (1.26.78) ────────────────────────────────────────────────

def test_세로막대는_순서있는_축에_쓴다():
    """`vbar`는 24시간·7요일처럼 **순서가 있고 이름이 짧은** 축에 쓴다.

    `hbar`(가로)로 24개를 그리면 세로가 600px을 넘어 한눈에 안 들어온다.
    나뉘는 기준이 흐려지면 둘 중 아무거나 쓰게 되므로 문서로 못박아 둔다.
    """
    svg = charts.vbar([str(h) for h in range(24)], [h * 10 for h in range(24)],
                      title="시간대별", unit="건")

    assert svg.count("<rect") == 24
    assert 'class="viz-bar"' in svg
    # 커서를 대면 값이 뜬다(charts.py 공통 규약).
    assert svg.count("data-tip=") == 24


def test_세로막대의_강조와_구분선은_글자_설명을_함께_낸다():
    """색만으로 뜻을 나르지 않는다(DESIGN.md 규칙 5).

    주말 막대를 붉게 칠하거나 경계선을 그으면, 그것이 무슨 뜻인지 **글자로도**
    적혀야 한다 — 색을 못 가리는 사람에게는 색이 아무 말도 하지 않는다.
    """
    svg = charts.vbar(list("월화수목금토일"), [10] * 7, title="요일별",
                      highlight=(5, 6), divider_note="붉은 막대 = 주말")

    assert svg.count('class="viz-bar warn"') == 2, "강조 막대가 둘이 아니다"
    assert "붉은 막대 = 주말" in svg, "색의 뜻을 글자로 적지 않았다"

    with_edges = charts.vbar([str(h) for h in range(24)], [10] * 24,
                             title="시간대별", dividers=(5, 10, 15, 20),
                             divider_note="붉은 선 = 회차 경계")
    assert with_edges.count('class="viz-divider"') == 4
    assert "붉은 선 = 회차 경계" in with_edges


def test_세로막대_눈금은_촘촘하면_건너뛴다():
    """24개를 다 적으면 글자가 겹친다 — 골라서 붙인다(charts.py 규약)."""
    many = charts.vbar([str(h) for h in range(24)], [10] * 24, title="24시간")
    few = charts.vbar(list("월화수목금토일"), [10] * 7, title="7요일")

    # 24개는 건너뛰고(12개), 7개는 전부 적는다.
    assert many.count('class="viz-tick"') < 24 + 3
    assert few.count('class="viz-tick"') >= 7


def test_꺾은선_x축_이름은_기본이_처음과_끝뿐이다():
    """실행 라벨(`2026-08-28 도로실측2`)은 길어서 다 적으면 겹친다.

    `all_ticks`로 켤 수 있게 열어 뒀지만 **기본은 꺼져 있어야 한다** —
    /kpi 화면이 그 기본에 기대고 있다.
    """
    labels = [f"2026-08-{d:02d} 실행분" for d in range(1, 13)]
    values = list(range(12))

    # ⚠️ 라벨은 커서 설명(data-tip)에도 들어가므로 원문을 그냥 세면 안 된다 —
    #    **축 눈금(<text class="viz-tick">)만** 센다.
    def ticks(svg):
        return re.findall(r'class="viz-tick"[^>]*>([^<]*)</text>', svg)

    default = ticks(charts.line(labels, values, title="추세"))
    assert sum(1 for t in default if t in labels) == 2,         f"기본이 처음·끝이 아니다: {[t for t in default if t in labels]}"

    every = ticks(charts.line(labels, values, title="추세",
                              all_ticks=True, width=840))
    assert sum(1 for t in every if t in labels) > 2, "all_ticks가 동작하지 않는다"


# ─────────────── 평균 기준 편차 막대 (1.26.107) ───────────────

def test_편차_막대는_평균을_기준으로_좌우로_뻗는다():
    """0 기준 막대는 *"값이 얼마인가"* 를 물을 때 옳다. 차량 형평성은
    *"고른가"* 를 묻는 것이라 기준이 **평균**이라야 한다 — 실측값
    339~402분을 0 기준으로 그렸더니 21개가 전부 같아 보였다(1.26.107)."""
    values = [402.3, 400.4, 381.5, 342.1, 339.2]
    svg = charts.deviation_hbar(["V06", "V03", "V15", "V01", "V09"], values,
                                title="시험", unit="분")
    mean = sum(values) / len(values)
    assert f"평균 {mean:.1f}분" in svg, "기준선에 평균값이 안 적혔다"
    # 부호를 붙여 적어야 어느 쪽으로 뻗은 막대인지 글자로도 읽힌다.
    assert f"{max(values) - mean:+.1f}분" in svg, "양의 편차를 부호와 함께 안 적는다"
    assert f"{min(values) - mean:+.1f}분" in svg, "음의 편차를 부호와 함께 안 적는다"
    assert "viz-baseline" in svg, "기준선을 격자로 그리고 있다"


def test_편차_막대_풍선에_절대값이_남아_있다():
    """편차만 보여 주면 '그래서 이 차는 몇 분 일했나'를 표까지 가야 안다.
    기준을 옮기되 값을 잃지는 않는다."""
    svg = charts.deviation_hbar(["V06", "V09"], [402.3, 339.2],
                                title="시험", unit="분")
    assert "V06: 402.3분" in svg, "풍선에 절대값이 없다"
    assert "평균 대비" in svg, "풍선에 편차가 없다"


def test_편차_막대의_값_글자가_항목_이름을_덮지_않는다():
    """막대가 **양쪽으로** 뻗으므로 값 글자 자리도 양쪽에 있어야 한다.
    왼쪽에 이름 몫만 잡았더니 가장 긴 음수 막대의 값이 차량 이름 위로
    올라탔다(실측 -58.8px)."""
    values = [500.0] + [100.0] * 4          # 한쪽으로 크게 치우친 최악의 경우
    svg = charts.deviation_hbar(["V01", "V02", "V03", "V04", "V05"], values,
                                title="시험", unit="분")
    import re

    # 이름은 text-anchor=end로 label_w에서 끝나고, 음수 값 글자도 end다.
    # 값 글자의 x가 이름 글자의 x보다 오른쪽이어야 겹치지 않는다.
    ticks = [float(m) for m in re.findall(
        r'<text x="([\d.]+)"[^>]*class="viz-tick" text-anchor="end"', svg)]
    values_x = [float(m) for m in re.findall(
        r'<text x="([\d.]+)"[^>]*class="viz-value" text-anchor="end"', svg)]
    assert ticks and values_x, "글자를 못 찾았다"
    assert min(values_x) > max(ticks), (
        f"음수 값 글자({min(values_x)})가 항목 이름({max(ticks)}) 자리로 넘어왔다")


def test_편차_막대의_기준을_부르는_쪽이_정할_수_있다():
    """어느 값이 평균에 들어갈지는 그리는 함수가 판단할 수 없다.

    차량 누적에서 `0분`은 "일이 없었다"가 아니라 **"아직 한 번도 안 나갔다"** 인데,
    그 0을 평균에 넣으면 기준선이 통째로 끌려 내려온다. 21대 중 4대가 놀고
    17대가 340분씩 일한 자료면 기준이 275.2분이 되고 **일한 17대가 전부
    `+64.8분`** — 오른쪽으로만 뻗는다. "누구에게 몰렸나"에 "일한 사람은 다
    평균 이상"이라고 답하는 그림이라 뜻이 없다.
    """
    values = [340.0] * 17 + [0.0] * 4

    # 기준을 안 주면 예전처럼 0까지 섞은 평균이다(그것이 옳은 자리도 있다).
    naive = charts.deviation_hbar([f"V{i:02d}" for i in range(1, 22)], values,
                                  title="시험", unit="분")
    assert "평균 275.2분" in naive
    assert "+64.8분" in naive, "0을 섞으면 일한 차량이 전부 평균 위가 된다"

    # 부른 쪽이 거른 평균을 넘기면 기준선이 그것을 따른다.
    worked = [v for v in values if v > 0]
    fixed = charts.deviation_hbar([f"V{i:02d}" for i in range(1, 22)], values,
                                  title="시험", unit="분",
                                  baseline=sum(worked) / len(worked),
                                  baseline_label="출동한 차량 평균")
    assert "출동한 차량 평균 340분" in fixed, "넘긴 기준을 안 쓴다"
    assert "+64.8분" not in fixed, "거른 평균인데도 옛 편차가 남아 있다"
    # 고르게 일한 17대는 편차가 0, 안 나간 4대만 왼쪽으로 뻗는다.
    # 편차는 풍선과 값 글자에 한 번씩 나오므로 **값 글자만** 센다.
    import re
    labels = re.findall(r'class="viz-value"[^>]*>([^<]+)<', fixed)
    assert labels.count("+0.0분") == 17
    assert labels.count("-340.0분") == 4


def test_빈_자료에도_죽지_않는다():
    assert "그릴 자료가 없습니다" in charts.deviation_hbar([], [], title="시험")


def test_빈_상태_문구를_그래프마다_따로_적지_않는다():
    """네 그래프가 **같은 문구를 각자** 적어 두고 있었다(1.26.117).

    빈 상태 문구는 화면 전체가 같아야 하는 값이라(DESIGN.md 빈 상태 규약),
    한쪽만 고치면 같은 자리에서 화면마다 다른 말이 나온다. 여기서 지키는 것은
    *"문구가 무엇인가"* 가 아니라 **"한 곳에서 나오는가"** 다.
    """
    from pathlib import Path

    src = Path(charts.__file__).read_text(encoding="utf-8")
    literal = src.count('class="empty">그릴 자료가 없습니다')
    assert literal == 0, f"빈 상태 문구를 직접 적은 곳이 {literal}군데 남아 있다"
    assert src.count("def _empty(") == 1, "빈 상태를 내는 곳은 하나여야 한다"

    # 실제로 같은 문구가 나오는지도 본다 — 함수만 있고 안 쓰면 뜻이 없다.
    outputs = {
        charts.hbar([], [], title="t"),
        charts.deviation_hbar([], [], title="t"),
        charts.vbar([], [], title="t") if hasattr(charts, "vbar") else charts._empty(),
    }
    assert len(outputs) == 1, f"그래프마다 빈 상태가 다르다: {outputs}"


def test_모든_그래프가_svg_봉투를_한_벌로_쓴다():
    """여섯 그래프가 같은 여는 태그를 **각자 적어** 두고 있었다.

    길이가 문제가 아니라 **갈라진다는 것**이 문제다 — `class="viz"`가
    스타일을, `role="img"`·`aria-label`이 접근성을 맡는데 그중 하나를
    고치려면 여섯 곳을 찾아야 하고 그러면 한둘은 빠진다.
    """
    import re
    from pathlib import Path

    code = (Path(charts.__file__)).read_text(encoding="utf-8")
    raw = re.findall(r"<svg viewBox", code)
    assert len(raw) == 1, (
        f"SVG 여는 태그를 {len(raw)}곳에서 직접 적고 있다 — _svg_open()을 써라")

    # 봉투가 접근성 속성을 실제로 달고 있는지도 함께 본다.
    tag = charts._svg_open(100, 50, "제목<&>")
    assert 'class="viz"' in tag and 'role="img"' in tag
    assert "제목&lt;&amp;&gt;" in tag, "aria-label을 이스케이프하지 않는다"
