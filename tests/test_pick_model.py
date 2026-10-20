"""수거 대수 학습(pipeline/step1_cluster/pick_model.py, EXPERIMENTS 56장 K)이 지키는 것.

1. **56장 실험 코드와 같은 값을 낸다.** 운영 쪽은 실험 코드를 옮겨 적은 것이다. 같은 합성 격자로 실험 함수
   (pick_qty_ml.build_rows · add_hybrid · table_values, pick_redistribute.take_until, pick_mix.wide)와 맞대,
   행 · 혼합 입력 · 예측 · 고르기가 같은지 본다. 옮기며 한 곳이라도 갈리면 56장의 검증이 이 코드의 것이 아니게 된다.
2. **켜고 끄는 조건.** auto는 운영 계획(실행 종류 plan)에서만 쓰고, 실험 · 재현은 지금 방식 그대로다. 휴일 ·
   재고 시계열 없음 · 오래된 관측 · 학습 평일 부족이면 지금 선정을 그대로 돌려준다.
3. **끝까지 돈다.** 합성 DB에서 학습 · 저장 · 다시 읽기 · 예측 · 고르기가 이어지고, 배송 쪽은 바뀌지 않는다.
4. **step1이 실험 경로에 K를 끌어들이지 않는다.** pick_model은 step1의 `__main__` 블록 안에서만 불러온다.
"""
import ast
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.step1_cluster import pick_model as pm  # noqa: E402

DURS = ("_05_10", "_10_15", "_15_20", "_20_05")


def _load(relpath: str, name: str):
    path = ROOT / relpath
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def exp():
    """56장 실험 코드(원본). pick_mix가 pick_qty_ml · pick_redistribute를 이름으로 불러오므로 그 폴더를 경로에 둔다."""
    sys.path.insert(0, str(ROOT / "experiments" / "structure"))
    mix = _load("experiments/structure/pick_mix.py", "_pm_원본_pick_mix")
    return {"pq": mix.pq, "pr": mix.pr, "mix": mix}


