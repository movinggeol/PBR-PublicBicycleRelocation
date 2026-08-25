"""빼 올 자전거가 정말 있는가 — `pick` 후보의 실재성 검증 (TODO 1-4 후속).

ILP는 군집마다 **총 pick = 총 drop**으로 맞춘다. 즉 "여기서 N대를 빼서 저기에
N대를 놓는다"가 계획의 뼈대다. 그런데 그 전제가 **`pick` 대여소에 실제로 N대가
있다**는 것이다.

1.21.0에서 수집 재고를 처음 들여다보니 **관측의 48.8%가 재고 0**이었고 1,372곳 중
261곳은 내내 비어 있었다. 그렇다면 의심할 만하다.

    계획: "ST1234에서 7대를 빼 온다"
    실제:  그 시각 ST1234에 3대뿐이라면 → 4대는 못 옮긴다
           → 그만큼 drop 쪽도 못 채운다 (ILP가 짝지어 놨으므로)

**계획이 종이 위에서만 균형을 맞추고 있을 수 있다.** 이것을 잰다.

    ① pick 후보의 계획 재고 vs 실측 재고가 얼마나 다른가
    ② 계획한 pick 수량을 **실제로 채울 수 있는 후보가 몇 %인가**
    ③ 못 채우는 양이 계획 전체의 몇 %인가   ← 이것이 '종이 위 계획'의 크기다

**주의**: 계획의 재고(`pick_drop.stock`)는 실행 시점 스냅샷 한 장이고, 수집은
다른 날의 시계열이다. 날짜가 다르므로 **개별 대여소의 오차가 아니라 분포·경향**으로
읽어야 한다. 그래서 시각별로 재서 '하루 중 언제 가능한가'까지 본다.

실행:
    python experiments/structure/pick_feasibility.py
    python experiments/structure/pick_feasibility.py --duration _05_10
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pandas as pd

import db
from project_config import REBAL_MIN_QTY, duration_hours

WINDOWS = ("_05_10", "_10_15", "_15_20")


def load_candidates(run_label: str) -> pd.DataFrame:
    """계획이 고른 pick 후보와 **계획이 전제한 재고**."""
    with db.session() as conn:
        frame = pd.read_sql(
            "SELECT duration, station_id, stock, rebal_qty FROM pick_drop"
            " WHERE run_label = ?", conn, params=[run_label])
    picks = frame[frame["rebal_qty"] < 0].copy()
    picks["뺄대수"] = picks["rebal_qty"].abs()
    return picks


def load_observed() -> pd.DataFrame:
    with db.session() as conn:
        frame = db.load_stock_history(conn)
    if frame.empty:
        return frame
    frame["관측"] = pd.to_datetime(frame["observed_at"])
    frame["시각"] = frame["관측"].dt.hour
    return frame


def feasibility(picks: pd.DataFrame, observed: pd.DataFrame, duration: str) -> dict:
    """그 시간대에 pick을 **실제로 채울 수 있는가**.

    수집 창(09~17시)과 겹치는 시각만 본다. 겹치는 시각이 없으면 판단하지 않는다 —
    없는 자료로 '가능하다/불가능하다'를 말하면 안 된다.
    """
    hours = [h for h in duration_hours(duration) if h in set(observed["시각"])]
    if not hours:
        return {}

    window = observed[observed["시각"].isin(hours)]
    # 그 창에서 대여소별 **중앙 재고**. 최댓값을 쓰면 하루 중 한 순간만 가능해도
    # 가능하다고 세게 되고, 최솟값은 지나치게 비관적이다.
    typical = window.groupby("station_id")["stock"].median()

    part = picks[picks["duration"] == duration].copy()
    part["실측재고"] = part["station_id"].map(typical)
    known = part.dropna(subset=["실측재고"])
    if known.empty:
        return {}

    # 계획을 채우려면 뺀 뒤에도 최소 재고가 남아야 한다는 규칙은 계획 단계에 있다.
    # 여기서는 **물리적으로 있는가**만 본다 — 없는 자전거는 뺄 수 없다.
    known["채울수있는대수"] = known[["뺄대수", "실측재고"]].min(axis=1)
    shortfall = float((known["뺄대수"] - known["채울수있는대수"]).sum())

    return {
        "시간대": duration,
        "겹치는시각": len(hours),
        "pick후보": len(part),
        "관측된후보": len(known),
        "계획재고중앙": float(known["stock"].median()),
        "실측재고중앙": float(known["실측재고"].median()),
        "계획pick": int(known["뺄대수"].sum()),
        "채울수있음": int(known["채울수있는대수"].sum()),
        "못채움": int(shortfall),
        "완전가능비율": float((known["실측재고"] >= known["뺄대수"]).mean()),
        "재고0비율": float((known["실측재고"] <= 0).mean()),
    }


def by_hour(picks: pd.DataFrame, observed: pd.DataFrame, duration: str) -> pd.DataFrame:
    """하루 중 **언제** 빼 올 수 있나. 회차 시각을 옮길 여지가 있는지 본다."""
    part = picks[picks["duration"] == duration]
    rows = []
    for hour, group in observed.groupby("시각"):
        stock = group.groupby("station_id")["stock"].median()
        merged = part.assign(실측=part["station_id"].map(stock)).dropna(subset=["실측"])
        if merged.empty:
            continue
        rows.append({
            "시각": hour,
            "가능비율": float((merged["실측"] >= merged["뺄대수"]).mean()),
            "평균재고": float(merged["실측"].mean()),
        })
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="pick 후보 실재성 검증")
    parser.add_argument("--duration", help="시간대 하나만")
    parser.add_argument("--run-label", help="실행 라벨 (기본: 최신)")
    args, _ = parser.parse_known_args()

    observed = load_observed()
    if observed.empty:
        print("수집된 재고가 없습니다. scripts/collector.ps1 install 로 시작하세요.")
        return 1

    with db.session() as conn:
        label = args.run_label or conn.execute(
            "SELECT MAX(run_label) FROM pick_drop").fetchone()[0]
    if not label:
        print("pick_drop이 비어 있습니다. 파이프라인을 돌리세요.")
        return 1

    picks = load_candidates(label)
    days = observed["관측"].dt.strftime("%Y-%m-%d").nunique()
    print(f"계획 '{label}' · 수집 {observed['관측'].nunique()}틱 / {days}일"
          f" · 창 {observed['시각'].min()}~{observed['시각'].max()}시\n")

    durations = [args.duration] if args.duration else list(WINDOWS)
    rows = [r for r in (feasibility(picks, observed, d) for d in durations) if r]
    if not rows:
        print("수집 창과 겹치는 시간대가 없습니다.")
        return 1

    print("① 계획이 전제한 재고 vs 실측 재고 (pick 후보, 중앙값)")
    print(f"{'시간대':8} {'후보':>5} {'관측됨':>6} {'계획재고':>8} {'실측재고':>8} {'재고0':>7}")
    for r in rows:
        print(f"{r['시간대']:8} {r['pick후보']:5d} {r['관측된후보']:6d} "
              f"{r['계획재고중앙']:8.1f} {r['실측재고중앙']:8.1f} "
              f"{r['재고0비율'] * 100:6.0f}%")

    print("\n② 계획한 pick을 실제로 채울 수 있나")
    print(f"{'시간대':8} {'계획pick':>8} {'채울수있음':>10} {'못채움':>7} "
          f"{'완전가능':>8}")
    for r in rows:
        print(f"{r['시간대']:8} {r['계획pick']:8d} {r['채울수있음']:10d} "
              f"{r['못채움']:7d} {r['완전가능비율'] * 100:7.0f}%")

    total_plan = sum(r["계획pick"] for r in rows)
    total_short = sum(r["못채움"] for r in rows)
    print(f"\n③ 못 채우는 양 {total_short}대 / 계획 {total_plan}대"
          f" = **{total_short / total_plan * 100:.1f}%**" if total_plan else "")

    print("\n④ 하루 중 언제 빼 올 수 있나 (계획한 만큼 있는 후보의 비율)")
    for duration in durations:
        hourly = by_hour(picks, observed, duration)
        if hourly.empty:
            continue
        marks = " ".join(f"{int(r['시각']):02d}시 {r['가능비율'] * 100:3.0f}%"
                         for _, r in hourly.iterrows())
        print(f"  {duration}: {marks}")

    print("\n읽는 법")
    print("  · 계획 재고는 실행 시점 스냅샷 한 장, 실측은 다른 날의 시계열이다.")
    print("    **개별 대여소가 아니라 분포·경향으로** 읽어야 한다.")
    print("  · 못 채우는 양이 크면 ILP의 '총 pick = 총 drop'이 종이 위에서만 성립한다는")
    print("    뜻이다 — pick이 모자라면 drop도 그만큼 못 채운다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
