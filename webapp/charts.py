"""그래프를 **인라인 SVG 문자열로** 만든다.

왜 SVG를 파이썬이 직접 쓰는가:

- 외부 JS 차트 라이브러리를 넣지 않는다. 이 프로젝트는 파이썬 단독이고,
  인터넷이 끊겨도 화면이 같아야 한다(글꼴을 저장소에 담은 것과 같은 이유).
- matplotlib PNG도 아니다. 이미지는 다크 모드에서 흰 판이 뜨고, 커서를 대도
  값을 읽을 수 없다.
- **인라인 SVG는 페이지의 CSS 변수를 그대로 상속한다.** 그래서 색을 `var(--blue)`로
  적으면 라이트/다크가 저절로 갈린다 — 색을 코드에 박지 않는다는 규약을 지킬 수 있다.

규칙 (docs/구현/DESIGN.md '그래프'):

- **계열이 하나면 범례를 두지 않는다.** 제목이 이미 무엇인지 말한다.
  계열을 여러 개 겹치는 대신 **작은 그래프를 여러 개** 낸다(축이 둘인 그래프 금지).
- 격자·축은 면보다 한 단 진한 실선 머리카락선이다. 점선을 쓰지 않는다.
- 값 표시는 **골라서** 붙인다. 점마다 숫자를 적지 않는다.
- 커서를 대면 값이 뜨고(`data-tip`), 키보드 초점으로도 같은 것이 뜬다.
  다만 **커서만으로 읽게 두지 않는다** — 부르는 쪽이 표 보기를 함께 낸다.
"""
from __future__ import annotations

import html
import math
from typing import Optional, Sequence


def _empty(message: str = "그릴 자료가 없습니다.") -> str:
    """자료가 없을 때 그래프 자리에 놓는 안내.

    네 그래프가 **같은 문구를 각자 적어** 두고 있었다(1.26.117). 빈 상태 문구는
    화면 전체가 같아야 하는 값이라(DESIGN.md의 빈 상태 규약) 한 곳에서 낸다 —
    한쪽만 고치면 같은 자리에서 화면마다 다른 말이 나온다.
    """
    return f'<p class="empty">{html.escape(message)}</p>'


def _svg_open(width: float, height: float, label: str) -> str:
    """모든 그래프가 같이 쓰는 SVG 여는 태그.

    여섯 그래프가 **같은 문자열을 각자 적어** 두고 있었다. 문제는 길이가
    아니라 **갈라진다는 것**이다 — `class="viz"`가 스타일을, `role="img"`와
    `aria-label`이 접근성을 맡는데, 그중 하나를 고치려면 여섯 곳을 찾아
    고쳐야 하고 그러면 한둘은 빠진다. 이 저장소가 이미 겪은 자리다
    (`mapviz.py`가 생기기 전 세 지도의 범례가 그렇게 갈라져 있었다).

    ⚠️ `aria-label`은 **부르는 쪽이 완성해서 넘긴다.** 대부분은 제목이지만
    산점도는 *"y 대비 x"*, 히트맵은 *"요일과 시간대별 값"* 이라 제목만으로는
    안 된다 — 여기서 제목을 받아 조립하면 그 둘이 다시 예외가 된다.
    """
    return (f'<svg viewBox="0 0 {width} {height}" class="viz" role="img" '
            f'aria-label="{html.escape(label)}">')


def _tip(title: str, value: str, sub: str = "") -> str:
    """커서 풍선에 **구조**를 준다 — 제목 / 값 / 덧말 (1.26.175).

    예전에는 `data-tip="2026-08-27 23: 0.51h"` 한 줄이었다. 어두운 상자에
    그대로 흘려 놓으니 **무엇이 이름이고 무엇이 값인지 구분이 없어** 비정형
    문자열처럼 보였다 — 콜론 하나가 그 둘을 가르는 유일한 표시였고, 실행
    라벨에도 콜론이 들어갈 수 있다.

    ⚠️ **이 저장소의 모든 풍선이 쓰는 형식이다.** 그래프에만 넣으면 같은
    화면에서 풍선이 두 모양이 된다 — `base.html`의 `#tipbox` 하나가 세
    속성을 읽어 조립하므로, 표·타일·내비 어디에 붙여도 같은 모양이 나온다.

    `data-tip`만 주면 **예전 그대로** 한 줄짜리 설명이다(내비 링크 등 설명문이
    들어가는 자리). 제목을 주면 값이 커지는 수치용 배치로 바뀐다.
    """
    out = (f'data-tip-title="{html.escape(str(title))}" '
           f'data-tip="{html.escape(str(value))}"')
    if sub:
        out += f' data-tip-sub="{html.escape(str(sub))}"'
    return out


