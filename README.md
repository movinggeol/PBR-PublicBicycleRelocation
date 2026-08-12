# Public Bike Rebalancing System

대전광역시 공공자전거 타슈(TASHU)의 대여 이력과 대여소 현황을 이용해 재배치 대상, 이동 수량, 차량 방문 경로를 계산하는 데이터·최적화 프로젝트입니다.

## 처리 흐름

```text
TASHU API·공공데이터 → 원천 데이터 정제 → 순수요·목표 재고 계산
→ Pick/Drop 선정·클러스터링 → ILP 수량 계획 → VRP 차량 경로
→ 지도 시각화·성과 평가
```

## 주요 결과

| 지표 | 결과 |
| --- | --- |
| 보유 대여이력 | 5,396,686건 (2025-01 ~ 2026-03, 15개월) |
| 분석 대여소 | 1,349개 |
| 재배치 후보 | 310개 (Pick 107 / Drop 203) |
| 작업 대상 | 87개 → 클러스터 10개 (차량 10대) |
| 총 이동거리 | 305 km |
| 시간 예산(120분) 준수 | **10/10 클러스터 (100%)** |
| 평균 불균형 개선 | **65%** (Pick 50% / Drop 77%) |

**하루 3회차 운용 결과** (05~10 / 10~15 / 15~20시)

| 회차 | 차량 | 처리 | 이동거리 | 최장 | 결품 시간 (전 → 후) |
| --- | --- | --- | --- | --- | --- |
| `_05_10` | 10대 | 313대 | 294 km | 115.8분 | 2.16h → **0.42h** |
| `_10_15` | 10대 | 204대 | 255 km | 95.9분 | 1.76h → **0.21h** |
| `_15_20` | 10대 | 231대 | 305 km | 108.3분 | 1.64h → **0.30h** |
| **합계** | **21대 전원** | **748대** | **854 km** | — | — |

회차마다 다른 차량이 나가며(로테이션), 하루면 보유 21대가 모두 최소 1회 출동합니다.
**시간 예산 초과는 3회차 통틀어 0건**입니다 — 군집 거리 가중치를 실험으로 조정한
결과입니다([step1 문서](docs/steps/step1_clustering.md)의 '거리 가중치' 절).

**목표 재고와 군집 가중치는 실측 실험으로 정했습니다** (`z = 1.99`, `γ = 3000`).
근거·재현은 [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md)에 있습니다.

※ 25년 11월 대여이력(511,951건), 2026-08-12 실행 결과입니다.
입력 데이터·파라미터에 따라 달라집니다.

> ⚠️ **개선률·목표 도달률은 `z`가 다른 실행끼리 비교하면 안 됩니다.**
> 두 지표는 목표 재고를 분모로 삼아, `z`를 올리면 결품이 줄어도 함께 떨어집니다.
> 그래서 위 표에는 목표값과 무관한 **결품 시간**을 실었습니다.
> 개선률은 **계획 달성률**이지 이용자 편익이 아닙니다 — [docs/KPI.md](docs/KPI.md) 참고.

## 프로젝트 구조

```text
.
├── data/                                  # 원천·중간·결과 데이터 (Git 미포함)
│   ├── raw_data/                          # 타슈 대여 이력
│   └── pp_data/                           # 파이프라인 산출물
├── step0 (raw데이터 처리)/                # API·원천 데이터 처리
├── step0(전처리 및 EDA)/                  # 이력 병합·탐색 분석
├── step1 (작업대상 선정 및 클러스터링)/   # Pick/Drop·클러스터링
├── step2 (ilp, vrp)/                      # 수량·경로 최적화
├── step3 (결과 시각화)/                   # TMAP/Folium 지도
├── step4 (성과 지표)/                     # 불균형 평가
├── docs/                                  # 문서 (파이프라인 설명·단계별 문서·TODO)
├── webapp/                                # 웹 대시보드 (FastAPI, 파이썬 단독)
├── tests/                                 # 스모크 테스트 (pytest)
├── tools/                                 # 합성 데이터 생성기 등 보조 도구
├── experiments/                           # 일회성 학습·검증 스크립트
├── project_config.py                      # 공통 설정(now/period/duration/raw_file)
├── db.py                                  # SQLite 저장소 (CSV와 이중 기록, DB_PLAN.md)
├── run_pipeline.py                        # 전체 단계 일괄 실행기
└── requirements.txt                       # 고정된 패키지 버전
```

