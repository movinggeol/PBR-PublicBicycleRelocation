"""다기간 계획: 회차를 이어 굴리면 앞 회차의 수거가 다음 회차의 배송을 부르나, 내다보는 규칙이 하루 결품을 줄이나
(2026-11-21, 사전 등록).

왜 재나
  제안 방법은 회차마다 독립된 정적 문제를 푼다. 원고 2.2.1과 발표 자료는 "회차 사이의 영향은 모형에 넣지 않았다"를
  근시안의 대가로 밝혔다(docs/연구/발표_기술선택.md 1절, 버전관리 1.26.427). 그 대가가 얼마인지는 잰 적이 없다.
  이 저장소의 대조군 비교(baseline_compare)는 회차마다 같은 스냅샷에서 따로 출발하므로 회차 사이의 영향을 볼 수 없다.
  그래서 하루를 05시부터 24시까지 회차를 이어 굴리는 시뮬레이션을 만든다.

어떻게 굴리나
  - 날마다 05시 재고 = 스냅샷 재고. 회차는 지금 운영 회차 가운데 낮의 넷(`_00_05`를 뺀 DURATIONS).
  - r회차 계획은 '계획 재고' S_r로 세운다. S_1 = 스냅샷, S_{r+1} = 날들의 r회차 끝 재고 평균(반올림, 0 ~ 거치대).
    계획은 (달, 씨앗, 정책)마다 회차당 한 벌이고 모든 날에 같다(그날 재고로 다시 세우는 운영보다 거칠다).
  - 날마다 r회차 시작 재고에 그 계획의 집행 증감(executed_delta)을 더하고 r회차 시간 동안 그날 순수요로 굴린다.
    결품 · 포화 시간은 step4의 `_simulate_stock()`으로 세고, 끝 재고는 같은 규칙(clip(재고 − 순수요, 0, 거치대))으로 굴린다.
    첫 회차에서 두 셈의 결품 수가 같은지 맞대 본다(어긋나면 멈춘다).
  - 계획은 운영 함수 그대로다(build_stats · compute_rebal_qty · select_top_unbalanced_st · 군집 · ILP · 그리디).

정책
  M   현행(근시안): 회차마다 지금 파이프라인 그대로
  L1  다음 회차를 지키는 수거: r = 1..3에서 수거 후보 i의 수거량 q를 q' = clip(floor(S_r(i) − μ_r(i) − T_{r+1}(i)), 0, q)로 줄인다.
      T_{r+1}은 다음 회차 통계로 낸 목표 재고(다음 회차 μ ≥ 0인 곳만, 나머지는 줄이지 않는다), μ_r는 r회차 평균 순유출.
  L2  두 회차 지평 목표: r = 1..3에서 목표 재고 통계를 r과 r+1 회차를 합친 창(_05_15 · _10_20 · _15_24)으로 낸다.
  마지막 회차(_20_24)는 세 정책 모두 현행이다.

신호 검사 (사전 등록)
  되돌림률 = M에서 r회차에 실제로 수거한 대수 가운데 r+1회차에 실제로 배송을 받은 대여소에서 뺀 대수의 비율(r = 1..3, 전체 합).
  5% 미만이면 '되돌림이 작다'로 보고 L1 · L2는 짧게(25년 11월 · 26년 03월 × 씨앗 42) 찍기만 한다. 5% 이상이면 모두 잰다.

판정 (사전 등록, L1 · L2 각각 M 대비, 달 × 씨앗 쌍 합계)
  하루 결품 −3% 이상 · 옮긴 대수 +5% 이내 · 포화 +1% 이내(상대) · 결품이 준 쪽의 부호 검정(양측) p < 0.05 → '후보'

재현
  python experiments/structure/multi_period.py --out experiments/structure/multi_period_261121.csv
  python experiments/structure/multi_period.py --summary-only experiments/structure/multi_period_261121.csv
"""
import argparse
import ast
import contextlib
import io
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import project_config  # noqa: E402,F401  (콘솔 인코딩을 먼저 맞춘다)
from project_config import DURATIONS, duration_hours, duration_start_hour, normalize_day_type  # noqa: E402
from experiments._shared import load_baseline, resolve_run_label  # noqa: E402

