"""예보에 강수'량'(mm)을 넣으면 관측 수준에 가까워지는가 (2026-08-29).

`forecast_impact.py`가 확률·유무만으로는 부족함을 쟀다(전체 -0.7~+0.6%, 관측
+2.9~+6.7%. WEATHER.md 1-3장). 이번에 새로 활용신청한 단기예보 격자
(`nph-dfs_shrt_grd`)는 PCP(1시간 강수량, mm)를 준다 — 관측과 **같은 단위**다.

문장 예보(`fct_afs_dl.php`)의 확률(rain_prob) 자리만 이 격자 강수량(log1p)으로
바꿔 넣는다. 나머지(기온)는 그대로 문장 예보에서 쓴다 — 격자에서 기온까지 받으면
호출이 두 배가 되는데, 이 실험이 확인하려는 것은 '강수 항을 mm으로 바꾸면
달라지는가' 하나이므로 다른 항은 건드리지 않는다. 판정 규칙은 ④·
forecast_impact.py와 완전히 같다. **재구현하지 않는다** — 문장 예보 파싱은
`forecast_impact.day_ahead_table()`을, 배율 학습·표본 밖 판정은 그 구조를
그대로 가져다 mm 항만 바꿨다.

발표시각은 D-1 23시(그날의 마지막 발표, 최대 정보)로 고정한다. 5시간 창 안의
시각마다(예: _05_10 → 05~09시) 한 번씩 격자를 받아 그 합을 창의 예보 강수량으로
쓴다 — apihub 격자 API는 **시각 하나·변수 하나만** 주므로(기간을 못 묶는다)
창 하나에 최대 5번 부른다.

⚠️ 3개 시간대 × 약 360일 × 최대 5시간 ≈ 5,000회 넘게 부른다 — 몇 분 걸린다.

실행: python experiments/structure/forecast_grid_impact.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import numpy as np
import pandas as pd

import db
import demand_model
import project_config
import weather
import weather_impact as wi          # 관측 기준 station_level()을 그대로 재사용
from forecast_impact import day_ahead_table, load_all_forecasts, summarize
from project_config import duration_hours, select_day_type

WINDOWS = wi.WINDOWS
DAY_TYPES = wi.DAY_TYPES
MIN_DEMAND = wi.MIN_DEMAND
WARMUP_DAYS = wi.WARMUP_DAYS


def grid_precip_table(dates, duration: str) -> pd.DataFrame:
    """대상 날짜마다 D-1 23시 발표로 그 창의 예보 강수량(mm) 합을 구한다.

    시각 하나가 실패(격자 밖·API 오류)하면 그 시각만 건너뛴다 — 나머지 시각의
    합으로 대신하고, 몇 시간을 받았는지는 `coverage`에 남긴다(전부 실패한 날은
    0행, 즉 표본에서 빠진다).
    """
    hours = duration_hours(duration)
    dates = sorted(pd.to_datetime(dates).unique())
    rows = []
    for i, date in enumerate(dates):
        date = pd.Timestamp(date)
        issued = date - pd.Timedelta(days=1)
        tmfc = issued.strftime("%Y%m%d23")
        total = 0.0
        ok_hours = 0
        for hour in hours:
            tmef = date.strftime("%Y%m%d") + f"{hour:02d}"
            try:
                grid = weather.fetch_grid(tmfc, tmef, var="PCP")
            except weather.WeatherError:
                continue
            value = weather.grid_value(grid, project_config.DEPOT_LAT, project_config.DEPOT_LON)
            if value is not None:
                total += value
                ok_hours += 1
        if ok_hours:
            rows.append({"target_date": date, "forecast_mm": total,
                        "coverage": ok_hours / len(hours)})
        if (i + 1) % 30 == 0:
            print(f"    격자 조회 {i + 1}/{len(dates)}일...")
    return pd.DataFrame(rows, columns=["target_date", "forecast_mm", "coverage"])


def _grid_terms(table: pd.DataFrame) -> list:
    """`weather_impact._weather_terms()`와 같은 자리 — mm만 관측 대신 예보다."""
    rain_mm = table["forecast_mm"].to_numpy(dtype=float)
    rainy = (rain_mm >= weather.RAIN_MM).astype(float)
    log_mm = np.log1p(rain_mm)
    temp = table["temp"].to_numpy(dtype=float)
    return [rainy, log_mm, temp, temp ** 2]


def fit_ratio_grid(demand_by_period: dict, periods, table: pd.DataFrame):
    rows = []
    for period in periods:
        demand = demand_by_period.get(period)
        if demand is None or demand.empty:
            continue
        daily = demand.groupby("date")["demand"].apply(lambda s: s.abs().sum())
        if daily.empty or not daily.mean():
            continue
        rows.append(pd.DataFrame({"date": pd.to_datetime(daily.index),
                                  "ratio": (daily / daily.mean()).to_numpy()}))
    if not rows:
        return None
    merged = pd.concat(rows, ignore_index=True).merge(
        table, left_on="date", right_on="target_date", how="inner")
    if len(merged) < 30:
        return None
    matrix = np.column_stack([np.ones(len(merged)), *_grid_terms(merged)])
    coef, *_ = np.linalg.lstsq(matrix, merged["ratio"].to_numpy(), rcond=None)
    return coef


def predict_ratio_grid(coef, table: pd.DataFrame) -> pd.Series:
    matrix = np.column_stack([np.ones(len(table)), *_grid_terms(table)])
    return pd.Series(np.clip(matrix @ coef, 0.2, 2.0),
                     index=pd.to_datetime(table["target_date"]))


def station_level_grid(conn, table: pd.DataFrame, duration: str, day_type: str) -> list:
    """`weather_impact.station_level()`과 판정 규칙이 완전히 같다 — 배율 모형만 바꿨다."""
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
                not in (1, 89):
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

        earlier = [p for p in periods
                   if demand_model.month_index(p) <= demand_model.month_index(train_period)]
        coef = fit_ratio_grid(demand, earlier, table)
        if coef is None:
            continue
        ratio = predict_ratio_grid(coef, table)
        dates = pd.to_datetime(merged["date"])
        merged["ratio"] = dates.map(ratio).fillna(1.0)
        merged["has_forecast"] = dates.isin(ratio.index)

        merged["base_err"] = (merged["demand"] - merged["mu"]).abs()
        merged["weather_err"] = (merged["demand"] - merged["mu"] * merged["ratio"]).abs()

        rainy_dates = set(table.loc[table["forecast_mm"] >= weather.RAIN_MM, "target_date"])
        is_rainy = dates.isin(rainy_dates)

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
                     "rainy_share": float(is_rainy.mean()),
                     "coverage": float(merged["has_forecast"].mean())})
    return rows


def main() -> int:
    with db.session() as conn:
        periods = [r[0] for r in conn.execute(
            "SELECT DISTINCT period FROM net_demand ORDER BY period")]
    print(f"대상 {len(periods)}개월: {', '.join(periods)}")

    forecast = load_all_forecasts(periods)  # 문장 예보 — 기온만 여기서 쓴다
    if forecast.empty:
        print("문장 예보를 못 받았습니다. .env의 APIHUN_KMA_TYPE01_KEY와 활용신청을 확인하세요.")
        return 1
    print(f"문장 예보 {len(forecast)}줄 받음\n")

    hourly = weather.load_hourly()

    with db.session() as conn:
        for duration in WINDOWS:
            cat_table = day_ahead_table(forecast, duration)
            print(f"=== {duration} — 격자 강수량 {len(cat_table)}일 요청 중 "
                  f"(D-1 23시 발표 기준) ===")
            grid_table = grid_precip_table(cat_table["target_date"], duration)
            table = cat_table.merge(grid_table, on="target_date", how="inner")
            print(f"    받은 날: {len(table)}일, 평균 격자 커버 "
                  f"{table['coverage'].mean() * 100 if len(table) else 0:.0f}%")
            for day_type in DAY_TYPES:
                fc_rows = station_level_grid(conn, table, duration, day_type)
                obs_rows = wi.station_level(conn, hourly, duration, day_type)
                print(f" - {day_type}")
                summarize(obs_rows, "관측(mm) 기준 — 참고용, WEATHER.md 4장과 같은 값")
                summarize(fc_rows, "예보 격자(mm) 기준 — 이번에 재는 것")
            print()

    print("판정: 격자 강수량을 넣은 개선률이 관측 기준(+4.3%, 비 온 날 +40.3%)에 근접하면")
    print("      정량 예보가 답이다 — 계획에 반영을 검토한다.")
    print("      여전히 못 미치면 12시간 단위 문장 예보와 무관하게, 격자 자체의")
    print("      D-1 예측 정확도(태풍·집중호우의 위치·강도 오차)가 한계로 본다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
