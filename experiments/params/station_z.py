"""대여소별 z: 대여율이 큰 곳에 여유를 더 주면(안전재고 총량은 그대로) 잃는 대여가 줄어드나 (사전 등록 2026-11-21).

왜: 목표 재고 mu + z · sigma의 z는 모든 대여소에 같은 1.99다(발표_기술선택.md 2절의 대가). 그런데 결품 한 시간의 손실은
대여소마다 다르다. 한 시간에 열 명이 빌리러 오는 곳과 한 명이 오는 곳의 결품은 같은 1시간이 아니다. 같은 안전재고
총량(Σ z_i σ_i)을 대여율 쪽으로 옮기면 잃는 대여가 줄어드는지 잰다. 균일 z 격자(18장)는 중립 모집단 결품을 0.075%밖에
못 움직였다. 이 실험은 그 '총량'이 아니라 '배분'을 바꾼다.

갈래: U 현행(z = 1.99) · A05 · A1. mu ≥ 0 대여소에서 z_i = c · 1.99 · (λ_i / λ̄)^α, c는 Σ z_i σ_i = 1.99 Σ σ_i가 되게.
λ_i = 학습 달 평일 그 회차 창의 하루 평균 대여 건수(총대여).

표본 밖 채점: 학습 달 m − 1로 세운 계획(baseline_compare 함수 그대로, 정본 스냅샷)을 다음 달 m의 평일 순수요로
시뮬레이션한다. 원고 6장 반복(repeat_eval)은 같은 달로 세우고 채점하는 표본 안 구조라 주 채점으로 쓰지 않는다(찍기만).
순수요는 대여이력에서 지금 코드(raw_to_net.net_from_rentals, 1.26.409 정의)로 달마다 다시 낸다. DB net_demand는 26년 03월만
새 정의라 쓰지 않는다.

주 지표 WS(대여 가중 결품) = Σ_{i,d} 결품시간_{i,d} · w_i / (D · Σ_i w_i), w_i = 평가 달 평일 그 회차 하루 평균 대여 건수.

판정(사전 등록, scratchpad 노트 2_station_z.md와 같다): α마다, 회차마다 (짝 × 씨앗) 평균 WS 상대 변화 ≤ −3%이고 단측
부호 검정 p < 0.05면 그 회차 통과, 세 회차 중 둘 이상 → 주 지표 통과. 지킴(세 회차 모두): 옮겨 끝낸 대수 비 0.95 ~ 1.05,
보통 결품 · 포화 상대 변화 ≤ +2%. 주 지표 + 지킴 → 채택 후보 · 주 지표만 → 대가가 남는다 · 아님 → 효과 없음.

이 스크립트는 DB에 쓰지 않는다(읽기만).

실행:
    python experiments/params/station_z.py --sanity    # 입력 점검: U 후보가 baseline_compare와 같은지 · 계획 한 번 걸린 시간
    python experiments/params/station_z.py --signal    # 멈춤 규칙(26년 03월 학습, 씨앗 42)
    python experiments/params/station_z.py             # 판정(17쌍 × 씨앗 3 × 세 회차)
    python experiments/params/station_z.py --every 2   # 짝을 하나 걸러(등록의 시간 규칙)
"""
import argparse
import contextlib
import io
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import project_config  # noqa: E402  (콘솔 인코딩을 먼저 맞춘다)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.stats import binomtest  # noqa: E402

import db  # noqa: E402
from experiments._shared import default_run_label, load_baseline  # noqa: E402
from pipeline.step0_collect import calculate_target_qty as target_mod  # noqa: E402
from pipeline.step0_collect import raw_to_net  # noqa: E402
from project_config import TARGET_Z, duration_hours, select_day_type  # noqa: E402

with contextlib.redirect_stdout(io.StringIO()):
    bc = load_baseline()