ROUNDS = tuple(d for d in DURATIONS if d != "_00_05")        # 05 → 10 → 15 → 20 → 24
PERIODS = ("25년 01월", "25년 05월", "25년 07월", "25년 09월", "25년 11월", "26년 03월")
SEEDS = (42, 7, 13)
POLICIES = ("M", "L1", "L2")
SHORT_RUN = (("25년 11월", 42), ("26년 03월", 42))      # 되돌림이 작을 때 L1 · L2를 찍는 범위
REVERSAL_THRESHOLD = 0.05
EMPTY = pd.DataFrame()


def round_end(duration: str) -> int:
    """회차가 끝나는 시(時). `_20_24` → 24."""
    return duration_hours(duration)[-1] + 1


def merged_window(duration: str, following: str) -> str:
    """r과 r+1 회차를 합친 창의 이름(L2). `_05_10` + `_10_15` → `_05_15`."""
    return f"_{duration_start_hour(duration):02d}_{round_end(following):02d}"


# ---------------------------------------------------------------- 정책별 작업 대상

def candidates(bc, step1, net, st_plan, policy: str, index: int):
    """정책에 맞춰 작업 대상을 고른다. 선정 함수는 운영 것 그대로다."""
    duration = ROUNDS[index]
    has_next = index + 1 < len(ROUNDS)
    if policy == "M" or not has_next:
        return bc.build_candidates(net, st_plan, duration, None, EMPTY, 0, step1)
    following = ROUNDS[index + 1]
    if policy == "L2":
        return bc.build_candidates(net, st_plan, merged_window(duration, following), None, EMPTY, 0, step1)

    # L1: 운영 계산을 그대로 거친 재배치량에서 수거만 다음 회차 목표 재고까지로 줄인다.
    initial = st_plan[["station_id", "parking_lot", "stock"]]
    stats, _daily, _ratio = bc.quiet(bc.target_mod.build_stats, net, initial, duration,
                                     warmup_net=None, warmup_days=0, verbose=False)
    rebal = bc.quiet(bc.target_mod.compute_rebal_qty, stats)
    nstats, _d, _r = bc.quiet(bc.target_mod.build_stats, net, initial, following,
                              warmup_net=None, warmup_days=0, verbose=False)
    nxt = bc.quiet(bc.target_mod.compute_rebal_qty, nstats).set_index("station_id")
    guard = nxt.loc[nxt["mu"] >= 0, "target_qty"]                   # 다음 회차에 빠져나갈 곳만 지킨다
    rebal = rebal.copy()
    t_next = rebal["station_id"].map(guard)
    pick = (rebal["rebal_qty"] < 0) & t_next.notna()
    room = np.floor(rebal["stock"] - rebal["mu"] - t_next).clip(lower=0)
    kept = np.minimum(-rebal["rebal_qty"], room)
    rebal.loc[pick, "rebal_qty"] = (-kept[pick]).astype(int)
    rebal["rebal_qty"] = rebal["rebal_qty"].astype(int)

    buffer = io.StringIO()
    rebal.to_csv(buffer, index=False, encoding="utf-8")
    buffer.seek(0)
    return bc.quiet(step1.select_top_unbalanced_st, buffer, duration, st_plan)


# ---------------------------------------------------------------- 하루 순차 시뮬레이션

