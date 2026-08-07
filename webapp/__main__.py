"""`python -m webapp` 실행 진입점."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("webapp.app:app", host="127.0.0.1", port=8000)
