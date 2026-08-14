"""평일/휴일 분리 검증 (docs/steps/step0_raw.md).

**휴일 = 주말 ∪ 공휴일**이다. 파이프라인은 첫 커밋부터 평일만 다뤘고, 그마저도
평일 자리에 걸린 공휴일(설·추석 연휴 등)이 섞여 있었다. 지켜야 할 것이 넷이다.

  1. `raw_to_net`이 **휴일을 버리지 않는다** — 예전 평일 필터가 되살아나면 안 된다.
  2. `calculate_target_qty`가 **한쪽만 골라** 계산한다 — 섞으면 부호가 반대인
     대여소끼리 상쇄돼 작업 대상에서 빠진다(실측 33~37%가 부호 역전).
  3. 두 구분의 결과가 **실제로 다르다** — 같으면 분리가 동작하지 않은 것이다.
  4. **공휴일이 평일에서 빠진다** — 평일 자리의 설·추석은 휴일로 세야 한다.

실데이터를 건드리지 않도록 고유 라벨을 쓰고 끝나면 그 라벨 파일만 지운다.
"""
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from project_config import (
    PP_ROOT, PROJECT_ROOT, holiday_mask, is_holiday, normalize_day_type,
    resolve_day_type, select_day_type,
)
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

@pytest.mark.parametrize("value", ["weekday", "holiday", "  WEEKDAY  "])
def test_normalize_day_type_accepts_valid(value):
    assert normalize_day_type(value) in ("weekday", "holiday")


@pytest.mark.parametrize("value", ["", "평일", "all", "weekend", "monday", None])
def test_normalize_day_type_rejects_invalid(value):
    """'all'을 막는 것이 핵심 — 섞는 선택지를 아예 만들지 않는다."""
    with pytest.raises(ValueError):
        normalize_day_type(value)


@pytest.mark.parametrize("date, expected", [
    ("2026-08-14", "weekday"),    # 금
    ("2026-08-15", "holiday"),    # 토 + 광복절
    ("2026-08-17", "holiday"),    # 월이지만 광복절 대체 휴일
    ("2025-10-06", "holiday"),    # 월이지만 추석
    ("2025-11-05", "weekday"),    # 수
])
def test_auto_resolves_from_calendar(date, expected):
    """auto는 계획 대상일을 달력으로 판정한다 — 평일 자리의 공휴일이 핵심."""
    assert normalize_day_type("auto", date) == expected
    assert resolve_day_type(date) == expected


def test_select_day_type_splits_by_holiday():
    frame = pd.DataFrame({
        "날짜": ["2025-11-07", "2025-11-08", "2025-11-09", "2025-11-10"],  # 금 토 일 월
        "value": [1, 2, 3, 4],
    })
    assert select_day_type(frame, "날짜", "weekday")["value"].tolist() == [1, 4]
    assert select_day_type(frame, "날짜", "holiday")["value"].tolist() == [2, 3]


def test_public_holiday_on_a_weekday_counts_as_holiday():
    """추석 연휴(2025-10-06~08)는 월·화·수인데 평일에서 빠져야 한다.

    이걸 놓치면 10월 평일 통계 18일 중 5일이 실제로는 휴일인 채로 섞인다.
    """
    frame = pd.DataFrame({"날짜": pd.date_range("2025-10-02", "2025-10-10")})
    weekday_dates = select_day_type(frame, "날짜", "weekday")["날짜"].dt.strftime("%m-%d")

    assert list(weekday_dates) == ["10-02", "10-10"],         "개천절·추석 연휴·한글날이 평일로 남아 있다"
    assert is_holiday("2025-10-06") and not is_holiday("2025-10-02")


def test_holiday_mask_matches_scalar_rule():
    """벡터 판정과 낱개 판정이 어긋나면 단계마다 다른 결과가 나온다."""
    dates = pd.date_range("2025-12-20", "2026-01-05")
    vector = holiday_mask(pd.Series(dates)).tolist()
    scalar = [is_holiday(d) for d in dates]

    assert vector == scalar


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


