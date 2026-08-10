import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import numpy as np
import pulp

import db
from project_config import PROJECT_ROOT, duration_list, ensure_output_dirs, get_runtime_config

# read_csv
metrics_path = str(PROJECT_ROOT / "data/pp_data/ILP/후보/top{duration} ({now}).csv")   # lat/lon 포함된 metrics
# to_csv
ilp_plan_path = str(PROJECT_ROOT / "data/pp_data/ILP/ILP_plan{duration} ({now}).csv")

config = get_runtime_config()
now = config.now

vehicle_speed_kmph = 25                                  # 이동 속도(km/h)

def haversine_km(lat1, lon1, lat2, lon2) -> float:
    '''
    위도와 경도를 이용해 두 지점 사이의 실제 지구 곡면 거리(km)를 계산
    (대여소 A - 대여소 B) 사이의 실제 거리
    '''
    R = 6371.0
    
    p1 = np.radians(lat1)
    p2 = np.radians(lat2)
    
    dlat = p2 - p1
    dlon = np.radians(lon2 - lon1)
    
    a = np.sin(dlat/2)**2 + np.cos(p1)*np.cos(p2)*np.sin(dlon/2)**2
    
    # 중심각 계산 (지구 기준 두 점 사이의 각도)
    c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1-a))

    # 최종 거리 (실제 지표면 길이(호의 길이)) (d = R * c)
    return float(R*c)


def km_to_travel_seconds(km: float, speed_kmph: float = 25.0) -> float:
    '''
    거리(km) -> 이동시간(초)
    '대여소 간 거리'를 '차량이 몇 초 걸려서 이동'할 수 있을지 구할 때 사용
    time(sec) = distance(km) / speed(km/h) * 3600
    '''
    # return float((km / max(speed_kmph, 1e-9)) * 3600.0)
    return float((km / speed_kmph) * 3600.0)


