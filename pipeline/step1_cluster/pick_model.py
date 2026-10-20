"""수거 대수 학습: 관측 재고로 배운 모형이 '어디서 얼마나' 뺄지 다시 나눈다 (EXPERIMENTS 56장 K, 1.26.375).

왜
  step1 `select_top_unbalanced_st()`는 재배치량이 큰 대여소 50곳에서 계산된 양을 그대로 뺀다. 관측 재고로 채점하면 그 수거
  대여소 가운데 5시간 안에 0대가 되지 않은 곳은 56장 시험 날 49.7%, 새 평일 62.0%뿐이었다. 56장 K는 수거 총량과 배송 쪽은
  그대로 두고, 대여소마다 '5시간 안에 0대가 되지 않고 뺄 수 있는 대수'를 분위수 GBM(k = PICK_MODEL_QUANTILE)으로 예측해
  많이 뺄 수 있는 곳부터 수거 총량까지 채운다. 운영 식이 수거를 원하지 않는 대여소(rebal_qty ≤ 2, 배송 대상은 뺀다)도 고를
  수 있다. 시험 날 35회차와 새 평일 20회차에서 두 번 모두 채택 후보였다(수거 무해 +14.8 · +11.6%p, 배송 적중 93.7 · 95.8%,
  거리 99.0 · 96.1%). 사용자 결정(2026-10-20): "K로 가자. 기본적으로 넣어라".

정의 (56장 그대로 옮겼다. 실험 쪽 원본과 같은지는 tests/test_pick_model.py가 맞댄다)
  정답    대여소 i, 시각 t의 5시간 낙폭 dd = s_i(t) − min_{(t, t+5h]} s_i
  행      학습 평일의 10분 틱 가운데 30분 간격, 재고가 관측된 모든 대여소. 창의 80% 이상이 관측되고, 창 안에 10분 사이 5대 이상
          빠진 칸(공사 트럭 흔적)이 없는 행만 학습에 쓴다
  입력    재고 · 거치대 · 꽉 찬 정도 · 시각 · 요일 · 회차 · 회차 시작 뒤 분 · 계획량(운영 식의 −rebal_qty) · mu · sigma ·
          1 · 3시간 전 대비 변화 · 지난 3시간 최고 · 최저, 그리고 그 대여소 × 회차의 학습 날 낙폭 분위수(0.5 · 0.9)와 행 수
          (학습 행에는 그 날을 뺀 나머지 날로 낸 값)
  안전량  지금 재고 − 예측 낙폭 − 1
  고르기  a = min(q, ⌊안전량⌋)(q는 운영 식의 수거량, 진단 밖이면 제한 없음). a가 큰 순으로 상한 50곳 안에서 지금 수거
          총량까지 채운다(마지막 곳은 남은 만큼, 2대 이하는 버린다)

언제 쓰나
  PICK_MODEL이 auto(기본)면 운영 계획(실행 종류 plan: 웹 실행 폼 · 동시각 계획)에서만, on이면 늘, off면 쓰지 않는다.
  실험 · 재현(종류 experiment 또는 선언 없음)은 지금 방식 그대로라 원고와 실험 기록의 수치가 바뀌지 않는다. 쓰기로 해도
  다음이면 지금 방식으로 돌아가고 그 이유를 찍는다: 휴일 회차(평일만 배웠다) · 재고 시계열이 없거나 오래됨 · 마지막 관측이
  회차 시작과 멀다 · 학습 평일이 모자람 · 예측이 하나도 없음 · 고른 수거가 0곳.

운영 설정 (56장과 다른 점)
  학습 날   56장은 08-25 ~ 09-16으로 고정했다. 여기서는 회차 날 전의 가장 최근 평일 PICK_MODEL_TRAIN_DAYS일로 날마다 다시
            배우고, 같은 날 안에서는 data/models에 저장한 모형을 다시 쓴다.
  지금 재고 56장은 회차 시작 틱의 관측으로 계획하고 채점했다. 운영에서는 입력을 마지막 관측 틱으로 만들고, 안전량은 계획이
            본 재고(타슈 API)에서 뺀다.
"""
from __future__ import annotations

import contextlib
import io
import pickle

import numpy as np
import pandas as pd

