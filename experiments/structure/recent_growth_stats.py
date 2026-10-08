"""성장 배율을 '1년 전 대비가 보이는 가장 최근 달들'의 대여 건수로 잡으면 작년 같은 철이 나아지나 (EXPERIMENTS 52장).

사용자 제안(2026-10-07) — *"1년 전 대비 x를 알 수 있는 가장 최근 달인 [2026년 3월, 2026년 2월~3월 의 평균(즉, 2.04와
1.77의 평균), 2026년 1월~3월]로 ~배가 늘었는지를 계산해서 실험해보자."*

50장은 Y = 작년 같은 달 ±1 × G, G = season_ratio(L−12 → L)(회차마다 순수요로 낸 배율 — 26년 3월 기준 평일 ×1.06~1.76)로 쟀고
판정은 *"이번 가을 예측만 낫다(보류)"* 였다. 50장 뒤에 본 월별 대여 건수가 약점 하나를 보였다 — **성장이 달마다 다르다**
(1년 전 대비 가을 ×1.2 안팎, 겨울 · 봄 ×1.5~2.0). 이 장은 **G의 정의만** 바꾼다. 작년 같은 달 ±1의 모양 · 계산 · 하네스는 50장과 같다.

후보 — 넷 모두 대여소별 날짜 순수요의 평균 · 표준편차(운영 함수 build_stats와 같은 식)
  L    현행 — 가장 최근 달 하나(p = T − 공개 지연). 배율 없음
  G1   작년 같은 달 ±1 × r(p)
  G2   작년 같은 달 ±1 × (r(p−1) + r(p)) / 2
  G3   작년 같은 달 ±1 × (r(p−2) + r(p−1) + r(p)) / 3
  Y50  (찍기만) 50장의 Y 그대로 — 새 배율이 50장의 배율보다 나은지
  r(m) = m달의 하루 평균 대여 건수 ÷ 1년 전 같은 달의 하루 평균 대여 건수. **그 요일 구분(평일 · 휴일)에서 자료가 있는 날**로
         나눈다 — 25년 2월 26~28일 · 3월 1~3일은 시스템 장애로 비어, 달력 일수로 나누면 배율이 부푼다(26년 2월 ×2.04 → 평일
         ×1.98, 26년 3월 ×1.77 → 평일 ×1.64). 배율은 도시 전체 하나 — 회차 · 대여소 구분이 없다(사용자 안 그대로).
         작년 자료에 없는 대여소(신설)는 L 값을 그대로 쓴다. 배율을 낼 달이 하나라도 없으면(24년 7월 이전 · 25년 12월) 그 칸은 그 후보에서 빠진다

사전 등록 (EXPERIMENTS 52장 — 결과 전 커밋. 바꾸지 않는다)
  1부 백테스트 : 50장 1부와 같은 칸 · 같은 자(history_window.measure, |실제 순수요| > 2)
  2부 관측     : 50장 2부와 같은 관측 · 회차 · 참조 계획(--until 2026-10-06 00:00), p = 26년 03월
  판정 (평일만, 후보 G1 · G2 · G3 각각 L과)
    ① 1부 — 그 후보가 잴 수 있는 지연 5개월 이상 칸에서 MAE 평균 < L, 부호 검정 단측 p < 0.05/3. 칸이 12개 미만이면 **판정 불가**(통과 아님)
    ② 2부 ⓐ — 합친 MAE < L, 회차 부호 검정 단측 p < 0.05/3
    ③ 2부 ⓑ — 3 · 5시간 모두 (배송 적중 − 같은 수 B1)이 L보다 1%p 넘게 낮지 않고, 5시간 수거 무해가 L보다 1%p 넘게 낮지 않다
    이름은 50장과 같다 — ①②③ 채택 후보(기본값은 사용자 결정) · ①② 예측만 낫다 · ①만 백테스트에서만(보류) · ②③ 이번 가을에만(보류)
    · ②만 이번 가을 예측만(보류) · ①② 둘 다 아님 기각. 후보가 셋이라 부호 검정 문턱을 셋으로 나눈다(본페로니)
  찍기만 : 지연별 표 · 커버리지 · 여유분 · 치우침 · Y50 · 휴일 · 배율 값

실행:
    python experiments/structure/recent_growth_stats.py            # 판정
    python experiments/structure/recent_growth_stats.py --sanity   # 결과 없이 입력(배율 · 칸)만 점검
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import project_config  # noqa: E402,F401  — 콘솔 인코딩을 먼저 맞춘다(— · 이모지)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import db  # noqa: E402
import history_window as hw  # noqa: E402
import plan_validity as pv  # noqa: E402
import same_season_stats as ss  # noqa: E402
import stockout_forecast as sf  # noqa: E402
from project_config import (DEFAULT_PERIOD, REBAL_MIN_QTY, TARGET_Z, TICKS_PER_HOUR,  # noqa: E402
                            duration_hours, duration_start_hour, holiday_mask)

# ── 사전 등록 (EXPERIMENTS 52장)
LAGS = ss.LAGS
MAIN_LAG = ss.MAIN_LAG
SPREAD = ss.SPREAD
VARIANTS = {"G1": 1, "G2": 2, "G3": 3}             # 이름 → 평균 낼 최근 달 수
SIGN_P = 0.05 / len(VARIANTS)                      # 본페로니
MIN_CELLS = 12                                     # ① 판정 칸이 이보다 적으면 판정 불가
PLAN_TOL = ss.PLAN_TOL
UNTIL = ss.UNTIL
CANDS = ("L", "G1", "G2", "G3", "Y50")
NAMES = {"L": "L 가장 최근 달(현행)", "G1": "G1 작년 같은 철 × 최근 1달 배율",
         "G2": "G2 작년 같은 철 × 최근 2달 배율", "G3": "G3 작년 같은 철 × 최근 3달 배율",
         "Y50": "Y50 50장의 Y(회차별 순수요 배율)"}


def daily_rates() -> dict:
    """{(요일 구분, 기간): 자료가 있는 날의 하루 평균 대여 건수}. 날짜는 대여일시로, 요일 구분은 holiday_mask로 가른다."""
    with db.session() as conn:
        frame = pd.read_sql("SELECT period, substr(rent_at, 1, 10) AS day, COUNT(*) AS n"
                            " FROM rental_history GROUP BY period, day", conn)
    frame = frame[frame["day"].str.match(r"^\d{4}-\d{2}-\d{2}$", na=False)]
    frame["day_type"] = np.where(holiday_mask(pd.to_datetime(frame["day"])), "holiday", "weekday")
    grouped = frame.groupby(["day_type", "period"]).agg(days=("day", "nunique"), n=("n", "sum"))
    return {key: row.n / row.days for key, row in grouped.iterrows() if row.days}


def growth(rates: dict, day_type: str, latest: int, months: int):
    """최근 `months`달(…, latest)의 1년 전 대비 배율 평균. 한 달이라도 없으면 None."""
    values = []
    for m in range(latest - months + 1, latest + 1):
        now, ago = rates.get((day_type, ss.label_of(m))), rates.get((day_type, ss.label_of(m - 12)))
        if not now or not ago:
            return None
        values.append(now / ago)
    return float(np.mean(values))


def build(latest: pd.DataFrame, shape: pd.DataFrame, gains: dict, g50: float, s50: float) -> dict:
    """후보별 (mu, sigma). 배율을 못 낸 후보는 빠진다. 대여소는 L의 것으로 맞춘다."""
    base, _ = ss.candidates(latest, shape, g50, s50)
    out = {"L": base["L"], "Y50": base["Y"]}
    for name, g in gains.items():
        if g is not None:
            out[name] = ss.candidates(latest, shape, g, 1.0)[0]["Y"]
    return out


def judge_cells(wide: pd.DataFrame, name: str):
    """(칸 수, 후보 평균, L 평균, 승, 패, p) — 그 후보가 잴 수 있었던 판정 칸에서."""
    part = wide[[name, "L"]].dropna()
    part = part[part.index.get_level_values("지연") >= MAIN_LAG]
    w, l = int((part[name] < part["L"]).sum()), int((part[name] > part["L"]).sum())
    return len(part), part[name].mean(), part["L"].mean(), w, l, pv.sign_test(w, l)


# ── 1부 — 백테스트
def backtest(day_types, sanity: bool, rates: dict) -> dict:
    verdict = {}
    for dt in day_types:
        periods = sorted(project_config.available_periods(), key=ss.abs_month)
        nets = {p: ss.load_period(p, dt) for p in periods}
        nets = {p: f for p, f in nets.items() if not f.empty}
        have = set(nets)
        rows, cells = [], {}
        for target in periods:
            tm = ss.abs_month(target)
            shape_months = [ss.label_of(tm - 12 + d) for d in range(-SPREAD, SPREAD + 1)]
            if not all(m in have for m in shape_months):
                continue
            for k in LAGS:
                latest_p, year_ago = ss.label_of(tm - k), ss.label_of(tm - k - 12)
                if latest_p not in have or year_ago not in have:
                    continue
                gains = {n: growth(rates, dt, tm - k, months) for n, months in VARIANTS.items()}
                cells[(target, k)] = gains
                if sanity:
                    continue
                for dur in pv.DURATIONS:
                    test = ss.daily(nets[target], dur)
                    lat_d, old_d = ss.daily(nets[latest_p], dur), ss.daily(nets[year_ago], dur)
                    shp_d = ss.daily([nets[m] for m in shape_months], dur)
                    latest, shape = hw.fit([lat_d]), hw.fit([shp_d])
                    cands = build(latest, shape, gains, ss.ratio(old_d, lat_d), ss.ratio(old_d, shp_d))
                    for name, stats in cands.items():
                        got = hw.measure(stats, test, TARGET_Z)
                        if got:
                            rows.append(dict(요일=dt, 대상=target, 지연=k, 회차=dur, 후보=name, **got))
        tag = "" if dt == "weekday" else " (찍기만)"
        print(f"\n## 1부 백테스트 — {pv.DAY_TYPES[dt]}{tag}")
        for (target, k), gains in sorted(cells.items(), key=lambda kv: (kv[0][1], ss.abs_month(kv[0][0]))):
            text = " · ".join(f"{n} ×{g:.2f}" if g is not None else f"{n} 없음" for n, g in gains.items())
            print(f"  대상 {target} · 지연 {k}개월(최근 달 {ss.label_of(ss.abs_month(target) - k)}): {text}")
        if sanity:
            for name in VARIANTS:
                n = sum(1 for (t, k), g in cells.items() if k >= MAIN_LAG and g[name] is not None) * len(pv.DURATIONS)
                print(f"  {name} 판정 칸(지연 {MAIN_LAG}개월 이상 × 회차): {n}" + ("" if n >= MIN_CELLS else " — 판정 불가"))
            continue
        frame = pd.DataFrame(rows)
        wide = frame.pivot_table(index=["대상", "지연", "회차"], columns="후보", values="mae")
        print(f"\n  지연별 MAE 평균(대 · 칸마다 같은 가중) — 후보마다 잰 칸이 다르다(괄호 = 칸 수)")
        print(f"  {'지연':>4} " + " ".join(f"{c:>13}" for c in CANDS))
        for k, part in wide.groupby(level="지연"):
            print(f"  {k:>4} " + " ".join(
                f"{part[c].mean():7.3f} ({part[c].notna().sum():>3})" if c in part and part[c].notna().any()
                else f"{'—':>13}" for c in CANDS))
        print(f"\n  [판정 칸] 지연 {MAIN_LAG}개월 이상 — 후보마다 L과 같은 칸에서")
        for name in VARIANTS:
            n, mine, base, w, l, p = judge_cells(wide, name) if name in wide else (0, np.nan, np.nan, 0, 0, 1.0)
            ok = n >= MIN_CELLS and mine < base and p < SIGN_P
            state = "판정 불가(칸 부족)" if n < MIN_CELLS else ("✅" if ok else "❌")
            print(f"    {name}: {n}칸 · MAE {mine:.3f} 대 L {base:.3f} ({(mine / base - 1) * 100:+.1f}%) · "
                  f"{w}승 {l}패 · 단측 p = {p:.4f} → ① {state}")
            if dt == "weekday":
                verdict[name] = bool(ok)
        late = frame[frame["지연"] >= MAIN_LAG]
        for label, col in (("커버리지(z = 1.99)", "coverage"), ("여유분 z·sigma(대)", "buffer"), ("치우침 실제 − mu(대)", "bias")):
            mean = late.groupby("후보")[col].mean()
            print(f"  {label}: " + " · ".join(f"{c} {mean.get(c, float('nan')):.3f}" for c in CANDS))
    return verdict


# ── 2부 — 관측
def observed(sanity: bool, until: str, rates: dict):
    with db.session() as conn:
        refs = {dt: pv.load_refs(conn, dt, before=until) for dt in pv.DAY_TYPES}   # 47 · 50장과 같은 참조 계획
        raw = sf.load_grid(conn)
    raw = raw[raw["ts"] < pd.Timestamp(until)]
    print(f"\n## 2부 관측 — 자료 {raw['ts'].min()} ~ {raw['ts'].max()} (--until {until})")
    stock = raw.pivot_table(index="ts", columns="station_id", values="stock")
    grid = stock.reindex(pd.date_range(stock.index.min().floor("D"), stock.index.max().ceil("D"),
                                       freq=pv.TICK, inclusive="left"))
    days = sf.dense_days(raw)
    hol = dict(zip(days, holiday_mask(pd.Series(pd.to_datetime(days)))))
    months = sorted({ss.abs_month(project_config.period_label(d)) for d in days})
    latest_p = DEFAULT_PERIOD
    lm = ss.abs_month(latest_p)
    year_ago = ss.label_of(lm - 12)
    need = {latest_p, year_ago} | {ss.label_of(m - 12 + d) for m in months for d in range(-SPREAD, SPREAD + 1)}

    plans, info = {}, []
    for dt in pv.DAY_TYPES:
        gains = {n: growth(rates, dt, lm, months_n) for n, months_n in VARIANTS.items()}
        info.append(f"  {pv.DAY_TYPES[dt]} 배율(p = {latest_p}): "
                    + " · ".join(f"{n} ×{g:.3f}" if g else f"{n} 없음" for n, g in gains.items()))
        nets = {p: ss.load_period(p, dt) for p in sorted(need, key=ss.abs_month)}
        for dur, (frame, lab) in sorted(refs[dt].items()):
            base = pv.current_stats(frame, nets[latest_p].rename(columns={"date": "날짜"}), dur)   # 47장 정정판과 같은 L
            lat_d, old_d = ss.daily(nets[latest_p], dur), ss.daily(nets[year_ago], dur)
            g50 = ss.ratio(old_d, lat_d)
            for m in months:
                shape_months = [ss.label_of(m - 12 + d) for d in range(-SPREAD, SPREAD + 1)]
                shape_net = pd.concat([nets[x] for x in shape_months], ignore_index=True).rename(columns={"date": "날짜"})
                shape = pv.current_stats(frame, shape_net, dur)
                s50 = ss.ratio(old_d, ss.daily([nets[x] for x in shape_months], dur))
                cands = build(base[["mu", "sigma"]], shape[["mu", "sigma"]], gains, g50, s50)
                for name, ms in cands.items():
                    plan = base.copy()
                    plan[["mu", "sigma"]] = ms.to_numpy()
                    plans[(dt, dur, m, name)] = plan
            info.append(f"    {dur}: 50장 배율 ×{g50:.2f} · 참조 계획 '{lab}'")
    print("\n".join(info))

    index = {ts: i for i, ts in enumerate(grid.index)}
    rngs = {c: np.random.default_rng(pv.SEED) for c in CANDS}    # 후보마다 47장과 같은 순서로 쓴다 — L은 인용판과 같은 B1
    rows, fc = [], []
    count = {dt: 0 for dt in pv.DAY_TYPES}
    for day in days:
        dt = "holiday" if hol[day] else "weekday"
        for dur in pv.DURATIONS:
            if dur not in refs[dt]:
                continue
            t = pd.Timestamp(day) + pd.Timedelta(hours=duration_start_hour(dur))
            if t not in index:
                continue
            i = index[t]
            s0 = grid.iloc[i].dropna()
            if len(s0) < 0.5 * grid.shape[1]:
                continue
            count[dt] += 1
            if sanity:
                continue
            m = ss.abs_month(project_config.period_label(t))
            key = f"{day} {dur}"
            names = [c for c in CANDS if (dt, dur, m, c) in plans]
            n = len(duration_hours(dur)) * TICKS_PER_HOUR
            win = grid.iloc[i:i + n + 1]
            if len(win) == n + 1:
                ends = win.iloc[[0, -1]]
                okay = (win.notna().mean() >= pv.MIN_OBSERVED) & ends.notna().all()
                okay &= ~(win.diff().abs() >= pv.JUMP).any()
                o = (ends.iloc[0] - ends.iloc[-1])[okay]
                o = o[o.abs() > REBAL_MIN_QTY]
                mus = {c: plans[(dt, dur, m, c)]["mu"] for c in names}
                common = o.index
                for c in names:
                    common = common.intersection(mus[c].dropna().index)
                if len(common):
                    rec = {"요일": dt, "회차": dur, "회차키": key, "n": len(common)}
                    for c in names:
                        err = o[common] - mus[c][common]
                        rec[f"{c}_abs"] = float(err.abs().sum())
                        rec[f"{c}_bias"] = float(err.sum())
                    fc.append(rec)
            for c in names:
                plan = plans[(dt, dur, m, c)]
                rebal = pv.replan(plan, s0)
                for h in pv.HORIZONS:
                    ws = pv.window_stats(grid, i, h)
                    if ws is None:
                        continue
                    row = pv.score_instance(rebal, s0, plan["parking_lot"], ws, rngs[c], plan["mu"])
                    row.update({"요일": dt, "회차": dur, "지평": h, "회차키": key, "후보": c})
                    rows.append(row)
    print(f"  계획 회차 — 평일 {count['weekday']}개 · 휴일 {count['holiday']}개 (47 · 50장은 평일 70)")
    if sanity:
        return None, None
    return pd.DataFrame(fc), pd.DataFrame(rows)


def report_observed(fc: pd.DataFrame, rows: pd.DataFrame, dt: str) -> dict:
    verdict = dt == "weekday"
    f = fc[fc["요일"] == dt]
    r = rows[rows["요일"] == dt]
    print(f"\n## 2부 — {pv.DAY_TYPES[dt]}, 회차 {r['회차키'].nunique()}개" + ("" if verdict else " (찍기만)"))
    n = f["n"].sum()
    out = {}
    if not n or r.empty:
        print("  잴 회차가 없다")
        return {name: (False, False) for name in VARIANTS}
    names = [c for c in CANDS if f"{c}_abs" in f]
    print(f"\n### ⓐ 예측 — 회차 창의 관측 재고 변화 대 mu (대여소 × 회차 {int(n):,}건, |변화| > {REBAL_MIN_QTY})")
    for c in names:
        print(f"  {NAMES[c]}: MAE {f[f'{c}_abs'].sum() / n:.3f}대 · 치우침(관측 − mu) {f[f'{c}_bias'].sum() / n:+.3f}대")
    for dur, g in f.groupby("회차"):
        k = g["n"].sum()
        print(f"    {dur}: " + " · ".join(f"{c} {g[f'{c}_abs'].sum() / k:.3f}" for c in names) + f" ({int(k):,}건)")
    per = {c: f[f"{c}_abs"] / f["n"] for c in names}

    print(f"\n### ⓑ 계획 — 47장 하네스, 후보마다 자기 계획")
    lifts, safes = {}, {}
    for h in pv.HORIZONS:
        print(f"  {h // TICKS_PER_HOUR}시간 뒤")
        for c in names:
            g = r[(r["지평"] == h) & (r["후보"] == c)]
            d = g[g["배송수"] > 0]
            pk = g[g["수거수"] > 0]
            hit = pv.rate(d["배송적중"].sum(), d["배송수"].sum())
            b1 = pv.rate(d["B1배송적중"].sum(), d["배송수"].sum())
            safe = pv.rate(pk["수거무해"].sum(), pk["수거수"].sum())
            lifts[(h, c)], safes[(h, c)] = hit - b1, safe
            print(f"    {NAMES[c]}: 배송 대여소 {int(d['배송수'].sum()):,} · 적중 {hit:.1%} · 같은 수 B1 {b1:.1%} · "
                  f"차 {(hit - b1) * 100:+.1f}%p · 수거 무해 {safe:.1%} ({int(pk['수거수'].sum()):,}곳)")
    for name in VARIANTS:
        if name not in names:
            out[name] = (False, False)
            continue
        w, l = int((per[name] < per["L"]).sum()), int((per[name] > per["L"]).sum())
        p = pv.sign_test(w, l)
        ok2 = bool(f[f"{name}_abs"].sum() < f["L_abs"].sum() and p < SIGN_P)
        ok3 = (all(lifts[(h, name)] >= lifts[(h, "L")] - PLAN_TOL for h in pv.MAIN_HORIZONS)
               and safes[(30, name)] >= safes[(30, "L")] - PLAN_TOL)
        print(f"  {name}: ⓐ {w}승 {l}패 · 단측 p = {p:.4f}" + (f" → ② {'✅' if ok2 else '❌'} · ③ {'✅' if ok3 else '❌'}"
                                                            if verdict else ""))
        out[name] = (ok2, ok3)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sanity", action="store_true", help="결과 없이 입력만 점검한다")
    parser.add_argument("--part", choices=("all", "backtest", "observed"), default="all")
    parser.add_argument("--until", default=UNTIL, help=f"2부 관측의 끝(등록값 {UNTIL} — 바꾸면 판정이 아니다)")
    args = parser.parse_args(argv)

    rates = daily_rates()
    print("## 하루 평균 대여 건수(자료가 있는 날) — 1년 전 대비")
    for dt in pv.DAY_TYPES:
        line = []
        for m in sorted({p for d, p in rates if d == dt}, key=ss.abs_month):
            ago = rates.get((dt, ss.label_of(ss.abs_month(m) - 12)))
            if ago:
                line.append(f"{m} ×{rates[(dt, m)] / ago:.2f}")
        print(f"  {pv.DAY_TYPES[dt]}: " + " · ".join(line))

    v1, v23 = {}, {}
    if args.part in ("all", "backtest"):
        v1 = backtest(tuple(pv.DAY_TYPES), args.sanity, rates)
    if args.part in ("all", "observed"):
        fc, rows = observed(args.sanity, args.until, rates)
        if not args.sanity:
            v23 = report_observed(fc, rows, "weekday")
            if (fc["요일"] == "holiday").any():
                report_observed(fc, rows, "holiday")
    if args.sanity:
        print("\n--sanity: 오차 · 점수는 계산하지 않았다")
        return 0
    if args.part == "all":
        print("\n## 판정 (사전 등록, 평일)")
        for name in VARIANTS:
            a = v1.get(name, False)
            b, c = v23.get(name, (False, False))
            print(f"  {NAMES[name]}: ① {'✅' if a else '❌'} · ② {'✅' if b else '❌'} · ③ {'✅' if c else '❌'}"
                  f" → {ss.name_verdict(a, b, c)}")
    return 0


if __name__ == "__main__":
    project_config.exit_if_help(__doc__)
    raise SystemExit(main())
