"""F2 — 결품 확률로 작업 대상의 **순서**를 바꾸면 결품이 줄까 (ML_고도화_계획 3장 F2, 1.26.301).

왜 재나 — step1 `select_top_unbalanced_st()`는 `|rebal_qty|`가 큰 순으로 상위 N=50을 자른다.
*"많이 모자란 곳"* 과 *"곧 빌 곳"* 은 다르다. ML 9번(`stockout_forecast.py`)이 *"1시간 뒤 비어 있을
확률"* 을 맞혔고(09-28 재학습: Brier 지속 규칙 대비 −19.9%, 보정 맞음, 누출 없음) 아직 계획에
잇지 않았다. 여기서 두 연결 방식을 **현행과 같은 군집·ILP·VRP로** 계획해 결품을 잰다.

연결 방식 (ML_고도화_계획 3장에 먼저 적었다 — 둘 다 재고, 결과를 본 뒤 하나만 고르지 않는다)
  A1 필터 : 수거 후보에서 P(빔, 1시간) ≥ 0.5인 곳을 뺀다 · 배송 후보는 P(빔, 1시간)가 높은 순
  A2 가중 : 배송 점수 = rebal_qty × (0.5 + P(빔, 창 길이)) 로 상위 N을 자른다 · 수거는 현행

어떻게 재나
  · 날: 24시간을 온전히 덮은 **평일**. 날마다 회차 시작 시각의 **관측 재고**를 출발 재고로 둔다.
  · 확률: 그날보다 **앞선 날만으로** 학습한 모형(날마다 다시 학습 — 누출을 막는 걷기 검증).
  · 계획: 현행 / A1 / A2 × 씨앗 42·7·13. 순수요·목표 재고는 운영과 같은 기간 통계다.
  · 결품·포화: step4와 같은 궤적 함수. **모집단은 관측된 대여소 전체**(중립) — A1·A2는 후보
    집합을 바꾸므로 자기 후보로 평균 내면 자가 달라진다(pbr-pipeline 스킬 '중립 모집단').
  · 관측 대조: 고른 배송 후보가 그 창에서 실제로 비었는지(재배치가 집행되지 않으므로 반사실에 가깝다).

채택 (사전 등록 — ML_고도화_계획 3장 F2 · 해석은 EXPERIMENTS 41장)
  결품이 현행보다 낮고(회차별 Wilcoxon, n = 날짜 × 씨앗, p < 0.05)
  + 포화 +5% 안 + 이동거리 · 차량 수 +5% 안
  + 평소 빈도 구간마다 결품이 현행 이하(둘 다 0인 구간은 건너뜀)
  **평일 20일이 차기 전에는 판정하지 않는다** — 그 전의 표는 착수 점검이다.

    python experiments/structure/f2_candidate_priority.py
    python experiments/structure/f2_candidate_priority.py --days 2026-09-21,2026-09-22 --durations _10_15
"""
from __future__ import annotations

import argparse
import io
import sys
from datetime import datetime, time as clock, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "baseline"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import project_config  # noqa: E402  — 콘솔 인코딩을 먼저 맞춘다(— · 이모지)
import baseline_compare as bc  # noqa: E402
import db  # noqa: E402
import stockout_forecast as sf  # noqa: E402
from project_config import (DEFAULT_PERIOD, DURATIONS, REBAL_MIN_QTY, TICKS_PER_HOUR,  # noqa: E402
                            TOP_STATION_LIMIT, duration_hours, duration_start_hour, is_holiday)

TICK_MINUTES = sf.TICK_MINUTES

# ── 사전 등록 (ML_고도화_계획 3장 F2) — 결과를 보고 바꾸지 않는다
PICK_EMPTY_CUT = 0.5          # A1: 수거 후보에서 뺄 확률
A1_HORIZON = 6                # A1: 1시간 뒤
A2_FLOOR = 0.5                # A2: 배송 점수의 바닥 가중
SEEDS = (42, 7, 13)
ADOPT_MIN_DAYS = 20           # 채택 판정은 평일 20일부터
COST_TOLERANCE = 0.05         # 포화 · 이동거리 · 차량 수 +5% 안
WILCOXON_ALPHA = 0.05
BASE_RATE_EDGES = [0, 0.2, 0.4, 0.6, 0.8, 0.95, 1.01]   # ML 9번의 구간 그대로

