"""포화 예측 — 대여소가 **N틱 뒤에 거치대의 90% 이상 차 있을 확률**을 맞힌다 (ML 11번 · F3 1단계, 1.26.305).

결품 예측(9번, `stockout_forecast.py`)의 거울상이다. 원고 6장이 잰 대가 — 제안 방법은 결품을
줄이는 만큼 포화를 늘린다(<표 6-2>, +0.16~+0.37h) — 를 계획에서 직접 줄이려면 *"어디가 곧
찰까"* 를 알아야 한다. F3 하네스(`f3_drop_saturation.py`)가 이 확률로 배송 후보를 거른다.

9번의 착수 전 검사를 그대로 지킨다 — 날짜로 자르기 · AUC가 아니라 Brier · 평소 빈도 구간별
검사와 누출 검사. 9번과 다른 것은 셋이다.

  ① 🔴 **거치대에 매여 있다.** 10칸짜리와 30칸짜리는 같은 문제가 아니다(ML_후보_10 1번) —
     `재고 / 거치대` 비율과 거치대 수를 피처로 넣는다. 거치대 수는 **최신 계획 실행의
     `station_info.parking_lot`**(F2 · F3 하네스와 같은 원천)이고, 없거나 0인 대여소는 뺀다.
  ② 라벨은 **거치대의 90% 이상**이다(ML_후보_10 1번의 사전 등록 안 — 2026-09-11 관측의 9.53%).
     step4의 포화(재고 = 거치대)보다 넓다. 이것은 예측의 신호이고, 계획의 대가는 F3가 step4의
     포화로 잰다.
  ③ 🔴 **베이스라인이 셋이다.** 9번의 둘(지속 규칙 · 대여소×시간대 과거 빈도)에 **순수요 누적**
     — 지금 재고 − Σ(그 시간대의 평균 순수요) — 을 더한다. F1(ML 10번)에서 ML의 이득처럼 보이던
     것의 대부분이 이 한 줄이었다(ML_고도화_계획 6장 규칙 1: *"파이프라인이 이미 아는 것"으로
     만든 베이스라인을 반드시 세운다*). 평균 순수요는 운영과 같은 기간(`DEFAULT_PERIOD`)의 시간대별
     평균이고, 그 틱의 날이 휴일이면 휴일 평균을 쓴다(파이프라인이 둘을 섞지 않는 것과 같다).

채택 (사전 등록 — ML_후보_10 1번 · ML_고도화_계획 3장 F3 · 해석은 EXPERIMENTS 43장)
  세 베이스라인을 **모두** Brier로 이길 것. 못 이기면 F3 계획 연결은 돌리지 않는다.
  평소 빈도 구간별 검사는 9번처럼 **지는 구간을 숨기지 않고 찍는다** — 구간 조건은 F2처럼
  계획 단계(구간마다 포화가 현행 이하)에서 건다.
  기본값(6틱 = 1시간 뒤 · 뒤 4일 검증)은 9번과 같다 — **바꿔 돌려 이겼다고 말하지 않는다.**

실행:
    python experiments/structure/saturation_forecast.py
    python experiments/structure/saturation_forecast.py --horizon 30
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
import stockout_forecast as sf  # noqa: E402
from project_config import DEFAULT_PERIOD, TICKS_PER_HOUR, holiday_mask  # noqa: E402

TICK_MINUTES = sf.TICK_MINUTES
SLOTS_PER_DAY = 24 * TICKS_PER_HOUR

# ── 사전 등록 (ML_후보_10 1번) — 결과를 보고 바꾸지 않는다
SAT_RATIO = 0.9                 # 거치대의 90% 이상이면 '포화'
DEFAULT_HORIZON = sf.DEFAULT_HORIZON        # 6틱 = 1시간 뒤 (9번과 같다)
DEFAULT_TEST_DAYS = sf.DEFAULT_TEST_DAYS    # 뒤 4일 (9번과 같다)

FEATURES = ["지금포화", "재고", "재고비율", "거치대", "1틱전포화", "3틱전포화", "6틱전포화",
            "최근6틱포화비율", "최근18틱포화비율", "재고변화3틱", "시", "요일", "대여소시간대포화비율"]


# ───────────────────────────────────────────── 자료

def load_capacity(conn) -> tuple:
    """거치대 수(station_id → parking_lot)와 그것을 읽은 계획 실행 라벨.

    F2 · F3 하네스가 `bc.load_inputs()`로 읽는 것과 **같은 실행**(최신 계획)이다 — 모형이 본
    거치대와 계획이 본 거치대가 갈리면 비율 피처가 다른 자가 된다.
    """
    label = db.latest_label(conn, "station_info", kinds=("plan",))
    info = db.load_frame(conn, "station_info", run_label=label)
    capacity = (info.drop_duplicates("station_id").set_index("station_id")["parking_lot"])
    return label, pd.to_numeric(capacity, errors="coerce")


def label_saturation(frame: pd.DataFrame, capacity: pd.Series) -> pd.DataFrame:
    """관측 격자에 거치대 수와 포화 라벨(재고 ≥ 거치대 × 0.9)을 붙인다.

    거치대 수를 모르는 대여소(마스터에 없거나 0)는 **뺀다** — 0으로 두면 늘 포화가 되고,
    비워 두면 비율 피처가 통째로 빠진다.
    """
    out = frame.copy()
    out["거치대"] = out["station_id"].map(capacity)
    out = out[out["거치대"] > 0].copy()
    out["포화"] = (out["stock"] >= SAT_RATIO * out["거치대"]).astype(np.int8)
    return out


def load_grid(conn, capacity: pd.Series) -> pd.DataFrame:
    """관측(`stock_history`)을 펴서 포화 라벨을 붙인다 — 수집기의 관측이라 정답표로 쓸 수 있다."""
    return label_saturation(sf.load_grid(conn), capacity)


def hourly_mu(conn, period: str) -> dict:
    """대여소 × 시(0~23)의 평균 순수요 — {False: 평일, True: 휴일}. 파이프라인이 쓰는 그 통계량이다.

    순수요는 대여 − 반납이라 **양수면 재고가 준다**(step4 `stock(t+1) = stock(t) − net`).
    """
    net = pd.read_sql("SELECT * FROM net_demand WHERE period = ?", conn, params=(period,))
    return split_mu(net)


def split_mu(net: pd.DataFrame) -> dict:
    cols = [f"net_{h:02d}" for h in range(24)]
    holiday = np.asarray(holiday_mask(pd.to_datetime(net["date"])), dtype=bool)
    out = {}
    for flag in (False, True):
        mu = net[holiday == flag].groupby("station_id")[cols].mean()
        mu.columns = range(24)
        out[flag] = mu
    return out


def window_outflow(mu: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """대여소 × 하루의 10분 칸(0~143)마다, 그 칸부터 `horizon`틱 동안의 평균 순유출 합.

    한 시간의 평균 순수요를 그 시간의 여섯 칸에 고르게 나눈다. 자정을 넘는 창은 **같은 날의
    요일 구분**으로 이어 붙인다(다음 날이 휴일이어도 — 베이스라인의 근사로 적어 둔다).
    """
    rate = np.repeat(mu.to_numpy(dtype=float) / TICKS_PER_HOUR, TICKS_PER_HOUR, axis=1)  # (대여소, 144)
    twice = np.concatenate([rate, rate], axis=1)
    csum = np.concatenate([np.zeros((len(mu), 1)), np.cumsum(twice, axis=1)], axis=1)
    slots = np.arange(SLOTS_PER_DAY)
    total = csum[:, slots + horizon] - csum[:, slots]
    return pd.DataFrame(total, index=mu.index, columns=slots)


def net_baseline(long: pd.DataFrame, mu_by_type: dict, horizon: int) -> np.ndarray:
    """베이스라인 ③ 순수요 누적 — (지금 재고 − 앞으로 k틱의 평균 순유출) ≥ 거치대 × 0.9 이면 1.

    F1의 N과 같은 식이다(점 예측 · 확률이 아니라 0/1 — 지속 규칙도 0/1이다). 순수요 통계에 없는
    대여소는 유출 0으로 둔다 — 그러면 지속 규칙과 같아진다.
    """
    slot = (long["ts"].dt.hour * TICKS_PER_HOUR + long["ts"].dt.minute // TICK_MINUTES).to_numpy()
    holiday = np.asarray(holiday_mask(long["ts"].dt.normalize()), dtype=bool)
    outflow = np.zeros(len(long))
    for flag in (False, True):
        pick = holiday == flag
        if not pick.any() or mu_by_type[flag].empty:
            continue
        table = window_outflow(mu_by_type[flag], horizon)
        # 행마다 (대여소, 칸) 한 값만 집는다 — reindex로 펴면 (행 수 × 144) 행렬이 된다.
        idx = table.index.get_indexer(long.loc[pick, "station_id"])
        got = table.to_numpy()[np.clip(idx, 0, None), slot[pick]]
        outflow[pick] = np.where(idx >= 0, np.nan_to_num(got, nan=0.0), 0.0)
    predicted = long["재고"].to_numpy(dtype=float) - outflow
    return (predicted >= SAT_RATIO * long["거치대"].to_numpy(dtype=float)).astype(float)


def build_features(frame: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """(시각, 대여소)마다 **그 시점까지 아는 것만으로** 피처를 만든다 — 9번과 같은 틀.

    🔴 미래를 쓰지 않는 것이 이 함수의 전부다. `shift(-horizon)`으로 만든 타깃 말고는 어떤
    컬럼도 t보다 뒤를 보지 않는다.
    """
    sat = frame.pivot_table(index="ts", columns="station_id", values="포화").sort_index()
    stock = frame.pivot_table(index="ts", columns="station_id", values="stock").sort_index()
    capacity = frame.groupby("station_id")["거치대"].first().reindex(stock.columns)

    target = sat.shift(-horizon)
    feats = {
        "지금포화": sat,
        "재고": stock,
        "재고비율": stock.div(capacity, axis=1),
        "1틱전포화": sat.shift(1),
        "3틱전포화": sat.shift(3),
        "6틱전포화": sat.shift(6),
        "최근6틱포화비율": sat.rolling(6, min_periods=3).mean(),
        "최근18틱포화비율": sat.rolling(18, min_periods=6).mean(),
        "재고변화3틱": stock - stock.shift(3),
    }

    long = target.stack().rename("타깃").to_frame()
    for name, table in feats.items():
        long[name] = table.stack()

    long = long.reset_index()
    long.columns = ["ts", "station_id"] + list(long.columns[2:])
    long["거치대"] = long["station_id"].map(capacity)
    long["시"] = long["ts"].dt.hour
    long["요일"] = long["ts"].dt.weekday
    return long.dropna()


def add_station_history(train: pd.DataFrame, frames: list) -> list:
    """대여소·시간대별 과거 포화 비율 — **학습 구간에서만** 만든다(9번과 같은 규칙).

    베이스라인 ②이자 모형 피처다. 검증 구간의 값으로 만들면 누출이다.
    """
    key = ["station_id", "시"]
    table = train.groupby(key)["지금포화"].mean().rename("대여소시간대포화비율")
    overall = train["지금포화"].mean()
    out = []
    for f in frames:
        merged = f.merge(table, on=key, how="left")
        merged["대여소시간대포화비율"] = merged["대여소시간대포화비율"].fillna(overall)
        out.append(merged)
    return out


def fit_model(train: pd.DataFrame):
    """9번과 같은 모형 · 같은 하이퍼파라미터 — 모형을 바꾸면 비교가 '라벨'이 아니게 된다."""
    from sklearn.ensemble import HistGradientBoostingClassifier

    model = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.1, max_depth=6,
                                           random_state=42)
    model.fit(train[FEATURES], train["타깃"])
    return model


BASELINES = {"① 지속 규칙": "지금포화", "② 과거 빈도": "대여소시간대포화비율", "③ 순수요 누적": "순수요누적"}


# ───────────────────────────────────────────── 실행

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON,
                        help=f"몇 틱 뒤를 맞히나 (기본 {DEFAULT_HORIZON}틱"
                             f" = {DEFAULT_HORIZON * TICK_MINUTES}분)")
    parser.add_argument("--test-days", type=int, default=DEFAULT_TEST_DAYS,
                        help=f"뒤 며칠을 검증으로 (기본 {DEFAULT_TEST_DAYS})")
    parser.add_argument("--period", default=DEFAULT_PERIOD,
                        help=f"베이스라인 ③의 순수요 기간 (기본 {DEFAULT_PERIOD} — 운영과 같다)")
    args = parser.parse_args(argv)

    with db.session() as conn:
        label, capacity = load_capacity(conn)
        raw = sf.load_grid(conn)
        mu_by_type = hourly_mu(conn, args.period)
    frame = label_saturation(raw, capacity)
    dropped = raw["station_id"].nunique() - frame["station_id"].nunique()
    print(f"거치대 수 = '{label}' station_info · 거치대를 몰라 뺀 대여소 {dropped}곳")
    print(f"순수요 누적(③) = {args.period} · 평일 {len(mu_by_type[False]):,}곳 · 휴일 {len(mu_by_type[True]):,}곳")

    days = sf.dense_days(frame)
    if len(days) < args.test_days + 3:
        print(f"[중단] 쓸 만한 날이 {len(days)}일뿐입니다"
              f" (검증 {args.test_days}일 + 학습 최소 3일 필요).")
        return 1

    train_days = days[:-args.test_days]
    test_days = days[-args.test_days:]
    print(f"관측 {len(frame):,}행 · 쓸 날 {len(days)}일 · 포화(거치대 {SAT_RATIO:.0%} 이상)"
          f" {frame['포화'].mean():.2%}")
    print(f"  학습 {len(train_days)}일: {train_days[0]} ~ {train_days[-1]}")
    print(f"  검증 {len(test_days)}일: {test_days[0]} ~ {test_days[-1]}")
    print(f"  묻는 것: {args.horizon}틱({args.horizon * TICK_MINUTES}분) 뒤에"
          f" 거치대의 {SAT_RATIO:.0%} 이상이 차 있나\n")

    long = build_features(frame, args.horizon)
    day = long["ts"].dt.date
    train = long[day.isin(train_days)].copy()
    test = long[day.isin(test_days)].copy()
    train, test = add_station_history(train, [train, test])
    test["순수요누적"] = net_baseline(test, mu_by_type, args.horizon)

    print(f"학습 {len(train):,}행 · 검증 {len(test):,}행")
    print(f"검증 구간 실제 포화 비율: {test['타깃'].mean():.1%}\n")

    actual = test["타깃"].to_numpy()
    결과 = {name: test[column].to_numpy(dtype=float) for name, column in BASELINES.items()}
    결과["⓪ 전체 평균"] = np.full(len(test), train["타깃"].mean())
    try:
        model = fit_model(train)
        결과["④ GBM"] = model.predict_proba(test[FEATURES])[:, 1]
    except ImportError:
        print("[건너뜀] scikit-learn이 없어 모델을 학습하지 못했습니다.")
        model = None

    print("=" * 62)
    print("판정 — Brier score (낮을수록 좋다)")
    print("=" * 62)
    base = sf.brier(결과["① 지속 규칙"], actual)
    for name, prob in 결과.items():
        b = sf.brier(prob, actual)
        mark = "" if name.startswith(("⓪", "①")) else f"   지속 규칙 대비 {(b / base - 1) * 100:+.1f}%"
        print(f"  {name:<12} {b:.4f}{mark}")

    if model is None:
        return 0
    gbm = sf.brier(결과["④ GBM"], actual)
    scores = {name: sf.brier(결과[name], actual) for name in BASELINES}
    이김 = all(gbm < b for b in scores.values())
    print("\n  사전 등록 채택 조건: 세 베이스라인을 **모두** 이길 것")
    print("    " + " · ".join(f"{name} {b:.4f}" for name, b in scores.items()) + f" · ④ GBM {gbm:.4f}")
    print(f"    → {'✅ 통과 — F3 계획 연결을 돌려도 된다' if 이김 else '❌ 미달 — F3는 돌리지 않는다'}")
    if not 이김:
        return 0

    # 9번처럼 여기서 멈추지 않는다 — 7번 시도는 교차검증을 통과하고도 틀렸다.
    sf.report_calibration(결과["④ GBM"], actual)
    report_topk(test, 결과["④ GBM"], actual)
    report_by_base_rate(test, 결과["④ GBM"], actual)
    report_by_capacity(test, 결과["④ GBM"], actual)
    sf.report_leak_checks(train, test, FEATURES, actual)
    return 0


def report_topk(test: pd.DataFrame, prob, actual) -> None:
    """실전 — 매 시각 '가장 찰 것 같은 K곳'을 고른다면 그중 몇 곳이 실제로 찼나."""
    print("\n" + "=" * 62)
    print("실전 — 매 시각 '가장 찰 것 같은 K곳'을 고른다면")
    print("=" * 62)
    frame = pd.DataFrame({"ts": test["ts"].to_numpy(), "GBM": prob,
                          "지속": test["지금포화"].to_numpy(dtype=float),
                          "과거": test["대여소시간대포화비율"].to_numpy(),
                          "순수요": test["순수요누적"].to_numpy(dtype=float), "실제": actual})
    for K in (20, 50, 100):
        acc = {"GBM": 0.0, "지속": 0.0, "과거": 0.0, "순수요": 0.0}
        n = 0
        for _, group in frame.groupby("ts"):
            if len(group) < K:
                continue
            n += 1
            for name in acc:
                acc[name] += group.nlargest(K, name)["실제"].sum()
        if n:
            print(f"  상위 {K:3d}곳 (시각 {n}개) — "
                  + " · ".join(f"{name} {acc[name] / n / K:.1%}" for name in acc))
    print(f"  무작위로 골랐다면 — {np.mean(actual):.1%}")


def report_by_base_rate(test: pd.DataFrame, prob, actual) -> None:
    """🔴 **'늘 차는 곳 부르기'가 아닌가** — 9번의 검사 ④를 거울로 옮겼다.

    평소 포화 빈도가 비슷한 것끼리 갈라 그 안에서 다시 잰다. 각 구간 안에서도 세 베이스라인 중
    나은 쪽을 이겨야 *"오늘 지금 상태"* 를 배운 것이다. **지는 구간을 숨기지 않고 찍는다** —
    전체 평균만 보면 한 구간의 큰 승리가 다른 구간의 패배를 가린다.
    """
    print("\n" + "=" * 62)
    print("평소 빈도 구간별 — '늘 차는 곳 부르기'가 아닌지")
    print("=" * 62)
    work = test.copy()
    work["예측"] = prob
    work["실제"] = actual
    edges = [0, 0.2, 0.4, 0.6, 0.8, 0.95, 1.01]
    work["구간"] = pd.cut(work["대여소시간대포화비율"], edges, right=False)
    for name, sub in work.groupby("구간", observed=True):
        if len(sub) < 500:
            continue
        a = sub["실제"].to_numpy()
        b_freq = sf.brier(sub["대여소시간대포화비율"].to_numpy(), a)
        b_now = sf.brier(sub["지금포화"].to_numpy(dtype=float), a)
        b_net = sf.brier(sub["순수요누적"].to_numpy(dtype=float), a)
        b_gbm = sf.brier(sub["예측"].to_numpy(), a)
        mark = "✅" if b_gbm < min(b_freq, b_now, b_net) else "🔴 진다"
        print(f"  평소포화 {str(name):<13} n={len(sub):>8,} 실제 {a.mean():.2f}"
              f" | 빈도 {b_freq:.4f} · 지속 {b_now:.4f} · 순수요 {b_net:.4f} · GBM {b_gbm:.4f}  {mark}")


def report_by_capacity(test: pd.DataFrame, prob, actual) -> None:
    """거치대 크기별 — *"10칸과 30칸은 같은 문제가 아니다"* 를 비율 피처가 실제로 풀었나."""
    print("\n" + "=" * 62)
    print("거치대 크기별 — 작은 대여소만 맞히는 것이 아닌지")
    print("=" * 62)
    work = pd.DataFrame({"거치대": test["거치대"].to_numpy(), "예측": prob, "실제": actual,
                         "지속": test["지금포화"].to_numpy(dtype=float),
                         "빈도": test["대여소시간대포화비율"].to_numpy(),
                         "순수요": test["순수요누적"].to_numpy(dtype=float)})
    work["크기"] = pd.cut(work["거치대"], [0, 10, 15, 20, 1000], right=True,
                        labels=["~10칸", "11~15칸", "16~20칸", "21칸~"])
    for name, sub in work.groupby("크기", observed=True):
        if len(sub) < 500:
            continue
        a = sub["실제"].to_numpy()
        b_gbm = sf.brier(sub["예측"].to_numpy(), a)
        b_best = min(sf.brier(sub[c].to_numpy(), a) for c in ("지속", "빈도", "순수요"))
        mark = "✅" if b_gbm < b_best else "🔴 진다"
        print(f"  {name:<8} n={len(sub):>8,} 실제 {a.mean():.3f} | 나은 베이스라인 {b_best:.4f}"
              f" · GBM {b_gbm:.4f}  {mark}")


if __name__ == "__main__":
    raise SystemExit(main())
