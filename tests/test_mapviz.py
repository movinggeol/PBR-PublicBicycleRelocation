"""지도 범례·팔레트(`mapviz.py`) 검증.

세 지도(step1 군집·step3 경로·step4 재고 현황)의 범례를 한 함수로 모으고
군집 팔레트를 색맹 안전 8색(Okabe & Ito, 2008)으로 바꿨다. 시안으로 만들어
실제로 띄워 비교한 뒤 채택했다(1.26.79 조사 → 1.26.80 적용).

여기서 지키는 것은 셋이다:

1. **군집마다 색이 달라야 한다** — 처음 시안은 8색을 순환하며 어둡게만
   만들었는데, 검정(#000000)은 어두워지지 않아 **군집 7과 15가 똑같은
   검정**으로 나왔다(실측: RGB 거리 0). 실제 산출물이 18개 군집을 그리므로
   지도에서 두 군집이 한 색이면 범례가 설명하는 구분이 화면에 없는 것이다.
2. **색만으로 뜻을 전하지 않는다**(docs/구현/DESIGN.md) — 범례 상자에는
   배지와 함께 **글자 설명**이 반드시 있어야 한다.
3. **범례가 지도를 가리지 않는다** — 군집 18개를 한 줄씩 세우면 범례가
   610~688px, 화면 세로의 2/3가 된다(실측). 긴 목록은 접어야 한다.
"""
import importlib.util
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mapviz

# 실제 산출물의 군집 수. `top*.csv`가 18개 군집을 만든다 — 8색 팔레트를
# 두 바퀴 넘게 돌리는 값이라, 순환 규칙이 무너지면 여기서 걸린다.
CLUSTER_COUNT = 18


@pytest.fixture(scope="module")
def shared():
    return mapviz


def _rgb(hex_color: str):
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def test_군집마다_색이_다르다(shared):
    """18개 군집이 모두 다른 색이어야 한다.

    ⚠️ 검정은 어둡게 만들 수 없다 — 0에 무엇을 곱해도 0이다. 처음 시안은
    `_darken`만 써서 군집 7과 15가 둘 다 #000000이었다.
    """
    colors = [shared.cluster_color(i) for i in range(CLUSTER_COUNT)]

    duplicates = {c for c in colors if colors.count(c) > 1}
    assert not duplicates, (
        f"군집 색이 겹친다: {sorted(duplicates)} — "
        f"지도에서 두 군집이 한 색이면 범례의 구분이 화면에 없다")


def test_색이_모두_유효한_16진수다(shared):
    """folium에 넘기는 값이라 `#rrggbb` 꼴이어야 한다."""
    for i in range(CLUSTER_COUNT):
        color = shared.cluster_color(i)
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", color), f"군집 {i}의 색이 이상하다: {color}"


def test_첫_여덟은_색맹_안전색_그대로다(shared):
    """군집이 8개 이하면 Okabe-Ito 원본 색을 손대지 않고 쓴다 —
    밝기를 흔드는 순간 색맹 안전 보장이 깨진다."""
    for i in range(8):
        assert shared.cluster_color(i) == shared._OKABE_ITO[i]


def test_이웃한_순번은_눈에_띄게_다르다(shared):
    """바로 앞 순번과 색이 붙어 있으면 군집 경계가 안 보인다."""
    for i in range(1, CLUSTER_COUNT):
        a, b = _rgb(shared.cluster_color(i - 1)), _rgb(shared.cluster_color(i))
        distance = sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5
        assert distance > 60, (
            f"군집 {i-1}과 {i}의 색이 너무 가깝다(거리 {distance:.1f})")


def test_범례는_색과_글자를_함께_낸다(shared):
    """색만으로 뜻을 전하지 않는다(docs/구현/DESIGN.md).

    배지(색)만 있고 설명이 없으면, 색을 구분하지 못하는 사람에게는
    범례가 빈 상자다.
    """
    html = shared.legend_html(
        "범례 — 군집",
        [(shared.swatch_circle("#E69F00", "0"), "군집 0"),
         (shared.swatch_line("#56B4E9", dashed=True), "군집 1 경로")],
        note="원 크기는 재배치 수량입니다.")

    assert "범례 — 군집" in html
    assert "군집 0" in html and "군집 1 경로" in html, "글자 설명이 빠졌다"
    assert "원 크기는 재배치 수량입니다." in html
    # 지도 위에 떠 있어야 한다 — Leaflet 타일보다 위.
    assert "position: fixed" in html and "z-index: 9999" in html