# 사전 등록 값(2026-11-21 13:24). 결과를 보고 바꾸지 않는다.
ARMS = {"U": None, "A05": 0.5, "A1": 1.0}
SEEDS = (42, 7, 13)
DURATIONS = ("_05_10", "_10_15", "_15_20")
DAY_TYPE = "weekday"
WS_GAIN = -0.03          # 주 지표: 평균 상대 변화가 이 값 이하
SIGN_P = 0.05
MOVED_BAND = (0.95, 1.05)
HARM_MAX = 0.02          # 보통 결품 · 포화 상대 변화 상한
SIGNAL_MIN = 0.03        # 멈춤 규칙
SIGNAL_PERIOD = "26년 03월"
RENT_COLUMNS = ["대여일시", "대여_대여소ID", "반납일시", "반납_대여소ID"]
OUT_CSV = Path(__file__).resolve().parent / "station_z_261121.csv"


def quiet(func, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return func(*args, **kwargs)


def month_key(period: str) -> int:
    y, m = re.match(r"(\d+)년\s*(\d+)월", period).groups()
    return int(y) * 12 + int(m)


def month_pairs(conn) -> list:
    """대여이력에 있는 달 가운데 연속한 (m − 1, m) 짝."""
    periods = [r[0] for r in conn.execute("SELECT DISTINCT period FROM rental_history")]
    periods.sort(key=month_key)
    return [(a, b) for a, b in zip(periods, periods[1:]) if month_key(b) == month_key(a) + 1]


def month_tables(period: str) -> tuple:
    """(평일 순수요, 회차 → 대여소별 하루 평균 대여 건수). 순수요는 지금 코드로 다시 낸다."""
    data, _src = db.read_rental_source(period, columns=RENT_COLUMNS)
    net = quiet(raw_to_net.net_from_rentals, data)          # 대여일시를 제자리에서 datetime으로 바꾼다
    net = select_day_type(net, "날짜", DAY_TYPE)
    days = net["날짜"].nunique()
    rent = data[["대여일시", "대여_대여소ID"]].copy()
    rent["날짜"] = rent["대여일시"].dt.date
    rent = select_day_type(rent, "날짜", DAY_TYPE)
    hour = rent["대여일시"].dt.hour
    lam = {}
    for dur in DURATIONS:
        part = rent[hour.isin(duration_hours(dur))]
        lam[dur] = part.groupby("대여_대여소ID").size().astype(float) / max(days, 1)
    return net, lam, days


def station_z(stats: pd.DataFrame, lam: pd.Series, alpha: float) -> tuple:
    """mu ≥ 0 대여소의 z_i = c · 1.99 · (λ_i / λ̄)^α, Σ z_i σ_i = 1.99 Σ σ_i. (z Series(stats 색인), c, 정보)"""
    pos = stats["mu"] >= 0
    lam_i = stats["station_id"].map(lam).fillna(0.0).astype(float)
    lbar = lam_i[pos].mean()
    raw = (lam_i / lbar) ** alpha if lbar > 0 else pd.Series(1.0, index=stats.index)
    sig = stats["sigma"].astype(float)
    denom = float((raw[pos] * sig[pos]).sum())
    c = float(sig[pos].sum()) / denom if denom > 0 else 1.0
    z = pd.Series(float(TARGET_Z), index=stats.index)
    z[pos] = TARGET_Z * c * raw[pos]
    return z, c


def candidates(net, info, duration, alpha, lam, step1) -> tuple:
    """baseline_compare.build_candidates와 같은 길. z만 대여소별로 바꾼다."""
    stats, _d, _r = quiet(target_mod.build_stats, net, info[["station_id", "parking_lot", "stock"]], duration,
                          warmup_net=None, warmup_days=0, verbose=False)
    z, c = (None, 1.0) if alpha is None else station_z(stats, lam, alpha)
    rebal = quiet(target_mod.compute_rebal_qty, stats, z=z)
    buffer = io.StringIO()
    rebal.to_csv(buffer, index=False, encoding="utf-8")
    buffer.seek(0)
    cand = quiet(step1.select_top_unbalanced_st, buffer, duration, info)
    zinfo = z[stats["mu"] >= 0].describe() if z is not None else None
    return cand, c, zinfo


def simulate(net, population, delta, duration, weights) -> dict:
    """결품 · 포화 · 대여 가중 결품을 한 궤적에서. `kpi_mod._simulate_stock`(bc.simulate_pair와 같은 궤적)."""
    hours = bc.kpi_mod.duration_hours(duration)
    stations = population[["station_id", "stock", "parking_lot"]].copy()
    stations["delta"] = stations["station_id"].map(delta).fillna(0)
    merged = net.merge(stations, on="station_id", how="inner")
    out = bc.kpi_mod._simulate_stock(merged, merged["stock"] + merged["delta"], merged["parking_lot"], hours)
    days = merged["날짜"].nunique()
    ids = merged["station_id"].unique()
    count = len(ids)
    w_row = merged["station_id"].map(weights).fillna(0.0).to_numpy()
    w_sum = float(weights.reindex(ids).fillna(0.0).sum())
    den = max(count * days, 1)
    return {
        "WS": float((out["stockout"].to_numpy() * w_row).sum() / (days * w_sum)) if w_sum > 0 else float("nan"),
        "S": float(out["stockout"].sum() / den),
        "Sat": float(out["saturated"].sum() / den),
        "unmet": float(out["unmet"].sum() / den),
        "pop": count, "days": days,
    }


def delta_gap(a: pd.Series, b: pd.Series) -> float:
    """Σ|a − b| / Σ|a| (집행 증감의 차이)."""
    idx = a.index.union(b.index)
    a, b = a.reindex(idx).fillna(0), b.reindex(idx).fillna(0)
    den = a.abs().sum()
    return float((a - b).abs().sum() / den) if den else float("nan")


def setup(conn):
    step1 = bc.load_step1()
    bc.align_day_type(DAY_TYPE, bc.ilp_mod, bc.vrp_mod, bc.kpi_mod, step1)
    solver = bc.ilp_mod.build_solver()
    label = default_run_label(conn)
    info = db.load_frame(conn, "station_info", run_label=label)[bc.ST_INFO_COLUMNS].copy()
    population = info[info["parking_lot"] > 0].copy()
    return step1, solver, label, info, population


def run_signal(conn) -> int:
    step1, solver, label, info, _pop = setup(conn)
    net, lam, days = month_tables(SIGNAL_PERIOD)
    print(f"신호 검사: 학습 {SIGNAL_PERIOD} 평일 {days}일 · 스냅샷 '{label}' · 씨앗 42")
    worst = 0.0
    for dur in DURATIONS:
        plans = {}
        for arm, alpha in ARMS.items():
            cand, c, zinfo = candidates(net, info, dur, alpha, lam[dur], step1)
            _, routes = bc.plan_with_clusters(cand.copy(), step1, solver, adjust=True, seed=42)
            plans[arm] = (cand, bc.executed_delta(routes), c, zinfo)
        u_ids = set(plans["U"][0]["station_id"])
        for arm in ("A05", "A1"):
            cand, d, c, zinfo = plans[arm]
            ids = set(cand["station_id"])
            jac = len(ids & u_ids) / len(ids | u_ids) if ids | u_ids else float("nan")
            gap = delta_gap(plans["U"][1], d)
            worst = max(worst, gap)
            print(f"  {dur} {arm}: c = {c:.3f} · z_i(mu ≥ 0) 최소 {zinfo['min']:.2f} · 중앙 {zinfo['50%']:.2f} · 최대 {zinfo['max']:.2f}"
                  f" · 후보 {len(ids)}곳(U {len(u_ids)}) 자카드 {jac:.2f} · 집행 증감 차이 {gap:.1%}")
    stop = worst < SIGNAL_MIN
    print(f"→ 최대 집행 증감 차이 {worst:.1%} (문턱 {SIGNAL_MIN:.0%}): " + ("효과 없음, 멈춘다" if stop else "신호 있음, 판정 실행으로 간다"))
    return 0


def run_sanity(conn) -> int:
    step1, solver, label, info, population = setup(conn)
    pairs = month_pairs(conn)
    print(f"연속한 달 짝 {len(pairs)}개: " + " · ".join(f"{a}→{b}" for a, b in pairs))
    t0 = time.time()
    net, lam, days = month_tables(SIGNAL_PERIOD)
    t_load = time.time() - t0
    same_all = True
    t_plan = []
    for dur in DURATIONS:
        mine, _c, _z = candidates(net, info, dur, None, lam[dur], step1)
        theirs = bc.build_candidates(net, info, dur, None, pd.DataFrame(), 0, step1)
        same = mine.reset_index(drop=True).equals(theirs.reset_index(drop=True))
        same_all &= same
        t1 = time.time()
        _, routes = bc.plan_with_clusters(mine.copy(), step1, solver, adjust=True, seed=42)
        t_plan.append(time.time() - t1)
        pop = bc.stockout_population(net, population, dur)
        print(f"  {dur}: U 후보 = baseline_compare.build_candidates {'같다' if same else '다르다'} ({len(mine)}곳) · "
              f"계획 {t_plan[-1]:.1f}초 · 중립 모집단 {pop}곳 · λ 합 {lam[dur].sum():,.0f}건/일")
    per_plan = float(np.mean(t_plan))
    est = len(pairs) * (2 * t_load + len(DURATIONS) * len(SEEDS) * len(ARMS) * per_plan) / 60
    print(f"달 하나 읽기 {t_load:.0f}초 · 계획 한 번 {per_plan:.1f}초 → 17쌍 전체 어림 {est:.0f}분"
          f" (75분 넘으면 등록대로 --every 2) · U 후보 일치 {'✅' if same_all else '❌'}")
    return 0


def report(frame: pd.DataFrame) -> None:
    keys = ["pair", "seed", "duration"]
    oos = frame[frame["채점"] == "표본 밖"]
    u = oos[oos["arm"] == "U"].set_index(keys)
    b0 = oos[oos["arm"] == "B0"].set_index(keys)
    print("\n## 표본 밖(m − 1로 세우고 m으로 채점) 평균")
    print("  회차 갈래 | WS(대여 가중 결품, 시간) · S 결품 · 포화 · 못 빌린 순유출 | 옮겨 끝낸 대수 · km · 120분 넘는 군집 | n")
    for dur in DURATIONS:
        for arm in ("B0", "U", "A05", "A1"):
            x = oos[(oos["duration"] == dur) & (oos["arm"] == arm)]
            print(f"  {dur} {arm:<3} | {x['WS'].mean():.4f} · {x['S'].mean():.4f} · {x['Sat'].mean():.4f} · {x['unmet'].mean():.3f}"
                  f" | {x['bikes_done'].mean():.1f} · {x['km'].mean():.1f} · {x['over'].mean():.2f} | {len(x)}")
    verdict = {}
    print("\n## 판정 (사전 등록)")
    for arm in ("A05", "A1"):
        a = oos[oos["arm"] == arm].set_index(keys)
        passed, guards_ok = 0, True
        for dur in DURATIONS:
            aa = a.xs(dur, level="duration")
            uu = u.xs(dur, level="duration").reindex(aa.index)
            bb = b0.xs(dur, level="duration").reindex(aa.index)
            r = aa["WS"] / uu["WS"] - 1
            wins, losses = int((aa["WS"] < uu["WS"]).sum()), int((aa["WS"] > uu["WS"]).sum())
            p = binomtest(wins, wins + losses, 0.5, alternative="greater").pvalue if wins + losses else 1.0
            ok = r.mean() <= WS_GAIN and p < SIGN_P
            passed += int(ok)
            moved = (aa["bikes_done"] / uu["bikes_done"]).mean()
            ds = (aa["S"] / uu["S"] - 1).mean()
            dsat = (aa["Sat"] / uu["Sat"] - 1).mean()
            g_ok = MOVED_BAND[0] <= moved <= MOVED_BAND[1] and ds <= HARM_MAX and dsat <= HARM_MAX
            guards_ok &= g_ok
            share = ((uu["WS"] - aa["WS"]) / (bb["WS"] - uu["WS"])).median()
            print(f"  {arm} {dur}: WS {r.mean() * 100:+.2f}% (중앙 {r.median() * 100:+.2f}%) · {wins}승 {losses}패 · 단측 p = {p:.4f}"
                  f" → {'✅' if ok else '❌'} | 지킴: 옮긴 대수 {moved:.3f}배 · 결품 {ds * 100:+.2f}% · 포화 {dsat * 100:+.2f}%"
                  f" → {'✅' if g_ok else '❌'} | 현행 효과(B0 − U) 대비 몫 중앙 {share * 100:+.1f}%")
        main_ok = passed >= 2
        name = "채택 후보" if main_ok and guards_ok else "대가가 남는다(보류)" if main_ok else "효과 없음(기각)"
        verdict[arm] = name
        print(f"  → {arm}: 주 지표 {passed}/3 회차 통과 · 지킴 {'✅' if guards_ok else '❌'} → {name}")
    ins = frame[frame["채점"] == "표본 안"]
    print("\n## 찍기만: 표본 안(같은 달로 세우고 채점, 원고 6장의 자) WS 상대 변화 평균")
    ui = ins[ins["arm"] == "U"].set_index(keys)
    for arm in ("A05", "A1"):
        ai = ins[ins["arm"] == arm].set_index(keys)
        print(f"  {arm}: " + " · ".join(
            f"{dur} {((ai.xs(dur, level='duration')['WS'] / ui.xs(dur, level='duration')['WS'].reindex(ai.xs(dur, level='duration').index)) - 1).mean() * 100:+.2f}%"
            for dur in DURATIONS))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sanity", action="store_true")
    parser.add_argument("--signal", action="store_true")
    parser.add_argument("--every", type=int, default=1, help="짝을 이 간격으로 고른다(등록의 시간 규칙: 2)")
    args = parser.parse_args(argv)
    with db.session() as conn:
        if args.sanity:
            return run_sanity(conn)
        if args.signal:
            return run_signal(conn)
        step1, solver, label, info, population = setup(conn)
        pairs = month_pairs(conn)[::args.every]
        print(f"짝 {len(pairs)}개 · 씨앗 {SEEDS} · 회차 {DURATIONS} · 스냅샷 '{label}' · 중립 모집단 {len(population)}곳")
        cache, rows = {}, []
        started = time.time()
        for k, (train, test) in enumerate(pairs, 1):
            for period in (train, test):
                if period not in cache:
                    cache[period] = month_tables(period)
            for old in [p for p in cache if p not in (train, test)]:
                del cache[old]
            net_tr, lam_tr, _ = cache[train]
            net_te, lam_te, _ = cache[test]
            for dur in DURATIONS:
                cands = {arm: candidates(net_tr, info, dur, alpha, lam_tr[dur], step1)[0] for arm, alpha in ARMS.items()}
                pops = {bc.stockout_population(net_te, population, dur)}
                for seed in SEEDS:
                    for arm in ("B0", *ARMS):
                        if arm == "B0":
                            delta, rs = pd.Series(dtype=float), bc.route_stats(pd.DataFrame())
                        else:
                            _, routes = bc.plan_with_clusters(cands[arm].copy(), step1, solver, adjust=True, seed=seed)
                            delta, rs = bc.executed_delta(routes), bc.route_stats(routes)
                        for tag, net, lam in (("표본 밖", net_te, lam_te), ("표본 안", net_tr, lam_tr)):
                            sim = simulate(net, population, delta, dur, lam[dur])
                            rows.append({"pair": f"{train}→{test}", "seed": seed, "duration": dur, "arm": arm, "채점": tag,
                                         "stations": int(len(cands.get(arm, []))) if arm != "B0" else 0, **sim, **rs})
                if len(pops) != 1:
                    print(f"  [!] 분모가 갈래마다 다르다: {pops}")
            print(f"  [{k}/{len(pairs)}] {train}→{test} ({time.time() - started:.0f}초)", flush=True)
        frame = pd.DataFrame(rows)
        frame.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
        print(f"행 {len(frame):,} → {OUT_CSV} · 걸린 시간 {time.time() - started:.0f}초")
    report(frame)
    return 0


if __name__ == "__main__":
    project_config.exit_if_help(__doc__)
    raise SystemExit(main())
