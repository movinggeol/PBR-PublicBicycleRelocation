# 테스트

이 프로젝트는 처음에 테스트가 하나도 없었고, **의존성이 전부 깨진 상태**로
파이프라인이 아예 돌지 않았습니다. 그때 만든 안전망이 지금의 298개 테스트입니다.
이후 모든 수정은 이 위에서 이뤄졌습니다.

```powershell
python -m pytest              # 전체 298개 (약 50~80초)
python -m pytest -q           # 요약만
python -m pytest tests/test_db.py -v
python -m pytest -k stockout  # 이름으로 골라 실행
```

---

## 1. 무엇을 어디서 검증하는가

| 파일 | 개수 | 무엇을 지키는가 |
| --- | --- | --- |
| [tests/test_pipeline.py](../tests/test_pipeline.py) | 35 | step0→1→2→4를 **실제로 실행**해 산출물·스키마 확인 |
| [tests/test_calculations.py](../tests/test_calculations.py) | 44 | **계산 자체** — 목표재고 공식·군집 목적함수·VRP 적재/시간 제약·ILP 수급 제약·집행 기준 결품·**솔버 값 반올림/입력 방어** ([FORMULATION.md](FORMULATION.md)) |
| [tests/test_kpi.py](../tests/test_kpi.py) | 19 | 성과 지표 계산과 `/kpi` 화면, 결측 지표 렌더링 ([KPI.md](KPI.md)) |
| [tests/test_db.py](../tests/test_db.py) | 22 | SQLite 저장소의 스코프·멱등성·최신 라벨·스키마 마이그레이션 ([DB_SCHEMA.md](DB_SCHEMA.md)) |
| [tests/test_webapp.py](../tests/test_webapp.py) | 50 | 웹 라우트가 통째로 깨지는 사고 방지, 실행 폼 입력 검증(기간·시간대 목록 포함), 파일 목록 쪽 나눔, **표 정렬을 건 표/걸지 않은 표** ([WEBAPP.md](WEBAPP.md)) |
| [tests/test_pipeline_progress.py](../tests/test_pipeline_progress.py) | 9 | 실행 로그에서 진행 단계를 뽑는 규약 ([WEBAPP.md](WEBAPP.md)) |
| [tests/test_charts.py](../tests/test_charts.py) | 15 | **그래프 규칙** — 계열 하나에 선 하나(축 둘 금지), 색을 SVG에 박지 않는지, 값 표시는 끝점만, 좌표가 뷰박스 안인지, 발산 척도의 가운데가 무채색인지 |
| [tests/test_orders.py](../tests/test_orders.py) | 9 | **작업지시서·실시간 재고 대조** — 대조가 지시량(요구량 아님)을 보는지, 집행 가능 판정이 계획과 같은 상한을 쓰는지, 타슈 API를 누를 때만 부르고 실패해도 500이 아닌지 |
| [tests/test_guide.py](../tests/test_guide.py) | 8 | 사용 안내가 설정값을 하드코딩하지 않는지, 폼 항목을 빠짐없이 설명하는지, **입력 예시가 실제로 통하는 형식인지**, 예상 소요를 지난 실행에서 뽑는지 |
| [tests/test_webapp_db.py](../tests/test_webapp_db.py) | 14 | 웹 API가 CSV 대신 DB를 읽는지 |
| [tests/test_rentals.py](../tests/test_rentals.py) | 10 | 대여이력 적재와 **CSV·DB 결과 동일성** |
| [tests/test_fleet.py](../tests/test_fleet.py) | 13 | 차량 로테이션·형평성과 보유 대수 변경 ([FLEET.md](FLEET.md)) |
| [tests/test_tmap.py](../tests/test_tmap.py) | 11 | TMAP 엔드포인트 선택·폴백 ([steps/step3_visualization.md](steps/step3_visualization.md)) |
| [tests/test_day_type.py](../tests/test_day_type.py) | 36 | 평일/휴일 분리·공휴일 판정·수요 모델 폴백·계절 보정, **평가도 같은 구분을 쓰는지** ([steps/step0_raw.md](steps/step0_raw.md)) |

