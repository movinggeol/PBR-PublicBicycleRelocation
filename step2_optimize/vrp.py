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
    DATA_ROOT, DEPOT_ID, DEPOT_LAT, DEPOT_LON, DROP_TIME_SEC, PICK_TIME_SEC, PROJECT_ROOT,
    ENFORCE_TIME_BUDGET, TIME_BUDGET_MINUTES, VEHICLE_CAPACITY,
    VEHICLE_SPEED_KMPH,
    duration_list, ensure_output_dirs, get_runtime_config, require_columns,
    travel_seconds,
)

# read_csv
metrics_file = str(DATA_ROOT / "pp_data/ILP/후보/top{duration} ({now}).csv")  # lat/lon 포함
ilp_plan_file = str(DATA_ROOT / "pp_data/ILP/ILP_plan{duration} ({now}).csv")
# to_csv
vrp_plan_file = str(DATA_ROOT / "pp_data/VRP/VRP_plan{duration} ({now}).csv")

config = get_runtime_config()
now = config.now

# 이동 속도(VEHICLE_SPEED_KMPH)와 작업시간(PICK/DROP_TIME_SEC)은 project_config
# 한 곳에서 읽는다 — 속도는 ILP와 같은 값이어야 한다.
# (1.13.2 이전에는 여기서 30을 따로 쓰고 ILP는 25를 써서 두 단계가 어긋나 있었다.
#  작업시간은 1.18.6까지 이 파일에 박혀 있어 다른 운영 상수와 따로 놀았다.)

# 한 회차에 차량 1대가 클러스터 1개를 맡고 depot으로 복귀한다(사용자 결정, 1.13.2).
# 여러 클러스터를 이어 도는 구조는 채택하지 않았다 — docs/구현/FLEET.md '운용 모델' 참고.


def _travel_sec(km: float) -> float:
    """ILP와 **같은 함수**를 쓴다 — 두 단계가 갈리면 ILP의 최적해가 VRP에서
    최소가 아니게 된다(1.13.2에서 겪음)."""
    return travel_seconds(km)


def _depot_return(cluster, from_id, from_lat, from_lon, cum_sec: float):
    """현재 위치에서 depot으로 돌아오는 행과 갱신된 누적시간을 만든다.

    복귀는 두 곳에서 난다 — 중간에 처리할 것이 없어 되돌아갈 때와, 작업을 마치고
    돌아올 때. 두 곳이 행을 따로 만들면 컬럼이 어긋난다.
    """
    distance = haversine_km(from_lat, from_lon, DEPOT_LAT, DEPOT_LON)
    travel = _travel_sec(distance)
    cum_sec += travel
    row = {
        'cluster': cluster,
        'from_id': from_id,
        'from_lat': from_lat,
        'from_lon': from_lon,
        'to_id': DEPOT_ID,
        'to_lat': DEPOT_LAT,
        'to_lon': DEPOT_LON,
        'action': 'return',
        'qty': 0,
        'distance_km': round(distance, 3),
        'travel_sec': round(travel, 1),
        'work_sec': 0.0,
        'cum_sec': round(cum_sec, 1),
    }
    return row, cum_sec


