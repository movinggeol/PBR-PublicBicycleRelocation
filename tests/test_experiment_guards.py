"""실험 하네스가 **조용히 틀린 답을 내지 않는지** 검증한다.

실험 스크립트는 파이프라인이 아니라 테스트가 잘 닿지 않는 자리인데, 여기서 나온
숫자가 그대로 논문에 실린다. 실제로 두 번 물렸다.

  · `z_sweep.py`의 `--run-label` 기본값이 DB에 없는 라벨이라 **오류 없이 작업량이
    전부 0인 비용 표**가 나왔다 (1.26.39).
  · 라벨이 없을 때 *"파이프라인을 한 번 돌리세요"* 라고 안내했는데, 이 상황에서는
    **틀린 처방**이다 — 다시 돌리면 오늘 재고가 들어와 정본 스냅샷이 복원되지
    않는다 (1.26.54).

지키려는 규칙: **없는 것은 없다고 말하고, 처방은 상황에 맞아야 한다.**
"""
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_baseline():
    for path in (PROJECT_ROOT, PROJECT_ROOT / "experiments" / "baseline"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    spec = importlib.util.spec_from_file_location(
        "baseline_compare", PROJECT_ROOT / "experiments" / "baseline" / "baseline_compare.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bc():
    return load_baseline()


def test_missing_label_does_not_advise_rerunning_the_pipeline(bc):
    """정본 스냅샷은 다시 돌려서 복원되지 않는다 — 그 길로 보내면 안 된다."""
    message = bc.missing_run_message("2026-08-11 real", ["2026-08-27 23"])
    assert "2026-08-11 real" in message
    assert "transfer_run.py" in message, "가져올 방법을 알려 줘야 한다"
    assert "2026-08-27 23" in message, "이 DB에 무엇이 있는지 보여 줘야 한다"
    assert "파이프라인을 한 번 돌려" not in message, \
        "라벨이 지정된 경우 파이프라인 재실행은 틀린 처방이다"


def test_empty_db_still_advises_running_the_pipeline(bc):
    """라벨을 지정하지 않았는데 비었으면 그때는 파이프라인이 맞는 처방이다."""
    message = bc.missing_run_message("", [])
    assert "파이프라인" in message
    assert "transfer_run" not in message


def test_load_inputs_names_the_missing_label(bc, monkeypatch, tmp_path):
    """조용히 빈 결과를 내지 말고 멈춰야 한다 (z_sweep에서 실제로 겪은 결함)."""
    import db

    net = pd.DataFrame({
        "station_id": ["ST0001"] * 2, "date": ["2025-11-03", "2025-11-04"],
        **{f"net_{h:02d}": [1, 2] for h in range(24)},
    })
    db.save_output("net_demand", net, period="25년 11월")
    db.save_output("station_info", pd.DataFrame({
        "station_id": ["ST0001"], "station_name": ["가"], "lat": [36.3],
        "lon": [127.3], "parking_lot": [10], "stock": [5],
    }), run_label="있는라벨", period="25년 11월", duration="_05_10")

    with pytest.raises(SystemExit) as err:
        bc.load_inputs("25년 11월", "없는라벨", "weekday", 0, "")
    text = str(err.value)
    assert "없는라벨" in text
    assert "있는라벨" in text, "쓸 수 있는 라벨을 알려 줘야 다음 수를 둔다"


def test_load_inputs_accepts_an_existing_label(bc):
    """정상 경로가 막히면 안 된다."""
    import db

    net = pd.DataFrame({
        "station_id": ["ST0001"] * 2, "date": ["2025-11-03", "2025-11-04"],
        **{f"net_{h:02d}": [1, 2] for h in range(24)},
    })
    db.save_output("net_demand", net, period="25년 11월")
    db.save_output("station_info", pd.DataFrame({
        "station_id": ["ST0001"], "station_name": ["가"], "lat": [36.3],
        "lon": [127.3], "parking_lot": [10], "stock": [5],
    }), run_label="있는라벨", period="25년 11월", duration="_05_10")

    _, info, _ = bc.load_inputs("25년 11월", "있는라벨", "weekday", 0, "")
    assert list(info["station_id"]) == ["ST0001"]


# ---------------------------------------------------------------- 공통 모집단

def _net(station_ids, days=2):
    """결품 계산에 필요한 최소 순수요 표.

    `_stockout_hours`는 `net_05`~`net_09` 같은 시간별 컬럼을 본다. A만 수요가
    커서 결품이 나고 나머지는 나지 않도록 만든다 — 그래야 모집단을 넓혔을 때
    평균이 내려가는 것을 확인할 수 있다.
    """
    rows = []
    for day in range(days):
        for sid in station_ids:
            row = {"station_id": sid, "날짜": f"2025-11-0{day + 1}"}
            for hour in range(5, 10):
                row[f"net_{hour:02d}"] = 5 if sid == "A" else -1
            rows.append(row)
    return pd.DataFrame(rows)


def _stations(station_ids, stock=3, parking_lot=20):
    return pd.DataFrame({"station_id": list(station_ids),
                         "stock": stock, "parking_lot": parking_lot})


def test_stockout_denominator_follows_the_population_not_the_plan(bc):
    """같은 계획이라도 **모집단이 다르면 다른 값**이 나온다는 것을 고정한다.

    이것이 1.26.56에서 고친 결함의 본질이다. `stockout()`은 주어진 집합 위의
    *평균*이므로, 방법마다 자기 후보를 넘기면 분모가 방법마다 달라진다.
    B2(z=0)만 후보가 다른 집합이었고, 그래서 논문 6.3의 B2 행은 다른 자로
    잰 값이었다 — **재배치 전 값부터** 2.63 대 2.00으로 어긋났다.
    """
    net = _net(["A", "B", "C", "D"])
    좁은_집합 = _stations(["A"])
    넓은_집합 = _stations(["A", "B", "C", "D"])

    좁게 = bc.stockout(net, 좁은_집합, {}, "_05_10")
    넓게 = bc.stockout(net, 넓은_집합, {}, "_05_10")

    assert 좁게[0] is not None and 넓게[0] is not None
    assert 좁게 != 넓게, (
        "모집단이 달라도 같은 값이 나온다면 이 테스트가 지키려는 성질이 사라진 것이다")


def test_all_methods_are_scored_on_one_population(bc):
    """run_duration이 방법마다 다른 모집단으로 점수를 매기지 않는지 본다.

    `stockout()` 호출부가 `candidates`(방법마다 다름)가 아니라 `population`
    (회차마다 하나)을 넘겨야 한다. 되돌리면 B2가 다시 혼자 다른 자로 잰다.
    """
    import inspect

    source = inspect.getsource(bc.run_duration)
    assert "stockout(net, population," in source, (
        "결품 점수는 공통 모집단 위에서 매겨야 한다")
    assert "stockout(net, candidates," not in source, (
        "방법의 자기 후보 집합으로 점수를 매기면 분모가 방법마다 달라진다")


def test_stockout_population_exposes_the_denominator(bc):
    """분모를 **볼 수 있어야** 한다 — 평균만 보면 흔들려도 티가 안 난다.

    같은 결함이 세 번 나왔다(1.26.56 B2 · 1.26.64 상한 · 1.26.65 z). 세 번 다
    늦게 찾았는데, `stockout()`이 평균 하나만 돌려주어 **분모를 볼 방법이
    없었기** 때문이다. 격자 실험이 파라미터마다 이 값을 찍으면 그 자리에서
    알아챈다.
    """
    net = _net(["A", "B", "C", "D"])

    assert bc.stockout_population(net, _stations(["A"]), "_05_10") == 1
    assert bc.stockout_population(net, _stations(["A", "B", "C", "D"]), "_05_10") == 4

    # 순수요에 없는 대여소는 분모에 들어가지 않는다 — stockout()과 같은 셈법이다.
    assert bc.stockout_population(net, _stations(["A", "없는곳"]), "_05_10") == 1


def test_stockout_population_moves_with_the_population_like_the_average_does(bc):
    """분모가 달라지면 `stockout()` 값도 달라진다 — 둘이 같은 것을 가리킨다.

    이 테스트가 지키는 것은 *"분모 지표가 평균과 따로 놀지 않는다"* 는 성질이다.
    따로 놀면 분모를 찍어 봐도 결함을 못 잡는다.
    """
    net = _net(["A", "B", "C", "D"])
    좁은_집합 = _stations(["A"])
    넓은_집합 = _stations(["A", "B", "C", "D"])

    좁은_분모 = bc.stockout_population(net, 좁은_집합, "_05_10")
    넓은_분모 = bc.stockout_population(net, 넓은_집합, "_05_10")
    assert 좁은_분모 != 넓은_분모

    assert bc.stockout(net, 좁은_집합, {}, "_05_10") != bc.stockout(
        net, 넓은_집합, {}, "_05_10"), (
        "분모는 달라졌는데 결품 평균이 같다면, 분모를 찍어 봐야 결함을 못 잡는다")


# ---------------- 분모(모집단) 감시 — 1.26.73 ----------------
#
# 결함이 세 번 나왔다(1.26.56 대조군 B2 · 1.26.64 상한 격자 · 1.26.65 z 격자).
# 세 번 다 stockout()의 docstring이 이미 경고한 **뒤에** 일어났다 —
# 문서로만 막으면 다음 호출자가 또 밟는다. 그래서 코드가 스스로 알린다.

def _tiny_inputs():
    """대여소 3곳 · 하루치. 결품 값 자체는 보지 않고 **분모만** 본다."""
    net = pd.DataFrame({
        "station_id": ["A", "B", "C"],
        "날짜": ["2026-01-01"] * 3,
        "net_demand": [0, 0, 0],
    })
    pop = pd.DataFrame({
        "station_id": ["A", "B", "C"],
        "stock": [5, 5, 5],
        "parking_lot": [10, 10, 10],
    })
    return net, pop


def test_분모가_바뀌면_경고한다(bc, capsys):
    """같은 회차를 다른 모집단으로 재면 알린다 — 17·18장의 결함이다."""
    net, pop = _tiny_inputs()
    bc.reset_population_guard()

    bc.stockout(net, pop, {}, "_05_10")               # 3곳
    capsys.readouterr()
    bc.stockout(net, pop.head(2), {}, "_05_10")       # 2곳 — 자가 바뀌었다

    err = capsys.readouterr().err
    assert "분모가 바뀌었습니다" in err
    assert "_05_10" in err


def test_같은_모집단이면_조용하다(bc, capsys):
    """정상 사용까지 시끄러우면 경고를 무시하게 된다."""
    net, pop = _tiny_inputs()
    bc.reset_population_guard()

    for _ in range(3):
        bc.stockout(net, pop, {}, "_05_10")

    assert "분모가 바뀌었습니다" not in capsys.readouterr().err


def test_경고는_회차마다_한_번만(bc, capsys):
    """격자를 도는 동안 같은 경고가 수십 번 쏟아지면 아무도 안 읽는다."""
    net, pop = _tiny_inputs()
    bc.reset_population_guard()

    bc.stockout(net, pop, {}, "_05_10")
    capsys.readouterr()
    for n in (2, 1, 2):
        bc.stockout(net, pop.head(n), {}, "_05_10")

    assert capsys.readouterr().err.count("분모가 바뀌었습니다") == 1


def test_회차가_다르면_서로_간섭하지_않는다(bc, capsys):
    """회차마다 후보 집합이 다른 것은 정상이다 — 그걸로 경고하면 거짓 경보다."""
    net, pop = _tiny_inputs()
    bc.reset_population_guard()

    bc.stockout(net, pop, {}, "_05_10")
    bc.stockout(net, pop.head(2), {}, "_10_15")

    assert "분모가 바뀌었습니다" not in capsys.readouterr().err


def test_감시를_초기화하면_다시_조용해진다(bc, capsys):
    """기간·스냅샷을 바꿔 다시 잴 때는 분모가 정당하게 달라진다."""
    net, pop = _tiny_inputs()
    bc.reset_population_guard()

    bc.stockout(net, pop, {}, "_05_10")
    bc.reset_population_guard()
    capsys.readouterr()
    bc.stockout(net, pop.head(2), {}, "_05_10")

    assert "분모가 바뀌었습니다" not in capsys.readouterr().err


# ------------------------------------------- 실측 결품: 어느 날을 셌다고 말하는가

def load_observed():
    """`experiments/structure/observed_stockout.py`를 싣는다."""
    for path in (PROJECT_ROOT, PROJECT_ROOT / "experiments" / "structure"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    spec = importlib.util.spec_from_file_location(
        "observed_stockout",
        PROJECT_ROOT / "experiments" / "structure" / "observed_stockout.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def obs():
    return load_observed()


def _mixed_window_frame(obs):
    """창이 섞인 자료. 넓은 날 하나와 좁지만 **자기 창 안에서는 촘촘한** 날들.

    실제로 겪은 모양이다 — 8/31만 07~22시고 나머지는 09~17시대였다.
    """
    rows = []
    for day, (start, end) in {
        "2026-08-25": (9, 17), "2026-08-26": (9, 17),
        "2026-08-31": (7, 22),
    }.items():
        for hour in range(start, end):
            for minute in range(0, 60, 10):
                rows.append({"station_id": "ST0001",
                             "관측": pd.Timestamp(f"{day} {hour:02d}:{minute:02d}"),
                             "날짜": day, "시각": hour, "stock": 0, "parking_lot": 10})
    return pd.DataFrame(rows)


def test_하루_전체_판정은_자료를_더하면_움직인다(obs):
    """**이것이 결함이다** — 좁은 창 날은 자기 창 안에서 결측 0인데도, 나중에
    넓은 날이 들어오면 소급해서 탈락한다. 잣대가 자료에 따라 움직인다.
    """
    frame = _mixed_window_frame(obs)
    narrow = frame[frame["날짜"] != "2026-08-31"]

    assert len(obs.complete_days(narrow)) == 2      # 좁은 날끼리는 둘 다 온전
    assert obs.complete_days(frame) == ["2026-08-31"]   # 넓은 날이 들어오자 탈락


def test_회차_판정은_넓은_날이_들어와도_흔들리지_않는다(obs):
    """회차별 판정은 그 시간대만 보므로 **소급해서 뒤집히지 않는다.**
    분석이 이쪽을 쓰는 이유이고, 머리기사도 이쪽이어야 한다.
    """
    frame = _mixed_window_frame(obs)
    hours = [10, 11, 12, 13, 14]
    narrow = frame[frame["날짜"] != "2026-08-31"]

    assert len(obs.duration_complete_days(narrow, hours)) == 2
    assert len(obs.duration_complete_days(frame, hours)) == 3


def test_머리기사는_회차별로_말하고_창이_모자라면_밝힌다(obs):
    """머리기사가 `complete_days()` 하나로 말하면 **근거를 낮춰 말한다** —
    실측에서 `_10_15`가 6일인데 "온전한 날 1일"이라고 적고 있었다.

    창이 회차를 통째로 덮지 못하면 날 수가 있어도 복원과 맞대지 못하므로
    그것도 함께 밝혀야 한다 — 안 그러면 쓸 수 있는 줄 안다.
    """
    frame = _mixed_window_frame(obs)
    window = frame["시각"].unique()

    usable = obs.usable_by_duration(frame, ["_10_15", "_05_10"], window)

    assert len(usable["_10_15"][0]) == 3
    assert usable["_10_15"][1] is False           # 10~14시는 창이 다 덮는다
    assert usable["_05_10"][1] is True            # 5~8시는 07시부터라 모자란다

    summary = obs.format_usable(usable)
    assert "_10_15 3일" in summary
    assert "(창 일부)" in summary and "_10_15 3일(창 일부)" not in summary


# ----------------------------------------- 어느 요일 구분으로 채점했는가

def load_imbalance():
    """step4의 `imbalance`를 싣는다 (실험들이 채점에 쓰는 그 모듈)."""
    for path in (PROJECT_ROOT, PROJECT_ROOT / "step4_metrics"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    spec = importlib.util.spec_from_file_location(
        "imbalance", PROJECT_ROOT / "step4_metrics" / "imbalance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_실행의_요일_구분을_읽어_맞춘다(capsys):
    """**오늘 달력으로 채점하면 안 된다.**

    실험 스크립트는 `get_runtime_config()`의 기본값(`auto` → 오늘)을 쓴다.
    그래서 **일요일에 돌린 문턱 스윕이 평일 계획을 휴일 순수요로** 재고
    있었다(1.26.127 실측: 결품 1.810 → 평일로 고치니 1.858). 같은 스크립트가
    월요일에는 다른 답을 냈다는 뜻이다. 정답은 `runs.day_type`에 있다.
    """
    import dataclasses

    import db

    kpi = load_imbalance()
    kpi.config = dataclasses.replace(kpi.config, day_type="holiday")
    with db.session() as conn:
        conn.execute("INSERT INTO runs (run_label, day_type, created_at)"
                     " VALUES (?, ?, ?)", ("계획-평일", "weekday", "2026-08-27 23:13:43"))

    got = kpi.use_run_day_type("계획-평일")

    assert got == "weekday"
    assert kpi.config.day_type == "weekday"
    assert "평일" in capsys.readouterr().out, "무엇으로 맞췄는지 말해야 한다"


def test_요일_기록이_없으면_조용히_넘어가지_않는다(capsys):
    """모르면 모른다고 말한다 — 조용히 오늘 달력을 쓰면 처음 그 실패와 같다."""
    import dataclasses

    kpi = load_imbalance()
    kpi.config = dataclasses.replace(kpi.config, day_type="holiday")

    got = kpi.use_run_day_type("기록에-없는-라벨")

    assert got == "holiday", "모르면 지금 설정을 그대로 쓴다"
    out = capsys.readouterr().out
    assert "기록에 없습니다" in out and "휴일" in out


def test_관측시간을_함께_내야_두_평균의_차이가_설명된다(obs):
    """본표는 반쪽짜리 날을 섞고 비교표는 안 섞는다. 같은 대여소·같은 회차인데
    값이 다른 이유가 **분모**이므로, 분모를 낼 수 있어야 한다.

    실측에서 `_10_15` 245곳이 1.02(7일) 대 1.15(6일)였다.
    """
    frame = _mixed_window_frame(obs)
    hours = [10, 11, 12, 13, 14]
    half = frame[(frame["날짜"] != "2026-08-25") | (frame["시각"] < 12)]

    full = obs.observed_hours(frame, hours).groupby("station_id")[
        ["결품시간", "관측시간"]].mean()
    mixed = obs.observed_hours(half, hours).groupby("station_id")[
        ["결품시간", "관측시간"]].mean()

    assert full.loc["ST0001", "관측시간"] == pytest.approx(5.0)
    # 반쪽 날이 섞이면 관측시간이 줄고, 결품시간도 그만큼 낮게 잡힌다
    assert mixed.loc["ST0001", "관측시간"] < full.loc["ST0001", "관측시간"]
    assert mixed.loc["ST0001", "결품시간"] < full.loc["ST0001", "결품시간"]
