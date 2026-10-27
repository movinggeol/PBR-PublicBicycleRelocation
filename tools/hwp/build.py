"""논문 원고(.md) 전체를 학과 양식 한글 파일로 조립한다(docs/연구/논문/한글_옮기기.md 6장).

## 왜 스크립트인가

`.md`가 정본이라 원고를 고칠 때마다 한글 파일을 다시 만들어야 한다(한글_옮기기.md 8장). 손으로 붙이면
수식 26개 · 표 · 그림 47개 · 차례 쪽 번호를 매번 다시 손봐야 하므로, 한글 자동화(COM)로 처음부터 다시 쓴다.

## 단계

1) body : 1~9장 · 참고문헌 · 부록을 body.hwp로 쓰고(쪽 번호 1부터), PDF에서 제목 · 캡션의 쪽을 찾는다.
          한글 자동 고침이 바꾼 글자를 되돌린 body_fixed.hwpx도 만든다(fix 단계)
2) front: 차례 · 그림 차례 · 표 차례 · 초록을 front.hwp로 쓴다(쪽 번호 로마 소문자)
3) final: 양식 파일에서 가이드라인 쪽과 샘플 차례 이후를 지우고, 표지 글자를 바꾼 뒤 front · body를 잇는다.
          결과를 docs/연구/논문/졸업논문_자동변환.hwp로 복사한다(.gitignore가 막는 경로)

이름 · 지도교수 · 날짜는 {이름} · {지도교수} · ○월 ○일로 남긴다. 개인 정보는 한글에서만 채운다.
한 번에 15~25분 걸리고, 한글 보안 승인 창('글')에 '허용'을 눌러 주어야 한다.

사용:
    python tools/hwp/build.py --template "…가이드라인 및 양식(251105).hwp"
    python tools/hwp/build.py --stages front,final --template …   # 본문은 두고 앞붙이 · 조립만
"""
import argparse
import json
import os
import re
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import project_config  # noqa: E402  (콘솔 인코딩을 먼저 세운다 · DATA_ROOT)
import pymupdf as fitz  # noqa: E402
from hwpkit import Hwp  # noqa: E402
import md2hwp  # noqa: E402

SRC = os.path.join(ROOT, "docs", "연구", "논문")
OUT = str(project_config.DATA_ROOT / "hwp_build")  # data/는 커밋되지 않는다
COPY_TO = os.path.join(SRC, "졸업논문_자동변환.hwp")

CHAPTERS = ["1장_서론.md", "2장_관련연구.md", "3장_문제정의와_모형.md", "4장_시스템설계.md", "5장_파라미터결정.md",
            "6장_성능평가.md", "7장_고찰.md", "8장_한계와_향후과제.md", "9장_결론.md", "부록_재현.md", "부록_핵심코드.md"]
TOC_FONT = "바탕"
ROMAN = "ⅰ ⅱ ⅲ ⅳ ⅴ ⅵ ⅶ ⅷ ⅸ ⅹ".split()
log = md2hwp.log


def norm(s):
    return re.sub(r"\s+", "", s)


def find_pages(pdf, keys, first=1, at_top=False):
    """keys를 차례대로 PDF에서 찾아 쪽 번호(first부터)를 돌려준다. 앞 항목보다 앞쪽은 보지 않는다.
    수식은 PDF 글자가 달라지므로 첫 수식 앞까지만 찾는다. at_top이면 쪽 첫머리에 있어야 한다."""
    doc = fitz.open(pdf)
    # 쪽 번호("-ⅰ-", "-12-")가 글 맨 앞에 뽑히므로 떼어 낸다
    pages = [re.sub(r"^-[0-9ivxlc]+-", "", norm(p.get_text())) for p in doc]
    out, cur = [], 0
    for k in keys:
        key = norm(k.split("$")[0])[:14]
        hit = next((i for i in range(cur, len(pages))
                    if (pages[i].startswith(key) if at_top else key in pages[i])), None)
        if hit is None:
            log("  ⚠ 쪽을 못 찾음:", k)
            out.append(None)
            continue
        cur = hit
        out.append(hit + first)
    return out


AUTOCORRECT = [  # 한글이 글 넣은 뒤 다음 동작에서 고친 것을 되돌린다. 원고 · 참고문헌에 이 꼴은 없다
    (re.compile("—"), "--"),               # --seed → —seed
    (re.compile(" – "), " - "),            # a - b → a – b (쪽 범위 667–680은 띄어쓰기가 없어 그대로)
    (re.compile(r"(?<=\S)“"), '"'),        # 끝 따옴표 "9" → "9“ (참고문헌의 여는 “는 띄어쓰기 뒤라 그대로)
    (re.compile("’"), "'"),                # f'net_' → f'net_’
]


