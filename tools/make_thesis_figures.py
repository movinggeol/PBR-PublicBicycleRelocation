"""논문 그림을 만든다 — 표만 131개고 그림이 0장이었다 (1.26.135).

**왜 필요한가.** 2026-09-07 논문 점검에서 본문의 표가 131개(6장만 69개)인데
**그림이 하나도 없다**는 것을 발견했다. 표는 값을 정확히 전하지만 *분포*와
*맞교환*을 못 보여 준다 — 6.5의 반복 실험은 `평균 ± 표준편차` 한 줄로
줄었고, 6.3의 "결품을 줄인 대가로 포화가 는다"는 두 표를 눈으로 맞대야만
보인다.

**무엇을 그리나.** 표로는 잘 안 보이는 것만 그린다. 표를 그림으로 옮겨
그리지 않는다 — 그러면 같은 값이 두 곳에 살아 한쪽이 낡는다.

  · 그림 6-1  결품 편익과 포화 **대가를 한 축에** (표 둘을 맞대야 보였다)
  · 그림 6-2  12개월 반복의 **분포** (표는 평균±표준편차로 줄인다)
  · 그림 6-3  이동거리 대비 편익 — **P가 더 멀리 다닌다**는 사실
  · 그림 5-1  `z` 격자를 **모집단 셋으로** — 모집단이 승자를 정한다
  · 그림 1-1  평일·휴일 순수요 **부호 반전** (33~37%가 반대다)
  · 그림 4-1  파이프라인 구조

**원고 자료 자리를 채우는 그림 (2026-09-18~, 1.26.235).** 원고(docs/연구/논문/)의
`(자리 [그림 n-m] …)` 문단을 채운다. 캡션은 원고가 그림 아래에 따로 달므로 그림
안에 '그림 n-m' 제목을 넣지 않는다.

  · 그림 3-2  재배치량의 tanh 완화 — **운영 함수 `compute_rebal_qty()`로** 그린다
  · 그림 4-3  대여이력 월별 건수 (빠진 달 · 겨울 달)
  · 그림 5-2  |mu| 분포 — <표 5-4>와 **같은 대여소 집합**(학습·검증 달 모두)
  · 그림 5-3  26년 2~3월 일별 대여와 배율 산정 구간

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
import matplotlib.dates  # noqa: E402  (그림 5-3의 날짜 축)
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
    plt.rcParams["savefig.bbox"] = "tight"
    plt.rcParams["axes.grid"] = True
    plt.rcParams["grid.alpha"] = 0.3
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
    """12개월 반복의 분포. 표는 이것을 평균±표준편차 한 줄로 줄인다."""
    frame = _need(EXP / "baseline/repeat_12month_pinned.csv", "12개월 반복 결과")
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
        ax.set_title(f"{duration}  (각 n={len(data[0])})", fontsize=10)
    axes[0].set_ylabel("재배치 후 결품 시간 (h) — 낮을수록 좋음")
    fig.suptitle("그림 6-2  12개월 × 씨앗 3개 반복의 결품 시간 분포 "
                 "(평일, 스냅샷 2026-08-11 real)\n"
                 "상자는 사분위·수염은 1.5 IQR, 겹친 점이 개별 실행 36개다", fontsize=11, y=1.06)
    save(fig, "그림6-2_결품분포", "반복의 분포 (표는 평균±표준편차로 줄인다)")


# ────────────────────────────────────────────────────────────── 6-1
def fig_6_1():
    """편익과 대가를 한 축에. 표 둘을 맞대야만 보이던 것이다."""
    frame = _need(EXP / "baseline/repeat_12month_pinned.csv", "12개월 반복 결과")
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
               label="포화 증가(대가) — 두 방법 공통 표기" if i == 0 else None)
        for xi, (b, c) in zip(x + off, zip(part["편익"], part["대가"])):
            ax.text(xi, b + 0.04, f"{b:.2f}", ha="center", fontsize=8)
            ax.text(xi, -c - 0.06, f"-{c:.2f}", ha="center", va="top",
                    fontsize=8, color="#b03a2e")
    ax.axhline(0, color="black", lw=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(DURATIONS)
    ax.set_ylim(-0.72, 2.05)                     # 값 라벨과 주의 문구가 겹치지 않게
    ax.set_ylabel("무재배치(B0) 대비 변화 (h)")
    ax.set_title("그림 6-1  재배치의 편익과 대가 — 결품은 줄지만 포화는 는다\n"
                 "(12개월 × 씨앗 3개 평균, 위=편익 · 아래=대가)", fontsize=11)
    ax.legend(fontsize=8, loc="upper right", ncol=1)
    # ⚠️ 두 막대를 빼서 하나로 합치지 않는다 — 같은 '시간'이라도 무게가 다르다(6.3)
    fig.text(0.5, -0.02, "[주의] 두 값을 빼서 합치지 않는다 - 포화 1시간과 결품 1시간은 무게가 다르다",
             ha="center", fontsize=8, color="#555")
    save(fig, "그림6-1_편익과_대가", "결품 편익과 포화 대가를 한 축에")


# ────────────────────────────────────────────────────────────── 6-3
def fig_6_3():
    """P가 더 멀리 다닌다 — 논문이 감추지 않기로 한 사실이다(6.6)."""
    frame = _need(EXP / "baseline/repeat_12month_pinned.csv", "12개월 반복 결과")
    if frame is None:
        return
    base = (frame[frame["method"] == "B0"]
            .set_index(["duration", "period", "seed"])["stockout_after"])
    fig, axes = plt.subplots(1, 2, figsize=(10.4, 4.0))
    for method in ("B1", "P"):
        part = frame[frame["method"] == method].set_index(["duration", "period", "seed"])
        gain = base.loc[part.index] - part["stockout_after"]
        axes[0].scatter(part["km"], gain, s=16, alpha=0.6, marker=STYLE[method]["마커"],
                        color=STYLE[method]["색"], edgecolor="black", lw=0.4,
                        label=STYLE[method]["이름"])
    axes[0].set_xlabel("총 이동거리 (km)")
    axes[0].set_ylabel("결품 감소 (h, 무재배치 대비)")
    axes[0].set_title("멀리 다닐수록 많이 줄이나", fontsize=10)
    axes[0].legend(fontsize=8)

    rows = []
    for method in ("B1", "P"):
        part = frame[frame["method"] == method].set_index(["duration", "period", "seed"])
        gain = base.loc[part.index] - part["stockout_after"]
        for duration in DURATIONS:
            per_km = (gain / part["km"]).xs(duration, level="duration")
            rows.append(dict(duration=duration, method=method,
                             값=float(per_km.mean() * 1000), 편차=float(per_km.std() * 1000)))
    summary = pd.DataFrame(rows)
    x = np.arange(len(DURATIONS))
    for i, method in enumerate(("B1", "P")):
        part = summary[summary["method"] == method].set_index("duration").loc[DURATIONS]
        axes[1].bar(x + (i - 0.5) * 0.36, part["값"], 0.36, yerr=part["편차"], capsize=3,
                    color=STYLE[method]["색"], hatch=STYLE[method]["해치"],
                    edgecolor="black", lw=0.6, label=STYLE[method]["이름"])
    axes[1].set_xticks(x)
    axes[1].set_xticklabels(DURATIONS)
    axes[1].set_ylabel("1,000km당 결품 감소 (h)")
    axes[1].set_title("km당으로 재면 대등하다 (오차막대 = 표준편차)", fontsize=10)
    axes[1].legend(fontsize=8)
    fig.suptitle("그림 6-3  편익과 이동 비용 — 제안 방법의 우위 일부는 더 멀리 다녀서 얻은 것이다",
                 fontsize=11, y=1.02)
    save(fig, "그림6-3_편익과_이동거리", "P가 더 멀리 다닌다 (6.6)")


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
            ax.plot(curve.index, curve.to_numpy(), marker=marker, ms=4, lw=1.3, label=name)
            best = curve.idxmin()
            ax.scatter([best], [curve.loc[best]], s=90, facecolors="none",
                       edgecolors="red", lw=1.4, zorder=5)
        ax.axvline(1.99, color="#888", ls="--", lw=1)
        ax.set_title(duration, fontsize=10)
        ax.set_xlabel("z")
    axes[0].set_ylabel("재배치 후 결품 시간 (h)")
    axes[0].legend(fontsize=7.5, title="모집단", title_fontsize=7.5)
    fig.suptitle("그림 5-1  $z$ 격자를 모집단 셋으로 각각 재면 — 빨간 원이 그 모집단의 최저점\n"
                 "각 모집단은 자기를 정의한 $z$를 뽑는다. 중립 모집단(전체 대여소)은 거의 평평하다\n"
                 "(점선 = 현행 $z$=1.99, 25년 11월 평일)", fontsize=11, y=1.12)
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
        period = sorted(periods, key=lambda s: (int(s.split("년")[0]),
                                                int(s.split("년")[1].replace("월", ""))))[-1]
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
        ax.set_title(f"{duration}   부호 반대 {opposite.mean() * 100:.0f}% "
                     f"({int(opposite.sum())}/{len(joined)}곳)", fontsize=10)
        ax.set_xlabel("평일 순수요 (대/일)")
    axes[0].set_ylabel("휴일 순수요 (대/일)")
    axes[0].legend(fontsize=8)
    fig.suptitle(f"그림 1-1  같은 대여소인데 평일과 휴일의 순수요 방향이 반대다 ({period}, 전체 대여소 기준)\n"
                 "2·4분면의 붉은 점 — 묶어 평균 내면 서로 상쇄돼 사라진다\n"
                 "(축은 1~99 분위로 잘랐다. 비율은 자르기 전 전체로 셌다)", fontsize=11, y=1.10)
    save(fig, "그림1-1_평일휴일_부호반전", "1.2의 33~37%를 눈으로")


# ────────────────────────────────────────────────────────────── 4-1
def fig_4_1():
    """파이프라인 구조. 4.1의 글을 그림으로 — 심사자가 가장 먼저 찾는 그림이다."""
    steps = [
        ("step0\n수집·전처리", "대여이력·API\n→ 순수요·목표재고"),
        ("step1\n군집화", "작업 대상 선정\n→ K-Medoids"),
        ("step2\n물량·경로", "ILP (수량)\n→ greedy VRP (순서)"),
        ("step3\n지도", "TMAP 경로\n시각화"),
        ("step4\n평가", "결품·포화\n· KPI"),
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
            "각 단계는 CSV 파일과 SQLite에 이중 기록한다 — CSV가 정본이고 DB는 조회용이다 (4.3)",
            ha="center", fontsize=8.5, color="#444")
    ax.set_title("그림 4-1  파이프라인 구조 — 단계마다 파일로 넘겨 독립 실행된다", fontsize=11)
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
            label=r"$\tanh$ 완화 (식 3.5, 정수화 뒤)")
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
    ax.legend(fontsize=8.5, loc="lower right")
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
            ax.text(i, 12_000, "공개\n자료\n없음", ha="center", va="bottom", fontsize=8, color="#888")
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
    ax.legend(handles, ["평월", "겨울 달(1·2월)"], fontsize=8.5, loc="upper left")
    total = sum(counts.values())
    ax.set_title(f"{len(counts)}개월 · 합계 {total:,}건 (막대 위 숫자는 만 건)", fontsize=10)
    save(fig, "그림4-3_월별_대여건수", f"대여이력 {len(counts)}개월 {total:,}건")


# ────────────────────────────────────────────────────────────── 5-3
def fig_5_3():
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
            f"2월 평일 평균 {feb_weekday:,.0f}건 (계획 통계의 달)", fontsize=8.5, color="#3182bd",
            va="top", bbox=dict(facecolor="white", edgecolor="none", alpha=0.85, pad=1.5))
    ax.set_ylabel("일별 대여 건수")
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%m-%d"))
    ax.legend(fontsize=8.5, loc="upper left")
    ax.set_ylim(0, daily.max() * 1.2)
    save(fig, "그림5-3_계절전환_일별대여", "26년 2~3월 일별 대여와 배율 산정 구간")


# ────────────────────────────────────────────────────────────── 5-2
def fig_5_2():
    """10~15시에는 대여소 대부분이 |mu| < 0.5다 — 전체 평균이 희석되는 이유 (5.3).

    <표 5-4>와 **같은 계산**이다 — 월쌍 9개의 학습 달, 평일, 대여소별 시간대 순수요
    평균(backtest_demand.daily_window_demand). 비율은 월쌍마다 구해 평균 낸다.

    ⚠️ **대여소는 학습 달과 검증 달에 모두 있는 곳만 센다.** 백테스트(`evaluate`)가
    두 달을 inner merge하기 때문이다. 학습 달에만 있는 곳까지 세면 05~10시가
    50.3%로 표(50.2%)와 어긋난다 — 2026-09-18 그림을 처음 만들 때 실제로 그랬다.
    """
    try:
        import db
        from project_config import select_day_type
        from backtest_demand import consecutive_pairs, daily_window_demand
    except Exception as exc:                       # pragma: no cover - 환경 의존
        print(f"  건너뜀 - 모듈을 못 불러왔습니다: {exc}")
        return
    with db.session() as conn:
        periods = [r[0] for r in conn.execute("SELECT DISTINCT period FROM net_demand").fetchall()]
        pairs = consecutive_pairs(periods)
        if not pairs:
            print("  건너뜀 - 이어지는 달이 없습니다.")
            return
        load = {p: select_day_type(db.load_frame(conn, "net_demand", period=p), "date", "weekday")
                for pair in pairs for p in pair}

    fig, axes = plt.subplots(1, 2, figsize=(10.4, 3.9), sharey=False)
    bins = np.arange(0, 10.25, 0.25)
    for ax, duration in zip(axes, ("_10_15", "_05_10")):
        pooled, near, targets = [], [], []
        for train, test in pairs:
            mu = daily_window_demand(load[train], duration).groupby("station_id")["demand"].mean().abs()
            mu = mu[mu.index.isin(set(load[test]["station_id"]))]
            pooled.append(mu)
            near.append(float((mu < 0.5).mean()))
            targets.append(int((mu > 2).sum()))
        values = pd.concat(pooled).clip(upper=10)
        counts, edges = np.histogram(values, bins=bins)
        colors = ["#b03a2e" if e < 0.5 else ("#3182bd" if e >= 2 else "#bbb") for e in edges[:-1]]
        ax.bar(edges[:-1], counts / len(pairs), width=0.25, align="edge", color=colors,
               edgecolor="white", lw=0.3)
        ax.set_title(f"{duration}   |μ|<0.5 {np.mean(near) * 100:.1f}% · "
                     f"|μ|>2 평균 {np.mean(targets):.0f}곳", fontsize=10)
        ax.set_xlabel("|μ| (대/일, 10 이상은 10에 모음)")
        print(f"    {duration}: |mu|<0.5 {np.mean(near) * 100:.1f}% · |mu|>2 평균 "
              f"{np.mean(targets):.0f}곳 (월쌍 {len(pairs)}개)")
    axes[0].set_ylabel("대여소 수 (월쌍 평균)")
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in ("#b03a2e", "#bbb", "#3182bd")]
    axes[1].legend(handles, ["|μ| < 0.5 — μ를 쓰든 0을 쓰든 같다", "0.5 ≤ |μ| ≤ 2",
                             "|μ| > 2 — 작업 대상 근사"], fontsize=8, loc="upper right")
    save(fig, "그림5-2_평균순수요_분포", "10~15시는 대여소 대부분이 0 근처 (5.3)")


FIGURES = {"6-1": fig_6_1, "6-2": fig_6_2, "6-3": fig_6_3,
           "5-1": fig_5_1, "1-1": fig_1_1, "4-1": fig_4_1,
           "3-2": fig_3_2, "4-3": fig_4_3, "5-2": fig_5_2, "5-3": fig_5_3}


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
