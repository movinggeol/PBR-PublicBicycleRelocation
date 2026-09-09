# DB 도입 계획 — SQLite 채택 결정

> 2026-08-07 결정. 현재 CSV 파일 기반 데이터 관리를 SQLite 단일 DB로 이관한다.
> **1~4단계 완료** (저장소 → 이중 기록 → 웹 API 전환 → 대여이력 적재, 1.4.0~1.7.0).
> **5단계 진행 중**: 조회 폴백 제거는 끝났고(1.26.165 — 조회는 DB가 정본),
> 단계 간 배선도 step1·step4는 DB로 옮겼습니다(1.26.164). 남은 것은 **step0·step2가
> 서로 CSV로 주고받는 배선**과 그 뒤의 `to_csv` 제거입니다 — 아래 5단계 절 참고.

## 결정 요약 (trade-off)

| 항목 | 내용 |
| --- | --- |
| 상황 | 단독 개발·파이썬 단독 스택. 대여이력 약 60만 행 + 산출 테이블 여러 개를 `{now}` 라벨이 박힌 CSV 파일명으로 주고받는 중. 웹 대시보드는 "수정시각이 가장 최근인 파일" 휴리스틱으로 최신 산출물을 찾음 |
| 대안 | SQLite / PostgreSQL(+TimescaleDB) / MySQL / MongoDB / InfluxDB |
| 선택 기준 | 운영 부담(설치·서버·계정), 데이터 규모 적합성, pandas 연동, 동시성 요구, 이식성 |
| 결정 | **SQLite** (`data/bike_system.db`, WAL 모드) |
| 결과 | 표준 라이브러리만으로 동작(추가 설치 0), 파일 하나로 백업·이동, 파이프라인(쓰기 1개)+웹(읽기)의 현재 동시성 구조에 정확히 부합 |

## 기각한 대안과 이유

- **시계열 DB (InfluxDB·TimescaleDB 등)** — 초당 수천 건 유입되는 센서·모니터링용.
  타슈 대여이력은 월 1회 배치 CSV라 이점이 없고 운영 부담만 늘어남.
  "시간 컬럼에 인덱스를 건 SQLite"로 충분.
- **MongoDB 등 NoSQL** — 데이터가 전부 정형이고 대여소 ID 기준 JOIN이 핵심 연산.
  NoSQL은 JOIN을 수작업으로 만들게 되어 역행.
- **PostgreSQL/MySQL 즉시 도입** — 단일 사용자·단일 PC 환경에서는 서버 설치·계정·백업
  관리 비용을 회수할 곳이 없음. 단, 아래 "전환 신호" 발생 시 PostgreSQL로 이관.

## PostgreSQL로 전환해야 하는 신호

1. 웹서비스를 외부에 공개해 **여러 사용자가 동시에 쓰기** 시작할 때
2. TASHU API를 **상시 수집**(예: 10분마다 재고 스냅샷)으로 바꿔 쓰기가 잦아질 때
3. 다른 PC/서버에서 **원격 접속**이 필요할 때

**전환 비용에 대한 재검토 (1단계 구현 시 결정 변경)**: 애초에 SQLAlchemy를 얇게 쓰기로
했으나, 실제로 구현해 보니 필요가 없었다. pandas의 `to_sql`/`read_sql`은 sqlite3 연결과
SQLAlchemy 엔진 모두에서 동일하게 동작하므로, 데이터 접근 코드는 어느 쪽이든 같다.
바뀌는 곳은 연결 생성뿐이라 `db.connect()` 하나로 격리했다. 지금은 표준 라이브러리만으로
돌아가고, PostgreSQL 전환 시점에 그 함수만 엔진 팩토리로 교체하면 된다.

## 스키마 (1단계에서 구현됨 — `db.py`)

> **ERD·컬럼 레퍼런스·조인 예시는 [DB_SCHEMA.md](DB_SCHEMA.md)에 있습니다.**
> 아래는 이관 계획 관점의 개요입니다.

