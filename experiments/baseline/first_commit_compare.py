"""첫 커밋(a60a871, 2026-06-15)의 계획 알고리즘과 지금 알고리즘을 같은 입력 · 같은 자로 잰다.

**왜 필요한가** — 첫 커밋에는 잰 성과가 남아 있지 않다. 그때 지표는 개선률(계획 달성률) 하나를 콘솔에
찍는 것뿐이었고, VRP에는 시간 계산도 depot 복귀도 없었다. 그래서 "처음보다 무엇이 나아졌나"를 물으면
답할 숫자가 없었다(2026-11-05 사용자 질문). 이 스크립트는 첫 커밋의 계획 단계를 그대로 옮겨 지금 코드와
나란히 돌린다.

**무엇을 묻나** — 시간당 처리량(옮긴 자전거 · 처리 대여소 / 차량·시간), 주행거리, 120분 예산 초과,
결품 · 포화 시간이 두 버전에서 어떻게 다른가.

**같게 둔 것** (차이가 알고리즘에서만 나오게):
  - 입력: 순수요(기본 25년 11월 평일) · 재고 스냅샷(정본 `2026-08-11 real`)
  - ILP: `ilp.solve_cluster_moves()` — 첫 커밋과 식이 같다(대수 가중 이동시간 최소 · 총 이동량 강제).
  - VRP: `vrp.greedy_route()` — 점수식(거리 / 처리량, 노드를 끝내면 ×0.1)이 같다. 지금 코드는 시간 계산과
    마지막 depot 복귀를 붙였을 뿐이고, 첫 커밋에도 그 둘을 붙여 잰다(안 붙이면 첫 커밋이 거리를 33% 덜 잰다).
  - 이동시간: 지금의 `travel_seconds()`(실도로 모형) + 30초/대 · 결품/포화: step4 시뮬레이션
  - 모집단: **전체 대여소(중립)** — 두 버전의 작업 대상이 달라 자기 후보로 재면 분모가 갈린다(EXPERIMENTS 17·18장).

**첫 커밋에서 옮긴 것** (원본: `git show a60a871:<경로>`):
  - 목표 재고: `mu>=0`이면 mu + 1.65·sigma, 아니면 stock + mu → [0, 거치대×1.5]로 자름 →
    10·tanh(·/10)로 상한 → 0 쪽으로 정수화. 계절 보정 · 휴일 구분 없음(25년 11월 평일은 공휴일이 없어
    첫 커밋의 `weekday < 5`와 지금 평일 필터가 같다 — 스크립트가 확인한다).
    ⚠️ 원본의 자르기는 `stats.loc[:, 'target_qty'].clip(inplace=True)`라 pandas 3(CoW)에서는 아무 일도
    안 한다. 당시(Python 3.8 · pandas 1.x/2.x)에는 반영됐다고 보고 **의도대로 자른다.**
  - 작업 대상: |rebal| > 2, pick · drop 각 상위 50곳, 누적합을 작은 쪽 총량에서 자름.
  - 군집: K = ceil(대상 수 / 7), K-Medoids 맨해튼. 원본은 `sklearn_extra` 기본값(alternate · heuristic)이고
    그 패키지는 지금 설치되지 않으므로 **같은 절차를 손으로 다시 짰다**(`kmedoids_alternate_heuristic`).
    ⚠️ 당시 설치본의 기본값이 달랐다면 군집이 달라진다 — 근사다.
  - 군집 조정: 첫 커밋의 `adjust_module.py`를 **git에서 그대로 읽어** 쓴다(alpha 1 · beta 100 · gamma 10,
    반복 200 · 임계 3 · 균형 한계 5).

**판정** — 판정 기준을 미리 정한 실험이 아니라 **기록**이다. 숫자를 그대로 싣는다.

**지금 쪽의 정의** — `baseline_compare.py`의 P(원고 <표 6-3>)와 같다. 운영 계획 경로의 수거 대수 학습
(K, 1.26.375)과 계절 보정(웜업)은 들어가지 않는다.

⚠️ **수집 PC에서 돌린다면** — 첫 커밋의 군집 조정이 회차당 4~5분 걸린다(pandas 슬라이싱, 1.23.8이 고친 병목).
프로세스 우선순위를 낮춰 돌리지만 메모리는 그대로 쓰므로, 정각 틱 직후에 한 회차씩 돌려라
(`--duration _05_10`). 2026-11-05에 메모리가 바닥인 가운데 틱이 늦거나 빠졌다.

사용법:
    python experiments/baseline/first_commit_compare.py --duration _05_10
    python experiments/baseline/first_commit_compare.py --period "25년 11월" --seeds 42,7,13 --out data/first_commit_compare.csv

결과: docs/기록/첫_커밋_대비_비교_2026-11-05.md
"""
import argparse
import subprocess
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]      # experiments/<분류>/ 아래에 있다
sys.path.insert(0, str(ROOT))

