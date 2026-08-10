"""pytest 공통 설정: 프로젝트 루트를 import 경로에 넣는다."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
