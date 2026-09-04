import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import folium
import numpy as np
import pandas as pd

import db
from mapviz import (DROP_COLOR, DROP_LABEL, DROP_WORD, PICK_COLOR, PICK_LABEL,
                    PICK_WORD, legend_html, swatch_circle, swatch_circle_dashed,
                    swatch_size_scale)
from project_config import (
    MAP_TILES, PICK_HARM_WARN_SHARE, PROJECT_ROOT, TIME_BUDGET_MINUTES,
    VEHICLE_CAPACITY, duration_hours, duration_list, ensure_output_dirs,
    get_runtime_config,
    require_columns, select_day_type,
)

file_path = str(PROJECT_ROOT / "data/pp_data/ILP/후보/top{duration} ({now}).csv")
vrp_plan_file = str(PROJECT_ROOT / "data/pp_data/VRP/VRP_plan{duration} ({now}).csv")
net_demand_file = str(PROJECT_ROOT / "data/pp_data/순수요/st_net_daily ({period}).csv")
st_info_file = str(PROJECT_ROOT / "data/pp_data/대여소 정보/st_info ({now}).csv")

result_file_path = str(PROJECT_ROOT / "data/pp_data/성능 지표/verification{duration} ({now}).csv")
route_summary_file = str(PROJECT_ROOT / "data/pp_data/성능 지표/route_summary{duration} ({now}).csv")
map_file_path = str(PROJECT_ROOT / "data/pp_data/성능 지표/visualization/imbalance_map{duration} ({now}).html")

config = get_runtime_config()
now = config.now

def demand_satisfaction(reloc: pd.DataFrame):
    '''
    '수요 충족률' 계산 : 평균 순수요(mu) 대비 얼마나 수요를 충족할 수 있었는지
    imbalance를 통해 재배치 '전'보다 '후'가 얼마나 목표 상태(target_qty)에 가까워졌는가를 측정 
    imbalance : 부족과 과잉을 나누지 않고 하나의 수치로 통합함 (절대값 활용)
    '''

    temp = reloc.iloc[:, [0,1,2,3,10,5,8,9,6,7]].copy()
    print(temp.head())

    # 재배치 후 재고 = 기존 재고 + 재배치 수량
    temp['new_stock'] = temp['stock'] + temp['rebal_qty']

    # 불균형(imbalance) : 현재 재고(stock/new_stock)가 이상적인 목표 재고(target_qty)에서 얼마나 벗어나 있는가 (수량)
    temp['bf_imbalance'] = abs(temp['stock'] - temp['target_qty'])
    temp['af_imbalance'] = abs(temp['new_stock'] - temp['target_qty'])

    # 개선량 (imbalance는 절대값이라 bf가 더 큰 값일 테니 af를 뺌) (rebal_qty의 절대값과 같음)
    temp['improvement'] = temp['bf_imbalance'] - temp['af_imbalance']

    # 개선률
    temp['improvement_rate'] = (temp['improvement'] / temp['bf_imbalance']).round(2)
    avg_imp_rate = temp['improvement_rate'].mean(axis=0).round(2)
    pick_avg_imp_rate = temp.loc[temp['rebal_qty'] < 0, 'improvement_rate'].mean(axis=0).round(2)
    drop_avg_imp_rate = temp.loc[temp['rebal_qty'] > 0, 'improvement_rate'].mean(axis=0).round(2)


    print("-"*50)
    print(f"평균 개선률(improvement_rate) : {(100 * avg_imp_rate).round(2)}%")
    print(f"   - Pick 대상 대여소 평균 개선률: {(100 * pick_avg_imp_rate).round(2)}%")
    print(f"   - Drop 대상 대여소 평균 개선률: {(100 * drop_avg_imp_rate).round(2)}%")
    print("-"*50)

    return temp


