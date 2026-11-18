"""계획을 세운 뒤 시간이 지나면 뒷순번 대여소는 얼마나 어긋나나, 언제 다시 짜야 하나, 어떤 설정이 끝까지 계획을 지키나 (EXPERIMENTS 65장).

왜: 63장은 계획 줄 전부를 **출발 시각** 재고로 대조했다. 그러나 차는 대여소를 하나씩 돌므로 뒷순번 대여소에는 출발 뒤
1 ~ 3시간에 닿고, 계획을 출발 한참 전에 미리 세웠다면 그 간격이 더 벌어진다(사용자 지적, 2026-11-14). 그래서 실제 경로
(작업 대상 선정 → 군집 → ILP → VRP, 운영 함수 그대로)를 세우고 대여소마다 **도착 시각**의 관측 재고와 대조한다. 앞
대여소에서 덜 실은 만큼 뒤 배송이 줄어드는 연쇄도 함께 센다.

무엇을 묻나
  Q1 뒷순번 불일치: 출발 뒤 도착까지 걸린 시간대별 불가 · 부족 · 넘침 비율, 못 채우는 지시량 비율, 연쇄 집행률
  Q2 다시 짜면 나아지나: 같은 회차를 네 안으로 집행해 견준다
       A 원안          : 미리 세운 계획 그대로. 기사는 도착해서 실을 수 있는 만큼만 싣고 내릴 수 있는 만큼만 내린다
       C 출발 때 재계획: 출발 시각 재고로 처음부터 다시 세운 계획(리드 0의 계획)
       D 트리거형 재계획: 출발 뒤 60 · 120분 시점에 남은 방문을 그 시각 재고로 판정해, 못 채우는 지시량이 10% 이상이면 그
                         자리 · 그 적재에서 군집의 남은 부분을 다시 푼다(대상 · 순서를 모두 다시 고른다)
       E 수거량만 다시  : 대여소와 순서는 그대로, 수거량만 출발 시각 재고로 다시 낸다(63장이 남긴 다음 수)
  Q3 끝까지 지키는 설정: 작업 대상 상한(TOP_STATION_LIMIT) × 회차 시간 예산(TIME_BUDGET_MINUTES) 격자에서 뒷순번 불일치와 일의 양

사전 등록 (EXPERIMENTS 65장. 결과 전 커밋. 바꾸지 않는다)
  자료     : 63장과 같다. `--stats current --until "2026-11-08 00:00"`, 하루 30틱 이상인 날, 참조 계획의 mu · sigma · 거치대.
             출발 D = 회차 시작 시각(05 · 10 · 15 · 20시 중 참조 계획이 있는 회차). D와 계획 시각 P 모두 대여소 절반 이상이 관측된 회차만
  리드     : P = D − L, L = 0 · 30 · 120 · 360 · 720 · 1440분. P의 관측 재고로 계획을 세운다(L = 0이 C안)
  계획     : 운영 함수 compute_rebal_qty → select_top_unbalanced_st → make_clustering + adjust_clustering → 군집별 ILP → greedy_route,
             씨앗 42. 수거 대수 학습(K)은 쓰지 않는다(실험은 현행 선정, 47 · 63장과 같다)
  도착     : 정류장의 도착 시각 = D + (VRP 누적 소요 − 그 정류장 작업 시간). 가장 가까운 10분 칸의 관측 재고(없으면 앞뒤 한 칸)
  판정     : 운영 판정 함수 webapp.orders.compare_stock()(계획 재고 = P의 관측, 지금 재고 = 도착 시각의 관측)
  연쇄 집행: 적재 상한 VEHICLE_CAPACITY. 수거 x = min(지시량, 도착 재고, 상한 − 적재), 배송 x = min(지시량, 적재, 거치대 × 1.5 − 도착 재고)
  채점     : 도착 뒤 3시간(18칸)의 관측. 칸의 80% 이상 관측된 정류장만. 유효 배송 = 그 창에서 재고가 한 번이라도 0이 된 대여소에
             실제로 내린 대수. 수거 무해 = 실제로 실은 x에 대해 창의 최저 − x ≥ 1(0 = 빔, 47장 정정식)
  판정     : 평일만.
             (1) 뒷순번 불일치가 실재 — L = 30분에서 경로의 마지막 3분의 1(도착 시각 기준) 정류장의 못 채우는 지시량이 10% 이상
             (2) D가 값이 있다(리드마다) — D의 유효 배송 대수가 A의 1.05배 이상, D의 수거 무해율이 A 이상, 회차 단위 부호 검정
                 (D > A, 유효 배송 대수, 같은 값 제외) 단측 p < 0.05. 통과하는 가장 짧은 리드를 적는다
             (3) 다시 세울 리드 — C 대 A를 (2)와 같은 기준으로. 통과하는 가장 짧은 리드를 적는다
             (4) Q3은 보고만 한다. 마지막 3분의 1의 못 채우는 지시량이 3 · 5 · 10% 이하인 칸과 그 칸의 일의 양을 현행(50 · 120분)과 견준다
             못 넘은 칸은 그대로 적고 문턱을 풀지 않는다. **어느 결과도 파이프라인을 바꾸지 않는다.** 바꿀 만한 후보는 사용자에게 보고한다
  찍기만   : 휴일, 도착 시간대별 표, 경로 길이별 표, 재고 변화가 큰 상위 10% 회차, 출발 직전 3시간 강수 1mm 이상 회차, 종이와 어긋난 정류장 수
  알려진 한계 — 계획은 집행되지 않았으므로 관측 재고는 '손대지 않은 세상'이다(47장). 앞 정류장의 집행이 뒤 정류장의 재고를 바꾸지
  않는다고 본다(군집이 서로 겹치지 않아 같은 대여소를 두 차가 돌지 않는다). 공사 트럭이 실제로 옮긴 몫은 걸러 내지 못한다.

실행:
    python experiments/structure/route_drift.py --sanity                    # 입력 점검 · 한 회차로 계획 하나
    python experiments/structure/route_drift.py --run main                  # Q1 · Q2 (현행 설정, 평일 · 휴일)
    python experiments/structure/route_drift.py --run sweep                 # Q3 (격자, 평일)
    python experiments/structure/route_drift.py --report                    # 표와 판정
    python experiments/structure/route_drift.py --run fine                  # 66장: 평일 60 · 90분 리드(A안만)
    python experiments/structure/route_drift.py --report-fine               # 66장 판정(65장 (3)과 같은 기준)
"""
import argparse
import contextlib
import io
import math
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import project_config  # noqa: E402,F401  — 콘솔 인코딩을 먼저 맞춘다(— · 이모지)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import db  # noqa: E402
import plan_validity as pv  # noqa: E402
import stockout_forecast as sf  # noqa: E402
from experiments._shared import default_run_label, load_baseline  # noqa: E402
from project_config import (DATA_ROOT, DURATIONS, REBAL_MIN_QTY, STOCK_TICK_MINUTES, TARGET_QTY_UPPER_RATIO,  # noqa: E402
                            TIME_BUDGET_MINUTES, TOP_STATION_LIMIT, VEHICLE_CAPACITY, duration_start_hour,
                            haversine_km, holiday_mask)
from webapp import orders  # noqa: E402

UNTIL = "2026-11-08 00:00"                 # 63장과 같은 자료 끝
LEADS = (0, 30, 120, 360, 720, 1440)       # 분. 0 = 출발 때 세운 계획(C안)
CHECKPOINTS = (60, 120)                    # D: 출발 뒤 분
THETA = 0.10                               # D 트리거: 남은 지시량 중 못 채우는 비율
WINDOW = 18                                # 도착 뒤 3시간(10분 칸 18개)
MIN_OBSERVED = 0.8
GAP_FLOOR = 0.10                           # 판정 (1)
GAIN = 1.05                                # 판정 (2) · (3)
BINS = (0, 30, 60, 90, 120, 180, math.inf)
GRID_LIMITS = (25, 35, 50)
GRID_BUDGETS = (60, 90, 120, 180)
SWEEP_LEADS = (30, 360)
SEED = pv.SEED
OUT_DIR = DATA_ROOT / "route_drift"
TICK_SEC = STOCK_TICK_MINUTES * 60
CURRENT = (TOP_STATION_LIMIT, int(TIME_BUDGET_MINUTES))
PARALLEL = 3


