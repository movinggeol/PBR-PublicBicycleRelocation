"""부록 B(핵심 코드)의 발췌를 **원본 파일에서 글자 그대로** 뽑아 다시 쓴다.

## 왜 도구인가

학과 양식이 구현한 프로그램의 핵심 코드를 부록으로 요구한다. 그 발췌를 손으로 옮기면
공백 하나만 달라져도 원본과 대조할 수 없고, 코드가 바뀌면 부록만 조용히 낡는다 —
이 저장소가 여러 번 겪은 *"한 곳만 고치면 다른 곳이 낡는다"* 의 한 갈래다.

여기서 **줄 번호로** 뽑아 두면 두 가지가 따라온다.

  · 코드를 고친 뒤 이 스크립트만 다시 돌리면 부록이 따라온다.
  · `check_consistency.py`의 부록 검사가 발췌 한 줄 한 줄을 원본에서 찾아 본다
    (주석은 뺀다 — 부록의 주석은 식을 읽히려고 여기서 새로 단 것이다).

⚠️ **줄 번호는 코드가 바뀌면 어긋난다.** 검사가 그것을 잡으므로, 검사가 물면
아래 범위를 고치고 다시 돌린다. 범위를 짐작으로 넓히지 마라 — 식에 대응하는
부분만 싣는 것이 부록의 요구다.

사용법:
    python tools/make_appendix_code.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import project_config  # noqa: F401  (콘솔 인코딩을 먼저 세운다)

OUT = "docs/연구/논문/부록_핵심코드.md"


def _lines(rel: str) -> list[str]:
    return (ROOT / rel).read_text(encoding="utf-8").splitlines()


def block(rel: str, items) -> str:
    """items: ("l", 시작, 끝[, 내어쓸 칸]) 원본 줄 · ("c", 주석) · ("b",) 빈 줄

    네 번째 값은 `if`/`else` 갈래를 걷어 낸 자리에서 그 줄만 더 내어쓰기 위한 것이다.
    **코드 글자는 건드리지 않는다** — 앞 공백만 덜어 내므로 원본과 대조가 계속 된다.
    """
    src = _lines(rel)
    picked: list[str] = []
    for it in items:
        if it[0] == "l":
            extra = it[3] if len(it) > 3 else 0
            for i in range(it[1] - 1, it[2]):
                picked.append(src[i].rstrip()[extra:])
        elif it[0] == "c":
            picked.append(it[1])
        else:
            picked.append("")
    solid = [x for x in picked if x.strip()]
    pad = min((len(x) - len(x.lstrip()) for x in solid), default=0)
    return "\n".join(x[pad:] if x.strip() else "" for x in picked)


B1 = block("pipeline/step0_collect/calculate_target_qty.py", [
    ("c", "    # 계절 배율 s를 mu와 sigma에 곱한다(분산만 부풀리는 z와 다르다)"),
    ("l", 103, 103), ("b",),
    ("c", "    # 목표 재고 t_i — 순유출이면 빠져나갈 양에 여유를 더해 채우고,"),
    ("c", "    #                순유입이면 들어올 양만큼 비운다"),
    ("l", 128, 128),
    ("l", 138, 140, 4),
    ("l", 142, 144), ("b",),
    ("c", "    # 상한은 거치대 수의 1.5배(up_limit), 하한은 0"),
    ("l", 148, 148), ("b",),
    ("c", "    # 재배치량 r_i — 목표와 현재의 차이를 적재 용량 근처에서 완만하게 자른다."),
    ("c", "    # tanh는 1에 점근하므로 자르기(clip)와 달리 작은 값도 함께 눌린다."),
    ("l", 155, 155),
    ("l", 171, 173), ("b",),
    ("c", "    # 0 방향으로 정수화한다"),
    ("l", 176, 180),
])

B2 = block("pipeline/step1_cluster/top_st_clustering.py", [
    ("c", "    # 재배치량의 절댓값이 임계값을 넘는 대여소만 후보로 삼는다"),
    ("l", 196, 201), ("b",),
    ("c", "    # 수거 후보 P — 작업량이 큰 순서로 상위 N곳"),
    ("l", 206, 210), ("b",),
    ("c", "    # 배송 후보 G — 같은 방식으로 상위 N곳"),
    ("l", 212, 216),
])

B3 = block("pipeline/step1_cluster/top_st_clustering.py", [
    ("c", "    # 처리 대수는 배송 합이다. 수거 합과 크기가 같으므로 둘을 더하면 두 번 센다."),
    ("l", 283, 283), ("b",),
    ("l", 285, 288),
])

B4 = block("pipeline/step1_cluster/adjust_module.py", [
    ("l", 62, 71), ("b",),
    ("c", "        # 군집 하나의 (재배치량 합의 제곱, 중앙점까지의 맨해튼 거리 합)"),
    ("l", 83, 83),
    ("l", 86, 87), ("b",),
    ("c", "    # 크기 항만 군집 수로 나눈다 — 식의 mean_k에 해당한다"),
    ("l", 91, 92),
])

B5 = block("pipeline/step2_optimize/ilp.py", [
    ("c", "    # 수거 집합 I와 배송 집합 J"),
    ("l", 185, 186), ("b",),
    ("c", "    # 비용 계수 — 직선거리를 식 (3.12)의 이동시간으로 바꾼다"),
    ("l", 193, 196), ("b",),
    ("l", 199, 199), ("b",),
    ("c", "    # 결정변수 x_ij — i에서 j로 옮기는 자전거 대수(음이 아닌 정수)"),
    ("l", 203, 203),
    ("l", 207, 207, 4), ("b",),
    ("c", "    # 목적함수 — 대수를 가중한 이동시간의 합"),
    ("l", 215, 215), ("b",),
    ("l", 218, 219), ("b",),
    ("c", "    # 제약 1 : 수거 노드의 공급 제한"),
    ("l", 222, 223), ("b",),
    ("c", "    # 제약 2 : 배송 노드의 수요 제한"),
    ("l", 226, 227), ("b",),
    ("c", "    # 제약 3 : 총 이동량 강제. 없으면 아무것도 옮기지 않는 해가 최적이 된다."),
    ("l", 230, 230),
])

B6 = block("pipeline/step2_optimize/vrp.py", [
    ("c", "        # 점수가 가장 작은 후보를 다음 방문지로 고른다"),
    ("l", 126, 131), ("b",),
    ("c", "            # 처리 가능량 a_u — 수거는 남은 적재 여유, 배송은 현재 적재량이 막는다"),
    ("l", 133, 138), ("b",),
    ("l", 140, 143),
])

B7 = block("pipeline/step4_metrics/imbalance.py", [
    ("l", 276, 276), ("b",),
    ("l", 283, 288), ("b",),
    ("c", "        # 아래로 잘린 만큼이 못 빌린 수, 위로 잘린 만큼이 못 세운 수다"),
    ("l", 291, 293), ("b",),
    ("l", 295, 297),
])

DOC = """# 부록 B 핵심 코드

