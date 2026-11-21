"""이웃 대여소 재고 피처의 신호 검사: 결품 예측 GBM(ML 9번)에 가까운 다섯 곳의 재고를 더하면 표본 밖 Brier가 내려가나.

왜 재나
  순수요 예측에서는 이웃 정보가 세 정의(위치 · 행동 · 흐름) 모두 졌다(ML_ATTEMPTS 5 · 8번, 셋 다 w = 0이 최적).
  재고에서는 잰 적이 없다. ML_고도화_계획 4장은 그래프 모형(GNN)으로 가기 전의 검사로 이것을 적어 두었다:
  결품 예측 GBM에 이웃 대여소의 재고를 피처로 더해 Brier가 더 내려가는가.
  F2(결품 확률로 배송 순서 바꾸기, 41장)는 평소 잘 비지 않는 구간 [0, 0.4)에서 결품이 늘어 보류되었으므로,
  그 구간의 예측이 이웃 정보로 나아지는지도 함께 본다.

무엇을 재나
  A(기준) = ML 9번 그대로: `stockout_forecast.FEATURES` 11개, h틱 뒤 빔이 타깃, 같은 GBM 설정,
            '대여소시간대빔비율'은 학습 행에서만 만든다.
  B(이웃) = A + 이웃 피처 넷. 이웃은 좌표가 있는 대여소 가운데 가장 가까운 다섯 곳(haversine, 자기 제외).
            이웃채움률(재고 ÷ 거치대 평균) · 이웃빈비율(재고 0 비율) · 이웃재고변화6틱(1시간 변화 평균) · 최근접거리(km).
            모두 t와 그 이전 값만 쓴다. 행은 A와 같다(이웃 결측은 GBM이 다룬다).
  두 원점(검증 4일 고정) × 지평 6 · 18틱(1 · 3시간) = 네 조합, 씨앗 42 · 7 · 13.

  격자는 이어진 시간(주말 · 휴일 포함)으로 만들고 학습 · 검증 **행만 평일**로 고른다. 지연 · 이동 평균은 ML 9번처럼
  틱 행으로 밀기 때문에, 주말을 빼고 격자를 만들면 금요일 밤에서 월요일 아침으로 건너뛴다.
  평일 판정은 `project_config.holiday_mask` 하나다(`db.load_stock_history(day_type=...)`와 같은 달력).

판정 (사전 등록 2026-11-21 13:20, 결과를 보고 바꾸지 않는다. 노트: 세션 scratchpad results/6_neighbor.md)
  1. 신호 있음: R = Brier_B / Brier_A − 1 ≤ −2%인 조합이 넷 중 셋 이상이고, 그 조합에서 B의 보정 최대 오차가
     A의 최대 오차 + 0.02 이하. 아니면 신호 없음(그래프 모형도 근거 없음).
  2. F2 재검토 근거: '대여소시간대빔비율' [0, 0.4) 구간에서 R ≤ −2%인 조합이 셋 이상.
  3. 1이 통과할 때만: 공간 대조(이웃을 무작위 다섯으로 바꾼 위약, 최근접거리는 그대로)보다 B가 상대 1% 이상 낮은
     조합이 셋 이상이어야 '공간 신호'. 누출 검사(라벨을 섞은 B가 '늘 학습 평균'의 0.96배보다 커야 정상).
  4. 멈춤: 씨앗 42에서 네 조합 모두 R > −0.5%면 씨앗 7 · 13과 3을 돌리지 않는다.

자원
  대여소 × 틱 float32 배열로 접고, 긴 표는 학습 행이 앞에 오도록 시간순으로 둔다(학습 행렬을 복사 없이 자른다).
  GBM 스레드는 4개(`OMP_NUM_THREADS`). 무거운 실행은 세션의 잠금 래퍼로 돌린다(재고 수집기와 메모리를 다툰다).

실행
    python experiments/structure/neighbor_stock_signal.py --selfcheck
    python experiments/structure/neighbor_stock_signal.py --out experiments/structure/neighbor_stock_signal_261121.csv
    python experiments/structure/neighbor_stock_signal.py --seeds 42 --horizons 6     # 일부만
"""
from __future__ import annotations

import os

os.environ.setdefault("OMP_NUM_THREADS", "4")   # sklearn을 싣기 전에 정해야 듣는다

