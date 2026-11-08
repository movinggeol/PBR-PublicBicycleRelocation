"""출발 직전 재고는 계획이 본 재고와 얼마나 달랐나, 그리고 대조 뒤 계획을 고치면 나아지나 (EXPERIMENTS 63장).

왜 — 논문 4.6의 *"판정만 하고 계획을 바꾸지 않는다"* 를 풀어 '개정판 발행'(제안 + 기사 승인)을 만들지 정하기 전에, 그
전제를 잰다. 그림 4-7의 '집행 불가 32/86'은 8월 계획을 10월 재고와 대조한 시연값이라 출발 직전의 불일치율이 아니다.
계획을 세운 재고(시각 t)와 출발 시각(t + L)의 **관측 재고**를 운영 판정 함수 `webapp.orders.compare_stock()`으로 대조한다.

무엇을 묻나
  Q1 불일치가 실재하나 — 계획 줄(배송 · 수거) 가운데 출발 시각에 불가 · 부족 · 넘침인 비율, 못 채우는 대수의 비율
  Q2 고치면 나아지나 — 같은 출발 시각 이후 창(3 · 5시간)의 관측 재고로 세 안을 채점
       A 원안      : 계획 그대로(종이)
       B 부분 개정 : 불가 줄은 빼고 부족 · 넘침 줄은 집행 가능량으로 줄인다(나머지 줄은 그대로 — 종이와 가장 덜 어긋난다)
       C 전면 재계획: 출발 시각 재고로 운영 함수를 다시 푼다(찍기만 — 종이와 가장 많이 어긋난다)

사전 등록 (EXPERIMENTS 63장 — 결과 전 커밋. 바꾸지 않는다)
  자료     : 47장과 같다(하루 30틱 이상인 날 · 회차 시작 시각 t = 05 · 10 · 15 · 20시 · 참조 계획 · `--stats current`)
  계획     : t의 관측 재고 + 참조 계획의 mu · sigma · 거치대로 운영 함수 compute_rebal_qty()(47장 replan()과 같다).
             배송 = rebal > 2 · 수거 = rebal < −2, 지시량 = |rebal|
  출발     : t + L, L = 10 · 30 · 60 · 120분. 그 시각에 대여소 절반 이상이 관측된 회차만, 줄은 두 시각 모두 관측된 대여소만
  채점     : 출발 시각 이후 3시간(18틱)과 5시간(30틱). 창의 10분 칸이 80% 이상 관측된 대여소만(47장 score_instance()).
             배송 적중률 = 출발 시각에 재고가 있던 배송 줄이 창 안에 한 번이라도 0이 된 비율
             수거 무해율 = 0을 빔으로 센 정정식(최저 − q ≥ 1, 47장 1.26.347)
             집행량 = 줄마다 min(지시량, 집행 가능량)의 합(함정 13) — 줄을 빼서 비율만 올리는 것을 막으려 함께 찍는다
  판정     : 평일만. **개정판을 만들 가치가 있다** = 둘 다 통과
             (1) 불일치가 실재 — L = 30분에서 못 채우는 지시량이 전체 지시량의 10% 이상
             (2) 개정이 낫다 — L = 60분 · 3시간 창에서 B의 배송 적중률이 A보다 1%p 이상 높고 B의 수거 무해율이 A 이상이며,
                 회차 단위 부호 검정(B > A, 같은 값 제외)이 단측 p < 0.05 · B의 집행량이 A의 95% 이상(빼서 이긴 것이 아님)
             하나라도 못 넘으면 '가치 없음'이다 — 못 넘은 칸을 그대로 적고 문턱을 풀지 않는다.
  찍기만   : 휴일 · L = 10 · 30 · 120분의 같은 표 · 5시간 창 · C안 · 종이와 어긋난 줄 수(B 줄 변경 · C 줄 변경)
  알려진 한계 — 계획은 집행되지 않았으므로 관측 재고는 '손대지 않은 세상'이다(47장). 이 실험은 출발 시각의 재고가
  계획과 얼마나 다른지와, 개정안의 진단이 이후 관측과 얼마나 맞는지를 재며, 개정이 결품 시간을 줄였는지는 못 잰다.

실행:
    python experiments/structure/departure_mismatch.py --stats current --until "2026-11-08 00:00"
    python experiments/structure/departure_mismatch.py --sanity   # 입력 점검만(회차 수 · 재계산 일치)
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
import plan_validity as pv  # noqa: E402
import stockout_forecast as sf  # noqa: E402
from project_config import DURATIONS, REBAL_MIN_QTY, duration_start_hour, holiday_mask  # noqa: E402
from webapp import orders  # noqa: E402

LAGS = {10: 1, 30: 3, 60: 6, 120: 12}      # 분 → 틱
WINDOWS = {"3시간": 18, "5시간": 30}
GAP_FLOOR = 0.10                           # 판정 (1) L = 30분 못 채우는 지시량 비율 하한
DROP_GAIN = 0.01                           # 판정 (2) 배송 적중률 개선 하한
EXEC_FLOOR = 0.95                          # 판정 (2) 집행량 하한
SEED = pv.SEED


def lines_of(rebal: pd.Series, ref: pd.DataFrame, s_plan: pd.Series, ids) -> pd.DataFrame:
    """계획 줄 — compare_stock()이 읽는 모양(action · need · stock · parking_lot)."""
    r = rebal.reindex(ids)
    r = r[(r > REBAL_MIN_QTY) | (r < -REBAL_MIN_QTY)]
    return pd.DataFrame({
        "station_id": r.index, "station_name": "", "cluster": None, "seq": None,
        "action": np.where(r > 0, "drop", "pick"), "need": r.abs().astype(int).to_numpy(),
        "stock": s_plan.reindex(r.index).to_numpy(), "parking_lot": ref["parking_lot"].reindex(r.index).to_numpy(),
    })


def revise(judged: pd.DataFrame, ids) -> pd.Series:
    """B 부분 개정 — 불가 줄은 0, 부족 · 넘침 줄은 집행 가능량, 나머지는 그대로. 부호는 배송 +, 수거 −."""
    q = judged["possible"].fillna(0).astype(int).where(judged["status"] != "확인 불가", judged["need"])
    sign = np.where(judged["action"] == "drop", 1, -1)
    return pd.Series(sign * q.to_numpy(), index=judged["station_id"].to_numpy()).reindex(ids).fillna(0)


def score(rebal: pd.Series, s_dep: pd.Series, ref: pd.DataFrame, ws, rng) -> dict:
    row = pv.score_instance(rebal, s_dep, ref["parking_lot"], ws, rng, ref["mu"])
    return {k: row[k] for k in ("배송수", "배송적중", "수거수", "수거무해0", "수거대수")}


def one(ref, s_plan, s_dep, grid, i_dep, rng) -> dict:
    """회차 하나 × 출발 지연 하나. 반환: Q1 줄 · Q2 안별 점수 · 종이와 어긋난 줄."""
    ids = s_plan.index.intersection(s_dep.index).intersection(ref.index)
    rebal = pv.replan(ref.loc[ids], s_plan.loc[ids])
    planned = lines_of(rebal, ref, s_plan, rebal.index)
    live = pd.DataFrame({"station_id": s_dep.index, "stock": s_dep.to_numpy()})
    judged = orders.compare_stock(planned, live)
    st = judged["status"]
    need = int(judged["need"].sum())
    possible = int(judged["possible"].fillna(0).sum())
    out = {"줄수": len(judged), "불가": int((st == "불가").sum()), "조정": int(st.isin(["부족", "넘침"]).sum()),
           "지시량": need, "집행가능량": possible,
           "배송줄": int((judged["action"] == "drop").sum()), "배송불가": int(((st == "불가") & (judged["action"] == "drop")).sum()),
           "수거줄": int((judged["action"] == "pick").sum()), "수거불가": int(((st == "불가") & (judged["action"] == "pick")).sum())}
    plans = {"A": rebal, "B": revise(judged, rebal.index), "C": pv.replan(ref.loc[ids], s_dep.loc[ids])}
    out["B줄변경"] = int((st != "가능").sum())
    c_new = plans["C"].reindex(rebal.index.union(plans["C"].index)).fillna(0)
    c_old = rebal.reindex(c_new.index).fillna(0)
    out["C줄변경"] = int((((c_new.abs() > REBAL_MIN_QTY) | (c_old.abs() > REBAL_MIN_QTY)) & (c_new != c_old)).sum())
    for label, h in WINDOWS.items():
        ws = pv.window_stats(grid, i_dep, h)
        if ws is None:
            continue
        for name, plan in plans.items():
            sc = score(plan, s_dep, ref, ws, rng)
            for key, value in sc.items():
                out[f"{name}|{label}|{key}"] = value
    for name, plan in plans.items():            # 집행량 — 출발 시각 재고로 실제 옮길 수 있는 대수
        p = plan.reindex(ids).dropna()
        stock = s_dep.reindex(p.index)
        cap = ref["parking_lot"].reindex(p.index) * project_config.TARGET_QTY_UPPER_RATIO
        moved_pick = np.minimum((-p).clip(lower=0), stock).sum()
        moved_drop = np.minimum(p.clip(lower=0), (cap - stock).clip(lower=0)).sum()
        out[f"{name}|집행량"] = float(moved_pick + moved_drop)
    return out


def tally(g: pd.DataFrame, label: str, window: str, verdict: bool) -> dict:
    """한 지연 · 한 요일의 합친 표와 판정 재료."""
    def r(a, b):
        return float(a / b) if b else float("nan")
    gap = r(g["지시량"].sum() - g["집행가능량"].sum(), g["지시량"].sum())
    print(f"  [{label}] 회차 {len(g)}개 · 줄 {int(g['줄수'].sum()):,}개 — 불가 {r(g['불가'].sum(), g['줄수'].sum()):.1%} "
          f"(배송 {r(g['배송불가'].sum(), g['배송줄'].sum()):.1%} · 수거 {r(g['수거불가'].sum(), g['수거줄'].sum()):.1%}) · "
          f"부족 · 넘침 {r(g['조정'].sum(), g['줄수'].sum()):.1%} · 못 채우는 지시량 {gap:.1%}")
    res = {"gap": gap}
    k = f"|{window}|"
    for name in "ABC":
        dh, dn = g[f"{name}{k}배송적중"].sum(), g[f"{name}{k}배송수"].sum()
        ph, pn = g[f"{name}{k}수거무해0"].sum(), g[f"{name}{k}수거수"].sum()
        print(f"      {name} {window}: 배송 적중 {r(dh, dn):.1%} ({int(dh)}/{int(dn)}) · 수거 무해 {r(ph, pn):.1%} "
              f"({int(ph)}/{int(pn)}) · 집행량 {g[f'{name}|집행량'].sum():,.0f}대")
        res[name] = (r(dh, dn), r(ph, pn), float(g[f"{name}|집행량"].sum()))
    a = g[f"A{k}배송적중"] / g[f"A{k}배송수"].replace(0, np.nan)
    b = g[f"B{k}배송적중"] / g[f"B{k}배송수"].replace(0, np.nan)
    diff = (b - a).dropna()
    wins, losses = int((diff > 1e-12).sum()), int((diff < -1e-12).sum())
    res["p"] = pv.sign_test(wins, losses)
    print(f"      회차 부호 검정(B > A, 배송 적중): {wins}승 {losses}패 {len(diff) - wins - losses}무 · p = {res['p']:.4f}")
    print(f"      종이와 어긋난 줄 — B {r(g['B줄변경'].sum(), g['줄수'].sum()):.1%} · C {g['C줄변경'].sum():,}줄(원안 {int(g['줄수'].sum()):,}줄 대비)")
    return res


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sanity", action="store_true")
    parser.add_argument("--stats", choices=("plan", "current"), default="current")
    parser.add_argument("--until", help="이 시각 전의 관측만 쓴다")
    args = parser.parse_args(argv)

    with db.session() as conn:
        refs = {dt: pv.load_refs(conn, dt, before=args.until) for dt in pv.DAY_TYPES}
        if args.stats == "current":
            for dt in pv.DAY_TYPES:
                net = pv.load_net(dt)
                for d, (frame, lab) in list(refs[dt].items()):
                    refs[dt][d] = (pv.current_stats(frame, net, d), f"{lab} → 지금 순수요")
        raw = sf.load_grid(conn)
    if args.until:
        raw = raw[raw["ts"] < pd.Timestamp(args.until)]
    print(f"관측 자료: {raw['ts'].min()} ~ {raw['ts'].max()}" + (f" (--until {args.until})" if args.until else " (끝 고정 없음)"))
    stock = raw.pivot_table(index="ts", columns="station_id", values="stock")
    grid = stock.reindex(pd.date_range(stock.index.min().floor("D"), stock.index.max().ceil("D"),
                                       freq=pv.TICK, inclusive="left"))
    for dt, ref in refs.items():
        print(f"참조 계획({pv.DAY_TYPES[dt]}): " + " · ".join(f"{d} ← '{lab}'" for d, (_, lab) in sorted(ref.items())))

    days = sf.dense_days(raw)
    hol = dict(zip(days, holiday_mask(pd.Series(pd.to_datetime(days)))))
    index = {ts: i for i, ts in enumerate(grid.index)}
    rng = np.random.default_rng(SEED)
    rows = []
    count = {dt: 0 for dt in pv.DAY_TYPES}
    for day in days:
        dt = "holiday" if hol[day] else "weekday"
        for dur in DURATIONS:
            if dur not in refs[dt]:
                continue
            t = pd.Timestamp(day) + pd.Timedelta(hours=duration_start_hour(dur))
            if t not in index:
                continue
            s_plan = grid.iloc[index[t]].dropna()
            if len(s_plan) < 0.5 * grid.shape[1]:
                continue
            count[dt] += 1
            if args.sanity:
                continue
            ref = refs[dt][dur][0]
            for minutes, ticks in LAGS.items():
                i_dep = index[t] + ticks
                if i_dep >= len(grid):
                    continue
                s_dep = grid.iloc[i_dep].dropna()
                if len(s_dep) < 0.5 * grid.shape[1]:
                    continue
                row = one(ref, s_plan, s_dep, grid, i_dep, rng)
                row.update({"요일": dt, "회차": dur, "지연": minutes, "회차키": f"{day} {dur}"})
                rows.append(row)
    print(f"계획 회차 — 평일 {count['weekday']}개 · 휴일 {count['holiday']}개")
    if args.sanity:
        return 0

    frame = pd.DataFrame(rows).fillna(0)
    verdict = {}
    for dt in ("weekday", "holiday"):
        sub = frame[frame["요일"] == dt]
        if sub.empty:
            continue
        print(f"\n## {pv.DAY_TYPES[dt]}")
        for minutes in LAGS:
            g = sub[sub["지연"] == minutes]
            if g.empty:
                continue
            for window in (("3시간", "5시간") if dt == "weekday" or minutes in (10, 30, 120) else ("3시간",)):
                res = tally(g, f"출발 +{minutes}분 · {window}", window, dt == "weekday")
                verdict[(dt, minutes, window)] = res

    print("\n## 판정 (사전 등록: 평일, 둘 다 통과해야 '개정판을 만들 가치 있음')")
    r30 = verdict.get(("weekday", 30, "3시간"))
    r60 = verdict.get(("weekday", 60, "3시간"))
    if not r30 or not r60:
        print("  자료가 모자라 판정하지 못했다")
        return 0
    c1 = r30["gap"] >= GAP_FLOOR
    a, b = r60["A"], r60["B"]
    c2 = (b[0] - a[0] >= DROP_GAIN) and (b[1] >= a[1]) and (r60["p"] < pv.SIGN_P) and (b[2] >= EXEC_FLOOR * a[2])
    print(f"  (1) L = 30분 못 채우는 지시량 {r30['gap']:.1%} ≥ {GAP_FLOOR:.0%} : {'✅' if c1 else '❌'}")
    print(f"  (2) L = 60분 · 3시간 — 배송 적중 B {b[0]:.1%} 대 A {a[0]:.1%}(+{(b[0] - a[0]) * 100:.1f}%p, 기준 +1%p) · "
          f"수거 무해 B {b[1]:.1%} 대 A {a[1]:.1%} · p = {r60['p']:.4f} · 집행량 B/A = {b[2] / a[2]:.1%} : {'✅' if c2 else '❌'}")
    print(f"  → {'✅ 개정판을 만들 가치 있음' if (c1 and c2) else '❌ 가치 없음 — 못 넘은 칸을 그대로 적는다'}")
    return 0


if __name__ == "__main__":
    project_config.exit_if_help(__doc__)
    raise SystemExit(main())
