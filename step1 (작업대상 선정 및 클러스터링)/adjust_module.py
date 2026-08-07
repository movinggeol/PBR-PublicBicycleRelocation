from scipy.spatial.distance import cdist
import pandas as pd
import numpy as np

def compute_medoids(pick_drop: pd.DataFrame):
    '''
    각 군집에 속한 대여소 좌표를 넘파이 배열로 바꾼 후
    반복문 속 
        [ [클러스터 C의 c_1번 대여소], [클러스터 C의 c_2 대여소] ... [클러스터 C의 c_e 대여소]] 형태에서
        소속 대여소들끼리의 거리를 계산
            [ [0. 0.1 0.2], [0.1, 0, 0.3], [0.2, 0.3, 0]] : 클러스터 C 안의 [ c_1대여소~~c_2,c_3[], c_2대여소~~[], c_3대여소~~[] ]
        sum(axis=1).armin() 을 통해 [ [0.3], [0.4], [0.5] ] 클러스터 C 내의 모든 대여소와의 거리합이 최소인 점(c_3)의 인덱스를 추출해 C의 중앙값으로 채택
    '''
    
    centers = {}

    for c in pick_drop['cluster'].unique():
        cluster_points = pick_drop[pick_drop['cluster'] == c][['lat', 'lon']].values
        dist_matrix = cdist(cluster_points, cluster_points, metric='cityblock')
        medoid_idx = dist_matrix.sum(axis=1).argmin()
        
        #print(dist_matrix.sum(axis=1))

        centers[c] = cluster_points[medoid_idx]
        
    centers_df = pd.DataFrame.from_dict(
        centers, orient='index', columns=['lat', 'lon']
    )
    return centers_df


def compute_objective(pick_drop:pd.DataFrame, K:int, alpha:float, beta:float, gamma:float):
    '''
    목적함수값(balance, size, distance) 계산
    '''
    balance = pick_drop.groupby('cluster')['rebal_qty'].sum()
    balance_term = (balance**2).sum()          # 대여소별 abs(불균형도) 합계

    size = pick_drop['cluster'].value_counts()
    ideal_size = len(pick_drop) / K                  # 목표 군집 크기(전체 / 군집 수 = 평균)
    size_term = ((size - ideal_size)**2).mean()      # 군집별 크기 균일화를 위한 분산 (편차 제곱 평균)
    
    medoid = compute_medoids(pick_drop)
    distance_term = 0

    # 거리 점수 계산
    for c in pick_drop['cluster'].unique():
        cluster_points = pick_drop[pick_drop['cluster'] == c][['lat', 'lon']].values
        #print(medoid)

        # (2, 1) 데이터(좌표 하나)를 cdist를 위해 (1,2)로 변환(좌표 데이터를 1개 가진 2차원 테이블) [lat, lon] -> [ [lat, lon] ]
        center = medoid.loc[c].values.reshape(1,-1) 
        #print(center)

        dist = cdist(cluster_points, center, metric='cityblock')

        # distance_term = Σ_i distance(point_i, center)
        distance_term += dist.sum()
    
    print("balance_term:", balance_term, end='\t')
    print("size_term:", size_term, end='\t')
    print("distance_term:", distance_term)
    
    objective_score = alpha*balance_term + beta*size_term + gamma*distance_term
    return objective_score


