"""`python -m webapp` 실행 진입점."""
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


if __name__ == "__main__":
    check_dependencies()
    uvicorn.run("webapp.app:app", host="127.0.0.1", port=8000)
