"""버전 번호가 또 겹치지 않는지 — `tools/check_consistency.py`를 검증한다.

버전 번호는 손으로 매긴다. 두 세션이 나란히 일하면 같은 번호대를 각자 소진하고,
그 사실은 한참 뒤 정합성 점검에서야 드러난다. **네 번 났다**(1.25.3 · 1.26.45 ·
1.26.47/48 · 1.26.68~74). 규칙은 1.25.3 때 이미 문서에 적혔지만 지켜지지 않았다.

그래서 규칙을 검사로 옮겼다. 여기서 지키려는 것: **검사기가 실제로 걸러 내는가.**
현재 문서를 통과시키는 것만으로는 부족하다 — 통과만 하고 아무것도 못 잡는
검사기는 규칙이 없는 것과 같기 때문이다.
"""
import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def checker():
    spec = importlib.util.spec_from_file_location(
        "check_consistency", PROJECT_ROOT / "tools" / "check_consistency.py")
    module = importlib.util.module_from_spec(spec)
    # dataclass가 정의될 때 sys.modules에서 자기 모듈을 찾으므로 먼저 등록한다.
    sys.modules["check_consistency"] = module
    spec.loader.exec_module(module)
    return module


def _problems(checker, text, tmp_path):
    """임시 버전 이력을 물려 검사 결과만 받는다."""
    fake = tmp_path / "버전관리.md"
    fake.write_text(text, encoding="utf-8")
    original = checker.CHANGELOG
    checker.CHANGELOG = fake
    try:
        return checker.check_version_numbers()
    finally:
        checker.CHANGELOG = original


def test_지금_버전이력은_통과한다(checker):
    """실제 문서가 규칙을 만족해야 한다 — 아니면 검사기를 켤 수 없다."""
    assert checker.check_version_numbers() == []


def test_새_번호가_겹치면_잡는다(checker, tmp_path):
    """네 번 났던 그 상황 — 두 세션이 같은 번호를 쓴다."""
    text = "## 1.26.95 - 하나\n\n본문\n\n## 1.26.95 - 둘\n\n본문\n"
    problems = _problems(checker, text, tmp_path)

    assert len(problems) == 1
    assert "1.26.95" in problems[0]


def test_새_겹침은_주석을_달아도_잡는다(checker, tmp_path):
    """예외가 아닌 번호는 설명 한 줄로 정당해지지 않는다 — 번호를 밀어야 한다.

    이게 없으면 다섯 번째 겹침도 주석만 붙이면 통과해 검사가 무의미해진다.
    """
    text = ("## 1.26.95 - 하나\n\n> 번호 겹침: 설명\n\n"
            "## 1.26.95 - 둘\n\n> 번호 겹침: 설명\n")
    problems = _problems(checker, text, tmp_path)

    assert len(problems) == 1
    assert "다음 번호로 미세요" in problems[0]


def test_남겨_두기로_한_겹침은_통과시킨다(checker, tmp_path):
    """1.26.88의 판단 — 밀면 다른 문서의 참조가 깨져 주석으로 갈음했다."""
    text = ("## 1.26.47 - 하나\n\n> 번호 겹침: 설명\n\n"
            "## 1.26.47 - 둘\n\n> 번호 겹침: 설명\n")

    assert _problems(checker, text, tmp_path) == []


def test_예외라도_주석이_없으면_잡는다(checker, tmp_path):
    """예외 목록이 '설명 없는 겹침'까지 삼켜 버리면 안 된다."""
    text = ("## 1.26.47 - 주석이 없다\n\n본문\n\n"
            "## 1.26.47 - 이쪽만 있다\n\n> 번호 겹침: 설명\n")
    problems = _problems(checker, text, tmp_path)

    assert len(problems) == 1
    assert "주석이 없습니다" in problems[0]


def test_최신이_맨_위가_아니면_잡는다(checker, tmp_path):
    """버전관리.md 6행이 '최신 버전이 맨 위'라고 선언한다."""
    text = "## 1.26.88 - 낮은 쪽\n\n본문\n\n## 1.26.89 - 높은 쪽\n\n본문\n"
    problems = _problems(checker, text, tmp_path)

    assert len(problems) == 1
    assert "최신이 맨 위" in problems[0]


def test_정상적인_내림차순은_조용하다(checker, tmp_path):
    text = "## 1.26.89 - 최신\n\n본문\n\n## 1.26.88 - 이전\n\n본문\n"

    assert _problems(checker, text, tmp_path) == []


