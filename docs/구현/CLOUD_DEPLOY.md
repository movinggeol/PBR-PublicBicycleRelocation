# 클라우드 배포 (GCP Compute Engine + Docker · 열람 전용 데모)

> 재현: `PBR_READONLY=1 python -m webapp` 로 로컬에서 먼저 확인한다.

## 결정 (2026-11-02, 사용자)

- **용도는 열람 전용 데모**다. 심사 · 발표에서 결과를 보여 주는 것이 목적이고, 클라우드에서 파이프라인을 돌리지 않는다. 계획 실행과 수집은 계속 로컬 PC에서 한다.
- **Compute Engine VM 위에서 Docker 컨테이너로 띄운다.** 데이터(SQLite 3.4GB · `pp_data`)는 이미지에 넣지 않고 VM 디스크를 마운트한다.
- **Cloud Run은 이번에는 쓰지 않는다.** 디스크가 휘발성이라 3.4GB DB를 이미지에 구워야 하고, 콜드스타트마다 그 이미지를 받는다. 작년(클라우드 컴퓨팅 과제)에는 DB 없는 Flask 앱이라 맞았지만 이 앱은 아니다.
- **systemd로 Python을 직접 설치하는 방식도 기각했다.** 같은 일을 하지만 VM 환경이 꼬였을 때 컨테이너처럼 지우고 다시 만들 수 없고, 작년 Dockerfile 방식과도 이어지지 않는다.

### 재 본 것 (2026-11-01, Cloud Run을 택할 경우를 위한 기록)

`rental_history`(912만 행)를 빼고 `stock_history`(1,001만 행)를 최근 3일만 남긴 **소형 DB는 205MB**였다(원본 3.4GB의 약 6%).
웹 순회 점검(`tools/crawl_webapp.py`) 2,299개 주소에서 문제 0, 주요 화면 27개 중 22개가 같았다.
다른 것은 `/collect`(수집 현황이 65일 7,281틱에서 3일 72틱으로 줄어듦), `/run`(DB 대여 기간 목록이 빔), `/upcoming`(재고 틱 시각 차이), JSON 키 순서 둘이다.
즉 Cloud Run도 가능하지만 소형 DB 스크립트 · 읽기 전용 연결 · `/collect` 손보기가 먼저 필요하다. 소형 DB 제작 스크립트는 저장소에 넣지 않았다.

## 왜 코드를 고쳤나

웹은 원래 *"로컬 운영 도구라 인증이 없다"* (`webapp/app.py` 머리말). 인터넷에 열면 방문자 누구나 `POST /runs`로 수 분짜리 파이프라인을 띄우고 `POST /runs/.../kind`로 실행 종류를 바꾸며, `/orders/live` · `/m/live`로 타슈 API 키의 한도를 먹을 수 있다. **`PBR_READONLY=1`** 이면 미들웨어(`readonly_guard`)가 쓰기 메서드 전부와 그 두 주소를 403으로 막는다.
예외는 `POST /m/vehicle` 하나(이 기기 쿠키에만 쓴다). 테스트는 `tests/test_webapp.py`의 `열람_전용` 셋이다. 포트는 `PBR_PORT`(기본 8000).

⚠️ 이 막이 *유일한* 방어다. DB 연결 자체는 쓰기 가능(`db.connect()`가 WAL을 켜고 스키마를 맞춘다)이라 **데이터 폴더를 읽기 전용으로 마운트하면 뜨지 않는다.** 쓰기 가능하게 마운트하고 막은 것은 미들웨어다.
공개 주소를 오래 열어 둘 거면 앞에 인증(IAP 또는 Nginx basic auth)을 추가한다.

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
gcloud compute firewall-rules create pbr-web-8000 --allow=tcp:8000 --target-tags=pbr-web
```

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

**6. 확인**: 브라우저에서 `http://<VM 외부 IP>:8000`. 밖에서 재면 아래가 나와야 한다.

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
| 8000 포트 열기 | `gcloud compute firewall-rules create pbr-web-8000 --allow=tcp:8000 --target-tags=pbr-web` |
| 접속 막기 (규칙 삭제) | `gcloud compute firewall-rules delete pbr-web-8000` |
| 계정 · 프로젝트 확인 | `gcloud config list` |
| 프로젝트 바꾸기 | `gcloud config set project <프로젝트 ID>` |
| 결제 연결 확인 | `gcloud billing projects describe <프로젝트 ID>` |
| 다시 로그인 | `gcloud auth login` |

