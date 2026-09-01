"""실험 — `z` 격자를 **모집단을 바꿔 가며** 다시 잰다 (TODO 대기-10, 1.26.65).

## 왜 다시 재는가

11장(`z_stockout_grid.py`)은 결품을 잴 때 **각 z의 자기 후보 집합**을 모집단으로
넘겼다. 그런데 `z`는 `target_qty`를 바꾸므로 **후보 집합도 z마다 달라진다.**
결품은 그 집합 위의 *평균*이라(`합계 / (대여소수 × 일수)`) 분모가 흔들린다 —
실제로 재배치 **전** 값이 z에 따라 2.0275 → 1.7798로 움직였다. 아무 것도 하지
않은 상태의 값이 z에 따라 달라질 수는 없다.

상한 격자에서 **같은 결함이 결론을 뒤집었다**(17장). 그래서 z도 다시 잰다.

## 왜 모집단을 셋으로 잼

1.26.64에서 z=2.33의 후보 집합을 모집단으로 잡아 재자 **z=2.33이 이겼다.**
그런데 **승자가 모집단을 정한 z와 같다** — 모집단 선택이 유리하게 작용했을
여지가 있다. 그래서 세 가지로 잰다:

    좁은 모집단  = 가장 작은 z의 후보 집합   (z가 작으면 대상도 적다)
    넓은 모집단  = 가장 큰 z의 후보 집합
    전체 대여소  = station_info 전부         ← **가장 중립적이다**

**셋이 같은 승자를 가리키면 모집단 선택과 무관하다.** 갈리면 그 자체가 결론이다
— *"z는 무엇을 모집단으로 보느냐에 달렸다"* 가 되고, 기본값을 바꿀 근거가 없다.

⚠️ **전체 대여소는 값이 작게 나온다.** 손대지 않는 대여소가 분모에 다 들어가
평균이 희석되기 때문이다. **절대값이 아니라 z 사이의 순서만** 본다.

사용법:
    python experiments/params/z_fixedpop_grid.py
    python experiments/params/z_fixedpop_grid.py --period "26년 03월" --run-label "sweep-10"
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def load_baseline():
    path = ROOT / "experiments" / "baseline" / "baseline_compare.py"
    spec = importlib.util.spec_from_file_location("baseline_compare", path)
    bc = importlib.util.module_from_spec(spec)
    sys.modules["baseline_compare"] = bc
    spec.loader.exec_module(bc)
    return bc


def measure(bc, args) -> pd.DataFrame:
    step1 = bc.load_step1()
    solver = bc.ilp_mod.build_solver()
    net, st_info, warmup = bc.load_inputs(
        args.period, args.run_label, args.day_type, args.warmup_days, "")
    print(f"[스냅샷] station_info run_label = '{args.run_label or '최신'}'"
          f" ({len(st_info)}곳)", flush=True)

    zs = args.z_grid
    rows = []
    for duration in args.durations:
        cands = {}
        for z in zs:
            c = bc.build_candidates(net, st_info, duration, z, warmup,
                                    args.warmup_days, step1)
            if not c.empty:
                cands[z] = c
        if not cands:
            continue

        # 세 모집단 — 좁은 쪽/넓은 쪽/전체
        pops = {"좁음(z=%s)" % min(cands): cands[min(cands)],
                "넓음(z=%s)" % max(cands): cands[max(cands)],
                "전체대여소": st_info}

        for z, cand in cands.items():
            for seed in args.seeds:
                _c, routes = bc.plan_with_clusters(
                    cand.copy(), step1, solver, adjust=True, seed=seed)
                delta = bc.executed_delta(routes)
                stats = bc.route_stats(routes)
                row = {"duration": duration, "z": z, "seed": seed,
                       "candidates": int(len(cand)),
                       "km": stats.get("km"), "over": stats.get("over"),
                       "bikes": stats.get("bikes")}
                for name, pop in pops.items():
                    before, after = bc.stockout(net, pop, delta, duration)
                    row[f"before::{name}"] = before
                    row[f"after::{name}"] = after
                rows.append(row)
        print(f"  {duration} 완료", flush=True)
    return pd.DataFrame(rows)


def report(df) -> None:
    pops = [c.split("::", 1)[1] for c in df.columns if c.startswith("after::")]
    print("\n" + "=" * 92)
    print("모집단을 바꿔 가며 잰 결품 시간(h) — 3회차 평균, 낮을수록 좋다")
    print("=" * 92)

    table = pd.DataFrame({p: df.groupby("z")[f"after::{p}"].mean() for p in pops})
    print(table.round(4).to_string())

    print("\n재배치 전 (모집단마다 다르지만 z에는 무관해야 한다):")
    for p in pops:
        b = df.groupby("z")[f"before::{p}"].mean()
        flat = "✅ z에 무관" if (b.max() - b.min()) < 1e-9 else f"⚠️ {b.min():.4f}~{b.max():.4f}"
        print(f"  {p:16s} {b.mean():.4f}h  {flat}")

    print("\n모집단별 최저 z:")
    winners = {}
    for p in pops:
        m = table[p].sort_values()
        winners[p] = m.index[0]
        print(f"  {p:16s} → z={m.index[0]}  ({m.iloc[0]:.4f}),"
              f" 차순 z={m.index[1]} ({m.iloc[1]:.4f})")

    uniq = set(winners.values())
    print()
    if len(uniq) == 1:
        w = uniq.pop()
        print(f"✅ **세 모집단 모두 z={w}을 가리킨다** — 모집단 선택과 무관하다.")
    else:
        print(f"🔴 **모집단마다 승자가 다르다** ({winners}) — "
              "z는 무엇을 모집단으로 보느냐에 달렸다. **기본값을 바꿀 근거가 없다.**")

    print("\n⚠️ 비용 — z를 올리면 작업량이 는다:")
    print(df.groupby("z")[["candidates", "bikes", "km", "over"]].mean().round(2).to_string())
    print("=" * 92)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="z 격자를 모집단을 바꿔 가며 잰다 (TODO 대기-10)")
    parser.add_argument("--period", default="25년 11월")
    parser.add_argument("--run-label", default="2026-08-11 real")
    parser.add_argument("--z-grid", default="1.65,1.80,1.99,2.10,2.33")
    parser.add_argument("--duration", default="_05_10,_10_15,_15_20")
    parser.add_argument("--day-type", default="weekday")
    parser.add_argument("--warmup-days", type=int, default=14)
    parser.add_argument("--seeds", default="42,7,13")
    parser.add_argument("--out", default="")
    args, _ = parser.parse_known_args()

    args.durations = [d.strip() for d in args.duration.split(",") if d.strip()]
    args.seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    args.z_grid = [float(z) for z in args.z_grid.split(",") if z.strip()]

    print(f"z {args.z_grid} · 회차 {len(args.durations)} · 씨앗 {len(args.seeds)}"
          f"  ({args.period})")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        bc = load_baseline()
    df = measure(bc, args)
    if df.empty:
        raise SystemExit("잰 것이 없습니다.")
    report(df)

    if args.out:
        out = Path(args.out)
        if not out.is_absolute():
            out = ROOT / out
        df.to_csv(out, index=False)
        print(f"\n결과를 저장했습니다: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
