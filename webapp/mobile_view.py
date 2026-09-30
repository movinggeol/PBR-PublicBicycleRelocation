"""현장 앱(`/m`) — 기사가 폰으로 드는 화면의 재료 (1.26.307).

관제 화면과 **같은 계산을 읽기만** 한다. 지시서(방문 순서·수량·적재)는
`orders.build()`, 회차별 이동·작업 분은 `route_summary`, 기본 실행·회차는
`/orders`와 같은 `_orders_context()`(app.py)다. 여기서 새로 정하는 것은 넷뿐이다.

  ① 회차 시각 — `_05_10`이면 05:00 시작, 완료 예정 = 시작 + 계획 소요 분
  ② 경로 색 — `mapviz.cluster_color()`. 기존 지도(step3)에서 같은 군집이 같은 색이다
  ③ 체크 기록을 어느 계획에 묶을지 — 계획 지문(`plan_fingerprint`)
  ④ 지도 바탕 — `project_config.MAP_TILES`를 folium과 **같은 규칙**으로 푼다

체크 기록(어느 대여소를 끝냈나)은 **이 기기에만** 남는다 — localStorage,
2026-09-30 사용자 결정(A안). 서버는 기록을 모르고, 진행률은 화면의 스크립트가
센다. 그래서 여기에는 진행률 계산이 없다 — 스크립트가 쓸 계획 합계(수거·배송
대수)만 넘긴다.

진행 중·예정·완료를 **시계로 가르지 않는다.** 계획 대상일이 오늘이 아닐 수 있고
(어제 세운 계획을 오늘 본다), 시연하는 시각이 회차 시각과 다르다. 체크 기록으로
가른다(0곳 = 예정, 일부 = 진행 중, 전부 = 완료).
"""
from __future__ import annotations

# ponytail: 체크가 기기마다 따로다 — 여러 폰의 진행을 모으려면 DB 표 + 쓰기 API(B안)로 옮긴다.

import hashlib
import math
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from mapviz import DROP_COLOR, PICK_COLOR, cluster_color
from project_config import (DEPOT_ID, DEPOT_LAT, DEPOT_LON, DEPOT_NAME, DURATION_LABELS,
                            MAP_TILES)
from webapp import kpi_view, store

# 지도 스크립트는 folium이 싣는 것과 **같은 판**이다 — 새 종류의 의존이 아니다.
# folium을 올리며 판이 바뀌면 `tests/test_mobile.py`가 알린다.
LEAFLET_VERSION = "1.9.3"
LEAFLET_JS = f"https://cdn.jsdelivr.net/npm/leaflet@{LEAFLET_VERSION}/dist/leaflet.js"
LEAFLET_CSS = f"https://cdn.jsdelivr.net/npm/leaflet@{LEAFLET_VERSION}/dist/leaflet.css"

_WINDOW = re.compile(r"^_(\d{2})_(\d{2})$")


def _num(value) -> Optional[float]:
    """결측(None/NaN)은 None. 화면과 JSON에 `nan`이 새지 않게 한다 — JSON의 NaN은
    브라우저 `JSON.parse`가 거절해 스크립트 전체가 멈춘다."""
    try:
        return None if value is None or pd.isna(value) else float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value) -> Optional[int]:
    number = _num(value)
    return None if number is None else int(number)


def duration_window(duration) -> Optional[tuple]:
    """`_05_10` → (5, 10). 모양이 다르면 None — 시각을 지어내지 않는다."""
    match = _WINDOW.match(str(duration or ""))
    if not match:
        return None
    start, end = int(match.group(1)), int(match.group(2))
    if start > 23 or end > 24:
        return None
    return start, end


def clock(start_hour: int, minutes: float = 0) -> str:
    """시작 시각 + 분 → 'HH:MM'. 자정을 넘기면 돌려 쓴다(`_20_05`).

    분은 **올림**이다 — 계획값으로 완료를 앞당겨 약속하지 않는다(7시 3.2분이면 07:04).
    """
    total = start_hour * 60 + math.ceil(max(float(minutes or 0), 0))
    total %= 24 * 60
    return f"{total // 60:02d}:{total % 60:02d}"