## 단계별 파일

| 단계 | 파일 | 역할 | 산출물 |
| --- | --- | --- | --- |
| 0 | `tashu_api.py` | TASHU 재고 API 수집 | 대여소별 재고 CSV |
| 0 | `extract_parking_lot.py` | 거치대 수 추출 | 대여소별 주차대수 CSV |
| 0 | `api_to_info.py` | 대여소·재고·거치대 정보 통합 | `st_info*.csv` |
| 0 | `raw_to_net.py` | 대여·반납 이력에서 순수요 계산 | `st_net_daily*.csv` |
| 0 | `calculate_target_qty.py` | 목표 재고·재배치량 계산 | `rebal_qty*.csv` |
| 1 | `1.top_st_clustering.py` | 불균형 대여소 선정·K-Medoids(kmedoids) 군집화 | `top*.csv` |
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
`raw_file`)을 공유합니다. 값은 CLI 인자(`--now` 등) → 환경변수(`PBR_NOW` 등) → 기본값 순으로
결정되며, 파이프라인 산출물 파일명은 모두 이 라벨로 만들어집니다.

전체 일괄 실행(각 단계를 순서대로 subprocess 호출):

```powershell
python run_pipeline.py              # 전체 실행 (기본 설정)
python run_pipeline.py --dry-run    # 실행 목록만 확인
python run_pipeline.py --skip-api --skip-eda   # 수집·EDA 생략
python run_pipeline.py --now "2026-05-21 18" --period "25년 11월" --duration "_05_10"
python run_pipeline.py --duration "_05_10,_10_15"   # 여러 시간대 일괄 처리
```

단계별 개별 실행:

```powershell
python "step0 (raw데이터 처리)/tashu_api.py"
python "step0 (raw데이터 처리)/extract_parking_lot.py"
python "step0 (raw데이터 처리)/api_to_info.py"
python "step0 (raw데이터 처리)/raw_to_net.py"
python "step0 (raw데이터 처리)/calculate_target_qty.py"
python "step1 (작업대상 선정 및 클러스터링)/1.top_st_clustering.py"
python "step1 (작업대상 선정 및 클러스터링)/st_visualization.py"
python "step2 (ilp, vrp)/ilp.py"
python "step2 (ilp, vrp)/vrp.py"
python "step3 (결과 시각화)/main.py"
python "step4 (성과 지표)/imbalance.py"
```

월별 파일을 합칠 때는 먼저 `step0(전처리 및 EDA)/concat_1year_file.py --concat`을 실행합니다.

## 테스트

실데이터나 API 키 없이 합성 데이터로 전 단계를 검증합니다.

```powershell
pip install -r requirements-dev.txt
python -m pytest                 # 34개 (웹 라우트 17 + 파이프라인 E2E 17), 약 20초
```

- `tests/test_webapp.py` — 라우트·경로 탈출 차단·템플릿 렌더링 회귀 감지
- `tests/test_pipeline.py` — 합성 데이터로 step0→step1→step2→step4 실행 후
  산출물 존재·스키마·ILP 공급 제약·개선량을 검증. 실행마다 고유 라벨을 써서
  실데이터를 건드리지 않고, 끝나면 그 라벨 파일만 정리합니다.

데모용 데이터만 만들고 싶다면:

```powershell
python tools/make_sample_data.py --now "데모"
```

## 웹 대시보드

