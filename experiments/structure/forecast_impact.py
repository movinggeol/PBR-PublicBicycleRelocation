"""날씨 배율을 예보로 재현할 수 있는가 — 예보에는 mm이 없다 (2026-08-29).

`weather_impact.py` ④(대여소 단위 표본 밖 판정, **채택은 결국 이 숫자로 한다**)는
관측 강수량(mm, log1p 변환)·강수유무·기온·풍속으로 날짜별 배율을 학습해
+4.3%(비 온 날 +40.3%)를 얻었다. 그런데 그건 **관측값**이고, 운영에서 실제로
쓸 수 있는 것은 **예보**다. 활용신청한 단기육상예보(`fct_afs_dl.php`)는 mm이
아니라 **강수확률(%)·강수유무코드**뿐이다(WEATHER.md 1-3장).

이 스크립트는 mm 항 자리에 예보 확률·유무코드를 넣어도 같은 효과가 남는지를
잰다. 판정 규칙은 ④와 똑같다 — 작업 대상만(|mu| > 2) · 평일/휴일 따로 ·
표본 밖(직전 달로만 학습) · 계절 보정 켠 베이스라인과 비교. **재구현이 아니라
`weather_impact.py`의 검증된 함수를 그대로 가져다 쓴다** — 이 저장소는
"재구현한 비교는 반드시 어긋난다"를 실제로 겪었다(EXPERIMENTS.md 6장).

예보는 12시간 단위(00~12시/12~24시)라 5시간 창과 정확히 안 맞는다 — 창의
시간대 다수가 걸리는 절반을 쓴다. "하루 앞서 세운다"는 운영 조건을 그대로
반영해, 대상 날짜 D의 예보는 **D-1에 발표된 것 중 가장 늦은 발표**만 쓴다.

실행: python experiments/structure/forecast_impact.py
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
import weather
import weather_impact as wi          # 관측 기준 station_level()을 그대로 재사용
from project_config import duration_hours, select_day_type

WINDOWS = wi.WINDOWS
DAY_TYPES = wi.DAY_TYPES
MIN_DEMAND = wi.MIN_DEMAND
WARMUP_DAYS = wi.WARMUP_DAYS


def period_bounds(period: str):
    """'25년 11월' → (그 달 1일, 말일)."""
    year_txt, month_txt = period.split("년")
    year = int(year_txt.strip())
    month = int(month_txt.replace("월", "").strip())
    start = pd.Timestamp(year=year, month=month, day=1)
    end = start + pd.offsets.MonthEnd(0)
    return start, end


def load_all_forecasts(periods) -> pd.DataFrame:
    """net_demand에 있는 달들의 예보 발표문을 전부 받는다(달마다 한 번 호출)."""
    frames = []
    for period in periods:
        start, end = period_bounds(period)
        got = weather.fetch_forecast(tmfc1=start.strftime("%Y%m%d0000"),
                                     tmfc2=end.strftime("%Y%m%d2359"))
        if not got.empty:
            frames.append(got)
    if not frames:
        return pd.DataFrame()
    forecast = pd.concat(frames, ignore_index=True)
    # 이미 지나간 구간의 잔여 정보(TM_EF <= TM_FC)는 버린다 — 값이 -99로 빈다.
    forecast = forecast[forecast["valid_at"] > forecast["issued_at"]]
    return forecast.drop_duplicates(subset=["issued_at", "valid_at"]).reset_index(drop=True)


def day_ahead_table(forecast: pd.DataFrame, duration: str) -> pd.DataFrame:
    """대상 날짜별로 **그 전날 마지막 발표**에서 그 창을 덮는 반나절 구간을 뽑는다.

    발효시각(TM_EF)이 자정(0시)이면 그 값은 **전날 12~24시** 구간이고, 정오(12시)면
    **그날 0~12시** 구간이다(실측 확인). 5시간 창의 시간이 둘 중 어디에 더 많이
    걸리는지로 하나를 고른다 — 정확한 매핑이 아니라 자릿수 근사다.
    """
    hours = duration_hours(duration)
    use_am_block = sum(h < 12 for h in hours) >= sum(h >= 12 for h in hours)

    frame = forecast.copy()
    frame["issued_date"] = frame["issued_at"].dt.normalize()
    am_block = frame["valid_at"].dt.hour == 12
    pm_block = frame["valid_at"].dt.hour == 0
    frame = frame[am_block | pm_block].copy()

    frame["target_date"] = frame["valid_at"].dt.normalize()
    frame.loc[pm_block, "target_date"] -= pd.Timedelta(days=1)
    frame["is_am_block"] = am_block

    block = frame[frame["is_am_block"] == use_am_block].copy()
    # 대상일 D는 D-1에 발표된 것만 쓴다 — 계획을 하루 앞서 세우는 운영 조건 그대로.
    block = block[block["issued_date"] == block["target_date"] - pd.Timedelta(days=1)]
    block = block.sort_values("issued_at").drop_duplicates("target_date", keep="last")
    return block[["target_date", "rain_prob", "rain_type", "temp"]].reset_index(drop=True)


def _forecast_terms(table: pd.DataFrame) -> list:
    """mm(log1p) 자리에 강수확률(%)을, 강수유무(mm 문턱) 자리에 강수유무코드를 넣는다."""
    rainy = (table["rain_type"] != "0").to_numpy(dtype=float)
    prob = table["rain_prob"].to_numpy(dtype=float)
    temp = table["temp"].to_numpy(dtype=float)
    return [rainy, prob, temp, temp ** 2]


def fit_ratio_forecast(demand_by_period: dict, periods, table: pd.DataFrame):
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
    matrix = np.column_stack([np.ones(len(merged)), *_forecast_terms(merged)])
    coef, *_ = np.linalg.lstsq(matrix, merged["ratio"].to_numpy(), rcond=None)
    return coef


def predict_ratio_forecast(coef, table: pd.DataFrame) -> pd.Series:
    matrix = np.column_stack([np.ones(len(table)), *_forecast_terms(table)])
    return pd.Series(np.clip(matrix @ coef, 0.2, 2.0),
                     index=pd.to_datetime(table["target_date"]))


def station_level_forecast(conn, table: pd.DataFrame, duration: str, day_type: str) -> list:
    """`weather_impact.station_level()`과 판정 규칙이 완전히 같다 — 배율 모형만 예보로 바꿨다."""
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
        coef = fit_ratio_forecast(demand, earlier, table)
        if coef is None:
            continue
        ratio = predict_ratio_forecast(coef, table)
        dates = pd.to_datetime(merged["date"])
        merged["ratio"] = dates.map(ratio).fillna(1.0)
        merged["has_forecast"] = dates.isin(ratio.index)

        merged["base_err"] = (merged["demand"] - merged["mu"]).abs()
        merged["weather_err"] = (merged["demand"] - merged["mu"] * merged["ratio"]).abs()

        rainy_dates = set(table.loc[table["rain_type"] != "0", "target_date"])
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


def summarize(rows, label):
    if not rows:
        print(f"   [{label}] 비교할 연속 달이 없습니다.")
        return
    detail = pd.DataFrame(rows)
    print(f"   [{label}]")
    print(f"   {'시간대':8} {'구분':8} {'검증 달':>6} {'대여소':>6} "
          f"{'MAE(현행)':>9} {'MAE(+날씨)':>10} {'전체':>7} {'비 온 날':>8} {'맑은 날':>8} "
          f"{'예보 커버':>8}")
    for (duration, day_type), group in detail.groupby(["duration", "day_type"]):
        cov = group["coverage"].mean() * 100 if "coverage" in group else float("nan")
        print(f"   {duration:8} {day_type:8} {len(group):6d} "
              f"{group['stations'].mean():6.0f} {group['mae'].mean():9.3f} "
              f"{group['mae_weather'].mean():10.3f} {group['gain'].mean():+6.1f}% "
              f"{group['rainy_gain'].mean():+7.1f}% {group['dry_gain'].mean():+7.1f}% "
              f"{cov:7.0f}%")
    print(f"\n   전체 평균 개선 {detail['gain'].mean():+.1f}% "
          f"(검증 달 {len(detail)}건 중 개선 {int((detail['gain'] > 0).sum())}건)")
    if "coverage" in detail:
        print(f"   평균 예보 커버리지 {detail['coverage'].mean() * 100:.0f}% "
              f"(D-1 발표가 없는 날은 배율 1.0으로 남는다 — 개선을 과소평가시킨다)")
    print(f"   비 온 날 {detail['rainy_share'].mean() * 100:.0f}%에서 "
          f"{detail['rainy_gain'].mean():+.1f}%, "
          f"나머지 날에서 {detail['dry_gain'].mean():+.1f}%")


def main() -> int:
    with db.session() as conn:
        periods = [r[0] for r in conn.execute(
            "SELECT DISTINCT period FROM net_demand ORDER BY period")]

    print(f"대상 {len(periods)}개월: {', '.join(periods)}")
    forecast = load_all_forecasts(periods)
    if forecast.empty:
        print("예보 자료를 못 받았습니다. .env의 APIHUN_KMA_TYPE01_KEY와 활용신청을 확인하세요.")
        return 1
    print(f"예보 발표문 {len(forecast)}줄 받음 "
          f"({forecast['issued_at'].min():%Y-%m-%d} ~ {forecast['issued_at'].max():%Y-%m-%d})\n")

    hourly = weather.load_hourly()

    with db.session() as conn:
        for duration in WINDOWS:
            table = day_ahead_table(forecast, duration)
            print(f"=== {duration} — 예보 표본 {len(table)}일 (D-1 발표 기준) ===")
            for day_type in DAY_TYPES:
                fc_rows = station_level_forecast(conn, table, duration, day_type)
                obs_rows = wi.station_level(conn, hourly, duration, day_type)
                print(f" - {day_type}")
                summarize(obs_rows, "관측(mm) 기준 — 참고용, WEATHER.md 4장과 같은 값")
                summarize(fc_rows, "예보(확률·유무) 기준 — 이번에 재는 것")
            print()

    print("판정: 예보 기준 개선률이 관측 기준(+4.3%, 비 온 날 +40.3%)에 크게 못 미치면")
    print("      확률·유무만으로는 부족하다는 뜻이다 — 그때 정량 예보(격자 수치예보)를 검토한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
