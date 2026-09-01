"""세 지도(군집·경로·재고 현황)에 통일 범례 + 색맹 안전 팔레트(mapviz_shared)를
적용해 다시 그리고, 기존 산출물과 나란히 비교하기 위한 스크립트.

**프로덕션 코드는 건드리지 않는다** — `step1_cluster/st_visualization.py`,
`step3_map/main.py`, `step4_metrics/imbalance.py`는 그대로 두고, 그 스크립트가
이미 만들어 둔 실제 CSV 산출물을 읽어 `mapviz_shared.py`로 다시 그린 뒤
`data/mapviz_compare/`에 따로 저장한다. 그 폴더는 `webapp/catalog.py`의
`MAP_CATEGORIES`가 보는 고정 하위 폴더(ILP/visualization 등)가 아니므로
`/maps`·`/data` 화면에는 섞여 나오지 않는다 — 열어서 비교만 하는 자리다.

경로 지도(step3 대응)는 **TMAP을 부르지 않는다.** 유료 API고 범례·팔레트
비교에는 필요 없다 — 프로덕션에도 이미 있는 폴백(한도 초과 시 직선 표시)을
항상 쓴다. 그래서 도로 실측 곡선 대신 점선 직선으로 나온다. 마커 팝업·
툴팁은 `step3_map.main`의 순수 함수(`visit_popup`·`visit_tooltip`)를 그대로
불러 쓴다 — 그 부분은 이번 개선 대상이 아니라서 새로 베끼지 않는다.

실행:
    python experiments/diagnostic/mapviz_compare.py
"""
import sys
import webbrowser
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
# step3_map/main.py는 `import module`로 형제 파일(step3_map/module.py)을
# 부른다 — python step3_map/main.py로 직접 실행할 때는 그 폴더가 자동으로
# sys.path에 들어가지만, 여기서는 패키지로 불러오므로 우리가 직접 넣어 줘야
# 형제 import가 풀린다.
sys.path.insert(0, str(_ROOT / "step3_map"))

import folium
from folium import plugins
import pandas as pd

from mapviz_shared import cluster_color, legend_html, swatch_circle, swatch_line

from project_config import DEPOT_ID, DEPOT_LAT, DEPOT_LON, DEPOT_NAME, MAP_TILES, PROJECT_ROOT
from step3_map.main import visit_popup, visit_tooltip
from step4_metrics.imbalance import demand_satisfaction

# 실제로 존재하는 산출물 한 쌍을 그대로 쓴다(같은 now·duration이라 세 지도가
# 서로 맞아떨어진다) — 파이프라인을 다시 돌리지 않는다.
NOW = "2026-05-21 18"
DURATION = "_05_10"

TOP_FILE = PROJECT_ROOT / "data/pp_data/ILP/후보" / f"top{DURATION} ({NOW}).csv"
VRP_FILE = PROJECT_ROOT / "data/pp_data/VRP" / f"VRP_plan{DURATION} ({NOW}).csv"

OUT_DIR = PROJECT_ROOT / "data/mapviz_compare"

ORIGINAL = {
    "step1 군집": PROJECT_ROOT / "data/pp_data/ILP/visualization" / f"clusterd_map{DURATION} ({NOW}).html",
    "step3 경로": PROJECT_ROOT / "data/pp_data/VRP/visualization" / f"vrp_map{DURATION} ({NOW}).html",
    "step4 재고 현황": PROJECT_ROOT / "data/pp_data/성능 지표/visualization" / f"imbalance_map{DURATION} ({NOW}).html",
}

DEPOT = {"id": DEPOT_ID, "name": DEPOT_NAME, "lat": DEPOT_LAT, "lon": DEPOT_LON}


def _base_map(center_lat: float, center_lon: float) -> folium.Map:
    return folium.Map(location=[center_lat, center_lon], zoom_start=13,
                      control_scale=True, tiles=MAP_TILES)


# ==================== 1) 군집 지도 (step1 대응) ====================

