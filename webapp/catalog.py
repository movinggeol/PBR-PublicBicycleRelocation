"""파이프라인 산출물(data/pp_data) 스캔과 안전한 경로 해석.

웹에서 서빙하는 파일은 data/ 아래의 .html / .csv 로 제한한다.
"""
from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mapviz
# ⚠️ 여기서 다시 정의하지 마라 — 정본은 `project_config`다. 예전에는 이 파일이
#    자기 `DATA_ROOT`를 따로 만들어서, 경로를 재정의해도 화면만 옛 폴더를 봤다.
from project_config import DATA_ROOT, DURATIONS, PP_ROOT, PROJECT_ROOT

ALLOWED_SUFFIXES = {".html", ".csv"}

# (표시 이름, pp_data 기준 폴더, 패턴)
MAP_CATEGORIES = [
    ("클러스터 지도 (step1)", "ILP/visualization", "*.html"),
    ("VRP 경로 지도 (step3)", "VRP/visualization", "*.html"),
    ("불균형 개선 지도 (step4)", "성능 지표/visualization", "*.html"),
]

# 각 폴더의 지도를 **그리는 모듈**. 낡음 판정에 쓴다 —
# `mapviz.source_stamp()`가 이 파일과 mapviz.py를 함께 해싱하므로, 둘 중
# 어느 쪽이 바뀌어도 이미 그려 둔 지도가 낡았다는 것이 드러난다.
#
# ⚠️ `MAP_CATEGORIES`와 **열쇠가 맞아야** 한다. 손으로 적은 두 목록이라
#    지도가 늘 때 한쪽만 고치기 쉽다 — `tests/test_mapviz.py`가 둘이
#    어긋나면 실패한다(`transfer_run.RUN_TABLES`에서 겪은 것과 같은 자리).
MAP_DRAWERS = {
    "ILP/visualization": PROJECT_ROOT / "pipeline" / "step1_cluster" / "st_visualization.py",
    "VRP/visualization": PROJECT_ROOT / "pipeline" / "step3_map" / "main.py",
    "성능 지표/visualization": PROJECT_ROOT / "pipeline" / "step4_metrics" / "imbalance.py",
}

# 지도 한 장이 100~800KB라 스물몇 장을 통째로 읽으면 화면이 느려진다.
#
# ⚠️ 처음에는 **꼬리**부터 읽었다. folium이 범례를 문서 끝에 붙일 것이라고
#    짐작했는데, 실제로 재 보니 지문은 **2,235번째 글자**(파일 앞쪽)에 있었다.
#    그래서 꼬리에서 못 찾고 매번 전부 다시 읽어, 최적화가 오히려 느리게
#    만들고 있었다(39장에 63ms). 머리부터 읽는다.
_HEAD_BYTES = 64 * 1024

CSV_CATEGORIES = [
    ("대여소별 재고 (step0)", "대여소별 재고", "*.csv"),
    ("대여소별 주차대수 (step0)", "대여소별 주차대수", "*.csv"),
    ("대여소 정보 (step0)", "대여소 정보", "*.csv"),
    ("순수요 (step0)", "순수요", "*.csv"),
    ("재배치 정보 (step0)", "재배치 정보", "*.csv"),
    ("Pick/Drop 후보 (step1)", "ILP/후보", "*.csv"),
    ("ILP 계획 (step2)", "ILP", "ILP_plan*.csv"),
    ("VRP 계획 (step2)", "VRP", "VRP_plan*.csv"),
    ("성능 지표 (step4)", "성능 지표", "verification*.csv"),
    ("경로 요약 (step4)", "성능 지표", "route_summary*.csv"),
]