def route_summary(duration: str):
    '''
    VRP 결과(거리·시간 컬럼 포함)로 클러스터별 총 이동거리·운행시간을 집계한다.
    VRP 파일이 없거나 구버전(시간 컬럼 없음)이면 건너뛴다.
    '''
    path = Path(vrp_plan_file.format(duration=duration, now=now))
    if not path.exists():
        print(f"VRP 결과가 없어 경로 요약을 건너뜁니다: {path}")
        return

    vrp = pd.read_csv(path, encoding='utf-8')
    if 'distance_km' not in vrp.columns:
        print("VRP 결과에 거리·시간 컬럼이 없습니다(구버전). step2 vrp.py를 다시 실행하세요.")
        return

    # 방문수는 depot 복귀를 빼고 실제로 들른 대여소 수,
    # 처리대수는 pick 기준(pick+drop을 더하면 한 대를 두 번 세어 2배가 된다).
    # step2의 vehicle_assignment와 같은 기준이라 두 산출물의 숫자가 맞는다.
    visited = vrp[vrp['action'] != 'return']
    summary = visited.groupby('cluster').agg(방문수=('to_id', 'nunique')).reset_index()

    moved = (vrp[vrp['action'] == 'pick'].groupby('cluster')['qty']
             .sum().rename('처리대수').reset_index())

    totals = vrp.groupby('cluster').agg(
        총이동거리_km=('distance_km', 'sum'),
        총이동시간_분=('travel_sec', 'sum'),
        총작업시간_분=('work_sec', 'sum'),
        총소요시간_분=('cum_sec', 'max'),
    ).reset_index()

    summary = summary.merge(moved, on='cluster', how='left').merge(totals, on='cluster', how='left')
    summary['처리대수'] = summary['처리대수'].fillna(0).astype(int)
    summary = summary[['cluster', '방문수', '처리대수', '총이동거리_km',
                       '총이동시간_분', '총작업시간_분', '총소요시간_분']]

    for col in ['총이동시간_분', '총작업시간_분', '총소요시간_분']:
        summary[col] = (summary[col] / 60).round(1)
    summary['총이동거리_km'] = summary['총이동거리_km'].round(2)

    print("-" * 50)
    print("클러스터별 경로 요약:")
    print(summary.to_string(index=False))

    within = (summary['총소요시간_분'] <= TIME_BUDGET_MINUTES).sum()
    rate = within / len(summary) * 100 if len(summary) else 0
    print(f"전체: {summary['총이동거리_km'].sum():.2f} km, "
          f"최장 소요 {summary['총소요시간_분'].max():.1f} 분")
    print(f"시간 예산({TIME_BUDGET_MINUTES:.0f}분) 준수: "
          f"{within}/{len(summary)} 클러스터 ({rate:.0f}%)")
    if within < len(summary):
        print("  ⚠ 초과한 클러스터는 수요 예측 시간대가 지나간 뒤 작업이 끝납니다.")
    print("-" * 50)

    summary.to_csv(route_summary_file.format(duration=duration, now=now), index=False, encoding='utf-8')
    print(f"route_summary 파일이 저장되었습니다. ({route_summary_file.format(duration=duration, now=now)})")

    # CSV·DB 이중 기록 (DB_PLAN 2단계). 한글 컬럼은 db가 ASCII로 변환한다.
    db.save_output("route_summary", summary, run_label=now, duration=duration)
    return summary


def load_net_demand() -> pd.DataFrame:
    '''시간대별 순수요를 읽는다(DB 우선, 없으면 CSV).

    **계획과 같은 요일 구분만 남긴다.** 평일 계획은 평일 순수요로, 휴일 계획은 휴일
    순수요로 평가해야 한다 — 앞 단계(calculate_target_qty)가 이미 한쪽만 골라
    목표 재고를 잡았기 때문이다. 섞으면 평일 계획을 주말 수요로 채점하게 되고,
    대여소의 33~37%가 두 구분에서 부호가 반대라 결과가 실제와 달라진다
    (experiments/structure/weekend_profile.py, docs/구현/steps/step0_raw.md).
    '''
    frame = pd.DataFrame()
    try:
        with db.session() as conn:
            loaded = db.load_frame(conn, 'net_demand', period=config.period)
        if not loaded.empty:
            frame = loaded.rename(columns={'date': '날짜'})
    except Exception as err:
        print(f"[경고] 순수요 DB 조회 실패: {type(err).__name__}: {err}")

    if frame.empty:
        path = Path(net_demand_file.format(period=config.period))
        if path.is_file():
            frame = pd.read_csv(path, encoding='utf-8')

    if frame.empty:
        return frame

    전체일수 = pd.to_datetime(frame['날짜']).dt.date.nunique()
    frame = select_day_type(frame, '날짜', config.day_type)
    사용일수 = pd.to_datetime(frame['날짜']).dt.date.nunique() if not frame.empty else 0
    print(f"결품 시뮬레이션: {config.day_label} 순수요만 사용 "
          f"({사용일수}일 / 전체 {전체일수}일)")
    return frame