def test_범례는_한국어_제목을_그대로_쓴다(shared):
    """화면의 나머지가 한국어다. step4는 영어 "Legend"였고 step1은 범례가
    아예 없어서, 같은 도구가 아닌 것처럼 보였다."""
    html = shared.legend_html("범례 — 재고 현황", [(shared.swatch_circle("red"), "Drop — 부족 해소")])

    assert "범례 — 재고 현황" in html
    assert "Legend" not in html


def test_긴_목록은_접는다(shared):
    """군집 18개를 한 줄씩 세우면 범례가 화면 세로의 2/3를 먹는다(실측
    610~688px). 앞의 고정 항목은 남기고 긴 목록만 접는다."""
    rows = [(shared.swatch_circle("red"), "출발")]
    rows += [(shared.swatch_line(shared.cluster_color(i)), f"군집 {i} 경로")
             for i in range(CLUSTER_COUNT)]

    html = shared.legend_html("범례 — 경로", rows,
                              collapse_after=1, collapse_label="군집")

    assert "<details" in html, "긴 목록을 접지 않았다"
    assert f"군집 {CLUSTER_COUNT}개 더 보기" in html
    # 접기 전 항목은 <details> **앞에** 있어야 한다 — 늘 보여야 하는 줄이다.
    assert html.index("출발") < html.index("<details")
    # 접어도 내용은 문서에 있다(펴면 보인다).
    assert f"군집 {CLUSTER_COUNT - 1} 경로" in html
    # 펴 둔 채로 시작하지 않는다 — 펴 두면 접는 뜻이 없다.
    assert "<details open" not in html


def test_접을_필요가_없으면_접지_않는다(shared):
    """재고 현황 지도는 줄이 둘뿐이다. 여기까지 <details>로 감싸면
    한 번 더 눌러야 읽을 수 있는 범례가 된다."""
    html = shared.legend_html(
        "범례 — 재고 현황",
        [(shared.swatch_circle("red"), "Drop — 부족 해소"),
         (shared.swatch_circle("blue"), "Pick — 과잉 해소")])

    assert "<details" not in html
    assert "Drop — 부족 해소" in html and "Pick — 과잉 해소" in html


def test_세_지도가_모두_mapviz를_쓴다():
    """팔레트·범례가 파일마다 따로 박혀 있으면 하나 고칠 때 셋이 갈라진다 —
    실제로 그래서 갈라져 있었다(step3만 한글 범례, step4는 영어, step1은
    범례 없음). `project_config.MAP_TILES`와 같은 이유의 규약이다."""
    sources = {
        "step1": PROJECT_ROOT / "step1_cluster" / "st_visualization.py",
        "step3": PROJECT_ROOT / "step3_map" / "main.py",
        "step4": PROJECT_ROOT / "step4_metrics" / "imbalance.py",
    }
    for step, path in sources.items():
        code = path.read_text(encoding="utf-8")
        assert "from mapviz import" in code, f"{step}이 mapviz.py를 쓰지 않는다"

    # 색 목록을 다시 파일 안에 박아 두지 않았는지 본다. 예전에 갈라진
    # 자리라 되돌아가기 쉽다.
    for step in ("step1", "step3"):
        code = sources[step].read_text(encoding="utf-8")
        assert "'darkred'" not in code and '"darkred"' not in code, \
            f"{step}에 옛 팔레트가 남아 있다"


def test_크기_눈금은_하한을_감추지_않는다():
    """크기로 값을 말했으면 **말할 수 없는 것도** 말해야 한다.

    불균형 지도의 마커 반지름은 `max(3, improvement)`라 3에서 막힌다.
    그래서 1대짜리도, 0도, 계획이 오히려 악화시킨 곳(음수)도 전부 같은
    크기다. 눈금이 그것을 그냥 "3대"라고 적으면 **거짓 주장**이 된다 —
    작은 원을 전부 3대로 읽게 만든다.
    """
    code = (PROJECT_ROOT / "step4_metrics" / "imbalance.py").read_text(encoding="utf-8")
    # 주석은 옛 낱말을 **인용해 설명한다** — 코드만 본다.
    body = "\n".join(l for l in code.splitlines() if not l.lstrip().startswith("#"))

    # 하한이 아직 있다면(있어야 한다 — 0이면 점이 사라진다) 눈금도 그렇게 적혀야 한다.
    assert "max(3, row['improvement'])" in body, "반지름 하한이 사라졌다면 이 시험을 고쳐라"
    assert '"3대"' not in body, "하한에 걸리는 값을 '3대'라고 단언하고 있다"
    assert "≤3대" in body, "눈금이 하한을 밝히지 않는다"

    # 악화된 곳은 크기로 구분할 수 없으므로 다른 수단이 있어야 한다.
    assert "worsened" in body, "악화된 대여소를 구분하지 않는다"
    assert "dash_array" in body, "악화 표시가 크기 말고는 없다"