def hours_minutes(minutes) -> str:
    """92.4 → '1시간 33분', 40 → '40분'. 올림(위 `clock`과 같은 이유)."""
    value = _num(minutes)
    if value is None:
        return "—"
    total = math.ceil(max(value, 0))
    hours, rest = divmod(total, 60)
    if hours and rest:
        return f"{hours}시간 {rest}분"
    if hours:
        return f"{hours}시간"
    return f"{rest}분"


def hours_minutes_parts(minutes) -> list:
    """92.4 → [(1, '시간'), (33, '분')]. 타일이 숫자는 크게·단위는 작게 쓰려고 쪼갠다.

    `hours_minutes`와 같은 올림이다. 통째 글자('1시간 33분')를 24px로 두었더니 360px
    폰에서 세 칸 타일 밖으로 4px(320px에서 17px) 넘쳐 화면이 가로로 밀렸다(실측).
    """
    value = _num(minutes)
    if value is None:
        return []
    hours, rest = divmod(math.ceil(max(value, 0)), 60)
    parts = [(hours, "시간")] if hours else []
    if rest or not hours:
        parts.append((rest, "분"))
    return parts


def short_name(duration) -> str:
    """'05~10시 (출근)' → '출근'. 괄호가 없으면 이름 그대로, 모르는 코드는 코드 그대로.

    회차 단추 넷이 360px 한 줄에 들어가야 한다 — 긴 이름은 단추의 `aria-label`에 둔다.
    """
    label = DURATION_LABELS.get(duration, duration or "")
    match = re.search(r"\(([^)]+)\)", label)
    return match.group(1) if match else label


def route_minutes(run_label: Optional[str], duration: Optional[str]) -> dict:
    """군집 → {travel_min, work_min}. `route_summary`에 없으면 빈 dict.

    지시서(`orders.build`)는 합계 분만 갖고 있어 이동·작업을 가르지 못한다.
    `/api/route-summary`와 같은 표를 읽는다.
    """
    if not run_label:
        return {}
    frame, _ = store.load("route_summary", run_label=run_label, duration=duration)
    if frame.empty or "cluster" not in frame:
        return {}
    minutes = {}
    for row in frame.to_dict("records"):
        cluster = _int_or_none(row.get("cluster"))
        if cluster is None:
            continue
        minutes[cluster] = {"travel_min": _num(row.get("travel_min")),
                            "work_min": _num(row.get("work_min"))}
    return minutes


def plan_fingerprint(sheets: list, names: list) -> str:
    """체크 기록을 묶을 계획 지문 — 차량 · 방문 순서 · 작업 · 수량.

    기록은 `(실행, 회차)`마다 따로 두지만, **같은 라벨로 다시 돌린 계획**은 정거장이
    바뀐다. 지문이 다르면 화면이 옛 체크를 버린다 — 3번을 끝냈다고 적어 둔 것이
    새 계획의 다른 대여소 3번에 붙으면 기사가 들르지 않은 곳을 끝냈다고 본다.
    """
    parts = []
    for sheet, name in zip(sheets, names):
        stops = ",".join(f"{s['station_id']}/{s['action']}/{s['qty']}" for s in sheet["stops"])
        parts.append(f"{name}:{stops}")
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]


def _station_facts(candidates: Optional[pd.DataFrame]) -> dict:
    """station_id → (계획 시점 재고, 거치대 수). 후보표(`pick_drop`)에서 읽는다.

    **계획 시점**의 재고다 — 지금 재고가 아니다. 화면이 그렇게 적는다. 지금 재고는
    누를 때만 타슈 API로 부르는 `/orders/live`의 몫이다(웹의 API 호출 규약).
    """
    if candidates is None or candidates.empty or "station_id" not in candidates:
        return {}
    facts = {}
    for row in candidates.to_dict("records"):
        facts[row["station_id"]] = (_int_or_none(row.get("stock")),
                                    _int_or_none(row.get("parking_lot")))
    return facts


