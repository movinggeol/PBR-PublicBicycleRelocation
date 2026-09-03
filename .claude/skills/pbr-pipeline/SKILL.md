---
name: pbr-pipeline
description: PBR(공공자전거 재배치) 프로젝트에서 코드를 읽거나 수정하기 전에 반드시 읽어야 하는 프로젝트 규약·함정·작업 가이드. 이 저장소의 step0~step4 파이프라인, 데이터 경로, now/period/duration 규칙을 다루는 모든 작업에 사용.
---

# PBR (Public Bicycle Relocation) 작업 가이드

대전 타슈 공공자전거 재배치 프로젝트. step0~step4가 CSV 파일로 데이터를 주고받는
배치 파이프라인이다. **코드를 수정하기 전에 이 규약과 함정을 반드시 확인할 것.**

## 문서 위치 (수정 전 필독)

**목차는 `docs/README.md`다** — 문서가 30개를 넘어 분석·구현·연구·기록 네 폴더로 나눠 두었다. 어디에 무엇이 있는지는 거기서 본다.

| 문서 | 내용 |
| --- | --- |
| `docs/기록/RETROSPECTIVE.md` | 전체 조망·측정이 뒤집은 가설·설계 결정 (처음 오면 여기부터) |
| `docs/기록/ORIGINS.md` | 시작 기록 — 초기 시행착오와 현장 확인 (값의 출처가 궁금할 때) |
| `docs/분석/EXPERIMENTS.md` | `z`·학습 창·`γ`의 실측 근거 (**모델 파라미터를 건드리기 전 필독**) |
| `docs/분석/DEMAND_DISTRIBUTION.md` | 순수요 분포 진단·정정과 ML 방향 (**예측을 건드리기 전 필독**) |
| `docs/구현/TESTING.md` | 테스트 607개가 지키는 것·격리 장치·외부 API 수동 검증 (**테스트 추가 전 필독**) |
| `docs/기록/TODO.md` | 알려진 버그·개선 과제 전체 목록 (우선순위 🔴🟡🟢) |
| `docs/연구/THESIS.md` | 졸업작품·논문 준비 — 대조군·반복 실험·선행연구 (**논문용 실험을 추가하기 전 필독**) |
| `docs/분석/FORMULATION.md` | 기호·수식·제약 (**수식을 인용하거나 모델을 바꾸기 전 필독**) |
| `docs/연구/RELATED_WORK.md` | 관련 연구와 본 연구의 위치 |
| `docs/연구/LITERATURE.md` | 문헌 24편 분석·비교 (**선행연구를 인용하기 전 필독**) |
| `docs/구현/PROJECT_PIPELINE.md` | 파이프라인 전체 구조 |
| `docs/구현/steps/step*.md` | 단계별 입출력·문제점·작업 목록 |
| `docs/구현/WEBAPP.md` | 웹 대시보드(webapp/) 실행·구조·API |
| `docs/구현/DESIGN.md` | 화면 디자인 시스템 — 색 토큰·글꼴·내비·타일 (**템플릿을 건드리기 전 필독**) |
| `docs/구현/DB_SCHEMA.md` | ERD·테이블 20개 컬럼·스코프 규칙 (**DB를 건드리기 전 필독**) |
| `docs/구현/DB_PLAN.md` | SQLite 도입 결정·이관 단계·성능 측정 (CSV→DB 작업 시 필독) |
| `docs/구현/COLLECTOR.md` | 재고 시계열 수집 — 창 가드·스케줄·운영 (**수집기를 건드리기 전 필독**) |
| `docs/분석/WEATHER.md` | 날씨 원천·측정 결과 (**날씨를 건드리기 전 필독**) |
| `docs/분석/DECISIONS.md` | 기술 선택의 근거와 대가 (**방법을 바꾸기 전 필독**) |
| `docs/GLOSSARY.md` | 용어집 — 한국어 용어 ↔ 코드 이름, 헷갈리기 쉬운 짝 |
| `docs/분석/KPI.md` | 성과 지표 체계 설계 (지표를 건드리기 전 필독) |
| `docs/구현/FLEET.md` | 차량 로테이션·형평성 (차량/클러스터 수를 건드리기 전 필독) |
| `docs/기록/버전관리.md` | 버전 이력, 수정 이유 기록 |

