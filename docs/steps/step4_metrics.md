# Step 4 — 성과 지표 (`step4 (성과 지표)/`)

재배치 전후의 재고 불균형을 비교해 개선 효과를 정량화하는 단계입니다.

## `imbalance.py`

- **입력**: `data/pp_data/ILP/후보/top{duration} ({now}).csv`, VRP 계획 CSV(경로 요약용)
- **출력**:
  - `data/pp_data/성능 지표/verification{duration} ({now}).csv` (지표 계산 결과)
  - `data/pp_data/성능 지표/route_summary{duration} ({now}).csv` (클러스터별 이동거리·운행시간)
  - `data/pp_data/성능 지표/visualization/imbalance_map{duration} ({now}).html` (개선 지도)
  - SQLite `metrics`·`route_summary` 테이블 (이중 기록, 한글 컬럼은 ASCII로 변환)
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

> ⚠️ **이 값은 "계획 달성률"입니다.** `rebal_qty`가 `target_qty − stock`에서 파생되므로,
> 계획이 자기가 세운 목표를 얼마나 채웠는지를 잽니다. 이용자가 실제로 자전거를 탈 수
> 있었는지는 측정하지 않으며, `target_qty` 자체가 틀렸다면 이 값이 높아도 의미가 없습니다.
> 실측 성격의 지표(결품 시간, 수요 충족률)와 KPI 체계 개선안은 [KPI.md](../KPI.md) 참고.

### 경로 요약 (`route_summary`)

VRP 결과의 distance_km·travel_sec·work_sec·cum_sec 컬럼(1.2.0에서 추가)을 클러스터별로
집계해 방문수·처리대수·총이동거리(km)·총이동/작업/소요시간(분)을 산출합니다.
구버전 VRP 결과(시간 컬럼 없음)면 건너뜁니다.

**시간 예산 준수율**도 함께 출력합니다 — `TIME_BUDGET_MINUTES`(기본 120분) 안에
끝나는 클러스터 비율입니다. 초과분은 수요 예측 시간대가 지나간 뒤 작업이 끝나므로
계획의 효과가 줄어듭니다 ([FLEET.md](../FLEET.md)의 시간 예산 절 참고).

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