def _simulate_stock(net: pd.DataFrame, initial: pd.Series,
                    capacity: pd.Series, hours: list) -> dict:
    '''재고 궤적을 **한 번 돌며** 네 가지를 함께 센다 (1.26.101).

        stock(t+1) = clip(stock(t) - net(t), 0, 거치대 수)

    순수요(net)는 대여 - 반납이므로 양수면 재고가 준다.

    🔴 **양쪽 clip이 버리던 값을 여기서 거둔다.** 지금까지는 `clip(0, 거치대)`로
    잘라내고 **얼마나 잘렸는지는 버렸다.** 그런데 잘려 나간 양이 곧
    *"빌리려다 못 빌린 수"*(아래 clip)와 *"반납하려다 못 한 수"*(위 clip)다 —
    [KPI.md](../docs/분석/KPI.md) 3-B가 미구현으로 남겨 둔 **수요 충족률·포화
    시간**이 바로 이 두 값이다. 궤적을 두 번 돌 필요 없이 같은 루프에서 나온다.

    반환 키:
      `stockout`  재고 0인 시간 수      (기존 지표)
      `saturated` 재고 = 거치대인 시간 수 → **반납 실패**
      `unmet`     아래로 잘린 양 합계    → 못 빌린 자전거 수
      `refused`   위로 잘린 양 합계      → 못 세운 자전거 수
      `outflow`   순유출(양수 net) 합계  → 충족률의 분모

    ⚠️ **`unmet`/`outflow`는 순수요 기준이라 '총 대여 건수'가 아니다.** `net_demand`가
    대여 − 반납이므로, 같은 시간에 오간 것은 이미 상쇄돼 있다. 따라서 충족률은
    **총 대여 대비가 아니라 순유출 대비**이며, 결품 시간과 같은 성격의
    **하한 지표**다(못 빌린 수요는 관측되지 않고 사라진다).
    '''
    stock = initial.astype(float).copy()
    zeros_i = pd.Series(0, index=net.index, dtype=int)
    zeros_f = pd.Series(0.0, index=net.index)
    out = {'stockout': zeros_i.copy(), 'saturated': zeros_i.copy(),
           'unmet': zeros_f.copy(), 'refused': zeros_f.copy(),
           'outflow': zeros_f.copy()}

    for hour in hours:
        column = f'net_{hour:02d}'
        if column not in net.columns:
            continue
        flow = net[column]
        raw = stock - flow                      # 자르기 **전**의 재고

        # 아래로 잘린 만큼이 못 빌린 수, 위로 잘린 만큼이 못 세운 수다.
        out['unmet'] += (-raw).clip(lower=0)
        out['refused'] += (raw - capacity).clip(lower=0)
        out['outflow'] += flow.clip(lower=0)

        stock = raw.clip(lower=0).clip(upper=capacity)
        out['stockout'] += (stock <= 0).astype(int)
        out['saturated'] += (stock >= capacity).astype(int)
    return out


def _stockout_hours(net: pd.DataFrame, initial: pd.Series,
                    capacity: pd.Series, hours: list) -> pd.Series:
    '''대여소별 결품 시간(행 단위 합). `_simulate_stock()`의 얇은 껍데기다.

    0에서 잘라내는 것은 물리적 현실이다 — 없는 자전거는 빌릴 수 없고,
    그 못 빌린 수요는 사라진다(그래서 이 값은 결품의 하한이다).

    ⚠️ **이 함수를 지우지 마라.** 실험 스크립트 넷과 테스트 넷이 이 이름으로
    부른다(baseline_compare · budget_enforce · min_qty_sweep · top_limit_sweep).
    측정 코드와 운영 코드가 같은 궤적을 쓰게 하는 것이 이 저장소의 규약이다.
    '''
    return _simulate_stock(net, initial, capacity, hours)['stockout']


def executed_delta(vrp: pd.DataFrame) -> pd.Series:
    """VRP가 **실제로 싣고 내린 양**을 대여소별 재고 증감으로 바꾼다.

    계획량(`rebal_qty`)과 다르다. ILP는 군집 안에서 `min(총 pick, 총 drop)`만큼만
    옮기므로 계획이 전부 집행되지는 않는다. 계획량으로 결품을 재면 **군집·ILP를
    건너뛴 방법과 점수가 같아져 비교 자체가 불가능**해진다
    (docs/분석/EXPERIMENTS.md 5장에서 실측으로 확인).

    실험 스크립트(experiments/baseline/baseline_compare.py)도 이 함수를 그대로 쓴다.
    """
    if vrp.empty:
        return pd.Series(dtype=float)
    work = vrp[vrp['action'] != 'return']
    if work.empty:
        return pd.Series(dtype=float)
    sign = np.where(work['action'] == 'drop', 1, -1)
    return (pd.Series(work['qty'].to_numpy() * sign, index=work['to_id'].to_numpy())
            .groupby(level=0).sum())


def load_vrp_plan(duration: str) -> pd.DataFrame:
    """VRP 경로 계획을 읽는다(없으면 빈 프레임)."""
    path = Path(vrp_plan_file.format(duration=duration, now=now))
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, encoding='utf-8')


