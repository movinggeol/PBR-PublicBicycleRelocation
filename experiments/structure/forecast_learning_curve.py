"""결품 · 포화 예측의 학습 곡선 — 재고를 더 모으면 예측이 좋아지나 (2026-10-04).

사용자 물음(2026-10-04): *"수집기를 통해 기간이 늘어날수록 유의미한 성능 향상을 관찰할 수 있었나?"*
답은 *"잰 적이 없다"* 였다. 결품 예측(ML 9번)은 09-10(평일 13일 · 낮)에 지속 규칙 대비 −30.7%, 09-28(24시간)에
−19.9%였지만 창과 모집단이 달라 그 둘을 이어 곡선으로 읽을 수 없다. 이 스크립트는 **검증 날을 고정하고 학습
날만 늘려** 같은 자로 잰다.

## 설계 (결과를 보기 전에 정했다)

- **평일만** — 휴일은 온전한 날이 적고, 평일 · 휴일을 섞지 않는다는 저장소 규약을 따른다.
- **12:00~15:50만 행으로 쓴다.** 09-14 전에는 수집 창이 낮뿐이었다(09~17시 → 07~22시 → 24시간). 학습 날을
  늘리면 시간대 구성까지 같이 바뀌므로, 모든 날이 덮는 시간대 안에서 지연 피처(최장 3시간 · 18틱)와 1시간 뒤
  타깃이 그날 창 안에 들도록 자른다. 그래서 9 · 11번의 등록 실행과 **절대값은 다르다** — 같은 것은 모형 ·
  피처 · 하이퍼파라미터 · 베이스라인이다.
- **쓸 날**: 09:00~16:50(48틱) 가운데 40틱 이상 관측된 평일 — 2026-10-04 집 PC 자료로 21일.
- **두 원점**(검증 4일씩, 겹치지 않는다):
  · A — 마지막 4일. 학습 후보는 그 앞 전부.
  · B — A 앞의 4일. 학습 후보는 그 앞 전부(A의 날은 쓰지 않는다).
- **학습 k일 = 검증 바로 앞의 k일**(가까운 날부터 넓힌다). k = 3 · 5 · 7 · 10 · 14 · 17(후보 수를 넘으면 후보 전부).
- 씨앗 셋(42 · 7 · 13)의 Brier 평균.

## 판정 (사전 등록)

- **향상**: GBM Brier가 k=5 대비 최대 k에서 **3% 이상** 낮다. 두 원점 **모두**면 ✅, 둘 다 아니면 ❌, 갈리면 ⚠️ 보류.
- **평평**: 마지막 한 단계(직전 k → 최대 k)의 감소가 **1% 미만**.
- 같은 판정을 ② 과거 빈도(대여소 × 시간대 빈도표)에도 적용한다.

## 결과를 보기 전의 예상 (틀려도 고치지 않는다)

1. GBM은 **❌ — 5일 넘어서는 3% 미만**. 모형이 주로 쓰는 것이 *지금 재고와 최근 몇 틱*이라 날이 늘어도
   배울 것이 많지 않다(9번은 13일에, 11번은 24시간 자료에서 이미 이겼다).
2. ② 과거 빈도는 **✅ — 3% 이상 좋아진다**. 대여소 × 시간대 칸마다 표본이 하루 몇 개뿐이라 날 수가 곧 표본이다.
3. GBM의 지속 규칙 대비 우위는 k에 따라 크게 변하지 않는다.

⚠️ **'양'과 '오래됨'이 함께 움직인다** — k를 늘리면 더 먼 날(8월 말)이 들어온다. 이 곡선이 답하는 것은
*"지금까지 모은 것을 더 써서 나아지나"* 이지 *"같은 철의 날이 더 있으면 나아지나"* 가 아니다.

실행:
    python experiments/structure/forecast_learning_curve.py
    python experiments/structure/forecast_learning_curve.py --target stockout
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
import saturation_forecast as satf  # noqa: E402
import stockout_forecast as sf  # noqa: E402
from project_config import is_holiday  # noqa: E402

ROW_HOURS = (12, 16)           # 행으로 쓰는 시각 [12시, 16시)
COVER_HOURS = (9, 17)          # 그날 창이 덮어야 하는 시간대 [9시, 17시) = 48틱
MIN_COVER_TICKS = 40           # 48틱 중
TEST_DAYS = 4
TRAIN_SIZES = (3, 5, 7, 10, 14, 17)
SEEDS = (42, 7, 13)
GAIN_THRESHOLD = 0.03          # k=5 → 최대 k에서 이만큼 낮아야 '향상'
PLATEAU = 0.01                 # 마지막 한 단계가 이보다 작으면 '평평'
BASE_K = 5


def qualified_days(timestamps: pd.Series) -> list:
    """09:00~16:50에서 40틱 이상 관측된 **평일**만, 날짜순으로."""
    ts = pd.Series(pd.to_datetime(timestamps).unique())
    window = ts[(ts.dt.hour >= COVER_HOURS[0]) & (ts.dt.hour < COVER_HOURS[1])]
    ticks = window.groupby(window.dt.date).size()
    return sorted(day for day, n in ticks.items()
                  if n >= MIN_COVER_TICKS and not is_holiday(day))


def origins(days: list) -> dict:
    """두 원점 — (검증 날, 학습 후보). A의 날은 B의 학습에도 검증에도 들지 않는다."""
    a_test, a_pool = days[-TEST_DAYS:], days[:-TEST_DAYS]
    b_test, b_pool = a_pool[-TEST_DAYS:], a_pool[:-TEST_DAYS]
    return {"A": (a_test, a_pool), "B": (b_test, b_pool)}


def train_sizes(pool_size: int) -> list:
    """후보 수를 넘는 k는 버리고, 후보 전부를 마지막 k로 넣는다."""
    sizes = [k for k in TRAIN_SIZES if k < pool_size]
    return sizes + [pool_size]


def row_mask(long: pd.DataFrame, days: list) -> pd.Series:
    hour = long["ts"].dt.hour
    return (long["ts"].dt.date.isin(days)
            & (hour >= ROW_HOURS[0]) & (hour < ROW_HOURS[1]))


def fit_predict(train, test, features, seed) -> np.ndarray:
    """9 · 11번과 같은 모형 · 하이퍼파라미터 — 씨앗만 바꾼다."""
    from sklearn.ensemble import HistGradientBoostingClassifier

    model = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.1, max_depth=6,
                                           random_state=seed)
    model.fit(train[features], train["타깃"])
    return model.predict_proba(test[features])[:, 1]


TARGETS = {
    # 이름: (지금 상태 컬럼, 빈도 컬럼, 피처, 과거 빈도를 붙이는 함수)
    "stockout": ("지금빔", "대여소시간대빔비율", sf.FEATURES, sf.add_station_history),
    "saturation": ("지금포화", "대여소시간대포화비율", satf.FEATURES, satf.add_station_history),
}


def curve(long: pd.DataFrame, target: str, days: list) -> pd.DataFrame:
    now_col, freq_col, features, add_history = TARGETS[target]
    rows = []
    for origin, (test_days, pool) in origins(days).items():
        test_base = long[row_mask(long, test_days)]
        actual = test_base["타깃"].to_numpy()
        for k in train_sizes(len(pool)):
            train = long[row_mask(long, pool[-k:])].copy()
            train, test = add_history(train, [train, test_base.copy()])
            gbm = [sf.brier(fit_predict(train, test, features, s), actual) for s in SEEDS]
            rows.append({
                "대상": target, "원점": origin, "k": k,
                "학습시작": str(pool[-k]), "학습행": len(train),
                "GBM": float(np.mean(gbm)), "GBM폭": float(np.ptp(gbm)),
                "지속": sf.brier(test[now_col].to_numpy(dtype=float), actual),
                "빈도": sf.brier(test[freq_col].to_numpy(dtype=float), actual),
                "검증": f"{test_days[0]}~{test_days[-1]}",
            })
            print(f"  [{target} {origin}] k={k:2d} GBM {rows[-1]['GBM']:.4f}"
                  f" · 빈도 {rows[-1]['빈도']:.4f}", flush=True)
    return pd.DataFrame(rows)


def judge(table: pd.DataFrame, column: str) -> dict:
    """원점마다 (k=5 대비 최대 k 감소율, 마지막 단계 감소율)."""
    out = {}
    for origin, part in table.groupby("원점"):
        part = part.sort_values("k")
        base = part.loc[part["k"] == BASE_K, column]
        if base.empty:
            continue
        last, prev = part[column].iloc[-1], part[column].iloc[-2]
        out[origin] = (1 - last / base.iloc[0], 1 - last / prev)
    return out


def verdict(gains: dict) -> str:
    hits = [gain >= GAIN_THRESHOLD for gain, _ in gains.values()]
    if hits and all(hits):
        return "✅ 늘수록 좋아진다"
    if not any(hits):
        return f"❌ {BASE_K}일 넘어 늘려도 {GAIN_THRESHOLD:.0%} 미만"
    return "⚠️ 원점에 따라 갈린다 — 보류"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", choices=("both", *TARGETS), default="both")
    args = parser.parse_args(argv)
    targets = list(TARGETS) if args.target == "both" else [args.target]

    with db.session() as conn:
        raw = sf.load_grid(conn)
        label, capacity = satf.load_capacity(conn)
    days = qualified_days(raw["ts"])
    print(f"쓸 평일 {len(days)}일 ({days[0]} ~ {days[-1]}) · 행 {ROW_HOURS[0]}:00~{ROW_HOURS[1] - 1}:50")
    for origin, (test_days, pool) in origins(days).items():
        print(f"  원점 {origin}: 검증 {test_days[0]}~{test_days[-1]} · 학습 후보 {len(pool)}일"
              f" · k = {train_sizes(len(pool))}")
    print(f"  거치대 = '{label}' station_info (포화 라벨)\n")

    tables = []
    for target in targets:
        if target == "stockout":
            long = sf.build_features(raw, sf.DEFAULT_HORIZON)
        else:
            long = satf.build_features(satf.label_saturation(raw, capacity), satf.DEFAULT_HORIZON)
        tables.append(curve(long, target, days))
        del long
    table = pd.concat(tables, ignore_index=True)

    print("\n" + "=" * 70)
    print("학습 곡선 — Brier (낮을수록 좋다, GBM은 씨앗 셋 평균)")
    print("=" * 70)
    shown = table.assign(**{"GBM 지속대비": (table["GBM"] / table["지속"] - 1) * 100})
    print(shown[["대상", "원점", "k", "학습시작", "학습행", "GBM", "GBM폭", "지속", "빈도",
                 "GBM 지속대비"]].to_string(index=False, float_format=lambda v: f"{v:.4f}"))

    print("\n판정 (사전 등록: k=5 → 최대 k에서 3% 이상 · 두 원점 모두)")
    for target, part in table.groupby("대상", sort=False):
        for column in ("GBM", "빈도"):
            gains = judge(part, column)
            detail = " · ".join(f"{o} {g:+.1%}(마지막 단계 {s:+.1%}{', 평평' if s < PLATEAU else ''})"
                                for o, (g, s) in gains.items())
            print(f"  {target:<10} {column:<4} {verdict(gains)}   [{detail}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