def test_예외가_낀_자리의_정렬은_넘어간다(checker, tmp_path):
    """겹침을 남겨 둔 탓에 생긴 뒤엉킴까지 정렬 위반으로 세면 거짓 경보다."""
    text = ("## 1.26.48 - 하나\n\n> 번호 겹침: 설명\n\n"
            "## 1.26.47 - 하나\n\n> 번호 겹침: 설명\n\n"
            "## 1.26.48 - 둘\n\n> 번호 겹침: 설명\n\n"
            "## 1.26.47 - 둘\n\n> 번호 겹침: 설명\n")

    assert _problems(checker, text, tmp_path) == []


def test_고칠_수_없는_커밋을_넘기되_조용히_넘기지_않는다(checker):
    """검사기가 **늘 빨간 상태**가 되면 사람이 무시하기 시작한다 (1.26.143).

    `check_commit_prefix()`는 이력 전체를 훑으므로, 이미 푸시돼 고칠 수 없는
    한 건이 **매 실행마다** 뜬다. 이 저장소가 *"통과만 하고 아무것도 못 잡는
    검사기는 규칙이 없는 것과 같다"* 고 적은 것의 뒷면이다 — 늘 빨간 검사기도
    같은 자리로 간다.

    버전 겹침에는 이미 `ALLOWED_DUPES`라는 같은 장치가 있었다. 커밋 쪽에만
    없었다.

    ⚠️ 여기서 지키는 것은 **면죄부가 되지 않는가**다: 넘긴 것은 사유와 함께
    적혀 있어야 하고, 검사기가 몇 건을 넘겼는지 말해야 한다.
    """
    import io
    import contextlib

    assert hasattr(checker, "ALLOWED_PREFIX_MISMATCH"), "넘김 목록이 없다"
    allowed = checker.ALLOWED_PREFIX_MISMATCH
    assert allowed, "목록이 비었다면 이 시험이 무엇을 지키는지 알 수 없다"

    for sha, reason in allowed.items():
        assert len(sha) >= 7, f"{sha}: 커밋 해시가 아니다"
        assert reason.strip(), f"{sha}: 왜 넘기는지 적혀 있지 않다"

    # 넘긴 건수를 **소리 내어** 말해야 한다. 조용히 빼면 목록이 늘어도 아무도 모른다.
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        problems = checker.check_commit_prefix()
    printed = buffer.getvalue()
    assert "넘긴 커밋" in printed, "무엇을 넘겼는지 밝히지 않는다"
    assert str(len(allowed)) in printed

    # 그리고 그 커밋들이 실제로 걸러졌는지 — 보고에 남아 있으면 안 된다.
    for sha in allowed:
        assert not any(sha in p for p in problems), f"{sha}를 넘긴다고 해 놓고 보고한다"


def test_설정_상수를_어떤_표기로_적든_읽어_낸다(checker, tmp_path):
    """진실의 출처를 **텍스트로** 읽으므로 표기가 바뀌면 조용히 멈춘다 (1.26.144).

    `_config_value()`는 `project_config.py`를 import하지 않고 정규식으로 읽는다.
    그래서 코드 쪽 표기가 바뀌면 값이 틀리는 게 아니라 **검사 자체가 사라진다** —
    실제로 21곳을 `env_float(...)`로 묶자 `z`·`γ` 검사가 통째로 멈췄고,
    "진실의 출처를 읽지 못했습니다"만 남았다.

    여기서 지키는 것은 **지금 쓰는 세 표기를 다 읽는가**다.
    """
    fake = tmp_path / "project_config.py"
    fake.write_text(
        "DEFAULT_FLEET_SIZE = 21\n"
        'OLD_STYLE = float(os.getenv("PBR_OLD_STYLE", "1.5"))\n'
        'TARGET_Z = env_float("PBR_TARGET_Z", 1.99)\n'
        'CLUSTER_SEED = env_int("PBR_CLUSTER_SEED", 42)\n'
        "ALIASED = DEFAULT_FLEET_SIZE\n",
        encoding="utf-8")

    original = checker.ROOT
    checker.ROOT = tmp_path
    try:
        assert checker._config_value("DEFAULT_FLEET_SIZE") == "21"   # 날 숫자
        assert checker._config_value("OLD_STYLE") == "1.5"           # 옛 표기
        assert checker._config_value("TARGET_Z") == "1.99"           # 헬퍼(실수)
        assert checker._config_value("CLUSTER_SEED") == "42"         # 헬퍼(정수)
        assert checker._config_value("ALIASED") == "21"              # 별칭
    finally:
        checker.ROOT = original


