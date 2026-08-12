# 웹 대시보드 (`webapp/`)

파이프라인 전 과정을 브라우저에서 실행하고 결과(지도 HTML·CSV)를 열람하는
**파이썬 단독** 웹서비스입니다. FastAPI + Jinja2 서버 렌더링이며 JS 빌드가 없습니다.

> 로컬 운영 도구입니다. 인증이 없으므로 외부 네트워크에 노출하지 마세요.

## 실행

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m webapp                       # http://127.0.0.1:8000
# 개발 중 자동 리로드가 필요하면:
uvicorn webapp.app:app --reload
```

> 검증 환경: Python 3.14.7 / Windows 11 (2026-08-10). 위 순서로 설치·구동을 확인했습니다.
> 설치 관련 주의사항은 [README](../README.md#설치)를 참고하세요.

## 화면 구성

| 경로 | 내용 |
| --- | --- |
| `/` | 실행 폼 + **DB 실행 이력(run_label)** + 작업 이력 + 최신 산출물 |
| `/runs/{id}` | 실행 상태·로그 (실행 중엔 3초마다 자동 갱신) + **실행 중단** 버튼 |
| `/kpi` | 실행별 성과 지표와 직전 실행 대비 증감 ([KPI.md](KPI.md)) |
| `/vehicles` | 차량별 누적 작업량·형평성·시간 예산 준수율·회차 배정 이력 ([FLEET.md](FLEET.md)) |
| `/maps` | step1·step3·step4가 생성한 folium 지도 목록 → iframe 열람(`/view/...`) 또는 새 창 |
| `/data` | 단계별 CSV 산출물 목록 → 미리보기(200행)·다운로드 |
| `/api/docs` | FastAPI 자동 API 문서 (Swagger UI) |

## JSON API

향후 JS 프론트엔드로 교체할 때 그대로 쓸 수 있도록 JSON/GeoJSON을 제공합니다.
**산출물 API는 SQLite를 조회**하며, `run_label`로 과거 실행분도 가져올 수 있습니다.

| 엔드포인트 | 내용 |
| --- | --- |
| `GET /api/stations` | Pick/Drop 후보 → GeoJSON FeatureCollection |
| `GET /api/plans/ilp` | ILP 이동 계획 |
| `GET /api/plans/vrp` | VRP 방문 계획 |
| `GET /api/metrics` | 불균형 개선 지표 |
| `GET /api/route-summary` | 클러스터별 총 이동거리·운행시간 |
| `GET /api/pipeline-runs` | DB에 기록된 실행 이력(run_label 목록) |
| `GET /api/kpi` | 실행별 성과 지표 (`run_label`·`duration`으로 필터) |
| `GET /api/vehicles` | 차량별 누적 작업량 (출동 횟수·처리 대수·거리·시간) |
| `GET /api/vehicles/assignments` | 회차별 차량 배정 이력 (`vehicle_id`·`run_label`로 필터) |
| `GET /api/runs/{id}` | 웹에서 띄운 **작업**의 상태 폴링 (위와 다른 개념) |

산출물 API는 `?run_label=...&duration=...` 쿼리를 받습니다. 생략하면 **최신 실행분**입니다.

```
GET /api/metrics                              # 최신 실행
GET /api/metrics?run_label=2026-05-21%2018    # 특정 실행
```

응답에는 어느 실행분인지·어디서 읽었는지가 함께 담깁니다.

```json
{"run_label": "2026-05-21 18", "duration": null, "source": "db", "count": 39, "rows": [...]}
```

### "최신"의 의미가 바뀌었습니다

| | 이전 | 현재 |
| --- | --- | --- |
| 기준 | 파일 **수정시각**이 가장 최근인 CSV | DB의 **`run_label`** 최대값 |
| 과거 실행 조회 | 불가능 | `?run_label=` 지정 |
| 취약점 | 파일 복사·재저장으로 순서가 뒤바뀜 | 없음 |

`source` 필드가 `"csv"`면 DB가 비어 있어 **폴백**으로 읽었다는 뜻입니다. 이중 기록
이전에 만들어진 산출물을 위한 전환기 장치이며, CSV 기록을 걷어낼 때 함께 제거합니다.
단, `run_label`을 지정한 요청은 폴백하지 않습니다(CSV에는 실행을 구분할 정보가 없음).

## 구조

```text
webapp/
├── app.py        # FastAPI 라우트 (페이지 + JSON API)
├── jobs.py       # run_pipeline.py를 subprocess로 실행, 상태·로그 추적
├── store.py      # 산출물 조회 계층 — DB 우선, 없으면 CSV 폴백
├── catalog.py    # data/pp_data 파일 스캔(지도·CSV 목록), 안전한 경로 해석
├── __main__.py   # python -m webapp 진입점
└── templates/    # base, index, run_detail, maps, view, data, preview
```

`store.py`는 **데이터**(지표·계획)를, `catalog.py`는 **파일**(지도 HTML·CSV 다운로드)을
담당합니다. 지도는 DB에 넣을 대상이 아니므로 `/maps`·`/data` 페이지는 계속 파일 기반입니다.

## 설계 결정

- **동시 실행 1개 제한**: 파이프라인 단계들이 같은 `data/pp_data` 파일을 읽고 쓰므로,
  실행 중일 때 새 실행 요청은 409로 거절합니다(폼 요청에는 JSON 대신 안내 문구가 붙은
  실행 화면을 다시 보여줍니다).
- **작업 ID는 밀리초 단위**: 초 단위 ID는 같은 초에 두 번 실행하면 충돌해 이전 기록·로그를
  덮어썼습니다. 밀리초 + 필요 시 일련번호로 유일성을 보장합니다.
- **실행 중단은 프로세스 트리 종료**: `run_pipeline.py`가 각 단계를 다시 subprocess로
  띄우므로 부모만 죽이면 진행 중인 단계가 고아로 남습니다. Windows에서는 `taskkill /T`로
  트리를 정리하고 상태를 `cancelled`로 기록합니다.
- **이력 상한**: `runs.json`은 최근 100건만 보관합니다(실행 중인 작업은 항상 보존).
- **백그라운드 실행**: step1 군집 조정이 수 분 걸리므로 요청-응답으로 돌리지 않고
  subprocess + 감시 스레드로 실행합니다. 로그는 `data/webapp/logs/run_{id}.log`,
  이력은 `data/webapp/runs.json` (둘 다 Git 미포함).
- **UTF-8 강제**: 하위 프로세스에 `PYTHONUTF8=1`을 줘서 Windows 콘솔 인코딩(cp949)으로
  로그가 깨지는 것을 방지합니다.
- **파일 서빙 제한**: `/files/`, `/view/`, `/preview/`는 `data/` 아래의
  `.html`/`.csv`만 허용합니다 (경로 탈출 검증 포함).
- **EDA 기본 생략**: 실행 폼에서 "EDA 생략"이 기본 체크되어 있습니다
  (1년치 병합 파일이 없는 환경에서도 매끄럽게 돌도록).
- **TemplateResponse 시그니처**: Starlette 1.x는 `TemplateResponse(request, name, context)`
  형식만 지원합니다(구 `TemplateResponse(name, {"request": ...})`는 제거됨).
  새 페이지 라우트를 추가할 때 이 형식을 지켜야 500 오류가 나지 않습니다.

## 검증 기록 (2026-08-10, Python 3.14.7)

| 항목 | 결과 |
| --- | --- |
| 서버 기동 | OK (uvicorn, 127.0.0.1:8000) |
| `/`, `/maps`, `/data`, `/api/docs` | 200 |
| 경로 탈출 차단 (`/files/../../etc/passwd`) | 404 (차단 확인) |
| 산출물 없을 때 `/api/stations` | 404 + 안내 메시지 |
| 폼 실행 → 백그라운드 subprocess → 로그 → 상태 추적 | OK (한글 인자 보존 확인) |
| 실패 감지 (`status=failed`, returncode 기록) | OK |
| 작업 ID 유일성 (연속 5회 생성) | OK — 밀리초+일련번호 |
| 실행 중 재실행 → 409 + 안내 페이지 | OK |
| 실행 중단 → `status=cancelled` | OK |
| 없는 작업 취소 404 / 끝난 작업 재취소 무해 | OK |
| CSV 미리보기 XSS (악성 대여소명) | OK — `&lt;script&gt;`로 이스케이프 |

## 남은 작업 / 확장 방향

- [x] ~~서버 기동·라우트 응답·작업 실행 검증~~ (2026-08-10 완료, 위 표 참고)
- [x] ~~실행 취소(프로세스 트리 kill) 버튼~~ (1.2.2)
- [x] ~~작업 ID 충돌·이력 무한 누적·409 UX·미리보기 전체 로드~~ (1.2.2)
- [ ] 실데이터로 파이프라인 전체 실행 검증 (API 키·원천 CSV 필요)
- [ ] 로그 스트리밍(SSE)으로 meta-refresh 대체
- [ ] CSV → SQLite 이관 후 대여소·지표 조회 API를 DB 기반으로 전환
- [ ] 필요해지면 프론트만 React + Leaflet로 교체 (JSON API는 그대로 사용)
