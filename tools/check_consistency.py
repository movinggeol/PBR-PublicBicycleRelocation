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
}
# 장 번호(6.3)·연도(2026)·표본 크기처럼 **압축한 글이 당연히 새로 쓰는** 수는 뺀다.
_DERIVED_SKIP = re.compile(r"^(?:\d{1,2}|\d{4}|\d\.\d|\d\.\d\.\d)$")


def check_derived() -> list[str]:
    """결론·초록이 원본 장에 없는 수치를 만들지 않았는지 본다 (1.26.139)."""
    problems: list[str] = []
    num = re.compile(r"\d+(?:\.\d+)?")
    for doc, src in DERIVED_DOCS.items():
        dp, sp = ROOT / doc, ROOT / src
        if not (dp.exists() and sp.exists()):
            continue
        source = sp.read_text(encoding="utf-8")
        for lineno, line in enumerate(dp.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith((">", "|")) or "](" in line:
                continue          # 머리말·표·링크는 원본을 가리키는 글이다
            for m in num.finditer(line):
                v = m.group(0)
                if _DERIVED_SKIP.match(v) or v in source:
                    continue
                problems.append(
                    f"[파생 문서] {doc}:{lineno} — '{v}'이(가) 원본({Path(src).name})에 "
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
        if any(part in SKIP_DIRS for part in path.parts):
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


CHECKS = {
    "값": check_values,
    "링크": check_links,
    "파일별": check_test_counts,
    "버전": check_version_numbers,
    "커밋": check_commit_prefix,
    "논문": check_thesis,
    "파생": check_derived,
    "규약": check_stale_claims,
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
