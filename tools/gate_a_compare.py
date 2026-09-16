"""게이트 A 재실행 — **끈 채(off)와 켠 채(on) 결과를 장별로 맞대어** 무엇이 움직였는지 뽑는다.

## 왜 필요한가

`scripts/gate_a_rerun.ps1`은 같은 정본 스냅샷·같은 코드로 작업 24개를 두 번 돌린다
(09-19 `-Mode off`, 09-20 `-Mode on`). 둘의 차이는 **이동시간 식 하나**뿐이므로,
두 폴더를 맞대면 *"스위치가 이 장의 무엇을 바꿨나"* 가 그대로 나온다. 4단계(EXPERIMENTS
17개 장·논문 6·8장 재작성)는 이 표를 보고 고칠 곳을 고른다 — 손으로 24쌍을 열어
숫자를 눈으로 대조하면 빠뜨린다.

## 무엇을 견주나

- **CSV** (`--out`이 있는 작업, `ortools_gap_seeds` 폴더 안의 CSV 포함) — 글자 열을 열쇠로
  행을 맞추고, 숫자 열마다 바뀐 행 수 · 평균(끈 → 켠) · 최대 |Δ| · 최대 상대 변화를 낸다.
  열쇠가 겹치면 행 순서로 맞추고, 행 수가 다르면 그 사실만 적는다.
- **로그** (CSV를 남기지 않는 5개 작업 — `budget_enforce` 둘 · `cluster_count_sweep` ·
  `wanted_vehicles_geo_sweep` · `fleet_outage_stress` · `budget_split`) — 숫자를 가린
  **줄 뼈대**가 같은 줄끼리 짝지어 숫자만 바뀐 곳을 `끈 → 켠`으로 보여 준다. 뼈대가 다른 줄은
  더해지거나 빠진 줄로 센다.

## 읽을 때 주의

- ⚠️ **변화 0인 작업도 정보다.** 이동시간을 거치지 않는 작업이면 같아야 한다(예: 25장
  `fleet_outage_stress`는 계산 경로에 이동시간 식이 보이지 않는다). 반대로 **거쳐야 할 작업이
  0이면** 스위치가 그 경로에 닿지 않는다는 뜻이라 먼저 의심한다.
- ⚠️ **시간 제한 탐색(OR-Tools)은 스위치와 무관하게 흔들린다.** `ortools_gap*`의 차이에는
  탐색 흔들림이 섞인다 — 표에 표시해 둔다. 씨앗 여럿(24장)으로 읽는다.
- 로그에는 걸린 시간 같은 줄도 있어 숫자가 바뀐다. 결론 수치인지는 사람이 가른다.

> 재현: `python tools/gate_a_compare.py data/gate_a_rerun/off_<시각> data/gate_a_rerun/on_<시각>`

## 쓰는 법

    python tools/gate_a_compare.py <off 폴더> <on 폴더>            # 표를 찍고 <on 폴더>/_비교.md에 저장
    python tools/gate_a_compare.py <off 폴더> <on 폴더> --on-also <-Only로 다시 돌린 on 폴더>
    python tools/gate_a_compare.py <off 폴더> <on 폴더> --only gamma_sweep
    python tools/gate_a_compare.py <off 폴더> <on 폴더> --max-lines 60  # 작업당 로그 줄 상한
"""
from __future__ import annotations

import argparse
import csv
import difflib
import io
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd                                     # noqa: E402

import project_config  # noqa: E402,F401 — 콘솔 인코딩(—·이모지)을 먼저 맞춘다

SUMMARY = "_요약.csv"
REPORT = "_비교.md"
# 시간 제한 탐색이라 같은 입력에서도 결과가 흔들리는 작업 (이름 접두)
NOISY_PREFIXES = ("ortools_gap",)
_NUM = re.compile(r"[-+]?\d[\d,]*\.?\d*%?")


# ── 요약 ──────────────────────────────────────────────────────────────────
def read_summary(folder: Path) -> dict:
    """`_요약.csv` → {작업: {chapter, exit, minutes}}. 없으면 빈 dict.

    PowerShell 5.1의 `Out-File -Encoding utf8`은 BOM을 붙이므로 `utf-8-sig`로 읽는다.
    """
    path = folder / SUMMARY
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8-sig")
    return {row["name"]: row for row in csv.DictReader(io.StringIO(text))}


