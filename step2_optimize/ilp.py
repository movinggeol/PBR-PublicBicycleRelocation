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

# 이동 속도는 project_config 한 곳에서 읽는다 — VRP와 반드시 같은 값이어야 한다.
# (1.13.2 이전에는 ILP 25 / VRP 30으로 갈려 ILP가 고른 조합이 VRP에서는 최소가 아니었다.)
vehicle_speed_kmph = VEHICLE_SPEED_KMPH

# ILP 솔버 설정. 실측하면 클러스터 하나가 0.05~0.2초에 풀리고 갭 2%가 남기는 손해는
# 0%다 — 지금 규모에서는 두 값 모두 아무 일도 하지 않는 안전장치다(사용자 결정, 1.18.7).
SOLVER_TIME_LIMIT_SEC = 600
SOLVER_GAP_REL = 0.02


def build_solver(msg: bool = False, time_limit: int = SOLVER_TIME_LIMIT_SEC,
                 gap_rel: float = SOLVER_GAP_REL) -> pulp.LpSolver:
    """CBC 솔버를 만든다. **PuLP 4.0에서 `PULP_CBC_CMD`가 사라진다.**

    지금은 PuLP가 CBC 바이너리를 동봉해 `PULP_CBC_CMD`가 그냥 된다. 4.0부터는
    `pip install pulp[cbc]`가 설치하는 `cbcbox`(약 150MB)를 `COIN_CMD`로 써야 한다.
    `requirements.txt`는 하한(`>=`) 고정이라 그날 새 환경에서 설치하면 step2가
    통째로 깨지므로, **있는 것을 골라 쓰도록** 해 둔다.

    순서에 뜻이 있다:
      1. `PULP_CBC_CMD` — **문서의 모든 수치가 이걸로 나왔다.** 있으면 그대로 쓴다.
      2. `COIN_CMD` — PATH의 cbc, 없으면 `cbcbox`가 알려 주는 경로.
      3. 둘 다 없으면 **무엇을 설치해야 하는지 알려 주고 멈춘다.**
         AttributeError로 죽는 것보다 낫다.
    """
    legacy = getattr(pulp, "PULP_CBC_CMD", None)
    if legacy is not None:
        solver = legacy(msg=msg, timeLimit=time_limit, gapRel=gap_rel)
        if solver.available():
            return solver

    solver = pulp.COIN_CMD(msg=msg, timeLimit=time_limit, gapRel=gap_rel)
    if solver.available():
        return solver

    try:
        import cbcbox
        solver = pulp.COIN_CMD(path=cbcbox.cbc_bin_path(), msg=msg,
                               timeLimit=time_limit, gapRel=gap_rel)
        if solver.available():
            return solver
    except ImportError:
        pass

    raise SystemExit(
        "CBC 솔버를 찾지 못했습니다. `pip install pulp[cbc]`로 설치한 뒤 다시 실행하세요."
        " (PuLP 4.0부터 PULP_CBC_CMD가 없어져 CBC를 따로 받아야 합니다 —"
        " docs/TODO.md P2-B)")


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
    # `LpVariable.dicts`도 PuLP 4.0에서 사라진다 — 새 API가 있으면 그쪽을 쓴다.
    keys = [(i, j) for i in I for j in J]
    if hasattr(prob, 'add_variable_dicts'):
        x = prob.add_variable_dicts('x', keys, lowBound=0, cat=pulp.LpInteger)
    else:
        x = pulp.LpVariable.dicts('x', keys, lowBound=0, cat=pulp.LpInteger)

    # 목적함수 : **대수 가중 이동시간**의 합 최소화 = 가까운 곳끼리 많이 옮기도록 유도
    #
    # ⚠️ 이것은 '실제 운행시간'이 아니다. 차량은 한 번에 최대 10대를 싣고 가므로
    # 실제 소요시간은 방문 순서(VRP)가 정한다. 여기서는 자전거 1대가 i -> j로
    # 옮겨지는 데 드는 시간을 대수만큼 더한 **대리 목적함수**를 쓴다.
    # 논문에 '총 작업시간 최소화'라고 쓰면 오해를 부른다 (docs/FORMULATION.md 5장).
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
    status = pulp.LpStatus[prob.status]
    print("Status:", status)

    # 최적해가 아니면 **조용히 넘어가면 안 된다.** 시간 제한에 걸리거나 모델이
    # 불가능해지면 계획이 통째로 비거나 부실해지는데, 예전에는 상태를 찍기만 하고
    # 그대로 진행했다. 계획이 비는 것과 '옮길 게 없는 것'은 다르다.
    if prob.status != pulp.LpStatusOptimal:
        print(f"[경고] ILP가 최적해를 못 찾았습니다 (status={status}). "
              f"이 클러스터의 계획이 부실하거나 비어 있을 수 있습니다.")

    rows = []
    moved = 0
    for i in I:
        for j in J:
            # **반올림해야 한다.** 정수변수라도 솔버는 4.999999999를 돌려줄 수 있고
            # int()는 0 방향으로 잘라 자전거를 조용히 잃는다. 현재 CBC는 정확한 값을
            # 주지만(40회 시행 오차 0), 솔버를 바꾸면 달라질 수 있다
            # — PuLP 4.0에서 PULP_CBC_CMD가 없어진다(docs/TODO.md).
            v = int(round(pulp.value(x[(i,j)]) or 0))
            if v > 0:
                moved += v
                rows.append({'pick_station_id': i,
                             'drop_station_id': j,
                             'qty': v,
                             'travel_time_sec': T[(i,j)]})

    # 총 이동량이 강제 제약과 맞는지 확인한다. 어긋나면 뒤 단계(VRP)의 수급 균형이
    # 깨져 greedy가 교착에 빠진다 — 거기서 터지기 전에 여기서 알아야 한다.
    if moved != move_total:
        print(f"[경고] 계획 합계가 강제 이동량과 다릅니다 ({moved} != {move_total}). "
              f"솔버 해를 확인하세요.")
    return rows


def run_ilp_plan(metrics: pd.DataFrame, duration: str, solver: pulp.LpSolver):
    '''
    재배치 후보 대여소(metrics) -> pick / drop 분리 ->  대여소 간 이동시간 계산 -> 
    ILP 문제 생성 -> 목적함수 정의 -> 제약조건 추가 -> 
    CBC Solver 최적화 -> 최적 재배치 계획 저장
    '''

    # rebal_qty 부호를 공급(pick)·수요(drop)로 편다. 호출자의 프레임을 건드리지
    # 않도록 복사본에 붙인다.
    metrics = metrics.copy()
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
    # 어떤 CBC를 쓸지는 build_solver()가 고른다 — PuLP 4.0 대비.
    solver = build_solver(msg=True)

    for duration in duration_list(config):
        candidates = Path(metrics_path.format(duration=duration, now=now))
        if not candidates.is_file():
            # step1이 '대상 없음'으로 건너뛴 시간대. 여기서도 건너뛴다.
            print(f"\n[건너뜀] {duration}: 후보 파일이 없습니다 ({candidates.name})")
            continue

        metrics = pd.read_csv(candidates, encoding='utf-8', low_memory=False)
        print(f"\n=== [ILP @ {duration}] ===")
        run_ilp_plan(metrics, duration, solver)