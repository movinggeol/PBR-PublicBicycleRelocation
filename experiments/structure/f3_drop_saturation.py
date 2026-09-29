"""F3 — 곧 찰 곳에는 배송하지 않으면 포화가 줄까 (ML_고도화_계획 3장 F3, 1.26.305).

왜 재나 — 원고 6장: 제안 방법은 결품을 1.01~1.53h 줄이는 대신 포화를 0.16~0.37h 늘린다(<표 6-2>).
그 포화는 배송한 자전거가 창 안에서 거치대를 채워 반납을 막은 몫이다. ML 11번
(`saturation_forecast.py`)이 *"k틱 뒤 거치대의 90% 이상"* 을 맞히면, 곧 찰 곳을 배송 후보에서
거를 수 있다. 원고가 *"공짜가 아니다"* 라고 적은 대가를 직접 줄일 수 있는 유일한 후보다.

연결 방식 (첫 실측 전에 적는다 — 모두 재고, 결과를 본 뒤 하나만 고르지 않는다)
  C1  필터 : 배송 후보에서 P(포화, 창 길이) ≥ 0.5인 곳을 뺀다        ← 계획 문구 그대로
  C2  가중 : 배송 점수 = rebal_qty × (1.5 − P(포화, 창 길이))        ← F2 A2의 거울상(바닥 0.5)
  C1N 필터 : C1과 같은 필터를 **순수요 누적**으로 — (회차 시작 재고 − 창의 평균 순유출)이
            거치대의 90% 이상이면 뺀다. ML 없이 파이프라인이 이미 아는 것만으로 만든 짝이다
            (ML_고도화_계획 6장 규칙 1 — F1에서 ML의 이득처럼 보이던 것이 이 한 줄이었다).
  수거는 셋 다 현행이다.
  계획 문구의 *"또는 배송량을 거치대 − 예측 재고로 자른다"* 는 세우지 않는다 — 확률 모형은
  재고를 내지 않고, 재고 예측은 F1에서 ML이 지속 규칙을 넘지 못했다(4/12, ML_고도화_계획 F1).

어떻게 재나 — F2 하네스와 같다(`f2_candidate_priority.py`의 선정 · 날 · 출발 재고 · 궤적을 그대로 쓴다)
  · 날: 24시간을 온전히 덮은 평일. 날마다 회차 시작의 **관측 재고**를 출발 재고로 둔다.
  · 확률: 그날보다 **앞선 날만으로** 날마다 다시 학습한 11번 모형(k = 창 길이: 30틱 · 밤 54틱).
  · 계획: 현행 / C1 / C2 / C1N × 씨앗 42·7·13, 같은 군집 · ILP · VRP. 후보가 현행과 같으면
    계획도 같으므로 다시 풀지 않고 옮겨 적는다.
  · 결품 · 포화: step4와 같은 궤적 함수. 모집단은 회차 시작에 관측된 대여소 **전체**(중립).

채택 (사전 등록 — ML_고도화_계획 3장 F3 · 해석은 EXPERIMENTS 43장)
  ML 11번이 세 베이스라인을 모두 이긴 뒤에만 뜻이 있다.
  (가) 현행 대비 — 포화가 낮고(회차별 Wilcoxon 단측, n = 날짜 × 씨앗, p < 0.05)
       + 결품 +5% 안 + 이동거리 · 차량 수 +5% 안(F2와 같은 하네스의 비용 조건)
       + 평소 포화 빈도 구간마다 포화 평균이 현행 이하(둘 다 0인 구간은 건너뜀)
  (나) ML의 몫 — C1 · C2는 **C1N보다도** 포화가 낮아야 한다(같은 검정). (가)만 서면
       *"비-ML로 같은 이득"* 이라 ML은 채택하지 않는다. C1N이 (가)를 서면 비-ML 보정 후보로 적는다.
  **평일 20일이 차기 전에는 판정하지 않는다** — 그 전의 표는 착수 점검이다.

    python experiments/structure/f3_drop_saturation.py
    python experiments/structure/f3_drop_saturation.py --days 2026-09-21 --durations _10_15 --seeds 42
"""
from __future__ import annotations

import argparse
import io
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "baseline"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import project_config  # noqa: E402  — 콘솔 인코딩을 먼저 맞춘다(— · 이모지)
import baseline_compare as bc  # noqa: E402
import db  # noqa: E402
import f2_candidate_priority as f2  # noqa: E402
import saturation_forecast as satf  # noqa: E402
import stockout_forecast as sf  # noqa: E402
from project_config import DEFAULT_PERIOD, DURATIONS, duration_hours  # noqa: E402

