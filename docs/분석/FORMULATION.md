# 문제 정형화 — 기호·수식·제약

> 2026-08-21 작성. 논문 3장(문제 정형화)의 원본입니다.
> **여기 있는 모든 수식은 코드에서 그대로 옮긴 것입니다.** 코드를 바꾸면 이 문서도
> 바꿔야 합니다 — 근거는 각 절 끝의 파일·함수 표시를 보세요.
> 파라미터를 왜 그 값으로 두었는지는 [EXPERIMENTS.md](EXPERIMENTS.md),
> 논문 전체 구성은 [THESIS.md](../연구/THESIS.md)에 있습니다.

---

## 1. 기호

| 기호 | 뜻 | 코드 |
| --- | --- | --- |
| $S$ | 전체 대여소 집합 ($\|S\| = 1{,}349$) | `station_info` |
| $i, j$ | 대여소 | `station_id` |
| $c_i$ | 대여소 $i$의 거치대 수 | `parking_lot` |
| $q_i$ | 대여소 $i$의 현재 재고 | `stock` |
| $D$ | 계획 시간대 (예: `_05_10` = 05~10시) | `duration` |
| $H(D)$ | 시간대 $D$에 속한 시각 목록 (자정을 넘기면 이어 돈다) | `duration_hours()` |
| $n_{i,d,h}$ | 대여소 $i$의 $d$일 $h$시 순수요 (대여 − 반납) | `net_demand.net_HH` |
| $\mu_i$ | 시간대 $D$의 일평균 순수요 | `mu` |
| $\sigma_i$ | 시간대 $D$의 순수요 표준편차 | `sigma` |
| $s$ | 계절 배율 (도시 전체 하나) | `season_ratio()` |
| $z$ | 안전재고 계수 = **1.99** | `TARGET_Z` |
| $t_i$ | 목표 재고 | `target_qty` |
| $r_i$ | 재배치량 (양수 = Drop 필요, 음수 = Pick 가능) | `rebal_qty` |
| $K$ | 군집 수 = 투입 차량 수 | `cluster` |
| $C_k$ | $k$번째 군집 | — |
| $m_k$ | 군집 $k$의 메도이드 | `compute_medoids()` |
| $Q$ | 차량 적재 용량 = **10대** | `VEHICLE_CAPACITY` |
| $v$ | 차량 속도 = **25 km/h** | `VEHICLE_SPEED_KMPH` |
| $B$ | 회차 시간 예산 = **120분** | `TIME_BUDGET_MINUTES` |
| $M$ | 보유 차량 = **21대**(상한), 회차당 투입 상한 = **21대** — 실제 K는 작업량이 정한다(12~16) | `FLEET_SIZE`, `VEHICLES_PER_ROUND` |
| $0$ | depot (타슈 관제센터 ST0001) | `DEPOT_ID` |
| $\theta$ | 작업 대상 임계 = **2대** | `REBAL_MIN_QTY` |
| $N$ | Pick·Drop 각각 상위 컷 = **50곳** | `TOP_STATION_LIMIT` |
| $s_{\text{pick}},\, s_{\text{drop}}$ | 자전거 1대 싣기·내리기 = **각 30초** | `PICK_TIME_SEC`, `DROP_TIME_SEC` |
| $c_{\text{tr}}$ | 대여소 하나당 이동 시간 = **12.5분** (실측) | `TRAVEL_MIN_PER_STATION` |
| $\rho$ | 군집 불균형 여유 = **1.4** (최장/평균 실측) | `CLUSTER_IMBALANCE_ALLOWANCE` |

**운영 모델:** 하루 3회차(`_05_10`, `_10_15`, `_15_20`), **차량 1대 = 군집 1개**,
보유 21대는 누적 부하 기준으로 로테이션한다([FLEET.md](../구현/FLEET.md)).
회차당 투입 대수는 **고정이 아니라 그 회차의 작업량이 정한다**(`wanted_vehicles()`,
실측 12~16대) — 보유 21대는 그 상한이다. 작업을 마치면 **depot으로 복귀하며,
1.19.1부터 거리·시간 계산에 포함된다** — 6장 참고.

