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

```powershell
python experiments/params/z_sweep.py            # 커버리지 vs 작업량
python experiments/params/predictor_compare.py  # 예측기 비교 + 무리별 진단
python experiments/params/seasonal_window.py    # 학습 창 비교
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
| `pick_feasibility.py` | 빼 올 자전거가 정말 있나 | **있다.** 후보의 재고 0 비율 0%, 못 채우는 양 3.5%. '재고 0이 48.8%'는 전체 평균이었다 |
| `history_window.py` | 1년치를 하드/소프트 스플릿으로 쓰면 나은가 | **아니다.** 과거를 많이 넣을수록 나빠진다. 겉보기 1위는 여유분(sigma)을 12% 키워 산 것이었다 |
| `temporal_signal.py` | LSTM 같은 시퀀스 모델을 붙일 값어치가 있나 | **낮다.** 자기상관 0.79는 '기억'이 아니라 대여소 수준 차이다(lag 10일에도 0.77). 잔차 기억은 최대 0.30 |
| `cross_station.py` | 대여소끼리 정보를 빌려 표본 부족을 메울 수 있나 | **아니다.** 개선 2.1%에 달별 승률 44~78%(우연). 대여소 간 진짜 차이가 추정 잡음의 **8.4배**라 뭉개는 손해가 크다 |
| `od_flow.py` | 대여 흐름(OD)에 순수요 예측을 도울 신호가 있나 | **없다.** OD 구조는 안정적(상관 0.78)이지만, 흐름 이웃으로 당기면 최적 w=0(3/9). 이웃을 어떻게 정의하든 대여소 자신의 신호가 압도한다 |
| ~~`travel_time_model.py`~~ | ~~이동시간 예측~~ | **삭제됨(1.23.1).** 정답표인 줄 안 `cum_sec`이 파이프라인 자신의 추정치였다 — 전제가 틀려 결과가 무의미했다. [ML_ATTEMPTS.md](../docs/분석/ML_ATTEMPTS.md) 7번 |
| `observed_stockout.py` | 결품을 관측에서 직접 세면 복원과 얼마나 다른가 | 실측이 **+13% 크다**(복원은 하한이 맞았다). 수집 하루치라 잠정 |
| `outlier_impact.py` | 이상치 제거가 계획을 바꾸나 | 바꾼다(작업 대상 13.4%). **그런데 IQR이 자르는 것은 오류가 아니라 정상 상위 4%였다** — 옮기지 않는다 |
| `weather_impact.py` | 날씨가 순수요를 설명하나 | 그렇다. 표본 밖 R² +0.412, 작업 대상 MAE +4.3% — **개선은 비 오는 날(10%)에 몰려 있다(+40%)** |

```powershell
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

## learning/ — 학습용 예제

**파이프라인과 무관합니다.** 라이브러리를 익히며 남긴 것이라 지우지 않고 모아 뒀습니다.

| 파일 | 내용 | 원래 위치 |
| --- | --- | --- |
| `pulp_test.py` | PuLP 라이브러리 튜토리얼 | `test/` |
| `pulp_test2.py` | PuLP 최소 예제 | `test/` |
| `matplotlib_month_graph.py` | 월별 대여량 그래프(더미 데이터, 한글 폰트 설정 예시) | `test/test.py` |
