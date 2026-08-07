"""파이프라인 실행 작업(Job) 관리.

run_pipeline.py를 subprocess로 실행하고 상태와 로그를 추적한다.
파이프라인 단계들이 같은 data/pp_data 파일을 읽고 쓰므로
동시에 1개 작업만 허용한다.

작업 이력은 data/webapp/runs.json, 로그는 data/webapp/logs/에 남는다.
(data/는 .gitignore 대상이라 Git에 포함되지 않는다.)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from project_config import PROJECT_ROOT

WEBAPP_DATA = PROJECT_ROOT / "data" / "webapp"
LOG_DIR = WEBAPP_DATA / "logs"
REGISTRY_FILE = WEBAPP_DATA / "runs.json"

_lock = threading.Lock()
_jobs: Dict[str, "Job"] = {}
_running_proc: Optional[subprocess.Popen] = None


@dataclass
class Job:
    id: str
    args: List[str] = field(default_factory=list)
    status: str = "running"            # running | success | failed | interrupted
    returncode: Optional[int] = None
    started_at: str = ""
    finished_at: str = ""

    @property
    def log_path(self) -> Path:
        return LOG_DIR / f"run_{self.id}.log"

    @property
    def is_running(self) -> bool:
        return self.status == "running"


def _now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _save_registry() -> None:
    WEBAPP_DATA.mkdir(parents=True, exist_ok=True)
    payload = [asdict(job) for job in _jobs.values()]
    REGISTRY_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _load_registry() -> None:
    if not REGISTRY_FILE.exists():
        return
    try:
        payload = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    for item in payload:
        job = Job(**item)
        # 서버 재시작으로 추적이 끊긴 작업은 '중단됨'으로 표시
        if job.status == "running":
            job.status = "interrupted"
            job.finished_at = job.finished_at or _now_str()
        _jobs[job.id] = job


def list_jobs() -> List[Job]:
    """최신 작업이 앞에 오도록 반환한다."""
    return sorted(_jobs.values(), key=lambda j: j.id, reverse=True)


def get_job(job_id: str) -> Optional[Job]:
    return _jobs.get(job_id)


def running_job() -> Optional[Job]:
    for job in _jobs.values():
        if job.is_running:
            return job
    return None


def read_log_tail(job: Job, max_lines: int = 300) -> str:
    """로그 파일의 마지막 max_lines 줄을 반환한다."""
    if not job.log_path.exists():
        return "(로그가 아직 없습니다)"
    try:
        text = job.log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as err:
        return f"(로그를 읽을 수 없습니다: {err})"
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = [f"... (앞 {len(lines) - max_lines}줄 생략) ..."] + lines[-max_lines:]
    return "\n".join(lines)


def _watch(job: Job, proc: subprocess.Popen, log_file) -> None:
    """subprocess 종료를 기다렸다가 작업 상태를 갱신한다."""
    global _running_proc
    returncode = proc.wait()
    log_file.close()
    with _lock:
        job.returncode = returncode
        job.status = "success" if returncode == 0 else "failed"
        job.finished_at = _now_str()
        _running_proc = None
        _save_registry()


def start_job(pipeline_args: List[str]) -> Job:
    """run_pipeline.py를 백그라운드로 실행하고 Job을 반환한다.

    이미 실행 중인 작업이 있으면 RuntimeError를 던진다.
    """
    global _running_proc

    with _lock:
        if running_job() is not None:
            raise RuntimeError("이미 실행 중인 파이프라인이 있습니다. 완료 후 다시 시도하세요.")

        job = Job(
            id=time.strftime("%Y%m%d-%H%M%S"),
            args=list(pipeline_args),
            started_at=_now_str(),
        )

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = open(job.log_path, "w", encoding="utf-8")

        # 하위 파이썬 프로세스의 출력이 콘솔 인코딩(cp949)으로 깨지지 않도록 UTF-8 강제
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        command = [sys.executable, str(PROJECT_ROOT / "run_pipeline.py")] + list(pipeline_args)
        log_file.write("$ " + " ".join(command) + "\n\n")
        log_file.flush()

        proc = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
        )
        _running_proc = proc
        _jobs[job.id] = job
        _save_registry()

    threading.Thread(target=_watch, args=(job, proc, log_file), daemon=True).start()
    return job


_load_registry()
