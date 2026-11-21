"""통합 경로 + 예산(68장 I-c)의 결품을 원고 6장의 19개월 반복 자로 잰다 (EXPERIMENTS 73장, 사전 등록 2026-11-21).

왜 재나
  68장에서 시간 예산(120분)을 지키는 통합 경로(I-c)가 '분해 + 예산 끊기'보다 13.2% 더 옮겼다(현행 물량의 102.6%,
  OR-Tools 300초). 그러나 물량만 셌다. I-c는 예산 안에서 어느 단위든 뺄 수 있어 급한 대여소를 남길 수 있으므로,
  넣을지 정하기 전에 결품으로 잰다(TODO 결정 대기 ①). 사용자 요청 "68장 통합 경로 결품부터 19개월 반복으로 재 봐".

어떻게 재나 (결과를 보기 전에 정했다. 바꾸지 말 것)
  입력    원고 6장 19개월 반복과 같다: 정본 스냅샷 · 부록 A의 19개월 · 평일 · 낮 세 회차 · 씨앗 42 · 7 · 13 · 계절 보정 없음.
          회차마다 57쌍(19개월 × 씨앗 셋). 계획 통계와 채점은 같은 달이다(원고 6장의 자)
  갈래    B0    재배치 없음
          P     현행 분해(plan_with_clusters, 120분은 점검만) = 원고 6장의 P
          P-B   P와 같은 군집 · ILP 수량에서 그리디를 120분에서 끊고, 실은 채 돌아온 몫을 마지막 수거부터 덜어 낸다
                (68장 I-c의 출발해와 같다)
          I-c   68장과 같은 모형: 후보 전체의 수거 · 배송을 1대 단위 선택 노드로, 차마다 120분을 넘지 못하게,
                K = P의 군집 수, P-B에서 출발, OR-Tools 300초
          찍기만: P-B0(P-B에서 싣고 돌아온 몫을 덜지 않은 그대로) · B1(원고 6장의 그리디 대조군)
  채점    bc.simulate_pair에 그 회차 후보 집합(갈래 공통)을 넘겨 결품 · 포화(재배치 뒤, 대여소 · 일 평균)를 낸다.
          재고 증감은 executed_delta 하나로 낸다(정차 목록은 같은 꼴의 행으로 바꿔 넘긴다)
  판정    E = 현행 효과 = 평균(결품_B0 − 결품_P), 회차마다.
          주(I-c 대 P-B, 둘 다 예산을 지킨다)
            결품 개선 = 짝 차(I-c − P-B) 중앙값 < 0 · 부호순위 p < 0.05 · 평균 감소 ≥ 0.05 E
            포화 악화 = 짝 차 중앙값 > 0 · 부호순위 p < 0.05
            '예산 안에서 낫다' = 결품 개선이 세 회차 중 둘 이상이고 포화 악화인 회차가 없다
          보조(I-c 대 P, P에는 120분을 넘는 차가 있다)
            비열등 = 평균 짝 차(I-c − P) ≤ 0.05 E이고 포화 악화(위 정의)가 아니다. 세 회차 모두
          이름: 주 + 보조 → '운영 후보' · 주만 → '예산을 강제할 때만 낫다' · 주가 아니면 → '넣을 근거 없음'
  찍기만  옮겨 끝낸 대수 · 거리 · 120분 넘는 차(모든 갈래를 integrated_gap.solution_metrics 한 자로) ·
          계산 시간(P 계획, I-c 모형 짓기 · 탐색, 마지막으로 해가 나아진 시각)

실행
  python experiments/baseline/integrated_repeat.py --sanity     # 61장 원자료의 P · B0 · B1 재현, I-c 경로 확인(결품은 찍지 않는다)
  python experiments/baseline/integrated_repeat.py --periods "24년 08월,24년 09월" --out <CSV>   # 달을 나눠 함께 돌린다
  python experiments/baseline/integrated_repeat.py --summary-only <CSV> [<CSV> ...]
"""
import argparse
import contextlib
import io
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import project_config  # noqa: E402,F401  (콘솔 인코딩을 먼저 맞춘다)
from experiments._shared import CANONICAL_RUN_LABEL, load_baseline  # noqa: E402
from experiments.baseline import integrated_gap as ig  # noqa: E402

