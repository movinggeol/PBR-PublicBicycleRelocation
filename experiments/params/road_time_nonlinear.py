"""이동시간 비선형 보정(F6): 근거리 과대평가를 **구간을 떼어 놓고** 재서 고칠 수 있나 (EXPERIMENTS 58장, 사전 등록 2026-10-25).

왜: 채택한 식 (3.12)(고정비 + 거리비례, 게이트 A · B)는 1km 이하 구간을 105 ~ 160초 길게 잡는다(원고 3.8 · 8.2).
ML_고도화_계획 F6은 이것을 *게이트 B 뒤, 논문 뒤* 의 과제로 두었고, 게이트 B는 10-12에 끝났다.
사용자 지시(2026-10-25) *"제출 뒤로 미룬 것들도 모두 다 할 수 있는 것 해라"*.

무엇을 묻나: 거리 하나의 등장 회귀(N1) 또는 거리 · 회차 · 요일을 쓰는 GBM(N2)이, **패널에 없던 구간**에서 선형식보다
맞히고 근거리 치우침을 없애나.

어떻게 재나(결과를 보기 전에 정했다. 바꾸지 말 것):
    자료    road_leg 고정 패널, 지금 패널과 같은 구간만(road_time_model.load_legs), 판정일 2026-09-02 ~ 2026-10-24.
            평일 · 휴일은 따로 잰다(같은 회귀에 섞지 않는다)
    검증    구간 하나를 떼어 놓고(leave-one-segment-out, 패널 100구간 = 100겹) 나머지로 배워 그 구간의 관측을 맞힌다.
            계획은 패널에 없는 대여소 짝을 다룬다. 날짜를 떼는 검증(게이트 A)은 같은 구간을 다시 보므로 이 물음에 답하지 못한다
    기준 L  고정비 + 거리비례를 겹마다 다시 맞춘 것. 채택 상수(A)는 이 자료 일부로 맞춘 값이라 참고로만 찍는다
    N1      IsotonicRegression(증가, 범위 밖은 끝값) on 직선 km
    N2      HistGradientBoostingRegressor(제곱 오차, 직선 km · 회차 시작 시 · 요일, km에 단조 증가 제약, 아래 GBM_PARAMS)
    판정    후보마다, 평일 · 휴일 따로: ① 떼어 놓은 구간의 MAE가 L보다 10% 이상 낮다
                                       ② 떼어 놓은 1km 이하 구간의 평균 부호 오차(예측 − 실측)가 ±30초 안
            둘 다 서면 통과. 둘 다 통과하면 단순한 N1을 고른다
    통과해도 기본값은 바꾸지 않는다: 바꾸면 원고의 모든 소요시간이 바뀐다. 켤지는 사용자 결정(게이트 A와 같은 절차)

실행:
    python experiments/params/road_time_nonlinear.py                 # 평일 · 휴일 판정
    python experiments/params/road_time_nonlinear.py --day-type holiday
    python experiments/params/road_time_nonlinear.py --sanity        # 표본 크기만(판정 수치를 찍지 않는다)
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np                                                      # noqa: E402
import pandas as pd                                                     # noqa: E402

import project_config                                                   # noqa: E402,F401  (출력 인코딩)
import road_time_model as rtm                                           # noqa: E402
from project_config import duration_start_hour, travel_seconds          # noqa: E402

# 사전 등록 (58장). 결과를 보고 고치지 말 것.
UNTIL = "2026-10-24"                 # 10-25는 수집 중인 날이라 뺀다
MAE_GAIN = 0.10                      # ① 떼어 놓은 구간 MAE가 L보다 이만큼 낮을 것
NEAR_KM = 1.0                        # ② 근거리 = 직선 1km 이하 구간
NEAR_BIAS_SEC = 30.0                 # ② 그 구간의 평균 부호 오차(예측 − 실측) 한계
GBM_PARAMS = dict(loss="squared_error", max_iter=300, learning_rate=0.05, max_leaf_nodes=15,
                  min_samples_leaf=40, random_state=0)
SEGMENT = ["cluster", "leg", "from_id", "to_id"]


def load(day_type: str) -> pd.DataFrame:
    """판정 표본: 지금 패널 구간 · 판정일부터 UNTIL까지 · 한 요일 구분."""
    frame = rtm.load_legs(False, panel_only=True, day_type=day_type, until=UNTIL)
    day = frame["날짜"].astype(str).str.slice(-10)
    frame = frame[day >= rtm.JUDGE_FROM].copy()
    frame["day"] = day[frame.index]
    frame["hour"] = frame["duration"].map(duration_start_hour).astype(float)
    frame["dow"] = pd.to_datetime(frame["day"]).dt.dayofweek.astype(float)
    frame["segment"] = frame[SEGMENT].astype(str).agg("|".join, axis=1)
    return frame.reset_index(drop=True)


def loso_predictions(frame: pd.DataFrame, day_type: str) -> pd.DataFrame:
    """구간 하나씩 떼어 놓고 L · N1 · N2를 배워 그 구간을 맞힌다. A(채택 상수)는 배우지 않는다."""
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.isotonic import IsotonicRegression

    out = frame[["segment", "day", "duration", "straight_km", "road_sec"]].copy()
    out["A"] = travel_seconds(out["straight_km"].to_numpy(), day_type=day_type)
    for name in ("L", "N1", "N2"):
        out[name] = np.nan
    features = ["straight_km", "hour", "dow"]
    for segment in frame["segment"].unique():
        test = frame["segment"] == segment
        train = frame[~test]
        km, sec = train["straight_km"].to_numpy(), train["road_sec"].to_numpy()
        fixed, speed = rtm.fit_linear(km, sec)
        out.loc[test, "L"] = rtm.predict(frame.loc[test, "straight_km"], fixed, speed)
        iso = IsotonicRegression(increasing=True, out_of_bounds="clip").fit(km, sec)
        out.loc[test, "N1"] = iso.predict(frame.loc[test, "straight_km"].to_numpy())
        gbm = HistGradientBoostingRegressor(monotonic_cst=[1, 0, 0], **GBM_PARAMS)
        gbm.fit(train[features].to_numpy(), sec)
        out.loc[test, "N2"] = gbm.predict(frame.loc[test, features].to_numpy())
    return out


def summary(pred: pd.DataFrame) -> pd.DataFrame:
    """모형마다 떼어 놓은 구간의 MAE와 근거리 부호 오차."""
    near = pred["straight_km"] <= NEAR_KM
    rows = []
    for name in ("A", "L", "N1", "N2"):
        error = pred[name] - pred["road_sec"]
        rows.append({"모형": name, "MAE초": error.abs().mean(), "근거리MAE초": error[near].abs().mean(),
                     "근거리부호오차초": error[near].mean(), "먼거리부호오차초": error[~near].mean()})
    return pd.DataFrame(rows).set_index("모형")


def by_band(pred: pd.DataFrame) -> pd.DataFrame:
    """5-F장과 같은 거리 구간별 부호 오차(찍기만 한다)."""
    band = pd.cut(pred["straight_km"], rtm.EDGES, labels=rtm.LABELS)
    table = {"관측": pred.groupby(band, observed=False).size()}
    for name in ("A", "L", "N1", "N2"):
        table[f"{name}부호"] = (pred[name] - pred["road_sec"]).groupby(band, observed=False).mean()
    return pd.DataFrame(table).round(1)


def judge(table: pd.DataFrame) -> dict:
    base = table.loc["L", "MAE초"]
    verdict = {}
    for name in ("N1", "N2"):
        gain = (base - table.loc[name, "MAE초"]) / base
        bias = table.loc[name, "근거리부호오차초"]
        verdict[name] = {"gain": gain, "bias": bias,
                         "①": gain >= MAE_GAIN, "②": abs(bias) <= NEAR_BIAS_SEC}
        verdict[name]["통과"] = verdict[name]["①"] and verdict[name]["②"]
    return verdict


def run(day_type: str, sanity: bool) -> None:
    frame = load(day_type)
    segments = frame["segment"].nunique()
    near_segments = frame.groupby("segment")["straight_km"].first().le(NEAR_KM).sum()
    print(f"\n[{day_type}] 관측 {len(frame):,}개 · 구간 {segments}개(1km 이하 {near_segments}개) · "
          f"날 {frame['day'].nunique()}일({frame['day'].min()} ~ {frame['day'].max()})")
    if sanity:
        return
    pred = loso_predictions(frame, day_type)
    table = summary(pred)
    print("\n떼어 놓은 구간(100겹)의 오차, 초. A = 채택 상수(참고), L = 겹마다 다시 맞춘 선형식")
    print(table.round(1).to_string())
    print("\n거리 구간별 평균 부호 오차(예측 − 실측), 초")
    print(by_band(pred).to_string())
    verdict = judge(table)
    print(f"\n판정 (① MAE가 L보다 {MAE_GAIN:.0%} 이상 낮다 · ② 1km 이하 부호 오차 ±{NEAR_BIAS_SEC:.0f}초 안)")
    for name, v in verdict.items():
        print(f"  {name}: ① {v['gain']:+.1%} {'✅' if v['①'] else '❌'} · ② {v['bias']:+.1f}초 "
              f"{'✅' if v['②'] else '❌'} → {'통과' if v['통과'] else '미달'}")
    passed = [name for name, v in verdict.items() if v["통과"]]
    if not passed:
        print("  → 둘 다 미달: 선형식(3.12)을 그대로 둔다.")
    else:
        pick = "N1" if "N1" in passed else passed[0]
        print(f"  → 통과: {', '.join(passed)}. 등록 규칙으로 {pick}를 고른다. 기본값은 바꾸지 않는다(사용자 결정).")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--day-type", choices=("weekday", "holiday"), help="한쪽만 (기본: 평일과 휴일을 따로 모두)")
    parser.add_argument("--sanity", action="store_true", help="표본 크기만 찍는다(판정 수치 없음)")
    args = parser.parse_args()
    for day_type in ([args.day_type] if args.day_type else ["weekday", "holiday"]):
        run(day_type, args.sanity)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
