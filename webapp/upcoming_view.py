"""다음 회차 예상 — **1 · 3시간 뒤 작업 대상이 될 대여소** (1.26.319, EXPERIMENTS 46장).

계획의 작업 대상은 회차 시작의 `rebal_qty = target_qty − stock`이 `REBAL_MIN_QTY`를 넘는 곳이다. 목표 재고는 달 통계로
미리 정해지므로, 지금 재고와 평균 흐름만으로 *"h시간 뒤 계획을 세우면 수거 · 배송 대상이 될 곳"* 을 미리 볼 수 있다.

## 왜 ML이 아니라 식 한 줄인가

ML 14번(`experiments/structure/target_forecast.py`)은 GBM이 모든 조합에서 이겼지만, **매 시각 상위 50곳만 고르면**
아래의 순수요 누적 격차 순서가 GBM과 같거나 나았다(1시간 수거 95.3 대 94.0%). 이 화면은 상위 목록을 보여 주는 것이라
식으로 충분하고, 모형 파일 · 재학습이 필요 없다. 확률(몇 %)은 계획 연결 판정(평일 20일) 뒤에 붙인다.

    예상 재고 = 지금 재고 − Σ(앞으로 h시간의 시간대별 평균 순수요)     ← 순수요 = 대여 − 반납
    예상 격차 = τ 회차의 목표 재고 − 예상 재고                          ← τ = 마지막 관측 + h시간
    수거 대상 = 예상 격차 < −REBAL_MIN_QTY · 배송 대상 = 예상 격차 > REBAL_MIN_QTY

## 계획을 바꾸지 않는다

`orders.py`의 재고 대조와 같다 — **보여 주기만 한다.** 이 목록으로 계획을 고치면 기사가 든 종이와 화면이 어긋난다.

## 물러나는 경우

- 마지막 관측이 `STALE_MINUTES`보다 오래됐으면 목록을 내지 않는다 — 수집이 멈춘 채 옛 재고로 '예상'을 내면 틀린 곳으로
  차를 보낸다(이 PC는 2026-09-30부터 여러 번 꺼져 있었다).
- τ의 회차 · 요일 구분에 맞는 계획이 없으면 목표 재고를 모르므로 목록을 내지 않는다.

타슈 API는 부르지 않는다 — 재고는 수집기가 쌓은 `stock_history`의 마지막 틱이다.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from project_config import REBAL_MIN_QTY, TOP_STATION_LIMIT, holiday_mask
from webapp import store

HORIZONS = (1, 3)                 # 시간 — ML 14번이 잰 두 지평만 연다
STALE_MINUTES = 60                # 마지막 관측이 이보다 오래되면 목록을 내지 않는다
TICK_MINUTES = 10
DURATIONS = (("_05_10", 5, 10), ("_10_15", 10, 15), ("_15_20", 15, 20), ("_20_05", 20, 29))

# ML 14번의 실측(EXPERIMENTS 46장) — 평일 검증 4일, 매 시각 상위 50곳 중 실제 대상 비율. 화면이 근거로 보여 준다
VALIDATED_TOP50 = {1: {"수거": 95.3, "배송": 99.4}, 3: {"수거": 85.8, "배송": 97.3}}


def duration_of(hour: int) -> str:
    for name, start, end in DURATIONS:
        if start <= hour < end or start <= hour + 24 < end:
            return name
    return "_20_05"


def expected_outflow(mu: pd.DataFrame, start: pd.Timestamp, hours: int) -> pd.Series:
    """대여소마다 `start`부터 `hours`시간 동안의 평균 순유출 합. `mu`는 대여소 × 시(0~23)의 시간당 평균 순수요.

    한 시간의 평균을 그 시간의 10분 칸 여섯에 고르게 나눈다 — ML 14번 베이스라인 ③과 같은 식이다.
    """
    per_hour = 60 // TICK_MINUTES
    slot = start.hour * per_hour + start.minute // TICK_MINUTES
    slots = (slot + np.arange(hours * per_hour)) % (24 * per_hour)
    hours_idx = slots // per_hour
    rate = mu.reindex(columns=range(24)).fillna(0.0).to_numpy(dtype=float) / per_hour
    return pd.Series(rate[:, hours_idx].sum(axis=1), index=mu.index)


def classify(frame: pd.DataFrame) -> pd.DataFrame:
    """예상 격차로 수거 · 배송을 가른다. 대상이 아닌 행은 빠진다."""
    out = frame.copy()
    out["side"] = np.where(out["예상격차"] < -REBAL_MIN_QTY, "수거",
                           np.where(out["예상격차"] > REBAL_MIN_QTY, "배송", ""))
    return out[out["side"] != ""]


# ───────────────────────────────────────────── 자료 (DB는 `store`만 연다 — webapp 계층 규약)

def _hourly_mu(period: str, holiday: bool) -> pd.DataFrame:
    """대여소 × 시(0~23)의 평균 순수요 — 그 요일 구분의 날만."""
    net = store.net_demand(period) if period else pd.DataFrame()
    if net.empty or "date" not in net:
        return pd.DataFrame(columns=range(24))
    cols = [f"net_{h:02d}" for h in range(24)]
    flag = np.asarray(holiday_mask(pd.to_datetime(net["date"])), dtype=bool) == holiday
    mu = net[flag].groupby("station_id")[cols].mean()
    mu.columns = range(24)
    return mu


# ───────────────────────────────────────────── 화면 재료

def context(hours: int = 1, *, now: Optional[datetime] = None) -> dict:
    hours = hours if hours in HORIZONS else HORIZONS[0]
    now = pd.Timestamp(now or datetime.now())
    ctx = {"hours": hours, "horizons": HORIZONS, "limit": TOP_STATION_LIMIT, "threshold": REBAL_MIN_QTY,
           "validated": VALIDATED_TOP50[hours], "error": None, "stale": None,
           "pick": [], "drop": [], "pick_total": 0, "drop_total": 0}
    last, stock = store.latest_stock_tick()
    if last is None or stock.empty:
        ctx["error"] = "수집한 재고가 없습니다."
        return ctx
    age = (now - last).total_seconds() / 60
    ctx.update({"last_tick": last.strftime("%Y-%m-%d %H:%M"), "age_minutes": int(age)})
    if age > STALE_MINUTES:
        ctx["stale"] = int(age)
        return ctx

    tau = last + pd.Timedelta(hours=hours)
    duration = duration_of(tau.hour)
    # 20~05시 회차는 **시작한 날**의 요일 구분으로 세운다 — 새벽 2시는 전날 20시에 시작한 회차다
    start_day = tau.normalize() - pd.Timedelta(days=1) if duration == "_20_05" and tau.hour < 5 else tau.normalize()
    holiday = bool(np.asarray(holiday_mask(pd.Series([start_day])), dtype=bool)[0])
    target, label, period = store.latest_plan_targets("holiday" if holiday else "weekday", duration)
    ctx.update({"tau": tau.strftime("%m-%d %H:%M"), "duration": duration,
                "day_type": "휴일" if holiday else "평일", "plan_label": label, "period": period})
    if target is None:
        ctx["error"] = f"{ctx['day_type']} {duration} 회차의 계획이 아직 없어 목표 재고를 모릅니다."
        return ctx
    mu = _hourly_mu(period, holiday)
    names = store.stock_station_names()

    frame = stock.set_index("station_id")["stock"].astype(float).to_frame("지금재고")
    frame["목표"] = target.reindex(frame.index)
    frame = frame.dropna(subset=["목표"])
    outflow = expected_outflow(mu, last, hours).reindex(frame.index).fillna(0.0)
    frame["예상재고"] = frame["지금재고"] - outflow
    frame["예상격차"] = frame["목표"] - frame["예상재고"]
    marked = classify(frame)
    for side, key in (("수거", "pick"), ("배송", "drop")):
        part = marked[marked["side"] == side]
        part = part.reindex(part["예상격차"].abs().sort_values(ascending=False).index).head(TOP_STATION_LIMIT)
        ctx[f"{key}_total"] = int((marked["side"] == side).sum())
        ctx[key] = [{"station_id": sid, "name": names.get(sid, sid), "stock": int(r["지금재고"]),
                     "target": round(float(r["목표"]), 1), "expected": round(float(r["예상재고"]), 1),
                     "gap": round(float(r["예상격차"]), 1)}
                    for sid, r in part.iterrows()]
    return ctx
