# Public Bike Rebalancing System

## 1. 프로젝트 개요
공공자전거 재배치 작업의 비효율성을 해결하기 위해
대전광역시 공공자전거(TASHU) 데이터를 수집·정제·가공하여
재배치 의사결정에 활용 가능한 데이터 파이프라인을 구축한 프로젝트로,
클러스터링, 정수선형계획법(ILP), 차량경로문제(VRP)를 적용하였다.

---

## 2. 프로젝트 목표

공공자전거 운영 시 발생하는 문제를 해결하기 위해
    - 특정 대여소 자전거 부족/과잉
    - 비효율적인 운영 차량 이동
    - 원천 데이터만으로는 최적화 모델 적용이 어려움

다음을 수행하는 의사결정 지원 시스템 개발
    1. Pick/Drop 대여소 선정
    2. 재배치 수량 최적화
    3. 차량 이동거리 최소화

"원천 데이터 -> 분석용 데이터셋 -> 최적화 입력 데이터"
로 이어지는 데이터 파이프라인을 구축하는 것을 목표로 한다.

---

## 3. 핵심 성과

- Tashu open API 데이터 자동 수집
- 대여/반납 데이터 정제
- 순수요(Net Demand) 데이터 생성
- 목표 재고(Target_qty) 및 재배치 수량(rebal_qty) 계산
- Pick / Drop 후보 생성
- 재배치 결과 데이터 검증(불균형 개선률 분석)

- 대전광역시 타슈 대여소 XXX개 분석
- 대여 이력 XXX만 건 처리
- Pick Station XX개 선정
- Drop Station XX개 선정
- 클러스터 XX개 생성
- 재배치 후 평균 불균형 XX% 개선

---

## 4. 전체 흐름

```mermaid
flowchart LR

A[TASHU API]
--> B[Raw Data]

B --> C[Station Metadata]
B --> D[Rental History]

C --> E[Data Cleaning]
D --> E

E --> F[Net Demand]

F --> G[Target Quantity]

G --> H[Pick/Drop Selection]

H --> I[Clustering]

I --> J[ILP]

J --> K[VRP]

K --> L[Visualization]

L --> M[Performance Evaluation]


```mermaid

    TASHU API 
        ↓ 
    데이터 전처리 
        ↓ 
    순수요(Net Demand) 계산 
        ↓ 
    목표 재고(Target Qty) 산정 
        ↓ 
    Pick / Drop 선정 
        ↓ 
    클러스터링 
        ↓ 
    ILP 최적화 
        ↓ 
    VRP 최적화 
        ↓ 
    시각화 
        ↓ 
    성능 평가

