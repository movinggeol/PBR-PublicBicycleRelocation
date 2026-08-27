"""실험 — 박정연 외(2024)의 모형이 `mu + z·sigma`를 이기는가 (THESIS 9번).

같은 도시·같은 시스템(대전 타슈)을 다룬 선행연구가 쓴 세 모형을 **본 연구의
타깃(순수요)에 적용**해, 현행 방식과 **같은 자로** 비교한다.

  · 선형회귀 (Linear regression)
  · 음이항회귀 → **여기서는 쓸 수 없다** (아래 참고)
  · 선형혼합효과 (Linear mixed effects) — 대여소·시간대를 임의효과로

⚠️ **왜 MAE를 나란히 놓지 않는가**

박정연 외는 **대여건수**를 예측했고(MAE 2.01), 본 연구는 **순수요**(대여−반납)를
예측한다. 타깃이 다르면 수치를 비교할 수 없다(COMPARISON.md 축 ⑤).
그래서 이 실험은 **그들의 모형을 우리 타깃에 옮겨** 방법론만 겨룬다.
*"같은 문제를 그들 방식으로 풀면 더 나은가?"* 가 물음이다.

⚠️ **음이항회귀는 제외한다.** 음이항분포는 **비음수 계수(count)** 를 위한 것인데
순수요는 **음수가 된다**(자전거가 쌓이는 대여소). 억지로 맞추면 모형 가정이
깨진다. 이것 자체가 논문에 쓸 발견이다 — *"선행연구의 모형 선택은 대여건수라는
타깃에 묶여 있다."*

⚠️ **주변환경·인구 변수는 없다.** 그들은 100m 내 지하철·학교·버스·공원,
200m 내 하천, 인구 격자를 썼다. 본 연구에는 없다. 다만 **그들의 최종 모형에서
유의한 주변환경 변수는 하천 하나뿐**이었으므로(LITERATURE.md 3-1),
비교 자체는 성립한다. 이 한계는 결과와 함께 밝힌다.

판정 기준은 `quantile_model_eval.py`의 것을 그대로 쓴다:
작업 대상만(`|순수요| > REBAL_MIN_QTY`) · 평일/휴일 따로 · **표본 밖** ·
베이스라인 초과. 그리고 **여유분을 맞춘 커버리지**도 함께 본다
(DEMAND_DISTRIBUTION.md 채택 기준 5번).

실행:
    python experiments/structure/park2024_compare.py
    python experiments/structure/park2024_compare.py --day-type holiday
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import numpy as np
import pandas as pd

import db
import weather
from project_config import (
    DAY_TYPES, DURATIONS, REBAL_MIN_QTY, TARGET_Z,
    normalize_day_type, select_day_type,
)
from backtest_demand import daily_window_demand


def period_key(label):
    year, month = label.split("년")
    return int(year.strip()) * 100 + int(month.replace("월", "").strip())


def previous_month(label):
    year, month = divmod(period_key(label), 100)
    month -= 1
    if month == 0:
        year, month = year - 1, 12
    return "%02d년 %02d월" % (year, month)


SEASON = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring",
          5: "spring", 6: "summer", 7: "summer", 8: "summer",
          9: "fall", 10: "fall", 11: "fall"}


def daily_weather() -> pd.DataFrame:
    """날짜별 기온·강수·풍속. 박정연 외의 변수 구성을 따른다.

    강수는 그들과 같이 **0.5mm 이상이면 1**로 이진화한다(기상청 기준).
    """
    try:
        hourly = weather.load_hourly()
    except Exception as err:
        print("[안내] 날씨를 읽지 못했습니다: %s: %s" % (type(err).__name__, err))
        return pd.DataFrame()
    if hourly.empty:
        return pd.DataFrame()
    frame = hourly.copy()
    frame["date"] = frame["time"].dt.normalize()
    daily = frame.groupby("date").agg(
        temp=("temp", "mean"),
        rain_mm=("rain", "sum"),
        wind=("wind", "mean"),
    ).reset_index()
    daily["rain"] = (daily["rain_mm"] >= 0.5).astype(float)
    daily["temp2"] = daily["temp"] ** 2
    daily["season"] = daily["date"].dt.month.map(SEASON)
    return daily


def build_design(demand: pd.DataFrame, wx: pd.DataFrame) -> pd.DataFrame:
    """(대여소, 날짜) 표에 박정연 외의 설명변수를 붙인다.

    고정효과: 기온 · 기온² · 강수 · 풍속 · 계절 · 계절×기온 · 계절×풍속
    (주변환경·인구는 본 연구에 없다 — 문서 상단 참고)
    """
    frame = demand.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    frame = frame.merge(wx, on="date", how="inner")
    if frame.empty:
        return frame

    # 계절 더미 (봄이 기준 — 원문과 같다)
    for s in ("summer", "fall", "winter"):
        frame[f"s_{s}"] = (frame["season"] == s).astype(float)
        frame[f"temp_x_{s}"] = frame[f"s_{s}"] * frame["temp"]
        frame[f"wind_x_{s}"] = frame[f"s_{s}"] * frame["wind"]
    return frame


FIXED = ["temp", "temp2", "rain", "wind",
         "s_summer", "s_fall", "s_winter",
         "temp_x_summer", "temp_x_fall", "temp_x_winter",
         "wind_x_summer", "wind_x_fall", "wind_x_winter"]


def usable_columns(train: pd.DataFrame, cols):
    """학습 자료에서 **실제로 쓸 수 있는** 설명변수만 고른다.

    ⚠️ **한 달로 학습하면 계절이 하나뿐이다.** 계절 더미와 그 교호작용이
    전부 0(또는 서로 중복)이 되어 설계행렬의 rank가 무너진다 — 실측에서
    14열 중 rank 5였다. 그대로 `pinv`로 풀면 계수가 폭주해 MAE가 10^11까지 뛴다.

    **이것은 구현 버그가 아니라 방법의 성질이다.** 박정연 외(2024)는
    **1년치를 한 번에** 학습해 계절이 다 들어 있었다. 본 연구는 직전 1달만
    쓰므로(그것이 더 낫다는 것을 6장에서 측정) 계절 변수를 쓸 수 없다.
    """
    keep = []
    for c in cols:
        v = train[c].to_numpy(float)
        if np.nanstd(v) > 1e-9:          # 상수열은 뺀다
            keep.append(c)
    if not keep:
        return keep
    # 남은 것끼리도 공선성이 있으면 rank가 모자란다 — 순서대로 넣어 본다
    chosen = []
    for c in keep:
        trial = chosen + [c]
        X = np.column_stack([np.ones(len(train))]
                            + [train[k].to_numpy(float) for k in trial])
        if np.linalg.matrix_rank(X) == X.shape[1]:
            chosen = trial
    return chosen


def fit_ols(train: pd.DataFrame, cols):
    """최소제곱 (numpy). 상수항을 붙이고 pinv로 푼다."""
    X = np.column_stack([np.ones(len(train))] + [train[c].to_numpy(float) for c in cols])
    y = train["demand"].to_numpy(float)
    return np.linalg.pinv(X) @ y


def predict_ols(beta, frame: pd.DataFrame, cols):
    X = np.column_stack([np.ones(len(frame))] + [frame[c].to_numpy(float) for c in cols])
    return X @ beta


def fit_lme(train: pd.DataFrame, cols):
    """선형혼합효과의 **간이 구현** — 고정효과 OLS + 대여소별 임의절편.

    임의절편은 잔차의 대여소 평균을 수축(shrinkage)해 얻는다:
        u_j = n_j / (n_j + lambda) * mean(resid_j)
    `lambda`는 잔차분산/집단간분산의 비를 모멘트법으로 추정한 값이다.
    이것이 REML의 임의절편 해와 같은 형태다.

    ⚠️ **statsmodels를 쓰지 않는 이유**: 새 의존성을 넣지 않으려는 것이고,
    임의절편만 있는 모형에서는 이 근사가 충분하다. 임의기울기가 필요해지면
    그때 statsmodels로 바꿔라.
    """
    beta = fit_ols(train, cols)
    resid = train["demand"].to_numpy(float) - predict_ols(beta, train, cols)
    work = train.assign(resid=resid)
    grouped = work.groupby("station_id")["resid"]
    counts = grouped.size()
    means = grouped.mean()

    within = float(np.var(resid, ddof=1))
    between = float(np.var(means, ddof=1)) if len(means) > 1 else 0.0
    between = max(between - within / max(counts.mean(), 1), 1e-9)
    lam = within / between if between > 0 else 1e9

    u = (counts / (counts + lam)) * means
    return beta, u.to_dict()


def predict_lme(model, frame: pd.DataFrame, cols):
    beta, u = model
    base = predict_ols(beta, frame, cols)
    return base + frame["station_id"].map(u).fillna(0.0).to_numpy(float)


def sigma_of(train: pd.DataFrame, resid: np.ndarray) -> pd.Series:
    """대여소별 잔차 표준편차. 목표재고의 여유분을 만들 때 쓴다."""
    return (train.assign(r=resid).groupby("station_id")["r"]
            .std().fillna(0.0))


def measure(pred: np.ndarray, sig: pd.Series, test: pd.DataFrame,
            z: float, fixed_buffer=None) -> dict:
    """작업 대상에서만 잰다. 커버리지는 두 번(그대로·여유분 고정)."""
    keep = test["demand"].abs() > REBAL_MIN_QTY
    if keep.sum() < 30:
        return {}
    truth = test.loc[keep, "demand"].to_numpy(float)
    mu = pred[keep.to_numpy()]
    s = test.loc[keep, "station_id"].map(sig).fillna(sig.median() if len(sig) else 0.0)
    s = s.to_numpy(float)
    got = {
        "mae": float(np.abs(truth - mu).mean()),
        "bias": float((truth - mu).mean()),
        "coverage": float((np.abs(truth) <= np.abs(mu) + z * s).mean()),
        "buffer": float((z * s).mean()),
        "n": int(keep.sum()),
    }
    if fixed_buffer is not None:
        got["cov_fixed"] = float((np.abs(truth) <= np.abs(mu) + fixed_buffer).mean())
    return got


def main() -> int:
    parser = argparse.ArgumentParser(description="박정연 외(2024) 모형 대비 비교")
    parser.add_argument("--day-type", default=DAY_TYPES[0])
    parser.add_argument("--z", type=float, default=TARGET_Z)
    args, _ = parser.parse_known_args()
    day_type = normalize_day_type(args.day_type)

    wx = daily_weather()
    if wx.empty:
        print("날씨 자료가 없어 비교할 수 없습니다 (docs/분석/WEATHER.md).")
        return 1

    with db.session() as conn:
        periods = [r[0] for r in conn.execute(
            "SELECT DISTINCT period FROM net_demand").fetchall()]
        net = {}
        for p in periods:
            frame = select_day_type(db.load_frame(conn, "net_demand", period=p),
                                    "date", day_type)
            if not frame.empty:
                net[p] = frame

    periods = sorted(net, key=period_key)
    print("적재 기간 %d개 · %s · z=%s · 작업 대상만(|순수요|>%d)"
          % (len(periods), day_type, args.z, REBAL_MIN_QTY))
    print("고정효과 %d개 (주변환경·인구는 본 연구에 없음)\n" % len(FIXED))

    rows, dropped = [], []
    for duration in DURATIONS:
        for test_period in periods:
            prev = previous_month(test_period)
            if prev not in net:
                continue

            train = build_design(daily_window_demand(net[prev], duration), wx)
            test = build_design(daily_window_demand(net[test_period], duration), wx)
            if len(train) < 200 or len(test) < 100:
                continue

            # ── 베이스라인: 본 연구의 mu + z·sigma (직전 달 통계) ──
            g = train.groupby("station_id")["demand"]
            base_mu, base_sd = g.mean(), g.std().fillna(0.0)
            base_pred = test["station_id"].map(base_mu).fillna(0.0).to_numpy(float)
            ruler = measure(base_pred, base_sd, test, args.z)
            if not ruler:
                continue
            fixed = ruler["buffer"]
            rows.append(dict(duration=duration, period=test_period,
                             method="본 연구 (mu+zσ)", **measure(
                                 base_pred, base_sd, test, args.z, fixed)))

            # 쓸 수 있는 변수만 고른다 (한 달 학습이면 계절이 빠진다)
            cols = usable_columns(train, FIXED)
            dropped.append(len(FIXED) - len(cols))
            if not cols:
                continue

            # ── 박정연 ①: 선형회귀 ──
            beta = fit_ols(train, cols)
            sd_lin = sigma_of(train, train["demand"].to_numpy(float)
                              - predict_ols(beta, train, cols))
            got = measure(predict_ols(beta, test, cols), sd_lin, test, args.z, fixed)
            if got:
                rows.append(dict(duration=duration, period=test_period,
                                 method="선형회귀", **got))

            # ── 박정연 ②: 선형혼합효과 (대여소 임의절편) ──
            model = fit_lme(train, cols)
            sd_lme = sigma_of(train, train["demand"].to_numpy(float)
                              - predict_lme(model, train, cols))
            got = measure(predict_lme(model, test, cols), sd_lme, test, args.z, fixed)
            if got:
                rows.append(dict(duration=duration, period=test_period,
                                 method="선형혼합효과", **got))

    if not rows:
        print("비교할 조합이 없습니다.")
        return 1

    frame = pd.DataFrame(rows)
    order = ["본 연구 (mu+zσ)", "선형혼합효과", "선형회귀"]

    if dropped:
        print("⚠️ 설계행렬 rank 때문에 뺀 변수: 평균 %.1f개 / %d개"
              % (np.mean(dropped), len(FIXED)))
        print("   한 달 학습이면 계절이 하나뿐이라 계절 더미·교호작용을 쓸 수 없다.")
        print("   박정연 외는 1년치를 한 번에 학습해 이 문제가 없었다.")
        print()

    print("=== 방법별 평균 (모든 시간대·달, 표본 밖) ===")
    agg = dict(mae=("mae", "mean"), coverage=("coverage", "mean"),
               buffer=("buffer", "mean"), bias=("bias", "mean"),
               n=("n", "sum"), 달=("period", "nunique"))
    if "cov_fixed" in frame:
        agg["cov_fixed"] = ("cov_fixed", "mean")
    summary = frame.groupby("method").agg(**agg)
    summary = summary.reindex([m for m in order if m in summary.index])
    print(summary.round(3).to_string())

    print("\n=== 시간대별 MAE ===")
    print(frame.pivot_table(index="method", columns="duration",
                            values="mae").reindex(
        [m for m in order if m in frame["method"].unique()]).round(3).to_string())

    base_name = "본 연구 (mu+zσ)"
    if base_name in summary.index:
        base = summary.loc[base_name]
        print("\n=== 판정 ===")
        print("베이스라인(%s) MAE %.3f · 커버리지 %.3f"
              % (base_name, base["mae"], base["coverage"]))
        pivot = frame.pivot_table(index=["duration", "period"],
                                  columns="method", values="mae")
        for name in summary.index:
            if name == base_name or name not in pivot:
                continue
            wins = int((pivot[name] < pivot[base_name]).sum())
            total = int(pivot[[name, base_name]].notna().all(axis=1).sum())
            row = summary.loc[name]
            print("  %-14s MAE %.3f (%+.1f%%) · 이긴 달 %d/%d"
                  % (name, row["mae"],
                     (base["mae"] - row["mae"]) / base["mae"] * 100, wins, total))
        print("\n  ⚠️ 박정연 외의 원 수치(MAE 2.01)와 비교하지 마라 —")
        print("     그들의 타깃은 대여건수이고 여기는 순수요다.")
        print("     이 표는 **같은 타깃에 그들 모형을 옮겨** 잰 것이다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
