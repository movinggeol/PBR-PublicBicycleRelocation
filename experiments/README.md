# experiments/

파이프라인에 속하지 않는 검증·실험 스크립트 모음입니다. **29개**가 되면서 **성격별로
다섯 갈래로 나눴습니다**(1.20.7).

```text
experiments/
├── params/       파라미터를 정한 실험 (z · γ · 학습 창)
├── baseline/     대조군 비교와 반복 실행 (논문용)
├── structure/    설계를 정하기 위한 측정 (섞어도 되나 · 붙일 값어치가 있나)
├── diagnostic/   중간 산출물 진단 (수급 격차 · 군집 결과)
└── learning/     학습용 예제 (파이프라인과 무관)
```

**서로 import 하는 스크립트는 같은 폴더에 뒀습니다** — `gamma_recheck.py`가
`baseline_compare.py`를 부르므로 둘 다 `baseline/`입니다.

> 새 실험을 추가할 때: 어느 갈래인지 정하고, 스크립트 맨 위의 `sys.path.insert`는
> `parents[2]`(저장소 루트)를 가리켜야 합니다. 한 칸 깊어졌기 때문입니다.

## params/ — 파라미터 실험 (결과는 [../docs/분석/EXPERIMENTS.md](../docs/분석/EXPERIMENTS.md))

기본값으로 쓰이는 `z`와 `γ`는 아래 스크립트로 정했습니다. **값을 바꾸려면
같은 스크립트를 다시 돌려 근거를 남기세요.**

| 파일 | 묻는 것 | 결론 |
| --- | --- | --- |
| `z_sweep.py` | `z = 1.65`가 정말 95%를 덮나 | 92.5%뿐 → `TARGET_Z = 1.99` |
| `predictor_compare.py` | `_10_15`의 `mu`는 쓸모없나 | 측정이 틀렸다 (작업 대상만 보면 +40.2%) |
| `seasonal_window.py` | 계절 전환기를 어떻게 넘나 | 분석 달 첫 14일로 배율 보정 |
| `travel_estimate.py` | 이동시간 추정에 '퍼짐'을 넣으면 나아지나 | **아니다.** 계수가 음수로 나온다 — 퍼짐은 대여소 수의 대리 변수일 뿐 |
| `min_qty_sweep.py` | 작업 문턱(2)이 맞나 | **문턱이 작동하지 않는다.** TOP_STATION_LIMIT(50)이 먼저 자른다 |
| `top_limit_sweep.py` | 후보 상한(50)이 맞나 | **맞다.** 넓힐수록 결품은 주지만 **필요 차량이 보유 21대를 넘어** 집행이 안 된다 |
| `limit_fixedpop_grid.py` | 후보 상한을 **같은(고정) 모집단**으로 다시 재면 `top_limit_sweep.py`(위)와 결론이 같나 | **반대다.** 두 달 모두 상한이 클수록 결품이 낮다 — 자기 모집단으로 잰 12장의 상한 비교는 무효였다(17장) |
| `limit_fleet_grid.py` | 상한 × 회차당 차량 수를 함께 흔들면 현행이 파레토 프론티어 위에 있나 | 🔴 **상한 축은 무효**(자기 모집단 결함 — 상한 비교는 `limit_fixedpop_grid.py`를 쓸 것). **차량 축은 유효** — 현행(21대)이 파레토 밖(12장) |
| `gamma_sweep.py` | 거리 가중치 `γ`(3000)를 바꿔 예산 초과를 줄일 수 있나 | **두 목표를 동시에 개선하는 γ가 없다.** γ↓는 결품 −24%이나 예산 초과 0건 조합이 0개, γ↑는 결품 +74% → **γ=3000 유지, 운영 결정으로 넘김** |
| `z_stockout_grid.py` | `z`를 **판정 지표(결품 시간)** 로 재면 1.99인가 | 1.65·1.80보다는 낫다. 2.10·2.33과는 못 가린다 ⚠️ **자기 후보를 모집단으로 써서 결함이 있다 — 판정에 쓰지 말 것**(18장) |
| `z_fixedpop_grid.py` | 모집단을 바꿔도 같은 `z`가 이기나 | **각 모집단은 자기를 정의한 z를 뽑는다.** 중립(전체 대여소)으로 보면 전체 폭 5~23초 → `z=1.99` 유지. **z 판정은 이 스크립트로 한다** |
| `center_stat_grid.py` | 중심 통계를 **중앙값 + z·MAD**로 바꾸면 나은가 | **가릴 수 없다.** 중립 모집단에서 1~18초(신호/잡음 0.1~2.8)이고 승자가 회차마다 갈린다. 각 방법은 **자기 후보를 모집단으로 삼으면 신호/잡음 5~31로 압승**한다 → **현행 유지**(28장) |
| `z_fixedpop_grid.py --z-grid wide` | 🔴 **미실행(TODO 대기-7).** 0.0~2.81 **16개 값**으로 넓혀도 회차별 `z`가 필요한가 | — |
| `cluster_count_sweep.py` | 군집 수 `K`를 줄이면 물량 손실이 주나 | **준다 — 결품 −17%·물량 +8%(24개 중 18개 우세). 그런데 군집의 80%가 예산 초과** → 채택 못 함. `K` 산정식이 거리를 모르는 것이 진짜 문제 |
| `wanted_vehicles_geo_sweep.py` | 대여소 수 대신 후보 집합의 기하(BHH 근사)로 `K`를 정하면 나은가 | 채택 보류(5-H장) — 뒤이어 `tour_length_estimate.py`(diagnostic/)가 **세 어림 모두 현행보다 못함**을 확인(13장) |
| `cluster_time_term.py` | 군집 목적함수의 거리 항을 메도이드 거리합 대신 **순회거리**로 바꾸면 나은가 | 예산 초과는 줄어도 **결품이 는다** — 맞바꿈, 채택 안 함(21장) |
| `convention_sweep.py` | 관행값 넷(`REBAL_MIN_QTY`·`ADJUST_MAX_ITER`·`ADJUST_BALANCE_OK`·`ADJUST_BALANCE_LIMIT`)이 결품에 영향을 주나 | **셋은 씨앗 잡음에 묻혀 무의미, `REBAL_MIN_QTY`만 유의하나 문턱 1~3은 평지** → 넷 다 현행 유지(19장) |
| `road_time_model.py` | 이동시간 계수를 쌓인 실측(고정 패널)으로 다시 추정하면 날짜·회차에 안정적인가 | 표본 밖 MAE 기준은 통과, **날짜별 변동계수 기준 미충족** — 아직 채택 안 함(20-A장, 자료 대기) |