def test_테스트_개수를_어떤_표기로_적든_잡는다(checker, tmp_path):
    """숫자와 낱말 사이에 무엇이 끼어도 잡는가 (1.26.145).

    `test_설정_상수를_어떤_표기로_적든_읽어_낸다`가 **코드 쪽**에 세운 것을
    여기서는 **문서 쪽**에 세운다. 값 검사는 문서를 정규식으로 읽으므로,
    같은 사실을 다른 표기로 적으면 틀렸다고 답하는 게 아니라 **아무 말도
    하지 않는다** — 그 자리는 검사되고 있다고 착각되는 만큼 더 오래 낡는다.

    실제로 났다: 1.26.144가 개수를 691로 올릴 때 문서 14줄이 따라갔는데
    목차 표의 *"**테스트** — 684개가 무엇을 지키는지"* 한 줄만 남았고,
    굵게 표시와 줄표가 사이에 끼었다는 이유로 검사기는 **0건**이라 답했다.

    지키는 것은 **지금 쓰는 표기를 다 읽는가**다. 표기를 늘리려거든
    여기 한 줄을 먼저 늘려라.
    """
    쓰임 = [
        "테스트 111개 통과",                    # 붙여 쓴 것
        "**테스트** — 111개가 무엇을 지키는지",  # 굵게 표시 + 줄표 (놓쳤던 것)
        "테스트는 111개다",                     # 조사가 붙은 것
        "| **테스트** | 111개 |",               # 표 칸
        "pytest 111개",
    ]
    doc = tmp_path / "아무문서.md"
    doc.write_text("\n".join(쓰임), encoding="utf-8")

    원래_ROOT, 원래_FACTS = checker.ROOT, checker.FACTS
    개수 = next(f for f in checker.FACTS if f.name == "테스트 개수")
    checker.ROOT = tmp_path
    # 진실을 111로 물려 둔다 — 실제 스위트를 수집하면 느리고, 개수가 늘 때마다
    # 이 테스트가 같이 흔들린다. 여기서 묻는 것은 개수가 아니라 **표기**다.
    checker.FACTS = [replace(개수, truth=lambda: "111")]
    try:
        assert checker.check_values() == [], "지금 쓰는 표기인데 못 읽는다"

        # 그리고 **정말 걸러 내는가.** 통과만 하는 검사는 규칙이 없는 것과 같다.
        doc.write_text("\n".join(s.replace("111", "222") for s in 쓰임),
                       encoding="utf-8")
        problems = checker.check_values()
        assert len(problems) == len(쓰임), (
            f"{len(쓰임)}줄이 다 틀렸는데 {len(problems)}건만 잡는다:\n"
            + "\n".join(problems))
    finally:
        checker.ROOT, checker.FACTS = 원래_ROOT, 원래_FACTS


def test_파일별_테스트_개수도_대조한다(checker, tmp_path):
    """합계만 보면 **파일별 숫자는 마음대로 낡는다** (1.26.147).

    값 검사는 스위트 **합계**(696)만 본다. 그런데 README와 TESTING.md는
    파일별 개수도 싣는데, 그 표기가 달라 어느 패턴에도 안 걸렸다:

        README      `tests/test_webapp.py` (37)   <- '개'가 없다
        TESTING.md  | [tests/test_webapp.py](…) | 121 |   <- 칸 안의 맨 숫자

    실측으로 README 3곳이 낡아 있었고 `test_webapp.py`는 **37 → 121**,
    3.3배였다. 합계는 늘 맞았으므로 검사기는 0건이라 답했다.

    📌 **같은 유형이 세 번째다.** 1.26.119는 *"확인"이 붙은 것만* 잡아
    `pbr-run`의 "607개 통과가 기준선"을 놓쳤고, 1.26.145는 굵게 표시와
    줄표가 끼었다는 이유로 README 목차 한 줄을 놓쳤다. 매번 값이 틀린 게
    아니라 **검사가 그 자리에 닿지 않았다.**

    여기서 지키는 것은 **파일별 숫자가 실제 수집과 같은가**다.
    """
    문서 = tmp_path / "안내.md"
    문서.write_text(
        "- `tests/test_alpha.py` (2) — 설명\n"
        "| [tests/test_beta.py](../../tests/test_beta.py) | 3 | 설명 |\n",
        encoding="utf-8")

    원래_ROOT = checker.ROOT
    원래_계산 = checker._pytest_counts
    checker.ROOT = tmp_path
    # 진실을 물려 둔다 — 실제 스위트를 수집하면 느리고, 파일이 자랄 때마다
    # 이 테스트가 함께 흔들린다. 묻는 것은 개수가 아니라 **대조하는가**다.
    checker._pytest_counts = lambda: {
        "tests": "5", "files": "2",
        "per_file": {"tests/test_alpha.py": 2, "tests/test_beta.py": 3},
    }
    try:
        assert checker.check_test_counts() == [], "맞는데 틀렸다고 한다"

        # 그리고 **정말 걸러 내는가.** 두 표기를 각각 어긋나게 해 본다.
        문서.write_text(
            "- `tests/test_alpha.py` (99) — 설명\n"
            "| [tests/test_beta.py](../../tests/test_beta.py) | 77 | 설명 |\n",
            encoding="utf-8")
        problems = checker.check_test_counts()
        assert len(problems) == 2, (
            f"두 표기가 다 어긋났는데 {len(problems)}건만 잡는다:\n"
            + "\n".join(problems))
        붙은글 = "\n".join(problems)
        assert "test_alpha" in 붙은글 and "test_beta" in 붙은글, (
            "어느 파일이 어긋났는지 말해 주지 않는다")
    finally:
        checker.ROOT = 원래_ROOT
        checker._pytest_counts = 원래_계산