```text
data/bike_system.db  (WAL 모드)
├── runs               (실행 이력: run_label, period, duration, raw_file, created_at)
├── station_stock      (TASHU API 재고 스냅샷)          ← step0 산출
├── station_info       (대여소 마스터)                  ← step0 산출
├── parking_lot        (거치대 수)                      ← step0 산출
├── net_demand         (순수요, period 스코프)          ← step0 산출
├── rebalance_plan     (rebal_qty)                     ← step0 산출
├── pick_drop          (Pick/Drop 후보 + 클러스터)      ← step1 산출
├── ilp_plan           (Pick→Drop 이동 계획)           ← step2 산출
├── vrp_plan           (방문 순서 + 거리·시간, seq 보존) ← step2 산출
├── metrics            (재배치 전후 불균형 개선)         ← step4 산출
├── route_summary      (클러스터별 이동거리·소요시간)     ← step4 산출
├── vehicle            (차량 마스터, 전역 스코프)         ← 1.8.0에서 추가
├── vehicle_assignment (회차별 배정·작업량)              ← step2 vrp 산출, 1.8.0
├── kpi_summary        (회차 1건 = 1행 지표)            ← step4 산출, 1.9.3
└── rental_history     (대여이력 원본, 대여일시 인덱스)   ← raw CSV (4단계에서 적재)
```

> 위 3개(`vehicle`·`vehicle_assignment`·`kpi_summary`)는 이관 계획 이후에 추가된
> 테이블입니다. 이관 단계와 무관하게 [FLEET.md](FLEET.md)·[KPI.md](../분석/KPI.md)에서 생겼습니다.

설계 시 실제 산출물 CSV의 컬럼을 뽑아 맞췄습니다. 주의할 점 3가지:

- **한글 컬럼은 ASCII로 변환**해 저장합니다 (`총 이용시간(분)` → `total_use_min`,
  `방문수` → `visits` 등). 변환표는 `db.TABLES`에 모여 있습니다.
- **`net_demand`는 `period` 스코프**입니다. 순수요는 원천 데이터 기간에만 의존하고
  분석 시점(`now`)과 무관하기 때문입니다.
- **`vrp_plan`은 `seq` 컬럼으로 방문 순서를 보존**합니다. 같은 대여소를 여러 번
  방문할 수 있어 자연키가 없습니다.

**핵심 설계: `run_label` 컬럼.** 지금 파일명에 박혀 있는 `{now}` 라벨을 산출 테이블의
컬럼으로 옮긴다(`run_label TEXT`, duration도 컬럼화). 효과:

- 실행 이력 간 비교("지난주 vs 이번주 개선률")가 쿼리 한 줄이 됨
- 웹 API의 최신 파일 휴리스틱 → `ORDER BY run_label DESC LIMIT 1`
- 파일명 규약(`이름{duration} ({now}).csv`) 의존성이 사라짐

초기 테이블 DDL 초안은 [메모.md](../기록/메모.md)의 SQLite 섹션 참고
(rental_history / station_info / station_workload / inventory_movement / visit_sequence —
실제 이관 시 위 스키마 기준으로 재정리).

## 작업 단계

- [x] 0. 선행: Python 설치 → 파이프라인 검증 → 커밋 (1.2.1)
- [x] 1. `db.py` 공통 모듈 — 연결(WAL), 스키마, `save_frame`/`load_frame`, run_label 규약.
      `tools/csv_to_db.py`로 기존 CSV 산출물을 적재할 수 있고, 테스트 17개로 검증됨
      (그중 1개는 파이프라인이 실제로 만든 CSV를 적재해 스키마 적합성을 확인)
- [x] 2. **이중 기록** — 9개 단계가 CSV와 DB에 모두 쓴다. CSV가 아직 정본이며,
      DB 기록 실패는 경고만 남기고 파이프라인을 멈추지 않는다.
      테스트 11개로 각 테이블 적재와 CSV 대조를 검증 (아래 "2단계" 참고)
- [x] 3. **webapp API를 DB 조회로 전환** — `webapp/store.py`가 DB를 먼저 보고,
      비어 있으면 CSV로 폴백한다. `?run_label=`로 과거 실행분 조회 가능,
      `/api/pipeline-runs`로 실행 이력 노출. 테스트 9개 (아래 "3단계" 참고)
- [x] 4. **대여이력 원본 적재 + step0 전환** — `tools/load_rentals.py`로 적재하면
      `raw_to_net.py`·`api_to_info.py`가 CSV 대신 DB에서 읽는다.
      CSV·DB 경로의 산출물이 완전히 같음을 테스트로 보증 (아래 "4단계" 참고)
