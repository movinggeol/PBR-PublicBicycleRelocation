"""대여소별 재고 CSV의 거치대 설명(parking_info)을 파싱해 주차 가능 대수를 계산한다.

입력: data/pp_data/대여소별 재고/대여소별_자전거대수 ({now}).csv  (tashu_api.py 출력)
출력: data/pp_data/대여소별 주차대수/대여소별_주차대수 ({now}).csv
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd

import db
from project_config import DATA_ROOT, ensure_output_dirs, get_runtime_config, exit_if_help

# read_csv
file_path = str(DATA_ROOT / "pp_data/대여소별 재고/대여소별_자전거대수 ({now}).csv")
# to_csv
out_file_path = str(DATA_ROOT / "pp_data/대여소별 주차대수/대여소별_주차대수 ({now}).csv")


def parse_capacity(x) -> int:
    s = str(x).strip()
    total = 0

    # 쉼표로 여러 덩어리 분리: "10대용*1, 5대용*2"
    parts = [p.strip() for p in s.split(",")]

    for p in parts:
        # 예: "10대용*1", "5대용(스텐)*2", "1대용*15"
        m = re.search(r"(\d+)\s*대용(?:\([^)]*\))?\s*\*\s*(\d+)", p)
        if m:
            size = int(m.group(1))   # 10, 5, 1 ...
            cnt  = int(m.group(2))   # 1,2,3...
            total += size * cnt
        else:
            # 혹시 '*' 없이 "10대용" 같은 변형이 있으면 1개로 가정(선택)
            m2 = re.search(r"(\d+)\s*대용(?:\([^)]*\))?", p)
            if m2:
                size = int(m2.group(1))
                total += size * 1
    return total


def parking_capacity(info: pd.Series) -> pd.Series:
    """거치대 설명(`이름 / 10대용*1 / 10`) → 거치대 수. 대수를 알 수 없으면 다른 대여소의 중앙값.

    🔴 **'/'가 없어도 상한이 걸리게 한다** (1.26.409, 사용자 결정 2026-11-05). 예전에는 '/' 뒤만 읽어
    '/'가 없는 행(지금 `ST0828` — 설명이 대여소 이름뿐이다)을 **버렸고**, 그러면 step0의
    `clip(upper=parking_lot × 비율)`이 NaN을 받아 **상한 없음**이 됐다(pandas는 NaN 상한을 무시한다).
    이제 '/'가 없으면 설명 전체에서 `N대용`을 찾고, 그래도 없으면 **대수를 아는 대여소의 중앙값**을 쓴다.
    ponytail: 대수를 모르는 곳은 중앙값으로 둔다 — 실제 거치대를 확인하면 아래 수동 보정 줄에 넣는다
    """
    parts = info.astype("string").str.split("/", n=2, expand=True)
    has_slash = parts[1].notna() if 1 in parts else pd.Series(False, index=info.index)
    text = (parts[1].where(has_slash, parts[0]) if 1 in parts else parts[0]).str.strip()
    stated_none = (text == "parking_lot 없음").fillna(False).astype(bool)   # '없음'이라고 말한 곳 — 0대 그대로
    capacity = text.where(~stated_none, "0").apply(parse_capacity).astype(float)
    unknown = (capacity == 0) & ~stated_none & ~has_slash
    if unknown.any():
        median = float(capacity[(capacity > 0) & has_slash].median())
        capacity[unknown] = median
        print(f"[경고] 거치대 설명에 대수가 없는 대여소 {int(unknown.sum())}곳 — 다른 대여소의 중앙값 "
              f"{median:g}대를 상한에 쓴다: {', '.join(info.index[unknown].astype(str)[:10])}")
    return capacity.astype(int)


def main() -> None:
    config = get_runtime_config()
    now = config.now

    # 앞 단계(tashu_api)의 재고를 DB에서 먼저 읽는다 (1.26.166).
    df, _ = db.read_step_output('station_stock', file_path.format(now=now),
                                run_label=now)
    if df.empty:
        raise SystemExit(f"대여소별 재고가 없습니다 — tashu_api.py를 먼저 돌리세요 ({now}).")

    # 🔴 이름으로 고른다 — DB에서 오면 앞에 `run_label`이 붙어 자리가 밀린다.
    df = df[['station_id', 'parking_info', 'lat', 'lon']].copy()

    # '/'가 없는 설명도 버리지 않는다 — 버리면 그 대여소의 목표 재고 상한이 풀린다(1.26.409).
    df['parking_lot'] = parking_capacity(df.set_index('station_id')['parking_info']).to_numpy()
    df = df[['station_id', 'lat', 'lon', 'parking_lot']]

    # --------------------------------------------------------------------
    # 실제와 다른 정보 수정(도로뷰 활용)
    df.loc[df['station_id'] == 'ST0370', 'parking_lot'] = 30
    df.loc[df['station_id'] == 'ST1133', 'parking_lot'] = 5
    df.loc[df['station_id'] == 'ST1392', 'parking_lot'] = 5

    df.loc[df['station_id'] == 'ST1341', 'parking_lot'] = 30  # 실제 자전거 주차장 매우 큼
    # --------------------------------------------------------------------

    print(f"모든 대여소의 주차대수는 : {df['parking_lot'].sum(axis=0)}")

    ensure_output_dirs()
    df.to_csv(out_file_path.format(now=now), encoding='utf-8', index=False)
    print(f"\n{out_file_path.format(now=now)} 가 저장되었습니다.")

    # CSV·DB 이중 기록 (DB_PLAN 2단계). 다음 단계는 DB를 먼저 읽는다
    # (db.read_step_output, 1.26.166) — CSV는 DB가 비었을 때의 폴백이다.
    db.save_output("parking_lot", df, run_label=now, period=config.period)


if __name__ == '__main__':
    exit_if_help(__doc__)
    main()
