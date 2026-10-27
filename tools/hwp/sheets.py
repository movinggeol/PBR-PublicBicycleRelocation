"""PDF 쪽들을 몇 장씩 모아 한 그림으로 만든다(변환 결과 훑어보기용).

사용: python tools/hwp/sheets.py data/hwp_build/졸업논문.pdf data/hwp_build/sheet --per 8 --cols 4
"""
import argparse

import pymupdf as fitz
from PIL import Image


def main():
    ap = argparse.ArgumentParser(description="PDF 쪽들을 모아 훑어보기 그림(PNG)을 만든다")
    ap.add_argument("pdf")
    ap.add_argument("prefix", help="출력 접두. 접두_001.png처럼 쓴다")
    ap.add_argument("--per", type=int, default=8, help="한 장에 담을 쪽 수")
    ap.add_argument("--cols", type=int, default=4, help="열 수")
    ap.add_argument("--zoom", type=float, default=0.6, help="배율")
    a = ap.parse_args()
    doc = fitz.open(a.pdf)
    print("쪽 수", doc.page_count)
    for start in range(0, doc.page_count, a.per):
        imgs = []
        for i in range(start, min(start + a.per, doc.page_count)):
            pix = doc[i].get_pixmap(matrix=fitz.Matrix(a.zoom, a.zoom))
            imgs.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
        w, h = imgs[0].size
        rows = (len(imgs) + a.cols - 1) // a.cols
        sheet = Image.new("RGB", (w * a.cols + 4 * (a.cols - 1), h * rows + 4 * (rows - 1)), "gray")
        for k, im in enumerate(imgs):
            sheet.paste(im, ((k % a.cols) * (w + 4), (k // a.cols) * (h + 4)))
        out = "%s_%03d.png" % (a.prefix, start + 1)
        sheet.save(out)
        print(out)


if __name__ == "__main__":
    main()
