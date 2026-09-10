"""결품 예측 — 대여소가 **N틱 뒤에 비어 있을 확률**을 맞힌다 (ML_계획.md 3-2).

이 저장소의 아홉 번째 ML 시도다. 앞의 여덟 번은 전부 졌고
([ML_ATTEMPTS.md](../../docs/분석/ML_ATTEMPTS.md)), 그 패배에서 뽑은
**착수 전 검사 네 가지**를 이 스크립트가 그대로 지킨다.

  ① 타깃을 누가 만들었나 — `stock_history`는 **수집기가 API에서 받은 관측**이다.
     파이프라인이 만든 값이 아니다(7번 시도가 여기서 틀렸다: `cum_sec`을
     TMAP 실측이라 **가정**했는데 파이프라인 자신의 추정치였다).
  ② 배울 신호가 있나 — **짜기 전에 쟀다.** 잔차 자기상관 0.790(순수요는 0.30이라
     4번 시도를 접었다). 시간 변동 0.411 > 대여소 간 차이 0.262.
  ③ 베이스라인이 공정한가 — **둘을 세운다.** 지속 규칙과 과거 빈도. 2번 시도는
     베이스라인 쪽 결함 때문에 이겼다가 되돌려졌다.
  ④ 이득을 무엇으로 샀나 — 확률만 좋아지고 **포화**가 늘면 이득이 아니다.
     3번 시도가 sigma를 키워 '산' 이득이었다.

🔴 **날짜로 자른다.** 무작위 분할은 같은 날의 앞뒤 틱을 학습·검증 양쪽에
넣어 **누출**이 된다 — 자기상관 0.790이면 그 누출만으로 이긴다.

🔴 **AUC가 아니라 Brier score로 잰다.** 계획에 쓰려면 *"어디가 급한가"* 의
순서가 아니라 **확률의 크기**가 맞아야 한다. 0.9와 0.6을 구분 못 하면
우선순위를 매길 수 없다.

실행:
    python experiments/structure/stockout_forecast.py
    python experiments/structure/stockout_forecast.py --horizon 6 --test-days 4
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

import db

# 수집 간격(분). 한 틱이 10분이므로 horizon 6 = 1시간 뒤를 묻는다.
TICK_MINUTES = 10

# 사전 등록 기본값 (ML_계획.md 4-1). 바꿔 돌릴 수는 있으나, **기본값을 바꾼 뒤
# 이겼다고 말하지 않는다** — 결과를 보고 조건을 고르는 것과 같아진다.
DEFAULT_HORIZON = 6        # 6틱 = 1시간 뒤
DEFAULT_TEST_DAYS = 4      # 뒤 4일을 검증으로

# 그 틱에 대여소가 실제로 관측됐는지 판정하는 기준. 수집이 성긴 날이 섞이면
# 앞 틱이 없어 lag 피처가 통째로 비는데, 그것을 0으로 채우면 '재고가 있었다'는
# 뜻이 되어 버린다(WEATHER의 -9와 같은 함정).
MIN_TICKS_PER_DAY = 30


def load_grid(conn) -> pd.DataFrame:
    """관측을 (시각 × 대여소) 격자로 편다.

    `stock_history`는 수집기가 API에서 받은 그대로다 — 파이프라인 산출물이
    아니므로 정답표로 쓸 수 있다(검사 ①).
    """
    frame = pd.read_sql_query(
        "SELECT observed_at, station_id, stock FROM stock_history", conn)
    frame["ts"] = pd.to_datetime(frame["observed_at"])
    frame["빔"] = (frame["stock"] == 0).astype(np.int8)
    return frame


def dense_days(frame: pd.DataFrame) -> list:
    """틱이 너무 적은 날은 뺀다 — 앞 틱이 없으면 lag 피처를 만들 수 없다."""
    per_day = frame.groupby(frame["ts"].dt.date)["ts"].nunique()
    return sorted(day for day, ticks in per_day.items()
                  if ticks >= MIN_TICKS_PER_DAY)


def build_features(frame: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """(시각, 대여소)마다 **그 시점까지 아는 것만으로** 피처를 만든다.

    🔴 미래를 쓰지 않는 것이 이 함수의 전부다. `shift(-horizon)`으로 만든
    타깃 말고는 어떤 컬럼도 t보다 뒤를 보지 않는다.
    """
    wide = frame.pivot_table(index="ts", columns="station_id",
                             values="빔").sort_index()
    stock = frame.pivot_table(index="ts", columns="station_id",
                              values="stock").sort_index()

    # 타깃: horizon 틱 뒤에 비어 있나
    target = wide.shift(-horizon)

    feats = {
        "지금빔": wide,
        "재고": stock,
        "1틱전빔": wide.shift(1),
        "3틱전빔": wide.shift(3),
        "6틱전빔": wide.shift(6),
        "최근6틱빔비율": wide.rolling(6, min_periods=3).mean(),
        "최근18틱빔비율": wide.rolling(18, min_periods=6).mean(),
        "재고변화3틱": stock - stock.shift(3),
    }

    long = target.stack().rename("타깃").to_frame()
    for name, table in feats.items():
        long[name] = table.stack()

    long = long.reset_index()
    long.columns = ["ts", "station_id"] + list(long.columns[2:])
    long["시"] = long["ts"].dt.hour
    long["요일"] = long["ts"].dt.weekday
    return long.dropna()


def add_station_history(train: pd.DataFrame,
                        frames: list) -> list:
    """대여소·시간대별 과거 빔 비율 — **학습 구간에서만** 만든다.

    검증 구간의 값으로 만들면 누출이다. 베이스라인 ②이자 모델 피처이기도
    하므로 한 곳에서 만들어 양쪽에 같은 값을 준다.
    """
    key = ["station_id", "시"]
    표 = train.groupby(key)["지금빔"].mean().rename("대여소시간대빔비율")
    전체 = train["지금빔"].mean()
    out = []
    for f in frames:
        merged = f.merge(표, on=key, how="left")
        merged["대여소시간대빔비율"] = merged["대여소시간대빔비율"].fillna(전체)
        out.append(merged)
    return out


def brier(prob, actual) -> float:
    """확률의 품질. 낮을수록 좋다 — 0이 완벽, 0.25가 '늘 0.5라고 답하기'."""
    return float(np.mean((np.asarray(prob) - np.asarray(actual)) ** 2))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON,
                        help=f"몇 틱 뒤를 맞히나 (기본 {DEFAULT_HORIZON}틱"
                             f" = {DEFAULT_HORIZON * TICK_MINUTES}분)")
    parser.add_argument("--test-days", type=int, default=DEFAULT_TEST_DAYS,
                        help=f"뒤 며칠을 검증으로 (기본 {DEFAULT_TEST_DAYS})")
    args = parser.parse_args(argv)

    with db.session() as conn:
        frame = load_grid(conn)

    days = dense_days(frame)
    if len(days) < args.test_days + 3:
        print(f"[중단] 쓸 만한 날이 {len(days)}일뿐입니다"
              f" (검증 {args.test_days}일 + 학습 최소 3일 필요).")
        return 1

    train_days = days[:-args.test_days]
    test_days = days[-args.test_days:]
    print(f"관측 {len(frame):,}행 · 쓸 날 {len(days)}일")
    print(f"  학습 {len(train_days)}일: {train_days[0]} ~ {train_days[-1]}")
    print(f"  검증 {len(test_days)}일: {test_days[0]} ~ {test_days[-1]}")
    print(f"  묻는 것: {args.horizon}틱"
          f"({args.horizon * TICK_MINUTES}분) 뒤에 비어 있나\n")

    long = build_features(frame, args.horizon)
    day = long["ts"].dt.date
    train = long[day.isin(train_days)].copy()
    test = long[day.isin(test_days)].copy()
    train, test = add_station_history(train, [train, test])

    print(f"학습 {len(train):,}행 · 검증 {len(test):,}행")
    print(f"검증 구간 실제 빔 비율: {test['타깃'].mean():.1%}\n")

    actual = test["타깃"].values
    결과 = {}

    # ── 베이스라인 ① 지속 규칙 — "지금 비어 있으면 계속 빈다"
    # 자기상관 0.790에서 이미 매우 강하다. 이것을 못 이기면 채택하지 않는다.
    결과["① 지속 규칙"] = test["지금빔"].values.astype(float)

    # ── 베이스라인 ② 대여소·시간대별 과거 빈도
    결과["② 과거 빈도"] = test["대여소시간대빔비율"].values

    # ── 베이스라인 ⓪ 전체 평균 (하한 확인용)
    결과["⓪ 전체 평균"] = np.full(len(test), train["타깃"].mean())

    # ── 모델: 그래디언트 부스팅
    피처 = ["지금빔", "재고", "1틱전빔", "3틱전빔", "6틱전빔",
           "최근6틱빔비율", "최근18틱빔비율", "재고변화3틱",
           "시", "요일", "대여소시간대빔비율"]
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
        model = HistGradientBoostingClassifier(
            max_iter=200, learning_rate=0.1, max_depth=6, random_state=42)
        model.fit(train[피처], train["타깃"])
        결과["③ GBM"] = model.predict_proba(test[피처])[:, 1]
    except ImportError:
        print("[건너뜀] scikit-learn이 없어 모델을 학습하지 못했습니다.")
        model = None

    # ── 판정
    print("=" * 62)
    print("판정 — Brier score (낮을수록 좋다)")
    print("=" * 62)
    base = None
    for name, prob in 결과.items():
        b = brier(prob, actual)
        if name.startswith("①"):
            base = b
        mark = ""
        if base is not None and not name.startswith(("⓪", "①")):
            mark = f"   지속 규칙 대비 {(b / base - 1) * 100:+.1f}%"
        print(f"  {name:<12} {b:.4f}{mark}")

    if model is not None:
        gbm = brier(결과["③ GBM"], actual)
        b1 = brier(결과["① 지속 규칙"], actual)
        b2 = brier(결과["② 과거 빈도"], actual)
        print()
        이김 = gbm < b1 and gbm < b2
        print(f"  사전 등록 채택 조건: 두 베이스라인을 **모두** 이길 것")
        print(f"    ① 지속 규칙 {b1:.4f} · ② 과거 빈도 {b2:.4f}"
              f" · ③ GBM {gbm:.4f}")
        print(f"    → {'✅ 통과' if 이김 else '❌ 미달'}")

        if not 이김:
            return 0

        # ── 여기서 멈추지 않는다. 7번 시도는 교차검증을 **통과하고도** 틀렸다.
        report_calibration(결과["③ GBM"], actual)
        report_topk(test, 결과, actual)
        report_by_base_rate(test, 결과["③ GBM"], actual)
        report_leak_checks(train, test, 피처, actual)

    return 0


def report_calibration(prob, actual) -> None:
    """확률의 크기가 맞나 — *"0.8이라 말한 것 중 실제로 몇 %가 비었나"*.

    Brier가 낮아도 보정이 어긋나면 계획에 못 쓴다. 0.9와 0.6을 구분 못 하면
    **어디부터 손댈지**를 정할 수 없기 때문이다(그것이 이 예측의 용도다).
    """
    print("\n" + "=" * 62)
    print("보정 — 예측 확률 대 실제 빈도")
    print("=" * 62)
    edges = np.linspace(0, 1, 11)
    which = np.digitize(prob, edges) - 1
    for i in range(10):
        sel = which == i
        if sel.sum() < 50:
            continue
        print(f"  예측 {edges[i]:.1f}~{edges[i + 1]:.1f}"
              f"   실제 {np.mean(actual[sel]):.3f}   n={sel.sum():,}")


def report_topk(test: pd.DataFrame, 결과: dict, actual) -> None:
    """실전 시나리오 — 계획은 **상위 몇 곳**만 손댄다(`TOP_STATION_LIMIT`).

    전체 정확도보다 이쪽이 실제 쓸모에 가깝다. 매 시각 상위 K곳을 골라
    그중 몇 곳이 실제로 비었는지 센다.
    """
    print("\n" + "=" * 62)
    print("실전 — 매 시각 '가장 위험한 K곳'을 고른다면")
    print("=" * 62)
    frame = pd.DataFrame({
        "ts": test["ts"].values,
        "GBM": 결과["③ GBM"],
        "지속": test["지금빔"].values.astype(float),
        "과거": test["대여소시간대빔비율"].values,
        "실제": actual,
    })
    for K in (20, 50, 100):
        acc = {"GBM": 0.0, "지속": 0.0, "과거": 0.0}
        n = 0
        for _, group in frame.groupby("ts"):
            if len(group) < K:
                continue
            n += 1
            for name in acc:
                acc[name] += group.nlargest(K, name)["실제"].sum()
        if not n:
            continue
        말 = " · ".join(f"{name} {acc[name] / n / K:.1%}" for name in acc)
        print(f"  상위 {K:3d}곳 (시각 {n}개) — {말}")
    print(f"  무작위로 골랐다면 — {np.mean(actual):.1%}")


def report_by_base_rate(test: pd.DataFrame, prob, actual) -> None:
    """🔴 **이 스크립트에서 가장 중요한 검사다 — "늘 비는 곳 부르기"가 아닌가.**

    상위 50곳을 뽑아 보면 **평소 빔 비율이 0.99**인 대여소들이 나온다. 그것만
    보면 모델이 한 일은 *"원래 비는 곳을 순서대로 부른 것"* 일 수 있고,
    그렇다면 GBM은 필요 없다 — 과거 빈도표만으로 같은 답이 나온다.

    그래서 **평소 빈도가 비슷한 것끼리 갈라** 그 안에서 다시 잰다. 각 구간
    안에서도 이겨야 *"오늘 지금 상태"* 를 배운 것이다.

    📌 실제로 이 검사가 판단을 바꿨다 — 처음 측정에서 예측이 고른 곳은
    **100%가 평소 90% 이상 비는 대여소**였고 순수요 `mu`가 0이었다(아무도
    안 쓰는 곳이라 맞히기는 쉽지만 채울 이유가 없다). 구간을 갈라 보고서야
    **모든 구간에서 이긴다**는 것이 확인됐다.
    """
    print("\n" + "=" * 62)
    print("평소 빈도 구간별 — '늘 비는 곳 부르기'가 아닌지")
    print("=" * 62)

    work = test.copy()
    work["예측"] = prob
    work["실제"] = actual
    edges = [0, 0.2, 0.4, 0.6, 0.8, 0.95, 1.01]
    work["구간"] = pd.cut(work["대여소시간대빔비율"], edges, right=False)

    for name, sub in work.groupby("구간", observed=True):
        if len(sub) < 500:
            continue
        a = sub["실제"].values
        b_freq = brier(sub["대여소시간대빔비율"].values, a)
        b_now = brier(sub["지금빔"].values.astype(float), a)
        b_gbm = brier(sub["예측"].values, a)
        표 = "✅" if b_gbm < min(b_freq, b_now) else "🔴 진다"
        print(f"  평소빔 {str(name):<13} n={len(sub):>7,} 실제 {a.mean():.2f}"
              f" | 빈도 {b_freq:.4f} · 지속 {b_now:.4f}"
              f" · GBM {b_gbm:.4f}  {표}")

    가운데 = work[(work["대여소시간대빔비율"] >= 0.2)
                & (work["대여소시간대빔비율"] < 0.8)]
    if 가운데.empty:
        return
    a = 가운데["실제"].values
    print(f"\n  🔴 애매한 구간(0.2~0.8)이 진짜 시험대다 — n={len(가운데):,}"
          f" · 실제 빔 {a.mean():.1%}")
    print(f"     ① 지속 {brier(가운데['지금빔'].values.astype(float), a):.4f}"
          f" · ② 빈도 {brier(가운데['대여소시간대빔비율'].values, a):.4f}"
          f" · ③ GBM {brier(가운데['예측'].values, a):.4f}")

    K = 50
    말 = []
    for 이름, 열 in (("예측", "예측"), ("빈도", "대여소시간대빔비율"),
                   ("지속", "지금빔")):
        hits = [g.nlargest(K, 열)["실제"].mean()
                for _, g in 가운데.groupby("ts") if len(g) >= K]
        if hits:
            말.append(f"{이름} {np.mean(hits):.1%}")
    if 말:
        print(f"     상위 {K}곳 실제로 빈 비율 — {' · '.join(말)}"
              f" (무작위 {a.mean():.1%})")


def report_leak_checks(train, test, 피처, actual) -> None:
    """🔴 **이겼을 때가 가장 위험하다** — 7번 시도가 그렇게 코드에 들어갔다.

    라벨을 섞어 학습하면 반드시 무작위 수준(≈0.25)이 나와야 한다. 여기서도
    이기면 피처 어딘가에 타깃이 새고 있다는 뜻이다.
    """
    print("\n" + "=" * 62)
    print("누출 검사")
    print("=" * 62)

    겹침 = train["ts"].max() >= test["ts"].min()
    print(f"  학습 마지막 {train['ts'].max()} · 검증 첫 {test['ts'].min()}")
    print(f"  시간 겹침: {'🔴 있다 — 누출이다' if 겹침 else '없다'}")

    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
    except ImportError:
        return
    rng = np.random.default_rng(42)
    섞은 = train.copy()
    섞은["타깃"] = rng.permutation(섞은["타깃"].values)
    m = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.1,
                                       max_depth=6, random_state=42)
    m.fit(섞은[피처], 섞은["타깃"])
    b = brier(m.predict_proba(test[피처])[:, 1], actual)
    판정 = "정상" if b > 0.24 else "🔴 라벨을 섞었는데도 맞힌다 — 누출이다"
    print(f"  라벨 섞은 학습: Brier {b:.4f}  ({판정})")


if __name__ == "__main__":
    raise SystemExit(main())
