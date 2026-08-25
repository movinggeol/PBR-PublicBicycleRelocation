"""실험 C — 계절이 바뀔 때 학습 창을 어떻게 잡을 것인가 (docs/분석/EXPERIMENTS.md 3장).

백테스트에서 26년 2월→3월 쌍의 커버리지가 86.5%로 유독 낮았다. 봄이 오면서
수요가 크게 뛰는데, "직전 한 달 평균"이 그 도약을 따라가지 못한 것이다.
학습 창을 바꾸면 나아지는지 본다.

  · prev1   직전 1달 (현행)
  · prev2/3 직전 2~3달 — 표본은 늘지만 더 오래된(더 추운) 달이 섞인다
  · warmup7 직전 1달 mu에, 검증 달 첫 7일로 구한 배율을 곱해 보정
            (운영 중 실제로 쓸 수 있는 방법 — 이달 초 실적은 이미 손에 있다)
  · warmup14 같은 방식, 14일

실행:
    python experiments/params/seasonal_window.py
    python experiments/params/seasonal_window.py --duration _05_10 --z 2.10
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]      # experiments/<분류>/ 아래에 있다
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import numpy as np
import pandas as pd

import db
from backtest_demand import DURATIONS, daily_window_demand


def period_key(label: str) -> int:
    """'25년 11월' -> 2511"""
    year, month = label.split("년")
    return int(year.strip()) * 100 + int(month.replace("월", "").strip())


def previous_months(label: str, count: int) -> list:
    """직전 `count`개월의 라벨을 만든다 (연도 넘어감 처리)."""
    key = period_key(label)
    year, month = divmod(key, 100)
    out = []
    for _ in range(count):
        month -= 1
        if month == 0:
            year, month = year - 1, 12
        out.append(f"{year:02d}년 {month:02d}월")
    return out


def fit(frames: list) -> pd.DataFrame:
    """여러 달을 합쳐 대여소별 mu/sigma를 낸다."""
    return (pd.concat(frames).groupby("station_id")["demand"]
            .agg(mu="mean", sigma="std").fillna(0))


def warmup_scale(stats: pd.DataFrame, test: pd.DataFrame, days: int) -> pd.DataFrame:
    """검증 달 앞 `days`일의 실적으로 전체 배율을 구해 mu·sigma에 곱한다.

    대여소별로 보정하면 며칠치 표본이라 잡음만 키운다. 계절 효과는 도시 전체에
    같은 방향으로 오므로 배율 하나만 추정하는 편이 안정적이다.
    """
    dates = pd.to_datetime(test["date"])
    head = test[dates <= dates.min() + pd.Timedelta(days=days - 1)]

    observed = head.groupby("station_id")["demand"].mean()
    joined = stats.join(observed.rename("head"), how="inner").dropna()
    baseline = joined["mu"].abs().sum()
    if baseline <= 0:
        return stats

    ratio = joined["head"].abs().sum() / baseline
    ratio = float(np.clip(ratio, 0.5, 2.0))     # 며칠치 잡음으로 과하게 튀는 것을 막는다

    scaled = stats.copy()
    scaled[["mu", "sigma"]] *= ratio
    return scaled


def measure(stats: pd.DataFrame, test: pd.DataFrame, z: float) -> dict:
    merged = test.merge(stats, on="station_id", how="inner")
    if merged.empty:
        return {}
    error = merged["demand"] - merged["mu"]
    return {
        "mae": float(error.abs().mean()),
        "bias": float(error.mean()),
        "coverage": float((merged["demand"] <= merged["mu"] + z * merged["sigma"]).mean()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="계절 전환기 학습 창 비교")
    parser.add_argument("--duration", help="시간대 하나만")
    parser.add_argument("--z", type=float, default=1.65, help="커버리지 판정 계수")
    args, _ = parser.parse_known_args()

    durations = [args.duration] if args.duration else DURATIONS

    with db.session() as conn:
        periods = [r[0] for r in conn.execute("SELECT DISTINCT period FROM net_demand").fetchall()]
        net = {p: db.load_frame(conn, "net_demand", period=p) for p in periods}

    available = set(periods)
    print(f"적재 기간 {len(periods)}개 · z={args.z}\n")

    # 각 달의 전체 수요 규모 — 계절 도약이 어디서 일어나는지 먼저 본다
    scale = pd.Series({p: float(daily_window_demand(net[p], "_05_10")["demand"].abs().mean())
                       for p in periods}).sort_index(key=lambda s: s.map(period_key))
    print("=== 달별 수요 규모 (_05_10 평균 |순수요|) ===")
    ratio = (scale / scale.shift(1)).round(2)
    print(pd.DataFrame({"평균|순수요|": scale.round(3), "전월대비": ratio}).to_string())
    print()

    rows = []
    for duration in durations:
        for test_period in periods:
            test = daily_window_demand(net[test_period], duration)
            history = previous_months(test_period, 3)
            if history[0] not in available:
                continue                       # 직전 달이 없으면 비교 자체가 안 된다

            windows = {}
            for count in (1, 2, 3):
                months = [m for m in history[:count] if m in available]
                if len(months) == count:
                    windows[f"prev{count}"] = fit([daily_window_demand(net[m], duration)
                                                   for m in months])
            base = windows.get("prev1")
            if base is not None:
                for days in (7, 14):
                    windows[f"warmup{days}"] = warmup_scale(base, test, days)

            for name, stats in windows.items():
                result = measure(stats, test, args.z)
                if result:
                    rows.append({"duration": duration, "검증": test_period,
                                 "창": name, **result})

    if not rows:
        print("비교할 월 쌍이 없습니다.")
        return 1

    frame = pd.DataFrame(rows)

    print("=== 학습 창별 평균 (전체 달) ===")
    overall = frame.groupby(["duration", "창"]).agg(
        mae=("mae", "mean"), bias=("bias", "mean"), coverage=("coverage", "mean")).round(3)
    overall["coverage"] = (overall["coverage"] * 100).round(1)
    print(overall.to_string())

    # 계절 도약이 큰 달만 따로 — 전월 대비 규모가 1.3배를 넘는 달
    jumps = [p for p in periods if p in ratio.index
             and pd.notna(ratio[p]) and (ratio[p] >= 1.3 or ratio[p] <= 0.77)]
    print(f"\n=== 계절 전환 달만 ({', '.join(jumps) if jumps else '없음'}) ===")
    if jumps:
        part = frame[frame["검증"].isin(jumps)]
        transition = part.groupby(["duration", "창"]).agg(
            mae=("mae", "mean"), bias=("bias", "mean"), coverage=("coverage", "mean")).round(3)
        transition["coverage"] = (transition["coverage"] * 100).round(1)
        print(transition.to_string())

        print("\n판정 (계절 전환 달, 커버리지 기준):")
        for duration in transition.index.get_level_values(0).unique():
            block = transition.loc[duration]
            best = block["coverage"].idxmax()
            print(f"  {duration}: 최선 '{best}' {block.loc[best, 'coverage']:.1f}%"
                  f" (현행 prev1 {block.loc['prev1', 'coverage']:.1f}%)"
                  f" · MAE {block.loc[best, 'mae']:.3f} vs {block.loc['prev1', 'mae']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
