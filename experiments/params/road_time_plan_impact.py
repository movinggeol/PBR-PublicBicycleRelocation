"""이동시간 식을 등장 회귀(F6 N1)로 바꾸면 계획이 얼마나 달라지나 (EXPERIMENTS 61장, 사전 등록 2026-10-26).

왜: 58장에서 등장 회귀(N1)가 패널 구간을 떼어 놓은 검증을 평일 · 휴일 모두 통과했다. 켤지는 사용자 결정이고(원고의 모든 소요시간이
바뀐다), 그 근거로 *켜면 무엇이 달라지는지* 를 원고 6장과 같은 자로 잰다. 사용자 결정(2026-10-26) *"권장대로 하자"*.

어떻게 재나(결과를 보기 전에 정했다. 바꾸지 말 것):
    입력    원고 6장 19개월 반복과 같다: 정본 스냅샷 '2026-08-11 real' · 부록 A의 19개월 · 평일 · 세 회차 · 씨앗 42 · 7 · 13 ·
            방법 P · B0 · B1 · 계절 보정 없음(`repeat_eval.collect` 그대로)
    두 시계 L = 지금 기본값(식 (3.12), 평일 320.4초 + 32.11km/h)
            N1 = 58장 평일 표본(2026-09-02 ~ 10-24, 11,600관측)에 맞춘 등장 회귀. 표본의 최장(21.2km)을 넘는 다리는 그 끝값에서
                 평일 속도(32.11km/h)로 잇는다. 이 실험 안에서만 ILP · VRP 모듈의 `travel_seconds`를 갈아 끼운다(기본값은 그대로)
    다시 재기  두 시계로 세운 계획 모두를 두 시계로 다시 잰다(다리 거리 · 작업 시간에서). N1 시계는 58장에서 실측에 더 가까운 쪽이다
    읽기    (가) 원고 결론: P 대 B1의 결품(재배치 뒤) 차가 회차마다 L에서와 같은 쪽 · 같은 유의(부호순위 p < 0.05, n = 57)인가
            (나) 계획의 값: P(N1) − P(L)의 결품 · 포화 중앙값 차와 부호순위 p, 끝낸 대수 · 이동거리의 평균 비
            (다) 시간: N1 시계로 잰 120분 넘는 군집 수와 최장 소요를 P(L) · P(N1)에서 견준다. P(L)을 L 시계로 잰 값(원고에 실린 쪽)도 나란히 둔다
    해석    켤지는 사용자 결정이다. 이 장은 보고만 한다. *원고 결론이 그대로* = (가)가 세 회차 모두 같다.
            *운영에 손해가 없다* = (나)에서 결품이 나빠지지 않고(중앙값 차 > 0이면서 p < 0.05인 회차가 없다) (다)에서 N1 시계로 잰
            넘는 군집이 P(L)보다 많지 않다

실행:
    python experiments/params/road_time_plan_impact.py --out data/exp61_f6_impact_1026.csv
    python experiments/params/road_time_plan_impact.py --sanity     # N1 함수와 갈아 끼우기만 확인(계획을 세우지 않는다)
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments" / "baseline"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np                                                      # noqa: E402
import pandas as pd                                                     # noqa: E402

import project_config                                                   # noqa: E402,F401  (출력 인코딩)
import baseline_compare as bc                                           # noqa: E402
import repeat_eval                                                      # noqa: E402
import road_time_nonlinear as rtn                                       # noqa: E402
from experiments._shared import CANONICAL_RUN_LABEL                     # noqa: E402
from project_config import ROAD_SPEED_KMPH_WEEKDAY, TIME_BUDGET_MINUTES, travel_seconds   # noqa: E402

PERIODS = ["24년 08월", "24년 09월", "24년 10월", "24년 11월", "24년 12월", "25년 01월", "25년 02월", "25년 03월",
           "25년 04월", "25년 05월", "25년 06월", "25년 07월", "25년 08월", "25년 09월", "25년 10월", "25년 11월",
           "26년 01월", "26년 02월", "26년 03월"]          # 부록 A의 19개월(scripts/gate_a_rerun.ps1과 같다)
SEEDS = [42, 7, 13]
DURATIONS = ["_05_10", "_10_15", "_15_20"]
METHODS = ["P", "B0", "B1"]
RUN_LABEL = CANONICAL_RUN_LABEL          # 원고 6장의 정본 스냅샷
KEYS = ["period", "seed", "duration", "method"]


def fit_n1():
    """58장 평일 표본에 맞춘 등장 회귀를 `travel_seconds`와 같은 모양의 함수로 돌려준다."""
    from sklearn.isotonic import IsotonicRegression

    frame = rtn.load("weekday")
    km, sec = frame["straight_km"].to_numpy(), frame["road_sec"].to_numpy()
    iso = IsotonicRegression(increasing=True, out_of_bounds="clip").fit(km, sec)
    lo, hi = float(km.min()), float(km.max())
    top = float(iso.predict([hi])[0])

    def n1_seconds(km, speed_kmph=None, day_type="weekday"):
        if day_type != "weekday":
            raise ValueError("이 실험의 N1은 평일 표본으로만 맞췄다")
        arr = np.asarray(km, dtype=float)
        out = iso.predict(np.clip(arr, lo, hi).ravel()).reshape(arr.shape)
        out = np.where(arr > hi, top + (arr - hi) * 3600.0 / ROAD_SPEED_KMPH_WEEKDAY, out)
        return float(out) if np.ndim(km) == 0 else out

    return n1_seconds, {"관측": len(frame), "최단km": lo, "최장km": hi}


def linear_seconds(km):
    return travel_seconds(np.asarray(km, dtype=float), day_type="weekday")


def make_route_stats(n1_seconds):
    """`bc.route_stats`에 두 시계로 다시 잰 최장 소요 · 넘는 군집 수를 덧붙인다."""
    original = bc.route_stats

    def route_stats(routes):
        out = original(routes)
        for name, clock in (("L", linear_seconds), ("N1", n1_seconds)):
            if routes.empty:
                out[f"max_min_{name}"], out[f"over_{name}"] = 0.0, 0
                continue
            sec = clock(routes["distance_km"].to_numpy()) + routes["work_sec"].to_numpy()
            minutes = pd.Series(sec, index=routes.index).groupby(routes["cluster"]).sum() / 60.0
            out[f"max_min_{name}"] = float(minutes.max())
            out[f"over_{name}"] = int((minutes > TIME_BUDGET_MINUTES).sum())
        return out

    return route_stats


def collect(clock: str, n1_seconds) -> pd.DataFrame:
    originals = (bc.ilp_mod.travel_seconds, bc.vrp_mod.travel_seconds)
    if clock == "N1":
        bc.ilp_mod.travel_seconds = bc.vrp_mod.travel_seconds = n1_seconds
    try:
        args = argparse.Namespace(periods=PERIODS, seeds=SEEDS, durations=DURATIONS, methods=METHODS,
                                  run_label=RUN_LABEL, day_type="weekday", warmup_days=0, warmup_period="")
        frame = repeat_eval.collect(args)
    finally:
        bc.ilp_mod.travel_seconds, bc.vrp_mod.travel_seconds = originals
    frame["clock"] = clock
    return frame


def signed_rank(diff: pd.Series) -> float:
    from scipy.stats import wilcoxon
    diff = diff.dropna()
    if (diff != 0).sum() < 5:
        return float("nan")
    return float(wilcoxon(diff).pvalue)


def report(frame: pd.DataFrame) -> None:
    lin, iso = (frame[frame["clock"] == c].set_index(KEYS) for c in ("L", "N1"))
    print("\n(가) 원고 결론: P − B1 결품(재배치 뒤) 차, 중앙값 · 부호순위 p")
    same = True
    for duration in DURATIONS:
        cells = []
        for name, part in (("L", lin), ("N1", iso)):
            p = part.xs(("P", duration), level=("method", "duration"))["stockout_after"]
            b1 = part.xs(("B1", duration), level=("method", "duration"))["stockout_after"]
            diff = (p - b1).dropna()
            cells.append((float(diff.median()), signed_rank(diff)))
        side = [np.sign(m) * (pv < 0.05) for m, pv in cells]
        same &= side[0] == side[1]
        print(f"  {duration}: L {cells[0][0]:+.4f} (p={cells[0][1]:.2g}) · N1 {cells[1][0]:+.4f} (p={cells[1][1]:.2g})"
              f" → {'같다' if side[0] == side[1] else '달라졌다'}")
    print(f"  (가) {'세 회차 모두 같다' if same else '달라진 회차가 있다'}")

    print("\n(나) P(N1) − P(L): 결품 · 포화 중앙값 차(p), 끝낸 대수 · 이동거리 평균 비")
    worse = False
    for duration in DURATIONS:
        a = lin.xs(("P", duration), level=("method", "duration"))
        b = iso.xs(("P", duration), level=("method", "duration"))
        so, sa = (b["stockout_after"] - a["stockout_after"]), (b["saturation_after"] - a["saturation_after"])
        p_so = signed_rank(so)
        worse |= so.median() > 0 and p_so < 0.05
        print(f"  {duration}: 결품 {so.median():+.4f} (p={p_so:.2g}) · 포화 {sa.median():+.4f} (p={signed_rank(sa):.2g})"
              f" · 끝낸 대수 {b['bikes_done'].mean() / a['bikes_done'].mean():.3f} · 거리 {b['km'].mean() / a['km'].mean():.3f}"
              f" · 군집 {a['vehicles'].mean():.1f} → {b['vehicles'].mean():.1f}")

    print("\n(다) 120분 넘는 군집(합) · 최장 소요(평균, 분). 같은 계획을 두 시계로 다시 잰다")
    rows = []
    for method in ("P", "B1"):
        for name, part in (("L로 세움", lin), ("N1로 세움", iso)):
            m = part.xs(method, level="method")
            rows.append({"방법": method, "계획": name, "넘음(L 시계)": int(m["over_L"].sum()), "넘음(N1 시계)": int(m["over_N1"].sum()),
                         "최장(L 시계)": round(m["max_min_L"].mean(), 1), "최장(N1 시계)": round(m["max_min_N1"].mean(), 1)})
    table = pd.DataFrame(rows)
    print(table.to_string(index=False))
    p_rows = table[table["방법"] == "P"].set_index("계획")
    more = p_rows.loc["N1로 세움", "넘음(N1 시계)"] > p_rows.loc["L로 세움", "넘음(N1 시계)"]
    print(f"\n해석: 원고 결론 {'그대로' if same else '달라짐'} · 운영 손해 "
          f"{'있음' if (worse or more) else '없음'} (결품 악화 {'있음' if worse else '없음'} · N1 시계 넘는 군집 {'늘었다' if more else '늘지 않았다'})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sanity", action="store_true", help="N1 함수와 갈아 끼우기만 확인한다(계획을 세우지 않는다)")
    parser.add_argument("--out", default="", help="원자료 CSV 경로")
    args = parser.parse_args()

    n1_seconds, info = fit_n1()
    print(f"N1: 평일 표본 {info['관측']:,}관측 · {info['최단km']:.2f} ~ {info['최장km']:.1f}km")
    bc.route_stats = make_route_stats(n1_seconds)
    if args.sanity:
        probe = np.array([0.0, 0.3, 1.0, 5.0, 25.0])
        print("  km      :", probe.tolist())
        print("  L  (초) :", np.round(linear_seconds(probe), 1).tolist())
        print("  N1 (초) :", np.round(n1_seconds(probe), 1).tolist())
        before = bc.ilp_mod.km_to_travel_seconds(1.0)
        bc.ilp_mod.travel_seconds = bc.vrp_mod.travel_seconds = n1_seconds
        swapped = (bc.ilp_mod.km_to_travel_seconds(1.0), bc.vrp_mod._travel_sec(1.0))
        print(f"  1km ILP · VRP: L {before:.1f}초 → N1 {swapped[0]:.1f} · {swapped[1]:.1f}초 (갈아 끼우기 확인)")
        return 0

    frame = pd.concat([collect("L", n1_seconds), collect("N1", n1_seconds)], ignore_index=True)
    if args.out:
        frame.to_csv(args.out, index=False, encoding="utf-8")
        print(f"\n원자료: {args.out} ({len(frame)}행)")
    check = frame[(frame["clock"] == "L") & (frame["method"] != "B0")]
    gap = (check["max_min"] - check["max_min_L"]).abs().max()
    print(f"[점검] L로 세운 계획의 최장 소요: 플래너 누적 대 다시 잰 L 시계 최대 차 {gap:.2f}분(거리 반올림만큼이어야 한다)")
    report(frame)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