PERIODS = ["24년 08월", "24년 09월", "24년 10월", "24년 11월", "24년 12월", "25년 01월", "25년 02월", "25년 03월",
           "25년 04월", "25년 05월", "25년 06월", "25년 07월", "25년 08월", "25년 09월", "25년 10월", "25년 11월",
           "26년 01월", "26년 02월", "26년 03월"]          # 부록 A의 19개월(scripts/gate_a_rerun.ps1과 같다)
SEEDS = (42, 7, 13)
DAY_TYPE = "weekday"
KEYS = ["period", "seed", "duration"]
ARMS = ("B0", "P", "P-B", "I-c", "P-B0", "B1")
LIMIT_SEC = 300                    # 68장의 정본 탐색 시간
GAIN_SHARE = 0.05                  # 결품 개선: 평균 감소가 현행 효과의 5% 이상
NONINF_SHARE = 0.05                # 비열등: 평균 증가가 현행 효과의 5% 이하
P_LEVEL = 0.05
REFERENCE_CSV = ROOT / "data" / "exp61_f6_impact_1026.csv"   # 61장 원자료(원고 6장 자의 P · B0 · B1)


def stops_frame(routes) -> pd.DataFrame:
    """정차 목록(차 한 대 = 목록 하나)을 executed_delta가 읽는 행(action · qty · to_id)으로 바꾼다."""
    rows = [{"action": action, "qty": int(qty), "to_id": sid}
            for stops in routes for sid, action, qty, _la, _lo in stops]
    return pd.DataFrame(rows, columns=["action", "qty", "to_id"])


def last_improvement(progress) -> tuple:
    """(마지막으로 목적값이 나아진 시각(초), 해를 찾은 횟수). 기록이 없으면 (nan, 0)."""
    if not progress:
        return float("nan"), 0
    best = min(obj for _t, obj in progress)
    first = next(t for t, obj in progress if obj == best)
    return float(first), len(progress)


def run_case(bc, step1, solver, net, base, period, duration, seed, limit_sec):
    """한 (달, 씨앗, 회차)의 여섯 갈래. 갈래마다 한 행."""
    coords = {r.station_id: (float(r.lat), float(r.lon)) for r in base.itertuples()}

    t0 = time.time()
    _clustered, rows = bc.plan_with_clusters(base.copy(), step1, solver, adjust=True, seed=seed)
    p_sec = time.time() - t0
    if rows.empty:
        print(f"  [건너뜀] {period} {duration} 씨앗 {seed}: 계획이 비었다", flush=True)
        return []
    p_routes = ig.routes_from_rows(rows)
    clusters = list(p_routes)
    K = len(clusters)

    # P-B: 같은 군집 · 같은 ILP, 그리디를 120분에서 끊는다(68장 D-B). 실은 채 돌아온 몫은 덜어 낸다.
    t1 = time.time()
    nodes = ig.cluster_nodes(rows)
    db_rows = []
    for cluster in clusters:
        copied = {k: dict(v) for k, v in nodes[cluster].items()}     # 함정 12: 값 사전까지 복사
        db_rows.extend(bc.quiet(bc.vrp_mod.greedy_route, copied, cluster, ig.BUDGET_SEC))
    db_frame = pd.DataFrame(db_rows)
    db_routes = ig.routes_from_rows(db_frame)
    trimmed = [ig.trim_carry_back(s) for s in db_routes.values()]
    pb_sec = time.time() - t1

    # I-c: 68장과 같은 모형 · 같은 출발해
    t2 = time.time()
    demand_c = {}
    for r in base.itertuples():
        q = int(r.rebal_qty)
        if q < 0:
            demand_c[(r.station_id, "pick")] = -q
        elif q > 0:
            demand_c[(r.station_id, "drop")] = q
    mc = ig.UnitModel(demand_c, coords, DAY_TYPE)
    init_c, _left = mc.to_unit_routes(trimmed)
    build_sec = time.time() - t2
    progress = []
    t3 = time.time()
    sol_c, _s_obj, _e_obj, ic_status = ig.solve_units(mc, K, init_c, limit_sec, optional=True,
                                                      hard_budget=True, progress=progress)
    solve_sec = time.time() - t3
    ic_routes = [mc.to_stops(r) for r in sol_c] if sol_c else trimmed
    if not sol_c:
        ic_status += " · 출발해를 그대로 쓴다"
    best_sec, n_solutions = last_improvement(progress)

    b1_rows = bc.plan_greedy(base)

    plans = {
        "B0": ([], pd.Series(dtype=float)),
        "P": (list(p_routes.values()), bc.executed_delta(rows)),
        "P-B": (trimmed, bc.executed_delta(stops_frame(trimmed))),
        "I-c": (ic_routes, bc.executed_delta(stops_frame(ic_routes))),
        "P-B0": (list(db_routes.values()), bc.executed_delta(db_frame)),
        "B1": (list(ig.routes_from_rows(b1_rows).values()), bc.executed_delta(b1_rows)),
    }
    population = bc.stockout_population(net, base, duration)
    seconds = {"P": p_sec, "P-B": p_sec + pb_sec, "I-c": build_sec + solve_sec}
    out = []
    for arm in ARMS:
        routes, delta = plans[arm]
        sim = bc.simulate_pair(net, base, delta, duration)
        m = ig.solution_metrics(routes, DAY_TYPE)
        out.append({
            "period": period, "seed": seed, "duration": duration, "arm": arm, "K": K,
            "stations": int(len(base)), "population": population, **sim,
            "moved": m["moved"], "carried": m["carried"], "km": m["km"], "over": m["over"],
            "max_min": m["max_min"], "vehicles": m["vehicles"],
            "over_pipeline": bc.route_stats(rows)["over"] if arm == "P" else np.nan,
            "plan_sec": seconds.get(arm, np.nan),
            "ic_status": ic_status if arm == "I-c" else "",
            "ic_build_sec": build_sec if arm == "I-c" else np.nan,
            "ic_solve_sec": solve_sec if arm == "I-c" else np.nan,
            "ic_best_sec": best_sec if arm == "I-c" else np.nan,
            "ic_solutions": n_solutions if arm == "I-c" else np.nan,
            "limit_sec": limit_sec,
        })
    by = {r["arm"]: r for r in out}
    print(f"  [{period} {duration} 씨앗 {seed}] K {K} · 옮긴 대수 P {by['P']['moved']} · P-B {by['P-B']['moved']} · "
          f"I-c {by['I-c']['moved']}(초과 {by['I-c']['over']}) · P 계획 {p_sec:.1f}초 · I-c 짓기 {build_sec:.1f}초 + "
          f"탐색 {solve_sec:.0f}초(마지막 개선 {best_sec:.0f}초)", flush=True)
    return out


