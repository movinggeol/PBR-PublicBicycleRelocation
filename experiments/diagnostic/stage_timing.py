"""단계별 계산 시간 — 3단계 분해가 정말 '풀리는 크기'를 만드는가.

원고 3.1이 *"군집화는 약 42초, 정수선형계획과 경로 계산은 군집당 수 초"* 라고
적고 있는데, 그 42초에는 **조건이 없다.** 어느 환경에서, 어느 회차를, 대여소
몇 곳으로 쟀는지가 원천(DECISIONS 1장 · RETROSPECTIVE ⑤)에 없다. 게다가
1.23.8이 군집 조정을 8.6배 빠르게 만든 뒤로 **다시 재지 않았다.**

그래서 조건과 함께 다시 잰다. 재는 것은 셋이다.

    군집화   make_clustering + adjust_clustering
    ILP      solve_cluster_moves (군집마다 한 번)
    경로     greedy_route (군집마다 한 번)

측정 코드는 운영 코드를 그대로 부른다 — `baseline_compare.py`의 P 계획 경로와
같은 함수·같은 순서다. 측정이 제 방식대로 계산하면 측정이 거짓말을 한다.

판정 기준은 없다. **측정만 한다** — 이 값은 방법을 고르는 근거가 아니라
분해가 계산을 작게 만든다는 3.1의 서술을 뒷받침하는 조건부 사실이다.

사용법:
    python experiments/diagnostic/stage_timing.py --run-label "2026-08-11 real"
    python experiments/diagnostic/stage_timing.py --duration "_05_10" --repeat 5
"""
import argparse
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments" / "baseline"))

import project_config  # noqa: E402,F401  (콘솔 인코딩을 먼저 잡는다)
import baseline_compare as bc  # noqa: E402
from project_config import DEFAULT_PERIOD, TARGET_Z  # noqa: E402


def measure(candidates, step1, solver, seed):
    """군집화 · ILP · 경로를 나누어 잰다. 반환은 초 단위와 군집 수."""
    t0 = time.perf_counter()
    clustered = bc.quiet(step1.make_clustering, candidates.copy(),
                         random_state=seed).copy()
    clustered = bc.quiet(step1.adjust_clustering, clustered).copy()
    cluster_sec = time.perf_counter() - t0

    frame = clustered.copy()
    frame["drop_qty"] = frame["rebal_qty"].clip(lower=0).astype(int)
    frame["pick_qty"] = (-frame["rebal_qty"].clip(upper=0)).astype(int)

    moves, ilp_sec = [], 0.0
    clusters = list(frame["cluster"].unique())
    for cluster in clusters:
        t = time.perf_counter()
        rows = bc.quiet(bc.ilp_mod.solve_cluster_moves,
                        frame[frame["cluster"] == cluster], solver)
        ilp_sec += time.perf_counter() - t
        for row in rows:
            moves.append({"cluster": cluster, **row})

    route_sec = 0.0
    move_frame = pd.DataFrame(moves)
    if not move_frame.empty:
        coords = clustered.set_index("station_id")[["lat", "lon"]]
        for cluster in move_frame["cluster"].unique():
            part = move_frame[move_frame["cluster"] == cluster]
            nodes = {}
            for sid, qty in part.groupby("pick_station_id")["qty"].sum().items():
                nodes[(sid, "pick")] = {"qty": int(qty),
                                        "lat": coords.loc[sid, "lat"],
                                        "lon": coords.loc[sid, "lon"]}
            for sid, qty in part.groupby("drop_station_id")["qty"].sum().items():
                nodes[(sid, "drop")] = {"qty": int(qty),
                                        "lat": coords.loc[sid, "lat"],
                                        "lon": coords.loc[sid, "lon"]}
            t = time.perf_counter()
            bc.quiet(bc.vrp_mod.greedy_route, nodes, cluster)
            route_sec += time.perf_counter() - t
    return dict(clusters=len(clusters), cluster_sec=cluster_sec,
                ilp_sec=ilp_sec, route_sec=route_sec)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--period", default=DEFAULT_PERIOD)
    ap.add_argument("--duration", default="_05_10,_10_15,_15_20")
    ap.add_argument("--day-type", default="weekday", choices=["weekday", "holiday"])
    ap.add_argument("--run-label", default="2026-08-11 real")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--repeat", type=int, default=3,
                    help="회차마다 몇 번 재서 중앙값을 낼지 (기본 3)")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    durations = [d.strip() for d in args.duration.split(",") if d.strip()]
    net, st_info, warmup = bc.load_inputs(
        args.period, args.run_label, args.day_type, 0, "")
    step1 = bc.load_step1()
    solver = bc.ilp_mod.build_solver()

    rows = []
    for duration in durations:
        t0 = time.perf_counter()
        candidates = bc.build_candidates(net, st_info, duration, TARGET_Z,
                                         warmup, 0, step1)
        prep_sec = time.perf_counter() - t0
        for run in range(args.repeat):
            got = measure(candidates, step1, solver, args.seed)
            rows.append(dict(duration=duration, run=run + 1,
                             stations=len(candidates), prep_sec=prep_sec, **got))
            print(f"  {duration} {run + 1}회 — 군집화 {got['cluster_sec']:.1f}초 · "
                  f"ILP {got['ilp_sec']:.1f}초 · 경로 {got['route_sec']:.2f}초 "
                  f"(군집 {got['clusters']}개)")

    frame = pd.DataFrame(rows)
    print("\n" + "=" * 92)
    print(f"단계별 계산 시간 — {args.period} · {args.day_type} · 씨앗 {args.seed} · "
          f"스냅샷 '{args.run_label}' · {args.repeat}회 중앙값")
    print("=" * 92)
    print(f"{'회차':9}{'작업 대상':>9}{'군집':>6}{'후보 만들기':>12}"
          f"{'군집화':>10}{'ILP 합':>10}{'군집당':>9}{'경로 합':>10}{'군집당':>9}")
    for duration in durations:
        part = frame[frame["duration"] == duration]
        if part.empty:
            continue
        clusters = int(part["clusters"].median())
        ilp, route = part["ilp_sec"].median(), part["route_sec"].median()
        print(f"{duration:9}{int(part['stations'].median()):>9}{clusters:>6}"
              f"{part['prep_sec'].median():>11.1f}초"
              f"{part['cluster_sec'].median():>9.1f}초{ilp:>9.1f}초"
              f"{ilp / max(clusters, 1):>8.2f}초{route:>9.2f}초"
              f"{route / max(clusters, 1):>8.3f}초")
    print("=" * 92)
    print("읽는 법")
    print("  · 후보 만들기는 순수요 집계·목표 재고·작업 대상 선정까지다(회차마다 한 번).")
    print("  · 군집화는 K-Medoids와 수급 균형 조정을 합친 시간이다.")
    print("  · 시간은 기계와 부하에 딸려 있다 — 값을 인용할 때 환경을 함께 밝힌다.")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(args.out, index=False, encoding="utf-8-sig")
        print(f"\n원본 결과를 저장했습니다: {args.out}")


if __name__ == "__main__":
    main()
