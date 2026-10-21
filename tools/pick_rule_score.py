"""운영 계획의 수거를 `pick_rule`별로 관측 재고에 채점한다 (읽기 전용, 1.26.380).

## 왜

1.26.375부터 운영 계획(실행 종류 plan)은 수거를 K(수거 대수 학습)로 고르고, 쓰지 못한 회차는 현행으로 돌아간다.
어느 쪽으로 골랐는지는 `pick_drop.pick_rule`에 남는다. 56장은 과거 회차로 재었으므로, **실제로 세운 계획**이 관측에서도
덜 해치는지는 계획이 쌓이는 대로 따로 봐야 한다(TODO 수거 줄의 남은 일 ③).

## 무엇을 재나

- 대상: 실행 종류 plan이고 `pick_rule`이 적힌 계획. 계획을 만든 시각이 그 회차 시작 2시간 전 ~ 1시간 뒤인 것만 쓴다
  (`pick_model.round_start`, K가 쓰이는 조건과 같다). 회차 시작 t 뒤 5시간 창이 아직 닫히지 않았으면 기다린다.
- 자: 53장 `worklist_validity.score()` 그대로. 주 기준 수거 무해 = 창 안 관측 최저 − 수거량 ≥ 1. 3 · 5시간.
- **짝 비교**: K로 고른 계획마다 같은 재배치량(`rebalance_plan`)에서 현행(step1 `select_top_unbalanced_st`)이 골랐을 수거를
  다시 세워 같은 창에서 함께 채점한다. 날이 달라 생기는 차이를 빼려는 것이다. 현행으로 돌아간 계획은 그 수거만 센다.
- 판정은 하지 않는다(사전 등록 없음). 회차가 20개 넘게 쌓이면 등록하고 판정한다.

    python tools/pick_rule_score.py           # 요약
    python tools/pick_rule_score.py --rows    # 계획마다
"""
from __future__ import annotations

import argparse
import contextlib
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments" / "structure"))

import project_config  # noqa: E402,F401  (콘솔 인코딩을 먼저 세운다)

import pandas as pd  # noqa: E402

import db  # noqa: E402
import plan_validity as pv  # noqa: E402
import worklist_validity as wv  # noqa: E402
from pipeline.step1_cluster import pick_model as pm  # noqa: E402

HORIZONS = (18, 30)          # 3 · 5시간 (53장 판정 지평)


def plans(conn) -> pd.DataFrame:
    """pick_rule이 적힌 계획 실행 × 회차 — (run_label, duration, created_at, pick_rule)."""
    return pd.read_sql(
        "SELECT r.run_label, r.duration, r.created_at, MIN(p.pick_rule) AS pick_rule"
        " FROM runs r JOIN pick_drop p ON p.run_label = r.run_label AND p.duration = r.duration"
        " WHERE r.kind = 'plan' AND p.pick_rule IS NOT NULL"
        " GROUP BY r.run_label, r.duration, r.created_at ORDER BY r.created_at", conn)


def current_picks(conn, label: str, duration: str, info: pd.DataFrame, step1) -> pd.Series:
    """같은 재배치량에서 현행이 골랐을 작업 목록(station_id → rebal_qty)."""
    rebal = db.load_frame(conn, "rebalance_plan", run_label=label, duration=duration)
    buf = io.StringIO()
    rebal.drop(columns=["run_label", "duration"], errors="ignore").to_csv(buf, index=False)
    buf.seek(0)
    with contextlib.redirect_stdout(io.StringIO()):
        base = step1.select_top_unbalanced_st(buf, duration, info)
    return base.set_index("station_id")["rebal_qty"].astype(float)