def pick_harm_share(pick_delta: float, drop_delta: float) -> float:
    """Pick 쪽 결품 증가가 Drop 쪽 이득의 몇 배인가.

    **Pick 대여소가 조금 나빠지는 것은 설계상 정상이다** — 재고를 빼내는 곳이고,
    `mu`가 크게 음수인 대여소는 `target_qty`가 0으로 잘려 거의 다 실어 간다.
    그래서 '양수면 경고'는 늘 뜨는 거짓 경보였다(1.19.6에서 고쳤다).

    Drop 쪽 이득이 없으면(0) Pick 손실만 있다는 뜻이라 1을 돌려준다 — 그때는
    몫을 따질 것 없이 알려야 한다.
    """
    if drop_delta:
        return pick_delta / abs(drop_delta)
    return 1.0 if pick_delta > 0 else 0.0


def stockout_simulation(duration: str, imbalance_df: pd.DataFrame) -> dict:
    '''재배치 전후의 결품 시간을 비교한다. (docs/분석/KPI.md 3-B, 4단계)

    지금까지의 지표는 전부 "계획이 목표 재고를 얼마나 채웠나"(계획 달성률)였다.
    이 지표는 **이용자가 실제로 자전거를 탈 수 있었는지**에 한 걸음 다가간다.
    다만 실측이 아니라 순수요로 복원한 시뮬레이션이며, 재배치를 실제 집행한 뒤
    관측한 값이 아니라는 점은 분명히 해 둔다.

    비교 대상은 작업 대상 대여소뿐이다(나머지는 전후가 같다).
    Drop은 재고가 늘어 결품이 줄지만 Pick은 재고가 줄어 늘 수 있으므로,
    합산 결과가 이 재배치가 이용자에게 이로웠는지를 말해 준다.

    **재배치 후 재고는 VRP가 실제로 옮긴 양으로 잡는다**(1.18.4). 계획량으로 잡으면
    편익을 과대평가하고(실측 약 13%), 무엇보다 **방법 간 비교가 불가능**해진다 —
    계획이 같고 집행만 다른 방법들의 점수가 전부 같아지기 때문이다.
    계획 기준 값은 `stockout_hours_plan`으로 함께 남겨 격차를 볼 수 있게 한다.
    '''
    net = load_net_demand()
    if net.empty:
        print("순수요 데이터가 없어 결품 시뮬레이션을 건너뜁니다.")
        return {}

    hours = duration_hours(duration)
    stations = imbalance_df[['station_id', 'stock', 'rebal_qty']].copy()

    # 거치대 수는 후보 파일에 있다(imbalance_df에는 없을 수 있음)
    candidates = pd.read_csv(file_path.format(duration=duration, now=now), encoding='utf-8')
    stations = stations.merge(candidates[['station_id', 'parking_lot']],
                              on='station_id', how='left')
    stations['parking_lot'] = stations['parking_lot'].fillna(stations['stock'] * 2 + 1)

    # 실제로 옮긴 양. VRP 결과가 없으면(구버전 산출물) 계획량으로 물러선다.
    moved = executed_delta(load_vrp_plan(duration))
    if moved.empty:
        print("[안내] VRP 결과가 없어 계획량(rebal_qty)으로 결품을 계산합니다"
              " — 이 값은 편익을 과대평가합니다.")
        stations['moved'] = stations['rebal_qty']
    else:
        stations['moved'] = stations['station_id'].map(moved).fillna(0).astype(int)

    merged = net.merge(stations, on='station_id', how='inner')
    if merged.empty:
        print("순수요와 작업 대상 대여소가 겹치지 않아 결품 시뮬레이션을 건너뜁니다.")
        return {}

    # 궤적을 한 번만 돌아 결품·포화·충족률을 함께 얻는다(1.26.101).
    sim_before = _simulate_stock(merged, merged['stock'],
                                 merged['parking_lot'], hours)
    sim_after = _simulate_stock(merged, merged['stock'] + merged['moved'],
                                merged['parking_lot'], hours)
    before, after = sim_before['stockout'], sim_after['stockout']
    planned = _stockout_hours(merged, merged['stock'] + merged['rebal_qty'],
                              merged['parking_lot'], hours)

    days = merged['날짜'].nunique() if '날짜' in merged.columns else 1
    count = merged['station_id'].nunique()
    denominator = max(count * days, 1)

    per_station = pd.DataFrame({
        'station_id': merged['station_id'],
        'rebal_qty': merged['rebal_qty'],
        'before': before, 'after': after,
    }).groupby(['station_id', 'rebal_qty'], as_index=False).sum()
    per_station['delta'] = per_station['after'] - per_station['before']

    result = {
        'stockout_hours_before': float(before.sum() / denominator),
        'stockout_hours_after': float(after.sum() / denominator),
        'stockout_hours_plan': float(planned.sum() / denominator),
        # 포화 시간 — 반납하려 해도 거치대가 없는 시간 (KPI.md 3-B, 1.26.101)
        'saturation_hours_before': float(sim_before['saturated'].sum() / denominator),
        'saturation_hours_after': float(sim_after['saturated'].sum() / denominator),
    }

    # 수요 충족률 — 순유출 대비 실제로 내준 비율. 분모가 0이면(그 회차에 빠져
    # 나가는 수요가 없으면) 비율이 정의되지 않으므로 아예 넘기지 않는다.
    for tag, sim in (('before', sim_before), ('after', sim_after)):
        outflow = float(sim['outflow'].sum())
        if outflow > 0:
            served = outflow - float(sim['unmet'].sum())
            result[f'demand_fulfill_{tag}'] = max(0.0, served / outflow)

    improved = int((per_station['delta'] < 0).sum())
    worsened = int((per_station['delta'] > 0).sum())
    pick_delta = int(per_station.loc[per_station['rebal_qty'] < 0, 'delta'].sum())
    drop_delta = int(per_station.loc[per_station['rebal_qty'] > 0, 'delta'].sum())

    change = result['stockout_hours_after'] - result['stockout_hours_before']
    print(f"\n결품 시뮬레이션 ({duration}, {count}곳 × {days}일, {len(hours)}시간 창):")
    print(f"  대여소·일 평균 결품 시간: {result['stockout_hours_before']:.2f}h"
          f" → {result['stockout_hours_after']:.2f}h ({change:+.2f}h)")
    print(f"  개선 {improved}곳 · 악화 {worsened}곳"
          f"  (Pick {pick_delta:+d}h / Drop {drop_delta:+d}h)")
    print(f"  계획이 100% 집행됐다면: {result['stockout_hours_plan']:.2f}h"
          f" — 계획과 집행의 격차 {result['stockout_hours_after'] - result['stockout_hours_plan']:+.2f}h")
    # Pick 쪽이 조금 나빠지는 것은 설계상 정상이다(재고를 빼내는 곳이므로).
    # Drop 쪽 이득에 견줘 **몫이 클 때만** 알린다 — 근거는 project_config.
    harm_share = pick_harm_share(pick_delta, drop_delta)
    if pick_delta > 0:
        print(f"  Pick 쪽 손실은 Drop 쪽 이득의 {harm_share * 100:.2f}%입니다"
              f" (문턱 {PICK_HARM_WARN_SHARE * 100:.0f}%).")
    if harm_share > PICK_HARM_WARN_SHARE:
        print(f"  ⚠ Pick 대여소의 결품 증가가 문턱을 넘었습니다 —"
              f" 회수량이 과한지 target_qty를 확인하세요.")
    return result