import db
import project_config
from project_config import (DATA_ROOT, DURATIONS, REBAL_MIN_QTY, STOCK_TICK_MINUTES, TICKS_PER_HOUR,
                            TOP_STATION_LIMIT, duration_hours, duration_start_hour, holiday_mask, is_holiday,
                            select_day_type)

# ── 56장 정의 (바꾸면 56장의 검증이 이 코드의 것이 아니게 된다)
H = 5 * TICKS_PER_HOUR            # 5시간 낙폭(pick_qty_ml.MAIN_HORIZON)
ALL_EVERY = 3                     # 학습 행은 30분 간격 틱(pick_redistribute.ALL_EVERY)
MIN_OBSERVED = 0.8                # 창의 10분 칸 중 관측 비율(plan_validity.MIN_OBSERVED)
JUMP = 5                          # 10분 사이 이만큼 빠지면 트럭 흔적(plan_validity.JUMP)
MIN_ROWS = 20                     # 대여소 × 회차 행이 이보다 적으면 회차 전체 분위수(pick_qty_ml.MIN_ROWS)
HYBRID_Q = {"표중앙": 0.5, "표상위": 0.9}
FEATURES = ["재고", "거치대", "꽉참", "시", "요일", "회차", "시작뒤분", "계획량", "mu", "sigma",
            "변화1h", "변화3h", "최고3h", "최저3h"]
FEATURES_H = FEATURES + list(HYBRID_Q) + ["표행수"]
GBM_PARAMS = dict(max_iter=300, learning_rate=0.05, max_depth=6, min_samples_leaf=50, random_state=0)

# ── 운영 설정
FULL_DAY_RATIO = 0.8              # 학습 날: 하루 144틱의 이만큼 이상을 덮은 평일
MIN_TRAIN_DAYS = 5                # 학습 평일이 이보다 적으면 쓰지 않는다
FRESH_MINUTES = 30                # 마지막 관측이 이보다 오래면 쓰지 않는다
START_BEFORE_MIN = 120            # 마지막 관측이 회차 시작 2시간 전 ~ 1시간 뒤일 때만 쓴다
START_AFTER_MIN = 60
MODEL_DIR = DATA_ROOT / "models"
MODEL_VERSION = 1

HOUR_DUR = {h: d for d in DURATIONS for h in duration_hours(d)}
INFO_COLS = ["station_id", "station_name", "lat", "lon", "parking_lot", "stock",
             "target_qty", "rebal_qty", "mu", "sigma"]
TICK = pd.Timedelta(minutes=STOCK_TICK_MINUTES)


def rule_name(k: float = None) -> str:
    """pick_drop.pick_rule에 남기는 이름. 지금 방식은 '현행'."""
    return f"K{project_config.PICK_MODEL_QUANTILE if k is None else k:g}"


