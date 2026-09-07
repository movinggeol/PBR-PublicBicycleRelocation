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
import project_config
from project_config import (
    DATA_ROOT, ADJUST_BALANCE_LIMIT, ADJUST_BALANCE_OK, ADJUST_MAX_ITER,
    CLUSTER_ALPHA, CLUSTER_BETA, CLUSTER_GAMMA, CLUSTER_SEED,
    PROJECT_ROOT, REBAL_MIN_QTY,
    CLUSTER_IMBALANCE_ALLOWANCE, DEPOT_LAT, DEPOT_LON, DROP_TIME_SEC,
    PICK_TIME_SEC, TIME_BUDGET_MINUTES, TOP_STATION_LIMIT,
    TRAVEL_MIN_PER_STATION, VEHICLES_PER_ROUND,
    duration_list, ensure_output_dirs, get_runtime_config, require_columns,
    travel_seconds,
)

# BHH(Beardwood-Halton-Hammersley) 상수 — 넓이 A에 무작위로 흩어진 점 n개를
# 잇는 최단 순회 경로 길이가 대략 k*sqrt(n*A)로 수렴한다는 실험적 근거값이다
# (평면 유클리드 TSP, k ≈ 0.7124). 정확한 최적해가 아니라 **자릿수 근사**로 쓴다 —
# 실제 순회는 K-Medoids·greedy가 정하고, 여기서는 "군집 몇 개가 필요한가"만 가늠한다.
_BHH_CONSTANT = 0.7124
_KM_PER_LAT_DEG = 111.0

# 순회거리를 어림하는 방법. BHH는 **면적**만 보고, 나머지 둘은 **실제 좌표쌍**을 본다.
#   bhh : 0.7124·sqrt(n·면적)  — 균일분포 가정. 실제 후보는 도로·생활권을 따라
#         뭉치므로 같은 면적이라도 실제 순회가 더 길다 → **낙관적으로 어림한다**
#         (1.26.10에서 K를 너무 작게 뽑아 예산 초과가 2.2배가 된 원인).
#   nn  : 최근접 이웃 순회 길이 — 실제 배치 그대로. TSP 상계에 가깝다.
#   mst : 최소 신장 트리 길이 — TSP 최적해의 **하계**(최적 ≤ 2·MST, 실제로는
#         MST의 1.1~1.3배 근처). 뭉친 분포에서 nn보다 덜 부풀린다.
GEO_METHODS = ("bhh", "nn", "mst")


def _pairwise_km(lat, lon):
    """좌표 배열의 모든 쌍 거리(km) 행렬. 후보 90여 곳 규모라 비용이 문제 안 된다."""
    lat_r, lon_r = np.radians(lat), np.radians(lon)
    dlat = lat_r[:, None] - lat_r[None, :]
    dlon = lon_r[:, None] - lon_r[None, :]
    a = (np.sin(dlat / 2) ** 2
         + np.cos(lat_r)[:, None] * np.cos(lat_r)[None, :] * np.sin(dlon / 2) ** 2)
    return 2 * 6371.0 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def _nn_tour_km(dist) -> float:
    """최근접 이웃 순회 길이. 첫 점으로 돌아오는 것까지 센다.

    **BHH와 달리 분포 가정이 없다** — 뭉쳐 있으면 짧고 흩어져 있으면 길다는 것을
    실제 좌표가 말해 준다. 시작점은 0번으로 고정한다(결정적이어야 한다).
    """
    n = len(dist)
    if n < 2:
        return 0.0
    unvisited = set(range(1, n))
    total, here = 0.0, 0
    while unvisited:
        nxt = min(unvisited, key=lambda j: dist[here][j])
        total += float(dist[here][nxt])
        unvisited.discard(nxt)
        here = nxt
    return total + float(dist[here][0])


def _mst_km(dist) -> float:
    """최소 신장 트리 길이 (Prim). TSP 최적해의 하계다."""
    n = len(dist)
    if n < 2:
        return 0.0
    reached = np.zeros(n, dtype=bool)
    reached[0] = True
    best = dist[0].copy()
    best[0] = np.inf
    total = 0.0
    for _ in range(n - 1):
        j = int(np.argmin(np.where(reached, np.inf, best)))
        total += float(best[j])
        reached[j] = True
        best = np.minimum(best, dist[j])
    return total


def total_tour_km(pick_drop: pd.DataFrame, method: str = "bhh") -> float:
    """후보 집합 전체를 한 번 도는 데 드는 거리(km) 어림. depot 왕복은 뺀 값이다."""
    n = len(pick_drop)
    if n < 2:
        return 0.0

    lat = pick_drop["lat"].to_numpy(dtype=float)
    lon = pick_drop["lon"].to_numpy(dtype=float)

    if method == "bhh":
        lat_mid = np.radians(lat.mean())
        width_km = (lon.max() - lon.min()) * _KM_PER_LAT_DEG * np.cos(lat_mid)
        height_km = (lat.max() - lat.min()) * _KM_PER_LAT_DEG
        area_km2 = max(abs(width_km) * abs(height_km), 0.01)
        return float(_BHH_CONSTANT * np.sqrt(n * area_km2))

    dist = _pairwise_km(lat, lon)
    if method == "nn":
        return _nn_tour_km(dist)
    if method == "mst":
        return _mst_km(dist)
    raise ValueError(f"모르는 어림 방법: {method!r} ({', '.join(GEO_METHODS)} 중에서)")


