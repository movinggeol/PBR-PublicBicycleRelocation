"""구(區)별 불균형 완화 — `/maps?run_label=`의 '구별로 보기' (1.26.316).

대여소 1,300여 곳을 점으로 찍으면 *"어느 동네가 나아졌나"* 가 한눈에 안 들어온다(사용자 지적).
대전의 다섯 자치구(동구 · 중구 · 서구 · 유성구 · 대덕구)로 묶어 작업 전후 불균형을 센다.

## 무엇을 세나 — step4와 같은 정의

- **불균형** = |재고 − 목표 재고| (대). `pipeline/step4_metrics/imbalance.py`의
  `demand_satisfaction()`과 같은 식이다. 작업 전은 `rebalance_plan`의 **모든 대여소**,
  작업 후는 작업한 대여소(`metrics`)만 `af_imbalance`로 바꾸고 나머지는 그대로 둔다.
- **완화율** = 1 − 작업 후 합 ÷ 작업 전 합. 구 전체의 불균형이 얼마나 줄었나다.
- **작업 대여소 개선률**은 step4 KPI의 `avg_improvement_rate`처럼 작업한 대여소만의
  평균이다 — 구 전체 완화율은 작업 안 한 대여소까지 분모에 들어가 작게 나온다. 둘은 다른 물음이다.
- 수거 · 배송 대수는 계획(`metrics.rebal_qty`) 기준이다.

## 구 경계

`daejeon_gu.geojson` — 통계청(KOSTAT) 2013 시군구 경계(github.com/southkorea/southkorea-maps,
KOSTAT 자료는 공유 · 가공 자유)에서 대전 다섯 구만 떼어 0.0002°(약 20m)로 줄인 것이다.
줄인 경계라 구 경계선 바로 위 대여소는 드물게 옆 구로 갈 수 있다. 어느 구에도 안 드는 점
(경계를 줄이며 생긴 틈)은 **가장 가까운 구**로 보낸다 — 대전 밖 대여소는 없다.
"""
from __future__ import annotations

import json
import math
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from webapp import store

GEOJSON = Path(__file__).with_name("daejeon_gu.geojson")


@lru_cache(maxsize=1)
def districts() -> list:
    """[{name, code, rings: [[(lon, lat), …], …]}] — 바깥 고리만 쓴다(대전 구에는 구멍이 없다)."""
    data = json.loads(GEOJSON.read_text(encoding="utf-8"))
    out = []
    for f in data["features"]:
        g = f["geometry"]
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        out.append({"name": f["properties"]["name"], "code": f["properties"]["code"],
                    "rings": [[(x, y) for x, y in poly[0]] for poly in polys]})
    out.sort(key=lambda d: d["code"])
    return out


def _inside(lon: float, lat: float, ring: list) -> bool:
    """반직선 교차 — 점에서 오른쪽으로 그은 선이 경계를 홀수 번 넘으면 안이다."""
    hit = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            hit = not hit
        j = i
    return hit


def _gap(lon: float, lat: float, ring: list) -> float:
    """점과 고리 꼭짓점 사이 가장 짧은 거리(도 단위, 경도는 위도로 줄인다) — 틈에 빠진 점용."""
    k = math.cos(math.radians(lat))
    return min(((x - lon) * k) ** 2 + (y - lat) ** 2 for x, y in ring)


def district_of(lat: float, lon: float) -> Optional[str]:
    if lat is None or lon is None or pd.isna(lat) or pd.isna(lon):
        return None
    for d in districts():
        if any(_inside(lon, lat, r) for r in d["rings"]):
            return d["name"]
    return min(districts(), key=lambda d: min(_gap(lon, lat, r) for r in d["rings"]))["name"]


def run_durations(run_label: str) -> list:
    """이 실행에서 구별 수치를 낼 수 있는 회차(`metrics`가 있는 것), 하루 순서대로."""
    return store.metric_durations(run_label)