```

---

## 5. 개발 환경

|항목|기술|
|언어|Python|
|데이터 전처리|Pandas 3.8, NumPy|
|시각화|Folium, Matplotlib|
|클러스터링|Scikit-learn|
|최적화|PuLP|
|데이터 소스|Tashu open API, public data portal|

---

## 6. 기여

| 영역 | 기여 내용 |
|--------|--------|
| Data Collection | TASHU API 자동 수집 모듈 구현 |
| Data Cleaning | 대여소 정보, 거치대 정보 정제 |
| Data Modeling | 순수요(Net Demand) 생성 로직 구현 |
| Feature Engineering | Pick/Drop 후보 생성 알고리즘 구현 |
| Optimization | ILP 입력 데이터 생성 |
| Validation | 재배치 전후 불균형 개선률 측정 |

---

## 5.프로젝트 구조

project/ 

├── step0 (데이터 전처리)
│ ├── tashu_api.py 
│ ├── api_to_info.py 
│ ├── extract_parking_lot.py 
│ ├── raw_to_net.py 
│ └── !calculate_target_qty.py
│ 
├── step1 (작업 대상 대여소 선정 및 클러스터링)
│ ├── adjust_module.py 
│ ├── 1.top_st_clustering.py 
│ └── 2.st_visualization.py 
│ 
├── step2 (ILP / VRP 최적화)
│ ├── ilp.py 
│ └── vrp.py 
│ 
├── step3 (결과 시각화)
│ ├── module.py 
│ └── main.py 
│ 
└── step4 (성능 평가)
│ └── imbalance.py

---

## 5. 처리 과정

### Step 0. Raw 데이터 전처리

공공데이터포털 및 타슈 API 데이터를 수집하고 분석 가능한 형태로 변환한다.

수집 데이터에 대해 다음 검증을 수행하였다.

- 중복 대여소 제거
- 좌표 누락 데이터 제거
- 비정상 재고 데이터 제거
- 대여/반납 이력 정합성 검증
- 재처리 가능한 CSV 저장

|파일명|설명|
|-----|-----|
|tashu_api.py|타슈 API 데이터 수집|
|api_to_info.py|API 데이터를 분석용 CSV 형식으로 변환|
|extract_parking_lot.py|대여소 정보 추출, 대여소 좌표 및 거치대 정보 생성|
|raw_to_net.py|대여/반납 데이터를 OD 네트워크 기반 순수요(Net Demand) 형태로 변환|
|!calculate_target_qty.py|목표 재고(Target Quantity) 계산|

!calculate_target_qty.py
    기능 : 논문의 수요 추정식을 기반으로 재배치 수량 계산


    Pick = Stock - \mu
    Drop = \mu + 1.65\sigma

    출력
    - 대여소별 목표 재고
    - 재배치 수량

---


### Step 1. 작업 대상 대여소 선정 및 클러스터링

재배치가 필요한 대여소를 선정하고 공간적 군집화를 수행한다. (재배치 대상 선정 과정)

    |파일명|설명|
    |-----|-----|
    |adjust_module.py|재배치 대상 선정 보조 모듈|
    |1.top_st_clustering.py	불균형 대여소 선정 및 거리 기반 클러스터링(대여소별 불균형 지수 계산, Pick/Drop station 선정, 거리 기반 클러스터링 수행)|
    |2.st_visualization.py|Pick/Drop 대여소 시각화|

1.top_st_clustering.py
    기능
        - 대여소별 불균형 지수 계산
        - Pick Station 선정
        - Drop Station 선정
        - 거리 기반 클러스터링 수행
    출력
        - 재배치 대상 대여소 목록
        - 클러스터 정보

st_visualization.py
    기능
        - Pick Station 시각화
        - Drop Station 시각화
        - 클러스터별 색상 구분

    출력
        - 지도 기반 시각화 결과

결과
    - Pick Station (회수 대상)
    - Drop Station (공급 대상)
    - Cluster 정보 생성

---

### Step 2. 재배치 최적화

정수선형계획법과 차량경로문제를 이용하여 최적 재배치 계획을 수립한다.

ILP (Integer Linear Programming, 정수선형계획법)

    파일: ilp.py

    목적:
        - 공급량 = 수요량 만족 (Pick 대여소에서 회수, Drop 대여소에 공급)
        - 차량 적재 용량 고려
        - 재배치 수량 결정
        - 최소 이동거리 계산

    입력 :
        - Pick Station
        - Drop Station
        - 재배치 수량

    출력 : 최적 운송 계획

VRP (Vehicle Routing Problem)

    파일: vrp.py

    목적:
        - 총 이동 거리/운영시간 최소화
        - 재배치 차량 운행 경로 최적화

    출력 : 
        - 차량별 방문 순서
        - 총 이동거리
        - 총 이동시간

---

### Step 3. 결과 시각화

최적화 결과를 지도 및 경로 형태로 시각화한다.

    |파일명|설명|
    |-----|-----|
    |module.py|시각화 보조 함수|
    |main.py|최종 재배치 결과 시각화|

출력
    - 재배치 경로 지도
    - 차량 이동 경로
    - Pick / Drop 대여소 표시

### Step 4. 성능 평가

재배치 전후의 불균형 개선 정도를 분석한다.

파일 : imbalance.py

평가 지표
    - 목표 재고 대비 불균형(imbalance)
    - 재배치 후 개선량(improvement)
    - 개선률(improvement rate)

Improvement
= Before Imbalance - After Imbalance

Improvement Rate
= Improvement / Before Imbalance

---

## 8. 기대 효과
- 공공자전거 수요·공급 불균형 완화
- 운영 차량 이동거리 감소
- 재배치 효율 향상
- 운영 비용 절감
- 데이터 기반 의사결정 지원

---

## 9. 배운 점

### Data Engineering
- API 기반 데이터 수집 파이프라인 구축
- 데이터 정합성 검증
- ETL 프로세스 설계
- 데이터 모델링
- 최적화 모델을 위한 데이터셋 구축
- 데이터 기반 의사결정 시스템 설계

### Optimization
- ILP 기반 운송 최적화
- VRP 기반 경로 최적화

### Visualization
- Folium 기반 지도 시각화
- 재배치 결과 검증 대시보드 구축

---

## 9. 참고 문헌
- 이은탁, 손봉수(2019). 「이용수요 기반의 서울시 공공자전거 재배치전략 도출」
- 대한교통학회지, 제37권 제1호, pp.27-38.
- 
- 
- 
- 
- 
- 
- 

---


# 사용 데이터

- 대전광역시 타슈 대여 이력 데이터
- 타슈 대여소 정보
- 타슈 거치대 정보
- 대여소 좌표 정보