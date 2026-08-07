# 웹 대시보드 (`webapp/`)

파이프라인 전 과정을 브라우저에서 실행하고 결과(지도 HTML·CSV)를 열람하는
**파이썬 단독** 웹서비스입니다. FastAPI + Jinja2 서버 렌더링이며 JS 빌드가 없습니다.

> 로컬 운영 도구입니다. 인증이 없으므로 외부 네트워크에 노출하지 마세요.

## 실행

```powershell
pip install -r requirements.txt
python -m webapp                       # http://127.0.0.1:8000
# 개발 중 자동 리로드가 필요하면:
uvicorn webapp.app:app --reload
```

## 화면 구성

| 경로 | 내용 |
| --- | --- |
| `/` | 실행 폼(now·period·duration·raw_file·skip 옵션) + 실행 이력 + 최신 산출물 |
| `/runs/{id}` | 실행 상태·로그 (실행 중엔 3초마다 자동 갱신) |
| `/maps` | step1·step3·step4가 생성한 folium 지도 목록 → iframe 열람(`/view/...`) 또는 새 창 |
| `/data` | 단계별 CSV 산출물 목록 → 미리보기(200행)·다운로드 |
| `/api/docs` | FastAPI 자동 API 문서 (Swagger UI) |

## JSON API

향후 JS 프론트엔드로 교체할 때 그대로 쓸 수 있도록 JSON/GeoJSON을 제공합니다.

| 엔드포인트 | 내용 |
| --- | --- |
| `GET /api/stations` | 최신 Pick/Drop 후보 → GeoJSON FeatureCollection |
| `GET /api/plans/ilp` | 최신 ILP 이동 계획 (records) |
| `GET /api/plans/vrp` | 최신 VRP 방문 계획 (records) |
| `GET /api/metrics` | 최신 불균형 개선 지표 (records) |
| `GET /api/runs/{id}` | 실행 상태 폴링 |

"최신"은 해당 폴더에서 수정 시각이 가장 최근인 파일 기준입니다.

## 구조

```text
webapp/
├── app.py        # FastAPI 라우트 (페이지 + JSON API)
├── jobs.py       # run_pipeline.py를 subprocess로 실행, 상태·로그 추적
├── catalog.py    # data/pp_data 산출물 스캔, 안전한 경로 해석
├── __main__.py   # python -m webapp 진입점
└── templates/    # base, index, run_detail, maps, view, data, preview
```

## 설계 결정

- **동시 실행 1개 제한**: 파이프라인 단계들이 같은 `data/pp_data` 파일을 읽고 쓰므로,
  실행 중일 때 새 실행 요청은 409로 거절합니다.
- **백그라운드 실행**: step1 군집 조정이 수 분 걸리므로 요청-응답으로 돌리지 않고
  subprocess + 감시 스레드로 실행합니다. 로그는 `data/webapp/logs/run_{id}.log`,
  이력은 `data/webapp/runs.json` (둘 다 Git 미포함).
- **UTF-8 강제**: 하위 프로세스에 `PYTHONUTF8=1`을 줘서 Windows 콘솔 인코딩(cp949)으로
  로그가 깨지는 것을 방지합니다.
- **파일 서빙 제한**: `/files/`, `/view/`, `/preview/`는 `data/` 아래의
  `.html`/`.csv`만 허용합니다 (경로 탈출 검증 포함).
- **EDA 기본 생략**: 실행 폼에서 "EDA 생략"이 기본 체크되어 있습니다
  (1년치 병합 파일이 없는 환경에서도 매끄럽게 돌도록).

## 남은 작업 / 확장 방향

- [ ] 서버 시작 후 첫 실행 검증 (이 PC에 Python 미설치라 실행 테스트 못 함)
- [ ] 실행 취소(프로세스 kill) 버튼
- [ ] 로그 스트리밍(SSE)으로 meta-refresh 대체
- [ ] CSV → SQLite 이관 후 대여소·지표 조회 API를 DB 기반으로 전환
- [ ] 필요해지면 프론트만 React + Leaflet로 교체 (JSON API는 그대로 사용)
