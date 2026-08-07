"""TASHU Open API에서 대여소별 현재 재고를 수집한다.

출력: data/pp_data/대여소별 재고/대여소별_자전거대수 ({now}).csv
now는 project_config의 분석 시점 라벨을 사용한다(파이프라인 전체가 같은 라벨 공유).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import requests
from dotenv import load_dotenv

from project_config import PROJECT_ROOT, ensure_output_dirs, get_runtime_config

# to_csv
out_file_path = str(PROJECT_ROOT / "data/pp_data/대여소별 재고/대여소별_자전거대수 ({now}).csv")

API_URL = "https://bikeapp.tashu.or.kr:50041/v1/openapi/station"


def main() -> None:
    config = get_runtime_config()

    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.getenv("TASHU_API_KEY")
    if not api_key:
        raise SystemExit("TASHU_API_KEY가 .env에 없습니다.")

    response = requests.get(API_URL, headers={"api-token": api_key}, timeout=30)
    if response.status_code != 200:
        raise SystemExit(f"API 호출 실패: {response.status_code} {response.text[:200]}")

    df = pd.DataFrame(response.json()["results"])
    print("타슈 대여소 api 호출 완료!")

    df = df.iloc[:, [0, 1, 3, 4, 5, 7]]

    # api에서 (x,y) -> (위도, 경도)의 순서로 되어 있음 (주의)
    df.rename(
        columns={'id': 'station_id', 'name': 'station_name', 'name_cn': 'parking_info',
                 'x_pos': 'lat', 'y_pos': 'lon',
                 'parking_count': 'stock'},
        inplace=True)

    ensure_output_dirs()
    df.to_csv(out_file_path.format(now=config.now), encoding='utf-8', index=False)
    print(f"\n{out_file_path.format(now=config.now)} 가 저장되었습니다.")


if __name__ == '__main__':
    main()
