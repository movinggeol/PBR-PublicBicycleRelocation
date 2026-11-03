# 클라우드 배포 (GCP Compute Engine + Docker · 열람 전용 데모)

> 재현: `PBR_READONLY=1 python -m webapp` 로 로컬에서 먼저 확인한다.

## 결정 (2026-11-02, 사용자)

- **용도는 열람 전용 데모**다. 심사 · 발표에서 결과를 보여 주는 것이 목적이고, 클라우드에서 파이프라인을 돌리지 않는다. 계획 실행과 수집은 계속 로컬 PC에서 한다.
- **Compute Engine VM 위에서 Docker 컨테이너로 띄운다.** 데이터(SQLite 3.4GB · `pp_data`)는 이미지에 넣지 않고 VM 디스크를 마운트한다.
- **Cloud Run은 이번에는 쓰지 않는다.** 디스크가 휘발성이라 3.4GB DB를 이미지에 구워야 하고, 콜드스타트마다 그 이미지를 받는다. 작년(클라우드 컴퓨팅 과제)에는 DB 없는 Flask 앱이라 맞았지만 이 앱은 아니다.
- **systemd로 Python을 직접 설치하는 방식도 기각했다.** 같은 일을 하지만 VM 환경이 꼬였을 때 컨테이너처럼 지우고 다시 만들 수 없고, 작년 Dockerfile 방식과도 이어지지 않는다.

### 공개 범위 (2026-11-03, 사용자)

- **평소에는 학교 · 집 IP에만 열고, 심사 · 발표 기간에만 모두에게 연다.** 인증(IAP · basic auth)과 HTTPS는 붙이지 않는다.
- 까닭: 웹에는 인증이 없고, 처음 만든 방화벽 규칙(`pbr-web-8000`)은 출처를 정하지 않아 **인터넷 전체에** 8000번을 열었다. 쓰기는 미들웨어가 막지만
  봇이 화면을 계속 열면 e2-medium(메모리 4GB)에 부하가 가고, 작업지시서 · 계획이 누구에게나 보인다. 열람 데모에는 인증보다 출처를 좁히는 쪽이 손이 덜 든다.
- 여닫는 명령은 아래 *방화벽 — 여닫기* 절에 있다. 할 일 목록은 [사람이_할_일](../기록/사람이_할_일.md) 0장.

### 재 본 것 (2026-11-01, Cloud Run을 택할 경우를 위한 기록)

`rental_history`(912만 행)를 빼고 `stock_history`(1,001만 행)를 최근 3일만 남긴 **소형 DB는 205MB**였다(원본 3.4GB의 약 6%).
웹 순회 점검(`tools/crawl_webapp.py`) 2,299개 주소에서 문제 0, 주요 화면 27개 중 22개가 같았다.
다른 것은 `/collect`(수집 현황이 65일 7,281틱에서 3일 72틱으로 줄어듦), `/run`(DB 대여 기간 목록이 빔), `/upcoming`(재고 틱 시각 차이), JSON 키 순서 둘이다.
즉 Cloud Run도 가능하지만 소형 DB 스크립트 · 읽기 전용 연결 · `/collect` 손보기가 먼저 필요하다. 소형 DB 제작 스크립트는 저장소에 넣지 않았다.

## 왜 코드를 고쳤나

웹은 원래 *"로컬 운영 도구라 인증이 없다"* (`webapp/app.py` 머리말). 인터넷에 열면 방문자 누구나 `POST /runs`로 수 분짜리 파이프라인을 띄우고 `POST /runs/.../kind`로 실행 종류를 바꾸며, `/orders/live` · `/m/live`로 타슈 API 키의 한도를 먹을 수 있다. **`PBR_READONLY=1`** 이면 미들웨어(`readonly_guard`)가 쓰기 메서드 전부와 그 두 주소를 403으로 막는다.
예외는 `POST /m/vehicle` 하나(이 기기 쿠키에만 쓴다). 테스트는 `tests/test_webapp.py`의 `열람_전용` 셋이다. 포트는 `PBR_PORT`(기본 8000).

⚠️ 이 막이 *유일한* 방어다. DB 연결 자체는 쓰기 가능(`db.connect()`가 WAL을 켜고 스키마를 맞춘다)이라 **데이터 폴더를 읽기 전용으로 마운트하면 뜨지 않는다.** 쓰기 가능하게 마운트하고 막은 것은 미들웨어다.
그래서 인증 대신 방화벽으로 출처를 좁힌다(위 *공개 범위*).