def _estimate_travel_km_per_vehicle(pick_drop: pd.DataFrame, k: int,
                                    method: str = "bhh") -> float:
    """군집을 나누기 **전** 후보 집합의 기하만으로, 차량 1대가 감당할 이동거리(km)를 어림한다.

    총 순회거리는 BHH 근사(0.7124·sqrt(n·면적))로 잡고, 이것을 K등분해
    군집당 몫으로 삼는다 — 1.26.9 실측이 "군집을 쪼개도 이동 총합은 거의
    안 변한다"고 확인했으므로 K에 따라 줄어들지 않는 편이 안전하다. 여기에
    차량마다 발생하는 depot 왕복(편도 거리 × 2)을 더한다.

    좌표계를 정확히 투영하지 않고 위경도 1도 ≈ 111km(경도는 위도의 cos배)로만
    변환한다 — K를 정하기 위한 자릿수 어림이지, 실제 경로 계산(haversine 기반)을
    대체하지 않는다.
    """
    n = len(pick_drop)
    if n == 0:
        return 0.0

    lat = pick_drop["lat"].to_numpy(dtype=float)
    lon = pick_drop["lon"].to_numpy(dtype=float)

    tour_km = total_tour_km(pick_drop, method)

    dlat = np.radians(lat - DEPOT_LAT)
    dlon = np.radians(lon - DEPOT_LON)
    p1, p2 = np.radians(DEPOT_LAT), np.radians(lat)
    a = np.sin(dlat / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlon / 2) ** 2
    depot_km = float((2 * 6371.0 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))).mean())

    return tour_km / k + 2 * depot_km

# read_csv
file_path = str(DATA_ROOT / "pp_data/재배치 정보/rebal_qty{duration} ({now}).csv")
st_info_file = str(DATA_ROOT / "pp_data/대여소 정보/st_info ({now}).csv")

# to_csv
clustered_file = str(DATA_ROOT / "pp_data/ILP/후보/top{duration} ({now}).csv")

config = get_runtime_config()
now = config.now