def build_cluster_map(pick_drop: pd.DataFrame) -> folium.Map:
    """st_visualization.py의 마커·레이어 구성은 그대로 두고, 팔레트만
    cluster_color()로, 범례는 legend_html()로 바꾼다(원래 범례가 없었다)."""
    m = _base_map(pick_drop["lat"].mean(), pick_drop["lon"].mean())
    unique_clusters = sorted(int(c) for c in pick_drop["cluster"].unique())

    for c in unique_clusters:
        color = cluster_color(c)
        for key, label in (("drop", "Drop"), ("pick", "Pick")):
            layer = folium.FeatureGroup(name=f"Cluster {c} - {label}", show=True)
            layer.add_to(m)
            mask = (pick_drop["cluster"] == c) & (
                (pick_drop["rebal_qty"] > 0) if key == "drop"
                else (pick_drop["rebal_qty"] <= 0))
            for _, row in pick_drop[mask].iterrows():
                rebal = row["rebal_qty"]
                status = "Drop (분배)" if rebal > 0 else "Pick (회수)"
                tooltip = (
                    f"<b>{row['station_name']}</b><br>"
                    f"유형: {status}<br>재배치 수량: {rebal}<br>"
                    f"현재 재고: {row['stock']}<br>클러스터: {c}<br>"
                    f"Target: {row['target_qty']:.2f}<br>"
                    f"mu: {row['mu']:.2f}, sigma: {row['sigma']:.2f}"
                )
                folium.CircleMarker(
                    location=[row["lat"], row["lon"]],
                    radius=max(5, abs(rebal) * 0.3),
                    color=color, weight=2, opacity=1.0,
                    fill=True, fill_color=color, fill_opacity=0.5,
                    tooltip=folium.Tooltip(tooltip, sticky=True),
                ).add_to(layer)

    legend_rows = [(swatch_circle(cluster_color(c), str(c)), f"군집 {c}")
                  for c in unique_clusters]
    m.get_root().html.add_child(folium.Element(legend_html(
        "범례 — 군집", legend_rows,
        note="원 크기는 재배치 수량입니다.<br>점에 커서를 대면 자세한 값이 뜹니다.")))
    folium.LayerControl(collapsed=False).add_to(m)
    return m


# ==================== 2) 경로 지도 (step3 대응, TMAP 미호출) ====================

def _route_points(cluster_df: pd.DataFrame, station_map: dict) -> list:
    """step3_map.main.make_vrp_map의 경로 구성 로직을 그대로 옮긴다 — depot
    시작(살짝 띄운 좌표) -> 경유지 -> depot 복귀."""
    pts = [{"id": DEPOT["id"], "name": DEPOT["name"],
           "lat": DEPOT["lat"] - 0.005, "lon": DEPOT["lon"]}]
    for _, row in cluster_df.iterrows():
        if row["action"] == "return":
            pts.append({"id": DEPOT["id"], "name": DEPOT["name"],
                       "lat": row["to_lat"], "lon": row["to_lon"],
                       "action": "return", "qty": 0})
            continue
        info = station_map[row["to_id"]]
        pts.append({"id": row["to_id"], "name": info["station_name"],
                   "lat": row["to_lat"], "lon": row["to_lon"],
                   "action": row["action"], "qty": row["qty"]})
    pts.append({"id": DEPOT["id"], "name": DEPOT["name"],
               "lat": DEPOT["lat"], "lon": DEPOT["lon"]})
    return pts