제3장의 식에 대응하는 구현을 발췌한다. 각 절의 제목은 식 번호이고, 그 아래 첫 줄이 파일 이름이다.
함수 전체가 아니라 식에 해당하는 부분만 옮겼으며, 저장소의 변경 이력을 가리키는 주석은 덜어 내고
식을 읽는 데 필요한 것만 남겼다. 코드 줄 자체는 원본 그대로이고 들여쓰기만 덜어 냈다. 변수 이름도
코드 그대로이므로, 식의 기호와 다른 것은 절마다 밝힌다.

언어는 Python 3.14이며 정수선형계획에 PuLP와 CBC, 군집화에 kmedoids(FasterPAM), 자료 처리에
pandas와 NumPy를 쓴다. 조정할 수 있는 상수는 한곳에 모아 두고 환경변수로 바꿀 수 있게 하였다.

<표 B-1> 식과 구현의 대응

| 식 | 내용 | 파일 | 함수 |
| --- | --- | --- | --- |
| (3.4), (3.5) | 목표 재고와 재배치량 | `calculate_target_qty.py` | `compute_rebal_qty` |
| (3.6) | 작업 대상 선정 | `top_st_clustering.py` | `select_top_unbalanced_st` |
| (3.7) | 군집 수 | `top_st_clustering.py` | `wanted_vehicles` |
| (3.8) | 수급 균형 조정의 목적함수 | `adjust_module.py` | `_objective_parts` |
| (3.9) | 물량 배분 정수선형계획 | `ilp.py` | `solve_cluster_moves` |
| (3.10) | 그리디 방문 순서 | `vrp.py` | `greedy_route` |
| (3.13) | 결품 시간 복원 | `imbalance.py` | `_simulate_stock` |

## B.1 목표 재고와 재배치량 — 식 (3.4), (3.5)

`pipeline/step0_collect/calculate_target_qty.py`

```python
{B1}
```

`stats['stock']`이 식의 $q_i$, `stats['parking_lot']`이 $c_i$, `up_limit`이 상한 배수 1.5,
`MAX_CAPACITY`가 적재 용량 $Q$(10대)이다. 정수화는 0 방향으로 자르므로 식의 $\\operatorname{{int}}$에
해당한다. 분위수 모델로 $s\\mu_i + z \\cdot s\\sigma_i$를 대신하는 갈래가 코드에 남아 있으나 기본으로
꺼져 있어 발췌에서 뺐다(3.3.4절).