# ---------------------------------------------------------------- 판정

def signed_rank(diff: pd.Series) -> float:
    """윌콕슨 부호순위 p(0이 아닌 차가 다섯 개 미만이면 nan). rebal_cap_open과 같은 규칙."""
    from scipy.stats import wilcoxon
    diff = diff.dropna()
    if (diff != 0).sum() < 5:
        return float("nan")
    return float(wilcoxon(diff).pvalue)


def judge(frame: pd.DataFrame) -> dict:
    """사전 등록 판정. 회차마다의 값과 이름을 돌려준다."""
    by = {a: frame[frame["arm"] == a].set_index(KEYS) for a in ARMS}
    lines, gains, sat_bad, noninf = [], 0, 0, 0
    for duration in ig.DAY_ROUNDS:
        if duration not in by["P"].index.get_level_values("duration"):
            continue
        b0, p, pb, ic = (by[a].xs(duration, level="duration") for a in ("B0", "P", "P-B", "I-c"))
        effect = float((b0["stockout_after"] - p["stockout_after"]).mean())
        d = (ic["stockout_after"] - pb["stockout_after"]).dropna()
        ds = (ic["saturation_after"] - pb["saturation_after"]).dropna()
        e = (ic["stockout_after"] - p["stockout_after"]).dropna()
        es = (ic["saturation_after"] - p["saturation_after"]).dropna()
        p_d, p_ds, p_es = signed_rank(d), signed_rank(ds), signed_rank(es)
        gain = bool(d.median() < 0 and p_d < P_LEVEL and -d.mean() >= GAIN_SHARE * effect)
        bad = bool(ds.median() > 0 and p_ds < P_LEVEL)
        bad_p = bool(es.median() > 0 and p_es < P_LEVEL)
        ok = bool(e.mean() <= NONINF_SHARE * effect and not bad_p)
        gains, sat_bad, noninf = gains + gain, sat_bad + bad, noninf + ok
        lines.append({
            "duration": duration, "n": int(len(d)), "effect": effect,
            "d_mean": float(d.mean()), "d_median": float(d.median()), "p_d": p_d,
            "d_share": float(-d.mean() / effect) if effect else float("nan"), "gain": gain,
            "ds_median": float(ds.median()), "p_ds": p_ds, "sat_bad": bad,
            "e_mean": float(e.mean()), "e_share": float(e.mean() / effect) if effect else float("nan"),
            "es_median": float(es.median()), "p_es": p_es, "sat_bad_p": bad_p, "noninf": ok,
        })
    main = gains >= 2 and sat_bad == 0
    second = bool(lines) and noninf == len(lines)
    name = "운영 후보" if main and second else "예산을 강제할 때만 낫다" if main else "넣을 근거 없음"
    return {"lines": lines, "main": main, "second": second, "name": name,
            "gains": gains, "sat_bad": sat_bad, "noninf": noninf}


