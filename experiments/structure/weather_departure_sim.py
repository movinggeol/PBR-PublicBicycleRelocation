"""2단계: 출발 직전 관측으로 목표 재고를 줄이면 결품 시간이 늘어나는가 (2026-11-08 등록, 결과 전에 기준을 적는다).

1단계(`weather_departure.py`, 1.26.423)는 예측 오차와 커버리지로 쟀고 기준 셋을 겨우 통과했다. 남은 물음은
**결품 쪽 대가**다: 출발 때는 비가 왔는데 회차 동안 그친 '오경보' 회차(대여소 × 날 기준 3.0%)에서 배송 대상의
커버리지가 97.4 → 94.9%로 떨어졌다. 이것이 결품 시간으로 얼마인지, 그 대가를 치르고 무엇을 아끼는지 잰다.

어떻게 재나 (19개월 반복 `repeat_eval.py`의 계획 · 평가 함수를 그대로 쓴다)
    - 재고 스냅샷은 정본 '2026-08-11 real', 순수요는 그 달 평일 또는 휴일, 계절 보정 없음, 씨앗 42 · 7 · 13.
      계획은 군집(+수급 조정) → ILP → VRP(제안 방법 P), 평가는 `simulate_pair`(결품 · 포화를 한 궤적에서).
    - 정책
        P0 현행     : 그 달 통계로 세운 계획 하나를 그 달 모든 날에 쓴다(지금 실험의 기준과 같다).
        P1 출발 비  : 출발 직전 3시간 관측 강수가 1mm 이상인 날만, 평균에 날씨 배율을 곱해(mu × r) 그날 계획을
                      다시 세운다. 목표 = mu × r + 1.99σ(mu < 0이면 재고 + mu × r), 상한 자르기는 운영 그대로.
                      배율 r은 1단계와 같은 모형(출발 직전 관측 → 도시 하루 배율, 검증 달 이전 달들로만 적합,
                      0.2~2.0)이다. 비가 안 온 날은 P0과 같은 계획이라 차이가 0이다.
        P2          : P1과 같되 05~10시 회차는 적용하지 않는다(1단계에서 05~10시 평일이 −5.3%로 나빴다).
    - 비교는 **출발 비 날에만** 짝지어(같은 날 · 같은 씨앗 · 같은 모집단) 한다. 모집단은 그날 P0 · P1 후보의 합집합이라
      두 정책이 같은 자로 잰다. 평가 순수요는 그날 하루치다.
    - 결과를 비 온 회차(창 강수 ≥ 1mm)와 오경보 회차(창 강수 < 1mm)로 갈라 함께 낸다.

채택 기준 (출발 비 날 전체의 평균, 정책별로 따로 판정)
    S1. 결품 시간 증가 ≤ P0의 1%       (결품을 사실상 늘리지 않는가)
    S2. 포화 시간 증가 ≤ P0의 1%       (반납을 더 막지 않는가)
    S3. 끝낸 대수 또는 이동거리 −5% 이상 (줄인 만큼 일을 아끼는가. 아끼는 것이 없으면 바꿀 까닭이 없다)
    S4. 오경보 회차만의 결품 증가 ≤ P0의 5% (1단계가 짚은 위험 자리)
    넷 다 통과한 정책이 있으면 운영 반영을 사용자에게 제안한다(둘 다면 적용 범위가 넓은 P1).
    없으면 자동 반영을 다시 꺼내지 않는다. 화면 알림(1.26.25)은 그대로다.

한계 (결과와 함께 밝힐 것)
    재고 스냅샷이 하나다 · 그 달 통계로 그 달을 재는 표본 안 설정이다(19개월 반복과 같은 설정, 두 정책 공통) ·
    관측소 한 곳 · 출발 비 날은 시간대 · 요일별로 적다.

실행: python experiments/structure/weather_departure_sim.py [--seeds 42,7,13] [--day-types weekday,holiday]
      [--out experiments/structure/weather_departure_sim.csv]
"""
import argparse
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "experiments" / "baseline"))