# ── 해석 (EXPERIMENTS 41장 — 첫 실측 전에 적었다)
MIN_TRAIN_DAYS = 5            # 학습 날이 이보다 적은 날은 평가하지 않는다
FULL_DAY_RATIO = 0.8          # 평가할 날: 하루 144틱의 80% 이상
START_TOLERANCE_MIN = 10      # 회차 시작 틱이 없으면 10분 안의 첫 틱을 쓴다


# ───────────────────────────────────────────── 선정 — 순서만 바꾼 step1

def select_ordered(rebal: pd.DataFrame, st_info: pd.DataFrame, drop_score=None,
                   pick_exclude=()) -> pd.DataFrame:
    """step1 `select_top_unbalanced_st()`와 **같은 자르기**에 순서만 바꿔 끼운다.

    `drop_score`를 안 주고 `pick_exclude`가 비면 step1과 같은 답이어야 한다 — 테스트가 지킨다.
    운영 함수는 정렬 기준을 받지 않아서 여기 복사했다. 둘이 갈라지면 테스트가 먼저 안다.

    drop_score: 배송 후보 프레임을 받아 점수(클수록 먼저)를 돌려주는 함수. 동점은 rebal_qty 순.
    pick_exclude: 수거 후보에서 뺄 station_id.
    """
    st = (rebal[rebal["rebal_qty"].abs() > REBAL_MIN_QTY]
          .merge(st_info, how="left", on="station_id", suffixes=("", "_info"))
          [["station_id", "station_name", "lat", "lon", "parking_lot", "stock",
            "target_qty", "rebal_qty", "mu", "sigma"]])

    pick = st[st["rebal_qty"] < 0]
    if len(pick_exclude):
        pick = pick[~pick["station_id"].isin(set(pick_exclude))]
    pick = pick.sort_values("rebal_qty", ascending=True).iloc[:TOP_STATION_LIMIT, :]

    drop = st[st["rebal_qty"] > 0]
    if drop_score is None:
        drop = drop.sort_values("rebal_qty", ascending=False)
    else:
        drop = (drop.assign(_점수=drop_score(drop).to_numpy())
                .sort_values(["_점수", "rebal_qty"], ascending=False)
                .drop(columns="_점수"))
    drop = drop.iloc[:TOP_STATION_LIMIT, :]

    cut = min(abs(pick["rebal_qty"].sum()), drop["rebal_qty"].sum())
    pick = pick[pick["rebal_qty"].cumsum().abs() <= cut]
    drop = drop[drop["rebal_qty"].cumsum() <= cut]
    return pd.concat([pick, drop], axis=0)


def a1_select(rebal, st_info, p_hour: pd.Series) -> pd.DataFrame:
    """A1 필터 — 곧 빌 곳에서 빼 오지 않고, 곧 빌 곳부터 채운다."""
    doomed = p_hour[p_hour >= PICK_EMPTY_CUT].index
    return select_ordered(rebal, st_info,
                          drop_score=lambda d: d["station_id"].map(p_hour).fillna(0.0),
                          pick_exclude=doomed)


def a2_select(rebal, st_info, p_window: pd.Series) -> pd.DataFrame:
    """A2 가중 — 배송 점수 = rebal_qty × (0.5 + P(빔, 창 길이))."""
    return select_ordered(
        rebal, st_info,
        drop_score=lambda d: d["rebal_qty"] * (A2_FLOOR + d["station_id"].map(p_window).fillna(0.0)))


# ───────────────────────────────────────────── 자료

def round_start(day, duration: str) -> datetime:
    return datetime.combine(day, clock(duration_start_hour(duration)))


def window_ticks(duration: str) -> int:
    """A2의 k = 창 길이(틱). `_20_05`는 9시간이다."""
    return len(duration_hours(duration)) * TICKS_PER_HOUR


def eval_days(frame: pd.DataFrame, only=None) -> list:
    """평가할 날 — 24시간을 온전히 덮은 평일 중 앞선 학습 날이 `MIN_TRAIN_DAYS` 이상인 날."""
    dense = sf.dense_days(frame)
    ticks = frame.groupby(frame["ts"].dt.date)["ts"].nunique()
    need = int(24 * TICKS_PER_HOUR * FULL_DAY_RATIO)
    out = []
    for day in dense:
        if is_holiday(day) or ticks.get(day, 0) < need:
            continue
        if sum(1 for d in dense if d < day) < MIN_TRAIN_DAYS:
            continue
        if only and day not in only:
            continue
        out.append(day)
    return out