def summarize(frame: pd.DataFrame) -> None:
    cases = frame[KEYS].drop_duplicates()
    print("\n" + "=" * 100)
    print(f"사례 {len(cases)}개(회차마다 " + " · ".join(
        f"{d} {int((cases['duration'] == d).sum())}" for d in ig.DAY_ROUNDS) + ")")
    pops = frame.groupby(KEYS)["population"].nunique()
    print(f"모집단: 사례 안에서 갈래마다 같다 {int((pops == 1).sum())}/{len(pops)}")
    print("=" * 100)
    cols = ["stockout_after", "saturation_after", "moved", "km", "over", "vehicles"]
    table = frame.groupby(["duration", "arm"], sort=False)[cols].mean()
    print("갈래별 평균(회차마다, 결품 · 포화는 대여소 · 일 평균 시간)")
    print(table.round(3).to_string())
    over = frame.groupby("arm")["over"].sum()
    print("\n120분 넘는 차 합: " + " · ".join(f"{a} {int(over.get(a, 0))}" for a in ARMS))

    v = judge(frame)
    print("\n판정 (사전 등록)")
    for r in v["lines"]:
        print(f"  {r['duration']} (n = {r['n']}, 현행 효과 E = {r['effect']:.4f}h)")
        print(f"    주  I-c − P-B: 결품 평균 {r['d_mean']:+.4f} · 중앙 {r['d_median']:+.4f} · p = {r['p_d']:.3g} · "
              f"E 대비 {r['d_share'] * 100:+.1f}% → {'개선' if r['gain'] else '개선 아님'} | "
              f"포화 중앙 {r['ds_median']:+.4f} · p = {r['p_ds']:.3g}{' → 악화' if r['sat_bad'] else ''}")
        print(f"    보조 I-c − P: 결품 평균 {r['e_mean']:+.4f}(E의 {r['e_share'] * 100:+.1f}%) · "
              f"포화 중앙 {r['es_median']:+.4f} · p = {r['p_es']:.3g} → {'비열등' if r['noninf'] else '비열등 아님'}")
    print(f"  → 주: 결품 개선 {v['gains']}/3 · 포화 악화 {v['sat_bad']}/3 → {'예산 안에서 낫다' if v['main'] else '낫지 않다'}"
          f" | 보조: 비열등 {v['noninf']}/{len(v['lines'])}")
    print(f"  → {v['name']}")

    ic = frame[frame["arm"] == "I-c"]
    p = frame[frame["arm"] == "P"]
    if not ic.empty:
        print("\n찍기만: 계산 시간(초)")
        print(f"  P 계획(군집 · ILP · 그리디) 중앙 {p['plan_sec'].median():.1f} · 평균 {p['plan_sec'].mean():.1f} · "
              f"최대 {p['plan_sec'].max():.1f}")
        print(f"  I-c 모형 짓기 중앙 {ic['ic_build_sec'].median():.1f} · 탐색 중앙 {ic['ic_solve_sec'].median():.0f}"
              f"(제한 {int(ic['limit_sec'].iloc[0])}초)")
        best = ic["ic_best_sec"].dropna()
        if not best.empty:
            q = best.quantile([0.5, 0.9]).tolist()
            print(f"  I-c 마지막 개선 시각 중앙 {q[0]:.0f} · 90% {q[1]:.0f} · 최대 {best.max():.0f} | "
                  + " · ".join(f"{s}초 안 {(best <= s).mean() * 100:.0f}%" for s in (60, 120, 240)))
        print("  I-c 상태: " + " · ".join(f"{k} {n}" for k, n in ic["ic_status"].value_counts().items()))


# ---------------------------------------------------------------- 실행

def prepare():
    with contextlib.redirect_stdout(io.StringIO()):
        bc = load_baseline()
    step1 = bc.load_step1()
    bc.align_day_type("weekday", bc.ilp_mod, bc.vrp_mod, bc.kpi_mod, step1)
    solver = bc.ilp_mod.build_solver()
    return bc, step1, solver