---

## 2. 수요 추정과 목표 재고

### 2.1 시간대 순수요

$$
N_{i,d} = \sum_{h \in H(D)} n_{i,d,h}, \qquad
\mu_i = \operatorname{mean}_d N_{i,d}, \qquad
\sigma_i = \operatorname{std}_d N_{i,d}
$$

$d$는 **평일과 휴일 중 한쪽만** 쓴다. 휴일 = 주말 ∪ 공휴일이며, 두 구분을 섞으면
대여소의 33~37%가 부호가 반대라 상쇄된다([step0_raw.md](../구현/steps/step0_raw.md)).

> 코드: `calculate_target_qty.build_stats()`, `project_config.select_day_type()`

### 2.2 계절 배율 (warmup)

지난달 통계로 이번 달을 맞히므로, 계절이 도약하는 달에는 구조적으로 낮게 나온다
(2월→3월 수요 1.5~1.8배). 계획 대상 달의 **첫 $W = 14$일** 실적으로 배율 하나를 구한다.

$$
s = \operatorname{clip}\!\left(
\frac{\sum_{i \in S^{+}} \left| \overline{N^{\text{head}}_i} \right|}
     {\sum_{i \in S^{+}} \left| \mu_i \right|},\ 0.5,\ 2.0 \right),
\qquad
S^{+} = \{\, i : |\mu_i| > 2 \,\}
$$

- **도시 전체 배율 하나**로만 추정한다. 대여소별로 나누면 며칠치 표본이라 잡음만 커진다.
- $S^{+}$로 수요가 0 근처인 대여소를 빼는 것이 핵심이다. 넣으면
  $\mathbb{E}|\hat{X}| > |\mathbb{E}X|$ 때문에 배율이 **과대추정**된다.
- 보정은 $\mu_i \leftarrow s\,\mu_i$, $\sigma_i \leftarrow s\,\sigma_i$ — **중심을 옮기는 것**이지
  분산만 부풀리는 $z$와 다르다.

> 코드: `demand_model.warmup_ratio()`, `demand_model.apply_warmup()`
> 근거: [EXPERIMENTS.md](EXPERIMENTS.md) 3장

### 2.3 목표 재고

$$
t_i =
\operatorname{clip}\!\left(
\begin{cases}
s\mu_i + z \cdot s\sigma_i & (\mu_i \ge 0) \\[4pt]
q_i + s\mu_i & (\mu_i < 0)
\end{cases}
,\ 0,\ 1.5\,c_i \right)
$$

- $\mu_i \ge 0$ (**순유출** — 대여가 반납보다 많다): 그 시간대에 빠져나갈 양을 미리 채워 둔다.
- $\mu_i < 0$ (**순유입** — 반납이 더 많다): 지금 재고에서 들어올 양만큼 여유를 둔다.
- $z = 1.99$는 정규분포 관행값(1.65)이 아니라 **12개월 백테스트로 정한 값**이다.
  1.65에서는 커버리지가 92.5%뿐이었다([EXPERIMENTS.md](EXPERIMENTS.md) 1장).

### 2.4 재배치량

$$
r_i = \operatorname{int}\!\left( Q \cdot \tanh\!\left(\frac{t_i - q_i}{Q}\right) \right),
\qquad Q = 10
$$

$\tanh$는 한 대여소에 몰리는 작업량을 차량 적재 용량 근처에서 부드럽게 자른다
(정수화는 0 방향으로 — 양수는 `floor`, 음수는 `ceil`).

> 코드: `calculate_target_qty.compute_rebal_qty()`

---

## 3. 작업 대상 선정

