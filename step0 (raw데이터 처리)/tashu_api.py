from datetime import datetime
import requests
import pandas as pd
from dotenv import load_dotenv
import os

# to_csv
out_file_path = "data/pp_data/대여소별 재고/대여소별_자전거대수 ({now}).csv"

now = datetime.now().strftime('%Y-%m-%d %H')

API_URL = "https://bikeapp.tashu.or.kr:50041/v1/openapi/station"

load_dotenv()
API_KEY = os.getenv("TASHU_API_KEY")

headers = {
    "api-token": API_KEY
}

response = requests.get(API_URL, headers=headers)


if response.status_code == 200:
    data = response.json()
    df = pd.DataFrame(data["results"])
else:
    print("API 호출 실패:", response.status_code, response.text)
print("타슈 대여소 api 호출 완료!")


#print(df.head())
df = df.iloc[:, [0,1,3,4,5,7]]

# api에서 (x,y) -> (위도, 경도)의 순서로 되어 있음 (주의)
df.rename(
    columns={'id':'station_id', 'name':'station_name', 'name_cn': 'parking_info',
             'x_pos':'lat', 'y_pos': 'lon',
             'parking_count':'stock'}, 
    inplace=True)

df.to_csv(out_file_path.format(now=now), encoding='utf-8', index=False)
print(f"\n{out_file_path.format(now=now)} 가 저장되었습니다.")

    
