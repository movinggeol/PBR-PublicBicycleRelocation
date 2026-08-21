import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import numpy as np
import pulp

import db
from project_config import (
    PROJECT_ROOT, VEHICLE_SPEED_KMPH, duration_list, ensure_output_dirs, get_runtime_config,
)

# read_csv
metrics_path = str(PROJECT_ROOT / "data/pp_data/ILP/후보/top{duration} ({now}).csv")   # lat/lon 포함된 metrics
# to_csv
ilp_plan_path = str(PROJECT_ROOT / "data/pp_data/ILP/ILP_plan{duration} ({now}).csv")

config = get_runtime_config()
now = config.now

vehicle_speed_kmph = VEHICLE_SPEED_KMPH   # 이동 속도(km/h) — VRP와 같은 값을 써야 한다

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


def km_to_travel_seconds(km: float, speed_kmph: float = VEHICLE_SPEED_KMPH) -> float:
    '''
    거리(km) -> 이동시간(초)
    '대여소 간 거리'를 '차량이 몇 초 걸려서 이동'할 수 있을지 구할 때 사용
    time(sec) = distance(km) / speed(km/h) * 3600
    '''
    # return float((km / max(speed_kmph, 1e-9)) * 3600.0)
    return float((km / speed_kmph) * 3600.0)


def solve_cluster_moves(cluster_df: pd.DataFrame, solver: pulp.LpSolver,
                        speed_kmph: float = VEHICLE_SPEED_KMPH) -> list:
    '''클러스터 1개의 ILP를 풀어 이동 계획(행 목록)을 돌려준다.

    **저장하지 않는 순수 계산이다.** 파일·DB에 남기는 것은 run_ilp_plan()이고,
    실험 스크립트(experiments/baseline_compare.py)는 이 함수를 직접 쓴다 —
    측정 코드와 운영 코드가 갈리면 비교가 성립하지 않는다.

    반환: [{'pick_station_id', 'drop_station_id', 'qty', 'travel_time_sec'}, ...]
    '''
    # 픽업/드롭 대상 대여소 분리
    pick_nodes = cluster_df.query('pick_qty > 0')[['station_id','pick_qty','lat','lon']]
    drop_nodes = cluster_df.query('drop_qty > 0')[['station_id','drop_qty','lat','lon']]

    if pick_nodes.empty or drop_nodes.empty:
        return []

    # 총 이동량(픽업/드롭) 계산 (둘 중 적은 값만큼만 운반 가능)
    total_pick = int(pick_nodes['pick_qty'].sum())
    total_drop = int(drop_nodes['drop_qty'].sum())
    move_total = min(total_pick, total_drop)
    print(f"#pick={len(pick_nodes)}곳,  #drop={len(drop_nodes)}곳,  move_total={move_total}대")

    if move_total == 0:
        return []

    # 픽업/드롭 대여소 ID 리스트 (출발/도착 노드 집합) (ILP 결정변수 x[(i,j)]는 iㅌI, jㅌJ 범위)
    I = pick_nodes['station_id'].tolist()
    J = drop_nodes['station_id'].tolist()

    # 이동시간(비용) 계산을 위한 좌표 딕셔너리.
    cp = pick_nodes.set_index('station_id')[['lat','lon']].to_dict('index')
    cd = drop_nodes.set_index('station_id')[['lat','lon']].to_dict('index')

    # 거리(haversine_km) -> 이동시간(km_to_travel_seconds) 계산
    T = {(i,j): km_to_travel_seconds(
            haversine_km(cp[i]['lat'], cp[i]['lon'], cd[j]['lat'], cd[j]['lon']),
            speed_kmph
        ) for i in I for j in J}

    # ILP 문제 생성(최소화 문제 : 정수 개수의 자전거를 최소 비용으로 옮긴다.)
    prob = pulp.LpProblem("cluster", pulp.LpMinimize)

    # 결정변수 x[(i,j)] : 픽업 대여소 i -> 드롭 대여소 j로 옮기는 자전거 대수
    x = pulp.LpVariable.dicts('x', ((i,j) for i in I for j in J),
                              lowBound=0, cat=pulp.LpInteger)

    # 목적함수 : 총 작업시간(이동 시간 x 옮기는 자전거 대수) 최소화
    prob += pulp.lpSum(T[(i,j)] * x[(i,j)] for i in I for j in J)

    # ---------------- 제약조건 ---------------
    pick_map = dict(zip(pick_nodes['station_id'], pick_nodes['pick_qty']))
    drop_map = dict(zip(drop_nodes['station_id'], drop_nodes['drop_qty']))

    # 제약조건 1 : 픽업 노드 공급 제한 (∑_{j∈J} x_{ij} ≤ s_i)
    for i in I:
        prob += pulp.lpSum(x[(i,j)] for j in J) <= pick_map[i]

    # 제약조건 2 : 드롭 노드 수요 제한 (∑_{i∈I} x_{ij} ≤ d_j)
    for j in J:
        prob += pulp.lpSum(x[(i,j)] for i in I) <= drop_map[j]

    # 제약조건 3 : 총 이동량 강제 (없으면 '아무것도 안 옮기는 해'가 최적이 된다)
    prob += pulp.lpSum(x[(i,j)] for i in I for j in J) == move_total
    # ---------------- 제약조건 ---------------

    prob.solve(solver)
    print("Status:", pulp.LpStatus[prob.status])

    rows = []
    for i in I:
        for j in J:
            v = int(pulp.value(x[(i,j)]) or 0)
            if v > 0:
                rows.append({'pick_station_id': i,
                             'drop_station_id': j,
                             'qty': v,
                             'travel_time_sec': T[(i,j)]})
    return rows


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
        for row in solve_cluster_moves(metrics[metrics['cluster'] == c], solver,
                                       vehicle_speed_kmph):
            rows.append({'hour': duration, 'cluster': c, **row})

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
        candidates = Path(metrics_path.format(duration=duration, now=now))
        if not candidates.is_file():
            # step1이 '대상 없음'으로 건너뛴 시간대. 여기서도 건너뛴다.
            print(f"\n[건너뜀] {duration}: 후보 파일이 없습니다 ({candidates.name})")
            continue

        metrics = pd.read_csv(candidates, encoding='utf-8', low_memory=False)
        print(f"\n=== [ILP @ {duration}] ===")
        run_ilp_plan(metrics, duration, solver)