# ── 사전 등록 (ML_고도화_계획 3장 F3 · EXPERIMENTS 43장) — 결과를 보고 바꾸지 않는다.
#    F2의 값을 가져다 쓰지 않고 여기 박는다 — F2를 고쳐도 F3의 등록은 움직이지 않게.
DROP_FULL_CUT = 0.5           # C1 · C1N: 배송 후보에서 뺄 포화 확률
C2_FLOOR = 0.5                # C2: 배송 점수 = rebal_qty × (C2_FLOOR + 1 − P)
SEEDS = (42, 7, 13)
ADOPT_MIN_DAYS = 20           # 채택 판정은 평일 20일부터
COST_TOLERANCE = 0.05         # 결품 · 이동거리 · 차량 수 +5% 안
WILCOXON_ALPHA = 0.05
BASE_RATE_EDGES = [0, 0.2, 0.4, 0.6, 0.8, 0.95, 1.01]   # 9번 · F2의 구간 그대로
ML_METHODS = ("C1", "C2")
NON_ML = "C1N"


# ───────────────────────────────────────────── 선정 — 배송 쪽만 바꾼다

def c1_select(rebal: pd.DataFrame, st_info: pd.DataFrame, p_full: pd.Series) -> pd.DataFrame:
    """C1 필터 — 곧 찰 곳(P ≥ 0.5)을 배송 후보에서 뺀다. 수거 · 자르기는 F2가 복사한 step1 그대로.

    C1N도 이 함수를 쓴다 — 확률만 순수요 누적(0/1)으로 바꿔 넘긴다.
    """
    full = set(p_full[p_full >= DROP_FULL_CUT].index)
    keep = ~(rebal["station_id"].isin(full) & (rebal["rebal_qty"] > 0))
    return f2.select_ordered(rebal[keep], st_info)


def c2_select(rebal: pd.DataFrame, st_info: pd.DataFrame, p_full: pd.Series) -> pd.DataFrame:
    """C2 가중 — 배송 점수 = rebal_qty × (1.5 − P). 곧 찰 곳을 자르는 자리 뒤로 민다."""
    return f2.select_ordered(
        rebal, st_info,
        drop_score=lambda d: d["rebal_qty"] * (C2_FLOOR + 1.0 - d["station_id"].map(p_full).fillna(0.0)))


def net_full(info: pd.DataFrame, mu: pd.DataFrame, duration: str) -> pd.Series:
    """C1N의 '확률' — (회차 시작 재고 − 창의 시간대별 평균 순유출 합) ≥ 거치대 × 0.9 이면 1.

    11번의 베이스라인 ③과 같은 식을 창 하나에 쓴 것이다. 순수요 통계에 없는 대여소는 유출 0 —
    지금 재고만으로 판정한다.
    """
    hours = duration_hours(duration)
    outflow = mu.reindex(info["station_id"])[hours].sum(axis=1).fillna(0.0).to_numpy()
    end = info["stock"].to_numpy(dtype=float) - outflow
    full = end >= satf.SAT_RATIO * info["parking_lot"].to_numpy(dtype=float)
    return pd.Series(full.astype(float), index=info["station_id"].to_numpy())


# ───────────────────────────────────────────── 확률

def predict_day(long: pd.DataFrame, day, starts: list) -> dict:
    """그날보다 **앞선 날만으로** 한 번 학습해 여러 회차 시작의 포화 확률을 낸다(F2와 같은 걷기).

    반환: {시작 시각: (그 시각 행 — station_id 색인 · P · 지금포화 · 타깃, 그 시(時)의 과거 포화 비율)}.
    타깃은 **참고 보고**(창 길이 확률이 지속 규칙보다 나은가)에만 쓴다 — 선정에는 들어가지 않는다.
    """
    train = long[long["ts"].dt.date < day]
    now = long[long["ts"].isin(starts)]
    train, now = satf.add_station_history(train, [train, now])
    model = satf.fit_model(train)
    now = now.assign(P=model.predict_proba(now[satf.FEATURES])[:, 1])
    out = {}
    for start in starts:
        at = now[now["ts"] == start].set_index("station_id")[["P", "지금포화", "타깃"]]
        base = train[train["시"] == start.hour].groupby("station_id")["지금포화"].mean()
        out[start] = (at, base)
    return out


# ───────────────────────────────────────────── 평가

