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
from datetime import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd

import db
from project_config import DURATIONS, REBAL_MIN_QTY, duration_hours

# 수집 간격(분). 한 틱이 대표하는 시간이 곧 결품 시간의 단위다.
TICK_MINUTES = 10

# 하루가 '온전하다'고 볼 기준 — **그날 실제로 관측된 시간 폭에서 구한다.**
#
# ⚠️ 예전에는 `MIN_TICKS_PER_DAY = 40`으로 박아 두었다. 09~17시(49틱) 기준의
# 82%였는데, 1.25.1에서 수집 창이 07~22시(91틱)로 넓어지자 **44%가 됐다** —
# 반나절만 모인 날이 '온전한 날'로 통과한다. 아래 `complete_days()`가 그러면
# 안 된다고 적어 둔 바로 그 상황이다("반쪽짜리 날을 섞으면 시간이 과소 집계된다").
# 창은 앞으로도 바뀔 수 있으므로 **상수 대신 비율로 판정한다.**
COMPLETE_DAY_RATIO = 0.8

# 창을 못 읽었을 때의 최소 안전선(틱). 하루에 이만큼도 없으면 어떤 비율이든 볼 것이 없다.
MIN_TICKS_FLOOR = 12


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


def expected_ticks(frame: pd.DataFrame) -> int:
    """관측된 자료에서 **하루에 기대되는 틱 수**를 되짚는다.

    수집 창을 여기 박아 두지 않는 이유는 창이 실제로 바뀌었기 때문이다
    (09~17 → 07~22, 1.25.1). 가장 많이 모인 날의 첫 틱~끝 틱 폭을 그날의 창으로
    보고 계산한다 — 하루라도 온전히 돈 날이 있으면 그게 곧 창이다.
    """
    if frame.empty:
        return 0
    span = frame.groupby("날짜")["관측"].agg(["min", "max", "nunique"])
    best = span.loc[span["nunique"].idxmax()]
    minutes = (best["max"] - best["min"]).total_seconds() / 60
    return int(minutes // TICK_MINUTES) + 1


def complete_days(frame: pd.DataFrame) -> list:
    """틱이 충분히 모인 날만 고른다. **반쪽짜리 날을 섞으면 시간이 과소 집계된다.**"""
    if frame.empty:
        return []
    threshold = max(int(expected_ticks(frame) * COMPLETE_DAY_RATIO), MIN_TICKS_FLOOR)
    ticks = frame.groupby("날짜")["관측"].nunique()
    return sorted(ticks[ticks >= threshold].index)


def duration_complete_days(frame: pd.DataFrame, hours: list) -> list:
    """**그 회차를 온전히 덮은 날**만 고른다 (2026-09-01).

    `complete_days()`는 하루 전체로 판정하는데, 수집 창이 날마다 다르면 그것이
    통하지 않는다. 실제로 8/25~9/01에는 두 종류의 창이 섞여 있었다 —
    8/31만 07~22시(77틱)이고 나머지는 09~18시대(34~61틱)다. 가장 넓은 날이
    기준(91틱의 80% = 72틱)을 정하는 바람에 **좁은 창 날이 전부 탈락해
    온전한 날이 1일**이 됐다. 그런데 그 날들은 반쪽짜리가 아니다 —
    **자기 창 안에서는 결측 0으로 촘촘하다**(실측 100%).

    회차 비교에 필요한 것은 '하루가 온전한가'가 아니라 **'이 회차의 시간대가
    다 덮였는가'** 이므로, 여기서는 회차 단위로 판정한다.
    """
    if frame.empty or not hours:
        return []
    part = frame[frame["시각"].isin(hours)]
    if part.empty:
        return []
    per_hour = 60 // TICK_MINUTES
    need = max(int(len(hours) * per_hour * COMPLETE_DAY_RATIO), 1)
    ticks = part.groupby("날짜")["관측"].nunique()
    return sorted(ticks[ticks >= need].index)


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


def target_stations(duration: str = None) -> set:
    """계획이 실제로 손대는 대여소. 전체 평균은 결론을 흐린다.

    **회차마다 대상이 다르다**(2026-09-01 발견). 예전에는 `MAX(run_label)`
    하나로 모든 회차를 재서, **`_10_15`의 결품을 `_15_20`의 대상으로** 재는
    일이 생겼다 — 실측 245 / 304 / 260곳으로 세 회차가 모두 다르다.
    `duration`을 주면 **그 회차를 다룬 가장 최근 실행**의 대상을 돌려준다.
    """
    with db.session() as conn:
        if duration:
            plan = pd.read_sql(
                "SELECT station_id, rebal_qty FROM rebalance_plan"
                " WHERE duration = ? AND run_label = ("
                "   SELECT MAX(run_label) FROM rebalance_plan WHERE duration = ?)",
                conn, params=[duration, duration])
        else:
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

    좁은 창(09~17시)에서는 `_05_10`이 09시 한 시각만, `_15_20`이 15시 한 시각만
    겹친다. 그 상태로 5시간 창의 복원값과 비교하면 실측이 5분의 1로 보인다.
    **겹치는 시각이 창 전체일 때만** 비교를 찍는 이유다.

    ⚠️ **두 값은 서로 다른 세상을 잰다.** 복원은 `stockout_hours_before`(재배치
    **전**)이고, 실측에는 **대전교통공사가 실제로 돌린 재배치**가 이미 반영돼
    있다(우리 계획이 아니다 — [ML_OPPORTUNITIES.md] ② 참고). 편향이 반대
    방향으로 둘이라 차이를 '복원의 오차' 하나로 읽으면 안 된다.

      · 복원은 재고를 0에서 자른다 → 못 빌린 수요가 사라져 **결품을 낮춰 잡는다**
      · 실측은 공사 차량이 채워 준 만큼 **결품이 줄어 있다**

    두 편향이 상쇄돼 우연히 비슷해 보일 수도 있다. **가른 뒤에야 정확한 비교가
    된다**(TODO 17-B 3단계).
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
        # **그 회차를 온전히 덮은 날만 쓴다**(2026-09-01). 창이 날마다 다르면
        # 하루 전체 판정으로는 반쪽짜리 회차가 섞여 결품이 과소 집계된다.
        ok_days = duration_complete_days(frame, overlap)
        if not ok_days:
            continue
        daily = observed_hours(frame[frame["날짜"].isin(ok_days)], overlap)
        if daily.empty:
            continue
        per_day = daily.groupby("station_id")["결품시간"].mean()
        # 대상도 **그 회차의 것**을 쓴다 — 회차마다 집합이 다르다.
        dur_targets = target_stations(duration) or targets
        if dur_targets:
            per_day = per_day.loc[per_day.index.isin(dur_targets)]
        match = simulated.loc[simulated["duration"] == duration, "복원"]
        if per_day.empty or match.empty:
            continue
        rows.append((duration, float(match.iloc[0]), float(per_day.mean()),
                     len(per_day), len(ok_days)))

    if not rows:
        print("\n(복원 대비 비교: 수집 창과 온전히 겹치는 시간대가 아직 없습니다)")
        return []

    print(f"\n복원 vs 실측 — 작업 대상 대여소, 대여소·일 평균 결품 시간(h)")
    print(f"{'시간대':8} {'복원(step4)':>11} {'실측(수집)':>11} {'차이':>9} {'대여소':>7} {'날':>4}")
    for duration, sim, obs, count, ndays in rows:
        gap = (obs / sim - 1) * 100 if sim else float("nan")
        print(f"{duration:8} {sim:11.2f} {obs:11.2f} {gap:+8.0f}% {count:7,d} {ndays:4d}")
    print("  → 실측이 크면 **복원이 결품을 낮춰 잡고 있었다**는 뜻입니다"
          "(0에서 잘라 못 빌린 수요가 사라지므로 예상된 방향입니다).")
    print("  ⚠️ 다만 실측에는 **공사가 실제로 돌린 재배치**가 이미 반영돼 있어")
    print("     그만큼 결품이 줄어 있습니다 — 두 편향이 반대 방향이라 차이를")
    print("     '복원의 오차'로만 읽으면 안 됩니다(TODO 17-B 3단계에서 가릅니다).")
    return rows


def _save_calibration(rows: list, day_type: str, days: int, window) -> None:
    """보정 계수를 DB에 남긴다 (1.26.1, 수정안 37).

    **왜 저장하는가 — 수집이 멈춰도 계수는 남기 위해서다.**
    *"재고 조사를 멈추면 사라지는 설득력"* 이라는 지적에 대한 답이다. 관측 자체는
    수집을 켜 둔 동안만 쌓이지만, 여기서 얻은 **복원 대비 보정비**는 표에 남아
    나중에도 인용할 수 있다. 다만 `days`(근거가 된 온전한 날 수)를 함께 남겨
    **얼마나 얇은 근거인지 숨기지 않는다.**

    ⚠️ 계수를 파이프라인이 자동으로 곱하지는 않는다. 지금은 3일치라 그럴 근거가
    없다 — 표에 쌓아 두고 추세를 보는 단계다.
    """
    if not rows:
        print("\n보정 계수를 낼 수 있는 시간대가 없습니다 — 저장하지 않았습니다.")
        return

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    hours = sorted(int(h) for h in window)
    note = f"관측 창 {hours[0]:02d}~{hours[-1]:02d}시"
    payload = []
    for duration, simulated, observed, stations, ndays in rows:
        payload.append({
            "measured_at": stamp, "duration": duration, "day_type": day_type,
            "ratio": (observed / simulated) if simulated else None,
            "observed": observed, "simulated": simulated,
            # **회차별 날 수**를 쓴다(2026-09-01) — 하루 전체 판정의 값 하나를
            # 모든 회차에 붙이면 얼마나 얇은 근거인지 가려진다.
            "days": ndays, "stations": stations, "note": note,
        })

    with db.session() as conn:
        db.init_schema(conn)
        db.save_stockout_calibration(conn, payload)

    print(f"\n보정 계수 {len(payload)}건을 남겼습니다 (measured_at={stamp}).")
    for row in payload:
        ratio = row["ratio"]
        print(f"  {row['duration']:8} 보정비 {ratio:.3f}"
              f"  (실측 {row['observed']:.2f} / 복원 {row['simulated']:.2f},"
              f" 온전한 날 {row['days']}일)")
    if days < 14:
        print(f"  ⚠️  근거가 {days}일뿐입니다. 논문에 인용할 때 이 사실을 함께 적으십시오.")


def main() -> int:
    parser = argparse.ArgumentParser(description="관측 재고로 결품을 직접 센다")
    parser.add_argument("--duration", help="시간대 하나만 (예: _10_15)")
    parser.add_argument("--day-type", default="weekday", choices=("weekday", "holiday"))
    parser.add_argument("--save", action="store_true",
                        help="보정 계수를 stockout_calibration에 남긴다 "
                             "(수집이 멈춰도 계수는 남는다)")
    args = parser.parse_args()

    frame = load(args.day_type)
    if frame.empty:
        print("수집된 재고가 없습니다. scripts/collector.ps1 install 로 수집을 시작하세요.")
        return 1

    days = complete_days(frame)
    span = f"{frame['날짜'].min()} ~ {frame['날짜'].max()}"
    print(f"수집 {frame['관측'].nunique():,}틱 · 대여소 {frame['station_id'].nunique():,}곳"
          f" · {span} · 요일 {args.day_type}")
    expected = expected_ticks(frame)
    threshold = max(int(expected * COMPLETE_DAY_RATIO), MIN_TICKS_FLOOR)
    print(f"관측된 창: 하루 {expected}틱 기대 · 온전한 날 기준 {threshold}틱 이상")
    print(f"온전한 날: {len(days)}일{' — ' + ', '.join(days) if days else ''}\n")

    if not days:
        print("⚠️  **아직 온전한 날이 없습니다.** 아래 숫자는 참고용이며, 하루가 다 모이기")
        print("    전까지는 결품 '시간'으로 인용하지 마십시오 — 관측한 시간만큼만 셉니다.\n")

    # 수집 창과 겹치는 시간대만 뜻이 있다. 창은 자료에서 읽는다(박아 두지 않는다).
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

        dur_targets = target_stations(duration) or targets
        if dur_targets:
            picked = per_day.loc[per_day.index.isin(dur_targets)]
            if not picked.empty:
                print(f"{'  └ 작업 대상만':22} {len(picked):7,d} "
                      f"{picked['결품시간'].mean():11.2f} {picked['만차시간'].mean():11.2f}")

    if not printed:
        hours = sorted(int(h) for h in window)
        print(f"  수집 창({hours[0]:02d}~{hours[-1]:02d}시)과 겹치는 시간대가 없습니다.")
        return 1

    rows = compare_with_simulation(frame, durations, window, targets)

    if args.save:
        _save_calibration(rows, args.day_type, len(days), window)

    hours = sorted(int(h) for h in window)
    print("\n읽는 법")
    print(f"  · 한 틱 = {TICK_MINUTES}분. 수집 창({hours[0]:02d}~{hours[-1]:02d}시) 안에서만 셉니다.")
    print("  · **결품 시간은 관측한 시간에 대한 값**입니다. 창 밖(야간·새벽)은 모릅니다.")
    print("  · step4의 결품은 순수요로 복원한 값이라 **직접 비교하려면 같은 시간대·같은")
    print("    대여소로 맞춰야** 합니다. 맞대어 보는 것이 이 스크립트의 목적입니다.")
    print("  · ⚠️ **두 값은 서로 다른 세상을 잽니다.** 복원은 `stockout_hours_before`")
    print("    (재배치 **전**)이고, 실측에는 **대전교통공사가 실제로 돌린 재배치**가")
    print("    이미 반영돼 있습니다. 편향이 반대 방향으로 둘입니다 — 복원은 0에서")
    print("    잘라 결품을 낮춰 잡고(실측↑), 실측은 공사 차량 덕에 낮아집니다(실측↓).")
    print("    **차이를 '복원의 오차'로만 읽으면 안 됩니다.**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