def build_plan(sheets: list, names: list, candidates: Optional[pd.DataFrame],
               minutes_by_cluster: dict, duration: Optional[str]) -> dict:
    """화면 넷과 스크립트가 함께 쓰는 계획 한 벌 (JSON으로 그대로 실린다).

    `sheets`는 `orders.build()`, `names`는 그와 같은 순서의 차량 이름(`_orders_context`의
    `sheet_names` — 차량이 없는 옛 산출물은 '군집 N')이다. 이름 규칙을 여기서 다시
    만들지 않는다 — `/orders?vehicle=`와 같은 이름이어야 두 화면이 같은 차를 가리킨다.
    """
    window = duration_window(duration)
    start = window[0] if window else None
    facts = _station_facts(candidates)

    vehicles = []
    for sheet, name in zip(sheets, names):
        work = [s for s in sheet["stops"] if s["action"] != "return"]
        pick_total = sum(s["qty"] for s in work if s["action"] == "pick")
        drop_total = sum(s["qty"] for s in work if s["action"] == "drop")
        split = minutes_by_cluster.get(sheet["cluster"], {})
        stops = []
        for s in sheet["stops"]:
            stock, lot = facts.get(s["station_id"], (None, None))
            stops.append({
                "no": s["no"], "id": s["station_id"], "name": s["station_name"],
                "lat": _num(s["lat"]), "lon": _num(s["lon"]),
                "action": s["action"], "label": s["action_label"], "qty": s["qty"],
                "load": s["load_after"], "minutes": _num(s["minutes"]),
                "stock": stock, "lot": lot,
            })
        vehicles.append({
            "id": name,
            "cluster": sheet["cluster"],
            "color": cluster_color(sheet["cluster"]),
            "stations": sheet["stations"],
            # `/orders`의 '옮길 자전거'와 같은 값(실을 대수 합)이다 — 두 화면의 숫자가 갈리지 않게.
            "bikes": sheet["bikes"],
            "distance_km": _num(sheet["distance_km"]),
            "minutes": _num(sheet["minutes"]),
            "travel_min": split.get("travel_min"),
            "work_min": split.get("work_min"),
            "finish": clock(start, sheet["minutes"]) if start is not None else None,
            "pick_total": pick_total,
            "drop_total": drop_total,
            # 🔴 옮긴 대수는 `min(수거, 배송)`으로 센다(pbr-pipeline 함정 13). 수거만 세면
            #    실었지만 내리지 못한 자전거가 '옮겼다'로 잡힌다. 스크립트의 완료율 분모다.
            "moved_plan": min(pick_total, drop_total),
            "max_load": max((s["load_after"] for s in sheet["stops"]), default=0),
            "chain": [s["station_id"] for s in work],
            "stops": stops,
        })

    longest = max((v["minutes"] or 0 for v in vehicles), default=0)
    return {
        "fp": plan_fingerprint(sheets, names),
        "start": clock(start) if start is not None else None,
        "finish": clock(start, longest) if start is not None and vehicles else None,
        "vehicles": vehicles,
        "totals": {
            "vehicles": len(vehicles),
            "stations": sum(v["stations"] for v in vehicles),
            # 체크의 단위 — 방문지(차고지 복귀 제외). 한 대여소에서 수거·배송을 둘 다 하면
            # 두 건이라 `stations`(대여소 수)와 다를 수 있다. 진행 현황은 이것으로 센다.
            "stops": sum(len(v["chain"]) for v in vehicles),
            "bikes": sum(v["bikes"] for v in vehicles),
            "moved_plan": sum(v["moved_plan"] for v in vehicles),
        },
        "depot": {"id": DEPOT_ID, "name": DEPOT_NAME, "lat": DEPOT_LAT, "lon": DEPOT_LON},
        # 지도 표지의 수거·배송 색 — 기존 지도(`mapviz`)와 같은 값. 표지는 채운 원이 아니라
        # **테두리**로만 쓴다(안의 번호 글자가 배송 순색 위에서 4.5:1을 못 넘는다).
        "colors": {"pick": PICK_COLOR, "drop": DROP_COLOR},
    }


