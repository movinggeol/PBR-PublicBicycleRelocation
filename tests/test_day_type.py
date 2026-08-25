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
    Path("step0_collect") / "extract_parking_lot.py",
    Path("step0_collect") / "api_to_info.py",
    Path("step0_collect") / "raw_to_net.py",
]
TARGET_QTY = Path("step0_collect") / "calculate_target_qty.py"


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
    # 허용: 직전 달 통계(prev_*), 달력에서 오는 것(month/duration/day_type),
    #      계획 대상 달의 **첫 N일**로 구하는 계절 배율(warmup_ratio).
    #      셋 다 계획을 세우는 시점에 손에 있는 정보다.
    #      그리고 계획 대상 날짜의 **날씨**(rain/rainy/temp/wind). 이것만 성격이 다르다 —
    #      운영에서는 관측이 아니라 **예보**로 채워야 손에 있는 정보가 된다.
    #      예보가 없으면 NaN으로 남고 모델이 알아서 처리한다(docs/WEATHER.md 5장).
    allowed = ("month", "duration_idx", "day_type_idx", "warmup_ratio",
               *demand_model.WEATHER_FEATURES)
    assert all(f.startswith("prev_") or f in allowed
               for f in demand_model.FEATURES), \
        "예측 시점에 알 수 없는 피처가 섞였다"


# ---------------- 계절 수준 보정 (warmup) ----------------

def test_warmup_ratio_scales_when_demand_jumps():
    """계획 대상 달의 수요가 1.5배면 배율도 그만큼 나와야 한다."""
    import demand_model

    stats = pd.DataFrame({"station_id": ["A", "B", "C"], "mu": [10.0, -6.0, 4.0]})
    recent = pd.DataFrame({
        "station_id": ["A", "B", "C"] * 3,
        "date": pd.to_datetime(["2026-03-02"] * 3 + ["2026-03-03"] * 3
                               + ["2026-03-04"] * 3),
        "demand": [15.0, -9.0, 6.0] * 3,          # 정확히 1.5배
    })

    ratio = demand_model.warmup_ratio(stats, recent, days=14)
    assert ratio == pytest.approx(1.5, abs=0.01)


def test_warmup_only_uses_the_first_n_days():
    """뒤쪽 날짜를 보면 안 된다 — 계획 시점에 없는 자료다."""
    import demand_model

    stats = pd.DataFrame({"station_id": ["A"], "mu": [10.0]})
    recent = pd.DataFrame({
        "station_id": ["A", "A"],
        "date": pd.to_datetime(["2026-03-01", "2026-03-20"]),
        "demand": [20.0, 100.0],                  # 20일차는 창 밖
    })

    assert demand_model.warmup_ratio(stats, recent, days=7) == pytest.approx(2.0)


def test_warmup_ratio_is_clipped():
    """며칠치 잡음으로 배율이 튀는 것을 막는다."""
    import demand_model

    # mu는 WARMUP_MIN_DEMAND(2)를 넘어야 배율 계산에 들어간다.
    stats = pd.DataFrame({"station_id": ["A"], "mu": [3.0]})
    recent = pd.DataFrame({"station_id": ["A"], "date": pd.to_datetime(["2026-03-01"]),
                           "demand": [300.0]})

    assert demand_model.warmup_ratio(stats, recent, days=14) == demand_model.WARMUP_CLIP[1]


@pytest.mark.parametrize("days, recent", [
    (0, "정상"),          # 꺼져 있으면 None
    (14, "빈값"),         # 자료가 없으면 None
])
def test_warmup_returns_none_when_unusable(days, recent):
    """배율을 낼 수 없으면 None — 호출부가 보정을 건너뛴다."""
    import demand_model

    stats = pd.DataFrame({"station_id": ["A"], "mu": [1.0]})
    frame = (pd.DataFrame(columns=["station_id", "date", "demand"]) if recent == "빈값"
             else pd.DataFrame({"station_id": ["A"], "date": pd.to_datetime(["2026-03-01"]),
                                "demand": [1.0]}))

    assert demand_model.warmup_ratio(stats, frame, days) is None


