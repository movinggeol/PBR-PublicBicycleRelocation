"""UI 기준표를 지킨다 — docs/구현/DESIGN.md 'UI 기준표' (2026-10-10).

반복되는 요소(글자 · 단추 · 떠 있는 층 · 상태)를 역할마다 한 값으로 정하고, 실제 화면을 대 본 뒤
어긋난 13건을 고쳤다(docs/기록/UI_기준_점검_2026-10.md). 그때 겪은 것은 대부분 **한 곳만 다르게
적은 것**이었다 — 화면 하나의 `head_extra`가 공통 규칙에 져서 먹지 않거나, 날값 하나가 토큰 밖에
남거나. 여기서는 그 갈래가 다시 생기지 않는지 템플릿 원문을 본다. 보이는 값(계산된 스타일)은
브라우저가 알고, 그 확인 방법은 점검 문서에 있다.
"""
import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1] / "webapp" / "templates"
FIELD = sorted(TEMPLATES.glob("m_*.html"))
CONTROL = sorted(p for p in TEMPLATES.glob("*.html")
                 if not p.name.startswith("m_") and p.name not in ("_tokens.html", "device.html"))


def _styles(path: Path) -> str:
    """템플릿의 `<style>` · `style_extra` 안 CSS — 주석은 걷는다(옛 값을 인용한 설명은 규칙이 아니다)."""
    text = path.read_text(encoding="utf-8")
    parts = re.findall(r"<style>(.*?)</style>|{% block style_extra %}(.*?){% endblock %}", text, re.S)
    css = "".join(a + b for a, b in parts)
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _rules(css: str):
    """(감싼 at-규칙들, 선택자, 선언) — 중괄호 깊이를 따라가며 낸다."""
    stack, i, start = [], 0, 0
    while i < len(css):
        ch = css[i]
        if ch == "{":
            head = css[start:i].strip()
            j = css.find("}", i)
            k = css.find("{", i + 1)
            if head.startswith("@") or (k != -1 and k < j):
                stack.append(head)
                start = i + 1
            else:
                yield tuple(stack), head, css[i + 1:j]
                i = j
                start = j + 1
        elif ch == "}":
            if stack:
                stack.pop()
            start = i + 1
        i += 1


def _tokens(block: str) -> dict:
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;", block))


