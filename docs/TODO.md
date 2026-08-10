# TODO — 해야 할 것 / 고쳐야 할 것

> 2026-08-07 기준. 전체 코드(step0~step4, test, run_pipeline.py, project_config.py)와
> 문서(README.md, docs/)를 전수 검토한 결과입니다.
> 우선순위: 🔴 파이프라인이 깨지는 문제 / 🟡 결과가 틀리거나 문서와 다른 문제 / 🟢 품질·유지보수 개선
>
> **2026-08-07 업데이트(버전 1.0.3)**: 🔴 P1 전체와 🟡 P2 대부분이 수정 완료되었습니다.
> 완료 항목은 아래 "✅ 완료" 섹션으로 이동했습니다.

---

## 🟡 P2. 남은 동작 문제

### 2-1. `vrp.py` — 남은 모델링 결정

- `VEHICLE_TOTAL = 21`은 선언만 되고 미사용 — 다차량 배정을 구현하거나 상수·문서에서 제거.
- ILP는 25km/h·Haversine, VRP는 30km/h — 기준 통일 여부는 운영 데이터로 결정 필요.
- VRP 작업시간은 "자전거 1대당 30초" 가정 — 현장 값으로 검증 필요.

---

## 🟢 P3. 품질·유지보수 개선

1. **폴더명 정리**: `step0 (raw데이터 처리)`와 `step0(전처리 및 EDA)`가 헷갈림(공백 유무만 다름).
   가능하면 `step0_collect`, `step0_eda`처럼 공백·괄호 없는 이름으로 변경
   (변경 시 run_pipeline.py·README 동시 수정).
2. **스키마 검증**: 단계 간 CSV의 필수 컬럼(station_id, lat, lon, rebal_qty, cluster …)을
   읽는 쪽에서 assert하는 얇은 검증 함수 추가.
3. **성능**: `adjust_module.try_move_node()`가 노드 이동 시도마다 `pick_drop.copy()` +
   전체 목적함수 재계산(내부에서 `compute_medoids` 반복) — 버전관리.txt에 기록된
   "5분 이상 소요" 문제의 원인. 이동 노드가 속한 두 군집만 재계산하는 증분 방식으로 개선.
4. **VRP 고도화**: 현행은 greedy 휴리스틱(단일 차량/클러스터). OR-Tools 등
   전용 VRP solver로 다차량·시간창·실도로 거리 반영.
5. **데이터 관리**: CSV → SQLite 이관 — **1~3단계 완료**(저장소 + 이중 기록 + 웹 API 전환).
   남은 단계는 [DB_PLAN.md](DB_PLAN.md) 참고
   (4. 대여이력 적재 → 5. 실행 이력 비교 화면 → CSV 기록·폴백 제거).
6. **EDA 시각화**: `month_graph`에 matplotlib 그래프 통합
   (`experiments/matplotlib_month_graph.py`의 한글 폰트 설정 참고).
7. **step1 매직 넘버**: 상위 50개 컷, `|rebal_qty| > 2`, target_cluster_size=7 등을 설정으로 추출.
8. **버전관리.txt → CHANGELOG.md 승격 검토.**
9. **README 주요 결과 수치의 산출 근거**(입력 데이터·실행 시점) 기록.
10. **webapp**: SSE 로그 스트리밍(현재 3초 meta-refresh),
    외부 공개 시 인증 (docs/WEBAPP.md 참고).
11. **테스트 확장**: 현재는 스모크 수준. 단계별 계산 로직(목표재고 공식, 군집 조정,
    VRP 적재 제약)의 단위 테스트와 CI(GitHub Actions) 연결.

---

## ✅ 완료 (2026-08-10, 버전 1.6.0) — 웹 API의 DB 전환 (3단계)

웹 API가 파일 대신 DB를 읽습니다. **"최신"의 기준이 파일 수정시각에서 `run_label`로** 바뀌었고,
과거 실행분 조회가 처음으로 가능해졌습니다.

| 항목 | 내용 |
| --- | --- |
| `webapp/store.py` | 데이터 조회 계층. DB 우선, 비어 있으면 CSV 폴백. `catalog.py`는 파일(지도·다운로드) 담당으로 역할 분리 |
| `?run_label=` · `?duration=` | 산출물 API가 과거 실행분을 지정해 조회 |
| `/api/pipeline-runs` | DB 실행 이력 목록 (`/api/runs/{id}`는 웹 작업 상태로 개념이 달라 이름 분리) |
| `/api/route-summary` | 클러스터별 이동거리·운행시간 API 신설 |
| 응답 봉투 | `run_label`·`source`(db\|csv)·`count`·`rows` — 어느 실행분을 어디서 읽었는지 표시 |
| 대시보드 | 첫 화면에 DB 실행 이력 표 추가 |
| 테스트 9개 추가 (전체 71개) | 최신 조회, 과거 실행 조회, 404 처리, 이력 목록. CSV 폴백은 별도 확인 |
| 테스트 격리 보강 | 라우트 호출만으로 실제 DB가 생성되는 문제를 `conftest.py` autouse fixture로 차단 |

