"""실험 — **차량 정비 결원 스트레스 테스트** (TODO 대기-8 / P4-9, 1.26.72).

## 무엇을 묻나

`vehicle.active` 플래그와 로테이션(`assign_vehicles`)은 **설계돼 있고 단발
테스트도 있다** — *"정비 차량이 배정에서 빠지는가"*. 그런데 **여러 회차에 걸쳐
결원이 이어질 때** 어떻게 되는지는 재 본 적이 없다.

📌 **12장이 이 질문에 근거를 줬다.** `wanted_vehicles()`가 K를 12~16으로 정하므로
**19~21번째 차는 어느 회차에서도 쓰이지 않는다** — 즉 **여유분이 2~3대 있다.**
정비로 그만큼 빠지면 바로 계획이 잘리는지, 아니면 견디는지가 이 실험이다.

    견딘다면 → 논문 8장의 "정비를 반영하지 않았다"를
               **"반영해도 견딘다"** 로 바꿔 쓸 수 있다.

## 재는 것

① **집행 가능성** — 결원 0·1·3·5·7대에서 `assign_vehicles`가 실패하는 회차 수
② **형평성** — 차량별 누적 소요시간의 표준편차가 결원 중 얼마나 벌어지고
   결원이 끝난 뒤 **몇 회차 만에 수렴**하는가
③ **결품** — 차가 모자라 못 돈 군집이 결품에 얼마를 더하는가

⚠️ **어느 차가 빠지느냐로 결과가 흔들린다.** 그래서 **씨앗을 여럿 써서** 결원
차량을 뽑는다 — 씨앗 1회로 재면 그 흔들림을 실험 효과로 착각한다(11장 교훈).

## 안전장치

🔴 **운영 DB를 건드리지 않는다.** `vehicle`·`vehicle_assignment`만 담은 **임시
사본**을 만들어 거기서 돌린다. 정비 결원을 흉내내려면 `active=0`을 써야 하는데,
그것을 실 DB에 남기면 다음 파이프라인 실행이 영향을 받는다.

⚠️ **`sync_fleet()`이 `active`를 되살린다** — 단, `note`가 "보유 대수 축소로
제외"인 차량만이다. 정비 결원은 **다른 note**를 쓰므로 살아남는다. 이 실험은
그 동작 자체도 확인한다.

사용법:
    python experiments/structure/fleet_outage_stress.py
    python experiments/structure/fleet_outage_stress.py --outages "0,1,3,5,7" --seeds "42,7,13"
"""
from __future__ import annotations

import argparse
import os
import random
import shutil
import sqlite3
import statistics
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import db                                              # noqa: E402
from project_config import FLEET_SIZE                  # noqa: E402

MAINT_NOTE = "정비 결원(스트레스 테스트)"

# 회차당 필요한 군집 수. 12장 실측(K=12~16)을 그대로 쓴다 — 파이프라인을 다시
# 돌리지 않고 배정 로직만 흔들기 위해서다.
CLUSTER_COUNTS = [12, 14, 16]


def make_scratch_db() -> Path:
    """`vehicle`·`vehicle_assignment`만 담은 임시 DB. 운영 DB는 읽기만 한다."""
    tmp = Path(tempfile.gettempdir()) / "pbr_fleet_stress.db"
    if tmp.exists():
        tmp.unlink()
    conn = sqlite3.connect(tmp)
    db.init_schema(conn)
    conn.close()
    return tmp


def set_maintenance(conn, vehicle_ids: list) -> None:
    """지정한 차량을 정비로 뺀다(note를 남겨 sync_fleet이 되살리지 못하게)."""
    conn.execute("UPDATE vehicle SET active = 1, note = NULL WHERE note = ?",
                 (MAINT_NOTE,))
    if vehicle_ids:
        marks = ",".join("?" * len(vehicle_ids))
        conn.execute(
            f"UPDATE vehicle SET active = 0, note = ? WHERE vehicle_id IN ({marks})",
            [MAINT_NOTE, *vehicle_ids])
    conn.commit()


