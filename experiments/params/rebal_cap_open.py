"""한 대여소 재배치량의 실효 상한을 9대에서 10대로 열면 결품 · 시간이 나아지나 (EXPERIMENTS 62장, 사전 등록 2026-11-08).

왜: 식 (3.5)의 tanh 완화와 0 쪽 정수화 때문에 한 대여소 계획량은 적재량(10대)이 아니라 9대에서 끊긴다(DECISIONS 5-B).
지금까지는 단순 절단의 작업량(+13.1%)만 쟀고 결품 · 포화 · 시간은 재지 않았다. 사용자 물음(2026-11-08)
*"10대로 하면 성능에 유의미한 개선이 있을까? 시간 등"* · *"재봐라"*.

어떻게 재나(결과를 보기 전에 정했다. 바꾸지 말 것):
    입력    원고 6장 19개월 반복과 같다: 정본 스냅샷 '2026-08-11 real' · 부록 A의 19개월 · 평일 · 세 회차 · 씨앗 42 · 7 · 13 ·
            방법 P(제안 방법) · 계절 보정 없음
    규칙    T = 현행, trunc(10·tanh(gap/10)), 실효 상한 9
            H = 상한만 연다, trunc(11·tanh(gap/11)). 격차 16.7대 이상만 10이 되고 작은 값은 T와 거의 같다(5 → 4, 8 → 6)
            C = 단순 절단, trunc(clip(gap, −10, 10)). 상한 10에 작은 값도 누르지 않는다(5 → 5, 8 → 8)
            gap = target_qty − stock(운영 코드 `compute_rebal_qty`가 낸 그대로). 이 실험 안에서만 rebal_qty를 다시 매긴다
    모집단  세 규칙의 작업 대상을 합친 것(B2와 같은 방식). 규칙마다 후보가 달라 자기 후보로 평균을 내면 분모가 흔들린다.
            같은 모집단에서 무재배치(B0)도 잰다
    지표    결품 · 포화(재배치 뒤, 대여소 · 일 평균) · 끝낸 대수 · 이동거리 · 군집 수 · 120분 넘는 군집 · 군집 평균 소요 · 최장 소요
    판정    규칙 V(H · C)마다 T와 짝지어(n = 57, 회차마다):
            결품 개선 = 짝 차(V − T) 중앙값 < 0 · 부호순위 p < 0.05 · 평균 감소가 현행이 무재배치 대비 줄인 결품(B0 − T)의 5% 이상
            포화 악화 = 짝 차 중앙값 > 0 · p < 0.05
            *의미 있는 개선* = 결품 개선이 세 회차 중 둘 이상이고 포화 악화인 회차가 없다
            *시간이 나아진다* = 120분 넘는 군집 합이 T보다 적고 군집 평균 소요의 짝 차 중앙값 < 0 · p < 0.05인 회차가 둘 이상
            *채택 후보* = 의미 있는 개선이면서 120분 넘는 군집 합이 T의 1.1배 이하이고 군집 수 평균이 T보다 0.5대 넘게 늘지 않는다.
            의미 있는 개선인데 시간 조건을 못 넘으면 *대가가 남는다*, 개선이 아니면 *바꿀 까닭 없음*

실행:
    python experiments/params/rebal_cap_open.py --out data/exp62_cap_1108.csv
    python experiments/params/rebal_cap_open.py --sanity     # 규칙 셋이 T를 그대로 재현하는지만 확인(계획을 세우지 않는다)
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments" / "baseline"))

import numpy as np                                                      # noqa: E402
import pandas as pd                                                     # noqa: E402

import project_config                                                   # noqa: E402,F401  (출력 인코딩)
import baseline_compare as bc                                           # noqa: E402
from experiments._shared import CANONICAL_RUN_LABEL                     # noqa: E402
from project_config import TIME_BUDGET_MINUTES                          # noqa: E402

PERIODS = ["24년 08월", "24년 09월", "24년 10월", "24년 11월", "24년 12월", "25년 01월", "25년 02월", "25년 03월",
           "25년 04월", "25년 05월", "25년 06월", "25년 07월", "25년 08월", "25년 09월", "25년 10월", "25년 11월",
           "26년 01월", "26년 02월", "26년 03월"]          # 부록 A의 19개월(scripts/gate_a_rerun.ps1과 같다)
SEEDS = [42, 7, 13]
DURATIONS = ["_05_10", "_10_15", "_15_20"]
RULES = ["T", "H", "C"]
RUN_LABEL = CANONICAL_RUN_LABEL          # 원고 6장의 정본 스냅샷
KEYS = ["period", "seed", "duration"]
ORIGINAL = bc.target_mod.compute_rebal_qty


def rule_qty(gap, rule: str) -> np.ndarray:
    """격차(target_qty − stock)에 규칙을 매긴 재배치량. 식 (3.5)처럼 0 쪽으로 정수화한다."""
    gap = np.asarray(gap, dtype=float)
    if rule == "T":
        value = 10 * np.tanh(gap / 10)
    elif rule == "H":
        value = 11 * np.tanh(gap / 11)
    elif rule == "C":
        value = np.clip(gap, -10, 10)
    else:
        raise ValueError(f"모르는 규칙: {rule}")
    return np.trunc(value).astype(int)


def make_compute(rule: str):
    def compute(stats, z=None, up_limit=None, model_target=None):
        out = ORIGINAL(stats, z=z, up_limit=up_limit, model_target=model_target)
        out["rebal_qty"] = rule_qty(out["target_qty"] - out["stock"], rule)
        return out
    return compute


def candidates(net, st_info, duration, step1, rule):
    bc.target_mod.compute_rebal_qty = make_compute(rule)
    try:
        return bc.build_candidates(net, st_info, duration, None, pd.DataFrame(), 0, step1)
    finally:
        bc.target_mod.compute_rebal_qty = ORIGINAL


def time_stats(routes) -> dict:
    if routes.empty:
        return {"mean_min": 0.0, "total_min": 0.0}
    minutes = routes.groupby("cluster")["cum_sec"].max() / 60
    return {"mean_min": float(minutes.mean()), "total_min": float(minutes.sum())}


def collect() -> pd.DataFrame:
    step1 = bc.load_step1()
    bc.align_day_type("weekday", bc.ilp_mod, bc.vrp_mod, bc.kpi_mod, step1)
    solver = bc.ilp_mod.build_solver()
    rows = []
    total = len(PERIODS) * len(SEEDS)
    for i, (period, seed) in enumerate(((p, s) for p in PERIODS for s in SEEDS), 1):
        print(f"\n### [{i}/{total}] 기간 {period} · 씨앗 {seed}", flush=True)
        net, st_info, _warmup = bc.load_inputs(period, RUN_LABEL, "weekday", 0, "")
        for duration in DURATIONS:
            base = {rule: candidates(net, st_info, duration, step1, rule) for rule in RULES}
            if any(c.empty for c in base.values()):
                print(f"  [건너뜀] {duration}: 작업 대상이 빈 규칙이 있다")
                continue
            population = (pd.concat([base[r] for r in RULES], ignore_index=True)
                          .drop_duplicates(subset="station_id", keep="first"))
            plans = {"B0": pd.DataFrame()}
            for rule in RULES:
                _c, plans[rule] = bc.plan_with_clusters(base[rule].copy(), step1, solver, adjust=True, seed=seed)
            for name, routes in plans.items():
                sim = bc.simulate_pair(net, population, bc.executed_delta(routes), duration)
                cand = base.get(name)
                rows.append({"period": period, "seed": seed, "duration": duration, "rule": name,
                             "population": len(population),
                             "stations": 0 if cand is None else len(cand),
                             "planned": 0 if cand is None else int(cand["rebal_qty"].abs().sum()),
                             "at_cap": 0 if cand is None else int((cand["rebal_qty"].abs() >= 10).sum()),
                             **sim, **bc.route_stats(routes), **time_stats(routes)})
            line = " · ".join(f"{r['rule']} {r['stockout_after']:.3f}h/{r['over']}" for r in rows[-4:])
            print(f"  {duration} 결품/넘는 군집: {line}", flush=True)
    return pd.DataFrame(rows)


def signed_rank(diff: pd.Series) -> float:
    from scipy.stats import wilcoxon
    diff = diff.dropna()
    if (diff != 0).sum() < 5:
        return float("nan")
    return float(wilcoxon(diff).pvalue)


def report(frame: pd.DataFrame) -> None:
    by = {r: frame[frame["rule"] == r].set_index(KEYS) for r in ["B0", *RULES]}
    t = by["T"]
    print("\n규칙별 평균(세 회차 합친 것이 아니라 회차마다)")
    cols = ["stockout_after", "saturation_after", "planned", "bikes_done", "km", "vehicles", "over", "mean_min", "max_min", "at_cap"]
    print(frame.groupby(["duration", "rule"])[cols].mean().round(3).to_string())

    for rule in ("H", "C"):
        v = by[rule]
        gains, sat_bad, faster = 0, 0, 0
        print(f"\n[{rule} 대 T]")
        for duration in DURATIONS:
            a, b, z = (x.xs(duration, level="duration") for x in (t, v, by["B0"]))
            so = (b["stockout_after"] - a["stockout_after"]).dropna()
            sa = (b["saturation_after"] - a["saturation_after"]).dropna()
            mm = (b["mean_min"] - a["mean_min"]).dropna()
            effect = float((z["stockout_after"] - a["stockout_after"]).mean())
            p_so, p_sa, p_mm = signed_rank(so), signed_rank(sa), signed_rank(mm)
            gain = so.median() < 0 and p_so < 0.05 and -so.mean() >= 0.05 * effect
            bad = sa.median() > 0 and p_sa < 0.05
            quick = mm.median() < 0 and p_mm < 0.05
            gains, sat_bad, faster = gains + gain, sat_bad + bad, faster + quick
            print(f"  {duration}: 결품 {so.median():+.4f} (p={p_so:.2g}, 평균 {so.mean():+.4f} · 현행 효과 {effect:.4f}의 "
                  f"{-so.mean() / effect * 100:+.1f}%) {'개선' if gain else ''} · 포화 {sa.median():+.4f} (p={p_sa:.2g})"
                  f"{' 악화' if bad else ''} · 군집 평균 소요 {mm.median():+.2f}분 (p={p_mm:.2g})")
        over_t, over_v = int(t["over"].sum()), int(v["over"].sum())
        veh = float(v["vehicles"].mean() - t["vehicles"].mean())
        meaningful = gains >= 2 and sat_bad == 0
        quicker = over_v < over_t and faster >= 2
        if meaningful and over_v <= 1.1 * over_t and veh <= 0.5:
            verdict = "채택 후보"
        elif meaningful:
            verdict = "대가가 남는다"
        else:
            verdict = "바꿀 까닭 없음"
        print(f"  120분 넘는 군집 합 {over_t} → {over_v} · 군집 수 평균 {veh:+.2f} · 끝낸 대수 비 "
              f"{v['bikes_done'].mean() / t['bikes_done'].mean():.3f} · 거리 비 {v['km'].mean() / t['km'].mean():.3f}")
        print(f"  판정: 의미 있는 개선 {'예' if meaningful else '아니오'}(결품 개선 {gains}/3 · 포화 악화 {sat_bad}/3) · "
              f"시간이 나아진다 {'예' if quicker else '아니오'} → {verdict}")


def sanity() -> int:
    """T 규칙이 운영 코드의 rebal_qty를 그대로 재현하는지, H · C가 어디서 갈리는지 본다."""
    net, st_info, _w = bc.load_inputs(PERIODS[-1], RUN_LABEL, "weekday", 0, "")
    stats, _d, _r = bc.quiet(bc.target_mod.build_stats, net, st_info[["station_id", "parking_lot", "stock"]],
                             DURATIONS[0], warmup_net=None, warmup_days=0, verbose=False)
    out = bc.quiet(ORIGINAL, stats.copy())
    gap = out["target_qty"] - out["stock"]
    same = int((rule_qty(gap, "T") == out["rebal_qty"].to_numpy()).sum())
    print(f"T 재현: {same:,}/{len(out):,}곳 같다")
    probe = np.array([3, 5, 8, 9, 10, 12, 15, 17, 20, 40, -5, -12, -20], dtype=float)
    print("  격차 :", probe.astype(int).tolist())
    for rule in RULES:
        print(f"  {rule}    :", rule_qty(probe, rule).tolist())
    return 0 if same == len(out) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sanity", action="store_true", help="규칙 셋만 확인한다(계획을 세우지 않는다)")
    parser.add_argument("--out", default="", help="원자료 CSV 경로")
    args = parser.parse_args()
    if args.sanity:
        return sanity()
    frame = collect()
    if args.out:
        frame.to_csv(args.out, index=False, encoding="utf-8")
        print(f"\n원자료: {args.out} ({len(frame)}행)")
    print(f"[점검] 시간 예산 {TIME_BUDGET_MINUTES}분 · 조합 {frame[KEYS].drop_duplicates().shape[0]}개")
    report(frame)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
