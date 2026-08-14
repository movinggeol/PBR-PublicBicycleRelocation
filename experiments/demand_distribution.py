"""순수요는 정규분포인가 — `target_qty = mu + z·sigma`의 전제를 검증한다.

파이프라인은 목표 재고를 `mu + z·sigma`로 잡는다. 이 공식은 **순수요가 정규분포**라는
전제 위에 서 있다. z=1.65가 "95%를 덮는다"는 말도 정규분포에서만 참이다.

이 스크립트는 그 전제를 네 각도에서 깨 본다.

  1. 모양   — 왜도(skewness)·첨도(kurtosis)가 정규분포(0, 0)에서 얼마나 먼가
  2. 꼬리   — 표준화 잔차의 실제 분위수 vs 정규분포 분위수
  3. 이산성 — 순수요는 정수다. 0의 비율이 얼마나 되나
  4. 등분산 — sigma가 대여소마다 다른가(=하나의 z로 덮을 수 있나)

**표본이 수십만이라 정규성 검정의 p값은 의미가 없다**(무엇이든 기각된다).
그래서 p값이 아니라 **효과 크기**(왜도·첨도·분위수 차이)를 본다.

실행: python experiments/demand_distribution.py
      python experiments/demand_distribution.py --day-type holiday
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
from scipy import stats as sps

import db
from project_config import DAY_TYPES, TARGET_Z, normalize_day_type, select_day_type

WINDOWS = {"_05_10": range(5, 10), "_10_15": range(10, 15), "_15_20": range(15, 20)}
TARGET_CUT = 2          # 파이프라인이 실제로 손대는 대여소 (|mu| > 2)

# 정규분포의 분위수 — 실측과 비교할 기준
NORMAL_Q = {0.90: 1.282, 0.95: 1.645, 0.99: 2.326}


def window_demand(net: pd.DataFrame, hours) -> pd.DataFrame:
    """날짜·대여소별 시간대 순수요 (calculate_target_qty와 같은 계산)."""
    columns = [f"net_{h:02d}" for h in hours]
    available = [c for c in columns if c in net.columns]
    return pd.DataFrame({
        "station_id": net["station_id"],
        "date": net["date"],
        "demand": net[available].sum(axis=1),
    })


def standardized(frame: pd.DataFrame, targets_only: bool) -> pd.Series:
    """대여소별로 (x − mu) / sigma. 정규분포라면 표준정규를 따라야 한다."""
    grouped = frame.groupby("station_id")["demand"]
    stats = grouped.agg(mu="mean", sigma="std")
    if targets_only:
        stats = stats[stats["mu"].abs() > TARGET_CUT]
    stats = stats[stats["sigma"] > 0]
    merged = frame.merge(stats, on="station_id", how="inner")
    return (merged["demand"] - merged["mu"]) / merged["sigma"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day-type", default="weekday", choices=DAY_TYPES)
    parser.add_argument("--all-stations", action="store_true",
                        help="작업 대상(|mu|>2) 외 전체 대여소도 함께 본다")
    args = parser.parse_args()
    day_type = normalize_day_type(args.day_type)

    with db.session() as conn:
        periods = [r[0] for r in conn.execute(
            "SELECT DISTINCT period FROM net_demand ORDER BY period")]
        frames = []
        for period in periods:
            net = select_day_type(
                db.load_frame(conn, "net_demand", period=period), "date", day_type)
            if not net.empty:
                frames.append(net)

    if not frames:
        print("순수요 데이터가 없습니다. tools/rebuild_net_demand.py 를 먼저 돌리세요.")
        return 1

    net_all = pd.concat(frames, ignore_index=True)
    print(f"기간 {len(frames)}개 · 요일 {day_type} · 행 {len(net_all):,}\n")

    scopes = [("작업 대상", True)] + ([("전체", False)] if args.all_stations else [])

    for label, targets_only in scopes:
        print(f"{'=' * 74}\n{label} 대여소\n{'=' * 74}")

        print("\n[1] 분포 모양 — 정규분포는 왜도 0, 초과첨도 0")
        print(f"{'회차':8} {'표본':>9} {'평균':>7} {'표준편차':>8} "
              f"{'왜도':>7} {'초과첨도':>8} {'0의 비율':>8}")
        for duration, hours in WINDOWS.items():
            demand = window_demand(net_all, hours)
            if targets_only:
                mu = demand.groupby("station_id")["demand"].transform("mean")
                demand = demand[mu.abs() > TARGET_CUT]
            values = demand["demand"]
            print(f"{duration:8} {len(values):9,} {values.mean():7.2f} "
                  f"{values.std():8.2f} {sps.skew(values):7.2f} "
                  f"{sps.kurtosis(values):8.2f} "
                  f"{(values == 0).mean() * 100:7.1f}%")

        print("\n[2] 꼬리 — 표준화 잔차 (x − mu)/sigma 의 분위수")
        print(f"{'회차':8} " + " ".join(f"{f'{int(q*100)}%':>16}" for q in NORMAL_Q)
              + f" {'최대':>7}")
        print(f"{'(정규)':8} "
              + " ".join(f"{NORMAL_Q[q]:>16.3f}" for q in NORMAL_Q) + f" {'—':>7}")
        for duration, hours in WINDOWS.items():
            z = standardized(window_demand(net_all, hours), targets_only)
            if z.empty:
                continue
            cells = []
            for q, normal in NORMAL_Q.items():
                actual = z.quantile(q)
                cells.append(f"{actual:8.3f}({actual - normal:+.2f})")
            print(f"{duration:8} " + " ".join(f"{c:>16}" for c in cells)
                  + f" {z.max():7.1f}")

        print("\n[3] z를 적용했을 때 실제로 덮이는 비율 (설계 의도 95%)")
        print(f"{'회차':8} {'z=1.65':>9} {f'z={TARGET_Z}':>9} {'z=2.5':>9} "
              f"{'95%에 필요한 z':>14}")
        for duration, hours in WINDOWS.items():
            z = standardized(window_demand(net_all, hours), targets_only)
            if z.empty:
                continue
            print(f"{duration:8} "
                  + " ".join(f"{(z <= v).mean() * 100:8.1f}%"
                             for v in (1.65, TARGET_Z, 2.5))
                  + f" {z.quantile(0.95):14.2f}")

        print("\n[4] 등분산인가 — 대여소별 sigma의 분포")
        print(f"{'회차':8} {'sigma 최소':>10} {'중앙값':>8} {'최대':>8} "
              f"{'최대/중앙':>9} {'sigma~|mu| 상관':>14}")
        for duration, hours in WINDOWS.items():
            demand = window_demand(net_all, hours)
            per = demand.groupby("station_id")["demand"].agg(mu="mean", sigma="std").dropna()
            if targets_only:
                per = per[per["mu"].abs() > TARGET_CUT]
            per = per[per["sigma"] > 0]
            if per.empty:
                continue
            corr = float(np.corrcoef(per["mu"].abs(), per["sigma"])[0, 1])
            print(f"{duration:8} {per['sigma'].min():10.2f} "
                  f"{per['sigma'].median():8.2f} {per['sigma'].max():8.2f} "
                  f"{per['sigma'].max() / per['sigma'].median():9.1f} {corr:14.3f}")
        print()

    # ---- 표본 내 vs 표본 밖 ----
    # 위 [2][3]은 **같은 달**로 mu/sigma를 만들고 그 달의 잔차를 본 것이다(표본 내).
    # 파이프라인은 지난달 통계로 **이번 달**을 덮어야 하므로(표본 밖) 둘을 갈라야
    # 한다 — 커버리지가 모자란 원인이 '분포 꼬리'인지 '추정 오차'인지가 갈린다.
    print(f"{'=' * 74}\n[5] 표본 내 vs 표본 밖 — 커버리지가 모자란 진짜 이유\n{'=' * 74}")
    print(f"{'회차':8} {'표본내 95% z':>12} {'표본밖 95% z':>12} {'차이':>7}  해석")

    ordered = []
    for period in periods:
        with db.session() as conn:
            frame = select_day_type(
                db.load_frame(conn, "net_demand", period=period), "date", day_type)
        if not frame.empty:
            ordered.append((period, frame))

    for duration, hours in WINDOWS.items():
        inside = standardized(window_demand(net_all, hours), True)

        residuals = []
        for (_, train), (_, test) in zip(ordered, ordered[1:]):
            train_d = window_demand(train, hours)
            test_d = window_demand(test, hours)
            stats = train_d.groupby("station_id")["demand"].agg(mu="mean", sigma="std")
            stats = stats[(stats["mu"].abs() > TARGET_CUT) & (stats["sigma"] > 0)]
            merged = test_d.merge(stats, on="station_id", how="inner")
            if not merged.empty:
                residuals.append((merged["demand"] - merged["mu"]) / merged["sigma"])
        if not residuals or inside.empty:
            continue

        outside = pd.concat(residuals, ignore_index=True)
        a, b = inside.quantile(0.95), outside.quantile(0.95)
        reason = "분포 꼬리" if a > 1.7 else "추정 오차(다음 달에 mu·sigma가 달라짐)"
        print(f"{duration:8} {a:12.2f} {b:12.2f} {b - a:+7.2f}  {reason}")

    print("\n읽는 법")
    print("  [1] 왜도가 0에서 멀면 좌우 비대칭, 초과첨도가 0보다 크면 꼬리가 두껍다.")
    print("  [2] 실측 분위수가 정규분포보다 크면 그 지점에서 과소평가하고 있다는 뜻이다.")
    print("  [3] z=1.65가 95%를 못 덮으면 '정규분포에서 95%'라는 근거가 깨진 것이다.")
    print("  [4] sigma가 대여소마다 크게 다르면 하나의 z로 모두를 덮을 수 없다.")
    print("  [5] 표본 내에서 이미 z가 크면 분포 자체의 문제,")
    print("      표본 내는 괜찮은데 표본 밖에서만 커지면 추정이 흔들리는 문제다.")
    print("      **대응이 완전히 다르다** — 앞이면 분포를 바꾸고, 뒤면 추정을 안정화한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
