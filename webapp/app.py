"""PBR 파이프라인 웹 대시보드.

실행:
    uvicorn webapp.app:app --reload
    # 또는
    python -m webapp

기능:
- 웹 폼으로 파이프라인 실행(run_pipeline.py 백그라운드 호출) + 실시간 로그
- 산출물 지도(HTML)를 브라우저에서 바로 열람
- CSV 산출물 미리보기·다운로드, GeoJSON API

로컬 운영 도구이므로 인증이 없다. 외부 네트워크에 노출하지 말 것.
"""
from __future__ import annotations

import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from project_config import (
    DAY_TYPE_AUTO, DAY_TYPE_LABELS, DAY_TYPES, DEFAULT_DAY_TYPE, DEFAULT_DURATION,
    DEFAULT_RAW_FILE, DEPOT_NAME, DURATION_LABELS, DURATIONS,
    FLEET_SIZE, MAX_FLEET_SIZE, REBAL_MIN_QTY, TARGET_QTY_UPPER_RATIO, TARGET_Z,
    TIME_BUDGET_MINUTES, VEHICLE_CAPACITY, VEHICLE_SPEED_KMPH, VEHICLES_PER_ROUND,
    available_periods, latest_period, normalize_day_type, normalize_durations,
    normalize_fleet_size, normalize_per_round, normalize_period, resolve_day_type,
)
import tashu
from webapp import catalog, charts, jobs, kpi_view, orders, store, weather_view

app = FastAPI(title="PBR 파이프라인 대시보드", docs_url="/api/docs")

_HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(_HERE / "templates"))
# 글꼴을 같이 담아 서빙한다 — 인터넷이 끊겨도 화면이 같아야 한다.
app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")


# ---------------- 오류 화면 ----------------

# 사람이 보는 화면에 날 JSON을 띄우지 않는다. 예전에는 산출물이 정리된 뒤 옛
# 링크를 누르면 `{"detail":"파일을 찾을 수 없습니다."}` 가 떴다(1.26.107) —
# 내비도 돌아갈 링크도 없었고, `/없는페이지`는 영어로 "Not Found"였다.
#
# ⚠️ **API는 그대로 JSON이다.** 기계가 읽는 자리라 화면을 돌려주면 오히려
#    깨진다. 가르는 기준은 경로(`/api/…`)와 `Accept` 헤더 둘 다 본다 —
#    경로만 보면 `fetch('/files/…')` 같은 호출이 HTML을 받는다.
ERROR_TITLES = {
    404: "찾는 것이 없습니다",
    400: "요청을 이해하지 못했습니다",
    403: "열 수 없는 파일입니다",
    500: "문제가 생겼습니다",
}


def _wants_html(request: Request) -> bool:
    if request.url.path.startswith("/api/"):
        return False
    accept = request.headers.get("accept", "")
    # 브라우저 주소창은 text/html을 먼저 요구한다. fetch·curl은 그렇지 않다.
    return "text/html" in accept


@app.exception_handler(StarletteHTTPException)
def http_error(request: Request, exc: StarletteHTTPException):
    code = exc.status_code
    if not _wants_html(request):
        return JSONResponse({"detail": exc.detail}, status_code=code,
                            headers=getattr(exc, "headers", None))
    detail = exc.detail
    if not isinstance(detail, str) or detail == "Not Found":
        detail = "주소가 바뀌었거나, 그 사이에 산출물이 정리되었을 수 있습니다."
    return templates.TemplateResponse(
        request, "error.html",
        {"code": code, "title": ERROR_TITLES.get(code, f"오류 {code}"),
         "detail": detail},
        status_code=code)


# 작업 상태를 사람이 읽는 말로 — 템플릿 전역이라 어느 화면에서나 같은 낱말을 쓴다.
JOB_STATUS_LABELS = {
    "running": "실행 중",
    "success": "완료",
    "failed": "실패",
    "cancelled": "중단됨",
    "interrupted": "추적 끊김",
}
templates.env.globals["job_labels"] = JOB_STATUS_LABELS

# 실행 종류도 같은 방식으로 둔다. 예전에는 이 세 낱말을 index.html 안에
# **두 번**(고르는 목록과 설명 풍선) 적어 뒀는데, `db.RUN_KINDS`와 갈리면
# 화면과 저장값이 어긋난다(1.26.110). 순서가 곧 목록의 순서다.
RUN_KIND_LABELS = {
    "plan": "운영 계획",
    "experiment": "실험",
    "probe": "수집",
}
templates.env.globals["kind_labels"] = RUN_KIND_LABELS

# 배정 이력 한 쪽에 실을 건수. 실행 1건이 평균 17.2행이므로(실측, 1.26.116)
# 50이면 대략 3회 실행분이 한 쪽에 들어온다.
#
# ⚠️ **상한을 올리는 것으로는 풀리지 않는다.** 예전에는 200행에서 잘라 내고
# "N건이 더 있습니다"라고만 적었는데, 실행이 12건만 쌓여도 그 안내가 **늘 참**이
# 되고 나머지에는 닿을 길이 없었다. 자른다면 나머지로 가는 길이 함께 있어야 한다.
ASSIGNMENTS_PER_PAGE = 50

# ---------------- 진행 단계 ----------------

# 단계 파일 이름을 사람이 읽는 말로. run_pipeline.STAGES와 짝을 이룬다.
STAGE_LABELS = {
    "tashu_api.py": "실시간 재고 수집",
    "extract_parking_lot.py": "대여소 거치대 수 추출",
    "api_to_info.py": "대여소 정보 정리",
    "concat_1year_file.py": "1년치 이력 합치기",
    "EDA.py": "탐색적 분석",
    "raw_to_net.py": "순수요 계산",
    "calculate_target_qty.py": "목표 재고 산정",
    "top_st_clustering.py": "작업 대상 선정·군집화",
    "st_visualization.py": "대여소 지도 생성",
    "ilp.py": "이동량 최적화 (ILP)",
    "vrp.py": "차량 경로 최적화 (VRP)",
    "main.py": "결과 지도 생성",
    "imbalance.py": "성과 지표 계산",
}

_PLAN_RE = re.compile(r"^\[(\d+)/(\d+)\] (?!실행: )(.+)$")
_START_RE = re.compile(r"^\[(\d+)/(\d+)\] 실행: ")
# 완료 줄에는 소요 시간이 붙는다("완료: step1_cluster/top_st_clustering.py (42.1초)").
# 괄호 부분을 떼고 파일명만 집는다 — 안 그러면 단계 표시가 통째로 안 켜진다.
_DONE_RE = re.compile(r"^완료: (.+?)(?: \([^()]*\))?$")
_FAIL_RE = re.compile(r"^실패: (.+?) \(exit code=")
_MISSING_RE = re.compile(r"^파일이 없습니다: (.+)$")


