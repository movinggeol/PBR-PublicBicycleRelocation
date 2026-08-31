"""실행 라벨 PC 간 이관 검증 (docs/구현/두_PC_작업.md).

`data/`가 `.gitignore`에 걸려 두 PC의 DB가 갈리는데, **`station_info.stock`만은
다시 만들 수 없다** — 실행 순간 라이브 API에서 받은 재고이기 때문이다. 그런데
DECISIONS.md 6-B가 실험의 정본 스냅샷을 특정 라벨로 못박았으므로, 그 라벨을
옮길 수단이 없으면 한쪽 PC는 실험을 하나도 재현할 수 없다.

지키려는 규칙:
  1. 내보낸 것을 받으면 **값이 그대로** 나온다 (특히 재고).
  2. 같은 라벨이 이미 있으면 **말없이 덮지 않는다** — 실험 결과가 붙어 있는
     실행을 덮으면 어느 쪽 숫자를 본 것인지 알 수 없게 된다.
  3. `road_leg`는 **따라가지 않는다** — 양쪽 PC가 각자 쌓는 관측치라,
     섞으면 '어느 PC에서 잰 것인가'가 사라진다.
  4. 안내문이 **실제로 쓴 DB**를 가리킨다 (`db.DB_PATH`는 import 시점에 굳는다).
"""
import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "tools" / "transfer_run.py"


