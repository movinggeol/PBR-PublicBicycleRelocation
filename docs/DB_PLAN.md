# DB 도입 계획 — SQLite 채택 결정

> 2026-08-07 결정. 현재 CSV 파일 기반 데이터 관리를 SQLite 단일 DB로 이관한다.
> (아직 미구현 — 작업 단계는 아래 참고. 선행 조건: 파이프라인 검증·커밋 완료)

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

```text
data/bike_system.db  (WAL 모드)
├── runs             (실행 이력: run_label, period, duration, raw_file, created_at)
├── station_stock    (TASHU API 재고 스냅샷)          ← step0 산출
├── station_info     (대여소 마스터)                  ← step0 산출
├── parking_lot      (거치대 수)                      ← step0 산출
├── net_demand       (순수요, period 스코프)          ← step0 산출
├── rebalance_plan   (rebal_qty)                     ← step0 산출
├── pick_drop        (Pick/Drop 후보 + 클러스터)      ← step1 산출
├── ilp_plan         (Pick→Drop 이동 계획)           ← step2 산출
├── vrp_plan         (방문 순서 + 거리·시간, seq 보존) ← step2 산출
├── metrics          (재배치 전후 불균형 개선)         ← step4 산출
├── route_summary    (클러스터별 이동거리·소요시간)     ← step4 산출
└── rental_history   (대여이력 원본, 대여일시 인덱스)   ← raw CSV (5단계에서 적재)
```

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

초기 테이블 DDL 초안은 [메모.txt](메모.txt)의 SQLite 섹션 참고
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
- [ ] 3. webapp API를 DB 조회로 전환 (`/api/stations` 등에서 최신 파일 휴리스틱 제거)
- [ ] 4. 대여이력 원본 적재 스크립트 (`raw_data` CSV → rental_history, 인덱스 생성)
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
| 1 | `1.top_st_clustering.py` | `pick_drop` |
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

### 다음 단계로 넘어가기 전 확인할 것

CSV와 DB가 일치하는지 실데이터로 확인한 뒤 3단계(웹 API 전환)로 갑니다.

```python
import db, pandas as pd
with db.session() as conn:
    print(db.list_runs(conn))
    print(db.load_frame(conn, "metrics").head())
```
