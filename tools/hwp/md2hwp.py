"""논문 원고(.md)를 학과 양식 서식의 한글 파일로 옮긴다(한글_옮기기.md 2~4장).

build.py가 부른다(write_all). 부록 파일 앞에 참고문헌 쪽을 넣고, 인용은 처음 나온 순서의 번호로 바꾼다(refs.py).

## 만들며 걸린 함정
- 한글 자동 고침: 글을 넣은 뒤 다음 동작(문단 나누기 · 글자 모양 · 다음 칸)에서 마지막 `--`를 `—`로, 끝 따옴표를
  둥근 따옴표로, 주소를 하이퍼링크로 바꾼다. build.py가 HWPX에서 되돌리고, 참고문헌은 줄바꿈을 글 안에 넣어 피한다.
- 표 배치: TableCreate의 배치 설정은 한 프로세스에서 처음 만든 표의 것만 먹는다. 만든 뒤 표 속성으로 다시 준다.
- 내어쓰기(Indentation) 값은 자동화 단위가 화면과 약 3배 어긋나 실측으로 맞췄다(item · references · code).
"""
import math
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hwpkit import Hwp  # noqa: E402
from latex2hwp import convert, forms  # noqa: E402
from refs import Refs  # noqa: E402
from PIL import Image  # noqa: E402

TEXT_W = 132.0  # 본문 폭(mm): 182 - 25 - 25
MAX_FIG_H = 150.0
EQ_SIDE = 15.0  # 번호 붙은 줄 수식의 양옆 칸(mm)
EQ_MAX_W = TEXT_W - 2 * EQ_SIDE - 5.0  # 가운데 칸 안쪽 여백을 뺀 수식 폭 상한(mm)
CODE_FONT = "돋움체"
MATH = re.compile(r"\$([^$\n]+?)\$")
SEG = re.compile(r"\$([^$\n]+?)\$|`([^`\n]+)`")


def log(*a):
    print(*a, flush=True)


# ── 마크다운 읽기 ─────────────────────────────────────────────

def blocks(md_text):
    """(종류, 내용) 목록. 종류: h1 h2 h3 para eq img table caption_fig caption_tab bullet number code."""
    t = re.sub(r"<!--.*?-->", "", md_text, flags=re.S)
    lines = t.split("\n")
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if not s:
            i += 1
            continue
        if s.startswith("```"):
            buf = []
            i += 1
            while not lines[i].strip().startswith("```"):
                buf.append(lines[i].rstrip())
                i += 1
            i += 1
            out.append(("code", buf))
            continue
        if s.startswith("$$"):
            buf = [s[2:]]
            i += 1
            while "$$" not in lines[i]:
                buf.append(lines[i])
                i += 1
            buf.append(lines[i].split("$$")[0])
            i += 1
            out.append(("eq", " ".join(buf)))
            continue
        m = re.match(r"(#{1,3}) (.*)", s)
        if m:
            out.append(("h%d" % len(m.group(1)), m.group(2).strip()))
            i += 1
            continue
        m = re.match(r"!\[[^\]]*\]\(([^)]+)\)", s)
        if m:
            out.append(("img", m.group(1)))
            i += 1
            continue
        if s.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in _split_row(lines[i].strip())]
                if not all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            out.append(("table", rows))
            continue
        m = re.match(r"(- |\d+\. )(.*)", s)
        if m:
            buf = [m.group(2)]
            i += 1
            while i < len(lines) and lines[i].startswith("  ") and lines[i].strip():
                buf.append(lines[i].strip())
                i += 1
            kind = "bullet" if m.group(1) == "- " else "number"
            num = m.group(1).strip().rstrip(".")
            out.append((kind, (num, " ".join(buf))))
            continue
        buf = [s]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"\s*(#|\||!\[|\$\$|- |\d+\. |```)", lines[i]):
            buf.append(lines[i].strip())
            i += 1
        text = " ".join(buf)
        if re.match(r"\[그림 [A-Z0-9]+-\d+\] ", text) and out and out[-1][0] == "img":
            out.append(("caption_fig", text))
        elif re.match(r"<표 [A-Z0-9]+-\d+> ", text) and _is_table_caption(lines, i):
            out.append(("caption_tab", text))
        else:
            out.append(("para", text))
    return out


