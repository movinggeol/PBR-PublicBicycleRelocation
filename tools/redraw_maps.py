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
**TMAP을 부른다**(유료). 지도만 다시 그리는 길이 필요하다.

## 무엇을 하지 않나

- **TMAP을 부르지 않는다.** 경로 지도(step3)는 실도로 좌표가 있어야 그려지고
  그것은 호출로만 얻는다 — 그래서 여기서는 다루지 않는다. 군집 지도(step1)와
  불균형 지도(step4)만 다시 그린다. 둘 다 CSV만 읽는다.
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
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
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
CLUSTER_MAP = str(PROJECT_ROOT
                  / "data/pp_data/ILP/visualization/clusterd_map{duration} ({now}).html")
IMBALANCE_MAP = str(PROJECT_ROOT
                    / "data/pp_data/성능 지표/visualization/imbalance_map{duration} ({now}).html")


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

from step1_cluster import st_visualization
st_visualization.make_clustered_map([duration])

from step4_metrics import imbalance
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
    args = parser.parse_args()

    found = available()
    if not found:
        print("다시 그릴 산출물이 없습니다 — step1 후보 파일이 있어야 합니다.")
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

    print(f"\n다시 그릴 지도: {len(targets)}쌍 (군집 + 불균형)")
    for run_label, duration, _ in targets:
        print(f"  - {run_label} {duration}")
    print("\n⚠️ 경로 지도(step3)는 TMAP 호출이 필요해 여기서 다루지 않습니다.")

    if args.dry_run:
        print("\n--dry-run이라 그리지 않았습니다.")
        return 0

    ensure_output_dirs()
    total = 0
    failed = []
    for run_label, duration, candidates in targets:
        print(f"\n[{run_label} {duration}]")
        try:
            drawn = 0
            for path in redraw(run_label, duration, candidates):
                print(f"  ✓ {Path(path).name}")
                total += 1
                drawn += 1
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
