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
import re
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


def elapsed_seconds(job: "Job", *, now: Optional[float] = None) -> Optional[float]:
    """한 작업이 걸린 시간(초). 시각이 없거나 이상하면 None.

    **돌고 있는 작업은 지금까지 걸린 시간**이다 (1.26.262). 예전에는 `finished_at`
    이 없으면 None이라, 진행 화면이 예상만 적고 *지금 몇 분째인지*는 말하지
    않았다 — 예상이 맞는지 보려면 둘이 나란히 있어야 한다.
    """
    if not job.started_at:
        return None
    try:
        started = time.mktime(time.strptime(job.started_at, "%Y-%m-%d %H:%M:%S"))
        if job.finished_at:
            finished = time.mktime(time.strptime(job.finished_at, "%Y-%m-%d %H:%M:%S"))
        elif job.is_running:
            finished = time.time() if now is None else now
        else:
            return None
    except ValueError:
        return None
    return finished - started if finished >= started else None


# ───────── 예상 소요 시간 (1.26.214) ─────────
#
# 🔴 **시간대를 몇 개 골랐느냐가 가장 크게 좌우한다.** 예전 안내는 지난 실행의
# 중앙값 하나만 보여 줬는데, 기록된 실행이 전부 시간대 **하나**짜리라 네 개를
# 고른 사람에게도 같은 숫자를 보여 주고 있었다 — 실제로는 그만큼 늘어난다.
#
# 어느 단계가 시간대마다 되풀이되는지는 **짐작이 아니라 코드가 말한다.**
# `duration_list()`를 도는 단계가 되풀이되는 단계다(2026-09-14 전수 확인).
# 여기 목록과 실제가 갈리면 예상이 조용히 틀리므로 시험이 대조한다.
DURATION_SCALED = frozenset({
    "step0_collect/calculate_target_qty.py",
    "step1_cluster/top_st_clustering.py",
    "step1_cluster/st_visualization.py",
    "step2_optimize/ilp.py",
    "step2_optimize/vrp.py",
    "step3_map/main.py",
    "step4_metrics/imbalance.py",
})

# 'API 수집 생략'(`--skip-api`)이 걷어 내는 단계. run_pipeline.STAGES의
# fetch + api와 같아야 한다 — 시험이 대조한다.
API_STAGE = frozenset({
    "step0_collect/tashu_api.py",
    "step0_collect/extract_parking_lot.py",
    "step0_collect/api_to_info.py",
})

# 로그 끝의 단계별 소요 표를 읽는다. "1.5초" 와 "5분 58초" 두 꼴이 모두 나온다.
_TIMING_RE = re.compile(r"^\s*(?:(\d+)분\s+)?([\d.]+)초\s\s+(\S+)\s*$")


def _script_key(text: str) -> str:
    r"""로그의 경로 표기를 `단계폴더/파일명`으로 줄인다.

    같은 실행기라도 로그마다 `step3_map\main.py`와
    `pipeline\step3_map\main.py` 두 꼴이 섞여 있다(8월분과 9월분). 그리고
    `main.py`만으로는 step3를 집을 수 없다 — 폴더까지 있어야 한다.
    """
    parts = [p for p in re.split(r"[\\/]", text.strip()) if p]
    return "/".join(parts[-2:])


def step_timings(log_text: str) -> Dict[str, float]:
    """로그 끝의 "=== 단계별 소요 시간 ===" 표를 {단계: 초}로 읽는다.

    ⚠️ **'합계' 줄에서 멈춘다.** 그 줄도 "40.1초  (11단계, ..." 꼴이라
    정규식에 걸려서, 안 끊으면 `(11단계,`라는 단계가 하나 생긴다.
    """
    found: Dict[str, float] = {}
    started = False
    for line in log_text.splitlines():
        if "단계별 소요 시간" in line:
            started = True
            continue
        if not started:
            continue
        if line.strip().startswith("합계"):
            break
        match = _TIMING_RE.match(line)
        if not match:
            if found and not line.strip():
                break
            continue
        minutes, seconds, script = match.groups()
        found[_script_key(script)] = int(minutes or 0) * 60 + float(seconds)
    return found