## ✅ 완료 (2026-08-10, 버전 1.5.0) — SQLite 이중 기록 (2단계)

파이프라인을 돌리면 이제 CSV와 DB에 **동시에** 기록됩니다. 별도 적재 명령이 필요 없습니다.

| 항목 | 내용 |
| --- | --- |
| 이중 기록 | 9개 단계가 각자의 테이블에 기록 (`station_stock`·`parking_lot`·`station_info`·`net_demand`·`rebalance_plan`·`pick_drop`·`ilp_plan`·`vrp_plan`·`metrics`·`route_summary`) |
| `db.save_output()` | 단계용 헬퍼. **CSV가 정본이므로 DB 실패는 경고만 남기고 파이프라인을 멈추지 않는다** — 대신 테스트가 누락을 잡는다 |
| `db.session()` | 연결을 열고 스키마를 보장한 뒤 **닫는** 컨텍스트 매니저 (sqlite3의 `with`는 연결을 닫지 않아 자원이 샌다) |
| `db.ensure_run()` | 단계마다 아는 정보가 달라(`period`만/`duration`만) `COALESCE`로 누적 갱신. 먼저 기록된 값을 덮어쓰지 않는다 |
| `PBR_DB_PATH` | DB 경로 재정의 환경변수. **테스트가 실제 `data/bike_system.db`를 오염시키지 않도록** 임시 파일을 가리킨다 |
| 테스트 11개 추가 (전체 62개) | 테이블별 적재 9개 + CSV 대조 + `runs` 등록. 이중 기록이 하나라도 빠지면 실패한다 |

## ✅ 완료 (2026-08-10, 버전 1.4.0) — SQLite 저장소 1단계

CSV 파일명에 박혀 있던 `{now}` 라벨을 **`run_label` 컬럼**으로 옮기는 기반을 만들었습니다.
step 스크립트는 아직 CSV를 쓰며, 이관은 [DB_PLAN.md](DB_PLAN.md) 2단계에서 진행합니다.

| 항목 | 내용 |
| --- | --- |
| `db.py` | `data/bike_system.db` 연결(WAL), 11개 테이블 스키마, `save_frame`/`load_frame`/`latest_label`/`record_run`. 라벨 생략 시 최신 실행분 반환 |
| `tools/csv_to_db.py` | 기존 CSV 산출물을 DB로 적재. `--list`로 실행 이력 확인 |
| `tests/test_db.py` (16개) | 멱등 저장, 실행·시간대 격리, 한글 컬럼 변환, VRP 방문 순서 보존, 실행 간 비교 쿼리 |
| 통합 검증 (1개) | 파이프라인이 **실제로 만든 CSV**를 적재해 스키마 적합성과 행 수 일치 확인 |
| 설계 변경 | SQLAlchemy를 쓰기로 했다가 표준 sqlite3로 변경 — pandas 계층 코드가 동일해 이점이 없었고, 교체 지점을 `db.connect()` 하나로 격리 (DB_PLAN.md에 근거 기록) |

## ✅ 완료 (2026-08-10, 버전 1.3.0) — 스모크 테스트·샘플 데이터

수정할 때마다 수동으로 확인하던 것을 자동화했습니다. `python -m pytest`로 34개가 20초에 돕니다.

| 항목 | 내용 |
| --- | --- |
| `tools/make_sample_data.py` | 타슈 CSV 스키마를 재현한 합성 데이터 생성기. API 키·실데이터 없이 파이프라인과 대시보드를 돌려볼 수 있다 (TODO의 "샘플 데이터 부재" 해소) |
| `tests/test_webapp.py` (17개) | 라우트 렌더링·경로 탈출 차단·허용 확장자·404 처리. Starlette 시그니처 변경 같은 회귀를 즉시 잡는다 |
| `tests/test_pipeline.py` (17개) | 합성 데이터로 step0→step1→step2→step4 실제 실행. 산출물 존재·스키마, ILP 공급 제약, VRP 시간 컬럼, 개선량 부호까지 검증 |
| 실데이터 안전성 | 실행마다 고유 라벨(`smoketest-{pid}`)을 써서 실데이터와 파일명이 겹치지 않고, 종료 시 해당 라벨 파일만 정리 |
| `pytest.ini` | `testpaths = tests` — `experiments/pulp_test.py`가 pytest 기본 패턴에 걸려 수집되던 문제 차단 |
| `requirements-dev.txt` | pytest·httpx 분리 (런타임 의존성에 섞지 않음) |