def observed_start(frame: pd.DataFrame, start: datetime) -> pd.Series:
    """회차 시작 시각의 관측 재고(station_id → stock). 그 틱이 없으면 10분 안의 첫 틱."""
    near = frame[(frame["ts"] >= start)
                 & (frame["ts"] < start + timedelta(minutes=START_TOLERANCE_MIN))]
    if near.empty:
        return pd.Series(dtype=float)
    first = near[near["ts"] == near["ts"].min()]
    return first.set_index("station_id")["stock"].astype(float)


def predict_day(long: pd.DataFrame, day, starts: list) -> dict:
    """그날보다 **앞선 날만으로** 한 번 학습해 여러 회차 시작 시각의 확률을 낸다.

    반환: {시작 시각: (station_id → P(빔), station_id → 그 시(時)의 과거 빔 비율)}.
    과거 빔 비율은 ML 9번의 '대여소시간대빔비율' 그대로 — 평소 빈도 구간과 결측 채움에 쓴다.
    """
    from sklearn.ensemble import HistGradientBoostingClassifier

    train = long[long["ts"].dt.date < day]
    now = long[long["ts"].isin(starts)]
    train, now = sf.add_station_history(train, [train, now])
    model = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.1, max_depth=6,
                                           random_state=42)
    model.fit(train[FEATURES], train["타깃"])
    now = now.assign(P=model.predict_proba(now[FEATURES])[:, 1])
    out = {}
    for start in starts:
        at = now[now["ts"] == start]
        base = train[train["시"] == start.hour].groupby("station_id")["지금빔"].mean()
        out[start] = (at.set_index("station_id")["P"], base)
    return out


FEATURES = ["지금빔", "재고", "1틱전빔", "3틱전빔", "6틱전빔", "최근6틱빔비율", "최근18틱빔비율",
            "재고변화3틱", "시", "요일", "대여소시간대빔비율"]


# ───────────────────────────────────────────── 평가

def per_station(net, population: pd.DataFrame, delta: pd.Series, duration: str) -> pd.DataFrame:
    """대여소별 결품·포화 시간(일 평균). 합계를 대여소 수로 나누면 `bc.simulate_pair`와 같다."""
    hours = duration_hours(duration)
    stations = population[["station_id", "stock", "parking_lot"]].copy()
    stations["delta"] = stations["station_id"].map(delta).fillna(0)
    merged = net.merge(stations, on="station_id", how="inner")
    sim = bc.kpi_mod._simulate_stock(merged, merged["stock"] + merged["delta"],
                                     merged["parking_lot"], hours)
    out = pd.DataFrame({"station_id": merged["station_id"], "결품": sim["stockout"],
                        "포화": sim["saturated"]})
    days = max(merged["날짜"].nunique(), 1)
    return out.groupby("station_id")[["결품", "포화"]].sum() / days


def observed_hit(frame, stations, start: datetime, duration: str) -> float:
    """고른 대여소 가운데 그 창에서 **한 번이라도 실제로 빈** 곳의 비율."""
    if not len(stations):
        return float("nan")
    end = start + timedelta(hours=len(duration_hours(duration)))
    part = frame[(frame["ts"] >= start) & (frame["ts"] < end)
                 & frame["station_id"].isin(set(stations))]
    if part.empty:
        return float("nan")
    return float(part.groupby("station_id")["빔"].max().reindex(stations).fillna(0).mean())