def test_지금_문서의_파일별_개수는_맞다(checker):
    """현행 문서가 실제로 통과하는지 — 회귀 감지용."""
    assert checker.check_test_counts() == []


def test_문서끼리_건_링크가_살아_있는지_본다(checker, tmp_path):
    """상대 링크가 실제 파일을 가리키는지 본다 (1.26.148).

    문서 58개가 서로를 촘촘히 가리킨다. 파일이 폴더를 옮기면 링크는 **조용히
    죽는다** — 오류가 안 나고, 누른 사람만 404를 본다. 실제로 버전관리.md의
    `[GLOSSARY](GLOSSARY.md)`가 그랬다(정본은 `docs/GLOSSARY.md`라 `../`가
    필요한데 빠져 있었다). 같은 문서 다른 두 곳은 `../GLOSSARY.md`로 맞게
    적혀 있어, **한 문서 안에서도 갈려 있었다.**

    ⚠️ **코드 담장(```) 안은 보지 않는다.** 표기법을 설명하려고 링크 모양을
    그대로 인용하는 자리가 있어서(1.26.147의 `| [tests/…](…) | 121 |`),
    그것까지 링크로 세면 검사기가 오탐만 늘어놓는다.
    """
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "있다.md").write_text("본문", encoding="utf-8")
    좋은글 = ("[이웃](있다.md) · [바깥](../밖.md)\n"
              "```\n"
              "[모양만](…) 코드 담장 안이라 안 센다\n"
              "```\n")
    (tmp_path / "밖.md").write_text("본문", encoding="utf-8")
    (tmp_path / "docs" / "글.md").write_text(좋은글, encoding="utf-8")

    원래 = checker.ROOT
    checker.ROOT = tmp_path
    try:
        assert checker.check_links() == [], "멀쩡한 링크를 깨졌다고 한다"

        # 그리고 **정말 걸러 내는가.**
        (tmp_path / "docs" / "글.md").write_text(
            "[없다](없는파일.md) · [폴더가틀림](GLOSSARY.md)\n", encoding="utf-8")
        problems = checker.check_links()
        assert len(problems) == 2, (
            f"둘 다 깨졌는데 {len(problems)}건만 잡는다:\n" + "\n".join(problems))
        붙임 = "\n".join(problems)
        assert "없는파일.md" in 붙임 and "GLOSSARY.md" in 붙임, (
            "어느 링크가 깨졌는지 말해 주지 않는다")
    finally:
        checker.ROOT = 원래


def test_지금_문서의_링크는_다_살아_있다(checker):
    """현행 문서가 실제로 통과하는지 — 회귀 감지용."""
    assert checker.check_links() == []


