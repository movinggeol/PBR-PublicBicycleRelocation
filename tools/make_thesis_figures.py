"""논문 그림을 만든다 — 표만 131개고 그림이 0장이었다 (1.26.135).

**왜 필요한가.** 2026-09-07 논문 점검에서 본문의 표가 131개(6장만 69개)인데
**그림이 하나도 없다**는 것을 발견했다. 표는 값을 정확히 전하지만 *분포*와
*맞교환*을 못 보여 준다 — 6.5의 반복 실험은 `평균 ± 표준편차` 한 줄로
줄었고, 6.3의 "결품을 줄인 대가로 포화가 는다"는 두 표를 눈으로 맞대야만
보인다.

**무엇을 그리나.** 표로는 잘 안 보이는 것만 그린다. 표를 그림으로 옮겨
그리지 않는다 — 그러면 같은 값이 두 곳에 살아 한쪽이 낡는다.

  · 그림 6-1  결품 편익과 포화 **대가를 한 축에** (표 둘을 맞대야 보였다)
  · 그림 6-2  19개월 반복의 **분포** (표는 평균±표준편차로 줄인다)
  · 그림 5-1  `z` 격자를 **모집단 셋으로** — 모집단이 승자를 정한다
  · 그림 1-1  평일·휴일 순수요 **부호 반전** (32~34%가 반대다 — 26년 03월)
  · 그림 4-1  파이프라인 구조

**그림 안에 번호 · 주장 · 내부 표기를 넣지 않는다 (2026-10-13, 1.26.366).** 위 그림들은(당시 여섯) 초안용이라
'그림 6-3 … 거리당으로도 앞선다' 같은 제목을 품고 있었는데, 원고가 고쳐진 뒤에도 그림 속 주장은
남아 본문과 반대말을 했다(6.5절). 캡션은 원고가 그림 아래에 달므로 그림 안에는 축 · 범례 · 칸 이름만
두고, 시간대는 코드(`_05_10`)가 아니라 원고의 이름('05~10시')으로 찍는다(`DURATION_NAME`).

**원고 자료 자리를 채우는 그림 (2026-09-18~, 1.26.239).** 원고(docs/연구/논문/)의
`(자리 [그림 n-m] …)` 문단을 채운다. 캡션은 원고가 그림 아래에 따로 달므로 그림
안에 '그림 n-m' 제목을 넣지 않는다.

  · 그림 3-2  재배치량의 tanh 완화 — **운영 함수 `compute_rebal_qty()`로** 그린다
  · 그림 4-3  대여이력 월별 건수 (빠진 달 · 겨울 달)
  · 그림 5-2  26년 2~3월 일별 대여와 배율 산정 구간 (2026-10-14까지 5-3)
  · 그림 2-1  정적·동적 재배치와 본 연구의 회차 구조 (개념도, 1.26.240)
  · 그림 4-2  대여소 1,368곳과 차고지 — `station_info` 스냅샷 (행정 경계는 그리지 않는다)

**원고에서 뺀 그림 (2026-10-14, 원고 분량 줄이기).** 옛 4-5(결원 형평성) · 5-2(|mu| 분포) ·
6-3(이동거리와 편익) · 8-1(공식 통계 대조) · 8-2(거점 기준)를 원고가 뺐으므로 함수와 그림 파일도
지웠다. 다시 그리려면 git 이력에서 함수를 되살린다.

**3장 그림 넷 (2026-09-18, 1.26.241).** 3장 넷은 정본 스냅샷을 **평일로 다시
계산한** 한 회차(`_canon_round`)를 함께 쓴다 — DB에 저장된 `2026-08-11 real`의 계획은 평일/
휴일 분리 전에 짠 것이라 그대로 그리면 3장 본문(평일)과 조건이 다르다.

  · 그림 3-1  3단계 분해 지도 네 칸 — `baseline_compare.py`의 입력·후보·계획 함수를 그대로
  · 그림 3-3  수급 균형 조정 전후 — 조정이 군집 하나를 **비운다**(17 → 16)는 것도 보인다
  · 그림 3-4  한 군집의 방문 순서·적재량 — 시간 열은 **지금 켜진 이동시간 식**을 따른다(게이트 A)
  · 그림 3-6  재고 궤적과 결품 — 궤적을 운영 함수 `_simulate_stock()`의 결품·포화와 **맞대어** 그린다

**이동시간 식 그림 (2026-09-19, 1.26.242).** 도로 패널은 집 PC DB에만 있어 회사 PC에서는 건너뛴다.

  · 그림 3-5  직선거리와 도로 이동시간 — `road_time_model.py`의 표본 읽기·적합을 그대로,
             판정일(`ROAD_FIT_TO`)까지 잘라 채택 계수와 같은 표본으로

**재현성**: 그림의 원천은 전부 `experiments/`의 CSV와 DB다. 원천이 없으면
그 그림만 건너뛰고 무엇이 없어서인지 말한다 — **조용히 빈 그림을 만들지
않는다.**

실행:
    python tools/make_thesis_figures.py
    python tools/make_thesis_figures.py --only 6-1,6-2
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates  # noqa: E402  (그림 5-2의 날짜 축)
import matplotlib.pyplot as plt
import matplotlib.ticker  # noqa: E402  (그림 4-3의 만 건 축)
import numpy as np
import pandas as pd

OUT = ROOT / "docs/연구/초안/그림"
EXP = ROOT / "experiments"

# 흑백 인쇄에서도 갈리도록 **색과 함께 해치·마커를** 준다.
STYLE = {
    "B0": dict(색="#d9d9d9", 해치="", 마커="o", 이름="B0 무재배치"),
    "B1": dict(색="#9ecae1", 해치="//", 마커="s", 이름="B1 그리디"),
    "B3": dict(색="#c7c7c7", 해치="\\\\", 마커="^", 이름="B3 지리 균등 군집"),
    "P": dict(색="#3182bd", 해치="", 마커="D", 이름="P 제안"),
}
DURATIONS = ["_05_10", "_10_15", "_15_20"]
# 그림 안의 시간대 이름 — 원고는 '05~10시'로 쓴다. 코드(`_05_10`)를 그대로 찍지 않는다.
DURATION_NAME = {"_05_10": "05~10시", "_10_15": "10~15시", "_15_20": "15~20시", "_20_05": "20~05시"}
# 그림 1-1의 달 — DB에서 가장 늦은 달을 고르면 다음 달 자료가 들어온 날 그림과 본문(32~34%)이 조용히 갈린다.
FIG_1_1_PERIOD = "26년 03월"
# 6장 그림 둘의 원천. **채택한 이동시간 식(320.4초 · 32.11km/h)으로 돌린 19개월 반복**이다 —
# 옛 `repeat_12month_pinned.csv`는 상수 속도(25km/h)로 잰 것이라, 그 파일로 그리면 옛 그림 6-3이
# *"km당으로는 대등하다"* 는 **철회된 주장**을 그린다(EXPERIMENTS 33장). 재현은 부록 A에 있다.
# 12개월 파일(`repeat_12month_on.csv`)은 IQR로 깎인 대여이력에서 나왔다 — 원본 19개월로
# 다시 잰 이 파일로 바꿨다(EXPERIMENTS 42장).
REPEAT = "baseline/repeat_19month_on.csv"
REPEAT_WHAT = "19개월 반복 결과(채택한 이동시간 식)"
# 그림 3-5의 표본 끝 — 게이트 A 판정일. 채택 계수(320.4초 · 32.11km/h)가 이날까지의 패널에서 나왔다.
ROAD_FIT_TO = "2026-09-15"


def setup():
    """한글 글꼴 — 없으면 그림의 라벨이 두부(□)가 된다."""
    import matplotlib.font_manager as fm
    names = {f.name for f in fm.fontManager.ttflist}
    for cand in ("Malgun Gothic", "NanumGothic", "Gulim", "AppleGothic"):
        if cand in names:
            plt.rcParams["font.family"] = cand
            break
    else:
        print("  [경고] 한글 글꼴을 못 찾았습니다 - 라벨이 깨질 수 있습니다.")
    plt.rcParams["axes.unicode_minus"] = False       # 마이너스가 □로 나오는 것 막기
    plt.rcParams["figure.dpi"] = 150
    plt.rcParams["savefig.dpi"] = 220                 # 인쇄 제본 대비 — 화면 150 · 저장 220
    plt.rcParams["savefig.bbox"] = "tight"
    # 차트 틀 정리 — 기본 matplotlib 박스(네 변 전부)는 잉크만 늘린다.
    # 위·오른쪽 테두리를 지우고, 남는 선·눈금·격자는 옅은 회색 한 톤으로 통일한다.
    plt.rcParams["axes.spines.top"] = False
    plt.rcParams["axes.spines.right"] = False
    plt.rcParams["axes.edgecolor"] = "#9a9890"
    plt.rcParams["axes.labelcolor"] = "#0b0b0b"
    plt.rcParams["text.color"] = "#0b0b0b"
    plt.rcParams["xtick.color"] = "#52514e"
    plt.rcParams["ytick.color"] = "#52514e"
    plt.rcParams["axes.grid"] = True
    plt.rcParams["axes.axisbelow"] = True              # 격자가 막대·선 뒤로
    plt.rcParams["grid.color"] = "#e1e0d9"
    plt.rcParams["grid.linewidth"] = 0.7
    plt.rcParams["grid.alpha"] = 1.0                   # 색 자체가 이미 옅다 — 더 섞지 않는다
    plt.rcParams["font.size"] = 11
    plt.rcParams["axes.titlesize"] = 13
    plt.rcParams["axes.titleweight"] = "bold"
    plt.rcParams["axes.labelsize"] = 11
    plt.rcParams["legend.frameon"] = False              # 범례 테두리 상자도 잉크다
    plt.rcParams["legend.fontsize"] = 10
    OUT.mkdir(parents=True, exist_ok=True)


def save(fig, name: str, caption: str):
    path = OUT / f"{name}.png"
    fig.savefig(path)
    plt.close(fig)
    print(f"  OK {path.relative_to(ROOT)}  - {caption}")


def _need(path: Path, what: str) -> pd.DataFrame | None:
    """원천이 없으면 **무엇이 없어서인지 말하고** 건너뛴다."""
    if not path.exists():
        print(f"  건너뜀 - {what}이(가) 없습니다: {path.relative_to(ROOT)}")
        return None
    return pd.read_csv(path)


# ────────────────────────────────────────────────────────────── 6-2
def fig_6_2():
    """19개월 반복의 분포. 표는 이것을 평균±표준편차 한 줄로 줄인다."""
    frame = _need(EXP / REPEAT, REPEAT_WHAT)
    if frame is None:
        return
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.6), sharey=True)
    order = ["B0", "B1", "P"]
    for ax, duration in zip(axes, DURATIONS):
        part = frame[frame["duration"] == duration]
        data = [part[part["method"] == m]["stockout_after"].to_numpy() for m in order]
        bp = ax.boxplot(data, patch_artist=True, widths=0.55,
                        medianprops=dict(color="black", lw=1.4))
        for patch, m in zip(bp["boxes"], order):
            patch.set_facecolor(STYLE[m]["색"])
            patch.set_hatch(STYLE[m]["해치"])
        # 점을 겹쳐 **n을 눈으로 보이게** 한다 — 상자만 그리면 36개인지 5개인지 모른다
        for i, values in enumerate(data, start=1):
            ax.scatter(np.random.default_rng(0).normal(i, 0.06, len(values)), values,
                       s=6, color="black", alpha=0.35, zorder=3)
        ax.set_xticks(range(1, len(order) + 1))
        ax.set_xticklabels([STYLE[m]["이름"] for m in order], fontsize=8)
        ax.set_title(f"{DURATION_NAME[duration]}  (방법마다 n={len(data[0])})", fontsize=10)
    axes[0].set_ylabel("재배치 후 결품 시간 (시간)")
    save(fig, "그림6-2_결품분포", "반복의 분포 (표는 평균±표준편차로 줄인다)")


# ────────────────────────────────────────────────────────────── 6-1
def fig_6_1():
    """편익과 대가를 한 축에. 표 둘을 맞대야만 보이던 것이다."""
    frame = _need(EXP / REPEAT, REPEAT_WHAT)
    if frame is None:
        return
    base = (frame[frame["method"] == "B0"]
            .set_index(["duration", "period", "seed"])[["stockout_after", "saturation_after"]])
    rows = []
    for method in ("B1", "P"):
        part = frame[frame["method"] == method].set_index(["duration", "period", "seed"])
        joined = part.join(base, rsuffix="_b0").dropna()
        for duration in DURATIONS:
            sub = joined.xs(duration, level="duration")
            rows.append(dict(
                duration=duration, method=method,
                편익=float((sub["stockout_after_b0"] - sub["stockout_after"]).mean()),
                대가=float((sub["saturation_after"] - sub["saturation_after_b0"]).mean())))
    summary = pd.DataFrame(rows)

    fig, ax = plt.subplots(figsize=(8.4, 4.0))
    x = np.arange(len(DURATIONS))
    width = 0.36
    for i, method in enumerate(("B1", "P")):
        part = summary[summary["method"] == method].set_index("duration").loc[DURATIONS]
        off = (i - 0.5) * width
        ax.bar(x + off, part["편익"], width, label=f"{STYLE[method]['이름']} — 결품 감소(편익)",
               color=STYLE[method]["색"], hatch=STYLE[method]["해치"], edgecolor="black", lw=0.6)
        ax.bar(x + off, -part["대가"], width, color="white", edgecolor="#b03a2e",
               hatch="xx", lw=0.8,
               label="포화 증가(대가) — 두 방법 같은 무늬" if i == 0 else None)
        for xi, (b, c) in zip(x + off, zip(part["편익"], part["대가"])):
            ax.text(xi, b + 0.04, f"{b:.2f}", ha="center", fontsize=8)
            # 포화가 **늘어난** 양이다 — <표 6-2>가 '+0.16'으로 적는 값이라 그림도 같은 부호로 찍는다
            ax.text(xi, -c - 0.06, f"+{c:.2f}", ha="center", va="top",
                    fontsize=8, color="#b03a2e")
    ax.axhline(0, color="black", lw=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels([DURATION_NAME[d] for d in DURATIONS])
    ax.set_ylim(-0.72, 2.05)                     # 값 라벨이 범례와 겹치지 않게
    ax.set_ylabel("무재배치 대비 변화 (시간)\n위: 결품 감소 · 아래: 포화 증가")
    ax.legend(fontsize=8, loc="upper right", ncol=1)
    # ⚠️ 두 막대를 빼서 하나로 합치지 않는다 — 같은 '시간'이라도 무게가 다르다. 그 말은 캡션이 한다(6.2).
    save(fig, "그림6-1_편익과_대가", "결품 편익과 포화 대가를 한 축에")


# ────────────────────────────────────────────────────────────── 5-1
def fig_5_1():
    """모집단이 승자를 정하고 있었다 — 18장의 결론을 그림 하나로."""
    frame = _need(EXP / "params/z_fixedpop_3pop_2511.csv", "z 격자 3모집단 결과")
    if frame is None:
        return
    pops = [(c[len("after::"):], c) for c in frame.columns if c.startswith("after::")]
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.9), sharex=True)
    for ax, duration in zip(axes, DURATIONS):
        part = frame[frame["duration"] == duration]
        for name, col in pops:
            curve = part.groupby("z")[col].mean()
            marker = "D" if "전체" in name else ("o" if "좁음" in name else "s")
            # 원천의 열 이름은 '좁음(z=1.65)' · '넓음(z=2.33)' · '전체대여소'다 — 본문의 이름으로 찍는다
            shown = ("전체 대여소" if "전체" in name
                     else f"$z$={name[name.index('=') + 1:].rstrip(')')}의 작업 대상")
            ax.plot(curve.index, curve.to_numpy(), marker=marker, ms=4, lw=1.3, label=shown)
            best = curve.idxmin()
            ax.scatter([best], [curve.loc[best]], s=90, facecolors="none",
                       edgecolors="red", lw=1.4, zorder=5)
        ax.axvline(1.99, color="#888", ls="--", lw=1)
        ax.set_title(DURATION_NAME[duration], fontsize=10)
        ax.set_xlabel("안전계수 $z$")
    axes[0].set_ylabel("재배치 후 결품 시간 (시간)")
    axes[0].legend(fontsize=7.5, title="모집단", title_fontsize=7.5)
    save(fig, "그림5-1_z_모집단", "모집단이 승자를 정한다 (EXPERIMENTS 18장)")


# ────────────────────────────────────────────────────────────── 1-1
def fig_1_1():
    """평일과 휴일의 순수요 부호가 반대인 대여소 — 1.2의 근거."""
    try:
        import db
        from project_config import select_day_type
        from backtest_demand import daily_window_demand
    except Exception as exc:                       # pragma: no cover - 환경 의존
        print(f"  건너뜀 - 모듈을 못 불러왔습니다: {exc}")
        return

    with db.session() as conn:
        periods = [r[0] for r in conn.execute(
            "SELECT DISTINCT period FROM net_demand").fetchall()]
        if not periods:
            print("  건너뜀 - DB에 net_demand가 없습니다.")
            return
        period = FIG_1_1_PERIOD
        if period not in periods:
            print(f"  건너뜀 - DB에 {period}의 net_demand가 없습니다.")
            return
        net = db.load_frame(conn, "net_demand", period=period)

    fig, axes = plt.subplots(1, 3, figsize=(11, 3.8))
    for ax, duration in zip(axes, DURATIONS):
        parts = {}
        for day_type in ("weekday", "holiday"):
            frame = select_day_type(net, "date", day_type)
            if frame.empty:
                continue
            parts[day_type] = (daily_window_demand(frame, duration)
                               .groupby("station_id")["demand"].mean())
        if len(parts) < 2:
            ax.text(0.5, 0.5, "휴일 자료 없음", ha="center", transform=ax.transAxes)
            continue
        joined = pd.concat(parts, axis=1).dropna()
        joined.columns = ["평일", "휴일"]
        opposite = (np.sign(joined["평일"]) * np.sign(joined["휴일"])) < 0
        ax.scatter(joined.loc[~opposite, "평일"], joined.loc[~opposite, "휴일"],
                   s=8, color="#bbb", label="같은 방향")
        ax.scatter(joined.loc[opposite, "평일"], joined.loc[opposite, "휴일"],
                   s=10, color="#b03a2e", label="반대 방향")
        ax.axhline(0, color="black", lw=0.8)
        ax.axvline(0, color="black", lw=0.8)
        # 이상치 몇 곳이 축을 다 먹어 가운데 뭉치가 안 보인다 — 1~99 분위로 자른다.
        # ⚠️ **비율은 자르기 전 전체로** 센다. 축만 좁히는 것이지 표본을 버리는 것이 아니다.
        lim = max(abs(np.percentile(joined[["평일", "휴일"]].to_numpy(), [1, 99])))
        ax.set_xlim(-lim, lim)
        ax.set_ylim(-lim, lim)
        ax.set_title(f"{DURATION_NAME[duration]}   부호 반대 {opposite.mean() * 100:.0f}% "
                     f"({int(opposite.sum()):,}/{len(joined):,}곳)", fontsize=10)
        ax.set_xlabel("평일 순수요 (대/일)")
        print(f"    {duration}: 부호 반대 {int(opposite.sum())}/{len(joined)}곳 "
              f"({opposite.mean() * 100:.1f}%)")
    axes[0].set_ylabel("휴일 순수요 (대/일)")
    axes[0].legend(fontsize=8)
    save(fig, "그림1-1_평일휴일_부호반전", "1.2의 32~34%를 눈으로")


# ────────────────────────────────────────────────────────────── 4-1
def fig_4_1():
    """파이프라인 구조. 4.1의 글을 그림으로 — 심사자가 가장 먼저 찾는 그림이다."""
    # 단계 이름은 원고 4.3절의 것이다 — 'step0' · 'greedy VRP' · 'KPI'는 원고에 한 번도 나오지 않는다
    steps = [
        ("0단계\n수집과 전처리", "대여이력 · 실시간 재고\n→ 순수요 · 목표 재고"),
        ("1단계\n선정과 군집화", "작업 대상 선정\n→ K-Medoids"),
        ("2단계\n최적화", "정수선형계획 (수량)\n→ 그리디 경로 (순서)"),
        ("3단계\n지도 시각화", "도로 경로를 얹은\n지도"),
        ("4단계\n성과 평가", "결품 · 포화 시간\n이동시간"),
    ]
    fig, ax = plt.subplots(figsize=(11, 2.9))
    ax.set_xlim(0, len(steps) * 2.2)
    ax.set_ylim(0, 3)
    ax.axis("off")
    for i, (title, body) in enumerate(steps):
        x = i * 2.2 + 0.12
        ax.add_patch(plt.Rectangle((x, 0.85), 1.85, 1.35, facecolor="#eaf2fb",
                                   edgecolor="#3182bd", lw=1.4, zorder=2))
        ax.text(x + 0.93, 1.86, title, ha="center", va="center",
                fontsize=10, fontweight="bold", zorder=3)
        ax.text(x + 0.93, 1.24, body, ha="center", va="center", fontsize=8.5, zorder=3)
        if i:
            ax.annotate("", xy=(x, 1.5), xytext=(x - 0.35, 1.5),
                        arrowprops=dict(arrowstyle="-|>", color="#3182bd", lw=1.6))
    ax.text(len(steps) * 1.1, 0.35,
            "각 단계는 산출물을 CSV 파일과 SQLite에 함께 기록한다",
            ha="center", fontsize=8.5, color="#444")
    save(fig, "그림4-1_파이프라인", "4.1의 구조도")


# ──────────────────────────────────────────── 원고 자료 자리 (2026-09-18~)
# 아래 그림은 원고(docs/연구/논문/)의 '(자리 [그림 n-m] …)' 문단을 채운다.
# ⚠️ **그림 안에 '그림 n-m' 제목을 넣지 않는다.** 원고는 캡션을 그림 아래에 따로
#    달므로(학과 양식), 그림 안에도 쓰면 제목이 두 번 나온다. 위의 옛 그림들은
#    초안용이라 제목을 품고 있다.

def _period_key(label: str) -> int:
    """'25년 11월' -> 2511."""
    year, month = label.split("년")
    return int(year.strip()) * 100 + int(month.replace("월", "").strip())


# ────────────────────────────────────────────────────────────── 3-2
def fig_3_2():
    """재배치량의 tanh 완화 — 식 (3.5)를 **운영 함수로** 그린다.

    식을 여기서 다시 쓰면 운영 코드가 바뀌어도 그림이 옛 식을 그린다. 그래서
    격차만 다른 가짜 대여소를 `compute_rebal_qty()`에 넣어 나온 값을 그린다.
    """
    from pipeline.step0_collect.calculate_target_qty import MAX_CAPACITY, compute_rebal_qty
    Q = MAX_CAPACITY
    gap = np.round(np.arange(-30, 30.001, 0.05), 2)
    # 순유출(mu ≥ 0): target = mu + z·0 = gap, stock 0 → 격차 = gap
    # 순유입(mu < 0): target = stock + mu = 100 + gap, stock 100 → 격차 = gap
    stats = pd.DataFrame({"mu": gap, "sigma": 0.0,
                          "stock": np.where(gap >= 0, 0.0, 100.0), "parking_lot": 1000.0})
    rebal = compute_rebal_qty(stats.copy())["rebal_qty"].to_numpy()
    clipped = np.clip(np.trunc(gap), -Q, Q)

    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    ax.plot(gap, clipped, color="#999", ls="--", lw=1.3, label=f"단순 절단  $[-Q, Q]$")
    ax.step(gap, rebal, where="post", color="#3182bd", lw=1.8,
            label=r"$\tanh$ 완화 (식 (3.5), 정수화 뒤)")
    for g in (5, 8):
        r = int(rebal[np.argmin(abs(gap - g))])
        ax.scatter([g], [r], color="#b03a2e", zorder=5, s=28)
        ax.annotate(f"격차 {g}대 → {r}대", (g, r), xytext=(g + 1.5, r - 2.6), fontsize=9,
                    arrowprops=dict(arrowstyle="-", color="#b03a2e", lw=0.8))
    top = int(rebal.max())
    ax.axhline(top, color="#b03a2e", ls=":", lw=1)
    ax.axhline(-top, color="#b03a2e", ls=":", lw=1)
    ax.text(-29.5, top + 0.4, f"실효 상한 {top}대 (적재 용량 $Q$={Q}대에 닿지 않는다)",
            fontsize=8.5, color="#b03a2e")
    ax.set_xlabel("목표 재고와 현재 재고의 격차  $t_i - q_i$ (대)")
    ax.set_ylabel("재배치량  $r_i$ (대)")
    ax.set_ylim(-Q - 2, Q + 2)
    # 오른쪽 아래 구석에 두면 -9대 점선이 범례 첫 줄을 가로지른다 — 점선 위로 올린다
    ax.legend(fontsize=8.5, loc="lower right", bbox_to_anchor=(1.0, 0.14))
    save(fig, "그림3-2_재배치량_완화", "tanh 완화와 단순 절단 (3.3.3)")


# ────────────────────────────────────────────────────────────── 4-3
def fig_4_3():
    """사용한 대여이력의 월별 건수 — 빠진 달과 겨울 달을 한눈에 (4.2)."""
    try:
        import db
    except Exception as exc:                       # pragma: no cover - 환경 의존
        print(f"  건너뜀 - 모듈을 못 불러왔습니다: {exc}")
        return
    with db.session() as conn:
        counts = dict(conn.execute(
            "SELECT period, COUNT(*) FROM rental_history GROUP BY period").fetchall())
    if not counts:
        print("  건너뜀 - DB에 rental_history가 없습니다.")
        return
    keys = sorted(_period_key(p) for p in counts)
    months, cursor = [], keys[0]
    while cursor <= keys[-1]:                      # 빠진 달도 자리를 둔다
        months.append(cursor)
        cursor = cursor + 1 if cursor % 100 < 12 else (cursor // 100 + 1) * 100 + 1
    label = {_period_key(p): p for p in counts}
    winter = {1, 2, 12}

    fig, ax = plt.subplots(figsize=(9.6, 3.8))
    for i, key in enumerate(months):
        tick = f"{key // 100:02d}.{key % 100:02d}"
        if key not in label:
            # '공개 자료 없음'이 아니다 — 그 달 이름의 파일은 공개됐으나 내용이 다른 달의 것이라 쓰지 않았다(4.2)
            ax.text(i, 12_000, "쓰지\n않음", ha="center", va="bottom", fontsize=8, color="#888")
            continue
        n = counts[label[key]]
        is_winter = key % 100 in winter
        ax.bar(i, n, color="#c6dbef" if is_winter else "#3182bd",
               hatch="//" if is_winter else "", edgecolor="black", lw=0.5)
        ax.text(i, n + 8_000, f"{n / 10_000:.1f}", ha="center", fontsize=8)
    ax.set_xticks(range(len(months)))
    ax.set_xticklabels([f"{k // 100:02d}.{k % 100:02d}" for k in months], fontsize=8.5)
    ax.set_xlabel("연.월")
    ax.set_ylabel("월 대여 건수")
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v / 10_000:.0f}만"))
    ax.set_ylim(0, max(counts.values()) * 1.15)
    handles = [plt.Rectangle((0, 0), 1, 1, color="#3182bd"),
               plt.Rectangle((0, 0), 1, 1, facecolor="#c6dbef", hatch="//", edgecolor="black", lw=0.5)]
    ax.legend(handles, ["그 밖의 달", "겨울 달(12·1·2월)"], fontsize=8.5, loc="upper left")
    total = sum(counts.values())
    save(fig, "그림4-3_월별_대여건수", f"대여이력 {len(counts)}개월 {total:,}건")


# ────────────────────────────────────────────────────────────── 5-2
def fig_5_2():
    """계절 전환 달의 일별 대여 — 배율 s를 구하는 첫 14일이 어디인지 (5.4).

    ⚠️ 배율 자체는 **대여 건수가 아니라 |mu|>2 대여소의 순수요**로 구한다(식 3.3).
    이 그림은 '왜 보정이 필요한가'를 보이는 것이지 배율의 계산을 보이는 것이 아니다.
    """
    try:
        import db
        from project_config import is_holiday
    except Exception as exc:                       # pragma: no cover - 환경 의존
        print(f"  건너뜀 - 모듈을 못 불러왔습니다: {exc}")
        return
    with db.session() as conn:
        rows = conn.execute(
            "SELECT substr(rent_at, 1, 10) AS d, COUNT(*) FROM rental_history "
            "WHERE period IN ('26년 02월', '26년 03월') GROUP BY d").fetchall()
    if not rows:
        print("  건너뜀 - 26년 02~03월 대여이력이 없습니다.")
        return
    daily = pd.Series(dict(rows))
    daily.index = pd.to_datetime(daily.index)
    daily = daily.sort_index()
    daily = daily[(daily.index >= "2026-02-01") & (daily.index <= "2026-03-31")]
    hol = pd.Series([is_holiday(d) for d in daily.index], index=daily.index)

    fig, ax = plt.subplots(figsize=(9.6, 3.8))
    ax.axvspan(pd.Timestamp("2026-03-01"), pd.Timestamp("2026-03-14 23:59"),
               color="#fdd0a2", alpha=0.5, label="배율 산정 구간 (3월 첫 14일)")
    ax.plot(daily.index, daily.to_numpy(), color="#888", lw=0.8, zorder=1)
    ax.scatter(daily.index[~hol], daily[~hol], s=16, color="#3182bd", label="평일", zorder=3)
    ax.scatter(daily.index[hol], daily[hol], s=22, marker="^", color="#b03a2e", label="휴일", zorder=3)
    feb_weekday = daily[(daily.index.month == 2) & ~hol].mean()
    ax.hlines(feb_weekday, pd.Timestamp("2026-02-01"), pd.Timestamp("2026-03-31"),
              color="#3182bd", ls="--", lw=1)
    # 글자가 점·선과 겹치지 않게 배율 구간 오른쪽 아래에 흰 바탕으로 둔다
    ax.text(pd.Timestamp("2026-03-16"), feb_weekday * 0.9,
            f"2월 평일 평균 {feb_weekday:,.0f}건", fontsize=8.5, color="#3182bd",
            va="top", bbox=dict(facecolor="white", edgecolor="none", alpha=0.85, pad=1.5))
    ax.set_ylabel("일별 대여 건수")
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%m-%d"))
    ax.legend(fontsize=8.5, loc="upper left")
    ax.set_ylim(0, daily.max() * 1.2)
    save(fig, "그림5-2_계절전환_일별대여", "26년 2~3월 일별 대여와 배율 산정 구간")


# ────────────────────────────────────────────────────────────── 4-2
def fig_4_2():
    """대여소 분포와 차고지 — 실험 스냅샷 `2026-08-11 real`의 `station_info` 1,368곳 (4.2).

    ⚠️ **행정 경계는 그리지 않는다.** 저장소에 경계 자료가 없고, 밖에서 받아 오면 출처와
    사용 조건이 따라붙는다. 대여소 분포가 시가지 모양을 그대로 보여 준다.
    거치대 수가 빈 대여소(19곳)는 크기를 줄 수 없어 속이 빈 점으로 찍는다 — 빼면 1,368곳이
    아니게 된다.
    """
    try:
        import db
        from project_config import DEPOT_LAT, DEPOT_LON
    except Exception as exc:                       # pragma: no cover - 환경 의존
        print(f"  건너뜀 - 모듈을 못 불러왔습니다: {exc}")
        return
    label = "2026-08-11 real"
    with db.session() as conn:
        frame = pd.read_sql("SELECT lat, lon, parking_lot, stock FROM station_info "
                            "WHERE run_label = ?", conn, params=(label,))
    if frame.empty:
        print(f"  건너뜀 - station_info에 '{label}' 스냅샷이 없습니다.")
        return
    known = frame["parking_lot"].notna()
    fig, ax = plt.subplots(figsize=(7.4, 7.0))
    sc = ax.scatter(frame.loc[known, "lon"], frame.loc[known, "lat"],
                    s=4 + frame.loc[known, "parking_lot"].clip(upper=30) * 1.2,
                    c=frame.loc[known, "stock"].clip(upper=15), cmap="viridis_r",
                    alpha=0.85, edgecolor="none")
    ax.scatter(frame.loc[~known, "lon"], frame.loc[~known, "lat"], s=14, facecolors="none",
               edgecolors="#666", lw=0.7, label=f"거치대 수 없음 ({int((~known).sum())}곳)")
    ax.scatter([DEPOT_LON], [DEPOT_LAT], marker="*", s=320, color="#b03a2e",
               edgecolor="white", lw=1.0, zorder=5, label="차고지")
    # 위도에서 경도 1도의 길이가 짧아지므로 가로세로 비를 맞춘다
    ax.set_aspect(1 / np.cos(np.deg2rad(frame["lat"].mean())))
    lon0, lat0 = frame["lon"].min(), frame["lat"].min()
    km_per_deg_lon = 111.32 * np.cos(np.deg2rad(frame["lat"].mean()))
    ax.plot([lon0, lon0 + 5 / km_per_deg_lon], [lat0, lat0], color="black", lw=2.2)
    ax.text(lon0 + 2.5 / km_per_deg_lon, lat0 + 0.004, "5km", ha="center", fontsize=9)
    cbar = fig.colorbar(sc, ax=ax, shrink=0.6, pad=0.02)
    cbar.set_label("재고 (대, 15 이상은 15)")
    ax.xaxis.set_major_locator(matplotlib.ticker.MultipleLocator(0.05))   # 눈금 글자가 겹치지 않게
    ax.xaxis.set_major_formatter(matplotlib.ticker.FormatStrFormatter("%.2f"))
    ax.set_xlabel("경도")
    ax.set_ylabel("위도")
    ax.legend(fontsize=8.5, loc="upper left")      # 오른쪽 위는 대여소가 있는 자리라 글자 위에 점이 찍혔다
    save(fig, "그림4-2_대여소_분포", f"대여소 {len(frame):,}곳과 차고지")


# ────────────────────────────────────────────────────────────── 2-1
def fig_2_1():
    """정적·동적 재배치와 본 연구의 회차 구조 — 개념도라 자료가 필요 없다 (2.2.1)."""
    rounds = [(5, 10), (10, 15), (15, 20), (20, 29)]        # 20~05시는 다음 날 5시(29시)까지
    fig, ax = plt.subplots(figsize=(10.4, 3.6))
    ax.set_xlim(4, 29.5)
    ax.set_ylim(-0.3, 3.3)
    ax.grid(False)
    rows = [(2.6, "정적 재배치", "이용이 거의 없는 시간에 한 번"),
            (1.6, "동적 재배치", "운영 중에 계속"),
            (0.6, "본 연구", "하루 네 회차, 회차 안에서는 정적")]
    for y, name, note in rows:
        ax.text(3.8, y, name, ha="right", va="center", fontsize=10, fontweight="bold")
        ax.text(3.8, y - 0.3, note, ha="right", va="center", fontsize=8, color="#555")
    # 정적: 새벽 한 번
    ax.add_patch(plt.Rectangle((26, 2.4), 2.5, 0.4, color="#9ecae1"))
    ax.annotate("", xy=(26, 2.6), xytext=(26, 3.05),
                arrowprops=dict(arrowstyle="-|>", color="#3182bd"))
    # 동적: 연속
    # color=를 주면 면과 테두리가 같은 색이 되어 빗금이 묻힌다 — 면과 테두리를 따로 준다
    ax.add_patch(plt.Rectangle((5, 1.4), 24, 0.4, facecolor="#9ecae1", edgecolor="#3182bd",
                               hatch="//", lw=0.5))
    # 본 연구: 네 회차
    for i, (a, b) in enumerate(rounds):
        ax.add_patch(plt.Rectangle((a + 0.1, 0.4), b - a - 0.2, 0.4, color="#3182bd"))
        ax.annotate("", xy=(a + 0.1, 0.6), xytext=(a + 0.1, 1.05),
                    arrowprops=dict(arrowstyle="-|>", color="#b03a2e"))
        if i:
            ax.text(a, 0.2, "×", ha="center", va="center", color="#b03a2e", fontsize=11)
    ax.text(5.2, 1.1, "▼ 계획 시점: 재고 스냅샷 + 직전 달 순수요 통계", fontsize=8, color="#b03a2e",
            va="center")
    ax.text(16.5, -0.15, "× 앞 회차의 결과는 뒤 회차의 초기 재고로 넘어가지 않는다", fontsize=8,
            color="#b03a2e", ha="center")
    ax.set_xticks([5, 10, 15, 20, 24, 29])
    ax.set_xticklabels(["05시", "10시", "15시", "20시", "24시", "05시"])
    ax.set_yticks([])
    for side in ("left", "right", "top"):
        ax.spines[side].set_visible(False)
    save(fig, "그림2-1_회차구조", "정적·동적 재배치와 본 연구의 회차 구조 (2.2.1)")


# ────────────────────────────────────────────────────── 3장 공용: 정본 회차 재계산
CANON_LABEL = "2026-08-11 real"
CANON_PERIOD = "25년 11월"
CANON_SEED = 42
_CANON: dict = {}
_CANON_WHY: dict = {}     # 계산하지 못한 까닭 — 그림마다 다시 말한다(캐시된 None이 조용히 넘어가지 않게)


def _load_experiment(name: str, relpath: str):
    """실험 스크립트를 모듈로 불러온다 — 그쪽 함수를 **그대로** 쓰기 위해서다."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canon_round(duration: str = "_05_10") -> dict | None:
    """정본 스냅샷을 **평일 통계로 다시 계산한** 한 회차 — 3장 그림 넷이 같은 결과를 쓴다.

    🔴 **DB에 저장된 `2026-08-11 real`의 군집·경로를 그대로 그리지 않는다.** 그 실행은
    2026-08-12에 만들어졌고 평일/휴일 분리(1.14.0)는 08-13에 들어왔다 — 저장된 계획은
    **평일과 휴일을 섞은 통계**로 짠 것이다(`runs.day_type`이 비어 있다). 논문 3장은
    제6장 P와 같은 조건(평일 · 씨앗 42)을 말하므로, 대조군 실험(`baseline_compare.py`)의
    `load_inputs` → `build_candidates` → `plan_with_clusters`를 **그대로** 불러 다시 짠다.
    측정 코드와 그림 코드가 같은 함수를 써야 그림이 표와 같은 계획을 보인다.

    이동시간 식(게이트 A)과는 무관하다 — 군집 수는 대여소당 이동 계수, 배분은 고정비가
    상수로 빠지는 목적함수, 방문 순서는 거리로 정해진다(3.8절). 소요시간만 달라진다.
    """
    if duration in _CANON:
        if _CANON[duration] is None:
            print(f"  건너뜀 - {_CANON_WHY.get(duration, '정본 회차를 계산하지 못했습니다.')}")
        return _CANON[duration]
    _CANON[duration] = None
    try:
        bc = _load_experiment("baseline_compare", "experiments/baseline/baseline_compare.py")
        step1 = bc.load_step1()
        # 순수요를 읽는 요일(아래 "weekday")과 ILP·VRP·군집 모듈의 요일을 맞춘다(1.26.281) — 안 맞추면 모듈은
        # 그림을 그리는 날의 달력을 따른다. 지금은 휴일 이동 계수가 평일로 폴백해 어느 날 그려도 같다.
        bc.align_day_type("weekday", bc.ilp_mod, bc.vrp_mod, bc.kpi_mod, step1)
        solver = bc.ilp_mod.build_solver()
        net, info, warm = bc.quiet(bc.load_inputs, CANON_PERIOD, CANON_LABEL, "weekday",
                                   bc.DEFAULT_WARMUP_DAYS, "")
    except SystemExit as exc:                      # 순수요·스냅샷이 없는 DB
        _CANON_WHY[duration] = f"정본 회차를 다시 계산할 자료가 없습니다: {str(exc.code).splitlines()[0]}"
        print(f"  건너뜀 - {_CANON_WHY[duration]}")
        return None
    except Exception as exc:                       # pragma: no cover - 환경 의존
        _CANON_WHY[duration] = f"모듈을 못 불러왔습니다: {exc}"
        print(f"  건너뜀 - {_CANON_WHY[duration]}")
        return None

    # 전체 대여소의 재배치량 — build_candidates가 안에서 부르는 두 함수를 그대로 부른다
    stats, _daily, _ratio = bc.quiet(
        bc.target_mod.build_stats, net, info[["station_id", "parking_lot", "stock"]],
        duration, warmup_net=None, warmup_days=bc.DEFAULT_WARMUP_DAYS, verbose=False)
    rebal = bc.quiet(bc.target_mod.compute_rebal_qty, stats, z=None)
    base = bc.build_candidates(net, info, duration, None, warm, bc.DEFAULT_WARMUP_DAYS, step1)
    if base.empty:
        _CANON_WHY[duration] = "이 회차에는 작업 대상이 없습니다."
        print(f"  건너뜀 - {_CANON_WHY[duration]}")
        return None
    initial = bc.quiet(step1.make_clustering, base.copy(), random_state=CANON_SEED).copy()
    adjusted, routes = bc.plan_with_clusters(base.copy(), step1, solver, adjust=True,
                                             seed=CANON_SEED)

    # 군집 하나를 고른다 — **방문 수가 가운데**(짝수면 위쪽)인 군집들 가운데 같은 대여소를
    # 두 번 들른 곳이 있는 것, 없으면 번호가 작은 것. 처음에는 '대여소 수가 가운데'로 골랐는데
    # 방문 5회에 차고지 왕복만 도드라진 군집이 뽑혀 적재량 변화가 안 보였다. 소요시간으로
    # 고르지 않는 이유: 이동시간 식(게이트 A)이 바뀌면 고른 군집이 바뀐다. 방문 순서는 거리로
    # 정해지므로 이 규칙은 식과 무관하다.
    stops = routes[routes["action"] != "return"]
    visits = stops.groupby("cluster").size()
    revisit = stops.groupby("cluster")["to_id"].apply(lambda s: s.duplicated().any())
    middle = sorted(visits)[len(visits) // 2]
    tied = sorted(visits[visits == middle].index)
    chosen = next((c for c in tied if revisit[c]), tied[0])
    sizes = adjusted["cluster"].value_counts()
    frame = adjusted[adjusted["cluster"] == chosen].copy()
    frame["drop_qty"] = frame["rebal_qty"].clip(lower=0).astype(int)      # plan_with_clusters와 같다
    frame["pick_qty"] = (-frame["rebal_qty"].clip(upper=0)).astype(int)
    moves = pd.DataFrame(bc.quiet(bc.ilp_mod.solve_cluster_moves, frame, solver))

    coords = info.set_index("station_id")[["lat", "lon"]]
    rebal = rebal.merge(info[["station_id", "lat", "lon"]], on="station_id", how="inner")
    _CANON[duration] = dict(bc=bc, net=net, info=info, rebal=rebal, base=base,
                            initial=initial, adjusted=adjusted, routes=routes,
                            chosen=chosen, moves=moves, coords=coords, duration=duration)
    print(f"    정본 회차 {duration}: 작업 대상 {len(base)}곳 · 군집 {adjusted['cluster'].nunique()}개"
          f" · 예시 군집 {chosen}(대여소 {sizes[chosen]}곳 · 방문 {visits[chosen]}회)")
    return _CANON[duration]


def _map_axes(ax, lat_mean: float, ticks: bool = False):
    """지도 축 — 위도에서 경도 1도가 짧아지므로 가로세로 비를 맞춘다."""
    ax.set_aspect(1 / np.cos(np.deg2rad(lat_mean)))
    ax.grid(False)
    if not ticks:
        ax.set_xticks([])
        ax.set_yticks([])


def _free_offset(placed: list, x: float, y: float, rx: float, ry: float, offsets: list) -> tuple:
    """가까이 이미 붙인 번호가 쓰지 않은 자리를 고른다 — 붙어 있는 두 대여소의 번호가 겹쳤다."""
    used = {k for px, py, k in placed if abs(x - px) < rx and abs(y - py) < ry}
    k = next((i for i in range(len(offsets)) if i not in used), len(placed) % len(offsets))
    placed.append((x, y, k))
    return offsets[k]


def _cluster_colors(clusters) -> dict:
    cmap = plt.get_cmap("tab20")
    return {c: cmap(i % 20) for i, c in enumerate(sorted(clusters))}


# ────────────────────────────────────────────────────────────── 3-1
def fig_3_1():
    """3단계 분해를 지도 네 칸으로 — 재배치량 → 작업 대상 → 군집 → 배분과 순서 (3.1)."""
    run = _canon_round()
    if run is None:
        return
    from project_config import DEPOT_LAT, DEPOT_LON
    rebal, base, adjusted = run["rebal"], run["base"], run["adjusted"]
    lat_mean = rebal["lat"].mean()
    fig, axes = plt.subplots(2, 2, figsize=(10.6, 10.4))
    axes = axes.ravel()

    # ① 전체 대여소의 재배치량
    ax = axes[0]
    zero = rebal["rebal_qty"] == 0
    ax.scatter(rebal.loc[zero, "lon"], rebal.loc[zero, "lat"], s=3, color="#dddddd")
    sc = ax.scatter(rebal.loc[~zero, "lon"], rebal.loc[~zero, "lat"], s=7,
                    c=rebal.loc[~zero, "rebal_qty"], cmap="RdBu_r", vmin=-9, vmax=9)
    cbar = fig.colorbar(sc, ax=ax, shrink=0.7, pad=0.01)
    cbar.set_label("재배치량 $r_i$ (음수 수거 · 양수 배송)", fontsize=8)
    ax.set_title(f"① 통계가 있는 대여소 {len(rebal):,}곳의 재배치량", fontsize=10)

    # ② 작업 대상
    ax = axes[1]
    ax.scatter(rebal["lon"], rebal["lat"], s=2, color="#e6e6e6")
    pick, drop = base[base["rebal_qty"] < 0], base[base["rebal_qty"] > 0]
    ax.scatter(pick["lon"], pick["lat"], marker="^", s=26, color="#2166ac", label=f"수거 {len(pick)}곳")
    ax.scatter(drop["lon"], drop["lat"], marker="v", s=26, color="#b2182b", label=f"배송 {len(drop)}곳")
    ax.legend(fontsize=8, loc="upper right")
    ax.set_title(f"② 작업 대상 {len(base)}곳 ($|r_i|>\\theta$, 상한 $N$)", fontsize=10)

    # ③ 군집
    ax = axes[2]
    colors = _cluster_colors(adjusted["cluster"].unique())
    ax.scatter(rebal["lon"], rebal["lat"], s=2, color="#e6e6e6")
    for c, part in adjusted.groupby("cluster"):
        for sign, marker in ((-1, "^"), (1, "v")):
            p = part[np.sign(part["rebal_qty"]) == sign]
            ax.scatter(p["lon"], p["lat"], marker=marker, s=26, color=colors[c])
    ax.scatter([DEPOT_LON], [DEPOT_LAT], marker="*", s=220, color="black", zorder=5)
    k0 = run["initial"]["cluster"].nunique()
    ax.set_title(f"③ 군집 {len(colors)}개" + (f"(식 (3.7)의 K={k0}, 조정이 {k0 - len(colors)}개를 비움)"
                                              if k0 != len(colors) else "")
                 + "\n▲ 수거 · ▼ 배송 · ★ 차고지", fontsize=10)

    # ④ 군집 하나 — 물량 배분 화살표와 방문 순서
    _draw_cluster_detail(axes[3], run, colors[run["chosen"]])
    axes[3].set_title(f"④ 군집 하나(대여소 {int((adjusted['cluster'] == run['chosen']).sum())}곳)의"
                      " 물량 배분과 방문 순서", fontsize=10)

    for ax, text in zip(axes, ["식 (3.2)~(3.5)", "식 (3.6)", "식 (3.7)~(3.8)", "식 (3.9)~(3.10)"]):
        _map_axes(ax, lat_mean)
        ax.text(0.5, -0.04, text, transform=ax.transAxes, ha="center", va="top", fontsize=9,
                color="#444")
    # ①~③은 같은 범위로 — 칸을 넘어가며 같은 도시를 보게
    for ax in axes[1:3]:
        ax.set_xlim(axes[0].get_xlim())
        ax.set_ylim(axes[0].get_ylim())
    fig.tight_layout()
    save(fig, "그림3-1_3단계_분해", "재배치 문제의 3단계 분해 (3.1)")


def _draw_cluster_detail(ax, run: dict, color) -> None:
    """군집 하나를 확대 — ILP 배분(회색 화살표, 굵기 = 대수)과 그리디 방문 순서(번호)."""
    coords, moves, chosen = run["coords"], run["moves"], run["chosen"]
    part = run["adjusted"][run["adjusted"]["cluster"] == chosen]
    route = run["routes"][run["routes"]["cluster"] == chosen]
    for m in moves.itertuples():
        a, b = coords.loc[m.pick_station_id], coords.loc[m.drop_station_id]
        ax.annotate("", xy=(b["lon"], b["lat"]), xytext=(a["lon"], a["lat"]),
                    arrowprops=dict(arrowstyle="-|>", color="#9e9e9e", lw=0.6 + 0.35 * m.qty,
                                    alpha=0.8, shrinkA=4, shrinkB=4,
                                    connectionstyle="arc3,rad=0.15"))
    for sign, marker, col in ((-1, "^", "#2166ac"), (1, "v", "#b2182b")):
        p = part[np.sign(part["rebal_qty"]) == sign]
        ax.scatter(p["lon"], p["lat"], marker=marker, s=70, color=col, edgecolor=color, lw=1.2,
                   zorder=3)
    stops = route[route["action"] != "return"].reset_index(drop=True)
    labels: dict = {}
    for i, row in stops.iterrows():
        labels.setdefault(row["to_id"], []).append(str(i + 1))
    pad_lon = (part["lon"].max() - part["lon"].min()) * 0.2 + 0.002
    pad_lat = (part["lat"].max() - part["lat"].min()) * 0.25 + 0.002
    placed: list = []
    for sid, nums in labels.items():
        c = coords.loc[sid]
        offset = _free_offset(placed, c["lon"], c["lat"], pad_lon, pad_lat,
                              [(5, 5), (-32, 4), (6, -18), (-32, -18)])
        ax.annotate(",".join(nums), (c["lon"], c["lat"]), xytext=offset,
                    textcoords="offset points", fontsize=8, fontweight="bold",
                    color="#b2182b" if len(nums) > 1 else "black",
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85))
    ax.plot([], [], color="#9e9e9e", lw=1.5, label="물량 배분 (굵기 = 대수)")
    ax.scatter([], [], marker="^", color="#2166ac", label="수거")
    ax.scatter([], [], marker="v", color="#b2182b", label="배송")
    ax.legend(fontsize=7.5, loc="upper left")
    ax.set_xlim(part["lon"].min() - pad_lon, part["lon"].max() + pad_lon)
    ax.set_ylim(part["lat"].min() - pad_lat, part["lat"].max() + pad_lat)