## 파이프라인 구조 (실행 순서 = 데이터 의존 순서)

```text
step0_collect  : tashu_api → extract_parking_lot → api_to_info → raw_to_net → calculate_target_qty
step0_eda    : concat_1year_file, EDA (선택적)
step1                   : top_st_clustering → st_visualization
step2                   : ilp → vrp
step3                   : main (TMAP 지도)
step4                   : imbalance
```

각 단계는 `data/pp_data/…/<이름>{duration} ({now}).csv` 형식의 파일로 통신한다.
**앞 단계의 출력 파일명과 뒤 단계의 입력 파일명이 정확히 일치해야 한다.**

## 공통 설정 규칙 (버전 1.0.3부터)

- 모든 step 스크립트는 루트의 `project_config.get_runtime_config()`에서
  `now`/`period`/`duration`/`raw_file`/`day_type`을 읽는다. 우선순위: CLI 인자(`--now` 등) →
  환경변수(`PBR_NOW` 등) → 기본값.
- **평일과 휴일은 절대 섞지 마라.** `--day-type weekday|holiday|auto`(기본 auto)로
  한쪽만 골라 통계를 낸다. **휴일 = 주말 ∪ 공휴일**이고 공휴일은 `holidays` 패키지를
  따른다. 실데이터에서 `_10_15`는 둘을 묶으면 57곳이 상쇄돼 사라졌고(휴일이 평일보다
  바쁘다 — 295곳 vs 189곳), 공휴일을 평일에서 빼자 연휴 낀 달의 작업 대상이 20~50%
  늘었다. `all` 같은 선택지를 만들지 마라. 거르는 것은 `select_day_type()` 하나,
  판정은 `is_holiday()`/`holiday_mask()` 하나를 써라.
- `auto`는 `--target-date`(기본 오늘)를 달력으로 판정한다. `RuntimeConfig.day_type`은
  **항상 해석된 값**이라 하위 코드가 'auto'를 볼 일이 없다.
- **`now`는 실행 시각이 아니라 파이프라인 실행을 묶는 라벨이다.** `datetime.now()`나
  하드코딩 값을 코드에 넣지 마라 — 단계 간 파일명이 어긋난다.
- 각 스크립트는 독립 실행되며(상호 import 없음) 실행 로직은
  `if __name__ == '__main__':` + `main()` 아래에 둔다. 순서는 run_pipeline.py가 제어한다.
- step 폴더의 스크립트는 상단에서 `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))`
  후 project_config를 import한다 — 새 스크립트를 만들 때 같은 패턴을 따르라.
- 데이터 경로는 `PROJECT_ROOT` 기준으로 만든다: `str(PROJECT_ROOT / "data/pp_data/...")`.
- 산출물을 쓰는 스크립트는 저장 전에 `ensure_output_dirs()`를 호출한다.

## ⚠️ 함정 (반드시 확인)

1. **step 폴더는 ASCII 이름이다** (1.18.3에서 정리했다 — 예전 이름은
   `step0 (raw데이터 처리)`처럼 공백·괄호·한글이 있어 셸 인용이 필요했다).
   `step0_collect`(수집·전처리)와 `step0_eda`(이력 병합·EDA)는 **별개 폴더**다.
   **파일명은 아직 정리 전이다** — `step1_cluster/top_st_clustering.py`는 숫자로
   시작해 일반 import가 안 되므로 `importlib`으로 불러야 한다.
2. **`data/`와 `*.csv`는 .gitignore로 전부 제외된다.** 데이터 파일은 커밋할 수 없고,
   로컬에 원천 CSV가 있어야만 파이프라인이 돈다. 데이터가 없으면 코드 실행 검증은
   구문 수준(`python -m py_compile`)까지만 가능하다.
3. **API 키는 `.env`** (`TASHU_API_KEY`=타슈, `API_KEY`=TMAP). 절대 커밋 금지.
   템플릿은 `.env.example`.
4. **지도 배경 타일은 `project_config.MAP_TILES` 하나다** — 군집·경로·재고 세 지도가
   같은 값을 써야 한다. 스크립트에 타일 이름을 박으면 테스트가 실패한다. 기본값은
   folium 기본값과 같은 `OpenStreetMap`이고, 한동안 CartoDB를 쓴 것은 옛 folium이
   OSM 서브도메인 URL을 써서 경고를 받았기 때문이다(1.19.4에서 되돌렸다).