def pipeline_progress(text: str) -> list[dict]:
    """로그에서 단계별 진행 상태를 뽑는다.

    run_pipeline이 맨 앞에 전체 단계 목록을 찍고, 각 단계마다
    '실행:' → '완료:'/'실패:'를 찍는다는 점을 이용한다. 로그 형식이
    바뀌면 목록이 비고, 화면은 진행 표시 없이 로그만 보여준다.
    """
    plan: list[dict] = []
    seen: set[str] = set()
    for line in text.splitlines():
        m = _PLAN_RE.match(line)
        if not m:
            continue
        script = m.group(3).strip()
        if script in seen:            # 계획 블록은 한 번만 읽는다
            continue
        seen.add(script)
        name = script.replace("\\", "/").rsplit("/", 1)[-1]
        plan.append({"script": script, "name": name,
                     "label": STAGE_LABELS.get(name, name), "status": "pending"})
    if not plan:
        return []

    by_script = {s["script"]: s for s in plan}
    for line in text.splitlines():
        if (m := _START_RE.match(line)):
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(plan) and plan[idx]["status"] == "pending":
                plan[idx]["status"] = "running"
        elif (m := _DONE_RE.match(line)):
            if (s := by_script.get(m.group(1).strip())):
                s["status"] = "done"
        elif (m := _FAIL_RE.match(line)):
            if (s := by_script.get(m.group(1).strip())):
                s["status"] = "failed"
        elif (m := _MISSING_RE.match(line)):
            if (s := by_script.get(m.group(1).strip())):
                s["status"] = "missing"
    return plan



# ---------------- 페이지 ----------------

def _minutes(seconds: Optional[float]) -> Optional[float]:
    """초 → 분(소수 한 자리). 값이 없으면 그대로 None을 돌려준다."""
    return None if seconds is None else round(seconds / 60, 1)


def _typical_vehicles() -> Optional[int]:
    """회차당 실제로 나간 차량 수의 중앙값. 기록이 없으면 None.

    1.19.1부터 대수는 회차의 작업량이 정하므로 **고정값을 안내에 적을 수 없다.**
    "보통 몇 대가 나가나"는 지난 실행에서 뽑아 보여준다.
    """
    rows = store.kpi()
    if rows.empty or "vehicles_used" not in rows:
        return None
    # 최근 것부터 20건만 본다. run_label은 사람이 붙이는 이름이라 정렬 기준이 못 되고,
    # 기록 시각(computed_at)이 있어야 "최근"이 성립한다.
    if "computed_at" in rows:
        rows = rows.sort_values("computed_at", ascending=False)
    used = rows["vehicles_used"].dropna().head(20)
    return int(round(float(used.median()))) if len(used) else None


def _suggested_now() -> str:
    """실행 폼에 채워 넣을 **이름 제안**. 오늘 날짜 + 지금 시각대다.

    `project_config.DEFAULT_NOW`는 건드리지 않는다 — `now`는 실행 시각이 아니라
    단계 간 파일명을 묶는 **라벨**이라, 코드 기본값이 시각에 따라 흔들리면
    step0가 쓴 파일을 step4가 못 찾는다. 여기서 만드는 것은 사람이 지우고
    고쳐 쓸 수 있는 폼의 초기값일 뿐이다.
    """
    return datetime.now().strftime("%Y-%m-%d %H")


def _year_ago_period(periods) -> Optional[str]:
    """가장 최근 달의 **1년 전 같은 달**이 자료에 있으면 그 라벨 (수정안 34).

    ⚠️ **기본값으로 삼지 않는다.** 측정에서 현행(가장 최근 달)을 넘지 못했다 —
    12개월 자료에 구멍이 있어 '1년 전 같은 달'이 실제로는 한 달 어긋난 달이
    되는 경우가 3개 중 2개였다(docs/분석/DEMAND_DISTRIBUTION.md 6-C장).

    그래도 **있으면 알려는 준다.** 계절을 맞추고 싶은 사람이 고를 수 있어야 한다.
    """
    latest = latest_period()
    match = re.match(r"(\d+)년 (\d+)월", str(latest))
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    want = f"{year - 1:02d}년 {month:02d}월"
    return want if want in set(periods) else None


def _index_context(error: Optional[str] = None) -> dict:
    # 기간·시간대는 **요청마다 다시 읽는다.** 서버를 띄워 둔 채 새 달치 순수요를
    # 계산해도 곧바로 선택지에 나와야 하기 때문이다(project_config의 상수는
    # import 시점에 굳는다).
    periods = available_periods()
    return {
        "defaults": {
            "now": _suggested_now(),
            "period": latest_period(),
            "duration": DEFAULT_DURATION,
            "raw_file": DEFAULT_RAW_FILE,
            # 환경변수(PBR_FLEET_SIZE 등)를 걸어 뒀으면 그 값이, 아니면 기본값이 뜬다.
            "fleet_size": FLEET_SIZE,
            "vehicles_per_round": VEHICLES_PER_ROUND,
            "day_type": DEFAULT_DAY_TYPE,
        },
        "max_fleet_size": MAX_FLEET_SIZE,
        # 순수요를 계산해 둔 달만 고르게 한다 — 없는 달을 넣으면 step0가 멈춘다.
        # 최근 달이 위로 오게 뒤집는다(대개 가장 최근 달로 계획한다).
        "periods": list(reversed(periods)),
        "year_ago_period": _year_ago_period(periods),
        # 시간대는 네 창이 전부다. 창마다 수요 방향이 반대라 섞지 않는다.
        "durations": [{"value": d, "label": DURATION_LABELS[d]} for d in DURATIONS],
        # 평일과 휴일은 수요 구조가 달라 한 실행에 섞지 않는다 (docs/구현/steps/step0_raw.md).
        # auto는 계획 대상일(기본 오늘)을 달력으로 판정한다 — 운영 기본값.
        "day_types": (
            [{"value": DAY_TYPE_AUTO,
              "label": f"자동 (오늘 = {DAY_TYPE_LABELS[resolve_day_type()]})"}]
            + [{"value": v, "label": DAY_TYPE_LABELS[v]} for v in DAY_TYPES]
        ),
        "typical_minutes": _minutes(jobs.typical_elapsed()),
        "typical_vehicles": _typical_vehicles(),
        "running": jobs.running_job(),
        "jobs": jobs.list_jobs()[:15],
        "latest": catalog.latest_outputs(),
        "pipeline_runs": store.records(store.run_labels().head(10)),
        "error": error,
    }


