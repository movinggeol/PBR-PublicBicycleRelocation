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
    "docs/구현/두_PC_작업.md",   # 1.26.121에 기록/ → 구현/으로 옮겼다
    "docs/기록/RETROSPECTIVE.md",  # "0개 → 548개" 같은 전후 대비를 싣는다
}

SKIP_DIRS = {".venv", ".git", "node_modules", "__pycache__", "site-packages"}
# git worktree 사본은 저장소 안(.claude/worktrees/)에 만들어져도 '지금 문서'가 아니다.
# 빼지 않으면 사본의 문서까지 훑어 값·링크·파일별 검사가 사본 경로로 거짓 실패한다
# (1.26.198 점검 중 실제로 167건이 났다).
SKIP_PREFIXES = (".claude/worktrees/",)


def _skipped(path: Path) -> bool:
    if any(part in SKIP_DIRS for part in path.parts):
        return True
    return path.relative_to(ROOT).as_posix().startswith(SKIP_PREFIXES)


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
    # env_int("PBR_...", 42) / env_float("PBR_...", 1.99) — 1.26.144에서 스무 곳
    # 넘는 날것 파싱을 이 헬퍼로 묶었다. 기본값이 **따옴표 없는 숫자**라 위
    # 정규식에 안 걸린다(실제로 그 판에서 z·γ 검사가 통째로 멈췄다).
    helper = re.search(
        rf"^{name}\s*=\s*env_(?:int|float)\([^,]+,\s*([0-9.]+)\s*\)", src, re.M
    )
    if helper:
        return helper.group(1)
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
    # 파일별 개수도 함께 센다 — 문서가 파일마다 숫자를 싣기 때문이다
    # (check_test_counts 참고). 저장소 기준 상대 경로를 열쇠로 쓴다.
    per_file: dict = {}
    for item in hook.items:
        path = Path(str(getattr(item, "path", None) or item.fspath))
        try:
            key = path.relative_to(ROOT).as_posix()
        except ValueError:
            key = path.name
        per_file[key] = per_file.get(key, 0) + 1
    _TEST_COUNTS["per_file"] = per_file
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
            # ⚠️ 예전에는 `테스트[는가]?` 뒤에 숫자가 **바로 붙어야만** 잡았다.
            #    그래서 목차 표의 *"**테스트** — 684개가 무엇을 지키는지"* 가,
            #    굵게 표시와 줄표가 사이에 끼었다는 이유만으로 새 나갔다 —
            #    1.26.144가 개수를 691로 올릴 때 문서 14줄이 따라갔는데 그
            #    한 줄만 684로 남았고, 검사기는 **0건이라고 답했다**(1.26.145).
            #    윗줄 1.26.119와 같은 실패다: 값이 틀린 게 아니라 **검사가 그
            #    자리에 닿지 않은 것**이라, 틀린 채로 더 오래 남는다.
            r"테스트\**[는가]?\**\s*[—–\-:·]?\s*(\d+)개(?! 추가)",  # "테스트 9개 추가"는 증분이다
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
            # 검사가 새 나갔던 그 줄을 **증거로** 인용한다(1.26.145). 지금
            # 값으로 고치면 "684가 691 옆에 남아 있었다"는 사실이 사라진다.
            "굵게 표시와 줄표가 끼었다는 이유로 새 나갔고",
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
        # ⚠️ 맨 `표 (\d+)개`는 쓰면 안 된다 — 한국어에서 '표'는 **DB의 표**와
        #    **문서의 표**를 둘 다 뜻한다. THESIS의 "표 131개 · 그림 0장"(논문에
        #    실린 표를 센 것)을 DB 테이블 수로 잡아 오탐이 났다(1.26.134).
        patterns=[r"테이블 (\d+)개", r"(\d+)개 테이블", r"표 (\d+)개의 컬럼"],
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
        if _skipped(path):
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


# ── 논문 검사 ─────────────────────────────────────────────────────────
# 🔴 **이 검사는 코드가 아니라 정본 절을 진실로 삼는다.** 논문 수치의 출처는
#    실험 CSV인데 `data/`·`*.csv`는 커밋되지 않으므로 CI에서 읽을 수 없다.
#    그래서 값을 여기 적어 두고 **정본 파일에 그 값이 실제로 있는지**를 먼저
#    확인한다 — 정본이 바뀌면 검사기가 먼저 걸리므로 조용히 낡지 않는다.
#
# ⚠️ 검사 범위는 **논문 본문뿐**이다(README + 초안). `docs/분석`·`docs/기록`은
#    날짜가 박힌 **기록**이라 옛 값을 그대로 두는 것이 맞다 — EXPERIMENTS 5장이
#    아직 2.00을 적고 있는 것은 낡은 것이 아니라 2026-08-26에 그렇게 쟀다는 뜻이다.
THESIS_SCOPE = ("README.md", "docs/연구/초안/")