import argparse  # noqa: E402
import ctypes  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import warnings  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import project_config  # noqa: E402,F401  콘솔 인코딩을 먼저 맞춘다
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import db  # noqa: E402
import stockout_forecast as sf  # noqa: E402
from experiments._shared import plan_label_before  # noqa: E402
from project_config import STOCK_TICK_MINUTES, TICKS_PER_HOUR, haversine_km, holiday_mask  # noqa: E402

# ── 사전 등록 (2026-11-21). 결과를 보고 바꾸지 않는다
GRID_START = "2026-09-14"          # 09-15 첫 틱의 지연 피처를 채울 하루
GRID_END = "2026-10-28"            # 이 날 0시 전까지. 10-28부터는 이 PC 수집이 하루 15 ~ 65틱
FULL_DAY_RATIO = 0.8               # 행으로 쓰는 날: 하루 틱의 80% 이상(41장 F2와 같다)
ORIGINS = {                        # 원점: (검증 첫날, 검증 끝날). 학습은 검증 첫날 앞의 평일 전부
    "E": ("2026-10-16", "2026-10-21"),
    "L": ("2026-10-22", "2026-10-27"),
}
TEST_DAYS = 4                      # 원점마다 검증 평일 4일(48장 · ML 9번의 검증 4일)
HORIZONS = (6, 18)                 # 1시간 · 3시간 뒤
SEEDS = (42, 7, 13)
K_NEIGHBORS = 5
NEIGHBOR_FEATURES = ["이웃채움률", "이웃빈비율", "이웃재고변화6틱", "최근접거리"]
FEATURES_A = list(sf.FEATURES)     # ML 9번 피처 11개 그대로
FEATURES_B = FEATURES_A + NEIGHBOR_FEATURES
GAIN_CUT = -0.02                   # 판정 1 · 2: 상대 −2% 이하
GAIN_COMBOS = 3                    # 넷 가운데 셋 이상
CALIB_TOLERANCE = 0.02             # 보정 최대 오차 허용 증가
CALIB_BIN_MIN = 1000               # 보정 칸 최소 행 수
SPATIAL_CUT = 0.01                 # 판정 3: 무작위 이웃보다 상대 1% 이상 낮아야
STOP_CUT = -0.005                  # 판정 4: 씨앗 42에서 네 조합 모두 이보다 크면 멈춘다
LOW_BAND = (0.0, 0.4)              # F2가 진 구간
PLACEBO_RNG = 42
GBM_PARAMS = dict(max_iter=200, learning_rate=0.1, max_depth=6)   # ML 9번과 같다

# 긴 표의 열 순서: A의 11개가 앞, 이웃 넷이 뒤. 그래야 A · B 행렬을 열 조각(복사 없는 보기)으로 자른다.
BASE_COL = FEATURES_A.index("대여소시간대빔비율")


# ───────────────────────────────────────────── 자원 기록

class _ProcMem(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


def peak_gb() -> float:
    """이 프로세스의 최대 작업 집합(GB). 윈도우가 아니면 nan."""
    try:
        k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ProcMem), ctypes.c_ulong]
        counters = _ProcMem()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return float("nan")
        return counters.PeakWorkingSetSize / 1024 ** 3
    except (AttributeError, OSError):
        return float("nan")


# ───────────────────────────────────────────── 자료