def test_점선_배지는_테두리로만_말한다():
    """색은 이미 싣기/내리기를 뜻한다 — 뜻을 하나 더 실을 수 없어
    모양(점선)으로 가른다."""
    badge = mapviz.swatch_circle_dashed()
    assert "dashed" in badge
    assert "transparent" in badge, "채우면 채운 마커와 헷갈린다"


def test_세_지도가_같은_낱말을_쓴다():
    """한 개념에 어휘가 여러 벌이면 기사가 화면을 오갈 때마다 번역해야 한다.
    step3만 "Pick (회수)"/"Drop (분배)"라는 **세 번째 어휘**를 쓰고 있었다."""
    for step, path in (("step3", PROJECT_ROOT / "step3_map" / "main.py"),
                       ("step4", PROJECT_ROOT / "step4_metrics" / "imbalance.py")):
        code = path.read_text(encoding="utf-8")
        # 주석에서 옛 낱말을 인용하는 것은 괜찮다 — 코드에 남아 있으면 안 된다.
        lines = [l for l in code.splitlines()
                 if not l.lstrip().startswith("#")]
        body = "\n".join(lines)
        assert 'action_txt = "Pick' not in body, f"{step}에 영어 낱말이 남아 있다"
        assert "status = '싣기'" not in body, f"{step}이 낱말을 따로 박아 두었다"


def test_쓰지_않는_색_변수를_두지_않는다():
    """`base_color = "blue"/"orange"`가 정의만 되고 어디에도 안 쓰였다.
    읽는 사람은 색이 작업 종류를 뜻한다고 오해하는데, 정작 마커는 전부
    보라 원이다."""
    code = (PROJECT_ROOT / "step3_map" / "main.py").read_text(encoding="utf-8")
    body = "\n".join(l for l in code.splitlines() if not l.lstrip().startswith("#"))
    assert "base_color" not in body, "쓰지 않는 색 변수가 남아 있다"


# ─────────── 산출물이 낡았는지 알리는 도장 (1.26.122) ───────────

def test_범례에_그린_코드의_지문이_찍힌다():
    """지도의 범례·팔레트는 코드에 있고 산출물은 디스크에 있다.

    코드를 고쳐도 이미 그려 둔 HTML은 낡은 채로 남는데, 사람이 보는 것은
    디스크의 HTML이다. 실제로 두 번 갈렸고(1.26.80 팔레트·1.26.107 싣기/내리기
    색) **두 번 다 사람이 눈으로 발견했다.**
    """
    html = mapviz.legend_html("시험", [(mapviz.swatch_circle("#000"), "가")])
    stamp = mapviz.stamp_in(html)
    assert stamp, "범례에 지문이 없다 — 낡음을 알 길이 없어진다"
    assert len(stamp) == 12 and all(c in "0123456789abcdef" for c in stamp)


def test_코드가_바뀌면_지문도_바뀐다(tmp_path):
    """판 번호를 손으로 올리는 방식이면 올리는 것을 잊는다.
    내용을 해싱하므로 **잊을 수가 없다.**"""
    fake = tmp_path / "drawer.py"
    fake.write_text("# 처음\n", encoding="utf-8")
    first = mapviz.source_stamp(str(fake))

    fake.write_text("# 처음\n# 한 줄 더\n", encoding="utf-8")
    second = mapviz.source_stamp(str(fake))

    assert first != second, "부르는 쪽이 바뀌었는데 지문이 그대로다"

    # 부르는 쪽이 다르면 지문도 달라야 한다 — 그래야 분류별로 가려 낸다.
    other = tmp_path / "other.py"
    other.write_text("# 처음\n", encoding="utf-8")
    assert mapviz.source_stamp(str(other)) != second


def test_지문을_캐시하지_않는다(tmp_path):
    """⚠️ 처음에는 캐시를 뒀는데, 웹앱은 오래 떠 있는 프로세스라 한 번 읽은
    지문이 굳어 **서버를 다시 띄우기 전까지 코드 변경을 영영 못 보는** 상태가
    됐다. 낡음을 알리려고 만든 장치가 낡은 값을 쥐고 있으면 없느니만 못하다.
    """
    fake = tmp_path / "drawer.py"
    fake.write_text("# 1\n", encoding="utf-8")
    a = mapviz.source_stamp(str(fake))
    fake.write_text("# 2\n", encoding="utf-8")
    b = mapviz.source_stamp(str(fake))
    assert a != b, "같은 경로를 다시 물었더니 옛 값이 나온다(캐시가 살아 있다)"