def build_route_map(pick_drop: pd.DataFrame, vrp_plan: pd.DataFrame,
                    capacity: int) -> folium.Map:
    m = _base_map(pick_drop["lat"].mean(), pick_drop["lon"].mean())
    station_map = pick_drop.set_index("station_id").to_dict("index")
    unique_clusters = sorted(int(c) for c in vrp_plan["cluster"].unique())

    for idx, c in enumerate(unique_clusters):
        fg = folium.FeatureGroup(name=f"Cluster {c}", show=True)
        fg.add_to(m)
        color = cluster_color(idx)
        cluster_df = vrp_plan[vrp_plan["cluster"] == c].copy()
        route_pts = _route_points(cluster_df, station_map)

        # TMAP을 부르지 않으므로 방문 순서대로 이은 직선 하나, 점선으로 표시한다
        # (프로덕션에도 이미 있는 '한도 초과 시' 폴백과 같은 표현이다).
        latlon = [[p["lat"], p["lon"]] for p in route_pts]
        poly = folium.PolyLine(latlon, weight=5, color=color, opacity=0.7,
                               dash_array="8,6").add_to(fg)
        plugins.PolyLineTextPath(
            poly, "▶     ", repeat=True, offset=6,
            attributes={"fill": color, "font-weight": "bold", "font-size": "12"},
        ).add_to(fg)

        folium.Marker(
            location=[DEPOT["lat"] - 0.0003, DEPOT["lon"]],
            tooltip=folium.Tooltip(f"<b>출발 · {DEPOT['name']}</b><br>클러스터 {c}", sticky=True),
            icon=folium.Icon(color="green", icon="play"),
        ).add_to(fg)

        station_visits: dict = {}
        current_load = 0
        for visit_idx, row in cluster_df.reset_index(drop=True).iterrows():
            if row["action"] == "return":
                current_load = 0
                continue
            sid, action, qty = row["to_id"], row["action"], row["qty"]
            visit_order = visit_idx + 2
            if action == "pick":
                current_load += qty
                action_txt = "Pick (회수)"
            else:
                current_load -= qty
                action_txt = "Drop (분배)"
            visit = station_visits.setdefault(sid, {
                "lat": row["to_lat"], "lon": row["to_lon"],
                "name": (station_map.get(sid, {}) or {}).get("station_name") or sid,
                "orders": [], "records": [],
            })
            visit["orders"].append(str(visit_order))
            visit["records"].append({
                "cluster": c, "order": visit_order, "round": len(visit["orders"]),
                "action": action_txt, "qty": qty, "load": current_load,
            })

        folium.Marker(
            location=[DEPOT["lat"], DEPOT["lon"]],
            tooltip=folium.Tooltip(
                f"<b>도착 · {DEPOT['name']}</b><br>클러스터 {c}<br>"
                f"최종 적재 {current_load}/{capacity}", sticky=True),
            icon=folium.Icon(color="red", icon="stop"),
        ).add_to(fg)

        for sid, data in station_visits.items():
            popup = visit_popup(data["records"], sid, capacity, lambda order: "-")
            folium.Marker(
                location=[data["lat"], data["lon"]],
                tooltip=folium.Tooltip(visit_tooltip(data["records"], data["name"]), sticky=True),
                popup=folium.Popup(f'<div style="width:420px;">{popup}</div>', max_width=500),
                icon=folium.DivIcon(html=f"""
                    <div style="background-color:purple;color:white;border-radius:50%;
                        width:32px;height:32px;text-align:center;line-height:32px;
                        font-size:12px;font-weight:bold;border:2px solid white;">
                        {','.join(data['orders'])}
                    </div>"""),
            ).add_to(fg)

    legend_rows = [(swatch_circle("green"), "출발 (차고지)"),
                  (swatch_circle("red"), "도착 (차고지 복귀)"),
                  (swatch_circle("purple", "3"), "방문 순서")]
    legend_rows += [(swatch_line(cluster_color(i), dashed=True), f"군집 {c} 경로")
                    for i, c in enumerate(unique_clusters)]
    m.get_root().html.add_child(folium.Element(legend_html(
        "범례 — 경로", legend_rows,
        note="이 비교본은 TMAP을 부르지 않아 모든 경로가 점선 직선입니다"
             "(실제 산출물은 도로선입니다).<br>점에 커서를 대면 요약이,"
             " 누르면 자세한 내용이 뜹니다.")))
    folium.LayerControl(collapsed=False).add_to(m)
    return m


# ==================== 3) 재고 현황 지도 (step4 대응) ====================

def build_imbalance_map(pick_drop: pd.DataFrame) -> folium.Map:
    """step4_metrics.imbalance.demand_satisfaction()를 그대로 불러 쓰고,
    마커 로직도 원본과 같다 — 범례만 통일한다(원래 영어·회색 테두리였다)."""
    imbalance_df = demand_satisfaction(pick_drop)
    m = _base_map(imbalance_df["lat"].mean(), imbalance_df["lon"].mean())
    unique_clusters = sorted(int(c) for c in imbalance_df["cluster"].unique())

    for c in unique_clusters:
        fg = folium.FeatureGroup(name=f"Cluster {c}", show=True)
        fg.add_to(m)
        for _, row in imbalance_df[imbalance_df["cluster"] == c].iterrows():
            drop = row["rebal_qty"] > 0
            color = "red" if drop else "blue"
            status = "Drop" if drop else "Pick"
            tooltip = (
                f"<b>{row['station_name']}</b><br>군집 : {c}<br>"
                f"작업 유형 : {status}<br>재배치 수량 : {row['rebal_qty']}<br>"
                f"작업 전 재고 : {row['stock']} → 작업 후 : {row['new_stock']}<br>"
                f"목표 재고 : {row['target_qty']:.1f}"
            )
            popup = (
                f"<b>{row['station_name']}</b><br>군집 : {c}<br>"
                f"작업 유형 : {status}<br>재배치 수량 : {row['rebal_qty']}<br>"
                f"작업 전 재고 : {row['stock']}<br>작업 후 재고 : {row['new_stock']}<br>"
                f"목표 재고 : {row['target_qty']:.1f}<br>"
                f"작업 전 불균형 : {row['bf_imbalance']:.2f}<br>"
                f"작업 후 불균형 : {row['af_imbalance']:.2f}<br>"
                f"개선량 : {row['improvement']:.2f}<br>"
                f"개선률 : {row['improvement_rate'] * 100:.1f}%"
            )
            folium.CircleMarker(
                location=[row["lat"], row["lon"]],
                radius=max(3, row["improvement"]),
                color=color, weight=2, opacity=1.0,
                fill=True, fill_color=color,
                fill_opacity=(0.2 + 0.5 * row["improvement_rate"]),
                tooltip=folium.Tooltip(tooltip, sticky=True),
                popup=folium.Popup(popup, max_width=260),
            ).add_to(fg)

    legend_rows = [(swatch_circle("red"), "Drop — 부족 해소"),
                  (swatch_circle("blue"), "Pick — 과잉 해소")]
    m.get_root().html.add_child(folium.Element(legend_html(
        "범례 — 재고 현황", legend_rows,
        note="원 크기는 불균형 해소량, 원 투명도는 개선률입니다.")))
    folium.LayerControl(collapsed=False).add_to(m)
    return m


