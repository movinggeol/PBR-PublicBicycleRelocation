# TODO — 해야 할 것 / 고쳐야 할 것

> 2026-08-07 기준. 전체 코드(step0~step4, test, run_pipeline.py, project_config.py)와
> 문서(README.md, docs/)를 전수 검토한 결과입니다.
> 우선순위: 🔴 파이프라인이 깨지는 문제 / 🟡 결과가 틀리거나 문서와 다른 문제 / 🟢 품질·유지보수 개선
>
> **2026-08-07 업데이트(버전 1.0.3)**: 🔴 P1 전체와 🟡 P2 대부분이 수정 완료되었습니다.
> 완료 항목은 아래 "✅ 완료" 섹션으로 이동했습니다.

---

## 🟡 P2. 남은 동작 문제

### 2-1. `vrp.py` — 남은 모델링 결정

- `VEHICLE_TOTAL = 21`은 선언만 되고 미사용 — 다차량 배정을 구현하거나 상수·문서에서 제거.
- ILP는 25km/h·Haversine, VRP는 30km/h — 기준 통일 여부는 운영 데이터로 결정 필요.
- VRP 작업시간은 "자전거 1대당 30초" 가정 — 현장 값으로 검증 필요.

---

## 🟢 P3. 품질·유지보수 개선

1. **폴더명 정리**: `step0 (raw데이터 처리)`와 `step0(전처리 및 EDA)`가 헷갈림(공백 유무만 다름).
   가능하면 `step0_collect`, `step0_eda`처럼 공백·괄호 없는 이름으로 변경
   (변경 시 run_pipeline.py·README 동시 수정).
2. **스키마 검증**: 단계 간 CSV의 필수 컬럼(station_id, lat, lon, rebal_qty, cluster …)을
   읽는 쪽에서 assert하는 얇은 검증 함수 추가.
3. **성능**: `adjust_module.try_move_node()`가 노드 이동 시도마다 `pick_drop.copy()` +
   전체 목적함수 재계산(내부에서 `compute_medoids` 반복) — 버전관리.txt에 기록된
   "5분 이상 소요" 문제의 원인. 이동 노드가 속한 두 군집만 재계산하는 증분 방식으로 개선.
4. **VRP 고도화**: 현행은 greedy 휴리스틱(단일 차량/클러스터). OR-Tools 등
   전용 VRP solver로 다차량·시간창·실도로 거리 반영.
5. **데이터 관리**: CSV → SQLite 이관 — **채택 결정됨**, 스키마·작업 단계는
   [DB_PLAN.md](DB_PLAN.md) 참고 (선행 조건: 파이프라인 검증 완료).
6. **EDA 시각화**: `month_graph`에 matplotlib 그래프 통합
   (`experiments/matplotlib_month_graph.py`의 한글 폰트 설정 참고).
7. **step1 매직 넘버**: 상위 50개 컷, `|rebal_qty| > 2`, target_cluster_size=7 등을 설정으로 추출.
8. **버전관리.txt → CHANGELOG.md 승격 검토.**
9. **README 주요 결과 수치의 산출 근거**(입력 데이터·실행 시점) 기록.
10. **webapp**: 실행 취소 버튼, SSE 로그 스트리밍 (docs/WEBAPP.md 참고).

---

## ✅ 완료 (2026-08-07, 버전 1.2.0)

### P2·P3 — 이번 라운드에 수정된 항목

- ~~step3 depot 복귀 KeyError~~ → `action == 'return'` 행을 depot 정보로 처리.
  depot id·좌표·차량 용량은 `project_config`의 공통 상수(DEPOT_*, VEHICLE_CAPACITY)로 통일
  (기존: main.py `ST0000` vs vrp.py `ST0001` 불일치).
- ~~vrp.py pick/drop 노드 덮어쓰기~~ → 노드 키를 `(station_id, type)`으로 변경.
- ~~VRP 시간 계산 미구현~~ → 이동거리(km)·이동시간·작업시간(자전거 1대당 30초 가정)·누적시간
  컬럼을 VRP 결과에 추가. drop-only 잔여 시 depot 무한복귀 가능성도 방어 코드 추가.
- ~~TMAP 경유지 30개 제한 방어 없음~~ → `module.call_tmap_chunked()`가 구간 분할 호출,
  `merge_tmap_results()`가 경로·누적시간 병합.