def _fmt(value: float, digits: int = 1) -> str:
    """축·표시용 숫자. 정수로 떨어지면 소수점을 붙이지 않는다."""
    if value == int(value):
        return str(int(value))
    return f"{value:.{digits}f}"


def _nice_bounds(low: float, high: float) -> tuple:
    """축 눈금이 어중간한 숫자로 끝나지 않게 위아래를 넉넉한 값으로 민다."""
    if low == high:
        return (low - 1, high + 1) if low else (0.0, 1.0)
    span = high - low
    step = 10 ** math.floor(math.log10(span))
    for factor in (1, 2, 2.5, 5, 10):
        if span / (step * factor) <= 4:
            step *= factor
            break
    return math.floor(low / step) * step, math.ceil(high / step) * step


def _text(x: float, y: float, body: str, cls: str = "viz-label",
          anchor: str = "middle") -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}" '
            f'text-anchor="{anchor}">{html.escape(str(body))}</text>')


def line(labels: Sequence[str], values: Sequence[Optional[float]], *,
         title: str, unit: str = "", width: int = 360, height: int = 150,
         lower_is_better: bool = False, all_ticks: bool = False) -> str:
    """계열 하나짜리 꺾은선. 실행 순서에 따른 변화를 본다.

    labels: x축 이름(실행 라벨). values: 값(None은 건너뛴다 — 못 잰 지표).
    lower_is_better: 마지막 값이 좋아졌는지 판정하는 방향만 뒤집는다.
    all_ticks: x축 이름을 **전부** 적는다. 기본은 처음·끝만인데, 그건 실행
      라벨(`2026-08-28 도로실측2`)이 길어 다 적으면 겹치기 때문이다.
      `2025-01`처럼 짧고 규칙적인 이름이면 켜서 전부 보여 준다.
    """
    pairs = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(pairs) < 2:
        return ('<p class="empty">그래프를 그리려면 실행이 2건 이상 필요합니다.</p>')

    pad_l, pad_r, pad_t, pad_b = 46, 14, 24, 26
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    ys = [v for _, v in pairs]
    low, high = _nice_bounds(min(ys), max(ys))
    span = high - low or 1

    def px(i):
        return pad_l + (plot_w * i / max(1, len(values) - 1))

    def py(v):
        return pad_t + plot_h - plot_h * (v - low) / span

    parts = [_svg_open(width, height, title)]

    # 격자 3줄 + y 눈금. 눈금 글자는 세로로 줄이 맞아야 해서 tabular-nums를 쓴다.
    for k in range(3):
        v = low + span * k / 2
        y = py(v)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" '
                     f'y2="{y:.1f}" class="viz-grid"/>')
        parts.append(_text(pad_l - 6, y + 4, _fmt(v), "viz-tick", "end"))

    parts.append(f'<polyline class="viz-line" points="'
                 + " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in pairs) + '"/>')

    # ── x축 밴드 — **y를 정확히 맞히지 않아도 된다** (1.26.175)
    #
    # 예전에는 점 둘레 11px 원만 히트 영역이라, 값을 읽으려면 곡선 위 그 한
    # 점을 **위아래로도 정확히** 짚어야 했다. 그래프를 읽는 사람이 아는 것은
    # x(어느 실행인가)뿐인데 y까지 맞히라고 요구한 셈이다.
    #
    # 이제 x 슬롯 하나를 **판 높이 전체로** 세운 띠가 히트 영역이다. 그 세로
    # 줄 어디에 커서를 둬도 그 실행의 값이 뜬다. 띠는 투명하고, 대신 안내선과
    # 후광이 켜져 **지금 읽고 있는 x가 어디인지** 보인다.
    slot = plot_w / max(1, len(values) - 1)
    for i, v in pairs:
        left = max(pad_l, px(i) - slot / 2)
        right = min(width - pad_r, px(i) + slot / 2)
        parts.append(
            f'<g class="viz-band" tabindex="0" '
            f'{_tip(labels[i], f"{_fmt(v, 2)}{unit}", title)}>'
            f'<rect x="{left:.1f}" y="{pad_t}" width="{max(1.0, right - left):.1f}" '
            f'height="{plot_h}" class="viz-band-hit"/>'
            f'<line x1="{px(i):.1f}" y1="{pad_t}" x2="{px(i):.1f}" '
            f'y2="{pad_t + plot_h}" class="viz-guide"/>'
            f'<circle cx="{px(i):.1f}" cy="{py(v):.1f}" r="8" class="viz-halo"/>'
            f'<circle cx="{px(i):.1f}" cy="{py(v):.1f}" r="4" class="viz-dot"/>'
            f'</g>')

    # 값 표시는 **끝점 하나만**. 점마다 숫자를 적으면 읽히지 않는다.
    last_i, last_v = pairs[-1]
    parts.append(_text(px(last_i), py(last_v) - 10,
                       f"{_fmt(last_v, 2)}{unit}", "viz-value",
                       "end" if last_i == len(values) - 1 else "middle"))

    # x축은 처음과 끝 이름만. 실행 라벨은 길어서 다 적으면 겹친다.
    if all_ticks:
        # 짧은 이름이면 전부 적되, 칸이 좁으면 건너뛰며 적는다(겹침 방지).
        step = max(1, math.ceil(len(labels) * 52 / max(1, plot_w)))
        for i, label in enumerate(labels):
            if i % step:
                continue
            anchor = "start" if i == 0 else ("end" if i == len(labels) - 1 else "middle")
            parts.append(_text(px(i), height - 8, str(label), "viz-tick", anchor))
    else:
        parts.append(_text(pad_l, height - 8, labels[pairs[0][0]], "viz-tick", "start"))
        if pairs[-1][0] != pairs[0][0]:
            parts.append(_text(width - pad_r, height - 8, labels[pairs[-1][0]],
                               "viz-tick", "end"))

    parts.append("</svg>")
    return "".join(parts)


def hbar(labels: Sequence[str], values: Sequence[float], *,
         title: str, unit: str = "", width: int = 640,
         bar_h: int = 20, gap: int = 8) -> str:
    """가로 막대. 항목별 누적값을 위아래로 늘어놓고 한눈에 견준다.

    표는 이미 있는데 행이 많아 형평성(누가 몰렸는지)이 한눈에 안 들어올 때
    쓴다 — 정렬은 부르는 쪽이 하고, 여기서는 받은 순서 그대로 그린다.
    labels/values 길이가 같아야 한다.
    """
    n = len(labels)
    if n == 0:
        return _empty()

    pad_l, pad_r, pad_t, pad_b = 60, 56, 4, 4
    row_h = bar_h + gap
    plot_w = width - pad_l - pad_r
    height = pad_t + row_h * n + pad_b

    _, high = _nice_bounds(0, max(values, default=0) or 1)
    span = high or 1

    parts = [_svg_open(width, height, title)]

    # 세로 격자 두 줄(중간·끝) — 값이 얼마나 찼는지 눈대중할 기준선.
    for k in (1, 2):
        x = pad_l + plot_w * k / 2
        parts.append(f'<line x1="{x:.1f}" y1="{pad_t}" x2="{x:.1f}" '
                     f'y2="{height - pad_b}" class="viz-grid"/>')

    for i, (label, v) in enumerate(zip(labels, values)):
        y = pad_t + row_h * i
        cy = y + bar_h / 2
        w = max(1.5, plot_w * v / span)
        parts.append(_text(pad_l - 8, cy + 4, label, "viz-tick", "end"))
        parts.append(
            f'<rect x="{pad_l}" y="{y:.1f}" width="{w:.1f}" height="{bar_h}" '
            f'rx="4" class="viz-bar" tabindex="0" '
            f'{_tip(label, f"{_fmt(v, 1)}{unit}", title)}/>')
        parts.append(_text(pad_l + w + 6, cy + 4, f"{_fmt(v, 1)}{unit}",
                           "viz-value", "start"))

    parts.append("</svg>")
    return "".join(parts)


def deviation_hbar(labels: Sequence[str], values: Sequence[float], *,
                   title: str, unit: str = "", baseline_label: str = "평균",
                   baseline: Optional[float] = None,
                   width: int = 640, bar_h: int = 18, gap: int = 7) -> str:
    """**평균에서 얼마나 떨어졌는가**를 좌우로 그리는 가로 막대.

    ## 왜 따로 있나 — `hbar`가 답을 못 준 자리

    차량별 누적 작업 시간을 `hbar`로 그렸더니 21개 막대가 **전부 같아 보였다**
    (1.26.107). 값이 339.2~402.3분이라 폭이 16%뿐인데 0에서 시작하는 막대는
    그 차이를 막대 길이의 16%로 밖에 못 그린다. 그래프가 답해야 할 질문이
    *"한 대에 일이 몰렸나"* 인데, 1000px를 쓰고도 바로 위 타일이 이미 적어 둔
    "차량 간 차이 63.1분"보다 못 알려 줬다.

    ⚠️ **0에서 시작하는 막대가 언제나 정직한 것은 아니다.** 0 기준은 *"값이
    얼마인가"* 를 물을 때 옳다. *"고른가"* 를 물을 때는 **고른 상태(평균)가
    기준**이라야 하고, 그때 0 기준은 차이를 숨긴다. 축을 잘라 과장하는 것과는
    반대 방향의 실수다 — 여기서는 축을 자르는 것이 아니라 **기준을 옮긴다**.

    막대 길이는 `값 − 평균`이고 눈금도 그렇게 읽는다. 절대값은 커서 풍선과
    바로 아래 표에 그대로 있다 — 어느 쪽도 잃지 않는다.

    ## `baseline` — 기준을 부르는 쪽이 정한다

    ⚠️ **어느 값이 기준에 들어갈지는 이 함수가 판단할 수 없다.** 차량 누적
    시간에서 `0분`은 "일이 없었다"가 아니라 **"아직 한 번도 안 나갔다"** 인데,
    그 0을 평균에 넣으면 기준선이 통째로 끌려 내려온다. 21대 중 4대가 놀고
    17대가 340분씩 일한 자료로 재 보면 기준선이 *"평균 275.2분"* 으로 뜨고
    **일한 차량 17대가 전부 `+64.8분`** — 오른쪽으로만 뻗는다. 물은 것은
    "누구에게 몰렸나"인데 그림은 "일한 사람은 다 평균 이상"이라고 답한다.

    그래서 기준을 인자로 뺐다. 주지 않으면 받은 값 전체의 평균을 쓴다(그것이
    옳은 자리도 있다). 걸러야 할 값이 있는 쪽은 **거른 뒤의 평균을 직접 넘긴다**
    — 막대는 여전히 전부 그리되 기준만 옮긴다.
    """
    n = len(labels)
    if n == 0:
        return _empty()

    mean = sum(values) / n if baseline is None else float(baseline)
    # ⚠️ **표시 자리에서 반올림한 뒤 0을 붙인다.** 값이 다 같아도 평균에는
    # 부동소수점 오차가 남아(예: 0.1 셋의 편차가 -1.4e-17) `{:+.1f}`가
    # `-0.0분`으로 찍힌다 — 평균과 똑같은 차를 "평균보다 적게 일했다"고
    # 말하는 셈이고, 하필 이 그래프의 존재 이유가 *"고른가"* 다(1.26.113).
    devs = [round(v - mean, 1) + 0.0 for v in values]
    # 좌우 대칭이라야 "왼쪽이 더 길다"가 눈대중으로 성립한다.
    reach = max((abs(d) for d in devs), default=0) or 1

    # 막대가 **양쪽으로** 뻗으므로 값 글자 자리도 양쪽에 있어야 한다. 왼쪽에
    # 항목 이름 몫(label_w)만 잡았더니 가장 긴 음수 막대의 "-42.3분"이 차량
    # 이름 위로 올라탔다(실측). 왼쪽 = 이름 + 값, 오른쪽 = 값.
    label_w, value_w = 60, 62
    pad_l, pad_r, pad_t, pad_b = label_w + value_w, value_w, 18, 6
    row_h = bar_h + gap
    plot_w = width - pad_l - pad_r
    half = plot_w / 2
    zero_x = pad_l + half
    height = pad_t + row_h * n + pad_b

    parts = [_svg_open(width, height, title)]

    # 기준선(평균). 이 그래프에서 유일하게 뜻이 있는 세로선이라 격자를 더 두지
    # 않는다 — 선이 여럿이면 어느 것이 기준인지 흐려진다.
    parts.append(f'<line x1="{zero_x:.1f}" y1="{pad_t - 6}" x2="{zero_x:.1f}" '
                 f'y2="{height - pad_b}" class="viz-baseline"/>')
    parts.append(_text(zero_x, pad_t - 9,
                       f"{baseline_label} {_fmt(mean, 1)}{unit}", "viz-tick", "middle"))

    for i, (label, value, dev) in enumerate(zip(labels, values, devs)):
        y = pad_t + row_h * i
        cy = y + bar_h / 2
        w = max(1.5, half * abs(dev) / reach)
        x = zero_x if dev >= 0 else zero_x - w
        parts.append(_text(label_w, cy + 4, label, "viz-tick", "end"))
        # 풍선에는 **절대값과 편차를 함께** 싣는다. 편차만 보여 주면
        # "그래서 이 차는 몇 분 일했나"를 표까지 가야 알 수 있다.
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{bar_h}" '
            f'rx="3" class="viz-bar" tabindex="0" '
            f'{_tip(label, f"{_fmt(value, 1)}{unit}", f"{baseline_label} 대비 {dev:+.1f}{unit}")}/>')
        # 값 글자는 막대 바깥, 뻗어 나간 쪽에 붙인다.
        if dev >= 0:
            parts.append(_text(x + w + 6, cy + 4, f"{dev:+.1f}{unit}",
                               "viz-value", "start"))
        else:
            parts.append(_text(x - 6, cy + 4, f"{dev:+.1f}{unit}",
                               "viz-value", "end"))

    parts.append("</svg>")
    return "".join(parts)