def quiet(func, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return func(*args, **kwargs)


# ---------------------------------------------------------------- 입력

def observed_grid(until: str):
    """관측 재고를 10분 격자로 편다(63장과 같은 식). 반환: (격자, 하루 30틱 이상인 날)."""
    with db.session() as conn:
        raw = sf.load_grid(conn)
    raw = raw[raw["ts"] < pd.Timestamp(until)]
    stock = raw.pivot_table(index="ts", columns="station_id", values="stock")
    grid = stock.reindex(pd.date_range(stock.index.min().floor("D"), stock.index.max().ceil("D"),
                                       freq=pv.TICK, inclusive="left"))
    return grid, sf.dense_days(raw)


def load_world(until: str, day_types, cache: str = None):
    """참조 계획(지금 순수요로 mu · sigma) · 관측 재고 격자 · 좌표 · 관측 날.

    `cache`를 주면 부모가 한 번 만들어 둔 격자를 읽는다. 자식마다 원천 재고(1,500만 행 남짓)를 다시 읽으면
    메모리가 모자라고 시간도 겹친다.
    """
    with db.session() as conn:
        refs = {dt: pv.load_refs(conn, dt, before=until) for dt in day_types}
        for dt in day_types:
            net = pv.load_net(dt)
            for d, (frame, lab) in list(refs[dt].items()):
                refs[dt][d] = (pv.current_stats(frame, net, d), f"{lab} → 지금 순수요")
        info = db.load_frame(conn, "station_info", run_label=default_run_label(conn))
    if cache and Path(cache).exists():
        grid, days = pd.read_pickle(cache)
    else:
        grid, days = observed_grid(until)
    coords = info.drop_duplicates("station_id").set_index("station_id")[["station_name", "lat", "lon"]]
    hol = dict(zip(days, holiday_mask(pd.Series(pd.to_datetime(days)))))
    return refs, grid, coords, days, hol


def rain_flags(day_types) -> dict:
    """(날짜, 회차) → 출발 직전 3시간 강수 ≥ 1mm(1.26.423과 같은 자). 날씨 자료가 없으면 빈 사전."""
    try:
        import weather_departure as wd
        raw = wd.load_raw_hourly()
    except Exception:                       # noqa: BLE001 — 찍기만 하는 갈래라 없으면 건너뛴다
        return {}
    if raw.empty:
        return {}
    out = {}
    for dur in DURATIONS:
        frame = wd.departure_frame(raw, dur, 3)
        for row in frame.itertuples():
            out[(row.date, dur)] = bool(row.rain >= 1.0)
    return out


# ---------------------------------------------------------------- 계획

def build_plan(bc, step1, solver, ref, stock: pd.Series, duration: str, coords: pd.DataFrame):
    """관측 재고 한 장으로 운영 함수를 그대로 돌려 (군집표, 정류장표)를 낸다. 정류장표는 군집 · 방문 순서 · 도착 초."""
    stats = ref[["mu", "sigma", "parking_lot"]].copy()
    stats["stock"] = stock.reindex(stats.index)
    stats = stats.dropna(subset=["stock", "mu", "sigma", "parking_lot"])
    rebal = quiet(bc.target_mod.compute_rebal_qty, stats.copy())
    frame = rebal.reset_index().rename(columns={"index": "station_id"})
    if "station_id" not in frame.columns:
        frame = frame.rename(columns={frame.columns[0]: "station_id"})
    info = coords.reindex(frame["station_id"]).reset_index()
    info["parking_lot"] = frame["parking_lot"].to_numpy()
    info["stock"] = frame["stock"].to_numpy()
    info = info.dropna(subset=["lat", "lon"])
    buffer = io.StringIO()
    frame[["station_id", "mu", "sigma", "parking_lot", "stock", "target_qty", "rebal_qty"]].to_csv(buffer, index=False)
    buffer.seek(0)
    candidates = quiet(step1.select_top_unbalanced_st, buffer, duration, info)
    candidates = candidates.dropna(subset=["lat", "lon"])
    if candidates.empty or (candidates["rebal_qty"] > 0).sum() == 0 or (candidates["rebal_qty"] < 0).sum() == 0:
        return None, None, rebal["rebal_qty"]
    clustered, routes = bc.plan_with_clusters(candidates, step1, solver, adjust=True, seed=SEED)
    return clustered, stops_of(routes), rebal["rebal_qty"]


def stops_of(routes: pd.DataFrame) -> pd.DataFrame:
    """VRP 행 → 정류장표(복귀 행 제외). 도착 초 = 누적 소요 − 그 정류장 작업 시간."""
    if routes is None or routes.empty:
        return pd.DataFrame(columns=["cluster", "order", "station_id", "action", "q", "arr_sec"])
    r = routes[routes["action"].isin(["pick", "drop"])].copy()
    r["order"] = r.groupby("cluster").cumcount()
    r["arr_sec"] = r["cum_sec"] - r["work_sec"]
    return r.rename(columns={"to_id": "station_id", "qty": "q"})[["cluster", "order", "station_id", "action", "q",
                                                                   "arr_sec", "to_lat", "to_lon"]]


# ---------------------------------------------------------------- 도착 · 판정 · 집행

class Observed:
    """관측 격자를 넘파이로 들고 도착 칸의 재고 · 뒤 창을 빨리 읽는다."""

    def __init__(self, grid: pd.DataFrame):
        self.values = grid.to_numpy(dtype=float)
        self.col = {sid: j for j, sid in enumerate(grid.columns)}
        self.ids = np.array(grid.columns)
        self.index = {ts: i for i, ts in enumerate(grid.index)}
        self.n = len(grid.index)

    def at(self, i: int, sid):
        j = self.col.get(sid)
        if j is None or not 0 <= i < self.n:
            return None
        for k in (i, i - 1, i + 1):
            if 0 <= k < self.n and np.isfinite(self.values[k, j]):
                return float(self.values[k, j])
        return None

    def row(self, i: int) -> pd.Series:
        v = self.values[i]
        mask = np.isfinite(v)
        return pd.Series(v[mask], index=self.ids[mask])

    def window(self, i: int, sid):
        j = self.col.get(sid)
        if j is None or i + 1 + WINDOW > self.n:
            return None
        w = self.values[i + 1:i + 1 + WINDOW, j]
        if np.isfinite(w).mean() < MIN_OBSERVED:
            return None
        return float(np.nanmin(w))


def judge(stops: pd.DataFrame, s_plan: pd.Series, live: dict, parking: pd.Series) -> pd.DataFrame:
    """운영 판정 함수로 정류장마다 판정한다. 같은 대여소를 두 번 들르면 방문 차례별로 나눠 부른다(compare_stock은 대여소당 한 재고)."""
    if stops.empty:
        return stops.assign(status=[], possible=[])
    out = []
    visit = stops.groupby(["cluster", "station_id"]).cumcount()
    for _, part in stops.groupby(visit, sort=True):
        planned = pd.DataFrame({
            "station_id": part["station_id"].to_numpy(), "station_name": "", "cluster": part["cluster"].to_numpy(),
            "seq": part["order"].to_numpy(), "action": part["action"].to_numpy(), "need": part["q"].astype(int).to_numpy(),
            "stock": s_plan.reindex(part["station_id"]).fillna(0).to_numpy(),
            "parking_lot": parking.reindex(part["station_id"]).fillna(0).to_numpy()})
        keys = list(zip(part["cluster"], part["order"]))
        stock_now = [live.get(k) if pd.notna(live.get(k, np.nan)) else None for k in keys]
        live_frame = pd.DataFrame({"station_id": [sid for sid, s in zip(part["station_id"], stock_now) if s is not None],
                                   "stock": [s for s in stock_now if s is not None]})
        judged = orders.compare_stock(planned, live_frame)
        # compare_stock은 (군집, 순서)로 다시 정렬해 돌려준다(종이와 같은 순서). 위치가 아니라 그 열쇠로 되붙인다
        where = {(int(c), int(o)): ix for ix, c, o in zip(part.index, part["cluster"], part["order"])}
        judged.index = [where[(int(c), int(o))] for c, o in zip(judged["cluster"], judged["seq"])]
        judged.loc[[ix for ix, s in zip(part.index, stock_now) if s is None], "status"] = "관측 없음"
        out.append(judged[["status", "possible"]])
    res = pd.concat(out).reindex(stops.index)
    return stops.assign(status=res["status"].to_numpy(), possible=res["possible"].to_numpy())


def execute(stops: pd.DataFrame, obs: Observed, i_dep: int, s_plan: pd.Series, parking: pd.Series,
            start_load: dict = None) -> pd.DataFrame:
    """정류장표를 도착 시각 순서대로 집행한다(연쇄). 반환 열: 도착 칸 · 도착 재고 · 판정 · 실제 대수 · 채점."""
    if stops.empty:
        return stops.assign(i_arr=[], s_arr=[], status=[], possible=[], x=[], useful=[], harmless=[])
    s = stops.copy()
    s["i_arr"] = i_dep + np.rint(s["arr_sec"].to_numpy() / TICK_SEC).astype(int)
    s["s_arr"] = [obs.at(i, sid) for i, sid in zip(s["i_arr"], s["station_id"])]
    live = {(c, o): v for c, o, v in zip(s["cluster"], s["order"], s["s_arr"]) if pd.notna(v)}
    s = judge(s, s_plan, live, parking)
    xs, useful, harmless = [], [], []
    for _cluster, g in s.groupby("cluster", sort=False):
        load = (start_load or {}).get(_cluster, 0)
        for r in g.sort_values("order").itertuples():
            q = int(r.q)
            if r.s_arr is None or (isinstance(r.s_arr, float) and math.isnan(r.s_arr)):
                xs.append((r.Index, np.nan)); useful.append((r.Index, np.nan)); harmless.append((r.Index, np.nan))
                continue
            now = int(r.s_arr)
            if r.action == "pick":
                x = max(0, min(q, now, VEHICLE_CAPACITY - load))
                load += x
            else:
                room = int(parking.get(r.station_id, 0) * TARGET_QTY_UPPER_RATIO - now)
                x = max(0, min(q, load, room))
                load -= x
            xs.append((r.Index, x))
            lo = obs.window(int(r.i_arr), r.station_id)
            if lo is None:
                useful.append((r.Index, np.nan)); harmless.append((r.Index, np.nan))
            elif r.action == "drop":
                useful.append((r.Index, float(x) if lo == 0 else 0.0)); harmless.append((r.Index, np.nan))
            else:
                useful.append((r.Index, np.nan)); harmless.append((r.Index, (1.0 if lo - x >= 1 else 0.0) if x > 0 else np.nan))
    s["x"] = pd.Series(dict(xs))
    s["useful"] = pd.Series(dict(useful))
    s["harmless"] = pd.Series(dict(harmless))
    return s


# ---------------------------------------------------------------- 안 E · D

def pick_only(stops: pd.DataFrame, rebal_dep: pd.Series) -> pd.DataFrame:
    """E — 대여소와 순서는 그대로, 수거량만 출발 시각 재고로 다시 낸다(늘리지는 않는다)."""
    s = stops.copy()
    pick = s["action"] == "pick"
    fresh = (-rebal_dep.reindex(s.loc[pick, "station_id"]).fillna(0)).clip(lower=0).to_numpy()
    fresh = np.where(fresh > REBAL_MIN_QTY, fresh, 0)
    s.loc[pick, "q"] = np.minimum(s.loc[pick, "q"].to_numpy(), fresh).astype(int)
    return s[s["q"] > 0]


def cluster_pools(clustered: pd.DataFrame, ref: pd.DataFrame, coords: pd.DataFrame, stops: pd.DataFrame) -> dict:
    """군집 → 그 군집 중심에 가장 가까운 대여소들(참조 계획에 있는 대여소 전부를 가장 가까운 중심에 붙인다).

    다른 차의 원래 방문지는 뺀다. 다시 푼 경로가 남의 정류장을 가져가면 한 대여소를 두 차가 돌게 된다.
    """
    cen = clustered.groupby("cluster")[["lat", "lon"]].mean()
    pts = coords.reindex(ref.index).dropna(subset=["lat", "lon"])
    d = np.stack([project_config.haversine_km(pts["lat"].to_numpy(), pts["lon"].to_numpy(), la, lo)
                  for la, lo in zip(cen["lat"], cen["lon"])], axis=1)
    owner = cen.index.to_numpy()[np.argmin(d, axis=1)]
    mine = stops.groupby("cluster")["station_id"].agg(set).to_dict()
    pools = {}
    for c in cen.index:
        others = set().union(*[v for k, v in mine.items() if k != c]) if len(mine) > 1 else set()
        pools[c] = [sid for sid in pts.index[owner == c] if sid not in others]
    return pools


def replan_tail(bc, solver, ref, coords: pd.DataFrame, stock_now: pd.Series, pool, visited: set, pos: tuple, load: int,
                n_pick: int, n_drop: int, cluster) -> pd.DataFrame:
    """D — 지금 자리(pos = (id, lat, lon))와 적재에서 군집의 남은 부분을 다시 푼다. 반환은 정류장표(도착 초는 재계획 시점부터)."""
    ids = [sid for sid in pool if sid not in visited]
    stats = ref.reindex(ids)[["mu", "sigma", "parking_lot"]].copy()
    stats["stock"] = stock_now.reindex(stats.index)
    stats = stats.dropna(subset=["stock", "mu", "sigma", "parking_lot"])
    if stats.empty:
        return None
    r = quiet(bc.target_mod.compute_rebal_qty, stats.copy())["rebal_qty"]
    picks = r[r < -REBAL_MIN_QTY].sort_values().head(max(n_pick, 0))
    drops = r[r > REBAL_MIN_QTY].sort_values(ascending=False).head(max(n_drop, 0))
    if drops.empty:
        return None
    rows = [{"station_id": sid, "pick_qty": int(-q), "drop_qty": 0, "lat": coords.loc[sid, "lat"], "lon": coords.loc[sid, "lon"]}
            for sid, q in picks.items()]
    rows += [{"station_id": sid, "pick_qty": 0, "drop_qty": int(q), "lat": coords.loc[sid, "lat"], "lon": coords.loc[sid, "lon"]}
             for sid, q in drops.items()]
    truck = "__truck__"
    if load > 0:
        rows.append({"station_id": truck, "pick_qty": int(load), "drop_qty": 0, "lat": pos[1], "lon": pos[2]})
    frame = pd.DataFrame(rows)
    if frame["pick_qty"].sum() == 0:
        return None
    moves = pd.DataFrame(quiet(bc.ilp_mod.solve_cluster_moves, frame, solver))
    if moves.empty:
        return None
    where = frame.set_index("station_id")[["lat", "lon"]]
    nodes = {}
    for sid, qty in moves.groupby("pick_station_id")["qty"].sum().items():
        nodes[(sid, "pick")] = {"qty": int(qty), "lat": where.loc[sid, "lat"], "lon": where.loc[sid, "lon"]}
    for sid, qty in moves.groupby("drop_station_id")["qty"].sum().items():
        nodes[(sid, "drop")] = {"qty": int(qty), "lat": where.loc[sid, "lat"], "lon": where.loc[sid, "lon"]}
    vrp = bc.vrp_mod
    saved = (vrp.DEPOT_ID, vrp.DEPOT_LAT, vrp.DEPOT_LON)
    try:                                    # 차고지 대신 지금 자리에서 출발시킨다(실험 안에서만 바꾸고 되돌린다)
        vrp.DEPOT_ID, vrp.DEPOT_LAT, vrp.DEPOT_LON = pos
        rows_out = quiet(vrp.greedy_route, nodes, cluster)
    finally:
        vrp.DEPOT_ID, vrp.DEPOT_LAT, vrp.DEPOT_LON = saved
    out = pd.DataFrame(rows_out)
    if out.empty:
        return None
    offset = 0.0
    first = out[(out["to_id"] == truck)]
    if not first.empty:                     # 적재를 '실은' 가상 정류장: 이동 고정비와 작업 시간을 시계에서 뺀다
        offset = float(first.iloc[0]["travel_sec"] + first.iloc[0]["work_sec"])
    out = out[out["action"].isin(["pick", "drop"]) & (out["to_id"] != truck)].copy()
    out["arr_sec"] = out["cum_sec"] - out["work_sec"] - offset
    out["order"] = range(len(out))
    return out.rename(columns={"to_id": "station_id", "qty": "q"})[["cluster", "order", "station_id", "action", "q",
                                                                    "arr_sec", "to_lat", "to_lon"]]


def trigger(bc, solver, stops: pd.DataFrame, clustered, ref, coords, obs: Observed, i_dep: int, s_plan, parking) -> tuple:
    """D — 체크포인트마다 남은 방문을 그 시각 재고로 판정해 문턱을 넘으면 군집의 남은 부분을 다시 푼다.

    반환: (집행 결과표, 다시 푼 군집 수, 종이와 어긋난 정류장 수)."""
    pools = cluster_pools(clustered, ref, coords, stops)
    done, replanned, changed = [], 0, 0
    for cluster, g in stops.groupby("cluster", sort=False):
        plan = g.sort_values("order").copy()
        load, visited, t0 = 0, set(), 0.0
        pos = (project_config.DEPOT_ID, project_config.DEPOT_LAT, project_config.DEPOT_LON)
        for cp in CHECKPOINTS:
            cut = cp * 60
            head = plan[plan["arr_sec"] <= cut]
            tail = plan[plan["arr_sec"] > cut]
            if tail.empty:
                break
            ran = execute(head, obs, i_dep, s_plan, parking, start_load={cluster: load})
            if not ran.empty:
                done.append(ran)
                for rr in ran.itertuples():
                    if not (isinstance(rr.x, float) and math.isnan(rr.x)):
                        load += int(rr.x) if rr.action == "pick" else -int(rr.x)
                visited |= set(ran["station_id"])
                last = ran.sort_values("order").iloc[-1]
                pos = (last["station_id"], float(last["to_lat"]), float(last["to_lon"]))
                t0 = float(last["arr_sec"])
            plan = tail
            i_cp = i_dep + int(round(cut / TICK_SEC))
            now_row = obs.row(i_cp)
            live = {(c, o): (float(now_row[sid]) if sid in now_row.index else None)
                    for c, o, sid in zip(plan["cluster"], plan["order"], plan["station_id"])}
            live = {k: v for k, v in live.items() if v is not None}
            judged = judge(plan, s_plan, live, parking)
            need = judged["q"].sum()
            short = (judged["q"] - judged["possible"].fillna(judged["q"])).clip(lower=0).sum()
            if need <= 0 or short / need < THETA:
                continue
            new = replan_tail(bc, solver, ref, coords, now_row, pools.get(cluster, []), visited, pos, max(load, 0),
                              int((plan["action"] == "pick").sum()), int((plan["action"] == "drop").sum()), cluster)
            if new is None or new.empty:
                continue
            replanned += 1
            old_keys = set(zip(plan["station_id"], plan["action"]))
            new_keys = set(zip(new["station_id"], new["action"]))
            changed += len(old_keys ^ new_keys)
            new["arr_sec"] = new["arr_sec"] + max(t0, cut)
            new["order"] = new["order"] + (int(head["order"].max()) + 1 if not head.empty else 0)
            plan = new
        ran = execute(plan, obs, i_dep, s_plan, parking, start_load={cluster: load})
        if not ran.empty:
            done.append(ran)
    res = pd.concat(done) if done else stops.iloc[0:0]
    return res, replanned, changed


# ---------------------------------------------------------------- 자식: 설정 하나

def child(args) -> int:
    limit, budget = TOP_STATION_LIMIT, int(TIME_BUDGET_MINUTES)
    day_types = args.day_types.split(",")
    leads = [int(x) for x in args.leads.split(",")]
    arms = set(args.arms.split(","))
    bc = load_baseline()
    step1 = bc.load_step1()
    solver = bc.ilp_mod.build_solver()
    refs, grid, coords, days, hol = load_world(args.until, day_types, cache=args.grid)
    obs = Observed(grid)
    rains = rain_flags(day_types)
    rows, inst = [], []
    current_dt = None
    started = time.time()
    total = 0
    for day in days:
        dt = "holiday" if hol[day] else "weekday"
        if dt not in day_types:
            continue
        day_str = str(pd.Timestamp(day).date())
        if dt != current_dt:
            bc.align_day_type(dt, bc.ilp_mod, bc.vrp_mod, bc.kpi_mod, step1)
            current_dt = dt
        for dur in DURATIONS:
            if dur not in refs[dt]:
                continue
            t = pd.Timestamp(day) + pd.Timedelta(hours=duration_start_hour(dur))
            if t not in obs.index:
                continue
            i_dep = obs.index[t]
            s_dep = obs.row(i_dep)
            if len(s_dep) < 0.5 * grid.shape[1]:
                continue
            ref = refs[dt][dur][0]
            parking = ref["parking_lot"]
            rebal_dep = None
            for lead in leads:
                i_p = i_dep - lead // STOCK_TICK_MINUTES
                if i_p < 0:
                    continue
                s_p = obs.row(i_p)
                if len(s_p) < 0.5 * grid.shape[1]:
                    continue
                common = s_p.index.intersection(s_dep.index)
                clustered, stops, rebal = build_plan(bc, step1, solver, ref, s_p, dur, coords)
                total += 1
                if stops is None or stops.empty:
                    continue
                if lead == 0:
                    rebal_dep = rebal
                key = dict(limit=limit, budget=budget, dt=dt, day=day_str, dur=dur, lead=lead,
                           vol=float((s_dep[common] - s_p[common]).abs().mean()), rain=rains.get((day_str, dur)))
                plan_n = len(stops)
                route_last = stops.groupby("cluster")["arr_sec"].transform("max")
                base = stops.assign(route_last=route_last)
                if "A" in arms or lead == 0:
                    ran = execute(base, obs, i_dep, s_p, parking)
                    rows.append(ran.assign(arm="A" if lead > 0 else "C", **key))
                if lead > 0 and "E" in arms:
                    if rebal_dep is None:
                        rebal_dep = build_plan(bc, step1, solver, ref, s_dep, dur, coords)[2]
                    e = pick_only(base, rebal_dep)
                    rows.append(execute(e, obs, i_dep, s_p, parking).assign(arm="E", **key))
                if lead > 0 and "D" in arms:
                    d, n_re, n_ch = trigger(bc, solver, base, clustered, ref, coords, obs, i_dep, s_p, parking)
                    rows.append(d.assign(arm="D", **key))
                    key = dict(key, d_replanned=n_re, d_changed=n_ch)
                inst.append(dict(key, stops=plan_n, clusters=stops["cluster"].nunique(),
                                 route_max=float(stops.groupby("cluster")["arr_sec"].max().max() / 60)))
        el = time.time() - started
        print(f"  [{limit} · {budget}분] {day} 까지 계획 {total}개 · {el / 60:.0f}분", flush=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    keep = ["limit", "budget", "dt", "day", "dur", "lead", "arm", "cluster", "order", "station_id", "action", "q",
            "arr_sec", "route_last", "i_arr", "s_arr", "status", "possible", "x", "useful", "harmless", "vol", "rain"]
    frame[[c for c in keep if c in frame.columns]].to_csv(out, index=False, encoding="utf-8")
    pd.DataFrame(inst).to_csv(out.with_name(out.stem + "_회차.csv"), index=False, encoding="utf-8")
    print(f"  [{limit} · {budget}분] 끝: 정류장 {len(frame):,}행 → {out}", flush=True)
    return 0


# ---------------------------------------------------------------- 부모: 자식 띄우기

def spawn(cells: list, until: str) -> int:
    """설정마다 자식 프로세스를 띄운다. 상한 · 예산은 project_config가 import 때 환경변수에서 읽어 한 프로세스 안에서 못 바꾼다."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cache = OUT_DIR / ("grid_" + until.replace(":", "").replace(" ", "_") + ".pkl")
    if not cache.exists():
        grid, days = observed_grid(until)
        pd.to_pickle((grid, days), cache)
        del grid
        print(f"관측 격자를 만들어 두었다: {cache}", flush=True)
    queue = list(cells)
    running = []
    while queue or running:
        while queue and len(running) < PARALLEL:
            limit, budget, leads, day_types, arms, name = queue.pop(0)
            env = dict(os.environ, PBR_TOP_STATION_LIMIT=str(limit), PBR_TIME_BUDGET_MINUTES=str(budget),
                       PYTHONIOENCODING="utf-8")
            log = open(OUT_DIR / f"{name}.log", "w", encoding="utf-8")
            cmd = [sys.executable, str(Path(__file__).resolve()), "--child", "--until", until, "--grid", str(cache),
                   "--leads", ",".join(map(str, leads)), "--day-types", ",".join(day_types), "--arms", ",".join(arms),
                   "--out", str(OUT_DIR / f"{name}.csv")]
            running.append((subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT), log, name))
            print(f"시작: {name}", flush=True)
        time.sleep(5)
        for p, log, name in list(running):
            if p.poll() is not None:
                log.close()
                running.remove((p, log, name))
                print(f"끝: {name} (종료 코드 {p.returncode})", flush=True)
    return 0


def main_cells():
    return [(CURRENT[0], CURRENT[1], LEADS, [dt], ["A", "D", "E"], f"main_{dt}") for dt in ("weekday", "holiday")]


FINE_LEADS = (60, 90)                      # 66장: 30분과 2시간 사이


def fine_cells():
    """66장: 평일 A안만, 리드마다 자식 하나. C안과 30 · 120분 A안은 65장 결과(main_weekday.csv)를 그대로 쓴다."""
    return [(CURRENT[0], CURRENT[1], [lead], ["weekday"], ["A"], f"fine_weekday_{lead}") for lead in FINE_LEADS]


def sweep_cells():
    cells = []
    for limit in GRID_LIMITS:
        for budget in GRID_BUDGETS:
            if (limit, budget) == CURRENT:
                continue                    # 현행 칸은 main의 평일 결과를 그대로 쓴다
            cells.append((limit, budget, SWEEP_LEADS, ["weekday"], ["A"], f"sweep_{limit}_{budget}"))
    return cells


# ---------------------------------------------------------------- 보고

def rate(a, b):
    return float(a / b) if b else float("nan")


def gap_share(g: pd.DataFrame) -> float:
    ok = g[g["status"] != "관측 없음"]
    need = ok["q"].sum()
    short = (ok["q"] - ok["possible"].fillna(ok["q"])).clip(lower=0).sum()
    return rate(short, need)


def exec_rate(g: pd.DataFrame) -> float:
    ok = g[g["x"].notna()]
    return rate(ok["x"].sum(), ok["q"].sum())


def third(g: pd.DataFrame) -> pd.Series:
    return np.where(g["route_last"] > 0, g["arr_sec"] / g["route_last"], 0.0)


def q1_table(sub: pd.DataFrame, label: str) -> None:
    print(f"\n### Q1 {label}: 도착까지 걸린 시간대별 (A안, 미리 세운 계획 그대로)")
    print("  리드 | " + " | ".join(f"{int(BINS[k])}~{'' if math.isinf(BINS[k + 1]) else int(BINS[k + 1])}분" for k in range(len(BINS) - 1))
          + " | 마지막 3분의 1 | 전체 집행률")
    for lead in sorted(sub["lead"].unique()):
        g = sub[sub["lead"] == lead]
        mins = g["arr_sec"] / 60
        cells = []
        for k in range(len(BINS) - 1):
            b = g[(mins >= BINS[k]) & (mins < BINS[k + 1])]
            cells.append(f"{gap_share(b):.1%} ({len(b):,})" if len(b) else "-")
        last = g[third(g) >= 2 / 3]
        print(f"  {lead}분 | " + " | ".join(cells) + f" | {gap_share(last):.1%} | {exec_rate(g):.1%}")


def arm_scores(g: pd.DataFrame) -> tuple:
    useful = g["useful"].sum()
    h = g["harmless"].dropna()
    return float(useful), rate(h.sum(), len(h)), float(g["x"].sum())


def q2_table(frame: pd.DataFrame, dt: str) -> dict:
    print(f"\n### Q2 {pv.DAY_TYPES[dt]}: 안별 유효 배송 대수 · 수거 무해율 · 실제 옮긴 대수")
    res = {}
    base = frame[(frame["dt"] == dt)]
    fresh = base[(base["arm"] == "C")]
    for lead in sorted(base["lead"].unique()):
        if lead == 0:
            continue
        g = base[base["lead"] == lead]
        keys = g[g["arm"] == "A"][["day", "dur"]].drop_duplicates()
        c = fresh.merge(keys, on=["day", "dur"])
        row = {}
        for arm, part in (("A", g[g["arm"] == "A"]), ("C", c), ("D", g[g["arm"] == "D"]), ("E", g[g["arm"] == "E"])):
            if part.empty:
                continue
            row[arm] = arm_scores(part)
            u, h, x = row[arm]
            print(f"  리드 {lead}분 {arm}: 유효 배송 {u:,.0f}대 · 수거 무해 {h:.1%} · 옮긴 대수 {x:,.0f}대")
        for arm in ("C", "D"):
            part = c if arm == "C" else g[g["arm"] == "D"]
            if part.empty or "A" not in row:
                continue
            a_by = g[g["arm"] == "A"].groupby(["day", "dur"])["useful"].sum()
            o_by = part.groupby(["day", "dur"])["useful"].sum()
            both = pd.concat([a_by, o_by], axis=1, keys=["A", arm]).dropna()
            wins, losses = int((both[arm] > both["A"]).sum()), int((both[arm] < both["A"]).sum())
            p = pv.sign_test(wins, losses)
            ua, ha, _ = row["A"]
            uo, ho, _ = row[arm]
            ok = (uo >= GAIN * ua) and (ho >= ha) and (p < pv.SIGN_P)
            print(f"      {arm} 대 A: 유효 배송 {rate(uo, ua):.2f}배 · 수거 무해 {ho:.1%} 대 {ha:.1%} · 부호 {wins}승 {losses}패 p = {p:.4f}"
                  f" → {'✅' if ok else '❌'}")
            res[(arm, lead)] = ok
    return res


def q3_table(frames: list) -> None:
    print("\n### Q3 평일: 작업 대상 상한 × 회차 시간 예산 (A안)")
    print("  상한 · 예산 | 리드 | 마지막 3분의 1 못 채움 | 전체 집행률 | 옮긴 대수 | 유효 배송 | 수거 무해")
    for f in frames:
        for (limit, budget, lead), g in f.groupby(["limit", "budget", "lead"]):
            if lead not in SWEEP_LEADS:
                continue
            last = g[third(g) >= 2 / 3]
            u, h, x = arm_scores(g)
            print(f"  {limit} · {budget}분 | {lead}분 | {gap_share(last):.1%} | {exec_rate(g):.1%} | {x:,.0f} | {u:,.0f} | {h:.1%}")


def extra_tables(frame: pd.DataFrame, inst: pd.DataFrame) -> None:
    """찍기만(65장 등록): 경로 길이별 · 재고 변화가 큰 상위 10% 회차 · 출발 직전 비 · D가 다시 푼 군집 수. 판정에 쓰지 않는다."""
    wk = frame[(frame["dt"] == "weekday")]
    print("\n### 찍기만 평일: 경로(군집) 길이별 마지막 3분의 1의 못 채우는 지시량 (A안)")
    edges = (0, 60, 90, 120, 180, math.inf)
    for lead in (30, 360):
        g = wk[(wk["arm"] == "A") & (wk["lead"] == lead)]
        if g.empty:
            continue
        cells = []
        for k in range(len(edges) - 1):
            b = g[(g["route_last"] / 60 >= edges[k]) & (g["route_last"] / 60 < edges[k + 1])]
            last = b[third(b) >= 2 / 3]
            name = f"{int(edges[k])}~{'' if math.isinf(edges[k + 1]) else int(edges[k + 1])}분"
            cells.append(f"{name} {gap_share(last):.1%}" if len(last) else f"{name} -")
        print(f"  리드 {lead}분: " + " · ".join(cells))
    keyed = ["day", "dur", "lead"]
    for label, pick in (("재고 변화가 큰 상위 10% 회차", "vol"), ("출발 직전 3시간 강수 1mm 이상 회차", "rain")):
        print(f"\n### 찍기만 평일: {label}")
        if pick == "rain" and wk["rain"].isna().all():
            # 날씨 자료가 기간을 덮지 않으면 모든 회차가 빈 값이다. "해당 회차 없음"으로 찍으면 비가 오지 않았다는 뜻으로
            # 읽힌다(65장 첫 보고, 2026-11-17: 날씨 자료가 2026-07-31에서 끝났다).
            print("  날씨 자료가 이 기간을 덮지 않아 가르지 못했다 (weather_departure.load_raw_hourly()의 마지막 날을 확인)")
            continue
        for lead in sorted(x for x in wk["lead"].unique() if x > 0):
            g = wk[wk["lead"] == lead]
            ins = g[g["arm"] == "A"][keyed + ["vol", "rain"]].drop_duplicates(keyed)
            if pick == "vol":
                chosen = ins[ins["vol"] >= ins["vol"].quantile(0.9)][keyed]
            else:
                chosen = ins[ins["rain"].fillna(False).astype(bool)][keyed]
            if chosen.empty:
                print(f"  리드 {lead}분: 해당 회차 없음")
                continue
            sub = g.merge(chosen, on=keyed)
            a_part = sub[sub["arm"] == "A"]
            last = a_part[third(a_part) >= 2 / 3]
            ua = arm_scores(a_part)[0]
            line = f"  리드 {lead}분 ({len(chosen)}회차): A 마지막 3분의 1 못 채움 {gap_share(last):.1%} · 집행률 {exec_rate(a_part):.1%}"
            for arm in ("D", "E"):
                part = sub[sub["arm"] == arm]
                if not part.empty:
                    line += f" · {arm} 유효 배송 {rate(arm_scores(part)[0], ua):.2f}배(A 대비)"
            print(line)
    if not inst.empty and "d_replanned" in inst.columns:
        print("\n### 찍기만: D가 다시 푼 군집 수와 종이와 어긋난 정류장 (회차 평균)")
        for (dt, lead), g in inst[inst["lead"] > 0].groupby(["dt", "lead"]):
            print(f"  {pv.DAY_TYPES[dt]} 리드 {lead}분: 다시 푼 군집 {g['d_replanned'].mean():.1f}개 / 군집 {g['clusters'].mean():.1f}개 · "
                  f"바뀐 정류장 {g['d_changed'].mean():.1f}곳 / 정류장 {g['stops'].mean():.1f}곳")


def age_tables(frame: pd.DataFrame) -> None:
    """사후 표(65장 결과를 정리하며 더함. 등록에 없고 판정에 쓰지 않는다): 계획 나이별 못 채움, 경로 앞 · 뒤의 수거 · 배송.

    사용자 물음 "회차를 몇 시간 안에 마쳐야 몇 % 안인가"에 바로 답하려고 리드와 도착 분을 더한 '계획 나이'로 다시 묶는다.
    출발 전에 흐른 시간(리드)과 출발 뒤에 흐른 시간(도착 분)은 길이가 같아도 무게가 달라 리드 0 · 30분과 120분을 나눠 적는다.
    앞 · 뒤 표는 그 까닭을 보인다. 경로는 빈 차로 떠나 수거가 앞에, 배송이 뒤에 몰리고, 배송은 거치대 × 1.5의 여유가 있다.
    """
    edges = (0, 30, 60, 90, 120, 150, 180, 240, 300)
    for dt in ("weekday", "holiday"):
        g = frame[(frame["dt"] == dt) & frame["arm"].isin(["A", "C"])]
        if g.empty:
            continue
        print(f"\n### 사후 {pv.DAY_TYPES[dt]}: 계획 나이(리드 + 도착 분)별 못 채우는 지시량 (등록에 없음, 판정에 쓰지 않음)")
        age = g["lead"] + g["arr_sec"] / 60
        for name, leads in (("리드 0 · 30분", (0, 30)), ("리드 120분", (120,))):
            cells = []
            for k in range(len(edges) - 1):
                b = g[g["lead"].isin(leads) & (age >= edges[k]) & (age < edges[k + 1])]
                if len(b):
                    cells.append(f"{edges[k]}~{edges[k + 1]}분 {gap_share(b):.1%} ({len(b):,})")
            print(f"  {name}: " + " · ".join(cells))
        print("  경로 앞 3분의 1 · 뒤 3분의 1: 수거 비중 · 수거의 못 채움 · 배송의 못 채움")
        for lead in sorted(g["lead"].unique()):
            part = g[g["lead"] == lead]
            pos = third(part)
            line = []
            for name, sel in (("앞", pos < 1 / 3), ("뒤", pos >= 2 / 3)):
                s = part[sel]
                picks, drops = s[s["action"] == "pick"], s[s["action"] == "drop"]
                line.append(f"{name} 수거 {rate(len(picks), len(s)):.0%} · {gap_share(picks):.1%} · {gap_share(drops):.1%}")
            print(f"  리드 {lead}분: " + " | ".join(line))


STOCK_DELAYS = (30, 60, 90, 120, 180, 240, 360)   # 분. 아래 사후 표(2026-11-18)


def stock_change_tables(frame: pd.DataFrame) -> None:
    """사후 표(2026-11-18 사용자 물음 *"계획 생성 뒤 2시간이 지나면 계획 당시 재고와 몇 % 다른가"*. 등록에 없고 판정에 쓰지 않는다).

    (가) 전체 대여소: 관측이 촘촘한 날 05 ~ 21시 정시마다, Δ 뒤 재고가 바뀐 대여소 비율과 평균 변화.
    (나) 계획 대상: C안(출발 시각에 세운 계획)의 정류장을 **같은 계획 그대로 Δ 늦게** 집행했다고 보고, 운영 판정 함수
         (`judge` → `compare_stock`)로 못 채우는 지시량을 센다. 경로 이동 시간은 뺀다(모든 정류장을 Δ 뒤 재고로 본다).
         거치대는 자료를 다시 읽지 않으려고 참조 계획 대신 정본 실행의 station_info를 쓴다.
    65장의 판정(계획의 값: 유효 배송)과 다른 자다. 이 표는 *재고가 얼마나 달라지나* 만 말한다.
    """
    cache = OUT_DIR / ("grid_" + UNTIL.replace(":", "").replace(" ", "_") + ".pkl")
    if not cache.exists():
        print(f"\n### 사후: 재고 변화 표는 관측 격자({cache.name})가 없어 건너뛴다 (먼저 --run main)")
        return
    grid, days = pd.read_pickle(cache)
    obs = Observed(grid)
    with db.session() as conn:
        info = db.load_frame(conn, "station_info", run_label=default_run_label(conn))
    parking = info.drop_duplicates("station_id").set_index("station_id")["parking_lot"].astype(float)
    dense = {pd.Timestamp(d).date() for d in days}
    starts = [i for i, ts in enumerate(grid.index) if ts.date() in dense and ts.minute == 0 and 5 <= ts.hour <= 21]
    print("\n### 사후: 계획을 세운 뒤 시간이 지나면 재고가 얼마나 달라지나 (2026-11-18, 등록에 없음, 판정에 쓰지 않음)")
    print(f"  (가) 전체 대여소 · 관측 날 05 ~ 21시 정시 {len(starts):,}곳에서")
    for delay in STOCK_DELAYS:
        k = delay // STOCK_TICK_MINUTES
        rows = [i for i in starts if i + k < obs.n]
        a, b = obs.values[rows], obs.values[[i + k for i in rows]]
        seen = np.isfinite(a) & np.isfinite(b)
        diff = np.abs(b - a)[seen]
        print(f"    {delay}분 뒤: 재고가 바뀐 대여소 {np.mean(diff >= 1):.1%} · 3대 이상 {np.mean(diff >= 3):.1%} · 평균 변화 {diff.mean():.2f}대")
    fresh = frame[frame["arm"] == "C"]
    for dt in ("weekday", "holiday"):
        part = fresh[fresh["dt"] == dt]
        if part.empty:
            continue
        judged = {delay: [] for delay in STOCK_DELAYS}
        moved = {delay: [] for delay in STOCK_DELAYS}
        for (day, dur), stops in part.groupby(["day", "dur"]):
            i_dep = obs.index.get(pd.Timestamp(day) + pd.Timedelta(hours=duration_start_hour(dur)))
            if i_dep is None:
                continue
            s_plan = obs.row(i_dep)
            for delay in STOCK_DELAYS:
                k = delay // STOCK_TICK_MINUTES
                later = {(c, o): obs.at(i_dep + k, sid)
                         for c, o, sid in zip(stops["cluster"], stops["order"], stops["station_id"])}
                judged[delay].append(judge(stops[["cluster", "order", "station_id", "action", "q"]], s_plan, later, parking))
                before = s_plan.reindex(stops["station_id"]).to_numpy(dtype=float)
                after = np.array([np.nan if v is None else v for v in later.values()], dtype=float)
                moved[delay].append(np.abs(after - before))
        print(f"  (나) {pv.DAY_TYPES[dt]} C안 정류장 {len(part):,}곳을 같은 계획 그대로 늦게 집행하면")
        for delay in STOCK_DELAYS:
            g = pd.concat(judged[delay], ignore_index=True)
            seen = g[g["status"] != "관측 없음"]
            change = np.concatenate(moved[delay])
            change = change[np.isfinite(change)]
            picks, drops = seen[seen["action"] == "pick"], seen[seen["action"] == "drop"]
            print(f"    {delay}분 늦게: 재고가 바뀐 곳 {np.mean(change >= 1):.0%} · 평균 변화 {change.mean():.2f}대 · "
                  f"못 채우는 지시량 {gap_share(g):.1%}(수거 {gap_share(picks):.1%} · 배송 {gap_share(drops):.1%}) · "
                  f"걸리는 줄 {rate((seen['status'] != '가능').sum(), len(seen)):.1%}")


def fired_tables(frame: pd.DataFrame, inst: pd.DataFrame) -> None:
    """사후 표(2026-11-18 사용자 물음 *"작업 직전에 세운 계획이 작업 중 대조에서 가능한 작업량이 ~% 이하이면 남은 계획을
    고치면 어떤가"*. 등록에 없고 판정에 쓰지 않는다). D안이 실제로 다시 푼 회차와, 다시 풀어 지시가 바뀐 군집만 떼어 A안과
    견준다. 회차 평균(리드 30분 1.00배)이 다시 풀지 않은 회차 · 군집으로 묽어진 값인지 보려는 것이다.
    """
    if inst.empty or "d_replanned" not in inst.columns:
        return
    key = ["day", "dur", "lead"]
    print("\n### 사후: D안이 실제로 다시 푼 회차 · 군집만 (2026-11-18, 등록에 없음, 판정에 쓰지 않음)")
    for (dt, lead), fired in inst[(inst["lead"] > 0) & (inst["d_replanned"] > 0)].groupby(["dt", "lead"]):
        total = int(((inst["dt"] == dt) & (inst["lead"] == lead)).sum())
        g = frame[(frame["dt"] == dt) & (frame["lead"] == lead) & frame["arm"].isin(["A", "D"])].merge(fired[key], on=key)
        by = g.groupby(key + ["arm"])["useful"].sum().unstack("arm")
        wins, losses = int((by["D"] > by["A"]).sum()), int((by["D"] < by["A"]).sum())
        changed = []
        for _, part in g.groupby(key):
            a, d = part[part["arm"] == "A"], part[part["arm"] == "D"]
            for c in set(a["cluster"]) | set(d["cluster"]):
                ca, cd = a[a["cluster"] == c], d[d["cluster"] == c]
                if sorted(zip(ca["station_id"], ca["q"])) != sorted(zip(cd["station_id"], cd["q"])):
                    changed.append((float(ca["useful"].sum()), float(cd["useful"].sum())))
        ua, ud = sum(x for x, _ in changed), sum(y for _, y in changed)
        cw, cl = sum(1 for x, y in changed if y > x), sum(1 for x, y in changed if y < x)
        print(f"  {pv.DAY_TYPES[dt]} 리드 {lead}분: 다시 푼 회차 {len(fired)} / {total} · 그 회차의 유효 배송 D/A "
              f"{rate(by['D'].sum(), by['A'].sum()):.3f} · 회차 {wins}승 {losses}패 p = {pv.sign_test(wins, losses):.3f}"
              f" | 지시가 바뀐 군집 {len(changed)}개: 유효 배송 {ua:,.0f} → {ud:,.0f}대 · {cw}승 {cl}패 p = {pv.sign_test(cw, cl):.3f}")


def frontier(frames: list) -> None:
    """Q3 요약(65장 판정 (4), 보고만): 마지막 3분의 1 못 채움이 문턱 이하인 칸과 그 칸의 일의 양을 현행과 견준다."""
    rows = []
    for f in frames:
        for (limit, budget, lead), g in f.groupby(["limit", "budget", "lead"]):
            if lead not in SWEEP_LEADS:
                continue
            last = g[third(g) >= 2 / 3]
            u, _h, x = arm_scores(g)
            rows.append(dict(limit=limit, budget=budget, lead=lead, gap=gap_share(last), moved=x, useful=u))
    table = pd.DataFrame(rows)
    if table.empty:
        return
    print("\n### Q3 요약: 마지막 3분의 1 못 채움이 문턱 이하인 칸 (옮긴 대수 · 유효 배송은 현행 50 · 120분 대비)")
    for lead in SWEEP_LEADS:
        t = table[table["lead"] == lead]
        cur = t[(t["limit"] == CURRENT[0]) & (t["budget"] == CURRENT[1])]
        if cur.empty:
            continue
        cm, cu = float(cur["moved"].iloc[0]), float(cur["useful"].iloc[0])
        for floor in (0.03, 0.05, 0.10):
            ok = t[t["gap"] <= floor].sort_values("moved", ascending=False)
            names = ", ".join(f"{int(r.limit)} · {int(r.budget)}분(옮긴 {rate(r.moved, cm):.0%} · 유효 {rate(r.useful, cu):.0%})"
                              for r in ok.itertuples()) or "없음"
            print(f"  리드 {lead}분 · {floor:.0%} 이하: {names}")


def report() -> int:
    files = sorted(OUT_DIR.glob("main_*.csv"))
    files = [f for f in files if not f.stem.endswith("_회차")]
    if not files:
        print("결과가 없습니다 — 먼저 --run main을 돌리십시오")
        return 1
    frame = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    for dt in ("weekday", "holiday"):
        sub = frame[(frame["dt"] == dt) & (frame["arm"].isin(["A", "C"]))]
        if sub.empty:
            continue
        q1_table(sub, pv.DAY_TYPES[dt])
    verdict = q2_table(frame, "weekday")
    if (frame["dt"] == "holiday").any():
        q2_table(frame, "holiday")
    wk = frame[(frame["dt"] == "weekday") & (frame["arm"].isin(["A", "C"]))]
    last30 = wk[(wk["lead"] == 30)]
    last30 = last30[third(last30) >= 2 / 3]
    print("\n## 판정 (사전 등록: 평일)")
    g1 = gap_share(last30)
    print(f"  (1) 리드 30분 · 마지막 3분의 1 못 채우는 지시량 {g1:.1%} ≥ {GAP_FLOOR:.0%} : {'✅ 실재' if g1 >= GAP_FLOOR else '❌ 작다'}")
    for arm, name in (("D", "(2) D가 값을 내기 시작하는 리드"), ("C", "(3) 다시 세워야 하는 리드")):
        passed = sorted(lead for (a, lead), ok in verdict.items() if a == arm and ok)
        print(f"  {name}: {f'{passed[0]}분' if passed else '없음 (어느 리드에서도 기준 미달)'}")
    inst_files = sorted(OUT_DIR.glob("main_*_회차.csv"))
    inst = pd.concat([pd.read_csv(f) for f in inst_files], ignore_index=True) if inst_files else pd.DataFrame()
    extra_tables(frame, inst)
    age_tables(frame)
    stock_change_tables(frame)
    fired_tables(frame, inst)
    sweep = [pd.read_csv(f) for f in sorted(OUT_DIR.glob("sweep_*.csv")) if not f.stem.endswith("_회차")]
    if sweep:
        cur = frame[(frame["dt"] == "weekday") & (frame["arm"] == "A") & frame["lead"].isin(SWEEP_LEADS)]
        q3_table([cur] + sweep)
        frontier([cur] + sweep)
    return 0


NEARBY_RADII = (0.3, 0.5, 1.0)              # km. 사후 추정(2026-11-18)


def nearby_report(until: str = UNTIL) -> int:
    """사후 추정(2026-11-18, 등록에 없음): 작업 직전 계획(A안 리드 30분)의 수거 부족 · 배송 넘침을, 그때의 관측 재고로
    **계획 밖의 가장 가까운 여유 대여소 한 곳** 으로 메울 수 있었나. `webapp.orders.NEARBY_RADIUS_KM`(1.26.444)의 근거다.

    여유는 운영 안내(`orders._nearby_pool`)와 같은 자다. 더 실을 수 = 재고 − 목표(올림), 더 내릴 수 = min(목표 − 재고,
    거치대 × 상한 − 재고). 목표는 그 회차 계획 시각 재고로 다시 셈한 `compute_rebal_qty`의 `target_qty`다. 그 회차 계획에
    든 대여소(모든 차량)는 뺀다. 배송은 자리가 모자란 것(넘침 · 불가)만 센다(적재가 모자란 것은 내릴 곳의 문제가 아니다).
    """
    cache = OUT_DIR / ("grid_" + until.replace(":", "").replace(" ", "_") + ".pkl")
    if not cache.exists():
        print(f"관측 격자({cache.name})가 없습니다 — 먼저 --run main")
        return 1
    bc = load_baseline()
    print("### 사후: 계획 밖의 가까운 대여소로 메울 수 있었나 (리드 30분 A안, 2026-11-18, 등록에 없음)")
    for dt in ("weekday", "holiday"):
        main = OUT_DIR / f"main_{dt}.csv"
        if not main.exists():
            continue
        refs, grid, coords, days, hol = load_world(until, (dt,), cache=str(cache))
        obs = Observed(grid)
        frame = pd.read_csv(main)
        a = frame[(frame["arm"] == "A") & (frame["lead"] == 30)]
        ids = np.array(coords.index)
        lat, lon = coords["lat"].to_numpy(float), coords["lon"].to_numpy(float)
        pos = {sid: k for k, sid in enumerate(ids)}
        total = {"pick": 0.0, "drop": 0.0}
        lack = {"pick": 0.0, "drop": 0.0}
        events = {"pick": 0, "drop": 0}
        filled = {r: {"pick": 0.0, "drop": 0.0, "n_pick": 0, "n_drop": 0, "km": []} for r in NEARBY_RADII}
        for (day, dur), stops in a.groupby(["day", "dur"]):
            i_dep = obs.index.get(pd.Timestamp(day) + pd.Timedelta(hours=duration_start_hour(dur)))
            if i_dep is None or dur not in refs[dt]:
                continue
            ref = refs[dt][dur][0]
            stats = ref[["mu", "sigma", "parking_lot"]].copy()
            stats["stock"] = obs.row(i_dep - 30 // STOCK_TICK_MINUTES).reindex(stats.index)
            stats = stats.dropna(subset=["stock", "mu", "sigma", "parking_lot"])
            target = np.ceil(quiet(bc.target_mod.compute_rebal_qty, stats.copy())["target_qty"].reindex(ids).to_numpy(float))
            ceiling = ref["parking_lot"].reindex(ids).to_numpy(float) * TARGET_QTY_UPPER_RATIO
            outside = ~np.isin(ids, stops["station_id"].unique())
            seen = stops[stops["status"] != "관측 없음"]
            for action in ("pick", "drop"):
                total[action] += seen.loc[seen["action"] == action, "q"].sum()
            for row in seen.itertuples():
                short = row.q - (row.possible if pd.notna(row.possible) else row.q)
                if short <= 0 or row.station_id not in pos or (row.action == "drop" and row.status not in ("넘침", "불가")):
                    continue
                lack[row.action] += short
                events[row.action] += 1
                now = np.array([np.nan if obs.at(int(row.i_arr), s) is None else obs.at(int(row.i_arr), s) for s in ids])
                spare = now - target if row.action == "pick" else np.minimum(target - now, np.trunc(ceiling - now))
                km = haversine_km(lat[pos[row.station_id]], lon[pos[row.station_id]], lat, lon)
                for radius in NEARBY_RADII:
                    cand = outside & np.isfinite(spare) & (spare >= 1) & (km > 0) & (km <= radius)
                    if cand.any():
                        j = int(np.argmin(np.where(cand, km, np.inf)))
                        filled[radius][row.action] += min(short, int(spare[j]))
                        filled[radius]["n_" + row.action] += 1
                        filled[radius]["km"].append(float(km[j]))
        print(f"  {pv.DAY_TYPES[dt]}: 수거 지시 {total['pick']:,.0f}대 중 부족 {lack['pick']:,.0f}대"
              f"({rate(lack['pick'], total['pick']):.2%}, {events['pick']}건) · 배송 지시 {total['drop']:,.0f}대 중 넘침 "
              f"{lack['drop']:,.0f}대({rate(lack['drop'], total['drop']):.2%}, {events['drop']}건)")
        for radius in NEARBY_RADII:
            f = filled[radius]
            mean_km = float(np.mean(f["km"])) if f["km"] else float("nan")
            print(f"    반경 {radius}km: 수거 부족 {f['n_pick']}/{events['pick']}건 · {f['pick']:,.0f}대 메움"
                  f"(부족의 {rate(f['pick'], lack['pick']):.0%}, 수거 지시의 {rate(f['pick'], total['pick']):.2%})"
                  f" | 배송 넘침 {f['n_drop']}/{events['drop']}건 · {f['drop']:,.0f}대 | 권한 곳까지 평균 {mean_km:.2f}km")
    return 0


def fine_report() -> int:
    """66장(사전 등록 2026-11-18): 다시 세울 리드를 30분과 2시간 사이로 좁힌다. 판정은 65장 (3)과 같은 `q2_table` 기준이다."""
    main = OUT_DIR / "main_weekday.csv"
    fine = [f for f in sorted(OUT_DIR.glob("fine_weekday_*.csv")) if not f.stem.endswith("_회차")]
    if not main.exists() or not fine:
        print("결과가 없습니다 — 65장 --run main과 66장 --run fine을 먼저 돌리십시오")
        return 1
    base = pd.read_csv(main)
    base = base[base["arm"].isin(["A", "C"]) & base["lead"].isin([0, 30, 120])]
    frame = pd.concat([base] + [pd.read_csv(f) for f in fine], ignore_index=True)
    q1_table(frame, "평일 (66장, 30 · 120분과 C안은 65장 결과)")
    verdict = q2_table(frame, "weekday")
    leads = sorted(lead for arm, lead in verdict if arm == "C")
    print("\n## 판정 (66장 사전 등록: 평일, 65장 (3)과 같은 기준)")
    for lead in leads:
        print(f"  리드 {lead}분: {'✅' if verdict[('C', lead)] else '❌'}")
    passed = [lead for lead in leads if verdict[("C", lead)]]
    monotone = all(verdict[("C", b)] for a, b in zip(leads, leads[1:]) if verdict[("C", a)])
    print(f"  다시 세울 가장 짧은 리드: {f'{passed[0]}분' if passed else '없음'}"
          + ("" if monotone else " · ⚠️ 통과가 리드 순서대로가 아니다(등록대로 경계를 정하지 않는다)"))
    return 0


# ---------------------------------------------------------------- 진입점

def sanity(until: str) -> int:
    bc = load_baseline()
    step1 = bc.load_step1()
    solver = bc.ilp_mod.build_solver()
    refs, grid, coords, days, hol = load_world(until, ("weekday", "holiday"))
    obs = Observed(grid)
    print(f"관측 자료: {grid.index.min()} ~ {grid.index.max()} · 날 {len(days)}일 · 대여소 {grid.shape[1]:,}곳")
    for dt, ref in refs.items():
        print(f"참조 계획({pv.DAY_TYPES[dt]}): " + " · ".join(f"{d} ← '{lab}'" for d, (_, lab) in sorted(ref.items())))
    count = {"weekday": 0, "holiday": 0}
    first = None
    for day in days:
        dt = "holiday" if hol[day] else "weekday"
        for dur in DURATIONS:
            if dur not in refs[dt]:
                continue
            t = pd.Timestamp(day) + pd.Timedelta(hours=duration_start_hour(dur))
            if t in obs.index and len(obs.row(obs.index[t])) >= 0.5 * grid.shape[1]:
                count[dt] += 1
                i_p = obs.index[t] - 360 // STOCK_TICK_MINUTES
                if first is None and dt == "weekday" and i_p >= 0 and len(obs.row(i_p)) >= 0.5 * grid.shape[1]:
                    first = (day, dur, dt)
    print(f"회차 — 평일 {count['weekday']}개 · 휴일 {count['holiday']}개 (63장: 평일 108 · 휴일 67)")
    day, dur, dt = first
    bc.align_day_type(dt, bc.ilp_mod, bc.vrp_mod, bc.kpi_mod, step1)
    print("  (점검용 회차: 리드 6시간의 계획 시각에도 대여소 절반 이상이 관측된 첫 평일 회차)")
    t = pd.Timestamp(day) + pd.Timedelta(hours=duration_start_hour(dur))
    i_dep = obs.index[t]
    ref = refs[dt][dur][0]
    for lead in (0, 360):
        s_p = obs.row(i_dep - lead // STOCK_TICK_MINUTES)
        clustered, stops, _ = build_plan(bc, step1, solver, ref, s_p, dur, coords)
        ran = execute(stops.assign(route_last=stops.groupby("cluster")["arr_sec"].transform("max")), obs, i_dep, s_p,
                      ref["parking_lot"])
        print(f"  {day} {dur} 리드 {lead}분: 작업 대상 {clustered['station_id'].nunique()}곳 · 군집 {stops['cluster'].nunique()}개 · "
              f"정류장 {len(stops)}개 · 가장 긴 경로 도착 {stops['arr_sec'].max() / 60:.0f}분 · 못 채움 {gap_share(ran):.1%} · "
              f"집행률 {exec_rate(ran):.1%} · 판정 {ran['status'].value_counts().to_dict()}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sanity", action="store_true")
    parser.add_argument("--run", choices=("main", "sweep", "all", "fine"))
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--report-fine", action="store_true", help="66장 판정(60 · 90분 리드)")
    parser.add_argument("--report-nearby", action="store_true", help="사후 추정: 계획 밖 가까운 대여소로 메울 수 있었나")
    parser.add_argument("--until", default=UNTIL)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--leads", default=",".join(map(str, LEADS)), help=argparse.SUPPRESS)
    parser.add_argument("--day-types", default="weekday,holiday", help=argparse.SUPPRESS)
    parser.add_argument("--arms", default="A,D,E", help=argparse.SUPPRESS)
    parser.add_argument("--out", help=argparse.SUPPRESS)
    parser.add_argument("--grid", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.child:
        return child(args)
    if args.sanity:
        return sanity(args.until)
    if args.run:
        if args.run == "fine":
            return spawn(fine_cells(), args.until)
        cells = (main_cells() if args.run in ("main", "all") else []) + (sweep_cells() if args.run in ("sweep", "all") else [])
        return spawn(cells, args.until)
    if args.report:
        return report()
    if args.report_fine:
        return fine_report()
    if args.report_nearby:
        return nearby_report(args.until)
    parser.print_help()
    return 0


if __name__ == "__main__":
    project_config.exit_if_help(__doc__)
    raise SystemExit(main())