# ── CSV ───────────────────────────────────────────────────────────────────
def _aligned(off: pd.DataFrame, on: pd.DataFrame):
    """두 표의 행을 맞춘다. 반환: (끈, 켠, 방법) 또는 (None, None, 까닭)."""
    keys = [c for c in off.columns
            if c in on.columns and not pd.api.types.is_numeric_dtype(off[c])]
    if keys and not off.duplicated(keys).any() and not on.duplicated(keys).any():
        merged = off.merge(on, on=keys, how="inner", suffixes=("\x00off", "\x00on"))
        if len(merged) == len(off) == len(on):
            a = merged[[c for c in merged.columns if c.endswith("\x00off")]]
            b = merged[[c for c in merged.columns if c.endswith("\x00on")]]
            a.columns = [c[:-4] for c in a.columns]
            b.columns = [c[:-3] for c in b.columns]
            return a, b, "열쇠 " + " · ".join(keys)
    if len(off) == len(on):
        return off.reset_index(drop=True), on.reset_index(drop=True), "행 순서"
    return None, None, f"행 수가 다르다 ({len(off)} → {len(on)})"


def compare_frames(off: pd.DataFrame, on: pd.DataFrame) -> dict:
    """숫자 열마다 무엇이 바뀌었나. 반환: {"how", "columns": [...], "note"}."""
    a, b, how = _aligned(off, on)
    result = {"how": how, "columns": [], "note": None}
    added = sorted(set(on.columns) - set(off.columns))
    removed = sorted(set(off.columns) - set(on.columns))
    if added or removed:
        result["note"] = f"열이 다르다 — 더해짐 {added} · 빠짐 {removed}"
    if a is None:
        return result
    for col in a.columns:
        if col not in b.columns:
            continue
        if not (pd.api.types.is_numeric_dtype(a[col]) and pd.api.types.is_numeric_dtype(b[col])):
            continue
        x = a[col].astype(float)
        y = b[col].astype(float)
        both_nan = x.isna() & y.isna()
        changed = ~both_nan & ((x - y).abs().fillna(float("inf")) > 1e-9)
        if not changed.any():
            continue
        delta = (y - x)[changed]
        base = x[changed].abs().where(lambda s: s > 0)
        rel = (delta.abs() / base).dropna()
        result["columns"].append({
            "column": col,
            "changed": int(changed.sum()),
            "rows": int(len(x)),
            "off_mean": float(x.mean()) if x.notna().any() else float("nan"),
            "on_mean": float(y.mean()) if y.notna().any() else float("nan"),
            "max_abs": float(delta.abs().max()) if delta.notna().any() else float("nan"),
            "max_rel": float(rel.max()) if len(rel) else float("nan"),
        })
    return result


# ── 로그 ──────────────────────────────────────────────────────────────────
def _skeleton(line: str) -> str:
    return _NUM.sub("#", line.rstrip())


def compare_logs(off_text: str, on_text: str) -> dict:
    """숫자만 바뀐 줄을 짝짓는다. 반환: {"changed": [(끈, 켠)], "added": [...], "removed": [...]}."""
    off_lines = [l for l in off_text.splitlines() if l.strip()]
    on_lines = [l for l in on_text.splitlines() if l.strip()]
    matcher = difflib.SequenceMatcher(
        a=[_skeleton(l) for l in off_lines], b=[_skeleton(l) for l in on_lines], autojunk=False)
    out = {"changed": [], "added": [], "removed": []}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                if off_lines[i].rstrip() != on_lines[j].rstrip():
                    out["changed"].append((off_lines[i].rstrip(), on_lines[j].rstrip()))
        else:
            out["removed"].extend(l.rstrip() for l in off_lines[i1:i2])
            out["added"].extend(l.rstrip() for l in on_lines[j1:j2])
    return out


def _numbers_diff(off_line: str, on_line: str) -> str:
    """같은 뼈대의 두 줄에서 바뀐 숫자만 `끈→켠`으로 모은다."""
    pairs = [f"{a}→{b}" for a, b in zip(_NUM.findall(off_line), _NUM.findall(on_line)) if a != b]
    return ", ".join(pairs)


# ── 폴더 ──────────────────────────────────────────────────────────────────
#
# 한쪽 결과가 **폴더 여럿으로 갈릴 수 있다.** 재실행 도중 죽은 작업을 `-Only`로 다시 돌리면
# `gate_a_rerun.ps1`이 새 시각 폴더를 만든다(이전 결과를 덮지 않으려는 설계). 그래서 한쪽을
# 폴더 목록으로 받고, 작업마다 **뒤에 준 폴더를 먼저** 본다 — 다시 돌린 결과가 이긴다.
def _folders(side) -> list:
    return [Path(p) for p in side] if isinstance(side, (list, tuple)) else [Path(side)]