def greedy_route(nodes: dict, cluster, time_budget_sec: float = None) -> list:
    """노드 목록을 받아 차량 1대의 방문 순서를 greedy로 만든다.

    **저장하지 않는 순수 계산이다.** 파일·DB에 남기는 것은 run_vrp_plan()이고,
    실험 스크립트(experiments/baseline/baseline_compare.py)는 이 함수를 직접 쓴다 —
    측정 코드와 운영 코드가 갈리면 비교가 성립하지 않는다.

    nodes: {(station_id, 'pick'|'drop'): {'qty', 'lat', 'lon'}}  (호출 측에서 소모된다)
    time_budget_sec: 주면 예산을 넘기는 작업 앞에서 멈추고 depot으로 돌아온다.
        **파이프라인은 주지 않는다**(None) — 현행 설계에서 시간 예산은 제약이 아니라
        사후 점검이다(docs/기록/RETROSPECTIVE.md 6장). 대조군처럼 클러스터 없이 한 대가
        전체 후보를 훑는 경우에는 멈출 곳이 있어야 해서 넣어 둔 선택 인자다.
    반환: VRP 행 목록(from/to·action·qty·거리·시간)
    """
    results = []

    # depot 출발, 적재 0
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

        # 작업 불가 상황 -> depot 복귀
        #
        # ⚠️ **파이프라인 입력에서는 이 분기가 실행될 수 없다.** ILP가 클러스터마다
        # 총 pick = 총 drop으로 맞춰 주기 때문이다(solve_cluster_moves의 '작업량 강제'
        # 제약). 임의 시점에 `남은 drop = 남은 pick + 적재량`이므로,
        #   · 적재가 꽉 차 못 실으면  -> 남은 drop = 남은 pick + 용량 > 0  (내릴 곳이 있다)
        #   · 적재가 0이라 못 내리면  -> 남은 drop = 남은 pick (있으면 실을 수 있고,
        #                                없으면 위 while 조건에서 이미 끝났다)
        # 실데이터 15개 실행·회차 1,224행에 `return` 행이 0건인 이유다.
        #
        # 살아 있는 호출부는 수급이 안 맞는 노드 집합을 주는
        # experiments/baseline/baseline_compare.py의 그리디 대조군(B1)뿐이다. 지우지 마라.
        if not candidates:
            if current_id == DEPOT_ID and current_load == 0:
                # depot에서 빈 차로도 후보가 없으면 더 진행 불가 (무한루프 방지)
                print(f"[경고] cluster {cluster}: 처리 불가 작업 잔여 "
                      f"(pick {remaining_pick}, drop {remaining_drop}) — 종료")
                break

            row, cum_sec = _depot_return(cluster, current_id, current_lat,
                                         current_lon, cum_sec)
            results.append(row)
            current_id = DEPOT_ID
            current_lat = DEPOT_LAT
            current_lon = DEPOT_LON
            current_load = 0
            continue

        # 최적 후보 선택 및 수행
        candidates.sort(key=lambda x: x[2])
        action, sid, _, distance, qty = candidates[0]
        node = nodes[(sid, action)]

        travel = _travel_sec(distance)
        per_bike = PICK_TIME_SEC if action == 'pick' else DROP_TIME_SEC
        work = qty * per_bike

        # 시간 예산이 주어졌으면, 이 작업을 하고 depot까지 돌아올 수 있을 때만 간다.
        if time_budget_sec is not None:
            back = _travel_sec(haversine_km(node['lat'], node['lon'], DEPOT_LAT, DEPOT_LON))
            if cum_sec + travel + work + back > time_budget_sec:
                break

        cum_sec += travel + work

        results.append({
            'cluster': cluster,
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

    # 마지막 대여소 -> depot 복귀 (1.19.1).
    #
    # 그 전에는 작업이 끝나면 **그 자리에서 멈췄다.** 위의 '작업 불가' 분기가 복귀
    # 행을 만들긴 하지만 ILP를 거친 입력에서는 실행될 수 없어(총 pick = 총 drop),
    # 실데이터 1,224행에 return이 0건이었다. 그래서 총 이동거리가 33% 과소 추정이고
    # 시간 예산 판정도 그만큼 낙관적이었다(docs/기록/TODO.md 1-1의 실측).
    # 설계는 처음부터 '차량 1대 = 클러스터 1개 + depot 복귀'였다(docs/구현/FLEET.md).
    #
    # 이미 depot에 있으면(작업이 없었거나 방금 되돌아왔으면) 붙이지 않는다.
    if current_id != DEPOT_ID:
        row, cum_sec = _depot_return(cluster, current_id, current_lat,
                                     current_lon, cum_sec)
        results.append(row)

    return results


def run_vrp_plan(ilp_plan: pd.DataFrame, duration: str):
    '''
    ILP에서 계산한 자전거 이동 계획을 기반으로 실제 차량이 어떤 순서로 대여소를 방문해야 하는지를 계산.
    클러스터별 작업 분리 -> pick/drop 노드 정리 -> 현재 위치 기준 최적 후보 선택 ->
    pick/drop 수행 -> 남은 작업이 없을 때까지 반복 -> VRP 결과 저장
    '''
    # 시간 예산을 제약으로 걸지 여부(기본 꺼짐). 근거는 project_config.
    budget_sec = TIME_BUDGET_MINUTES * 60 if ENFORCE_TIME_BUDGET else None
    if budget_sec:
        print(f"  시간 예산 {TIME_BUDGET_MINUTES:.0f}분을 **제약으로** 적용합니다"
              " — 넘기는 작업은 미집행으로 남습니다.")

    results = []
    clusters = ilp_plan['cluster'].unique()

    # 좌표 불러오기 (클러스터 루프 밖에서 1회)
    station_info = pd.read_csv(metrics_file.format(duration=duration, now=now), encoding='utf-8')
    require_columns(station_info, ['station_id', 'lat', 'lon'], f'step1 후보 {duration}')
    require_columns(ilp_plan, ['cluster', 'pick_station_id', 'drop_station_id', 'qty'],
                    f'step2 ILP 계획 {duration}')

    # 대여소가 중복되면 .loc[sid, 'lat']이 값이 아니라 Series를 돌려주고,
    # 그 Series가 노드 좌표로 들어가 거리 계산이 조용히 망가진다. 먼저 막는다.
    duplicated = station_info['station_id'].duplicated()
    if duplicated.any():
        raise SystemExit(
            f"후보 파일에 중복된 대여소가 있습니다: "
            f"{station_info.loc[duplicated, 'station_id'].tolist()[:5]} … "
            f"({int(duplicated.sum())}건). step1 산출물을 확인하세요.")
    station_info = station_info.set_index('station_id')

    missing = ({*ilp_plan['pick_station_id'], *ilp_plan['drop_station_id']}
               - set(station_info.index))
    if missing:
        raise SystemExit(
            f"ILP 계획의 대여소가 후보 파일에 없습니다: {sorted(missing)[:5]} … "
            f"({len(missing)}곳). 두 산출물의 실행 라벨이 같은지 확인하세요.")

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

        results.extend(greedy_route(nodes, c, time_budget_sec=budget_sec))

    # -------------------------
    # 차량 배정 (로테이션) — docs/구현/FLEET.md
    # -------------------------
    vrp_result = pd.DataFrame(results)
    vrp_result = _assign_fleet(vrp_result, duration)

    # -------------------------
    # 저장
    # -------------------------
    vrp_result.to_csv(vrp_plan_file.format(duration=duration, now=now), index=False)
    print(f"\nvrp_plan_path 파일이 저장되었습니다. ({vrp_plan_file.format(duration=duration, now=now)})")

    # CSV·DB 이중 기록 (DB_PLAN 2단계). 방문 순서는 db가 seq 컬럼으로 보존한다.
    db.save_output("vrp_plan", vrp_result, run_label=now, duration=duration)


def cluster_workload(vrp_result: pd.DataFrame) -> pd.DataFrame:
    """클러스터별 작업량(방문 대여소 수·옮긴 자전거 수·이동거리·소요시간).

    `bikes`는 **실제로 옮긴 자전거 수**다. 한 대는 한 번 실리고 한 번 내려지므로
    pick과 drop의 qty를 모두 더하면 2배가 된다(ILP 계획 대수와 어긋남).
    그래서 pick만 센다. 반면 작업시간은 싣기·내리기가 각각 드는 게 맞으므로
    `work_sec`는 두 동작을 모두 반영한다.
    """
    if vrp_result.empty:
        return pd.DataFrame(columns=["cluster", "stations", "bikes", "distance_km", "minutes"])

    work = vrp_result[vrp_result["action"] != "return"]
    summary = work.groupby("cluster").agg(
        stations=("to_id", "nunique"),
    ).reset_index()

    moved = (vrp_result[vrp_result["action"] == "pick"]
             .groupby("cluster")["qty"].sum()
             .rename("bikes").reset_index())
    summary = summary.merge(moved, on="cluster", how="left")
    summary["bikes"] = summary["bikes"].fillna(0).astype(int)

    # 이동거리·소요시간은 depot 복귀 구간까지 포함해야 실제 운행량이 된다.
    totals = vrp_result.groupby("cluster").agg(
        distance_km=("distance_km", "sum"),
        seconds=("cum_sec", "max"),
    ).reset_index()

    summary = summary.merge(totals, on="cluster", how="left")
    summary["distance_km"] = summary["distance_km"].round(2)
    summary["minutes"] = (summary.pop("seconds") / 60).round(1)
    return summary


def _assign_fleet(vrp_result: pd.DataFrame, duration: str) -> pd.DataFrame:
    """클러스터에 실제 차량을 배정하고 결과에 vehicle_id를 붙인다.

    누적 작업이 적은 차량부터 뽑으므로, 직전 회차에 나간 차량은 다음 회차에서
    뒤로 밀린다(로테이션). 배정 근거와 회차별 작업량은 DB에 남는다.
    """
    workload = cluster_workload(vrp_result)
    if workload.empty:
        vrp_result["vehicle_id"] = pd.Series(dtype="object")
        return vrp_result

    loads = dict(zip(workload["cluster"], workload["minutes"]))

    try:
        with db.session() as conn:
            mapping = db.assign_vehicles(conn, loads, run_label=now, duration=duration)
            rows = [{
                "vehicle_id": mapping[int(row.cluster)],
                "cluster": int(row.cluster),
                "stations": int(row.stations),
                "bikes": int(row.bikes),
                "distance_km": float(row.distance_km),
                "minutes": float(row.minutes),
            } for row in workload.itertuples()]
            db.save_assignments(conn, run_label=now, duration=duration, rows=rows)
    except Exception as err:
        # 배정은 부가 기능이다 — 실패해도 경로 계획 자체는 살린다.
        print(f"[경고] 차량 배정 실패: {type(err).__name__}: {err}")
        vrp_result["vehicle_id"] = pd.Series(dtype="object")
        return vrp_result

    vrp_result["vehicle_id"] = vrp_result["cluster"].map(mapping)

    print(f"\n차량 배정 ({duration}, {len(mapping)}대):")
    over = 0
    for row in workload.sort_values("minutes", ascending=False).itertuples():
        exceeded = row.minutes > TIME_BUDGET_MINUTES
        over += exceeded
        print(f"  {mapping[int(row.cluster)]}  클러스터 {int(row.cluster):<3d}"
              f" 대여소 {int(row.stations):>3d}곳  {int(row.bikes):>3d}대"
              f"  {row.distance_km:>6.2f}km  {row.minutes:>6.1f}분"
              + ("  ⚠ 시간 예산 초과" if exceeded else ""))

    if over:
        print(f"\n[경고] {len(workload)}개 중 {over}개가 시간 예산"
              f" {TIME_BUDGET_MINUTES:.0f}분을 넘었습니다.")
        print("  작업이 늦어지면 수요 예측 시간대가 이미 지나가 계획의 효과가 줄어듭니다.")
        print("  클러스터를 더 잘게 나누거나(VEHICLES_PER_ROUND 확대) 대상 대여소를 줄이세요.")
    return vrp_result


if __name__ == '__main__':
    ensure_output_dirs()

    for duration in duration_list(config):
        plan_path = Path(ilp_plan_file.format(duration=duration, now=now))
        if not plan_path.is_file():
            # 앞 단계가 '대상 없음'으로 건너뛴 시간대.
            print(f"\n[건너뜀] {duration}: ILP 계획이 없습니다 ({plan_path.name})")
            continue

        ilp_plan = pd.read_csv(plan_path, encoding='utf-8', low_memory=False)
        if ilp_plan.empty:
            print(f"\n[건너뜀] {duration}: ILP 계획이 비어 있습니다(이동할 자전거 없음)")
            continue

        run_vrp_plan(ilp_plan, duration)
        print("VRP 완료")
