"""반복 실행 — 결과를 하나의 숫자가 아니라 분포로 낸다.

지금까지 README와 문서의 모든 수치는 **한 달·한 실행**의 결과였다. 그런데 우리는
이미 이렇게 적어 두었다(docs/RETROSPECTIVE.md 4장 ②):

    greedy 탐색이라 단일 실행 결과가 흔들린다. 단일 실행으로 판단하면 안 된다.

이 규칙을 파라미터 실험에는 적용했는데 정작 최종 결과표에는 적용하지 않았다.
이 스크립트가 여러 달 × 여러 씨앗으로 같은 비교를 반복해 **평균 ± 표준편차**와
대조군 대비 개선의 **유의성**을 낸다.

계획 단위는 baseline_compare.py 그대로다 — 여기서 다시 계산하는 것은 없다.

사용법:
    python experiments/repeat_eval.py --periods "25년 09월,25년 10월,25년 11월"
    python experiments/repeat_eval.py --periods "25년 11월" --seeds 42,7,13,99
    python experiments/repeat_eval.py --methods P,B0,B1 --duration "_05_10"

검정: 같은 (기간, 씨앗, 시간대)에서 두 방법을 짝지어 Wilcoxon 부호순위 검정을 한다.
정규성을 가정하지 않고 표본이 적어도 쓸 수 있다. 표본이 5쌍 미만이면 검정을
건너뛴다 — 그 수로는 어떤 검정도 의미가 없다.
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import baseline_compare as bc  # noqa: E402


def collect(args):
    """기간 × 씨앗을 돌며 baseline_compare의 계획·평가를 그대로 반복한다."""
    step1 = bc.load_step1()
    import pulp
    solver = bc.ilp_mod.build_solver()   # 파이프라인과 같은 솔버 설정

    rows = []
    total = len(args.periods) * len(args.seeds)
    done = 0
    for period in args.periods:
        for seed in args.seeds:
            done += 1
            print(f"\n### [{done}/{total}] 기간 {period} · 씨앗 {seed}")
            try:
                net, st_info, warmup = bc.load_inputs(
                    period, args.run_label, args.day_type,
                    args.warmup_days, args.warmup_period)
            except SystemExit as err:
                print(f"[건너뜀] {period}: {err}")
                continue

            run_args = argparse.Namespace(
                period=period, seed=seed, day_type=args.day_type,
                warmup_days=args.warmup_days, methods=args.methods,
                plan_basis=False)

            for duration in args.durations:
                rows.extend(bc.run_duration(net, st_info, warmup, duration,
                                            run_args, step1, solver))
    return pd.DataFrame(rows)


def summarize(frame, reference="B0"):
    """방법·시간대별 평균 ± 표준편차. 편익은 무재배치 대비 결품 감소로 잰다."""
    base = (frame[frame["method"] == reference]
            .set_index(["period", "seed", "duration"])["stockout_after"])
    frame = frame.copy()
    frame["benefit"] = frame.apply(
        lambda r: base.get((r["period"], r["seed"], r["duration"]), float("nan"))
        - r["stockout_after"], axis=1)

    summary = (frame.groupby(["duration", "method"])
               .agg(n=("stockout_after", "size"),
                    결품_평균=("stockout_after", "mean"),
                    결품_표준편차=("stockout_after", "std"),
                    감소_평균=("benefit", "mean"),
                    감소_표준편차=("benefit", "std"),
                    처리대수=("bikes", "mean"),
                    이동km=("km", "mean"),
                    최장분=("max_min", "mean"),
                    초과=("over", "mean"))
               .reset_index())
    return frame, summary


def wilcoxon(frame, left, right):
    """두 방법의 결품 시간을 짝지어 비교한다 (같은 기간·씨앗·시간대)."""
    try:
        from scipy.stats import wilcoxon as test
    except ImportError:
        return None

    keys = ["period", "seed", "duration"]
    a = frame[frame["method"] == left].set_index(keys)["stockout_after"]
    b = frame[frame["method"] == right].set_index(keys)["stockout_after"]
    paired = pd.concat([a.rename("a"), b.rename("b")], axis=1).dropna()
    if len(paired) < 5:
        return {"n": len(paired), "p": None}
    if (paired["a"] - paired["b"]).abs().sum() == 0:
        return {"n": len(paired), "p": None, "identical": True}

    stat, p = test(paired["a"], paired["b"])
    return {"n": len(paired), "p": float(p),
            "median_diff": float((paired["a"] - paired["b"]).median())}


def show(frame, summary, args):
    print("\n" + "=" * 100)
    print(f"반복 실행 요약 — 기간 {len(args.periods)}개 × 씨앗 {len(args.seeds)}개"
          f" ({args.day_type})")
    print("=" * 100)

    for duration, part in summary.groupby("duration", sort=False):
        print(f"\n[{duration}]  결품 시간 (대여소·일 평균) — 낮을수록 좋다")
        print(f"{'방법':<34}{'n':>4}{'결품h 평균±표준편차':>22}"
              f"{'감소 평균±표준편차':>22}{'처리':>7}{'이동km':>9}{'최장분':>8}")
        for row in part.itertuples():
            label = bc.LABELS.get(row.method, row.method)
            결품 = f"{row.결품_평균:.2f} ± {0 if pd.isna(row.결품_표준편차) else row.결품_표준편차:.2f}"
            감소 = f"{row.감소_평균:.2f} ± {0 if pd.isna(row.감소_표준편차) else row.감소_표준편차:.2f}"
            print(f"{label:<34}{row.n:>4d}{결품:>22}{감소:>22}"
                  f"{row.처리대수:>7.0f}{row.이동km:>9.1f}{row.최장분:>8.1f}")

    if "P" in args.methods:
        print("\n" + "-" * 100)
        print("유의성 검정 (Wilcoxon 부호순위, 짝은 같은 기간·씨앗·시간대의 결품 시간)")
        print("-" * 100)
        for other in [m for m in args.methods if m != "P"]:
            result = wilcoxon(frame, other, "P")
            label = bc.LABELS.get(other, other)
            if result is None:
                print(f"  {label:<34} scipy가 없어 건너뜀")
            elif result.get("identical"):
                print(f"  {label:<34} n={result['n']:<3d} 두 방법의 결과가 완전히 같다")
            elif result["p"] is None:
                print(f"  {label:<34} n={result['n']:<3d} 표본이 5쌍 미만이라 검정 생략")
            else:
                mark = "유의" if result["p"] < 0.05 else "유의하지 않음"
                print(f"  {label:<34} n={result['n']:<3d}"
                      f" 중앙값 차 {result['median_diff']:+.2f}h"
                      f"  p = {result['p']:.4f}  ({mark}, α=0.05)")

    print("\n" + "=" * 100)
    print("읽는 법")
    print("  · 표준편차가 크면 그 방법은 달·씨앗에 따라 결과가 흔들린다는 뜻이다.")
    print("  · 감소 = 무재배치(B0) 대비 줄인 결품 시간. 이것이 재배치의 편익이다.")
    print("  · 시간대를 섞어 하나의 평균으로 내지 마라 — 수요 구조가 반대다.")
    print("  · 검정은 '차이가 있다'만 말해 준다. 차이의 크기는 감소 평균으로 읽어라.")
    print("=" * 100)


def main():
    parser = argparse.ArgumentParser(description="반복 실행·통계")
    parser.add_argument("--periods", default="25년 09월,25년 10월,25년 11월",
                        help="순수요 기간 목록 (콤마 구분)")
    parser.add_argument("--seeds", default="42", help="K-Medoids 씨앗 목록 (콤마 구분)")
    parser.add_argument("--duration", default="_05_10,_10_15,_15_20")
    parser.add_argument("--day-type", default="weekday", choices=["weekday", "holiday"])
    parser.add_argument("--methods", default="P,B0,B1")
    parser.add_argument("--run-label", default="")
    parser.add_argument("--warmup-period", default="")
    parser.add_argument("--warmup-days", type=int, default=0)
    parser.add_argument("--out", default="", help="원본 결과를 CSV로 저장할 경로")
    args, _ = parser.parse_known_args()

    args.periods = [p.strip() for p in args.periods.split(",") if p.strip()]
    args.seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    args.durations = [d.strip() for d in args.duration.split(",") if d.strip()]
    args.methods = [m.strip().upper() for m in args.methods.split(",") if m.strip()]
    args.day_type = bc.normalize_day_type(args.day_type)

    unknown = [m for m in args.methods if m not in bc.METHODS]
    if unknown:
        raise SystemExit(f"알 수 없는 방법: {unknown} (가능: {', '.join(bc.METHODS)})")
    if "B0" not in args.methods:
        raise SystemExit("B0(무재배치)이 있어야 편익을 잴 수 있습니다 — --methods에 넣으세요.")

    frame = collect(args)
    if frame.empty:
        raise SystemExit("결과가 없습니다.")

    frame, summary = summarize(frame)
    show(frame, summary, args)

    if args.out:
        frame.to_csv(args.out, index=False, encoding="utf-8")
        print(f"\n원본 결과를 저장했습니다: {args.out}")


if __name__ == "__main__":
    main()
