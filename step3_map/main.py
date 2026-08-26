import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import folium
from folium import plugins
from dotenv import load_dotenv

import module
from module import (
    TmapBudgetExceeded,
    TmapQuotaExceeded,
    call_count,
    seconds_to_hms,
    call_tmap_chunked,
    merge_tmap_results,
)

from project_config import (
    DEPOT_ID, DEPOT_LAT, DEPOT_LON, DEPOT_NAME, MAP_TILES, PROJECT_ROOT,
    VEHICLE_CAPACITY,
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

VISIT_SEPARATOR = "<br>-------------------------<br>"


def visit_popup(records: list, station_id: str, capacity: int, arrival_txt) -> str:
    """대여소 하나의 방문 기록을 팝업 HTML **문자열**로 만든다.

    **문자열로 돌려주는 것이 요점이다.** 예전에는 여러 번 방문한 경우에만 join하고
    한 번뿐이면 리스트를 그대로 f-string에 넣어, 팝업에
    `['클러스터 : 3<br>대여소 : ST0123<br>…']`처럼 대괄호와 따옴표가 찍혔다(1.20.3).
    방문이 한 번인 대여소가 대부분이라 거의 모든 팝업이 그랬다.

    한 번만 들른 대여소에는 '방문 회차'를 적지 않는다 — 1번째 방문뿐이라 뜻이 없다.
    """
    single = len(records) == 1
    parts = []
    for record in records:
        lines = [
            f"클러스터 : {record['cluster']}",
            f"대여소 : {station_id}",
            f"방문 순서 : {record['order']}",
        ]
        if not single:
            lines.append(f"방문 회차 : {record['round']}번째 방문")
        lines += [
            f"작업 유형 : {record['action']}",
            f"재배치 수량 : {record['qty']}",
            f"현재 적재량 : {record['load']}/{capacity}",
            f"누적 도착시간 : {arrival_txt(record['order'])}",
        ]
        parts.append("<br>".join(lines))
    return VISIT_SEPARATOR.join(parts)


def visit_tooltip(records: list, station_name: str) -> str:
    """커서를 댔을 때 뜨는 **요약**. 눌러서 여는 팝업(`visit_popup`)의 앞자리다.

    누르기 전에 알고 싶은 것만 담는다 — **어디를(이름), 무엇을(작업), 몇 대**.
    좌표·적재량·누적 시간처럼 따져 볼 것은 팝업에 남긴다. 한 대여소를 두 번
    들르면 줄을 둘로 나눈다.

    ⚠️ 이름을 앞에 둔다. 예전에는 대여소 **ID**만 떠서(`ST0123`) 커서를 대도
    어디인지 알 수 없었다 — 기사가 아는 것은 이름이다.
    """
    head = f"<b>{station_name}</b>"
    lines = [
        f"{r['order']}번째 · {r['action']} {r['qty']}대"
        for r in records
    ]
    return head + "<br>" + "<br>".join(lines)


def make_vrp_map(depot: dict, pick_drop: pd.DataFrame, vrp_plan: pd.DataFrame,
                 duration: str, headers: dict, tmap_url: str):
    
    center_lat = pick_drop['lat'].mean()
    center_lon = pick_drop['lon'].mean()

    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=13,
        control_scale=True,
        tiles=MAP_TILES                 # 세 지도가 같은 배경을 써야 한다(project_config)
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

        # 경유지가 100개를 넘으면 module이 구간을 분할 호출한 뒤 병합.
        # TMAP은 호출 한도가 있는 유료 API이므로, 한도·예산이 걸리면 지도만
        # 직선 경로로 낮춰 그리고 파이프라인은 계속 진행한다.
        merged = None
        try:
            geo_list = call_tmap_chunked(start, end, via,
                                         headers=headers,
                                         url=tmap_url)
            merged = merge_tmap_results(geo_list)
        except (TmapQuotaExceeded, TmapBudgetExceeded) as exc:
            print(f"  ⚠ 클러스터 {c}: {exc}")
            print("    → 이 경로는 직선으로 그립니다 (도로 경로·도착 시각 없음)")
        except RuntimeError as exc:
            print(f"  ⚠ 클러스터 {c}: TMAP 호출 실패 ({exc}) → 직선으로 그립니다")

        elapsed_list = merged["elapsed_sec"] if merged else []

        def _arrival_txt(order: int) -> str:
            idx = order - 1
            if 0 <= idx < len(elapsed_list):
                return seconds_to_hms(elapsed_list[idx]) or "-"
            return "-"

        # --------- 도로 선 그리기 ----------
        if merged:
            segments = [
                [[pt[1], pt[0]] for pt in feat["geometry"]["coordinates"]]
                for feat in merged["features"]
                if feat.get("geometry", {}).get("type") == "LineString"
            ]
            dashed = None
        else:
            # 대체 표시: 방문 순서대로 이은 직선 하나 (점선으로 구분)
            segments = [[[p["lat"], p["lon"]] for p in route_pts]]
            dashed = "8,6"

        for latlon in segments:
            poly = folium.PolyLine(
                latlon,
                weight=5,
                color=color,
                opacity=0.7,
                dash_array=dashed,
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
            tooltip=folium.Tooltip(
                f"<b>출발 · {depot['name']}</b><br>클러스터 {c}", sticky=True),
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
                    # 커서 요약에 쓴다 — 기사가 아는 것은 ID가 아니라 이름이다.
                    # pick_drop에 없는 대여소는 ID로 물러선다.
                    "name": (station_map.get(sid, {}) or {}).get("station_name") or sid,
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
            f"클러스터 : {c}<br>"
            f"<b>총 누적 작업시간</b> : {total_time_txt}<br>"
            f"<b>최종 적재량</b> : {current_load}/{vehicle_capacity}"
        )

        folium.Marker(
            location=[depot["lat"], depot["lon"]],
            tooltip=folium.Tooltip(
                f"<b>도착 · {depot['name']}</b><br>클러스터 {c}<br>"
                f"총 작업시간 {total_time_txt}<br>"
                f"최종 적재 {current_load}/{vehicle_capacity}", sticky=True),
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

            popup_parts = visit_popup(data["records"], sid, vehicle_capacity,
                                      _arrival_txt)

            folium.Marker(
                location=[lat, lon],
                tooltip=folium.Tooltip(visit_tooltip(data["records"], data["name"]),
                                       sticky=True),
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
    # 범례. 화면의 나머지가 한국어이므로 여기도 한국어로 적고, 지도에 실제로
    # 있는 것만 담는다(보라 원=방문 번호, 파랑/주황=작업 종류).
    # 색을 값으로 읽게 두지 않는다 — 글자를 함께 적는다 (docs/구현/DESIGN.md).
    legend_html = """
    <div style="position: fixed; bottom: 24px; left: 24px; z-index: 9999;
                background: rgba(255,255,255,.94);
                -webkit-backdrop-filter: blur(6px); backdrop-filter: blur(6px);
                padding: 12px 14px; border-radius: 10px;
                border: 1px solid rgba(0,0,0,.14);
                box-shadow: rgba(0,0,0,.22) 3px 5px 30px 0;
                font: 13px/1.7 -apple-system, 'Segoe UI', 'Malgun Gothic', sans-serif;
                color: #1a1a1a;">
      <div style="font-weight:700; margin-bottom:6px;">범례</div>
      🟢 출발 (차고지)<br>
      🔴 도착 (차고지 복귀)<br>
      <span style="display:inline-block;width:15px;height:15px;border-radius:50%;
                   background:purple;color:#fff;font-size:9px;font-weight:700;
                   text-align:center;line-height:15px;vertical-align:-3px;">3</span>
      방문 순서<br>
      ▶ 차량 이동 방향
      <div style="margin-top:7px; padding-top:7px; border-top:1px solid rgba(0,0,0,.12);
                  color:#555; font-size:12px;">
        점에 커서를 대면 요약이,<br>누르면 자세한 내용이 뜹니다.
      </div>
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

    # 엔드포인트는 module이 요청마다 고른다 — routeSequential30을 먼저 쓰고,
    # 일일 한도를 소진하면(429 QUOTA_EXCEEDED) routeSequential100으로 자동 전환한다.
    # 두 엔드포인트의 한도가 따로 잡히므로 이렇게 쓰면 하루치 호출이 늘어난다.
    # PBR_TMAP_URL을 주면 그 엔드포인트만 쓰고 폴백하지 않는다(수동 검증용).
    TMAP_URL = os.getenv("PBR_TMAP_URL")     # None이면 자동 선택
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

    print(f"\nTMAP 호출 {call_count()}건 (예산 {module.MAX_CALLS}건)")
    남은_엔드포인트 = [e.name for e in module.available_endpoints()]
    print(f"  사용 가능한 엔드포인트: {', '.join(남은_엔드포인트) or '없음(모두 한도 소진)'}")