def select_cluster_candidates(balance : pd.Series, c_size: pd.Series, K_SIZE: int, BALANCE_LIMIT: int):
    '''
    노드를 보낼/받을 클러스터 후보 선정
    '''
    # 기본 : size 기준
    from_cand = c_size[c_size > K_SIZE].index.tolist()
    to_cand = c_size[c_size < K_SIZE].index.tolist()

    # balance_emergency cluster
    balance_emergency_clusters = balance[balance.abs() > BALANCE_LIMIT].index.tolist()

    # balance_emergency mode
    for e in balance_emergency_clusters:
        e_size = c_size[e]
        e_balance = balance[e]

        if e_size > K_SIZE+1:      # 너무 큰 cluster
            if e not in from_cand:
                from_cand.append(e)

        elif e_size < K_SIZE-1:    # 너무 작은 cluster
            if e not in to_cand:
                to_cand.append(e)

        else:                       # size 정상 범위
            # pick 과잉
            if e_balance < 0:
                if e not in from_cand:
                    from_cand.append(e)

                opposite = balance[balance > 0].index.tolist()
                for oc in opposite:
                    if oc not in to_cand:
                        to_cand.append(oc)

            # drop 과잉
            else:
                if e not in to_cand:
                    to_cand.append(e)

                opposite = balance[balance < 0].index.tolist()
                for oc in opposite:
                    if oc not in from_cand:
                        from_cand.append(oc)

    # 중복 제거
    from_cand = list(set(from_cand))
    to_cand = list(set(to_cand))

    return from_cand, to_cand



def make_cluster_pairs(centers: pd.DataFrame, from_cand: list, to_cand: list) :
    '''
    cluster pair 생성 (클러스터별 거리 기준)
    '''
    from_coords = centers.loc[from_cand]
    to_coords = centers.loc[to_cand]

    dist_matrix = cdist(from_coords, to_coords, metric='cityblock')

    dist_df = pd.DataFrame(dist_matrix, index=from_cand, columns=to_cand)

    pairs_df = dist_df.stack().reset_index()
    pairs_df.columns = ['from_c', 'to_c', 'dist']

    pairs = list(
        pairs_df[['dist', 'from_c', 'to_c']]
        .itertuples(index=False, name=None)
    )
    pairs.sort(key=lambda x: x[0])

    return pairs


def get_movable_nodes(pick_drop: pd.DataFrame, from_c: int, to_c: int, balance: pd.DataFrame, centers: pd.DataFrame):
    '''
    이동 가능한 node 추출
    '''

    # from
    nodes = pick_drop[pick_drop['cluster'] == from_c].copy()

    if balance[from_c] < 0:                     # balance < 0 : pick 이동
        nodes = nodes[nodes['rebal_qty'] < 0]
    else:                                       # balance > 0 : drop 이동
        nodes = nodes[nodes['rebal_qty'] > 0]

    if nodes.empty:
        return nodes

    # '보낼 군집'과 가까운 node 우선 (맨해튼 거리 기반)
    nodes['dist'] = (
        abs(nodes['lat'] - centers.loc[to_c, 'lat']) +
        abs(nodes['lon'] - centers.loc[to_c, 'lon'])
    )

    nodes = nodes.sort_values('dist')

    return nodes



def check_size_constraint(from_c: int, to_c: int, c_size: pd.DataFrame, balance: pd.DataFrame, MIN_SIZE: int, MAX_SIZE: int, BALANCE_LIMIT: int):
    '''
    size constraint 확인 
    (크기 > 특정값 : sender, 크기 < 특정값 : receiver)
    '''

    from_balance = balance[from_c]

    # balance가 너무 불균형한 군집
    balance_emergency = abs(from_balance) > BALANCE_LIMIT
    if balance_emergency:
        return True

    from_size = c_size[from_c]
    to_size = c_size[to_c]

    if from_size <= MIN_SIZE:
        return False

    if to_size >= MAX_SIZE:
        return False

    return True


def try_move_node(
        pick_drop: pd.DataFrame, nodes: pd.DataFrame, to_c: int, current_score: float, \
                    K: int, alpha: float, beta: float, gamma: float
        ):
    '''
    node 이동 시도
    '''
    for idx, row in nodes.iterrows():
        temp = pick_drop.copy()
        temp.loc[idx, 'cluster'] = to_c

        new_score = compute_objective(temp, K, alpha, beta, gamma)

        if new_score < current_score:
            print(
                f"✅ MOVE "
                f"{row['station_name']} "
                f"({row['rebal_qty']})"
                f"\n 현재 점수 : {current_score}"
            )

            return temp, True

    return pick_drop, False