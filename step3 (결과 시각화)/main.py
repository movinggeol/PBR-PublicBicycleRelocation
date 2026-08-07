import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import folium
from folium import plugins
from dotenv import load_dotenv

from module import (
    seconds_to_hms,
    call_tmap_chunked,
    merge_tmap_results,
)

from project_config import (
    DEPOT_ID, DEPOT_LAT, DEPOT_LON, DEPOT_NAME, PROJECT_ROOT, VEHICLE_CAPACITY,
    duration_list, ensure_output_dirs, get_runtime_config,
)

'''
<구조>
main
 ├─ csv 읽기
 ├─ API 설정
 └─ make_vrp_map() 호출
        ├─ 지도 생성
        ├─ 클러스터별 경로 시각화
        ├─ Tmap API 호출
        ├─ 마커 생성
        └─ html 저장
'''
'''
<순서>
VRP 결과(csv) 읽기
    ↓
클러스터별 경로 생성
    ↓
Tmap API로 실제 도로 경로 요청
    ↓
Folium으로 지도 위에 선/마커 표시
    ↓
방문 순서, 적재량, 도착시간 표시
    ↓
HTML 지도 저장
'''

# ---------------- 설정 ----------------
vrp_plan_file = str(PROJECT_ROOT / "data/pp_data/VRP/VRP_plan{duration} ({now}).csv")
clustered_file = str(PROJECT_ROOT / "data/pp_data/ILP/후보/top{duration} ({now}).csv")

result_path = str(PROJECT_ROOT / "data/pp_data/VRP/visualization/vrp_map{duration} ({now}).html")

config = get_runtime_config()
now = config.now

# 차고지: 프로젝트 공통 상수 (vrp.py와 동일한 depot)
depot = {
    "id": DEPOT_ID,
    "name": DEPOT_NAME,
    "lat": DEPOT_LAT,
    "lon": DEPOT_LON
}

vehicle_capacity = VEHICLE_CAPACITY

