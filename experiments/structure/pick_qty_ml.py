"""수거 대상은 그대로 두고 수거 대수만 관측 재고로 배운 모형으로 줄이면 수거가 무해해지나 (EXPERIMENTS 54장 — 등록 전 준비).

47 · 53장이 남긴 것 — 수거 대상은 맞는데(수거지의 91~93%가 이미 꽉 찼거나 곧 찬다) 양이 많다. 계획한 양을 회차 시작에
뺐다면 5시간 안에 40%가 한 번은 0대가 됐을 것이다(1.26.347, 0 = 빔). 49장은 3월 대여이력의 창 안 누적 순유출로 양을 줄여
59.6 → 66.6%까지 올렸다. 남은 원인 둘 — ① 시간 단위 순수요는 회차 중간의 바닥을 못 본다 ② 3월 통계로 가을을 잰다.
관측 재고(10분 간격, 지금 계절)로 배우면 둘을 함께 피한다.

정답 — 대여소 i, 시각 t, 지평 H에서 낙폭 dd = s_i(t) − min_{(t, t+H]} s_i. q대를 빼도 0대가 안 되려면 q ≤ s_i(t) − dd − 1.
모형은 dd의 높은 분위수를 맞히고, 수거량은 min(계획량, ⌊s_i(t) − 예측 낙폭 − 1⌋)로 줄인다(2대 이하면 수거하지 않는다 —
운영 문턱과 같다). **수거 대상 선정은 바꾸지 않는다**(사용자 결정 2026-10-11).

방법
  C  현행 — 계획량 그대로
  U  균등 축소 — 계획량 × f (손잡이 f)
  P  49장 경로 기준 — min(q, 부드러운 상한(재고 − 경로 안전 재고))
  T  단순 표 — 대여소 × 회차마다 학습 날의 낙폭 k분위수(행이 MIN_ROWS 미만이면 회차 전체의 k분위수) (손잡이 k)
  M  분위수 GBM — 아래 입력으로 낙폭의 k분위수(손잡이 k)

학습 행 — 평일 10분 틱마다 그 시각이 속한 회차의 운영 식으로 계획을 다시 세워(47장 --stats current와 같은 mu · sigma)
  수거 대상(rebal_qty < −2)이 된 대여소. 창의 10분 칸이 80% 이상 관측되고, 창 안에 10분 사이 JUMP대 이상 빠진 칸(공사 트럭
  흔적일 수 있다)이 없는 행만 학습에 쓴다. 채점은 **회차 시작 시각**의 수거 대상에서만, 트럭 흔적이 있어도 뺀다(보수적).
나누기 — 회차 시작 날짜로(날이 섞이면 점수가 부푼다) 앞 60% 학습 · 다음 20% 손잡이 고르기 · 마지막 20% 시험.
입력(t까지의 것만) — 재고 · 거치대 · 꽉 찬 정도 · 시각 · 요일 · 회차 · 회차 시작 뒤 분 · 계획 수거량 · mu · sigma ·
  1 · 3시간 전 대비 재고 변화 · 지난 3시간 최고 · 최저.

실행:
    python experiments/structure/pick_qty_ml.py --sanity    # 결과 없이 입력만(행 수 · 날짜 나누기 · 결측)
"""
import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import project_config  # noqa: E402  — 콘솔 인코딩을 먼저 맞춘다(— · 이모지)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import db  # noqa: E402
import pick_path_target as ppt  # noqa: E402
import plan_validity as pv  # noqa: E402
import stockout_forecast as sf  # noqa: E402
from project_config import (DATA_ROOT, DURATIONS, REBAL_MIN_QTY, TICKS_PER_HOUR, duration_hours,  # noqa: E402
                            duration_start_hour, holiday_mask)

