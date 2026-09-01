"""상한 하나로 **계획만** 세워 실행 결과(delta)를 내놓는다 — `limit_fixedpop_grid.py`의 자식.

`TOP_STATION_LIMIT`은 `project_config` 모듈 수준에서 환경변수를 읽으므로 한
프로세스 안에서 값을 바꿀 수 없다. 그래서 상한마다 이 스크립트를 새로 띄운다.

**평가는 여기서 하지 않는다.** 결품을 재려면 모집단이 필요한데, 상한마다 후보
집합이 달라 여기서 재면 **분모가 흔들린다**(그것이 1.26.64에서 고친 결함이다).
계획(delta)만 JSON으로 넘기고, 부모가 **고정 모집단** 위에서 함께 잰다.
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

LIMIT = int(os.environ["PBR_TOP_STATION_LIMIT"])
PERIOD = os.environ["PBR_EXP_PERIOD"]
RUN_LABEL = os.environ["PBR_EXP_RUN_LABEL"]
DURATIONS = os.environ["PBR_EXP_DURATIONS"].split(",")
SEEDS = [int(s) for s in os.environ["PBR_EXP_SEEDS"].split(",")]

buf = io.StringIO()
with contextlib.redirect_stdout(buf):          # 파이프라인 수다를 삼킨다
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
            out.append({"duration": duration, "seed": seed, "limit": LIMIT,
                        "stations": int(len(base)),
                        "km": stats.get("km"), "over": stats.get("over"),
                        "vehicles": stats.get("vehicles"), "bikes": stats.get("bikes"),
                        "delta_json": json.dumps(
                            {str(k): int(v) for k, v in delta.items()})})
print(json.dumps(out))
