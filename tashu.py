"""TASHU Open API 클라이언트.

**수집(step0)과 웹의 실시간 재고 대조가 같은 코드를 쓴다.** 두 곳이 각자
호출하면 컬럼 이름·좌표 뒤집기 같은 규약이 갈리고, 그러면 화면이 보여주는
'현재 재고'와 계획이 쓴 '재고'가 조용히 다른 것을 가리키게 된다.

여기서는 **받아 오기만 한다.** 파일·DB에 남기는 것은 step0_collect/tashu_api.py다.
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent

API_URL = "https://bikeapp.tashu.or.kr:50041/v1/openapi/station"

# API가 돌려주는 이름 → 이 저장소의 컬럼명.
# ⚠️ x_pos가 위도, y_pos가 경도다 (API에서 뒤집혀 있다).
COLUMN_MAP = {
    "id": "station_id",
    "name": "station_name",
    "name_cn": "parking_info",
    "x_pos": "lat",
    "y_pos": "lon",
    "parking_count": "stock",
}

COLUMNS = ("station_id", "station_name", "parking_info", "lat", "lon", "stock")


class TashuError(RuntimeError):
    """API 키가 없거나 호출이 실패했다. 부르는 쪽이 사람에게 보여줄 문구를 담는다."""


def fetch_stations(timeout: float = 30) -> pd.DataFrame:
    """대여소별 **현재** 재고를 받아 온다.

    반환 컬럼: station_id, station_name, parking_info, lat, lon, stock

    실패하면 TashuError를 올린다 — 웹 요청 안에서 부르므로 SystemExit로 죽이면
    서버가 500만 뱉고 원인을 못 알린다.
    """
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.getenv("TASHU_API_KEY")
    if not api_key:
        raise TashuError("TASHU_API_KEY가 .env에 없습니다.")

    try:
        response = requests.get(API_URL, headers={"api-token": api_key}, timeout=timeout)
    except requests.RequestException as err:
        raise TashuError(f"타슈 API에 연결하지 못했습니다: {type(err).__name__}") from err

    if response.status_code != 200:
        raise TashuError(f"타슈 API 호출 실패: {response.status_code} {response.text[:200]}")

    try:
        rows = response.json()["results"]
    except (ValueError, KeyError, TypeError) as err:
        raise TashuError("타슈 API 응답 형식이 예상과 다릅니다.") from err

    frame = pd.DataFrame(rows)
    missing = set(COLUMN_MAP) - set(frame.columns)
    if missing:
        raise TashuError(f"타슈 API 응답에 필요한 항목이 없습니다: {sorted(missing)}")

    frame = frame[list(COLUMN_MAP)].rename(columns=COLUMN_MAP)
    frame["stock"] = pd.to_numeric(frame["stock"], errors="coerce").fillna(0).astype(int)
    return frame
