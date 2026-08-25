"""회차당 필요 차량 추정에 **거리**를 넣으면 나아지는가 (시간 예산 3단계).

`wanted_vehicles()`는 이동시간을 이렇게 잡는다.

    이동시간 ≈ 대여소 수 × TRAVEL_MIN_PER_STATION(12.5분)

**대여소 수만 센다.** 그래서 같은 5곳이라도 **모여 있는 5곳과 흩어진 5곳을 구분하지
못한다.** 실측에서 소요시간을 지배하는 것은 대여소 수(r=0.80)가 아니라 **이동
거리(r=0.966)** 였고, 예산을 넘긴 군집은 전부 45~56km로 정상(30km)보다 흩어져
있었다(1.21.6).

그런데 **추정 시점에는 군집이 아직 없다.** 군집을 나누기 전에 "몇 대가 필요한가"를
정해야 하므로, 군집 안 이동거리를 알 수 없다. 쓸 수 있는 것은 **후보 대여소들이
얼마나 퍼져 있는가**뿐이다.

    퍼짐 = 후보 좌표의 평균 최근접 이웃 거리 (km)

이것으로 이동시간을 예측할 수 있는지 잰다. 묻는 것은 둘이다.

    ① 지금 추정(대여소 수만)은 실제를 얼마나 맞히나
    ② 퍼짐을 넣으면 나아지나  ← 나아지지 않으면 넣지 않는다

**주의**: 여기서 재는 것은 '군집 수를 몇 개로 할지'가 아니라 **'추정 총 소요시간이
실제와 맞는지'** 다. 군집 수는 그 추정에서 파생된다.

실행: python experiments/params/travel_estimate.py
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

import db
from project_config import (
    DROP_TIME_SEC, PICK_TIME_SEC, TRAVEL_MIN_PER_STATION,
)


def haversine_km(lat1, lon1, lat2, lon2):
    """두 점 사이 거리(km). step2와 같은 공식을 쓴다."""
    radius = 6371.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp = p2 - p1
    dl = np.radians(lon2) - np.radians(lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * radius * np.arcsin(np.sqrt(a))


def spread_km(points: pd.DataFrame) -> float:
    """후보들이 얼마나 퍼져 있나 — **평균 최근접 이웃 거리**.

    군집 전이라 경로를 모르므로, '이웃까지 평균 몇 km인가'로 대신한다.
    지름이나 표준편차가 아니라 최근접을 쓰는 이유: 차량은 가까운 곳부터 훑으므로
    실제 이동은 **이웃 간 거리의 합**에 가깝다.
    """
    if len(points) < 2:
        return 0.0
    lat = points["lat"].to_numpy()
    lon = points["lon"].to_numpy()
    total = 0.0
    for i in range(len(lat)):
        distances = haversine_km(lat[i], lon[i], lat, lon)
        distances[i] = np.inf
        total += distances.min()
    return float(total / len(lat))


def load_runs():
    """실행·회차별 실제 소요시간과, 그때의 후보 대여소 특성(좌표 포함)."""
    with db.session() as conn:
        routes = pd.read_sql(
            "SELECT run_label, duration, cluster, visits, bikes,"
            " distance_km, total_min FROM route_summary", conn)
        candidates = pd.read_sql(
            "SELECT run_label, duration, station_id, lat, lon, rebal_qty"
            " FROM pick_drop", conn)
    return routes, candidates


def main() -> int:
    parser = argparse.ArgumentParser(description="추정에 거리를 넣으면 나아지나")
    parser.parse_known_args()

    routes, candidates = load_runs()
    if routes.empty or candidates.empty:
        print("route_summary 또는 pick_drop이 비어 있습니다. 파이프라인을 돌리세요.")
        return 1

    rows = []
    for (label, duration), group in routes.groupby(["run_label", "duration"]):
        cand = candidates[(candidates["run_label"] == label)
                          & (candidates["duration"] == duration)]
        if cand.empty:
            continue
        coords = cand[["lat", "lon"]].dropna()
        if len(coords) < 2:
            continue

        bikes = float(cand.loc[cand["rebal_qty"] > 0, "rebal_qty"].sum())
        work_min = bikes * (PICK_TIME_SEC + DROP_TIME_SEC) / 60.0
        stations = len(cand)

        rows.append({
            "실행": label, "시간대": duration,
            "대여소": stations,
            "퍼짐km": spread_km(coords),
            "실제총분": float(group["total_min"].sum()),
            "실제이동분": float(group["total_min"].sum()) - work_min,
            "작업분": work_min,
            "현행추정이동분": stations * TRAVEL_MIN_PER_STATION,
        })

    if len(rows) < 5:
        print(f"표본이 {len(rows)}개뿐이라 비교할 수 없습니다.")
        return 1

    frame = pd.DataFrame(rows)
    print(f"표본 {len(frame)}건 (실행 × 회차)\n")

    print("① 지금 추정은 실제 이동시간을 얼마나 맞히나")
    err = frame["현행추정이동분"] - frame["실제이동분"]
    print(f"  평균 오차 {err.mean():+8.1f}분 · 절대 오차 {err.abs().mean():6.1f}분"
          f" · 상관 r = {frame['현행추정이동분'].corr(frame['실제이동분']):.3f}")

    print("\n② 무엇이 실제 이동시간을 설명하나 (상관계수)")
    for col in ("대여소", "퍼짐km"):
        print(f"  실제이동분 ~ {col:8} r = {frame['실제이동분'].corr(frame[col]):.3f}")
    frame["대여소x퍼짐"] = frame["대여소"] * frame["퍼짐km"]
    print(f"  실제이동분 ~ {'대여소×퍼짐':8} r = "
          f"{frame['실제이동분'].corr(frame['대여소x퍼짐']):.3f}")

    print("\n③ 회귀로 계수를 뽑으면")
    # 이동분 ≈ a·대여소 + b·(대여소 × 퍼짐)
    X = np.column_stack([frame["대여소"], frame["대여소x퍼짐"]])
    y = frame["실제이동분"].to_numpy()
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    predicted = X @ coef
    r2_new = 1 - ((y - predicted) ** 2).sum() / ((y - y.mean()) ** 2).sum()

    base = frame["현행추정이동분"].to_numpy()
    r2_old = 1 - ((y - base) ** 2).sum() / ((y - y.mean()) ** 2).sum()

    print(f"  현행 (대여소 × {TRAVEL_MIN_PER_STATION})        R² = {r2_old:6.3f}"
          f" · 절대오차 {np.abs(base - y).mean():5.1f}분")
    print(f"  제안 ({coef[0]:.2f}·대여소 + {coef[1]:.2f}·대여소·퍼짐)  R² = {r2_new:6.3f}"
          f" · 절대오차 {np.abs(predicted - y).mean():5.1f}분")

    print("\n판정")
    print("  절대오차가 뚜렷이 줄면  → 추정식에 퍼짐 항을 넣는다.")
    print("  거의 같으면            → 넣지 않는다. 항이 늘면 조정할 것도 는다.")
    print("\n※ 이 표본은 실행 라벨이 섞여 있다(파라미터 스윕 포함). 계수를 확정하려면")
    print("   같은 조건의 실행만 골라 다시 재야 한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