**API 키가 필요한 두 단계는 자동 테스트에서 제외**했습니다 —
`step0/tashu_api.py`(TASHU)와 `step3/main.py`(TMAP). 검증 방법은 4장에 있습니다.
다만 **엔드포인트 선택·폴백 규칙은 네트워크 없이 검증합니다**(`test_tmap.py`) —
`requests.post`를 가로채 어느 URL로 보냈는지만 보면 되고, 검증 대상이 응답 내용이
아니라 규칙이기 때문입니다. 쿼터는 한 건도 쓰지 않습니다.

### 특히 중요한 것들

**스모크 테스트가 진짜로 스크립트를 실행합니다.** `test_pipeline.py`는 함수를
import해 부르는 게 아니라 `subprocess`로 각 단계 파일을 돌립니다. import 부작용,
경로 문제, 인자 전달 같은 **"실제로 실행해야만 드러나는" 고장**을 잡기 위해서입니다.
실제로 이 방식으로 Starlette 시그니처 변경과 BOM 인코딩 문제를 잡았습니다.

**CSV·DB 동일성이 이관 작업의 판정 기준이었습니다.** `test_rentals.py`의
`test_csv_and_db_paths_produce_identical_output`은 같은 입력을 CSV로 한 번,
DB로 한 번 계산해 결과가 **완전히 같은지** 봅니다. 저장소를 바꾸는 작업이라
값이 하나라도 달라지면 이관 실패입니다. 이 테스트가 `REAL` 컬럼이 정수를
실수로 바꾸는 문제를 잡았습니다(→ `NUMERIC` 어피니티로 수정).

**경로 탈출 차단은 보안 테스트입니다.** `/files/../../../etc/passwd` 같은 요청 4종을
막는지 확인합니다. 웹앱에 인증이 없으므로(로컬 전용) 최소한 파일 접근은 가둡니다.

---

## 2. 테스트가 실데이터를 건드리지 않는 방법

이 프로젝트는 실행하면 `data/` 아래에 파일을 쓰고 SQLite에 기록합니다.
**테스트가 사용자 데이터를 오염시킨 적이 실제로 있었습니다.** 지금은 세 겹으로 막습니다.

| 장치 | 위치 | 하는 일 |
| --- | --- | --- |
| `isolate_db` (autouse) | [tests/conftest.py](../tests/conftest.py) | 모든 테스트에 `PBR_DB_PATH`를 임시 경로로 강제 |
| `PP_ROOT` 격리 (autouse) | `tests/test_webapp_db.py` | `catalog.PP_ROOT`를 임시 디렉터리로 |
| 고유 실행 라벨 | `tests/test_pipeline.py` | `now`/`period`를 `smoketest-{PID}`로 두고, 끝나면 그 라벨 파일만 삭제 |

`isolate_db`가 **autouse**인 것이 핵심입니다. 웹 API가 DB를 조회하게 되면서
라우트를 한 번 부르기만 해도 실제 `data/bike_system.db`가 생성되기 때문에,
개별 테스트가 깜빡해도 자동으로 막히도록 했습니다.

---

## 3. 합성 데이터

실데이터 없이도 전 단계가 돌아가야 합니다. [tools/make_sample_data.py](../tools/make_sample_data.py)가
대여소·재고·대여이력을 만들어 냅니다.

**무작위 난수가 아니라 방향성 있는 수요를 심었습니다.** `FLOW_WINDOWS`로 시간대마다
"어느 대여소에서 어느 대여소로" 흐르는 창을 세 개 두어, 순수요가 실제처럼
한쪽으로 쏠리게 합니다. 균등 난수로 만들면 순수요가 0 근처에 몰려
**Pick/Drop 후보가 아예 안 생기고 step1이 빈 프레임을 받습니다.**

```powershell
python tools/make_sample_data.py --label mytest
```

---

## 4. 자동화하지 않은 검증

### 외부 API (TASHU · TMAP)

호출마다 **일일 한도를 소모하는 유료 API**라 자동 테스트에 넣지 않았습니다.
CI에서 매번 돌면 한도가 금방 마릅니다. 대신 변경 시 수동으로 확인합니다.

**TMAP 엔드포인트 교체 검증 (1.12.0)** — 실제로 수행한 절차입니다.