브라우저에서 파이프라인 실행부터 결과 지도 열람까지 할 수 있습니다. 자세한 내용은 [docs/WEBAPP.md](docs/WEBAPP.md).

```powershell
python -m webapp        # http://127.0.0.1:8000
```

- `/` 실행 폼·DB 실행 이력·작업 이력·최신 산출물
- `/kpi` 실행별 성과 지표와 직전 실행 대비 증감
- `/vehicles` 차량별 누적 작업량·회차 배정 이력 (로테이션 형평성)
- `/maps` folium 지도 결과(HTML)를 브라우저에서 바로 열람
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
DB 이관 계획은 [docs/DB_PLAN.md](docs/DB_PLAN.md)에 있습니다.

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
경우에는 CSV가 더 빠릅니다. 실측 비교는 [docs/DB_PLAN.md](docs/DB_PLAN.md) 4단계에 있습니다.

## 성과 지표

```text
개선량 = 재배치 전 불균형 - 재배치 후 불균형
개선률 = 개선량 / 재배치 전 불균형
```

여기에 클러스터별 총 이동거리·소요시간(`route_summary`)이 더해집니다.

> ⚠️ 위 개선률은 **계획 달성률**입니다. `rebal_qty`가 `target_qty − stock`에서
> 파생되므로 "계획이 자기 목표를 얼마나 채웠는가"를 재는 값이고, 이용자가 실제로
> 자전거를 탈 수 있었는지와는 다릅니다. 지표 체계의 한계와 개선안은
> [docs/KPI.md](docs/KPI.md)에 정리했습니다.

## 문서

| 문서 | 내용 |
| --- | --- |
| [docs/RETROSPECTIVE.md](docs/RETROSPECTIVE.md) | **작업 회고** — 전체 조망, 측정이 뒤집은 가설, 설계 결정 |
| [docs/EXPERIMENTS.md](docs/EXPERIMENTS.md) | **실험 기록** — `z`·학습 창·`γ`를 실데이터로 정한 과정과 근거 |
| [docs/TESTING.md](docs/TESTING.md) | **테스트** — 117개가 무엇을 지키는지, 외부 API 수동 검증 절차 |
| [docs/PROJECT_PIPELINE.md](docs/PROJECT_PIPELINE.md) | 전체 데이터 파이프라인 상세 설명 |
| [docs/WEBAPP.md](docs/WEBAPP.md) | 웹 대시보드 실행·구조·API |
| [docs/DB_PLAN.md](docs/DB_PLAN.md) | SQLite 도입 결정·스키마·이관 계획·성능 측정 |
| [docs/KPI.md](docs/KPI.md) | 성과 지표 체계 설계 (현재 지표의 한계와 개선안) |
| [docs/FLEET.md](docs/FLEET.md) | 차량 운용 — 하루 3회차 로테이션과 형평성 기록 |
| [docs/TODO.md](docs/TODO.md) | 해야 할 것·고쳐야 할 것 (우선순위별) |
| [docs/steps/step0_raw.md](docs/steps/step0_raw.md) | Step 0: 수집·전처리·순수요·재배치량 |
| [docs/steps/step0_eda.md](docs/steps/step0_eda.md) | Step 0: 이력 병합·EDA |
| [docs/steps/step1_clustering.md](docs/steps/step1_clustering.md) | Step 1: Pick/Drop 선정·클러스터링 |
| [docs/steps/step2_ilp_vrp.md](docs/steps/step2_ilp_vrp.md) | Step 2: ILP 수량·VRP 경로 최적화 |
| [docs/steps/step3_visualization.md](docs/steps/step3_visualization.md) | Step 3: TMAP·Folium 지도 |
| [docs/steps/step4_metrics.md](docs/steps/step4_metrics.md) | Step 4: 불균형 개선 평가 |

## 참고

- [대전 타슈](https://bike.tashu.or.kr/main.do)
- [공공데이터포털](https://www.data.go.kr/)
- [TMAP API](https://tmapapi.tmapmobility.com/)

