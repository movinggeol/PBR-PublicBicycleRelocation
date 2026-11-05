# 클라우드 배포 (GCP Compute Engine + Docker · 열람 전용 데모)

> 재현: `PBR_READONLY=1 python -m webapp` 로 로컬에서 먼저 확인한다.

## 결정 (2026-11-02, 사용자)

- **용도는 열람 전용 데모**다. 심사 · 발표에서 결과를 보여 주는 것이 목적이고, 클라우드에서 파이프라인을 돌리지 않는다. 계획 실행과 수집은 계속 로컬 PC에서 한다.
- **Compute Engine VM 위에서 Docker 컨테이너로 띄운다.** 데이터(SQLite 3.4GB · `pp_data`)는 이미지에 넣지 않고 VM 디스크를 마운트한다.
- **Cloud Run은 이번에는 쓰지 않는다.** 디스크가 휘발성이라 3.4GB DB를 이미지에 구워야 하고, 콜드스타트마다 그 이미지를 받는다. 작년(클라우드 컴퓨팅 과제)에는 DB 없는 Flask 앱이라 맞았지만 이 앱은 아니다.
- **systemd로 Python을 직접 설치하는 방식도 기각했다.** 같은 일을 하지만 VM 환경이 꼬였을 때 컨테이너처럼 지우고 다시 만들 수 없고, 작년 Dockerfile 방식과도 이어지지 않는다.

### 공개 범위 (2026-11-03, 사용자)

- **평소에는 학교 · 집 IP에만 열고, 심사 · 발표 기간에만 모두에게 연다.** 인증(IAP · basic auth)은 붙이지 않는다. (HTTPS는 같은 날 뒤에 붙이기로 바꿨다. 아래 *HTTPS* 항목.)
- 까닭: 웹에는 인증이 없고, 처음 만든 방화벽 규칙(`pbr-web-8000`)은 출처를 정하지 않아 **인터넷 전체에** 8000번을 열었다. 쓰기는 미들웨어가 막지만
  봇이 화면을 계속 열면 e2-medium(메모리 4GB)에 부하가 가고, 작업지시서 · 계획이 누구에게나 보인다. 열람 데모에는 인증보다 출처를 좁히는 쪽이 손이 덜 든다.
- 여닫는 명령은 아래 *방화벽 — 여닫기* 절에 있다. 할 일 목록은 [사람이_할_일](../기록/사람이_할_일.md) 0장.

### HTTPS (2026-11-03, 사용자 요청)

