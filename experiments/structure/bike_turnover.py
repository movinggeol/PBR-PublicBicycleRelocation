"""채운 자전거는 3시간 안에 대여되는가 — 대여소 · 시각별 **3시간 대여 확률** (EXPERIMENTS 44장 2부 · ML 12번, 1.26.312).

왜 — 지금 KPI는 *"결품 시간이 줄었나"* 만 본다. 10대를 채웠는데 아무도 안 타면 결품은 줄어도 쓸모가 없다
(ML_후보_10 2번). 이 스크립트는 *"이 대여소에 이 시각에 놓인 자전거가 3시간 안에 대여될 확률"* 을 맞히고,
그것으로 계획과 공사(1부가 복원한 실제 이동)의 배송을 같은 자로 잰다.

🔴 **옮겨진 자전거로 학습하지 않는다.** 옮겨진 자전거는 정의상 *다시 대여된 것만* 보이고 도착 시각을 모른다.
도착 시각이 정확한 **이용자 반납**으로 학습한다.

사전 등록 (EXPERIMENTS 44장 — 결과 전 커밋. 바꾸지 않는다)
  라벨     : 다음 대여가 같은 자리(같은 번호 또는 50m 안)에서 반납 뒤 3시간 안이면 1 · 그 밖은 0 ·
             다른 곳에서 3시간 안(3시간 안에 옮겨짐)은 판정 불가로 뺀다
  분할     : 반납 시각 — 학습 24-08 ~ 25-08 · 시험 25-09 ~ 26-03 (25-12 없음 → 6개월). 평일 · 휴일 따로
  베이스   : ⓪ 학습 평균 · ① 대여소 × 시 × 요일 구분 과거 빈도(표본 20 미만이면 대여소 × 요일 구분 → 평균)
  ML 채택  : 시험 6개월 × 평일 · 휴일 = 12칸 전부에서 GBM Brier < ① + 보정(행 1,000 이상 구간에서 |예측 − 실제| ≤ 0.05)
  못 넘으면: ML 기각, 지표는 ①로 낸다

코드로 옮기며 정한 것 (첫 실측 전 — 44장 *해석* 표에 같은 내용)
  · 대여소 비율 피처는 학습 행에서 **자기 달을 뺀** 값(달 단위 leave-one-out)이다 — 자기 라벨이 섞인 비율을
    피처로 주면 작은 대여소에서 과적합한다. 시험 행은 학습 기간 전체의 값.
  · 빠진 달 · 자료 끝을 건너는 반납: 다음 대여가 없거나 다른 연속 구간이면 '반납 + 3시간'이 구간 끝 안일 때만
    0으로 두고, 넘으면 뺀다(그 3시간에 무슨 일이 있었는지 자료에 없다).

실행:
    python experiments/structure/bike_turnover.py
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import project_config  # noqa: E402,F401  — 콘솔 인코딩을 먼저 맞춘다(— · 이모지)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import db  # noqa: E402
import rebalance_trace as rt  # noqa: E402
import weather  # noqa: E402
from project_config import holiday_mask  # noqa: E402

# ── 사전 등록 (EXPERIMENTS 44장)
HORIZON_H = 3
TRAIN_END = pd.Timestamp("2025-09-01")      # 이 앞이 학습(24-08 ~ 25-08)
MIN_CELL = 20                               # ① 빈도표의 칸이 이보다 작으면 물러난다
CALIB_MIN_ROWS = 1000
CALIB_TOL = 0.05
MAINTENANCE = "ST0001"                      # 정비대기 반납은 이용자 반납이 아니다
GBM_PARAMS = dict(max_iter=200, learning_rate=0.1, max_depth=6, random_state=0)

WEATHER_COLS = ["temp", "rain", "wind", "humid", "snow"]
FEATURES = ["시", "요일", "달", "대여소비율", "대여소시비율", "대여소일평균대여", *WEATHER_COLS]
ROUND_START_H = {"_05_10": 5, "_10_15": 10, "_15_20": 15, "_20_05": 20}


# ───────────────────────────────────────────── 표본 · 라벨

def build_returns(df: pd.DataFrame, block_end: dict) -> tuple:
    """이용자 반납 한 건 = 한 행. (라벨 붙은 표, 뺀 행 수 dict)"""
    d = df.sort_values(["bike_no", "rent_at"], kind="stable").reset_index(drop=True)
    nxt = d.shift(-1)
    same_bike = d["bike_no"].eq(d["bike_no"].shift(-1)).to_numpy()
    out = pd.DataFrame({
        "station_id": d["return_station"].astype(str),
        "t": d["return_at"],
        "lat": d["return_lat"].to_numpy(dtype=float), "lon": d["return_lon"].to_numpy(dtype=float),
        "block": d["block"].to_numpy(),
    })
    out["next_station"] = np.where(same_bike, nxt["rent_station"].astype(str), None)
    out["next_at"] = pd.to_datetime(nxt["rent_at"]).where(same_bike)
    out["next_block"] = nxt["block"].where(same_bike)
    nlat = nxt["rent_lat"].to_numpy(dtype=float)
    nlon = nxt["rent_lon"].to_numpy(dtype=float)

    dropped = {}
    keep = out["station_id"] != MAINTENANCE
    dropped["정비대기 반납"] = int((~keep).sum())

    dt_h = (out["next_at"] - out["t"]).dt.total_seconds() / 3600
    has_next = out["next_at"].notna() & (out["next_block"] == out["block"])
    reversed_ = has_next & (dt_h < 0)
    dropped["시각 역전"] = int((keep & reversed_).sum())
    keep &= ~reversed_

    same_place = (out["next_station"] == out["station_id"]) | (
        rt.haversine_m(out["lat"], out["lon"], nlat, nlon) < rt.SAME_PLACE_M)
    within = dt_h <= HORIZON_H
    y = np.where(has_next & same_place & within, 1, 0)
    ambiguous = has_next & ~same_place & within
    dropped["3시간 안에 옮겨짐(판정 불가)"] = int((keep & ambiguous).sum())
    keep &= ~ambiguous

    end = out["block"].map(block_end)
    censored = ~has_next & (out["t"] + pd.Timedelta(hours=HORIZON_H) > end)
    dropped["구간 끝에 걸림"] = int((keep & censored).sum())
    keep &= ~censored

    res = out.loc[keep, ["station_id", "t"]].copy()
    res["y"] = y[keep.to_numpy()].astype(np.int8)
    # 같은 자리 다음 대여까지 분 — 45장이 '곧바로 다시 빌림'(10분 안)을 곁에서 가를 때 쓴다. 라벨에는 쓰지 않는다
    same_min = (dt_h * 60).where(has_next & same_place)
    res["next_same_min"] = same_min[keep].to_numpy()
    res["holiday"] = np.asarray(holiday_mask(res["t"]), dtype=bool)
    res["시"] = res["t"].dt.hour.astype(np.int8)
    res["요일"] = res["t"].dt.dayofweek.astype(np.int8)
    res["달"] = res["t"].dt.month.astype(np.int8)
    res["ym"] = res["t"].dt.to_period("M")
    res["train"] = res["t"] < TRAIN_END
    return res, dropped


# ───────────────────────────────────────────── 피처 · 베이스라인

def loo_rate(train: pd.DataFrame, rows: pd.DataFrame, keys: list, is_train: bool) -> pd.Series:
    """키별 3시간 대여 비율. 학습 행이면 자기 달을 뺀 값, 시험 행이면 학습 전체. 분모 20 미만은 NaN."""
    total = train.groupby(keys)["y"].agg(["sum", "count"])
    joined = rows[keys].join(total, on=keys)
    s, n = joined["sum"].fillna(0), joined["count"].fillna(0)
    if is_train:
        by_month = train.groupby(keys + ["ym"])["y"].agg(["sum", "count"])
        own = rows[keys + ["ym"]].join(by_month, on=keys + ["ym"])
        s, n = s - own["sum"].fillna(0), n - own["count"].fillna(0)
    rate = s / n.where(n >= MIN_CELL)
    return rate.astype(float)


def add_features(train: pd.DataFrame, rows: pd.DataFrame, is_train: bool, daily: pd.Series,
                 hourly_weather: pd.DataFrame) -> pd.DataFrame:
    rows = rows.copy()
    rows["대여소비율"] = loo_rate(train, rows, ["station_id"], is_train).to_numpy()
    rows["대여소시비율"] = loo_rate(train, rows, ["station_id", "시"], is_train).to_numpy()
    rows["대여소일평균대여"] = rows["station_id"].map(daily).astype(float)
    hour = rows["t"].dt.floor("h")
    w = hourly_weather.reindex(hour.to_numpy())
    for col in WEATHER_COLS:
        rows[col] = w[col].to_numpy(dtype=float)
    return rows


def baseline_table(train: pd.DataFrame) -> dict:
    """① 빈도표 — 대여소 × 시 → 대여소 → 전체 순으로 물러난다."""
    sh = train.groupby(["station_id", "시"])["y"].agg(["mean", "count"])
    st = train.groupby("station_id")["y"].agg(["mean", "count"])
    return {"sh": sh[sh["count"] >= MIN_CELL]["mean"], "st": st[st["count"] >= MIN_CELL]["mean"],
            "all": float(train["y"].mean())}


def baseline_predict(table: dict, station: pd.Series, hour: pd.Series) -> np.ndarray:
    idx = pd.MultiIndex.from_arrays([station.to_numpy(), hour.to_numpy()])
    p = pd.Series(table["sh"].reindex(idx).to_numpy(), index=station.index)
    p = p.fillna(station.map(table["st"]))
    return p.fillna(table["all"]).to_numpy(dtype=float)


def daily_rentals(df: pd.DataFrame, holiday: bool) -> pd.Series:
    """학습 기간 대여소의 그 요일 구분 하루 평균 대여 — 이용 규모 피처."""
    r = df[["rent_station", "rent_at"]].dropna()
    r = r[r["rent_at"] < TRAIN_END]
    flag = np.asarray(holiday_mask(r["rent_at"]), dtype=bool) == holiday
    r = r[flag]
    days = r["rent_at"].dt.normalize().nunique()
    return r.groupby(r["rent_station"].astype(str), observed=True).size() / max(days, 1)


# ───────────────────────────────────────────── 평가

def brier(p, y) -> float:
    return float(np.mean((np.asarray(p, dtype=float) - np.asarray(y, dtype=float)) ** 2))


def calibration_gap(p: np.ndarray, y: np.ndarray) -> tuple:
    """행 1,000 이상인 10구간 중 |예측 평균 − 실제|의 최댓값과 그 구간 수."""
    bins = np.clip((p * 10).astype(int), 0, 9)
    worst, used = 0.0, 0
    for b in range(10):
        m = bins == b
        if m.sum() >= CALIB_MIN_ROWS:
            used += 1
            worst = max(worst, abs(float(p[m].mean()) - float(y[m].mean())))
    return worst, used


def predict_test(df: pd.DataFrame, returns: pd.DataFrame, hourly_weather: pd.DataFrame):
    """요일 구분마다 학습하고 시험 행에 세 예측(p_gbm · p_base · p_mean)을 붙여 내놓는다.

    (holiday, 학습 피처 표, 시험 표, ① 빈도표)를 차례로 돌려준다 — 45장(수준 보정)도 이 함수를 그대로 부른다.
    """
    from sklearn.ensemble import HistGradientBoostingClassifier

    for holiday in (False, True):
        name = "휴일" if holiday else "평일"
        part = returns[returns["holiday"] == holiday]
        train, test = part[part["train"]], part[~part["train"]]
        daily = daily_rentals(df, holiday)
        tr = add_features(train, train, True, daily, hourly_weather)
        te = add_features(train, test, False, daily, hourly_weather)
        table = baseline_table(train)
        print(f"\n## {name} — 학습 {len(tr):,}행(양성 {tr['y'].mean():.3f}) · 시험 {len(te):,}행(양성 {te['y'].mean():.3f})")

        model = HistGradientBoostingClassifier(**GBM_PARAMS)
        model.fit(tr[FEATURES], tr["y"])
        te = te.assign(p_gbm=model.predict_proba(te[FEATURES])[:, 1],
                       p_base=baseline_predict(table, te["station_id"], te["시"]),
                       p_mean=float(train["y"].mean()))
        yield holiday, tr, te, table


def fit_and_evaluate(df: pd.DataFrame, returns: pd.DataFrame, hourly_weather: pd.DataFrame) -> tuple:
    rows, tables = [], {}
    for holiday, tr, te, table in predict_test(df, returns, hourly_weather):
        name = "휴일" if holiday else "평일"
        tables[holiday] = table
        for ym, g in te.groupby("ym"):
            y = g["y"].to_numpy()
            b_gbm, b_base, b_mean = brier(g["p_gbm"], y), brier(g["p_base"], y), brier(g["p_mean"], y)
            worst, used = calibration_gap(g["p_gbm"].to_numpy(), y)
            rows.append({"요일": name, "달": str(ym), "행": len(g), "양성": y.mean(),
                         "⓪ 평균": b_mean, "① 빈도표": b_base, "GBM": b_gbm,
                         "GBM/① −1": b_gbm / b_base - 1, "Brier 이김": b_gbm < b_base,
                         "보정 최대오차": worst, "보정 구간": used, "보정 통과": worst <= CALIB_TOL})
        leak_check(tr, te, holiday)
    return pd.DataFrame(rows), tables, None


def leak_check(tr: pd.DataFrame, te: pd.DataFrame, holiday: bool) -> None:
    """라벨을 섞어 학습하면 '늘 학습 평균'과 같아야 한다 — 30만 행 표본 (점검, 문턱 아님)."""
    from sklearn.ensemble import HistGradientBoostingClassifier

    sample = tr.sample(min(len(tr), 300_000), random_state=0)
    shuffled = sample["y"].sample(frac=1.0, random_state=1).to_numpy()
    m = HistGradientBoostingClassifier(**GBM_PARAMS).fit(sample[FEATURES], shuffled)
    y = te["y"].to_numpy()
    print(f"  누출 점검: 라벨 섞은 학습 Brier {brier(m.predict_proba(te[FEATURES])[:, 1], y):.4f} · "
          f"늘 학습 평균 {brier(np.full(len(y), tr['y'].mean()), y):.4f}")


# ───────────────────────────────────────────── 지표

def station_rate(table: dict, stations: pd.Series) -> np.ndarray:
    return stations.map(table["st"]).fillna(table["all"]).to_numpy(dtype=float)


def report_kpi(conn, df: pd.DataFrame, tables: dict) -> None:
    weekday = tables[False]
    print(f"\n## 지표 — 채운 자전거의 기대 3시간 대여 (① 빈도표 · 평일)")
    plan = db.load_frame(conn, "pick_drop", run_label=rt.PLAN_LABEL)
    if plan.empty:
        print(f"  ⚠️ `{rt.PLAN_LABEL}` pick_drop이 이 DB에 없다 — 지표를 건너뛴다")
        return
    drops = plan[(plan["rebal_qty"] > 0) & ~plan["station_id"].isin(rt.CENTER_STATIONS)].copy()
    print(f"  (가) 정본 계획 `{rt.PLAN_LABEL}` — 회차 시작 시각의 P")
    for duration, g in drops.groupby("duration"):
        hour = pd.Series(ROUND_START_H.get(duration, 0), index=g.index)
        p = baseline_predict(weekday, g["station_id"], hour)
        qty = g["rebal_qty"].to_numpy(dtype=float)
        print(f"    {duration}: 배송 {qty.sum():.0f}대 · {len(g)}곳 → 3시간 안 대여 기대 {np.dot(qty, p):.1f}대 "
              f"(대당 {np.dot(qty, p) / qty.sum():.3f})")

    print(f"  (나) 공사 대 계획 — 대여소 × 평일의 하루 평균 P (시 무관), {rt.PLAN_PERIOD} 평일")
    pairs = rt.build_pairs(df)
    moves = rt.classify_moves(pairs)
    target = pd.Period(pd.Timestamp(2025, 11, 1), "M")
    op = moves[moves["kind"].isin(["대여소 간", "센터 출고"]) & ~moves["holiday"] & (moves["to_month"] == target)
               & ~moves["to_station"].isin(rt.CENTER_STATIONS)]
    p_op = station_rate(weekday, op["to_station"])
    qty = drops["rebal_qty"].to_numpy(dtype=float)
    p_plan = station_rate(weekday, drops["station_id"])
    all_st = pd.Series(weekday["st"].index)
    print(f"    공사 배송 {len(op):,}대 · 대당 {p_op.mean():.3f}")
    print(f"    계획 배송 {qty.sum():.0f}대 · 대당 {np.dot(qty, p_plan) / qty.sum():.3f}")
    print(f"    (참고) 대여소 고르게 하나씩: {station_rate(weekday, all_st).mean():.3f}")

    test_moves = moves[(moves["kind"] == "대여소 간") & (moves["to_at"] >= TRAIN_END)]
    print(f"\n## 참고 표 (판정에 쓰지 않는다) — 시험 기간 대여소 간 이동 {len(test_moves):,}건 중 "
          f"구간 ≤ {HORIZON_H}시간 {(test_moves['gap_h'] <= HORIZON_H).mean():.1%} — '다시 대여된 이동' 안의 하한일 뿐이다")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    with db.session() as conn:
        df, ends = rt.load_rentals(conn)
        print(f"대여이력 {len(df):,}건 · {df['period'].nunique()}개월 · 연속 구간 {df['block'].nunique()}개")
        returns, dropped = build_returns(df, ends)
        print(f"표본(이용자 반납) {len(returns):,}행 · 양성 {returns['y'].mean():.3f}")
        for k, v in dropped.items():
            print(f"  뺀 행 — {k}: {v:,}")
        hourly = weather.load_hourly().set_index("time")[WEATHER_COLS]
        hourly = hourly[~hourly.index.duplicated()]
        table, tables, _ = fit_and_evaluate(df, returns, hourly)

        print("\n## 시험 6개월 × 요일 구분 — Brier (낮을수록 좋다)")
        with pd.option_context("display.width", 200):
            print(table.round(4).to_string(index=False))
        wins, calib = int(table["Brier 이김"].sum()), int(table["보정 통과"].sum())
        passed = bool(table["Brier 이김"].all() and table["보정 통과"].all())
        print(f"\n  Brier 이김 {wins}/{len(table)} · 보정 통과 {calib}/{len(table)} → "
              f"{'✅ ML 채택 조건 통과' if passed else '❌ ML 채택 조건 미달 — 지표는 ① 빈도표로 낸다'}")
        report_kpi(conn, df, tables)


if __name__ == "__main__":
    project_config.exit_if_help(__doc__)
    main()
