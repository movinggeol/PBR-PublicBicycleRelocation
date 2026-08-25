"""목표 재고(target_qty)를 분위수 회귀로 직접 예측한다 (docs/분석/DEMAND_DISTRIBUTION.md).

기존 방식은 `target_qty = mu + z·sigma`다. `mu`와 `sigma`를 **따로 추정해 조립**
하므로 오차가 두 번 쌓이고, `z`는 손으로 고른 상수다.

여기서는 **다음 달 순수요의 95분위를 한 번에 예측**한다.

    지금:  target_qty = mu + z·sigma      (두 번 추정 + 상수 z)
    여기:  target_qty = Q95(피처)          (한 번 추정, z가 사라짐)

**왜 분위수인가**는 분포 문서에 정리돼 있다. 요지는 "순수요 꼬리가 두꺼워서"가
아니라 **지난달 통계로 이번 달을 맞히는 데서 오는 추정 오차** 때문이다. 지난달
피처로 이번 달 분위수를 맞히도록 훈련하면 그 오차 자체를 학습이 흡수한다.

설계 원칙 셋:

  · **피처는 직전 달 정보만** 쓴다. 예측 시점에 알 수 있는 것만 넣어야 백테스트가
    거짓말을 하지 않는다.
  · **대여소 ID를 넣지 않는다.** 표본이 8~19일뿐이라 외우면 과적합한다. 대신
    대여소 '특성'을 줘서 비슷한 대여소끼리 정보를 나누게 한다.
  · **모델이 없으면 조용히 기존 공식으로 돌아간다.** DB 실패가 파이프라인을 멈추지
    않는 것과 같은 원칙이다(DB_PLAN 2단계).

학습:  python tools/train_demand_model.py
평가:  python experiments/quantile_model_eval.py
"""
from __future__ import annotations

import pickle
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

import weather
from project_config import DATA_ROOT, DAY_TYPES, duration_hours, select_day_type

MODEL_PATH = DATA_ROOT / "models" / "target_quantile.pkl"

# 학습 분위수. target_qty의 설계 의도(95%를 덮는다)와 같은 값이다.
#
# 1.15.2에서 0.97로 올려 '채택'했다가 1.15.3에서 되돌렸다. 그 보정은 **계절 배율이
# 과대추정된 베이스라인**에 맞춰 고른 값이었고, 배율을 고치자 어느 분위수에서도
# 베이스라인을 이기지 못했다(0.93/0.95/0.97 전부 확인).
# 자세한 경위: docs/분석/DEMAND_DISTRIBUTION.md 5장.
TARGET_QUANTILE = 0.95

DURATIONS = ("_05_10", "_10_15", "_15_20", "_20_05")

# 계획 대상 **날짜**의 날씨. 위 피처들과 성격이 다르다 — 대여소마다가 아니라
# 날마다 달라지고, 도시 전체에 같은 값이 들어간다(관측소가 대전에 하나뿐이다).
#
# 근거는 docs/분석/WEATHER.md. **비 오는 날에 몰린 개선**이라는 것이 핵심이다 —
# 비 오는 날(전체의 10%) 예측 오차가 40% 줄고, 나머지 날엔 거의 그대로다.
WEATHER_FEATURES = ["rain", "rainy", "temp", "wind"]

# 직전 달 통계에서 뽑는 피처. **예측 시점에 알 수 있는 것만** 넣는다.
FEATURES = [
    "prev_mu", "prev_sigma", "prev_abs_mu", "prev_median",
    "prev_q90", "prev_max", "prev_min", "prev_zero_ratio", "prev_days",
    "warmup_ratio",
    "month", "duration_idx", "day_type_idx",
    *WEATHER_FEATURES,
]
CATEGORICAL = ["duration_idx", "day_type_idx"]

