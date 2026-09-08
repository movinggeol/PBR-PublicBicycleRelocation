"""재고 수집 현황 화면의 데이터 조립 (1.26.158).

`stock_history`는 **이 프로젝트의 유일한 실측**이다(1,376곳 × 12일 = 62.7만 행).
논문의 결품 지표가 여기서 나오는데, 확인하는 길이 터미널
(`tools/collect_stock.py --status`)뿐이라 웹에는 화면이 하나도 없었다.
`/data`는 파이프라인 **산출물**만 보여 준다.

그 사이에 **수집이 두 번 조용히 멈췄다** — 등록이 안 돼 있던 것(1.26.103),
등록한 그날 비활성화된 채 닷새 방치된 것(1.26.140). 둘 다 사람이 터미널을
열어 봐야만 알 수 있었다.

## 판정을 여기서 다시 하지 않는다

숫자는 전부 `tools/collect_stock.py`가 낸다(`coverage()`·`resolve_window()`).
같은 규칙을 여기 다시 적으면 **화면과 터미널이 다른 답을 하게 된다** — 이
저장소는 그 사고를 이미 겪었다(1.26.55에서 창을, 1.26.121에서 "온전한 날"을).

⚠️ **"온전한 날"은 여기서 쓰는 뜻이 하나뿐이 아니다.** 이 화면이 말하는 `온전`은
*등록된 창을 100% 채웠나*이고, 실험 쪽(`dense_days()`)은 *그날 실제로 돈 창을
80% 채웠나*를, 회차 판정(`duration_complete_days()`)은 *그 회차 시간대가
덮였나*를 묻는다(1.26.153). 셋은 같은 날에 다르게 답한다 — 그래서 화면이
**무엇을 묻고 있는지 밝힌다.**
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from webapp import store

# 며칠 이상 새 관측이 없으면 "멈춘 것 같다"고 말할지. 수집은 평일만 돌므로
# 주말(최대 2일)을 넘겨야 한다 — 하루로 두면 월요일 아침마다 거짓 경보가 뜬다.
STALLED_DAYS = 3


def _tool():
    """수집기 모듈. 없거나 깨졌으면 `None` — 화면은 그 사실을 말한다.

    ⚠️ 임포트 자체는 API를 부르지 않는다(`--status`도 안 부른다). 그래도
    감싸 두는 이유는 이것이 **도구**라서다 — 도구가 없다고 대시보드 전체가
    500이 되면 안 된다.
    """
    try:
        from tools import collect_stock
        return collect_stock
    except Exception:      # 임포트 실패는 화면을 죽일 이유가 못 된다
        return None


def _window() -> tuple:
    """(창, 간격, 출처). 등록된 작업을 먼저 읽고 없으면 기본값으로 물러선다.

    ⚠️ **출처를 함께 돌려주는 것이 핵심이다.** 기본값으로 떨어진 채 결측을
    세면 창이 다른 만큼 표가 통째로 틀리는데, 화면은 그것을 알 수 없다
    (1.26.55에 실제로 그랬다 — 07~22시로 등록해 두고 "하루 49틱 기대"라고
    답했다).
    """
    tool = _tool()
    if tool is None:
        return None, None, "수집기 모듈을 읽지 못했습니다"
    found = None
    try:
        found = tool.registered_args()
    except Exception:
        found = None
    if found:
        return (found.get("window", tool.DEFAULT_WINDOW),
                found.get("interval", tool.DEFAULT_INTERVAL),
                f"등록된 작업 '{tool.TASK_NAME}'")
    return (tool.DEFAULT_WINDOW, tool.DEFAULT_INTERVAL,
            "기본값 — 등록된 작업을 찾지 못했습니다")


def context() -> dict:
    """수집 현황 화면이 쓸 값. **읽기만 한다** — 타슈 API를 부르지 않는다."""
    tool = _tool()
    window, interval, source = _window()
    empty = {
        "window": window, "interval": interval, "source": source,
        "rows": [], "total_ticks": 0, "days": 0, "stations": 0,
        "span": None, "last_seen": None, "intact": [], "split_days": 0,
        "expected": None, "stalled": None, "error": None,
    }
    if tool is None:
        empty["error"] = "수집기 모듈(tools/collect_stock.py)을 읽지 못했습니다."
        return empty

    try:
        start, end = tool.parse_window(window)
        table = tool.coverage(start, end, interval)
    except Exception as exc:      # 창 표기가 깨졌거나 DB를 못 읽었다
        empty["error"] = f"수집 현황을 읽지 못했습니다: {exc}"
        return empty

    if table.empty:
        return empty

    rows = table.to_dict(orient="records")
    intact = [r["날짜"] for r in rows if r["상태"] == "온전"]
    last_seen = rows[-1]["날짜"]

    return {
        "window": window, "interval": interval, "source": source,
        "rows": rows,
        "total_ticks": int(table["틱"].sum()),
        "expected": int(table["기대"].iloc[0]),
        "days": len(rows),
        "stations": store.stock_station_count(),
        "span": f"{rows[0]['날짜']} ~ {last_seen}",
        "last_seen": last_seen,
        "intact": intact,
        # 하루가 여러 구간이면 수집 실패가 아니라 **PC가 꺼져 있던 것**이다.
        # 둘은 대응이 완전히 다르므로 세어서 화면이 갈라 말하게 한다.
        "split_days": sum(1 for r in rows if r["구간"] > 1),
        "stalled": _stalled_note(last_seen),
        "error": None,
    }


def _stalled_note(last_day: str, *, today=None) -> Optional[dict]:
    """마지막 관측이 오래됐으면 **멈춘 것 같다**고 말한다.

    수집이 두 번 조용히 멈췄고 둘 다 사람이 터미널을 열어야만 알 수 있었다.
    화면이 먼저 말하면 그 며칠이 사라지지 않는다.

    ⚠️ **판정할 수 없으면 `None`** 이다(날짜를 못 읽었거나 미래로 찍혔다).
    짐작해서 "정상"이라 답하면 멈춘 것을 알리려던 장치가 거짓말을 한다 —
    `store.age_note()`·지도 지문과 같은 규약이다.
    """
    stamp = pd.to_datetime(last_day, errors="coerce")
    if pd.isna(stamp):
        return None
    now = pd.Timestamp.today().normalize() if today is None \
        else pd.to_datetime(today).normalize()
    days = (now - stamp.normalize()).days
    if days < 0:
        return None
    return {"days": days, "stalled": days >= STALLED_DAYS}
