---
name: pbr-run
description: PBR 프로젝트의 앱을 실제로 띄우고 조작해 변경이 동작하는지 확인한다. 웹 대시보드 실행, 파이프라인 전체 실행, 브라우저로 화면 확인(스크린샷·좌표 측정·JS 상호작용), 테스트 데이터 정리까지. 화면을 고쳤거나 파이프라인을 건드린 뒤 "실제로 되는지" 봐야 할 때 사용.
---

# PBR 실행·확인 가이드

**코드가 아니라 앱을 본다.** 테스트가 통과해도 화면이 깨질 수 있고, 특히
이 저장소의 UI 수정은 **JS 상호작용·좌표 겹침**이라 HTML 문자열 검사로는
못 잡는다(실제로 겪었다 — 아래 '왜 브라우저가 필요한가').

> 코드 규약·함정은 **`pbr-pipeline` 스킬**에 있다. 이 스킬은 **실행과 확인**만 다룬다.

---

## 0. 준비 — 한 번만

```powershell
.\.venv\Scripts\python.exe -m pip install playwright
.\.venv\Scripts\python.exe -m playwright install chromium
```

⚠️ **`requirements.txt`에 넣지 마라.** 파이프라인 의존성이 아니라 **검증 도구**다.
없어도 파이프라인·테스트는 다 돈다.

---

## 1. 자동 테스트 (가장 먼저, 40초)

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

**783개 통과가 기준선이다.** 여기서 깨지면 아래로 내려가지 마라.

---

## 2. 웹 대시보드 띄우기

```bash
./.venv/Scripts/python.exe -m webapp > "$TEMP/webapp.log" 2>&1 &
sleep 6
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://127.0.0.1:8000/
```

`HTTP 200`이면 떴다. 안 뜨면 `$TEMP/webapp.log`를 봐라.

### ⚠️ 끌 때는 PowerShell로 끝내라

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*webapp*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

`pkill -f "python.*webapp"`은 **윈도우에서 안 죽는다** — 죽은 줄 알고 다음 실행을
띄우면 포트가 물려 있다.

---

## 3. 파이프라인 전체 실행 (합성 데이터)

**실데이터를 건드리지 않는다.** 라벨을 붙여 돌리고 끝나면 지운다.

```powershell
.\.venv\Scripts\python.exe tools\make_sample_data.py --now "테스트"
.\.venv\Scripts\python.exe run_pipeline.py --skip-api --skip-eda --skip-map `
  --now "테스트" --period "26년 03월" --raw-file "data/raw_data/합성_대여이력.csv"
```

**기준선: 전체 약 33초** (step1이 20초로 가장 오래 걸린다). 6분씩 걸리면
1.23.8의 넘파이 최적화가 되돌려진 것이다.

- `--skip-map`을 **빼면** TMAP을 실제로 부른다(유료·한도 있음). 지도나
  `road_leg` 정답표를 확인할 때만 빼라.
- `--skip-api`는 `extract_parking_lot`·`api_to_info`도 건너뛴다.

### 끝나면 반드시 정리

```powershell
# 파일
Get-ChildItem data -Recurse -Filter "*테스트*" | Remove-Item -Force
Remove-Item "data\raw_data\합성_대여이력.csv" -ErrorAction SilentlyContinue
```

```python
# DB — 라벨이 들어가는 테이블 전부
import db
with db.session() as c:
    for t in ('runs','vrp_plan','pick_drop','kpi_summary','vehicle_assignment',
              'ilp_plan','route_summary','metrics','rebalance_plan',
              'station_info','parking_lot','station_stock','road_leg'):
        try: c.execute(f"DELETE FROM {t} WHERE run_label='테스트'")
        except Exception: pass
    c.commit()
```

⚠️ **`find ... -delete`로 지운 뒤 DB를 안 지우면** 화면의 계획 목록에 유령
라벨이 남는다.

---

## 4. 브라우저로 화면 확인

### 왜 브라우저가 필요한가 — 실제로 놓쳤던 것

| 수정안 | HTML 검사로 보이는 것 | 실제 증상 |
| --- | --- | --- |
| 30 단추 겹침 | `.actions` 클래스가 있다 ✅ | **글자가 단추 위에 올라탐** (좌표 문제) |
| 31 팝업 겹침 | `.table-panel`이 있다 ✅ | **두 창이 완전히 포개짐** (JS 배치) |
| 32 툴팁 | `data-tip`이 있다 ✅ | 뜨는지·고정되는지는 **이벤트** |

**문자열 검사는 "있다"만 말한다. "보인다·동작한다"는 브라우저만 안다.**

### 골격

```python
from playwright.sync_api import sync_playwright
BASE = "http://127.0.0.1:8000"

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1400, "height": 1000})
    errs = []
    page.on("pageerror", lambda e: errs.append(str(e)))

    page.goto(f"{BASE}/kpi", wait_until="networkidle")
    # ... 조작 ...
    page.screenshot(path="shot.png", full_page=True)
    print("JS 오류:", len(errs))
    b.close()