def load_grid(conn, start: str, end: str):
    """[start, end)의 관측을 (틱 × 대여소) float32 배열로 편다. 하루씩 읽어 문자열 표를 크게 들지 않는다.

    반환: (ts: DatetimeIndex, stations: list, stock: (T, S) float32, 관측 없음 = nan)
    격자의 행은 관측된 시각 전부다(ML 9번 `pivot_table`의 행과 같다).
    """
    stations = [r[0] for r in conn.execute(
        "SELECT DISTINCT station_id FROM stock_history WHERE observed_at >= ? AND observed_at < ?"
        " ORDER BY station_id", (start, end))]
    parts = []
    for day in pd.date_range(start, end, freq="D", inclusive="left"):
        lo, hi = day.strftime("%Y-%m-%d"), (day + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        chunk = pd.read_sql_query(
            "SELECT observed_at, station_id, stock FROM stock_history"
            " WHERE observed_at >= ? AND observed_at < ?", conn, params=[lo, hi])
        if chunk.empty:
            continue
        parts.append((pd.to_datetime(chunk["observed_at"]).to_numpy(),
                      pd.Categorical(chunk["station_id"], categories=stations).codes.astype(np.int32),
                      chunk["stock"].to_numpy(dtype=np.float32)))
    stamps = np.concatenate([p[0] for p in parts])
    ts = pd.DatetimeIndex(np.unique(stamps))
    stock = np.full((len(ts), len(stations)), np.nan, dtype=np.float32)
    for when, code, value in parts:
        stock[ts.get_indexer(when), code] = value
    return ts, stations, stock


def shift_rows(a: np.ndarray, n: int) -> np.ndarray:
    """pandas `shift(n)`와 같다: n > 0이면 n행 앞(과거) 값을, n < 0이면 뒤(미래) 값을 가져온다."""
    out = np.full_like(a, np.nan)
    if n > 0:
        out[n:] = a[:-n]
    elif n < 0:
        out[:n] = a[-n:]
    else:
        out[:] = a
    return out


def rolling_mean(a: np.ndarray, window: int, min_periods: int) -> np.ndarray:
    """pandas `rolling(window, min_periods).mean()`과 같다(결측은 빼고 세며, 관측이 min_periods 미만이면 nan)."""
    return pd.DataFrame(a).rolling(window, min_periods=min_periods).mean().to_numpy(dtype=np.float32)


def empty_of(stock: np.ndarray) -> np.ndarray:
    return np.where(np.isnan(stock), np.nan, (stock == 0)).astype(np.float32)


def own_features(stock: np.ndarray, horizon: int) -> dict:
    """ML 9번 `build_features()`의 열을 (T, S) 배열로 만든다. 시 · 요일 · 대여소시간대빔비율은 행을 고른 뒤 붙인다."""
    empty = empty_of(stock)
    return {
        "타깃": shift_rows(empty, -horizon),
        "지금빔": empty,
        "재고": stock,
        "1틱전빔": shift_rows(empty, 1),
        "3틱전빔": shift_rows(empty, 3),
        "6틱전빔": shift_rows(empty, 6),
        "최근6틱빔비율": rolling_mean(empty, 6, 3),
        "최근18틱빔비율": rolling_mean(empty, 18, 6),
        "재고변화3틱": stock - shift_rows(stock, 3),
    }


def nearest_neighbors(stations: list, info: pd.DataFrame, k: int, rng=None):
    """대여소마다 이웃 k곳의 열 번호(없으면 −1)와 최근접 거리(km).

    rng를 주면 이웃을 좌표가 있는 대여소 가운데 무작위로 고른다(공간 대조). 최근접거리는 그때도 진짜 값이다.
    """
    coords = info.set_index("station_id").reindex(stations)
    lat = coords["lat"].to_numpy(dtype=float)
    lon = coords["lon"].to_numpy(dtype=float)
    has = ~(np.isnan(lat) | np.isnan(lon))
    dist = haversine_km(lat[:, None], lon[:, None], lat[None, :], lon[None, :])
    dist = np.where(has[:, None] & has[None, :], dist, np.inf)
    np.fill_diagonal(dist, np.inf)
    order = np.argsort(dist, axis=1)[:, :k]
    nearest = np.where(has, dist[np.arange(len(stations)), order[:, 0]], np.nan)
    neighbors = np.where(has[:, None], order, -1)
    if rng is not None:
        pool = np.flatnonzero(has)
        neighbors = np.full((len(stations), k), -1, dtype=np.int64)
        for i in np.flatnonzero(has):
            neighbors[i] = rng.choice(pool[pool != i], size=k, replace=False)
    return neighbors, nearest.astype(np.float32)


def neighbor_mean(a: np.ndarray, neighbors: np.ndarray, chunk: int = 512) -> np.ndarray:
    """(T, S) 배열의 이웃 평균(결측 이웃은 빼고, 다 없으면 nan). 틱을 조각내 (T, S, k) 임시 배열을 작게 둔다."""
    T, S = a.shape
    index = np.where(neighbors < 0, S, neighbors)
    out = np.empty((T, S), dtype=np.float32)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)   # 이웃이 다 결측인 칸
        for lo in range(0, T, chunk):
            block = np.concatenate([a[lo:lo + chunk], np.full((min(chunk, T - lo), 1), np.nan, np.float32)], axis=1)
            out[lo:lo + chunk] = np.nanmean(block[:, index], axis=2)
    return out


def neighbor_features(stock: np.ndarray, parking: np.ndarray, neighbors: np.ndarray,
                      nearest: np.ndarray) -> dict:
    fill = stock / np.where(parking > 0, parking, np.nan)[None, :].astype(np.float32)
    return {
        "이웃채움률": neighbor_mean(fill.astype(np.float32), neighbors),
        "이웃빈비율": neighbor_mean(empty_of(stock), neighbors),
        "이웃재고변화6틱": neighbor_mean(stock - shift_rows(stock, 6), neighbors),
        "최근접거리": np.broadcast_to(nearest[None, :], stock.shape),
    }


