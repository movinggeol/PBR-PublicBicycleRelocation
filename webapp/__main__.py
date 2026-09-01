"""`python -m webapp` 실행 진입점."""
import socket
import sys

import uvicorn


def check_dependencies() -> None:
    """서버를 띄우기 전에 런타임 의존성을 확인한다.

    빠진 게 있으면 **페이지마다 500이 나는 대신** 지금 멈춘다 —
    .venv가 아닌 시스템 python으로 실행하면 실제로 겪는 상황이다.
    """
    missing = []
    for module, why in (("holidays", "평일/휴일 판정"),
                        ("pandas", "산출물 조회"),
                        ("jinja2", "화면 렌더링")):
        try:
            __import__(module)
        except ImportError:
            missing.append(f"{module} ({why})")

    if missing:
        print("실행에 필요한 패키지가 없습니다: " + ", ".join(missing), file=sys.stderr)
        print(r"  가상환경으로 실행:  .\.venv\Scripts\python.exe -m webapp", file=sys.stderr)
        print("  또는 지금 환경에 설치:  python -m pip install -r requirements.txt",
              file=sys.stderr)
        raise SystemExit(1)


def _lan_ip() -> str | None:
    """같은 와이파이에서 이 PC로 접속할 주소를 알려준다.

    실제로 패킷을 보내지는 않는다 — UDP 소켓을 그 목적지로 "연결"만 해 두면
    OS가 라우팅에 쓸 로컬 인터페이스의 IP를 알려준다(8.8.8.8은 도달하지 않아도 된다).
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None


if __name__ == "__main__":
    check_dependencies()
    lan_ip = _lan_ip()
    if lan_ip:
        print(f"같은 와이파이 기기에서: http://{lan_ip}:8000"
              "  (인증이 없습니다. 신뢰하는 네트워크에서만 쓰세요)")
    uvicorn.run("webapp.app:app", host="0.0.0.0", port=8000)
