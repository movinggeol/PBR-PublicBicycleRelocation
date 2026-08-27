"""다른 PC가 모은 재고 시계열을 이 PC의 DB로 합친다 (docs/구현/COLLECTOR.md 11장).

**왜 필요한가.** 수집기의 정본은 `data/bike_system.db`인데, 이 파일은 1GB를 넘고
`.gitignore`에 걸려 있다 — git으로는 절대 오갈 수 없다. 두 PC가 서로 다른 창을
맡아 수집하면(예: A는 09~17시, B는 야간·휴일) 두 DB에 따로 쌓이므로, 분석 전에
한쪽으로 모아야 한다.

**먼저 수집한 것이 이긴다.** 저장은 `INSERT OR IGNORE`다 — 로컬에 이미 있는
`(observed_at, station_id)`는 건드리지 않고 **비어 있는 틱만 채운다.** 수집기 본체의
`INSERT OR REPLACE`(재실행 멱등)와 정반대인데, 의도한 차이다:

  - 수집기는 **같은 PC가 같은 틱을 다시 받아온** 경우라 새 값이 더 정확하다.
  - 병합은 **다른 PC가 이미 잰 값을 나중에 들고 온** 경우다. 겹치는 구간은 같은
    API의 같은 값이므로 굳이 덮어쓸 이유가 없고, 두 PC의 시계가 몇 초씩 어긋나
    `fetched_at`으로 선후를 가리는 것도 믿을 수 없다.

**입력은 두 가지를 받는다.**

    python tools/merge_stock.py 옮겨온폴더/            # 일별 CSV 묶음 (가볍다)
    python tools/merge_stock.py 옮겨온_bike_system.db  # DB 파일 (마스터까지 온다)

CSV(`stock_YYYY-MM-DD.csv`)에는 재고만 있고 **이름·좌표(마스터)가 없다.** B PC만
관측한 날짜가 있다면 그날 마스터가 이 PC에 없게 되므로, 그런 날이 섞였다면 DB
파일로 옮기는 편이 낫다 — 이 스크립트가 어느 날 마스터가 비었는지 알려준다.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

import db
from project_config import DATA_ROOT, is_holiday

HISTORY_DIR = DATA_ROOT / "raw_data" / "재고이력"
CSV_GLOB = "stock_*.csv"
CSV_COLUMNS = ("observed_at", "station_id", "stock")
DB_SUFFIXES = (".db", ".sqlite", ".sqlite3")


class MergeError(RuntimeError):
    """읽을 수 없는 원천. 부르는 쪽이 사람에게 보여줄 문구를 담는다."""


# ---- 원천 읽기 ----

def read_csv_dir(source: Path) -> Tuple[pd.DataFrame, List[Path]]:
    """폴더(또는 파일 하나) 안의 일별 CSV를 모아 한 프레임으로 만든다.

    `collect_log.csv`는 이름이 달라 glob에서 자연히 빠진다 — 형식이 다르므로
    섞여 들어오면 안 된다.
    """
    paths = sorted(source.glob(CSV_GLOB)) if source.is_dir() else [source]
    if not paths:
        raise MergeError(f"{source}에 {CSV_GLOB} 파일이 없습니다.")

    frames = []
    for path in paths:
        frame = pd.read_csv(path, dtype={"station_id": str})
        missing = [column for column in CSV_COLUMNS if column not in frame.columns]
        if missing:
            raise MergeError(f"{path.name}에 {', '.join(missing)} 컬럼이 없습니다.")
        frames.append(frame[list(CSV_COLUMNS)])
    return pd.concat(frames, ignore_index=True), paths


def read_db_file(source: Path) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """다른 PC의 DB에서 재고 시계열과 마스터를 읽는다.

    읽기 전용으로 연다(`mode=ro`) — 옮겨온 원본은 증거물이라 손대지 않는다.
    """
    try:
        conn = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    except sqlite3.OperationalError as error:
        raise MergeError(f"{source}를 열 수 없습니다: {error}")
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "stock_history" not in tables:
            raise MergeError(f"{source}에 stock_history 테이블이 없습니다 — "
                             "PBR DB가 맞는지 확인하세요.")
        history = pd.read_sql_query(
            "SELECT observed_at, station_id, stock FROM stock_history", conn)
        master = (pd.read_sql_query(
            "SELECT observed_on, station_id, station_name, parking_info,"
            " lat, lon FROM stock_station_master", conn)
            if "stock_station_master" in tables else pd.DataFrame())
    finally:
        conn.close()
    return history, master


def load_source(source: Path) -> Tuple[pd.DataFrame, pd.DataFrame, str]:
    """원천 종류를 판단해 (재고, 마스터, 사람이 읽을 설명)을 돌려준다."""
    if not source.exists():
        raise MergeError(f"원천이 없습니다: {source}")
    if source.is_file() and source.suffix.lower() in DB_SUFFIXES:
        history, master = read_db_file(source)
        return history, master, f"DB 파일 {source.name}"
    history, paths = read_csv_dir(source)
    label = f"CSV {len(paths)}개" if len(paths) > 1 else f"CSV {paths[0].name}"
    return history, pd.DataFrame(), label


# ---- 정리 ----

def normalize(history: pd.DataFrame) -> pd.DataFrame:
    """저장 형식에 맞춘다: observed_at 'YYYY-MM-DD HH:MM', stock 정수.

    원천 안에서 같은 (시각, 대여소)가 두 번 나오면 **첫 줄을 남긴다** — CSV 백업은
    append라, 한 틱을 손으로 다시 돌리면 같은 키가 두 줄이 될 수 있다.
    """
    if history.empty:
        return history
    frame = history.copy()
    stamps = pd.to_datetime(frame["observed_at"], errors="coerce")
    frame["observed_at"] = stamps.dt.strftime("%Y-%m-%d %H:%M")
    frame["station_id"] = frame["station_id"].astype(str)
    frame["stock"] = pd.to_numeric(frame["stock"], errors="coerce")
    frame = frame.dropna(subset=["observed_at", "stock"])
    frame["stock"] = frame["stock"].astype(int)
    return frame.drop_duplicates(subset=["observed_at", "station_id"], keep="first")


# ---- 저장 ----

def merge_history(conn: sqlite3.Connection, history: pd.DataFrame) -> int:
    """비어 있는 틱만 채운다. **먼저 수집한 것이 이긴다** — 모듈 설명 참고.

    새로 들어간 행 수를 돌려준다(이미 있어서 건너뛴 것은 세지 않는다).
    fetched_at은 넣지 않는다 — 이 PC가 부른 시각이 아니라 남기면 거짓말이 된다.
    """
    if history.empty:
        return 0
    rows = list(zip(history["observed_at"], history["station_id"],
                    history["stock"].astype(int)))
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO stock_history (observed_at, station_id, stock)"
        " VALUES (?, ?, ?)", rows)
    conn.commit()
    return conn.total_changes - before


def merge_master(conn: sqlite3.Connection, master: pd.DataFrame) -> int:
    """마스터도 같은 규칙 — 이 PC에 이미 그날 마스터가 있으면 놔둔다."""
    if master.empty:
        return 0
    columns = ("station_name", "parking_info", "lat", "lon")
    rows = [(str(record["observed_on"]), str(record["station_id"]),
             *(record.get(column) for column in columns))
            for record in master.to_dict("records")]
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO stock_station_master"
        " (observed_on, station_id, station_name, parking_info, lat, lon)"
        " VALUES (?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    return conn.total_changes - before


# ---- 보고 ----

def summarize_days(history: pd.DataFrame) -> pd.DataFrame:
    """원천이 담고 있는 날짜별 틱 수. 무엇을 들여오는지 저장 전에 보여준다."""
    if history.empty:
        return pd.DataFrame(columns=["날짜", "틱", "대여소"])
    frame = history.copy()
    frame["날짜"] = frame["observed_at"].str.slice(0, 10)
    grouped = frame.groupby("날짜").agg(
        틱=("observed_at", "nunique"), 대여소=("station_id", "nunique"))
    return grouped.reset_index()


def missing_master_days(conn: sqlite3.Connection,
                        history: pd.DataFrame) -> List[str]:
    """들여온 날짜 중 이 PC에 마스터(이름·좌표)가 없는 날.

    CSV로만 옮기면 반드시 생긴다 — CSV에는 재고밖에 없기 때문이다.
    """
    if history.empty:
        return []
    days = sorted({stamp[:10] for stamp in history["observed_at"]})
    return [day for day in days if not db.has_stock_master(conn, day)]


def describe_window(history: pd.DataFrame) -> str:
    """어느 시간대를 채우는지 한 줄로. 창을 나눠 맡았는지 눈으로 확인하는 용도."""
    if history.empty:
        return ""
    times = pd.to_datetime(history["observed_at"])
    clock = times.dt.strftime("%H:%M")
    span = f"{clock.min()}~{clock.max()}"
    holidays = sum(1 for day in {stamp.date() for stamp in times} if is_holiday(day))
    return f"{span} · 휴일 {holidays}일 포함" if holidays else span


# ---- CLI ----

def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="다른 PC가 모은 재고 시계열을 이 PC의 DB로 합친다.")
    parser.add_argument("source", type=Path,
                        help="옮겨온 재고이력 폴더, 일별 CSV, 또는 DB 파일")
    parser.add_argument("--dry-run", action="store_true",
                        help="무엇이 들어올지만 보여주고 저장하지 않는다")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        history, master, label = load_source(args.source)
    except MergeError as error:
        print(f"[실패] {error}")
        return 1

    history = normalize(history)
    if history.empty:
        print(f"[건너뜀] {label}에 읽을 수 있는 재고 행이 없습니다.")
        return 0

    days = summarize_days(history)
    print(f"원천: {label}")
    print(f"  행     : {len(history):,}개 · {len(days)}일 · {describe_window(history)}")
    print(days.to_string(index=False))

    if args.dry_run:
        print("\n[모의] 저장하지 않았습니다.")
        return 0

    with db.session() as conn:
        added = merge_history(conn, history)
        added_master = merge_master(conn, master)
        gaps = missing_master_days(conn, history)

    print(f"\n  새로 채움 : {added:,}행")
    print(f"  이미 있음 : {len(history) - added:,}행 (먼저 수집한 값을 남겼습니다)")
    if added_master:
        print(f"  마스터    : {added_master:,}행")
    if gaps:
        print(f"\n  ⚠ 이름·좌표가 없는 날: {', '.join(gaps)}")
        print("    CSV에는 재고만 들어 있습니다. 그 PC의 DB 파일로 다시 합치면 채워집니다.")
    print("\n  현황 확인 : python tools/collect_stock.py --status")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
