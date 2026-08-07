"""대여 이력·재고·주차대수를 통합해 대여소 정보(st_info)를 만든다.

입력: 원천 대여 이력 CSV(project_config의 raw_file),
      대여소별_자전거대수 ({now}).csv, 대여소별_주차대수 ({now}).csv
출력: data/pp_data/대여소 정보/st_info ({now}).csv
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from project_config import PROJECT_ROOT, ensure_output_dirs, get_runtime_config

# read_csv
stock_file = str(PROJECT_ROOT / "data/pp_data/대여소별 재고/대여소별_자전거대수 ({now}).csv")
parking_lot_file = str(PROJECT_ROOT / "data/pp_data/대여소별 주차대수/대여소별_주차대수 ({now}).csv")

# to_csv
out_file_path = str(PROJECT_ROOT / "data/pp_data/대여소 정보/st_info ({now}).csv")


def main() -> None:
    config = get_runtime_config()
    now = config.now

    st = pd.read_csv(config.raw_path, low_memory=False)

    # 대여일시 -> 평일만 필터링
    st['대여일시'] = pd.to_datetime(st['대여일시'])
    st = st[st['대여일시'].dt.weekday < 5].copy()

    print(st.head())

    year_month = st['대여일시'].dt.to_period('M').iloc[0]

    weekday_qty = st['대여일시'].dt.date.nunique()
    print(f"{year_month}에는 {weekday_qty}개의 평일이 존재합니다.")

    st = st.loc[:, ['대여일시', '대여_대여소ID', '대여_대여소명', '대여_X좌표', '대여_Y좌표',
                             '반납일시', '반납_대여소ID', '이용시간(분)', '이용거리(km)']]

    # 대여 대여소를 기준으로 그룹화하여 집계
    # (그룹화 과정에서 인덱스가 된 st_id를 다시 컬럼으로 빼내고, 중복된 첫 번째 열을 제외)
    rent_agg = st.groupby('대여_대여소ID').agg(
        station_id=('대여_대여소ID', 'first'),
        station_name=('대여_대여소명', 'first'),
        lat=('대여_X좌표', 'first'),
        lon=('대여_Y좌표', 'first'),
        rent_count=('대여_대여소ID', 'size'),
        총_이용시간_분=('이용시간(분)', 'sum'),
        총_이용거리_km=('이용거리(km)', 'sum'),
    ).reset_index().iloc[:, 1:]

    # 반납 대여소를 기준으로 그룹화하여 반납 건수(return_count)를 계산하고 컬럼명 정리
    ret_agg = (
        st.groupby('반납_대여소ID')
        .size()
        .reset_index(name='return_count')
        .rename(columns={'반납_대여소ID': 'station_id'})
    )

    # merge(대여 : rent, 반납 : return)
    st_info = rent_agg.merge(ret_agg, how='left', on='station_id').dropna()

    # merge(대여소 정보 : st_info, 주차대수 : parking_lot)
    parking_lot = pd.read_csv(parking_lot_file.format(now=now), encoding='utf-8')

    st_info = (
        st_info
        .iloc[:, [0, 1, 2, 3, 4, 7, 5, 6]]
    )
    st_info = (
        st_info
        .merge(parking_lot, how='left', on='station_id')
        .iloc[:, [0, 1, 2, 3, 10, 4, 5, 6, 7]]
    )

    st_info.columns = [
        'station_id', 'station_name', 'lat', 'lon', 'parking_lot', 'rent_count', 'return_count',
        '총 이용시간(분)', '총 이용거리(km)']

    # stock 파일 읽기 : 대여소별 자전거 주차대수
    st_stock = pd.read_csv(stock_file.format(now=now), encoding='utf-8')
    st_stock = st_stock.iloc[:, [0, -1]]

    intersect_df = st_info.merge(st_stock, how='left', on='station_id')
    print(intersect_df.head())

    intersect_df = intersect_df.iloc[:, [0, 1, 2, 3, 4, 9, 5, 6, 7, 8]]

    ensure_output_dirs()
    intersect_df.to_csv(out_file_path.format(now=now), encoding='utf-8', index=False)
    print(f"\n{out_file_path.format(now=now)} 가 저장되었습니다.")

    '''
    # ------------------ 이용자들 대상으로 사용하지 않는 대여소(타슈관제센터 2곳) ------------------
    사용자들 대상이 아닌 대여소 2곳(타슈 관제센터 정비대기, 타슈관제센터)
    0     ST0001  타슈관제센터 정비대기  36.406607  127.306457   ...
    1101  ST1220       타슈관제센터  36.402205  127.309734   ...
    '''


if __name__ == '__main__':
    main()