def test_면제_목록이_없는_파일을_가리키지_않는다(checker):
    """`HISTORY_DOCS`가 **실재하는 문서**를 가리키는지 본다 (1.26.148).

    이 목록은 *"이 문서는 옛 값을 싣는 게 정상이니 값 검사에서 빼라"* 는
    뜻이다. 그런데 파일이 폴더를 옮기면 목록만 옛 경로로 남고, **면제는
    조용히 죽는다** — 그 문서는 이제 검사 대상인데 아무도 모른다.

    실제로 `docs/기록/두_PC_작업.md`가 1.26.121에서 `docs/구현/`으로 옮겨져
    면제가 죽어 있었다. 지금은 그 문서에 검사 대상 값이 없어 탈이 안 났을
    뿐이다 — 값이 하나 들어오는 순간 이유 없이 빨간 줄이 뜬다.
    """
    없는것 = [d for d in checker.HISTORY_DOCS if not (checker.ROOT / d).exists()]
    assert not 없는것, (
        f"면제 목록이 없는 파일을 가리킨다(옮겼거나 지웠다): {없는것}")


def test_고친_규약을_문서가_현재형으로_가르치지_않는지_본다(checker, tmp_path):
    """산문으로 적힌 낡은 규약은 값 검사도 링크 검사도 못 잡는다 (1.26.151).

    🔴 실제로 났다. `WEBAPP.md`가 *"최신 = `run_label` 최대값 · 취약점 없음"* 이라고
    **1.26.125에서 고친 바로 그 버그**를 현재 동작으로 적어 두었고, 같은 표가
    `DB_PLAN.md`에도 있었다. `DB_SCHEMA.md`만 따라가 고쳐져 **한 저장소가 두 말을
    하고 있었다.** 숫자가 아니라 산문이라 기존 검사 어디에도 안 걸렸다.

    ⚠️ 낡은 숫자보다 나쁘다 — 취약점을 *"없음"* 이라 적어 두면 읽는 사람이
    **안심하고** 옛 규약을 따라 한다.

    여기서 지키는 것은 둘이다: **지금 문서가 통과하는가**, 그리고
    **정말 걸러 내는가**(통과만 하는 검사는 규칙이 없는 것과 같다).
    """
    assert checker.check_stale_claims() == [], "현행 문서가 낡은 규약을 가르친다"

    # 면제 목록이 실재하는 파일을 가리키는지 — 옮기면 면제가 조용히 헛돈다.
    for claim in checker.STALE_CLAIMS:
        for 면제 in claim["면제"]:
            assert (checker.ROOT / 면제).exists(), f"면제 대상이 없다: {면제}"
        증거파일, _ = claim["증거"]
        assert (checker.ROOT / 증거파일).exists(), f"증거 파일이 없다: {증거파일}"

    # 그리고 **정말 잡는가.** 네 줄을 심어 둘만 물어야 한다 —
    # 다 물면 정정문까지 잡는 것이고, 하나도 안 물면 규칙이 없는 것과 같다.
    원래 = checker.ROOT
    (tmp_path / "docs" / "구현").mkdir(parents=True)
    (tmp_path / "db.py").write_text("created_at", encoding="utf-8")
    (tmp_path / "docs" / "구현" / "아무거나.md").write_text(
        "기준은 DB의 **`run_label`** 최대값입니다.\n"          # ← 물어야 한다
        "최신은 `MAX(run_label)`으로 고릅니다.\n"               # ← 물어야 한다
        "🔴 **`MAX(run_label)`이 아닙니다.** 지금은 시각으로 고릅니다.\n"  # 정정문
        "> `MAX(run_label)`은 영영 실험을 가리켰습니다.\n",     # 인용(옛 설명)
        encoding="utf-8")
    checker.ROOT = tmp_path
    try:
        문제 = checker.check_stale_claims()
        assert len(문제) == 2, (
            f"낡은 줄 둘·정정문 둘을 심었는데 {len(문제)}건 잡는다:\n"
            + "\n".join(문제))
        붙잡힌_줄 = " ".join(문제)
        assert "아닙니다" not in 붙잡힌_줄, "정정문을 낡은 것으로 잡는다"
        assert "영영" not in 붙잡힌_줄, "인용문을 낡은 것으로 잡는다"
    finally:
        checker.ROOT = 원래


# ──────────────────────────────────────────── 그림·표가 원고에 실렸는가 (1.26.257)
#
# 🔴 실제로 났다. 6장 그림 셋은 **파일도 있고 초안에도 실려** 있었는데 원고와
#    그림 차례 어디에도 없어, 논문의 핵심 장이 그림 0개로 남았다. 자리 표시
#    규칙(원고 README 4-1)은 *아직 만들지 않은 자료*만 세므로 이것을 못 잡는다.

