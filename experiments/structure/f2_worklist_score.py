"""F2 덧붙임 — A1이 고른 작업 목록을 53장의 자로 채점한다: 곧 빌 곳에서 빼 오지 않으면 수거가 실제로 덜 해치나 (EXPERIMENTS 41장 덧붙임).

왜 재나 — 53장에서 제안 방법의 작업 목록이 단순 규칙에 지는 곳은 **수거 하나**였다(평일 3시간 무해 75.1 대 77.6%,
5시간 59.2 대 61.1%). A1(41장)은 수거 후보에서 *"1시간 뒤 비어 있을 확률(ML 9번) ≥ 0.5"* 인 곳을 빼므로 그 약점을
정면으로 겨눈다. 그런데 41장이 재는 것은 **시뮬레이션 결품**이고(채택 조건도 그것이다), A1의 수거가 **관측 재고에서**
덜 해쳤는지는 아무도 재지 않았다. 이 스크립트가 그것을 잰다. **F2의 채택 판정은 41장 그대로다** — 여기서 정하는 것은
관측 쪽을 어떻게 읽을지뿐이다.

무엇을 견주나 — 같은 회차 · 같은 관측 재고 · 같은 군집 · 조정 · ILP · VRP(씨앗 42 · 7 · 13)로 세운 작업 목록
  P    현행 순서 — step1 `select_top_unbalanced_st()` 그대로(53장의 P)
  A1   41장의 A1 그대로 — 수거 후보에서 P(빔, 1시간) ≥ 0.5 제외 · 배송은 P(빔, 1시간) 높은 순
  A1H  비-ML 짝 — A1과 같은 규칙에 확률 대신 **그 시(時)의 과거 빔 비율**(ML 9번의 베이스라인 ②, 같은 학습 날).
       그 시에 관측이 없던 대여소는 0으로 둔다(빼지 않는다 — `filled()`)
  U    덜 옮긴 몫 — P의 작업 목록에서 수거만 A1과 같은 총량으로 균등하게 줄인다(49장의 U와 같은 정수화).
       A1이 P보다 많이 빼는 회차는 P 그대로 둔다(늘리지 않는다)
  S    53장의 지금 재고 규칙을 **A1의 작업 목록에** 맞춘 것 — 같은 수 · 같은 총량

사전 등록 (EXPERIMENTS 41장 덧붙임 — 결과 전 커밋. 바꾸지 않는다)
  자료     : F2 판정과 같은 DB에서 같은 날 돌린다. 평가일은 41장의 규칙 그대로(`f2.eval_days` — 24시간의 80% 이상 관측된
             평일 · 앞선 학습 날 5일 이상), 회차는 그중 회차 시작 t에 대여소 절반 이상이 관측되고 t 뒤 5시간 창이 닫힌 것
  확률     : 41장 그대로 — 그날보다 앞선 날만으로 날마다 다시 학습한 ML 9번(1시간 뒤). 피처가 모자라 빠진 곳은 과거 빔
             비율로 채우고 곳 수를 찍는다
  계획 입력 : 53장 그대로 — t의 관측 재고 · 자료 끝 전 가장 최근 계획 실행의 station_info · 지금 순수요(26년 03월)로
             build_stats()(계절 보정 끔) · z 기본값
  채점     : 53장 `score()` 그대로 — 주 기준(0 = 빔). 수거 무해 = 창 안 관측 최저 − 수거량 ≥ 1 · 배송 적중 = t에 재고가
             있던 배송 대여소가 창 안에 0이 됐나. 씨앗 셋의 셈을 회차마다 합친다
  읽기     : 평일 · 3 · 5시간 **모두**에서
    ⓐ 덜 옮긴 몫을 넘나 — 수거 무해 A1 > U(합친 비율) + 회차 부호 검정(A1 > U) 단측 p < 0.05
    ⓑ ML의 몫이 있나   — 수거 무해 A1 > A1H(합친 비율) + 회차 부호 검정(A1 > A1H) 단측 p < 0.05
    ⓒ 규칙에 안 지나   — 수거 무해 A1 ≥ S(합친 비율)
    ⓓ 배송을 팔지 않나  — 배송 적중 A1이 P보다 1%p 넘게 낮지 않다
    이름 — ⓐ ✗ → "관측 이득은 덜 옮긴 몫뿐이다" · ⓐ ✓ ⓑ ✗ → "덜 해치지만 과거 빈도로도 된다 — ML의 몫이 없다" ·
           ⓐ ⓑ ✓ ⓒ ✗ → "ML의 몫은 있으나 지금 재고 규칙에는 못 미친다" · ⓐ ⓑ ⓒ ✓ → "관측으로도 A1의 수거가 덜
           해친다 — 53장의 약점을 메운다". ⓓ ✗이면 어느 이름 뒤에나 "· 배송 적중을 1%p 넘게 판다"를 붙인다
  찍기만   : 30분 · 1시간 · 보조 기준 · 47장식 무해 · 회차당 수거 · 배송 곳과 대수 · 회차당 해친 수거 곳 수 · P 대 A1의 무해
             (같은 자로 본 현행과의 차이 — 총량이 달라 판정에 쓰지 않는다) · 수거 진단에서 A1 · A1H 규칙이 뺀 곳 수(회차 평균)

알려진 한계 — 41장 · 53장과 같다. 계획은 집행되지 않았으므로 관측은 *"우리가 손대지 않은 세상"* 이고 공사의 실제 재배치가
섞인다(모든 방법에 같이 걸린다). A1은 배송 순서도 바꾸므로 수거만의 효과가 아니다 — 41장의 정의를 그대로 둔다.

실행:
    python experiments/structure/f2_worklist_score.py --sanity --limit 2   # 결과 없이 입력 · 목록 크기 · 걸린 시간만
    python experiments/structure/f2_worklist_score.py                     # 읽기(등록 그대로) — F2 판정과 같은 날
"""
import argparse
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
import worklist_validity as wv  # noqa: E402
import f2_candidate_priority as f2  # noqa: E402
import pick_path_target as ppt  # noqa: E402
from project_config import DATA_ROOT, DURATIONS, REBAL_MIN_QTY, duration_start_hour  # noqa: E402

