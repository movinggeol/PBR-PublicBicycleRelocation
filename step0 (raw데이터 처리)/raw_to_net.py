from datetime import datetime
import pandas as pd

# read_csv
file_path = "data/raw_data/대전시 공영자전거 타슈 대여이력 정보(25년11월).csv"
# to_csv
out_file_path = "data/pp_data/순수요/st_net_daily ({period}).csv"

period = file_path[-11:-5]
period = period[:3] + " " + period[3:]

'''
3753개의 자전거 운용됨
1370개의 '대여_대여소ID'
1371개의 '반납_대여소ID'
'''

raw_data = pd.read_csv(file_path, low_memory=False)
#print(raw_data.head())
data = raw_data.loc[:, ['자전거번호', 
                        '대여일시', '대여_대여소ID', '대여_X좌표', '대여_Y좌표', 
                        '반납일시', '반납_대여소ID', '반납_X좌표', '반납_Y좌표',]]

#print(data.info())
data['대여일시'] = pd.to_datetime(data['대여일시'])
data['반납일시'] = pd.to_datetime(data['반납일시'])
data['평일유무'] = data['대여일시'].dt.weekday < 5

d_data = data[data['평일유무']].copy()
#e_data = data[~data['평일유무']]

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

#print(rent.head())
#print(ret.head())

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
#print(net_daily.head())

net_daily = net_daily.reset_index()
net_daily.rename(columns={'level_1':'station_id'}, inplace=True)
#print(net_daily.head())

# csv파일로 저장
net_daily.to_csv(out_file_path.format(period=period), encoding='utf-8', index=False)
print("날짜별 대여소당 순수요 데이터 저장")
print(f"\n{out_file_path.format(period=period)} 가 저장되었습니다.")

'''
print()
print(d_data.info())

bike = d_data['자전거번호'].unique().tolist()
print(f"\n\n{len(bike)}개의 자전거 운용됨")

rent_station = d_data['대여_대여소ID'].unique().tolist()
print(f"\n\n{len(rent_station)}개의 '대여_대여소ID'")
#print(f" \n내용은 : {rent_station}")

ret_station = d_data['반납_대여소ID'].unique().tolist()
print(f"\n\n{len(ret_station)}개의 '반납_대여소ID'")
#print(f" \n내용은 : {ret_station}")
print()
'''


