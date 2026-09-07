"""시간 예산을 넘긴 군집을 **쪼개면** 예산을 지킬 수 있나 (TODO P2 2-1, 1.26.97).

## 왜 쪼개나 — 파라미터로 푸는 길이 전부 막혔다

시간 예산(120분) 초과는 이 저장소가 **다섯 번** 파라미터로 풀려다 막힌 문제다.

| 시도 | 결과 |
| --- | --- |
| γ 재조정 (5-C장) | 두 목표를 동시에 개선하는 γ가 **없다** |
| K 낮추기 (5-G) | 군집의 80%가 예산 초과 |
| `wanted_vehicles` 거리항 (5-H) | 초과 28 → 62건, **2.2배** |
| 상한 조정 (17장) | 결품과 예산이 맞바뀐다 — 운영 선택 |
| 차량 증차 | **보유 21대가 상한**(관제센터 유선 문의, 2025년 9월경) |

남은 길은 **구조**다. TODO도 진작 *"시간 예산 관리는 클러스터를 어떻게 묶느냐
(step1)의 문제로 남는다"* 고 지목했다.

## 예산 강제(5-E장)와 무엇이 다른가

`--enforce-time-budget`은 **작업을 버려서** 예산을 지킨다 — 예산에서 멈추고
남은 것을 미집행으로 남긴다. 실측 대가가 **미집행 15.9%·결품 +0.040h**였다.

쪼개기는 **작업을 버리지 않는다.** 초과 군집을 둘로 나눠 차량을 한 대 더 넣는다.
대신 **차량을 더 쓴다** — 그래서 물어야 할 것이 다르다.

    ① 초과가 실제로 사라지나       (쪼갠다고 반드시 예산 안에 들지는 않는다)
    ② 차량이 몇 대나 더 드나        ← **보유 21대를 넘으면 집행 불가다**
    ③ 결품은 어떻게 되나            (작업을 안 버리므로 나빠질 이유가 없다)
    ④ 이동거리는 얼마나 느나        (depot 왕복이 한 번 더 든다)

②가 이 실험의 핵심이다. 5-G장에서 **K를 낮추면** 예산이 깨졌는데, 이것은 반대로
**K를 올리는** 쪽이다. 21대 상한에 부딪히는 지점이 어디인지가 산출물이다.

## 🔴 배율을 **인자로 받는다** — 자를 고치는 일과 분리한다

`cum_sec`은 직선거리 ÷ 25km/h라 실제보다 낙관적이다(5-D장). 그 자를 고치는 일은
**고정 패널이 10일 쌓여야** 판정할 수 있어 지금은 못 한다(TODO '이동시간 추정을
고치는 일').

그래서 이 실험은 배율을 `--road-factor`로 받는다. **기본 1.0은 현행 동작 그대로**고,
계수가 채택되면 그 값만 주면 된다. 이렇게 해 두면 지금 만든 것이 나중에 버려지지
않고, 5-E장의 함정(**틀린 자 위에 예산을 거는 것**)도 피한다.

    python experiments/structure/budget_split.py
    python experiments/structure/budget_split.py --road-factor 1.32   # 5-D 실측
    python experiments/structure/budget_split.py --duration _05_10

⚠️ **판정은 배율 1.0과 1.32를 **둘 다** 보고 한다.** 자가 낙관적이면 초과가 적게
세어져 *"쪼갤 것이 별로 없다"* 는 착시가 생긴다.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
# step 폴더를 sys.path에 밀어 넣지 않는다 — 폴더 이름으로 부른다(1.26.154).

import db                                          # noqa: E402
from step2_optimize import vrp as vrp_mod                              # noqa: E402  (step2)
from project_config import (                       # noqa: E402
    TIME_BUDGET_MINUTES, VEHICLES_PER_ROUND,
)

WINDOWS = ("_05_10", "_10_15", "_15_20")


def load_step1():
    """step1은 파일명이 숫자로 시작해 일반 import가 안 된다."""
    path = ROOT / "step1_cluster" / "top_st_clustering.py"
    spec = importlib.util.spec_from_file_location("top_st_clustering", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["top_st_clustering"] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- 입력

def load_plan(run_label: str, duration: str) -> pd.DataFrame:
    with db.session() as conn:
        return pd.read_sql(
            "SELECT * FROM ilp_plan WHERE run_label = ? AND duration = ?",
            conn, params=[run_label, duration])


def station_points(run_label: str) -> dict:
    with db.session() as conn:
        info = pd.read_sql(
            "SELECT station_id, lat, lon FROM station_info WHERE run_label = ?",
            conn, params=[run_label])
        if info.empty:
            info = pd.read_sql(
                "SELECT station_id, lat, lon FROM station_info"
                " WHERE run_label = (SELECT MAX(run_label) FROM station_info)", conn)
    return {r.station_id: (r.lat, r.lon) for r in info.itertuples()}


def check_stations_known(plan: pd.DataFrame, points: dict) -> None:
    """모든 대여소가 좌표를 갖고 있는지 미리 확인한다 — `run_vrp_plan()`과 같은 규약
    (step2_optimize/vrp.py:250-255).

    🔴 **예전에는 `build_nodes()`가 없는 대여소를 조용히 걸렀다(1.26.127에서 발견).**
    그러면 그 대여소만 빠진 채 측정이 지나가고, 표에는 아무 흔적도 안 남는다.
    실측(2026-09-07, 현재 DB의 ilp_plan·station_info 6개 조합)으로는 걸리는
    대여소가 **없었다** — `station_info`는 run_label당 대여소 전수(1361곳)를 담고
    `ilp_plan`은 그 전수의 부분집합만 쓰므로 구조상 빠질 일이 없다. 다만 그것이
    항상 참이라는 보장은 없으므로, 이제는 **조용히 거르지 않고 무엇이 빠졌는지
    밝히고 멈춘다** — `moved_bikes()`가 서로 다른 조각 수를 비교하는 자리라,
    조용한 결측은 '쪼개서 버린 것'과 '애초에 못 잰 것'을 구분 못 하게 만든다.
    """
    missing = ({*plan["pick_station_id"], *plan["drop_station_id"]} - set(points))
    if missing:
        raise SystemExit(
            f"ILP 계획의 대여소가 좌표(station_info)에 없습니다: "
            f"{sorted(missing)[:5]} … ({len(missing)}곳). "
            f"station_info의 run_label이 ilp_plan과 같은지 확인하세요.")


def build_nodes(cluster_plan: pd.DataFrame, points: dict) -> dict:
    """`run_vrp_plan()`과 **같은 방식**으로 노드를 만든다 — 제 방식대로 만들면
    비교가 성립하지 않는다(budget_enforce.py와 같은 규약).

    좌표 결측은 여기서 조용히 거르지 않는다 — 호출 측이 먼저
    `check_stations_known()`으로 확인해 둔다.
    """
    nodes = {}
    for sid, qty in cluster_plan.groupby("pick_station_id")["qty"].sum().items():
        lat, lon = points[sid]
        nodes[(sid, "pick")] = {"qty": int(qty), "lat": lat, "lon": lon}
    for sid, qty in cluster_plan.groupby("drop_station_id")["qty"].sum().items():
        lat, lon = points[sid]
        nodes[(sid, "drop")] = {"qty": int(qty), "lat": lat, "lon": lon}
    return nodes


# ---------------------------------------------------------------- 소요시간

def route_minutes(rows: list, road_factor: float) -> float:
    """경로 한 벌의 소요시간(분). **배율은 이동 부분에만 곱한다.**

    작업시간(싣기·내리기)은 도로 사정과 무관하므로 곱하면 안 된다. `cum_sec`을
    통째로 곱하는 것이 흔한 실수인데, 그러면 작업시간까지 부풀려진다.
    """
    if not rows:
        return 0.0
    travel = sum(float(r.get("travel_sec", 0) or 0) for r in rows)
    work = sum(float(r.get("work_sec", 0) or 0) for r in rows)
    return (travel * road_factor + work) / 60.0


def solve_cluster(nodes: dict, cluster, road_factor: float):
    """군집 하나를 greedy로 풀고 (행 목록, 분)을 돌려준다.

    ⚠️ **`greedy_route()`는 넘긴 dict를 소모한다**(그쪽 docstring: *"호출 측에서
    소모된다"*). 그래서 반드시 **얕은 복사를 넘긴다** — 원본을 그대로 주면 한 번
    푼 뒤 노드가 비어, 같은 노드를 다시 풀려는 쪽이 **빈 경로를 받고도 오류가
    나지 않는다.** 실제로 이 실험을 만들며 걸렸다(1.26.97): 쪼개기 전 계산이
    노드를 비워 버려 쪼갠 결과가 전부 0으로 나왔는데, **아무도 예외를 던지지
    않아** 표가 그럴듯하게 찍혔다.
    """
    rows = vrp_mod.greedy_route({k: dict(v) for k, v in nodes.items()}, cluster)
    return rows, route_minutes(rows, road_factor)


# ---------------------------------------------------------------- 쪼개기

def moved_bikes(rows: list) -> int:
    """그 경로가 **실제로 재배치를 끝낸** 자전거 수 = min(실은 것, 내린 것).

    🔴 **pick만 세면 안 된다.** 쪼개다 보면 pick만 있고 drop이 없는 조각이 생기는데,
    그 차는 자전거를 싣고 **갈 곳이 없어 그대로 들고 돌아온다.** pick으로 세면
    그것도 "옮겼다"가 되어 **작업이 버려진 것을 못 잡는다** — 실제로 이 실험을
    만들며 걸렸다(1.26.97): 군집 0을 셋으로 쪼개니 9+8+0 = 17로 원본 17과 같아
    검사를 통과했는데, 세 조각 다 **내린 곳이 없었다.**

    재배치가 끝난 대수는 **실은 것과 내린 것 중 작은 쪽**이다. 정상 경로는 둘이
    같고(ILP가 짝지어 둔다), 균형이 깨진 조각에서만 작아진다.
    """
    picked = sum(int(r["qty"]) for r in rows if r["action"] == "pick")
    dropped = sum(int(r["qty"]) for r in rows if r["action"] == "drop")
    return int(min(picked, dropped))


def split_nodes(nodes: dict, seed: int = 42) -> tuple:
    """노드를 좌표로 2등분한다 — **step1과 같은 방식**(K-Medoids)으로 가른다.

    🔴 **여기가 이 실험이 실제로 부딪힌 벽이다.** 좌표로만 가르면 ILP가 짝지어
    둔 pick↔drop 균형이 깨진다. 실측(1.26.97, `_15_20` 군집 10):

        원본     pick 23 · drop 23          122.8분
        half0    pick  9 · drop 23  →  14대를 내릴 수 없다
        half1    pick 14 · drop  0  →  실어도 갈 곳이 없다

    `greedy_route()`는 처리 못 하는 작업을 **조용히 버리고** 짧은 경로를 낸다
    (경고는 찍지만 반환값에는 안 나온다). 그래서 **쪼개면 예산을 지키는 것처럼
    보이는데, 실은 작업을 버려서 짧아진 것**이다 — 예산 강제(5-E장)와 똑같은
    일이 이름만 바꿔 일어난다.

    ⚠️ **그러므로 옮긴 대수를 반드시 함께 세야 한다**(`moved_bikes`). 이 함수는
    균형을 맞추지 않는다 — 맞추려면 좌표가 아니라 **ILP를 다시 풀어야** 하고,
    그것은 쪼개기가 step1만의 문제가 아니라는 뜻이다.
    """
    from kmedoids import KMedoids

    keys = list(nodes)
    if len(keys) < 2:
        return None
    coords = np.array([[nodes[k]["lat"], nodes[k]["lon"]] for k in keys])
    model = KMedoids(n_clusters=2, metric="manhattan", method="fasterpam",
                     random_state=seed)
    labels = model.fit_predict(coords)

    # 값까지 복사한다 — greedy_route가 노드 dict를 소모하기 때문이다
    # (solve_cluster의 경고 참고).
    left = {k: dict(nodes[k]) for k, lab in zip(keys, labels) if lab == 0}
    right = {k: dict(nodes[k]) for k, lab in zip(keys, labels) if lab == 1}
    if not left or not right:
        return None
    return left, right


def split_until_budget(nodes: dict, cluster, budget_min: float,
                       road_factor: float, max_vehicles: int,
                       seed: int = 42) -> list:
    """예산을 넘는 동안 **반복해서** 쪼갠다. 차량 상한에 걸리면 멈춘다.

    반환: [(행 목록, 분), ...] — 쪼갠 결과 전부. 길이가 쓰인 차량 수다.

    **왜 반복인가** — 한 번 쪼개서 예산 안에 든다는 보장이 없다. 196분짜리
    군집을 둘로 나눠도 100분 넘게 남을 수 있다.

    **멈추는 조건이 둘이다.**
      ① 전부 예산 안에 들었다                   ← 성공
      ② 더 쪼개면 차량 상한을 넘는다            ← 집행 불가라 멈춘다
    """
    rows, minutes = solve_cluster(nodes, cluster, road_factor)
    parts = [(nodes, rows, minutes)]

    while True:
        over = [i for i, (_n, _r, m) in enumerate(parts) if m > budget_min]
        if not over:
            break
        if len(parts) >= max_vehicles:
            break                       # 차량 상한 — 더는 못 쪼갠다

        # 가장 오래 걸리는 것부터 쪼갠다
        idx = max(over, key=lambda i: parts[i][2])
        halves = split_nodes(parts[idx][0], seed)
        if halves is None:
            break                       # 더 못 쪼갠다(노드 1개)

        left, right = halves
        new = []
        for half in (left, right):
            r, m = solve_cluster(half, cluster, road_factor)
            new.append((half, r, m))
        parts = parts[:idx] + new + parts[idx + 1:]

    return [(r, m) for _n, r, m in parts]


# ---------------------------------------------------------------- 측정

def measure(label: str, duration: str, road_factor: float,
            budget_min: float, seed: int) -> dict:
    plan = load_plan(label, duration)
    if plan.empty:
        return {}
    points = station_points(label)
    check_stations_known(plan, points)

    before_over = before_max = 0.0
    before_km = 0.0
    before_clusters = before_moved = 0
    after_clusters = after_over = after_moved = 0
    after_max = after_km = 0.0
    hit_limit = 0

    # 회차 전체 군집 수 — 차량 상한을 회차 단위로 걸려면 미리 알아야 한다.
    before_clusters_total = int(plan["cluster"].nunique())

    for cluster, part in plan.groupby("cluster"):
        nodes = build_nodes(part, points)
        if not nodes:
            continue
        rows, minutes = solve_cluster(nodes, cluster, road_factor)
        before_clusters += 1
        before_over += int(minutes > budget_min)
        before_max = max(before_max, minutes)
        before_km += sum(float(r["distance_km"]) for r in rows)
        before_moved += moved_bikes(rows)

        # 🔴 상한은 **회차 전체**에 걸린다 — 군집 하나에 21대를 다 줄 수는 없다.
        # 남은 차량 = 상한 − (이미 쓴 조각) − (아직 안 푼 군집 수, 각 1대는 필요)
        남은군집 = before_clusters_total - before_clusters
        여유 = max(1, VEHICLES_PER_ROUND - after_clusters - 남은군집)
        pieces = split_until_budget(nodes, cluster, budget_min, road_factor,
                                    여유, seed)
        after_clusters += len(pieces)
        for r, m in pieces:
            after_over += int(m > budget_min)
            after_max = max(after_max, m)
            after_km += sum(float(x["distance_km"]) for x in r)
            after_moved += moved_bikes(r)
        if len(pieces) >= VEHICLES_PER_ROUND and any(m > budget_min for _r, m in pieces):
            hit_limit += 1

    return {
        "duration": duration,
        "군집전": before_clusters, "군집후": after_clusters,
        "초과전": int(before_over), "초과후": after_over,
        "최장전": round(before_max, 1), "최장후": round(after_max, 1),
        "거리전": round(before_km, 1), "거리후": round(after_km, 1),
        # 🔴 쪼개면 pick/drop 균형이 깨져 **작업이 버려진다**. 이 둘을 안 보면
        # "예산을 지켰다"가 사실은 "일을 안 했다"인 것을 놓친다(split_nodes 참고).
        "옮긴전": before_moved, "옮긴후": after_moved,
        "상한걸림": hit_limit,
    }


def report(frame: pd.DataFrame, road_factor: float, budget_min: float) -> None:
    print("\n" + "=" * 88)
    print(f"예산 초과 군집을 쪼갠다 — 예산 {budget_min:.0f}분 ·"
          f" 이동 배율 {road_factor} · 차량 상한 {VEHICLES_PER_ROUND}대")
    print("=" * 88)
    if frame.empty:
        print("계획이 없습니다.")
        return
    print(frame.to_string(index=False))

    총초과전 = int(frame["초과전"].sum())
    총초과후 = int(frame["초과후"].sum())
    총차전 = int(frame["군집전"].sum())
    총차후 = int(frame["군집후"].sum())
    km전 = float(frame["거리전"].sum())
    km후 = float(frame["거리후"].sum())
    옮긴전 = int(frame["옮긴전"].sum())
    옮긴후 = int(frame["옮긴후"].sum())

    print("\n합계")
    print(f"  초과 군집 : {총초과전} → {총초과후}")
    print(f"  차량(군집): {총차전} → {총차후}  ({총차후 - 총차전:+d}대)")
    if km전 > 0:
        print(f"  이동거리  : {km전:.1f} → {km후:.1f} km"
              f"  ({(km후 / km전 - 1) * 100:+.1f}%)")
    print(f"  옮긴 대수 : {옮긴전} → {옮긴후}  ({옮긴후 - 옮긴전:+d}대)")

    if frame["상한걸림"].sum():
        print(f"\n  [!] 차량 상한({VEHICLES_PER_ROUND}대)에 걸려 더 못 쪼갠 회차가"
              f" {int(frame['상한걸림'].sum())}건 있습니다.")
        print("      쪼개기로는 이 회차의 초과를 풀 수 없습니다.")

    # 🔴 판정 — 작업을 버리고 얻은 '준수'는 준수가 아니다.
    if 옮긴후 < 옮긴전:
        잃은 = 옮긴전 - 옮긴후
        print(f"\n  🔴 **쪼개면서 {잃은}대({잃은 / max(옮긴전, 1) * 100:.1f}%)를"
              f" 못 옮기게 됐습니다.**")
        print("      좌표로만 가르면 ILP가 짝지어 둔 pick↔drop 균형이 깨져,")
        print("      실을 곳이나 내릴 곳이 없는 작업이 조용히 버려집니다.")
        print("      **초과가 준 것은 일을 덜 했기 때문이지 빨라져서가 아닙니다** —")
        print("      예산 강제(5-E장)와 같은 일이 이름만 바꿔 일어납니다.")
        print("      → 쪼개려면 좌표가 아니라 **ILP를 다시 풀어야** 합니다.")
    elif 총초과후 < 총초과전:
        print("\n  ✅ 작업을 버리지 않고 초과를 줄였습니다.")

    print("\n※ 회차별 군집 수는 그 회차 안에서만 차량 상한을 받습니다"
          " (회차끼리는 차를 돌려 씁니다).")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="예산 초과 군집을 쪼개면 예산을 지킬 수 있나 (P2 2-1)")
    parser.add_argument("--run-label", default="",
                        help="비우면 ilp_plan의 최신 실행분")
    parser.add_argument("--duration", default=",".join(WINDOWS))
    parser.add_argument("--road-factor", type=float, default=1.0,
                        help="이동시간 배율. 1.0=현행(직선/25kmh), 1.32=5-D 실측")
    parser.add_argument("--budget", type=float, default=TIME_BUDGET_MINUTES)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    label = args.run_label
    if not label:
        with db.session() as conn:
            got = pd.read_sql("SELECT MAX(run_label) AS m FROM ilp_plan", conn)
        label = got["m"][0]
    if not label:
        print("ilp_plan이 비어 있습니다. 파이프라인을 한 번 돌리십시오.")
        return 1
    print(f"[실행] run_label = '{label}'")

    durations = [d.strip() for d in args.duration.split(",") if d.strip()]
    rows = [measure(label, d, args.road_factor, args.budget, args.seed)
            for d in durations]
    report(pd.DataFrame([r for r in rows if r]), args.road_factor, args.budget)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
