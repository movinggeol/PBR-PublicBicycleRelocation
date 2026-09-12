"""고장 추정 자전거 **수거** 시나리오 — 언제 나가야 하나, 재배치에 끼워 넣을 수 있나.

> 재현: `python experiments/structure/broken_collect.py`

## 왜 이 실험이 필요한가

이 프로젝트를 설명하며 받은 피드백과 현행 공공자전거 운영의 실제 어려움이
같은 곳을 가리켰다 — **고장·파손 자전거의 수거**다. 재배치는 "멀쩡한 자전거를
수요가 있는 곳으로 옮기는" 일이고, 수거는 "못 쓰는 자전거를 회수하는" 일이라
**문제의 구조가 다르다.**

| | 재배치 (기존 파이프라인) | 수거 (이 실험) |
| --- | --- | --- |
| 문제 유형 | Pickup **and** Delivery | **순수 Pickup** (목적지 1곳) |
| 수요 예측 | μ·σ·z → 목표 재고 | **없다** |
| 짝 맞추기 | ILP가 군집 내 `min(pick, drop)` | **없다** |
| 시간 예산 | 120분 (사후 점검) | **구속하지 않는다**(아래) |

시간 예산이 구속하지 않는 이유는 **수거가 미룰 수 있는 작업**이기 때문이다.
재배치가 늦으면 그 회차의 결품이 늘지만, 고장 자전거는 이미 아무도 못 쓰므로
한나절 늦는다고 다음 운영에 지장을 주지 않는다(사용자 판단).

**그래서 물음이 '어떻게 도나'가 아니라 '언제 나가나'가 된다.**

## 🔴 고장 자전거 좌표를 **지어내지 않는다**

이 실험의 처음 구상은 *"파손 자전거 좌표를 임의로 찍는다"* 였다. 그러면
**분포 가정이 결론을 정하는데 검증할 실측이 없다.** 균일하게 뿌리면 경로가
길어져 최적화 이득이 크게 나오고, 대여소 주변에 뭉치면 작게 나온다.

그런데 `rental_history`에 **`bike_no`가 있다.** 그래서 좌표를 만들지 않고
**관측에서 추론**한다.

    기준기간에 돌던 자전거가 추적기간에 한 번도 안 나타난다 → 고장 추정
    그 자전거의 **마지막 반납 대여소**가 마지막으로 알려진 위치다

실측으로 확인한 것(2026-09-12, 아래 `--audit`로 재현):

- 사라진 자전거는 그전에 **적게** 쓰였다(중앙 156회 vs 222회, p=5e-34).
  이용량 5분위별 소멸률이 **27.3% → 8.5%로 단조**다. 즉 마모로 고장나는 게
  아니라 **상태가 나빠 아무도 안 빌려가고, 그러다 사라진다.**
- 마지막 주행이 정상 길이다(10분·0.8km vs 전체 11분·0.9km) — **주행 중 고장
  흔적이 없다.** 정상 반납 후 그 자리에 남았다는 뜻이라, 마지막 반납 대여소를
  위치로 써도 된다.
- 한적한 대여소가 아니라 **가장 번잡한 대여소에 몰려 있다**(대여소당 1.42대
  vs 0.09대, 16배). 반납이 1,200건씩 일어나는 곳에서 멀쩡한 자전거가 몇 달
  남아 있을 수 없다 — 수백 명이 보고 **고르지 않았다**는 뜻이다.

⚠️ **그래도 '고장'이 아니라 '고장 추정'이다.** 정비 기록이 없으므로 검증되지
않았고, 특히 **세대 퇴역과 구분할 수 없다**(DJ2 소멸률 24.4% vs DJ3 11.8% —
일부는 개별 고장이 아니라 구형 일괄 퇴역이다). 문서·논문에 쓸 때 반드시
`고장 추정`으로 쓸 것.

## 무엇을 묻나

**① 배치 임계치 — 몇 대 모이면 나가나**

미룰 수 있는 작업에는 맞바꿈이 있다.

    지금 나간다 → 방치 시간 ↓, 자전거 1대당 이동비용 ↑
    모아서 나간다 → 방치 시간 ↑, 1대당 이동비용 ↓

**방치 시간(대·일)** = Σ(수거 시각 − 고장 발생 시각). 기존 결품 시간 KPI와
같은 구조이고, **발생 시각이 실측**이라 가정한 도착률이 필요 없다.

**② 통합 vs 분리 — 재배치 회차에 끼워 넣을 수 있나**

고장 자전거는 번잡한 대여소에 몰려 있고, 재배치도 불균형이 큰 대여소를
고른다. **겹친다면 전용 출동 없이 지나가는 길에 실을 수 있다.**

## 어떻게 판정하나

①은 **무릎(knee)**을 찾는다 — 임계치를 올려도 이동거리가 더는 안 줄어드는
지점. ②는 **겹침 비율**이다. 겹침이 낮으면 통합해도 얻는 게 없다.

🔴 **모집단을 고정한다.** 임계치는 *언제* 수거하는지만 바꾸지 *어느* 자전거를
수거하는지는 바꾸지 않는다. 그래서 모든 정책이 같은 N대를 처리해야 하고,
이 스크립트는 그것을 **매번 검사해 어긋나면 멈춘다**(EXPERIMENTS 17·18장이
남긴 교훈 — 분모가 흔들리면 서로 다른 자로 잰 값이 된다).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
# step 폴더를 sys.path에 밀어 넣지 않는다 — 폴더 이름으로 부른다(1.26.154).

import db                                                       # noqa: E402
from pipeline.step2_optimize import vrp as vrp_mod              # noqa: E402
from project_config import (                                    # noqa: E402
    DEPOT_ID, DEPOT_LAT, DEPOT_LON, VEHICLE_CAPACITY,
)

# 운영 시설 — **공개 대여소가 아니다.** 라이브 대여소 목록(stock_station_master)에
# 없고 parking_lot이 NULL이다. 여기로 마지막 반납된 자전거는 '수거할 것'이
# 아니라 **이미 들어간 것**이므로 대상에서 뺀다.
#   ST0001 타슈관제센터 정비대기  ← 이름부터 정비 대기소다. DEPOT과 좌표가 같다.
#   ST1220 타슈관제센터           ← 약 570m 떨어진 운영 거점 (대여 134k vs 반납 8k)
#   ST1418 타슈 시험용 대여소
OPS_STATIONS = ("ST0001", "ST1220", "ST1418")

DEFAULT_BASE = "2025-09,2025-10"
DEFAULT_FOLLOW = "2026-01,2026-02,2026-03"


# ──────────────────────────────────────────────────── ① 대상 도출

def detect_broken(conn, base_months: list, follow_months: list) -> pd.DataFrame:
    """고장 추정 자전거와 **마지막으로 알려진 위치**를 뽑는다.

    기준기간에 돌던 자전거 중 추적기간에 한 번도 안 나타나는 것을 고른다.

    🔴 **기간을 고정하는 것이 핵심이다.** 자전거별 누적 이용량을 그냥 비교하면
    일찍 사라진 자전거는 관측 창이 짧아 누적이 자동으로 작다 — 그러면
    *"적게 쓰인 자전거가 고장난다"* 가 공짜로 나온다. 같은 창에서 재야 한다.

    씨앗이 없다 — **결정적**이다. 같은 DB에서 늘 같은 목록이 나온다.
    """
    qs_base = ",".join("?" * len(base_months))
    qs_follow = ",".join("?" * len(follow_months))

    frame = pd.read_sql(
        f"""
        WITH base AS (SELECT DISTINCT bike_no FROM rental_history
                       WHERE substr(rent_at,1,7) IN ({qs_base})),
             live AS (SELECT DISTINCT bike_no FROM rental_history
                       WHERE substr(rent_at,1,7) IN ({qs_follow})),
             gone AS (SELECT bike_no FROM base
                       WHERE bike_no NOT IN (SELECT bike_no FROM live)),
             last AS (SELECT bike_no, MAX(rent_at) AS m
                        FROM rental_history GROUP BY bike_no)
        SELECT r.bike_no,
               r.return_station AS station_id,
               r.return_lat     AS lat,
               r.return_lon     AS lon,
               r.return_at      AS broken_at
          FROM rental_history r
          JOIN last  ON r.bike_no = last.bike_no AND r.rent_at = last.m
          JOIN gone  ON r.bike_no = gone.bike_no
        """, conn, params=base_months + follow_months)

    frame = frame.drop_duplicates("bike_no")
    before = len(frame)
    frame = frame[~frame["station_id"].isin(OPS_STATIONS)]
    frame = frame.dropna(subset=["lat", "lon"])
    frame["broken_at"] = pd.to_datetime(frame["broken_at"])

    print(f"고장 추정 {before:,}대 중 운영시설 반납분 {before - len(frame):,}대를 뺀"
          f" **수거 대상 {len(frame):,}대**")
    return frame.sort_values("broken_at").reset_index(drop=True)


# ──────────────────────────────────────────────────── 경로

def route_once(batch: pd.DataFrame, capacity: int) -> dict:
    """한 번의 출동 — 대여소별로 묶어 greedy로 돌고 depot에 인계한다.

    `greedy_route()`를 **운영 코드 그대로** 부른다. 제 방식대로 경로를 짜면
    기존 결과와 비교가 성립하지 않는다(baseline_compare.py와 같은 규약).

    ⚠️ 순수 pick만 주므로 **적재가 차면 `if not candidates:` 분기가 돈다** —
    depot으로 돌아가 비우고 다시 나간다(다회 왕복). 이 분기는 파이프라인
    입력에서는 **구조상 실행될 수 없어**(총 pick = 총 drop) 실데이터에
    `return` 행이 0건이었다(vrp.py:144-153). 즉 이 실험이 그 경로에 처음으로
    체중을 싣는다 — `tests/test_broken_collect.py`가 그래서 있다.
    """
    nodes = {}
    for sid, grp in batch.groupby("station_id"):
        nodes[(sid, "pick")] = {
            "qty": int(len(grp)),
            "lat": float(grp["lat"].iloc[0]),
            "lon": float(grp["lon"].iloc[0]),
        }

    # 🔴 값 dict까지 복사해서 넘긴다 — greedy_route는 넘긴 dict의 `qty`를 0으로
    #    깎아 **소모한다**(함정 12. 실측: 호출 후 qty 3 → 0).
    #
    #    지금은 위에서 노드를 매 호출마다 새로 만들므로 이 복사가 없어도 돌아간다.
    #    그런데 대여소가 출동마다 겹치므로 **노드를 미리 만들어 캐싱하는 것이
    #    자연스러운 최적화**이고, 그렇게 고치는 순간 두 번째 출동부터 빈 경로를
    #    받는다. **예외가 나지 않아서** 표는 그럴듯하게 찍힌다(1.26.97에서
    #    *"이동거리 −100%"* 표를 만들고서야 알아챘다). 그래서 미리 막아 둔다.
    safe = {k: dict(v) for k, v in nodes.items()}

    rows = vrp_mod.greedy_route(safe, cluster=0)
    if not rows:
        return {"km": 0.0, "sec": 0.0, "stations": 0, "returns": 0, "picked": 0}

    frame = pd.DataFrame(rows)
    return {
        "km": float(frame["distance_km"].sum()),
        "sec": float(frame["cum_sec"].max()),
        "stations": int(frame.loc[frame["action"] == "pick", "to_id"].nunique()),
        "returns": int((frame["action"] == "return").sum()),
        "picked": int(frame.loc[frame["action"] == "pick", "qty"].sum()),
    }


# ──────────────────────────────────────────────────── ② 배치 임계치

def simulate(broken: pd.DataFrame, rule: str, value, capacity: int) -> dict:
    """정책 하나를 돌린다.

    rule='count' : value대 모이면 나간다
    rule='days'  : value일마다 나간다

    ⚠️ **남은 자투리는 관측 끝에서 강제 수거한다.** 안 그러면 임계치가 큰 정책이
    *"아직 안 모여서 안 나갔다"* 며 꼬리를 통째로 빼먹어 유리해진다. 강제 수거는
    그 자투리에 긴 방치 시간을 물리므로 **큰 임계치에 불리한(보수적인) 쪽**이다.
    # ponytail: 자투리 처리가 임계치 비교에 약간의 편향을 준다 — 관측 창을 늘려
    #           꼬리 비중을 줄이는 것이 다음 수다.
    """
    horizon = broken["broken_at"].max()
    batches, buffer, opened = [], [], None

    for row in broken.itertuples():
        if not buffer:
            opened = row.broken_at
        buffer.append(row.Index)

        full = (rule == "count" and len(buffer) >= value)
        due = (rule == "days"
               and (row.broken_at - opened).total_seconds() >= value * 86400)
        if full or due:
            batches.append((row.broken_at, list(buffer)))
            buffer = []

    if buffer:                       # 자투리 — 관측 끝에 강제로 내보낸다
        batches.append((horizon, list(buffer)))

    total = {"km": 0.0, "sec": 0.0, "returns": 0, "picked": 0}
    idle_days = 0.0
    for at, idx in batches:
        part = broken.loc[idx]
        stats = route_once(part, capacity)
        for key in total:
            total[key] += stats[key]
        idle_days += (at - part["broken_at"]).dt.total_seconds().sum() / 86400

    n = len(broken)
    return {
        "정책": f"{value}{'대' if rule == 'count' else '일'}",
        "출동": len(batches),
        "처리": total["picked"],
        "방치_대일": round(idle_days, 1),
        "방치평균_일": round(idle_days / n, 2),
        "이동km": round(total["km"], 1),
        "대당km": round(total["km"] / n, 2),
        "depot왕복": total["returns"],
        "차량시간_h": round(total["sec"] / 3600, 1),
    }


# ──────────────────────────────────────────────────── ③ 통합 vs 분리

def integration_overlap(conn, broken: pd.DataFrame, run_label: str) -> None:
    """재배치가 이미 들르는 대여소에 고장 자전거가 얼마나 있나.

    겹치면 **전용 출동 없이 지나가는 길에** 실을 수 있다. 재배치도 고장도
    둘 다 번잡한 대여소를 향하므로 겹칠 여지가 있다.
    """
    visited = pd.read_sql(
        "SELECT duration, to_id AS station_id FROM vrp_plan"
        " WHERE run_label = ? AND action IN ('pick','drop')",
        conn, params=[run_label])
    if visited.empty:
        print(f"\n[건너뜀] '{run_label}'의 vrp_plan이 비어 있습니다.")
        return

    per_station = broken.groupby("station_id").size()
    print(f"\n=== ③ 통합 가능성 — 재배치 실행 '{run_label}' ===")
    print(f"{'회차':<10}{'재배치 방문':>12}{'고장 겹침':>10}{'겹친 자전거':>12}{'비율':>8}")

    union = set()
    for duration, grp in visited.groupby("duration"):
        stations = set(grp["station_id"])
        union |= stations
        hit = per_station.index.intersection(stations)
        bikes = int(per_station.loc[hit].sum()) if len(hit) else 0
        print(f"{duration:<10}{len(stations):>12}{len(hit):>10}{bikes:>12}"
              f"{bikes / len(broken) * 100:>7.1f}%")

    hit = per_station.index.intersection(union)
    bikes = int(per_station.loc[hit].sum()) if len(hit) else 0
    print(f"{'세 회차 합':<10}{len(union):>12}{len(hit):>10}{bikes:>12}"
          f"{bikes / len(broken) * 100:>7.1f}%")
    print(f"\n  전체 고장 추정 {len(broken):,}대가 {per_station.size}곳에 흩어져 있고,"
          f" 그중 {len(hit)}곳을 재배치가 이미 들른다.")
    print("  ⚠️ 겹친다고 공짜는 아니다 — 재배치 차량은 이미 자전거를 싣고 있어"
          " 적재를 나눠 써야 한다(용량 10대는 상한이고 통상 7대다).")


# ──────────────────────────────────────────────────── 감사

def audit(conn, base_months: list, follow_months: list) -> None:
    """탐지 방법이 근거 있는지 되짚는다 — 두 가설을 실제로 가른다."""
    qs_b = ",".join("?" * len(base_months))
    qs_f = ",".join("?" * len(follow_months))
    base = pd.read_sql(
        f"SELECT bike_no, COUNT(*) AS trips FROM rental_history"
        f" WHERE substr(rent_at,1,7) IN ({qs_b}) GROUP BY bike_no",
        conn, params=base_months)
    live = pd.read_sql(
        f"SELECT DISTINCT bike_no FROM rental_history"
        f" WHERE substr(rent_at,1,7) IN ({qs_f})", conn, params=follow_months)
    base["survived"] = base["bike_no"].isin(set(live["bike_no"]))
    base["세대"] = base["bike_no"].str.split("-").str[0]

    print("\n=== 감사 ① 이용량과 소멸의 관계 (H1 마모설 vs H2 방치설) ===")
    base["분위"] = pd.qcut(base["trips"], 5,
                          labels=["최저", "하", "중", "상", "최고"])
    tbl = base.groupby("분위", observed=True).agg(
        대수=("bike_no", "size"), 주행중앙=("trips", "median"),
        소멸률=("survived", lambda s: round((~s).mean() * 100, 1)))
    print(tbl.to_string())
    print("  단조 감소면 H2(상태가 나빠 안 빌려간다)가 지지된다 —"
          " H1(많이 써서 고장난다)이면 반대로 증가해야 한다.")

    print("\n=== 감사 ② 세대별 — 퇴역 교란 ===")
    gen = base.groupby("세대").agg(
        대수=("bike_no", "size"),
        소멸률=("survived", lambda s: round((~s).mean() * 100, 1)))
    print(gen.to_string())
    print("  세대 간 격차가 크면 **개별 고장이 아니라 일괄 퇴역**이 섞여 있다는 뜻이다.")


# ──────────────────────────────────────────────────── 실행

def main() -> None:
    parser = argparse.ArgumentParser(
        description="고장 추정 자전거 수거 — 배치 임계치와 통합 가능성")
    parser.add_argument("--base", default=DEFAULT_BASE,
                        help="기준기간 월 목록 (이 창에서 이용량을 잰다)")
    parser.add_argument("--follow", default=DEFAULT_FOLLOW,
                        help="추적기간 월 목록 (여기 없으면 고장 추정)")
    parser.add_argument("--counts", default="5,10,20,30,50,100",
                        help="대수 기준 임계치")
    parser.add_argument("--days", default="1,3,7,14,30",
                        help="일수 기준 임계치")
    parser.add_argument("--capacity", type=int, default=VEHICLE_CAPACITY,
                        help=f"적재 용량 (기본 {VEHICLE_CAPACITY}는 **상한**이고"
                             " 통상은 7대다 — 둘 다 돌려 볼 것)")
    parser.add_argument("--run-label", default="",
                        help="통합 비교에 쓸 재배치 실행 (기본: vrp_plan 최신)")
    parser.add_argument("--audit", action="store_true",
                        help="탐지 방법의 근거를 되짚는다")
    parser.add_argument("--out", default="", help="정책 표를 CSV로 저장")
    args = parser.parse_args()

    base_months = [m.strip() for m in args.base.split(",") if m.strip()]
    follow_months = [m.strip() for m in args.follow.split(",") if m.strip()]

    with db.session() as conn:
        print("=" * 78)
        print("고장 추정 자전거 수거 시나리오")
        print(f"  기준기간 {base_months}  →  추적기간 {follow_months}")
        print(f"  적재 용량 {args.capacity}대 · depot {DEPOT_ID}"
              f" ({DEPOT_LAT}, {DEPOT_LON})")
        print("=" * 78)

        broken = detect_broken(conn, base_months, follow_months)
        if broken.empty:
            raise SystemExit("수거 대상이 없습니다 — 기간을 확인하세요.")

        per = broken.groupby("station_id").size()
        span = (broken["broken_at"].max() - broken["broken_at"].min()).days or 1
        print(f"  흩어진 대여소 {per.size}곳 · 대여소당 중앙 {per.median():.0f}대"
              f" · 최대 {per.max()}대 · 1대뿐인 곳 {(per == 1).sum()}곳")
        print(f"  발생 {broken['broken_at'].min():%Y-%m-%d}"
              f" ~ {broken['broken_at'].max():%Y-%m-%d} ({span}일)"
              f" · 하루 {len(broken) / span:.1f}대")

        if args.audit:
            audit(conn, base_months, follow_months)

        rows = []
        for raw in args.counts.split(","):
            if raw.strip():
                rows.append(simulate(broken, "count", int(raw), args.capacity))
        for raw in args.days.split(","):
            if raw.strip():
                rows.append(simulate(broken, "days", int(raw), args.capacity))

        table = pd.DataFrame(rows)

        # 🔴 모집단 검사 — 임계치는 '언제'만 바꾼다. '몇 대'가 달라지면 그 표는 무효다.
        bad = table[table["처리"] != len(broken)]
        if not bad.empty:
            raise SystemExit(
                f"모집단이 흔들렸습니다 — 모든 정책이 {len(broken)}대를 처리해야"
                f" 하는데 어긋난 행이 있습니다:\n{bad.to_string(index=False)}")

        print(f"\n=== ② 배치 임계치 (모든 정책이 같은 {len(broken):,}대를 처리한다) ===")
        print(table.to_string(index=False))
        print("\n  읽는 법: 임계치를 올리면 이동거리(대당km)는 줄고 방치 시간은 는다.")
        print("  더 올려도 대당km가 안 줄어드는 지점이 무릎이다.")

        label = args.run_label or pd.read_sql(
            "SELECT run_label FROM vrp_plan ORDER BY run_label DESC LIMIT 1",
            conn)["run_label"].iloc[0]
        integration_overlap(conn, broken, label)

        if args.out:
            table.to_csv(args.out, index=False, encoding="utf-8-sig")
            print(f"\n저장: {args.out}")


if __name__ == "__main__":
    main()
