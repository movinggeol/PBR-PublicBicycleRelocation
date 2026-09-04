"""세 지도(step1 군집·step3 경로·step4 재고 현황)가 함께 쓰는 범례·팔레트.

`project_config.MAP_TILES`가 "세 지도가 같은 배경을 써야 한다"를 맡는 것과
같은 이유로, **범례와 색도 여기 한 벌만 둔다.** 파일마다 따로 박아 두면
하나 고칠 때 셋이 갈라진다 — 실제로 그렇게 갈라져 있었다(1.26.75 조사):
`step3_map/main.py`만 한글·블러·그림자를 갖췄고,
`step4_metrics/imbalance.py`는 영어("Legend")에 회색 굵은 테두리,
`step1_cluster/st_visualization.py`는 범례가 **아예 없었다.**

색이 곧 뜻인 화면에서 그 뜻을 설명하는 상자가 화면마다 다르면 같은 도구가
아닌 것처럼 보인다(docs/구현/DESIGN.md "색만으로 뜻을 전하지 않는다").

## 어떻게 정해졌나

`experiments/diagnostic/`의 시안으로 먼저 만들고, 실제 산출물을 다시 그려
**여섯 장을 나란히 띄워 비교한 뒤** 채택했다(1.26.79 → 1.26.80). 그 비교가
아니었으면 못 봤을 것 둘을 짚어 둔다:

- **검정은 어둡게 만들 수 없다.** 8색을 순환하며 한 단계씩 어둡게 하는데
  `#000000`은 0에 무엇을 곱해도 0이라 **군집 7과 15가 같은 색**이었다.
  `_shift()`가 어두운 색을 밝히는 쪽으로 돌리는 것은 이 때문이다.
- **범례가 길어지면 지도를 가린다.** 18개 군집을 한 줄씩 세우니 범례가
  610~688px, 화면의 2/3가 됐다. `collapse_after`가 있는 이유다.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

# Okabe & Ito(2008)가 제안한, 데이터 시각화에서 널리 쓰이는 공개 색맹 안전
# 8색이다. 검정은 마커 배경·글자와 겹치기 쉬워 순번을 맨 뒤로 뺐다.
_OKABE_ITO = [
    "#E69F00",  # orange
    "#56B4E9",  # sky blue
    "#009E73",  # bluish green
    "#F0E442",  # yellow
    "#0072B2",  # blue
    "#D55E00",  # vermillion
    "#CC79A7",  # reddish purple
    "#000000",  # black
]


# ─────────────────────── 싣기·내리기 (작업 두 종류) ───────────────────────
#
# 🔴 **파랑이 두 화면에서 정반대를 뜻했다**(1.26.107에서 잡았다).
#
#   개념                     작업지시서(웹)   불균형 지도
#   Pick · 싣기 · 과잉 해소   초록            **파랑**
#   Drop · 내리기 · 부족 해소  **파랑**        빨강
#
# 기사가 지도에서 파란 점을 보고 "내릴 곳"으로 읽으면 정확히 반대로 간다.
# 게다가 웹이 쓰던 두 색은 **둘 다 예약된 색**이었다 — `--good-ink`는
# 상태 전용이고 `--blue`는 상호작용 전용이다(docs/구현/DESIGN.md). 범주를
# 상태색으로 칠한 쪽이 틀린 것이라, 지도가 아니라 웹을 고쳤다.
#
# 색은 Okabe-Ito에서 골랐다. 지도는 **채운 원**이라 순색을 그대로 쓰고,
# 웹은 **글자**라 같은 색상 계열에서 명암비를 맞춘 값을 따로 둔다
# (base.html의 `--pick-ink`/`--drop-ink`) — 채움과 글자는 기준이 다르다.
# `--on-blue`에서 이미 겪은 것과 같다: 한 색이 두 역할을 못 한다.
PICK_COLOR = "#0072B2"   # 싣기 — 자전거가 남는 곳에서 실어 온다
DROP_COLOR = "#D55E00"   # 내리기 — 자전거가 모자란 곳에 내린다

# 화면·종이·지도가 같은 말을 쓰게 한다. 예전 범례는 `Drop`·`Pick`이라는
# 영어를 그대로 노출했는데, 지시서는 싣기/내리기, CSV는 pick/drop이라
# **한 개념에 용어가 세 벌**이었다.
# 범례 상자는 좁다 — 길게 쓰면 두 줄로 접힌다(실측). step1 툴팁과 **같은 말**을
# 쓴다: 화면마다 표현이 다르면 같은 개념인지 알아보기 어렵다.
PICK_LABEL = "싣기 (과잉 해소)"
DROP_LABEL = "내리기 (부족 해소)"

# 범례 밖에서 쓰는 **짧은 낱말**. 풍선·창의 '작업 유형 : ___'처럼 괄호 설명이
# 군더더기가 되는 자리다. 괄호만 뗀 같은 말이라 화면끼리 어긋나지 않는다.
PICK_WORD = "싣기"
DROP_WORD = "내리기"


def cluster_color(index: int) -> str:
    """군집 번호 -> 색.

    처음 8개는 색맹 안전색을 그대로 쓴다. 그 이상(9번째부터)은 같은 8색을
    한 단계씩 **어둡게(검정은 밝게)** 돌려써서 최소한 '바로 앞 순번'과는
    밝기가 갈라지게 한다 — 색 자체가 유한하므로 8개를 넘는 군집 전부를
    색맹 안전으로 보장하지는 못한다. 대신 세 지도 모두 레이어 컨트롤로
    군집을 하나씩 껐다 켤 수 있으므로, 9번째부터는 '완전히 안전하지는
    않다'를 알고 쓰는 보완책이다.

    ⚠️ **검정(#000000)은 어둡게 만들 수 없다.** 0에 무엇을 곱해도 0이라,
    처음엔 군집 7과 15가 **똑같은 검정**으로 나왔다(실측: RGB 거리 0).
    18개 군집을 그리는 실제 산출물에서 두 군집이 한 색이면 범례가 설명하는
    구분이 지도에 없는 것이다. 그래서 어두운 색은 **밝히는 쪽으로** 돌린다.
    """
    base = _OKABE_ITO[index % len(_OKABE_ITO)]
    cycle = index // len(_OKABE_ITO)
    if cycle == 0:
        return base
    return _shift(base, 0.72 ** cycle)


def _shift(hex_color: str, factor: float) -> str:
    """`factor`(<1)만큼 어둡게 — 단, 이미 어두운 색은 같은 만큼 **밝게**.

    검정처럼 더 어두워질 수 없는 색이 순환마다 제자리에 머무는 것을 막는다.
    """
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    # 사람 눈 기준 밝기(Rec. 601). 어두운 색을 더 어둡게 하면 검정끼리 겹친다.
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    if luma < 64:
        # 흰색 쪽으로 같은 비율만큼 당긴다.
        r, g, b = (int(v + (255 - v) * (1 - factor)) for v in (r, g, b))
    else:
        r, g, b = (int(v * factor) for v in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def swatch_circle(color: str, label_inside: str = "") -> str:
    """범례용 작은 원 배지. 마커가 원(CircleMarker/DivIcon)일 때 모양을 맞춘다."""
    return (
        f'<span style="display:inline-block;width:15px;height:15px;'
        f'border-radius:50%;background:{color};color:#fff;font-size:9px;'
        f'font-weight:700;text-align:center;line-height:15px;'
        f'vertical-align:-3px;">{label_inside}</span>'
    )


def swatch_circle_dashed(color: str = "#8a8a8f") -> str:
    """점선 테두리 원 배지. **크기가 말하지 못하는 것**을 표시할 때 쓴다.

    불균형 지도의 마커 반지름은 `max(3, improvement)`라 3에서 막힌다. 그래서
    계획이 오히려 악화시킨 대여소(improvement < 0)가 3대 해소한 곳과 픽셀까지
    같아진다 — 크기로는 영영 구분되지 않는다. 색은 이미 싣기/내리기를 뜻하고
    있어 뜻을 하나 더 실을 수 없으므로, 테두리 모양으로 가른다.
    """
    return (
        f'<span style="display:inline-block;width:15px;height:15px;'
        f'border-radius:50%;background:transparent;'
        f'border:2px dashed {color};vertical-align:-3px;"></span>'
    )


def swatch_size_scale(radii: Sequence[float], labels: Sequence[str],
                      color: str = "#8a8a8f") -> str:
    """크기로 값을 나타낼 때 함께 내는 **눈금**.

    ⚠️ 크기 인코딩은 눈금 없이는 못 읽는다. 예전 범례는 *"원 크기는 불균형
    해소량"* 이라고 적어 놓고 **몇 대가 얼마만 한 원인지는 안 줬다**
    (1.26.107). 그러면 "이 원이 저 원보다 크다"까지만 알 수 있고, 정작
    묻고 싶은 "몇 대인가"는 점을 하나씩 눌러 봐야 한다.

    지름이 아니라 **반지름**을 받는다 — 그리는 쪽(CircleMarker)의 단위와
    같아야 눈금이 실제 마커와 맞는다.
    """
    cells = []
    box = max(radii) * 2 + 2
    for r, label in zip(radii, labels):
        d = r * 2
        cells.append(
            f'<span style="display:inline-block;width:{box:.0f}px;'
            f'text-align:center;vertical-align:bottom;">'
            f'<span style="display:block;width:{d:.0f}px;height:{d:.0f}px;'
            f'margin:0 auto 3px;border-radius:50%;background:{color};'
            f'opacity:.55;border:1.5px solid {color};"></span>'
            f'<span style="font-size:11px;color:#555;">{label}</span></span>')
    return ('<span style="display:inline-flex;gap:6px;align-items:flex-end;'
            'margin-top:4px;">' + "".join(cells) + "</span>")


def swatch_line(color: str, dashed: bool = False) -> str:
    """범례용 짧은 선분 배지. 경로(PolyLine) 색을 설명할 때 쓴다."""
    style = "3px dashed" if dashed else "3px solid"
    return (
        f'<span style="display:inline-block;width:18px;height:0;'
        f'border-top:{style} {color};vertical-align:4px;"></span>'
    )


# ── 산출물이 낡았는지 알리는 도장 ──────────────────────────────
#
# 지도의 범례·팔레트는 **코드**에 있고 산출물은 **디스크**에 있다. 코드를
# 고쳐도 이미 그려 둔 지도는 낡은 채로 남는데, 사람이 보는 것은 디스크의
# HTML이다. 실제로 두 번 갈렸다 — 1.26.80이 색맹 안전 팔레트를 넣었지만
# 화면의 군집 지도에는 범례가 아예 없었고, 1.26.107이 싣기·내리기 색을 웹과
# 통일했지만 지도는 한동안 **웹과 반대 색**이었다.
#
# 🔴 둘 다 **사람이 눈으로 발견했다.** 알려 주는 장치가 없으면 다음에도
#    누군가 우연히 볼 때까지 틀린 채로 남는다. 그래서 그릴 때 그린 코드의
#    지문을 함께 찍고, `/maps`가 그것을 지금 코드와 견준다.
#
# 지문에는 **이 파일과 부르는 쪽 모듈**이 함께 들어간다. 이 파일만 해싱하면
# `imbalance.py`가 자기 범례를 고쳤을 때를 놓친다. 부르는 쪽을 손으로 적은
# 목록에 두지 않고 호출 프레임에서 얻는 것은, 그런 목록이 늘 때 빠뜨리기
# 때문이다(`transfer_run.RUN_TABLES`에서 겪었다 — 1.26.113).
STAMP_ATTR = "data-mapviz"
_STAMP_RE = re.compile(rf'{STAMP_ATTR}="([0-9a-f]{{12}})"')


def source_stamp(caller_file: Optional[str] = None) -> str:
    """이 파일(+부르는 쪽)의 내용을 해싱한 12자리 지문.

    내용이 한 글자라도 바뀌면 값이 달라진다 — 판 번호를 손으로 올리지 않아도
    되고, **올리는 것을 잊을 수도 없다.**

    ⚠️ **값을 캐시하지 않는다.** 처음에는 캐시를 뒀는데, 웹앱은 오래 떠 있는
    프로세스라 한 번 읽은 지문이 굳어 **서버를 다시 띄우기 전까지 코드 변경을
    영영 못 보는** 상태가 됐다(실측으로 잡았다 — 코드를 고쳤는데 `/maps`가
    끝까지 "낡지 않았다"고 했다). 낡음을 알리려고 만든 장치가 낡은 값을 쥐고
    있으면 없느니만 못하다. 파일 두 개(50KB)를 해싱하는 데 0.4ms라
    `/maps` 한 번에 1.2ms다 — 캐시할 이유가 없다.
    """
    digest = hashlib.sha256()
    paths = [Path(__file__)]
    if caller_file:
        paths.append(Path(caller_file))
    for path in paths:
        try:
            digest.update(path.read_bytes())
        except OSError:
            # 읽을 수 없으면 그 사실 자체를 지문에 남긴다 — 조용히 빼면
            # 서로 다른 코드가 같은 지문을 갖게 된다.
            digest.update(b"<unreadable>")
    return digest.hexdigest()[:12]


def stamp_in(html_text: str) -> Optional[str]:
    """저장된 지도 HTML에서 지문을 읽는다. 없으면 None(도장 이전 산출물)."""
    found = _STAMP_RE.search(html_text)
    return found.group(1) if found else None


def _caller_file() -> Optional[str]:
    """`legend_html()`을 부른 모듈의 파일 경로."""
    try:
        return sys._getframe(2).f_globals.get("__file__")
    except Exception:      # noqa: BLE001 — 지문이 없어도 지도는 그려져야 한다
        return None


def legend_html(title: str, rows: Sequence[Tuple[str, str]], *,
                note: Optional[str] = None,
                collapse_after: Optional[int] = None,
                collapse_label: str = "항목",
                position: str = "bottom: 24px; left: 24px;") -> str:
    """세 지도가 같이 쓰는 범례 상자.

    `rows`는 (배지 HTML, 설명 글자) 쌍의 목록이다. `step3_map/main.py`의
    기존 범례(블러 배경·둥근 모서리·`--shadow-product`와 같은 그림자 값)를
    그대로 기준으로 삼았다 — 이 저장소에서 이미 한 번 다듬어진 모양이라
    처음부터 새로 디자인하지 않는다.

    collapse_after: 이 개수를 넘는 줄은 `<details>`로 접는다.
      ⚠️ **접지 않으면 범례가 지도를 가린다.** 군집은 실제로 18개라 한 줄씩
      세우면 범례가 610~688px, 화면 세로의 2/3가 됐다(실측). 앞의 몇 줄
      (출발·도착 같은 고정 항목)은 늘 보이고 긴 목록만 접힌다.
      `<details>`는 folium이 CDN에서 받는 것이 아니라 브라우저 기본 기능이라
      **오프라인에서도 열린다**(1.26.74에서 지도가 하얗게 뜨던 것과 다르다).
    """
    visible = rows if collapse_after is None else rows[:collapse_after]
    hidden = () if collapse_after is None else rows[collapse_after:]

    def _lines(items):
        return "".join(
            f'<div style="margin-top:4px">{badge} {label}</div>'
            for badge, label in items
        )

    row_html = _lines(visible)
    if hidden:
        # 접힌 채로 시작한다 — 펴 두면 접는 뜻이 없다.
        row_html += (
            '<details style="margin-top:4px;">'
            '<summary style="cursor:pointer; color:#555; font-size:12px;">'
            f'{collapse_label} {len(hidden)}개 더 보기</summary>'
            f'<div style="max-height:40vh; overflow-y:auto;">{_lines(hidden)}</div>'
            '</details>'
        )

    note_html = ""
    if note:
        note_html = (
            '<div style="margin-top:7px; padding-top:7px; '
            'border-top:1px solid rgba(0,0,0,.12); color:#555; font-size:12px;">'
            f'{note}</div>'
        )
    # 그린 코드의 지문을 산출물에 남긴다 — `/maps`가 이것으로 낡음을 안다.
    stamp = source_stamp(_caller_file())
    return f"""
    <div {STAMP_ATTR}="{stamp}"
         style="position: fixed; {position} z-index: 9999;
                background: rgba(255,255,255,.94);
                -webkit-backdrop-filter: blur(6px); backdrop-filter: blur(6px);
                padding: 12px 14px; border-radius: 10px;
                border: 1px solid rgba(0,0,0,.14);
                box-shadow: rgba(0,0,0,.22) 3px 5px 30px 0;
                font: 13px/1.7 -apple-system, 'Segoe UI', 'Malgun Gothic', sans-serif;
                color: #1a1a1a; max-width: 220px;">
      <div style="font-weight:700; margin-bottom:2px;">{title}</div>
      {row_html}
      {note_html}
    </div>
    """