# 계획 · 요일 맞추기는 53장 하네스의 대조군 모듈 **한 벌**로 한다. f2에서는 그 모듈을 쓰지 않는 함수(자르기 ·
# 평가일 · 확률)만 부른다 — f2가 따로 import한 baseline_compare에 기대면 요일 맞추기가 다른 벌에 걸릴 수 있다.
bc = wv.bc

# ── 사전 등록 (EXPERIMENTS 41장 덧붙임) — 결과를 보고 바꾸지 않는다
SEEDS = (42, 7, 13)                      # 41장과 같다
HORIZONS = wv.HORIZONS                   # 30분 · 1 · 3 · 5시간(53장과 같다)
MAIN_HORIZONS = wv.MAIN_HORIZONS         # 읽기 지평 3 · 5시간
SIGN_P = 0.05
DROP_TOLERANCE = 0.01                    # ⓓ 배송 적중이 P보다 이만큼 넘게 낮으면 '판다'
METHODS = ("P", "A1", "A1H")             # 군집 · ILP · VRP를 거치는 것
ALL = METHODS + ("U", "S")
NAMES = {"P": "P 현행 순서", "A1": "A1 ML 필터", "A1H": "A1H 과거 빈도 짝", "U": "U 균등 축소",
         "S": "S 지금 재고 규칙"}
OUT_DIR = DATA_ROOT / "exp_41w"


# ───────────────────────────────────────────── 목록

def uniform_to(p_delta: pd.Series, a1_delta: pd.Series) -> pd.Series:
    """U — P의 작업 목록에서 수거만 A1의 수거 총량으로 균등하게 줄인다. 배송은 P 그대로.

    A1이 P보다 많이 빼면 P 그대로 둔다(늘리지 않는다). 0대가 된 곳은 목록에서 빠진다.
    """
    pick = -p_delta[p_delta < 0]
    total = int(round(-a1_delta[a1_delta < 0].sum()))
    if pick.empty or total >= pick.sum():
        return p_delta.copy()
    shrunk = ppt.shrink_to(pick.astype(float), total)
    out = pd.concat([p_delta[p_delta > 0], -shrunk])
    return out[out != 0].astype(float)