def vbar(labels: Sequence[str], values: Sequence[float], *,
         title: str, unit: str = "", width: int = 640, height: int = 220,
         highlight: Optional[Sequence[int]] = None,
         dividers: Optional[Sequence[float]] = None,
         divider_note: str = "") -> str:
    """세로 막대. **가로축이 시간·요일처럼 순서가 있는 것**에 쓴다.

    `hbar`와 나뉘는 기준은 축의 성격이다 — 항목 이름이 길고 순서가 없으면
    가로(`hbar`), 24시간·7요일처럼 **순서가 있고 이름이 짧으면** 세로다.
    24개를 가로로 쌓으면 세로가 600px을 넘어 한눈에 안 들어온다.

    highlight: 다르게 칠할 막대의 인덱스(예: 주말). **색만으로 뜻을 나르지
      않으므로** 부르는 쪽이 `divider_note` 같은 글자 설명을 함께 낸다.
    dividers: 세로 구분선을 그을 x 위치(막대 인덱스 기준, 0.5 단위로 경계).
    """
    n = len(labels)
    if n == 0:
        return _empty()

    pad_l, pad_r, pad_t, pad_b = 46, 12, 22, 26
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    _, high = _nice_bounds(0, max(values, default=0) or 1)
    span = high or 1
    slot = plot_w / n
    bar_w = max(2.0, slot * .72)
    marked = set(highlight or ())

    parts = [_svg_open(width, height, title)]

    # 가로 격자 셋(0·중간·꼭대기)과 왼쪽 눈금.
    for k in range(3):
        value = span * k / 2
        y = pad_t + plot_h - plot_h * k / 2
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" '
                     f'y2="{y:.1f}" class="viz-grid"/>')
        parts.append(_text(pad_l - 6, y + 4, _fmt(value, 0), "viz-tick", "end"))

    for i, (label, v) in enumerate(zip(labels, values)):
        x = pad_l + slot * i + (slot - bar_w) / 2
        h = max(0.0, plot_h * v / span)
        y = pad_t + plot_h - h
        cls = "viz-bar warn" if i in marked else "viz-bar"
        # 막대가 짧으면(값이 0 근처) 짚을 데가 거의 없다. 기둥 하나를 **판
        # 높이 전체로** 세워 그 칸 어디서나 값이 뜨게 한다(line과 같은 규약).
        parts.append(
            f'<g class="viz-band" tabindex="0" '
            f'{_tip(label, f"{_fmt(v, 1)}{unit}", title)}>'
            f'<rect x="{pad_l + slot * i:.1f}" y="{pad_t}" width="{slot:.1f}" '
            f'height="{plot_h}" class="viz-band-hit"/>'
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{h:.1f}" '
            f'rx="2" class="{cls}"/>'
            f'</g>')

    # x축 눈금은 **골라서** 붙인다 — 24개를 다 적으면 글자가 겹친다.
    step = 1 if n <= 12 else 2
    for i, label in enumerate(labels):
        if i % step:
            continue
        cx = pad_l + slot * i + slot / 2
        parts.append(_text(cx, height - pad_b + 14, str(label), "viz-tick", "middle"))

    for edge in dividers or ():
        x = pad_l + slot * edge
        parts.append(f'<line x1="{x:.1f}" y1="{pad_t}" x2="{x:.1f}" '
                     f'y2="{pad_t + plot_h}" class="viz-divider"/>')
    if divider_note:
        parts.append(_text(width - pad_r, pad_t - 8, divider_note,
                           "viz-axis note", "end"))

    parts.append("</svg>")
    return "".join(parts)


