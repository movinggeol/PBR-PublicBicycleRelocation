"""줄인 수거량으로 작업 목록을 다시 세우면 차가 가는 곳에서도 수거가 무해해지나, 배송은 얼마나 잃나 (EXPERIMENTS 54장 덧붙임).

54장은 운영 식의 **진단**(rebal_qty < −2인 모든 대여소)에서 수거 대수만 줄여 채점했다. 차가 실제로 가는 곳은 그중 상위
후보를 군집 · ILP · VRP가 짝지은 일부이고, ILP는 군집 안에서 수거와 배송을 짝지으므로(min(수거, 배송)) 수거를 줄이면
**배송도 함께 준다**. 이 스크립트는 53장의 제안 경로(`baseline_compare.py`의 함수 그대로)에 54장이 고른 수거량을 넣어
작업 목록을 다시 세우고, ① 53장의 자로 관측 채점하고 ② 그 대가를 관측(배송 적중 개수)과 6장 시뮬레이션(결품 · 포화)으로 잰다.

변형 — 후보표(build_candidates)의 수거 행만 바꾼다. 배송 행 · 군집 · ILP · VRP는 그대로다.
  P     현행(53장 P와 같다)
  P·M1  수거량 = min(q, ⌊재고 − M1 예측 낙폭 − 1⌋), 2대 이하면 후보에서 뺀다 — 54장 M1(5시간 학습 · 손잡이 k 0.85)
  P·T   같은 식, 단순 표(5시간 · k 0.95) — 찍기만
  P·U   ⌊q × 0.45⌋ — 찍기만
  손잡이는 54장 첫 실측이 고르기 날에서 고른 값 그대로다(새로 고르지 않는다).

실행:
    python experiments/structure/pick_qty_worklist.py --sanity --limit 2   # 결과 없이 입력 · 후보 크기 · 걸린 시간만
    python experiments/structure/pick_qty_worklist.py                      # 등록판(54장 덧붙임)
"""
import argparse
import contextlib
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import project_config  # noqa: E402  — 콘솔 인코딩을 먼저 맞춘다(— · 이모지)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import db  # noqa: E402
import pick_qty_ml as pq  # noqa: E402
import plan_validity as pv  # noqa: E402
import stockout_forecast as sf  # noqa: E402
import worklist_validity as wv  # noqa: E402
from project_config import DATA_ROOT, DURATIONS, REBAL_MIN_QTY, TICKS_PER_HOUR, format_seconds, holiday_mask  # noqa: E402

bc = wv.bc
UNTIL = "2026-10-11 00:00"               # 54장과 같다
H = pq.MAIN_HORIZON                      # 모형은 5시간 판(54장의 판정 지평)
KNOB = {"M1": 0.85, "T": 0.95, "U": 0.45}   # 54장 첫 실측이 고르기 날에서 고른 값
VARIANTS = ("P", "P·M1", "P·T", "P·U")
SCORE_H = (3 * TICKS_PER_HOUR, 5 * TICKS_PER_HOUR)
KEEP = 0.90                              # 판정 ② · ③ — P가 낸 몫의 90%를 지킨다
CI_ALPHA = 0.05
OUT_DIR = DATA_ROOT / "exp_54_worklist"


def safe_tables(rows: pd.DataFrame, starts: pd.Series) -> dict:
    """회차 시작 틱의 (시각, 대여소) → 방법마다 '빼도 되는 최대 대수'. M1 · T는 학습 날로만 만든다."""
    from sklearn.ensemble import HistGradientBoostingRegressor
    train = rows[(rows["나눔"] == "학습") & pq.usable(rows, H, True)]
    rows = pq.add_hybrid(rows, train, H)
    train = rows.loc[train.index]
    target = rows[starts & rows["나눔"].isin(["고르기", "시험"])]
    model = HistGradientBoostingRegressor(loss="quantile", quantile=KNOB["M1"], **pq.GBM_PARAMS)
    model.fit(train[pq.FEATURES_H], train[f"낙폭{H}"])
    key = pd.MultiIndex.from_arrays([target["ts"], target["station_id"]])
    m1 = target["재고"] - model.predict(target[pq.FEATURES_H]) - 1
    tt = target["재고"] - pq.table_values(train, target, H, KNOB["T"]) - 1
    return {"M1": pd.Series(m1.to_numpy(), index=key), "T": pd.Series(tt.to_numpy(), index=key)}


