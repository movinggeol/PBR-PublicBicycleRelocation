"""원천 대여 이력의 기초 탐색(EDA).

**그래프를 파일로 남긴다** — 화면에 띄우지 않는다. 파이프라인이 이 파일을
subprocess로 돌리므로, `plt.show()`를 부르면 창이 뜬 채 **파이프라인 전체가
멈춘다**(사람이 닫아 줄 때까지). 그래서 백엔드를 Agg로 고정한다.

산출물은 **두 벌**이다. 쓰임이 다르기 때문이다:

| | 무엇에 | 왜 |
| --- | --- | --- |
| `EDA.html` | 웹·화면 | 다크 모드를 따라가고 커서를 대면 값이 뜬다. `catalog.py`가 `.html`을 이미 서빙하므로 **보안 규약을 넓히지 않고** `/data`에 나온다 |
| `*.png` | 문서·논문 | 인쇄물과 마크다운에는 이미지가 맞다 |

HTML은 `webapp/charts.py`의 인라인 SVG를 그대로 쓴다 — 그리는 규칙이 웹 화면과
갈리지 않게 하려는 것이다(`charts.py` 첫머리 참고: 이미지는 다크 모드에서 흰
판이 뜨고 값을 못 읽는다).

  - 월별 대여량 (하루 평균) — 계절성
  - 시간대별 대여량 — 회차(`_05_10` 등)를 왜 그렇게 나눴는지
  - 요일별 대여량 — 평일/휴일을 왜 섞지 않는지

입력은 **DB의 대여이력 전체**(여러 달)를 우선 쓰고, 없으면 원천 CSV 한 기간으로
물러선다. 계절성은 한 달만 봐서는 보이지 않기 때문이다.
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib
# ⚠️ pyplot을 import하기 **전에** 백엔드를 정해야 한다. 배치 실행이라 창을
#    띄우면 안 되고, 창 없는 환경(CI)에서는 기본 백엔드가 아예 실패한다.
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd

import db
from project_config import PP_ROOT, get_runtime_config
from webapp import charts

EDA_DIR = PP_ROOT / "EDA"

# 회차 경계. step1~4가 쓰는 `_05_10` 같은 시간대와 같은 값이라, 그래프에
# 그어 두면 "왜 여기서 끊었나"를 그림 한 장으로 답할 수 있다.
DURATION_EDGES = (5, 10, 15, 20)

WEEKDAY_LABELS = ("월", "화", "수", "목", "금", "토", "일")


def _use_korean_font() -> None:
    """한글이 깨지지 않게 글꼴을 고른다.

    ⚠️ 경로를 하드코딩하지 않는다 — `C:/Windows/Fonts/malgun.ttf`는 리눅스·CI에
    없어서 그때 죽는다. 설치된 것 중 **있는 것을 골라 쓰고**, 하나도 없으면
    글꼴을 건드리지 않는다(그래프는 나오고 한글만 깨진다 — 죽는 것보다 낫다).
    """
    from matplotlib import font_manager

    installed = {f.name for f in font_manager.fontManager.ttflist}
    for name in ("Malgun Gothic", "AppleGothic", "NanumGothic",
                 "Noto Sans CJK KR", "Noto Sans KR"):
        if name in installed:
            plt.rcParams["font.family"] = name
            break
    else:
        print("[경고] 한글 글꼴을 찾지 못했습니다 — 그래프의 한글이 깨질 수 있습니다.")

    # 글꼴을 바꾸면 유니코드 마이너스가 두부로 뜬다(음수 축이 있는 그래프).
    plt.rcParams["axes.unicode_minus"] = False


def _save(fig, name: str) -> Path:
    """PNG로 저장하고 경로를 알린다. 화면에는 띄우지 않는다."""
    EDA_DIR.mkdir(parents=True, exist_ok=True)
    path = EDA_DIR / name
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)          # 닫지 않으면 그림이 메모리에 쌓인다
    print(f"저장: {path}")
    return path


def now_month(df: pd.DataFrame):
    '''
    원본 데이터에서 현재 달에 해당하는 데이터만 필터링(1달 단위)
    '''
    now = datetime.now()
    month = now.strftime('%m')

    temp_df = df[df['대여일시'].dt.month == int(month)]
    print(f"현재({month}월)에 해당하는 데이터로 필터링한 shape : {temp_df.shape}")


def month_graph(df: pd.DataFrame):
    """월별 대여량을 그린다 — **하루 평균**으로 잰다.

    총량으로 그리면 2월(28일)이 실제보다 낮게, 31일 달이 높게 보인다.
    자료의 달 수가 고르지 않을 때도(어떤 달은 며칠치뿐) 총량은 거짓말을 한다.
    """
    daily = df.groupby(df["대여일시"].dt.to_period("M")).agg(
        건수=("대여일시", "size"),
        일수=("대여일시", lambda s: s.dt.date.nunique()),
    )
    if daily.empty:
        print("월별 그래프: 자료가 없어 건너뜁니다.")
        return None

    daily["하루평균"] = daily["건수"] / daily["일수"]
    labels = [str(p) for p in daily.index]

    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(labels, daily["하루평균"], marker="o", color="#0066cc")
    ax.set_title("월별 대여량 (하루 평균)")
    ax.set_ylabel("하루 평균 대여 건수")
    ax.grid(True, alpha=.3)
    # 0에서 시작해야 배율이 과장되지 않는다. 위쪽은 12% 띄운다 — 최댓값이
    # 축 꼭대기에 붙으면 아래의 '최다' 주석이 제목 자리로 올라간다(실제로 겹쳤다).
    ax.set_ylim(0, daily["하루평균"].max() * 1.12)
    fig.autofmt_xdate(rotation=45)

    # 최저·최고 달을 짚어 준다 — 계절성의 폭이 이 프로젝트의 전제다.
    lo, hi = daily["하루평균"].idxmin(), daily["하루평균"].idxmax()
    ratio = daily["하루평균"].max() / max(daily["하루평균"].min(), 1)
    ax.annotate(f"{hi} 최다", (str(hi), daily.loc[hi, "하루평균"]),
                textcoords="offset points", xytext=(0, 9), ha="center", fontsize=9)
    ax.annotate(f"{lo} 최소", (str(lo), daily.loc[lo, "하루평균"]),
                textcoords="offset points", xytext=(0, 9), ha="center", fontsize=9)
    ax.set_xlabel(f"최다/최소 = {ratio:.1f}배")

    print(f"월별 하루 평균: 최소 {lo} {daily['하루평균'].min():,.0f}건 · "
          f"최다 {hi} {daily['하루평균'].max():,.0f}건 ({ratio:.1f}배)")
    _save(fig, "월별_대여량.png")
    return labels, list(daily["하루평균"]), ratio


def hour_graph(df: pd.DataFrame):
    """시간대별 대여량. 회차 경계(`_05_10` 등)를 함께 긋는다.

    파이프라인이 하루를 네 회차로 나누는 근거가 이 그림이다 — 출퇴근 봉우리가
    어디 있는지 보이면 왜 그 시각에서 끊었는지 설명이 된다.
    """
    counts = df["대여일시"].dt.hour.value_counts().sort_index()
    counts = counts.reindex(range(24), fill_value=0)
    days = df["대여일시"].dt.date.nunique() or 1

    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.bar(counts.index, counts / days, color="#0066cc")
    ax.set_title("시간대별 대여량 (하루 평균)")
    ax.set_xlabel("시각")
    ax.set_ylabel("하루 평균 대여 건수")
    ax.set_xticks(range(0, 24, 2))
    ax.grid(True, axis="y", alpha=.3)

    # 봉우리가 꼭대기에 닿으면 아래 설명이 막대에 묻힌다 — 위를 15% 띄운다.
    ax.set_ylim(0, (counts / days).max() * 1.15)
    for edge in DURATION_EDGES:
        ax.axvline(edge - .5, color="#d70015", linestyle="--", linewidth=1, alpha=.7)
    ax.text(.99, .97, "빨간 선 = 회차 경계 (05·10·15·20시)", transform=ax.transAxes,
            ha="right", va="top", fontsize=9, color="#d70015",
            bbox=dict(facecolor="white", edgecolor="none", alpha=.85, pad=2))

    peak = int((counts / days).idxmax())
    print(f"시간대별: 봉우리 {peak}시 ({counts[peak] / days:,.0f}건/일)")
    _save(fig, "시간대별_대여량.png")
    return [str(h) for h in counts.index], list(counts / days), peak


def weekday_graph(df: pd.DataFrame):
    """요일별 대여량. 평일과 휴일을 왜 섞지 않는지가 여기서 보인다."""
    weekday = df["대여일시"].dt.weekday
    counts = weekday.value_counts().sort_index().reindex(range(7), fill_value=0)
    # 요일마다 자료에 든 날 수가 다르므로(달 경계) 하루 평균으로 잰다.
    days = df.groupby(weekday)["대여일시"].apply(lambda s: s.dt.date.nunique())
    days = days.reindex(range(7)).fillna(1)
    per_day = counts / days

    fig, ax = plt.subplots(figsize=(8, 4.5))
    colors = ["#0066cc"] * 5 + ["#d70015"] * 2      # 주말만 다른 색
    ax.bar(WEEKDAY_LABELS, per_day, color=colors)
    ax.set_title("요일별 대여량 (하루 평균)")
    ax.set_ylabel("하루 평균 대여 건수")
    ax.grid(True, axis="y", alpha=.3)
    ax.set_ylim(0, per_day.max() * 1.15)
    ax.text(.99, .97, "붉은 막대 = 주말", transform=ax.transAxes,
            ha="right", va="top", fontsize=9, color="#d70015",
            bbox=dict(facecolor="white", edgecolor="none", alpha=.85, pad=2))

    workday, weekend = per_day[:5].mean(), per_day[5:].mean()
    print(f"요일별: 평일 평균 {workday:,.0f}건/일 · 주말 평균 {weekend:,.0f}건/일 "
          f"({weekend / max(workday, 1):.2f}배)")
    _save(fig, "요일별_대여량.png")
    return list(WEEKDAY_LABELS), list(per_day), weekend / max(workday, 1)


# 독립 HTML이라 base.html의 CSS를 물려받지 못한다. **그래프가 쓰는 토큰만**
# 담는다 — 화면 전체를 흉내 내려 들면 두 벌을 유지하게 된다.
HTML_HEAD = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>대여 이력 탐색 (EDA)</title>
<style>
  :root {
    color-scheme: light dark;
    --ink: #1d1d1f; --ink-2: #424245; --ink-3: #6e6e73; --ink-4: #86868b;
    --blue: #0066cc; --critical-ink: #d70015;
    --page: #f5f5f7; --surface: #ffffff; --divider-soft: #e8e8ed;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --ink: #f5f5f7; --ink-2: #d2d2d7; --ink-3: #a1a1a6; --ink-4: #86868b;
      --blue: #2997ff; --critical-ink: #ff453a;
      --page: #000000; --surface: #1c1c1e; --divider-soft: #2c2c2e;
    }
  }
  body { margin: 0; padding: 24px; background: var(--page); color: var(--ink);
         font: 14px/1.5 -apple-system, "Segoe UI", "Malgun Gothic", sans-serif; }
  main { max-width: 900px; margin: 0 auto; }
  h1 { font-size: 24px; margin: 0 0 4px; letter-spacing: -.5px; }
  .lead { color: var(--ink-3); margin: 0 0 24px; }
  .card { background: var(--surface); border-radius: 12px; padding: 16px;
          margin-bottom: 16px; }
  .card h2 { font-size: 15px; margin: 0 0 2px; color: var(--ink-2); }
  .card .hint { display: block; color: var(--ink-4); font-size: 12px;
                margin-bottom: 10px; }
  .viz { width: 100%; height: auto; display: block; overflow: visible; }
  .viz-grid { stroke: var(--divider-soft); stroke-width: 1; }
  .viz-line { fill: none; stroke: var(--blue); stroke-width: 2;
              stroke-linejoin: round; stroke-linecap: round; }
  .viz-dot { fill: var(--blue); }
  .viz-dot.ring { stroke: var(--surface); stroke-width: 2; }
  .viz-bar { fill: var(--blue); }
  .viz-bar.warn { fill: var(--critical-ink); }
  .viz-divider { stroke: var(--critical-ink); stroke-width: 1; opacity: .55; }
  .viz-hit { fill: transparent; cursor: default; }
  .viz-tick { fill: var(--ink-4); font-size: 10px;
              font-variant-numeric: tabular-nums; }
  .viz-axis { fill: var(--ink-3); font-size: 11px; }
  .viz-axis.note { fill: var(--critical-ink); font-size: 10px; }
  .viz-value { fill: var(--ink); font-size: 11px; font-weight: 600; }
  .empty { color: var(--ink-4); }
  footer { color: var(--ink-4); font-size: 12px; margin-top: 24px; }
</style></head><body><main>
"""


