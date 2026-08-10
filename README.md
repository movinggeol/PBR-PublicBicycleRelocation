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
| 분석 대여소 | 1,400개 |
| 처리 대여 이력 | 약 60,000건 |
| Pick / Drop 대여소 | 각 40개 |
| 클러스터 | 12개 |
| 평균 불균형 개선 | 약 70% |

※ 25년 11월 대여 이력 + `_05_10` 시간대 기준 실행 결과이며, 입력 데이터·파라미터에 따라 달라집니다.

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
├── experiments/                           # 일회성 학습·검증 스크립트
├── project_config.py                      # 공통 설정(now/period/duration/raw_file)
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

## 웹 대시보드

브라우저에서 파이프라인 실행부터 결과 지도 열람까지 할 수 있습니다. 자세한 내용은 [docs/WEBAPP.md](docs/WEBAPP.md).

```powershell
python -m webapp        # http://127.0.0.1:8000
```

- `/` 실행 폼·실행 이력·최신 산출물
- `/maps` folium 지도 결과(HTML)를 브라우저에서 바로 열람
- `/data` CSV 산출물 미리보기·다운로드
- `/api/docs` JSON API 문서 (GeoJSON 대여소, ILP/VRP 계획, 성과 지표)

## 성과 지표

```text
개선량 = 재배치 전 불균형 - 재배치 후 불균형
개선률 = 개선량 / 재배치 전 불균형
```

## 문서

| 문서 | 내용 |
| --- | --- |
| [docs/PROJECT_PIPELINE.md](docs/PROJECT_PIPELINE.md) | 전체 데이터 파이프라인 상세 설명 |
| [docs/WEBAPP.md](docs/WEBAPP.md) | 웹 대시보드 실행·구조·API |
| [docs/DB_PLAN.md](docs/DB_PLAN.md) | SQLite 도입 결정·스키마·이관 계획 |
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