import pandas as pd

import baseline_compare as bc
import db
import demand_model
import weather
import weather_departure as wd
from project_config import select_day_type

WINDOWS = ("_05_10", "_10_15", "_15_20")
S1_STOCKOUT, S2_SATURATION, S3_SAVING, S4_FALSE_ALARM = 1.0, 1.0, 5.0, 5.0   # %


def scaled_candidates(net, st_info, duration, ratio, step1):
    """`bc.build_candidates`와 같은 길인데, 목표 재고를 계산하기 직전에 평균에 배율을 곱한다."""
    stats, _daily, _r = bc.quiet(bc.target_mod.build_stats, net, st_info[["station_id", "parking_lot", "stock"]],
                                 duration, warmup_net=None, warmup_days=0, verbose=False)
    stats = stats.copy()
    stats["mu"] = stats["mu"] * ratio
    rebal = bc.quiet(bc.target_mod.compute_rebal_qty, stats, z=None)
    buffer = io.StringIO()
    rebal.to_csv(buffer, index=False, encoding="utf-8")
    buffer.seek(0)
    return bc.quiet(step1.select_top_unbalanced_st, buffer, duration, st_info)


def ratio_by_day(conn, hourly, raw, duration, day_type, periods):
    """검증 달마다 그 이전 달들로 적합한 배율 모형으로, 출발 비 날의 배율을 낸다. {period: {date: (r, 창 강수)}}"""
    dep = wd.departure_frame(raw, duration, 3)
    window = weather.window_frame(hourly, duration).set_index("date")["rain"]
    net = {p: select_day_type(db.load_frame(conn, "net_demand", period=p), "date", day_type) for p in periods}
    demand = {p: demand_model.window_demand(f, duration) for p, f in net.items() if not f.empty}
    out = {}
    for period in periods:
        earlier = [p for p in periods if demand_model.month_index(p) < demand_model.month_index(period)]
        coef = wd.fit_ratio(demand, earlier, dep)
        if coef is None:
            continue
        ratio = wd.predict(coef, dep)
        days = set(pd.to_datetime(net[period]["date"]).dt.strftime("%Y-%m-%d")) if period in net else set()
        wet_dep = dep[(dep["rain"] >= weather.RAIN_MM) & dep["date"].isin(days)]
        out[period] = {d: (float(ratio.get(pd.Timestamp(d), 1.0)), float(window.get(d, 0.0))) for d in wet_dep["date"]}
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seeds", default="42,7,13")
    parser.add_argument("--day-types", default="weekday,holiday")
    parser.add_argument("--run-label", default="")
    parser.add_argument("--out", default=str(ROOT / "experiments" / "structure" / "weather_departure_sim.csv"))
    parser.add_argument("--fresh", action="store_true", help="이어 돌지 않고 처음부터(기존 --out을 지운다)")
    args = parser.parse_args()
    # 한 짝을 끝낼 때마다 --out에 덧붙이고, 다시 켜면 끝난 짝은 건너뛴다(11-08: 돌던 중 노트북을 꺼야 했다).
    out = Path(args.out)
    if args.fresh and out.exists():
        out.unlink()
    done = set()
    if out.exists():
        prev = pd.read_csv(out, encoding="utf-8", dtype=str)
        done = set(prev[["day_type", "duration", "period", "seed", "date"]].itertuples(index=False, name=None))
        print(f"이어 돈다: 끝난 짝 {len(done)}개는 건너뛴다 ({out})")
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    day_types = [d.strip() for d in args.day_types.split(",") if d.strip()]

    hourly, raw = weather.load_hourly(), wd.load_raw_hourly()
    step1 = bc.load_step1()
    solver = bc.ilp_mod.build_solver()
    with db.session() as conn:
        periods = sorted({r[0] for r in conn.execute("SELECT DISTINCT period FROM net_demand")},
                         key=demand_model.month_index)
        periods = weather.covered_periods(periods, hourly)
        plan = {(t, d): ratio_by_day(conn, hourly, raw, d, t, periods) for t in day_types for d in WINDOWS}
    n_days = sum(len(v) for per in plan.values() for v in per.values())
    print(f"출발 비 날 {n_days}개(요일 구분 × 시간대 × 달) · 씨앗 {len(seeds)}개 → 날씨 계획 {n_days * len(seeds)}번\n")

    rows = []
    for day_type in day_types:
        bc.align_day_type(day_type, bc.ilp_mod, bc.vrp_mod, bc.kpi_mod, step1)
        for period in periods:
            todo = {d: plan[(day_type, d)].get(period, {}) for d in WINDOWS}
            if not any(todo.values()):
                continue
            bc.reset_population_guard()
            net, st_info, _w = bc.quiet(bc.load_inputs, period, args.run_label, day_type, 0, "")
            for duration, days in todo.items():
                if not days:
                    continue
                base = bc.build_candidates(net, st_info, duration, None, pd.DataFrame(), 0, step1)
                if base.empty:
                    continue
                for seed in seeds:
                    left = {d: v for d, v in days.items() if (day_type, duration, period, str(seed), d) not in done}
                    if not left:
                        continue
                    _c, routes0 = bc.plan_with_clusters(base.copy(), step1, solver, adjust=True, seed=seed)
                    delta0, stats0 = bc.executed_delta(routes0), bc.route_stats(routes0)
                    for date, (ratio, window_rain) in left.items():
                        cand = scaled_candidates(net, st_info, duration, ratio, step1)
                        if cand.empty:
                            continue
                        _c1, routes1 = bc.plan_with_clusters(cand.copy(), step1, solver, adjust=True, seed=seed)
                        population = (pd.concat([base, cand], ignore_index=True)
                                      .drop_duplicates(subset="station_id", keep="first"))
                        day_net = net[pd.to_datetime(net["날짜"]).dt.strftime("%Y-%m-%d") == date]   # 날짜 칸이 글자든 시각이든 같은 날을 고른다
                        # 날마다 모집단(그날 두 정책 후보의 합집합)이 달라 분모 감시를 날 단위로 다시 건다.
                        # 같은 날의 두 정책은 아래 두 줄이 같은 `population` · `day_net`을 받아 같은 자로 잰다.
                        bc.reset_population_guard()
                        sim0 = bc.simulate_pair(day_net, population, delta0, duration)
                        sim1 = bc.simulate_pair(day_net, population, bc.executed_delta(routes1), duration)
                        if sim0 is None or sim1 is None:
                            continue
                        stats1 = bc.route_stats(routes1)
                        rows.append({"day_type": day_type, "duration": duration, "period": period, "seed": seed,
                                     "date": date, "ratio": ratio, "window_rain": window_rain,
                                     "wet": window_rain >= weather.RAIN_MM, "population": len(population),
                                     **{f"{k}_p0": sim0[k] for k in ("stockout_after", "saturation_after")},
                                     **{f"{k}_p1": sim1[k] for k in ("stockout_after", "saturation_after")},
                                     "stockout_before": sim0["stockout_before"],
                                     **{f"{k}_p0": stats0[k] for k in ("bikes_done", "km", "over")},
                                     **{f"{k}_p1": stats1[k] for k in ("bikes_done", "km", "over")}})
                        pd.DataFrame([rows[-1]]).to_csv(out, mode="a", header=not out.exists(), index=False,
                                                        encoding="utf-8")
                    print(f"  {day_type} {period} {duration} 씨앗 {seed}: 출발 비 날 {len(days)}개", flush=True)

    if not out.exists():
        print("비교할 날이 없습니다.")
        return 1
    df = pd.read_csv(out, encoding="utf-8")          # 앞서 돈 짝까지 합친 전체
    df["wet"] = df["wet"].astype(str).str.lower().isin(("true", "1"))
    print(f"\n원자료 {len(df)}행 → {out}\n")
    return report(df)


