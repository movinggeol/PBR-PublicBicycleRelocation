"""구별 불균형 완화(`webapp/district_view.py`, 1.26.316) 테스트."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from webapp import district_view as dv


def test_다섯_구가_다_있다():
    assert [d["name"] for d in dv.districts()] == ["동구", "중구", "서구", "유성구", "대덕구"]


@pytest.mark.parametrize("lat, lon, name", [
    (36.3504, 127.3845, "서구"),     # 대전시청
    (36.3721, 127.3604, "유성구"),   # KAIST
    (36.3324, 127.4343, "동구"),     # 대전역
    (36.3256, 127.4213, "중구"),     # 중구청
    (36.3467, 127.4155, "대덕구"),   # 대덕구청
])
def test_알려진_곳이_제_구에_든다(lat, lon, name):
    assert dv.district_of(lat, lon) == name


def test_경계_틈에_빠진_점은_가장_가까운_구로_간다():
    """경계를 줄이며 생긴 틈이나 대전 바로 밖 점도 None이 아니라 가까운 구다."""
    assert dv.district_of(36.20, 127.40) in {"중구", "동구", "서구"}
    assert dv.district_of(None, 127.4) is None


def test_지도는_다섯_구를_그리고_값을_글자로도_적는다():
    rows = [{"name": n, "cut_pct": p, "before": 10, "after": 8, "worked": 1, "stations": 5}
            for n, p in [("동구", 10.0), ("중구", 20.0), ("서구", None), ("유성구", 25.0), ("대덕구", 0.0)]]
    svg = dv.map_svg(rows)
    assert svg.count("<path") == 5
    assert "25%" in svg and "—" in svg, "값이 없는 구는 0이 아니라 —로 적는다"
    assert 'role="img"' in svg