def score_plan(grid: pd.DataFrame, t: pd.Timestamp, lists: dict, cap: pd.Series) -> list:
    """작업 목록마다 × 지평마다 53장의 셈. 창이 모자라면 []."""
    if t not in grid.index:
        return []
    i = grid.index.get_loc(t)
    s0 = grid.iloc[i].dropna()
    rows = []
    for h in HORIZONS:
        ws = pv.window_stats(grid, i, h)
        if ws is None:
            return []
        for name, delta in lists.items():
            rows.append({"목록": name, "지평": h, **wv.score(delta, s0, cap, ws)})
    return rows


def summarize(rows: pd.DataFrame) -> pd.DataFrame:
    """규칙 · 목록 · 지평마다 합친 비율."""
    g = rows.groupby(["pick_rule", "목록", "지평"])
    out = g[["수거곳", "수거대수", "수거무해", "배송수", "배송적중"]].sum()
    out["계획"] = g["run_label"].nunique()
    out["수거무해율"] = out["수거무해"] / out["수거곳"].where(out["수거곳"] > 0)
    out["배송적중률"] = out["배송적중"] / out["배송수"].where(out["배송수"] > 0)
    return out.reset_index()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rows", action="store_true", help="계획마다 찍는다")
    args = ap.parse_args(argv)

    import importlib
    step1 = importlib.import_module("pipeline.step1_cluster.top_st_clustering")
    rows, skipped = [], {}
    with db.session() as conn:
        todo = plans(conn)
        last = conn.execute("SELECT MAX(observed_at) FROM stock_history").fetchone()[0]
        for r in todo.itertuples():
            t = pm.round_start(r.duration, pd.Timestamp(r.created_at))
            if t is None:
                skipped["회차 시작과 먼 계획"] = skipped.get("회차 시작과 먼 계획", 0) + 1
                continue
            if last is None or t + pd.Timedelta(minutes=10 * max(HORIZONS)) > pd.Timestamp(last):
                skipped["창이 아직 안 닫힘"] = skipped.get("창이 아직 안 닫힘", 0) + 1
                continue
            info = db.load_frame(conn, "station_info", run_label=r.run_label)
            picks = db.load_frame(conn, "pick_drop", run_label=r.run_label, duration=r.duration)
            lists = {"계획": picks.set_index("station_id")["rebal_qty"].astype(float)}
            if r.pick_rule != "현행":
                lists["같은 회차의 현행"] = current_picks(conn, r.run_label, r.duration, info, step1)
            grid = pm.load_grid(conn, t - pd.Timedelta(minutes=10), t + pd.Timedelta(minutes=10 * max(HORIZONS)))
            cap = info.drop_duplicates("station_id").set_index("station_id")["parking_lot"].astype(float)
            got = score_plan(grid, t, lists, cap)
            if not got:
                skipped["회차 시작 관측 없음"] = skipped.get("회차 시작 관측 없음", 0) + 1
                continue
            for row in got:
                rows.append({"run_label": r.run_label, "duration": r.duration, "pick_rule": r.pick_rule, "t": t, **row})
    print(f"pick_rule이 적힌 계획 {len(todo)}개 · 채점 {len({(x['run_label'], x['duration']) for x in rows})}개"
          + (" · 뺀 것 " + " · ".join(f"{k} {v}" for k, v in skipped.items()) if skipped else ""))
    if not rows:
        print("아직 채점할 계획이 없다. 계획이 쌓이면 다시 돌린다")
        return 0
    frame = pd.DataFrame(rows)
    if args.rows:
        print(frame[["run_label", "duration", "pick_rule", "목록", "지평", "수거곳", "수거대수", "수거무해", "배송수", "배송적중"]]
              .to_string(index=False))
    out = summarize(frame)
    print("\n규칙 · 목록 · 지평마다 (주 기준, 판정 아님)")
    for x in out.itertuples():
        print(f"  {x.pick_rule:<5} {x.목록:<10} {x.지평 // 6}시간  계획 {x.계획:>3} · 수거 {x.수거곳:>4}곳 {x.수거대수:>6.0f}대 · "
              f"무해 {x.수거무해율:6.1%} · 배송 적중 {x.배송적중률:6.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