def _overlaps(a: tuple, b: tuple) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _label_spot(label: str, cx: float, cy: float, taken: list,
                width: int, pad_l: int, pad_r: int):
    """점 옆 글자를 **비어 있는 자리에** 놓는다. 없으면 놓지 않는다.

    ⚠️ 예전에는 언제나 점 위 11px에 붙였다. 그래서 값이 가까운 두 점의 글자가
    겹쳐 `20_05`가 `0_05`로 잘려 보였고, 한쪽 글자가 다른 점 위에 올라탔다
    (실측 1.26.107). 커서를 못 대는 인쇄·터치에서는 읽을 방법이 없었다.

    위 → 아래 → 오른쪽 → 왼쪽 순으로 빈자리를 찾는다. 넷 다 막혔으면
    **글자를 포기한다** — 겹쳐 찍는 것보다 없는 편이 낫다. 값은 커서 풍선과
    표에 그대로 있다(부르는 쪽이 표 보기를 함께 낸다).
    """
    # 글꼴이 11px 굵은 글씨다. 폭은 정확히 잴 수 없으므로 넉넉히 잡는다 —
    # 좁게 잡으면 '안 겹친다'고 판단해 놓고 실제로는 겹친다.
    w, h = len(str(label)) * 6.6 + 2, 12
    for dx, dy, anchor in ((0, -11, "middle"), (0, 15, "middle"),
                           (9, 4, "start"), (-9, 4, "end")):
        lx, ly = cx + dx, cy + dy
        left = lx - (w / 2 if anchor == "middle" else (w if anchor == "end" else 0))
        box = (left, ly - h + 3, left + w, ly + 3)
        if box[0] < pad_l - 4 or box[2] > width - pad_r + 4:
            continue                       # 그림 밖으로 나가면 잘린다
        if any(_overlaps(box, other) for other in taken):
            continue
        return lx, ly, anchor, box
    return None