| 단계 | 방법 | 호출 수 | 결과 |
| --- | --- | --- | --- |
| 1. 엔드포인트 확인 | 두 엔드포인트에 최소 페이로드(경유지 1개)로 1회씩 | 2 | `routeSequential30` → **429 QUOTA_EXCEEDED**<br>`routeSequential100` → **200 OK** |
| 2. 정상 경로 | `_05_10`·`_10_15` 전체 + `_15_20` 일부 | 25 | 지도 정상 생성 |
| 3. 정상 경로 재확인 | `_15_20` 단독 재실행 | 10 | 호출 10건, 폴리라인 91개, **점선 0개**, 도착시각 결측 0개 |
| 4. 폴백 경로 | `PBR_TMAP_MAX_CALLS=0` | **0** | 크래시 없이 지도 생성, 점선 10건 |
| | | **합계 37** | |

**4번이 호출 없이 실패 경로를 검증하는 방법입니다.** 예산 상한을 0으로 두면
HTTP 요청을 보내기 **전에** 예외가 발생하므로, 한도 초과 상황의 폴백 동작을
쿼터를 한 건도 쓰지 않고 확인할 수 있습니다.

```powershell
# 호출 없이 폴백 경로만 확인
$env:PBR_TMAP_MAX_CALLS="0"
python "step3_map/main.py" --now "<라벨>" --duration "_15_20"
```

검증 포인트는 **"실패해도 파이프라인이 멈추지 않는가"** 입니다. 걸린 클러스터만
직선(점선)으로 그리고 도착 시각을 `-`로 둔 채 다음 단계로 넘어가야 합니다.

### 파라미터 실험

`z`·`γ` 같은 모델 파라미터는 "맞다/틀리다"가 아니라 **맞바꿈(trade-off)** 이라
단위 테스트로 판정할 수 없습니다. 대신 실데이터 실험으로 근거를 남깁니다
→ [EXPERIMENTS.md](EXPERIMENTS.md), 재현 스크립트는 [experiments/](../experiments/).

파라미터를 바꿀 때 지켜야 할 것:

- **한 회차만 보고 판단하지 않는다.** 3회차 전부로 재확인한다 (틀린 적 있음).
- **`γ`는 비단조다.** γ=2000이 γ=1000보다 나빴다 — 두 점 사이를 보간하면 안 된다.
- **`z` 실험을 개선률·목표 도달률로 판정하지 않는다.** 두 지표가 `target_qty`를
  분모로 삼아 z에 딸려 움직인다. 결품 시간으로 비교한다.

---

## 5. 아직 덮지 못한 것

| 항목 | 왜 |
| --- | --- |
| **계산 로직의 단위 테스트** | 현재는 대부분 스모크 수준이다. 목표 재고 공식, 군집 조정, VRP 적재 제약을 값 단위로 확인하는 테스트가 없다 |
| **CI (GitHub Actions)** | 로컬 실행에만 의존한다 |
| `step0/tashu_api.py` | API 키 필요 — 응답을 고정한 목(mock) 테스트로 대체 가능 |
| `step3/main.py` | 위 4장 참고 (수동) |
| `step0_eda` | 산출물이 분석용이라 파이프라인 의존이 없다 |

→ [TODO.md](TODO.md) 11번

---

## 6. 테스트를 추가할 때

1. **실데이터를 건드리지 않는지 먼저 확인하세요.** `conftest.py`의 autouse fixture가
   DB는 막아주지만, `data/pp_data/`에 파일을 쓰는 테스트라면 고유 라벨을 쓰고
   끝나면 그 라벨 파일만 지워야 합니다 (`test_pipeline.py` 참고).
2. **왜 이 테스트가 필요한지 docstring에 적으세요.** 기존 테스트 파일들은
   맨 위에 "무엇을 막으려는 테스트인가"를 적어 뒀습니다 —
   예: "라이브러리 업그레이드로 라우트가 통째로 깨지는 사고를 잡는 것이 목적".
3. **버그를 고쳤다면 그 버그를 재현하는 테스트를 같이 넣으세요.**
   처리 대수 2배 계산 버그는 두 곳에 있었고, 한 곳만 고친 뒤 나머지가
   실데이터에서 다시 드러났습니다.

## 관련 문서

| 문서 | 내용 |
| --- | --- |
| [TODO.md](TODO.md) | 테스트 확장 과제 (11번) |
| [EXPERIMENTS.md](EXPERIMENTS.md) | 파라미터를 실험으로 정하는 방법 |
| [DB_PLAN.md](DB_PLAN.md) | CSV·DB 동일성 검증의 배경 |
| [steps/step3_visualization.md](steps/step3_visualization.md) | TMAP 호출 한도와 폴백 |
