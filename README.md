# Public Bike Rebalancing System

대전광역시 공공자전거 타슈(TASHU)의 대여 이력과 대여소 현황을 이용해 재배치 대상, 이동 수량, 차량 방문 경로를 계산하는 데이터·최적화 프로젝트입니다.

## 처리 흐름

```text
TASHU API·공공데이터 → 원천 데이터 정제 → 순수요·목표 재고 계산
→ Pick/Drop 선정·클러스터링 → ILP 수량 계획 → VRP 차량 경로
→ 지도 시각화·성과 평가
```

## 주요 결과

**대조군 비교** — 재배치 후 대여소·일 평균 **결품 시간(h)**, 낮을수록 좋습니다.
(25년 11월 평일, VRP가 실제로 옮긴 대수 기준)

| 방법 | `_05_10` | `_10_15` | `_15_20` |
| --- | --- | --- | --- |
| 무재배치 | 2.00 | 1.67 | 1.72 |
| 그리디 (군집·ILP 없음) | 0.70 | 0.54 | 0.66 |
| 지리 균등 군집 (불균형 조정 없음) | 1.40 | 1.04 | 1.50 |
| 평균 목표재고 (`z = 0`) | 1.66 | 3.58 | 3.90 |
| **제안 방법** | **0.55** | **0.40** | **0.43** |

> **2026-08-26 재측정** — 위 값은 **depot 복귀를 포함**합니다(총 이동의 33%).
> 이전 판은 복귀를 뺀 값이었습니다. 근거·재현은
> [docs/분석/EXPERIMENTS.md](docs/분석/EXPERIMENTS.md) 5장.
> ✅ 5개월 반복도 재측정했습니다 — 제안 방법 **0.53 ± 0.07 / 0.43 ± 0.04 /
> 0.52 ± 0.09**, 무재배치·그리디·지리 군집 대비 모두 유의합니다
> (Wilcoxon, n = 15, p = 0.0001).

> **그리디는 제안 방법보다 더 많이 옮기고도(316대 vs 270대) 결품을 더 남깁니다.**
> 몇 대를 옮기느냐가 아니라 어디로 옮기느냐의 문제입니다.
> 다만 **km당 편익은 그리디와 대등합니다** — 복귀를 포함해 재 보니 총 이동거리가
> 430 km 대 442 km로 거의 같아져, 이 점이 더 뚜렷해졌습니다.
> 제안 방법의 우위 중 일부는 더 멀리 다녀서 얻은 것입니다.

> **`z = 0`은 아무것도 안 하느니 못합니다** — `_10_15`·`_15_20`에서 무재배치(1.67·1.72h)
> 보다 나쁩니다(3.58·3.90h). 안전재고를 빼면 평균만 맞추다가 오히려 결품이 늡니다.

**하루 3회차 운용 결과** (05~10 / 10~15 / 15~20시)

| 회차 | 차량 | 처리 | 이동거리 | 최장 소요 | 예산 초과 | 결품 시간 (전 → 후) |
| --- | --- | --- | --- | --- | --- | --- |
| `_05_10` | 10대 | 270대 | 430 km | 133.6분 | 1건 | 2.00h → **0.55h** |
| `_10_15` | 10대 | 221대 | 422 km | 111.5분 | 0건 | 1.67h → **0.40h** |
| `_15_20` | 10대 | 249대 | 450 km | 141.5분 | 1건 | 1.72h → **0.43h** |
| **합계** | **21대 전원** | **740대** | **1,302 km** | — | **2건** | — |

| 지표 | 결과 |
| --- | --- |
| 보유 대여이력 | 5,396,686건 (2025-01 ~ 2026-03, 15개월) |
| 분석 대여소 | 1,349개 |
| 작업 대상 (`_05_10`) | 89개 → 클러스터 10개 (차량 10대) |
| 목표 재고·군집 가중치 | `z = 1.99`, `γ = 3000` — **실측 실험으로 결정** |

회차마다 다른 차량이 나가며(로테이션), 하루면 보유 21대가 모두 최소 1회 출동합니다.