@dataclass
class ThesisFact:
    name: str
    truth: str
    source: str          # 이 값의 정본 — 여기 없으면 검사기가 낡은 것이다
    patterns: list[str]
    allow: list[str] = field(default_factory=list)


# 1.26.134에서 **핵심 결과가 네 판으로 갈려 있던** 것을 사람이 눈으로 찾았다.
# 같은 일이 다시 나면 여기서 걸린다.
THESIS_FACTS = [
    ThesisFact(
        name="무재배치 결품 (25년 11월 `_05_10`)",
        truth="2.40",
        source="docs/연구/초안/6장_실험_성능평가.md",
        # 6장은 `± 0.00`이 붙고 README는 안 붙는다. 두 모양을 따로 잡되,
        # `\| 무재배치 \|`는 **앞에 B0가 없는 줄만** 무는다 — 그래야 같은 절의
        # 포화 표(`| B0 무재배치 | 1.36 |`)를 오탐하지 않는다.
        patterns=[r"무재배치 \| (\d\.\d\d) ±", r"\| 무재배치 \| (\d\.\d\d) \|"],
    ),
    ThesisFact(
        name="제안 방법 결품 (25년 11월 `_05_10`)",
        truth="1.05",
        source="docs/연구/초안/6장_실험_성능평가.md",
        patterns=[r"P 제안\*{0,2} \| \*\*(\d\.\d\d) ±",
                  r"\*\*제안 방법\*\* \| \*\*(\d\.\d\d)\*\*"],
    ),
    ThesisFact(
        name="반복 실험의 씨앗 수",
        truth="3",
        source="docs/연구/초안/6장_실험_성능평가.md",
        patterns=[r"12개월 × 씨앗 (\d+)개"],
    ),
    ThesisFact(
        name="대여소 스냅샷 지문",
        truth="9e4c0d5c",
        source="docs/연구/초안/6장_실험_성능평가.md",
        patterns=[r"지문 `([0-9a-f]{8})`"],
    ),
]


def _thesis_targets() -> list[Path]:
    out = []
    for path in _scan_targets():
        rel = path.relative_to(ROOT).as_posix()
        if any(rel == s or rel.startswith(s) for s in THESIS_SCOPE):
            out.append(path)
    return out


def check_thesis() -> list[str]:
    """논문 본문이 **한 목소리로** 말하는지 확인한다 (1.26.138).

    코드가 아니라 **정본 절**이 진실이다. 정본에서 그 값이 사라지면 다른 곳을
    보기 전에 그것부터 알린다 — 그러지 않으면 이 검사기 자체가 낡은 값을
    지키는 다섯 번째 판이 된다.
    """
    problems: list[str] = []
    targets = _thesis_targets()
    for fact in THESIS_FACTS:
        source = ROOT / fact.source
        text = source.read_text(encoding="utf-8") if source.exists() else ""
        if not any(re.search(p, line) and re.search(p, line).group(1) == fact.truth
                   for line in text.splitlines() for p in fact.patterns):
            problems.append(
                f"[{fact.name}] 정본({fact.source})에 '{fact.truth}'이(가) 없습니다 — "
                f"정본이 바뀌었다면 `tools/check_consistency.py`의 값을 함께 고치십시오"
            )
            continue
        for path in targets:
            rel = path.relative_to(ROOT).as_posix()
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            for lineno, line in enumerate(lines, 1):
                if any(a in line for a in fact.allow):
                    continue
                for pattern in fact.patterns:
                    for m in re.finditer(pattern, line):
                        if m.group(1) != fact.truth:
                            problems.append(
                                f"[{fact.name}] {rel}:{lineno} — "
                                f"문서 {m.group(1)}, 정본 {fact.truth}\n"
                                f"      {line.strip()[:110]}"
                            )
    return problems