def _split_row(row):
    """표 한 줄을 칸으로 나눈다. `…` 안의 |는 칸 경계가 아니다."""
    cells, cur, code = [], [], False
    for ch in row.strip("|"):
        if ch == "`":
            code = not code
        if ch == "|" and not code:
            cells.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    cells.append("".join(cur))
    return cells


def _is_table_caption(lines, i):
    """다음 비어 있지 않은 줄이 표이면 표 제목이다(본문이 <표 n-m>으로 시작하는 경우와 가른다)."""
    while i < len(lines) and not lines[i].strip():
        i += 1
    return i < len(lines) and lines[i].strip().startswith("|")


def chapter_title(s):
    m = re.match(r"제(\d+)장 (.*)", s)
    if m:
        title = m.group(2)
        if len(title) == 2:
            title = " ".join(title)
        return "제 %s 장 %s" % (m.group(1), title)
    m = re.match(r"부록 ([A-Z]) (.*)", s)
    if m:
        return "[부 록 %s] %s" % (m.group(1), m.group(2))
    return s


# ── 한글에 쓰기 ──────────────────────────────────────────────

class Writer:
    def __init__(self, hwp, base_dir, refs=None):
        self.w = hwp
        self.base = base_dir
        self.refs = refs or Refs()
        self.first_chapter = True
        self.fit = {}
        self.targets = []  # 차례에 실을 것: (종류, 표시 글, 찾을 글)
        self.in_appendix = False

    def inline(self, text, size=10.0, bold=False):
        """$…$는 한글 수식, `…`는 돋움체로, 나머지는 인용 번호를 붙여 글자로 넣는다."""
        pos = 0
        for m in SEG.finditer(text):
            self.w.text(self.refs.cite(text[pos:m.start()]))
            if m.group(1) is not None:
                self.w.equation(convert(m.group(1))[0], size)
            else:
                self.w.char(size - 1, bold=bold, font=CODE_FONT)
                self.w.text(m.group(2))
                self.w.char(size, bold=bold)
            pos = m.end()
        self.w.text(self.refs.cite(text[pos:]))

    def heading(self, s, size, align, before, after):
        self.w.para(align, 160, before=before, after=after, keep=True)
        self.w.char(size, bold=True)
        self.inline(s, size, bold=True)
        self.w.enter()

    def h1(self, s):
        if not self.first_chapter:
            self.w.page_break()
        self.first_chapter = False
        self.heading(chapter_title(s), 13, "center", 0, 20)
        self.targets.append(("h1", chapter_title(s), chapter_title(s)))

    def h2(self, s):
        self.heading(s, 12, "left", 14, 4)
        if not self.in_appendix:
            self.targets.append(("h2", s, s))

    def h3(self, s):
        self.heading(s, 11, "left", 8, 2)
        if not self.in_appendix:
            self.targets.append(("h3", s, s))

    def para(self, s):
        if self.in_appendix and re.fullmatch(r"`[^`]+`", s):
            # 부록 B의 파일 이름 줄: 코드 상자의 머리표처럼 굵게, 아래 상자와 한 쪽에
            self.w.para("left", 160, before=8, after=2, keep=True)
            self.w.char(9, bold=True, font=CODE_FONT)
            self.w.text(s.strip("`"))
            self.w.enter()
            return
        # 부록은 파일 이름 · 명령 · 영문 낱말이 길어(띄어 쓴 자리에서만 줄이 바뀐다) 양쪽 정렬이면 앞 줄
        # 낱말 사이가 크게 벌어진다. 부록 문단은 왼쪽 정렬로 둔다
        self.w.para("left" if self.in_appendix else "justify", 200)
        self.w.char(10)
        if s.startswith("주:"):
            self.w.char(9)
            self.inline(s, 9)
        else:
            self.inline("  " + s)
        self.w.enter()

    def item(self, mark, s):
        # 내어쓰기 값은 자동화 단위가 화면과 약 3배 어긋나 실측으로 맞췄다(-32 ≒ 글머리표와 띄어쓰기 폭)
        self.w.para("justify", 200, left=0, indent=-32)
        self.w.char(10)
        self.inline(mark + " " + s)
        self.w.enter()

    def code(self, lines):
        """회색 바탕 상자 하나에 담는다. 쪽 끝에서는 줄 단위로 나뉜다. 주석 줄은 회색 글자, 길어서 꺾인 줄은
        내어 써서 원래 줄의 이음인 것이 보이게 한다(내어쓰기 30 ≒ 반각 4자, 자동화 단위 보정은 item()과 같다)."""
        self.gap(keep=True)
        for k, ln in enumerate(lines):
            self.w.para("left", 150, left=30, indent=-30, box=True, keep=k < 2)  # 첫 줄만 쪽 끝에 남지 않게
            self.w.char(8, font=CODE_FONT, color=0x707070 if ln.lstrip().startswith("#") else 0)
            self.w.text(ln)
            self.w.enter()
        self.gap(line=160)

    def gap(self, line=100, keep=False):
        """상자 앞뒤의 얇은 빈 줄(상자 바탕을 끊는 역할도 한다)."""
        self.w.para("left", line, keep=keep)
        self.w.char(6)
        self.w.enter()

    def measure(self, md_paths, tmp_dir):
        """줄 수식마다 후보 모양의 실제 폭을 재어, 수식 칸에 들어가는 첫 모양을 고른다."""
        cands = []
        for md in md_paths:
            for kind, val in blocks(open(md, encoding="utf-8").read()):
                if kind == "eq":
                    for script, size in forms(val):
                        cands.append((val, script, size))
        if not cands:
            return
        for _, script, size in cands:
            self.w.para("left", 100)
            self.w.equation(script, size)
            self.w.enter()
        tmp = os.path.join(tmp_dir, "_measure.hwpx")
        self.w.save(tmp, "HWPX")
        xml = zipfile.ZipFile(tmp).read("Contents/section0.xml").decode("utf-8")
        widths = [int(w) / 283.465 for w in re.findall(r"<hp:equation .*?<hp:sz width=\"(\d+)\"", xml, re.S)]
        assert len(widths) == len(cands), (len(widths), len(cands))
        for (latex, script, size), wmm in zip(cands, widths):
            if latex not in self.fit and (wmm <= EQ_MAX_W or size == 8.0):
                self.fit[latex] = (script, size, wmm)
        self.w.run("SelectAll")
        self.w.run("Delete")
        for latex, (script, size, wmm) in self.fit.items():
            if script != forms(latex)[0][0] or size != 10.0:
                log("  긴 수식 조정: %.0fmm, %gpt, %s" % (wmm, size, script[:50]))

    def eq(self, latex):
        script, size, _ = self.fit.get(latex) or (convert(latex)[0], 10.0, 0)
        tag = convert(latex)[1]
        if not tag:
            self.w.para("center", 200)
            self.w.char(10)
            self.w.equation(script, size)
            self.w.enter()
            return
        # 번호 붙은 줄 수식: 테두리 없는 1행 3칸 표(여백 · 수식 · 번호). 탭은 자동화에서 일관되지 않았다.
        side = EQ_SIDE
        self.w.para("center", 130, before=2, after=2)
        self.w.table(1, 3, [side, TEXT_W - 2 * side, side])
        self.w.run("TableRightCell")
        self.w.para("center", 130)
        self.w.char(10)
        self.w.equation(script, size)
        self.w.run("TableRightCell")
        self.w.para("right", 130)
        self.w.char(10)
        self.w.text("(%s)" % tag)
        self.w.no_borders()
        self.w.run("MoveDocEnd")
        self.w.enter()

    def img(self, rel):
        path = os.path.normpath(os.path.join(self.base, rel))
        with Image.open(path) as im:
            px_w, px_h = im.size
            dpi = (im.info.get("dpi") or (150, 150))[0] or 150
        width = min(TEXT_W, px_w / dpi * 25.4)
        height = width * px_h / px_w
        if height > MAX_FIG_H:
            height = MAX_FIG_H
            width = height * px_w / px_h
        self.w.para("center", 100, before=6, keep=True)
        self.w.picture(path, width, height)
        self.w.run("MoveDocEnd")
        self.w.enter()

    def caption(self, s, fig):
        self.w.para("center", 160, before=2 if fig else 8, after=8 if fig else 2, keep=not fig)
        self.w.char(10)
        self.inline(s)
        self.w.enter()
        self.targets.append(("fig" if fig else "tab", short_caption(s), s))

    def table(self, rows):
        ncol = max(len(r) for r in rows)
        rows = [r + [""] * (ncol - len(r)) for r in rows]
        if "출처" in rows[0]:
            j = rows[0].index("출처")
            for r in rows[1:]:
                r[j] = self.refs.source(r[j])

        widths = col_widths(rows)
        numeric = [_numeric_col([r[j] for r in rows[1:]]) for j in range(ncol)]
        self.w.para("center", 130)
        # 쪽을 넘길 만큼 긴 표만 쪽 끝에서 나눈다. 짧은 표까지 나누면 쪽 끝에 제목 줄만 남거나 표 제목과
        # 표가 떨어진다(띄워 놓은 표는 제목의 '다음 문단과 함께'가 닿지 않는다). 9pt · 줄 간격 130%의 한 줄은
        # 약 4.1mm, 칸 위아래 여백이 약 1mm. 본문 높이는 207mm
        est = sum(max(_n_lines(c, widths[j]) for j, c in enumerate(r)) * 4.1 + 1.0 for r in rows)
        split = est > 150
        if split:
            log("  긴 표 나눔: %s… 약 %dmm" % (rows[0][0][:8], est))
        self.w.table(len(rows), ncol, widths, split=split)
        if split:
            self.w.header_row()
        for ri, r in enumerate(rows):
            for j, c in enumerate(r):
                align = "center" if ri == 0 else ("right" if numeric[j] else "left")
                self.w.para(align, 130)
                self.w.char(9)
                self.inline(c, 9)
                if not (ri == len(rows) - 1 and j == ncol - 1):
                    self.w.run("TableRightCell")
        self.w.run("MoveDocEnd")
        self.w.enter()

    def references(self):
        self.w.page_break()
        self.heading("[참 고 문 헌]", 12, "center", 0, 14)
        self.targets.append(("h1", "[참 고 문 헌]", "[참 고 문 헌]"))
        for item in self.refs.listing():
            # 둘째 줄부터 약 16.6pt 내어쓰기(2-3). 자동화 단위 보정은 item()과 같다
            self.w.para("left", 160, left=0, indent=-38)  # 주소 · 영문 제목이 길어 양쪽 정렬이면 벌어진다
            self.w.char(10)
            # 글 안의 줄바꿈으로 문단을 끝낸다. 문단 나누기 동작을 쓰면 한글이 주소를 하이퍼링크(파란 밑줄)로 바꾼다
            self.w.text(item + chr(13) + chr(10))
        log("  참고문헌 %d편 (목록에만 있고 인용 안 된 것: %s)" % (len(self.refs.order), self.refs.uncited() or "없음"))
        if self.refs.unknown:
            log("  ⚠ 목록에 없는 인용:", self.refs.unknown)

    def write(self, md_path):
        bl = blocks(open(md_path, encoding="utf-8").read())
        for kind, val in bl:
            if kind in ("h1", "h2", "h3"):
                getattr(self, kind)(val)
            elif kind == "para":
                self.para(val)
            elif kind == "eq":
                self.eq(val)
            elif kind == "img":
                self.img(val)
            elif kind == "caption_fig":
                self.caption(val, True)
            elif kind == "caption_tab":
                self.caption(val, False)
            elif kind == "table":
                self.table(val)
            elif kind == "bullet":
                self.item("•", val[1])
            elif kind == "number":
                self.item(val[0] + ")", val[1])
            elif kind == "code":
                self.code(val)
        log("  %s: 블록 %d개" % (os.path.basename(md_path), len(bl)))