def pct(new, old):
    return (new - old) / old * 100 if old else float("nan")


def report(df: pd.DataFrame) -> int:
    p2 = df.copy()
    early = p2["duration"] == "_05_10"
    for k in ("stockout_after", "saturation_after", "bikes_done", "km", "over"):
        p2.loc[early, f"{k}_p1"] = p2.loc[early, f"{k}_p0"]      # 05~10시는 적용하지 않는다 = P0과 같다
    policies = {"P1": df, "P2": p2}

    print("출발 비 날의 짝 비교 (같은 날 · 같은 씨앗 · 같은 모집단, 평균)")
    print(f"  짝 {len(df)}개 · 그중 비 온 회차 {int(df['wet'].sum())} · 오경보 회차 {int((~df['wet']).sum())} · "
          f"배율 평균 {df['ratio'].mean():.2f}\n")
    print(f"{'시간대':8} {'구분':8} {'짝':>4} {'결품 P0→P1(h)':>18} {'포화 P0→P1(h)':>18} {'끝낸 대수':>14} {'이동 km':>16}")
    for (d, t), g in df.groupby(["duration", "day_type"]):
        print(f"{d:8} {t:8} {len(g):4d} {g['stockout_after_p0'].mean():8.3f}→{g['stockout_after_p1'].mean():.3f} "
              f"{g['saturation_after_p0'].mean():8.3f}→{g['saturation_after_p1'].mean():.3f} "
              f"{g['bikes_done_p0'].mean():6.0f}→{g['bikes_done_p1'].mean():.0f} "
              f"{g['km_p0'].mean():7.1f}→{g['km_p1'].mean():.1f}")

    ok_any = None
    for name, frame in policies.items():
        fa = frame[~frame["wet"]]
        s1 = pct(frame["stockout_after_p1"].mean(), frame["stockout_after_p0"].mean())
        s2 = pct(frame["saturation_after_p1"].mean(), frame["saturation_after_p0"].mean())
        save_bikes = -pct(frame["bikes_done_p1"].mean(), frame["bikes_done_p0"].mean())
        save_km = -pct(frame["km_p1"].mean(), frame["km_p0"].mean())
        s4 = pct(fa["stockout_after_p1"].mean(), fa["stockout_after_p0"].mean()) if not fa.empty else float("nan")
        wet = frame[frame["wet"]]
        s1_wet = pct(wet["stockout_after_p1"].mean(), wet["stockout_after_p0"].mean()) if not wet.empty else float("nan")
        checks = [s1 <= S1_STOCKOUT, s2 <= S2_SATURATION, max(save_bikes, save_km) >= S3_SAVING, s4 <= S4_FALSE_ALARM]
        print(f"\n{name} 판정 (결과 전에 적은 기준)")
        print(f"  S1 결품 증가 {s1:+.2f}%  (≤ {S1_STOCKOUT:.0f}%)  {'통과' if checks[0] else '실패'}"
              f"   [비 온 회차 {s1_wet:+.2f}% · 오경보 회차 {s4:+.2f}%]")
        print(f"  S2 포화 증가 {s2:+.2f}%  (≤ {S2_SATURATION:.0f}%)  {'통과' if checks[1] else '실패'}")
        print(f"  S3 아낀 일: 끝낸 대수 {save_bikes:+.1f}% · 이동거리 {save_km:+.1f}%  (둘 중 하나 ≥ {S3_SAVING:.0f}%)  "
              f"{'통과' if checks[2] else '실패'}")
        print(f"  S4 오경보 회차 결품 증가 {s4:+.2f}%  (≤ {S4_FALSE_ALARM:.0f}%)  {'통과' if checks[3] else '실패'}")
        print("  → " + ("넷 다 통과" if all(checks) else "하나 이상 실패"))
        if all(checks) and ok_any is None:
            ok_any = name
    print("\n결론: " + (f"{ok_any}을(를) 운영 반영 후보로 사용자에게 제안한다" if ok_any
                       else "통과한 정책이 없다. 자동 반영을 다시 꺼내지 않는다(화면 알림 유지)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
