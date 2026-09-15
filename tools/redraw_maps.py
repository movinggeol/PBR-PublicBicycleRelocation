"""이미 계산된 실행의 **지도만** 다시 그린다 — 값은 손대지 않는다.

## 왜 필요한가

지도의 범례·팔레트는 코드에 있고 산출물은 디스크에 있다. 코드를 고쳐도
**이미 그려 둔 지도는 낡은 채로 남는다.** 그런데 사람이 보는 것은 코드가
아니라 디스크의 HTML이다. 실제로 그렇게 갈렸다:

- 1.26.80이 색맹 안전 팔레트와 접히는 범례를 넣었지만, 그 뒤로 파이프라인을
  돌린 적이 없어 화면의 군집 지도에는 **범례가 아예 없고** 레이어 컨트롤이
  38줄로 지도 오른쪽을 덮고 있었다.
- 1.26.107이 싣기·내리기 색을 웹과 통일했지만, 다시 그리기 전까지 지도는
  여전히 웹과 **반대 색**으로 남는다.

파이프라인을 통째로 다시 돌리면 되지만 그건 비싸고(수 분), 무엇보다
**TMAP을 부른다**(무료 요금제지만 일일 호출 한도를 쓴다 — 이 자리의 *"유료"* 는
1.26.210이 열두 곳을 고칠 때 빠졌다). 지도만 다시 그리는 길이 필요하다.

## 무엇을 하지 않나

- **기본으로는 TMAP을 부르지 않는다.** 군집 지도(step1)와 불균형 지도(step4)만
  다시 그린다. 둘 다 DB의 `pick_drop`을 먼저 읽고, 없으면 후보 CSV로
  물러선다(1.26.164) — 읽기만 하고 쓰지 않는다.

  🔴 **`--with-route`를 주면 경로 지도(step3)까지 그린다** — 그때는 TMAP을
  실제로 부른다. 예전에 이 자리에는 *"그래서 여기서는 다루지 않는다"* 라고만
  적혀 있었는데, 그 한 줄 때문에 세션마다 경로 지도를 **낡은 채로 두고
  지나갔다**(1.26.210에서 사용자가 지적). **TMAP은 무료 요금제다 — 아무리
  불러도 요금이 청구되지 않고 일일 호출 한도만 있다.** 아껴야 할 것은 돈이
  아니라 그날 남은 호출 수다.

  ⚠️ 이 갈래에서만은 **DB에 쓴다** — step3가 TMAP 실측을 `road_leg`에 남긴다
  (같은 (실행, 회차)의 기존 행을 갈아끼우므로 중복은 안 쌓인다). 게이트를
  판정하는 패널 행(`roadprobe-*`)과는 라벨이 달라 서로 섞이지 않는다.
- **다시 계산하지 않는다.** step4의 `__main__`을 그냥 돌리면 지표를 새로
  구해 `metrics`·`kpi_summary`에 덮어쓴다. 입력이 그대로면 같은 값이 나오겠
  지만, *"그렇겠지"* 로 과거 실행의 기록을 덮는 것은 다시 그리기가 아니다.
  여기서는 지도 함수만 직접 부른다 — **DB를 건드리지 않는다.**

> 재현: `python tools/redraw_maps.py --dry-run`

## 쓰는 법

    python tools/redraw_maps.py --dry-run          # 무엇을 다시 그릴지만 본다
    python tools/redraw_maps.py                    # 가장 최근 실행
    python tools/redraw_maps.py --all              # 남아 있는 것 전부
    python tools/redraw_maps.py --run-label "obs-cmp-1520"
    python tools/redraw_maps.py --all --with-route # 경로 지도까지 (TMAP 호출)

경로 지도는 군집 하나에 호출 하나다 — 회차당 14~16건이다.

## 🔴 하루 호출 총량을 스스로 묶는다 (1.26.219)

2026-09-14에 이 도구가 하루 **138건**을 불렀고, 이튿날 07:14 도로 수집(같은 키로 매일
20건)이 두 엔드포인트 모두 `429 QUOTA_EXCEEDED`로 **0구간**이 됐다 — 게이트 A 10일째
날이었다. 문서(docs/구현/TESTING.md 4장)에 *"20건을 남기고 불러라"* 를 적었지만, 이 도구는
회차마다 프로세스를 따로 띄워 `PBR_TMAP_MAX_CALLS`가 **프로세스마다 새로** 걸렸다 —
그 규칙을 지킬 방법이 도구 안에 없었다. 그래서:

- **실행 전체의 예산**을 둔다. `--tmap-budget` > 바깥 `PBR_TMAP_MAX_CALLS` > 기본값
  (`100 − 도로 수집기 몫 20` = 80건) 순이다. 한도가 **몇 시에 풀리는지 모르므로**(한국 시간
  자정이 아니다) 오늘 수집이 끝났어도 내일 아침 몫을 남긴다.
- 회차마다 **필요한 호출 수(VRP 계획의 군집 수)** 를 미리 세고, 남은 예산으로 모자라면 그
  경로 지도는 **아예 그리지 않는다** — 반쯤 부르다 끊기면 나머지 군집이 직선으로 그려진 채
  *최신* 지문을 받는다. 자식에게는 남은 예산을 `PBR_TMAP_MAX_CALLS`로 넘긴다.
- 쓴 호출은 자식이 찍는 `TMAP 호출 N건`으로 깎는다. 못 읽으면(자식이 먼저 죽었다)
  필요량을 다 쓴 것으로 센다 — 적게 세는 쪽이 한도를 태운다.
- **오늘 도로 수집이 이 PC의 DB에서 얼마나 찼는지** 함께 말한다. 다른 PC가 같은 키로
  수집하는지는 여기서 알 수 없으므로, 판단은 사람에게 넘기고 예산은 늘 남긴다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from project_config import DATA_ROOT, ensure_output_dirs

# 두 지도가 함께 읽는 입력. step1 후보 파일 하나로 둘 다 그릴 수 있다.
CANDIDATE_DIR = DATA_ROOT / "pp_data/ILP/후보"
CANDIDATE_RE = re.compile(r"^top(_\d+_\d+) \((.+)\)\.csv$")

# 두 지도의 산출 경로. step 모듈의 상수와 **같은 문자열이라야** 한다 —
# 여기서 확인하는 이름과 저쪽이 저장하는 이름이 갈리면 확인이 뜻이 없다.
CLUSTER_MAP = str(DATA_ROOT
                  / "pp_data/ILP/visualization/clusterd_map{duration} ({now}).html")
IMBALANCE_MAP = str(DATA_ROOT
                    / "pp_data/성능 지표/visualization/imbalance_map{duration} ({now}).html")
# 경로 지도(step3)는 `--with-route`일 때만 그린다. 이름은 step3의 result_path와
# **같은 문자열이라야** 한다 — 위의 둘과 같은 이유다.
ROUTE_MAP = str(DATA_ROOT
                / "pp_data/VRP/visualization/vrp_map{duration} ({now}).html")
STEP3_MAIN = PROJECT_ROOT / "pipeline" / "step3_map" / "main.py"

# ── TMAP 호출 총량 (1.26.219) ─────────────────────────────────────────
# `routeSequential30`의 일일 한도가 하루 약 100건이다(docs/구현/steps/step3_visualization.md).
# 기본 예산은 여기서 **도로 수집기 몫**(`road_collection_today()["reserve"]`)을 뺀다.
TMAP_DAILY_LIMIT = 100
# step3 `__main__`이 끝에 찍는 줄: "TMAP 호출 14건 (예산 35건)"
_CALLS_RE = re.compile(r"TMAP 호출 (\d+)건")


class RouteRedrawError(RuntimeError):
    """경로 지도 다시 그리기가 실패했다. `calls`는 그때까지 쓴 TMAP 호출 수(모르면 None)."""

    def __init__(self, message: str, calls: int | None = None):
        super().__init__(message)
        self.calls = calls


def calls_in(stdout: str) -> int | None:
    """자식이 찍은 TMAP 호출 수. 그 줄이 없으면 None이다 — 0으로 짐작하지 않는다."""
    found = _CALLS_RE.findall(stdout or "")
    return int(found[-1]) if found else None


def tmap_budget(given: int | None, reserve: int) -> int:
    """이번 실행의 호출 총량: `--tmap-budget` > 바깥 `PBR_TMAP_MAX_CALLS` > 100 − 수집기 몫.

    바깥 `PBR_TMAP_MAX_CALLS`를 **실행 전체**의 값으로 읽는다 — TESTING.md 4장 3단계가
    *"이번 작업의 호출 수를 묶는다"* 는 뜻으로 그 변수를 쓰라고 적었기 때문이다.
    """
    if given is not None:
        return given
    outer = os.environ.get("PBR_TMAP_MAX_CALLS")
    if outer:
        try:
            return max(int(outer), 0)
        except ValueError:
            raise SystemExit(
                f"환경변수 PBR_TMAP_MAX_CALLS는 정수여야 합니다 (입력: {outer!r}).") from None
    return max(TMAP_DAILY_LIMIT - reserve, 0)


def road_collection_today(day: date | None = None) -> dict:
    """오늘 도로 수집(`roadprobe-…`)이 **이 PC의 DB에서** 얼마나 찼는가.

    반환: `label` · `collected`(구간 수, DB를 못 읽으면 None) · `expected`(패널이 없으면
    None) · `reserve`(수집기가 하루에 쓰는 호출 수 = 사슬 × 회차).

    ⚠️ 패널이 없을 때 `load_panel()`을 부르지 않는다 — 그 함수는 없으면 **새로 만든다.**
    확인하려다 다른 PC와 대조할 수 없는 패널을 만드는 일은 없어야 한다.
    """
    from project_config import DURATIONS
    from tools import collect_road_time as road

    day = day or date.today()
    text = day.isoformat()
    label = (road.PROBE_PREFIX + ("holiday-" if road.is_holiday_date(text) else "")
             + text)
    chains = None
    if road.PANEL_PATH.exists():
        chains = json.loads(road.PANEL_PATH.read_text(encoding="utf-8"))["chains"]
    try:
        collected = sum(road.collected_legs(label).values())
    except Exception:                                   # noqa: BLE001
        collected = None
    return {
        "label": label,
        "collected": collected,
        "expected": road.legs_per_duration(chains) * len(DURATIONS) if chains else None,
        "reserve": (len(chains) if chains else road.CHAIN_COUNT) * len(DURATIONS),
    }


def road_status_lines(state: dict, budget: int, adjust: str = "--tmap-budget으로") -> list:
    """오늘 도로 수집 상태와 이번 예산을 사람이 읽을 말로.

    `adjust`는 예산을 바꾸는 길이다 — 파이프라인은 이 도구의 `--tmap-budget`이 없어
    `PBR_TMAP_MAX_CALLS로`를 넘긴다(`run_pipeline.tmap_notice`, 1.26.222).

    🔴 **이 PC에 기록이 없다고 "수집이 안 됐다"로 말하지 않는다.** 회사환경은 도로 수집이
    꺼져 있어 늘 0구간이다 — 같은 키로 집 PC가 받고 있을 수 있다.
    """
    label, got, want = state["label"], state["collected"], state["expected"]
    reserve = state["reserve"]
    if got is None:
        head = f"오늘 도로 수집({label})을 이 PC의 DB에서 읽지 못했습니다."
    elif want and got >= want:
        head = f"오늘 도로 수집({label})은 이 PC에서 끝났습니다 ({got}/{want}구간)."
    elif got:
        head = (f"오늘 도로 수집({label})이 덜 찼습니다 ({got}/{want or '?'}구간) — 이 PC가"
                " 수집기라면 먼저 `python tools/collect_road_time.py --if-needed`를 돌리십시오.")
    else:
        head = (f"이 PC의 DB에는 오늘 도로 수집({label})이 없습니다 — 다른 PC가 같은 키로"
                " 수집하는지는 여기서 알 수 없습니다.")
    if budget + reserve > TMAP_DAILY_LIMIT:
        tail = (f"⚠️ 예산 {budget}건은 도로 수집기 몫 {reserve}건을 남기지 않습니다 —"
                " 그날(한도가 안 풀렸다면 이튿날 아침) 수집이 막힐 수 있습니다.")
    else:
        tail = (f"한도가 풀리는 시각을 몰라 도로 수집기 몫 {reserve}건을 남기고,"
                f" 이번 실행은 최대 {budget}건만 부릅니다 ({adjust} 조정).")
    return [head, tail]


def route_calls_needed(run_label: str, duration: str) -> int:
    """경로 지도 한 장이 부를 TMAP 호출 수 — VRP 계획의 **군집 수**다.

    군집 하나에 호출 하나다(현실 최대 경유지 26곳이라 분할이 없다, 2026-09-14 실측).
    엔드포인트 폴백이 일어나면 429 한 건이 더 붙으므로 이 값은 **하한**이다.
    step3와 같은 경로로 읽는다 — DB 먼저, 없으면 CSV.
    """
    import db

    csv_path = DATA_ROOT / f"pp_data/VRP/VRP_plan{duration} ({run_label}).csv"
    frame, _ = db.read_step_output("vrp_plan", str(csv_path),
                                   run_label=run_label, duration=duration)
    if frame.empty or "cluster" not in frame.columns:
        return 0
    return int(frame["cluster"].nunique())


def available() -> list:
    """(run_label, duration, 후보 파일) 목록.

    **CSV 파일 이름이 1차 색인이고, DB가 그것을 채운다 (1.26.164).** 예전에는
    파일 이름만 색인이라 CSV 쓰기를 걷으면 이 도구가 통째로 눈이 멀었다
    (1.26.163 조사). 이제 DB에만 있는 (실행, 회차)도 함께 내놓는다 — 그런
    항목의 경로는 **아직 없는 파일**을 가리키고, 자식 쪽은 DB를 먼저 읽으므로
    문제가 되지 않는다(`load_step_output`).

    ⚠️ 정렬 열쇠를 파일 수정 시각에서 **분리했다.** DB에만 있는 항목은 잴 파일이
    없어 `stat()`이 터진다 — 파일이 있으면 그 시각을, 없으면 0을 쓴다.
    """
    found = []
    본_것 = set()
    if CANDIDATE_DIR.is_dir():
        for path in sorted(CANDIDATE_DIR.glob("top*.csv")):
            match = CANDIDATE_RE.match(path.name)
            if match:
                found.append((match.group(2), match.group(1), path))
                본_것.add((match.group(2), match.group(1)))

    try:
        import db
        with db.session() as conn:
            rows = conn.execute(
                "SELECT DISTINCT run_label, duration FROM pick_drop").fetchall()
        for label, duration in rows:
            if (label, duration) in 본_것:
                continue
            found.append((label, duration, CANDIDATE_DIR
                          / f"top{duration} ({label}).csv"))
    except Exception as err:      # DB가 없어도 CSV만으로 돌아야 한다
        print(f"[경고] pick_drop DB 조회 실패: {type(err).__name__}: {err}")

    # 최근 것이 앞에 오게 — 파일 수정 시각이 라벨 문자열보다 믿을 만하다.
    found.sort(key=lambda row: row[2].stat().st_mtime if row[2].is_file() else 0,
               reverse=True)
    return found


# 자식 프로세스가 실행할 본문. 지도 함수만 부르고 **DB를 건드리지 않는다.**
_CHILD = """
import sys
sys.path.insert(0, r"{root}")

