"""로컬에서 세운 계획 한 회차(실행 라벨)를 클라우드 서버(VM)로 보낸다 (docs/구현/CLOUD_DEPLOY.md).

계획을 세우는 무거운 일은 로컬이 하고, 서버는 받은 계획을 보여 주고 지금 재고와 견줄 뿐이다.
DB 전체(3.5GB)를 다시 올리지 않고 **그 라벨의 표만**(`tools/transfer_run.py`의 `RUN_TABLES`, 수 MB)
내보내 올린 뒤, VM의 컨테이너 안에서 같은 도구로 `--import --overwrite` 한다.

    python tools/push_plan.py --list                    # 보낼 수 있는 라벨
    python tools/push_plan.py "2026-11-07 19"           # 한 회차 보내기
    python tools/push_plan.py                           # 가장 최근 계획 라벨
    python tools/push_plan.py "2026-11-07 19" --dry-run # 명령만 보여 준다

## 알아 둘 것

- **VM이 켜져 있어야 한다.** 꺼져 있으면 알리고 멈춘다(`--start`를 주면 켠다. 켜 두면 과금된다).
- VM은 **이름**(`pbr-web`)으로 찾는다 — 외부 IP는 쓰지 않으니 IP가 바뀌어도 상관없다.
- 같은 라벨이 서버에 이미 있으면 덮어쓴다(`--overwrite`). 서버는 정본이 아니라 사본이다.
- 보내는 것은 DB의 표뿐이다. 지도 HTML · CSV(`pp_data`)는 따라가지 않아 `/maps`는 옛 것일 수 있다.
- 서버 웹은 열람 전용이라 이 길 말고는 서버에 계획을 넣는 방법이 없다(웹의 `POST`는 막혀 있다).
- 이 PC 사용자 이름이 한글이면 일부 도구에서 gcloud가 IAP 터널로 물러설 때 실패한다. VM이 켜져 있고
  외부 IP가 있으면 일어나지 않는다.
"""
from __future__ import annotations

import argparse
import gc
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db

DEFAULT_INSTANCE = os.environ.get("PBR_VM_NAME", "pbr-web")
DEFAULT_ZONE = os.environ.get("PBR_VM_ZONE", "asia-northeast3-a")
CONTAINER = "pbr-web"                  # vm_setup.sh가 띄우는 대시보드 컨테이너 이름
REMOTE_DATA_DIR = "/opt/pbr-data"      # 컨테이너의 /app/data로 마운트되는 VM의 폴더


def find_gcloud() -> Optional[str]:
    """gcloud 실행 파일. PATH에 없으면 Windows 기본 설치 위치를 본다."""
    found = shutil.which("gcloud") or shutil.which("gcloud.cmd")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidate = Path(local) / "Google" / "Cloud SDK" / "google-cloud-sdk" / "bin" / "gcloud.cmd"
        if candidate.is_file():
            return str(candidate)
    return None


def safe_name(label: str) -> str:
    """라벨(공백 · 콜론이 든다)을 파일 이름으로. 원격 셸에 그대로 들어가므로 영숫자 · `_-.`만 남긴다."""
    cleaned = "".join(ch if (ch.isascii() and (ch.isalnum() or ch in "-_.")) else "_" for ch in label)
    return f"push_{cleaned.strip('_') or 'run'}.db"


def remote_import_command(file_name: str) -> str:
    """VM 안에서 돌릴 한 줄 — 올라온 파일을 데이터 폴더로 옮기고, 컨테이너 안에서 합치고, 지운다.

    `file_name`은 `safe_name()`이 만든 값만 온다(따옴표 · 공백 · 셸 문자가 없다)."""
    target = f"{REMOTE_DATA_DIR}/{file_name}"
    return (f"sudo mv {file_name} {target}"
            f" && sudo docker exec {CONTAINER} python tools/transfer_run.py"
            f" --import /app/data/{file_name} --overwrite"
            f" ; status=$?; sudo rm -f {target}; exit $status")


def build_commands(gcloud: str, local_file: Path, instance: str, zone: str) -> List[List[str]]:
    """[올리기, 합치기] 두 명령(인자 목록). 실행은 부르는 쪽이 한다 — 시험하려고 나눴다."""
    name = local_file.name
    return [
        [gcloud, "compute", "scp", f"--zone={zone}", str(local_file), f"{instance}:{name}"],
        [gcloud, "compute", "ssh", instance, f"--zone={zone}", f"--command={remote_import_command(name)}"],
    ]


