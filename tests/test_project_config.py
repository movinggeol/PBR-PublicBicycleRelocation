"""숫자 환경변수 파싱 검증 (1.26.125에서 발견, 1.26.144에서 고침).

**지키려는 것은 값이 아니라 문구다.** `PBR_*` 스무 개가 날것으로
`int(os.getenv(...))`를 부르고 있었는데, 잘못된 값을 주면 `project_config`를
import 하는 순간 *"invalid literal for int() with base 10: 'abc'"* 로 죽었다 —
**어느 변수 탓인지가 그 문구에 없다.** 스무 개 중 하나를 잘못 준 사용자는
스택 추적을 읽고 줄 번호를 세는 수밖에 없었다.

그래서 이 테스트가 확인하는 것은 *"오류가 나는가"* 가 아니라
**"오류 문구에 변수 이름이 있는가"** 다.
"""
import pytest

from project_config import env_float, env_int


def test_안_주면_기본값을_쓴다(monkeypatch):
    monkeypatch.delenv("PBR_TEST_INT", raising=False)
    monkeypatch.delenv("PBR_TEST_FLOAT", raising=False)
    assert env_int("PBR_TEST_INT", 42) == 42
    assert env_float("PBR_TEST_FLOAT", 1.99) == 1.99


def test_주면_그_값을_쓴다_공백은_턴다(monkeypatch):
    monkeypatch.setenv("PBR_TEST_INT", " 7 ")
    monkeypatch.setenv("PBR_TEST_FLOAT", " 2.5 ")
    assert env_int("PBR_TEST_INT", 42) == 7
    assert env_float("PBR_TEST_FLOAT", 1.99) == 2.5


@pytest.mark.parametrize("name,value", [
    ("PBR_CLUSTER_SEED", "abc"),
    ("PBR_ADJUST_MAX_ITER", "2.5"),      # 정수 자리에 실수도 오류다
])
def test_정수가_아니면_변수_이름을_밝히고_죽는다(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError) as err:
        env_int(name, 42)
    assert name in str(err.value), "어느 변수 탓인지 문구에 있어야 한다"
    assert value in str(err.value), "무엇을 줬는지도 함께 보여야 한다"


def test_실수가_아니면_변수_이름을_밝히고_죽는다(monkeypatch):
    monkeypatch.setenv("PBR_TARGET_Z", "많이")
    with pytest.raises(ValueError) as err:
        env_float("PBR_TARGET_Z", 1.99)
    assert "PBR_TARGET_Z" in str(err.value)
    assert "많이" in str(err.value)


def test_빈_값은_기본값이_아니라_오류다(monkeypatch):
    """값을 주려다 비운 것과 아예 안 준 것을 가를 수 없다 — 조용히 도는 쪽이 나쁘다."""
    monkeypatch.setenv("PBR_TARGET_Z", "")
    with pytest.raises(ValueError) as err:
        env_float("PBR_TARGET_Z", 1.99)
    assert "PBR_TARGET_Z" in str(err.value)

# ---------------------------------------------------------------------------
# `.env`가 실제로 읽히는가 (1.26.208에서 고침)
#
# 🔴 2026-09-14 실측: `.env`에 `PBR_TARGET_Z=9.9`를 적어도 코드는 1.99를,
# `PBR_FLEET_SIZE=77`을 적어도 21을 썼다. `.env.example`이 안내하는 `PBR_*`
# 28개가 **전부 조용히 무시**되고 있었다 — `project_config`가 이들을 import
# 시점에 상수로 굳히는데, 저장소에서 `load_dotenv()`를 부르던 네 곳이 모두
# 자기 함수 안이라 **이미 늦었기** 때문이다.
#
# 하필 [수집완료_계획.md](../docs/기록/수집완료_계획.md) 2-4의 ③-1이
# *"계수를 .env에 적는다"* 라 게이트 A 채택일에 **옛 계수로 조용히 도는**
# 결함이었다. 표는 그럴듯하게 찍히고 그대로 논문에 간다.
#
# ⚠️ 시험이 실제 `.env`를 만지므로 **아무도 읽지 않는 탐침 변수만** 쓴다.
# 중간에 죽어 한 줄이 남더라도 동작이 바뀌지 않는다 — 진짜 `PBR_*`를 썼다면
# 남은 줄이 바로 위 문단의 사고를 일으킨다.
# ---------------------------------------------------------------------------
import subprocess
import sys
import textwrap
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parents[1]
_PROBE = "PBR_TEST_DOTENV_PROBE"


@pytest.fixture
def dotenv_probe():
    """`.env` 끝에 탐침 한 줄을 붙였다 되돌린다."""
    env = _ROOT / ".env"
    before = env.read_text(encoding="utf-8") if env.exists() else None
    try:
        env.write_text(f"{before or ''}\n{_PROBE}=from-dotenv\n",
                       encoding="utf-8")
        yield
    finally:
        if before is None:
            env.unlink(missing_ok=True)
        else:
            env.write_text(before, encoding="utf-8")


def _probe_in_subprocess(extra_env=None):
    """새 프로세스에서 project_config를 import한 뒤 탐침 값을 돌려준다."""
    import os as _os
    env = dict(_os.environ)
    env.pop(_PROBE, None)
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra_env or {})
    code = textwrap.dedent(f"""
        import os
        import project_config          # noqa: F401
        print(os.getenv({_PROBE!r}, ''))
    """)
    out = subprocess.run([sys.executable, "-c", code], cwd=_ROOT, env=env,
                         capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_env파일의_값이_import만으로_읽힌다(dotenv_probe):
    assert _probe_in_subprocess() == "from-dotenv"


def test_진짜_환경변수가_env파일을_이긴다(dotenv_probe):
    """`override=False`여야 한다 — 실험 하네스가 자식에게 꽂는 값이 `.env`에
    가려지면 격자 탐색이 통째로 같은 값을 돌고도 다른 값을 돈 것처럼 보인다."""
    assert _probe_in_subprocess({_PROBE: "from-environ"}) == "from-environ"


def test_env파일_적재가_첫_환경변수_읽기보다_앞선다():
    """순서가 곧 결함이었다 — 늦게 부르면 상수는 이미 굳어 있다."""
    lines = (_ROOT / "project_config.py").read_text(encoding="utf-8").splitlines()

    load_at = next(
        (i for i, ln in enumerate(lines) if ln.startswith("load_dotenv(")), None)
    assert load_at is not None, (
        "project_config.py가 모듈 수준에서 load_dotenv를 부르지 않는다 — "
        ".env의 PBR_*가 전부 무시된다")

    first_read = next(
        i for i, ln in enumerate(lines)
        if ln[:1].strip()                      # 모듈 수준(들여쓰기 없음)
        and any(k in ln for k in ("os.getenv(", "env_float(", "env_int("))
        and not ln.startswith(("def ", "#"))
    )
    assert load_at < first_read, (
        f"load_dotenv가 {load_at + 1}번째 줄인데 첫 환경변수 읽기가 "
        f"{first_read + 1}번째 줄이다 — .env가 상수에 반영되지 않는다")