def scatter(points: Sequence[dict], *, x_key: str, y_key: str,
            x_label: str, y_label: str, width: int = 640,
            height: int = 300) -> str:
    """효과·비용 산점도. 점 하나가 실행·회차 하나다.

    points: [{x_key, y_key, label, tip}]  — label은 화면에 붙는 짧은 이름,
    tip은 커서를 댔을 때 나오는 전체 설명.

    **한 색만 쓴다.** 회차를 색으로 나누면 강조색이 여러 개가 되고, 색이 곧 뜻인
    상태가 된다. 회차 구분은 점 옆 글자와 커서 설명이 맡는다.
    """
    usable = [p for p in points
              if p.get(x_key) is not None and p.get(y_key) is not None]
    if len(usable) < 2:
        return '<p class="empty">그래프를 그리려면 점이 2개 이상 필요합니다.</p>'

    pad_l, pad_r, pad_t, pad_b = 52, 18, 18, 40
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    x_low, x_high = _nice_bounds(min(p[x_key] for p in usable),
                                max(p[x_key] for p in usable))
    y_low, y_high = _nice_bounds(min(p[y_key] for p in usable),
                                 max(p[y_key] for p in usable))
    x_span = x_high - x_low or 1
    y_span = y_high - y_low or 1

    def px(v):
        return pad_l + plot_w * (v - x_low) / x_span

    def py(v):
        return pad_t + plot_h - plot_h * (v - y_low) / y_span

    parts = [_svg_open(width, height, f"{y_label} 대비 {x_label}")]

    for k in range(3):
        y = py(y_low + y_span * k / 2)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" '
                     f'y2="{y:.1f}" class="viz-grid"/>')
        parts.append(_text(pad_l - 8, y + 4, _fmt(y_low + y_span * k / 2),
                           "viz-tick", "end"))
    for k in range(3):
        v = x_low + x_span * k / 2
        parts.append(_text(px(v), height - 20, _fmt(v), "viz-tick"))

    placed = [(px(p[x_key]) - 7, py(p[y_key]) - 7,
               px(p[x_key]) + 7, py(p[y_key]) + 7) for p in usable]

    for p in usable:
        cx, cy = px(p[x_key]), py(p[y_key])
        parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="13" class="viz-hit" '
                     f'tabindex="0" '
                     f'{_tip(p.get("label") or x_label, f"{_fmt(p[y_key], 2)}", p.get("tip", ""))}/>')
        # 겹치는 점은 면 색 링으로 떼어 놓는다(테두리를 그리는 게 아니다).
        parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5" class="viz-dot ring"/>')
        if p.get("label"):
            spot = _label_spot(p["label"], cx, cy, placed, width, pad_l, pad_r)
            if spot:
                lx, ly, anchor, box = spot
                placed.append(box)
                parts.append(_text(lx, ly, p["label"], "viz-value", anchor))

    parts.append(_text(pad_l + plot_w / 2, height - 4, x_label, "viz-axis"))
    parts.append(f'<text x="12" y="{pad_t + plot_h / 2:.1f}" class="viz-axis" '
                 f'text-anchor="middle" transform="rotate(-90 12 '
                 f'{pad_t + plot_h / 2:.1f})">{html.escape(y_label)}</text>')
    parts.append("</svg>")
    return "".join(parts)