def latest_plan_label(conn: sqlite3.Connection) -> Optional[str]:
    """가장 최근에 만든 계획 실행(실험 · 탐침 제외)의 라벨."""
    row = conn.execute(
        "SELECT run_label FROM runs WHERE kind IS NULL OR kind = 'plan' "
        "ORDER BY created_at DESC, run_label DESC LIMIT 1").fetchone()
    return row[0] if row else None


def vm_status(gcloud: str, instance: str, zone: str) -> str:
    result = subprocess.run(
        [gcloud, "compute", "instances", "describe", instance, f"--zone={zone}",
         "--format=get(status)"], capture_output=True, text=True)
    return (result.stdout or "").strip() or "UNKNOWN"


def push(label: str, instance: str = DEFAULT_INSTANCE, zone: str = DEFAULT_ZONE,
         start: bool = False, dry_run: bool = False) -> int:
    """계획 한 회차를 서버로 보낸다. 성공하면 0."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "transfer_run", Path(__file__).resolve().parent / "transfer_run.py")
    transfer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(transfer)

    gcloud = find_gcloud()
    if not gcloud:
        print("gcloud를 찾지 못했습니다. Google Cloud SDK를 설치하고 로그인하세요.", file=sys.stderr)
        return 1

    # export_run()이 연 SQLite 연결을 닫지 않아(with는 커밋만 한다) Windows에서 임시 폴더가 안 지워진다 —
    # 수거를 부르고, 그래도 잠겨 있으면 지우기 실패는 넘긴다.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        local_file = Path(tmp) / safe_name(label)
        with db.session() as conn:
            counts = transfer.export_run(conn, label, local_file)
        gc.collect()
        total = sum(counts.values())
        size_kb = local_file.stat().st_size / 1024
        print(f"'{label}' → {local_file.name}  {total:,}행 · {size_kb:,.0f}KB")

        commands = build_commands(gcloud, local_file, instance, zone)
        if dry_run:
            for command in commands:
                print("  (실행 안 함)", " ".join(command))
            return 0

        status = vm_status(gcloud, instance, zone)
        if status != "RUNNING":
            if not start:
                print(f"VM '{instance}'이 {status} 상태입니다. 켜지 않고 멈춥니다.\n"
                      f"  켜려면: gcloud compute instances start {instance} --zone={zone}\n"
                      f"  또는 --start를 주세요(켜 두면 과금됩니다). 켠 직후에는 SSH가 뜰 때까지 1분쯤 걸립니다.",
                      file=sys.stderr)
                return 1
            subprocess.run([gcloud, "compute", "instances", "start", instance, f"--zone={zone}"], check=False)
            print("VM을 켰습니다. SSH가 뜰 때까지 기다린 뒤 다시 시도하세요(약 1분).")
            return 1

        for step, command in zip(("올리기", "서버에 합치기"), commands):
            print(f"[{step}]")
            if subprocess.run(command).returncode != 0:
                print(f"{step}에 실패했습니다. 서버의 계획은 바뀌지 않았거나 일부만 바뀌었을 수 있습니다.",
                      file=sys.stderr)
                return 1
    print(f"서버에 '{label}'을 보냈습니다. 접속해서 /orders 에서 확인하세요.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="계획 한 회차를 클라우드 서버로 보낸다")
    parser.add_argument("label", nargs="?", help="보낼 실행 라벨(생략하면 가장 최근 계획)")
    parser.add_argument("--list", action="store_true", help="보낼 수 있는 라벨을 보여 준다")
    parser.add_argument("--instance", default=DEFAULT_INSTANCE, help="VM 이름 (기본: pbr-web)")
    parser.add_argument("--zone", default=DEFAULT_ZONE, help="VM 영역 (기본: asia-northeast3-a)")
    parser.add_argument("--start", action="store_true", help="VM이 꺼져 있으면 켠다(과금)")
    parser.add_argument("--dry-run", action="store_true", help="내보내기만 하고 명령을 보여 준다")
    args = parser.parse_args()

    with db.session() as conn:
        if args.list:
            rows = conn.execute(
                "SELECT run_label, duration, created_at FROM runs "
                "WHERE kind IS NULL OR kind = 'plan' ORDER BY created_at DESC LIMIT 20").fetchall()
            for label, duration, created in rows:
                print(f"{label}\t{duration or ''}\t{created}")
            return 0
        label = args.label or latest_plan_label(conn)
    if not label:
        print("보낼 계획이 없습니다.", file=sys.stderr)
        return 1
    return push(label, args.instance, args.zone, args.start, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