- [ ] 5. 실행 이력 비교 기능 (run_label 간 개선률 비교 API·화면)

## 1단계 사용법

```powershell
# 기존 CSV 산출물을 DB로 적재 (project_config의 now/period/duration 사용)
python tools/csv_to_db.py
python tools/csv_to_db.py --list        # 적재된 실행 이력 확인
```

```python
import db
with db.connect() as conn:
    db.init_schema(conn)
    latest = db.load_frame(conn, "metrics")            # 최신 실행분
    old    = db.load_frame(conn, "metrics", run_label="2026-05-21 18")
```

`run_label` 도입의 효과 — 실행 간 비교가 쿼리 한 줄이 됩니다:

```sql
SELECT run_label, AVG(improvement_rate) FROM metrics GROUP BY run_label;
```

## 2단계 — 이중 기록 (완료)

파이프라인을 돌리면 CSV와 DB에 **동시에** 기록됩니다. 별도 적재 명령이 필요 없습니다.

| 단계 | 스크립트 | 테이블 |
| --- | --- | --- |
| 0 | `tashu_api.py` | `station_stock` |
| 0 | `extract_parking_lot.py` | `parking_lot` |
| 0 | `api_to_info.py` | `station_info` |
| 0 | `raw_to_net.py` | `net_demand` (period 스코프) |
| 0 | `calculate_target_qty.py` | `rebalance_plan` |
| 1 | `top_st_clustering.py` | `pick_drop` |
| 2 | `ilp.py` | `ilp_plan` |
| 2 | `vrp.py` | `vrp_plan` |
| 4 | `imbalance.py` | `metrics`, `route_summary` |

각 스크립트는 CSV를 저장한 직후 한 줄을 더 부릅니다.

```python
df.to_csv(out_path, index=False)                                     # 기존 (정본)
db.save_output("pick_drop", df, run_label=now, duration=duration)    # 추가
```

### 이 단계의 설계 원칙

- **CSV가 아직 정본입니다.** `save_output()`은 DB 기록이 실패해도 예외를 올리지 않고
  경고만 출력합니다. 전환기에 DB 문제로 파이프라인이 멈추면 안 되기 때문입니다.
  대신 조용히 넘어가지 않도록 테스트가 각 테이블의 적재를 검사합니다.
- **멱등성**: `save_frame()`이 같은 라벨의 기존 행을 먼저 지우므로, 파이프라인을
  재실행해도 중복이 쌓이지 않습니다.
- **`runs` 테이블은 누적 갱신**: 단계마다 아는 정보가 다릅니다(순수요 단계는 `period`만,
  최적화 단계는 `duration`만). `ensure_run()`이 `COALESCE`로 빈 값만 채워
  먼저 기록된 값을 덮어쓰지 않습니다.
- **DB 경로는 `PBR_DB_PATH`로 재정의**할 수 있습니다. 테스트가 실제
  `data/bike_system.db`를 오염시키지 않도록 이 변수로 임시 파일을 가리킵니다.

### 확인 방법

```python
import db
with db.session() as conn:
    print(db.list_runs(conn))
    print(db.load_frame(conn, "metrics").head())
```

## 3단계 — 웹 API를 DB 조회로 전환 (완료)

웹 API가 파일 대신 DB를 읽습니다. **"최신"의 의미가 바뀐 것**이 핵심입니다.

| | 이전 | 현재 |
| --- | --- | --- |
| 최신 판단 기준 | 파일 **수정시각** | DB의 **`runs.created_at`**(`db.latest_label()`) |
| 과거 실행 조회 | 불가능 | `?run_label=` 지정 |
| 실행 종류 고르기 | 불가능 | `kinds=("plan",)` — 계획만 고른다 |
| 취약점 | 파일 복사·재저장으로 순서가 뒤바뀜 | 라벨이 `runs`에 없는 옛 자료는 가장 오래된 것으로 친다 |

🔴 **3단계를 처음 쓸 때는 `MAX(run_label)`이었고, 그것이 버그였습니다.**
`obs-cmp-…`는 `'o' > '2'`라 날짜 라벨을 전부 이겨서 실험이 영영 '최신'이 됐습니다.
1.26.125에서 `runs.created_at` 기준으로 바꿨습니다 — 자세한 것은
[DB_SCHEMA.md](DB_SCHEMA.md) 2장과 [WEBAPP.md](WEBAPP.md)에 있습니다.

