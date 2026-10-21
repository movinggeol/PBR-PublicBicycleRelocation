"""55장의 맞바꿈(덜 해치기 대 배송 지키기)을 푸는 세 수 — 혼합 · 총량을 채우는 손잡이 · 수거와 배송의 짝 풀기 (EXPERIMENTS 56장).

55장 — 수거 총량은 지키고 '어디서 얼마나'만 모형이 다시 나누면, 모형(E·M1)은 수거를 안전하게 하지만 안전하다는 양만으로는
총량을 못 채워 배송이 73%로 줄었고, 총량을 채운 단순 표(E·T)는 배송 98.5%를 지켰지만 수거 무해는 +5.2%p뿐이었다.
사용자 요청(2026-10-13): 다음 수 셋을 모두 한다.

변형 — 배송 쪽은 P 그대로. 수거 쪽과(D는) 배송의 짝을 바꾼다. 군집 · ILP · VRP는 그대로다.
  P      현행
  E·M1   55장 그대로(진단 밖까지, 모든 대여소 행으로 학습한 분위수 GBM, k 0.85) — 재현 확인
  E·T    55장 그대로(같은 행의 단순 표) — 모형 없는 대조
  **H 혼합** — E·M1이 고른 곳 · 양을 먼저 쓰고, 모자란 몫만 아직 안 고른 곳에서 E·T의 양으로 a가 큰 순으로 채운다(상한 50곳은 합쳐서)
  **K 손잡이** — E·M1과 같은 규칙, 손잡이 k를 **고르기 날**(09-17 ~ 23)에서 *수거 총량을 90% 이상 채우는 가장 큰(보수적인) k*로
         고른다(격자 0.5 · 0.6 · 0.7 · 0.8 · 0.85 · 0.9 · 0.95, 닿는 k가 없으면 가장 많이 채우는 k). 시험 날은 쓰지 않는다
  **D 짝 풀기** — 수거는 E·M1 그대로, 군집마다 배송이 수거보다 많으면 그 차이를 **차고지 좌표의 가상 수거 지점**(차량이 처음 싣고
         나오는 자전거)으로 채운다. 차고지 자전거는 모자라지 않는다고 둔다(운영 가정이 바뀐다 — 한계)

확인 실행(56장 '확인 실행', 2026-10-20 등록): 56장 시험 뒤의 새 평일 회차로 같은 설정을 다시 잰다. 학습 날 · 모형 설정 ·
손잡이(H는 k 0.85와 표 0.95, K는 k 0.7 고정, 다시 고르지 않는다) · 판정 다섯은 56장 그대로다. 자료 끝 · 참조 계획 · 대여소
정보는 확인 자료 끝(CONFIRM_UNTIL) 기준이다(54장 확인 실행과 같은 방식).

휴일 실행(57장, 2026-10-21 등록): 같은 하네스를 휴일에 돌린다. 학습은 HOLIDAY_TRAIN_UNTIL까지의 휴일, 시험은 HOLIDAY_TEST_FROM
뒤의 휴일 회차, K의 손잡이는 평일 운영값 0.7 고정(고르기 휴일을 따로 두기엔 휴일이 모자라다). 순수요 · 기준 통계 · 도로 계수는 휴일 것이다.

실행:
    python experiments/structure/pick_mix.py --holiday --sanity  # 휴일 실행의 입력 점검(결과 없이)
    python experiments/structure/pick_mix.py --holiday           # 휴일 실행(57장)
    python experiments/structure/pick_mix.py --sanity --limit 4   # 결과 없이 입력 · 후보 크기 · 고른 손잡이 · DB 지문만
    python experiments/structure/pick_mix.py                      # 등록판(56장)
    python experiments/structure/pick_mix.py --confirm --sanity   # 확인 실행의 입력 점검(결과 없이)
    python experiments/structure/pick_mix.py --confirm            # 확인 실행(DB 지문을 등록에 적은 뒤)
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
import pick_redistribute as pr  # noqa: E402
import plan_validity as pv  # noqa: E402
import stockout_forecast as sf  # noqa: E402
import worklist_validity as wv  # noqa: E402
from project_config import (DATA_ROOT, DEPOT_LAT, DEPOT_LON, REBAL_MIN_QTY, TICKS_PER_HOUR,  # noqa: E402
                            TOP_STATION_LIMIT, format_seconds, holiday_mask)

bc = wv.bc
UNTIL = pr.UNTIL                         # 55장과 같은 자료 · 같은 DB 지문
FINGERPRINT = pr.FINGERPRINT
TRAIN = pr.TRAIN
PICK_DAYS = ("2026-09-17", "2026-09-23")  # 54장 고르기 날 — K의 손잡이를 여기서 고른다
TEST_FROM = pr.TEST_FROM
H = pq.MAIN_HORIZON
K_GRID = pq.KNOB_Q
K_FILL = 0.90                            # K — 고르기 날 수거 총량을 이만큼 채우는 가장 큰 k
VARIANTS = ("P", "E·M1", "E·T", "H", "K", "D")
PRIMARY = {"H 혼합": "H", "K 손잡이": "K", "D 짝 풀기": "D"}
CONTROL = "E·T"
SCORE_H = pr.SCORE_H
KEEP, KM_MAX = pr.KEEP, pr.KM_MAX
CI_ALPHA = 0.05 / 3                      # 주 판정이 셋이라 본페로니
DEPOT_PREFIX = "차고지_가상_"            # 가상 수거 지점의 station_id — 군집 c는 '차고지_가상_c'(관측 대여소 번호와 겹치지 않는다)
OUT_DIR = DATA_ROOT / "exp_56"

# ── 확인 실행 (56장 '확인 실행', 결과 전에 등록) ─────────────────────────────
CONFIRM_FROM = "2026-10-12 15:00"        # 56장 시험에 들지 않은 첫 평일 회차(10-12 15~20시)부터
CONFIRM_UNTIL = "2026-10-19 16:10"       # 회사 PC 자료(10-12 ~ 10-19 16:00)의 끝 + 한 틱. 이 PC가 그 뒤에 모은 틱은 쓰지 않는다
CONFIRM_K = 0.7                          # 56장에서 고른 K의 손잡이 그대로(다시 고르지 않는다)
CONFIRM_FINGERPRINT = (8220990, "2026-10-19 16:00", 150)   # 합친 직후(10-20) 찍어 결과 전에 적었다
# 휴일 실행 (57장, 2026-10-21 등록) — 결과 전에 적는다
HOLIDAY_UNTIL = "2026-10-19 06:00"       # 마지막 시험 휴일(10-18) 20~05시 회차의 5시간 창이 닫힌 뒤
HOLIDAY_TRAIN_UNTIL = "2026-10-05"       # 학습: 이 날까지의 휴일(09-12 · 09-19 · 20 · 추석 09-24 ~ 27 · 10-03 ~ 05)
HOLIDAY_TEST_FROM = "2026-10-09"         # 시험: 10-09 ~ 11 · 10-17 · 18 (5일)
HOLIDAY_K = 0.7                          # 평일 운영값 그대로(다시 고르지 않는다)
HOLIDAY_FINGERPRINT = (8149282, "2026-10-19 05:50", 150)   # 등록 전(10-21)에 찍어 적었다


def fingerprint_at(conn, until: str) -> tuple:
    """(until 전 재고 행 수 · 마지막 관측 · until 전에 만든 실행 수). 55 · 56장 `pr.fingerprint()`와 같은 셈, 끝만 다르다."""
    rows, last = conn.execute("SELECT COUNT(*), MAX(observed_at) FROM stock_history WHERE observed_at < ?",
                              (until,)).fetchone()
    runs = conn.execute("SELECT COUNT(*) FROM runs WHERE created_at < ?", (until,)).fetchone()[0]
    return int(rows), str(last), int(runs)


def fit_wide(rows_all: pd.DataFrame, starts: pd.Series) -> tuple:
    """모든 대여소 행 — k 격자마다 분위수 GBM의 안전 수거량과 단순 표의 안전 수거량(고르기 · 시험 날 회차 시작 틱)."""
    from sklearn.ensemble import HistGradientBoostingRegressor
    train = rows_all[(rows_all["나눔"] == "학습") & pq.usable(rows_all, H, True)]
    rows_all = pq.add_hybrid(rows_all, train, H)
    train = rows_all.loc[train.index]
    target = rows_all[starts.reindex(rows_all.index, fill_value=False) & rows_all["나눔"].isin(["고르기", "시험"])]
    key = pd.MultiIndex.from_arrays([target["ts"], target["station_id"]])
    m1 = {}
    for k in K_GRID:
        model = HistGradientBoostingRegressor(loss="quantile", quantile=k, **pq.GBM_PARAMS)
        model.fit(train[pq.FEATURES_H], train[f"낙폭{H}"])
        m1[k] = pd.Series(np.asarray(target["재고"] - model.predict(target[pq.FEATURES_H]) - 1, dtype=float), index=key)
    tt = pd.Series(np.asarray(target["재고"] - pq.table_values(train, target, H, pr.KNOB["T"]) - 1, dtype=float), index=key)
    print(f"  모든 대여소 학습 행 {len(train):,} · 예측 행 {len(target):,} · k {len(K_GRID)}개")
    return m1, tt


def wide_pool(full: pd.DataFrame, base: pd.DataFrame, t, safe: pd.Series) -> pd.DataFrame:
    """55장 E 규칙의 후보 — rebal_qty ≤ 2이고 P의 배송지가 아닌 곳, a = ⌊안전 수거량⌋(진단의 수거 대여소는 q로 자른다)."""
    drops = base[base["rebal_qty"] > 0]
    pool = full[(full["rebal_qty"] <= REBAL_MIN_QTY) & ~full["station_id"].isin(drops["station_id"])].copy()
    q = (-pool["rebal_qty"]).where(pool["rebal_qty"] < -REBAL_MIN_QTY, np.inf).astype(float).to_numpy()
    s = pr.lookup(safe, t, pool["station_id"])
    pool["a"] = np.where(np.isnan(s), 0.0, np.minimum(q, np.floor(np.clip(s, 0, None))))
    return pool


def pick_total(base: pd.DataFrame) -> float:
    return float(-base.loc[base["rebal_qty"] < 0, "rebal_qty"].sum())


def wide(full, base, t, safe) -> tuple:
    """E 규칙 그대로 — (후보표, 모자란 대수)."""
    drops = base[base["rebal_qty"] > 0]
    picks, left = pr.take_until(wide_pool(full, base, t, safe), pick_total(base))
    return pd.concat([picks, drops], ignore_index=True), left


def mix(full, base, t, m1: pd.Series, tt: pd.Series) -> tuple:
    """H — E·M1의 곳 · 양을 먼저, 모자란 몫만 아직 안 고른 곳에서 E·T의 양으로(상한은 합쳐서)."""
    drops = base[base["rebal_qty"] > 0]
    first, left = pr.take_until(wide_pool(full, base, t, m1), pick_total(base))
    rest = wide_pool(full, base, t, tt)
    rest = rest[~rest["station_id"].isin(first["station_id"])]
    room = TOP_STATION_LIMIT - len(first)
    parts = [first]
    if left >= REBAL_MIN_QTY + 1 and room > 0:
        rest = rest[rest["a"] > REBAL_MIN_QTY].sort_values(["a", "rebal_qty"], ascending=[False, True]).head(room)
        second, left = pr.take_until(rest, left)
        parts.append(second)
    # 빈 표를 이어 붙이면 좌표 열이 object가 되어 군집 조정(cdist)이 죽는다(56장 첫 실행) — 빈 것은 빼고 붙인다
    return pd.concat([x for x in parts + [drops] if len(x)], ignore_index=True), left


def choose_k(todo_pick, info, net, m1: dict, step1) -> tuple:
    """고르기 날 회차에서 k마다 수거 총량을 얼마나 채우나 — 후보 크기만 쓴다(관측 결과는 보지 않는다)."""
    filled = {k: 0.0 for k in K_GRID}
    want = 0.0
    for _day, dur, _dt, t, s0 in todo_pick:
        inst = wv.with_stock(info, s0)
        base = bc.build_candidates(net, inst, dur, None, pd.DataFrame(), 0, step1)
        if base.empty:
            continue
        full = pr.full_rebal(net, inst, dur)
        total = pick_total(base)
        want += total
        for k in K_GRID:
            _c, left = wide(full, base, t, m1[k])
            filled[k] += total - left
    ratio = {k: filled[k] / want for k in K_GRID}
    ok = [k for k in K_GRID if ratio[k] >= K_FILL]
    k_star = max(ok) if ok else max(K_GRID, key=lambda k: ratio[k])
    return k_star, ratio


def unpair(clustered: pd.DataFrame) -> pd.DataFrame:
    """D — 군집마다 배송이 수거보다 많으면 그 차이를 차고지 좌표의 가상 수거 지점으로 채운다."""
    rows = []
    for c, g in clustered.groupby("cluster"):
        gap = g["rebal_qty"].clip(lower=0).sum() - (-g["rebal_qty"].clip(upper=0)).sum()
        if gap > 0:
            row = {col: np.nan for col in clustered.columns}
            row.update({"station_id": f"{DEPOT_PREFIX}{c}", "lat": DEPOT_LAT, "lon": DEPOT_LON,
                        "rebal_qty": -float(gap), "cluster": c})
            if "station_name" in clustered.columns:
                row["station_name"] = "차고지(가상)"
            rows.append(row)
    if not rows:
        return clustered
    return pd.concat([clustered, pd.DataFrame(rows)], ignore_index=True)


def is_virtual(index: pd.Index) -> np.ndarray:
    return np.asarray(index.astype(str).str.startswith(DEPOT_PREFIX), dtype=bool)


def plan(cand: pd.DataFrame, step1, solver, post=None) -> tuple:
    if cand.empty or not (cand["rebal_qty"] > 0).any() or (post is None and not (cand["rebal_qty"] < 0).any()):
        return pd.Series(dtype=float), pd.DataFrame()
    _, routes = bc.plan_with_clusters(cand.copy(), step1, solver, adjust=True, seed=pv.SEED, post=post)
    d = bc.executed_delta(routes)
    return d[d != 0].astype(float), routes


def report(frame: pd.DataFrame, k_star) -> None:
    keys = frame["회차키"].unique()
    print(f"\n## 시험 날 — 회차 {len(keys)}개 · K의 손잡이 k = {k_star}")
    for h in SCORE_H:
        x = frame[frame["지평"] == h]
        print(f"\n### {h // TICKS_PER_HOUR}시간 — 수거 무해(수거한 곳만) · 해친 곳 · 수거 곳 · 대수 · 진단 밖 · 배송 곳 · 대수 · 배송 적중 개수(률)")
        for v in VARIANTS:
            y = x[x["변형"] == v]
            print(f"  {v:<5} 무해 {wv.pooled(y, '수거무해', '수거곳'):.1%} · 해친 곳 {int((y['수거곳'] - y['수거무해']).sum())}"
                  f" | 수거 {int(y['수거곳'].sum())}곳 {y['수거대수'].sum():.0f}대 (진단 밖 {int(y['진단밖'].sum())}곳)"
                  f" | 배송 {int(y['배송곳'].sum())}곳 {y['배송대수'].sum():.0f}대 · 적중 {int(y['배송적중'].sum())}개"
                  f" ({wv.pooled(y, '배송적중', '배송수'):.1%})")
    x = frame[frame["지평"] == H]
    print("\n### 6장 시뮬레이션(모집단 = 재고가 관측된 모든 대여소 · 회차 합) — 결품 전 → 후 · 포화 전 → 후 · 끝낸 대수 · 거리 · 모자란 수거 · 차고지에서 실은 대수")
    for v in VARIANTS:
        y = x[x["변형"] == v]
        print(f"  {v:<5} 결품 {y['결품전'].sum():.2f} → {y['결품후'].sum():.2f} · 포화 {y['포화전'].sum():.2f} → {y['포화후'].sum():.2f}"
              f" · 끝낸 {int(y['끝낸대수'].sum())}대 · {y['거리'].sum():.0f}km · 모자람 {y['모자람'].sum():.0f}대 · 차고지 {int(y['차고지'].sum())}대"
              f" · 시간 예산 넘은 군집 {int(y['예산초과'].sum())}개 · 가장 긴 운행 {format_seconds(y['최장분'].max() * 60)}")
    p = x[x["변형"] == "P"].set_index("회차키").reindex(keys).fillna(0)
    c = x[x["변형"] == CONTROL].set_index("회차키").reindex(keys).fillna(0)
    print(f"\n### P 대비(5시간) — 구간은 {1 - CI_ALPHA:.1%}")
    rows = {}
    for v in VARIANTS[1:]:
        b = x[x["변형"] == v].set_index("회차키").reindex(keys).fillna(0)
        lo, hi = wv.boot_diff(b, p, "수거무해", "수거곳", alpha=CI_ALPHA)
        diff = wv.pooled(b, "수거무해", "수거곳") - wv.pooled(p, "수거무해", "수거곳")
        hit = b["배송적중"].sum() / max(p["배송적중"].sum(), 1)
        sim = pr.gain(b) / pr.gain(p) if pr.gain(p) else float("nan")
        km = b["거리"].sum() / max(p["거리"].sum(), 1e-9)
        clo, chi = wv.boot_diff(b, c, "수거무해", "수거곳", alpha=CI_ALPHA) if v != CONTROL else (float("nan"),) * 2
        rows[v] = (lo, hit, sim, km, clo, chi)
        print(f"  {v} − P: 수거 무해 {diff * 100:+.1f}%p [{lo * 100:+.1f}, {hi * 100:+.1f}] · 배송 적중 개수 {hit:.1%} · "
              f"시뮬레이션 감소 {sim:.1%} · 거리 {km:.1%}")
    print("\n판정 (사전 등록)")
    for label, v in PRIMARY.items():
        lo, hit, sim, km, clo, chi = rows[v]
        one, two, three, four, five = lo > 0, hit >= KEEP, sim >= KEEP, km <= KM_MAX, clo > 0
        print(f"  [{label}] ① 수거 무해 > P: {'✅' if one else '❌'} · ② 배송 적중 개수 ≥ {KEEP:.0%}: {'✅' if two else '❌'} · "
              f"③ 시뮬레이션 ≥ {KEEP:.0%}: {'✅' if three else '❌'} · ④ 거리 ≤ {KM_MAX:.0%}: {'✅' if four else '❌'} · "
              f"⑤ 수거 무해 > {CONTROL}([{clo * 100:+.1f}, {chi * 100:+.1f}]): {'✅' if five else '❌'}")
        name = ("수거가 안전해지지 않는다" if not one else "대가가 남는다" if not (two and three and four)
                else "채택 후보 — 모형의 몫이 있다" if five else "표로 충분 — 모형 없이도 된다")
        print(f"    → {name}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sanity", action="store_true", help="결과 없이 입력 · 후보 크기 · 고른 손잡이 · DB 지문만 찍는다")
    parser.add_argument("--limit", type=int, default=0, help="--sanity에서 시험 회차를 이만큼만 돈다")
    parser.add_argument("--holiday", action="store_true",
                        help=f"휴일 실행(57장): 학습 {HOLIDAY_TRAIN_UNTIL}까지의 휴일 · 시험 {HOLIDAY_TEST_FROM} 뒤 휴일 회차 · k {HOLIDAY_K} 고정")
    parser.add_argument("--confirm", action="store_true",
                        help=f"확인 실행: 56장 설정 그대로, {CONFIRM_FROM} 뒤 평일 회차만 채점(끝 {CONFIRM_UNTIL})")
    args = parser.parse_args(argv)

    if args.confirm and args.holiday:
        parser.error("--confirm과 --holiday는 함께 쓰지 않는다")
    until = CONFIRM_UNTIL if args.confirm else HOLIDAY_UNTIL if args.holiday else UNTIL
    registered = CONFIRM_FINGERPRINT if args.confirm else HOLIDAY_FINGERPRINT if args.holiday else FINGERPRINT
    day_type = "holiday" if args.holiday else "weekday"
    step1 = bc.load_step1()
    solver = bc.ilp_mod.build_solver()
    with db.session() as conn:
        mark = fingerprint_at(conn, until)
        print(f"DB 지문(끝 {until} 전 재고 행 · 마지막 관측 · 실행 수): {mark}")
        if not args.sanity and registered is None:
            raise SystemExit("이 실행의 DB 지문이 등록에 없다. 찍은 값을 상수(CONFIRM_ · HOLIDAY_FINGERPRINT)와 등록에 적고 돌린다")
        if not args.sanity and mark != registered:
            raise SystemExit(f"DB가 등록 때({registered})와 다르다 — 등록을 고치거나 덧붙임으로 적고 돌린다")
        refs = pv.load_refs(conn, day_type, before=until)
        raw = sf.load_grid(conn)
        label, info = wv.snapshot(conn, until)
    raw = raw[raw["ts"] < pd.Timestamp(until)]
    net = pv.load_net(day_type)
    for dur, (frame, _lab) in list(refs.items()):
        refs[dur] = pv.current_stats(frame, net, dur)
    stock = raw.pivot_table(index="ts", columns="station_id", values="stock")
    grid = stock.reindex(pd.date_range(stock.index.min().floor("D"), stock.index.max().ceil("D"),
                                       freq=pv.TICK, inclusive="left"))
    dense = sf.dense_days(raw)
    hol = dict(zip(dense, holiday_mask(pd.Series(pd.to_datetime(dense)))))
    weekdays = [d for d in dense if hol[d] == args.holiday]   # 휴일 실행이면 휴일만(이름은 옛것 그대로)
    lo_, hi_ = (pd.Timestamp(d).date() for d in TRAIN)
    pk_lo, pk_hi = (pd.Timestamp(d).date() for d in PICK_DAYS)
    test_from = pd.Timestamp(TEST_FROM).date()
    confirm_from = pd.Timestamp(CONFIRM_FROM)

    def part(d):
        if args.holiday:                     # 고르기 날 없음(k 고정)
            return ("학습" if d <= pd.Timestamp(HOLIDAY_TRAIN_UNTIL).date()
                    else "시험" if d >= pd.Timestamp(HOLIDAY_TEST_FROM).date() else "쓰지 않음")
        if args.confirm:                     # 학습 날은 56장 그대로, 고르기 날은 쓰지 않는다(K는 고정), 확인 날만 '시험'
            return "학습" if lo_ <= d <= hi_ else "시험" if d >= confirm_from.date() else "쓰지 않음"
        return "학습" if lo_ <= d <= hi_ else "고르기" if pk_lo <= d <= pk_hi else "시험" if d >= test_from else "쓰지 않음"

    keep = (lambda i, ts, start: (part(start.date()) == "학습" and i % pr.ALL_EVERY == 0)
            or (part(start.date()) in ("고르기", "시험") and ts == start))
    rows_all = pq.build_rows(grid, refs, pq.window_arrays(grid), set(weekdays), stations="all", keep=keep)
    rows_all["나눔"] = rows_all["날"].map(part)
    cap = info.set_index("station_id")["parking_lot"].astype(float)
    cap = cap[cap > 0]
    print(f"관측 자료: {raw['ts'].min()} ~ {raw['ts'].max()} (끝 {until}) · 대여소 정보 '{label}' · 모든 대여소 행 {len(rows_all):,}")
    m1, tt = fit_wide(rows_all, rows_all["시작뒤분"] == 0)

    inst_all = [x for x in wv.instances(raw, grid) if x[2] == day_type]
    todo_pick = [x for x in inst_all if part(x[0]) == "고르기"]
    todo = [x for x in inst_all if part(x[0]) == "시험"]
    project_config.align_day_type(day_type, bc.ilp_mod, bc.vrp_mod, bc.kpi_mod, step1)
    if args.holiday:
        last = raw["ts"].max()
        todo = [x for x in todo if grid.index[x[3]] + pd.Timedelta(minutes=H * project_config.STOCK_TICK_MINUTES) <= last]
        k_star = HOLIDAY_K
        print(f"휴일 실행: 학습 휴일 {sorted({d for d in weekdays if part(d) == '학습'})} · K의 손잡이 k = {k_star}(평일 운영값) · 시험 회차 "
              + ", ".join(f"{d} {u}" for d, u, *_ in todo))
    elif args.confirm:
        # 56장 시험 뒤의 회차만, 그리고 5시간 창이 자료 끝 전에 닫힌 것만(창이 안 닫힌 회차는 채점할 수 없다)
        last = raw["ts"].max()
        todo = [x for x in todo if grid.index[x[3]] >= confirm_from
                and grid.index[x[3]] + pd.Timedelta(minutes=H * project_config.STOCK_TICK_MINUTES) <= last]
        k_star = CONFIRM_K
        print(f"확인 실행: K의 손잡이는 56장 값 k = {k_star}(다시 고르지 않는다) · 확인 회차 "
              + ", ".join(f"{d} {u}" for d, u, *_ in todo))
    else:
        k_star, ratio = choose_k([(d, u, dt, grid.index[i], s) for d, u, dt, i, s in todo_pick], info, net, m1, step1)
        print(f"K — 고르기 날 {len(todo_pick)}회차에서 k마다 수거 총량을 채운 비율: "
              + " · ".join(f"{k} {r:.1%}" for k, r in ratio.items()) + f" → k = {k_star}")
    print(f"시험 회차 {len(todo)}개 · 수거 상한 {TOP_STATION_LIMIT}곳")
    if args.sanity and args.limit:
        todo = todo[:args.limit]

    out, started = [], time.time()
    for n, (day, dur, _dt, i, s0) in enumerate(todo, 1):
        t = grid.index[i]
        inst = wv.with_stock(info, s0)
        base = bc.build_candidates(net, inst, dur, None, pd.DataFrame(), 0, step1)
        if base.empty:
            continue
        full = pr.full_rebal(net, inst, dur)
        diag_ids = set(full.loc[full["rebal_qty"] < -REBAL_MIN_QTY, "station_id"])
        e_m1 = wide(full, base, t, m1[pr.KNOB["M1"]])
        cands = {"P": (base, 0.0, None), "E·M1": (*e_m1, None), "E·T": (*wide(full, base, t, tt), None),
                 "H": (*mix(full, base, t, m1[pr.KNOB["M1"]], tt), None), "K": (*wide(full, base, t, m1[k_star]), None),
                 "D": (*e_m1, unpair)}
        if args.sanity:
            sizes = " · ".join(
                f"{v} {int((c['rebal_qty'] < 0).sum())}곳 {-c.loc[c['rebal_qty'] < 0, 'rebal_qty'].sum():.0f}대(모자람 {short:.0f})"
                for v, (c, short, _p) in cands.items())
            print(f"  [{n}/{len(todo)}] {day} {dur} — {sizes} ({format_seconds(time.time() - started)})")
            continue
        population = inst[["station_id", "stock", "parking_lot"]]
        for v, (cand, short, post) in cands.items():
            delta, routes = plan(cand, step1, solver, post)
            is_depot = is_virtual(delta.index)
            depot = float(-delta[is_depot].sum())
            delta = delta[~is_depot]                         # 가상 수거 지점은 채점 · 시뮬레이션에서 뺀다
            sim = bc.simulate_pair(net, population, delta, dur) or {}
            rs = bc.route_stats(routes)
            outside = int((~delta[delta < 0].index.isin(list(diag_ids))).sum())
            for h in SCORE_H:
                ws = pv.window_stats(grid, i, h)
                if ws is None:
                    continue
                row = wv.score(delta, s0, cap, ws)
                row.update({"회차키": f"{day} {dur}", "지평": h, "변형": v, "모자람": short if v != "D" else 0.0,
                            "진단밖": outside, "차고지": depot,
                            "결품전": sim.get("stockout_before", 0.0), "결품후": sim.get("stockout_after", 0.0),
                            "포화전": sim.get("saturation_before", 0.0), "포화후": sim.get("saturation_after", 0.0),
                            "끝낸대수": rs["bikes_done"], "거리": rs["km"], "예산초과": rs["over"], "최장분": rs["max_min"]})
                out.append(row)
        print(f"  {n}/{len(todo)} 회차 ({format_seconds(time.time() - started)})", flush=True)
    if args.sanity:
        print("--sanity: 작업 목록 · 채점은 계산하지 않았다")
        return 0

    frame = pd.DataFrame(out)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    name = "rows_confirm.csv" if args.confirm else "rows_holiday.csv" if args.holiday else "rows.csv"
    frame.to_csv(OUT_DIR / name, index=False, encoding="utf-8-sig")
    report(frame, k_star)
    return 0


if __name__ == "__main__":
    project_config.exit_if_help(__doc__)
    raise SystemExit(main())
