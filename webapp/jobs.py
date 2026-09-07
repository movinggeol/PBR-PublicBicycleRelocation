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
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from project_config import DATA_ROOT, PROJECT_ROOT

WEBAPP_DATA = DATA_ROOT / "webapp"
LOG_DIR = WEBAPP_DATA / "logs"
REGISTRY_FILE = WEBAPP_DATA / "runs.json"

# 이력이 무한히 쌓이지 않도록 최근 N건만 보관한다(실행 중인 작업은 항상 유지).
MAX_HISTORY = 100

# 조회 함수도 락을 잡는데 start_job이 락을 쥔 채 running_job()을 호출하므로
# 재진입 가능한 RLock이어야 교착이 생기지 않는다.
_lock = threading.RLock()
_jobs: Dict[str, "Job"] = {}
_running_proc: Optional[subprocess.Popen] = None


@dataclass
class Job:
    id: str
    args: List[str] = field(default_factory=list)
    status: str = "running"   # running | success | failed | cancelled | interrupted
    returncode: Optional[int] = None
    started_at: str = ""
    finished_at: str = ""
    cancelled: bool = False

    @property
    def log_path(self) -> Path:
        return LOG_DIR / f"run_{self.id}.log"

    @property
    def is_running(self) -> bool:
        return self.status == "running"


def _now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _new_job_id() -> str:
    """작업 ID를 만든다.

    초 단위 ID는 같은 초에 두 번 실행하면 충돌해 이전 기록과 로그가
    덮어써지므로 밀리초까지 쓰고, 그래도 겹치면 일련번호를 붙인다.
    (문자열 정렬이 곧 시간순 정렬이 되도록 자리수를 고정한다.)
    """
    base = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    job_id = base
    serial = 1
    while job_id in _jobs:
        serial += 1
        job_id = f"{base}-{serial}"
    return job_id


def _prune() -> None:
    """최근 MAX_HISTORY건만 남긴다. 실행 중인 작업은 예외 없이 보존.

    **레코드를 버릴 때 로그 파일도 함께 지운다** (1.26.149). 예전에는 레코드만
    잘라서, 이력에서 사라진 실행의 로그가 `data/webapp/logs/`에 영영 남았다 —
    실측 건당 약 360KB라 100건이면 35MB가 아무도 찾지 않는 파일로 쌓인다.
    이력에서 못 여는 로그는 지워도 잃을 것이 없다.
    """
    if len(_jobs) <= MAX_HISTORY:
        return
    keep = {job.id for job in sorted(_jobs.values(), key=lambda j: j.id, reverse=True)[:MAX_HISTORY]}
    keep |= {job.id for job in _jobs.values() if job.is_running}
    for job_id in [j for j in _jobs if j not in keep]:
        job = _jobs.pop(job_id, None)
        if job is None:
            continue
        try:
            job.log_path.unlink(missing_ok=True)
        except OSError:
            pass       # 지우지 못해도 이력 정리는 계속한다


def _save_registry() -> None:
    WEBAPP_DATA.mkdir(parents=True, exist_ok=True)
    _prune()
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

    known = {f.name for f in fields(Job)}
    for item in payload:
        # 과거 버전에서 저장된 레코드에 없는/추가된 키가 있어도 깨지지 않게 한다.
        job = Job(**{k: v for k, v in item.items() if k in known})
        # 서버 재시작으로 추적이 끊긴 작업은 '중단됨'으로 표시
        if job.status == "running":
            job.status = "interrupted"
            job.finished_at = job.finished_at or _now_str()
        _jobs[job.id] = job


def list_jobs() -> List[Job]:
    """최신 작업이 앞에 오도록 반환한다."""
    with _lock:
        return sorted(_jobs.values(), key=lambda j: j.id, reverse=True)