def shrink(base: pd.DataFrame, t: pd.Timestamp, how: str, safe: dict) -> tuple:
    """후보표의 수거 행만 줄인다. 2대 이하가 된 수거 행은 후보에서 뺀다. (후보표, 54장 행이 없어 그대로 둔 수거 곳 수)"""
    out = base.copy()
    pick = out["rebal_qty"] < 0
    q = -out.loc[pick, "rebal_qty"].astype(float)
    if how == "U":
        new = np.floor(q * KNOB["U"])
        missing = 0
    else:
        key = pd.MultiIndex.from_arrays([pd.Index([t] * int(pick.sum())), out.loc[pick, "station_id"]])
        s = safe[how].reindex(key)
        missing = int(s.isna().sum())
        cap = np.floor(s.clip(lower=0).to_numpy())
        new = pd.Series(np.where(np.isnan(cap), q, np.minimum(q, cap)), index=q.index)
    new = new.where(new > REBAL_MIN_QTY, 0.0)
    out.loc[pick, "rebal_qty"] = -new
    out = out[out["rebal_qty"] != 0].reset_index(drop=True)
    return out, missing


def plan(cand: pd.DataFrame, step1, solver) -> tuple:
    if cand.empty or not (cand["rebal_qty"] < 0).any() or not (cand["rebal_qty"] > 0).any():
        return pd.Series(dtype=float), pd.DataFrame()
    _, routes = bc.plan_with_clusters(cand.copy(), step1, solver, adjust=True, seed=pv.SEED)
    d = bc.executed_delta(routes)
    return d[d != 0].astype(float), routes