# 🔴 **결론과 초록은 새 수치를 만들면 안 된다.** 둘 다 앞 장을 압축한 글이라
#    거기 처음 나오는 숫자는 **근거 없는 숫자**다. 실제로 1.26.137의 9장 초안이
#    6.3의 `374.7~458.5km`를 `375~459km`로 반올림해 **6장에 없는 범위를**
#    만들었다(1.26.139에서 잡음).
DERIVED_DOCS = {
    "docs/연구/초안/9장_결론.md": "docs/연구/초안/6장_실험_성능평가.md",
    "docs/연구/초안/초록.md": "docs/연구/초안/6장_실험_성능평가.md",
    # 🔴 1.26.259까지 **초안만** 보고 있었다. 정작 심사에 내는 정본(논문 폴더)은
    #    검사 밖이라, 9장이 6장에 없는 12개월 평균 대수를 인용해도 통과했다.
    #    정본의 결론·초록은 6장만이 아니라 **본문 전체**를 압축하므로 원본도
    #    장 파일 전부로 둔다("CHAPTERS").
    "docs/연구/논문/9장_결론.md": "CHAPTERS",
    "docs/연구/논문/초록.md": "CHAPTERS",
}
# 장 번호(6.3)·연도(2026)·표본 크기처럼 **압축한 글이 당연히 새로 쓰는** 수는 뺀다.
_DERIVED_SKIP = re.compile(r"^(?:\d{1,2}|\d{4}|\d\.\d|\d\.\d\.\d)$")