def plan_delta(cand: pd.DataFrame, step1, solver, seed: int) -> pd.Series:
    """작업 대상 → 군집 · 조정 → ILP → VRP가 실제로 싣고 내린 대여소별 증감(+ 배송 · − 수거)."""
    if cand.empty:
        return pd.Series(dtype=float)
    _c, routes = bc.plan_with_clusters(cand.copy(), step1, solver, adjust=True, seed=seed)
    d = bc.executed_delta(routes)
    return d[d != 0].astype(float)


def candidates(rebal: pd.DataFrame, info: pd.DataFrame, step1, duration: str,
               p_hour: pd.Series, base: pd.Series) -> dict:
    """P · A1 · A1H의 작업 대상. P는 운영 step1, A1 · A1H는 41장의 같은 자르기에 순서만 바꾼 것."""
    buffer = io.StringIO()
    rebal.to_csv(buffer, index=False, encoding="utf-8")
    buffer.seek(0)
    return {"P": bc.quiet(step1.select_top_unbalanced_st, buffer, duration, info),
            "A1": f2.a1_select(rebal, info, p_hour),
            "A1H": f2.a1_select(rebal, info, base)}


# ───────────────────────────────────────────── 읽기

def per_round(rows: pd.DataFrame) -> pd.DataFrame:
    """씨앗 셋의 셈을 (회차, 지평, 방법)마다 합친다."""
    keep = ["수거곳", "수거무해", "수거무해보조", "수거무해47", "수거대수", "배송곳", "배송대수", "배송수", "배송적중"]
    return rows.groupby(["회차키", "지평", "방법"], as_index=False)[keep].sum()


def sign_vs(g: pd.DataFrame, a: str, b: str) -> tuple:
    """회차마다 수거 무해 비율을 견준 (승, 패, 단측 p). 어느 한쪽이라도 수거가 없는 회차는 뺀다."""
    x = g[g["방법"] == a].set_index("회차키")
    y = g[g["방법"] == b].set_index("회차키")
    keys = x.index.intersection(y.index)
    x, y = x.loc[keys], y.loc[keys]
    both = (x["수거곳"] > 0) & (y["수거곳"] > 0)
    rx = x.loc[both, "수거무해"] / x.loc[both, "수거곳"]
    ry = y.loc[both, "수거무해"] / y.loc[both, "수거곳"]
    wins, losses = int((rx > ry).sum()), int((rx < ry).sum())
    return wins, losses, pv.sign_test(wins, losses)


def judge(g: pd.DataFrame) -> dict:
    """한 지평의 ⓐ ~ ⓓ. g는 `per_round()`의 그 지평 행."""
    def rate(m, num, den):
        x = g[g["방법"] == m]
        return pv.rate(x[num].sum(), x[den].sum())

    safe = {m: rate(m, "수거무해", "수거곳") for m in ALL}
    hit = {m: rate(m, "배송적중", "배송수") for m in ("P", "A1")}
    out = {"safe": safe, "hit": hit}
    for tag, other in (("a", "U"), ("b", "A1H")):
        w, l, p = sign_vs(g, "A1", other)
        out[tag] = bool(safe["A1"] > safe[other] and p < SIGN_P)
        out[f"{tag}_sign"] = (w, l, p)
    out["c"] = bool(safe["A1"] >= safe["S"])
    out["d"] = bool(hit["A1"] >= hit["P"] - DROP_TOLERANCE)
    return out


def reading(a: bool, b: bool, c: bool, d: bool) -> str:
    """등록한 이름 — 모든 조합에 이름이 있다(49장의 함정을 되풀이하지 않는다)."""
    if not a:
        word = "관측 이득은 덜 옮긴 몫뿐이다"
    elif not b:
        word = "덜 해치지만 과거 빈도로도 된다 — ML의 몫이 없다"
    elif not c:
        word = "ML의 몫은 있으나 지금 재고 규칙에는 못 미친다"
    else:
        word = "관측으로도 A1의 수거가 덜 해친다 — 53장의 약점을 메운다"
    return word + ("" if d else " · 배송 적중을 1%p 넘게 판다")


# ───────────────────────────────────────────── 자료