def run_shape(job: "Job") -> Optional[Dict[str, float]]:
    """한 실행을 **고정 비용과 시간대당 비용으로** 가른다. 못 읽으면 None.

    돌려주는 값은 초 단위 셋이다.
      · `수집`     — 'API 수집 생략'으로 빠지는 부분
      · `전처리`   — 늘 드는 나머지 고정 부분(순수요 계산 등)
      · `시간대당` — 시간대 하나를 더 고를 때마다 늘어나는 몫
    """
    durations = job_durations(job)
    if not durations:
        return None
    try:
        text = job.log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    timings = step_timings(text)
    if not timings:
        return None

    scaled = sum(v for k, v in timings.items() if k in DURATION_SCALED)
    if not scaled:          # 되풀이 단계가 하나도 안 보이면 표를 잘못 읽은 것이다
        return None
    return {
        "수집": sum(v for k, v in timings.items() if k in API_STAGE),
        "전처리": sum(v for k, v in timings.items()
                   if k not in API_STAGE and k not in DURATION_SCALED),
        "시간대당": scaled / len(durations),
    }


def job_durations(job: "Job") -> List[str]:
    """그 실행이 고른 시간대 목록. 인자에 없으면 빈 목록."""
    args = list(job.args or ())
    if "--duration" not in args:
        return []
    value = args[args.index("--duration") + 1] if len(args) > args.index("--duration") + 1 else ""
    return [item.strip() for item in value.split(",") if item.strip()]