## 파일

| 파일 | 역할 |
| --- | --- |
| `Dockerfile` | Python 3.14 이미지 + `requirements.txt` + 코드. `PBR_READONLY=1` 기본. 워커 1개(작업 상태 · 캐시가 프로세스 메모리에 있다) |
| `.dockerignore` | `data/` · `.env` · `*.csv` · `*.db` · `docs/` · `experiments/` · `tests/` 제외. **`tools/`는 넣는다**: `/collect`가 `tools.collect_stock`을 불러온다 |
| `deploy/vm_setup.sh` | VM 안에서 한 번 실행: Docker 설치, 코드 받기, 데이터 배치, 이미지 빌드, 컨테이너 실행 |

## 올릴 것

`data/`는 git에 없으므로 따로 올린다. 필요한 것은 둘이다.

| 무엇 | 크기 | 비고 |
| --- | --- | --- |
| `data/bike_system.db` | 3.4GB | 대부분 `rental_history`와 `stock_history` |
| `data/pp_data/` | 127MB | 지도 HTML · CSV 다운로드가 여기서 서빙된다 |

`data/raw_data/`(2.5GB)와 `.env`(API 키)는 올리지 않는다. 열람 전용은 키가 필요 없다.

## 절차

```powershell
# 0. 올릴 DB를 단일 파일 사본으로 뜬다 — 수집기를 멈추지 않는다 (아래 'DB 사본 뜨기')
.\.venv\Scripts\python.exe -c "import sqlite3; s=sqlite3.connect('file:data/bike_system.db?mode=ro', uri=True); d=sqlite3.connect('data/transfer/bike_system_upload.db'); s.backup(d); d.execute('PRAGMA journal_mode=DELETE'); d.close(); s.close()"

# 1. VM (서울 리전, Debian 12, 디스크 30GB 이상)
gcloud services enable compute.googleapis.com
gcloud compute instances create pbr-web --zone=asia-northeast3-a --machine-type=e2-medium `
  --image-family=debian-12 --image-project=debian-cloud --boot-disk-size=30GB --tags=pbr-web
# 출처를 학교 · 집 IP로 좁혀 만든다 (공개 범위 결정). 출처를 빼면 0.0.0.0/0 — 인터넷 전체다
gcloud compute firewall-rules create pbr-web-8000 --allow=tcp:8000 --target-tags=pbr-web `
  --source-ranges=<학교 IP>/32,<집 IP>/32

# 2. 데이터와 스크립트 올리기
gcloud compute scp --zone=asia-northeast3-a data/transfer/bike_system_upload.db pbr-web:~/bike_system.db
gcloud compute scp --recurse --zone=asia-northeast3-a data/pp_data pbr-web:~/pp_data
gcloud compute scp --zone=asia-northeast3-a deploy/vm_setup.sh pbr-web:~/vm_setup.sh
```

```bash
# 3. VM 안에서 (gcloud compute ssh pbr-web --zone=asia-northeast3-a)
# 코드를 git으로 받을 때
REPO_URL=https://github.com/<사용자>/<저장소>.git bash vm_setup.sh
# 코드를 scp로 ~/pbr-src 에 미리 올렸다면
bash vm_setup.sh
```

접속은 `http://<VM 외부 IP>:8000`이다. 고정 IP가 필요하면 `gcloud compute addresses create`로 예약한다.
빌드가 끝나면 `e2-small`로 낮춰도 되는지는 아직 재 보지 않았다.

## DB 사본 뜨기 — 수집기를 멈추지 않는다

회사 PC는 10분마다 재고를 모으는 수집 PC라 **수집기를 멈추지 않는다**(잃은 틱은 되살릴 수 없다). 예전 0단계(`PRAGMA wal_checkpoint(TRUNCATE)` 뒤
원본 파일을 그대로 올리기)는 수집기를 멈춰야 했다 — 쓰는 중인 DB 파일을 복사하면 WAL에 남은 틱이 빠지거나 반쯤 쓰인 파일이 올라간다.

