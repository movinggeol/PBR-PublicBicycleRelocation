# DB 데이터 옮기기 — 따라 하기

두 PC를 오가며 작업할 때 **재고 스냅샷 하나를 옮기는 방법**입니다.
명령을 그대로 복사해 쓰시면 됩니다.

> **전체 동기화 이야기는 [두_PC_작업.md](두_PC_작업.md)가 정본입니다** — 무엇을
> 옮기고 무엇을 다시 만드는지 갈라 놓았습니다. 이 문서는 그중 **DB 이관 절차만**
> 떼어 손에 잡히게 쓴 것입니다.

---

## 0. 30초 요약

```powershell
# ① 가진 PC에서 내보낸다 — 셋 중 하나를 고르십시오
python tools/transfer_run.py --export "2026-08-11 real" --out data/transfer/run_20260811_real.db   # 하나만
python tools/transfer_run.py --export-all --period "25년 11월" --out data/transfer/runs_2511.db     # 그 기간 전부
python tools/transfer_run.py --export-all --out data/transfer/runs_all.db                          # 전부

# ② 파일을 USB·클라우드로 옮긴다  (git은 이 파일을 나르지 않습니다)

# ③ 받는 PC에서 넣는다 — 어느 갈래든 명령은 같습니다
python tools/transfer_run.py --import data/transfer/runs_all.db --dry-run
python tools/transfer_run.py --import data/transfer/runs_all.db
```

| 무엇을 옮기나 | 명령 | 크기(이 저장소 기준) |
| --- | --- | --- |
| **정본 하나만** (보통 이걸로 충분) | `--export "라벨"` | 약 **1.4MB** (9,124행) |
| **한 기간 전부** | `--export-all --period "25년 11월"` | 약 **3MB** (실행 7건·23,851행) |
| **전부** (새 PC 세팅) | `--export-all` | 약 **8.3MB** (실행 16건·69,474행) |

셋 다 1.3GB짜리 `bike_system.db`를 통째로 옮기는 것보다 **훨씬 작습니다** —
나머지는 파이프라인이 다시 만들기 때문입니다.