$$
S_{\text{cand}} = \{\, i \in S : |r_i| > \theta \,\}, \qquad \theta = 2
$$

$$
P = \operatorname{top}_{N}\{\, i : r_i < 0 \,\}\ (|r_i| \text{ 내림차순}), \qquad
G = \operatorname{top}_{N}\{\, i : r_i > 0 \,\}\ (r_i \text{ 내림차순}), \qquad N = 50
$$

Pick 가능량과 Drop 필요량 중 **적은 쪽**까지만 남긴다 (누적합 컷):

$$
\Lambda = \min\!\left( \Big| \sum_{i \in P} r_i \Big|,\ \sum_{i \in G} r_i \right),
\qquad
P' = \{\, i \in P : |\text{cumsum}(r)_i| \le \Lambda \,\},\quad
G' = \{\, i \in G : \text{cumsum}(r)_i \le \Lambda \,\}
$$

받아 줄 곳이 없는데 싣기만 하는 계획을 막는 장치다.
**$P'$ 또는 $G'$가 비면 그 회차는 건너뛴다** — 한쪽만 있으면 재배치가 성립하지 않는다.

> 코드: `top_st_clustering.select_top_unbalanced_st()`
> $\theta$·$N$은 `project_config`에서 읽는다(1.18.8).
> ⚠️ **셋 다 실험으로 정한 값이 아니라 관행값이다.** $z$·$\gamma$와 달리 재실험
> 기록이 없으므로, 논문에서 근거를 묻는다면 그렇게 답해야 한다.

---

## 4. 군집화 — 차량별 담당 구역

### 4.1 군집 수

**군집 1개 = 차량 1대**이므로, 군집 수는 그 회차의 **작업량**이 정한다(1.19.1).
한 대가 감당할 수 있는 양을 시간으로 따진다.

$$
\hat{T} = \Lambda \,(s_{\text{pick}} + s_{\text{drop}}) + |P' \cup G'| \cdot c_{\text{tr}},
\qquad
K = \min\!\left( \left\lceil \frac{\rho \hat{T}}{B} \right\rceil,\ M_{\text{round}} \right)
$$

$\hat{T}$는 회차 전체의 추정 소요시간이다. 앞항(작업)은 정확히 계산되고,
뒷항(이동)은 대여소 수에 비례한다고 본다 — 같은 재고로 $K$만 바꿔 재면 총 소요시간이
거의 변하지 않기 때문이다(군집을 쪼개면 depot 왕복이 늘지만 군집 안 이동이 그만큼 준다).
$\rho$를 곱하는 이유는 시간 예산 $B$가 **평균이 아니라 최장 군집**으로 판정되기 때문이다.

$M_{\text{round}}$(회차당 투입 상한)는 기본값이 보유 대수와 같아 사실상 걸리지 않는다.
현장 사정으로 줄이면 상한에 걸리고, step1이 몇 대가 모자란지 경고한다.

> 1.19.1 이전에는 $K = \min(\lceil |P' \cup G'| / 7 \rceil, 10)$이었다. 7에 근거가
> 없었고, 실데이터에서는 늘 상한 10에 걸려 **사실상 10대 고정**이었다.

### 4.2 초기 군집

좌표 $(\text{lat}, \text{lon})$에 대해 맨해튼 거리 K-Medoids(FasterPAM). 좌표는
스케일링하지 않는다 — 위경도 자체가 거리 단위다.

### 4.3 목적함수와 조정

초기 군집은 지리적으로만 뭉쳐 있어 **군집 안의 수급이 맞지 않는다.** 아래 목적함수를
줄이는 방향으로 대여소를 한 개씩 옮긴다(greedy, 최대 200회).

