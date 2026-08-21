import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# kmedoids 패키지(Rust 구현, FasterPAM). 기존 sklearn_extra.cluster.KMedoids는
# 프로젝트가 아카이브되어 Python 3.12+ 휠이 없으므로 교체했다. (버전관리 1.2.1)
from kmedoids import KMedoids
import pandas as pd
import numpy as np
from adjust_module import compute_medoids, compute_objective, select_cluster_candidates, \
                            make_cluster_pairs, get_movable_nodes, check_size_constraint, try_move_node

import db
from project_config import (
    ADJUST_BALANCE_LIMIT, ADJUST_BALANCE_OK, ADJUST_MAX_ITER,
    CLUSTER_ALPHA, CLUSTER_BETA, CLUSTER_GAMMA, PROJECT_ROOT, REBAL_MIN_QTY,
    TARGET_CLUSTER_SIZE, TOP_STATION_LIMIT, VEHICLES_PER_ROUND,
    duration_list, ensure_output_dirs, get_runtime_config, require_columns,
)

# read_csv
file_path = str(PROJECT_ROOT / "data/pp_data/재배치 정보/rebal_qty{duration} ({now}).csv")
st_info_file = str(PROJECT_ROOT / "data/pp_data/대여소 정보/st_info ({now}).csv")

# to_csv
clustered_file = str(PROJECT_ROOT / "data/pp_data/ILP/후보/top{duration} ({now}).csv")

config = get_runtime_config()
now = config.now

def select_top_unbalanced_st(file_path:str, duration:str, st_info:pd.DataFrame) -> pd.DataFrame:
    '''
    1차 : 재배치 작업 시, 대상 대여소(pick/drop)를 선택한다.
    '''

    types = ['pick', 'drop']

    # file_path는 호출부에서 이미 완전히 포맷된 경로
    st_rebal = pd.read_csv(file_path, encoding='utf-8', low_memory=False)
    require_columns(st_rebal, ['station_id', 'rebal_qty', 'target_qty'],
                    f'step0 재배치량 {duration}')
    require_columns(st_info, ['station_id', 'station_name', 'lat', 'lon',
                              'parking_lot', 'stock'], 'step0 대여소 정보')
    
    st = (
        st_rebal[abs(st_rebal['rebal_qty']) > REBAL_MIN_QTY]
        .merge(st_info, how='left', on='station_id')
        .iloc[:,[0,7,8,9,3,4,5,6,1,2]]
        .rename(columns={'parking_lot_x':'parking_lot', 'stock_x':'stock'})
        )
    
    print(st.head(10))

    # pick/drop을 작업량 내림차순으로 정렬 후 붙이기
    pick_st = (
        st[st['rebal_qty'] < 0]
        .sort_values('rebal_qty', ascending=True)
    )
    pick_st = pick_st.iloc[:TOP_STATION_LIMIT, :]

    drop_st = (
        st[st['rebal_qty'] > 0]
        .sort_values('rebal_qty', ascending=False)
    )
    drop_st = drop_st.iloc[:TOP_STATION_LIMIT, :]

    # 각 작업량 계산
    pick_qty = pick_st['rebal_qty'].sum(axis=0)
    drop_qty = drop_st['rebal_qty'].sum(axis=0)


    cut_point = min(abs(pick_qty), drop_qty)

    pick_st['cumul'] = pick_st['rebal_qty'].cumsum()
    drop_st['cumul'] = drop_st['rebal_qty'].cumsum()
    

    pick_st = pick_st[abs(pick_st['cumul']) <= cut_point].drop(columns='cumul')
    drop_st = drop_st[drop_st['cumul'] <= cut_point].drop(columns='cumul')
    

    print(f"\n pick 대상) \
          \n - {len(pick_st)}개의 대여소 \
          \n - {pick_st['rebal_qty'].sum(axis=0)}개의 작업량")
    print(f"\n drop 대상 \
          \n - {len(drop_st)}개의 대여소\
          \n - {drop_st['rebal_qty'].sum(axis=0)}개의 작업량")
    

    pick_drop = pd.concat([pick_st, drop_st], axis=0)
    ''' 
    [5, 3, -3, -3, -3] 방지용으로 할까 했는데, 몇 대 정도의 차이는 괜찮을 듯 일단 킵
    pick_drop['cumul'] = pick_drop['rebal_qty'].cumsum()
    pick_drop = pick_drop[pick_drop['cumul'] <= 0]
    pick_drop.drop(columns='cumul', inplace=True)
    '''
    #pick_drop.to_csv("asd.csv", encoding='utf-8', index=False)
    
    return pick_drop