`_convention_worker.py`·`_limit_plan_worker.py`는 표에 올리지 않았습니다 — 각각
`convention_sweep.py`·`limit_fixedpop_grid.py`가 셀 하나를 별도 프로세스로 돌리려고
부르는 워커일 뿐, 사람이 직접 실행하는 진입점이 아닙니다.

```powershell
python experiments/params/z_sweep.py            # 커버리지 vs 작업량
python experiments/params/predictor_compare.py  # 예측기 비교 + 무리별 진단
python experiments/params/seasonal_window.py    # 학습 창 비교
python experiments/params/gamma_sweep.py         # γ 다월·다씨앗 (120회, 약 90분)
python experiments/params/cluster_count_sweep.py  # 군집 수 K (약 15분)

# z 넓은 격자 — 16개 값 x 씨앗 5 x 3회차 = 240회. 오래 걸리니 --out을 꼭 줄 것
python experiments/params/z_fixedpop_grid.py --z-grid wide `
  --seeds "42,7,13,21,99" --out experiments/params/z_wide_2511.csv
```

셋 다 DB의 `net_demand`를 읽으므로 **여러 달의 순수요가 적재돼 있어야** 합니다
(연속된 달이 최소 2개). 적재는 `tools/load_rentals.py --split-by-month` 참고.

## baseline/ — 대조군 비교와 반복 실행 (논문용, 결과는 [../docs/분석/EXPERIMENTS.md](../docs/분석/EXPERIMENTS.md) 5장)

제안 방법이 **단순한 방법보다 정말 나은지**를 재는 자리입니다. 그전까지 모든 수치는
'재배치 전 → 후' 자기 비교뿐이었습니다 ([../docs/연구/THESIS.md](../docs/연구/THESIS.md) 3장).

| 파일 | 묻는 것 | 결론 |
| --- | --- | --- |
| `baseline_compare.py` | 군집·ILP·`z`는 각각 제 몫을 하나 | 셋 다 한다. 특히 계획 기준 지표로는 **대조군을 구분조차 못 한다** |
| `repeat_eval.py` | 그 차이가 달·씨앗을 바꿔도 유지되나 | 평균±표준편차와 Wilcoxon 검정으로 확인 |
| `gamma_recheck.py` | `γ = 3000`이 다른 달에서도 맞나 | 편익(결품)과 비용(거리·시간)을 함께 본다 |
| `budget_enforce.py` | 시간 예산을 제약으로 걸면 무엇을 잃나 | 초과 4건 → 0건, 대가는 **결품 +40초**. 기본은 꺼 둠 |
| `ortools_gap.py` | greedy 경로가 최적에서 얼마나 떨어져 있나 | 갭을 재고, **빠져 있는 depot 복귀**도 함께 잰다 |
| `ortools_gap_seeds.py` | 위 갭이 **씨앗에 얼마나 흔들리나** | **2.5배 흔들린다**(평균 1.93~4.86%, 최대 9.0~19.5%) — 논문의 "2.2%·최악 10.1%"는 씨앗 하나의 값이었다. 교체 결론은 유지, 서술만 범위로 고침(24장) |

`ortools_gap.py`만 별도 설치가 필요합니다 — **파이프라인 의존성이 아닙니다.**

```powershell
pip install ortools      # 이 실험 전용
```

```powershell
python experiments/baseline/baseline_compare.py --period "25년 11월" --plan-basis
python experiments/baseline/repeat_eval.py --periods "25년 09월,25년 10월,25년 11월" --methods P,B0,B1
```

- 대조군은 **B0 무재배치 / B1 그리디(군집·ILP 없음) / B2 평균 목표재고(z=0) /
  B3 지리 균등 군집(불균형 조정 없음)** 넷입니다.
- 판정은 **결품 시간**으로 합니다 — 개선률·목표 도달률은 `target_qty`가 분모라
  z가 다른 B2와는 비교조차 할 수 없습니다.
- 평가는 **VRP가 실제로 옮긴 대수**로 합니다. 계획량(`rebal_qty`)으로 재면
  군집·ILP를 건너뛴 B1도 같은 점수가 나옵니다.
- 두 스크립트 모두 파이프라인 함수를 **그대로 호출합니다**(`build_stats`,
  `compute_rebal_qty`, `select_top_unbalanced_st`, `make_clustering`,
  `adjust_clustering`, `solve_cluster_moves`, `greedy_route`, `_stockout_hours`).
  측정 코드가 제 방식대로 계산하면 측정이 거짓말을 합니다.

## structure/ — 설계를 정하기 위한 측정

파라미터가 아니라 **설계를 정하기 위해** 잰 것들입니다. 결과는
[docs/구현/steps/step0_raw.md](../docs/구현/steps/step0_raw.md)에 정리돼 있습니다.

| 파일 | 물음 | 결론 |
| --- | --- | --- |
| `net_vs_volume.py` | 이용량이 흔들리면 재배치 필요량도 흔들리나 | 그렇다(R² 0.88~0.96). 날씨를 붙일 값어치가 있다 |
| `weekend_profile.py` | 평일과 휴일을 한 통계로 묶어도 되나 | 안 된다. 33~37%가 부호 반대 — 섞으면 상쇄된다 |
| `holiday_impact.py` | 공휴일을 평일에서 빼면 얼마나 달라지나 | 연휴 낀 달의 작업 대상이 20~50% 늘어난다 |
| `demand_distribution.py` | 순수요가 정규분포인가 | 대여소별로는 거의 정규. 문제는 꼬리가 아니라 추정 오차 |
| `quantile_model_eval.py` | 분위수 모델이 mu+z·sigma를 이기나 | **아직 못 이긴다**(3개 검증 달 중 2패) |
| `park2024_compare.py` | 같은 도시 선행연구(박정연 외 2024)의 모형이 mu+z·sigma를 이기나 | **못 이긴다** — 평일·휴일 모두 본 연구 우세(THESIS 9번, 2026-08-27 종료) |
| `pick_feasibility.py` | 빼 올 자전거가 정말 있나 | **있다.** 후보의 재고 0 비율 0%, 못 채우는 양 3.5%. '재고 0이 48.8%'는 전체 평균이었다 |
| `history_window.py` | 1년치를 하드/소프트 스플릿으로 쓰면 나은가 | **아니다.** 과거를 많이 넣을수록 나빠진다. 겉보기 1위는 여유분(sigma)을 12% 키워 산 것이었다 |
| `temporal_signal.py` | LSTM 같은 시퀀스 모델을 붙일 값어치가 있나 | **낮다.** 자기상관 0.79는 '기억'이 아니라 대여소 수준 차이다(lag 10일에도 0.77). 잔차 기억은 최대 0.30 |
| `cross_station.py` | 대여소끼리 정보를 빌려 표본 부족을 메울 수 있나 | **아니다.** 개선 2.1%에 달별 승률 44~78%(우연). 대여소 간 진짜 차이가 추정 잡음의 **8.4배**라 뭉개는 손해가 크다 |
| `od_flow.py` | 대여 흐름(OD)에 순수요 예측을 도울 신호가 있나 | **없다.** OD 구조는 안정적(상관 0.78)이지만, 흐름 이웃으로 당기면 최적 w=0(3/9). 이웃을 어떻게 정의하든 대여소 자신의 신호가 압도한다 |
| ~~`travel_time_model.py`~~ | ~~이동시간 예측~~ | **삭제됨(1.23.1).** 정답표인 줄 안 `cum_sec`이 파이프라인 자신의 추정치였다 — 전제가 틀려 결과가 무의미했다. [ML_ATTEMPTS.md](../docs/분석/ML_ATTEMPTS.md) 7번 |
| `observed_stockout.py` | 결품을 관측에서 직접 세면 복원과 얼마나 다른가 | 실측이 **+13% 크다**(복원은 하한이 맞았다). 수집 하루치라 잠정 |
| `stock_decompose.py` | 재고 변화 중 **이용자 몫만 얼마나 크나**(트럭·사람 분리 문턱 1단계) | 이용자만으로도 10분에 **최대 26대**가 움직인다 — 단순 점프 문턱으론 못 가른다. 문턱 8이면 오인율 0.075%(ML_OPPORTUNITIES ②) |
| `outlier_impact.py` | 이상치 제거가 계획을 바꾸나 | 바꾼다(작업 대상 13.4%). **그런데 IQR이 자르는 것은 오류가 아니라 정상 상위 4%였다** — 옮기지 않는다 |
| `budget_split.py` | 예산 초과 군집을 **쪼개면** 예산을 지키나 | 🔴 **지키는 것처럼 보이지만 일을 버린다** — 좌표로 가르면 ILP가 짝지은 pick↔drop이 깨져 **4.5~22.1%를 못 옮긴다.** 배율 1.32에서는 21대를 다 써도 초과가 남는다. 쪼개려면 **ILP를 다시 풀어야** 한다(26장) |
| `multi_cluster_route.py` | 차량 1대가 **군집 여럿을 이어 돌면** 값어치가 있나 | **맞교환이다.** 2개씩 묶으면 차량 48 → **25대**·거리 **−23.2%**·차고지 왕복 65% → 43%, 대가는 최장 소요 2.6 → **3.5시간**. 3개씩은 17대·−34.6%지만 5.2시간. 현행 유지 — **성능 한계가 아니라 미확인 운영 조건**이다(8·27장) |
| `fleet_outage_stress.py` | 차량 정비 결원이 **여러 회차** 이어지면 견디나 | **5대까지 견딘다**(25장) |
| `fulfill_gross.py` | 수요 충족률을 **총 대여 건수**로 재면 달라지나 | **달라지고, 순서까지 뒤집힌다.** 순수요는 총 대여의 **18~44%만** 센다(분모 2.3~5.4배). `_15_20`은 총대여 기준이 **더 높다**(0.489 대 0.378) — 둘은 상·하한이 아니라 **다른 물음**이다(29장) |
| `weather_impact.py` | 날씨가 순수요를 설명하나 | 그렇다. 표본 밖 R² +0.412, 작업 대상 MAE +4.3% — **개선은 비 오는 날(10%)에 몰려 있다(+40%)** |
| `forecast_impact.py` | 일기예보의 **확률·유무코드**만으로 관측 수준(mm)의 순수요 개선을 재현하나 | **재현 못 한다**(측정 완료, WEATHER.md) |
| `forecast_grid_impact.py` | 격자 수치예보(mm, 관측과 같은 단위)로 바꾸면 신호가 돌아오나 | **돌아온다**(측정 완료, WEATHER.md) — 운영에 쓰려면 격자 API 신청이 더 필요 |

```powershell
python experiments/structure/budget_split.py --road-factor 1.32  # 초과 군집 쪼개기
python experiments/structure/multi_cluster_route.py --chain-size 2  # 군집 연쇄 주행
python experiments/structure/fulfill_gross.py       # 충족률 두 정의 비교
python experiments/structure/net_vs_volume.py     # 이용량 ↔ 필요량 상관
python experiments/structure/weekend_profile.py   # 평일/휴일 수요 구조 비교
python experiments/structure/holiday_impact.py    # 공휴일 제거 효과
python experiments/structure/demand_distribution.py            # 분포 진단
python experiments/structure/quantile_model_eval.py --holdout "25년 11월"   # 모델 채택 판정
python experiments/structure/weather_impact.py    # 날씨 → 이용량 → 순수요 전달 측정
python experiments/structure/outlier_impact.py    # 이상치 제거가 계획을 바꾸는가
python experiments/structure/observed_stockout.py  # 관측 재고로 결품 실측
python experiments/structure/park2024_compare.py   # 선행연구 모형 대 μ+zσ
```

`rental_history`와 `net_demand`를 읽습니다. `weather_impact.py`는 여기에 더해
`data/raw_data/날씨`의 관측 자료가 있어야 합니다(docs/분석/WEATHER.md).

## diagnostic/ — 중간 산출물 진단

파이프라인이 만든 것을 **눈으로 확인**하는 자리입니다. 결론을 내는 실험이 아니라,
"지금 무엇이 나왔나"를 보는 도구에 가깝습니다.

| 파일 | 내용 | 원래 위치 |
| --- | --- | --- |
| `step0_rebal_qty_check.py` | 재배치량 진단 — 작업 대상 수와 **Pick·Drop 수급 격차** (1.18.8에서 되살림) | `step0_collect/test.py` |
| `step1_cluster_memo.py` | 클러스터링 실행 결과 메모 | `step1 (...)/test.py` |
| `tour_length_estimate.py` | 순회거리 어림(대여소 수 비례·BHH 근사 등) 셋 중 무엇이 실제 경로에 가까운가 — **세 어림 모두 현행(대여소 수 비례)보다 못하다**(13장, 5-H의 BHH 보류를 확정) | — |

### 지도 범례 시안은 채택돼 `mapviz.py`로 나갔습니다 (1.26.79 조사 → 1.26.80 채택)

여기 있던 `mapviz_shared.py`·`mapviz_compare.py`는 **더 이상 없습니다.**
시안이 채택돼 공용 모듈이 저장소 뿌리의 **[`mapviz.py`](../mapviz.py)**로
옮겨졌고, 세 지도 생성기가 그것을 직접 씁니다. 비교 스크립트는 프로덕션을
그대로 베낀 사본이 되어(같은 범례를 두 벌 유지하게 됩니다) 지웠습니다.

**남길 것은 그 비교가 무엇을 밝혔는가입니다.** 여섯 장(시안 3·기존 3)을
`2026-08-28 도로실측2 _15_20`으로 실제로 띄워 재지 않았으면 못 봤을 것들입니다:

| 지도 | 범례 높이 (기존 → 채택) | |
| --- | --- | --- |
| step4 재고 현황 | 144 → **197px** | 영어 `Legend`·회색 2px 테두리가 사라졌습니다 |
| step1 군집 | 없음 → **149px** | 원래 범례가 **아예 없던** 화면입니다 |
| step3 경로 | 199 → **234px** | |

- **범례를 접지 않으면 지도를 가립니다.** 처음 시안은 18개 군집을 한 줄씩
  세워 610·688px, 화면 세로의 2/3를 먹었습니다. 군집 목록만 `<details>`로
  접어 149·234px가 됐습니다(펴면 509·594px, 그 안에서 스크롤).
- **검정은 어둡게 만들 수 없습니다.** 8색을 순환하며 한 단계씩 어둡게 하는데
  `#000000`은 0에 무엇을 곱해도 0이라 **군집 7과 15가 같은 색**이었습니다
  (실측: RGB 거리 0). 어두운 색은 밝히는 쪽으로 돌리게 고쳤습니다.
- **경로 범례에서 `▶ 차량 이동 방향`이 빠져 있었습니다.** 지도에는
  `PolyLineTextPath`로 화살표를 실제로 그리는데 범례가 그 기호를 설명하지
  않아, 시안이 기존보다 뒤로 간 자리였습니다.

셋 다 `tests/test_mapviz.py`가 지킵니다.

## learning/ — 학습용 예제

**파이프라인과 무관합니다.** 라이브러리를 익히며 남긴 것이라 지우지 않고 모아 뒀습니다.

| 파일 | 내용 | 원래 위치 |
| --- | --- | --- |
| `pulp_test.py` | PuLP 라이브러리 튜토리얼 | `test/` |
| `pulp_test2.py` | PuLP 최소 예제 | `test/` |
| `matplotlib_month_graph.py` | 월별 대여량 그래프(더미 데이터, 한글 폰트 설정 예시) | `test/test.py` |
