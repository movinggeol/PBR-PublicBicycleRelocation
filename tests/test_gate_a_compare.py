"""게이트 A 끈/켠 비교 도구 검증 (`tools/gate_a_compare.py`).

09-19·20 재실행 뒤 4단계 재작성이 이 표를 보고 고칠 곳을 고른다. 여기서 틀리면
**바뀐 장을 '변화 없음'으로 넘기거나**, 실패한 작업의 반쪽 산출물을 믿게 된다.
"""
import pandas as pd

import tools.gate_a_compare as gc

LOG_OFF = """실행 '2026-08-11 real' · 예산 120분

시간대      구분           군집     최장분   초과     옮긴대수
_05_10   현행(무제한)      10   147.8    5      307
_05_10   예산 강제        10   118.0    0      297
결품 시간 (대여소·일 평균 h)
_05_10      2.259      2.269   +0.009  같음
"""
LOG_ON = """실행 '2026-08-11 real' · 예산 120분

시간대      구분           군집     최장분   초과     옮긴대수
_05_10   현행(무제한)      10   171.2    7      307
_05_10   예산 강제        10   118.0    0      281
결품 시간 (대여소·일 평균 h)
_05_10      2.259      2.301   +0.042  나쁨
새로 생긴 경고 줄
"""


def _folder(root, mode, summary_rows, files):
    folder = root / mode
    folder.mkdir()
    # PowerShell 5.1 Out-File -Encoding utf8은 BOM을 붙인다 — 그대로 흉내 낸다
    text = "name,chapter,exit,minutes\n" + "".join(f"{r}\n" for r in summary_rows)
    (folder / gc.SUMMARY).write_bytes("﻿".encode("utf-8") + text.encode("utf-8"))
    for rel, content in files.items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, pd.DataFrame):
            content.to_csv(path, index=False)
        else:
            path.write_text(content, encoding="utf-8")
    return folder


def test_로그는_숫자만_바뀐_줄을_짝짓는다():
    got = gc.compare_logs(LOG_OFF, LOG_ON)
    changed = dict(got["changed"])
    assert any("147.8" in a and "171.2" in b for a, b in got["changed"]), got
    assert "_05_10   예산 강제        10   118.0    0      297" in changed, "옮긴대수 297→281을 놓쳤다"
    assert "새로 생긴 경고 줄" in got["added"]
    # '같음'→'나쁨'은 숫자가 아니라 글자가 바뀐 줄이다 — 짝이 아니라 빠짐/더해짐으로 잡혀야 한다
    assert any("같음" in l for l in got["removed"]) and any("나쁨" in l for l in got["added"])


def test_같은_로그는_변화가_없다():
    got = gc.compare_logs(LOG_OFF, LOG_OFF)
    assert got == {"changed": [], "added": [], "removed": []}


def test_CSV는_글자_열로_맞추고_순서가_달라도_같은_행을_견준다():
    off = pd.DataFrame({"duration": ["_05_10", "_10_15"], "z": [1.99, 1.99], "stockout": [2.0, 1.5]})
    on = pd.DataFrame({"duration": ["_10_15", "_05_10"], "z": [1.99, 1.99], "stockout": [1.5, 2.2]})
    got = gc.compare_frames(off, on)
    assert got["how"].startswith("열쇠")
    cols = {c["column"]: c for c in got["columns"]}
    assert set(cols) == {"stockout"}, "행 순서만 다른 것을 변화로 셌다"
    assert cols["stockout"]["changed"] == 1
    assert abs(cols["stockout"]["max_rel"] - 0.1) < 1e-9


def test_행_수가_다르면_값을_견주지_않고_그_사실만_적는다():
    off = pd.DataFrame({"k": [1, 2], "v": [1.0, 2.0]})
    on = pd.DataFrame({"k": [1, 2, 3], "v": [1.0, 2.0, 3.0]})
    got = gc.compare_frames(off, on)
    assert got["columns"] == [] and "행 수" in got["how"]