def route_extras(duration: str) -> dict:
    """경로에서만 알 수 있는 운영·효율 지표 (docs/분석/KPI.md C·D장).

    route_summary는 클러스터 단위 합계라 **구간별 적재량**을 모른다. 공차 이동
    비율은 각 이동을 시작할 때 차에 몇 대가 있었는지 되짚어야 나온다.

    - `travel_time_ratio` — 이동시간 / 총 소요시간. 낮을수록 좋다(이동보다 작업).
    - `empty_distance_ratio` — **빈 차로 달린 거리** 비율. 차고지에서 첫 대여소로
      가는 구간과 복귀 구간이 기본으로 여기 들어간다.
    - `depot_returns` — 복귀 행 수. 1.19.1부터 클러스터당 1건이 정상이다.
    - `bikes_per_minute` — 분당 처리 대수(작업 밀도).

    못 구하면 빈 dict를 돌려준다 — 그 컬럼은 NULL로 남는다(db.KPI_FIELDS).
    """
    path = Path(vrp_plan_file.format(duration=duration, now=now))
    if not path.exists():
        return {}

    vrp = pd.read_csv(path, encoding='utf-8')
    if not {'distance_km', 'travel_sec', 'work_sec', 'cum_sec'} <= set(vrp.columns):
        return {}

    total_distance = float(vrp['distance_km'].sum())
    # 총 소요시간은 클러스터마다 따로 흐르므로 max를 클러스터별로 더한다.
    total_seconds = float(vrp.groupby('cluster')['cum_sec'].max().sum())
    if total_seconds <= 0:
        return {}

    empty_km = 0.0
    for _, rows in vrp.groupby('cluster'):
        load = 0
        for _, row in rows.iterrows():
            # 이 구간은 **도착 전 적재량**으로 달린다. 0이면 빈 차다.
            if load == 0:
                empty_km += float(row['distance_km'])
            if row['action'] == 'pick':
                load += int(row['qty'])
            elif row['action'] == 'drop':
                load -= int(row['qty'])

    moved = int(vrp[vrp['action'] == 'pick']['qty'].sum())
    return {
        'travel_time_ratio': float(vrp['travel_sec'].sum() / total_seconds),
        'empty_distance_ratio': (float(empty_km / total_distance)
                                 if total_distance else None),
        'depot_returns': int((vrp['action'] == 'return').sum()),
        'bikes_per_minute': float(moved / (total_seconds / 60)),
    }


