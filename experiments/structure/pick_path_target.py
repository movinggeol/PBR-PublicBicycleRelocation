"""수거량을 회차 **안** 누적 순유출의 최댓값으로 잡으면 수거가 무해해지나 — 대가는 얼마인가 (EXPERIMENTS 49장).

47장이 남긴 물음 — 계획의 수거는 장소는 맞는데(수거 대여소의 92%가 5시간 안에 거치대 90%까지 찬다) 양이 많아,
수거량을 회차 시작에 뺐다면 평일 수거 대여소의 35%가 5시간 안에 한 번은 0이 됐을 것이었다. 안전 재고
`mu + z·sigma`는 회차 **끝**의 누적 순수요로 잡은 값이라 창 안에서 크게 빠졌다 돌아오는 대여소를 못 본다는 것이
유력한 까닭이었다(47장 사후 진단 — 5시간에서 약한 쪽이 안전 재고 갈래였다).

그래서 같은 달(운영 기간)의 날짜별 시간 순수요로 **창 안 누적 순유출의 최댓값** M = max(0, max_k Σ_{h≤k} net_h)을
날마다 구하고, 경로 안전 재고 = mean(M) + z·sd(M)(z = TARGET_Z, 원래 식과 같은 상한 · 하한)로 둔다. 수거량은 원래
수거량을 **넘지 않게만** 줄인다 — q_new = min(q, 10·tanh(max(0, 재고 − 경로 안전 재고)/10)의 내림), 2대 이하면
수거하지 않는다. 배송은 그대로다. 이 안은 수거를 줄이기만 하므로 무해율은 당연히 오른다 — 그래서 물음은 **크기와
대가**, 그리고 **같은 양만큼 아무렇게나 줄여도 같은가**다.

사전 등록 (EXPERIMENTS 49장 — 결과 전 커밋. 바꾸지 않는다)
  하네스   : 47장 그대로(plan_validity.py) — 같은 규칙으로 고른 회차(등록 시점 평일 71 · 휴일 27 — 47장 뒤 수집이
             늘었다) · 같은 참조 계획 · 같은 관측 격자 · 80% 관측 규칙
  수준     : 원래 계획 · 새 안 모두 **지금 순수요**(26년 03월, 원본 19개월)에서 낸다 — 47장 정정판(--stats current)과
             같은 계획이 원래 안이다. 계절 보정은 걸지 않는다(대조 회차가 배율 1)
  모집단   : **원래 계획의 수거 대여소**(47장의 수거 모집단). 수거하지 않게 된 곳은 무해로 센다
  대조     : U 균등 축소 — 회차마다 원래 수거량에 같은 배율을 곱해 **새 안과 같은 총량**으로 줄인다(최대 나머지로 정수화)
  지평     : 3 · 5시간(판정) · 1시간(찍기만)
  판정     : 평일만. ① 5시간 무해율(새 안) ≥ 90% · ② 3 · 5시간 모두 새 안 무해율 > U이고 회차 부호 검정 단측 p < 0.05 ·
             ③ 옮길 수 있는 양(회차마다 min(Σ수거, Σ배송)의 합)이 원래보다 20% 넘게 줄지 않는다.
             셋 다 → '채택 후보'(파이프라인 반영은 6장 시뮬레이션 · ILP로 다시 잰 뒤). ②만 → '경로 기준이 맞지만 맞바꿈이 크다'
  찍기만   : 해의 크기(빼 갔다면 비었을 시간) · 수거 뒤에도 포화(관측 최고 − q ≥ 거치대 90%) 비율 · 휴일 · 수거 총량

알려진 한계 — 옮길 수 있는 양은 시 전체 min(Σ수거, Σ배송)이라 군집 · ILP · 시간 예산이 실제로 고르는 양의 상한이다.
통계는 26년 03월이고 관측은 9월이다(47장과 같다).

실행:
    python experiments/structure/pick_path_target.py            # 판정
    python experiments/structure/pick_path_target.py --sanity   # 결과 없이 입력만 점검
    python experiments/structure/pick_path_target.py --until "2026-10-06 00:00"   # 인용판(1.26.325)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import project_config  # noqa: E402,F401  — 콘솔 인코딩을 먼저 맞춘다(— · 이모지)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import db  # noqa: E402
import plan_validity as pv  # noqa: E402
import stockout_forecast as sf  # noqa: E402
from project_config import (REBAL_MIN_QTY, TARGET_QTY_UPPER_RATIO, TARGET_Z, TICKS_PER_HOUR,  # noqa: E402
                            VEHICLE_CAPACITY, duration_hours, duration_start_hour, holiday_mask)

# ── 사전 등록 (EXPERIMENTS 49장)
HORIZONS = (6, 18, 30)
MAIN_HORIZONS = (18, 30)
SAFE_FLOOR = 0.90
SIGN_P = 0.05
MAX_VOLUME_DROP = 0.20


def path_target(net: pd.DataFrame, duration: str, parking_lot: pd.Series) -> tuple:
    """대여소 → 경로 안전 재고(창 안 누적 순유출 최댓값의 mean + z·sd)와, 대조용 창 끝 합계의 mean."""
    cols = [f"net_{h:02d}" for h in duration_hours(duration)]
    values = net[cols].to_numpy(dtype=float)
    peak = np.maximum(np.cumsum(values, axis=1).max(axis=1), 0.0)
    frame = pd.DataFrame({"station_id": net["station_id"].to_numpy(), "M": peak, "end": values.sum(axis=1)})
    g = frame.groupby("station_id")
    target = (g["M"].mean() + TARGET_Z * g["M"].std().fillna(0.0))
    cap = parking_lot.reindex(target.index) * TARGET_QTY_UPPER_RATIO
    return target.clip(lower=0).clip(upper=cap), g["end"].mean()


def squash(x: pd.Series) -> pd.Series:
    """운영 식과 같은 부드러운 상한과 내림(calculate_target_qty.compute_rebal_qty)."""
    return np.floor(VEHICLE_CAPACITY * np.tanh(x / VEHICLE_CAPACITY))


def shrink_to(q: pd.Series, total: int) -> pd.Series:
    """균등 축소 — 같은 배율을 곱하고 최대 나머지로 정수화해 총량을 `total`에 정확히 맞춘다."""
    if q.sum() == 0:
        return q * 0
    raw = q * (total / q.sum())
    base = np.floor(raw)
    rest = int(total - base.sum())
    if rest > 0:
        base[(raw - base).sort_values(ascending=False).index[:rest]] += 1
    return base


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sanity", action="store_true", help="결과 없이 입력만 점검한다")
    parser.add_argument("--until", help="이 시각 전의 관측만 쓴다(예: '2026-10-06 00:00') — 인용 값을 고정한다(1.26.325)")
    args = parser.parse_args(argv)

    with db.session() as conn:
        # --until은 참조 계획도 묶는다 — 자료 끝 뒤에 세운 계획을 집지 않는다(1.26.326)
        refs = {dt: pv.load_refs(conn, dt, before=args.until) for dt in pv.DAY_TYPES}
        raw = sf.load_grid(conn)
    if args.until:
        raw = raw[raw["ts"] < pd.Timestamp(args.until)]          # 인용 값을 고정한다(1.26.325)
    print(f"관측 자료: {raw['ts'].min()} ~ {raw['ts'].max()}" + (f" (--until {args.until})" if args.until else " (끝 고정 없음)"))
    nets = {dt: pv.load_net(dt) for dt in pv.DAY_TYPES}
    stock = raw.pivot_table(index="ts", columns="station_id", values="stock")
    grid = stock.reindex(pd.date_range(stock.index.min().floor("D"), stock.index.max().ceil("D"),
                                       freq=pv.TICK, inclusive="left"))

    # 원래 계획도 **같은 순수요**에서 낸다 — 47장 정정판(--stats current)과 같다. 깎인 자료로 선 참조 계획의 mu · sigma를
    # 쓰면 두 안이 다른 자료를 보고 겨룬다(1.26.323에서 찾았다)
    targets = {}
    for dt, ref in refs.items():
        for dur, (frame, lab) in sorted(ref.items()):
            fresh = pv.current_stats(frame, nets[dt], dur)
            refs[dt][dur] = (fresh, f"{lab} → 지금 순수요")
            target, end_mean = path_target(nets[dt], dur, fresh["parking_lot"])
            targets[(dt, dur)] = target
            common = fresh.index.intersection(end_mean.index)
            same = np.isclose(end_mean[common], fresh.loc[common, "mu"], atol=1e-6).mean()
            print(f"{pv.DAY_TYPES[dt]} {dur}: 창 끝 합계 평균 = 다시 낸 mu {same:.1%} ({len(common):,}곳, 100%여야 한다) · "
                  f"안전 재고 중앙값 — 원래(mu + z·sigma) {(fresh['mu'] + TARGET_Z * fresh['sigma']).clip(lower=0).median():.1f}대 · "
                  f"경로 {target.median():.1f}대")

    days = sf.dense_days(raw)
    hol = dict(zip(days, holiday_mask(pd.Series(pd.to_datetime(days)))))
    index = {ts: i for i, ts in enumerate(grid.index)}
    rows = []
    for day in days:
        dt = "holiday" if hol[day] else "weekday"
        for dur in pv.DURATIONS:
            if dur not in refs[dt]:
                continue
            t = pd.Timestamp(day) + pd.Timedelta(hours=duration_start_hour(dur))
            if t not in index:
                continue
            s0 = grid.iloc[index[t]].dropna()
            if len(s0) < 0.5 * grid.shape[1]:
                continue
            ref, _ = refs[dt][dur]
            rebal = pv.replan(ref, s0)
            q = -rebal[rebal < -REBAL_MIN_QTY].astype(float)
            drop_total = float(rebal[rebal > REBAL_MIN_QTY].sum())
            excess = (s0.reindex(q.index) - targets[(dt, dur)].reindex(q.index)).fillna(np.inf).clip(lower=0)
            q_new = np.minimum(q, squash(excess))
            q_new[q_new <= REBAL_MIN_QTY] = 0.0
            q_u = shrink_to(q, int(q_new.sum()))
            base = {"요일": dt, "회차": dur, "회차키": f"{day} {dur}", "배송합": drop_total,
                    "수거합_원래": q.sum(), "수거합_새": q_new.sum(), "수거합_균등": q_u.sum(),
                    "옮김_원래": min(q.sum(), drop_total), "옮김_새": min(q_new.sum(), drop_total),
                    "수거곳_새": int((q_new > 0).sum())}
            if args.sanity:
                rows.append(base)
                continue
            cap = ref["parking_lot"].reindex(q.index)
            for h in HORIZONS:
                ws = pv.window_stats(grid, index[t], h)
                if ws is None:
                    continue
                lo, hi, _, ok, win = ws
                keep = ok.reindex(q.index).fillna(False).astype(bool)
                qq = {"원래": q[keep], "새": q_new[keep], "균등": q_u[keep]}
                row = dict(base, 지평=h, 모집단=int(keep.sum()))
                for name, v in qq.items():
                    row[f"무해_{name}"] = int((lo[v.index] >= v).sum())
                    w = win[v.index]
                    below = w.le(v, axis=1).where(w.notna()) & (v > 0)
                    row[f"빈시간_{name}"] = float((below.sum() / w.notna().sum()).sum())
                    row[f"포화남음_{name}"] = int(((hi[v.index] - v) >= pv.SAT_RATIO * cap[v.index]).sum())
                rows.append(row)

    frame = pd.DataFrame(rows)
    inst = frame.drop_duplicates("회차키")
    for dt in pv.DAY_TYPES:
        g = inst[inst["요일"] == dt]
        print(f"{pv.DAY_TYPES[dt]} 회차 {len(g)}개")
    if args.sanity:
        print("--sanity: 결과(무해 · 옮길 수 있는 양)는 계산하지 않았다")
        return 0

    verdict = {}
    for dt in pv.DAY_TYPES:
        g = frame[frame["요일"] == dt]
        inst = g.drop_duplicates("회차키")
        moved_o, moved_n = inst["옮김_원래"].sum(), inst["옮김_새"].sum()
        vol_drop = 1 - moved_n / moved_o if moved_o else float("nan")
        print(f"\n## {pv.DAY_TYPES[dt]} — 회차 {len(inst)}개" + ("" if dt == "weekday" else " (찍기만)"))
        print(f"  수거 총량 원래 {inst['수거합_원래'].sum():,.0f}대 → 새 안 {inst['수거합_새'].sum():,.0f}대 "
              f"({inst['수거합_새'].sum() / inst['수거합_원래'].sum() - 1:+.1%}) · 수거 대여소 "
              f"{int(inst['수거곳_새'].sum()):,}곳 · 배송 총량 {inst['배송합'].sum():,.0f}대")
        print(f"  옮길 수 있는 양 min(Σ수거, Σ배송): 원래 {moved_o:,.0f}대 → 새 안 {moved_n:,.0f}대 ({-vol_drop:+.1%})")
        res = {}
        for h in HORIZONS:
            gh = g[g["지평"] == h]
            n = gh["모집단"].sum()
            r = {k: gh[f"무해_{k}"].sum() / n for k in ("원래", "새", "균등")}
            per_new = gh["무해_새"] / gh["모집단"].replace(0, np.nan)
            per_u = gh["무해_균등"] / gh["모집단"].replace(0, np.nan)
            wins, losses = int((per_new > per_u).sum()), int((per_new < per_u).sum())
            p = pv.sign_test(wins, losses)
            empty = {k: gh[f"빈시간_{k}"].sum() / n for k in ("원래", "새", "균등")}
            sat = {k: gh[f"포화남음_{k}"].sum() / n for k in ("원래", "새", "균등")}
            res[h] = (r, p)
            print(f"\n### {h // TICKS_PER_HOUR}시간 — 원래 수거 대여소 {n:,}곳")
            print(f"  무해율: 원래 {r['원래']:.1%} · **새 안 {r['새']:.1%}** · 같은 총량 균등 축소 {r['균등']:.1%}")
            print(f"    회차 부호 검정(새 안 > 균등): {wins}승 {losses}패 {len(gh) - wins - losses}무 · 단측 p = {p:.4f}")
            if len(gh) > 1:
                a, b = pv.boot_ci(gh, "무해_새", "모집단")
                c, d2 = pv.boot_ci(gh, "무해_새", "모집단", "무해_균등")
                print(f"  [인용] 95% 구간(회차 재표집 2,000번) — 새 안 무해 {a:.1%}~{b:.1%} · 새 안 − 균등 "
                      f"{c * 100:+.1f}~{d2 * 100:+.1f}%p")
            print(f"  빼 갔다면 비었을 시간(창 대비): 원래 {empty['원래']:.1%} · 새 안 {empty['새']:.1%} · 균등 {empty['균등']:.1%}")
            print(f"  수거 뒤에도 포화(관측 최고 − q ≥ 거치대 90%): 원래 {sat['원래']:.1%} · 새 안 {sat['새']:.1%} · 균등 {sat['균등']:.1%}")
        if dt == "weekday":
            v1 = res[30][0]["새"] >= SAFE_FLOOR
            v2 = all(res[h][0]["새"] > res[h][0]["균등"] and res[h][1] < SIGN_P for h in MAIN_HORIZONS)
            v3 = vol_drop <= MAX_VOLUME_DROP
            verdict = {"① 5시간 무해 ≥ 90%": v1, "② 균등 축소보다 낫다(3 · 5시간)": v2,
                       "③ 옮길 수 있는 양 감소 ≤ 20%": v3}

    print("\n## 판정 (사전 등록: 평일 — 셋 다면 '채택 후보', ②만이면 '경로 기준이 맞지만 맞바꿈이 크다')")
    for k, v in verdict.items():
        print(f"  {k}: {'✅' if v else '❌'}")
    if all(verdict.values()):
        print("  → ✅ 채택 후보 — 파이프라인 반영은 6장 시뮬레이션 · ILP로 다시 잰 뒤")
    elif verdict["② 균등 축소보다 낫다(3 · 5시간)"]:
        print("  → ◐ 경로 기준이 맞지만 맞바꿈이 크다 — 못 넘은 칸을 그대로 적는다")
    else:
        print("  → ❌ 경로 기준이 같은 양의 균등 축소보다 낫지 않다")
    return 0


if __name__ == "__main__":
    project_config.exit_if_help(__doc__)
    raise SystemExit(main())
