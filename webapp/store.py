"""산출물 조회 계층 — **DB가 정본이다** (DB_PLAN 3단계 완료, 1.26.165).

웹 API는 한때 `catalog.latest_file()`로 **파일 수정시각이 가장 최근인 것**을
최신으로 삼았다. 파일을 복사하거나 다시 저장하면 순서가 뒤바뀌는 휴리스틱이었다.
이제는 DB의 `run_label`을 기준으로 조회하고, 특정 실행분도 지정할 수 있다.

**CSV 폴백은 걷어냈다.** 이중 기록 이전 산출물을 위한 전환기 장치였는데, 남겨
두는 편이 더 위험해졌다:

  - 폴백은 **`run_label`도 `duration`도 없을 때만** 돌았다. 즉 *"최신 계획을
    보여 달라"* 는 물음에만 답했는데, 고르는 방법이 **파일 수정시각**이라
    실험 산출물(`obs-cmp-…`)을 집을 수 있었다. DB 경로는 그것을 막으려고
    `kinds=("plan",)`를 쓴다(1.26.125) — **폴백에는 그 장치가 없다.** 실측:
    `metrics`·`route_summary`·`ilp_plan` 세 표에서 폴백이 고르는 파일이
    실제로 `obs-cmp-1520`이었다.
  - DB가 이미 다섯 표 모두를 답한다(실측 87·66·106·87·14행).

**옛 산출물을 못 읽게 되는 것이 아니다** — 파일은 그대로 있고 `/files`가
`catalog.py`로 내려받게 해 준다. 달라지는 것은 *"계획을 물었을 때 API가
파일 수정시각으로 고른 것을 정답이라 내놓지 않는다"* 는 점이다.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

import db
from project_config import DURATIONS

# CSV_FALLBACK은 1.26.165에서 지웠다 — 위 설명 참고. 파일 목록·내려받기는
# `catalog.py`가 계속 담당한다(그쪽은 "무슨 파일이 있나"를 묻는 자리라 성격이
# 다르다). 되살릴 일이 있다면 **`kinds=("plan",)`에 해당하는 장치부터** 만들어라.


def load(table: str, run_label: Optional[str] = None,
         duration: Optional[str] = None) -> Tuple[pd.DataFrame, str]:
    """산출물을 읽는다.

    반환: (DataFrame, 출처). 출처는 "db" | "none" (CSV 폴백은 1.26.165에 제거).
    run_label을 생략하면 DB의 최신 실행분을 쓴다.
    """
    try:
        with db.session() as conn:
            # 라벨을 안 주면 **계획** 중에서 최신을 고른다. 실험도 같은 테이블에
            # 쌓이는데(`obs-cmp-…`), 운영 화면이 그것을 계획으로 내놓으면 안 된다 —
            # 라벨 없이 연 `/orders`가 실험을 **현장 지시서**로 내고 있었다(1.26.125).
            # 계획이 하나도 없으면 예전처럼 최신을 준다(비우지 않는다).
            frame = db.load_frame(conn, table, run_label=run_label,
                                  duration=duration, kinds=("plan",))
        if not frame.empty:
            return frame, "db"
    except Exception as err:      # DB가 없거나 손상돼도 화면이 죽지 않게 빈 결과로 떨어진다
        print(f"[경고] DB 조회 실패 ({table}): {type(err).__name__}: {err}")

    # DB에 없으면 없다고 답한다 — 파일 수정시각으로 고른 것을 계획이라 내놓지
    # 않는다(1.26.165). 옛 산출물은 `/files`에서 그대로 내려받을 수 있다.
    return pd.DataFrame(), "none"


def duration_rank(duration) -> int:
    """회차의 하루 안 순서 — `project_config.DURATIONS`의 순번. 모르는 회차는 맨 뒤.

    문자열 정렬에 기대지 않는다. 지금 네 창은 사전순과 하루 순서가 우연히 같지만
    (`_05_10 < _10_15 < _15_20 < _20_05`), 창을 바꾸면 조용히 갈린다 — 순서는
    설정에서 읽는다(점검 기록 4장 "설정은 코드에서 읽어 온다").
    """
    try:
        return DURATIONS.index(duration)
    except ValueError:
        return len(DURATIONS)


def _run_created_at(conn=None) -> dict:
    """실행마다 `runs.created_at` — `{run_label: 시각}`. 읽지 못하면 빈 dict."""
    try:
        if conn is not None:
            pairs = conn.execute("SELECT run_label, created_at FROM runs").fetchall()
        else:
            with db.session() as own:
                pairs = own.execute("SELECT run_label, created_at FROM runs").fetchall()
    except Exception as err:
        print(f"[경고] 실행 시각 조회 실패: {type(err).__name__}: {err}")
        return {}
    return {label: at for label, at in pairs}


def kpi_run_order(rows: pd.DataFrame, created: Optional[dict] = None) -> list:
    """지표 행의 실행 라벨을 **최신순**으로 — 웹 전체와 같은 `runs.created_at` 기준.

    `run_label`은 사람이 `--now`에 적는 이름이라 정렬 기준이 못 된다(1.26.146).
    이 한 함수를 `/kpi` 표·`/api/kpi`(`kpi()`), 헤드라인(`app._runs_newest_first`),
    추세 그래프(`kpi_view._runs_in_order`)가 함께 쓴다 — 예전에는 헤드라인만 시각순이고
    표는 사전 역순이라 한 화면에 '최신'이 둘이었다(1.26.283).

    🔴 **기준은 `kpi_summary.computed_at`이 아니라 `runs.created_at`이다** (1.26.283 검토).
    실행 칩(`plan_runs`)·지시서 기본값(`plan_targets`)·'최근 작업'·`db.latest_label()`이
    모두 `runs.created_at`을 쓴다. 같은 라벨을 다시 돌리면 둘이 갈린다 — `ensure_run`은
    `INSERT OR IGNORE`라 `created_at`이 처음 시각으로 남고, `save_kpi`는 `computed_at`을
    지금으로 덮는다. 그러면 `/kpi` 한 화면에서 표 첫 행과 첫 칩이 다른 실행을 가리켰다
    (웹 폼의 기본 라벨이 'YYYY-MM-DD HH'라 한 시간 안에 두 번 돌리면 실제로 생긴다).

    순서: `runs.created_at` 내림차순 → 실행별 `computed_at` 최댓값 내림차순 → 라벨 사전
    역순. `runs`에 없는 옛 라벨은 `db.latest_label()`처럼 가장 오래된 것으로 치고, 그들
    끼리는 `computed_at`으로 가린다(1.26.146의 순서가 그대로 남는다). `created`는
    `{run_label: created_at}` — 주지 않으면 여기서 읽는다.
    """
    if rows.empty or "run_label" not in rows:
        return []
    if created is None:
        created = _run_created_at()
    order = pd.DataFrame({"run_label": rows["run_label"].drop_duplicates().to_numpy()})
    order["created"] = order["run_label"].map(created)
    if "computed_at" in rows:
        order["computed"] = order["run_label"].map(
            rows.groupby("run_label")["computed_at"].max())
    else:
        order["computed"] = None
    # 안정 정렬을 뒤 키부터 — 앞 키가 같을 때만 뒤 키가 남는다.
    order = order.sort_values("run_label", ascending=False, kind="stable")
    order = order.sort_values("computed", ascending=False, kind="stable", na_position="last")
    order = order.sort_values("created", ascending=False, kind="stable", na_position="last")
    return order["run_label"].tolist()


def _kpi_newest_first(rows: pd.DataFrame, created: Optional[dict] = None) -> pd.DataFrame:
    """지표 행을 **최근 실행이 위, 한 실행 안에서는 하루 순서대로** 세운다(1.26.283)."""
    if rows.empty or "run_label" not in rows:
        return rows
    order = {label: i for i, label in enumerate(kpi_run_order(rows, created))}
    keys = pd.DataFrame({
        "run": rows["run_label"].map(order),
        "dur": rows["duration"].map(duration_rank) if "duration" in rows else 0,
    }, index=rows.index)
    index = keys.sort_values(["run", "dur"], kind="stable").index
    return rows.loc[index].reset_index(drop=True)


def kpi(run_label: Optional[str] = None, duration: Optional[str] = None) -> pd.DataFrame:
    """실행별 성과 지표 (docs/분석/KPI.md의 kpi_summary) — **최근 실행이 위**.

    `db.load_kpi`는 `run_label` 사전 역순이라 `/kpi` 표가 `sweep-21`부터 시작하고
    최신 계획의 첫 행이 72행 중 40번째였다(390px에서 약 9,300px 아래, 1.26.283
    실측). 순서를 여기서 세워 `/kpi`와 `/api/kpi`가 같은 순서를 받게 한다.
    """
    try:
        with db.session() as conn:
            rows = db.load_kpi(conn, run_label=run_label, duration=duration)
            created = _run_created_at(conn)
    except Exception as err:
        print(f"[경고] KPI 조회 실패: {type(err).__name__}: {err}")
        return pd.DataFrame()
    return _kpi_newest_first(rows, created)


def stockout_calibration(day_type: str = "weekday") -> list:
    """관측으로 잰 결품 보정 계수. 표가 없으면 빈 목록 (수정안 37).

    결품 시간은 순수요로 **복원**한 값이라 재고 0에서 잘려 실제보다 낮게 나온다.
    그 격차를 관측과 맞대어 재 둔 것이 이 계수다. **수집이 멈춰도 표에 남는다.**
    """
    try:
        with db.session() as conn:
            frame = db.latest_stockout_calibration(conn)
    except Exception as err:
        # 표가 아직 없는 옛 DB, 또는 DB가 잠기거나 손상된 경우. 화면은 떠야 한다.
        # 예전에는 `db.session()`이 try 밖이라 연결 실패가 /kpi를 500으로 만들었다(1.26.273).
        print(f"[경고] 결품 보정 조회 실패: {type(err).__name__}: {err}")
        return []
    return [] if frame.empty else frame.to_dict("records")


def vehicle_workload() -> pd.DataFrame:
    """차량별 누적 작업량(로테이션 형평성 확인용).

    `last_run`·`last_duration`은 **기록에 실제로 있는 한 배정**이다(1.26.283) —
    `db.vehicle_workload`는 두 칸을 각각 사전순 `MAX`로 뽑아 붙여, 21대가 전부
    `sweep-21 _20_05`였는데 `sweep-21`에는 `_20_05` 회차가 없었다(실측). 기록에
    없는 조합을 '최근'이라 적은 것이다. 여기서 `_latest_assignments()`로 덮는다.
    `last_kind`는 그 배정이 속한 실행의 종류다(화면이 실험이면 밝힌다).
    `/vehicles`와 `/api/vehicles`가 둘 다 이 함수를 쓰므로 한 곳에서 맞는다.
    """
    try:
        with db.session() as conn:
            db.ensure_fleet(conn)
            workload = db.vehicle_workload(conn)
            latest = _latest_assignments(conn)
    except Exception as err:
        print(f"[경고] 차량 부하 조회 실패: {type(err).__name__}: {err}")
        return pd.DataFrame()
    if workload.empty:
        return workload
    base = workload.drop(columns=["last_run", "last_duration"], errors="ignore")
    return base.merge(latest, on="vehicle_id", how="left")


def _latest_assignments(conn) -> pd.DataFrame:
    """차량마다 **가장 최근 배정 한 행**의 (실행, 회차, 종류).

    최근의 기준은 `db.latest_label()`과 같다 — `runs.created_at` 내림차순이고,
    `runs`에 없는 옛 라벨은 시각을 모르니 가장 오래된 것으로 친다(`COALESCE(…,'')`).
    동률은 라벨 사전 역순, 한 실행 안에서는 하루의 늦은 회차가 최근이다
    (`project_config.DURATIONS` 순번 — 문자열 정렬에 기대지 않는다).

    종류로 거르지 않는다. 표 전체가 '전체 실행 합계'(실험 포함)라 이 칸만 계획으로
    좁히면 한 표 안에서 기준이 갈린다 — 대신 `last_kind`로 실험임을 밝힌다.
    """
    cases = " ".join(f"WHEN ? THEN {i}" for i in range(len(DURATIONS)))
    frame = pd.read_sql(
        "SELECT vehicle_id, run_label AS last_run, duration AS last_duration,"
        "       kind AS last_kind"
        "  FROM (SELECT a.vehicle_id, a.run_label, a.duration, r.kind,"
        "               ROW_NUMBER() OVER ("
        "                 PARTITION BY a.vehicle_id"
        "                 ORDER BY COALESCE(r.created_at, '') DESC, a.run_label DESC,"
        f"                         CASE a.duration {cases} ELSE -1 END DESC) AS rn"
        "          FROM vehicle_assignment a"
        "          LEFT JOIN runs r ON r.run_label = a.run_label)"
        " WHERE rn = 1",
        conn, params=list(DURATIONS))
    if frame.empty:
        return pd.DataFrame(columns=["vehicle_id", "last_run", "last_duration", "last_kind"])
    # 저장된 종류가 없으면 짐작한다 — 규칙은 `db.classify_run_label()` 하나다.
    guessed = frame["last_run"].map(db.classify_run_label)
    return frame.assign(last_kind=frame["last_kind"].where(frame["last_kind"].notna(), guessed))


def vehicle_assignments(vehicle_id: Optional[str] = None,
                        run_label: Optional[str] = None,
                        limit: Optional[int] = None,
                        offset: int = 0) -> pd.DataFrame:
    """회차별 차량 배정 이력. `limit`을 주면 그 쪽만 읽는다."""
    try:
        with db.session() as conn:
            return db.assignment_history(conn, vehicle_id=vehicle_id,
                                         run_label=run_label, limit=limit, offset=offset)
    except Exception as err:
        print(f"[경고] 배정 이력 조회 실패: {type(err).__name__}: {err}")
        return pd.DataFrame()


def vehicle_assignment_count(vehicle_id: Optional[str] = None,
                             run_label: Optional[str] = None) -> int:
    """배정 이력 전체 건수(쪽 수 계산용). 실패하면 0 — 화면은 떠야 한다."""
    try:
        with db.session() as conn:
            return db.count_assignments(conn, vehicle_id=vehicle_id, run_label=run_label)
    except Exception as err:
        print(f"[경고] 배정 이력 건수 조회 실패: {type(err).__name__}: {err}")
        return 0


def run_labels() -> pd.DataFrame:
    """DB에 기록된 실행 이력 — **최신순**(`runs.created_at` 내림차순). DB가 없으면 빈 DataFrame.

    `db.list_runs()`는 `run_label` 사전 역순이다(그쪽 docstring이 *"최신순이
    아니다"* 라고 밝힌다). 이 docstring은 1.26.283까지 *"(최신순)"* 이라 적고 그
    순서를 그대로 넘겨, `/run` '저장된 실행'의 앞 10행이 전부 `sweep-*`·
    `roadprobe-*`·`brokenmix4-*`이고 계획은 0건이었다(실측). 여기서 다시 세운다.

    **안정 정렬**이라 같은 시각이면 `list_runs()`의 순서(라벨 사전 역순)가 남는다.
    종류로 묶지 않는다 — `/run` '저장된 실행'은 **종류를 바로잡는 표**라, 계획을
    먼저 묶으면 실험으로 잘못 매겨진 계획이 아래로 가라앉는다. 계획 먼저는
    `plan_runs()`(`/kpi`·`/vehicles` 칩)에서만 한다. `db.py`는 건드리지 않는다.
    """
    try:
        with db.session() as conn:
            frame = db.list_runs(conn)
    except Exception as err:
        print(f"[경고] 실행 이력 조회 실패: {type(err).__name__}: {err}")
        return pd.DataFrame()
    if frame.empty or "created_at" not in frame:
        return frame
    return frame.sort_values("created_at", ascending=False, kind="stable",
                             na_position="last").reset_index(drop=True)


# 실행 종류(plan/experiment/probe) 판정·확정. `webapp/`에서 `db.py`를
# 직접 import하는 곳은 여기 하나여야 한다 — 예전에는 `app.py`가 함수 안에서
# `import db`를 두 번(폴백 판정·POST 라우트) 따로 했는데, 그러면 "새 데이터
# API는 store.load()를 써라"는 이 계층의 존재 이유가 갈린다(1.26.110).
RUN_KINDS = db.RUN_KINDS
classify_run_label = db.classify_run_label


def run_kind(run_label: str, runs: Optional[pd.DataFrame] = None) -> str:
    """실행 종류. `runs`에 행이 없어도 라벨로 짐작해 돌려준다.

    `runs`를 미리 읽어 뒀으면(예: 이미 `run_labels()`를 부른 호출 쪽) 다시
    쿼리하지 않고 넘겨받는다 — 홈 화면 하나가 뜰 때마다 같은 표를 두 번
    읽을 이유가 없다.
    """
    if runs is None:
        runs = run_labels()
    if not runs.empty and "kind" in runs:
        row = runs.loc[runs["run_label"] == run_label, "kind"]
        if len(row) and pd.notna(row.iloc[0]):
            return str(row.iloc[0])
    return classify_run_label(run_label)


def set_run_kind(run_label: str, kind: str) -> None:
    """실행 종류를 사람이 못박는다. 실행 행이 없으면 만들어 두고 붙인다.

    `kind`가 `RUN_KINDS`에 없으면 `db.set_run_kind`가 `ValueError`를 낸다 —
    여기서 다시 검사하지 않는다(부르는 쪽 라우트가 HTTP 400으로 먼저 거른다).
    """
    with db.session() as conn:
        db.ensure_run(conn, run_label)
        db.set_run_kind(conn, run_label, kind)


def plan_runs() -> pd.DataFrame:
    """계획 화면의 필터가 쓸 실행 목록 — **계획이 아닌 실행을 뺀다.**

    도로 시간 수집기(`roadprobe-*`)도 `runs`에 행을 남긴다. 그것까지 필터
    칩으로 내면 계획인 척 섞여 있다가, 눌러 보면 지표도 배정도 없는 빈 표만
    나온다(1.26.107에서 실제로 그랬다). 종류는 db.list_runs()가 붙여 준다.

    실험(`experiment`)은 **남긴다** — 계획 모양이고 실제로 견줘 볼 값이
    들어 있다. 빼야 하는 것은 애초에 계획이 아닌 것뿐이다.

    **계획을 앞에, 그 안에서는 최신순**이다(1.26.283). 예전에는 `list_runs()`의
    사전 역순 그대로라 칩 앞 12개가 실험이고 최신 계획은 14번째였다(실측).
    `plan_targets()`와 같은 규약(1.26.125)이다 — 실험을 지우지 않고 뒤로 보낸다.
    """
    runs = run_labels()
    if runs.empty or "kind" not in runs:
        return runs
    runs = runs[runs["kind"] != "probe"]
    # 안정 정렬이라 계획끼리·실험끼리는 `run_labels()`의 최신순이 그대로 남는다.
    order = (runs["kind"] != "plan").astype(int).sort_values(kind="stable").index
    return runs.loc[order].reset_index(drop=True)


def plan_targets() -> pd.DataFrame:
    """작업지시서를 만들 수 있는 (실행, 회차) 목록 — `vrp_plan`에 경로가 있는 것.

    최근 실행이 앞에 오게 `runs.created_at`으로 정렬한다. `run_label`은 사람이
    붙이는 이름이라 사전순으로 줄 세우면 시간 순서와 어긋난다.

    **계획을 실험보다 앞에 둔다** (1.26.125). 화면은 `targets[0]`을 기본값으로
    쓰는데, 실험이 계획보다 나중에 돌면 그것이 **현장 지시서**가 됐다 — 실측에서
    `obs-cmp-1520`(실험)이 기본값이었다. 목록에서 **지우지는 않는다**: 실험 회차의
    지시서를 열어 보는 것은 정당한 용도이고, 지우면 그 길이 막힌다.

    🔴 **`runs`는 실행(`run_label`)으로만 붙인다** (1.26.283). `runs`의 기본 키는
    `run_label`이고 `duration` 칸에는 **첫 회차 하나**만 남는다(`ensure_run`의
    COALESCE). 1.19.2부터 `AND r.duration = v.duration`으로 붙여 와서, 첫 회차가
    아닌 행은 `created_at`·`kind`가 비었다 — 실측 72행 중 46행. 그 결과
    `brokenmix4-2603`(실험으로 못박힘, 라벨 짐작은 '계획')의 뒤 세 회차가 계획으로
    짐작돼 목록 8~22번에 끼고, 최신 계획의 회차는 0번과 23~25번으로 갈렸다.
    회차 없이 `/orders?run_label=brokenmix4-2603`로 들어오면 `_05_10`이 아니라
    `_10_15`가 골라졌다(1.26.262 폴백의 "목록 순서 = 시간대 순" 전제가 깨짐).
    실행으로만 붙이면 한 실행의 모든 회차가 같은 시각·종류를 받아 한데 붙어 나온다.

    **한 실행 안의 회차는 `DURATIONS`의 하루 순서다**(`duration_rank`). SQL의
    `v.duration ASC`는 문자열 순서라 지금 네 창에서만 하루 순서와 우연히 같다 —
    그 우연에 기대면 `targets[0]`(기본값)과 `app._run_groups`(칩·폴백)가 '첫 회차'를
    서로 다르게 고를 수 있다(1.26.283 검토). 아래에서 다시 세운다.
    """
    try:
        with db.session() as conn:
            frame = pd.read_sql(
                "SELECT v.run_label, v.duration,"
                "       COUNT(DISTINCT v.cluster) AS clusters,"
                "       MAX(r.created_at) AS created_at,"
                "       MAX(r.kind) AS kind"
                "  FROM vrp_plan v"
                "  LEFT JOIN runs r ON r.run_label = v.run_label"
                " GROUP BY v.run_label, v.duration"
                " ORDER BY created_at DESC, v.run_label DESC, v.duration ASC",
                conn)
    except Exception as err:
        print(f"[경고] 경로 목록 조회 실패: {type(err).__name__}: {err}")
        return pd.DataFrame()

    if frame.empty:
        return frame
    # 저장된 종류가 없으면 짐작한다 — 판정 규칙은 `db.classify_run_label()`
    # 하나만 쓴다(SQL에 같은 규칙을 다시 적으면 두 곳이 갈린다).
    # ⚠️ `row["kind"] or ...` 로 쓰면 안 된다 — 빈 칸은 `NaN`이고 **NaN은 참**이라
    # 짐작이 한 번도 안 걸린다(실제로 그렇게 썼다가 순서가 안 바뀌었다).
    kind = frame.apply(
        lambda row: row["kind"] if pd.notna(row["kind"])
        else db.classify_run_label(row["run_label"]), axis=1)
    frame = frame.assign(kind=kind)
    # 계획 먼저 → SQL이 준 실행 순서(시각 → 라벨) → 한 실행 안은 하루 순서.
    # 안정 정렬이라 계획끼리·실험끼리는 위의 시각 순서가 그대로 남는다.
    runs = frame["run_label"].drop_duplicates()
    keys = pd.DataFrame({
        "plan": (kind != "plan").astype(int),
        "run": frame["run_label"].map({label: i for i, label in enumerate(runs)}),
        "dur": frame["duration"].map(duration_rank),
    }, index=frame.index)
    order = keys.sort_values(["plan", "run", "dur"], kind="stable").index
    return frame.loc[order].reset_index(drop=True)


def run_durations() -> dict:
    """실행마다 **경로가 있는 회차**(`vrp_plan`) — `{run_label: [회차, ...]}`, 하루 순서.

    `runs.duration`에는 첫 회차 하나만 남으므로(`ensure_run`의 COALESCE) 네 회차
    실행도 `_05_10` 하나로 보였다. `/run` '저장된 실행'의 시간대 칸과
    `/api/pipeline-runs`가 이 한 벌을 쓴다(`saved_runs`, 1.26.283).
    """
    try:
        with db.session() as conn:
            pairs = conn.execute(
                "SELECT DISTINCT run_label, duration FROM vrp_plan").fetchall()
    except Exception as err:
        print(f"[경고] 회차 목록 조회 실패: {type(err).__name__}: {err}")
        return {}
    found: dict = {}
    for label, duration in pairs:
        if duration:
            found.setdefault(label, []).append(duration)
    # 설정에 없는 옛 코드는 맨 뒤, 그들끼리는 글자순(`DISTINCT`의 순서는 정해져 있지 않다)
    return {label: sorted(ds, key=lambda d: (duration_rank(d), d))
            for label, ds in found.items()}


def saved_runs() -> pd.DataFrame:
    """`run_labels()`(최신순 전부)에 `durations` 열을 더한 것 — 화면과 API가 같이 쓴다.

    `durations`는 경로가 있는 회차(`run_durations`)이고, 없으면 `runs.duration`
    하나, 그것도 없으면 빈 목록이다 — 기록에서 읽은 값만 쓴다. `/run` '저장된 실행'
    표(`app._saved_runs`)와 `/api/pipeline-runs`가 이 함수 하나를 거친다(점검 기록
    4장 "화면과 API는 같은 계산", 1.26.283 검토 — 예전에는 화면만 목록을 계산했다).
    """
    runs = run_labels()
    if runs.empty:
        return runs
    rounds = run_durations()

    def durations_of(row: dict) -> list:
        known = rounds.get(row["run_label"])
        if known:
            return list(known)
        first = row.get("duration")
        return [first] if isinstance(first, str) and first else []

    # 칸마다 목록이 든다 — `pd.Series(dtype=object)`로 만들어야 길이가 같은 목록들이
    # 2차원 배열로 펴지지 않는다.
    values = [durations_of(row) for row in runs.to_dict("records")]
    return runs.assign(durations=pd.Series(values, index=runs.index, dtype=object))


def records(frame: pd.DataFrame) -> list:
    """JSON 응답용 레코드 목록. NaN은 None으로 바꾼다."""
    if frame.empty:
        return []
    return frame.astype(object).where(pd.notna(frame), None).to_dict(orient="records")


# 계획이 '낡았다'고 볼 경계(시간). **실측에서 골랐다** — 하루가 지나면 대여소
# 54~64%의 재고가 달라진다(stock_history 12일, 날짜쌍 11개 전수). 계획은 그
# 시점 재고 스냅샷으로 세우므로, 하루가 지나면 전제의 절반 이상이 어긋난다.
#
# ⚠️ *"틀렸다"* 가 아니라 *"전제가 흔들렸다"* 는 뜻이다. 화면도 그렇게 말한다 —
# `/orders`의 '지금 재고와 대조하기'가 그것을 확인하는 길이다.
PLAN_STALE_HOURS = 24


def age_note(when, *, now=None) -> Optional[dict]:
    """*"얼마나 오래됐나"* 를 사람 말로. 못 읽으면 **`None`(모름)** 을 낸다.

    화면이 절대 시각만 적으면 읽는 사람이 오늘 날짜와 빼기를 해야 한다.
    실제로 홈이 **12일 된 계획**을 아무 말 없이 '마지막 계획'으로 띄우고
    있었다(2026-09-08 실측). 지도는 이미 지문으로 낡음을 말하는데
    (`catalog.list_maps()`) 계획에는 그 장치가 없었다.

    돌려주는 것: `{"hours": float, "text": str, "stale": bool}`.
    `stale`은 `PLAN_STALE_HOURS`를 넘겼는지다. **판정할 수 없으면 `None`** 이라
    화면이 *"모른다"* 고 말할 수 있다 — 지도 쪽과 같은 규약이다.
    """
    if when is None or (isinstance(when, float) and pd.isna(when)):
        return None
    stamp = pd.to_datetime(when, errors="coerce")
    if pd.isna(stamp):
        return None
    current = pd.Timestamp.now() if now is None else pd.to_datetime(now)
    hours = (current - stamp).total_seconds() / 3600
    if hours < 0:          # 시계가 어긋났다 — 짐작해서 말하지 않는다.
        return None
    if hours < 1:
        text = "방금"
    elif hours < 24:
        text = f"{int(hours)}시간 전"
    else:
        text = f"{int(hours // 24)}일 전"
    return {"hours": round(hours, 1), "text": text,
            "stale": hours >= PLAN_STALE_HOURS}


def stock_station_count() -> int:
    """수집된 재고가 있는 대여소 수. 없으면 0.

    수집 현황 화면(`collect_view`)이 쓴다. **여기 두는 이유는 계층 규약이다** —
    `webapp/`에서 `db.py`를 아는 곳은 이 파일 하나여야 한다(1.26.110에서
    되돌린 그 규약이고, 테스트가 지킨다).
    """
    try:
        with db.session() as conn:
            frame = pd.read_sql(
                "SELECT COUNT(DISTINCT station_id) AS n FROM stock_history", conn)
    except Exception as err:
        print(f"[경고] 재고 대여소 수 조회 실패: {type(err).__name__}: {err}")
        return 0
    return int(frame["n"].iloc[0]) if not frame.empty else 0


def periods_with_rentals() -> frozenset:
    """대여이력이 DB에 이미 적재된 순수요 기간 목록.

    `/run` 폼이 "이 기간은 원천 CSV를 안 씁니다"를 알리는 데 쓴다 —
    `db.read_rental_source()`가 이 목록에 있는 기간이면 CSV 경로를 안 보기 때문이다.
    """
    try:
        with db.session() as conn:
            return db.rental_periods(conn)
    except Exception as err:
        # 비우면 폼이 "원천 CSV가 필요하다"고 보수적으로 말한다 — 500보다 낫다(1.26.273).
        print(f"[경고] 적재된 기간 조회 실패: {type(err).__name__}: {err}")
        return frozenset()


def backtest(day_type: str = "weekday") -> pd.DataFrame:
    """월쌍 백테스트 기록(`tools/backtest_demand.py`). 못 읽으면 빈 표.

    `kpi_view.py`가 1.19.3부터 `db.load_backtest`를 직접 불렀다 — "webapp에서 db를
    import하는 곳은 store 하나"라는 규약의 알려진 예외였고(1.26.110), 여기로 옮겨
    예외 목록에서 지운다(1.26.273).
    """
    try:
        with db.session() as conn:
            return db.load_backtest(conn, day_type=day_type)
    except Exception as err:
        print(f"[경고] 백테스트 조회 실패: {type(err).__name__}: {err}")
        return pd.DataFrame()


def net_demand(period: str) -> pd.DataFrame:
    """한 기간의 순수요 표(`net_demand`). 못 읽으면 빈 표. (`backtest()`와 같은 이유로 여기에.)"""
    try:
        with db.session() as conn:
            return db.load_frame(conn, "net_demand", period=period)
    except Exception as err:
        print(f"[경고] 순수요 조회 실패: {type(err).__name__}: {err}")
        return pd.DataFrame()


def db_stamp() -> Optional[float]:
    """DB 파일의 수정 시각. 캐시 열쇠에 넣어 **웹 밖의 실행**도 캐시를 낡게 한다.

    1.26.262의 완료 훅은 웹에서 띄운 작업만 잡는다 — 서버를 켠 채 CLI로
    `run_pipeline.py`나 `tools/rebuild_net_demand.py`를 돌리면 히트맵이 영영 옛
    그림이었다(1.26.273). 파일이 없으면 None(그때는 어차피 캐시할 것도 없다).
    """
    try:
        return db.active_db_path().stat().st_mtime
    except OSError:
        return None


def time_budgets() -> dict:
    """(run_label, duration) → 그 회차가 계획될 때의 시간 예산(분). 기록 없으면 빈 dict.

    예산은 실행마다 바뀔 수 있어 `kpi_summary.time_budget_minutes`에 함께 적힌다.
    `/vehicles`의 예산 준수 타일이 상수 하나로 과거 회차 전부를 재고 있었다(1.26.273).
    """
    rows = kpi()
    if rows.empty or "time_budget_minutes" not in rows:
        return {}
    ok = rows.dropna(subset=["time_budget_minutes"])
    return {(str(r), str(d)): float(b)
            for r, d, b in zip(ok["run_label"], ok["duration"], ok["time_budget_minutes"])}