def _그림_저장소(tmp_path, 장, 앞붙이, 파일들):
    """원고·앞붙이·그림 폴더만 있는 작은 저장소를 만든다."""
    원고 = tmp_path / "docs" / "연구" / "논문"
    원고.mkdir(parents=True)
    (원고 / "6장_성능평가.md").write_text(장, encoding="utf-8")
    (원고 / "앞붙이.md").write_text(앞붙이, encoding="utf-8")
    그림 = tmp_path / "docs" / "연구" / "초안" / "그림"
    그림.mkdir(parents=True)
    for 이름 in 파일들:
        (그림 / 이름).write_bytes(b"")
    return tmp_path


def _그림_문제(checker, tmp_path, 장, 앞붙이, 파일들):
    원래 = checker.ROOT
    checker.ROOT = _그림_저장소(tmp_path, 장, 앞붙이, 파일들)
    try:
        return checker.check_figures()
    finally:
        checker.ROOT = 원래


_장_한쪽 = """# 제6장

[그림 6-1]은 편익과 대가를 함께 보인다.

![그림 6-1](../초안/그림/그림6-1_편익과_대가.png)

[그림 6-1] 재배치의 편익과 대가 (12개월 평균)
"""
_앞붙이_한쪽 = """## 6. 그림 차례

| 번호 | 제목 |
| --- | --- |
| [그림 6-1] | 재배치의 편익과 대가 |
"""


def test_지금_원고의_그림과_표는_다_실려_있다(checker):
    """실제 문서가 통과해야 검사를 켤 수 있다."""
    assert checker.check_figures() == []


def test_만들어_두고_원고에_싣지_않은_그림을_잡는다(checker, tmp_path):
    문제 = _그림_문제(checker, tmp_path, _장_한쪽, _앞붙이_한쪽,
                    ["그림6-1_편익과_대가.png", "그림6-2_결품분포.png"])
    assert len(문제) == 1, 문제
    assert "그림6-2_결품분포.png" in 문제[0]
    assert "싣지 않았습니다" in 문제[0]


def test_그림_차례에_빠진_것을_잡는다(checker, tmp_path):
    문제 = _그림_문제(checker, tmp_path, _장_한쪽, "## 6. 그림 차례\n",
                    ["그림6-1_편익과_대가.png"])
    assert any("그림 차례에 없습니다" in p for p in 문제), 문제


def test_다_그린_그림에_남은_자리_표시를_잡는다(checker, tmp_path):
    앞붙이 = _앞붙이_한쪽.replace("| 재배치의 편익과 대가 |",
                              "| 재배치의 편익과 대가 *(자리)* |")
    문제 = _그림_문제(checker, tmp_path, _장_한쪽, 앞붙이,
                    ["그림6-1_편익과_대가.png"])
    assert any("자리" in p for p in 문제), 문제


def test_표_번호로_시작하는_본문_문장을_캡션으로_읽지_않는다(checker, tmp_path):
    """처음 돌렸을 때 이것으로 다섯 건을 잘못 잡았다.

    원고는 `<표 2-1>은 …`처럼 표 번호로 문장을 시작한다. 줄머리만 보면 캡션과
    구분되지 않으므로 **자리로 가른다** — 표 캡션은 바로 아래가 표다.
    """
    장 = """# 제6장

<표 6-1>은 비교한 방법을 정리한 것이다.

<표 6-1> 비교한 다섯 가지 방법

| 기호 | 방법 |
| --- | --- |
| P | 제안 방법 |
"""
    앞붙이 = """## 7. 표 차례

| 번호 | 제목 |
| --- | --- |
| <표 6-1> | 비교한 다섯 가지 방법 |
"""
    assert _그림_문제(checker, tmp_path, 장, 앞붙이, []) == []


# ──────────────────────────────────── 파생 검사 — 정본까지 보고, 경계를 문다 (1.26.259)

def _파생_문제(checker, tmp_path, 파생, 원본들):
    """가짜 논문 폴더를 만들고 정본 결론/초록 검사를 돌린다."""
    원고 = tmp_path / "docs" / "연구" / "논문"
    원고.mkdir(parents=True)
    (원고 / "9장_결론.md").write_text(파생, encoding="utf-8")
    for 이름, 내용 in 원본들.items():
        (원고 / 이름).write_text(내용, encoding="utf-8")
    원래_root, 원래_docs = checker.ROOT, checker.DERIVED_DOCS
    checker.ROOT = tmp_path
    checker.DERIVED_DOCS = {"docs/연구/논문/9장_결론.md": "CHAPTERS"}
    try:
        return checker.check_derived()
    finally:
        checker.ROOT, checker.DERIVED_DOCS = 원래_root, 원래_docs