def check_derived() -> list[str]:
    """결론·초록이 원본 장에 없는 수치를 만들지 않았는지 본다 (1.26.139)."""
    problems: list[str] = []
    num = re.compile(r"\d+(?:\.\d+)?")
    for doc, src in DERIVED_DOCS.items():
        dp = ROOT / doc
        if not dp.exists():
            continue
        if src == "CHAPTERS":      # 정본의 결론·초록은 본문 전체를 압축한다
            sources = [f for f in sorted((ROOT / MANUSCRIPT_DIR).glob("*장_*.md"))
                       if f != dp]
        else:
            sources = [ROOT / src]
        sources = [f for f in sources if f.exists()]
        if not sources:
            continue
        source = chr(10).join(f.read_text(encoding="utf-8") for f in sources)
        for lineno, line in enumerate(dp.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith((">", "|")) or "](" in line:
                continue          # 머리말·표·링크는 원본을 가리키는 글이다
            for m in num.finditer(line):
                v = m.group(0)
                # 🔴 **부분 문자열로 찾으면 못 잡는다** (1.26.259). 9장의
                #    *"251대"* 가 <표 5-7>의 **251.4** 안에서 발견돼 통과했다.
                #    숫자의 앞뒤 경계를 함께 본다.
                if _DERIVED_SKIP.match(v) or re.search(
                        r"(?<![\d.])" + re.escape(v) + r"(?![\d.])", source):
                    continue
                where = "본문 장" if src == "CHAPTERS" else Path(src).name
                problems.append(
                    f"[파생 문서] {doc}:{lineno} — '{v}'이(가) 원본({where})에 "
                    f"없습니다. 반올림했거나 새로 만든 수치입니다\n"
                    f"      {line.strip()[:110]}"
                )
    return problems


# 문서가 파일별 테스트 개수를 싣는 두 표기.
#   README      - `tests/test_webapp.py` (121) — 설명
#   TESTING.md  | [tests/test_webapp.py](../../tests/test_webapp.py) | 121 | 설명
_PER_FILE_PATTERNS = [
    re.compile(r"`(tests/[\w.]+\.py)`\s*\((\d+)\)"),
    re.compile(r"\[(tests/[\w.]+\.py)\]\([^)]*\)\s*\|\s*(\d+)\s*\|"),
]


def check_test_counts() -> list[str]:
    """문서가 싣는 **파일별** 테스트 개수를 실제 수집과 대조한다 (1.26.147).

    값 검사는 스위트 **합계**만 본다. 그래서 파일별 숫자는 표기가 달라 어느
    패턴에도 안 걸린 채 마음대로 낡았다 — 실측으로 README 세 곳이 어긋났고
    `test_webapp.py`는 37이라 적힌 것이 실제로는 **121**이었다(3.3배).

    🔴 **같은 유형이 세 번째다.** 1.26.119는 *"확인"이 붙은 것만* 잡아
    `pbr-run`의 "607개 통과가 기준선"을 놓쳤고, 1.26.145는 굵게 표시와 줄표가
    끼었다는 이유로 README 목차 한 줄을 놓쳤다. 매번 값이 틀린 게 아니라
    **검사가 그 자리에 닿지 않았다** — 그리고 그 자리는 검사되고 있다고
    착각되는 만큼 더 오래 틀린 채 남았다.
    """
    problems: list[str] = []
    per_file = _pytest_counts().get("per_file") or {}
    if not per_file:
        return ["[파일별 테스트] 파일별 개수를 세지 못했습니다"]

    for path in _scan_targets():
        rel = path.relative_to(ROOT).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for pattern in _PER_FILE_PATTERNS:
                for m in pattern.finditer(line):
                    name, said = m.group(1), int(m.group(2))
                    real = per_file.get(name)
                    if real is None:
                        problems.append(
                            f"[파일별 테스트] {rel}:{lineno} — '{name}'은(는) "
                            "수집되지 않는 파일입니다(이름이 바뀌었거나 지워졌습니다)")
                    elif real != said:
                        problems.append(
                            f"[파일별 테스트] {rel}:{lineno} — {name} "
                            f"문서 {said}, 실제 {real}\n"
                            f"      {line.strip()[:110]}")
    return problems


_LINK_RE = re.compile(r"\[([^\]]{1,80})\]\(([^)]+)\)")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def check_links() -> list[str]:
    """문서끼리 건 상대 링크가 **실제 파일을 가리키는지** 본다 (1.26.148).

    문서 58개가 서로를 촘촘히 가리킨다. 파일이 폴더를 옮기면 링크는 조용히
    죽는다 — 오류가 나지 않고, 누른 사람만 404를 본다. 실제로 버전관리.md의
    `[GLOSSARY](GLOSSARY.md)`가 그랬다(정본이 `docs/GLOSSARY.md`라 `../`가
    필요했다). **같은 문서 다른 두 곳은 맞게 적혀 있었다** — 한 문서 안에서도
    갈렸다는 뜻이라, 사람 눈으로 지킬 수 있는 종류가 아니다.

    ⚠️ 코드 담장(``` / ~~~) 안은 세지 않는다. 표기법을 설명하려고 링크 모양을
    그대로 인용하는 자리가 있어서(1.26.147), 그것까지 세면 오탐만 늘어난다.
    """
    problems: list[str] = []
    for path in sorted(ROOT.rglob("*.md")):
        if _skipped(path):
            continue
        rel = path.relative_to(ROOT).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        fence = False
        for lineno, line in enumerate(text.splitlines(), 1):
            if _FENCE_RE.match(line):
                fence = not fence
                continue
            if fence:
                continue
            for m in _LINK_RE.finditer(line):
                target = m.group(2).split("#")[0].strip()
                if not target or target.startswith(
                        ("http://", "https://", "mailto:", "<")):
                    continue
                if (path.parent / target).exists():
                    continue
                problems.append(
                    f"[링크] {rel}:{lineno} — '{target}'이(가) 없습니다"
                    f"\n      [{m.group(1)[:40]}]({target})")
    return problems


# 고쳐 놓고 문서만 안 따라간 규약. **문서가 옛 동작을 현재형으로 가르치면**
# 읽는 사람이 안심하고 그대로 따라 한다 — 낡은 숫자보다 나쁘다.
#
# `문구`가 문서에 있는데 `증거`가 코드에 함께 없으면 낡은 것으로 본다.
# 코드가 진실의 출처이므로, 규약이 또 바뀌면 이 표가 아니라 **코드가 먼저**
# 바뀌고 검사가 그때 운다.
STALE_CLAIMS = [
    {
        "이름": "최신 판단 기준",
        "문구": re.compile(r"`run_label`\*{0,2}\s*최대값|MAX\(run_label\)"),
        "증거": ("db.py", "created_at"),
        # ⚠️ **파일 단위로 면제하지 않는다.** WEBAPP·DB_PLAN이 바로 이 버그를
        # 가졌던 문서라, 통째로 빼면 정작 지켜야 할 자리가 빈다. 대신 아래
        # `정정표시`가 붙은 **줄만** 넘긴다 — 옛 규약을 *인용해서 부정하는* 글이다.
        "면제": (
            "docs/기록/",                 # 이력은 그때의 기록이다
            # 실험 기록도 마찬가지다 — *"그때 이렇게 재고 있었다"* 를 과거형으로
            # 남긴 자리라, 현재 동작으로 고치면 그 실험을 왜 다시 쟀는지가 사라진다.
            "docs/분석/EXPERIMENTS.md",
        ),
        "안내": "`latest_label()`은 1.26.125부터 `runs.created_at`으로 고른다",
    },
]

# 옛 규약을 **인용해서 부정하는** 줄. 이것이 붙어 있으면 낡은 것이 아니라
# 정정문이다 — 이 표시를 지우면 검사가 그 줄을 다시 문다.
_정정표시 = re.compile(r"아닙니다|아니다|버그였|고쳤|고쳐졌|이(?:었|였)고|까지는|였습니다")


def check_stale_claims() -> list[str]:
    """고친 규약을 문서가 아직 현재 동작으로 가르치지 않는지 본다 (1.26.151).

    🔴 실제로 났다. `WEBAPP.md`가 *"최신 = `run_label` 최대값 · 취약점 없음"* 이라고
    **1.26.125에서 고친 바로 그 버그**를 현재 동작으로 적어 두었고, 같은 표가
    `DB_PLAN.md`에도 있었다. `DB_SCHEMA.md`만 따라가 고쳐져 **한 저장소가 두 말을
    하고 있었다.** 숫자가 아니라 산문이라 값 검사도 링크 검사도 못 잡았다.
    """
    problems: list[str] = []
    for claim in STALE_CLAIMS:
        evidence_file, evidence = claim["증거"]
        src = ROOT / evidence_file
        if not src.exists() or evidence not in src.read_text(encoding="utf-8"):
            continue          # 코드가 그 규약을 안 쓴다 — 문서가 맞을 수도 있다
        for path in sorted(ROOT.glob("docs/**/*.md")):
            rel = path.relative_to(ROOT).as_posix()
            if any(rel.startswith(x) for x in claim["면제"]):
                continue
            for lineno, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), 1):
                # 인용문(`>`)은 옛 규약을 **설명하는** 자리다 — `check_derived()`도
                # 같은 이유로 머리말을 건너뛴다.
                if line.lstrip().startswith(">"):
                    continue
                if claim["문구"].search(line) and not _정정표시.search(line):
                    problems.append(
                        f"[낡은 규약] {rel}:{lineno} — {claim['이름']}: "
                        f"{claim['안내']}\n      {line.strip()[:110]}")
    return problems