## ✅ 완료 (2026-08-10, 버전 1.2.2) — 웹 대시보드 코드 리뷰 수정

| 항목 | 내용 |
| --- | --- |
| 🔴 작업 ID 충돌 | 초 단위 ID → 같은 초 재실행 시 기록·로그 덮어씀. 밀리초+일련번호로 수정 |
| 🟡 실행 중단 불가 | `cancel_job()` + 중단 버튼 추가. Windows `taskkill /T`로 프로세스 트리 종료 |
| 🟡 409 UX | 폼 요청에 JSON 오류 대신 안내 문구가 붙은 실행 화면 반환 |
| 🟡 미리보기 비효율 | 200행 보여주며 전체 `read_csv` → `nrows` + 개행 카운트 |
| 🟢 이력 무한 누적 | `runs.json` 최근 100건 유지 |
| 🟢 기타 | 조회 함수 락(RLock), Popen 실패 시 파일 누수 방지, favicon 204, meta refresh `<head>` 이동 |

안전 확인: CSV 미리보기 XSS(pandas `to_html` 이스케이프), 경로 탈출 차단은 기존대로 정상.

## ✅ 완료 (2026-08-10, 버전 1.2.1) — 실제 환경 검증

Python 3.14.7 / Windows 11에서 설치·구동을 검증하고, 그 과정에서 드러난 문제를 수정했습니다.

| 검증 항목 | 결과 |
| --- | --- |
| `pip install -r requirements.txt` | 구버전 고정 → **실패**(아래 수정) → 갱신 후 성공 |
| `py_compile` (27개 파일) | 통과 |
| 전체 모듈·step 스크립트 import (13개) | 통과 |
| `run_pipeline.py --dry-run` | 13단계 정상 출력, exit 0 |
| webapp 기동·라우트 | 구 시그니처로 500 → **수정 후 전부 정상** |
| 웹 폼 실행 → 로그 → 상태 추적 | 정상 (한글 인자 보존 확인) |
| **전체 파이프라인 E2E** (합성 데이터: 대여소 90곳·이력 14,000건) | step0~step2·step4 전부 exit 0 |
| ㄴ step0 (4단계) | 통과 — 주차대수 파싱·대여소 정보·순수요·재배치량 산출 |
| ㄴ step1 (클러스터링·지도) | 통과 — 39개 대여소 → 6클러스터, 4.5초, \|balance\| ≤ 2 |
| ㄴ step2 (ILP·VRP) | 통과 — CBC `Optimal`, VRP 계획 생성 |
| ㄴ step4 (지표·경로요약) | 통과 — 평균 개선률 75%, 총 192km / 최장 95분 |
| 산출물 있는 상태의 webapp | 지도 HTML·CSV 미리보기·GeoJSON API 전부 200 |
| API 키 필요 단계 (tashu_api, step3 TMAP) | ❌ 미검증 — 키 없음 |

수정한 내용:

- ~~requirements.txt의 2023년 고정 버전~~ → Python 3.14의 C 확장 ABI와 맞지 않아
  numpy/pandas/scipy/sklearn/folium/matplotlib/fastapi가 **전부 import 실패**했음.
  3.14 휠이 있는 버전으로 갱신하고 하한(`>=`) 고정으로 변경.
- ~~`scikit-learn-extra` (step1 K-Medoids)~~ → 아카이브된 프로젝트라 Python 3.12+ 휠이 없고
  소스 빌드에 MSVC C++ 빌드툴을 요구해 **설치 불가**. Rust 구현(FasterPAM)의
  `kmedoids` 패키지로 교체(API 호환).
- ~~webapp 전 페이지 500 오류~~ → Starlette 1.x가 구
  `TemplateResponse(name, {"request": ...})` 시그니처를 제거함.
  `TemplateResponse(request, name, {...})`로 6개 라우트 수정.

## ✅ 완료 (2026-08-07, 버전 1.2.0)

### P2·P3 — 이번 라운드에 수정된 항목

- ~~step3 depot 복귀 KeyError~~ → `action == 'return'` 행을 depot 정보로 처리.
  depot id·좌표·차량 용량은 `project_config`의 공통 상수(DEPOT_*, VEHICLE_CAPACITY)로 통일
  (기존: main.py `ST0000` vs vrp.py `ST0001` 불일치).
