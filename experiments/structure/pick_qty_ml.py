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
  Z  평균 + z · 표준편차 — T와 같은 칸의 낙폭 평균 + z · sd (손잡이 z)
  M1 분위수 GBM + 혼합 입력(그 대여소 × 회차의 학습 날 낙폭 분위수 — 학습 행은 그 날을 뺀 값) — **주 판정**
  M2 분위수 GBM, 혼합 입력 없이 · M3 선형 분위수 회귀(학습 행 1만 표본) · M4 분위수 랜덤 포레스트 · M5 LightGBM ·
  M6 XGBoost · M7 신경망 + 고르기 날 잔차 보정 · M8 분류형("q′대를 빼면 빌 확률") — 모두 손잡이 k, 찍기만
  설계와 까닭은 docs/기록/수거대수_학습_설계_2026-10-11.md, 등록은 EXPERIMENTS 54장.

학습 행 — 평일 10분 틱마다 그 시각이 속한 회차의 운영 식으로 계획을 다시 세워(47장 --stats current와 같은 mu · sigma)
  수거 대상(rebal_qty < −2)이 된 대여소. 창의 10분 칸이 80% 이상 관측되고, 창 안에 10분 사이 JUMP대 이상 빠진 칸(공사 트럭
  흔적일 수 있다)이 없는 행만 학습에 쓴다. 채점은 **회차 시작 시각**의 수거 대상에서만, 트럭 흔적이 있어도 뺀다(보수적).
나누기 — 회차 시작 날짜로(날이 섞이면 점수가 부푼다) 앞 60% 학습 · 다음 20% 손잡이 고르기 · 마지막 20% 시험.
입력(t까지의 것만) — 재고 · 거치대 · 꽉 찬 정도 · 시각 · 요일 · 회차 · 회차 시작 뒤 분 · 계획 수거량 · mu · sigma ·
  1 · 3시간 전 대비 재고 변화 · 지난 3시간 최고 · 최저.

실행:
    python experiments/structure/pick_qty_ml.py --sanity    # 결과 없이 입력만(행 수 · 날짜 나누기 · 결측)
    python experiments/structure/pick_qty_ml.py --until "2026-10-11 00:00"    # 54장 등록판(약 30분)
