---
name: pbr-pipeline
description: PBR(공공자전거 재배치) 프로젝트에서 코드를 읽거나 수정하기 전에 반드시 읽어야 하는 프로젝트 규약·함정·작업 가이드. 이 저장소의 step0~step4 파이프라인, 데이터 경로, now/period/duration 규칙을 다루는 모든 작업에 사용.
---

# PBR (Public Bicycle Relocation) 작업 가이드

대전 타슈 공공자전거 재배치 프로젝트. step0~step4가 CSV 파일로 데이터를 주고받는
배치 파이프라인이다. **코드를 수정하기 전에 이 규약과 함정을 반드시 확인할 것.**

## 문서 위치 (수정 전 필독)

| 문서 | 내용 |
| --- | --- |
| `docs/TODO.md` | 알려진 버그·개선 과제 전체 목록 (우선순위 🔴🟡🟢) |
| `docs/PROJECT_PIPELINE.md` | 파이프라인 전체 구조 |
| `docs/steps/step*.md` | 단계별 입출력·문제점·작업 목록 |
| `docs/WEBAPP.md` | 웹 대시보드(webapp/) 실행·구조·API |
| `docs/DB_PLAN.md` | SQLite 도입 결정·목표 스키마·이관 단계 (CSV→DB 작업 시 필독) |
| `docs/버전관리.txt` | 버전 이력, 수정 이유 기록 |

## 파이프라인 구조 (실행 순서 = 데이터 의존 순서)

```text
step0 (raw데이터 처리)  : tashu_api → extract_parking_lot → api_to_info → raw_to_net → calculate_target_qty
step0(전처리 및 EDA)    : concat_1year_file, EDA (선택적)
step1                   : 1.top_st_clustering → st_visualization
step2                   : ilp → vrp
step3                   : main (TMAP 지도)
step4                   : imbalance
```

각 단계는 `data/pp_data/…/<이름>{duration} ({now}).csv` 형식의 파일로 통신한다.
**앞 단계의 출력 파일명과 뒤 단계의 입력 파일명이 정확히 일치해야 한다.**

## 공통 설정 규칙 (버전 1.0.3부터)

- 모든 step 스크립트는 루트의 `project_config.get_runtime_config()`에서
  `now`/`period`/`duration`/`raw_file`을 읽는다. 우선순위: CLI 인자(`--now` 등) →
  환경변수(`PBR_NOW` 등) → 기본값.
- **`now`는 실행 시각이 아니라 파이프라인 실행을 묶는 라벨이다.** `datetime.now()`나
  하드코딩 값을 코드에 넣지 마라 — 단계 간 파일명이 어긋난다.
- 각 스크립트는 독립 실행되며(상호 import 없음) 실행 로직은
  `if __name__ == '__main__':` + `main()` 아래에 둔다. 순서는 run_pipeline.py가 제어한다.
- step 폴더의 스크립트는 상단에서 `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))`
  후 project_config를 import한다 — 새 스크립트를 만들 때 같은 패턴을 따르라.
- 데이터 경로는 `PROJECT_ROOT` 기준으로 만든다: `str(PROJECT_ROOT / "data/pp_data/...")`.
- 산출물을 쓰는 스크립트는 저장 전에 `ensure_output_dirs()`를 호출한다.

## ⚠️ 함정 (반드시 확인)

1. **폴더명에 공백·괄호·한글이 있다.** 셸 명령에서는 반드시 따옴표로 감싸라:
   `python "step0 (raw데이터 처리)/tashu_api.py"`.
   `step0 (raw데이터 처리)`와 `step0(전처리 및 EDA)`는 공백 유무만 다른 **별개 폴더**다.
2. **`data/`와 `*.csv`는 .gitignore로 전부 제외된다.** 데이터 파일은 커밋할 수 없고,
   로컬에 원천 CSV가 있어야만 파이프라인이 돈다. 데이터가 없으면 코드 실행 검증은
   구문 수준(`python -m py_compile`)까지만 가능하다.