- ~~vrp.py pick/drop 노드 덮어쓰기~~ → 노드 키를 `(station_id, type)`으로 변경.
- ~~VRP 시간 계산 미구현~~ → 이동거리(km)·이동시간·작업시간(자전거 1대당 30초 가정)·누적시간
  컬럼을 VRP 결과에 추가. drop-only 잔여 시 depot 무한복귀 가능성도 방어 코드 추가.
- ~~TMAP 경유지 30개 제한 방어 없음~~ → `module.call_tmap_chunked()`가 구간 분할 호출,
  `merge_tmap_results()`가 경로·누적시간 병합.
- ~~step4 총 이동거리·운행시간 미구현~~ → `route_summary()`가 클러스터별
  방문수·처리대수·총이동거리·총이동/작업/소요시간을 집계해 `route_summary*.csv` 저장.
- ~~module.py 죽은 코드~~ → 주석 처리된 구버전 정규화 유틸 삭제.
- ~~테스트 정리~~ → `test/`·각 step의 `test.py`를 `experiments/`로 이동 (experiments/README.md 참고).
- ~~`.env.example` 추가~~ → API 키·PBR_* 환경변수 템플릿.
- ~~`adjust_module` docstring 오류~~ → 유클리드 → 맨해튼 거리로 정정.

## ✅ 완료 (2026-08-07, 버전 1.0.3)

### P1 — 파이프라인이 깨지는 문제 (전체 해소)

- ~~**`now` 값 3원화**~~ → 전 스크립트가 `project_config.get_runtime_config()` 사용.
  CLI 인자 → 환경변수(`PBR_NOW` 등) → 기본값 순으로 결정되며, 모든 단계가 같은 라벨 공유.
  경로도 `PROJECT_ROOT` 기준 절대경로가 되어 어느 cwd에서 실행해도 동작.
- ~~**import 부작용 체인**~~ → step0의 상호 import 제거, 실행 로직을
  `if __name__ == '__main__':` + `main()`으로 격리. 실행 순서는 run_pipeline.py가 제어.
- ~~**EDA.py 즉시 크래시**~~ → `pd.DateFrame` 오타 2곳, `month_graph(file_path)` 인자 오류 수정.
  원천 파일이 없으면 정상 종료(파이프라인 중단 방지).
- ~~**run_pipeline 인자 무시**~~ → 하위 스크립트가 project_config로 인자를 읽으므로
  `--now/--period/--duration/--raw-file` 전달이 실제로 동작. docstring 파일명도 수정.
  `ensure_output_dirs()`를 실행 시작 시 호출.
- ~~**`!calculate_target_qty.py` 파일명**~~ → `calculate_target_qty.py`로 개명.

### P2 — 함께 수정된 항목

- ~~`tashu_api.py` API 실패 시 NameError·거짓 성공 메시지~~ → 실패 시 `SystemExit`, timeout 추가.
- ~~`calculate_target_qty.py`의 `.loc[:, ...].clip(inplace=True)`~~ → 재할당 방식으로 수정
  (pandas 2.x에서 target_qty 상·하한이 무시될 위험 제거).
- ~~`raw_to_net.py` 파일명 슬라이싱 period 추출~~ → `config.period` 사용.
- ~~`vrp.py` depot 복귀 기록의 `'from_id': sid`~~ → `current_id`로 수정, 복귀 후 `current_id` 갱신 추가.
- ~~`vrp.py` 죽은 코드~~ → `manhattan_distance_km`, `drop_qty` get 잔재 제거,
  station_info 로드를 클러스터 루프 밖으로 이동.
- ~~`ilp.py` solver 전역 의존~~ → `run_ilp_plan(metrics, duration, solver)` 인자로 변경.
  `assume_initial_stock` 미사용 상수 제거.
- ~~`step3/main.py` 전역 의존~~ → `make_vrp_map(..., duration, headers, tmap_url)` 인자화,
  duration_list 루프 지원.
- ~~`imbalance.py` 산출물 미저장~~ → verification CSV 저장·지도 생성 활성화,
  `title=` → `tiles=` 오타 수정, `fillna` no-op 제거.
- ~~`st_visualization.py` 유효하지 않은 `title=` kwarg~~ → 제거.
- ~~`concat_1year_file.py` 주석 토글 실행~~ → `--concat`/`--preprocess` CLI 옵션화,
  파일 없으면 건너뜀.

### 검증 상태

- ✅ 2026-08-10 Python 3.14.7 환경에서 검증 완료 (아래 1.2.1 섹션 참고).