def make_clustering(pick_drop: pd.DataFrame, target_cluster_size: int = None,
                    random_state: int = 42) -> pd.DataFrame:
    '''
    # 2차 : 클러스터링(K-Medoids)

    군집 1개 = 차량 1대가 맡는 작업이므로, 군집 수는 한 회차에 투입할 수 있는
    차량 수를 넘을 수 없다. 상한에 걸리면 군집이 커지고 차량당 작업량이 늘어난다.
    (docs/FLEET.md)

    random_state는 파이프라인에서 늘 42다. 실험이 씨앗을 바꿔 가며 돌려
    greedy 탐색의 변동성을 재려고 열어 둔 인자다(experiments/baseline_compare.py).
    '''

    target_cluster_size = (TARGET_CLUSTER_SIZE if target_cluster_size is None
                           else target_cluster_size)
    wanted = int(np.ceil(len(pick_drop) / target_cluster_size))
    K = min(wanted, VEHICLES_PER_ROUND)

    if K < wanted:
        print(f"군집 개수 K = {K} (희망 {wanted} → 회차당 가용 차량 {VEHICLES_PER_ROUND}대로 제한)")
    else:
        print(f"군집 개수 K = {K}")

    # K-Medoids 클러스터링 (좌표는 스케일링하지 않는다 — 위경도 자체가 거리 단위)
    X = pick_drop[['lat', 'lon']].values

    model = KMedoids(
        n_clusters=K,
        metric='manhattan',
        method='fasterpam',
        random_state=random_state
    )

    pick_drop['cluster'] = model.fit_predict(X)

    return pick_drop


# 군집별 정보(불균형 지수 : rebal_qty, 대여소 개수)와 중심점 계산
def cal_cluster_info(pick_drop: pd.DataFrame) -> pd.DataFrame:
    cluster_balance = (     # 군집 내 대여소별 rebal_qty 합계(불균형도)
        pick_drop.groupby('cluster')['rebal_qty']
        .sum()
        .reset_index(name='balance')
    )
    #print(cluster_balance)

    cluster_size = (           # 군집별 크기
        pick_drop['cluster']
        .value_counts()
        .reset_index(name='st_qty')
    )
    #print(cluster_size)
    
    cluster_info = cluster_balance.merge(cluster_size, how='left', on='cluster').copy()
    
    mean_location = pick_drop.groupby('cluster')[['lat', 'lon']].mean()  # 군집별 중심점 좌표
    
    cluster_info = (
        cluster_info
        .merge(mean_location, how='left', on='cluster')
        .sort_values(by='cluster')
        .iloc[:, [0,2,1,3,4]]
    )

    print("-"*30); print(f"군집별 정보 : \n{cluster_info}"); print("-"*30)

    return cluster_info


