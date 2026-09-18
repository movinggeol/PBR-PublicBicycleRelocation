"""논문 화면 캡처 — 원고 [그림 4-4]·[그림 4-6]~[그림 4-9]를 다시 찍는다 (2026-09-18).

**왜 도구로 만들었나.** 화면 캡처는 손으로 찍으면 다시 찍을 수 없다 — 어느 실행을,
어느 창 크기로, 무엇을 누른 상태로 찍었는지가 남지 않는다. 게이트 A 뒤나 화면을 고친
뒤 다시 찍어야 할 때 같은 조건이 나오도록 조건을 코드에 적어 둔다.

**무엇을 찍나** (실행은 모두 정본 스냅샷 `2026-08-11 real`의 05~10시):

  · 그림 4-4  경로 지도 — 가운데에 가장 가까운 방문 표식을 눌러 정보 창을 띄운 상태
  · 그림 4-6  실행 화면 — '새 실행' 폼 카드만
  · 그림 4-7  성과 지표 — 지표 타일과 '효과와 비용' 그래프까지
  · 그림 4-8  작업지시서 — 인쇄 매체로 흉내 낸 차량 V01 한 장
  · 그림 4-9  실시간 재고 대조 — V01. **타슈 공개 API를 한 번 부르므로 `--live`를 줄 때만**

⚠️ **정본 실행은 평일/휴일 분리(1.14.0, 2026-08-13)와 차고지 복귀(1.19.1) 이전의 산출물이다.**
그래서 지도·지시서의 계획은 3장 그림(평일로 다시 계산한 계획)과 군집 수가 다르고,
지시서에 "차고지 복귀 구간이 없습니다" 안내가 뜬다. 화면을 보이는 그림이라 계획을 다시
만들지 않았다 — 다시 돌리면 `station_info.stock`이 오늘 재고로 바뀌어 정본이 깨진다.
원고 캡션이 이 사실을 밝힌다.

⚠️ **4-9는 찍을 때마다 값이 다르다.** 계획(08-11 재고)을 **지금** 재고와 대조하므로
찍은 날짜를 원고 캡션에 적는다.

준비 (Playwright는 파이프라인 의존성이 아니라 검증 도구라 requirements.txt에 없다):
    .\\.venv\\Scripts\\python.exe -m pip install playwright
    .\\.venv\\Scripts\\python.exe -m playwright install chromium

실행 (대시보드를 먼저 띄운다 — `python -m webapp`):
    python tools/capture_thesis_screens.py              # 4-4 · 4-6 · 4-7 · 4-8
    python tools/capture_thesis_screens.py --live       # 4-9까지
    python tools/capture_thesis_screens.py --only 4-8
"""
from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/연구/초안/그림"
BASE = "http://127.0.0.1:8000"
LABEL = "2026-08-11 real"
DURATION = "_05_10"
VEHICLE = "V01"


def plan(base: str = BASE) -> list[dict]:
    """찍을 목록. **조건을 여기 한 곳에 적는다** — 창 크기·매체·누를 것."""
    q = urllib.parse.quote
    run = f"run_label={q(LABEL)}&duration={DURATION}"
    map_file = q(f"pp_data/VRP/visualization/vrp_map{DURATION} ({LABEL}).html")
    return [
        dict(key="4-4", name="그림4-4_경로지도", url=f"{base}/files/{map_file}",
             viewport=(1200, 900), action="popup", what="경로 지도와 정보 창"),
        dict(key="4-6", name="그림4-6_실행화면", url=f"{base}/run", viewport=(1280, 1000),
             element="#run", what="실행 화면의 '새 실행' 폼"),
        dict(key="4-7", name="그림4-7_성과지표", url=f"{base}/kpi?run_label={q(LABEL)}",
             viewport=(1280, 1000), clip_to="실행·회차별 지표", what="성과 지표 화면 윗부분"),
        dict(key="4-8", name="그림4-8_작업지시서", url=f"{base}/orders?{run}&vehicle={VEHICLE}",
             viewport=(900, 1000), media="print", what=f"작업지시서 인쇄 미리보기({VEHICLE})"),
        dict(key="4-9", name="그림4-9_실시간대조", url=f"{base}/orders/live?{run}&vehicle={VEHICLE}",
             viewport=(1280, 1000), live=True, clip_from="지금 재고와 대조", clip_to="footer",
             what=f"실시간 재고 대조({VEHICLE})"),
    ]