def test_지도_분류마다_그리는_모듈이_적혀_있다():
    """`MAP_CATEGORIES`와 `MAP_DRAWERS`는 손으로 적은 두 목록이다 —
    지도가 늘 때 한쪽만 고치면 그 분류는 **낡음을 영영 모른다.**
    `transfer_run.RUN_TABLES`에서 겪은 것과 같은 자리다(1.26.113)."""
    import sys
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from webapp import catalog

    subdirs = {subdir for _title, subdir, _pattern in catalog.MAP_CATEGORIES}
    assert subdirs == set(catalog.MAP_DRAWERS), (
        f"두 목록이 어긋난다: 분류에만 {subdirs - set(catalog.MAP_DRAWERS)}, "
        f"그리는 모듈에만 {set(catalog.MAP_DRAWERS) - subdirs}")

    for subdir, drawer in catalog.MAP_DRAWERS.items():
        assert drawer.is_file(), f"{subdir}의 그리는 모듈이 없다: {drawer}"


def test_산출물_지문_캐시가_파일_변경을_따라온다(tmp_path):
    """파일 지문은 캐시하되 **열쇠에 mtime·크기·머리 내용의 해시를 넣는다.**

    `mapviz.source_stamp()`의 캐시는 걷어냈다 — 거기는 열쇠(모듈 경로)가
    내용이 바뀌어도 그대로라 옛 값이 굳었기 때문이다. 여기도 한때
    "mtime·크기만으로 충분하다"고 여겼는데, Windows에서 두 번 연속 쓰기가
    **크기는 같고 mtime_ns 눈금까지 같은** 경우가 실측 80%였다(1.26.128) —
    이 테스트가 실제로 26%(200회 중 52회) 확률로 그 자리에서 깨졌다. 머리
    내용의 해시까지 열쇠에 넣어야 굳지 않는다. 굳으면 다시 그린 지도가
    계속 "낡음"으로 남는다.
    """
    import sys
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from webapp import catalog

    f = tmp_path / "map.html"
    f.write_text('<div data-mapviz="aaaaaaaaaaaa"></div>', encoding="utf-8")
    assert catalog._stamp_of_file(f) == "aaaaaaaaaaaa"

    # 같은 경로, 다른 내용 -> 새 값이 나와야 한다
    f.write_text('<div data-mapviz="bbbbbbbbbbbb"></div>', encoding="utf-8")
    assert catalog._stamp_of_file(f) == "bbbbbbbbbbbb",         "파일이 바뀌었는데 캐시가 옛 지문을 내놓는다"

    # 지문이 없는 산출물은 None (도장 이전 파일) — 모른다고 말한다
    f.write_text("<div>도장 없음</div>", encoding="utf-8")
    assert catalog._stamp_of_file(f) is None


def test_mtime와_크기가_같아도_머리_내용이_다르면_열쇠가_갈린다(tmp_path, monkeypatch):
    """(경로, mtime_ns, 크기)만으로는 안 된다는 것을 **강제로** 만들어 확인한다.

    실제 콜리전은 Windows에서만·확률적으로 일어난다(1.26.128 실측: 300회 중
    239회, 80%). 이 환경·이 순간에 항상 재현되는 건 아니므로, 두 번째
    `stat()`이 첫 번째와 **똑같은 mtime_ns·크기**를 내놓도록 강제해 콜리전을
    직접 만든다 — 머리 해시가 열쇠에 없었다면 반드시 깨졌을 조건이다.
    """
    from webapp import catalog

    f = tmp_path / "map.html"
    f.write_text('<div data-mapviz="aaaaaaaaaaaa"></div>', encoding="utf-8")
    frozen_mtime_ns = f.stat().st_mtime_ns
    frozen_size = f.stat().st_size

    class FrozenStat:
        st_mtime_ns = frozen_mtime_ns
        st_size = frozen_size

    real_stat = Path.stat

    def fake_stat(self, *args, **kwargs):
        return FrozenStat() if self == f else real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fake_stat)

    assert catalog._stamp_of_file(f) == "aaaaaaaaaaaa"

    # 내용은 바뀌었는데 stat()은 (강제로) 똑같은 mtime_ns·크기를 답한다 —
    # 열쇠에 머리 해시가 없으면 이 자리에서 무조건 옛 지문이 나온다.
    f.write_text('<div data-mapviz="bbbbbbbbbbbb"></div>', encoding="utf-8")
    assert catalog._stamp_of_file(f) == "bbbbbbbbbbbb", (
        "mtime·크기가 (강제로) 같아도 머리 내용이 다르면 열쇠도 달라야 한다")