- **`https://geol.cloud`로 열기로 했다.** 처음 결정은 "HTTPS는 붙이지 않는다"였지만, `http://`로 열면 브라우저가 "안전하지 않음"을 띄우고 `geol.cloud`만 쳐서는 HTTPS로 가지 않는다. 앞단에 Caddy를 두면 인증서(Let's Encrypt)를 자동으로 받고 갱신하며 `http://`로 온 요청을 `https://`로 이동시킨다.
- **대가: 80번을 전체에 열어야 한다.** Let's Encrypt가 발급 · 갱신 때 밖에서 80번으로 접속해 보는데 그 출처 IP를 알 수 없다. 80번에서는 Caddy가 HTTPS로 이동시키는 응답만 하므로 대시보드는 노출되지 않는다. **출처 제한은 443번에 건다**(공개 범위 결정은 443번에서 그대로 지킨다).
- 대시보드 컨테이너는 호스트 포트를 열지 않는다(Caddy를 거쳐서만 닿는다). 그래서 `8000`번 직접 접속은 없어지고, 방화벽 규칙 `pbr-web-8000`은 쓸 일이 없다.

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
| `deploy/Caddyfile` | 앞단 Caddy 설정: `DOMAIN`(기본 `geol.cloud`)의 인증서를 받고 `pbr-web:8000`으로 넘긴다(아래 *도메인과 HTTPS*) |

## 올릴 것

`data/`는 git에 없으므로 따로 올린다. 필요한 것은 둘이다.

| 무엇 | 크기 | 비고 |
| --- | --- | --- |
| `data/bike_system.db` | 3.4GB | 대부분 `rental_history`와 `stock_history` |
| `data/pp_data/` | 127MB | 지도 HTML · CSV 다운로드가 여기서 서빙된다 |

`data/raw_data/`(2.5GB)와 `.env`(API 키)는 올리지 않는다. 열람 전용은 키가 필요 없다.

## 절차 (2026-11-02에 이 순서로 띄웠다)

⚠️ **gcloud 명령은 "Google Cloud SDK Shell"에서 친다. 이 창은 PowerShell이 아니라 cmd다.** 줄바꿈 기호(`` ` ``)가 먹지 않으니 명령을 한 줄로 붙여 넣는다. 일반 터미널에는 `gcloud`가 PATH에 없다(설치 위치: `%LOCALAPPDATA%\Google\Cloud SDK\google-cloud-sdk\bin`).

**0. 준비 (한 번만)**: 로그인, 프로젝트, 결제 연결, Compute Engine API.

```
gcloud auth login
gcloud config set project publicbicyclerebalancing
gcloud billing projects describe publicbicyclerebalancing
gcloud services enable compute.googleapis.com
```

`billingEnabled: true`가 나와야 한다.

**1. DB 스냅샷 만들기 (로컬, PowerShell)**: 수집기가 10분마다 DB에 쓰고 있어서, 3.4GB를 올리는 수십 분 사이에 파일을 그대로 복사하면 깨진 복사본이 될 수 있다. 수집기를 멈추는 대신 SQLite 백업 기능으로 일관된 스냅샷을 만든다(원본은 읽기만 한다, 약 45초).

```powershell
.\.venv\Scripts\python.exe -c "import sqlite3; s=sqlite3.connect('file:data/bike_system.db?mode=ro', uri=True, timeout=60); d=sqlite3.connect('data/bike_system_snapshot.db'); s.backup(d, pages=20000); d.execute('PRAGMA journal_mode=DELETE'); print(d.execute('PRAGMA quick_check').fetchone()); d.close(); s.close()"
```

`('ok',)`가 찍히면 된다. `data/`는 git이 무시한다. 올린 뒤 지운다.

**2. VM과 방화벽 만들기 (Cloud SDK Shell)**

```
gcloud compute instances create pbr-web --zone=asia-northeast3-a --machine-type=e2-medium --image-family=debian-12 --image-project=debian-cloud --boot-disk-size=30GB --tags=pbr-web
gcloud compute firewall-rules create pbr-web-80 --allow=tcp:80 --target-tags=pbr-web
gcloud compute firewall-rules create pbr-web-443 --allow=tcp:443 --target-tags=pbr-web --source-ranges=<학교 IP>/32,<집 IP>/32
```

80번은 인증서 발급용이라 전체에 연다(Caddy가 HTTPS로 이동시키는 응답만 한다). **출처 제한은 443번에 건다.** 처음 띄울 때 만든 `pbr-web-8000`은 `DOMAIN=none`(IP로만 열기)이 아니면 쓰지 않으니 `gcloud compute firewall-rules delete pbr-web-8000`으로 지운다.

끝에 찍히는 `EXTERNAL_IP`가 접속 주소가 된다. 디스크 200GB 미만이라는 경고는 무시해도 된다. 처음 `scp`나 `ssh`를 할 때 SSH 키를 만들며 암호를 물으면 엔터 두 번(빈 암호)이다. 서버 키를 저장할지 물으면 `y`.

**3. 데이터 올리기 (Cloud SDK Shell)**: `<VM 계정>`은 로그인한 구글 계정 이메일의 `@` 앞부분이다(VM 홈이 `/home/<VM 계정>`).

```
cd /d "C:\myPython\졸작(4-1,4-2)\data"
gcloud compute scp --zone=asia-northeast3-a bike_system_snapshot.db pbr-web:/home/<VM 계정>/bike_system.db
gcloud compute scp --recurse --zone=asia-northeast3-a pp_data pbr-web:/home/<VM 계정>/
cd /d "C:\myPython\졸작(4-1,4-2)\deploy"
gcloud compute scp --zone=asia-northeast3-a vm_setup.sh pbr-web:/home/<VM 계정>/vm_setup.sh
```

- 목적지 파일 이름은 `bike_system.db`다. `vm_setup.sh`가 그 이름을 찾는다.
- **목적지에 `~/`를 쓰지 마라.** Windows의 `gcloud compute scp`는 PuTTY의 `pscp`를 쓰는데 `~`를 풀지 못해 `unable to open ~/...`로 실패한다. 절대 경로를 쓴다.
- **원본 경로에 `C:`가 들어가면 scp가 서버 주소로 오해한다.** 위처럼 `cd /d`로 먼저 이동하고 상대 파일 이름만 쓴다.
- 로컬 사용자 이름이 한글이면 `Invalid characters in local username` 경고가 뜨고 gcloud가 구글 계정 이름으로 바꿔 쓴다. 동작에는 문제없다.
- DB 3.4GB는 약 20분 걸렸다(2.5MB/s 안팎).

**4. VM에서 서버 올리기**

```
gcloud compute ssh pbr-web --zone=asia-northeast3-a
```

PuTTY 창이 열린다(프롬프트 `<VM 계정>@pbr-web:~$`). **아래는 그 VM 창에서 친다.** `sed`처럼 Linux 명령은 Cloud SDK Shell(Windows)에서 실행되지 않는다.

```bash
sed -i 's/\r$//' vm_setup.sh
head -2 vm_setup.sh
REPO_URL=https://github.com/movinggeol/PBR-PublicBicycleRelocation.git bash vm_setup.sh
```

`head -2` 첫 줄이 `#!/usr/bin/env bash`면 정상이다. 스크립트가 `[1/5]`~`[5/5]`를 출력하고, 이미지 빌드(`[4/5]`)가 몇 분 걸린다. 끝에 컨테이너 로그와 접속 주소가 나온다.

스크립트를 `curl`로 받는 방법(`raw.githubusercontent.com`)은 주소를 직접 치다 글자가 빠져 HTML 오류 페이지가 `vm_setup.sh`로 저장된 일이 있어서(`<!DOCTYPE html>`에서 문법 오류) 쓰지 않았다. 쓴다면 `curl -fsSL -o`로 오류 때 저장하지 않게 한다.

**5. 한글 파일 이름 고치기 (VM 창, 올릴 때마다)**: Windows `pscp`가 한글 이름을 cp949로 보내 Linux에 깨진 이름으로 저장된다. 그대로 두면 `/maps`가 `UnicodeEncodeError: surrogates not allowed`로 500이다(2026-11-02 190개: 폴더 6개와 파일 184개). 깨진 이름만 cp949로 읽어 UTF-8로 다시 붙인다. 멀쩡한 이름은 건드리지 않고, 같은 이름이 이미 있으면 건너뛴다.

```bash
python3 - <<'PYEOF'
import os
root = b'/opt/pbr-data/pp_data'
bad = 0
for d, dirs, files in os.walk(root, topdown=False):
    for n in dirs + files:
        try:
            n.decode('utf-8'); continue
        except UnicodeDecodeError:
            pass
        bad += 1
        try:
            new = n.decode('cp949').encode('utf-8')
        except UnicodeDecodeError:
            print('변환 불가:', os.path.join(d, n)); continue
        src, dst = os.path.join(d, n), os.path.join(d, new)
        if os.path.exists(dst):
            print('이미 있음:', dst); continue
        os.rename(src, dst)
        print('고침:', src, '->', new.decode('utf-8'))
print('깨진 이름', bad, '개 처리')
PYEOF
```

서버 재시작은 필요 없다. 목록은 요청마다 새로 읽는다.

**6. 확인**: 브라우저에서 `https://geol.cloud`(`DOMAIN=none`으로 띄웠으면 `http://<VM 외부 IP>:8000`). 밖에서 재면 아래가 나와야 한다.

- 화면 200: `/` `/kpi` `/maps` `/data` `/collect` `/upcoming` `/vehicles` `/orders` `/api/kpi` (응답 0.2~0.8초)
- 열람 전용: `POST /runs`와 `GET /orders/live`가 403
- `/maps` 지도 링크 303개, `/data` CSV 미리보기 링크 348개 (2026-11-02 스냅샷 기준)

## gcloud 명령 모음 (Cloud SDK Shell)

| 하는 일 | 명령 |
| --- | --- |
| 상태 · 외부 IP 보기 | `gcloud compute instances list` |
| **중지** | `gcloud compute instances stop pbr-web --zone=asia-northeast3-a` |
| **시작** | `gcloud compute instances start pbr-web --zone=asia-northeast3-a` |
| 재부팅 | `gcloud compute instances reset pbr-web --zone=asia-northeast3-a` |
| **삭제** (디스크째 지워짐, 복구 불가) | `gcloud compute instances delete pbr-web --zone=asia-northeast3-a` |
| 외부 IP만 보기 | `gcloud compute instances describe pbr-web --zone=asia-northeast3-a --format="get(networkInterfaces[0].accessConfigs[0].natIP)"` |
| VM에 접속 | `gcloud compute ssh pbr-web --zone=asia-northeast3-a` |
| 파일 올리기 | `gcloud compute scp --zone=asia-northeast3-a <파일> pbr-web:/home/<VM 계정>/<이름>` |
| 폴더 올리기 | `gcloud compute scp --recurse --zone=asia-northeast3-a <폴더> pbr-web:/home/<VM 계정>/` |
| 방화벽 규칙 목록 | `gcloud compute firewall-rules list` |
| 학교 · 집 IP로 좁히기 (평소) | `gcloud compute firewall-rules update pbr-web-443 --source-ranges=<학교 IP>/32,<집 IP>/32` |
| 모두에게 열기 (심사 · 발표) | `gcloud compute firewall-rules update pbr-web-443 --source-ranges=0.0.0.0/0` |
| 접속 막기 | `gcloud compute firewall-rules update pbr-web-443 --disabled` (`pbr-web-80`은 막지 않는다 — 아래 *방화벽 — 여닫기*) |
| (`DOMAIN=none`일 때만) 8000 포트 열기 · 막기 | `gcloud compute firewall-rules create pbr-web-8000 --allow=tcp:8000 --target-tags=pbr-web` · `delete pbr-web-8000` |
| 계정 · 프로젝트 확인 | `gcloud config list` |
| 프로젝트 바꾸기 | `gcloud config set project <프로젝트 ID>` |
| 결제 연결 확인 | `gcloud billing projects describe <프로젝트 ID>` |
| 다시 로그인 | `gcloud auth login` |

**VM 창에서 (컨테이너 관리)**

| 하는 일 | 명령 |
| --- | --- |
| 실행 중인 컨테이너 | `sudo docker ps` |
| 로그 보기 (오류 원인 찾기) | `sudo docker logs --tail 50 pbr-web` |
| 재시작 | `sudo docker restart pbr-web` — ⚠️ **DB를 바꿀 때는 쓰지 않는다.** 멈추고 옛 `-wal`을 지운 뒤 바꾼다(아래 *갱신 · 끄기*) |
| 코드 변경 반영 | `bash vm_setup.sh` 다시 실행 |

**중지와 삭제의 차이**: 중지하면 컴퓨터 사용료는 멈추고 디스크(30GB)에 대한 소액만 나온다. 데이터와 컨테이너는 그대로다. 다시 시작하면 컨테이너는 `--restart unless-stopped`라서 자동으로 뜨지만 **외부 IP가 바뀐다**(`list`로 확인). 삭제하면 과금이 완전히 멈추지만 DB를 다시 올려야 한다.

**창을 닫아도 서버는 계속 돈다.** PuTTY 창이나 Cloud SDK Shell은 접속용 터미널일 뿐이고, 서버는 컨테이너로 VM 안에서 백그라운드로 돈다(`docker run -d`). 창을 닫는 것은 서버와 과금에 영향이 없다.

## DB 사본 뜨기 — 수집기를 멈추지 않는다

회사 PC는 10분마다 재고를 모으는 수집 PC라 **수집기를 멈추지 않는다**(잃은 틱은 되살릴 수 없다). 예전 0단계(`PRAGMA wal_checkpoint(TRUNCATE)` 뒤
원본 파일을 그대로 올리기)는 수집기를 멈춰야 했다 — 쓰는 중인 DB 파일을 복사하면 WAL에 남은 틱이 빠지거나 반쯤 쓰인 파일이 올라간다.

대신 원본을 **읽기 전용으로 열어 SQLite 백업 API로** 사본을 뜬다(절차 1단계의 한 줄). 하나의 읽기 스냅샷을 복사하므로 수집기가 쓰는 중이어도
어긋나지 않고, 원본에는 아무것도 쓰지 않는다. 끝에 `journal_mode=DELETE`로 바꿔 두어 `-wal` 없이 파일 하나만 옮기면 된다.

- 실측(2026-10-27, 회사 PC): 3.6GB를 **62초**에 떴다. 10분 틱 직후에 시작해 틱을 잃지 않았다.
- 사본 검사(`PRAGMA quick_check`)는 같은 파일에 **7분 30초**가 걸려 다음 틱과 겹쳤다(그 틱은 성공했다). 1단계의 한 줄은 검사까지 하므로 **회사 PC에서는 틱 직후에** 시작한다.
- `db.session()`을 거치는 도구(`transfer_run.py` 등)로 열지 않는다. 새 코드를 받은 직후면 스키마 맞추기가 원본에 칸을 붙인다(2026-10-22에 `pick_drop.pick_rule`이 그렇게 붙었다).

## 방화벽 — 여닫기

공개 범위 결정(위)에 따른 명령이다. **여닫는 규칙은 `pbr-web-443`이다**(HTTPS 구성, 절차 2단계). 80번 규칙 `pbr-web-80`은 인증서 발급 · 갱신용이라 늘 전체 공개로 둔다.

```powershell
# 지금 상태 — 출처와 꺼짐 여부
gcloud compute firewall-rules describe pbr-web-443 --format="value(sourceRanges,disabled)"

# 평소: 학교 · 집 IP만 (내 공인 IP는 (Invoke-RestMethod https://api.ipify.org) 로 확인)
gcloud compute firewall-rules update pbr-web-443 --source-ranges=<학교 IP>/32,<집 IP>/32

# 심사 · 발표 기간: 모두에게
gcloud compute firewall-rules update pbr-web-443 --source-ranges=0.0.0.0/0

# 끝나면 다시 좁히거나, 아예 끈다(--no-disabled로 되켠다)
gcloud compute firewall-rules update pbr-web-443 --disabled
```

- **`pbr-web-80`은 좁히거나 끄지 마라.** 80번을 막으면 인증서 갱신이 실패한다(발급은 90일짜리라 만료가 다가와서야 드러난다). 80번에서는 HTTPS로 이동시키는 응답만 나가므로 열어 둬도 대시보드는 노출되지 않는다.
- 학교 망은 공인 IP가 여러 개(NAT 풀)일 수 있다. 학교에서 안 열리면 그 자리의 IP를 다시 확인해 더한다.
- `default-allow-ssh`(22번, 기본 네트워크가 만드는 규칙)는 키 인증이라 이 결정의 대상이 아니다.

## 갱신 · 끄기

- **데이터를 바꿨을 때**: 1번 스냅샷을 새로 만들어 3번처럼 올린 뒤, VM에서 **컨테이너를 멈추고 바꾼다.** 한글 이름이 든 파일을 올렸다면 5번을 다시 돌린다.

  ```bash
  sudo docker stop pbr-web
  rm -f /opt/pbr-data/bike_system.db-wal /opt/pbr-data/bike_system.db-shm   # 옛 WAL이 새 DB에 덮이지 않게
  mv ~/bike_system.db /opt/pbr-data/bike_system.db
  sudo docker start pbr-web
  ```

  ⚠️ 서버가 연 채로 파일을 바꾸거나 옛 `-wal`이 남아 있으면 다음에 열 때 SQLite가 옛 WAL을 새 DB에 적용해 **DB가 깨질 수 있다**(2026-11-03 점검). **VM의 DB를 정본으로 삼지 마라.** 정본은 로컬 두 PC다(`두_PC_작업.md`). VM의 DB는 올린 시점의 사본이라 저절로 갱신되지 않는다.
- **코드를 바꿨을 때**: 저장소에 push한 뒤 VM에서 `vm_setup.sh`를 다시 실행한다(이미지를 새로 빌드한다).
- **발표 직전**: VM을 시작하고 `list`로 새 IP를 확인한다. IP가 바뀌었으면 **DNS의 A 레코드도 새 IP로 고친다**(아래 *도메인과 HTTPS*). 그러지 않으면 `geol.cloud`가 옛 IP를 가리켜 아무 응답이 없다. 443번을 모두에게 열지도 이때 정한다(*방화벽 — 여닫기*). **끝나면 중지하거나 삭제한다.** 켜 두면 계속 과금된다.

## 접속 주소의 두 가지

- `https://geol.cloud`: 접속 주소다. 방화벽 `pbr-web-443`의 출처를 학교 · 집 IP로 좁힌 경우 그 IP에서만 열린다(출처를 빼면 인터넷 전체). 로그인이 없고 쓰기만 막혀 있다. `http://`로 오면 `https://`로 이동한다(308).
- `DOMAIN=none`으로 띄운 경우에만 `http://<VM 외부 IP>:8000`이 된다(HTTPS 없음, `pbr-web-8000` 규칙 필요).
- 로그에 찍히는 `http://172.17.0.2:8000`(또는 `172.18.0.2`)은 컨테이너 안의 내부 주소다. 로컬 서버의 "같은 와이파이 기기에서" 안내가 그대로 찍힌 것이라 무시한다.

## 점검 결과 — 보완할 것 (2026-11-03)

사용자가 VM에 띄운 뒤(2026-11-03) 회사 PC에서 `origin/main`의 웹 코드를 읽어 점검했다. 실제 서버에는 접속하지 않았다(주소 미공유).
2026-11-05 09:30에 회사 PC에서 `https://geol.cloud`로 GET만 보내 증상을 보려 했으나 443 · 80번 모두 20초 안에 응답이 없었다(DNS는 34.64.226.59).
80번은 늘 전체 공개이므로 출처 제한 탓이 아니라 VM이 꺼져 있거나 다시 켜며 IP가 바뀐 것으로 본다. 아래 ① · ②는 그래서 아직 코드 읽기로만 확인한 것이다.

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

- 인증은 없다. 도메인 · HTTPS는 `https://geol.cloud`로 붙였다(아래 *도메인과 HTTPS*). 공개 범위 결정(학교 · 집 IP만)은 `pbr-web-443`의 출처로 건다. 2026-11-03 현재 443번은 전체 공개 상태이고, 고정 IP는 예약하지 않았다.
- VM의 DB는 올린 시점의 스냅샷이라 그 뒤 수집분은 없다(2026-11-02 22:42, 2026-11-04 다시 뜸).
- 위 보완 ① · ② · ⑤는 코드가 남았다([TODO](../기록/TODO.md) 색인).
- 빌드가 끝난 뒤 `e2-small`로 낮춰도 되는지는 재 보지 않았다.
- 한글 파일 이름 정리(5번)가 손으로 하는 일이다. 올릴 때마다 반복할 거면 `tools/`의 스크립트나 `vm_setup.sh` 안으로 넣는 편이 낫다.
- Docker 이미지 빌드는 Linux에서 `ortools` · `kmedoids`가 Python 3.14 휠로 설치되어 성공했다(첫 빌드, 2026-11-02).

## 도메인과 HTTPS (`https://geol.cloud`)

- **도메인은 업체의 "포워딩"이 아니라 DNS의 A 레코드**(`@` → VM 외부 IP)로 건다. 포워딩은 카페24 서버를 거치고(유동은 주소창이 IP로 바뀌고 고정은 프레임에 넣는다), 반영에 24~48시간이 걸린다. A 레코드는 30분~1시간이고 주소창이 `geol.cloud`로 유지된다(2026-11-03에 카페24 DNS 관리에서 A 레코드를 넣었더니 구글 DNS에는 바로 반영됐고, 공급자 DNS 캐시가 풀리는 데 몇 시간이 걸렸다). A 레코드가 적용된 뒤에는 포워딩 설정은 필요 없다.
- **`vm_setup.sh`가 컨테이너 둘을 띄운다.** 대시보드 `pbr-web`(호스트 포트 없음)과 Caddy `pbr-caddy`(80 · 443). 둘은 도커 네트워크 `pbr-net`으로 이어지고 Caddy가 `pbr-web:8000`으로 넘긴다(`deploy/Caddyfile`). 인증서는 볼륨 `pbr_caddy_data`에 남아 컨테이너를 다시 만들어도 새로 받지 않는다(발급 한도 때문이다). 도메인은 환경변수 `DOMAIN`(기본 `geol.cloud`)이고, `DOMAIN=none`이면 Caddy 없이 옛 방식(IP의 80 · 8000번 직접)으로 연다.
- **띄우는 순서가 있다.** (1) DNS의 A 레코드가 VM 외부 IP를 가리킨다. (2) 방화벽 `pbr-web-80`과 `pbr-web-443`을 만든다(절차 2단계). (3) VM에서 `bash /opt/pbr/deploy/vm_setup.sh`. 끝에 Caddy 로그가 나오고 `certificate obtained successfully` 같은 줄이 보이면 발급된 것이다. 방화벽이나 DNS가 안 맞은 채 돌리면 발급이 실패하고, 한도(시간당 실패 5회)에 걸리면 기다려야 하니 **1 · 2번을 먼저 확인한다.**
- **발급이 안 될 때**: `sudo docker logs pbr-caddy`. 흔한 원인은 80 · 443번이 막혀 있는 것, A 레코드가 옛 IP를 가리키는 것이다.
- **VM을 껐다 켜면 외부 IP가 바뀌어** A 레코드가 옛 IP를 가리킨다. 인증서는 도메인에 묶여 있어 DNS만 따라가면 유지된다. 켤 때마다 레코드를 고치거나(TTL을 300초로 짧게) 고정 IP를 예약한다(`gcloud compute addresses create`, 꺼진 동안에도 소액이 청구된다). 갱신(약 60일마다)에는 80번이 밖에서 닿아야 한다.
- `.cloud` 최상위 도메인이 브라우저에서 HTTPS만 허용하도록 정해져 있는지는 확인하지 못했다. HTTPS를 붙였으니 어느 쪽이든 열린다.

**띄워 본 결과 (2026-11-03)**: 밖에서 `https://geol.cloud/kpi` 200(인증서 검증 통과, Let's Encrypt), `http://geol.cloud` 308 → `https://geol.cloud/`, `POST /runs` 403, 첫 화면 제목 "PBR 대시보드". 발급은 TLS-ALPN-01(443번)로 5초 안에 끝났다. 이어서 `pbr-web-8000` 규칙을 지웠다.

**겪은 일 (같은 일을 반복하지 않게)**

- **VM 안의 `gcloud`는 방화벽을 못 바꾼다.** VM 서비스 계정의 권한 범위에 없어 `insufficient authentication scopes`가 난다. 방화벽 · VM 시작 · 중지는 내 PC의 Cloud SDK Shell에서 한다.
- **scp로 올린 파일이 `git pull`과 충돌한다.** VM의 작업 폴더에 같은 파일이 먼저 있으면 `would be overwritten by merge`로 pull이 멈춘다. 코드는 push한 뒤 pull로만 받는다(급해서 scp로 올렸다면 `rm`이나 `git checkout --`로 치우고 pull).
- **`vm_setup.sh`가 도는 중에 pull이 자기 자신을 바꿔** 옛 내용으로 끝까지 돌았다. 지금은 pull 뒤 HEAD가 달라졌으면 새 버전으로 처음부터 다시 돈다(`PBR_REEXEC`). 옛 VM 스크립트(`.git` 대체 경로가 없는 버전)를 한 번 넘길 때만 `REPO_URL=... bash vm_setup.sh`로 실행한다.
- **이 PC의 사용자 이름이 한글이라** 일부 도구에서 `gcloud compute scp`가 `couldn't be escaped for ProxyCommand`로 실패한다. 사용자 Cloud SDK Shell에서는 되므로 올리기는 거기서 한다.
- **`stop`하면** CPU · 메모리 요금이 멈추고 디스크 요금은 계속 나온다. 임시 외부 IP는 꺼진 동안 무료이고 고정 IP는 소액이 붙는다.
- 이미 떠 있는 컨테이너를 이 구성으로 바꾸려면 push한 뒤 VM에서 `git -C /opt/pbr pull && bash /opt/pbr/deploy/vm_setup.sh`를 실행한다(기존 `pbr-web`을 지우고 둘을 다시 만든다. DB와 `pp_data`는 VM 디스크라 그대로다).