$$
J(\mathcal{C}) =
\underbrace{\alpha \sum_{k} \Big( \sum_{i \in C_k} r_i \Big)^{2}}_{\text{수급 불균형}}
+ \underbrace{\beta \cdot \operatorname{mean}_k \Big( |C_k| - \tfrac{n}{K} \Big)^{2}}_{\text{크기 균일}}
+ \underbrace{\gamma \sum_k \sum_{i \in C_k} \| x_i - m_k \|_1}_{\text{군집 내 거리}}
$$

$$
\alpha = 1, \qquad \beta = 100, \qquad \gamma = 3000
$$

종료 조건: $\max_k \left| \sum_{i \in C_k} r_i \right| \le 3$(`ADJUST_BALANCE_OK`)이거나
더 이상 개선이 없을 때. 반복 상한은 200회(`ADJUST_MAX_ITER`), 재조정 대상 기준은
$\left| \sum r_i \right| > 5$(`ADJUST_BALANCE_LIMIT`)다. 이 셋도 관행값이다.

> **$\gamma$는 비단조다.** $\gamma = 2000$이 $\gamma = 1000$보다 나빴다. 두 점을 재고
> 사이를 보간하면 안 된다. $\gamma = 10$~150 구간에서는 거리 항이 불균형 항에 묻혀
> 이동 자체가 일어나지 않는다([EXPERIMENTS.md](EXPERIMENTS.md) 4장).
>
> 코드: `adjust_module.compute_objective()`, `top_st_clustering.adjust_clustering()`

---

## 5. ILP — 군집 안의 이동 수량

군집 $k$마다 독립적으로 푼다. Pick 집합 $I = \{i \in C_k : r_i < 0\}$,
Drop 집합 $J = \{j \in C_k : r_j > 0\}$, 공급 $s_i = |r_i|$, 수요 $d_j = r_j$.

**결정변수** $x_{ij} \in \mathbb{Z}_{\ge 0}$ — $i$에서 $j$로 옮기는 자전거 대수.

**목적함수** — **대수 가중 이동시간**의 합 최소화 (가까운 곳끼리 많이 옮기도록)

$$
\min \sum_{i \in I} \sum_{j \in J} T_{ij}\, x_{ij},
\qquad
T_{ij} = \frac{\operatorname{haversine}(i, j)}{v} \times 3600 \ \text{(초)}
$$

**제약**

$$
\begin{aligned}
\text{(공급)}\quad & \sum_{j \in J} x_{ij} \le s_i && \forall i \in I \\
\text{(수요)}\quad & \sum_{i \in I} x_{ij} \le d_j && \forall j \in J \\
\text{(작업량 강제)}\quad & \sum_{i \in I} \sum_{j \in J} x_{ij} = \Lambda_k,
& \Lambda_k &= \min\Big( \sum_i s_i,\ \sum_j d_j \Big)
\end{aligned}
$$

세 번째 제약이 없으면 **아무 것도 옮기지 않는 해**($x = 0$)가 최적이 된다.

> ⚠️ **이 목적함수는 실제 운행시간이 아니다.** 차량은 한 번에 최대 $Q = 10$대를 싣고
> 가므로 실제 소요시간은 방문 순서(6장)가 정한다. 여기서는 자전거 한 대가 $i 	o j$로
> 옮겨지는 시간을 대수만큼 더한 **대리 목적함수**를 쓴다. 논문에 "총 작업시간을
> 최소화한다"고 쓰면 심사에서 바로 걸린다 — **"대수로 가중한 이동시간의 합"** 이라고 써야 한다.

> **구조:** 이 문제는 공급·수요가 있는 **수송문제(최소비용흐름)** 다. 제약행렬이
> 전적으로 단모듈이라 **선형완화의 최적해가 자동으로 정수**다(무작위 40회 시행에서
> 연속변수로 풀어도 오차 0). 정수변수로 푸는 것은 안전장치이지 필요조건이 아니다.

