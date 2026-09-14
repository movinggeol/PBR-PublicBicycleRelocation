"""재고 수집 간격을 10분에서 늘리면 무엇을 잃는가 (2026-09-14).

## 왜 이 실험이 필요한가

수집 창을 24시간으로 넓히기로 하면서(1.26.206) *"그럼 밤에는 10분마다 안 찍어도
되지 않나"* 라는 물음이 나왔다. **짐작으로 답할 일이 아니다** — 간격은 한 번 정하면
그 기간의 자료를 되돌릴 수 없다. 촘촘히 모은 것은 나중에 솎을 수 있지만, 성기게
모은 것은 촘촘하게 만들 수 없다.

## 무엇을 묻는가

① 10분 사이에 재고가 바뀌는 대여소는 몇 곳인가 (시간대별)
② 간격을 늘리면 **개별 사건**(대여·반납·재배치 한 건씩)을 얼마나 놓치는가
③ 간격을 늘리면 **결품 시간** 추정이 얼마나 달라지는가

②와 ③을 갈라 묻는 것이 요점이다. 결품은 몇 시간씩 이어지는 *상태*라 성기게
찍어도 면적이 비슷하지만, 사건은 두 틱 사이에서 +1과 −1이 서로 지워진다.

## 어떻게 판정하는가

이미 모은 10분 자료를 **솎아서** 성긴 수집을 흉내 낸다(같은 날, 같은 대여소).
- 결품 시간 = (재고 0인 대여소·틱 수) × 간격. 10분 값을 기준으로 오차율을 본다.
- 놓친 사건 = Σ|Δ재고|. 10분 값을 기준으로 감소율을 본다.

🔴 **판정은 쓰임새가 정한다.** 결품만 쓰면(P4 7번의 `z`·`γ` 재조정) 성겨도 되지만,
`Δ관측 − Δ이용자 = 재배치`(TODO 17-B 3단계)는 사건을 세는 계산이라 못 쓴다.

⚠️ **야간(19~08시) 표본이 아직 없다.** 이 PC의 실제 가동이 08:30~18:50이었다 —
24시간 수집이 쌓인 뒤 다시 돌려야 밤 구간을 말할 수 있다.

> 재현: `python experiments/diagnostic/tick_interval.py`
> (회사환경 DB에만 있는 `stock_history`를 읽는다 — 결과는 docs/분석/EXPERIMENTS.md 35장)
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import project_config  # noqa: F401,E402  (cp949 출력 가드 — 1.26.194)
import db  # noqa: E402

# 솎아서 흉내 낼 간격들(분). 10분이 기준이다.
STEPS = (10, 20, 30, 60, 180)
# 하루 전체를 견줄 수 있을 만큼 모인 날만 센다 — 반쪽짜리 날은 비율을 흔든다.
MIN_TICKS_PER_DAY = 40
NIGHT_HOURS = range(0, 5)          # 00~05시 — 간격을 늘리자는 후보 구간


def _at(stamp: str) -> datetime:
    return datetime.strptime(stamp, "%Y-%m-%d %H:%M")


def load_ticks() -> dict:
    """틱 시각 → {대여소: 재고}."""
    with db.session() as conn:
        rows = conn.execute(
            "select observed_at, station_id, stock from stock_history order by observed_at"
        ).fetchall()
    by_tick: dict = defaultdict(dict)
    for stamp, station, stock in rows:
        by_tick[stamp][station] = stock
    return by_tick


def hourly_change(by_tick: dict) -> dict:
    """시간대별로 '연속한 10분 틱 사이에 재고가 바뀐 대여소 수'를 모은다."""
    ticks = sorted(by_tick)
    per_hour: dict = defaultdict(list)
    for before, after in zip(ticks, ticks[1:]):
        start, end = _at(before), _at(after)
        if (end - start).total_seconds() != 600:      # 연속한 10분 쌍만
            continue
        a, b = by_tick[before], by_tick[after]
        per_hour[start.hour].append(sum(1 for s in a.keys() & b.keys() if a[s] != b[s]))
    return per_hour


def downsample(by_tick: dict, step: int) -> tuple:
    """간격 `step`분으로 솎았을 때의 (결품 대여소·틱, 사건 총량, 틱 수)."""
    days: dict = defaultdict(list)
    for stamp in sorted(by_tick):
        days[stamp[:10]].append(stamp)

    zero = events = kept = 0
    for stamps in days.values():
        if len(stamps) < MIN_TICKS_PER_DAY:
            continue
        # 하루 안의 절대 분(分)으로 격자를 잡는다 — 시(時)로 나누면 180분이
        # 60분과 같은 틱을 고르고도 3배를 곱해 결품을 부풀린다(만들며 걸렸다).
        chosen = [s for s in stamps if (_at(s).hour * 60 + _at(s).minute) % step == 0]
        kept += len(chosen)
        for stamp in chosen:
            zero += sum(1 for v in by_tick[stamp].values() if v == 0)
        for before, after in zip(chosen, chosen[1:]):
            if (_at(after) - _at(before)).total_seconds() / 60 != step:
                continue
            a, b = by_tick[before], by_tick[after]
            events += sum(abs(b[s] - a[s]) for s in a.keys() & b.keys())
    return zero, events, kept


def main() -> int:
    by_tick = load_ticks()
    if not by_tick:
        raise SystemExit("stock_history가 비어 있습니다 — 수집이 쌓인 환경에서 돌리십시오.")
    print(f"틱 {len(by_tick)}개 · 대여소 최대 {max(len(v) for v in by_tick.values())}곳")

    print("\n① 10분 사이에 재고가 바뀐 대여소 수")
    per_hour = hourly_change(by_tick)
    print("  시각   쌍수   평균   중앙   최대")
    for hour in sorted(per_hour):
        counts = sorted(per_hour[hour])
        print(f"  {hour:02d}시  {len(counts):5d}  {sum(counts) / len(counts):5.1f}"
              f"  {counts[len(counts) // 2]:5d}  {counts[-1]:5d}")
    if not set(per_hour) & set(NIGHT_HOURS):
        print("  ⚠️ 야간(00~05시) 표본이 없습니다 — 24시간 수집이 쌓인 뒤 다시 보십시오.")

    print("\n②③ 간격을 늘리면")
    print("  간격   틱수   결품시간(대·시간)    차이   놓친 사건")
    base_hours = base_events = None
    for step in STEPS:
        zero, events, kept = downsample(by_tick, step)
        hours = zero * step / 60
        if base_hours is None:
            base_hours, base_events = hours, events
        drift = (hours - base_hours) / base_hours * 100
        lost = (base_events - events) / base_events * 100 if base_events else 0.0
        print(f"  {step:3d}분  {kept:5d}  {hours:15.1f}  {drift:+6.1f}%  {lost:6.1f}%")

    print("\n📌 결품은 간격에 둔감하고 사건은 민감합니다 — 쓰임새가 간격을 정합니다.")
    print("   (docs/분석/EXPERIMENTS.md 35장 · docs/구현/COLLECTOR.md 2장)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
