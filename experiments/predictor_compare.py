"""실험 B — `_10_15` 회차의 `mu`는 왜 '0 예측'과 차이가 없나 (docs/EXPERIMENTS.md 2장).

백테스트(1.11.0)에서 `_10_15`의 MAE는 네 회차 중 가장 낮았는데도 기준선 대비
-0.2%로, "대여소별 평균을 쓰는 것이 0이라고 찍는 것보다 나을 게 없다"는 결과가 나왔다.
MAE가 낮은 것은 그 시간대의 수요 자체가 작기 때문이지 예측이 좋아서가 아니었다.

여기서 묻는 것은 두 가지다.

  1. 진단 — `mu`가 잡아내려는 신호가 애초에 있기는 한가?
     · 신호대잡음비: 대여소 간 mu 분산 ÷ 대여소 내 일별 분산
     · 재현성: 학습 달 mu와 검증 달 mu의 상관계수 (신호라면 달이 바뀌어도 남는다)
  2. 대안 — 다른 예측기를 쓰면 나아지는가?
     0 / 전체평균 / 대여소평균(현행) / 대여소중앙값 / 요일별평균 / 최근 7일

실행:
    python experiments/predictor_compare.py
    python experiments/predictor_compare.py --duration _10_15
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import numpy as np
import pandas as pd

import db
from backtest_demand import DURATIONS, consecutive_pairs, daily_window_demand


# ---------- 1. 진단 ----------

def diagnose(train: pd.DataFrame, test: pd.DataFrame) -> dict:
    """신호가 있는지를 두 각도에서 본다."""
    train_stats = train.groupby("station_id")["demand"].agg(mu="mean", sigma="std").fillna(0)
    test_mu = test.groupby("station_id")["demand"].mean().rename("test_mu")

    joined = train_stats.join(test_mu, how="inner")
    signal = float(joined["mu"].var())            # 대여소 간 차이 = 잡아내려는 신호
    noise = float((train_stats["sigma"] ** 2).mean())   # 대여소 내 일별 흔들림 = 잡음

    absolute = train_stats["mu"].abs()
    return {
        "신호(대여소간 분산)": signal,
        "잡음(대여소내 분산)": noise,
        "신호대잡음비": signal / noise if noise else float("nan"),
        # 학습 달의 mu가 검증 달에도 남아 있는가 (진짜 신호라면 상관이 높다)
        "mu_재현성": float(joined["mu"].corr(joined["test_mu"])) if len(joined) > 2 else float("nan"),
        "평균수요크기": float(train["demand"].abs().mean()),
        # 분산은 소수의 큰 대여소가 끌어올린다. 중앙값과 함께 봐야 분포가 보인다.
        "|mu|_중앙값": float(absolute.median()),
        "|mu|_상위10%": float(absolute.quantile(0.9)),
        "|mu|<0.5_비율": float((absolute < 0.5).mean()),
    }


# ---------- 1-b. 작업 대상 대여소만 ----------

def score_by_group(train: pd.DataFrame, test: pd.DataFrame, threshold: float = 2.0) -> list:
    """전체 평균 MAE는 파이프라인이 손대지 않는 대여소까지 포함한다.

    step1은 `|rebal_qty| > 2`인 곳만 후보로 삼는다. 그런 대여소는 전체의 일부이고,
    나머지 대부분은 수요가 0 근처라 무엇으로 예측하든 오차가 비슷하다.
    두 무리를 섞어 평균 내면 예측기의 값어치가 희석된다.
    """
    station_mu = train.groupby("station_id")["demand"].mean()
    station_med = train.groupby("station_id")["demand"].median()
    targets = set(station_mu[station_mu.abs() >= threshold].index)

    rows = []
    for label, members in (("작업대상", targets), ("나머지", set(station_mu.index) - targets)):
        part = test[test["station_id"].isin(members)]
        if part.empty:
            continue
        rows.append({
            "무리": label,
            "대여소": len(members),
            "mae_현행": float((part["demand"] - part["station_id"].map(station_mu)).abs().mean()),
            "mae_중앙값": float((part["demand"] - part["station_id"].map(station_med)).abs().mean()),
            "mae_0예측": float(part["demand"].abs().mean()),
        })
    return rows


# ---------- 2. 대안 예측기 ----------

def predictors(train: pd.DataFrame) -> dict:
    """학습 달로 만든 예측기들. 각각 (검증 프레임 -> 예측값 시리즈) 함수."""
    station_mu = train.groupby("station_id")["demand"].mean()
    station_med = train.groupby("station_id")["demand"].median()
    global_mu = train["demand"].mean()

    train = train.copy()
    train["dow"] = pd.to_datetime(train["date"]).dt.dayofweek
    dow_mu = train.groupby(["station_id", "dow"])["demand"].mean()

    recent_cut = pd.to_datetime(train["date"]).max() - pd.Timedelta(days=7)
    recent = train[pd.to_datetime(train["date"]) > recent_cut]
    recent_mu = recent.groupby("station_id")["demand"].mean()

    def by_dow(test: pd.DataFrame) -> pd.Series:
        keys = pd.MultiIndex.from_arrays(
            [test["station_id"], pd.to_datetime(test["date"]).dt.dayofweek])
        return pd.Series(dow_mu.reindex(keys).to_numpy(), index=test.index).fillna(global_mu)

    return {
        "0 예측": lambda t: pd.Series(0.0, index=t.index),
        "전체평균": lambda t: pd.Series(global_mu, index=t.index),
        "대여소평균(현행)": lambda t: t["station_id"].map(station_mu).fillna(global_mu),
        "대여소중앙값": lambda t: t["station_id"].map(station_med).fillna(global_mu),
        "요일별평균": by_dow,
        "최근7일": lambda t: t["station_id"].map(recent_mu).fillna(global_mu),
    }


def score(train: pd.DataFrame, test: pd.DataFrame) -> dict:
    """예측기별 MAE. 학습 달에 있는 대여소만 대상으로 한다."""
    known = set(train["station_id"])
    test = test[test["station_id"].isin(known)]
    if test.empty:
        return {}
    return {name: float((test["demand"] - fn(test)).abs().mean())
            for name, fn in predictors(train).items()}


def main() -> int:
    parser = argparse.ArgumentParser(description="_10_15 회차 재검토 — 예측기 비교")
    parser.add_argument("--duration", help="시간대 하나만")
    args, _ = parser.parse_known_args()

    durations = [args.duration] if args.duration else DURATIONS

    with db.session() as conn:
        periods = [r[0] for r in conn.execute("SELECT DISTINCT period FROM net_demand").fetchall()]
        net = {p: db.load_frame(conn, "net_demand", period=p) for p in periods}

    pairs = consecutive_pairs(periods)
    print(f"기간 {len(periods)}개 · 연속 월쌍 {len(pairs)}개\n")

    diag_rows, score_rows, group_rows = [], [], []
    for duration in durations:
        for train_period, test_period in pairs:
            train = daily_window_demand(net[train_period], duration)
            test = daily_window_demand(net[test_period], duration)
            diag_rows.append({"duration": duration, **diagnose(train, test)})
            result = score(train, test)
            if result:
                score_rows.append({"duration": duration, **result})
            group_rows.extend({"duration": duration, **row}
                              for row in score_by_group(train, test))

    diag = pd.DataFrame(diag_rows).groupby("duration").mean().round(3)
    print("=== 진단: 잡아낼 신호가 있는가 ===")
    print(diag.to_string())
    print("\n  신호대잡음비 — 대여소마다 수요가 다른 정도를, 같은 대여소가 날마다 흔들리는")
    print("  정도로 나눈 값. 이 값이 작으면 대여소별 평균을 내봐야 잡음을 평균한 것에 가깝다.")
    print("  mu_재현성 — 학습 달의 mu와 검증 달의 mu가 얼마나 같은 순서를 유지하는가.")

    groups = pd.DataFrame(group_rows).groupby(["duration", "무리"]).mean().round(3)
    groups["현행_개선률%"] = ((1 - groups["mae_현행"] / groups["mae_0예측"]) * 100).round(1)
    groups["중앙값_개선률%"] = ((1 - groups["mae_중앙값"] / groups["mae_0예측"]) * 100).round(1)
    print("\n=== 무리를 나눠 보면 (|mu| ≥ 2 = step1 후보 기준) ===")
    print(groups.to_string())
    print("\n  파이프라인이 실제로 손대는 곳과 손대지 않는 곳을 섞어 평균 내면,")
    print("  수가 훨씬 많은 '나머지'가 전체 MAE를 지배한다.")

    scores = pd.DataFrame(score_rows).groupby("duration").mean().round(3)
    print("\n=== 대안: 예측기별 MAE (낮을수록 좋음) ===")
    print(scores.to_string())

    print("\n=== 현행(대여소평균) 대비 개선률(%) ===")
    base = scores["대여소평균(현행)"]
    relative = ((base.values[:, None] - scores.values) / base.values[:, None] * 100)
    print(pd.DataFrame(relative, index=scores.index, columns=scores.columns).round(1).to_string())

    print("\n판정:")
    for duration in scores.index:
        best = scores.loc[duration].idxmin()
        gain = (scores.loc[duration, "대여소평균(현행)"] - scores.loc[duration, best])
        snr, repeat = diag.loc[duration, "신호대잡음비"], diag.loc[duration, "mu_재현성"]
        if best == "대여소평균(현행)":
            print(f"  {duration}: 현행이 최선 (SNR {snr:.2f}, 재현성 {repeat:.2f})")
        else:
            print(f"  {duration}: '{best}'가 MAE {gain:.3f}대 더 낮다"
                  f" (SNR {snr:.2f}, 재현성 {repeat:.2f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
