"""운영 K(수거 대수 학습, 1.26.375)를 이 PC에서 점검하고 손잡이 k를 56장 규칙으로 다시 고른다 (읽기 전용, 1.26.380).

## 왜

K는 계획을 세울 때 최근 평일 10일로 모형을 배운다. 두 가지가 남아 있었다(TODO 수거 줄의 남은 일).

1. **이 PC에서 학습이 얼마나 드나.** 회사 PC(수집 PC)는 메모리가 빠듯해지면 재고 수집이 틱을 잃는다. 이 PC에서 잰 값은
   학습 1회 약 4분 · 최대 528MB였다. 수집 PC에서 같은 값을 잰다.
2. **k를 다시 고르는 길.** k 0.7은 56장의 고르기 날(09-17 ~ 23)에서 *수거 총량을 90% 이상 채우는 가장 큰 k* 로 정한 값이다.
   날이 쌓이면 같은 규칙으로 다시 고를 수 있어야 한다.

## 무엇을 하나

- 고르기 날: 하루 144틱의 80% 이상을 덮은 최근 평일 `--days`일(오늘 제외).
- 학습 날: 고르기 날의 첫날보다 앞선 평일 `PICK_MODEL_TRAIN_DAYS`일(운영과 같은 규칙 `pick_model.training_days`).
- 고르기 날의 회차 시작 틱마다, 그 틱의 관측 재고로 지금 선정(step1)을 세우고 k마다 K의 수거가 총량을 얼마나 채우나를 센다.
  관측 결과(수거 무해)는 보지 않는다. 56장 `pick_mix.choose_k()`와 같은 규칙이다.
- 설정은 바꾸지 않는다. 권한 k를 쓰려면 `PBR_PICK_MODEL_QUANTILE`을 바꾼다(사용자 결정).

    python tools/pick_model_check.py                    # 고르기 평일 5일, k 0.5 ~ 0.95
    python tools/pick_model_check.py --days 2 --k 0.7   # 학습 시간 · 메모리만 빨리
"""
from __future__ import annotations

import argparse
import contextlib
import io
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import project_config  # noqa: E402  (콘솔 인코딩을 먼저 세운다)

import pandas as pd  # noqa: E402

import db  # noqa: E402
from experiments._shared import plan_label_before  # noqa: E402
from pipeline.step1_cluster import pick_model as pm  # noqa: E402
from project_config import DEFAULT_PERIOD, DURATIONS, duration_start_hour, format_seconds  # noqa: E402

K_GRID = (0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95)   # 56장 pick_mix.K_GRID
K_FILL = 0.90                                     # 56장 pick_mix.K_FILL


