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

**404개 통과가 기준선이다.** 여기서 깨지면 아래로 내려가지 마라.

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

**기준선: 전체 약 37초** (step1이 26초로 가장 오래 걸린다). 6분씩 걸리면
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

---

## 5. 무엇을 볼 것인가 — 화면별

| 경로 | 확인할 것 |
| --- | --- |
| `/` | 실행 이름 기본값이 **오늘 날짜**인가(`#f-now`), 시간대 체크박스 4개 |
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

- [ ] `pytest` 통과 (404개)
- [ ] 서버를 **PowerShell로** 껐다
- [ ] 테스트 라벨 파일·DB 행을 **둘 다** 지웠다
- [ ] `git status`가 깨끗하다 (합성 데이터가 남지 않았나)
- [ ] 스크린샷을 **실제로 봤다**