def nav_url(stop: dict) -> Optional[str]:
    """정거장 → 카카오맵 길찾기 주소. 좌표가 없으면 None(빈 좌표로 엉뚱한 곳에 안내하지 않는다).

    모양: `https://map.kakao.com/link/to/이름,위도,경도` — 웹 주소라 앱이 없어도 열리고,
    앱이 있으면 폰이 앱으로 넘긴다. 이름의 쉼표는 구분자와 겹쳐 공백으로 바꾼다.
    ⚠️ **m_base.html의 `navUrl()`과 같은 규칙이다** — 스크립트는 체크를 따라 다음
    대여소로 고쳐 쓰고, 이쪽은 스크립트가 없을 때의 첫 값이다. 한쪽을 고치면 다른 쪽도.
    TMAP·네이버 앱 주소는 넣지 않았다 — 폰에서 확인하지 못했다(2026-09-30).
    """
    lat, lon = _num(stop.get("lat")), _num(stop.get("lon"))
    if lat is None or lon is None:
        return None
    name = str(stop.get("name") or stop.get("id") or "").replace(",", " ")
    # `safe`는 자바스크립트 `encodeURIComponent`가 그대로 두는 기호다 — 두 쪽 주소가 같게.
    return f"https://map.kakao.com/link/to/{quote(name, safe="!~*'()")},{lat},{lon}"


@lru_cache(maxsize=None)
def tile_source() -> dict:
    """`MAP_TILES` → Leaflet 타일 주소·출처 표기. folium의 `TileLayer`에 **풀게 한다.**

    지도 바탕은 `project_config.MAP_TILES` 하나다(pbr-pipeline 함정 4 — 세 지도가 같은
    값을 쓴다). 여기서 주소를 따로 적으면 설정을 바꿔도 이 화면만 옛 바탕이 남는다.
    folium은 'OpenStreetMap'을 `OpenStreetMap Mapnik`으로 바꿔 부르는 등 이름 풀이에
    예외가 있어서, 같은 규칙을 다시 쓰지 않고 folium에 맡긴다. 못 풀면 빈 dict —
    화면은 지도를 접고 목록만 보인다.
    """
    try:
        import folium

        layer = folium.TileLayer(MAP_TILES)
        options = layer.options
        return {"url": layer.tiles,
                "attribution": options.get("attribution", ""),
                "max_zoom": options.get("max_zoom", 19),
                "subdomains": options.get("subdomains", "abc")}
    except Exception as err:          # noqa: BLE001 — 지도가 없어도 목록은 써야 한다
        print(f"[경고] 지도 바탕({MAP_TILES})을 풀지 못했다: {type(err).__name__}: {err}")
        return {}


def plan_kpi(run_label: Optional[str], duration: Optional[str]) -> dict:
    """진행 현황의 '계획 지표' — 그 회차의 `kpi_summary` 한 줄. 없으면 빈 dict.

    사진 시안의 '총 이용 횟수 · 신고 건수'는 싣지 않았다 — 실시간 대여 수와 신고
    자료가 이 시스템에 없다. 지어내지 않고, 있는 계획 지표를 계획 지표라고 적는다.
    결품 감소율은 첫 화면·`/kpi`와 같은 `kpi_view.stockout_cut_pct`다.
    """
    if not run_label or not duration:
        return {}
    rows = store.kpi(run_label, duration)
    if rows.empty:
        return {}
    row = rows.iloc[0].to_dict()
    before, after = _num(row.get("stockout_hours_before")), _num(row.get("stockout_hours_after"))
    return {
        "stockout_before": before,
        "stockout_after": after,
        "stockout_cut": kpi_view.stockout_cut_pct(before, after),
        "distance_km": _num(row.get("total_distance_km")),
        "budget_met": _num(row.get("time_budget_met")),
        "time_budget": _num(row.get("time_budget_minutes")),
    }