def row_days(ts: pd.DatetimeIndex) -> np.ndarray:
    return ts.normalize().to_numpy()


def usable_days(ts: pd.DatetimeIndex) -> list:
    """행으로 쓰는 날: 평일이고 하루 틱의 80% 이상 관측된 날(격자 첫날은 지연 채움용이라 뺀다)."""
    days = pd.Series(row_days(ts))
    ticks = days.value_counts()
    need = int(24 * TICKS_PER_HOUR * FULL_DAY_RATIO)
    holiday = holiday_mask(pd.Series(ticks.index))
    keep = [d for d, h in zip(ticks.index, holiday) if not h and ticks[d] >= need]
    return sorted(pd.Timestamp(d) for d in keep if pd.Timestamp(d) > pd.Timestamp(GRID_START))


def build_long(ts, stock, horizon, days, extra=None):
    """행을 고르고 긴 표(행 × 열)를 만든다. 행은 시간순(틱, 대여소)이라 학습 날이 앞에 모인다.

    반환: (L: (n, 열) float32, meta: dict(t, s, day, 타깃)), 열 = FEATURES_A (+ extra의 열들)
    """
    own = own_features(stock, horizon)
    valid = np.ones(stock.shape, dtype=bool)
    for name in ("타깃", "지금빔", "재고", "1틱전빔", "3틱전빔", "6틱전빔", "최근6틱빔비율", "최근18틱빔비율", "재고변화3틱"):
        valid &= ~np.isnan(own[name])
    in_days = np.isin(row_days(ts), np.array(days, dtype="datetime64[ns]"))
    valid &= in_days[:, None]
    t_idx, s_idx = np.nonzero(valid)
    columns = FEATURES_A + (list(extra) if extra else [])
    L = np.empty((len(t_idx), len(columns)), dtype=np.float32)
    for j, name in enumerate(columns):
        if name == "시":
            L[:, j] = ts.hour.to_numpy()[t_idx]
        elif name == "요일":
            L[:, j] = ts.weekday.to_numpy()[t_idx]
        elif name == "대여소시간대빔비율":
            L[:, j] = np.nan            # 원점마다 학습 행으로 채운다
        elif name in own:
            L[:, j] = own[name][t_idx, s_idx]
        else:
            L[:, j] = extra[name][t_idx, s_idx]
    meta = {"t": t_idx, "s": s_idx, "day": row_days(ts)[t_idx], "타깃": own["타깃"][t_idx, s_idx].astype(np.int8)}
    return L, meta


def fill_base_rate(L, meta, n_train, n_stations):
    """'대여소시간대빔비율' = 학습 행의 (대여소, 시) 지금빔 평균, 없으면 학습 전체 평균(`sf.add_station_history`와 같다)."""
    hour = L[:, FEATURES_A.index("시")].astype(np.int64)
    key = meta["s"].astype(np.int64) * 24 + hour
    now = L[:, FEATURES_A.index("지금빔")].astype(np.float64)
    total = np.bincount(key[:n_train], weights=now[:n_train], minlength=n_stations * 24)
    count = np.bincount(key[:n_train], minlength=n_stations * 24)
    overall = now[:n_train].mean()
    rate = np.where(count > 0, total / np.maximum(count, 1), overall)
    L[:, BASE_COL] = rate[key].astype(np.float32)


# ───────────────────────────────────────────── 지표

def brier(prob, actual) -> float:
    return float(np.mean((np.asarray(prob, dtype=float) - np.asarray(actual, dtype=float)) ** 2))


def calibration(prob, actual):
    """10등분 칸(칸마다 `CALIB_BIN_MIN`행 이상)의 |평균 예측 − 실제 빈도| 최댓값과 가중 평균(ECE)."""
    prob = np.asarray(prob, dtype=float)
    actual = np.asarray(actual, dtype=float)
    which = np.clip((prob * 10).astype(int), 0, 9)
    worst, ece = 0.0, 0.0
    for b in range(10):
        sel = which == b
        if sel.sum() < CALIB_BIN_MIN:
            continue
        gap = abs(prob[sel].mean() - actual[sel].mean())
        worst = max(worst, gap)
        ece += gap * sel.sum() / len(prob)
    return worst, ece


