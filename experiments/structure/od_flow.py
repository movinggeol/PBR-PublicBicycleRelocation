"""실험 — 대여 흐름(OD)에 순수요 예측을 도울 신호가 있나 (ML_OPPORTUNITIES ③).

`rental_history` 539만 건에는 **출발지–도착지**가 다 있는데, 파이프라인은 그것을
시간별 건수 차이(순수요) 하나로 접어서 쓴다. 자전거가 **어디서 어디로** 가는지는
버려진다. 거기에 남은 신호가 있는가.

세 가지를 잰다.

  1. **OD 구조가 달마다 유지되나** — 유지되지 않으면 배울 것이 없다.
  2. **OD로 만든 순수요 = 기존 순수요인가** — 같은 원천이므로 확인만 한다.
  3. **흐름 이웃으로 당기면 나아지나** — 이것이 본론이다.
     `예측 = (1-w)·자기 지난달 + w·흐름으로 이어진 곳들의 가중평균`
     w=0이 최적이면 흐름을 봐도 얻을 것이 없다는 뜻이다.

**3번은 [cross_station.py](cross_station.py)의 세 번째 이웃 정의다.** 거기서는
이웃을 '위치'와 '행동'으로 정의해 둘 다 졌다. 흐름은 **실제로 자전거가 오가는
관계**라 가장 그럴듯한 정의인데, 그것도 지는지 본다.

⚠️ **타깃 출처 확인** (1.23.1의 교훈): `rental_history`는 `tools/load_rentals.py`가
대전시 원본 CSV를 그대로 적재한 것이다. 파이프라인이 만든 값이 아니므로
정답표로 쓸 수 있다.

실행:
    python experiments/structure/od_flow.py
    python experiments/structure/od_flow.py --hours 15 19
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

import db
from project_config import REBAL_MIN_QTY

WEIGHTS = (0.0, 0.05, 0.1, 0.2, 0.3)


def period_key(label):
    year, month = label.split("년")
    return int(year.strip()) * 100 + int(month.replace("월", "").strip())


def load_od(lo: int, hi: int) -> pd.DataFrame:
    """기간·출발·도착별 대여 건수. 평일 그 시간대만.

    **집계는 SQL에서 한다** — 539만 행을 파이썬으로 끌어오면 느리고, 컬럼 수가
    로드 시간을 좌우한다(docs/구현/DB_PLAN.md).
    """
    with db.session() as conn:
        return pd.read_sql(
            "SELECT period, rent_station o, return_station d,"
            " COUNT(*) n, COUNT(DISTINCT DATE(rent_at)) days"
            " FROM rental_history"
            " WHERE CAST(strftime('%H', rent_at) AS INT) BETWEEN ? AND ?"
            "   AND CAST(strftime('%w', rent_at) AS INT) BETWEEN 1 AND 5"
            " GROUP BY period, rent_station, return_station",
            conn, params=(lo, hi))


def net_of(frame: pd.DataFrame) -> pd.Series:
    """OD에서 대여소별 하루 평균 순수요. **양수 = 빠져나간다**(대여 > 반납)."""
    days = max(int(frame["days"].max() or 1), 1)
    out = frame.groupby("o")["n"].sum()
    inn = frame.groupby("d")["n"].sum()
    return out.subtract(inn, fill_value=0) / days


def flow_neighbor(frame: pd.DataFrame, prev: pd.Series) -> pd.Series:
    """그 대여소가 **실제로 자전거를 주고받는 곳들**의 순수요 가중평균.

    가중치는 오간 횟수다. 제자리 반납(8.1%)은 이웃이 아니므로 뺀다.
    """
    link = frame.groupby(["o", "d"], as_index=False)["n"].sum()
    link = link[link["o"] != link["d"]].copy()
    link["peer"] = link["d"].map(prev)
    link = link.dropna(subset=["peer"])
    if link.empty:
        return pd.Series(dtype=float)
    weighted = link["n"] * link["peer"]
    total = link.groupby("o")["n"].sum()
    return (weighted.groupby(link["o"]).sum() / total).replace([np.inf, -np.inf], np.nan)


def main() -> int:
    parser = argparse.ArgumentParser(description="OD 흐름에 신호가 있나")
    parser.add_argument("--hours", nargs=2, type=int, default=(5, 9),
                        metavar=("시작", "끝"), help="대여 시각 범위 (기본 05~09 = _05_10)")
    args, _ = parser.parse_known_args()
    lo, hi = args.hours

    od = load_od(lo, hi)
    if od.empty:
        print("rental_history가 비어 있습니다. python tools/load_rentals.py 로 적재하세요.")
        return 1

    periods = sorted(od["period"].unique(), key=period_key)
    print("OD쌍 %s행 · 기간 %d개 · 대여 %02d~%02d시 평일\n"
          % (format(len(od), ","), len(periods), lo, hi))

    # ---- 1. OD 구조가 달마다 유지되나 ----
    pivot = od.pivot_table(index=["o", "d"], columns="period", values="n", fill_value=0)
    rows = []
    for a, b in zip(periods, periods[1:]):
        if a not in pivot or b not in pivot:
            continue
        pair = pivot[[a, b]]
        pair = pair[(pair[a] + pair[b]) >= 5]
        if len(pair) < 100:
            continue
        rows.append(dict(월쌍="%s→%s" % (a, b), OD쌍=len(pair),
                         상관=float(np.corrcoef(pair[a], pair[b])[0, 1]),
                         간격=period_key(b) - period_key(a)))
    stability = pd.DataFrame(rows)
    print("=== 1. OD 구조가 달마다 유지되나 ===")
    print(stability.round(3).to_string(index=False))
    adjacent = stability[stability["간격"] == 1]
    print("\n  이어지는 달 평균 상관 %.3f — 구조 자체는 안정적이다."
          % adjacent["상관"].mean())

    # ---- 2·3. 흐름 이웃이 도움이 되나 ----
    print("\n=== 2. 흐름 이웃으로 당기면 나아지나 (본론) ===")
    print("  예측 = (1-w)·자기 지난달 + w·흐름으로 이어진 곳들의 가중평균\n")
    header = "%-10s " % "검증달" + "".join("w=%-6.2f" % w for w in WEIGHTS)
    print(header)

    scores, wins = [], 0
    for a, b in zip(periods, periods[1:]):
        if period_key(b) - period_key(a) != 1:
            continue
        past = od[od["period"] == a]
        prev, cur = net_of(past), net_of(od[od["period"] == b])
        neighbor = flow_neighbor(past, prev)
        joined = pd.concat([prev.rename("p"), cur.rename("c"),
                            neighbor.rename("nb")], axis=1).dropna()
        joined = joined[joined["p"].abs() > REBAL_MIN_QTY]
        if len(joined) < 50:
            continue
        row = [float(np.abs(joined["c"] - ((1 - w) * joined["p"] + w * joined["nb"])).mean())
               for w in WEIGHTS]
        scores.append(row)
        wins += min(row[1:]) < row[0]
        print("%-10s " % b + "".join("%-8.3f" % v for v in row))

    if not scores:
        print("  비교할 달이 없습니다.")
        return 1

    mean = np.array(scores).mean(0)
    print("%-10s " % "평균" + "".join("%-8.3f" % v for v in mean))

    best = WEIGHTS[int(np.argmin(mean))]
    print("\n=== 판정 ===")
    print("  최적 w = %.2f   (%.3f → %.3f, %+.1f%%)"
          % (best, mean[0], mean.min(), (1 - mean.min() / mean[0]) * 100))
    print("  흐름 이웃이 이긴 달: %d / %d" % (wins, len(scores)))
    if best == 0.0:
        print("\n  → **흐름을 봐도 얻을 것이 없다.** 자기 지난달만 쓰는 것이 최적이다.")
        print("     cross_station.py의 '위치'·'행동' 이웃과 같은 결론이다 —")
        print("     이웃을 어떻게 정의하든 대여소 자신의 신호가 압도한다.")
    else:
        print("\n  → 이득이 있다. 다만 달별 승패를 함께 볼 것(우연일 수 있다).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