def test_폴더를_맞대면_변화없음과_실패와_흔들림을_표에_표시한다(tmp_path):
    same = pd.DataFrame({"outage": [0, 1], "failed": [0, 0]})
    off = _folder(tmp_path, "off", ["budget_enforce_120,5-E,0,0.2", "fleet_outage_stress,25,0,1.0",
                                    "ortools_gap_seeds,24,0,3.0"],
                  {"budget_enforce_120.log": LOG_OFF, "fleet_outage_stress.log": "결원 0대 실패 0\n",
                   "ortools_gap_seeds/seed42.csv": pd.DataFrame({"n": ["a"], "gap": [0.10]}),
                   "budget_enforce_120.err": ""})
    on = _folder(tmp_path, "on", ["budget_enforce_120,5-E,0,0.3", "fleet_outage_stress,25,0,1.1",
                                  "ortools_gap_seeds,24,1,0.1"],
                 {"budget_enforce_120.log": LOG_ON, "fleet_outage_stress.log": "결원 0대 실패 0\n",
                  "ortools_gap_seeds/seed42.csv": pd.DataFrame({"n": ["a"], "gap": [0.12]})})

    names = gc.job_names(off, on)
    assert names == ["budget_enforce_120", "fleet_outage_stress", "ortools_gap_seeds"], \
        ".err나 요약 파일을 작업으로 셌거나 순서가 요약을 따르지 않는다"
    jobs = [gc.compare_job(off, on, n) for n in names]
    report = gc.render(off, on, jobs)

    row = {line.split("|")[1].strip(): line for line in report.splitlines()
           if line.startswith("| ") and not line.startswith("| 작업") and not line.startswith("| ---")}
    assert "변화 없음" in row["fleet_outage_stress"]
    assert "변화 없음" not in row["budget_enforce_120"]
    assert "시간 제한 탐색" in row["ortools_gap_seeds"]
    assert "🔴 켠 종료 1" in row["ortools_gap_seeds"], "실패한 작업을 표에 드러내지 않는다"
    assert "ortools_gap_seeds/seed42.csv" in report, "하위 폴더 CSV를 견주지 않았다"
    assert "147.8→171.2" in report


def test_Only로_다시_돌린_폴더를_주면_그_결과가_이긴다(tmp_path):
    """죽은 작업을 `-Only`로 다시 돌리면 새 시각 폴더에 남는다 — 한쪽이 두 폴더로 갈린다."""
    off = _folder(tmp_path, "off", ["a,1,0,1.0", "b,2,0,1.0"], {"a.log": "x 1\n", "b.log": "y 1\n"})
    on = _folder(tmp_path, "on", ["a,1,0,1.0", "b,2,1,0.1"], {"a.log": "x 1\n", "b.log": "Traceback\n"})
    again = _folder(tmp_path, "on_again", ["b,2,0,1.2"], {"b.log": "y 5\n"})

    assert gc.main([str(off), str(on)]) == 1, "첫 on 폴더만 주면 b는 실패로 남아야 한다"

    jobs = [gc.compare_job([off], [on, again], n) for n in gc.job_names([off], [on, again])]
    by = {j["name"]: j for j in jobs}
    assert by["b"]["on_dir"] == again and by["a"]["on_dir"] == on
    assert gc.failed_jobs(jobs) == []
    assert by["b"]["log"]["changed"] == [("y 1", "y 5")]
    assert gc.main([str(off), str(on), "--on-also", str(again)]) == 0


def test_실패한_작업이_있으면_종료_코드로_알린다(tmp_path):
    off = _folder(tmp_path, "off", ["a,1,0,1.0"], {"a.log": "x 1\n"})
    on = _folder(tmp_path, "on", ["a,1,1,0.1"], {"a.log": "x 2\n"})
    assert gc.main([str(off), str(on)]) == 1
    assert (on / gc.REPORT).is_file()