def undo_autocorrect(hwpx_in, hwpx_out):
    """HWPX 본문 글(<hp:t>)에서 자동 고침을 되돌려 새 HWPX로 쓴다. 되돌린 개수를 돌려준다."""
    n = 0

    def fix_text(m):
        nonlocal n
        t = m.group(2)
        for pat, rep in AUTOCORRECT:
            t, k = pat.subn(rep, t)
            n += k
        return m.group(1) + t + m.group(3)

    with zipfile.ZipFile(hwpx_in) as zi, zipfile.ZipFile(hwpx_out, "w") as zo:
        for item in zi.infolist():
            data = zi.read(item.filename)
            if re.fullmatch(r"Contents/section\d+\.xml", item.filename):
                data = re.sub(r"(<hp:t>)([^<]*)(</hp:t>)", fix_text, data.decode("utf-8")).encode("utf-8")
            zo.writestr(item, data)  # mimetype이 맨 앞 · 무압축이어야 하므로 항목 정보를 그대로 쓴다
    return n


def stage_fix(out, reopen=True):
    """body.hwp → body.hwpx → 자동 고침을 되돌린 body_fixed.hwpx. 최종 조립은 이 파일을 잇는다."""
    if reopen:
        hwp = Hwp(visible=False)
        try:
            log("본문 열기", hwp.h.Open(os.path.join(out, "body.hwp"), "HWP", "forceopen:true"))
            log("저장 hwpx", hwp.save(os.path.join(out, "body.hwpx"), "HWPX"))
        finally:
            hwp.quit()
    log("자동 고침 되돌림 %d곳" % undo_autocorrect(os.path.join(out, "body.hwpx"), os.path.join(out, "body_fixed.hwpx")))