def server_up(base: str, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(base + "/", timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _shoot(browser, item: dict) -> Path:
    width, height = item["viewport"]
    page = browser.new_page(viewport={"width": width, "height": height})
    errors: list = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    if item.get("media"):
        page.emulate_media(media=item["media"])
    page.goto(item["url"], wait_until="networkidle", timeout=120_000)
    page.wait_for_timeout(1500)                    # 지도 타일·차트 그리기
    path = OUT / f"{item['name']}.png"

    if item.get("action") == "popup":
        markers = page.locator(".leaflet-marker-icon")
        if markers.count() == 0:
            raise RuntimeError("지도에 방문 표식이 없습니다 - 경로 지도가 비었습니다.")
        boxes = [(i, markers.nth(i).bounding_box()) for i in range(markers.count())]
        boxes = [(i, b) for i, b in boxes if b]
        i, _ = min(boxes, key=lambda t: (t[1]["x"] - width / 2) ** 2 + (t[1]["y"] - height / 2) ** 2)
        markers.nth(i).click()
        page.mouse.move(5, height - 5)            # 커서 풍선이 정보 창과 겹치지 않게
        page.wait_for_timeout(800)
        if page.locator(".leaflet-popup-content").count() == 0:
            raise RuntimeError("표식을 눌렀는데 정보 창이 뜨지 않았습니다.")
        page.screenshot(path=str(path))
    elif item.get("element"):
        # 위에 고정된 머리띠가 카드 제목을 덮는다(처음 찍은 4-6에서 '새 실행'이 가려졌다) —
        # 고정·붙박이 요소를 풀어 문서 흐름에 두고 찍는다.
        page.evaluate("""() => { for (const e of document.querySelectorAll('*')) {
            const p = getComputedStyle(e).position;
            if (p === 'sticky' || p === 'fixed') e.style.position = 'static'; } }""")
        page.locator(item["element"]).screenshot(path=str(path))
    elif item.get("clip_to"):
        # 제목 두 개 사이를 자른다. 제목의 부모 요소로 자르면 부모가 페이지 전체라
        # 잘리지 않았다(처음 찍은 4-7) — 그래서 **다음 제목의 위치**를 경계로 쓴다.
        box = page.evaluate(
            """([start, stop]) => {
                const top = (text) => {
                    if (text === 'footer') { const f = document.querySelector('footer');
                                             return f ? f.getBoundingClientRect().top + scrollY : null; }
                    const h = [...document.querySelectorAll('h2')]
                        .find(e => e.innerText.trim().startsWith(text));
                    return h ? h.getBoundingClientRect().top + scrollY : null;
                };
                return [start ? top(start) : 0, top(stop)];
            }""", [item.get("clip_from"), item["clip_to"]])
        if None in box:
            raise RuntimeError(f"자를 경계 제목을 찾지 못했습니다 {item.get('clip_from')!r}~"
                               f"{item['clip_to']!r} - 화면이 바뀌었습니다.")
        y0 = max(box[0] - 12, 0)
        page.screenshot(path=str(path), full_page=True,
                        clip={"x": 0, "y": y0, "width": width, "height": box[1] - 8 - y0})
    else:
        page.screenshot(path=str(path), full_page=True)
    page.close()
    if errors:
        print(f"    [경고] 페이지 JS 오류 {len(errors)}건: {errors[0][:120]}")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="논문 화면 캡처 (그림 4-4, 4-6~4-9)")
    parser.add_argument("--only", help="쉼표로 구분한 그림 번호 (예: 4-4,4-8)")
    parser.add_argument("--live", action="store_true",
                        help="4-9도 찍는다 - 타슈 공개 API를 한 번 부른다")
    parser.add_argument("--base", default=BASE, help="대시보드 주소")
    args, _ = parser.parse_known_args(argv)

    items = plan(args.base)
    keys = [i["key"] for i in items]
    wanted = [k.strip() for k in args.only.split(",")] if args.only else keys
    unknown = [k for k in wanted if k not in keys]
    if unknown:
        print(f"모르는 그림: {', '.join(unknown)} (있는 것: {', '.join(keys)})")
        return 1
    chosen = [i for i in items if i["key"] in wanted]
    skipped = [i for i in chosen if i.get("live") and not args.live]
    chosen = [i for i in chosen if i not in skipped]
    for i in skipped:
        print(f"  건너뜀 - 그림 {i['key']}({i['what']})은 외부 API를 부릅니다. --live를 주십시오.")
    if not chosen:
        return 0

    if not server_up(args.base):
        print(f"대시보드가 응답하지 않습니다: {args.base}\n"
              "  먼저 띄우십시오:  python -m webapp")
        return 1
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright가 없습니다 (검증 도구라 requirements.txt에 없습니다):\n"
              "  python -m pip install playwright\n"
              "  python -m playwright install chromium")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    print(f"화면을 찍습니다 → {OUT.relative_to(ROOT)}  (실행 '{LABEL}' · {DURATION})\n")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for item in chosen:
                path = _shoot(browser, item)
                print(f"  OK {path.relative_to(ROOT)}  - {item['what']}")
        finally:
            browser.close()
    print("\n[주의] 캡처한 화면에 개인정보·로컬 경로가 없는지 눈으로 확인하고 커밋하십시오.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
