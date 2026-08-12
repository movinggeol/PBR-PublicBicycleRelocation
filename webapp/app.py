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

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from project_config import (
    DEFAULT_DURATION, DEFAULT_NOW, DEFAULT_PERIOD, DEFAULT_RAW_FILE,
    FLEET_SIZE, TIME_BUDGET_MINUTES, VEHICLES_PER_ROUND,
)
from webapp import catalog, jobs, store

app = FastAPI(title="PBR 파이프라인 대시보드", docs_url="/api/docs")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


# ---------------- 페이지 ----------------

def _index_context(error: Optional[str] = None) -> dict:
    return {
        "defaults": {
            "now": DEFAULT_NOW,
            "period": DEFAULT_PERIOD,
            "duration": DEFAULT_DURATION,
            "raw_file": DEFAULT_RAW_FILE,
        },
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
    duration: str = Form(""),
    raw_file: str = Form(""),
    skip_api: Optional[str] = Form(None),
    skip_eda: Optional[str] = Form(None),
):
    args = []
    for flag, value in (("--now", now), ("--period", period),
                        ("--duration", duration), ("--raw-file", raw_file)):
        value = value.strip()
        if value:
            args.extend([flag, value])
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
    })


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

    return templates.TemplateResponse(request, "vehicles.html", {
        "fleet_size": FLEET_SIZE,
        "per_round": VEHICLES_PER_ROUND,
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
    return JSONResponse({
        "fleet_size": FLEET_SIZE,
        "vehicles_per_round": VEHICLES_PER_ROUND,
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