5. **재고 시계열은 `stock_history`가 정본이고 `station_stock`이 아니다.**
   후자는 PK가 `(run_label, station_id)`라 실행 1건당 스냅샷 1장이고, `run_label`에
   시각을 넣으면 `latest_label()`(**사전순** MAX)이 파이프라인 실행을 밀어낸다.
   수집기(`tools/collect_stock.py`)는 `runs`·`station_stock`을 건드리지 않는다 —
   테스트가 지킨다. **휴일을 거르는 것은 스케줄러가 아니라 스크립트의 창 가드다**
   (작업 스케줄러는 요일만 안다). 자세한 것은 docs/구현/COLLECTOR.md.
   **수집기의 요일 옵션은 세 갈래다** — 없음(평일만) / `--include-holidays`
   (평일+휴일) / `--holidays-only`(휴일만, B PC용). 셋 다 **창 가드는 지킨다**
   (`--force`만 창까지 푼다). 이것은 **관측 범위**지 분석의 `--day-type`이
   아니다 — 평일·휴일을 섞어 통계 내지 말라는 규약은 그대로고, 거를 때는
   `db.load_stock_history(day_type=...)`을 쓴다.
   `--holidays-only`에서 **트리거가 7일인 것이 의도다**: 토·일만 걸면
   공휴일(어린이날=화)을 놓친다. 7일을 깨우고 평일은 스크립트가 거른다.
   ⚠️ **저장 규칙이 두 곳에서 정반대다.** 수집기 `save_stock_snapshot()`은
   `INSERT OR REPLACE`(재실행 멱등 — 나중 값이 이긴다), 병합기
   `tools/merge_stock.py`는 `INSERT OR IGNORE`(**먼저 수집한 것이 이긴다** —
   다른 PC 값이 A PC 값을 덮으면 안 된다). 한쪽을 고치며 다른 쪽 규칙을 옮겨
   붙이지 마라 — 회귀 테스트가 고정한다(COLLECTOR.md 11장).
6. **depot·차량 상수는 project_config에 있다**(DEPOT_ID/LAT/LON/NAME, VEHICLE_CAPACITY,
   FLEET_SIZE, VEHICLES_PER_ROUND). step2·step3에서 별도 하드코딩하지 마라.
   **클러스터 1개 = 차량 1대**이므로 step1의 K는 `VEHICLES_PER_ROUND`를 넘을 수 없다.
   두 대수 모두 **실행마다 바뀐다** — 웹 실행 폼/`--fleet-size`/`--vehicles-per-round`가
   `PBR_FLEET_SIZE`·`PBR_VEHICLES_PER_ROUND`로 전달되고 후자는 전자로 잘린다.
   차량 배정·로테이션은 `db.assign_vehicles()`가 담당한다 (docs/구현/FLEET.md).
   마스터를 대수에 맞추는 것은
   `db.sync_fleet()`(실행 경로)뿐이고 **조회 경로는 `db.ensure_fleet()`를 쓴다** —
   바꾸면 화면을 여는 것만으로 직전 실행의 보유 대수가 되돌아간다.
   하루 여러 회차를 돌리므로 **한쪽 후보만 있는 시간대는 크래시가 아니라 건너뛴다** —
   step1/step2/step4의 건너뛰기 가드를 지우지 마라.
7. **일회성 스크립트는 `experiments/<분류>/`에 둔다** — step 폴더나 루트에 test.py를
   만들지 마라. 분류는 params·baseline·structure·diagnostic·learning 다섯이고,
   스크립트 상단의 `sys.path.insert`는 **`parents[2]`**(저장소 루트)를 가리켜야 한다.
   루트에 있던 `db_test.py`는 1.20.7에서 `tools/show_schema.py`로 옮겼다 —
   **여러 번 쓰는 도구는 `tools/`, 한 번 재고 마는 것은 `experiments/`다.**
8. **가상환경은 `.venv`** (검증 환경: Python 3.14.7). 명령은 `.\.venv\Scripts\python.exe ...`로
   실행하라 — 시스템 `python`에는 의존성이 없다.