def score_rows(prob, actual, base_rate, days):
    """전체 · 두 구간 · 날마다의 Brier와 보정."""
    out = []
    worst, ece = calibration(prob, actual)
    out.append(("전체", len(actual), brier(prob, actual), worst, ece))
    low = (base_rate >= LOW_BAND[0]) & (base_rate < LOW_BAND[1])
    for name, sel in (("[0,0.4)", low), ("[0.4,1]", ~low)):
        if sel.sum():
            out.append((name, int(sel.sum()), brier(prob[sel], actual[sel]), np.nan, np.nan))
    for day in np.unique(days):
        sel = days == day
        out.append((f"일:{pd.Timestamp(day).date()}", int(sel.sum()), brier(prob[sel], actual[sel]), np.nan, np.nan))
    return out


# ───────────────────────────────────────────── 학습 · 평가

def fit_predict(X_train, y_train, X_test, seed):
    from sklearn.ensemble import HistGradientBoostingClassifier

    model = HistGradientBoostingClassifier(**GBM_PARAMS, random_state=seed)
    model.fit(X_train, y_train)
    return model.predict_proba(X_test)[:, 1]


def origin_split(meta, days, origin):
    """(학습 행 수, 검증 행 범위, 학습 날 수). 행이 시간순이라 학습은 앞 조각, 검증은 그 뒤 조각이다."""
    first, last = (pd.Timestamp(d) for d in ORIGINS[origin])
    test_days = [d for d in days if first <= d <= last]
    if len(test_days) != TEST_DAYS:
        raise SystemExit(f"원점 {origin}: 검증 평일이 {len(test_days)}일이다(등록 {TEST_DAYS}일)")
    n_train = int(np.searchsorted(meta["day"], np.datetime64(first.to_datetime64(), "ns"), side="left"))
    n_end = int(np.searchsorted(meta["day"], np.datetime64(last.to_datetime64(), "ns"), side="right"))
    train_days = [d for d in days if d < first]
    return n_train, (n_train, n_end), len(train_days)


def run_pass(ts, stations, stock, days, parking, neighbors, nearest, horizons, seeds, models,
             rows, placebo=None, log=print):
    """horizons × 원점 × seeds × models를 돌려 rows에 쌓는다. models: 'A' · 'B' · 'B무작위'."""
    for horizon in horizons:
        started = time.time()
        extra = neighbor_features(stock, parking, neighbors, nearest)
        if "B무작위" in models:
            rand = neighbor_features(stock, parking, placebo, nearest)
            extra.update({f"{k}_무작위": v for k, v in rand.items() if k != "최근접거리"})
        L, meta = build_long(ts, stock, horizon, days, extra)
        del extra
        log(f"\n[지평 {horizon}틱 = {horizon * STOCK_TICK_MINUTES}분] 행 {len(L):,} · 열 {L.shape[1]}"
            f" · 표 {L.nbytes / 1024 ** 3:.2f}GB · {time.time() - started:.0f}초 · 최대 메모리 {peak_gb():.2f}GB")
        # 공간 대조의 열: A 11개 + 진짜 최근접거리 + 무작위 이웃의 움직이는 피처 셋(extra에 그 순서로 붙는다)
        cols_rand = list(range(len(FEATURES_A))) + [len(FEATURES_A) + NEIGHBOR_FEATURES.index("최근접거리")]
        cols_rand += [len(FEATURES_B) + i for i in range(len(NEIGHBOR_FEATURES) - 1)]
        for origin in ORIGINS:
            n_train, (lo, hi), n_train_days = origin_split(meta, days, origin)
            fill_base_rate(L, meta, n_train, len(stations))
            y_train = meta["타깃"][:n_train]
            y_test = meta["타깃"][lo:hi]
            base_test = L[lo:hi, BASE_COL]
            days_test = meta["day"][lo:hi]
            log(f"  원점 {origin}: 학습 평일 {n_train_days}일 {n_train:,}행 · 검증 {TEST_DAYS}일 {hi - lo:,}행"
                f" · 검증 빔 비율 {y_test.mean():.1%}")
            if seeds and seeds[0] == SEEDS[0] and "A" in models:
                for name, prob in (("지속", L[lo:hi, FEATURES_A.index("지금빔")]),
                                   ("과거빈도", base_test),
                                   ("전체평균", np.full(hi - lo, y_train.mean()))):
                    for seg, n, b, worst, ece in score_rows(prob, y_test, base_test, days_test):
                        rows.append(dict(지평=horizon, 원점=origin, 씨앗=-1, 모형=name, 구분=seg, n=n,
                                         brier=b, 보정최대=worst, ece=ece))
            for seed in seeds:
                for model in models:
                    t0 = time.time()
                    if model == "A":
                        X = L[:n_train, :len(FEATURES_A)].astype(np.float64)
                        Xt = L[lo:hi, :len(FEATURES_A)]
                    elif model == "B":
                        X = L[:n_train, :len(FEATURES_B)].astype(np.float64)
                        Xt = L[lo:hi, :len(FEATURES_B)]
                    else:
                        X = L[:n_train][:, cols_rand].astype(np.float64)
                        Xt = L[lo:hi][:, cols_rand]
                    prob = fit_predict(X, y_train, Xt, seed)
                    del X
                    for seg, n, b, worst, ece in score_rows(prob, y_test, base_test, days_test):
                        rows.append(dict(지평=horizon, 원점=origin, 씨앗=seed, 모형=model, 구분=seg, n=n,
                                         brier=b, 보정최대=worst, ece=ece))
                    whole = [r for r in rows if r["지평"] == horizon and r["원점"] == origin
                             and r["씨앗"] == seed and r["모형"] == model and r["구분"] == "전체"][-1]
                    log(f"    씨앗 {seed:>2} {model:<5} Brier {whole['brier']:.5f} · 보정 최대 {whole['보정최대']:.3f}"
                        f" · {time.time() - t0:.0f}초 · 최대 메모리 {peak_gb():.2f}GB")
        del L, meta