```
GET /api/metrics                              # 최신 실행
GET /api/metrics?run_label=2026-05-21%2018    # 특정 실행
GET /api/pipeline-runs                        # 실행 이력 목록
```

응답에 `run_label`과 `source`(db|csv)가 함께 담겨, 어느 실행분을 어디서 읽었는지
확인할 수 있습니다. 대시보드 첫 화면에도 실행 이력 표가 추가되었습니다.

### 설계 결정

- **`webapp/store.py` 신설** — 데이터 조회(DB 우선, CSV 폴백)를 한 곳에 모았습니다.
  기존 `catalog.py`는 **파일**(지도 HTML, CSV 다운로드) 담당으로 역할이 갈립니다.
  지도는 DB에 넣을 대상이 아니라 `/maps`·`/data` 페이지는 계속 파일 기반입니다.
- **CSV 폴백을 남긴 이유, 그리고 없앤 이유 (1.26.165)** — 이중 기록 이전 산출물을
  가진 사용자가 DB를 채우기 전에도 대시보드를 쓰게 하려던 장치였습니다. 그런데
  폴백이 고르는 방법은 **파일 수정시각**이라, 실험 산출물(`obs-cmp-…`)을 계획
  자리에 내놓을 수 있었습니다 — DB 경로가 `kinds=("plan",)`로 막는 바로 그 사고
  (1.26.125)를 폴백은 막지 못했습니다. 실측에서 `metrics`·`route_summary`·`ilp_plan`
  세 표의 폴백 후보가 실제로 `obs-cmp-1520`이었습니다. 지금은 DB가 다섯 표를 모두
  답하므로(87·66·106·87·14행) 폴백은 이득 없이 위험만 남아 걷어냈습니다.
  **옛 산출물 파일은 그대로 있고 `/files`에서 내려받습니다.**
- **엔드포인트 이름 충돌 회피** — `/api/runs/{id}`는 이미 "웹에서 띄운 작업의 상태"를
  뜻합니다. DB의 실행 이력은 다른 개념이라 `/api/pipeline-runs`로 분리했습니다.
- **테스트 격리** — API가 DB를 조회하게 되면서 라우트를 한 번 부르기만 해도 실제
  `data/bike_system.db`가 생성됩니다. `tests/conftest.py`의 autouse fixture가
  모든 테스트에 `PBR_DB_PATH`를 임시 경로로 강제합니다.

## 4단계 — 대여이력 적재와 step0 전환 (완료)

원천 대여이력(60만 행 규모)을 `rental_history`에 적재하고, step0 두 스크립트가
CSV 대신 DB에서 읽도록 바꿨습니다.

```powershell
python tools/load_rentals.py            # 적재
python tools/load_rentals.py --status   # 기간별 적재 현황
```

적재해 두면 `raw_to_net.py`·`api_to_info.py`가 자동으로 DB를 씁니다
(적재돼 있지 않으면 CSV로 폴백하므로 기존 사용자도 그대로 동작).

### 성능 — 측정해 보니 예상과 달랐습니다

"DB로 옮기면 빨라진다"고 예상했지만, **단일 기간만 다룰 때는 오히려 CSV가 빠릅니다.**
pandas의 C CSV 파서가 매우 빠르고, SQLite → Python 객체 → DataFrame 경로에는
행 단위 변환 비용이 있기 때문입니다. 594,000행(93MB) 합성 데이터 기준:

| 시나리오 | CSV | DB | 결과 |
| --- | --- | --- | --- |
| 한 달치 저장소에서 4컬럼 로드 | 0.89초 | 0.98초 | 비슷 |
| 한 달치 저장소에서 9컬럼 로드 | 0.99초 | 1.79초 | **CSV 1.8배 빠름** |
| **3개월치 저장소에서 한 달만 로드** | 2.43초 | 0.99초 | **DB 2.45배 빠름** |

즉 **저장소가 필요한 양보다 클 때 DB가 이깁니다.** 실제 운용은 이쪽입니다 —
`concat_1year_file.py`로 1년치를 병합해 두고 한 달씩 분석하므로, 12개월이 든
DB에서 한 달만 인덱스로 읽는 편이 훨씬 유리합니다. 반대로 딱 한 달치 파일만
가지고 돌린다면 DB 전환의 속도 이득은 없습니다.

