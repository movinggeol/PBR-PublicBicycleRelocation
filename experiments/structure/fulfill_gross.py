"""실험 — 수요 충족률을 **총 대여 건수**로 다시 잰다 (TODO P3 5번).

## 무엇이 문제인가

1.26.101에서 넣은 `demand_fulfill`은 **순수요 기준**이다.

    충족률 = (순유출 − 못 빌린 양) / 순유출        ← 지금
    순유출 = Σ max(0, 대여 − 반납)

`step4_metrics/imbalance.py`가 스스로 경고를 달아 두었다 —
*"`unmet`/`outflow`는 순수요 기준이라 '총 대여 건수'가 아니다."*

🔴 **왜 반쪽인가.** 한 시간에 10명이 빌리고 10명이 반납한 대여소는 순수요가 0이라
**"수요가 없었다"** 로 세어진다. 실제로는 **10명이 자전거를 가져갔다.** 분모가
작아지므로 충족률이 실제 이용자 경험과 다른 값을 가리킬 수 있다.

## 두 정의를 나란히 잰다

이 실험은 **운영 코드를 한 줄도 바꾸지 않는다.** `rental_history`(대여이력
540만 건)에서 시간대별 **총 대여 건수**를 직접 세어, 같은 재고 궤적 위에서 두
정의를 함께 계산한다.

| 정의 | 못 빌린 양 | 분모 | 무엇을 묻나 |
| --- | --- | --- | --- |
| **순수요**(현행) | max(0, 순유출 − 재고) | Σ 순유출 | 재배치가 **다뤄야 할 순 부족분** 중 얼마를 메웠나 |
| **총대여**(이 실험) | max(0, 총대여 − 재고) | Σ 총대여 | 자전거를 **가져간 사람** 중 얼마가 그럴 수 있었나 |

🔴 **처음에 '낙관 대 보수'라고 적었다가 재 보고 지웠다.** 못 빌린 양은 총대여
쪽이 늘 크지만(총대여 ≥ 순유출이라 점별로 크거나 같다), **분모가 더 빨리 커져서
비율의 순서가 뒤집힐 수 있다.** 실제로 `_15_20`에서 총대여 기준이 **더 높게**
나왔다(0.489 대 0.378).

즉 **둘은 같은 것의 상·하한이 아니라 서로 다른 물음**이다. 하나를 다른 하나로
대신할 수 없고, **한쪽만 인용하면 오해를 부른다.**

## 🔴 대여이력으로는 '실패한 대여'를 셀 수 없다

`rental_history`에는 **성사된 대여만** 남는다. 자전거가 없어 발길을 돌린 사람은
자료에 없다. 그래서 이 실험이 재는 것은 *"관측된 대여가 계획된 재고로 가능했겠는가"*
이지 **진짜 수요 충족률이 아니다.** 논문에 쓸 때 이 구분을 지켜라.

사용법:
    python experiments/structure/fulfill_gross.py
    python experiments/structure/fulfill_gross.py --period "26년 03월" --day-type holiday
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import db  # noqa: E402
from step0_collect.calculate_target_qty import duration_columns  # noqa: E402


def load_baseline():
    path = ROOT / "experiments" / "baseline" / "baseline_compare.py"
    spec = importlib.util.spec_from_file_location("baseline_compare", path)
    bc = importlib.util.module_from_spec(spec)
    sys.modules["baseline_compare"] = bc
    spec.loader.exec_module(bc)
    return bc


def gross_rentals(period: str) -> pd.DataFrame:
    """대여이력에서 대여소·날짜·시각별 **총 대여 건수**를 센다.

    반납은 세지 않는다 — 이 지표의 분모는 '자전거를 가져간 사람 수'다.
    """
    query = """
        SELECT rent_station AS station_id,
               date(rent_at)                AS date,
               CAST(strftime('%H', rent_at) AS INTEGER) AS hour,
               COUNT(*)                     AS rentals
        FROM rental_history
        WHERE period = ? AND rent_station IS NOT NULL AND rent_at IS NOT NULL
        GROUP BY 1, 2, 3
    """
    with db.connect() as conn:
        return pd.read_sql_query(query, conn, params=(period,))


def simulate(net, gross, stations, initial, capacity, hours):
    """재고 궤적을 한 번 돌며 **두 정의의 못 빌린 양**을 함께 센다.

    재고 변화는 **순수요로만** 일어난다(물리적으로 그것이 맞다). 다른 것은
    '못 빌렸다'를 무엇으로 세느냐뿐이다.
    """
    stock = initial.astype(float).copy()
    out = {"unmet_net": pd.Series(0.0, index=stations),
           "unmet_gross": pd.Series(0.0, index=stations),
           "outflow_net": pd.Series(0.0, index=stations),
           "outflow_gross": pd.Series(0.0, index=stations)}

    for hour in hours:
        flow = net[hour].reindex(stations).fillna(0.0)
        rent = gross[hour].reindex(stations).fillna(0.0)

        raw = stock - flow
        out["unmet_net"] += (-raw).clip(lower=0)
        out["outflow_net"] += flow.clip(lower=0)

        # 보수적 정의 — 그 시간의 반납을 기대하지 않는다.
        out["unmet_gross"] += (rent - stock).clip(lower=0)
        out["outflow_gross"] += rent

        stock = raw.clip(lower=0).clip(upper=capacity)
    return out


def rate(unmet: float, total: float) -> float:
    return max(0.0, (total - unmet) / total) if total > 0 else float("nan")


def measure(bc, args) -> pd.DataFrame:
    net, st_info, _warmup = bc.load_inputs(
        args.period, args.run_label, args.day_type, 0, "")
    gross_raw = gross_rentals(args.period)
    if gross_raw.empty:
        print(f"🔴 `{args.period}` 대여이력이 없습니다.")
        return pd.DataFrame()

    # 순수요와 같은 날짜만 남긴다 — 평일/휴일 구분을 그대로 따라간다.
    net_dates = set(pd.to_datetime(net["날짜"]).dt.strftime("%Y-%m-%d"))
    gross_raw = gross_raw[gross_raw["date"].isin(net_dates)]
    days = len(net_dates)
    print(f"[자료] 순수요 {len(net)}행 · 대여 {int(gross_raw['rentals'].sum()):,}건"
          f" · {days}일 · 대여소 {len(st_info)}곳", flush=True)

    stations = st_info["station_id"]
    initial = st_info.set_index("station_id")["stock"].reindex(stations).fillna(0.0)
    capacity = st_info.set_index("station_id")["parking_lot"].reindex(stations).fillna(0.0)

    rows = []
    for duration in args.durations:
        hours = [int(c.split("_")[-1]) for c in duration_columns(duration)]

        # 하루 평균으로 눌러 담는다 — 궤적은 '평균적인 하루'를 돈다.
        net_h = {}
        for hour in hours:
            column = f"net_{hour:02d}"
            net_h[hour] = (net.groupby("station_id")[column].sum() / days)
        gross_h = {}
        for hour in hours:
            part = gross_raw[gross_raw["hour"] == hour]
            gross_h[hour] = (part.groupby("station_id")["rentals"].sum() / days)

        sim = simulate(net_h, gross_h, stations, initial, capacity, hours)
        row = {
            "duration": duration,
            "총대여_일평균": round(float(sum(g.sum() for g in gross_h.values())), 1),
            "순유출_일평균": round(float(sim["outflow_net"].sum()), 1),
            "충족률_순수요": round(rate(float(sim["unmet_net"].sum()),
                                   float(sim["outflow_net"].sum())), 4),
            "충족률_총대여": round(rate(float(sim["unmet_gross"].sum()),
                                   float(sim["outflow_gross"].sum())), 4),
        }
        row["분모배율"] = (round(row["총대여_일평균"] / row["순유출_일평균"], 2)
                       if row["순유출_일평균"] else float("nan"))
        rows.append(row)
        print(f"  {duration} 완료", flush=True)
    return pd.DataFrame(rows)


def report(df) -> None:
    if df.empty:
        return
    print("\n" + "=" * 88)
    print("수요 충족률 — 두 정의 (재배치 전, 같은 재고 궤적)")
    print("=" * 88)
    print(df.to_string(index=False))

    gap = (df["충족률_순수요"] - df["충족률_총대여"]).abs()
    ratio = df["분모배율"]
    print(f"\n분모가 {ratio.min():.1f}~{ratio.max():.1f}배 커진다"
          f" — 순수요는 총 대여의 {1 / ratio.max():.0%}~{1 / ratio.min():.0%}만 센다.")
    print(f"충족률 차이는 {gap.min():.4f}~{gap.max():.4f}"
          f" ({gap.min() * 100:.1f}~{gap.max() * 100:.1f}%p)다.")
    flip = df[df["충족률_총대여"] > df["충족률_순수요"]]["duration"].tolist()
    if flip:
        print(f"🔴 순서가 뒤집힌 회차: {', '.join(flip)} — 총대여 기준이 **더 높다.**"
              " 둘은 상·하한이 아니다.")

    print("\n읽는 법")
    print("  · 두 값은 **같은 것의 경계가 아니라 서로 다른 물음**이다.")
    print("    못 빌린 양은 총대여 쪽이 늘 크지만 분모가 더 빨리 커져 비율은 뒤집힐 수 있다.")
    print("  · 대여이력에는 **성사된 대여만** 있다 — 발길을 돌린 사람은 어디에도 없다.")
    print("  · 분모배율이 크다는 것은 **반납과 대여가 같은 대여소에서 맞물린다**는 뜻이다.")


def main() -> int:
    parser = argparse.ArgumentParser(description="충족률을 총 대여 건수로 다시 잰다")
    parser.add_argument("--period", default="")
    parser.add_argument("--durations", default="_05_10,_10_15,_15_20")
    parser.add_argument("--day-type", default="weekday")
    parser.add_argument("--run-label", default="")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    from project_config import DEFAULT_PERIOD
    if not args.period:
        args.period = DEFAULT_PERIOD
    args.durations = [d.strip() for d in args.durations.split(",") if d.strip()]

    bc = load_baseline()
    df = measure(bc, args)
    report(df)
    if args.out and not df.empty:
        df.to_csv(args.out, index=False, encoding="utf-8-sig")
        print(f"\n저장: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