def short_caption(cap):
    """차례에 싣는 짧은 제목: 번호 + 조건 괄호와 덧붙인 설명을 뺀 앞부분(앞붙이.md 7장 끝 규칙)."""
    m = re.match(r"(\[그림 [A-Z0-9]+-\d+\]|<표 [A-Z0-9]+-\d+>) (.*)", cap)
    label, title = m.group(1), m.group(2)
    title = re.split(r" \(|\. ", title)[0].rstrip(".")
    return label + " " + title


UNIT, PAD = 1.65, 3.8  # 9pt에서 반각 한 글자 폭(mm), 칸 안 여백(mm)


def _units(c):
    plain = MATH.sub(lambda m: "x" * max(2, len(m.group(1)) // 3), c).replace("`", "")
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in plain)


def _longest_word(c):
    return max([_units(t) for t in c.split()] or [0])


def _n_lines(c, wmm):
    return max(1, math.ceil(_units(c) * UNIT / max(wmm - PAD, 5.0)))


def col_widths(rows):
    """표 열 너비(mm). 합은 본문 폭이다."""
    ncol = len(rows[0])

    def need(j, rs, head=None):
        # 한글은 글자 단위로 줄이 바뀌므로, 칸마다 반각 10자(전각 5자)까지는 한 줄에 들도록 한다
        n = max([_units(r[j]) for r in rs] + [_longest_word(head[j]) if head else 0])
        return max(10.0, n * UNIT + PAD)

    nat = [need(j, rows) for j in range(ncol)]
    if sum(nat) <= TEXT_W:
        return [TEXT_W * n / sum(nat) for n in nat]
    # 머리글이 먼저 줄을 바꾼다. 머리글 "예산을 넘긴 차량 (대)"가 숫자 열을 넓히면 그만큼 본문 열이 좁아져
    # "B1 그리 / 디"처럼 이름이 꺾였다(<표 6-3>). 본문 칸이 한 줄에 들고 머리글은 낱말이 안 끊기는 폭을 먼저 주고,
    # 남는 폭은 머리글이 더 바라는 열에 나눈다
    body = [need(j, rows[1:], rows[0]) for j in range(ncol)]
    if sum(body) <= TEXT_W:
        extra = [n - b for n, b in zip(nat, body)]
        return [b + (TEXT_W - sum(body)) * e / sum(extra) for b, e in zip(body, extra)]
    lo = [min(nat[j], max(10, max(_longest_word(r[j]) for r in rows)) * UNIT + PAD) for j in range(ncol)]
    # 한 열이 아주 길면 남는 폭이 거의 다 그 열로 가서 짧은 열이 낱말 하나 폭으로 줄고 "<표 / 5-1>"처럼
    # 꺾인다(<표 A-1>). 짧은 열에도 반각 12자(전각 6자)까지는 먼저 준다
    floor = [min(nat[j], max(lo[j], 12 * UNIT + PAD)) for j in range(ncol)]
    if sum(floor) < TEXT_W:
        lo = floor
    if sum(lo) >= TEXT_W:
        return [TEXT_W * n / sum(lo) for n in lo]
    extra = [n - m for n, m in zip(nat, lo)]
    return [m + (TEXT_W - sum(lo)) * e / sum(extra) for m, e in zip(lo, extra)]


def _numeric_col(cells):
    vals = [c for c in cells if c and c not in ("없음", "기준", "해당 없음")]
    if not vals:
        return False
    num = sum(1 for c in vals if re.fullmatch(r"[−\-+]?[\d,.]+\s*(%p?|%p|대|곳|초|분|km|개|회|일|건|배)?(\s*\(.*\))?", c))
    return num / len(vals) >= 0.6


def write_all(hwp, mds, tmp_dir):
    """본문 장들, 참고문헌, 부록을 차례로 쓴다. Writer를 돌려준다(차례 만들기에 쓴다)."""
    wr = Writer(hwp, os.path.dirname(os.path.abspath(mds[0])))
    wr.measure(mds, tmp_dir)
    hwp.page_num(roman=False)  # 본문은 제1장 첫 쪽이 1(2-2)
    hwp.new_number(1)
    refs_done = False
    for md in mds:
        if os.path.basename(md).startswith("부록") and not refs_done:
            wr.references()
            refs_done = True
        wr.in_appendix = os.path.basename(md).startswith("부록")
        wr.write(md)
    if not refs_done:
        wr.references()
    return wr