import pandas as pd
from project_config import ensure_output_dirs

ensure_output_dirs()
duration = {duration!r}

from pipeline.step1_cluster import st_visualization
st_visualization.make_clustered_map([duration])

from pipeline.step4_metrics import imbalance
# DB 우선으로 읽는다(1.26.164) — 없으면 이 CSV로 물러선다. 파일 경로를 그대로
# 넘기는 것은 **읽기**일 뿐이라 "DB를 건드리지 않는다"(쓰지 않는다)는 규칙과
# 어긋나지 않는다.
reloc_df = imbalance.load_step_output("pick_drop", r"{candidates}", duration=duration)
imbalance.demand_satisfaction_map(
    reloc_df, imbalance.demand_satisfaction(reloc_df).copy(), duration)
"""


def redraw(run_label: str, duration: str, candidates: Path) -> list:
    """한 (실행, 회차)의 지도 둘을 다시 그린다. 만든 파일 목록을 돌려준다.

    ⚠️ **자식 프로세스로 돌린다.** step 스크립트는 import 시점에 모듈 전역
    `now`를 굳히는데, 그 값이 오는 `project_config.DEFAULT_NOW`도 그쪽
    모듈이 처음 import될 때 한 번만 읽힌다. 그래서 `os.environ`을 나중에
    바꾸고 step 모듈만 reload해도 **라벨이 안 따라온다** — 처음 이 도구를
    그렇게 짰다가 불균형 지도 두 장을 **다른 실행의 자료로 덮었다**
    (1.26.107에서 겪음). 한 실행에 한 프로세스면 그런 상태가 아예 없다.
    """
    body = _CHILD.format(root=str(PROJECT_ROOT), duration=duration,
                         candidates=str(candidates))
    env = dict(os.environ, PBR_NOW=run_label, PYTHONIOENCODING="utf-8")
    proc = subprocess.run([sys.executable, "-c", body], env=env,
                          capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "").strip()[-400:])

    made = []
    for template in (CLUSTER_MAP, IMBALANCE_MAP):
        path = Path(template.format(duration=duration, now=run_label))
        # 그 이름으로 **실제로 생겼는지 확인하고** 보고한다. 계산한 이름을
        # 그대로 찍으면 엉뚱한 곳에 저장돼도 ✓가 뜬다 — 그래서 못 봤다.
        if not path.is_file():
            raise RuntimeError(f"저장되지 않았습니다: {path.name}")
        _keep_mtime(path, candidates)
        made.append(str(path))
    return made


def redraw_route(run_label: str, duration: str, candidates: Path,
                 max_calls: int | None = None) -> tuple:
    """경로 지도(step3) 한 장을 **TMAP을 실제로 불러** 다시 그린다.

    반환: (만든 파일 목록, 쓴 TMAP 호출 수 — 모르면 None). `max_calls`를 주면 자식의
    `PBR_TMAP_MAX_CALLS`로 넘겨 **그 이상은 부르지 않게** 한다(1.26.219). 실패하면
    `RouteRedrawError`에 그때까지 쓴 호출 수를 실어 던진다.

    위의 `redraw()`와 달리 step 모듈을 직접 부르지 않고 `step3_map/main.py`를
    스크립트로 띄운다 — 그쪽 `__main__`이 `.env`에서 `API_KEY`를 읽고 엔드포인트
    폴백(`routeSequential30` → `100`)까지 세워 두기 때문이다. 그 준비를 여기서
    베껴 쓰면 두 벌이 갈린다.

    ⚠️ **DB에 쓴다.** step3는 TMAP 실측을 `road_leg`에 남긴다(같은 (실행, 회차)
    범위를 갈아끼우므로 중복은 안 쌓인다). 이 도구의 다른 갈래는 읽기만 하므로
    쓰는 것은 여기뿐이다. 게이트를 판정하는 패널 행(`roadprobe-*`)과는 라벨이
    달라 서로 섞이지 않는다.
    """
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    if max_calls is not None:
        env["PBR_TMAP_MAX_CALLS"] = str(max_calls)
    proc = subprocess.run(
        [sys.executable, str(STEP3_MAIN), "--now", run_label, "--duration", duration],
        env=env, capture_output=True, text=True, encoding="utf-8")
    used = calls_in(proc.stdout)
    if proc.returncode != 0:
        raise RouteRedrawError((proc.stderr or proc.stdout or "").strip()[-400:], used)

    # 호출 수를 그대로 올려 보여 준다 — 일일 한도는 돈이 아니라 **횟수**라,
    # 얼마나 썼는지가 다음 판단의 유일한 근거다.
    # `[건너뜀]` 줄도 올린다 — step3가 예산이 모자라 그리지 않았으면(1.26.222) 아래
    # *"저장되지 않았습니다"* 만으로는 왜인지 안 보인다.
    for line in (proc.stdout or "").splitlines():
        if (line.startswith(("TMAP 호출", "[건너뜀]", "⚠️ 경로 지도"))
                or "엔드포인트" in line):
            print(f"    {line.strip()}")

    path = Path(ROUTE_MAP.format(duration=duration, now=run_label))
    if not path.is_file():
        raise RouteRedrawError(f"저장되지 않았습니다: {path.name}", used)
    if candidates.is_file():
        _keep_mtime(path, candidates)
    return [str(path)], used


def _keep_mtime(target: Path, source: Path) -> None:
    """산출물의 수정 시각을 **원래대로 되돌린다.**

    ⚠️ 다시 그리기는 새 결과가 아니라 같은 결과를 같은 자료로 다시 그린
    것이다. 그런데 화면은 파일 수정 시각으로 "가장 최근 산출물"을 고른다
    (webapp/catalog.py) — 그대로 두면 **전부 오늘이 되어 최신 순서가 통째로
    사라진다.** 실제로 그렇게 만들었다가 2026-04-28 지도가 최신으로 올라
    왔다(1.26.107).

    기준은 그 실행의 후보 CSV다. 같은 파이프라인 실행에서 지도 직전에
    만들어진 파일이라 원래 시각에 가장 가깝고, 다시 그리기가 건드리지 않는다.
    """
    import os as _os

    stat = source.stat()
    _os.utime(target, (stat.st_atime, stat.st_mtime))


def main() -> int:
    parser = argparse.ArgumentParser(description="계산은 그대로 두고 지도만 다시 그린다")
    parser.add_argument("--run-label", help="이 실행만 (기본: 가장 최근 실행)")
    parser.add_argument("--all", action="store_true", help="남아 있는 것 전부")
    parser.add_argument("--dry-run", action="store_true", help="목록만 보고 그리지 않는다")
    parser.add_argument("--with-route", action="store_true",
                        help="경로 지도(step3)까지 그린다 — TMAP을 실제로 부른다"
                             " (무료 요금제, 일일 호출 한도만 있음)")
    parser.add_argument("--tmap-budget", type=int, default=None,
                        help="경로 지도가 이번 실행 전체에서 부를 TMAP 호출 총량."
                             " 기본은 바깥 PBR_TMAP_MAX_CALLS, 없으면"
                             f" {TMAP_DAILY_LIMIT} - 도로 수집기 몫(20) = 80건")
    args = parser.parse_args()
    if args.tmap_budget is not None and args.tmap_budget < 0:
        parser.error("--tmap-budget은 0 이상이어야 합니다")

    found = available()
    if not found:
        print("다시 그릴 산출물이 없습니다 — step1 후보(DB `pick_drop` 또는 후보 CSV)가 있어야 합니다.")
        print(f"  찾은 곳: {CANDIDATE_DIR}")
        return 1

    if args.run_label:
        targets = [row for row in found if row[0] == args.run_label]
        if not targets:
            labels = sorted({row[0] for row in found})
            print(f"'{args.run_label}' 산출물이 없습니다. 있는 실행:")
            for label in labels:
                print(f"  - {label}")
            return 1
    elif args.all:
        targets = found
    else:
        newest = found[0][0]
        targets = [row for row in found if row[0] == newest]
        print(f"가장 최근 실행만 다시 그립니다: {newest}"
              f"  (전부 하려면 --all)")

    묶음 = "셋(군집 + 불균형 + 경로)" if args.with_route else "쌍(군집 + 불균형)"
    print(f"\n다시 그릴 지도: {len(targets)}{묶음}")
    for run_label, duration, _ in targets:
        print(f"  - {run_label} {duration}")
    budget = 0
    needs = {}
    if args.with_route:
        state = road_collection_today()
        budget = tmap_budget(args.tmap_budget, state["reserve"])
        needs = {(label, dur): route_calls_needed(label, dur) for label, dur, _ in targets}
        need_total = sum(needs.values())
        print("\n⚠️ 경로 지도는 TMAP을 **실제로 부릅니다** — 군집 하나에 호출 하나입니다.")
        print("   무료 요금제라 요금이 청구되지는 않지만 일일 호출 한도를 씁니다.")
        for line in road_status_lines(state, budget):
            print(f"   {line}")
        print(f"   필요한 호출은 약 {need_total}건(군집 수 합)이고 이번 예산은 {budget}건입니다.")
        if need_total > budget:
            print("   예산이 모자란 회차의 경로 지도는 **그리지 않고 건너뜁니다** — 반쯤 불러"
                  " 직선이 섞인 지도를 '최신'으로 남기지 않기 위해서입니다.")
        print("   VRP 계획이 없는 회차는 조용히 건너뜁니다.")
    else:
        print("\n⚠️ 경로 지도(step3)는 빠집니다 — 그리려면 --with-route를 주십시오"
              " (TMAP을 부릅니다).")

    if args.dry_run:
        print("\n--dry-run이라 그리지 않았습니다.")
        return 0

    ensure_output_dirs()
    total = 0
    failed = []
    spent = 0            # 이번 실행이 경로 지도에 쓴 TMAP 호출
    over_budget = []     # 예산이 모자라 안 그린 경로 지도
    for run_label, duration, candidates in targets:
        print(f"\n[{run_label} {duration}]")
        try:
            drawn = 0
            for path in redraw(run_label, duration, candidates):
                print(f"  ✓ {Path(path).name}")
                total += 1
                drawn += 1
            if args.with_route:
                # 경로 지도는 **따로 센다.** VRP 계획이 없는 회차가 흔한데
                # (step2가 '대상 없음'으로 건너뛴 시간대), 그걸 실패로 세면
                # 종료 코드가 거짓말을 한다 — 군집·불균형은 멀쩡히 나왔다.
                need = needs.get((run_label, duration), 0)
                remaining = budget - spent
                if need > remaining:
                    print(f"  · 경로 지도는 건너뜁니다: 필요 {need}건 > 남은 예산 {remaining}건")
                    over_budget.append(f"{run_label} {duration}")
                else:
                    try:
                        paths, used = redraw_route(run_label, duration, candidates,
                                                   max_calls=remaining)
                        for path in paths:
                            print(f"  ✓ {Path(path).name}")
                            total += 1
                    except Exception as err:          # noqa: BLE001
                        used = getattr(err, "calls", None)
                        print(f"  · 경로 지도는 건너뜁니다: {type(err).__name__}: {err}")
                    # 몇 건 썼는지 모르면 **필요량을 다 쓴 것으로** 센다 — 적게 세는
                    # 쪽이 한도를 태운다.
                    spent += need if used is None else used
            if not drawn:
                # 예외 없이 **한 장도 안 나온** 경우도 실패다. 자식이 조용히
                # 죽으면 여기로 온다 — 성공과 구분하지 않으면 종료 코드가
                # 거짓말을 한다.
                print("  ⚠ 그려진 파일이 없습니다.")
                failed.append(f"{run_label} {duration}")
        except Exception as err:                      # noqa: BLE001
            # 한 회차가 실패해도 나머지는 그린다 — 옛 산출물은 컬럼이 다를 수 있다.
            print(f"  ⚠ 건너뜁니다: {type(err).__name__}: {err}")
            failed.append(f"{run_label} {duration}")

    print(f"\n지도 {total}장을 다시 그렸습니다.")
    if args.with_route:
        print(f"TMAP 호출 {spent}건 / 예산 {budget}건.")
        if over_budget:
            # 실패가 아니라 **건너뜀**이다 — 종료 코드를 바꾸지 않는다(VRP 계획이 없는
            # 회차와 같다). 대신 무엇을 안 그렸는지는 빠짐없이 말한다.
            print(f"⚠️ 예산이 모자라 경로 지도 {len(over_budget)}장을 안 그렸습니다:"
                  f" {', '.join(over_budget)}")
            print("   한도가 남은 날 다시 돌리거나 --tmap-budget으로 늘리십시오 — 그날 같은 키를"
                  " 쓰는 모든 PC·세션의 합계를 생각하십시오(docs/구현/TESTING.md 4장).")

    # ⚠️ **실패를 종료 코드로 말한다.** 예전에는 열두 쌍이 전부 깨져도
    #    "지도 0장을 다시 그렸습니다."를 찍고 0으로 끝났다 — 화면을 읽는
    #    사람에게는 경고가 보이지만, `$?`만 보는 CI·래퍼·`&&` 사슬에는
    #    성공으로 읽힌다. 실패가 한 건이라도 있으면 0이 아니어야 한다.
    if failed:
        print(f"⚠️ {len(failed)}쌍이 실패했습니다: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