※ 25년 11월 순수요(평일), 씨앗 42, **2026-08-26 재측정** 결과입니다
(depot 복귀 포함). 입력 데이터·파라미터에 따라 달라집니다.

> ⚠️ **읽을 때 주의할 것 세 가지**
>
> 1. **개선률·목표 도달률은 `z`가 다른 실행끼리 비교할 수 없습니다.** 두 지표는 목표
>    재고를 분모로 삼아, `z`를 올리면 결품이 줄어도 함께 떨어집니다. 그래서 위 표는
>    목표값과 무관한 **결품 시간**으로 냈습니다 — [docs/분석/KPI.md](docs/분석/KPI.md).
> 2. **결품 시간은 실측이 아니라 순수요로 복원한 시뮬레이션**이고, 재고를 0에서
>    자르므로 **결품의 하한**입니다.
> 3. **경로는 greedy 휴리스틱**입니다. OR-Tools(탐색 60초) 대비 이동거리 갭은
>    **씨앗에 따라 평균 1.9~4.9%, 최악 9.0~19.5%** 로 흔들립니다 — 씨앗이 군집을
>    바꾸면 경로 문제 자체가 달라지기 때문입니다. **단일 값으로 인용하지 마세요.**
>    갭이 작아 greedy를 유지하기로 했습니다 —
>    [docs/분석/EXPERIMENTS.md](docs/분석/EXPERIMENTS.md) 6·23·24장.

## 프로젝트 구조

```text
.
├── data/                                  # 원천·중간·결과 데이터 (Git 미포함)
│   ├── raw_data/                          # 타슈 대여 이력
│   └── pp_data/                           # 파이프라인 산출물
├── step0_collect/                # API·원천 데이터 처리
├── step0_eda/                  # 이력 병합·탐색 분석
├── step1_cluster/   # Pick/Drop·클러스터링
├── step2_optimize/                      # 수량·경로 최적화
├── step3_map/                   # TMAP/Folium 지도
├── step4_metrics/                     # 불균형 평가
├── docs/                                  # 문서 — 읽는 사람 기준으로 나눠 두었습니다
│   ├── README.md                          #   목차: 어느 폴더에 무엇이 있는지
│   ├── GLOSSARY.md                        #   용어집
│   ├── 분석/                              #   수요 모델·지표·실험 (FORMULATION, DECISIONS, KPI …)
│   ├── 구현/                              #   파이프라인·화면·DB·테스트 (steps/ 포함)
│   ├── 연구/                              #   졸업작품·논문 (THESIS, RELATED_WORK, LITERATURE)
│   └── 기록/                              #   회고·TODO·버전 이력
├── webapp/                                # 웹 대시보드 (FastAPI, 파이썬 단독)
├── tests/                                 # 스모크 테스트 (pytest)
├── tools/                                 # 합성 데이터 생성기 등 보조 도구
├── experiments/                           # 검증·실험 스크립트 (성격별 5분류)
│   ├── params/ baseline/ structure/       #   파라미터·대조군·설계 측정
│   └── diagnostic/ learning/              #   산출물 진단·학습용 예제
├── project_config.py                      # 공통 설정(now/period/duration/day_type/…)·운영 상수
├── demand_model.py                        # 수요 피처·계절 보정(warmup)·모델 하네스
├── tashu.py                               # 타슈 API 클라이언트 (수집·실시간 대조 공용)
├── weather.py                             # 날씨 원천 (포털 CSV + 기상청 API 허브)
├── mapviz.py                              # 지도 범례·군집 팔레트 (세 지도가 공유)
├── db.py                                  # SQLite 저장소 (CSV와 이중 기록, DB_SCHEMA.md)
├── run_pipeline.py                        # 전체 단계 일괄 실행기
├── requirements.txt                       # 런타임 의존성 (하한 `>=` 고정)
└── requirements-dev.txt                   # 테스트 전용 (pytest·httpx2)
```

## 단계별 파일

