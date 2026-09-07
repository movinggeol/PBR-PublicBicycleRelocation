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
