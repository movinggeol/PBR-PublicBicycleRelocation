import pandas as pd

path = "data/raw_data/타슈 대여이력 정보(25.04~26.03)/대전시 공영자전거 타슈 대여이력 정보({period}).csv"
result_file = "data/raw_data/타슈 대여이력(25.04~26.03).csv"
#result_file = "data/raw_data/대전시 공영자전거 타슈 대여이력 정보(25년11월).csv"

def concat_file(period: list):
    df_list = []

    for p in period:

        file_path = path.format(period=p)

        print(f"읽는 중: {p}")

        try:
            df = pd.read_csv(
                file_path,
                encoding='utf-8-sig'
            )
            print("utf-8-sig 성공")

        except UnicodeDecodeError:

            df = pd.read_csv(
                file_path,
                encoding='cp949'
            )
            print("cp949 성공")

        print(f"{p}의 shape : {df.shape}")
        df_list.append(df)
        print("-"*50)


    final_df = pd.concat(df_list, ignore_index=True)

    print()
    print(f"최종 파일 shape : {final_df.shape}")
    print(f"최종 파일 head : {final_df.head()}")

    final_df.to_csv(result_file, index=False, encoding='utf-8')
    print(f"{result_file}을 저장했습니다.")


def preprocessing(file_path : str):
    df = pd.read_csv(file_path, encoding='utf-8')
    #print(f"파일 형태 : {df.shape}")
    #print(f"파일 내용 예시 : {df.head()}")
    print()
    #print(df.describe())
    

    print(f"전처리 전의 형태 : {df.shape}")
    target_col = ['이용시간(분)', '이용거리(km)']

    print('-'*50)
    
    for t in target_col:

        Q3 = df[t].quantile(0.75)
        Q1 = df[t].quantile(0.25)
        
        IQR = Q3 - Q1

        print(f"IQR({t}) : {IQR}")
        
        lower = Q1 - 1.5 * IQR
        upper = Q3 + 1.5 * IQR 

        df = df[(df[t] >= lower) & (df[t] <= upper)]
        print(f"df[{t}]의 이상값을 제거한 후의 shape : {df[t].shape}")
        print('-'*50)

    print(f"전처리 후의 형태 : {df.shape}")

    df.to_csv(result_file, index=False, encoding='utf-8')
    print(f"전처리 후, {result_file}을 저장했습니다.")


if __name__ == "__main__":
    
    period = [
        "25년04월", "25년05월", "25년06월", "25년07월",
        "25년08월", "25년09월", "25년10월", "25년11월",
        "25년12월", "26년01월", "26년02월", "26년03월"
    ]
    
    #concat_file(period)

    #file_path = "data/raw_data/대전시 공영자전거 타슈 대여이력 정보(25년11월).csv"
    #preprocessing(file_path)   # 테스트
    
    preprocessing(result_file)

    df = pd.read_csv(result_file, encoding='utf-8')
    print(df.shape)
    print(df.columns)
    
    #df = df.iloc[:, 1:].copy()
    #df.to_csv(result_file, index=False, encoding='utf-8')