# 원고가 그림·표를 싣는 곳. 장 파일이 본문이고, 앞붙이가 차례다.
MANUSCRIPT_DIR = "docs/연구/논문"
FIGURE_DIR = "docs/연구/초안/그림"
FRONT_MATTER = "docs/연구/논문/앞붙이.md"
# 차례를 싣지 않는 원고 문서 — 규약·절차 문서라 본문이 아니다.
_NOT_BODY = {"README.md", "앞붙이.md", "한글_옮기기.md"}
# 원고에 싣지 않기로 한 그림 파일. **비어 있는 것이 정상이고**, 넣을 때는
# 값에 그 까닭을 적는다(지우면 검사가 다시 문다).
FIGURE_EXEMPT: dict[str, str] = {}


def check_figures() -> list[str]:
    """그림 파일 · 원고 본문 · 앞붙이 차례가 **같은 목록**을 말하는지 본다 (1.26.257).

    🔴 실제로 났다. 6장 그림 셋(6-1·6-2·6-3)은 **파일도 있고 초안에도 실려**
    있었는데 원고 본문과 그림 차례 어디에도 없었다. 논문의 핵심 장이 그림 0개인
    채로 넘어갔고, 그동안 그 파일들은 09-07의 옛 이동시간 가정으로 그린 것이라
    **철회된 주장을 그린 그림**이 저장소에 남아 있었다.

    원고 README 4-1의 '자리' 규칙은 이것을 못 잡는다 — 자리 표시는 *아직 만들지
    않은 자료*만 세므로 **만들어 두고 싣지 않은 자료**는 세지 않는다. 사람이
    눈으로 장을 넘겨 보아야만 걸리는 자리였다.
    """
    problems: list[str] = []
    man = ROOT / MANUSCRIPT_DIR
    if not man.exists():
        return problems

    body = sorted(f for f in man.glob("*.md") if f.name not in _NOT_BODY)
    chapters = sorted(man.glob("*장_*.md"))
    text = {f: f.read_text(encoding="utf-8") for f in body}

    # ① 그림 파일 ↔ 원고가 건 이미지 링크
    linked = {m.group(1) for s in text.values()
              for m in re.finditer(r"!\[[^\]]*\]\([^)]*그림/([^)]+\.png)\)", s)}
    on_disk = {f.name for f in (ROOT / FIGURE_DIR).glob("*.png")}
    for name in sorted(on_disk - linked - set(FIGURE_EXEMPT)):
        problems.append(
            f"[그림] {FIGURE_DIR}/{name} — 그림 파일이 있는데 **원고가 싣지 않았습니다**. "
            f"실을 자리가 없으면 FIGURE_EXEMPT에 까닭과 함께 적으십시오")
    for name in sorted(linked - on_disk):
        problems.append(f"[그림] 원고가 없는 파일을 가리킵니다: {FIGURE_DIR}/{name}")

    # ② 캡션 ↔ 본문 참조
    #
    # ⚠️ **줄머리에 있다고 캡션이 아니다.** 본문 문장도 `<표 2-1>은 …`처럼 시작한다
    #    (이 검사를 처음 돌렸을 때 그런 다섯 줄을 캡션으로 잘못 읽었다). 원고 규약이
    #    표는 위에, 그림은 아래에 캡션을 두므로 **자리로 가른다** —
    #    표 캡션은 바로 아래가 표(`|`)이고, 그림 캡션은 바로 위가 이미지 링크다.
    caption_fig, caption_tab = {}, {}
    caption_lines: set[tuple[str, int]] = set()

    def _table_follows(lines: list[str], i: int) -> bool:
        """캡션 줄 다음에 표가 오는가. **캡션은 여러 줄로 접힌다** — <표 8-2>의
        조건 문구가 두 줄이라 바로 다음 줄만 보면 표를 못 찾는다."""
        for nxt in lines[i:i + 6]:
            if nxt.lstrip().startswith("|"):
                return True
            if nxt.lstrip().startswith("#"):
                break
        return False

    def _prev_solid(lines: list[str], i: int) -> str:
        for prev in reversed(lines[:i]):
            if prev.strip():
                return prev.strip()
        return ""

    for f in chapters:
        lines = text[f].splitlines()
        for idx, line in enumerate(lines):
            lineno = idx + 1
            m = re.match(r"\[그림 (\d+-\d+)\] ", line)
            if m and _prev_solid(lines, idx).startswith("!["):
                caption_fig.setdefault(m.group(1), f"{f.name}:{lineno}")
                caption_lines.add((f.name, lineno))
            m = re.match(r"<표 (\d+-\d+)> ", line)
            if m and _table_follows(lines, lineno):
                caption_tab.setdefault(m.group(1), f"{f.name}:{lineno}")
                caption_lines.add((f.name, lineno))

    ref_fig, ref_tab = set(), set()
    for f, s in text.items():
        for lineno, line in enumerate(s.splitlines(), 1):
            if (f.name, lineno) in caption_lines:
                continue
            ref_fig |= set(re.findall(r"\[그림 (\d+-\d+)\]", line))
            ref_tab |= set(re.findall(r"<표 (\d+-\d+)>", line))
    for num in sorted(ref_fig - set(caption_fig)):
        problems.append(f"[그림] 본문이 [그림 {num}]을 가리키는데 **캡션이 없습니다**")
    for num in sorted(set(caption_fig) - ref_fig):
        problems.append(f"[그림] [그림 {num}]에 캡션만 있고 **본문이 한 번도 가리키지 않습니다** "
                        f"({caption_fig[num]})")
    for num in sorted(ref_tab - set(caption_tab)):
        problems.append(f"[표] 본문이 <표 {num}>을 가리키는데 **캡션이 없습니다**")
    for num in sorted(set(caption_tab) - ref_tab):
        problems.append(f"[표] <표 {num}>에 캡션만 있고 **본문이 한 번도 가리키지 않습니다** "
                        f"({caption_tab[num]})")

    # ③ 앞붙이의 그림·표 차례 ↔ 캡션
    front = ROOT / FRONT_MATTER
    if front.exists():
        fm = front.read_text(encoding="utf-8")
        listed_fig = set(re.findall(r"^\| \[그림 (\d+-\d+)\] \|", fm, re.M))
        listed_tab = set(re.findall(r"^\| <표 (\d+-\d+)> \|", fm, re.M))
        for num in sorted(set(caption_fig) - listed_fig):
            problems.append(f"[그림] [그림 {num}]이 앞붙이의 **그림 차례에 없습니다**")
        for num in sorted(listed_fig - set(caption_fig)):
            problems.append(f"[그림] 그림 차례의 [그림 {num}]이 **원고에 없습니다**")
        for num in sorted(set(caption_tab) - listed_tab):
            problems.append(f"[표] <표 {num}>이 앞붙이의 **표 차례에 없습니다**")
        for num in sorted(listed_tab - set(caption_tab)):
            problems.append(f"[표] 표 차례의 <표 {num}>이 **원고에 없습니다**")
        # 다 그린 그림에 '자리' 표시가 남아 있지 않은지 (README 4-1)
        for lineno, line in enumerate(fm.splitlines(), 1):
            m = re.match(r"^\| \[그림 (\d+-\d+)\] \|", line)
            if m and "(자리)" in line and m.group(1) in caption_fig:
                problems.append(
                    f"[그림] 그림 차례 {FRONT_MATTER}:{lineno} — [그림 {m.group(1)}]은 "
                    f"이미 원고에 실렸는데 *(자리)* 표시가 남아 있습니다")
    return problems