def leak_check(ts, stations, stock, days, parking, neighbors, nearest, log=print):
    """라벨을 섞은 B(원점 L · 1시간 · 씨앗 42)가 '늘 학습 평균'의 0.96배보다 커야 정상(ML 9번 `LEAK_RATIO`)."""
    extra = neighbor_features(stock, parking, neighbors, nearest)
    L, meta = build_long(ts, stock, HORIZONS[0], days, extra)
    del extra
    n_train, (lo, hi), _ = origin_split(meta, days, "L")
    fill_base_rate(L, meta, n_train, len(stations))
    y_train = np.random.default_rng(42).permutation(meta["타깃"][:n_train])
    y_test = meta["타깃"][lo:hi]
    prob = fit_predict(L[:n_train, :len(FEATURES_B)].astype(np.float64), y_train, L[lo:hi, :len(FEATURES_B)], 42)
    b = brier(prob, y_test)
    base = brier(np.full(hi - lo, meta["타깃"][:n_train].mean()), y_test)
    ok = b > base * sf.LEAK_RATIO
    log(f"\n누출 검사(라벨 섞은 B): Brier {b:.5f} · 늘 학습 평균 {base:.5f} → {'정상' if ok else '누출 의심'}")
    return ok, b, base


# ───────────────────────────────────────────── 판정

def seed_mean(table: pd.DataFrame) -> pd.DataFrame:
    part = table[table["씨앗"] >= 0]
    return part.groupby(["지평", "원점", "모형", "구분"], as_index=False)[["brier", "보정최대", "ece"]].mean()


