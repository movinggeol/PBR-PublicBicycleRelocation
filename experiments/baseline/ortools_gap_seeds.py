"""실험 — OR-Tools 갭이 **씨앗에 얼마나 흔들리나** (TODO 대기-0A ②, 1.26.71).

## 왜 재는가

6장이 논문에 싣고 있는 *"greedy가 OR-Tools보다 평균 2.2%·최악 10.1% 길다"* 는
**씨앗 42 하나로 잰 값**이다. 그런데 씨앗은 K-Medoids 군집화를 바꾸므로
**경로 문제 자체가 달라진다** — 같은 후보 집합이라도 군집이 다르면 다른 TSP다.

이 저장소는 씨앗 하나로 잰 표가 뒤집히는 것을 이미 두 번 겪었다
(11장 `_15_20`, 14장 논문 6.3의 B2 행). 갭도 같은 자리에 있다.

## 무엇을 재나

같은 기간·같은 스냅샷·같은 탐색 시간으로 **씨앗만 바꿔** 갭을 다시 잰다.
묻는 것은 둘이다.

    ① 갭 평균이 씨앗에 얼마나 흔들리나   ← 흔들리면 "평균 2.2%"를 그대로 못 쓴다
    ② 갭이 큰 클러스터가 같은 곳인가     ← 6장의 '선택적 재최적화' 논거가 여기 걸린다

⚠️ **씨앗마다 클러스터 수가 다를 수 있다.** K는 작업량이 정하는데 군집이 바뀌면
경계가 달라진다. 그래서 클러스터 번호로 짝지어 비교하지 않는다 — 씨앗 사이에
같은 번호가 같은 군집이 아니다.

사용법:
    python experiments/baseline/ortools_gap_seeds.py --seeds "42,7,13"
    python experiments/baseline/ortools_gap_seeds.py --seeds "42,7,13" --limit-sec 60
"""
from __future__ import annotations

import argparse
import os
import statistics
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

GAP_SCRIPT = Path(__file__).with_name("ortools_gap.py")


def run_seed(seed: int, args, out_path: Path) -> pd.DataFrame:
    """씨앗 하나로 갭을 잰다. 이미 있는 파일은 다시 돌리지 않는다."""
    if out_path.exists():
        print(f"  씨앗 {seed}: 기존 파일 사용 ({out_path.name})", flush=True)
        return pd.read_csv(out_path)

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [sys.executable, str(GAP_SCRIPT),
           "--period", args.period, "--duration", args.duration,
           "--seed", str(seed), "--limit-sec", str(args.limit_sec),
           "--out", str(out_path)]
    if args.run_label:
        cmd += ["--run-label", args.run_label]

    proc = subprocess.run(cmd, capture_output=True, text=True,
                          env=env, encoding="utf-8")
    if proc.returncode != 0:
        raise SystemExit(f"씨앗 {seed} 실패:\n{proc.stderr[-2000:]}")
    print(f"  씨앗 {seed} 완료", flush=True)
    return pd.read_csv(out_path)


def report(frames: dict, args) -> None:
    print("\n" + "=" * 88)
    print(f"OR-Tools 갭의 씨앗 민감도 — {args.period} {args.duration}"
          f" · 탐색 {args.limit_sec}초")
    print("=" * 88)

    print(f"\n{'씨앗':>6} {'갭평균%':>9} {'갭최대%':>9} {'클러스터':>9}"
          f" {'절감km':>9} {'총km':>9} {'음수':>5}")
    rows = []
    for seed, d in frames.items():
        절감 = float((d.greedy_km - d.ortools_km).sum())
        음수 = int((d.gap_pct < -1e-6).sum())
        rows.append((seed, d.gap_pct.mean(), d.gap_pct.max(), len(d),
                     절감, d.greedy_km.sum(), 음수))
        print(f"{seed:>6} {d.gap_pct.mean():>9.2f} {d.gap_pct.max():>9.2f}"
              f" {len(d):>9} {절감:>9.1f} {d.greedy_km.sum():>9.1f} {음수:>5}")

    means = [r[1] for r in rows]
    maxes = [r[2] for r in rows]
    if len(means) < 2:
        print("\n씨앗이 하나뿐이라 흔들림을 잴 수 없습니다.")
        return

    폭 = max(means) - min(means)
    print(f"\n① 갭 평균 — {statistics.mean(means):.2f}%"
          f" (표준편차 {statistics.stdev(means):.2f}%p · 폭 **{폭:.2f}%p**)")
    print(f"   최댓값끼리 — {min(maxes):.1f}% ~ {max(maxes):.1f}%")
    if 폭 >= min(means):
        print("   🔴 **폭이 최솟값만큼 크다 — 씨앗 하나로 잰 갭은 쓸 수 없다.**")

    print("\n② 갭이 큰 클러스터가 같은 곳인가")
    for seed, d in frames.items():
        top = d.nlargest(3, "gap_pct")
        desc = " / ".join(f"{int(r.nodes)}노드 {r.gap_pct:.1f}%"
                          for r in top.itertuples())
        print(f"   씨앗 {seed}: {desc}")
    print("   ⚠️ 씨앗마다 군집 경계가 달라 **클러스터 번호로 짝지을 수 없다.**"
          " 상위 갭의 '크기'가 씨앗에 따라 얼마나 다른지만 본다.")
    print("=" * 88)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="OR-Tools 갭의 씨앗 민감도 (TODO 대기-0A ②)")
    parser.add_argument("--period", default="25년 11월")
    parser.add_argument("--run-label", default="2026-08-11 real")
    parser.add_argument("--duration", default="_05_10")
    parser.add_argument("--seeds", default="42,7,13")
    parser.add_argument("--limit-sec", type=int, default=60)
    parser.add_argument("--out-dir", default="experiments/baseline")
    args, _ = parser.parse_known_args()

    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir

    print(f"씨앗 {seeds} · {args.period} {args.duration}"
          f" · 탐색 {args.limit_sec}초 · 스냅샷 '{args.run_label or '최신'}'")
    frames = {}
    for seed in seeds:
        path = out_dir / f"ortools_gap_seed{seed}.csv"
        frames[seed] = run_seed(seed, args, path)

    report(frames, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