# 참고문헌과 본문 인용을 맞춰 보는 검사가 쓰는 것.
REFERENCES = "docs/연구/논문/참고문헌.md"
# 본문이 인용하지 않아도 되는 참고문헌 항목("성 연도" 또는 기관 이름).
# **비어 있는 것이 정상이고**, 넣을 때는 값에 그 까닭을 적는다.
CITATION_EXEMPT: dict[str, str] = {}

_NAME = r"[가-힣A-Za-z'’]+"
_JOIN = r"\s*(?:외|과|와|·|,)\s*"


def _ref_entries(text: str) -> list[tuple[str, str, str]]:
    """참고문헌 문단을 (저자 앞머리, 연도, 전문)으로 가른다.

    연도가 없는 문단은 URL만 적은 **자료 출처**다(기상청·공공데이터포털 등).
    그쪽은 저자-연도로 인용되지 않으므로 기관 이름이 본문에 나오는지만 본다.
    """
    out: list[tuple[str, str, str]] = []
    for para in re.split(r"\n\s*\n", text):
        one = " ".join(para.split())
        if not one or one.startswith("#"):
            continue
        m = re.search(r"\((\d{4})", one)
        if m:
            out.append((one[:m.start()].strip(), m.group(1), one))
        else:
            out.append((one.split(".")[0].strip(), "", one))
    return out