def evaluate(rows: list, day_count: int) -> list:
    """사전 등록 채택 조건. 평일이 `ADOPT_MIN_DAYS`보다 적으면 판정하지 않는다."""
    from scipy.stats import wilcoxon

    table = pd.DataFrame(rows)
    verdicts = []
    for (duration, method), part in table[table["method"] != "현행"].groupby(["duration", "method"]):
        base = table[(table["duration"] == duration) & (table["method"] == "현행")]
        pair = part.merge(base, on=["day", "seed"], suffixes=("", "_현행"))
        if pair.empty:
            continue
        diff = pair["결품"] - pair["결품_현행"]
        try:
            p = float(wilcoxon(pair["결품"], pair["결품_현행"], alternative="less").pvalue) \
                if (diff != 0).any() else 1.0
        except ValueError:
            p = float("nan")
        cost = {k: pair[k].mean() / pair[f"{k}_현행"].mean() - 1 if pair[f"{k}_현행"].mean() else 0.0
                for k in ("포화", "km", "vehicles")}
        bins_ok = bins_not_worse(pair, bin_names(table))
        worse = bins_worse(pair, bin_names(table))
        passed = (diff.mean() < 0 and p < WILCOXON_ALPHA
                  and all(v <= COST_TOLERANCE for v in cost.values()) and bins_ok)
        if day_count < ADOPT_MIN_DAYS:
            word = f"보류 — 평일 {day_count}일(판정은 {ADOPT_MIN_DAYS}일부터)"
        else:
            word = "✅ 채택 조건 통과" if passed else "❌ 미달"
        verdicts.append({"duration": duration, "method": method, "n": len(pair),
                         "결품차": diff.mean(), "p": p, **{f"비용_{k}": v for k, v in cost.items()},
                         "구간": bins_ok, "나빠진_구간": worse, "판정": word})
    return verdicts


def bins_worse(pair: pd.DataFrame, names: list) -> list:
    """현행보다 결품이 **커진** 구간을 (구간, 방법 평균, 현행 평균)으로 돌려준다.

    첫 실측(2026-10-08)이 구간 ❌만 찍고 어느 구간인지 남기지 않아 80분 결과를 되짚을 수 없었다 —
    판정 줄 아래에 이것을 찍는다. 규칙(`bins_not_worse`)은 그대로다.
    """
    out = []
    for b in names:
        mine, base = pair[f"결품_{b}"].mean(), pair[f"결품_{b}_현행"].mean()
        if (pd.isna(mine) and pd.isna(base)) or (mine == 0 and base == 0):
            continue
        if pd.isna(mine) or pd.isna(base) or mine > base + 1e-12:
            out.append((b, float(mine), float(base)))
    return out


def bins_not_worse(pair: pd.DataFrame, names: list) -> bool:
    """평소 빈도 구간마다 결품 평균이 현행 **이하**인가. 둘 다 0인 구간은 건너뛴다.

    ML 9번의 검사 ④를 계획에 옮긴 것 — 늘 비는 곳만 채워 평균을 끌어내린 것이 아닌지 본다.
    """
    for b in names:
        mine, base = pair[f"결품_{b}"].mean(), pair[f"결품_{b}_현행"].mean()
        if (pd.isna(mine) and pd.isna(base)) or (mine == 0 and base == 0):
            continue
        if pd.isna(mine) or pd.isna(base) or mine > base + 1e-12:
            return False
    return True


