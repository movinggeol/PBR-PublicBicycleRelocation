"""수요 절단 보정: 빈 시간을 빼고 대여율을 다시 내면 계획의 진단이 관측과 더 맞나 (사전 등록 2026-11-21).

왜: 대여소가 비어 있던 시간에는 대여가 관측되지 않는다. 그래서 순수요(대여 − 반납)의 mu가 순유출 쪽으로 낮게 잡히고,
자주 비는 대여소일수록 목표 재고가 모자라게 선다. 지금까지는 이 절단을 결품 복원이 '하한'이라는 한계로만 적었다
(FORMULATION 408행, KPI 3-B). 수요 추정에 보정을 넣어 잰 적은 없다.

무엇을 하나:
  1. 학습 달(26년 03월)의 '빈 시간 후보'를 대여이력의 자전거 번호 사슬로 복원한다(44장 rebalance_trace의 함수).
     하한 재고 = 같은 자리에서 반납된 뒤 같은 자리에서 다음 대여가 난 자전거의 그 사이 구간 + 자료 안 첫 사건이 03월
     대여인 자전거의 [월초, 첫 대여) + 마지막 사건이 03월 반납인 자전거의 [반납, 월말). 7일 넘는 구간은 뺀다.
     10분 칸에 구간이 없고 대여도 없으면 '빈 칸'이다.
  2. 회차 창의 '있음' 비율 A로 대여 수를 나눈다(배율 1 / max(A, 0.2), 상한 5배). 늘어난 대여만 순수요에 더한다(C_L).
     민감도 C_U는 자리가 바뀐 쌍의 구간을 양쪽 대여소에 다 넣어 빈 시간을 낮게 잡는다(찍기만).
  3. 47장 하네스(plan_validity)로 현행과 보정의 진단을 같은 관측 재고 · 같은 자로 채점한다.

판정(사전 등록, scratchpad 노트 1_censor.md와 같다): 평일 3 · 5시간에서
  ① (배송 적중 − 같은 수 B1)이 현행보다 +1.0%p 이상, 회차 부호 검정 단측 p < 0.05
  ② 5시간 수거 무해(정정판, 최저 − q ≥ 1)가 현행 − 1.0%p 이상
  ③ 배송 대상 수 합이 현행의 1.20배 이하
  ①②③ → 채택 후보 · ① 아님 → 효과 없음. 채점 전에 신호(대상급 대여소의 변경 비율)가 3% 미만이면 멈춘다.

이 스크립트는 DB에 쓰지 않는다(읽기만).

실행:
    python experiments/structure/censored_demand.py --sanity   # 입력 점검만(순수요 재현 · 보정 크기 · 회차 수)
    python experiments/structure/censored_demand.py            # 신호 검사 → 채점 → 판정
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import project_config  # noqa: E402  (콘솔 인코딩을 먼저 맞춘다)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import db  # noqa: E402
import plan_validity as pv  # noqa: E402
import rebalance_trace as rt  # noqa: E402
import stockout_forecast as sf  # noqa: E402
from pipeline.step0_collect import raw_to_net  # noqa: E402
from project_config import (DATA_ROOT, DURATIONS, REBAL_MIN_QTY, STOCK_TICK_MINUTES, TICKS_PER_HOUR,  # noqa: E402
                            duration_hours, duration_start_hour, holiday_mask, select_day_type)

# 사전 등록 값(2026-11-21 13:24). 결과를 보고 바꾸지 않는다.
UNTIL = "2026-11-21 00:00"
MONTH, PREV = "26년 03월", "26년 02월"
M0, M1 = pd.Timestamp("2026-03-01"), pd.Timestamp("2026-04-01")
LONG_GAP = pd.Timedelta(days=rt.LONG_GAP_DAYS)
A_MIN = 0.2                       # 배율 상한 1 / 0.2 = 5배
SIGNAL_MIN = 0.03                 # 신호가 이보다 작으면 채점하지 않는다
GAP_MIN = 0.01                    # ① 배송 gap 개선 문턱
PICK_TOL = 0.01                   # ② 수거 무해 허용 하락
COUNT_MAX = 1.20                  # ③ 배송 대상 수 상한 배율
SIGN_P = 0.05
JUDGE_HORIZONS = (18, 30)         # 3 · 5시간
BIN = pd.Timedelta(minutes=STOCK_TICK_MINUTES)
NBINS = int((M1 - M0) / BIN)
NDAYS = (M1 - M0).days
ARMS = ("현행", "C_L", "C_U")
OUT_CSV = Path(__file__).resolve().parent / "censored_demand_261121.csv"


# ───────────────────────────────────────────── 관측 격자 (날짜 구간별로 읽는다)

def load_grid(conn, until: str):
    """재고이력을 (10분 칸 × 대여소) float32 격자로 편다.

    47장 `stockout_forecast.load_grid()`는 표 전체를 문자열 열째로 읽는다. 지금 1,078만 행이라 그대로 읽으면 2GB를 넘는다.
    7일씩 끊어 읽어 같은 격자(빈 칸은 NaN)를 만든다. 하루 틱 수(COUNT DISTINCT observed_at)로 47장과 같은 '30틱 이상' 날을 고른다.
    """
    lo, hi = conn.execute("SELECT MIN(observed_at), MAX(observed_at) FROM stock_history WHERE observed_at < ?",
                          (until,)).fetchone()
    stations = sorted(r[0] for r in conn.execute(
        "SELECT DISTINCT station_id FROM stock_history WHERE observed_at < ?", (until,)))
    col = {s: i for i, s in enumerate(stations)}
    start = pd.Timestamp(lo).floor("D")
    end = pd.Timestamp(hi).ceil("D")
    if end <= pd.Timestamp(hi):
        end += pd.Timedelta(days=1)
    index = pd.date_range(start, end, freq=BIN, inclusive="left")
    arr = np.full((len(index), len(stations)), np.nan, dtype=np.float32)
    cur = start
    while cur < end:
        nxt = min(cur + pd.Timedelta(days=7), end)
        part = pd.read_sql("SELECT observed_at, station_id, stock FROM stock_history "
                           "WHERE observed_at >= ? AND observed_at < ? AND observed_at < ?", conn,
                           params=(cur.strftime("%Y-%m-%d %H:%M"), nxt.strftime("%Y-%m-%d %H:%M"), until))
        if len(part):
            ts = pd.to_datetime(part["observed_at"], format="%Y-%m-%d %H:%M")
            rows = ((ts - start) // BIN).to_numpy(dtype=np.int64)
            cols = part["station_id"].map(col).to_numpy(dtype=np.int64)
            arr[rows, cols] = part["stock"].to_numpy(dtype=np.float32)
        cur = nxt
    grid = pd.DataFrame(arr, index=index, columns=stations)
    ticks = conn.execute("SELECT substr(observed_at, 1, 10), COUNT(DISTINCT observed_at) FROM stock_history "
                         "WHERE observed_at < ? GROUP BY 1", (until,)).fetchall()
    days = sorted(pd.Timestamp(d).date() for d, n in ticks if n >= sf.MIN_TICKS_PER_DAY)
    return grid, days


# ───────────────────────────────────────────── 하한 재고 · 빈 시간

def chain_intervals(df: pd.DataFrame, pairs: pd.DataFrame, upper: bool) -> pd.DataFrame:
    """자전거가 그 대여소에 있었다고 볼 구간 (대여소, 시작, 끝). 03월 안으로 자르고 7일 넘는 구간은 뺀다."""
    keep = (pairs["status"] == "ok") & (pairs["gap_h"] <= LONG_GAP / pd.Timedelta(hours=1))
    same = keep & pairs["same_place"]
    parts = [pd.DataFrame({"station": pairs.loc[same, "from_station"].to_numpy(),
                           "start": pairs.loc[same, "from_at"].to_numpy(),
                           "end": pairs.loc[same, "to_at"].to_numpy()})]
    if upper:
        moved = keep & ~pairs["same_place"]
        for side in ("from_station", "to_station"):
            parts.append(pd.DataFrame({"station": pairs.loc[moved, side].to_numpy(),
                                       "start": pairs.loc[moved, "from_at"].to_numpy(),
                                       "end": pairs.loc[moved, "to_at"].to_numpy()}))
    d = df.dropna(subset=["rent_at"]).sort_values(["bike_no", "rent_at"], kind="stable")
    first = d.groupby("bike_no", observed=True).head(1)
    first = first[(first["rent_at"] >= M0) & (first["rent_at"] - M0 <= LONG_GAP)]
    parts.append(pd.DataFrame({"station": first["rent_station"].astype(str).to_numpy(),
                               "start": M0, "end": first["rent_at"].to_numpy()}))
    last = d.groupby("bike_no", observed=True).tail(1).dropna(subset=["return_at"])
    last = last[(last["return_at"] >= M0) & (last["return_at"] < M1) & (M1 - last["return_at"] <= LONG_GAP)]
    parts.append(pd.DataFrame({"station": last["return_station"].astype(str).to_numpy(),
                               "start": last["return_at"].to_numpy(), "end": M1}))
    out = pd.concat(parts, ignore_index=True)
    out["start"] = pd.to_datetime(out["start"]).clip(lower=M0)
    out["end"] = pd.to_datetime(out["end"]).clip(upper=M1)
    return out[out["end"] > out["start"]].reset_index(drop=True)


def rental_cells(df: pd.DataFrame, stations: list) -> tuple:
    """03월 대여의 (대여소 번호, 날짜 번호, 시각, 10분 칸). 대여소는 순수요 표의 것만."""
    idx = {s: i for i, s in enumerate(stations)}
    r = df[(df["rent_at"] >= M0) & (df["rent_at"] < M1)]
    st = r["rent_station"].astype(str).map(idx)
    m = st.notna().to_numpy()
    t = r["rent_at"][m]
    return (st[m].astype(int).to_numpy(), (t - M0).dt.days.to_numpy(), t.dt.hour.to_numpy(),
            ((t - M0) // BIN).to_numpy(dtype=np.int64))


def availability(intervals: pd.DataFrame, stations: list, rent_bins: tuple) -> np.ndarray:
    """(대여소, 10분 칸) '있음'. 구간이 덮거나 그 칸에 대여가 있었으면 있음."""
    idx = {s: i for i, s in enumerate(stations)}
    st = intervals["station"].map(idx)
    m = st.notna().to_numpy()
    st = st[m].astype(int).to_numpy()
    sb = ((intervals["start"][m] - M0) // BIN).to_numpy(dtype=np.int64)
    eb = ((intervals["end"][m] - M0) // BIN).to_numpy(dtype=np.int64)
    diff = np.zeros((len(stations), NBINS + 1), dtype=np.int32)
    np.add.at(diff, (st, sb), 1)
    np.add.at(diff, (st, eb), -1)
    avail = np.cumsum(diff, axis=1)[:, :NBINS] > 0
    avail[rent_bins[0], rent_bins[3]] = True
    return avail


def window_share(avail: np.ndarray) -> np.ndarray:
    """(대여소, 날짜, 시각) → 그 시각이 속한 회차 창의 '있음' 비율 A."""
    hourly = avail.reshape(avail.shape[0], NDAYS, 24, TICKS_PER_HOUR).mean(axis=3)
    out = np.empty_like(hourly)
    for dur in DURATIONS:
        hours = duration_hours(dur)
        out[:, :, hours] = hourly[:, :, hours].mean(axis=2, keepdims=True)
    return out


def extra_rentals(share: np.ndarray, rent: tuple, stations: list) -> pd.DataFrame:
    """대여 × (1 / max(A, A_MIN) − 1). (날짜, station_id, net_00 ~ net_23) 표. 대여가 없던 칸은 0이다."""
    counts = np.zeros(share.shape, dtype=np.float64)
    np.add.at(counts, (rent[0], rent[1], rent[2]), 1.0)
    extra = counts * (1.0 / np.maximum(share, A_MIN) - 1.0)
    st_i, day_i = np.nonzero(extra.sum(axis=2) > 0)
    frame = pd.DataFrame(extra[st_i, day_i, :], columns=[f"net_{h:02d}" for h in range(24)])
    frame.insert(0, "station_id", np.asarray(stations, dtype=object)[st_i])
    frame.insert(0, "날짜", (M0 + pd.to_timedelta(day_i, unit="D")).strftime("%Y-%m-%d"))
    return frame, counts


def add_extra(net: pd.DataFrame, extra: pd.DataFrame) -> pd.DataFrame:
    """현행 순수요에 추가 대여만 더한다. 행은 그대로(현행에 없는 (날짜, 대여소)는 만들지 않는다)."""
    cols = [f"net_{h:02d}" for h in range(24)]
    out = net.copy()
    key = pd.MultiIndex.from_arrays([pd.to_datetime(out["날짜"]).dt.strftime("%Y-%m-%d"), out["station_id"]])
    add = extra.set_index(["날짜", "station_id"])[cols].reindex(key).fillna(0.0).to_numpy()
    out[cols] = out[cols].to_numpy(dtype=float) + add
    missing = len(extra) - int(extra.set_index(["날짜", "station_id"]).index.isin(key).sum())
    if missing:
        print(f"  [!] 추가 대여 {missing}행이 현행 순수요에 없는 (날짜, 대여소)라 버렸다")
    return out


# ───────────────────────────────────────────── 채점

def instances(grid: pd.DataFrame, days: list, refs: dict) -> list:
    """(날짜, 회차, 요일, 격자 행, t의 관측 재고). 47장과 같은 규칙."""
    hol = dict(zip(days, holiday_mask(pd.Series(pd.to_datetime(days)))))
    index = {ts: i for i, ts in enumerate(grid.index)}
    out = []
    for day in days:
        dt = "holiday" if hol[day] else "weekday"
        for dur in DURATIONS:
            if dur not in refs[dt]:
                continue
            t = pd.Timestamp(day) + pd.Timedelta(hours=duration_start_hour(dur))
            if t not in index:
                continue
            s0 = grid.iloc[index[t]].dropna()
            if len(s0) < 0.5 * grid.shape[1]:
                continue
            out.append((day, dur, dt, index[t], s0))
    return out


def signal(rebals: dict) -> tuple:
    """작업 대상급(|rebal| > 2)인 대여소 가운데 대상 여부가 다르거나 rebal이 1대 이상 다른 곳 (바뀐 곳, 합집합)."""
    a, b = rebals["현행"], rebals["C_L"].reindex(rebals["현행"].index)
    ta, tb = a.abs() > REBAL_MIN_QTY, b.abs() > REBAL_MIN_QTY
    union = ta | tb
    changed = union & ((ta != tb) | ((a - b).abs() >= 1))
    return int(changed.sum()), int(union.sum())


def summarize(rows: pd.DataFrame, dt: str, verdict: bool) -> dict:
    """갈래 × 지평의 합친 비율 · 회차 부호 검정."""
    print(f"\n## {pv.DAY_TYPES[dt]} (회차 {rows['회차키'].nunique()}개)" + ("" if verdict else " (찍기만, 판정 아님)"))
    out = {}
    for h in pv.HORIZONS:
        g = rows[rows["지평"] == h]
        print(f"\n### {h // TICKS_PER_HOUR}시간 뒤 ({h}틱)")
        print("  갈래 | 배송 적중 · 같은 수 B1 · gap | B0 | 수거 무해(정정 0 = 빔) · 47장식 | 배송 대상 · 수거 대상 · 수거 대수")
        per = {}
        for arm in ARMS:
            x = g[g["갈래"] == arm]
            nd = x["배송수"].sum()
            hit, b1 = pv.rate(x["배송적중"].sum(), nd), pv.rate(x["B1배송적중"].sum(), nd)
            safe0 = pv.rate(x["수거무해0"].sum(), x["수거수"].sum())
            safe = pv.rate(x["수거무해"].sum(), x["수거수"].sum())
            b0 = pv.rate(x["B0적중"].sum(), x["B0풀"].sum())
            per[arm] = dict(gap=hit - b1, safe0=safe0, nd=nd, x=x.set_index("회차키"))
            print(f"  {arm:<4} | {hit:.1%} · {b1:.1%} · {(hit - b1) * 100:+.1f}%p | {b0:.1%} | {safe0:.1%} · {safe:.1%}"
                  f" | {int(nd):,} · {int(x['수거수'].sum()):,} · {x['수거대수'].sum():,.0f}")
        base = per["현행"]["x"]
        for arm in ("C_L", "C_U"):
            other = per[arm]["x"].reindex(base.index)
            both = (base["배송수"] > 0) & (other["배송수"] > 0)
            ga = (base.loc[both, "배송적중"] - base.loc[both, "B1배송적중"]) / base.loc[both, "배송수"]
            gb = (other.loc[both, "배송적중"] - other.loc[both, "B1배송적중"]) / other.loc[both, "배송수"]
            wins, losses = int((gb > ga).sum()), int((gb < ga).sum())
            p = pv.sign_test(wins, losses)
            dgap = per[arm]["gap"] - per["현행"]["gap"]
            ratio = per[arm]["nd"] / per["현행"]["nd"] if per["현행"]["nd"] else float("nan")
            dsafe = per[arm]["safe0"] - per["현행"]["safe0"]
            print(f"  {arm} − 현행: gap {dgap * 100:+.2f}%p · 회차 부호 검정 {wins}승 {losses}패 {int(both.sum()) - wins - losses}무"
                  f" (단측 p = {p:.4f}) · 수거 무해 {dsafe * 100:+.2f}%p · 배송 대상 {ratio:.3f}배")
            out[(arm, h)] = dict(dgap=dgap, p=p, dsafe=dsafe, ratio=ratio)
        cells = []
        for dur in sorted(g["회차"].unique()):
            gaps = [dur_gap(g[(g["회차"] == dur) & (g["갈래"] == arm)]) for arm in ARMS]
            cells.append(f"{dur} " + " / ".join(f"{v * 100:+.1f}" for v in gaps))
        print("  회차별 gap %p (현행 / C_L / C_U): " + " · ".join(cells))
    return out


def dur_gap(x: pd.DataFrame) -> float:
    """합친 (배송 적중 − 같은 수 B1) / 배송 수."""
    return pv.rate(x["배송적중"].sum() - x["B1배송적중"].sum(), x["배송수"].sum())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sanity", action="store_true", help="입력 점검만 한다(신호 · 채점 없음)")
    args = parser.parse_args(argv)
    started = time.time()

    with db.session() as conn:
        # 1. 현행 순수요(DB, 1.26.409 정의)와 대여 사슬
        net_all, _ = db.read_step_output("net_demand", str(DATA_ROOT / f"pp_data/순수요/st_net_daily ({MONTH}).csv"),
                                         period=MONTH)
        net_all = net_all.rename(columns={"date": "날짜"})
        stations = sorted(net_all["station_id"].unique())
        print(f"현행 순수요 {MONTH}: {len(net_all):,}행 · {net_all['날짜'].nunique()}일 · {len(stations):,}곳")
        df, _ = rt.load_rentals(conn, [PREV, MONTH])
        pairs = rt.build_pairs(df)
        print(f"대여 사슬: {len(df):,}건({PREV} · {MONTH}) · 쌍 {len(pairs):,} · 같은 자리 쌍 "
              f"{(pairs['same_place'] & (pairs['status'] == 'ok')).mean():.1%}")

        rent = rental_cells(df, stations)
        arms_net = {"현행": net_all}
        for arm, upper in (("C_L", False), ("C_U", True)):
            iv = chain_intervals(df, pairs, upper=upper)
            avail = availability(iv, stations, rent)
            share = window_share(avail)
            extra, counts = extra_rentals(share, rent, stations)
            arms_net[arm] = add_extra(net_all, extra)
            has = counts > 0
            factor = 1.0 / np.maximum(share, A_MIN)
            wk = np.asarray(~holiday_mask(pd.Series(pd.date_range(M0, M1, inclusive="left"))), dtype=bool)
            print(f"  {arm}: 구간 {len(iv):,}개 · 빈 칸 비율 {1 - avail.mean():.1%} · 대여가 있는 (대여소, 날짜, 시각) 중 A < 1 "
                  f"{(share[has] < 1).mean():.1%} · 평균 배율 {factor[has].mean():.2f} · 5배(상한) {(share[has] <= A_MIN).mean():.1%} · "
                  f"추가 대여 {extra.iloc[:, 2:].to_numpy().sum():,.0f}건 / 대여 {counts.sum():,.0f}건 "
                  f"({extra.iloc[:, 2:].to_numpy().sum() / counts.sum():.1%}) · 평일만 "
                  f"{(counts[:, wk, :] * (factor[:, wk, :] - 1)).sum() / counts[:, wk, :].sum():.1%}")
        in_empty = rental_in_uncovered(chain_intervals(df, pairs, upper=False), stations, rent)
        print(f"  구간 밖(하한 재고 0) 칸에서 난 대여 {in_empty:.1%} (자리가 바뀐 자전거 · 구간을 못 잡은 대여의 몫)")

        if args.sanity:
            data, src = db.read_rental_source(MONTH, columns=["대여일시", "대여_대여소ID", "반납일시", "반납_대여소ID"])
            again = pv.quiet(raw_to_net.net_from_rentals, data)
            cols = [f"net_{h:02d}" for h in range(24)]
            k1 = pd.to_datetime(again["날짜"]).dt.strftime("%Y-%m-%d") + "|" + again["station_id"]
            k2 = pd.to_datetime(net_all["날짜"]).dt.strftime("%Y-%m-%d") + "|" + net_all["station_id"]
            a = again.set_index(k1)[cols].sort_index()
            b = net_all.set_index(k2)[cols].sort_index()
            same = a.index.equals(b.index) and np.array_equal(a.to_numpy(dtype=float), b.to_numpy(dtype=float))
            print(f"  [점검] DB 순수요 = 대여이력에서 지금 코드로 다시 낸 순수요: {'같다' if same else '다르다'} "
                  f"({len(a):,} · {len(b):,}행, 출처 {src}) · 대여 칸 합 {len(rent[0]):,} = 재계산 대여 {len(data):,}건 중 03월 대여")

        # 2. 참조 계획 · 관측
        refs = {}
        for dt in pv.DAY_TYPES:
            base = pv.load_refs(conn, dt, before=UNTIL)
            refs[dt] = {arm: {d: pv.current_stats(frame, select_day_type(arms_net[arm], "날짜", dt), d)
                              for d, (frame, _lab) in base.items()} for arm in ARMS}
            print(f"참조 계획({pv.DAY_TYPES[dt]}): " + " · ".join(f"{d} ← '{lab}'" for d, (_f, lab) in sorted(base.items())))
        grid, days = load_grid(conn, UNTIL)
    print(f"관측 격자 {grid.shape[0]:,}칸 × {grid.shape[1]:,}곳 ({grid.index.min()} ~ {grid.index.max()}) · 30틱 이상인 날 {len(days)}일"
          f" · 읽기 {time.time() - started:.0f}초")
    todo = instances(grid, days, {dt: refs[dt]["현행"] for dt in refs})
    print(f"회차: 평일 {sum(x[2] == 'weekday' for x in todo)}개 · 휴일 {sum(x[2] == 'holiday' for x in todo)}개")
    if args.sanity:
        print("--sanity: 신호 · 채점은 계산하지 않았다")
        return 0

    # 3. 신호 검사(채점 전, 멈춤 규칙)
    rebals_all = []
    changed = union = 0
    per_dur = {}
    for day, dur, dt, i, s0 in todo:
        rebals = {arm: pv.replan(refs[dt][arm][dur], s0) for arm in ARMS}
        rebals_all.append(rebals)
        if dt == "weekday":
            c, u = signal(rebals)
            changed, union = changed + c, union + u
            pc, pu = per_dur.get(dur, (0, 0))
            per_dur[dur] = (pc + c, pu + u)
    share_changed = changed / union if union else 0.0
    print(f"\n## 신호 검사 (평일): 대상급 대여소 {union:,}곳(회차 합) 중 바뀐 곳 {changed:,} = {share_changed:.1%}"
          f" (멈춤 문턱 {SIGNAL_MIN:.0%}) · 회차별 " + " · ".join(f"{d} {c / u:.1%}" for d, (c, u) in sorted(per_dur.items()) if u))
    if share_changed < SIGNAL_MIN:
        print("→ 신호가 문턱보다 작다. 사전 등록대로 채점하지 않고 '효과 없음'으로 멈춘다.")
        return 0

    # 4. 채점
    rows = []
    for k, ((day, dur, dt, i, s0), rebals) in enumerate(zip(todo, rebals_all)):
        for h in pv.HORIZONS:
            ws = pv.window_stats(grid, i, h)
            if ws is None:
                continue
            for arm in ARMS:
                rng = np.random.default_rng(pv.SEED + 1000 * k + h)
                ref = refs[dt][arm][dur]
                row = pv.score_instance(rebals[arm], s0, ref["parking_lot"], ws, rng, ref["mu"])
                row.update({"요일": dt, "회차": dur, "지평": h, "회차키": f"{day} {dur}", "갈래": arm})
                rows.append(row)
        if (k + 1) % 50 == 0:
            print(f"  {k + 1}/{len(todo)} 회차 채점 ({time.time() - started:.0f}초)", flush=True)
    frame = pd.DataFrame(rows)
    frame.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    res = summarize(frame[frame["요일"] == "weekday"], "weekday", verdict=True)
    if (frame["요일"] == "holiday").any():
        summarize(frame[frame["요일"] == "holiday"], "holiday", verdict=False)

    print("\n## 판정 (사전 등록: 평일 · 3 · 5시간 · C_L 대 현행)")
    one = all(res[("C_L", h)]["dgap"] >= GAP_MIN and res[("C_L", h)]["p"] < SIGN_P for h in JUDGE_HORIZONS)
    two = res[("C_L", 30)]["dsafe"] >= -PICK_TOL
    three = all(res[("C_L", h)]["ratio"] <= COUNT_MAX for h in JUDGE_HORIZONS)
    for h in JUDGE_HORIZONS:
        r = res[("C_L", h)]
        print(f"  {h // TICKS_PER_HOUR}시간: gap {r['dgap'] * 100:+.2f}%p (기준 +{GAP_MIN * 100:.1f}) · p = {r['p']:.4f} · 대상 {r['ratio']:.3f}배")
    print(f"  ① 배송 {'✅' if one else '❌'} · ② 수거 {'✅' if two else '❌'} ({res[('C_L', 30)]['dsafe'] * 100:+.2f}%p) · ③ 작업량 {'✅' if three else '❌'}")
    name = ("채택 후보" if one and two and three else
            "진단은 낫지만 작업량이 는다(보류)" if one and two else
            "배송은 낫지만 수거가 나빠진다(보류)" if one and three else
            "배송은 낫지만 수거 · 작업량 둘 다 대가(보류)" if one else "효과 없음(기각)")
    print(f"  → {name}")
    print(f"걸린 시간 {time.time() - started:.0f}초 · 행 {OUT_CSV}")
    return 0


def rental_in_uncovered(intervals: pd.DataFrame, stations: list, rent: tuple) -> float:
    """구간이 하나도 덮지 않은 칸(대여 칸 규칙을 넣기 전)에서 난 대여의 비율."""
    idx = {s: i for i, s in enumerate(stations)}
    st = intervals["station"].map(idx)
    m = st.notna().to_numpy()
    st = st[m].astype(int).to_numpy()
    sb = ((intervals["start"][m] - M0) // BIN).to_numpy(dtype=np.int64)
    eb = ((intervals["end"][m] - M0) // BIN).to_numpy(dtype=np.int64)
    diff = np.zeros((len(stations), NBINS + 1), dtype=np.int32)
    np.add.at(diff, (st, sb), 1)
    np.add.at(diff, (st, eb), -1)
    covered = np.cumsum(diff, axis=1)[:, :NBINS] > 0
    return float((~covered[rent[0], rent[3]]).mean())


if __name__ == "__main__":
    project_config.exit_if_help(__doc__)
    raise SystemExit(main())