HORIZONS = (3 * TICKS_PER_HOUR, 5 * TICKS_PER_HOUR)   # 3 · 5시간
MAIN_HORIZON = 5 * TICKS_PER_HOUR
SPLIT = (0.6, 0.2)                       # 학습 · 손잡이 고르기 (나머지가 시험)
SAFE_TARGET = 0.90                       # 손잡이는 고르기 날에서 이 무해율을 지키는 것 가운데 가장 많이 빼는 값
KNOB_Q = (0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95)   # T · M의 낙폭 분위수
KNOB_U = tuple(round(x, 2) for x in np.arange(0.0, 1.0001, 0.05))
MIN_ROWS = 20                            # 단순 표 — 대여소 × 회차 행이 이보다 적으면 회차 전체 값
GBM_PARAMS = dict(max_iter=300, learning_rate=0.05, max_depth=6, min_samples_leaf=50, random_state=0)
FEATURES = ["재고", "거치대", "꽉참", "시", "요일", "회차", "시작뒤분", "계획량", "mu", "sigma",
            "변화1h", "변화3h", "최고3h", "최저3h"]
OUT_DIR = DATA_ROOT / "exp_54"

HOUR_DUR = {h: d for d in DURATIONS for h in duration_hours(d)}   # 시 → 그 시각이 속한 회차


def forward(frame: pd.DataFrame, h: int, how: str) -> pd.DataFrame:
    """각 행 t에 대해 (t, t+h] 창의 min · mean · max — 뒤집어 굴린다.

    min · max는 창 안의 관측된 칸만 본다(빈 칸은 건너뛴다). 창이 자료 끝을 넘는지는 관측 비율(mean, 창이 다 차야
    값이 나온다)이 가린다 — 그 행은 80% 규칙에서 빠진다.
    """
    roll = frame.shift(-1).iloc[::-1].rolling(h, min_periods=h if how == "mean" else 1)
    return getattr(roll, how)().iloc[::-1]


def window_arrays(grid: pd.DataFrame) -> dict:
    """지평마다 (창 최저, 관측 비율, 트럭 흔적) — 10분 격자 전체에 한 번에."""
    obs = grid.notna().astype(float)
    step = grid.diff()
    drop = (step <= -pv.JUMP).astype(float).where(step.notna(), 0.0)
    out = {}
    for h in HORIZONS:
        lo = forward(grid, h, "min")
        seen = forward(obs, h, "mean")
        trace = forward(drop, h, "max")
        out[h] = (lo, seen, trace)
    return out


def tick_duration(ts: pd.Timestamp) -> tuple:
    """틱이 속한 회차와 그 회차의 시작 시각(밤 회차의 새벽 틱은 전날 20시에 시작)."""
    dur = HOUR_DUR[ts.hour]
    start = ts.normalize() + pd.Timedelta(hours=duration_start_hour(dur))
    if start > ts:
        start -= pd.Timedelta(days=1)
    return dur, start