# 발산 척도 단계. 0 근처가 면 색으로 물러나고, 클수록 진해진다.
_STEPS = 4


def _diverging_class(value: float, scale: float) -> str:
    """값 → 색 토큰 이름. scale은 양쪽 팔의 최대 절댓값."""
    if scale <= 0 or value == 0:
        return "viz-zero"
    level = min(_STEPS, max(1, math.ceil(abs(value) / scale * _STEPS)))
    return f"viz-{'pos' if value > 0 else 'neg'}-{level}"


def heatmap(rows: Sequence[str], cols: Sequence[str],
            matrix: Sequence[Sequence[Optional[float]]], *,
            unit: str = "", cell: int = 26, width: Optional[int] = None) -> str:
    """요일 × 시간 같은 격자를 **발산 척도**로 칠한다.

    순수요는 부호가 있는 값이다 — 양수면 자전거가 빠져나가는 곳, 음수면 쌓이는 곳.
    한 색조의 농담으로는 방향을 말할 수 없으므로 따뜻한 쪽 / 찬 쪽으로 나누고
    가운데를 무채색으로 둔다.

    **색만으로 값을 읽게 두지 않는다** — 척도 범례를 함께 내고, 부르는 쪽이
    표 보기를 붙인다.
    """
    if not rows or not cols:
        return _empty()

    pad_l, pad_t, pad_b = 42, 20, 8
    grid_w = cell * len(cols)
    total_w = width or (pad_l + grid_w + 8)
    total_h = pad_t + cell * len(rows) + pad_b

    values = [v for row in matrix for v in row if v is not None]
    scale = max((abs(v) for v in values), default=0)

    parts = [_svg_open(total_w, total_h, "요일과 시간대별 값")]

    for c, name in enumerate(cols):
        # 칸이 좁으면 두 칸에 한 번만 적는다 — 다 적으면 글자가 겹친다.
        if len(cols) <= 12 or c % 2 == 0:
            parts.append(_text(pad_l + cell * c + cell / 2, pad_t - 6, name, "viz-tick"))

    for r, row_name in enumerate(rows):
        y = pad_t + cell * r
        parts.append(_text(pad_l - 8, y + cell / 2 + 4, row_name, "viz-tick", "end"))
        for c, value in enumerate(matrix[r]):
            x = pad_l + cell * c
            if value is None:
                continue
            token = _diverging_class(value, scale)
            # 방향(쌓임/빠져나감)을 **글자로** 함께 준다 — 색만으로 읽게 두지 않는다.
            way = "빠져나감 · 채워 줘야 함" if value > 0 else (
                "쌓임 · 빼내야 함" if value < 0 else "손댈 필요 없음")
            tip = _tip(f"{row_name} {cols[c]}", f"{_fmt(value, 2)}{unit}", way)
            # 칸 사이 2px은 테두리가 아니라 **면이 드러난 틈**이다.
            parts.append(
                f'<rect x="{x + 1}" y="{y + 1}" width="{cell - 2}" '
                f'height="{cell - 2}" rx="3" fill="var(--{token})" '
                f'tabindex="0" {tip}/>')

    parts.append("</svg>")
    return "".join(parts)


