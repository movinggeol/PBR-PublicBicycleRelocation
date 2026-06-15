import pandas as pd
from datetime import datetime

# 1545

file_path = "data/raw_data/대전시 공영자전거 타슈 대여이력 정보(25년11월).csv"
#file_path = "data/raw_data/타슈 대여이력(25.04~26.03).csv"


def now_month(df: pd.DateFrame):
    '''
    원본 데이터에서 현재 달에 해당하는 데이터만 필터링(1달 단위)
    '''
    now = datetime.now()
    print(now)

    month = now.strftime('%m')
    print(month)

    
    temp_df = df[df['대여일시'].str.split('-').str[1] == month]
    print(f"현재({month}월)에 해당하는 데이터로 필터링한 shape : {temp_df.shape}")

    

def month_graph(df: pd.DateFrame):
    '''
    1년치 자료에서 매 달마다 대여량을 추출해 시각화
    '''
    
    # [봄(3~5), 여름(6~8), 가을(9~11), 겨울(12~2)], 달마다 대여량을 이용해 그래프 제작
     
    monthly_rent = {}
    
    for i in range(1,13):
        temp_df = df[df['대여일시'].dt.month == i]

        monthly_rent[i] = len(temp_df)
    
    print(monthly_rent)


if __name__ == '__main__':

    df = pd.read_csv(file_path, encoding='utf-8')
    df['대여일시'] = pd.to_datetime(df['대여일시'])

    print(df.head())
    print(df.shape)

    #now_month(file_path)
    month_graph(file_path)