import project_config  # noqa: E402,F401  — 최상단에서 UTF-8 출력 가드를 거친다
import numpy as np      # noqa: E402
import pandas as pd     # noqa: E402
from scipy.spatial.distance import cdist  # noqa: E402

from experiments._shared import CANONICAL_RUN_LABEL, load_baseline  # noqa: E402
from project_config import align_day_type  # noqa: E402

FIRST_COMMIT = "a60a871"
ADJUST_PATH = "step1 (작업대상 선정 및 클러스터링)/adjust_module.py"


def load_first_commit_adjust():
    """첫 커밋의 `adjust_module.py`를 git에서 읽어 모듈로 싣는다 (파일을 만들지 않는다)."""
    source = subprocess.run(
        ["git", "-c", "core.quotePath=false", "show", f"{FIRST_COMMIT}:{ADJUST_PATH}"],
        cwd=ROOT, capture_output=True, check=True).stdout.decode("utf-8")
    module = types.ModuleType("first_commit_adjust")
    exec(compile(source, f"{FIRST_COMMIT}:{ADJUST_PATH}", "exec"), module.__dict__)
    return module


# ------------------------------------------------------------ 첫 커밋 step0 (calculate_target_qty)
def first_rebal(net_wd: pd.DataFrame, st_info: pd.DataFrame, duration: str,
                z=1.65, up_limit=1.5, max_capacity=10) -> pd.DataFrame:
    start, end = int(duration.split("_")[1]), int(duration.split("_")[2])
    if start < end:
        hours = [f"net_{h:02d}" for h in range(start, end)]
    else:
        hours = [f"net_{h:02d}" for h in list(range(start, 24)) + list(range(0, end))]
    tmp = net_wd.copy()
    tmp["s"] = tmp[hours].sum(axis=1)
    stats = tmp.groupby("station_id")["s"].agg(["mean", "std"])
    stats.columns = ["mu", "sigma"]
    stats = stats.fillna(0).reset_index()
    stats = stats.merge(st_info[["station_id", "parking_lot", "stock"]], how="left", on="station_id")
    stats = stats[~stats["stock"].isna()].copy()

    pos = stats["mu"] >= 0
    stats["target_qty"] = np.where(pos, stats["mu"] + z * stats["sigma"], stats["stock"] + stats["mu"])
    stats["target_qty"] = stats["target_qty"].clip(lower=0, upper=stats["parking_lot"] * up_limit)
    r = stats["target_qty"] - stats["stock"]
    r = max_capacity * np.tanh(r / max_capacity)
    stats["rebal_qty"] = np.where(r >= 0, np.floor(r), np.ceil(r)).astype(int)
    return stats


# ------------------------------------------------------------ 첫 커밋 step1 (1.top_st_clustering)
def first_select(rebal: pd.DataFrame, st_info: pd.DataFrame) -> pd.DataFrame:
    st = (rebal[rebal["rebal_qty"].abs() > 2]
          .merge(st_info[["station_id", "station_name", "lat", "lon"]], how="left", on="station_id"))
    st = st[["station_id", "station_name", "lat", "lon", "parking_lot", "stock",
             "target_qty", "rebal_qty", "mu", "sigma"]]
    pick = st[st["rebal_qty"] < 0].sort_values("rebal_qty", ascending=True).iloc[:50].copy()
    drop = st[st["rebal_qty"] > 0].sort_values("rebal_qty", ascending=False).iloc[:50].copy()
    cut = min(abs(pick["rebal_qty"].sum()), drop["rebal_qty"].sum())
    pick["c"] = pick["rebal_qty"].cumsum()
    drop["c"] = drop["rebal_qty"].cumsum()
    pick = pick[pick["c"].abs() <= cut].drop(columns="c")
    drop = drop[drop["c"] <= cut].drop(columns="c")
    return pd.concat([pick, drop], axis=0)


