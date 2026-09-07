import os
import sys
from datetime import datetime
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
    start_time_for,
)

from mapviz import (DROP_WORD, PICK_WORD, cluster_color, legend_html,
                    swatch_circle, swatch_line)
from project_config import (
    DATA_ROOT, DEPOT_ID, DEPOT_LAT, DEPOT_LON, DEPOT_NAME, MAP_TILES, PROJECT_ROOT,
    VEHICLE_CAPACITY,
    duration_list, ensure_output_dirs, get_runtime_config,
)
import db

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
vrp_plan_file = str(DATA_ROOT / "pp_data/VRP/VRP_plan{duration} ({now}).csv")
clustered_file = str(DATA_ROOT / "pp_data/ILP/후보/top{duration} ({now}).csv")

result_path = str(DATA_ROOT / "pp_data/VRP/visualization/vrp_map{duration} ({now}).html")

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


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    """두 지점의 직선거리(km). ILP·VRP가 쓰는 것과 같은 계산이다."""
    import math

    r = 6371.0
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dp = p2 - p1
    dl = math.radians(float(lon2) - float(lon1))
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def _road_legs(cluster: int, route_pts: list, elapsed_sec: list,
               start_time: str = None) -> list:
    """TMAP이 준 누적 소요를 **구간별 실측**으로 풀어 낸다 (1.23.2).

    `elapsed_sec`는 방문 지점마다의 누적 초이므로, 앞 값과 빼면 그 구간의
    실제 도로 소요가 된다. 지점 수와 길이가 어긋나거나 값이 비면 건너뛴다
    (TMAP 한도에 걸리면 직선으로 낮춰 그리므로 실측이 없다).

    **직선거리를 함께 남기는 것이 요점이다.** ILP는 계획 시점에 도로거리를
    모르고 직선거리만 아는데, 배워야 할 것은 바로 그 둘의 관계다.
    """
    if not elapsed_sec or len(elapsed_sec) < 2:
        return []

    rows = []
    # ⚠️ **leg 0은 버린다** (1.26.129). TMAP 요청의 출발점은 차고지가 아니라
    # 차고지에서 남쪽으로 0.005도(약 555m) 민 자리다 — 출발지와 도착지가
    # 같으면 경유지 최적화가 성립하지 않아서 벌려 둔 것이다(아래 `start` 참고).
    # 그래서 첫 구간은 직선거리도 도로 소요도 **있지도 않은 지점**을 기준으로
    # 잰 값이다. 이 표는 이동시간 모형을 적합하는 정답표이므로 섞이면 안 된다.
    # (실측: 1,705구간 중 29건이 그랬고, 빼고 다시 적합하면 고정비 332.9→332.3초,
    #  거리계수 113.3→112.4 s/km — 결론을 바꿀 크기는 아니었지만 거짓은 거짓이다.)
    for i in range(1, min(len(route_pts), len(elapsed_sec)) - 1):
        here, nxt = elapsed_sec[i], elapsed_sec[i + 1]
        if here is None or nxt is None:
            continue
        gap = float(nxt) - float(here)
        if gap <= 0:                    # 같은 자리를 두 번 들르면 0이 나온다
            continue
        a, b = route_pts[i], route_pts[i + 1]
        rows.append({
            "cluster": int(cluster),
            "leg": i,
            "from_id": a.get("id"),
            "to_id": b.get("id"),
            "from_lat": a.get("lat"), "from_lon": a.get("lon"),
            "to_lat": b.get("lat"), "to_lon": b.get("lon"),
            "straight_km": round(_haversine_km(
                a["lat"], a["lon"], b["lat"], b["lon"]), 4),
            "road_sec": round(gap, 1),
            # 언제 잰 값인지 남긴다. 배율은 교통 상황에 따라 달라지므로
            # **측정 시각 없이는 나중에 해석할 수 없다** (1.26.7).
            "observed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            # 어느 시각의 교통량으로 계산됐는지 — 호출 시각과 다른 값이다.
            "start_time": start_time,
        })
    return rows


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
    

    road_rows = []                  # TMAP 실측 구간 (정답표). 아래에서 DB에 남긴다
    unique_clusters = sorted(vrp_plan['cluster'].unique())
    # 이 회차의 교통량 기준 시각. 클러스터마다 다시 구하면 자정을 넘길 때
    # 앞뒤 클러스터가 다른 날을 보게 된다 — 한 번만 정하고 돌려 쓴다.
    tmap_start_time = start_time_for(duration)

    # 경로 색은 mapviz.cluster_color()가 정한다 — 세 지도가 한 벌을 쓴다.
    # 예전 목록은 red/darkred, green/darkgreen/lightgreen처럼 인접한 색이
    # 많았다(1.26.75 조사 → 1.26.80 채택).

    # ---------------- cluster별 처리 ----------------
    station_map = pick_drop.set_index('station_id').to_dict('index')
    

    for c in unique_clusters:

        station_visits = {}
        
        fg = folium.FeatureGroup(name=f"Cluster {c}", show=True)
        fg.add_to(m)

        cluster_df = vrp_plan[vrp_plan["cluster"]==c].copy()

        current_load = 0

        # ⚠️ **군집 번호로 색을 정한다 — 목록의 자리(idx)가 아니다** (1.26.129).
        # ILP가 이동을 못 만든 군집은 VRP 계획에서 빠지므로 자리와 번호가
        # 어긋난다. 실산출물 11쌍 중 3쌍(27%)에서 실제로 갈렸고
        # (obs-cmp-1520 _15_20은 군집 5·11이 빠져 6번부터 14개가 밀렸다),
        # 그러면 step1의 군집 지도와 이 경로 지도가 **같은 군집을 다른 색으로**
        # 그린다 — 두 화면을 나란히 놓고 보는 사람에게는 다른 군집이 된다.
        color = cluster_color(c)

        # --------- 경로 구성 ----------
        route_pts = []

        # depot 시작. **좌표는 실제 차고지 그대로 둔다** (1.26.129).
        # 예전에는 여기에 `depot["lat"] - 0.005`(약 555m 남쪽)를 박아 뒀는데,
        # 그 자리가 지도에도 그대로 그려져(한도 초과 시 직선 대체 경로) 차고지에서
        # 뻗어 나가는 555m짜리 헛선이 생겼다. TMAP에 출발·도착을 벌려 보내야 하는
        # 사정은 아래 `start`에서만 처리한다.
        route_pts.append({
            "id": depot["id"],
            "name": depot["name"],
            "lat": depot["lat"],
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
        # ⚠️ 출발점만 남쪽으로 조금 민다. 차량은 차고지에서 나와 차고지로 돌아오는데
        # TMAP 경유지 최적화는 출발지와 도착지가 **같은 좌표면 성립하지 않는다**.
        # 이 어긋남이 실측 표(road_leg)에 새지 않도록 `_road_legs()`가 leg 0을
        # 버린다 — 지도에 그리는 경로(route_pts)에는 진짜 차고지가 들어간다.
        TMAP_START_OFFSET_DEG = 0.005          # 약 555m
        start = {
            "name": route_pts[0]["name"],
            "X": str(route_pts[0]["lon"]),
            "Y": str(route_pts[0]["lat"] - TMAP_START_OFFSET_DEG)
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
            # 출동 시각을 넘긴다 — searchOption이 교통최적이므로 **그 시각의
            # 교통량**으로 계산된다. 1.26.4까지 2017년 저녁으로 고정돼 있어
            # 새벽 회차에도 퇴근길 교통량이 적용됐다.
            geo_list = call_tmap_chunked(start, end, via,
                                         headers=headers,
                                         url=tmap_url,
                                         start_time=tmap_start_time)
            merged = merge_tmap_results(geo_list)
        except (TmapQuotaExceeded, TmapBudgetExceeded) as exc:
            print(f"  ⚠ 클러스터 {c}: {exc}")
            print("    → 이 경로는 직선으로 그립니다 (도로 경로·도착 시각 없음)")
        except RuntimeError as exc:
            print(f"  ⚠ 클러스터 {c}: TMAP 호출 실패 ({exc}) → 직선으로 그립니다")

        elapsed_list = merged["elapsed_sec"] if merged else []

        # --------- TMAP 실측을 정답표로 남긴다 (1.23.2) ---------
        # 지도를 그리려고 이미 받은 값이다. 저장하지 않으면 **VEHICLE_SPEED_KMPH가
        # 맞는지 검증할 방법이 없다** — 예전에는 vrp_plan.cum_sec을 실측으로 착각해
        # 틀린 결론을 냈다(1.23.1). 여기 들어가는 road_sec만이 도로 실측이다.
        road_rows.extend(_road_legs(c, route_pts, elapsed_list, tmap_start_time))

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
            # 낱말은 mapviz.py 한 벌에서 온다. 예전에는 여기만
            # "Pick (회수)"/"Drop (분배)"라, 같은 작업을 지도는 영어로
            # 웹 작업지시서는 한글로 불렀다 — 기사가 두 화면을 오가며
            # 보는데 세 번째 어휘가 살아 있었다(1.26.107이 놓친 자리).
            #
            # ⚠️ 여기서 **색은 정하지 않는다.** 이 지도의 대여소 마커는
            #    방문 순서를 보라는 보라 원(DivIcon)이라 작업 종류로 색을
            #    나누지 않는다. 예전에는 `base_color`에 "blue"/"orange"를
            #    넣어 두고 **쓰지 않았다** — 읽는 사람은 색이 작업을
            #    뜻한다고 오해하게 된다. 지운다.
            if action == "pick":
                current_load += qty
                action_txt = PICK_WORD
            else:
                current_load -= qty
                action_txt = DROP_WORD

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
    # 있는 것만 담는다(보라 원=방문 번호, 선=군집별 경로).
    #
    # ⚠️ 예전 주석은 *"파랑/주황=작업 종류"* 라고 적어 두었는데
    #    `legend_rows`에 그런 항목은 **없다.** 이 지도는 작업 종류를
    #    색으로 말하지 않고(마커는 전부 보라 원이다) 풍선·창의
    #    ‘작업 유형’ 글자로 말한다. 주석이 없는 색을 설명하면 다음
    #    사람이 범례에서 그것을 찾다가 범례가 빠졌다고 오해한다.
    # 색을 값으로 읽게 두지 않는다 — 글자를 함께 적는다 (docs/구현/DESIGN.md).
    #
    # 상자 모양은 mapviz.legend_html()이 맡는다 — 이 범례가 세 지도 중
    # 유일하게 다듬어져 있어서, 그것을 기준 삼아 나머지 둘을 맞췄다(1.26.80).
    #
    # ⚠️ 🟢·🔴은 원 배지가 아니라 **이모지 그대로** 둔다. 차고지 마커는
    #    CircleMarker가 아니라 folium.Icon(핀 모양)이라, 원으로 그리면
    #    범례와 지도의 모양이 어긋난다.
    legend_rows = [
        ("🟢", "출발 (차고지)"),
        ("🔴", "도착 (차고지 복귀)"),
        (swatch_circle("purple", "3"), "방문 순서"),
        ("▶", "차량 이동 방향"),
    ]
    # 군집 경로 색은 접어 둔다. 18개를 한 줄씩 세우면 범례가 688px까지
    # 늘어나 지도를 가린다(실측). 앞의 넷은 늘 보인다.
    # 색은 위와 **같은 인자**(군집 번호)로 뽑아야 범례와 선이 맞는다.
    legend_rows += [(swatch_line(cluster_color(c)), f"군집 {c} 경로")
                    for c in unique_clusters]
    m.get_root().html.add_child(folium.Element(legend_html(
        "범례 — 경로", legend_rows,
        collapse_after=4, collapse_label="군집",
        note="점에 커서를 대면 요약이,<br>누르면 자세한 내용이 뜹니다.")))

    # ⚠️ 레이어 컨트롤은 지도 **위에** 겹쳐 뜬다. 펴 두면 군집 수만큼
    # 줄이 서서 지도 오른쪽을 위에서 아래까지 덮는다 — 군집 19개짜리
    # 산출물에서 38줄, 780px였다(실측 1.26.107). 접어 둔다: 색이 무슨
    # 뜻인지는 이제 **범례**가 말하고, 컨트롤은 걸러 보는 도구다.
    folium.LayerControl(collapsed=True).add_to(m)

    # ⚠️ folium.Icon(출발·도착 핀)은 Leaflet이 키보드 접근성으로 role="button"을
    #    자동으로 붙이는데, 그 안의 글자 없는 FontAwesome 아이콘(::before로
    #    그려진다)만으로는 이름이 없다 — Marker(alt=...)를 줘 봐도 소용없다
    #    (Leaflet은 <img>에만 alt를 적용하는데 AwesomeMarkers 아이콘은 <div>다,
    #    실측). 방문 순서 원(DivIcon)은 안에 숫자가 그대로 보여 이름이 있으므로
    #    건드리지 않는다 — 글자가 이미 있는 마커까지 덮어써 화면에 보이는 것과
    #    다른 말을 지어내지 않으려는 것이다(axe aria-command-name, 1.26.123).
    #    이미 붙여 둔 풍선(tooltip) 글을 그대로 이름으로 쓴다.
    #
    #    ⚠️ `m.get_root().script`에 바로 붙이면 **마커보다 앞선 자리**에
    #    나온다 — folium은 루트의 script 자식을 지도·마커(각자 `.add_to(m)`로
    #    붙은 것들)보다 먼저 렌더링한다(실측: eachLayer가 undefined를 읽어
    #    콘솔 오류, 마커 0개). `window`의 `load`를 기다리면 소스 안에서
    #    어디 있든 실제 실행은 모든 마커가 생긴 뒤가 된다.
    m.get_root().script.add_child(folium.Element(f"""
        window.addEventListener('load', function () {{
            {m.get_name()}.eachLayer(function walk(layer) {{
                if (layer.eachLayer) {{ layer.eachLayer(walk); return; }}
                var el = layer.getElement && layer.getElement();
                if (!el || el.getAttribute('role') !== 'button') return;
                if (el.hasAttribute('aria-label') || el.textContent.trim()) return;
                var tip = layer.getTooltip && layer.getTooltip();
                var text = tip ? String(tip.getContent()).replace(/<[^>]*>/g, ' ').replace(/\\s+/g, ' ').trim() : '';
                if (text) el.setAttribute('aria-label', text);
            }});
        }});
    """))

    m.save(result_path.format(duration=duration, now=now))

    # TMAP 실측을 DB에 남긴다. CSV는 만들지 않는다 — 산출물이 아니라 **측정치**이고,
    # 쌓여야 값어치가 생긴다(docs/분석/ML_OPPORTUNITIES.md ①).
    if road_rows:
        db.save_output("road_leg", pd.DataFrame(road_rows),
                       run_label=now, duration=duration)
        print(f"TMAP 실측 {len(road_rows)}구간을 road_leg에 남겼습니다.")
    else:
        print("[안내] TMAP 실측이 없어 road_leg에 남기지 않았습니다"
              " (한도 초과 등으로 직선으로 그린 경우).")

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
        # 앞 단계(step1·step2)가 '대상 없음'으로 건너뛴 시간대는 여기서도
        # 건너뛴다 (1.26.129). 예전에는 확인 없이 바로 읽어 FileNotFoundError로
        # **파이프라인 전체가 죽었다** — 지도는 산출물일 뿐이고 뒤에 지표(step4)가
        # 남아 있는데도. ilp.py·vrp.py는 이미 같은 가드를 갖고 있었다.
        plan_path = Path(vrp_plan_file.format(duration=duration, now=now))
        candidates = Path(clustered_file.format(duration=duration, now=now))
        missing = [p.name for p in (plan_path, candidates) if not p.is_file()]
        if missing:
            print(f"\n[건너뜀] {duration}: 입력이 없습니다 ({', '.join(missing)})")
            continue

        vrp_plan = pd.read_csv(plan_path, encoding="utf-8")
        if vrp_plan.empty:
            print(f"\n[건너뜀] {duration}: VRP 계획이 비어 있습니다(그릴 경로 없음)")
            continue

        pick_drop = pd.read_csv(candidates, encoding="utf-8")

        make_vrp_map(depot, pick_drop, vrp_plan, duration, HEADERS, TMAP_URL)

    print(f"\nTMAP 호출 {call_count()}건 (예산 {module.MAX_CALLS}건)")
    남은_엔드포인트 = [e.name for e in module.available_endpoints()]
    print(f"  사용 가능한 엔드포인트: {', '.join(남은_엔드포인트) or '없음(모두 한도 소진)'}")