- ~~step4 총 이동거리·운행시간 미구현~~ → `route_summary()`가 클러스터별
  방문수·처리대수·총이동거리·총이동/작업/소요시간을 집계해 `route_summary*.csv` 저장.
- ~~module.py 죽은 코드~~ → 주석 처리된 구버전 정규화 유틸 삭제.
- ~~테스트 정리~~ → `test/`·각 step의 `test.py`를 `experiments/`로 이동 (experiments/README.md 참고).
- ~~`.env.example` 추가~~ → API 키·PBR_* 환경변수 템플릿.
- ~~`adjust_module` docstring 오류~~ → 유클리드 → 맨해튼 거리로 정정.

## ✅ 완료 (2026-08-07, 버전 1.0.3)

### P1 — 파이프라인이 깨지는 문제 (전체 해소)

- ~~**`now` 값 3원화**~~ → 전 스크립트가 `project_config.get_runtime_config()` 사용.
  CLI 인자 → 환경변수(`PBR_NOW` 등) → 기본값 순으로 결정되며, 모든 단계가 같은 라벨 공유.
  경로도 `PROJECT_ROOT` 기준 절대경로가 되어 어느 cwd에서 실행해도 동작.
- ~~**import 부작용 체인**~~ → step0의 상호 import 제거, 실행 로직을
  `if __name__ == '__main__':` + `main()`으로 격리. 실행 순서는 run_pipeline.py가 제어.
- ~~**EDA.py 즉시 크래시**~~ → `pd.DateFrame` 오타 2곳, `month_graph(file_path)` 인자 오류 수정.
  원천 파일이 없으면 정상 종료(파이프라인 중단 방지).
- ~~**run_pipeline 인자 무시**~~ → 하위 스크립트가 project_config로 인자를 읽으므로
  `--now/--period/--duration/--raw-file` 전달이 실제로 동작. docstring 파일명도 수정.
  `ensure_output_dirs()`를 실행 시작 시 호출.
- ~~**`!calculate_target_qty.py` 파일명**~~ → `calculate_target_qty.py`로 개명.

### P2 — 함께 수정된 항목

- ~~`tashu_api.py` API 실패 시 NameError·거짓 성공 메시지~~ → 실패 시 `SystemExit`, timeout 추가.
- ~~`calculate_target_qty.py`의 `.loc[:, ...].clip(inplace=True)`~~ → 재할당 방식으로 수정
  (pandas 2.x에서 target_qty 상·하한이 무시될 위험 제거).
- ~~`raw_to_net.py` 파일명 슬라이싱 period 추출~~ → `config.period` 사용.
- ~~`vrp.py` depot 복귀 기록의 `'from_id': sid`~~ → `current_id`로 수정, 복귀 후 `current_id` 갱신 추가.
- ~~`vrp.py` 죽은 코드~~ → `manhattan_distance_km`, `drop_qty` get 잔재 제거,
  station_info 로드를 클러스터 루프 밖으로 이동.
- ~~`ilp.py` solver 전역 의존~~ → `run_ilp_plan(metrics, duration, solver)` 인자로 변경.
  `assume_initial_stock` 미사용 상수 제거.
- ~~`step3/main.py` 전역 의존~~ → `make_vrp_map(..., duration, headers, tmap_url)` 인자화,
  duration_list 루프 지원.
- ~~`imbalance.py` 산출물 미저장~~ → verification CSV 저장·지도 생성 활성화,
  `title=` → `tiles=` 오타 수정, `fillna` no-op 제거.
- ~~`st_visualization.py` 유효하지 않은 `title=` kwarg~~ → 제거.
- ~~`concat_1year_file.py` 주석 토글 실행~~ → `--concat`/`--preprocess` CLI 옵션화,
  파일 없으면 건너뜀.

### 검증 상태

- ⚠️ 이 PC에 Python이 설치되어 있지 않아(Microsoft Store 스텁만 존재) 구문·실행 검증을
  수행하지 못함. Python 설치 후 다음으로 확인할 것:
  ```powershell
  python run_pipeline.py --dry-run     # 파일 존재·실행 순서 확인
  python -m py_compile run_pipeline.py project_config.py  # + 각 step 파일
  ```
