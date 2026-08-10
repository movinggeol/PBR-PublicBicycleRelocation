# Step 1 — 작업대상 선정 및 클러스터링 (`step1 (작업대상 선정 및 클러스터링)/`)

재배치량이 큰 대여소를 Pick/Drop 대상으로 선정하고, 차량 1대가 처리할 수 있는
규모의 공간 군집으로 묶는 단계입니다.

## 파일별 상세

### `1.top_st_clustering.py` (메인)

3단계로 진행됩니다.

1. **선정** `select_top_unbalanced_st()`
   - `|rebal_qty| > 2`인 대여소만 후보로
   - Pick(음수)·Drop(양수) 각각 작업량 내림차순 상위 50개 컷
   - 누적합(cumsum)이 `min(|총 Pick|, 총 Drop)` 이내가 되도록 잘라 Pick·Drop 총량 균형 맞춤
2. **클러스터링** `make_clustering()`
   - K-Medoids (`kmedoids` 패키지, `method='fasterpam'`, `metric='manhattan'`, `random_state=42`)
   - `K = ceil(대상 대여소 수 / target_cluster_size(7))`
   - 좌표는 스케일링하지 않는다 (위경도 자체가 거리 단위)
3. **군집 조정** `adjust_clustering()`
   - 목적함수: `α·balance² + β·size분산 + γ·거리합` (α=1, β=100, γ=10)
   - 군집별 |balance| ≤ 3(THRESHOLD)이면 종료, 최대 200회 노드 이동
   - balance가 ±5(BALANCE_LIMIT) 초과 시 emergency mode로 balance 우선 조정
     (버전관리.txt 1.0.2에서 도입된 로직)

- **입력**: `rebal_qty{duration} ({now}).csv`, `st_info ({now}).csv`
- **출력**: `data/pp_data/ILP/후보/top{duration} ({now}).csv`
  (station_id, station_name, lat, lon, mu, sigma, parking_lot, stock, target_qty, rebal_qty, cluster)

### `adjust_module.py` (군집 조정 보조)

| 함수 | 역할 |
| --- | --- |
| `compute_medoids` | 군집별 메도이드(맨해튼 거리합 최소점) 계산 |
| `compute_objective` | 목적함수 점수 계산 |
| `select_cluster_candidates` | 노드를 보낼(from)/받을(to) 군집 후보 선정 (size 기준 + balance emergency) |
| `make_cluster_pairs` | 군집 쌍을 중심 간 거리 오름차순으로 생성 |
| `get_movable_nodes` | balance 부호에 맞는(Pick 과잉이면 Pick만) 이동 가능 노드 추출 |
| `check_size_constraint` | 이동 후 size 제약 확인 (emergency 시 무시) |
| `try_move_node` | 점수가 개선되는 첫 이동을 적용 |

### `st_visualization.py`
- **입력**: `top{duration} ({now}).csv`
- **출력**: `data/pp_data/ILP/visualization/clusterd_map{duration} ({now}).html`
  (군집별·Pick/Drop별 레이어 토글 가능한 Folium 지도)

## 현재 문제점

| 우선순위 | 문제 |
| --- | --- |
| 🟡 | `try_move_node()`가 이동 후보마다 `pick_drop.copy()` + 전체 목적함수 재계산 → 실행 5분 이상 (버전관리.txt 1.0.1에 기록된 성능 문제) |
| 🟢 | 상위 50개 컷·target_cluster_size=7 등 매직 넘버가 코드에 산재 — 설정으로 추출 필요 |

## 작업 목록

- [x] ~~`now`를 project_config로 통일~~ — 시각화가 파일을 못 찾던 문제 해소 (1.0.3)
- [x] ~~이미 포맷된 경로에 `.format()` 재호출 정리~~ (1.0.3)
- [x] ~~duration 다중 실행 시 st_visualization 연동~~ — 두 파일 모두 `duration_list(config)` 사용 (1.0.3)
- [x] ~~docstring 거리 표기 정정(유클리드→맨해튼), test.py를 experiments/로 이동~~ (1.2.0)
- [x] ~~`sklearn_extra.cluster.KMedoids` → `kmedoids.KMedoids` 교체~~ (1.2.1)
      — 합성 데이터 60개로 검증: K=9 생성, 재실행 결정성 확인,
      `adjust_clustering` 수렴(|balance| 최대 33 → 4)
- [ ] `compute_objective` 증분 계산(이동 노드가 속한 두 군집만 재계산)으로 성능 개선
- [ ] 선정 기준(상위 N, |rebal_qty| 임계값)·군집 파라미터를 설정/CLI로 노출
