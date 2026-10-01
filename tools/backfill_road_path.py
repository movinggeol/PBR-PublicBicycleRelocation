"""이미 그려 둔 경로 지도(step3)에서 **실도로 선의 좌표**를 되살려 `road_path`에 넣는다.

## 왜 필요한가 (1.26.308)

현장 앱(`/m`)이 대여소 사이를 직선으로 이어 *"길을 일직선으로 뚫고 다니는"* 경로를 그렸다
(사용자 지적 2026-09-30). 실제 도로 좌표는 step3가 TMAP에서 받아 **지도 HTML에만** 그렸고
DB에는 없었다. 1.26.308부터 step3가 `road_path`에 남기지만, 그 전에 그린 지도는 좌표가 HTML
안에만 있다. TMAP을 다시 부르지 않고(일일 한도) 그 HTML에서 읽어 온다.

## 어떻게 읽나

folium이 쓴 HTML에서 군집마다:

- 레이어 이름표 `"Cluster N" : feature_group_…` 로 군집 번호와 묶음 변수를 잇고,
- `L.polyline([[위도, 경도], …], {…}).addTo(feature_group_…)` 를 그 묶음의 선으로 모은다.
- **점선(`"dashArray": "8,6"`)은 버린다** — TMAP을 못 받아 직선으로 낮춘 선이다.

## 무엇을 확인하나

같은 라벨로 다시 돌린 계획이면 HTML의 선이 **지금 계획과 다른 정거장**을 지날 수 있다.
그래서 군집마다 `vrp_plan`의 방문 대여소가 **모두 선에서 200m 안**에 있는지 보고, 아니면
그 군집은 넣지 않는다(지어내지 않는다). DB에는 확인을 통과한 군집만 갈아끼운다
(`db.replace_road_paths` — 다른 군집의 행은 그대로).

> 재현: `python tools/backfill_road_path.py --dry-run`

## 쓰는 법

    python tools/backfill_road_path.py --dry-run                 # 무엇을 넣을지만 본다
    python tools/backfill_road_path.py                           # 남아 있는 경로 지도 전부
    python tools/backfill_road_path.py --run-label "2026-09-30 22"
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import project_config  # noqa: F401  — 출력 인코딩을 UTF-8로 못 박는다(파일로 넘겨도 안 죽게)
from project_config import DATA_ROOT, DEPOT_ID

import pandas as pd

import db

MAP_DIR = DATA_ROOT / "pp_data/VRP/visualization"
# step3 `result_path`의 모양: vrp_map{duration} ({now}).html
_NAME = re.compile(r"^vrp_map(_\d{2}_\d{2}) \((.+)\)\.html$")
_GROUP = re.compile(r'"Cluster (\d+)"\s*:\s*(feature_group_\w+)')
_LINE = re.compile(r"L\.polyline\(\s*(\[\[.*?\]\])\s*,\s*(\{.*?\})\s*\)\s*\.addTo\((feature_group_\w+)\)",
                   re.S)
NEAR_M = 200.0     # 방문 대여소가 선에서 이만큼 안이면 그 선이 이 계획의 경로다


def paths_from_html(html: str) -> dict:
    """경로 지도 HTML → {군집 번호: [선 조각, …]}. 점선(직선 대체)은 뺀다."""
    groups = {var: int(num) for num, var in _GROUP.findall(html)}
    paths: dict = {}
    for coords, options, var in _LINE.findall(html):
        if var not in groups:
            continue
        try:
            dash = json.loads(options).get("dashArray")
        except ValueError:
            continue
        if dash:
            continue
        paths.setdefault(groups[var], []).append(json.loads(coords))
    return paths


def _to_segment_m(lat, lon, a, b) -> float:
    """점 (lat, lon)에서 선분 a-b까지의 거리(m). 좁은 범위라 평면으로 근사한다."""
    k = math.cos(math.radians(lat))
    px, py = 0.0, 0.0
    ax, ay = (a[1] - lon) * 111_320 * k, (a[0] - lat) * 111_320
    bx, by = (b[1] - lon) * 111_320 * k, (b[0] - lat) * 111_320
    dx, dy = bx - ax, by - ay
    norm = dx * dx + dy * dy
    u = 0.0 if norm == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / norm))
    return math.hypot(ax + u * dx - px, ay + u * dy - py)


def far_stops(segments: list, stops: list) -> list:
    """선에서 `NEAR_M`보다 먼 방문 대여소 id 목록. 비어 있으면 이 계획의 선이다.

    꼭짓점이 아니라 **선분**까지 잰다 — 곧은 길은 꼭짓점이 수백 m씩 떨어져 있어, 꼭짓점만
    재면 길 한가운데 붙은 대여소도 '멀다'로 판정했다(시험으로 잡음).
    """
    pieces = [(seg[i], seg[i + 1]) for seg in segments for i in range(len(seg) - 1)]
    pieces += [(seg[0], seg[0]) for seg in segments if len(seg) == 1]
    far = []
    for station_id, lat, lon in stops:
        if not pieces or min(_to_segment_m(lat, lon, a, b) for a, b in pieces) > NEAR_M:
            far.append(station_id)
    return far


def backfill_file(path: Path, dry_run: bool = False) -> dict:
    """지도 파일 하나를 읽어 넣는다. 반환: {run_label, duration, saved, skipped: {군집: 까닭}}."""
    match = _NAME.match(path.name)
    if not match:
        return {"file": path.name, "error": "이름이 vrp_map{회차} ({실행}).html 모양이 아니다"}
    duration, run_label = match.group(1), match.group(2)
    result = {"file": path.name, "run_label": run_label, "duration": duration,
              "saved": [], "skipped": {}}
    with db.session() as conn:
        plan = db.load_frame(conn, "vrp_plan", run_label=run_label, duration=duration)
    if plan.empty:
        result["error"] = "DB에 이 실행·회차의 vrp_plan이 없다"
        return result

    paths = paths_from_html(path.read_text(encoding="utf-8"))
    rows = []
    for cluster, group in plan.groupby("cluster"):
        cluster = int(cluster)
        segments = paths.get(cluster)
        if not segments:
            result["skipped"][cluster] = "지도에 실도로 선이 없다(직선으로 그렸거나 군집이 없다)"
            continue
        stops = [(r.to_id, float(r.to_lat), float(r.to_lon)) for r in group.itertuples()
                 if r.action != "return" and r.to_id != DEPOT_ID
                 and pd.notna(r.to_lat) and pd.notna(r.to_lon)]
        far = far_stops(segments, stops)
        if far:
            result["skipped"][cluster] = (f"선이 방문 대여소 {len(far)}곳을 {NEAR_M:.0f}m 밖으로 지난다"
                                          f"({', '.join(far[:3])}) — 다른 계획의 지도다")
            continue
        rows.append(db.road_path_row(cluster, segments, source="map_html"))
        result["saved"].append(cluster)
    if rows and not dry_run:
        db.replace_road_paths(pd.DataFrame(rows), run_label, duration, result["saved"])
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--run-label", help="이 실행의 지도만 (생략하면 남아 있는 전부)")
    parser.add_argument("--dry-run", action="store_true", help="DB에 쓰지 않고 무엇을 넣을지만 본다")
    args = parser.parse_args(argv)

    files = sorted(MAP_DIR.glob("vrp_map*.html"))
    if args.run_label:
        files = [f for f in files if f.name.endswith(f" ({args.run_label}).html")]
    if not files:
        print(f"경로 지도가 없습니다: {MAP_DIR}")
        return 1
    total = 0
    for path in files:
        res = backfill_file(path, dry_run=args.dry_run)
        if res.get("error"):
            print(f"  건너뜀 {path.name}: {res['error']}")
            continue
        total += len(res["saved"])
        print(f"  {res['run_label']} {res['duration']}: 군집 {len(res['saved'])}개"
              f"{' (쓰지 않음)' if args.dry_run else ''}"
              + (f" · 뺀 군집 {len(res['skipped'])}개" if res["skipped"] else ""))
        for cluster, why in res["skipped"].items():
            print(f"      군집 {cluster}: {why}")
    print(f"{'넣을' if args.dry_run else '넣은'} 군집 {total}개")
    return 0


if __name__ == "__main__":
    sys.exit(main())