def _run_kind(run_label: str) -> str:
    """실행 종류(`plan`·`experiment`·`probe`).

    판정은 `store.run_kind()`가 한다 — `webapp/`에서 `db.py`를 직접 아는 곳은
    `store.py` 하나여야 한다(1.26.110). 여기 남겨 둔 것은 화면이 부르는
    이름을 바꾸지 않기 위한 얇은 껍데기다.

    `runs`에 행이 없어도 **모른다고 넘기지 않는다.** 지표만 남고 실행 행이
    없는 경우가 있는데(옛 자료·부분 기록), 그때 '모름'을 운영 계획으로 취급
    하면 화면이 조용히 거짓말한다 — 그것이 1.26.107에서 고친 바로 그 결함이다.
    """
    return store.run_kind(run_label)


def _home_context() -> dict:
    """메인 화면(현황판)이 쓸 값. **읽기만 한다** — 외부 API를 부르지 않는다.

    처음 들어온 사람이 '지금 무슨 상태인가'를 먼저 보게 하려고 만들었다
    (수정안 33). 실행 폼은 /run으로 옮겼다.
    """
    rows = store.kpi()
    last = None
    if not rows.empty:
        # 가장 최근 실행 하나를 회차 합계로 접는다. 회차마다 한 줄씩 쌓이므로
        # 마지막 run_label의 행을 모두 모아야 '그 실행'이 된다.
        order = _runs_newest_first(rows)
        if order:
            part = rows[rows["run_label"] == order[0]]
            stockout_after = _sum(part, "stockout_hours_after")
            last = {
                "run_label": order[0],
                "computed_at": str(part["computed_at"].max()) if "computed_at" in part else "",
                "durations": int(part["duration"].nunique()) if "duration" in part else 0,
                "bikes": int(_sum(part, "bikes_moved")),
                "vehicles": int(_sum(part, "vehicles_used")),
                "distance_km": round(float(_sum(part, "total_distance_km")), 1),
                "stockout_before": round(float(_sum(part, "stockout_hours_before")), 2),
                "stockout_after": round(float(stockout_after), 2),
                "max_minutes": round(float(_max(part, "max_cluster_minutes")), 1),
                "budget": float(part["time_budget_minutes"].max())
                          if "time_budget_minutes" in part else TIME_BUDGET_MINUTES,
            }
            # 실험용 실행(파라미터 스윕·대조군)은 운영 계획이 아니다. DB에
            # 함께 쌓이므로 가장 최근 것이 실험일 수 있다.
            #
            # ⚠️ 예전에는 여기서 **라벨 하드코딩 목록**으로 짐작했다. 목록에
            #    없던 `obs-cmp-1520`이 경고 없이 첫 화면 헤드라인에 올라왔다
            #    (1.26.107). 판정은 db.list_runs()가 한 벌로 하고 화면은 읽기만
            #    한다 — 화면마다 짐작하면 화면마다 다른 답이 나온다.
            last["is_experiment"] = _run_kind(order[0]) == "experiment"
            if last["stockout_before"]:
                last["cut_pct"] = round(
                    (1 - last["stockout_after"] / last["stockout_before"]) * 100, 1)

    return {
        "last": last,
        "period": latest_period(),
        "runs_total": int(rows["run_label"].nunique()) if not rows.empty else 0,
        "running": jobs.running_job(),
        "typical_minutes": _minutes(jobs.typical_elapsed()),
        "typical_vehicles": _typical_vehicles(),
    }


def _runs_newest_first(rows) -> list:
    """실행 라벨을 최신순으로. computed_at이 있으면 그것으로, 없으면 라벨순."""
    if "computed_at" in rows:
        order = (rows.groupby("run_label")["computed_at"].max()
                 .sort_values(ascending=False).index.tolist())
    else:
        order = sorted(rows["run_label"].unique(), reverse=True)
    return order


def _kpi_labels_newest_first(rows) -> list:
    """지표 표의 실행 라벨을 **최신순**으로 (1.26.146).

    `db.load_kpi()`는 `ORDER BY run_label DESC`로 준다. 그런데 `run_label`은
    사람이 `--now`에 적는 이름이라 **정렬 기준이 못 된다** — 사전순으로는
    `sweep-z-01`이 `2026-08-27 23`보다 위라, 오늘 돌린 계획이 있는데도 옛
    실험이 '최신'으로 헤드라인에 오른다.

    같은 함정을 `_runs_newest_first()`는 이미 알고 `computed_at`을 쓴다.
    /kpi만 안 쓰고 있었다 — 화면마다 다른 기준으로 '최신'을 고르면 화면마다
    다른 답이 나온다.

    📌 지금 자료에서는 두 순서가 **우연히 같다.** 그래서 이 값은 눈으로는
    안 보였다. 다만 `2026-05-21 18`이 실제로는 08-25에 계산된 것처럼 라벨과
    시각이 갈리는 자료가 이미 있어, 우연에 기대 둘 자리가 아니다.
    """
    return _runs_newest_first(rows)


def _sum(frame, column: str) -> float:
    return float(pd.to_numeric(frame[column], errors="coerce").sum()) if column in frame else 0.0


def _max(frame, column: str) -> float:
    return float(pd.to_numeric(frame[column], errors="coerce").max()) if column in frame else 0.0


@app.get("/")
def home(request: Request):
    """현황판. '재배치 계획'을 눌러도 여기로 온다 (수정안 33)."""
    return templates.TemplateResponse(request, "home.html", _home_context())


@app.get("/run")
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", _index_context())


@app.get("/api/weather")
def weather_now(refresh: int = 0):
    """지금 날씨. 계획 화면이 비 여부를 띄우는 데 쓴다 (docs/분석/WEATHER.md).

    **화면을 그리는 요청 안에서 부르지 않는다** — 외부 API가 느리면 실행 폼 자체가
    늦게 뜬다. 브라우저가 화면을 띄운 뒤 따로 물어보고, 응답은 10분 캐시한다.
    실패해도 200에 available=false로 답한다(화면이 죽으면 안 된다).
    """
    return JSONResponse(weather_view.current(force=bool(refresh)))


@app.get("/api/forecast")
def weather_forecast(target_date: str = "", refresh: int = 0):
    """계획 대상일 예보. **계획은 하루 앞서 세우므로 정작 맞아야 하는 것은 그날이다.**

    `target_date`(YYYY-MM-DD)를 생략하면 내일이다 — 실행 폼에는 대상일 입력이
    없으므로(요일은 `day_type`이 정한다) 화면은 늘 내일을 묻는다. 다른 날이
    필요하면 이 인자로 직접 부른다.

    `/api/weather`와 같은 성향이다 — 화면을 그리는 요청 안에서 부르지 않고,
    실패해도 200에 available=false로 답한다.
    """
    target = None
    if target_date:
        try:
            target = datetime.strptime(target_date, "%Y-%m-%d").date()
        except ValueError:
            return JSONResponse(
                {"available": False, "raining": False,
                 "error": "target_date는 YYYY-MM-DD 형식이어야 합니다."})
    return JSONResponse(weather_view.forecast(target=target, force=bool(refresh)))


