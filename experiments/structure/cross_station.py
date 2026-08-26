"""실험 — 대여소끼리 정보를 빌리면 표본 부족을 메울 수 있나 (수정안 19번 후속).

**배경**: 평일 한 달이면 대여소당 17~22일뿐이라 `mu`·`sigma`가 흔들린다
(DEMAND_DISTRIBUTION.md 6장 '되물음'). 시간축으로 표본을 늘리는 길
(옛 달 넣기)은 이미 막혔다 — 달마다 값이 달라 중심이 틀어졌다.

남은 길은 **대여소를 가로질러 빌리는 것**이다. 표본이 적은 대여소가 비슷한
대여소 여럿에게서 배우면 실질 표본이 커진다(축소추정, shrinkage). 5장의
분위수 회귀가 노렸던 것도 이것이다 — 다만 **실제로는 시도된 적이 없다**.
`demand_model.FEATURES`는 전부 **그 대여소 자신의 지난달 통계**이고,
거치대 수·좌표 같은 대여소 특성은 하나도 들어 있지 않다.

**그래서 모델을 짜기 전에 이득이 있는지부터 잰다.**

  · 이웃을 **위치**로 정의 (가장 가까운 K곳)
  · 이웃을 **행동**으로 정의 (지난달 mu가 비슷한 K곳)
  · 당기는 정도 `w`를 0부터 키워 가며 이번 달 mu 예측 오차를 본다
        예측 = (1-w)·자기 지난달 mu + w·이웃 평균
    w=0이 최적이면 **빌릴 값어치가 없다**는 뜻이다.

판정 규칙은 이 저장소의 것을 그대로 따른다 — 작업 대상만(`|mu| > REBAL_MIN_QTY`),
평일/휴일 분리, 표본 밖(지난달로 이번 달을 맞힌다), 여러 달로 확인.

실행:
    python experiments/structure/cross_station.py
    python experiments/structure/cross_station.py --duration _10_15 --k 30
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import numpy as np
import pandas as pd

import db
from project_config import DAY_TYPES, DURATIONS, REBAL_MIN_QTY, normalize_day_type, select_day_type
from backtest_demand import daily_window_demand

WEIGHTS = (0.0, 0.05, 0.1, 0.2, 0.3, 0.5)


def period_key(label):
    year, month = label.split("년")
    return int(year.strip()) * 100 + int(month.replace("월", "").strip())


def neighbors_by_location(frame: pd.DataFrame, k: int) -> np.ndarray:
    """가장 가까운 k곳의 지난달 mu 평균. 자기 자신은 뺀다."""
    coords = frame[["lat", "lon"]].to_numpy(dtype=float)
    dist = ((coords[:, None, :] - coords[None, :, :]) ** 2).sum(-1)
    np.fill_diagonal(dist, np.inf)
    idx = np.argsort(dist, axis=1)[:, :k]
    return frame["prev"].to_numpy()[idx].mean(1)


def neighbors_by_behavior(frame: pd.DataFrame, k: int) -> np.ndarray:
    """지난달 mu가 비슷한 k곳의 평균 — '행동이 닮은' 대여소끼리 묶는다."""
    values = frame["prev"].to_numpy(dtype=float)
    order = np.argsort(values)
    rank = np.empty_like(order)
    rank[order] = np.arange(len(values))
    half = max(1, k // 2)
    return np.array([
        values[np.clip(np.arange(r - half, r + half + 1), 0, len(values) - 1)].mean()
        for r in rank
    ])


def signal_to_noise(frame: pd.DataFrame) -> tuple:
    """대여소 간 진짜 차이 vs mu 추정 잡음.

    이 비가 크면 축소추정은 손해다 — 줄일 잡음보다 뭉갤 진짜 차이가 크다.
    """
    spread = float(frame["prev"].std())
    noise = float((frame["sd"] / np.sqrt(frame["n"])).median())
    return spread, noise, (spread / noise if noise else float("nan"))


def main() -> int:
    parser = argparse.ArgumentParser(description="대여소끼리 정보를 빌리면 나아지나")
    parser.add_argument("--duration", help="시간대 하나만 (기본: 전부)")
    parser.add_argument("--day-type", default=DAY_TYPES[0])
    parser.add_argument("--k", type=int, default=20, help="이웃 수 (기본 20)")
    args, _ = parser.parse_known_args()

    day_type = normalize_day_type(args.day_type)
    durations = [args.duration] if args.duration else DURATIONS

    with db.session() as conn:
        info = (db.load_frame(conn, "station_info")
                .drop_duplicates("station_id", keep="last")
                .set_index("station_id")[["lat", "lon", "parking_lot"]])
        periods = [r[0] for r in conn.execute(
            "SELECT DISTINCT period FROM net_demand").fetchall()]
        net = {}
        for p in periods:
            frame = select_day_type(db.load_frame(conn, "net_demand", period=p),
                                    "date", day_type)
            if not frame.empty:
                net[p] = frame

    periods = sorted(net, key=period_key)
    if len(periods) < 2:
        print("기간이 모자랍니다.")
        return 1

    print("적재 기간 %d개 · %s · 이웃 %d곳 · 작업 대상만(|순수요|>%d)\n"
          % (len(periods), day_type, args.k, REBAL_MIN_QTY))

    rows, snr_rows = [], []
    for duration in durations:
        for i in range(1, len(periods)):
            test_period, prev = periods[i], periods[i - 1]
            if period_key(test_period) - period_key(prev) != 1:
                continue                    # 이어지는 달끼리만 (구멍 건너뛰기 금지)

            cur = (daily_window_demand(net[test_period], duration)
                   .groupby("station_id")["demand"].mean().rename("cur"))
            prev_daily = daily_window_demand(net[prev], duration)
            grouped = prev_daily.groupby("station_id")["demand"]
            frame = pd.concat([cur, grouped.mean().rename("prev"),
                               grouped.std().rename("sd"),
                               grouped.size().rename("n")], axis=1).dropna()
            frame = frame.join(info, how="inner").dropna()
            if len(frame) < 100:
                continue

            work = (frame["prev"].abs() > REBAL_MIN_QTY).to_numpy()
            # ⚠️ 문턱을 높이면 **결과가 만들어진다.** 예전에 30곳으로 뒀더니
            # `_10_15`(작업 대상 중앙값 33곳)에서 9달 중 4달만 남았고, 하필
            # '빌리기가 이긴 달'이 3/4으로 남아 승률이 100%로 찍혔다.
            # 전체로 보면 4/9(우연)이다. 통계가 성립하는 최소선만 남긴다.
            if work.sum() < 10:
                continue

            spread, noise, ratio = signal_to_noise(frame[work])
            snr_rows.append(dict(duration=duration, period=test_period,
                                 spread=spread, noise=noise, ratio=ratio))

            own = frame["prev"].to_numpy(dtype=float)
            truth = frame["cur"].to_numpy(dtype=float)
            for label, neighbor in (("위치", neighbors_by_location(frame, args.k)),
                                    ("행동", neighbors_by_behavior(frame, args.k))):
                for w in WEIGHTS:
                    pred = (1 - w) * own + w * neighbor
                    rows.append(dict(duration=duration, period=test_period,
                                     이웃=label, w=w,
                                     mae=float(np.abs(truth[work] - pred[work]).mean())))

    if not rows:
        print("비교할 조합이 없습니다.")
        return 1

    frame = pd.DataFrame(rows)

    print("=== 이웃 쪽으로 당길수록 이번 달 mu를 잘 맞히나 (MAE) ===")
    pivot = frame.pivot_table(index="이웃", columns="w", values="mae")
    print(pivot.round(3).to_string())

    print("\n=== 판정 ===")
    for label in pivot.index:
        best_w = pivot.loc[label].idxmin()
        base = pivot.loc[label, 0.0]
        gain = (1 - pivot.loc[label].min() / base) * 100
        verdict = ("**빌릴 값어치가 없다** (자기 것만 쓰는 w=0이 최적)"
                   if best_w == 0.0 else "최적 w=%.2f, %.1f%% 개선" % (best_w, gain))
        print("  이웃을 %s로 정의: %s" % (label, verdict))

    if snr_rows:
        snr = pd.DataFrame(snr_rows)
        print("\n=== 왜 그런가 — 신호 대 잡음 ===")
        print(snr.groupby("duration")[["spread", "noise", "ratio"]].mean().round(2).to_string())
        print("\n  spread = 대여소 간 mu의 표준편차 (진짜 차이)")
        print("  noise  = mu 추정의 표준오차 중앙값 (표본이 적어 생기는 흔들림)")
        print("  ratio  = spread / noise")
        mean_ratio = float(snr["ratio"].mean())
        if mean_ratio > 3:
            print("\n  비가 %.1f배다 — **진짜 차이가 잡음보다 훨씬 크다.**" % mean_ratio)
            print("  이럴 때 이웃 쪽으로 당기면 줄이는 잡음보다 뭉개는 차이가 커서")
            print("  손해다. 축소추정이 이기려면 이 비가 1에 가까워야 한다.")

    # ⚠️ 시간대 평균만 보면 안 된다. 작업 대상이 수십 곳뿐인 시간대(`_10_15`는
    # 중앙값 33곳)는 한 달만 튀어도 평균이 크게 움직인다. **달별 승패**를 함께
    # 봐야 그 '개선'이 진짜인지 우연인지 갈린다.
    print("\n=== 달별 승패 (w=0 vs 최적 w) — 평균이 감추는 것 ===")
    detail = frame.pivot_table(index=["duration", "period", "이웃"],
                               columns="w", values="mae")
    tally = []
    for (dur, period_label, label), row in detail.iterrows():
        tally.append(dict(duration=dur, 이웃=label,
                          이김=bool(row.drop(0.0).min() < row[0.0])))
    summary = (pd.DataFrame(tally).groupby(["duration", "이웃"])["이김"]
               .agg(이긴달="sum", 전체="count"))
    summary["승률"] = (summary["이긴달"] / summary["전체"] * 100).round(0)
    print(summary.to_string())
    print("\n  승률이 50% 근처면 그 '개선'은 우연이다 — 채택하지 않는다.")

    print("\n=== 시간대별 (w=0 대비 최적 w의 개선) ===")
    per = frame.pivot_table(index=["duration", "이웃"], columns="w", values="mae")
    out = []
    for key_, row in per.iterrows():
        out.append(dict(duration=key_[0], 이웃=key_[1],
                        최적w=row.idxmin(), 개선=f"{(1 - row.min() / row[0.0]) * 100:+.1f}%"))
    print(pd.DataFrame(out).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
