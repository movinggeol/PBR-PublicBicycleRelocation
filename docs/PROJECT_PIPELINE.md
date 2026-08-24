# 프로젝트 전체 구조와 데이터 파이프라인

이 문서는 타슈(TASHU) 공공자전거 재배치 프로젝트의 원천 데이터가 최종 차량 경로와 성과 지표가 되기까지의 전체 흐름을 설명합니다.

## 1. 전체 흐름

~~~mermaid
flowchart TD
    A[TASHU Open API] --> C[대여소 재고 CSV]
    B[공공데이터포털 대여 이력] --> D[원천 이력 CSV]
    C --> E[대여소 정보 통합]
    D --> E
    E --> F[순수요 Net Demand]
    F --> G[목표 재고와 재배치량]
    G --> H[Pick Drop 선정]
    E --> H
    H --> I[K-Medoids 클러스터링]
    I --> J[ILP 수량 최적화]
    J --> K[VRP 차량 경로]
    K --> L[TMAP Folium 지도]
    L --> M[불균형 개선 평가]
~~~

각 단계는 CSV 파일을 주고받습니다. 따라서 다음 단계가 기대하는 폴더와 파일명으로 이전 단계의 결과가 생성되어야 합니다.

## 2. 저장소 구조

~~~text
.
├── data/
│   ├── raw_data/                         # 공공데이터포털 대여 이력
│   └── pp_data/                          # 처리 결과
│       ├── 대여소별 재고/
│       ├── 대여소별 주차대수/
│       ├── 대여소 정보/
│       ├── 순수요/
│       ├── 재배치 정보/
│       ├── ILP/후보/
│       ├── ILP/
│       ├── VRP/
│       └── 성능 지표/
├── step0_collect/                # 수집·전처리
├── step0_eda/                  # 이력 병합·탐색
├── step1_cluster/
├── step2_optimize/
├── step3_map/
├── step4_metrics/
├── webapp/                               # 웹 대시보드 (FastAPI + Jinja2)
├── tests/                                # pytest 200개
├── tools/                                # 합성 데이터·적재·백테스트 보조 도구
├── experiments/                          # 파라미터 실험·구조 결정용 측정
└── docs/
~~~

지도 HTML은 각 산출 폴더 아래 `visualization/`에 저장됩니다
(`ILP/visualization`, `VRP/visualization`, `성능 지표/visualization`).
폴더는 `project_config.ensure_output_dirs()`가 만들어 두므로 손으로 만들 필요가 없습니다.

data는 대용량 원천·중간·결과 파일을 보관하는 영역이며, 코드에서 경로를 직접 참조합니다. 폴더명과 파일명의 날짜·시간도 코드의 now, period, duration 값과 일치해야 합니다.

## 3. 입력 데이터

### TASHU Open API

step0_collect/tashu_api.py가 TASHU API에서 대여소별 현재 재고를 조회합니다.

- 입력: .env의 TASHU_API_KEY
- 출력: data/pp_data/대여소별 재고/대여소별_자전거대수 ({now}).csv
- 용도: 분석 시점의 재고와 대여소 기본 정보 확보

### 공공데이터포털 대여 이력

data/raw_data/에 타슈 대여 이력 CSV를 둡니다. 월별 파일은 step0_eda/concat_1year_file.py로 병합할 수 있습니다.

주요 입력 항목은 대여·반납 시각, 출발·도착 대여소 ID입니다.

## 4. Step 0: ETL과 순수요 계산

### 4.1 이력 병합·탐색

- concat_1year_file.py: 여러 월의 CSV를 하나로 병합
- EDA.py: 월별 대여량 등 기본 탐색

### 4.2 대여소 정보 통합

extract_parking_lot.py는 거치대 설명에서 주차 가능 대수를 추출합니다. api_to_info.py는 대여소 ID를 기준으로 이력, API 재고, 좌표, 거치대 정보를 결합합니다.

출력:

~~~text
data/pp_data/대여소별 주차대수/대여소별_주차대수 ({now}).csv
data/pp_data/대여소 정보/st_info ({now}).csv
~~~

이후 단계에서 쓰는 핵심 값은 대여소 ID, 위도, 경도, 현재 재고, 거치대 용량입니다.

### 4.3 순수요

raw_to_net.py는 시간대와 대여소별 대여·반납량을 집계합니다.

~~~text
순수요 = 대여량 - 반납량
~~~

출력은 data/pp_data/순수요/st_net_daily ({period}).csv입니다.

### 4.4 목표 재고와 재배치량

calculate_target_qty.py는 재고와 순수요 통계를 이용해 대여소별 목표 재고를 계산합니다.

~~~text
mu ≥ 0 : target_qty = mu + z × sigma        (z = TARGET_Z, 기본 1.99)
mu < 0 : target_qty = stock + mu
         이후 [0, parking_lot × 1.5]로 자름
rebal_qty = target_qty − stock              (tanh로 완화 후 정수화)
~~~

