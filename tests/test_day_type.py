"""평일/주말 분리 검증 (docs/steps/step0_raw.md).

파이프라인은 첫 커밋부터 평일만 다뤘다. 주말을 넣으면서 지켜야 할 것이 셋이다.

  1. `raw_to_net`이 **주말을 버리지 않는다** — 예전 필터가 되살아나면 안 된다.
  2. `calculate_target_qty`가 **한쪽만 골라** 계산한다 — 섞으면 부호가 반대인
     대여소끼리 상쇄돼 작업 대상에서 빠진다(실측 33~37%가 부호 역전).
  3. 두 요일 구분의 결과가 **실제로 다르다** — 같으면 분리가 동작하지 않은 것이다.

실데이터를 건드리지 않도록 고유 라벨을 쓰고 끝나면 그 라벨 파일만 지운다.
"""
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from project_config import PP_ROOT, PROJECT_ROOT, normalize_day_type, select_day_type
from tools.make_sample_data import generate

DURATION = "_05_10"
LABEL = f"daytype-{os.getpid()}"

PREP = [
    Path("step0 (raw데이터 처리)") / "extract_parking_lot.py",
    Path("step0 (raw데이터 처리)") / "api_to_info.py",
    Path("step0 (raw데이터 처리)") / "raw_to_net.py",
]
TARGET_QTY = Path("step0 (raw데이터 처리)") / "calculate_target_qty.py"


# ---------------- 설정 헬퍼 ----------------

@pytest.mark.parametrize("value", ["weekday", "weekend", "  WEEKDAY  "])
def test_normalize_day_type_accepts_valid(value):
    assert normalize_day_type(value) in ("weekday", "weekend")


@pytest.mark.parametrize("value", ["", "평일", "all", "monday", None])
def test_normalize_day_type_rejects_invalid(value):
    """'all'을 막는 것이 핵심 — 섞는 선택지를 아예 만들지 않는다."""
    with pytest.raises(ValueError):
        normalize_day_type(value)


def test_select_day_type_splits_by_weekend():
    frame = pd.DataFrame({
        "날짜": ["2025-11-07", "2025-11-08", "2025-11-09", "2025-11-10"],  # 금 토 일 월
        "value": [1, 2, 3, 4],
    })
    assert select_day_type(frame, "날짜", "weekday")["value"].tolist() == [1, 4]
    assert select_day_type(frame, "날짜", "weekend")["value"].tolist() == [2, 3]


# ---------------- 실제 단계 실행 ----------------

@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    """합성 데이터로 순수요까지 만들어 둔다(요일 구분과 무관한 단계들)."""
    raw_path = tmp_path_factory.mktemp("raw") / "합성_대여이력.csv"
    generate(now=LABEL, period=LABEL, stations=70, days=28,
             rentals_per_day=400, raw_path=raw_path)

    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               PBR_DB_PATH=str(tmp_path_factory.mktemp("db") / "daytype.db"))

    for script in PREP:
        done = subprocess.run(
            [sys.executable, str(script), "--now", LABEL, "--period", LABEL,
             "--duration", DURATION, "--raw-file", str(raw_path)],
            cwd=PROJECT_ROOT, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        if done.returncode != 0:
            pytest.fail(f"{script} 실패\n{done.stdout[-1500:]}\n{done.stderr[-1500:]}")

    yield env

    for path in PP_ROOT.rglob(f"*{LABEL}*"):
        if path.is_file():
            path.unlink()


def _rebal(env, day_type: str) -> pd.DataFrame:
    """주어진 요일 구분으로 재배치량을 계산하고 결과를 읽는다."""
    done = subprocess.run(
        [sys.executable, str(TARGET_QTY), "--now", LABEL, "--period", LABEL,
         "--duration", DURATION, "--day-type", day_type],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    if done.returncode != 0:
        pytest.fail(f"calculate_target_qty({day_type}) 실패\n{done.stderr[-1500:]}")
    path = PP_ROOT / f"재배치 정보/rebal_qty{DURATION} ({LABEL}).csv"
    return pd.read_csv(path, encoding="utf-8").set_index("station_id")


def test_net_demand_keeps_weekends(prepared):
    """raw_to_net이 주말을 버리지 않는다(예전 평일 필터 회귀 방지)."""
    net = pd.read_csv(PP_ROOT / f"순수요/st_net_daily ({LABEL}).csv", encoding="utf-8")
    weekday = pd.to_datetime(net["날짜"]).dt.dayofweek

    assert (weekday >= 5).any(), "주말이 사라졌다 — 평일 필터가 되살아났는지 확인하라"
    assert (weekday < 5).any(), "평일도 있어야 한다"


def test_weekday_and_weekend_differ(prepared):
    """두 요일 구분의 재배치 계획이 실제로 다르다."""
    weekday = _rebal(prepared, "weekday")
    weekend = _rebal(prepared, "weekend")

    joined = weekday[["rebal_qty"]].join(
        weekend[["rebal_qty"]], lsuffix="_wd", rsuffix="_we", how="inner")
    assert not joined.empty

    assert not joined["rebal_qty_wd"].equals(joined["rebal_qty_we"]), \
        "평일과 주말 결과가 같다 — day_type 필터가 걸리지 않았다"

    # 합성 데이터는 주말에 흐름 방향을 뒤집어 두었다(tools/make_sample_data.py).
    # 그러니 부호가 반대인 대여소가 나와야 한다 — 섞으면 상쇄될 바로 그 대여소들이다.
    meaningful = joined[(joined["rebal_qty_wd"].abs() > 2)
                        | (joined["rebal_qty_we"].abs() > 2)]
    flipped = (meaningful["rebal_qty_wd"] * meaningful["rebal_qty_we"]) < 0
    assert flipped.any(), "부호가 뒤집히는 대여소가 하나도 없다 — 합성 데이터를 확인하라"


def test_day_type_is_recorded_in_runs(prepared):
    """어느 요일 구분으로 돌린 실행인지 DB에 남는다(파일명에는 안 들어간다)."""
    import sqlite3

    _rebal(prepared, "weekend")
    with sqlite3.connect(prepared["PBR_DB_PATH"]) as conn:
        row = conn.execute(
            "SELECT day_type FROM runs WHERE run_label = ?", (LABEL,)).fetchone()

    assert row is not None and row[0] == "weekend"