# ────────────────────────────────────────────────────────────── 3-3
def fig_3_3():
    """수급 균형 조정 전후의 군집 — 같은 대여소, 같은 씨앗, 조정만 다르다 (3.5.2).

    범례에 군집 17개의 수급 합을 늘어놓으면 지도를 가리므로 **아래 칸에 막대로** 그린다.
    """
    run = _canon_round()
    if run is None:
        return
    from project_config import ADJUST_BALANCE_OK
    initial, adjusted, rebal = run["initial"], run["adjusted"], run["rebal"]
    # 조정이 군집을 **비울** 수 있다(초기 1~2곳짜리 수거 군집이 이웃에 흡수된다) — 색은 둘의 합집합
    colors = _cluster_colors(set(initial["cluster"]) | set(adjusted["cluster"]))
    emptied = sorted(set(initial["cluster"]) - set(adjusted["cluster"]))
    moved = int((initial.set_index("station_id")["cluster"]
                 != adjusted.set_index("station_id")["cluster"]).sum())
    fig, axes = plt.subplots(2, 2, figsize=(10.8, 9.6), gridspec_kw={"height_ratios": [2.3, 1]})
    sums = {}
    for col, (name, frame) in enumerate((("K-Medoids 초기 군집", initial),
                                         ("식 (3.8)의 조정 뒤", adjusted))):
        ax = axes[0, col]
        ax.scatter(rebal["lon"], rebal["lat"], s=2, color="#e6e6e6")
        for c, part in frame.groupby("cluster"):
            for sign, marker in ((-1, "^"), (1, "v")):
                p = part[np.sign(part["rebal_qty"]) == sign]
                ax.scatter(p["lon"], p["lat"], marker=marker, s=28, color=colors[c])
        balance = frame.groupby("cluster")["rebal_qty"].sum().reindex(sorted(colors), fill_value=0)
        sums[name] = balance
        ax.set_title(f"{name} — $|\\sum r_i|$ 최대 {int(balance.abs().max())} · "
                     f"합 {int(balance.abs().sum())}", fontsize=10)
        _map_axes(ax, rebal["lat"].mean())
        bx = axes[1, col]
        bx.bar(range(len(balance)), balance.to_numpy(), color=[colors[c] for c in balance.index],
               edgecolor="black", lw=0.4)
        bx.axhspan(-ADJUST_BALANCE_OK, ADJUST_BALANCE_OK, color="#bdbdbd", alpha=0.4,
                   label=f"멈춤 기준 $\\pm${ADJUST_BALANCE_OK}")
        bx.axhline(0, color="black", lw=0.6)
        bx.set_xticks(range(len(balance)))
        bx.set_xticklabels([str(int(c) + 1) for c in balance.index], fontsize=7.5)
        for i, c in enumerate(balance.index):
            if c not in set(frame["cluster"]):
                bx.text(i, 0.5, "빔", ha="center", va="bottom", fontsize=7.5, color="#b03a2e")
        bx.set_xlabel("군집")
        bx.legend(fontsize=7.5, loc="lower right")
    lim = max(s.abs().max() for s in sums.values()) * 1.1
    for bx in axes[1]:
        bx.set_ylim(-lim, lim)
    axes[1, 0].set_ylabel("군집의 수급 합 (대)")
    for ax in axes[0]:
        ax.set_xlim(axes[0, 0].get_xlim())
        ax.set_ylim(axes[0, 0].get_ylim())
    fig.suptitle(f"작업 대상 {len(adjusted)}곳 · 초기 군집 {initial['cluster'].nunique()}개 → 조정 뒤 "
                 f"{adjusted['cluster'].nunique()}개 · 옮긴 대여소 {moved}곳 (▲ 수거 · ▼ 배송)",
                 fontsize=10)
    fig.tight_layout()
    print(f"    조정 전 |합| 최대 {int(sums['K-Medoids 초기 군집'].abs().max())} → 뒤 "
          f"{int(sums['식 (3.8)의 조정 뒤'].abs().max())} · 옮긴 대여소 {moved}곳 · 비운 군집 "
          f"{[int(c) + 1 for c in emptied]}")
    save(fig, "그림3-3_군집_조정_전후", "수급 균형 조정 전후의 군집 (3.5.2)")


