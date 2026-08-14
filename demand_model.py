"""목표 재고(target_qty)를 분위수 회귀로 직접 예측한다 (docs/DEMAND_DISTRIBUTION.md).

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

from project_config import DATA_ROOT, DAY_TYPES, select_day_type

MODEL_PATH = DATA_ROOT / "models" / "target_quantile.pkl"

# 목표 분위수. target_qty의 설계 의도(95%를 덮는다)와 같은 값이다.
TARGET_QUANTILE = 0.95

DURATIONS = ("_05_10", "_10_15", "_15_20", "_20_05")

# 직전 달 통계에서 뽑는 피처. **예측 시점에 알 수 있는 것만** 넣는다.
FEATURES = [
    "prev_mu", "prev_sigma", "prev_abs_mu", "prev_median",
    "prev_q90", "prev_max", "prev_min", "prev_zero_ratio", "prev_days",
    "month", "duration_idx", "day_type_idx",
]
CATEGORICAL = ["duration_idx", "day_type_idx"]


def window_hours(duration: str) -> list:
    start, end = int(duration.split("_")[1]), int(duration.split("_")[2])
    return list(range(start, end)) if start < end else \
        list(range(start, 24)) + list(range(0, end))


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


def training_frame(net_by_period: dict, day_types=DAY_TYPES,
                   durations=DURATIONS) -> pd.DataFrame:
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

                features = add_context(summarize(train_demand), duration, day_type, month)
                merged = test_demand.merge(features, on="station_id", how="inner")
                if not merged.empty:
                    rows.append(merged)

    if not rows:
        return pd.DataFrame(columns=["station_id", "date", "demand", *FEATURES])
    return pd.concat(rows, ignore_index=True)


# ---------------- 계절 수준 보정 (warmup) ----------------
#
# ML이 아니다. 지난달 통계에 **배율 하나**를 곱하는 것뿐이다.
# 계절이 도약하는 달(2월→3월 수요 1.5배)에는 지난달 평균이 구조적으로 낮게 나오고,
# z를 올리는 것으로는 대체되지 않는다(z=2.10에서도 전환 달 커버리지 90.4%).
# 근거: docs/EXPERIMENTS.md 3장.

WARMUP_CLIP = (0.5, 2.0)      # 며칠치 잡음으로 배율이 과하게 튀는 것을 막는다


def warmup_ratio(stats: pd.DataFrame, recent: pd.DataFrame, days: int) -> Optional[float]:
    """계획 대상 달 앞 `days`일의 실적으로 **도시 전체 배율 하나**를 구한다.

    stats  : 학습 달의 대여소별 통계 (station_id, mu)
    recent : 계획 대상 달의 부분 실적 (station_id, date, demand)

    대여소별로 보정하지 않는 이유는 며칠치 표본으로 나누면 잡음만 커지기 때문이다.
    계절 효과는 도시 전체에 같은 방향으로 온다.

    배율을 낼 수 없으면(자료 부족) None — 호출부가 보정을 건너뛴다.
    """
    if days <= 0 or recent.empty or stats.empty:
        return None

    dates = pd.to_datetime(recent["date"])
    head = recent[dates <= dates.min() + pd.Timedelta(days=days - 1)]
    if head.empty:
        return None

    observed = head.groupby("station_id")["demand"].mean()
    joined = stats.set_index("station_id")[["mu"]].join(
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


def predict_target(bundle: dict, stats: pd.DataFrame, duration: str,
                   day_type: str, month: int) -> np.ndarray:
    """대여소별 목표 재고(= 다음 기간 순수요의 95분위) 예측.

    `stats`는 calculate_target_qty가 가진 대여소별 통계여야 하며,
    최소한 summarize()가 만드는 컬럼을 갖고 있어야 한다.
    """
    features = add_context(stats, duration, day_type, month)
    missing = [c for c in FEATURES if c not in features.columns]
    if missing:
        raise KeyError(f"피처가 없습니다: {missing}")
    return bundle["model"].predict(features[FEATURES].to_numpy())