- **`z = 1.99`는 실측값입니다.** 관행값 1.65는 "정규분포 95%"라는 이유로 쓰였지만
  12개월 백테스트에서 실제 커버리지가 91.7~92.8%에 그쳤습니다. 근거·재현은
  [EXPERIMENTS.md](EXPERIMENTS.md) 1장, `PBR_TARGET_Z`로 바꿉니다.
- **평일과 휴일 중 한쪽만 골라 계산합니다**(`--day-type`, 기본 auto).
  휴일 = 주말 ∪ 공휴일이며, 섞으면 부호가 반대인 대여소끼리 상쇄됩니다.
- **계절 수준 보정(warmup)이 기본으로 켜져 있습니다.** 계획 대상 달의 첫 14일
  실적으로 **도시 전체 배율 하나**를 구해 `mu`·`sigma`에 곱합니다
  (`--warmup-period` / `--warmup-days 0`으로 끔). 배율을 대여소별로 추정하지
  않는 이유는 며칠치로 나누면 잡음만 커지기 때문입니다.

출력은 data/pp_data/재배치 정보/rebal_qty{duration} ({now}).csv입니다. 양수는 공급이 필요한 Drop, 음수는 회수 가능한 Pick 후보로 사용됩니다.

## 5. Step 1: Pick·Drop과 클러스터

top_st_clustering.py는 재배치량이 큰 대여소를 선정하고 양수·음수 재배치량을 Drop·Pick으로 분류합니다. 위도·경도 정보도 함께 붙여 다음 최적화 단계의 후보 파일을 만듭니다.

~~~text
data/pp_data/ILP/후보/top{duration} ({now}).csv
~~~

K-Medoids 기반 공간 클러스터링은 작업 대여소를 가까운 군집으로 묶습니다.

- adjust_module.py: 메도이드, 군집 목적함수, 군집 후보 이동 보조
- top_st_clustering.py: 군집 생성·크기 및 작업량 균형 조정
- st_visualization.py: Pick·Drop과 군집을 지도에 저장

군집 조정의 목적은 Pick 또는 Drop이 특정 군집에 과도하게 몰리지 않게 하고, 차량이 처리할 수 있는 규모로 작업을 나누는 것입니다.

## 6. Step 2: ILP 수량 최적화

step2_optimize/ilp.py는 클러스터별 Pick 대여소에서 Drop 대여소로 이동할 자전거 수를 정수선형계획법으로 계산합니다.

입력은 top 후보 CSV이며, 주요 컬럼은 station_id, lat, lon, pick_qty, drop_qty, cluster입니다.

결정변수 x(i,j)는 Pick 대여소 i에서 Drop 대여소 j로 옮기는 자전거 수입니다.

제약조건:

1. Pick에서 회수하는 수량은 해당 대여소의 공급량 이하
2. Drop에 공급하는 수량은 해당 대여소의 필요량 이하
3. 클러스터 총 이동량은 총 Pick과 총 Drop 중 작은 값
4. 이동 수량은 음수가 아닌 정수

목적함수는 대여소 간 Haversine 거리에서 환산한 이동시간과 이동 수량의 합을 최소화합니다.

- 기본 차량 속도: 25 km/h (`project_config.VEHICLE_SPEED_KMPH`)
- 요일 구분: `--day-type weekday|holiday|auto` (기본 auto = 계획 대상일로 판정).
  **휴일 = 주말 ∪ 공휴일**이며, 평일과 휴일은 수요 구조가 달라 한 실행에 섞지 않습니다
  ([steps/step0_raw.md](steps/step0_raw.md))
- solver: PuLP CBC
- 출력: data/pp_data/ILP/ILP_plan{duration} ({now}).csv

## 7. Step 2: VRP 차량 경로

vrp.py는 ILP 결과를 실제 차량이 수행할 방문 순서로 바꿉니다.

1. ILP 결과를 클러스터별로 분리
2. Pick·Drop 작업량 집계
3. 타슈 관제센터를 depot으로 설정
4. 현재 위치에서 처리 효율이 높은 다음 대여소 선택
5. 적재량이 부족하면 depot으로 복귀 (ILP 입력에서는 발생하지 않음 —
   [steps/step2_ilp_vrp.md](steps/step2_ilp_vrp.md))
6. 남은 작업이 없어질 때까지 반복

현재 코드의 운영 가정 (depot·적재 용량은 project_config 공통 상수):

- 차량 적재 용량: 10대 (대전교통공사 확인값, 버전관리.md 1.0.1)
- 차량 속도: 25 km/h (`project_config.VEHICLE_SPEED_KMPH` — ILP와 같은 값, 1.13.2에서 통일)
- Pick·Drop 작업 시간: 자전거 1대당 각 30초 (가정값)
- depot: 타슈 관제센터 (ST0001)
- 보유 차량 21대(`FLEET_SIZE`), 회차당 투입 상한 10대(`VEHICLES_PER_ROUND`)

**클러스터 1개 = 차량 1대**입니다. 그래서 step1의 클러스터 수는 회차당 투입 가능
대수를 넘지 못하며, 경로 계산이 끝나면 실제 차량이 배정됩니다. 누적 작업이 적은
차량부터 뽑으므로 회차마다 다른 차량이 나갑니다(로테이션). 배정 결과와 차량별
누적 작업량은 DB에 기록되어 웹 `/vehicles`에서 확인할 수 있습니다.
자세한 운용 모델과 규칙은 [FLEET.md](FLEET.md)를 참고하세요.

