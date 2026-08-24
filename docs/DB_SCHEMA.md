# DB 스키마 — ERD와 테이블 레퍼런스

> `data/bike_system.db` (SQLite, WAL 모드) 의 구조 문서입니다.
> **왜 SQLite인가·어떻게 이관했는가**는 [DB_PLAN.md](DB_PLAN.md)에 있고,
> 이 문서는 **지금 무엇이 어떻게 들어 있는가**만 다룹니다.
> 정본은 언제나 [db.py](../db.py)의 `SCHEMA` 상수입니다 — 스키마를 바꾸면 이 문서도 함께 고치세요.

| | |
| --- | --- |
| 파일 | `data/bike_system.db` (환경변수 `PBR_DB_PATH`로 재정의) |
| 엔진 | SQLite 3, `journal_mode=WAL` (파이프라인이 쓰는 중에도 웹이 읽을 수 있음) |
| 테이블 | 15개 (실행 이력 1 · 산출물 10 · 차량 운용 2 · 지표 1 · 원천 1) |
| 접근 | 항상 `db.session()` 경유. `sqlite3.connect`를 직접 부르지 마세요 |

---

## 1. 이 DB를 이해하는 열쇠 — 스코프

테이블 구조 자체보다 **"한 행이 무엇에 속하는가"** 가 먼저입니다. 산출물 테이블에는
자연키가 없고, 대신 **어느 실행의 어느 회차인지**가 키의 일부입니다.

| 스코프 | 키 | 뜻 | 해당 테이블 |
| --- | --- | --- | --- |
| **실행** | `run_label` | 파이프라인 실행 1건. CSV 파일명의 `{now}`가 컬럼이 된 것 | `station_stock`, `station_info`, `parking_lot` |
| **실행 + 회차** | `run_label` + `duration` | 한 실행 안의 시간대(`_05_10` 등). 하루 3회차면 3행 세트 | `rebalance_plan`, `pick_drop`, `ilp_plan`, `vrp_plan`, `metrics`, `route_summary`, `vehicle_assignment`, `kpi_summary` |
| **기간** | `period` | 원천 데이터 기간(`25년 11월`). **실행과 무관** | `net_demand`, `rental_history` |
| **전역** | — | 실행에 딸리지 않는 마스터 | `vehicle` |

**`net_demand`가 `period` 스코프인 것이 이 설계의 핵심 판단입니다.** 순수요는 과거
대여이력에서만 나오므로 언제 분석하든 같은 값입니다. `run_label`로 묶었다면 실행할
때마다 같은 데이터가 복제됐을 것입니다.

> `run_label`은 **실행 시각이 아니라 라벨**입니다. `2026-05-21 18` 같은 문자열이며,
> 문자열 정렬이 곧 최신순이 되도록 되어 있습니다(`ORDER BY run_label DESC`).

---

## 2. ERD

관계가 두 축으로 갈립니다. 한 그림에 다 그리면 읽히지 않아 나눴습니다.

### 2-1. 실행(run) 축 — 무엇이 어느 실행에 속하는가

