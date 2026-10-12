"""수거 총량은 지키고 어디서 얼마나 뺄지를 관측으로 배운 모형이 다시 나누면, 수거가 안전해지면서 배송을 잃지 않나 (EXPERIMENTS 55장).

54장 덧붙임 — 수거량만 줄이면 차가 가는 곳의 수거 무해는 49 → 80%로 오르지만, ILP가 군집 안에서 수거한 만큼만 배송하므로
배송도 47% 줄었다. 같은 양이면 학습한 수거량이 단순 표보다 13.6%p 안전했다 — 학습의 몫은 '얼마나'보다 '어디서'다.
사용자 결정(2026-10-12 · 13): *"진행하라"* · *"1번과 2번을 모두 고려해줘"* — 수거 대상 선정을 실험 안에서 바꾼다(운영 코드는
그대로). 입력 점검에서 진단(rebal_qty < −2) 안의 안전한 양을 다 더해도 현행 수거 총량에 못 미쳐(09-28 네 회차 0 ~ 167대 모자람),
두 길을 함께 잰다 — **2번** 진단 안에서만 나눈다(모자란 만큼 배송이 준다) · **1번** 진단 밖의 대여소까지 넓힌다.

변형 — 배송 쪽(대상 · 양)은 현행 그대로 두고 수거 쪽만 바꾼다. 군집 · ILP · VRP는 그대로다.
  P      현행 — 진단 가운데 수거량이 큰 순으로 TOP_STATION_LIMIT곳, 배송 총량에서 자른다
  P·M1s  54장 덧붙임의 '줄이기만' — P의 수거 대상에서 수거량만 M1으로 줄인다(대조)
  [2번 — 진단 안] 진단의 수거 대여소마다 a = min(q, ⌊모형의 안전 수거량⌋)(2대 이하면 0), a가 큰 순으로 P의 수거 총량까지
  R·M8   **2번 주 판정** — 분류형(54장 M8, 수거 대상 행으로 학습)
  R·M1 · R·T  분위수 GBM · 단순 표 — 찍기만
  R·U    모형 없는 대조 — a = ⌊q × 0.45⌋. '나누기만'의 몫을 가른다
  [1번 — 진단 밖까지] 재고가 관측된 모든 대여소 가운데 운영 식이 배송을 원하지 않는 곳(rebal_qty ≤ 2)에서 a = ⌊모형의 안전
         수거량⌋(진단의 수거 대여소는 q로 자른다), a가 큰 순으로 P의 수거 총량까지
  E·M1   **1번 주 판정** — 분위수 GBM을 **모든 대여소 행**(학습 날, 30분 간격 틱)으로 다시 학습
  E·T    같은 행의 단순 표 — 모형 없는 대조(관측 표만)
  상한 TOP_STATION_LIMIT곳은 모두 같다. 손잡이는 54장 첫 실측 그대로(k 0.85 · 표 0.95 · U 0.45) — 새로 고르지 않는다.

실행:
    python experiments/structure/pick_redistribute.py --sanity --limit 3   # 결과 없이 입력 · 후보 크기 · DB 지문만
    python experiments/structure/pick_redistribute.py                      # 등록판(55장)
"""
import argparse
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
from pipeline.step0_collect import calculate_target_qty as target_mod  # noqa: E402
from project_config import (DATA_ROOT, REBAL_MIN_QTY, TICKS_PER_HOUR, TOP_STATION_LIMIT,  # noqa: E402
                            format_seconds, holiday_mask)

