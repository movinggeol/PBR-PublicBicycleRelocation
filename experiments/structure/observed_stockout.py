"""결품을 계산이 아니라 **관측**으로 센다 (TODO 1-4).

지금 `pipeline/step4_metrics/imbalance.py`의 결품 시간은 **복원**입니다.

    stock(t+1) = clip(stock(t) − net(t), 0, 거치대 수)

초기 재고는 실행 시점 스냅샷 **한 장**이고, 그 뒤의 궤적은 **지난달 순수요**로
만들어 냅니다. 그래서 두 가지 한계가 있었습니다.

  · 초기 재고(오늘)와 순수요(지난달)의 **시점이 다르다**
  · 0에서 잘라 내므로 못 빌린 수요가 사라져 **결품의 하한**이다

1.20.0부터 재고를 10분 간격으로 모으고 있으므로([COLLECTOR.md](../../docs/구현/COLLECTOR.md)),
이제 그 궤적을 **실측으로 대체**할 수 있습니다. 이 스크립트가 하는 일은 하나입니다.

    관측된 재고에서 "몇 시간 동안 빌릴 수 없었나 / 반납할 수 없었나"를 직접 센다.

**복원값과 맞대어 보는 것이 목적입니다.** 복원이 결품을 얼마나 낮춰 잡고 있었는지
알면, 지금까지의 성과 수치를 어떻게 읽어야 하는지가 정해집니다.

🔴 **맞대려면 짝이 맞아야 합니다.** 복원은 그 요일 구분으로 계획한 **계획 실행**만
쓰고(1.26.270), 모집단은 **복원값을 낸 실행마다 그 실행의 작업 대상**을 씁니다
(1.26.274). 한쪽만 고치면 휴일로 돌려도 평일 실험의 대여소로 실측을 재게 됩니다 —
실제로 그랬고, 겹치는 곳이 41%뿐이었습니다.

⚠️ **차이를 '복원의 오차' 하나로 읽으면 안 됩니다.** 편향이 셋입니다.

  · 복원의 0 절단 — 못 빌린 수요가 사라져 결품을 **낮춰** 잡는다
  · 실측에 섞인 공사의 실제 재배치 — 채워 준 만큼 결품이 **줄어** 있다
  · 지난달 순수요로 이번 달을 맞히는 **추정 오차** — 어느 방향으로도 걸린다

셋째 때문에 휴일 `_05_10`은 실측이 오히려 **35% 작습니다**(2026-09-22). 같은 234곳을
평일로 재면 1.72시간이고 휴일로 재면 1.21시간이므로 모집단이 아니라 **날** 때문이며,
휴일 새벽에는 자전거가 밤새 나가지 않아 만차 시간이 결품 시간과 맞먹습니다.

실행:
    python experiments/structure/observed_stockout.py
    python experiments/structure/observed_stockout.py --duration _10_15
    python experiments/structure/observed_stockout.py --day-type holiday --save
    python experiments/structure/observed_stockout.py --day-type holiday --same-day
"""
import argparse
from datetime import date, datetime, time as clock, timedelta
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd

import db
from project_config import DURATIONS, REBAL_MIN_QTY, duration_hours, is_holiday

# 수집 간격(분). 한 틱이 대표하는 시간이 곧 결품 시간의 단위다.
TICK_MINUTES = 10

# 그 날(또는 그 회차)이 '촘촘하다'고 볼 기준 — **잰 창의 비율로 판정한다.**
#
# ⚠️ 예전에는 `MIN_TICKS_PER_DAY = 40`으로 박아 두었다. 09~17시(49틱) 기준의
# 82%였는데, 1.25.1에서 수집 창이 07~22시(91틱)로 넓어지자 **44%가 됐다** —
# 반나절만 모인 날이 통과했다. 창은 앞으로도 바뀌므로 **상수 대신 비율**이다.
#
# 🔴 비율로 바꾼 뒤에도 함정이 하나 남아 있었다(1.26.153에서 제거). 분모를
# **가장 넓게 모인 하루**에서 구해 모든 날에 들이댔더니, 넓은 날이 하나
# 들어올 때마다 좁은 창의 촘촘한 날들이 **소급해 탈락**했다. 이제 분모는
# 언제나 **재는 대상 자신의 창**이다 — 날은 그날의 창, 회차는 그 회차의 시간대.
COMPLETE_DAY_RATIO = 0.8

# `--same-day`가 "회차 시작 시각에 세운 계획"으로 인정하는 늦음(분). 그보다 늦게 세운
# 계획은 출발 재고가 회차 시작의 상태가 아니므로 뺀다(1.26.278).
SAME_DAY_TOLERANCE_MIN = 30

# ── 같은 시각 비교의 판정 — **자료를 보기 전에** 정했다 (2026-09-23, 1.26.285) ──────────
# 첫 동시각 계획이 서기 전(09-23 15:03 · 추석 09-24 05:03)에 적었다. 결과를 보고 문턱을
# 옮기지 마라 — 모자라면 날을 더 모으는 것이 답이다(EXPERIMENTS 32장 '사전 등록').
#
# 출발 빈 곳(계획 스냅샷)과 실제 빈 곳(회차 시작 정각의 관측)의 차가 이 안이어야 **출발 재고를
# 맞춘 실행**이다. 오후 스냅샷으로 세운 계획은 3.9~23.7%p 어긋났다.
SAME_DAY_START_MATCH_PP = 5.0
# 한 칸(요일 × 회차)을 판정하는 데 필요한 날 — 창이 차고 출발이 맞은 날만 센다.
SAME_DAY_MIN_DAYS = 3
# 비교 기준 — **다른 날로 잰** 여덟 칸의 차이(관측 ÷ 복원 − 1, %). 1.26.274 · 원고 8.3.
SAME_DAY_OLD_GAPS = {
    ("weekday", "_05_10"): 12, ("weekday", "_10_15"): 21,
    ("weekday", "_15_20"): 46, ("weekday", "_20_05"): 92,
    ("holiday", "_05_10"): -35, ("holiday", "_10_15"): 42,
    ("holiday", "_15_20"): 70, ("holiday", "_20_05"): 79,
}
# 휴일은 10월 연휴의 마지막 날(10-11)까지 받으면 확정한다. 그 전의 판정(추석 나흘 등)은 잠정이다.
SAME_DAY_HOLIDAY_FINAL = date(2026, 10, 11)