def _entry(path: Path) -> Dict:
    stat = path.stat()
    return {
        "name": path.name,
        # /files/, /preview/ 라우트에서 쓰는 data/ 기준 상대 경로
        "relpath": path.relative_to(DATA_ROOT).as_posix(),
        "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
        "mtime_raw": stat.st_mtime,
        "size_kb": max(1, stat.st_size // 1024),
    }


def _scan(categories) -> List[Dict]:
    result = []
    for title, subdir, pattern in categories:
        folder = PP_ROOT / subdir
        entries: List[Dict] = []
        if folder.exists():
            for path in folder.glob(pattern):
                # 목록을 만드는 사이 파일이 지워질 수 있다(`forget_run.py`·
                # 다시 그리는 지도). `stat()`이 FileNotFoundError를 내면 그
                # 파일만 빼고 간다 — 화면 전체가 500이 될 이유가 아니다(1.26.263).
                try:
                    if path.is_file():
                        entries.append(_entry(path))
                except OSError:
                    continue
            entries.sort(key=lambda e: e["mtime_raw"], reverse=True)
        result.append({"title": title, "entries": entries})
    return result


# 읽어 둔 산출물의 지문. 열쇠에 **mtime·크기·머리 내용의 해시**가 들어간다.
#
# ⚠️ `mapviz.source_stamp()`의 캐시는 걷어냈는데 여기는 두는 이유가 있다.
#    거기는 열쇠(모듈 경로)가 내용이 바뀌어도 그대로라 옛 값이 굳었다. 여기서도
#    한때 **"파일이 바뀌면 열쇠가 바뀐다 — 굳을 수가 없다"** 고 적어 뒀는데
#    그 전제가 틀렸다(1.26.128에서 실측). Windows에서 두 번 연속 쓰기가
#    **크기는 같고 mtime_ns 눈금도 같은** 경우가 300회 중 239회(80%)였다
#    (관측된 최소 증가분 0.34ms) — `(경로, mtime_ns, 크기)`만으로는 내용이
#    바뀌어도 열쇠가 안 바뀔 수 있다. 실제로 테스트가 같은 순서(쓰기→읽기
#    반복)로 200회 중 52회(26%) 이 자리에서 깨졌다.
#
#    머리(`_HEAD_BYTES`)는 **이미 읽고 있으므로** 그 해시를 열쇠에 얹는 데
#    추가 I/O가 들지 않는다. 캐시가 정말 아끼려던 것은 **지문이 없는 파일의
#    전체 읽기**(지금 39장 중 37장, 도장 이전 산출물이라 매번 끝까지 읽어야
#    한다)이고, 그 이득은 그대로 남는다 — 달라지는 것은 머리가 바뀌면
#    옛 결과를 안 돌려준다는 것뿐이다.
_FILE_STAMPS: Dict[tuple, Optional[str]] = {}


def _stamp_of_file(path: Path) -> Optional[str]:
    """저장된 지도에서 지문을 읽는다. 머리부터 보고 없을 때만 전부 읽는다."""
    try:
        stat = path.stat()
        with path.open("rb") as fh:
            head_bytes = fh.read(_HEAD_BYTES)
    except OSError:
        return None

    key = (str(path), stat.st_mtime_ns, stat.st_size,
           hashlib.sha256(head_bytes).digest())
    if key in _FILE_STAMPS:
        return _FILE_STAMPS[key]

    head = head_bytes.decode("utf-8", "ignore")
    found = mapviz.stamp_in(head)
    if found is None and stat.st_size > _HEAD_BYTES:
        # 앞쪽에 없으면 자리가 바뀐 것일 수 있다 — 놓치면 낡음을 영영
        # 모르므로, 확인만은 끝까지 한다(그 결과를 여기 남긴다).
        try:
            found = mapviz.stamp_in(
                path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            return None

    # 열쇠가 파일마다·판마다·머리 내용마다 다르므로 무한히 자라지는 않지만,
    # 실행이 쌓이면 옛 판의 열쇠가 남는다. 넉넉한 상한에서 통째로 비운다 —
    # 다시 읽으면 된다.
    if len(_FILE_STAMPS) > 512:
        _FILE_STAMPS.clear()
    _FILE_STAMPS[key] = found
    return found


def list_maps() -> List[Dict]:
    """지도 목록. 각 항목에 **낡았는지**(`stale`)를 함께 싣는다.

    `stale`은 셋 중 하나다:
      `True`   그린 뒤에 코드가 바뀌었다 — 다시 그려야 화면과 맞는다
      `False`  지금 코드로 그린 것이다
      `None`   도장 이전에 그린 산출물이라 **알 수 없다**(모른다고 말한다)
    """
    groups = _scan(MAP_CATEGORIES)
    for group, (_title, subdir, _pattern) in zip(groups, MAP_CATEGORIES):
        drawer = MAP_DRAWERS.get(subdir)
        current = mapviz.source_stamp(str(drawer)) if drawer else None
        stale_count = 0
        for entry in group["entries"]:
            stamp = _stamp_of_file(DATA_ROOT / entry["relpath"])
            entry["stamp"] = stamp
            entry["stale"] = None if (stamp is None or current is None) \
                else stamp != current
            if entry["stale"]:
                stale_count += 1
        group["stale_count"] = stale_count
        group["unknown_count"] = sum(
            1 for e in group["entries"] if e["stale"] is None)
    return groups


def list_csvs() -> List[Dict]:
    return _scan(CSV_CATEGORIES)


# ---------------- 실행으로 좁히기 (1.26.284) ----------------
#
# 파일 이름 규약은 `<접두>{duration} ({now}).<확장자>`다(pbr-pipeline 스킬). `{now}`가 DB의
# `run_label`이므로 **이름 꼬리만으로** 실행을 가를 수 있다 — DB를 바꿀 필요가 없다.
#
# ⚠️ **정규식으로 쪼개지 않는다.** 라벨에 괄호·공백이 들어갈 수 있다(`check_run_label`은
#    `\/:*?"<>|`만 막는다). `(\([^)]*\))$` 같은 식은 `A (B)`를 `B)`로 읽는다. 대신 **알려진
#    라벨** 가운데 ` ({라벨}){확장자}`로 끝나는 **가장 긴** 것에 파일을 배정한다 — 여는 괄호와
#    확장자까지 붙여 맞추므로 `16 웹점검`과 `2026-09-21 16 웹점검`도 서로 걸리지 않는다.


def run_of(name: str, labels: Iterable[str]) -> Optional[str]:
    """파일 이름의 `({now})` 꼬리가 가리키는 실행 — 알려진 라벨 중 **가장 긴** 것. 없으면 None."""
    suffix = Path(name).suffix
    best: Optional[str] = None
    for label in labels:
        if label and name.endswith(f" ({label}){suffix}") and (best is None or len(label) > len(best)):
            best = label
    return best


def _round_of(name: str, label: str) -> Optional[str]:
    """꼬리 ` ({label}).ext` 앞의 줄기가 끝나는 회차 코드(`project_config.DURATIONS`). 없으면 None.

    스냅샷(`대여소별_자전거대수 (X).csv`)처럼 회차가 없는 파일은 None — 회차로 좁혀도 남긴다
    (그 실행의 모든 회차가 쓰는 입력이다).
    """
    stem = name[: -len(f" ({label}){Path(name).suffix}")]
    return next((d for d in DURATIONS if stem.endswith(d)), None)


def for_run(groups: List[Dict], run_label: str, labels: Iterable[str], *,
            duration: Optional[str] = None, period: Optional[str] = None) -> List[Dict]:
    """`list_maps()`·`list_csvs()`의 결과를 **한 실행(과 회차)** 으로 좁힌 새 목록.

    - 파일은 `run_of()`로 실행에 배정한다. 고른 라벨도 후보에 넣는다 — DB에 없는 라벨이어도
      제 파일은 찾는다.
    - `period`를 주면 **어느 실행에도 배정되지 않은** 파일 가운데 ` ({period}).ext`로 끝나는
      것도 남긴다 — 순수요(`st_net_daily (26년 03월).csv`)는 실행이 아니라 기간으로 붙는다.
    - `duration`을 주면 회차가 있는 파일은 그 회차만, 회차가 없는 파일(스냅샷·순수요)은 남긴다.
    - **다른 실행으로 물러서지 않는다.** 분류가 비면 빈 목록이다 — 화면이 '이 실행의 파일이
      없습니다'라고 말한다(`/maps`가 '가장 최근'이라며 다른 계획의 경로를 띄운 것이 계기).
    - 지도 분류의 `stale_count`·`unknown_count`는 좁힌 항목으로 다시 센다.
    - 기간으로 붙인 파일에는 `rewritten_after_run`을 단다 (1.26.284 검토). 기간 파일은 실행이
      아니라 기간의 이름이라, 같은 달로 **나중에 돈 다른 실행이 덮어쓴다**(실측: 09-21 16:25 실행의
      순수요로 09-23 11:00에 다시 쓰인 파일이 붙었다 — 다른 파일은 모두 16:24~16:27). 짐작하지
      않고 시각만 견준다: 그 실행 **자기 파일 가운데 가장 늦은 것**보다 늦게 쓰였으면 `True`,
      견줄 자기 파일이 없으면 `None`(모름). 실행 시각(`runs.created_at`)과 견주지 않는 것은,
      그 실행의 순수요 단계가 실행 행이 생긴 **뒤에** 파일을 쓰므로 제 파일도 늦게 나오기 때문이다.
    """
    known = set(labels) | {run_label}
    own_times = [entry.get("mtime_raw") for group in groups for entry in group.get("entries", [])
                 if entry.get("mtime_raw") is not None and run_of(entry["name"], known) == run_label]
    own_newest = max(own_times) if own_times else None
    narrowed = []
    for group in groups:
        kept = []
        for entry in group.get("entries", []):
            name = entry["name"]
            owner = run_of(name, known)
            if owner == run_label:
                code = _round_of(name, run_label)
                if duration and code is not None and code != duration:
                    continue
                kept.append(entry)
            elif owner is None and period and name.endswith(f" ({period}){Path(name).suffix}"):
                later = (None if own_newest is None or entry.get("mtime_raw") is None
                         else entry["mtime_raw"] > own_newest)
                kept.append({**entry, "rewritten_after_run": later})
        new = {**group, "entries": kept}
        if "stale_count" in group:
            new["stale_count"] = sum(1 for e in kept if e.get("stale"))
            new["unknown_count"] = sum(1 for e in kept if e.get("stale") is None)
        narrowed.append(new)
    return narrowed


def tag_runs(groups: List[Dict], labels: Iterable[str]) -> List[Dict]:
    """항목마다 `run_label`(그 파일의 실행, 모르면 None)을 단다 — 거르지 않는다.

    인자 없는 `/maps`의 미리보기가 **어느 실행의 지도인지** 캡션에 밝히는 데 쓴다(1.26.284).
    예전에는 '가장 최근'이라고만 적어, 최신 계획에 경로 지도가 없을 때 하루 전 다른 계획의
    경로가 그 이름으로 떴다(실측 — 그 사실은 캡션의 파일 이름으로만 드러났다).
    """
    known = list(labels)
    for group in groups:
        for entry in group.get("entries", []):
            entry["run_label"] = run_of(entry["name"], known)
    return groups


def latest_outputs(limit_per_category: int = 2) -> List[Dict]:
    """대시보드 요약용: 카테고리별 최신 파일 일부."""
    summary = []
    for group in list_maps() + list_csvs():
        if group["entries"]:
            summary.append({
                "title": group["title"],
                "entries": group["entries"][:limit_per_category],
            })
    return summary


def safe_resolve(relpath: str) -> Optional[Path]:
    """data/ 기준 상대 경로를 검증해 절대 경로로 돌려준다.

    data/ 밖으로 나가거나 허용 확장자가 아니면 None.
    """
    try:
        target = (DATA_ROOT / relpath).resolve()
    except (OSError, ValueError):
        return None
    if DATA_ROOT.resolve() not in target.parents:
        return None
    if target.suffix.lower() not in ALLOWED_SUFFIXES:
        return None
    if not target.is_file():
        return None
    return target

