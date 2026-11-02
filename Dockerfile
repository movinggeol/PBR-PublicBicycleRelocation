# 웹 대시보드(열람 전용) 이미지 - docs/구현/CLOUD_DEPLOY.md
# 데이터(data/)는 이미지에 넣지 않는다. 실행할 때 VM 디스크를 /app/data에 마운트한다.
# 요구 버전(pandas>=3.0.5, numpy>=2.5.2)과 검증 환경이 Python 3.14라서 3.14 이미지를 쓴다.
FROM python:3.14-slim

# 휠이 없는 패키지를 빌드할 때만 필요하다. 필요 없다고 확인되면 이 층을 지워 이미지를 줄인다.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 의존성 층을 먼저 둔다 - 코드만 바뀌면 이 층은 캐시에서 온다
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 코드. data/ · .env · docs/ · experiments/ · tests/ 는 .dockerignore가 막는다
COPY . .

ENV PYTHONUNBUFFERED=1 \
    PBR_READONLY=1 \
    PBR_PORT=8000
EXPOSE 8000

# uvicorn 워커는 1개다 - 작업 상태와 캐시가 프로세스 메모리에 있어 워커를 늘리면 어긋난다
CMD ["python", "-m", "webapp"]
