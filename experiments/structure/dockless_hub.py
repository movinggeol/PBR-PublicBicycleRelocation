"""도크리스(GPS 기반)로 가면 재배치의 단위는 무엇이 되나 — **거점 후보를 잰다**.

## 왜 묻나

대전시 「2025 도시교통정비 중기계획」은 타슈를 **도크리스로 병행 전환**하는 사업을
편성했다(3-5-1, QR단말기 2,500대·6.0억, 2019~2025). 같은 계획이 기술 과제로
**자전거 트래킹(GPS·RFID)** 을, 문제점으로 *"반납 위치 부정확"* 을 적고, 부록 자문의견
(581쪽)은 못을 박는다 — *"도크리스 변화를 통해 … **가상대여소의 위치 선정에 대한
분석이 필요**함"*. 도크리스에서는 고정 대여소가 사라지므로 본 연구의 재배치 파이프라인이
서는 **단위**(어디의 재고를 세고, 차가 어디를 방문하나)가 비어 버린다.

사용자 제안은 *"자전거 자체 GPS만 있다면 대여·반납이 잦은 곳을 거점으로 삼는다"* 이다.
이 스크립트는 그 전제가 자료에서 성립하는지만 본다 — **거점 방식을 구현하지 않는다.**

## 무엇을 묻나

- Q1 **집중도**: 지점을 통행 끝점 수(대여 + 반납)로 줄 세우면 상위 K개가 전체의 몇 %를 덮나.
- Q2 **포함률**: 정본 실행(`2026-08-11 real`)의 회차별 재배치 후보(`|rebal_qty| > 2`)가
  상위 K 거점 안에 얼마나 들어오나. 거점만 다뤄도 지금 하는 일을 덮는지 본다.
- Q3 **안정성**: 달마다 상위 K 집합이 얼마나 겹치나(자카드). 거점을 지난 자료로 정해
  다음 달에 그대로 쓸 수 있어야 방식이 성립한다.

## 판정 — **자료를 보기 전에 정한다** (2026-09-16 등록)

```text
① 상위 K가 전 지점의 30% 이하로 통행 끝점의 80% 이상을 덮는다
② 그 거점 집합이 정본 회차 후보의 70% 이상을 포함한다 (세 회차 모두)
③ 달 사이 상위 K 집합의 자카드가 0.7 이상 (인접 달 쌍의 최솟값 기준)
```

셋 다 통과하면 *"거점 기반 재배치가 도크리스 전환에서도 같은 파이프라인으로 성립할
여지가 있다"* 까지 적는다. 하나라도 미달이면 **미달 항목을 한계로 적는다** — 기준을
고치지 않는다.

🔴 **이 측정의 한계를 먼저 못박는다.** `rental_history`의 좌표는 **대여소 좌표**다
(타슈는 지금 도크 기반이다). 그러므로 이것은 *도크리스에서 자전거가 실제로 어디에
버려지는지*를 관측한 것이 **아니고**, **수요가 얼마나 모여 있는지의 상한**이다. 도크리스가
되면 같은 수요가 더 흩어지므로 커버리지는 **이 값보다 나빠질 수밖에 없다.** 이 수치를
*"도크리스에서도 K개면 된다"* 로 읽으면 안 된다.

> 재현: `python experiments/structure/dockless_hub.py`
> 기간을 바꾸려면 `--periods "25년 10월,25년 11월,26년 03월"`.

---

## 2차 — 거점 기준을 **순수요 편향**으로 바꾼다 (2026-09-16 등록, 36장 후속)

1차(이용량 기준)는 ①②가 미달이었다. 그 미달이 *"거점 방식이 안 된다"* 가 아니라 *"거점을
이용량으로 고르면 안 된다"* 를 뜻한다는 것이 1차의 결론이므로, 기준만 갈아끼워 다시 잰다.

**점수 정의 (결과를 보기 전에 정한다).** 각 지점의 회차별 평균 순수요 `μ`를 **평일만** 모아
구하고, 네 회차 중 **절댓값이 가장 큰 것**을 그 지점의 점수로 쓴다 — 하루 중 한 회차라도
한쪽으로 크게 쏠리면 거점이 있어야 하기 때문이다. 동점은 이용량으로 가른다.
`--criterion demand`가 이 점수를, `--criterion usage`가 1차의 이용량을 쓴다.

```text
① 같은 예산(1차와 같은 K = 커버리지 80%의 543곳)에서 포함률이 세 회차 모두 70% 이상
①-b 표본 밖: 정본이 쓰는 기간(25년 11월)을 뺀 11개월로 거점을 뽑아도 세 회차 모두 70% 이상
② 포함률 90%를 전 지점의 30% 이하(422곳)로 달성한다
③ 인접 달 상위 K 집합의 자카드가 0.70 이상 (최솟값)
④ 그 거점이 통행 끝점의 50% 이상을 덮는다
```

①③④를 통과하면 *"거점 기준을 순수요 편향으로 두면 같은 파이프라인이 도크리스에서도 선다"*
까지 적는다. ②는 설계용 숫자다(거점을 몇 개 둬야 하나). 미달 항목은 한계로 적고 기준은 고치지 않는다.

🔴 **순환성을 미리 밝힌다.** 재배치 후보(`rebal_qty`)도 순수요에서 나오므로, 편향 기준의 포함률이
높게 나오는 것은 **부분적으로 구조적**이다. 그래서 ①-b(정본이 쓰는 달을 뺀 표본 밖)와 ④(거점이
실제 통행도 덮는가)를 함께 본다. ①만 통과하고 ①-b가 미달이면 *"같은 달 안에서만 맞는다"* 로 적는다.

> 재현: `python experiments/structure/dockless_hub.py --criterion demand`
> 표본 밖: `python experiments/structure/dockless_hub.py --criterion demand --exclude-period "25년 11월"`

📌 1차에서 `[Q4·사후]`로 찍던 *"후보의 90%를 덮는 K"* 는 두 기준을 견주려고 2차에서 `[Q2]` 줄로
옮겼다(값의 뜻은 같다). 1차의 판정값(①②③)은 그대로 재현된다.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import project_config  # noqa: F401  (cp949 콘솔에서 '—'·이모지가 죽지 않게 먼저 거친다)
import pandas as pd

import db

CANON_LABEL = "2026-08-11 real"
COVER_TARGETS = (0.80, 0.90)

# 사전 등록 기준 (위 docstring과 같은 값 — 여기서만 고친다)
CRIT_SHARE_OF_SITES = 0.30      # ① 거점 수 / 전 지점 수
CRIT_COVERAGE = 0.80            # ① 통행 끝점 커버리지
CRIT_CANDIDATE_INCLUSION = 0.70  # ② 후보 포함률 (회차마다)
CRIT_JACCARD = 0.70            # ③ 달 사이 상위 K 집합 겹침
CRIT_HUB_COVERAGE = 0.50       # ④ (2차) 편향 기준 거점이 덮어야 할 통행 끝점 비율


def endpoint_counts(conn, periods: list) -> pd.Series:
    """지점별 통행 끝점 수 = 대여 건수 + 반납 건수."""
    marks = ",".join("?" * len(periods))
    rent = pd.read_sql(
        f"SELECT rent_station AS site, COUNT(*) AS n FROM rental_history"
        f" WHERE period IN ({marks}) GROUP BY rent_station", conn, params=periods)
    ret = pd.read_sql(
        f"SELECT return_station AS site, COUNT(*) AS n FROM rental_history"
        f" WHERE period IN ({marks}) GROUP BY return_station", conn, params=periods)
    both = pd.concat([rent, ret]).groupby("site")["n"].sum()
    return both[both.index.notna()].sort_values(ascending=False)


DURATION_HOURS = {
    "_05_10": [5, 6, 7, 8, 9],
    "_10_15": [10, 11, 12, 13, 14],
    "_15_20": [15, 16, 17, 18, 19],
    "_20_05": [20, 21, 22, 23, 0, 1, 2, 3, 4],
}


def demand_scores(conn, periods: list) -> pd.Series:
    """지점별 **순수요 편향** 점수 = 회차별 평균 순수요의 절댓값 중 최댓값 (평일만).

    하루 중 한 회차라도 한쪽으로 크게 쏠리면 거점이 있어야 한다는 뜻이다. 회차를 합치면
    아침에 비고 저녁에 차는 곳이 상쇄돼 0으로 보인다 — 그것이 1차에서 이용량 기준이
    놓친 것과 같은 함정이다.
    """
    marks = ",".join("?" * len(periods))
    cols = ", ".join(f"net_{h:02d}" for h in range(24))
    frame = pd.read_sql(
        f"SELECT station_id, date, {cols} FROM net_demand"
        f" WHERE period IN ({marks})", conn, params=periods)
    weekday = ~project_config.holiday_mask(pd.to_datetime(frame["date"]))
    frame = frame[weekday.to_numpy()]
    scores = None
    for duration, hours in DURATION_HOURS.items():
        part = frame[[f"net_{h:02d}" for h in hours]].sum(axis=1)
        mu = part.groupby(frame["station_id"]).mean().abs()
        scores = mu if scores is None else pd.concat([scores, mu], axis=1).max(axis=1)
    return scores.sort_values(ascending=False)


def rank_sites(counts: pd.Series, scores: pd.Series | None) -> pd.Series:
    """거점 순위를 정한다. 편향 기준이면 동점은 이용량으로 가른다."""
    if scores is None:
        return counts
    frame = pd.DataFrame({"score": scores}).join(
        counts.rename("usage"), how="outer").fillna(0.0)
    frame = frame.sort_values(["score", "usage"], ascending=False)
    return frame["score"]


def inclusion_at(cand: set, order: list, k: int) -> float:
    hubs = set(order[:k])
    return len(cand & hubs) / len(cand) if cand else 0.0


def k_for_inclusion(cand: set, order: list, target: float) -> int:
    """후보의 target 비율을 덮는 데 필요한 K (순위를 따라 걸어간다)."""
    need = target * len(cand)
    hit = 0
    for i, site in enumerate(order, start=1):
        if site in cand:
            hit += 1
            if hit >= need:
                return i
    return len(order)


def coverage_curve(counts: pd.Series) -> pd.DataFrame:
    """상위 K개까지의 누적 커버리지."""
    total = counts.sum()
    frame = counts.reset_index()
    frame.columns = ["site", "n"]
    frame["누적"] = frame["n"].cumsum() / total
    frame["K"] = range(1, len(frame) + 1)
    return frame


def k_for(frame: pd.DataFrame, target: float) -> int:
    """커버리지 target을 처음 넘는 K."""
    hit = frame.index[frame["누적"] >= target]
    return int(frame.loc[hit[0], "K"]) if len(hit) else len(frame)


def candidates(conn, label: str = CANON_LABEL) -> dict:
    """정본 실행의 회차별 재배치 후보 대여소."""
    frame = pd.read_sql(
        "SELECT duration, station_id, rebal_qty FROM rebalance_plan"
        " WHERE run_label = ?", conn, params=(label,))
    out = {}
    for duration, part in frame.groupby("duration"):
        keep = part[part["rebal_qty"].abs() > project_config.REBAL_MIN_QTY]
        out[duration] = set(keep["station_id"])
    return out


def jaccard(left: set, right: set) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--periods", default=None,
                        help="쉼표로 구분한 기간 (생략하면 DB에 있는 전부)")
    parser.add_argument("--criterion", choices=("usage", "demand"), default="usage",
                        help="거점 선정 기준 — usage: 이용량(1차) · demand: 순수요 편향(2차)")
    parser.add_argument("--exclude-period", default=None,
                        help="거점 점수에서 뺄 기간 (①-b 표본 밖 검사: 정본이 쓰는 '25년 11월')")
    args = parser.parse_args(argv)

    with db.session() as conn:
        if args.periods:
            periods = [p.strip() for p in args.periods.split(",") if p.strip()]
        else:
            periods = [r[0] for r in conn.execute(
                "SELECT DISTINCT period FROM rental_history ORDER BY period")]
        print(f"기간 {len(periods)}개: {', '.join(periods)}")
        print(f"거점 기준: {'순수요 편향(2차)' if args.criterion == 'demand' else '이용량(1차)'}")

        counts = endpoint_counts(conn, periods)
        frame = coverage_curve(counts)
        sites = len(frame)
        print(f"\n지점 {sites:,}곳 · 통행 끝점 {int(counts.sum()):,}건"
              " (대여 + 반납, 한 통행이 둘을 만든다)")

        # 예산 K는 **두 기준에서 같게** 둔다 — 1차의 커버리지 80% 지점(543곳)이다.
        k80 = k_for(frame, 0.80)
        print(f"예산 K = {k80:,}곳 (전 지점의 {k80 / sites:.1%})"
              " — 1차에서 통행 끝점 80%를 덮던 수, 두 기준을 같은 예산으로 견준다")

        scores = None
        if args.criterion == "demand":
            score_periods = [p for p in periods if p != args.exclude_period]
            if args.exclude_period:
                print(f"①-b 표본 밖: 점수에서 '{args.exclude_period}'을 뺐다"
                      f" → {len(score_periods)}개월로 거점을 뽑는다")
            scores = demand_scores(conn, score_periods)
        order = list(rank_sites(counts, scores).index)
        hubs = set(order[:k80])

        rank_of = {site: i for i, site in enumerate(frame["site"], start=1)}
        print(f"\n[Q1] 이 거점 {k80:,}곳이 통행 끝점을 얼마나 덮나")
        covered = counts.reindex(list(hubs)).fillna(0).sum() / counts.sum()
        print(f"  끝점 커버리지 {covered:6.1%}"
              f" · 거점의 이용량 순위 중앙값 {sorted(rank_of.get(s, sites) for s in hubs)[len(hubs) // 2]:,}위")
        if args.criterion == "usage":
            for share in (0.05, 0.10, 0.20, 0.30, 0.50):
                k = max(int(sites * share), 1)
                print(f"  상위 {share:>4.0%} ({k:>4}곳) → {frame.loc[k - 1, '누적']:6.1%}")

        cand = candidates(conn)
        print(f"\n[Q2] 포함률 — 거점 {k80:,}곳이 정본 후보를 얼마나 덮나")
        inclusion, need90 = {}, {}
        for duration, sites_set in sorted(cand.items()):
            if not sites_set:
                continue
            inclusion[duration] = inclusion_at(sites_set, order, k80)
            need90[duration] = k_for_inclusion(sites_set, order, 0.90)
            print(f"  {duration}: 후보 {len(sites_set):>4}곳 중"
                  f" {int(round(inclusion[duration] * len(sites_set))):>4}곳"
                  f" = {inclusion[duration]:6.1%}"
                  f" · 90%를 덮는 K = {need90[duration]:,}곳"
                  f" ({need90[duration] / sites:.1%})")
        if not inclusion:
            print("  ⚠️ 정본 라벨의 rebalance_plan이 이 DB에 없다 — 판정하지 않는다.")

        print(f"\n[Q3] 안정성 — 달마다 같은 기준으로 상위 {k80:,}곳을 다시 뽑는다")
        monthly = {}
        for period in periods:
            if args.criterion == "demand":
                part = demand_scores(conn, [period])
                month_order = list(rank_sites(endpoint_counts(conn, [period]), part).index)
            else:
                month_order = list(endpoint_counts(conn, [period]).index)
            monthly[period] = set(month_order[:k80])
        pairs = []
        for before, after in zip(periods, periods[1:]):
            value = jaccard(monthly[before], monthly[after])
            pairs.append(value)
            print(f"  {before} → {after}: {value:.3f}")
        worst = min(pairs) if pairs else 0.0

        print("\n판정 (사전 등록)")
        if args.criterion == "usage":
            ok1 = (k80 / sites <= CRIT_SHARE_OF_SITES)
            print(f"  ① 상위 K ≤ 전 지점의 {CRIT_SHARE_OF_SITES:.0%}로"
                  f" {CRIT_COVERAGE:.0%} 커버: {'통과' if ok1 else '미달'}"
                  f" (K={k80:,} = {k80 / sites:.1%})")
            checks = [ok1]
        else:
            checks = []
        if inclusion:
            ok2 = all(v >= CRIT_CANDIDATE_INCLUSION for v in inclusion.values())
            low = min(inclusion, key=inclusion.get)
            tag = "①-b 표본 밖 포함률" if args.exclude_period else (
                "① 포함률" if args.criterion == "demand" else "② 후보 포함률")
            print(f"  {tag} ≥ {CRIT_CANDIDATE_INCLUSION:.0%} (세 회차 모두):"
                  f" {'통과' if ok2 else '미달'}"
                  f" (가장 낮은 회차 {low} {inclusion[low]:.1%})")
            checks.append(ok2)
        ok3 = worst >= CRIT_JACCARD
        print(f"  ③ 달 사이 자카드 ≥ {CRIT_JACCARD:.2f}:"
              f" {'통과' if ok3 else '미달'} (최솟값 {worst:.3f})")
        checks.append(ok3)
        if args.criterion == "demand":
            ok4 = covered >= CRIT_HUB_COVERAGE
            print(f"  ④ 거점이 통행 끝점의 {CRIT_HUB_COVERAGE:.0%} 이상 커버:"
                  f" {'통과' if ok4 else '미달'} ({covered:.1%})")
            checks.append(ok4)
            if need90:
                worst_k = max(need90.values())
                ok_design = worst_k / sites <= CRIT_SHARE_OF_SITES
                print(f"  ② 포함률 90%를 전 지점의 {CRIT_SHARE_OF_SITES:.0%} 이하로:"
                      f" {'통과' if ok_design else '미달'}"
                      f" (가장 많이 드는 회차 {worst_k:,}곳 = {worst_k / sites:.1%}) — 설계용 숫자")

        if all(checks):
            print("\n▶ 사전 등록 기준을 모두 통과했다.")
        else:
            print("\n▶ 미달 항목이 있다 — 그 항목을 한계로 적는다. 기준을 고치지 않는다.")
        if args.criterion == "demand":
            print("🔴 순환성 주의 — 후보도 순수요에서 나온다. ①-b(정본이 쓰는 달을 뺀"
                  " 표본 밖)와 ④를 함께 읽어야 한다.")
        print("🔴 어느 쪽이든 이 커버리지는 **상한**이다 — 좌표가 대여소 좌표라"
              " 도크리스의 실제 분산을 관측한 것이 아니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
