"""문서와 코드가 같은 값을 말하는지 기계로 확인한다.

**왜 있는가.** 같은 사실이 여러 문서에 복사돼 있으면 한 번에 다 안 고쳐진다.
실제로 세 번 되살아났다 — `VEHICLES_PER_ROUND`(10 vs 21), 테스트 개수
(460 vs 548), OR-Tools 갭 값. 버전 번호는 네 번 겹쳤다(1.25.3 · 1.26.45 ·
1.26.68~74 일곱 개 · 1.25.9/47/48). 규칙은 이미 문서에 적혀 있었지만
지켜지지 않았다. **지켜지지 않는 규칙은 검사로 바꾸는 편이 낫다.**

    python tools/check_consistency.py           # 사람이 읽는 보고
    python tools/check_consistency.py --strict  # 어긋나면 exit 1 (CI용)
    python tools/check_consistency.py --only 값 # 한 검사만

**무엇을 검사하지 않는가.** 값이 *맞는지*는 보지 않는다 — 코드가 말하는 값과
문서가 말하는 값이 *같은지*만 본다. 코드가 틀렸으면 이 검사는 조용히
통과한다. 그건 실험과 사람의 몫이다.

자세한 것은 docs/구현/TESTING.md 7장.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

def _force_utf8_output() -> None:
    """윈도우에서 stdout이 파이프면 cp949라 '—' 한 글자에 죽는다.

    `project_config._force_utf8_output()`과 같은 방어인데, **이 검사기는 훅이나
    CI에서 단독으로 돌아야** 해서 의존성 없이 여기 다시 둔다. 실제로 결과를
    한 줄도 못 찍고 죽었다 — 1.19.0·1.19.3과 같은 자리다.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if getattr(stream, "encoding", "").lower() not in ("utf-8", "utf8"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_force_utf8_output()

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "docs" / "기록" / "버전관리.md"

# ── 검사에서 빼는 문서 ────────────────────────────────────────────────
# 지난 상태를 그대로 남기는 것이 목적인 문서다. 여기의 "문헌 11편"은
# 낡은 값이 아니라 그때의 기록이다 — 고치면 이력이 아니게 된다.
HISTORY_DOCS = {
    "docs/기록/버전관리.md",
    "docs/기록/메모.md",
    "docs/기록/ORIGINS.md",
    "docs/기록/두_PC_작업.md",
    "docs/기록/RETROSPECTIVE.md",  # "0개 → 548개" 같은 전후 대비를 싣는다
}

SKIP_DIRS = {".venv", ".git", "node_modules", "__pycache__", "site-packages"}


# ── 진실의 출처 ───────────────────────────────────────────────────────
def _config_value(name: str) -> str:
    """project_config.py에서 상수 하나를 읽는다(import 없이 텍스트로)."""
    src = (ROOT / "project_config.py").read_text(encoding="utf-8")
    direct = re.search(rf"^{name}\s*=\s*([0-9.]+)\s*$", src, re.M)
    if direct:
        return direct.group(1)
    env = re.search(
        rf'^{name}\s*=\s*\w+\(os\.getenv\([^,]+,\s*"([^"]+)"\)', src, re.M
    )
    if env:
        return env.group(1)
    alias = re.search(rf"^{name}\s*=\s*(\w+)\s*$", src, re.M)
    if alias:
        return _config_value(alias.group(1))
    raise LookupError(f"project_config.py에서 {name}을 찾지 못했습니다")


def _table_count() -> str:
    """db.py가 실제로 만드는 테이블의 가짓수."""
    src = (ROOT / "db.py").read_text(encoding="utf-8")
    # 뒤의 `\s*\(`가 있어야 한다 — 없으면 505행의 *산문 속* `CREATE TABLE IF
    # NOT EXISTS`까지 세어 테이블이 21개가 된다(이 검사기가 실제로 그랬다).
    names = re.findall(r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?(\w+)\s*\(", src)
    if not names:
        raise LookupError("db.py에서 CREATE TABLE을 찾지 못했습니다")
    return str(len(set(names)))


def _literature_count() -> str:
    """LITERATURE.md가 카드로 다루는 문헌 편수."""
    src = (ROOT / "docs" / "연구" / "LITERATURE.md").read_text(encoding="utf-8")
    # 3장 "문헌별 카드"의 `### 3-N.` 만 센다. 다른 절의 `### ①` 같은 것은
    # 문헌이 아니라 확보 경로 안내다.
    cards = re.findall(r"^###\s+3-\d+\.", src, re.M)
    if not cards:
        raise LookupError("LITERATURE.md에서 문헌 카드를 찾지 못했습니다")
    return str(len(cards))


_TEST_COUNTS: dict[str, str] = {}


def _pytest_counts() -> dict[str, str]:
    """pytest가 실제로 수집하는 테스트 개수와 파일 수."""
    if _TEST_COUNTS:
        return _TEST_COUNTS
    import pytest

    class _Collect:
        def __init__(self) -> None:
            self.items: list = []

        def pytest_collection_modifyitems(self, items):
            self.items = list(items)

    hook = _Collect()
    # -p no:cacheprovider — 검사가 .pytest_cache를 남기지 않게 한다.
    # 수집 목록(556줄)이 보고를 덮으므로 pytest의 출력은 버린다.
    # pytest.ini의 addopts(-v)까지 삼키려면 이 방법뿐이다.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
        io.StringIO()
    ):
        pytest.main(
            ["--collect-only", "-q", "-p", "no:cacheprovider", str(ROOT / "tests")],
            plugins=[hook],
        )
    if not hook.items:
        raise LookupError("pytest가 테스트를 하나도 수집하지 못했습니다")
    files = {str(getattr(item, "path", None) or item.fspath) for item in hook.items}
    _TEST_COUNTS["tests"] = str(len(hook.items))
    _TEST_COUNTS["files"] = str(len(files))
    return _TEST_COUNTS


@dataclass
class Fact:
    """여러 문서에 복사돼 있는 사실 하나."""

    name: str
    truth: object  # () -> str
    patterns: list[str]
    # 그 값을 '옛 상태' 또는 '부분집합'으로서 정당하게 싣는 줄.
    # 부분 문자열로 맞춘다 — 줄 번호로 잡으면 줄이 밀릴 때 깨진다.
    allow: list[str] = field(default_factory=list)
    # 이 값보다 작은 수는 '증분'으로 보고 넘긴다. *"테스트 3개 추가"* 같은
    # 서술이 문서 곳곳에 있는데, 전체 개수와 자릿수부터 다르다.
    min_value: int = 0


FACTS = [
    Fact(
        name="테스트 개수",
        truth=lambda: _pytest_counts()["tests"],
        # 좁게 잡는다. `전체 (\d+)개`처럼 열어 두면 *"전체 43개 클러스터"* 까지
        # 테스트로 세어, 검사기가 오탐만 늘어놓다 무시당한다.
        patterns=[
            r"테스트[는가]? (\d+)개(?! 추가)",  # "테스트 9개 추가"는 증분이다
            r"pytest (\d+)개",
            r"\*\*합계\*\*\s*\|\s*\*\*(\d+)\*\*",
            # ⚠️ 예전에는 `(\d+)개 통과 확인`이라 **"확인"이 붙은 것만** 잡았다.
            #    그래서 `pbr-run` 스킬의 *"607개 통과가 기준선이다"* 가 스위트가
            #    607 → 635로 자라는 동안 아무에게도 안 걸렸다. 검사기가 있는데도
            #    새는 자리가 있으면, 그 자리는 **검사되고 있다고 착각되는 만큼**
            #    더 오래 틀린 채 남는다(1.26.119).
            r"(\d+)개 통과",
            r"통과 \((\d+)개\)",
            r"(\d+)개[,·] 약 \d+초",
            r"전체 (\d+)개 \(약",
        ],
        allow=[
            # 커밋 문체 예시다 — *"이전 개수 → 이후 개수"* 형식을 보여 주는
            # 줄이라 지금 값으로 고치면 예시의 뜻이 사라진다.
            "417 -> 421개 통과",
        ],
        # 스위트가 세 자리다. 두 자리 이하는 *"테스트 5개(...)"* 처럼 그때
        # 늘린 몫을 적은 것이라 전체와 비교할 대상이 아니다.
        min_value=100,
    ),
    Fact(
        name="테스트 파일 수",
        truth=lambda: _pytest_counts()["files"],
        # `\((\d+)개 파일\)` 만으로는 `py_compile (27개 파일)`도 잡힌다
        patterns=[r"pytest \d+개\((\d+)개 파일\)", r"(\d+)개 파일 · 약"],
    ),
    Fact(
        name="DB 테이블 수",
        truth=_table_count,
        patterns=[r"테이블 (\d+)개", r"(\d+)개 테이블", r"표 (\d+)개"],
        allow=[
            # 파이프라인이 지나가는 테이블만 센 것 — 전체 수가 아니다
            "kpi_summary`까지",
            "kpi_summary까지",
            "`db.SCHEMA` 테이블 2개",
            # 2026-08-11 실행분을 떼어 옮긴 기록. 그때 그 라벨에 있던 수다
            "9,124행",
            "온전히 남아",
            "온전하며",
        ],
    ),
    Fact(
        name="문헌 편수",
        truth=_literature_count,
        # *"국내 문헌 10편"*, *"해외 유료 문헌 4편"* 처럼 **부분집합**을 세는
        # 서술이 훨씬 많다. 전체를 뜻하는 자리만 좁게 잡는다.
        patterns=[
            r"문헌 (\d+)편의",
            r"\*\*문헌 (\d+)편",
            r"문헌 (\d+)편 분석",
            r"논문 (\d+)편 한 편씩",
        ],
    ),
    Fact(
        name="보유 차량",
        truth=lambda: _config_value("DEFAULT_FLEET_SIZE"),
        patterns=[r"PBR_FLEET_SIZE=(\d+)", r"보유 차량 (\d+)대", r"보유 대수 (\d+)대"],
    ),
    Fact(
        name="회차당 투입 상한",
        truth=lambda: _config_value("DEFAULT_VEHICLES_PER_ROUND"),
        patterns=[r"PBR_VEHICLES_PER_ROUND=(\d+)"],
    ),
    Fact(
        name="목표재고 안전계수 z",
        truth=lambda: _config_value("TARGET_Z"),
        patterns=[r"PBR_TARGET_Z=([\d.]+)"],
    ),
    Fact(
        name="군집 거리 가중치 γ",
        truth=lambda: _config_value("CLUSTER_GAMMA"),
        patterns=[r"PBR_CLUSTER_GAMMA=(\d+)"],
    ),
]


def _same_number(found: str, truth: str) -> bool:
    """'3000'과 '3000.0', '1.99'와 '1.990'을 같게 본다."""
    try:
        return float(found) == float(truth)
    except ValueError:
        return found == truth


# ── 검사 ──────────────────────────────────────────────────────────────
def _scan_targets() -> list[Path]:
    out = []
    for path in ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix not in {".md", ".yml", ".yaml"} and path.name != ".env.example":
            continue
        if path.relative_to(ROOT).as_posix() in HISTORY_DOCS:
            continue
        out.append(path)
    return sorted(out)


def check_values() -> list[str]:
    """흩어진 값이 코드와 어긋나는 곳을 찾는다."""
    problems: list[str] = []
    targets = _scan_targets()
    for fact in FACTS:
        try:
            truth = str(fact.truth())
        except Exception as exc:  # noqa: BLE001 — 어떤 실패든 보고해야 한다
            problems.append(f"[{fact.name}] 진실의 출처를 읽지 못했습니다 — {exc}")
            continue
        hits = 0
        for path in targets:
            rel = path.relative_to(ROOT).as_posix()
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if any(a in line for a in fact.allow):
                    continue
                for pattern in fact.patterns:
                    for m in re.finditer(pattern, line):
                        if float(m.group(1)) < fact.min_value:
                            continue
                        hits += 1
                        if not _same_number(m.group(1), truth):
                            problems.append(
                                f"[{fact.name}] {rel}:{lineno} — "
                                f"문서 {m.group(1)}, 코드 {truth}\n"
                                f"      {line.strip()[:110]}"
                            )
        if hits == 0:
            problems.append(
                f"[{fact.name}] 어느 문서에서도 찾지 못했습니다 — 표현이 바뀌었으면 "
                "patterns를 고쳐야 합니다(검사가 조용히 죽습니다)"
            )
    return problems


# 1.26.88이 **번호를 밀지 않고 남겨 두기로** 한 겹침. 다른 문서의 인용이 모두
# 한쪽만 가리켜, 밀면 참조 다섯이 깨진다. **새 겹침을 여기 추가하지 마라** —
# 번호를 다음 값으로 미는 것이 규칙이다(1.26.90).
ALLOWED_DUPES = {"1.25.9", "1.26.47", "1.26.48"}


def _version_key(ver):
    return tuple(int(x) for x in ver.split("."))


def _version_sections():
    """(버전, 줄번호, 본문)을 문서에 나온 순서대로 돌려준다."""
    text = CHANGELOG.read_text(encoding="utf-8")
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        m = re.match(r"^## (\d+\.\d+\.\d+)", line)
        if m:
            out.append([m.group(1), lineno, ""])
        elif out:
            out[-1][2] += line + "\n"
    return [tuple(x) for x in out]


def check_version_numbers() -> list[str]:
    """버전관리.md의 번호가 겹치는지, 최신이 맨 위인지 본다.

    겹쳐도 되는 경우가 있다 — 이미 다른 문서가 그 번호를 인용하고 있어 밀 수
    없을 때다. 그때는 양쪽에 `ℹ️ 번호 겹침` 주석을 달기로 했다(1.26.88).

    ⚠️ **주석이 달렸다고 아무 겹침이나 통과시키지는 않는다** (1.26.90).
    그러면 다섯 번째 겹침도 주석 한 줄로 정당해져 검사가 무의미해진다.
    남겨 두기로 한 것은 `ALLOWED_DUPES` 셋뿐이고, **그 셋조차 주석이 빠지면
    걸린다.** 새로 겹친 번호는 주석이 있어도 실패다 — 번호를 밀어야 한다.
    """
    sections = _version_sections()

    grouped = {}
    for ver, lineno, body in sections:
        grouped.setdefault(ver, []).append((lineno, body))

    problems = []
    for num, entries in sorted(grouped.items(), key=lambda kv: _version_key(kv[0])):
        if len(entries) == 1:
            continue
        lines = ", ".join(str(ln) for ln, _ in entries)
        if num not in ALLOWED_DUPES:
            problems.append(
                f"[버전 번호] {num}이(가) {len(entries)}번 나옵니다 (줄 {lines}). "
                "나중 커밋 쪽을 다음 번호로 미세요 — 규칙은 '커밋 시각 순서'입니다"
            )
            continue
        unmarked = [str(ln) for ln, body in entries if "번호 겹침" not in body]
        if unmarked:
            problems.append(
                f"[버전 번호] {num}은(는) 남겨 두기로 한 겹침인데 "
                f"줄 {', '.join(unmarked)}에 `ℹ️ 번호 겹침` 주석이 없습니다"
            )

    # 6행이 선언한 "최신 버전이 맨 위". 예외가 낀 자리의 뒤엉킴은 넘어간다.
    for (cur, cur_ln, _), (nxt, nxt_ln, _) in zip(sections, sections[1:]):
        if cur in ALLOWED_DUPES or nxt in ALLOWED_DUPES:
            continue
        if _version_key(cur) < _version_key(nxt):
            problems.append(
                f"[버전 정렬] 줄 {cur_ln}의 {cur} 아래에 더 큰 {nxt}"
                f"(줄 {nxt_ln})가 있습니다 — 최신이 맨 위여야 합니다"
            )

    return problems


# 이미 푸시돼 **고칠 수 없는** 접두사 어긋남. 히스토리를 다시 쓰지 않기로 한 것들이다.
#
# 🔴 **이 목록은 면죄부가 아니라 처리 완료 표시다** — `ALLOWED_DUPES`와 같은
# 성격이다. 새로 어긋난 커밋은 여기 넣지 말고 **커밋 직후에 제목을 고쳐라**
# (아직 푸시 전이면 `git commit --amend`로 끝난다).
#
# 왜 필요한가: 이 검사기는 이력 전체를 훑으므로 못 고치는 한 건이 **매 실행마다**
# 뜬다. 늘 빨간 검사기는 사람이 "아 그거"라며 넘기기 시작하고, 그때부터
# **진짜 어긋남이 섞여도 안 보인다** — 이 저장소가 *"통과만 하고 아무것도 못
# 잡는 검사기는 규칙이 없는 것과 같다"* 고 적은 것의 뒷면이다(1.26.143).
ALLOWED_PREFIX_MISMATCH = {
    # 260830으로 적었으나 실제 커밋일은 260831. 푸시된 뒤에 발견했다.
    "b9a6544": "260830 군집 연쇄 주행을 쟀다 (1.26.38) — 푸시 후 발견, 되돌리지 않음",
}


def check_commit_prefix() -> list[str]:
    """커밋 제목의 `YYMMDD` 접두사가 실제 커밋 날짜와 같은지 본다.

    긴 세션이 자정을 넘기면 앞 커밋의 날짜를 그대로 이어 쓰게 된다.
    실제로 네 번 났다(260810 · 260811 · 260826 · 260830).

    이미 푸시돼 고칠 수 없는 것은 `ALLOWED_PREFIX_MISMATCH`에 사유와 함께
    적어 두고 넘긴다 — 위 주석 참고.
    """
    try:
        out = subprocess.run(
            ["git", "log", "--format=%h|%ad|%s", "--date=format:%y%m%d"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8",
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return ["[커밋 접두사] git을 부르지 못해 건너뜁니다"]

    problems = []
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        sha, when, subject = parts
        m = re.match(r"^(\d{6}) ", subject)
        if not m:
            continue
        if m.group(1) == when:
            continue
        if sha in ALLOWED_PREFIX_MISMATCH:
            continue
        problems.append(
            f"[커밋 접두사] {sha} — 제목 {m.group(1)}, 실제 {when}\n"
            f"      {subject[:100]}"
        )

    # 넘긴 것을 **한 줄로 밝힌다.** 조용히 빼면 목록이 늘어나도 아무도 모르고,
    # 그러면 면죄부 목록이 되어 버린다.
    if ALLOWED_PREFIX_MISMATCH:
        print(f"  (고칠 수 없어 넘긴 커밋 {len(ALLOWED_PREFIX_MISMATCH)}건: "
              + ", ".join(sorted(ALLOWED_PREFIX_MISMATCH)) + ")")
    return problems


CHECKS = {
    "값": check_values,
    "버전": check_version_numbers,
    "커밋": check_commit_prefix,
}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="문서와 코드가 같은 값을 말하는지 확인한다."
    )
    ap.add_argument("--strict", action="store_true", help="어긋나면 exit 1")
    ap.add_argument("--only", choices=sorted(CHECKS), help="한 검사만 돌린다")
    ap.add_argument(
        "--no-commit-check",
        action="store_true",
        help="커밋 접두사 검사를 뺀다(이미 push된 이력은 되돌리기 어렵다)",
    )
    args = ap.parse_args()

    names = [args.only] if args.only else list(CHECKS)
    if args.no_commit_check and "커밋" in names:
        names.remove("커밋")

    total = 0
    for name in names:
        problems = CHECKS[name]()
        print(f"\n{'=' * 62}\n{name} 검사 — {len(problems)}건")
        for p in problems:
            print(f"  X {p}")
        if not problems:
            print("  OK 어긋난 곳이 없습니다")
        total += len(problems)

    print(f"\n{'=' * 62}\n합계 {total}건")
    return 1 if (args.strict and total) else 0


if __name__ == "__main__":
    sys.exit(main())