# ────────────────────────────────────────────────────────────── 3-4
def fig_3_4():
    """그리디가 만든 한 군집의 방문 순서와 적재량 — 구간표를 옆에 붙인다 (3.7.1).

    ⚠️ **소요시간은 지금 켜진 이동시간 식으로 계산된다**(`greedy_route` → `travel_seconds`).
    게이트 A 전에는 직선거리 ÷ 25km/h, 켠 뒤에는 고정비 + 거리/속도다. 그래서 그림 안에
    어느 식인지 적고, 원고의 이 그림 문단에 게이트 A 표시를 단다 — 순서와 적재량은 그대로고
    표의 시간 열만 바뀐다.
    """
    run = _canon_round()
    if run is None:
        return
    import project_config as pc
    coords, chosen = run["coords"], run["chosen"]
    route = run["routes"][run["routes"]["cluster"] == chosen].reset_index(drop=True)
    part = run["adjusted"][run["adjusted"]["cluster"] == chosen]

    fig, (ax, tx) = plt.subplots(1, 2, figsize=(11.4, 5.4), gridspec_kw={"width_ratios": [1, 1.05]})
    load, loads = 0, []
    for row in route.itertuples():
        load += row.qty if row.action == "pick" else -row.qty if row.action == "drop" else 0
        loads.append(load)
    route["load"] = loads
    xs = [route.loc[0, "from_lon"], *route["to_lon"]]
    ys = [route.loc[0, "from_lat"], *route["to_lat"]]
    ax.plot(xs, ys, color="#636363", lw=1.0, zorder=1)
    for sign, marker, col in ((-1, "^", "#2166ac"), (1, "v", "#b2182b")):
        p = part[np.sign(part["rebal_qty"]) == sign]
        ax.scatter(p["lon"], p["lat"], marker=marker, s=90, color=col, zorder=3)
    stops = route[route["action"] != "return"].reset_index(drop=True)
    labels: dict = {}
    for i, row in stops.iterrows():
        labels.setdefault(row["to_id"], []).append(str(i + 1))
    # 차고지는 군집에서 멀어(이 예에서 10km 안팎) 함께 넣으면 군집이 점 하나로 뭉친다 —
    # 지도를 군집 범위로 자르고 차고지 왕복선은 칸 밖으로 나가게 둔다.
    span_lon = part["lon"].max() - part["lon"].min()
    span_lat = part["lat"].max() - part["lat"].min()
    pad = max(span_lon, span_lat) * 0.25 + 0.002
    ax.set_xlim(part["lon"].min() - pad, part["lon"].max() + pad)
    ax.set_ylim(part["lat"].min() - pad, part["lat"].max() + pad)
    placed: list = []
    for sid, nums in labels.items():
        c = coords.loc[sid]
        offset = _free_offset(placed, c["lon"], c["lat"], pad * 0.6, pad * 0.6,
                              [(7, 7), (-36, 4), (8, -20), (-36, -20)])
        ax.annotate(",".join(nums), (c["lon"], c["lat"]), xytext=offset, textcoords="offset points",
                    fontsize=9, fontweight="bold", color="#b2182b" if len(nums) > 1 else "black",
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.9), zorder=5)
    for i, row in route.iterrows():                 # 구간 가운데에 그 구간의 적재량
        before = route.loc[i - 1, "load"] if i else 0
        if row["action"] == "return" or i == 0:
            continue                                # 차고지 구간은 칸 밖이라 표에만 싣는다
        mx, my = (row["from_lon"] + row["to_lon"]) / 2, (row["from_lat"] + row["to_lat"]) / 2
        ax.text(mx, my, f"{int(before)}", fontsize=7.5, color="#6a51a3", ha="center", va="center",
                bbox=dict(boxstyle="round,pad=0.12", fc="#f2f0f7", ec="#6a51a3", lw=0.5), zorder=4)
    ax.scatter([], [], marker="^", color="#2166ac", label="수거")
    ax.scatter([], [], marker="v", color="#b2182b", label="배송")
    ax.plot([], [], color="#6a51a3", lw=0, marker="s", mfc="#f2f0f7", label="구간 위 수 = 그 구간의 적재량")
    ax.plot([], [], color="#636363", lw=1.0, label="칸 밖으로 나가는 선 = 차고지 왕복")
    ax.legend(fontsize=7.5, loc="best")
    _map_axes(ax, part["lat"].mean())
    revisit = [sid for sid, nums in labels.items() if len(nums) > 1]
    ax.set_title(f"방문 {len(stops)}회 · 대여소 {len(labels)}곳"
                 + (f" · 두 번 들른 곳 {len(revisit)}곳(빨간 번호)" if revisit else ""), fontsize=10)

    names = {"pick": "수거", "drop": "배송", "return": "복귀"}
    cells = [[str(i + 1) if r.action != "return" else "—", names[r.action],
              str(int(r.qty)) if r.qty else "", str(int(r.load)), f"{r.distance_km:.2f}",
              f"{r.travel_sec / 60:.1f}", f"{r.work_sec / 60:.1f}", f"{r.cum_sec / 60:.1f}"]
             for i, r in enumerate(route.itertuples())]
    tx.axis("off")
    table = tx.table(cellText=cells, colLabels=["순서", "작업", "대수", "적재", "거리(km)",
                                                "이동(분)", "작업(분)", "누적(분)"],
                     loc="center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1, 1.25)
    if pc.USE_ROAD_MODEL:
        model = (f"고정비 {pc.ROAD_FIXED_SEC_WEEKDAY:g}초 + 직선거리 ÷ {pc.ROAD_SPEED_KMPH_WEEKDAY:g}km/h")
    else:
        model = f"직선거리 ÷ {pc.VEHICLE_SPEED_KMPH:g}km/h (상수 속도)"
    tx.set_title(f"이동시간 식: {model}\n작업시간: 한 대에 {pc.PICK_TIME_SEC:g}초(수거)·"
                 f"{pc.DROP_TIME_SEC:g}초(배송)", fontsize=9)
    print(f"    군집 {chosen}: 방문 {len(stops)}회 · 총 {pc.format_seconds(route['cum_sec'].iloc[-1])} · "
          f"이동시간 식 = {model}")
    save(fig, "그림3-4_방문순서_적재량", "그리디가 만든 한 군집의 방문 순서와 적재량 (3.7.1)")


# ────────────────────────────────────────────────────────────── 3-6
def fig_3_6():
    """재고 궤적의 복원과 결품 시간 — 식 (3.13)을 한 대여소·하루로 풀어 본다 (3.9).

    대여소 고르는 규칙(원고 캡션에 같은 말을 적는다): 이 회차에 **실제로 자전거를 받고
    집행 뒤 재고가 거치대 수 안에 드는** 배송 대여소 가운데 재배치 전 **평일 평균 결품
    시간이 가장 긴 곳**, 그리고 그 대여소의
    평일 가운데 재배치 전 결품이 **처음 난 날**. 결품을 가장 많이 줄인 날을 고르면 효과를
    부풀리므로 그렇게 고르지 않는다. 옆 칸에 그 대여소의 평일 전부를 함께 그린다.

    궤적은 여기서 한 줄로 다시 쓰지만(`clip(q − n, 0, c)`), 결품·포화 시간은 운영 함수
    `_simulate_stock()`이 같은 행에 낸 값과 **맞대어 어긋나면 그리지 않는다.**
    """
    run = _canon_round()
    if run is None:
        return
    from pipeline.step4_metrics import imbalance as kpi_mod
    from project_config import duration_hours
    duration, net, base = run["duration"], run["net"], run["base"]
    hours = duration_hours(duration)
    delta = kpi_mod.executed_delta(run["routes"])
    stations = base[["station_id", "station_name", "stock", "parking_lot"]].copy()
    stations["delta"] = stations["station_id"].map(delta).fillna(0)
    merged = net.merge(stations, on="station_id", how="inner").reset_index(drop=True)
    before = kpi_mod._simulate_stock(merged, merged["stock"], merged["parking_lot"], hours)
    after = kpi_mod._simulate_stock(merged, merged["stock"] + merged["delta"],
                                    merged["parking_lot"], hours)
    merged["so_before"], merged["so_after"] = before["stockout"], after["stockout"]
    merged["sat_before"], merged["sat_after"] = before["saturated"], after["saturated"]
    # 집행 뒤 재고가 거치대 수를 넘는 곳은 예시에서 뺀다. 목표 재고의 상한이 1.5c라(식 3.4)
    # 모형으로는 정상이지만, 궤적이 거치대 선 위에서 시작해 첫 시각에 잘려 그림이 읽히지 않는다
    # (처음 뽑힌 곳이 거치대 5개에 +6대였다, 2026-09-18).
    received = merged[(merged["delta"] > 0)
                      & (merged["stock"] + merged["delta"] <= merged["parking_lot"])]
    if received.empty:
        print("  건너뜀 - 이 회차에 자전거를 받은(거치대 안에서) 대여소가 없습니다.")
        return
    mean_so = received.groupby("station_id")["so_before"].mean()
    sid = mean_so.idxmax()
    days = merged[merged["station_id"] == sid].sort_values("날짜")
    first = days[days["so_before"] > 0].iloc[0]

    def trajectory(q0):
        q, out = float(q0), [float(q0)]
        for h in hours:
            q = min(max(q - float(first[f"net_{h:02d}"]), 0.0), float(first["parking_lot"]))
            out.append(q)
        return out

    cap = float(first["parking_lot"])
    traj = {"재배치 전": trajectory(first["stock"]),
            "집행 뒤": trajectory(first["stock"] + first["delta"])}
    for name, key in (("재배치 전", "before"), ("집행 뒤", "after")):
        so = sum(q <= 0 for q in traj[name][1:])
        sat = sum(q >= cap for q in traj[name][1:])
        if (so, sat) != (first[f"so_{key}"], first[f"sat_{key}"]):
            raise RuntimeError(f"궤적이 운영 함수와 다릅니다({name}: 결품 {so} 대 {first[f'so_{key}']}, "
                               f"포화 {sat} 대 {first[f'sat_{key}']}) - 식을 다시 확인하십시오.")

    fig, (ax, bx) = plt.subplots(1, 2, figsize=(11.2, 4.2), gridspec_kw={"width_ratios": [1.25, 1]})
    xs = [hours[0] + i for i in range(len(hours) + 1)]
    for name, col, ls in (("재배치 전", "#969696", "--"), ("집행 뒤", "#2166ac", "-")):
        ax.step(xs, traj[name], where="post", color=col, ls=ls, lw=2, label=name)
        ax.plot(xs, traj[name], "o", color=col, ms=4)
    # 음영은 재배치 전 결품 하나만 — 둘을 겹쳐 칠하면 색이 섞여 읽히지 않았다.
    # 집행 뒤의 결품·포화는 해당 시각 끝점에 표식으로 찍는다.
    for i in range(1, len(xs)):
        if traj["재배치 전"][i] <= 0:
            ax.axvspan(xs[i] - 1, xs[i], color="#fdd49e", alpha=0.6, lw=0)
    ax.axhline(cap, color="black", lw=0.8, ls=":")
    ax.text(xs[0] + 0.05, min(cap, max(max(traj["재배치 전"]), max(traj["집행 뒤"])) + 1.5),
            f"거치대 수 $c_i$={int(cap)}" + ("" if cap <= max(traj["집행 뒤"]) + 3 else " (칸 위쪽 밖)"),
            va="bottom", fontsize=8)
    ax.fill_between([], [], color="#fdd49e", label="재배치 전 결품 시각")
    after_so = [xs[i] for i in range(1, len(xs)) if traj["집행 뒤"][i] <= 0]
    sat = [(xs[i], traj[n][i]) for n in traj for i in range(1, len(xs)) if traj[n][i] >= cap]
    if after_so:
        ax.plot(after_so, [0] * len(after_so), "x", color="#b2182b", ms=9, mew=2,
                label="집행 뒤 결품")
    if sat:
        ax.plot(*zip(*sat), "^", color="#6a51a3", ms=8, label="포화 (거치대가 참)")
    ax.set_xticks(xs)
    ax.set_xticklabels([f"{h:02d}시" for h in xs])
    # 세로축은 궤적이 닿는 범위까지만 — 거치대 20개에 재고 0~2대면 선이 바닥에 붙는다.
    # 거치대 선이 그 위에 있으면 칸 안에 수만 적는다.
    top = max(max(traj["재배치 전"]), max(traj["집행 뒤"]))
    ax.set_ylim(-0.5, max(top + 2, 4) if cap > top + 3 else cap + 1.5)
    ax.set_ylabel("재고 (대)")
    ax.legend(fontsize=8, loc="center right")
    day = pd.Timestamp(str(first["날짜"])[:10])
    ax.set_title(f"{day.year}년 {day.month}월 {day.day}일 · 초기 재고 {int(first['stock'])}대 → 집행 "
                 f"+{int(first['delta'])}대 · 결품 {int(first['so_before'])}시간 → "
                 f"{int(first['so_after'])}시간", fontsize=9.5)

    xi = np.arange(len(days))
    bx.bar(xi - 0.2, days["so_before"], 0.4, color="#bdbdbd", label="재배치 전")
    bx.bar(xi + 0.2, days["so_after"], 0.4, color="#2166ac", label="집행 뒤")
    bx.set_xticks(xi)
    bx.set_xticklabels([str(d)[8:10] for d in days["날짜"]], fontsize=7)
    bx.set_xlabel(f"20{CANON_PERIOD[:2]}년 {int(CANON_PERIOD[4:6])}월 평일 (일)")
    bx.set_ylabel("결품 시간 (시간)")
    bx.set_ylim(0, len(hours) + 0.5)
    bx.legend(fontsize=8)
    bx.set_title(f"같은 대여소의 평일 {len(days)}일 — 평균 {days['so_before'].mean():.2f} → "
                 f"{days['so_after'].mean():.2f}시간", fontsize=9.5)
    print(f"    대여소 {sid}: {str(first['날짜'])[:10]} 결품 {int(first['so_before'])}→"
          f"{int(first['so_after'])} · 평일 평균 {days['so_before'].mean():.2f}→"
          f"{days['so_after'].mean():.2f} · 운영 함수와 일치")
    save(fig, "그림3-6_재고궤적_결품", "재고 궤적의 복원과 결품 시간 (3.9)")


# ────────────────────────────────────────────────────────────── 3-5
def fig_3_5():
    """직선거리와 도로 이동시간 — 고정비 식과 상수 속도 가정 (3.8).

    표본 읽기와 적합은 `road_time_model.py`의 함수를 **그대로** 부른다 — 여기서 다시
    구현하면 3.8절의 계수와 어긋난다. 표본은 게이트 A 판정일(`ROAD_FIT_TO`)까지의 평일
    고정 패널 전부다. 채택 계수(320.4초 · 32.11km/h)를 낸 것과 같은 표본이며, 08-31과
    09-01의 패널 일치 구간도 들어간다(채택 조건의 '10일'은 09-02부터 센 일수다). 판정일
    뒤에 쌓인 날을 넣으면 계수가 원고와 달라진다. 도로 패널은 집 PC DB에만 있다.
    """
    import importlib.util
    try:
        spec = importlib.util.spec_from_file_location(
            "road_time_model", ROOT / "experiments/params/road_time_model.py")
        rtm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rtm)
        legs = rtm.load_legs(False, panel_only=True, day_type="weekday")
    except Exception as exc:                       # pragma: no cover - 환경 의존
        print(f"  건너뜀 - 모듈을 못 불러왔거나 도로 패널을 못 읽었습니다: {exc}")
        return
    legs = legs[legs["날짜"] <= ROAD_FIT_TO]
    if legs.empty:
        print(f"  건너뜀 - {ROAD_FIT_TO}까지의 평일 도로 패널이 DB에 없습니다 (도로 수집은 집 PC).")
        return
    km, sec = legs["straight_km"].to_numpy(), legs["road_sec"].to_numpy()
    fixed, speed = rtm.fit_linear(km, sec)
    const = rtm.VEHICLE_SPEED_KMPH
    bins = legs.assign(구간=pd.cut(legs["straight_km"], rtm.EDGES, labels=rtm.LABELS),
                       상수=km * 3600.0 / const, 식=rtm.predict(km, fixed, speed))
    bins = bins.groupby("구간", observed=False).agg(
        km=("straight_km", "mean"), 실측=("road_sec", "mean"), 상수=("상수", "mean"), 식=("식", "mean"))

    def signed(v):
        return f"{v:+.0f}"            # 맑은 고딕에는 U+2212가 없다 — 하이픈 그대로 (setup()과 같은 까닭)

    box = dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85)
    fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.4), gridspec_kw={"width_ratios": [1.25, 1]})
    for ax, xmax in zip(axes, (km.max() * 1.03, 2.1)):
        near = km <= xmax
        ax.scatter(km[near], sec[near], s=5, color="#9ecae1", alpha=0.35, lw=0,
                   label=f"실측 {len(km):,}구간" if ax is axes[0] else None)
        x = np.linspace(0, xmax, 200)
        ax.plot(x, rtm.predict(x, fixed, speed), color="#08519c", lw=2.0,
                label=f"식 (3.12)  F={fixed:.1f}초, v={speed:.2f}km/h")
        ax.plot(x, x * 3600.0 / const, color="#b03a2e", lw=1.8, ls="--",
                label=f"상수 속도  F=0, v={const:g}km/h")
        ax.set_xlim(0, xmax)
        ax.set_xlabel("직선거리 (km)")
    axes[0].set_ylabel("경로 안내 API 이동시간 (초)")
    axes[0].set_ylim(0, sec.max() * 1.04)
    axes[0].set_title("전체 거리", fontsize=10)
    axes[0].legend(fontsize=8, loc="upper left")

    ax = axes[1]
    ax.set_ylim(0, 900)
    shown = ["0~0.5", "0.5~1", "1~2"]
    ax.scatter(bins.loc[shown, "km"], bins.loc[shown, "실측"], marker="D", s=36, color="black",
               zorder=7, label="거리 구간별 실측 평균")          # 글상자(6)가 점을 가리지 않게
    for lab in ("0~0.5", "1~2"):
        row = bins.loc[lab]
        ax.annotate("", xy=(row["km"], row["실측"]), xytext=(row["km"], row["상수"]),
                    arrowprops=dict(arrowstyle="<->", color="#b03a2e", lw=1.2))
        ax.annotate("", xy=(row["km"], row["실측"]), xytext=(row["km"], row["식"]),
                    arrowprops=dict(arrowstyle="<->", color="#08519c", lw=1.2))
        ax.text(row["km"] + 0.05, (row["실측"] + row["상수"]) / 2,
                f"상수 속도 {signed(row['상수'] - row['실측'])}초", color="#b03a2e", fontsize=8.5,
                va="center", bbox=box, zorder=6)
        # 0.5km 이하는 글상자가 옆 구간(0.5~1km)의 점에 닿는다 — 선 위로 올린다
        y = row["식"] + 40 if lab == "0~0.5" else (row["실측"] + row["식"]) / 2
        ax.text(row["km"] + 0.05, y, f"식 (3.12) {signed(row['식'] - row['실측'])}초", color="#08519c",
                fontsize=8.5, va="center", bbox=box, zorder=6)
    ax.set_title("2km 이하 확대 — 구간 평균과 두 식의 차이", fontsize=10)
    ax.legend(fontsize=8, loc="upper left")
    days = sorted(legs["날짜"].unique())
    print(f"    {days[0]}~{days[-1]} {len(days)}일 · {len(km):,}구간 · F={fixed:.1f}초 v={speed:.2f}km/h · "
          + " · ".join(f"{lab} 상수 {bins.loc[lab, '상수'] - bins.loc[lab, '실측']:+.0f}초 식 "
                       f"{bins.loc[lab, '식'] - bins.loc[lab, '실측']:+.0f}초" for lab in rtm.LABELS))
    save(fig, "그림3-5_이동시간_모형", "직선거리와 도로 이동시간 — 고정비 식과 상수 속도 (3.8)")