| 단계 | 파일 | 역할 | 산출물 |
| --- | --- | --- | --- |
| 0 | `tashu_api.py` | TASHU 재고 API 수집 | 대여소별 재고 CSV |
| 0 | `extract_parking_lot.py` | 거치대 수 추출 | 대여소별 주차대수 CSV |
| 0 | `api_to_info.py` | 대여소·재고·거치대 정보 통합 | `st_info*.csv` |
| 0 | `raw_to_net.py` | 대여·반납 이력에서 순수요 계산 | `st_net_daily*.csv` |
| 0 | `calculate_target_qty.py` | 목표 재고·재배치량 계산 | `rebal_qty*.csv` |
| 1 | `top_st_clustering.py` | 불균형 대여소 선정·K-Medoids(kmedoids) 군집화 | `top*.csv` |
| 1 | `st_visualization.py` | Pick/Drop·클러스터 지도 | HTML 지도 |
| 2 | `ilp.py` | Pick→Drop 이동 수량 최적화 | `ILP_plan*.csv` |
| 2 | `vrp.py` | 차량 방문 순서 계산 | `VRP_plan*.csv` |
| 3 | `main.py`, `module.py` | TMAP 도로 경로·지도 생성 | VRP HTML 지도 |
| 4 | `imbalance.py` | 전후 불균형·개선률 평가 | 검증 CSV·HTML |

## 설치

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

