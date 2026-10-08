"""거리 인지 `wanted_vehicles()`가 대여소 수 기준(legacy)보다 나은가 (1.26.10).

TODO.md 2-1 ⭐ · EXPERIMENTS.md 5-G ㉰가 지목한 "다음 단계"를 구현했다 —
`_wanted_vehicles_geo()`가 대여소 수 대신 후보 집합의 기하(BHH 근사 + depot
왕복)로 이동거리를 어림하고 `travel_seconds()`를 거쳐 K를 정한다. 흩어진
회차는 K를 늘리고 뭉친 회차는 줄여 예산을 지키면서 물량을 더 옮기는 것이 목표다.

판정 규칙(다른 실험과 같다):
  · 결품 시간이 판정 기준이다. 물량·거리는 보조다.
  · 여러 달·여러 씨앗의 합계로 본다 — 한 조건에서 좋은 것은 요철일 수 있다.
  · 이긴 조합 수를 함께 센다.
  · **예산 초과율이 현행보다 나빠지면 안 된다** — 5-G ㉯에서 K=8이 결품은
    이겼지만 초과 80%로 막힌 것과 같은 함정을 여기서도 확인해야 한다.

실행:
    python experiments/params/wanted_vehicles_geo_sweep.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments" / "baseline"))
import pandas as pd
import baseline_compare as bc
from experiments._shared import resolve_run_label

step1 = bc.load_step1(); solver = bc.ilp_mod.build_solver()
# 순수요를 읽는 요일(아래 'weekday')과 ILP·VRP·군집 모듈의 요일을 맞춘다(1.26.281) — 안 맞추면 모듈은
# 오늘 달력을 따른다. 지금은 휴일 이동 계수가 평일로 폴백해 어느 날 돌려도 수치는 같다.
bc.align_day_type('weekday', bc.ilp_mod, bc.vrp_mod, bc.kpi_mod, step1)
orig = step1.wanted_vehicles
PERIODS = ('25년 09월', '25년 11월', '26년 01월', '26년 03월')
SEEDS = (42, 7)

# 재고 스냅샷을 고정한다 — 환경변수 PBR_RUN_LABEL 로 바꿀 수 있다(인자를 안 받으므로 안내도 그 이름으로 한다).
RUN_LABEL = resolve_run_label(__import__('os').environ.get('PBR_RUN_LABEL'), hint="PBR_RUN_LABEL=…")

rows = []
k_rows = []
for period in PERIODS:
    # 기간이 바뀌면 분모도 정당하게 달라진다 — 분모 감시(1.26.73) 초기화.
    bc.reset_population_guard()
    net, st, warm = bc.load_inputs(period, RUN_LABEL, 'weekday', 0, '')
    for dur in ('_05_10', '_10_15', '_15_20'):
        base = bc.build_candidates(net, st, dur, None, warm, 0, step1)
        if base.empty:
            continue
        k_rows.append({'period': period, 'duration': dur,
                       'legacy_K': orig(base, geo=False), 'geo_K': orig(base, geo=True)})
        for mode in ('legacy', 'geo'):
            for seed in SEEDS:
                if mode == 'geo':
                    step1.wanted_vehicles = lambda df, _o=orig: _o(df, geo=True)
                try:
                    cand, routes = bc.plan_with_clusters(base.copy(), step1, solver,
                                                         adjust=True, seed=seed)
                finally:
                    step1.wanted_vehicles = orig
                delta = bc.executed_delta(routes)
                _, after = bc.stockout(net, cand, delta, dur)
                s = bc.route_stats(routes)
                rows.append({'period': period, 'duration': dur, 'mode': mode,
                             'seed': seed, 'stockout': after, 'bikes': s['bikes'],
                             'km': s['km'], 'over': s['over'], 'max_min': s['max_min'],
                             'vehicles': s['vehicles']})

f = pd.DataFrame(rows)
n_combos = len(f) // 2
print("4개월 x 3회차 x 씨앗 2개 = %d개 조합" % n_combos)
print()

print("회차별 K (auto) — legacy vs geo")
print(pd.DataFrame(k_rows).to_string(index=False))
print()

g = f.groupby('mode').agg(결품=('stockout', 'mean'), 옮김=('bikes', 'sum'),
                          이동km=('km', 'sum'), 초과=('over', 'sum'),
                          최장=('max_min', 'max'), 차량계=('vehicles', 'sum')).reset_index()
print(g.round(3).to_string(index=False))
print()

w = f.pivot_table(index=['period', 'duration', 'seed'], columns='mode', values='stockout')
better = int((w['geo'] < w['legacy']).sum())
worse = int((w['geo'] > w['legacy']).sum())
tie = int((w['geo'] == w['legacy']).sum())
print(f"결품 기준 geo가 이긴 조합: {better} / 진 조합: {worse} / 동률: {tie} (총 {len(w)})")

o = f.pivot_table(index=['period', 'duration', 'seed'], columns='mode', values='over')
print(f"예산 초과 건수 합 — legacy {int(o['legacy'].sum())} / geo {int(o['geo'].sum())}")