# 계절 배율을 곱해야 하는 피처 — '수준'을 나타내는 것들만이다.
# 비율(prev_zero_ratio)과 개수(prev_days)는 배율과 무관하다.
LEVEL_FEATURES = ("prev_mu", "prev_sigma", "prev_abs_mu", "prev_median",
                  "prev_q90", "prev_max", "prev_min")


def window_hours(duration: str) -> list:
    """시간대 -> 시각 목록. 규칙은 project_config 하나를 쓴다."""
    return duration_hours(duration)


def window_demand(net: pd.DataFrame, duration: str) -> pd.DataFrame:
    """날짜·대여소별 시간대 순수요 (calculate_target_qty와 같은 계산)."""
    columns = [f"net_{h:02d}" for h in window_hours(duration)]
    available = [c for c in columns if c in net.columns]
    if not available:
        return pd.DataFrame(columns=["station_id", "date", "demand"])
    return pd.DataFrame({
        "station_id": net["station_id"].values,
        "date": net["date"].values,
        "demand": net[available].sum(axis=1).values,
    })


def month_index(period: str) -> int:
    """'25년 11월' → 2511. 연속 여부 판정과 계절 피처에 쓴다."""
    year, month = period.split("년")
    return int(year.strip()) * 100 + int(month.replace("월", "").strip())


def summarize(demand: pd.DataFrame) -> pd.DataFrame:
    """한 달치 순수요를 대여소별 피처로 접는다."""
    grouped = demand.groupby("station_id")["demand"]
    frame = pd.DataFrame({
        "prev_mu": grouped.mean(),
        "prev_sigma": grouped.std().fillna(0.0),
        "prev_median": grouped.median(),
        "prev_q90": grouped.quantile(0.90),
        "prev_max": grouped.max(),
        "prev_min": grouped.min(),
        "prev_zero_ratio": grouped.apply(lambda s: float((s == 0).mean())),
        "prev_days": grouped.size().astype(float),
    })
    frame["prev_abs_mu"] = frame["prev_mu"].abs()
    return frame.reset_index()


def add_context(frame: pd.DataFrame, duration: str, day_type: str,
                month: int) -> pd.DataFrame:
    """시간대·요일 구분·계절을 피처로 붙인다(범주는 정수 인덱스로)."""
    frame = frame.copy()
    frame["duration_idx"] = DURATIONS.index(duration)
    frame["day_type_idx"] = DAY_TYPES.index(day_type)
    frame["month"] = month
    return frame


def build_features(train_demand: pd.DataFrame, duration: str, day_type: str,
                   month: int, ratio: Optional[float] = None) -> pd.DataFrame:
    """직전 달 순수요 → 모델 입력 피처.

    **학습과 예측이 반드시 이 함수를 거쳐야 한다.** 한쪽만 계절 배율을 곱하면
    모델이 배운 것과 다른 세계를 예측하게 된다(train/serve skew).

    `ratio`(계절 배율)를 주면 수준 피처에 곱하고 배율 자체도 피처로 넣는다 —
    보정이 얼마나 컸는지를 모델이 알아야 "많이 보정한 달은 더 불확실하다"를
    배울 수 있다. 배율은 호출부가 구한다(베이스라인과 **같은 값**을 써야 하므로).
    """
    features = summarize(train_demand)
    if ratio:
        for column in LEVEL_FEATURES:
            features[column] = features[column] * ratio

    features["warmup_ratio"] = ratio if ratio else 1.0
    return add_context(features, duration, day_type, month)


# ---------------- 날씨 (있으면 좋고 없어도 되는 입력) ----------------

_HOURLY = None                # 관측 자료는 한 번만 읽는다(학습이 수십 번 부른다)


def _hourly() -> pd.DataFrame:
    global _HOURLY
    if _HOURLY is None:
        _HOURLY = weather.load_hourly()
    return _HOURLY