def judge(table: pd.DataFrame, log=print) -> dict:
    mean = seed_mean(table)
    seeds_used = sorted(table.loc[table["씨앗"] >= 0, "씨앗"].unique().tolist())
    combos = sorted({(h, o) for h, o in zip(mean["지평"], mean["원점"])})

    def pick(h, o, m, seg="전체", col="brier"):
        sel = mean[(mean["지평"] == h) & (mean["원점"] == o) & (mean["모형"] == m) & (mean["구분"] == seg)]
        return float(sel[col].iloc[0]) if len(sel) else float("nan")

    def base(h, o, m, seg="전체"):
        sel = table[(table["지평"] == h) & (table["원점"] == o) & (table["모형"] == m)
                    & (table["씨앗"] == -1) & (table["구분"] == seg)]
        return float(sel["brier"].iloc[0]) if len(sel) else float("nan")

    log("\n" + "=" * 100)
    log(f"판정 (사전 등록 2026-11-21) · 씨앗 {seeds_used} 평균")
    log("=" * 100)
    log(f"{'지평':>4} {'원점':>3} | {'지속':>8} {'과거빈도':>8} | {'A':>8} {'B':>8} {'R(B/A)':>8} | "
        f"{'보정A':>6} {'보정B':>6} | {'[0,0.4) A':>9} {'B':>8} {'R':>7} | 날 B<A")
    gains, low_gains, calib_ok, days_better, days_total = [], [], [], 0, 0
    for h, o in combos:
        a, b = pick(h, o, "A"), pick(h, o, "B")
        r = b / a - 1
        ca, cb = pick(h, o, "A", col="보정최대"), pick(h, o, "B", col="보정최대")
        la, lb = pick(h, o, "A", "[0,0.4)"), pick(h, o, "B", "[0,0.4)")
        lr = lb / la - 1
        day_segs = sorted(s for s in mean["구분"].unique() if s.startswith("일:"))
        better = sum(1 for s in day_segs if pick(h, o, "B", s) < pick(h, o, "A", s))
        present = sum(1 for s in day_segs if not np.isnan(pick(h, o, "A", s)))
        days_better += better
        days_total += present
        gains.append(r)
        low_gains.append(lr)
        calib_ok.append(cb <= ca + CALIB_TOLERANCE)
        log(f"{h:>4} {o:>3} | {base(h, o, '지속'):8.5f} {base(h, o, '과거빈도'):8.5f} | {a:8.5f} {b:8.5f} {r:+8.2%} | "
            f"{ca:6.3f} {cb:6.3f} | {la:9.5f} {lb:8.5f} {lr:+7.2%} | {better}/{present}")
    pass1 = sum(1 for r, c in zip(gains, calib_ok) if r <= GAIN_CUT and c) >= GAIN_COMBOS
    pass2 = sum(1 for r in low_gains if r <= GAIN_CUT) >= GAIN_COMBOS
    stop = all(r > STOP_CUT for r in gains)
    log(f"\n  판정 1 신호: R ≤ {GAIN_CUT:.0%}이고 보정이 나빠지지 않은 조합 "
        f"{sum(1 for r, c in zip(gains, calib_ok) if r <= GAIN_CUT and c)}/{len(gains)} → {'신호 있음' if pass1 else '신호 없음'}")
    log(f"  판정 2 F2 재검토: [0, 0.4)에서 R ≤ {GAIN_CUT:.0%}인 조합 {sum(1 for r in low_gains if r <= GAIN_CUT)}/{len(low_gains)}"
        f" → {'근거 있음' if pass2 else '근거 없음'}")
    log(f"  날마다 B < A: {days_better}/{days_total}칸")
    out = dict(gains=gains, low_gains=low_gains, pass1=pass1, pass2=pass2, stop=stop, seeds=seeds_used,
               days_better=days_better, days_total=days_total)
    if (mean["모형"] == "B무작위").any():
        spatial = []
        for h, o in combos:
            br, b = pick(h, o, "B무작위"), pick(h, o, "B")
            spatial.append(br / b - 1)
            log(f"  공간 대조 {h}틱 {o}: B무작위 {br:.5f} · B {b:.5f} · 무작위/B − 1 = {br / b - 1:+.2%}")
        out["spatial"] = spatial
        out["pass3"] = sum(1 for s in spatial if s >= SPATIAL_CUT) >= GAIN_COMBOS
        log(f"  판정 3 공간 신호: {'있음' if out['pass3'] else '없음'}")
    return out


# ───────────────────────────────────────────── 자기 검사

def selfcheck(conn, log=print) -> int:
    """A 피처가 ML 9번 `build_features()`와 같은 값인지 사흘치로 맞댄다(허용 1e-5)."""
    start, end = "2026-10-06", "2026-10-09"
    frame = db.load_stock_history(conn, start=start, end="2026-10-08")
    frame["ts"] = pd.to_datetime(frame["observed_at"])
    frame["빔"] = (frame["stock"] == 0).astype(np.int8)
    ts, stations, stock = load_grid(conn, start, end)
    worst_all = 0.0
    for horizon in HORIZONS:
        ref = sf.build_features(frame, horizon)
        days = sorted(pd.Timestamp(d) for d in np.unique(row_days(ts)))
        L, meta = build_long(ts, stock, horizon, days)
        mine = pd.DataFrame(L, columns=FEATURES_A)
        mine["ts"] = ts[meta["t"]]
        mine["station_id"] = np.asarray(stations)[meta["s"]]
        mine["타깃"] = meta["타깃"]
        joined = ref.merge(mine, on=["ts", "station_id"], how="outer", suffixes=("_ref", ""), indicator=True)
        only = joined["_merge"].value_counts().to_dict()
        log(f"[자기 검사 {horizon}틱] 행: ML 9번 {len(ref):,} · 이 스크립트 {len(mine):,} · 짝 {only}")
        both = joined[joined["_merge"] == "both"]
        for name in ["타깃"] + [f for f in FEATURES_A if f != "대여소시간대빔비율"]:
            gap = float(np.nanmax(np.abs(both[f"{name}_ref"].astype(float) - both[name].astype(float))))
            worst_all = max(worst_all, gap)
            if gap > 1e-5:
                log(f"   열 {name}: 최대 차 {gap}")
        if only.get("left_only", 0) or only.get("right_only", 0):
            worst_all = max(worst_all, 1.0)
    ok = worst_all <= 1e-5
    log(f"[자기 검사] {'같다' if ok else '다르다'} (최대 차 {worst_all:.2e}) · 최대 메모리 {peak_gb():.2f}GB")
    return 0 if ok else 1


