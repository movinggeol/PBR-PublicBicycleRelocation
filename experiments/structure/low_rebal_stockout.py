"""재배치 흔적이 드문 대여소에서 관측 결품과 복원 B0를 맞댄다 (EXPERIMENTS NN장 초안, 2026-11-21 사전 등록).

왜: 관측 재고(`stock_history`)에는 공사의 실제 재배치가 섞여 있어 '재배치를 안 했다면'(B0)을 볼 수 없다
(ML_고도화_계획 F5 막힘). 32장은 회차 시작 시각에 세운 계획으로 출발 재고를 맞춘 뒤에도 관측 결품이 복원 B0보다
컸고(평일 +12 · +18 · +49 · +94%), 남은 원인 셋(지난달 순수요 · 0 절단 · 공사의 실제 재배치)을 가르지 못했다.
재배치 흔적이 거의 없는 대여소만 고르면 셋째 원인이 빠진 채로 복원을 관측과 맞댈 수 있다.

등록 전에 본 것: 동시각 계획 실행에서 32장의 두 쪽 모집단이 다르다. 복원(`kpi_summary.stockout_hours_before`)은
step4가 `pick_drop`(선정 대여소, 회차당 85 ~ 97곳)으로 내고, 관측은 `observed_stockout.target_stations()`가
`rebalance_plan`의 |rebal_qty| > 2(회차당 250 ~ 340곳)로 센다. 그래서 여기서는 두 쪽을 **같은 대여소 · 같은 창**에서
잰다. 결품 값은 등록 전에 보지 않았다.

무엇을 하나
  단위     : (날, 회차, 대여소)의 창. 출발 재고 = 회차 시작 칸의 관측(없으면 앞뒤 한 칸).
             창 = 시작 뒤 첫 칸부터 회차 끝 칸까지 6H칸(복원이 매시 끝의 상태를 세는 것과 맞춘다). 80% 이상 관측된 창만
  관측 결품: 창에서 재고 <= 0인 관측 칸의 비율 x H(시간)
  복원 B0  : step4 `_simulate_stock`(운영과 같은 궤적 함수)을 그 출발 재고에서 `26년 03월` 같은 요일 구분의 날마다
             돌린 결품 시간의 평균. 거치대가 없으면 step4와 같은 폴백(재고 x 2 + 1)
  모집단   : 주 P2 = 그 출발 재고로 운영 함수 compute_rebal_qty를 다시 낸 |rebal_qty| > 2(32장 관측 쪽 정의) ·
             보조 P1 = 같은 계획으로 select_top_unbalanced_st가 고른 대여소(step4 복원의 모집단)
  흔적     : 이웃한 두 관측 칸의 재고 차 |d| >= J. 엄격 J = 5(pv.JUMP, 47 · 53 · 54장 관례) · 느슨 J = 8(대여이력으로 잰
             이용자만의 |d| >= 8 비율 0.075%, ML_OPPORTUNITIES 1단계)
  드묾     : 관측한 모든 날(평일 · 휴일, 모든 시각)에서 흔적 칸이 관측 30일(관측 칸 4,320개)당 1개 이하인 대여소

판정 (평일 · P2 · 회차 x 문턱마다. 결과를 보고 바꾸지 않는다)
  1. 잴 수 있음: '드묾'이면서 작업 대상급인 대여소가 30곳 이상. 아니면 그 칸은 측정 불가
  2. 맞는다    : rho >= 0.5이고 비(관측 평균 / 복원 평균)가 0.67 ~ 1.5 안. rho는 그 회차에서 창 3개 이상인 대여소의
                 대여소별 평균끼리의 스피어만(대여소가 3곳 미만이면 계산하지 않고 '맞는다'를 줄 수 없다)
  3. 어긋나면 방향: 비 > 1.5 복원이 결품을 과소 추정 · 비 < 0.67 과대 추정 · rho < 0.5 대여소 순위도 맞지 않음
  멈추는 규칙: 두 문턱 모두 모든 평일 회차에서 30곳에 못 미치면 '측정 불가'로 적고 찍기만 표만 낸다

찍기만: '흔적 있음' · 전체 P2 · 창 단위 거르기(창 안에 흔적 칸이 없는 창) · P1과 32장식 어긋난 짝(관측은 P2, 복원은 P1) ·
  수거 쪽(rebal < -2) · 배송 쪽(rebal > 2) · 휴일 · 날 덩어리 부트스트랩(2,000회, 씨앗 42) 95% 구간 ·
  `--chain` 사슬 교차 확인(25년 9 · 10 · 11월 대여이력을 44장 함수 그대로 이어 대여소별 평일 하루 공사 이동을 센다)

알려진 한계: 흔적 문턱보다 작은 재배치(한두 대)와 이용 흐름에 가려진 재배치는 못 본다. '드묾'은 한산한 대여소로
  치우친다(엄격 문턱은 붐비는 곳의 이용 흐름까지 거른다). 관측은 순수요 기간(26년 03월)과 다른 철(26년 가을)이다.
  0 절단과 지난달 순수요의 몫은 이 설계로도 가르지 못한다.

등록 뒤 추가 (본 결과를 본 뒤, 판정 아님): `--same-day`는 32장이 쓴 동시각 계획 실행 그대로 관측과 복원의 모집단 짝만
  바꿔 다시 잰다. 본 측정에서 '32장식 짝'(관측 P2 / 복원 P1)이 32장 표와 같은 모양을 내고 맞춘 짝은 1에 가까웠기 때문이다.

실행:
    python experiments/structure/low_rebal_stockout.py            # 본 측정(평일 판정 + 휴일 찍기만)
    python experiments/structure/low_rebal_stockout.py --chain    # 사슬 교차 확인(대여이력 3개월, 판정 아님)
    python experiments/structure/low_rebal_stockout.py --same-day # 32장 동시각 계획의 모집단 짝 확인(사후, 판정 아님)
"""
import argparse
import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import project_config  # noqa: E402,F401  (콘솔 인코딩을 먼저 맞춘다)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import db  # noqa: E402
import plan_validity as pv  # noqa: E402
from experiments._shared import default_run_label, load_baseline  # noqa: E402
from project_config import (DATA_ROOT, DAY_TYPES, DURATIONS, REBAL_MIN_QTY, TICKS_PER_HOUR,  # noqa: E402
                            align_day_type, duration_hours, duration_start_hour, holiday_mask)