출력에는 이동별 거리(distance_km)·이동시간(travel_sec)·작업시간(work_sec)·
누적시간(cum_sec)과 배정된 `vehicle_id`가 포함되며, step4가 이를 집계해
경로 요약을 만듭니다.

출력은 data/pp_data/VRP/VRP_plan{duration} ({now}).csv입니다.

> Pick 후보만 있고 Drop 후보가 없는 시간대(옮길 곳이 없는 경우)에는 재배치가
> 성립하지 않으므로 step1이 해당 회차를 건너뛰고, step2·step4도 함께 건너뜁니다.

## 8. Step 3: 지도 시각화

step3_map/module.py는 CSV 좌표와 경로를 정규화하고 TMAP 응답을 처리합니다. main.py는 VRP 경로, Pick·Drop 마커, 클러스터 정보를 Folium 지도에 표시합니다.

- 입력: VRP 계획 CSV, Pick·Drop 후보 CSV
- API 키: .env의 API_KEY
- API: TMAP Routes API
- 출력: data/pp_data/VRP/visualization/vrp_map*.html

HTML 결과는 브라우저에서 직접 열어 확인할 수 있습니다.

## 9. Step 4: 성과 평가

imbalance.py는 목표 재고 대비 재배치 전후의 불균형을 비교합니다.

~~~text
개선량 = 재배치 전 불균형 - 재배치 후 불균형
개선률 = 개선량 / 재배치 전 불균형
~~~

- 입력: Pick·Drop 후보 및 VRP 결과
- 출력: data/pp_data/성능 지표/verification*.csv (불균형 개선),
  route_summary*.csv (클러스터별 총 이동거리·이동/작업/소요시간)
- 지도: data/pp_data/성능 지표/visualization/imbalance_map*.html

## 10. 전체 실행 순서

일괄 실행은 `run_pipeline.py`가 아래 순서를 그대로 돌립니다
(옵션 전체는 [README](../README.md#실행) 참고).

~~~powershell
python run_pipeline.py --dry-run          # 실행 목록·파일 존재 확인
python run_pipeline.py                    # 전체 실행
~~~

단계별로 따로 돌릴 때는 프로젝트 루트에서 실행합니다.
각 스크립트도 같은 공통 옵션(`--now` 등)을 그대로 받습니다.

~~~powershell
python "step0_eda/concat_1year_file.py"   # 월별 파일을 합칠 때만
python "step0_collect/tashu_api.py"
python "step0_collect/extract_parking_lot.py"
python "step0_collect/api_to_info.py"
python "step0_collect/raw_to_net.py"
python "step0_collect/calculate_target_qty.py"
python "step1_cluster/top_st_clustering.py"
python "step1_cluster/st_visualization.py"
python "step2_optimize/ilp.py"
python "step2_optimize/vrp.py"
python "step3_map/main.py"
python "step4_metrics/imbalance.py"
~~~

## 11. 재현성 점검

- 원천 CSV가 data/raw_data/에 있는가
- data/pp_data/의 입력·출력 하위 폴더가 있는가
- now, period, duration_list가 같은 분석 시점을 가리키는가
- 대여소 ID와 좌표가 중복·누락되지 않았는가
- 재고·거치대 수가 음수 또는 비정상 값이 아닌가
- TASHU_API_KEY와 API_KEY가 .env에 있는가
- 각 단계의 출력 CSV가 다음 단계의 입력 경로에 존재하는가
- API 키와 원천 데이터가 Git에 포함되지 않았는가

## 12. 개선 방향

상세 목록과 우선순위는 [TODO.md](TODO.md), 단계별 상세는 [steps/](steps/) 문서를 참고합니다.

- ~~requirements.txt를 추가해 패키지 버전을 고정~~ → 완료 (`requirements.txt`)
- ~~날짜와 경로를 공통 설정으로 통합~~ → 완료. 모든 step 스크립트가
  `project_config.get_runtime_config()`를 사용하며, `run_pipeline.py`의
  `--now/--period/--duration/--raw-file` 인자가 그대로 전달됨 (버전 1.0.3)
- ~~step0 스크립트 간 import 부작용 체인 제거~~ → 완료. 각 스크립트가 독립 실행되며
  순서는 run_pipeline.py가 제어 (버전 1.0.3)
- CSV 파일 간 암묵적 스키마를 검증하는 코드 추가
- ~~데이터 규모가 커질 경우 SQLite 등으로 중간 데이터 관리~~ → 완료. `db.py`가
  CSV와 **이중 기록**하며, 웹 산출물 API는 DB를 읽는다
  ([DB_SCHEMA.md](DB_SCHEMA.md) · [DB_PLAN.md](DB_PLAN.md) 1~4단계)
- 현재 휴리스틱 VRP를 차량·시간창·실제 도로 거리 제약을 포함한 전용 solver로 확장