# 테스트까지 돌리려면 (pytest·httpx2 — 런타임 의존성도 함께 깔립니다)
python -m pip install -r requirements-dev.txt
```

### 검증된 환경 (2026-08-10, 전체 파이프라인 E2E 확인)

| 구분 | 버전 |
| --- | --- |
| Python | **3.14.7** (Windows 11) |
| 데이터 | pandas 3.0.5 · numpy 2.5.2 · scipy 1.18.0 |
| 최적화·군집 | scikit-learn 1.9.0 · kmedoids 0.5.5 · PuLP 3.3.2 (CBC 내장) |
| 시각화 | folium 0.20.0 · matplotlib 3.11.1 |
| 웹 | fastapi 0.141.1 · uvicorn 0.52.1 · jinja2 3.1.6 · python-multipart 0.0.32 |
| 기타 | requests 2.34.2 · python-dotenv 1.2.2 |

> ⚠️ **Python 3.11 이하에서 쓰던 구버전 조합(pandas 2.0.3 / numpy 1.24.4 등)은
> 3.12+에서 설치되지 않거나 import가 실패합니다.** 반드시 위 버전 이상을 사용하세요.

- PuLP는 내장 CBC solver를 사용하므로 별도 solver 설치가 필요 없습니다.
- K-Medoids는 `kmedoids`(Rust FasterPAM) 패키지를 씁니다. 과거에 쓰던
  `scikit-learn-extra`는 프로젝트가 아카이브되어 Python 3.12+ 휠이 없고
  C++ 빌드툴을 요구하므로 교체했습니다.
- `requirements.txt`는 하한(`>=`)으로 고정합니다. 상한을 걸면 새 Python 버전에서
  휠이 없어 설치가 깨집니다.

## 환경변수

`.env.example`을 `.env`로 복사한 뒤 키를 입력합니다. `.env`는 Git에 포함하지 않습니다.

```dotenv
TASHU_API_KEY=발급받은_타슈_API_키
API_KEY=발급받은_TMAP_API_키
```

## 실행

모든 단계가 [project_config.py](project_config.py)의 공통 설정(`now`, `period`, `duration`,
`raw_file`, `day_type`, `target_date`, `warmup_*`)을 공유합니다. 값은 CLI 인자(`--now` 등) →
환경변수(`PBR_NOW` 등) → 기본값 순으로 결정되며, 파이프라인 산출물 파일명은 모두 이
라벨로 만들어집니다.

전체 일괄 실행(각 단계를 순서대로 subprocess 호출):

```powershell
python run_pipeline.py              # 전체 실행 (기본 설정)
python run_pipeline.py --dry-run    # 실행 목록만 확인
python run_pipeline.py --skip-api --skip-eda   # 수집·EDA 생략
python run_pipeline.py --skip-map              # TMAP 지도 생략 (키가 없을 때)
python run_pipeline.py --now "2026-05-21 18" --period "25년 11월" --duration "_05_10"
python run_pipeline.py --duration "_05_10,_10_15"   # 여러 시간대 일괄 처리
```

`run_pipeline.py`의 옵션은 전부 하위 단계로 그대로 전달됩니다(차량 대수만 예외 —
`project_config`가 import 시점 상수로 읽으므로 환경변수로 내려보냅니다).

| 옵션 | 뜻 | 기본값 |
| --- | --- | --- |
| `--now` | 실행을 묶는 라벨. 산출물 파일명과 DB `run_label`이 됩니다 | `2026-05-21 18` |
| `--period` | 순수요를 뽑을 기간 | **계산해 둔 것 중 가장 최근 달** |
| `--duration` | 시간대. 콤마로 여러 개 (`_05_10`·`_10_15`·`_15_20`·`_20_05`) | `_05_10` |
| `--raw-file` | 원천 대여이력 CSV(루트 기준 상대 경로) | `data/raw_data/…(25년11월).csv` |
| `--day-type` | `weekday` \| `holiday` \| `auto`. **휴일 = 주말 ∪ 공휴일** | `auto` |
| `--target-date` | 계획 대상일(YYYY-MM-DD). `auto` 판정의 기준 | 오늘 |
| `--warmup-period` | 계절 보정에 쓸 기간 | 계획 대상일의 달 |
| `--warmup-days` | 보정에 쓸 일수. `0`이면 끔 | `14` |
| `--fleet-size` | 보유 차량 대수 (1~99) | `21` |
| `--vehicles-per-round` | 회차당 투입 **상한**. 실제 대수는 작업량이 정합니다 | 보유 대수(`21`) |
| `--skip-api` / `--skip-eda` | 수집·EDA 생략 | 꺼짐 |
| `--skip-map` | step3 TMAP 지도 생략 (TMAP 키가 없는 환경) | 꺼짐 |
| `--continue-on-error` | 한 단계가 실패해도 계속 | 꺼짐 |

```powershell
python run_pipeline.py --day-type holiday --now "260813 휴일"   # 휴일 계획
python run_pipeline.py --target-date 2026-09-25                # 그날로 자동 판정
python run_pipeline.py --fleet-size 15 --vehicles-per-round 6  # 차량이 모자란 날
python run_pipeline.py --warmup-period "26년 03월"             # 계절 보정 기간 지정
python run_pipeline.py --warmup-days 0                         # 계절 보정 끄기
```

**평일과 휴일은 한 실행에 섞지 마세요.** 시간대별로 대여소의 33~37%가 두 구분에서
부호가 반대(평일엔 채울 곳이 휴일엔 빼 올 곳)라 평균을 내면 상쇄돼 작업 대상에서
빠집니다 ([docs/구현/steps/step0_raw.md](docs/구현/steps/step0_raw.md)).

단계별 개별 실행:

```powershell
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
```

월별 파일을 합칠 때는 먼저 `step0_eda/concat_1year_file.py --concat`을 실행합니다.

## 재고 시계열 수집 (운영 예시 시나리오)

파이프라인이 쓰는 초기 재고는 **실행하는 순간의 스냅샷 한 장**입니다. 실측 재고가
시간에 따라 어떻게 움직이는지 남겨 두면, 결품을 시뮬레이션이 아니라 **실측으로**
잴 수 있습니다. 그래서 평일 07~22시 재고를 10분마다 모읍니다
([docs/구현/COLLECTOR.md](docs/구현/COLLECTOR.md)).

```powershell
.\scripts\collector.ps1 install     # 수집 시작 (최초 1회 등록)
.\scripts\collector.ps1 pause       # 일시정지 — 작업은 남기고 안 깨움
.\scripts\collector.ps1 resume      # 재개
.\scripts\collector.ps1 uninstall   # 완전 중지 — 작업 삭제
.\scripts\collector.ps1 status      # 스케줄 상태 + 수집 현황
.\scripts\collector.ps1 now         # 지금 한 틱 즉시 수집
```

`install` 한 번이면 평일 07:00에 저절로 시작해 22:00에 멈추고, 주말·공휴일은
건너뜁니다. **일시정지·중지는 스케줄만 건드리며 모은 데이터를 지우지 않습니다.**

수집기 자체를 직접 부를 수도 있습니다.

```powershell
python tools/collect_stock.py            # 한 틱 (스케줄러가 부르는 형태)
python tools/collect_stock.py --status   # 수집 현황만 (API 호출 안 함)
python tools/collect_stock.py --loop     # 창이 끝날 때까지 상주
```

- **공휴일 제외는 스케줄러가 아니라 스크립트가 합니다** — 작업 스케줄러는 요일만
  알기 때문입니다. 판정은 `project_config.is_holiday()`(주말 ∪ 공휴일) 하나입니다.
- 저장 위치는 `stock_history` 테이블이고, `data/raw_data/재고이력/`에 일별 CSV
  백업과 수집 로그가 함께 남습니다. **실패도 로그에 남습니다** — 그래야 나중에
  '결측'과 '재고 0'을 구분할 수 있습니다.
- **PC가 깨어 있어야 모입니다.** 화면 꺼짐·잠금은 괜찮지만 절전은 안 됩니다
  (전원 어댑터를 꽂아 두세요).

## 5분 안에 직접 돌려보기 (데이터·API 키 없이)

원천 데이터와 API 키가 없어도 **합성 데이터로 전 단계를 그대로 실행**할 수 있습니다.
심사·리뷰에서 직접 확인하려면 이 절차만 따르면 됩니다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt

python -m pytest              # 665개 통과 확인 (약 100초)
python tools/reproduce.py     # 합성 데이터 생성 → step0~step4 → 결과 표 (약 20초)
```