def report(frame: pd.DataFrame, part: str, verdict: bool) -> None:
    g = frame[frame["나눔"] == part]
    keys = g["회차키"].unique()
    print(f"\n## {part} 날 — 회차 {len(keys)}개" + ("" if verdict else " (찍기만)"))
    for h in SCORE_H:
        x = g[g["지평"] == h]
        hours = h // TICKS_PER_HOUR
        print(f"\n### {hours}시간 — 수거 무해(수거한 곳만, 53장 자) · 회차 합 수거곳 · 수거대수 · 배송곳 · 배송대수 · 배송 적중 개수 · 배송 적중률")
        for v in VARIANTS:
            y = x[x["변형"] == v]
            print(f"  {v:<5} 무해 {wv.pooled(y, '수거무해', '수거곳'):.1%} | 수거 {int(y['수거곳'].sum())}곳 {y['수거대수'].sum():.0f}대"
                  f" | 배송 {int(y['배송곳'].sum())}곳 {y['배송대수'].sum():.0f}대 · 적중 {int(y['배송적중'].sum())}개"
                  f" ({wv.pooled(y, '배송적중', '배송수'):.1%})")
        p = x[x["변형"] == "P"].set_index("회차키").reindex(keys).fillna(0)
        for v in VARIANTS[1:]:
            b = x[x["변형"] == v].set_index("회차키").reindex(keys).fillna(0)
            lo, hi = wv.boot_diff(b, p, "수거무해", "수거곳", alpha=CI_ALPHA)
            diff = wv.pooled(b, "수거무해", "수거곳") - wv.pooled(p, "수거무해", "수거곳")
            hit = b["배송적중"].sum() / max(p["배송적중"].sum(), 1)
            print(f"  {v} − P: 수거 무해 {diff * 100:+.1f}%p [{lo * 100:+.1f}, {hi * 100:+.1f}] · 배송 적중 개수 {hit:.1%} 유지")
            if verdict and v == "P·M1" and h == H:
                one = lo > 0
                two = hit >= KEEP
                sim = g[(g["지평"] == H)]
                sp = sim[sim["변형"] == "P"]
                sm = sim[sim["변형"] == "P·M1"]
                gain_p = (sp["결품전"] + sp["포화전"] - sp["결품후"] - sp["포화후"]).sum()
                gain_m = (sm["결품전"] + sm["포화전"] - sm["결품후"] - sm["포화후"]).sum()
                three = gain_m >= KEEP * gain_p
    print("\n### 6장 시뮬레이션(3월 순수요, 회차 합 · 대여소 · 일 평균 시간) — 결품 전 → 후 · 포화 전 → 후 · 옮겨 끝낸 대수 · 거리")
    x = g[g["지평"] == H]
    for v in VARIANTS:
        y = x[x["변형"] == v]
        print(f"  {v:<5} 결품 {y['결품전'].sum():.2f} → {y['결품후'].sum():.2f} · 포화 {y['포화전'].sum():.2f} → {y['포화후'].sum():.2f}"
              f" · 끝낸 {int(y['끝낸대수'].sum())}대 · {y['거리'].sum():.0f}km")
    if verdict:
        print(f"\n판정 — ① 수거 무해 P·M1 > P(95% 구간 하한 > 0): {'✅' if one else '❌'} · ② 배송 적중 개수 ≥ P의 {KEEP:.0%}: "
              f"{'✅' if two else '❌'} · ③ 시뮬레이션 결품 + 포화 감소 ≥ P의 {KEEP:.0%}({gain_m:.2f} 대 {gain_p:.2f}): "
              f"{'✅' if three else '❌'}")
        name = ("작업 목록에서도 듣고 대가가 작다" if one and two and three else
                "작업 목록에서는 안 듣는다" if not one else "수거는 안전해지나 대가가 크다")
        print(f"  → {name}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sanity", action="store_true", help="결과 없이 입력 · 후보 크기 · 걸린 시간만 찍는다")
    parser.add_argument("--limit", type=int, default=0, help="--sanity에서 회차를 이만큼만 돈다")
    args = parser.parse_args(argv)

    step1 = bc.load_step1()
    solver = bc.ilp_mod.build_solver()
    with db.session() as conn:
        refs = pv.load_refs(conn, "weekday", before=UNTIL)
        raw = sf.load_grid(conn)
        label, info = wv.snapshot(conn, UNTIL)
    raw = raw[raw["ts"] < pd.Timestamp(UNTIL)]
    net = pv.load_net("weekday")
    for dur, (frame, _lab) in list(refs.items()):
        refs[dur] = pv.current_stats(frame, net, dur)
    stock = raw.pivot_table(index="ts", columns="station_id", values="stock")
    grid = stock.reindex(pd.date_range(stock.index.min().floor("D"), stock.index.max().ceil("D"),
                                       freq=pv.TICK, inclusive="left"))
    dense = sf.dense_days(raw)
    hol = dict(zip(dense, holiday_mask(pd.Series(pd.to_datetime(dense)))))
    weekdays = [d for d in dense if not hol[d]]
    rows = pq.build_rows(grid, refs, pq.window_arrays(grid), set(weekdays))
    split = pq.split_days(sorted(rows["날"].unique()))
    rows["나눔"] = rows["날"].map(split)
    starts = rows["시작뒤분"] == 0
    cap = info.set_index("station_id")["parking_lot"].astype(float)
    cap = cap[cap > 0]
    print(f"관측 자료: {raw['ts'].min()} ~ {raw['ts'].max()} (끝 {UNTIL}) · 대여소 정보 '{label}'")

    todo = [x for x in wv.instances(raw, grid) if x[2] == "weekday" and split.get(x[0]) in ("고르기", "시험")]
    print(f"회차 — 고르기 {sum(split[x[0]] == '고르기' for x in todo)}개 · 시험 {sum(split[x[0]] == '시험' for x in todo)}개")
    if args.sanity and args.limit:
        todo = todo[:args.limit]
    safe = safe_tables(rows, starts)

    out, started = [], time.time()
    project_config.align_day_type("weekday", bc.ilp_mod, bc.vrp_mod, bc.kpi_mod, step1)
    for k, (day, dur, _dt, i, s0) in enumerate(todo, 1):
        t = grid.index[i]
        inst = wv.with_stock(info, s0)
        base = bc.build_candidates(net, inst, dur, None, pd.DataFrame(), 0, step1)
        if base.empty:
            continue
        cands = {"P": (base, 0)}
        for how in ("M1", "T", "U"):
            cands[f"P·{how}"] = shrink(base, t, how, safe)
        if args.sanity:
            sizes = " · ".join(f"{v} 수거 후보 {int((c['rebal_qty'] < 0).sum())}곳 {-c.loc[c['rebal_qty'] < 0, 'rebal_qty'].sum():.0f}대"
                               f" (54장 행 없음 {miss})" for v, (c, miss) in cands.items())
            print(f"  [{k}/{len(todo)}] {day} {dur} — {sizes} ({format_seconds(time.time() - started)})")
            continue
        for v, (cand, miss) in cands.items():
            delta, routes = plan(cand, step1, solver)
            sim = bc.simulate_pair(net, base, delta, dur) or {}
            rs = bc.route_stats(routes)
            for h in SCORE_H:
                ws = pv.window_stats(grid, i, h)
                if ws is None:
                    continue
                row = wv.score(delta, s0, cap, ws)
                row.update({"회차키": f"{day} {dur}", "나눔": split[day], "지평": h, "변형": v, "행없음": miss,
                            "결품전": sim.get("stockout_before", 0.0), "결품후": sim.get("stockout_after", 0.0),
                            "포화전": sim.get("saturation_before", 0.0), "포화후": sim.get("saturation_after", 0.0),
                            "끝낸대수": rs["bikes_done"], "거리": rs["km"]})
                out.append(row)
        print(f"  {k}/{len(todo)} 회차 ({format_seconds(time.time() - started)})", flush=True)
    if args.sanity:
        print(f"--sanity: 회차당 {format_seconds((time.time() - started) / max(len(todo), 1))} — 작업 목록 · 채점은 계산하지 않았다")
        return 0

    frame = pd.DataFrame(out)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    frame.to_csv(OUT_DIR / "rows.csv", index=False, encoding="utf-8-sig")
    print(f"\n54장 행이 없어 수거량을 그대로 둔 수거 후보 — 회차 합 {int(frame[(frame['변형'] == 'P·M1') & (frame['지평'] == H)]['행없음'].sum())}곳")
    report(frame, "시험", verdict=True)
    report(frame, "고르기", verdict=False)
    return 0


if __name__ == "__main__":
    project_config.exit_if_help(__doc__)
    raise SystemExit(main())
