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


# 좁은 격자 — 11·18장이 쓴 값. 인자 없이 돌리면 이쪽이다(과거 결과 재현용).
Z_GRID = [1.65, 1.80, 1.99, 2.10, 2.33]

# 넓은 격자 — `--z-grid wide` (TODO 대기-7, 1.26.76)
#
# 정규분포 커버리지를 사다리로 깐 16개 값이다. 왼쪽 끝 0.0은 **5장 B2 대조군과
# 같은 값**(평균 목표재고)이라, 파라미터 격자와 대조군 실험이 한 축에서 만난다.
#
# 🔴 **0점대·1.0점대를 넣은 이유** — 지금까지 결품 시간으로 잰 z는 1.65 이상뿐이다.
# 18장이 *"편익은 5~23초 안에서 흔들리는데 비용은 z에 단조롭게 는다 → 낮은 쪽이
# 안전하다"* 는 판단을 내렸는데, **그 '낮은 쪽'을 실제로 잰 적이 없다.** 격자의
# 왼쪽이 비어 있었기 때문이다. 0까지 내려가야 그 판단을 검증할 수 있고, z를 0으로
# 내렸을 때의 악화폭이 곧 **추정 오차의 크기**가 된다(18장: z가 덮는 것은 분포의
# 꼬리가 아니라 추정 오차다).
#
# ⚠️ **1.96과 1.99는 둘 다 넣되, 사전 기대는 "차이 없음"이다.** 1.96은 통계 관행값
# (양측 95% 신뢰구간), 1.99는 이 프로젝트가 백테스트로 구한 값이다. 0.03 차이라
# 결품 시간으로는 못 가릴 것이고 **그것이 결론이다** — *"관행값과 실측값의 차이는
# 측정 한계 아래"*. 만약 씨앗 잡음을 넘는 차이가 나오면 **자를 의심할 것.**
#
# ⚠️ 낮은 z에서는 후보가 비어 계획이 서지 않을 수 있다. 건너뛴 칸을 **결품 0으로
# 읽지 마라** — "대상이 없었다"와 "결품이 없었다"는 다른 말이다.
Z_GRID_WIDE = [0.0, 0.25, 0.52, 0.67, 0.84, 1.04, 1.28, 1.44,
               1.65, 1.80, 1.96, 1.99, 2.10, 2.33, 2.58, 2.81]


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
    parser.add_argument("--z-grid", default=",".join(str(z) for z in Z_GRID),
                        help="쉼표로 구분한 z 목록. 'wide'를 주면 Z_GRID_WIDE(16개)")
    parser.add_argument("--duration", default="_05_10,_10_15,_15_20")
    parser.add_argument("--day-type", default="weekday")
    parser.add_argument("--warmup-days", type=int, default=14)
    parser.add_argument("--seeds", default="42,7,13")
    parser.add_argument("--out", default="")
    args, _ = parser.parse_known_args()

    args.durations = [d.strip() for d in args.duration.split(",") if d.strip()]
    args.seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    if args.z_grid.strip().lower() == "wide":
        args.z_grid = list(Z_GRID_WIDE)
    else:
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
