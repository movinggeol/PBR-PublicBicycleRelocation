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

def resolve_run_label(label=None):
    """재고 스냅샷 라벨을 정한다 — **못 찾으면 멈춘다.**

    `db.load_frame()`은 라벨이 비면 `latest_label()`로 **말없이 최신**을 쓴다.
    그러면 같은 명령이 다른 날 다른 재고로 돌고 결과에 그 사실이 남지 않는다
    (1.26.58에서 `gamma_sweep.py`가, 그 전에 `z_sweep.py`가 이 결함으로 걸렸다).
    판정 기준은 "인자가 있는가"가 아니라 **"못 찾았을 때 멈추는가"** 다.
    """
    import db
    with db.session() as conn:
        available = [r[0] for r in conn.execute(
            "SELECT DISTINCT run_label FROM station_info ORDER BY 1")]
    if not available:
        raise SystemExit("station_info가 비어 있습니다. 파이프라인을 한 번 돌리십시오.")
    chosen = label or available[-1]
    if chosen not in available:
        raise SystemExit(f"station_info에 '{chosen}' 실행이 없습니다."
                         f" --run-label 로 고르십시오: {available}")
    print(f"[스냅샷] station_info run_label = '{chosen}'"
          f"{' (기본: 최신)' if not label else ''}")
    return chosen


step1 = bc.load_step1(); solver = bc.ilp_mod.build_solver()
orig = step1.wanted_vehicles
PERIODS = ('25년 09월', '25년 11월', '26년 01월', '26년 03월')
SEEDS = (42, 7)

# 재고 스냅샷을 고정한다 — 환경변수 PBR_RUN_LABEL 로 바꿀 수 있다.
RUN_LABEL = resolve_run_label(__import__('os').environ.get('PBR_RUN_LABEL'))

rows = []
k_rows = []
for period in PERIODS:
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
