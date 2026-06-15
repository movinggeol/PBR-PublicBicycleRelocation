from datetime import datetime
import pandas as pd
import numpy as np
import api_to_info, raw_to_net

# read_csv
st_info_file = "data/pp_data/대여소 정보/st_info ({now}).csv"
net_file = "data/pp_data/순수요/st_net_daily ({period}).csv"

# to_csv
out_file_path = "data/pp_data/재배치 정보/rebal_qty{duration} ({now})" #.csv

#now = datetime.now().strftime('%Y-%m-%d %H')
now = '2026-05-21 18'
period = '25년 11월'

MAX_CAPACITY=10

# 시간대에 따른 target_qty(목표대수)와 rebal_qty(재배치대수)를 계산한다. (z=1.65)
def calculate_rebal_qty(stats: pd.DataFrame, duration: str, z=1.65, up_limit=1.5, low_limit=0.2):
    '''
    대여소별의 시간대별(_05_10, _10_15, _15_20, _20_05) mu, sigma 를 통해 목표 stock량(target_qty)에 따른 작업량(rebal_qty)를 산출해 저장
    기본 파라미터 : 신뢰구간 z, 상한/하한 비율
    '''
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
    stats.loc[:, 'target_qty'].clip(lower=0, upper=stats['parking_lot']*up_limit, inplace=True)

    # 2. ---------- rebal_qty 계산 ----------
    # 목표 재고(target_qty)가 현재 재고(stock)보다 적어, 자전거를 빼내야(pick) 하는 상황인지 판단
    pick_mask = (stats['target_qty'] < stats['stock'])

    # mu > 0 (순수요가 양수이면서)
    stats.loc[cond_pos & pick_mask, 'rebal_qty'] = ( # 빼내야(pick) 하는 경우
        stats.loc[cond_pos & pick_mask, 'target_qty'] - stats.loc[cond_pos & pick_mask, 'stock']
    )
    stats.loc[cond_pos & ~pick_mask, 'rebal_qty'] = ( # 채워야(drop) 하는 경우
        stats.loc[cond_pos & ~pick_mask, 'target_qty'] - stats.loc[cond_pos & ~pick_mask, 'stock']
        )#.clip(lower= -abs(low_limit * stats.loc[cond_pos & ~pick_mask, 'parking_lot'] - stats.loc[cond_pos & ~pick_mask, 'stock']))

    
    # mu < 0 (순수요가 음수이면서)
    stats.loc[~cond_pos & ~pick_mask, 'rebal_qty'] = ( # 채워야(drop) 하는 경우
        stats.loc[~cond_pos & ~pick_mask, 'target_qty'] - stats.loc[~cond_pos & ~pick_mask, 'stock']
        )
    stats.loc[~cond_pos & pick_mask, 'rebal_qty'] = (  # 빼내야(pick) 하는 경우
        stats.loc[~cond_pos & pick_mask, 'target_qty'] - stats.loc[~cond_pos & pick_mask, 'stock']
        )

     # pick/drop 대수 제한
    stats['rebal_qty'] = (
        MAX_CAPACITY * np.tanh(stats['rebal_qty'] / MAX_CAPACITY)
    )
    #stats['rebal_qty'] = stats['rebal_qty'].clip(lower=-MAX_CAPACITY, upper=MAX_CAPACITY)


    #stats = stats[stats['mu'] > 0]
    # 정수화 처리
    stats['rebal_qty'] = np.where(
        stats['rebal_qty'] >= 0,
        np.floor(stats['rebal_qty']),
        np.ceil(stats['rebal_qty'])
    ).astype(int)


    stats.to_csv(out_file_path.format(duration=duration, now=now) + '.csv', encoding='utf-8', index=False)
    print(f"rebal{duration}가 저장되었습니다. (저장 위치 : {out_file_path.format(duration=duration, now=now) + '.csv'})")


# 메인
if __name__ == '__main__':
    st_info = pd.read_csv(st_info_file.format(now=now), encoding='utf-8', low_memory=False)
    st_initial_qty = st_info.loc[:, ["station_id", 'parking_lot', 'stock']]

    net_daily = pd.read_csv(net_file.format(period=period), encoding='utf-8', low_memory=False)

    # 재배치 시간은 05시, 15시로 2회, 재배치 시간은 대충 2시간으로 잡고,
    # 05~07시 재배치 기준은(05~14:59), 15~17시 재배치 기준은(15~04:59) 동안 사용할 양이다.
    #   (하루에 2번은 좀 적은 것 같아서 늘려야 할 수도)
    #durations = ['_05_10', '_10_15', '_15_20', '_20_05']
    durations = ['_05_10']

    for duration in durations:
        net_temp = net_daily.copy()
        start = int(duration.split('_')[1])
        end = int(duration.split('_')[2])

        if start < end:
            hours = [f"net_{h:02d}" for h in range(start, end)]
        else:
            hours = [f"net_{h:02d}" for h in list(range(start, 24)) + list(range(0, end))]

        # 날짜, 대여소별 특정 시간대의 총 수요
        net_temp[f'sum{duration}'] = net_temp[hours].sum(axis=1)
        #print(net_temp)

        
        # 대여소별로 그룹화하여 평균(mu)과 표준편차(sigma) 산출
        stats = net_temp.groupby(['station_id']).agg({
            f'sum{duration}':['mean', 'std']
        })

        stats.columns = ['mu', 'sigma']
        stats.reset_index()
        stats = stats.fillna(0)
        
        # 두 df 이너조인
        stats = stats.merge(st_initial_qty, how='left', on='station_id')
        stats = stats[~stats['stock'].isna()]
        #print(stats.head())

        calculate_rebal_qty(stats, duration)