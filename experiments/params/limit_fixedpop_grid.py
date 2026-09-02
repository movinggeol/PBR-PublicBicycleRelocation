"""실험 — 후보 상한을 **같은 모집단**으로 다시 잰다 (TODO 대기-1, 1.26.64).

## 왜 다시 재는가 — 12장 표는 자가 흔들렸다

`limit_fleet_grid.py`는 결품을 잴 때 **각 상한의 자기 후보 집합**을 모집단으로
넘겼다(`bc.stockout(net, base, ...)`). 그런데 `base`는 상한이 정하는 집합이라
**상한마다 크기가 다르다** — 실측 26곳(상한 15) ~ 80곳(상한 50).

결품 시간은 그 집합 위의 **평균**이므로(`before.sum() / (대여소수 × 일수)`)
집합이 다르면 **분모가 다른 자로 잰 값**이 된다. 실제로 *재배치 전* 값부터
어긋나 있었다:

    상한 15 → 재배치 전 1.568h     상한 50 → 재배치 전 1.854h

작업량이 큰 곳부터 담으므로 **상한이 작을수록 결품이 심한 곳만 남고**, 그
집합에서 잰 평균은 상한이 클 때와 비교할 수 없다.

⚠️ **이 결함은 1.26.56에서 이미 한 번 겪은 것이다.** 그때는 B2(z=0)만 후보가
달라 혼자 다른 모집단에서 평균을 냈고, 논문 6.3의 *"B2가 무재배치보다 나쁘다"*
는 서술이 거기서 나왔다. `bc.stockout()` docstring이 그 사고를 적어 두고
**"population은 방법마다 달라지면 안 된다"** 고 경고하는데, 상한 격자가 같은
함수를 같은 방식으로 잘못 불렀다.

## 어떻게 고치나

**계획은 상한별로 세우되, 평가는 하나의 고정 모집단에서 한다.**

    상한 15로 계획 ┐
    상한 30으로 계획 ├→ 각 delta를 **상한 50의 후보 집합** 위에서 평가
    상한 50으로 계획 ┘   (가장 넓은 집합 = 어느 상한의 대상도 포함한다)

손대지 않은 대여소는 `delta=0`으로 들어가므로 **"후보에서 뺐다"는 선택의 대가가
결품에 그대로 잡힌다.** 이것이 원래 재려던 것이다.

## 결과 (재현: 아래 명령)

두 달 모두 **상한이 클수록 결품이 낮다** — 12장의 결론과 **방향이 반대**다.
25년 11월은 9/9 회차·씨앗에서, 26년 03월은 8/9에서 그렇다.

사용법:
    python experiments/params/limit_fixedpop_grid.py \
        --period "25년 11월" --run-label "2026-08-11 real" --limits "15,20,25,30,40,50"
    python experiments/params/limit_fixedpop_grid.py \
        --period "26년 03월" --run-label "sweep-10" --limits "30,40,50,70"
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

WORKER = Path(__file__).with_name("_limit_plan_worker.py")

# 셀 하나가 이 시간을 넘기면 버리고 다음으로 간다 (TODO 대기-12 ①).
# 이 환경에서 자식 프로세스가 **생성만 되고 시작하지 못하는** 일이 있는데,
# 타임아웃이 없으면 격자 전체가 그 자리에 선다.
CELL_TIMEOUT_SEC = int(os.getenv("PBR_GRID_CELL_TIMEOUT", "900"))


def collect_plans(limits, args) -> list:
    """상한마다 자식 프로세스를 띄워 계획(delta)을 모은다."""
    rows = []
    for limit in limits:
        env = dict(os.environ)
        env["PBR_TOP_STATION_LIMIT"] = str(limit)
        env["PBR_VEHICLES_PER_ROUND"] = str(args.fleet)
        env["PBR_FLEET_SIZE"] = str(max(args.fleet, 21))
        env["PBR_EXP_PERIOD"] = args.period
        env["PBR_EXP_RUN_LABEL"] = args.run_label
        env["PBR_EXP_DURATIONS"] = ",".join(args.durations)
        env["PBR_EXP_SEEDS"] = ",".join(str(s) for s in args.seeds)
        env["PYTHONIOENCODING"] = "utf-8"

        try:
            proc = subprocess.run([sys.executable, str(WORKER)],
                                  capture_output=True, text=True,
                                  env=env, encoding="utf-8",
                                  timeout=CELL_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            # 이 환경에서 자식이 '생성만 되고 시작하지 못하는' 일이 있다
            # (TODO 대기-12 ①). 멈춘 자식은 여기서 죽고 격자는 계속 간다.
            print(f"  [시간초과] 상한 {limit}: {CELL_TIMEOUT_SEC}초를 넘겨 건너뛴다"
                  f" (PBR_GRID_CELL_TIMEOUT으로 조정)")
            continue
        if proc.returncode != 0:
            raise SystemExit(f"상한 {limit} 실패:\n{proc.stderr[-2000:]}")
        rows.extend(json.loads(proc.stdout.strip().splitlines()[-1]))
        print(f"  상한 {limit:>3} 계획 수집 완료", flush=True)
    return rows


def evaluate(rows, limits, args) -> pd.DataFrame:
    """모든 계획을 **가장 넓은 상한의 후보 집합** 위에서 다시 평가한다."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        os.environ["PBR_TOP_STATION_LIMIT"] = str(max(limits))
        spec = importlib.util.spec_from_file_location(
            "baseline_compare",
            ROOT / "experiments" / "baseline" / "baseline_compare.py")
        bc = importlib.util.module_from_spec(spec)
        sys.modules["baseline_compare"] = bc
        spec.loader.exec_module(bc)

        step1 = bc.load_step1()
        net, st_info, warmup = bc.load_inputs(
            args.period, args.run_label, "weekday", 14, "")
        pops = {d: bc.build_candidates(net, st_info, d, None, warmup, 14, step1)
                for d in args.durations}

        out = []
        for r in rows:
            pop = pops.get(r["duration"])
            if pop is None or pop.empty:
                continue
            delta = json.loads(r["delta_json"])
            before, after = bc.stockout(net, pop, delta, r["duration"])
            out.append({k: r[k] for k in ("duration", "seed", "limit", "stations",
                                          "km", "over", "vehicles", "bikes")}
                       | {"before_fixed": before, "after_fixed": after,
                          "population": int(len(pop))})
    return pd.DataFrame(out)