@app.post("/runs")
def create_run(
    request: Request,
    now: str = Form(""),
    period: str = Form(""),
    duration: List[str] = Form([]),
    raw_file: str = Form(""),
    day_type: str = Form(""),
    fleet_size: str = Form(""),
    vehicles_per_round: str = Form(""),
    skip_api: Optional[str] = Form(None),
    skip_eda: Optional[str] = Form(None),
):
    # 🔴 **이 화면에서 띄운 실행은 운영 계획이다.** 종류를 여기서 못박아야
    # `runs.kind`가 채워지고, 화면이 라벨 짐작(`classify_run_label`)에 기대지
    # 않게 된다 — 짐작은 새 이름 규칙이 생길 때마다 틀리고, 틀리면 실험이
    # 첫 화면 헤드라인에 '마지막 계획'으로 올라온다(1.26.107이 그 사고였다).
    # 실험 격자는 CLI에서 `--run-kind experiment`로 띄운다.
    args = ["--run-kind", "plan"]
    for flag, value in (("--now", now), ("--raw-file", raw_file)):
        value = value.strip()
        if value:
            args.extend([flag, value])

    # 잘못된 입력은 파이프라인을 띄우기 전에 폼 단계에서 거른다.
    def invalid(message: str):
        return templates.TemplateResponse(
            request, "index.html", _index_context(error=message), status_code=400)

    # 기간·시간대는 값이 조금만 어긋나도 한참 뒤 단계에서 파일을 못 찾고 멈춘다.
    # 여기서 거르면 사용자가 무엇을 고르면 되는지 그 자리에서 알 수 있다.
    try:
        if (checked := normalize_period(period)):
            args.extend(["--period", checked])
        if (chosen := normalize_durations(duration)):
            args.extend(["--duration", chosen])
    except ValueError as err:
        return invalid(str(err))

    if day_type.strip():
        try:
            args.extend(["--day-type", normalize_day_type(day_type)])
        except ValueError as err:
            return invalid(str(err))

    fleet, per_round = FLEET_SIZE, VEHICLES_PER_ROUND
    try:
        if fleet_size.strip():
            fleet = normalize_fleet_size(fleet_size)
            args.extend(["--fleet-size", str(fleet)])
        if vehicles_per_round.strip():
            per_round = normalize_per_round(vehicles_per_round)
            args.extend(["--vehicles-per-round", str(per_round)])
    except ValueError as err:
        return invalid(str(err))

    # 보유 대수보다 많이 투입할 수는 없다. 상수 경로는 조용히 잘리지만, 사용자가
    # 두 값을 직접 적은 경우에는 잘라 버리는 대신 되돌려서 알려 준다.
    if per_round > fleet:
        return invalid(f"회차당 투입 대수({per_round}대)가 보유 차량 대수({fleet}대)보다 많습니다.")

    if skip_api:
        args.append("--skip-api")
    if skip_eda:
        args.append("--skip-eda")

    try:
        job = jobs.start_job(args)
    except RuntimeError as err:
        # 폼에서 온 요청이므로 JSON 오류 대신 안내 문구를 담아 실행 화면을 다시 보여준다.
        return templates.TemplateResponse(
            request, "index.html", _index_context(error=str(err)), status_code=409
        )

    return RedirectResponse(url=f"/runs/{job.id}", status_code=303)


@app.post("/runs/{job_id}/cancel")
def cancel_run(job_id: str):
    """실행 중인 파이프라인을 중단한다(하위 단계 프로세스까지 함께 종료)."""
    if jobs.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="해당 실행 이력이 없습니다.")
    jobs.cancel_job(job_id)   # 이미 끝난 작업이면 아무 일도 하지 않는다
    return RedirectResponse(url=f"/runs/{job_id}", status_code=303)


@app.post("/runs/{run_label:path}/kind")
def set_run_kind(run_label: str, kind: str = Form(...)):
    """실행의 종류(운영 계획·실험·수집)를 못박는다.

    라벨 규칙으로 짐작한 값은 틀릴 수 있다 — 그리고 틀린 채로 두면 실험이
    첫 화면에 운영 계획으로 올라간다(1.26.107). 사람이 고칠 수 있어야
    짐작에 기대지 않게 된다.

    ⚠️ **지우지는 않는다.** 실행 하나를 지우려면 20개 가까운 테이블에서
    행을 걷어내야 하고, 되돌릴 수 없다. 그 일은 `tools/forget_run.py`가
    `--dry-run`과 함께 맡는다 — 웹 화면에서 한 번의 클릭으로 할 일이 아니다.
    """
    if kind not in store.RUN_KINDS:
        raise HTTPException(status_code=400, detail="모르는 실행 종류입니다.")
    try:
        # 실행 행이 없으면 만들어 두고 못박는 것까지 store가 맡는다 — 라우트가
        # DB 연결을 직접 여는 자리가 아니다(1.26.110).
        store.set_run_kind(run_label, kind)
    except Exception as err:                          # noqa: BLE001
        raise HTTPException(status_code=500,
                            detail=f"실행 종류를 바꾸지 못했습니다: {err}") from err
    return RedirectResponse(url="/run#saved-runs", status_code=303)


@app.get("/runs/{job_id}")
def run_detail(request: Request, job_id: str):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="해당 실행 이력이 없습니다.")
    return templates.TemplateResponse(request, "run_detail.html", {
        "job": job,
        "log": jobs.read_log_tail(job),
        "progress": pipeline_progress(jobs.read_log(job)),
    })


@app.get("/guide")
def guide_page(request: Request):
    """사용 안내.

    설정값을 하드코딩하지 않고 넘긴다 — 차량 대수나 시간 예산을 바꿨을 때
    안내 문서만 옛날 숫자로 남는 일을 막는다.
    """
    return templates.TemplateResponse(request, "guide.html", {
        "fleet_size": FLEET_SIZE,
        "per_round": VEHICLES_PER_ROUND,
        "capacity": VEHICLE_CAPACITY,
        "speed": VEHICLE_SPEED_KMPH,
        "time_budget": TIME_BUDGET_MINUTES,
        "target_z": TARGET_Z,
        "today_day_type": DAY_TYPE_LABELS[resolve_day_type()],
        # 시간대 선택지도 코드에서 읽어 넘긴다 — 안내에 창을 박아 두면
        # 폼과 어긋나는 순간 그대로 거짓말이 된다(tests/test_guide.py).
        "durations": [{"value": d, "label": DURATION_LABELS[d]} for d in DURATIONS],
        # 예상 소요는 **이 서버의 지난 실행에서 뽑는다.** 사람이 적어 두면
        # 조건이 바뀐 뒤에도 남아 거짓말이 된다(옛 안내의 '보통 5~10분'이 그랬다).
        "typical_minutes": _minutes(jobs.typical_elapsed()),
        # 회차당 대수는 작업량이 정한다 — 안내에는 지난 실행의 중앙값을 보여준다.
        "typical_vehicles": _typical_vehicles(),
        # 계획이 쓰는 기준을 안내에 밝힌다 — 코드에만 있으면 현장에서 물어볼 곳이 없다.
        "min_qty": REBAL_MIN_QTY,
        "upper_ratio": TARGET_QTY_UPPER_RATIO,
    })


