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

## 프로젝트 구조

```text
.
├── data/                                  # 원천·중간·결과 데이터
│   ├── raw_data/                          # 타슈 대여 이력
│   └── pp_data/                           # 파이프라인 산출물
├── step0 (raw데이터 처리)/                # API·원천 데이터 처리
├── step0(전처리 및 EDA)/                  # 이력 병합·탐색 분석
├── step1 (작업대상 선정 및 클러스터링)/   # Pick/Drop·클러스터링
├── step2 (ilp, vrp)/                      # 수량·경로 최적화
├── step3 (결과 시각화)/                   # TMAP/Folium 지도
├── step4 (성과 지표)/                     # 불균형 평가
└── docs/                                  # 작업 메모
```

## 단계별 파일

| 단계 | 파일 | 역할 | 산출물 |
| --- | --- | --- | --- |
| 0 | `tashu_api.py` | TASHU 재고 API 수집 | 대여소별 재고 CSV |
| 0 | `extract_parking_lot.py` | 거치대 수 추출 | 대여소별 주차대수 CSV |
| 0 | `api_to_info.py` | 대여소·재고·거치대 정보 통합 | `st_info*.csv` |
| 0 | `raw_to_net.py` | 대여·반납 이력에서 순수요 계산 | `st_net_daily*.csv` |
| 0 | `!calculate_target_qty.py` | 목표 재고·재배치량 계산 | `rebal_qty*.csv` |
| 1 | `1.top_st_clustering.py` | 불균형 대여소 선정·K-Medoids 군집화 | `top*.csv` |
| 1 | `st_visualization.py` | Pick/Drop·클러스터 지도 | HTML 지도 |
| 2 | `ilp.py` | Pick→Drop 이동 수량 최적화 | `ILP_plan*.csv` |
| 2 | `vrp.py` | 차량 방문 순서 계산 | `VRP_plan*.csv` |
| 3 | `main.py`, `module.py` | TMAP 도로 경로·지도 생성 | VRP HTML 지도 |
| 4 | `imbalance.py` | 전후 불균형·개선률 평가 | 검증 CSV·HTML |

## 설치

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install pandas numpy requests python-dotenv scipy scikit-learn scikit-learn-extra pulp folium matplotlib
```

현재 `requirements.txt`는 없습니다. PuLP는 CBC solver를 사용합니다.

## 환경변수

프로젝트 루트의 `.env`에 입력합니다. `.env`는 Git에 포함하지 않습니다.

```dotenv
TASHU_API_KEY=발급받은_타슈_API_키
API_KEY=발급받은_TMAP_API_키
```

## 실행

원천 데이터와 `data/pp_data/` 하위 폴더를 준비하고, 스크립트의 `now`, `period`, `duration_list`를 같은 시점으로 맞춥니다.

```powershell
python "step0 (raw데이터 처리)/tashu_api.py"
python "step0 (raw데이터 처리)/extract_parking_lot.py"
python "step0 (raw데이터 처리)/api_to_info.py"
python "step0 (raw데이터 처리)/raw_to_net.py"
python "step0 (raw데이터 처리)/!calculate_target_qty.py"
python "step1 (작업대상 선정 및 클러스터링)/1.top_st_clustering.py"
python "step1 (작업대상 선정 및 클러스터링)/st_visualization.py"
python "step2 (ilp, vrp)/ilp.py"
python "step2 (ilp, vrp)/vrp.py"
python "step3 (결과 시각화)/main.py"
python "step4 (성과 지표)/imbalance.py"
```

월별 파일을 합칠 때는 먼저 `step0(전처리 및 EDA)/concat_1year_file.py`를 실행합니다.

## 성과 지표

```text
개선량 = 재배치 전 불균형 - 재배치 후 불균형
개선률 = 개선량 / 재배치 전 불균형
```

## 참고

- [대전 타슈](https://bike.tashu.or.kr/main.do)
- [공공데이터포털](https://www.data.go.kr/)
- [TMAP API](https://tmapapi.tmapmobility.com/)