def _contrast(fg: str, bg: str) -> float:
    def lum(h):
        h = h.lstrip("#")
        v = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        v = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in v]
        return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]
    a, b = lum(fg), lum(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


# ─────────────────────────── 글자 ───────────────────────────

def test_관제_화면의_굵기는_400과_600뿐이다():
    """500 · 700이 섞여 있었다(결과 묶음 현재 항목 · 접이식 제목 · 경고 기호). 브라우저 기본 `<b>`도
    700이라 강조 글자 300여 곳이 기준 밖이었다 — `b, strong`을 600으로 묶어 둔다."""
    for path in CONTROL:
        for _, sel, body in _rules(_styles(path)):
            if sel.startswith("@font-face"):
                continue
            for w in re.findall(r"font-weight:\s*([^;]+)", body):
                assert w.strip() in ("400", "600"), f"{path.name} {sel}: font-weight {w.strip()}"
    base = _styles(TEMPLATES / "base.html")
    assert re.search(r"(^|\n)\s*b, strong \{ font-weight: 600; \}", base), "브라우저 기본 <b>(700)를 그대로 둔다"


def test_현장_앱의_굵기는_400_600_700뿐이다():
    """현장 앱은 제목만 700이다(1.26.308 밀도 규칙). 끝낸 방문지 이름 · 타일 단위의 500을 걷었다."""
    for path in FIELD:
        for _, sel, body in _rules(_styles(path)):
            if sel.startswith("@font-face"):
                continue
            for w in re.findall(r"font-weight:\s*([^;]+)", body):
                assert w.strip() in ("400", "600", "700"), f"{path.name} {sel}: font-weight {w.strip()}"


def test_관제_화면_글자는_12px_아래로_내리지_않는다():
    """예외는 둘이다 — SVG 그래프의 눈금(viewBox 단위라 그래프 크기에 비례한다)과 종이에 찍는
    작업지시서 표(인쇄 매체, 11px ≈ 8pt). 타일 덧말 · 증감 · 탭 이름 · 풍선 제목이 11px이었다."""
    for path in CONTROL:
        for at, sel, body in _rules(_styles(path)):
            for v in re.findall(r"font-size:\s*([\d.]+)px", body):
                if float(v) >= 12:
                    continue
                svg = all(s.strip().startswith(".viz-") for s in sel.split(","))
                printed = any(a.startswith("@media print") for a in at)
                assert svg or printed, f"{path.name} {sel}: font-size {v}px — 12px 아래"


def test_현장_앱_글자_크기는_별칭_단계만_쓴다():
    """14벌(12.5 · 11.5 · 10.5 · 13.5 같은 반 픽셀까지)이던 것을 별칭 층의 단계(`--m-fs-*`)로 모았다.
    날값으로 남겨 둔 것은 둘 — 폭을 따라 움직이는 타일 값과 지도 확대 단추의 기호(+ −)다."""
    allowed = {".m-tile .value": "clamp(16px, 4.8vw, 19px)", ".m-map .leaflet-control-zoom a": "20px"}
    for path in FIELD:
        css = _styles(path).replace('{% include "_tokens.html" %}', "")
        for _, sel, body in _rules(css):
            if sel == ":root" and "--m-accent:" in body:
                continue                                    # 별칭 층 — 값을 정하는 곳
            for v in re.findall(r"font-size:\s*([^;]+)", body):
                v = v.strip()
                if v.startswith("var(--m-fs-"):
                    continue
                assert allowed.get(sel) == v, f"{path.name} {sel}: font-size {v} — --m-fs-* 단계를 써라"


def test_힌트는_자간을_직접_준다():
    """부모 것을 물려받아 같은 12px 힌트가 −0.224 · −0.374 · −0.4 세 가지였다(163곳)."""
    for _, sel, body in _rules(_styles(TEMPLATES / "base.html")):
        if sel == ".hint":
            assert "letter-spacing: -.08px" in body
            return
    raise AssertionError(".hint 규칙이 없다")


# ─────────────────────────── 히어로 ───────────────────────────

def test_작업_화면_히어로는_base의_한_벌을_쓴다():
    """현황판은 `.home-hero h1`로 제목을 24px로 줄였다고 적어 두었는데 base.html의 `.hero h1`과
    특이도가 같고 먼저 실려 **한 번도 먹지 않았다**(제목 40px). 화면 쪽에서 히어로 제목 · 리드의
    크기를 다시 적지 않는다 — `hero compact`를 단다."""
    base = _styles(TEMPLATES / "base.html")
    assert ".hero.compact h1 {" in base
    for name in ("home.html", "index.html"):
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        assert re.search(r'<section class="hero compact[" ]', text), f"{name}가 작업 화면 히어로를 안 쓴다"
    for path in CONTROL:
        if path.name == "base.html":
            continue
        for _, sel, body in _rules(_styles(path)):
            if "hero" in sel and re.search(r"\b(h1|\.lead)\b", sel) and "font-size" in body:
                raise AssertionError(f"{path.name} {sel}: 히어로 글자 크기를 화면에서 다시 적었다")


# ─────────────────────────── 단추 · 상태 ───────────────────────────

def test_채운_단추는_두_테마_모두_올리면_색이_바뀐다():
    """올림 색을 초점 색(--blue-focus)에 기대면 다크에서는 --blue와 같아 변화가 없었다."""
    css = (TEMPLATES / "_tokens.html").read_text(encoding="utf-8")
    light = css[css.index(":root {"):css.index("@media (prefers-color-scheme: dark)")]
    dark_media = css[css.index("@media (prefers-color-scheme: dark)"):css.index(':root[data-theme="dark"]')]
    dark = css[css.index(':root[data-theme="dark"]'):]
    for label, block in (("라이트", light), ("다크(시스템)", dark_media), ("다크(단추)", dark)):
        tok = _tokens(block)
        assert tok["blue-hover"].lower() != tok["blue"].lower(), f"{label}: 올려도 같은 색"
        ratio = _contrast(tok["on-blue"], tok["blue-hover"])
        assert ratio >= 4.5, f"{label}: 올린 단추 위 글자 {ratio:.2f}:1"
    for _, sel, body in _rules(_styles(TEMPLATES / "base.html")):
        if sel == ".btn:hover":
            assert "var(--blue-hover)" in body
            return
    raise AssertionError(".btn:hover 규칙이 없다")


def test_누름_반응은_하나다():
    """관제 화면은 .95, 현장 앱은 .97이었다."""
    for path in CONTROL + FIELD:
        for _, sel, body in _rules(_styles(path)):
            if ":active" in sel:
                for v in re.findall(r"scale\(([^)]+)\)", body):
                    assert v == ".97", f"{path.name} {sel}: scale({v})"


def test_작은_단추는_한_벌이다():
    """작업 흐름 줄의 '계획 실행'만 `.flow-nav .btn`으로 7 · 18을 따로 받았다."""
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert ".flow-nav .btn" not in _styles(TEMPLATES / "base.html")
    assert '<a class="btn sm" href="/run#run"' in base


def test_입력칸과_선택_상자는_같은_높이다():
    """/run 한 줄에서 글 입력칸 38px · 선택 상자 40px로 바닥이 2px 어긋났다."""
    for _, sel, body in _rules(_styles(TEMPLATES / "base.html")):
        if sel.startswith(".field input[type=text]"):
            assert "min-height: 40px" in body
            return
    raise AssertionError("입력칸 규칙이 없다")


def test_비활성은_한_벌이다():
    """회색 채움(단추) · 투명도 .35(칩) · 투명도 .55(입력칸)로 셋이었다. 현장 앱 단추와 같은 모양
    (옅은 면 · 흐린 글자 · 헤어라인)으로 모은다. 줄 단추(m-row)는 여러 색의 글이 들어 줄째 흐린다."""
    found = False
    for _, sel, body in _rules(_styles(TEMPLATES / "base.html")):
        if ":disabled" in sel and "opacity" in body:
            assert "opacity: 1" in body, f"{sel}: 투명도로 흐리지 않는다"
        if sel.startswith(".btn:disabled"):
            found = True
            assert ".chip:disabled" in sel
            for want in ("var(--surface-2)", "var(--ink-4)", "var(--hairline)", "not-allowed"):
                assert want in body
    assert found, "단추 비활성 규칙이 없다"


# ─────────────────────────── 그림자 ───────────────────────────

def test_그림자는_토큰으로만_쓴다():
    """떠 있는 층(표 창 · 고르기 판 · 풍선)의 그림자가 토큰 밖 날값 두 벌이었다. 그림자 값은
    `_tokens.html`에만 있고 규칙은 `var(--shadow-*)`를 읽는다. 선으로 쓰는 `inset` · `0 0 0 3px`
    고리는 그림자가 아니라 테두리 대용이라 여기서 보지 않는다."""
    for path in CONTROL + FIELD:
        assert "rgba(0, 0, 0" not in _styles(path).replace('{% include "_tokens.html" %}', ""), (
            f"{path.name}: 그림자 날값 — _tokens.html의 --shadow-*를 써라")
    base = _styles(TEMPLATES / "base.html")
    for sel in (".table-panel", ".pick-panel"):
        body = next(b for _, s, b in _rules(base) if s == sel)
        assert "box-shadow: var(--shadow-float)" in body, sel
    tip = next(b for _, s, b in _rules(base) if s == "#tipbox" and "box-shadow" in b)
    assert "var(--shadow-float-sm)" in tip
