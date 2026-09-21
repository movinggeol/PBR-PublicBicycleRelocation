"""웹 대시보드의 **모든 GET 주소를 실데이터로 순회**해 500·새는 값을 찾는다 (1.26.264).

## 왜 필요한가

`tests/test_webapp.py`는 **임시 DB**로 돈다(`conftest.py`가 `PBR_DB_PATH`를 강제한다).
그래서 실제 DB에만 있는 것 — 옛 산출물의 NULL 지표, 실험 라벨, 좌표 없는 대여소,
cp949 원천 파일 — 은 시험이 못 본다. 1.26.263에서 이 순회로 `duration=None` 링크와
미리보기 `NaN`을 찾았는데, 그때 스크립트는 임시 폴더에 있었다. 다음 점검이 같은 것을
다시 쓰지 않게 도구로 둔다.

## 무엇을 하나

- 화면·API·오류 경로·**DB의 실행 라벨마다** 필터 주소·작업지시서(실행·회차·차량별)·
  지도 열람·CSV 미리보기·내려받기를 전부 GET으로 부른다. **POST는 부르지 않는다** —
  실행을 띄우거나 실행 종류를 바꾸지 않는다. 외부 API를 부르는 `/orders/live`·
  `/api/weather`·`/api/forecast`도 뺀다(누를 때만 부른다는 규약).
- 응답마다 셋을 본다: **500 이상**, HTML 본문(스크립트 제외)에 새는 `nan`·`None`·
  `NaN`·`Undefined`, 처리 중 stdout에 찍힌 `[경고]`·`Traceback`.
- 문제가 하나라도 있으면 종료 코드 1이다 — CI나 커밋 전 확인에 그대로 쓴다.

## 어떻게 판정하나

`None`·`NaN`은 화면 글자로 찍혀서는 안 되는 값이다(결측은 `—`로 낸다는 규약,
docs/구현/DESIGN.md). 다만 파일 이름·실행 라벨에 그 글자가 든 경우가 있을 수 있으므로
**주소와 앞뒤 60자를 함께** 찍어 사람이 보고 가른다.

## 쓰는 법

    python tools/crawl_webapp.py                 # 실제 DB·산출물
    python tools/crawl_webapp.py --show 20       # 문제 주소를 20개까지 자세히
    PBR_DB_PATH=... python tools/crawl_webapp.py # 다른 DB로

⚠️ `starlette.testclient`(httpx)를 쓴다 — 서버를 띄우지 않고 앱을 직접 부른다. 시험
의존성이라 `.venv`에 있다.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import re
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import project_config  # noqa: F401  — 출력 인코딩을 UTF-8로 못 박는다(파일로 넘겨도 안 죽게)

# 언제나 부르는 주소. 오류 경로도 넣는다 — 404·422가 화면(HTML)으로 오는지 본다.
FIXED_URLS = [
    "/", "/run", "/kpi", "/vehicles", "/orders", "/maps", "/data", "/collect", "/guide",
    "/device", "/device?path=/kpi",
    "/api/pipeline-runs", "/api/kpi", "/api/vehicles", "/api/vehicles/assignments",
    "/api/stations", "/api/plans/ilp", "/api/plans/vrp", "/api/metrics", "/api/route-summary",
    "/없는페이지", "/files/없음.csv", "/preview/없음.csv", "/view/없음.html",
    "/vehicles?page=999", "/vehicles?page=0", "/vehicles?page=abc",
    "/kpi?run_label=없음", "/orders?run_label=없음", "/orders?run_label=없음&duration=_05_10",
    "/api/stations?run_label=없음", "/api/vehicles/assignments?vehicle_id=v99",
]

LEAK_PATTERNS = (r"\bnan\b", r"\bNone\b", r"\bNaN\b", r"Undefined")


def collect_urls(client, store, catalog, previews_per_group: int) -> list:
    """실데이터에서 주소 목록을 만든다. 라벨·회차·차량은 DB가 정한다."""
    urls = list(FIXED_URLS)

    runs = store.run_labels()
    labels = runs["run_label"].tolist() if not runs.empty else []
    for label in labels:
        q = quote(label)
        urls += [f"/kpi?run_label={q}", f"/vehicles?run_label={q}",
                 f"/api/stations?run_label={q}", f"/api/metrics?run_label={q}",
                 f"/api/plans/ilp?run_label={q}", f"/api/plans/vrp?run_label={q}",
                 f"/api/route-summary?run_label={q}",
                 f"/api/vehicles/assignments?run_label={q}"]

    targets = store.records(store.plan_targets())
    for t in targets:
        base = f"/orders?run_label={quote(t['run_label'])}&duration={quote(t['duration'])}"
        urls += [base, f"/orders?run_label={quote(t['run_label'])}"]
    if targets:
        # 차량별 보기는 첫 계획에서만 — 17대 × 39계획을 다 돌 이유가 없다.
        t = targets[0]
        base = f"/orders?run_label={quote(t['run_label'])}&duration={quote(t['duration'])}"
        html = client.get(base).text
        for name in sorted(set(re.findall(r"vehicle=([^\"&]+)", html)))[:3]:
            urls.append(f"{base}&vehicle={name}")
        urls.append(f"{base}&vehicle=없는차")

    for group in catalog.list_maps():
        for entry in group["entries"]:
            urls.append("/view/" + quote(entry["relpath"]))
    for group in catalog.list_csvs():
        for entry in group["entries"][:previews_per_group]:
            urls.append("/preview/" + quote(entry["relpath"]))
            urls.append("/files/" + quote(entry["relpath"]))

    seen, unique = set(), []
    for url in urls:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


def inspect(client, url: str) -> list:
    """주소 하나를 부르고 문제 목록을 돌려준다(없으면 빈 목록)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            response = client.get(url, headers={"accept": "text/html,*/*"})
        except Exception as err:                      # noqa: BLE001
            return [f"예외: {type(err).__name__}: {err}"[:200]]

    issues = []
    if response.status_code >= 500:
        issues.append(f"HTTP {response.status_code}")
    if "text/html" in response.headers.get("content-type", ""):
        body = re.sub(r"<script.*?</script>", "", response.text, flags=re.S)
        for pattern in LEAK_PATTERNS:
            for match in list(re.finditer(pattern, body))[:2]:
                start = match.start()
                context = body[max(0, start - 60):start + 40].replace("\n", " ")
                issues.append(f"{pattern}: …{context}…")
    noise = [line for line in buf.getvalue().splitlines()
             if "[경고]" in line or "Traceback" in line]
    if noise:
        issues.append("stdout: " + " | ".join(noise)[:200])
    return issues


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--show", type=int, default=50, help="자세히 찍을 문제 주소 수")
    parser.add_argument("--previews-per-group", type=int, default=3,
                        help="CSV 분류마다 미리보기·내려받기를 몇 파일까지 부를지")
    args = parser.parse_args(argv)

    from starlette.testclient import TestClient

    from webapp import app as webapp_app, catalog, store

    # 예외를 던지지 않고 500 응답으로 받는다 — 순회가 첫 500에서 멈추면 안 된다.
    client = TestClient(webapp_app.app, raise_server_exceptions=False)
    urls = collect_urls(client, store, catalog, args.previews_per_group)

    problems = []
    for url in urls:
        issues = inspect(client, url)
        if issues:
            problems.append((url, issues))

    print(f"훑은 주소 {len(urls)}개 · 문제 {len(problems)}개")
    for url, issues in problems[:args.show]:
        print(f"\n  {url}")
        for issue in issues:
            print(f"    - {issue[:300]}")
    if len(problems) > args.show:
        print(f"\n  … {len(problems) - args.show}개 더 (--show로 늘린다)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
