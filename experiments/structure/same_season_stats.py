"""순수요를 '작년 같은 철 × 성장 배율'로 내면 지금(가장 최근 달)보다 맞나 — 공개 지연 아래에서 (EXPERIMENTS 50장).

사용자 제안(2026-10-06) — *"그저 최근 달이 아닌, 과거의 현재 계절 혹은 과거의 이번 달 근처 1~2달씩을 비율로써 참고하는
거다. 작년과 현재는 총 자전거 수도 달라졌을 테니까. 그러면 자연스럽게 기온이나 날씨 추세 등 계절성도 포함되니까 더 설득력
있는 답안이 나올 것 같다."*

왜 다시 재나 — 같은 제안을 2026-08-28에 쟀다(DEMAND_DISTRIBUTION 6-C, 원본 19개월 재측정 1.26.303: 작년 같은 달이 직전 한
달보다 MAE 15% 나쁨). 그 비교는 **직전 달이 손에 있다**(공개 지연 1개월)고 놓았다. 실제 운영은 다르다 — 대여이력은 26년
03월에서 끝나고 2026-08 이후분은 2027년 초에 올라온다(THESIS ⑤). 그래서 8~10월 계획은 **5~7개월 묵은 3월 통계**로 섰고,
계획 대상 달의 실적이 없어 계절 보정(warmup)은 한 번도 걸리지 않았다(웹 실행 로그 9건 모두 "계절 보정 건너뜀"). 물음은
*"직전 달이 있을 때"* 가 아니라 *"가장 최근 달이 몇 달 묵었을 때"* 다.

후보 — 넷 모두 같은 식(대여소별 날짜 순수요의 평균 · 표준편차 = 운영 함수 build_stats)으로 낸다
  L   현행 — 가장 최근 달 하나(공개 지연 k개월이면 T−k). 배율 없음 — 운영에서 warmup이 걸리지 않는 것과 같다
  Y   사용자 안 — 작년 같은 달 ±1(T−13 · T−12 · T−11)을 합쳐 낸 mu · sigma × 성장 배율 G.
      G = season_ratio(L−12 → L): 가장 최근 달과 **그 1년 전 같은 달**의 도시 전체 |순수요| 비. 같은 달끼리라 계절이 빠지고
      성장(자전거 · 대여소 · 이용자)만 남는다. 작년 자료에 없는 대여소(신설)는 L 값을 그대로 쓴다
  Y0  (찍기만) Y에서 G를 뺀 것 — 성장 배율의 몫
  LS  (찍기만) L × S, S = season_ratio(L−12 → 작년 같은 달 ±1): 가장 최근 달의 **대여소 모양**에 작년의 계절 변화만 곱한
      것. Y와 견주면 이득이 '수준'에서 오는지 '작년 같은 철의 대여소 모양'에서 오는지 갈린다

사전 등록 (EXPERIMENTS 50장 — 결과 전 커밋. 바꾸지 않는다)
  1부 백테스트 : 대여이력 19개월(평일 · 휴일 따로). 계획 대상 달 T × 공개 지연 k = 1~7 × 회차 4. 칸은 T−k · T−k−12 · 작년
                 같은 달 ±1이 모두 있을 때만. 오차는 6장 history_window.measure()와 같은 자 — 그 달의 (날짜 × 대여소) 중
                 |실제 순수요| > 2. 대여소는 L의 대여소로 맞춘다(후보마다 모집단이 같다)
  2부 관측     : 47장 인용판과 같은 관측 · 같은 회차(--until 2026-10-06 00:00 · 하루 30틱 이상 · 시작 시각 관측 50% 이상).
                 L = 26년 03월(47장 정정판과 같은 계획), Y = 회차 달 M의 작년 M−1 · M · M+1 × G(25년 03월 → 26년 03월)
                 ⓐ 예측 — 회차 창 처음 ~ 끝의 관측 재고 변화 O = 재고(시작) − 재고(끝)을 그 대여소의 mu와 견준다. 창(양 끝 포함)
                    80% 이상 관측 · 양 끝 관측 · 10분에 5대 이상 움직인 칸이 없는(트럭 흔적 없음) 대여소 중 |O| > 2
                 ⓑ 계획 — 47장 하네스 그대로(배송 적중 · 같은 수 B1 · 수거 무해), 후보마다 자기 계획 · 자기 B1
  판정 (평일만)
    ① 1부 — 지연 5개월 이상 칸(T · k · 회차)에서 Y의 MAE 평균 < L, 칸 부호 검정 단측 p < 0.05
    ② 2부 ⓐ — 합친 MAE Y < L, 회차 부호 검정 단측 p < 0.05
    ③ 2부 ⓑ — 3 · 5시간 모두 (배송 적중 − 같은 수 B1)이 L보다 1%p 넘게 낮지 않고, 5시간 수거 무해가 L보다 1%p 넘게 낮지 않다
    ①②③ → 채택 후보(기본값을 바꾸는 것은 사용자 결정 · 6장 복원으로 다시 잰 뒤) · ①② 그리고 ③ 아님 → 예측만 낫다(채택 안 함)
    · ①만 → 백테스트에서만 낫다(보류) · ②③ → 이번 가을에만 낫다(보류) · ②만 → 이번 가을 예측만 낫다(보류) · ①② 둘 다 아님 → 기각
  찍기만 : 지연별 표 전체(지연 1은 6-C의 재현) · 커버리지 · 여유분(z·sigma) · 치우침 · Y0 · LS · 휴일 · 배율 값 · 신설 대여소 수

알려진 한계 — 1부의 지연 5개월 이상 칸은 계획 대상 달이 26년 01 · 02 · 03월뿐이다(작년 같은 달과 성장 배율을 함께 낼 수 있는
칸이 그것뿐). 2부의 관측 O는 비거나 찬 대여소에서 잘린다(못 빌린 수요는 안 보인다) — 두 후보에 똑같이 걸린다. 공사의 실제
재배치는 트럭 흔적으로 거르지만 10분에 5대 미만으로 옮긴 것은 남는다.

실행:
    python experiments/structure/same_season_stats.py            # 판정 (1부 + 2부, 2부는 --until 2026-10-06 00:00)
    python experiments/structure/same_season_stats.py --sanity   # 결과 없이 입력만 점검
    python experiments/structure/same_season_stats.py --part backtest   # 1부만
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
import demand_model  # noqa: E402
import history_window as hw  # noqa: E402
import plan_validity as pv  # noqa: E402
import stockout_forecast as sf  # noqa: E402
from project_config import (DATA_ROOT, DEFAULT_PERIOD, REBAL_MIN_QTY, TARGET_Z, TICKS_PER_HOUR,  # noqa: E402
                            duration_hours, duration_start_hour, holiday_mask, select_day_type)

# ── 사전 등록 (EXPERIMENTS 50장)
LAGS = range(1, 8)                       # 공개 지연 1~7개월
MAIN_LAG = 5                             # ① 판정 칸 = 지연 5개월 이상
SPREAD = 1                               # 작년 같은 달 ±1
SIGN_P = 0.05
PLAN_TOL = 0.01                          # ③ 계획 지표가 L보다 이만큼 넘게 낮으면 실패
UNTIL = "2026-10-06 00:00"               # 2부 — 47장 인용판과 같은 자료 끝
RATIO_DAYS = 400                         # season_ratio에 '모든 날'을 넣는다(앞 N일만 보는 warmup과 다르다)
CANDS = ("L", "Y", "Y0", "LS")
NAMES = {"L": "L 가장 최근 달(현행)", "Y": "Y 작년 같은 철 × 성장", "Y0": "Y0 작년 같은 철(배율 없음)",
         "LS": "LS 최근 달 × 작년 계절 변화"}


def abs_month(label: str) -> int:
    """'25년 11월' → 연 × 12 + 월. 해를 넘어도 간격이 맞는다."""
    return hw.abs_month(label)


def label_of(n: int) -> str:
    year, month = divmod(n - 1, 12)
    return f"{year:02d}년 {month + 1:02d}월"


def load_period(period: str, day_type: str) -> pd.DataFrame:
    """그 달의 날짜별 시간 순수요(평일 · 휴일 한쪽) — plan_validity.load_net과 같은 경로, 날짜 열은 'date'."""
    path = DATA_ROOT / f"pp_data/순수요/st_net_daily ({period}).csv"
    net, _ = db.read_step_output("net_demand", str(path), period=period)
    if net.empty:
        return net
    return select_day_type(net, "date", day_type)


def daily(frames, duration: str) -> pd.DataFrame:
    """(대여소, 날짜, 그 창의 순수요 합) — 여러 달을 받으면 이어 붙인다."""
    if isinstance(frames, pd.DataFrame):
        frames = [frames]
    parts = [hw.daily_window_demand(f, duration) for f in frames if not f.empty]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["station_id", "date", "demand"])


def ratio(old: pd.DataFrame, new: pd.DataFrame) -> float:
    """도시 전체 |순수요| 배율 — 운영과 같은 함수(season_ratio, |mu| > 2 대여소 · 0.5~2.0으로 자름). 못 내면 1."""
    got = demand_model.season_ratio(old, new, RATIO_DAYS)
    return 1.0 if got is None else float(got)


def candidates(latest: pd.DataFrame, shape: pd.DataFrame, g: float, s: float) -> tuple:
    """후보 넷의 (mu, sigma)와 신설 대여소 수 — 대여소는 L의 것으로 맞춘다(모집단이 같다). 작년에 없던 대여소는 L 값을 그대로 쓴다."""
    latest = latest[["mu", "sigma"]]
    raw = shape[["mu", "sigma"]].reindex(latest.index)
    missing = raw["mu"].isna()
    y0 = raw.copy()
    y0.loc[missing] = latest.loc[missing]
    y = raw * g
    y.loc[missing] = latest.loc[missing]
    return {"L": latest, "Y": y, "Y0": y0, "LS": latest * s}, int(missing.sum())


def sign_text(wins: int, losses: int) -> str:
    return f"{wins}승 {losses}패 · 단측 p = {pv.sign_test(wins, losses):.4f}"


# ── 1부 — 백테스트
def backtest(day_types, sanity: bool) -> bool:
    ok_main = None
    for dt in day_types:
        periods = sorted((p for p in project_config.available_periods()), key=abs_month)
        nets = {p: load_period(p, dt) for p in periods}
        nets = {p: f for p, f in nets.items() if not f.empty}
        have = set(nets)
        rows, cells = [], {}
        for target in periods:
            tm = abs_month(target)
            shape_months = [label_of(tm - 12 + d) for d in range(-SPREAD, SPREAD + 1)]
            if not all(m in have for m in shape_months):
                continue
            for k in LAGS:
                latest_p, year_ago = label_of(tm - k), label_of(tm - k - 12)
                if latest_p not in have or year_ago not in have or abs_month(latest_p) < abs_month(shape_months[-1]):
                    continue
                cells.setdefault(k, []).append(target)
                if sanity:
                    continue
                for dur in pv.DURATIONS:
                    test = daily(nets[target], dur)
                    lat_d = daily(nets[latest_p], dur)
                    old_d = daily(nets[year_ago], dur)
                    shp_d = daily([nets[m] for m in shape_months], dur)
                    latest = hw.fit([lat_d])
                    shape = hw.fit([shp_d])
                    grow, season = ratio(old_d, lat_d), ratio(old_d, shp_d)
                    cands, missing = candidates(latest, shape, grow, season)
                    for name, stats in cands.items():
                        got = hw.measure(stats, test, TARGET_Z)
                        if got:
                            rows.append(dict(요일=dt, 대상=target, 지연=k, 회차=dur, 후보=name, G=grow, S=season,
                                             신설=missing, **got))
        print(f"\n## 1부 백테스트 — {pv.DAY_TYPES[dt]} · 기간 {len(have)}개" + ("" if dt == "weekday" else " (찍기만)"))
        for k in LAGS:
            print(f"  지연 {k}개월: 계획 대상 달 {', '.join(cells.get(k, [])) or '없음'}")
        if sanity:
            continue
        frame = pd.DataFrame(rows)
        wide = frame.pivot_table(index=["대상", "지연", "회차"], columns="후보", values="mae").dropna()
        print(f"\n  지연별 MAE 평균(대 · 칸마다 같은 가중) — 칸 = 대상 달 × 회차, 넷 모두 잰 칸만")
        print(f"  {'지연':>4} {'칸':>3} " + " ".join(f"{c:>7}" for c in CANDS) + "   Y 대 L")
        for k, part in wide.groupby(level="지연"):
            w, l = int((part["Y"] < part["L"]).sum()), int((part["Y"] > part["L"]).sum())
            print(f"  {k:>4} {len(part):>3} " + " ".join(f"{part[c].mean():7.3f}" for c in CANDS) + f"   {sign_text(w, l)}")
        main = wide[wide.index.get_level_values("지연") >= MAIN_LAG]
        w, l = int((main["Y"] < main["L"]).sum()), int((main["Y"] > main["L"]).sum())
        better = main["Y"].mean() < main["L"].mean()
        p = pv.sign_test(w, l)
        print(f"\n  [판정 칸] 지연 {MAIN_LAG}개월 이상 {len(main)}칸 — MAE Y {main['Y'].mean():.3f} · L {main['L'].mean():.3f}"
              f" ({(main['Y'].mean() / main['L'].mean() - 1) * 100:+.1f}%) · {sign_text(w, l)}")
        for name, col in (("커버리지(z = 1.99)", "coverage"), ("여유분 z·sigma(대)", "buffer"), ("치우침 실제 − mu(대)", "bias")):
            mean = frame[frame["지연"] >= MAIN_LAG].groupby("후보")[col].mean()
            print(f"  {name}: " + " · ".join(f"{c} {mean.get(c, float('nan')):.3f}" for c in CANDS))
        gs = frame[frame["후보"] == "Y"].groupby("지연")[["G", "S"]].mean()
        print("  배율 평균 — " + " · ".join(f"지연 {k}: G ×{r.G:.2f} S ×{r.S:.2f}" for k, r in gs.iterrows()))
        print(f"  신설 대여소(작년 자료에 없어 L 값을 씀) 칸 평균 {frame['신설'].mean():.0f}곳")
        if dt == "weekday":
            ok_main = bool(better and p < SIGN_P)
            print(f"  → ① {'✅' if ok_main else '❌'}")
    return ok_main


# ── 2부 — 관측
def observed(sanity: bool, until: str):
    with db.session() as conn:
        refs = {dt: pv.load_refs(conn, dt, before=until) for dt in pv.DAY_TYPES}   # 47장 인용판과 같은 참조 계획
        raw = sf.load_grid(conn)
    raw = raw[raw["ts"] < pd.Timestamp(until)]
    print(f"\n## 2부 관측 — 자료 {raw['ts'].min()} ~ {raw['ts'].max()} (--until {until})")
    stock = raw.pivot_table(index="ts", columns="station_id", values="stock")
    grid = stock.reindex(pd.date_range(stock.index.min().floor("D"), stock.index.max().ceil("D"),
                                       freq=pv.TICK, inclusive="left"))
    days = sf.dense_days(raw)
    hol = dict(zip(days, holiday_mask(pd.Series(pd.to_datetime(days)))))
    months = sorted({abs_month(project_config.period_label(d)) for d in days})
    latest_p = DEFAULT_PERIOD
    year_ago = label_of(abs_month(latest_p) - 12)
    need = {latest_p, year_ago} | {label_of(m - 12 + d) for m in months for d in range(-SPREAD, SPREAD + 1)}

    plans, info = {}, []
    for dt in pv.DAY_TYPES:
        nets = {p: load_period(p, dt) for p in sorted(need, key=abs_month)}
        for dur, (frame, lab) in sorted(refs[dt].items()):
            base = pv.current_stats(frame, nets[latest_p].rename(columns={"date": "날짜"}), dur)   # 47장 정정판과 같은 L
            lat_d, old_d = daily(nets[latest_p], dur), daily(nets[year_ago], dur)
            g = ratio(old_d, lat_d)
            for m in months:
                shape_months = [label_of(m - 12 + d) for d in range(-SPREAD, SPREAD + 1)]
                shape_net = pd.concat([nets[x] for x in shape_months], ignore_index=True).rename(columns={"date": "날짜"})
                shape = pv.current_stats(frame, shape_net, dur)                 # 같은 운영 함수 · 같은 거치대
                s = ratio(old_d, daily([nets[x] for x in shape_months], dur))
                cands, missing = candidates(base[["mu", "sigma"]], shape[["mu", "sigma"]], g, s)
                for name, ms in cands.items():
                    plan = base.copy()
                    plan[["mu", "sigma"]] = ms.to_numpy()
                    plans[(dt, dur, m, name)] = plan
                info.append(f"  {pv.DAY_TYPES[dt]} {dur} {label_of(m)}: 작년 {shape_months[0]}~{shape_months[-1]} · "
                            f"G ×{g:.2f} · S ×{s:.2f} · 신설 {missing}곳 · L '{lab}'")
    print(f"  L = {latest_p}(47장 정정판) · 성장 배율 G = {year_ago} → {latest_p}")
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
            m = abs_month(project_config.period_label(t))
            key = f"{day} {dur}"
            # ⓐ 예측 — 창 처음 ~ 끝의 관측 재고 변화
            n = len(duration_hours(dur)) * TICKS_PER_HOUR
            win = grid.iloc[i:i + n + 1]
            if len(win) == n + 1:
                ends = win.iloc[[0, -1]]
                okay = (win.notna().mean() >= pv.MIN_OBSERVED) & ends.notna().all()
                okay &= ~(win.diff().abs() >= pv.JUMP).any()
                o = (ends.iloc[0] - ends.iloc[-1])[okay]
                o = o[o.abs() > REBAL_MIN_QTY]
                mus = {c: plans[(dt, dur, m, c)]["mu"] for c in CANDS}
                common = o.index
                for c in CANDS:
                    common = common.intersection(mus[c].dropna().index)
                if len(common):
                    rec = {"요일": dt, "회차": dur, "회차키": key, "n": len(common)}
                    for c in CANDS:
                        err = o[common] - mus[c][common]
                        rec[f"{c}_abs"] = float(err.abs().sum())
                        rec[f"{c}_bias"] = float(err.sum())
                    fc.append(rec)
            # ⓑ 계획 — 47장 하네스 그대로, 후보마다 자기 계획
            for c in CANDS:
                plan = plans[(dt, dur, m, c)]
                rebal = pv.replan(plan, s0)
                for h in pv.HORIZONS:
                    ws = pv.window_stats(grid, i, h)
                    if ws is None:
                        continue
                    row = pv.score_instance(rebal, s0, plan["parking_lot"], ws, rngs[c], plan["mu"])
                    row.update({"요일": dt, "회차": dur, "지평": h, "회차키": key, "후보": c})
                    rows.append(row)
    print(f"  계획 회차 — 평일 {count['weekday']}개 · 휴일 {count['holiday']}개 (47장 인용판은 평일 70)")
    if sanity:
        return None, None
    return pd.DataFrame(fc), pd.DataFrame(rows)


def report_observed(fc: pd.DataFrame, rows: pd.DataFrame, dt: str):
    verdict = dt == "weekday"
    f = fc[fc["요일"] == dt]
    r = rows[rows["요일"] == dt]
    print(f"\n## 2부 — {pv.DAY_TYPES[dt]}, 회차 {r['회차키'].nunique()}개" + ("" if verdict else " (찍기만)"))
    n = f["n"].sum()
    if not n or r.empty:
        print("  잴 회차가 없다")
        return False, False
    print(f"\n### ⓐ 예측 — 회차 창의 관측 재고 변화 대 mu (대여소 × 회차 {int(n):,}건, |변화| > {REBAL_MIN_QTY})")
    for c in CANDS:
        print(f"  {NAMES[c]}: MAE {f[f'{c}_abs'].sum() / n:.3f}대 · 치우침(관측 − mu) {f[f'{c}_bias'].sum() / n:+.3f}대")
    per = {c: f[f"{c}_abs"] / f["n"] for c in CANDS}
    w, l = int((per["Y"] < per["L"]).sum()), int((per["Y"] > per["L"]).sum())
    better = f["Y_abs"].sum() < f["L_abs"].sum()
    p = pv.sign_test(w, l)
    print(f"  회차 부호 검정(Y < L): {sign_text(w, l)}")
    for dur, g in f.groupby("회차"):
        k = g["n"].sum()
        print(f"    {dur}: " + " · ".join(f"{c} {g[f'{c}_abs'].sum() / k:.3f}" for c in CANDS) + f" ({int(k):,}건)")
    ok2 = bool(better and p < SIGN_P)

    print(f"\n### ⓑ 계획 — 47장 하네스, 후보마다 자기 계획")
    lifts, safes = {}, {}
    for h in pv.HORIZONS:
        print(f"  {h // TICKS_PER_HOUR}시간 뒤")
        for c in CANDS:
            g = r[(r["지평"] == h) & (r["후보"] == c)]
            d = g[g["배송수"] > 0]
            pk = g[g["수거수"] > 0]
            hit = pv.rate(d["배송적중"].sum(), d["배송수"].sum())
            b1 = pv.rate(d["B1배송적중"].sum(), d["배송수"].sum())
            safe = pv.rate(pk["수거무해"].sum(), pk["수거수"].sum())
            lifts[(h, c)], safes[(h, c)] = hit - b1, safe
            print(f"    {NAMES[c]}: 배송 적중 {hit:.1%} ({int(d['배송적중'].sum()):,}/{int(d['배송수'].sum()):,}) · 같은 수 B1 {b1:.1%}"
                  f" · 차 {(hit - b1) * 100:+.1f}%p · 수거 무해 {safe:.1%} ({int(pk['수거수'].sum()):,}곳 · "
                  f"{pk['수거대수'].sum():,.0f}대) · 수거 포화 {pv.rate(pk['수거포화'].sum(), pk['수거수'].sum()):.1%}")
    ok3 = (all(lifts[(h, "Y")] >= lifts[(h, "L")] - PLAN_TOL for h in pv.MAIN_HORIZONS)
           and safes[(30, "Y")] >= safes[(30, "L")] - PLAN_TOL)
    if verdict:
        print(f"  → ② {'✅' if ok2 else '❌'} · ③ {'✅' if ok3 else '❌'}")
    return ok2, ok3


def name_verdict(v1: bool, v2: bool, v3: bool) -> str:
    if v1 and v2:
        return "채택 후보 — 기본값을 바꿀지는 사용자 결정 · 6장 복원으로 다시 잰 뒤" if v3 else \
            "예측만 낫다 — 계획 진단이 못 따라온다(채택 안 함)"
    if v1:
        return "백테스트에서만 낫다 — 이번 가을 관측이 지지하지 않는다(보류)"
    if v2:
        return "이번 가을에만 낫다 — 일반화 근거가 모자란다(보류)" if v3 else "이번 가을 예측만 낫다(보류)"
    return "기각 — 현행(가장 최근 달) 유지"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sanity", action="store_true", help="결과 없이 입력만 점검한다")
    parser.add_argument("--part", choices=("all", "backtest", "observed"), default="all")
    parser.add_argument("--until", default=UNTIL, help=f"2부 관측의 끝(등록값 {UNTIL} — 바꾸면 판정이 아니다)")
    args = parser.parse_args(argv)

    v1 = v2 = v3 = None
    if args.part in ("all", "backtest"):
        v1 = backtest(tuple(pv.DAY_TYPES), args.sanity)
    if args.part in ("all", "observed"):
        fc, rows = observed(args.sanity, args.until)
        if not args.sanity:
            v2, v3 = report_observed(fc, rows, "weekday")
            if (fc["요일"] == "holiday").any():
                report_observed(fc, rows, "holiday")
    if args.sanity:
        print("\n--sanity: 오차 · 점수는 계산하지 않았다")
        return 0
    if args.part == "all":
        print(f"\n## 판정 (사전 등록, 평일) — ① {'✅' if v1 else '❌'} · ② {'✅' if v2 else '❌'} · ③ {'✅' if v3 else '❌'}")
        print(f"  → {name_verdict(bool(v1), bool(v2), bool(v3))}")
    return 0


if __name__ == "__main__":
    project_config.exit_if_help(__doc__)
    raise SystemExit(main())
