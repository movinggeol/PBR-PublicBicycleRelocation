"""성과 지표 화면의 데이터 조립.

화면은 [KPI.md](../docs/분석/KPI.md) 5장의 3단 구성을 따른다.

1. 헤드라인 몇 개 — 이번 실행 값 + **같은 조건의 앞선 실행** 대비 증감
   (`comparable_previous` — 종류·요일 구분·회차 구성이 같은 것, 1.26.284)
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

from project_config import DURATION_LABELS
from webapp import store

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
    "최장 작업": "가장 오래 걸린 차량. 시간 예산을 넘기면 겨냥한 시간대를 지나"
                " 효과가 줄 수 있습니다(집행은 됩니다).",
    "평균 개선률": "계획을 얼마나 지켰나. z가 다른 실행끼리는 비교하지 마세요.",
    "이동 시간 비중": "소요시간 중 이동이 차지하는 몫. 낮을수록 작업에 쓴 것입니다.",
    "공차 이동 비율": "빈 차로 달린 거리 몫. 차고지 왕복이 여기 들어갑니다.",
}

WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")

# 순수요 집계는 4만 행을 훑는다. 화면을 열 때마다 다시 하면 1초씩 잡아먹으므로
# 기간별로 한 번만 계산해 둔다.
#
# ⚠️ 예전 주석은 *"파이프라인이 다시 돌면 서버를 재시작한다"* 고 전제했는데,
#    웹 실행 폼으로 돌리면 서버는 그대로다 — 새 달의 순수요를 계산해도 이
#    캐시는 옛 값을 냈다. 그래서 `jobs`의 완료 훅이 `reset_cache()`를 부른다
#    (등록은 app.py, 1.26.262).
_heatmap_cache: dict = {}


def reset_cache() -> None:
    """파이프라인이 끝났을 때·테스트에서 캐시를 비운다."""
    _heatmap_cache.clear()


def _weighted(frame: pd.DataFrame, column: str,
              weight: Optional[str]) -> Optional[float]:
    """회차별 값을 실행 1개 값으로 요약한다.

    가중치를 주면 가중평균이다 — 회차마다 대여소 수가 달라서 단순평균을 내면
    작은 회차가 큰 회차와 같은 무게를 갖는다.
    """
    if column not in frame or frame[column].isna().all():
        return None
    # 🔴 분자와 분모를 **같은 행**에서 센다 (1.26.271). 예전에는 분자만 NaN 행을
    # 건너뛰고 분모는 그 행의 가중치를 더해, 회차 하나가 NULL이면 값이 그만큼
    # 끌려 내려갔다(값 [1.0, NaN]·가중치 [10, 10] → 0.5). `db.KPI_FIELDS`는
    # 계산 못 한 지표를 NULL로 두므로 언제든 생길 수 있는 조건이다.
    part = frame.loc[frame[column].notna()]
    if weight is None or weight not in part:
        return float(part[column].mean())
    weights = part[weight].fillna(0)
    if weights.sum() <= 0:
        return float(part[column].mean())
    return float((part[column] * weights).sum() / weights.sum())


# 첫 화면(`app.py`의 헤드라인)도 이 함수를 쓴다 — 같은 가중평균을 두 곳에
# 따로 적으면 가중치 합 0일 때처럼 가장자리에서 답이 갈린다(1.26.271).
weighted_mean = _weighted


def duration_name(duration, short: bool = False) -> str:
    """회차 코드의 사람 이름 — `project_config.DURATION_LABELS` 한 벌 (1.26.284).

    `/kpi`의 표·산점도(점 이름표·풍선·표 보기)·수요 예측·보정 계수가 같은 이름을 쓴다.
    1.26.283이 칩·지시서·`/vehicles`를 이름으로 바꾸고 `/kpi`만 코드로 남겨, 한 화면에서
    `_05_10`과 '05~10시 (출근)'이 섞일 수 있었다. 모르는 코드는 짐작하지 않고 그대로 둔다.

    `short=True`는 **괄호 앞까지**('05~10시') — 산점도 점 옆 글자만 쓴다(1.26.284 검토).
    전체 이름('05~10시 (출근)')을 점 옆에 붙였더니 글자가 넓어 빈자리를 찾은 점이 72개 중
    17개에서 9개로 줄었다(실측) — 괄호 앞까지면 코드와 같은 17개다. 풍선·표 보기는 전체
    이름이다. 짧은 형태를 이 한 곳에서만 만든다 — 템플릿마다 자르면 규칙이 갈린다.
    """
    if duration is None or (isinstance(duration, float) and pd.isna(duration)):
        return ""
    name = DURATION_LABELS.get(str(duration), str(duration))
    return name.split(" (")[0] if short else name


def stockout_cut_pct(before, after) -> Optional[float]:
    """재배치 **전** 대비 결품이 준 비율(%) — 첫 화면과 `/kpi`가 같은 값을 쓴다 (1.26.284).

    두 값을 먼저 화면에 찍히는 자릿수(소수 둘째)로 반올림하고 잰다 — 첫 화면이 그렇게
    셌다. `/kpi`가 반올림 전 값으로 따로 세면 같은 실행이 72.9%와 73.0%로 갈린다.
    재배치 전 값이 없거나 0이면 `None`(지어내지 않는다). **음수면 결품이 늘었다는 뜻**이다 —
    화면은 부호에 따라 '감소'·'증가'로 말한다(예전 `/kpi` 타일은 늘어도 초록 ▼였다).
    """
    if before is None or after is None or pd.isna(before) or pd.isna(after):
        return None
    before, after = round(float(before), 2), round(float(after), 2)
    if before == 0:
        return None
    return round((1 - after / before) * 100, 1)


# 증감의 짝을 가르는 조건. `period`(수요 기간)는 `run_conditions`가 함께 싣지만 **여기에는
# 넣지 않는다** — 반박자 조정안(C05)이다. 달이 다른 실행끼리도 견주되, 히어로가 두 기간을
# 적어 읽는 사람이 보게 한다(1.26.284 검토 — 처음에는 적지 않아 '같은 조건'이 기간까지
# 같다는 뜻으로 읽혔다. 실측 `brokenmix4-2506`(25년 06월)의 짝이 `brokenmix4-2603`(26년 03월)).
COMPARE_KEYS = ("kind", "day_type", "durations")


def run_conditions(rows: pd.DataFrame, runs: pd.DataFrame,
                   labels: Optional[list] = None) -> dict:
    """실행마다 **견줄 수 있게 하는 조건** — `{라벨: {kind, day_type, durations, period}}` (1.26.284).

    - `kind`: `runs.kind`(못박힌 종류 — `db.list_runs`가 비면 라벨 짐작으로 채워 둔다), 행이
      없으면 `store.classify_run_label` 짐작 — `store.run_kind()`와 같은 규칙이다.
    - `day_type`: `runs.day_type`. 기록이 없으면 `None`(모름) — 짐작하지 않는다.
    - `durations`: 이 실행의 지표 행에 있는 회차, 하루 순서(`store.duration_rank`).
    - `period`: `runs.period`(수요 기간) — **표시용**이다. 짝을 가르는 데는 쓰지 않는다(`COMPARE_KEYS`).

    ⚠️ 표를 **한 번에** 만든다 (1.26.284 검토). 처음에는 실행 하나마다 `rows`·`runs` 전체에
    불리언 필터를 세 번씩 걸어, 짝을 찾으며 앞선 실행을 훑으면 O(실행 수 × 행 수)였다 —
    실측 `/kpi` 한 번에 약 110ms(페이지 시간의 +75%), 실행이 하나 늘 때마다 3~4ms씩 늘었다.
    `labels`를 주지 않으면 `rows`에 있는 모든 실행이다.
    """
    if labels is None:
        labels = (list(dict.fromkeys(rows["run_label"].astype(str)))
                  if not rows.empty and "run_label" in rows else [])

    def column(name: str) -> dict:
        if runs.empty or "run_label" not in runs or name not in runs:
            return {}
        return {str(label): str(value) for label, value in zip(runs["run_label"], runs[name])
                if pd.notna(value) and str(value)}

    kinds, day_types, periods = column("kind"), column("day_type"), column("period")
    durations: dict = {}
    if not rows.empty and "duration" in rows and "run_label" in rows:
        present = rows.loc[rows["duration"].notna(), ["run_label", "duration"]]
        for label, codes in present.groupby(present["run_label"].astype(str))["duration"]:
            durations[label] = tuple(sorted(set(codes.astype(str)),
                                            key=lambda d: (store.duration_rank(d), d)))
    return {str(label): {"kind": kinds.get(str(label)) or store.classify_run_label(str(label)),
                         "day_type": day_types.get(str(label)),
                         "durations": durations.get(str(label), ()),
                         "period": periods.get(str(label))}
            for label in labels}


def run_condition(rows: pd.DataFrame, runs: pd.DataFrame, label: str) -> dict:
    """실행 하나의 조건 — `run_conditions`의 한 칸(같은 규칙 한 벌)."""
    return run_conditions(rows, runs, [label])[str(label)]


def _compare_key(condition: dict) -> tuple:
    return tuple(condition[key] for key in COMPARE_KEYS)


def comparable_previous(rows: pd.DataFrame, runs: pd.DataFrame, label: str) -> dict:
    """헤드라인 증감을 견줄 **같은 조건의 앞선 실행** (1.26.284).

    예전 `/kpi`는 '직전'을 기록 시각만으로 골랐다 — 휴일 계획을 평일 계획과 견줘
    *"시간 예산 준수 ▼18%"* 를 붉게 띄웠다(실측). 평일과 휴일은 이 프로젝트가 **절대 섞지
    않는** 축이고, 실험이 계획 뒤에 돌면 실험과도 견줬다. 거꾸로 칩으로 한 실행을 고르면
    행이 그 실행뿐이라 증감이 통째로 사라졌다.

    고르는 규칙 — `rows`는 **전체** 지표(필터 전), 순서는 `store.kpi_run_order()` 한 벌:
      1. `label`보다 앞선(오래된) 실행 가운데
      2. 종류(`kind`)가 같고
      3. 요일 구분(`day_type`)이 같으며 **기록이 있고** — 모르면 짝짓지 않는다
      4. 회차 구성(`durations`)이 **같은** 것 — 헤드라인은 회차 가중평균이라, 네 회차 평균을
         한 회차 값과 견주면 그것도 조건 섞기다(실측: 네 회차 80% 대 한 회차 0%)
      5. 그중 가장 최근 것.

    **수요 기간(`period`)은 가르지 않는다**(`COMPARE_KEYS`) — 두 실행의 기간은 조건에 실어
    히어로가 적는다. 달이 다르면 증감에 달의 차이도 섞인다는 것을 읽는 사람이 보게 한다.

    반환: `{"label": 비교 실행 또는 None, "condition": 이 실행의 조건,
    "baseline_condition": 짝의 조건 또는 None, "reason": None | "day_type_unknown" | "no_match"}`.
    짝이 없으면 화면은 증감을 비우고 **왜 비었는지** 말한다(점검 기록 4장 — 판정할 수 없으면
    모른다고 한다). 조건표는 한 번에 만든다(`run_conditions` — 실행마다 다시 거르지 않는다).
    """
    created = {}
    if not runs.empty and "created_at" in runs:
        created = dict(zip(runs["run_label"], runs["created_at"]))
    order = store.kpi_run_order(rows, created)
    conditions = run_conditions(rows, runs, list(dict.fromkeys([label, *order])))
    condition = conditions[str(label)]
    result = {"label": None, "condition": condition, "baseline_condition": None, "reason": None}
    if condition["day_type"] is None:
        result["reason"] = "day_type_unknown"
        return result
    key = _compare_key(condition)
    later = order[order.index(label) + 1:] if label in order else []
    for other in later:
        if _compare_key(conditions[str(other)]) == key:
            result["label"] = other
            result["baseline_condition"] = conditions[str(other)]
            return result
    result["reason"] = "no_match"
    return result


def lower_is_better(column: str) -> bool:
    """그 지표가 **낮을수록 좋은가** — `TRENDS`의 마지막 칸 한 벌 (1.26.284 검토).

    `/kpi` 타일의 증감 색(good/bad)이 쓴다. 처음에는 결품 타일에 `true`를 템플릿에 박고 네
    카드는 라우트에 `False`를 적어, 방향을 `TRENDS`와 두 곳이 따로 쥐었다. `TRENDS`에 없는
    지표(한 번에 닿는 범위·시간 예산 준수)는 높을수록 좋은 비율이라 `False`다.
    """
    return next((flag for name, *_rest, flag in TRENDS if name == column), False)


def _runs_in_order(rows: pd.DataFrame) -> list:
    """실행 라벨을 **오래된 것부터**. 라벨은 사람이 붙인 이름이라 사전순은 뜻이 없다.

    `/kpi` 표·헤드라인과 같은 `store.kpi_run_order()`를 거꾸로 쓴다(1.26.283 검토) —
    예전에는 여기만 `computed_at`으로 따로 세워, 같은 라벨을 다시 돌리면 그래프의
    맨 오른쪽 점과 표의 첫 행이 서로 다른 실행이 될 수 있었다.
    """
    if rows.empty:
        return []
    return list(reversed(store.kpi_run_order(rows)))


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

        # 점 이름표·풍선·표 보기는 회차의 **사람 이름**이다(1.26.284) — 같은 화면의 표·칩과
        # 표기를 맞춘다. 점 옆 글자만 괄호 앞까지(`short`) — 전체 이름은 넓어서 이름표를 붙인
        # 점이 17 → 9개로 줄었다(1.26.284 검토 실측). 코드(`duration`)는 따로 싣는다(정렬·주소).
        name = duration_name(row["duration"])
        points.append({
            "x": float(distance), "y": y,
            "label": duration_name(row["duration"], short=True),
            "name": name,
            "tip": f"{row['run_label']} · {name} · "
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
    table = [(f"{p['run_label']} · {p['name']}", p["x"], p["y"])
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
    rows = store.backtest(day_type)
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
            "duration_name": duration_name(duration),
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
            "title": f"{duration_name(duration)} 예측 오차",
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
    # 열쇠에 DB 파일의 수정 시각을 넣는다 — 완료 훅(1.26.262)은 웹 작업만 잡아서
    # CLI로 순수요를 다시 계산하면 옛 그림이 영영 남았다(1.26.273).
    key = (period, store.db_stamp())
    if key in _heatmap_cache:
        return _heatmap_cache[key]

    empty = {"rows": [], "cols": [], "matrix": [], "scale": 0.0, "period": period}
    frame = store.net_demand(period)
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
    _heatmap_cache.clear()          # 지문이 바뀐 옛 열쇠를 쌓아 두지 않는다
    _heatmap_cache[key] = result
    return result
