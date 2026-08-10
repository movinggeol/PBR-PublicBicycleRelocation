"""원천 대여 이력에서 날짜·대여소·시간대별 순수요(대여 − 반납)를 계산한다.

입력: DB의 rental_history(해당 period가 적재돼 있으면) 또는 원천 CSV
      적재는 `python tools/load_rentals.py`
출력: data/pp_data/순수요/st_net_daily ({period}).csv  (net_00 ~ net_23)
      + SQLite net_demand 테이블
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

import db
from project_config import PROJECT_ROOT, ensure_output_dirs, get_runtime_config

# to_csv
out_file_path = str(PROJECT_ROOT / "data/pp_data/순수요/st_net_daily ({period}).csv")


def main() -> None:
    config = get_runtime_config()
    period = config.period

    # DB에 적재돼 있으면 DB에서, 없으면 원천 CSV에서 읽는다 (DB_PLAN 4단계).
    # 순수요 계산에 실제로 쓰는 4개 컬럼만 읽는다(60만 행에서 전체 12컬럼과 차이가 크다).
    data, source = db.read_rental_source(
        period, csv_path=config.raw_path,
        columns=['대여일시', '대여_대여소ID', '반납일시', '반납_대여소ID'])
    if data.empty:
        raise SystemExit(f"대여이력을 찾을 수 없습니다: {config.raw_path}")
    print(f"대여이력 {len(data):,}행 로드 (출처: {source})")

    data['대여일시'] = pd.to_datetime(data['대여일시'])
    data['반납일시'] = pd.to_datetime(data['반납일시'])
    data['평일유무'] = data['대여일시'].dt.weekday < 5

    d_data = data[data['평일유무']].copy()

    d_data['날짜'] = d_data['대여일시'].dt.date

    rent = (
        d_data
        .assign(hour=d_data['대여일시'].dt.hour)
        .groupby(['날짜', '대여_대여소ID', 'hour'])
        .size()
        .unstack(fill_value=0)
    )

    ret = (
        d_data
        .assign(hour=d_data['반납일시'].dt.hour)
        .groupby(['날짜', '반납_대여소ID', 'hour'])
        .size()
        .unstack(fill_value=0)
    )

    # 인덱스 정렬 및 시간 컬럼 맞춤
    hours = list(range(24))
    rent = rent.reindex(columns=hours, fill_value=0)
    ret = ret.reindex(columns=hours, fill_value=0)

    # 모든 날짜와 대여소 조합을 포함하도록 인덱스 재설정
    all_index = rent.index.union(ret.index)
    rent = rent.reindex(all_index, fill_value=0)
    ret = ret.reindex(all_index, fill_value=0)

    # 날짜별 순수요 (Net Demand) 계산
    net_daily = rent - ret
    net_daily.columns = [f"net_{h:02d}" for h in hours]

    net_daily = net_daily.reset_index()
    net_daily.rename(columns={'level_1': 'station_id'}, inplace=True)

    # csv파일로 저장
    ensure_output_dirs()
    net_daily.to_csv(out_file_path.format(period=period), encoding='utf-8', index=False)
    print("날짜별 대여소당 순수요 데이터 저장")
    print(f"\n{out_file_path.format(period=period)} 가 저장되었습니다.")

    # CSV·DB 이중 기록 (DB_PLAN 2단계).
    # 순수요는 원천 데이터 기간에만 의존하므로 run_label이 아니라 period로 묶는다.
    db.save_output("net_demand", net_daily, period=period)


if __name__ == '__main__':
    main()
