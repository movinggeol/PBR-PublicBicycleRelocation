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
    
    # ⚡ 좌표·군집표를 **한 번만** 넘파이로 꺼낸다 (1.23.8).
    # 예전에는 군집마다 `pick_drop[pick_drop['cluster'] == c][[...]]`로 DataFrame을
    # 잘랐다. 이 함수는 목적함수 안에서 도는데, 실데이터(92곳·16군집)에서 목적함수가
    # 최악 24만 번 호출되므로 **pandas 인덱싱 비용이 전체의 대부분**이었다
    # (프로파일: `DataFrame.__getitem__` 누적 527초). 결과는 그대로다.
    labels = pick_drop['cluster'].to_numpy()
    coords = pick_drop[['lat', 'lon']].to_numpy()

    centers = {}
    for c in pd.unique(labels):
        cluster_points = coords[labels == c]
        dist_matrix = cdist(cluster_points, cluster_points, metric='cityblock')
        medoid_idx = dist_matrix.sum(axis=1).argmin()
        centers[c] = cluster_points[medoid_idx]

    centers_df = pd.DataFrame.from_dict(
        centers, orient='index', columns=['lat', 'lon']
    )
    return centers_df


def _cluster_terms(pts, qty_sum):
    """군집 하나의 (balance 제곱, 거리합). 메도이드도 여기서 고른다.

    이 함수가 조정 루프의 최안쪽이다 — 실측에서 `cdist`가 278,337회 불렸다.
    """
    d = cdist(pts, pts, metric='cityblock')
    center = pts[d.sum(axis=1).argmin()].reshape(1, -1)
    return qty_sum ** 2, float(cdist(pts, center, metric='cityblock').sum())


def _objective_parts(labels, coords, qty, K, alpha, beta, gamma, cache=None):
    """목적함수의 세 항을 **넘파이만으로** 계산한다 (1.26.8).

    `compute_objective`가 하던 일과 **결과가 같다.** 다른 것은 pandas를 거치지
    않는다는 점뿐이다 — 이 함수는 군집 조정의 안쪽 고리에서 수천 번 불린다
    (실측 7,855회·29.6초, 그중 pandas `__getitem__`이 12초였다).

    `cache`를 주면 **군집별 항을 재사용**한다. 대여소 하나를 옮기면 바뀌는 군집은
    **떠난 곳·받는 곳 둘뿐**인데, 예전에는 16개를 전부 다시 쟀다.

    ⚠️ **캐시를 써도 결과가 같아야 한다.** 합산은 늘 `np.unique(labels)` 순서로
    하므로 부동소수 덧셈 순서가 바뀌지 않는다 — 이것이 지켜지지 않으면 마지막
    자리가 흔들려 채택 여부가 달라질 수 있다.

    labels: (n,) 군집 라벨 · coords: (n, 2) 위경도 · qty: (n,) rebal_qty
    """
    uniq = np.unique(labels)

    balance_term = 0.0
    size_term_acc = 0.0
    distance_term = 0.0
    ideal = len(labels) / K

    for c in uniq:
        mask = labels == c
        size_term_acc += (mask.sum() - ideal) ** 2

        key = None
        if cache is not None:
            # 군집의 구성원 집합이 같으면 balance·거리도 같다.
            key = (int(c), np.flatnonzero(mask).tobytes())
            hit = cache.get(key)
            if hit is not None:
                balance_term += hit[0]
                distance_term += hit[1]
                continue

        b, d = _cluster_terms(coords[mask], float(qty[mask].sum()))
        if cache is not None:
            cache[key] = (b, d)
        balance_term += b
        distance_term += d

    # ⚠️ size_term은 **등장한 군집 수**로 나눈다 — pandas value_counts()가
    # 빈 군집을 빼고 세던 것과 같게 하려면 len(uniq)여야 한다.
    size_term = size_term_acc / len(uniq)
    return alpha * balance_term + beta * size_term + gamma * distance_term


def compute_objective(pick_drop: pd.DataFrame, K: int, alpha: float, beta: float,
                      gamma: float, verbose: bool = False):
    '''목적함수값(balance, size, distance) 계산.

    파이프라인·실험이 부르는 바깥 문이다. 안쪽 계산은 `_objective_parts()`가
    넘파이로 한다 — 결과는 같고 pandas 왕복이 없다.
    '''
    labels = pick_drop['cluster'].to_numpy()
    coords = pick_drop[['lat', 'lon']].to_numpy()
    qty = pick_drop['rebal_qty'].to_numpy()
    score = _objective_parts(labels, coords, qty, K, alpha, beta, gamma)

    if verbose:
        print("objective:", score)
    return score


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
        pick_drop: pd.DataFrame, nodes: pd.DataFrame, to_c: int, current_score: float,                     K: int, alpha: float, beta: float, gamma: float
        ):
    '''대여소 하나를 `to_c`로 옮겨 점수가 내려가면 채택한다.

    **후보마다 DataFrame을 복사하지 않는다** (1.26.8). 예전에는 후보 하나를
    시험할 때마다 `pick_drop.copy()`를 하고 목적함수를 pandas로 다시 냈다 —
    실데이터에서 목적함수가 7,855회 불려 29.6초가 들었고, 그중 pandas
    `__getitem__`만 12초였다.

    지금은 **라벨 배열 한 칸만 바꿔** 넘파이로 점수를 내고, **채택된 경우에만**
    DataFrame을 복사한다. 결과는 예전과 완전히 같다(무작위 입력 검증).
    '''
    labels = pick_drop['cluster'].to_numpy().copy()
    coords = pick_drop[['lat', 'lon']].to_numpy()
    qty = pick_drop['rebal_qty'].to_numpy()

    # 후보를 하나씩 시험하는 동안 **손대지 않은 군집은 그대로**다. 그 항을
    # 재사용한다 — 후보마다 16개 군집을 전부 다시 재던 것을 2개로 줄인다.
    cache = {}

    # 행 라벨(index) → 배열 위치. nodes가 넘겨주는 것은 DataFrame의 index다.
    position = {label: i for i, label in enumerate(pick_drop.index)}

    for idx, row in nodes.iterrows():
        i = position.get(idx)
        if i is None:
            continue

        before = labels[i]
        if before == to_c:
            continue

        labels[i] = to_c
        new_score = _objective_parts(labels, coords, qty, K, alpha, beta, gamma,
                                     cache=cache)

        if new_score < current_score:
            print(
                f"✅ MOVE "
                f"{row['station_name']} "
                f"({row['rebal_qty']})"
                f"\n 현재 점수 : {current_score}"
            )
            moved = pick_drop.copy()
            moved.loc[idx, 'cluster'] = to_c
            return moved, True

        labels[i] = before          # 되돌린다 — 다음 후보를 같은 조건에서 본다

    return pick_drop, False