def peak_mb() -> float:
    """이 프로세스의 최대 작업 집합(MB). Windows가 아니면 ru_maxrss."""
    try:
        import ctypes
        from ctypes import wintypes

        class _PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("a", ctypes.c_size_t), ("b", ctypes.c_size_t), ("c", ctypes.c_size_t),
                        ("d", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

        k32, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
        pmc = _PMC()
        pmc.cb = ctypes.sizeof(_PMC)
        psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
        return pmc.PeakWorkingSetSize / 2**20
    except (AttributeError, OSError):
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def choose_k(ratio: dict, fill: float = K_FILL) -> float:
    """총량을 fill 이상 채우는 가장 큰 k. 닿는 k가 없으면 가장 많이 채우는 k(56장 choose_k와 같다)."""
    ok = [k for k, r in ratio.items() if r >= fill]
    return max(ok) if ok else max(ratio, key=lambda k: ratio[k])


def base_plan(net, info: pd.DataFrame, stock: pd.Series, duration: str, step1) -> tuple:
    """관측 재고로 지금 선정을 세운다. (base, full, st_rebal)"""
    inst = info[info["station_id"].isin(stock.index)].copy()
    inst["stock"] = inst["station_id"].map(stock).round().astype(int)
    target = pm._target_mod()
    stats, _d, _r = target.build_stats(net, inst[["station_id", "stock", "parking_lot"]], duration, verbose=False)
    st_rebal = target.compute_rebal_qty(stats)
    buf = io.StringIO()
    st_rebal.to_csv(buf, index=False)
    buf.seek(0)
    with contextlib.redirect_stdout(io.StringIO()):
        base = step1.select_top_unbalanced_st(buf, duration, inst)
    full = st_rebal.merge(inst, how="left", on="station_id", suffixes=("", "_info"))[pm.INFO_COLS]
    return base, full, st_rebal


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=5, help="고르기 평일 수 (기본 5)")
    ap.add_argument("--k", type=float, nargs="+", default=list(K_GRID), help="k 격자 (기본 56장 격자)")
    ap.add_argument("--period", default=DEFAULT_PERIOD, help="순수요 기간 (기본 운영과 같다)")
    ap.add_argument("--run-label", help="대여소 정보 실행 (기본: 자료 끝 전 가장 최근 계획)")
    args = ap.parse_args(argv)

    import importlib
    step1 = importlib.import_module("pipeline.step1_cluster.top_st_clustering")
    t0 = time.time()
    with db.session() as conn:
        label = args.run_label or plan_label_before(conn)
        info = db.load_frame(conn, "station_info", run_label=label)
        today = pd.Timestamp.now().normalize()
        pick_days = pm.training_days(conn, today.date())[-args.days:]
        if not pick_days:
            print("고르기 평일이 없다")
            return 1
        train_days = pm.training_days(conn, pick_days[0])
        if len(train_days) < pm.MIN_TRAIN_DAYS:
            print(f"학습 평일 {len(train_days)}일(최소 {pm.MIN_TRAIN_DAYS}일)")
            return 1
        net = pm.load_net_weekday(args.period)
        refs = pm.reference_stats(net, info)
        print(f"대여소 정보 '{label}' · 순수요 {args.period} · 학습 평일 {len(train_days)}일({train_days[0]} ~ {train_days[-1]}) · "
              f"고르기 평일 {len(pick_days)}일({pick_days[0]} ~ {pick_days[-1]})")
        grid = pm.load_grid(conn, pd.Timestamp(train_days[0]) - pd.Timedelta(days=1),
                            pd.Timestamp(train_days[-1]) + pd.Timedelta(days=1, hours=10))
        train = pm.build_rows(grid, refs, set(train_days), only_usable=True)
        del grid
        train = pm.add_hybrid_train(train)
        tables = pm.quantile_tables(train)
        print(f"학습 행 {len(train):,} · 준비 {format_seconds(time.time() - t0)} · 최대 메모리 {peak_mb():,.0f}MB")
        bundles = {}
        for k in args.k:
            t1 = time.time()
            bundles[k] = {"model": pm.fit(train, k), "tables": tables}
            print(f"  k {k:g}: 학습 {format_seconds(time.time() - t1)} · 최대 메모리 {peak_mb():,.0f}MB")
        del train

        want, filled, rounds = 0.0, {k: 0.0 for k in args.k}, 0
        for day in pick_days:
            for duration in DURATIONS:
                start = pd.Timestamp(day) + pd.Timedelta(hours=duration_start_hour(duration))
                win = pm.load_grid(conn, start - pd.Timedelta(hours=4), start)
                if start not in win.index or win.loc[start].notna().sum() < 0.5 * win.shape[1]:
                    continue
                win = win.loc[:start]
                stock = win.loc[start].dropna()
                base, full, st_rebal = base_plan(net, info, stock, duration, step1)
                total = float(-base.loc[base["rebal_qty"] < 0, "rebal_qty"].sum())
                if total <= 0:
                    continue
                plan_stock = st_rebal.drop_duplicates("station_id").set_index("station_id")["stock"].astype(float)
                want += total
                rounds += 1
                for k, bundle in bundles.items():
                    safe = pm.predict_safe(bundle, win, refs, duration, start, plan_stock)
                    _out, left = pm.select_picks(base, full, safe)
                    filled[k] += total - left
    if not rounds:
        print("셀 회차가 없다")
        return 1
    ratio = {k: filled[k] / want for k in args.k}
    best = choose_k(ratio)
    print(f"\n고르기 회차 {rounds}개 · 지금 선정의 수거 총량 {want:,.0f}대")
    for k in args.k:
        mark = " ← 권함" if k == best else ""
        now = " (지금 설정)" if k == project_config.PICK_MODEL_QUANTILE else ""
        print(f"  k {k:<5g} 총량의 {ratio[k]:6.1%}{now}{mark}")
    print(f"\n권하는 k = {best:g} (총량 {K_FILL:.0%} 이상을 채우는 가장 큰 k). 지금 설정 {project_config.PICK_MODEL_QUANTILE:g}. "
          f"바꾸려면 PBR_PICK_MODEL_QUANTILE · 사용자 결정")
    print(f"전체 {format_seconds(time.time() - t0)} · 최대 메모리 {peak_mb():,.0f}MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