def build_rows(grid, refs, arrays, days_ok) -> pd.DataFrame:
    """평일 틱마다 계획을 다시 세워 수거 대상 행을 만든다(입력 · 정답 · 채점에 쓸 창 값)."""
    hour, three = TICKS_PER_HOUR, 3 * TICKS_PER_HOUR
    lag6, lag18 = grid.shift(hour), grid.shift(three)
    max3 = grid.rolling(three, min_periods=2 * hour).max()
    min3 = grid.rolling(three, min_periods=2 * hour).min()
    rows = []
    for i, ts in enumerate(grid.index):
        dur, start = tick_duration(ts)
        if start.date() not in days_ok or dur not in refs:
            continue
        s0 = grid.iloc[i].dropna()
        if len(s0) < 0.5 * grid.shape[1]:
            continue
        ref = refs[dur]
        rebal = pv.replan(ref, s0)
        pick = rebal[rebal < -REBAL_MIN_QTY]
        if pick.empty:
            continue
        ids = pick.index
        cap = ref["parking_lot"].reindex(ids)
        frame = pd.DataFrame({
            "station_id": ids, "ts": ts, "시작": start, "날": start.date(), "회차": DURATIONS.index(dur),
            "재고": s0[ids].to_numpy(), "거치대": cap.to_numpy(), "계획량": (-pick).to_numpy(),
            "mu": ref["mu"].reindex(ids).to_numpy(), "sigma": ref["sigma"].reindex(ids).to_numpy(),
            "시": ts.hour + ts.minute / 60, "요일": start.dayofweek,
            "시작뒤분": int((ts - start).total_seconds() // 60),
            "변화1h": (s0[ids] - lag6.iloc[i].reindex(ids)).to_numpy(),
            "변화3h": (s0[ids] - lag18.iloc[i].reindex(ids)).to_numpy(),
            "최고3h": max3.iloc[i].reindex(ids).to_numpy(), "최저3h": min3.iloc[i].reindex(ids).to_numpy(),
        })
        frame["꽉참"] = frame["재고"] / frame["거치대"].replace(0, np.nan)
        for h, (lo, seen, trace) in arrays.items():
            frame[f"최저{h}"] = lo.iloc[i].reindex(ids).to_numpy()
            frame[f"관측{h}"] = seen.iloc[i].reindex(ids).to_numpy()
            frame[f"흔적{h}"] = trace.iloc[i].reindex(ids).to_numpy()
            frame[f"낙폭{h}"] = frame["재고"] - frame[f"최저{h}"]
        rows.append(frame)
    return pd.concat(rows, ignore_index=True)


def split_days(days: list) -> dict:
    """회차 시작 날짜를 시간 순으로 학습 · 고르기 · 시험으로 나눈다."""
    n = len(days)
    a, b = round(n * SPLIT[0]), round(n * (SPLIT[0] + SPLIT[1]))
    return {d: ("학습" if k < a else "고르기" if k < b else "시험") for k, d in enumerate(sorted(days))}


def usable(rows: pd.DataFrame, h: int, train: bool) -> pd.Series:
    """그 지평의 창이 80% 이상 관측된 행. 학습 행은 트럭 흔적도 뺀다."""
    ok = rows[f"관측{h}"] >= pv.MIN_OBSERVED
    if train:
        ok &= rows[f"흔적{h}"] < 1
    return ok


def reduce(q: pd.Series, safe: pd.Series) -> pd.Series:
    """수거량을 min(q, ⌊safe⌋)로 줄이고, 2대 이하면 수거하지 않는다."""
    out = np.minimum(q, np.floor(safe.clip(lower=0)))
    return out.where(out > REBAL_MIN_QTY, 0.0)


def table_values(train: pd.DataFrame, target: pd.DataFrame, h: int, k: float) -> pd.Series:
    """단순 표 — 학습 행의 대여소 × 회차 낙폭 k분위수(행이 적으면 회차 전체 값)."""
    col = f"낙폭{h}"
    g = train.groupby(["station_id", "회차"])[col]
    per = g.quantile(k)[g.size() >= MIN_ROWS]
    pooled = train.groupby("회차")[col].quantile(k)
    key = pd.MultiIndex.from_arrays([target["station_id"], target["회차"]])
    val = pd.Series(per.reindex(key).to_numpy(), index=target.index)
    return val.fillna(target["회차"].map(pooled))


def fit_models(train: pd.DataFrame, h: int) -> dict:
    from sklearn.ensemble import HistGradientBoostingRegressor
    out = {}
    for k in KNOB_Q:
        model = HistGradientBoostingRegressor(loss="quantile", quantile=k, **GBM_PARAMS)
        out[k] = model.fit(train[FEATURES], train[f"낙폭{h}"])
    return out


def method_qty(name: str, knob, rows: pd.DataFrame, h: int, train: pd.DataFrame, models: dict) -> pd.Series:
    q = rows["계획량"]
    if name == "C":
        return q
    if name == "U":
        out = np.floor(q * knob)
        return out.where(out > REBAL_MIN_QTY, 0.0)
    if name == "P":
        return rows["P49"]
    if name == "T":
        return reduce(q, rows["재고"] - table_values(train, rows, h, knob) - 1)
    if name == "M":
        return reduce(q, rows["재고"] - pd.Series(models[knob].predict(rows[FEATURES]), index=rows.index) - 1)
    raise ValueError(name)


def score(rows: pd.DataFrame, qty: pd.Series, h: int) -> dict:
    """무해(0 = 빔 — 최저 − q ≥ 1, 수거하지 않은 곳은 무해) · 뺀 총량."""
    harmless = (qty == 0) | (rows[f"최저{h}"] - qty >= 1)
    return {"무해": float(harmless.mean()), "총량": float(qty.sum()), "곳": int(len(rows))}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sanity", action="store_true", help="결과 없이 입력만 점검한다(행 수 · 날짜 나누기 · 결측)")
    parser.add_argument("--until", help="이 시각 전의 관측만 쓴다 — 등록 때 고정한다")
    args = parser.parse_args(argv)

    with db.session() as conn:
        refs = pv.load_refs(conn, "weekday", before=args.until)
        raw = sf.load_grid(conn)
    if args.until:
        raw = raw[raw["ts"] < pd.Timestamp(args.until)]
    net = pv.load_net("weekday")
    for dur, (frame, lab) in list(refs.items()):
        refs[dur] = pv.current_stats(frame, net, dur)
    stock = raw.pivot_table(index="ts", columns="station_id", values="stock")
    grid = stock.reindex(pd.date_range(stock.index.min().floor("D"), stock.index.max().ceil("D"),
                                       freq=pv.TICK, inclusive="left"))
    dense = sf.dense_days(raw)
    hol = dict(zip(dense, holiday_mask(pd.Series(pd.to_datetime(dense)))))
    weekdays = [d for d in dense if not hol[d]]
    print(f"관측 자료: {raw['ts'].min()} ~ {raw['ts'].max()}" + (f" (--until {args.until})" if args.until else " (끝 고정 없음)"))
    print(f"평일(하루 30틱 이상) {len(weekdays)}일 · 대여소 {grid.shape[1]:,}곳")

    arrays = window_arrays(grid)
    rows = build_rows(grid, refs, arrays, set(weekdays))
    split = split_days(sorted(rows["날"].unique()))
    rows["나눔"] = rows["날"].map(split)
    starts = rows["시작뒤분"] == 0
    for dur, frame in refs.items():               # 49장 경로 기준의 수거량(채점 행에만 쓴다)
        target, _ = ppt.path_target(net, dur, frame["parking_lot"])
        m = rows["회차"] == DURATIONS.index(dur)
        excess = (rows.loc[m, "재고"] - rows.loc[m, "station_id"].map(target)).fillna(np.inf).clip(lower=0)
        rows.loc[m, "P49"] = reduce(rows.loc[m, "계획량"], ppt.squash(excess) + 0.0)

    for part in ("학습", "고르기", "시험"):
        ds = sorted(d for d, p in split.items() if p == part)
        g = rows[rows["나눔"] == part]
        print(f"\n[{part}] 날 {len(ds)}개 ({ds[0]} ~ {ds[-1]}) · 수거 대상 행 {len(g):,} · 회차 시작 행 {int(starts[g.index].sum()):,}"
              f" · 회차 {g.loc[starts[g.index], '시작'].nunique()}개")
        for h in HORIZONS:
            print(f"    {h // TICKS_PER_HOUR}시간 — 창 관측 80% 이상 {int(usable(g, h, False).sum()):,}행 · 그중 트럭 흔적 없음 "
                  f"{int(usable(g, h, True).sum()):,}행")
    print("\n입력 결측(학습 행): " + " · ".join(f"{c} {rows.loc[rows['나눔'] == '학습', c].isna().mean():.1%}"
                                             for c in FEATURES if rows[c].isna().any()))
    if args.sanity:
        print("--sanity: 정답(낙폭) · 무해 · 총량은 계산하지 않았다")
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for h in HORIZONS:
        train = rows[(rows["나눔"] == "학습") & usable(rows, h, True)]
        models = fit_models(train, h)
        pick_rows = {p: rows[(rows["나눔"] == p) & starts & usable(rows, h, False)] for p in ("고르기", "시험")}
        for name, knobs in (("C", (None,)), ("P", (None,)), ("U", KNOB_U), ("T", KNOB_Q), ("M", KNOB_Q)):
            for knob in knobs:
                for p, g in pick_rows.items():
                    res = score(g, method_qty(name, knob, g, h, train, models), h)
                    results.append({"지평": h, "방법": name, "손잡이": knob, "나눔": p, **res})
    frame = pd.DataFrame(results)
    frame.to_csv(OUT_DIR / "knobs.csv", index=False, encoding="utf-8-sig")
    print(f"\n손잡이별 결과 {len(frame)}줄 — {OUT_DIR / 'knobs.csv'} (판정은 등록 뒤에 쓴다)")
    return 0


if __name__ == "__main__":
    project_config.exit_if_help(__doc__)
    raise SystemExit(main())
