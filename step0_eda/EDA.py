"""원천 대여 이력의 기초 탐색(EDA).

입력: project_config의 raw_file (기본: 25년 11월 대여 이력)
현재는 월별 대여량 집계를 출력한다. 입력 파일이 없으면 건너뛴다(파이프라인 중단 방지).
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from project_config import get_runtime_config


def now_month(df: pd.DataFrame):
    '''
    원본 데이터에서 현재 달에 해당하는 데이터만 필터링(1달 단위)
    '''
    now = datetime.now()
    month = now.strftime('%m')

    temp_df = df[df['대여일시'].dt.month == int(month)]
    print(f"현재({month}월)에 해당하는 데이터로 필터링한 shape : {temp_df.shape}")


def month_graph(df: pd.DataFrame):
    '''
    자료에서 매 달마다 대여량을 추출해 출력
    '''
    # [봄(3~5), 여름(6~8), 가을(9~11), 겨울(12~2)], 달마다 대여량을 이용해 그래프 제작

    monthly_rent = {}

    for i in range(1, 13):
        temp_df = df[df['대여일시'].dt.month == i]
        monthly_rent[i] = len(temp_df)

    print(monthly_rent)


if __name__ == '__main__':
    config = get_runtime_config()

    if not config.raw_path.exists():
        print(f"원천 파일이 없어 EDA를 건너뜁니다: {config.raw_path}")
        sys.exit(0)

    df = pd.read_csv(config.raw_path, encoding='utf-8')
    df['대여일시'] = pd.to_datetime(df['대여일시'])

    print(df.head())
    print(df.shape)

    month_graph(df)
