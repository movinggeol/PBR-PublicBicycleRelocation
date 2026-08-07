"""파이프라인 산출물(data/pp_data) 스캔과 안전한 경로 해석.

웹에서 서빙하는 파일은 data/ 아래의 .html / .csv 로 제한한다.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from project_config import PROJECT_ROOT

DATA_ROOT = PROJECT_ROOT / "data"
PP_ROOT = DATA_ROOT / "pp_data"

ALLOWED_SUFFIXES = {".html", ".csv"}

# (표시 이름, pp_data 기준 폴더, 패턴, 종류)
MAP_CATEGORIES = [
    ("클러스터 지도 (step1)", "ILP/visualization", "*.html"),
    ("VRP 경로 지도 (step3)", "VRP/visualization", "*.html"),
    ("불균형 개선 지도 (step4)", "성능 지표/visualization", "*.html"),
]

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
            entries = [_entry(p) for p in folder.glob(pattern) if p.is_file()]
            entries.sort(key=lambda e: e["mtime_raw"], reverse=True)
        result.append({"title": title, "entries": entries})
    return result


def list_maps() -> List[Dict]:
    return _scan(MAP_CATEGORIES)


def list_csvs() -> List[Dict]:
    return _scan(CSV_CATEGORIES)


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


def latest_file(subdir: str, pattern: str) -> Optional[Path]:
    """pp_data/subdir 안에서 pattern에 맞는 가장 최근 파일."""
    folder = PP_ROOT / subdir
    if not folder.exists():
        return None
    files = [p for p in folder.glob(pattern) if p.is_file()]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)