def test_정본_결론은_본문_장_전체를_원본으로_본다(checker, tmp_path):
    """결론은 6장만이 아니라 1~8장을 압축한다 — 5장에서 온 값도 통과해야 한다."""
    문제 = _파생_문제(
        checker, tmp_path,
        "안전계수는 1.99로 정하였고 결품은 0.18~0.28시간 낮다.",
        {"5장_파라미터결정.md": "안전계수 1.99를 고른 근거는 커버리지다.",
         "6장_성능평가.md": "그리디 대비 0.18~0.28시간 낮다."})
    assert 문제 == [], 문제


def test_원본에_없는_수치는_정본에서도_잡는다(checker, tmp_path):
    문제 = _파생_문제(
        checker, tmp_path,
        "그리디는 회차당 251대를 옮긴다.",
        {"6장_성능평가.md": "옮긴 대수는 표에 있다."})
    assert len(문제) == 1 and "251" in 문제[0], 문제


def test_부분_문자열로_통과시키지_않는다(checker, tmp_path):
    """🔴 실제로 났다 — 9장의 '251대'가 <표 5-7>의 **251.4** 안에서 발견돼 통과했다."""
    문제 = _파생_문제(
        checker, tmp_path,
        "그리디는 회차당 251대를 옮긴다.",
        {"5장_파라미터결정.md": "| 2.33 | 84.0 | 251.4 | 429.3 |"})
    assert len(문제) == 1 and "251" in 문제[0], 문제


# ────────────────────────── 인용과 참고문헌이 서로를 가리키는가 (1.26.260)
#
# 🔴 실제로 났다. 참고문헌은 도로 이동시간의 출처로 *티맵모빌리티 TMAP API*를
#    싣는데 원고는 <표 4-1>에서도 *"상용 경로 안내 API"* 라고만 적어, 그 항목만
#    본문 어디에도 닿지 않았다. 같은 표의 다른 행은 출처를 이름으로 적는다.

def _인용_문제(checker, tmp_path, 참고문헌, 본문들):
    """참고문헌과 본문 몇 쪽만 있는 작은 원고를 만들어 검사를 돌린다."""
    원고 = tmp_path / "docs" / "연구" / "논문"
    원고.mkdir(parents=True)
    (원고 / "참고문헌.md").write_text(참고문헌, encoding="utf-8")
    for 이름, 내용 in 본문들.items():
        (원고 / 이름).write_text(내용, encoding="utf-8")
    원래 = checker.ROOT
    checker.ROOT = tmp_path
    try:
        return checker.check_citations()
    finally:
        checker.ROOT = 원래


_참고문헌 = """# 참고문헌

김영일 (2022, 12월 13일). [동행취재] 타슈2 수거 담당자의 바람. *중도일보*.

Raviv, T., Tzur, M., & Forma, I. A. (2013). Static repositioning in a
bike-sharing system. *EURO Journal on Transportation and Logistics*, 2, 187-229.

티맵모빌리티. *TMAP API*. https://tmapapi.tmapmobility.com/
"""


def test_서술형과_괄호형_인용을_모두_짝짓는다(checker, tmp_path):
    문제 = _인용_문제(checker, tmp_path, _참고문헌, {
        "2장_관련연구.md": "Raviv 외(2013)는 두 가지 정식화를 제시하였다.",
        "8장_한계와_향후과제.md": "7인 1개 조로 운영된다고 전한다(김영일, 2022).",
        "4장_시스템설계.md": "| 도로 이동시간 | 티맵모빌리티 TMAP API | 100구간 |",
    })
    assert 문제 == [], 문제


def test_참고문헌에_없는_인용을_잡는다(checker, tmp_path):
    문제 = _인용_문제(checker, tmp_path, _참고문헌, {
        "2장_관련연구.md": ("Raviv 외(2013)와 Schuijbroek 외(2017)를 보라."
                            " 7인 1개 조이다(김영일, 2022)."),
        "4장_시스템설계.md": "티맵모빌리티 TMAP API로 수집하였다.",
    })
    assert len(문제) == 1 and "Schuijbroek(2017)" in 문제[0], 문제


def test_아무도_인용하지_않는_참고문헌을_잡는다(checker, tmp_path):
    문제 = _인용_문제(checker, tmp_path, _참고문헌, {
        "2장_관련연구.md": "Raviv 외(2013)는 두 가지 정식화를 제시하였다.",
        "4장_시스템설계.md": "티맵모빌리티 TMAP API로 수집하였다.",
    })
    assert len(문제) == 1 and "김영일 2022" in 문제[0], 문제


