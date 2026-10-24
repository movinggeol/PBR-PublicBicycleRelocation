"""실험 스크립트가 함께 쓰는 것 — **복사해 들고 있던 코드를 한 곳으로 모았다** (2026-10-08).

## 왜 모았나

실험은 서로 import하지 않는 독립 스크립트라, 필요한 함수를 옆 파일에서 **복사해** 썼다.
그러다 한 벌을 고친 것이 나머지에 닿지 않았다.

  · `gamma_sweep` · `z_sweep`은 1.26.281에 기본 실행을 *"사전순 최대 라벨"* 에서 *"가장 최근
    계획"* 으로 고쳤는데, 같은 코드의 복사본 `resolve_run_label()` 셋(`cluster_count_sweep` ·
    `wanted_vehicles_geo_sweep` · `tour_length_estimate`)은 그대로였다. 실험 라벨(`sweep-10`)은
    `'s' > '2'`라 어떤 날짜 라벨도 이기고, 그 스냅샷은 재고가 10.4% 적다 — 1.26.341에서 다시 고쳤다.
  · `baseline_compare.py`를 경로로 싣는 `load_baseline()`은 함수 다섯 벌 · 인라인 다섯 벌이었다.

**여기 있는 것이 정본이다.** 실험 폴더 안에 같은 일을 하는 함수를 다시 만들면
`tests/test_experiment_guards.py`가 막는다.

## 부르는 법

분류 폴더(`params/` · `baseline/` …)는 패키지가 아니다. 스크립트가 저장소 루트를 `sys.path`에
넣은 뒤(`sys.path.insert(0, str(ROOT))` — 어느 실험이나 하는 일이다) 이렇게 부른다.

    from experiments._shared import load_baseline, resolve_run_label

`experiments/`에도 `__init__.py`가 없다 — 이름공간 패키지로 읽힌다(루트가 `sys.path`에 있으면 된다).

## 여기 두지 않는 것

거리(`haversine_km`) · 재고 틱(`STOCK_TICK_MINUTES` · `TICKS_PER_HOUR`) · 회차와 요일 목록
(`DURATIONS` · `DAY_TYPES` · `DAY_TYPE_LABELS`) · 회차 시각(`duration_hours` ·
`duration_start_hour`) · 요일 맞추기(`align_day_type`)는 파이프라인 · 웹과 함께 쓰는 값이라
`project_config`에 있다. 여기는 **실험끼리만** 나누는 것을 둔다.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import project_config  # noqa: E402,F401  — 콘솔 인코딩을 먼저 맞춘다(— · 이모지)
import db  # noqa: E402

BASELINE_PATH = ROOT / "experiments" / "baseline" / "baseline_compare.py"

# 실험이 라벨 없이 돌 때 고르는 실행의 종류. `kinds`를 비우면 `db.latest_label()`이 종류를 안 가려
# 파라미터 스윕(`sweep-10`) 같은 **실험 스냅샷**을 집는다(1.26.132).
# 재고 스냅샷(`station_info`)은 정본이 있으면 이 규칙까지 오지 않는다 — 아래 `default_run_label()`이
# 정본을 먼저 본다. 저장된 계획 표(`vrp_plan` …)도 원고의 계획 실행(raw19)이 있으면 오지 않는다(2026-10-24).
DEFAULT_RUN_KINDS = ("plan",)

# 원고 수치를 잰 **정본 재고 스냅샷**(대여소 1,368곳 · 총재고 3,214대). 라벨 없는 실험의 기본값이다
# (사용자 결정 2026-10-08, 구현 2026-10-14 — docs/기록/TODO.md 색인 ⑥).
# 예전 기본값 '가장 최근 계획'은 계획을 돌릴 때마다 바뀌어, 10-14에는 '2026-10-11 20 휴일 동시각'
# (_20_05 한 회차 · 총재고 2,384대 — 정본과 26% 차이)을 집고 있었다. 부록 A의 라벨 없는 명령이
# 원고와 다른 재고로 돌던 까닭이다. 스크립트마다 `--run-label` 기본값으로 따로 적던 같은 문자열
# 일곱 곳도 이것을 쓴다. `dockless_hub.CANON_LABEL`은 그대로 뒀다 — 재고가 아니라 저장된 계획
# (`rebalance_plan`)을 읽는 라벨이라 아래 범위 밖이다.
CANONICAL_RUN_LABEL = "2026-08-11 real"

# 정본을 기본으로 고르는 표 — **재고 스냅샷만이다.** 저장된 계획 표(ilp_plan · vrp_plan · pick_drop ·
# rebalance_plan …)는 원고가 `2026-08-11 real raw19`(종류 experiment)로 읽는다. 같은 정본 라벨로
# 묶으면 그 표에서는 **옛 코드가 푼 계획**을 읽게 되므로, 그 표의 기본은 아래 `CANONICAL_PLAN_LABEL`이다.
CANONICAL_DEFAULT_TABLES = ("station_info",)

# 저장된 계획 표를 읽는 실험의 기본 실행 (사용자 결정 2026-10-24, docs/기록/TODO.md 색인).
# 정본 스냅샷을 복사해 그때 코드와 원본 19개월 대여이력으로 다시 세운 실험 실행이다(2026-09-29,
# EXPERIMENTS 42장 · docs/기록/재측정_계획.md). 원고 7.5 · 8.2 · 8.6이 이 계획을 읽는다. 예전 기본값
# '가장 최근 계획'은 계획을 돌릴 때마다 바뀌어 10-24에는 10-18 23시 **휴일** 계획을 집었고, 10-20부터는
# K로 고른 수거(`pick_drop.pick_rule`)가 섞일 수 있다. 세 회차(`_05_10` · `_10_15` · `_15_20`)뿐이다.
# ⚠️ 고정된 과거 계획이라 그 뒤의 코드 변경(K 등)은 담기지 않는다. 지금 운영 계획을 재려면 라벨을 준다.
CANONICAL_PLAN_LABEL = "2026-08-11 real raw19"
CANONICAL_PLAN_TABLES = ("rebalance_plan", "pick_drop", "ilp_plan", "vrp_plan")


def load_baseline():
    """`baseline_compare.py`를 모듈로 실어 돌려준다 — 대조군 실험의 함수를 그대로 쓰기 위해서다.

    분류 폴더가 패키지가 아니라 경로로 직접 읽는다. `sys.modules["baseline_compare"]`에 올려 두므로
    뒤이어 맨 이름으로 부르는 쪽(`import baseline_compare`)도 **같은 인스턴스**를 받는다.

    부를 때마다 새로 싣는다(그 자리의 등록을 갈아 끼운다) — 통합 전 복사본들이 하던 그대로다.
    스크립트는 한 프로세스에서 한 번만 부르므로 인스턴스는 하나다. 파이프라인 모듈이 import되며
    찍는 출력을 삼키려면 부르는 쪽에서 `contextlib.redirect_stdout()`으로 감싼다.
    """
    spec = importlib.util.spec_from_file_location("baseline_compare", BASELINE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["baseline_compare"] = module
    spec.loader.exec_module(module)
    return module


def default_run_label(conn, table: str = "station_info", *, warn: bool = True):
    """라벨을 안 줬을 때 쓸 실행. 표가 비었으면 None.

    **기본 실행을 고르는 규칙은 여기 하나다.**

      1. 재고 스냅샷(`CANONICAL_DEFAULT_TABLES`)에 정본(`CANONICAL_RUN_LABEL`)이 있으면 **정본**.
         저장된 계획 표(`CANONICAL_PLAN_TABLES`)에 원고의 계획 실행(`CANONICAL_PLAN_LABEL`)이 있으면 **그 실행**.
      2. 없으면 그 표에 자료가 있는 **가장 최근 계획**(`db.latest_label(…, kinds=("plan",))` —
         `runs.created_at` 순, 사전순 최대가 아니다 — 1.26.125). 계획이 하나도 없으면 종류를 무시한
         최신이다. 재고 스냅샷 · 계획 표에서 이리로 넘어오면 `warn`이 켜진 동안 **크게 경고한다.**

    왜 2를 남겼나 — 정본이 없는 DB(새 클론 · `make_sample_data`의 합성 데모 · 테스트 DB)에서 멈추면
    데모와 시험이 통째로 못 돈다. 대신 원고와 다른 재고라는 것을 감추지 않는다.

    부르는 쪽은 `label == CANONICAL_RUN_LABEL`로 어느 쪽으로 골랐는지 안다(정본이 없을 때만 2로
    넘어오므로 둘은 겹치지 않는다). 안내 문구는 `default_run_note()`가 만든다. 한 실행에서 두 번
    부르는 곳(대조군의 `load_inputs()` → `_report_snapshot()`)은 두 번째를 `warn=False`로 부른다.

    ⚠️ 2의 '가장 최근 계획'은 요일도 회차 구성도 가리지 않는다(휴일 한 회차짜리 동시각 계획이 잡히기도
    한다 — 1.26.341). 계획 표가 정본 라벨이 아니라 raw19를 고르는 까닭은 `CANONICAL_DEFAULT_TABLES` 주석.
    """
    for tables, canon in ((CANONICAL_DEFAULT_TABLES, CANONICAL_RUN_LABEL),
                          (CANONICAL_PLAN_TABLES, CANONICAL_PLAN_LABEL)):
        if table in tables and conn.execute(
                f"SELECT 1 FROM {table} WHERE run_label = ? LIMIT 1", (canon,)).fetchone():
            return canon
    label = db.latest_label(conn, table, kinds=DEFAULT_RUN_KINDS)
    if warn and label is not None and table in CANONICAL_PLAN_TABLES:
        print(f"⚠️⚠️ 원고의 계획 실행 '{CANONICAL_PLAN_LABEL}'이 이 DB의 {table}에 없습니다. "
              f"가장 최근 계획 '{label}'로 돕니다.\n"
              f"   원고 수치와 **다른 계획**입니다. 원고를 재현하려면 그 실행을 가진 PC에서 옮기십시오"
              f" (tools/transfer_run.py).")
    if warn and label is not None and table in CANONICAL_DEFAULT_TABLES:
        print(f"⚠️⚠️ 정본 스냅샷 '{CANONICAL_RUN_LABEL}'이 이 DB의 {table}에 없습니다 — "
              f"가장 최근 계획 '{label}'로 돕니다.\n"
              f"   원고 수치와 **다른 재고**입니다. 원고를 재현하려면 정본을 가진 PC에서 옮기십시오"
              f" (tools/transfer_run.py — docs/구현/두_PC_작업.md 4-2장).")
    return label


def default_run_note(label) -> str:
    """라벨을 안 줬을 때 고른 실행을 [스냅샷] 줄에 덧붙일 말 — 정본인지 아닌지를 가른다."""
    if label == CANONICAL_RUN_LABEL:
        return "(기본: 정본)"
    if label == CANONICAL_PLAN_LABEL:
        return "(기본: 원고의 계획 실행)"
    return "(기본: 정본 없음 → 가장 최근 계획 — 원고 수치와 다르다)"


def plan_label_before(conn, until=None, *, period: str | None = None) -> str:
    """자료 끝 전에 만든 가장 최근 **계획** 실행(대여소 정보가 있는 것). 없으면 멈춘다.

    정본 기본값(`default_run_label()`)과 다른 규칙이다. 사전 등록이 *최신 계획 실행의 station_info* 로
    정해 둔 실험이 쓴다: 41장 F2(2026-09-28) · 43장 F3(09-29, 포화 모형 ML 11번 포함) · 53장 계열
    (`worklist_validity.snapshot()`). 1.26.371(10-14)이 라벨 없는 기본값을 정본으로 바꾸며 F2 · F3
    하네스가 등록과 다른 스냅샷을 읽게 됐고, 채택 판정 전(10-20, 결과를 보기 전)에 이 규칙으로 되돌렸다.

    '최신'을 실행하는 시각이 아니라 **자료 끝**에 묶는다. 그래야 판정 뒤에 계획을 더 돌려도 같은 명령이
    같은 실행을 고른다(10-18 밤에 계획 하나가 돌아 '최신 계획'이 바뀐 일이 있었다).

    until  : 이 시각 전에 만든 실행만 본다. 비우면 재고 시계열의 마지막 관측 + 한 틱이다.
    period : 순수요 기간으로도 거른다(53장 계열은 `DEFAULT_PERIOD`). 비우면 거르지 않는다. F2 · F3의
             착수 점검은 `db.latest_label(kinds=("plan",))`로 골라 기간을 보지 않았다.
    """
    import pandas as pd

    if until is None:
        last = conn.execute("SELECT MAX(observed_at) FROM stock_history").fetchone()[0]
        if last is None:
            raise SystemExit("재고 시계열이 비어 자료 끝을 정할 수 없습니다(--run-label로 실행을 지정하십시오)")
        until = pd.Timestamp(last) + pd.Timedelta(minutes=project_config.STOCK_TICK_MINUTES)
    sql = "SELECT run_label FROM runs WHERE kind = 'plan' AND created_at < ?"
    params = [str(pd.Timestamp(until))]
    if period:
        sql += " AND period = ?"
        params.append(period)
    for (label,) in conn.execute(sql + " ORDER BY created_at DESC", params):
        if conn.execute("SELECT 1 FROM station_info WHERE run_label = ? LIMIT 1", (label,)).fetchone():
            return label
    raise SystemExit(f"{pd.Timestamp(until)} 전에 만든 계획 실행의 station_info가 없습니다")


def resolve_run_label(label=None, *, hint: str = "--run-label", announce: bool = True) -> str:
    """재고 스냅샷(`station_info`) 라벨을 정한다 — **못 찾으면 멈춘다.**

    `db.load_frame()`은 라벨이 비면 `latest_label()`로 **말없이 최신**을 쓴다.
    그러면 같은 명령이 다른 날 다른 재고로 돌고 결과에 그 사실이 남지 않는다
    (1.26.58에서 `gamma_sweep.py`가, 그 전에 `z_sweep.py`가 이 결함으로 걸렸다).
    판정 기준은 "인자가 있는가"가 아니라 **"못 찾았을 때 멈추는가"** 다.

    라벨을 안 주면 `default_run_label()`이 고른다(정본, 없으면 가장 최근 계획 + 경고). 라벨을 주면
    기본 실행을 아예 고르지 않는다 — 고르면 쓰지도 않을 실행 때문에 정본 없음 경고가 뜬다.

    `hint`는 오류 문구에서 라벨을 고르는 방법으로 안내할 이름이다 — argparse가 없는 스크립트에
    `--run-label`을 안내하면 통하지 않는다(그쪽은 `PBR_RUN_LABEL=…`). `announce`를 끄면 고른
    라벨을 찍지 않는다(스스로 찍는 스크립트용). 정본 없음 경고는 `announce`와 상관없이 찍힌다.
    """
    with db.session() as conn:
        available = [r[0] for r in conn.execute(
            "SELECT DISTINCT run_label FROM station_info ORDER BY 1")]
        if not available:
            raise SystemExit("station_info가 비어 있습니다. 파이프라인을 한 번 돌리십시오.")
        chosen = label or default_run_label(conn)
    if chosen not in available:
        raise SystemExit(f"station_info에 '{chosen}' 실행이 없습니다."
                         f" {hint} 로 고르십시오: {available}")
    if announce:
        print(f"[스냅샷] station_info run_label = '{chosen}'"
              f"{' ' + default_run_note(chosen) if not label else ''}")
    return chosen