# 창을 못 읽었을 때의 최소 안전선(틱). 하루에 이만큼도 없으면 어떤 비율이든 볼 것이 없다.
MIN_TICKS_FLOOR = 12


def load(day_type: str) -> pd.DataFrame:
    with db.session() as conn:
        frame = db.load_stock_history(conn, day_type=day_type)
        parking = pd.read_sql("SELECT station_id, parking_lot FROM parking_lot", conn)
    if frame.empty:
        return frame
    frame["관측"] = pd.to_datetime(frame["observed_at"])
    frame["날짜"] = frame["관측"].dt.strftime("%Y-%m-%d")
    frame["시각"] = frame["관측"].dt.hour
    # 거치대는 대여소당 하나이므로 중복을 걷어낸다(수집일마다 쌓일 수 있다).
    parking = parking.drop_duplicates("station_id")
    return frame.merge(parking, on="station_id", how="left")


def dense_days(frame: pd.DataFrame) -> list:
    """**자기 창 안에서** 촘촘히 모인 날만 고른다 (1.26.153).

    ⚠️ **하루 전체를 판정하지 않는다.** 예전 `complete_days()`는 *가장 넓게
    모인 하루*로 잣대를 세워 **모든 날에 그 잣대를 들이댔다.** 그래서 잣대가
    자료를 더하면 움직였다 — 실측에서 8/31(07~22시)이 들어오자 기대가
    49 → 91틱으로 뛰며 8/25~28이 **소급해서 탈락**했다(4일 → 1일). 그 네 날은
    반쪽짜리가 아니다. 09~17시 창 안에서 **결측 0으로 100% 촘촘하다.**

    그래서 각 날을 **그날 실제로 돈 창**에 대고 잰다. 이 판정은 나중에 더 넓은
    날이 들어와도 뒤집히지 않는다. 대신 *"하루가 온전한가"* 는 더 이상 묻지
    않는다 — 그 물음은 **회차 단위**(`duration_complete_days()`)가 답하고,
    수집 창 자체의 결손은 `tools/collect_stock.py --status`가 **등록된 창**에
    대고 답한다(둘은 다른 물음이다, COLLECTOR.md 6장).
    """
    if frame.empty:
        return []
    span = frame.groupby("날짜")["관측"].agg(["min", "max", "nunique"])
    minutes = (span["max"] - span["min"]).dt.total_seconds() / 60
    expected = (minutes // TICK_MINUTES).astype(int) + 1
    need = (expected * COMPLETE_DAY_RATIO).astype(int).clip(lower=MIN_TICKS_FLOOR)
    return sorted(span.index[span["nunique"] >= need])


def duration_complete_days(frame: pd.DataFrame, hours: list) -> list:
    """**그 회차를 온전히 덮은 날**만 고른다 (2026-09-01).

    날이 촘촘한 것(`dense_days()`)과 **그 회차가 덮인 것**은 다른 물음이다.
    09~17시에 결측 0으로 모은 날은 촘촘하지만 `_15_20`은 한 시간도 못 덮는다.
    회차 비교에 필요한 것은 뒤쪽이므로 여기서는 **그 회차의 시간대만** 본다.
    """
    if frame.empty or not hours:
        return []
    part = frame[frame["시각"].isin(hours)]
    if part.empty:
        return []
    per_hour = 60 // TICK_MINUTES
    need = max(int(len(hours) * per_hour * COMPLETE_DAY_RATIO), 1)
    ticks = part.groupby("날짜")["관측"].nunique()
    return sorted(ticks[ticks >= need].index)


def usable_by_duration(frame: pd.DataFrame, durations: list, window) -> dict:
    """회차마다 **쓸 수 있는 날**과 창이 회차를 통째로 덮는지를 돌려준다.

    `{회차: (날 목록, 창이_일부인가)}`. 머리기사가 이것을 써야 하는 이유는
    날의 촘촘함이 **회차가 덮였는지를 말해 주지 못하기** 때문이다 — 09~17시로
    촘촘한 날도 `_15_20`은 못 덮는다. 실측에서도 `_10_15`가 6일인데 머리기사는
    1일이라 적고 있었다(1.26.123에서 옮겼다).

    창이 회차를 통째로 덮지 못하면 날이 아무리 많아도 복원과 맞댈 수 없다
    (`compare_with_simulation`이 건너뛴다). 날 수만 적으면 쓸 수 있는 줄 안다.
    """
    usable = {}
    for duration in durations:
        whole = duration_hours(duration)
        inside = [h for h in whole if h in window]
        usable[duration] = (duration_complete_days(frame, inside),
                            len(inside) < len(whole))
    return usable


def format_usable(usable: dict) -> str:
    """`_10_15 6일 · _05_10 1일(창 일부)` 꼴로 적는다."""
    return " · ".join(
        f"{duration} {len(days)}일" + ("(창 일부)" if partial else "")
        for duration, (days, partial) in usable.items())


def observed_hours(frame: pd.DataFrame, hours: list) -> pd.DataFrame:
    """대여소·날짜별로 **빌릴 수 없던 시간**과 **반납할 수 없던 시간**을 센다.

    한 틱 = TICK_MINUTES분으로 환산한다. 결품(재고 0)과 만차(재고 ≥ 거치대)를 따로
    세는 이유는 둘의 처방이 반대이기 때문이다 — 전자는 채워야 하고 후자는 빼내야 한다.
    """
    part = frame[frame["시각"].isin(hours)].copy()
    if part.empty:
        return part
    part["빈"] = part["stock"] <= 0
    part["찬"] = part["parking_lot"].notna() & (part["stock"] >= part["parking_lot"])

    grouped = part.groupby(["station_id", "날짜"])
    result = grouped.agg(틱=("관측", "size"),
                         빈틱=("빈", "sum"),
                         찬틱=("찬", "sum"),
                         평균재고=("stock", "mean")).reset_index()
    result["결품시간"] = result["빈틱"] * TICK_MINUTES / 60
    result["만차시간"] = result["찬틱"] * TICK_MINUTES / 60
    result["관측시간"] = result["틱"] * TICK_MINUTES / 60
    return result


def is_plan_run(run_label: str, kind: str = None) -> bool:
    """이 실행이 **계획**인가. 복원과 모집단이 같은 잣대를 쓰게 하는 한 곳이다.

    `runs.kind`가 비어 있는 옛 실행만 `db.classify_run_label()`의 짐작에
    맡긴다 — 선언이 없으면 NULL로 두는 것이 1.26.114의 설계다.
    """
    return (kind or db.classify_run_label(run_label)) == "plan"


def target_stations(run_label: str, duration: str = None) -> set:
    """**그 실행이** 실제로 손대는 대여소. 전체 평균은 결론을 흐린다.

    **회차마다 대상이 다르다**(2026-09-01 발견). 예전에는 `MAX(run_label)`
    하나로 모든 회차를 재서, **`_10_15`의 결품을 `_15_20`의 대상으로** 재는
    일이 생겼다 — 실측 245 / 304 / 260곳으로 세 회차가 모두 다르다.

    🔴 **실행을 반드시 지정한다**(1.26.274). 회차는 갈랐지만 실행은 그 뒤로도
    `MAX(run_label)`로 짐작했는데, 그것은 **문자열 최대**라 요일도 실행 종류도
    가리지 않았다. 실측으로 `_05_10`·`_10_15`·`_15_20`은 `sweep-21`(평일 γ
    실험), `_20_05`는 `brokenmix4-2603`(kind=experiment)을 집고 있었다. 그래서
    `--day-type holiday`로 돌려도 모집단은 **평일 실험의 작업 대상**이었고,
    휴일 계획의 실제 대상 281곳과 겹치는 곳이 115곳(41%)뿐이었다.

    복원 쪽만 요일·종류로 가리게 고친 1.26.270이 **짝을 반쪽만 맞춘** 셈이다.
    짐작하는 분기를 남겨 두면 같은 자리에서 세 번째가 나므로 아예 없앴다 —
    부르는 쪽이 어느 실행인지 말해야 한다.
    """
    sql = "SELECT station_id, rebal_qty FROM rebalance_plan WHERE run_label = ?"
    params = [run_label]
    if duration:
        sql += " AND duration = ?"
        params.append(duration)
    with db.session() as conn:
        plan = pd.read_sql(sql, conn, params=params)
    if plan.empty:
        return set()
    return set(plan.loc[plan["rebal_qty"].abs() > REBAL_MIN_QTY, "station_id"])


def plan_start_stock(run_label: str, duration: str) -> pd.Series:
    """그 계획이 **실제로 쓴** 출발 재고(대여소별) — `rebalance_plan.stock`에서 읽는다 (1.26.286).

    `station_stock`이 아니다. `--skip-api`로 스냅샷을 물려받은 실행은 그 표에 **자기 행이
    없어서**, 1.26.278이 여덟 칸 가운데 평일 다섯 자리의 출발 재고를 재지 못했다. 계획표에는
    그 계획이 쓴 재고가 회차마다 남는다 — 두 표를 맞대 보니 스냅샷을 직접 뜬 실행에서 한 행도
    다르지 않았다(2026-09-23, `2026-09-22 휴일 전회차` 5,376행 중 0).
    """
    with db.session() as conn:
        plan = pd.read_sql("SELECT station_id, stock FROM rebalance_plan"
                           " WHERE run_label = ? AND duration = ?",
                           conn, params=[run_label, duration])
    return plan.drop_duplicates("station_id").set_index("station_id")["stock"]


def start_empty_share(run_label: str, duration: str, own: set) -> float:
    """계획의 출발점에서 작업 대상 가운데 빈 곳(재고 ≤ 0)의 비율(%)."""
    stock = plan_start_stock(run_label, duration)
    stock = stock[stock.index.isin(own)]
    return float((stock <= 0).mean() * 100) if len(stock) else float("nan")


def real_start_empty(frame: pd.DataFrame, days: list, hour: int, own: set) -> float:
    """그 날들의 **회차 시작 시각 첫 틱**에 작업 대상 가운데 빈 곳의 비율(%) — 날마다 재고 평균."""
    part = frame[frame["날짜"].isin(days) & (frame["시각"] == hour)
                 & frame["station_id"].isin(own)]
    if part.empty:
        return float("nan")
    first = part[part["관측"] == part.groupby("날짜")["관측"].transform("min")]
    return float(((first["stock"] <= 0).groupby(first["날짜"]).mean() * 100).mean())


def latest_plan_targets(day_type: str, duration: str) -> set:
    """그 요일 구분으로 **계획**한 가장 최근 실행의 작업 대상.

    개요 표에만 쓴다. 비교표는 이것을 쓰지 않는다 — 거기서는 복원값을 낸
    실행마다 **그 실행의** 대상으로 짝지어 잰다(`compare_with_simulation`).
    """
    with db.session() as conn:
        runs = pd.read_sql(
            "SELECT r.run_label, r.kind FROM runs r"
            " WHERE r.day_type = ?"
            "   AND EXISTS (SELECT 1 FROM rebalance_plan p"
            "                WHERE p.run_label = r.run_label"
            "                  AND p.duration = ?)"
            " ORDER BY r.created_at DESC", conn, params=[day_type, duration])
    for label, kind in zip(runs["run_label"], runs["kind"]):
        if is_plan_run(label, kind):
            return target_stations(label, duration)
    return set()


def simulated_stockout(day_type: str) -> pd.DataFrame:
    """step4가 남긴 **복원** 결품(재배치 전). 실측과 맞대어 볼 상대다.

    🔴 **요일 구분과 실행 종류를 가린다** (1.26.270). 예전에는 `kpi_summary`를
    통째로 평균했다 — `runs`에 `day_type`·`kind`가 다 있는데 둘 다 읽지 않았다.
    그래서 `--day-type holiday`로 돌려도 **평일 복원과 맞대고 있었고**, 평균에
    `z165`·`g2000`·`sweep-*`·`brokenmix-*` 같은 **파라미터를 바꾼 실험**까지
    섞여 기준선을 흔들었다. 요일을 바꿔도 복원 열이 한 자리도 안 변하는 것이
    그 증거였다.

    `db.run_day_type()`의 주석이 적어 둔 1.26.127과 **같은 실패**다 — 정답은
    DB에 저장돼 있는데 분석 쪽이 읽지 않았다.

    `kind`가 비어 있는 옛 실행은 `db.classify_run_label()`의 짐작에 맡긴다.
    `day_type`이 비어 있는 실행은 **뺀다** — 어느 요일로 계획했는지 알 수 없는
    값을 요일별 비교에 넣을 수는 없다.

    🔴 **실행별로 한 행씩 돌려준다**(1.26.274). 예전에는 여기서 회차별로
    평균해 버렸는데, 그러면 복원은 실행 여럿의 평균인데 모집단은 집합 하나가
    되어 **자가 어긋난다.** 짝은 부르는 쪽이 실행 단위로 맞춘다.
    """
    with db.session() as conn:
        frame = pd.read_sql(
            "SELECT k.run_label, k.duration, k.stockout_hours_before AS 복원,"
            "       r.kind, r.day_type"
            "  FROM kpi_summary k JOIN runs r ON r.run_label = k.run_label"
            " WHERE k.stockout_hours_before IS NOT NULL", conn)
    if frame.empty:
        return frame
    frame = frame[frame["day_type"] == day_type]
    if frame.empty:
        return frame
    kind = frame["kind"].fillna("").map(
        lambda x: x or None)
    frame = frame[[
        is_plan_run(lab, k)
        for k, lab in zip(kind, frame["run_label"])]]
    if frame.empty:
        return frame
    labels = sorted(frame["run_label"].unique())
    print(f"\n복원 기준 — 요일 {day_type} · 계획 실행 {len(labels)}건: "
          f"{', '.join(labels)}")
    return frame[["run_label", "duration", "복원"]].reset_index(drop=True)


def compare_with_simulation(frame: pd.DataFrame, durations: list, window,
                            day_type: str) -> list:
    """복원과 실측을 나란히 놓는다 — **시간대가 온전히 겹칠 때만.**

    좁은 창(09~17시)에서는 `_05_10`이 09시 한 시각만, `_15_20`이 15시 한 시각만
    겹친다. 그 상태로 5시간 창의 복원값과 비교하면 실측이 5분의 1로 보인다.
    **겹치는 시각이 창 전체일 때만** 비교를 찍는 이유다.

    ⚠️ **두 값은 서로 다른 세상을 잰다.** 복원은 `stockout_hours_before`(재배치
    **전**)이고, 실측에는 **대전교통공사가 실제로 돌린 재배치**가 이미 반영돼
    있다(우리 계획이 아니다 — [ML_OPPORTUNITIES.md] ② 참고). 편향이 반대
    방향으로 둘이라 차이를 '복원의 오차' 하나로 읽으면 안 된다.

      · 복원은 재고를 0에서 자른다 → 못 빌린 수요가 사라져 **결품을 낮춰 잡는다**
      · 실측은 공사 차량이 채워 준 만큼 **결품이 줄어 있다**

    두 편향이 상쇄돼 우연히 비슷해 보일 수도 있다. **가른 뒤에야 정확한 비교가
    된다**(TODO 17-B 3단계).
    """
    simulated = simulated_stockout(day_type)
    if simulated.empty:
        print("\n(복원 대비 비교: 이 요일 구분으로 계획한 실행이 DB에 없습니다 —"
              " 먼저 `python run_pipeline.py --day-type ...`을 돌리십시오)")
        return []
    rows, starts = [], []
    for duration in durations:
        hours = duration_hours(duration)
        overlap = [h for h in hours if h in window]
        if len(overlap) != len(hours):          # 창이 통째로 겹칠 때만
            continue
        # **그 회차를 온전히 덮은 날만 쓴다**(2026-09-01). 창이 날마다 다르면
        # 하루 전체 판정으로는 반쪽짜리 회차가 섞여 결품이 과소 집계된다.
        ok_days = duration_complete_days(frame, overlap)
        if not ok_days:
            continue
        daily = observed_hours(frame[frame["날짜"].isin(ok_days)], overlap)
        if daily.empty:
            continue
        per_day = daily.groupby("station_id")["결품시간"].mean()
        runs = simulated[simulated["duration"] == duration]
        if per_day.empty or runs.empty:
            continue
        # 🔴 **실행별로 짝지어 잰 뒤 평균한다** (1.26.274). 복원이 실행 여럿의
        # 평균인데 모집단이 집합 하나면 자가 어긋난다 — 실행마다 **그 실행의**
        # 작업 대상으로 실측을 재고, 그 다음에 평균한다.
        paired = []
        for label, sim in zip(runs["run_label"], runs["복원"]):
            own = target_stations(label, duration)
            if not own:
                print(f"  ⚠️  {duration} {label}: 그 실행의 작업 대상이"
                      f" rebalance_plan에 없어 건너뜁니다")
                continue
            sub = per_day.loc[per_day.index.isin(own)]
            if sub.empty:
                print(f"  ⚠️  {duration} {label}: 작업 대상 {len(own):,}곳 가운데"
                      f" 온전히 관측된 곳이 없어 건너뜁니다")
                continue
            paired.append((label, float(sim), float(sub.mean()), len(sub)))
            starts.append((duration, label, start_empty_share(label, duration, own),
                           real_start_empty(frame, ok_days, hours[0], own)))
        if not paired:
            continue
        n_runs = len(paired)
        rows.append((duration,
                     sum(p[1] for p in paired) / n_runs,
                     sum(p[2] for p in paired) / n_runs,
                     round(sum(p[3] for p in paired) / n_runs),
                     len(ok_days), paired))

    if not rows:
        print("\n(복원 대비 비교: 수집 창과 온전히 겹치는 시간대가 아직 없습니다)")
        return []

    print(f"\n복원 vs 실측 — 작업 대상 대여소, 대여소·일 평균 결품 시간(h)")
    print("  실행마다 **그 실행의** 작업 대상으로 재고 나서 평균했습니다"
          " (1.26.274). `대여소`는 그 평균입니다.")
    print(f"{'시간대':8} {'복원(step4)':>11} {'실측(수집)':>11} {'차이':>9} "
          f"{'대여소':>7} {'날':>4} {'실행':>4}")
    for duration, sim, obs, count, ndays, paired in rows:
        gap = (obs / sim - 1) * 100 if sim else float("nan")
        print(f"{duration:8} {sim:11.2f} {obs:11.2f} {gap:+8.0f}% {count:7,d}"
              f" {ndays:4d} {len(paired):4d}")
        if len(paired) > 1:          # 평균 뒤에 숨지 않게 실행별로 펼친다
            for label, one_sim, one_obs, n_st in paired:
                one_gap = (one_obs / one_sim - 1) * 100 if one_sim else float("nan")
                print(f"      · {label}: 복원 {one_sim:.2f} · 실측 {one_obs:.2f}"
                      f" ({one_gap:+.0f}%) · {n_st:,}곳")
    if starts:
        # 복원은 계획을 세운 시각의 재고에서 출발한다(EXPERIMENTS 32장) — 출발이 실제보다
        # 비어 있으면 복원이 결품 쪽으로 기운다. 실행마다 찍는다(1.26.286).
        print("\n출발 재고 — 작업 대상 가운데 빈 곳: 계획이 쓴 재고 vs 그 날들의 회차 시작 첫 틱")
        print(f"{'시간대':8} {'출발 빈곳':>9} {'실제 빈곳':>9} {'차':>8}   실행")
        for duration, label, snap, real in starts:
            print(f"{duration:8} {snap:8.1f}% {real:8.1f}% {snap - real:+7.1f}%p   {label}")
    print("  → 실측이 크면 **복원이 결품을 낮춰 잡고 있었다**는 뜻입니다"
          "(0에서 잘라 못 빌린 수요가 사라지므로 예상된 방향입니다).")
    print("  ⚠️ 다만 실측에는 **공사가 실제로 돌린 재배치**가 이미 반영돼 있어")
    print("     그만큼 결품이 줄어 있습니다 — 두 편향이 반대 방향이라 차이를")
    print("     '복원의 오차'로만 읽으면 안 됩니다(TODO 17-B 3단계에서 가릅니다).")
    return rows


def _save_calibration(rows: list, day_type: str, window) -> None:
    """보정 계수를 DB에 남긴다 (1.26.1, 수정안 37).

    **왜 저장하는가 — 수집이 멈춰도 계수는 남기 위해서다.**
    *"재고 조사를 멈추면 사라지는 설득력"* 이라는 지적에 대한 답이다. 관측 자체는
    수집을 켜 둔 동안만 쌓이지만, 여기서 얻은 **복원 대비 보정비**는 표에 남아
    나중에도 인용할 수 있다. 다만 `days`(근거가 된 온전한 날 수)를 함께 남겨
    **얼마나 얇은 근거인지 숨기지 않는다.**

    ⚠️ 계수를 파이프라인이 자동으로 곱하지는 않는다. 지금은 3일치라 그럴 근거가
    없다 — 표에 쌓아 두고 추세를 보는 단계다.
    """
    if not rows:
        print("\n보정 계수를 낼 수 있는 시간대가 없습니다 — 저장하지 않았습니다.")
        return

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    hours = sorted(int(h) for h in window)
    note = f"관측 창 {hours[0]:02d}~{hours[-1]:02d}시"
    payload = []
    for duration, simulated, observed, stations, ndays, paired in rows:
        payload.append({
            "measured_at": stamp, "duration": duration, "day_type": day_type,
            "ratio": (observed / simulated) if simulated else None,
            "observed": observed, "simulated": simulated,
            # **회차별 날 수**를 쓴다(2026-09-01) — 하루 전체 판정의 값 하나를
            # 모든 회차에 붙이면 얼마나 얇은 근거인지 가려진다.
            "days": ndays, "stations": stations,
            # **짝지은 계획 실행 수**를 함께 남긴다 (1.26.274) — 실행 1건에
            # 기댄 값인지 여럿의 평균인지가 계수를 읽는 데 필요하다.
            "note": f"{note} · 계획 실행 {len(paired)}건",
        })

    with db.session() as conn:
        db.init_schema(conn)
        db.save_stockout_calibration(conn, payload)

    print(f"\n보정 계수 {len(payload)}건을 남겼습니다 (measured_at={stamp}).")
    for row in payload:
        ratio = row["ratio"]
        print(f"  {row['duration']:8} 보정비 {ratio:.3f}"
              f"  (실측 {row['observed']:.2f} / 복원 {row['simulated']:.2f},"
              f" 온전한 날 {row['days']}일)")
    # 가장 얇은 **회차**로 경고한다 (2026-09-05). 하루 전체 판정의 값 하나로
    # 재면 근거를 실제보다 얇게 말한다 — `_10_15`가 6일인데 "1일뿐"이라고 했다.
    thinnest = min(payload, key=lambda row: row["days"])
    if thinnest["days"] < 14:
        print(f"  ⚠️  근거가 가장 얇은 회차는 {thinnest['duration']}"
              f" {thinnest['days']}일입니다. 논문에 인용할 때 함께 적으십시오.")


def same_day_runs(day_type: str, run_label: str = None) -> pd.DataFrame:
    """**회차 시작 시각에 세운** 계획 실행 — 출발 재고가 그날 그 시각의 실제 상태인 것 (1.26.278).

    왜 따로 고르나 — 복원은 계획을 세운 시각의 재고에서 출발한다. 오후에 세운 계획으로
    새벽 회차를 복원하면 출발부터 어긋난다(EXPERIMENTS 32장: 휴일 `_05_10` 작업 대상의
    빈 곳이 출발점 45.7% · 실제 05시 22.0%). 여기서는 `runs.created_at`이 그 회차 시작
    시각부터 `SAME_DAY_TOLERANCE_MIN`분 안인 실행만 남긴다. 그날이 요일 구분과 맞는지도
    본다 — 휴일 계획을 평일 날 세웠으면 그날의 관측은 휴일이 아니다.

    `run_label`을 주면 그 실행 하나를 **늦음과 요일을 가리지 않고** 돌려준다(점검용).
    늦은 만큼 출발 재고가 어긋난다는 것은 표가 함께 찍는다.
    """
    with db.session() as conn:
        frame = pd.read_sql(
            "SELECT k.run_label, k.duration, k.stockout_hours_before AS 복원,"
            "       r.kind, r.day_type, r.created_at"
            "  FROM kpi_summary k JOIN runs r ON r.run_label = k.run_label"
            " WHERE k.stockout_hours_before IS NOT NULL", conn)
    if run_label:
        frame = frame[frame["run_label"] == run_label]
    else:
        frame = frame[frame["day_type"] == day_type]
        frame = frame[[is_plan_run(lab, k or None)
                       for lab, k in zip(frame["run_label"], frame["kind"])]]
    keep = []
    for row in frame.itertuples(index=False):
        made = pd.Timestamp(row.created_at).to_pydatetime()
        start = datetime.combine(made.date(), clock(duration_hours(row.duration)[0]))
        late = (made - start).total_seconds() / 60
        if not run_label:
            if not 0 <= late <= SAME_DAY_TOLERANCE_MIN:
                continue
            if is_holiday(start.date()) != (day_type == "holiday"):
                continue
        keep.append({"run_label": row.run_label, "duration": row.duration,
                     "복원": row.복원, "start": start, "late_min": late})
    return pd.DataFrame(keep)


def same_day_window(start: datetime, duration: str) -> tuple:
    """관측 창 `[회차 시작, 회차 시작 + 회차 길이)`. `_20_05`는 다음 날 05시에 닫힌다."""
    return start, start + timedelta(hours=len(duration_hours(duration)))


def same_day_usable(row: dict) -> str:
    """판정에 넣을 수 있으면 빈 문자열, 아니면 **뺀 까닭**을 돌려준다 (1.26.285)."""
    if not row["complete"]:
        return "창이 덜 찼다"
    snap, real = row["snap_empty"], row["real_empty"]
    if pd.isna(snap) or pd.isna(real):
        return "출발 재고를 확인할 수 없다"
    if abs(snap - real) > SAME_DAY_START_MATCH_PP:
        return f"출발이 {snap - real:+.1f}%p 어긋났다"
    return ""


def same_day_verdict(day_type: str, duration: str, gap: float, days: int) -> str:
    """한 칸의 판정. 규칙은 **자료를 보기 전에** 정했다 (1.26.285, EXPERIMENTS 32장).

    - 날이 `SAME_DAY_MIN_DAYS`보다 적으면 판정하지 않는다.
    - 다른 날로 잰 차이가 음수였던 칸(휴일 `_05_10` −35%)은 **원인**을 묻는다 — 출발 재고를
      맞추자 부호가 바뀌거나 차이가 절반 넘게 줄면 출발 재고가 주 원인이고, 아니면 출발
      재고로는 설명되지 않는다(남는 것은 지난달 순수요 · 0 절단 · 공사의 실제 재배치).
    - 양수였던 일곱 칸은 **방향**을 묻는다 — 관측이 여전히 크면 8.3의 서술이 서고, 아니면
      그 칸에서 *"관측이 크다"* 를 거둔다.
    """
    if days < SAME_DAY_MIN_DAYS:
        return f"보류 — {days}일뿐이다(판정은 {SAME_DAY_MIN_DAYS}일부터)"
    old = SAME_DAY_OLD_GAPS.get((day_type, duration))
    if old is None:
        return "비교 기준이 없다"
    if old < 0:
        if gap >= 0:
            return f"뒤집혔다 ({old:+d}% → {gap:+.0f}%) — 출발 재고가 원인이었다"
        if gap > old / 2:
            return f"출발 재고가 주 원인 ({old:+d}% → {gap:+.0f}%, 절반 넘게 줄었다)"
        return (f"출발 재고로는 설명되지 않는다 ({old:+d}% → {gap:+.0f}%)"
                " — 순수요 · 0 절단 · 공사 재배치가 남는다")
    if gap > 0:
        return f"방향 유지 — 관측이 크다 ({old:+d}% → {gap:+.0f}%)"
    return f"뒤집혔다 ({old:+d}% → {gap:+.0f}%) — 이 칸에서 '관측이 크다'를 거둔다"


def summarize_same_day(rows: list, day_type: str) -> list:
    """회차마다 **판정에 넣을 수 있는** 실행만 모아 평균하고 판정을 붙인다 (1.26.285).

    날 수는 서로 다른 날짜로 센다 — 같은 날 다시 세운 계획이 표본을 부풀리지 않게.
    """
    out = []
    for duration in DURATIONS:
        part = [r for r in rows if r["duration"] == duration and not same_day_usable(r)]
        if not part:
            continue
        sim = sum(r["복원"] for r in part) / len(part)
        seen = sum(r["관측"] for r in part) / len(part)
        gap = (seen / sim - 1) * 100 if sim else float("nan")
        days = len({r["date"] for r in part})
        out.append({"duration": duration, "복원": sim, "관측": seen, "gap": gap, "days": days,
                    "last": max(r["date"] for r in part),
                    "verdict": same_day_verdict(day_type, duration, gap, days)})
    return out


def compare_same_day(day_type: str, run_label: str = None) -> list:
    """같은 날·같은 시각에서 출발한 복원과 관측을 맞댄다 (1.26.278).

    관측 창은 **그 회차 시작 시각부터 회차 길이만큼**이다 — `_20_05`는 그날 20시부터
    다음 날 05시까지라, 달력 날짜로 자르면 두 밤이 섞인다(요일 필터도 걸지 않는다 —
    휴일 밤이 평일 새벽으로 넘어간다).

    출발 재고가 정말 맞았는지를 **같이 찍는다.** `출발 빈 곳`은 계획이 뜬 스냅샷,
    `실제 빈 곳`은 회차 시작 정각의 관측이다. 둘이 가까워야 이 비교가 뜻이 있다 —
    오후 스냅샷으로 세운 계획에서는 이 차가 3.9~23.7%p였다.
    """
    runs = same_day_runs(day_type, run_label)
    if runs.empty:
        what = f"실행 {run_label}" if run_label else (
            f"요일 {day_type} · 회차 시작 {SAME_DAY_TOLERANCE_MIN}분 안에 세운 계획")
        print(f"(같은 시각 비교: {what}이 없습니다 — tools/sameday_plan.py로 세웁니다)")
        return []

    per_hour = 60 // TICK_MINUTES
    rows = []
    for run in runs.itertuples(index=False):
        hours = duration_hours(run.duration)
        start, end = same_day_window(run.start, run.duration)
        with db.session() as conn:
            obs = db.load_stock_history(conn, start=start.strftime("%Y-%m-%d"),
                                        end=(start + timedelta(days=2)).strftime("%Y-%m-%d"))
        own = target_stations(run.run_label, run.duration)
        if obs.empty or not own:
            continue
        obs["관측"] = pd.to_datetime(obs["observed_at"])
        window = obs[(obs["관측"] >= start) & (obs["관측"] < end)]
        ticks = window["관측"].nunique()
        need = int(len(hours) * per_hour * COMPLETE_DAY_RATIO)
        mine = window[window["station_id"].isin(own)]
        empty = (mine["stock"] <= 0).groupby(mine["station_id"]).sum() * TICK_MINUTES / 60
        first = mine[mine["관측"] == mine["관측"].min()] if not mine.empty else mine
        rows.append({
            "run_label": run.run_label, "duration": run.duration, "date": start.date(),
            "late_min": run.late_min, "복원": float(run.복원),
            "관측": float(empty.mean()) if len(empty) else float("nan"),
            "stations": int(len(empty)), "ticks": ticks,
            "expected": len(hours) * per_hour, "complete": ticks >= need,
            "snap_empty": start_empty_share(run.run_label, run.duration, own),
            "real_empty": (first["stock"] <= 0).mean() * 100 if len(first) else float("nan"),
        })

    print("")
    print("같은 시각 비교 — 회차 시작에 세운 계획의 복원 vs 그날의 관측 (1.26.278)")
    print(f"{'날짜':10} {'회차':7} {'늦음':>5} {'복원':>6} {'관측':>6} {'차이':>6} "
          f"{'대여소':>5} {'틱':>7} {'출발 빈곳':>8} {'실제 빈곳':>8}")
    for r in rows:
        gap = (r["관측"] / r["복원"] - 1) * 100 if r["복원"] else float("nan")
        why = same_day_usable(r)
        mark = f"  ← {why}(판정에서 뺀다)" if why else ""
        print(f"{r['date']!s:10} {r['duration']:7} {r['late_min']:4.0f}분 {r['복원']:6.2f} "
              f"{r['관측']:6.2f} {gap:+5.0f}% {r['stations']:5d} {r['ticks']:3d}/{r['expected']:<3d} "
              f"{r['snap_empty']:7.1f}% {r['real_empty']:7.1f}%{mark}")

    summary = [] if run_label else summarize_same_day(rows, day_type)
    if summary:
        print("")
        print(f"{'회차':7} {'복원':>6} {'관측':>6} {'차이':>6} {'날':>3}   판정 (창이 차고 출발이 맞은 날만 · 규칙은 1.26.285에 미리 정함)")
        for g in summary:
            print(f"{g['duration']:7} {g['복원']:6.2f} {g['관측']:6.2f} {g['gap']:+5.0f}% {g['days']:3d}   {g['verdict']}")
        if day_type == "holiday" and max(g["last"] for g in summary) < SAME_DAY_HOLIDAY_FINAL:
            print(f"  ⚠️ 휴일은 잠정이다 — {SAME_DAY_HOLIDAY_FINAL}(10월 연휴 마지막 날)까지 받으면 확정한다.")
    print("  ⚠️ `출발 빈곳`과 `실제 빈곳`이 가까워야 출발 재고의 몫이 빠진 비교다.")
    print("     남는 차이는 0 절단 · 지난달 순수요 · 공사의 실제 재배치 몫이다.")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="관측 재고로 결품을 직접 센다")
    parser.add_argument("--duration", help="시간대 하나만 (예: _10_15)")
    parser.add_argument("--day-type", default="weekday", choices=("weekday", "holiday"))
    parser.add_argument("--save", action="store_true",
                        help="보정 계수를 stockout_calibration에 남긴다 "
                             "(수집이 멈춰도 계수는 남는다)")
    parser.add_argument("--same-day", action="store_true",
                        help="회차 시작 시각에 세운 계획만 그날의 관측과 맞댄다 "
                             "(출발 재고를 맞춘 비교, 1.26.278)")
    parser.add_argument("--run-label",
                        help="실행 하나만 같은 시각 비교로 본다(늦음·요일을 가리지 않는다)")
    args = parser.parse_args()

    if args.same_day or args.run_label:
        compare_same_day(args.day_type, args.run_label)
        return 0

    frame = load(args.day_type)
    if frame.empty:
        print("수집된 재고가 없습니다. scripts/collector.ps1 install 로 수집을 시작하세요.")
        return 1

    days = dense_days(frame)
    span = f"{frame['날짜'].min()} ~ {frame['날짜'].max()}"
    print(f"수집 {frame['관측'].nunique():,}틱 · 대여소 {frame['station_id'].nunique():,}곳"
          f" · {span} · 요일 {args.day_type}")
    print(f"자기 창 안에서 촘촘한 날: {len(days)}일"
          f"{' — ' + ', '.join(days) if days else ''}")
    # ⚠️ **"하루가 온전하다"는 뜻이 아니다.** 좁은 창에서 결측 없이 모은 날도
    # 여기 든다 — 얼마나 넓게 덮었는지는 아래 회차별 판정이 답하고, 등록된
    # 창에 견준 결손은 `tools/collect_stock.py --status`가 답한다.
    print("           ↳ 창의 넓이는 안 봅니다. 회차를 덮었는지는 아래를,")
    print("             등록된 창 대비 결손은 tools/collect_stock.py --status를 보세요.")

    # 수집 창과 겹치는 시간대만 뜻이 있다. 창은 자료에서 읽는다(박아 두지 않는다).
    window = frame["시각"].unique()
    durations = [args.duration] if args.duration else list(DURATIONS)

    # ⚠️ **위 숫자를 머리기사로 쓰지 않는다** (2026-09-05). 분석이 실제로 쓰는
    # 것은 **회차별** 판정이다 — 회차를 덮었는지는 날의 촘촘함이 답하지 못한다.
    usable = usable_by_duration(frame, durations, window)
    if any(days_ for days_, _ in usable.values()):
        print(f"회차별 쓸 수 있는 날: {format_usable(usable)}")
        print("  ↳ 회차 비교에 필요한 것은 '하루가 온전한가'가 아니라 **그 회차의"
              " 시간대가 다 덮였는가**입니다.")
        print("     '(창 일부)'는 수집 창이 그 회차를 통째로 덮지 못한다는 뜻이라"
              " 복원과 맞대지 않습니다.\n")
    else:
        print("\n⚠️  **아직 온전히 덮인 회차가 없습니다.** 아래 숫자는 참고용이며,")
        print("    한 회차가 다 모이기 전까지는 결품 '시간'으로 인용하지 마십시오"
              " — 관측한 시간만큼만 셉니다.\n")

    # `날`·`관측/일`을 함께 낸다 (2026-09-05). **이 표는 온전하지 않은 날도
    # 섞어 평균한다** — 그래서 아래 비교표(온전한 날만)와 같은 회차·같은
    # 대여소인데도 값이 다르다. 실측 `_10_15`·작업 대상 245곳 기준으로
    # 1.02(9일) 대 1.15(6일), **12.6% 낮게** 나온다. 결품 시간은 절대값이라
    # 반쪽만 관측한 날이 그대로 평균을 끌어내린다. 분모를 감추면 두 숫자가
    # 같은 이름을 달고 서로 다른 말을 한다.
    print(f"{'시간대':8} {'겹치는 시각':>10} {'날':>4} {'관측/일':>8} {'대여소':>7} "
          f"{'결품시간/일':>11} {'만차시간/일':>11} {'결품 비율':>9}")
    printed = 0
    for duration in durations:
        hours = [h for h in duration_hours(duration) if h in window]
        if not hours:
            continue
        daily = observed_hours(frame, hours)
        if daily.empty:
            continue
        per_day = daily.groupby("station_id")[["결품시간", "만차시간", "관측시간"]].mean()
        share = (daily["빈틱"].sum() / daily["틱"].sum()) if daily["틱"].sum() else 0
        print(f"{duration:8} {len(hours):10d} {daily['날짜'].nunique():4d} "
              f"{per_day['관측시간'].mean():8.2f} {len(per_day):7,d} "
              f"{per_day['결품시간'].mean():11.2f} {per_day['만차시간'].mean():11.2f} "
              f"{share * 100:8.1f}%")
        printed += 1

        # 요일 구분까지 맞춘 **계획** 실행의 대상을 쓴다 (1.26.274).
        # `or targets`로 떨어지는 길은 없앴다 — 그 자리가 평일 실험의
        # 모집단을 휴일 표에 흘려 넣던 구멍이었다.
        dur_targets = latest_plan_targets(args.day_type, duration)
        if dur_targets:
            picked = per_day.loc[per_day.index.isin(dur_targets)]
            if not picked.empty:
                print(f"{'  └ 작업 대상만':25} {picked['관측시간'].mean():8.2f} "
                      f"{len(picked):7,d} "
                      f"{picked['결품시간'].mean():11.2f} {picked['만차시간'].mean():11.2f}")

    if not printed:
        hours = sorted(int(h) for h in window)
        print(f"  수집 창({hours[0]:02d}~{hours[-1]:02d}시)과 겹치는 시간대가 없습니다.")
        return 1

    rows = compare_with_simulation(frame, durations, window, args.day_type)

    if args.save:
        _save_calibration(rows, args.day_type, window)

    hours = sorted(int(h) for h in window)
    print("\n읽는 법")
    print(f"  · 한 틱 = {TICK_MINUTES}분. 수집 창({hours[0]:02d}~{hours[-1]:02d}시) 안에서만 셉니다.")
    print("  · **결품 시간은 관측한 시간에 대한 값**입니다. 창 밖(야간·새벽)은 모릅니다.")
    print("  · ⚠️ **위 표와 아래 비교표는 날을 다르게 고릅니다.** 위 표는 반쪽만")
    print("    관측한 날도 섞어 평균하고(그래서 `날`·`관측/일`을 함께 냅니다),")
    print("    아래 비교표는 **그 회차를 온전히 덮은 날만** 씁니다. 같은 회차·같은")
    print("    대여소인데 값이 다르면 그 때문입니다 — 결품 시간은 절대값이라")
    print("    반쪽짜리 날이 평균을 끌어내립니다(`_10_15` 245곳: 1.02 대 1.15).")
    print("  · step4의 결품은 순수요로 복원한 값이라 **직접 비교하려면 같은 시간대·같은")
    print("    대여소로 맞춰야** 합니다. 맞대어 보는 것이 이 스크립트의 목적입니다.")
    print("  · ⚠️ **두 값은 서로 다른 세상을 잽니다.** 복원은 `stockout_hours_before`")
    print("    (재배치 **전**)이고, 실측에는 **대전교통공사가 실제로 돌린 재배치**가")
    print("    이미 반영돼 있습니다. 편향이 반대 방향으로 둘입니다 — 복원은 0에서")
    print("    잘라 결품을 낮춰 잡고(실측↑), 실측은 공사 차량 덕에 낮아집니다(실측↓).")
    print("    **차이를 '복원의 오차'로만 읽으면 안 됩니다.**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