# ───────────────────────────────────────────── 실행

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--selfcheck", action="store_true", help="A 피처가 ML 9번과 같은지 사흘치로 맞댄다")
    parser.add_argument("--seeds", default="", help="씨앗(쉼표). 비우면 등록 절차(42 먼저, 멈춤 규칙, 7 · 13)")
    parser.add_argument("--horizons", default=",".join(str(h) for h in HORIZONS))
    parser.add_argument("--out", default="", help="결과 행을 남길 CSV")
    args = parser.parse_args(argv)

    with db.session() as conn:
        if args.selfcheck:
            return selfcheck(conn)
        label = plan_label_before(conn, until=f"{GRID_END} 00:00")
        info = pd.read_sql_query("SELECT station_id, lat, lon, parking_lot FROM station_info WHERE run_label = ?",
                                 conn, params=[label])
        started = time.time()
        ts, stations, stock = load_grid(conn, GRID_START, GRID_END)
    days = usable_days(ts)
    print(f"대여소 정보 = '{label}' station_info ({len(info):,}곳, 자료 끝 전 가장 최근 계획)")
    print(f"격자 {GRID_START} ~ {GRID_END} 전: 틱 {len(ts):,} × 대여소 {len(stations):,}"
          f" · {time.time() - started:.0f}초 · 최대 메모리 {peak_gb():.2f}GB")
    print(f"행으로 쓰는 평일 {len(days)}일: {days[0].date()} ~ {days[-1].date()}")
    in_info = set(info["station_id"])
    print(f"좌표 없는 대여소 {sum(1 for s in stations if s not in in_info)}곳(이웃 피처 결측)")

    parking = info.set_index("station_id").reindex(stations)["parking_lot"].to_numpy(dtype=np.float32)
    neighbors, nearest = nearest_neighbors(stations, info, K_NEIGHBORS)
    placebo, _ = nearest_neighbors(stations, info, K_NEIGHBORS, rng=np.random.default_rng(PLACEBO_RNG))
    print(f"최근접 거리 중앙 {np.nanmedian(nearest):.3f}km · 90분위 {np.nanpercentile(nearest, 90):.3f}km")

    horizons = [int(h) for h in args.horizons.split(",") if h.strip()]
    rows: list = []
    common = (ts, stations, stock, days, parking, neighbors, nearest)
    if args.seeds:
        seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
        run_pass(*common, horizons, seeds, ("A", "B"), rows)
        verdict = judge(pd.DataFrame(rows))
    else:
        run_pass(*common, horizons, [SEEDS[0]], ("A", "B"), rows)
        first = judge(pd.DataFrame(rows))
        if first["stop"]:
            print(f"\n멈춤 규칙: 씨앗 {SEEDS[0]}에서 네 조합 모두 R > {STOP_CUT:.1%}다. 씨앗 7 · 13과 공간 대조를 돌리지 않는다.")
            verdict = first
        else:
            run_pass(*common, horizons, list(SEEDS[1:]), ("A", "B"), rows)
            verdict = judge(pd.DataFrame(rows))
            if verdict["pass1"]:
                run_pass(*common, horizons, list(SEEDS), ("B무작위",), rows, placebo=placebo)
                verdict = judge(pd.DataFrame(rows))
                leak_check(*common)
    if args.out:
        pd.DataFrame(rows).to_csv(args.out, index=False, encoding="utf-8-sig")
        print(f"\n행 {len(rows)}개를 {args.out}에 남겼다.")
    print(f"\n걸린 시간 {(time.time() - started) / 60:.1f}분 · 최대 메모리 {peak_gb():.2f}GB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