def load_tool():
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    spec = importlib.util.spec_from_file_location("transfer_run", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tool():
    return load_tool()


def seed_run(label="2026-08-11 real", stock_base=7):
    """이관 대상이 될 실행 하나를 현재 DB에 심는다."""
    import db

    info = pd.DataFrame({
        "station_id": [f"ST{i:04d}" for i in range(1, 6)],
        "station_name": [f"대여소{i}" for i in range(1, 6)],
        "lat": [36.30 + i * 0.01 for i in range(5)],
        "lon": [127.30 + i * 0.01 for i in range(5)],
        "parking_lot": [10, 12, 8, 15, 20],
        # 이 값이 다시 만들 수 없는 것이다 — 그날의 라이브 재고.
        "stock": [stock_base + i for i in range(5)],
    })
    db.save_output("station_info", info, run_label=label, period="25년 11월",
                   duration="_05_10")
    return info


def test_export_import_preserves_live_stock(tool, tmp_path, monkeypatch):
    """다시 만들 수 없는 재고가 그대로 건너가야 한다 — 이 도구의 존재 이유다."""
    import db

    info = seed_run()
    out = tmp_path / "run.db"
    with db.session() as conn:
        counts = tool.export_run(conn, "2026-08-11 real", out)
    assert counts["station_info"] == 5

    # 받는 쪽은 빈 DB다.
    monkeypatch.setenv("PBR_DB_PATH", str(tmp_path / "other.db"))
    with db.session() as conn:
        tool.import_run(conn, out, overwrite=False, dry_run=False)
    with db.session() as conn:
        got = pd.read_sql(
            "SELECT station_id, stock, parking_lot FROM station_info"
            " WHERE run_label = ? ORDER BY station_id", conn,
            params=("2026-08-11 real",))

    assert got["stock"].tolist() == info["stock"].tolist()
    assert got["parking_lot"].tolist() == info["parking_lot"].tolist()


def test_import_refuses_existing_label(tool, tmp_path):
    """이미 있는 라벨을 말없이 덮으면 어느 숫자를 본 것인지 알 수 없게 된다."""
    import db

    seed_run()
    out = tmp_path / "run.db"
    with db.session() as conn:
        tool.export_run(conn, "2026-08-11 real", out)
        with pytest.raises(SystemExit, match="이미"):
            tool.import_run(conn, out, overwrite=False, dry_run=False)


def test_overwrite_replaces_instead_of_duplicating(tool, tmp_path):
    """--overwrite는 덮어쓰는 것이지 행을 쌓는 것이 아니다."""
    import db

    seed_run(stock_base=7)
    out = tmp_path / "run.db"
    with db.session() as conn:
        tool.export_run(conn, "2026-08-11 real", out)

    seed_run(stock_base=99)             # 같은 라벨을 다른 값으로 덮어 둔다
    with db.session() as conn:
        tool.import_run(conn, out, overwrite=True, dry_run=False)
        got = pd.read_sql("SELECT stock FROM station_info WHERE run_label = ?"
                          " ORDER BY station_id", conn, params=("2026-08-11 real",))
    assert len(got) == 5                       # 10이 되면 쌓인 것이다
    assert got["stock"].tolist() == [7, 8, 9, 10, 11]


def test_dry_run_writes_nothing(tool, tmp_path, monkeypatch):
    """--dry-run은 내용만 보여주고 넣지 않는다."""
    import db

    seed_run()
    out = tmp_path / "run.db"
    with db.session() as conn:
        tool.export_run(conn, "2026-08-11 real", out)

    monkeypatch.setenv("PBR_DB_PATH", str(tmp_path / "other.db"))
    with db.session() as conn:
        counts = tool.import_run(conn, out, overwrite=False, dry_run=True)
    assert counts["station_info"] == 5         # 셌지만
    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM station_info").fetchone()[0] == 0


def test_export_refuses_unknown_label(tool, tmp_path):
    """없는 라벨을 조용히 빈 파일로 만들면 받는 쪽에서야 알게 된다."""
    import db

    seed_run()
    with db.session() as conn:
        with pytest.raises(SystemExit, match="없습니다"):
            tool.export_run(conn, "없는 실행", tmp_path / "x.db")


def test_export_refuses_to_clobber_existing_file(tool, tmp_path):
    import db

    seed_run()
    out = tmp_path / "run.db"
    out.write_text("기존 파일", encoding="utf-8")
    with db.session() as conn:
        with pytest.raises(SystemExit, match="이미 있습니다"):
            tool.export_run(conn, "2026-08-11 real", out)


def test_road_leg_is_not_carried(tool):
    """TMAP 실측은 양쪽 PC가 각자 쌓는 관측치다 — 실행을 옮긴다고 따라가면 안 된다."""
    assert "road_leg" not in tool.RUN_TABLES
    assert "net_demand" not in tool.RUN_TABLES      # 기간 스코프 · 재생성 가능


def test_active_db_path_follows_env(tool, tmp_path, monkeypatch):
    """안내문이 기본 경로를 찍으면 어디에 넣었는지 거짓말을 한다."""
    import db

    monkeypatch.setenv("PBR_DB_PATH", str(tmp_path / "elsewhere.db"))
    assert tool.active_db_path() == tmp_path / "elsewhere.db"
    monkeypatch.delenv("PBR_DB_PATH")
    assert tool.active_db_path() == Path(db.DB_PATH)


def test_cli_roundtrip(tmp_path, monkeypatch):
    """실제 프로세스로도 도는지 — import 부작용·인자 전달은 실행해야 드러난다."""
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    out = tmp_path / "moved.db"
    env = {**dict(**__import__("os").environ), "PBR_DB_PATH": str(source)}

    setup = (
        "import sys; sys.path.insert(0, r'%s');"
        "import pandas as pd, db;"
        "db.save_output('station_info', pd.DataFrame({"
        "'station_id':['ST0001'],'station_name':['가'],'lat':[36.3],'lon':[127.3],"
        "'parking_lot':[10],'stock':[42]}), run_label='라벨', period='25년 11월',"
        " duration='_05_10')" % PROJECT_ROOT
    )
    subprocess.run([sys.executable, "-c", setup], env=env, check=True,
                   cwd=PROJECT_ROOT)
    subprocess.run([sys.executable, "tools/transfer_run.py", "--export", "라벨",
                    "--out", str(out)], env=env, check=True, cwd=PROJECT_ROOT)
    assert out.is_file()

    env["PBR_DB_PATH"] = str(target)
    subprocess.run([sys.executable, "tools/transfer_run.py", "--import", str(out)],
                   env=env, check=True, cwd=PROJECT_ROOT)

    with sqlite3.connect(target) as conn:
        stock = conn.execute(
            "SELECT stock FROM station_info WHERE run_label = '라벨'").fetchone()[0]
    assert stock == 42