def observed_full(frame, stations, start: datetime, duration: str) -> float:
    """고른 배송 후보 가운데 그 창에서 **한 번이라도 실제로 90% 이상 찬** 곳의 비율(보여 주기만 한다)."""
    if not len(stations):
        return float("nan")
    end = start + timedelta(hours=len(duration_hours(duration)))
    part = frame[(frame["ts"] >= start) & (frame["ts"] < end)
                 & frame["station_id"].isin(set(stations))]
    if part.empty:
        return float("nan")
    return float(part.groupby("station_id")["포화"].max().reindex(stations).fillna(0).mean())


def cost_ratio(mine: float, base: float) -> float:
    """짝 평균의 비 − 1. 기준이 0인데 늘었으면 무한대다 — 0으로 두면 '안 늘었다'가 된다."""
    if base:
        return mine / base - 1
    return 0.0 if not mine else float("inf")


def bins_not_worse(pair: pd.DataFrame, names: list, metric: str = "포화") -> bool:
    """평소 포화 빈도 구간마다 포화 평균이 기준 **이하**인가. 둘 다 0인 구간은 건너뛴다.

    ML 9번의 검사 ④를 계획에 옮긴 것(F2와 같은 규칙) — 늘 차는 곳 몇 군데를 비워 평균을
    끌어내린 것이 아닌지 본다.
    """
    for b in names:
        mine, base = pair[f"{metric}_{b}"].mean(), pair[f"{metric}_{b}_기준"].mean()
        if (pd.isna(mine) and pd.isna(base)) or (mine == 0 and base == 0):
            continue
        if pd.isna(mine) or pd.isna(base) or mine > base + 1e-12:
            return False
    return True


def paired(table: pd.DataFrame, duration: str, method: str, base_method: str, names: list):
    """`method` 대 `base_method`의 (날, 씨앗) 짝으로 (가)의 네 조건을 잰다. 짝이 없으면 None."""
    from scipy.stats import wilcoxon

    mine = table[(table["duration"] == duration) & (table["method"] == method)]
    base = table[(table["duration"] == duration) & (table["method"] == base_method)]
    pair = mine.merge(base, on=["day", "seed"], suffixes=("", "_기준"))
    if pair.empty:
        return None
    diff = pair["포화"] - pair["포화_기준"]
    try:
        p = float(wilcoxon(pair["포화"], pair["포화_기준"], alternative="less").pvalue) \
            if (diff != 0).any() else 1.0
    except ValueError:
        p = float("nan")
    cost = {k: cost_ratio(pair[k].mean(), pair[f"{k}_기준"].mean()) for k in ("결품", "km", "vehicles")}
    bins_ok = bins_not_worse(pair, names)
    passed = (diff.mean() < 0 and p < WILCOXON_ALPHA
              and all(v <= COST_TOLERANCE for v in cost.values()) and bins_ok)
    return {"n": len(pair), "포화차": float(diff.mean()), "p": p,
            **{f"비용_{k}": v for k, v in cost.items()}, "구간": bins_ok, "통과": passed}


def evaluate(rows: list, day_count: int) -> list:
    """사전 등록 채택 조건 (가) · (나). 평일이 `ADOPT_MIN_DAYS`보다 적으면 판정하지 않는다."""
    table = pd.DataFrame(rows)
    names = sorted(c[len("포화_"):] for c in table.columns if c.startswith("포화_["))
    verdicts = []
    for (duration, method), _part in table[table["method"] != "현행"].groupby(["duration", "method"]):
        vs_now = paired(table, duration, method, "현행", names)
        if vs_now is None:
            continue
        share = None
        if method in ML_METHODS:
            vs_net = paired(table, duration, method, NON_ML, names)
            share = bool(vs_net and vs_net["포화차"] < 0 and vs_net["p"] < WILCOXON_ALPHA)
        if day_count < ADOPT_MIN_DAYS:
            word = f"보류 — 평일 {day_count}일(판정은 {ADOPT_MIN_DAYS}일부터)"
        elif not vs_now["통과"]:
            word = "❌ 미달"
        elif method == NON_ML:
            word = "✅ 비-ML 보정 후보 (순수요 누적 필터)"
        elif not share:
            word = f"⚠️ 현행은 이기나 {NON_ML}보다 낫지 않다 — 비-ML로 같은 이득, ML 채택 아님"
        else:
            word = "✅ 채택 조건 통과"
        verdicts.append({"duration": duration, "method": method, **vs_now, "ML의몫": share, "판정": word})
    return verdicts