def weather_frame(duration: str) -> pd.DataFrame:
    """날짜 → 그 창의 날씨 피처. 자료가 없으면 빈 표를 돌려준다."""
    folded = weather.window_frame(_hourly(), duration)
    if folded.empty:
        return pd.DataFrame(columns=["date", *WEATHER_FEATURES])
    folded["rainy"] = (folded["rain"] >= weather.RAIN_MM).astype(float)
    return folded[["date", *WEATHER_FEATURES]]


def add_weather(frame: pd.DataFrame, duration: str, date=None) -> pd.DataFrame:
    """(대여소, 날짜) 표에 그날의 날씨를 붙인다.

    **학습과 예측이 반드시 이 함수를 거쳐야 한다.** 다른 피처와 달리 날씨는 행마다
    날짜가 다른 학습 표와, 계획 대상 날짜 **하나뿐인** 예측 표가 모양이 다르다.
    `date`를 주면 그 하루의 값을 모든 행에 같이 넣는다 — 날씨는 도시 하나의 값이니
    맞는 처리다. 두 경로를 따로 만들면 학습이 본 것과 예측이 주는 것이 어긋난다.

    자료가 없거나 그 날짜가 자료 밖이면 **NaN으로 남긴다.** 0으로 채우면 안 된다 —
    '비가 안 왔다'와 '모른다'는 다르고, 0은 전자를 뜻한다.
    """
    frame = frame.copy()
    table = weather_frame(duration)

    if date is not None:
        stamp = pd.to_datetime(date).strftime("%Y-%m-%d")
        row = table[table["date"] == stamp]
        for column in WEATHER_FEATURES:
            frame[column] = float(row[column].iloc[0]) if not row.empty else np.nan
        return frame

    if "date" not in frame.columns or table.empty:
        for column in WEATHER_FEATURES:
            frame[column] = np.nan
        return frame

    key = pd.to_datetime(frame["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    joined = key.to_frame("date").merge(table, on="date", how="left")
    for column in WEATHER_FEATURES:
        frame[column] = joined[column].to_numpy()
    return frame


def season_ratio(train_demand: pd.DataFrame, recent: Optional[pd.DataFrame],
                 warmup_days: int) -> Optional[float]:
    """직전 달 통계와 계획 대상 달의 부분 실적으로 계절 배율을 구한다.

    **배율 계산의 유일한 진입점이다.** 백테스트와 파이프라인이 이 함수를 함께 써야
    측정이 실제 동작을 반영한다(예전에는 한쪽만 대여소를 걸러 값이 달랐다).
    """
    if warmup_days <= 0 or recent is None or recent.empty or train_demand.empty:
        return None
    stats = train_demand.groupby("station_id")["demand"].mean().rename("mu").reset_index()
    return warmup_ratio(stats, recent, warmup_days)


def training_frame(net_by_period: dict, day_types=DAY_TYPES,
                   durations=DURATIONS, warmup_days: int = 0) -> pd.DataFrame:
    """(직전 달 피처 → 이번 달 실제 순수요) 표를 만든다.

    한 행 = (대여소, 이번 달의 하루). 타깃은 그날의 실제 순수요다.
    **직전 달로만 피처를 만들기 때문에** 학습 자체가 표본 밖 상황을 재현한다.
    """
    periods = sorted(net_by_period, key=month_index)
    rows = []

    for train_period, test_period in zip(periods, periods[1:]):
        gap = month_index(test_period) - month_index(train_period)
        if gap != 1 and gap != 89:            # 12월 → 1월
            continue
        for day_type in day_types:
            train_net = select_day_type(net_by_period[train_period], "date", day_type)
            test_net = select_day_type(net_by_period[test_period], "date", day_type)
            if train_net.empty or test_net.empty:
                continue
            month = month_index(test_period) % 100

            for duration in durations:
                train_demand = window_demand(train_net, duration)
                test_demand = window_demand(test_net, duration)
                if train_demand.empty or test_demand.empty:
                    continue

                ratio = season_ratio(train_demand, test_demand, warmup_days)
                features = build_features(train_demand, duration, day_type,
                                          month, ratio)
                merged = test_demand.merge(features, on="station_id", how="inner")
                if not merged.empty:
                    # 날짜별 피처는 여기서 붙는다 — build_features는 대여소별 통계라
                    # 날짜 축이 없다(한 대여소 = 한 행).
                    rows.append(add_weather(merged, duration))

    if not rows:
        return pd.DataFrame(columns=["station_id", "date", "demand", *FEATURES])
    return pd.concat(rows, ignore_index=True)


# ---------------- 계절 수준 보정 (warmup) ----------------
# (build_features가 이 함수들을 쓴다 — 파이썬은 호출 시점에 이름을 찾으므로
#  정의 순서는 문제가 되지 않는다.)
#
# ML이 아니다. 지난달 통계에 **배율 하나**를 곱하는 것뿐이다.
# 계절이 도약하는 달(2월→3월 수요 1.5배)에는 지난달 평균이 구조적으로 낮게 나오고,
# z를 올리는 것으로는 대체되지 않는다(z=2.10에서도 전환 달 커버리지 90.4%).
# 근거: docs/분석/EXPERIMENTS.md 3장.

WARMUP_CLIP = (0.5, 2.0)      # 며칠치 잡음으로 배율이 과하게 튀는 것을 막는다

# 배율을 추정할 때 쓸 대여소의 최소 수요 크기(|mu|).
#
# **0으로 두면 안 된다.** 순수요가 0 근처인 대여소가 1,000곳 넘는데, 며칠치로
# 잰 |평균|은 참값이 0에 가까울수록 위쪽으로 치우친다(잡음의 절댓값이 더해지므로).
# 그런 대여소를 배율 계산에 넣으면 분자만 부풀어 **배율이 과대추정**된다.
# 실측: 전체로 재면 전 기간 MAE가 오히려 나빠졌다(기준선 대비 44.3% -> 42.8%).
# 파이프라인이 실제로 손대는 대여소(|rebal_qty| > 2)와 같은 기준을 쓴다.
WARMUP_MIN_DEMAND = 2.0


def warmup_ratio(stats: pd.DataFrame, recent: pd.DataFrame, days: int,
                 min_demand: float = WARMUP_MIN_DEMAND) -> Optional[float]:
    """계획 대상 달 앞 `days`일의 실적으로 **도시 전체 배율 하나**를 구한다.

    stats  : 학습 달의 대여소별 통계 (station_id, mu)
    recent : 계획 대상 달의 부분 실적 (station_id, date, demand)

    대여소별로 보정하지 않는 이유는 며칠치 표본으로 나누면 잡음만 커지기 때문이다.
    계절 효과는 도시 전체에 같은 방향으로 온다.

    **수요가 0 근처인 대여소는 뺀다**(min_demand). 넣으면 배율이 과대추정된다 —
    위 WARMUP_MIN_DEMAND 주석 참고.

    배율을 낼 수 없으면(자료 부족) None — 호출부가 보정을 건너뛴다.
    """
    if days <= 0 or recent.empty or stats.empty:
        return None

    dates = pd.to_datetime(recent["date"])
    head = recent[dates <= dates.min() + pd.Timedelta(days=days - 1)]
    if head.empty:
        return None

    usable = stats[stats["mu"].abs() > min_demand] if min_demand > 0 else stats
    if usable.empty:
        return None

    observed = head.groupby("station_id")["demand"].mean()
    joined = usable.set_index("station_id")[["mu"]].join(
        observed.rename("head"), how="inner").dropna()
    baseline = joined["mu"].abs().sum()
    if joined.empty or baseline <= 0:
        return None

    ratio = float(joined["head"].abs().sum() / baseline)
    return float(np.clip(ratio, *WARMUP_CLIP))


def apply_warmup(stats: pd.DataFrame, ratio: Optional[float]) -> pd.DataFrame:
    """mu·sigma에 배율을 곱한다. **중심을 옮기는 것**이지 분산만 부풀리는 게 아니다."""
    if not ratio or ratio == 1.0:
        return stats
    scaled = stats.copy()
    for column in ("mu", "sigma"):
        if column in scaled.columns:
            scaled[column] = scaled[column] * ratio
    return scaled


# ---------------- 분위수 모델 ----------------

def train(frame: pd.DataFrame, quantile: float = TARGET_QUANTILE,
          random_state: int = 7):
    """분위수 회귀 모델을 학습한다.

    sklearn의 HistGradientBoosting을 쓰는 이유는 추가 설치가 없어서다
    (requirements의 scikit-learn으로 충분하다). LightGBM이 더 강하지만
    이득을 확인하기 전에 의존성을 늘리지 않는다.
    """
    from sklearn.ensemble import HistGradientBoostingRegressor

    if frame.empty:
        raise ValueError("학습 데이터가 비었습니다. 순수요를 여러 달 만들어 두세요.")

    # 날씨 자료가 아예 없으면 그 컬럼이 통째로 NaN이 된다. 부분 결측은 이 모델이
    # 알아서 다루지만 **전부 NaN이면 구간화 자체가 깨진다**(joblib에서
    # "window shape cannot be larger than input array shape"로 터진다).
    # 정보가 없는 컬럼이므로 상수로 눕힌다 — 트리는 상수로 나눌 수 없어 무해하다.
    frame = frame.copy()
    for column in WEATHER_FEATURES:
        if frame[column].isna().all():
            frame[column] = 0.0

    model = HistGradientBoostingRegressor(
        loss="quantile", quantile=quantile,
        max_iter=300, learning_rate=0.08, max_depth=6,
        min_samples_leaf=40, l2_regularization=1.0,
        categorical_features=[FEATURES.index(c) for c in CATEGORICAL],
        random_state=random_state,
    )
    model.fit(frame[FEATURES].to_numpy(), frame["demand"].to_numpy())
    return model


def save(model, quantile: float, path: Optional[Path] = None) -> Path:
    """모델과 함께 **무엇으로 학습했는지**를 남긴다(나중에 검증할 수 있게)."""
    path = Path(path or MODEL_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        pickle.dump({"model": model, "quantile": quantile,
                     "features": FEATURES, "durations": DURATIONS,
                     "day_types": DAY_TYPES}, handle)
    return path


def load(path: Optional[Path] = None) -> Optional[dict]:
    """저장된 모델. 없거나 읽을 수 없으면 None(호출부가 기존 공식으로 폴백)."""
    path = Path(path or MODEL_PATH)
    if not path.exists():
        return None
    try:
        with path.open("rb") as handle:
            bundle = pickle.load(handle)
    except Exception as err:                  # 손상·버전 불일치 — 폴백이 안전하다
        print(f"[경고] 수요 모델을 읽지 못했습니다({type(err).__name__}: {err})."
              " mu + z·sigma로 계산합니다.")
        return None
    if bundle.get("features") != FEATURES:
        print("[경고] 저장된 모델의 피처 구성이 지금 코드와 다릅니다."
              " 다시 학습하세요(tools/train_demand_model.py). mu + z·sigma로 계산합니다.")
        return None
    return bundle


def predict(bundle: dict, features: pd.DataFrame) -> np.ndarray:
    """목표 재고 예측 = 다음 기간 순수요의 `bundle['quantile']` 분위수.

    `features`는 **build_features()가 만든 것**이어야 한다. 컬럼 순서가 학습 때와
    같아야 하므로 FEATURES로 다시 정렬한다 — 호출부가 순서를 맞추게 두면
    언젠가 어긋난다.
    """
    missing = [c for c in FEATURES if c not in features.columns]
    if missing:
        raise KeyError(f"피처가 없습니다: {missing}"
                       " (build_features()를 거치지 않았을 수 있습니다)")
    return bundle["model"].predict(features[FEATURES].to_numpy())
