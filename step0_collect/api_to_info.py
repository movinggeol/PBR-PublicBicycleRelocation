"""대여 이력·재고·주차대수를 통합해 대여소 정보(st_info)를 만든다.

입력: DB의 rental_history(해당 period가 적재돼 있으면) 또는 원천 CSV,
      대여소별_자전거대수 ({now}).csv, 대여소별_주차대수 ({now}).csv
      적재는 `python tools/load_rentals.py`
출력: data/pp_data/대여소 정보/st_info ({now}).csv + SQLite station_info 테이블
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

import db
from project_config import (
    DATA_ROOT, PROJECT_ROOT, ensure_output_dirs, get_runtime_config, select_day_type,
)

# read_csv
stock_file = str(DATA_ROOT / "pp_data/대여소별 재고/대여소별_자전거대수 ({now}).csv")
parking_lot_file = str(DATA_ROOT / "pp_data/대여소별 주차대수/대여소별_주차대수 ({now}).csv")

# to_csv
out_file_path = str(DATA_ROOT / "pp_data/대여소 정보/st_info ({now}).csv")


def main() -> None:
    config = get_runtime_config()
    now = config.now

    # DB에 적재돼 있으면 DB에서, 없으면 원천 CSV에서 읽는다 (DB_PLAN 4단계).
    # 집계에 실제로 쓰는 컬럼만 읽는다.
    st, source = db.read_rental_source(
        config.period, csv_path=config.raw_path,
        columns=['대여일시', '대여_대여소ID', '대여_대여소명', '대여_X좌표', '대여_Y좌표',
                 '반납일시', '반납_대여소ID', '이용시간(분)', '이용거리(km)'])
    if st.empty:
        raise SystemExit(f"대여이력을 찾을 수 없습니다: {config.raw_path}")
    print(f"대여이력 {len(st):,}행 로드 (출처: {source})")

    # 계획하려는 요일 구분(--day-type)에 맞춰 거른다.
    # 평일 계획이면 평일 이력만, 휴일 계획이면 휴일 이력만 집계해야
    # 대여소 목록·좌표·이용량이 그 계획과 같은 세계를 가리킨다.
    # (예전에는 평일로 고정이라 휴일에만 쓰이는 대여소가 통째로 빠졌다.)
    st['대여일시'] = pd.to_datetime(st['대여일시'])
    st = select_day_type(st, '대여일시', config.day_type).copy()
    if st.empty:
        raise SystemExit(f"{config.day_label} 대여이력이 없습니다.")

    year_month = st['대여일시'].dt.to_period('M').iloc[0]

    day_qty = st['대여일시'].dt.date.nunique()
    print(f"{year_month}에는 {day_qty}개의 {config.day_label}이 존재합니다.")

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

    # CSV·DB 이중 기록 (DB_PLAN 2단계). CSV가 아직 정본이다.
    db.save_output("station_info", intersect_df, run_label=now,
                   period=config.period)

    '''
    # ------------------ 이용자들 대상으로 사용하지 않는 대여소(타슈관제센터 2곳) ------------------
    사용자들 대상이 아닌 대여소 2곳(타슈 관제센터 정비대기, 타슈관제센터)
    0     ST0001  타슈관제센터 정비대기  36.406607  127.306457   ...
    1101  ST1220       타슈관제센터  36.402205  127.309734   ...
    '''


if __name__ == '__main__':
    main()