# main adjust
def adjust_clustering(pick_drop):

    # 반복 상한과 판정 기준은 project_config에서 읽는다(PBR_ADJUST_* 로 조정).
    MAX_ITER = ADJUST_MAX_ITER          # 최대 대여소 이동 횟수
    THRESHOLD = ADJUST_BALANCE_OK       # 모든 군집이 이 값 이내면 만족하고 끝낸다
    BALANCE_LIMIT = ADJUST_BALANCE_LIMIT  # 초과하는 군집은 재조정 대상

    # '군집 개수'(K), '군집당 적정 크기'(K_SIZE) 계산
    K = pick_drop['cluster'].nunique()
    K_SIZE = len(pick_drop) / K

    # 대여소를 보낼/받을 군집 선정 시 size 기준
    MIN_SIZE = int(np.floor(K_SIZE - 1))
    MAX_SIZE = int(np.ceil(K_SIZE + 1))

    # 목적함수 가중치 (project_config, 환경변수 PBR_CLUSTER_ALPHA/BETA/GAMMA로 조정)
    alpha = CLUSTER_ALPHA   # 군집 내 불균형도 (balance_term)
    beta = CLUSTER_BETA     # 군집 크기 (size_term)
    gamma = CLUSTER_GAMMA   # 군집 내 노드별 거리 (distance_term)
    print(f"목적함수 가중치: alpha={alpha}, beta={beta}, gamma={gamma}")


    # < 메인 반복문 (최대 MAX_ITER 만큼의 대여소 이동 발생) >
    for iter in range(MAX_ITER):
        print("\n" + "="*70)
        print(f"{iter} 번째 iteration")

        # 상태 계산
        balance = pick_drop.groupby('cluster')['rebal_qty'].sum()
        c_size = pick_drop.groupby('cluster').size()

        # 클러스터별 상태 계산 및 출력 ()
        cluster_info = cal_cluster_info(pick_drop)

        # 종료 조건
        if balance.abs().max() <= THRESHOLD:
            print("balance threshold 만족")
            break

        # 현재 점수 계산
        current_score = compute_objective(pick_drop, K, alpha, beta, gamma)
        
        # 군집별 '중앙점' 계산
        centers = compute_medoids(pick_drop)
                                  
        # cluster 후보 선정(노드를 보낼/받을)
        from_cand, to_cand = select_cluster_candidates(balance, c_size, K_SIZE, BALANCE_LIMIT)
        
        if not from_cand or not to_cand:
            print("후보 cluster 없음")
            break

        # pair 생성
        pairs = make_cluster_pairs(centers, from_cand, to_cand)

        improved = False    # 대여소 이동 전/후의 '개선' 여부

        # 이동 시도
        for _, from_c, to_c in pairs:

            if from_c == to_c:
                continue

            # size 제약
            if not check_size_constraint(from_c, to_c, c_size, balance, MIN_SIZE, MAX_SIZE, BALANCE_LIMIT):
                continue

            # 이동 가능한 node
            nodes = get_movable_nodes(pick_drop, from_c, to_c, balance, centers)
            if nodes.empty:
                continue

            # 실제 이동
            pick_drop, improved = (
                try_move_node(pick_drop, nodes, to_c, current_score, K, alpha, beta, gamma)
            )

            if improved:
                break

        if not improved:

            print("더 이상 개선 없음")
            break

    return pick_drop



if __name__ == '__main__':

    ensure_output_dirs()

    st_info = pd.read_csv(st_info_file.format(now=now), low_memory=False, encoding='utf-8')

    # 시간대 목록은 project_config의 --duration(콤마 구분)으로 지정
    for duration in duration_list(config):
        print('-'*100)
        print(f"< {duration[1:]} > 시간대 처리")
        
        # 1차 : 재배치 대상 대여소 선택
        pick_drop = select_top_unbalanced_st(file_path.format(duration=duration, now=now), duration, st_info)

        # Pick과 Drop이 둘 다 있어야 재배치가 성립한다. 한쪽만 있는 시간대
        # (예: 모든 대여소가 과잉이라 받아줄 곳이 없음)에는 할 작업이 없다.
        # 하루 여러 회차를 돌리면 실제로 생기는 상황이므로 크래시 대신 건너뛴다.
        if pick_drop.empty:
            print(f"\n[건너뜀] {duration}: 재배치 대상이 없습니다 "
                  f"(Pick 또는 Drop 후보가 없어 옮길 곳이 없음)")
            continue

        # 2차 : 클러스터링
        pick_drop = make_clustering(pick_drop).copy()
        cluster_info = cal_cluster_info(pick_drop)
                
        # 3차 : 군집 간 불균형 조정
        pick_drop = adjust_clustering(pick_drop).copy()
        cluster_info = cal_cluster_info(pick_drop)

        # 저장
        pick_drop.to_csv(clustered_file.format(duration=duration, now=now),encoding='utf-8', index=False)
        print(f"\n{len(pick_drop)}개의 행이 저장된 {clustered_file.format(duration=duration, now=now)} 파일이 저장되었습니다.")

        # CSV·DB 이중 기록 (DB_PLAN 2단계). CSV가 아직 정본이다.
        db.save_output("pick_drop", pick_drop, run_label=now,
                       period=config.period, duration=duration)