def summary(run_label: str, duration: str) -> Optional[dict]:
    """구별 작업 전후 불균형. 자료가 없으면 None."""
    # DB는 store.py만 연다(웹 계층 규약, test_웹이_db를_직접_열지_않는다). 실패하면 빈 표가 온다.
    plan, _ = store.load("rebalance_plan", run_label=run_label, duration=duration)
    info, _ = store.load("station_info", run_label=run_label)
    worked, _ = store.load("metrics", run_label=run_label, duration=duration)
    if plan.empty or info.empty:
        return None

    where = info.drop_duplicates("station_id").set_index("station_id")[["lat", "lon"]]
    frame = plan[["station_id", "stock", "target_qty"]].join(where, on="station_id")
    frame["before"] = (frame["stock"] - frame["target_qty"]).abs()
    frame["after"] = frame["before"]
    frame["pick"] = 0
    frame["drop"] = 0
    frame["worked"] = False
    frame["rate"] = float("nan")
    if not worked.empty:
        w = worked.drop_duplicates("station_id").set_index("station_id")
        hit = frame["station_id"].isin(w.index)
        ids = frame.loc[hit, "station_id"]
        frame.loc[hit, "after"] = ids.map(w["af_imbalance"]).values
        qty = ids.map(w["rebal_qty"]).values
        frame.loc[hit, "pick"] = [-q if q < 0 else 0 for q in qty]
        frame.loc[hit, "drop"] = [q if q > 0 else 0 for q in qty]
        frame.loc[hit, "worked"] = True
        frame.loc[hit, "rate"] = ids.map(w["improvement_rate"]).values
    frame["district"] = [district_of(a, b) for a, b in zip(frame["lat"], frame["lon"])]

    rows = []
    for d in districts():
        part = frame[frame["district"] == d["name"]]
        before, after = float(part["before"].sum()), float(part["after"].sum())
        rates = part.loc[part["worked"], "rate"].dropna()
        rows.append({
            "name": d["name"],
            "stations": int(len(part)),
            "worked": int(part["worked"].sum()),
            "pick": int(part["pick"].sum()),
            "drop": int(part["drop"].sum()),
            "before": round(before, 1),
            "after": round(after, 1),
            "cut": round(before - after, 1),
            "cut_pct": round(100 * (before - after) / before, 1) if before > 0 else None,
            "worked_pct": round(100 * float(rates.mean()), 1) if len(rates) else None,
        })
    total_before = sum(r["before"] for r in rows)
    total_after = sum(r["after"] for r in rows)
    return {
        "rows": rows,
        "unplaced": int(frame["district"].isna().sum()),   # 좌표가 없는 대여소
        "total": {
            "stations": sum(r["stations"] for r in rows),
            "worked": sum(r["worked"] for r in rows),
            "pick": sum(r["pick"] for r in rows),
            "drop": sum(r["drop"] for r in rows),
            "before": round(total_before, 1),
            "after": round(total_after, 1),
            "cut": round(total_before - total_after, 1),
            "cut_pct": round(100 * (total_before - total_after) / total_before, 1) if total_before else None,
        },
    }


def map_svg(rows: list, width: int = 520) -> str:
    """다섯 구를 완화율로 칠한 SVG. 진할수록 많이 줄었다 — 같은 값을 아래 표가 글자로 적는다."""
    ds = districts()
    pts = [p for d in ds for r in d["rings"] for p in r]
    lon0, lon1 = min(p[0] for p in pts), max(p[0] for p in pts)
    lat0, lat1 = min(p[1] for p in pts), max(p[1] for p in pts)
    k = math.cos(math.radians((lat0 + lat1) / 2))       # 경도 1°는 위도 1°보다 짧다
    pad = 8
    scale = (width - 2 * pad) / ((lon1 - lon0) * k)
    height = round((lat1 - lat0) * scale + 2 * pad)

    def xy(p):
        return (pad + (p[0] - lon0) * k * scale, pad + (lat1 - p[1]) * scale)

    by_name = {r["name"]: r for r in rows}
    top = max((r["cut_pct"] or 0) for r in rows) or 1
    parts = [f'<svg viewBox="0 0 {width} {height}" class="viz district-map" role="img" '
             f'aria-label="구별 불균형 완화율 지도 — 같은 값이 아래 표에 있습니다">']
    labels = []
    for d in ds:
        r = by_name.get(d["name"], {})
        pct = r.get("cut_pct")
        shade = 0.12 + 0.68 * (max(pct, 0) / top) if pct is not None else 0.05
        tip = (f'data-tip-title="{d["name"]}" data-tip="불균형 {r.get("before", 0):g} → {r.get("after", 0):g}대'
               f' ({"—" if pct is None else f"{pct:g}% 완화"})" '
               f'data-tip-sub="작업 대여소 {r.get("worked", 0)}곳 / {r.get("stations", 0)}곳"')
        for ring in d["rings"]:
            path = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in map(xy, ring)) + " Z"
            parts.append(f'<path d="{path}" class="district-shape" fill-opacity="{shade:.2f}" '
                         f'tabindex="0" {tip}/>')
        # 이름표는 가장 큰 고리의 꼭짓점 평균에 둔다(다섯 구는 모두 고리 하나다).
        ring = max(d["rings"], key=len)
        cx = sum(xy(p)[0] for p in ring) / len(ring)
        cy = sum(xy(p)[1] for p in ring) / len(ring)
        labels.append(f'<text x="{cx:.1f}" y="{cy - 4:.1f}" class="district-name" text-anchor="middle">{d["name"]}</text>'
                      f'<text x="{cx:.1f}" y="{cy + 17:.1f}" class="district-value" text-anchor="middle">'
                      f'{"—" if pct is None else f"{pct:g}%"}</text>')
    parts.extend(labels)        # 글자는 모든 면 위에 — 옆 구의 면에 가리지 않게
    parts.append("</svg>")
    return "".join(parts)