def _quiet(func, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return func(*args, **kwargs)


def _target_mod():
    from pipeline.step0_collect import calculate_target_qty
    return calculate_target_qty


# ───────────────────────────────────────────── 격자 · 기준 통계

def _stamp(value) -> str:
    """stock_history.observed_at과 같은 꼴('YYYY-MM-DD HH:MM'). 문자열로 비교하므로 초를 붙이면 경계의 틱이 빠진다."""
    return pd.Timestamp(value).strftime("%Y-%m-%d %H:%M")


def load_grid(conn, start, end) -> pd.DataFrame:
    """재고 시계열을 (10분 틱 × 대여소) 격자로 편다. 빈 틱은 NaN 행으로 남긴다(창의 관측 비율이 그것을 센다).

    격자는 start가 든 날 0시에서 시작한다. 30분 간격 틱(i % ALL_EVERY == 0)이 :00 · :30에 놓이게(56장과 같다).
    하루씩 읽어 편다. 2주 남짓(240만 행)을 문자열째 한 번에 읽어 펴면 최대 1.1GB였다(10-20 실측, 수집 PC는 여유가 1 ~ 2GB).
    (observed_at, station_id)가 기본 키라 겹치는 값이 없으므로 집계 없이 편다.
    """
    first, last = pd.Timestamp(start).floor("D"), pd.Timestamp(end)
    parts, day = [], first
    while day <= last:
        upper = min(day + pd.Timedelta(days=1) - TICK, last)
        raw = pd.read_sql("SELECT observed_at, station_id, stock FROM stock_history"
                          " WHERE observed_at >= ? AND observed_at <= ?", conn, params=(_stamp(day), _stamp(upper)))
        if not raw.empty:
            raw["ts"] = pd.to_datetime(raw["observed_at"])
            parts.append(raw.pivot(index="ts", columns="station_id", values="stock").astype(float))
        day += pd.Timedelta(days=1)
    if not parts:
        return pd.DataFrame()
    stock = pd.concat(parts).sort_index()
    stock = stock.reindex(columns=sorted(stock.columns))
    return stock.reindex(pd.date_range(first, stock.index.max(), freq=TICK))


def reference_stats(net_weekday: pd.DataFrame, st_info: pd.DataFrame) -> dict:
    """회차 → (station_id 색인) mu · sigma · parking_lot. 운영 함수 build_stats 그대로, 계절 보정 없이.

    56장의 참조 통계(`plan_validity.current_stats`)와 같은 셈이다: 거치대는 대여소 정보, mu · sigma는 운영 기간 평일 순수요.
    """
    target_mod = _target_mod()
    init = st_info[["station_id", "stock", "parking_lot"]].drop_duplicates("station_id")
    out = {}
    for duration in DURATIONS:
        stats, _d, _r = _quiet(target_mod.build_stats, net_weekday, init, duration,
                               warmup_net=None, warmup_days=0, verbose=False)
        ref = stats.drop_duplicates("station_id").set_index("station_id")[["mu", "sigma", "parking_lot"]]
        out[duration] = ref.dropna(subset=["mu", "sigma"])
    return out


def replan(ref: pd.DataFrame, stock: pd.Series) -> pd.Series:
    """참조 통계에 새 재고를 넣어 운영 함수로 rebal_qty를 다시 낸다(plan_validity.replan과 같다)."""
    stats = ref[["mu", "sigma", "parking_lot"]].copy()
    stats["stock"] = stock.reindex(stats.index)
    stats = stats.dropna(subset=["stock", "mu", "sigma", "parking_lot"])
    out = _quiet(_target_mod().compute_rebal_qty, stats.copy())
    return out["rebal_qty"]


# ───────────────────────────────────────────── 행

def _forward(frame: pd.DataFrame, h: int, how: str) -> pd.DataFrame:
    """각 행 t의 (t, t+h] 창 min · mean · max(pick_qty_ml.forward와 같다)."""
    roll = frame.shift(-1).iloc[::-1].rolling(h, min_periods=h if how == "mean" else 1)
    return getattr(roll, how)().iloc[::-1]


def tick_duration(ts: pd.Timestamp) -> tuple:
    """틱이 속한 회차와 그 회차의 시작 시각(밤 회차의 새벽 틱은 전날 20시에 시작)."""
    dur = HOUR_DUR[ts.hour]
    start = ts.normalize() + pd.Timedelta(hours=duration_start_hour(dur))
    if start > ts:
        start -= pd.Timedelta(days=1)
    return dur, start


def _lag_rows(grid: pd.DataFrame, pos: list) -> tuple:
    """pos 위치 틱의 (1시간 전 재고, 3시간 전 재고, 지난 3시간 최고, 최저).

    격자 전체를 한 번씩 굴리고 그 틱만 남긴다. 격자 크기의 배열을 넷 들고 있지 않는다(수집 PC의 메모리, 10-20 실측 1.1GB).
    """
    hour, three = TICKS_PER_HOUR, 3 * TICKS_PER_HOUR
    makers = (lambda g: g.shift(hour), lambda g: g.shift(three),
              lambda g: g.rolling(three, min_periods=2 * hour).max(),
              lambda g: g.rolling(three, min_periods=2 * hour).min())
    return tuple(make(grid).iloc[pos] for make in makers)


def _row_frame(row: pd.Series, ts, start, dur, ref, lag6, lag18, max3, min3) -> pd.DataFrame:
    """한 틱(row = 그 틱의 격자 행)의 모든 대여소 행(입력만). 시각 값은 ts, 회차 값은 start에서 낸다."""
    s0 = row.dropna()
    rebal = replan(ref, s0)
    ids = rebal.index
    frame = pd.DataFrame({
        "station_id": ids, "ts": ts, "시작": start, "날": start.date(), "회차": DURATIONS.index(dur),
        "재고": s0[ids].to_numpy(), "거치대": ref["parking_lot"].reindex(ids).to_numpy(), "계획량": (-rebal).to_numpy(),
        "mu": ref["mu"].reindex(ids).to_numpy(), "sigma": ref["sigma"].reindex(ids).to_numpy(),
        "시": ts.hour + ts.minute / 60, "요일": start.dayofweek,
        "시작뒤분": int((ts - start).total_seconds() // 60),
        "변화1h": (s0[ids] - lag6.reindex(ids)).to_numpy(),
        "변화3h": (s0[ids] - lag18.reindex(ids)).to_numpy(),
        "최고3h": max3.reindex(ids).to_numpy(), "최저3h": min3.reindex(ids).to_numpy(),
    })
    frame["꽉참"] = frame["재고"] / frame["거치대"].replace(0, np.nan)
    return frame


def build_rows(grid: pd.DataFrame, refs: dict, days_ok: set, only_usable: bool = False) -> pd.DataFrame:
    """학습 행: days_ok에 회차 시작이 있는 틱 가운데 30분 간격(틱 번호 % ALL_EVERY == 0), 모든 대여소.

    pick_qty_ml.build_rows(stations="all", keep=학습 날 · i % ALL_EVERY == 0)과 같은 행을 낸다. only_usable이면 학습에 쓸 행
    (usable)만 쌓는다(운영 경로, 메모리를 아낀다).
    """
    counts = grid.notna().sum(axis=1).to_numpy()
    pos, meta = [], []
    for i, ts in enumerate(grid.index):
        if i % ALL_EVERY:
            continue
        dur, start = tick_duration(ts)
        if start.date() not in days_ok or dur not in refs:
            continue
        if counts[i] < 0.5 * grid.shape[1]:
            continue
        pos.append(i)
        meta.append((ts, dur, start))
    if not pos:
        return pd.DataFrame()
    # 창 값은 격자 전체를 굴린 뒤 쓸 틱만 남긴다(값은 pick_qty_ml.window_arrays와 같다)
    lo = _forward(grid, H, "min").iloc[pos]
    obs = grid.notna().astype(float)
    seen = _forward(obs, H, "mean").iloc[pos]
    del obs
    step = grid.diff()
    drop = (step <= -JUMP).astype(float).where(step.notna(), 0.0)
    del step
    trace = _forward(drop, H, "max").iloc[pos]
    del drop
    lag6, lag18, max3, min3 = _lag_rows(grid, pos)
    rows = []
    for j, (i, (ts, dur, start)) in enumerate(zip(pos, meta)):
        frame = _row_frame(grid.iloc[i], ts, start, dur, refs[dur],
                           lag6.iloc[j], lag18.iloc[j], max3.iloc[j], min3.iloc[j])
        if frame.empty:
            continue
        ids = frame["station_id"]
        frame[f"최저{H}"] = lo.iloc[j].reindex(ids).to_numpy()
        frame[f"관측{H}"] = seen.iloc[j].reindex(ids).to_numpy()
        frame[f"흔적{H}"] = trace.iloc[j].reindex(ids).to_numpy()
        frame[f"낙폭{H}"] = frame["재고"] - frame[f"최저{H}"]
        rows.append(frame[usable(frame)] if only_usable else frame)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def usable(rows: pd.DataFrame) -> pd.Series:
    """창이 80% 이상 관측되고 트럭 흔적이 없는 행(pick_qty_ml.usable(train=True))."""
    return (rows[f"관측{H}"] >= MIN_OBSERVED) & (rows[f"흔적{H}"] < 1)


# ───────────────────────────────────────────── 혼합 입력 · 학습

def quantile_tables(train: pd.DataFrame) -> dict:
    """대여소 × 회차 낙폭 분위수(행 MIN_ROWS 이상만) · 회차 전체 분위수 · 행 수."""
    col = f"낙폭{H}"
    g = train.groupby(["station_id", "회차"])[col]
    size = g.size()
    out = {"행수": size}
    for name, k in HYBRID_Q.items():
        out[name] = (g.quantile(k)[size >= MIN_ROWS], train.groupby("회차")[col].quantile(k))
    return out


def hybrid_values(tables: dict, target: pd.DataFrame) -> pd.DataFrame:
    """target 행의 혼합 입력(pick_qty_ml.table_values · add_hybrid와 같은 값)."""
    key = pd.MultiIndex.from_arrays([target["station_id"], target["회차"]])
    out = pd.DataFrame(index=target.index)
    for name in HYBRID_Q:
        per, pooled = tables[name]
        val = pd.Series(per.reindex(key).to_numpy(), index=target.index)
        out[name] = val.fillna(target["회차"].map(pooled)).to_numpy()
    out["표행수"] = tables["행수"].reindex(key).fillna(0).to_numpy()
    return out


def add_hybrid_train(train: pd.DataFrame) -> pd.DataFrame:
    """학습 행의 혼합 입력: 그 날을 뺀 나머지 학습 날로 낸 값(누수를 막는다). train에 열을 채워 돌려준다.

    표를 통째로 복사하지 않고, 날을 뺄 때도 분위수에 드는 네 열만 나눈다(수집 PC의 메모리).
    """
    slim = train[["station_id", "회차", f"낙폭{H}", "날"]]
    fresh = {col: np.full(len(train), np.nan) for col in (*HYBRID_Q, "표행수")}
    where = {d: np.flatnonzero((train["날"] == d).to_numpy()) for d in sorted(train["날"].unique())}
    for d, at in where.items():
        vals = hybrid_values(quantile_tables(slim[slim["날"] != d]), slim.iloc[at])
        for col in fresh:
            fresh[col][at] = vals[col].to_numpy()
    for col, values in fresh.items():
        train[col] = values
    return train


def fit(train: pd.DataFrame, k: float):
    from sklearn.ensemble import HistGradientBoostingRegressor
    model = HistGradientBoostingRegressor(loss="quantile", quantile=k, **GBM_PARAMS)
    model.fit(train[FEATURES_H], train[f"낙폭{H}"])
    return model


# ───────────────────────────────────────────── 고르기 (pick_mix.wide_pool · pick_redistribute.take_until)

def take_until(pool: pd.DataFrame, total: float) -> tuple:
    """a가 큰 순으로 상한 곳 수 안에서 total까지(마지막 곳은 남은 만큼, 2대 이하는 버린다). (고른 행, 모자란 대수)"""
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


def select_picks(base: pd.DataFrame, full: pd.DataFrame, safe: pd.Series) -> tuple:
    """K: 배송은 base 그대로, 수거는 안전량으로 다시 고른다. (후보표, 모자란 대수)

    base : 지금 선정(select_top_unbalanced_st)의 수거 · 배송   full : 모든 대여소의 재배치량(INFO_COLS)
    safe : station_id → 안전량(지금 재고 − 예측 낙폭 − 1). 없는 대여소는 고르지 않는다.
    """
    drops = base[base["rebal_qty"] > 0]
    total = float(-base.loc[base["rebal_qty"] < 0, "rebal_qty"].sum())
    pool = full[(full["rebal_qty"] <= REBAL_MIN_QTY) & ~full["station_id"].isin(drops["station_id"])].copy()
    q = (-pool["rebal_qty"]).where(pool["rebal_qty"] < -REBAL_MIN_QTY, np.inf).astype(float).to_numpy()
    s = safe.reindex(pool["station_id"]).to_numpy(dtype=float)
    pool["a"] = np.where(np.isnan(s), 0.0, np.minimum(q, np.floor(np.clip(s, 0, None))))
    picks, left = take_until(pool, total)
    picks["rebal_qty"] = picks["rebal_qty"].round().astype(int)
    parts = [x for x in (picks, drops) if len(x)]
    out = pd.concat(parts, ignore_index=True) if parts else base.iloc[:0].copy()
    return out[base.columns.intersection(out.columns, sort=False)], left


# ───────────────────────────────────────────── 운영 경로

def wanted(run_kind, mode: str = None) -> tuple:
    """(쓰나, 까닭). auto는 실행 종류 plan일 때만."""
    mode = mode or project_config.PICK_MODEL
    if mode == "off":
        return False, "PBR_PICK_MODEL=off"
    if mode == "on":
        return True, "PBR_PICK_MODEL=on"
    if run_kind == "plan":
        return True, "운영 계획(실행 종류 plan)"
    return False, f"실행 종류 {run_kind or '선언 없음'}(auto는 운영 계획에서만 쓴다)"


def round_start(duration: str, tau: pd.Timestamp):
    """마지막 관측 tau에 가장 가까운 이 회차의 시작. 시작 2시간 전 ~ 1시간 뒤 밖이면 None."""
    base = tau.normalize() + pd.Timedelta(hours=duration_start_hour(duration))
    best = min((base + pd.Timedelta(days=d) for d in (-1, 0, 1)), key=lambda s: abs(tau - s))
    lead = (tau - best).total_seconds() / 60
    return best if -START_BEFORE_MIN <= lead <= START_AFTER_MIN else None


def training_days(conn, before) -> list:
    """before(날짜) 전의 평일 가운데 하루 144틱의 80% 이상을 덮은 날, 가장 최근 PICK_MODEL_TRAIN_DAYS일."""
    per = pd.read_sql("SELECT substr(observed_at, 1, 10) AS d, COUNT(DISTINCT observed_at) AS n FROM stock_history"
                      " WHERE observed_at < ? GROUP BY d", conn, params=(_stamp(pd.Timestamp(before).normalize()),))
    if per.empty:
        return []
    per["date"] = pd.to_datetime(per["d"])
    per = per[(per["n"] >= FULL_DAY_RATIO * 24 * TICKS_PER_HOUR) & ~np.asarray(holiday_mask(per["date"]), dtype=bool)]
    return sorted(per["date"].dt.date)[-project_config.PICK_MODEL_TRAIN_DAYS:]


def load_net_weekday(period: str) -> pd.DataFrame:
    path = str(DATA_ROOT / f"pp_data/순수요/st_net_daily ({period}).csv")
    net, _ = db.read_step_output("net_demand", path, period=period)
    if net.empty:
        return net
    return select_day_type(net.rename(columns={"date": "날짜"}), "날짜", "weekday")


def train_or_load(conn, days: list, refs: dict, period: str, k: float, log=print) -> dict:
    """학습 날이 같으면 저장한 모형을 다시 쓴다. {model, tables, days, rows}"""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    path = MODEL_DIR / f"pick_k{k:g}_{period}_{days[-1]}_{len(days)}d_v{MODEL_VERSION}.pkl"
    if path.exists():
        with open(path, "rb") as f:
            bundle = pickle.load(f)
        if bundle.get("days") == [str(d) for d in days]:
            log(f"  저장한 모형을 씁니다: {path.name}")
            return bundle
    grid = load_grid(conn, pd.Timestamp(days[0]) - pd.Timedelta(days=1),
                     pd.Timestamp(days[-1]) + pd.Timedelta(days=1, hours=10))
    train = build_rows(grid, refs, set(days), only_usable=True)
    del grid
    if train.empty:
        return {}
    train = add_hybrid_train(train)
    model = fit(train, k)
    bundle = {"model": model, "tables": quantile_tables(train), "days": [str(d) for d in days],
              "rows": len(train), "k": k, "period": period, "version": MODEL_VERSION}
    del train
    with open(path, "wb") as f:
        pickle.dump(bundle, f)
    log(f"  학습했습니다: 평일 {len(days)}일({days[0]} ~ {days[-1]}) · 학습 행 {bundle['rows']:,} → {path.name}")
    return bundle


def predict_safe(bundle: dict, grid: pd.DataFrame, refs: dict, duration: str, start: pd.Timestamp,
                 plan_stock: pd.Series) -> pd.Series:
    """station_id → 안전량. 입력은 마지막 관측 틱(회차 시작 기준 시각 값), 안전량은 계획이 본 재고에서 뺀다."""
    i = len(grid) - 1
    lag6, lag18, max3, min3 = (x.iloc[0] for x in _lag_rows(grid, [i]))
    frame = _row_frame(grid.iloc[i], start, start, duration, refs[duration], lag6, lag18, max3, min3)
    if frame.empty:
        return pd.Series(dtype=float)
    frame = frame.reset_index(drop=True)
    hyb = hybrid_values(bundle["tables"], frame)
    for col in hyb:
        frame[col] = hyb[col].to_numpy()
    pred = bundle["model"].predict(frame[FEATURES_H])
    stock = plan_stock.reindex(frame["station_id"]).to_numpy(dtype=float)
    return pd.Series(stock - pred - 1, index=frame["station_id"].to_numpy())


def apply(base: pd.DataFrame, st_rebal: pd.DataFrame, st_info: pd.DataFrame, duration: str, *,
          day_type: str, period: str, run_kind=None, clock=None, conn=None, log=print) -> tuple:
    """지금 선정(base)을 K로 바꿔 돌려준다. (후보표, pick_rule). 쓰지 않으면 base와 '현행'을 그대로 돌려준다."""
    use, why = wanted(run_kind)
    if not use:
        log(f"[수거 학습] 쓰지 않음: {why}")
        return base, "현행"
    k = project_config.PICK_MODEL_QUANTILE

    def fallback(reason: str):
        log(f"[수거 학습] 지금 방식으로 둡니다: {reason}")
        return base, "현행"

    if day_type == "holiday":
        return fallback("휴일 계획(모형은 평일만 배웠다)")
    if base.empty or not (base["rebal_qty"] < 0).any():
        return fallback("지금 선정에 수거가 없다")
    if conn is None:
        with db.session() as own:
            return apply(base, st_rebal, st_info, duration, day_type=day_type, period=period,
                         run_kind=run_kind, clock=clock, conn=own, log=log)

    last = conn.execute("SELECT MAX(observed_at) FROM stock_history").fetchone()[0]
    if last is None:
        return fallback("재고 시계열이 없다")
    tau = pd.Timestamp(last)
    clock = pd.Timestamp.now() if clock is None else pd.Timestamp(clock)
    if (clock - tau) > pd.Timedelta(minutes=FRESH_MINUTES):
        return fallback(f"마지막 관측 {tau:%m-%d %H:%M}이 {FRESH_MINUTES}분보다 오래됐다")
    start = round_start(duration, tau)
    if start is None:
        return fallback(f"마지막 관측 {tau:%m-%d %H:%M}이 {duration} 회차 시작과 멀다")
    if is_holiday(start.date()):
        return fallback(f"회차 날 {start.date()}이 휴일이다")
    days = training_days(conn, start.date())
    if len(days) < MIN_TRAIN_DAYS:
        return fallback(f"학습 평일 {len(days)}일(최소 {MIN_TRAIN_DAYS}일)")
    net = load_net_weekday(period)
    if net.empty:
        return fallback(f"순수요가 없다({period})")
    refs = reference_stats(net, st_info)
    bundle = train_or_load(conn, days, refs, period, k, log=log)
    if not bundle:
        return fallback("학습 행이 없다")
    grid = load_grid(conn, tau - pd.Timedelta(hours=4), tau)
    plan_stock = st_rebal.drop_duplicates("station_id").set_index("station_id")["stock"].astype(float)
    safe = predict_safe(bundle, grid, refs, duration, start, plan_stock)
    if safe.notna().sum() == 0:
        return fallback("예측이 하나도 없다")
    full = (st_rebal.merge(st_info, how="left", on="station_id", suffixes=("", "_info"))[INFO_COLS]
            .reset_index(drop=True))
    out, left = select_picks(base, full, safe)
    if not (out["rebal_qty"] < 0).any():
        return fallback("고른 수거가 0곳")
    before = base[base["rebal_qty"] < 0]
    after = out[out["rebal_qty"] < 0]
    outside = int((~after["station_id"].isin(before["station_id"])).sum())
    log(f"[수거 학습 {rule_name(k)}] 마지막 관측 {tau:%m-%d %H:%M} · 회차 시작 {start:%m-%d %H:%M} · 학습 평일 {len(days)}일 · "
        f"수거 {len(before)}곳 {-before['rebal_qty'].sum():.0f}대 → {len(after)}곳 {-after['rebal_qty'].sum():.0f}대"
        f"(모자람 {left:.0f}대, 지금 선정 밖 {outside}곳)")
    return out, rule_name(k)