def _sheet_name(sheet: dict) -> str:
    """지시서 한 장을 가리키는 이름. 차량이 없는 옛 산출물은 군집 번호로."""
    return str(sheet.get("vehicle_id") or f"군집 {sheet.get('cluster')}")


def _orders_context(run_label: Optional[str], duration: Optional[str],
                    vehicle: Optional[str] = None) -> dict:
    """작업지시서 화면의 공통 재료. 고르지 않으면 가장 최근 경로를 쓴다."""
    targets = store.records(store.plan_targets())
    if targets and not run_label:
        run_label = targets[0]["run_label"]
        duration = duration or targets[0]["duration"]

    sheets = orders.build(run_label, duration) if run_label else []

    # 차량 하나만 보기. 한 회차에 17대가 나가면 지시서가 세로로 39,000px,
    # 휴대폰에서 **47화면**이었다(실측 1.26.107). 기사는 자기 차 한 장만
    # 필요한데 앞의 열여섯 장을 넘겨야 했고, 인쇄도 17장이 통으로 나왔다.
    # 서버에서 거른다 — 스크립트가 없어도 되고, 인쇄가 저절로 한 장이 된다.
    names = [_sheet_name(s) for s in sheets]
    if vehicle:
        sheets = [s for s in sheets if _sheet_name(s) == vehicle]

    return {
        "targets": targets,
        "run_label": run_label,
        "duration": duration,
        "sheets": sheets,
        "sheet_names": names,
        # ⚠️ **유효한 값일 때만 넘기면 안 된다.** `vehicle in names`가 아닐
        # 때(오타·재배정으로 번호가 바뀜·옛 QR코드) None으로 떨어뜨리면
        # sheets는 이미 위에서 걸러져 비어 있는데 selected_vehicle만 없어져,
        # 빈 상태가 "'{{ vehicle }}'의 지시서가 없습니다"가 아니라 "경로가
        # 없습니다 — 계획을 끝까지 실행하세요"로 뜬다. 실제로는 그 회차에
        # 지시서가 17장 있는데 계획이 아예 없다고 말하는 것이다(실측 확인).
        # 유효성 검사가 아니라 **그대로 전달**이 맞다 — 화면이 판단한다.
        "selected_vehicle": vehicle,
        "capacity": VEHICLE_CAPACITY,
        "depot_name": DEPOT_NAME,
        "upper_ratio": TARGET_QTY_UPPER_RATIO,
        "min_qty": REBAL_MIN_QTY,
    }


@app.get("/orders")
def orders_page(request: Request, run_label: Optional[str] = None,
                duration: Optional[str] = None, vehicle: Optional[str] = None):
    """차량별 작업지시서 — 현장에 그대로 내보내는 종이."""
    return templates.TemplateResponse(
        request, "orders.html", _orders_context(run_label, duration, vehicle))