def run_rounds(conn, rounds: int, outage: int, rng: random.Random,
               recover_at: int) -> dict:
    """회차를 이어 돌리며 배정한다. `recover_at` 회차부터 결원을 복구한다."""
    db.ensure_fleet(conn, FLEET_SIZE)
    conn.execute("DELETE FROM vehicle_assignment")
    set_maintenance(conn, [])
    conn.commit()

    all_ids = db.vehicle_ids(FLEET_SIZE)
    실패, 기록 = 0, []

    for r in range(rounds):
        if r == 0 and outage:
            set_maintenance(conn, rng.sample(all_ids, outage))
        if r == recover_at:
            set_maintenance(conn, [])            # 결원 복구

        k = CLUSTER_COUNTS[r % len(CLUSTER_COUNTS)]
        loads = {c: 60.0 + 40.0 * rng.random() for c in range(k)}
        label, duration = f"stress-{r}", "_05_10"
        try:
            mapping = db.assign_vehicles(conn, loads, label, duration)
        except ValueError:
            실패 += 1
            기록.append({"회차": r, "결원중": r < recover_at, "실패": True,
                         "필요K": k, "표준편차": None})
            continue

        db.save_assignments(conn, label, duration, [
            {"vehicle_id": v, "cluster": c, "stations": 5, "bikes": 30,
             "distance_km": 20.0, "minutes": loads[c]}
            for c, v in mapping.items()])

        # 형평성 — 활성 차량의 누적 소요시간 표준편차
        acc = pd.read_sql(
            "SELECT v.vehicle_id, COALESCE(SUM(a.minutes), 0) AS minutes"
            " FROM vehicle v LEFT JOIN vehicle_assignment a"
            " ON a.vehicle_id = v.vehicle_id"
            " WHERE v.active = 1 GROUP BY v.vehicle_id", conn)
        sd = float(acc.minutes.std(ddof=0)) if len(acc) > 1 else 0.0
        기록.append({"회차": r, "결원중": r < recover_at, "실패": False,
                     "필요K": k, "표준편차": sd})

    return {"실패": 실패, "기록": 기록}


def summarize(results: dict, rounds: int, recover_at: int) -> None:
    print("\n" + "=" * 84)
    print(f"차량 정비 결원 스트레스 — 보유 {FLEET_SIZE}대 · {rounds}회차"
          f" (0~{recover_at - 1} 결원, {recover_at}~ 복구)")
    print("=" * 84)

    print(f"\n① 집행 가능성 — 배정에 실패한 회차 수 (씨앗 평균)")
    print(f"{'결원':>5} {'가용':>5} {'실패회차':>9} {'판정':>6}")
    for outage, per_seed in results.items():
        실패 = statistics.mean(r["실패"] for r in per_seed)
        가용 = FLEET_SIZE - outage
        판정 = "✅ 견딤" if 실패 == 0 else f"🔴 {실패:.1f}건"
        print(f"{outage:>5} {가용:>5} {실패:>9.1f} {판정:>6}")

    print(f"\n② 형평성 — 누적 소요시간 표준편차(분)")
    print(f"{'결원':>5} {'결원중':>9} {'복구후':>9} {'수렴회차':>9}")
    for outage, per_seed in results.items():
        during, after, 수렴 = [], [], []
        for r in per_seed:
            rows = [x for x in r["기록"] if not x["실패"]]
            d = [x["표준편차"] for x in rows if x["결원중"]]
            a = [x["표준편차"] for x in rows if not x["결원중"]]
            if d:
                during.append(statistics.mean(d))
            if a:
                after.append(a[-1])
                # 결원 끝난 표준편차가 결원 전 최댓값 아래로 내려오기까지
                기준 = max(d) if d else 0.0
                이후 = [i for i, v in enumerate(a) if v <= 기준]
                수렴.append(이후[0] + 1 if 이후 else len(a) + 1)
        if during and after:
            print(f"{outage:>5} {statistics.mean(during):>9.1f}"
                  f" {statistics.mean(after):>9.1f}"
                  f" {statistics.mean(수렴):>9.1f}")

    print("\n읽는 법")
    print("  · 실패회차 = assign_vehicles가 '차량이 부족합니다'로 멈춘 회차 수.")
    print("  · 표준편차가 클수록 특정 차에 일이 몰린 것이다(0이면 완전 균등).")
    print("  · 수렴회차 = 결원이 끝난 뒤 표준편차가 결원 중 최댓값 아래로"
          " 내려오기까지 걸린 회차 수.")
    print("=" * 84)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="차량 정비 결원 스트레스 테스트 (TODO 대기-8)")
    parser.add_argument("--outages", default="0,1,3,5,7",
                        help="동시에 빠지는 차량 수")
    parser.add_argument("--seeds", default="42,7,13")
    parser.add_argument("--rounds", type=int, default=12)
    parser.add_argument("--recover-at", type=int, default=6,
                        help="이 회차부터 결원을 복구한다")
    args, _ = parser.parse_known_args()

    outages = [int(x) for x in args.outages.split(",") if x.strip()]
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]

    over = [o for o in outages if o >= FLEET_SIZE]
    if over:
        raise SystemExit(f"보유 {FLEET_SIZE}대인데 결원 {over}는 잴 수 없습니다.")

    scratch = make_scratch_db()
    print(f"임시 DB: {scratch}  (운영 DB는 건드리지 않습니다)")
    print(f"결원 {outages} · 씨앗 {seeds} · {args.rounds}회차"
          f" · {args.recover_at}회차부터 복구\n")

    results = {}
    try:
        conn = sqlite3.connect(scratch)
        for outage in outages:
            per_seed = []
            for seed in seeds:
                per_seed.append(run_rounds(conn, args.rounds, outage,
                                           random.Random(seed), args.recover_at))
            results[outage] = per_seed
            print(f"  결원 {outage}대 완료", flush=True)
        conn.close()
    finally:
        if scratch.exists():
            scratch.unlink()                     # 임시 DB는 반드시 지운다

    summarize(results, args.rounds, args.recover_at)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
