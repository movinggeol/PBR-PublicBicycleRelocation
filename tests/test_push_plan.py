"""계획 한 회차를 클라우드 서버로 보내는 도구 검증 (tools/push_plan.py, docs/구현/CLOUD_DEPLOY.md).

지키려는 규칙:
  1. 원격 셸에 들어가는 파일 이름에는 안전한 글자만 남는다(라벨에는 공백 · 콜론이 든다).
  2. VM은 IP가 아니라 **이름**으로 찾는다 — 켜고 끌 때마다 IP가 바뀌어도 그대로 돈다.
  3. 실험 · 탐침이 아니라 **계획** 실행만 '가장 최근'으로 고른다.
  4. 완료 훅은 꺼져 있는 것이 기본이고, 열람 전용 서버 · 실패한 작업에서는 보내지 않는다.
"""
import importlib.util
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_tool():
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    spec = importlib.util.spec_from_file_location("push_plan", PROJECT_ROOT / "tools" / "push_plan.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tool():
    return load_tool()


def test_파일_이름에는_안전한_글자만_남는다(tool):
    name = tool.safe_name("2026-11-07 19 휴일; rm -rf /")
    assert name.startswith("push_") and name.endswith(".db")
    assert all(ch.isascii() and (ch.isalnum() or ch in "-_.") for ch in name)
    assert tool.safe_name("한글만") == "push_run.db"


def test_원격_명령은_옮기고_합치고_지운다(tool):
    command = tool.remote_import_command("push_x.db")
    assert "sudo mv push_x.db /opt/pbr-data/push_x.db" in command
    assert "docker exec pbr-web python tools/transfer_run.py --import /app/data/push_x.db --overwrite" in command
    assert "rm -f /opt/pbr-data/push_x.db" in command
    assert "exit $status" in command            # 합치기가 실패하면 실패로 돌려준다


def test_VM은_이름으로_찾는다_IP를_쓰지_않는다(tool):
    scp, ssh = tool.build_commands("gcloud", Path("C:/t/push_x.db"), "pbr-web", "asia-northeast3-a")
    assert scp[:3] == ["gcloud", "compute", "scp"] and scp[-1] == "pbr-web:push_x.db"
    assert ssh[:4] == ["gcloud", "compute", "ssh", "pbr-web"]
    assert not any(part.count(".") == 3 and part.replace(".", "").isdigit() for part in scp + ssh)


def test_가장_최근_계획은_실험을_건너뛴다(tool):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE runs (run_label TEXT, kind TEXT, created_at TEXT)")
    conn.executemany("INSERT INTO runs VALUES (?,?,?)", [
        ("계획 옛것", "plan", "2026-11-01 10:00"),
        ("계획 새것", None, "2026-11-05 10:00"),        # kind가 없는 옛 기록도 계획으로 본다
        ("실험", "experiment", "2026-11-07 10:00"),
        ("탐침", "probe", "2026-11-07 11:00"),
    ])
    assert tool.latest_plan_label(conn) == "계획 새것"
    conn.execute("DELETE FROM runs")
    assert tool.latest_plan_label(conn) is None


def _job(status="success", label="2026-11-07 19"):
    from webapp import jobs
    return jobs.Job(id="t", args=["--now", label] if label else [], status=status)


def _run_hook(monkeypatch, env, job, readonly=False):
    from webapp import app as app_module
    tool = load_tool()
    sent = []
    monkeypatch.setattr(tool, "push", lambda label, *a, **k: sent.append(label) or 0)
    monkeypatch.setitem(sys.modules, "tools.push_plan", tool)
    monkeypatch.setattr("tools.push_plan", tool, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    if readonly:
        monkeypatch.setenv("PBR_READONLY", "1")
    else:
        monkeypatch.delenv("PBR_READONLY", raising=False)
    app_module._push_plan_after_run(job)
    for thread in threading.enumerate():
        if thread.name == "push-plan":
            thread.join(5)
    return sent


def test_완료_훅은_켜졌고_성공했을_때만_보낸다(monkeypatch):
    on = {"PBR_PUSH_AFTER_RUN": "1"}
    assert _run_hook(monkeypatch, on, _job()) == ["2026-11-07 19"]
    assert _run_hook(monkeypatch, {"PBR_PUSH_AFTER_RUN": "0"}, _job()) == []        # 기본은 꺼짐
    assert _run_hook(monkeypatch, on, _job(status="failed")) == []
    assert _run_hook(monkeypatch, on, _job(label="")) == []                          # 라벨을 모르면 짐작하지 않는다
    assert _run_hook(monkeypatch, on, _job(), readonly=True) == []                   # 서버는 보내는 쪽이 아니다
