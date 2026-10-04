"""대여이력의 자전거 사슬로 공사의 **실제 재배치**를 복원한다 (EXPERIMENTS 44장 1부, 1.26.312).

왜 — 재고 관측은 24시간 평일이 6일뿐인데, 대여이력은 19개월 911만 건이다. 같은 `bike_no`의 이용을
시간순으로 이으면 **반납한 대여소 ≠ 다음에 대여된 대여소**인 쌍이 생기고, 그것은 이용자가 아닌 누군가
(트럭 · 정비)가 옮긴 것이다. ML_후보_10 5번은 재고 변화량만 보고 *"정답표가 없다"* 고 적었다 — 이
스크립트가 그 정답표를 만든다.

무엇을 묻나 (사전 등록 — EXPERIMENTS 44장, 결과 전 커밋. 1부는 판정 기준 없이 **읽는 법**만 등록했다)
  Q1 규모    : 갈래별 하루 이동 대수 (평일 · 휴일)
  Q2 시각    : 구간이 한 회차 창 안에 통째로 들어가는 이동의 비율과 회차 분포
  Q3 표적성  : 대여소 × 달(평일)의 공사 순공급(채운 − 빼 간) 대 이용자 순유출(대여 − 반납) Spearman ρ
               ρ ≥ 0.5 '불균형을 따라간다' · 0.2~0.5 '부분적으로' · < 0.2 '표적이 안 보인다'
  Q4 겹침    : 정본 계획(`2026-08-11 real raw19`, 25년 11월 평일)의 수거 · 배송 대여소 대 같은 달 공사 이동 상위 N
               대조는 이용량 상위 N곳 — 공사가 이보다 계획과 닮지 않았으면 '바쁜 곳 이상으로 닮지 않았다'

함정
  · 🔴 **빠진 달(25년 12월)을 건너는 쌍은 끊는다** — 잇으면 한 달의 공백이 가짜 이동이 된다. 같은 연속 구간
    안의 긴 공백(몇 달 쉰 자전거)은 '장기'로 따로 센다.
  · 대여소 번호가 달라도 좌표가 50m 안이면 같은 자리다(신설 · 번호 재지정).
  · 이동은 **다시 대여돼야** 보인다 — 대여가 많은 곳으로 채운 것이 더 잘 잡혀 Q3의 ρ를 위로 민다.
    장기 문턱을 30일로 바꾼 ρ를 함께 찍는다.

실행:
    python experiments/structure/rebalance_trace.py
    python experiments/structure/rebalance_trace.py --months "25년 09월"   # 한 달만 (빠른 점검)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import project_config  # noqa: E402,F401  — 콘솔 인코딩을 먼저 맞춘다(— · 이모지)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import db  # noqa: E402
from project_config import holiday_mask  # noqa: E402

# ── 사전 등록 (EXPERIMENTS 44장) — 결과를 보고 바꾸지 않는다
SAME_PLACE_M = 50.0                     # 좌표가 이 안이면 같은 자리
LONG_GAP_DAYS = 7                       # 구간이 이보다 길면 '장기'(정비 · 보관 가능)
SENSITIVITY_LONG_DAYS = 30              # Q3 민감도
CENTER_STATIONS = ("ST1220", "ST0001")  # 타슈관제센터 · 타슈관제센터 정비대기 (이름으로 확인)
PLAN_LABEL = "2026-08-11 real raw19"    # Q4의 정본 계획 (25년 11월 · 평일)
PLAN_PERIOD = "25년 11월"
ROUND_STARTS = (5, 10, 15, 20)          # 회차 창 05~10 · 10~15 · 15~20 · 20~05

MOVE_KINDS = ("대여소 간", "센터 출고", "센터 입고")   # '공사 이동' — 장기는 뺀다

COLUMNS = ("period, bike_no, rent_at, rent_station, rent_lat, rent_lon, "
           "return_at, return_station, return_lat, return_lon")


# ───────────────────────────────────────────── 자료 · 사슬 (2부도 이 함수들을 쓴다)

def month_index(period: pd.Series) -> pd.Series:
    """'24년 08월' → 연 × 12 + 월. 달 사이가 이어지는지 셀 때 쓴다."""
    parts = period.str.extract(r"(\d+)년\s*(\d+)월").astype(int)
    return parts[0] * 12 + parts[1]


def assign_blocks(month_idx: pd.Series) -> tuple:
    """연속한 달끼리 같은 구간 번호를 준다. (구간 번호 Series, 구간 → 끝 시각 dict)

    끝 시각은 구간 마지막 달의 **다음 달 1일 0시**다 — 그 뒤의 일은 자료에 없다.
    """
    months = sorted(month_idx.unique())
    block_of, ends, block = {}, {}, 0
    for i, m in enumerate(months):
        if i and m != months[i - 1] + 1:
            block += 1
        block_of[m] = block
        year, mon = divmod(m, 12)
        if mon == 0:
            year, mon = year - 1, 12
        nxt = pd.Timestamp(year=2000 + year, month=mon, day=1) + pd.offsets.MonthBegin(1)
        ends[block] = nxt
    return month_idx.map(block_of), ends


def load_rentals(conn, months=None) -> tuple:
    """대여이력을 사슬에 필요한 열만 읽는다. (표, 구간 끝 dict)"""
    sql = f"SELECT {COLUMNS} FROM rental_history"
    params = ()
    if months:
        sql += f" WHERE period IN ({','.join('?' * len(months))})"
        params = tuple(months)
    df = pd.read_sql(sql, conn, params=params)
    for col in ("rent_at", "return_at"):
        df[col] = pd.to_datetime(df[col], format="%Y-%m-%d %H:%M:%S", errors="coerce")
    for col in ("bike_no", "rent_station", "return_station", "period"):
        df[col] = df[col].astype("category")
    df["month_idx"] = month_index(df["period"].astype(str))
    df["block"], ends = assign_blocks(df["month_idx"])
    return df, ends


def haversine_m(lat1, lon1, lat2, lon2) -> np.ndarray:
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(v, dtype=float)) for v in (lat1, lon1, lat2, lon2))
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6_371_000 * np.arcsin(np.sqrt(a))


def build_pairs(df: pd.DataFrame) -> pd.DataFrame:
    """자전거마다 (직전 반납 → 다음 대여) 쌍. 끊는 쌍은 `status`로 표시만 하고 남긴다.

    status: 'ok' · 'block'(빠진 달을 건넘) · 'reversed'(다음 대여가 직전 반납보다 이르다) · 'missing'(시각 없음)
    """
    d = df.sort_values(["bike_no", "rent_at"], kind="stable")
    same_bike = d["bike_no"].eq(d["bike_no"].shift(-1)).to_numpy()
    cur = d[same_bike]
    nxt = d.shift(-1)[same_bike]
    pairs = pd.DataFrame({
        "bike_no": cur["bike_no"].to_numpy(),
        "from_station": cur["return_station"].astype(str).to_numpy(),
        "from_at": cur["return_at"].to_numpy(),
        "from_lat": cur["return_lat"].to_numpy(), "from_lon": cur["return_lon"].to_numpy(),
        "to_station": nxt["rent_station"].astype(str).to_numpy(),
        "to_at": pd.to_datetime(nxt["rent_at"]).to_numpy(),
        "to_lat": nxt["rent_lat"].to_numpy(dtype=float), "to_lon": nxt["rent_lon"].to_numpy(dtype=float),
        "from_block": cur["block"].to_numpy(), "to_block": nxt["block"].to_numpy(dtype=float),
    })
    pairs["gap_h"] = (pairs["to_at"] - pairs["from_at"]).dt.total_seconds() / 3600
    status = np.full(len(pairs), "ok", dtype=object)
    status[pairs["gap_h"].isna().to_numpy()] = "missing"
    status[(pairs["gap_h"] < 0).to_numpy()] = "reversed"
    status[(pairs["from_block"] != pairs["to_block"]).to_numpy()] = "block"
    pairs["status"] = status
    dist = haversine_m(pairs["from_lat"], pairs["from_lon"], pairs["to_lat"], pairs["to_lon"])
    pairs["dist_m"] = dist
    pairs["same_place"] = (pairs["from_station"] == pairs["to_station"]) | (dist < SAME_PLACE_M)
    return pairs


def classify_moves(pairs: pd.DataFrame, long_days: float = LONG_GAP_DAYS) -> pd.DataFrame:
    """쌍 가운데 이동만 골라 갈래를 붙인다 — 대여소 간 · 센터 출고 · 센터 입고 · 장기."""
    m = pairs[(pairs["status"] == "ok") & ~pairs["same_place"]].copy()
    src_center = m["from_station"].isin(CENTER_STATIONS)
    dst_center = m["to_station"].isin(CENTER_STATIONS)
    kind = np.where(src_center, "센터 출고", np.where(dst_center, "센터 입고", "대여소 간"))
    kind = np.where(m["gap_h"] > long_days * 24, "장기", kind)
    m["kind"] = kind
    m["holiday"] = np.asarray(holiday_mask(m["to_at"]), dtype=bool)
    m["to_month"] = m["to_at"].dt.to_period("M")
    return m


def round_instance(t: pd.Series) -> pd.DataFrame:
    """시각 → (회차가 시작한 날, 회차 번호 0~3). 20~05시는 시작한 날에 묶는다."""
    shifted = t - pd.Timedelta(hours=5)
    h = shifted.dt.hour
    rnd = np.select([h < 5, h < 10, h < 15], [0, 1, 2], default=3)
    return pd.DataFrame({"day": shifted.dt.normalize(), "round": rnd}, index=t.index)


# ───────────────────────────────────────────── Q1 ~ Q4

def report_pairs(pairs: pd.DataFrame) -> None:
    print("\n## 사슬")
    print(f"  연속 쌍 {len(pairs):,}")
    for s, n in pairs["status"].value_counts().items():
        print(f"    {s:9s} {n:,} ({n / len(pairs):.2%})")
    ok = pairs[pairs["status"] == "ok"]
    renum = ok[(ok["from_station"] != ok["to_station"]) & (ok["dist_m"] < SAME_PLACE_M)]
    print(f"  번호는 다르나 50m 안(같은 자리로 봄) {len(renum):,}")


def q1_scale(moves: pd.DataFrame) -> None:
    print("\n## Q1 규모 — 갈래별 하루 이동 대수 (다음 대여 날 기준)")
    days = moves.groupby([moves["to_at"].dt.normalize(), "holiday", "kind"]).size()
    table = days.groupby(level=["holiday", "kind"]).agg(["median", "mean", "min", "max", "count"])
    table.index = table.index.set_levels(["평일", "휴일"], level=0)
    print(table.round(1).to_string())
    print(f"  갈래 합계: {moves['kind'].value_counts().to_dict()}")
    gap = moves.loc[moves["kind"] == "대여소 간", "gap_h"]
    print("  대여소 간 구간(시간) 분위:", gap.quantile([.1, .25, .5, .75, .9]).round(1).to_dict())


def q2_timing(moves: pd.DataFrame) -> pd.DataFrame:
    print("\n## Q2 시각 — 구간이 한 회차 창 안에 통째로 든 이동")
    m = moves[moves["kind"].isin(MOVE_KINDS)]
    a, b = round_instance(m["from_at"]), round_instance(m["to_at"])
    same = (a["day"] == b["day"]) & (a["round"] == b["round"])
    print(f"  공사 이동 {len(m):,} 중 귀속 가능 {int(same.sum()):,} ({same.mean():.1%})")
    names = {i: f"_{s:02d}_{ROUND_STARTS[(i + 1) % 4]:02d}" for i, s in enumerate(ROUND_STARTS)}
    assigned = m[same].assign(round=a.loc[same, "round"].map(names))
    print(assigned.groupby(["holiday", "round"]).size().unstack(0).rename(columns={False: "평일", True: "휴일"})
          .to_string())
    return assigned


def user_flow(df: pd.DataFrame) -> pd.DataFrame:
    """대여소 × 달 × 요일 구분의 이용자 대여 · 반납 수."""
    rent = df[["rent_station", "rent_at"]].rename(columns={"rent_station": "station_id", "rent_at": "t"})
    ret = df[["return_station", "return_at"]].rename(columns={"return_station": "station_id", "return_at": "t"})
    out = []
    for name, frame in (("대여", rent), ("반납", ret)):
        frame = frame.dropna()
        frame = frame.assign(station_id=frame["station_id"].astype(str),
                             month=frame["t"].dt.to_period("M"),
                             holiday=np.asarray(holiday_mask(frame["t"]), dtype=bool))
        out.append(frame.groupby(["station_id", "month", "holiday"]).size().rename(name))
    return pd.concat(out, axis=1).fillna(0)


def operator_flow(moves: pd.DataFrame) -> pd.DataFrame:
    m = moves[moves["kind"].isin(MOVE_KINDS)]
    drop = m.groupby([m["to_station"].rename("station_id"), "to_month", "holiday"]).size().rename("채운")
    pick = m.groupby([m["from_station"].rename("station_id"), "to_month", "holiday"]).size().rename("빼간")
    out = pd.concat([drop, pick], axis=1).fillna(0)
    out.index = out.index.set_names(["station_id", "month", "holiday"])
    return out


def q3_targeting(df: pd.DataFrame, pairs: pd.DataFrame, flow: pd.DataFrame) -> None:
    print("\n## Q3 표적성 — 공사 순공급 대 이용자 순유출, 대여소 × 달 Spearman ρ (센터 제외)")
    rows = []
    for long_days in (LONG_GAP_DAYS, SENSITIVITY_LONG_DAYS):
        op = operator_flow(classify_moves(pairs, long_days))
        joined = flow.join(op, how="left").fillna(0)
        joined = joined[~joined.index.get_level_values("station_id").isin(CENTER_STATIONS)]
        joined["순유출"] = joined["대여"] - joined["반납"]
        joined["순공급"] = joined["채운"] - joined["빼간"]
        for (month, hol), g in joined.groupby(level=["month", "holiday"]):
            if len(g) < 30:
                continue
            rho = g["순유출"].corr(g["순공급"], method="spearman")
            pos = g[g["순유출"] > 0]
            offset = pos["순공급"].clip(lower=0).sum() / pos["순유출"].sum() if pos["순유출"].sum() else np.nan
            rows.append({"장기문턱": f"{long_days}일", "달": str(month), "요일": "휴일" if hol else "평일",
                         "대여소": len(g), "ρ": rho, "순유출 상쇄율": offset})
    table = pd.DataFrame(rows)
    print(table.pivot_table(index=["요일", "달"], columns="장기문턱", values="ρ").round(3).to_string())
    print("\n  달 중앙 ρ:", table.groupby(["장기문턱", "요일"])["ρ"].median().round(3).to_dict())
    print("  달 중앙 순유출 상쇄율(순유출 > 0 대여소에서 공사 순공급 / 순유출):",
          table.groupby(["장기문턱", "요일"])["순유출 상쇄율"].median().round(3).to_dict())
    med = table[(table["장기문턱"] == f"{LONG_GAP_DAYS}일") & (table["요일"] == "평일")]["ρ"].median()
    verdict = ("불균형을 따라간다" if med >= 0.5 else "부분적으로 따라간다" if med >= 0.2 else "표적이 안 보인다")
    print(f"  → 평일 · 장기 {LONG_GAP_DAYS}일 기준 달 중앙 ρ {med:.3f}: '{verdict}' (등록한 읽는 법)")


def q4_overlap(conn, df: pd.DataFrame, moves: pd.DataFrame, flow: pd.DataFrame) -> None:
    print(f"\n## Q4 계획과 겹침 — 정본 `{PLAN_LABEL}` 대 {PLAN_PERIOD} 평일 공사 이동")
    plan = db.load_frame(conn, "pick_drop", run_label=PLAN_LABEL)
    if plan.empty:
        print("  ⚠️ 정본 계획의 pick_drop이 이 DB에 없다 — Q4를 건너뛴다")
        return
    plan = plan[~plan["station_id"].isin(CENTER_STATIONS)]
    plan_sets = {"수거": set(plan.loc[plan["rebal_qty"] < 0, "station_id"]),
                 "배송": set(plan.loc[plan["rebal_qty"] > 0, "station_id"])}
    target = pd.Period(pd.Timestamp(2025, 11, 1), "M")
    m = moves[moves["kind"].isin(MOVE_KINDS) & ~moves["holiday"] & (moves["to_month"] == target)]
    op_counts = {"수거": m["from_station"].value_counts(), "배송": m["to_station"].value_counts()}
    usage = flow.xs((target, False), level=["month", "holiday"]).sum(axis=1)
    usage = usage[~usage.index.isin(CENTER_STATIONS)].sort_values(ascending=False)
    for side, ps in plan_sets.items():
        n = len(ps)
        counts = op_counts[side].drop(labels=list(CENTER_STATIONS), errors="ignore")
        op_top = set(counts.head(n).index)
        busy = set(usage.head(n).index)
        def stat(other):
            inter = len(ps & other)
            return inter / n if n else np.nan, inter / len(ps | other) if ps | other else np.nan
        (op_share, op_j), (busy_share, busy_j) = stat(op_top), stat(busy)
        print(f"  {side}: 계획 {n}곳 · 공사가 그 달 {side}한 대여소 {len(counts)}곳")
        print(f"    공사 상위 {n}  — 계획 대여소 중 겹침 {op_share:.1%} · 자카드 {op_j:.3f}")
        print(f"    이용량 상위 {n}(대조) — 겹침 {busy_share:.1%} · 자카드 {busy_j:.3f}")
        print(f"    계획 대여소 중 공사가 한 번이라도 {side}한 곳 {len(ps & set(counts.index)) / n:.1%}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--months", default="", help="쉼표로 나눈 기간만 (예: '25년 09월'). 비우면 19개월 전부")
    args = parser.parse_args(argv)
    months = [m.strip() for m in args.months.split(",") if m.strip()]
    with db.session() as conn:
        df, _ = load_rentals(conn, months or None)
        print(f"대여이력 {len(df):,}건 · {df['period'].nunique()}개월 · 자전거 {df['bike_no'].nunique():,}대 · "
              f"연속 구간 {df['block'].nunique()}개")
        pairs = build_pairs(df)
        report_pairs(pairs)
        moves = classify_moves(pairs)
        q1_scale(moves)
        q2_timing(moves)
        flow = user_flow(df)
        q3_targeting(df, pairs, flow)
        q4_overlap(conn, df, moves, flow)


if __name__ == "__main__":
    project_config.exit_if_help(__doc__)
    main()
