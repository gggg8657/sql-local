# 제3자 구성요소 (Third-party notices) — sql-local

조사일 2026-10-12. 실제 파일(`setup.sh`, `app.py`·`seed.py` import, `ui.html`, `sample/`) 기준. 라이선스는 각 프로젝트의 LICENSE / 모델 카드에서 확인했다.
이 표는 고지용 정리이며 법률 자문이 아니다. 상업 이용 전에는 원문을 다시 확인할 것.

## 동봉 (저장소에 들어 있음)

제3자 코드·글꼴·아이콘 동봉 없음. 차트는 `app.py` 가 SVG 로 직접 그린다.

| 이름 | 쓰임 | 권리 | 비고 |
|---|---|---|---|
| `sample/research.db` | 예시 DB | 이 저장소 원저작물 | `seed.py` 로 만든 가상 데이터 (실제 기관·인물 정보 아님) |

## 실행 환경 (동봉 안 함)

| 이름 | 버전 | 쓰임 | 라이선스 | 링크 | 상업 이용 | 비고 |
|---|---|---|---|---|---|---|
| Python 표준 라이브러리 (sqlite3 포함) | 3.9+ | 서버 | PSF-2.0 (SQLite 는 Public Domain) | https://docs.python.org/3/license.html | 가능 | |
| psycopg | 미설치(선택) | PostgreSQL 연결 시에만 `import` — 운영자가 직접 설치 | LGPL-3.0 | https://github.com/psycopg/psycopg | 조건부 | 수정 없이 라이브러리로 쓰는 것은 일반적으로 가능. 함께 재배포하면 LGPL 고지 필요 |
| PyMySQL | 미설치(선택) | MySQL 연결 시에만 `import` — 운영자가 직접 설치 | MIT | https://github.com/PyMySQL/PyMySQL | 가능 | |
| Ollama | 설치 시점 최신 | `setup.sh` 가 없으면 설치 — LLM 실행기 | MIT | https://github.com/ollama/ollama | 가능 | |

## 모델 가중치 (저장소에 없음 — 사용자가 내려받음)

| 이름 | 쓰임 | 라이선스 | 링크 | 상업 이용 | 비고 |
|---|---|---|---|---|---|
| Qwen3-8B (`qwen3:8b`) | `setup.sh` 기본 pull 모델 | Apache-2.0 | https://huggingface.co/Qwen/Qwen3-8B | 가능 | `MODEL=` 로 변경 |
| Gemma 4 31B (`gemma4:31b`) | 포털 배포 기본 모델 | Apache-2.0 | https://ai.google.dev/gemma/docs/gemma_4_license | 가능 | |

## 확인 필요

없음 (2026-10-12 조사 기준). psycopg(LGPL-3.0)는 선택 드라이버로 이 저장소에 들어 있지 않다.

## 이 저장소의 원저작물

`app.py`, `ui.html`, `goal-prompt.md`, `seed.py`, `selftest.py`, `setup.sh`, `README.md`, `sample/research.db` — DongJu Kim (gggg8657), MIT (`LICENSE`).
