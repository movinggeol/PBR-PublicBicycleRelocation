"""날씨를 붙일 값어치가 있는지 먼저 재 본다 — 이용량과 순수요의 관계.

날씨는 자전거 **이용량**을 크게 흔든다. 하지만 이 파이프라인이 예측하는 것은
이용량이 아니라 **순수요(반납 − 대여)** 다. 비가 와서 대여와 반납이 함께 반토막
나면 이용량은 급감해도 순수요는 거의 그대로일 수 있다. 그러면 날씨를 넣어도
재배치 계획은 안 바뀐다.

그래서 날씨 데이터를 구하기 전에 로컬 데이터만으로 먼저 확인한다:

    날짜별 이용량이 흔들릴 때 날짜별 재배치 필요량도 같이 흔들리는가?

- 같이 움직인다 → 날씨가 이용량을 통해 순수요에 전달된다. 붙일 값어치가 있다.
- 따로 논다     → 날씨를 넣어도 순수요 예측은 별로 안 좋아진다.

실행: python experiments/net_vs_volume.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

import db

WINDOWS = {"_05_10": range(5, 10), "_10_15": range(10, 15), "_15_20": range(15, 20)}


def daily_net(conn, period: str, hours) -> pd.DataFrame:
    """날짜별 재배치 필요량. 대여소별 순수요의 절댓값 합 = 그날 옮겨야 할 대수."""
    columns = ", ".join(f"net_{h:02d}" for h in hours)
    frame = pd.read_sql(
        f"SELECT date, station_id, {columns} FROM net_demand WHERE period = ?",
        conn, params=[period])
    if frame.empty:
        return frame
    frame["net"] = frame[[f"net_{h:02d}" for h in hours]].sum(axis=1)
    return frame.groupby("date").agg(
        need=("net", lambda s: s.abs().sum()),      # 그날 옮겨야 할 총량
        net_sum=("net", "sum"),                     # 도시 전체 순유출입(방향)
    ).reset_index()


def daily_volume(conn, period: str, hours) -> pd.DataFrame:
    """날짜별 이용량(대여 건수). 날씨가 직접 흔드는 값."""
    lo, hi = f"{min(hours):02d}", f"{max(hours):02d}"
    return pd.read_sql(
        "SELECT substr(rent_at, 1, 10) AS date, COUNT(*) AS volume"
        " FROM rental_history"
        " WHERE period = ? AND substr(rent_at, 12, 2) BETWEEN ? AND ?"
        " GROUP BY date ORDER BY date",
        conn, params=[period, lo, hi])


def cv(series) -> float:
    """변동계수 — 평균 대비 얼마나 흔들리는가."""
    return float(series.std() / series.mean()) if series.mean() else float("nan")


def main() -> int:
    with db.session() as conn:
        periods = [r[0] for r in conn.execute(
            "SELECT DISTINCT period FROM net_demand ORDER BY period")]

        print("날짜별 이용량 vs 재배치 필요량 (날짜 수가 적은 기간은 참고만)\n")
        print(f"{'기간':11} {'회차':8} {'일수':>4} {'이용량 CV':>9} {'필요량 CV':>9} "
              f"{'상관':>7} {'설명력 R²':>9}")

        rows = []
        for period in periods:
            for duration, hours in WINDOWS.items():
                net = daily_net(conn, period, hours)
                volume = daily_volume(conn, period, hours)
                if net.empty or volume.empty:
                    continue
                merged = net.merge(volume, on="date")
                if len(merged) < 8:
                    continue

                r = float(np.corrcoef(merged["volume"], merged["need"])[0, 1])
                rows.append({"period": period, "duration": duration, "n": len(merged),
                             "vol_cv": cv(merged["volume"]), "need_cv": cv(merged["need"]),
                             "r": r})
                print(f"{period:11} {duration:8} {len(merged):4d} "
                      f"{cv(merged['volume']):9.3f} {cv(merged['need']):9.3f} "
                      f"{r:7.3f} {r * r:9.3f}")

        if not rows:
            print("\n데이터가 없습니다. tools/load_rentals.py로 대여이력을 적재하세요.")
            return 1

        summary = pd.DataFrame(rows)
        print("\n회차별 평균")
        print(f"{'회차':8} {'이용량 CV':>9} {'필요량 CV':>9} {'상관':>7} {'설명력 R²':>9}")
        for duration, group in summary.groupby("duration"):
            r = group["r"].mean()
            print(f"{duration:8} {group['vol_cv'].mean():9.3f} "
                  f"{group['need_cv'].mean():9.3f} {r:7.3f} {(group['r'] ** 2).mean():9.3f}")

        # 이용량 변동 중 요일이 설명하는 몫. 나머지가 날씨 등에 남은 여지다.
        print("\n평일/휴일이 설명하는 몫 (나머지가 날씨 등에 남은 여지)")
        print(f"{'회차':8} {'필요량 CV':>9} {'평일/휴일 R²':>12} {'남은 변동 CV':>12}")
        for duration, hours in WINDOWS.items():
            frames = []
            for period in periods:
                net = daily_net(conn, period, hours)
                if not net.empty:
                    frames.append(net)
            if not frames:
                continue
            allday = pd.concat(frames, ignore_index=True)
            allday["date"] = pd.to_datetime(allday["date"], errors="coerce")
            allday = allday.dropna(subset=["date"])
            # 공휴일은 아직 없으므로 주말만으로 근사한다(대략적 크기 확인이 목적).
            allday["holiday"] = allday["date"].dt.dayofweek >= 5

            # 기간별 수준 차이를 빼고 요일 효과만 본다(달마다 이용량 자체가 다르다).
            allday["ratio"] = allday["need"] / allday.groupby(
                allday["date"].dt.to_period("M"))["need"].transform("mean")
            grand = allday["ratio"].mean()
            predicted = allday.groupby("holiday")["ratio"].transform("mean")
            ss_tot = ((allday["ratio"] - grand) ** 2).sum()
            ss_res = ((allday["ratio"] - predicted) ** 2).sum()
            r2 = 1 - ss_res / ss_tot if ss_tot else float("nan")
            residual_cv = float((allday["ratio"] - predicted).std())
            print(f"{duration:8} {cv(allday['ratio']):9.3f} {r2:12.3f} {residual_cv:12.3f}")

        print("\n해석 기준")
        print("  상관이 높다(R² > 0.5)  → 이용량이 흔들리면 필요량도 흔들린다.")
        print("                           날씨가 이용량을 통해 전달되므로 붙일 값어치가 있다.")
        print("  상관이 낮다(R² < 0.2)  → 필요량은 이용량과 따로 논다.")
        print("                           날씨를 넣어도 순수요 예측은 별로 안 좋아진다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