3. **API 키는 `.env`** (`TASHU_API_KEY`=타슈, `API_KEY`=TMAP). 절대 커밋 금지.
   템플릿은 `.env.example`.
4. **depot·차량 적재 용량은 project_config의 공통 상수**(DEPOT_ID/LAT/LON/NAME,
   VEHICLE_CAPACITY)다. step2·step3에서 별도 하드코딩하지 마라.
5. **일회성 스크립트는 `experiments/`에 둔다** — step 폴더나 루트에 test.py를 만들지 마라.
6. **가상환경은 `.venv`** (검증 환경: Python 3.14.7). 명령은 `.\.venv\Scripts\python.exe ...`로
   실행하라 — 시스템 `python`에는 의존성이 없다.
7. **K-Medoids는 `kmedoids` 패키지**(FasterPAM)다. `sklearn_extra`는 아카이브되어
   Python 3.12+에서 설치되지 않으므로 되돌리지 마라.
8. **Starlette 1.x 템플릿 응답은 `TemplateResponse(request, name, {...})`** 형식만 동작한다.
   구 형식(`TemplateResponse(name, {"request": ...})`)으로 쓰면 500 오류가 난다.
9. **requirements.txt는 하한(`>=`) 고정**을 유지하라. 상한을 걸면 새 Python 버전에서
   휠이 없어 설치가 통째로 깨진다(1.2.1에서 실제로 겪음).

## 실행 방법

```powershell
python run_pipeline.py --dry-run          # 실행 목록 확인 (파일 존재 검증)
python run_pipeline.py                    # 전체 실행 (기본 설정)
python run_pipeline.py --skip-api --skip-eda  # 수집·EDA 생략
python run_pipeline.py --now "2026-05-21 18" --period "25년 11월" --duration "_05_10,_10_15"
python -m webapp                          # 웹 대시보드 (http://127.0.0.1:8000)
```

## 웹 대시보드 (webapp/)

- FastAPI + Jinja2 파이썬 단독. `webapp/jobs.py`가 run_pipeline.py를 subprocess로
  실행하고(동시 1개 제한), `webapp/catalog.py`가 data/pp_data 산출물을 스캔한다.
- 파일 서빙은 `data/` 아래 `.html`/`.csv`로 제한된다 — 새 라우트를 추가할 때
  `catalog.safe_resolve()`를 우회하지 마라.
- 파이프라인 로직을 웹 요청 안에서 직접 실행하지 마라(수 분 소요) —
  반드시 jobs.start_job() 경유.

## 코드 규약

- 주석·출력 메시지·문서는 한국어를 사용한다.
- 컬럼명 규약: `station_id`, `station_name`, `lat`, `lon`, `stock`, `parking_lot`,
  `rebal_qty`(양수=Drop 필요, 음수=Pick 가능), `target_qty`, `cluster`.
- 좌표: TASHU API는 `x_pos`=위도, `y_pos`=경도 (뒤집혀 있음 — 변환 코드 존재).
- 운영 상수(변경 시 근거 기록): 차량 적재 용량 10대(대전교통공사 확인),
  ILP 속도 25km/h, VRP 속도 30km/h, 신뢰계수 z=1.65, depot=타슈 관제센터(ST0001).
- pandas 2.x 기준으로 작성 (`.loc` 슬라이스에 inplace 연산 금지).
- 버전에 영향 주는 수정을 하면 `docs/버전관리.txt`에 이유와 함께 기록한다.

## 수정 후 확인 절차

1. `python -m py_compile <수정한 파일>` 로 구문 확인
2. 수정한 파일이 읽는/쓰는 파일명 패턴(`{now}`, `{duration}`, `{period}`)이
   앞뒤 단계와 일치하는지 확인
3. `docs/TODO.md`의 해당 항목을 완료 처리하고, 새로 발견한 문제는 추가
4. 동작이 바뀌었으면 `docs/steps/` 해당 단계 문서도 갱신
