# Step 3 — 결과 시각화 (`step3 (결과 시각화)/`)

VRP 경로를 TMAP Routes API로 실제 도로 경로로 변환해 Folium 지도에 그리는 단계입니다.

## 파일별 상세

### `main.py`
- **입력**: `VRP_plan{duration} ({now}).csv`, `top{duration} ({now}).csv`, `.env`의 `API_KEY`(TMAP)
- **출력**: `data/pp_data/VRP/visualization/vrp_map{duration} ({now}).html`
- **처리 흐름**:
  1. 클러스터별로 depot → 방문지들 → depot 경로 포인트 구성
  2. TMAP `routeSequential30` API로 도로 경로(GeoJSON) 요청
  3. Folium에 경로 폴리라인(방향 화살표), 방문 순서 마커(DivIcon), 적재량·누적 도착시간 팝업 표시
  4. 클러스터별 레이어 토글 + 범례

### `module.py`
- `seconds_to_hms()`: 초 → HH:MM:SS
- `call_tmap_sequential()`: TMAP 경유지 최적화 API 1회 호출 (429 재시도, 4xx 즉시 실패)
- `call_tmap_chunked()`: 경유지 30개 초과 시 구간 분할 호출 (구간 끝 경유지를 도착지로 연결)
- `extract_cumulative_times()`: GeoJSON 선분의 `time` 속성을 누적해 포인트별 도착시간 계산
- `merge_tmap_results()`: 분할 호출 결과의 경로·누적시간을 하나로 병합 (경계 중복 제거)

## 현재 문제점

| 우선순위 | 문제 |
| --- | --- |
| 🟢 | depot 시작 좌표에 `-0.005`/`-0.0003` 오프셋 하드코딩 (마커 겹침 회피용) — 주석 없음 |
| 🟢 | `start_time="201709121938"` 고정값 — TMAP 요청 파라미터 의미 확인 필요 |
| 🟢 | 분할 호출 시 구간 경계 경유지는 TMAP 순서 최적화 대상에서 제외됨(구간 도착지로 고정) — 방문 순서는 VRP가 이미 정하므로 실질 영향 없음 |

## 작업 목록

- [x] ~~`now`를 project_config로 통일~~ + duration_list 루프 지원 (1.0.3)
- [x] ~~TMAP 호출 정보를 `make_vrp_map` 인자로 주입 (전역 제거)~~ (1.0.3)
- [x] ~~`action == 'return'` 행 처리~~ — depot 경유지로 포함, 마커는 생략·적재량 초기화 (1.2.0)
- [x] ~~depot id·좌표·차량 용량을 project_config 공통 상수로 통일~~ (1.2.0)
- [x] ~~경유지 30개 초과 시 분할 요청~~ — `call_tmap_chunked` + `merge_tmap_results` (1.2.0)
- [x] ~~module.py 죽은 코드 삭제~~ (1.2.0)