def _grid(seed=0, stations=8):
    """일요일 0시 ~ 다음 토요일 12시의 10분 격자. 무작위 걸음 + 관측 빈칸 + 트럭 흔적(10분 사이 6대) 몇 군데."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-10-11 00:00", "2026-10-17 12:00", freq="10min")
    ids = [f"ST{i:04d}" for i in range(stations)]
    walk = 15 + rng.integers(-2, 3, size=(len(idx), stations)).cumsum(axis=0)
    grid = pd.DataFrame(np.clip(walk, 8, 25).astype(float), index=idx, columns=ids)
    for r, c in ((150, 1), (400, 3), (700, 5)):           # 10분 사이 6대 이상 빠진다(걸음은 많아야 +2)
        grid.iloc[r:, c] = grid.iloc[r:, c] - 8
    grid = grid.mask(rng.random(grid.shape) < 0.03)
    grid.iloc[300:330, 2] = np.nan                        # 5시간 창의 관측이 80%에 못 미치는 구간
    return grid


def _refs(stations=8, seed=1):
    rng = np.random.default_rng(seed)
    ids = [f"ST{i:04d}" for i in range(stations)]
    return {d: pd.DataFrame({"mu": rng.normal(0, 4, stations), "sigma": rng.uniform(1, 5, stations),
                             "parking_lot": rng.integers(15, 30, stations).astype(float)}, index=ids)
            for d in DURS}


DAYS = {pd.Timestamp(d).date() for d in ("2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16")}
COLS = ["station_id", "ts", "재고", "거치대", "계획량", "mu", "sigma", "시", "요일", "회차", "시작뒤분",
        "변화1h", "변화3h", "최고3h", "최저3h", "꽉참", "최저30", "관측30", "흔적30", "낙폭30"]


def _sorted(frame):
    return frame.sort_values(["ts", "station_id"]).reset_index(drop=True)


@pytest.fixture(scope="module")
def built(exp):
    """같은 합성 격자로 만든 학습 행: 운영 쪽과 실험 쪽(한 번만 만들어 세 시험이 나눠 쓴다)."""
    pq = exp["pq"]
    grid, refs = _grid(), _refs()
    mine = _sorted(pm.build_rows(grid, refs, DAYS))
    theirs = _sorted(pq.build_rows(grid, refs, pq.window_arrays(grid), DAYS, stations="all",
                                   keep=lambda i, ts, start: i % pm.ALL_EVERY == 0))
    return grid, refs, mine, theirs


# ───────────────────────────────────────────── 1. 실험 코드와 같은 값

def test_정의는_56장_실험의_상수와_같다(exp):
    pq, pr = exp["pq"], exp["pr"]
    assert pm.H == pq.MAIN_HORIZON == 30
    assert pm.ALL_EVERY == pr.ALL_EVERY
    assert pm.FEATURES_H == pq.FEATURES_H and pm.HYBRID_Q == pq.HYBRID_Q
    assert pm.GBM_PARAMS == pq.GBM_PARAMS and pm.MIN_ROWS == pq.MIN_ROWS
    assert (pm.MIN_OBSERVED, pm.JUMP) == (pq.pv.MIN_OBSERVED, pq.pv.JUMP)
    assert pm.HOUR_DUR == pq.HOUR_DUR
    import project_config
    assert project_config.PICK_MODEL_QUANTILE == exp["mix"].CONFIRM_K == 0.7, "운영의 k는 56장이 고른 값이다"


def test_학습_행은_56장_실험의_행과_같다(exp, built):
    pq = exp["pq"]
    _grid_, _refs_, mine, theirs = built
    assert len(mine) == len(theirs) > 1000
    pd.testing.assert_frame_equal(mine[COLS], theirs[COLS], check_dtype=False)
    assert pm.usable(mine).equals(pq.usable(theirs, 30, True)), "학습에 쓰는 행(80% 관측 · 트럭 흔적 없음)도 같다"
    assert (~pm.usable(mine)).any() and pm.usable(mine).any(), "합성 격자가 두 경우를 모두 담는다"


def test_혼합_입력은_그_날을_뺀_값으로_56장과_같다(exp, built):
    pq = exp["pq"]
    rows = built[2]
    train = rows[pm.usable(rows)]
    mine = pm.add_hybrid_train(train)

    theirs_rows = rows.copy()
    theirs_rows["나눔"] = "학습"
    theirs = pq.add_hybrid(theirs_rows, theirs_rows.loc[train.index], 30).loc[train.index]
    for col in ("표중앙", "표상위", "표행수"):
        np.testing.assert_allclose(mine[col].to_numpy(dtype=float), theirs[col].to_numpy(dtype=float), equal_nan=True)
    assert (mine["표행수"] >= pm.MIN_ROWS).any(), "대여소 × 회차 분위수 경로(행 20개 이상)도 지난다"


def test_예측한_안전량은_56장의_방식과_같다(exp, built, monkeypatch):
    """회차 시작 틱 t에서: 실험은 전체 격자의 t 행으로, 운영은 t에서 끝나는 격자의 마지막 행으로 입력을 만든다.
    같은 모형으로 양쪽 입력을 예측해 맞대므로 모형은 짧게 배워도 된다."""
    pq = exp["pq"]
    grid, refs, rows, _theirs = built
    monkeypatch.setattr(pm, "GBM_PARAMS", {**pm.GBM_PARAMS, "max_iter": 30})
    train = pm.add_hybrid_train(rows[pm.usable(rows)])
    model = pm.fit(train, 0.7)
    bundle = {"model": model, "tables": pm.quantile_tables(rows[pm.usable(rows)])}

    t = pd.Timestamp("2026-10-16 15:00")
    stock_t = grid.loc[t].dropna()
    mine = pm.predict_safe(bundle, grid.loc[:t], refs, "_15_20", t, stock_t)

    target = pq.build_rows(grid, refs, pq.window_arrays(grid), {t.date()}, stations="all",
                           keep=lambda i, ts, start: ts == start and ts == t)
    plain = rows[pm.usable(rows)]
    for name, k in pq.HYBRID_Q.items():
        target[name] = pq.table_values(plain, target, 30, k).to_numpy()
    n = plain.groupby(["station_id", "회차"]).size()
    target["표행수"] = n.reindex(pd.MultiIndex.from_arrays([target["station_id"], target["회차"]])).fillna(0).to_numpy()
    theirs = pd.Series((target["재고"] - model.predict(target[pq.FEATURES_H]) - 1).to_numpy(),
                       index=target["station_id"].to_numpy())
    assert len(mine) == len(theirs) > 0
    np.testing.assert_allclose(mine.reindex(theirs.index).to_numpy(), theirs.to_numpy())


def _full(seed=2, n=40):
    rng = np.random.default_rng(seed)
    full = pd.DataFrame({
        "station_id": [f"ST{i:04d}" for i in range(n)], "station_name": [f"대여소{i}" for i in range(n)],
        "lat": rng.uniform(36.3, 36.4, n), "lon": rng.uniform(127.3, 127.4, n),
        "parking_lot": rng.integers(10, 30, n), "stock": rng.integers(0, 25, n),
        "target_qty": rng.uniform(0, 20, n), "rebal_qty": rng.integers(-12, 12, n),
        "mu": rng.normal(0, 3, n), "sigma": rng.uniform(1, 4, n)})
    picks = full[full["rebal_qty"] < -2].sort_values("rebal_qty").head(8)
    drops = full[full["rebal_qty"] > 2].sort_values("rebal_qty", ascending=False).head(8)
    base = pd.concat([picks, drops])
    safe = pd.Series(rng.uniform(-3, 15, n), index=full["station_id"].to_numpy())
    safe.iloc[::7] = np.nan                                   # 예측이 없는 대여소
    return full, base, safe


def test_고르기는_56장_K와_같다(exp):
    mix = exp["mix"]
    full, base, safe = _full()
    mine, left = pm.select_picks(base, full, safe)
    t = pd.Timestamp("2026-10-16 15:00")
    keyed = pd.Series(safe.to_numpy(), index=pd.MultiIndex.from_arrays([pd.Index([t] * len(safe)), safe.index]))
    theirs, left2 = mix.wide(full, base, t, keyed)
    assert left == left2
    key = ["station_id", "rebal_qty"]
    a = mine[key].assign(rebal_qty=mine["rebal_qty"].astype(int)).sort_values(key).reset_index(drop=True)
    b = theirs[key].assign(rebal_qty=theirs["rebal_qty"].round().astype(int)).sort_values(key).reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b)
    assert list(mine.columns) == list(base.columns), "pick_drop과 같은 열 순서"
    drops = base[base["rebal_qty"] > 0]
    assert mine[mine["rebal_qty"] > 0].set_index("station_id")["rebal_qty"].equals(
        drops.set_index("station_id")["rebal_qty"]), "배송 쪽은 그대로"


def test_take_until은_56장과_같다(exp):
    pr = exp["pr"]
    full, _base, safe = _full(seed=5)
    pool = full.copy()
    pool["a"] = np.floor(np.clip(safe.reindex(pool["station_id"]).fillna(0).to_numpy(), 0, None))
    for total in (0, 2, 3, 17, 60, 500):
        mine, left = pm.take_until(pool.copy(), total)
        theirs, left2 = pr.take_until(pool.copy(), total)
        assert left == left2
        assert mine[["station_id", "rebal_qty"]].equals(theirs[["station_id", "rebal_qty"]])


# ───────────────────────────────────────────── 2. 켜고 끄는 조건

@pytest.mark.parametrize("mode, kind, expect", [
    ("auto", "plan", True), ("auto", "experiment", False), ("auto", None, False), ("auto", "probe", False),
    ("on", "experiment", True), ("on", None, True), ("off", "plan", False)])
def test_auto는_운영_계획에서만_켠다(mode, kind, expect):
    assert pm.wanted(kind, mode)[0] is expect


def test_운영_계획이_아니면_지금_선정을_그대로_돌려준다(monkeypatch):
    full, base, _safe = _full()
    monkeypatch.setattr(pm.project_config, "PICK_MODEL", "auto")
    out, rule = pm.apply(base, full, full, "_15_20", day_type="weekday", period="26년 03월",
                         run_kind="experiment", log=lambda *_: None)
    assert out is base and rule == "현행"


def test_휴일과_재고_시계열_없음은_지금_방식으로_돌아간다(monkeypatch):
    full, base, _safe = _full()
    monkeypatch.setattr(pm.project_config, "PICK_MODEL", "auto")
    said = []
    out, rule = pm.apply(base, full, full, "_15_20", day_type="holiday", period="26년 03월", run_kind="plan",
                         log=said.append)
    assert (out is base, rule) == (True, "현행") and "휴일" in said[-1]
    out, rule = pm.apply(base, full, full, "_15_20", day_type="weekday", period="26년 03월", run_kind="plan",
                         log=said.append)                       # conftest의 빈 임시 DB
    assert (out is base, rule) == (True, "현행") and "재고 시계열이 없다" in said[-1]


def test_회차_시작과_먼_관측은_쓰지_않는다():
    tau = pd.Timestamp("2026-10-20 14:50")
    assert pm.round_start("_15_20", tau) == pd.Timestamp("2026-10-20 15:00")
    assert pm.round_start("_10_15", tau) is None                  # 10시 시작에서 4시간 50분 뒤
    assert pm.round_start("_20_05", pd.Timestamp("2026-10-20 19:30")) == pd.Timestamp("2026-10-20 20:00")
    assert pm.round_start("_05_10", pd.Timestamp("2026-10-21 04:40")) == pd.Timestamp("2026-10-21 05:00")
    assert pm.round_start("_20_05", pd.Timestamp("2026-10-21 00:30")) is None


# ───────────────────────────────────────────── 3. 끝까지 돈다(합성 DB)

def _seed_stock(conn, stations, days, last):
    rng = np.random.default_rng(7)
    rows = []
    for ts in pd.date_range(days[0], last, freq="10min"):
        if ts.date() not in days and ts.normalize() != pd.Timestamp(last).normalize():
            continue
        for j, sid in enumerate(stations):
            rows.append((ts.strftime("%Y-%m-%d %H:%M"), sid, int(10 + 6 * np.sin(ts.hour / 24 * 6.28 + j)
                                                                + rng.integers(-2, 3))))
    conn.executemany("INSERT INTO stock_history (observed_at, station_id, stock) VALUES (?, ?, ?)", rows)
    conn.commit()


def test_운영_계획에서는_학습하고_저장하고_다시_읽어_수거를_고른다(monkeypatch, tmp_path):
    import db

    stations = [f"ST{i:04d}" for i in range(12)]
    days = [pd.Timestamp(d).date() for d in ("2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15",
                                             "2026-10-16", "2026-10-19")]
    last = pd.Timestamp("2026-10-20 14:50")
    with db.session() as conn:
        _seed_stock(conn, stations, days, last)
    rng = np.random.default_rng(3)
    refs = {d: pd.DataFrame({"mu": rng.normal(0, 3, 12), "sigma": rng.uniform(1, 4, 12),
                             "parking_lot": np.full(12, 25.0)}, index=stations) for d in DURS}
    monkeypatch.setattr(pm, "load_net_weekday", lambda period: pd.DataFrame({"x": [1]}))
    monkeypatch.setattr(pm, "reference_stats", lambda net, info: refs)
    monkeypatch.setattr(pm, "MODEL_DIR", tmp_path)
    monkeypatch.setattr(pm.project_config, "PICK_MODEL", "auto")
    monkeypatch.setattr(pm, "GBM_PARAMS", {**pm.GBM_PARAMS, "max_iter": 20})   # 흐름을 보는 시험이라 짧게 배운다

    info = pd.DataFrame({"station_id": stations, "station_name": stations, "lat": 36.35, "lon": 127.38,
                         "parking_lot": 25, "stock": 12})
    rebal = pd.DataFrame({"station_id": stations, "mu": 0.0, "sigma": 1.0, "parking_lot": 25,
                          "stock": [18, 17, 16, 15, 14, 13, 4, 3, 2, 1, 12, 12],
                          "target_qty": 10.0, "rebal_qty": [-8, -7, -6, -5, -4, -3, 6, 7, 8, 9, 0, 1]})
    full = rebal.merge(info, on="station_id", suffixes=("", "_info"))[pm.INFO_COLS]
    base = pd.concat([full[full["rebal_qty"] < -2], full[full["rebal_qty"] > 2]])

    said = []
    out, rule = pm.apply(base, rebal, info, "_15_20", day_type="weekday", period="26년 03월", run_kind="plan",
                         clock=last + pd.Timedelta(minutes=5), log=said.append)
    assert rule == "K0.7", said
    assert any("학습했습니다" in s for s in said)
    assert list(tmp_path.glob("pick_k0.7_*.pkl")), "모형을 저장한다"
    drops = base[base["rebal_qty"] > 0].set_index("station_id")["rebal_qty"]
    assert out[out["rebal_qty"] > 0].set_index("station_id")["rebal_qty"].equals(drops), "배송 쪽은 그대로"
    picked = -out.loc[out["rebal_qty"] < 0, "rebal_qty"].sum()
    assert 0 < picked <= -base.loc[base["rebal_qty"] < 0, "rebal_qty"].sum(), "수거 총량을 넘지 않는다"
    assert (out.loc[out["rebal_qty"] < 0, "rebal_qty"] <= -3).all(), "2대 이하 수거는 없다"

    said.clear()
    out2, rule2 = pm.apply(base, rebal, info, "_15_20", day_type="weekday", period="26년 03월", run_kind="plan",
                           clock=last + pd.Timedelta(minutes=5), log=said.append)
    assert rule2 == "K0.7" and any("저장한 모형" in s for s in said), "같은 학습 날이면 다시 배우지 않는다"
    pd.testing.assert_frame_equal(out.reset_index(drop=True), out2.reset_index(drop=True))

    said.clear()
    out3, rule3 = pm.apply(base, rebal, info, "_15_20", day_type="weekday", period="26년 03월", run_kind="plan",
                           clock=last + pd.Timedelta(hours=2), log=said.append)
    assert (out3 is base, rule3) == (True, "현행") and "오래됐다" in said[-1]


# ───────────────────────────────────────────── 4. 실험 경로에 닿지 않는다

def test_step1은_pick_model을_main_블록_안에서만_불러온다():
    """실험(baseline_compare.load_step1 등)은 step1 모듈을 불러와 select_top_unbalanced_st()를 쓴다. 모듈 맨 위에서
    pick_model을 불러오면 실험마다 sklearn을 싣고, 실험이 잰 '현행'에 K가 섞일 길이 생긴다."""
    tree = ast.parse((ROOT / "pipeline/step1_cluster/top_st_clustering.py").read_text(encoding="utf-8"))

    def imports(nodes):
        found = []
        for node in nodes:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Import):
                    found += [a.name for a in sub.names]
                elif isinstance(sub, ast.ImportFrom):
                    found += [sub.module or ""] + [a.name for a in sub.names]
        return found

    main = [n for n in tree.body if isinstance(n, ast.If) and "__main__" in ast.unparse(n.test)]
    top = [n for n in tree.body if n not in main]
    assert not any("pick_model" in name for name in imports(top))
    assert any("pick_model" in name for name in imports(main))
    assert "pick_model.apply(" in ast.unparse(main[0])
    select = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "select_top_unbalanced_st")
    assert "pick_model" not in ast.unparse(select), "실험이 쓰는 선정 함수는 그대로다"
