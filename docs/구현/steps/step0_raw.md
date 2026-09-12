# Step 0 — 원천 데이터 처리 (`pipeline/step0_collect/`)

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
python tools/load_rentals.py                  # 원천 CSV → rental_history
python tools/load_rentals.py --split-by-month # 여러 달이 든 병합 파일 (월별 분리)
python tools/load_rentals.py --status         # 기간별 적재 현황
```

**여러 달이 든 파일은 반드시 `--split-by-month`로 넣으세요.** 계절이 다른 달을 섞어
평균을 내면 목표 재고가 엉뚱해집니다(실측: 겨울 27만행 vs 여름 54만행으로 2배 차이).

원천 CSV는 utf-8-sig(BOM)로 읽습니다 — 공공데이터 CSV는 BOM이 붙어 오는 경우가 많고,
그냥 utf-8로 읽으면 첫 컬럼명에 BOM이 붙어 컬럼을 못 찾습니다.

적재돼 있지 않으면 원천 CSV로 폴백하므로, 적재하지 않아도 파이프라인은 그대로 돕니다.
1년치를 적재해 두고 한 달씩 분석할 때 DB 쪽이 유리합니다(성능 비교는 [DB_PLAN.md](../DB_PLAN.md) 4단계).

## 파일별 상세

### 1. `tashu_api.py`
- **입력**: `.env`의 `TASHU_API_KEY`, TASHU Open API (`/v1/openapi/station`)
- **출력**: `data/pp_data/대여소별 재고/대여소별_자전거대수 ({now}).csv`
  (station_id, station_name, parking_info, lat, lon, stock)
- **주의**: API 응답의 `x_pos`가 위도, `y_pos`가 경도 (순서 주의, 코드에 반영됨)
- **한계**: 이 CSV는 **실행하는 순간의 스냅샷 한 장**입니다. 계획 대상일의 요일
  구분과 일치한다는 보장이 없습니다 ([TODO.md](../../기록/TODO.md) 1-4).
  시간에 따른 실측 재고가 필요하면 `tools/collect_stock.py`가 평일 07~22시에
  10분마다 `stock_history`에 쌓습니다 — [COLLECTOR.md](../COLLECTOR.md).
  **둘은 별개 저장소입니다**: 파이프라인은 이 CSV/`station_stock`을 쓰고,
  수집기는 그 둘을 건드리지 않습니다.

### 2. `extract_parking_lot.py`
- **입력**: 1번 출력 CSV
- **처리**: `parking_info` 문자열(`"10대용*1, 5대용*2"`)을 정규식으로 파싱해 거치대 수 계산.
  도로뷰로 확인한 4개 대여소(ST0370, ST1133, ST1392, ST1341)는 하드코딩으로 보정
- **출력**: `data/pp_data/대여소별 주차대수/대여소별_주차대수 ({now}).csv`

### 3. `api_to_info.py`
- **입력**: 대여이력(DB `rental_history` 또는 원천 CSV), 1·2번 출력
- **처리**: `--day-type`(평일/주말) 필터 → 대여/반납 건수 집계 → 주차대수·재고 병합
  (평일 계획이면 평일 이력만 집계해야 대여소 목록·좌표가 그 계획과 같은 세계를 가리킨다.
  1.14.0 이전에는 평일 고정이라 **휴일에만 쓰이는 대여소가 통째로 빠졌다**)
- **출력**: `data/pp_data/대여소 정보/st_info ({now}).csv`
- **참고**: 타슈 관제센터 2곳(ST0001, ST1220)은 이용자 대상 대여소가 아님 (주석 참고)

### 4. `raw_to_net.py`
- **입력**: 대여이력(DB `rental_history` 또는 원천 CSV)
- **처리**: **평일·휴일 모두**, 날짜×대여소×시간대(0~23시)별 `순수요 = 대여량 − 반납량`
  (어느 쪽으로 계획할지는 다음 단계가 `--day-type`으로 고른다)
- **출력**: `data/pp_data/순수요/st_net_daily ({period}).csv` (net_00 ~ net_23 컬럼)

## 평일과 휴일은 섞지 않는다 (1.14.0~1.14.1)

**휴일 = 주말 ∪ 공휴일**입니다. 공휴일은 [holidays](https://pypi.org/project/holidays/)
패키지의 `SouthKorea` public 카테고리를 그대로 씁니다 — 대체공휴일·임시공휴일까지
포함하고 규칙이 해마다 바뀌므로 달력을 손으로 관리하지 않습니다.

첫 커밋부터 파이프라인은 **평일만** 다뤘습니다(`raw_to_net`의 `평일유무` 필터).
휴일 이용이 전체의 **21~34%**인데 통째로 버려지고 있었고, 휴일 계획은 아예
만들 수 없었습니다.

이제 `raw_to_net`이 모든 날짜를 계산하고, **`calculate_target_qty`가 한쪽만
골라** 통계를 냅니다.

```powershell
python run_pipeline.py                      # 오늘로 자동 판정 (--day-type auto)
python run_pipeline.py --day-type holiday   # 휴일 계획을 직접 지정
python run_pipeline.py --target-date 2026-09-25   # 추석 계획을 미리 뽑기
```

`auto`가 기본입니다 — **계획 대상일(기본 오늘)을 달력으로 판정**합니다.
로그에 판정 근거가 함께 남습니다.

```text
요일 구분: 휴일 (2026-08-17 광복절 대체 휴일 → 휴일)
```

### 왜 골라야 하는가 ① — 섞으면 상쇄됩니다

25년 11월 실데이터입니다.

| 회차 | 평일 대상 | 주말 대상 | 부호역전 | **섞으면 소멸** |
| --- | --- | --- | --- | --- |
| `_05_10` | 341곳 | 248곳 | 2곳 | 11곳 |
| `_10_15` | 189곳 | **295곳** | 10곳 | **57곳** |
| `_15_20` | 200곳 | 221곳 | 14곳 | 35곳 |

- **부호역전** — 평일엔 채워야 하는데 주말엔 빼 와야 하는 대여소(또는 반대)
- **섞으면 소멸** — 한쪽에서 의미 있는 수요인데 두 요일을 한 통계로 묶으면
  0 근처로 상쇄돼 작업 대상에서 빠지는 대여소

`_10_15`는 **주말이 평일보다 바쁩니다**(295곳 vs 189곳). 그동안 통째로 못 보고
있던 수요입니다. 그리고 섞었다면 57곳이 사라졌을 것입니다.

그래서 `day_type`에 `all`을 두지 않았습니다 — 섞는 선택지를 아예 만들지 않습니다.
재현: `python experiments/structure/weekend_profile.py`

### 왜 골라야 하는가 ② — 공휴일이 평일 수요를 가리고 있었습니다 (1.14.1)

1.14.0까지 '평일'은 **월~금**이었습니다. 설·추석 연휴가 그대로 섞여 있었고
(25년 10월은 평일 자리 18일 중 **5일**이 공휴일), 그 날들의 낮은 수요가 평균을
끌어내렸습니다. 공휴일을 빼자 같은 데이터에서 이렇게 달라졌습니다.

| 기간 | 평일 일수 | `mu` 변화 | **작업 대상 (`_05_10`)** |
| --- | --- | --- | --- |
| 25년 01월 (설) | 17 → 14일 | **+17.3%** | 32곳 → **48곳** |
| 25년 10월 (추석) | 20 → 16일 | **+19.7%** | 184곳 → **229곳** |
| 26년 02월 (설) | 17 → 15일 | +12.8% | 170곳 → **195곳** |
| 25년 08월 (광복절) | 19 → 18일 | +2.6% | 207곳 → 215곳 |

**연휴가 낀 달에는 평일 재배치 수요를 20~50% 과소평가하고 있었습니다.**
`sigma`는 거의 그대로(-5~+1%)이므로, 바뀐 것은 분산이 아니라 **평균**입니다.
재현: `python experiments/structure/holiday_impact.py`

### 계절이 바뀌는 달 — warmup 보정 (1.15.1)

지난달 통계는 계절이 도약하는 달을 못 따라갑니다(2월→3월 수요 1.5배).
**계획 대상 달의 첫 14일 실적**으로 도시 전체 배율 하나를 구해 `mu`·`sigma`에
곱합니다. 3월 계획을 세울 때 3월 초 며칠의 실적은 이미 손에 있으니, 운영에서
실제로 쓸 수 있는 정보입니다.

```powershell
python run_pipeline.py --period "26년 02월" --warmup-period "26년 03월"
python run_pipeline.py --warmup-days 0        # 보정 끄기
```

`--warmup-period`를 생략하면 **계획 대상일이 속한 달**을 씁니다. 그 달의 순수요가
없으면 조용히 건너뜁니다 — 있으면 좋고 없어도 도는 보정입니다.

| `_05_10` 평일, 26년 2→3월 | 보정 없음 | **warmup 14일** |
| --- | --- | --- |
| 커버리지 | 88.7% | **94.7%** |
| MAE | 3.58 | **3.36** |

**배율은 반드시 도시 전체 하나**로만 추정합니다. 며칠치로 대여소별 보정을 하면
잡음만 커지고, 계절 효과는 도시 전체에 같은 방향으로 옵니다.
안전장치로 배율을 [0.5, 2.0]으로 자릅니다.

> `z`를 올리는 것과는 다릅니다. `z`는 분산만 부풀려 전 구간에 일률적으로 여유를
> 주지만(작업량 +30%), warmup은 **중심을 옮깁니다.** 그래서 MAE도 함께 좋아집니다.
> 실제로 z=2.10까지 올려도 전환 달은 90.4%에 머물렀습니다
> ([EXPERIMENTS.md](../../분석/EXPERIMENTS.md) 3장).

### 기존 순수요를 다시 만들려면

1.14.0 이전에 만든 `net_demand`에는 휴일이 없습니다. 기간마다 다시 돌려야 합니다.

```powershell
python tools/rebuild_net_demand.py            # 적재된 전 기간
python tools/rebuild_net_demand.py --dry-run  # 대상만 확인
```

> **주의**: 요일 구분은 **산출물 파일명에 들어가지 않습니다.** 평일 계획과 주말
> 계획을 둘 다 남기려면 `--now` 라벨을 달리해 두 번 실행하세요. 어느 쪽으로 돌린
> 실행인지는 DB `runs.day_type`에 기록됩니다.
>
> 공휴일 판정은 `holidays` 패키지를 따릅니다. 특정 날짜의 분류가 운영과 다르면
> `project_config.korean_holidays()`가 유일한 판정 지점이니 거기만 손보면 됩니다.

> **`z`는 1.99입니다** (기존 1.65). 백테스트(2025-04~2026-03)에서 z=1.65의 실제
> 커버리지가 91.7~92.8%로 설계 의도(95%)에 못 미쳤습니다. 순수요 분포의 꼬리가
> 정규분포보다 두껍기 때문입니다. z=1.99에서 94.9%가 됩니다.
> 근거·재현은 [EXPERIMENTS.md](../../분석/EXPERIMENTS.md) 1장 (`python experiments/params/z_sweep.py`).
>
> `_10_15` 시간대의 `mu`가 "순수요 0"과 차이가 없다던 결과는 **측정 오류였습니다.**
> 전체 대여소 평균이라 파이프라인이 손대지 않는 곳(72.8%가 `|mu| < 0.5`)에
> 희석된 것이고, 작업 대상 31곳만 보면 오차를 40.2% 줄입니다
> ([EXPERIMENTS.md](../../분석/EXPERIMENTS.md) 2장).
>
> 남은 약점은 **계절 전환기**입니다. 2월→3월처럼 수요가 1.5배 뛰는 구간은
> z를 올려도 커버리지가 90% 언저리에 머뭅니다. 분석 달 초 실적으로 배율을 보정하면
> 95%대로 회복되지만 아직 구현하지 않았습니다 ([EXPERIMENTS.md](../../분석/EXPERIMENTS.md) 3장).

### 5. `calculate_target_qty.py`
- **입력**: st_info, st_net_daily
- **처리** (시간대 duration별, `z = project_config.TARGET_Z` 기본 1.99, `PBR_TARGET_Z`로 변경):
  - `mu ≥ 0`(부족 경향): `target_qty = mu + z·sigma`
  - `mu < 0`(과잉 경향): `target_qty = stock + mu`
  - target_qty를 `[0, parking_lot × 1.5]`로 제한
  - `rebal_qty = target_qty − stock`을 `MAX_CAPACITY·tanh(x/MAX_CAPACITY)`로 완화 후 정수화
    (양수 = Drop 필요, 음수 = Pick 가능)
- **출력**: `data/pp_data/재배치 정보/rebal_qty{duration} ({now}).csv`

## 현재 문제점 (자세한 내용은 [../TODO.md](../../기록/TODO.md))

| 우선순위 | 문제 |
| --- | --- |
| 🟢 | `설명.txt` 4·5번 항목 미완 — 본 문서로 대체 후 삭제 검토 |

※ 구 `test.py`는 `experiments/diagnostic/step0_rebal_qty_check.py`로 이동했습니다 (1.2.0).

## 작업 목록

- [x] ~~5개 스크립트 모두 `project_config.get_runtime_config()` 사용으로 통일~~ (1.0.3)
- [x] ~~import 체인 제거: 실행 로직을 `if __name__ == '__main__':`으로 격리~~ (1.0.3)
- [x] ~~`!calculate_target_qty.py` → `calculate_target_qty.py` 개명~~ (1.0.3)
- [x] ~~API 실패 시 명시적 종료 처리~~ (1.0.3)
- [x] ~~clip inplace 버그 수정~~ (1.0.3)
- [x] ~~durations 다중 시간대 지원~~ → `--duration "_05_10,_10_15"` 콤마 구분 입력으로 지원 (1.0.3)
- [ ] 다중 시간대(`_10_15`, `_15_20`, `_20_05`) 실제 데이터로 검증 실행