def sanity() -> int:
    """61장 원자료의 P · B0 · B1을 그대로 내는지, I-c 경로가 끝까지 도는지 본다(I-c 결품은 찍지 않는다)."""
    bc, step1, solver = prepare()
    period, seed, duration = PERIODS[0], SEEDS[0], ig.DAY_ROUNDS[0]
    net, st_info, _w = bc.load_inputs(period, CANONICAL_RUN_LABEL, "weekday", 0, "")
    base = bc.build_candidates(net, st_info, duration, None, pd.DataFrame(), 0, step1)
    rows = run_case(bc, step1, solver, net, base, period, duration, seed, 10)
    got = {r["arm"]: r for r in rows}
    bad = 0
    if REFERENCE_CSV.exists():
        ref = pd.read_csv(REFERENCE_CSV)
        ref = ref[(ref["period"] == period) & (ref["seed"] == seed) & (ref["duration"] == duration)
                  & (ref["clock"] == "L")].set_index("method")
        for arm in ("P", "B0", "B1"):
            if arm not in ref.index:
                print(f"  61장 원자료에 {arm}이 없다")
                continue
            a, b = got[arm], ref.loc[arm]
            same = (abs(a["stockout_after"] - b["stockout_after"]) < 1e-9
                    and abs(a["saturation_after"] - b["saturation_after"]) < 1e-9
                    and a["stations"] == b["stations"])
            bad += not same
            print(f"  {arm}: 결품 {a['stockout_after']:.6f} 대 61장 {b['stockout_after']:.6f} · 포화 "
                  f"{a['saturation_after']:.6f} 대 {b['saturation_after']:.6f} · 후보 {a['stations']} 대 "
                  f"{int(b['stations'])} → {'같다' if same else '다르다'}")
    else:
        print(f"  61장 원자료가 없어 재현은 보지 못했다: {REFERENCE_CSV}")
    ic = got["I-c"]
    print(f"  I-c 경로(10초): {ic['ic_status']} · 초과 {ic['over']} · 싣고 돌아옴 {ic['carried']} · "
          f"해 {ic['ic_solutions']}번")
    bad += ic["over"] != 0 or ic["carried"] != 0
    print("입력 점검 " + ("✅" if bad == 0 else f"❌ {bad}건"))
    return 0 if bad == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="통합 경로 + 예산의 19개월 반복 결품 (사전 등록 2026-11-21)")
    parser.add_argument("--periods", default=",".join(PERIODS), help="달(쉼표 구분, 기본: 부록 A의 19개월)")
    parser.add_argument("--limit-sec", type=int, default=LIMIT_SEC, help="I-c 탐색 시간(초, 등록: 300)")
    parser.add_argument("--out", default="", help="사례별 결과 CSV(사례마다 덮어써 중간 결과를 남긴다)")
    parser.add_argument("--sanity", action="store_true", help="61장 재현과 I-c 경로만 확인한다")
    parser.add_argument("--summary-only", nargs="+", default=None, help="이미 만든 CSV(여럿 가능)로 판정만 다시")
    args = parser.parse_args()

    if args.summary_only:
        summarize(pd.concat([pd.read_csv(f) for f in args.summary_only], ignore_index=True))
        return 0
    if args.sanity:
        return sanity()

    bc, step1, solver = prepare()
    periods = [p.strip() for p in args.periods.split(",") if p.strip()]
    rows = []
    started = time.time()
    for period in periods:
        bc.reset_population_guard()
        net, st_info, _w = bc.load_inputs(period, CANONICAL_RUN_LABEL, "weekday", 0, "")
        for duration in ig.DAY_ROUNDS:
            base = bc.build_candidates(net, st_info, duration, None, pd.DataFrame(), 0, step1)
            if base.empty:
                print(f"[건너뜀] {period} {duration}: 작업 대상 없음")
                continue
            for seed in SEEDS:
                rows.extend(run_case(bc, step1, solver, net, base, period, duration, seed, args.limit_sec))
                if args.out:
                    pd.DataFrame(rows).to_csv(args.out, index=False, encoding="utf-8")
        print(f"[{period}] 끝 · 누적 {time.time() - started:.0f}초", flush=True)
    if not rows:
        raise SystemExit("결과가 없습니다.")
    summarize(pd.DataFrame(rows))
    if args.out:
        print(f"\n결과를 저장했습니다: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