def make_vrp_map(depot: dict, pick_drop: pd.DataFrame, vrp_plan: pd.DataFrame,
                 duration: str, headers: dict, tmap_url: str):
    
    center_lat = pick_drop['lat'].mean()
    center_lon = pick_drop['lon'].mean()

    #m = folium.Map(location=[center_lat, center_lon],
    #               zoom_start=13,
    #               control_scale=True)
    #m.get_root().header.add_child(folium.Element('<meta name="referrer" content="no-referrer-when-downgrade">'))
    
    
    
    # 수정 코드 (CartoDB 타일 사용 - Referer 제한에서 비교적 자유로움)
    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=13,
        control_scale=True,
        tiles='CartoDB positron'        # 밝은 배경 지도
        # tiles="CartoDB dark_matter"   # 어두운 배경 지도
    )
    

    unique_clusters = sorted(vrp_plan['cluster'].unique())

    palette = [
        'red','blue','green','purple','orange',
        'darkred','cadetblue','darkgreen','pink', 'black', 
        'darkblue', 'darkpurple', 'lightblue', 'lightgreen', 'gray'
    ]

    # ---------------- cluster별 처리 ----------------
    station_map = pick_drop.set_index('station_id').to_dict('index')
    

    for idx, c in enumerate(unique_clusters):

        station_visits = {}
        
        fg = folium.FeatureGroup(name=f"Cluster {c}", show=True)
        fg.add_to(m)

        cluster_df = vrp_plan[vrp_plan["cluster"]==c].copy()

        current_load = 0

        color = palette[idx % len(palette)]

        # --------- 경로 구성 ----------
        route_pts = []

        # depot 시작
        route_pts.append({
            "id": depot["id"],
            "name": depot["name"],
            "lat": depot["lat"] - 0.005,
            "lon": depot["lon"]
        })
        
        for _, row in cluster_df.iterrows():

            if row["action"] == "return":
                # depot 복귀도 경유지로 포함 (station_map에 depot이 없으므로 별도 처리)
                route_pts.append({
                    "id": depot["id"],
                    "name": depot["name"],
                    "lat": row["to_lat"],
                    "lon": row["to_lon"],
                    "action": "return",
                    "qty": 0
                })
                continue

            info = station_map[row["to_id"]]
            route_pts.append({
                "id": row["to_id"],
                "name": info["station_name"],
                "lat": row["to_lat"],
                "lon": row["to_lon"],
                "action": row["action"],
                "qty": row["qty"]
            })

        # depot 복귀
        route_pts.append({
            "id": depot["id"],
            "name": depot["name"],
            "lat": depot["lat"],
            "lon": depot["lon"]
        })

        # --------- Tmap 요청 ----------
        start = {
            "name": route_pts[0]["name"],
            "X": str(route_pts[0]["lon"]),
            "Y": str(route_pts[0]["lat"])
        }

        end = {
            "name": route_pts[-1]["name"],
            "X": str(route_pts[-1]["lon"]),
            "Y": str(route_pts[-1]["lat"])
        }

        via = []
        for p in route_pts[1:-1]:
            via.append({
                "viaPointId": p["id"],
                "viaPointName": p["name"],
                "viaX": str(p["lon"]),
                "viaY": str(p["lat"])
            })

        # 경유지가 30개를 넘으면 module이 구간을 분할 호출한 뒤 병합
        geo_list = call_tmap_chunked(start, end, via,
                                     headers=headers,
                                     url=tmap_url)
        merged = merge_tmap_results(geo_list)
        elapsed_list = merged["elapsed_sec"]

        def _arrival_txt(order: int) -> str:
            idx = order - 1
            if 0 <= idx < len(elapsed_list):
                return seconds_to_hms(elapsed_list[idx]) or "-"
            return "-"

        # --------- 도로 선 그리기 ----------
        for feat in merged["features"]:
            geom = feat.get("geometry", {})
            if geom.get("type")=="LineString":
                coords = geom["coordinates"]
                latlon = [[c[1], c[0]] for c in coords]

                poly = folium.PolyLine(
                    latlon,
                    weight=5,
                    color=color,
                    opacity=0.7
                ).add_to(fg)

                plugins.PolyLineTextPath(
                    poly,
                    "▶     ",
                    repeat=True,
                    offset=6,
                    attributes={
                        "fill": color,
                        "font-weight": "bold",
                        "font-size": "12"
                    }
                ).add_to(fg)

        # --------- 마커 + 적재량 ----------
        visit_counter = {}
        current_load = 0

        folium.Marker(
            location=[depot["lat"]-0.0003, depot["lon"]],
            tooltip="출발",
            icon=folium.Icon(color="green", icon="play")
        ).add_to(fg)

        for visit_idx, row_data in cluster_df.reset_index(drop=True).iterrows():

            sid = row_data["to_id"]
            lat = row_data["to_lat"]
            lon = row_data["to_lon"]
            action = row_data["action"]
            qty = row_data["qty"]

            visit_order = visit_idx + 2   # 1번은 depot

            if action == "return":
                # depot 복귀: 마커는 만들지 않고 적재량만 초기화 (방문 번호는 유지)
                current_load = 0
                continue

            # --- 적재량 계산 ---
            if action == "pick":
                current_load += qty
                action_txt = "Pick (회수)"
                base_color = "blue"
            else:
                current_load -= qty
                action_txt = "Drop (분배)"
                base_color = "orange"

            # 방문 정보 저장
            if sid not in station_visits:
                station_visits[sid] = {
                    "lat": lat,
                    "lon": lon,
                    "orders": [],
                    "records": []
                }

            station_visits[sid]["orders"].append(str(visit_order))

            station_visits[sid]["records"].append({
                "cluster": c,
                "order": visit_order,
                "round": len(station_visits[sid]["orders"]),
                "action": action_txt,
                "qty": qty,
                "load": current_load
            })

        # --- 도착 정보 계산 ---
        total_time_txt = (seconds_to_hms(elapsed_list[-1]) if elapsed_list else None) or "-"

        arrival_popup = (
            f"<b>도착 (depot)</b><br>"
            f"Cluster : {c}<br>"
            f"<b>총 누적 작업시간</b> : {total_time_txt}<br>"
            f"<b>최종 적재량</b> : {current_load}/{vehicle_capacity}"
        )

        folium.Marker(
            location=[depot["lat"], depot["lon"]],
            tooltip=f"[클:{c}] 도착",
            popup=folium.Popup(
                f"""
                <div style="width:420px;">
                {arrival_popup}
                </div>
                """,
                max_width=500
            ),
            icon=folium.Icon(color="red", icon="stop")
        ).add_to(fg)

        for sid, data in station_visits.items():

            lat = data["lat"]
            lon = data["lon"]
            orders_str = ",".join(data["orders"])

            popup_parts = []

            if len(data["records"]) == 1:
                r = data["records"][0]

                popup_parts.append(
                    f"Cluster : {r['cluster']}<br>"
                    f"대여소ID : {sid}<br>"
                    f"방문 순서 : {r['order']}<br>"
                    f"작업 유형 : {r['action']}<br>"
                    f"재배치 수량 : {r['qty']}<br>"
                    f"현재 적재량 : {r['load']}/{vehicle_capacity}<br>"
                    f"누적 도착시간 : {_arrival_txt(r['order'])}"
                )

            else:
                for r in data["records"]:
                    popup_parts.append(
                        f"Cluster : {r['cluster']}<br>"
                        f"대여소ID : {sid}<br>"
                        f"방문 순서 : {r['order']}<br>"
                        f"방문 회차 : {r['round']}번째 방문<br>"
                        f"작업 유형 : {r['action']}<br>"
                        f"재배치 수량 : {r['qty']}<br>"
                        f"현재 적재량 : {r['load']}/{vehicle_capacity}<br>"
                        f"누적 도착시간 : {_arrival_txt(r['order'])}"
                    )

                popup_parts = "<br>-------------------------<br>".join(popup_parts)

            folium.Marker(
                location=[lat, lon],
                tooltip=f"[클:{c}] [{sid}] [{orders_str}]",
                popup=folium.Popup(
                    f"""
                    <div style="width:420px;">
                    {popup_parts}
                    </div>
                    """,
                    max_width=500
                ),
                icon=folium.DivIcon(html=f"""
                    <div style="
                        background-color:purple;
                        color:white;
                        border-radius:50%;
                        width:32px;
                        height:32px;
                        text-align:center;
                        line-height:32px;
                        font-size:12px;
                        font-weight:bold;
                        border:2px solid white;
                    ">
                        {orders_str}
                    </div>
                """)
            ).add_to(fg)
    # --------- Legend ----------
    legend_html = """
    <div style="position: fixed; bottom: 30px; left: 30px; 
                background: white; padding: 10px; 
                border:2px solid gray; z-index:9999;">
      <b>Legend</b><br>
      🟢 출발<br>
      🔴 도착<br>
      ▶ 차량 이동 방향<br>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    folium.LayerControl(collapsed=False).add_to(m)

    m.save(result_path.format(duration=duration, now=now))

    print("지도 생성 완료")
    print("result_path 파일이 저장되었습니다.", (result_path.format(duration=duration, now=now)))


# ---------------- 메인 ----------------
if __name__ == "__main__":

    load_dotenv(PROJECT_ROOT / ".env")
    API_KEY = os.getenv("API_KEY")

    if not API_KEY:
        raise RuntimeError("API_KEY 환경변수 설정 필요")

    TMAP_URL = "https://apis.openapi.sk.com/tmap/routes/routeSequential30"
    HEADERS  = {
        "accept":"application/json",
        "appKey":API_KEY,
        "content-type":"application/json"
    }

    ensure_output_dirs()

    for duration in duration_list(config):
        vrp_plan = pd.read_csv(
            vrp_plan_file.format(duration=duration, now=now),
            encoding="utf-8"
        )

        pick_drop = pd.read_csv(
            clustered_file.format(duration=duration, now=now),
            encoding="utf-8"
        )

        make_vrp_map(depot, pick_drop, vrp_plan, duration, HEADERS, TMAP_URL)
