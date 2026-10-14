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

## 🔴 숨은 조건 둘 (2026-10-13, 1.26.363에서 고쳤다)

**① 회차당 차량 대수.** `--fleet`의 기본값이 12였다 — 12장의 차량 축에서 가장 작은
값을 물려받은 것인데, 운영 기본값은 21이고(`project_config.VEHICLES_PER_ROUND`)
군집 수 식은 상한 50에서 14~18을 낸다. 그래서 인자 없이 돌리면 **차량이 12대로
눌린 계획**을 쟀고, 원고 <표 5-9>가 그 조건을 적지 않은 채 실렸다(상한 50의 초과가
7.78대, 상한 100은 *"12.0대가 모두"*). 지금 기본값은 운영값을 따른다. **위 '결과'와
EXPERIMENTS 17장의 옛 표는 12대에서 잰 것이라, 다시 내려면 `--fleet 12`를 준다.**

**② 고정 모집단이 `--limits`의 최댓값을 따라가지 않았다.** `evaluate()`가
`PBR_TOP_STATION_LIMIT`을 환경변수로 넣었지만, 머리에 `import project_config`가
들어간 뒤로는(1.26.194) 값이 이미 굳어 있어 **50보다 큰 상한을 줘도 모집단은
상한 50의 후보**였다. 지금은 독립 인스턴스인 step1의 전역을 갈아 끼우고,
**자식이 그 상한으로 고른 후보 수와 맞는지 확인한 뒤에만** 잰다
(`check_population`). 최댓값이 50인 격자는 처음부터 영향이 없었다.

사용법:
    python experiments/params/limit_fixedpop_grid.py \
        --period "25년 11월" --run-label "2026-08-11 real" --limits "15,20,25,30,40,50,100"
    python experiments/params/limit_fixedpop_grid.py \
        --period "26년 03월" --run-label "sweep-10" --limits "30,40,50,70" --fleet 12
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# 출력 인코딩 가드를 깨운다 — 계획은 자식 프로세스(_limit_plan_worker.py)가 세우고
# 이 프로세스는 요약 단계에서야 project_config를 거쳐, 그 앞의 진행 문구가 cp949로
# 나갔다(2026-09-14 예행 로그 한 파일에 인코딩이 둘). 거기에 '—' 하나만 들어가도
# 죽는다 — 출력 순서에 기대지 않고 처음부터 거친다.
import project_config  # noqa: E402
from experiments._shared import CANONICAL_RUN_LABEL, load_baseline  # noqa: E402

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


def check_population(rows, pops, limits) -> None:
    """부모가 만든 모집단이 **가장 넓은 상한의 후보**가 맞는지 자식의 값으로 확인한다.

    자식은 상한마다 새 프로세스라 그 상한이 실제로 걸린다. 부모의 모집단이 그 수와
    다르면 상한이 부모에 걸리지 않은 것이다 — 1.26.194부터 10-13까지 실제로 그랬고
    (50보다 큰 상한을 줘도 상한 50의 후보), 머리말은 *"상한 100의 후보"* 라고 찍었다.
    틀린 자로 잰 표를 내느니 멈춘다.
    """
    top = max(limits)
    want = {r["duration"]: int(r["stations"]) for r in rows if r["limit"] == top}
    for duration, count in want.items():
        pop = pops.get(duration)
        got = 0 if pop is None else int(len(pop))
        if got != count:
            raise SystemExit(
                f"[실패] 고정 모집단이 상한 {top}의 후보가 아닙니다 — {duration}:"
                f" 부모 {got}곳 · 자식 {count}곳."
                f" 상한이 평가 쪽에 걸리지 않았습니다 — 이 표는 내지 않습니다.")


def evaluate(rows, limits, args) -> pd.DataFrame:
    """모든 계획을 **가장 넓은 상한의 후보 집합** 위에서 다시 평가한다."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        bc = load_baseline()

        step1 = bc.load_step1()
        # 상한은 project_config가 import될 때 굳는다 — 이 프로세스는 머리에서 이미 읽었으므로
        # 환경변수로는 못 바꾼다(그렇게 하려다 상한 50에 머물렀다). load_step1()이 주는
        # 독립 인스턴스의 전역을 갈아 끼운다 — 다른 모듈로 새지 않는다.
        step1.TOP_STATION_LIMIT = max(limits)
        net, st_info, warmup = bc.load_inputs(
            args.period, args.run_label, "weekday", 14, "")
        pops = {d: bc.build_candidates(net, st_info, d, None, warmup, 14, step1)
                for d in args.durations}
        check_population(rows, pops, limits)

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
    sizes = " · ".join(f"{d} {int(n)}곳" for d, n in
                       df.groupby("duration").population.first().items())
    print(f"고정 모집단(상한 {max(limits)}의 후보 — {sizes})에서 다시 잰 결품 시간(h)")
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
    parser.add_argument("--run-label", default=CANONICAL_RUN_LABEL)
    parser.add_argument("--limits", default="15,20,25,30,40,50")
    # 기본값은 운영값을 따른다 — 12로 박혀 있던 동안 인자 없이 돌린 표가 전부
    # '차량 12대'라는 조건을 숨긴 채 인용됐다(머리말 '숨은 조건 둘').
    parser.add_argument("--fleet", type=int,
                        default=project_config.VEHICLES_PER_ROUND,
                        help="회차당 차량 대수 (기본: 운영값)")
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
