"""다음 작업 대상 예측 — **k틱 뒤 계획에서 수거 · 배송 대상급이 될 확률** (EXPERIMENTS 46장 · ML 14번, 1.26.317).

사용자 물음: *"다음 작업 예정이 될 가능성이 높은 대여소를 예측할 수 있나? ~시간 안에 pick/drop 할 가능성 ~%인 대여소"*.

계획의 작업 대상은 회차 시작 시각의 `rebal_qty = target_qty − stock`이 `REBAL_MIN_QTY`(2)를 넘는 곳이다. 목표 재고는
달 통계로 미리 정해지므로 물음은 *"k틱 뒤 재고가 그 회차 목표보다 2대 넘게 많을까(수거) · 적을까(배송)"* 가 된다.
9번(빔, `stockout_forecast.py`) · 11번(포화, `saturation_forecast.py`)과 같은 꼴이고, 문턱만 대여소마다 다르다.

사전 등록 (EXPERIMENTS 46장 — 결과 전 커밋. 바꾸지 않는다)
  자료     : stock_history 관측(하루 30틱 이상인 날) — 평일만
  목표     : 회차마다 가장 최근 평일 계획 실행(kind = plan, 운영 기간)의 rebalance_plan.target_qty
  라벨     : τ = t + k틱의 회차 목표로 rebal = target − stock(τ). 수거 = rebal < −2 · 배송 = rebal > 2
  지평     : 6틱(1시간) · 18틱(3시간) · 뒤 4일 검증
  베이스   : ① 지속(지금 재고로 τ의 목표와 견줌) · ② 과거 빈도(대여소 × 시) · ③ 순수요 누적(지금 재고 − k틱 평균 순유출)
  문턱     : 두 라벨 × 두 지평 = 4조합 모두에서 세 베이스라인을 전부 Brier로 이길 것

코드로 옮기며 정한 것 (첫 실측 전 — 46장 해석 표에 같은 내용)
  · 격자를 **10분 칸 전부로 다시 펴고** 나서 민다. 9번 · 11번은 관측된 시각끼리 `shift`했는데, 수집이 빈 구간이
    있으면 '6틱 뒤'가 실제로는 몇 시간 뒤가 된다. 빈 칸은 NaN이고 그 행은 빠진다.
  · τ가 자정을 넘어 휴일로 들어가는 행은 뺀다(평일 목표로 휴일을 재게 된다).

실행:
    python experiments/structure/target_forecast.py
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
import saturation_forecast as satf  # noqa: E402
import stockout_forecast as sf  # noqa: E402
from project_config import DEFAULT_PERIOD, REBAL_MIN_QTY, holiday_mask  # noqa: E402

TICK = pd.Timedelta(minutes=sf.TICK_MINUTES)

# ── 사전 등록 (EXPERIMENTS 46장)
HORIZONS = (6, 18)                       # 1시간 · 3시간
TEST_DAYS = sf.DEFAULT_TEST_DAYS         # 뒤 4일
GBM_PARAMS = dict(max_iter=200, learning_rate=0.1, max_depth=6, random_state=42)
LABELS = {"수거": "pick", "배송": "drop"}

FEATURES = ["재고", "목표", "격차", "지금수거", "지금배송", "재고1틱전", "재고3틱전", "재고6틱전",
            "재고변화3틱", "재고변화6틱", "거치대", "순수요누적격차", "시", "요일",
            "과거빈도_수거", "과거빈도_배송"]


def duration_of(hours: np.ndarray) -> np.ndarray:
    """시(0~23) → 회차 이름."""
    return np.select([(hours >= 5) & (hours < 10), (hours >= 10) & (hours < 15), (hours >= 15) & (hours < 20)],
                     ["_05_10", "_10_15", "_15_20"], default="_20_05")


def load_targets(conn) -> dict:
    """회차 → (대여소 → target_qty, 그 계획 라벨). 회차마다 가장 최근 평일 계획 실행."""
    sql = """SELECT p.run_label, p.duration, p.station_id, p.target_qty
             FROM rebalance_plan p JOIN runs r USING (run_label)
             WHERE r.kind = 'plan' AND r.day_type = 'weekday' AND r.period = ?
               AND p.run_label = (SELECT p2.run_label FROM rebalance_plan p2 JOIN runs r2 USING (run_label)
                                  WHERE r2.kind = 'plan' AND r2.day_type = 'weekday' AND r2.period = ?
                                    AND p2.duration = p.duration
                                  ORDER BY r2.created_at DESC LIMIT 1)"""
    frame = pd.read_sql(sql, conn, params=(DEFAULT_PERIOD, DEFAULT_PERIOD))
    out = {}
    for duration, g in frame.groupby("duration"):
        out[duration] = (g.drop_duplicates("station_id").set_index("station_id")["target_qty"].astype(float),
                         g["run_label"].iloc[0])
    return out


def build_rows(stock: pd.DataFrame, targets: dict, capacity: pd.Series, mu_weekday: pd.DataFrame,
               horizon: int, days: set) -> pd.DataFrame:
    """(시각 t, 대여소)마다 피처와 두 라벨. `stock`은 10분 칸 전부로 편 격자(빈 칸 NaN)다."""
    ts = stock.index
    stations = stock.columns
    S = stock.to_numpy(dtype=float)
    T = len(ts)

    def shifted(k: int) -> np.ndarray:            # k > 0: k틱 전 · k < 0: k틱 뒤
        out = np.full_like(S, np.nan)
        if k > 0:
            out[k:] = S[:-k]
        else:
            out[:k] = S[-k:]
        return out

    future = shifted(-horizon)
    tau = ts + horizon * TICK
    tau_dur = duration_of(tau.hour.to_numpy())
    target = np.full_like(S, np.nan)
    for duration, (series, _) in targets.items():
        rows = tau_dur == duration
        target[rows] = series.reindex(stations).to_numpy(dtype=float)

    # 순수요 누적: 지금 재고 − 앞으로 k틱의 평균 순유출(평일 · 운영 기간). 통계에 없는 대여소는 유출 0
    outflow_table = satf.window_outflow(mu_weekday, horizon).reindex(stations).fillna(0.0).to_numpy()
    slot = (ts.hour * 6 + ts.minute // sf.TICK_MINUTES).to_numpy()
    outflow = outflow_table[:, slot].T                                   # (T, N)

    gap = target - S
    feats = {
        "재고": S, "목표": target, "격차": gap,
        "지금수거": (gap < -REBAL_MIN_QTY).astype(float), "지금배송": (gap > REBAL_MIN_QTY).astype(float),
        "재고1틱전": shifted(1), "재고3틱전": shifted(3), "재고6틱전": shifted(6),
        "재고변화3틱": S - shifted(3), "재고변화6틱": S - shifted(6),
        "거치대": np.broadcast_to(capacity.reindex(stations).to_numpy(dtype=float), S.shape),
        "순수요누적격차": target - (S - outflow),
    }
    rebal_future = target - future

    t_day = pd.Series(ts.date)
    weekday_t = ~np.asarray(holiday_mask(pd.Series(ts.normalize())), dtype=bool)
    weekday_tau = ~np.asarray(holiday_mask(pd.Series(tau.normalize())), dtype=bool)
    row_ok = weekday_t & weekday_tau & t_day.isin(days).to_numpy()
    valid = (np.isfinite(S) & np.isfinite(future) & np.isfinite(target)) & row_ok[:, None]

    ii, jj = np.nonzero(valid)
    out = pd.DataFrame({"ts": ts[ii], "station_id": stations[jj]})
    for name, arr in feats.items():
        out[name] = np.asarray(arr)[ii, jj].astype(np.float32)
    out["τ회차"] = tau_dur[ii]
    out["시"] = ts.hour.to_numpy()[ii]
    out["요일"] = ts.dayofweek.to_numpy()[ii]
    out["y_수거"] = (rebal_future[ii, jj] < -REBAL_MIN_QTY).astype(np.int8)
    out["y_배송"] = (rebal_future[ii, jj] > REBAL_MIN_QTY).astype(np.int8)
    return out


def add_history(train: pd.DataFrame, frames: list) -> list:
    """② 과거 빈도 — 학습 구간의 대여소 × 시 라벨 비율. 베이스라인이자 피처(9번과 같은 규칙)."""
    out = []
    for f in frames:
        f = f.copy()
        for name in LABELS:
            table = train.groupby(["station_id", "시"])[f"y_{name}"].mean().rename(f"과거빈도_{name}")
            f = f.merge(table, on=["station_id", "시"], how="left")
            f[f"과거빈도_{name}"] = f[f"과거빈도_{name}"].fillna(train[f"y_{name}"].mean())
        out.append(f)
    return out


def evaluate(train: pd.DataFrame, test: pd.DataFrame, name: str, horizon: int) -> tuple:
    from sklearn.ensemble import HistGradientBoostingClassifier

    y_tr, y = train[f"y_{name}"], test[f"y_{name}"].to_numpy()
    model = HistGradientBoostingClassifier(**GBM_PARAMS).fit(train[FEATURES], y_tr)
    prob = model.predict_proba(test[FEATURES])[:, 1]
    flag = "지금수거" if name == "수거" else "지금배송"
    net = (test["순수요누적격차"] < -REBAL_MIN_QTY) if name == "수거" else (test["순수요누적격차"] > REBAL_MIN_QTY)
    preds = {"⓪ 전체 평균": np.full(len(y), y_tr.mean()),
             "① 지속": test[flag].to_numpy(dtype=float),
             "② 과거 빈도": test[f"과거빈도_{name}"].to_numpy(dtype=float),
             "③ 순수요 누적": net.to_numpy(dtype=float),
             "④ GBM": prob}
    scores = {k: sf.brier(v, y) for k, v in preds.items()}
    win = all(scores["④ GBM"] < scores[k] for k in ("① 지속", "② 과거 빈도", "③ 순수요 누적"))
    print(f"\n### {name} · {horizon}틱({horizon * 10}분) 뒤 — 검증 {len(y):,}행 · 실제 비율 {y.mean():.1%}")
    best = min(scores[k] for k in ("① 지속", "② 과거 빈도", "③ 순수요 누적"))
    for k, b in scores.items():
        print(f"  {k:<10} {b:.4f}" + ("" if k.startswith("⓪") else f"   (세 베이스 중 최선 대비 {(b / best - 1) * 100:+.1f}%)"))
    print(f"  → {'✅ 세 베이스라인을 모두 이긴다' if win else '❌ 못 이긴다 — 이 조합은 베이스라인으로 낸다'}")
    return win, preds, model


def report_details(test: pd.DataFrame, preds: dict, name: str) -> None:
    y = test[f"y_{name}"].to_numpy()
    prob = preds["④ GBM"]
    sf.report_calibration(prob, y)
    print("\n  회차별 (τ의 회차) — Brier GBM · ③ 순수요 누적 · ① 지속")
    for duration, idx in test.groupby("τ회차").indices.items():
        print(f"    {duration}: n={len(idx):,} 실제 {y[idx].mean():.1%} · GBM {sf.brier(prob[idx], y[idx]):.4f}"
              f" · ③ {sf.brier(preds['③ 순수요 누적'][idx], y[idx]):.4f} · ① {sf.brier(preds['① 지속'][idx], y[idx]):.4f}")
    K = 50
    work = pd.DataFrame({"ts": test["ts"].to_numpy(), "GBM": prob, "빈도": preds["② 과거 빈도"],
                         "격차": test["순수요누적격차"].to_numpy() * (-1 if name == "수거" else 1), "실제": y})
    hits = {k: [] for k in ("GBM", "빈도", "격차")}
    for _, g in work.groupby("ts"):
        if len(g) >= K:
            for k in hits:
                hits[k].append(g.nlargest(K, k)["실제"].mean())
    print(f"  매 시각 상위 {K}곳 중 실제 {name} 대상 — GBM {np.mean(hits['GBM']):.1%} · 과거 빈도 "
          f"{np.mean(hits['빈도']):.1%} · 순수요 누적 격차 순 {np.mean(hits['격차']):.1%} · 무작위 {y.mean():.1%}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    with db.session() as conn:
        targets = load_targets(conn)
        cap_label, capacity = satf.load_capacity(conn)
        raw = sf.load_grid(conn)
        mu = satf.hourly_mu(conn, DEFAULT_PERIOD)[False]
    print(f"목표 재고(평일, {DEFAULT_PERIOD}): " + " · ".join(f"{d} ← '{lab}'" for d, (_, lab) in sorted(targets.items())))
    print(f"거치대 = '{cap_label}' · 순수요 누적 = {DEFAULT_PERIOD} 평일 {len(mu):,}곳")

    days = sf.dense_days(raw)
    weekday_days = [d for d, h in zip(days, holiday_mask(pd.Series(pd.to_datetime(days)))) if not h]
    train_days, test_days = set(weekday_days[:-TEST_DAYS]), set(weekday_days[-TEST_DAYS:])
    print(f"평일 {len(weekday_days)}일 — 학습 {len(train_days)}일 · 검증 {sorted(test_days)}")

    stock = raw.pivot_table(index="ts", columns="station_id", values="stock")
    grid = pd.date_range(stock.index.min().floor("D"), stock.index.max().ceil("D"), freq=TICK, inclusive="left")
    stock = stock.reindex(grid)
    print(f"격자 {len(grid):,}칸 × 대여소 {stock.shape[1]:,} · 관측된 칸 {np.isfinite(stock.to_numpy()).mean():.1%}")

    verdicts = {}
    for horizon in HORIZONS:
        rows = build_rows(stock, targets, capacity, mu, horizon, train_days | test_days)
        day = rows["ts"].dt.date
        train, test = rows[day.isin(train_days)], rows[day.isin(test_days)]
        train, test = add_history(train, [train, test])
        print(f"\n## 지평 {horizon}틱 — 학습 {len(train):,}행 · 검증 {len(test):,}행")
        for name in LABELS:
            win, preds, _ = evaluate(train, test, name, horizon)
            verdicts[(name, horizon)] = win
            report_details(test, preds, name)
            sf.report_leak_checks(train.assign(타깃=train[f"y_{name}"]), test, FEATURES, test[f"y_{name}"].to_numpy())
            if horizon == 18 and name == "배송":
                # 대여소가 거의 다 관측된 시각 가운데 가장 늦은 것(09-30처럼 수집이 끊긴 날의 끝을 피한다)
                counts = test["ts"].value_counts()
                last = counts[counts >= 0.95 * counts.max()].index.max()
                at = test[test["ts"] == last].assign(P=preds["④ GBM"][(test["ts"] == last).to_numpy()])
                print(f"\n  예시 — {last} 에 본 '3시간 뒤 배송 대상 확률' 상위 10곳")
                print(at.nlargest(10, "P")[["station_id", "재고", "목표", "τ회차", "P", "y_배송"]]
                      .round(3).to_string(index=False))

    print("\n## 판정 (사전 등록: 4조합 모두에서 세 베이스라인을 이길 것)")
    for (name, horizon), win in verdicts.items():
        print(f"  {name} · {horizon}틱: {'✅' if win else '❌'}")
    passed = all(verdicts.values())
    print(f"  → {'✅ 모형 단계 통과' if passed else '❌ 모형 단계 미달 — 못 넘은 조합은 베이스라인으로 낸다'}")
    return 0


if __name__ == "__main__":
    project_config.exit_if_help(__doc__)
    raise SystemExit(main())
