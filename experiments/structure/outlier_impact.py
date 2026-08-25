"""이상치 제거가 계획을 바꾸는가 — 지금 안 걸려 있는데, 걸었어야 했나 (TODO 18).

`step0_eda/concat_1year_file.py`는 이용시간(분)·이용거리(km)의 **IQR × 1.5 밖**을
잘라 냅니다. 그런데 순수요를 만드는 `step0_collect/raw_to_net.py`는 월별 원본에서
**네 컬럼(시각·대여소ID)만** 읽습니다 — 이용시간·이용거리는 읽지도 않으므로
**그 필터는 계획 경로에 걸리지 않습니다**(docs/분석/DECISIONS.md 6-1).

그래도 되는지에 대해 근거가 양쪽에 있습니다.

  된다  순수요는 **건수**만 센다. 8시간을 탔든 3분을 탔든 그 대여소에서 한 대가
        나간 것은 같다. 시간 이상치를 지우면 정상적인 장시간 이용까지 지운다.
  안 된다  이용시간이 0분이거나 며칠인 기록은 **대여 자체가 오류**(장비 오작동·
        미반납)일 수 있고, 그렇다면 건수에서도 빠져야 한다.

**말로는 안 끝납니다.** 그래서 잽니다. 묻는 것은 셋입니다.

    ① 얼마나 잘려 나가는가          (전체의 몇 %인가)
    ② 순수요가 얼마나 달라지는가    (대여소·날짜 단위로)
    ③ **계획이 달라지는가**          ← 이것만이 실제로 중요합니다

③에서 달라지지 않으면 이 논쟁은 닫힙니다. 달라지면 어느 쪽이 옳은지 따로 재야 합니다.

실행:
    python experiments/structure/outlier_impact.py
    python experiments/structure/outlier_impact.py --period "25년 11월"
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

import db
from project_config import (
    DURATIONS, REBAL_MIN_QTY, TARGET_Z, duration_hours, select_day_type,
)

# EDA가 쓰는 것과 **같은 규칙**이어야 한다. 다른 문턱으로 재면 측정이 거짓말을 한다.
OUTLIER_COLUMNS = ("이용시간(분)", "이용거리(km)")
IQR_FACTOR = 1.5

WINDOWS = ("_05_10", "_10_15", "_15_20")


def iqr_bounds(series: pd.Series) -> tuple:
    """IQR × 1.5 울타리. 분포 모양을 가정하지 않는 것이 이 방식의 이유다."""
    q1, q3 = series.quantile(0.25), series.quantile(0.75)
    spread = q3 - q1
    return q1 - IQR_FACTOR * spread, q3 + IQR_FACTOR * spread


def mark_outliers(rentals: pd.DataFrame) -> pd.Series:
    """EDA와 같은 규칙으로 이상치 행을 표시한다(어느 한 컬럼이라도 벗어나면 이상치)."""
    outlier = pd.Series(False, index=rentals.index)
    for column in OUTLIER_COLUMNS:
        values = pd.to_numeric(rentals[column], errors="coerce")
        low, high = iqr_bounds(values.dropna())
        outlier |= ~values.between(low, high)
        print(f"  {column:12} 정상 범위 {low:8.1f} ~ {high:8.1f}"
              f"  → 밖 {int((~values.between(low, high)).sum()):,}건")
    return outlier


def net_demand(rentals: pd.DataFrame) -> pd.DataFrame:
    """대여이력 → (대여소, 날짜, 시각)별 순수요. raw_to_net과 같은 계산이다."""
    rent = rentals[["대여일시", "대여_대여소ID"]].copy()
    rent["날짜"] = pd.to_datetime(rent["대여일시"]).dt.date
    rent["hour"] = pd.to_datetime(rent["대여일시"]).dt.hour
    out = rent.groupby(["날짜", "대여_대여소ID", "hour"]).size().rename("대여")

    back = rentals[["반납일시", "반납_대여소ID"]].copy()
    back["날짜"] = pd.to_datetime(back["반납일시"]).dt.date
    back["hour"] = pd.to_datetime(back["반납일시"]).dt.hour
    into = back.groupby(["날짜", "반납_대여소ID", "hour"]).size().rename("반납")

    out.index.names = into.index.names = ["날짜", "station_id", "hour"]
    joined = pd.concat([out, into], axis=1).fillna(0)
    joined["net"] = joined["대여"] - joined["반납"]
    return joined.reset_index()


def window_frame(net: pd.DataFrame, duration: str) -> pd.DataFrame:
    """(대여소, 날짜)별 그 시간대 순수요."""
    hours = duration_hours(duration)
    part = net[net["hour"].isin(hours)]
    frame = part.groupby(["station_id", "날짜"])["net"].sum().rename("demand").reset_index()
    frame["date"] = pd.to_datetime(frame["날짜"])
    return frame


def plan_from(net: pd.DataFrame, duration: str, day_type: str) -> pd.DataFrame:
    """순수요 → 대여소별 mu·sigma·목표 재고. **계획이 달라지는지**를 보는 자리다.

    재고는 실행 시점 스냅샷이라 여기서는 쓰지 않는다. 대신 `target_qty`(= mu + z·sigma)와
    작업 대상 판정(|mu| > 문턱)까지만 본다 — 재고를 넣으면 그날 스냅샷의 잡음이
    이상치 효과와 섞인다.
    """
    frame = window_frame(net, duration)
    frame = select_day_type(frame, "date", day_type)
    if frame.empty:
        return frame
    stats = frame.groupby("station_id")["demand"].agg(mu="mean", sigma="std").fillna(0)
    stats["target_qty"] = stats["mu"] + TARGET_Z * stats["sigma"]
    stats["작업대상"] = stats["mu"].abs() > REBAL_MIN_QTY
    return stats


def compare(keep: pd.DataFrame, drop: pd.DataFrame, label: str) -> dict:
    """두 계획을 맞대어 본다. 인덱스가 다를 수 있으므로 합집합으로 맞춘다."""
    if keep.empty or drop.empty:
        return {}
    joined = keep.join(drop, how="outer", lsuffix="_전체", rsuffix="_제거").fillna(0)

    target_a = joined["작업대상_전체"].astype(bool)
    target_b = joined["작업대상_제거"].astype(bool)
    both = int((target_a & target_b).sum())
    only_a = int((target_a & ~target_b).sum())
    only_b = int((~target_a & target_b).sum())

    mu_gap = (joined["mu_제거"] - joined["mu_전체"]).abs()
    qty_gap = (joined["target_qty_제거"] - joined["target_qty_전체"]).abs()
    scope = target_a | target_b            # 작업 대상에서만 본다(이 저장소의 규칙)

    return {
        "구분": label,
        "대여소": len(joined),
        "작업대상(전체)": int(target_a.sum()),
        "작업대상(제거)": int(target_b.sum()),
        "둘 다": both, "전체만": only_a, "제거만": only_b,
        "mu 평균차": float(mu_gap[scope].mean()) if scope.any() else 0.0,
        "mu 최대차": float(mu_gap[scope].max()) if scope.any() else 0.0,
        "목표재고 평균차": float(qty_gap[scope].mean()) if scope.any() else 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="이상치 제거가 계획을 바꾸는가")
    parser.add_argument("--period", default=None, help="기간 (기본: 적재된 마지막 달)")
    parser.add_argument("--day-type", default="weekday", choices=("weekday", "holiday"))
    args = parser.parse_args()

    with db.session() as conn:
        periods = [r[0] for r in conn.execute(
            "SELECT DISTINCT period FROM rental_history ORDER BY period")]
    if not periods:
        print("대여이력이 DB에 없습니다. tools/load_rentals.py로 적재하세요.")
        return 1
    period = args.period or periods[-1]

    rentals, source = db.read_rental_source(
        period, columns=["대여일시", "대여_대여소ID", "반납일시", "반납_대여소ID",
                         *OUTLIER_COLUMNS])
    if rentals.empty:
        print(f"'{period}' 대여이력이 없습니다. 적재된 기간: {', '.join(periods)}")
        return 1

    print(f"기간 {period} · {len(rentals):,}행 (출처: {source}) · 요일 {args.day_type}\n")

    print("① 얼마나 잘려 나가는가 (EDA와 같은 IQR × 1.5 규칙)")
    outlier = mark_outliers(rentals)
    share = outlier.mean()
    print(f"  → 이상치로 걸리는 행 {int(outlier.sum()):,}건 / {len(rentals):,}건"
          f" = **{share * 100:.1f}%**\n")

    kept = rentals[~outlier]
    print("② 순수요가 얼마나 달라지는가")
    net_all = net_demand(rentals)
    net_kept = net_demand(kept)
    total_all = net_all["net"].abs().sum()
    total_kept = net_kept["net"].abs().sum()
    print(f"  |순수요| 총합  전체 {total_all:,.0f}  →  제거 {total_kept:,.0f}"
          f"  ({(total_kept / total_all - 1) * 100:+.1f}%)\n")

    print("③ 계획이 달라지는가  ← 실제로 중요한 것은 이것뿐이다")
    rows = []
    for duration in WINDOWS:
        result = compare(plan_from(net_all, duration, args.day_type),
                         plan_from(net_kept, duration, args.day_type), duration)
        if result:
            rows.append(result)
    if not rows:
        print("  비교할 시간대가 없습니다.")
        return 1

    summary = pd.DataFrame(rows)
    print(f"{'시간대':8} {'작업대상(전체)':>12} {'작업대상(제거)':>12} "
          f"{'한쪽에만':>8} {'mu 평균차':>10} {'mu 최대차':>10} {'목표재고 평균차':>14}")
    for _, r in summary.iterrows():
        print(f"{r['구분']:8} {r['작업대상(전체)']:12d} {r['작업대상(제거)']:12d} "
              f"{r['전체만'] + r['제거만']:8d} {r['mu 평균차']:10.3f} "
              f"{r['mu 최대차']:10.3f} {r['목표재고 평균차']:14.3f}")

    swapped = int(summary["전체만"].sum() + summary["제거만"].sum())
    scope = int(summary["작업대상(전체)"].sum())
    if scope:
        print(f"\n작업 대상이 뒤바뀐 대여소 {swapped}곳 / {scope}곳"
              f" = **{swapped / scope * 100:.1f}%**")

    print("\n④ 그런데 IQR이 자르는 것이 정말 '이상치'인가")
    describe_range(rentals)

    print("\n⑤ 명백한 오류만 걸렀을 때 (아래 규칙)")
    broken = obvious_errors(rentals)
    print(f"  → {int(broken.sum()):,}건 / {len(rentals):,}건 = {broken.mean() * 100:.1f}%")
    net_sane = net_demand(rentals[~broken])
    rows = []
    for duration in WINDOWS:
        result = compare(plan_from(net_all, duration, args.day_type),
                         plan_from(net_sane, duration, args.day_type), duration)
        if result:
            rows.append(result)
    if rows:
        sane = pd.DataFrame(rows)
        swapped_sane = int(sane["전체만"].sum() + sane["제거만"].sum())
        print(f"  작업 대상 뒤바뀜 {swapped_sane}곳 / {scope}곳"
              f" = **{swapped_sane / scope * 100:.1f}%**"
              f"  ·  목표재고 평균차 {sane['목표재고 평균차'].mean():.3f}대")

    print("\n⑥ 순수요가 **실제로 쓰는 네 필드**는 성한가")
    integrity(period)

    print("\n판정 기준")
    print("  뒤바뀜 < 1% 이고 목표재고 평균차 < 0.5대  →  그 필터는 안 옮겨도 된다.")
    print("  그보다 크다  →  옮길지 말지를 따로 판단해야 한다(무엇을 자르는지 보고).")
    print("  ⑥에 결함이 있으면  →  **거기부터** 고친다. 이용시간·이용거리를 근거로")
    print("     행을 지우는 것은 순수요를 고치는 일이 아니다.")
    return 0


def integrity(period: str) -> None:
    """순수요 계산이 읽는 네 필드만 검사한다.

    **이용시간·이용거리는 여기 없다.** 순수요는 '언제 어느 대여소에서 한 대가 나갔나'만
    세므로, 거리 기록이 실패했다고 그 대여가 없던 일이 되지는 않는다. 지워야 할 것이
    있다면 **시각이나 대여소 ID 자체가 이상한** 기록뿐이다.
    """
    rentals, _ = db.read_rental_source(
        period, columns=["대여일시", "대여_대여소ID", "반납일시", "반납_대여소ID"])
    rent = pd.to_datetime(rentals["대여일시"], errors="coerce")
    back = pd.to_datetime(rentals["반납일시"], errors="coerce")

    with db.session() as conn:
        known = {row[0] for row in conn.execute("SELECT station_id FROM station_info")}

    checks = [
        ("대여 시각 결측", int(rent.isna().sum())),
        ("반납 시각 결측", int(back.isna().sum())),
        ("반납이 대여보다 이름", int((back < rent).sum())),
        ("대여소 ID 결측", int(rentals["대여_대여소ID"].isna().sum()
                              + rentals["반납_대여소ID"].isna().sum())),
    ]
    if known:
        unknown = (~rentals["대여_대여소ID"].astype(str).isin(known)).sum() \
            + (~rentals["반납_대여소ID"].astype(str).isin(known)).sum()
        checks.append((f"마스터({len(known):,}곳)에 없는 대여소 ID", int(unknown)))

    for label, count in checks:
        mark = "✓" if count == 0 else "✗"
        print(f"  {mark} {label:32} {count:,}건")


def describe_range(rentals: pd.DataFrame) -> None:
    """자르는 문턱이 분포의 어디쯤인지 보여 준다.

    **이것이 이 실험의 핵심 화면이다.** IQR은 '분포에서 멀리 떨어진 값'을 자를 뿐,
    '틀린 값'을 자르지 않는다. 원천이 이미 상한에서 잘려 있으면 IQR 울타리는
    **정상 상위 몇 %** 를 자르게 된다.
    """
    for column in OUTLIER_COLUMNS:
        values = pd.to_numeric(rentals[column], errors="coerce").dropna()
        low, high = iqr_bounds(values)
        above = float((values > high).mean())
        print(f"  {column:12} 실측 범위 {values.min():.1f} ~ {values.max():.1f}"
              f"  ·  IQR 상한 {high:.1f} (상위 {above * 100:.1f}%가 잘린다)"
              f"  ·  95분위 {values.quantile(0.95):.1f}")


def obvious_errors(rentals: pd.DataFrame) -> pd.Series:
    """**분포가 아니라 뜻으로** 걸러 낸다 — 있을 수 없는 값만.

    IQR은 "남들과 다르다"를 자르고, 이쪽은 "말이 안 된다"를 자른다. 순수요는 건수만
    세므로, 지워야 할 것이 있다면 **대여 자체가 성립하지 않는 기록**뿐이다.
    """
    minutes = pd.to_numeric(rentals["이용시간(분)"], errors="coerce")
    km = pd.to_numeric(rentals["이용거리(km)"], errors="coerce")
    same_station = rentals["대여_대여소ID"] == rentals["반납_대여소ID"]

    return (
        (minutes <= 0)                      # 시간이 흐르지 않은 대여
        | (minutes >= 24 * 60)              # 하루를 넘김 = 미반납 처리
        | (km < 0)                          # 있을 수 없는 거리
        | ((km == 0) & ~same_station)       # 다른 대여소인데 이동거리가 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
