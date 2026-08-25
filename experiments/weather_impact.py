"""날씨가 순수요를 실제로 설명하는가 — 붙이기 전에 재 본다.

`net_vs_volume.py`가 답한 것은 **전달 경로**였다: 이용량이 흔들리면 재배치 필요량도
같이 흔들린다(R² 0.88~0.96). 하지만 그것만으로는 날씨를 넣어야 한다는 근거가 못 된다.
날씨가 흔드는 것은 **이용량**이고, 이 파이프라인이 맞혀야 하는 것은 **순수요**다.
비가 와서 대여와 반납이 함께 반토막 나면 이용량은 급감해도 순수요는 그대로일 수 있다.

그래서 여기서는 한 칸 더 간다.

    ① 날씨 → 이용량      : 정말 흔드는가 (안 흔들면 자료가 이상한 것이다)
    ② 날씨 → 재배치 필요량 : 그게 순수요까지 오는가
    ③ 표본 밖             : 그 설명력이 새 날에도 남는가 (5겹 교차검증)

**남은 변동이 대상이다.** 달마다 이용량 수준이 다르고 평일/휴일이 다르므로, 그 둘을
먼저 빼고(달 평균으로 나누고, 평일·휴일을 따로 재고) 남은 변동만 본다. 그 남은 변동의
CV가 0.20~0.24이고, 날씨는 그것의 유력한 설명 변수로 지목돼 있었다
(docs/분석/DEMAND_DISTRIBUTION.md).

판정:

    표본 밖 R²가 0에 가깝거나 음수  → 붙이지 않는다. 지금까지와 같은 규칙이다.
    표본 밖 R²가 뚜렷하게 양수      → demand_model.build_features()에 붙일 값어치가 있다.

실행: python experiments/weather_impact.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

import db
import demand_model
import weather
from project_config import duration_hours, holiday_mask, select_day_type

WINDOWS = ("_05_10", "_10_15", "_15_20")
DAY_TYPES = ("weekday", "holiday")
RAIN_MM = weather.RAIN_MM   # '비 온 날' 문턱. 정의는 weather.py 하나다
FOLDS = 5
MIN_DEMAND = 2.0   # 작업 대상 대여소 문턱 (|mu| > 2) — 전체 평균은 결론을 뒤집는다
WARMUP_DAYS = 14   # 계절 보정. 베이스라인도 이걸 켜고 겨뤄야 공정하다


def daily_net(conn, period: str, hours) -> pd.DataFrame:
    """날짜별 재배치 필요량. 대여소별 순수요의 절댓값 합 = 그날 옮겨야 할 대수."""
    columns = ", ".join(f"net_{h:02d}" for h in hours)
    frame = pd.read_sql(
        f"SELECT date, station_id, {columns} FROM net_demand WHERE period = ?",
        conn, params=[period])
    if frame.empty:
        return frame
    frame["net"] = frame[[f"net_{h:02d}" for h in hours]].sum(axis=1)
    return frame.groupby("date").agg(
        need=("net", lambda s: s.abs().sum()),
        net_sum=("net", "sum"),
    ).reset_index()


def daily_volume(conn, period: str, hours) -> pd.DataFrame:
    """날짜별 이용량(대여 건수). 날씨가 직접 흔드는 값."""
    lo, hi = f"{min(hours):02d}", f"{max(hours):02d}"
    return pd.read_sql(
        "SELECT substr(rent_at, 1, 10) AS date, COUNT(*) AS volume"
        " FROM rental_history"
        " WHERE period = ? AND substr(rent_at, 12, 2) BETWEEN ? AND ?"
        " GROUP BY date ORDER BY date",
        conn, params=[period, lo, hi])


def build_table(conn, periods, duration: str, hourly: pd.DataFrame) -> pd.DataFrame:
    """(날짜 × 이용량·필요량·날씨) 표. 달 수준 차이는 여기서 뺀다."""
    hours = duration_hours(duration)
    frames = []
    for period in periods:
        net = daily_net(conn, period, hours)
        volume = daily_volume(conn, period, hours)
        if net.empty or volume.empty:
            continue
        frames.append(net.merge(volume, on="date"))
    if not frames:
        return pd.DataFrame()

    table = pd.concat(frames, ignore_index=True)
    table["date"] = pd.to_datetime(table["date"], errors="coerce")
    table = table.dropna(subset=["date"])

    window = weather.window_frame(hourly, duration)
    window["date"] = pd.to_datetime(window["date"])
    table = table.merge(window, on="date", how="inner")

    table["holiday"] = holiday_mask(table["date"])
    # 달마다 이용량 수준 자체가 다르다 — 수준을 빼고 '그 달 평균 대비'로 본다.
    month = table["date"].dt.to_period("M")
    for name in ("need", "volume"):
        table[f"{name}_ratio"] = table[name] / table.groupby(
            [month, table["holiday"]])[name].transform("mean")
    table["rainy"] = table["rain"] >= RAIN_MM
    return table


def month_dummies(table: pd.DataFrame) -> np.ndarray:
    """달마다 하나씩 세우는 수준 항 = **베이스라인**.

    지금 파이프라인이 쓰는 정보가 딱 이만큼이다 — 직전 달 통계로 이번 달 수준을 잡고,
    평일과 휴일을 따로 센다. 날씨는 이 위에 무엇을 더 얹는지로 판정해야 한다.
    """
    month = table["date"].dt.to_period("M").astype(str)
    return pd.get_dummies(month, drop_first=False).to_numpy(dtype=float)


def design(table: pd.DataFrame, kind: str) -> np.ndarray:
    """설명변수 행렬. 기온은 곡선(더워도 추워도 덜 탄다)이라 제곱항을 함께 둔다.

    **어느 행렬이든 달 수준 항을 깔고 간다.** 날씨만 넣은 모형과 겨루면 날씨가 계절
    수준까지 대신 설명해 버려서, 실제로 보태는 몫보다 크게 나온다.
    """
    base = month_dummies(table)
    if kind == "base":
        return base
    rain = np.log1p(table["rain"].to_numpy())
    rainy = table["rainy"].to_numpy(dtype=float)
    temp = table["temp"].to_numpy()
    wind = table["wind"].to_numpy()
    if kind == "rain":
        return np.column_stack([base, rainy, rain])
    if kind == "temp":
        return np.column_stack([base, temp, temp ** 2])
    return np.column_stack([base, rainy, rain, temp, temp ** 2, wind])


def r2(actual: np.ndarray, predicted: np.ndarray) -> float:
    """설명력. 평균만 쓰는 것보다 얼마나 나은가 — 음수면 평균보다 못하다는 뜻이다."""
    ss_tot = float(((actual - actual.mean()) ** 2).sum())
    ss_res = float(((actual - predicted) ** 2).sum())
    return 1 - ss_res / ss_tot if ss_tot else float("nan")


def fit_r2(matrix: np.ndarray, target: np.ndarray) -> float:
    """표본 안 설명력."""
    coef, *_ = np.linalg.lstsq(matrix, target, rcond=None)
    return r2(target, matrix @ coef)


def cv_r2(matrix: np.ndarray, target: np.ndarray, folds: int = FOLDS) -> float:
    """표본 밖 설명력 — 날짜를 섞어 겹으로 나눠, 못 본 날을 맞혀 본다.

    **이 저장소의 판정 기준이다.** 표본 안 R²는 변수를 더 넣기만 해도 오르므로
    아무것도 말해 주지 않는다(분위수 모델에서 실제로 겪었다).
    """
    if len(target) < folds * 3:
        return float("nan")
    order = np.random.default_rng(0).permutation(len(target))
    predicted = np.empty_like(target)
    for fold in range(folds):
        test = order[fold::folds]
        train = np.setdiff1d(order, test)
        coef, *_ = np.linalg.lstsq(matrix[train], target[train], rcond=None)
        predicted[test] = matrix[test] @ coef
    return r2(target, predicted)


def report(table: pd.DataFrame, duration: str, day_type: str) -> dict:
    """한 (시간대 × 요일 구분)을 잰다."""
    part = table[table["holiday"] == (day_type == "holiday")]
    if len(part) < 30:
        return {}

    row = {"duration": duration, "day_type": day_type, "n": len(part),
           "need_cv": float(part["need_ratio"].std()),
           "vol_cv": float(part["volume_ratio"].std())}

    # 표본 안 설명력은 '달 수준만' 쓴 것 대비 얼마나 늘었는지로 읽는다.
    for target_name, column in (("vol", "volume"), ("need", "need")):
        target = part[column].to_numpy(dtype=float)
        base_fit = fit_r2(design(part, "base"), target)
        row[f"{target_name}_base"] = base_fit
        for kind in ("rain", "temp", "all"):
            row[f"{target_name}_{kind}"] = fit_r2(design(part, kind), target) - base_fit

        base_oos = cv_r2(design(part, "base"), target)
        row[f"{target_name}_base_oos"] = base_oos
        row[f"{target_name}_oos"] = cv_r2(design(part, "all"), target)
        row[f"{target_name}_gain"] = row[f"{target_name}_oos"] - base_oos

    rainy, dry = part[part["rainy"]], part[~part["rainy"]]
    row["rainy_days"] = len(rainy)
    row["vol_gap"] = (float(rainy["volume_ratio"].mean() - dry["volume_ratio"].mean())
                      if len(rainy) >= 3 else float("nan"))
    row["need_gap"] = (float(rainy["need_ratio"].mean() - dry["need_ratio"].mean())
                       if len(rainy) >= 3 else float("nan"))
    return row


def ratio_model(demand_by_period: dict, periods, hourly, duration: str):
    """도시 전체 **하루 배율**을 날씨로 맞히는 모형을 적합한다.

    붙이는 방식을 계절 배율(`season_ratio`)과 똑같이 잡았다 — 대여소별로 날씨를
    나누지 않고 **도시 하나의 배율**을 mu에 곱한다. 며칠치로 대여소를 쪼개면 잡음만
    커진다는 것은 계절 보정에서 이미 겪었고, 관측소도 대전에 하나뿐이다.

    적합에 쓰는 것은 **검증 달보다 앞선 달들뿐**이다(운영에서 그 시점에 손에 있는 자료).
    """
    rows = []
    for period in periods:
        demand = demand_by_period.get(period)
        if demand is None or demand.empty:
            continue
        daily = demand.groupby("date")["demand"].apply(lambda s: s.abs().sum())
        if daily.empty or not daily.mean():
            continue
        frame = pd.DataFrame({"date": pd.to_datetime(daily.index),
                              "ratio": (daily / daily.mean()).to_numpy()})
        rows.append(frame)
    if not rows:
        return None

    table = pd.concat(rows, ignore_index=True)
    window = weather.window_frame(hourly, duration)
    window["date"] = pd.to_datetime(window["date"])
    table = table.merge(window, on="date", how="inner")
    if len(table) < 30:
        return None

    table["rainy"] = table["rain"] >= RAIN_MM
    matrix = np.column_stack([np.ones(len(table)), *_weather_terms(table)])
    coef, *_ = np.linalg.lstsq(matrix, table["ratio"].to_numpy(), rcond=None)
    return coef


def _weather_terms(table: pd.DataFrame) -> list:
    """배율 모형의 설명변수. 달 수준 항은 여기 없다 — 배율 자체가 이미 수준을 뺀 값이다."""
    return [table["rainy"].to_numpy(dtype=float), np.log1p(table["rain"].to_numpy()),
            table["temp"].to_numpy(), table["temp"].to_numpy() ** 2,
            table["wind"].to_numpy()]


def predict_ratio(coef, hourly, duration: str) -> pd.Series:
    """날짜 → 배율. 0.2~2.0으로 자른다(외삽으로 음수 배율이 나오면 계획이 뒤집힌다)."""
    window = weather.window_frame(hourly, duration)
    window["rainy"] = window["rain"] >= RAIN_MM
    matrix = np.column_stack([np.ones(len(window)), *_weather_terms(window)])
    return pd.Series(np.clip(matrix @ coef, 0.2, 2.0),
                     index=pd.to_datetime(window["date"]))


def station_level(conn, hourly, duration: str, day_type: str) -> list:
    """**작업 대상 대여소에서** 날씨 배율이 예측을 개선하는지 잰다.

    이 저장소의 채택 기준을 그대로 쓴다 — 작업 대상만(|mu| > 2), 평일/휴일 따로,
    직전 달로만 학습(표본 밖), 계절 보정을 켠 베이스라인과 겨룬다. 도시 총량이
    설명된다고 대여소별 예측이 그만큼 좋아지지는 않는다. 전체 평균으로 재서
    정반대 결론을 낸 적이 있다(docs/분석/EXPERIMENTS.md 2장).
    """
    periods = sorted({r[0] for r in conn.execute(
        "SELECT DISTINCT period FROM net_demand")}, key=demand_model.month_index)
    net = {p: select_day_type(db.load_frame(conn, "net_demand", period=p),
                              "date", day_type) for p in periods}
    demand = {p: demand_model.window_demand(frame, duration)
              for p, frame in net.items() if not frame.empty}
    periods = [p for p in periods if p in demand and not demand[p].empty]

    rows = []
    for train_period, test_period in zip(periods, periods[1:]):
        if demand_model.month_index(test_period) - demand_model.month_index(train_period) \
                not in (1, 89):   # 12월 → 1월
            continue
        train, test = demand[train_period], demand[test_period]

        stats = train.groupby("station_id")["demand"].agg(mu="mean", sigma="std").fillna(0)
        stats = demand_model.apply_warmup(
            stats, demand_model.season_ratio(train, test, WARMUP_DAYS))
        stats = stats[stats["mu"].abs() > MIN_DEMAND].reset_index()
        if stats.empty:
            continue

        merged = test.merge(stats, on="station_id", how="inner")
        if merged.empty:
            continue

        # 배율 모형은 **검증 달을 뺀** 앞선 달들로만 적합한다.
        earlier = [p for p in periods
                   if demand_model.month_index(p) <= demand_model.month_index(train_period)]
        coef = ratio_model(demand, earlier, hourly, duration)
        if coef is None:
            continue
        ratio = predict_ratio(coef, hourly, duration)
        merged["ratio"] = pd.to_datetime(merged["date"]).map(ratio).fillna(1.0)

        merged["base_err"] = (merged["demand"] - merged["mu"]).abs()
        merged["weather_err"] = (merged["demand"] - merged["mu"] * merged["ratio"]).abs()

        # 비 온 날에 몰린 개선인지 갈라 본다. 운영에서는 이쪽이 더 중요하다 —
        # 1년에 며칠뿐인 날을 크게 고치는 것과, 매일 조금씩 고치는 것은 다른 처방이다.
        window = weather.window_frame(hourly, duration)
        rainy_dates = set(window.loc[window["rain"] >= RAIN_MM, "date"])
        is_rainy = pd.to_datetime(merged["date"]).dt.strftime("%Y-%m-%d").isin(rainy_dates)

        def gain_of(part):
            if part.empty or not part["base_err"].mean():
                return float("nan")
            return float((1 - part["weather_err"].mean() / part["base_err"].mean()) * 100)

        base = float(merged["base_err"].mean())
        adjusted = float(merged["weather_err"].mean())
        rows.append({"duration": duration, "day_type": day_type, "test": test_period,
                     "stations": int(merged["station_id"].nunique()),
                     "mae": base, "mae_weather": adjusted,
                     "gain": (base - adjusted) / base * 100 if base else float("nan"),
                     "rainy_gain": gain_of(merged[is_rainy]),
                     "dry_gain": gain_of(merged[~is_rainy]),
                     "rainy_share": float(is_rainy.mean())})
    return rows


def main() -> int:
    hourly = weather.load_hourly()
    if hourly.empty:
        print("날씨 자료가 없습니다. data/raw_data/날씨에 CSV를 두세요 (docs/분석/WEATHER.md).")
        return 1
    print(f"날씨 자료 {len(hourly):,}시간 "
          f"({hourly['time'].min():%Y-%m-%d} ~ {hourly['time'].max():%Y-%m-%d})\n")

    with db.session() as conn:
        periods = [r[0] for r in conn.execute(
            "SELECT DISTINCT period FROM net_demand ORDER BY period")]
        rows = []
        for duration in WINDOWS:
            table = build_table(conn, periods, duration, hourly)
            if table.empty:
                continue
            for day_type in DAY_TYPES:
                if (row := report(table, duration, day_type)):
                    rows.append(row)

    if not rows:
        print("겹치는 날짜가 없습니다. tools/load_rentals.py로 대여이력을 적재하세요.")
        return 1
    summary = pd.DataFrame(rows)

    print("① 날씨 → 이용량. 날씨가 자전거 이용을 흔드는가")
    print("   (R²는 '달 수준만 쓴 것'에 날씨를 더해 **늘어난 몫**, 표본 안)")
    print(f"{'시간대':8} {'구분':8} {'일수':>4} {'비온날':>5} "
          f"{'+강수':>7} {'+기온':>7} {'+전체':>7} {'비 온 날 이용량':>14}")
    for _, r in summary.iterrows():
        print(f"{r['duration']:8} {r['day_type']:8} {r['n']:4.0f} {r['rainy_days']:5.0f} "
              f"{r['vol_rain']:7.3f} {r['vol_temp']:7.3f} {r['vol_all']:7.3f} "
              f"{r['vol_gap'] * 100:+13.1f}%")

    print("\n② 날씨 → 재배치 필요량. 그게 순수요까지 오는가")
    print(f"{'시간대':8} {'구분':8} {'남은변동 CV':>11} "
          f"{'+강수':>7} {'+기온':>7} {'+전체':>7} {'비 온 날 필요량':>14}")
    for _, r in summary.iterrows():
        print(f"{r['duration']:8} {r['day_type']:8} {r['need_cv']:11.3f} "
              f"{r['need_rain']:7.3f} {r['need_temp']:7.3f} {r['need_all']:7.3f} "
              f"{r['need_gap'] * 100:+13.1f}%")

    print(f"\n③ 표본 밖 R² ({FOLDS}겹 교차검증) — **채택 판정은 이 숫자로 한다**")
    print("   달 수준만 = 지금 파이프라인이 쓰는 정보. 그 위에 날씨가 무엇을 보태는가")
    print(f"{'시간대':8} {'구분':8} {'달 수준만':>9} {'+날씨':>8} {'보탠 몫':>8}")
    for _, r in summary.iterrows():
        print(f"{r['duration']:8} {r['day_type']:8} {r['need_base_oos']:9.3f} "
              f"{r['need_oos']:8.3f} {r['need_gain']:+8.3f}")

    gain = summary["need_gain"].mean()
    print(f"\n평균 — 달 수준만 {summary['need_base_oos'].mean():.3f}, "
          f"날씨까지 {summary['need_oos'].mean():.3f} (보탠 몫 {gain:+.3f})")
    print("\n판정 기준")
    print("  보탠 몫 < 0.05  → 붙이지 않는다. 날씨는 이용량을 흔들지만 순수요까지 오지 않는다.")
    print("  보탠 몫 > 0.15  → demand_model.build_features()에 붙일 값어치가 있다.")
    print("\n④ 대여소 단위 표본 밖 판정 — **채택은 결국 이 숫자로 한다**")
    print(f"   작업 대상만(|mu| > {MIN_DEMAND:.0f}) · "
          f"계절 보정 {WARMUP_DAYS}일을 켠 베이스라인과 비교")
    station_rows = []
    with db.session() as conn:
        for duration in WINDOWS:
            for day_type in DAY_TYPES:
                station_rows.extend(station_level(conn, hourly, duration, day_type))

    if station_rows:
        detail = pd.DataFrame(station_rows)
        print(f"{'시간대':8} {'구분':8} {'검증 달':>6} {'대여소':>6} "
              f"{'MAE(현행)':>9} {'MAE(+날씨)':>10} {'전체':>7} {'비 온 날':>8} {'맑은 날':>8}")
        for (duration, day_type), group in detail.groupby(["duration", "day_type"]):
            print(f"{duration:8} {day_type:8} {len(group):6d} "
                  f"{group['stations'].mean():6.0f} {group['mae'].mean():9.3f} "
                  f"{group['mae_weather'].mean():10.3f} {group['gain'].mean():+6.1f}% "
                  f"{group['rainy_gain'].mean():+7.1f}% {group['dry_gain'].mean():+7.1f}%")
        print(f"\n전체 평균 개선 {detail['gain'].mean():+.1f}% "
              f"(검증 달 {len(detail)}건 중 개선 {int((detail['gain'] > 0).sum())}건)")
        print(f"비 온 날 {detail['rainy_share'].mean() * 100:.0f}%에서 "
              f"{detail['rainy_gain'].mean():+.1f}%, "
              f"나머지 날에서 {detail['dry_gain'].mean():+.1f}%")
    else:
        print("   비교할 연속 달이 없습니다.")

    print("\n※ 여기서 쓴 것은 **관측값**이다. 계획은 하루 앞서 세우므로 실제로 쓰려면")
    print("   그 자리에 **예보**가 들어간다 — 예보 오차만큼 이 숫자는 줄어든다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
