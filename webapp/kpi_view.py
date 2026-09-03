"""성과 지표 화면의 데이터 조립.

화면은 [KPI.md](../docs/분석/KPI.md) 5장의 3단 구성을 따른다.

1. 헤드라인 몇 개 — 이번 실행 값 + 직전 실행 대비 증감
2. **추세** — 실행 축의 꺾은선. 조건을 바꿨을 때 효과를 눈으로 본다
3. 실행 비교 표

여기에 두 가지를 더 붙였다.

- **효과·비용 산점도** — "개선률만 올리면 소요시간이 늘어난다"는 경고를 그림
  하나로 대체한다. 지표 하나만 크게 띄우지 말라는 KPI.md의 당부가 이것이다.
- **수요 구조 히트맵** — 성과가 아니라 자료 자체를 보여준다. 평일과 휴일이 왜
  섞이면 안 되는지, 왜 시간대를 나누는지가 한 장에 담긴다.

조립만 하고 그리지 않는다 — SVG는 `charts.py`가 만든다.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

import db

# 실행 축 꺾은선으로 낼 지표. (컬럼, 제목, 단위, 배수, 가중치 컬럼, 낮은 쪽이 좋은가)
#
# **축이 둘인 그래프를 만들지 않는다.** 단위가 다른 지표를 한 판에 겹치면 두 축의
# 정렬이 임의가 되고, 없는 상관관계가 있는 것처럼 보인다. 그래서 지표마다 작은
# 그래프를 따로 낸다.
TRENDS = (
    ("stockout_hours_after", "결품 시간", "h", 1, "stations", True),
    # 결품의 **반대쪽**이다. 결품만 보면 "채우면 좋다"가 되는데, 채워서 포화가
    # 늘면 반납이 막힌다 — 둘을 나란히 놔야 그 맞바꿈이 보인다(1.26.101).
    ("saturation_hours_after", "포화 시간", "h", 1, "stations", True),
    ("improvement_per_km", "km당 개선", "대", 1, "total_distance_km", False),
    ("max_cluster_minutes", "최장 작업", "분", 1, None, True),
    ("avg_improvement_rate", "평균 개선률", "%", 100, "stations", False),
    ("travel_time_ratio", "이동 시간 비중", "%", 100, "stations", True),
    ("empty_distance_ratio", "공차 이동 비율", "%", 100, "total_distance_km", True),
)

# 지표마다 한 줄 설명. 그래프만 보고 "이게 오르면 좋은 건가"를 묻지 않게 한다.
TREND_HINTS = {
    "결품 시간": "자전거가 없어 못 빌리는 시간(대여소·일 평균). 낮을수록 좋습니다.",
    "포화 시간": "거치대가 꽉 차 반납을 못 받는 시간. 결품과 반대 방향이라"
                " 함께 봐야 합니다 — 채우면 결품은 줄지만 포화는 늘 수 있습니다.",
    "km당 개선": "1km 움직여 줄인 불균형 대수. 투입 대비 효율입니다.",
    "최장 작업": "가장 오래 걸린 차량. 시간 예산을 넘기면 계획이 헛돕니다.",
    "평균 개선률": "계획을 얼마나 지켰나. z가 다른 실행끼리는 비교하지 마세요.",
    "이동 시간 비중": "소요시간 중 이동이 차지하는 몫. 낮을수록 작업에 쓴 것입니다.",
    "공차 이동 비율": "빈 차로 달린 거리 몫. 차고지 왕복이 여기 들어갑니다.",
}

WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")

# 순수요 집계는 4만 행을 훑는다. 화면을 열 때마다 다시 하면 1초씩 잡아먹으므로
# 기간별로 한 번만 계산해 둔다(파이프라인이 다시 돌면 서버를 재시작한다).
_heatmap_cache: dict = {}


def _weighted(frame: pd.DataFrame, column: str,
              weight: Optional[str]) -> Optional[float]:
    """회차별 값을 실행 1개 값으로 요약한다.

    가중치를 주면 가중평균이다 — 회차마다 대여소 수가 달라서 단순평균을 내면
    작은 회차가 큰 회차와 같은 무게를 갖는다.
    """
    if column not in frame or frame[column].isna().all():
        return None
    if weight is None or weight not in frame:
        return float(frame[column].mean())
    weights = frame[weight].fillna(0)
    if weights.sum() <= 0:
        return float(frame[column].mean())
    return float((frame[column] * weights).sum() / weights.sum())


def _runs_in_order(rows: pd.DataFrame) -> list:
    """실행 라벨을 **오래된 것부터**. 라벨은 사람이 붙인 이름이라 사전순은 뜻이 없다."""
    if rows.empty:
        return []
    order = "computed_at" if "computed_at" in rows else "run_label"
    seen = rows.sort_values(order)["run_label"].drop_duplicates().tolist()
    return seen


def trends(rows: pd.DataFrame, limit: int = 12) -> list:
    """실행 축 꺾은선용 데이터. 값이 2건 미만인 지표는 아예 내지 않는다."""
    labels = _runs_in_order(rows)[-limit:]
    if len(labels) < 2:
        return []

    series = []
    for column, title, unit, factor, weight, lower_better in TRENDS:
        values = []
        for label in labels:
            value = _weighted(rows[rows["run_label"] == label], column, weight)
            values.append(None if value is None else value * factor)
        if sum(v is not None for v in values) < 2:
            continue
        series.append({
            "title": title, "unit": unit, "labels": labels, "values": values,
            "hint": TREND_HINTS.get(title, ""),
            "lower_is_better": lower_better,
        })
    return series


def cost_benefit(rows: pd.DataFrame) -> dict:
    """효과·비용 산점도. 점 하나가 실행·회차 하나다.

    y는 **결품 감소**를 먼저 쓴다 — 목표 재고를 분모로 삼지 않아 z가 다른 실행끼리도
    견줄 수 있는 유일한 축이다. 결품을 못 잰 기록만 있으면 개선률로 물러서고,
    그 사실을 부제로 밝힌다(두 뜻을 한 그래프에 섞지 않는다).
    """
    if rows.empty:
        return {"points": [], "y_label": "", "y_unit": "",
                "table": [], "note": ""}

    has_stockout = ("stockout_hours_before" in rows
                    and not rows["stockout_hours_before"].isna().all())

    points, skipped = [], 0
    for _, row in rows.iterrows():
        distance = row.get("total_distance_km")
        if distance is None or pd.isna(distance):
            skipped += 1
            continue

        if has_stockout:
            before, after = row.get("stockout_hours_before"), row.get("stockout_hours_after")
            if pd.isna(before) or pd.isna(after):
                skipped += 1
                continue
            y = float(before) - float(after)
            y_text = f"결품 {y:.2f}h 감소"
        else:
            rate = row.get("avg_improvement_rate")
            if rate is None or pd.isna(rate):
                skipped += 1
                continue
            y = float(rate) * 100
            y_text = f"개선률 {y:.0f}%"

        points.append({
            "x": float(distance), "y": y,
            "label": str(row["duration"]).lstrip("_"),
            "tip": f"{row['run_label']} {row['duration']} · "
                   f"이동 {float(distance):.0f}km · {y_text}",
            # 표 보기가 쓸 값. 점 옆 글자는 회차뿐이라 같은 회차가 여러 번
            # 나오면 어느 실행인지 구분되지 않는다 — 표에는 실행까지 적는다.
            "run_label": str(row["run_label"]),
            "duration": str(row["duration"]),
        })

    note = ""
    if skipped:
        note = f"값이 없는 {skipped}건은 빼고 그렸습니다."
    # 커서만으로 값을 읽게 두지 않는다 — 다른 그래프는 전부 표 보기를 함께
    # 내는데 이 산점도만 없었다(1.26.107). 인쇄·터치에서는 풍선이 안 뜬다.
    table = [(f"{p['run_label']} {p['duration']}", p["x"], p["y"])
             for p in sorted(points, key=lambda q: q["y"], reverse=True)]
    return {
        "points": points,
        "y_label": "결품 감소 (시간)" if has_stockout else "평균 개선률 (%)",
        "y_unit": "시간" if has_stockout else "%",
        "table": table,
        "note": note,
    }


def forecast_accuracy(day_type: str = "weekday") -> dict:
    """수요 예측이 얼마나 맞는가 (docs/분석/KPI.md E장).

    `tools/backtest_demand.py`가 쌓아 둔 월쌍 백테스트를 시간대별로 요약한다.
    **파이프라인 실행과 무관한 기록이다** — 한 달로 만든 mu가 다음 달을 맞히는지
    보는 것이라 실행 라벨이 없다.

    `mu`가 틀리면 그 위의 모든 지표가 허수이므로, 이 절이 성과 지표의 전제다.
    기준선(늘 0이라고 예측)을 못 이기는 시간대가 있으면 그 시간대의 목표 재고는
    근거가 약하다는 뜻이다.
    """
    try:
        with db.session() as conn:
            rows = db.load_backtest(conn, day_type=day_type)
    except Exception as err:
        print(f"[경고] 백테스트 조회 실패: {type(err).__name__}: {err}")
        return {"summary": [], "series": []}
    if rows.empty:
        return {"summary": [], "series": []}

    summary, series = [], []
    for duration, group in rows.groupby("duration"):
        mae = float(group["mae"].mean())
        # 기준선은 둘 중 **더 낮은 쪽**과 겨룬다 — 쉬운 기준선만 골라 이겼다고 하면 안 된다.
        baseline = min(float(group["mae_zero"].mean()),
                       float(group["mae_global"].mean()))
        summary.append({
            "duration": duration,
            "pairs": int(len(group)),
            "mae": mae,
            "baseline": baseline,
            "gain": (1 - mae / baseline) * 100 if baseline else None,
            "coverage": float(group["coverage"].mean()) * 100,
            "bias": float(group["bias"].mean()),
            "z_for_95": float(group["z_for_95"].mean()),
        })
        ordered = group.sort_values("test_period")
        series.append({
            "title": f"{duration} 예측 오차",
            "unit": "대",
            "labels": ordered["test_period"].tolist(),
            "values": [float(v) for v in ordered["mae"]],
            "hint": "한 달로 만든 mu가 다음 달을 얼마나 틀렸나(MAE). 낮을수록 좋습니다.",
            "lower_is_better": True,
        })

    return {"summary": summary, "series": series, "day_type": day_type}


def demand_heatmap(period: str) -> dict:
    """요일 × 시간 순수요 히트맵.

    도시 전체 순수요를 하루·시간 단위로 더한 뒤 요일별 평균을 낸다.
    **요일이 행이므로 평일과 휴일이 섞이지 않는다** — 이 프로젝트가 둘을 절대
    섞지 않는 이유가 바로 이 그림에 보인다(같은 시간에 부호가 반대다).

    대여소별로 쪼개지 않는 이유는 한 칸이 대여소 하나짜리 표본이 되어 잡음만
    커지기 때문이다(계절 배율을 도시 전체로 구하는 것과 같은 이유).
    """
    if period in _heatmap_cache:
        return _heatmap_cache[period]

    empty = {"rows": [], "cols": [], "matrix": [], "scale": 0.0, "period": period}
    try:
        with db.session() as conn:
            frame = db.load_frame(conn, "net_demand", period=period)
    except Exception as err:
        print(f"[경고] 순수요 조회 실패: {type(err).__name__}: {err}")
        return empty
    if frame.empty or "date" not in frame:
        return empty

    hours = [c for c in frame.columns if c.startswith("net_")]
    if not hours:
        return empty

    daily = frame.groupby("date")[hours].sum()          # 하루 × 시간 (도시 전체)
    weekday = pd.to_datetime(daily.index).dayofweek
    by_weekday = daily.groupby(weekday).mean()

    matrix = [[float(by_weekday.loc[w, h]) if w in by_weekday.index else None
               for h in hours] for w in range(7)]
    values = [v for row in matrix for v in row if v is not None]

    result = {
        "rows": list(WEEKDAYS),
        "cols": [h.replace("net_", "") for h in hours],
        "matrix": matrix,
        "scale": max((abs(v) for v in values), default=0.0),
        "period": period,
        "days": int(len(daily)),
    }
    _heatmap_cache[period] = result
    return result
