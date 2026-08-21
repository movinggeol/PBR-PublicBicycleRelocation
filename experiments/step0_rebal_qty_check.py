"""재배치량(rebal_qty) 산출물을 눈으로 확인하는 진단 스크립트.

step0의 `calculate_target_qty`가 만든 표가 그럴듯한지 빠르게 보는 용도다.
Pick 가능량과 Drop 필요량이 얼마나 어긋나 있는지가 핵심이다 — 둘 중 **적은 쪽**까지만
옮길 수 있으므로(step1의 누적합 컷), 격차가 크면 그만큼 계획에서 잘려 나간다.

1.18.8 이전에는 `duration = '_05_15'`(지금은 없는 시간대)와 `datetime.now()`가
박혀 있어 **실행하면 그냥 깨졌다.** now는 실행 시각이 아니라 파이프라인 실행을 묶는
라벨이므로 `project_config`에서 읽어야 한다.

사용법:
    python experiments/step0_rebal_qty_check.py
    python experiments/step0_rebal_qty_check.py --now "2026-08-11 real" --duration "_05_10"
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from project_config import (  # noqa: E402
    PROJECT_ROOT, REBAL_MIN_QTY, duration_list, get_runtime_config,
)

FILE_PATH = str(PROJECT_ROOT / "data/pp_data/재배치 정보/rebal_qty{duration} ({now}).csv")


def main():
    config = get_runtime_config()
    print(f"실행 라벨(now): {config.now}\n")

    for duration in duration_list(config):
        path = Path(FILE_PATH.format(duration=duration, now=config.now))
        if not path.is_file():
            print(f"[건너뜀] {duration}: 산출물이 없습니다 ({path.name})")
            continue

        frame = pd.read_csv(path, encoding="utf-8")
        target = frame[frame["rebal_qty"].abs() > REBAL_MIN_QTY]
        pick = -target.loc[target["rebal_qty"] < 0, "rebal_qty"].sum()
        drop = target.loc[target["rebal_qty"] > 0, "rebal_qty"].sum()

        print(f"[{duration}] 대여소 {len(frame)}곳 중 작업 대상 {len(target)}곳"
              f" (|rebal_qty| > {REBAL_MIN_QTY})")
        print(f"    Pick 가능 {pick:>5}대 · Drop 필요 {drop:>5}대"
              f" · 실제로 옮길 수 있는 최대 {min(pick, drop):>5}대")
        if pick and drop:
            print(f"    수급 격차 {abs(pick - drop):>5}대"
                  f" ({abs(pick - drop) / max(pick, drop) * 100:.0f}%)"
                  f" — 이만큼은 계획에서 잘려 나갑니다")
        else:
            print("    ⚠ 한쪽이 비었습니다 — 이 회차는 step1에서 건너뜁니다")
        print()


if __name__ == "__main__":
    main()
