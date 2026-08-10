# Step 0 — 원천 데이터 처리 (`step0 (raw데이터 처리)/`)

TASHU API와 공공데이터포털 대여 이력을 받아, 이후 최적화 단계가 쓰는
대여소 정보·순수요·재배치량 CSV를 만드는 단계입니다.

## 실행 순서와 데이터 흐름

```text
1. tashu_api.py            TASHU API → 대여소별_자전거대수 ({now}).csv
2. extract_parking_lot.py  1의 결과 → 대여소별_주차대수 ({now}).csv
3. api_to_info.py          대여 이력 + 1·2의 결과 → st_info ({now}).csv
4. raw_to_net.py           대여 이력 → st_net_daily ({period}).csv
5. calculate_target_qty.py 3·4의 결과 → rebal_qty{duration} ({now}).csv
```

모든 스크립트는 `project_config.get_runtime_config()`에서 `now`/`period`/`duration`/`raw_file`을
읽으며, 각 파일은 독립 실행된다(상호 import 없음). 실행 순서는 `run_pipeline.py`가 제어한다.

각 단계는 CSV와 함께 **SQLite에도 기록**한다(이중 기록, [DB_PLAN.md](../DB_PLAN.md) 2단계).
CSV가 아직 정본이며 DB 기록 실패는 경고만 남긴다.

| 스크립트 | DB 테이블 |
| --- | --- |
| `tashu_api.py` | `station_stock` |
| `extract_parking_lot.py` | `parking_lot` |
| `api_to_info.py` | `station_info` |
| `raw_to_net.py` | `net_demand` (period 스코프) |
| `calculate_target_qty.py` | `rebalance_plan` |

`api_to_info.py`와 `raw_to_net.py`는 **읽기도 DB에서** 합니다(DB_PLAN 4단계).
원천 대여이력을 먼저 적재해 두면 됩니다.

```powershell
python tools/load_rentals.py            # 원천 CSV → rental_history
python tools/load_rentals.py --status   # 기간별 적재 현황
```

적재돼 있지 않으면 원천 CSV로 폴백하므로, 적재하지 않아도 파이프라인은 그대로 돕니다.
1년치를 적재해 두고 한 달씩 분석할 때 DB 쪽이 유리합니다(성능 비교는 [DB_PLAN.md](../DB_PLAN.md) 4단계).

## 파일별 상세

### 1. `tashu_api.py`
- **입력**: `.env`의 `TASHU_API_KEY`, TASHU Open API (`/v1/openapi/station`)
- **출력**: `data/pp_data/대여소별 재고/대여소별_자전거대수 ({now}).csv`
  (station_id, station_name, parking_info, lat, lon, stock)
- **주의**: API 응답의 `x_pos`가 위도, `y_pos`가 경도 (순서 주의, 코드에 반영됨)

### 2. `extract_parking_lot.py`
- **입력**: 1번 출력 CSV
- **처리**: `parking_info` 문자열(`"10대용*1, 5대용*2"`)을 정규식으로 파싱해 거치대 수 계산.
  도로뷰로 확인한 4개 대여소(ST0370, ST1133, ST1392, ST1341)는 하드코딩으로 보정
- **출력**: `data/pp_data/대여소별 주차대수/대여소별_주차대수 ({now}).csv`

### 3. `api_to_info.py`
- **입력**: 대여이력(DB `rental_history` 또는 원천 CSV), 1·2번 출력
- **처리**: 평일만 필터 → 대여/반납 건수 집계 → 주차대수·재고 병합
- **출력**: `data/pp_data/대여소 정보/st_info ({now}).csv`
- **참고**: 타슈 관제센터 2곳(ST0001, ST1220)은 이용자 대상 대여소가 아님 (주석 참고)

### 4. `raw_to_net.py`
- **입력**: 대여이력(DB `rental_history` 또는 원천 CSV)
- **처리**: 평일 기준, 날짜×대여소×시간대(0~23시)별 `순수요 = 대여량 − 반납량`
- **출력**: `data/pp_data/순수요/st_net_daily ({period}).csv` (net_00 ~ net_23 컬럼)

### 5. `calculate_target_qty.py`
- **입력**: st_info, st_net_daily
- **처리** (시간대 duration별, z=1.65):
  - `mu ≥ 0`(부족 경향): `target_qty = mu + z·sigma`
  - `mu < 0`(과잉 경향): `target_qty = stock + mu`
  - target_qty를 `[0, parking_lot × 1.5]`로 제한
  - `rebal_qty = target_qty − stock`을 `MAX_CAPACITY·tanh(x/MAX_CAPACITY)`로 완화 후 정수화
    (양수 = Drop 필요, 음수 = Pick 가능)
- **출력**: `data/pp_data/재배치 정보/rebal_qty{duration} ({now}).csv`

## 현재 문제점 (자세한 내용은 [../TODO.md](../TODO.md))

| 우선순위 | 문제 |
| --- | --- |
| 🟢 | `설명.txt` 4·5번 항목 미완 — 본 문서로 대체 후 삭제 검토 |

※ 구 `test.py`는 `experiments/step0_rebal_qty_check.py`로 이동했습니다 (1.2.0).

## 작업 목록

- [x] ~~5개 스크립트 모두 `project_config.get_runtime_config()` 사용으로 통일~~ (1.0.3)
- [x] ~~import 체인 제거: 실행 로직을 `if __name__ == '__main__':`으로 격리~~ (1.0.3)
- [x] ~~`!calculate_target_qty.py` → `calculate_target_qty.py` 개명~~ (1.0.3)
- [x] ~~API 실패 시 명시적 종료 처리~~ (1.0.3)
- [x] ~~clip inplace 버그 수정~~ (1.0.3)
- [x] ~~durations 다중 시간대 지원~~ → `--duration "_05_10,_10_15"` 콤마 구분 입력으로 지원 (1.0.3)
- [ ] 다중 시간대(`_10_15`, `_15_20`, `_20_05`) 실제 데이터로 검증 실행