def select_top_unbalanced_st(file_path:str, duration:str, st_info:pd.DataFrame) -> pd.DataFrame:
    '''
    1차 : 재배치 작업 시, 대상 대여소(pick/drop)를 선택한다.
    '''

    types = ['pick', 'drop']

    # file_path는 호출부에서 이미 완전히 포맷된 경로
    st_rebal = pd.read_csv(file_path, encoding='utf-8', low_memory=False)
    require_columns(st_rebal, ['station_id', 'mu', 'sigma', 'parking_lot', 'stock',
                               'target_qty', 'rebal_qty'], f'step0 재배치량 {duration}')
    require_columns(st_info, ['station_id', 'station_name', 'lat', 'lon',
                              'parking_lot', 'stock'], 'step0 대여소 정보')

    # 이름으로 고른다 — 예전엔 위치(iloc)로 골라 입력 컬럼 순서가 바뀌면
    # 조용히 엉뚱한 값을 썼다(docs/기록/TODO.md P3-0). parking_lot·stock은
    # 두 프레임에 모두 있어 이름이 겹치므로, **재배치량 파일(st_rebal) 쪽 값을
    # 그대로 쓴다** — st_info 쪽은 `_info` 접미사를 붙여 밀어낸다.
    st = (
        st_rebal[abs(st_rebal['rebal_qty']) > REBAL_MIN_QTY]
        .merge(st_info, how='left', on='station_id', suffixes=('', '_info'))
        [['station_id', 'station_name', 'lat', 'lon', 'parking_lot', 'stock',
          'target_qty', 'rebal_qty', 'mu', 'sigma']]
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


def wanted_vehicles(pick_drop: pd.DataFrame, geo: bool = None) -> int:
    """이번 회차의 작업량으로 **필요한 차량(=군집) 수**를 추정한다.

    한 대가 감당할 수 있는 양을 시간으로 따진다.

      작업시간 = 처리 대수 × (싣기 + 내리기)        — 정확히 계산된다
      이동시간 = 대여소 수 × TRAVEL_MIN_PER_STATION  — 실측 계수
      필요 대수 = ceil((작업 + 이동) × 불균형 여유 / 시간 예산)

    이동을 **대여소 수에 비례**하게 잡는 근거: 같은 재고로 K만 바꿔 재 보면
    총 소요시간이 거의 변하지 않는다(K=10 → 18에서 이동분/곳 10.2 → 13.0).
    군집을 쪼개면 depot 왕복이 늘지만 군집 안 이동이 그만큼 줄기 때문이다.
    근거와 계수는 project_config와 docs/분석/EXPERIMENTS.md에 있다.

    ⚠️ **이 식은 거리를 안 본다** — `project_config.WANTED_VEHICLES_GEO`
    (기본 꺼짐)를 켜거나 `geo=True`를 넘기면 `_wanted_vehicles_geo()`가
    후보 집합의 기하로 이동거리를 어림해 대신 쓴다(EXPERIMENTS.md 5-G장).
    `geo` 인자는 실험이 project_config를 건드리지 않고 바로 비교하기 위한 것이다.

    **저장하지 않는 순수 계산이다** — 실험이 계수를 바꿔 가며 직접 부른다.
    """
    if pick_drop.empty:
        return 1

    use_geo = project_config.WANTED_VEHICLES_GEO if geo is None else geo
    if use_geo:
        return _wanted_vehicles_geo(pick_drop)

    # 처리 대수는 drop 합(= |pick 합|)이다. 앞의 cut_point가 두 쪽을 맞춰 뒀다.
    # pick·drop을 모두 더하면 한 대를 두 번 세어 2배가 된다.
    bikes = float(pick_drop.loc[pick_drop['rebal_qty'] > 0, 'rebal_qty'].sum())

    work_min = bikes * (PICK_TIME_SEC + DROP_TIME_SEC) / 60.0
    travel_min = len(pick_drop) * TRAVEL_MIN_PER_STATION
    needed = (work_min + travel_min) * CLUSTER_IMBALANCE_ALLOWANCE / TIME_BUDGET_MINUTES
    return max(1, int(np.ceil(needed)))


def _wanted_vehicles_geo(pick_drop: pd.DataFrame) -> int:
    """`wanted_vehicles()`의 거리 인지 버전 — 차량 1대의 시간을 직접 예산과 견준다.

      차량 1대 시간 = [ 처리 대수 ÷ K × (싣기+내리기)
                      + travel_seconds(순회거리 ÷ K + depot 왕복) ] × 불균형 여유

    순회거리(`_estimate_travel_km_per_vehicle`)는 K가 늘어도 거의 안 변한다는
    실측(1.26.9)을 따라 K로 나누지만, depot 왕복은 차량마다 **한 번씩 더** 드는
    비용이라 나누지 않는다. K가 클수록 두 항 다 줄거나 그대로이므로 시간은
    K에 대해 단조 감소한다 — 예산을 넘지 않는 가장 작은 K를 앞에서부터 찾는다.
    `travel_seconds()`를 거치므로 `USE_ROAD_MODEL`을 켜면 이 추정도 같은
    고정비+거리비례 식을 자동으로 쓴다.
    """
    bikes = float(pick_drop.loc[pick_drop['rebal_qty'] > 0, 'rebal_qty'].sum())
    n = len(pick_drop)

    for k in range(1, n + 1):
        travel_km = _estimate_travel_km_per_vehicle(pick_drop, k)
        vehicle_min = (
            bikes / k * (PICK_TIME_SEC + DROP_TIME_SEC) / 60.0
            + travel_seconds(travel_km) / 60.0
        ) * CLUSTER_IMBALANCE_ALLOWANCE
        if vehicle_min <= TIME_BUDGET_MINUTES:
            return k
    return n


def make_clustering(pick_drop: pd.DataFrame,
                    random_state: int = CLUSTER_SEED) -> pd.DataFrame:
    '''
    # 2차 : 클러스터링(K-Medoids)

    군집 1개 = 차량 1대가 맡는 작업이므로, 군집 수는 한 회차에 투입할 수 있는
    차량 수를 넘을 수 없다. 상한에 걸리면 군집이 커지고 차량당 작업량이 늘어난다.
    (docs/구현/FLEET.md)

    random_state의 기본값은 `CLUSTER_SEED`(기본 42)다. 실험이 씨앗을 바꿔 가며
    돌려 greedy 탐색의 변동성을 재려고 열어 둔 인자이고(experiments/baseline/
    baseline_compare.py), **파이프라인 경로에서는 `--seed`/`PBR_CLUSTER_SEED`가
    이 기본값을 움직인다**(1.26.56). 예전에는 여기 42가 박혀 있어 전체 실행으로
    얻은 표를 다른 씨앗으로 재볼 방법이 아예 없었다.
    '''

    wanted = wanted_vehicles(pick_drop)
    K = min(wanted, VEHICLES_PER_ROUND)

    if K < wanted:
        print(f"군집 개수 K = {K}"
              f" (작업량 기준 {wanted}대 필요 → 회차당 가용 차량 {VEHICLES_PER_ROUND}대로 제한)")
        print(f"  ⚠ {K}대로는 시간 예산({TIME_BUDGET_MINUTES:.0f}분)을 넘기는 회차가 생깁니다."
              f" 회차당 투입 대수를 늘리거나 작업 대상을 줄여야 합니다.")
    else:
        print(f"군집 개수 K = {K} (작업량 기준)")

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