`tools/reproduce.py`는 **명령 하나**로 합성 대여소·대여이력을 만들고 파이프라인
전체를 돌린 뒤 회차별 결품 시간을 찍는다. 실 DB를 건드리지 않도록 별도 DB
(`data/재현.db`)를 쓰고, 끝나면 만든 파일을 지운다(`--keep`으로 남긴다).
웹 화면까지 보려면:

```powershell
python tools/reproduce.py --keep
$env:PBR_DB_PATH = "data/재현.db"; python -m webapp   # http://127.0.0.1:8000
```

> 🔴 **예전 안내(`make_sample_data.py` + `run_pipeline.py --skip-api`)는 쓰지 마십시오.**
> 그 절차는 합성 대여소 90곳을 만들어 놓고 **실데이터 1,361곳을 돌렸습니다** —
> `--skip-api`가 직전 실행의 재고 스냅샷을 물려받기 때문입니다. 오류가 나지 않아
> 실데이터가 있는 PC에서는 드러나지 않았고, 깨끗이 복제한 PC에서는 저장소에 없는
> 원천 CSV를 찾다가 죽었습니다(2026-08-31 확인). `tools/reproduce.py`는
> `--skip-fetch`(라이브 API 호출만 생략)를 쓰고, **파이프라인이 정말 합성
> 대여소를 봤는지 수를 대조해 확인합니다.**

`data/`와 `*.csv`는 저장소에 포함되지 않습니다(.gitignore). 실데이터로 돌리려면
`.env`에 API 키를 넣고 `data/raw_data/`에 타슈 대여 이력을 두어야 합니다 — 아래 '환경변수'와
'데이터 준비' 절을 보세요.

## 테스트

실데이터나 API 키 없이 합성 데이터로 전 단계를 검증합니다.

```powershell
pip install -r requirements-dev.txt
python -m pytest                 # 665개, 약 100초 (tests/ 만 수집)
```

- `tests/test_pipeline.py` (32) — 합성 데이터로 step0→step1→step2→step4를
  **subprocess로 실제 실행**한 뒤 산출물 존재·스키마·ILP 공급 제약·개선량을 검증.
  실행마다 고유 라벨(`smoketest-{PID}`)을 써서 실데이터를 건드리지 않고,
  끝나면 그 라벨 파일만 정리합니다.