def _stems(folder: Path) -> list:
    """폴더 안의 작업 이름(산출물 파일·하위 폴더에서). 요약·.err·보고서는 빼고."""
    names = []
    for path in sorted(folder.iterdir()):
        if path.name.startswith("_") or path.suffix == ".err":
            continue
        stem = path.stem if path.is_file() else path.name
        if stem not in names:
            names.append(stem)
    return names


def pick(side, name: str) -> tuple:
    """작업 `name`을 읽을 폴더와 그 요약 행. 뒤 폴더부터 보고, 없으면 첫 폴더와 빈 행."""
    folders = _folders(side)
    for folder in reversed(folders):
        row = read_summary(folder).get(name)
        if row is not None or name in _stems(folder):
            return folder, row or {}
    return folders[0], {}


def job_names(off, on) -> list:
    """양쪽에 나온 작업 이름. 요약 순서를 따르고, 요약에 없는 산출물 이름을 뒤에 붙인다."""
    names = []
    folders = _folders(off) + _folders(on)
    for folder in folders:
        names += [n for n in read_summary(folder) if n not in names]
    for folder in folders:
        names += [n for n in _stems(folder) if n not in names]
    return names


def compare_job(off, on, name: str) -> dict:
    (off_dir, off_row), (on_dir, on_row) = pick(off, name), pick(on, name)
    job = {"name": name, "csv": [], "log": None, "missing": [],
           "off_dir": off_dir, "on_dir": on_dir, "off_row": off_row, "on_row": on_row}
    csvs = []
    for side in (off_dir, on_dir):
        if (side / f"{name}.csv").is_file():
            csvs.append(Path(f"{name}.csv"))
        if (side / name).is_dir():
            csvs.extend(p.relative_to(side) for p in sorted((side / name).rglob("*.csv")))
    for rel in dict.fromkeys(csvs):
        a, b = off_dir / rel, on_dir / rel
        if not (a.is_file() and b.is_file()):
            job["missing"].append(f"{rel} ({'끈' if not a.is_file() else '켠'} 쪽 없음)")
            continue
        job["csv"].append({"file": str(rel).replace("\\", "/"),
                           **compare_frames(pd.read_csv(a), pd.read_csv(b))})
    la, lb = off_dir / f"{name}.log", on_dir / f"{name}.log"
    if la.is_file() and lb.is_file():
        job["log"] = compare_logs(la.read_text(encoding="utf-8", errors="replace"),
                                  lb.read_text(encoding="utf-8", errors="replace"))
    elif la.is_file() or lb.is_file():
        job["missing"].append(f"{name}.log ({'끈' if not la.is_file() else '켠'} 쪽 없음)")
    return job


def changed_count(job: dict) -> int:
    n = sum(c["changed"] for f in job["csv"] for c in f["columns"])
    if job["log"]:
        n += len(job["log"]["changed"]) + len(job["log"]["added"]) + len(job["log"]["removed"])
    return n


def failed_jobs(jobs: list) -> list:
    """어느 쪽이든 종료 코드가 0이 아닌 작업. 요약에 없는 작업은 모른다고 보고 넘긴다."""
    return [j["name"] for j in jobs
            if any(str(row.get("exit", "0")) not in ("0", "") for row in (j["off_row"], j["on_row"]))]


# ── 보고 ──────────────────────────────────────────────────────────────────
def _fmt(v: float) -> str:
    if v != v:          # NaN
        return "—"
    return f"{v:,.4g}"


