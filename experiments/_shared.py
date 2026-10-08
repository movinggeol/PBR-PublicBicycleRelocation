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
DEFAULT_RUN_KINDS = ("plan",)


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


def default_run_label(conn, table: str = "station_info"):
    """라벨을 안 줬을 때 쓸 실행 — 그 표에 자료가 있는 **가장 최근 계획**. 표가 비었으면 None.

    **기본 실행을 고르는 규칙은 여기 하나다.** `db.latest_label()`은 `runs.created_at` 순으로
    고르고(사전순 최대가 아니다 — 1.26.125), 계획이 하나도 없으면 종류를 무시한 최신을 준다.

    ⚠️ '가장 최근 계획'은 요일도 회차 구성도 가리지 않는다(휴일 한 회차짜리 동시각 계획이 잡히기도
    한다 — 1.26.341). 원고 수치는 언제나 `--run-label "2026-08-11 real"`로 못박아 잰다. 기본값을
    정본으로 바꿀지는 따로 정할 일이고(docs/기록/TODO.md), 바꾼다면 고칠 곳이 여기다.
    """
    return db.latest_label(conn, table, kinds=DEFAULT_RUN_KINDS)


def resolve_run_label(label=None, *, hint: str = "--run-label", announce: bool = True) -> str:
    """재고 스냅샷(`station_info`) 라벨을 정한다 — **못 찾으면 멈춘다.**

    `db.load_frame()`은 라벨이 비면 `latest_label()`로 **말없이 최신**을 쓴다.
    그러면 같은 명령이 다른 날 다른 재고로 돌고 결과에 그 사실이 남지 않는다
    (1.26.58에서 `gamma_sweep.py`가, 그 전에 `z_sweep.py`가 이 결함으로 걸렸다).
    판정 기준은 "인자가 있는가"가 아니라 **"못 찾았을 때 멈추는가"** 다.

    라벨을 안 주면 `default_run_label()`이 고른다.

    `hint`는 오류 문구에서 라벨을 고르는 방법으로 안내할 이름이다 — argparse가 없는 스크립트에
    `--run-label`을 안내하면 통하지 않는다(그쪽은 `PBR_RUN_LABEL=…`). `announce`를 끄면 고른
    라벨을 찍지 않는다(스스로 찍는 스크립트용).
    """
    with db.session() as conn:
        available = [r[0] for r in conn.execute(
            "SELECT DISTINCT run_label FROM station_info ORDER BY 1")]
        latest_plan = default_run_label(conn)
    if not available:
        raise SystemExit("station_info가 비어 있습니다. 파이프라인을 한 번 돌리십시오.")
    chosen = label or latest_plan
    if chosen not in available:
        raise SystemExit(f"station_info에 '{chosen}' 실행이 없습니다."
                         f" {hint} 로 고르십시오: {available}")
    if announce:
        print(f"[스냅샷] station_info run_label = '{chosen}'"
              f"{' (기본: 가장 최근 계획)' if not label else ''}")
    return chosen