def station_coverage(worked: int) -> dict:
    """분석 대상 대여소 가운데 몇 곳을 손댔나 (docs/분석/KPI.md C장).

    전체 대여소 수는 이번 실행이 수집한 대여소 정보에서 센다. 파일이 없으면
    지표를 빼고 넘긴다 — 지어내지 않는다.
    """
    path = Path(st_info_file.format(now=now))
    if not path.exists():
        return {}
    total = len(pd.read_csv(path, encoding='utf-8'))
    if total <= 0:
        return {}
    return {'stations_total': int(total), 'station_coverage': float(worked / total)}


def save_kpi_summary(duration: str, imbalance_df: pd.DataFrame,
                     summary: pd.DataFrame) -> None:
    '''
    흩어져 있는 지표를 실행 1건 = 1행으로 모아 kpi_summary에 기록한다. (docs/분석/KPI.md)

    지금까지는 개선률은 콘솔에, 이동거리·시간은 route_summary CSV에, 차량 배정은
    또 다른 테이블에 있어 실행 간 비교를 매번 손으로 맞춰야 했다.
    '''
    rate = imbalance_df['improvement_rate']
    minutes = summary['총소요시간_분']
    distance = summary['총이동거리_km'].sum()
    improvement = imbalance_df['improvement'].sum()

    metrics = {
        'stations': int(len(imbalance_df)),
        'clusters': int(len(summary)),
        'bikes_moved': int(summary['처리대수'].sum()),
        'avg_improvement_rate': float(rate.mean()),
        'pick_improvement_rate': float(rate[imbalance_df['rebal_qty'] < 0].mean()),
        'drop_improvement_rate': float(rate[imbalance_df['rebal_qty'] > 0].mean()),
        # 목표에 사실상 도달한 대여소 비율 (평균 개선률이 감추는 분포를 보완).
        # ⚠️ **실행 품질을 재는 값이 아니다.** 계획량이 `Q·tanh(격차/Q)`로 눌리므로
        # 격차가 8대만 넘어도 도달이 구조적으로 불가능하다(docs/분석/KPI.md).
        'target_met_ratio': float((imbalance_df['af_imbalance'] <= 1).mean()),
        # 한 번 방문으로 닿을 수 있는 범위였던 대여소 비율.
        # 트럭 적재 용량이 10대인데 격차는 최대 58대까지 벌어진다 — 얼마나 많은
        # 작업이 애초에 한 회차로 해결 불가능한지를 이 값이 말해 준다.
        'reachable_ratio': float((imbalance_df['bf_imbalance']
                                  <= VEHICLE_CAPACITY).mean()),
        'gap_median': float(imbalance_df['bf_imbalance'].median()),
        'gap_max': float(imbalance_df['bf_imbalance'].max()),
        'total_distance_km': float(distance),
        'max_cluster_minutes': float(minutes.max()),
        'avg_cluster_minutes': float(minutes.mean()),
        'time_budget_minutes': float(TIME_BUDGET_MINUTES),
        'time_budget_met': float((minutes <= TIME_BUDGET_MINUTES).mean()),
        'vehicle_load_gap': float(minutes.max() - minutes.min()),
        # 1km 이동으로 줄인 불균형 대수 — 효과와 비용을 한 지표로 묶는다
        'improvement_per_km': float(improvement / distance) if distance else None,
        'cluster_max_imbalance': int(imbalance_df.groupby('cluster')['rebal_qty']
                                     .sum().abs().max()),
    }

    # 결품 시뮬레이션 (KPI.md 4단계). 순수요가 없으면 빈 dict라 컬럼은 NULL로 남는다.
    metrics.update(stockout_simulation(duration, imbalance_df))
    # 운영·효율 지표 (KPI.md C·D장). 못 구하면 빈 dict.
    metrics.update(route_extras(duration))
    metrics.update(station_coverage(len(imbalance_df)))

    try:
        with db.session() as conn:
            assigned = db.assignment_history(conn, run_label=now)
            metrics['vehicles_used'] = int(
                assigned[assigned['duration'] == duration]['vehicle_id'].nunique())
            db.save_kpi(conn, run_label=now, duration=duration, metrics=metrics)
    except Exception as err:
        print(f"[경고] KPI 기록 실패: {type(err).__name__}: {err}")
        return

    print(f"\nKPI 요약 ({duration}):")
    print(f"  개선률 {metrics['avg_improvement_rate'] * 100:.0f}%"
          f" · 목표도달 {metrics['target_met_ratio'] * 100:.0f}%"
          f" · km당 개선 {metrics['improvement_per_km']:.2f}대")
    print(f"  격차 중앙값 {metrics['gap_median']:.0f}대 · 최대 {metrics['gap_max']:.0f}대"
          f" · 한 번에 닿는 범위 {metrics['reachable_ratio'] * 100:.0f}%"
          f" (적재 {VEHICLE_CAPACITY}대)")
    print(f"  이동 {distance:.0f}km · 최장 {minutes.max():.0f}분"
          f" · 예산준수 {metrics['time_budget_met'] * 100:.0f}%"
          f" · 차량 {metrics.get('vehicles_used', 0)}대")
    if 'stockout_hours_before' in metrics:
        print(f"  결품 {metrics['stockout_hours_before']:.2f}h"
              f" → {metrics['stockout_hours_after']:.2f}h (대여소·일 평균)")
    if 'travel_time_ratio' in metrics:
        print(f"  이동 비중 {metrics['travel_time_ratio'] * 100:.0f}%"
              f" · 공차 이동 {metrics['empty_distance_ratio'] * 100:.0f}%"
              f" · 분당 {metrics['bikes_per_minute']:.2f}대"
              f" · 복귀 {metrics['depot_returns']}건")