def elapsed_seconds(job: "Job") -> Optional[float]:
    """한 작업이 걸린 시간(초). 시각이 없거나 이상하면 None."""
    if not (job.started_at and job.finished_at):
        return None
    try:
        started = time.mktime(time.strptime(job.started_at, "%Y-%m-%d %H:%M:%S"))
        finished = time.mktime(time.strptime(job.finished_at, "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None
    return finished - started if finished >= started else None


def typical_elapsed(limit: int = 20) -> Optional[float]:
    """최근 성공한 실행의 **중앙값** 소요 시간(초). 기록이 없으면 None.

    안내 화면의 예상 소요를 여기서 뽑는다 — 사람이 숫자를 적어 두면 조건이
    바뀐 뒤에도 그대로 남아 거짓말이 된다(예전 안내의 '보통 5~10분'이 그랬다).
    평균이 아니라 중앙값인 이유는, 중간에 멈췄다 이어 돌린 한 건이 평균을
    통째로 끌어올리기 때문이다.
    """
    samples = []
    for job in list_jobs():
        if job.status != "success":
            continue
        if (seconds := elapsed_seconds(job)) is not None:
            samples.append(seconds)
        if len(samples) >= limit:
            break
    if not samples:
        return None
    samples.sort()
    middle = len(samples) // 2
    if len(samples) % 2:
        return samples[middle]
    return (samples[middle - 1] + samples[middle]) / 2


def get_job(job_id: str) -> Optional[Job]:
    with _lock:
        return _jobs.get(job_id)


def running_job() -> Optional[Job]:
    with _lock:
        for job in _jobs.values():
            if job.is_running:
                return job
        return None


def read_log(job: Job) -> str:
    """로그 파일 전체를 반환한다.

    진행 단계 표시는 로그 맨 앞의 계획 블록을 봐야 하므로 tail로는 안 된다.
    """
    if not job.log_path.exists():
        return ""
    try:
        return job.log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def read_log_tail(job: Job, max_lines: int = 300) -> str:
    """로그 파일의 마지막 max_lines 줄을 반환한다.

    ⚠️ **'아직'과 '이제 없다'를 가른다** (1.26.149). 예전에는 파일이 없으면
    무조건 *"로그가 아직 없습니다"* 라고 답했다. 그래서 9일 전에 **성공으로
    끝난** 실행이 오지 않을 것을 기다리라고 말하고 있었다(실측: 이력 15건 중
    11건의 로그가 이미 없었다). `interrupted` 상태에서는 화면이 *"실제로
    끝까지 돌았는지는 아래 로그로 판단하세요"* 라고 안내하므로 더 나쁘다 —
    없는 증거를 보라고 시키는 셈이다.
    """
    if not job.log_path.exists():
        if job.is_running:
            return "(로그가 아직 없습니다)"
        return "(로그가 남아 있지 않습니다 — 오래된 실행이라 정리되었습니다)"
    try:
        text = job.log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as err:
        return f"(로그를 읽을 수 없습니다: {err})"
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = [f"... (앞 {len(lines) - max_lines}줄 생략) ..."] + lines[-max_lines:]
    return "\n".join(lines)


def _terminate_tree(proc: subprocess.Popen) -> None:
    """실행 중인 파이프라인을 하위 단계 프로세스까지 함께 종료한다.

    run_pipeline.py는 각 단계를 다시 subprocess로 띄우므로 부모만 죽이면
    진행 중이던 단계가 고아 프로세스로 계속 돈다. Windows에서는 taskkill /T로
    프로세스 트리를 정리한다.
    """
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True, check=False,
        )
    else:
        proc.terminate()


def cancel_job(job_id: str) -> bool:
    """실행 중인 작업을 중단한다. 중단 요청을 보냈으면 True."""
    with _lock:
        job = _jobs.get(job_id)
        if job is None or not job.is_running or _running_proc is None:
            return False
        job.cancelled = True
        proc = _running_proc

    # 종료는 락 밖에서 — taskkill이 끝날 때까지 다른 요청을 막을 이유가 없다.
    _terminate_tree(proc)
    return True


def _watch(job: Job, proc: subprocess.Popen, log_file) -> None:
    """subprocess 종료를 기다렸다가 작업 상태를 갱신한다."""
    global _running_proc
    returncode = proc.wait()
    log_file.close()
    with _lock:
        job.returncode = returncode
        if job.cancelled:
            job.status = "cancelled"
        else:
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
            id=_new_job_id(),
            args=list(pipeline_args),
            started_at=_now_str(),
        )

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = open(job.log_path, "w", encoding="utf-8")

        try:
            # 하위 파이썬 프로세스의 출력이 콘솔 인코딩(cp949)으로 깨지지 않도록 UTF-8 강제
            env = dict(os.environ)
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            # 출력이 파일로 가면 파이썬이 블록 버퍼링을 하는 탓에, run_pipeline이
            # 찍는 진행 표시('[3/11] 실행:')가 몇 KB씩 몰려서 뒤늦게 나온다.
            # 3초마다 새로고침하는 진행 화면이 실제보다 늘 뒤처져 보인다.
            env["PYTHONUNBUFFERED"] = "1"

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
        except Exception:
            # 프로세스를 못 띄웠으면 열어둔 로그 파일을 닫고 그대로 올려보낸다.
            log_file.close()
            raise

        _running_proc = proc
        _jobs[job.id] = job
        _save_registry()

    threading.Thread(target=_watch, args=(job, proc, log_file), daemon=True).start()
    return job


_load_registry()
