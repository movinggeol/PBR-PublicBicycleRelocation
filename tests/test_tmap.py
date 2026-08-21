"""TMAP 엔드포인트 선택과 폴백 검증 (docs/steps/step3_visualization.md).

실제 API는 일일 한도가 있는 유료 서비스라 자동 테스트에서 부르지 않는다.
대신 `requests.post`를 가로채 **어느 엔드포인트로 보냈는지**만 본다 —
검증하려는 것이 응답 내용이 아니라 선택·폴백 규칙이기 때문이다.

지키려는 규칙:
  1. 경유지가 30 이하면 routeSequential30을 먼저 쓴다(한도가 따로 잡혀 아낄 수 있다)
  2. 30을 넘으면 처음부터 routeSequential100 — 쪼개서 두 번 부르면 쿼터를 더 쓴다
  3. 30이 일일 한도를 소진하면 100으로 자동 전환하고, 그 뒤로는 30을 다시 부르지 않는다
  4. 둘 다 소진되면 TmapQuotaExceeded (호출한 쪽이 직선 경로로 대체한다)
"""
import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "step3_map" / "module.py"


def load_module():
    """step3 모듈을 경로로 직접 읽는다.

    환경변수를 import 시점에 읽으므로 테스트마다 새로 로드해야 한다.
    """
    spec = importlib.util.spec_from_file_location("step3_module", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, status_code, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload or {"features": []}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


QUOTA_BODY = '{"error":{"code":"QUOTA_EXCEEDED","message":"Limit Exceeded"}}'


def fake_post(sent, quota_exceeded=()):
    """호출된 URL을 sent에 기록한다. quota_exceeded에 든 이름은 429로 응답."""
    def _post(url, **kwargs):
        sent.append(url)
        if any(name in url for name in quota_exceeded):
            return FakeResponse(429, QUOTA_BODY)
        return FakeResponse(200, payload={"features": [{"ok": url}]})
    return _post


def _args(via_count):
    point = {"name": "s", "X": 127.3, "Y": 36.3}
    via = [{"viaPointId": str(i), "viaPointName": str(i), "viaX": 127.3, "viaY": 36.3}
           for i in range(via_count)]
    return point, point, via


@pytest.mark.parametrize("via_count, expected", [
    (0, "routeSequential30"),
    (30, "routeSequential30"),
    (31, "routeSequential100"),
    (100, "routeSequential100"),
])
def test_smallest_endpoint_that_fits_is_chosen(via_count, expected):
    """한 번에 담기는 가장 작은 엔드포인트를 고른다."""
    module = load_module()
    assert module.pick_endpoint(via_count).name == expected


def test_falls_back_to_100_when_30_is_exhausted():
    """30이 일일 한도를 소진하면 같은 요청을 100으로 다시 보낸다."""
    module = load_module()
    sent = []
    module.requests.post = fake_post(sent, quota_exceeded=("routeSequential30",))

    result = module.call_tmap_sequential(*_args(5), headers={})

    assert len(sent) == 2, "30으로 한 번, 100으로 한 번이어야 한다"
    assert sent[0].endswith("routeSequential30")
    assert sent[1].endswith("routeSequential100")
    assert result["features"][0]["ok"].endswith("routeSequential100")


def test_exhausted_endpoint_is_not_retried():
    """한 번 소진된 엔드포인트는 다음 요청에서 건너뛴다 (하루가 지나야 풀린다)."""
    module = load_module()
    sent = []
    module.requests.post = fake_post(sent, quota_exceeded=("routeSequential30",))

    module.call_tmap_sequential(*_args(5), headers={})   # 여기서 30이 소진된다
    sent.clear()
    module.call_tmap_sequential(*_args(5), headers={})

    assert sent == [module.ENDPOINTS[1].url], "두 번째 요청은 100으로만 가야 한다"
    assert module.pick_endpoint(5).name == "routeSequential100"


def test_all_endpoints_exhausted_raises():
    """둘 다 소진되면 예외 — 호출한 쪽이 직선 경로로 대체한다."""
    module = load_module()
    sent = []
    module.requests.post = fake_post(sent, quota_exceeded=("routeSequential",))

    with pytest.raises(module.TmapQuotaExceeded):
        module.call_tmap_sequential(*_args(5), headers={})

    assert module.available_endpoints() == []


def test_fixed_url_does_not_fall_back(monkeypatch):
    """PBR_TMAP_URL로 엔드포인트를 지정하면 폴백하지 않는다(수동 검증용 탈출구)."""
    monkeypatch.setenv("PBR_TMAP_URL",
                       "https://apis.openapi.sk.com/tmap/routes/routeSequential30")
    module = load_module()
    sent = []
    module.requests.post = fake_post(sent, quota_exceeded=("routeSequential30",))

    with pytest.raises(module.TmapQuotaExceeded):
        module.call_tmap_sequential(*_args(5), headers={})

    assert len(sent) == 1, "지정한 엔드포인트로 한 번만 시도해야 한다"


def test_chunking_uses_the_chosen_endpoint_limit():
    """경유지 40개는 100으로 한 번에 간다 — 30으로 쪼개면 쿼터를 두 번 쓴다."""
    module = load_module()
    sent = []
    module.requests.post = fake_post(sent)

    module.call_tmap_chunked(*_args(40), headers={})

    assert len(sent) == 1
    assert sent[0].endswith("routeSequential100")


def test_reset_clears_exhausted_endpoints():
    """호출 수 초기화는 소진 표시도 함께 지운다(다음 실행을 오염시키지 않도록)."""
    module = load_module()
    sent = []
    module.requests.post = fake_post(sent, quota_exceeded=("routeSequential30",))
    module.call_tmap_sequential(*_args(5), headers={})

    module.reset_call_count()

    assert module.call_count() == 0
    assert module.pick_endpoint(5).name == "routeSequential30"


def test_budget_stops_calls_before_quota_is_spent(monkeypatch):
    """호출 예산을 다 쓰면 HTTP 요청 전에 멈춘다 (쿼터 소모 0)."""
    monkeypatch.setenv("PBR_TMAP_MAX_CALLS", "0")
    module = load_module()
    sent = []
    module.requests.post = fake_post(sent)

    with pytest.raises(module.TmapBudgetExceeded):
        module.call_tmap_sequential(*_args(5), headers={})

    assert sent == [], "예산 초과 시 요청을 보내면 안 된다"
