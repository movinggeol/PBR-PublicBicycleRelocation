"""시간 예산을 **제약으로 걸면** 무엇을 잃고 무엇을 얻나 (TODO: 시간 예산 1단계).

지금 시간 예산(120분)은 **제약이 아니라 사후 점검**이다 — 넘겨도 계획을 바꾸지 않고
표시만 한다. 실측 준수율은 78~85%이고, `_05_10`에는 196분짜리 회차도 있다.

`greedy_route()`에는 **이미 `time_budget_sec` 인자가 있다.** 주면 "이 작업을 하고
depot까지 돌아올 수 있을 때만" 간다. 파이프라인이 안 넘길 뿐이다.

그런데 켜면 **공짜가 아니다.**

    예산에서 멈춘다 → 남은 작업이 미집행으로 남는다 → 그 대여소는 결품이 는다

그래서 켜기 전에 잰다. 묻는 것은 셋이다.

    ① 예산을 지키게 되는가        (초과 회차가 사라지나)
    ② 얼마를 못 옮기게 되는가      (미집행 대수)
    ③ **결품이 어떻게 되는가**      ← 이것만이 판정 기준이다

③에서 나빠지면 켜지 않는다. 예산을 지키자고 이용자를 더 불편하게 만들 수는 없다.

실행:
    python experiments/baseline/budget_enforce.py
    python experiments/baseline/budget_enforce.py --duration _05_10
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
# step 폴더를 sys.path에 밀어 넣지 않는다 — 폴더 이름으로 부른다(1.26.154).

import pandas as pd

import db
from step2_optimize import vrp as vrp_mod                              # noqa: E402  (step2)
from project_config import (                       # noqa: E402
    DURATIONS, TIME_BUDGET_MINUTES, get_runtime_config,
)

WINDOWS = ("_05_10", "_10_15", "_15_20")


def load_plan(run_label: str, duration: str) -> pd.DataFrame:
    """그 실행의 ILP 이동 계획. VRP의 입력이다."""
    with db.session() as conn:
        return pd.read_sql(
            "SELECT * FROM ilp_plan WHERE run_label = ? AND duration = ?",
            conn, params=[run_label, duration])


def station_points(run_label: str) -> dict:
    """대여소 좌표. ilp_plan에는 없으므로 station_info에서 가져온다."""
    with db.session() as conn:
        info = pd.read_sql(
            "SELECT station_id, lat, lon FROM station_info WHERE run_label = ?",
            conn, params=[run_label])
        if info.empty:      # 그 실행분이 없으면 최신 것으로
            info = pd.read_sql(
                "SELECT station_id, lat, lon FROM station_info"
                " WHERE run_label = (SELECT MAX(run_label) FROM station_info)", conn)
    return {r.station_id: (r.lat, r.lon) for r in info.itertuples()}


def check_stations_known(plan: pd.DataFrame, points: dict) -> None:
    """모든 대여소가 좌표를 갖고 있는지 미리 확인한다 — `run_vrp_plan()`과 같은 규약
    (step2_optimize/vrp.py:250-255).

    🔴 **예전에는 없었다 — 없으면 `build_nodes()`가 `points[sid]`에서 어느
    대여소 탓인지 안 보이는 `KeyError`로 죽었다(1.26.127에서 발견).** 실측
    (2026-09-07, 현재 DB의 ilp_plan·station_info 6개 조합)으로는 걸리는 대여소가
    없었지만, 죽더라도 **무엇이 빠졌는지는 밝히고 죽어야** 한다.
    """
    missing = ({*plan["pick_station_id"], *plan["drop_station_id"]} - set(points))
    if missing:
        raise SystemExit(
            f"ILP 계획의 대여소가 좌표(station_info)에 없습니다: "
            f"{sorted(missing)[:5]} … ({len(missing)}곳). "
            f"station_info의 run_label이 ilp_plan과 같은지 확인하세요.")


def build_nodes(cluster_plan: pd.DataFrame, points: dict) -> dict:
    """run_vrp_plan()과 **같은 방식**으로 노드를 만든다.

    측정 코드가 제 방식대로 만들면 비교가 성립하지 않는다. 좌표 결측은 여기서
    걸러지지 않는다 — 호출 측이 먼저 `check_stations_known()`으로 확인해 둔다.
    """
    nodes = {}
    for sid, qty in cluster_plan.groupby("pick_station_id")["qty"].sum().items():
        lat, lon = points[sid]
        nodes[(sid, "pick")] = {"qty": int(qty), "lat": lat, "lon": lon}
    for sid, qty in cluster_plan.groupby("drop_station_id")["qty"].sum().items():
        lat, lon = points[sid]
        nodes[(sid, "drop")] = {"qty": int(qty), "lat": lat, "lon": lon}
    return nodes


def run(plan: pd.DataFrame, points: dict, budget_sec) -> pd.DataFrame:
    """모든 군집을 돌려 VRP 결과를 만든다. budget_sec=None이면 현행 동작."""
    rows = []
    for cluster, part in plan.groupby("cluster"):
        rows.extend(vrp_mod.greedy_route(build_nodes(part, points), cluster,
                                         time_budget_sec=budget_sec))
    return pd.DataFrame(rows)


def summarize(result: pd.DataFrame, planned_bikes: int) -> dict:
    """회차별 소요시간과 집행량."""
    if result.empty:
        return {}
    per_cluster = result.groupby("cluster")["cum_sec"].max() / 60.0
    moved = int(result.loc[result["action"] == "pick", "qty"].sum())
    return {
        "군집": len(per_cluster),
        "최장분": float(per_cluster.max()),
        "초과": int((per_cluster > TIME_BUDGET_MINUTES).sum()),
        "옮긴대수": moved,
        "미집행": planned_bikes - moved,
        "거리km": float(result["distance_km"].sum()),
    }


def stockout_compare(label: str, durations: list, budget_sec: float) -> None:
    """**판정 기준.** 예산을 걸면 결품이 어떻게 되는가.

    step4의 함수(`executed_delta`·`_stockout_hours`)를 **그대로** 쓴다. 측정 코드가
    제 방식대로 결품을 세면 파이프라인이 내는 값과 달라져 비교가 성립하지 않는다.
    """
    import imbalance as kpi_mod                     # noqa: E402  (step4)

    net = kpi_mod.load_net_demand()
    if net.empty:
        print("\n(결품 비교: 순수요가 없어 건너뜁니다)")
        return

    with db.session() as conn:
        stock = pd.read_sql(
            "SELECT station_id, stock, parking_lot FROM station_info"
            " WHERE run_label = (SELECT MAX(run_label) FROM station_info)", conn)

    print("\n결품 시간 (대여소·일 평균 h) — **이것이 판정 기준이다**")
    print(f"{'시간대':8} {'현행':>8} {'예산 강제':>10} {'차이':>8}")
    for duration in durations:
        plan = load_plan(label, duration)
        if plan.empty:
            continue
        points = station_points(label)
        check_stations_known(plan, points)
        hours = kpi_mod.duration_hours(duration)

        values = []
        for budget in (None, budget_sec):
            result = run(plan, points, budget)
            moved = kpi_mod.executed_delta(result)
            merged = net.merge(stock, on="station_id", how="inner")
            if merged.empty:
                break
            merged["moved"] = merged["station_id"].map(moved).fillna(0)
            after = kpi_mod._stockout_hours(
                merged, merged["stock"] + merged["moved"],
                merged["parking_lot"], hours)
            values.append(float(after.mean()))

        if len(values) == 2:
            gap = values[1] - values[0]
            mark = "나빠짐" if gap > 0.01 else ("같음" if abs(gap) <= 0.01 else "좋아짐")
            print(f"{duration:8} {values[0]:8.3f} {values[1]:10.3f} "
                  f"{gap:+8.3f}  {mark}")


def main() -> int:
    parser = argparse.ArgumentParser(description="시간 예산을 제약으로 걸면?")
    parser.add_argument("--duration", help="시간대 하나만")
    parser.add_argument("--run-label", help="실행 라벨 (기본: 최신)")
    parser.add_argument("--budget", type=float, default=TIME_BUDGET_MINUTES,
                        help=f"예산(분). 기본 {TIME_BUDGET_MINUTES:.0f}")
    args, _ = parser.parse_known_args()

    with db.session() as conn:
        # 사전순 MAX를 쓰지 않는다 — 실험 라벨이 날짜 라벨을 이긴다(1.26.127).
        label = args.run_label or db.latest_label(conn, "ilp_plan", kinds=("plan",))
    if not label:
        print("ilp_plan이 비어 있습니다. 파이프라인을 한 번 돌리세요.")
        return 1

    durations = [args.duration] if args.duration else list(WINDOWS)
    print(f"실행 '{label}' · 예산 {args.budget:.0f}분")
    import imbalance as kpi_mod                     # noqa: E402  (step4)
    kpi_mod.use_run_day_type(label)                 # 오늘 달력이 아니라 그 실행의 요일로
    print()

    print(f"{'시간대':8} {'구분':10} {'군집':>4} {'최장분':>7} {'초과':>4} "
          f"{'옮긴대수':>8} {'미집행':>7} {'거리km':>8}")
    rows, 빈회차 = [], []
    for duration in durations:
        plan = load_plan(label, duration)
        if plan.empty:
            빈회차.append(duration)      # 말없이 건너뛰지 않는다
            continue
        planned = int(plan["qty"].sum())
        points = station_points(label)
        check_stations_known(plan, points)

        for name, budget in (("현행(무제한)", None), ("예산 강제", args.budget * 60)):
            summary = summarize(run(plan, points, budget), planned)
            if not summary:
                continue
            rows.append({"시간대": duration, "구분": name, **summary})
            print(f"{duration:8} {name:10} {summary['군집']:4d} {summary['최장분']:7.1f} "
                  f"{summary['초과']:4d} {summary['옮긴대수']:8d} {summary['미집행']:7d} "
                  f"{summary['거리km']:8.1f}")

    if not rows:
        print(f"\n이 실행에는 {', '.join(durations)} 자료가 없습니다.")
        with db.session() as conn:
            pairs = conn.execute(
                "SELECT duration, run_label FROM ilp_plan"
                " GROUP BY duration, run_label ORDER BY duration").fetchall()
        if pairs:
            print("\n회차를 가진 실행:")
            for duration, run_label in pairs:
                print(f"  {duration}  --run-label \"{run_label}\"")
        return 1

    stockout_compare(label, durations, args.budget * 60)

    if 빈회차:
        print(f"\n⚠️  이 실행에 자료가 없는 회차: {', '.join(빈회차)}")
        print(f"    위 표는 {len(durations) - len(빈회차)}/{len(durations)} 회차만 봤습니다 —"
              " **한 회차만 보고 판단하지 마십시오.**")

    frame = pd.DataFrame(rows)
    before = frame[frame["구분"] == "현행(무제한)"]
    after = frame[frame["구분"] == "예산 강제"]
    lost = int(after["미집행"].sum() - before["미집행"].sum())
    planned_total = int(after["옮긴대수"].sum() + after["미집행"].sum())

    print(f"\n초과 회차 {int(before['초과'].sum())}개 → {int(after['초과'].sum())}개")
    print(f"못 옮기게 되는 대수 {lost}대 / 계획 {planned_total}대"
          f" = {lost / planned_total * 100:.1f}%" if planned_total else "")
    print("\n판정")
    print("  미집행이 적고 초과가 사라지면  → 켤 만하다.")
    print("  미집행이 크면 → 예산을 지키자고 이용자를 더 불편하게 만드는 것이다.")
    print("  **결품 시간으로 확인하기 전에는 켜지 않는다** — 미집행 대수는 대리 지표다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
