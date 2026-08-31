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
    # top_[숫자]로 좁힌다 — `top*.csv`로 두면 top_center*.csv 같은 다른 산출물까지
    # 잡아 mtime이 최신인 엉뚱한 파일을 읽는다.
    "pick_drop": ("ILP/후보", "top_[0-9]*.csv"),
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
    # duration도 마찬가지다 — 폴백은 시간대를 거를 수 없어서, 없는 시간대를
    # 물었는데 **전 시간대가 섞인 표**를 200으로 돌려주고 있었다(실측:
    # /api/metrics?duration=bogus가 29KB를 반환). 시간대가 다르면 수요 구조가
    # 반대라 섞인 값은 틀린 답이다.
    if run_label is None and duration is None and table in CSV_FALLBACK:
        subdir, pattern = CSV_FALLBACK[table]
        path = catalog.latest_file(subdir, pattern)
        if path is not None:
            return pd.read_csv(path, encoding="utf-8", low_memory=False), "csv"

    return pd.DataFrame(), "none"


def kpi(run_label: Optional[str] = None, duration: Optional[str] = None) -> pd.DataFrame:
    """실행별 성과 지표 (docs/분석/KPI.md의 kpi_summary)."""
    try:
        with db.session() as conn:
            return db.load_kpi(conn, run_label=run_label, duration=duration)
    except Exception as err:
        print(f"[경고] KPI 조회 실패: {type(err).__name__}: {err}")
        return pd.DataFrame()


def stockout_calibration(day_type: str = "weekday") -> list:
    """관측으로 잰 결품 보정 계수. 표가 없으면 빈 목록 (수정안 37).

    결품 시간은 순수요로 **복원**한 값이라 재고 0에서 잘려 실제보다 낮게 나온다.
    그 격차를 관측과 맞대어 재 둔 것이 이 계수다. **수집이 멈춰도 표에 남는다.**
    """
    with db.session() as conn:
        try:
            frame = db.latest_stockout_calibration(conn)
        except Exception:
            # 표가 아직 없는 옛 DB. 화면은 떠야 하므로 조용히 비운다.
            return []
    return [] if frame.empty else frame.to_dict("records")


def vehicle_workload() -> pd.DataFrame:
    """차량별 누적 작업량(로테이션 형평성 확인용)."""
    try:
        with db.session() as conn:
            db.ensure_fleet(conn)
            return db.vehicle_workload(conn)
    except Exception as err:
        print(f"[경고] 차량 부하 조회 실패: {type(err).__name__}: {err}")
        return pd.DataFrame()


def vehicle_assignments(vehicle_id: Optional[str] = None,
                        run_label: Optional[str] = None) -> pd.DataFrame:
    """회차별 차량 배정 이력."""
    try:
        with db.session() as conn:
            return db.assignment_history(conn, vehicle_id=vehicle_id, run_label=run_label)
    except Exception as err:
        print(f"[경고] 배정 이력 조회 실패: {type(err).__name__}: {err}")
        return pd.DataFrame()


def run_labels() -> pd.DataFrame:
    """DB에 기록된 실행 이력(최신순). DB가 없으면 빈 DataFrame."""
    try:
        with db.session() as conn:
            return db.list_runs(conn)
    except Exception as err:
        print(f"[경고] 실행 이력 조회 실패: {type(err).__name__}: {err}")
        return pd.DataFrame()


def plan_targets() -> pd.DataFrame:
    """작업지시서를 만들 수 있는 (실행, 회차) 목록 — `vrp_plan`에 경로가 있는 것.

    최근 실행이 앞에 오게 `runs.created_at`으로 정렬한다. `run_label`은 사람이
    붙이는 이름이라 사전순으로 줄 세우면 시간 순서와 어긋난다.
    """
    try:
        with db.session() as conn:
            return pd.read_sql(
                "SELECT v.run_label, v.duration,"
                "       COUNT(DISTINCT v.cluster) AS clusters,"
                "       MAX(r.created_at) AS created_at"
                "  FROM vrp_plan v"
                "  LEFT JOIN runs r"
                "    ON r.run_label = v.run_label AND r.duration = v.duration"
                " GROUP BY v.run_label, v.duration"
                " ORDER BY created_at DESC, v.run_label DESC, v.duration ASC",
                conn)
    except Exception as err:
        print(f"[경고] 경로 목록 조회 실패: {type(err).__name__}: {err}")
        return pd.DataFrame()


def records(frame: pd.DataFrame) -> list:
    """JSON 응답용 레코드 목록. NaN은 None으로 바꾼다."""
    if frame.empty:
        return []
    return frame.astype(object).where(pd.notna(frame), None).to_dict(orient="records")
