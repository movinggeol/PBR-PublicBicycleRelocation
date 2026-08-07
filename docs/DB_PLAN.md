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

전환 비용을 낮추기 위해 이관 시 SQLAlchemy를 얇게 사용한다(raw sqlite3 직접 호출 지양).

## 목표 스키마

```text
data/bike_system.db  (WAL 모드)
├── rental_history   (대여이력, 대여일시 인덱스)      ← raw CSV
├── station_info     (대여소 마스터)                  ← step0 산출
├── net_demand       (순수요)                        ← step0 산출
├── rebalance_plan   (rebal_qty)                     ← step0 산출
├── ilp_plan         (Pick→Drop 이동 계획)           ← step2 산출
├── vrp_plan         (방문 순서 + 거리·시간)          ← step2 산출
└── metrics          (verification + route_summary)  ← step4 산출
```

**핵심 설계: `run_label` 컬럼.** 지금 파일명에 박혀 있는 `{now}` 라벨을 산출 테이블의
컬럼으로 옮긴다(`run_label TEXT`, duration도 컬럼화). 효과:

- 실행 이력 간 비교("지난주 vs 이번주 개선률")가 쿼리 한 줄이 됨
- 웹 API의 최신 파일 휴리스틱 → `ORDER BY run_label DESC LIMIT 1`
- 파일명 규약(`이름{duration} ({now}).csv`) 의존성이 사라짐

초기 테이블 DDL 초안은 [메모.txt](메모.txt)의 SQLite 섹션 참고
(rental_history / station_info / station_workload / inventory_movement / visit_sequence —
실제 이관 시 위 스키마 기준으로 재정리).

## 작업 단계

- [ ] 0. 선행: Python 설치 → 파이프라인 검증 → 커밋 (docs/TODO.md 참고)
- [ ] 1. `db.py` 공통 모듈: 연결(WAL), 테이블 생성, `df.to_sql`/`read_sql` 헬퍼, run_label 규약
- [ ] 2. step0부터 순차 이관: CSV 저장과 DB 저장 병행(이중 기록) → 검증 후 CSV 기록 제거
- [ ] 3. webapp API를 DB 조회로 전환 (`/api/stations` 등에서 최신 파일 휴리스틱 제거)
- [ ] 4. 대여이력 원본 적재 스크립트 (`raw_data` CSV → rental_history, 인덱스 생성)
- [ ] 5. 실행 이력 비교 기능 (run_label 간 개선률 비교 API·화면)
