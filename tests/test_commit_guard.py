"""커밋 가드가 **실제로 막는지** 본다 (1.26.155).

⚠️ 이 테스트는 `--check`만 부르지 않는다. 진짜 물음은 *"판정 함수가 옳은
값을 내나"* 가 아니라 **"`git commit`이 실제로 멈추나"** 이기 때문이다.
그래서 임시 저장소를 만들어 훅을 놓고 **커밋을 시켜 본 뒤 HEAD가
안 움직였는지**로 판정한다. 판정 함수만 부르면, 훅이 설치되지 않았거나
종료 코드를 잃어버려도 테스트는 통과한다.

가드가 막으려는 것은 2026-09-08에 실제로 겪은 일이다 — 세션 둘이 같은
`.git/index`를 써서, 확인한 뒤 커밋하는 사이에 남의 파일 8개가 섞이고
내 파일 4개가 빠졌다.
"""
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[1] / "tools" / "commit_guard.py"


def _git(repo, *args, check=True):
    out = subprocess.run(["git", "-c", "core.quotepath=false", *args],
                         cwd=repo, capture_output=True)
    if check and out.returncode != 0:
        raise AssertionError(f"git {args}: "
                             f"{out.stderr.decode('utf-8', 'replace')}")
    return out


def _guard(repo, *args):
    return subprocess.run([sys.executable, str(GUARD), *args],
                          cwd=repo, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """훅이 설치된 임시 저장소. 한글 경로도 함께 둔다(실제 저장소가 그렇다)."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "내것.md").write_text("mine\n", encoding="utf-8")
    (tmp_path / "남의것.md").write_text("theirs\n", encoding="utf-8")
    _git(tmp_path, "add", "내것.md", "남의것.md")
    _git(tmp_path, "commit", "-qm", "첫 커밋")
    # 임시 저장소엔 `tools/`가 없으므로 **진짜 가드를 절대 경로로** 가리키는
    # 훅을 직접 놓는다. `--install`이 만드는 것과 같은 모양이다.
    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(
        "#!/bin/sh\n"
        f'"{Path(sys.executable).as_posix()}" "{GUARD.as_posix()}" '
        "--check || exit 1\n",
        encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    return tmp_path


def _head(repo):
    return _git(repo, "rev-parse", "HEAD").stdout.decode().strip()


def test_목록이_없으면_평소_커밋을_막지_않는다(repo):
    """**기본은 아무것도 안 막는 것이다.** 손으로 하는 커밋이 계속 돌아야 한다."""
    before = _head(repo)
    (repo / "내것.md").write_text("changed\n", encoding="utf-8")
    _git(repo, "add", "내것.md")
    assert _git(repo, "commit", "-qm", "평소 커밋", check=False).returncode == 0
    assert _head(repo) != before, "목록이 없는데 커밋이 막혔다"


def test_남의_파일이_섞이면_커밋이_실제로_멈춘다(repo):
    """2026-09-08에 겪은 그 상황 — 확인 뒤에 남의 파일이 인덱스에 들어왔다."""
    before = _head(repo)
    _guard(repo, "--intend", "내것.md")

    (repo / "내것.md").write_text("a\n", encoding="utf-8")
    (repo / "남의것.md").write_text("b\n", encoding="utf-8")   # 다른 세션이 넣었다
    _git(repo, "add", "내것.md", "남의것.md")

    done = _git(repo, "commit", "-m", "섞인 커밋", check=False)
    assert done.returncode != 0, "남의 파일이 섞였는데 커밋이 됐다"
    assert _head(repo) == before, "커밋이 만들어졌다"
    assert "남의것.md" in done.stderr.decode("utf-8", "replace")


def test_내_파일이_빠지면_커밋이_실제로_멈춘다(repo):
    """반대쪽 — 넣기로 한 것을 다른 세션이 가져가 버린 경우."""
    before = _head(repo)
    (repo / "내것.md").write_text("a\n", encoding="utf-8")
    (repo / "남의것.md").write_text("b\n", encoding="utf-8")
    _guard(repo, "--intend", "내것.md", "남의것.md")
    _git(repo, "add", "남의것.md")            # 내것.md가 빠졌다

    done = _git(repo, "commit", "-m", "빠진 커밋", check=False)
    assert done.returncode != 0, "파일이 빠졌는데 커밋이 됐다"
    assert _head(repo) == before
    assert "내것.md" in done.stderr.decode("utf-8", "replace")


def test_목록과_같으면_커밋이_된다(repo):
    """막기만 하고 통과를 안 시키면 쓸 수 없다."""
    before = _head(repo)
    (repo / "내것.md").write_text("a\n", encoding="utf-8")
    _guard(repo, "--intend", "내것.md")
    _git(repo, "add", "내것.md")

    assert _git(repo, "commit", "-qm", "맞는 커밋", check=False).returncode == 0
    assert _head(repo) != before, "목록과 같은데 막혔다"


def test_통과하면_목록을_지워_다음_커밋에_안_걸린다(repo):
    """목록이 남으면 **다음 커밋이 엉뚱하게 막힌다.** 통과 시 치워야 한다."""
    (repo / "내것.md").write_text("a\n", encoding="utf-8")
    _guard(repo, "--intend", "내것.md")
    _git(repo, "add", "내것.md")
    _git(repo, "commit", "-qm", "첫 번째")

    assert not (repo / ".git" / "PBR_COMMIT_FILES").exists()
    # 곧바로 다른 파일을 커밋해도 걸리지 않아야 한다.
    (repo / "남의것.md").write_text("b\n", encoding="utf-8")
    _git(repo, "add", "남의것.md")
    assert _git(repo, "commit", "-qm", "두 번째", check=False).returncode == 0


def test_한글_경로를_따옴표로_받아_전부_다르다고_하지_않는다(repo):
    """🔴 `core.quotepath` 기본값이면 git이 `"\\352\\265..."` 로 내놓는다.

    이 저장소는 경로가 거의 다 한글이라, 그대로 비교하면 **맞는 커밋도
    전부 막힌다** — 가드가 못 쓰게 된다.
    """
    before = _head(repo)
    (repo / "한글이름.md").write_text("x\n", encoding="utf-8")
    _guard(repo, "--intend", "한글이름.md")
    _git(repo, "add", "한글이름.md")

    assert _git(repo, "commit", "-qm", "한글 경로", check=False).returncode == 0
    assert _head(repo) != before, "한글 경로가 목록과 같은데 막혔다"


def test_경로를_직접_준_커밋도_판정한다(repo):
    """`git commit <파일>`은 인덱스를 우회하는 것처럼 보이지만 훅은 돈다."""
    before = _head(repo)
    (repo / "내것.md").write_text("a\n", encoding="utf-8")
    (repo / "남의것.md").write_text("b\n", encoding="utf-8")
    _guard(repo, "--intend", "내것.md")

    done = _git(repo, "commit", "-m", "경로 지정", "남의것.md", check=False)
    assert done.returncode != 0, "경로를 직접 주면 가드를 지나쳤다"
    assert _head(repo) == before