def bin_names(table: pd.DataFrame) -> list:
    return sorted(c[len("결품_"):] for c in table.columns
                  if c.startswith("결품_[") and not c.endswith("_현행"))


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
    parser.add_argument("--out", help="(날, 회차, 방법, 씨앗) 행을 CSV로 남긴다 — 80분짜리 실행을 되짚으려면 꼭 준다")
    args, _ = parser.parse_known_args(argv)

    durations = [d.strip() for d in args.durations.split(",") if d.strip()]
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    only = {datetime.strptime(d.strip(), "%Y-%m-%d").date()
            for d in args.days.split(",") if d.strip()} or None

    step1 = bc.load_step1()
    project_config.align_day_type("weekday", bc.ilp_mod, bc.vrp_mod, bc.kpi_mod, step1)
    solver = bc.ilp_mod.build_solver()
    net, st_info, _warmup = bc.load_inputs(args.period, None, "weekday", 0, "")

    with db.session() as conn:
        frame = sf.load_grid(conn)
    days = eval_days(frame, only)
    print(f"\n평가할 평일 {len(days)}일: {', '.join(str(d) for d in days) or '없음'}"
          f" · 순수요 {args.period} · 씨앗 {seeds}")
    if not days:
        return 1

    # 창이 아직 안 닫힌 회차는 뺀다 — 타깃이 없어 피처 행이 통째로 빠지고, 확률이
    # 전부 과거 빈도로 채워져 A1·A2가 조용히 다른 것이 된다.
    last_tick = frame["ts"].max()
    rounds = [(day, d) for day in days for d in durations
              if round_start(day, d) + timedelta(hours=len(duration_hours(d))) <= last_tick]
    horizons = sorted({A1_HORIZON, *(window_ticks(d) for d in durations)})
    probs = {}   # (day, duration, horizon) -> (P, 과거 빔 비율)
    for horizon in horizons:
        long = sf.build_features(frame, horizon)
        for day in days:
            need = [d for (dd, d) in rounds if dd == day
                    and horizon in (A1_HORIZON, window_ticks(d))]
            if not need:
                continue
            got = predict_day(long, day, [round_start(day, d) for d in need])
            for d in need:
                probs[(day, d, horizon)] = got[round_start(day, d)]
        del long
        print(f"  확률 {horizon}틱 뒤 — 날마다 앞선 날로 학습 끝")

    rows = []
    for day, duration in rounds:
        start = round_start(day, duration)
        stock = observed_start(frame, start)
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
        p1, base_rate = probs[(day, duration, A1_HORIZON)]
        pk, _ = probs[(day, duration, window_ticks(duration))]
        fill = info["station_id"].map(base_rate).fillna(base_rate.mean() if len(base_rate) else 0.5)
        fill.index = info["station_id"].to_numpy()
        missing = int(p1.reindex(fill.index).isna().sum())
        p1 = p1.reindex(fill.index).fillna(fill)
        pk = pk.reindex(fill.index).fillna(fill)
        chosen = {"현행": current, "A1": a1_select(rebal, info, p1), "A2": a2_select(rebal, info, pk)}
        bins = info["station_id"].map(base_rate).fillna(0.0).map(bin_label)
        bins.index = info["station_id"].to_numpy()

        for seed in seeds:
            for method, cand in chosen.items():
                if cand.empty:
                    continue
                _c, routes = bc.plan_with_clusters(cand.copy(), step1, solver, adjust=True, seed=seed)
                each = per_station(net, info, bc.executed_delta(routes), duration)
                stat = bc.route_stats(routes)
                drop_ids = cand.loc[cand["rebal_qty"] > 0, "station_id"].tolist()
                row = {"day": day, "duration": duration, "method": method, "seed": seed,
                       "결품": float(each["결품"].mean()), "포화": float(each["포화"].mean()),
                       "km": stat["km"], "vehicles": stat["vehicles"], "bikes": stat["bikes"],
                       "배송후보_실제빔": observed_hit(frame, drop_ids, start, duration)}
                by_bin = each["결품"].groupby(bins.reindex(each.index)).mean()
                for name, value in by_bin.items():
                    row[f"결품_{name}"] = float(value)
                rows.append(row)
        last = [r for r in rows if r["day"] == day and r["duration"] == duration]
        summary = " · ".join(
            f"{m} {np.mean([r['결품'] for r in last if r['method'] == m]):.3f}h"
            for m in chosen)
        note = f"  (확률이 없어 과거 빈도로 채운 곳 {missing})" if missing else ""
        print(f"  {day} {duration}: {summary}{note}")

    if not rows:
        print("결과가 없습니다.")
        return 1
    table = pd.DataFrame(rows)
    if args.out:
        table.to_csv(args.out, index=False, encoding="utf-8-sig")
        print(f"\n행 {len(table)}개를 {args.out}에 남겼다.")
    print("\n" + "=" * 96)
    print("회차 × 방법 평균 (중립 모집단 = 회차 시작에 관측된 대여소 전체)")
    print("=" * 96)
    agg = table.groupby(["duration", "method"])[["결품", "포화", "km", "vehicles", "bikes",
                                                 "배송후보_실제빔"]].mean()
    print(agg.to_string(float_format=lambda v: f"{v:.3f}"))

    print("\n판정 (사전 등록 — ML_고도화_계획 3장 F2)")
    for v in evaluate(rows, len(days)):
        print(f"  {v['duration']:7} {v['method']}  n={v['n']:>3}  결품 {v['결품차']:+.4f}h  p={v['p']:.3f}"
              f"  포화 {v['비용_포화']:+.1%} · km {v['비용_km']:+.1%} · 차량 {v['비용_vehicles']:+.1%}"
              f"  구간 {'✅' if v['구간'] else '❌'}  → {v['판정']}")
        for b, mine, base in v["나빠진_구간"]:
            print(f"      ↳ 평소 빈도 {b}: {mine:.3f}h > 현행 {base:.3f}h")
    return 0


if __name__ == "__main__":
    sys.exit(main())