bc = wv.bc
UNTIL = "2026-10-12 17:00"
# 등록 때의 DB 지문 — UNTIL 전 stock_history 행 수 · 마지막 관측 · 계획 실행 수. 등록 뒤 DB가 바뀌면(54장 덧붙임에서 겪었다) 멈춘다
FINGERPRINT = (6873641, "2026-10-12 16:50", 143)   # 2026-10-13 --sanity에서 읽은 값
TRAIN = ("2026-08-25", "2026-09-16")     # 54장 학습 날
TEST_FROM = "2026-09-24"                 # 54장 고르기 날 뒤의 평일 전부가 시험 날
H = pq.MAIN_HORIZON
KNOB = {"M8": 0.85, "M1": 0.85, "T": 0.95, "U": 0.45}   # 54장 첫 실측이 고르기 날에서 고른 값
ALL_EVERY = 3                            # 1번 모형의 학습 행 — 모든 대여소 × 30분 간격 틱(10분 간격이면 300만 행이 넘는다)
VARIANTS = ("P", "P·M1s", "R·M8", "R·M1", "R·T", "R·U", "E·M1", "E·T")
PRIMARY = {"2번 진단 안": ("R·M8", "R·U"), "1번 진단 밖까지": ("E·M1", "E·T")}   # 주 판정 · 모형 없는 대조
SCORE_H = (3 * TICKS_PER_HOUR, 5 * TICKS_PER_HOUR)
KEEP = 0.90                              # 판정 ② · ③ — P가 낸 몫의 90%를 지킨다
KM_MAX = 1.10                            # 판정 ④ — 이동거리는 P의 110% 이하
CI_ALPHA = 0.05
OUT_DIR = DATA_ROOT / "exp_55"
INFO_COLS = ["station_id", "station_name", "lat", "lon", "parking_lot", "stock", "target_qty", "rebal_qty", "mu", "sigma"]


def fingerprint(conn) -> tuple:
    rows, last = conn.execute("SELECT COUNT(*), MAX(observed_at) FROM stock_history WHERE observed_at < ?",
                              (UNTIL,)).fetchone()
    runs = conn.execute("SELECT COUNT(*) FROM runs WHERE created_at < ?", (UNTIL,)).fetchone()[0]
    return int(rows), str(last), int(runs)


def full_rebal(net, info, duration) -> pd.DataFrame:
    """모든 대여소의 재배치량 — select_top_unbalanced_st가 거르고 자르기 전. 운영 함수 그대로."""
    stats, _d, _r = pv.quiet(target_mod.build_stats, net, info[["station_id", "parking_lot", "stock"]], duration,
                             warmup_net=None, warmup_days=0, verbose=False)
    rebal = pv.quiet(target_mod.compute_rebal_qty, stats)
    st = rebal.merge(info, how="left", on="station_id", suffixes=("", "_info"))
    return st[INFO_COLS].reset_index(drop=True)


def fit_safe(rows: pd.DataFrame, starts: pd.Series, models: tuple) -> dict:
    """시험 날 회차 시작 틱의 (시각, 대여소) → 모형마다 '빼도 되는 최대 대수'. 학습 날로만 만든다."""
    from sklearn.ensemble import HistGradientBoostingRegressor
    train = rows[(rows["나눔"] == "학습") & pq.usable(rows, H, True)]
    rows = pq.add_hybrid(rows, train, H)
    train = rows.loc[train.index]
    target = rows[starts.reindex(rows.index, fill_value=False) & (rows["나눔"] == "시험")]
    key = pd.MultiIndex.from_arrays([target["ts"], target["station_id"]])
    out = {}
    if "M8" in models:
        out["M8"] = pq.predict_safe("M8", train, target, target.iloc[:0], H)[KNOB["M8"]]
    if "M1" in models:
        model = HistGradientBoostingRegressor(loss="quantile", quantile=KNOB["M1"], **pq.GBM_PARAMS)
        model.fit(train[pq.FEATURES_H], train[f"낙폭{H}"])
        out["M1"] = target["재고"] - model.predict(target[pq.FEATURES_H]) - 1
    if "T" in models:
        out["T"] = target["재고"] - pq.table_values(train, target, H, KNOB["T"]) - 1
    print(f"  학습 행 {len(train):,} · 예측 행 {len(target):,} — {', '.join(models)}")
    return {name: pd.Series(np.asarray(v, dtype=float), index=key) for name, v in out.items()}


def lookup(safe: pd.Series, t, ids) -> np.ndarray:
    key = pd.MultiIndex.from_arrays([pd.Index([t] * len(ids)), pd.Index(ids)])
    return safe.reindex(key).to_numpy()


def take_until(pool: pd.DataFrame, total: float) -> tuple:
    """a가 큰 순으로 상한 곳 수 안에서 total까지(마지막 곳은 남은 만큼, 3대 미만이면 버린다). (고른 행, 모자란 대수)"""
    pool = pool[pool["a"] > REBAL_MIN_QTY].sort_values(["a", "rebal_qty"], ascending=[False, True]).head(TOP_STATION_LIMIT)
    take, left = [], total
    for row in pool.itertuples():
        if left < REBAL_MIN_QTY + 1:
            break
        amount = min(row.a, left)
        if amount <= REBAL_MIN_QTY:
            continue
        take.append((row.Index, amount))
        left -= amount
    picks = pool.loc[[i for i, _ in take]].copy()
    picks["rebal_qty"] = [-x for _, x in take]
    return picks.drop(columns="a"), left


