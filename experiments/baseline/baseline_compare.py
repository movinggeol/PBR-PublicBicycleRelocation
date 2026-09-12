"""대조군 비교 — 제안 방법이 단순한 방법보다 정말 나은가.

지금까지 프로젝트의 모든 수치는 **"재배치 전 → 후"** 자기 비교였다. 그래서
"그냥 부족한 데부터 채우면 되지 않나"에 답하는 숫자가 하나도 없었다
(docs/연구/THESIS.md 3장). 이 스크립트가 그 표를 만든다.

    P   제안 방법        군집(K-Medoids + 불균형 조정) → ILP → VRP
    B0  무재배치         아무 것도 하지 않는다 (하한선)
    B1  그리디           군집·ILP 없이 차량이 가까운 곳부터 계속 훑는다
    B2  평균 목표재고    z = 0 (안전재고 없음), 이후는 P와 같다
    B3  지리 균등 군집   K-Medoids만 쓰고 불균형 조정을 하지 않는다, 이후는 P와 같다

**모든 방법을 같은 자로 잰다.** 판정 기준은 결품 시간이다 — 개선률·목표 도달률은
`target_qty`를 분모로 삼아 z가 다른 B2와는 비교조차 할 수 없다(docs/분석/KPI.md).

**'실제로 옮긴 대수'로 평가한다.** ILP는 군집 안에서 min(pick, drop)만큼만 옮기므로
계획량(rebal_qty)이 전부 집행되지는 않는다. 계획으로 재면 군집·ILP를 건너뛴 B1도
같은 점수가 나와 비교가 성립하지 않는다(이 실험이 그것을 드러냈고, 1.18.4에서
파이프라인의 step4도 집행 기준으로 바뀌었다). `--plan-basis`로 계획 기준도 함께 본다.

측정 코드는 운영 코드를 그대로 부른다 — build_stats·compute_rebal_qty·
select_top_unbalanced_st·make_clustering·adjust_clustering·solve_cluster_moves·
greedy_route·_stockout_hours 전부 파이프라인의 함수다. 측정이 제 방식대로 계산하면
측정이 거짓말을 한다(docs/분석/DEMAND_DISTRIBUTION.md 5장에서 실제로 겪었다).

사용법:
    python experiments/baseline/baseline_compare.py --period "25년 11월"
    python experiments/baseline/baseline_compare.py --period "26년 03월" --duration "_05_10"
    python experiments/baseline/baseline_compare.py --methods P,B1 --seed 7
"""
import argparse
import contextlib
import importlib.util
import io
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pulp

ROOT = Path(__file__).resolve().parents[2]      # experiments/<분류>/ 아래에 있다
sys.path.insert(0, str(ROOT))

# step 폴더를 sys.path에 밀어 넣지 않는다 — **폴더 이름으로 부른다**(1.26.154).
# 예전에는 네 폴더를 각각 sys.path에 넣고 `import ilp`처럼 맨 이름으로 불렀다.
# 그래야만 했던 이유는 `vrp.py`·`top_st_clustering.py`가 형제 모듈을 맨 이름으로
# 부르고 있어서였고, 그 두 줄을 고치자 이 우회가 필요 없어졌다.
import db                                                                # noqa: E402
from pipeline.step0_collect import calculate_target_qty as target_mod    # noqa: E402
from pipeline.step2_optimize import ilp as ilp_mod                       # noqa: E402
from pipeline.step2_optimize import vrp as vrp_mod                       # noqa: E402
from pipeline.step4_metrics import imbalance as kpi_mod                  # noqa: E402
from project_config import (                # noqa: E402
    DEFAULT_PERIOD, DEFAULT_WARMUP_DAYS, TIME_BUDGET_MINUTES, VEHICLES_PER_ROUND,
    normalize_day_type, select_day_type,
)

METHODS = ("P", "B0", "B1", "B2", "B3")
LABELS = {
    "P": "P  제안 (군집+조정→ILP→VRP)",
    "B0": "B0 무재배치",
    "B1": "B1 그리디 (군집·ILP 없음)",
    "B2": "B2 평균 목표재고 (z=0)",
    "B3": "B3 지리 균등 군집 (조정 없음)",
}

