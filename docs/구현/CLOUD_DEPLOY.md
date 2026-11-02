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

## 절차

```powershell
# 0. 업로드 전에 DB를 한 덩어리로 (WAL 조각 방지). 수집기가 쓰는 중이면 멈춘 뒤에 한다
.\.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('data/bike_system.db'); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()"

# 1. VM (서울 리전, Debian 12, 디스크 30GB 이상)
gcloud services enable compute.googleapis.com
gcloud compute instances create pbr-web --zone=asia-northeast3-a --machine-type=e2-medium `
  --image-family=debian-12 --image-project=debian-cloud --boot-disk-size=30GB --tags=pbr-web
gcloud compute firewall-rules create pbr-web-8000 --allow=tcp:8000 --target-tags=pbr-web

# 2. 데이터와 스크립트 올리기
gcloud compute scp --zone=asia-northeast3-a data/bike_system.db pbr-web:~/bike_system.db
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

## 갱신 · 끄기

- **데이터를 바꿨을 때**: DB를 다시 올리고 `sudo docker restart pbr-web`. 3.4GB를 매번 올리기 부담이면 `rsync`로 차이만 올린다. **VM의 DB를 정본으로 삼지 마라.** 정본은 로컬 두 PC다(`두_PC_작업.md`).
- **코드를 바꿨을 때**: `vm_setup.sh`를 다시 실행한다.
- **끝나면 VM을 중지하거나 삭제한다.** 그대로 두면 계속 과금된다.

## 열린 것

- 실제 VM에서 아직 띄워 보지 못했다. Docker 이미지는 빌드조차 못 했다(이 PC에 docker가 없다).
- Linux에서 `ortools` · `kmedoids`가 Python 3.14 휠로 설치되는지 모른다. 안 되면 첫 빌드 로그에서 드러난다.
- 공개 노출 전 인증 여부, 도메인 · HTTPS는 정해지지 않았다.
