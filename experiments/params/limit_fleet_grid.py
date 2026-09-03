"""실험 — 작업 대상 상한 × 회차당 차량 수의 **파레토 프론티어** (TODO P4-5).

🔴 **상한 축 비교에는 쓰지 마라 (1.26.64).** 이 스크립트는 결품을 잴 때 각 상한의
**자기 후보 집합**을 모집단으로 넘긴다(`bc.stockout(net, base, ...)`). `base`는
상한이 정하는 집합이라 **상한마다 크기가 다르고**(26~80곳), 결품은 그 집합 위의
*평균*이라 **분모가 흔들린다.** 실제로 재배치 **전** 값부터 1.568h(상한 15) 대
1.854h(상한 50)로 어긋난다 — 아무 것도 하지 않은 상태의 값이 상한에 따라 달라질
수는 없다.

    상한 축 → experiments/params/limit_fixedpop_grid.py 를 쓴다 (고정 모집단)
    차량 축 → 이 스크립트가 유효하다 (상한 고정이면 모집단이 흔들리지 않는다)

근거: docs/분석/EXPERIMENTS.md 17장.


1.21.8에서 `TOP_STATION_LIMIT=50`이 보유 차량 21대와 맞물린 값임을 확인했지만
**그 조합 하나만 쟀다.** 둘은 서로 맞물려 있다.

    상한 ↑ → 후보 ↑ → 작업량 ↑ → 차량이 모자라면 예산 초과 ↑
    차량 ↑ → 군집이 잘게 쪼개진다 → 군집당 일감 ↓ → 예산 초과 ↓ (대신 비용 ↑)

그래서 한 축만 흔들면 *"상한을 올렸더니 나빠졌다"*가 **상한 탓인지 차량이
모자란 탓인지** 가릴 수 없다. 이 스크립트는 둘을 **함께** 흔들어
"차량을 몇 대 늘리면 결품이 얼마나 주는가"를 곡선으로 낸다.

`top_limit_sweep.py`와 무엇이 다른가
------------------------------------
그쪽은 **계획 수준의 추정**이다(`estimate()` — 작업량으로 필요 차량을 나눠 본다).
군집·ILP·VRP를 실제로 돌리지 않으므로 **이동거리·최장 소요·예산 초과가 실측이
아니다.** 파레토 프론티어의 세로축이 비용인데 비용이 추정값이면 곡선을 믿을 수
없다. 이 스크립트는 **파이프라인을 실제로 돌린다**(z 격자와 같은 방식).

⚠️ 주의
-------
- **각 셀을 별도 프로세스로 돌린다.** `TOP_STATION_LIMIT`·`VEHICLES_PER_ROUND`가
  `project_config` 모듈 수준에서 환경변수를 읽으므로, 한 프로세스 안에서는
  값을 바꿔도 반영되지 않는다.
- **씨앗을 여럿 쓴다.** z 격자(11장)에서 **씨앗 하나로 재면 결론이 뒤집히는 것**을
  겪었다 — `_15_20`은 씨앗 잡음이 파라미터 효과와 같은 크기였다. 기본 3개.
- **3회차 전부**로 재확인한다. 회차마다 수요 구조가 반대다.
- `z`·`γ`는 현행값으로 고정한다. 함께 흔들면 어느 쪽 몫인지 가를 수 없다.
- **차량은 보유 21대가 상한이다.** 그 위는 재지 않는다 — 집행할 수 없는 계획의
  결품은 *"차가 더 있다면"* 의 값이라, 파레토 프론티어에 올리면 **고를 수 없는
  지점을 권하게 된다.** `--fleet`에 21을 넘겨도 걸러낸다.

사용법:
    python experiments/params/limit_fleet_grid.py
    python experiments/params/limit_fleet_grid.py --limits "30,50,70" --fleet "15,21"
    python experiments/params/limit_fleet_grid.py --seeds "42,7,13" --out grid.csv
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from project_config import (                    # noqa: E402
    CLUSTER_GAMMA, DEFAULT_PERIOD, TARGET_Z, TIME_BUDGET_MINUTES,
    TOP_STATION_LIMIT, VEHICLES_PER_ROUND, normalize_day_type,
)

# 셀 하나에 허용할 시간(초). **넘기면 그 셀만 버리고 넘어간다.**
#
# ⚠️ 2026-09-01 실측 — 격자를 돌리면 CBC가 **생성만 되고 시작하지 못한 채**
# 멈추는 일이 있다(`cbc.exe`가 CPU 0.0초·스레드 1·핸들 4로 12시간 대기).
# 같은 인스턴스를 손으로 돌리면 0.01초에 풀리므로 모델 문제가 아니다.
# 타임아웃이 없던 때는 **한 셀이 막히면 격자 전체가 섰고**, 그런데도 종료
# 코드는 0이라 완료로 착각하기 쉬웠다. 막힌 셀은 버리는 편이 낫다 —
# 평균이 거짓말을 하지 않도록 **결과에서 빼고 몇 개를 뺐는지 알린다.**
CELL_TIMEOUT_SEC = int(os.getenv("PBR_GRID_CELL_TIMEOUT", "900"))

LIMITS = [30, 40, 50, 70, 100]

# ⚠️ **보유 차량 21대가 상한이다** — 그보다 크게 잡지 마라.
# 21은 본 연구의 설계 선택이 아니라 **관제센터 유선 문의로 확인한 보유 대수**다
# (2025년 9월경, docs/구현/FLEET.md · docs/기록/ORIGINS.md 4장). 그래서
# **집행 가능성의 경계**다. 21을 넘는 조합의 결품은 "차가 더 있다면"의 값이라
# 파레토 프론티어에 올리면 고를 수 없는 지점을 권하게 된다.
FLEET = [12, 15, 18, 21]
MAX_FLEET = 21


# ------------------------------------------------------------------ 자식 프로세스

WORKER = r'''
import io, json, sys, contextlib
from pathlib import Path
ROOT = Path(sys.argv[1])
sys.path.insert(0, str(ROOT))
import importlib.util
path = ROOT / "experiments" / "baseline" / "baseline_compare.py"
spec = importlib.util.spec_from_file_location("baseline_compare", path)
bc = importlib.util.module_from_spec(spec)
sys.modules["baseline_compare"] = bc
buf = io.StringIO()
with contextlib.redirect_stdout(buf):          # 파이프라인 수다를 삼킨다
    spec.loader.exec_module(bc)
    args = json.loads(sys.argv[2])
    step1 = bc.load_step1()
    solver = bc.ilp_mod.build_solver()
    net, st_info, warmup = bc.load_inputs(
        args["period"], args["run_label"], args["day_type"], args["warmup_days"], "")
    out = []
    for duration in args["durations"]:
        base = bc.build_candidates(net, st_info, duration, None, warmup,
                                   args["warmup_days"], step1)
        if base.empty:
            continue
        for seed in args["seeds"]:
            _c, routes = bc.plan_with_clusters(base.copy(), step1, solver,
                                               adjust=True, seed=seed)
            delta = bc.executed_delta(routes)
            before, after = bc.stockout(net, base, delta, duration)
            stats = bc.route_stats(routes)
            out.append({"duration": duration, "seed": seed,
                        "stations": int(len(base)),
                        "stockout_before": before, "stockout_after": after,
                        **stats})
print(json.dumps(out))
'''


def run_cell(limit: int, fleet: int, args) -> list:
    """상한·차량 수를 환경변수로 박고 자식 프로세스에서 파이프라인을 돌린다."""
    env = dict(os.environ)
    env["PBR_TOP_STATION_LIMIT"] = str(limit)
    env["PBR_VEHICLES_PER_ROUND"] = str(fleet)
    env["PBR_FLEET_SIZE"] = str(max(fleet, VEHICLES_PER_ROUND))
    env["PYTHONIOENCODING"] = "utf-8"

    payload = json.dumps({
        "period": args.period, "run_label": args.run_label,
        "day_type": args.day_type, "warmup_days": args.warmup_days,
        "durations": args.durations, "seeds": args.seeds,
    })
    try:
        proc = subprocess.run([sys.executable, "-c", WORKER, str(ROOT), payload],
                              capture_output=True, text=True, encoding="utf-8",
                              env=env, timeout=CELL_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        # 멈춘 자식(과 그 CBC)은 여기서 이미 죽는다. 격자는 계속 간다.
        print(f"  [시간초과] 상한 {limit} · 차량 {fleet}:"
              f" {CELL_TIMEOUT_SEC}초를 넘겨 이 셀을 건너뛴다"
              f" (PBR_GRID_CELL_TIMEOUT으로 조정)")
        return []
    if proc.returncode != 0:
        print(f"  [실패] 상한 {limit} · 차량 {fleet}: {proc.stderr.strip()[-300:]}")
        return []
    try:
        rows = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        print(f"  [실패] 상한 {limit} · 차량 {fleet}: 결과를 읽지 못했다")
        return []
    for row in rows:
        row["limit"] = limit
        row["fleet"] = fleet
    return rows


# ------------------------------------------------------------------ 표

def show(frame):
    """회차별 격자. 세로 = 상한, 가로 = 차량 수."""
    for duration, part in frame.groupby("duration", sort=False):
        grid = part.groupby(["limit", "fleet"]).agg(
            결품h=("stockout_after", "mean"), km=("km", "mean"),
            초과=("over", "mean"), 차량=("vehicles", "mean"),
        ).reset_index()

        print("\n" + "=" * 100)
        print(f"[{duration}]  결품 시간(h) — 낮을수록 좋다  ·  괄호는 (이동km / 예산초과)")
        print("=" * 100)
        fleets = sorted(grid["fleet"].unique())
        print(f"{'상한':>6}" + "".join(f"{f'차량{f}':>18}" for f in fleets))
        for limit in sorted(grid["limit"].unique()):
            line = f"{limit:>6}"
            for fleet in fleets:
                cell = grid[(grid["limit"] == limit) & (grid["fleet"] == fleet)]
                if cell.empty:
                    line += f"{'-':>18}"
                    continue
                r = cell.iloc[0]
                line += f"{r['결품h']:>8.3f}({r['km']:>4.0f}/{r['초과']:>3.1f})"
            print(line)


def pareto(frame):
    """비용(이동거리) 대 편익(결품 시간)의 파레토 프론티어.

    **한 회차만 보고 판단하지 않는다** — 세 회차 평균으로 낸다. 다만 회차마다
    수요 구조가 반대이므로, 평균이 가리는 것이 없는지 위 격자 표를 함께 본다.
    """
    avg = (frame.groupby(["limit", "fleet"])
           .agg(결품h=("stockout_after", "mean"), km=("km", "mean"),
                초과=("over", "mean"), 차량=("vehicles", "mean"))
           .reset_index().sort_values("km"))

    front, best = [], float("inf")
    for row in avg.itertuples():                # 거리 오름차순 → 결품이 갱신될 때만
        if row.결품h < best:
            best = row.결품h
            front.append(row)

    print("\n" + "=" * 100)
    print("파레토 프론티어 — 이동거리를 늘려 결품을 사는 지점들 (3회차 평균)")
    print("=" * 100)
    print(f"{'상한':>6}{'차량':>7}{'이동km':>10}{'결품h':>10}{'예산초과':>10}{'실차량':>9}{'한계이득':>12}")
    prev = None
    for row in front:
        gain = "" if prev is None else f"{(prev.결품h - row.결품h) / max(row.km - prev.km, 1e-9):+.5f}h/km"
        mark = "  ← 현행" if (row.limit == TOP_STATION_LIMIT
                            and row.fleet == VEHICLES_PER_ROUND) else ""
        print(f"{row.limit:>6}{row.fleet:>7}{row.km:>10.1f}{row.결품h:>10.3f}"
              f"{row.초과:>10.1f}{row.차량:>9.1f}{gain:>12}{mark}")
        prev = row

    cur = avg[(avg["limit"] == TOP_STATION_LIMIT) & (avg["fleet"] == VEHICLES_PER_ROUND)]
    if not cur.empty:
        c = cur.iloc[0]
        on = any(r.limit == TOP_STATION_LIMIT and r.fleet == VEHICLES_PER_ROUND
                 for r in front)
        print(f"\n현행(상한 {TOP_STATION_LIMIT} · 차량 {VEHICLES_PER_ROUND}): "
              f"이동 {c['km']:.1f}km · 결품 {c['결품h']:.3f}h"
              f" — 프론티어 {'위에 있다' if on else '**밖이다**'}")
        if not on:
            better = avg[(avg["km"] <= c["km"]) & (avg["결품h"] < c["결품h"])]
            if not better.empty:
                b = better.sort_values("결품h").iloc[0]
                print(f"  → 상한 {int(b['limit'])} · 차량 {int(b['fleet'])}이 "
                      f"**더 적은 이동({b['km']:.1f}km)으로 더 낮은 결품"
                      f"({b['결품h']:.3f}h)**을 낸다.")

    print("\n읽는 법")
    print("  · 한계이득 = 이동 1km를 더 써서 줄인 결품 시간. 클수록 값싼 개선이다.")
    print(f"  · 예산초과 = 시간 예산({TIME_BUDGET_MINUTES}분)을 넘긴 차량 수(평균).")
    print("  · 실차량 = 실제로 쓰인 군집 수. 상한 차량 수보다 작으면 작업량이 정한 것이다.")
    print("  · **회차마다 수요 구조가 반대다** — 위 회차별 격자를 반드시 함께 본다.")
    print("=" * 100)


def main():
    parser = argparse.ArgumentParser(
        description="상한 × 차량 수 격자 (TODO P4-5)")
    parser.add_argument("--period", default=DEFAULT_PERIOD)
    parser.add_argument("--duration", default="_05_10,_10_15,_15_20")
    parser.add_argument("--day-type", default="weekday", choices=["weekday", "holiday"])
    parser.add_argument("--run-label", default="",
                        help="재고 스냅샷을 고정할 실행 라벨 (기본: 최신)")
    parser.add_argument("--warmup-days", type=int, default=14)
    parser.add_argument("--limits", default=",".join(str(x) for x in LIMITS))
    parser.add_argument("--fleet", default=",".join(str(x) for x in FLEET))
    parser.add_argument("--seeds", default="42,7,13")
    parser.add_argument("--out", default="")
    args, _ = parser.parse_known_args()

    args.day_type = normalize_day_type(args.day_type)
    args.durations = [d.strip() for d in args.duration.split(",") if d.strip()]
    args.seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    limits = [int(x) for x in args.limits.split(",") if x.strip()]
    fleet = [int(x) for x in args.fleet.split(",") if x.strip()]

    # 보유 대수를 넘는 조합은 아예 재지 않는다 — 고를 수 없는 지점이기 때문이다.
    if over := [f for f in fleet if f > MAX_FLEET]:
        print(f"[안내] 보유 차량 {MAX_FLEET}대를 넘는 {over}는 제외합니다"
              f" (docs/구현/FLEET.md).")
        fleet = [f for f in fleet if f <= MAX_FLEET]
    if not fleet:
        raise SystemExit(f"잴 차량 수가 없습니다 (보유 상한 {MAX_FLEET}대).")

    print(f"상한 {limits} × 차량 {fleet} × 회차 {len(args.durations)}"
          f" × 씨앗 {len(args.seeds)}"
          f"  (z={TARGET_Z}·γ={CLUSTER_GAMMA} 고정,"
          f" 스냅샷 '{args.run_label or '최신'}')")
    print(f"셀 {len(limits) * len(fleet)}개를 각각 별도 프로세스로 돌린다.\n")

    rows = []
    for limit in limits:
        for f in fleet:
            got = run_cell(limit, f, args)
            rows.extend(got)
            if got:
                mean = sum(r["stockout_after"] for r in got) / len(got)
                print(f"  상한 {limit:>3} · 차량 {f:>2}  →  결품 {mean:.3f}h"
                      f"  ({len(got)}건)")

    if not rows:
        raise SystemExit("잰 결과가 없습니다.")

    frame = pd.DataFrame(rows)
    show(frame)
    pareto(frame)

    if args.out:
        frame.to_csv(args.out, index=False, encoding="utf-8")
        print(f"\n결과를 저장했습니다: {args.out}")


if __name__ == "__main__":
    main()
