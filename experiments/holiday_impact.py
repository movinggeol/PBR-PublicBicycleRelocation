"""공휴일을 평일에서 빼면 평일 계획이 얼마나 달라지나 (1.14.1).

1.14.1 이전의 '평일'은 **월~금**이었다. 설·추석 연휴처럼 평일 자리에 걸린 공휴일이
그대로 섞여 있었고(25년 10월은 18일 중 5일), 그 날들의 낮은 수요가 평균을 끌어내려
**평일 재배치 수요를 과소평가**하고 있었다.

  옛 규칙: 평일 = 월~금
  새 규칙: 평일 = 월~금 중 공휴일이 아닌 날 (holidays 패키지)

같은 net_demand를 두 규칙으로 각각 집계해 mu·sigma·target_qty와 작업 대상 수를
비교한다. 실행: python experiments/holiday_impact.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

import db
from project_config import TARGET_Z, holiday_mask

WINDOWS = {"_05_10": range(5, 10), "_10_15": range(10, 15), "_15_20": range(15, 20)}


def stats(frame, hours):
    cols = [f"net_{h:02d}" for h in hours]
    work = frame.copy()
    work["net"] = work[cols].sum(axis=1)
    agg = work.groupby("station_id")["net"].agg(["mean", "std"]).fillna(0)
    agg["target"] = agg["mean"] + TARGET_Z * agg["std"]
    return agg


print(f"{'기간':11} {'회차':8} {'옛평일':>6} {'새평일':>6} "
      f"{'mu 변화율':>9} {'sigma 변화율':>11} {'target 변화율':>12} {'대상 옛→새':>12}")

with db.session() as conn:
    periods = [r[0] for r in conn.execute(
        "SELECT DISTINCT period FROM net_demand ORDER BY period")]
    for period in periods:
        net = db.load_frame(conn, "net_demand", period=period)
        if net.empty:
            continue
        dates = pd.to_datetime(net["date"])
        old_rule = dates.dt.dayofweek < 5                 # 공휴일이 섞인 옛 평일
        new_rule = ~holiday_mask(net["date"])             # 공휴일을 뺀 새 평일
        if old_rule.sum() == new_rule.sum():
            continue                                      # 평일 자리에 공휴일이 없던 달

        for duration, hours in WINDOWS.items():
            old = stats(net[old_rule], hours)
            new = stats(net[new_rule], hours)
            joined = old.join(new, lsuffix="_o", rsuffix="_n", how="inner")

            def rate(a, b):
                base = joined[a].abs().sum()
                return (joined[b].abs().sum() - base) / base * 100 if base else 0.0

            old_t = int((joined["mean_o"].abs() > 2).sum())
            new_t = int((joined["mean_n"].abs() > 2).sum())
            print(f"{period:11} {duration:8} "
                  f"{int(old_rule.sum() / net['station_id'].nunique() or 0):6d} "
                  f"{int(new_rule.sum() / net['station_id'].nunique() or 0):6d} "
                  f"{rate('mean_o', 'mean_n'):8.1f}% {rate('std_o', 'std_n'):10.1f}% "
                  f"{rate('target_o', 'target_n'):11.1f}% "
                  f"{f'{old_t}→{new_t}':>12}")
