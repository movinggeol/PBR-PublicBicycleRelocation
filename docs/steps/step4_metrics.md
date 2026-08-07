# Step 4 — 성과 지표 (`step4 (성과 지표)/`)

재배치 전후의 재고 불균형을 비교해 개선 효과를 정량화하는 단계입니다.

## `imbalance.py`

- **입력**: `data/pp_data/ILP/후보/top{duration} ({now}).csv`, VRP 계획 CSV(경로 요약용)
- **출력**:
  - `data/pp_data/성능 지표/verification{duration} ({now}).csv` (지표 계산 결과)
  - `data/pp_data/성능 지표/route_summary{duration} ({now}).csv` (클러스터별 이동거리·운행시간)
  - `data/pp_data/성능 지표/visualization/imbalance_map{duration} ({now}).html` (개선 지도)
  - 콘솔에 전체·Pick·Drop 평균 개선률과 경로 요약 출력

### 지표 정의

```text
재배치 후 재고  new_stock    = stock + rebal_qty
재배치 전 불균형 bf_imbalance = |stock − target_qty|
재배치 후 불균형 af_imbalance = |new_stock − target_qty|
개선량          improvement  = bf_imbalance − af_imbalance
개선률          improvement_rate = improvement / bf_imbalance
```

전체 평균과 Pick/Drop 대상별 평균 개선률을 각각 출력합니다.

※ `improvement_rate`는 목표 대비 얼마나 좁혔는지를 보는 지표입니다. rebal_qty가
tanh 완화·정수화로 부분 재배치가 되는 경우 1.0 미만이 되며, 완전 재배치면 1.0입니다.

### 경로 요약 (`route_summary`)

VRP 결과의 distance_km·travel_sec·work_sec·cum_sec 컬럼(1.2.0에서 추가)을 클러스터별로
집계해 방문수·처리대수·총이동거리(km)·총이동/작업/소요시간(분)을 산출합니다.
구버전 VRP 결과(시간 컬럼 없음)면 건너뜁니다.

### 지도 (`demand_satisfaction_map`)

- Drop=빨강, Pick=파랑 CircleMarker
- 원 크기 = 개선량, 투명도 = 개선률
- 클러스터별 레이어 토글 + 범례

## 작업 목록

- [x] ~~`now`를 project_config로 통일~~ (1.0.3)
- [x] ~~CSV 저장·지도 생성 활성화 + `tiles=` 오타 수정~~ (1.0.3)
- [x] ~~`fillna` no-op 제거~~ (1.0.3)
- [x] ~~duration 다중 실행 지원~~ — `duration_list(config)` 사용 (1.0.3)
- [x] ~~VRP 결과 기반 총 이동거리·운행시간 지표 추가~~ — `route_summary()` (1.2.0)
- [x] ~~improvement_rate 의미 문서화~~ (1.2.0)
