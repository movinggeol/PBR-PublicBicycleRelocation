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
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from project_config import (
    DAY_TYPE_AUTO, DAY_TYPE_LABELS, DAY_TYPES, DEFAULT_DAY_TYPE, DEFAULT_DURATION,
    DEFAULT_NOW, DEFAULT_RAW_FILE, DEPOT_NAME, DURATION_LABELS, DURATIONS,
    FLEET_SIZE, MAX_FLEET_SIZE, REBAL_MIN_QTY, TARGET_QTY_UPPER_RATIO, TARGET_Z,
    TIME_BUDGET_MINUTES, VEHICLE_CAPACITY, VEHICLE_SPEED_KMPH, VEHICLES_PER_ROUND,
    available_periods, latest_period, normalize_day_type, normalize_durations,
    normalize_fleet_size, normalize_per_round, normalize_period, resolve_day_type,
)
import tashu
from webapp import catalog, jobs, orders, store

app = FastAPI(title="PBR 파이프라인 대시보드", docs_url="/api/docs")

_HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(_HERE / "templates"))
# 글꼴을 같이 담아 서빙한다 — 인터넷이 끊겨도 화면이 같아야 한다.
app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

# 작업 상태를 사람이 읽는 말로 — 템플릿 전역이라 어느 화면에서나 같은 낱말을 쓴다.
JOB_STATUS_LABELS = {
    "running": "실행 중",
    "success": "완료",
    "failed": "실패",
    "cancelled": "중단됨",
    "interrupted": "추적 끊김",
}
templates.env.globals["job_labels"] = JOB_STATUS_LABELS

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


def _index_context(error: Optional[str] = None) -> dict:
    # 기간·시간대는 **요청마다 다시 읽는다.** 서버를 띄워 둔 채 새 달치 순수요를
    # 계산해도 곧바로 선택지에 나와야 하기 때문이다(project_config의 상수는
    # import 시점에 굳는다).
    periods = available_periods()
    return {
        "defaults": {
            "now": DEFAULT_NOW,
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
        # 시간대는 네 창이 전부다. 창마다 수요 방향이 반대라 섞지 않는다.
        "durations": [{"value": d, "label": DURATION_LABELS[d]} for d in DURATIONS],
        # 평일과 휴일은 수요 구조가 달라 한 실행에 섞지 않는다 (docs/steps/step0_raw.md).
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


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", _index_context())


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
    args = []
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


def _orders_context(run_label: Optional[str], duration: Optional[str]) -> dict:
    """작업지시서 화면의 공통 재료. 고르지 않으면 가장 최근 경로를 쓴다."""
    targets = store.records(store.plan_targets())
    if targets and not run_label:
        run_label = targets[0]["run_label"]
        duration = duration or targets[0]["duration"]
    return {
        "targets": targets,
        "run_label": run_label,
        "duration": duration,
        "sheets": orders.build(run_label, duration) if run_label else [],
        "capacity": VEHICLE_CAPACITY,
        "depot_name": DEPOT_NAME,
        "upper_ratio": TARGET_QTY_UPPER_RATIO,
        "min_qty": REBAL_MIN_QTY,
    }


@app.get("/orders")
def orders_page(request: Request, run_label: Optional[str] = None,
                duration: Optional[str] = None):
    """차량별 작업지시서 — 현장에 그대로 내보내는 종이."""
    return templates.TemplateResponse(
        request, "orders.html", _orders_context(run_label, duration))


@app.get("/orders/live")
def orders_live(request: Request, run_label: Optional[str] = None,
                duration: Optional[str] = None):
    """계획 대상 대여소만 타슈 API로 다시 조회해 집행 가능한지 대조한다.

    **사용자가 눌렀을 때만 부른다.** 화면을 열 때마다 외부 API를 때리면
    출발 직전에 정작 필요할 때 제한에 걸릴 수 있다.
    """
    context = _orders_context(run_label, duration)
    planned = orders.planned_work(context["run_label"], context["duration"])

    try:
        live = tashu.fetch_stations()
    except tashu.TashuError as err:
        context["live_error"] = str(err)
        return templates.TemplateResponse(request, "orders.html", context)

    compared = orders.compare_stock(planned, live)
    context["compared"] = store.records(compared)
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
    """실행별 성과 지표와 실행 간 비교 (docs/KPI.md)."""
    rows = store.kpi(run_label=run_label)

    latest = None
    previous = None
    if not rows.empty:
        labels = rows["run_label"].drop_duplicates().tolist()
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
        for label, column, weight, fmt in [
            ("평균 개선률", "avg_improvement_rate", "stations", "pct"),
            ("목표 도달 비율", "target_met_ratio", "stations", "pct"),
            ("km당 개선", "improvement_per_km", "total_distance_km", "num"),
            ("시간 예산 준수", "time_budget_met", "clusters", "pct"),
        ]:
            now_value = headline(latest, column, weight)
            before = headline(previous, column, weight) if previous is not None else None
            cards.append({
                "label": label,
                "value": now_value,
                "delta": (now_value - before) if (now_value is not None and before is not None) else None,
                "fmt": fmt,
            })

    return templates.TemplateResponse(request, "kpi.html", {
        "rows": store.records(rows),
        "cards": cards,
        "stockout": stockout,
        "latest_label": latest["run_label"].iloc[0] if latest is not None else None,
        "previous_label": previous["run_label"].iloc[0] if previous is not None else None,
        "selected_run": run_label,
        "runs": store.records(store.run_labels()),
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
def vehicles_page(request: Request, run_label: Optional[str] = None):
    """차량별 누적 작업량과 회차 배정 이력 (docs/FLEET.md)."""
    workload = store.vehicle_workload()
    assignments = store.vehicle_assignments(run_label=run_label)

    balance = None
    if not workload.empty and workload["rounds"].sum() > 0:
        worked = workload[workload["rounds"] > 0]
        balance = {
            "used": len(worked),
            "idle": int((workload["rounds"] == 0).sum()),
            "min_minutes": float(workload["minutes"].min()),
            "max_minutes": float(workload["minutes"].max()),
            "gap_minutes": round(float(workload["minutes"].max() - workload["minutes"].min()), 1),
        }

    # 시간 예산 준수율 — 회차 단위로 본다(차량 누적이 아니라 한 번의 작업 기준).
    budget = None
    if not assignments.empty:
        within = int((assignments["minutes"] <= TIME_BUDGET_MINUTES).sum())
        budget = {
            "limit": TIME_BUDGET_MINUTES,
            "within": within,
            "total": len(assignments),
            "rate": round(within / len(assignments) * 100),
            "over": len(assignments) - within,
        }

    # 보유 대수는 실행마다 바뀔 수 있으므로(웹 실행 폼의 '차량 대수') 설정 상수가
    # 아니라 DB의 운용 가능 차량 수를 보여준다.
    fleet_size = len(workload) if not workload.empty else FLEET_SIZE

    return templates.TemplateResponse(request, "vehicles.html", {
        "fleet_size": fleet_size,
        "per_round": min(VEHICLES_PER_ROUND, fleet_size),
        "time_budget": TIME_BUDGET_MINUTES,
        "workload": store.records(workload),
        "assignments": store.records(assignments.head(60)),
        "balance": balance,
        "budget": budget,
        "selected_run": run_label,
        "runs": store.records(store.run_labels()),
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