적재 자체는 594,000행에 **6.1초**(약 97,000행/초)입니다.

### 왜 느린가 — 비용을 분리해서 측정했습니다

"DB가 느리다"는 관찰만으로는 대응할 수 없어, 어디에 시간이 쓰이는지 나눠 재봤습니다.
150,000행 × 12컬럼 기준입니다.

| 단계 | 시간 | 비중 |
| --- | --- | --- |
| SQLite 스캔만 (`COUNT(*)`) | 0.008초 | **1.4%** |
| + 셀마다 Python 객체 생성 (`fetchall`) | 0.374초 | **66.3%** |
| + DataFrame 변환 (`read_sql`) | 0.182초 | **32.3%** |
| **DB 합계** | **0.564초** | |
| **CSV (`read_csv`)** | **0.244초** | |

**데이터를 읽는 일 자체는 전체의 1.4%입니다.** 나머지 98.6%는 SQLite에서 Python으로
값을 건네는 비용입니다.

- **DB 경로는 셀 하나당 Python 객체를 만듭니다.** 150,000행 × 12컬럼 = 180만 개의
  PyObject이고, 각각 메모리 할당과 참조 카운트가 붙습니다. 그렇게 만든 튜플 리스트를
  pandas가 다시 훑어 컬럼형 배열로 옮깁니다 — 두 번 일하는 셈입니다.
- **CSV 경로는 그 과정을 건너뜁니다.** pandas의 C 파서는 파일을 블록 단위로 읽어
  미리 할당한 타입 배열에 값을 직접 써 넣습니다. 셀마다 객체를 만들지 않고,
  C 레벨 한 번의 패스로 끝나며 GIL도 놓습니다.

이것이 앞 표의 **컬럼 수 민감도**를 설명합니다. DB 비용은 만들어야 할 셀 개수에
정비례하고(4→0.98초, 9→1.79초, 12→2.23초), CSV는 바이트를 훑는 비용이 지배적이라
완만합니다(0.89 → 0.99 → 1.14초).

부수적인 이유 둘:

- **행 지향 저장이라 컬럼을 골라도 I/O가 줄지 않습니다.** SQLite는 한 행을 붙여
  저장하므로 12개 중 4개만 뽑아도 모든 행의 페이지를 훑고 나머지를 버립니다.
  컬럼 선택으로 얻은 이득은 I/O가 아니라 **객체 생성이 줄어서** 생긴 것입니다.
  (Parquet 같은 컬럼 지향 포맷이었다면 실제로 안 읽었을 것입니다.)
- **인덱스는 전체 스캔에 도움이 안 됩니다.** 인덱스는 많은 것 중 적은 것을 찾을 때
  쓰는 물건이라, 한 기간의 모든 행을 달라는 요청에는 낄 자리가 없습니다.
  3개월치에서 한 달만 읽을 때 DB가 이긴 것이 인덱스가 나머지를 **건너뛴** 경우입니다.

### 그래서 지금 최적화할 것인가 — 아닙니다

정리하면 **SQLite를 잘 못하는 일(대용량 벌크 로딩)에 쓰고 있는 것**이고, 그 강점
(선택적 조회·조인·부분 로딩)은 아직 안 쓰고 있습니다. 제대로 쓰려면 계산을 SQL로
밀어 넣어 집계 결과만 경계를 넘게 해야 합니다. 실제로 `raw_to_net`의 groupby를
SQL `GROUP BY`로 시험하니 0.346초로 전체 fetch(0.564초)보다 빨랐습니다. 다만 그
케이스는 그룹 키가 (날짜×대여소×시간)이라 결과가 원본의 56%밖에 줄지 않아 이득이
제한적이었습니다.

**그럼에도 지금 손댈 일은 아니라고 봅니다.** 로딩 차이는 1~2초인데 step1 군집 조정이
실데이터에서 5분 이상 걸립니다. 최적화 우선순위는 이렇습니다.

1. **step1 군집 조정 증분 계산** (5분+ → 수십 초) ← 진짜 병목
2. DB 집계 푸시다운 (1~2초 절약)

따라서 4단계의 실질적 가치는 속도가 아니라 **1년치를 한 곳에 두고 필요한 기간만
꺼내 쓰는 구조**와 **ad-hoc 쿼리 가능성**입니다.

### 설계 결정

