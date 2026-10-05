"""계획표는 몇 시간 뒤에 실제로 맞았나 — 회차 시작 재고로 세운 계획의 배송 · 수거 진단을 그 뒤 관측 재고로 판정한다 (EXPERIMENTS 47장).

사용자 물음 — *"재배치 계획표로 미래에 부족할 것 같은 곳에 drop, 과잉인 곳에 pick을 한다. 이게 몇 시간 뒤에 진짜로
유효한지 증명하고 싶다."* KPI.md B의 빈칸 *"재배치 대여소 적중률 — 대상 선정 검증"* 이 이 자리다.

왜 시뮬레이션이 아닌가 — 6장의 결품 감소는 재고 사진 한 장에 다른 달의 순수요를 더해 **복원**한 값이라, *"계획을 세운
날의 몇 시간 뒤에 실제로 그렇게 됐다"* 를 보이지 못한다. 여기서는 시각 t의 **관측 재고**로 계획을 세우고(입력은 t의 재고와
26년 03월 통계뿐 — 관측 기간의 미래는 들어가지 않는다), t 뒤의 **관측 재고**로 채점한다.

무엇을 증명하고 무엇을 못 하나 — 계획은 집행되지 않았으므로 관측 재고는 *"우리가 손대지 않은 세상"* 이다. 그래서 이 실험은
**진단이 맞았나**(배송하라던 곳은 그냥 두면 정말 비었나 · 수거하라던 곳은 빼도 괜찮았나)를 잰다. *"집행했으면 결품이 몇 시간
줄었나"* 는 못 잰다 — 빈 동안 못 빌린 수요는 관측되지 않는다. 그쪽은 시뮬레이션의 몫으로 남는다.

사전 등록 (EXPERIMENTS 47장 — 결과 전 커밋. 바꾸지 않는다)
  계획     : 하루 30틱 이상인 날의 회차 시작 시각 t(05 · 10 · 15 · 20시)마다, 그날 요일 구분의 **가장 최근 계획 실행**
             (kind = plan, 운영 기간)의 mu · sigma · 거치대에 **t의 관측 재고**를 넣어 운영 함수 compute_rebal_qty()로
             다시 계산. 배송 = rebal_qty > 2 · 수거 = rebal_qty < −2(수거량 q = −rebal_qty)
  지평     : t 뒤 1 · 3 · 5시간(6 · 18 · 30틱). 그 창의 10분 칸이 80% 이상 관측된 대여소만 센다
  배송 적중: t에 재고가 있던(> 0) 배송 대여소가 창 안에 한 번이라도 0이 됐나
  수거 무해: 창 안의 관측 재고 최저값 ≥ q (빼 갔어도 0이 안 됐을 것)
  대조     : B0 무작위 = t에 재고가 있던 모든 대여소의 빔 비율 · B1 지금 재고 규칙 = 같은 수의 **재고가 가장 적은**
             대여소(배송) / 같은 수의 **재고가 가장 많은** 대여소에서 같은 총량을 고정 여유 R로 빼기(수거)
  판정     : 평일만. 배송은 지평마다 — 합친 적중률이 B0 · B1보다 높고, 회차 단위 부호 검정(계획 > B1)이 단측 p < 0.05.
             수거는 지평마다 — 합친 무해율이 B1 이상, 5시간 무해율 ≥ 90%.
             **3시간 · 5시간에서 둘 다 통과하면 '증명'**. 1시간과 휴일은 찍기만 한다
  찍기만   : 이미 비어 있던 배송 대여소의 빈 시간 비율 · 수거 대여소의 포화(거치대 90%) 도달 · 회차별 표 ·
             실제 동시각 계획(kind = plan, 라벨 '동시각')의 차량 계획(vrp_plan) 대여소로 같은 지표

사후 진단 (등록 뒤 추가 · 판정 아님 — 1.26.321) — 첫 실측에서 수거가 5시간 기준에 못 미쳐(무해 64.8%) 까닭을 보려고
  목표 재고 식의 두 갈래(mu < 0 · mu ≥ 0)별 무해, 빼 갔다면 비었을 시간, 공사 트럭의 흔적(10분에 JUMP대 이상 변화)을
  더 찍는다. 판정 줄의 수치는 이것을 더하기 전과 한 자리도 같다.

알려진 치우침 — 관측 기간에 공사가 실제로 채운 대여소는 관측 재고가 덜 빈다. 배송 적중을 **낮춰** 잡는 쪽이고 계획과 대조군에
똑같이 걸린다. 공사의 이동을 대여이력 사슬(44장)로 걸러 낼 수는 없다 — 대여이력이 26년 03월에서 끝난다.

정정 (1.26.323) — 등록의 '가장 최근 계획 실행' 규칙이 09-28 15시 이전 동시각 계획(평일 셋 · 휴일 넷)을 집었는데, 그 계획들은
  IQR로 깎인 대여이력의 순수요로 섰다(7c6f851). `--stats current`는 mu · sigma를 지금 순수요로 운영 함수 build_stats()가
  다시 낸다. 원고에 쓰는 값은 이쪽이다(EXPERIMENTS 47장 정정 절).

실행:
    python experiments/structure/plan_validity.py            # 판정(등록 그대로)
    python experiments/structure/plan_validity.py --stats current   # 정정판 — 지금 순수요로
    python experiments/structure/plan_validity.py --stats current --until "2026-10-06 00:00"   # 인용판(1.26.325)
    python experiments/structure/plan_validity.py --sanity   # 결과 없이 입력만 점검(재계산 일치 · 회차 수)
"""
import argparse
import contextlib
import io
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import project_config  # noqa: E402,F401  — 콘솔 인코딩을 먼저 맞춘다(— · 이모지)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import db  # noqa: E402
import stockout_forecast as sf  # noqa: E402
from pipeline.step0_collect import calculate_target_qty as target_mod  # noqa: E402
from project_config import (DATA_ROOT, DEFAULT_PERIOD, REBAL_MIN_QTY, duration_hours, holiday_mask,  # noqa: E402
                            select_day_type)

