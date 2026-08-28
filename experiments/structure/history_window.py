"""실험 — 순수요를 한 달이 아니라 1년치에서 어떻게 뽑을 것인가 (수정안 19번).

지금은 계획 대상 달의 **직전 한 달**로 대여소별 mu·sigma를 내고, 계절 보정
(warmup 14일)으로 수준만 끌어올린다. raw_data에는 12개월이 있는데 한 달만 쓴다.

사용자가 제시한 두 방향을 그대로 잰다.

  · 하드스플릿 — 달·계절로 끊어 그 구간만 쓴다
        same_month   작년 같은 달
        season       같은 계절의 달을 모두 합침
        all_past     가진 과거 전부
  · 소프트스플릿 — 전부 쓰되 관련 없는 것은 비중을 낮춘다
        recency      최근일수록 무겁게 (반감기 감쇠)
        temp         기온이 계획 달과 비슷한 달에 가중
        recency+temp 둘 다

**베이스라인은 현행(prev1 + warmup14)이다.** 이것을 못 넘으면 채택하지 않는다.

판정 규칙(EXPERIMENTS.md·DEMAND_DISTRIBUTION.md의 규칙을 그대로 따른다):

  · **작업 대상 대여소에서만 잰다**(|순수요| > REBAL_MIN_QTY). 전체 평균은
    파이프라인이 손대지 않는 대여소에 희석돼 정반대 결론이 나온 적이 있다.
  · 평일·휴일을 섞지 않는다(`--day-type`, 기본 weekday).
  · 표본 밖이다 — 학습에 계획 대상 달을 넣지 않는다. warmup의 첫 14일만
    예외이고, 이는 운영 중에도 실제로 손에 있는 값이다.
  · MAE와 커버리지를 **함께** 본다. 커버리지만 보면 sigma를 부풀리는 방법이
    전부 좋아 보인다 — z를 올리는 것과 구분이 안 된다.

실행:
    python experiments/structure/history_window.py
    python experiments/structure/history_window.py --duration _05_10 --day-type holiday
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
import weather
from project_config import (
    DAY_TYPES, REBAL_MIN_QTY, TARGET_Z, normalize_day_type, select_day_type,
)
from backtest_demand import DURATIONS, daily_window_demand


def period_key(label):
    """'25년 11월' -> 2511"""
    year, month = label.split("년")
    return int(year.strip()) * 100 + int(month.replace("월", "").strip())


def month_of(label):
    return period_key(label) % 100


def abs_month(label):
    """비교용 절대 월 번호 — 연도를 넘어가도 간격이 맞는다."""
    key = period_key(label)
    return key // 100 * 12 + key % 100


def previous_month(label):
    year, month = divmod(period_key(label), 100)
    month -= 1
    if month == 0:
        year, month = year - 1, 12
    return "%02d년 %02d월" % (year, month)


SEASON = {12: "겨울", 1: "겨울", 2: "겨울", 3: "봄", 4: "봄", 5: "봄",
          6: "여름", 7: "여름", 8: "여름", 9: "가을", 10: "가을", 11: "가을"}


def year_ago_window(periods, test_period, spread=1):
    """1년 전 같은 달 ±`spread`달에 해당하는 기간 라벨들 (수정안 34).

    **왜 ±로 넓히는가**: 작년 같은 달 하나면 표본이 14~19일(평일)뿐이고,
    12개월 자료에 구멍이 있어(25년 02·03·12월) 아예 없을 수도 있다.
    앞뒤 달을 붙이면 계절은 거의 유지하면서 표본이 세 배가 된다.

    ⚠️ **계획 대상 달보다 미래인 기간은 넣지 않는다.** 작년 기준으로는 과거라도
    표본 밖 원칙이 깨지는 것은 아니지만, 운영 시점에 손에 없는 자료를 쓰면
    안 되므로 `past` 안에서만 고른다(호출부가 걸러 넘긴다).
    """
    target = abs_month(test_period) - 12
    return [p for p in periods if abs(abs_month(p) - target) <= spread]


def level_ratio(stats, reference_frames):
    """`stats`의 수준을 `reference_frames`(최근 자료)의 수준에 맞추는 배율.

    **두 축을 나누는 장치다** (수정안 34).
      · 계절의 **모양**(대여소별 상대 크기·분산)은 1년 전 같은 시기에서
      · 시스템의 **수준**(대여소 신설·이용자 증가)은 최근 자료에서

    warmup_scale과 같은 방식으로 도시 전체 하나의 배율만 쓰고 0.5~2.0으로 자른다 —
    대여소별로 맞추면 최근 자료의 잡음까지 그대로 옮겨 온다.
    """
    if stats.empty or not reference_frames:
        return stats
    recent = pd.concat(reference_frames, ignore_index=True)
    if recent.empty:
        return stats
    observed = recent.groupby("station_id")["demand"].mean()
    joined = stats.join(observed.rename("recent"), how="inner").dropna()
    baseline = joined["mu"].abs().sum()
    if baseline <= 0:
        return stats
    ratio = float(np.clip(joined["recent"].abs().sum() / baseline, 0.5, 2.0))
    scaled = stats.copy()
    scaled[["mu", "sigma"]] *= ratio
    return scaled


def fit(frames):
    """여러 달을 합쳐 대여소별 mu/sigma. seasonal_window.py와 같은 계산이다."""
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=["mu", "sigma"])
    return (pd.concat(frames).groupby("station_id")["demand"]
            .agg(mu="mean", sigma="std").fillna(0))


def fit_weighted(frame):
    """가중 mu/sigma (소프트스플릿). `w` 열이 있어야 한다.

    가중치를 **빈도가 아니라 신뢰도**로 본다 — 표본이 늘었다고 치지 않는다.
    sum(w)로 나누고 유효표본수(neff)로 편향만 보정한다. 빈도로 보면 sigma가
    작아져 커버리지가 거짓으로 좋아진다.
    """
    def agg(g):
        w = g["w"].to_numpy(dtype=float)
        x = g["demand"].to_numpy(dtype=float)
        sw = w.sum()
        if sw <= 0:
            return pd.Series({"mu": 0.0, "sigma": 0.0})
        mu = float((w * x).sum() / sw)
        neff = sw ** 2 / float((w ** 2).sum())
        if neff <= 1:
            return pd.Series({"mu": mu, "sigma": 0.0})
        var = float((w * (x - mu) ** 2).sum() / sw) * neff / (neff - 1)
        return pd.Series({"mu": mu, "sigma": float(np.sqrt(max(var, 0.0)))})

    return frame.groupby("station_id")[["demand", "w"]].apply(agg)


def warmup_scale(stats, test, days=14):
    """계획 달 첫 `days`일 실적으로 도시 전체 배율을 구해 곱한다.

    운영 코드(`demand_model.season_ratio`)와 같은 방식이다 — 대여소별이 아니라
    도시 전체 하나, 그리고 0.5~2.0으로 자른다.
    """
    if stats.empty or test.empty:
        return stats
    dates = pd.to_datetime(test["date"])
    head = test[dates <= dates.min() + pd.Timedelta(days=days - 1)]
    observed = head.groupby("station_id")["demand"].mean()
    joined = stats.join(observed.rename("head"), how="inner").dropna()
    baseline = joined["mu"].abs().sum()
    if baseline <= 0:
        return stats
    ratio = float(np.clip(joined["head"].abs().sum() / baseline, 0.5, 2.0))
    scaled = stats.copy()
    scaled[["mu", "sigma"]] *= ratio
    return scaled


def measure(stats, test, z, fixed_buffer=None):
    """작업 대상 대여소에서만 잰다 — 전체 평균은 결론을 뒤집는다.

    **커버리지는 두 번 잰다.**

    · `coverage`  z*sigma를 그대로 쓴다. 방법마다 여유분이 다르다.
    · `cov_fixed` 여유분을 **모든 방법에 똑같이** 고정하고 잰다.

    둘을 나눠 재는 이유: sigma를 키우면 커버리지는 저절로 오른다. 그건 예측이
    좋아진 것이 아니라 **목표재고에 여유를 더 붙인 것**이고, 그만큼 작업량이
    늘어난다(z를 올린 것과 구분이 안 된다). 여유분을 맞춰 놓고도 이기는지를
    봐야 **중심이 옳게 옮겨졌는지** 알 수 있다 — 1.15.2에서 이걸 놓쳐
    되돌린 적이 있다(docs/분석/DEMAND_DISTRIBUTION.md 5장).
    """
    if stats.empty:
        return {}
    merged = test.merge(stats, on="station_id", how="inner")
    merged = merged[merged["demand"].abs() > REBAL_MIN_QTY]
    if len(merged) < 30:
        return {}
    error = merged["demand"] - merged["mu"]
    got = {
        "mae": float(error.abs().mean()),
        "bias": float(error.mean()),
        "coverage": float((merged["demand"].abs()
                           <= merged["mu"].abs() + z * merged["sigma"]).mean()),
        "buffer": float((z * merged["sigma"]).mean()),
        "n": int(len(merged)),
    }
    if fixed_buffer is not None:
        got["cov_fixed"] = float((merged["demand"].abs()
                                  <= merged["mu"].abs() + fixed_buffer).mean())
    return got


def month_temperature():
    """달별 평균 기온. 없으면 빈 dict (기온 가중을 건너뛴다)."""
    try:
        hourly = weather.load_hourly()
    except Exception as err:
        print("[안내] 날씨를 읽지 못해 기온 가중은 건너뜁니다: %s: %s"
              % (type(err).__name__, err))
        return {}
    if hourly.empty:
        return {}
    frame = hourly.copy()
    frame["key"] = frame["time"].dt.year % 100 * 100 + frame["time"].dt.month
    return frame.groupby("key")["temp"].mean().to_dict()


def main():
    parser = argparse.ArgumentParser(description="순수요 학습 구간을 1년치에서 어떻게 뽑나")
    parser.add_argument("--duration", help="시간대 하나만")
    parser.add_argument("--day-type", default=DAY_TYPES[0])
    parser.add_argument("--z", type=float, default=TARGET_Z)
    parser.add_argument("--half-life", type=float, default=3.0,
                        help="recency 감쇠 반감기(개월)")
    args, _ = parser.parse_known_args()

    day_type = normalize_day_type(args.day_type)
    durations = [args.duration] if args.duration else DURATIONS

    with db.session() as conn:
        periods = [r[0] for r in conn.execute(
            "SELECT DISTINCT period FROM net_demand").fetchall()]
        net = {}
        for p in periods:
            frame = select_day_type(db.load_frame(conn, "net_demand", period=p),
                                    "date", day_type)
            if not frame.empty:
                net[p] = frame

    periods = sorted(net, key=period_key)
    if len(periods) < 3:
        print("기간이 3개 미만이라 비교할 수 없습니다.")
        return 1

    temps = month_temperature()
    print("적재 기간 %d개 · z=%s · %s · 작업 대상만(|순수요|>%s)"
          % (len(periods), args.z, day_type, REBAL_MIN_QTY))
    print("기간: %s\n" % ", ".join(periods))

    rows = []
    for duration in durations:
        for test_period in periods:
            test = daily_window_demand(net[test_period], duration)
            if test.empty:
                continue
            prev = previous_month(test_period)
            if prev not in net:
                continue                # 직전 달이 없으면 베이스라인 자체가 없다

            past = [p for p in periods if period_key(p) < period_key(test_period)]
            if len(past) < 2:
                continue

            m = month_of(test_period)
            cand = {}

            def build(months):
                return [daily_window_demand(net[x], duration) for x in months]

            # ---- 베이스라인 (현행) ----
            cand["prev1"] = fit(build([prev]))
            cand["prev1+warm"] = warmup_scale(cand["prev1"], test)

            # ---- 하드스플릿 ----
            same = [p for p in past if month_of(p) == m]
            if same:
                cand["same_month"] = fit(build(same))
            season = [p for p in past if SEASON[month_of(p)] == SEASON[m]]
            if season:
                cand["season"] = fit(build(season))
            cand["all_past"] = fit(build(past))

            # ---- 월중 추세: 지난달 안에서 최근을 무겁게 ----
            # 한 달 안에서도 수준이 움직인다(후반/전반 비가 0.90~1.27).
            # 그렇다면 "지난달 평균"보다 "지난달 후반"이 나을 수 있다.
            # 선형 가중(첫날 1 → 마지막 날 2)으로 잰다. 후반만 쓰는 것은
            # 표본이 절반이 되어 더 나빴다(실측).
            recent = daily_window_demand(net[prev], duration)
            if not recent.empty:
                recent = recent.copy()
                recent["date"] = pd.to_datetime(recent["date"])
                days = sorted(recent["date"].unique())
                order = pd.Series(range(len(days)), index=days)
                recent["w"] = 1.0 + recent["date"].map(order) / max(1, len(days) - 1)
                cand["wmean+warm"] = warmup_scale(fit_weighted(recent), test)

            # ---- 수준을 맞춰 합치기 ----
            # "한 달치는 표본이 적다"는 지적에 대한 답. 옛 달을 넣되 **그 달의
            # 수준으로 나눠 정규화**한 뒤 합치고, 직전 달 수준으로 되돌린다.
            # 달마다 수요 수준이 2.49배까지 벌어지므로, 그냥 합치면 표본은 늘어도
            # 서로 다른 수준이 섞여 중심이 틀어진다. 그 수준 차이만 제거해 본다.
            pooled, ref = [], None
            for p in past:
                f = daily_window_demand(net[p], duration)
                if f.empty:
                    continue
                level = float(f["demand"].abs().mean())
                if level <= 0:
                    continue
                if p == prev:
                    ref = level
                scaled_frame = f.copy()
                scaled_frame["demand"] = scaled_frame["demand"] / level
                pooled.append(scaled_frame)
            if pooled and ref:
                cand["pooled_norm+warm"] = warmup_scale(fit(pooled) * ref, test)

            # ---- 두 축 분리 (수정안 34, 사용자 제안) ----
            # "계절 형태는 1년 전 같은 시기에서, 시스템 변화는 최근에서"
            #
            # 한 창에서 둘 다 가져오는 현행과 달리 **모양과 수준을 따로** 잡는다.
            #   모양 = 1년 전 같은 달 ±1달  (계절이 맞다)
            #   수준 = 직전 달              (대여소 신설·이용자 증가가 반영돼 있다)
            for spread in (0, 1, 2):
                window = year_ago_window(past, test_period, spread)
                if not window:
                    continue
                shape = fit(build(window))
                if shape.empty:
                    continue
                name = f"year±{spread}"
                cand[name] = shape
                # 수준만 직전 달에 맞춘다 — 두 축을 나눈 것이 이 줄이다
                cand[f"{name}+lvl"] = level_ratio(shape, build([prev]))
                # 거기에 계획 달 첫 14일 보정까지 (현행 베이스라인과 같은 장치)
                cand[f"{name}+lvl+warm"] = warmup_scale(cand[f"{name}+lvl"], test)

            # ---- 소프트스플릿 ----
            frames = []
            for p in past:
                f = daily_window_demand(net[p], duration)
                if f.empty:
                    continue
                f = f.copy()
                age = abs_month(test_period) - abs_month(p)
                f["w_rec"] = 0.5 ** (age / args.half_life)
                t_test = temps.get(period_key(test_period))
                t_p = temps.get(period_key(p))
                if t_test is not None and t_p is not None:
                    f["w_temp"] = float(np.exp(-((t_p - t_test) / 8.0) ** 2))
                else:
                    f["w_temp"] = 1.0
                frames.append(f)

            if frames:
                pool = pd.concat(frames, ignore_index=True)
                for name, col in (("recency", "w_rec"), ("temp", "w_temp")):
                    tmp = pool.copy()
                    tmp["w"] = tmp[col]
                    cand[name] = fit_weighted(tmp)
                tmp = pool.copy()
                tmp["w"] = tmp["w_rec"] * tmp["w_temp"]
                cand["recency+temp"] = fit_weighted(tmp)
                # 소프트스플릿에도 계절 보정을 얹는다 — 베이스라인과 같은 조건으로 겨룬다
                cand["recency+warm"] = warmup_scale(cand["recency"], test)
                cand["recency+temp+warm"] = warmup_scale(cand["recency+temp"], test)

            # 공정한 커버리지 비교의 자(尺)는 **베이스라인의 여유분**이다.
            # 먼저 베이스라인을 재서 그 값을 모든 방법에 똑같이 적용한다.
            ruler = measure(cand.get("prev1+warm", pd.DataFrame()), test, args.z)
            fixed = ruler.get("buffer") if ruler else None

            for name, stats in cand.items():
                got = measure(stats, test, args.z, fixed_buffer=fixed)
                if got:
                    rows.append(dict(duration=duration, period=test_period,
                                     method=name, **got))

    if not rows:
        print("비교할 조합이 없습니다.")
        return 1

    frame = pd.DataFrame(rows)

    print("=== 방법별 평균 (모든 시간대·달) ===")
    agg = dict(mae=("mae", "mean"), coverage=("coverage", "mean"),
               buffer=("buffer", "mean"), bias=("bias", "mean"),
               n=("n", "sum"), 달=("period", "nunique"))
    if "cov_fixed" in frame:
        agg["cov_fixed"] = ("cov_fixed", "mean")
    summary = frame.groupby("method").agg(**agg).sort_values("mae")
    print(summary.round(3).to_string())
    print("\n  coverage  = z*sigma 그대로 (여유분이 방법마다 다르다)")
    print("  buffer    = 목표재고에 붙는 여유분(z*sigma). 클수록 작업량이 는다")
    print("  cov_fixed = 여유분을 베이스라인과 **같게 맞추고** 잰 커버리지")

    if "prev1+warm" in summary.index:
        base = summary.loc["prev1+warm"]
        print("\n베이스라인(prev1+warm) MAE %.3f · 커버리지 %.3f · 여유분 %.2f대"
              % (base["mae"], base["coverage"], base["buffer"]))

        better = summary[(summary["mae"] < base["mae"]) &
                         (summary["coverage"] >= base["coverage"])]
        better = better.drop(index="prev1+warm", errors="ignore")
        if better.empty:
            print("→ MAE와 커버리지를 동시에 이긴 방법이 없습니다.")
        else:
            print("→ 겉보기로 둘 다 이긴 방법: %s" % ", ".join(better.index))

            # 여기서 한 번 더 거른다. 여유분이 늘었다면 그 커버리지는
            # 'sigma를 키워서 산 것'이라 z를 올린 것과 구분되지 않는다.
            print("\n  [여유분을 맞춰 다시 보기]")
            for name in better.index:
                row = summary.loc[name]
                grew = (row["buffer"] - base["buffer"]) / base["buffer"] * 100
                gap_raw = (row["coverage"] - base["coverage"]) * 100
                if "cov_fixed" in summary:
                    gap_fix = (row["cov_fixed"] - base["cov_fixed"]) * 100
                    print("  %-20s 커버리지 %+.1f%%p → 여유분 맞추면 %+.1f%%p"
                          " (여유분 %+.1f%%)" % (name, gap_raw, gap_fix, grew))
                    if gap_fix < 1.0:
                        print("      → 상승분의 대부분이 **sigma 확대**에서 왔습니다."
                              " 작업량만 늘고 예측은 그대로입니다.")
                else:
                    print("  %-20s 커버리지 %+.1f%%p (여유분 %+.1f%%)"
                          % (name, gap_raw, grew))

    print("\n=== 시간대별 MAE ===")
    print(frame.pivot_table(index="method", columns="duration",
                            values="mae").round(3).to_string())

    print("\n=== 계절 전환 달만 (3·4·9·10월) ===")
    turn = frame[frame["period"].map(lambda p: month_of(p) in (3, 4, 9, 10))]
    if turn.empty:
        print("해당 달이 없습니다.")
    else:
        # 여기서도 여유분을 맞춘 커버리지를 함께 찍는다 — 전환기라고 해서
        # sigma를 키워 산 커버리지가 진짜가 되지는 않는다.
        tagg = dict(mae=("mae", "mean"), coverage=("coverage", "mean"),
                    buffer=("buffer", "mean"))
        if "cov_fixed" in turn:
            tagg["cov_fixed"] = ("cov_fixed", "mean")
        print(turn.groupby("method").agg(**tagg)
              .sort_values("mae").round(3).to_string())

    # ── 같은 달끼리만 비교 (수정안 34) ────────────────────────────────
    #
    # ⚠️ **위 표들은 방법마다 검증 달 수가 다르다.** `year±1`은 1년 전 자료가
    # 필요한데 12개월에 구멍이 있어(25년 02·03·12월) 9개 달 중 2개에서만
    # 계산된다. 그 상태로 평균을 나란히 놓으면 **다른 자를 견주는 것**이다.
    #
    # 그래서 **모든 방법이 값을 낸 (달·회차)만** 골라 다시 잰다. 표본은 줄지만
    # 비교는 공정해진다.
    print("\n=== 같은 달끼리만 (모든 방법이 값을 낸 달) ===")
    counts = frame.groupby("method")["period"].nunique()
    if counts.nunique() == 1:
        print("모든 방법이 같은 달에서 계산됐습니다 — 위 표를 그대로 읽으십시오.")
    else:
        common = None
        for _, part in frame.groupby("method"):
            cells = set(zip(part["period"], part["duration"]))
            common = cells if common is None else (common & cells)
        if not common:
            print("모든 방법이 함께 값을 낸 (달·회차)가 없습니다.")
            print(f"  방법별 달 수: {dict(counts)}")
        else:
            mask = frame.apply(
                lambda r: (r["period"], r["duration"]) in common, axis=1)
            fair = frame[mask]
            fagg = dict(mae=("mae", "mean"), coverage=("coverage", "mean"),
                        buffer=("buffer", "mean"))
            if "cov_fixed" in fair:
                fagg["cov_fixed"] = ("cov_fixed", "mean")
            print(f"공통 (달·회차) {len(common)}개 — "
                  f"{', '.join(sorted({p for p, _ in common}))}")
            print(fair.groupby("method").agg(**fagg)
                  .sort_values("mae").round(3).to_string())
            print("\n  ⚠️ 표본이 작습니다. 이 표는 '어느 방법이 낫나'의 방향만"
                  " 보고, 크기는 위 표에서 읽으십시오.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