**수집기 자료(재고 시계열·TMAP 실측)는 도구가 다릅니다** — [3-B장](#3-b-수집기-자료-옮기기--재고-시계열tmap-실측):

```powershell
python tools/export_collected.py --road --out data/transfer/collected.db   # 내보내기
python tools/merge_stock.py data/transfer/collected.db                     # 받기(합치기)
```

---

## 1. 왜 이걸 해야 하나

**대부분의 데이터는 옮길 필요가 없습니다.** `bike_system.db`도, `pp_data/`도
`run_pipeline.py`가 다시 만듭니다.

**딱 하나, 다시 만들 수 없는 것이 있습니다.**

`station_info.stock`은 파이프라인이 **실행하는 순간 타슈 API에서 받아 온 실시간
재고**입니다. 2026-08-11에 돌린 실행의 재고는 그날로 끝났고, 오늘 다시 돌리면
**오늘 재고**가 들어옵니다.

그런데 실험의 **정본 스냅샷**이 `2026-08-11 real`로 정해져 있습니다
([DECISIONS.md 6-B](../분석/DECISIONS.md)). 그 라벨이 없는 PC는
**[TODO.md](../기록/TODO.md)의 실험 명령을 하나도 재현할 수 없습니다.**

> ⚠️ 실제로 2026-08-31에 이 일이 났습니다. 한쪽 PC에는 그 라벨이 있고 다른 쪽에는
> 없었는데, **양쪽 문서가 각자의 DB를 보고 서로 반대되는 사실을 적었습니다.**

---

## 2. 내보내기 (가진 PC에서)

### 2-1. 무엇이 있는지 본다

```powershell
python tools/transfer_run.py --list
```

이 DB에 있는 실행 라벨이 표로 나옵니다. **인자 없이 실행해도 같은 목록**이 나옵니다.

```
               실행 라벨  period duration    행
     2026-08-11 real 25년 11월   _05_10 9123
            sweep-10 26년 03월   _05_10 9299
            ...
```

### 2-2. 내보낸다

```powershell
python tools/transfer_run.py --export "2026-08-11 real" --out data/transfer/run_20260811_real.db
```

성공하면 담긴 표가 전부 찍힙니다.

```
'2026-08-11 real' → data\transfer\run_20260811_real.db
  runs                          1 행
  station_info              1,368 행
  parking_lot               1,373 행
  station_stock             1,374 행
  rebalance_plan            4,047 행
  pick_drop                   231 행
  ilp_plan                    194 행
  vrp_plan                    242 행
  metrics                     231 행
  route_summary                30 행
  kpi_summary                   3 행
  vehicle_assignment           30 행

합계 9,124행.
```

> 📁 **`data/transfer/`에 두십시오.** `data/`는 `.gitignore`에 있어 **git이
> 이 파일을 나르지 않습니다** — 폴더가 없으면 `mkdir data\transfer`로 만듭니다.
> 다른 경로에 둬도 동작하지만, 저장소 안 한 곳에 모아 두는 편이 찾기 쉽습니다.

### 2-3. 여러 개를 한 번에 — 기간 단위

한 기간의 실행을 **전부 한 파일**에 담습니다.

```powershell
python tools/transfer_run.py --export-all --period "25년 11월" --out data/transfer/runs_2511.db
```

```
실행 7건 → data	ransfer
uns_2511.db
  runs                          7 행
  station_info              4,104 행
  ...
담긴 라벨: 2026-08-11 real, 2026-08-12 all-g3000, 2026-08-12 g2000, ...
합계 23,851행.
```

> 기간 이름이 틀리면 **있는 기간 목록을 보여 주고 멈춥니다.** `--list`의
> `period` 칸에 나오는 표기를 그대로 쓰십시오(예: `25년 11월`).

### 2-4. 전부 옮기기 — 새 PC를 세팅할 때

```powershell
python tools/transfer_run.py --export-all --out data/transfer/runs_all.db
```

이 저장소 기준 **실행 16건 · 69,474행 · 약 8.3MB**입니다. 그래도 1.3GB DB보다
**150배 작습니다** — 나머지는 파이프라인이 다시 만드는 것들이기 때문입니다.

> 💡 **새 PC라면 이게 제일 편합니다.** 라벨을 하나씩 고를 필요 없이 한 번에
> 옮기고, 받는 쪽도 `--import` 한 번이면 끝납니다.

> ⚠️ **`net_demand`(순수요)는 담기지 않습니다.** 실행 라벨이 아니라 기간에
>묶인 자료라서입니다. 받는 PC에서 원천 대여이력으로 다시 만드십시오:
> `python tools/rebuild_net_demand.py` (원천 CSV는 어차피 양쪽에 있어야 합니다).

---

## 3. 파일 옮기기

**USB·클라우드 드라이브·메신저** 등 편한 방법으로 옮기면 됩니다. 1.4MB라
어디든 들어갑니다.

> ❌ **git으로는 옮겨지지 않습니다.** `data/`가 `.gitignore`에 있기 때문이고,
> 이는 **의도된 것**입니다 — DB를 커밋하면 커밋 1회당 약 404MB로 저장소가
> 60배가 되고 GitHub 100MB 상한에 걸립니다. 근거는
> [DECISIONS.md 6-D](../분석/DECISIONS.md).

---

## 3-B. 수집기 자료 옮기기 — 재고 시계열·TMAP 실측

지금까지는 **파이프라인 실행**을 옮겼습니다. 수집기가 모으는 자료는 **성격이
다릅니다** — 실행에 묶이지 않고 **시간에 묶여** 계속 쌓입니다.

| 자료 | 무엇 | 수집기 |
| --- | --- | --- |
| `stock_history` | 10분마다 쌓는 **재고 시계열** | `collect_stock.py` |
| `road_leg` | 고정 패널 **TMAP 실측** | `collect_road_time.py` |

> 🔑 **이쪽은 "합치는" 것이지 "덮는" 것이 아닙니다.** 두 PC가 **각자 다른 창을
> 맡아** 수집하므로(예: A는 평일, B는 휴일) 양쪽에 서로 다른 관측이 있습니다.
> 덮어쓰면 한쪽 관측이 사라집니다 — 그래서 받는 쪽은 **먼저 수집한 것이
> 이깁니다**(`INSERT OR IGNORE`).

### 3-B-1. 무엇이 쌓였는지 본다

```powershell
python tools/export_collected.py --list
```

```
  stock_history : 413,005행  2026-08-25 ~ 2026-09-01
  road_leg      : 고정 패널 300행 · 파이프라인 부산물 478행

날짜별 수집 틱:
      날짜  틱
2026-08-25 49
2026-08-28 61
...
```

### 3-B-2. 내보낸다

```powershell
# 전부 (TMAP 실측까지)
python tools/export_collected.py --road --out data/transfer/collected.db

# 기간을 잘라서
python tools/export_collected.py --from 2026-08-25 --to 2026-08-31 --out data/transfer/w35.db
```

```
수집 자료(처음 ~ 끝) → data	ransfer\collected.db
  stock_history        413,005 행
  stock_station_master   8,233 행
  road_leg                 300 행

합계 421,538행.
```

> 📌 **`stock_station_master`(그날의 대여소 이름·좌표)가 함께 담깁니다.**
> 재고만 옮기면 받는 PC에서 **"이름·좌표가 없는 날"** 경고가 뜹니다 —
> 한쪽만 관측한 날짜의 대여소 이름을 알 수 없기 때문입니다.
>
> 그래서 **`data/raw_data/재고이력/`의 일별 CSV를 복사하는 것보다 이 방법이
> 낫습니다.** CSV에는 재고만 있고 마스터도 TMAP 실측도 없습니다.

### 3-B-3. 받는다 — `merge_stock.py`가 받습니다

`transfer_run.py`가 **아니라** `merge_stock.py`입니다. 합치는 규칙이 다르기
때문입니다.

```powershell
python tools/merge_stock.py data/transfer/collected.db --dry-run
python tools/merge_stock.py data/transfer/collected.db
```

```
  새로 채움 : 413,005행
  이미 있음 : 0행 (먼저 수집한 값을 남겼습니다)
  마스터    : 8,233행
  TMAP 실측 : 300행 (고정 패널)
```

**몇 번을 다시 넣어도 안전합니다.** 두 번째부터는 `새로 채움 : 0행`이 되고
행이 늘지 않습니다(겹치는 관측은 건너뜁니다).

> ⚠️ **파이프라인 부산물 `road_leg`는 담기지 않습니다.** `roadprobe%` 라벨,
> 즉 **고정 패널 수집분만** 옮깁니다. 지도를 그리며 받은 실측은 그 PC의
> 실행에 속한 것이라 합칠 대상이 아닙니다.

### 3-B-4. 두 방식 비교

| | 파이프라인 실행 | 수집기 자료 |
| --- | --- | --- |
| 내보내기 | `transfer_run.py --export…` | `export_collected.py` |
| 받기 | `transfer_run.py --import` | **`merge_stock.py`** |
| 겹칠 때 | **멈춘다** (`--overwrite` 필요) | **합친다** (먼저 것이 이김) |
| 왜 | 실험 결과가 붙어 있어 덮으면 안 됨 | 각자 다른 창을 관측해 둘 다 필요 |

---

## 4. 받기 — 파이프라인 실행 (받는 PC에서)

### 4-1. 먼저 넣지 말고 확인한다

```powershell
python tools/transfer_run.py --import data/transfer/run_20260811_real.db --dry-run
```

`--dry-run`은 **아무것도 쓰지 않고** 무엇이 들어올지만 보여 줍니다.
**항상 이걸 먼저 하십시오.**

### 4-2. 넣는다

```powershell
python tools/transfer_run.py --import data/transfer/run_20260811_real.db
```

### 4-3. 들어갔는지 확인한다

```powershell
python tools/transfer_run.py --list
```

목록에 그 라벨이 보이면 끝입니다. 이제 실험 명령이 그 PC에서도 돕니다.

---

## 5. 이럴 때는 (실제 메시지)

| 화면에 나오는 것 | 뜻 | 할 일 |
| --- | --- | --- |
| `'○○' 실행이 이 DB에 없습니다.` + 목록 | 라벨 이름이 틀렸다 | **목록에서 그대로 복사**해 쓰십시오. 공백·따옴표까지 같아야 합니다 |
| `파일이 없습니다: ...` | 경로가 틀렸다 | 파일을 옮겼는지, 경로가 맞는지 확인 |
| `'○○'이 이미 이 DB에 있습니다. 말없이 덮지 않습니다` | **정상 동작입니다** | 아래 5-1 참고 |
| `period='○○'인 실행이 없습니다.` + 기간 목록 | 기간 표기가 틀렸다 | `--list`의 `period` 칸 표기를 그대로 쓰십시오 (`25년 11월`) |
| `○○가 이미 있습니다. 지우거나 다른 이름을 주십시오.` | **내보낼 파일**이 이미 있다 | 지우거나 `--out`에 다른 이름을 주십시오 |
| `적재된 실행이 없습니다.` | 그 DB가 비어 있다 (그 PC에서 파이프라인을 한 번도 안 돌렸다) | 받기만 할 거라면 그대로 `--import` 하면 됩니다 |

### 5-1. "이미 있습니다"가 나오면

**대부분 그냥 두는 것이 맞습니다.** 이미 있다는 것은 전에 받았다는 뜻이니까요.

⚠️ **여러 라벨이 든 파일은 하나라도 겹치면 멈춥니다.** 이미 받은 것을 다시
받으려 할 때 흔합니다 — 새로 받을 라벨만 있는 파일을 다시 내보내거나,
아래처럼 덮어쓰십시오.

정말 덮어야 한다면(예: 내보낸 쪽에서 다시 만들었다면):

```powershell
python tools/transfer_run.py --import data/transfer/run_20260811_real.db --overwrite
```

> ⚠️ **`--overwrite`는 신중히.** 그 라벨로 이미 실험을 돌렸다면, 덮는 순간
> **어느 쪽 숫자를 본 것인지 알 수 없게 됩니다.** 이것이 도구가 기본적으로
> 멈추는 이유입니다.

---

## 6. 알아 둘 것

### 6-1. 무엇이 따라가고 무엇이 안 가나

| | 따라가나 | 왜 |
| --- | --- | --- |
| `runs`·`station_info`·`station_stock` 등 **12개 표** | ✅ | 라벨 하나에 묶인 실행 전체 |
| `net_demand` (순수요) | ❌ | 기간 스코프다. `tools/rebuild_net_demand.py`로 다시 만든다 |
| `road_leg` (TMAP 실측) | ❌ | 각자 쌓는 관측치라 **합쳐야** 한다 → [3-B장](#3-b-수집기-자료-옮기기--재고-시계열tmap-실측) |
| `stock_history` (재고 시계열) | ❌ | 〃 (`export_collected.py` → `merge_stock.py`) |

### 6-2. 이 방식의 약점 — 자동이 아니다

**손으로 옮기는 것을 잊으면 두 PC가 다시 갈립니다.** 그래서 실험 결과를 볼 때는
**어느 스냅샷으로 쟀는지** 항상 확인하십시오.

⚠️ **다만 스냅샷을 찍어 주는 스크립트는 아직 일부뿐입니다**(2026-09-01 기준
`tour_length_estimate.py`·`cluster_count_sweep.py`·`wanted_vehicles_geo_sweep.py`가
`[스냅샷] station_info run_label = '...'`을, `z_stockout_grid.py`가 실행 조건에
라벨을 찍습니다). 나머지는 **`--run-label`로 고정할 수는 있지만 결과만 보고는
무엇으로 쟀는지 알 수 없습니다** — 명령을 직접 확인하십시오
([TODO.md](../기록/TODO.md) 대기-9).

### 6-3. 라벨은 PC마다 다를 수 있다

같은 프로젝트라도 PC마다 돌린 실행이 다르므로 **`--list` 결과가 다릅니다.**
문서에 적힌 라벨이 그 PC에 없을 수 있으니, **재기 전에 `--list`로 확인**하십시오.

---

## 관련 문서

- [두_PC_작업.md](두_PC_작업.md) — **두 PC 동기화 전체** (무엇을 옮기고 무엇을 다시 만드나)
- [COLLECTOR.md 11장](COLLECTOR.md) — 재고 시계열 수집 분담·`merge_stock.py`
- [DECISIONS.md 6-B·6-D](../분석/DECISIONS.md) — 정본 스냅샷을 정한 이유, git을 쓰지 않는 이유
- [DB_SCHEMA.md](DB_SCHEMA.md) — 표 18개의 컬럼·스코프 규칙
