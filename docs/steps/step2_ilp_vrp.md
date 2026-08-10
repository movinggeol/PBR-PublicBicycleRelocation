# Step 2 — ILP 수량 최적화 · VRP 경로 (`step2 (ilp, vrp)/`)

클러스터별로 "어느 Pick 대여소에서 어느 Drop 대여소로 몇 대를 옮길지"(ILP)와
"차량이 어떤 순서로 방문할지"(VRP)를 계산하는 단계입니다.

## `ilp.py` — 이동 수량 최적화

- **입력**: `data/pp_data/ILP/후보/top{duration} ({now}).csv`
- **출력**: `data/pp_data/ILP/ILP_plan{duration} ({now}).csv`
  (hour, cluster, pick_station_id, drop_station_id, qty, travel_time_sec)
  + SQLite `ilp_plan` 테이블 (`hour`는 `duration`과 중복이라 DB에는 저장하지 않는다)

### 모델 (클러스터별 독립 수행)

- 결정변수: `x(i,j)` = Pick i → Drop j 이동 대수 (음이 아닌 정수)
- 목적함수: `min Σ T(i,j)·x(i,j)` — Haversine 거리를 25km/h로 환산한 이동시간 가중 합
- 제약조건:
  1. `Σ_j x(i,j) ≤ pick_qty(i)` — 공급 제한
  2. `Σ_i x(i,j) ≤ drop_qty(j)` — 수요 제한
  3. `Σ x = min(총 Pick, 총 Drop)` — 총 이동량 강제 (없으면 x=0이 최적해가 됨)
- Solver: PuLP CBC, `timeLimit=600초`, `gapRel=2%`

## `vrp.py` — 방문 순서 계산 (greedy 휴리스틱)

- **입력**: ILP_plan CSV + top 후보 CSV(좌표)
- **출력**: `data/pp_data/VRP/VRP_plan{duration} ({now}).csv`
  (cluster, from_id/lat/lon, to_id/lat/lon, action(pick/drop/return), qty,
  distance_km, travel_sec, work_sec, cum_sec)
  + SQLite `vrp_plan` 테이블 (같은 대여소 재방문이 있어 `seq` 컬럼으로 방문 순서 보존)
- 노드 키는 `(station_id, type)` — 같은 대여소가 pick·drop 양쪽에 있어도 유실 없음
- 작업시간은 자전거 1대당 `PICK/DROP_TIME_SEC`(30초) 가정

### 알고리즘

1. 클러스터별로 ILP 결과를 pick/drop 노드로 집계
2. depot(타슈 관제센터 ST0001)에서 출발, 적재량 0
3. 각 후보에 `score = 거리 / 처리가능량` 부여 (노드를 완결 지으면 ×0.1 보너스) → 최소 score 선택
4. 적재 초과/부족으로 후보가 없으면 depot 복귀 후 계속
5. 남은 작업이 없을 때까지 반복

### 운영 상수

| 상수 | 값 | 상태 |
| --- | --- | --- |
| `VEHICLE_CAPACITY` | 10대 | project_config 공통 상수 (대전교통공사 확인값) |
| `DEPOT_*` | ST0001 타슈 관제센터 | project_config 공통 상수 (step3와 공유) |
| `VEHICLE_TOTAL` | 21대 | **선언만, 미사용** — 실제는 클러스터당 1대 |
| `VEHICLE_SPEED_KMPH` | 30 | 시간 계산에 사용 (ILP 25km/h와 다름 — 결정 대기) |
| `PICK/DROP_TIME_SEC` | 30초 | 자전거 1대당 작업시간으로 사용 |

## 현재 문제점

| 우선순위 | 문제 |
| --- | --- |
| 🟡 | ILP는 25km/h·Haversine, VRP는 30km/h — 기준 불일치, 통일 여부는 운영 데이터로 결정 |
| 🟡 | 작업시간 "1대당 30초"는 가정값 — 현장 검증 필요 |

## 작업 목록

- [x] ~~`now`를 project_config로 통일~~ (1.0.3)
- [x] ~~vrp.py depot 복귀 from_id 버그 수정~~ — `current_id` 기록·복귀 후 갱신 (1.0.3)
- [x] ~~ilp.py solver를 `run_ilp_plan()` 인자로 변경~~ (1.0.3)
- [x] ~~죽은 코드 제거~~ — `assume_initial_stock`, `manhattan_distance_km`, `drop_qty` get 잔재 (1.0.3)
- [x] ~~station_info 반복 로드를 루프 밖으로 이동~~ (1.0.3)
- [x] ~~pick/drop 노드 덮어쓰기 수정~~ — `(sid, type)` 키 구조 (1.2.0)
- [x] ~~이동시간·작업시간 계산을 VRP 결과에 추가~~ — distance_km/travel_sec/work_sec/cum_sec (1.2.0)
- [x] ~~drop-only 잔여 시 depot 무한복귀 방어~~ (1.2.0)
- [ ] `VEHICLE_TOTAL` 반영 방안 결정: 다차량 배정 구현 or 상수·문서에서 제거
- [ ] 장기: OR-Tools 등 전용 VRP solver로 교체 (시간창·실도로 거리 반영)
