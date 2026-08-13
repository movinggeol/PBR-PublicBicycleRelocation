"""시간대(duration)별 목표 재고(target_qty)와 재배치량(rebal_qty)을 계산한다.

**평일과 주말 중 한쪽만 골라 계산한다**(`--day-type`, 기본 weekday).
두 요일 구분은 수요 구조가 달라 섞으면 안 된다 — 실측에서 대여소의 33~37%가
평일과 주말에 부호가 반대였다(experiments/weekend_profile.py). 섞어서 평균 내면
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
from project_config import (
    PROJECT_ROOT,
    TARGET_Z,
    duration_list,
    ensure_output_dirs,
    get_runtime_config,
    select_day_type,
)

# read_csv
st_info_file = str(PROJECT_ROOT / "data/pp_data/대여소 정보/st_info ({now}).csv")
net_file = str(PROJECT_ROOT / "data/pp_data/순수요/st_net_daily ({period}).csv")

# to_csv
out_file_path = str(PROJECT_ROOT / "data/pp_data/재배치 정보/rebal_qty{duration} ({now})")  # .csv

MAX_CAPACITY = 10


# 시간대에 따른 target_qty(목표대수)와 rebal_qty(재배치대수)를 계산한다.
def calculate_rebal_qty(stats: pd.DataFrame, duration: str, now: str, z=None,
                        up_limit=1.5, low_limit=0.2, day_type=None):
    '''
    대여소별의 시간대별(_05_10, _10_15, _15_20, _20_05) mu, sigma 를 통해 목표 stock량(target_qty)에 따른 작업량(rebal_qty)를 산출해 저장
    기본 파라미터 : 신뢰구간 z, 상한/하한 비율

    z를 지정하지 않으면 project_config.TARGET_Z(기본 1.99)를 쓴다.
    환경변수 PBR_TARGET_Z로 바꿀 수 있다 — 근거는 docs/EXPERIMENTS.md 1장.
    '''
    z = TARGET_Z if z is None else z
    # 1. ---------- target_qty 계산 ----------
    # 평균 순수요(mu)가 양수(자전거가 부족한 상황)인지 확인하는 조건
    cond_pos = stats['mu'] >= 0

    # mu > 0 : 목표 재고량(target_qty) = 평균(mu) + 신뢰계수 (z : 1.65) * 표준편차(sigma)
    stats.loc[cond_pos, 'target_qty'] = (
        (stats.loc[cond_pos, 'mu'] + z * stats.loc[cond_pos, 'sigma'])
    )
    # mu < 0 : 목표 재고량(target_qty) = 재고(stock) + 평균(mu)
    stats.loc[~cond_pos, 'target_qty'] = (
        stats.loc[~cond_pos, 'stock'] + stats.loc[~cond_pos, 'mu']
    )

    # 목표 재고량 상한/하한 제한 (최솟값 : 0, 최댓값 : parking_lot * 1.5)
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

    stats.to_csv(out_file_path.format(duration=duration, now=now) + '.csv', encoding='utf-8', index=False)
    print(f"rebal{duration}가 저장되었습니다. (z={z}, 저장 위치 : {out_file_path.format(duration=duration, now=now) + '.csv'})")

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

    # 평일과 주말 중 한쪽만 남긴다. 섞으면 부호가 반대인 대여소끼리 상쇄된다.
    전체일수 = pd.to_datetime(net_daily['날짜']).dt.date.nunique()
    net_daily = select_day_type(net_daily, '날짜', config.day_type)
    if net_daily.empty:
        raise SystemExit(
            f"{config.day_label} 데이터가 없습니다 (기간 {period})."
            " raw_to_net.py를 다시 돌려 주말을 포함시켰는지 확인하세요.")
    사용일수 = pd.to_datetime(net_daily['날짜']).dt.date.nunique()
    print(f"{config.day_label} 기준으로 계산합니다 ({사용일수}일 / 전체 {전체일수}일)")

    # 재배치 시간은 05시, 15시로 2회, 재배치 시간은 대충 2시간으로 잡고,
    # 05~07시 재배치 기준은(05~14:59), 15~17시 재배치 기준은(15~04:59) 동안 사용할 양이다.
    # 시간대 목록은 project_config의 --duration(콤마 구분)으로 지정한다. 예: "_05_10,_10_15"
    for duration in duration_list(config):
        net_temp = net_daily.copy()
        start = int(duration.split('_')[1])
        end = int(duration.split('_')[2])

        if start < end:
            hours = [f"net_{h:02d}" for h in range(start, end)]
        else:
            hours = [f"net_{h:02d}" for h in list(range(start, 24)) + list(range(0, end))]

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

        calculate_rebal_qty(stats, duration, now, day_type=config.day_type)