def redistribute(full: pd.DataFrame, base: pd.DataFrame, t, how: str, safe: dict, wide: bool) -> tuple:
    """배송은 base 그대로, 수거는 다시 나눈다. wide면 진단 밖(rebal_qty ≤ 2)까지. (후보표, 모자란 대수, 예측이 없어 뺀 곳 수)"""
    drops = base[base["rebal_qty"] > 0]
    total = float(-base.loc[base["rebal_qty"] < 0, "rebal_qty"].sum())
    if wide:
        pool = full[(full["rebal_qty"] <= REBAL_MIN_QTY) & ~full["station_id"].isin(drops["station_id"])].copy()
    else:
        pool = full[full["rebal_qty"] < -REBAL_MIN_QTY].copy()
    q = (-pool["rebal_qty"]).where(pool["rebal_qty"] < -REBAL_MIN_QTY, np.inf).astype(float)   # 진단 밖은 q로 자르지 않는다
    if how == "U":
        a, missing = np.floor(q * KNOB["U"]), 0
    else:
        s = lookup(safe[how], t, pool["station_id"])
        missing = int(np.isnan(s).sum())
        a = np.where(np.isnan(s), 0.0, np.minimum(q.to_numpy(), np.floor(np.clip(s, 0, None))))
    pool["a"] = np.asarray(a, dtype=float)
    picks, left = take_until(pool, total)
    return pd.concat([picks, drops], ignore_index=True), left, missing


def shrink_only(base: pd.DataFrame, t, safe: dict) -> pd.DataFrame:
    """54장 덧붙임의 P·M1과 같은 식 — P의 수거 대상에서 양만 M1으로 줄인다(예측이 없으면 그대로)."""
    out = base.copy()
    pick = out["rebal_qty"] < 0
    q = -out.loc[pick, "rebal_qty"].astype(float)
    s = lookup(safe["M1"], t, out.loc[pick, "station_id"])
    new = pd.Series(np.where(np.isnan(s), q, np.minimum(q, np.floor(np.clip(s, 0, None)))), index=q.index)
    out.loc[pick, "rebal_qty"] = -new.where(new > REBAL_MIN_QTY, 0.0)
    return out[out["rebal_qty"] != 0].reset_index(drop=True)


def plan(cand: pd.DataFrame, step1, solver) -> tuple:
    if cand.empty or not (cand["rebal_qty"] < 0).any() or not (cand["rebal_qty"] > 0).any():
        return pd.Series(dtype=float), pd.DataFrame()
    _, routes = bc.plan_with_clusters(cand.copy(), step1, solver, adjust=True, seed=pv.SEED)
    d = bc.executed_delta(routes)
    return d[d != 0].astype(float), routes


def gain(g: pd.DataFrame) -> float:
    return float((g["결품전"] + g["포화전"] - g["결품후"] - g["포화후"]).sum())


