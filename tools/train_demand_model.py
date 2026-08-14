"""목표 재고 분위수 모델을 학습한다 (docs/DEMAND_DISTRIBUTION.md 5장).

DB에 적재된 순수요 전 기간으로 (직전 달 피처 → 이번 달 실제 순수요) 표를 만들고
95분위 회귀를 학습해 `data/models/target_quantile.pkl`에 저장한다.

모델이 없으면 `calculate_target_qty.py`가 조용히 `mu + z·sigma`로 돌아가므로,
학습은 **선택**이다. 이득이 확인되기 전까지는 그게 안전한 기본값이다.

실행:
    python tools/train_demand_model.py
    python tools/train_demand_model.py --quantile 0.9
    python tools/train_demand_model.py --dry-run     # 학습 데이터 크기만 확인
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import db
import demand_model


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quantile", type=float,
                        default=demand_model.TARGET_QUANTILE,
                        help=f"목표 분위수 (기본 {demand_model.TARGET_QUANTILE})")
    parser.add_argument("--out", help="저장 경로 (기본 data/models/target_quantile.pkl)")
    parser.add_argument("--dry-run", action="store_true", help="학습 없이 데이터만 확인")
    args = parser.parse_args()

    with db.session() as conn:
        periods = [r[0] for r in conn.execute(
            "SELECT DISTINCT period FROM net_demand ORDER BY period")]
        if len(periods) < 2:
            print(f"기간이 {len(periods)}개뿐이라 학습할 수 없습니다"
                  " (직전 달 → 이번 달 쌍이 필요합니다).")
            return 1
        net = {p: db.load_frame(conn, "net_demand", period=p) for p in periods}

    frame = demand_model.training_frame(net)
    if frame.empty:
        print("학습 데이터를 만들지 못했습니다. 이어지는 달이 있는지 확인하세요.")
        return 1

    print(f"기간 {len(periods)}개 → 학습 표본 {len(frame):,}행"
          f" · 대여소 {frame['station_id'].nunique():,}곳")
    print(f"  시간대별: "
          + ", ".join(f"{demand_model.DURATIONS[i]} {n:,}"
                      for i, n in frame['duration_idx'].value_counts().sort_index().items()))
    print(f"  요일별:   "
          + ", ".join(f"{demand_model.DAY_TYPES[i]} {n:,}"
                      for i, n in frame['day_type_idx'].value_counts().sort_index().items()))

    if args.dry_run:
        return 0

    print(f"\n{args.quantile:.0%} 분위수 회귀 학습 중...")
    model = demand_model.train(frame, quantile=args.quantile)
    path = demand_model.save(model, args.quantile, args.out)

    print(f"저장: {path}")
    print("\n다음 실행부터 calculate_target_qty가 이 모델을 씁니다.")
    print("먼저 검증하세요 — python experiments/quantile_model_eval.py")
    print("(베이스라인을 못 이기면 모델 파일을 지우면 기존 공식으로 돌아갑니다.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
