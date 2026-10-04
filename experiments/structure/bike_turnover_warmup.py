"""ML 12번을 **그 달 첫 2주로 수준을 맞춰** 다시 잰다 (EXPERIMENTS 45장 · ML 13번, 1.26.313).

왜 — ML 12번(`bike_turnover.py`, 44장 2부)은 GBM이 빈도표를 Brier로 12칸 모두 이겼지만 보정이 6칸에서 ±0.05를 넘어
기각됐고, 이득과 어긋남이 모두 겨울에 몰렸다. 학습 1년의 평균 수준이 시험 달의 수준과 달라서라고 읽었다. 파이프라인은
같은 문제를 계획 대상 달 첫 14일로 배율을 맞추는 warmup으로 푼다 — 같은 일을 확률에 한다.

사전 등록 (EXPERIMENTS 45장 — 결과 전 커밋. 바꾸지 않는다)
  바꾸는 것 : 수준 보정 하나. 시험 달마다 반납일 1~14일 행으로 예측기 · 달 · 요일 구분마다 로짓 상수 b를 맞추고
              (보정 창 평균 예측 = 평균 실제), 15일 이후 행만 평가한다. 라벨 · 분할 · 피처 · 모형은 44장 그대로
  공정성    : 빈도표 ① · 평균 ⓪도 똑같이 보정한다(⓪'은 보정 창의 평균) — 수준 맞추기는 파이프라인이 이미 하는 일
  ML 채택   : 12칸 전부에서 GBM' Brier < ①' + 보정(행 1,000 이상 구간에서 |예측 − 실제| ≤ 0.05)
  곁에 찍기 : 같은 자리 다음 대여가 10분 안인 행을 뺀 표 — 판정에 쓰지 않는다

코드로 옮기며 정한 것: 곁의 표는 시험 달에서 10분 안 행을 빼고 **보정 창 · 평가를 같은 절차로** 다시 한다(모형은
그대로) — 빼고 나면 양성 비율이 달라져, 뺀 행이 섞인 보정 창의 b를 쓰면 보정이 어긋난 것처럼 보인다. 학습을 다시
하면 바꾸는 것이 둘이 된다.

실행:
    python experiments/structure/bike_turnover_warmup.py
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import project_config  # noqa: E402,F401  — 콘솔 인코딩을 먼저 맞춘다(— · 이모지)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.optimize import brentq  # noqa: E402
from scipy.special import expit, logit  # noqa: E402

import bike_turnover as bt  # noqa: E402
import db  # noqa: E402
import rebalance_trace as rt  # noqa: E402
import weather  # noqa: E402

# ── 사전 등록 (EXPERIMENTS 45장)
WARMUP_LAST_DAY = 14         # 반납일 1~14일이 보정 창, 15일부터 평가
RERENT_MIN = 10              # 곁의 표에서 뺄 '곧바로 다시 빌림'
EPS = 1e-6
PREDICTORS = {"p_mean": "⓪' 평균", "p_base": "①' 빈도표", "p_gbm": "GBM'"}


def level_offset(p: np.ndarray, y: np.ndarray) -> float:
    """보정 창에서 mean(expit(logit(p) + b)) = mean(y)가 되는 b."""
    z = logit(np.clip(p, EPS, 1 - EPS))
    target = float(np.mean(y))
    target = min(max(target, EPS), 1 - EPS)
    return brentq(lambda b: float(np.mean(expit(z + b))) - target, -20, 20)


def shift(p: np.ndarray, b: float) -> np.ndarray:
    return expit(logit(np.clip(p, EPS, 1 - EPS)) + b)


def evaluate_month(g: pd.DataFrame) -> tuple:
    """한 시험 달 · 요일 구분: 보정 창으로 b를 맞추고 15일 이후 행에 보정된 예측을 붙인다."""
    day = g["t"].dt.day
    warm, ev = g[day <= WARMUP_LAST_DAY], g[day > WARMUP_LAST_DAY].copy()
    offsets = {}
    for col in PREDICTORS:
        if col == "p_mean":
            ev[col + "_w"] = float(warm["y"].mean())        # ⓪'은 보정 창의 평균
            offsets[col] = np.nan
            continue
        b = level_offset(warm[col].to_numpy(), warm["y"].to_numpy())
        offsets[col] = b
        ev[col + "_w"] = shift(ev[col].to_numpy(), b)
    return ev, offsets, len(warm)


def score(ev: pd.DataFrame) -> dict:
    y = ev["y"].to_numpy()
    out = {"행": len(ev), "양성": y.mean() if len(y) else np.nan}
    for col, name in PREDICTORS.items():
        out[name] = bt.brier(ev[col + "_w"], y)
    out["GBM'/①' −1"] = out["GBM'"] / out["①' 빈도표"] - 1
    out["Brier 이김"] = out["GBM'"] < out["①' 빈도표"]
    worst, used = bt.calibration_gap(ev["p_gbm_w"].to_numpy(), y)
    out["보정 최대오차"], out["보정 구간"] = worst, used
    out["보정 통과"] = worst <= bt.CALIB_TOL
    return out


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    with db.session() as conn:
        df, ends = rt.load_rentals(conn)
    returns, _ = bt.build_returns(df, ends)
    hourly = weather.load_hourly().set_index("time")[bt.WEATHER_COLS]
    hourly = hourly[~hourly.index.duplicated()]

    main_rows, side_rows = [], []
    for holiday, _tr, te, _table in bt.predict_test(df, returns, hourly):
        name = "휴일" if holiday else "평일"
        for ym, g in te.groupby("ym"):
            ev, offsets, n_warm = evaluate_month(g)
            row = {"요일": name, "달": str(ym), "보정창 행": n_warm,
                   "b ①": offsets["p_base"], "b GBM": offsets["p_gbm"]}
            main_rows.append({**row, **score(ev)})
            quick = g["next_same_min"] <= RERENT_MIN
            ev_side, _, _ = evaluate_month(g[~quick])
            side_rows.append({"요일": name, "달": str(ym), "뺀 행": int(quick.sum()), **score(ev_side)})

    table = pd.DataFrame(main_rows)
    print("\n## 수준 보정 뒤 — 시험 6개월 × 요일 구분, 15일 이후 (Brier, 낮을수록 좋다)")
    with pd.option_context("display.width", 220):
        print(table.round(4).to_string(index=False))
    wins, calib = int(table["Brier 이김"].sum()), int(table["보정 통과"].sum())
    passed = bool(table["Brier 이김"].all() and table["보정 통과"].all())
    print(f"\n  Brier 이김 {wins}/{len(table)} · 보정 통과 {calib}/{len(table)} → "
          f"{'✅ ML 채택 조건 통과' if passed else '❌ ML 채택 조건 미달'}")

    side = pd.DataFrame(side_rows)
    print(f"\n## 곁의 표 (판정에 쓰지 않는다) — 같은 자리 다음 대여가 {RERENT_MIN}분 안인 행을 평가에서 뺐다")
    with pd.option_context("display.width", 220):
        print(side.round(4).to_string(index=False))
    print(f"\n  Brier 이김 {int(side['Brier 이김'].sum())}/{len(side)} · 보정 통과 {int(side['보정 통과'].sum())}/{len(side)}")


if __name__ == "__main__":
    project_config.exit_if_help(__doc__)
    main()
