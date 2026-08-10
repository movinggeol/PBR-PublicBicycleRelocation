"""ILP 이동 계획을 차량 방문 순서(VRP)로 변환한다. (greedy 휴리스틱)

- 클러스터당 차량 1대가 depot(타슈 관제센터)에서 출발해
  '거리 / 처리가능량' 점수가 가장 낮은 대여소를 반복 선택한다.
- 같은 대여소가 pick과 drop 양쪽에 있을 수 있으므로 노드 키는 (station_id, type).
- 각 이동에 대해 거리(km)·이동시간(초)·작업시간(초)·누적시간(초)을 기록한다.
  (작업시간은 자전거 1대당 PICK/DROP_TIME_SEC 가정)

출력: data/pp_data/VRP/VRP_plan{duration} ({now}).csv
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from ilp import haversine_km

import db
from project_config import (
    DEPOT_ID, DEPOT_LAT, DEPOT_LON, PROJECT_ROOT, VEHICLE_CAPACITY,
    duration_list, ensure_output_dirs, get_runtime_config,
)

# read_csv
metrics_file = str(PROJECT_ROOT / "data/pp_data/ILP/후보/top{duration} ({now}).csv")  # lat/lon 포함
ilp_plan_file = str(PROJECT_ROOT / "data/pp_data/ILP/ILP_plan{duration} ({now}).csv")
# to_csv
vrp_plan_file = str(PROJECT_ROOT / "data/pp_data/VRP/VRP_plan{duration} ({now}).csv")

config = get_runtime_config()
now = config.now

VEHICLE_TOTAL = 21          # TODO: 아직 미사용 — 현재는 클러스터당 차량 1대 가정 (docs/TODO.md 참고)
VEHICLE_SPEED_KMPH = 30     # ILP(25km/h)와 다름 — 통일 여부는 운영 데이터로 결정 (docs/TODO.md)
PICK_TIME_SEC = 30.0        # 자전거 1대 싣는 시간
DROP_TIME_SEC = 30.0        # 자전거 1대 내리는 시간


def _travel_sec(km: float) -> float:
    return km / VEHICLE_SPEED_KMPH * 3600.0


def run_vrp_plan(ilp_plan: pd.DataFrame, duration: str):
    '''
    ILP에서 계산한 자전거 이동 계획을 기반으로 실제 차량이 어떤 순서로 대여소를 방문해야 하는지를 계산.
    클러스터별 작업 분리 -> pick/drop 노드 정리 -> 현재 위치 기준 최적 후보 선택 ->
    pick/drop 수행 -> 남은 작업이 없을 때까지 반복 -> VRP 결과 저장
    '''
    results = []
    clusters = ilp_plan['cluster'].unique()

    # 좌표 불러오기 (클러스터 루프 밖에서 1회)
    station_info = pd.read_csv(metrics_file.format(duration=duration, now=now), encoding='utf-8')
    station_info = station_info.set_index('station_id')

    for c in clusters:
        cluster_plan = ilp_plan[ilp_plan['cluster'] == c]

        # -------------------------
        # 1. pick / drop 집계 → 노드 구성
        #    같은 대여소가 양쪽에 있을 수 있으므로 (station_id, type) 키 사용
        # -------------------------
        pick_dict = cluster_plan.groupby('pick_station_id')['qty'].sum().to_dict()
        drop_dict = cluster_plan.groupby('drop_station_id')['qty'].sum().to_dict()

        nodes = {}
        for sid, qty in pick_dict.items():
            nodes[(sid, 'pick')] = {
                'qty': int(qty),
                'lat': station_info.loc[sid, 'lat'],
                'lon': station_info.loc[sid, 'lon'],
            }
        for sid, qty in drop_dict.items():
            nodes[(sid, 'drop')] = {
                'qty': int(qty),
                'lat': station_info.loc[sid, 'lat'],
                'lon': station_info.loc[sid, 'lon'],
            }

        # -------------------------
        # 2. VRP 시작 (depot 출발, 적재 0)
        # -------------------------
        current_id = DEPOT_ID
        current_lat = DEPOT_LAT
        current_lon = DEPOT_LON
        current_load = 0
        cum_sec = 0.0

        while True:
            remaining_pick = sum(v['qty'] for (sid, t), v in nodes.items()
                                 if t == 'pick' and v['qty'] > 0)
            remaining_drop = sum(v['qty'] for (sid, t), v in nodes.items()
                                 if t == 'drop' and v['qty'] > 0)

            if remaining_pick == 0 and remaining_drop == 0:
                break

            # 후보 계산: 가까우면서 많이 처리 가능한 곳 우선 (score 낮을수록 우선)
            candidates = []
            for (sid, ntype), info in nodes.items():
                if info['qty'] <= 0:
                    continue

                distance = haversine_km(current_lat, current_lon, info['lat'], info['lon'])

                if ntype == 'pick' and current_load < VEHICLE_CAPACITY:
                    possible = min(info['qty'], VEHICLE_CAPACITY - current_load)
                elif ntype == 'drop' and current_load > 0:
                    possible = min(info['qty'], current_load)
                else:
                    continue

                score = distance / (possible + 1e-6)
                if possible == info['qty']:    # 노드를 완결 지으면 보너스
                    score *= 0.1
                candidates.append((ntype, sid, score, distance, possible))

            # -------------------------
            # 3. 작업 불가 상황 → depot 복귀
            # -------------------------
            if not candidates:
                if current_id == DEPOT_ID and current_load == 0:
                    # depot에서 빈 차로도 후보가 없으면 더 진행 불가 (무한루프 방지)
                    print(f"[경고] cluster {c}: 처리 불가 작업 잔여 "
                          f"(pick {remaining_pick}, drop {remaining_drop}) — 종료")
                    break

                distance = haversine_km(current_lat, current_lon, DEPOT_LAT, DEPOT_LON)
                travel = _travel_sec(distance)
                cum_sec += travel
                results.append({
                    'cluster': c,
                    'from_id': current_id,
                    'from_lat': current_lat,
                    'from_lon': current_lon,
                    'to_id': DEPOT_ID,
                    'to_lat': DEPOT_LAT,
                    'to_lon': DEPOT_LON,
                    'action': 'return',
                    'qty': 0,
                    'distance_km': round(distance, 3),
                    'travel_sec': round(travel, 1),
                    'work_sec': 0.0,
                    'cum_sec': round(cum_sec, 1),
                })
                current_id = DEPOT_ID
                current_lat = DEPOT_LAT
                current_lon = DEPOT_LON
                current_load = 0
                continue

            # -------------------------
            # 4. 최적 후보 선택 및 수행
            # -------------------------
            candidates.sort(key=lambda x: x[2])
            action, sid, _, distance, qty = candidates[0]
            node = nodes[(sid, action)]

            travel = _travel_sec(distance)
            per_bike = PICK_TIME_SEC if action == 'pick' else DROP_TIME_SEC
            work = qty * per_bike
            cum_sec += travel + work

            results.append({
                'cluster': c,
                'from_id': current_id,
                'from_lat': current_lat,
                'from_lon': current_lon,
                'to_id': sid,
                'to_lat': node['lat'],
                'to_lon': node['lon'],
                'action': action,
                'qty': qty,
                'distance_km': round(distance, 3),
                'travel_sec': round(travel, 1),
                'work_sec': round(work, 1),
                'cum_sec': round(cum_sec, 1),
            })

            if action == 'pick':
                current_load += qty
            else:
                current_load -= qty
            node['qty'] -= qty

            current_id = sid
            current_lat = node['lat']
            current_lon = node['lon']

    # -------------------------
    # 저장
    # -------------------------
    vrp_result = pd.DataFrame(results)
    vrp_result.to_csv(vrp_plan_file.format(duration=duration, now=now), index=False)
    print(f"\nvrp_plan_path 파일이 저장되었습니다. ({vrp_plan_file.format(duration=duration, now=now)})")

    # CSV·DB 이중 기록 (DB_PLAN 2단계). 방문 순서는 db가 seq 컬럼으로 보존한다.
    db.save_output("vrp_plan", vrp_result, run_label=now, duration=duration)


if __name__ == '__main__':
    ensure_output_dirs()

    for duration in duration_list(config):
        ilp_plan = pd.read_csv(ilp_plan_file.format(duration=duration, now=now), encoding='utf-8', low_memory=False)
        run_vrp_plan(ilp_plan, duration)
        print("VRP 완료")
