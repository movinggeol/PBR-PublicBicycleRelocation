"""출발 직전 관측으로 날씨 배율을 걸면 얼마나 남는가 (2026-11-08 등록, 결과 전에 기준을 적는다).

왜 다시 재나
    docs/분석/WEATHER.md 4장 ④는 날씨 배율에 **회차 5시간 동안 실제로 온 강수**(창 강수)를 썼다.
    계획 시각에는 아직 오지 않은 미래 값이라 '완벽한 예보'를 가정한 위쪽 끝이다(비 온 날 오차 −32%).
    예보로 다시 잰 두 실험(forecast_impact.py 08-29 · 격자 08-30)은 **전날(D-1) 발표**를 썼다.
    그런데 1.26.372(10-18)부터 계획은 재고 시각 + 30분이 드는 **곧 출발할 회차 하나**를 세운다
    (`project_config.auto_duration`). 사용자(11-08): *"재배치 계획은 하루 전이 아니라 계획 실행 전에
    만들고 실행하기에 비가 온다고 했다가 안 오는 경우가 적을 것이다"*. 즉 D-1 예보는 지금 운영보다
    비관적인 아래쪽 끝이었고, '자동 반영 안 함' 결정(08-25 · 08-30)도 그 전제 위에 있었다.

무엇을 묻나
    운영에서 출발 시각에 **실제로 손에 있는 정보**(직전 시각까지의 관측)만으로 배율을 걸면,
    위쪽 끝(창 강수)의 개선 중 얼마가 남는가. 그리고 출발 때는 비가 왔는데 회차 동안 그친
    '오경보' 회차에서 배송 대상의 목표 재고가 수요를 덜 덮게 되는가(결품 쪽 위험의 대리 지표).

어떻게 재나 (weather_impact.station_level과 같은 자, 날씨 칸만 바꾼다)
    - 작업 대상만(|mu| > 2) · 평일/휴일 따로 · 직전 달로 학습 · 계절 보정 14일을 켠 베이스라인.
    - 배율 = 도시 하나의 하루 배율을 mu에 곱한다. 배율 모형은 검증 달보다 앞선 달들로만 적합하고
      0.2~2.0으로 자른다(`_weather_terms` · 자르기 범위 모두 weather_impact 그대로).
    - 비교하는 날씨 칸 셋:
        현행      배율 없음
        창 관측   회차 5시간의 실제 강수 · 평균 기온 · 풍속 (위쪽 끝, 4장 ④와 같은 값)
        출발 관측 출발(회차 시작 − 30분) 직전 정시까지의 관측. 강수는 직전 3시간 합, 기온 · 풍속은
                  직전 정시 값. **주 변형이다.** 1시간 합 변형도 함께 찍는다.
    - 🔴 미래 정보가 섞이지 않게: 겨울(11~3월) 강수는 3시간 누적이라 `weather.load_hourly()`가 그 값을
      앞 3시간에 나눠 둔다. 나눈 값을 쓰면 출발 시각에 아직 발표 안 된 기록(예: 05~10시 회차의 06시
      누적)이 04시 칸에 들어온다. 출발 관측은 **나누기 전 원본 기록 중 직전 정시까지 발표된 것만**
      더한다. 그래서 겨울에는 1시간 합이 대개 0이고, 3시간 합이 주 변형이다.
    - 비 온 회차 = 창 강수 1mm 이상(`weather.RAIN_MM`). 출발 비 = 출발 직전 3시간 합 1mm 이상.
    - 커버리지 · 과잉은 배송 대상(mu > 2)에서 잰다. 목표 = mu × 배율 + z·sigma(z = TARGET_Z),
      덮음 = 그날 순수요 ≤ 목표. 파이프라인의 상한(주차면 × 1.5)은 이 표에 대여소 정보가 없어 생략한다.

채택 기준 (주 변형 = 출발 직전 3시간, 검증 달 × 시간대 × 요일 행의 평균)
    C1. 비 온 회차 MAE 개선 ≥ +10%            (위쪽 끝 −32% 중 의미 있는 몫이 남는가)
    C2. 맑은 회차 MAE 개선 ≥ −1%              (맑은 날을 망가뜨리지 않는가)
    C3. 맑은 회차 배송 대상 커버리지 하락 ≤ 1%p (현행 대비, 결품 쪽 위험)
    셋 다 통과 → 2단계(결품 시간 시뮬레이션, baseline_compare의 자)를 따로 등록할 후보.
    하나라도 실패 → 자동 반영을 다시 꺼내지 않는다. 화면 알림(1.26.25)은 그대로.
    오경보 회차(출발 비 · 창 맑음)의 커버리지는 기준 없이 따로 보고한다(2단계의 입력).
    1시간 변형 · 창 관측은 비교용이고 판정에 쓰지 않는다.

한계 (결과와 함께 밝힐 것)
    관측소 한 곳(대전 133) · 비 온 회차 표본이 시간대별로 적다 · 커버리지는 결품 시간이 아니다.

실행: python experiments/structure/weather_departure.py
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd

import db
import demand_model
import weather
import weather_impact as wi
from project_config import DAY_TYPES, DEFAULT_DEPART_LEAD_MINUTES, DEFAULT_WEATHER_FILE, PROJECT_ROOT, TARGET_Z, duration_start_hour, select_day_type

LEAD_MINUTES = DEFAULT_DEPART_LEAD_MINUTES   # 출발 = 회차 시작 − 30분(운영 기본값, auto_duration과 같은 값)
LOOKBACKS = (3, 1)           # 직전 3시간(주 변형) · 1시간
C1_RAINY, C2_DRY, C3_COVER = 10.0, -1.0, 1.0


def load_raw_hourly() -> pd.DataFrame:
    """겨울 3시간 누적을 **나누지 않은** 시간 자료. 출발 시각까지 발표된 기록만 더하려면 원본이어야 한다.

    `weather.load_hourly()`와 읽는 순서가 같고 `_spread_winter_rain`만 뺐다(빈칸 규칙은 `_fill_missing` 그대로).
    """
    path = Path(DEFAULT_WEATHER_FILE)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    files = sorted(path.glob("*.csv")) if path.is_dir() else ([path] if path.exists() else [])
    if not files:
        return pd.DataFrame(columns=list(weather.COLUMNS))
    frame = pd.concat([weather._rename(weather._read_one(f), f.name) for f in files], ignore_index=True)
    frame["time"] = pd.to_datetime(frame["time"], errors="coerce")
    frame = frame.dropna(subset=["time"]).sort_values("time")
    frame = frame.drop_duplicates(subset=["time"], keep="last").reset_index(drop=True)
    return weather._fill_missing(frame)


def departure_frame(raw: pd.DataFrame, duration: str, lookback: int) -> pd.DataFrame:
    """날짜 → 출발 시각에 손에 있는 관측. 강수는 직전 정시까지 `lookback`시간 합, 기온 · 풍속은 직전 정시 값.

    출발 = 회차 시작 − 30분이라 마지막으로 발표된 정시 관측은 (시작 − 1)시다. 05~10시 회차라면 04시 관측
    (03~04시 강수)까지가 손에 있다.
    """
    start = duration_start_hour(duration)
    last = (start * 60 - LEAD_MINUTES) // 60          # 직전 정시
    hours = [(last - k) % 24 for k in range(lookback)]
    part = raw[raw["time"].dt.hour.isin(hours)].copy()
    part["date"] = part["time"].dt.normalize()
    # 자정을 넘겨 거슬러 가면(시작이 이른 회차) 전날 몫이다 — 지금 세 회차는 해당하지 않지만 막아 둔다
    part.loc[part["time"].dt.hour > last, "date"] += pd.Timedelta(days=1)
    rain = part.groupby("date")["rain"].sum()
    at_last = raw[raw["time"].dt.hour == last].assign(date=lambda f: f["time"].dt.normalize())
    at_last = at_last.drop_duplicates("date").set_index("date")
    out = pd.DataFrame({"rain": rain}).join(at_last[["temp", "wind"]], how="inner").reset_index()
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    return out


def fit_ratio(demand: dict, periods, frame: pd.DataFrame):
    """`weather_impact.ratio_model`과 같은 적합. 날씨 칸만 바깥에서 받는다."""
    rows = []
    for period in periods:
        part = demand.get(period)
        if part is None or part.empty:
            continue
        daily = part.groupby("date")["demand"].apply(lambda s: s.abs().sum())
        if daily.empty or not daily.mean():
            continue
        rows.append(pd.DataFrame({"date": pd.to_datetime(daily.index), "ratio": (daily / daily.mean()).to_numpy()}))
    if not rows:
        return None
    table = pd.concat(rows, ignore_index=True)
    table = table.merge(frame.assign(date=pd.to_datetime(frame["date"])), on="date", how="inner")
    if len(table) < 30:
        return None
    table["rainy"] = table["rain"] >= weather.RAIN_MM
    matrix = np.column_stack([np.ones(len(table)), *wi._weather_terms(table)])
    coef, *_ = np.linalg.lstsq(matrix, table["ratio"].to_numpy(), rcond=None)
    return coef


def predict(coef, frame: pd.DataFrame) -> pd.Series:
    frame = frame.copy()
    frame["rainy"] = frame["rain"] >= weather.RAIN_MM
    matrix = np.column_stack([np.ones(len(frame)), *wi._weather_terms(frame)])
    return pd.Series(np.clip(matrix @ coef, 0.2, 2.0), index=pd.to_datetime(frame["date"]))


def evaluate(conn, hourly, raw, duration: str, day_type: str) -> list:
    frames = {"창 관측": weather.window_frame(hourly, duration)[["date", "rain", "temp", "wind"]]}
    for lb in LOOKBACKS:
        frames[f"출발 {lb}시간"] = departure_frame(raw, duration, lb)
    window_rain = frames["창 관측"].set_index(pd.to_datetime(frames["창 관측"]["date"]))["rain"]
    dep_rain = frames["출발 3시간"].set_index(pd.to_datetime(frames["출발 3시간"]["date"]))["rain"]

    periods = sorted({r[0] for r in conn.execute("SELECT DISTINCT period FROM net_demand")},
                     key=demand_model.month_index)
    periods = weather.covered_periods(periods, hourly)
    net = {p: select_day_type(db.load_frame(conn, "net_demand", period=p), "date", day_type) for p in periods}
    demand = {p: demand_model.window_demand(f, duration) for p, f in net.items() if not f.empty}
    periods = [p for p in periods if p in demand and not demand[p].empty]

    rows = []
    for train_p, test_p in zip(periods, periods[1:]):
        if demand_model.month_index(test_p) - demand_model.month_index(train_p) not in (1, 89):
            continue
        train, test = demand[train_p], demand[test_p]
        stats = train.groupby("station_id")["demand"].agg(mu="mean", sigma="std").fillna(0)
        stats = demand_model.apply_warmup(stats, demand_model.season_ratio(train, test, wi.WARMUP_DAYS))
        stats = stats[stats["mu"].abs() > wi.MIN_DEMAND].reset_index()
        merged = test.merge(stats, on="station_id", how="inner")
        if merged.empty:
            continue
        earlier = [p for p in periods if demand_model.month_index(p) <= demand_model.month_index(train_p)]
        dates = pd.to_datetime(merged["date"])
        merged["wet"] = dates.map(window_rain).fillna(0) >= weather.RAIN_MM        # 회차 동안 비
        merged["dep_wet"] = dates.map(dep_rain).fillna(0) >= weather.RAIN_MM       # 출발 때 비
        merged["base_err"] = (merged["demand"] - merged["mu"]).abs()
        drop = merged["mu"] > wi.MIN_DEMAND                                         # 배송 대상
        base_target = merged["mu"] + TARGET_Z * merged["sigma"]

        row = {"duration": duration, "day_type": day_type, "test": test_p,
               "wet_share": float(merged["wet"].mean()), "dep_wet_share": float(merged["dep_wet"].mean()),
               "false_alarm_share": float((merged["dep_wet"] & ~merged["wet"]).mean()),
               "miss_share": float((~merged["dep_wet"] & merged["wet"]).mean())}

        def gain(err, mask):
            part = merged[mask]
            if part.empty or not part["base_err"].mean():
                return float("nan")
            return float((1 - err[mask].mean() / part["base_err"].mean()) * 100)

        def cover(target, mask):
            m = mask & drop
            return float((merged.loc[m, "demand"] <= target[m]).mean() * 100) if m.any() else float("nan")

        def excess(target, mask):
            m = mask & drop
            return float((target[m] - merged.loc[m, "demand"]).clip(lower=0).mean()) if m.any() else float("nan")

        everyone = pd.Series(True, index=merged.index)
        false_alarm = merged["dep_wet"] & ~merged["wet"]
        for name, mask in (("all", everyone), ("wet", merged["wet"]), ("dry", ~merged["wet"]), ("fa", false_alarm)):
            row[f"base_cover_{name}"] = cover(base_target, mask)
            row[f"base_excess_{name}"] = excess(base_target, mask)
        for label, frame in frames.items():
            coef = fit_ratio(demand, earlier, frame)
            if coef is None:
                continue
            ratio = dates.map(predict(coef, frame)).fillna(1.0)
            err = (merged["demand"] - merged["mu"] * ratio).abs()
            target = merged["mu"] * ratio + TARGET_Z * merged["sigma"]
            row[f"{label}|all"] = gain(err, everyone)
            row[f"{label}|wet"] = gain(err, merged["wet"])
            row[f"{label}|dry"] = gain(err, ~merged["wet"])
            for name, mask in (("dry", ~merged["wet"]), ("wet", merged["wet"]), ("fa", false_alarm)):
                row[f"{label}|cover_{name}"] = cover(target, mask)
                row[f"{label}|excess_{name}"] = excess(target, mask)
        rows.append(row)
    return rows


def main() -> int:
    hourly, raw = weather.load_hourly(), load_raw_hourly()
    if hourly.empty or raw.empty:
        print("날씨 자료가 없습니다. data/raw_data/날씨에 CSV를 두세요 (docs/분석/WEATHER.md).")
        return 1
    with db.session() as conn:
        rows = [r for d in wi.WINDOWS for t in DAY_TYPES for r in evaluate(conn, hourly, raw, d, t)]
    if not rows:
        print("비교할 연속 달이 없습니다.")
        return 1
    df = pd.DataFrame(rows)
    labels = ["창 관측", "출발 3시간", "출발 1시간"]

    print(f"검증 행(달 × 시간대 × 요일) {len(df)}개 · 작업 대상 |mu| > {wi.MIN_DEMAND:.0f} · 계절 보정 {wi.WARMUP_DAYS}일\n")
    print("회차 구성 (대여소 × 날 기준 비율 평균)")
    print(f"  비 온 회차 {df['wet_share'].mean() * 100:.1f}% · 출발 때 비 {df['dep_wet_share'].mean() * 100:.1f}% · "
          f"오경보(출발 비 · 회차 맑음) {df['false_alarm_share'].mean() * 100:.1f}% · "
          f"놓침(출발 맑음 · 회차 비) {df['miss_share'].mean() * 100:.1f}%\n")

    print("① MAE 개선 (현행 대비, 양수가 좋다)")
    print(f"{'시간대':8} {'구분':8} " + " ".join(f"{l + ' 비':>12} {l + ' 맑음':>12}" for l in labels))
    for (d, t), g in df.groupby(["duration", "day_type"]):
        print(f"{d:8} {t:8} " + " ".join(f"{g[l + '|wet'].mean():+11.1f}% {g[l + '|dry'].mean():+11.1f}%" for l in labels))
    print(f"{'평균':17} " + " ".join(f"{df[l + '|wet'].mean():+11.1f}% {df[l + '|dry'].mean():+11.1f}%" for l in labels))
    up = df["창 관측|wet"].mean()
    print(f"\n   위쪽 끝(창 관측) 대비 남은 몫: 출발 3시간 {df['출발 3시간|wet'].mean() / up * 100:.0f}% · "
          f"출발 1시간 {df['출발 1시간|wet'].mean() / up * 100:.0f}%")

    print(f"\n② 배송 대상(mu > {wi.MIN_DEMAND:.0f}) 목표 재고 커버리지 % (목표 = mu × 배율 + {TARGET_Z}σ) · 과잉(대)")
    print(f"{'':14} {'맑은 회차':>16} {'비 온 회차':>16} {'오경보 회차':>16}")
    print(f"{'현행':14} " + " ".join(f"{df[f'base_cover_{n}'].mean():8.1f}% {df[f'base_excess_{n}'].mean():5.2f}대"
                                    for n in ("dry", "wet", "fa")))
    for l in labels:
        print(f"{l:14} " + " ".join(f"{df[f'{l}|cover_{n}'].mean():8.1f}% {df[f'{l}|excess_{n}'].mean():5.2f}대"
                                    for n in ("dry", "wet", "fa")))

    c1 = df["출발 3시간|wet"].mean()
    c2 = df["출발 3시간|dry"].mean()
    c3 = df["base_cover_dry"].mean() - df["출발 3시간|cover_dry"].mean()
    print("\n판정 (주 변형 = 출발 직전 3시간, 결과 전에 적은 기준)")
    print(f"  C1 비 온 회차 MAE 개선 {c1:+.1f}%  (≥ +{C1_RAINY:.0f}%)  {'통과' if c1 >= C1_RAINY else '실패'}")
    print(f"  C2 맑은 회차 MAE 개선 {c2:+.1f}%  (≥ {C2_DRY:.0f}%)  {'통과' if c2 >= C2_DRY else '실패'}")
    print(f"  C3 맑은 회차 커버리지 하락 {c3:+.2f}%p  (≤ {C3_COVER:.0f}%p)  {'통과' if c3 <= C3_COVER else '실패'}")
    ok = c1 >= C1_RAINY and c2 >= C2_DRY and c3 <= C3_COVER
    print("  → " + ("셋 다 통과: 2단계(결품 시간 시뮬레이션) 등록 후보" if ok
                    else "하나 이상 실패: 자동 반영을 다시 꺼내지 않는다(화면 알림 유지)"))
    return 0


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    raise SystemExit(main())
