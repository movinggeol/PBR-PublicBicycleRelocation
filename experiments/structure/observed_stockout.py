"""결품을 계산이 아니라 **관측**으로 센다 (TODO 1-4).

지금 `step4_metrics/imbalance.py`의 결품 시간은 **복원**입니다.

    stock(t+1) = clip(stock(t) − net(t), 0, 거치대 수)

초기 재고는 실행 시점 스냅샷 **한 장**이고, 그 뒤의 궤적은 **지난달 순수요**로
만들어 냅니다. 그래서 두 가지 한계가 있었습니다.

  · 초기 재고(오늘)와 순수요(지난달)의 **시점이 다르다**
  · 0에서 잘라 내므로 못 빌린 수요가 사라져 **결품의 하한**이다

1.20.0부터 재고를 10분 간격으로 모으고 있으므로([COLLECTOR.md](../../docs/구현/COLLECTOR.md)),
이제 그 궤적을 **실측으로 대체**할 수 있습니다. 이 스크립트가 하는 일은 하나입니다.

    관측된 재고에서 "몇 시간 동안 빌릴 수 없었나 / 반납할 수 없었나"를 직접 센다.

**복원값과 맞대어 보는 것이 목적입니다.** 복원이 결품을 얼마나 낮춰 잡고 있었는지
알면, 지금까지의 성과 수치를 어떻게 읽어야 하는지가 정해집니다.

실행:
    python experiments/structure/observed_stockout.py
    python experiments/structure/observed_stockout.py --duration _10_15
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd

import db
from project_config import DURATIONS, REBAL_MIN_QTY, duration_hours

# 수집 간격(분). 한 틱이 대표하는 시간이 곧 결품 시간의 단위다.
TICK_MINUTES = 10

# 하루가 '온전하다'고 볼 최소 틱 수. 09~17시 10분 간격이면 49틱이 기대값이고,
# 몇 틱쯤 빠져도 하루의 모양은 남는다.
MIN_TICKS_PER_DAY = 40


def load(day_type: str) -> pd.DataFrame:
    with db.session() as conn:
        frame = db.load_stock_history(conn, day_type=day_type)
        parking = pd.read_sql("SELECT station_id, parking_lot FROM parking_lot", conn)
    if frame.empty:
        return frame
    frame["관측"] = pd.to_datetime(frame["observed_at"])
    frame["날짜"] = frame["관측"].dt.strftime("%Y-%m-%d")
    frame["시각"] = frame["관측"].dt.hour
    # 거치대는 대여소당 하나이므로 중복을 걷어낸다(수집일마다 쌓일 수 있다).
    parking = parking.drop_duplicates("station_id")
    return frame.merge(parking, on="station_id", how="left")


def complete_days(frame: pd.DataFrame) -> list:
    """틱이 충분히 모인 날만 고른다. **반쪽짜리 날을 섞으면 시간이 과소 집계된다.**"""
    ticks = frame.groupby("날짜")["관측"].nunique()
    return sorted(ticks[ticks >= MIN_TICKS_PER_DAY].index)


def observed_hours(frame: pd.DataFrame, hours: list) -> pd.DataFrame:
    """대여소·날짜별로 **빌릴 수 없던 시간**과 **반납할 수 없던 시간**을 센다.

    한 틱 = TICK_MINUTES분으로 환산한다. 결품(재고 0)과 만차(재고 ≥ 거치대)를 따로
    세는 이유는 둘의 처방이 반대이기 때문이다 — 전자는 채워야 하고 후자는 빼내야 한다.
    """
    part = frame[frame["시각"].isin(hours)].copy()
    if part.empty:
        return part
    part["빈"] = part["stock"] <= 0
    part["찬"] = part["parking_lot"].notna() & (part["stock"] >= part["parking_lot"])

    grouped = part.groupby(["station_id", "날짜"])
    result = grouped.agg(틱=("관측", "size"),
                         빈틱=("빈", "sum"),
                         찬틱=("찬", "sum"),
                         평균재고=("stock", "mean")).reset_index()
    result["결품시간"] = result["빈틱"] * TICK_MINUTES / 60
    result["만차시간"] = result["찬틱"] * TICK_MINUTES / 60
    result["관측시간"] = result["틱"] * TICK_MINUTES / 60
    return result


def target_stations() -> set:
    """가장 최근 계획이 실제로 손대는 대여소. 전체 평균은 결론을 흐린다."""
    with db.session() as conn:
        plan = pd.read_sql(
            "SELECT station_id, rebal_qty FROM rebalance_plan"
            " WHERE run_label = (SELECT MAX(run_label) FROM rebalance_plan)", conn)
    if plan.empty:
        return set()
    return set(plan.loc[plan["rebal_qty"].abs() > REBAL_MIN_QTY, "station_id"])


def simulated_stockout() -> pd.DataFrame:
    """step4가 남긴 **복원** 결품(재배치 전). 실측과 맞대어 볼 상대다."""
    with db.session() as conn:
        return pd.read_sql(
            "SELECT duration, AVG(stockout_hours_before) AS 복원"
            " FROM kpi_summary WHERE stockout_hours_before IS NOT NULL"
            " GROUP BY duration", conn)


def compare_with_simulation(frame: pd.DataFrame, durations: list, window,
                            targets: set) -> None:
    """복원과 실측을 나란히 놓는다 — **시간대가 온전히 겹칠 때만.**

    수집 창은 09~17시라 `_05_10`은 09시 한 시각만, `_15_20`은 15시 한 시각만 겹친다.
    그 상태로 5시간 창의 복원값과 비교하면 실측이 5분의 1로 보인다. **겹치는 시각이
    창 전체일 때만** 비교를 찍는 이유다.
    """
    simulated = simulated_stockout()
    if simulated.empty:
        return
    rows = []
    for duration in durations:
        hours = duration_hours(duration)
        overlap = [h for h in hours if h in window]
        if len(overlap) != len(hours):          # 창이 통째로 겹칠 때만
            continue
        daily = observed_hours(frame, overlap)
        if daily.empty:
            continue
        per_day = daily.groupby("station_id")["결품시간"].mean()
        if targets:
            per_day = per_day.loc[per_day.index.isin(targets)]
        match = simulated.loc[simulated["duration"] == duration, "복원"]
        if per_day.empty or match.empty:
            continue
        rows.append((duration, float(match.iloc[0]), float(per_day.mean()), len(per_day)))

    if not rows:
        print("\n(복원 대비 비교: 수집 창과 온전히 겹치는 시간대가 아직 없습니다)")
        return

    print(f"\n복원 vs 실측 — 작업 대상 대여소, 대여소·일 평균 결품 시간(h)")
    print(f"{'시간대':8} {'복원(step4)':>11} {'실측(수집)':>11} {'차이':>9} {'대여소':>7}")
    for duration, sim, obs, count in rows:
        gap = (obs / sim - 1) * 100 if sim else float("nan")
        print(f"{duration:8} {sim:11.2f} {obs:11.2f} {gap:+8.0f}% {count:7,d}")
    print("  → 실측이 크면 **복원이 결품을 낮춰 잡고 있었다**는 뜻입니다"
          "(0에서 잘라 못 빌린 수요가 사라지므로 예상된 방향입니다).")


def main() -> int:
    parser = argparse.ArgumentParser(description="관측 재고로 결품을 직접 센다")
    parser.add_argument("--duration", help="시간대 하나만 (예: _10_15)")
    parser.add_argument("--day-type", default="weekday", choices=("weekday", "holiday"))
    args = parser.parse_args()

    frame = load(args.day_type)
    if frame.empty:
        print("수집된 재고가 없습니다. scripts/collector.ps1 install 로 수집을 시작하세요.")
        return 1

    days = complete_days(frame)
    span = f"{frame['날짜'].min()} ~ {frame['날짜'].max()}"
    print(f"수집 {frame['관측'].nunique():,}틱 · 대여소 {frame['station_id'].nunique():,}곳"
          f" · {span} · 요일 {args.day_type}")
    print(f"온전한 날({MIN_TICKS_PER_DAY}틱 이상): {len(days)}일"
          f"{' — ' + ', '.join(days) if days else ''}\n")

    if not days:
        print("⚠️  **아직 온전한 날이 없습니다.** 아래 숫자는 참고용이며, 하루가 다 모이기")
        print("    전까지는 결품 '시간'으로 인용하지 마십시오 — 관측한 시간만큼만 셉니다.\n")

    # 수집 창(09~17시)과 겹치는 시간대만 뜻이 있다.
    window = frame["시각"].unique()
    durations = [args.duration] if args.duration else list(DURATIONS)
    targets = target_stations()

    print(f"{'시간대':8} {'겹치는 시각':>10} {'대여소':>7} "
          f"{'결품시간/일':>11} {'만차시간/일':>11} {'결품 비율':>9}")
    printed = 0
    for duration in durations:
        hours = [h for h in duration_hours(duration) if h in window]
        if not hours:
            continue
        daily = observed_hours(frame, hours)
        if daily.empty:
            continue
        per_day = daily.groupby("station_id")[["결품시간", "만차시간", "관측시간"]].mean()
        share = (daily["빈틱"].sum() / daily["틱"].sum()) if daily["틱"].sum() else 0
        print(f"{duration:8} {len(hours):10d} {len(per_day):7,d} "
              f"{per_day['결품시간'].mean():11.2f} {per_day['만차시간'].mean():11.2f} "
              f"{share * 100:8.1f}%")
        printed += 1

        if targets:
            picked = per_day.loc[per_day.index.isin(targets)]
            if not picked.empty:
                print(f"{'  └ 작업 대상만':22} {len(picked):7,d} "
                      f"{picked['결품시간'].mean():11.2f} {picked['만차시간'].mean():11.2f}")

    if not printed:
        print("  수집 창(09~17시)과 겹치는 시간대가 없습니다.")
        return 1

    compare_with_simulation(frame, durations, window, targets)

    print("\n읽는 법")
    print(f"  · 한 틱 = {TICK_MINUTES}분. 수집 창(09~17시) 안에서만 셉니다.")
    print("  · **결품 시간은 관측한 시간에 대한 값**입니다. 창 밖(야간·새벽)은 모릅니다.")
    print("  · step4의 결품은 순수요로 복원한 값이라 **직접 비교하려면 같은 시간대·같은")
    print("    대여소로 맞춰야** 합니다. 맞대어 보는 것이 이 스크립트의 목적입니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
