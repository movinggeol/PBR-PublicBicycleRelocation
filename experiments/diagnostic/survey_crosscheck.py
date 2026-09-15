"""공식 교통현황조사(2025)와 본 연구 대여이력을 같은 주로 맞대 본다 (2026-09-15).

## 왜 이 대조가 필요한가

논문 1장이 타슈 이용 특성의 출처로 「대전광역시 교통현황조사」를 인용한다. 그런데
2026-09-15에 2025년판을 읽어 보니 2024년판의 수치 일부가 이상했다 — 관제센터 한 곳이
도시 전체 대여의 17.7%였다(2025년판은 3.0%). **어느 판을 믿을지 짐작으로 고를 일이
아니다.** 2025년판의 조사 주간(2025-10-20~26)이 본 연구 `rental_history`
(2025-01~2026-03) 안에 들어오므로 직접 맞대 볼 수 있다.

## 무엇을 묻는가

① 요일별 모양이 같은가 — 요일변동계수(일 대여 / 주 일평균)
② 수준이 같은가 — 날마다 본 연구 / 보고서 비율, 그 비율이 날마다 일정한가
③ 많이 빌리고 반납하는 곳이 같은가 — 보고서의 최다 지점 5곳
④ 규모 — 그 주와 그 달에 실제로 대여된 자전거·대여소 수

## 판정하지 않는다

진단이다. 채택할 파라미터가 없다. 비율이 날마다 일정하면 *"같은 원천을 범위·정의만
다르게 센 것"*, 날마다 흔들리면 *"다른 것을 센 것"* 으로 읽는다 — 원인 확인은
운영기관 문의로 넘긴다(docs/연구/THESIS.md 10-D절 트랙 ③).

⚠️ **보고서 수치는 이미지 PDF에서 손으로 옮겼다**(텍스트 층이 없다). 옮겨 적은 일별
대여로 요일변동계수를 다시 계산해 보고서가 적은 계수와 맞는지 먼저 찍는다 — 한 칸을
잘못 옮기면 여기서 드러난다. 쪽 번호를 옆에 적었으니 의심되면 원문 쪽을 다시 본다.

> 재현: `python experiments/diagnostic/survey_crosscheck.py`
> (`rental_history`에 25년 10월이 적재된 DB가 필요하다 — 결과는 docs/연구/THESIS.md 10-D절)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import project_config  # noqa: F401,E402  (cp949 출력 가드 — 1.26.194)
import db  # noqa: E402

WEEK_START, WEEK_END = "2025-10-20", "2025-10-27"   # 끝은 포함하지 않는다
MONTH_START, MONTH_END = "2025-10-01", "2025-11-01"

# 「2025년도 대전광역시 교통현황조사 및 분석보고서」 342쪽 '시간대별 이용량' 표의 '계'
REPORT_DAILY = {
    "2025-10-20": 21632, "2025-10-21": 21391, "2025-10-22": 23591,
    "2025-10-23": 25158, "2025-10-24": 27083, "2025-10-25": 24595,
    "2025-10-26": 20151,
}
DAYS = len(REPORT_DAILY)
# 같은 보고서 341쪽 — 요일변동계수(월~일)와 최다 대여·반납 지점(건/일)
REPORT_COEF = (0.93, 0.92, 1.01, 1.08, 1.16, 1.05, 0.86)
# 이름은 본 연구 대여소명에서 찾을 열쇠말만 적는다(보고서 표기와 괄호가 다르다)
REPORT_TOP_RENT = (("타슈관제센터", 692), ("무역전시관", 181), ("어은동 유성구청", 165),
                   ("어은동 한빛아파트", 165), ("충남대학교 학생회관", 160))
REPORT_TOP_RETURN = (("타슈관제센터", 734), ("무역전시관", 185), ("어은동 유성구청", 169),
                     ("어은동 한빛아파트", 168), ("충남대학교 학생회관", 160))


def daily_rentals(conn) -> dict:
    """조사 주간의 날짜 → 대여 건수."""
    rows = conn.execute(
        "SELECT substr(rent_at, 1, 10), COUNT(*) FROM rental_history "
        "WHERE rent_at >= ? AND rent_at < ? GROUP BY 1 ORDER BY 1",
        (WEEK_START, WEEK_END)).fetchall()
    return dict(rows)


def station_names(conn) -> dict:
    """조사 주간에 대여가 있었던 대여소 → 이름. 반납 쪽 테이블에는 이름이 없다."""
    return dict(conn.execute(
        "SELECT rent_station, MAX(rent_station_name) FROM rental_history "
        "WHERE rent_at >= ? AND rent_at < ? GROUP BY rent_station",
        (WEEK_START, WEEK_END)).fetchall())


def per_day(conn, column: str, at: str) -> dict:
    """대여소 → 조사 주간 하루 평균 건수. column·at은 대여 또는 반납 쪽 한 쌍이다."""
    assert (column, at) in (("rent_station", "rent_at"), ("return_station", "return_at"))
    rows = conn.execute(
        f"SELECT {column}, COUNT(*) * 1.0 / ? FROM rental_history "
        f"WHERE {at} >= ? AND {at} < ? GROUP BY {column}",
        (DAYS, WEEK_START, WEEK_END)).fetchall()
    return dict(rows)


def scale(conn, start: str, end: str) -> tuple:
    """(대여된 자전거 수, 대여가 있었던 대여소 수)."""
    return conn.execute(
        "SELECT COUNT(DISTINCT bike_no), COUNT(DISTINCT rent_station) FROM rental_history "
        "WHERE rent_at >= ? AND rent_at < ?", (start, end)).fetchone()


def print_top(label: str, counts: dict, names: dict, report: tuple) -> None:
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    rank = {sid: i + 1 for i, (sid, _) in enumerate(ranked)}
    print(f"\n③ 최다 {label} 지점 (건/일) — 보고서 5곳이 본 연구에서 몇 위인가")
    for keyword, value in report:
        hits = [sid for sid, name in names.items() if name and keyword in name]
        found = " · ".join(f"{sid} {names[sid]} {counts.get(sid, 0):.0f}건 {rank.get(sid, '-')}위"
                           for sid in hits) or "이름으로 못 찾음"
        print(f"  보고서 {keyword} {value}건 → {found}")
    top5 = " · ".join(f"{names.get(sid, sid)} {v:.0f}" for sid, v in ranked[:5])
    print(f"  본 연구 상위 5곳: {top5}")


def main() -> None:
    with db.session() as conn:
        ours = daily_rentals(conn)
        if len(ours) != DAYS:
            raise SystemExit(f"조사 주간 {DAYS}일 중 {len(ours)}일만 적재돼 있습니다 — "
                             "25년 10월 대여이력을 먼저 적재하세요(tools/load_rentals.py).")
        names = station_names(conn)
        rent = per_day(conn, "rent_station", "rent_at")
        ret = per_day(conn, "return_station", "return_at")
        week_scale = scale(conn, WEEK_START, WEEK_END)
        month_scale = scale(conn, MONTH_START, MONTH_END)

    rep_mean = sum(REPORT_DAILY.values()) / DAYS
    typo = max(abs(v / rep_mean - c) for v, c in zip(REPORT_DAILY.values(), REPORT_COEF))
    print(f"옮겨 적은 보고서 일별 대여로 다시 낸 계수 vs 보고서 계수: 최대 차 {typo:.3f}"
          f" ({'반올림 안' if typo <= 0.005 else '⚠️ 옮겨 적기를 다시 볼 것'})")

    our_mean = sum(ours.values()) / DAYS
    print("\n① 요일 모양 · ② 수준")
    print(f"  {'날짜':<11}{'본 연구':>8}{'보고서':>8}{'비율':>8}{'계수(본)':>9}{'계수(보)':>9}")
    ratios = []
    for (day, rep), coef in zip(REPORT_DAILY.items(), REPORT_COEF):
        ratios.append(ours[day] / rep)
        print(f"  {day:<11}{ours[day]:>8,}{rep:>8,}{ours[day] / rep:>8.1%}"
              f"{ours[day] / our_mean:>9.2f}{coef:>9.2f}")
    gap = max(abs(ours[d] / our_mean - c) for d, c in zip(REPORT_DAILY, REPORT_COEF))
    print(f"  일평균 본 연구 {our_mean:,.0f} · 보고서 {rep_mean:,.0f} → {our_mean / rep_mean:.1%}"
          f" (날마다 {min(ratios):.1%}~{max(ratios):.1%}) · 요일변동계수 최대 차 {gap:.2f}")

    print_top("대여", rent, names, REPORT_TOP_RENT)
    print_top("반납", ret, names, REPORT_TOP_RETURN)

    print(f"\n④ 규모 — 조사 주간 자전거 {week_scale[0]:,}대·대여소 {week_scale[1]:,}곳 / "
          f"10월 한 달 {month_scale[0]:,}대·{month_scale[1]:,}곳")
    print("  보고서 16쪽: 대여소 기반 2,305대 · free-floating 2,500대 "
          "(2024년판과 같은 값 — 도시교통정비 중기계획은 2,305대를 2020년 기준으로 인용한다)")


if __name__ == "__main__":
    main()