def report(frame: pd.DataFrame) -> None:
    keys = frame["회차키"].unique()
    print(f"\n## 시험 날 — 회차 {len(keys)}개")
    for h in SCORE_H:
        x = frame[frame["지평"] == h]
        print(f"\n### {h // TICKS_PER_HOUR}시간 — 수거 무해(수거한 곳만) · 수거 곳 · 대수 · 진단 밖 수거 곳 · 배송 곳 · 대수 · 배송 적중 개수(률)")
        for v in VARIANTS:
            y = x[x["변형"] == v]
            print(f"  {v:<6} 무해 {wv.pooled(y, '수거무해', '수거곳'):.1%} | 수거 {int(y['수거곳'].sum())}곳 {y['수거대수'].sum():.0f}대"
                  f" (진단 밖 {int(y['진단밖'].sum())}곳) | 배송 {int(y['배송곳'].sum())}곳 {y['배송대수'].sum():.0f}대 · "
                  f"적중 {int(y['배송적중'].sum())}개 ({wv.pooled(y, '배송적중', '배송수'):.1%})")
    x = frame[frame["지평"] == H]
    print("\n### 6장 시뮬레이션(3월 순수요, 모집단 = 재고가 관측된 모든 대여소로 고정 · 회차 합) — 결품 전 → 후 · 포화 전 → 후 · "
          "끝낸 대수 · 거리 · 모자란 수거 · 예측 없음")
    for v in VARIANTS:
        y = x[x["변형"] == v]
        print(f"  {v:<6} 결품 {y['결품전'].sum():.2f} → {y['결품후'].sum():.2f} · 포화 {y['포화전'].sum():.2f} → {y['포화후'].sum():.2f}"
              f" · 끝낸 {int(y['끝낸대수'].sum())}대 · {y['거리'].sum():.0f}km · 모자람 {y['모자람'].sum():.0f}대 · 예측 없음 {int(y['예측없음'].sum())}곳")

    p = x[x["변형"] == "P"].set_index("회차키").reindex(keys).fillna(0)
    rows = {}
    print("\n### P 대비(5시간, 시험 날)")
    for v in VARIANTS[1:]:
        b = x[x["변형"] == v].set_index("회차키").reindex(keys).fillna(0)
        lo, hi = wv.boot_diff(b, p, "수거무해", "수거곳", alpha=CI_ALPHA)
        diff = wv.pooled(b, "수거무해", "수거곳") - wv.pooled(p, "수거무해", "수거곳")
        hit = b["배송적중"].sum() / max(p["배송적중"].sum(), 1)
        sim = gain(b) / gain(p) if gain(p) else float("nan")
        km = b["거리"].sum() / max(p["거리"].sum(), 1e-9)
        rows[v] = (b, lo, hit, sim, km)
        print(f"  {v} − P: 수거 무해 {diff * 100:+.1f}%p [{lo * 100:+.1f}, {hi * 100:+.1f}] · 배송 적중 개수 {hit:.1%} · "
              f"시뮬레이션 감소 {sim:.1%} · 거리 {km:.1%}")
    print("\n판정 (사전 등록)")
    for label, (main, ctrl) in PRIMARY.items():
        b, lo, hit, sim, km = rows[main]
        c = rows[ctrl][0]
        clo, chi = wv.boot_diff(b, c, "수거무해", "수거곳", alpha=CI_ALPHA)
        one, two, three, four, five = lo > 0, hit >= KEEP, sim >= KEEP, km <= KM_MAX, clo > 0
        print(f"  [{label}] {main} — ① 수거 무해 > P(95% 구간 하한 > 0): {'✅' if one else '❌'} · ② 배송 적중 개수 ≥ P의 "
              f"{KEEP:.0%}: {'✅' if two else '❌'} · ③ 시뮬레이션 결품 + 포화 감소 ≥ P의 {KEEP:.0%}: {'✅' if three else '❌'} · "
              f"④ 거리 ≤ P의 {KM_MAX:.0%}: {'✅' if four else '❌'} · ⑤ 수거 무해 > {ctrl}(하한 > 0, [{clo * 100:+.1f}, {chi * 100:+.1f}]): "
              f"{'✅' if five else '❌'}")
        name = ("나눠도 수거가 안전해지지 않는다" if not one else "대가가 남는다" if not (two and three and four)
                else "채택 후보 — 모형의 몫이 있다" if five else "나누기로 충분 — 모형 없이도 된다")
        print(f"    → {name}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sanity", action="store_true", help="결과 없이 입력 · 후보 크기 · DB 지문만 찍는다")
    parser.add_argument("--limit", type=int, default=0, help="--sanity에서 회차를 이만큼만 돈다")
    args = parser.parse_args(argv)

    step1 = bc.load_step1()
    solver = bc.ilp_mod.build_solver()
    with db.session() as conn:
        mark = fingerprint(conn)
        print(f"DB 지문(UNTIL 전 재고 행 · 마지막 관측 · 계획 실행 수): {mark}")
        if not args.sanity and mark != FINGERPRINT:
            raise SystemExit(f"DB가 등록 때({FINGERPRINT})와 다르다 — 등록을 고치거나 덧붙임으로 적고 돌린다")
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
    lo_, hi_ = (pd.Timestamp(d).date() for d in TRAIN)
    test_from = pd.Timestamp(TEST_FROM).date()

    def split(frame: pd.DataFrame) -> pd.DataFrame:
        frame["나눔"] = np.where((frame["날"] >= lo_) & (frame["날"] <= hi_), "학습",
                               np.where(frame["날"] >= test_from, "시험", "쓰지 않음"))
        return frame

    arrays = pq.window_arrays(grid)
    rows = split(pq.build_rows(grid, refs, arrays, set(weekdays)))
    # 1번 — 모든 대여소 행: 학습 날은 30분 간격 틱, 시험 날은 회차 시작 틱만
    keep = (lambda i, ts, start: (lo_ <= start.date() <= hi_ and i % ALL_EVERY == 0)
            or (start.date() >= test_from and ts == start))
    rows_all = split(pq.build_rows(grid, refs, arrays, set(weekdays), stations="all", keep=keep))
    cap = info.set_index("station_id")["parking_lot"].astype(float)
    cap = cap[cap > 0]
    test_days = sorted(d for d in weekdays if d >= test_from)
    print(f"관측 자료: {raw['ts'].min()} ~ {raw['ts'].max()} (끝 {UNTIL}) · 대여소 정보 '{label}'")
    print(f"학습 {TRAIN[0]} ~ {TRAIN[1]} · 시험 평일 {len(test_days)}일: {', '.join(str(d)[5:] for d in test_days)}")
    print(f"행 — 수거 대상 {len(rows):,} · 모든 대여소 {len(rows_all):,}(학습 날 {ALL_EVERY * 10}분 간격)")

    todo = [x for x in wv.instances(raw, grid) if x[2] == "weekday" and x[0] >= test_from]
    print(f"시험 회차 {len(todo)}개 · 수거 상한 {TOP_STATION_LIMIT}곳")
    if args.sanity and args.limit:
        todo = todo[:args.limit]
    safe = fit_safe(rows, rows["시작뒤분"] == 0, ("M8", "M1", "T"))
    wide = fit_safe(rows_all, rows_all["시작뒤분"] == 0, ("M1", "T"))

    out, started = [], time.time()
    project_config.align_day_type("weekday", bc.ilp_mod, bc.vrp_mod, bc.kpi_mod, step1)
    for k, (day, dur, _dt, i, s0) in enumerate(todo, 1):
        t = grid.index[i]
        inst = wv.with_stock(info, s0)
        base = bc.build_candidates(net, inst, dur, None, pd.DataFrame(), 0, step1)
        if base.empty:
            continue
        full = full_rebal(net, inst, dur)
        diag_ids = set(full.loc[full["rebal_qty"] < -REBAL_MIN_QTY, "station_id"])
        cands = {"P": (base, 0.0, 0), "P·M1s": (shrink_only(base, t, safe), 0.0, 0)}
        for how in ("M8", "M1", "T", "U"):
            cands[f"R·{how}"] = redistribute(full, base, t, how, safe, wide=False)
        for how in ("M1", "T"):
            cands[f"E·{how}"] = redistribute(full, base, t, how, wide, wide=True)
        if args.sanity:
            sizes = " · ".join(
                f"{v} {int((c['rebal_qty'] < 0).sum())}곳 {-c.loc[c['rebal_qty'] < 0, 'rebal_qty'].sum():.0f}대"
                f"(밖 {int((~c.loc[c['rebal_qty'] < 0, 'station_id'].isin(diag_ids)).sum())} · 모자람 {short:.0f} · 없음 {miss})"
                for v, (c, short, miss) in cands.items())
            print(f"  [{k}/{len(todo)}] {day} {dur} — 진단 수거 {len(diag_ids)}곳 · {sizes} ({format_seconds(time.time() - started)})")
            continue
        population = inst[["station_id", "stock", "parking_lot"]]
        for v, (cand, short, miss) in cands.items():
            delta, routes = plan(cand, step1, solver)
            sim = bc.simulate_pair(net, population, delta, dur) or {}
            rs = bc.route_stats(routes)
            outside = int((~delta[delta < 0].index.isin(list(diag_ids))).sum())
            for h in SCORE_H:
                ws = pv.window_stats(grid, i, h)
                if ws is None:
                    continue
                row = wv.score(delta, s0, cap, ws)
                row.update({"회차키": f"{day} {dur}", "지평": h, "변형": v, "모자람": short, "예측없음": miss,
                            "진단밖": outside,
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
    report(frame)
    return 0


if __name__ == "__main__":
    project_config.exit_if_help(__doc__)
    raise SystemExit(main())