**솔버:** CBC (PuLP 내장), 시간 제한 600초, 상대 갭 2%.
실측하면 클러스터 하나가 0.05~0.2초에 풀리고 **2% 갭이 남기는 손해는 0%** 다
(4×5 ~ 40×45 규모 4종). 즉 두 안전장치는 지금 규모에서 아무 일도 하지 않는다.

> 코드: `ilp.solve_cluster_moves()`

---

## 6. VRP — 방문 순서

차량 1대가 depot에서 출발해 군집 $k$의 작업을 처리한다.
노드는 $(i, \text{pick})$ 또는 $(j, \text{drop})$이며, 같은 대여소가 양쪽에 있을 수 있다.

> **작업을 마치면 depot으로 돌아온다 (1.19.1부터 계산에 포함).** 그전에는
> `greedy_route()`가 그 자리에서 멈춰, 중간에 적재가 막혀 돌아가는 경우에만 복귀가
> 기록됐다(실데이터에서 `vrp_plan`의 `return` 행 0건). 그래서 소요시간·이동거리가
> **복귀분만큼 과소 추정**이었고 시간 예산 판정도 낙관적이었다 —
> 1.19.1 이전에 잰 표를 인용할 때는 그 점을 밝혀야 한다.


**greedy 선택 규칙** — 현재 위치 $p$, 적재량 $\ell$에서 다음 노드는

$$
\arg\min_{u}\ \text{score}(u),
\qquad
\text{score}(u) = \frac{\operatorname{haversine}(p, u)}{a_u} \times
\begin{cases} 0.1 & (a_u = \text{잔여량}_u) \\ 1 & (\text{그 외}) \end{cases}
$$

$$
a_u =
\begin{cases}
\min(\text{잔여량}_u,\ Q - \ell) & (u \text{가 pick이고 } \ell < Q) \\
\min(\text{잔여량}_u,\ \ell) & (u \text{가 drop이고 } \ell > 0) \\
\text{후보 제외} & (\text{그 외})
\end{cases}
$$

$0.1$ 보너스는 **노드를 한 번에 끝낼 수 있으면 우선한다**는 뜻이다(재방문 회피).

코드에는 "후보가 없으면 depot으로 복귀해 다시 시작한다"는 분기가 있지만,
**ILP를 거친 입력에서는 실행될 수 없다.** 5장의 '작업량 강제' 제약이 군집마다
$\sum_i s_i' = \sum_j d_j'$를 보장하므로, 임의 시점에

$$
\text{남은 drop} = \text{남은 pick} + \ell
$$

이고 따라서 $\ell = Q$(못 실음)면 내릴 곳이, $\ell = 0$(못 내림)면 실을 곳이 반드시
남아 있다. **후보가 비는 상태 자체가 존재하지 않는다.**

**소요시간**

$$
\tau = \sum \left( \frac{\text{거리}}{v} \times 3600 \right) + 30 \times (\text{싣기} + \text{내리기 대수})
$$

**시간 예산**

$$
\tau_k \le B = 120\ \text{분}
$$

⚠️ **이 제약은 현재 모델에 들어 있지 않다.** 계산 후 초과 여부를 경고할 뿐 계획을
바꾸지 않는다(사후 점검). 논문에서는 이를 한계로 밝혀야 한다 — [THESIS.md](../연구/THESIS.md) 6장.
`greedy_route(time_budget_sec=…)`로 예산 안에서 멈추는 경로도 만들 수 있지만
파이프라인은 쓰지 않는다(대조군 실험 전용).

> 코드: `vrp.greedy_route()`

---

## 7. 평가 지표

### 7.1 결품 시간 (주 지표)

재고 궤적을 순수요로 복원한다.

$$
q_i(h+1) = \operatorname{clip}\big( q_i(h) - n_{i,d,h},\ 0,\ c_i \big),
\qquad
\text{Stockout}_i = \sum_{h \in H(D)} \mathbb{1}\!\left[ q_i(h) \le 0 \right]
$$