def write_html(month, hour, weekday, *, span: str, rows: int) -> Path:
    """세 그래프를 한 장짜리 HTML로 묶는다.

    **PNG와 같은 값을 그린다** — 위 함수들이 이미 집계해 돌려준 것을 받아 쓴다.
    다시 계산하면 두 산출물이 조용히 갈릴 수 있다.

    `webapp/charts.py`를 쓰는 이유는 그리는 규칙(격자·눈금·커서 설명)이 웹
    화면과 한 벌이어야 하기 때문이다. 그쪽을 고치면 여기도 같이 바뀐다.
    """
    parts = [HTML_HEAD,
             "<h1>대여 이력 탐색</h1>",
             f'<p class="lead">{span} · 대여 {rows:,}건. '
             f'막대와 점에 커서를 대면 값이 뜹니다.</p>']

    def card(title, hint, svg):
        parts.append(f'<div class="card"><h2>{title}</h2>'
                     f'<span class="hint">{hint}</span>{svg}</div>')

    if month:
        labels, values, ratio = month
        card("월별 대여량 (하루 평균)",
             f"계절성. 최다/최소 {ratio:.1f}배 — 달마다 일수가 달라 하루 평균으로 잽니다.",
             # 월 이름은 짧아 전부 적어도 겹치지 않는다. 기본값(처음·끝만)은
             # 실행 라벨이 길어서 정한 규칙이라 여기엔 맞지 않는다.
             charts.line(labels, values, title="월별 대여량", unit="건",
                         width=840, height=220, all_ticks=True))
    if hour:
        labels, values, peak = hour
        card("시간대별 대여량 (하루 평균)",
             f"봉우리 {peak}시. 붉은 선은 회차 경계(05·10·15·20시)입니다 — "
             f"파이프라인이 하루를 넷으로 나누는 자리입니다.",
             charts.vbar(labels, values, title="시간대별 대여량", unit="건",
                         width=840, height=240,
                         dividers=DURATION_EDGES,
                         divider_note="붉은 선 = 회차 경계"))
    if weekday:
        labels, values, ratio = weekday
        card("요일별 대여량 (하루 평균)",
             f"주말은 평일의 {ratio:.2f}배. 평일과 휴일은 수요 구조가 달라 "
             f"파이프라인이 섞지 않습니다.",
             charts.vbar(labels, values, title="요일별 대여량", unit="건",
                         width=840, height=220,
                         highlight=(5, 6), divider_note="붉은 막대 = 주말"))

    parts.append('<footer>step0_eda/EDA.py가 만들었습니다. '
                 'PNG 같은 폴더에 함께 있습니다.</footer></main></body></html>')

    EDA_DIR.mkdir(parents=True, exist_ok=True)
    path = EDA_DIR / "EDA.html"
    path.write_text("".join(parts), encoding="utf-8")
    print(f"저장: {path}")
    return path