# ==================== 실행 ====================

def _index_page(paths: dict) -> str:
    rows = []
    for title, v2_path in paths.items():
        orig = ORIGINAL.get(title)
        orig_link = (f'<a href="file:///{orig.as_posix()}" target="_blank">기존 산출물 ↗</a>'
                    if orig and orig.exists() else "기존 산출물 없음")
        rows.append(f"""
        <tr>
          <td>{title}</td>
          <td><a href="{v2_path.name}" target="_blank">비교본(범례 통일 + 색맹 안전 팔레트) ↗</a></td>
          <td>{orig_link}</td>
        </tr>""")
    return f"""<!doctype html><html lang="ko"><meta charset="utf-8">
    <title>지도 시각화 비교</title>
    <style>
      body {{ font-family: -apple-system, 'Segoe UI', 'Malgun Gothic', sans-serif;
             max-width: 720px; margin: 40px auto; color: #1a1a1a; }}
      table {{ border-collapse: collapse; width: 100%; }}
      td, th {{ border-bottom: 1px solid #ddd; padding: 10px 8px; text-align: left; }}
      caption {{ text-align: left; color: #555; margin-bottom: 12px; font-size: 14px; }}
    </style>
    <h1>지도 시각화 비교 — {NOW} {DURATION}</h1>
    <p>왼쪽 링크는 mapviz_shared.py(범례 통일 + 색맹 안전 팔레트)로 다시 그린
    비교본, 오른쪽은 실제 파이프라인이 만든 기존 산출물입니다. 새 탭으로 열어
    나란히 두고 비교하세요.</p>
    <table>
      <tr><th>지도</th><th>비교본</th><th>기존 산출물</th></tr>
      {''.join(rows)}
    </table>
    """


def main():
    if not TOP_FILE.exists() or not VRP_FILE.exists():
        raise SystemExit(
            f"입력 파일이 없습니다 — NOW/DURATION을 이 저장소에 실제로 있는 "
            f"산출물로 맞춰 주세요.\n  {TOP_FILE}\n  {VRP_FILE}")

    pick_drop = pd.read_csv(TOP_FILE, encoding="utf-8")
    vrp_plan = pd.read_csv(VRP_FILE, encoding="utf-8")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    outputs = {
        "step1 군집": OUT_DIR / f"cluster_v2{DURATION} ({NOW}).html",
        "step3 경로": OUT_DIR / f"route_v2{DURATION} ({NOW}).html",
        "step4 재고 현황": OUT_DIR / f"imbalance_v2{DURATION} ({NOW}).html",
    }

    build_cluster_map(pick_drop).save(str(outputs["step1 군집"]))
    print(f"저장: {outputs['step1 군집']}")

    from project_config import VEHICLE_CAPACITY
    build_route_map(pick_drop, vrp_plan, VEHICLE_CAPACITY).save(str(outputs["step3 경로"]))
    print(f"저장: {outputs['step3 경로']}")

    build_imbalance_map(pick_drop).save(str(outputs["step4 재고 현황"]))
    print(f"저장: {outputs['step4 재고 현황']}")

    index_path = OUT_DIR / "index.html"
    index_path.write_text(_index_page(outputs), encoding="utf-8")
    print(f"\n비교 페이지: {index_path}")

    try:
        webbrowser.open(index_path.as_uri())
    except Exception:
        pass


if __name__ == "__main__":
    main()