def kmedoids_alternate_heuristic(X: np.ndarray, k: int, max_iter=300) -> np.ndarray:
    """`sklearn_extra.cluster.KMedoids(method='alternate', init='heuristic')`의 절차를 다시 짠 것.

    초기 메도이드 = 다른 점까지 거리 합이 가장 작은 k개. 배정 → 군집마다 거리 합 최소인 점으로 메도이드 교체를
    메도이드가 안 바뀔 때까지 되풀이한다. 씨앗이 쓰이지 않아 결정적이다.
    """
    D = cdist(X, X, metric="cityblock")
    med = np.argpartition(D.sum(axis=1), k - 1)[:k]
    for _ in range(max_iter):
        old = med.copy()
        labels = np.argmin(D[med, :], axis=0)
        for c in range(k):
            idx = np.where(labels == c)[0]
            if idx.size == 0:
                continue
            sub = D[np.ix_(idx, idx)]
            best = np.argmin(sub.sum(axis=1))
            cur = sub[:, np.argmax(idx == med[c])].sum()
            if sub[best].sum() < cur:
                med[c] = idx[best]
        if np.all(old == med):
            break
    return np.argmin(D[med, :], axis=0)


def first_cluster(pick_drop: pd.DataFrame, size=7) -> pd.DataFrame:
    pick_drop = pick_drop.copy()
    k = int(np.ceil(len(pick_drop) / size))
    pick_drop["cluster"] = kmedoids_alternate_heuristic(pick_drop[["lat", "lon"]].values, k)
    return pick_drop


def first_adjust(pick_drop: pd.DataFrame, am) -> pd.DataFrame:
    """첫 커밋 `adjust_clustering()`의 반복문 — 함수들은 그 커밋의 `adjust_module`을 그대로 부른다."""
    MAX_ITER, THRESHOLD, BALANCE_LIMIT = 200, 3, 5
    K = pick_drop["cluster"].nunique()
    K_SIZE = len(pick_drop) / K
    MIN_SIZE, MAX_SIZE = int(np.floor(K_SIZE - 1)), int(np.ceil(K_SIZE + 1))
    alpha, beta, gamma = 1, 100, 10
    for _ in range(MAX_ITER):
        balance = pick_drop.groupby("cluster")["rebal_qty"].sum()
        c_size = pick_drop.groupby("cluster").size()
        if balance.abs().max() <= THRESHOLD:
            break
        score = am.compute_objective(pick_drop, K, alpha, beta, gamma)
        centers = am.compute_medoids(pick_drop)
        fc, tc = am.select_cluster_candidates(balance, c_size, K_SIZE, BALANCE_LIMIT)
        if not fc or not tc:
            break
        improved = False
        for _, f, t in am.make_cluster_pairs(centers, fc, tc):
            if f == t:
                continue
            if not am.check_size_constraint(f, t, c_size, balance, MIN_SIZE, MAX_SIZE, BALANCE_LIMIT):
                continue
            nodes = am.get_movable_nodes(pick_drop, f, t, balance, centers)
            if nodes.empty:
                continue
            pick_drop, improved = am.try_move_node(pick_drop, nodes, t, score, K, alpha, beta, gamma)
            if improved:
                break
        if not improved:
            break
    return pick_drop


class _KeepClusters:
    """`plan_with_clusters()`에 넘기는 step1 자리 — 군집이 이미 정해져 있으므로 그대로 돌려준다."""

    @staticmethod
    def make_clustering(frame, random_state=None):
        return frame


# ------------------------------------------------------------ 측정
def measure(bc, version, duration, seed, candidates, routes, net, population, seconds):
    delta = bc.executed_delta(routes)
    sim = bc.simulate_pair(net, population, delta, duration)
    rs = bc.route_stats(routes)
    if routes.empty:
        work, per_vehicle_h = routes, pd.Series(dtype=float)
    else:
        work = routes[routes["action"].isin(["pick", "drop"])]
        per_vehicle_h = routes.groupby("cluster")["cum_sec"].max() / 3600
    vh = float(per_vehicle_h.sum())
    stations = int(work["to_id"].nunique()) if not work.empty else 0
    return {
        "version": version, "duration": duration, "seed": seed, "seconds": round(seconds, 1),
        "cand": len(candidates),
        "cand_pick": int((candidates["rebal_qty"] < 0).sum()),
        "cand_drop": int((candidates["rebal_qty"] > 0).sum()),
        "plan_qty": int(candidates["rebal_qty"].clip(lower=0).sum()),
        "vehicles": rs["vehicles"], "stations": stations, "stops": int(len(work)),
        "bikes_done": rs["bikes_done"], "km": round(rs["km"], 1),
        "veh_h": round(vh, 2),
        "travel_h": round(float(routes["travel_sec"].sum() / 3600), 2) if not routes.empty else 0.0,
        "work_h": round(float(routes["work_sec"].sum() / 3600), 2) if not routes.empty else 0.0,
        "avg_min": round(float(per_vehicle_h.mean() * 60), 1) if vh else 0.0,
        "max_min": round(rs["max_min"], 1), "over120": rs["over"],
        "bikes_per_vh": round(rs["bikes_done"] / vh, 2) if vh else None,
        "stations_per_vh": round(stations / vh, 2) if vh else None,
        **{k: round(v, 4) for k, v in sim.items()},
    }


