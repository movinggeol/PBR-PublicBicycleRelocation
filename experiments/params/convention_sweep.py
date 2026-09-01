"""실험 — **관행값 넷**을 흔들어 본다 (TODO 대기-3 / P4-6, 1.26.67).

논문 8장의 *"가정값"* 목록을 줄이는 작업이다. 넷 다 **실험으로 정한 값이 아니라
관행값**이라, 근거를 물으면 그렇게 답할 수밖에 없었다.

| 상수 | 현재값 | 무엇을 정하나 |
| --- | ---: | --- |
| `REBAL_MIN_QTY` | 2 | 이 값 이하의 작업량은 손대지 않는다 (**후보를 고른다**) |
| `ADJUST_MAX_ITER` | 200 | 군집 조정 루프의 최대 이동 횟수 |
| `ADJUST_BALANCE_OK` | 3 | 모든 군집이 이 값 이내면 만족하고 끝낸다 |
| `ADJUST_BALANCE_LIMIT` | 5 | 이 값을 넘는 군집은 재조정 대상 |

## 모집단을 어떻게 잡나 — 17·18장의 교훈

🔴 **`REBAL_MIN_QTY`만 성격이 다르다.** 이 값은 **후보 집합 자체를 바꾼다**
(`top_st_clustering.py:174`). 결품은 그 집합 위의 *평균*이라, 각 문턱의 자기
후보 집합에서 재면 **분모가 흔들려 비교가 성립하지 않는다** — 상한 격자에서
같은 결함이 결론을 뒤집었고(17장), z 격자에서는 **모집단이 승자를 정하고
있었다**(18장).

그래서 이 스크립트는 **중립 모집단(전체 대여소)** 위에서 잰다. 손대지 않은
대여소는 `delta=0`으로 들어가므로 *"후보에서 뺐다"* 는 선택의 대가가 그대로
잡힌다.

✅ **나머지 셋은 후보를 바꾸지 않는다** — 군집을 어떻게 나눌지만 바꾼다. 그래도
같은 자로 재는 것이 맞으므로 함께 중립 모집단으로 잰다.

## 미리 아는 것

⚠️ **`REBAL_MIN_QTY`는 무력할 가능성이 높다.** 1.21.7 실측으로 문턱 2에서 자격을
갖춘 대여소가 pick 76~89곳·drop 208~298곳인데 `TOP_STATION_LIMIT`(각 50곳)이
**먼저 자른다.** 문턱을 1~4로 바꿔도 후보가 그대로다 — **문턱이 실제로 고르기
시작하는 것은 5부터**다. 그래서 격자를 **5 이상까지** 넓혀 잡는다.

사용법:
    python experiments/params/convention_sweep.py                  # 넷 다
    python experiments/params/convention_sweep.py --knob REBAL_MIN_QTY
    python experiments/params/convention_sweep.py --period "26년 03월" --run-label "sweep-10"
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

WORKER = Path(__file__).with_name("_convention_worker.py")

# 관행값 → (환경변수, 격자, 현재값)
KNOBS = {
    "REBAL_MIN_QTY":        ("PBR_REBAL_MIN_QTY",        [1, 2, 3, 5, 8, 12], 2),
    "ADJUST_MAX_ITER":      ("PBR_ADJUST_MAX_ITER",      [50, 100, 200, 400], 200),
    "ADJUST_BALANCE_OK":    ("PBR_ADJUST_BALANCE_OK",    [1, 2, 3, 5, 8], 3),
    "ADJUST_BALANCE_LIMIT": ("PBR_ADJUST_BALANCE_LIMIT", [3, 5, 8, 12], 5),
}


def collect(knob, values, args) -> list:
    env_name = KNOBS[knob][0]
    rows = []
    for value in values:
        env = dict(os.environ)
        env[env_name] = str(value)
        env["PBR_EXP_PERIOD"] = args.period
        env["PBR_EXP_RUN_LABEL"] = args.run_label
        env["PBR_EXP_DURATIONS"] = ",".join(args.durations)
        env["PBR_EXP_SEEDS"] = ",".join(str(s) for s in args.seeds)
        env["PBR_EXP_KNOB"] = knob
        env["PBR_EXP_VALUE"] = str(value)
        env["PYTHONIOENCODING"] = "utf-8"

        proc = subprocess.run([sys.executable, str(WORKER)],
                              capture_output=True, text=True,
                              env=env, encoding="utf-8")
        if proc.returncode != 0:
            raise SystemExit(f"{knob}={value} 실패:\n{proc.stderr[-2000:]}")
        rows.extend(json.loads(proc.stdout.strip().splitlines()[-1]))
        print(f"    {knob}={value} 완료", flush=True)
    return rows


def evaluate(rows, args) -> pd.DataFrame:
    """**중립 모집단(전체 대여소)** 위에서 다시 평가한다."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        spec = importlib.util.spec_from_file_location(
            "baseline_compare",
            ROOT / "experiments" / "baseline" / "baseline_compare.py")
        bc = importlib.util.module_from_spec(spec)
        sys.modules["baseline_compare"] = bc
        spec.loader.exec_module(bc)
        net, st_info, _w = bc.load_inputs(
            args.period, args.run_label, "weekday", 14, "")

        out = []
        for r in rows:
            delta = json.loads(r["delta_json"])
            before, after = bc.stockout(net, st_info, delta, r["duration"])
            out.append({k: v for k, v in r.items() if k != "delta_json"}
                       | {"before": before, "after": after})
    df = pd.DataFrame(out)
    # 값은 숫자로 — 문자열이면 표가 12, 5 순으로 정렬돼 읽기 어렵다.
    df["value"] = pd.to_numeric(df["value"])
    return df


