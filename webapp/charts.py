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
         lower_is_better: bool = False) -> str:
    """계열 하나짜리 꺾은선. 실행 순서에 따른 변화를 본다.

    labels: x축 이름(실행 라벨). values: 값(None은 건너뛴다 — 못 잰 지표).
    lower_is_better: 마지막 값이 좋아졌는지 판정하는 방향만 뒤집는다.
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

    parts = [f'<svg viewBox="0 0 {width} {height}" class="viz" role="img" '
             f'aria-label="{html.escape(title)}">']

    # 격자 3줄 + y 눈금. 눈금 글자는 세로로 줄이 맞아야 해서 tabular-nums를 쓴다.
    for k in range(3):
        v = low + span * k / 2
        y = py(v)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" '
                     f'y2="{y:.1f}" class="viz-grid"/>')
        parts.append(_text(pad_l - 6, y + 4, _fmt(v), "viz-tick", "end"))

    parts.append(f'<polyline class="viz-line" points="'
                 + " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in pairs) + '"/>')

    for i, v in pairs:
        # 히트 영역을 마크보다 크게 잡는다(8px 점을 정확히 짚게 만들지 않는다).
        parts.append(
            f'<circle cx="{px(i):.1f}" cy="{py(v):.1f}" r="11" class="viz-hit" '
            f'tabindex="0" data-tip="{html.escape(labels[i])}: {_fmt(v, 2)}{unit}"/>')
        parts.append(f'<circle cx="{px(i):.1f}" cy="{py(v):.1f}" r="4" '
                     f'class="viz-dot"/>')

    # 값 표시는 **끝점 하나만**. 점마다 숫자를 적으면 읽히지 않는다.
    last_i, last_v = pairs[-1]
    parts.append(_text(px(last_i), py(last_v) - 10,
                       f"{_fmt(last_v, 2)}{unit}", "viz-value",
                       "end" if last_i == len(values) - 1 else "middle"))

    # x축은 처음과 끝 이름만. 실행 라벨은 길어서 다 적으면 겹친다.
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
        return '<p class="empty">그릴 자료가 없습니다.</p>'

    pad_l, pad_r, pad_t, pad_b = 60, 56, 4, 4
    row_h = bar_h + gap
    plot_w = width - pad_l - pad_r
    height = pad_t + row_h * n + pad_b

    _, high = _nice_bounds(0, max(values, default=0) or 1)
    span = high or 1

    parts = [f'<svg viewBox="0 0 {width} {height}" class="viz" role="img" '
             f'aria-label="{html.escape(title)}">']

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
            f'data-tip="{html.escape(str(label))}: {_fmt(v, 1)}{unit}"/>')
        parts.append(_text(pad_l + w + 6, cy + 4, f"{_fmt(v, 1)}{unit}",
                           "viz-value", "start"))

    parts.append("</svg>")
    return "".join(parts)


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

    parts = [f'<svg viewBox="0 0 {width} {height}" class="viz" role="img" '
             f'aria-label="{html.escape(y_label)} 대비 {html.escape(x_label)}">']

    for k in range(3):
        y = py(y_low + y_span * k / 2)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" '
                     f'y2="{y:.1f}" class="viz-grid"/>')
        parts.append(_text(pad_l - 8, y + 4, _fmt(y_low + y_span * k / 2),
                           "viz-tick", "end"))
    for k in range(3):
        v = x_low + x_span * k / 2
        parts.append(_text(px(v), height - 20, _fmt(v), "viz-tick"))

    for p in usable:
        cx, cy = px(p[x_key]), py(p[y_key])
        parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="13" class="viz-hit" '
                     f'tabindex="0" data-tip="{html.escape(p.get("tip", ""))}"/>')
        # 겹치는 점은 면 색 링으로 떼어 놓는다(테두리를 그리는 게 아니다).
        parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5" class="viz-dot ring"/>')
        if p.get("label"):
            parts.append(_text(cx, cy - 11, p["label"], "viz-value"))

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
        return '<p class="empty">그릴 자료가 없습니다.</p>'

    pad_l, pad_t, pad_b = 42, 20, 8
    grid_w = cell * len(cols)
    total_w = width or (pad_l + grid_w + 8)
    total_h = pad_t + cell * len(rows) + pad_b

    values = [v for row in matrix for v in row if v is not None]
    scale = max((abs(v) for v in values), default=0)

    parts = [f'<svg viewBox="0 0 {total_w} {total_h}" class="viz" role="img" '
             f'aria-label="요일과 시간대별 값">']

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
            tip = f"{row_name} {cols[c]}: {_fmt(value, 2)}{unit}"
            # 칸 사이 2px은 테두리가 아니라 **면이 드러난 틈**이다.
            parts.append(
                f'<rect x="{x + 1}" y="{y + 1}" width="{cell - 2}" '
                f'height="{cell - 2}" rx="3" fill="var(--{token})" '
                f'tabindex="0" data-tip="{html.escape(tip)}"/>')

    parts.append("</svg>")
    return "".join(parts)


def scale_legend(scale: float, unit: str = "") -> str:
    """발산 척도 범례. 히트맵과 반드시 함께 낸다."""
    if scale <= 0:
        return ""
    chips = []
    for level in range(_STEPS, 0, -1):
        chips.append(f'<i style="background:var(--viz-neg-{level})"></i>')
    chips.append('<i style="background:var(--viz-zero)"></i>')
    for level in range(1, _STEPS + 1):
        chips.append(f'<i style="background:var(--viz-pos-{level})"></i>')
    return (f'<div class="viz-scale"><span>쌓임 −{_fmt(scale)}{unit}</span>'
            + "".join(chips)
            + f'<span>빠져나감 +{_fmt(scale)}{unit}</span></div>')