# 2026-10-14 원고 분량 줄이기에서 그림 다섯(옛 4-5 결원 · 5-2 |mu| 분포 · 6-3 이동거리 · 8-1 공식 통계 ·
# 8-2 거점)을 원고에서 뺐다. 그리는 함수도 함께 지웠고(git 이력에 있다), 옛 5-3은 5-2로 당겼다.
FIGURES = {"6-1": fig_6_1, "6-2": fig_6_2,
           "5-1": fig_5_1, "1-1": fig_1_1, "4-1": fig_4_1,
           "3-2": fig_3_2, "4-3": fig_4_3, "5-2": fig_5_2,
           "4-2": fig_4_2, "2-1": fig_2_1,
           "3-1": fig_3_1, "3-3": fig_3_3, "3-4": fig_3_4,
           "3-6": fig_3_6, "3-5": fig_3_5}


def main() -> int:
    parser = argparse.ArgumentParser(description="논문 그림 생성")
    parser.add_argument("--only", help="쉼표로 구분한 그림 번호 (예: 6-1,6-2)")
    args, _ = parser.parse_known_args()

    wanted = [k.strip() for k in args.only.split(",")] if args.only else list(FIGURES)
    unknown = [k for k in wanted if k not in FIGURES]
    if unknown:
        print(f"모르는 그림: {', '.join(unknown)} (있는 것: {', '.join(FIGURES)})")
        return 1

    setup()
    print(f"그림을 만듭니다 → {OUT.relative_to(ROOT)}\n")
    for key in wanted:
        FIGURES[key]()
    print("\n[주의] 그림의 수치를 본문 표에 옮겨 적지 마십시오 - 한쪽이 낡습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
