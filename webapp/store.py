"""산출물 조회 계층 — DB 우선, 없으면 CSV 폴백 (DB_PLAN 3단계).

웹 API는 지금까지 `catalog.latest_file()`로 **파일 수정시각이 가장 최근인 것**을
최신으로 삼았다. 파일을 복사하거나 다시 저장하면 순서가 뒤바뀌는 휴리스틱이었다.
이제는 DB의 `run_label`을 기준으로 조회하고, 특정 실행분도 지정할 수 있다.

CSV 폴백은 **이중 기록 이전에 만들어진 산출물**을 위한 전환기 장치다.
CSV 기록을 걷어내는 시점(DB_PLAN 마지막 단계)에 함께 제거한다.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

import db
from webapp import catalog

# 테이블 -> CSV 폴백 위치 (pp_data 기준 폴더, glob 패턴)
CSV_FALLBACK = {
    "pick_drop": ("ILP/후보", "top*.csv"),
    "ilp_plan": ("ILP", "ILP_plan*.csv"),
    "vrp_plan": ("VRP", "VRP_plan*.csv"),
    "metrics": ("성능 지표", "verification*.csv"),
    "route_summary": ("성능 지표", "route_summary*.csv"),
}


def load(table: str, run_label: Optional[str] = None,
         duration: Optional[str] = None) -> Tuple[pd.DataFrame, str]:
    """산출물을 읽는다.

    반환: (DataFrame, 출처). 출처는 "db" | "csv" | "none".
    run_label을 생략하면 DB의 최신 실행분을 쓴다.
    """
    try:
        with db.session() as conn:
            frame = db.load_frame(conn, table, run_label=run_label, duration=duration)
        if not frame.empty:
            return frame, "db"
    except Exception as err:      # DB가 없거나 손상돼도 CSV로 응답할 수 있게 한다
        print(f"[경고] DB 조회 실패 ({table}): {type(err).__name__}: {err}")

    # 특정 실행을 콕 집어 요청한 경우에는 폴백하지 않는다
    # (CSV에는 어느 실행분인지 구분할 정보가 파일명 말고 없다).
    if run_label is None and table in CSV_FALLBACK:
        subdir, pattern = CSV_FALLBACK[table]
        path = catalog.latest_file(subdir, pattern)
        if path is not None:
            return pd.read_csv(path, encoding="utf-8", low_memory=False), "csv"

    return pd.DataFrame(), "none"


def run_labels() -> pd.DataFrame:
    """DB에 기록된 실행 이력(최신순). DB가 없으면 빈 DataFrame."""
    try:
        with db.session() as conn:
            return db.list_runs(conn)
    except Exception as err:
        print(f"[경고] 실행 이력 조회 실패: {type(err).__name__}: {err}")
        return pd.DataFrame()


def records(frame: pd.DataFrame) -> list:
    """JSON 응답용 레코드 목록. NaN은 None으로 바꾼다."""
    if frame.empty:
        return []
    return frame.astype(object).where(pd.notna(frame), None).to_dict(orient="records")