def rounds(raw: pd.DataFrame, grid: pd.DataFrame, durations, only=None) -> list:
    """(날, 회차, 격자 행 번호, t의 관측 재고) — 41장의 평가일 × 53장의 회차 규칙 · 5시간 창이 닫힌 것만."""
    index = {ts: i for i, ts in enumerate(grid.index)}
    last = raw["ts"].max()
    out = []
    for day in f2.eval_days(raw, only):
        for dur in durations:
            t = pd.Timestamp(day) + pd.Timedelta(hours=duration_start_hour(dur))
            if t not in index or t + max(HORIZONS) * pv.TICK > last:
                continue
            s0 = grid.iloc[index[t]].dropna()
            if len(s0) < 0.5 * grid.shape[1]:
                continue
            out.append((day, dur, index[t], s0))
    return out


def probabilities(raw: pd.DataFrame, todo: list) -> dict:
    """(날, 회차) → (P(빔, 1시간), 그 시의 과거 빔 비율). 날마다 그날보다 앞선 날로만 학습한다(41장)."""
    long = sf.build_features(raw, f2.A1_HORIZON)
    out = {}
    for day in sorted({x[0] for x in todo}):
        need = [dur for (d, dur, _i, _s) in todo if d == day]
        got = f2.predict_day(long, day, [f2.round_start(day, dur) for dur in need])
        for dur in need:
            out[(day, dur)] = got[f2.round_start(day, dur)]
    return out


def excluded(rebal: pd.DataFrame, p: pd.Series) -> int:
    """수거 진단(재배치량 < −REBAL_MIN_QTY) 가운데 그 확률로 A1 규칙이 빼는 곳 수 — 찍기만.

    A1의 수거가 줄어드는 길은 둘이다. 이 규칙이 빼는 것, 그리고 배송 순서가 바뀌어 짝 맞추기(`cut`)가 줄어드는 것.
    뒤쪽이면 A1은 큰 수거부터 남기므로(누적 자르기) 대여소당 수거량이 오히려 클 수 있다 — 어느 쪽인지 보이려고 센다.
    """
    ids = rebal.loc[rebal["rebal_qty"] < -REBAL_MIN_QTY, "station_id"]
    return int((ids.map(p) >= f2.PICK_EMPTY_CUT).sum())


def filled(p: pd.Series, base: pd.Series, ids) -> tuple:
    """(A1의 확률, A1H의 과거 빈도, 확률을 채운 곳 수, 과거 빈도가 없는 곳 수).

    A1의 확률은 41장 그대로 채운다 — 빠진 곳은 그 대여소의 과거 빔 비율, 그것도 없으면 그 시의 평균, 시 전체가
    없으면 0.5. **A1H의 과거 빈도는 채우지 않고 0으로 둔다** — 같은 채움을 쓰면 그 시(時)에 학습 행이 하나도 없는
    날(24시간 수집 전, 09-15 `_05_10`)에 모든 곳이 0.5가 되어 수거 후보가 통째로 빠진다(등록 전 입력 점검에서 봤다).
    모르면 현행대로 둔다는 뜻이고, 그래서 그런 회차의 A1H는 P와 같다.
    """
    ids = pd.Index(ids)
    fill = pd.Series(ids, index=ids).map(base).fillna(base.mean() if len(base) else 0.5)
    missing = int(p.reindex(ids).isna().sum())
    known = base.reindex(ids)
    return p.reindex(ids).fillna(fill), known.fillna(0.0), missing, int(known.isna().sum())


