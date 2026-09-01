"""관행값 하나를 박고 계획을 세워 delta를 내놓는다 — `convention_sweep.py`의 자식.

관행값은 `project_config` 모듈 수준에서 환경변수를 읽으므로 한 프로세스 안에서
바꿀 수 없다. 그래서 값마다 이 스크립트를 새로 띄운다.

**평가는 여기서 하지 않는다** — `REBAL_MIN_QTY`는 후보 집합을 바꾸므로, 자기
후보 집합에서 결품을 재면 분모가 흔들린다(EXPERIMENTS 17·18장). 계획만 넘기고
부모가 **중립 모집단(전체 대여소)** 위에서 함께 잰다.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PERIOD = os.environ["PBR_EXP_PERIOD"]
RUN_LABEL = os.environ["PBR_EXP_RUN_LABEL"]
DURATIONS = os.environ["PBR_EXP_DURATIONS"].split(",")
SEEDS = [int(s) for s in os.environ["PBR_EXP_SEEDS"].split(",")]
KNOB = os.environ["PBR_EXP_KNOB"]
VALUE = os.environ["PBR_EXP_VALUE"]

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    spec = importlib.util.spec_from_file_location(
        "baseline_compare", ROOT / "experiments" / "baseline" / "baseline_compare.py")
    bc = importlib.util.module_from_spec(spec)
    sys.modules["baseline_compare"] = bc
    spec.loader.exec_module(bc)

    step1 = bc.load_step1()
    solver = bc.ilp_mod.build_solver()
    net, st_info, warmup = bc.load_inputs(PERIOD, RUN_LABEL, "weekday", 14, "")

    out = []
    for duration in DURATIONS:
        base = bc.build_candidates(net, st_info, duration, None, warmup, 14, step1)
        if base.empty:
            continue
        for seed in SEEDS:
            _c, routes = bc.plan_with_clusters(base.copy(), step1, solver, True, seed)
            delta = bc.executed_delta(routes)
            stats = bc.route_stats(routes)
            out.append({"duration": duration, "seed": seed,
                        "knob": KNOB, "value": VALUE,
                        "candidates": int(len(base)),
                        "km": stats.get("km"), "over": stats.get("over"),
                        "vehicles": stats.get("vehicles"), "bikes": stats.get("bikes"),
                        "max_min": stats.get("max_min"),
                        "delta_json": json.dumps(
                            {str(k): int(v) for k, v in delta.items()})})
print(json.dumps(out))