def lower_priority():
    """수집 PC에서 수집기보다 뒤로 물러선다(윈도우만). 자식 CBC도 이 우선순위를 물려받는다."""
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.SetPriorityClass(k32.GetCurrentProcess(), 0x4000)   # BELOW_NORMAL_PRIORITY_CLASS


def main():
    parser = argparse.ArgumentParser(description="첫 커밋 대비 계획 알고리즘 비교")
    parser.add_argument("--period", default="25년 11월", help='순수요 기간 (첫 커밋의 기본값)')
    parser.add_argument("--duration", default="_05_10,_10_15,_15_20", help="시간대 (콤마 구분)")
    parser.add_argument("--run-label", default=CANONICAL_RUN_LABEL, help="재고 스냅샷 (기본: 정본)")
    parser.add_argument("--seeds", default="42,7,13", help="지금 쪽 K-Medoids 씨앗 (첫 커밋 쪽은 결정적)")
    parser.add_argument("--out", default="", help="결과 CSV 경로")
    args = parser.parse_args()

    lower_priority()
    t0 = time.time()
    bc = load_baseline()
    step1 = bc.load_step1()
    align_day_type("weekday", bc.ilp_mod, bc.vrp_mod, bc.kpi_mod, step1)
    solver = bc.ilp_mod.build_solver()
    net, st_info, warmup = bc.quiet(bc.load_inputs, args.period, args.run_label, "weekday", 0, "")
    weekday = pd.to_datetime(net["날짜"]).dt.weekday < 5
    if not weekday.all():
        raise SystemExit("이 기간의 평일에 공휴일이 끼어 있다 — 첫 커밋(weekday < 5)과 지금 평일 필터가 달라진다.")
    population = st_info[["station_id", "stock", "parking_lot"]]
    am = load_first_commit_adjust()
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    print(f"준비 {time.time() - t0:.1f}초 · 기간 {args.period} · 스냅샷 {args.run_label}", flush=True)

    rows = []
    for duration in [d.strip() for d in args.duration.split(",") if d.strip()]:
        bc.reset_population_guard()
        t = time.time()
        candidates = first_select(first_rebal(net, st_info, duration), st_info)
        clustered = first_cluster(candidates)
        t_adjust = time.time()
        clustered = bc.quiet(first_adjust, clustered, am).copy()
        adjust_sec = time.time() - t_adjust
        routes = bc.quiet(bc.plan_with_clusters, clustered, _KeepClusters(), solver,
                          adjust=False, seed=0)[1]
        rows.append(measure(bc, "first", duration, "-", candidates, routes, net, population,
                            time.time() - t))
        print(f"{duration} 첫 커밋  {time.time() - t:6.1f}초 (조정 {adjust_sec:.1f}초)"
              f"  옮김 {rows[-1]['bikes_done']}대  차량·시간당 {rows[-1]['bikes_per_vh']}대", flush=True)

        base = bc.build_candidates(net, st_info, duration, None, warmup, 0, step1)
        for seed in seeds:
            t = time.time()
            _clustered, routes = bc.plan_with_clusters(base.copy(), step1, solver, adjust=True, seed=seed)
            rows.append(measure(bc, "now", duration, seed, base, routes, net, population,
                                time.time() - t))
            print(f"{duration} 지금 s{seed:<3d} {time.time() - t:6.1f}초"
                  f"  옮김 {rows[-1]['bikes_done']}대  차량·시간당 {rows[-1]['bikes_per_vh']}대", flush=True)
        if args.out:
            pd.DataFrame(rows).to_csv(args.out, index=False, encoding="utf-8-sig")

    frame = pd.DataFrame(rows)
    print(frame.to_string(index=False))
    if args.out:
        print(f"\n결과를 저장했습니다: {args.out}")


if __name__ == "__main__":
    main()