def test_이름을_적지_않은_자료_출처를_잡는다(checker, tmp_path):
    """🔴 회귀 — 본문이 '상용 경로 안내 API'라고만 적으면 참고문헌이 떠 있게 된다."""
    문제 = _인용_문제(checker, tmp_path, _참고문헌, {
        "2장_관련연구.md": "Raviv 외(2013)를 보라. 7인 1개 조이다(김영일, 2022).",
        "4장_시스템설계.md": "| 도로 이동시간 | 상용 경로 안내 API | 100구간 |",
    })
    assert len(문제) == 1 and "티맵모빌리티" in 문제[0], 문제


def test_저자와_연도가_줄바꿈으로_갈려도_통과한다(checker, tmp_path):
    """🔴 오탐 회귀 — 줄 단위로 읽으면 멀쩡한 인용 스무 건을 짝이 없다고 물었다."""
    문제 = _인용_문제(checker, tmp_path, _참고문헌, {
        "2장_관련연구.md": "두 가지 정식화를 제시한 것은 Raviv\n외(2013)이다.",
        "8장_한계와_향후과제.md": "7인 1개 조로 운영된다(김영일,\n2022).",
        "4장_시스템설계.md": "티맵모빌리티 TMAP API로 수집하였다.",
    })
    assert 문제 == [], 문제


# ─────────────────── 부록 B의 발췌가 지금 코드와 같은가 (1.26.261)
#
# 학과 양식이 요구하는 핵심 코드 부록은 복사본이라, 원본이 바뀌어도 아무 일도
# 일어나지 않는다. 심사자가 부록과 저장소를 대조하면 바로 드러나는 자리다.

def test_지금_부록_발췌는_원본과_같다(checker):
    assert checker.check_appendix_code() == []


def _부록_문제(checker, tmp_path, 문서, 소스들):
    """가짜 부록과 원본 파일을 만들고 검사를 돌린다."""
    (tmp_path / "docs" / "연구" / "논문").mkdir(parents=True)
    (tmp_path / "docs" / "연구" / "논문" / "부록_핵심코드.md").write_text(
        문서, encoding="utf-8")
    for 이름, 내용 in 소스들.items():
        경로 = tmp_path / 이름
        경로.parent.mkdir(parents=True, exist_ok=True)
        경로.write_text(내용, encoding="utf-8")
    원래 = checker.ROOT
    checker.ROOT = tmp_path
    try:
        return checker.check_appendix_code()
    finally:
        checker.ROOT = 원래


_원본 = "def f(x):\n    score = x / (x + 1e-6)\n    return score\n"


def test_들여쓰기만_덜어_낸_발췌는_통과한다(checker, tmp_path):
    문제 = _부록_문제(checker, tmp_path, (
        "`pipeline/a.py`\n\n```python\n"
        "# 식을 읽히려고 부록에서 새로 단 주석\n"
        "score = x / (x + 1e-6)\n"
        "return score\n"
        "```\n"), {"pipeline/a.py": _원본})
    assert 문제 == [], 문제


def test_원본과_다른_줄을_잡는다(checker, tmp_path):
    """🔴 회귀 — 코드가 바뀌었는데 부록만 옛 값을 싣고 있으면 잡아야 한다."""
    문제 = _부록_문제(checker, tmp_path, (
        "`pipeline/a.py`\n\n```python\n"
        "score = x / (x + 1e-9)\n"
        "```\n"), {"pipeline/a.py": _원본})
    assert len(문제) == 1 and "1e-9" in 문제[0], 문제


def test_파일_이름_없는_발췌를_잡는다(checker, tmp_path):
    문제 = _부록_문제(checker, tmp_path, (
        "발췌를 싣는다.\n\n```python\nscore = x / (x + 1e-6)\n```\n"),
        {"pipeline/a.py": _원본})
    assert len(문제) == 1 and "파일 이름이 없습니다" in 문제[0], 문제


def test_없는_파일을_가리키면_잡는다(checker, tmp_path):
    문제 = _부록_문제(checker, tmp_path, (
        "`pipeline/b.py`\n\n```python\nscore = x / (x + 1e-6)\n```\n"),
        {"pipeline/a.py": _원본})
    assert len(문제) == 1 and "없는 파일" in 문제[0], 문제