- `tests/test_webapp.py` (37) — 라우트·경로 탈출 차단·실행 폼 입력 검증
- `tests/test_calculations.py` (36) — **계산 단위 테스트**: 목표재고 공식(`μ + zσ`,
  거치대 상한, tanh 제한, 0 방향 정수화), 군집 목적함수, VRP 적재·시간 제약,
  ILP 수급 제약, **집행 기준 결품 계산**. 근거는 [docs/분석/FORMULATION.md](docs/분석/FORMULATION.md)
- `tests/test_day_type.py` (36) — 평일/휴일 분리·공휴일 판정·계절 보정,
  **계획과 평가가 같은 요일 구분을 쓰는지**
- 나머지 파일과 각 테스트가 무엇을 지키는지는 [docs/구현/TESTING.md](docs/구현/TESTING.md)에
  정리돼 있습니다.

데모용 데이터만 만들고 싶다면:

```powershell
python tools/make_sample_data.py --now "데모"
```

## 웹 대시보드

브라우저에서 파이프라인 실행부터 결과 지도 열람까지 할 수 있습니다. 자세한 내용은 [docs/구현/WEBAPP.md](docs/구현/WEBAPP.md).

```powershell
python -m webapp        # http://127.0.0.1:8000
```

- `/` 실행 폼·DB 실행 이력·작업 이력·최신 산출물 + **지금 날씨**
  (비가 오면 "실제 재배치 필요량은 평소의 40~55% 수준"을 알립니다 — 계획을 바꾸지는 않습니다)
- `/guide` 사용 안내 — 시작 순서·입력 항목·지표 읽는 법·문제 해결·용어
- `/runs/{id}` 실행 상태·진행 단계·로그 (실행 중단 포함)
- `/kpi` 실행별 성과 지표와 직전 실행 대비 증감, **그래프 3종**
  (실행 추세 꺾은선 · 효과·비용 산점도 · 요일 × 시간 수요 히트맵)과
  **수요 예측 정확도**(월쌍 백테스트)
- `/vehicles` 차량별 누적 작업량·회차 배정 이력 (로테이션 형평성)
- `/maps` folium 지도 결과(HTML)를 브라우저에서 바로 열람
- `/orders` **차량별 작업지시서**(인쇄하면 한 대가 한 장) + 출발 직전
  타슈 API로 계획 대상 대여소만 다시 조회하는 **실시간 재고 대조**
- `/data` CSV 산출물 미리보기·다운로드
- `/api/docs` JSON API 문서 (GeoJSON 대여소, ILP/VRP 계획, 성과 지표)

산출물 API는 DB를 조회하므로 과거 실행분도 볼 수 있습니다.

```
GET /api/metrics                              # 최신 실행
GET /api/metrics?run_label=2026-05-21%2018    # 특정 실행
GET /api/pipeline-runs                        # 실행 이력 목록
```

## 데이터 저장

파이프라인은 CSV와 SQLite에 **동시에** 기록합니다(이중 기록). CSV가 아직 정본이며,
스키마(ERD·테이블 레퍼런스)는 [docs/구현/DB_SCHEMA.md](docs/구현/DB_SCHEMA.md),
DB 이관 계획은 [docs/구현/DB_PLAN.md](docs/구현/DB_PLAN.md)에 있습니다.

```python
import db

with db.session() as conn:
    print(db.list_runs(conn))                 # 실행 이력
    latest = db.load_frame(conn, "metrics")   # 라벨 생략 시 최신 실행분
```

파일명에 있던 분석 시점(`{now}`)이 DB에서는 `run_label` 컬럼이라, 실행 간 비교가
쿼리 한 줄이 됩니다.

```sql
SELECT run_label, AVG(improvement_rate) FROM metrics GROUP BY run_label;
```

DB 위치는 `data/bike_system.db`이며 `PBR_DB_PATH` 환경변수로 바꿀 수 있습니다.

원천 대여이력을 적재해 두면 step0가 CSV 대신 DB에서 읽습니다(적재하지 않으면 CSV 폴백).

```powershell
python tools/load_rentals.py            # 원천 CSV → rental_history
python tools/load_rentals.py --status   # 기간별 적재 현황
```