- **`rental_history`는 원본 CSV 12개 컬럼을 그대로 미러링**합니다. 처음에는
  대여소명·좌표를 "station_info와 중복"이라고 판단해 뺐는데, 확인해 보니
  `api_to_info.py`가 **대여소 정보를 만들 때 이 값들을 대여이력에서 집계**하고
  있었습니다. 빼면 산출물이 달라지므로 되돌렸습니다.
- **`use_min`·`use_km`은 `NUMERIC` 친화도**입니다. `REAL`로 두면 원본이 정수인
  `이용시간(분)`이 실수로 바뀌어 CSV 경로와 dtype이 달라집니다(동일성 테스트가 잡아냄).
- **적재 중에는 인덱스를 지웠다가 끝나고 다시 만듭니다.** 인덱스가 걸린 채로
  대량 삽입하면 매 행마다 B-Tree를 갱신해 훨씬 느립니다.
- **날짜는 `YYYY-MM-DD HH:MM:SS`로 정규화**해 저장합니다. TEXT 컬럼이라
  문자열 비교로 범위 조회가 되어야 하기 때문입니다.
- **호출부가 필요한 컬럼만 요청**합니다(`read_rental_source(columns=[...])`).
  전체 12컬럼을 읽을 이유가 없고, 컬럼 수가 로드 시간에 직접 영향을 줍니다.

### 검증

가장 중요한 테스트는 **CSV 경로와 DB 경로의 산출물이 한 행도 다르지 않은지**입니다.
같은 스크립트를 빈 DB(→CSV 폴백)와 적재된 DB로 각각 실행해
`pd.testing.assert_frame_equal`로 비교합니다. 이 테스트가 위의 dtype 문제를 잡았습니다.

## 5단계 — CSV를 걷어낸다 (진행 중)

> 재현: `python tools/check_consistency.py` · `python -m pytest tests/test_pipeline.py`

CSV는 **산출물이면서 단계 간 배선**이기도 했습니다. 그래서 `to_csv`부터 지우면
파이프라인이 먼저 끊깁니다(1.26.163 조사). 순서를 정해 두고 하나씩 걷습니다.

| | 무엇 | 상태 |
| --- | --- | --- |
| ① | 단계 간 배선을 `db.load_frame()`으로 | ✅ step4·step1 완료 (1.26.164) |
| ② | `redraw_maps` 색인에 DB를 더한다 | ✅ 완료 (1.26.164) |
| ③ | `store.CSV_FALLBACK` 제거 | ✅ 완료 (1.26.165) |
| ④ | `to_csv` 9곳 제거 | ⏸ **아직** — 아래 이유 |

### ④를 아직 못 하는 이유 — step0·step2가 서로 CSV로 읽습니다

①에서 step4·step1은 끊었지만, 앞쪽은 그대로입니다.

| 어디 | 무엇을 파일로 받나 |
| --- | --- |
| `step0_collect/api_to_info.py` | 주차대수·재고 (같은 step0의 앞 스크립트) |
| `step0_collect/calculate_target_qty.py` | 대여소 정보·순수요·warmup |
| `step0_collect/extract_parking_lot.py` | 대여소 원천 |
| `step1_cluster/top_st_clustering.py` | 재배치 정보·대여소 정보 |
| `step2_optimize/ilp.py` | step1 후보 |
| `step2_optimize/vrp.py` | ILP 계획·대여소 정보 |

🔴 **여기서 배운 것**: ①을 먼저 하지 않고 ④부터 했다면 실패가 **조용했을** 수
있습니다. 실제로 ①을 하자마자 `demand_satisfaction()`이 컬럼을 **자리로** 집던
것이 드러났는데(`iloc[:, [0,1,2,3,10,5,8,9,6,7]]`), DB 입력은 앞에 스코프 컬럼이
붙어 자리가 밀립니다 — `cluster` 자리에서 `mu`를 집어도 **둘 다 숫자라 groupby가
그냥 돕니다.** 지도가 `KeyError`로 죽어 준 것이 운이 좋았던 것입니다(1.26.164).

⚠️ **원천 CSV는 이 이야기와 무관합니다.** 대여이력·재고이력 같은 **입력**은
계속 파일입니다. 걷어내는 것은 파이프라인이 **자기가 만든 것을 자기가 다시
읽는** 자리뿐입니다.
