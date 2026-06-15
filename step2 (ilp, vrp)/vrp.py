from datetime import datetime
import pandas as pd
import numpy as np
from ilp import haversine_km #, km_to_travel_seconds

# read_csv
metrics_file = "data/pp_data/ILP/후보/top{duration} ({now}).csv"  # lat/lon 포함
ilp_plan_file = "data/pp_data/ILP/ILP_plan{duration} ({now}).csv"
# to_csv
vrp_plan_file = "data/pp_data/VRP/VRP_plan{duration} ({now}).csv"

now = '2026-05-21 18'
#now = datetime.now().strftime('%Y-%m-%d %H')

VEHICLE_TOTAL   = 21
VEHICLE_CAPACITY = 10
VEHICLE_SPEED_KMPH = 30
PICK_TIME_SEC = 30.0
DROP_TIME_SEC = 30.0

def manhattan_distance_km(lat1, lon1, lat2, lon2):
    lat_km_per_degree = 111.0 # Approximate conversion
    lon_km_per_degree = 111.0 # Simplification

    delta_lat = abs(lat2 - lat1)
    delta_lon = abs(lon2 - lon1)

    distance = (delta_lat * lat_km_per_degree) + (delta_lon * lon_km_per_degree)
    return float(distance)


def run_vrp_plan(ilp_plan: pd.DataFrame, duration: str):
    '''
    ILP에서 계산한 자전거 이동 계획을 기반으로 실제 차량이 어떤 순서로 대여소를 방문해야 하는지를 계산.
    ILP 결과 불러오기 -> 클러스터별 작업 분리 -> pick / drop 대여소 정리 ->
    현재 차량 위치 기준 가장 효율적인 후보 선택 -> pick/drop 수행 -> 모든 작업 끝날 때까지 반복 -> VRP 결과 저장
    '''
    DEPOT_ID = 'ST0001'; DEPOT_LAT = 36.406607; DEPOT_LON = 127.306457   # 타슈 관제센터

    results = []
    clusters = ilp_plan['cluster'].unique()

    for c in clusters:
        cluster_plan = ilp_plan[ilp_plan['cluster'] == c]

        # -------------------------
        # 1. pick / drop 집계
        # -------------------------
        
        pick_dict = (
            cluster_plan.groupby('pick_station_id')['qty']
            .sum().to_dict()
        )
        drop_dict = (
            cluster_plan.groupby('drop_station_id')['qty']
            .sum().to_dict()
        )

        # 좌표 불러오기
        station_info = pd.read_csv(metrics_file.format(duration=duration, now=now), encoding='utf-8')
        station_info = station_info.set_index('station_id')

        # 노드 통합
        nodes = {}

        for sid, qty in pick_dict.items():
            nodes[sid] = {
                'type': 'pick',
                'qty': qty,
                'lat': station_info.loc[sid, 'lat'],
                'lon': station_info.loc[sid, 'lon']
            }
            
        for sid, qty in drop_dict.items():
            nodes[sid] = {
                'type': 'drop',
                'qty': qty,
                'lat': station_info.loc[sid, 'lat'],
                'lon': station_info.loc[sid, 'lon']
            }

        # -------------------------
        # 2. VRP 시작
        # -------------------------
        current_id = DEPOT_ID
        current_lat = DEPOT_LAT
        current_lon = DEPOT_LON
        current_load = 0

        visited_count = {}

        while True:
            
            # 남은 작업 체크
            remaining_pick = sum(
                v['qty'] for v in nodes.values()
                if v['type'] == 'pick' and v['qty'] > 0
            )

            remaining_drop = sum(
                v['qty'] for v in nodes.values()
                if v['type'] == 'drop' and v.get('drop_qty', v['qty']) > 0
            )

            if remaining_pick == 0 and remaining_drop == 0:
                break

            candidates = []

            for sid, info in nodes.items():
    
                lat = info['lat']
                lon = info['lon']

                distance = haversine_km(
                    current_lat, current_lon,
                    lat, lon
                )

                # pick 가능 조건
                # 가까우면서 많이 처리 가능한 곳을 우선 선택(score가 낮을수록 우선 처리)
                # 후보 저장 (작업유형, 대여소ID, score, 거리, 처리가능량)
                if info['type'] == 'pick' and info['qty'] > 0:
                    if current_load < VEHICLE_CAPACITY:
                        possible = min(info['qty'], VEHICLE_CAPACITY - current_load)
                        score = distance / (possible + 1e-6)
                        if possible == info['qty']:
                            score *= 0.1
                        candidates.append(('pick', sid, score, distance, possible))

                drop_qty = (
                    info.get('drop_qty', info['qty'])
                    if info['type'] == 'drop'
                    else 0
                )
                # drop 가능 조건
                if drop_qty > 0 and current_load > 0:
                    possible = min(drop_qty, current_load)
                    score = distance / (possible + 1e-6)
                    if possible == info['qty']:
                        score *= 0.1
                    candidates.append(('drop', sid, score, distance, possible))

            # -------------------------
            # 3. 작업 불가 상황 → depot 복귀
            # -------------------------

            if not candidates:
                results.append({
                    'cluster': c, 
                    'from_id': sid,
                    'from_lat': current_lat,
                    'from_lon': current_lon,
                    'to_id': DEPOT_ID,
                    'to_lat': DEPOT_LAT,
                    'to_lon': DEPOT_LON,
                    'action': 'return',
                    'qty': 0
                })

                current_lat = DEPOT_LAT
                current_lon = DEPOT_LON
                current_load = 0
                continue

            # -------------------------
            # 4. 최적 후보 선택
            # -------------------------

            # score 기준 정렬
            candidates.sort(key=lambda x: x[2])
            #print("---------------------------------------------------------------------------")
            #print(candidates)
            action, sid, _, distance, qty = candidates[0]
            
            node = nodes[sid]
            #print(nodes)
            #print(node)
            
            # 이동 기록
            results.append({
                'cluster': c,
                'from_id': current_id,
                'from_lat': current_lat,
                'from_lon': current_lon,
                'to_id': sid,
                'to_lat': node['lat'],
                'to_lon': node['lon'],
                'action': action,
                'qty': qty
            })

            # load 업데이트
            if action == 'pick':
                current_load += qty
                node['qty'] -= qty

            else:  # drop
                current_load -= qty
                node['qty'] -= qty
                    
            current_id = sid
            current_lat = node['lat']
            current_lon = node['lon']

    # -------------------------
    # 저장
    # -------------------------

    vrp_result = pd.DataFrame(results)
    vrp_result.to_csv(
        vrp_plan_file.format(duration=duration, now=now),
        index=False
    )
    print(f"\nvrp_plan_path 파일이 저장되었습니다. ({vrp_plan_file.format(duration=duration, now=now)})")


if __name__ == '__main__':
    duration_list = ['_05_10']
    ####duration_list = ['_05_10', '_10_15', '_15_20', '_20_05']

    for duration in duration_list:
        # -------------------- 시점별 VRP 실행 (K-Medoids + 맨해튼 거리 클러스터링 적용) --------------------
        ilp_plan = pd.read_csv(ilp_plan_file.format(duration=duration, now=now), encoding='utf-8', low_memory=False)
        run_vrp_plan(ilp_plan, duration)
        print("VRP 완료")
        
