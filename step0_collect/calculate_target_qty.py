"""시간대(duration)별 목표 재고(target_qty)와 재배치량(rebal_qty)을 계산한다.

**평일과 휴일 중 한쪽만 골라 계산한다**(`--day-type`, 기본 auto = 오늘로 판정).
휴일 = 주말 ∪ 공휴일이다. 두 구분은 수요 구조가 달라 섞으면 안 된다 — 실측에서
대여소의 33~37%가 부호가 반대였다(experiments/weekend_profile.py). 섞어서 평균 내면
서로 상쇄돼 작업 대상에서 빠진다.

입력: st_info ({now}).csv, st_net_daily ({period}).csv
출력: data/pp_data/재배치 정보/rebal_qty{duration} ({now}).csv
      (rebal_qty 양수 = Drop 필요, 음수 = Pick 가능)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

import db
import demand_model
from project_config import (
    PROJECT_ROOT,
    TARGET_QTY_UPPER_RATIO, TARGET_Z, VEHICLE_CAPACITY,
    duration_hours,
    duration_list,
    ensure_output_dirs,
    get_runtime_config,
    select_day_type,
    target_stamp,
)

# read_csv
st_info_file = str(PROJECT_ROOT / "data/pp_data/대여소 정보/st_info ({now}).csv")
net_file = str(PROJECT_ROOT / "data/pp_data/순수요/st_net_daily ({period}).csv")

# to_csv
out_file_path = str(PROJECT_ROOT / "data/pp_data/재배치 정보/rebal_qty{duration} ({now})")  # .csv

# 한 대여소에서 한 번에 옮길 수 있는 최대 대수 = 차량 적재 용량.
# **project_config에서 읽는다** — 1.19.7 이전에는 여기 10이 박혀 있어,
# 차량 용량을 바꿔도 계획량 제한은 그대로였다.
MAX_CAPACITY = VEHICLE_CAPACITY


def duration_columns(duration: str) -> list:
    """시간대 문자열(_05_10) -> 순수요 컬럼 목록. 자정을 넘기면 이어서 돈다."""
    hours = duration_hours(duration)
    return [f"net_{h:02d}" for h in hours]


def build_stats(net_daily: pd.DataFrame, st_initial_qty: pd.DataFrame, duration: str,
                warmup_net=None, warmup_days: int = 0, verbose: bool = True):
    """대여소별 mu·sigma(계절 보정 반영)와 날짜별 순수요를 만든다.

    **저장하지 않는 순수 계산이다.** 실험 스크립트도 이 함수를 그대로 쓴다 —
    측정 코드가 운영 코드와 다른 방식으로 mu·sigma를 구하면 비교 자체가
    성립하지 않는다(docs/분석/DEMAND_DISTRIBUTION.md 5장에서 실제로 겪었다).

    반환: (stats, daily, ratio)
    """
    net_temp = net_daily.copy()
    hours = duration_columns(duration)

    # 날짜, 대여소별 특정 시간대의 총 수요
    net_temp[f'sum{duration}'] = net_temp[hours].sum(axis=1)

    # 대여소별로 그룹화하여 평균(mu)과 표준편차(sigma) 산출
    stats = net_temp.groupby(['station_id']).agg({
        f'sum{duration}': ['mean', 'std']
    })

    stats.columns = ['mu', 'sigma']
    stats = stats.fillna(0)

    # 두 df 조인 (station_id는 groupby 인덱스 — merge가 인덱스 이름으로 조인)
    stats = stats.merge(st_initial_qty, how='left', on='station_id')
    stats = stats[~stats['stock'].isna()]

    # 학습 달의 날짜별 순수요 — 베이스라인과 모델이 **같은 입력**에서 출발한다.
    daily = pd.DataFrame({
        "station_id": net_temp["station_id"].values,
        "date": net_temp["날짜"].values,
        "demand": net_temp[f'sum{duration}'].values,
    })

    # 계절 배율은 한 번만 구해 양쪽에 같이 쓴다.
    # (베이스라인과 모델이 다른 배율을 쓰면 비교 자체가 성립하지 않는다.)
    ratio = None
    if warmup_net is not None and not warmup_net.empty:
        recent = warmup_net.copy()
        recent[f'sum{duration}'] = recent[hours].sum(axis=1)
        ratio = demand_model.season_ratio(daily, pd.DataFrame({
            "station_id": recent["station_id"].values,
            "date": recent["날짜"].values,
            "demand": recent[f'sum{duration}'].values,
        }), warmup_days)
        if ratio is not None and verbose:
            print(f"  {duration}: 계절 배율 ×{ratio:.2f}")

    # 계절 배율을 곱해 **중심을 옮긴다**(분산만 부풀리는 z와 다르다).
    stats = demand_model.apply_warmup(stats, ratio)

    return stats, daily, ratio


# 시간대에 따른 target_qty(목표대수)와 rebal_qty(재배치대수)를 계산한다.
def compute_rebal_qty(stats: pd.DataFrame, z=None, up_limit=None,
                      model_target=None) -> pd.DataFrame:
    '''
    대여소별의 시간대별(_05_10, _10_15, _15_20, _20_05) mu, sigma 를 통해 목표 stock량(target_qty)에 따른 작업량(rebal_qty)를 산출
    기본 파라미터 : 신뢰구간 z, 상한/하한 비율

    z를 지정하지 않으면 project_config.TARGET_Z(기본 1.99)를 쓴다.
    환경변수 PBR_TARGET_Z로 바꿀 수 있다 — 근거는 docs/분석/EXPERIMENTS.md 1장.

    **저장하지 않는 순수 계산이다.** 파일·DB에 남기는 것은 calculate_rebal_qty()이고,
    실험 스크립트(experiments/baseline_compare.py의 z=0 대조군)는 이 함수를 직접 쓴다 —
    측정 코드와 운영 코드가 갈리면 측정이 거짓말을 한다(docs/분석/DEMAND_DISTRIBUTION.md 5장).
    '''
    z = TARGET_Z if z is None else z
    # 상한 배수도 project_config에서 읽는다 — 웹의 실시간 재고 대조가 같은 값으로
    # "더 내려놓을 수 있는가"를 판정한다(1.19.2).
    up_limit = TARGET_QTY_UPPER_RATIO if up_limit is None else up_limit
    # 1. ---------- target_qty 계산 ----------
    # 평균 순수요(mu)가 양수(자전거가 부족한 상황)인지 확인하는 조건
    cond_pos = stats['mu'] >= 0

    # mu > 0 : 목표 재고량(target_qty) = 평균(mu) + 신뢰계수 (z : 1.99) * 표준편차(sigma)
    #
    # model_target이 오면 그 값이 **mu + z·sigma를 대신한다** — 분위수 모델이
    # 다음 기간 순수요의 95분위를 직접 예측한 것이다(docs/분석/DEMAND_DISTRIBUTION.md).
    # 모델이 없으면(기본) 여기로 오지 않으므로 기존 동작이 그대로다.
    if model_target is not None:
        stats.loc[cond_pos, 'target_qty'] = model_target[cond_pos]
    else:
        stats.loc[cond_pos, 'target_qty'] = (
            (stats.loc[cond_pos, 'mu'] + z * stats.loc[cond_pos, 'sigma'])
        )
    # mu < 0 : 목표 재고량(target_qty) = 재고(stock) + 평균(mu)
    stats.loc[~cond_pos, 'target_qty'] = (
        stats.loc[~cond_pos, 'stock'] + stats.loc[~cond_pos, 'mu']
    )

    # 목표 재고량 상한/하한 제한 (최솟값 : 0, 최댓값 : parking_lot * TARGET_QTY_UPPER_RATIO)
    # (.loc 슬라이스에 inplace clip을 쓰면 pandas 2.x에서 원본 미반영 — 재할당 방식 사용)
    stats['target_qty'] = stats['target_qty'].clip(lower=0, upper=stats['parking_lot'] * up_limit)

    # 2. ---------- rebal_qty 계산 ----------
    # 목표 재고(target_qty)가 현재 재고(stock)보다 적어, 자전거를 빼내야(pick) 하는 상황인지 판단
    pick_mask = (stats['target_qty'] < stats['stock'])

    # mu > 0 (순수요가 양수이면서)
    stats.loc[cond_pos & pick_mask, 'rebal_qty'] = (  # 빼내야(pick) 하는 경우
        stats.loc[cond_pos & pick_mask, 'target_qty'] - stats.loc[cond_pos & pick_mask, 'stock']
    )
    stats.loc[cond_pos & ~pick_mask, 'rebal_qty'] = (  # 채워야(drop) 하는 경우
        stats.loc[cond_pos & ~pick_mask, 'target_qty'] - stats.loc[cond_pos & ~pick_mask, 'stock']
    )

    # mu < 0 (순수요가 음수이면서)
    stats.loc[~cond_pos & ~pick_mask, 'rebal_qty'] = (  # 채워야(drop) 하는 경우
        stats.loc[~cond_pos & ~pick_mask, 'target_qty'] - stats.loc[~cond_pos & ~pick_mask, 'stock']
    )
    stats.loc[~cond_pos & pick_mask, 'rebal_qty'] = (  # 빼내야(pick) 하는 경우
        stats.loc[~cond_pos & pick_mask, 'target_qty'] - stats.loc[~cond_pos & pick_mask, 'stock']
    )

    # pick/drop 대수 제한
    stats['rebal_qty'] = (
        MAX_CAPACITY * np.tanh(stats['rebal_qty'] / MAX_CAPACITY)
    )

    # 정수화 처리
    stats['rebal_qty'] = np.where(
        stats['rebal_qty'] >= 0,
        np.floor(stats['rebal_qty']),
        np.ceil(stats['rebal_qty'])
    ).astype(int)

    return stats


def calculate_rebal_qty(stats: pd.DataFrame, duration: str, now: str, z=None,
                        up_limit=None, day_type=None, model_target=None):
    '''compute_rebal_qty()로 계산한 뒤 CSV·DB에 저장한다.'''
    stats = compute_rebal_qty(stats, z=z, up_limit=up_limit, model_target=model_target)

    stats.to_csv(out_file_path.format(duration=duration, now=now) + '.csv', encoding='utf-8', index=False)
    # z를 안 넘기면 compute_rebal_qty가 TARGET_Z로 채운다. 여기서도 같은 값을
    # 풀어 써야 한다 — 안 그러면 로그에 'mu + None·sigma'로 찍혀, z 실험 중에
    # 어떤 값으로 돌았는지 로그만 봐서는 알 수 없다.
    쓴_z = TARGET_Z if z is None else z
    방식 = "분위수 모델" if model_target is not None else f"mu + {쓴_z}·sigma"
    print(f"rebal{duration}가 저장되었습니다. ({방식}, 저장 위치 : "
          f"{out_file_path.format(duration=duration, now=now) + '.csv'})")

    # CSV·DB 이중 기록 (DB_PLAN 2단계). CSV가 아직 정본이다.
    db.save_output("rebalance_plan", stats, run_label=now, duration=duration,
                   day_type=day_type)


# 메인
if __name__ == '__main__':
    config = get_runtime_config()
    now = config.now
    period = config.period

    ensure_output_dirs()

    st_info = pd.read_csv(st_info_file.format(now=now), encoding='utf-8', low_memory=False)
    st_initial_qty = st_info.loc[:, ["station_id", 'parking_lot', 'stock']]

    net_daily = pd.read_csv(net_file.format(period=period), encoding='utf-8', low_memory=False)

    # 평일과 휴일 중 한쪽만 남긴다. 섞으면 부호가 반대인 대여소끼리 상쇄된다.
    전체일수 = pd.to_datetime(net_daily['날짜']).dt.date.nunique()
    net_daily = select_day_type(net_daily, '날짜', config.day_type)
    if net_daily.empty:
        raise SystemExit(
            f"{config.day_label} 데이터가 없습니다 (기간 {period})."
            " raw_to_net.py를 다시 돌려 휴일을 포함시켰는지 확인하세요.")
    사용일수 = pd.to_datetime(net_daily['날짜']).dt.date.nunique()
    print(f"{config.day_label} 기준으로 계산합니다 "
          f"({사용일수}일 / 전체 {전체일수}일) — {config.day_reason}")

    # 분위수 모델이 학습돼 있으면 target_qty를 그것으로 잡는다.
    # **없는 것이 기본이다** — 현재 모델은 베이스라인을 이기지 못했다
    # (docs/분석/DEMAND_DISTRIBUTION.md 5장). 학습은 tools/train_demand_model.py.
    bundle = demand_model.load()
    if bundle is not None:
        print(f"분위수 모델을 사용합니다 (q={bundle['quantile']:.0%})."
              " 모델 파일을 지우면 mu + z·sigma로 돌아갑니다.")

    # 계절 수준 보정(warmup) — 계획 대상 달의 첫 N일 실적으로 배율을 구한다.
    # 계절이 도약하는 달에는 지난달 통계가 구조적으로 낮다(2월→3월 수요 1.5배).
    # 자료가 없으면 조용히 건너뛴다 — 있으면 좋고 없어도 도는 보정이다.
    warmup_net = None
    if config.warmup_days > 0 and config.warmup_label != period:
        warmup_path = Path(net_file.format(period=config.warmup_label))
        if warmup_path.exists():
            warmup_net = select_day_type(
                pd.read_csv(warmup_path, encoding='utf-8', low_memory=False),
                '날짜', config.day_type)
            print(f"계절 보정: {config.warmup_label} 첫 {config.warmup_days}일 실적을 씁니다.")
        else:
            print(f"[안내] 계절 보정 건너뜀 — {config.warmup_label} 순수요가 없습니다"
                  f" ({warmup_path.name}). 지난달 통계를 그대로 씁니다.")

    # 재배치 시간은 05시, 15시로 2회, 재배치 시간은 대충 2시간으로 잡고,
    # 05~07시 재배치 기준은(05~14:59), 15~17시 재배치 기준은(15~04:59) 동안 사용할 양이다.
    # 시간대 목록은 project_config의 --duration(콤마 구분)으로 지정한다. 예: "_05_10,_10_15"
    for duration in duration_list(config):
        net_temp = net_daily
        stats, daily, ratio = build_stats(
            net_temp, st_initial_qty, duration,
            warmup_net=warmup_net, warmup_days=config.warmup_days)

        model_target = None
        if bundle is not None:
            try:
                # 학습과 **같은 함수**로 피처를 만든다(train/serve 불일치 방지).
                # month는 **계획 대상 달**이다(학습 달이 아니다).
                # training_frame이 검증 달의 월을 쓰므로 여기도 같아야 한다 —
                # 학습 달의 월을 넣으면 계절 피처가 한 달 어긋난다.
                features = demand_model.build_features(
                    daily, duration, config.day_type,
                    target_stamp(config.target_date).month, ratio)
                merged = stats[["station_id"]].merge(features, on="station_id", how="left")
                # 날씨는 **계획 대상 날짜 하루**의 값을 모든 대여소에 같이 넣는다
                # (도시 하나의 값이다). 자료가 없으면 NaN으로 남고 모델이 알아서
                # 처리한다 — 0으로 채우면 '비가 안 왔다'는 뜻이 되어 버린다.
                merged = demand_model.add_weather(
                    merged, duration, date=target_stamp(config.target_date))
                predicted = demand_model.predict(bundle, merged)
                model_target = pd.Series(predicted, index=stats.index)
            except Exception as err:      # 모델 문제로 파이프라인을 멈추지 않는다
                print(f"[경고] 분위수 예측 실패({type(err).__name__}: {err})."
                      " mu + z·sigma로 계산합니다.")

        calculate_rebal_qty(stats, duration, now, day_type=config.day_type,
                            model_target=model_target)
