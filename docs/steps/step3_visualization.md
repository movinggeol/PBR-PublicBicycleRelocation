# Step 3 — 결과 시각화 (`step3 (결과 시각화)/`)

VRP 경로를 TMAP Routes API로 실제 도로 경로로 변환해 Folium 지도에 그리는 단계입니다.

## 파일별 상세

### `main.py`
- **입력**: `VRP_plan{duration} ({now}).csv`, `top{duration} ({now}).csv`, `.env`의 `API_KEY`(TMAP)
- **출력**: `data/pp_data/VRP/visualization/vrp_map{duration} ({now}).html`
- **처리 흐름**:
  1. 클러스터별로 depot → 방문지들 → depot 경로 포인트 구성
  2. TMAP `routeSequential100` API로 도로 경로(GeoJSON) 요청
  3. Folium에 경로 폴리라인(방향 화살표), 방문 순서 마커(DivIcon), 적재량·누적 도착시간 팝업 표시
  4. 클러스터별 레이어 토글 + 범례

### `module.py`
- `seconds_to_hms()`: 초 → HH:MM:SS
- `call_tmap_sequential()`: TMAP 경유지 최적화 API 1회 호출 (429 재시도, 4xx 즉시 실패)
- `call_tmap_chunked()`: 경유지 100개 초과 시 구간 분할 호출 (구간 끝 경유지를 도착지로 연결)
- `extract_cumulative_times()`: GeoJSON 선분의 `time` 속성을 누적해 포인트별 도착시간 계산
- `merge_tmap_results()`: 분할 호출 결과의 경로·누적시간을 하나로 병합 (경계 중복 제거)

## TMAP 호출 한도 (1.12.0)

**`routeSequential30`은 일일 한도가 작아 3회차를 한 번 돌리면 소진됩니다.**
실제로 파이프라인이 이 단계에서 멈췄고, 응답은 `429 QUOTA_EXCEEDED`였습니다.

```text
[routeSequential30]  HTTP 429  {"error":{"code":"QUOTA_EXCEEDED","message":"Limit Exceeded"}}
[routeSequential100] HTTP 200  features: 5
```

**`routeSequential100`으로 바꿨습니다.** 한도가 넉넉할 뿐 아니라 경유지를 100개까지
받으므로, 현실적인 클러스터 크기(최대 26곳)에서는 **클러스터 하나가 호출 한 번**으로
끝납니다. 분할 호출은 사실상 일어나지 않습니다.

유료 API인 만큼 안전장치를 두 겹 뒀습니다.

| 장치 | 동작 | 설정 |
| --- | --- | --- |
| 호출 예산 | 한 프로세스에서 N건을 넘으면 중단 | `PBR_TMAP_MAX_CALLS` (기본 35) |
| 한도 초과 즉시 포기 | `QUOTA_EXCEEDED`는 재시도해도 하루가 지나야 풀리므로 기다리지 않음 | — |

**둘 다 파이프라인을 멈추지 않습니다.** 걸리면 그 클러스터만 도로 경로 대신
**방문 순서를 이은 직선(점선)** 으로 그리고 도착 시각은 `-`로 둡니다.
지도 품질만 떨어질 뿐 이후 단계는 정상 진행됩니다.

기본 예산 35는 정규 실행량(클러스터 10개 × 3회차 = 30건)에 여유를 조금 둔 값입니다.

```powershell
$env:PBR_TMAP_MAX_CALLS="0"    # 호출 없이 직선 지도만 (쿼터 소모 0)
$env:PBR_TMAP_URL="https://apis.openapi.sk.com/tmap/routes/routeSequential30"  # 엔드포인트 교체
```

## 현재 문제점

| 우선순위 | 문제 |
| --- | --- |
| 🟢 | depot 시작 좌표에 `-0.005`/`-0.0003` 오프셋 하드코딩 (마커 겹침 회피용) — 주석 없음 |
| 🟢 | `start_time="201709121938"` 고정값 — TMAP 요청 파라미터 의미 확인 필요 |
| 🟢 | 분할 호출 시 구간 경계 경유지는 TMAP 순서 최적화 대상에서 제외됨(구간 도착지로 고정) — 방문 순서는 VRP가 이미 정하므로 실질 영향 없음 |
| 🟢 | 호출 예산은 **프로세스 단위**라 여러 번 나눠 실행하면 일일 한도를 넘을 수 있음 |

## 작업 목록

- [x] ~~`now`를 project_config로 통일~~ + duration_list 루프 지원 (1.0.3)
- [x] ~~TMAP 호출 정보를 `make_vrp_map` 인자로 주입 (전역 제거)~~ (1.0.3)
- [x] ~~`action == 'return'` 행 처리~~ — depot 경유지로 포함, 마커는 생략·적재량 초기화 (1.2.0)
- [x] ~~depot id·좌표·차량 용량을 project_config 공통 상수로 통일~~ (1.2.0)
- [x] ~~경유지 30개 초과 시 분할 요청~~ — `call_tmap_chunked` + `merge_tmap_results` (1.2.0)
- [x] ~~일일 한도 소진으로 파이프라인 중단~~ — `routeSequential100` 전환 + 호출 예산 +
  직선 폴백 (1.12.0)
- [x] ~~module.py 죽은 코드 삭제~~ (1.2.0)
