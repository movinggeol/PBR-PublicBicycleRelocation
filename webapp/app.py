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
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from project_config import DEFAULT_DURATION, DEFAULT_NOW, DEFAULT_PERIOD, DEFAULT_RAW_FILE
from webapp import catalog, jobs

app = FastAPI(title="PBR 파이프라인 대시보드", docs_url="/api/docs")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


# ---------------- 페이지 ----------------

@app.get("/")
def index(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "defaults": {
            "now": DEFAULT_NOW,
            "period": DEFAULT_PERIOD,
            "duration": DEFAULT_DURATION,
            "raw_file": DEFAULT_RAW_FILE,
        },
        "running": jobs.running_job(),
        "jobs": jobs.list_jobs()[:15],
        "latest": catalog.latest_outputs(),
    })


@app.post("/runs")
def create_run(
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
        raise HTTPException(status_code=409, detail=str(err))

    return RedirectResponse(url=f"/runs/{job.id}", status_code=303)


@app.get("/runs/{job_id}")
def run_detail(request: Request, job_id: str):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="해당 실행 이력이 없습니다.")
    return templates.TemplateResponse("run_detail.html", {
        "request": request,
        "job": job,
        "log": jobs.read_log_tail(job),
    })


@app.get("/maps")
def maps_page(request: Request):
    return templates.TemplateResponse("maps.html", {
        "request": request,
        "groups": catalog.list_maps(),
    })


@app.get("/data")
def data_page(request: Request):
    return templates.TemplateResponse("data.html", {
        "request": request,
        "groups": catalog.list_csvs(),
    })


@app.get("/view/{relpath:path}")
def view_map(request: Request, relpath: str):
    """지도 HTML을 대시보드 안(iframe)에서 열람한다."""
    target = catalog.safe_resolve(relpath)
    if target is None or target.suffix.lower() != ".html":
        raise HTTPException(status_code=404, detail="열람할 수 없는 파일입니다.")
    return templates.TemplateResponse("view.html", {
        "request": request,
        "name": target.name,
        "relpath": relpath,
    })


@app.get("/preview/{relpath:path}")
def preview_csv(request: Request, relpath: str):
    target = catalog.safe_resolve(relpath)
    if target is None or target.suffix.lower() != ".csv":
        raise HTTPException(status_code=404, detail="미리볼 수 없는 파일입니다.")

    df = pd.read_csv(target, encoding="utf-8", low_memory=False)
    max_rows = 200
    table_html = df.head(max_rows).to_html(
        classes="preview-table", index=False, border=0
    )
    return templates.TemplateResponse("preview.html", {
        "request": request,
        "name": target.name,
        "relpath": relpath,
        "total_rows": len(df),
        "shown_rows": min(max_rows, len(df)),
        "table_html": table_html,
    })


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


def _read_latest_csv(subdir: str, pattern: str) -> pd.DataFrame:
    path = catalog.latest_file(subdir, pattern)
    if path is None:
        raise HTTPException(status_code=404, detail=f"'{subdir}'에 산출물이 없습니다. 파이프라인을 먼저 실행하세요.")
    return pd.read_csv(path, encoding="utf-8", low_memory=False)


@app.get("/api/stations")
def api_stations():
    """최신 Pick/Drop 후보를 GeoJSON FeatureCollection으로 반환한다."""
    df = _read_latest_csv("ILP/후보", "top*.csv")
    features = []
    for _, row in df.iterrows():
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [float(row["lon"]), float(row["lat"])]},
            "properties": {
                "station_id": row["station_id"],
                "station_name": row.get("station_name", ""),
                "rebal_qty": int(row["rebal_qty"]),
                "type": "drop" if row["rebal_qty"] > 0 else "pick",
                "cluster": int(row["cluster"]),
                "stock": int(row["stock"]),
            },
        })
    return JSONResponse({"type": "FeatureCollection", "features": features})


@app.get("/api/plans/ilp")
def api_ilp_plan():
    df = _read_latest_csv("ILP", "ILP_plan*.csv")
    return JSONResponse(df.to_dict(orient="records"))


@app.get("/api/plans/vrp")
def api_vrp_plan():
    df = _read_latest_csv("VRP", "VRP_plan*.csv")
    return JSONResponse(df.to_dict(orient="records"))


@app.get("/api/metrics")
def api_metrics():
    df = _read_latest_csv("성능 지표", "verification*.csv")
    return JSONResponse(df.to_dict(orient="records"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("webapp.app:app", host="127.0.0.1", port=8000, reload=True)