def _median(values: List[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


# 계수를 뽑을 때 볼 실행 수. **3은 작지만 일부러 작다** — 아래 docstring 참고.
ESTIMATE_WINDOW = 3

# 계수 캐시. 열쇠는 **이력의 (id, 상태) 목록**이라 작업이 하나 끝나거나 새로
# 뜨면 저절로 어긋나 다시 센다 — 시각으로 만료시키면 끝난 직후 몇 분간 옛
# 계수를 보여 준다. 화면 하나가 이 함수를 두세 번 부르고(첫 화면 3번, 실행
# 폼 2번) 한 번마다 로그 최대 3개(건당 약 360KB)를 읽고 있었다(1.26.262).
_ESTIMATE_CACHE: Dict[str, object] = {"key": None, "value": None}


def estimate_model(limit: int = ESTIMATE_WINDOW) -> Optional[Dict[str, float]]:
    """최근 성공한 실행에서 뽑은 예상 계수. 기록이 없으면 None.

    🔴 **창이 좁은 데는 이유가 있다(기본 3건).** 예전 `typical_elapsed()`는 20건을
    봤는데, 이 저장소에서 20건은 **코드가 달라진 시대까지 거슬러 올라간다.**
    군집 단계 하나가 이렇게 움직였다(같은 시간대·같은 설정, 실측):

        08-25  5분 58초  →  08-26  1분 33초  →  08-27  1분 10초
             →  09-13  36.7초  →  09-14  43.1초

    한 방향으로 줄곧 빨라진 것이라 **잡음이 아니라 치우침**이다. 옛것을 섞으면
    중앙값이 늘 실제보다 크게 나온다 — 20건 중앙값이 *"보통 2.8분"* 이라고 적게
    했는데 같은 날 실측은 1분 54초였다. 넓은 창은 표본을 늘리는 것이 아니라
    **낡은 사실**을 섞는다.

    ⚠️ 그렇다고 1건으로 줄이면 중간에 멈췄다 이어 돌린 한 건이 그대로 예상이
    된다. 3은 **이상치 하나를 중앙값이 걸러 낼 수 있는 가장 좁은 창**이다.
    평균이 아니라 중앙값인 이유도 같다.

    ⚠️ '수집'은 **그 단계를 실제로 돈 실행만** 세어 중앙값을 낸다. `--skip-api`로
    돌린 실행은 수집이 0이라, 섞으면 **'API 수집 생략'을 안 켠 사람의 예상까지
    0으로 끌어내린다.**
    """
    history = list_jobs()
    key = (limit, tuple((job.id, job.status) for job in history))
    if _ESTIMATE_CACHE["key"] == key:
        return _ESTIMATE_CACHE["value"]

    shapes = []
    for job in history:
        if job.status != "success":
            continue
        if (shape := run_shape(job)) is not None:
            shapes.append(shape)
        if len(shapes) >= limit:
            break

    model = None
    if shapes:
        수집표본 = [s["수집"] for s in shapes if s["수집"] > 0]
        model = {
            "수집": _median(수집표본) if 수집표본 else 0.0,
            "전처리": _median([s["전처리"] for s in shapes]),
            "시간대당": _median([s["시간대당"] for s in shapes]),
            "표본": len(shapes),
        }
    _ESTIMATE_CACHE.update(key=key, value=model)
    return model


def reset_estimate_cache() -> None:
    """테스트가 쓴다 — 같은 이력 목록으로 로그만 바꿔 다시 잴 때."""
    _ESTIMATE_CACHE.update(key=None, value=None)


def estimate_seconds(model: Optional[Dict[str, float]], durations: int,
                     skip_api: bool = False) -> Optional[float]:
    """고른 시간대 수로 예상 초를 낸다. 계수가 없으면 None.

    `고정 + 시간대당 x 개수`다. 2026-09-14 실측으로 검증했다 — 계수(고정 13초 +
    시간대당 28초)로 시간대 둘을 예측하면 69초인데 실제도 69초였다.

    ⚠️ **EDA를 켠 실행은 기록이 없다.** 그래서 그 몫은 더하지 않는다 — 모르는
    것을 숫자로 지어내는 대신 화면이 '더 걸린다'고 말한다.
    """
    if not model or durations <= 0:
        return None
    고정 = model["전처리"] + (0.0 if skip_api else model["수집"])
    return 고정 + model["시간대당"] * durations


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


def read_log_tail(job: Job, max_lines: int = 300, text: Optional[str] = None) -> str:
    """로그 파일의 마지막 max_lines 줄을 반환한다.

    ⚠️ **'아직'과 '이제 없다'를 가른다** (1.26.149). 예전에는 파일이 없으면
    무조건 *"로그가 아직 없습니다"* 라고 답했다. 그래서 9일 전에 **성공으로
    끝난** 실행이 오지 않을 것을 기다리라고 말하고 있었다(실측: 이력 15건 중
    11건의 로그가 이미 없었다). `interrupted` 상태에서는 화면이 *"실제로
    끝까지 돌았는지는 아래 로그로 판단하세요"* 라고 안내하므로 더 나쁘다 —
    없는 증거를 보라고 시키는 셈이다.

    `text`를 주면 그것을 자른다 — 진행 화면이 단계 표시용으로 `read_log()`를
    이미 읽어 두었는데 같은 파일(약 360KB)을 3초마다 두 번 열고 있었다(1.26.262).
    """
    if text is None:
        if not job.log_path.exists():
            return _missing_log_note(job)
        try:
            text = job.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError as err:
            return f"(로그를 읽을 수 없습니다: {err})"
    elif not text and not job.log_path.exists():
        return _missing_log_note(job)
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = [f"... (앞 {len(lines) - max_lines}줄 생략) ..."] + lines[-max_lines:]
    return "\n".join(lines)


def _missing_log_note(job: Job) -> str:
    if job.is_running:
        return "(로그가 아직 없습니다)"
    return "(로그가 남아 있지 않습니다 — 오래된 실행이라 정리되었습니다)"


# 작업이 끝났을 때 부를 함수들. 화면 쪽 캐시(순수요 히트맵 등)를 비우는 데
# 쓴다 — 이 모듈이 화면 모듈을 import하면 층이 거꾸로 되므로 등록을 받는다.
_finished_hooks: List = []


def add_finished_hook(hook) -> None:
    """작업이 끝날 때(성공·실패·중단 모두) `hook(job)`을 부른다."""
    _finished_hooks.append(hook)


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
    # 락 밖에서 — 훅이 무엇을 하든 다른 요청을 막을 이유가 없다.
    for hook in list(_finished_hooks):
        try:
            hook(job)
        except Exception as err:                      # noqa: BLE001
            print(f"[경고] 작업 완료 훅 실패: {type(err).__name__}: {err}")


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