def demand_satisfaction_map(reloc_df: pd.DataFrame, imbalance_df: pd.DataFrame, duration: str):

    center_lat = reloc_df['lat'].mean()
    center_lon = reloc_df['lon'].mean()

    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=13,
        control_scale=True,
        tiles=MAP_TILES                 # 세 지도가 같은 배경을 써야 한다(project_config)
    )

    unique_clusters = sorted(imbalance_df['cluster'].unique())
    

    for idx, c in enumerate(unique_clusters):
        
        fg = folium.FeatureGroup(
            # 레이어 컨트롤은 지도 위에 겹쳐 뜬다 — 이름이 짧아야 한다.
            name=f"군집 {c}",
            show=True
        )
        fg.add_to(m)

        cluster_df = imbalance_df[imbalance_df['cluster']==c].copy()

        for _, row in cluster_df.iterrows():
            
            # 싣기/내리기 색은 mapviz.py 한 벌에서 온다 — 예전에는 여기
            # 'red'/'blue'를 직접 박아 두어, 웹 작업지시서와 **파랑이 서로
            # 반대 작업**을 뜻했다(1.26.107). 용어도 한글로 통일한다.
            if row['rebal_qty'] > 0:
                color = DROP_COLOR
                status = DROP_WORD
            else:
                color = PICK_COLOR
                status = PICK_WORD

            # 작업 후 불균형 '개선률(improvement_rate)' 기반 마커
            #
            # ⚠️ **반지름은 3에서 막힌다.** 그리지 않으면 개선량이 작은 점이
            #    사라져 ‘대여소가 어디 있는지’조차 안 보이기 때문이다. 문제는
            #    `improvement`가 **0이거나 음수**일 수 있다는 것이다
            #    (`bf_imbalance - af_imbalance`라 계획이 오히려 악화시킨 대여소가
            #    여기 온다). 그럴 땐 악화된 점과 3대 해소한 점이 **픽셀까지
            #    똑같아진다** — 범례가 그것을 "3대"라고 단언하면 거짓말이 된다.
            #    크기로는 구분할 수 없으니 **테두리로** 구분하고, 범례에도 적는다.
            worsened = row['improvement'] < 0
            radius = max(3, row['improvement'])
            # 개선률이 음수면 불투명도가 0 아래로 내려가 folium이 무시한다 —
            # 보이는 범위로 잡아 둔다.
            opacity = min(0.7, max(0.15, 0.2 + 0.5*row['improvement_rate']))

            # 커서를 대면 뜨는 요약. **목표 재고는 소수점 첫째 자리까지만** 쓴다
            # (수정안 38) — mu + z*sigma라 자릿수가 길게 나온다.
            tooltip = f"""
            <b>{row['station_name']}</b><br>
            군집 : {row['cluster']}<br>
            작업 유형 : {status}<br>
            재배치 수량 : {row['rebal_qty']}<br>
            작업 전 재고 : {row['stock']} → 작업 후 : {row['new_stock']}<br>
            목표 재고 : {row['target_qty']:.1f}
            """

            # 눌러서 **고정**되는 창. 커서를 떼도 남으므로 값을 따져 볼 때 쓴다
            # (수정안 38). 요약과 달리 불균형 지표까지 싣는다.
            popup_html = f"""
            <b>{row['station_name']}</b><br>
            군집 : {row['cluster']}<br>
            작업 유형 : {status}<br>
            재배치 수량 : {row['rebal_qty']}<br>
            작업 전 재고 : {row['stock']}<br>
            작업 후 재고 : {row['new_stock']}<br>
            목표 재고 : {row['target_qty']:.1f}<br>
            작업 전 불균형 : {row['bf_imbalance']:.2f}<br>
            작업 후 불균형 : {row['af_imbalance']:.2f}<br>
            개선량 : {row['improvement']:.2f}<br>
            개선률 : {row['improvement_rate']*100:.1f}%
            """

            folium.CircleMarker(
                location=[row['lat'], row['lon']],
                radius=radius,

                color=color,
                weight=2,
                opacity=1.0,
                # 악화된 대여소는 하한(3)에 걸려 크기로는 구분이 안 되므로
                # 점선 테두리로 표시한다. 색은 그대로 둔다 — 색은 이미
                # 싣기/내리기를 뜻하고 있어서 뜻을 겹쳐 실을 수 없다.
                dash_array='4,3' if worsened else None,

                fill=True,
                fill_color=color,
                fill_opacity=opacity,

                # sticky: 풍선이 커서를 따라온다. 점이 촘촘한 곳에서
                # 어느 점의 설명인지 헷갈리지 않는다 (세 지도가 같게).
                tooltip=folium.Tooltip(tooltip, sticky=True),
                popup=folium.Popup(popup_html, max_width=260)
            ).add_to(fg)

    # 범례는 세 지도가 mapviz.py 한 벌을 같이 쓴다. 예전에는 여기만 영어
    # ("Legend")에 회색 2px 테두리라, 같은 실행의 산출물인데 다른 도구처럼
    # 보였다(1.26.75 조사 → 1.26.80 채택).
    m.get_root().html.add_child(folium.Element(legend_html(
        "범례 — 재고 현황",
        [(swatch_circle(DROP_COLOR), DROP_LABEL),
         (swatch_circle(PICK_COLOR), PICK_LABEL),
         # 크기로 값을 말했으면 **눈금도 줘야** 읽을 수 있다. 마커 반지름이
         # 곧 해소 대수(max(3, improvement))라 눈금도 같은 수를 쓴다.
         #
         # ⚠️ 가장 작은 눈금은 "3대"가 아니라 **"3대 이하"** 다. 반지름이 3에서
         #    막히므로 1대짜리도, 0도, 계획이 오히려 악화시킨 곳(음수)도 모두
         #    같은 크기로 그려진다. 눈금이 "3대"라고 단언하면 그 점들을 전부
         #    3대라고 잘못 읽게 된다 — 크기가 말할 수 있는 것까지만 말한다.
         (swatch_size_scale([3, 7, 12], ["≤3대", "7대", "12대"]), ""),
         (swatch_circle_dashed(), "점선 = 오히려 나빠진 곳")],
        note="원 크기는 불균형 해소량, 원이 진할수록 개선률이 높습니다.<br>"
             "가장 작은 원은 <b>3대 이하가 모두 같은 크기</b>입니다 — 정확한 값은 "
             "점을 눌러 '개선량'에서 보세요.<br>"
             "점에 커서를 대면 자세한 값이 뜹니다.")))

    # ⚠️ 레이어 컨트롤은 지도 **위에** 겹쳐 뜬다. 펴 두면 군집 수만큼
    # 줄이 서서 지도 오른쪽을 위에서 아래까지 덮는다 — 군집 19개짜리
    # 산출물에서 38줄, 780px였다(실측 1.26.107). 접어 둔다: 색이 무슨
    # 뜻인지는 이제 **범례**가 말하고, 컨트롤은 걸러 보는 도구다.
    folium.LayerControl(collapsed=True).add_to(m)


    m.save(map_file_path.format(duration=duration, now=now))

    print("지도 생성 완료")
    print("map_file_path  파일이 저장되었습니다.", (map_file_path.format(duration=duration, now=now)))