def _surnames(authors: str) -> list[str]:
    """앞머리에서 성만 뽑는다. 영문 이니셜(`C.`·`L.-M.`)은 성이 아니므로 버린다."""
    out = []
    for a in re.split(r"\s*[,·&]\s*|\s+외\s*|\s+and\s+", authors):
        a = a.strip()
        if a and not re.fullmatch(r"[A-Z]\.(?:\s*[A-Z-]+\.)*", a):
            out.append(a)
    return out


def check_citations() -> list[str]:
    """본문 인용과 참고문헌이 **서로를 가리키는지** 본다 (1.26.260).

    🔴 실제로 났다. 참고문헌은 도로 이동시간의 출처로 *티맵모빌리티 TMAP API*를
    싣고 있었는데, 원고 본문은 <표 4-1>을 포함해 어디서도 그 이름을 적지 않고
    *"상용 경로 안내 API"* 라고만 했다. 같은 표의 다른 행은 출처를 이름으로
    적으므로 **그 항목만 아무 데도 닿지 않는 참고문헌**이었다.

    ⚠️ **줄 단위로 읽으면 안 된다.** `Ghosh\n외(2017)`처럼 저자와 연도가 줄바꿈으로
    갈리므로, 이 검사를 처음 돌렸을 때 멀쩡한 인용 스무 건을 짝이 없다고 물었다.
    문서를 한 줄로 이어 붙인 뒤에 본다.
    """
    problems: list[str] = []
    man, ref_path = ROOT / MANUSCRIPT_DIR, ROOT / REFERENCES
    if not man.exists() or not ref_path.exists():
        return problems

    entries = _ref_entries(ref_path.read_text(encoding="utf-8"))
    index: dict[tuple[str, str], str] = {}
    for authors, year, full in entries:
        for s in _surnames(authors) if year else []:
            index.setdefault((s, year), full)

    skip = _NOT_BODY | {Path(REFERENCES).name}
    body = {f.name: " ".join(f.read_text(encoding="utf-8").split())
            for f in sorted(man.glob("*.md")) if f.name not in skip}

    # 서술형 `저자 외(2017)`와 괄호형 `(김영일, 2022)` 두 갈래를 다 본다.
    narrative = re.compile(rf"({_NAME}(?:{_JOIN}{_NAME})*)(?:\s*외)?\s*\((\d{{4}})\)")
    parens = re.compile(rf"\(({_NAME}(?:{_JOIN}{_NAME})*)(?:\s*외)?,\s*(\d{{4}})\)")

    cited: set[str] = set()
    unknown: set[tuple[str, str]] = set()
    for name, s in body.items():
        for pat in (narrative, parens):
            for m in pat.finditer(s):
                year = m.group(2)
                toks = [x for x in re.split(_JOIN, m.group(1)) if x]
                hit = next((x for x in toks if (x, year) in index), None)
                if hit:
                    cited.add(index[(hit, year)])
                elif toks:
                    unknown.add((name, f"{toks[-1]}({year})"))

    for name, txt in sorted(unknown):
        problems.append(f"[인용] {MANUSCRIPT_DIR}/{name} — 본문이 {txt}을 인용하는데 "
                        f"**참고문헌에 그 항목이 없습니다**")

    for authors, year, full in entries:
        label = f"{(_surnames(authors) or [authors])[0]} {year}".strip()
        if full in cited or label in CITATION_EXEMPT or authors in CITATION_EXEMPT:
            continue
        if not year:
            if authors and any(authors in s for s in body.values()):
                continue
            problems.append(
                f"[인용] 자료 출처 '{authors}'를 참고문헌이 싣는데 **원고 본문이 "
                f"한 번도 이름을 적지 않습니다** — 쓰지 않으면 참고문헌에서 빼십시오")
            continue
        if not any((s, year) in index and index[(s, year)] is full
                   for s in _surnames(authors)):
            continue
        problems.append(
            f"[인용] 참고문헌 '{label}'을 **본문이 한 번도 인용하지 않습니다** — "
            f"인용하지 않을 것이면 CITATION_EXEMPT에 까닭과 함께 적으십시오")
    return problems


