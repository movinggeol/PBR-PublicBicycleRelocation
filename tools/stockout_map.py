"""결품 지도 — **실측 재고**로 어느 대여소가 언제 비는지 본다.

지금 파이프라인의 결품 지표는 **계획을 시뮬레이션한 값**이다(step4
`stockout_simulation`). *"계획대로 됐다면 이랬을 것"* 이지 실제로 언제 비었는지가
아니다(docs/분석/KPI.md). 이 도구는 `stock_history`(10분 간격 실측)만 읽는다.

**모델이 아니라 집계다.** 자료가 며칠치뿐이어도 바로 쓸 수 있고, 쌓일수록
그대로 정확해진다.

세 가지로 나눠 본다 — **재배치로 고칠 수 있는 곳만 골라내는 것이 요점이다.**

  · **늘 빔**   관측 내내 재고 0. 채워도 곧 비거나, 애초에 자전거가 안 온다.
                재배치 대상이 아니라 **배치·증설 문제**다.
  · **늘 있음** 한 번도 0이 아니었다. 손댈 필요가 없다.
  · **오가는 곳** 비었다 찼다 한다. **여기만이 재배치가 값어치 있는 곳**이고,
                예측 모델을 붙인다면 학습 대상도 여기다.

⚠️ **수집 창 밖은 알 수 없다.** 기본 창이 평일 09~17시라 `_05_10`(출근)은
9시 한 시간만, `_20_05`(야간)는 아예 관측이 없다. 창 밖 시간을 '결품 없음'으로
읽으면 안 된다 — 이 도구는 관측이 있는 시간만 세고, 커버리지를 함께 찍는다.

실행:
    python tools/stockout_map.py
    python tools/stockout_map.py --top 30 --csv data/결품지도.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

import db
from project_config import DURATIONS, duration_hours


def load_history() -> pd.DataFrame:
    """재고 실측. 대여소 이름은 마스터에서 붙인다."""
    with db.session() as conn:
        frame = pd.read_sql(
            "SELECT observed_at, station_id, stock FROM stock_history", conn)
        try:
            master = pd.read_sql(
                "SELECT station_id, station_name, parking_info"
                " FROM stock_station_master", conn)
            master = master.drop_duplicates("station_id", keep="last")
        except Exception:
            master = pd.DataFrame(columns=["station_id", "station_name"])

    if frame.empty:
        return frame
    frame["observed_at"] = pd.to_datetime(frame["observed_at"])
    frame["date"] = frame["observed_at"].dt.date
    frame["hour"] = frame["observed_at"].dt.hour
    if not master.empty:
        frame = frame.merge(master, on="station_id", how="left")
    if "station_name" not in frame:
        frame["station_name"] = ""
    frame["station_name"] = frame["station_name"].fillna("")
    return frame


def coverage(frame: pd.DataFrame) -> pd.DataFrame:
    """시간대마다 관측이 몇 시간이나 있는지. **창 밖을 '문제 없음'으로 읽지 않도록.**"""
    seen = set(frame["hour"].unique())
    rows = []
    for duration in DURATIONS:
        hours = set(duration_hours(duration))
        got = sorted(hours & seen)
        rows.append({
            "시간대": duration,
            "대상시간": len(hours),
            "관측된시간": len(got),
            "커버리지": f"{len(got) / len(hours) * 100:.0f}%",
            "판정": "충분" if len(got) == len(hours)
                    else ("일부" if got else "**관측 없음**"),
        })
    return pd.DataFrame(rows)


def classify(frame: pd.DataFrame) -> pd.DataFrame:
    """대여소별로 '늘 빔 / 오가는 곳 / 늘 있음'을 가른다."""
    grouped = frame.groupby(["station_id", "station_name"])
    stat = grouped.agg(
        관측=("stock", "size"),
        빈비율=("stock", lambda s: float((s == 0).mean())),
        재고중앙=("stock", "median"),
        재고최대=("stock", "max"),
    ).reset_index()

    stat["구분"] = np.where(stat["빈비율"] >= 1.0, "늘 빔",
                   np.where(stat["빈비율"] <= 0.0, "늘 있음", "오가는 곳"))
    return stat.sort_values("빈비율", ascending=False)


def hourly_risk(frame: pd.DataFrame, movers: set) -> pd.DataFrame:
    """**오가는 곳**만 놓고 시각별 결품 비율. 언제 나가야 하는지의 근거."""
    part = frame[frame["station_id"].isin(movers)]
    if part.empty:
        return pd.DataFrame()
    table = part.groupby("hour").agg(
        관측=("stock", "size"),
        결품비율=("stock", lambda s: float((s == 0).mean())),
        평균재고=("stock", "mean"),
    ).reset_index()
    table["결품비율"] = (table["결품비율"] * 100).round(1)
    table["평균재고"] = table["평균재고"].round(2)
    return table


def main() -> int:
    parser = argparse.ArgumentParser(description="실측 재고로 결품 지도를 만든다")
    parser.add_argument("--top", type=int, default=20, help="가장 자주 비는 곳 몇 개")
    parser.add_argument("--csv", help="대여소별 결과를 CSV로 저장할 경로")
    args = parser.parse_args()

    frame = load_history()
    if frame.empty:
        print("stock_history가 비어 있습니다. 수집기를 먼저 돌리세요:")
        print(r"  .\scripts\collector.ps1 install")
        return 1

    days = frame["date"].nunique()
    print("관측 %s건 · 대여소 %s곳 · %d일 (%s ~ %s)\n"
          % (format(len(frame), ","), format(frame["station_id"].nunique(), ","),
             days, frame["date"].min(), frame["date"].max()))

    print("=== 0. 이 자료로 무엇을 말할 수 있나 (수집 창) ===")
    print(coverage(frame).to_string(index=False))
    print("\n  ⚠️ '관측 없음'인 시간대는 **결품이 없는 것이 아니라 모르는 것**입니다.")
    if days < 5:
        print("  ⚠️ %d일치뿐이라 아직 '이 며칠의 모습'입니다. 추세로 읽지 마세요." % days)

    stat = classify(frame)
    movers = set(stat.loc[stat["구분"] == "오가는 곳", "station_id"])

    print("\n=== 1. 대여소를 셋으로 가른다 ===")
    summary = stat.groupby("구분").agg(곳=("station_id", "size")).reset_index()
    summary["비율"] = (summary["곳"] / len(stat) * 100).round(0).astype(int).astype(str) + "%"
    order = {"늘 빔": 0, "오가는 곳": 1, "늘 있음": 2}
    print(summary.sort_values("구분", key=lambda s: s.map(order)).to_string(index=False))
    print("\n  · 늘 빔     → 재배치로 못 고친다. **배치·증설 문제**다.")
    print("  · 오가는 곳 → **재배치가 값어치 있는 곳.** 예측을 붙인다면 여기다.")
    print("  · 늘 있음   → 손댈 필요 없다.")

    print("\n=== 2. 가장 자주 비는 곳 %d — '늘 빔'은 뺐다 ===" % args.top)
    movers_stat = stat[stat["구분"] == "오가는 곳"].head(args.top).copy()
    if movers_stat.empty:
        print("  오가는 곳이 없습니다.")
    else:
        movers_stat["빈비율"] = (movers_stat["빈비율"] * 100).round(1)
        print(movers_stat[["station_id", "station_name", "빈비율",
                           "재고중앙", "재고최대", "관측"]]
              .rename(columns={"빈비율": "빈시간%"}).to_string(index=False))

    print("\n=== 3. 언제 비나 (오가는 곳 %s곳) ===" % format(len(movers), ","))
    risk = hourly_risk(frame, movers)
    if risk.empty:
        print("  관측이 모자랍니다.")
    else:
        # 이름 바꾸기는 **출력용 사본**에서만 한다 — `risk`에는 계속 `결품비율`뿐이다.
        # (예전에는 `if "결품%" in risk`로 갈랐는데 늘 거짓이라 죽은 분기였다, 1.26.143.)
        print(risk.rename(columns={"결품비율": "결품%"}).to_string(index=False))
        worst = risk.loc[risk["결품비율"].idxmax()]
        print("\n  가장 나쁜 시각: %d시 (결품 %.1f%%)"
              % (int(worst["hour"]), float(worst["결품비율"])))

    print("\n=== 4. '늘 빔'으로 분류된 곳 (재배치 대상이 아니다) ===")
    always = stat[stat["구분"] == "늘 빔"]
    print("  %s곳. 관측 내내 재고가 0이었습니다." % format(len(always), ","))
    if len(always):
        print("  예: " + ", ".join(
            (always["station_name"].fillna("").astype(str) + "(" + always["station_id"] + ")")
            .head(5).tolist()))
        print("  → 채워 넣어도 곧 비거나, 애초에 자전거가 흘러들지 않는 곳입니다.")
        print("     **재배치 계획에서 빼거나 거치대 조정을 검토할 대상**입니다.")

    if args.csv:
        target = Path(args.csv)
        target.parent.mkdir(parents=True, exist_ok=True)
        stat.to_csv(target, index=False, encoding="utf-8-sig")
        print("\n대여소별 결과를 저장했습니다: %s" % target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
