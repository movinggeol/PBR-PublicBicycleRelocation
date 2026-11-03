#!/usr/bin/env bash
# VM 안에서 한 번 실행한다: Docker 설치 -> 코드 받기 -> 이미지 빌드 -> 컨테이너 실행.
# 절차 전체는 docs/구현/CLOUD_DEPLOY.md.
#
# 쓰기 (VM에 ssh 접속한 뒤):
#   # 방법 1) git으로 코드 받기
#   REPO_URL=https://github.com/<사용자>/<저장소>.git bash vm_setup.sh
#   # 방법 2) 코드를 ~/pbr-src 로 미리 scp 해 뒀다면
#   bash vm_setup.sh
#
# 미리 올려 둘 것(로컬에서 gcloud compute scp):
#   ~/bike_system.db      (3.4GB)
#   ~/pp_data/            (127MB)
set -euo pipefail

APP_DIR=/opt/pbr          # 코드
DATA_DIR=/opt/pbr-data    # 데이터(컨테이너의 /app/data로 마운트)
IMAGE=pbr-web
NAME=pbr-web

echo "[1/5] Docker 설치"
if ! command -v docker >/dev/null; then
  sudo apt-get update
  sudo apt-get install -y docker.io git
  sudo systemctl enable --now docker
fi

echo "[2/5] 코드 준비 -> $APP_DIR"
sudo mkdir -p "$APP_DIR"
sudo chown "$USER" "$APP_DIR"
if [ -n "${REPO_URL:-}" ]; then
  if [ -d "$APP_DIR/.git" ]; then git -C "$APP_DIR" pull --ff-only; else git clone "$REPO_URL" "$APP_DIR"; fi
elif [ -d "$APP_DIR/.git" ]; then
  # 이미 받아 둔 저장소가 있으면 REPO_URL 없이도 갱신한다(두 번째 실행부터)
  git -C "$APP_DIR" pull --ff-only
elif [ -d "$HOME/pbr-src" ]; then
  cp -a "$HOME/pbr-src/." "$APP_DIR/"
else
  echo "코드가 없습니다. REPO_URL을 주거나 ~/pbr-src 에 코드를 올려 두세요." >&2; exit 1
fi

echo "[3/5] 데이터 배치 -> $DATA_DIR"
sudo mkdir -p "$DATA_DIR"
sudo chown "$USER" "$DATA_DIR"
[ -f "$DATA_DIR/bike_system.db" ] || { [ -f "$HOME/bike_system.db" ] && mv "$HOME/bike_system.db" "$DATA_DIR/"; }
[ -d "$DATA_DIR/pp_data" ] || { [ -d "$HOME/pp_data" ] && mv "$HOME/pp_data" "$DATA_DIR/"; }
[ -f "$DATA_DIR/bike_system.db" ] || { echo "DB가 없습니다: $DATA_DIR/bike_system.db" >&2; exit 1; }
[ -d "$DATA_DIR/pp_data" ] || echo "[경고] pp_data가 없습니다. 지도 · CSV 화면이 비어 보입니다." >&2

echo "[4/5] 이미지 빌드 (처음엔 몇 분 걸린다)"
sudo docker build -t "$IMAGE" "$APP_DIR"

echo "[5/5] 컨테이너 실행"
sudo docker rm -f "$NAME" 2>/dev/null || true
# 데이터는 읽기+쓰기로 마운트한다: db.connect()가 WAL 파일을 만들어서 읽기 전용이면 뜨지 않는다.
# 쓰기를 막는 것은 PBR_READONLY=1(미들웨어)이다.
sudo docker run -d --name "$NAME" --restart unless-stopped \
  -p 80:8000 -p 8000:8000 \
  -v "$DATA_DIR":/app/data \
  "$IMAGE"

sleep 5
sudo docker logs --tail 15 "$NAME"
echo
# 80번은 도메인(http://geol.cloud)용이다: 브라우저는 포트가 없으면 80번으로 간다.
# 방화벽 규칙 pbr-web-80이 있어야 밖에서 열린다(docs/구현/CLOUD_DEPLOY.md '도메인 연결').
IP="$(curl -s -H 'Metadata-Flavor: Google' \
  http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip 2>/dev/null || echo '<VM 외부 IP>')"
echo "접속: http://$IP  (포트 80, 도메인용)  또는  http://$IP:8000"
echo "갱신할 때: 데이터를 다시 올린 뒤 'sudo docker restart $NAME', 코드를 바꿨으면 이 스크립트를 다시 실행"