# 대여소 정보 컬럼 순서 — select_top_unbalanced_st가 위치(iloc)로 고르므로
# st_info ({now}).csv와 같은 순서를 지켜야 한다.
ST_INFO_COLUMNS = ["station_id", "station_name", "lat", "lon", "parking_lot",
                   "stock", "rent_count", "return_count", "total_use_min", "total_use_km"]


def load_step1():
    """step1 군집 모듈을 불러온다 (파일명이 숫자로 시작해 일반 import가 안 된다)."""
    path = ROOT / "pipeline" / "step1_cluster" / "top_st_clustering.py"
    spec = importlib.util.spec_from_file_location("top_st_clustering", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def quiet(func, *args, **kwargs):
    """파이프라인 함수는 콘솔 출력이 많다 — 실험 표를 가리지 않게 삼킨다."""
    with contextlib.redirect_stdout(io.StringIO()):
        return func(*args, **kwargs)


# ---------------------------------------------------------------- 입력

def missing_run_message(run_label: str, available: list) -> str:
    """station_info가 비었을 때의 안내. **두 상황의 처방이 정반대다.**

    라벨을 지정했는데 없는 경우에 *"파이프라인을 한 번 돌리세요"* 라고 안내하면
    **틀린 길로 보낸다** — `station_info.stock`은 `tashu.py`가 실행 순간 라이브
    API에서 받은 재고라, 다시 돌리면 **오늘 재고**가 들어온다. 그날 값은 복원되지
    않고, 라벨만 하나 더 생겨 정본으로 잰 다른 결과와 비교가 깨진다.
    (DECISIONS.md 6-B가 실험의 정본 스냅샷을 특정 라벨로 못박고 있다.)
    """
    if not run_label:
        return "대여소 정보가 없습니다. 파이프라인을 한 번 돌려 station_info를 채우세요."
    return "\n".join([
        f"'{run_label}' 실행이 이 DB에 없습니다.",
        f"  이 DB에 있는 실행: {available if available else '(없음)'}",
        "  [주의] 파이프라인을 새로 돌려 채우지 마십시오 - station_info.stock은",
        "         실행 순간의 라이브 재고라 그날 값이 복원되지 않습니다.",
        "  라벨을 가진 PC에서 떼어 옮기십시오 (docs/구현/두_PC_작업.md 4-2장):",
        f'     python tools/transfer_run.py --export "{run_label}" --out run.db',
        "     python tools/transfer_run.py --import run.db",
    ])


def _report_snapshot(info, run_label, period, net) -> None:
    """**어느 스냅샷으로 쟀는지 찍는다.** 논문 숫자가 여기서 나온다 (1.26.130).

    🔴 `--run-label`을 비우면 `latest_label()`이 고르는데, 그것이 **계획이라는
    보장이 없다.** 실제로 `sweep-10`(차량 10대 파라미터 스윕)을 집고 있었다 —
    대여소 1,376곳·총재고 2,880으로, 계획 스냅샷(1,368곳·3,214)보다 **재고가
    10.4% 적다.** 무재배치(B0)의 결품이 2.10 대 2.40으로 갈리는 크기다.

    조용히 고르면 **자료가 쌓일수록 답이 달라진다.** 그래서 라벨과 그 종류,
    스냅샷의 크기를 함께 찍고, 실험으로 보이면 경고한다. 1.26.125·1.26.127이
    같은 결함을 `/orders`와 실험 13곳에서 잡았는데 **여기는 안 닿았다.**
    """
    label = run_label
    if not label:
        with db.session() as conn:
            label = db.latest_label(conn, "station_info", kinds=("plan",))
    kind = db.classify_run_label(label)
    stock = int(info["stock"].sum()) if "stock" in info else -1
    print(f"[스냅샷] 대여소 정보 = '{label}' ({kind}) · "
          f"{len(info)}곳 · 총재고 {stock:,}")
    # 이 시점의 net은 아직 컬럼 이름을 바꾸기 전이라 'date'다.
    day_col = "날짜" if "날짜" in net.columns else "date"
    days = net[day_col].nunique() if day_col in net.columns else -1
    print(f"[스냅샷] 순수요 = {period} · {days}일 (요일 구분 적용 전)")
    if kind != "plan":
        print(f"  ⚠️ '{label}'은 계획이 아니라 **{kind}**로 보입니다. "
              f"논문에 실을 값이라면 `--run-label`로 계획 실행을 못박으세요.")
    if not run_label:
        print("  ⚠️ `--run-label`이 비어 있어 **최신 계획 실행**을 골랐습니다 — "
              "계획을 다시 돌리면 같은 명령이 다른 답을 냅니다. "
              "논문에 실을 값이라면 라벨을 못박으세요.")


def load_inputs(period, run_label, day_type, warmup_days, warmup_period):
    """순수요·대여소 정보를 DB에서 읽는다."""
    with db.session() as conn:
        net = db.load_frame(conn, "net_demand", period=period)
        # 라벨을 안 주면 **계획 실행 중에서** 최신을 고른다 (1.26.132).
        # `kinds`를 비우면 `latest_label()`이 종류를 안 가려, 파라미터 스윕
        # (`sweep-10`)처럼 재고가 10.4% 적은 **실험 스냅샷**을 집는다.
        # 논문 6.3이 재현되지 않은 원인의 절반이 여기였다(EXPERIMENTS 30·31장).
        # ⚠️ `db.load_frame`과 `latest_label`은 처음부터 `kinds`를 받고 있었다 —
        #    **아무도 넘기지 않았을 뿐이다.** 여기 한 곳을 고치면 이 함수를
        #    쓰는 실험 19개가 함께 고쳐진다.
        info = db.load_frame(conn, "station_info",
                             **({"run_label": run_label} if run_label
                                else {"kinds": ("plan",)}))
        warmup = pd.DataFrame()
        if warmup_days > 0 and warmup_period and warmup_period != period:
            warmup = db.load_frame(conn, "net_demand", period=warmup_period)
        available = [r[0] for r in conn.execute(
            "SELECT DISTINCT run_label FROM station_info ORDER BY 1")]

    if net.empty:
        raise SystemExit(f"순수요가 없습니다 (기간 {period}). tools/load_rentals.py로 적재하세요.")
    if info.empty:
        raise SystemExit(missing_run_message(run_label, available))

    _report_snapshot(info, run_label, period, net)

    net = select_day_type(net.rename(columns={"date": "날짜"}), "날짜", day_type)
    if not warmup.empty:
        warmup = select_day_type(warmup.rename(columns={"date": "날짜"}), "날짜", day_type)

    missing = [c for c in ST_INFO_COLUMNS if c not in info.columns]
    if missing:
        raise SystemExit(f"station_info에 컬럼이 없습니다: {missing}")
    return net, info[ST_INFO_COLUMNS].copy(), warmup


def build_candidates(net, st_info, duration, z, warmup, warmup_days, step1):
    """목표재고 → 재배치량 → 작업 대상 선정. 전부 운영 코드를 그대로 부른다."""
    stats, _daily, _ratio = quiet(
        target_mod.build_stats, net, st_info[["station_id", "parking_lot", "stock"]],
        duration, warmup_net=(warmup if not warmup.empty else None),
        warmup_days=warmup_days, verbose=False)

    rebal = quiet(target_mod.compute_rebal_qty, stats, z=z)

    # select_top_unbalanced_st는 CSV 경로를 받는다 — 메모리 버퍼로 대신한다.
    buffer = io.StringIO()
    rebal.to_csv(buffer, index=False, encoding="utf-8")
    buffer.seek(0)
    return quiet(step1.select_top_unbalanced_st, buffer, duration, st_info)


# ---------------------------------------------------------------- 방법별 계획

def plan_with_clusters(candidates, step1, solver, adjust, seed):
    """군집 → ILP → VRP. adjust=False면 K-Medoids 결과를 그대로 쓴다(B3)."""
    clustered = quiet(step1.make_clustering, candidates.copy(), random_state=seed).copy()
    if adjust:
        clustered = quiet(step1.adjust_clustering, clustered).copy()

    frame = clustered.copy()
    frame["drop_qty"] = frame["rebal_qty"].clip(lower=0).astype(int)
    frame["pick_qty"] = (-frame["rebal_qty"].clip(upper=0)).astype(int)

    moves = []
    for cluster in frame["cluster"].unique():
        for row in quiet(ilp_mod.solve_cluster_moves,
                         frame[frame["cluster"] == cluster], solver):
            moves.append({"cluster": cluster, **row})
    moves = pd.DataFrame(moves)
    if moves.empty:
        return clustered, pd.DataFrame()

    coords = clustered.set_index("station_id")[["lat", "lon"]]
    rows = []
    for cluster in moves["cluster"].unique():
        part = moves[moves["cluster"] == cluster]
        nodes = {}
        for sid, qty in part.groupby("pick_station_id")["qty"].sum().items():
            nodes[(sid, "pick")] = {"qty": int(qty),
                                    "lat": coords.loc[sid, "lat"],
                                    "lon": coords.loc[sid, "lon"]}
        for sid, qty in part.groupby("drop_station_id")["qty"].sum().items():
            nodes[(sid, "drop")] = {"qty": int(qty),
                                    "lat": coords.loc[sid, "lat"],
                                    "lon": coords.loc[sid, "lon"]}
        rows.extend(quiet(vrp_mod.greedy_route, nodes, cluster))
    return clustered, pd.DataFrame(rows)


def plan_greedy(candidates):
    """B1 — 군집도 ILP도 없이, 차량이 depot에서 가장 가까운 작업지를 계속 고른다.

    같은 대수·같은 적재 용량·같은 경로 엔진(greedy_route)을 쓴다. 다른 것은
    **묶지 않고 짝짓지 않는다**는 것뿐이라, 차이가 곧 군집+ILP의 몫이다.
    한 대가 전체를 훑을 수는 없으므로 차량마다 시간 예산에서 멈춘다.
    """
    nodes = {}
    for row in candidates.itertuples():
        qty = int(abs(row.rebal_qty))
        if qty <= 0:
            continue
        kind = "drop" if row.rebal_qty > 0 else "pick"
        nodes[(row.station_id, kind)] = {"qty": qty, "lat": row.lat, "lon": row.lon}

    rows = []
    for vehicle in range(VEHICLES_PER_ROUND):
        rows.extend(quiet(vrp_mod.greedy_route, nodes, vehicle,
                          TIME_BUDGET_MINUTES * 60))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 평가

# VRP가 실제로 옮긴 양을 재고 증감으로 바꾸는 계산은 **step4의 함수를 그대로 쓴다**
# (1.18.4에서 파이프라인이 같은 기준을 쓰게 되면서 옮겨 갔다).
executed_delta = kpi_mod.executed_delta


def stockout(net, population, delta, duration):
    """결품 시간(대여소·일 평균)을 재배치 전후로 잰다. step4와 같은 함수를 쓴다.

    ⚠️ **`population`은 방법마다 달라지면 안 된다.** 여기서 나오는 값은 이 집합
    위의 *평균*이므로, 집합이 다르면 분모가 달라져 **서로 다른 자로 잰 값**이 된다.

    1.26.56 이전에는 각 방법의 **자기 후보 집합**을 그대로 넘겼다. B0·B1·B3·P는
    후보가 모두 같아 문제가 없었지만 **B2(z=0)만 후보가 다른 집합**이라(실측:
    5~47곳 대 83~99곳) 혼자 다른 모집단에서 평균을 냈다. 그래서 **재배치 전** 값부터
    2.63 대 2.00으로 어긋났고, 논문 6.3의 *"B2가 무재배치보다 나쁘다"* 는 서술이
    거기서 나왔다. 같은 모집단에서 다시 재면 B2는 **한 번도 무재배치보다 나쁘지
    않다**(2.00→1.84 등). 근거: docs/분석/EXPERIMENTS.md 14장.
    """
    pair = simulate_pair(net, population, delta, duration)
    if pair is None:
        return None, None
    return pair["stockout_before"], pair["stockout_after"]


def simulate_pair(net, population, delta, duration):
    """**결품과 포화를 한 번의 궤적에서 함께** 잰다 (1.26.130).

    🔴 **결품만 재면 반쪽이다.** 채우면 결품은 주는데 **반납이 막힌다** —
    [KPI.md](../../docs/분석/KPI.md) 3-B의 첫 측정에서 포화 시간은 결품의
    **1/4**인데 막힌 건수는 **2~3배**였다(가득 찬 곳에 반납이 몰린다).
    그런데 대조군 비교는 `_stockout_hours()`만 불러 **포화를 버리고 있었다.**
    논문 6장이 결품 하나로만 방법을 판정한 것이 여기서 나왔다.

    `_simulate_stock()`은 처음부터 둘을 함께 세고 있었다(1.26.101). 궤적을
    두 번 돌 필요가 없다 — **꺼내 쓰지 않았을 뿐이다.**

    ⚠️ **`population`은 방법마다 달라지면 안 된다.** `stockout()`의 주석에
    적힌 이유가 포화에도 그대로 걸린다 — 분모가 흔들리면 서로 다른 자로 잰
    값이 된다.

    반환: `stockout_before/after` · `saturation_before/after` (대여소·일 평균),
    모집단이 비면 `None`.
    """
    hours = kpi_mod.duration_hours(duration)
    stations = population[["station_id", "stock", "parking_lot"]].copy()
    stations["delta"] = stations["station_id"].map(delta).fillna(0)

    merged = net.merge(stations, on="station_id", how="inner")
    if merged.empty:
        return None

    before = kpi_mod._simulate_stock(merged, merged["stock"],
                                     merged["parking_lot"], hours)
    after = kpi_mod._simulate_stock(merged, merged["stock"] + merged["delta"],
                                    merged["parking_lot"], hours)
    days = merged["날짜"].nunique()
    count = merged["station_id"].nunique()
    denominator = max(count * days, 1)

    _warn_if_population_moved(duration, count, days)
    return {
        "stockout_before": float(before["stockout"].sum() / denominator),
        "stockout_after": float(after["stockout"].sum() / denominator),
        "saturation_before": float(before["saturated"].sum() / denominator),
        "saturation_after": float(after["saturated"].sum() / denominator),
    }


# 회차별로 **처음 본 분모**를 기억해 둔다. 같은 프로세스가 같은 회차를 다시 재는데
# 분모가 달라졌다면, 파라미터를 따라 자가 움직인 것이다.
_SEEN_POPULATION: dict = {}
_WARNED_POPULATION: set = set()


def reset_population_guard() -> None:
    """분모 감시를 초기화한다. **기간·스냅샷을 바꿔 다시 잴 때 부른다.**

    기간이 바뀌면 순수요가 달라져 분모도 정당하게 달라진다 — 그때까지 경고하면
    거짓 경보가 된다.
    """
    _SEEN_POPULATION.clear()
    _WARNED_POPULATION.clear()


def _warn_if_population_moved(duration, count, days) -> None:
    """분모가 회차 안에서 움직이면 **한 번** 경고한다 (1.26.73).

    🔴 **경고를 문서에만 적어 두면 다음 호출자가 또 밟는다.** 이 결함은 세 번
    나왔고(1.26.56 대조군 B2 · 1.26.64 상한 격자 · 1.26.65 `z` 격자), 세 번 다
    위 docstring이 이미 *"population은 방법마다 달라지면 안 된다"* 고 경고한
    **뒤에** 일어났다. 그래서 이번엔 코드가 스스로 알린다.

    ⚠️ **막지는 않는다** — 정당하게 달라지는 경우가 있다(기간을 바꿔 다시 재는
    경우, 중립 모집단과 후보 집합을 **의도적으로** 나란히 재는 경우). 판단은
    사람이 하고, 코드는 **그런 일이 일어났다는 사실**만 알린다.

    🔴 **한계 — 셀마다 자식 프로세스를 띄우는 격자는 잡지 못한다.** 상태가
    모듈 수준이라 프로세스가 갈리면 초기화된다(`limit_fleet_grid.py` ·
    `convention_sweep.py` · `limit_fixedpop_grid.py`가 그 방식이다 —
    `TOP_STATION_LIMIT` 같은 값이 import 시점에 읽히기 때문에 그래야 한다).
    **그 격자들은 부모가 분모를 모아 비교해야 한다** — `stockout_population()`이
    그 용도다. 이 감시는 **한 프로세스 안에서 여러 설정을 도는 실험**
    (`gamma_sweep` · `z_fixedpop_grid` · `cluster_time_term` 등)을 지킨다.
    """
    key = str(duration)
    seen = _SEEN_POPULATION.get(key)
    if seen is None:
        _SEEN_POPULATION[key] = (count, days)
        return
    if seen == (count, days) or key in _WARNED_POPULATION:
        return

    _WARNED_POPULATION.add(key)
    print(
        f"[!] stockout() 분모가 바뀌었습니다 — {duration}:"
        f" 대여소 {seen[0]}곳×{seen[1]}일 → {count}곳×{days}일.\n"
        f"    파라미터를 바꿀 때마다 **재는 자가 같이 바뀌면** 그 표는 서로 다른"
        f" 자로 잰 값입니다\n"
        f"    (docs/분석/EXPERIMENTS.md 17·18장). 모집단을 고정했는지 확인하십시오.\n"
        f"    의도한 것이라면(기간 변경 등) reset_population_guard()를 부르십시오.",
        file=sys.stderr)


def stockout_population(net, population, duration) -> int:
    """`stockout()`이 **분모로 쓰는 대여소 수**를 돌려준다 (1.26.72).

    **왜 필요한가 — 평균만 보면 분모가 흔들려도 티가 나지 않는다.**
    "파라미터가 후보 집합을 바꾸면 자기 후보에서 잰 결품은 비교가 안 된다"는
    결함이 **세 번** 나왔다(1.26.56 대조군 B2 · 1.26.64 상한 격자 ·
    1.26.65 `z` 격자). 세 번 다 **결과를 한참 쓰고 나서야** 발견했는데,
    `stockout()`이 평균 하나만 돌려주어 분모를 볼 방법이 없었기 때문이다.

    격자를 도는 실험은 파라미터마다 이 값을 함께 찍어라. **값이 파라미터를
    따라 움직이면 그 표는 서로 다른 자로 잰 것이다** — 재배치 *전* 결품이
    파라미터에 따라 달라지는 것과 같은 신호이고, 이쪽이 더 일찍 보인다.

        for limit in limits:
            pop = build_candidates(...)
            print(limit, stockout_population(net, pop, duration))   # 같아야 한다

    ⚠️ `stockout()`과 **같은 방식으로 세야** 뜻이 있다 — 그래서 여기서도
    `net`과 inner join한 뒤 센다. 후보에 있어도 순수요가 없는 대여소는
    분모에 들어가지 않기 때문이다.
    """
    stations = population[["station_id"]].drop_duplicates()
    merged = net.merge(stations, on="station_id", how="inner")
    return int(merged["station_id"].nunique())


def route_stats(routes):
    """이동거리·최장 소요시간·예산 초과 건수·처리 대수(pick 기준)."""
    if routes.empty:
        return {"bikes": 0, "km": 0.0, "max_min": 0.0, "over": 0, "vehicles": 0}
    minutes = routes.groupby("cluster")["cum_sec"].max() / 60
    picked = routes[routes["action"] == "pick"]["qty"].sum()
    return {
        "bikes": int(picked),
        "km": float(routes["distance_km"].sum()),
        "max_min": float(minutes.max()),
        "over": int((minutes > TIME_BUDGET_MINUTES).sum()),
        "vehicles": int(routes["cluster"].nunique()),
    }


# ---------------------------------------------------------------- 실행

def run_duration(net, st_info, warmup, duration, args, step1, solver):
    print("\n" + "=" * 92)
    print(f"< {duration} >  기간 {args.period} · {args.day_type} · 씨앗 {args.seed}")
    print("=" * 92)

    base = build_candidates(net, st_info, duration, None, warmup,
                            args.warmup_days, step1)
    if base.empty:
        print("[건너뜀] 재배치 대상이 없습니다 (Pick 또는 Drop 후보 없음)")
        return []

    print(f"작업 대상 {len(base)}곳 "
          f"(Pick {(base['rebal_qty'] < 0).sum()} / Drop {(base['rebal_qty'] > 0).sum()})")

    # B2만 후보 집합이 다르므로, **점수를 매길 모집단은 미리 합쳐 둔다.**
    # 방법마다 자기 후보 위에서 평균을 내면 분모가 달라져 비교가 성립하지 않는다
    # (stockout()의 주석 참고). B2를 안 돌리면 모집단은 base 그대로다.
    population = base
    if "B2" in args.methods:
        zero_all = build_candidates(net, st_info, duration, 0.0, warmup,
                                    args.warmup_days, step1)
        if not zero_all.empty:
            population = (pd.concat([base, zero_all], ignore_index=True)
                          .drop_duplicates(subset="station_id", keep="first"))
            if len(population) > len(base):
                print(f"  · 공통 모집단 {len(population)}곳"
                      f" (base {len(base)} ∪ z=0 후보 {len(zero_all)})")

    results = []
    for name in args.methods:
        if name == "B0":
            candidates, routes = base, pd.DataFrame()
        elif name == "B1":
            candidates, routes = base, plan_greedy(base)
        elif name == "B2":
            zero = build_candidates(net, st_info, duration, 0.0, warmup,
                                    args.warmup_days, step1)
            if zero.empty:
                print(f"[건너뜀] {name}: z=0에서는 재배치 대상이 없습니다")
                continue
            candidates, routes = plan_with_clusters(zero, step1, solver,
                                                    adjust=True, seed=args.seed)
        elif name == "B3":
            candidates, routes = plan_with_clusters(base.copy(), step1, solver,
                                                    adjust=False, seed=args.seed)
        else:
            candidates, routes = plan_with_clusters(base.copy(), step1, solver,
                                                    adjust=True, seed=args.seed)

        delta = executed_delta(routes)
        sim = simulate_pair(net, population, delta, duration)
        if sim is None:
            print(f"[건너뜀] {name}: 모집단과 겹치는 순수요가 없습니다")
            continue
        before, after = sim["stockout_before"], sim["stockout_after"]
        row = {"duration": duration, "method": name, "seed": args.seed,
               "period": args.period, "day_type": args.day_type,
               "stations": int(len(candidates)),
               **sim,
               **route_stats(routes)}

        if args.plan_basis:
            plan_delta = candidates.set_index("station_id")["rebal_qty"]
            _b, plan_after = stockout(net, population, plan_delta, duration)
            row["plan_after"] = plan_after

        results.append(row)
        print(f"  {LABELS[name]:<34} 처리 {row['bikes']:>4d}대"
              f"  결품 {before:.2f}h → {after:.2f}h"
              f"  포화 {sim['saturation_before']:.2f}h"
              f" → {sim['saturation_after']:.2f}h")

    return results


def show(frame, plan_basis):
    for duration, part in frame.groupby("duration", sort=False):
        base = part[part["method"] == "B0"]
        origin = float(base["stockout_after"].iloc[0]) if not base.empty else None
        # 포화의 기준선도 B0다. 결품만 기준을 두면 **맞바꿈이 안 보인다.**
        origin_sat = (float(base["saturation_after"].iloc[0])
                      if not base.empty and "saturation_after" in base else None)

        print("\n" + "-" * 108)
        print(f"[{duration}]  대조군 비교 — 결품 시간과 **포화 시간을 함께** (대여소·일 평균)")
        print("-" * 108)
        header = (f"{'방법':<34}{'처리대수':>8}{'이동km':>9}{'최장분':>8}"
                  f"{'초과':>5}{'결품h':>8}{'감소':>8}{'감소율':>8}"
                  f"{'포화h':>8}{'포화Δ':>8}")
        if plan_basis:
            header += f"{'계획기준':>9}"
        print(header)

        for row in part.itertuples():
            drop = origin - row.stockout_after if origin is not None else float("nan")
            rate = (drop / origin * 100) if origin else float("nan")
            sat = getattr(row, "saturation_after", float("nan"))
            sat_delta = (sat - origin_sat) if origin_sat is not None else float("nan")
            line = (f"{LABELS[row.method]:<34}{row.bikes:>8d}{row.km:>9.1f}"
                    f"{row.max_min:>8.1f}{row.over:>5d}"
                    f"{row.stockout_after:>8.2f}{drop:>8.2f}{rate:>7.1f}%"
                    f"{sat:>8.2f}{sat_delta:>+8.2f}")
            if plan_basis:
                plan = getattr(row, "plan_after", float("nan"))
                line += f"{plan:>9.2f}"
            print(line)

    print("\n" + "=" * 92)
    print("읽는 법")
    print("  · 결품h = 재배치 후 대여소·일 평균 결품 시간. **낮을수록 좋다.**")
    print("  · 감소 = B0(무재배치) 대비 줄어든 결품 시간. 이 값이 재배치의 실제 편익이다.")
    print("  · 포화h = 재고가 거치대 수와 같은 시간(**반납이 막힌다**). 포화Δ는 B0 대비 증감이고,")
    print("    **+면 결품을 줄인 대가로 반납을 막은 것이다.** 결품만 보면 '채우면 좋다'가 되므로")
    print("    반드시 함께 본다 — 첫 측정에서 포화 시간은 결품의 1/4인데 막힌 건수는 2~3배였다.")
    print("  · 초과 = 시간 예산을 넘긴 차량 수. P는 예산을 사후 점검만 하므로 초과가 날 수 있고,")
    print("    B1은 예산 안에서 멈추므로 초과가 0인 대신 일을 덜 한다 — 함께 봐야 한다.")
    if plan_basis:
        print("  · 계획기준 = rebal_qty가 전부 집행됐다고 가정한 결품 시간(step4의 계산 방식).")
        print("    '결품h'와의 차이가 곧 **계획과 집행의 격차**다.")
    print("  · 시간대(duration)가 다르면 수요 구조가 반대다 — 섞어서 평균 내지 마라.")
    print("=" * 92)


def main():
    parser = argparse.ArgumentParser(description="대조군 비교 실험")
    parser.add_argument("--period", default=DEFAULT_PERIOD, help='순수요 기간 (예: "25년 11월")')
    parser.add_argument("--duration", default="_05_10,_10_15,_15_20", help="시간대 (콤마 구분)")
    parser.add_argument("--day-type", default="weekday", choices=["weekday", "holiday"])
    parser.add_argument("--run-label", default="", help="대여소 정보를 가져올 실행 라벨 (기본: 최신)")
    parser.add_argument("--warmup-period", default="", help="계절 보정에 쓸 기간 (기본: 사용 안 함)")
    parser.add_argument("--warmup-days", type=int, default=DEFAULT_WARMUP_DAYS)
    parser.add_argument("--seed", type=int, default=42, help="K-Medoids 씨앗 (변동성 측정용)")
    parser.add_argument("--methods", default=",".join(METHODS))
    parser.add_argument("--plan-basis", action="store_true",
                        help="계획량(rebal_qty)이 전부 집행됐다고 본 결품 시간도 함께 낸다")
    parser.add_argument("--out", default="", help="결과를 CSV로 저장할 경로")
    args, _ = parser.parse_known_args()

    args.day_type = normalize_day_type(args.day_type)
    args.methods = [m.strip().upper() for m in args.methods.split(",") if m.strip()]
    unknown = [m for m in args.methods if m not in METHODS]
    if unknown:
        raise SystemExit(f"알 수 없는 방법: {unknown} (가능: {', '.join(METHODS)})")

    step1 = load_step1()
    solver = ilp_mod.build_solver()   # 파이프라인과 같은 솔버 설정
    net, st_info, warmup = load_inputs(args.period, args.run_label, args.day_type,
                                       args.warmup_days, args.warmup_period)

    rows = []
    for duration in [d.strip() for d in args.duration.split(",") if d.strip()]:
        rows.extend(run_duration(net, st_info, warmup, duration, args, step1, solver))

    if not rows:
        raise SystemExit("비교할 결과가 없습니다.")

    frame = pd.DataFrame(rows)
    show(frame, args.plan_basis)

    if args.out:
        frame.to_csv(args.out, index=False, encoding="utf-8")
        print(f"\n결과를 저장했습니다: {args.out}")


if __name__ == "__main__":
    main()