def test_net_demand_keeps_holidays(prepared):
    """raw_to_net이 휴일을 버리지 않는다(예전 평일 필터 회귀 방지)."""
    net = pd.read_csv(PP_ROOT / f"순수요/st_net_daily ({LABEL}).csv", encoding="utf-8")
    mask = holiday_mask(net["날짜"])

    assert mask.any(), "휴일이 사라졌다 — 평일 필터가 되살아났는지 확인하라"
    assert (~mask).any(), "평일도 있어야 한다"


def test_weekday_and_holiday_differ(prepared):
    """평일과 휴일의 재배치 계획이 실제로 다르다."""
    weekday = _rebal(prepared, "weekday")
    weekend = _rebal(prepared, "holiday")

    joined = weekday[["rebal_qty"]].join(
        weekend[["rebal_qty"]], lsuffix="_wd", rsuffix="_we", how="inner")
    assert not joined.empty

    assert not joined["rebal_qty_wd"].equals(joined["rebal_qty_we"]), \
        "평일과 휴일 결과가 같다 — day_type 필터가 걸리지 않았다"

    # 합성 데이터는 휴일에 흐름 방향을 뒤집어 두었다(tools/make_sample_data.py).
    # 그러니 부호가 반대인 대여소가 나와야 한다 — 섞으면 상쇄될 바로 그 대여소들이다.
    meaningful = joined[(joined["rebal_qty_wd"].abs() > 2)
                        | (joined["rebal_qty_we"].abs() > 2)]
    flipped = (meaningful["rebal_qty_wd"] * meaningful["rebal_qty_we"]) < 0
    assert flipped.any(), "부호가 뒤집히는 대여소가 하나도 없다 — 합성 데이터를 확인하라"


def test_day_type_is_recorded_in_runs(prepared):
    """어느 구분으로 돌린 실행인지 DB에 남는다(파일명에는 안 들어간다)."""
    import sqlite3

    _rebal(prepared, "holiday")
    with sqlite3.connect(prepared["PBR_DB_PATH"]) as conn:
        row = conn.execute(
            "SELECT day_type FROM runs WHERE run_label = ?", (LABEL,)).fetchone()

    assert row is not None and row[0] == "holiday"


# ---------------- 분위수 모델 (docs/DEMAND_DISTRIBUTION.md) ----------------
#
# 현재 모델은 베이스라인을 이기지 못해 **기본으로 켜지 않는다**.
# 그래서 여기서 지켜야 할 핵심은 "모델이 없어도, 깨져 있어도 파이프라인이 돈다"이다.

def test_model_absent_falls_back(tmp_path):
    """모델 파일이 없으면 None을 돌려준다 — 호출부가 기존 공식으로 간다."""
    import demand_model

    assert demand_model.load(tmp_path / "없는모델.pkl") is None


def test_corrupt_model_falls_back(tmp_path, capsys):
    """읽을 수 없는 모델이 파이프라인을 멈추지 않는다(경고만)."""
    import demand_model

    broken = tmp_path / "broken.pkl"
    broken.write_bytes(b"this is not a pickle")

    assert demand_model.load(broken) is None
    assert "경고" in capsys.readouterr().out


def test_stale_feature_set_falls_back(tmp_path, capsys):
    """피처 구성이 바뀐 옛 모델은 쓰지 않는다 — 조용히 틀린 예측을 하면 안 된다."""
    import pickle

    import demand_model

    stale = tmp_path / "stale.pkl"
    with stale.open("wb") as handle:
        pickle.dump({"model": None, "quantile": 0.95,
                     "features": ["옛피처"], "durations": (), "day_types": ()}, handle)

    assert demand_model.load(stale) is None
    assert "다시 학습" in capsys.readouterr().out


def test_training_frame_uses_only_previous_month():
    """피처는 **직전 달 정보만** 써야 한다 — 미래를 보면 백테스트가 거짓말을 한다."""
    import demand_model

    # 이어지는 달이 없으면 학습 표가 비어야 한다(쌍을 못 만든다).
    assert demand_model.training_frame({"25년 11월": pd.DataFrame()}).empty

    assert "station_id" not in demand_model.FEATURES, \
        "대여소 ID를 외우면 표본 8~19일에서 과적합한다"
    assert all(f.startswith("prev_") or f in ("month", "duration_idx", "day_type_idx")
               for f in demand_model.FEATURES), \
        "예측 시점에 알 수 없는 피처가 섞였다"