def test_apply_warmup_moves_center_not_just_spread():
    """mu와 sigma **둘 다** 곱해야 한다 — sigma만 키우면 z를 올린 것과 같다."""
    import demand_model

    stats = pd.DataFrame({"mu": [10.0], "sigma": [2.0]})
    scaled = demand_model.apply_warmup(stats, 1.5)

    assert scaled["mu"].iloc[0] == pytest.approx(15.0)
    assert scaled["sigma"].iloc[0] == pytest.approx(3.0)
    # 원본은 그대로여야 한다(호출부가 되돌릴 수 있게)
    assert stats["mu"].iloc[0] == 10.0


def test_train_and_serve_use_the_same_features():
    """학습과 예측이 같은 함수를 거쳐야 한다 — 어긋나면 조용히 틀린 값이 나온다."""
    import demand_model

    daily = pd.DataFrame({
        "station_id": ["A"] * 6 + ["B"] * 6,
        "date": list(pd.date_range("2026-02-02", periods=6)) * 2,
        "demand": [5, 6, 4, 7, 5, 6, -3, -2, -4, -3, -2, -3],
    })
    recent = pd.DataFrame({
        "station_id": ["A", "B"],
        "date": pd.to_datetime(["2026-03-02", "2026-03-02"]),
        "demand": [10.0, -6.0],                   # 대략 2배
    })

    ratio = demand_model.season_ratio(daily, recent, warmup_days=14)
    assert ratio is not None and ratio > 1.0

    features = demand_model.build_features(daily, "_05_10", "weekday", 3, ratio)

    # 날씨는 **날짜별**이라 대여소별 통계를 접는 build_features에 들어갈 수 없다.
    # 그래서 두 함수가 짝을 이룬다 — 학습도 예측도 반드시 둘 다 거쳐야 FEATURES가
    # 채워진다. 한쪽만 거치면 예측에서 KeyError가 난다.
    station_features = set(demand_model.FEATURES) - set(demand_model.WEATHER_FEATURES)
    assert station_features <= set(features.columns), \
        "build_features가 대여소 피처를 전부 만들지 않으면 예측 때 KeyError가 난다"

    with_weather = demand_model.add_weather(features, "_05_10", date="2026-03-02")
    assert set(demand_model.FEATURES) <= set(with_weather.columns), \
        "build_features + add_weather가 FEATURES를 전부 채워야 한다"
    # 날씨는 도시 하나의 값이다 — 그 날짜의 모든 대여소가 같은 값을 받는다.
    for column in demand_model.WEATHER_FEATURES:
        assert with_weather[column].nunique(dropna=False) == 1
    assert features["warmup_ratio"].iloc[0] == pytest.approx(ratio)

    # 배율은 '수준' 피처에만 곱해야 한다
    plain = demand_model.build_features(daily, "_05_10", "weekday", 3, None)
    assert features["prev_mu"].iloc[0] == pytest.approx(plain["prev_mu"].iloc[0] * ratio)
    assert features["prev_days"].iloc[0] == plain["prev_days"].iloc[0]
    assert features["prev_zero_ratio"].iloc[0] == plain["prev_zero_ratio"].iloc[0]


def test_warmup_ratio_excludes_near_zero_stations():
    """수요가 0 근처인 대여소는 배율 계산에서 빠져야 한다.

    |평균|의 합으로 비율을 내면 참값이 0에 가까운 대여소가 분자만 부풀린다
    (E|X̂| > |E X|). 실제로 이걸 놓쳐 배율이 과대추정되고, 그 위에서 고른
    분위수 보정이 통째로 무너진 적이 있다 (버전관리 1.15.3).
    """
    import demand_model

    # 신호가 있는 대여소 하나 + 0 근처 잡음 대여소 여럿
    stats = pd.DataFrame({
        "station_id": ["신호"] + [f"잡음{i}" for i in range(20)],
        "mu": [10.0] + [0.1] * 20,
    })
    recent = pd.DataFrame({
        "station_id": ["신호"] + [f"잡음{i}" for i in range(20)],
        "date": pd.to_datetime(["2026-03-02"] * 21),
        # 신호는 그대로, 잡음은 부호가 섞인 관측값(참값은 0에 가깝다)
        "demand": [10.0] + [3.0 if i % 2 else -3.0 for i in range(20)],
    })

    걸러냄 = demand_model.warmup_ratio(stats, recent, days=14)
    전체 = demand_model.warmup_ratio(stats, recent, days=14, min_demand=0)

    assert 걸러냄 == pytest.approx(1.0, abs=0.01), "신호 대여소만 보면 배율은 1이다"
    assert 전체 > 1.5, "0 근처 대여소를 넣으면 배율이 부풀어야 한다(이 검증의 전제)"
    assert 걸러냄 < 전체


