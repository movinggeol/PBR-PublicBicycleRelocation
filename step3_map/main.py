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
    DEPOT_ID, DEPOT_LAT, DEPOT_LON, DEPOT_NAME, MAP_TILES, PROJECT_ROOT,
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
    for i in range(min(len(route_pts), len(elapsed_sec)) - 1):
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
    

    for idx, c in enumerate(unique_clusters):

        station_visits = {}
        
        fg = folium.FeatureGroup(name=f"Cluster {c}", show=True)
        fg.add_to(m)

        cluster_df = vrp_plan[vrp_plan["cluster"]==c].copy()

        current_load = 0

        color = cluster_color(idx)

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
    legend_rows += [(swatch_line(cluster_color(i)), f"군집 {c} 경로")
                    for i, c in enumerate(unique_clusters)]
    m.get_root().html.add_child(folium.Element(legend_html(
        "범례 — 경로", legend_rows,
        collapse_after=4, collapse_label="군집",
        note="점에 커서를 대면 요약이,<br>누르면 자세한 내용이 뜹니다.")))

    # ⚠️ 레이어 컨트롤은 지도 **위에** 겹쳐 뜬다. 펴 두면 군집 수만큼
    # 줄이 서서 지도 오른쪽을 위에서 아래까지 덮는다 — 군집 19개짜리
    # 산출물에서 38줄, 780px였다(실측 1.26.107). 접어 둔다: 색이 무슨
    # 뜻인지는 이제 **범례**가 말하고, 컨트롤은 걸러 보는 도구다.
    folium.LayerControl(collapsed=True).add_to(m)

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