def run_day(bc, step1, solver, net, st_info, seed: int, policy: str, period: str, checked: dict):
    """한 (달, 씨앗, 정책)의 05 ~ 24시를 회차별로 굴리고 회차별 기록을 돌려준다."""
    merged = net.merge(st_info[["station_id", "parking_lot", "stock"]], on="station_id", how="inner")
    cap = merged["parking_lot"].astype(float)
    day_stock = merged["stock"].astype(float).copy()
    stations = int(merged["station_id"].nunique())
    days = int(merged["날짜"].nunique())

    # 첫 회차는 스냅샷 그대로(대조군 하네스의 계획과 같다). 뒤 회차는 시뮬레이션 끝 재고라 이미 0 ~ 거치대다.
    plan_stock = st_info.set_index("station_id")["stock"].astype(float)
    records = []
    for index, duration in enumerate(ROUNDS):
        st_plan = st_info.copy()
        # 스냅샷에는 재고 · 거치대가 빈 곳이 19곳(차고지 ST0001 포함) 있다. 대조군 하네스처럼 실수(NaN 포함)로 둔다.
        st_plan["stock"] = st_plan["station_id"].map(plan_stock).round()
        base = candidates(bc, step1, net, st_plan, policy, index)
        if base.empty:
            routes = pd.DataFrame()
        else:
            _clustered, routes = bc.plan_with_clusters(base.copy(), step1, solver, adjust=True, seed=seed)
        delta = bc.executed_delta(routes)
        stats = bc.route_stats(routes)

        work = routes[routes["action"].isin(["pick", "drop"])] if not routes.empty else routes
        picks = work[work["action"] == "pick"].groupby("to_id")["qty"].sum() if not work.empty else pd.Series(dtype=float)
        drops = work[work["action"] == "drop"].groupby("to_id")["qty"].sum() if not work.empty else pd.Series(dtype=float)
        drop_targets = set(base.loc[base["rebal_qty"] > 0, "station_id"]) if not base.empty else set()

        start = day_stock + merged["station_id"].map(delta).fillna(0).astype(float)
        shortfall = float((-start).clip(lower=0).sum())          # 그날 재고보다 많이 뺀 양
        overfill = float((start - cap).clip(lower=0).sum())      # 거치대를 넘게 채운 양
        hours = duration_hours(duration)
        counted = bc.kpi_mod._simulate_stock(merged, start, cap, hours)

        stock = start.copy()
        mine = np.zeros(len(stock), dtype=int)
        for hour in hours:
            column = f"net_{hour:02d}"
            if column not in merged.columns:
                continue
            stock = (stock - merged[column]).clip(lower=0).clip(upper=cap)
            mine += (stock <= 0).to_numpy().astype(int)
        if not checked.get("done"):
            if int(mine.sum()) != int(counted["stockout"].sum()):
                raise SystemExit(f"끝 재고를 굴리는 규칙이 _simulate_stock과 다르다: {int(mine.sum())} 대 "
                                 f"{int(counted['stockout'].sum())}")
            checked["done"] = True
        day_stock = stock

        end_mean = day_stock.groupby(merged["station_id"]).mean()
        plan_stock = plan_stock.copy()
        plan_stock.loc[end_mean.index] = end_mean

        records.append({
            "period": period, "seed": seed, "policy": policy, "round": duration, "round_index": index,
            "stations": stations, "days": days,
            "stockout_h": float(counted["stockout"].sum()), "saturated_h": float(counted["saturated"].sum()),
            "unmet": float(counted["unmet"].sum()),
            "cand_n": int(len(base)), "moved": int(stats["bikes_done"]), "km": float(stats["km"]),
            "over": int(stats["over"]), "max_min": float(stats["max_min"]), "vehicles": int(stats["vehicles"]),
            "shortfall": shortfall, "overfill": overfill,
            "picks": picks.to_dict(), "drops": drops.to_dict(), "drop_targets": sorted(drop_targets),
        })
    return records


# ---------------------------------------------------------------- 되돌림과 판정

def reversal(records) -> dict:
    """M의 회차 기록에서 되돌림 두 방향을 센다."""
    by_key = {}
    for r in records:
        by_key.setdefault((r["period"], r["seed"]), {})[r["round_index"]] = r
    picked = back = cand_back = dropped = flip = 0
    for rounds in by_key.values():
        for i in range(len(ROUNDS) - 1):
            cur, nxt = rounds.get(i), rounds.get(i + 1)
            if cur is None or nxt is None:
                continue
            for sid, q in cur["picks"].items():
                picked += q
                if nxt["drops"].get(sid, 0) > 0:
                    back += q
                if sid in set(nxt["drop_targets"]):
                    cand_back += q
            for sid, q in cur["drops"].items():
                dropped += q
                if nxt["picks"].get(sid, 0) > 0:
                    flip += q
    return {"picked": picked, "back": back, "rate": back / picked if picked else float("nan"),
            "cand_rate": cand_back / picked if picked else float("nan"),
            "dropped": dropped, "flip": flip, "flip_rate": flip / dropped if dropped else float("nan")}