재배치 전은 $q_i(0) = q_i$, 재배치 후는 $q_i(0) = q_i + \Delta_i$.

- $\Delta_i = r_i$ — **계획 기준**. step4가 쓰는 방식이며, 계획이 100% 집행된다고 가정한다.
- $\Delta_i$ = VRP가 실제로 싣고 내린 양 — **집행 기준**. 대조군 비교는 이쪽을 쓴다
  (`experiments/baseline/baseline_compare.py`). 계획 기준으로 재면 군집·ILP를 건너뛴 대조군도
  같은 점수가 나와 비교가 성립하지 않는다.
- 0에서 자르므로 이 값은 **결품의 하한**이다. 못 빌린 수요는 사라진다.

> 코드: `imbalance._stockout_hours()`

### 7.2 계획 달성률 (보조 지표)

$$
\text{improvement rate}_i = \frac{|q_i - t_i| - |q_i + r_i - t_i|}{|q_i - t_i|}
$$

⚠️ **$t_i$가 분모에 들어간다.** $r_i$가 $t_i - q_i$에서 파생되므로 구조적으로 높게
나오고, **$z$가 다른 실행끼리는 비교할 수 없다** — $z$를 올리면 결품이 줄어도 이 값은
떨어진다. 이용자 편익이 아니라 **계획 달성률**이다([KPI.md](KPI.md) 2장).

### 7.3 효율

$$
\text{improvement per km} = \frac{\sum_i \left( |q_i - t_i| - |q_i + r_i - t_i| \right)}
{\sum_k \text{거리}_k}
$$

편익만 재면 언제나 "더 크게"가 답이 된다. **비용을 같이 재야 멈출 곳이 보인다.**

---

## 8. 이 정형화의 한계 (논문 8장에 그대로)

| 한계 | 내용 |
| --- | --- |
| 거리 | ILP·VRP 모두 **직선거리(Haversine)** 다. 실도로(TMAP)는 step3 시각화에서만 쓴다 |
| 최적성 | VRP는 greedy 휴리스틱이며 **최적성 보장이 없다.** 갭을 측정하지 않았다 |
| 시간 예산 | 제약이 아니라 사후 점검이다 (6장) |
| 차량 | 1대 = 1군집 고정. 여러 군집을 이어 도는 구조는 채택하지 않았다(1.13.2 결정) |
| 작업시간 | 자전거 1대당 30초는 **현장 확인이 안 된 가정값**이다 |
| 재고 | $q_i$는 API 현재 스냅샷이고 $n_{i,d,h}$는 과거 달 실적이라 **시점이 다르다** |
| 회차 간 | 세 회차를 독립적으로 계획한다. 앞 회차의 결과가 뒤 회차의 초기 재고에 반영되지 않는다 |
| ~~depot 복귀~~ | ~~마지막 복귀 구간이 거리·시간에 빠져 있다~~ → **1.19.1에서 해소.** 계산에 포함된다(6장) |
| ~~평가 기준~~ | ~~step4는 계획량이 100% 집행된다고 가정한다~~ → **1.18.4에서 해소.** `executed_delta()`로 **VRP가 실제로 옮긴 양**을 쓴다(VRP 산출물이 없을 때만 계획량으로 폴백) ([EXPERIMENTS.md](EXPERIMENTS.md) 5장) |

## 관련 문서

| 문서 | 내용 |
| --- | --- |
| [THESIS.md](../연구/THESIS.md) | 논문 전체 구성과 남은 것 |
| [EXPERIMENTS.md](EXPERIMENTS.md) | $z$·$\gamma$·학습 창을 이 값으로 정한 근거 |
| [KPI.md](KPI.md) | 지표 체계와 그 함정 |
| [PROJECT_PIPELINE.md](../구현/PROJECT_PIPELINE.md) | 단계별 입출력 |
| [FLEET.md](../구현/FLEET.md) | 차량 로테이션·시간 예산 |