9. **K-Medoids는 `kmedoids` 패키지**(FasterPAM)다. `sklearn_extra`는 아카이브되어
   Python 3.12+에서 설치되지 않으므로 되돌리지 마라.
10. **Starlette 1.x 템플릿 응답은 `TemplateResponse(request, name, {...})`** 형식만 동작한다.
   구 형식(`TemplateResponse(name, {"request": ...})`)으로 쓰면 500 오류가 난다.
11. **requirements.txt는 하한(`>=`) 고정**을 유지하라. 상한을 걸면 새 Python 버전에서
   휠이 없어 설치가 통째로 깨진다(1.2.1에서 실제로 겪음).
12. **`greedy_route()`는 넘긴 노드 dict를 소모한다** (그쪽 docstring: *"호출 측에서
   소모된다"*). **값 dict까지 복사해서** 넘겨라 — `{k: dict(v) for k, v in nodes.items()}`.
   껍데기만 복사(`dict(nodes)`)하면 안쪽이 공유돼, 한 번 푼 뒤 노드가 비고
   **다음 호출이 빈 경로를 받는다.** 🔴 **예외가 나지 않아서** 표는 그럴듯하게
   찍힌다 — 1.26.97에서 *"이동거리 −100%"* 라는 표를 만들고서야 알아챘다.
13. **집행량은 `min(pick, drop)`으로 세라.** `pick`만 세면 **실었지만 내리지 못한**
   자전거가 "옮겼다"로 잡힌다. 군집을 쪼개 pick↔drop 짝이 깨지면 그 차는
   자전거를 싣고 **갈 곳이 없어 그대로 들고 돌아오는데**, pick 기준으로는
   9+8+0 = 17이 원본 17과 같아 **검사를 통과한다**(1.26.97에서 실제로 통과했고,
   세 조각 다 내린 곳이 없었다). 참고: `experiments/structure/budget_split.py`의
   `moved_bikes()`.
14. **적재 용량 10대는 상한이지 통상값이 아니다.** 관제센터 유선 문의(2026-05-15)에서
   *"통상 7대, 많이 실어야 10대"* 라고 답했다([ORIGINS.md](../../../docs/기록/ORIGINS.md) 4장).
   문서·논문에 쓸 때 **상한임을 밝혀라** — 평균처럼 읽으면 계획이 낙관적으로 보인다.

## 실행·확인은 `pbr-run` 스킬에 있다

앱을 실제로 띄우고 조작해 확인하는 방법(웹 대시보드·브라우저 확인·테스트 데이터
정리)은 **`pbr-run` 스킬**로 따로 뺐다. 화면을 고쳤거나 파이프라인을 건드린 뒤
*"실제로 되는지"* 봐야 할 때 그쪽을 읽어라.

## 실행 방법

```powershell
python run_pipeline.py --dry-run          # 실행 목록 확인 (파일 존재 검증)
python run_pipeline.py                    # 전체 실행 (기본 설정)
python run_pipeline.py --skip-api --skip-eda  # 수집·EDA 생략
python run_pipeline.py --skip-map             # TMAP 지도 생략 (키 없는 환경)
python run_pipeline.py --now "2026-05-21 18" --period "25년 11월" --duration "_05_10,_10_15"
python run_pipeline.py --fleet-size 15 --vehicles-per-round 6   # 차량 대수 (기본 21 / 21)
python run_pipeline.py --day-type holiday --now "260813 휴일"   # 휴일 계획 (기본 auto)
python run_pipeline.py --target-date 2026-09-25                # 그날로 자동 판정
python run_pipeline.py --warmup-period "26년 03월"             # 계절 보정 (기본 14일)
python tools/rebuild_net_demand.py            # 전 기간 순수요 재계산(휴일 포함)
python -m webapp                          # 웹 대시보드 (http://127.0.0.1:8000)
.\scripts\collector.ps1 install           # 재고 시계열 수집 시작 (평일 07~22시, 10분)
.\scripts\collector.ps1 install -HolidaysOnly  # 두 번째 PC — 휴일만 (COLLECTOR.md 11장)
python tools/collect_stock.py --status    # 수집 현황
python tools/merge_stock.py <경로> --dry-run  # 다른 PC 수집분 합치기 (COLLECTOR.md 11장)
.\scripts\road_collector.ps1 install      # TMAP 실도로 소요시간 수집 (매 평일 03:30)
python tools/collect_road_time.py --status    # 고정 패널 수집 현황
python experiments/params/road_time_model.py  # 이동시간 모형 재추정 (EXPERIMENTS.md 9장)
```

## 웹 대시보드 (webapp/)

- FastAPI + Jinja2 파이썬 단독. `jobs.py`가 run_pipeline.py를 subprocess로 실행하고
  (동시 1개 제한), `store.py`가 산출물 **데이터**를, `catalog.py`가 **파일**(지도 HTML·
  CSV 다운로드)을 담당한다. 새 데이터 API는 `store.load()`를 써라.
- **산출물 API는 DB를 읽는다**(`?run_label=`로 과거 실행분 조회). DB가 비면 CSV로
  폴백하는데, 이건 전환기 장치이니 새 기능이 여기 의존하게 만들지 마라.
- 파일 서빙은 `data/` 아래 `.html`/`.csv`로 제한된다 — 새 라우트를 추가할 때
  `catalog.safe_resolve()`를 우회하지 마라.
- 파이프라인 로직을 웹 요청 안에서 직접 실행하지 마라(수 분 소요) —
  반드시 jobs.start_job() 경유.
- **타슈 API는 루트 `tashu.py` 하나로 부른다** — step0 수집과 웹의 실시간 재고
  대조가 같은 클라이언트를 쓴다. 각자 호출하면 컬럼 규약(`x_pos`가 위도)이 갈린다.
  웹에서는 `/orders/live`처럼 **사용자가 누를 때만** 부른다.
- 작업지시서·재고 대조 판정은 `webapp/orders.py`의 순수 계산이다. **판정만 하고
  계획을 바꾸지 않는다** — 자동 보정을 넣으면 기사가 든 종이와 화면이 어긋난다.
- `/api/runs/{id}`는 **웹 작업 상태**, `/api/pipeline-runs`는 **DB 실행 이력**이다.
  이름이 비슷하니 헷갈리지 마라.
- **화면에 설정값을 하드코딩하지 마라.** 차량 대수·적재 용량·속도·시간 예산은
  실행마다 바뀐다. 라우트에서 `project_config` 값을 넘겨 쓰고, 넘길 수 없는 자리
  (예: base.html의 내비 풍선)라면 숫자를 아예 빼라. `tests/test_guide.py`가 지킨다.
- **폼·안내의 입력 예시는 실제로 통하는 값이어야 한다.** 시간대는 맨 앞 밑줄까지가
  값이고(`_10_15`), 기간은 `25년 11월` 표기다. 예시를 잘못 적으면 그대로 입력한
  사용자가 step4 `duration_hours()`에서 크래시를 본다(1.17.3에서 실제로 있었다).
- **DB에 NULL로 남는 지표가 있다**(`db.KPI_FIELDS`, 계산 못 하면 빠진다).
  템플릿에서 `round`·`int`에 바로 넣지 마라 — 화면 전체가 500이 된다.

## 코드 규약

- 주석·출력 메시지·문서는 한국어를 사용한다.
- 컬럼명 규약: `station_id`, `station_name`, `lat`, `lon`, `stock`, `parking_lot`,
  `rebal_qty`(양수=Drop 필요, 음수=Pick 가능), `target_qty`, `cluster`.
- 좌표: TASHU API는 `x_pos`=위도, `y_pos`=경도 (뒤집혀 있음 — 변환 코드 존재).
- 운영 상수(변경 시 근거 기록): 차량 적재 용량 10대(대전교통공사 확인),
  차량 속도 25km/h(`VEHICLE_SPEED_KMPH` — **ILP·VRP가 반드시 같은 값**),
  depot=타슈 관제센터(ST0001). 한 회차에 차량 1대 = 클러스터 1개 + depot 복귀.
- **모델 파라미터는 실측 실험으로 정해져 있다** — `TARGET_Z = 1.99`,
  `CLUSTER_GAMMA = 3000`. 근거는 `docs/분석/EXPERIMENTS.md`이고 재현 스크립트는
  `experiments/`에 있다. **바꾸려면 같은 방식으로 재실험할 것.** 특히:
  - `γ`는 **비단조**다(γ=2000이 γ=1000보다 나빴다). 두 점 사이를 보간하면 안 된다.
  - **한 회차만 보고 판단하지 말 것.** 3회차 전부로 재확인한다 (과거에 틀린 적 있음).
  - `z`와 `γ`는 연동된다 — z를 올리면 작업량이 늘어 시간 예산을 압박한다.
  - **개선률·목표 도달률은 `target_qty`를 분모로 삼으므로 `z` 실험의 판정 기준이
    될 수 없다.** 결품 시간으로 비교할 것.
  - **z가 필요한 이유는 '꼬리가 두꺼워서'가 아니다** — 대여소별로 보면 거의
    정규분포다. 진짜 원인은 지난달 통계로 이번 달을 맞히는 **추정 오차**다
    (docs/분석/DEMAND_DISTRIBUTION.md). 분포를 고치는 처방은 헛다리다.
  - 🔴 **파라미터가 후보 집합을 바꾸면 중립 모집단(전체 대여소)으로도 재라.**
    결품은 *집합 위의 평균*이라(`합계 / (대여소수 × 일수)`) 분모가 흔들리면
    **서로 다른 자로 잰 값**이 된다. 이 저장소가 **세 번** 걸렸다(1.26.56 대조군
    B2 · 1.26.64 상한 격자 · 1.26.65 `z` 격자). 게다가 자기 후보를 모집단으로
    잡으면 **자가 승자를 고른다** — 각 모집단은 자기를 정의한 파라미터를 뽑는다
    (EXPERIMENTS 17·18장).
    - **판정법**: 재배치 **전** 결품이 파라미터에 따라 움직이면 그 표는 무효다.
      아무것도 안 한 상태의 값이 파라미터로 달라질 수는 없다.
    - `bc.stockout_population(net, population, duration)`으로 **분모를 찍어라**.
      격자를 돌 때 이 값이 파라미터를 따라 움직이면 그 자리에서 알아챈다.
  - 🔴 **사전 등록 기준을 결과 보고 우회하지 마라.** 채택 조건은 **자료를 보기
    전에** 정하고, 자료가 모자라면 **대기가 정답이다.** 예: 이동시간 계수는
    *"표본 밖 MAE −20% + 날짜별 계수 변동계수 15% 미만, 10일 이상"* 인데
    2일치로 재면 변동계수는 **정의상 계산되지 않는다**(EXPERIMENTS 9장).
    하루치로 상수를 갈아끼우면 11장(씨앗 하나로 결론이 뒤집힌 일)을 반복한다.
    **씨앗도 여럿 써라** — 파라미터 격자를 씨앗 1회 결과로 표에 싣지 마라.
- **계절 보정(warmup)이 기본으로 켜져 있다** — 계획 대상 달 첫 14일 실적으로 도시
  전체 배율을 구해 mu·sigma에 곱한다(`--warmup-days 0`으로 끔). 배율을 대여소별로
  추정하지 마라 — 며칠치로 나누면 잡음만 커진다.
- **수요 예측을 바꾸려면 `experiments/structure/quantile_model_eval.py`의 판정을 통과해야 한다** —
  작업 대상만·평일/휴일 따로·표본 밖·베이스라인 초과.
- **분위수 모델은 꺼져 있다** — 1.15.2에서 채택했다가 1.15.3에서 되돌렸다.
  그 '승리'는 계절 배율이 과대추정된 베이스라인과 겨룬 결과였다
  (docs/분석/DEMAND_DISTRIBUTION.md 5장). 코드는 다음 시도를 위한 하네스로 남겨 뒀다.
  **학습·예측은 반드시 `demand_model.build_features()` 하나를 거쳐야 한다** —
  한쪽만 계절 배율을 곱하면 조용히 틀린 값이 나온다(테스트가 지킨다).
- **계절 배율은 `season_ratio()` 하나로만 구하라.** 0 근처 대여소를 넣으면
  `E|X̂| > |E X|` 때문에 배율이 과대추정된다(`WARMUP_MIN_DEMAND`). 측정 코드와
  운영 코드가 다른 함수를 쓰면 측정이 거짓말을 한다 — 실제로 겪었다.
- 예측 정확도는 **작업 대상 대여소(`|rebal_qty| > 2`)에서** 재야 한다. 전체 평균은
  파이프라인이 손대지 않는 대여소에 희석돼 정반대 결론이 나온 적이 있다.
- pandas 2.x 기준으로 작성 (`.loc` 슬라이스에 inplace 연산 금지).
- 버전에 영향 주는 수정을 하면 `docs/기록/버전관리.md`에 이유와 함께 기록한다.

## 저장소 (db.py — CSV·DB 이중 기록 중)

- `db.py`가 `data/bike_system.db`(SQLite, WAL)를 다룬다. **파일명의 `{now}`는 DB에서
  `run_label` 컬럼**이고, 라벨을 생략하면 최신 실행분이 나온다.
- **각 단계는 CSV를 저장한 뒤 `db.save_output(...)`을 부른다.** 새 산출물을 만드는
  단계를 추가하면 이 호출도 함께 넣어라(테스트가 누락을 잡는다).
- **CSV가 아직 정본이다.** `save_output()`은 DB 실패 시 경고만 남기고 파이프라인을
  멈추지 않는다. 이 동작을 예외로 바꾸지 마라 — 전환기 설계다 (DB_PLAN 2단계).
- 새 테이블을 추가할 때는 `db.TABLES`에 스코프·컬럼 변환을 등록하고 `SCHEMA`에
  DDL을 넣어라. 한글 컬럼은 ASCII로 변환한다(변환표가 `db.TABLES`에 모여 있다).
- **컬럼 추가는 `SCHEMA`만 고치면 된다** — `init_schema()`의 `migrate_schema()`가
  기존 DB에 `ALTER TABLE ADD COLUMN` 한다. 이름 변경·삭제·타입 변경은 자동이 아니다
  (데이터 손실 위험). `rental_history`는 `MIGRATION_EXCLUDED` — 재적재가 정답이라
  빈 컬럼을 붙이면 안 된다.
- 연결은 `db.session()`(스키마 보장 + 자동 close)을 써라. `sqlite3.connect`를 직접
  부르지 마라 — 다른 DB로 옮길 때 `db.connect()` 한 곳만 바꾸면 되도록 격리해 뒀다.
- **`PBR_DB_PATH`로 DB 경로를 재정의**할 수 있다. 테스트는 이 변수로 임시 DB를 쓴다 —
  이걸 빼면 테스트 데이터가 실제 DB에 쌓인다.
- **대여이력은 `db.read_rental_source(period, csv_path=..., columns=[...])`로 읽는다**
  (DB 우선, 없으면 CSV 폴백). 원본 한글 컬럼명 그대로 돌려주므로 기존 계산 코드가
  그대로 돈다. **필요한 컬럼만 지정하라** — 60만 행에서 컬럼 수가 로드 시간을 좌우한다.
- 대여이력 적재는 `python tools/load_rentals.py`. DB가 항상 빠른 건 아니다 —
  단일 기간만 다루면 CSV가 빠르고, 여러 기간이 든 DB에서 한 기간만 읽을 때 DB가 이긴다
  (실측은 DB_PLAN.md 4단계).
- **저장소를 바꾸는 수정을 하면 CSV 경로와 DB 경로의 산출물이 같은지 반드시 확인하라.**
  `tests/test_rentals.py`의 동일성 테스트가 그 역할이며, 실제로 dtype 결함을 잡아냈다.

## 성과 지표를 다룰 때

- **`improvement_rate`는 "계획 달성률"이지 실제 효과가 아니다.** `rebal_qty`가
  `target_qty − stock`에서 파생되므로 구조적으로 높게 나온다. 이 값을 대외 성과로
  인용할 때는 성격을 밝혀라. 자세한 근거와 개선안은 `docs/분석/KPI.md`.
- 지표를 추가할 때는 **효과·비용·효율을 함께** 둬라. 개선률만 올리면 소요시간이 늘어난다.
  실측에서 `_05_10`은 개선률이 가장 낮은데(65%) km당 개선은 가장 높았다(2.08대).
- 새 지표는 `kpi_summary` 테이블(실행 1건 = 1행)에 넣는다. `db.KPI_FIELDS`에 컬럼을
  등록하고 step4의 `save_kpi_summary()`에서 계산해 넘기면 된다.
  계산 못 한 지표는 빼고 넘기면 NULL로 남는다.
- 시간대(`duration`)가 다르면 수요 구조가 반대이므로 섞어서 평균 내지 마라.

## 테스트

```powershell
python -m pytest                 # 607개, 약 100초 (tests/ 만 수집)
python tools/make_sample_data.py --now "데모"   # 합성 데이터만 생성
```

- `tests/test_webapp.py` — 라우트·보안·템플릿 회귀
- `tests/test_pipeline.py` — 합성 데이터로 step0→step4 실제 실행 + 산출물 검증
- 실행마다 고유 라벨(`smoketest-{pid}`)을 쓰므로 실데이터를 덮어쓰지 않는다.
  **테스트를 추가할 때 이 규칙을 깨지 마라** — 고정 라벨을 쓰면 사용자 데이터가 지워진다.
- `conftest.py`의 autouse fixture가 모든 테스트에 `PBR_DB_PATH`를 임시 경로로 강제한다.
  이걸 지우면 테스트가 실제 `data/bike_system.db`를 만들고 오염시킨다.
- 새 step이나 라우트를 추가하면 해당 테스트도 함께 추가한다.

## 🔴 문서화는 철저히 — 이 저장소에서 가장 자주 강조된 요구다

**사용자가 반복해서 요구한 사항이다**(*"문서화 철저히 해"*, 2026-09-02 포함 여러 차례).
이 저장소의 문서는 *"무엇을 했는가"* 가 아니라 **"왜 그렇게 했는가"** 를 남긴다 —
되돌린 결정, 측정이 뒤집은 가설, 고치지 않기로 한 이유까지 함께 적는다.

**무엇을 적나**

- **기각한 것도 적는다.** 재 보고 기각한 것과 안 해 본 것은 다르다. 논문 8장에
  *"표시만 한다"* 가 아니라 **"쪼개 봤으나 손실이 커 채택하지 않았다"** 로 쓸 수
  있는 것이 이 기록에서 나온다.
- **틀렸던 과정도 적는다.** 결론만 남기면 다음 사람이 같은 함정에 빠진다.
  스스로 걸린 함정은 **특히** 적는다(EXPERIMENTS 26장의 '만들며 걸린 함정 둘').
- **조건을 함께 적는다.** 수치만 적으면 나중에 해석할 수 없다 — 탐색 시간·씨앗
  개수·스냅샷·모집단·기간을 수치 옆에 붙인다.
- **재현 명령을 적는다.** 각 장 머리에 `> 재현: python ...` 한 줄.

**어디에 적나** — 한 곳만 고치면 다른 곳이 낡는다(실제로 여러 번 겪었다).

| 무엇 | 어디 |
| --- | --- |
| 실험 결과·근거 | `docs/분석/EXPERIMENTS.md` (+ 맨 위 목차 표에 한 줄) |
| 변경 이유 | `docs/기록/버전관리.md` (판 번호 절) |
| 남은 과제·기각 결정 | `docs/기록/TODO.md` (+ '지금 열려 있는 것' 색인) |
| 실험 스크립트 | `experiments/README.md` 표 |
| 논문에 걸리는 것 | `docs/연구/THESIS.md` · `docs/연구/초안/` 해당 장 |

⚠️ **스크립트 자체에도 적어라.** docstring에 *"왜 이 실험이 필요한가 · 무엇을
묻는가 · 어떻게 판정하는가"* 를 쓴다. 문서를 읽어야만 피할 수 있는 함정은
**도구가 스스로 알리게** 만드는 것이 더 낫다(예: `collect_road_time.py --status`가
옛 파라미터분을 세어 경고한다).

## 수정 후 확인 절차

0. `python -m pytest` 로 회귀 확인 (가장 먼저)
1. `python -m py_compile <수정한 파일>` 로 구문 확인
2. 수정한 파일이 읽는/쓰는 파일명 패턴(`{now}`, `{duration}`, `{period}`)이
   앞뒤 단계와 일치하는지 확인
3. `docs/기록/TODO.md`의 해당 항목을 완료 처리하고, 새로 발견한 문제는 추가
4. 동작이 바뀌었으면 `docs/구현/steps/` 해당 단계 문서도 갱신
5. **`python tools/check_consistency.py`** — 판 번호 겹침과 흩어진 값(테스트
   개수 등)을 잡는다. 여러 환경이 동시에 올리므로 **커밋 직전에** 돌린다.