# ───────────────────────────────────────────── 실행

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sanity", action="store_true", help="결과 없이 입력 · 작업 목록 크기 · 걸린 시간만 찍는다")
    parser.add_argument("--limit", type=int, default=0, help="--sanity에서 회차를 이만큼만 돈다(시간 재기용)")
    parser.add_argument("--until", default="", help="이 시각 전의 관측만 쓴다(기본: DB 전부 — F2 판정과 같은 날 돌린다)")
    parser.add_argument("--durations", default=",".join(DURATIONS))
    parser.add_argument("--days", default="", help="이 날만 (쉼표, YYYY-MM-DD) — 시간 재기용")
    args = parser.parse_args(argv)

    durations = [d.strip() for d in args.durations.split(",") if d.strip()]
    only = {pd.Timestamp(d.strip()).date() for d in args.days.split(",") if d.strip()} or None
    step1 = bc.load_step1()
    project_config.align_day_type("weekday", bc.ilp_mod, bc.vrp_mod, bc.kpi_mod, step1)
    solver = bc.ilp_mod.build_solver()
    net = pv.load_net("weekday")

    with db.session() as conn:
        raw = sf.load_grid(conn)
        until = args.until or str(raw["ts"].max() + pv.TICK)
        raw = raw[raw["ts"] < pd.Timestamp(until)]
        label, info = wv.snapshot(conn, until)
    stock = raw.pivot_table(index="ts", columns="station_id", values="stock")
    grid = stock.reindex(pd.date_range(stock.index.min().floor("D"), stock.index.max().ceil("D"),
                                       freq=pv.TICK, inclusive="left"))
    cap = info.set_index("station_id")["parking_lot"].astype(float)
    cap = cap[cap > 0]
    todo = rounds(raw, grid, durations, only)
    days = sorted({x[0] for x in todo})
    print(f"관측 자료: {raw['ts'].min()} ~ {raw['ts'].max()} (끝 {until})")
    print(f"대여소 정보: '{label}' {len(info):,}곳 · 거치대 > 0인 곳 {len(cap):,}")
    print(f"평가일(41장 규칙) {len(days)}일: {', '.join(str(d) for d in days) or '없음'}")
    print(f"회차 {len(todo)}개 · 씨앗 {list(SEEDS)}")
    if not todo:
        return 1
    if args.sanity and args.limit:
        todo = todo[:args.limit]

    started = time.time()
    probs = probabilities(raw, todo)
    print(f"확률: 날마다 앞선 날로 학습 끝 ({time.time() - started:.0f}초)")

    rng = np.random.default_rng(pv.SEED)
    rows = []
    for k, (day, dur, i, s0) in enumerate(todo, 1):
        inst = wv.with_stock(info, s0)
        stats, _d, _r = bc.quiet(bc.target_mod.build_stats, net, inst[["station_id", "parking_lot", "stock"]], dur,
                                 warmup_net=None, warmup_days=0, verbose=False)
        rebal = bc.quiet(bc.target_mod.compute_rebal_qty, stats, z=None)
        p1, base, missing, unknown = filled(*probs[(day, dur)], inst["station_id"].to_numpy())
        cands = candidates(rebal, inst, step1, dur, p1, base)
        key = f"{day} {dur}"
        ex_a1, ex_h = excluded(rebal, p1), excluded(rebal, base)
        if args.sanity:
            sizes = " · ".join(f"{m} 수거 후보 {int((c['rebal_qty'] < 0).sum())}곳 {-c.loc[c['rebal_qty'] < 0, 'rebal_qty'].sum():.0f}대"
                               for m, c in cands.items())
            print(f"  [{k}/{len(todo)}] {key} — {sizes} · 수거 진단에서 뺀 곳 A1 {ex_a1} · A1H {ex_h}"
                  f" · 확률을 채운 곳 {missing} · 과거 빈도가 없는 곳 {unknown} ({time.time() - started:.0f}초)")
            continue
        for seed in SEEDS:
            deltas = {m: plan_delta(c, step1, solver, seed) for m, c in cands.items()}
            deltas["U"] = uniform_to(deltas["P"], deltas["A1"])
            for h in HORIZONS:
                ws = pv.window_stats(grid, i, h)
                if ws is None:
                    continue
                lists = dict(deltas, S=wv.rule_list(deltas["A1"], s0, cap, ws[3], rng))
                for m, delta in lists.items():
                    row = wv.score(delta, s0, cap, ws)
                    row.update({"회차키": key, "회차": dur, "지평": h, "방법": m, "씨앗": seed, "확률채움": missing,
                                "빈도없음": unknown, "A1제외": ex_a1, "A1H제외": ex_h})
                    rows.append(row)
        if k % 5 == 0:
            print(f"  {k}/{len(todo)} 회차 ({time.time() - started:.0f}초)", flush=True)

    if args.sanity:
        print(f"--sanity: 회차당 {(time.time() - started) / max(len(todo), 1):.1f}초 — 결과는 계산하지 않았다")
        return 0

    frame = pd.DataFrame(rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    frame.to_csv(OUT_DIR / "rows.csv", index=False, encoding="utf-8-sig")
    once = frame.drop_duplicates("회차키")
    print(f"\n수거 진단에서 확률 규칙이 뺀 곳(회차 평균) — A1 {once['A1제외'].mean():.1f} · A1H {once['A1H제외'].mean():.1f}"
          f" · 확률을 과거 빈도로 채운 곳 합 {int(once['확률채움'].sum())}"
          f" · 과거 빈도가 없어 A1H가 빼지 않은 곳 합 {int(once['빈도없음'].sum())}")
    agg = per_round(frame)
    verdicts = {}
    for h in HORIZONS:
        g = agg[agg["지평"] == h]
        if g.empty:
            continue
        n = g["회차키"].nunique()
        print(f"\n### {wv.horizon_name(h)} 뒤 ({h}틱) — 회차 {n}개 · 씨앗 {len(SEEDS)}")
        print("  방법 | 수거 무해 주 · 보조 · 47장식 | 배송 적중 | 회차당 수거곳 · 대수 · 해친 곳 | 배송곳 · 대수")
        for m in ALL:
            x = g[g["방법"] == m]
            print(f"  {NAMES[m]:<14} | {pv.rate(x['수거무해'].sum(), x['수거곳'].sum()):.1%} · "
                  f"{pv.rate(x['수거무해보조'].sum(), x['수거곳'].sum()):.1%} · {pv.rate(x['수거무해47'].sum(), x['수거곳'].sum()):.1%}"
                  f" | {pv.rate(x['배송적중'].sum(), x['배송수'].sum()):.1%}"
                  f" | {x['수거곳'].sum() / n / len(SEEDS):.1f} · {x['수거대수'].sum() / n / len(SEEDS):.1f} · "
                  f"{(x['수거곳'] - x['수거무해']).sum() / n / len(SEEDS):.1f}"
                  f" | {x['배송곳'].sum() / n / len(SEEDS):.1f} · {x['배송대수'].sum() / n / len(SEEDS):.1f}")
        v = judge(g)
        verdicts[h] = v
        for tag, other in (("a", "U"), ("b", "A1H")):
            w, l, p = v[f"{tag}_sign"]
            print(f"  {'ⓐ' if tag == 'a' else 'ⓑ'} A1 {v['safe']['A1']:.1%} 대 {NAMES[other]} {v['safe'][other]:.1%} — "
                  f"회차 {w}승 {l}패 · 단측 p = {p:.4f}" + (f" → {'✅' if v[tag] else '❌'}" if h in MAIN_HORIZONS else ""))
        print(f"  ⓒ A1 {v['safe']['A1']:.1%} 대 S {v['safe']['S']:.1%}"
              + (f" → {'✅' if v['c'] else '❌'}" if h in MAIN_HORIZONS else ""))
        print(f"  ⓓ 배송 적중 A1 {v['hit']['A1']:.1%} 대 P {v['hit']['P']:.1%}"
              + (f" → {'✅' if v['d'] else '❌'}" if h in MAIN_HORIZONS else ""))
        w, l, p = sign_vs(g, "A1", "P")
        print(f"  (찍기만) 수거 무해 A1 대 P — 회차 {w}승 {l}패 · 단측 p = {p:.4f} · 총량이 달라 읽기에 쓰지 않는다")

    print("\n## 읽기 (사전 등록: 평일 · 주 기준 · 3 · 5시간 모두)")
    if not all(h in verdicts for h in MAIN_HORIZONS):
        print("  3 · 5시간 창이 관측된 회차가 없어 읽지 않는다")
        return 1
    marks = {t: all(verdicts[h][t] for h in MAIN_HORIZONS) for t in "abcd"}
    print("  " + " · ".join(f"{s} {'✅' if marks[t] else '❌'}" for s, t in zip("ⓐⓑⓒⓓ", "abcd")))
    print(f"  → {reading(**marks)}")
    print("  (F2의 채택 판정은 41장의 시뮬레이션 조건 그대로다 — 이 읽기는 관측 쪽의 설명이다)")
    return 0


if __name__ == "__main__":
    project_config.exit_if_help(__doc__)
    raise SystemExit(main())