# 부록 B(핵심 코드)의 발췌가 원본과 같은지 보는 검사가 쓰는 것.
APPENDIX_CODE = "docs/연구/논문/부록_핵심코드.md"
APPENDIX_TOOL = "tools/make_appendix_code.py"


def check_appendix_code() -> list[str]:
    """부록 B의 코드 발췌가 **지금 코드와 같은 줄인지** 본다 (1.26.261).

    학과 양식은 구현한 프로그램의 핵심 코드를 부록으로 요구한다. 그런데 발췌는
    복사본이라 **원본이 바뀌어도 아무 일도 일어나지 않는다** — 문서가 조용히 낡는
    이 저장소의 단골 결함이고, 심사자가 부록과 저장소를 대조하면 바로 드러난다.

    발췌의 코드 줄을 원본에서 한 줄씩 찾는다. 앞뒤 공백과 순서는 보지 않는다 —
    부록은 들여쓰기를 덜어 내고 갈래 하나를 걷어 내기 때문이다. `#`로 시작하는 줄은
    부록에서 식을 읽히려고 새로 단 주석이므로 건너뛴다.

    고치는 법: `python tools/make_appendix_code.py`로 다시 뽑는다. 줄 번호가
    어긋났으면 그 스크립트의 범위를 고친 뒤 다시 돌린다.
    """
    problems: list[str] = []
    doc = ROOT / APPENDIX_CODE
    if not doc.exists():
        return problems

    lines = doc.read_text(encoding="utf-8").splitlines()
    cache: dict[str, set[str]] = {}
    rel = None
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m = re.fullmatch(r"`([\w./-]+\.py)`", line)
        if m:
            rel = m.group(1)
        elif line.startswith("```python"):
            i += 1
            start = i
            while i < len(lines) and not lines[i].strip().startswith("```"):
                i += 1
            if rel is None:
                problems.append(
                    f"[부록] {APPENDIX_CODE}:{start} — 발췌 위에 **파일 이름이 없습니다**")
                continue
            if rel not in cache:
                src = ROOT / rel
                if not src.exists():
                    problems.append(f"[부록] {APPENDIX_CODE} — 없는 파일을 가리킵니다: {rel}")
                    cache[rel] = set()
                else:
                    cache[rel] = {x.strip() for x in
                                  src.read_text(encoding="utf-8").splitlines()}
            for n in range(start, i):
                code = lines[n].strip()
                if not code or code.startswith("#") or not cache[rel]:
                    continue
                if code not in cache[rel]:
                    problems.append(
                        f"[부록] {APPENDIX_CODE}:{n + 1} — 이 줄이 {rel}에 **없습니다**. "
                        f"`python {APPENDIX_TOOL}`로 다시 뽑으십시오\n      {code}")
            rel = None
        i += 1
    return problems


CHECKS = {
    "값": check_values,
    "링크": check_links,
    "파일별": check_test_counts,
    "버전": check_version_numbers,
    "커밋": check_commit_prefix,
    "논문": check_thesis,
    "파생": check_derived,
    "규약": check_stale_claims,
    "그림": check_figures,
    "인용": check_citations,
    "부록": check_appendix_code,
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
