"""실험 — 회차 시작 시각의 재고를 미리 맞힐 수 있나 (ML 고도화 후보 F1의 착수 전 검사 ②).

**왜 필요한가.** 계획은 `rebal_qty = target_qty − stock`인데, 그 `stock`은 계획을
계산한 순간의 라이브 재고다. 실제 집행은 몇 시간 뒤 회차 시작 시각이고, 그 사이
재고는 움직인다. KPI.md 8장은 초기 재고가 ±3대만 달라져도 개선량이 0.46~1.74h로
3.8배 흔들린다고 쟀다 — 스냅샷 민감성은 원고 8장의 한계이기도 하다. 수집기가
10분 간격 재고를 24시간 쌓은 지금은 *"계획 시각 재고 → 회차 시작 재고"* 를
관측으로 잴 수 있다.

**무엇을 묻는가.** 회차 시작 h시간 전(계획 시각)의 재고로 회차 시작 재고를
맞힐 때, 세 방법의 오차를 견준다.

  P  지속       — 지금 재고가 그대로다 (파이프라인 현행과 같다)
  N  순수요 누적 — 지금 재고 − Σ(그 시간대의 월평균 순수요)  ← 파이프라인이 **이미 아는
                  것**만으로 만들 수 있는 공정한 베이스라인(검사 ③)
  M  관측 평균   — 지금 재고 + 다른 날 같은 대여소·같은 구간의 평균 변화(날짜별 교차)
  G  GBM         — 위 셋과 lag 재고를 피처로 한 회귀 (날짜별 교차)

**판정.** G가 P·N을 **둘 다** 이기고, 작업 대상급(창의 |순수요| > REBAL_MIN_QTY)
대여소에서도 이기며, 오차 3대 이상인 대여소의 비율이 줄어야 *"붙일 값어치가 있다"*
로 적는다. 하나라도 아니면 F1은 착수하지 않는다. 결과를 보기 전에 정한 기준이다.

⚠️ 자료가 평일 며칠뿐이다(24시간 창은 2026-09-15부터). 여기서 나오는 것은
채택 근거가 아니라 **착수 여부**다 — ML_계획.md 1장의 검사 ②.

🔴 날짜로 자른다(같은 날의 앞뒤 틱이 양쪽에 들어가면 누출). 무작위 분할 금지.

실행:
    python experiments/structure/stock_nowcast_signal.py
    python experiments/structure/stock_nowcast_signal.py --leads 1,2,3 --min-ticks 100
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import project_config  # noqa: E402,F401  — 다른 어떤 print보다 먼저 콘솔 UTF-8을 켠다(출력을 파일로 넘겨도 죽지 않게)

import argparse  # noqa: E402

import numpy as np
import pandas as pd

import db
from project_config import DURATIONS, REBAL_MIN_QTY, holiday_mask, latest_period

TICK_MIN = 10
START_HOURS = {"_05_10": 5, "_10_15": 10, "_15_20": 15, "_20_05": 20}


def load_stock(conn, min_ticks: int) -> pd.DataFrame:
    """평일·틱이 충분한 날만 (시각 × 대여소) 격자로 편다. 수집기의 관측 그대로다(검사 ①)."""
    # 날짜별 틱 수는 SQL로 먼저 센다 — 290만 행을 다 읽어 놓고 거르면 느리다.
    per_day = pd.read_sql(
        "SELECT substr(observed_at, 1, 10) AS d, COUNT(DISTINCT observed_at) AS n"
        " FROM stock_history GROUP BY d", conn)
    dense = per_day[per_day["n"] >= min_ticks]["d"]
    dense = dense[~holiday_mask(pd.to_datetime(dense)).values]
    days = sorted(pd.to_datetime(dense).dt.date)
    if not days:
        raise SystemExit(f"하루 {min_ticks}틱 이상인 평일이 없습니다 — --min-ticks를 낮추십시오")
    frame = db.load_stock_history(conn, start=str(days[0]), end=str(days[-1]))
    frame["ts"] = pd.to_datetime(frame["observed_at"])
    frame = frame[frame["ts"].dt.date.isin(days)]
    wide = (frame.pivot_table(index="ts", columns="station_id", values="stock")
            .sort_index().asfreq(f"{TICK_MIN}min"))
    return wide, days


def hourly_mu(conn, period: str) -> pd.DataFrame:
    """대여소 × 시(0~23)의 평일 평균 순수요 — 파이프라인이 쓰는 그 통계량이다."""
    net = pd.read_sql("SELECT * FROM net_demand WHERE period = ?", conn, params=(period,))
    net = net[~holiday_mask(pd.to_datetime(net["date"]))]
    cols = [f"net_{h:02d}" for h in range(24)]
    mu = net.groupby("station_id")[cols].mean()
    mu.columns = range(24)
    return mu


def window_mu(mu: pd.DataFrame, duration: str) -> pd.Series:
    """창 안 순수요 평균의 절댓값 — 작업 대상급을 가르는 자."""
    start = START_HOURS[duration]
    hours = [(start + i) % 24 for i in range(9 if duration == "_20_05" else 5)]
    return mu[hours].sum(axis=1).abs()


def build_rows(wide: pd.DataFrame, mu: pd.DataFrame, duration: str, lead: int) -> pd.DataFrame:
    """(날짜, 대여소)마다 계획 시각(t0)의 정보와 회차 시작 재고(타깃)를 만든다."""
    start = START_HOURS[duration]
    at_start = wide[(wide.index.hour == start) & (wide.index.minute == 0)]
    rows = []
    for ts in at_start.index:
        t0 = ts - pd.Timedelta(hours=lead)
        if t0 not in wide.index:
            continue
        now = wide.loc[t0]
        if now.isna().all():
            continue
        lags = {f"lag{k}": wide.loc[t0 - pd.Timedelta(minutes=TICK_MIN * k)]
                if (t0 - pd.Timedelta(minutes=TICK_MIN * k)) in wide.index else now
                for k in (1, 3, 6)}
        # 계획 시각부터 회차 시작까지의 시간대 평균 순수요 합 (순유출이 양수 → 재고가 준다)
        hours = [(t0.hour + i) % 24 for i in range(lead)]
        cum = mu.reindex(now.index)[hours].sum(axis=1).fillna(0.0)
        df = pd.DataFrame({
            "date": ts.date(), "station_id": now.index, "hour0": t0.hour,
            "stock0": now.values, "target": at_start.loc[ts].values,
            "lag1": lags["lag1"].reindex(now.index).values,
            "lag3": lags["lag3"].reindex(now.index).values,
            "lag6": lags["lag6"].reindex(now.index).values,
            "cum_mu": cum.values,
        })
        rows.append(df.dropna(subset=["stock0", "target"]))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def evaluate(rows: pd.DataFrame, target_like: pd.Series, seed: int = 42) -> dict:
    """날짜별 교차(하루를 빼고 나머지로 학습)로 네 방법의 MAE를 낸다."""
    from sklearn.ensemble import HistGradientBoostingRegressor

    rows = rows.copy()
    rows["delta"] = rows["target"] - rows["stock0"]
    rows["작업대상급"] = rows["station_id"].map(target_like).fillna(0) > REBAL_MIN_QTY
    preds = {m: np.full(len(rows), np.nan) for m in ("P", "N", "M", "G")}
    for day in rows["date"].unique():
        te = rows["date"] == day
        tr = ~te
        # M — 같은 대여소·같은 구간의 다른 날 평균 변화
        mean_delta = rows[tr].groupby("station_id")["delta"].mean()
        m_delta = rows.loc[te, "station_id"].map(mean_delta).fillna(0.0)
        preds["P"][te] = rows.loc[te, "stock0"]
        preds["N"][te] = rows.loc[te, "stock0"] - rows.loc[te, "cum_mu"]
        preds["M"][te] = rows.loc[te, "stock0"] + m_delta
        feats = ["stock0", "lag1", "lag3", "lag6", "cum_mu", "hour0"]
        X = rows[feats].copy()
        X["mean_delta"] = rows["station_id"].map(mean_delta).fillna(0.0)
        model = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05,
                                              random_state=seed)
        model.fit(X[tr], rows.loc[tr, "delta"])
        preds["G"][te] = rows.loc[te, "stock0"] + model.predict(X[te])
    out = {}
    for m, p in preds.items():
        p = np.clip(np.round(p), 0, None)
        err = np.abs(p - rows["target"].values)
        big = rows["작업대상급"].values
        out[m] = {"mae": err.mean(), "mae_big": err[big].mean() if big.any() else np.nan,
                  "share_ge3": (err >= 3).mean(),
                  "share_ge3_big": (err[big] >= 3).mean() if big.any() else np.nan}
    out["_n"] = len(rows)
    out["_n_big"] = int(rows["작업대상급"].sum())
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--leads", default="1,2,3", help="계획 시각이 회차 시작 몇 시간 전인가")
    ap.add_argument("--min-ticks", type=int, default=100, help="하루 최소 틱 수(24시간=144)")
    ap.add_argument("--period", default=None, help="순수요 통계 기간(기본 latest_period)")
    ap.add_argument("--durations", default=",".join(DURATIONS))
    args = ap.parse_args(argv)
    leads = [int(x) for x in args.leads.split(",")]
    period = args.period or latest_period()

    with db.session() as conn:
        wide, days = load_stock(conn, args.min_ticks)
        mu = hourly_mu(conn, period)
    print("=" * 96)
    print("회차 시작 재고를 계획 시각 재고로 맞힐 수 있나 — 지속(P) · 순수요 누적(N) · 관측 평균(M) · GBM(G)")
    print("=" * 96)
    print(f"평일 {len(days)}일 ({days[0]} ~ {days[-1]}, 하루 {args.min_ticks}틱 이상) · 대여소 {wide.shape[1]}곳"
          f" · 순수요 통계 {period} · 날짜별 교차 · 예측은 정수로 반올림하고 0 아래는 자른다")
    print("MAE 단위는 대(臺). '작업대상급'은 창의 평일 |순수요 평균 합| > "
          f"{REBAL_MIN_QTY}인 대여소. '≥3'은 오차가 3대 이상인 대여소 비율(KPI 8장의 ±3대 민감성 문턱)\n")

    summary = []
    for duration in args.durations.split(","):
        like = window_mu(mu, duration)
        for lead in leads:
            rows = build_rows(wide, mu, duration, lead)
            if rows.empty or rows["date"].nunique() < 3:
                print(f"{duration} lead {lead}h — 날짜가 3일 미만이라 건너뜀")
                continue
            r = evaluate(rows, like)
            print(f"[{duration}] 계획 시각 = 시작 {lead}시간 전 · 행 {r['_n']:,} (작업대상급 {r['_n_big']:,})")
            print(f"  {'방법':<14}{'MAE 전체':>10}{'MAE 작업대상급':>16}{'≥3 전체':>10}{'≥3 작업대상급':>14}")
            for m, name in (("P", "지속"), ("N", "순수요 누적"), ("M", "관측 평균"), ("G", "GBM")):
                v = r[m]
                print(f"  {m} {name:<11}{v['mae']:>10.3f}{v['mae_big']:>16.3f}"
                      f"{v['share_ge3']:>10.3f}{v['share_ge3_big']:>14.3f}")
            g, p, n = r["G"], r["P"], r["N"]
            beats = g["mae_big"] < min(p["mae_big"], n["mae_big"]) and g["share_ge3_big"] < min(p["share_ge3_big"], n["share_ge3_big"])
            best_base = min(p["mae_big"], n["mae_big"])
            gain = (best_base - g["mae_big"]) / best_base * 100 if best_base else float("nan")
            print(f"  → GBM이 P·N을 작업대상급 MAE·≥3 모두에서 {'이긴다' if beats else '이기지 못한다'}"
                  f" (작업대상급 MAE 최선 베이스라인 대비 {gain:+.1f}%)\n")
            summary.append((duration, lead, beats, gain))

    print("판정 (사전 등록: 작업대상급에서 P·N 둘 다, MAE와 ≥3 비율 모두)")
    for duration, lead, beats, gain in summary:
        print(f"  {duration} {lead}h: {'✅' if beats else '❌'} {gain:+.1f}%")
    won = sum(1 for *_, b, _g in summary if b)
    print(f"\n{won}/{len(summary)} 조합에서 이겼다. "
          + ("착수할 값어치가 있다 — 다만 채택은 계획에 연결해 결품으로 재야 한다." if won >= len(summary) * 2 / 3
             else "착수하지 않는다 — 신호가 베이스라인을 넘지 못한다."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
