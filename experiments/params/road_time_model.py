"""이동시간 모형을 **쌓인 실측으로 다시 추정한다** — 계절·요일 안정성 (1.26.39).

5-F장(1.26.7)에서 `이동시간 = 275초 + 직선km × 3600/27.3`이 상수 속도(25km/h)보다
표본 밖 오차를 42% 줄인다는 것을 쟀다. 그러나 그것은 **하루치 한 번**이었고,
그래서 채택하지 않고 `USE_ROAD_MODEL`을 꺼 둔 채 남겨 두었다.

이 스크립트는 `tools/collect_road_time.py`가 매일 쌓는 고정 패널로 그 계수를
다시 추정하고, **날짜에 따라 흔들리는지**를 본다. 묻는 것은 셋이다.

    ① 계수가 날마다 얼마나 흔들리나      ← 흔들리면 상수로 박을 수 없다
    ② 회차(시간대)마다 다른가            ← 다르면 회차별 계수가 필요하다
    ③ 다른 날로 넘어가도 맞히나          ← 날짜 하나를 빼고 학습해 그 날로 검증

**판정 기준**(미리 정해 둔다 — 결과를 보고 정하면 안 된다):

    · 채택: 표본 밖 MAE가 상수 속도 가정보다 뚜렷이(20% 이상) 낮고,
            날짜별 계수의 변동계수가 15% 미만
    · 회차별 분리: 회차별 계수의 폭이 날짜별 변동보다 뚜렷이 클 때만

실행:
    python experiments/params/road_time_model.py
    python experiments/params/road_time_model.py --include-pipeline
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

import db
from project_config import ROAD_FIXED_SEC, ROAD_SPEED_KMPH, VEHICLE_SPEED_KMPH

PROBE_PREFIX = "roadprobe-"

# 5-F장과 같은 거리 구간을 쓴다 — 그때 값과 나란히 놓고 볼 수 있어야 한다.
EDGES = [0, 0.5, 1, 2, 5, 10, 20, 999]
LABELS = ["0~0.5", "0.5~1", "1~2", "2~5", "5~10", "10~20", "20+"]

# 판정 기준 (위 문서 참고). 결과를 보고 고치지 말 것.
MAE_GAIN_THRESHOLD = 0.20        # 상수 속도 대비 표본 밖 MAE 감소폭
CV_THRESHOLD = 0.15              # 날짜별 계수의 변동계수 상한
MIN_DAYS = 10                    # 판정에 필요한 수집 일수

# 🔴 **판정용 표본은 09-02부터 센다** (EXPERIMENTS 9장 사전 등록).
# 08-31은 패널을 커밋으로 나르기 전이고, 09-01은 로컬에서 만든 패널로 재
# 100구간 중 31개가 어긋났다(`_20_05` 회차도 빠졌다). 이 스크립트는 어긋난
# 구간만 버리고 그날을 **한 날로 세므로**, 그대로 두면 사전 등록보다 이틀
# 느슨해진다 — 2026-09-11에 실제로 스크립트가 "10일 · 통과"라고 말했으나
# 등록 기준으로는 8일이었다. 결과를 본 뒤 기준을 앞당기는 것과 같아지므로
# 여기서 막는다.
JUDGE_FROM = "2026-09-02"


def panel_segments() -> set:
    """지금 패널이 정의하는 구간 집합. 파일이 없으면 빈 집합(거르지 않는다)."""
    path = ROOT / "tools" / "road_panel.json"
    if not path.exists():
        return set()
    spec = json.loads(path.read_text(encoding="utf-8"))
    return {(int(c["chain"]), leg,
             c["points"][leg]["id"], c["points"][leg + 1]["id"])
            for c in spec.get("chains", [])
            for leg in range(len(c["points"]) - 1)}


def load_legs(include_pipeline: bool, panel_only: bool = True,
              day_type: str = "weekday") -> pd.DataFrame:
    """road_leg에서 쓸 만한 구간을 읽는다.

    ⚠️ **`roadprobe` 접두사만으로는 부족하다** (2026-09-02 실측). 패널이 도중에
    바뀌면 접두사는 같은데 **구간이 다른 날**이 섞인다 — 실제로 09-01이 그랬다
    (100구간 중 31개가 어긋났다). 그러면 날짜별 계수의 흔들림이 *교통 때문인지
    구간이 바뀌어서인지* 가릴 수 없는데, **그것이 채택 기준 둘 중 하나다.**

    그래서 기본값으로 **지금 패널과 같은 구간만** 남긴다. `--all-legs`로 끌 수
    있지만, 껐을 때 무엇이 섞이는지 아래에서 알린다.

    🔴 **평일·휴일도 섞지 않는다 (1.26.158).** `collect_road_time.py`가
    `--day-type`으로 나눠 수집한 뒤 `runs.day_type`에 남기므로, 여기서도 그
    값으로 걸러야 한다 — 그러지 않으면 이 저장소 전체가 지키는 "평일과 휴일은
    절대 섞지 마라" 규약을 이 스크립트만 어기게 된다.

    ⚠️ **다만 그 기록은 1.26.159부터 있다.** 그 전 수집분은 `day_type`이
    NULL이라 기록만 믿으면 통째로 빠진다 — 아래에서 라벨로 되짚는다.
    """
    with db.session() as conn:
        frame = pd.read_sql(
            "SELECT road_leg.run_label, road_leg.duration, cluster, leg,"
            " from_id, to_id, straight_km, road_sec, observed_at, start_time,"
            " runs.day_type"
            " FROM road_leg LEFT JOIN runs"
            " ON runs.run_label = road_leg.run_label", conn)

    frame["패널"] = frame["run_label"].str.startswith(PROBE_PREFIX)

    # 🔴 **`runs.day_type`만 믿으면 1.26.159 이전 수집분이 통째로 빠진다.**
    # 그 컬럼은 1.26.159에서 처음 기록하기 시작했고, 그 전 6일(08-31~09-08)은
    # 전부 NULL이다 — `== day_type` 비교는 NULL을 어느 쪽에도 넣지 않으므로
    # 판정용 표본이 5일에서 1일로 줄었다(2026-09-09에 실제로 그렇게 나왔다).
    # 라벨이 사실을 알고 있다: 휴일분만 `roadprobe-holiday-`를 달고, 그 접두어가
    # 없으면 평일분이다. 그래서 **기록이 있으면 기록을, 없으면 라벨을** 쓴다.
    라벨상_휴일 = frame["run_label"].str.startswith(PROBE_PREFIX + "holiday-")
    실제_구분 = frame["day_type"].where(
        frame["day_type"].notna(), 라벨상_휴일.map({True: "holiday", False: "weekday"}))
    frame = frame[~frame["패널"] | (실제_구분 == day_type)].copy()
    if not include_pipeline:
        frame = frame[frame["패널"]]
    else:
        # 파이프라인 실행분에는 **1.26.4 이전 파라미터로 받은 값**이 섞여 있다
        # (startTime 2017년 저녁 고정 · carType=대형화물차). start_time이 비어
        # 있는 것이 그것이고, 배율이 1.55~1.58로 높게 나온다. 섞으면 계수가
        # 그쪽으로 끌린다 — 2026-09-02에 실제로 겪었다(EXPERIMENTS.md 5-D장).
        옛것 = int((~frame["패널"] & frame["start_time"].isna()).sum())
        if 옛것:
            print(f"[!] 파이프라인 실행분 중 {옛것}구간은 start_time이 비어 있습니다"
                  " — 1.26.4 이전 파라미터로 받은 값입니다.")
            print("    배율이 1.55~1.58로 높아 계수를 끌어당깁니다."
                  " 판정에는 쓰지 마십시오.")

    expected = panel_segments()
    if expected:
        key = list(zip(frame["cluster"], frame["leg"],
                       frame["from_id"], frame["to_id"]))
        frame["패널일치"] = [k in expected for k in key]
        어긋남 = frame[frame["패널"] & ~frame["패널일치"]]
        if not 어긋남.empty:
            날짜 = sorted(어긋남["run_label"].unique())
            print(f"[!] 지금 패널과 다른 구간이 {len(어긋남)}개 있습니다"
                  f" ({', '.join(날짜)}).")
            if panel_only:
                print("    → 제외하고 잽니다. 섞으려면 --all-legs 를 주십시오.")
                frame = frame[~frame["패널"] | frame["패널일치"]]
            else:
                print("    → **섞어서 잽니다** — 날짜별 계수의 흔들림이 교통 탓인지"
                      " 구간이 바뀐 탓인지 가릴 수 없습니다.")

    # 날짜: 패널은 라벨에서, 파이프라인 실행분은 observed_at에서 뽑는다.
    date = frame["run_label"].str.replace(PROBE_PREFIX, "", regex=False)
    date = date.where(frame["패널"], frame["observed_at"].str.slice(0, 10))
    frame["날짜"] = date

    frame = frame.dropna(subset=["straight_km", "road_sec"])
    # 0km 구간(같은 자리 재방문)과 음수는 모형이 배울 것이 없다.
    frame = frame[(frame["straight_km"] > 0) & (frame["road_sec"] > 0)]
    return frame.reset_index(drop=True)


def fit_linear(km, sec):
    """sec ≈ a + b·km. (고정비 초, 유효 속도 km/h)를 돌려준다."""
    design = np.column_stack([np.ones(len(km)), km])
    coef, *_ = np.linalg.lstsq(design, sec, rcond=None)
    fixed, per_km = float(coef[0]), float(coef[1])
    speed = 3600.0 / per_km if per_km > 0 else float("nan")
    return fixed, speed


def predict(km, fixed, speed):
    return fixed + np.asarray(km) * 3600.0 / speed


def mae(a, b):
    return float(np.abs(np.asarray(a) - np.asarray(b)).mean())


def section_distance(frame: pd.DataFrame) -> pd.DataFrame:
    """거리 구간별 실측 배율·유효 속도. 5-F장 표와 같은 모양."""
    part = frame.copy()
    part["구간"] = pd.cut(part["straight_km"], EDGES, labels=LABELS)
    part["추정초"] = part["straight_km"] * 3600.0 / VEHICLE_SPEED_KMPH
    part["배율"] = part["road_sec"] / part["추정초"]
    grouped = part.groupby("구간", observed=False).agg(
        구간수=("road_sec", "size"),
        직선km=("straight_km", "sum"),
        실도로초=("road_sec", "sum"),
        배율중앙=("배율", "median"),
    ).reset_index()
    grouped["유효kmh"] = grouped["직선km"] / (grouped["실도로초"] / 3600.0)
    return grouped[["구간", "구간수", "유효kmh", "배율중앙"]]


def per_group_coefficients(frame: pd.DataFrame, key: str) -> pd.DataFrame:
    """묶음(날짜/회차)마다 계수를 따로 뽑는다. 흔들림을 보려는 것이다."""
    rows = []
    for value, group in frame.groupby(key):
        if len(group) < 20:                 # 계수 둘을 뽑기엔 너무 적다
            continue
        fixed, speed = fit_linear(group["straight_km"].to_numpy(),
                                  group["road_sec"].to_numpy())
        total_km = group["straight_km"].sum()
        rows.append({
            key: value,
            "구간수": len(group),
            "고정비초": round(fixed, 1),
            "속도kmh": round(speed, 2),
            "전체배율": round(group["road_sec"].sum()
                            / (total_km * 3600.0 / VEHICLE_SPEED_KMPH), 3),
        })
    return pd.DataFrame(rows)


def leave_one_day_out(frame: pd.DataFrame) -> pd.DataFrame:
    """날짜 하나를 빼고 학습해 그 날로 검증한다 — 표본 밖 성능."""
    days = sorted(frame["날짜"].dropna().unique())
    if len(days) < 2:
        return pd.DataFrame()

    rows = []
    for day in days:
        train = frame[frame["날짜"] != day]
        test = frame[frame["날짜"] == day]
        if len(train) < 20 or test.empty:
            continue
        fixed, speed = fit_linear(train["straight_km"].to_numpy(),
                                  train["road_sec"].to_numpy())
        km, sec = test["straight_km"].to_numpy(), test["road_sec"].to_numpy()
        rows.append({
            "검증일": day,
            "구간수": len(test),
            "상수속도MAE": round(mae(sec, km * 3600.0 / VEHICLE_SPEED_KMPH), 1),
            "현행모형MAE": round(mae(sec, predict(km, ROAD_FIXED_SEC,
                                                ROAD_SPEED_KMPH)), 1),
            "재추정MAE": round(mae(sec, predict(km, fixed, speed)), 1),
        })
    return pd.DataFrame(rows)


def verdict(frame: pd.DataFrame, by_day: pd.DataFrame, oos: pd.DataFrame) -> None:
    print("\n" + "=" * 66)
    print("판정")
    print("=" * 66)

    days = frame["날짜"].nunique()
    if days < 2:
        print(f"  수집 일수가 {days}일뿐이라 **안정성은 판정할 수 없습니다.**")
        print("  최소 10일(가급적 서로 다른 요일)이 쌓인 뒤 다시 돌리십시오.")
        print("  .\\scripts\\road_collector.ps1 install 로 매 평일 자동 수집됩니다.")
        return

    if by_day.empty or len(by_day) < 2:
        print("  날짜별 계수를 뽑을 만큼 구간이 모이지 않았습니다.")
        return

    cv_fixed = by_day["고정비초"].std(ddof=0) / abs(by_day["고정비초"].mean())
    cv_speed = by_day["속도kmh"].std(ddof=0) / abs(by_day["속도kmh"].mean())
    print(f"  ① 날짜별 계수 변동계수 — 고정비 {cv_fixed:.1%} · 속도 {cv_speed:.1%}"
          f"  (기준 {CV_THRESHOLD:.0%} 미만)")
    stable = max(cv_fixed, cv_speed) < CV_THRESHOLD

    # 🔴 **셋째 조건 — 수집 일수.** 사전 등록은 세 조건의 AND인데 예전에는
    # 앞의 둘만 재고 "두 기준을 모두 통과"라 말했다. 일수를 안 세면 그 문구가
    # 채택 신호로 읽힌다 — 2026-09-11에 실제로 그렇게 출력됐다.
    # 표본 밖 검증을 못 해도 일수는 알려야 하므로 아래 early return보다 앞에 둔다.
    판정일 = sorted(d for d in by_day["날짜"].astype(str) if d >= JUDGE_FROM)
    등록일수 = len(판정일)
    print(f"  ② 판정용 수집 일수 — {등록일수}일"
          f"  (기준 {MIN_DAYS}일 이상 · {JUDGE_FROM}부터 센다)")
    if 등록일수 < days:
        print(f"     ⚠️ 전체 수집은 {days}일이지만 {JUDGE_FROM} 이전"
              f" {days - 등록일수}일은 패널이 달라 세지 않습니다"
              " (EXPERIMENTS 9장 사전 등록).")
    enough = 등록일수 >= MIN_DAYS

    if oos.empty:
        print("  ③ 표본 밖 검증을 할 수 없습니다.")
        return

    base = oos["상수속도MAE"].mean()
    refit = oos["재추정MAE"].mean()
    gain = (base - refit) / base if base else 0.0
    print(f"  ③ 표본 밖 MAE — 상수속도 {base:.1f}초 → 재추정 {refit:.1f}초"
          f" ({gain:+.1%}, 기준 −{MAE_GAIN_THRESHOLD:.0%})")

    if stable and gain >= MAE_GAIN_THRESHOLD and not enough:
        print(f"\n  ⏳ 앞의 두 기준은 통과했으나 **수집 일수가 모자랍니다**"
              f" ({등록일수}/{MIN_DAYS}일).")
        print("     🔴 **여기서 채택하지 마십시오.** 기준 통과를 이유로 표본"
              " 조건을 앞당기면 결과를 보고 기준을 고치는 것과 같아집니다.")
        print(f"     평일만 쌓이므로 {MIN_DAYS - 등록일수}번 더 수집하면 됩니다.")
        return

    if stable and gain >= MAE_GAIN_THRESHOLD:
        fixed, speed = fit_linear(frame["straight_km"].to_numpy(),
                                  frame["road_sec"].to_numpy())
        print(f"\n  ✅ 세 기준을 모두 통과했습니다 — 계수를 갱신할 근거가 있습니다.")
        print(f"     PBR_ROAD_FIXED_SEC={fixed:.0f}"
              f" · PBR_ROAD_SPEED_KMPH={speed:.1f}")
        print(f"     (현행 {ROAD_FIXED_SEC:.0f}초 / {ROAD_SPEED_KMPH}km/h)")
        print("     ⚠️ 켜면 문서의 모든 소요시간 수치가 바뀝니다 — 대조군·γ·z"
              " 실험을 함께 다시 돌려야 재현성이 유지됩니다.")
    elif not stable:
        print("\n  ❌ 계수가 날마다 흔들립니다 — 상수로 박을 수 없습니다."
              " 현행(상수 속도)을 유지하고 한계로 서술하십시오.")
    else:
        print("\n  ❌ 표본 밖 이득이 기준에 못 미칩니다 — 바꿀 값어치가 없습니다.")


def main() -> int:
    parser = argparse.ArgumentParser(description="이동시간 모형 재추정")
    parser.add_argument("--all-legs", action="store_true",
                        help="지금 패널과 다른 구간도 섞어서 잰다"
                             " (기본: 제외 — 흔들림의 원인을 가릴 수 없게 된다)")
    parser.add_argument("--include-pipeline", action="store_true",
                        help="파이프라인 실행분도 함께 쓴다 (구간이 매번 다르다)")
    parser.add_argument("--day-type", choices=("weekday", "holiday"), default="weekday",
                        help="어느 쪽 패널분을 잴지 (기본 weekday). 평일·휴일 계수는"
                             " 절대 같은 회귀에 섞지 않는다")
    args = parser.parse_args()

    frame = load_legs(args.include_pipeline, panel_only=not args.all_legs,
                      day_type=args.day_type)
    if frame.empty:
        print(f"road_leg에 {args.day_type} 패널분이 없습니다."
              " python tools/collect_road_time.py 부터 돌리십시오.")
        return 1

    days = sorted(frame["날짜"].dropna().unique())
    print(f"[{args.day_type}] 구간 {len(frame)}개 · 날짜 {len(days)}일"
          f" ({days[0]} ~ {days[-1]})")
    if args.include_pipeline:
        print(f"  고정 패널 {int(frame['패널'].sum())} /"
              f" 파이프라인 {int((~frame['패널']).sum())}")

    print("\n① 거리 구간별 실측")
    print(section_distance(frame).to_string(index=False))

    print("\n② 전체 적합")
    fixed, speed = fit_linear(frame["straight_km"].to_numpy(),
                              frame["road_sec"].to_numpy())
    km, sec = frame["straight_km"].to_numpy(), frame["road_sec"].to_numpy()
    total_ratio = sec.sum() / (km.sum() * 3600.0 / VEHICLE_SPEED_KMPH)
    print(f"  전체 배율 {total_ratio:.3f}배"
          f" · 유효 속도 {km.sum() / (sec.sum() / 3600.0):.1f} km/h")
    print(f"  적합 계수  고정비 {fixed:.1f}초 + {speed:.2f} km/h"
          f"   (현행 상수 {ROAD_FIXED_SEC:.0f}초 / {ROAD_SPEED_KMPH} km/h)")
    print(f"  표본 안 MAE — 상수속도 {mae(sec, km * 3600.0 / VEHICLE_SPEED_KMPH):.1f}초"
          f" · 현행모형 {mae(sec, predict(km, ROAD_FIXED_SEC, ROAD_SPEED_KMPH)):.1f}초"
          f" · 재적합 {mae(sec, predict(km, fixed, speed)):.1f}초")

    print("\n③ 날짜별 계수 (흔들림을 본다)")
    by_day = per_group_coefficients(frame, "날짜")
    print(by_day.to_string(index=False) if not by_day.empty else "  (표본 부족)")

    print("\n④ 회차별 계수 (시간대가 다르면 계수도 다른가)")
    by_duration = per_group_coefficients(frame, "duration")
    print(by_duration.to_string(index=False) if not by_duration.empty else "  (표본 부족)")

    print("\n⑤ 날짜 하나를 빼고 학습 → 그 날로 검증 (표본 밖)")
    oos = leave_one_day_out(frame)
    print(oos.to_string(index=False) if not oos.empty
          else "  (날짜가 2일 이상 쌓여야 잴 수 있습니다)")

    verdict(frame, by_day, oos)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
