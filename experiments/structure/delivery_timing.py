"""배송 · 수거는 대여소가 비기(차기) 전에 도착했나 — 실제 동시각 계획의 VRP 도착 시각을 그 뒤 관측 재고와 맞댄다 (EXPERIMENTS 51장).

사용자 물음(2026-10-07) — *"각 차량당 재배치 예상 시간이 얼마나 걸려야 한다고 생각해?"* 에 *"총 소요(차고지 왕복)가
아니라 회차 시작부터 각 대여소 도착까지가 중요하고, 마지막 배송은 60분 · 늦어도 90분 안"* 이라 권했다. 근거는 둘이었다 —
배송 대상의 40.3%가 1시간 안에 빈다(KPI 9장) · 지금 계획은 배송 도착 중앙이 60~64분이고 절반이 60분 뒤에 닿는다.
그러나 둘은 **다른 대여소 묶음의 평균**이라 *"늦게 닿는 곳이 일찍 비는 곳인가"* 는 모른다. 그래서 **같은 정류장**에서 잰다.

무엇을 쓰나 — 회차 시작 시각에 실제로 세운 **동시각 계획**(kind = plan, 라벨 '… 동시각' · created_at이 회차 시작 30분 안 ·
`observed_stockout.is_same_day_plan()`)의 `vrp_plan`. 계획은 집행되지 않았으므로 관측 재고는 *"손대지 않은 세상"* 이다(47장과 같다).

사전 등록 (결과 전 커밋 — 바꾸지 않는다)
  도착     : 회차 시작 t + (cum_sec − work_sec). 차량은 t에 차고지를 떠난다고 본다(계획의 가정)
  창       : t 뒤 5시간(30틱). 그 10분 칸의 80% 이상이 관측된 정류장만 센다(47장과 같다)
  배송     : t에 0이면 '이미 빔'(따로 셈). 아니면 창 안에서 처음 0이 관측된 칸 e —
             e ≤ 도착이면 **늦음**, e > 도착이면 **제때**, 창 안에 0이 없으면 '관측상 필요 없음'
  수거     : 같은 규칙, 기준은 **꽉 참**(재고 ≥ 거치대 — step4의 포화와 같다). 거치대는 그 실행의 station_info
  지표     : ① 늦은 배송 비율 = 늦음 / (늦음 + 제때) · 늦은 수거 비율도 같이
             ② 30분 일찍 출발(도착 − 30분)이면 ①이 몇 %p 주나
             ③ 필요했던 배송(늦음 + 제때)의 빈 시각 e − t 분포 — 60 · 90 · 120분 안 비율과 중앙값 m
  판정     : 평일이 판정, 휴일은 찍기만. 표본은 **회사 PC(A)의 동시각 계획 전부**(평일 09-23 · 09-28~10-02, 휴일 추석 ·
             10월 연휴) — 이 PC(집, 21건 = 평일 5 · 휴일 16)는 **예비**로 찍기만 한다. 스크립트는 평일 동시각
             계획이 **20회차 이상**일 때만 판정 줄을 찍는다(회사 PC 평일은 09-23 둘 + 09-28~10-02 스물 = 22회차)
             · 출발 당기기: ②가 **5%p 이상**이면 *"출발을 30분 당긴다"* 를 파이프라인 후보로 올린다
             · 마지막 배송 목표: m ≤ 60분이면 **60분**, 60 < m ≤ 90분이면 **90분**, 그 밖이면 **120분(현행 목표 유지)**
               (한 마감으로 필요했던 배송의 절반 이상을 제때 대려면 마감이 m보다 앞서야 한다)
  찍기만   : 차량별 회차 시작 → 마지막 배송 · 첫 방문 → 마지막 방문 · 복귀 포함 총 소요 / 순서 상한(차량마다 같은 도착
             칸들을 e가 이른 정류장부터 다시 배정했을 때의 늦은 배송 비율 — 이동을 다시 풀지 않은 **상한**이다)

결과를 보기 전의 예상 (틀려도 고치지 않는다)
  ① 늦은 배송 35~50% · ② −10~−15%p(→ 출발 당기기 후보) · ③ m은 60~90분(→ 목표 90분) · 순서 상한은 ①을 10%p쯤 줄인다

알려진 치우침
  · 공사가 관측 기간에 실제로 채운 정류장은 덜 빈다 → '늦음'을 낮춰 잡는다(47장과 같다)
  · 계획 실행은 t+3분에 시작해 몇 분 걸린다 → 실제 출발은 t보다 늦다 → '늦음'을 낮춰 잡는다
  · 10분 칸 해상도 — 처음 0이 관측된 칸은 실제로 빈 시각보다 최대 10분 늦다 → '늦음'을 낮춰 잡는다
  · 09-28 20:03 전 동시각 계획은 깎인 대여이력으로 섰다(1.26.298) — 어느 정류장을 고르는지가 다르지, 도착과 빈 시각을
    맞대는 이 실험의 규칙은 같다. 회사 PC 본판정에서는 원본 이후 계획만 따로도 찍는다

실행:
    python experiments/structure/delivery_timing.py
    python experiments/structure/delivery_timing.py --until "2026-10-06 00:00"
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
import stockout_forecast as sf  # noqa: E402
from observed_stockout import RENTAL_SWAP_AT, is_same_day_plan  # noqa: E402

TICK = pd.Timedelta(minutes=sf.TICK_MINUTES)
WINDOW_TICKS = 30              # 5시간
MIN_OBSERVED = 0.8             # 창의 10분 칸 중 관측 비율
EARLY = pd.Timedelta(minutes=30)
SHIFT_THRESHOLD = 0.05         # ②가 이만큼(%p) 이상이면 출발 당기기를 후보로
DEADLINES = (60, 90, 120)
DAY_TYPES = {"weekday": "평일", "holiday": "휴일"}


def plan_start(label: str) -> pd.Timestamp:
    """'2026-09-28 10 평일 동시각' → 2026-09-28 10:00 (회차 시작)."""
    date_s, hour_s = label.split()[:2]
    return pd.Timestamp(f"{date_s} {int(hour_s):02d}:00")


def stops_with_arrival(vrp: pd.DataFrame, start: pd.Timestamp) -> pd.DataFrame:
    """pick · drop 행에 도착 시각을 붙인다 — 도착 = 시작 + (cum_sec − work_sec)."""
    out = vrp[vrp["action"].isin(["pick", "drop"])].copy()
    out["도착"] = start + pd.to_timedelta(out["cum_sec"] - out["work_sec"], unit="s")
    return out


def first_hit(series: pd.Series, start: pd.Timestamp, threshold: float, full: bool) -> tuple:
    """창 (start, start + 5시간]에서 처음 기준에 닿은 칸.

    반환 (상태, 시각): 상태는 '이미' · '닿음' · '안 닿음' · '관측 부족'. 배송은 재고 == 0(threshold 0, full=False),
    수거는 재고 ≥ 거치대(full=True).
    """
    s0 = series.get(start, np.nan)
    window = series[(series.index > start) & (series.index <= start + WINDOW_TICKS * TICK)]
    if pd.isna(s0) or window.notna().sum() < MIN_OBSERVED * WINDOW_TICKS:
        return "관측 부족", None
    hit = (lambda v: v >= threshold) if full else (lambda v: v <= threshold)
    if hit(s0):
        return "이미", None
    reached = window[window.notna() & hit(window)]
    if reached.empty:
        return "안 닿음", None
    return "닿음", reached.index[0]


def classify(stops: pd.DataFrame, grid: pd.DataFrame, cap: pd.Series, start: pd.Timestamp) -> pd.DataFrame:
    """정류장마다 '이미 · 늦음 · 제때 · 필요 없음 · 관측 부족'과 처음 닿은 시각."""
    rows = []
    for _, r in stops.iterrows():
        sid = r["to_id"]
        if sid not in grid.columns:
            state, when = "관측 부족", None
        elif r["action"] == "drop":
            state, when = first_hit(grid[sid], start, 0, full=False)
        else:
            c = cap.get(sid, np.nan)
            state, when = ("관측 부족", None) if not c > 0 else first_hit(grid[sid], start, c, full=True)
        if state == "닿음":
            state = "늦음" if when <= r["도착"] else "제때"
        elif state == "안 닿음":
            state = "필요 없음"
        rows.append({**r.to_dict(), "상태": state, "닿은시각": when})
    out = pd.DataFrame(rows)
    # 닿지 않은 정류장의 None이 섞이면 object 열이 되어 시각 뺄셈이 안 된다(첫 실행에서 겪었다) — NaT로 맞춘다.
    out["닿은시각"] = pd.to_datetime(out["닿은시각"])
    return out


def late_rate(frame: pd.DataFrame, shift: pd.Timedelta = pd.Timedelta(0)) -> tuple:
    """(늦음, 늦음 + 제때). shift만큼 일찍 도착했다고 보고 다시 가른다."""
    need = frame[frame["상태"].isin(["늦음", "제때"])]
    late = int((need["닿은시각"] <= need["도착"] - shift).sum())
    return late, len(need)


def oracle_reorder(frame: pd.DataFrame) -> tuple:
    """순서 상한 — 차량마다 같은 도착 칸들을, 일찍 비는 정류장부터 다시 배정했을 때의 (늦음, 필요).

    이동을 다시 풀지 않으므로 실제로 이룰 수 있는 값이 아니라 **순서만 바꿨을 때의 상한**이다.
    필요 없었던 정류장은 가장 늦은 칸으로 민다(빈 시각이 없으니 맨 뒤가 손해가 없다).
    """
    late = need = 0
    for _, g in frame[frame["action"] == "drop"].groupby(["run_label", "cluster"]):
        g = g[g["상태"] != "관측 부족"]
        slots = np.sort(g["도착"].to_numpy())
        key = g["닿은시각"].where(g["상태"].isin(["늦음", "제때"]))
        ordered = g.assign(_k=key.fillna(pd.Timestamp.max)).sort_values("_k")
        for slot, (_, r) in zip(slots, ordered.iterrows()):
            if r["상태"] in ("늦음", "제때"):
                need += 1
                late += int(r["닿은시각"] <= slot)
    return late, need


def rate(num: int, den: int) -> float:
    return num / den if den else float("nan")


def deadline_verdict(median_min: float) -> int:
    """마지막 배송 목표 — m ≤ 60이면 60, ≤ 90이면 90, 그 밖이면 120(현행)."""
    for d in DEADLINES[:-1]:
        if median_min <= d:
            return d
    return DEADLINES[-1]


def plan_input(created_at) -> str:
    """그 계획이 선 대여이력 — 회사 PC 본 DB를 원본으로 바꾼 시각(09-28 15:24)보다 먼저면 '깎임'.

    등록이 *"본판정에서는 원본 이후 계획만 따로도 찍는다"* 고 적었다(1.26.336에 채웠다).
    """
    return "원본" if pd.Timestamp(created_at) >= pd.Timestamp(RENTAL_SWAP_AT) else "깎임"


def load_plans(conn) -> pd.DataFrame:
    runs = pd.read_sql("SELECT run_label, day_type, created_at FROM runs"
                       " WHERE kind = 'plan' AND run_label LIKE '%동시각%'", conn)
    vrp = pd.read_sql("SELECT run_label, duration, cluster, seq, to_id, action, qty, cum_sec, work_sec, travel_sec"
                      " FROM vrp_plan", conn)
    vrp = vrp[vrp["run_label"].isin(runs["run_label"])]
    keep = []
    for _, r in runs.iterrows():
        for dur in vrp.loc[vrp["run_label"] == r["run_label"], "duration"].unique():
            if is_same_day_plan(r["created_at"], dur, r["day_type"]):
                keep.append((r["run_label"], dur, r["day_type"], plan_input(r["created_at"])))
    return vrp, pd.DataFrame(keep, columns=["run_label", "duration", "day_type", "입력"])


def vehicle_times(vrp: pd.DataFrame) -> pd.DataFrame:
    """차량마다 회차 시작 → 첫 방문 · 마지막 배송 · 마지막 방문 · 복귀까지(분)."""
    rows = []
    for (label, cluster), g in vrp.groupby(["run_label", "cluster"]):
        g = g.sort_values("seq")
        visit = g[g["action"].isin(["pick", "drop"])]
        drop = g[g["action"] == "drop"]
        if visit.empty:
            continue
        arrive = (visit["cum_sec"] - visit["work_sec"]) / 60
        rows.append({"run_label": label, "cluster": cluster,
                     "첫방문": arrive.iloc[0],
                     "마지막배송": ((drop["cum_sec"] - drop["work_sec"]) / 60).max() if len(drop) else np.nan,
                     "마지막방문": arrive.iloc[-1],
                     "총소요": g["cum_sec"].iloc[-1] / 60})
    out = pd.DataFrame(rows)
    out["첫→마지막"] = out["마지막방문"] - out["첫방문"]
    return out


def report(frame: pd.DataFrame, times: pd.DataFrame, title: str, judge: bool) -> None:
    print(f"\n## {title}" + ("" if judge else " — 찍기만"))
    for action, name in (("drop", "배송"), ("pick", "수거")):
        part = frame[frame["action"] == action]
        counts = part["상태"].value_counts()
        print(f"  {name} 정류장 {len(part)}: " + " · ".join(f"{k} {counts.get(k, 0)}"
                                                         for k in ("늦음", "제때", "필요 없음", "이미", "관측 부족")))
        late, need = late_rate(part)
        print(f"    ① 늦은 {name} 비율 {rate(late, need):.1%} ({late}/{need})")
        if action == "drop":
            late_e, _ = late_rate(part, EARLY)
            gain = rate(late, need) - rate(late_e, need)
            print(f"    ② 30분 일찍 출발하면 {rate(late_e, need):.1%} — {gain * 100:+.1f}%p 준다"
                  + (f" → {'✅ 출발 당기기 후보' if gain >= SHIFT_THRESHOLD else '❌ 5%p 미만'}" if judge else ""))
            need_df = part[part["상태"].isin(["늦음", "제때"])]
            if len(need_df):
                minutes = (need_df["닿은시각"] - need_df["run_label"].map(plan_start)).dt.total_seconds() / 60
                share = " · ".join(f"{d}분 안 {(minutes <= d).mean():.0%}" for d in DEADLINES)
                m = float(np.median(minutes))
                print(f"    ③ 필요했던 배송이 빈 시각(회차 시작부터): {share} · 중앙 {m:.0f}분"
                      + (f" → 마지막 배송 목표 **{deadline_verdict(m)}분**" if judge else ""))
            o_late, o_need = oracle_reorder(part)
            print(f"    순서 상한(일찍 비는 곳부터, 이동은 그대로): {rate(o_late, o_need):.1%} ({o_late}/{o_need})")
    if not times.empty:
        q = times.describe().loc[["50%", "max"]]
        print("  차량별(분, 중앙 · 최대): " + " · ".join(
            f"{c} {q.loc['50%', c]:.0f} · {q.loc['max', c]:.0f}"
            for c in ("첫방문", "마지막배송", "마지막방문", "첫→마지막", "총소요")))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--until", help="이 시각 전의 관측만 쓴다(예: '2026-10-06 00:00')")
    args = parser.parse_args(argv)

    with db.session() as conn:
        vrp, plans = load_plans(conn)
        raw = sf.load_grid(conn)
        caps = {}
        for label in plans["run_label"].unique():
            info = db.load_frame(conn, "station_info", run_label=label)
            caps[label] = pd.to_numeric(info.drop_duplicates("station_id").set_index("station_id")["parking_lot"],
                                        errors="coerce")
    if args.until:
        raw = raw[raw["ts"] < pd.Timestamp(args.until)]
    grid = raw.pivot_table(index="ts", columns="station_id", values="stock")
    grid = grid.reindex(pd.date_range(grid.index.min().floor("D"), grid.index.max().ceil("D"),
                                      freq=TICK, inclusive="left"))
    print(f"관측 {grid.index.min()} ~ {raw['ts'].max()} · 동시각 계획 {len(plans)}회차"
          f" (평일 {int((plans['day_type'] == 'weekday').sum())} · 휴일 {int((plans['day_type'] == 'holiday').sum())})")

    frames, timings = [], []
    for _, p in plans.iterrows():
        start = plan_start(p["run_label"])
        sub = vrp[(vrp["run_label"] == p["run_label"]) & (vrp["duration"] == p["duration"])]
        stops = stops_with_arrival(sub, start)
        got = classify(stops, grid, caps[p["run_label"]], start)
        got["day_type"] = p["day_type"]
        got["입력"] = p["입력"]
        frames.append(got)
        times = vehicle_times(sub)
        times["day_type"] = p["day_type"]
        times["입력"] = p["입력"]
        timings.append(times)
    frame = pd.concat(frames, ignore_index=True)
    times = pd.concat(timings, ignore_index=True)

    # 판정 표본은 회사 PC의 동시각 계획 전부다 — 이 PC(평일 5회차)는 예비라 judge를 끈다.
    judge = len(plans[plans["day_type"] == "weekday"]) >= 20
    for dt, name in DAY_TYPES.items():
        report(frame[frame["day_type"] == dt],
               times[times["day_type"] == dt].drop(columns=["day_type", "입력"]),
               f"{name} {int((plans['day_type'] == dt).sum())}회차", judge and dt == "weekday")
    # 등록: 본판정에서는 원본 대여이력 뒤에 선 계획만 따로도 찍는다 — 판정은 위의 전체로만 한다.
    fresh = plans[(plans["day_type"] == "weekday") & (plans["입력"] == "원본")]
    if judge and 0 < len(fresh) < int((plans["day_type"] == "weekday").sum()):
        pick = (frame["day_type"] == "weekday") & (frame["입력"] == "원본")
        tpick = (times["day_type"] == "weekday") & (times["입력"] == "원본")
        report(frame[pick], times[tpick].drop(columns=["day_type", "입력"]),
               f"평일 중 원본 대여이력 뒤 {len(fresh)}회차", False)
    if not judge:
        print("\n⚠️ 평일 동시각 계획이 20회차 미만이라 **예비**다 — 판정은 회사 PC의 계획 전부로 한다(사전 등록).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