**VM 창에서 (컨테이너 관리)**

| 하는 일 | 명령 |
| --- | --- |
| 실행 중인 컨테이너 | `sudo docker ps` |
| 로그 보기 (오류 원인 찾기) | `sudo docker logs --tail 50 pbr-web` |
| 재시작 (데이터를 바꾼 뒤) | `sudo docker restart pbr-web` |
| 코드 변경 반영 | `bash vm_setup.sh` 다시 실행 |

**중지와 삭제의 차이**: 중지하면 컴퓨터 사용료는 멈추고 디스크(30GB)에 대한 소액만 나온다. 데이터와 컨테이너는 그대로다. 다시 시작하면 컨테이너는 `--restart unless-stopped`라서 자동으로 뜨지만 **외부 IP가 바뀐다**(`list`로 확인). 삭제하면 과금이 완전히 멈추지만 DB를 다시 올려야 한다.

**창을 닫아도 서버는 계속 돈다.** PuTTY 창이나 Cloud SDK Shell은 접속용 터미널일 뿐이고, 서버는 컨테이너로 VM 안에서 백그라운드로 돈다(`docker run -d`). 창을 닫는 것은 서버와 과금에 영향이 없다.

## 갱신 · 끄기

- **데이터를 바꿨을 때**: 1번 스냅샷을 새로 만들어 3번처럼 올리고 `sudo docker restart pbr-web`. 한글 이름이 든 파일을 올렸다면 5번을 다시 돌린다. **VM의 DB를 정본으로 삼지 마라.** 정본은 로컬 두 PC다(`두_PC_작업.md`).
- **코드를 바꿨을 때**: 저장소에 push한 뒤 VM에서 `vm_setup.sh`를 다시 실행한다(이미지를 새로 빌드한다).
- **발표 직전**: VM을 시작하고 `list`로 새 IP를 확인한다. **끝나면 중지하거나 삭제한다.** 켜 두면 계속 과금된다.

## 접속 주소의 두 가지

- `http://<VM 외부 IP>:8000`: 공개 주소다. 어느 네트워크에서나 열린다(방화벽이 8000을 전체에 허용). 로그인이 없고 쓰기만 막혀 있다. HTTPS가 아니라 브라우저가 "안전하지 않음"이라고 표시할 수 있다.
- 로그에 찍히는 `http://172.17.0.2:8000`은 컨테이너 안의 내부 주소다. 로컬에서 서버를 띄울 때의 "같은 와이파이 기기에서" 안내가 그대로 찍힌 것이라 이 환경에서는 무시한다.

## 열린 것

- 인증은 없다. 주소를 아는 사람은 누구나 볼 수 있다. 접속자를 줄이려면 방화벽 규칙에 허용할 IP 대역을 지정하거나 앞에 인증을 둔다. 도메인 · HTTPS도 정하지 않았다.
- VM의 DB는 2026-11-02 22:42 스냅샷이다. 그 뒤 수집분은 없다.
- 빌드가 끝난 뒤 `e2-small`로 낮춰도 되는지는 재 보지 않았다.
- 한글 파일 이름 정리(5번)가 손으로 하는 일이다. 올릴 때마다 반복할 거면 `tools/`의 스크립트나 `vm_setup.sh` 안으로 넣는 편이 낫다.
- Docker 이미지 빌드는 Linux에서 `ortools` · `kmedoids`가 Python 3.14 휠로 설치되어 성공했다(첫 빌드, 2026-11-02).
