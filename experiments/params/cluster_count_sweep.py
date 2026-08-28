"""군집 수 K가 산출물을 어떻게 바꾸나 (1.26.9).

`wanted_vehicles()`가 작업량으로 K를 정하는데, **그 K가 최선인지는 재 본 적이
없었다.** 파이프라인의 물량 손실을 추적하다 나온 물음이다 —
군집화 → ILP에서 **28%가 사라진다**(docs/분석/EXPERIMENTS.md 5-B장).

    ILP는 군집 **안에서만** 옮긴다 → 군집이 작을수록 한쪽이 남는다
    군집을 키우면 그 안에서 수급이 맞아 버리는 양이 준다

그런데 군집이 크면 차량 하나가 더 오래 돈다. **맞바꿈이다.**

판정 규칙(다른 실험과 같다):
  · **결품 시간**이 판정 기준이다. 물량·거리는 보조다.
  · 여러 달·여러 씨앗의 합계로 본다 — 한 조건에서 좋은 것은 요철일 수 있다.
  · **이긴 조합 수**를 함께 센다.

실행:
    python experiments/params/cluster_count_sweep.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments" / "baseline"))
import pandas as pd
import baseline_compare as bc

step1 = bc.load_step1(); solver = bc.ilp_mod.build_solver()
orig = step1.wanted_vehicles
PERIODS = ('25년 09월', '25년 11월', '26년 01월', '26년 03월')
SEEDS = (42, 7)

rows = []
for period in PERIODS:
    net, st, warm = bc.load_inputs(period, '', 'weekday', 0, '')
    for dur in ('_05_10', '_10_15', '_15_20'):
        base = bc.build_candidates(net, st, dur, None, warm, 0, step1)
        if base.empty:
            continue
        for K in (8, 12, 16, None):
            for seed in SEEDS:
                if K is not None:
                    step1.wanted_vehicles = lambda df, _K=K: _K
                try:
                    cand, routes = bc.plan_with_clusters(base.copy(), step1, solver,
                                                         adjust=True, seed=seed)
                finally:
                    step1.wanted_vehicles = orig
                delta = bc.executed_delta(routes)
                _, after = bc.stockout(net, cand, delta, dur)
                s = bc.route_stats(routes)
                rows.append({'period': period, 'duration': dur, 'K': K or 'auto',
                             'seed': seed, 'stockout': after, 'bikes': s['bikes'],
                             'km': s['km'], 'over': s['over'], 'max_min': s['max_min']})

f = pd.DataFrame(rows)
print("4개월 x 3회차 x 씨앗 2개 = %d개 조합" % (len(f) // 4))
print()
g = f.groupby('K').agg(결품=('stockout', 'mean'), 옮김=('bikes', 'sum'),
                       이동km=('km', 'sum'), 초과=('over', 'sum'),
                       최장=('max_min', 'max')).reset_index()
print(g.round(3).to_string(index=False))
print()
w = f.loc[f.groupby(['period', 'duration', 'seed'])['stockout'].idxmin()]
print("결품 최소인 K (조합별):")
print(w['K'].value_counts().to_string())