def day_totals(frame: pd.DataFrame) -> pd.DataFrame:
    """(달, 씨앗, 정책)마다 하루 합계. 결품 · 포화는 대여소 · 일 평균(분모는 회차마다 같다)."""
    g = frame.groupby(["period", "seed", "policy"], sort=False)
    out = g.agg(stockout_h=("stockout_h", "sum"), saturated_h=("saturated_h", "sum"), moved=("moved", "sum"),
                km=("km", "sum"), over=("over", "sum"), stations=("stations", "max"), days=("days", "max"),
                pop_min=("stations", "min"), shortfall=("shortfall", "sum")).reset_index()
    if (out["stations"] != out["pop_min"]).any():
        raise SystemExit("회차마다 모집단이 다르다: 분모가 흔들린다")
    denom = out["stations"] * out["days"]
    out["stockout"] = out["stockout_h"] / denom
    out["saturation"] = out["saturated_h"] / denom
    return out


def sign_test(wins: int, losses: int) -> float:
    """양측 부호 검정(동률 제외)."""
    n = wins + losses
    if n == 0:
        return float("nan")
    k = min(wins, losses)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def judge(totals: pd.DataFrame, policy: str) -> dict:
    m = totals[totals["policy"] == "M"].set_index(["period", "seed"])
    p = totals[totals["policy"] == policy].set_index(["period", "seed"])
    keys = m.index.intersection(p.index)
    if len(keys) == 0:
        return {}
    m, p = m.loc[keys], p.loc[keys]
    d_stock = p["stockout_h"].sum() / m["stockout_h"].sum() - 1
    d_moved = p["moved"].sum() / m["moved"].sum() - 1
    d_sat = p["saturated_h"].sum() / m["saturated_h"].sum() - 1
    wins = int((p["stockout_h"] < m["stockout_h"]).sum())
    losses = int((p["stockout_h"] > m["stockout_h"]).sum())
    pval = sign_test(wins, losses)
    months = (p.groupby(level="period")["stockout_h"].mean() < m.groupby(level="period")["stockout_h"].mean())
    ok = bool(d_stock <= -0.03 and d_moved <= 0.05 and d_sat <= 0.01 and wins > losses and pval < 0.05)
    return {"pairs": len(keys), "d_stockout": d_stock, "d_moved": d_moved, "d_saturation": d_sat,
            "d_km": p["km"].sum() / m["km"].sum() - 1, "over_M": int(m["over"].sum()), "over_P": int(p["over"].sum()),
            "wins": wins, "losses": losses, "p": pval, "months_better": int(months.sum()), "months": len(months),
            "candidate": ok}


def summarize(frame: pd.DataFrame, rev: dict) -> None:
    totals = day_totals(frame)
    print("\n" + "=" * 96)
    print("신호 검사 (M)")
    print("=" * 96)
    print(f"r회차 수거 {rev['picked']}대 중 r+1회차에 배송을 받은 곳에서 뺀 것 {rev['back']}대 "
          f"= 되돌림률 {rev['rate'] * 100:.2f}% (다음 회차 배송 후보 기준 {rev['cand_rate'] * 100:.2f}%) · 문턱 5%")
    print(f"반대 방향(찍기만): r회차 배송 {rev['dropped']}대 중 r+1회차에 수거된 곳 {rev['flip']}대 "
          f"= {rev['flip_rate'] * 100:.2f}%")

    print("\n하루(05 ~ 24시) 합계, 대여소 · 일 평균")
    show = totals.copy()
    print(show[["period", "seed", "policy", "stockout", "saturation", "moved", "km", "over", "shortfall",
                "stations", "days"]].round(4).to_string(index=False))
    for policy in ("L1", "L2"):
        j = judge(totals, policy)
        if not j:
            continue
        print(f"\n[{policy} 대 M] 쌍 {j['pairs']} · 결품 {j['d_stockout'] * 100:+.2f}% · 옮긴 대수 {j['d_moved'] * 100:+.2f}% · "
              f"포화 {j['d_saturation'] * 100:+.2f}% · 거리 {j['d_km'] * 100:+.2f}% · 120분 초과 {j['over_M']} → {j['over_P']}")
        print(f"   결품이 준 쌍 {j['wins']} · 는 쌍 {j['losses']} · 부호 검정 p = {j['p']:.4f} · "
              f"달 평균으로 나아진 달 {j['months_better']}/{j['months']}")
        full = rev["rate"] >= REVERSAL_THRESHOLD
        if full:
            print(f"   판정: {'후보' if j['candidate'] else '후보 아님'}")
        else:
            print("   판정 없음(되돌림이 작아 짧게 찍기만 했다)")