```

⚠️ **스크린샷을 찍었으면 Read 도구로 실제로 봐라.** 빈 화면에서도
assert는 통과한다.

### 겹침은 좌표로 잰다 (문자열로는 못 잰다)

```python
r = page.evaluate("""() => {
    const a = document.querySelector('.actions').getBoundingClientRect();
    const b = document.querySelector('.section-lead').getBoundingClientRect();
    return {gap: b.top - a.bottom};
}""")
assert r["gap"] >= 0, f"겹친다: {r['gap']}px"
```

### 팝업 계단 배치

```python
page.locator("[data-table-open]").nth(0).click(); page.wait_for_timeout(350)
page.locator("[data-table-open]").nth(1).click(); page.wait_for_timeout(350)
panels = page.locator(".table-panel:not([hidden])")
a, c = panels.nth(0).bounding_box(), panels.nth(1).bounding_box()
assert abs(a["x"] - c["x"]) > 10 or abs(a["y"] - c["y"]) > 10
```

### 툴팁 4단계 (호버 → 떼기 → 고정 → 닫기)

```python
tip = page.locator(".tile .label .tip").first
box = page.locator("#tipbox")

tip.hover(); page.wait_for_timeout(300)
assert box.get_attribute("data-show") is not None      # 뜬다
page.mouse.move(5, 5); page.wait_for_timeout(300)
assert box.get_attribute("data-show") is None          # 떼면 사라진다
tip.hover(); tip.click(); page.wait_for_timeout(250)
assert box.get_attribute("data-pinned") is not None    # 클릭하면 고정
page.locator("#tipbox .tipclose").click()              # x로 닫는다
```

⚠️ **고정은 `.tip` 딱지에서만 된다.** 내비 링크에도 `data-tip`이 있어서,
아무 데서나 고정하면 **페이지 이동이 막힌다**(1.22.1에서 겪음).

### 400% 확대 — 낱말이 세로로 쪼개지는지 (1.26.115)

1280x1024를 400%로 확대하면 **CSS 폭이 320px**이다(WCAG 1.4.10이 보는 폭).
뷰포트를 그 값으로 두면 미디어 쿼리도 같은 폭을 봐서 실제 확대와 조건이 같다.

⚠️ **가로 스크롤 0건이 "읽힌다"는 뜻이 아니다.** 좁아지면 브라우저가 줄을
바꿔 넘침을 없애므로 `scrollWidth > clientWidth`는 통과한다. 그런데 두 글자
낱말이 한 글자씩 세로로 서면 넘치지 않아도 못 읽는다 — 내비 단추 라벨
"넓게"가 **여덟 화면 전부에서** 그러고 있었는데 숫자 검사를 다 빠져나갔다.

```python
page = b.new_page(viewport={"width": 320, "height": 256})   # = 1280 @ 400%
page.goto(BASE + path, wait_until="load")   # /maps는 networkidle이 안 끝난다
page.wait_for_timeout(900)                  # 지연 스크립트가 붙은 뒤에 잰다

