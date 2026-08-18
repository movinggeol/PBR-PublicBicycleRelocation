"""수요 예측 백테스트 — `mu`가 다음 달을 얼마나 맞히는가 (docs/KPI.md 5단계).

파이프라인의 모든 지표는 `target_qty`를 기준으로 계산되고, `target_qty`는
`mu`(평균 순수요)와 `sigma`에서 나온다. **그런데 `mu`가 맞는지는 검증된 적이 없다.**
`mu`가 틀리면 개선률이 높아도 의미가 없다.

이 도구는 한 달로 만든 `mu`가 다음 달 실제 순수요를 얼마나 맞히는지 잰다.

**평일과 휴일은 따로 잰다**(`--day-type`, 기본 weekday). 두 구분은 수요 구조가
달라 섞으면 학습·검증 양쪽이 오염된다(docs/steps/step0_raw.md).

실행:
    python tools/backtest_demand.py                       # 평일, 전 시간대
    python tools/backtest_demand.py --day-type holiday    # 휴일
    python tools/backtest_demand.py --duration _05_10     # 특정 시간대만
    python tools/backtest_demand.py --min-demand 2        # 작업 대상 대여소만
    python tools/backtest_demand.py --z 1.65              # 커버리지 판정 계수
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
from project_config import (
    DAY_TYPE_AUTO, DAY_TYPES, TARGET_Z, normalize_day_type, select_day_type,
)

DURATIONS = ["_05_10", "_10_15", "_15_20", "_20_05"]


def window_hours(duration: str) -> list:
    start, end = int(duration.split("_")[1]), int(duration.split("_")[2])
    return list(range(start, end)) if start < end else \
        list(range(start, 24)) + list(range(0, end))


def daily_window_demand(net: pd.DataFrame, duration: str) -> pd.DataFrame:
    """날짜·대여소별 해당 시간대 순수요 합 (calculate_target_qty와 같은 계산)."""
    columns = [f"net_{h:02d}" for h in window_hours(duration)]
    available = [c for c in columns if c in net.columns]
    return pd.DataFrame({
        "station_id": net["station_id"],
        "date": net["date"],
        "demand": net[available].sum(axis=1),
    })


def consecutive_pairs(periods: list) -> list:
    """이어지는 달끼리 (학습, 검증) 쌍을 만든다. 빠진 달은 건너뛴다."""
    def key(label):                       # '25년 11월' -> 2511
        year, month = label.split("년")
        return int(year.strip()) * 100 + int(month.replace("월", "").strip())

    ordered = sorted(periods, key=key)
    return [(a, b) for a, b in zip(ordered, ordered[1:]) if key(b) - key(a) == 1
            or (key(a) % 100 == 12 and key(b) - key(a) == 89)]


def evaluate(train: pd.DataFrame, test: pd.DataFrame, z: float,
             min_demand: float = 0.0, warmup_days: int = 0) -> dict:
    """학습 달의 mu/sigma로 검증 달을 예측하고 오차를 잰다.

    min_demand > 0이면 **작업 대상 대여소만** 본다(|mu| > min_demand).
    전체 평균은 파이프라인이 손대지도 않는 대여소에 희석돼 정반대 결론이 나온 적이
    있다(docs/EXPERIMENTS.md 2장) — 정확도를 인용할 때는 이 필터를 켜야 한다.
    """
    stats = train.groupby("station_id")["demand"].agg(mu="mean", sigma="std").fillna(0)
    global_mu = train["demand"].mean()
    if min_demand > 0:
        stats = stats[stats["mu"].abs() > min_demand]

    # 계절 수준 보정 — 검증 달 첫 N일만 본다. 운영에서 그 시점에 손에 있는 자료다.
    # 배율은 **작업 대상 필터 이전의 전체 대여소**로 구한다. 파이프라인이 그렇게
    # 하므로 여기서도 같아야 측정이 실제 동작을 반영한다(도시 전체 배율이라는
    # 정의에도 이쪽이 맞다).
    if warmup_days > 0:
        stats = demand_model.apply_warmup(
            stats, demand_model.season_ratio(train, test, warmup_days))

    merged = test.merge(stats, on="station_id", how="inner")
    if merged.empty:
        return {}

    actual = merged["demand"]
    error = actual - merged["mu"]

    return {
        "stations": int(merged["station_id"].nunique()),
        "samples": int(len(merged)),
        # mu 예측 오차
        "mae": float(error.abs().mean()),
        "rmse": float(np.sqrt((error ** 2).mean())),
        "bias": float(error.mean()),            # 양수면 실제가 예측보다 크다(과소예측)
        # 기준선 — 이보다 못하면 대여소별 평균이 값을 못 하는 것
        "mae_zero": float(actual.abs().mean()),
        "mae_global": float((actual - global_mu).abs().mean()),
        # target_qty = mu + z*sigma 가 실제 수요를 덮는 비율 (설계 의도는 약 95%)
        "coverage": float((actual <= merged["mu"] + z * merged["sigma"]).mean()),
        # 95%를 실제로 덮으려면 z가 얼마여야 하는지 (표준화 잔차의 95분위)
        "z_for_95": _required_z(merged, 0.95),
    }


def _required_z(merged: pd.DataFrame, target: float) -> float:
    """목표 커버리지를 달성하는 z를 실측에서 구한다.

    z=1.65는 정규분포를 가정한 값이다. 실제 순수요 분포의 꼬리가 더 두꺼우면
    이 값으로는 목표에 못 미친다.
    """
    usable = merged[merged["sigma"] > 0]
    if usable.empty:
        return float("nan")
    standardized = (usable["demand"] - usable["mu"]) / usable["sigma"]
    return float(standardized.quantile(target))


def main() -> int:
    parser = argparse.ArgumentParser(description="수요 예측(mu) 백테스트")
    parser.add_argument("--duration", help="시간대 하나만 (예: _05_10)")
    parser.add_argument("--z", type=float, default=TARGET_Z,
                        help=f"커버리지 판정 계수 (기본 {TARGET_Z})")
    parser.add_argument("--day-type", default="weekday",
                        choices=(*DAY_TYPES, DAY_TYPE_AUTO),
                        help="요일 구분 (기본 weekday). 평일과 휴일은 따로 잰다")
    parser.add_argument("--min-demand", type=float, default=0.0,
                        help="작업 대상만 보려면 2 (|mu| > 2). 기본 0 = 전체 대여소")
    parser.add_argument("--warmup-days", type=int, default=0,
                        help="검증 달 첫 N일로 계절 배율 보정 (기본 0 = 끔). 권장 14")
    args, _ = parser.parse_known_args()

    durations = [args.duration] if args.duration else DURATIONS
    day_type = normalize_day_type(args.day_type)

    with db.session() as conn:
        periods = [r[0] for r in conn.execute(
            "SELECT DISTINCT period FROM net_demand").fetchall()]
        if len(periods) < 2:
            print(f"기간이 {len(periods)}개뿐이라 백테스트할 수 없습니다.")
            print("여러 달의 순수요를 먼저 만드세요:")
            print('  python "step0 (raw데이터 처리)/raw_to_net.py" --period "25년 10월"')
            return 1
        net = {p: select_day_type(db.load_frame(conn, "net_demand", period=p),
                                  "date", day_type)
               for p in periods}

    empty = [p for p, frame in net.items() if frame.empty]
    if empty:
        print(f"'{day_type}' 데이터가 없는 기간: {', '.join(sorted(empty))}")
        print("  tools/rebuild_net_demand.py 로 순수요를 다시 만드세요"
              " (1.14.0 이전 산출물에는 휴일이 없습니다).\n")
        net = {p: f for p, f in net.items() if not f.empty}
        periods = sorted(net)
        if len(periods) < 2:
            return 1

    pairs = consecutive_pairs(periods)
    if not pairs:
        print(f"이어지는 달이 없습니다: {sorted(periods)}")
        return 1

    scope = f"작업 대상만(|mu| > {args.min_demand})" if args.min_demand else "전체 대여소"
    warm = f" · warmup {args.warmup_days}일" if args.warmup_days else ""
    print(f"기간 {len(periods)}개 · 연속 쌍 {len(pairs)}개 · z={args.z}"
          f" · 요일 {day_type} · {scope}{warm}\n")

    all_rows = []
    for duration in durations:
        rows = []
        for train_period, test_period in pairs:
            result = evaluate(daily_window_demand(net[train_period], duration),
                              daily_window_demand(net[test_period], duration),
                              args.z, args.min_demand, args.warmup_days)
            if result:
                rows.append({"duration": duration,
                             "학습": train_period, "검증": test_period, **result})
        if not rows:
            continue

        frame = pd.DataFrame(rows)
        all_rows.extend(rows)

        print(f"=== {duration} ===")
        display = frame[["학습", "검증", "mae", "rmse", "bias",
                         "mae_zero", "mae_global", "coverage"]].copy()
        for column in ["mae", "rmse", "bias", "mae_zero", "mae_global"]:
            display[column] = display[column].round(2)
        display["coverage"] = (display["coverage"] * 100).round(1)
        print(display.to_string(index=False))

        mae, zero, glob = frame["mae"].mean(), frame["mae_zero"].mean(), frame["mae_global"].mean()
        best = min(zero, glob)
        verdict = f"기준선 대비 {(1 - mae / best) * 100:+.1f}%" if best else "—"
        print(f"  평균 MAE {mae:.2f}대 (0 예측 {zero:.2f} / 전체평균 예측 {glob:.2f}) → {verdict}")
        print(f"  평균 커버리지 {frame['coverage'].mean() * 100:.1f}% (설계 의도 ~95%)"
              f" · 95%를 덮으려면 z={frame['z_for_95'].mean():.2f} 필요 (현재 {args.z})")
        print(f"  평균 편향 {frame['bias'].mean():+.2f}대"
              f" ({'과소예측' if frame['bias'].mean() > 0 else '과대예측'})\n")

    if all_rows:
        total = pd.DataFrame(all_rows)
        print("=== 전체 요약 ===")
        summary = total.groupby("duration").agg(
            MAE=("mae", "mean"), 기준선_0=("mae_zero", "mean"),
            편향=("bias", "mean"), 커버리지=("coverage", "mean"),
            필요_z=("z_for_95", "mean")).round(3)
        summary["기준선대비"] = ((1 - summary["MAE"] / summary["기준선_0"]) * 100).round(1)
        summary["커버리지"] = (summary["커버리지"] * 100).round(1)
        print(summary[["MAE", "기준선_0", "기준선대비", "편향", "커버리지", "필요_z"]].to_string())

        print("\n판정:")
        for duration, row in summary.iterrows():
            if row["기준선대비"] <= 0:
                print(f"  ⚠ {duration}: mu가 '0 예측'보다 나을 게 없다"
                      f" (MAE {row['MAE']:.2f} vs {row['기준선_0']:.2f})."
                      f" 이 시간대의 target_qty는 근거가 약하다.")
            else:
                print(f"  {duration}: 기준선 대비 {row['기준선대비']:+.1f}% ·"
                      f" 커버리지 {row['커버리지']:.1f}%")
        print(f"\n  참고: 월별 '필요 z'의 평균은 {total['z_for_95'].mean():.2f}지만,"
              f" 그 값을 실제로 적용했을 때의 커버리지는 따로 확인해야 한다.")
        print(f"  커버리지는 z에 대해 비선형이라 두 값이 일치하지 않는다"
              f" — python experiments/z_sweep.py 로 z별 실제 커버리지를 볼 수 있다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