대신 원본을 **읽기 전용으로 열어 SQLite 백업 API로** 사본을 뜬다(절차 0단계의 한 줄). 하나의 읽기 스냅샷을 복사하므로 수집기가 쓰는 중이어도
어긋나지 않고, 원본에는 아무것도 쓰지 않는다. 끝에 `journal_mode=DELETE`로 바꿔 두어 `-wal` 없이 파일 하나만 옮기면 된다.

- 실측(2026-10-27, 회사 PC): 3.6GB를 **62초**에 떴다. 10분 틱 직후에 시작해 틱을 잃지 않았다.
- 사본 검사(`PRAGMA quick_check`)는 같은 파일에 **7분 30초**가 걸려 다음 틱과 겹쳤다(그 틱은 성공했다). 돌린다면 틱 직후에 시작한다.
- `db.session()`을 거치는 도구(`transfer_run.py` 등)로 열지 않는다. 새 코드를 받은 직후면 스키마 맞추기가 원본에 칸을 붙인다(2026-10-22에 `pick_drop.pick_rule`이 그렇게 붙었다).

## 방화벽 — 여닫기

공개 범위 결정(위)에 따른 명령이다. 규칙 이름은 절차 1단계의 `pbr-web-8000`.

```powershell
# 지금 상태 — 출처와 꺼짐 여부
gcloud compute firewall-rules describe pbr-web-8000 --format="value(sourceRanges,disabled)"

# 평소: 학교 · 집 IP만 (내 공인 IP는 (Invoke-RestMethod https://api.ipify.org) 로 확인)
gcloud compute firewall-rules update pbr-web-8000 --source-ranges=<학교 IP>/32,<집 IP>/32

# 심사 · 발표 기간: 모두에게
gcloud compute firewall-rules update pbr-web-8000 --source-ranges=0.0.0.0/0

# 끝나면 다시 좁히거나, 아예 끈다(--no-disabled로 되켠다)
gcloud compute firewall-rules update pbr-web-8000 --disabled
```

- 학교 망은 공인 IP가 여러 개(NAT 풀)일 수 있다. 학교에서 안 열리면 그 자리의 IP를 다시 확인해 더한다.
- `default-allow-ssh`(22번, 기본 네트워크가 만드는 규칙)는 키 인증이라 이 결정의 대상이 아니다.

## 갱신 · 끄기

- **데이터를 바꿨을 때**: 위 *DB 사본 뜨기*로 사본을 떠 올린 뒤, VM에서 **컨테이너를 멈추고 바꾼다.**

  ```bash
  sudo docker stop pbr-web
  rm -f /opt/pbr-data/bike_system.db-wal /opt/pbr-data/bike_system.db-shm   # 옛 WAL이 새 DB에 덮이지 않게
  mv ~/bike_system.db /opt/pbr-data/bike_system.db
  sudo docker start pbr-web
  ```

  ⚠️ 서버가 연 채로 파일을 바꾸거나 옛 `-wal`이 남아 있으면, 다음에 열 때 SQLite가 옛 WAL을 새 DB에 적용해 **DB가 깨질 수 있다**
  (예전 문구 *"DB를 다시 올리고 `docker restart`"* 의 빈틈, 2026-11-03 점검). **VM의 DB를 정본으로 삼지 마라.** 정본은 로컬 두 PC다(`두_PC_작업.md`).
  VM의 DB는 올린 시점의 사본이라 저절로 갱신되지 않는다.
- **코드를 바꿨을 때**: `vm_setup.sh`를 다시 실행한다.
- **끝나면 VM을 중지하거나 삭제한다.** 그대로 두면 계속 과금된다.

## 점검 결과 — 보완할 것 (2026-11-03)

사용자가 VM에 띄운 뒤(2026-11-03) 회사 PC에서 `origin/main`의 웹 코드를 읽어 점검했다. 실제 서버에는 접속하지 않았다(주소 미공유).

**막이는 제대로 막는다** — 고칠 것 없음

- 타슈 API를 부르는 곳은 웹 전체에서 `/orders/live` · `/m/live` 둘뿐이고(`webapp/app.py`의 `tashu.fetch_stations` 두 곳), 둘 다 `READONLY_BLOCKED_GETS`에 있다.
- 끝에 `/`를 붙인 주소는 Starlette가 원래 주소로 다시 보내므로 그대로 막힌다.
- 막히지 않는 GET 주소 가운데 하위 프로세스를 띄우거나(`jobs.start_job`은 `POST /runs`에서만 부른다) DB · 파일에 쓰는 곳은 없다.
- 그래프는 SVG 문자열(`webapp/charts.py`)이라 서버에 한글 글꼴이 없어도 깨지지 않는다.
- `rental_history`에는 회원 정보가 없고(자전거 번호 · 대여소 · 시각 · 좌표), 웹은 그 행을 그대로 내보내지 않는다.

