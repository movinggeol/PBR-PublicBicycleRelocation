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
    args = parser.parse_args(argv)

    with db.session() as conn:
        if args.periods:
            periods = [p.strip() for p in args.periods.split(",") if p.strip()]
        else:
            periods = [r[0] for r in conn.execute(
                "SELECT DISTINCT period FROM rental_history ORDER BY period")]
        print(f"기간 {len(periods)}개: {', '.join(periods)}")

        counts = endpoint_counts(conn, periods)
        frame = coverage_curve(counts)
        sites = len(frame)
        print(f"\n지점 {sites:,}곳 · 통행 끝점 {int(counts.sum()):,}건"
              " (대여 + 반납, 한 통행이 둘을 만든다)")

        print("\n[Q1] 집중도 — 상위 K가 덮는 비율")
        for share in (0.05, 0.10, 0.20, 0.30, 0.50):
            k = max(int(sites * share), 1)
            print(f"  상위 {share:>4.0%} ({k:>4}곳) → {frame.loc[k - 1, '누적']:6.1%}")
        ks = {t: k_for(frame, t) for t in COVER_TARGETS}
        for target, k in ks.items():
            print(f"  커버리지 {target:.0%}에 필요한 K = {k:,}곳"
                  f" (전 지점의 {k / sites:.1%})")

        k80 = ks[0.80]
        hubs = set(frame.loc[:k80 - 1, "site"])

        print(f"\n[Q2] 포함률 — 상위 {k80:,}곳(커버리지 80%)이 정본 후보를 얼마나 덮나")
        inclusion = {}
        for duration, cand in sorted(candidates(conn).items()):
            if not cand:
                continue
            rate = len(cand & hubs) / len(cand)
            inclusion[duration] = rate
            print(f"  {duration}: 후보 {len(cand):>4}곳 중 {len(cand & hubs):>4}곳"
                  f" = {rate:6.1%}")
        if not inclusion:
            print("  ⚠️ 정본 라벨의 rebalance_plan이 이 DB에 없다 — Q2는 판정하지 않는다.")

        print(f"\n[Q3] 안정성 — 달마다 상위 {k80:,}곳을 다시 뽑아 겹침을 본다")
        monthly = {}
        for period in periods:
            part = endpoint_counts(conn, [period])
            monthly[period] = set(part.index[:k80])
        pairs = []
        for before, after in zip(periods, periods[1:]):
            value = jaccard(monthly[before], monthly[after])
            pairs.append(value)
            print(f"  {before} → {after}: {value:.3f}")
        worst = min(pairs) if pairs else 0.0

        # ── 사후 탐색 (2026-09-16에 결과를 본 뒤 덧붙였다 — 사전 등록이 아니다) ──
        # ②가 미달이라 "그럼 거점을 몇 개 둬야 후보를 덮나"가 바로 따라온다.
        # 이 값은 가설 검정이 아니라 설계용 숫자이므로 판정에 넣지 않는다.
        print("\n[Q4·사후] 이용량 순위로 거점을 늘려 후보를 덮으려면 (사전 등록 아님)")
        rank_of = {site: i for i, site in enumerate(frame["site"], start=1)}
        for duration, cand in sorted(candidates(conn).items()):
            if not cand:
                continue
            ranks = sorted(rank_of.get(s, sites) for s in cand)
            need90 = ranks[int(len(ranks) * 0.90) - 1]
            median = ranks[len(ranks) // 2]
            print(f"  {duration}: 후보의 90%를 덮는 K = {need90:,}곳"
                  f" (전 지점의 {need90 / sites:.1%}) · 후보 이용량 순위 중앙값 {median:,}위"
                  f" / {sites:,}곳")

        print("\n판정 (사전 등록)")
        ok1 = (k80 / sites <= CRIT_SHARE_OF_SITES)
        print(f"  ① 상위 K ≤ 전 지점의 {CRIT_SHARE_OF_SITES:.0%}로 {CRIT_COVERAGE:.0%} 커버:"
              f" {'통과' if ok1 else '미달'} (K={k80:,} = {k80 / sites:.1%})")
        if inclusion:
            ok2 = all(v >= CRIT_CANDIDATE_INCLUSION for v in inclusion.values())
            worst_dur = min(inclusion, key=inclusion.get)
            print(f"  ② 후보 포함률 ≥ {CRIT_CANDIDATE_INCLUSION:.0%} (세 회차 모두):"
                  f" {'통과' if ok2 else '미달'}"
                  f" (가장 낮은 회차 {worst_dur} {inclusion[worst_dur]:.1%})")
        else:
            ok2 = None
        ok3 = worst >= CRIT_JACCARD
        print(f"  ③ 달 사이 자카드 ≥ {CRIT_JACCARD:.2f}:"
              f" {'통과' if ok3 else '미달'} (최솟값 {worst:.3f})")

        passed = [ok for ok in (ok1, ok2, ok3) if ok is not None]
        if all(passed):
            print("\n▶ 셋 다 통과 — '거점 기반 재배치가 도크리스 전환에서도 같은"
                  " 파이프라인으로 성립할 여지가 있다'까지 적을 수 있다.")
        else:
            print("\n▶ 미달 항목이 있다 — 그 항목을 한계로 적는다. 기준을 고치지 않는다.")
        print("🔴 어느 쪽이든 이 커버리지는 **상한**이다 — 좌표가 대여소 좌표라"
              " 도크리스의 실제 분산을 관측한 것이 아니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
