# Step 2 — ILP 수량 최적화 · VRP 경로 (`step2_optimize/`)

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
   — **ILP를 거친 입력에서는 이 분기가 실행되지 않는다**(총 pick = 총 drop이라
   후보가 비지 않는다). 살아 있는 호출부는 대조군 B1뿐이다
5. 남은 작업이 없을 때까지 반복
6. **마지막 대여소에서 depot으로 복귀** (1.19.1). 이미 depot이면 붙이지 않는다.
   중간 복귀(4번)와 최종 복귀가 `_depot_return()` 한 곳에서 나온다

> **1.19.1 이전에는 6번이 없어 그 자리에서 멈췄다.** 4번이 복귀를 기록하는 유일한
> 자리인데 ILP 입력에서는 실행되지 않으므로 **가끔이 아니라 항상 빠졌다**
> (실데이터 1,224행에 `return` 0건). 그만큼 이동거리·소요시간·예산 판정이
> 낙관적이었다 — 실측으로 총 이동의 33%가 누락돼 있었다.
> **1.19.1 이전에 잰 수치와 섞어서 비교하지 마라.** → [TODO.md](../../기록/TODO.md) 1-1

> **greedy의 최적성 갭**: 같은 노드·같은 적재 제약·**같은 depot 복귀 조건**으로
> OR-Tools와 비교하면 이동거리가 클러스터 평균 **2~5%** 더 깁니다
> (`experiments/baseline/ortools_gap.py`).
>
> 🔴 **단일 값으로 인용하지 마십시오 — 갭은 세 조건에 함께 달려 있습니다.**
>
> | 조건 | 실측 |
> | --- | --- |
> | 탐색 시간 | 10초 **2.2%** → 60초 **3.8%** → 300초 **5.3%** (수렴하지 않음) |
> | 난수 씨앗 | **1.93 ~ 4.86%** (같은 조건, 씨앗 셋 — **폭이 가장 크다**) |
> | 달 | 25년 11월 3.8% ≈ 26년 03월 3.9% (요철 아님) |
>
> 씨앗은 K-Medoids 군집 경계를 바꾸므로 **경로 문제 자체가 달라집니다.**
> 근거: [EXPERIMENTS.md](../../분석/EXPERIMENTS.md) 6·23·24장.
>
> ⚠️ **옛 "평균 11%"는 틀린 값이었습니다** — OR-Tools만 depot 복귀를 빼고 풀어
> 갭이 부풀어 있었습니다(2026-08-27 정정). 그 이전 수치와 섞지 마십시오.

### 운영 상수

| 상수 | 값 | 상태 |
| --- | --- | --- |
| `VEHICLE_CAPACITY` | 10대 | project_config 공통 상수 (대전교통공사 확인값) |
| `DEPOT_*` | ST0001 타슈 관제센터 | project_config 공통 상수 (step3와 공유) |
| `FLEET_SIZE` | 21대 | 기본값(대전교통공사 확인값, FLEET.md). 웹 실행 폼·`--fleet-size`(`PBR_FLEET_SIZE`)로 실행마다 조정 |
| `VEHICLES_PER_ROUND` | 10대 | 회차당 투입 상한 = step1의 클러스터 수 상한. 보유 대수로 잘린다 |
| `VEHICLE_SPEED_KMPH` | 25km/h | project_config 공통 상수. **ILP와 같은 값이어야 한다**(1.13.2에서 통일). 선행연구(이은탁·손봉수, 2019, Constraint 3)와 같은 값 |
| `PICK/DROP_TIME_SEC` | 30초 | 자전거 1대당 작업시간으로 사용. 선행연구(이은탁·손봉수, 2019, Constraint 4)와 같은 값 — 다만 그 논문도 실측이 아니라 가정값이라 "수렴" 근거이지 "실측 검증"은 아니다 |

### 차량 배정 (로테이션)

경로 계산이 끝나면 클러스터별 작업량을 산출해 **실제 차량을 배정**합니다.
누적 작업이 적은 차량부터 뽑으므로 직전 회차에 나간 차량은 다음 회차에서 뒤로 밀립니다.
배정 결과는 `vehicle_assignment` 테이블에 남고 `vrp_plan`에 `vehicle_id`로 붙습니다.
규칙과 운영 모델은 [FLEET.md](../FLEET.md) 참고.

## 현재 문제점

| 우선순위 | 문제 |
| --- | --- |
| ~~🟡~~ | ~~ILP 25km/h vs VRP 30km/h 불일치~~ → 해소(1.13.2). 둘 다 `VEHICLE_SPEED_KMPH`(25)를 읽는다 |
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
- [x] ~~`VEHICLE_TOTAL` 반영 방안 결정~~ → 회차당 투입 상한으로 클러스터 수를 제한하고
      실제 차량을 배정·기록 (1.8.0, [FLEET.md](../FLEET.md))
- [x] ~~차량이 여러 클러스터를 순차 처리하도록 확장~~ → **채택하지 않음(1.13.2, 사용자 결정).**
      한 회차에 차량 1대 = 클러스터 1개 + depot 복귀 구조를 유지한다
- [ ] 시간 예산(예: 120분)을 클러스터링 제약으로 반영 — 현재는 사후 확인만 가능
- [ ] 장기: OR-Tools 등 전용 VRP solver로 교체 (시간창·실도로 거리 반영)