**보완할 것** — ①②⑤는 코드, ③④는 이 문서에서 정했다

| | 무엇 | 어떻게 되나 | 고치는 법 |
| --- | --- | --- | --- |
| ① | **컨테이너 시각이 UTC다** | 코드는 시간대를 정하지 않고 지역 시각(`datetime.now()` · `date.today()`)을 쓰는데 `python:3.14-slim`의 기본이 UTC라 한국보다 9시간 늦다. `/run`의 *지금 회차*(`_auto_round`)가 9시간 전 회차로, `/upcoming`의 재고 경과 시간이 틀리게(같은 날 뜬 DB면 음수 — 낡은 재고를 새것으로 읽는다), 예보의 *내일*이 KST 00 ~ 09시에는 오늘로 나온다 | `Dockerfile`에 `ENV TZ=Asia/Seoul`과 `tzdata` 설치. 슬림 이미지에 시간대 자료가 없으면 `TZ`가 소리 없이 UTC로 남으므로 둘 다 넣고, 띄운 뒤 `sudo docker exec pbr-web date`가 KST인지 본다 |
| ② | **`/collect`가 서버에서 틀린 기준을 말한다** | 창을 윈도우 작업 스케줄러(`schtasks`)에서 읽는데 리눅스에는 없어 기본값 09:00-17:00으로 떨어진다 — 머리말이 *"기본값 — 등록된 작업을 찾지 못했습니다"* · 하루 49틱. 날짜별 표는 그날 창(`WINDOW_HISTORY`)으로 세어 맞다. DB가 올린 날에서 멈춰 있어 멈춤 경보도 뜬다 — 데모를 보는 사람에게 *"수집이 멈췄다"* 로 읽힌다 | 열람 전용(`PBR_READONLY`)일 때 창은 24시간으로 두고, 멈춤 경보 대신 *"○월 ○일 ○시 기준 사본"* 이라 쓴다 |
| ③ | **인터넷 전체에 열려 있었다** | 위 *공개 범위* | 정했다 — 방화벽으로 출처를 좁힌다(위 *방화벽 — 여닫기*) |
| ④ | **DB 올리기 절차가 수집기를 멈추게 했고, 교체가 DB를 깨뜨릴 수 있었다** | 0단계가 *"수집기가 쓰는 중이면 멈춘 뒤"* 였고, 갱신이 *"다시 올리고 restart"* 였다 | 고쳤다 — 위 *DB 사본 뜨기* · *갱신* |
| ⑤ | 작은 것 셋 | (가) 외부 API를 부르는 GET 주소가 새로 생기면 `READONLY_BLOCKED_GETS`에 손으로 넣어야 하고, 빠뜨리면 조용히 열린다. (나) `/api/weather` · `/api/forecast`의 `refresh=1`은 캐시를 건너뛴다 — 지금은 VM에 기상청 키가 없어 외부 호출이 없지만, 키를 넣으면 방문자가 한도를 쓴다. (다) `docker run`에 로그 크기 제한이 없어 오래 켜 두면 봇 요청 로그가 디스크를 채운다 | (가) *"`tashu.`를 부르는 라우트는 막는 목록에 있어야 한다"* 를 구문 트리로 보는 테스트. (나) 열람 전용이면 `refresh`를 무시한다. (다) `vm_setup.sh`의 `docker run`에 `--log-opt max-size=10m --log-opt max-file=3` |

## 열린 것

- 2026-11-03에 사용자가 VM에 띄웠다(열람 전용). 이미지가 빌드됐으므로 `requirements.txt` 의존성은 리눅스 Python 3.14에서 설치된 것으로 본다(사용자 보고 기준, 빌드 로그는 보지 않았다).
- 위 보완 ① · ② · ⑤는 코드가 남았다([TODO](../기록/TODO.md) 색인).
- 도메인 · HTTPS는 두지 않는다(공개 범위 결정). 고정 IP를 예약했는지는 기록이 없다.
