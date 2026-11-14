"""19개월 반복 결과를 다른 검정으로 다시 읽는다: 결론이 검정의 선택과 독립 단위에 기대는가.

왜 필요한가
    원고 6.4절 <표 6-4>는 (달, 시드, 시간대)로 짝지은 57쌍에 윌콕슨 부호순위검정을 썼다
    (repeat_eval.py). 두 물음이 열려 있었다(2026-11-14 사용자 질문).
    1) 정규성을 가정하지 않는 다른 검정(부호검정, 부호 뒤집기 순열검정, 부트스트랩 구간)이나
       정규성을 가정하는 대응 t검정으로 재도 같은 결론인가.
    2) 같은 달의 시드 셋은 같은 수요를 공유하므로 서로 독립이 아니다. 독립 단위를 달로 잡아
       시드를 달마다 평균한 19쌍으로 재도 같은가. 57쌍을 독립으로 세면 p가 실제보다 작게 나온다.

무엇을 묻는가
    회차 셋 × 비교 둘(제안 방법 P 대 무재배치 B0, P 대 그리디 B1) × 지표 둘(결품, 포화) = 12개 비교마다
    - 57쌍: 윌콕슨(원고 재현), 대응 t, 부호검정, 부호 뒤집기 순열검정(평균 차, 몬테카를로),
      차이의 정규성(Shapiro-Wilk)과 왜도, Hodges-Lehmann 추정량, 중앙값 차의 부트스트랩 95% 구간
    - 19쌍(시드를 달마다 평균): 윌콕슨(정확 분포), 대응 t, 부호검정, 부호 뒤집기 순열검정(2^19 전부),
      정규성, 달 단위로 다시 뽑는 군집 부트스트랩 95% 구간(중앙값 차)
    차이의 부호는 <표 6-4>와 같다: 대조군 − P이므로 결품에서 양수면 P가 낮고, 포화에서 음수면 P가 길다.

어떻게 판정하는가
    계획을 다시 세우지 않는다. repeat_eval.py가 남긴 원자료(CSV)만 읽고, 먼저 그 자료가 <표 6-4>의
    중앙값 차와 p 열두 쌍을 그대로 재현하는지 확인한다. 하나라도 다르면 멈춘다(다른 실행의 자료다).
    12개 비교를 한꺼번에 보므로 유의수준 5%를 12로 나눈 값(0.0042)도 함께 본다.
    19쌍에서는 모든 달이 같은 방향이어도 양측 p가 2/2^19(약 3.8e-6)보다 작아질 수 없다.

사용법
    python experiments/baseline/repeat_alt_tests.py
    python experiments/baseline/repeat_alt_tests.py --csv experiments/baseline/repeat_19month_on.csv --out data/repeat_alt_tests.csv
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import project_config  # noqa: E402,F401  (콘솔 출력을 UTF-8로 맞춘다)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import stats  # noqa: E402

DEFAULT_CSV = "experiments/baseline/repeat_19month_on.csv"
DURATIONS = ["_05_10", "_10_15", "_15_20"]
OTHERS = ["B0", "B1"]
METRICS = {"stockout_after": "결품", "saturation_after": "포화"}
ALPHA, N_TESTS = 0.05, 12

# 원고 <표 6-4>: (회차, 대조군, 지표) → (중앙값 차, p). 원자료가 이것을 재현해야 이어 간다.
TABLE_6_4 = {
    ("_05_10", "B0", "stockout_after"): (1.693, 5.1e-11), ("_05_10", "B0", "saturation_after"): (-0.207, 5.1e-11),
    ("_05_10", "B1", "stockout_after"): (0.211, 5.4e-11), ("_05_10", "B1", "saturation_after"): (-0.072, 1.3e-10),
    ("_10_15", "B0", "stockout_after"): (1.529, 5.1e-11), ("_10_15", "B0", "saturation_after"): (-0.275, 5.4e-11),
    ("_10_15", "B1", "stockout_after"): (0.305, 5.1e-11), ("_10_15", "B1", "saturation_after"): (-0.029, 2.8e-07),
    ("_15_20", "B0", "stockout_after"): (1.230, 5.1e-11), ("_15_20", "B0", "saturation_after"): (-0.270, 5.1e-11),
    ("_15_20", "B1", "stockout_after"): (0.158, 1.3e-10), ("_15_20", "B1", "saturation_after"): (-0.020, 4.8e-05),
}


def paired(frame, duration, other, column):
    """(달, 시드)로 짝지은 차이 대조군 − P. 행은 달, 열은 시드인 표로 돌려준다."""
    keys = ["period", "seed"]
    part = frame[frame["duration"] == duration]
    a = part[part["method"] == other].set_index(keys)[column]
    b = part[part["method"] == "P"].set_index(keys)[column]
    both = pd.concat([a.rename("a"), b.rename("b")], axis=1).dropna()
    return (both["a"] - both["b"]).unstack("seed")


def sign_test(d):
    pos, neg = int((d > 0).sum()), int((d < 0).sum())
    return pos, neg, stats.binomtest(pos, pos + neg, 0.5).pvalue


def flip_mc(d, rng, draws=200_000, chunk=20_000):
    """부호 뒤집기 순열검정(평균 차, 양측), 몬테카를로. p의 하한은 1/(draws+1)이다."""
    obs, hits = abs(d.mean()), 0
    for start in range(0, draws, chunk):
        size = min(chunk, draws - start)
        flips = rng.choice(np.array([-1.0, 1.0]), size=(size, d.size))
        hits += int((np.abs(flips @ d) / d.size >= obs - 1e-12).sum())
    return (hits + 1) / (draws + 1)


def flip_exact(d, chunk=65_536):
    """부호 뒤집기 순열검정(평균 차, 양측), 2^n 경우를 모두 센다(n=19면 524,288)."""
    n, obs, hits = d.size, abs(d.mean()), 0
    shifts = np.arange(n)
    for start in range(0, 2 ** n, chunk):
        idx = np.arange(start, min(start + chunk, 2 ** n))[:, None]
        signs = ((idx >> shifts) & 1) * 2.0 - 1.0
        hits += int((np.abs(signs @ d) / n >= obs - 1e-12).sum())
    return hits / 2 ** n


def hodges_lehmann(d):
    walsh = (d[:, None] + d[None, :]) / 2
    return float(np.median(walsh[np.triu_indices(d.size)]))


def boot_iid(d, rng, draws=10_000):
    idx = rng.integers(0, d.size, size=(draws, d.size))
    return np.percentile(np.median(d[idx], axis=1), [2.5, 97.5])


def boot_cluster(table, rng, draws=10_000):
    """달을 다시 뽑고 그 달의 시드 셋을 함께 넣는다(같은 달의 시드는 독립이 아니다)."""
    mat = table.to_numpy()
    idx = rng.integers(0, mat.shape[0], size=(draws, mat.shape[0]))
    pooled = mat[idx].reshape(draws, -1)
    return np.percentile(np.nanmedian(pooled, axis=1), [2.5, 97.5])


def analyse(frame, rng):
    rows = []
    for column, label in METRICS.items():
        for duration in DURATIONS:
            for other in OTHERS:
                table = paired(frame, duration, other, column)
                d = table.to_numpy().ravel()
                d = d[~np.isnan(d)]
                dm = table.mean(axis=1).to_numpy()
                exp_med, exp_p = TABLE_6_4[(duration, other, column)]
                w_p = stats.wilcoxon(d).pvalue
                if abs(np.median(d) - exp_med) > 6e-4 or f"{w_p:.1e}" != f"{exp_p:.1e}":
                    sys.exit(f"[멈춤] 원자료가 <표 6-4>를 재현하지 않습니다: {duration} {other} {label} "
                             f"중앙값 차 {np.median(d):+.3f}(표 {exp_med:+.3f}), p {w_p:.1e}(표 {exp_p:.1e})")
                pos, neg, s_p = sign_test(d)
                mpos, mneg, ms_p = sign_test(dm)
                lo, hi = boot_iid(d, rng)
                clo, chi = boot_cluster(table, rng)
                rows.append({
                    "지표": label, "회차": duration, "비교": f"P 대 {other}",
                    "짝": d.size, "달": dm.size,
                    "중앙값차": float(np.median(d)), "HL": hodges_lehmann(d), "평균차": float(d.mean()),
                    "P쪽_짝": pos if column == "stockout_after" else neg,
                    "반대_짝": neg if column == "stockout_after" else pos,
                    # 대응 t검정은 짝의 차이에 대한 일표본 t검정과 같다
                    "윌콕슨57": w_p, "t57": stats.ttest_1samp(d, 0.0).pvalue,
                    "부호57": s_p, "순열57": flip_mc(d, rng),
                    "정규성57": stats.shapiro(d).pvalue, "왜도57": float(stats.skew(d)),
                    "구간57": (float(lo), float(hi)),
                    "같은방향_달": mpos if column == "stockout_after" else mneg,
                    "반대_달": mneg if column == "stockout_after" else mpos,
                    "윌콕슨19": stats.wilcoxon(dm).pvalue, "t19": stats.ttest_1samp(dm, 0.0).pvalue,
                    "부호19": ms_p, "순열19": flip_exact(dm),
                    "정규성19": stats.shapiro(dm).pvalue,
                    "군집구간": (float(clo), float(chi)),
                })
    return pd.DataFrame(rows)


def fmt_p(p):
    return f"{p:.1e}" if p < 1e-3 else f"{p:.4f}"


def report(res):
    cut = ALPHA / N_TESTS
    print("원자료가 <표 6-4>의 중앙값 차와 p 열두 쌍을 모두 재현한다.\n")
    for label in METRICS.values():
        part = res[res["지표"] == label]
        print(f"## {label}: 57쌍(달 × 시드)\n")
        print("| 회차 | 비교 | 중앙값 차 | HL | P 쪽/반대 짝 | 윌콕슨 | 대응 t | 부호검정 | 순열(평균) | 정규성 p | 왜도 | 중앙값 차 95% 구간 |")
        print("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
        for _, r in part.iterrows():
            print(f"| {r['회차']} | {r['비교']} | {r['중앙값차']:+.3f} | {r['HL']:+.3f} | {r['P쪽_짝']}/{r['반대_짝']} "
                  f"| {fmt_p(r['윌콕슨57'])} | {fmt_p(r['t57'])} | {fmt_p(r['부호57'])} | {fmt_p(r['순열57'])} "
                  f"| {fmt_p(r['정규성57'])} | {r['왜도57']:+.2f} | {r['구간57'][0]:+.3f} ~ {r['구간57'][1]:+.3f} |")
        print(f"\n## {label}: 19쌍(시드를 달마다 평균)\n")
        print("| 회차 | 비교 | 같은 방향/반대 달 | 윌콕슨(정확) | 대응 t | 부호검정 | 순열(정확) | 정규성 p | 군집 부트스트랩 95% 구간 |")
        print("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
        for _, r in part.iterrows():
            print(f"| {r['회차']} | {r['비교']} | {r['같은방향_달']}/{r['반대_달']} | {fmt_p(r['윌콕슨19'])} "
                  f"| {fmt_p(r['t19'])} | {fmt_p(r['부호19'])} | {fmt_p(r['순열19'])} | {fmt_p(r['정규성19'])} "
                  f"| {r['군집구간'][0]:+.3f} ~ {r['군집구간'][1]:+.3f} |")
        print()
    print(f"## 판정 (5% 기준과, 12개 비교를 보정한 {cut:.4f} 기준)\n")
    for col, name in [("윌콕슨57", "윌콕슨 57쌍"), ("t57", "대응 t 57쌍"), ("부호57", "부호검정 57쌍"),
                      ("순열57", "순열 57쌍"), ("윌콕슨19", "윌콕슨 19쌍"), ("t19", "대응 t 19쌍"),
                      ("부호19", "부호검정 19쌍"), ("순열19", "순열 19쌍")]:
        print(f"- {name}: 5% 기준 {int((res[col] < ALPHA).sum())}/12, 보정 기준 {int((res[col] < cut).sum())}/12, "
              f"가장 큰 p {fmt_p(res[col].max())}")
    zero_out = sum(1 for _, r in res.iterrows() if r["군집구간"][0] <= 0 <= r["군집구간"][1])
    print(f"- 군집 부트스트랩 95% 구간이 0을 포함한 비교: {zero_out}/12")
    print(f"- 정규성(Shapiro-Wilk)이 5%에서 기각된 비교: 57쌍 {int((res['정규성57'] < ALPHA).sum())}/12, "
          f"19쌍 {int((res['정규성19'] < ALPHA).sum())}/12")


def main():
    parser = argparse.ArgumentParser(description="19개월 반복 결과의 대안 검정과 달 단위 검정")
    parser.add_argument("--csv", default=DEFAULT_CSV, help="repeat_eval.py --out으로 남긴 원자료")
    parser.add_argument("--seed", type=int, default=20261114, help="순열 · 부트스트랩의 난수 시드")
    parser.add_argument("--out", default="", help="비교별 결과를 CSV로 저장할 경로")
    args = parser.parse_args()

    path = Path(args.csv)
    if not path.exists():
        sys.exit(f"[멈춤] 원자료가 없습니다: {path} (부록 A의 6.4 행 명령으로 만든다)")
    frame = pd.read_csv(path)
    frame = frame[frame["method"].isin(["P", *OTHERS])]
    res = analyse(frame, np.random.default_rng(args.seed))
    report(res)
    if args.out:
        res.to_csv(args.out, index=False, encoding="utf-8-sig")
        print(f"\n저장: {args.out}")


if __name__ == "__main__":
    main()