def bin_label(rate: float) -> str:
    for lo, hi in zip(BASE_RATE_EDGES[:-1], BASE_RATE_EDGES[1:]):
        if lo <= rate < hi:
            return f"[{lo:.2f},{min(hi, 1.0):.2f})"
    return f"[{BASE_RATE_EDGES[-2]:.2f},1.00)"


# ───────────────────────────────────────────── 실행

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--period", default=DEFAULT_PERIOD, help=f"순수요 기간 (기본 {DEFAULT_PERIOD})")
    parser.add_argument("--durations", default=",".join(DURATIONS))
    parser.add_argument("--days", default="", help="평가할 날만 (쉼표, YYYY-MM-DD)")
    parser.add_argument("--seeds", default=",".join(str(s) for s in SEEDS))
    args, _ = parser.parse_known_args(argv)

    durations = [d.strip() for d in args.durations.split(",") if d.strip()]
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    only = {datetime.strptime(d.strip(), "%Y-%m-%d").date()
            for d in args.days.split(",") if d.strip()} or None

    print("⚠️ ML 11번(saturation_forecast.py)이 세 베이스라인을 모두 이긴 뒤에만 뜻이 있는 표다.")
    step1 = bc.load_step1()
    project_config.align_day_type("weekday", bc.ilp_mod, bc.vrp_mod, bc.kpi_mod, step1)
    solver = bc.ilp_mod.build_solver()
    net, st_info, _warmup = bc.load_inputs(args.period, None, "weekday", 0, "")

    # 거치대 수는 계획과 같은 station_info에서 — 모형의 비율 피처와 계획의 자가 같아야 한다.
    capacity = pd.to_numeric(st_info.drop_duplicates("station_id").set_index("station_id")["parking_lot"],
                             errors="coerce")
    with db.session() as conn:
        frame = satf.label_saturation(sf.load_grid(conn), capacity)
        mu = satf.hourly_mu(conn, args.period)[False]          # 평가하는 날은 평일이다
    days = f2.eval_days(frame, only)
    print(f"\n평가할 평일 {len(days)}일: {', '.join(str(d) for d in days) or '없음'}"
          f" · 순수요 {args.period} · 씨앗 {seeds}")
    if not days:
        return 1

    # 창이 아직 안 닫힌 회차는 뺀다 — 타깃이 없어 피처 행이 통째로 빠진다(F2와 같은 까닭).
    last_tick = frame["ts"].max()
    rounds = [(day, d) for day in days for d in durations
              if f2.round_start(day, d) + timedelta(hours=len(duration_hours(d))) <= last_tick]
    probs = {}   # (day, duration) -> (그 시각 행, 과거 포화 비율)
    for horizon in sorted({f2.window_ticks(d) for d in durations}):
        long = satf.build_features(frame, horizon)
        for day in days:
            need = [d for (dd, d) in rounds if dd == day and f2.window_ticks(d) == horizon]
            if not need:
                continue
            got = predict_day(long, day, [f2.round_start(day, d) for d in need])
            for d in need:
                probs[(day, d)] = got[f2.round_start(day, d)]
        del long
        print(f"  확률 {horizon}틱 뒤 — 날마다 앞선 날로 학습 끝")

    rows, checks = [], []
    for day, duration in rounds:
        start = f2.round_start(day, duration)
        stock = f2.observed_start(frame, start)
        if stock.empty:
            print(f"  [건너뜀] {day} {duration}: 회차 시작 관측이 없다")
            continue
        info = st_info[st_info["station_id"].isin(stock.index)].copy()
        info["stock"] = info["station_id"].map(stock)
        stats, _d, _r = bc.quiet(bc.target_mod.build_stats, net,
                                 info[["station_id", "parking_lot", "stock"]], duration,
                                 warmup_net=None, warmup_days=0, verbose=False)
        rebal = bc.quiet(bc.target_mod.compute_rebal_qty, stats, z=None)
        buffer = io.StringIO()
        rebal.to_csv(buffer, index=False, encoding="utf-8")
        buffer.seek(0)
        current = bc.quiet(step1.select_top_unbalanced_st, buffer, duration, info)

        at, base_rate = probs[(day, duration)]
        fill = info["station_id"].map(base_rate).fillna(base_rate.mean() if len(base_rate) else 0.0)
        fill.index = info["station_id"].to_numpy()
        missing = int(at["P"].reindex(fill.index).isna().sum())
        p_full = at["P"].reindex(fill.index).fillna(fill)
        p_net = net_full(info, mu, duration)
        chosen = {"현행": current, "C1": c1_select(rebal, info, p_full),
                  "C2": c2_select(rebal, info, p_full), "C1N": c1_select(rebal, info, p_net)}
        bins = info["station_id"].map(base_rate).fillna(0.0).map(bin_label)
        bins.index = info["station_id"].to_numpy()

        # 참고 — 창 길이 확률이 이 회차들에서 베이스라인보다 나은가(판정에 쓰지 않는다)
        seen = at.dropna(subset=["타깃"])
        if len(seen):
            checks.append({"duration": duration, "n": len(seen),
                           "GBM": sf.brier(seen["P"], seen["타깃"]),
                           "지속": sf.brier(seen["지금포화"].astype(float), seen["타깃"]),
                           "빈도": sf.brier(seen.index.map(base_rate).fillna(fill.mean()), seen["타깃"]),
                           "순수요": sf.brier(p_net.reindex(seen.index).fillna(0.0), seen["타깃"])})

        current_drops = set(current.loc[current["rebal_qty"] > 0, "station_id"])
        cache = {}   # 후보가 같으면 계획도 같다(씨앗별) — 다시 풀지 않는다
        for seed in seeds:
            for method, cand in chosen.items():
                if cand.empty:
                    continue
                key = (tuple(cand["station_id"]), tuple(cand["rebal_qty"]), seed)
                if key not in cache:
                    _c, routes = bc.plan_with_clusters(cand.copy(), step1, solver, adjust=True, seed=seed)
                    cache[key] = (f2.per_station(net, info, bc.executed_delta(routes), duration),
                                  bc.route_stats(routes))
                each, stat = cache[key]
                drop_ids = cand.loc[cand["rebal_qty"] > 0, "station_id"].tolist()
                row = {"day": day, "duration": duration, "method": method, "seed": seed,
                       "결품": float(each["결품"].mean()), "포화": float(each["포화"].mean()),
                       "km": stat["km"], "vehicles": stat["vehicles"], "bikes": stat["bikes"],
                       "배송후보": len(drop_ids),
                       "현행과_다른_배송": len(set(drop_ids) ^ current_drops),
                       "배송후보_실제포화": observed_full(frame, drop_ids, start, duration)}
                for name, value in each["포화"].groupby(bins.reindex(each.index)).mean().items():
                    row[f"포화_{name}"] = float(value)
                rows.append(row)
        last = [r for r in rows if r["day"] == day and r["duration"] == duration]
        summary = " · ".join(
            f"{m} 포화 {np.mean([r['포화'] for r in last if r['method'] == m]):.3f}h"
            f"/결품 {np.mean([r['결품'] for r in last if r['method'] == m]):.3f}h"
            for m in chosen if any(r["method"] == m for r in last))
        note = f"  (확률이 없어 과거 빈도로 채운 곳 {missing})" if missing else ""
        print(f"  {day} {duration}: {summary}{note}")

    if not rows:
        print("결과가 없습니다.")
        return 1
    table = pd.DataFrame(rows)
    print("\n" + "=" * 100)
    print("회차 × 방법 평균 (중립 모집단 = 회차 시작에 관측된 대여소 전체)")
    print("=" * 100)
    agg = table.groupby(["duration", "method"])[["포화", "결품", "km", "vehicles", "bikes", "배송후보",
                                                 "현행과_다른_배송", "배송후보_실제포화"]].mean()
    print(agg.to_string(float_format=lambda v: f"{v:.3f}"))

    if checks:
        print("\n참고 — 창 길이 확률의 Brier (이 회차들의 시작 시각 · 판정에 쓰지 않는다)")
        ref = pd.DataFrame(checks).groupby("duration")[["GBM", "지속", "빈도", "순수요"]].mean()
        print(ref.to_string(float_format=lambda v: f"{v:.4f}"))

    print("\n판정 (사전 등록 — ML_고도화_계획 3장 F3 · (가) 현행 대비 · (나) C1N 대비)")
    for v in evaluate(rows, len(days)):
        share = "" if v["ML의몫"] is None else f"  (나) {'✅' if v['ML의몫'] else '❌'}"
        print(f"  {v['duration']:7} {v['method']:<3}  n={v['n']:>3}  포화 {v['포화차']:+.4f}h  p={v['p']:.3f}"
              f"  결품 {v['비용_결품']:+.1%} · km {v['비용_km']:+.1%} · 차량 {v['비용_vehicles']:+.1%}"
              f"  구간 {'✅' if v['구간'] else '❌'}{share}  → {v['판정']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