"""
import argparse
import math
import sys
import time
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
                            duration_start_hour, format_seconds, holiday_mask)

HORIZONS = (3 * TICKS_PER_HOUR, 5 * TICKS_PER_HOUR)   # 3 · 5시간
MAIN_HORIZON = 5 * TICKS_PER_HOUR
SPLIT = (0.6, 0.2)                       # 학습 · 손잡이 고르기 (나머지가 시험)
SAFE_TARGET = 0.90                       # 손잡이는 고르기 날에서 이 무해율을 지키는 것 가운데 가장 많이 빼는 값
KNOB_Q = (0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95)   # T · M의 낙폭 분위수
KNOB_U = tuple(round(x, 2) for x in np.arange(0.0, 1.0001, 0.05))
MIN_ROWS = 20                            # 단순 표 — 대여소 × 회차 행이 이보다 적으면 회차 전체 값
GBM_PARAMS = dict(max_iter=300, learning_rate=0.05, max_depth=6, min_samples_leaf=50, random_state=0)
LGB_PARAMS = dict(n_estimators=300, learning_rate=0.05, max_depth=6, min_child_samples=50, random_state=0, verbose=-1)
XGB_PARAMS = dict(n_estimators=300, learning_rate=0.05, max_depth=6, min_child_weight=50, tree_method="hist", random_state=0)
QRF_PARAMS = dict(n_estimators=200, min_samples_leaf=50, max_features=0.5, n_jobs=-1, random_state=0)
MLP_PARAMS = dict(hidden_layer_sizes=(64, 32), early_stopping=True, max_iter=200, random_state=0)
LINEAR_ROWS = 10_000                     # M3 — 6만 행이면 한 번에 7분 8초, 1만 행 표본이면 약 40초
M8_CANDS = (3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30)   # 분류형 학습 행을 늘릴 q′ 후보(+ 계획량 자체)
KNOB_Z = (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0)
TEST_FLOOR = 0.88                        # 판정 ① — 시험 날 5시간 무해가 고르기 날 목표에서 2%p 넘게 미끄러지지 않는다
CI_LEVEL = 1 - 0.05 / 2                  # 판정 ② — 대조가 T · U 둘이라 97.5%
BOOT = 2000
FEATURES = ["재고", "거치대", "꽉참", "시", "요일", "회차", "시작뒤분", "계획량", "mu", "sigma",
            "변화1h", "변화3h", "최고3h", "최저3h"]
HYBRID_Q = {"표중앙": 0.5, "표상위": 0.9}   # 혼합 입력 — 그 대여소 × 회차의 학습 날 낙폭 분위수(+ 행 수)
FEATURES_H = FEATURES + list(HYBRID_Q) + ["표행수"]
MODELS = ("M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8")
KNOBS = {"C": (None,), "P": (None,), "U": KNOB_U, "T": KNOB_Q, "Z": KNOB_Z, **{m: KNOB_Q for m in MODELS}}

# 확인 실행(54장 덧붙임 — 회사 PC 자료를 합친 뒤). 54장 등록문: "이번 실행에 없던 평일만을 확인 시험 날로 삼아, 이번 학습 날로 학습한
# 같은 모형 · 이번에 고른 손잡이로 다시 채점한다". 나눔과 손잡이는 54장 첫 실측 그대로 박아 두고 새로 고르지 않는다.
CONFIRM_UNTIL = "2026-10-12 17:00"
CONFIRM_RANGES = {"학습": ("2026-08-25", "2026-09-16"), "고르기": ("2026-09-17", "2026-09-23")}
# 54장 실행(합치기 전 이 PC, 평일 28일)에 없던 평일 — 합치기 전 DB로 돈 53장 rows.csv의 평일 28일과 맞대 가렸다(10-01 · 10-02),
# 10-12는 54장의 자료 끝 뒤
CONFIRM_DAYS = ("2026-10-01", "2026-10-02", "2026-10-12")
CONFIRM_KNOBS = {
    3 * TICKS_PER_HOUR: {"C": None, "P": None, "U": 0.7, "T": 0.95, "Z": 1.5, "M1": 0.8, "M2": 0.85, "M3": 0.85,
                         "M4": 0.8, "M5": 0.8, "M6": 0.8, "M7": 0.8, "M8": 0.85},
    5 * TICKS_PER_HOUR: {"C": None, "P": None, "U": 0.45, "T": 0.95, "Z": 2.5, "M1": 0.85, "M2": 0.85, "M3": 0.9,
                         "M4": 0.85, "M5": 0.85, "M6": 0.85, "M7": 0.85, "M8": 0.85},
}
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


def z_values(train: pd.DataFrame, target: pd.DataFrame, h: int, z: float) -> pd.Series:
    """Z — 학습 행의 대여소 × 회차 낙폭 평균 + z · 표준편차(행이 적으면 회차 전체 값). 지금 목표 재고 식의 관측판."""
    col = f"낙폭{h}"
    g = train.groupby(["station_id", "회차"])[col]
    per = (g.mean() + z * g.std(ddof=0))[g.size() >= MIN_ROWS]
    p = train.groupby("회차")[col]
    pooled = p.mean() + z * p.std(ddof=0)
    key = pd.MultiIndex.from_arrays([target["station_id"], target["회차"]])
    val = pd.Series(per.reindex(key).to_numpy(), index=target.index)
    return val.fillna(target["회차"].map(pooled))


def add_hybrid(rows: pd.DataFrame, train: pd.DataFrame, h: int) -> pd.DataFrame:
    """혼합 입력 — 그 대여소 × 회차가 학습 날에 보인 낙폭 분위수와 행 수.

    누수를 막는다 — 학습 나눔의 행에는 **그 날을 뺀** 나머지 학습 날로 낸 값을, 고르기 · 시험 행에는 학습 날 전체로 낸 값을 넣는다.
    """
    out = rows.copy()

    def fill(src: pd.DataFrame, idx: pd.Index):
        tgt = rows.loc[idx]
        for name, k in HYBRID_Q.items():
            out.loc[idx, name] = table_values(src, tgt, h, k).to_numpy()
        n = src.groupby(["station_id", "회차"]).size()
        key = pd.MultiIndex.from_arrays([tgt["station_id"], tgt["회차"]])
        out.loc[idx, "표행수"] = n.reindex(key).fillna(0).to_numpy()

    learn = rows["나눔"] == "학습"
    fill(train, rows.index[~learn])
    for d in sorted(rows.loc[learn, "날"].unique()):
        fill(train[train["날"] != d], rows.index[learn & (rows["날"] == d)])
    return out


def imputed(model):
    """결측을 중앙값 + 결측 표시 열로 메우고 표준화한다(선형 · 숲 · 신경망 — 결측을 못 받는 모형)."""
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler(), model)


def expand(rows: pd.DataFrame, cands) -> pd.DataFrame:
    """분류형 M8 — 행마다 뺄 양 후보 q′를 붙여 늘린다(계획량을 넘는 후보는 버리고, None은 계획량 자체)."""
    parts = []
    for c in cands:
        q = rows["계획량"] if c is None else pd.Series(float(c), index=rows.index)
        m = q <= rows["계획량"]
        part = rows[m].copy()
        part["빼는량"] = q[m]
        parts.append(part)
    out = pd.concat(parts)
    out["남는량"] = out["재고"] - out["빼는량"]
    return out


def predict_safe(name: str, train: pd.DataFrame, target: pd.DataFrame, calib: pd.DataFrame, h: int) -> dict:
    """모형마다 손잡이 → 대여소별 '빼도 되는 최대 대수'(재고 − 예측 낙폭 − 1, 분류형은 고른 q′)."""
    y = train[f"낙폭{h}"]
    X, Xt = train[FEATURES_H], target[FEATURES_H]
    s0 = target["재고"]

    def from_dd(pred) -> pd.Series:
        return s0 - pd.Series(np.asarray(pred), index=target.index) - 1

    if name in ("M1", "M2"):
        from sklearn.ensemble import HistGradientBoostingRegressor
        cols = FEATURES_H if name == "M1" else FEATURES
        out = {}
        for k in KNOB_Q:
            model = HistGradientBoostingRegressor(loss="quantile", quantile=k, **GBM_PARAMS).fit(train[cols], y)
            out[k] = from_dd(model.predict(target[cols]))
        return out
    if name == "M3":
        from sklearn.linear_model import QuantileRegressor
        sample = train.sample(n=min(LINEAR_ROWS, len(train)), random_state=pv.SEED)
        out = {}
        for k in KNOB_Q:
            model = imputed(QuantileRegressor(quantile=k, alpha=0.0, solver="highs"))
            out[k] = from_dd(model.fit(sample[FEATURES_H], sample[f"낙폭{h}"]).predict(Xt))
        return out
    if name == "M4":
        from quantile_forest import RandomForestQuantileRegressor
        model = imputed(RandomForestQuantileRegressor(**QRF_PARAMS)).fit(X, y)
        # 파이프라인 끝 단계에만 quantiles를 넘긴다 — 앞 단계 변환은 직접 한다
        pred = model[-1].predict(model[:-1].transform(Xt), quantiles=list(KNOB_Q))
        return {k: from_dd(pred[:, j]) for j, k in enumerate(KNOB_Q)}
    if name == "M5":
        import lightgbm as lgb
        out = {}
        for k in KNOB_Q:
            model = lgb.LGBMRegressor(objective="quantile", alpha=k, **LGB_PARAMS).fit(X, y)
            out[k] = from_dd(model.predict(Xt))
        return out
    if name == "M6":
        import xgboost as xgb
        model = xgb.XGBRegressor(objective="reg:quantileerror", quantile_alpha=np.array(KNOB_Q), **XGB_PARAMS).fit(X, y)
        pred = model.predict(Xt)
        return {k: from_dd(pred[:, j]) for j, k in enumerate(KNOB_Q)}
    if name == "M7":
        from sklearn.neural_network import MLPRegressor
        model = imputed(MLPRegressor(**MLP_PARAMS)).fit(X, y)
        resid = calib[f"낙폭{h}"].to_numpy() - model.predict(calib[FEATURES_H])   # 분할 등각 보정 — 고르기 날 잔차
        base = model.predict(Xt)
        return {k: from_dd(base + np.quantile(resid, k)) for k in KNOB_Q}
    if name == "M8":
        from sklearn.ensemble import HistGradientBoostingClassifier
        cols = FEATURES_H + ["빼는량", "남는량"]
        big = expand(train, M8_CANDS + (None,))
        label = (big[f"최저{h}"] - big["빼는량"] < 1).astype(int)        # q′를 빼면 창 안에 0대가 되나
        cst = [0] * len(FEATURES_H) + [1, -1]
        model = HistGradientBoostingClassifier(monotonic_cst=cst, **GBM_PARAMS).fit(big[cols], label)
        qmax = int(target["계획량"].max())
        grid = expand(target, tuple(range(REBAL_MIN_QTY + 1, qmax + 1)))
        grid["p"] = model.predict_proba(grid[cols])[:, 1]
        grid = grid.rename_axis("행").reset_index().sort_values(["행", "빼는량"])
        grid["p"] = grid.groupby("행")["p"].cummax()                    # 더 빼면 덜 위험해질 수는 없다
        out = {}
        for k in KNOB_Q:
            best = grid[grid["p"] <= 1 - k].groupby("행")["빼는량"].max()
            out[k] = best.reindex(target.index).fillna(0.0)          # 2대 이하는 reduce()가 0으로 만든다
        return out
    raise ValueError(name)


def method_qty(name: str, knob, rows: pd.DataFrame, h: int, train: pd.DataFrame, safe: dict) -> pd.Series:
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
    if name == "Z":
        return reduce(q, rows["재고"] - z_values(train, rows, h, knob) - 1)
    return reduce(q, safe[name][knob].reindex(rows.index))


def score(rows: pd.DataFrame, qty: pd.Series, h: int) -> dict:
    """무해(0 = 빔 — 최저 − q ≥ 1, 수거하지 않은 곳은 무해) · 뺀 총량."""
    harmless = (qty == 0) | (rows[f"최저{h}"] - qty >= 1)
    return {"무해": float(harmless.mean()), "총량": float(qty.sum()), "곳": int(len(rows)),
            "수거곳": int((qty > 0).sum()), "회차": int(rows["시작"].nunique())}


def choose(frame: pd.DataFrame) -> pd.Series:
    """고르기 날에서 무해 SAFE_TARGET 이상인 손잡이 가운데 총량이 가장 큰 것. 없으면 무해가 가장 높은 것."""
    ok = frame[frame["무해"] >= SAFE_TARGET]
    if len(ok):
        return ok.sort_values(["총량", "무해"], ascending=False).iloc[0]
    return frame.sort_values(["무해", "총량"], ascending=False).iloc[0]


def boot_total(rows: pd.DataFrame, a: pd.Series, b: pd.Series) -> tuple:
    """회차를 단위로 다시 뽑은 뺀 총량 차(a − b)의 CI_LEVEL 구간."""
    per = (a - b).groupby(rows["시작"]).sum().to_numpy()
    rng = np.random.default_rng(pv.SEED)
    draws = per[rng.integers(0, len(per), size=(BOOT, len(per)))].sum(axis=1)
    tail = (1 - CI_LEVEL) / 2
    return float(per.sum()), float(np.quantile(draws, tail)), float(np.quantile(draws, 1 - tail))


def boot_rate(rows: pd.DataFrame, qty: pd.Series, h: int) -> tuple:
    """무해율의 회차 재표집 95% 구간."""
    harm = ((qty == 0) | (rows[f"최저{h}"] - qty >= 1)).astype(float)
    g = harm.groupby(rows["시작"])
    num, den = g.sum().to_numpy(), g.size().to_numpy()
    rng = np.random.default_rng(pv.SEED)
    idx = rng.integers(0, len(num), size=(BOOT, len(num)))
    rate = num[idx].sum(axis=1) / den[idx].sum(axis=1)
    return float(np.quantile(rate, 0.025)), float(np.quantile(rate, 0.975))


def evaluate(rows: pd.DataFrame, starts: pd.Series, h: int, models=None) -> tuple:
    """한 지평 — 모형을 학습하고 손잡이별로 고르기 · 시험 날을 채점한다."""
    train = rows[(rows["나눔"] == "학습") & usable(rows, h, True)]
    rows = add_hybrid(rows, train, h)
    train = rows.loc[train.index]
    calib = rows[(rows["나눔"] == "고르기") & usable(rows, h, True)]
    pick_rows = {p: rows[(rows["나눔"] == p) & starts & usable(rows, h, False)] for p in ("고르기", "시험")}
    target = pd.concat(pick_rows.values())
    print(f"\n[{h // TICKS_PER_HOUR}시간] 학습 행 {len(train):,} · 보정 행(M7) {len(calib):,} · 채점 행 고르기 "
          f"{len(pick_rows['고르기']):,} · 시험 {len(pick_rows['시험']):,}")
    safe, took = {}, {}
    for name in models or MODELS:
        t0 = time.perf_counter()
        safe[name] = predict_safe(name, train, target, calib, h)
        took[name] = time.perf_counter() - t0
        print(f"    {name} 학습 · 예측 {format_seconds(took[name])}", flush=True)
    results, qtys = [], {}
    for name, knobs in KNOBS.items():
        if name in MODELS and name not in safe:
            continue
        for knob in knobs:
            for p, g in pick_rows.items():
                qty = method_qty(name, knob, g, h, train, safe)
                qtys[(name, knob, p)] = qty
                results.append({"지평": h // TICKS_PER_HOUR, "방법": name, "손잡이": knob, "나눔": p, **score(g, qty, h)})
    return pd.DataFrame(results), qtys, pick_rows, took


def report(frame: pd.DataFrame, qtys: dict, pick_rows: dict, h: int, took: dict, fixed: dict = None) -> None:
    """손잡이를 고르고 시험 날 값을 낸다. 주 지평이면 판정까지. `fixed`면 손잡이를 고르지 않고 그 값을 쓴다(확인 실행)."""
    test = pick_rows["시험"]
    where = "확인 날" if fixed else "시험 날"
    how = "54장 첫 실측이 고른 손잡이 그대로" if fixed else f"고르기 날에서 무해 {SAFE_TARGET:.0%}를 지키며 가장 많이 빼는 손잡이"
    print(f"\n=== {h // TICKS_PER_HOUR}시간 — {how} → {where} ===")
    print(f"방법  손잡이 | 고르기 무해  총량 | {where[:2]} 무해 (95% 구간)       총량 수거곳   곳 회차 | 수거한 곳만 무해 · 해친 곳 | 학습 · 예측")
    chosen = {}
    for name in frame["방법"].unique():
        sub = frame[frame["방법"] == name]
        if fixed:
            knob = fixed[name]
            same = sub["손잡이"].isna() if knob is None else np.isclose(sub["손잡이"].astype(float), knob)
            pick = sub[same & (sub["나눔"] == "고르기")].iloc[0]
        else:
            pick = choose(sub[sub["나눔"] == "고르기"])
            knob = None if pd.isna(pick["손잡이"]) else pick["손잡이"]
            same = sub["손잡이"].isna() if knob is None else sub["손잡이"] == knob
        t = sub[same & (sub["나눔"] == "시험")].iloc[0]
        qty = qtys[(name, pick["손잡이"] if knob is not None else None, "시험")]
        lo, hi = boot_rate(test, qty, h)
        harm = (qty == 0) | (test[f"최저{h}"] - qty >= 1)
        chosen[name] = pick["손잡이"] if knob is not None else None
        spent = format_seconds(took[name]) if name in took else "—"
        print(f"{name:<4}{'—' if knob is None else knob:>7} | {pick['무해']:>9.1%}{pick['총량']:>7.0f} | {t['무해']:>8.1%} "
              f"({lo:.1%}~{hi:.1%}){t['총량']:>7.0f}{t['수거곳']:>6}{t['곳']:>6}{t['회차']:>5} | "
              f"{harm[qty > 0].mean():.1%} · {int((~harm).sum())} | {spent}")
    if "M1" not in chosen:
        return
    m1 = qtys[("M1", chosen["M1"], "시험")]
    lower = {}
    for ref in ("T", "U", "M2"):
        if ref not in chosen:
            continue
        diff, lo, hi = boot_total(test, m1, qtys[(ref, chosen[ref], "시험")])
        lower[ref] = lo
        tag = "" if ref in ("T", "U") else "[찍기만] "
        print(f"  {tag}M1 − {ref} 뺀 총량(시험): {diff:+.0f}대 ({CI_LEVEL:.1%} 구간 {lo:+.0f} ~ {hi:+.0f})")
    if h == MAIN_HORIZON:
        rate = score(test, m1, h)["무해"]
        one = rate >= TEST_FLOOR
        two = lower["T"] > 0 and lower["U"] > 0
        name = ("채택 후보" if one and two else "보정이 날을 못 넘는다" if not one
                else "단순 표로 충분" if lower["T"] <= 0 else "균등 축소를 못 넘는다")
        print(f"\n{'확인(54장 판정은 그대로)' if fixed else '판정'} — ① M1 {where} 5시간 무해 {rate:.1%} ≥ {TEST_FLOOR:.0%}: "
              f"{'✅' if one else '❌'} · ② 뺀 총량 > T · U(구간 하한 > 0): {'✅' if two else '❌'} → {name}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sanity", action="store_true", help="결과 없이 입력만 점검한다(행 수 · 날짜 나누기 · 결측)")
    parser.add_argument("--until", help="이 시각 전의 관측만 쓴다 — 등록 때 고정한다")
    parser.add_argument("--models", nargs="*", choices=MODELS, help="이 모형만 돌린다(점검용, 기본은 전부)")
    parser.add_argument("--confirm", action="store_true",
                        help=f"확인 실행 — 54장의 나눔 · 손잡이 그대로, 확인 날 {', '.join(CONFIRM_DAYS)}만 채점(끝 {CONFIRM_UNTIL})")
    args = parser.parse_args(argv)
    if args.confirm:
        if args.until and args.until != CONFIRM_UNTIL:
            parser.error(f"--confirm은 끝을 {CONFIRM_UNTIL}로 고정한다")
        args.until = CONFIRM_UNTIL

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
    print("패키지: " + " · ".join(f"{m} {__import__(m).__version__}" for m in ("sklearn", "lightgbm", "xgboost", "quantile_forest")))

    arrays = window_arrays(grid)
    rows = build_rows(grid, refs, arrays, set(weekdays))
    if args.confirm:                              # 54장 첫 실측의 나눔 그대로 + 확인 날. 그 밖의 날(54장 시험 날 등)은 쓰지 않는다
        confirm = {pd.Timestamp(d).date() for d in CONFIRM_DAYS}
        split = {d: next((p for p, (a, b) in CONFIRM_RANGES.items() if pd.Timestamp(a).date() <= d <= pd.Timestamp(b).date()),
                         "시험" if d in confirm else None) for d in rows["날"].unique()}
        split = {d: p for d, p in split.items() if p}
        rows = rows[rows["날"].isin(split)].reset_index(drop=True)
        print(f"확인 실행 — 시험 = 확인 날 {', '.join(CONFIRM_DAYS)} · 손잡이는 54장 첫 실측 값")
    else:
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
    frames = []
    for h in HORIZONS:
        frame, qtys, pick_rows, took = evaluate(rows, starts, h, args.models)
        frames.append(frame)
        report(frame, qtys, pick_rows, h, took, CONFIRM_KNOBS[h] if args.confirm else None)
    out = OUT_DIR / ("knobs_confirm.csv" if args.confirm else "knobs.csv")
    pd.concat(frames).to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n손잡이별 결과 — {out}")
    return 0


if __name__ == "__main__":
    project_config.exit_if_help(__doc__)
    raise SystemExit(main())