~~~mermaid
erDiagram
    runs ||--o{ station_stock : run_label
    runs ||--o{ station_info : run_label
    runs ||--o{ parking_lot : run_label
    runs ||--o{ rebalance_plan : "run_label+duration"
    runs ||--o{ pick_drop : "run_label+duration"
    runs ||--o{ ilp_plan : "run_label+duration"
    runs ||--o{ vrp_plan : "run_label+duration"
    runs ||--o{ metrics : "run_label+duration"
    runs ||--o{ route_summary : "run_label+duration"
    runs ||--o{ vehicle_assignment : "run_label+duration"
    runs ||--o{ kpi_summary : "run_label+duration"
    runs }o..o{ net_demand : "period (라벨 아님)"
    runs }o..o{ rental_history : "period (라벨 아님)"

    runs {
        TEXT run_label PK "실행 라벨 = 파일명의 now"
        TEXT period "순수요 입력 기간"
        TEXT duration "시간대(마지막 기록값)"
        TEXT raw_file "원천 CSV 경로"
        TEXT day_type "weekday / weekend"
        TEXT created_at "기록 시각"
    }
    net_demand {
        TEXT period PK "실행이 아니라 기간에 속한다"
        TEXT date PK
        TEXT station_id PK
        INTEGER net_00_to_23 "시간대별 순수요 24컬럼"
    }
    rental_history {
        INTEGER id PK "AUTOINCREMENT"
        TEXT period "적재 기간"
        TEXT rent_at "대여일시(정규화 문자열)"
        TEXT rent_station
    }
~~~

`runs`와 `net_demand`·`rental_history`만 **파선 다대다**로 그린 이유: 두 테이블은
`period`로 묶이는데, 여러 실행이 같은 기간을 공유할 수 있고 `runs.period`는 유일하지도
않습니다. **참조 관계가 아니라 "같은 값을 쓴다"에 가깝습니다** — 실행을 지워도 이 두
테이블은 남아야 합니다.

### 2-2. 대여소·클러스터·차량 축 — 한 회차 안에서 무엇이 무엇을 가리키는가

아래 관계는 **모두 같은 `(run_label, duration)` 안에서만 성립합니다.**

~~~mermaid
erDiagram
    station_info ||--o| pick_drop : station_id
    rebalance_plan ||--o| pick_drop : station_id
    pick_drop ||--o{ ilp_plan : "pick/drop_station_id"
    pick_drop ||--o{ vrp_plan : "from_id / to_id"
    pick_drop ||--|| metrics : station_id
    pick_drop }o--|| route_summary : cluster
    route_summary ||--|| vehicle_assignment : cluster
    vehicle ||--o{ vehicle_assignment : vehicle_id
    vehicle ||--o{ vrp_plan : vehicle_id
    route_summary }o--|| kpi_summary : "회차 집계"

    pick_drop {
        TEXT station_id PK "작업 대상 대여소"
        INTEGER rebal_qty "양수=Drop, 음수=Pick"
        INTEGER cluster "군집 = 차량 1대"
    }
    ilp_plan {
        TEXT pick_station_id PK "어디서 빼서"
        TEXT drop_station_id PK "어디에 넣나"
        INTEGER qty "몇 대"
    }
    vrp_plan {
        INTEGER seq PK "방문 순서(자연키가 없어 필요)"
        INTEGER cluster
        TEXT vehicle_id FK
        TEXT action "pick / drop / return"
    }
    route_summary {
        INTEGER cluster PK
        REAL total_min "이동+작업"
    }
    vehicle {
        TEXT vehicle_id PK "V01 ~ V21"
        INTEGER active "0이면 배정 제외"
    }
    vehicle_assignment {
        TEXT vehicle_id PK "누가"
        INTEGER cluster "어느 군집을"
        REAL minutes "형평성 추적의 원장"
    }
~~~

**`cluster`가 두 번째 조인 축입니다.** 클러스터 1개 = 차량 1대이므로
`pick_drop.cluster` → `route_summary.cluster` → `vehicle_assignment.cluster` →
`vehicle_assignment.vehicle_id`로 "이 대여소는 어느 차가 맡았나"가 이어집니다
([FLEET.md](FLEET.md)).

---

## 3. ⚠️ 외래키(FOREIGN KEY)가 선언되어 있지 않습니다

위 ERD의 선은 **논리적 관계일 뿐, DB가 강제하지 않습니다.** `db.py`의 `SCHEMA`에
`REFERENCES` 절이 한 곳도 없습니다. `connect()`가 `PRAGMA foreign_keys=ON`을 켜지만,
선언된 외래키가 없으므로 **지금은 아무것도 하지 않습니다**(장래 선언 대비 설정).

무결성은 대신 이렇게 지켜집니다.

| 장치 | 무엇을 막는가 |
| --- | --- |
| **복합 PK** (스코프 + 자연키) | 같은 실행·회차에 같은 대여소가 두 번 들어가는 것 |
| **`save_frame()`의 선삭제** | 스코프로 먼저 `DELETE` 후 `INSERT` → 재실행해도 중복이 안 쌓임(멱등) |
| **파이프라인 순서** | 앞 단계 산출물을 읽어 뒤 단계를 만들므로 고아 행이 생기기 어려움 |
| **테스트** | `tests/test_db.py`가 스코프·멱등성을, `test_pipeline.py`가 실제 적재를 검사 |

**FK를 안 건 이유**는 이중 기록 구조 때문입니다. CSV가 아직 정본이고, `save_output()`은
DB 기록이 실패해도 파이프라인을 멈추지 않습니다(DB_PLAN 2단계). 단계 하나가 DB 기록에
실패한 상태에서 다음 단계가 FK 위반으로 **연쇄 실패**하면, "DB 문제로 파이프라인을
멈추지 않는다"는 설계가 무너집니다.

> CSV 기록을 걷어내고 DB를 정본으로 삼는 시점(DB_PLAN 5단계)에는 FK를 선언하는 편이
> 낫습니다. 그때는 `runs`를 부모로 두고 `ON DELETE CASCADE`를 걸면 실행 1건을 통째로
> 지우는 일이 `DELETE FROM runs` 한 줄이 됩니다.

---

## 4. 테이블 레퍼런스

### 4-1. `runs` — 실행 이력

산출물이 어느 실행에 속하는지의 기준점. 웹의 `/api/pipeline-runs`가 이걸 읽습니다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `run_label` | TEXT **PK** | 실행 라벨(=파일명의 `{now}`) |
| `period` | TEXT | 순수요 입력 기간 |
| `duration` | TEXT | 시간대 |
| `raw_file` | TEXT | 원천 CSV 경로 |
| `day_type` | TEXT | `weekday` / `holiday`. **파일명에 안 들어가므로 여기가 유일한 기록** |
| `created_at` | TEXT NOT NULL | 기록 시각 |

**단계마다 아는 정보가 다릅니다** — 순수요 단계는 `period`만, 최적화 단계는 `duration`만
압니다. 그래서 `ensure_run()`이 `COALESCE`로 **빈 값만 채우고** 먼저 기록된 값을
덮어쓰지 않습니다. 전체를 확정해 덮어쓸 때만 `record_run()`을 씁니다.

⚠️ **`day_type`만 반대입니다** (`COALESCE(?, day_type)` — 인자가 우선).
요일 구분은 **산출물의 성격을 규정**하므로, 같은 라벨을 다른 요일로 다시 돌리면
산출물이 덮어써지는 만큼 기록도 따라가야 합니다. 안 그러면 주말 산출물에
'평일'이라고 적혀 남습니다.

### 4-2. step0 산출 — 대여소 상태와 수요

<details>
<summary><code>station_stock</code> — TASHU API 재고 스냅샷 (PK: run_label, station_id)</summary>

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `run_label` | TEXT **PK** | |
| `station_id` | TEXT **PK** | |
| `station_name` | TEXT | |
| `parking_info` | TEXT | API 원본의 거치대 설명 문자열 |
| `lat`, `lon` | REAL | TASHU API는 `x_pos`=위도, `y_pos`=경도로 **뒤집혀** 있어 변환 후 저장 |
| `stock` | INTEGER | 현재 재고 |

</details>

<details>
<summary><code>station_info</code> — 대여소 마스터 + 이용량 집계 (PK: run_label, station_id)</summary>

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `run_label`, `station_id` | TEXT **PK** | |
| `station_name` | TEXT | |
| `lat`, `lon` | REAL | |
| `parking_lot` | INTEGER | 거치대 수 |
| `stock` | INTEGER | 재고 |
| `rent_count`, `return_count` | INTEGER | 대여·반납 건수 |
| `total_use_min` | REAL | CSV의 `총 이용시간(분)` |
| `total_use_km` | REAL | CSV의 `총 이용거리(km)` |

`rent_count` 이하는 `rental_history`(또는 원천 CSV)를 집계해 만듭니다.
</details>

<details>
<summary><code>parking_lot</code> — 거치대 수 (PK: run_label, station_id)</summary>

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `run_label`, `station_id` | TEXT **PK** | |
| `lat`, `lon` | REAL | |
| `parking_lot` | INTEGER | 거치대 수 |

</details>

<details>
<summary><code>net_demand</code> — 순수요 (PK: period, date, station_id) ⚠️ period 스코프</summary>

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `period` | TEXT **PK** | 원천 기간. **`run_label`이 아님** |
| `date` | TEXT **PK** | CSV의 `날짜` |
| `station_id` | TEXT **PK** | |
| `net_00` ~ `net_23` | INTEGER × 24 | 시간대별 순수요(대여−반납) |

**평일과 휴일이 모두 들어 있습니다**(1.14.0부터). 어느 쪽으로 계획할지는
`calculate_target_qty`가 `--day-type`으로 고릅니다 — 둘을 한 통계로 섞으면
부호가 반대인 대여소끼리 상쇄됩니다([steps/step0_raw.md](steps/step0_raw.md)).

**세로(long)가 아니라 가로(wide) 24컬럼**입니다. 현재 계산 코드가 wide 형태를 기대해
그대로 보존했습니다. 정규화하려면 `(period, date, station_id, hour, net)` 4컬럼이
정석이지만, 그러면 행 수가 24배가 되고 기존 계산 코드를 전부 고쳐야 합니다.
</details>

<details>
<summary><code>rebalance_plan</code> — 목표 재고와 재배치량 (PK: run_label, duration, station_id)</summary>

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `run_label`, `duration`, `station_id` | TEXT **PK** | |
| `mu`, `sigma` | REAL | 순수요 평균·표준편차 |
| `parking_lot` | INTEGER | 거치대 수 |
| `stock` | INTEGER | 현재 재고 |
| `target_qty` | REAL | `mu + z·sigma` (z=`TARGET_Z`, 근거는 [EXPERIMENTS.md](EXPERIMENTS.md)) |
| `rebal_qty` | INTEGER | **양수 = Drop 필요, 음수 = Pick 가능** |

</details>

### 4-3. step1·2 산출 — 선정·군집·최적화

<details>
<summary><code>pick_drop</code> — 작업 대상 + 군집 (PK: run_label, duration, station_id)</summary>

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `run_label`, `duration`, `station_id` | TEXT **PK** | |
| `station_name` | TEXT | |
| `lat`, `lon` | REAL | |
| `parking_lot`, `stock` | INTEGER | |
| `target_qty` | REAL | |
| `rebal_qty` | INTEGER | 양수=Drop, 음수=Pick |
| `mu`, `sigma` | REAL | |
| `cluster` | INTEGER | K-Medoids 군집 번호. **군집 1개 = 차량 1대** |

`rebalance_plan`에서 `|rebal_qty| > 2`인 대여소를 골라 군집을 붙인 것입니다.
전체 대여소가 아니라 **파이프라인이 실제로 손대는 대여소만** 들어 있습니다.
</details>

<details>
<summary><code>ilp_plan</code> — Pick→Drop 이동 계획 (PK: run_label, duration, pick_station_id, drop_station_id)</summary>

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `run_label`, `duration` | TEXT **PK** | |
| `cluster` | INTEGER | |
| `pick_station_id` | TEXT **PK** | 어디서 빼서 |
| `drop_station_id` | TEXT **PK** | 어디에 넣나 |
| `qty` | INTEGER | 몇 대 |
| `travel_time_sec` | REAL | ILP 기준 이동시간(25km/h) |

CSV의 `hour` 컬럼은 `duration`과 같은 값이라 **저장하지 않습니다**(`TABLES`의 `drop`).
</details>

<details>
<summary><code>vrp_plan</code> — 방문 순서와 구간별 비용 (PK: run_label, duration, seq)</summary>

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `run_label`, `duration` | TEXT **PK** | |
| `seq` | INTEGER **PK** | **행 순서 = 방문 순서** |
| `cluster` | INTEGER | |
| `vehicle_id` | TEXT | `vehicle`을 가리킴(논리적 FK) |
| `from_id`, `from_lat`, `from_lon` | | 출발 |
| `to_id`, `to_lat`, `to_lon` | | 도착 |
| `action` | TEXT | `pick` / `drop` / depot 복귀 |
| `qty` | INTEGER | 이 구간에서 싣거나 내린 대수 |
| `distance_km`, `travel_sec`, `work_sec`, `cum_sec` | REAL | 구간 비용과 누적 |

**이 테이블만 자연키가 없습니다.** 한 경로가 같은 대여소를 여러 번 지날 수 있어
`(대여소, 동작)` 조합이 유일하지 않습니다. 그래서 `save_frame()`이 `seq`가 없으면
행 순서대로 0,1,2…를 붙여 넣습니다 — **정렬 없이 읽으면 경로가 뒤섞입니다.**
반드시 `ORDER BY seq`로 읽으세요.
</details>

### 4-4. step4 산출 — 성과

<details>
<summary><code>metrics</code> — 대여소별 재배치 전후 (PK: run_label, duration, station_id)</summary>

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `run_label`, `duration`, `station_id` | TEXT **PK** | |
| `station_name`, `lat`, `lon`, `cluster` | | |
| `stock`, `mu`, `sigma`, `target_qty`, `rebal_qty` | | 입력값 |
| `new_stock` | INTEGER | 재배치 후 재고 |
| `bf_imbalance`, `af_imbalance` | REAL | 전·후 불균형 |
| `improvement` | REAL | 줄어든 불균형(대) |
| `improvement_rate` | REAL | ⚠️ **"계획 달성률"이지 실제 효과가 아닙니다** |

`improvement_rate`는 `rebal_qty`가 `target_qty − stock`에서 파생되므로 구조적으로
높게 나옵니다. 대외 인용 시 성격을 밝히세요 ([KPI.md](KPI.md)).
</details>

<details>
<summary><code>route_summary</code> — 클러스터별 경로 요약 (PK: run_label, duration, cluster)</summary>

| 컬럼 | 타입 | 원본 한글 컬럼 |
| --- | --- | --- |
| `run_label`, `duration`, `cluster` | **PK** | |
| `visits` | INTEGER | `방문수` |
| `bikes` | INTEGER | `처리대수` |
| `distance_km` | REAL | `총이동거리_km` |
| `travel_min` | REAL | `총이동시간_분` |
| `work_min` | REAL | `총작업시간_분` |
| `total_min` | REAL | `총소요시간_분` = 이동 + 작업 |

`total_min`이 `TIME_BUDGET_MINUTES`(기본 120)를 넘으면 시간 예산 초과입니다.
실측상 **소요시간의 60~75%가 이동**입니다 ([FLEET.md](FLEET.md)).
</details>

<details>
<summary><code>kpi_summary</code> — 회차 1건 = 1행 (PK: run_label, duration)</summary>

흩어진 지표를 한 줄로 모아 **실행 간 비교를 쿼리 하나로** 만드는 테이블입니다.

| 분류 | 컬럼 |
| --- | --- |
| 키·시각 | `run_label`, `duration`, `computed_at` |
| 규모 | `stations`, `clusters`, `vehicles_used`, `bikes_moved` |
| A. 계획 | `avg_improvement_rate`, `pick_improvement_rate`, `drop_improvement_rate`, `target_met_ratio` |
| B. 실측 | `stockout_hours_before`, `stockout_hours_after`(**집행 기준**), `stockout_hours_plan`(계획 기준), `demand_mae` |
| C. 운영 | `total_distance_km`, `max_cluster_minutes`, `avg_cluster_minutes`, `time_budget_minutes`, `time_budget_met`, `vehicle_load_gap`, `depot_returns`, `stations_total`, `station_coverage` |
| D. 효율 | `improvement_per_km`, `bikes_per_minute`, `travel_time_ratio`, `empty_distance_ratio` |
| E. 품질 | `cluster_max_imbalance` |

C·D의 뒤쪽 여섯 개는 1.19.3에서 붙었습니다. `empty_distance_ratio`는 **도착 전
적재량**으로 판단합니다 — 도착 후 적재량으로 세면 차고지에서 첫 대여소로 가는
구간이 '실은 채로 달렸다'가 되어 비율이 낮게 나옵니다.

**계산하지 못한 지표는 그냥 빼고 넘기면 NULL로 남습니다.** `save_kpi()`가 `db.KPI_FIELDS`에
있는 키만 골라 저장하기 때문입니다. `time_budget_minutes`를 값과 함께 기록해 두는 이유는
**판정 기준이 바뀔 수 있어서**입니다 — 나중에 예산을 90분으로 바꾸면 과거 `time_budget_met`가
무슨 기준이었는지 알 수 없게 됩니다.
</details>

<details>
<summary><code>demand_backtest</code> — 월쌍 1건 = 1행 (PK: duration, train_period, test_period, day_type) ⚠️ 실행 스코프 아님</summary>

**파이프라인 실행과 무관한 기록입니다.** 한 달로 만든 `mu`가 **다음 달**을 얼마나
맞히는지 재는 것이라 `run_label`이 없습니다. `tools/backtest_demand.py`가 채우고
`/kpi`의 '수요 예측은 얼마나 맞나' 절이 읽습니다.

| 분류 | 컬럼 |
| --- | --- |
| 키·시각 | `duration`, `train_period`, `test_period`, `day_type`, `computed_at` |
| 그때 쓴 설정 | `z`, `min_demand`, `warmup_days`, `stations` |
| 오차 | `mae`, `rmse`, `bias`(양수면 과소예측) |
| 기준선 | `mae_zero`(늘 0이라고 예측), `mae_global`(전체 평균으로 예측) |
| 커버리지 | `coverage`, `z_for_95` |

- **`day_type`이 키에 들어 있습니다.** 평일과 휴일은 수요 구조가 달라 섞으면
  학습·검증 양쪽이 오염됩니다. 조회도 한쪽만 돌려줍니다.
- 같은 축을 다시 재면 **덮어씁니다.** `z`를 바꿔 재면 이전 값이 사라지므로,
  설정별로 남겨야 하면 그때는 키를 늘려야 합니다.
- 기준선이 두 개인 이유는 **쉬운 기준선만 골라 이겼다고 하지 않기 위해서**입니다.
  화면은 둘 중 더 낮은(더 어려운) 쪽과 겨룹니다.
</details>

### 4-5. 차량 운용

<details>
<summary><code>vehicle</code> — 차량 마스터 (PK: vehicle_id) ⚠️ 전역 스코프</summary>

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `vehicle_id` | TEXT **PK** | `V01` ~ `V21` (`VEHICLE_ID_FORMAT`) |
| `active` | INTEGER NOT NULL DEFAULT 1 | 0이면 배정에서 제외 |
| `note` | TEXT | 비고. `'보유 대수 축소로 제외'`는 예약어 성격 |

**실행에 딸리지 않는 유일한 산출 테이블**입니다. 정비로 빠지면 `active=0`으로 두면 됩니다.

```sql
UPDATE vehicle SET active = 0, note = '정비 입고' WHERE vehicle_id = 'V07';
```

보유 대수를 줄이면 `db.sync_fleet()`이 범위 밖 차량을 `active=0` +
`note='보유 대수 축소로 제외'`로 빼되 **행은 남깁니다**(작업 이력이 지워지면 안 되므로).
다시 늘리면 이 note가 붙은 차량만 복귀하고, 정비 차량은 그대로 둡니다.
</details>

<details>
<summary><code>vehicle_assignment</code> — 회차별 배정과 작업량 (PK: run_label, duration, vehicle_id)</summary>

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `run_label`, `duration`, `vehicle_id` | TEXT **PK** | 어느 회차에 어느 차량 |
| `cluster` | INTEGER NOT NULL | 맡은 군집 |
| `stations` | INTEGER | 방문 대여소 수 |
| `bikes` | INTEGER | **pick 기준** 처리 대수 |
| `distance_km` | REAL | |
| `minutes` | REAL | 이동 + 작업 |

**형평성 추적의 원장**입니다. 로테이션도 이 누적을 보고 정합니다.

> `bikes`를 pick 기준으로 세는 이유: 한 대는 실리고 내려지므로 pick·drop의 `qty`를
> 모두 더하면 2배가 되어 ILP 계획 대수와 어긋납니다. 반면 `minutes`에 들어가는
> 작업시간은 싣기·내리기가 **각각 드는 게 맞으므로** 두 동작을 모두 반영합니다.

인덱스: `idx_assignment_vehicle(vehicle_id)` — 차량 1대의 전체 이력 조회용.
PK 선두가 `run_label`이라 차량으로 거는 조회는 PK 인덱스를 못 씁니다.
</details>

### 4-6. `rental_history` — 원천 대여이력 (PK: id) ⚠️ period 스코프, 대용량

원본 CSV 12개 컬럼을 **그대로 미러링**합니다. 60만 행 규모입니다.

| 컬럼 | 타입 | 원본 한글 컬럼 |
| --- | --- | --- |
| `id` | INTEGER **PK** AUTOINCREMENT | (합성 키) |
| `period` | TEXT NOT NULL | 적재 시 부여 |
| `bike_no` | TEXT | `자전거번호` |
| `rent_at` | TEXT NOT NULL | `대여일시` |
| `rent_station` | TEXT NOT NULL | `대여_대여소ID` |
| `rent_station_name` | TEXT | `대여_대여소명` |
| `rent_lat`, `rent_lon` | REAL | `대여_X좌표`(위도), `대여_Y좌표`(경도) |
| `return_at` | TEXT | `반납일시` |
| `return_station` | TEXT | `반납_대여소ID` |
| `return_lat`, `return_lon` | REAL | `반납_X좌표`, `반납_Y좌표` |
| `use_min` | **NUMERIC** | `이용시간(분)` |
| `use_km` | **NUMERIC** | `이용거리(km)` |

세 가지가 의도적입니다.

1. **대여소명·좌표가 `station_info`와 중복인데 남겨 뒀습니다.** 처음엔 중복이라 뺐는데,
   `api_to_info.py`가 **대여소 정보를 만들 때 이 값을 대여이력에서 집계**하고 있었습니다.
   빼면 산출물이 달라집니다.
2. **`use_min`·`use_km`은 `REAL`이 아니라 `NUMERIC` 친화도**입니다. `REAL`로 두면
   원본이 정수인 `이용시간(분)`이 실수가 되어 CSV 경로와 dtype이 달라집니다
   (동일성 테스트가 잡아낸 실제 결함).
3. **`rent_at`은 `YYYY-MM-DD HH:MM:SS`로 정규화**해 저장합니다. TEXT 컬럼이라
   문자열 비교로 범위 조회가 되어야 하기 때문입니다.

읽을 때는 `db.read_rental_source(period, csv_path=..., columns=[...])`를 쓰세요
(DB 우선, 없으면 CSV 폴백). **필요한 컬럼만 지정하세요** — 컬럼 수가 로드 시간을
좌우합니다([DB_PLAN.md](DB_PLAN.md#4단계--대여이력-적재와-step0-전환-완료)의 성능 측정).

---

## 5. 인덱스

| 인덱스 | 대상 | 왜 |
| --- | --- | --- |
| `idx_rental_period` | `rental_history(period)` | 1년치에서 한 달만 꺼내는 것이 주 사용 패턴 |
| `idx_rental_rent_at` | `rental_history(rent_at)` | 시간 범위 조회 |
| `idx_rental_station` | `rental_history(rent_station)` | 대여소별 집계 |
| `idx_pick_drop_cluster` | `pick_drop(run_label, duration, cluster)` | 군집으로 좁히는 조회. PK가 `(…, station_id)`라 `cluster`는 PK 인덱스로 못 탐 |
| `idx_assignment_vehicle` | `vehicle_assignment(vehicle_id)` | 차량 1대의 전체 이력. PK 선두가 `run_label`이라 필요 |
| `idx_metrics_run` | `metrics(run_label, duration)` | **PK 자동 인덱스와 중복** (아래 참고) |

복합 PK를 선언하면 SQLite가 자동으로 `sqlite_autoindex_<table>_1`을 만들고,
**왼쪽 접두사 조회에 그대로 쓰입니다.** `metrics`의 PK는
`(run_label, duration, station_id)`이므로 `(run_label, duration)` 조회는 이미 커버됩니다.
`EXPLAIN QUERY PLAN`으로 확인한 결과입니다.

```text
-- idx_metrics_run 이 있을 때
SEARCH metrics USING INDEX idx_metrics_run (run_label=? AND duration=?)
-- 지운 뒤
SEARCH metrics USING INDEX sqlite_autoindex_metrics_1 (run_label=? AND duration=?)
```

같은 계획으로 떨어집니다. **지워도 성능이 같고 쓰기 비용만 줄지만**, 실측 이득이
미미하고 스키마 변경은 위험 대비 효용이 낮아 지금은 그대로 뒀습니다
([TODO.md](TODO.md)에 기록).
`idx_pick_drop_cluster`는 `cluster`가 PK에 없으므로 **중복이 아닙니다.**

---

## 6. 일부러 정규화하지 않은 곳

이 DB는 3정규형을 목표로 하지 않습니다. **각 산출 테이블이 그 단계의 CSV와 1:1로
대응하는 것**이 우선입니다. CSV가 아직 정본이고, 두 경로의 산출물이 같아야 하기
때문입니다(`tests/test_rentals.py`의 동일성 테스트).

| 중복 | 어디에 | 왜 남겼나 |
| --- | --- | --- |
| `station_name`, `lat`, `lon` | `station_stock`, `station_info`, `pick_drop`, `metrics`, `rental_history` | 대여소 마스터가 **실행별 스냅샷**이다. 대여소가 신설·폐지되므로 "그때 그 좌표"가 필요하다 |
| `mu`, `sigma`, `target_qty`, `stock` | `rebalance_plan`, `pick_drop`, `metrics` | 각 단계 CSV를 그대로 옮긴 것. 조인 없이 한 테이블만 읽어도 그 단계를 재현할 수 있다 |
| `cluster` | `pick_drop`, `ilp_plan`, `vrp_plan`, `metrics`, `route_summary`, `vehicle_assignment` | 조인 축이라 의도적으로 반복 |

**대여소 마스터를 실행과 무관한 전역 테이블로 뽑는 것**은 검토할 만하지만, 그러면
"2026년 5월 실행 시점의 좌표"를 잃습니다. 이력 테이블(SCD Type 2)이 필요해지고,
지금 규모에 비해 과합니다.

---

## 7. 자주 쓰는 쿼리

```sql
-- 실행 간 개선률 비교 (run_label 도입의 목적)
SELECT run_label, duration, ROUND(AVG(improvement_rate), 3) AS rate
FROM metrics GROUP BY run_label, duration ORDER BY run_label DESC;

-- 최신 실행 라벨
SELECT run_label FROM runs ORDER BY run_label DESC LIMIT 1;

-- 시간 예산을 넘긴 회차·클러스터와 담당 차량
SELECT r.run_label, r.duration, r.cluster, r.total_min, a.vehicle_id
FROM route_summary r
LEFT JOIN vehicle_assignment a
       ON a.run_label = r.run_label AND a.duration = r.duration
      AND a.cluster   = r.cluster
WHERE r.total_min > 120
ORDER BY r.total_min DESC;

-- 차량별 누적 부하 (한 번도 안 나간 차량도 0으로)
SELECT v.vehicle_id, COUNT(a.vehicle_id) AS rounds,
       ROUND(COALESCE(SUM(a.minutes), 0), 1) AS minutes
FROM vehicle v
LEFT JOIN vehicle_assignment a ON a.vehicle_id = v.vehicle_id
WHERE v.active = 1
GROUP BY v.vehicle_id ORDER BY minutes ASC;

-- 어느 대여소를 어느 차가 맡았나 (대여소 → 군집 → 차량)
SELECT p.station_id, p.station_name, p.rebal_qty, p.cluster, a.vehicle_id
FROM pick_drop p
LEFT JOIN vehicle_assignment a
       ON a.run_label = p.run_label AND a.duration = p.duration
      AND a.cluster   = p.cluster
WHERE p.run_label = ? AND p.duration = ?;

-- 경로는 반드시 seq 순서로
SELECT seq, cluster, from_id, to_id, action, qty
FROM vrp_plan WHERE run_label = ? AND duration = ? ORDER BY seq;

-- 한 기간의 대여 건수 (인덱스 사용)
SELECT COUNT(*) FROM rental_history WHERE period = '25년 11월';
```

파이썬에서는 헬퍼를 쓰세요.

```python
import db
with db.session() as conn:
    print(db.list_runs(conn))
    latest = db.load_frame(conn, "metrics")                 # 최신 실행분
    old    = db.load_frame(conn, "metrics", run_label="2026-05-21 18")
    kpi    = db.load_kpi(conn)
```

---

## 8. 한글 → ASCII 컬럼 변환표

CSV의 한글 컬럼은 DB에서 ASCII로 바뀝니다. 변환표는 `db.TABLES`(산출물)와
`db.RENTAL_COLUMNS`(대여이력)에 모여 있습니다.

| 테이블 | CSV 컬럼 | DB 컬럼 |
| --- | --- | --- |
| `station_info` | `총 이용시간(분)` | `total_use_min` |
| `station_info` | `총 이용거리(km)` | `total_use_km` |
| `net_demand` | `날짜` | `date` |
| `route_summary` | `방문수` / `처리대수` | `visits` / `bikes` |
| `route_summary` | `총이동거리_km` / `총이동시간_분` | `distance_km` / `travel_min` |
| `route_summary` | `총작업시간_분` / `총소요시간_분` | `work_min` / `total_min` |
| `rental_history` | 12개 컬럼 전체 | `db.RENTAL_COLUMNS` 참고 |

`read_rental_source()`는 **원본 한글 컬럼명 그대로 돌려줍니다**(`RENTAL_COLUMNS_REVERSED`).
기존 계산 코드를 안 고치기 위한 것입니다.

---

## 9. 스키마를 바꿀 때

1. **`db.py`의 `SCHEMA`에 DDL 추가.** `CREATE TABLE IF NOT EXISTS`를 지키세요 —
   `init_schema()`가 연결할 때마다 실행됩니다. **컬럼 추가라면 여기까지가 전부입니다** —
   기존 DB에는 `migrate_schema()`가 자동으로 채웁니다(아래 참고).
2. **`db.TABLES`에 `TableSpec` 등록** (산출물 테이블인 경우). `scope`로 멱등성 기준을,
   `rename`으로 한글 컬럼 변환을, `drop`으로 버릴 컬럼을 지정합니다.
3. **단계 스크립트에 `db.save_output(...)` 호출 추가.** 빠뜨리면 테스트가 잡습니다.
4. **테스트 추가** — `tests/test_db.py`에 스코프·멱등성, `tests/test_pipeline.py`에 적재 검사.
5. **이 문서와 [DB_PLAN.md](DB_PLAN.md)를 갱신**하고 [버전관리.md](버전관리.md)에 이유를 남기세요.

### 마이그레이션 — 컬럼 추가는 자동, 나머지는 수동

`CREATE TABLE IF NOT EXISTS`는 **이미 있는 테이블을 고쳐 주지 않습니다.** 그래서
`init_schema()`가 `migrate_schema()`를 함께 부릅니다 — SCHEMA에 있는데 실제 테이블에
없는 컬럼을 `ALTER TABLE ... ADD COLUMN`으로 채웁니다.

```text
[안내] DB 스키마에 컬럼을 추가했습니다: kpi_summary.stockout_hours_before, ...
```

기대 컬럼은 **DDL을 파싱하지 않고** 임시 메모리 DB에 `SCHEMA`를 만들어 SQLite에게
`PRAGMA table_info`로 물어봅니다(`expected_columns()`). SCHEMA 문자열을 고쳐도
따로 손볼 곳이 없습니다. 비용은 연결당 **0.3ms 수준**(연결 자체가 2.5ms)이라
`session()`마다 돌려도 문제되지 않습니다.

**이것이 고친 실제 결함**: `kpi_summary`의 `stockout_hours_*`·`demand_mae`는 1.10.0·1.11.0에서
추가됐습니다. 그 이전에 DB를 만든 사용자는 컬럼이 없어 `save_kpi()`가 `no such column`으로
실패했는데, step4가 예외를 잡아 경고만 남기므로 **조용히 지표가 안 쌓였습니다.**

#### 자동으로 하지 않는 것

| 변경 | 왜 자동이 아닌가 |
| --- | --- |
| 컬럼 **이름 변경·삭제·타입 변경** | 데이터를 잃을 수 있어 사람이 판단할 일입니다. `migrate_schema()`는 **추가만** 합니다 |
| **기본값 없는 NOT NULL** 컬럼 추가 | SQLite가 막습니다. 조용히 건너뛰면 예전과 똑같이 실패하므로 **경고로 알립니다** |
| `rental_history` | `MIGRATION_EXCLUDED`로 빼 뒀습니다 (아래) |

`rental_history`는 `_ensure_rental_schema()`가 컬럼 구성이 다르면 **지우고 다시 만듭니다** —
원천 CSV에서 재적재할 수 있으니 그게 맞는 처리입니다. 여기에 빈 컬럼을 붙이면
**재적재가 필요한 상태를 정상으로 착각**하게 되므로 자동 추가에서 제외했습니다.

```text
[안내] rental_history 스키마가 달라 다시 만듭니다(원천에서 재적재 필요).
```

---

## 관련 문서

| 문서 | 내용 |
| --- | --- |
| [DB_PLAN.md](DB_PLAN.md) | **왜** SQLite인가, 이관 단계, 성능 실측(CSV vs DB) |
| [FLEET.md](FLEET.md) | `vehicle`·`vehicle_assignment`를 쓰는 로테이션·형평성 규칙 |
| [KPI.md](KPI.md) | `kpi_summary` 각 지표의 정의와 해석 주의점 |
| [PROJECT_PIPELINE.md](PROJECT_PIPELINE.md) | 어느 단계가 어느 테이블을 만드는가 |
| [TESTING.md](TESTING.md) | CSV·DB 동일성 테스트와 DB 격리 장치 |
| [WEBAPP.md](WEBAPP.md) | 웹 API가 DB를 읽는 경로(`store.py`) |
