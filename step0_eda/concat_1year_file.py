"""월별 원천 대여 이력 CSV를 1년치 파일로 병합하고 이상치를 제거한다.

- concat: data/raw_data/타슈 대여이력 정보(25.04~26.03)/ 하위 월별 파일 병합
- preprocess: 이용시간(분)·이용거리(km)의 IQR×1.5 밖 이상치 제거

실행 예:
    python "step0_eda/concat_1year_file.py" --concat --preprocess
    python "step0_eda/concat_1year_file.py" --preprocess
옵션 없이 실행하면(파이프라인 기본) 병합 파일이 있을 때만 이상치 제거를 수행하고,
없으면 건너뛴다(전체 파이프라인 중단 방지).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from project_config import PROJECT_ROOT

path = str(PROJECT_ROOT / "data/raw_data/타슈 대여이력 정보(25.04~26.03)/대전시 공영자전거 타슈 대여이력 정보({period}).csv")
result_file = str(PROJECT_ROOT / "data/raw_data/타슈 대여이력(25.04~26.03).csv")


def concat_file(period: list):
    df_list = []

    for p in period:
        file_path = path.format(period=p)
        print(f"읽는 중: {p}")

        try:
            df = pd.read_csv(file_path, encoding='utf-8-sig')
            print("utf-8-sig 성공")
        except UnicodeDecodeError:
            df = pd.read_csv(file_path, encoding='cp949')
            print("cp949 성공")

        print(f"{p}의 shape : {df.shape}")
        df_list.append(df)
        print("-" * 50)

    final_df = pd.concat(df_list, ignore_index=True)

    print()
    print(f"최종 파일 shape : {final_df.shape}")
    print(f"최종 파일 head : {final_df.head()}")

    final_df.to_csv(result_file, index=False, encoding='utf-8')
    print(f"{result_file}을 저장했습니다.")


def preprocessing(file_path: str):
    df = pd.read_csv(file_path, encoding='utf-8')

    print(f"전처리 전의 형태 : {df.shape}")
    target_col = ['이용시간(분)', '이용거리(km)']

    print('-' * 50)

    for t in target_col:
        Q3 = df[t].quantile(0.75)
        Q1 = df[t].quantile(0.25)

        IQR = Q3 - Q1
        print(f"IQR({t}) : {IQR}")

        lower = Q1 - 1.5 * IQR
        upper = Q3 + 1.5 * IQR

        df = df[(df[t] >= lower) & (df[t] <= upper)]
        print(f"df[{t}]의 이상값을 제거한 후의 shape : {df[t].shape}")
        print('-' * 50)

    print(f"전처리 후의 형태 : {df.shape}")

    df.to_csv(file_path, index=False, encoding='utf-8')
    print(f"전처리 후, {file_path}을 저장했습니다.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--concat", action="store_true", help="월별 파일 병합 실행")
    parser.add_argument("--preprocess", action="store_true", help="이상치 제거 실행")
    args, _ = parser.parse_known_args()

    period = [
        "25년04월", "25년05월", "25년06월", "25년07월",
        "25년08월", "25년09월", "25년10월", "25년11월",
        "25년12월", "26년01월", "26년02월", "26년03월"
    ]

    if args.concat:
        concat_file(period)

    if args.preprocess:
        preprocessing(result_file)
    elif not args.concat:
        # 옵션 없이 실행된 경우(파이프라인 기본): 병합 파일이 있으면 이상치 제거, 없으면 건너뜀
        if Path(result_file).exists():
            preprocessing(result_file)
        else:
            print(f"병합 파일이 없어 건너뜁니다: {result_file}")