def render(off, on, jobs: list, max_lines: int = 30) -> str:
    lines = ["# 게이트 A 끈/켠 비교", ""]
    lines += [f"- 끈 채: `{p}`" for p in _folders(off)]
    lines += [f"- 켠 채: `{p}`" for p in _folders(on)]
    lines += ["", "| 작업 | 장 | 끈 종료·분 | 켠 종료·분 | 바뀐 값·줄 | 최대 상대 변화 | 비고 |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    multi = len(_folders(off)) > 1 or len(_folders(on)) > 1
    for job in jobs:
        name, ro, rn = job["name"], job["off_row"], job["on_row"]
        rels = [c["max_rel"] for f in job["csv"] for c in f["columns"] if c["max_rel"] == c["max_rel"]]
        notes = []
        if name.startswith(NOISY_PREFIXES):
            notes.append("시간 제한 탐색 — 흔들림 섞임")
        if changed_count(job) == 0 and (job["csv"] or job["log"]):
            notes.append("**변화 없음**")
        for side, row in (("끈", ro), ("켠", rn)):
            if row and str(row.get("exit")) not in ("0", ""):
                notes.append(f"🔴 {side} 종료 {row.get('exit')}")
        if job["missing"]:
            notes.append("산출물 한쪽 없음")
        elif not job["csv"] and not job["log"]:
            notes.append("⚠️ 산출물 없음 — 견줄 것이 없다")
        if multi:
            notes.append(f"`{job['off_dir'].name}` ↔ `{job['on_dir'].name}`")
        lines.append(
            f"| {name} | {ro.get('chapter') or rn.get('chapter') or ''} "
            f"| {ro.get('exit', '—')}·{ro.get('minutes', '—')} | {rn.get('exit', '—')}·{rn.get('minutes', '—')} "
            f"| {changed_count(job)} | {(f'{max(rels):.1%}' if rels else '—')} | {' · '.join(notes)} |")

    for job in jobs:
        lines += ["", f"## {job['name']}", ""]
        for item in job["missing"]:
            lines.append(f"- ⚠️ {item}")
        for f in job["csv"]:
            lines.append(f"**{f['file']}** — 맞춤: {f['how']}" + (f" · ⚠️ {f['note']}" if f["note"] else ""))
            if not f["columns"]:
                lines += ["", "숫자 열 변화 없음", ""]
                continue
            lines += ["", "| 열 | 바뀐 행 | 평균 끈 → 켠 | 최대 \\|Δ\\| | 최대 상대 |", "| --- | --- | --- | --- | --- |"]
            for c in f["columns"]:
                rel = f"{c['max_rel']:.1%}" if c["max_rel"] == c["max_rel"] else "—"
                lines.append(f"| {c['column']} | {c['changed']}/{c['rows']} | {_fmt(c['off_mean'])} → "
                             f"{_fmt(c['on_mean'])} | {_fmt(c['max_abs'])} | {rel} |")
            lines.append("")
        log = job["log"]
        if log:
            total = len(log["changed"]) + len(log["added"]) + len(log["removed"])
            lines.append(f"**로그** — 숫자만 바뀐 줄 {len(log['changed'])} · 더해진 줄 {len(log['added'])}"
                         f" · 빠진 줄 {len(log['removed'])}")
            if total:
                shown = 0
                lines += ["", "```text"]
                for a, b in log["changed"]:
                    if shown >= max_lines:
                        break
                    lines += [f"  {b}", f"    ↳ {_numbers_diff(a, b)}"]
                    shown += 1
                for l in log["removed"][:max(0, max_lines - shown)]:
                    lines.append(f"- {l}")
                    shown += 1
                for l in log["added"][:max(0, max_lines - shown)]:
                    lines.append(f"+ {l}")
                    shown += 1
                if total > shown:
                    lines.append(f"  … {total - shown}줄 더 (--max-lines로 늘린다)")
                lines.append("```")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="게이트 A 끈/켠 재실행 결과를 장별로 맞댄다.")
    parser.add_argument("off", type=Path, help="gate_a_rerun.ps1 -Mode off 결과 폴더")
    parser.add_argument("on", type=Path, help="gate_a_rerun.ps1 -Mode on 결과 폴더")
    parser.add_argument("--off-also", type=Path, action="append", default=[],
                        help="-Only로 다시 돌린 off 폴더 (여러 번 줄 수 있다 — 뒤에 준 것이 이긴다)")
    parser.add_argument("--on-also", type=Path, action="append", default=[],
                        help="-Only로 다시 돌린 on 폴더 (여러 번 줄 수 있다 — 뒤에 준 것이 이긴다)")
    parser.add_argument("--only", help="이 작업만 (쉼표로 여럿)")
    parser.add_argument("--max-lines", type=int, default=30, help="작업당 로그 줄 상한 (기본 30)")
    parser.add_argument("--out", type=Path, help=f"보고서 경로 (기본 <on 폴더>/{REPORT})")
    args = parser.parse_args(argv)

    off = [args.off, *args.off_also]
    on = [args.on, *args.on_also]
    for label, folder in [("off", p) for p in off] + [("on", p) for p in on]:
        if not folder.is_dir():
            print(f"[오류] {label} 폴더가 없습니다: {folder}")
            return 2
    names = job_names(off, on)
    if args.only:
        wanted = [n.strip() for n in args.only.split(",") if n.strip()]
        unknown = [n for n in wanted if n not in names]
        if unknown:
            print(f"[오류] 없는 작업: {unknown} — 있는 것: {names}")
            return 2
        names = wanted
    jobs = [compare_job(off, on, n) for n in names]
    report = render(off, on, jobs, args.max_lines)
    out = args.out or (args.on / REPORT)
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"[저장] {out}")
    failed = failed_jobs(jobs)
    if failed:
        print(f"🔴 종료 코드가 0이 아닌 작업: {', '.join(failed)} — 그 작업의 비교는 믿지 마십시오"
              " (-Only로 다시 돌렸다면 --on-also/--off-also로 그 폴더를 주십시오).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