# ---------------------------------------------------------------- 사전 등록 (2026-11-21 13:22)
UNTIL = "2026-11-08 00:00"                                     # 63 · 65 · 66장과 같은 자료 끝
GRID_CACHE = DATA_ROOT / "route_drift" / "grid_2026-11-08_0000.pkl"   # 65장이 만든 관측 격자
JUMPS = {"엄격": pv.JUMP, "느슨": 8}
RARE_PER_30_DAYS = 1.0                                         # 흔적 칸이 관측 30일당 이만큼 이하면 '드묾'
TICKS_PER_DAY = 24 * TICKS_PER_HOUR
MIN_OBSERVED = 0.8
MIN_STATIONS = 30
RHO_FLOOR = 0.5
RATIO_LOW, RATIO_HIGH = 0.67, 1.5
MIN_WINDOWS_FOR_RHO = 3
BOOT = 2000
SEED = 42
CHAIN_MONTHS = ("25년 09월", "25년 10월", "25년 11월")         # 관측(26년 가을)과 같은 철
OUT_DIR = Path(__file__).resolve().parent
STAMP = "261121"


def quiet(func, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return func(*args, **kwargs)


# ---------------------------------------------------------------- 입력

def load_grid():
    """65장이 만든 관측 격자(시각 x 대여소, 10분)와 하루 30틱 이상인 날. 없으면 멈춘다(원천 1,500만 행을 다시 읽지 않는다)."""
    if not GRID_CACHE.exists():
        raise SystemExit(f"관측 격자 캐시가 없습니다: {GRID_CACHE}\n"
                         "  python experiments/structure/route_drift.py --run main 이 처음 돌 때 만든다(65장).")
    grid, days = pd.read_pickle(GRID_CACHE)
    return grid.astype("float32"), list(days)


def trace_rates(values: np.ndarray, threshold: int) -> tuple:
    """대여소마다 (흔적 칸 수, 관측 칸 수). 흔적 = 이웃한 두 관측 칸의 재고 차 |d| >= threshold."""
    d = np.abs(np.diff(values, axis=0))
    hits = np.nansum(d >= threshold, axis=0)            # NaN 비교는 False
    seen = np.isfinite(values).sum(axis=0)
    return hits.astype(int), seen.astype(int)


def station_classes(grid: pd.DataFrame) -> pd.DataFrame:
    """대여소마다 문턱별 흔적 비율(관측 30일당)과 '드묾' 여부."""
    values = grid.to_numpy(dtype="float32")
    out = pd.DataFrame(index=grid.columns)
    for name, j in JUMPS.items():
        hits, seen = trace_rates(values, j)
        days = seen / TICKS_PER_DAY
        rate = np.where(days > 0, hits / np.maximum(days, 1e-9) * 30, np.nan)
        out[f"hits_{name}"] = hits
        out[f"rate_{name}"] = rate
        out[f"rare_{name}"] = rate <= RARE_PER_30_DAYS
    out["obs_days"] = np.isfinite(values).sum(axis=0) / TICKS_PER_DAY
    return out


def references(conn, day_type: str, net: pd.DataFrame) -> dict:
    """회차 → mu · sigma · 거치대(대여소 색인). 2026-11-08 전에 만든 plan 실행의 거치대에 지금 순수요로 mu · sigma를 다시 낸다.

    참조 계획이 옛 회차(`_20_05`)만 가져 지금 회차가 없으면, 다른 회차의 행을 대여소 목록 · 거치대로 쓰고 mu · sigma는
    그 회차의 시각으로 `pv.current_stats`가 다시 낸다(대여소 목록과 거치대는 회차와 무관하다).
    """
    refs = pv.load_refs(conn, day_type, before=UNTIL)
    if not refs:
        raise SystemExit(f"{day_type}: {UNTIL} 전에 만든 계획 실행이 없습니다")
    base_frame, base_label = next(iter(refs.values()))
    out = {}
    for dur in DURATIONS:
        frame, label = refs.get(dur, (base_frame, base_label))
        stats = pv.current_stats(frame, net, dur)
        out[dur] = (stats, label if dur in refs else f"{base_label} (거치대만)")
    return out


def restoration_table(kpi_mod, net: pd.DataFrame, parking: pd.Series, dur: str, smax: int) -> pd.DataFrame:
    """(대여소 x 출발 재고 0..smax) → 복원 B0 결품 시간. step4 `stockout_simulation()`의 재배치 전 계산을 대여소별로 푼다.

    같은 궤적 함수(`_simulate_stock`) · 같은 거치대 폴백(재고 x 2 + 1) · 같은 분모(그 요일 구분의 날 수).
    """
    hours = duration_hours(dur)
    cols = [f"net_{h:02d}" for h in hours if f"net_{h:02d}" in net.columns]
    merged = net[["station_id", "날짜"] + cols].merge(parking.rename("parking_lot"), left_on="station_id",
                                                     right_index=True, how="inner").reset_index(drop=True)
    n_days = max(merged["날짜"].nunique(), 1)
    table = {}
    for s in range(smax + 1):
        init = pd.Series(float(s), index=merged.index)
        cap = merged["parking_lot"].astype(float).fillna(s * 2 + 1)
        sim = kpi_mod._simulate_stock(merged, init, cap, hours)
        table[s] = sim["stockout"].groupby(merged["station_id"]).sum() / n_days
    return pd.DataFrame(table)


def select_p1(step1, frame: pd.DataFrame, coords: pd.DataFrame, dur: str) -> set:
    """운영 선정(select_top_unbalanced_st, 현행 · K 없음)이 고르는 대여소. 좌표가 없는 곳은 군집에 못 드므로 뺀다(65장과 같다)."""
    buf = io.StringIO()
    frame[["station_id", "mu", "sigma", "parking_lot", "stock", "target_qty", "rebal_qty"]].to_csv(buf, index=False)
    buf.seek(0)
    info = coords.reindex(frame["station_id"]).reset_index()
    info["parking_lot"] = frame["parking_lot"].to_numpy()
    info["stock"] = frame["stock"].to_numpy()
    cand = quiet(step1.select_top_unbalanced_st, buf, dur, info)
    cand = cand.dropna(subset=["lat", "lon"])
    return set(cand["station_id"])


# ---------------------------------------------------------------- 창 모으기

def collect(day_type: str, grid: pd.DataFrame, days: list, hol: dict, classes: pd.DataFrame, bc, step1,
            coords: pd.DataFrame) -> pd.DataFrame:
    with db.session() as conn:
        net = pv.load_net(day_type)
        refs = references(conn, day_type, net)
    align_day_type(day_type, bc.kpi_mod, step1)
    values = grid.to_numpy(dtype="float32")
    ids = np.array(grid.columns)
    index = {ts: i for i, ts in enumerate(grid.index)}
    n = len(grid.index)
    smax = int(np.nanmax(values))
    tables = {}
    for dur in DURATIONS:
        stats, label = refs[dur]
        tables[dur] = restoration_table(bc.kpi_mod, net, stats["parking_lot"], dur, smax)
        print(f"  [{day_type} {dur}] 참조 계획 {label} · 복원 표 {tables[dur].shape[0]:,}곳 x 출발 재고 0~{smax}", flush=True)
    rows = []
    for day in days:
        if hol[day] != (day_type == "holiday"):
            continue
        for dur in DURATIONS:
            H = len(duration_hours(dur))
            t0 = pd.Timestamp(day) + pd.Timedelta(hours=duration_start_hour(dur))
            i0 = index.get(t0)
            if i0 is None or i0 + H * TICKS_PER_HOUR >= n:
                continue
            start = values[i0].copy()
            for k in (i0 - 1, i0 + 1):                 # 시작 칸이 비면 앞뒤 한 칸
                if 0 <= k < n:
                    miss = ~np.isfinite(start)
                    start[miss] = values[k][miss]
            block = values[i0 + 1:i0 + 1 + H * TICKS_PER_HOUR]
            seen = np.isfinite(block)
            frac = seen.mean(axis=0)
            empty = np.where(seen, block <= 0, False).sum(axis=0) / np.maximum(seen.sum(axis=0), 1) * H
            steps = np.abs(np.diff(values[i0:i0 + 1 + H * TICKS_PER_HOUR], axis=0))
            win_hit = {name: np.nansum(steps >= j, axis=0) > 0 for name, j in JUMPS.items()}
            ok = np.isfinite(start) & (frac >= MIN_OBSERVED)
            stats, _ = refs[dur]
            sid = pd.Index(ids[ok])
            keep = sid.isin(stats.index) & sid.isin(tables[dur].index)
            if not keep.any():
                continue
            sid = sid[keep]
            pos = np.flatnonzero(ok)[keep]
            s0 = pd.Series(start[pos], index=sid)
            st = stats.loc[sid, ["mu", "sigma", "parking_lot"]].copy()
            st["stock"] = s0
            rebal = quiet(bc.target_mod.compute_rebal_qty, st.copy())
            frame = rebal.reset_index().rename(columns={"index": "station_id"})
            if "station_id" not in frame.columns:
                frame = frame.rename(columns={frame.columns[0]: "station_id"})
            frame = frame.set_index("station_id")
            p2 = frame.index[frame["rebal_qty"].abs() > REBAL_MIN_QTY]
            p1 = select_p1(step1, frame.reset_index(), coords, dur)
            chosen = p2.union(pd.Index(sorted(p1 & set(frame.index))))
            if chosen.empty:
                continue
            where = {s: p for s, p in zip(sid, pos)}
            js = np.array([where[s] for s in chosen])
            s_int = np.clip(np.rint(s0.reindex(chosen).to_numpy()), 0, smax).astype(int)
            tab = tables[dur].reindex(chosen).to_numpy()
            rest = tab[np.arange(len(chosen)), s_int]
            part = pd.DataFrame({
                "dt": day_type, "day": str(pd.Timestamp(day).date()), "dur": dur, "station_id": chosen,
                "start": s0.reindex(chosen).to_numpy(), "rebal": frame.loc[chosen, "rebal_qty"].to_numpy(),
                "in_p2": chosen.isin(p2), "in_p1": chosen.isin(list(p1)),
                "obs": empty[js], "rest": rest, "frac": frac[js]})
            for name in JUMPS:
                part[f"win_{name}"] = win_hit[name][js]
                part[f"rare_{name}"] = classes.loc[chosen, f"rare_{name}"].to_numpy()
            rows.append(part)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


# ---------------------------------------------------------------- 지표

def spearman(a: pd.Series, b: pd.Series) -> float:
    if len(a) < 3:
        return float("nan")
    return float(a.rank().corr(b.rank()))


def cell_stats(g: pd.DataFrame, rng) -> dict:
    """창 묶음 하나의 지표: 비 · 날 덩어리 부트스트랩 95% 구간 · 대여소별 평균의 rho."""
    out = {"windows": len(g), "stations": int(g["station_id"].nunique()), "days": int(g["day"].nunique())}
    if g.empty:
        return dict(out, obs=np.nan, rest=np.nan, ratio=np.nan, lo=np.nan, hi=np.nan, rho=np.nan, rho_n=0)
    obs, rest = float(g["obs"].mean()), float(g["rest"].mean())
    out.update(obs=obs, rest=rest, ratio=obs / rest if rest > 0 else np.nan)
    by_day = g.groupby("day")[["obs", "rest"]].sum()
    if len(by_day) >= 2:
        pick = rng.integers(0, len(by_day), size=(BOOT, len(by_day)))
        o, r = by_day["obs"].to_numpy()[pick].sum(axis=1), by_day["rest"].to_numpy()[pick].sum(axis=1)
        ratios = np.where(r > 0, o / np.where(r > 0, r, 1), np.nan)
        out["lo"], out["hi"] = (float(np.nanpercentile(ratios, 2.5)), float(np.nanpercentile(ratios, 97.5)))
    else:
        out["lo"] = out["hi"] = np.nan
    per = g.groupby("station_id").agg(obs=("obs", "mean"), rest=("rest", "mean"), n=("obs", "size"))
    per = per[per["n"] >= MIN_WINDOWS_FOR_RHO]
    out["rho"], out["rho_n"] = spearman(per["obs"], per["rest"]), int(len(per))
    return out


def verdict(c: dict) -> str:
    if c["stations"] < MIN_STATIONS:
        return f"측정 불가({c['stations']}곳 < {MIN_STATIONS})"
    fit_ratio = RATIO_LOW <= c["ratio"] <= RATIO_HIGH if np.isfinite(c["ratio"]) else False
    fit_rho = c["rho"] >= RHO_FLOOR if np.isfinite(c["rho"]) else False
    if fit_ratio and fit_rho:
        return "맞는다"
    why = []
    if not np.isfinite(c["ratio"]):
        why.append("비 계산 불가")
    elif c["ratio"] > RATIO_HIGH:
        why.append("복원이 결품을 과소 추정")
    elif c["ratio"] < RATIO_LOW:
        why.append("복원이 결품을 과대 추정")
    if not fit_rho:
        why.append("대여소 순위가 맞지 않음" if np.isfinite(c["rho"]) else "rho 계산 불가")
    return "어긋남: " + " · ".join(why)


def fmt(c: dict) -> str:
    ci = f"[{c['lo']:.2f}, {c['hi']:.2f}]" if np.isfinite(c.get("lo", np.nan)) else "[-]"
    rho = f"{c['rho']:.2f} ({c['rho_n']}곳)" if np.isfinite(c["rho"]) else f"- ({c['rho_n']}곳)"
    return (f"{c['stations']:4d}곳 · 창 {c['windows']:6,d} · 관측 {c['obs']:.3f}h · 복원 {c['rest']:.3f}h · "
            f"비 {c['ratio']:.2f} {ci} · rho {rho}")


def report(frame: pd.DataFrame) -> pd.DataFrame:
    rng = np.random.default_rng(SEED)
    lines = []
    for dt in DAY_TYPES:
        part = frame[frame["dt"] == dt]
        if part.empty:
            continue
        name = "평일 (판정)" if dt == "weekday" else "휴일 (찍기만)"
        print(f"\n### {name}: 날 {part['day'].nunique()}일")
        for dur in DURATIONS:
            g = part[part["dur"] == dur]
            if g.empty:
                print(f"  {dur}: 창 없음")
                continue
            p2 = g[g["in_p2"]]
            print(f"\n  [{dur}] 날 {g['day'].nunique()}일")
            for jname in JUMPS:
                rare = p2[p2[f"rare_{jname}"]]
                c = cell_stats(rare, rng)
                v = verdict(c) if dt == "weekday" else "(찍기만)"
                print(f"    {jname}(J={JUMPS[jname]}) 드묾       : {fmt(c)}  -> {v}")
                lines.append(dict(dt=dt, dur=dur, jump=jname, group="드묾", verdict=v, **c))
                other = cell_stats(p2[~p2[f"rare_{jname}"]], rng)
                print(f"    {jname}(J={JUMPS[jname]}) 흔적 있음  : {fmt(other)}")
                lines.append(dict(dt=dt, dur=dur, jump=jname, group="흔적 있음", verdict="", **other))
                clean = cell_stats(p2[~p2[f"win_{jname}"]], rng)
                print(f"    {jname}(J={JUMPS[jname]}) 창에 흔적 없음: {fmt(clean)}")
                lines.append(dict(dt=dt, dur=dur, jump=jname, group="창 단위", verdict="", **clean))
            allp2 = cell_stats(p2, rng)
            print(f"    전체 P2              : {fmt(allp2)}")
            lines.append(dict(dt=dt, dur=dur, jump="", group="전체 P2", verdict="", **allp2))
            p1 = cell_stats(g[g["in_p1"]], rng)
            print(f"    P1(선정)             : {fmt(p1)}")
            lines.append(dict(dt=dt, dur=dur, jump="", group="P1", verdict="", **p1))
            mism = allp2["obs"] / p1["rest"] if p1["rest"] and np.isfinite(p1["rest"]) else np.nan
            print(f"    32장식 짝(관측 P2 / 복원 P1): 비 {mism:.2f}  (맞춘 짝: P2 {allp2['ratio']:.2f} · P1 {p1['ratio']:.2f})")
            for side, mask in (("배송 쪽", p2["rebal"] > 0), ("수거 쪽", p2["rebal"] < 0)):
                c = cell_stats(p2[mask], rng)
                cr = cell_stats(p2[mask & p2["rare_느슨"]], rng)
                print(f"    {side} 전체 P2       : {fmt(c)}")
                print(f"    {side} 느슨 드묾     : {fmt(cr)}")
    table = pd.DataFrame(lines)
    wk = table[(table["dt"] == "weekday") & (table["group"] == "드묾")]
    print("\n### 판정 (평일 · P2 · 드묾, 사전 등록)")
    for jname in JUMPS:
        w = wk[wk["jump"] == jname]
        fits = int((w["verdict"] == "맞는다").sum())
        measurable = int((~w["verdict"].str.startswith("측정 불가")).sum())
        print(f"  {jname}(J={JUMPS[jname]}): 잴 수 있는 회차 {measurable} / {len(w)} · 맞는다 {fits}")
        for r in w.itertuples():
            print(f"    {r.dur}: {r.verdict}")
    if (~wk["verdict"].str.startswith("측정 불가")).sum() == 0:
        print("  멈추는 규칙: 두 문턱 모두 모든 회차에서 30곳에 못 미친다 -> 측정 불가")
    return table


# ---------------------------------------------------------------- 사슬 교차 확인 (판정 아님)

def chain_check() -> int:
    import rebalance_trace as rt
    grid, _days = load_grid()
    classes = station_classes(grid)
    del grid
    with db.session() as conn:
        df, _ends = rt.load_rentals(conn, list(CHAIN_MONTHS))
    print(f"대여이력 {', '.join(CHAIN_MONTHS)}: {len(df):,}건")
    rent_day = df["rent_at"].dt.normalize()
    weekdays = int(rent_day[~np.asarray(holiday_mask(rent_day), dtype=bool)].nunique())
    pairs = rt.build_pairs(df)
    del df
    moves = rt.classify_moves(pairs)
    del pairs
    moves = moves[moves["kind"].isin(rt.MOVE_KINDS) & ~moves["holiday"]]
    centers = set(rt.CENTER_STATIONS)
    out_ = moves.loc[~moves["from_station"].isin(centers), "from_station"].value_counts()
    in_ = moves.loc[~moves["to_station"].isin(centers), "to_station"].value_counts()
    per_day = out_.add(in_, fill_value=0) / max(weekdays, 1)
    print(f"평일 {weekdays}일 · 공사 이동(장기 제외) {len(moves):,}건 · 손댄 대여소 {len(per_day):,}곳")
    chain = per_day.reindex(classes.index).fillna(0.0)      # 격자의 대여소마다. 사슬에 안 나오면 0대
    for jname in JUMPS:
        rare = classes[f"rare_{jname}"].astype(bool)
        rate = classes[f"rate_{jname}"]
        rho = spearman(rate[rate.notna()], chain[rate.notna()])
        print(f"  {jname}(J={JUMPS[jname]}): 드묾 {int(rare.sum()):,}곳 하루 공사 이동 평균 {chain[rare].mean():.3f}대"
              f" (0인 곳 {float((chain[rare] == 0).mean()):.1%}) · 흔적 있음 {int((~rare).sum()):,}곳 {chain[~rare].mean():.3f}대"
              f" (0인 곳 {float((chain[~rare] == 0).mean()):.1%}) · 흔적 비율 대 사슬 이동 rho {rho:.2f}")
    path = OUT_DIR / f"low_rebal_stockout_chain_{STAMP}.csv"
    classes.assign(chain_per_weekday=chain.reindex(classes.index)).to_csv(path, encoding="utf-8")
    print(f"대여소 표: {path}")
    return 0


# ---------------------------------------------------------------- 32장 동시각 계획의 짝 확인 (등록 뒤 추가, 판정 아님)

def same_day_check() -> int:
    """32장이 쓴 **바로 그 동시각 계획 실행**으로 모집단의 짝을 바꿔 다시 잰다 (본 결과를 본 뒤 더한 사후 확인).

    32장 · 원고 8.3의 비교는 관측을 `target_stations()`(|rebal_qty| > 2, P2)로, 복원을 `kpi_summary`(step4가
    `pick_drop`으로 낸 값, P1)로 쟀다. 여기서는 같은 실행 · 같은 창 · 같은 넣는 규칙(`same_day_usable`)으로 네 짝을 찍는다.
      A 32장 짝     : 관측 P2 평균 / 저장된 복원(P1)        (32장 표를 다시 내야 한다)
      B 맞춘 짝 P1  : 관측 P1 평균 / 저장된 복원(P1)        (복원은 그대로, 관측 쪽 대여소만 맞춘다)
      C 맞춘 짝 P1' : 관측 P1 평균 / 지금 DB로 다시 낸 복원(P1)
      D 맞춘 짝 P2' : 관측 P2 평균 / 지금 DB로 다시 낸 복원(P2)
    관측 창은 32장과 같다([회차 시작, 끝), 재고 <= 0인 틱 x 10분). 다시 낸 복원은 `recomputed_recon()`과 같은 계산을
    대여소별로 푼 것이다(그 실행의 순수요 기간 · 그 실행의 출발 재고 · step4 거치대 폴백).
    """
    import observed_stockout as ob
    from datetime import timedelta
    from project_config import STOCK_TICK_MINUTES, select_day_type

    bc = load_baseline()
    out = []
    for dt in DAY_TYPES:
        runs = ob.same_day_runs(dt)
        cache = {}
        for run in runs.itertuples(index=False):
            hours = duration_hours(run.duration)
            start, end = ob.same_day_window(run.start, run.duration)
            day = start.date()
            if day not in cache:
                with db.session() as conn:
                    loaded = db.load_stock_history(conn, start=start.strftime("%Y-%m-%d"),
                                                   end=(start + timedelta(days=2)).strftime("%Y-%m-%d"))
                if not loaded.empty:
                    loaded["관측"] = pd.to_datetime(loaded["observed_at"])
                cache[day] = loaded
            obs = cache[day]
            if obs.empty:
                continue
            p2 = ob.target_stations(run.run_label, run.duration)
            with db.session() as conn:
                pdp = db.load_frame(conn, "pick_drop", run_label=run.run_label, duration=run.duration)
                plan = pd.read_sql("SELECT station_id, stock, parking_lot FROM rebalance_plan"
                                   " WHERE run_label = ? AND duration = ?", conn, params=[run.run_label, run.duration])
                period = conn.execute("SELECT period FROM runs WHERE run_label = ?", (run.run_label,)).fetchone()[0]
                net = db.load_frame(conn, "net_demand", period=period)
            p1 = set(pdp["station_id"])
            window = obs[(obs["관측"] >= start) & (obs["관측"] < end)]
            ticks = window["관측"].nunique()
            need = int(len(hours) * TICKS_PER_HOUR * ob.COMPLETE_DAY_RATIO)
            empty = (window["stock"] <= 0).groupby(window["station_id"]).sum() * STOCK_TICK_MINUTES / 60
            mine = window[window["station_id"].isin(p2)]
            first = mine[mine["관측"] == mine["관측"].min()] if not mine.empty else mine
            row = {"complete": ticks >= need,
                   "snap_empty": ob.start_empty_share(run.run_label, run.duration, p2),
                   "real_empty": (first["stock"] <= 0).mean() * 100 if len(first) else float("nan")}
            # 다시 낸 복원: P1은 step4처럼 pick_drop의 재고 · 거치대, P2는 계획표(rebalance_plan)의 재고 · 거치대
            net = select_day_type(net.rename(columns={"date": "날짜"}), "날짜", dt)
            rec = {}
            for name, st in (("p1", pdp[["station_id", "stock", "parking_lot"]].drop_duplicates("station_id")),
                             ("p2", plan[plan["station_id"].isin(p2)].drop_duplicates("station_id"))):
                st = st.copy()
                st["parking_lot"] = st["parking_lot"].fillna(st["stock"] * 2 + 1)
                merged = net.merge(st, on="station_id", how="inner")
                if merged.empty:
                    rec[name] = float("nan")
                    continue
                sim = bc.kpi_mod._simulate_stock(merged, merged["stock"], merged["parking_lot"], hours)
                per = sim["stockout"].groupby(merged["station_id"]).sum() / max(merged["날짜"].nunique(), 1)
                rec[name] = float(per.mean())
            out.append({"dt": dt, "run": run.run_label, "dur": run.duration, "date": str(day),
                        "usable": ob.same_day_usable(row) == "", "why": ob.same_day_usable(row),
                        "stored_p1": float(run.복원), "rec_p1": rec["p1"], "rec_p2": rec["p2"],
                        "obs_p1": float(empty.reindex(sorted(p1)).dropna().mean()),
                        "obs_p2": float(empty.reindex(sorted(p2)).dropna().mean()),
                        "n_p1": len(p1), "n_p2": len(p2)})
    table = pd.DataFrame(out)
    path = OUT_DIR / f"low_rebal_stockout_sameday_{STAMP}.csv"
    table.to_csv(path, index=False, encoding="utf-8")
    print(f"동시각 계획 {len(table)}건 (넣는 것 {int(table['usable'].sum())}건) → {path}")
    print("관측 ÷ 복원 − 1 (%) · 회차마다 넣는 실행의 평균끼리(32장 summarize_same_day와 같은 평균)")
    print("요일 회차   날 | A 32장 짝 | B 맞춘 P1(저장) | C 맞춘 P1(다시) | D 맞춘 P2(다시) | 다시/저장(P1) | P1 · P2 곳")
    for (dt, dur), g in table[table["usable"]].groupby(["dt", "dur"], sort=False):
        m = g[["stored_p1", "rec_p1", "rec_p2", "obs_p1", "obs_p2", "n_p1", "n_p2"]].mean()
        cells = [m["obs_p2"] / m["stored_p1"], m["obs_p1"] / m["stored_p1"], m["obs_p1"] / m["rec_p1"],
                 m["obs_p2"] / m["rec_p2"]]
        print(f"{dt:7} {dur:7} {g['date'].nunique():2d} | " + " | ".join(f"{(c - 1) * 100:+6.0f}%" for c in cells)
              + f" | {m['rec_p1'] / m['stored_p1']:.2f} | {m['n_p1']:.0f} · {m['n_p2']:.0f}")
    return 0


# ---------------------------------------------------------------- 진입점

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="재배치 흔적이 드문 대여소의 관측 결품 대 복원 B0")
    p.add_argument("--chain", action="store_true", help="사슬 교차 확인만(대여이력 3개월)")
    p.add_argument("--same-day", action="store_true", help="32장 동시각 계획으로 모집단 짝 확인(사후, 판정 아님)")
    a = p.parse_args(argv)
    if a.chain:
        return chain_check()
    if a.same_day:
        return same_day_check()
    grid, days = load_grid()
    hol = dict(zip(days, np.asarray(holiday_mask(pd.Series(pd.to_datetime(days))), dtype=bool)))
    print(f"관측 격자 {grid.shape[0]:,}칸 x {grid.shape[1]:,}곳 · 하루 30틱 이상 {len(days)}일"
          f" (평일 {sum(not v for v in hol.values())} · 휴일 {sum(hol.values())}) · 자료 끝 {UNTIL}")
    classes = station_classes(grid)
    for jname in JUMPS:
        print(f"  {jname}(J={JUMPS[jname]}): 드묾 {int(classes[f'rare_{jname}'].sum()):,} / {len(classes):,}곳"
              f" · 흔적 비율(관측 30일당) 중앙 {np.nanmedian(classes[f'rate_{jname}']):.1f}")
    bc = load_baseline()
    step1 = bc.load_step1()
    with db.session() as conn:
        info = db.load_frame(conn, "station_info", run_label=default_run_label(conn))
    coords = info.drop_duplicates("station_id").set_index("station_id")[["station_name", "lat", "lon"]]
    frames = [collect(dt, grid, days, hol, classes, bc, step1, coords) for dt in DAY_TYPES]
    frame = pd.concat([f for f in frames if not f.empty], ignore_index=True)
    path = OUT_DIR / f"low_rebal_stockout_{STAMP}.csv"
    frame.to_csv(path, index=False, encoding="utf-8")
    print(f"\n창 {len(frame):,}개 → {path}")
    table = report(frame)
    table.to_csv(OUT_DIR / f"low_rebal_stockout_cells_{STAMP}.csv", index=False, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