def test_backtest_and_pipeline_share_one_ratio_entry_point():
    """측정과 운영이 같은 함수를 써야 한다 — 갈라지면 측정이 거짓말을 한다."""
    import inspect

    import demand_model
    import tools.backtest_demand as backtest

    assert "season_ratio" in inspect.getsource(backtest.evaluate), \
        "백테스트가 season_ratio를 우회하면 파이프라인과 다른 배율을 쓰게 된다"
    assert demand_model.WARMUP_MIN_DEMAND > 0


def test_run_pipeline_forwards_warmup_options():
    """계절 보정 옵션이 run_pipeline에서 하위 단계까지 내려가야 한다.

    문서에는 `--warmup-period` / `--warmup-days`가 있는데 run_pipeline이 인자를
    선언하지 않아 '알 수 없는 인자'로 거절하던 적이 있다(1.17.3에서 수정).
    """
    import run_pipeline

    argv = sys.argv
    try:
        sys.argv = ["run_pipeline.py", "--warmup-period", "26년 03월", "--warmup-days", "0"]
        args = run_pipeline.parse_args()
    finally:
        sys.argv = argv

    command = run_pipeline.build_command(Path("step0/calculate_target_qty.py"), args)

    assert "--warmup-period" in command and "26년 03월" in command
    # 0은 '보정을 끈다'는 뜻이라 값으로 참·거짓을 판정하면 조용히 사라진다.
    assert command[command.index("--warmup-days") + 1] == "0"


def _load_step4():
    """step4 모듈을 경로로 직접 읽는다(모듈 전역 `config`를 갈아끼우므로 독립 이름)."""
    import importlib.util

    path = PROJECT_ROOT / "step4_metrics" / "imbalance.py"
    spec = importlib.util.spec_from_file_location("_imbalance_daytype", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("day_type", ["weekday", "holiday"])
def test_stockout_evaluation_uses_the_same_day_type(monkeypatch, day_type):
    """5번 규칙: **평가도 계획과 같은 요일 구분만 써야 한다.**

    계획(calculate_target_qty)은 한쪽만 골라 목표 재고를 잡는데, step4의
    `load_net_demand()`가 기간 전체를 읽으면 **평일 계획을 주말 수요로 채점**하게
    된다. 대여소의 33~37%가 두 구분에서 부호가 반대라 결과가 실제와 달라진다.
    """
    import dataclasses

    step4 = _load_step4()

    dates = [f"2026-08-{day:02d}" for day in range(17, 24)]   # 월~일 한 주
    frame = pd.DataFrame({
        "station_id": ["ST0001"] * len(dates),
        "date": dates,
        **{f"net_{hour:02d}": [1] * len(dates) for hour in range(24)},
    })

    # 기대값은 달력에서 직접 구한다 — 공휴일·대체공휴일이 끼어도 테스트가 흔들리지 않는다.
    휴일여부 = holiday_mask(pd.to_datetime(frame["date"]))
    기대건수 = int((휴일여부 == (day_type == "holiday")).sum())
    assert 0 < 기대건수 < len(dates), "입력에 평일과 휴일이 모두 있어야 의미 있는 검증이다"

    monkeypatch.setattr(step4.db, "load_frame", lambda *a, **k: frame.copy())
    monkeypatch.setattr(step4, "config",
                        dataclasses.replace(step4.config, day_type=day_type))

    loaded = step4.load_net_demand()

    assert not loaded.empty, "해당 구분의 날짜는 남아야 한다"
    남은날짜 = holiday_mask(pd.to_datetime(loaded["날짜"]))
    assert 남은날짜.nunique() == 1, "평일과 휴일이 섞였다 — 계획과 평가의 기준이 어긋난다"
    assert bool(남은날짜.iloc[0]) == (day_type == "holiday")
    assert len(loaded) == 기대건수
