"""세 지도 생성기(step1·step3·step4)가 각자 만든 범례·팔레트를 하나로 모은 시안.

**아직 프로덕션 코드를 대체하지 않는다.** `mapviz_compare.py`가 이 모듈로
기존 산출물을 다시 그려 `data/mapviz_compare/`에 따로 내놓으므로, 실제
산출물과 나란히 열어 비교한 뒤 채택 여부를 정한다.

바꾼 것 셋 (버전관리 1.26.72~74 시각화 UX 점검의 연장):

1. **범례를 한 함수로 통일한다** — `step3_map/main.py`만 한글·블러·그림자를
   갖췄고, `step4_metrics/imbalance.py`는 영어("Legend")에 회색 굵은 테두리,
   `step1_cluster/st_visualization.py`는 범례가 아예 없었다. 색이 곧 뜻인
   화면에서 그 뜻을 설명하는 상자가 화면마다 다르면 같은 도구가 아닌 것처럼
   보인다(docs/구현/DESIGN.md "색만으로 뜻을 전하지 않는다").
2. **그 함수를 세 지도가 같이 쓴다** — 팔레트·범례 HTML이 파일마다 따로
   박혀 있으면 하나 고칠 때 셋 다 갈라진다(실제로 그래서 갈라져 있었다).
3. **군집 팔레트를 색맹 안전 8색(Okabe–Ito, 2008) 기준으로 새로 짠다** —
   기존 18색(`st_visualization.py`)은 `red`/`darkred`/`Salmon`,
   `green`/`Olive`/`Lime`처럼 인접한 색이 많아 군집이 여럿 겹치면 구분이
   어려웠다.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

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


def cluster_color(index: int) -> str:
    """군집 번호 -> 색.

    처음 8개는 색맹 안전색을 그대로 쓴다. 그 이상(9번째부터)은 같은 8색을
    한 단계씩 어둡게 돌려써서 최소한 '바로 앞 순번'과는 밝기가 갈라지게
    한다 — 색 자체가 유한하므로 8개를 넘는 군집 전부를 색맹 안전으로
    보장하지는 못한다. 대신 세 지도 모두 레이어 컨트롤로 군집을 하나씩
    껐다 켤 수 있으므로, 9번째부터는 '완전히 안전하지는 않다'를 알고 쓰는
    보완책이다.
    """
    base = _OKABE_ITO[index % len(_OKABE_ITO)]
    cycle = index // len(_OKABE_ITO)
    return base if cycle == 0 else _darken(base, 0.72 ** cycle)


def _darken(hex_color: str, factor: float) -> str:
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    return f"#{int(r * factor):02x}{int(g * factor):02x}{int(b * factor):02x}"


def swatch_circle(color: str, label_inside: str = "") -> str:
    """범례용 작은 원 배지. 마커가 원(CircleMarker/DivIcon)일 때 모양을 맞춘다."""
    return (
        f'<span style="display:inline-block;width:15px;height:15px;'
        f'border-radius:50%;background:{color};color:#fff;font-size:9px;'
        f'font-weight:700;text-align:center;line-height:15px;'
        f'vertical-align:-3px;">{label_inside}</span>'
    )


def swatch_line(color: str, dashed: bool = False) -> str:
    """범례용 짧은 선분 배지. 경로(PolyLine) 색을 설명할 때 쓴다."""
    style = "3px dashed" if dashed else "3px solid"
    return (
        f'<span style="display:inline-block;width:18px;height:0;'
        f'border-top:{style} {color};vertical-align:4px;"></span>'
    )


def legend_html(title: str, rows: List[Tuple[str, str]], *,
                note: Optional[str] = None,
                position: str = "bottom: 24px; left: 24px;") -> str:
    """세 지도가 같이 쓰는 범례 상자.

    `rows`는 (배지 HTML, 설명 글자) 쌍의 목록이다. `step3_map/main.py`의
    기존 범례(블러 배경·둥근 모서리·`--shadow-product`와 같은 그림자 값)를
    그대로 기준으로 삼았다 — 이 저장소에서 이미 한 번 다듬어진 모양이라
    처음부터 새로 디자인하지 않는다.
    """
    row_html = "".join(
        f'<div style="margin-top:4px">{badge} {label}</div>'
        for badge, label in rows
    )
    note_html = ""
    if note:
        note_html = (
            '<div style="margin-top:7px; padding-top:7px; '
            'border-top:1px solid rgba(0,0,0,.12); color:#555; font-size:12px;">'
            f'{note}</div>'
        )
    return f"""
    <div style="position: fixed; {position} z-index: 9999;
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
