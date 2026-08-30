"""관측 재고에서 재배치를 골라낼 문턱을 대여이력으로 실측한다 (ML_OPPORTUNITIES ②의 1단계).

`stock_history`의 재고 변화에는 **이용자의 대여·반납**과 **공사 차량의 재배치**가
섞여 있습니다. 소윤상 외(2017)가 같은 방식으로 재고를 모으고 스스로 밝힌 한계이고
([LITERATURE.md](../../docs/연구/LITERATURE.md) 3-21), 분리하지 않으면 재배치 효과를
과대평가하게 됩니다.

⚠️ **`vrp_plan`을 빼는 방법은 성립하지 않습니다** (2026-08-30 확인).
[ML_OPPORTUNITIES.md](../../docs/분석/ML_OPPORTUNITIES.md) ②는 *"본 연구는 자기
재배치 계획을 알고 있으니 그 시각·대여소를 빼면 된다"* 고 적어 두었지만, 두 가지가
어긋납니다.

  · **그 차는 실제로 나간 적이 없습니다.** `vrp_plan`은 본 시스템이 세운 계획이고,
    시스템은 현장에 배치되지 않았습니다([THESIS.md](../../docs/연구/THESIS.md) 10-B).
    `stock_history`를 흔든 것은 **대전교통공사 자신의 재배치**이지 우리 계획이 아닙니다.
  · **뺄 시각 자체가 없습니다.** `vrp_plan`에는 절대 시각 컬럼이 없습니다
    (`cum_sec`은 경로 시작부터의 상대 초, `run_label`은 실행 시각이 아니라 라벨).

[COLLECTOR.md](../../docs/구현/COLLECTOR.md) 8장 4번이 이 관계를 정확히 적어
두었습니다 — `Δstock = 반납 − 대여 − 재배치`, 그리고 그 재배치는 **공사 차량**입니다.
공사의 배차 기록은 우리에게 없으므로, 분리는 **재고 궤적 자체에서** 해야 합니다.

**그래서 이 스크립트가 하는 일:** 이용자만으로 10분 사이에 재고가 얼마나 움직일 수
있는지를 `rental_history`(540만 건)로 재서, "이건 사람이 아니라 차다"라고 볼 문턱을
**추측이 아니라 실측으로** 정합니다. 대여이력에는 재배치가 섞이지 않습니다 — 이용자
한 건 한 건이 대여소·시각과 함께 기록된 것이므로, 여기서 만든 Δ는 **순수하게
이용자 몫**입니다.

    Δ이용자(대여소, 10분) = 그 칸에 반납된 건수 − 그 칸에서 대여된 건수

문턱을 백분위로 읽고, 그 문턱을 썼을 때 **멀쩡한 이용자 변화를 차로 오인하는 비율**
(거짓양성)이 얼마인지 함께 냅니다. 문턱은 공짜가 아니므로 대가를 같이 봐야 합니다.

⚠️ **이것은 문턱을 정하는 1단계입니다.** 실제 분리의 정확도는 `stock_history`와
`rental_history`가 **같은 기간에** 있을 때라야 검증할 수 있습니다(지금은 안 겹칩니다 —
대여이력 2025-01~2026-03, 재고 수집 2026-08~). 그때 `Δ관측 − Δ이용자 = 재배치`로
정확히 갈라 이 문턱이 맞았는지 재십시오.

실행:
    python experiments/structure/stock_decompose.py
    python experiments/structure/stock_decompose.py --periods "25년 11월,26년 03월"
    python experiments/structure/stock_decompose.py --bucket 10 --hours 9-17
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd

import db

# 수집 간격(분)과 같은 칸으로 묶어야 문턱을 그대로 쓸 수 있다.
DEFAULT_BUCKET_MIN = 10

# 수집 창(09~17시)에 맞춘 기본 시간대. 창 밖 시각의 분포를 섞으면 문턱이 흐려진다 —
# 새벽은 이용이 거의 없어 |Δ|가 작고, 그만큼 문턱을 낮춰 잡게 된다.
DEFAULT_HOURS = "9-17"

# 문턱 후보로 볼 백분위. 높을수록 차를 놓치고(거짓음성), 낮을수록 사람을 차로 본다.
PERCENTILES = (99.0, 99.5, 99.9, 99.95, 99.99)


def parse_hours(text: str) -> list:
    """'9-17' → [9..17], '9,10,11' → [9,10,11]."""
    text = text.strip()
    if "-" in text:
        start, end = (int(p) for p in text.split("-", 1))
        return list(range(start, end + 1))
    return [int(p) for p in text.split(",") if p.strip()]


def user_deltas(period: str, bucket_min: int, hours: list) -> pd.DataFrame:
    """대여이력에서 **이용자만의** 대여소별·시간칸별 재고 변화를 만든다.

    반납은 +1, 대여는 -1이다. 두 쪽을 각각 칸으로 묶어 더한 뒤 합친다 —
    한 대여소의 한 칸에서 반납 3건·대여 1건이면 Δ = +2다.

    **관측이 없는 칸(아무도 안 빌리고 안 반납한 칸)은 Δ=0이지만 여기에는 안 나온다.**
    문턱은 '움직인 칸'의 분포에서 읽어야 하므로 그게 맞다 — 0을 전부 채워 넣으면
    분포가 0으로 눌려 백분위가 무의미해진다.
    """
    frame, source = db.read_rental_source(
        period, columns=["대여일시", "대여_대여소ID", "반납일시", "반납_대여소ID"])
    if frame.empty:
        return pd.DataFrame(columns=["station_id", "bucket", "delta"]), source

    freq = f"{bucket_min}min"
    parts = []
    for time_col, station_col, sign in (("대여일시", "대여_대여소ID", -1),
                                        ("반납일시", "반납_대여소ID", +1)):
        part = frame[[time_col, station_col]].copy()
        part.columns = ["when", "station_id"]
        part["when"] = pd.to_datetime(part["when"], errors="coerce")
        part = part.dropna(subset=["when", "station_id"])
        part = part[part["when"].dt.hour.isin(hours)]
        if part.empty:
            continue
        part["bucket"] = part["when"].dt.floor(freq)
        counted = part.groupby(["station_id", "bucket"]).size().reset_index(name="n")
        counted["delta"] = counted["n"] * sign
        parts.append(counted[["station_id", "bucket", "delta"]])

    if not parts:
        return pd.DataFrame(columns=["station_id", "bucket", "delta"]), source

    merged = (pd.concat(parts, ignore_index=True)
              .groupby(["station_id", "bucket"], as_index=False)["delta"].sum())
    return merged, source


def summarize(deltas: pd.DataFrame, label: str) -> dict:
    """|Δ|의 분포와 백분위 문턱을 낸다."""
    magnitude = deltas["delta"].abs().to_numpy()
    moved = magnitude[magnitude > 0]          # 0인 칸은 문턱과 무관하다
    if moved.size == 0:
        return {}

    row = {"기간": label, "움직인 칸": int(moved.size),
           "평균|Δ|": float(moved.mean()), "최대|Δ|": int(moved.max())}
    for p in PERCENTILES:
        row[f"p{p}"] = float(np.percentile(moved, p))
    return row


def false_positive_table(magnitude: np.ndarray, thresholds: list) -> list:
    """문턱을 썼을 때 **이용자 칸이 차로 오인되는 비율**. 문턱의 대가다."""
    rows = []
    total = magnitude.size
    for t in thresholds:
        flagged = int((magnitude >= t).sum())
        rows.append((t, flagged, flagged / total * 100 if total else float("nan")))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="재배치 판별 문턱을 대여이력으로 실측한다")
    parser.add_argument("--periods", default=None,
                        help="쉼표로 구분. 생략하면 DB에 적재된 전 기간")
    parser.add_argument("--bucket", type=int, default=DEFAULT_BUCKET_MIN,
                        help=f"시간 칸(분). 기본 {DEFAULT_BUCKET_MIN} — 수집 간격과 맞춘다")
    parser.add_argument("--hours", default=DEFAULT_HOURS,
                        help=f"볼 시각. 기본 {DEFAULT_HOURS}(수집 창)")
    args = parser.parse_args()

    hours = parse_hours(args.hours)
    if args.periods:
        periods = [p.strip() for p in args.periods.split(",") if p.strip()]
    else:
        with db.session() as conn:
            periods = [r[0] for r in conn.execute(
                "SELECT DISTINCT period FROM rental_history ORDER BY period")]
    if not periods:
        print("대여이력이 없습니다. python tools/load_rentals.py 로 적재하세요.")
        return 1

    print(f"칸 {args.bucket}분 · 시각 {hours[0]}~{hours[-1]}시 · 기간 {len(periods)}개")
    print("대여이력에는 재배치가 섞이지 않는다 — 여기서 나온 Δ는 순수하게 이용자 몫이다.\n")

    rows, pooled = [], []
    for period in periods:
        deltas, source = user_deltas(period, args.bucket, hours)
        if deltas.empty:
            print(f"  {period}: 자료 없음")
            continue
        row = summarize(deltas, period)
        if row:
            rows.append(row)
            # **기간별 표와 같은 분모를 쓴다** — 0인 칸을 섞으면 합산 백분위만
            # 아래로 눌려 두 표가 서로 다른 문턱을 말하게 된다.
            magnitude = deltas["delta"].abs().to_numpy()
            pooled.append(magnitude[magnitude > 0])
        print(f"  {period}: {len(deltas):>8,}칸 ({source})")

    if not rows:
        print("\n요약할 자료가 없습니다.")
        return 1

    table = pd.DataFrame(rows)
    print(f"\n=== 이용자만의 |Δ재고| — 기간별 ===")
    header = f"{'기간':>10} {'움직인 칸':>10} {'평균':>7} {'최대':>6}"
    for p in PERCENTILES:
        header += f" {'p' + str(p):>8}"
    print(header)
    for _, r in table.iterrows():
        line = f"{r['기간']:>10} {r['움직인 칸']:>10,.0f} {r['평균|Δ|']:>7.2f} {r['최대|Δ|']:>6.0f}"
        for p in PERCENTILES:
            line += f" {r[f'p{p}']:>8.0f}"
        print(line)

    every = np.concatenate(pooled)
    print(f"\n=== 전 기간 합산 (움직인 칸 {every.size:,}개) ===")
    for p in PERCENTILES:
        print(f"  p{p:<6} = {np.percentile(every, p):.0f}대")
    print(f"  최대   = {every.max():.0f}대")

    print(f"\n=== 문턱의 대가 — 이용자 칸을 차로 오인하는 비율 ===")
    print("  (분모는 위와 같은 '움직인 칸'이다 — 아무도 안 쓴 칸은 애초에 판별 대상이 아니다)")
    print(f"{'문턱(|Δ|≥)':>10} {'오인 칸':>12} {'비율':>9}")
    candidates = sorted({int(np.ceil(np.percentile(every, p))) for p in PERCENTILES})
    for threshold, flagged, share in false_positive_table(every, candidates):
        print(f"{threshold:>10} {flagged:>12,} {share:>8.3f}%")

    print("\n판정: 문턱을 낮추면 사람을 차로 오인하고, 높이면 차를 놓친다.")
    print("      위 표에서 오인 비율이 충분히 작은 가장 낮은 문턱을 고른다.")
    print("      ⚠️ 이 문턱은 아직 **검증 전**이다 — stock_history와 rental_history가")
    print("      같은 기간에 쌓이면 'Δ관측 − Δ이용자 = 재배치'로 정확히 갈라 확인할 것.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