def stage_body(src, out):
    hwp = Hwp(visible=False)
    try:
        hwp.page_setup()
        wr = md2hwp.write_all(hwp, [os.path.join(src, f) for f in CHAPTERS], out)
        log("저장 hwp", hwp.save(os.path.join(out, "body.hwp")))
        log("저장 hwpx", hwp.save(os.path.join(out, "body.hwpx"), "HWPX"))
        log("저장 pdf", hwp.save(os.path.join(out, "body.pdf"), "PDF"))
    finally:
        hwp.quit()
    stage_fix(out, reopen=False)
    pages = find_pages(os.path.join(out, "body.pdf"), [t[2] for t in wr.targets])
    toc = [dict(kind=k, text=d, page=p) for (k, d, _), p in zip(wr.targets, pages)]
    json.dump(toc, open(os.path.join(out, "toc.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log("차례 항목 %d개, 본문 %d쪽" % (len(toc), fitz.open(os.path.join(out, "body.pdf")).page_count))


def stage_front(src, out):
    toc = json.load(open(os.path.join(out, "toc.json"), encoding="utf-8"))
    abstract = md2hwp.blocks(open(os.path.join(src, "초록.md"), encoding="utf-8").read())
    hwp = Hwp(visible=False)
    wr = md2hwp.Writer(hwp, src)
    try:
        hwp.page_setup()
        hwp.page_num(roman=True)
        hwp.new_number(1)  # 표지 구역의 쪽 수를 잇지 않고 차례가 ⅰ

        def title(s):
            hwp.para("center", 160, after=16)
            hwp.char(13, bold=True)
            hwp.text(s)
            hwp.enter()

        def entry(text, page, bold=False, first=False, indent=""):
            if first:
                hwp.para("left", 170, tabs=[(132.0, "right", 3)])  # 오른쪽 탭 + 점선 채움(차례 항목)
            hwp.char(10, bold=bold, font=TOC_FONT)
            wr.inline(indent + text, 10, bold=bold)
            hwp.text("\t%s" % page)
            hwp.enter()

        title("차            례")
        for k, name in enumerate(["그림 차례", "표 차례", "초록"]):
            entry(name, "@R%d@" % k, first=(k == 0))
        for t in toc:
            if t["kind"] == "h1":
                entry(t["text"], t["page"], bold=True)
            elif t["kind"] == "h2":
                entry(t["text"], t["page"], indent="   ")
            elif t["kind"] == "h3":
                entry(t["text"], t["page"], indent="      ")
        for kind, name in (("fig", "그 림 차 례"), ("tab", "표 차 례")):
            hwp.page_break()
            title(name)
            for t in toc:
                if t["kind"] == kind:
                    entry(t["text"], t["page"])
        hwp.page_break()
        title("초            록")
        for kind, val in abstract:
            if kind == "para":
                if val.startswith("주제어"):
                    hwp.para("justify", 160, before=6)
                    hwp.char(10)
                    wr.inline(val)
                else:
                    hwp.para("justify", 160)
                    hwp.char(10)
                    wr.inline("  " + val)
                hwp.enter()
        path = os.path.join(out, "front.hwp")
        hwp.save(path)
        hwp.save(os.path.join(out, "front.pdf"), "PDF")
        # 앞붙이 쪽(로마 숫자)을 찾아 자리표시를 바꾼다
        pages = find_pages(os.path.join(out, "front.pdf"), ["그 림 차 례", "표 차 례", "초            록"], at_top=True)
        for k, p in enumerate(pages):
            hwp.replace_all("@R%d@" % k, ROMAN[p - 1])
        log("앞붙이 쪽:", pages)
        log("저장 hwp", hwp.save(path))
    finally:
        hwp.quit()


def titles(src):
    t = open(os.path.join(src, "앞붙이.md"), encoding="utf-8").read()
    ko = re.search(r"- 국문: (.*)", t).group(1).strip()
    en = re.search(r"- 영문: (.*?)\n  - 표지", t, re.S).group(1)
    return ko, re.sub(r"\s+", " ", en).strip()


def stage_final(src, template, out, copy_to=None):
    ko, en = titles(src)
    hwp = Hwp(visible=False)
    try:
        log("양식 열기", hwp.h.Open(os.path.abspath(template), "HWP", "forceopen:true"))
        # 샘플 차례(문단 99)부터 끝까지 지운다
        hwp.h.SetPos(0, 99, 0)
        hwp.run("MoveSelDocEnd")
        hwp.run("Delete")
        # 가이드라인 쪽(문단 0~29)을 지운다. 문단 0의 구역 정의는 남는다
        hwp.h.SelectText(0, 0, 30, 0)
        hwp.run("Delete")
        for a, b in [("개인의 감정과 협업필터링을 이용한 개인화 영화 추천 시스템", ko),
                     ("A Personalized Movie Recommendation System Based on Personal Sentiment and Collaborative Filtering", en),
                     ("2023년도", "2026년도"), ("2023년 11월 26일", "2026년 ○월 ○일"),
                     ("김 선 호", "{이름}"), ("김선호", "{이름}"), ("박 두 순", "{지도교수}")]:
            log("  바꾸기", a[:12], hwp.replace_all(a, b))
        # 우리 제목이 샘플보다 길어 표지 글이 넘친다. 영문 제목부터 줄여 표지 4쪽에 맞춘다
        for target, size in ((en, 16), (en, 14), (ko, 19)):
            if hwp.h.PageCount <= 5:  # 4쪽 + 끝의 빈 쪽
                break
            hwp.run("MoveDocBegin")
            for _ in range(3):  # 겉표지 · 속표지 · 제출서. 찾기가 문서 처음으로 돌아가 끝없이 돌지 않게 세 번만
                if not hwp.find_next(target):
                    break
                hwp.set_height(size)
            hwp.run("Cancel")
            log("  제목 %dpt로: %d쪽" % (size, hwp.h.PageCount))
        # 인준서 뒤에 남는 빈 문단이 빈 쪽을 만든다. 표지 · 속표지 · 제출서 · 인준서 4쪽이 될 때까지 지운다
        for _ in range(5):
            if hwp.h.PageCount <= 4:
                break
            hwp.run("MoveDocEnd")
            hwp.run("DeleteBack")
        log("  표지 쪽 수", hwp.h.PageCount)
        hwp.run("MoveDocBegin")
        for k in range(4):
            hwp.hide_page_number()
            hwp.run("MovePageDown")
        hwp.run("MoveDocEnd")
        log("앞붙이 잇기", hwp.insert_file(os.path.join(out, "front.hwp")))
        hwp.run("MoveDocEnd")
        log("본문 잇기", hwp.insert_file(os.path.join(out, "body_fixed.hwpx")))
        path = os.path.join(out, "졸업논문.hwp")
        log("저장 hwp", hwp.save(path))
        log("저장 pdf", hwp.save(os.path.join(out, "졸업논문.pdf"), "PDF"))
    finally:
        hwp.quit()
    if copy_to:
        shutil.copyfile(path, copy_to)
        log("복사", copy_to)


def main():
    ap = argparse.ArgumentParser(description="논문 원고(.md)를 학과 양식 한글 파일로 조립한다")
    ap.add_argument("--template", help="학과 양식 한글 파일(final 단계에 필요). 저장소에는 없다")
    ap.add_argument("--src", default=SRC, help="원고 폴더 (기본: docs/연구/논문)")
    ap.add_argument("--out", default=OUT, help="중간 산출물 폴더 (기본: data/hwp_build)")
    ap.add_argument("--stages", default="body,front,final", help="body · fix · front · final 중 쉼표로")
    ap.add_argument("--copy-to", default=COPY_TO, help="완성본을 복사할 곳 (빈 값이면 복사하지 않는다)")
    a = ap.parse_args()
    stages = a.stages.split(",")
    if "final" in stages and not a.template:
        ap.error("final 단계에는 --template(학과 양식 한글 파일)이 필요합니다")
    src, out = os.path.abspath(a.src), os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    if "body" in stages:
        stage_body(src, out)
    if "fix" in stages:
        stage_fix(out)
    if "front" in stages:
        stage_front(src, out)
    if "final" in stages:
        stage_final(src, os.path.abspath(a.template), out, a.copy_to)


if __name__ == "__main__":
    main()
