"""작업 목록은 몇 시간 뒤에 실제로 맞았나 — 제안 방법과 다른 방법이 같은 시각 같은 재고로 낸 작업 목록을 그 뒤 관측 재고로 채점한다 (EXPERIMENTS 53장).

47장(plan_validity.py)은 운영 함수가 다시 낸 **진단**(rebal_qty가 ±2를 넘는 모든 대여소)을 채점했다. 차가 실제로 가는 곳은
그중 상위 후보를 군집 · ILP · VRP가 짝지은 일부다. 이 실험은 그 **작업 목록**(VRP 경로가 실제로 싣고 내린 대여소와 대수)을
채점하고, 논문 6장의 대조군이 같은 시각 같은 재고로 냈을 작업 목록과 맞댄다.

왜 저장된 재고로 되나 — 계획의 입력은 그 시각의 재고 · 거치대 · 26년 03월 통계뿐이다. 수집기는 계획이 부르는 것과 같은 타슈
API를 10분마다 저장하므로, 저장된 재고로 다시 세운 계획은 그 시각에 세웠을 계획과 같다(t 뒤의 정보는 들어가지 않는다). 그
시각에 실제로 낸 계획(동시각 계획)과 얼마나 같은지는 찍어서 보인다.

무엇을 증명하고 무엇을 못 하나 — 47장과 같다. 계획은 집행되지 않았으므로 관측 재고는 *"우리가 손대지 않은 세상"* 이고,
이 실험은 **진단이 맞았나**를 잰다. 집행했다면 결품이 몇 시간 줄었을지는 못 잰다.

사전 등록 (EXPERIMENTS 53장 — 결과 전 커밋. 바꾸지 않는다)
  자료     : 이 PC의 stock_history, 끝은 --until "2026-10-09 00:00"으로 고정. 회차 = 하루 30틱 이상인 날의 회차 시작 시각 t
             (05 · 10 · 15 · 20시)에 대여소 절반 이상이 관측된 것(47장과 같은 규칙)
  계획 입력 : t의 관측 재고 · 좌표와 거치대는 자료 끝 전 가장 최근 계획 실행의 station_info · mu · sigma는 지금 순수요(26년 03월)로
             운영 함수 build_stats()가 낸다(47장 --stats current와 같다)
  방법     : P 제안(군집 + 조정 → ILP → VRP) · B1 그리디 · B2 평균 목표재고(z = 0) · B3 지리 균등 군집 — baseline_compare.py의
             함수 그대로(씨앗 42) · S 지금 재고 규칙(P와 같은 수의 재고가 가장 적은 곳에 같은 총량을 고정 수준까지 배송 ·
             가장 많은 곳에서 같은 총량을 고정 여유로 수거) · R 무작위(관측된 모든 대여소의 비율 = 무작위로 골랐을 때의 기댓값)
  작업 목록 : VRP 경로가 실제로 싣고 내린 대수(executed_delta) — 내린 곳 = 배송, 실은 곳 = 수거
  지평     : t 뒤 30분 · 1 · 3 · 5시간(3 · 6 · 18 · 30틱). 그 창의 10분 칸이 80% 이상 관측된 대여소만 센다
  기준     : 주 — 빔 = 재고 0 · 꽉 참 = 재고 ≥ 거치대. 보조 — 2대 이하 · 거치대의 90% 이상
    배송 적중 : t에 기준보다 많던(주 > 0 · 보조 > 2) 배송 대여소가 창 안에 기준에 닿았나(주 0 · 보조 ≤ 2)
    수거 무해 : 창 안의 관측 재고에서 수거량 q를 빼도 기준에 안 닿았나(주 최저 − q ≥ 1 · 보조 최저 − q ≥ 3)
    수거 적중 : t에 기준 아래(주 < 거치대 · 보조 < 90%)이던 수거 대여소가 창 안에 기준에 닿았나 — 찍기만
    배송 넘침 : 창 안의 관측 재고에 배송량 d를 더하면 기준에 닿나(주 최고 + d ≥ 거치대 · 보조 ≥ 90%) — 찍기만
  판정     : 평일 · 주 기준 · 3 · 5시간.
    ① 진단(대 R · S) — 지평마다 배송 적중 P > R · P > S, 회차 부호 검정(P > S) 단측 p < 0.05, 수거 무해 P ≥ S.
       두 지평에서 모두 통과하면 '증명'(모두 통과해야 하는 교집합 판정이라 다중 비교 보정을 하지 않는다)
    ② 방법 비교(대 B1 · B2 · B3) — 지평 둘 × 지표 둘(배송 적중 · 수거 무해)의 차이 P − Bk를 회차 재표집 2,000번의
       98.3% 구간(0.05 / 3)으로 — 하한 > 0 앞섬 · 상한 < 0 뒤짐 · 나머지 비김. 앞섬 ≥ 1 · 뒤짐 0 → 'P가 낫다',
       뒤짐 ≥ 1 · 앞섬 0 → 'P가 못하다', 둘 다 → '갈린다', 넷 다 비김 → '구별 안 됨'
  찍기만   : 보조 기준 전부 · 30분 · 1시간 · 휴일 · 회차당 작업 대여소 수와 적중 개수 · 수거 적중 · 배송 넘침 · 47장 정의의 수거 무해
             (최저 ≥ q) · 이미 빈 배송 · 이미 찬 수거 대여소의 비율과 그 창의 빈(찬) 시간 · 공사 트럭 흔적(10분에 5대 이상)과 겹침 ·
             실제 동시각 계획과의 입력(재고) · 결과(작업 대여소) 일치

47장 정의와 다른 한 곳 — 47장은 수거 무해를 '최저 ≥ q'로 셌다. 남은 재고가 정확히 0인 순간도 무해로 친 셈이다(같은 파일의
'빼 갔다면 비었을 시간'은 0을 빔으로 센다). 이 장은 주 기준(0 = 빔)에 맞춰 '최저 − q ≥ 1'로 세고, 47장식도 함께 찍는다.

실행:
    python experiments/structure/worklist_validity.py --sanity   # 결과 없이 입력 · 작업 목록 크기 · 걸린 시간만
    python experiments/structure/worklist_validity.py            # 판정(등록 그대로)
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
import plan_validity as pv  # noqa: E402
import stockout_forecast as sf  # noqa: E402
from experiments._shared import load_baseline  # noqa: E402
from pipeline.step0_collect import calculate_target_qty as target_mod  # noqa: E402
from project_config import (DATA_ROOT, DEFAULT_PERIOD, DURATIONS, TICKS_PER_HOUR,  # noqa: E402
                            duration_start_hour, holiday_mask)

with contextlib.redirect_stdout(io.StringIO()):
    bc = load_baseline()

UNTIL = "2026-10-09 00:00"
HORIZONS = (3, 6, 18, 30)                # 30분 · 1 · 3 · 5시간
MAIN_HORIZONS = (18, 30)                 # 판정 지평
LOW_AUX = 2                              # 보조 기준 — 2대 이하
HIGH_AUX = pv.SAT_RATIO                  # 보조 기준 — 거치대의 90% 이상
METHODS = ("P", "B1", "B2", "B3")        # 군집 · ILP · VRP를 거치는 방법(baseline_compare.py)
ALL = METHODS + ("S",)
NAMES = {"P": "P 제안", "B1": "B1 그리디", "B2": "B2 평균 목표재고", "B3": "B3 지리 균등 군집",
         "S": "S 지금 재고 규칙", "R": "R 무작위"}
SIGN_P = 0.05
CI_ALPHA = 0.05 / 3                      # 대조군 셋
BOOT = 2000
OUT_DIR = DATA_ROOT / "exp_53_20261010"


def horizon_name(h: int) -> str:
    minutes = h * 60 // TICKS_PER_HOUR
    return f"{minutes}분" if minutes < 60 else f"{minutes // 60}시간"


def snapshot(conn, until: str):
    """자료 끝 전 가장 최근 계획 실행의 대여소 정보 — 좌표 · 거치대를 여기서 읽는다(재고는 회차마다 관측으로 갈아 끼운다)."""
    for (label,) in conn.execute("SELECT run_label FROM runs WHERE kind = 'plan' AND period = ? AND created_at < ?"
                                 " ORDER BY created_at DESC", (DEFAULT_PERIOD, str(pd.Timestamp(until)))):
        info = db.load_frame(conn, "station_info", run_label=label)
        if not info.empty:
            return label, info[bc.ST_INFO_COLUMNS].copy()
    raise SystemExit("자료 끝 전에 만든 계획 실행의 station_info가 없습니다")


def with_stock(info: pd.DataFrame, s0: pd.Series) -> pd.DataFrame:
    """대여소 정보의 재고를 t의 관측으로 바꾼다. 관측이 없는 대여소는 뺀다(순서는 그대로)."""
    out = info[info["station_id"].isin(s0.index)].copy()
    out["stock"] = s0.reindex(out["station_id"]).round().astype(int).to_numpy()
    return out


def worklists(net, info, duration, step1, solver) -> dict:
    """방법마다 VRP가 실제로 싣고 내린 대여소별 증감(+ 배송 · − 수거). baseline_compare.py의 함수 그대로."""
    empty = pd.Series(dtype=float)
    out = {m: empty for m in METHODS}
    base = bc.build_candidates(net, info, duration, None, pd.DataFrame(), 0, step1)
    if not base.empty:
        _, routes = bc.plan_with_clusters(base.copy(), step1, solver, adjust=True, seed=pv.SEED)
        out["P"] = bc.executed_delta(routes)
        out["B1"] = bc.executed_delta(bc.plan_greedy(base))
        _, routes = bc.plan_with_clusters(base.copy(), step1, solver, adjust=False, seed=pv.SEED)
        out["B3"] = bc.executed_delta(routes)
    zero = bc.build_candidates(net, info, duration, 0.0, pd.DataFrame(), 0, step1)
    if not zero.empty:
        _, routes = bc.plan_with_clusters(zero, step1, solver, adjust=True, seed=pv.SEED)
        out["B2"] = bc.executed_delta(routes)
    return {m: d[d != 0].astype(float) for m, d in out.items()}


def matched_fill(stock: pd.Series, total: float) -> pd.Series:
    """고정 채움 수준 L — 고른 곳들을 L대까지 채울 때 총량이 `total`에 가장 가까운 L의 배송량(matched_reserve의 거울)."""
    best, best_gap = None, None
    for level in range(int(stock.max()) + int(total) + 2):
        q = (level - stock).clip(lower=0)
        gap = abs(q.sum() - total)
        if best_gap is None or gap < best_gap:
            best, best_gap = q, gap
        if q.sum() > total:
            break
    return best[best > 0]


def rule_list(p: pd.Series, s0: pd.Series, cap: pd.Series, ok: pd.Series, rng) -> pd.Series:
    """S 지금 재고 규칙 — P의 채점 대상과 같은 수 · 같은 총량. 배송은 재고가 가장 적은 곳(t에 재고 > 0), 수거는 가장 많은 곳."""
    seen = ok[ok].index.intersection(s0.index).intersection(cap.dropna().index)
    p = p[p.index.isin(seen)]
    drop, pick = p[(p > 0) & (s0.reindex(p.index) > 0)], -p[p < 0]
    parts = []
    if len(drop):
        live = s0[seen][s0[seen] > 0]
        chosen = pv.lowest(live, len(drop), rng)
        parts.append(matched_fill(live[chosen], drop.sum()))
    if len(pick):
        top = pv.lowest(-s0[seen], len(pick), rng)
        parts.append(-pv.matched_reserve(s0[top], pick.sum()))
    return pd.concat(parts).astype(float) if parts else pd.Series(dtype=float)


def score(delta: pd.Series, s0: pd.Series, cap: pd.Series, ws) -> dict:
    """작업 목록 하나 × 지평 하나의 셈. 주 기준과 보조 기준을 함께 센다."""
    lo, hi, zero_share, ok, win = ws
    seen = ok[ok].index.intersection(s0.index).intersection(cap.dropna().index)
    delta = delta[delta.index.isin(seen)]
    drop, pick = delta[delta > 0], -delta[delta < 0]
    di, pi = drop.index, pick.index
    row = {"배송곳": len(drop), "배송대수": float(drop.sum()), "수거곳": len(pick), "수거대수": float(pick.sum())}
    for tag, floor in (("", 0), ("보조", LOW_AUX)):
        live = di[s0[di] > floor]
        row[f"배송{tag}수"] = len(live)
        row[f"배송{tag}적중"] = int((lo[live] <= floor).sum())
    # 이미 빈 배송 · 이미 찬 수거 — 적중 · 넘침의 분모에서 빠지는 곳. 창의 몇 %를 그대로 비어(차) 있었나를 따로 센다
    empty = di[s0[di] == 0]
    row["배송이미빔"] = len(empty)
    row["배송이미빔_빈시간합"] = float(zero_share.reindex(empty).fillna(0).sum())
    full = pi[s0[pi] >= cap[pi]]
    row["수거이미참"] = len(full)
    row["수거이미참_찬시간합"] = float((win[full].ge(cap[full], axis=1).where(win[full].notna()).sum()
                                    / win[full].notna().sum()).fillna(0).sum()) if len(full) else 0.0
    row["배송넘침"] = int((hi[di] + drop >= cap[di]).sum())
    row["배송넘침보조"] = int((hi[di] + drop >= HIGH_AUX * cap[di]).sum())
    rest = lo[pi] - pick
    row["수거무해"] = int((rest >= 1).sum())
    row["수거무해보조"] = int((rest >= LOW_AUX + 1).sum())
    row["수거무해47"] = int((rest >= 0).sum())
    for tag, ratio in (("", 1.0), ("보조", HIGH_AUX)):
        live = pi[s0[pi] < ratio * cap[pi]]
        row[f"수거적중{tag}수"] = len(live)
        row[f"수거적중{tag}"] = int((hi[live] >= ratio * cap[live]).sum())
    return row


def base_rates(s0: pd.Series, cap: pd.Series, lo, hi, ok) -> dict:
    """R 무작위 — 관측된 모든 대여소의 비율(무작위로 골랐을 때의 기댓값)."""
    seen = ok[ok].index.intersection(s0.index).intersection(cap.dropna().index)
    s, c = s0[seen], cap[seen]
    row = {}
    for tag, floor in (("", 0), ("보조", LOW_AUX)):
        live = seen[s > floor]
        row[f"배송{tag}수"] = len(live)
        row[f"배송{tag}적중"] = int((lo[live] <= floor).sum())
    for tag, ratio in (("", 1.0), ("보조", HIGH_AUX)):
        live = seen[s < ratio * c]
        row[f"수거적중{tag}수"] = len(live)
        row[f"수거적중{tag}"] = int((hi[live] >= ratio * c[live]).sum())
    return row


def traces(lists: dict, s0: pd.Series, win: pd.DataFrame, ok: pd.Series) -> dict:
    """공사 트럭의 흔적(10분에 JUMP대 이상) — 창 안에 채워진(↑) 곳 · 빠진(↓) 곳과 작업 목록의 겹침. 찍기만."""
    seen = ok[ok].index.intersection(s0.index)
    step = pd.concat([s0.reindex(win.columns).to_frame().T, win]).diff()
    up = (step >= pv.JUMP).any().reindex(seen).fillna(False)
    down = (step <= -pv.JUMP).any().reindex(seen).fillna(False)
    row = {"채움흔적": int(up.sum()), "뺌흔적": int(down.sum())}
    for m, delta in lists.items():
        delta = delta[delta.index.isin(seen)]
        di, pi = delta.index[delta > 0], delta.index[delta < 0]
        row[f"{m}_배송중채움"] = int(up[di].sum())
        row[f"{m}_수거중뺌"] = int(down[pi].sum())
    return row


def instances(raw: pd.DataFrame, grid: pd.DataFrame) -> list:
    """(날짜, 회차, 요일 구분, 격자 행 번호, t의 관측 재고) — 47장과 같은 규칙."""
    days = sf.dense_days(raw)
    hol = dict(zip(days, holiday_mask(pd.Series(pd.to_datetime(days)))))
    index = {ts: i for i, ts in enumerate(grid.index)}
    out = []
    for day in days:
        for dur in DURATIONS:
            t = pd.Timestamp(day) + pd.Timedelta(hours=duration_start_hour(dur))
            if t not in index:
                continue
            s0 = grid.iloc[index[t]].dropna()
            if len(s0) < 0.5 * grid.shape[1]:
                continue
            out.append((day, dur, "holiday" if hol[day] else "weekday", index[t], s0))
    return out


def plan_stats(conn, label: str, duration: str) -> pd.DataFrame:
    """그 계획 실행이 쓴 mu · sigma · 거치대 · 재고(rebalance_plan)."""
    g = pd.read_sql("SELECT station_id, mu, sigma, parking_lot, stock FROM rebalance_plan WHERE run_label = ? AND duration = ?",
                    conn, params=(label, duration))
    return g.drop_duplicates("station_id").reset_index(drop=True)


def p_from_stats(stats: pd.DataFrame, info: pd.DataFrame, duration: str, step1, solver) -> pd.Series:
    """주어진 mu · sigma · 재고로 운영 함수를 차례로 불러 P의 작업 목록을 낸다(build_candidates의 build_stats만 건너뛴다)."""
    rebal = pv.quiet(target_mod.compute_rebal_qty, stats.copy())
    buffer = io.StringIO()
    rebal.to_csv(buffer, index=False, encoding="utf-8")
    buffer.seek(0)
    cand = pv.quiet(step1.select_top_unbalanced_st, buffer, duration, info)
    if cand.empty:
        return pd.Series(dtype=float)
    _, routes = bc.plan_with_clusters(cand.copy(), step1, solver, adjust=True, seed=pv.SEED)
    d = bc.executed_delta(routes)
    return d[d != 0].astype(float)


def jaccard(a, b) -> float:
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else float("nan")


def real_plan_match(conn, grid, step1, solver, until, mine: dict) -> None:
    """실제 동시각 계획과의 일치 — 찍기만.

    a0 그 계획의 재고 + 그 계획의 통계 → 코드가 그때와 같으면 100%
    a  수집기 재고   + 그 계획의 통계 → 재고 차이(계획은 t + 3분에 API를 따로 불렀다)만큼 갈린다
    b  수집기 재고   + 지금 통계     → 이 실험이 쓰는 것. 09-28 15시 이전 동시각 계획은 깎인 대여이력의 통계로 섰다(1.26.323)
    """
    runs = pd.read_sql("SELECT run_label, day_type, duration, created_at FROM runs WHERE kind = 'plan'"
                       " AND run_label LIKE '%동시각%' AND created_at < ? ORDER BY created_at",
                       conn, params=(str(pd.Timestamp(until)),))
    print(f"\n## 실제 동시각 계획과의 일치 (찍기만) — {len(runs)}건")
    if runs.empty:
        return
    index = {ts: i for i, ts in enumerate(grid.index)}
    vrp = pd.read_sql("SELECT run_label, duration, to_id, action, qty FROM vrp_plan", conn)
    print("  계획 | 입력: 재고 같은 곳 · 평균 차 | 배송 · 수거 대여소 겹침(자카드) — a0 그 계획 재고+통계 · a 수집기 재고+그 계획 통계 · "
          "b 수집기 재고+지금 통계")
    for r in runs.itertuples():
        date_s, hour_s = r.run_label.split()[:2]
        t = pd.Timestamp(f"{date_s} {hour_s}:00")
        if t not in index:
            print(f"  {r.run_label}: t의 관측 없음")
            continue
        s0 = grid.iloc[index[t]].dropna()
        own = db.load_frame(conn, "station_info", run_label=r.run_label)[bc.ST_INFO_COLUMNS]
        both = own.set_index("station_id")["stock"].reindex(s0.index).dropna()
        same = (both == s0[both.index]).mean()
        gap = (both - s0[both.index]).abs().mean()
        issued = bc.executed_delta(vrp[(vrp["run_label"] == r.run_label) & (vrp["duration"] == r.duration)])
        issued = issued[issued != 0]
        stats = plan_stats(conn, r.run_label, r.duration)
        project_config.align_day_type(r.day_type, bc.ilp_mod, bc.vrp_mod, bc.kpi_mod, step1)
        a0 = p_from_stats(stats, own, r.duration, step1, solver)
        s_obs = stats.copy()
        s_obs["stock"] = s0.reindex(s_obs["station_id"]).to_numpy()
        s_obs = s_obs.dropna(subset=["stock"])
        a = p_from_stats(s_obs, with_stock(own, s0), r.duration, step1, solver)
        b = mine.get((t.date(), r.duration), pd.Series(dtype=float))
        cells = []
        for name, d in (("a0", a0), ("a", a), ("b", b)):
            cells.append(f"{name} {jaccard(d.index[d > 0], issued.index[issued > 0]):.0%}·"
                         f"{jaccard(d.index[d < 0], issued.index[issued < 0]):.0%}")
        print(f"  {r.run_label}: {same:.1%} · {gap:.2f}대 | " + " · ".join(cells)
              + f" (낸 계획 배송 {int((issued > 0).sum())} · 수거 {int((issued < 0).sum())}곳)")


def pooled(g: pd.DataFrame, num: str, den: str) -> float:
    return pv.rate(g[num].sum(), g[den].sum())


def boot_diff(a: pd.DataFrame, b: pd.DataFrame, num: str, den: str, alpha: float = CI_ALPHA) -> tuple:
    """회차를 단위로 다시 뽑은 합친 비율의 차이(a − b) 구간. a · b는 같은 회차 순서로 맞춰 둔다."""
    rng = np.random.default_rng(pv.SEED)
    an, ad = a[num].to_numpy(float), a[den].to_numpy(float)
    bn, bd = b[num].to_numpy(float), b[den].to_numpy(float)
    idx = rng.integers(0, len(a), size=(BOOT, len(a)))
    with np.errstate(invalid="ignore", divide="ignore"):
        vals = an[idx].sum(1) / ad[idx].sum(1) - bn[idx].sum(1) / bd[idx].sum(1)
    vals = vals[~np.isnan(vals)]
    return float(np.percentile(vals, 100 * alpha / 2)), float(np.percentile(vals, 100 * (1 - alpha / 2)))


def report(rows: pd.DataFrame, base: pd.DataFrame, day_type: str, verdict: bool) -> dict:
    keys = rows["회차키"].unique()
    print(f"\n## {pv.DAY_TYPES[day_type]} — 회차 {len(keys)}개" + ("" if verdict else " (찍기만 — 판정 아님)"))
    out = {}
    for h in HORIZONS:
        g = rows[rows["지평"] == h]
        r0 = base[base["지평"] == h]
        print(f"\n### {horizon_name(h)} 뒤 ({h}틱)")
        print("  방법 | 배송 적중 주 · 보조 | 수거 무해 주 · 보조 · 47장식 | 수거 적중 주 · 보조 | 배송 넘침 주 · 보조 | 회차당 배송곳 · 대수 · 적중 개수 | 수거곳 · 대수"
              " | 이미 빈 배송 비율 · 그 창의 빈 시간 | 이미 찬 수거 비율 · 그 창의 찬 시간")
        n = len(g["회차키"].unique())
        for m in ALL:
            x = g[g["방법"] == m]
            print(f"  {NAMES[m]:<14} | {pooled(x, '배송적중', '배송수'):.1%} · {pooled(x, '배송보조적중', '배송보조수'):.1%}"
                  f" | {pooled(x, '수거무해', '수거곳'):.1%} · {pooled(x, '수거무해보조', '수거곳'):.1%} · "
                  f"{pooled(x, '수거무해47', '수거곳'):.1%}"
                  f" | {pooled(x, '수거적중', '수거적중수'):.1%} · {pooled(x, '수거적중보조', '수거적중보조수'):.1%}"
                  f" | {pooled(x, '배송넘침', '배송곳'):.1%} · {pooled(x, '배송넘침보조', '배송곳'):.1%}"
                  f" | {x['배송곳'].sum() / n:.1f} · {x['배송대수'].sum() / n:.1f} · {x['배송적중'].sum() / n:.1f}"
                  f" | {x['수거곳'].sum() / n:.1f} · {x['수거대수'].sum() / n:.1f}"
                  f" | {pooled(x, '배송이미빔', '배송곳'):.1%} · {pooled(x, '배송이미빔_빈시간합', '배송이미빔'):.1%}"
                  f" | {pooled(x, '수거이미참', '수거곳'):.1%} · {pooled(x, '수거이미참_찬시간합', '수거이미참'):.1%}")
        print(f"  {NAMES['R']:<14} | {pooled(r0, '배송적중', '배송수'):.1%} · {pooled(r0, '배송보조적중', '배송보조수'):.1%}"
              f" |  —  | {pooled(r0, '수거적중', '수거적중수'):.1%} · {pooled(r0, '수거적중보조', '수거적중보조수'):.1%} |  —")

        p = g[g["방법"] == "P"].set_index("회차키").reindex(keys)
        s = g[g["방법"] == "S"].set_index("회차키").reindex(keys)
        d = p["배송수"] > 0
        per_p = p.loc[d, "배송적중"] / p.loc[d, "배송수"]
        per_s = s.loc[d, "배송적중"] / s.loc[d, "배송수"].replace(0, np.nan)
        wins, losses = int((per_p > per_s).sum()), int((per_p < per_s).sum())
        p_sign = pv.sign_test(wins, losses)
        rate_p, rate_s = pooled(p, "배송적중", "배송수"), pooled(s, "배송적중", "배송수")
        rate_r = pooled(r0, "배송적중", "배송수")
        safe_p, safe_s = pooled(p, "수거무해", "수거곳"), pooled(s, "수거무해", "수거곳")
        ok1 = rate_p > rate_r and rate_p > rate_s and p_sign < SIGN_P and safe_p >= safe_s
        print(f"  ① 대 R · S: 배송 적중 P {rate_p:.1%} · S {rate_s:.1%} · R {rate_r:.1%} · 부호 검정(P > S) {wins}승 {losses}패 "
              f"{int(d.sum()) - wins - losses}무 · 단측 p = {p_sign:.4f} | 수거 무해 P {safe_p:.1%} · S {safe_s:.1%}"
              + (f" → {'✅' if ok1 else '❌'}" if verdict and h in MAIN_HORIZONS else ""))
        cells = {}
        for k in ("B1", "B2", "B3"):
            b = g[g["방법"] == k].set_index("회차키").reindex(keys)
            parts = []
            for metric, num, den in (("배송 적중", "배송적중", "배송수"), ("수거 무해", "수거무해", "수거곳")):
                lo_, hi_ = boot_diff(p.fillna(0), b.fillna(0), num, den)
                diff = pooled(p, num, den) - pooled(b, num, den)
                mark = "앞섬" if lo_ > 0 else "뒤짐" if hi_ < 0 else "비김"
                cells[(k, metric)] = mark
                parts.append(f"{metric} {diff * 100:+.1f}%p [{lo_ * 100:+.1f}, {hi_ * 100:+.1f}] {mark}")
            print(f"  ② P − {NAMES[k]}: " + " · ".join(parts))
        out[h] = (ok1, cells)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sanity", action="store_true", help="결과 없이 입력 · 작업 목록 크기 · 걸린 시간만 찍는다")
    parser.add_argument("--until", default=UNTIL, help=f"이 시각 전의 관측만 쓴다(등록값 {UNTIL})")
    parser.add_argument("--limit", type=int, default=0, help="--sanity에서 회차를 이만큼만 돈다(시간 재기용)")
    args = parser.parse_args(argv)

    step1 = bc.load_step1()
    solver = bc.ilp_mod.build_solver()
    with db.session() as conn:
        label, info = snapshot(conn, args.until)
        nets = {dt: pv.load_net(dt) for dt in pv.DAY_TYPES}
        raw = sf.load_grid(conn)
        raw = raw[raw["ts"] < pd.Timestamp(args.until)]
        stock = raw.pivot_table(index="ts", columns="station_id", values="stock")
        grid = stock.reindex(pd.date_range(stock.index.min().floor("D"), stock.index.max().ceil("D"),
                                           freq=pv.TICK, inclusive="left"))
        cap = info.set_index("station_id")["parking_lot"].astype(float)
        cap = cap[cap > 0]
        print(f"관측 자료: {raw['ts'].min()} ~ {raw['ts'].max()} (--until {args.until})")
        print(f"대여소 정보: '{label}' {len(info):,}곳 · 거치대 > 0인 곳 {len(cap):,} · 관측 대여소 {grid.shape[1]:,}곳 중 "
              f"정보에 없는 곳 {len(set(grid.columns) - set(info['station_id'])):,}")
        todo = instances(raw, grid)
        print(f"회차 — 평일 {sum(x[2] == 'weekday' for x in todo)}개 · 휴일 {sum(x[2] == 'holiday' for x in todo)}개")
        if args.sanity and args.limit:
            todo = todo[:args.limit]

        rng = np.random.default_rng(pv.SEED)
        rows, base, trace_rows, mine = [], [], [], {}
        started = time.time()
        for k, (day, dur, dt, i, s0) in enumerate(todo, 1):
            project_config.align_day_type(dt, bc.ilp_mod, bc.vrp_mod, bc.kpi_mod, step1)
            inst = with_stock(info, s0)
            lists = worklists(nets[dt], inst, dur, step1, solver)
            mine[(day, dur)] = lists["P"]
            key = f"{day} {dur}"
            if args.sanity:
                sizes = " · ".join(f"{m} 배송 {int((d > 0).sum())}곳 {d[d > 0].sum():.0f}대 / 수거 {int((d < 0).sum())}곳"
                                   f" {-d[d < 0].sum():.0f}대" for m, d in lists.items())
                print(f"  [{k}/{len(todo)}] {key} {pv.DAY_TYPES[dt]} — {sizes} ({time.time() - started:.0f}초)")
                continue
            for h in HORIZONS:
                ws = pv.window_stats(grid, i, h)
                if ws is None:
                    continue
                lo, hi, _, ok, win = ws
                lists_h = dict(lists, S=rule_list(lists["P"], s0, cap, ok, rng))
                for m, delta in lists_h.items():
                    row = score(delta, s0, cap, ws)
                    row.update({"요일": dt, "회차": dur, "지평": h, "회차키": key, "방법": m})
                    rows.append(row)
                row = base_rates(s0, cap, lo, hi, ok)
                row.update({"요일": dt, "회차": dur, "지평": h, "회차키": key})
                base.append(row)
                if h == 30:
                    row = traces(lists_h, s0, win, ok)
                    row.update({"요일": dt, "회차키": key})
                    trace_rows.append(row)
            if k % 10 == 0:
                print(f"  {k}/{len(todo)} 회차 ({time.time() - started:.0f}초)", flush=True)
        if args.sanity:
            per = (time.time() - started) / max(len(todo), 1)
            print(f"--sanity: 회차당 {per:.1f}초 — 결과는 계산하지 않았다")
            real_plan_match(conn, grid, step1, solver, args.until, mine)
            return 0

        frame, base = pd.DataFrame(rows), pd.DataFrame(base)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        frame.to_csv(OUT_DIR / "rows.csv", index=False, encoding="utf-8-sig")
        base.to_csv(OUT_DIR / "base.csv", index=False, encoding="utf-8-sig")
        verdicts = report(frame[frame["요일"] == "weekday"], base[base["요일"] == "weekday"], "weekday", verdict=True)
        if (frame["요일"] == "holiday").any():
            report(frame[frame["요일"] == "holiday"], base[base["요일"] == "holiday"], "holiday", verdict=False)

        tr = pd.DataFrame(trace_rows)
        print("\n## 공사 트럭의 흔적과 겹침 — 5시간 창 (찍기만)")
        for dt, g in tr.groupby("요일"):
            print(f"  {pv.DAY_TYPES[dt]}: 회차당 채움 흔적 {g['채움흔적'].mean():.1f}곳 · 뺌 흔적 {g['뺌흔적'].mean():.1f}곳")
            for m in ALL:
                f = frame[(frame["요일"] == dt) & (frame["지평"] == 30) & (frame["방법"] == m)]
                print(f"    {NAMES[m]:<14} 배송 대여소 중 공사도 채운 곳 {pv.rate(g[f'{m}_배송중채움'].sum(), f['배송곳'].sum()):.1%}"
                      f" · 공사가 채운 곳 중 이 방법의 배송 {pv.rate(g[f'{m}_배송중채움'].sum(), g['채움흔적'].sum()):.1%}"
                      f" · 수거 대여소 중 공사도 뺀 곳 {pv.rate(g[f'{m}_수거중뺌'].sum(), f['수거곳'].sum()):.1%}")
        real_plan_match(conn, grid, step1, solver, args.until, mine)

    print("\n## 판정 (사전 등록: 평일 · 주 기준 · 3 · 5시간)")
    proven = all(verdicts[h][0] for h in MAIN_HORIZONS)
    for h in MAIN_HORIZONS:
        print(f"  ① {horizon_name(h)}: {'✅' if verdicts[h][0] else '❌'}")
    print(f"  ① → {'✅ 증명 — 작업 목록의 진단이 몇 시간 뒤 실제와 맞았고, 지금 재고 규칙 · 무작위보다 낫다' if proven else '❌ 증명 안 됨'}")
    for k in ("B1", "B2", "B3"):
        marks = [verdicts[h][1][(k, m)] for h in MAIN_HORIZONS for m in ("배송 적중", "수거 무해")]
        ahead, behind = marks.count("앞섬"), marks.count("뒤짐")
        word = ("P가 낫다" if ahead and not behind else "P가 못하다" if behind and not ahead
                else "갈린다" if ahead and behind else "구별 안 됨")
        print(f"  ② 대 {NAMES[k]}: 앞섬 {ahead} · 뒤짐 {behind} · 비김 {marks.count('비김')} → {word}")
    return 0


if __name__ == "__main__":
    project_config.exit_if_help(__doc__)
    raise SystemExit(main())
