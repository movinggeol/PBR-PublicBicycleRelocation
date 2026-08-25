"""분위수 모델이 `mu + z·sigma`를 이기는가 — 표본 밖 비교.

**채택 기준**(docs/분석/DEMAND_DISTRIBUTION.md 5장)을 그대로 적용한다.

  1. 작업 대상 대여소에서만 (`|prev_mu| > 2`)
  2. 평일과 휴일을 따로
  3. **표본 밖** — 마지막 달을 학습에서 빼고 그 달로만 평가한다
  4. 베이스라인을 못 이기면 채택하지 않는다

두 가지를 본다.

  · **커버리지** — target_qty가 실제 수요를 덮는 비율. 설계 의도는 95%.
  · **과잉(overshoot)** — 덮되 얼마나 넉넉히 덮는가. 커버리지만 보면
    "target을 무한대로" 두는 게 이기므로, 비용도 함께 봐야 한다.

실행: python experiments/quantile_model_eval.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

import db
import demand_model
from project_config import DAY_TYPE_LABELS, DAY_TYPES, TARGET_Z

TARGET_CUT = 2
DURATIONS = ("_05_10", "_10_15", "_15_20")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holdout", help="검증에 쓸 달 (기본: 가장 최근 달)")
    parser.add_argument("--warmup-days", type=int, default=14,
                        help="계절 보정 일수 (기본 14, 0이면 끔). 베이스라인에도 같이 적용")
    parser.add_argument("--quantile", type=float, default=demand_model.TARGET_QUANTILE,
                        help="학습 분위수 (기본 0.95). 실현 커버리지가 모자라면 올린다"
                             " — z를 1.65→1.99로 올린 것과 같은 보정")
    args = parser.parse_args()

    with db.session() as conn:
        periods = [r[0] for r in conn.execute(
            "SELECT DISTINCT period FROM net_demand ORDER BY period")]
        net = {p: db.load_frame(conn, "net_demand", period=p) for p in periods}

    ordered = sorted(net, key=demand_model.month_index)
    if len(ordered) < 3:
        print("기간이 3개 이상 있어야 학습/검증을 나눌 수 있습니다.")
        return 1

    holdout = args.holdout or ordered[-1]
    if holdout not in ordered:
        print(f"'{holdout}' 순수요가 없습니다. 가능한 값: {', '.join(ordered)}")
        return 1
    # 검증 달과 그 이후는 학습에서 뺀다 — 미래를 보고 배우면 안 된다.
    train_periods = [p for p in ordered
                     if demand_model.month_index(p) < demand_model.month_index(holdout)]
    if len(train_periods) < 2:
        print(f"'{holdout}' 이전 기간이 부족합니다.")
        return 1
    warm = f"warmup {args.warmup_days}일" if args.warmup_days else "warmup 없음"
    print(f"학습 {len(train_periods)}개월 ({train_periods[0]} ~ {train_periods[-1]})"
          f" · 검증 {holdout} (학습에서 제외) · {warm}\n")

    train_frame = demand_model.training_frame({p: net[p] for p in train_periods},
                                              warmup_days=args.warmup_days)
    if train_frame.empty:
        print("학습 데이터를 만들지 못했습니다.")
        return 1
    model = demand_model.train(train_frame, quantile=args.quantile)
    bundle = {"model": model, "quantile": args.quantile,
              "features": demand_model.FEATURES}

    # 검증: 직전 달로 피처를 만들고 holdout 달을 맞힌다
    eval_frame = demand_model.training_frame(
        {p: net[p] for p in (train_periods[-1], holdout)},
        warmup_days=args.warmup_days)
    if eval_frame.empty:
        print(f"검증 데이터를 만들지 못했습니다 — {train_periods[-1]} 다음이"
              f" {holdout}가 아닙니다(빠진 달이 있습니다).")
        print(f"  적재된 기간: {', '.join(ordered)}")
        return 1

    targets = eval_frame[eval_frame["prev_abs_mu"] > TARGET_CUT].copy()
    if targets.empty:
        print("작업 대상 대여소가 없습니다.")
        return 1

    # prev_mu·prev_sigma에는 이미 같은 계절 배율이 곱해져 있다(build_features).
    # 그래야 베이스라인과 모델이 같은 조건에서 겨룬다.
    targets["baseline"] = targets["prev_mu"] + TARGET_Z * targets["prev_sigma"]
    targets["model"] = bundle["model"].predict(
        targets[demand_model.FEATURES].to_numpy())

    print(f"{'요일':6} {'회차':8} {'표본':>7} "
          f"{'커버리지(기준)':>13} {'커버리지(모델)':>14} "
          f"{'과잉(기준)':>11} {'과잉(모델)':>11}")

    rows = []
    for day_index, day_type in enumerate(DAY_TYPES):
        for duration_index, duration in enumerate(DURATIONS):
            part = targets[(targets["day_type_idx"] == day_index)
                           & (targets["duration_idx"] == duration_index)]
            if len(part) < 50:
                continue
            actual = part["demand"]
            result = {"day": day_type, "duration": duration, "n": len(part)}
            for name in ("baseline", "model"):
                predicted = part[name]
                result[f"cov_{name}"] = float((actual <= predicted).mean())
                # 덮은 경우에만 얼마나 넉넉했는지 (비용)
                covered = predicted[actual <= predicted] - actual[actual <= predicted]
                result[f"over_{name}"] = float(covered.mean()) if len(covered) else np.nan
            rows.append(result)
            print(f"{DAY_TYPE_LABELS[day_type]:6} {duration:8} {len(part):7,} "
                  f"{result['cov_baseline'] * 100:12.1f}% "
                  f"{result['cov_model'] * 100:13.1f}% "
                  f"{result['over_baseline']:11.2f} {result['over_model']:11.2f}")

    if not rows:
        print("\n비교할 구간이 없습니다.")
        return 1

    summary = pd.DataFrame(rows)
    print(f"\n{'=' * 70}")
    print(f"평균 커버리지  기준 {summary['cov_baseline'].mean() * 100:.1f}%"
          f"  →  모델 {summary['cov_model'].mean() * 100:.1f}%  (목표 95%)")
    print(f"평균 과잉      기준 {summary['over_baseline'].mean():.2f}대"
          f"  →  모델 {summary['over_model'].mean():.2f}대  (낮을수록 덜 넉넉)")

    # 판정: 95%에서 얼마나 벗어났는가 + 과잉 비용
    base_gap = abs(summary["cov_baseline"].mean() - 0.95)
    model_gap = abs(summary["cov_model"].mean() - 0.95)
    print(f"\n95%에서 벗어난 정도  기준 {base_gap * 100:.1f}%p"
          f"  →  모델 {model_gap * 100:.1f}%p")

    # 비 온 날만 갈라 본다. 날씨가 주는 이득은 **그 날들에 몰려 있고**(docs/분석/WEATHER.md),
    # 전체 평균은 90%인 맑은 날에 희석돼 성격을 감춘다. 조건부로 쓸지(비 오는 날에만)
    # 판단하려면 이 줄이 필요하다.
    if "rainy" in targets.columns and targets["rainy"].notna().any():
        for label, part in (("비 온 날", targets[targets["rainy"] == 1]),
                            ("맑은 날", targets[targets["rainy"] == 0])):
            if len(part) < 50:
                continue
            actual = part["demand"]
            base_cov = float((actual <= part["baseline"]).mean())
            model_cov = float((actual <= part["model"]).mean())
            # 과잉은 '덮은 만큼 넉넉했던 대수' = 헛일의 크기다. 비 오는 날의 손실은
            # 결품(커버리지)이 아니라 이쪽으로 나타난다 — 필요량이 줄었는데 계획은
            # 그대로이므로 덮기는 덮되 과하게 덮는다.
            base_over = float((part["baseline"] - actual)[actual <= part["baseline"]].mean())
            model_over = float((part["model"] - actual)[actual <= part["model"]].mean())
            print(f"  {label} ({len(part):,}건)  커버리지 기준 {base_cov * 100:.1f}%"
                  f" → 모델 {model_cov * 100:.1f}%   |   과잉 기준 {base_over:.2f}대"
                  f" → 모델 {model_over:.2f}대")

    if model_gap < base_gap and summary["over_model"].mean() <= summary["over_baseline"].mean():
        print("\n판정: 모델이 커버리지와 과잉 **둘 다** 개선했다 → 채택 검토")
    elif model_gap < base_gap:
        print("\n판정: 커버리지는 좋아졌으나 과잉이 늘었다 → 맞바꿈을 따져야 한다")
    else:
        print("\n판정: 베이스라인을 이기지 못했다 → **채택하지 않는다**")
        print("  복잡도만 늘리는 셈이다. 피처를 더하거나(날씨·대여소 특성)"
              " 표본을 늘려 다시 시도할 것.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