def run_ilp_plan(metrics: pd.DataFrame, duration: str, solver: pulp.LpSolver):
    '''
    재배치 후보 대여소(metrics) -> pick / drop 분리 ->  대여소 간 이동시간 계산 -> 
    ILP 문제 생성 -> 목적함수 정의 -> 제약조건 추가 -> 
    CBC Solver 최적화 -> 최적 재배치 계획 저장
    '''

    # metrics 테이블에 시점별 target 반영
    print(metrics.head())
    metrics['drop_qty'] = metrics['rebal_qty'].clip(lower=0).astype(int)
    metrics['pick_qty'] = (-metrics['rebal_qty'].clip(upper=0)).astype(int)

    clusters = metrics['cluster'].unique()
    rows=[]

    # 클러스터별로 ILP 수행
    for c in clusters:
        
        cluster_df = metrics[metrics['cluster'] == c]

        # 픽업/드롭 대상 대여소 분리
        pick_nodes = cluster_df.query('pick_qty > 0')[['station_id','pick_qty','lat','lon']]
        drop_nodes = cluster_df.query('drop_qty > 0')[['station_id','drop_qty','lat','lon']]

        if pick_nodes.empty or drop_nodes.empty:
            continue

        # 총 이동량(픽업/드롭) 계산 (둘 중 적은 값만큼만 운반 가능)
        total_pick = int(pick_nodes['pick_qty'].sum())
        total_drop = int(drop_nodes['drop_qty'].sum())
        move_total = min(total_pick, total_drop)
        print(f"#pick={len(pick_nodes)}곳,  #drop={len(drop_nodes)}곳,  move_total={move_total}대")

        if move_total == 0:
            continue


        # 픽업/드롭 대여소 ID 리스트 (출발/도착 노드 집합) (ILP 결정변수 x[(i,j)]는 iㅌI, jㅌJ 범위)
        I = pick_nodes['station_id'].tolist() 
        J = drop_nodes['station_id'].tolist()

        # 이동시간(비용) 계산을 위한 좌표 딕셔너리. 
        # 예 :{'ST0319': {'lat': 36.329316, 'lon': 127.382432}, 'ST0461': {'lat': 36.345683, 'lon': 127.399695}
        cp = pick_nodes.set_index('station_id')[['lat','lon']].to_dict('index')
        cd = drop_nodes.set_index('station_id')[['lat','lon']].to_dict('index')

    
        # 거리(haversine_km) -> 이동시간(km_to_travel_time) 계산 (모든 픽업/드롭 대여소 iㅌI와 jㅌJ 조합에 대해 거리 계산)
        #   그 거리를 차량 속도로 나눠 이동시간(초)으로 변환 (결과 : T[(i,j)] -> i에서 j로 갈 때 걸리는 시간)
        #   예 : {('ST0319', 'ST0374'): 699.8302806850342, ('ST0319', 'ST1051'): 541.9651076658998}
        T = {(i,j): km_to_travel_seconds(
                haversine_km(cp[i]['lat'], cp[i]['lon'], cd[j]['lat'], cd[j]['lon']),
                vehicle_speed_kmph
            ) for i in I for j in J}


        # ILP 문제 생성(최소화 문제 : 정수 개수의 자전거를 최소 비용으로 옮긴다.)
        prob = pulp.LpProblem(f"cluster_{c}", pulp.LpMinimize)

        # 결정변수 x[(i,j)] : 픽업 대여소 i -> 드롭 대여소 j로 가능한 모든 이동 경로 ('자전거 대수'는 Solver 최적화 이후 가능)
        # 예 : {('ST0319', 'ST0374'): x_('ST0319',_'ST0374'), ('ST0319', 'ST1051'): x_('ST0319',_'ST1051')}
        #   'ST0319'에서 'ST0374'로 몇 대 이동할 것인가? (x_{ij}, 즉 x_{'ST0319', 'ST0374'})
        x = pulp.LpVariable.dicts('x', ((i,j) for i in I for j in J), 
                                  lowBound=0, cat=pulp.LpInteger)


        # 목적함수 : 총 작업시간(이동 시간 x 옮기는 자전거 대수) 최소화 ( min ∑_i ∑_j (T_{ij} * x_{ij}) )
        #   즉, 가까운 곳끼리 많이 옮기도록 유도
        prob += pulp.lpSum(T[(i,j)] * x[(i,j)] for i in I for j in J)



        # ---------------- 제약조건 ---------------
        # 공급/수요 제약을 위한 맵 생성
        # pick_map[i] = i 대여소에서 가져올 수 있는 최대 대수
        # drop_map[j] = j 대여소에서 받을 수 있는 최대 대수(필요량)
        pick_map = dict(zip(pick_nodes['station_id'], pick_nodes['pick_qty']))
        drop_map = dict(zip(drop_nodes['station_id'], drop_nodes['drop_qty']))

        # 제약조건 1 : 픽업 노드 공급 제한 (∑_{j∈J} x_{ij} ≤ s_i(pick_i))
        # 대여소 i 에서 다른 곳으로 보내는 자전거 총합은 해당 대여소가 실제로 제공 가능한 수량 이하이어야 한다.
        # pick_map['ST0001']=5 이며, 변수 x(ST0001, A),x(ST0001, B),x(ST0001, C) 가 있으면 x_A + x_B + x_C ≤ 5
        for i in I: 
            prob += pulp.lpSum(x[(i,j)] for j in J) <= pick_map[i]
        
        # 제약조건 2 : 드롭 노드 수요 제한 (∑_{i∈I} x_{ij} ≤ d_j(drop_j)) : (drop_j로 들어오는 물량 합 ≤ 실제로 필요한 수량)
        for j in J: 
            prob += pulp.lpSum(x[(i,j)] for i in I) <= drop_map[j]

        # 제약조건 3 : 총 이동량 강제 (가능한 만큼은 반드시 작업)
        # 이것이 없으면 비용이 가장 작은 해인 x[i,j]=0 (아무것도 옮기지 않는 해)가 최적해가 되어버림
        # 공급/수요 중 가능한 최대치 min(total_pick 또는 drop)만큼 실제 이동하도록 강제
        prob += pulp.lpSum(x[(i,j)] for i in I for j in J) == move_total
        # ---------------- 제약조건 ---------------

        prob.solve(solver)
        print("Status:", pulp.LpStatus[prob.status])

        # 결과(해) 추출 : "x[i,j] > 0" 만 저장
        #print(I); print(J)
        for i in I:
            for j in J:
                v = int(pulp.value(x[(i,j)]) or 0)
                if v>0:
                    rows.append({'hour':duration, 
                                'cluster': c,
                                'pick_station_id':i, 
                                'drop_station_id':j,
                                'qty':v, 
                                'travel_time_sec':T[(i,j)]})
    # 재배치 계획표 도출
    ilp_plan = pd.DataFrame(rows)
    ilp_plan.to_csv(ilp_plan_path.format(duration=duration, now=now), index=False)
    print(f"\nilp_plan_path 파일이 저장되었습니다. ({ilp_plan_path.format(duration=duration, now=now)})")

    # CSV·DB 이중 기록 (DB_PLAN 2단계). CSV가 아직 정본이다.
    db.save_output("ilp_plan", ilp_plan, run_label=now, duration=duration)


# main
if __name__ == "__main__":
    ensure_output_dirs()

    # 솔버 객체 생성 후 실행 (시간 제한/갭 포함)
    # 계산 시간의 폭증을 방지하고 실시간 운영 가능성을 확보하기 위해,
    # 본 연구에서는 CBC 정수계획 솔버에 시간 제한(600초)과 상대적 최적 갭(2%)을 적용하였다.
    solver = pulp.PULP_CBC_CMD(msg=True, timeLimit=600, gapRel=0.02)

    for duration in duration_list(config):
        metrics = pd.read_csv(metrics_path.format(duration=duration, now=now), encoding='utf-8', low_memory=False)
        print(f"\n=== [ILP @ {duration}] ===")
        run_ilp_plan(metrics, duration, solver)