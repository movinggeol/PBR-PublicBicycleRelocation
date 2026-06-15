from datetime import datetime
import pandas as pd
import re
import tashu_api

# read_csv
file_path = "data/pp_data/대여소별 재고/대여소별_자전거대수 ({now}).csv"
# to_csv
out_file_path = "data/pp_data/대여소별 주차대수/대여소별_주차대수 ({now}).csv"

now = datetime.now().strftime('%Y-%m-%d %H')


def parse_capacity(x) -> int:
    s = str(x).strip()
    total = 0

    # 쉼표로 여러 덩어리 분리: "10대용*1, 5대용*2"
    parts = [p.strip() for p in s.split(",")]

    for p in parts:
        # 예: "10대용*1", "5대용(스텐)*2", "1대용*15"
        m = re.search(r"(\d+)\s*대용(?:\([^)]*\))?\s*\*\s*(\d+)", p)
        if m:
            size = int(m.group(1))   # 10, 5, 1 ...
            cnt  = int(m.group(2))   # 1,2,3...
            total += size * cnt
        else:
            # 혹시 '*' 없이 "10대용" 같은 변형이 있으면 1개로 가정(선택)
            m2 = re.search(r"(\d+)\s*대용(?:\([^)]*\))?", p)
            if m2:
                size = int(m2.group(1))
                total += size * 1
    return total


df = pd.read_csv(file_path.format(now=now), encoding='utf-8', low_memory=False)
#print(df)


df = df.iloc[:,[0,2,3,4]].copy()
#print(df)

df.loc[:, 'parking_info'] = df.loc[:, 'parking_info'].str.split('/', expand=True)[1].str.strip()
df.loc[:, 'parking_info'] = df.loc[:, 'parking_info'].replace('parking_lot 없음', 0)
df = df.loc[~df['parking_info'].isna()]

df['parking_lot'] = df['parking_info'].apply(parse_capacity)
df = df.iloc[:, [0,2,3,4]]

#--------------------------------------------------------------------
# 실제와 다른 정보 수정(도로뷰 활용)
df.loc[df['station_id']=='ST0370', 'parking_lot'] = 30
df.loc[df['station_id']=='ST1133', 'parking_lot'] = 5
df.loc[df['station_id']=='ST1392', 'parking_lot'] = 5

df.loc[df['station_id']=='ST1341', 'parking_lot'] = 30 # 실제 자전거 주자장 매우 큼
#--------------------------------------------------------------------

#print(df.head())
print(f"모든 대여소의 주차대수는 : {df['parking_lot'].sum(axis=0)}")

df.to_csv(out_file_path.format(now=now), encoding='utf-8', index=False)
print(f"\n{out_file_path.format(now=now)} 가 저장되었습니다.")