def report(df, knob) -> None:
    현재 = KNOBS[knob][2]
    print("\n" + "=" * 88)
    print(f"[{knob}]  중립 모집단(전체 대여소)에서 잰 결품 시간(h) — 현재값 {현재}")
    print("=" * 88)

    piv = df.pivot_table(index="value", columns="duration", values="after")
    piv["3회차평균"] = piv.mean(axis=1)
    print(piv.round(5).to_string())

    # ⚠️ **회차별로 확인한다.** 회차마다 결품 수준이 다르므로 회차를 섞어 평균
    #    내면, 어떤 값에서 회차가 통째로 빠졌을 때(후보 0곳) **자가 흔들린 것처럼
    #    보인다** — REBAL_MIN_QTY=8에서 실제로 그렇게 보였다.
    per = df.groupby(["duration", "value"]).before.mean().unstack()
    print("\n재배치 전 (회차별) — 모집단이 같으면 값에 무관해야 한다:")
    flat = True
    for dur, row in per.iterrows():
        ok = (row.max() - row.min()) < 1e-9
        flat = flat and ok
        print(f"  {dur}  {row.mean():.5f}h  {'✅' if ok else '🔴 값에 따라 움직인다'}")
    if not flat:
        print("  🔴 **자를 의심하라** — 모집단이 값에 따라 바뀌고 있다.")

    # 후보가 0곳이 된 회차가 있는 값은 3회차 평균을 비교할 수 없다.
    완결 = df.groupby("value").duration.nunique()
    부족 = 완결[완결 < df.duration.nunique()]
    if not 부족.empty:
        print("\n⚠️ **회차가 빠진 값이 있다** — 그 회차는 후보가 0곳이었다:")
        for v, n in 부족.items():
            print(f"  {knob}={v}: {n}/{df.duration.nunique()}회차만 계획이 섰다")
        print("  → 이 값들은 **3회차 평균을 다른 값과 비교하지 않는다.**")

    b = df.groupby("value").before.mean()
    full = [v for v in piv.index if v not in set(부족.index)]
    if len(full) < 2:
        print("\n비교할 값이 부족하다.")
        return
    m = piv.loc[full, "3회차평균"]
    폭 = m.max() - m.min()
    잡음 = df[df.value.isin(full)].groupby(["value", "duration"]).after.std().max()
    print(f"\n격자 전체 폭 = {폭:.5f}h = **{폭 * 3600:.1f}초**"
          f"  (재배치 전의 {폭 / b.mean() * 100:.3f}%,"
          f" 3회차가 다 선 값 {full}만)")
    if 잡음 and 잡음 > 0:
        print(f"최대 씨앗 잡음 = {잡음:.5f}h → 신호/잡음 = **{폭 / 잡음:.2f}**")
        if 폭 / 잡음 < 3:
            print("  ⚠️ 3 미만이다 — **씨앗 잡음과 구분되지 않는다**(11장 교훈).")

    cur = m.get(현재)
    best = m.idxmin()
    if cur is not None:
        print(f"\n현재값 {현재} = {cur:.5f}h · 최저 {best} = {m.min():.5f}h"
              f"  (차이 {abs(cur - m.min()) * 3600:.1f}초)")
        if best == 현재:
            print("  ✅ 현재값이 최저다.")

    # 후보가 바뀌는 손잡이인지 — REBAL_MIN_QTY 진단
    c = df.groupby("value").candidates.mean()
    if c.max() - c.min() > 0.5:
        print(f"\n📌 후보 수가 값에 따라 바뀐다: {c.min():.0f} ~ {c.max():.0f}곳")
        print(c.round(1).to_string())
    else:
        print(f"\n📌 후보 수가 값과 **무관하다**({c.mean():.0f}곳) — "
              "이 손잡이는 후보 선정에 관여하지 않는다.")

    print("\n비용:")
    print(df.groupby("value")[["km", "over", "max_min"]].mean().round(2).to_string())
    print("=" * 88)