def scale_legend(scale: float, unit: str = "") -> str:
    """발산 척도 범례. 히트맵과 반드시 함께 낸다.

    양 끝에 **무엇을 해야 하는지**(빼내야 함 / 채워 줘야 함)를 값과 함께
    적는다. 예전에는 `쌓임 −147대` / `빠져나감 +147대`뿐이라 세 가지가
    빠져 있었다:

    - **할 일이 없었다.** 본문은 "채워 줘야 함/빼내야 함"으로 설명하는데
      범례는 그 말을 쓰지 않아, 범례만 따로 보면 무엇을 하라는 건지 몰랐다.
      같은 화면에서 같은 것을 두 어휘로 부르지 않는다.
    - **그 숫자가 무엇인지 몰랐다.** 합계인지 최댓값인지 알 수 없었다 —
      램프의 양 끝값이므로 `…까지`를 붙여 밝힌다.
    - **가운데 무채색을 아무도 설명하지 않았다.** 0 근처를 면 색으로
      물러나게 한 것이 이 척도의 설계인데(DESIGN.md '발산 척도'),
      그 뜻을 글자로 적은 곳이 없었다.

    램프는 `.viz-ramp`로 한 번 더 감싼다 — 좁은 화면에서 flex가 줄을 바꿀 때
    9칸 사이가 끊기면 색 사다리가 두 줄로 잘려 읽히지 않는다.
    """
    if scale <= 0:
        return ""
    chips = []
    for level in range(_STEPS, 0, -1):
        chips.append(f'<i style="background:var(--viz-neg-{level})"></i>')
    chips.append('<i style="background:var(--viz-zero)"></i>')
    for level in range(1, _STEPS + 1):
        chips.append(f'<i style="background:var(--viz-pos-{level})"></i>')
    return (
        '<div class="viz-scale">'
        f'<span><b>쌓임</b> −{_fmt(scale)}{unit}까지 · 빼내야 함</span>'
        f'<span class="viz-ramp">{"".join(chips)}</span>'
        f'<span><b>빠져나감</b> +{_fmt(scale)}{unit}까지 · 채워 줘야 함</span>'
        '</div>'
        '<p class="viz-scale-note">가운데 무채색은 0 근처입니다 —'
        ' 그 시간대는 손댈 필요가 없습니다.</p>'
    )
