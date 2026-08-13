"""평일과 주말의 수요 구조가 얼마나 다른지 잰다 — 따로 계산해야 하는지의 근거.

파이프라인은 첫 커밋부터 평일만 다뤘다(raw_to_net의 `평일유무` 필터).
주말을 넣으려면 먼저 정해야 할 것이 있다:

    평일과 주말을 **한 통계로 묶어도 되는가, 따로 계산해야 하는가?**

묶어도 된다면 필터만 지우면 끝이다. 따로 계산해야 한다면 day_type이 실행
설정으로 올라가야 한다(시간대 `duration`이 그렇듯이).

판단 기준은 프로젝트가 이미 쓰는 것과 같다 —
"시간대가 다르면 수요 구조가 반대이므로 섞어서 평균 내지 마라"(SKILL.md).
요일도 같은 논리가 적용되는지 본다.

실행: python experiments/weekend_profile.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

import db

WINDOWS = {"_05_10": range(5, 10), "_10_15": range(10, 15), "_15_20": range(15, 20)}
TARGET_CUT = 2      # |순수요| > 2 인 대여소가 파이프라인의 작업 대상이다


def net_demand(period: str) -> pd.DataFrame:
    """raw_to_net과 같은 방식으로 순수요를 계산하되 **주말도 남긴다**.

    순수요 = 대여 − 반납 (양수면 자전거가 빠져나가는 대여소).
    """
    data, _ = db.read_rental_source(
        period, columns=['대여일시', '대여_대여소ID', '반납일시', '반납_대여소ID'])
    if data.empty:
        return data

    data['대여일시'] = pd.to_datetime(data['대여일시'])
    data['반납일시'] = pd.to_datetime(data['반납일시'])
    data['날짜'] = data['대여일시'].dt.date

    rent = (data.assign(hour=data['대여일시'].dt.hour)
                .groupby(['날짜', '대여_대여소ID', 'hour']).size().unstack(fill_value=0))
    ret = (data.assign(hour=data['반납일시'].dt.hour)
               .groupby(['날짜', '반납_대여소ID', 'hour']).size().unstack(fill_value=0))

    hours = list(range(24))
    rent = rent.reindex(columns=hours, fill_value=0)
    ret = ret.reindex(columns=hours, fill_value=0)
    index = rent.index.union(ret.index)

    net = (rent.reindex(index, fill_value=0) - ret.reindex(index, fill_value=0))
    net.columns = [f"net_{h:02d}" for h in hours]
    net = net.reset_index()
    net.columns = ['날짜', 'station_id'] + list(net.columns[2:])
    net['날짜'] = pd.to_datetime(net['날짜'])
    net['주말'] = net['날짜'].dt.dayofweek >= 5
    return net


def window_stats(net: pd.DataFrame, hours) -> dict:
    """한 시간대의 요일 구분별 요약."""
    columns = [f"net_{h:02d}" for h in hours]
    frame = net.copy()
    frame['net'] = frame[columns].sum(axis=1)

    out = {}
    for weekend, group in frame.groupby('주말'):
        daily = group.groupby('날짜')['net'].apply(lambda s: s.abs().sum())
        target = group[group['net'].abs() > TARGET_CUT]
        per_day_targets = target.groupby('날짜').size()
        # 대여소별 평균 순수요 = 파이프라인의 mu
        mu = group.groupby('station_id')['net'].mean()
        out['주말' if weekend else '평일'] = {
            'days': int(group['날짜'].nunique()),
            'need': float(daily.mean()),
            'targets': float(per_day_targets.mean()) if len(per_day_targets) else 0.0,
            'mu': mu,
        }
    return out


def main() -> int:
    with db.session() as conn:
        periods = [r[0] for r in conn.execute(
            "SELECT DISTINCT period FROM rental_history ORDER BY period")]
    if not periods:
        print("rental_history가 비어 있습니다. tools/load_rentals.py로 적재하세요.")
        return 1

    print(f"대상 기간 {len(periods)}개\n")
    rows, correlations = [], []

    for period in periods:
        net = net_demand(period)
        if net.empty:
            continue
        for duration, hours in WINDOWS.items():
            stats = window_stats(net, hours)
            if '평일' not in stats or '주말' not in stats:
                continue
            weekday, weekend = stats['평일'], stats['주말']

            # 대여소별 mu가 평일과 주말에 같은 방향인가?
            joined = pd.concat([weekday['mu'].rename('wd'),
                                weekend['mu'].rename('we')], axis=1).dropna()
            r = float(np.corrcoef(joined['wd'], joined['we'])[0, 1]) if len(joined) > 2 else np.nan
            # 부호가 뒤집히는 대여소 비율 (|mu|가 의미 있는 곳만)
            meaningful = joined[(joined['wd'].abs() > 1) | (joined['we'].abs() > 1)]
            flipped = float((np.sign(meaningful['wd']) != np.sign(meaningful['we'])).mean()) \
                if len(meaningful) else np.nan

            rows.append({
                'period': period, 'duration': duration,
                'wd_days': weekday['days'], 'we_days': weekend['days'],
                'wd_need': weekday['need'], 'we_need': weekend['need'],
                'wd_targets': weekday['targets'], 'we_targets': weekend['targets'],
                'r': r, 'flipped': flipped,
            })
            correlations.append(r)

    frame = pd.DataFrame(rows)
    if frame.empty:
        print("비교할 데이터가 없습니다.")
        return 1

    print("회차별 평균 (12개월 종합)")
    print(f"{'회차':8} {'평일 일수':>8} {'주말 일수':>8} {'평일 필요량':>10} {'주말 필요량':>10} "
          f"{'주말/평일':>8} {'평일 대상':>8} {'주말 대상':>8} {'mu 상관':>8} {'부호역전':>8}")
    for duration, group in frame.groupby('duration'):
        ratio = group['we_need'].mean() / group['wd_need'].mean()
        print(f"{duration:8} {group['wd_days'].mean():8.1f} {group['we_days'].mean():8.1f} "
              f"{group['wd_need'].mean():10.1f} {group['we_need'].mean():10.1f} "
              f"{ratio:8.2f} {group['wd_targets'].mean():8.1f} "
              f"{group['we_targets'].mean():8.1f} {group['r'].mean():8.3f} "
              f"{group['flipped'].mean() * 100:7.1f}%")

    print("\n해석")
    print("  mu 상관이 높고 부호역전이 적다  → 같은 대여소가 같은 방향으로 움직인다.")
    print("                                    한 통계로 묶어도 큰 무리가 없다.")
    print("  mu 상관이 낮거나 부호역전이 많다 → 수요 구조가 다르다.")
    print("                                    섞으면 둘 다 틀린다 — 따로 계산해야 한다.")
    print(f"\n  ※ 주말 표본은 월 {frame['we_days'].mean():.0f}일 안팎이다."
          " sigma 추정이 평일보다 불안정하다는 뜻이므로,")
    print("     따로 계산하기로 하면 표본 부족을 함께 다뤄야 한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