TICK = pd.Timedelta(minutes=sf.TICK_MINUTES)

# ── 사전 등록 (EXPERIMENTS 47장)
DURATIONS = ("_05_10", "_10_15", "_15_20", "_20_05")
HORIZONS = (6, 18, 30)                   # 1 · 3 · 5시간
MAIN_HORIZONS = (18, 30)                 # 판정 지평
MIN_OBSERVED = 0.8                       # 창의 10분 칸 중 관측 비율
SIGN_P = 0.05
PICK_SAFE_FLOOR = 0.90                   # 5시간 수거 무해율 하한
SAT_RATIO = 0.9                          # 포화 = 거치대의 90% 이상(11번과 같다) — 찍기만
JUMP = 5                                 # 사후 진단: 10분 사이 이만큼 움직이면 트럭의 흔적으로 본다(등록 뒤 추가)
SEED = 42
DAY_TYPES = {"weekday": "평일", "holiday": "휴일"}


def quiet(func, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return func(*args, **kwargs)


def load_refs(conn, day_type: str) -> dict:
    """회차 → (그 계획의 rebalance_plan 행, 라벨). 요일 구분마다 가장 최근 계획 실행(운영 기간)."""
    # 라벨부터 고르고 그 행만 읽는다 — 행마다 하위 질의를 돌리면 rebalance_plan 전체를 훑느라 10분을 넘긴다
    runs = pd.read_sql("SELECT run_label, created_at FROM runs WHERE kind = 'plan' AND day_type = ? AND period = ?"
                       " ORDER BY created_at DESC", conn, params=(day_type, DEFAULT_PERIOD))
    out = {}
    for label in runs["run_label"]:
        durations = [d for (d,) in conn.execute(
            "SELECT DISTINCT duration FROM rebalance_plan WHERE run_label = ?", (label,)) if d not in out]
        for duration in durations:
            g = pd.read_sql("SELECT station_id, mu, sigma, parking_lot, stock, target_qty, rebal_qty FROM rebalance_plan"
                            " WHERE run_label = ? AND duration = ?", conn, params=(label, duration))
            out[duration] = (g.drop_duplicates("station_id").set_index("station_id"), label)
        if len(out) == len(DURATIONS):
            break
    return out


def load_net(day_type: str) -> pd.DataFrame:
    """운영 기간의 날짜별 시간 순수요(지금 DB) — 요일 구분 한쪽만."""
    path = DATA_ROOT / f"pp_data/순수요/st_net_daily ({DEFAULT_PERIOD}).csv"
    net, _ = db.read_step_output("net_demand", str(path), period=DEFAULT_PERIOD)
    return select_day_type(net.rename(columns={"date": "날짜"}), "날짜", day_type)


def current_stats(ref: pd.DataFrame, net: pd.DataFrame, duration: str) -> pd.DataFrame:
    """참조 계획의 거치대는 두고 mu · sigma만 **지금 순수요로** 운영 함수 build_stats()가 다시 낸다(계절 보정 없음).

    1.26.323 — 47장 첫 실측의 참조 계획 가운데 09-28 15시 이전 동시각 계획(평일 셋 · 휴일 넷)은 **깎인 대여이력**으로
    만든 순수요로 세운 것이었다(7c6f851). 09-30 22시 계획은 계절 보정이 걸리지 않았으므로(배율 1) 여기도 걸지 않는다.
    """
    init = ref[["stock", "parking_lot"]].reset_index()
    stats, _, _ = quiet(target_mod.build_stats, net, init, duration, verbose=False)
    out = ref.copy()
    fresh = stats.set_index("station_id")[["mu", "sigma"]]
    out[["mu", "sigma"]] = fresh.reindex(out.index).to_numpy()
    return out.dropna(subset=["mu", "sigma"])


def replan(ref: pd.DataFrame, stock: pd.Series) -> pd.Series:
    """참조 계획의 mu · sigma · 거치대에 새 재고를 넣어 운영 함수로 rebal_qty를 다시 낸다."""
    stats = ref[["mu", "sigma", "parking_lot"]].copy()
    stats["stock"] = stock.reindex(stats.index)
    stats = stats.dropna(subset=["stock", "mu", "sigma", "parking_lot"])
    out = quiet(target_mod.compute_rebal_qty, stats.copy())
    return out["rebal_qty"]


def sign_test(wins: int, losses: int) -> float:
    """단측 부호 검정 — P(X ≥ wins | n = wins + losses, 1/2)."""
    n = wins + losses
    if n == 0:
        return 1.0
    return sum(math.comb(n, k) for k in range(wins, n + 1)) / 2 ** n


def lowest(values: pd.Series, n: int, rng) -> pd.Index:
    """값이 작은 순 n곳. 같은 값은 무작위로(씨앗 고정)."""
    shuffled = values.sample(frac=1.0, random_state=rng.integers(1 << 31))
    return shuffled.sort_values(kind="stable").index[:n]


def matched_reserve(stock: pd.Series, total: float) -> pd.Series:
    """고정 여유 R — 재고가 많은 곳들에서 R대씩 남기고 뺄 때 총량이 `total`에 가장 가까운 R의 수거량."""
    best, best_gap = None, None
    for r in range(int(stock.max()) + 1):
        q = (stock - r).clip(lower=0)
        gap = abs(q.sum() - total)
        if best_gap is None or gap < best_gap:
            best, best_gap = q, gap
    return best[best > 0]


def window_stats(grid: pd.DataFrame, i: int, horizon: int):
    """t(행 i) 뒤 horizon틱의 창 — 대여소별 (최저, 최고, 0인 칸 비율, 관측 충분 여부)."""
    win = grid.iloc[i + 1:i + 1 + horizon]
    if len(win) < horizon:
        return None
    observed = win.notna().mean()
    return (win.min(), win.max(), (win == 0).sum() / win.notna().sum().replace(0, np.nan), observed >= MIN_OBSERVED,
            win)


def score_instance(rebal: pd.Series, s0: pd.Series, cap: pd.Series, ws, rng, mu: pd.Series) -> dict:
    """회차 하나 × 지평 하나의 점수."""
    lo, hi, zero_share, ok = ws[:4]
    ok = ok.reindex(rebal.index).fillna(False).astype(bool)
    s0 = s0.reindex(rebal.index)
    lo, hi = lo.reindex(rebal.index), hi.reindex(rebal.index)
    hit0 = lo == 0
    sat = hi >= SAT_RATIO * cap.reindex(rebal.index)
    row = {}

    # 배송 — t에 재고가 있던 곳만 판정에 쓴다(이미 빈 곳은 맞는 게 당연하다)
    pool = ok & (s0 > 0)
    drop = pool & (rebal > REBAL_MIN_QTY)
    n = int(drop.sum())
    row["배송수"] = n
    row["배송적중"] = int(hit0[drop].sum())
    row["B0풀"] = int(pool.sum())
    row["B0적중"] = int(hit0[pool].sum())
    if n:
        chosen = lowest(s0[pool], n, rng)
        row["B1배송적중"] = int(hit0[chosen].sum())
    else:
        row["B1배송적중"] = 0
    empty = ok & (s0 == 0) & (rebal > REBAL_MIN_QTY)
    row["빈배송수"] = int(empty.sum())
    row["빈배송_빈시간합"] = float(zero_share.reindex(rebal.index)[empty].sum())

    # 수거 — 수거량 q를 빼도 창 안에서 0이 안 됐을까
    pick = ok & (rebal < -REBAL_MIN_QTY)
    q = -rebal[pick]
    row["수거수"] = int(pick.sum())
    row["수거대수"] = float(q.sum())
    row["수거무해"] = int((lo[pick] >= q).sum())
    row["수거포화"] = int(sat[pick].sum())
    # 사후 진단(등록 뒤 추가 · 판정 아님) — 목표 재고 식의 두 갈래(mu ≥ 0: mu + z·sigma / mu < 0: 재고 + mu)별 무해와
    # 해의 크기(수거량을 뺀 궤적이 0 이하인 칸의 비율 = 빼 갔다면 비었을 시간)
    neg = pick & (mu.reindex(rebal.index) < 0)
    pos = pick & ~neg
    row["수거_mu음수"] = int(neg.sum())
    row["수거_mu음수_무해"] = int((lo[neg] >= q[neg[pick]]).sum()) if neg.any() else 0
    row["수거_mu양수"] = int(pos.sum())
    row["수거_mu양수_무해"] = int((lo[pos] >= q[pos[pick]]).sum()) if pos.any() else 0
    win = ws[4]
    if pick.any():
        below = win[q.index].le(q, axis=1).where(win[q.index].notna())
        row["수거_빈시간합"] = float((below.sum() / win[q.index].notna().sum()).sum())
    else:
        row["수거_빈시간합"] = 0.0
    # 사후 진단 — 공사 트럭의 흔적. 10분 사이 JUMP대 이상 준(수거) · 는(배송) 칸이 창에 있으면 사람 손으로 본다.
    # 수거 대상은 거의 다 차는 곳이라 공사도 거기서 빼 간다 — 관측 재고가 트럭 때문에 내려가면 우리 수거가 해로워 보인다
    step = pd.concat([s0.to_frame().T, win]).diff()
    jump_down = (step <= -JUMP).any().reindex(rebal.index).fillna(False).astype(bool)
    jump_up = (step >= JUMP).any().reindex(rebal.index).fillna(False).astype(bool)
    clean = pick & ~jump_down
    row["수거_흔적"] = int((pick & jump_down).sum())
    row["수거_흔적없음"] = int(clean.sum())
    row["수거_흔적없음_무해"] = int((lo[clean] >= q[clean[pick]]).sum()) if clean.any() else 0
    miss = drop & ~hit0
    row["배송빗나감"] = int(miss.sum())
    row["배송빗나감_흔적"] = int((miss & jump_up).sum())
    if len(q):
        everyone = ok & s0.notna()
        top = lowest(-s0[everyone], len(q), rng)              # 재고가 가장 많은 곳
        qb = matched_reserve(s0[top], q.sum())
        row["B1수거수"] = int(len(qb))
        row["B1수거대수"] = float(qb.sum())
        row["B1수거무해"] = int((lo[qb.index] >= qb).sum())
        row["B1수거포화"] = int(sat[qb.index].sum())
    else:
        row.update({"B1수거수": 0, "B1수거대수": 0.0, "B1수거무해": 0, "B1수거포화": 0})
    row["전체수"] = int(ok.sum())
    row["전체포화"] = int(sat[ok].sum())
    return row


def boot_ci(g: pd.DataFrame, num: str, den: str, num2: str = None, n: int = 2000, seed: int = SEED) -> tuple:
    """회차를 단위로 다시 뽑은(2,000번) 합친 비율의 95% 구간. num2를 주면 (num − num2)/den의 차이 구간.

    인용용 출력이다(1.26.325 추가 · 판정 아님). 대여소가 아니라 **회차**를 뽑는 까닭 — 같은 회차의 대여소들은 같은 날씨 ·
    같은 시각을 함께 겪어 서로 독립이 아니다.
    """
    rng = np.random.default_rng(seed)
    a = g[num].to_numpy(dtype=float)
    b = g[den].to_numpy(dtype=float)
    c = g[num2].to_numpy(dtype=float) if num2 else None
    idx = rng.integers(0, len(g), size=(n, len(g)))
    top = a[idx].sum(axis=1) - (c[idx].sum(axis=1) if c is not None else 0)
    vals = top / b[idx].sum(axis=1)
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def rate(num: float, den: float) -> float:
    return num / den if den else float("nan")


def report(rows: pd.DataFrame, day_type: str, verdict: bool) -> dict:
    print(f"\n## {DAY_TYPES[day_type]} — 회차 {rows['회차키'].nunique()}개"
          + ("" if verdict else " (찍기만 — 판정 아님)"))
    results = {}
    for h in HORIZONS:
        g = rows[rows["지평"] == h]
        d = g[g["배송수"] > 0]
        plan_d = rate(d["배송적중"].sum(), d["배송수"].sum())
        b0 = rate(g["B0적중"].sum(), g["B0풀"].sum())
        b1_d = rate(d["B1배송적중"].sum(), d["배송수"].sum())
        per_plan = d["배송적중"] / d["배송수"]
        per_b1 = d["B1배송적중"] / d["배송수"]
        wins, losses = int((per_plan > per_b1).sum()), int((per_plan < per_b1).sum())
        p_drop = sign_test(wins, losses)

        pk = g[g["수거수"] > 0]
        safe = rate(pk["수거무해"].sum(), pk["수거수"].sum())
        b1_safe = rate(pk["B1수거무해"].sum(), pk["B1수거수"].sum())
        sat_p = rate(pk["수거포화"].sum(), pk["수거수"].sum())
        sat_b1 = rate(pk["B1수거포화"].sum(), pk["B1수거수"].sum())
        sat_all = rate(g["전체포화"].sum(), g["전체수"].sum())
        empty = rate(g["빈배송_빈시간합"].sum(), g["빈배송수"].sum())

        drop_ok = plan_d > b0 and plan_d > b1_d and p_drop < SIGN_P
        pick_ok = safe >= b1_safe and (h != 30 or safe >= PICK_SAFE_FLOOR)
        results[h] = (drop_ok, pick_ok)
        print(f"\n### {h // 6}시간 뒤 ({h}틱)")
        print(f"  배송 적중 (t에 재고 > 0) : 계획 {plan_d:.1%} ({int(d['배송적중'].sum()):,}/{int(d['배송수'].sum()):,})"
              f" · B1 지금 재고 규칙 {b1_d:.1%} · B0 무작위 {b0:.1%}")
        print(f"    회차 부호 검정(계획 > B1): {wins}승 {losses}패 {len(d) - wins - losses}무 · 단측 p = {p_drop:.4f}")
        print(f"  이미 빈 배송 대여소 {int(g['빈배송수'].sum()):,}곳 — 창의 {empty:.1%}를 비어 있었다")
        print(f"  수거 무해 : 계획 {safe:.1%} ({int(pk['수거무해'].sum()):,}/{int(pk['수거수'].sum()):,}곳 · "
              f"{pk['수거대수'].sum():,.0f}대) · B1 고정 여유 {b1_safe:.1%} "
              f"({int(pk['B1수거수'].sum()):,}곳 · {pk['B1수거대수'].sum():,.0f}대)")
        print(f"  수거 대여소 포화 도달 : 계획 {sat_p:.1%} · B1 {sat_b1:.1%} · 전체 대여소 {sat_all:.1%}")
        print(f"  [사후 진단] 수거 무해 — mu < 0(목표 = 재고 + mu) "
              f"{rate(pk['수거_mu음수_무해'].sum(), pk['수거_mu음수'].sum()):.1%} ({int(pk['수거_mu음수'].sum()):,}곳) · "
              f"mu ≥ 0(목표 = mu + z·sigma) {rate(pk['수거_mu양수_무해'].sum(), pk['수거_mu양수'].sum()):.1%} "
              f"({int(pk['수거_mu양수'].sum()):,}곳) · 빼 갔다면 비었을 시간 = 창의 "
              f"{rate(pk['수거_빈시간합'].sum(), pk['수거수'].sum()):.1%}")
        print(f"  [사후 진단] 공사 트럭의 흔적(10분에 {JUMP}대 이상) — 수거 대여소 {rate(pk['수거_흔적'].sum(), pk['수거수'].sum()):.1%}"
              f"에 있었고, 흔적 없는 {int(pk['수거_흔적없음'].sum()):,}곳만 보면 수거 무해 "
              f"{rate(pk['수거_흔적없음_무해'].sum(), pk['수거_흔적없음'].sum()):.1%} · 빗나간 배송 대여소 중 채운 흔적 "
              f"{rate(d['배송빗나감_흔적'].sum(), d['배송빗나감'].sum()):.1%}")
        if len(d) > 1 and len(pk) > 1:
            lo_d, hi_d = boot_ci(d, "배송적중", "배송수")
            lo_x, hi_x = boot_ci(d, "배송적중", "배송수", "B1배송적중")
            lo_p, hi_p = boot_ci(pk, "수거무해", "수거수")
            print(f"  [인용] 95% 구간(회차 재표집 2,000번) — 배송 적중 {lo_d:.1%}~{hi_d:.1%} · 계획 − B1 "
                  f"{lo_x * 100:+.1f}~{hi_x * 100:+.1f}%p · 수거 무해 {lo_p:.1%}~{hi_p:.1%}")
        if verdict:
            print(f"  → 배송 {'✅' if drop_ok else '❌'} · 수거 {'✅' if pick_ok else '❌'}"
                  + ("" if h in MAIN_HORIZONS else "  (1시간은 판정 아님)"))
    if verdict:
        print(f"\n  회차별 3시간 배송 적중 — 계획 · B1 · B0")
        g = rows[rows["지평"] == 18]
        for dur, gg in g.groupby("회차"):
            dd = gg[gg["배송수"] > 0]
            print(f"    {dur}: 계획 {rate(dd['배송적중'].sum(), dd['배송수'].sum()):.1%} · "
                  f"B1 {rate(dd['B1배송적중'].sum(), dd['배송수'].sum()):.1%} · "
                  f"B0 {rate(gg['B0적중'].sum(), gg['B0풀'].sum()):.1%} · 회차 {len(gg)}개")
    print(f"\n  [인용] 회차별 — 배송 적중 계획 / B1 / B0 · 수거 무해 계획 / B1 (회차 수)")
    for h in HORIZONS:
        g = rows[rows["지평"] == h]
        for dur, gg in g.groupby("회차"):
            dd = gg[gg["배송수"] > 0]
            pp = gg[gg["수거수"] > 0]
            print(f"    {h // 6}시간 {dur}: 배송 {rate(dd['배송적중'].sum(), dd['배송수'].sum()):.1%} / "
                  f"{rate(dd['B1배송적중'].sum(), dd['배송수'].sum()):.1%} / {rate(gg['B0적중'].sum(), gg['B0풀'].sum()):.1%}"
                  f" · 수거 {rate(pp['수거무해'].sum(), pp['수거수'].sum()):.1%} / "
                  f"{rate(pp['B1수거무해'].sum(), pp['B1수거수'].sum()):.1%} ({len(gg)})")
    return results


def real_plans(conn, grid: pd.DataFrame) -> None:
    """실제 동시각 계획의 차량 계획(vrp_plan) 대여소로 같은 지표 — 찍기만."""
    runs = pd.read_sql("SELECT run_label, day_type FROM runs WHERE kind = 'plan' AND run_label LIKE '%동시각%'", conn)
    if runs.empty:
        print("\n## 실제 동시각 계획 — 없음")
        return
    vrp = pd.read_sql("SELECT run_label, duration, to_id, action, qty FROM vrp_plan", conn)
    vrp = vrp[vrp["run_label"].isin(runs["run_label"]) & vrp["action"].isin(["pick", "drop"])]
    stops = vrp.groupby(["run_label", "duration", "to_id", "action"], as_index=False)["qty"].sum()
    print(f"\n## 실제 동시각 계획 {runs['run_label'].nunique()}건의 차량 계획 대여소 (찍기만)")
    index = {ts: i for i, ts in enumerate(grid.index)}
    for day_type, labels in runs.groupby("day_type")["run_label"]:
        acc = {h: dict(d=0, dhit=0, d0=0, p=0, psafe=0) for h in HORIZONS}
        used = 0
        for label in labels:
            date_s, hour_s = label.split()[:2]
            t = pd.Timestamp(f"{date_s} {hour_s}:00")
            if t not in index:
                continue
            i = index[t]
            s0 = grid.iloc[i]
            used += 1
            st = stops[stops["run_label"] == label]
            for h in HORIZONS:
                ws = window_stats(grid, i, h)
                if ws is None:
                    continue
                lo, _, _, ok = ws[:4]
                for _, r in st.iterrows():
                    sid = r["to_id"]
                    if sid not in ok.index or not ok[sid] or pd.isna(s0.get(sid)):
                        continue
                    if r["action"] == "drop":
                        if s0[sid] > 0:
                            acc[h]["d"] += 1
                            acc[h]["dhit"] += int(lo[sid] == 0)
                        else:
                            acc[h]["d0"] += 1
                    else:
                        acc[h]["p"] += 1
                        acc[h]["psafe"] += int(lo[sid] >= r["qty"])
        print(f"  {DAY_TYPES.get(day_type, day_type)} — 계획 {used}건(t 관측 있음)")
        for h in HORIZONS:
            a = acc[h]
            print(f"    {h // 6}시간: 배송 적중 {rate(a['dhit'], a['d']):.1%} ({a['dhit']}/{a['d']}, 이미 빈 곳 {a['d0']}) · "
                  f"수거 무해 {rate(a['psafe'], a['p']):.1%} ({a['psafe']}/{a['p']})")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sanity", action="store_true", help="결과 없이 입력만 점검한다")
    parser.add_argument("--stats", choices=("plan", "current"), default="plan",
                        help="plan = 등록 그대로 참조 계획의 mu · sigma · current = 지금 순수요로 다시 계산(1.26.323 정정)")
    parser.add_argument("--until", help="이 시각 전의 관측만 쓴다(예: '2026-10-06 00:00') — 인용 값을 고정한다(1.26.325)")
    args = parser.parse_args(argv)

    with db.session() as conn:
        refs = {dt: load_refs(conn, dt) for dt in DAY_TYPES}
        if args.stats == "current":
            for dt in DAY_TYPES:
                net = load_net(dt)
                for d, (frame, lab) in list(refs[dt].items()):
                    fresh = current_stats(frame, net, d)
                    same = np.isclose(fresh["mu"], frame["mu"].reindex(fresh.index), atol=1e-6).mean()
                    print(f"  {DAY_TYPES[dt]} {d}: 지금 순수요로 다시 낸 mu가 '{lab}'의 mu와 같은 곳 {same:.1%}")
                    refs[dt][d] = (fresh, f"{lab} → 지금 순수요")
        raw = sf.load_grid(conn)
        if args.until:
            # 수집기는 계속 틱을 더한다 — 끝을 고정하지 않으면 같은 명령이 날마다 다른 값을 낸다(1.26.325)
            raw = raw[raw["ts"] < pd.Timestamp(args.until)]
        print(f"관측 자료: {raw['ts'].min()} ~ {raw['ts'].max()}" + (f" (--until {args.until})" if args.until else " (끝 고정 없음)"))
        stock = raw.pivot_table(index="ts", columns="station_id", values="stock")
        grid = stock.reindex(pd.date_range(stock.index.min().floor("D"), stock.index.max().ceil("D"),
                                           freq=TICK, inclusive="left"))
        for dt, ref in refs.items():
            print(f"참조 계획({DAY_TYPES[dt]}, {DEFAULT_PERIOD}): "
                  + " · ".join(f"{d} ← '{lab}'" for d, (_, lab) in sorted(ref.items())))
            for d, (frame, lab) in sorted(ref.items()):
                again = replan(frame, frame["stock"])
                same = (again == frame["rebal_qty"].reindex(again.index)).mean()
                print(f"  재계산 일치 {d}: {same:.1%} ({len(again):,}곳) — 100%가 아니면 mu · sigma가 계획이 쓴 값이 아니다")

        days = sf.dense_days(raw)
        hol = dict(zip(days, holiday_mask(pd.Series(pd.to_datetime(days)))))
        index = {ts: i for i, ts in enumerate(grid.index)}
        rng = np.random.default_rng(SEED)
        rows = []
        instances = {dt: 0 for dt in DAY_TYPES}
        for day in days:
            dt = "holiday" if hol[day] else "weekday"
            for dur in DURATIONS:
                if dur not in refs[dt]:
                    continue
                t = pd.Timestamp(day) + pd.Timedelta(hours=duration_hours(dur)[0])
                if t not in index:
                    continue
                s0 = grid.iloc[index[t]].dropna()
                if len(s0) < 0.5 * grid.shape[1]:
                    continue                                   # t에 수집이 거의 비었다
                ref, _ = refs[dt][dur]
                rebal = replan(ref, s0)
                instances[dt] += 1
                if args.sanity:
                    continue
                for h in HORIZONS:
                    ws = window_stats(grid, index[t], h)
                    if ws is None:
                        continue
                    row = score_instance(rebal, s0, ref["parking_lot"], ws, rng, ref["mu"])
                    row.update({"요일": dt, "회차": dur, "지평": h, "회차키": f"{day} {dur}"})
                    rows.append(row)
        print(f"계획 회차 — 평일 {instances['weekday']}개 · 휴일 {instances['holiday']}개 (하루 30틱 이상 · t 관측 50% 이상)")
        if args.sanity:
            print("--sanity: 결과는 계산하지 않았다")
            return 0

        frame = pd.DataFrame(rows)
        verdicts = report(frame[frame["요일"] == "weekday"], "weekday", verdict=True)
        if (frame["요일"] == "holiday").any():
            report(frame[frame["요일"] == "holiday"], "holiday", verdict=False)
        real_plans(conn, grid)

    print("\n## 판정 (사전 등록: 평일 3 · 5시간에서 배송 · 수거 모두 통과하면 '증명')")
    for h in MAIN_HORIZONS:
        drop_ok, pick_ok = verdicts[h]
        print(f"  {h // 6}시간: 배송 {'✅' if drop_ok else '❌'} · 수거 {'✅' if pick_ok else '❌'}")
    proven = all(all(verdicts[h]) for h in MAIN_HORIZONS)
    print(f"  → {'✅ 증명 — 계획의 진단이 몇 시간 뒤 실제와 맞았고, 지금 재고 규칙보다 낫다' if proven else '❌ 증명 안 됨 — 못 넘은 칸을 그대로 적는다'}")
    return 0


if __name__ == "__main__":
    project_config.exit_if_help(__doc__)
    raise SystemExit(main())