if __name__ == "__main__":
    ensure_output_dirs()

    for duration in duration_list(config):
        candidates = Path(file_path.format(duration=duration, now=now))
        if not candidates.is_file():
            # step1이 '대상 없음'으로 건너뛴 시간대.
            print(f"\n[건너뜀] {duration}: 후보 파일이 없습니다 ({candidates.name})")
            continue

        reloc_df = pd.read_csv(candidates, encoding='utf-8')
        require_columns(reloc_df, ['station_id', 'stock', 'target_qty', 'rebal_qty',
                                   'cluster', 'parking_lot'], f'step1 후보 {duration}')
        print(reloc_df.head())

        imbalance_df = demand_satisfaction(reloc_df).copy()

        demand_satisfaction_map(reloc_df, imbalance_df, duration)

        imbalance_df.to_csv(result_file_path.format(duration=duration, now=now), index=False, encoding='utf-8')
        print(f"\nresult_file_path 파일이 저장되었습니다. ({result_file_path.format(duration=duration, now=now)})")

        # CSV·DB 이중 기록 (DB_PLAN 2단계). CSV가 아직 정본이다.
        db.save_output("metrics", imbalance_df, run_label=now,
                       period=config.period, duration=duration)

        summary = route_summary(duration)
        if summary is not None:
            save_kpi_summary(duration, imbalance_df, summary)