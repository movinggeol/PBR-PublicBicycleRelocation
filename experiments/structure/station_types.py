"""대여소 유형화(F7)의 신호 검사 둘: 유형이 날마다 같은가, 유형별 계절 배율이 도시 하나보다 맞나 (EXPERIMENTS 59장, 사전 등록 2026-10-25).

왜: ML_고도화_계획 F7은 하루 재고 궤적으로 대여소를 묶어(아침에 비는 곳 · 저녁에 차는 곳 · 늘 빈 곳) 유형별 계절 배율이나
결품 복원의 반영 시각에 쓰자는 안이다. 쓰기 전에 신호 둘을 통과해야 착수한다. 논문 뒤로 둔 과제였고,
사용자 지시(2026-10-25) *"제출 뒤로 미룬 것들도 모두 다 할 수 있는 것 해라"* 로 잰다.

어떻게 재나(결과를 보기 전에 정했다. 바꾸지 말 것):
    ① 안정성  stock_history 평일 2026-09-15 ~ 2026-10-24, 하루 130틱 이상 모인 날. 대여소-날은 24시간 중 20시간 이상 관측된 것.
              궤적 = 시간별 평균 채움률(재고 ÷ 거치대, 0 ~ 1.5로 자름) 24칸. 거치대는 자료 끝(2026-10-25 00:00) 전 가장 최근
              계획 실행의 station_info(plan_label_before, F2 · F3과 같은 규칙).
              대여소별 평균 궤적에 k-means(n_init 10). k는 {3, 4, 5, 6} 가운데 실루엣이 가장 큰 것(씨앗 0으로 고른다).
              날마다 대여소-날 궤적을 가장 가까운 중심에 배정하고(빠진 시는 그 대여소 평균으로 채운다), 이웃한 두 관측 평일마다
              유형별 자카드를 합집합 크기로 가중 평균한다. 쌍들의 평균이 0.8 이상이고 씨앗 0 · 1 · 2 모두 그러면 통과.
              휴일은 같은 방법으로 찍기만 한다.
    ② 배율    ①을 통과할 때만 돈다. 부록 A의 19개월에서 이어지는 달 쌍, 평일 네 회차. 직전 달 통계(mu, sigma) × 계획 달 첫
              14일 배율. 도시 하나(season_ratio) 대 유형별(같은 함수를 유형 안에서, 못 내면 도시 값). 15일째부터의 날,
              유형이 있고 |mu| > 2인 대여소에서 커버리지(실적 ≤ mu' + z·sigma', z = TARGET_Z)를 잰다.
              여유분을 맞춘다: 유형별 쪽의 z를 칸 안 z·sigma'의 합이 도시 쪽과 같아지게 다시 정한다(3번 시도의 함정).
              판정: 맞춘 커버리지가 (회차 × 달) 칸 평균 +1.0%p 이상 높고, 칸의 2/3 이상에서 높을 것.
    착수      ① · ② 모두 통과해야 한다. ①이 지면 ②는 돌리지 않고 접는다.

실행:
    python experiments/structure/station_types.py
    python experiments/structure/station_types.py --sanity     # 표본 크기만(판정 수치를 찍지 않는다)
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import numpy as np                                                      # noqa: E402
import pandas as pd                                                     # noqa: E402

import db                                                               # noqa: E402
import project_config                                                   # noqa: E402,F401  (출력 인코딩)
from backtest_demand import consecutive_pairs, daily_window_demand      # noqa: E402
from demand_model import WARMUP_MIN_DEMAND, season_ratio                # noqa: E402
from experiments._shared import plan_label_before                       # noqa: E402
from project_config import DURATIONS, TARGET_Z, holiday_mask, select_day_type   # noqa: E402

# 사전 등록 (59장). 결과를 보고 고치지 말 것.
START, END = "2026-09-15", "2026-10-24"
UNTIL = "2026-10-25 00:00"
MIN_DAY_TICKS = 130
MIN_HOURS = 20
FILL_CLIP = 1.5
K_CHOICES = (3, 4, 5, 6)
SEEDS = (0, 1, 2)
JACCARD_PASS = 0.8
WARMUP_DAYS = 14
COVERAGE_GAIN = 0.01
CELL_SHARE = 2 / 3
PERIODS = ["24년 08월", "24년 09월", "24년 10월", "24년 11월", "24년 12월", "25년 01월", "25년 02월", "25년 03월",
           "25년 04월", "25년 05월", "25년 06월", "25년 07월", "25년 08월", "25년 09월", "25년 10월", "25년 11월",
           "26년 01월", "26년 02월", "26년 03월"]          # 부록 A의 19개월(scripts/gate_a_rerun.ps1과 같다)


def load_profiles(day_type: str):
    """대여소-날의 시간별 평균 채움률(행 = (날, 대여소), 열 = 0 ~ 23시)과 쓴 날 목록, 거치대 라벨."""
    with db.session() as conn:
        hourly = pd.read_sql(
            "SELECT substr(observed_at, 1, 10) AS day, CAST(substr(observed_at, 12, 2) AS INTEGER) AS hour,"
            " station_id, AVG(stock) AS stock FROM stock_history"
            " WHERE observed_at >= ? AND observed_at < ? GROUP BY day, hour, station_id",
            conn, params=[START, f"{pd.Timestamp(END) + pd.Timedelta(days=1):%Y-%m-%d}"])
        ticks = pd.read_sql(
            "SELECT substr(observed_at, 1, 10) AS day, COUNT(DISTINCT observed_at) AS ticks FROM stock_history"
            " WHERE observed_at >= ? AND observed_at < ? GROUP BY day",
            conn, params=[START, f"{pd.Timestamp(END) + pd.Timedelta(days=1):%Y-%m-%d}"])
        label = plan_label_before(conn, until=UNTIL)
        capacity = pd.read_sql("SELECT station_id, parking_lot FROM station_info WHERE run_label = ?",
                               conn, params=[label])
    ticks["holiday"] = holiday_mask(ticks["day"] + " 12:00").to_numpy()
    days = ticks[(ticks["ticks"] >= MIN_DAY_TICKS) & (ticks["holiday"] == (day_type == "holiday"))]["day"]
    hourly = hourly[hourly["day"].isin(set(days))]
    capacity = capacity[capacity["parking_lot"] > 0]
    hourly = hourly.merge(capacity, on="station_id")
    hourly["fill"] = (hourly["stock"] / hourly["parking_lot"]).clip(0, FILL_CLIP)
    wide = hourly.pivot_table(index=["day", "station_id"], columns="hour", values="fill")
    wide = wide.reindex(columns=range(24))
    wide = wide[wide.notna().sum(axis=1) >= MIN_HOURS]
    return wide, sorted(days), label


def station_means(wide: pd.DataFrame) -> pd.DataFrame:
    """대여소별 평균 궤적. 한 시라도 비면 뺀다(그런 대여소는 날마다 배정할 기준이 없다)."""
    return wide.groupby(level="station_id").mean().dropna()


def choose_k(means: pd.DataFrame) -> tuple[int, dict]:
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score

    scores = {}
    for k in K_CHOICES:
        labels = KMeans(n_clusters=k, n_init=10, random_state=0).fit_predict(means.to_numpy())
        scores[k] = float(silhouette_score(means.to_numpy(), labels))
    return max(scores, key=scores.get), scores


def daily_types(wide: pd.DataFrame, means: pd.DataFrame, centers: np.ndarray) -> pd.Series:
    """날마다 대여소-날 궤적을 가장 가까운 중심에 배정한다. 빠진 시는 그 대여소 평균으로 채운다."""
    part = wide[wide.index.get_level_values("station_id").isin(means.index)]
    filled = part.fillna(means.reindex(part.index.get_level_values("station_id")).set_axis(part.index))
    values = filled.to_numpy()
    distance = ((values[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
    return pd.Series(distance.argmin(axis=1), index=part.index)


def pair_jaccard(a: pd.Series, b: pd.Series) -> tuple[float, float]:
    """두 날 모두 배정된 대여소에서 유형별 자카드의 합집합 가중 평균, 그리고 같은 유형으로 남은 대여소 비율."""
    common = a.index.intersection(b.index)
    a, b = a[common], b[common]
    num = den = 0.0
    for t in sorted(set(a) | set(b)):
        sa, sb = set(a.index[a == t]), set(b.index[b == t])
        union = len(sa | sb)
        if union:
            num += len(sa & sb)
            den += union
    return (num / den if den else float("nan")), float((a == b).mean())


def stability(wide: pd.DataFrame, days: list, k: int, seed: int):
    from sklearn.cluster import KMeans

    means = station_means(wide)
    model = KMeans(n_clusters=k, n_init=10, random_state=seed).fit(means.to_numpy())
    assigned = daily_types(wide, means, model.cluster_centers_)
    by_day = {day: assigned.xs(day, level="day") for day in days if day in assigned.index.get_level_values("day")}
    ordered = [d for d in days if d in by_day]
    pairs = []
    for left, right in zip(ordered, ordered[1:]):
        jac, same = pair_jaccard(by_day[left], by_day[right])
        pairs.append({"앞날": left, "뒷날": right, "자카드": jac, "같은유형비율": same})
    return pd.DataFrame(pairs), model, means


def describe_types(model, means: pd.DataFrame) -> pd.DataFrame:
    labels = model.predict(means.to_numpy())
    centers = pd.DataFrame(model.cluster_centers_, columns=range(24))
    table = centers[[3, 7, 9, 12, 15, 18, 21]].round(2)
    table.columns = [f"{h:02d}시" for h in table.columns]
    table.insert(0, "대여소", pd.Series(labels).value_counts().reindex(range(len(centers))).fillna(0).astype(int))
    return table


def season_check(types: pd.Series) -> pd.DataFrame:
    """② 유형별 배율 대 도시 하나. 칸 = (회차, 검증 달). 여유분을 맞춘 커버리지 차를 낸다."""
    rows = []
    with db.session() as conn:
        net = {p: select_day_type(db.load_frame(conn, "net_demand", period=p), "date", "weekday") for p in PERIODS}
    for prev, test in consecutive_pairs([p for p in PERIODS if not net[p].empty]):
        for duration in DURATIONS:
            train = daily_window_demand(net[prev], duration)
            recent = daily_window_demand(net[test], duration)
            city = season_ratio(train, recent, WARMUP_DAYS)
            if city is None:
                continue
            stats = train.groupby("station_id")["demand"].agg(mu="mean", sigma="std").fillna(0)
            stats["type"] = types.reindex(stats.index)
            stats = stats[stats["type"].notna() & (stats["mu"].abs() > WARMUP_MIN_DEMAND)]
            ratio_t = {}
            for t in stats["type"].unique():
                ids = set(types.index[types == t])
                r = season_ratio(train[train["station_id"].isin(ids)], recent[recent["station_id"].isin(ids)], WARMUP_DAYS)
                ratio_t[t] = city if r is None else r
            stats["r_t"] = stats["type"].map(ratio_t)
            dates = pd.to_datetime(recent["date"])
            later = recent[dates > dates.min() + pd.Timedelta(days=WARMUP_DAYS - 1)]
            merged = later.merge(stats, left_on="station_id", right_index=True)
            if merged.empty:
                continue
            margin_city = (TARGET_Z * merged["sigma"] * city).sum()
            margin_type = (merged["sigma"] * merged["r_t"]).sum()
            z_type = margin_city / margin_type if margin_type > 0 else TARGET_Z
            cov_city = float((merged["demand"] <= merged["mu"] * city + TARGET_Z * merged["sigma"] * city).mean())
            cov_type = float((merged["demand"] <= merged["mu"] * merged["r_t"]
                              + z_type * merged["sigma"] * merged["r_t"]).mean())
            rows.append({"검증달": test, "회차": duration, "도시배율": round(city, 3),
                         "유형배율": ", ".join(f"{ratio_t[t]:.2f}" for t in sorted(ratio_t)),
                         "도시커버리지": cov_city, "유형커버리지": cov_type, "차": cov_type - cov_city,
                         "MAE도시": float((merged["demand"] - merged["mu"] * city).abs().mean()),
                         "MAE유형": float((merged["demand"] - merged["mu"] * merged["r_t"]).abs().mean())})
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sanity", action="store_true", help="표본 크기만 찍는다(판정 수치 없음)")
    args = parser.parse_args()

    passed = {}
    for day_type in ("weekday", "holiday"):
        wide, days, label = load_profiles(day_type)
        means = station_means(wide)
        print(f"\n[{day_type}] 거치대 = '{label}' · 날 {len(days)}일({days[0]} ~ {days[-1]}) · "
              f"대여소-날 {len(wide):,}개 · 평균 궤적을 낸 대여소 {len(means):,}곳")
        if args.sanity:
            continue
        k, scores = choose_k(means)
        print("  실루엣: " + " · ".join(f"k={key} {value:.3f}" for key, value in scores.items()) + f" → k = {k}")
        results = []
        for seed in SEEDS:
            pairs, model, _ = stability(wide, days, k, seed)
            results.append(pairs["자카드"].mean())
            if seed == SEEDS[0]:
                print("  유형(씨앗 0)의 중심 채움률:")
                print(describe_types(model, means).to_string())
                print(f"  이웃 날 {len(pairs)}쌍 (씨앗 0): 자카드 평균 {pairs['자카드'].mean():.3f}"
                      f" (최소 {pairs['자카드'].min():.3f} · 최대 {pairs['자카드'].max():.3f}),"
                      f" 같은 유형으로 남은 대여소 {pairs['같은유형비율'].mean():.1%}")
        print("  씨앗별 자카드 평균: " + " · ".join(f"{s} {v:.3f}" for s, v in zip(SEEDS, results)))
        ok = all(v >= JACCARD_PASS for v in results)
        passed[day_type] = ok
        note = "(찍기만 한다)" if day_type == "holiday" else ""
        print(f"  ① 안정성 {'통과' if ok else '미달'} (기준 {JACCARD_PASS}, 씨앗 셋 모두) {note}")

    if args.sanity:
        return 0
    if not passed.get("weekday"):
        print("\n① 미달: 등록대로 ②는 돌리지 않고 F7을 접는다.")
        return 0

    wide, days, _ = load_profiles("weekday")
    means = station_means(wide)
    k, _ = choose_k(means)
    from sklearn.cluster import KMeans
    types = pd.Series(KMeans(n_clusters=k, n_init=10, random_state=0).fit_predict(means.to_numpy()), index=means.index)
    cells = season_check(types)
    print("\n② 유형별 배율 대 도시 하나 (여유분을 맞춘 커버리지)")
    print(cells.round(4).to_string(index=False))
    gain, share = cells["차"].mean(), float((cells["차"] > 0).mean())
    ok = gain >= COVERAGE_GAIN and share >= CELL_SHARE
    print(f"  칸 {len(cells)}개 · 평균 차 {gain * 100:+.2f}%p · 높은 칸 {share:.0%}"
          f" → ② {'통과' if ok else '미달'} (기준 +{COVERAGE_GAIN * 100:.1f}%p · {CELL_SHARE:.0%})")
    print(f"  F7 착수: {'한다(설계를 따로 등록한다)' if ok else '하지 않는다'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
