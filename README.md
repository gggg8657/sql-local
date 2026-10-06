# sql-local — 자연어로 사내 DB에 질문, SQL·표·차트까지 (로컬 LLM)

> **한 줄 요약** — "부서별 과제 예산 합계 큰 순서로"처럼 한국어로 물으면 로컬 LLM(Ollama·vLLM)이 SQL을 짜고, 코드 레벨 안전 필터(단일 SELECT만, DDL·DML 차단, 500행 제한, 10초 타임아웃)를 통과한 쿼리만 읽기 전용으로 실행해 표와 SVG 차트로 보여주는 도구입니다. 파이썬 표준 라이브러리만 쓰고(sqlite3 내장), 폴더 복사로 배포됩니다. DB-GPT 같은 플랫폼의 경량판으로, 실패하면 오류를 LLM에 되돌려 1회 자동 수정합니다.
>
> - **의존성**: 없음. Python 3.9+. Postgres·MySQL은 드라이버만 pip로 추가.
> - **안전장치**: 쿼리는 LLM이 아니라 코드가 검사. 읽기 전용 연결(sqlite `mode=ro`).
> - **모델**: 한국어 되는 아무거나. SQL 품질은 모델 크기에 비례(8B PoC 통과, 서버에선 30B급 권장).

## 실행

```bash
bash setup.sh                      # OS 판별 → Python → LLM 서버 탐색/세팅 → selftest → http://localhost:8772
python3 seed.py && python3 app.py  # 수동: 샘플 DB 생성 후 기동
DB_URL=/data/ops.db python3 app.py                               # 다른 sqlite
DB_URL=postgresql://user:pw@host/db python3 app.py                # pip install "psycopg[binary]"
DB_URL=mysql://user:pw@host/db python3 app.py                     # pip install pymysql
LLM_API=openai LLM_BASE_URL=http://gpu:8000/v1 LLM_MODEL=Qwen3-32B python3 app.py
python3 app.py --cli "2024년 월별 집행액 추이"                    # CLI
python3 selftest.py                                               # LLM 없이 안전필터·차트·재생성 검증
```

| 환경변수 | 기본 | 설명 |
|---|---|---|
| `LLM_API` | `ollama` | `ollama` 또는 `openai` |
| `LLM_BASE_URL` | `http://localhost:11434` / `http://localhost:8000/v1` | 서버 주소 |
| `LLM_MODEL` | `qwen3:8b` | 기본 모델 (UI에서 변경 가능) |
| `LLM_API_KEY` | (없음) | OpenAI 호환 서버 키 |
| `NUM_CTX` | `16384` | Ollama 컨텍스트 |
| `DB_URL` | `sample/research.db` | sqlite 경로 또는 postgresql:// mysql:// |
| `PORT` | `8772` | |

## 동작

```
질문 ─ 스키마 요약(테이블·열·FK·예시 3행 + 사용자가 UI에서 적은 한국어 설명)
  → LLM 1콜: {"sql", "explanation", "chart":{type,x,y}} (JSON 계약)
  → 안전 필터: SELECT/WITH 단일 문장만 · INSERT/UPDATE/DELETE/DROP/ALTER/PRAGMA/ATTACH 등 차단 · LIMIT 500 강제 · 10초 중단
  → 읽기 전용 실행 → 실패하면 오류 메시지를 넣어 1회 재생성
  → 표(정렬) · SVG 차트(bar/line/pie, 서버 생성) · CSV · SQL 수정 후 재실행(LLM 0콜)
```

- 이전 질문 3개의 SQL·결과 머리를 다음 질문에 넘겨 "그중 2024년만", "그 과제의 논문은?" 같은 후속 질문이 됩니다.
- 왼쪽 스키마 패널에 열 설명(예: `IF` = 저널 영향력 지수)을 적고 저장하면 프롬프트에 들어가 정확도가 올라갑니다. `_workspace/schema_notes.json`.
- **demoDB**(`seed.py`, 기본 연결): 연구과제 15 · 집행 1,512 · 인력 68 · 장비 19 · 논문 31 — 모두 가상 데이터. 기본 DB를 쓰는 동안 화면 제목 옆에 "demoDB · 가상 데이터" 표시가 붙고, `DB_URL`로 다른 DB를 연결하면 사라집니다.

## 폐쇄망
이 폴더를 복사하면 끝. 외부 통신은 LLM 서버 주소 하나뿐. DB는 읽기 전용으로 열며, 운영 DB에 붙일 때는 읽기 전용 계정을 쓰세요.

## 출처·감사 (Credits)

- 파이썬 표준 라이브러리(sqlite3)만 씁니다. 선택 드라이버(psycopg, pymysql)는 운영자가 따로 설치. `sample/research.db` 는 `seed.py` 로 만든 가상 데이터
- **LLM 실행** — OpenAI 호환 API 로 호출합니다(모델 가중치는 동봉하지 않음). 기본 배포는 [Ollama](https://github.com/ollama/ollama) (MIT) 위의 Google [Gemma](https://ai.google.dev/gemma) `gemma4:31b` — 모델 이용 조건은 Gemma 배포처 참고.
- 이 도구는 [agent-page-portal](https://github.com/gggg8657/agent-page-portal) 에 연결해 쓰도록 만들었습니다(단독 실행도 됨).

저작권 표기·전체 목록은 `NOTICE` 를 보세요.
