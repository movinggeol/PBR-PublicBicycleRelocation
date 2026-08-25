# Step 0 — 전처리 및 EDA (`step0_eda/`)

여러 달의 원천 대여 이력 CSV를 하나로 병합하고, 기초 탐색 분석을 하는 보조 단계입니다.
월 단위 분석만 할 때는 건너뛸 수 있습니다.

## 파일별 상세

### `concat_1year_file.py`
- **입력**: `data/raw_data/타슈 대여이력 정보(25.04~26.03)/…({period}).csv` (월별 12개 파일)
- **처리**:
  - `concat_file(period)`: utf-8-sig → cp949 순으로 인코딩 시도하며 12개월 병합
  - `preprocessing(file_path)`: `이용시간(분)`, `이용거리(km)`의 IQR×1.5 밖 이상치 제거
- **출력**: `data/raw_data/타슈 대여이력(25.04~26.03).csv`
- **실행**: `--concat`(병합), `--preprocess`(이상치 제거) 옵션으로 선택.
  옵션 없이 실행하면(파이프라인 기본) 병합 파일이 있을 때만 이상치 제거를 수행하고 없으면 건너뜀

### `EDA.py`
- **처리**: 월별 대여량 집계(`month_graph`), 현재 월 필터링(`now_month`)
- **입력**: project_config의 `raw_file`. 파일이 없으면 건너뛰고 정상 종료(파이프라인 중단 방지)

## 현재 문제점

| 우선순위 | 문제 |
| --- | --- |
| 🟢 | 시각화 코드가 `experiments/matplotlib_month_graph.py`(matplotlib 월별 그래프, 더미 데이터)에 분리되어 있음 — EDA.py로 통합 |

## 작업 목록

- [x] ~~`pd.DateFrame` → `pd.DataFrame` 오타 수정~~ (1.0.3)
- [x] ~~`month_graph(df)` 호출 인자 수정~~ (1.0.3)
- [x] ~~`concat_file`/`preprocessing` 실행을 CLI 인자(`--concat`/`--preprocess`)로 선택~~ (1.0.3)
- [ ] `month_graph`에 matplotlib 시각화 통합 (한글 폰트 설정 포함,
  [experiments/matplotlib_month_graph.py](../../../experiments/matplotlib_month_graph.py) 참고)
- [ ] 이상치 제거 기준(IQR×1.5)을 README 또는 본 문서의 데이터 품질 섹션에 명시
