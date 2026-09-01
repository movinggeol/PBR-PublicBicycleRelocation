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
for period in PERIODS:
    net, st, warm = bc.load_inputs(period, RUN_LABEL, 'weekday', 0, '')
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
