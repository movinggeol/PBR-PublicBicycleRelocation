"""실험 — 이동시간을 상수 속도 대신 예측하면 얼마나 나아지나.

`VEHICLE_SPEED_KMPH = 25`가 ILP·VRP·시간예산 판정에 모두 쓰인다. 그런데
TMAP이 준 **실제 도로 소요시간**과 대 보면 중앙값은 19 km/h이고, 무엇보다
**속도가 상수가 아니라 거리에 따라 변한다**(짧은 구간 7.7 → 긴 구간 24.0 km/h).
출발·정지·신호가 거리와 무관하게 들어가기 때문이다.

세 가지를 견준다.

  · 상수      현행. `분 = 거리 / 25 * 60`
  · 선형      `분 = a + b·거리` — 절편이 '정차 비용'이다
  · GBM       거리 + 좌표 + 시간대. **어느 구역이 느린지**를 배운다

**평가는 학습에 없던 OD쌍에서만 한다.** 같은 대여소 쌍이 48% 반복 등장하므로
실행 단위로만 나누면 외운 것을 다시 맞히게 된다 — TMAP 응답을 캐시한 것과
같아져서 '예측이 좋아졌다'고 말할 수 없다.

⚠️ 이동시간 상수를 바꿀 때는 **ILP와 VRP가 반드시 같은 함수를 써야 한다**
(지금 상수를 공유하는 것과 같은 이유 — 두 단계가 다른 값을 쓰면 ILP가 최소
비용이라고 고른 조합이 VRP에서는 최소가 아니게 된다).

실행:
    python experiments/structure/travel_time_model.py
    python experiments/structure/travel_time_model.py --folds 10
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
from project_config import TIME_BUDGET_MINUTES, VEHICLE_SPEED_KMPH

EARTH_KM = 6371.0


def legs() -> pd.DataFrame:
    """vrp_plan에서 구간 단위 (거리, 실제 소요시간)를 뽑는다.

    `cum_sec`은 누적이므로 앞 행과 빼야 그 구간의 소요가 된다. 군집마다
    새로 시작하므로 (실행, 시간대, 군집)으로 묶어 차분한다.
    """
    with db.session() as conn:
        frame = pd.read_sql(
            "SELECT run_label, duration, cluster, seq, distance_km, cum_sec,"
            " from_id, to_id, from_lat, from_lon, to_lat, to_lon"
            " FROM vrp_plan WHERE distance_km > 0", conn)
    if frame.empty:
        return frame

    frame = frame.sort_values(["run_label", "duration", "cluster", "seq"])
    prev = frame.groupby(["run_label", "duration", "cluster"])["cum_sec"].shift(1)
    frame["leg_sec"] = frame["cum_sec"] - prev.fillna(0)

    frame = frame[(frame["leg_sec"] > 0) & (frame["distance_km"] > 0.05)].copy()
    frame["minutes"] = frame["leg_sec"] / 60.0
    speed = frame["distance_km"] / (frame["leg_sec"] / 3600.0)
    # 1km/h 미만·80km/h 초과는 TMAP 응답 이상치로 본다(도심 배송 차량이다)
    frame = frame[(speed > 1) & (speed < 80)].copy()
    frame["speed"] = frame["distance_km"] / (frame["leg_sec"] / 3600.0)

    lat1, lon1, lat2, lon2 = (np.radians(frame[c].to_numpy())
                              for c in ("from_lat", "from_lon", "to_lat", "to_lon"))
    hav = (np.sin((lat2 - lat1) / 2) ** 2
           + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    frame["straight_km"] = 2 * EARTH_KM * np.arcsin(np.sqrt(hav))
    frame["pair"] = frame["from_id"] + ">" + frame["to_id"]
    frame["dur_idx"] = frame["duration"].astype("category").cat.codes
    return frame


def main() -> int:
    parser = argparse.ArgumentParser(description="이동시간 예측이 상수를 이기나")
    parser.add_argument("--folds", type=int, default=5, help="OD쌍 교차검증 겹 수")
    args, _ = parser.parse_known_args()

    frame = legs()
    if frame.empty or len(frame) < 200:
        print("vrp_plan에 구간이 모자랍니다. 파이프라인을 몇 번 돌린 뒤 다시 보세요.")
        return 1

    print("구간 %s개 · OD쌍 %s개 (같은 쌍 반복 %.0f%%) · 현행 상수 %.0f km/h\n"
          % (format(len(frame), ","), format(frame["pair"].nunique(), ","),
             (1 - frame["pair"].nunique() / len(frame)) * 100, VEHICLE_SPEED_KMPH))

    print("=== 1. 속도는 상수가 아니다 ===")
    bins = pd.cut(frame["distance_km"], [0, .5, 1, 2, 4, 8, 1e9],
                  labels=["~0.5", "0.5~1", "1~2", "2~4", "4~8", "8+"])
    table = frame.groupby(bins, observed=True).agg(
        구간수=("speed", "size"), 속도중앙=("speed", "median"),
        소요중앙_분=("minutes", "median"))
    print(table.round(2).to_string())
    print("\n  짧은 구간일수록 느리다 — 출발·정지·신호가 거리와 무관하게 들어간다.")
    print("  고정 속도 모델은 **구조적으로** 틀린다.")

    try:
        from sklearn.ensemble import HistGradientBoostingRegressor
    except ImportError:
        HistGradientBoostingRegressor = None
        print("\n[안내] sklearn이 없어 GBM은 건너뜁니다.")

    # OD쌍 단위로 나눈다 — 실행 단위로 나누면 외운 쌍을 다시 맞히게 된다
    pairs = pd.Series(frame["pair"].unique())
    pairs = pairs.sample(frac=1.0, random_state=0).to_numpy()
    folds = np.array_split(pairs, max(2, args.folds))

    scores = {"상수 %.0fkm/h" % VEHICLE_SPEED_KMPH: [], "선형": []}
    if HistGradientBoostingRegressor is not None:
        scores["GBM(거리만)"] = []
        scores["GBM(거리+좌표)"] = []
    coefs = []

    for fold in folds:
        test = frame[frame["pair"].isin(fold)]
        train = frame[~frame["pair"].isin(fold)]
        if len(test) < 50 or len(train) < 200:
            continue

        truth = test["minutes"].to_numpy()
        const = test["distance_km"].to_numpy() / VEHICLE_SPEED_KMPH * 60
        scores["상수 %.0fkm/h" % VEHICLE_SPEED_KMPH].append(
            float(np.abs(const - truth).mean()))

        slope, intercept = np.polyfit(train["distance_km"], train["minutes"], 1)
        coefs.append((intercept, slope))
        scores["선형"].append(
            float(np.abs((intercept + slope * test["distance_km"]) - truth).mean()))

        if HistGradientBoostingRegressor is not None:
            for name, cols in (
                    ("GBM(거리만)", ["distance_km", "dur_idx"]),
                    ("GBM(거리+좌표)", ["distance_km", "straight_km", "dur_idx",
                                    "from_lat", "from_lon", "to_lat", "to_lon"])):
                model = HistGradientBoostingRegressor(
                    max_iter=300, random_state=0).fit(train[cols], train["minutes"])
                scores[name].append(
                    float(np.abs(model.predict(test[cols]) - truth).mean()))

    print("\n=== 2. 학습에 없던 OD쌍에서만 평가 (겹 %d개) ===" % len(coefs))
    base_key = "상수 %.0fkm/h" % VEHICLE_SPEED_KMPH
    base = float(np.mean(scores[base_key]))
    for name, values in scores.items():
        gain = "" if name == base_key else "  %+5.1f%%" % ((1 - np.mean(values) / base) * 100)
        print("  %-16s MAE %.3f분   최악 %.3f%s"
              % (name, np.mean(values), max(values), gain))

    intercept = float(np.mean([c[0] for c in coefs]))
    slope = float(np.mean([c[1] for c in coefs]))
    print("\n=== 3. 선형 모델이 말하는 것 ===")
    print("  분 = %.2f + %.2f × 거리(km)" % (intercept, slope))
    print("  절편 %.2f분  = 거리와 무관한 **정차 비용**(출발·정지·신호)" % intercept)
    print("  기울기       → 순수 주행 %.1f km/h" % (60 / slope))
    print("\n  → 상수 %.0f은 **주행 속도로는 맞았고, 정차 비용이 통째로 빠져 있었다.**"
          % VEHICLE_SPEED_KMPH)

    # 운용에 미치는 영향 — 시간 예산 판정이 얼마나 낙관적인가
    with db.session() as conn:
        summary = pd.read_sql(
            "SELECT visits, distance_km, travel_min FROM route_summary", conn)
    if not summary.empty:
        now_over = int((summary["travel_min"] > TIME_BUDGET_MINUTES).sum())
        assumed = summary["distance_km"] / VEHICLE_SPEED_KMPH * 60
        fixed = intercept * summary["visits"] + slope * summary["distance_km"]
        adjusted = summary["travel_min"] - assumed + fixed
        new_over = int((adjusted > TIME_BUDGET_MINUTES).sum())
        print("\n=== 4. 시간 예산(%.0f분) 판정에 미치는 영향 ===" % TIME_BUDGET_MINUTES)
        print("  회차 %d개" % len(summary))
        print("  현재 기준 초과        %3d (%.0f%%)"
              % (now_over, now_over / len(summary) * 100))
        print("  실측 관계로 고치면    %3d (%.0f%%)"
              % (new_over, new_over / len(summary) * 100))
        print("  회차당 이동시간 %.0f분 → %.0f분 (%+.0f분)"
              % (assumed.mean(), fixed.mean(), fixed.mean() - assumed.mean()))
        print("\n  ⚠️ 여기에 depot 복귀 누락(TODO 1-1)이 더해진다 —")
        print("     두 결함이 **같은 방향으로** 낙관을 만들고 있다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
