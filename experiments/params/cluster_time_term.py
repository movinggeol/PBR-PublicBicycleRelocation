"""실험 — 군집 목적함수의 거리 항을 **순회거리**로 바꾼다 (TODO 대기-0B ②, 1.26.69).

## 무엇이 문제인가

0B의 진단은 이미 서 있다 — 소요시간의 60~75%가 이동이고, 예산을 넘긴 군집은
작업량이 많아서가 아니라 **거리가 길어서** 넘었다(r=0.966). 그런데 목적함수는

    J = α·(수급 불균형)² + β·(크기 편차)² + γ·(거리합)

의 `거리합`이 **메도이드까지의 거리 합**이다(`adjust_module._cluster_terms`).
이것은 *"군집이 얼마나 퍼져 있나"* 는 재지만 **차량이 실제로 도는 거리**가
아니다. 별 모양으로 퍼진 6곳과 한 줄로 늘어선 6곳은 메도이드 거리합이 같아도
순회거리가 다르다.

## 무엇을 바꿔 보나

`_cluster_terms`가 돌려주는 거리 항만 **최근접 이웃 순회(NN tour)** 길이로
바꾼다. 나머지(α·β 항, 조정 루프, K 결정)는 손대지 않는다 — 한 번에 하나만
흔들어야 무엇이 무엇을 바꿨는지 알 수 있다.

    현행:  Σ |점 − 메도이드|            (cityblock)
    대안:  NN 순회 길이                  (닫힌 순회, km)

⚠️ **γ의 스케일이 달라진다.** 두 항은 단위도 크기도 다르므로 같은 γ를 쓰면
거리 항의 비중이 통째로 바뀐다. 그래서 **γ를 함께 스윕**한다 — 순회 항에서
현행과 같은 비중을 내는 γ가 얼마인지 모르는 채로 한 값만 재면, 항을 바꾼
효과인지 비중이 달라진 효과인지 가릴 수 없다.

## 판정 규칙 (0B가 미리 정해 둔 것)

- **편익은 결품 시간, 비용은 최장 소요·예산 초과·총 이동거리.**
- **개선률·목표 도달률은 쓰지 않는다** (`target_qty`가 분모라 흔들린다).
- **이긴 조합 수를 함께 센다** — 평균만 보면 한 조합에 끌려간다.
- 🔴 **맞바꿈을 반드시 확인한다.** 1.21.6에서 예산을 제약으로 걸었더니 초과는
  0건이 됐지만 결품이 40초 나빠졌다. *"준수율만 올리고 결품을 악화시키는 개선"*
  은 개선이 아니다.

**중립 모집단(전체 대여소)으로 잰다** — 이 실험은 후보 집합을 바꾸지 않지만,
17·18장 이후로는 그것이 이 저장소의 기본값이다.

사용법:
    python experiments/params/cluster_time_term.py
    python experiments/params/cluster_time_term.py --period "26년 03월" --run-label "sweep-10"
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# 출력 인코딩 가드를 깨운다 — 이 스크립트는 파이프라인 모듈을 importlib으로
# 늦게 부르므로, 그 전에 찍는 문구가 cp949로 인코딩돼 죽었다(2026-09-12 실측:
# 🔴 한 글자에 UnicodeEncodeError). project_config를 거치면 _force_utf8_output()이
# 돈다 — 저장소가 이 함정을 막아 둔 단일 지점이다.
import project_config  # noqa: F401,E402

# 순회 항으로 바꿀 때 함께 볼 γ 후보. 현행 3000은 메도이드 거리합 기준이라
# 그대로 쓰면 비중이 어긋난다 — 넓게 훑는다.
GAMMA_TOUR = [100, 300, 1000, 3000]


def load_baseline():
    path = ROOT / "experiments" / "baseline" / "baseline_compare.py"
    spec = importlib.util.spec_from_file_location("baseline_compare", path)
    bc = importlib.util.module_from_spec(spec)
    sys.modules["baseline_compare"] = bc
    spec.loader.exec_module(bc)
    return bc


def patch_tour_term(adjust_mod, step1):
    """`_cluster_terms`의 거리 항을 NN 순회 길이로 갈아 끼운다.

    되돌릴 수 있게 원본을 돌려준다 — 같은 프로세스에서 현행도 재기 때문이다.
    """
    original = adjust_mod._cluster_terms

    def tour_terms(pts, qty_sum):
        if len(pts) < 2:
            return qty_sum ** 2, 0.0
        dist = step1._pairwise_km(pts[:, 0], pts[:, 1])
        return qty_sum ** 2, step1._nn_tour_km(dist)

    adjust_mod._cluster_terms = tour_terms
    return original


def measure(bc, step1, args, tour: bool, gamma: float) -> list:
    """한 설정으로 3회차 × 씨앗을 돌려 행을 만든다."""
    solver = bc.ilp_mod.build_solver()
    net, st_info, warmup = bc.load_inputs(
        args.period, args.run_label, "weekday", args.warmup_days, "")

    step1.CLUSTER_GAMMA = gamma
    rows = []
    for duration in args.durations:
        base = bc.build_candidates(net, st_info, duration, None, warmup,
                                   args.warmup_days, step1)
        if base.empty:
            continue
        for seed in args.seeds:
            _c, routes = bc.plan_with_clusters(base.copy(), step1, solver,
                                               adjust=True, seed=seed)
            delta = bc.executed_delta(routes)
            before, after = bc.stockout(net, st_info, delta, duration)  # 중립 모집단
            stats = bc.route_stats(routes)
            rows.append({"항": "순회" if tour else "메도이드", "gamma": gamma,
                         "duration": duration, "seed": seed,
                         "before": before, "after": after, **stats})
    return rows


def report(df) -> None:
    print("\n" + "=" * 92)
    print("군집 목적함수의 거리 항 — 메도이드 거리합 대 NN 순회거리")
    print("=" * 92)

    base = df[df["항"] == "메도이드"]
    if base.empty:
        print("현행 기준선이 없습니다.")
        return

    print("\n현행(메도이드 · γ=3000) 회차별:")
    print(base.groupby("duration")[["after", "km", "over", "max_min"]]
          .mean().round(3).to_string())
    print(f"  3회차 평균 — 결품 {base.after.mean():.4f}h ·"
          f" 이동 {base.km.mean():.1f}km · 초과 {base.over.mean():.2f}대"
          f" · 최장 {base.max_min.mean():.1f}분")

    print("\n순회 항 (γ별):")
    print(f"{'γ':>7} {'결품h':>9} {'이동km':>9} {'초과':>7} {'최장분':>8}"
          f" {'결품승':>8} {'초과승':>8}")
    key = ["duration", "seed"]
    for gamma, g in df[df["항"] == "순회"].groupby("gamma"):
        # 같은 (회차, 씨앗)끼리 짝지어 센다 — 평균만 보면 한 조합에 끌려간다.
        merged = g.set_index(key)[["after", "over"]].join(
            base.set_index(key)[["after", "over"]], rsuffix="_base")
        결품승 = int((merged["after"] < merged["after_base"]).sum())
        초과승 = int((merged["over"] <= merged["over_base"]).sum())
        print(f"{gamma:>7.0f} {g.after.mean():>9.4f} {g.km.mean():>9.1f}"
              f" {g.over.mean():>7.2f} {g.max_min.mean():>8.1f}"
              f" {결품승:>5}/{len(merged)} {초과승:>5}/{len(merged)}")

    print("\n판정")
    tours = df[df["항"] == "순회"]
    best = tours.groupby("gamma").after.mean().idxmin()
    cand = tours[tours.gamma == best]
    결품 = cand.after.mean() - base.after.mean()
    초과 = cand.over.mean() - base.over.mean()
    최장 = cand.max_min.mean() - base.max_min.mean()
    print(f"  가장 나은 순회 설정 γ={best:.0f}:"
          f" 결품 {결품:+.4f}h · 초과 {초과:+.2f}대 · 최장 {최장:+.1f}분")
    if 결품 < 0 and 초과 <= 0:
        print("  ✅ 결품과 예산 초과가 **함께** 나아졌다 — 채택 후보다.")
    elif 결품 < 0:
        print(f"  🔴 결품은 나아지지만 **예산 초과가 {초과:+.2f}대**다 —"
              " 1.21.6과 같은 맞바꿈이다. 판정 규칙상 채택하지 않는다.")
    else:
        print("  ❌ 결품이 나아지지 않는다 — 채택하지 않는다.")
    print("=" * 92)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="군집 목적함수의 거리 항을 순회거리로 (TODO 대기-0B)")
    parser.add_argument("--period", default="25년 11월")
    parser.add_argument("--run-label", default="2026-08-11 real")
    parser.add_argument("--duration", default="_05_10,_10_15,_15_20")
    parser.add_argument("--seeds", default="42,7,13")
    parser.add_argument("--gammas", default=",".join(str(g) for g in GAMMA_TOUR))
    parser.add_argument("--warmup-days", type=int, default=14)
    parser.add_argument("--out", default="")
    args, _ = parser.parse_known_args()

    args.durations = [d.strip() for d in args.duration.split(",") if d.strip()]
    args.seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    gammas = [float(g) for g in args.gammas.split(",") if g.strip()]

    print(f"거리 항 2종 · γ {gammas} · 회차 {len(args.durations)}"
          f" · 씨앗 {len(args.seeds)}  (스냅샷 '{args.run_label}', {args.period})")
    print("🔴 중립 모집단(전체 대여소)으로 잽니다.\n")

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        bc = load_baseline()
        step1 = bc.load_step1()
        from pipeline.step1_cluster import adjust_module

    rows = []
    with contextlib.redirect_stdout(buf):
        rows += measure(bc, step1, args, tour=False, gamma=3000.0)
    print("  현행(메도이드) 완료", flush=True)

    original = patch_tour_term(adjust_module, step1)
    try:
        for gamma in gammas:
            with contextlib.redirect_stdout(buf):
                rows += measure(bc, step1, args, tour=True, gamma=gamma)
            print(f"  순회 γ={gamma:.0f} 완료", flush=True)
    finally:
        adjust_module._cluster_terms = original      # 반드시 되돌린다

    df = pd.DataFrame(rows)
    report(df)

    if args.out:
        out = Path(args.out)
        if not out.is_absolute():
            out = ROOT / out
        df.to_csv(out, index=False)
        print(f"\n결과를 저장했습니다: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
