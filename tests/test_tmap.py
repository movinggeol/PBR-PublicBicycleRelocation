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


# ---------------- 경로 지도의 팝업 (수정안 15) ----------------
#
# 지도에서 지점을 누르면 뜨는 창이다. 사람이 현장에서 읽는 글이므로 파이썬 자료구조가
# 그대로 새어 나오면 안 된다.

MAIN_PATH = Path(__file__).resolve().parents[1] / "step3_map" / "main.py"


def load_main():
    """main.py를 경로로 읽는다.

    스크립트로 돌 때는 제 폴더가 sys.path에 들어가 `import module`이 되지만,
    테스트에서 경로로 읽을 때는 우리가 넣어 줘야 한다.
    """
    import sys

    folder = str(MAIN_PATH.parent)
    if folder not in sys.path:
        sys.path.insert(0, folder)
    spec = importlib.util.spec_from_file_location("step3_main", MAIN_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record(order=1, round_=1, cluster=3, action="Drop", qty=5, load=2):
    return {"cluster": cluster, "order": order, "round": round_,
            "action": action, "qty": qty, "load": load}


def test_팝업에_대괄호가_새어_나오지_않는다():
    """한 번만 들른 대여소에서 리스트가 그대로 찍혔다 — 방문 대부분이 이 경우다."""
    html = load_main().visit_popup([record()], "ST0123", 10, lambda o: "00:12:30")

    assert "[" not in html and "]" not in html
    assert "'" not in html                      # 리스트를 찍으면 따옴표도 함께 나온다
    assert "ST0123" in html and "00:12:30" in html


def test_한_번만_들른_대여소에는_방문_회차를_적지_않는다():
    """'1번째 방문'은 뜻이 없는 줄이다 — 여러 번 들를 때만 필요하다."""
    once = load_main().visit_popup([record()], "ST0123", 10, lambda o: "-")
    assert "방문 회차" not in once


def test_여러_번_들르면_회차를_구분해_이어_붙인다():
    main = load_main()
    html = main.visit_popup([record(order=2, round_=1), record(order=7, round_=2)],
                            "ST0123", 10, lambda o: f"{o}분")

    assert main.VISIT_SEPARATOR in html
    assert "1번째 방문" in html and "2번째 방문" in html
    assert "[" not in html