def main():
    parser = argparse.ArgumentParser(description="다기간 계획: 회차 사이의 영향 (사전 등록 2026-11-21)")
    parser.add_argument("--periods", default=",".join(PERIODS))
    parser.add_argument("--seeds", default=",".join(str(s) for s in SEEDS))
    parser.add_argument("--day-type", default="weekday", choices=["weekday", "holiday"])
    parser.add_argument("--run-label", default="", help="재고 스냅샷(기본: 정본)")
    parser.add_argument("--out", default="", help="회차별 기록 CSV(사례마다 덮어써 중간 결과를 남긴다)")
    parser.add_argument("--summary-only", default="", help="이미 만든 CSV로 판정만 다시 찍는다")
    args, _ = parser.parse_known_args()

    if args.summary_only:
        frame = pd.read_csv(args.summary_only)
        for col in ("picks", "drops", "drop_targets"):
            frame[col] = frame[col].map(ast.literal_eval)
        summarize(frame, reversal(frame[frame["policy"] == "M"].to_dict("records")))
        return

    args.day_type = normalize_day_type(args.day_type)
    periods = [p.strip() for p in args.periods.split(",") if p.strip()]
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]

    with contextlib.redirect_stdout(io.StringIO()):
        bc = load_baseline()
    step1 = bc.load_step1()
    bc.align_day_type(args.day_type, bc.ilp_mod, bc.vrp_mod, bc.kpi_mod, step1)
    solver = bc.ilp_mod.build_solver()
    label = resolve_run_label(args.run_label or None)

    inputs = {}
    records = []
    checked = {}

    def save():
        if args.out:
            pd.DataFrame(records).to_csv(args.out, index=False, encoding="utf-8")

    def run(period, seed, policy):
        if period not in inputs:
            net, st_info, _warmup = bc.load_inputs(period, label, args.day_type, 0, "")
            inputs[period] = (net, st_info)
        net, st_info = inputs[period]
        t0 = time.time()
        rec = run_day(bc, step1, solver, net, st_info, seed, policy, period, checked)
        records.extend(rec)
        save()
        tot_stock = sum(r["stockout_h"] for r in rec) / (rec[0]["stations"] * rec[0]["days"])
        print(f"  [{period} 씨앗 {seed} {policy}] 결품 {tot_stock:.4f}h · 옮긴 대수 {sum(r['moved'] for r in rec)} · "
              f"{time.time() - t0:.0f}초", flush=True)

    # 1단계: 현행으로 굴리고 되돌림을 센다
    for period in periods:
        for seed in seeds:
            run(period, seed, "M")
    rev = reversal([r for r in records if r["policy"] == "M"])
    print(f"\n[신호 검사] 되돌림률 {rev['rate'] * 100:.2f}% (문턱 {REVERSAL_THRESHOLD * 100:.0f}%)", flush=True)

    # 2단계: 되돌림이 문턱 이상이면 모두, 아니면 짧게
    if rev["rate"] >= REVERSAL_THRESHOLD:
        scope = [(p, s) for p in periods for s in seeds]
    else:
        scope = [(p, s) for p, s in SHORT_RUN if p in periods and s in seeds]
        print("  되돌림이 작다 → L1 · L2는 짧게 찍기만 한다", flush=True)
    for period, seed in scope:
        for policy in ("L1", "L2"):
            run(period, seed, policy)

    summarize(pd.DataFrame(records), rev)
    if args.out:
        print(f"\n결과를 저장했습니다: {args.out}")


if __name__ == "__main__":
    main()