# ⚠️ 줄 수를 **상자 높이로 짐작하지 마라.** 44px 터치 타깃을 전부 "2줄"로
#    잘못 센다. Range는 글자가 실제로 그려진 줄마다 사각형을 하나씩 준다.
bad = page.evaluate("""() => {
    const out = [];
    const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = w.nextNode())) {
        const t = n.textContent.trim();
        if (!t || t.length > 24) continue;
        const r = document.createRange();
        r.selectNodeContents(n);
        const rects = [...r.getClientRects()].filter(x => x.width && x.height);
        if (rects.length < 2) continue;                  // 한 줄이면 통과
        const words = t.split(/\\s+/).filter(Boolean);
        if (words.length >= rects.length) continue;      // 낱말마다 줄 = 정상
        out.push({text: t, lines: rects.length});        // 낱말 하나가 여러 줄
    }
    return out;
}""")
# ⚠️ 이 검사는 **정상인 것도 문다.** 걸린 것을 다 고치려 들지 마라.
print(path, bad)
```

지금 기준선(1.26.115 실측)은 **8개 화면 중 5개가 0건**이고, 나머지 셋에 걸리는
것은 **고치지 않기로 한 것들**이다. 새로 걸린 것이 있는지만 보면 된다.

| 걸리는 것 | 어디 | 왜 안 고치나 |
| --- | --- | --- |
| `--day-type` · `docs/분석/KPI.md` | `/guide` | 60~84px 열에 정말 안 들어간다. 그 표는 **가로로 스크롤되는 상자 안**이라 WCAG 1.4.10이 명시적으로 허용하는 예외다 |
| `했습니다(` · `참고.` | `/run` · `/vehicles` | 산문이 인라인 태그 경계에서 흐르는 것이라 **검사기의 오탐**이다. 낱말이 쪼개진 것이 아니다 |

**고칠 때**: 짧고 접히면 안 되는 라벨에는 `white-space: nowrap`과
`flex-shrink: 0`을 **함께** 건다(하나만 걸면 flex가 여전히 줄인다). 긴 코드
문자열에는 `word-break: break-all` 대신 **`overflow-wrap: anywhere`** 를 쓴다 —
`break-all`은 다음 줄에 통째로 들어갈 토큰까지 갈랐다.

### 저사양 렌더 — 스크립트인가 레이아웃인가 (1.26.115)

CPU를 느리게 두고 잰다. ⚠️ **한 번만 재면 잡음을 사실로 남긴다** — 노드
162개짜리 홈이 1971개짜리 지시서보다 느리게 나온 적이 있다. **각 5회**를 재고
중앙값을 쓴다.

```python
cdp = page.context.new_cdp_session(page)
cdp.send("Emulation.setCPUThrottlingRate", {"rate": 6})   # 저가 기기
cdp.send("Performance.enable")
page.goto(url, wait_until="load")          # /maps는 networkidle이 안 끝난다
page.wait_for_timeout(2000)
m = {x["name"]: x["value"] for x in cdp.send("Performance.getMetrics")["metrics"]}
# ScriptDuration / LayoutDuration / RecalcStyleDuration 으로 갈린다
```

**어디를 고칠지는 이 갈래가 정한다.** 실측(1.26.115)은 스크립트 13~79ms에
레이아웃 560~2136ms였다 — 코드가 아니라 CSS 쪽이었다. CPU 프로파일에
자바스크립트 자기시간이 0이고 전부 `(program)`이면 그것도 같은 신호다.

⚠️ **글꼴을 줄이겠다는 판단은 반드시 A/B로 확인하라.** 518KB 가변 글꼴을
의심해 막고 재니 **레이아웃이 오히려 34% 느려졌다**(시스템 대체 글꼴이 더
비싸다). 재 보지 않았으면 화면을 느리게 만들 뻔했다.

사용자가 실제로 겪는 값은 **누르고 나서 화면이 바뀌기까지**다. 총 로드 시간이
길어도 조금씩 나눠 쓰면 화면은 반응한다.

```python
ms = page.evaluate("""(sel) => new Promise(res => {
    const el = document.querySelector(sel);
    const t0 = performance.now();
    let done = false;
    const fin = () => { if (!done) { done = true; res(Math.round(performance.now() - t0)); } };
    requestAnimationFrame(() => requestAnimationFrame(fin));   // 실제 페인트까지
    el.click();
    setTimeout(fin, 6000);
})""", "#view-toggle")
```

기준(INP): 200ms 이하 좋음 · 500ms 넘으면 나쁨. 6배 느린 CPU에서 이 저장소는
42~173ms였다.

### 자동 스캐너(axe-core) — 사람이 안 본 것을 도구가 본다 (1.26.126)

위 400%·저사양·겹침 검사는 전부 **사람이 Playwright로 눈으로 본 것**이다.
축이 다른 확인 하나가 남는다 — WCAG 규칙 자체를 기계로 스캔하는 것.
`axe-core`는 `requirements.txt`에도 없고 설치도 필요 없다 — CDN에서 그때그때 받아
쓰고 버린다.

```python
import os
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
PAGES = ["/", "/run", "/kpi", "/vehicles", "/orders", "/maps", "/guide", "/api"]

# 고정 버전으로 받는다 — 최신을 그때그때 받으면 결과가 날짜마다 달라진다.
os.system(
    'curl -s -o axe.min.js '
    'https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.9.1/axe.min.js'
)
AXE_JS = open("axe.min.js", encoding="utf-8").read()

with sync_playwright() as p:
    b = p.chromium.launch()
    for vp in [{"width": 1400, "height": 1000}, {"width": 375, "height": 812}]:
        page = b.new_page(viewport=vp)
        page.add_init_script(AXE_JS)     # 페이지 로드마다 axe가 함께 실린다
        for path in PAGES:
            page.goto(BASE + path, wait_until="load")
            page.wait_for_timeout(400)
            res = page.evaluate("""async () => axe.run(document, {
                runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa']}
            })""")
            for v in res["violations"]:
                print(path, v["id"], len(v["nodes"]), v["help"])
        page.close()
    b.close()
```

⚠️ **`document-title`·`html-has-lang`·`meta-viewport`는 지도 HTML을 file://로
단독 열었을 때만 뜬다** — folium 산출물 자체엔 `<html lang>`이 없지만, `/maps`
안에서는 `<iframe>`으로 감싸여 있고 axe의 그 세 규칙은 **최상위 문서에만
적용된다.** iframe 안의 진짜 문제(`aria-command-name` 등)는 그대로 잡힌다 —
1.26.126에서 이 방식으로 지도 마커 28개의 이름 없음을 찾았다.

**실제로 이걸로 잡은 것 (1.26.126)**: `#tipbox`가 비어 있을 때도 이름 없는
툴팁으로 노출됨(8개 화면 전부) · `.muted`/`.hint`/`.empty` 안의 문장 속
링크가 색만으로 표시됨(5개 화면) · step3 지도의 출발·도착 핀에 이름이
없음(28개 노드). 셋 다 사람 눈에는 "그냥 그렇게 생긴 것"이라 넘어갔던
것들이다.

⚠️ **이 스캔은 스크린리더 실주행의 대체가 아니다.** axe는 속성(`aria-label`
등)의 **유무**만 본다 — 실제로 어떤 순서로 읽히는지, 같은 말이 중복으로
들리지는 않는지는 사람이 NVDA로 직접 들어야 안다(TODO.md, 아직 미완).

---

## 5. 무엇을 볼 것인가 — 화면별

| 경로 | 확인할 것 |
| --- | --- |
| `/` | **현황판이다**(1.26.0~). 마지막 계획 요약·실험 라벨 표시 |
| `/run` | **실행 폼**. 실행 이름 기본값이 오늘 날짜인가(`#f-now`), 시간대 체크박스 4개 |
| `/kpi` | 타일 5개에 툴팁, 표 팝업 계단 배치, 그래프 렌더 |
| `/vehicles` | 지표가 **어느 기간인지** 화면에 적혀 있나 |
| `/orders` | 작업지시서에 **차고지 복귀 행**이 있나(1.19.1), 군집 > 순서로 정렬됐나 |
| `/maps` | 지도가 뜨나 (⚠️ CDN 의존이라 인터넷 없으면 빈 화면) |

### 실시간 재고 대조는 자동 확인하지 마라

`/orders/live`는 **타슈 API를 실제로 부른다.** 자동 스크립트로 반복 호출하지
말고, 필요하면 사람이 한 번 눌러 본다.

---

## 6. 지도 확인 — TMAP 없이

지도(step3)는 TMAP 키가 필요하고 **호출마다 비용**이 든다. 도로 요청만 가짜로
막고 나머지 생성 경로는 그대로 태울 수 있다.

```python
def fake_chunked(start, end, pass_list, headers, url, **kw):
    xy = lambda d: (float(d.get("X") or d["viaX"]), float(d.get("Y") or d["viaY"]))
    pts = [xy(start)] + [xy(v) for v in (pass_list or [])] + [xy(end)]
    return [{"features": [
        {"geometry": {"type": "LineString", "coordinates": [list(pts[i]), list(pts[i+1])]},
         "properties": {"totalDistance": 1000, "totalTime": 300, "pointType": "GP"}}
        for i in range(len(pts) - 1)]}]

m.call_tmap_chunked = fake_chunked      # step3 모듈을 로드한 뒤 갈아끼운다
```

⚠️ **경유지는 `viaX`/`viaY`, 출발·도착은 `X`/`Y`다.** 형식이 다르다.
그리고 이렇게 만든 지도의 `road_leg`는 **정답표가 아니다** — 가짜 시간이다.

---

## 7. 함정 모음

- **윈도우 경로**: 파이썬에서 `/tmp/...`는 안 열린다. `os.environ["TEMP"]`를 써라.
- **콘솔 인코딩**: 한글 출력이 깨지면 `PYTHONIOENCODING=utf-8`을 앞에 붙여라.
  `—`·`✅` 한 글자에 `UnicodeEncodeError`로 죽는다.
- **파일명에 공백**: `find ... | while read`는 깨진다. `-print0`과
  `read -r -d ''`를 쓰거나 파이썬으로 처리해라.
- **`h2` 개수로 페이지를 판정하지 마라** — 작업지시서는 카드 안에 제목이 있어
  `h2`가 0이어도 정상이다. 스크린샷을 봐라.

---

## 8. 마무리 체크리스트

- [ ] `pytest` 통과 (783개)
- [ ] 서버를 **PowerShell로** 껐다
- [ ] 테스트 라벨 파일·DB 행을 **둘 다** 지웠다
- [ ] `git status`가 깨끗하다 (합성 데이터가 남지 않았나)
- [ ] 스크린샷을 **실제로 봤다**