## B.2 작업 대상 선정 — 식 (3.6)

`pipeline/step1_cluster/top_st_clustering.py`

```python
{B2}
```

`REBAL_MIN_QTY`가 식의 임계값 $\\theta$(현행 2), `TOP_STATION_LIMIT`이 후보 상한 $N$(현행 50)이다.
수거는 재배치량이 음수이므로 오름차순 정렬이 곧 작업량 내림차순이다.

## B.3 군집 수 — 식 (3.7)

`pipeline/step1_cluster/top_st_clustering.py`

```python
{B3}
```

`work_min + travel_min`이 식의 $\\hat{{T}}$, `CLUSTER_IMBALANCE_ALLOWANCE`가 불균형 여유
$\\rho$(1.4), `TIME_BUDGET_MINUTES`가 시간 예산 $B$(120분), `TRAVEL_MIN_PER_STATION`이 대여소당
이동시간 상수 $c_{{\\text{{tr}}}}$(12.5분)이다. 상한 $M$은 이 함수를 부른 쪽이 $\\min$으로 건다.

## B.4 수급 균형 조정의 목적함수 — 식 (3.8)

`pipeline/step1_cluster/adjust_module.py`

```python
{B4}
```

`labels`가 군집 배정, `coords`가 위경도, `qty`가 재배치량이고 `alpha`·`beta`·`gamma`가 식의
$\\alpha=1$, $\\beta=100$, $\\gamma=3000$이다. 군집 조정은 대여소를 하나씩 옮기며 이 값이 줄어드는
이동만 받아들인다.

대여소 하나가 옮겨 가면 바뀌는 군집은 떠난 곳과 받는 곳 둘뿐이므로, 실제 구현은 군집별 항을 캐시에
담아 재사용한다(발췌에서는 뺐다). 합산 순서를 언제나 `np.unique(labels)`로 고정하여 캐시를 쓴 결과와
쓰지 않은 결과가 부동소수점 마지막 자리까지 같게 하였다.

## B.5 물량 배분 정수선형계획 — 식 (3.9)

`pipeline/step2_optimize/ilp.py`

```python
{B5}
```

`pick_map`이 식의 $\\text{{sup}}_i$, `drop_map`이 $\\text{{dem}}_j$, `move_total`이 $\\Lambda_k$이다.
목적함수는 자전거 한 대가 $i$에서 $j$로 옮겨지는 데 드는 시간을 대수만큼 더한 대리 목적함수이며,
실제 운행 시간은 방문 순서가 정한다(3.6.1절). `LpVariable.dicts`는 PuLP 4.0에서 사라지므로 실제
코드는 새 API가 있으면 그쪽을 먼저 쓰는 갈래를 두었고, 발췌에는 한쪽만 남겼다.

## B.6 그리디 방문 순서 — 식 (3.10)

`pipeline/step2_optimize/vrp.py`

```python
{B6}
```

`info['qty']`가 식의 잔여량, `possible`이 $a_u$, `current_load`가 적재량 $\\ell$,
`VEHICLE_CAPACITY`가 $Q$이다. `1e-6`은 0으로 나누는 것을 막는 값이고, 노드를 완결 지을 때 곱하는
0.1이 식의 갈래에 해당한다. 차량은 차고지에서 출발하여 남은 작업이 없으면 차고지로 돌아온다.

## B.7 결품 시간 복원 — 식 (3.13)

`pipeline/step4_metrics/imbalance.py`

```python
{B7}
```

`initial`이 식의 $q_i^{{(0)}}$, `flow`가 $n_{{i,d,h_k}}$, `capacity`가 $c_i$이다. 순수요는 대여에서
반납을 뺀 값이므로 양수이면 재고가 준다. `out['stockout']`의 합이 식의 $\\text{{Stockout}}_i$이고,
같은 궤적에서 포화 시간과 충족률의 분자·분모를 함께 센다(3.9절). 재배치의 효과는 계획량이 아니라
집행량을 초기 재고에 더해 같은 궤적을 다시 돌려 잰다.
"""


def main() -> int:
    text = DOC.format(B1=B1, B2=B2, B3=B3, B4=B4, B5=B5, B6=B6, B7=B7)
    (ROOT / OUT).write_text(text, encoding="utf-8")
    print(f"{OUT}을 다시 썼습니다 (발췌 7개)")
    print("이어서 `python tools/check_consistency.py --only 부록`으로 원본과 대조하십시오")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
