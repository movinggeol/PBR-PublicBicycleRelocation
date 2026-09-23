"""회차가 **시작하는 시각**마다 계획을 세워, 복원의 출발 재고를 그날의 실제 상태로 맞춘다 (1.26.278).

왜 필요한가 — 복원 결품은 **계획을 세운 시각의 재고**에서 출발한다. 지금까지 비교에
쓴 계획은 모두 오후 3시~4시 반에 세운 것이라, 새벽 회차를 오후 재고로 복원하고
있었다. 휴일 `_05_10` 작업 대상의 빈 곳이 출발점에서는 45.7%인데 실제 휴일 05시에는
22.0%였다(EXPERIMENTS 32장). 그래서 관측과 복원의 차이에서 **출발 재고의 몫**을 뗄 수
없었고, 휴일 `_05_10`의 −35%가 무엇 때문인지 가르지 못했다.

회차 시작 시각에 계획을 세우면 출발 재고가 **그날 그 시각의 실제 상태**가 되고, 그날의
관측과 맞대면 시각도 날도 같다. 맞대는 쪽은
`experiments/structure/observed_stockout.py --same-day`다.

**한 번 실행 = 한 회차.** 작업 스케줄러가 회차마다 한 번씩 부른다.

    python tools/sameday_plan.py install --dates 2026-09-24 2026-09-25 2026-09-26 2026-09-27
    python tools/sameday_plan.py status
    python tools/sameday_plan.py uninstall
    python tools/sameday_plan.py run --duration _05_10      # 스케줄러가 부르는 것

🔴 **스케줄러에는 한글을 넘기지 않는다.** 작업 이름(`PBR-sameday-20260925-05`)과 인자는
ASCII이고, 라벨(`2026-09-25 05 휴일 동시각`)과 순수요 기간(`26년 03월`)은 여기서 만든다.
인코딩이 끼어들 자리를 아예 없앤다.

⚠️ **늦게 깨면 세우지 않는다.** 이 실험의 전제가 *"회차 시작 시각의 재고"* 이므로 시작
시각에서 `--tolerance-min`(기본 20분)을 넘기면 계획을 세우지 않고 로그만 남긴다.
`StartWhenAvailable`을 켠 것은 1~2분 늦은 기동을 살리려는 것이지 몇 시간 뒤를 살리려는
것이 아니다.

⚠️ **TMAP은 부르지 않는다**(`--skip-map`). 경로 지도가 필요 없고, 도로 수집은 집 PC가
같은 키로 돈다(docs/구현/두_PC_작업.md).

⚠️ 수집기의 정시 틱(:00)과 겹치지 않게 **시작 시각 3분 뒤**에 건다. DB는 WAL이고 수집
틱은 몇 초면 끝난다.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date, datetime, time as clock, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import project_config  # noqa: E402,F401  — 콘솔 인코딩을 먼저 맞춘다(— · 이모지)
from project_config import DATA_ROOT, duration_hours, is_holiday  # noqa: E402

TASK_PREFIX = "PBR-sameday-"
DURATIONS = ("_05_10", "_10_15", "_15_20", "_20_05")
# 순수요 기간 — 지금 DB에서 가장 최근 달이고, 2026-09-22 휴일 계획도 이 달로 세웠다.
PERIOD = "26년 03월"
OFFSET_MIN = 3
TOLERANCE_MIN = 20
LOG_DIR = DATA_ROOT / "logs" / "sameday"


def start_hour(duration: str) -> int:
    """회차가 시작하는 시(時). `_20_05`는 20이다."""
    return duration_hours(duration)[0]


def label_for(day: date, duration: str, day_type: str, tag: str = "") -> str:
    """`2026-09-25 05 휴일 동시각`. 라벨만 봐도 언제 세운 계획인지 안다."""
    kind = "휴일" if day_type == "holiday" else "평일"
    base = f"{day.isoformat()} {start_hour(duration):02d} {kind} 동시각"
    return f"{base} {tag}" if tag else base


def task_name(day: date, duration: str) -> str:
    return f"{TASK_PREFIX}{day:%Y%m%d}-{start_hour(duration):02d}"


def within_window(now: datetime, duration: str, tolerance_min: int) -> bool:
    """지금이 그 회차 시작 시각부터 `tolerance_min`분 안인가."""
    start = datetime.combine(now.date(), clock(start_hour(duration)))
    return start <= now <= start + timedelta(minutes=tolerance_min)


def pipeline_command(python: str, label: str, duration: str, day_type: str,
                     period: str, run_kind: str) -> list[str]:
    """계획 한 회차를 세우는 명령. 스냅샷은 라이브 API로 뜬다(`--skip-api`를 주지 않는다)."""
    return [python, str(ROOT / "run_pipeline.py"),
            "--day-type", day_type, "--period", period, "--now", label,
            "--duration", duration, "--run-kind", run_kind,
            "--skip-map", "--skip-eda"]


def console_python() -> str:
    """스케줄러는 `pythonw.exe`로 부른다. 파이프라인은 콘솔판 `python.exe`로 돌린다."""
    exe = Path(sys.executable)
    candidate = exe.with_name("python.exe")
    return str(candidate if candidate.exists() else exe)


# ------------------------------------------------------------------- run

def run(args: argparse.Namespace) -> int:
    now = datetime.now()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{now:%Y-%m-%d}_{start_hour(args.duration):02d}.log"

    def note(message: str) -> None:
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")
        print(message)

    if not within_window(now, args.duration, args.tolerance_min):
        note(f"건너뜀 — {args.duration} 시작 시각에서 {args.tolerance_min}분을 넘겼다"
             f"(지금 {now:%H:%M}). 출발 재고가 회차 시작의 상태가 아니게 되므로 세우지 않는다.")
        return 0
    if args.day_type == "holiday" and not is_holiday(now.date()):
        note(f"건너뜀 — {now.date()}은 휴일이 아니다. 휴일 계획을 평일 재고로 세우지 않는다.")
        return 0

    label = label_for(now.date(), args.duration, args.day_type, args.tag)
    command = pipeline_command(console_python(), label, args.duration, args.day_type,
                               args.period, args.run_kind)
    note(f"시작 — {label} · 기간 {args.period} · 종류 {args.run_kind}")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    with open(log_path, "a", encoding="utf-8") as log:
        proc = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                              env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    note(f"끝 — 종료 코드 {proc.returncode}")
    return proc.returncode


# ------------------------------------------------------- install · status · uninstall

def _powershell(script: str) -> subprocess.CompletedProcess:
    prefix = "[Console]::OutputEncoding = [Text.Encoding]::UTF8; $ErrorActionPreference = 'Stop'; "
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", prefix + script],
        capture_output=True, text=True, encoding="utf-8", errors="replace")


def install_script(entries: list[tuple[str, datetime, str]], day_type: str,
                   extra: str = "") -> str:
    """예약 작업을 거는 PowerShell 한 덩어리. 인자는 모두 ASCII다.

    `extra`는 `run`에 덧붙일 인자다 — 무인 가동 전에 같은 경로로 한 번 돌려 보는
    리허설(`--run-kind experiment --tag check` 등)에만 쓴다.
    """
    if not extra.isascii():
        raise ValueError("스케줄러에 넘기는 인자는 ASCII여야 한다")
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    script = ROOT / "tools" / "sameday_plan.py"
    lines = [
        "$s = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew "
        "-ExecutionTimeLimit (New-TimeSpan -Minutes 15) -StartWhenAvailable "
        "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries",
    ]
    for name, at, duration in entries:
        argument = f'"{script}" run --duration {duration} --day-type {day_type} {extra}'.rstrip()
        lines += [
            f"$a = New-ScheduledTaskAction -Execute '{pythonw}' -Argument '{argument}' "
            f"-WorkingDirectory '{ROOT}'",
            f"$t = New-ScheduledTaskTrigger -Once -At '{at:%Y-%m-%dT%H:%M:%S}'",
            f"Register-ScheduledTask -TaskName '{name}' -Action $a -Trigger $t -Settings $s "
            f"-Description 'PBR same-day plan (tools/sameday_plan.py)' -Force | Out-Null",
            f"'{name}'",
        ]
    return "; ".join(lines)


def install(args: argparse.Namespace) -> int:
    now = datetime.now()
    entries = []
    for text in args.dates:
        day = date.fromisoformat(text)
        if args.day_type == "holiday" and not is_holiday(day):
            print(f"  ⚠️ {day}은 휴일이 아니다 — 건너뛴다")
            continue
        for duration in args.durations:
            at = datetime.combine(day, clock(start_hour(duration), OFFSET_MIN))
            if at <= now:
                print(f"  건너뜀 {day} {duration} — 이미 지난 시각({at:%H:%M})")
                continue
            entries.append((task_name(day, duration), at, duration))
    if not entries:
        print("걸 작업이 없습니다.")
        return 1
    result = _powershell(install_script(entries, args.day_type))
    if result.returncode != 0:
        print(result.stderr.strip())
        return result.returncode
    print(f"예약 작업 {len(entries)}개를 걸었습니다 — 시작 시각 {OFFSET_MIN}분 뒤, "
          f"늦어도 {TOLERANCE_MIN}분 안에만 세웁니다.")
    for name, at, duration in entries:
        print(f"  {name}  {at:%Y-%m-%d %H:%M}  {duration}")
    return 0


def status(_args: argparse.Namespace) -> int:
    result = _powershell(
        f"Get-ScheduledTask -TaskName '{TASK_PREFIX}*' -ErrorAction SilentlyContinue | "
        "ForEach-Object { $i = Get-ScheduledTaskInfo -TaskName $_.TaskName; "
        "'{0}|{1}|{2:yyyy-MM-dd HH:mm}|{3:yyyy-MM-dd HH:mm}|{4}' -f "
        "$_.TaskName, $_.State, $i.NextRunTime, $i.LastRunTime, $i.LastTaskResult }")
    rows = [line.split("|") for line in result.stdout.splitlines() if line.count("|") == 4]
    print(f"예약 작업 {len(rows)}개")
    for name, state, next_run, last_run, code in sorted(rows):
        ran = f"마지막 {last_run} · 결과 {code}" if not last_run.startswith("1999") else "아직 안 돎"
        print(f"  {name}  {state:8}  다음 {next_run or '-':16}  {ran}")

    import db
    with db.session() as conn:
        made = conn.execute(
            "SELECT run_label, created_at FROM runs WHERE run_label LIKE '% 동시각%'"
            " ORDER BY created_at").fetchall()
    print(f"\n세운 계획 {len(made)}건")
    for label, created in made:
        print(f"  {label}  (세운 시각 {created})")
    if LOG_DIR.exists():
        print(f"\n로그: {LOG_DIR}")
    return 0


def uninstall(_args: argparse.Namespace) -> int:
    result = _powershell(
        f"$n = @(Get-ScheduledTask -TaskName '{TASK_PREFIX}*' -ErrorAction SilentlyContinue); "
        "$n | Unregister-ScheduledTask -Confirm:$false; $n.Count")
    print(f"예약 작업 {result.stdout.strip() or 0}개를 지웠습니다. DB의 계획은 그대로 둡니다.")
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="지금 한 회차의 계획을 세운다(스케줄러가 부른다)")
    p_run.add_argument("--duration", required=True, choices=DURATIONS)
    p_run.add_argument("--day-type", default="holiday", choices=("holiday", "weekday"))
    p_run.add_argument("--period", default=PERIOD)
    p_run.add_argument("--run-kind", default="plan", choices=("plan", "experiment", "probe"))
    p_run.add_argument("--tolerance-min", type=int, default=TOLERANCE_MIN)
    p_run.add_argument("--tag", default="", help="라벨 끝에 붙일 말(점검용)")
    p_run.set_defaults(func=run)

    p_install = sub.add_parser("install", help="날짜마다 네 회차의 예약 작업을 건다")
    p_install.add_argument("--dates", nargs="+", required=True, help="YYYY-MM-DD ...")
    p_install.add_argument("--durations", nargs="+", default=list(DURATIONS), choices=DURATIONS)
    p_install.add_argument("--day-type", default="holiday", choices=("holiday", "weekday"))
    p_install.set_defaults(func=install)

    sub.add_parser("status", help="걸린 작업과 세운 계획을 본다").set_defaults(func=status)
    sub.add_parser("uninstall", help="걸린 작업을 모두 지운다").set_defaults(func=uninstall)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
