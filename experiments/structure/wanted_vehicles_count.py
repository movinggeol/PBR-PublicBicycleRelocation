"""군집 수 어림을 '옮길 수 있는 대수'로 세면 K가 바뀌나: 2026-10-08 점검 ①의 재측정 (EXPERIMENTS 60장, 사전 등록 2026-10-25).

왜: `top_st_clustering.wanted_vehicles()`는 처리 대수를 배송 합으로 센다. 그런데 `select_top_unbalanced_st()`의 cut_point는
누적합이 cut_point 이하인 행만 남기므로, 배송 쪽 합계가 더 작은 회차에서는 수거 합이 배송 합보다 작아질 수 있다
([-7, -7, 5, 5] → 수거 7 · 배송 10). ILP가 옮기는 것은 군집마다 min(수거, 배송)이라 배송 합으로 세면 K가 위쪽으로 어긋난다.
사용자 결정(2026-10-08) *"바꾸지 않는다, 논문 제출 뒤 재측정과 함께 본다"* → 2026-10-25 *"제출 뒤로 미룬 것들도 모두 다 할 수 있는 것 해라"*.

무엇을 재나(결과를 보기 전에 정했다. 바꾸지 말 것):
    입력    정본 스냅샷 '2026-08-11 real' · 부록 A의 19개월 · 평일 · 원고의 세 회차 · 계절 보정 없음(원고 6장 baseline_compare와 같다)
    규칙    지금 = 배송 합, 새 = min(|수거 합|, 배송 합). 나머지(대여소 수 × 12.5분 · 여유 1.4 · 120분)는 같다.
            회차당 상한(VEHICLES_PER_ROUND)으로 자른 K로 견준다
    판정 묶음  원고가 군집을 쓰는 조합: z = TARGET_Z 후보(제안 방법 P · B3) 19개월 × 세 회차, z = 0 후보(B2) 25년 11월 × 세 회차.
            z = 0의 나머지 달은 찍기만 한다(원고의 19개월 표에는 B2가 없다)
    판정    잘린 K가 바뀌는 조합이 0이면 새 규칙으로 고친다(원고 수치가 움직이지 않는다).
            하나라도 있으면 그 조합마다 같은 후보를 두 규칙으로 풀어(씨앗 42 · 7 · 13, 같은 모집단) 결품 · 포화 · 이동거리 ·
            120분 넘는 군집 · 끝낸 대수를 찍고, 원고 수치가 움직이므로 기본값은 사용자 결정으로 남긴다
    찍기만  저장된 계획(pick_drop)으로 같은 셈. 원고 5장의 격자(z · γ · 상한)는 세지 않았다

실행:
    python experiments/structure/wanted_vehicles_count.py
    python experiments/structure/wanted_vehicles_count.py --sanity     # 입력 크기만(K를 찍지 않는다)
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments" / "baseline"))

import numpy as np                                                      # noqa: E402
import pandas as pd                                                     # noqa: E402

import baseline_compare as bc                                           # noqa: E402
import db                                                               # noqa: E402
import project_config                                                   # noqa: E402,F401  (출력 인코딩)
from experiments._shared import CANONICAL_RUN_LABEL                     # noqa: E402
from project_config import VEHICLES_PER_ROUND                           # noqa: E402

PERIODS = ["24년 08월", "24년 09월", "24년 10월", "24년 11월", "24년 12월", "25년 01월", "25년 02월", "25년 03월",
           "25년 04월", "25년 05월", "25년 06월", "25년 07월", "25년 08월", "25년 09월", "25년 10월", "25년 11월",
           "26년 01월", "26년 02월", "26년 03월"]          # 부록 A의 19개월(scripts/gate_a_rerun.ps1과 같다)
DURATIONS = ("_05_10", "_10_15", "_15_20")
B2_PERIOD = "25년 11월"
SEEDS = (42, 7, 13)


def bikes(pick_drop: pd.DataFrame, rule: str) -> float:
    drop = float(pick_drop.loc[pick_drop["rebal_qty"] > 0, "rebal_qty"].sum())
    pick = float(-pick_drop.loc[pick_drop["rebal_qty"] < 0, "rebal_qty"].sum())
    return drop if rule == "drop" else min(pick, drop)


def k_by(step1, pick_drop: pd.DataFrame, rule: str) -> int:
    """`wanted_vehicles()`와 같은 식. 처리 대수만 규칙으로 바꾼다."""
    if pick_drop.empty:
        return 1
    work_min = bikes(pick_drop, rule) * (step1.PICK_TIME_SEC + step1.DROP_TIME_SEC) / 60.0
    travel_min = len(pick_drop) * step1.TRAVEL_MIN_PER_STATION
    needed = (work_min + travel_min) * step1.CLUSTER_IMBALANCE_ALLOWANCE / step1.TIME_BUDGET_MINUTES
    return max(1, int(np.ceil(needed)))


def moved_rule(step1):
    """독립 모듈 인스턴스(`bc.load_step1()`)에 끼울 새 규칙. 원래 함수는 다른 인스턴스에 그대로 남는다."""
    def wanted(pick_drop, geo=None):
        return k_by(step1, pick_drop, "moved")
    return wanted


def count(step1) -> pd.DataFrame:
    rows = []
    for period in PERIODS:
        bc.reset_population_guard()
        net, st_info, warmup = bc.quiet(bc.load_inputs, period, CANONICAL_RUN_LABEL, "weekday", 0, "")
        for duration in DURATIONS:
            for z in (None, 0.0):
                cand = bc.build_candidates(net, st_info, duration, z, warmup, 0, step1)
                if cand.empty:
                    continue
                old, new = k_by(step1, cand, "drop"), k_by(step1, cand, "moved")
                assert old == step1.wanted_vehicles(cand), "식을 옮겨 적다 어긋났다"
                rows.append({"period": period, "duration": duration, "z": "TARGET_Z" if z is None else "0",
                             "stations": len(cand), "drop": bikes(cand, "drop"), "moved": bikes(cand, "moved"),
                             "K_old": old, "K_new": new,
                             "K_old_cap": min(old, VEHICLES_PER_ROUND), "K_new_cap": min(new, VEHICLES_PER_ROUND)})
    frame = pd.DataFrame(rows)
    frame["judged"] = (frame["z"] == "TARGET_Z") | (frame["period"] == B2_PERIOD)
    frame["changed"] = frame["K_old_cap"] != frame["K_new_cap"]
    return frame


def outcomes(changed: pd.DataFrame, step1_old, step1_new) -> pd.DataFrame:
    """K가 바뀐 판정 조합마다 같은 후보 · 같은 모집단으로 두 규칙을 풀어 견준다."""
    solver = bc.ilp_mod.build_solver()
    rows = []
    for period, part in changed.groupby("period", sort=False):
        bc.reset_population_guard()
        net, st_info, warmup = bc.quiet(bc.load_inputs, period, CANONICAL_RUN_LABEL, "weekday", 0, "")
        for _, combo in part.iterrows():
            z = None if combo["z"] == "TARGET_Z" else 0.0
            cand = bc.build_candidates(net, st_info, combo["duration"], z, warmup, 0, step1_old)
            base = bc.build_candidates(net, st_info, combo["duration"], None, warmup, 0, step1_old)
            zero = bc.build_candidates(net, st_info, combo["duration"], 0.0, warmup, 0, step1_old)
            population = (pd.concat([base, zero], ignore_index=True)
                          .drop_duplicates(subset="station_id", keep="first"))
            for seed in SEEDS:
                for rule, step1 in (("drop", step1_old), ("moved", step1_new)):
                    _, routes = bc.plan_with_clusters(cand.copy(), step1, solver, adjust=True, seed=seed)
                    sim = bc.simulate_pair(net, population, bc.executed_delta(routes), combo["duration"])
                    rows.append({"period": period, "duration": combo["duration"], "z": combo["z"], "seed": seed,
                                 "rule": rule, **sim, **bc.route_stats(routes)})
    return pd.DataFrame(rows)


def stored_runs() -> pd.DataFrame:
    """저장된 계획(pick_drop)으로 같은 셈. 찍기만 한다."""
    step1 = bc.load_step1()
    with db.session() as conn:
        frame = pd.read_sql("SELECT run_label, duration, rebal_qty, cluster FROM pick_drop", conn)
    rows = []
    for (label, duration), part in frame.groupby(["run_label", "duration"]):
        rows.append({"run_label": label, "duration": duration, "clusters": part["cluster"].nunique(),
                     "K_old": min(k_by(step1, part, "drop"), VEHICLES_PER_ROUND),
                     "K_new": min(k_by(step1, part, "moved"), VEHICLES_PER_ROUND)})
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sanity", action="store_true", help="입력 크기만 찍는다(K를 찍지 않는다)")
    parser.add_argument("--out", default="", help="조합별 표를 CSV로 저장할 경로")
    args = parser.parse_args()

    step1_old, step1_new = bc.load_step1(), bc.load_step1()
    step1_new.wanted_vehicles = moved_rule(step1_new)
    bc.align_day_type("weekday", bc.ilp_mod, bc.vrp_mod, bc.kpi_mod, step1_old, step1_new)

    if args.sanity:
        with db.session() as conn:
            have = {r[0] for r in conn.execute("SELECT DISTINCT period FROM net_demand")}
            info = conn.execute("SELECT COUNT(*) FROM station_info WHERE run_label = ?",
                                (CANONICAL_RUN_LABEL,)).fetchone()[0]
        print(f"정본 '{CANONICAL_RUN_LABEL}' 대여소 {info}곳 · 19개월 중 DB에 있는 달 {sum(p in have for p in PERIODS)}개")
        return 0

    frame = count(step1_old)
    if args.out:
        frame.to_csv(args.out, index=False, encoding="utf-8")
    judged = frame[frame["judged"]]
    split = frame[frame["drop"] != frame["moved"]]
    print(f"\n조합 {len(frame)}개(판정 {len(judged)}개) · 배송 합과 옮길 수 있는 대수가 갈린 조합 {len(split)}개"
          f"(판정 묶음 {int(split['judged'].sum())}개, 차이 중앙 {float((split['drop'] - split['moved']).median()) if len(split) else 0:.0f}대"
          f" · 최대 {float((split['drop'] - split['moved']).max()) if len(split) else 0:.0f}대)")
    print(f"잘리기 전 K가 바뀐 조합 {int((frame['K_old'] != frame['K_new']).sum())}개 · "
          f"상한 {VEHICLES_PER_ROUND}대로 자른 K가 바뀐 조합 {int(frame['changed'].sum())}개"
          f"(판정 묶음 {int(judged['changed'].sum())}개)")
    print(frame[frame["changed"]].to_string(index=False) if frame["changed"].any() else "  (바뀐 조합 없음)")

    print("\n저장된 계획으로 같은 셈(찍기만):")
    stored = stored_runs()
    moved = stored[stored["K_old"] != stored["K_new"]]
    print(f"  실행 × 회차 {len(stored)}개 가운데 K가 바뀌는 것 {len(moved)}개")
    if len(moved):
        print(moved.to_string(index=False))

    changed = judged[judged["changed"]]
    if changed.empty:
        print("\n판정: 판정 묶음에서 K가 바뀐 조합이 없다 → 등록대로 새 규칙으로 고친다(원고 수치가 움직이지 않는다).")
        return 0
    print(f"\n판정: 판정 묶음 {len(changed)}개 조합에서 K가 바뀐다 → 원고 수치가 움직인다. 기본값은 사용자 결정으로 남긴다.")
    result = outcomes(changed, step1_old, step1_new)
    keys = ["period", "duration", "z", "seed"]
    wide = result.pivot_table(index=keys, columns="rule",
                              values=["stockout_after", "saturation_after", "km", "over", "max_min", "bikes_done", "vehicles"])
    print("\n두 규칙의 결과(씨앗 셋 평균, 새 − 지금)")
    diff = pd.DataFrame({name: wide[(name, "moved")] - wide[(name, "drop")]
                         for name in ("vehicles", "stockout_after", "saturation_after", "km", "over", "max_min", "bikes_done")})
    print(diff.groupby(level=["period", "duration", "z"]).mean().round(3).to_string())
    print("\n전체 평균(새 − 지금):")
    print(diff.mean().round(3).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