def load_history(config) -> pd.DataFrame:
    """대여이력을 읽는다 — **DB의 전 기간**이 있으면 그쪽을 쓴다.

    계절성은 한 달만 봐서는 보이지 않는다. DB에 여러 달이 적재돼 있으면
    전부 읽고, 없으면 원천 CSV 한 기간으로 물러선다.
    """
    try:
        with db.session() as conn:
            periods = [row[0] for row in conn.execute(
                "SELECT DISTINCT period FROM rental_history ORDER BY period")]
    except Exception as err:            # DB가 없거나 스키마 이전이어도 CSV로 간다
        print(f"[경고] DB 조회 실패({type(err).__name__}) — CSV로 물러섭니다.")
        periods = []

    if periods:
        print(f"DB에서 {len(periods)}개 기간을 읽습니다: {periods[0]} ~ {periods[-1]}")
        frames = []
        for period in periods:
            frame, _ = db.read_rental_source(period, columns=["대여일시"])
            frames.append(frame)
        return pd.concat(frames, ignore_index=True)

    frame, source = db.read_rental_source(
        config.period, csv_path=config.raw_path, columns=["대여일시"])
    print(f"{config.period} 한 기간만 읽었습니다(출처: {source}) — "
          f"계절성은 여러 달이 있어야 보입니다.")
    return frame


def main() -> None:
    config = get_runtime_config()

    df = load_history(config)
    if df.empty:
        print("대여이력이 없어 EDA를 건너뜁니다.")
        return

    df["대여일시"] = pd.to_datetime(df["대여일시"], errors="coerce")
    df = df.dropna(subset=["대여일시"])
    print(f"대여 {len(df):,}건 · {df['대여일시'].min():%Y-%m-%d} ~ "
          f"{df['대여일시'].max():%Y-%m-%d}")

    _use_korean_font()
    month = month_graph(df)
    hour = hour_graph(df)
    weekday = weekday_graph(df)

    write_html(month, hour, weekday,
               span=f"{df['대여일시'].min():%Y-%m-%d} ~ {df['대여일시'].max():%Y-%m-%d}",
               rows=len(df))


if __name__ == '__main__':
    main()
