"""실험 — `z` 격자를 **결품 시간**으로 잰다 (docs/분석/EXPERIMENTS.md 1장·4장).

4장에서 확립한 규칙은 *"z가 다른 실행끼리는 `target_qty`에 의존하지 않는 결품
시간으로 비교하라"* 인데, **정작 결품 시간으로 잰 z는 1.65와 1.99 두 값뿐이었다.**
커버리지 격자(`z_sweep.py`)는 예닐곱 값인데 판정 지표 격자가 두 값이라,
`z=1.99` 채택이 프로젝트 자신의 판정 규칙 위에서 완결되지 않았다
(docs/기록/TODO.md P4-4의 🔴).

이 스크립트가 그 빈 칸을 메운다. `z`만 바꿔가며 **결품 시간·최장 소요·예산
초과**를 격자로 낸다.

왜 `z_sweep.py`로는 안 되는가
-----------------------------
`z_sweep.py`가 재는 것은 **커버리지(편익)와 작업량(비용)**이지 결품 시간이
아니다. 커버리지는 "mu+z·sigma가 실수요를 덮는 비율"이라 z를 올리면 정의상
단조 증가한다 — z를 올릴수록 좋아 보이는 자다. 결품 시간은 재고 궤적을 복원해
재므로 **z를 올려 작업량이 늘면 시간 예산에 막혀 오히려 나빠질 수 있다.**
판정을 이 자로 해야 하는 이유다.

측정 코드는 운영 코드를 그대로 부른다
-------------------------------------
`baseline_compare.py`의 함수를 그대로 재사용한다(build_candidates·
plan_with_clusters·stockout·route_stats). 그쪽이 이미 `z`를 인자로 받아
B2(z=0)를 만들고 있었으므로, **z만 격자로 바꾸면 되는 구조가 이미 있었다.**
측정 코드가 제 방식대로 계산하면 측정이 거짓말을 한다
(docs/분석/DEMAND_DISTRIBUTION.md 5장에서 실제로 겪었다).

⚠️ 주의
-------
- **`γ`는 3000으로 고정한다.** z와 γ는 연동된다(작업량이 늘면 예산을 압박한다).
  둘을 함께 흔들면 어느 쪽 몫인지 가를 수 없다.
- **한 회차만 보고 판단하지 마라** — 3회차 전부로 재확인한다(과거에 틀린 적 있다).
- **평일과 휴일을 섞지 마라**(프로젝트 규약). 기본은 평일이다.
- 재고 스냅샷 한 장에 `rebal_qty`가 크게 좌우되므로(`target_qty − stock`),
  **`--run-label`로 스냅샷을 고정**하고 그 라벨을 결과에 함께 남긴다.

사용법:
    python experiments/params/z_stockout_grid.py
    python experiments/params/z_stockout_grid.py --run-label "2026-08-11 real"
    python experiments/params/z_stockout_grid.py --z-grid "1.65,1.99,2.10"
    python experiments/params/z_stockout_grid.py --seeds "42,7,13"   # 씨앗 변동성
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]      # experiments/<분류>/ 아래에 있다
sys.path.insert(0, str(ROOT))

import db                                       # noqa: E402
from project_config import (                    # noqa: E402
    CLUSTER_GAMMA, DEFAULT_PERIOD, DEFAULT_WARMUP_DAYS, TARGET_Z,
    TIME_BUDGET_MINUTES, normalize_day_type,
)


def load_baseline():
    """`baseline_compare.py`를 모듈로 불러온다 — 측정 함수를 그대로 쓰기 위해서다.

    파일 이름이 식별자로 쓸 수 있는 형태라 일반 import도 되지만, experiments/의
    분류 폴더가 패키지가 아니므로 경로로 직접 읽는다.
    """
    path = ROOT / "experiments" / "baseline" / "baseline_compare.py"
    spec = importlib.util.spec_from_file_location("baseline_compare", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["baseline_compare"] = module
    spec.loader.exec_module(module)
    return module


Z_GRID = [1.65, 1.80, 1.99, 2.10, 2.33]


def run_grid(bc, net, st_info, warmup, duration, args, step1, solver):
    """한 회차(duration)에서 z 격자를 돈다."""
    print("\n" + "=" * 96)
    print(f"< {duration} >  기간 {args.period} · {args.day_type}"
          f" · γ={CLUSTER_GAMMA} 고정 · 스냅샷 '{args.run_label or '최신'}'")
    print("=" * 96)

    rows = []
    for z in args.z_grid:
        for seed in args.seeds:
            candidates = bc.build_candidates(net, st_info, duration, z, warmup,
                                             args.warmup_days, step1)
            if candidates.empty:
                print(f"  [건너뜀] z={z}: 재배치 대상이 없습니다")
                continue

            _clustered, routes = bc.plan_with_clusters(
                candidates.copy(), step1, solver, adjust=True, seed=seed)

            delta = bc.executed_delta(routes)
            before, after = bc.stockout(net, candidates, delta, duration)
            stats = bc.route_stats(routes)

            rows.append({
                "duration": duration, "z": z, "seed": seed,
                "period": args.period, "day_type": args.day_type,
                "run_label": args.run_label or "(최신)",
                "stations": int(len(candidates)),
                "stockout_before": before, "stockout_after": after,
                **stats,
            })
            print(f"  z={z:<5} 씨앗{seed:<4} 대상 {len(candidates):>3}곳"
                  f"  처리 {stats['bikes']:>4d}대"
                  f"  결품 {before:.3f}h → {after:.3f}h"
                  f"  최장 {stats['max_min']:>5.1f}분  초과 {stats['over']}")
    return rows


def show(frame):
    """회차별 격자 표. **판정은 결품h(작업 후)로 한다.**"""
    for duration, part in frame.groupby("duration", sort=False):
        # 씨앗이 여럿이면 평균으로 접는다 — 씨앗 변동은 z의 효과가 아니다.
        grid = part.groupby("z").agg(
            대상=("stations", "mean"), 처리대수=("bikes", "mean"),
            이동km=("km", "mean"), 최장분=("max_min", "mean"),
            초과=("over", "mean"), 결품h=("stockout_after", "mean"),
        ).round(3)

        base = grid["결품h"].get(TARGET_Z)
        print("\n" + "-" * 96)
        print(f"[{duration}]  z 격자 — 판정 기준은 결품 시간 (대여소·일 평균, 낮을수록 좋다)")
        print("-" * 96)
        print(f"{'z':>6}{'대상':>7}{'처리대수':>10}{'이동km':>10}"
              f"{'최장분':>9}{'초과':>7}{'결품h':>9}{'현행대비':>10}")
        for z, row in grid.iterrows():
            mark = "  ← 현행" if z == TARGET_Z else ""
            diff = ""
            if base is not None and z != TARGET_Z:
                gap = row["결품h"] - base
                diff = f"{gap:+.3f}h"
            print(f"{z:>6}{row['대상']:>7.0f}{row['처리대수']:>10.0f}"
                  f"{row['이동km']:>10.1f}{row['최장분']:>9.1f}"
                  f"{row['초과']:>7.1f}{row['결품h']:>9.3f}{diff:>10}{mark}")

    print("\n" + "=" * 96)
    print("읽는 법")
    print("  · 결품h = 재배치 후 대여소·일 평균 결품 시간. **낮을수록 좋다.**")
    print("    개선률·목표 도달률은 target_qty가 분모라 z 비교에 쓸 수 없다(4장).")
    print(f"  · 현행대비 = 현행 z={TARGET_Z} 대비 결품 시간 증감. +면 그 z가 더 나쁘다.")
    print(f"  · 초과 = 시간 예산({TIME_BUDGET_MINUTES}분)을 넘긴 차량 수."
          " z를 올리면 작업량이 늘어 여기가 먼저 무너진다.")
    print("  · 시간대(duration)가 다르면 수요 구조가 반대다 — 섞어서 평균 내지 마라.")
    print("  · **한 회차만 보고 판단하지 마라.** 3회차가 같은 방향일 때만 결론이다.")
    print("=" * 96)


def verdict(frame):
    """회차별 최적 z가 일치하는지 본다 — 갈리면 그것 자체가 결과다."""
    print("\n[회차별 결품 시간이 가장 낮은 z]")
    best = {}
    for duration, part in frame.groupby("duration", sort=False):
        grid = part.groupby("z")["stockout_after"].mean()
        z_best = grid.idxmin()
        best[duration] = z_best
        gap = grid.min() - grid.get(TARGET_Z, float("nan"))
        print(f"  {duration}: z={z_best}  (현행 {TARGET_Z} 대비 {gap:+.3f}h)")

    picks = set(best.values())
    print()
    if len(best) < 3:
        print(f"  ⚠ 회차가 {len(best)}개뿐이다 — 3회차를 다 재기 전에는 결론이 아니다"
              " (--duration 기본값으로 다시 돌려라).")
        print()
    if picks == {TARGET_Z}:
        print(f"  → 세 회차 모두 현행 z={TARGET_Z}가 최적이다."
              " 채택 근거가 판정 규칙 위에서 완결된다.")
    elif len(picks) == 1:
        only = picks.pop()
        print(f"  → 세 회차 모두 z={only}를 가리킨다. 현행 {TARGET_Z}와 다르므로"
              " 재검토가 필요하다.")
    else:
        print("  → **회차마다 최적 z가 다르다.** 하나로 못 정한다는 것이 결과다 —"
              " 회차별 z를 둘지, 평균이 가장 나은 z를 고를지는 별도 판단이다.")


def main():
    parser = argparse.ArgumentParser(
        description="z 격자를 결품 시간으로 재는 실험 (TODO P4-4)")
    parser.add_argument("--period", default=DEFAULT_PERIOD, help='순수요 기간 (예: "25년 11월")')
    parser.add_argument("--duration", default="_05_10,_10_15,_15_20", help="시간대 (콤마 구분)")
    parser.add_argument("--day-type", default="weekday", choices=["weekday", "holiday"])
    parser.add_argument("--run-label", default="",
                        help="재고 스냅샷을 고정할 실행 라벨 (기본: 최신)")
    parser.add_argument("--warmup-period", default="", help="계절 보정에 쓸 기간")
    parser.add_argument("--warmup-days", type=int, default=DEFAULT_WARMUP_DAYS)
    parser.add_argument("--z-grid", default=",".join(str(z) for z in Z_GRID),
                        help="잴 z 값들 (콤마 구분)")
    parser.add_argument("--seeds", default="42",
                        help="K-Medoids 씨앗 (콤마 구분). 여럿이면 평균낸다")
    parser.add_argument("--out", default="", help="결과를 CSV로 저장할 경로")
    args, _ = parser.parse_known_args()

    args.day_type = normalize_day_type(args.day_type)
    args.z_grid = [float(z) for z in args.z_grid.split(",") if z.strip()]
    args.seeds = [int(s) for s in args.seeds.split(",") if s.strip()]

    bc = load_baseline()
    step1 = bc.load_step1()
    solver = bc.ilp_mod.build_solver()          # 파이프라인과 같은 솔버 설정
    net, st_info, warmup = bc.load_inputs(
        args.period, args.run_label, args.day_type,
        args.warmup_days, args.warmup_period)

    rows = []
    for duration in [d.strip() for d in args.duration.split(",") if d.strip()]:
        rows.extend(run_grid(bc, net, st_info, warmup, duration,
                             args, step1, solver))

    if not rows:
        raise SystemExit("잰 결과가 없습니다.")

    frame = pd.DataFrame(rows)
    show(frame)
    verdict(frame)

    if args.out:
        frame.to_csv(args.out, index=False, encoding="utf-8")
        print(f"\n결과를 저장했습니다: {args.out}")


if __name__ == "__main__":
    main()