@app.get("/orders/live")
def orders_live(request: Request, run_label: Optional[str] = None,
                duration: Optional[str] = None, vehicle: Optional[str] = None):
    """계획 대상 대여소만 타슈 API로 다시 조회해 집행 가능한지 대조한다.

    **사용자가 눌렀을 때만 부른다.** 화면을 열 때마다 외부 API를 때리면
    출발 직전에 정작 필요할 때 제한에 걸릴 수 있다.

    ⚠️ **보던 차량을 그대로 들고 온다**(1.26.146). 예전에는 `vehicle`을 아예
    받지 않아, 한 대만 보던 기사가 '지금 재고와 대조하기'를 누르면 지시서가
    14장으로 되돌아갔다 — 1.26.107이 *"한 회차 17대면 세로 39,000px,
    휴대폰에서 47화면"* 이라며 서버 필터를 넣은 바로 그 자리인데, 뒤에 붙은
    이 화면만 따라가지 않았다.
    """
    context = _orders_context(run_label, duration, vehicle)
    planned = orders.planned_work(context["run_label"], context["duration"])

    try:
        live = tashu.fetch_stations()
    except tashu.TashuError as err:
        context["live_error"] = str(err)
        return templates.TemplateResponse(request, "orders.html", context)

    compared = orders.compare_stock(planned, live)
    live_sheets = orders.build_live(
        context["run_label"], context["duration"], compared)
    # ⚠️ **대조 지시서에도 같은 필터를 건다.** `build_live()`는 `build()`를
    #    스스로 불러 전 차량을 만들므로, 라우트가 `vehicle`을 받는 것만으로는
    #    화면이 안 좁혀진다 — 실제로 그리는 것은 `sheets`가 아니라 이쪽이다.
    #    거르는 규칙은 `_orders_context()`와 **같은 `_sheet_name()`** 이어야
    #    두 목록이 어긋나지 않는다.
    if vehicle:
        live_sheets = [s for s in live_sheets if _sheet_name(s) == vehicle]
    context["compared_sheets"] = live_sheets
    context["live_summary"] = orders.summarize(compared)
    context["checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return templates.TemplateResponse(request, "orders.html", context)


@app.get("/maps")
def maps_page(request: Request):
    return templates.TemplateResponse(request, "maps.html", {
        "groups": catalog.list_maps(),
    })


@app.get("/data")
def data_page(request: Request):
    return templates.TemplateResponse(request, "data.html", {
        "groups": catalog.list_csvs(),
    })


@app.get("/kpi")
def kpi_page(request: Request, run_label: Optional[str] = None):
    """실행별 성과 지표와 실행 간 비교 (docs/분석/KPI.md)."""
    rows = store.kpi(run_label=run_label)

    latest = None
    previous = None
    if not rows.empty:
        # 라벨 사전순이 아니라 **기록 시각순**이다 — 위 헬퍼의 설명 참고.
        labels = _kpi_labels_newest_first(rows)
        latest = rows[rows["run_label"] == labels[0]]
        if len(labels) > 1:
            previous = rows[rows["run_label"] == labels[1]]

    def headline(frame, column, weight=None):
        """회차별 값을 하나로 요약한다(가중평균 또는 합계)."""
        if frame is None or frame.empty or frame[column].isna().all():
            return None
        if weight is None:
            return float(frame[column].sum())
        w = frame[weight].fillna(0)
        return float((frame[column] * w).sum() / w.sum()) if w.sum() else None

    stockout = None
    if latest is not None and not latest["stockout_hours_before"].isna().all():
        before = headline(latest, "stockout_hours_before", "stations")
        after = headline(latest, "stockout_hours_after", "stations")
        if before is not None and after is not None:
            stockout = {"before": before, "after": after, "cut": before - after}

    cards = []
    if latest is not None:
        # 헤드라인에는 **목표 재고를 분모로 삼지 않는 값**을 앞세운다.
        # '목표 도달 비율'은 1.19.7에서 내렸다 — 계획량이 `Q·tanh(격차/Q)`로 눌려
        # 격차가 8대만 넘어도 도달이 구조적으로 불가능하다. 실행을 아무리 잘해도
        # 오르지 않는 값을 헤드라인에 두면 화면이 거짓말을 한다(docs/분석/KPI.md).
        # 표에는 그대로 있고, 대신 '한 번에 닿는 범위'가 그 자리를 설명한다.
        # 타일마다 용어 설명을 함께 넘긴다(수정안 32번). 아래 표의 머리글과
        # **같은 문구**를 쓴다 — 두 곳이 갈리면 같은 지표를 다르게 설명하게 된다.
        for label, column, weight, fmt, tip in [
            ("평균 개선률", "avg_improvement_rate", "stations", "pct",
             "목표 재고까지 모자란 양을 계획이 몇 % 메웠는지입니다."
             " 수요를 맞췄다는 뜻이 아니라 계획을 지켰다는 뜻입니다."),
            ("한 번에 닿는 범위", "reachable_ratio", "stations", "pct",
             "목표까지 벌어진 양이 트럭 적재 용량 안이라 한 번 방문으로 해결할 수 있었던"
             " 대여소의 비율입니다. 낮으면 애초에 한 회차로는 못 푸는 일감이 많다는 뜻입니다."),
            ("km당 개선", "improvement_per_km", "total_distance_km", "num",
             "1km 움직일 때마다 개선률이 얼마나 올랐는지입니다. 연료 대비 효율로 볼 수 있습니다."),
            ("시간 예산 준수", "time_budget_met", "clusters", "pct",
             "차량 한 대의 회차가 시간 예산 안에 끝난 비율입니다."
             " 넘으면 계획이 겨냥한 시간대가 이미 지나가 효과가 줄어듭니다."),
        ]:
            now_value = headline(latest, column, weight)
            before = headline(previous, column, weight) if previous is not None else None
            cards.append({
                "label": label,
                "value": now_value,
                "delta": (now_value - before) if (now_value is not None and before is not None) else None,
                "fmt": fmt,
                "tip": tip,
            })

    # ── 그래프 (docs/구현/DESIGN.md '그래프') ──
    # SVG 문자열을 만들어 넘긴다. 라이브러리를 넣지 않고, 색은 CSS 변수를 상속한다.
    trends = []
    for series in kpi_view.trends(rows):
        trends.append({
            "title": series["title"],
            "hint": series["hint"],
            "svg": charts.line(series["labels"], series["values"],
                               title=series["title"], unit=series["unit"],
                               lower_is_better=series["lower_is_better"]),
            "table": list(zip(series["labels"], series["values"])),
            "unit": series["unit"],
        })

    cost = kpi_view.cost_benefit(rows)
    cost_svg = charts.scatter(cost["points"], x_key="x", y_key="y",
                              x_label="총 이동거리 (km)", y_label=cost["y_label"])

    forecast = kpi_view.forecast_accuracy()
    for series in forecast["series"]:
        series["svg"] = charts.line(series["labels"], series["values"],
                                    title=series["title"], unit=series["unit"],
                                    lower_is_better=True)
        # 표 보기용 쌍은 파이썬에서 만든다 — Jinja에는 zip 필터가 없다.
        series["table"] = list(zip(series["labels"], series["values"]))

    heat = kpi_view.demand_heatmap(latest_period())
    heat_svg = charts.heatmap(heat["rows"], heat["cols"], heat["matrix"], unit="대")
    heat_legend = charts.scale_legend(heat["scale"], "대")

    return templates.TemplateResponse(request, "kpi.html", {
        "rows": store.records(rows),
        "cards": cards,
        "stockout": stockout,
        "calibration": store.stockout_calibration(),
        "trends": trends,
        "cost": cost, "cost_svg": cost_svg,
        "heat": heat, "heat_svg": heat_svg, "heat_legend": heat_legend,
        "forecast": forecast,
        "capacity": VEHICLE_CAPACITY,
        "latest_label": latest["run_label"].iloc[0] if latest is not None else None,
        "previous_label": previous["run_label"].iloc[0] if previous is not None else None,
        "selected_run": run_label,
        # 계획이 아닌 실행(도로 수집 등)은 필터에서 뺀다 — 눌러도
        # 빈 표만 나오는 칩이었다(1.26.107).
        "runs": store.records(store.plan_runs()),
    })


@app.get("/api/kpi")
def api_kpi(run_label: Optional[str] = None, duration: Optional[str] = None):
    """실행별 성과 지표. run_label·duration으로 좁힐 수 있다."""
    rows = store.kpi(run_label=run_label, duration=duration)
    return JSONResponse({
        "run_label": run_label,
        "duration": duration,
        "count": len(rows),
        "rows": store.records(rows),
    })


@app.get("/vehicles")
def vehicles_page(request: Request, run_label: Optional[str] = None, page: int = 1):
    """차량별 누적 작업량과 회차 배정 이력 (docs/구현/FLEET.md)."""
    workload = store.vehicle_workload()

    # 배정 이력은 실행을 거듭할수록 무한히 쌓인다. 한 쪽만 읽고 나머지는
    # 쪽으로 닿게 한다 — 상세는 아래 '쪽 나눔' 주석.
    assignments_total = store.vehicle_assignment_count(run_label=run_label)
    pages = max(1, -(-assignments_total // ASSIGNMENTS_PER_PAGE))   # 올림
    page = min(max(1, page), pages)
    assignments = store.vehicle_assignments(
        run_label=run_label, limit=ASSIGNMENTS_PER_PAGE,
        offset=(page - 1) * ASSIGNMENTS_PER_PAGE)

    # ⚠️ 최소·최대·차이는 **출동한 차량끼리** 잰다. 한 번도 안 나간 차량의
    #    0분을 섞으면 "가장 적게 일한 차량 0분"이 되어 차이가 곧 최대값이 된다
    #    — 로테이션이 고른지를 묻는 자리에서 답이 "안 나간 차가 있다"로 바뀐다.
    #    그것은 바로 옆 '출동한 차량' 타일이 이미 말하고 있다.
    balance = None
    if not workload.empty and workload["rounds"].sum() > 0:
        worked = workload[workload["rounds"] > 0]
        balance = {
            "used": len(worked),
            "idle": int((workload["rounds"] == 0).sum()),
            "min_minutes": float(worked["minutes"].min()),
            "max_minutes": float(worked["minutes"].max()),
            "gap_minutes": round(float(worked["minutes"].max() - worked["minutes"].min()), 1),
        }

    # 시간 예산 준수율 — 회차 단위로 본다(차량 누적이 아니라 한 번의 작업 기준).
    #
    # 🔴 **한 쪽이 아니라 전체를 센다**(1.26.146). 예전에는 바로 위에서 읽은
    #    `assignments`(한 쪽, 50건)로 셌는데, 화면은 그 값을 *"전체 실행"* 이라
    #    밝히고 있었다. 실측: 전체 86회차 중 9건 초과인데 1쪽은 "50회차 중
    #    45회차 · 5건 초과", 2쪽은 "36회차 중 32회차 · 4건 초과"라 답했고
    #    준수율도 90% → 89%로 흔들렸다. 바로 옆 두 타일(`balance`)은 전체를
    #    쓰므로, 한 줄에 선 타일 셋의 기준이 서로 달랐다. 1.26.116이 이 표에
    #    쪽 나눔을 넣을 때 요약 타일을 함께 옮기지 않은 자리다.
    #
    # ⚠️ 쪽 나눔은 **표**를 위한 것이지 요약을 위한 것이 아니다. 요약은 늘
    #    전체를 봐야 하므로 여기서 한 번 더 읽는다(limit 없이).
    budget = None
    all_assignments = store.vehicle_assignments(run_label=run_label)
    if not all_assignments.empty:
        within = int((all_assignments["minutes"] <= TIME_BUDGET_MINUTES).sum())
        budget = {
            "limit": TIME_BUDGET_MINUTES,
            "within": within,
            "total": len(all_assignments),
            "rate": round(within / len(all_assignments) * 100),
            "over": len(all_assignments) - within,
        }

    # 보유 대수는 실행마다 바뀔 수 있으므로(웹 실행 폼의 '차량 대수') 설정 상수가
    # 아니라 DB의 운용 가능 차량 수를 보여준다.
    fleet_size = len(workload) if not workload.empty else FLEET_SIZE

    # 표만으로는 어느 차량에 일이 몰렸는지 행을 다 읽어야 보인다 — 누적 시간
    # 기준 내림차순 막대로 형평성을 한눈에 보여준다(가장 몰린 차량이 위).
    #
    # 표도 같은 순서로 준다. DB는 vehicle_id 순으로 주지만(db.vehicle_workload),
    # 표는 화면에서 위 8행만 펴 두므로(data-row-limit) ID 순이면 v01~v08이라는
    # 아무 뜻 없는 여덟 대가 펴진다. 막대와 순서를 맞추면 위에서 본 그 차량이
    # 표에서도 위에 있다.
    sorted_wl = workload.sort_values("minutes", ascending=False) \
        if not workload.empty else workload

    # balance와 같은 조건이다 — 아직 한 번도 안 나간 새 설치라면 21대가 전부
    # 0분짜리 막대가 되어 아무 뜻이 없다.
    #
    # 🔴 0 기준 막대(`charts.hbar`)로 그렸더니 **21개가 전부 같아 보였다**
    #    (1.26.107). 값이 339~402분이라 폭이 16%뿐이라서, 1000px를 쓰고도
    #    바로 위 타일의 "차이 63.1분"보다 못 알려 줬다. 묻는 것이 *"얼마인가"*
    #    가 아니라 *"고른가"* 라면 기준은 0이 아니라 **고른 상태(평균)** 다.
    #
    # ⚠️ 기준선의 평균에서 **한 번도 안 나간 차량은 뺀다.** `vehicle_workload`는
    #    출동한 적 없는 차량도 0분으로 함께 준다(그래서 바로 위 타일이 "한 번도
    #    나가지 않은 차량 N대"를 셀 수 있다). 그 0을 평균에 넣으면 기준선이
    #    통째로 내려앉아 **일한 차량이 전부 평균 위**로 그려진다 — 21대 중
    #    4대가 놀고 17대가 340분씩이면 기준이 275.2분이 되고 17대가 모두
    #    `+64.8분`이다. "누구에게 몰렸나"에 "일한 사람은 다 평균 이상"이라고
    #    답하는 그림이라 뜻이 없다. 막대는 21대를 다 그리되(0분도 사실이다)
    #    기준만 일한 차량 쪽으로 옮긴다.
    workload_svg = None
    if balance:
        worked_minutes = sorted_wl.loc[sorted_wl["rounds"] > 0, "minutes"]
        workload_svg = charts.deviation_hbar(
            sorted_wl["vehicle_id"].tolist(), sorted_wl["minutes"].tolist(),
            title="차량별 누적 작업 시간 — 출동한 차량 평균과의 차이", unit="분",
            baseline=float(worked_minutes.mean()),
            baseline_label="출동한 차량 평균")

    return templates.TemplateResponse(request, "vehicles.html", {
        "fleet_size": fleet_size,
        "per_round": min(VEHICLES_PER_ROUND, fleet_size),
        "time_budget": TIME_BUDGET_MINUTES,
        "workload": store.records(sorted_wl),
        "workload_svg": workload_svg,
        "assignments": store.records(assignments),
        "assignments_total": assignments_total,
        "page": page,
        "pages": pages,
        "per_page": ASSIGNMENTS_PER_PAGE,
        "balance": balance,
        "budget": budget,
        "selected_run": run_label,
        # 계획이 아닌 실행(도로 수집 등)은 필터에서 뺀다 — 눌러도
        # 빈 표만 나오는 칩이었다(1.26.107).
        "runs": store.records(store.plan_runs()),
    })


@app.get("/api/vehicles")
def api_vehicles():
    """차량별 누적 작업량."""
    workload = store.vehicle_workload()
    fleet_size = len(workload) if not workload.empty else FLEET_SIZE
    return JSONResponse({
        "fleet_size": fleet_size,          # DB의 운용 가능 대수(실행마다 바뀔 수 있음)
        "configured_fleet_size": FLEET_SIZE,   # 웹 프로세스의 설정값(폼 기본값)
        "vehicles_per_round": min(VEHICLES_PER_ROUND, fleet_size),
        "count": len(workload),
        "rows": store.records(workload),
    })


@app.get("/api/vehicles/assignments")
def api_vehicle_assignments(vehicle_id: Optional[str] = None,
                            run_label: Optional[str] = None):
    """회차별 차량 배정 이력. vehicle_id·run_label로 좁힐 수 있다."""
    history = store.vehicle_assignments(vehicle_id=vehicle_id, run_label=run_label)
    return JSONResponse({
        "vehicle_id": vehicle_id,
        "run_label": run_label,
        "count": len(history),
        "rows": store.records(history),
    })


@app.get("/view/{relpath:path}")
def view_map(request: Request, relpath: str):
    """지도 HTML을 대시보드 안(iframe)에서 열람한다."""
    target = catalog.safe_resolve(relpath)
    if target is None or target.suffix.lower() != ".html":
        raise HTTPException(status_code=404, detail="열람할 수 없는 파일입니다.")
    return templates.TemplateResponse(request, "view.html", {
        "name": target.name,
        "relpath": relpath,
    })


def _count_csv_rows(path: Path) -> int:
    """헤더를 뺀 데이터 행 수를 센다.

    전체를 파싱하지 않고 개행만 세므로 큰 파일에서도 가볍다.
    (필드 안에 줄바꿈이 든 CSV는 과다 계산되지만 파이프라인 산출물에는 없다.)
    """
    with path.open("rb") as f:
        newlines = sum(chunk.count(b"\n") for chunk in iter(lambda: f.read(1 << 20), b""))
    return max(0, newlines - 1)


@app.get("/preview/{relpath:path}")
def preview_csv(request: Request, relpath: str):
    target = catalog.safe_resolve(relpath)
    if target is None or target.suffix.lower() != ".csv":
        raise HTTPException(status_code=404, detail="미리볼 수 없는 파일입니다.")

    # 미리보기는 앞부분만 필요하므로 전체를 읽지 않는다.
    max_rows = 200
    df = pd.read_csv(target, encoding="utf-8", nrows=max_rows)
    total_rows = _count_csv_rows(target)
    table_html = df.to_html(classes="preview-table", index=False, border=0)

    return templates.TemplateResponse(request, "preview.html", {
        "name": target.name,
        "relpath": relpath,
        "total_rows": total_rows,
        "shown_rows": len(df),
        "table_html": table_html,
    })


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """아이콘이 없으므로 404 로그를 남기지 않고 조용히 응답한다."""
    return Response(status_code=204)


@app.get("/files/{relpath:path}")
def serve_file(relpath: str):
    """data/ 아래의 지도 HTML·CSV를 서빙한다 (지도는 브라우저에서 바로 열림)."""
    target = catalog.safe_resolve(relpath)
    if target is None:
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")
    return FileResponse(target)


# ---------------- JSON API ----------------

@app.get("/api/runs/{job_id}")
def api_run_status(job_id: str):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="해당 실행 이력이 없습니다.")
    return {
        "id": job.id,
        "status": job.status,
        "returncode": job.returncode,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "args": job.args,
    }


def _load_or_404(table: str, run_label: Optional[str], duration: Optional[str]):
    """산출물을 읽고, 없으면 404. (DB 우선, 이전 산출물은 CSV 폴백)"""
    frame, source = store.load(table, run_label=run_label, duration=duration)
    if frame.empty:
        raise HTTPException(
            status_code=404,
            detail=f"'{table}' 산출물이 없습니다. 파이프라인을 먼저 실행하세요."
                   + (f" (run_label={run_label})" if run_label else ""),
        )
    return frame, source


def _int(value, default=0) -> int:
    """CSV 폴백 등으로 결측이 섞여도 API가 500으로 죽지 않게 한다."""
    try:
        if pd.isna(value):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _envelope(frame: pd.DataFrame, source: str, run_label: Optional[str],
              duration: Optional[str]) -> dict:
    """레코드 목록에 어느 실행분인지·어디서 읽었는지를 함께 담는다."""
    label = run_label
    if label is None and "run_label" in frame.columns and not frame.empty:
        label = frame["run_label"].iloc[0]
    return {
        "run_label": label,
        "duration": duration,
        "source": source,          # db | csv
        "count": len(frame),
        "rows": store.records(frame),
    }


@app.get("/api/pipeline-runs")
def api_pipeline_runs():
    """DB에 기록된 파이프라인 실행 이력(최신순).

    웹에서 띄운 작업 상태를 보는 /api/runs/{job_id}와는 다른 개념이다.
    이쪽은 산출물이 어느 실행(run_label)에 속하는지를 다룬다.
    """
    runs = store.run_labels()
    return JSONResponse({"count": len(runs), "rows": store.records(runs)})


@app.get("/api/stations")
def api_stations(run_label: Optional[str] = None, duration: Optional[str] = None):
    """Pick/Drop 후보를 GeoJSON FeatureCollection으로 반환한다.

    run_label을 주면 그 실행분을, 생략하면 최신 실행분을 돌려준다.
    """
    df, source = _load_or_404("pick_drop", run_label, duration)

    features = []
    for _, row in df.iterrows():
        rebal = _int(row["rebal_qty"])
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [float(row["lon"]), float(row["lat"])]},
            "properties": {
                "station_id": row["station_id"],
                "station_name": row.get("station_name", ""),
                "rebal_qty": rebal,
                "type": "drop" if rebal > 0 else "pick",
                "cluster": _int(row.get("cluster")),
                "stock": _int(row.get("stock")),
            },
        })

    label = run_label
    if label is None and "run_label" in df.columns and not df.empty:
        label = df["run_label"].iloc[0]

    return JSONResponse({
        "type": "FeatureCollection",
        "run_label": label,
        "source": source,
        "features": features,
    })


@app.get("/api/plans/ilp")
def api_ilp_plan(run_label: Optional[str] = None, duration: Optional[str] = None):
    df, source = _load_or_404("ilp_plan", run_label, duration)
    return JSONResponse(_envelope(df, source, run_label, duration))


@app.get("/api/plans/vrp")
def api_vrp_plan(run_label: Optional[str] = None, duration: Optional[str] = None):
    df, source = _load_or_404("vrp_plan", run_label, duration)
    return JSONResponse(_envelope(df, source, run_label, duration))


@app.get("/api/metrics")
def api_metrics(run_label: Optional[str] = None, duration: Optional[str] = None):
    df, source = _load_or_404("metrics", run_label, duration)
    return JSONResponse(_envelope(df, source, run_label, duration))


@app.get("/api/route-summary")
def api_route_summary(run_label: Optional[str] = None, duration: Optional[str] = None):
    """클러스터별 총 이동거리·운행시간 (step4 산출)."""
    df, source = _load_or_404("route_summary", run_label, duration)
    return JSONResponse(_envelope(df, source, run_label, duration))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("webapp.app:app", host="127.0.0.1", port=8000, reload=True)