def report(df, limits) -> None:
    print("\n" + "=" * 92)
    print(f"고정 모집단(상한 {max(limits)}의 후보 {df.population.iloc[0]}곳)에서 다시 잰 결품 시간(h)")
    print("=" * 92)
    piv = df.pivot_table(index="limit", columns="duration", values="after_fixed")
    piv["3회차평균"] = piv.mean(axis=1)
    print(piv.round(4).to_string())

    base = df.before_fixed.mean()
    print(f"\n재배치 전 = {base:.4f}h — **모든 상한에서 같다**(고정 모집단이므로).")

    lo, hi = min(limits), max(limits)
    a = df[df.limit == hi].set_index(["duration", "seed"]).after_fixed
    b = df[df.limit == lo].set_index(["duration", "seed"]).after_fixed
    print(f"상한 {hi}가 {lo}을 이긴 건수: **{int((a < b).sum())}/{len(a)}**")

    bad = sum(not g.sort_values("limit").after_fixed.is_monotonic_decreasing
              for _, g in df.groupby(["duration", "seed"]))
    print(f"단조성(상한↑ → 결품↓) 위반: {bad}/{df.groupby(['duration', 'seed']).ngroups}건")

    for dur, g in df.groupby("duration"):
        m = g.groupby("limit").after_fixed.mean()
        n = g.groupby("limit").after_fixed.std().max()
        if n and n > 0:
            print(f"  {dur}: 신호/잡음 = {(m.max() - m.min()) / n:.1f}")
    print("\n⚠️ 비용(이동거리·예산초과)은 상한이 커질수록 는다 — 결품만 보고 정하지 말 것.")
    cost = df.groupby("limit")[["km", "over", "vehicles"]].mean()
    print(cost.round(2).to_string())
    print("=" * 92)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="후보 상한을 같은 모집단으로 다시 잰다 (TODO 대기-1)")
    parser.add_argument("--period", default="25년 11월")
    parser.add_argument("--run-label", default="2026-08-11 real")
    parser.add_argument("--limits", default="15,20,25,30,40,50")
    parser.add_argument("--fleet", type=int, default=12)
    parser.add_argument("--duration", default="_05_10,_10_15,_15_20")
    parser.add_argument("--seeds", default="42,7,13")
    parser.add_argument("--out", default="")
    args, _ = parser.parse_known_args()

    args.durations = [d.strip() for d in args.duration.split(",") if d.strip()]
    args.seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    limits = [int(x) for x in args.limits.split(",") if x.strip()]

    print(f"상한 {limits} · 차량 {args.fleet} · 회차 {len(args.durations)}"
          f" · 씨앗 {len(args.seeds)}  (스냅샷 '{args.run_label}', {args.period})")
    rows = collect_plans(limits, args)
    df = evaluate(rows, limits, args)

    # 🔴 **잰 것이 없으면 성공이 아니다** (TODO 대기-12). 종료 코드 0으로 끝내면
    #    호출한 쪽이 완료로 착각한다 — 18셀 격자가 EXIT=0인데 CSV가 없던 일이 있다.
    if df.empty:
        print("\n[실패] 잰 것이 하나도 없습니다 —"
              " 모든 셀이 시간초과이거나 후보가 비었습니다.")
        return 1

    report(df, limits)

    if args.out:
        out = Path(args.out)
        if not out.is_absolute():
            out = ROOT / out
        df.to_csv(out, index=False)
        print(f"\n결과를 저장했습니다: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