def main() -> int:
    parser = argparse.ArgumentParser(description="관행값 민감도 스윕 (TODO 대기-3)")
    parser.add_argument("--knob", default="", help=f"하나만: {', '.join(KNOBS)}")
    parser.add_argument("--values", default="", help="격자를 직접 준다 (쉼표)")
    parser.add_argument("--period", default="25년 11월")
    parser.add_argument("--run-label", default="2026-08-11 real")
    parser.add_argument("--duration", default="_05_10,_10_15,_15_20")
    parser.add_argument("--seeds", default="42,7,13")
    parser.add_argument("--out", default="")
    args, _ = parser.parse_known_args()

    args.durations = [d.strip() for d in args.duration.split(",") if d.strip()]
    args.seeds = [int(s) for s in args.seeds.split(",") if s.strip()]

    knobs = [args.knob] if args.knob else list(KNOBS)
    for k in knobs:
        if k not in KNOBS:
            raise SystemExit(f"모르는 손잡이 '{k}'. 가능: {', '.join(KNOBS)}")

    print(f"관행값 {len(knobs)}개 · 회차 {len(args.durations)} · 씨앗 {len(args.seeds)}"
          f"  (스냅샷 '{args.run_label}', {args.period})")
    print("🔴 중립 모집단(전체 대여소)으로 잰다 — 후보를 바꾸는 손잡이가 있다"
          " (EXPERIMENTS 17·18장).\n")

    frames = []
    for knob in knobs:
        values = ([int(v) for v in args.values.split(",")] if args.values
                  else KNOBS[knob][1])
        print(f"  [{knob}] {values}")
        rows = collect(knob, values, args)
        df = evaluate(rows, args)
        report(df, knob)
        frames.append(df)

    if args.out:
        out = Path(args.out)
        if not out.is_absolute():
            out = ROOT / out
        pd.concat(frames).to_csv(out, index=False)
        print(f"\n결과를 저장했습니다: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
