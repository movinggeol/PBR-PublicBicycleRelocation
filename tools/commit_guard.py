"""커밋에 **내가 만들지 않은 변경**이 섞이는 것을 막는다 (1.26.155).

여러 세션이 같은 작업 트리·같은 `.git/index`를 공유해서 생긴 문제다.
2026-09-08에 실제로 이렇게 깨졌다:

    ① 파일을 이름으로 콕 집어 스테이징한다  (규약대로)
    ② `git diff --cached`로 확인한다        → 내 파일만 있다
    ③ `git commit` 한다
    ④ 실제로 들어간 것: 남의 파일 8개가 섞이고 내 파일 4개가 빠졌다

**②와 ③ 사이의 창**에서 다른 세션이 인덱스를 갈아 끼운 것이다. 사람이
확인을 아무리 잘해도 못 막는다 — 확인은 그 순간의 스냅샷이고 인덱스는
공유 자원이다. 그래서 **커밋 안쪽에서** 판정해야 한다.

이 도구는 `pre-commit` 훅으로 불린다. 훅은 인덱스가 확정된 뒤 커밋이
만들어지기 **직전에** 돌기 때문에, 여기서 본 것이 곧 커밋될 것이다.

## 무엇을 막나

**의도를 적어 둔 커밋만** 통과시킨다. `.git/PBR_COMMIT_FILES`에 이번
커밋에 넣을 파일을 적어 두면, 스테이징된 것이 그 목록과 정확히 같을
때만 커밋된다. 목록이 없으면 아무것도 막지 않는다 — 사람이 손으로 하는
커밋은 지금처럼 그대로 된다.

    python tools/commit_guard.py --intend a.py b.md   # 넣을 것을 적는다
    git add a.py b.md
    git commit -m "..."                               # 훅이 대조한다

어긋나면 커밋을 **거부하고 무엇이 다른지 말한다**. 남의 파일이 섞였으면
빼라고, 내 파일이 빠졌으면 다시 add하라고 알려 준다.

⚠️ **목록은 커밋이 끝나면 지운다**(`--clear`, 훅이 통과 시 자동으로 한다).
남겨 두면 다음 커밋이 엉뚱한 목록에 걸린다.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# ⚠️ 훅으로 불릴 때는 콘솔이 CP949라 한글 안내가 통째로 깨진다(실제로 겪었다 —
# "커밋을 막았습니다"가 `Ŀ���� ���ҽ��ϴ�`로 나와 읽을 수 없었다).
# **막는 것보다 왜 막혔는지 읽히는 것이 중요하므로** 출력 스트림을 UTF-8로
# 다시 연다. 콘솔이 못 받으면 글자를 잃더라도 죽지는 않게 `replace`를 쓴다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # 파이프로 묶였거나 재설정 불가
        pass

# 목록은 `.git/` 안에 둔다 — 저장소에 커밋되지 않고, worktree마다 따로 산다.
INTENT_NAME = "PBR_COMMIT_FILES"


def _git(*args: str) -> str:
    """git을 부르고 **UTF-8로** 해독한다.

    ⚠️ `text=True`만 쓰면 윈도우에서 콘솔 코드페이지(CP949)로 해독한다.
    저장소 경로에 한글이 있어 `졸작(4-1,4-2)`가 `議몄옉(4-1,4-2)`로 깨졌고,
    그 엉뚱한 경로에 훅을 만들어 놓고 성공했다고 말했다(실제로 겪었다).
    git은 경로를 UTF-8로 내보내므로 여기서 못박는다.
    """
    # ⚠️ `core.quotepath=false`가 없으면 git이 한글 경로를 `"docs/\352..."` 로
    # 따옴표·8진수로 내놓는다. 이 저장소는 경로가 거의 다 한글이라, 그대로
    # 비교하면 **모든 파일이 목록과 다르다**고 나온다.
    out = subprocess.run(["git", "-c", "core.quotepath=false", *args],
                         capture_output=True)
    if out.returncode != 0:
        raise SystemExit(f"git {args[0]} 실패: "
                         f"{out.stderr.decode('utf-8', 'replace').strip()}")
    return out.stdout.decode("utf-8").strip()


def git_dir() -> Path:
    return Path(_git("rev-parse", "--absolute-git-dir"))


def intent_path() -> Path:
    return git_dir() / INTENT_NAME


def staged_files() -> list[str]:
    """스테이징된 경로. **훅 안에서 부르면 곧 커밋될 것과 같다.**"""
    out = _git("diff", "--cached", "--name-only", "--diff-filter=ACMRD")
    return sorted(p for p in out.splitlines() if p.strip())


def read_intent() -> list[str] | None:
    path = intent_path()
    if not path.exists():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    return sorted(p.strip() for p in lines if p.strip())


def cmd_intend(files: list[str]) -> int:
    if not files:
        raise SystemExit("넣을 파일을 하나 이상 적으십시오.")
    intent_path().write_text("\n".join(sorted(files)) + "\n", encoding="utf-8")
    print(f"이번 커밋에 넣을 것 {len(files)}개를 적어 뒀습니다.")
    for f in sorted(files):
        print(f"  · {f}")
    print("\n이제 add 하고 commit 하십시오. 다르면 훅이 막습니다.")
    return 0


def cmd_clear() -> int:
    path = intent_path()
    if path.exists():
        path.unlink()
        print("커밋 의도 목록을 지웠습니다.")
    return 0


def cmd_check() -> int:
    """훅이 부르는 판정. 목록이 없으면 **아무것도 막지 않는다.**"""
    intent = read_intent()
    if intent is None:
        return 0

    staged = staged_files()
    extra = [p for p in staged if p not in intent]     # 남의 것이 섞였다
    missing = [p for p in intent if p not in staged]   # 내 것이 빠졌다

    if not extra and not missing:
        # 통과했으니 목록을 치운다 — 남겨 두면 다음 커밋이 걸린다.
        intent_path().unlink(missing_ok=True)
        return 0

    print("", file=sys.stderr)
    print("커밋을 막았습니다 — 스테이징된 것이 적어 둔 것과 다릅니다.",
          file=sys.stderr)
    print("여러 세션이 같은 인덱스를 쓰면 확인한 뒤에도 바뀝니다"
          " (docs/구현/두_PC_작업.md).", file=sys.stderr)

    if extra:
        print(f"\n  ⚠️ 넣기로 한 적 없는 것 {len(extra)}개 "
              f"— 다른 세션 것일 수 있습니다:", file=sys.stderr)
        for p in extra:
            print(f"     + {p}", file=sys.stderr)
        print("\n     빼려면:  git restore --staged " +
              " ".join(f'"{p}"' for p in extra[:4]) +
              (" ..." if len(extra) > 4 else ""), file=sys.stderr)

    if missing:
        print(f"\n  ⚠️ 넣기로 했는데 빠진 것 {len(missing)}개 "
              f"— 다른 세션이 가져갔을 수 있습니다:", file=sys.stderr)
        for p in missing:
            print(f"     - {p}", file=sys.stderr)
        print("\n     되넣으려면:  git add " +
              " ".join(f'"{p}"' for p in missing[:4]) +
              (" ..." if len(missing) > 4 else ""), file=sys.stderr)

    print("\n  목록을 지금 것으로 고치려면: "
          "python tools/commit_guard.py --intend <파일들>", file=sys.stderr)
    print("  이번만 그냥 넘기려면:        "
          "python tools/commit_guard.py --clear\n", file=sys.stderr)
    return 1


def cmd_install() -> int:
    """`pre-commit` 훅을 놓는다. 이미 있으면 **덮어쓰지 않는다.**"""
    hook = git_dir() / "hooks" / "pre-commit"
    # ⚠️ 경로를 **절대 경로로 박는다.** 상대 경로(`tools/commit_guard.py`)로
    # 두면 저장소 뿌리에서 커밋할 때만 돌고, 하위 폴더에서 `git commit` 하면
    # `No such file` 로 죽어 **커밋이 통째로 막힌다**(테스트가 잡았다).
    # 파이썬도 마찬가지 — 가상환경을 절대 경로로 찾고, 없으면 `python`으로
    # 물러선다(이 도구는 표준 라이브러리만 쓰므로 어느 쪽이든 돈다).
    root = Path(_git("rev-parse", "--show-toplevel"))
    venv = root / ".venv" / "Scripts" / "python.exe"
    body = (
        "#!/bin/sh\n"
        "# PBR 커밋 가드 (tools/commit_guard.py) — 세션이 인덱스를 공유해서\n"
        "# 생기는 섞임을 막는다. 의도 목록이 없으면 아무것도 하지 않는다.\n"
        f'PY="{venv.as_posix()}"\n'
        '[ -x "$PY" ] || PY="python"\n'
        f'"$PY" "{(root / "tools" / "commit_guard.py").as_posix()}" '
        "--check || exit 1\n"
    )
    if hook.exists():
        if "commit_guard" in hook.read_text(encoding="utf-8", errors="replace"):
            print(f"이미 설치돼 있습니다: {hook}")
            return 0
        print(f"pre-commit 훅이 이미 있습니다 — 덮어쓰지 않았습니다: {hook}",
              file=sys.stderr)
        print("다음 줄을 직접 넣으십시오:\n"
              "  python tools/commit_guard.py --check || exit 1", file=sys.stderr)
        return 1
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(body, encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    print(f"설치했습니다: {hook}")
    print("이제 `--intend`로 적어 둔 커밋은 목록과 다르면 막힙니다.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="커밋에 남의 변경이 섞이는 것을 막는다 (세션이 인덱스를 공유한다)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--intend", nargs="+", metavar="파일",
                       help="이번 커밋에 넣을 파일을 적어 둔다")
    group.add_argument("--check", action="store_true",
                       help="스테이징된 것이 적어 둔 것과 같은지 본다 (훅이 부른다)")
    group.add_argument("--clear", action="store_true",
                       help="적어 둔 목록을 지운다")
    group.add_argument("--install", action="store_true",
                       help="pre-commit 훅을 놓는다")
    args = parser.parse_args()

    if args.intend:
        return cmd_intend(args.intend)
    if args.check:
        return cmd_check()
    if args.clear:
        return cmd_clear()
    return cmd_install()


if __name__ == "__main__":
    raise SystemExit(main())