1년치를 적재해 두고 한 달씩 분석할 때 유리합니다 — 반대로 딱 한 달치 파일만 쓰는
경우에는 CSV가 더 빠릅니다. 실측 비교는 [docs/구현/DB_PLAN.md](docs/구현/DB_PLAN.md) 4단계에 있습니다.

## 성과 지표

```text
개선량 = 재배치 전 불균형 - 재배치 후 불균형
개선률 = 개선량 / 재배치 전 불균형
```

여기에 클러스터별 총 이동거리·소요시간(`route_summary`)이 더해집니다.

> ⚠️ 위 개선률은 **계획 달성률**입니다. `rebal_qty`가 `target_qty − stock`에서
> 파생되므로 "계획이 자기 목표를 얼마나 채웠는가"를 재는 값이고, 이용자가 실제로
> 자전거를 탈 수 있었는지와는 다릅니다. 지표 체계의 한계와 개선안은
> [docs/분석/KPI.md](docs/분석/KPI.md)에 정리했습니다.

## 문서

| 문서 | 내용 |
| --- | --- |
| [docs/README.md](docs/README.md) | **문서 목차** — 어느 폴더에 무엇이 있는지, 겹치는 내용의 정본은 어디인지 |
| [docs/기록/ORIGINS.md](docs/기록/ORIGINS.md) | **시작 기록** — 초기 문제 인식·시행착오·현장 확인 (노션에서 옮김) |
| [docs/기록/RETROSPECTIVE.md](docs/기록/RETROSPECTIVE.md) | **작업 회고** — 전체 조망, 측정이 뒤집은 가설, 설계 결정 |
| [docs/연구/THESIS.md](docs/연구/THESIS.md) | **졸업작품·논문** — 장별 재료 매핑, 대조군·반복 실험 설계, 체크리스트 |
| [docs/분석/FORMULATION.md](docs/분석/FORMULATION.md) | **문제 정형화** — 기호표·목표재고·군집 목적함수·ILP·VRP 수식 |
| [docs/연구/RELATED_WORK.md](docs/연구/RELATED_WORK.md) | **관련 연구** — 문제의 갈래와 본 연구의 위치 |
| [docs/연구/LITERATURE.md](docs/연구/LITERATURE.md) | **문헌 분석** — 논문 24편 한 편씩 분석·비교표·인용 지도 |
| [docs/분석/EXPERIMENTS.md](docs/분석/EXPERIMENTS.md) | **실험 기록** — `z`·학습 창·`γ`를 실데이터로 정한 과정과 근거 |
| [docs/구현/TESTING.md](docs/구현/TESTING.md) | **테스트** — 665개가 무엇을 지키는지, 외부 API 수동 검증 절차 |
| [docs/구현/PROJECT_PIPELINE.md](docs/구현/PROJECT_PIPELINE.md) | 전체 데이터 파이프라인 상세 설명 |
| [docs/구현/WEBAPP.md](docs/구현/WEBAPP.md) | 웹 대시보드 실행·구조·API |
| [docs/구현/DESIGN.md](docs/구현/DESIGN.md) | 화면 디자인 시스템 — 색·글꼴·내비게이션 규칙 |
| [docs/분석/DEMAND_DISTRIBUTION.md](docs/분석/DEMAND_DISTRIBUTION.md) | **순수요 분포** — 정규분포 전제 검증, 커버리지 원인 정정, ML 방향 |
| [docs/구현/DB_SCHEMA.md](docs/구현/DB_SCHEMA.md) | **DB 스키마** — ERD, 테이블 20개 컬럼 레퍼런스, 조인 쿼리 |
| [docs/구현/DB_PLAN.md](docs/구현/DB_PLAN.md) | SQLite 도입 결정·이관 계획·성능 측정 |
| [docs/구현/COLLECTOR.md](docs/구현/COLLECTOR.md) | **재고 시계열 수집** — 평일 07–22시 10분 간격 수집기·운영(시작/일시정지/중지), 두 번째 PC로 휴일 맡기기 |
| [docs/구현/두_PC_작업.md](docs/구현/두_PC_작업.md) | **두 PC로 번갈아 작업** — `.gitignore` 항목별 판단(옮길 것·다시 만들 것), 새 PC 세팅 순서 |
| [docs/구현/DB_이관.md](docs/구현/DB_이관.md) | **DB 이관 절차서** — 실행 스냅샷을 하나만/기간별/전부 내보내고 받는 방법 |
| [docs/분석/WEATHER.md](docs/분석/WEATHER.md) | **날씨** — 어떤 기상청 API를 받는지, 결측·겨울 3시간 누적 처리, 순수요 설명력 측정 |
| [docs/분석/DECISIONS.md](docs/분석/DECISIONS.md) | **기술 선택의 근거** — 왜 ILP·VRP·K-Medoids인지, 이상치·결측·정합성을 왜 그렇게 다뤘는지, 얻음과 잃음 |
| [docs/GLOSSARY.md](docs/GLOSSARY.md) | **용어집** — 한국어 용어 ↔ 코드 이름, 헷갈리기 쉬운 짝 |
| [docs/분석/KPI.md](docs/분석/KPI.md) | 성과 지표 체계 설계 (현재 지표의 한계와 개선안) |
| [docs/구현/FLEET.md](docs/구현/FLEET.md) | 차량 운용 — 하루 3회차 로테이션과 형평성 기록 |
| [docs/기록/TODO.md](docs/기록/TODO.md) | 해야 할 것·고쳐야 할 것 (우선순위별) |
| [docs/기록/버전관리.md](docs/기록/버전관리.md) | **버전 이력** — 무엇을 왜 바꿨는지 (최신순, 1.0 ~ 현재) |
| [docs/기록/메모.md](docs/기록/메모.md) | 작업 메모 — README·포트폴리오 정리 노트 (개인 메모) |
| [docs/기록/출발지-도착지.md](docs/기록/출발지-도착지.md) | depot 좌표 메모 — 초기 설계의 출발지·도착지 |
| [docs/분석/ML_OPPORTUNITIES.md](docs/분석/ML_OPPORTUNITIES.md) | **ML 기회 조사** — 어디에 머신러닝을 쓸 수 있나 (조사) |
| [docs/분석/ML_ATTEMPTS.md](docs/분석/ML_ATTEMPTS.md) | **ML 시도 기록** — 해 보고 안 된 것과 **왜 안 됐는지** |
| [docs/분석/VISUALIZATION.md](docs/분석/VISUALIZATION.md) | **시각화 대안 조사** — HTML 말고 더 나은 수단이 있나 (조사) |
| [docs/연구/COMPARISON.md](docs/연구/COMPARISON.md) | **선행연구 축별 비교** — 문헌 전부를 한 줄에 놓고 본 표 |
| [docs/연구/초안/](docs/연구/초안/) | **논문 초안** — 1~8장 + 3-보(방법 선택의 근거) |
| [docs/구현/steps/step0_raw.md](docs/구현/steps/step0_raw.md) | Step 0: 수집·전처리·순수요·재배치량 |
| [docs/구현/steps/step0_eda.md](docs/구현/steps/step0_eda.md) | Step 0: 이력 병합·EDA |
| [docs/구현/steps/step1_clustering.md](docs/구현/steps/step1_clustering.md) | Step 1: Pick/Drop 선정·클러스터링 |
| [docs/구현/steps/step2_ilp_vrp.md](docs/구현/steps/step2_ilp_vrp.md) | Step 2: ILP 수량·VRP 경로 최적화 |
| [docs/구현/steps/step3_visualization.md](docs/구현/steps/step3_visualization.md) | Step 3: TMAP·Folium 지도 |
| [docs/구현/steps/step4_metrics.md](docs/구현/steps/step4_metrics.md) | Step 4: 불균형 개선 평가 |

## 참고

- [대전 타슈](https://bike.tashu.or.kr/main.do)
- [공공데이터포털](https://www.data.go.kr/)
- [TMAP API](https://tmapapi.tmapmobility.com/)

