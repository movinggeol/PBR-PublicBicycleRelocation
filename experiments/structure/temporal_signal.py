"""실험 — 순수요에 '시계열 기억'이 있는가 (LSTM 같은 것을 붙일 값어치가 있나).

**질문**: 1년치가 있으면 LSTM처럼 그 중에서 골라 쓸 수 없나? 효과가 좋을까?

LSTM·GRU 같은 시퀀스 모델이 이기려면 **어제가 오늘을 말해 주는 것**이 있어야
한다. 그것이 없으면 아무리 층을 쌓아도 배울 것이 없다. 그래서 모델을 짜기 전에
**신호가 있는지부터** 잰다.

세 가지를 잰다.

  1. **자기상관** — lag 1·2·3·5·10일. 시퀀스 모델이 먹고 사는 신호다.
  2. **과거 평균과의 상관** — 지금 방법(`mu + z·sigma`)이 이미 쓰는 신호.
     자기상관이 이것보다 약하면 **시퀀스로 얻을 것이 없다**.
  3. **대여소 평균을 뺀 잔차의 자기상관** — 여기에만 '추가로' 배울 것이 있다.
     대여소 간 수준 차이는 지금 방법이 이미 대여소별 `mu`로 잡고 있기 때문이다.

**판정 기준**: 잔차 자기상관이 약하면(|r| < 0.3 안팎) 시퀀스 모델을 붙여도
지금 방법을 이기기 어렵다. 붙일 값어치는 **잔차에 남은 기억의 크기**로 판단한다.

실행:
    python experiments/structure/temporal_signal.py
    python experiments/structure/temporal_signal.py --day-type holiday
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
from project_config import DAY_TYPES, DURATIONS, REBAL_MIN_QTY, normalize_day_type, select_day_type
from backtest_demand import daily_window_demand


LAGS = (1, 2, 3, 5, 10)


def corr(a: pd.Series, b: pd.Series) -> float:
    """두 열의 상관. 표본이 모자라거나 한쪽이 상수면 NaN."""
    if len(a) < 30 or a.std() == 0 or b.std() == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def panel(net: dict, duration: str) -> pd.DataFrame:
    """(대여소, 날짜) 패널. 대여소별로 날짜순 정렬해 lag를 붙일 수 있게 한다."""
    frames = [daily_window_demand(v, duration) for v in net.values() if not v.empty]
    if not frames:
        return pd.DataFrame()
    frame = pd.concat(frames, ignore_index=True)
    frame["date"] = pd.to_datetime(frame["date"])
    return frame.sort_values(["station_id", "date"]).reset_index(drop=True)


def measure(frame: pd.DataFrame) -> dict:
    """자기상관·과거평균·잔차 자기상관을 한 번에 낸다."""
    out = {}
    grouped = frame.groupby("station_id")["demand"]

    for lag in LAGS:
        shifted = grouped.shift(lag)
        sub = frame.assign(prev=shifted).dropna(subset=["prev"])
        out[f"lag{lag}"] = corr(sub["demand"], sub["prev"])

    # 지금 방법이 쓰는 신호 — 그 대여소의 '지금까지의 평균'
    # (expanding이므로 미래를 보지 않는다)
    cum = grouped.transform(lambda s: s.shift(1).expanding().mean())
    sub = frame.assign(cum=cum).dropna(subset=["cum"])
    out["past_mean"] = corr(sub["demand"], sub["cum"])

    # 대여소 수준을 뺀 뒤 남는 기억 — 시퀀스 모델이 '추가로' 얻을 수 있는 전부
    mu = grouped.transform("mean")
    resid = frame["demand"] - mu
    work = frame.assign(resid=resid)
    for lag in (1, 2, 3):
        prev = work.groupby("station_id")["resid"].shift(lag)
        sub = work.assign(prev=prev).dropna(subset=["prev"])
        out[f"resid_lag{lag}"] = corr(sub["resid"], sub["prev"])

    total = frame["demand"].var()
    between = frame.groupby("station_id")["demand"].mean().var()
    out["level_share"] = float(between / total * 100) if total else float("nan")
    out["n"] = int(len(frame))
    out["stations"] = int(frame["station_id"].nunique())
    out["days"] = int(frame["date"].nunique())
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="순수요에 시계열 기억이 있는가")
    parser.add_argument("--day-type", default=DAY_TYPES[0])
    args, _ = parser.parse_known_args()
    day_type = normalize_day_type(args.day_type)

    with db.session() as conn:
        periods = [r[0] for r in conn.execute(
            "SELECT DISTINCT period FROM net_demand").fetchall()]
        net = {}
        for p in periods:
            frame = select_day_type(db.load_frame(conn, "net_demand", period=p),
                                    "date", day_type)
            if not frame.empty:
                net[p] = frame

    if len(net) < 2:
        print("기간이 모자랍니다.")
        return 1

    print("적재 기간 %d개 · %s" % (len(net), day_type))
    print("기간: %s\n" % ", ".join(sorted(net)))

    rows = []
    for duration in DURATIONS:
        frame = panel(net, duration)
        if frame.empty:
            continue
        rows.append(dict(duration=duration, **measure(frame)))

    if not rows:
        print("잴 것이 없습니다.")
        return 1

    table = pd.DataFrame(rows).set_index("duration")

    print("=== 1. 자기상관 — 시퀀스 모델이 먹고 사는 신호 ===")
    print(table[[f"lag{l}" for l in LAGS]].round(3).to_string())
    print("\n  lag가 늘어도 값이 안 떨어지면 그것은 '기억'이 아니라")
    print("  대여소마다 수준이 다른 것(=지금 방법이 이미 잡는 것)이다.")

    print("\n=== 2. 지금 방법이 쓰는 신호와 견주면 ===")
    cmp = table[["lag1", "past_mean"]].copy()
    cmp["차이"] = cmp["lag1"] - cmp["past_mean"]
    print(cmp.round(3).to_string())
    if (cmp["차이"] < 0).all():
        print("\n  → **모든 시간대에서 '어제'가 '과거 평균'보다 약하다.**")
        print("     어제 하나를 보는 것은 지금 방법보다 못하다.")

    print("\n=== 3. 대여소 수준을 뺀 뒤 남는 기억 (추가로 배울 수 있는 전부) ===")
    print(table[["resid_lag1", "resid_lag2", "resid_lag3", "level_share"]]
          .round(3).to_string())
    print("\n  level_share = 분산 중 '대여소 간 수준 차이'가 설명하는 몫(%)")
    print("  이 몫은 지금 방법이 대여소별 mu로 이미 잡고 있다.")

    worst = table["resid_lag1"].max()
    print("\n=== 판정 ===")
    print("잔차 자기상관 lag1: 최대 %.3f" % worst)
    if worst < 0.35:
        print("→ **시퀀스 모델(LSTM 등)을 붙일 값어치가 낮다.**")
        print("   대여소 수준을 빼고 나면 어제가 오늘을 말해 주는 정도가 약하다.")
        print("   층을 쌓아도 배울 것이 별로 없다.")
    else:
        print("→ 남은 기억이 제법 있다. 시퀀스 모델을 재 볼 만하다.")

    print("\n표본: 대여소 %d곳 · 날짜 %d일 · 행 %s"
          % (table["stations"].iloc[0], table["days"].iloc[0],
             format(int(table["n"].iloc[0]), ",")))
    print("※ 작업 대상(|순수요|>%d)만 보면 대여소당 날짜가 크게 줄어든다 —"
          % REBAL_MIN_QTY)
    print("   시퀀스 학습은 연속된 날짜를 요구하므로 이 점이 더 불리하다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
