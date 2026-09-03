# PBR (공공자전거 재배치) 프로젝트

대전 타슈 공공자전거 재배치 시스템. 목차는 `docs/README.md`, 파이프라인
규약·함정은 `pbr-pipeline` 스킬, 실행·확인은 `pbr-run` 스킬, 커밋 문체는
`commit-message` 스킬을 따른다. **코드를 고치기 전 `pbr-pipeline` 스킬을
먼저 읽어라** — step0~4 파일명 규칙, `now`/`period`/`duration`, DB 이중
기록 등 반드시 알아야 할 함정이 정리돼 있다.

## 항상 지킬 것

- **git**: `git add -A`·`git add .` 금지 — 파일을 이름으로 콕 집어
  스테이징한다. 커밋은 하되 `git push`는 사용자가 명시적으로 요청할 때만
  한다(전역 `~/.claude/CLAUDE.md`와 동일).
- **언어**: 코드 주석·문서·커밋 메시지는 한국어.
- **가상환경**: `.\.venv\Scripts\python.exe`를 쓴다 — 시스템 `python`에는
  의존성이 없다.
- **커밋 금지**: `data/`·`*.csv`·`.env`는 데이터/비밀키다 (`.gitignore`가
  막지만 다시 확인할 것).
- 수정 후에는 `python -m pytest`, 커밋 직전에는
  `python tools/check_consistency.py`로 문서·코드 정합성을 확인한다.
