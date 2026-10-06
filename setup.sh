#!/usr/bin/env bash
# sql-local — 원샷 설치·실행 스크립트 (macOS / Linux / Windows Git Bash)
#
#   bash setup.sh            # 설치 + LLM 서버 탐색/세팅 + 웹 서버 기동 + 브라우저
#   bash setup.sh stop       # 웹 서버 종료
#   curl -fsSL https://raw.githubusercontent.com/gggg8657/sql-local/master/setup.sh | bash
#
# 환경변수로 덮어쓰기:
#   LLM_BASE_URL=http://gpu:8000/v1 LLM_API=openai   이미 있는 서버를 강제 지정 (탐색 생략)
#   MODEL=qwen3:32b                                  Ollama에 모델이 없을 때 pull 할 모델 (기본 qwen3:8b)
#   PORT=8772
set -euo pipefail

REPO=https://github.com/gggg8657/sql-local.git
MODEL="${MODEL:-qwen3:8b}"
PORT="${PORT:-8772}"

# ── 화면 ─────────────────────────────────────────────────────────────────
if [ -t 1 ]; then B=$'\033[1m'; D=$'\033[2m'; C=$'\033[36m'; G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; N=$'\033[0m'; else B= D= C= G= R= Y= N=; fi
STEP=0
banner() {
  printf '%s' "$C"
  cat <<'EOF'

   ███████╗ ██████╗ ██╗
   ██╔════╝██╔═══██╗██║
   ███████╗██║   ██║██║         local
   ╚════██║██║▄▄ ██║██║
   ███████║╚██████╔╝███████╗
   ╚══════╝ ╚══▀▀═╝ ╚══════╝
EOF
  printf '%s  %s자연어 → SQL → 표·차트 · 로컬 설치%s   %s(MIT · stdlib only)%s\n\n' "$N" "$B" "$N" "$D" "$N"
}
step() { STEP=$((STEP+1)); printf '  %s[%d/7]%s %s%s%s  ' "$D" "$STEP" "$N" "$B" "$1" "$N"; }
ok()   { printf '%s✔%s %s\n' "$G" "$N" "${1:-}"; }
skip() { printf '%s–%s %s\n' "$D" "$N" "${1:-}"; }
die()  { printf '%s✘ %s%s\n\n' "$R" "$*" "$N" >&2; exit 1; }
warn() { printf '\n  %s!%s %s\n' "$Y" "$N" "$*"; }
# spin "메시지" cmd args…  — 긴 작업을 스피너와 함께 실행
spin() {
  local msg=$1; shift; local log; log=$(mktemp)
  "$@" >"$log" 2>&1 & local pid=$!
  local f='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏' i=0
  while kill -0 $pid 2>/dev/null; do printf '\r  %s      %s%s%s %s' "$D" "$C" "${f:i%10:1}" "$N" "$msg"; i=$((i+1)); sleep 0.1; done
  printf '\r\033[K'
  if wait $pid; then rm -f "$log"; return 0; else printf '%s' "$D"; tail -5 "$log" | sed 's/^/        /'; printf '%s' "$N"; rm -f "$log"; return 1; fi
}
has()   { command -v "$1" >/dev/null 2>&1; }
probe() { curl -fsS -m 2 "$1" >/dev/null 2>&1; }
wait_for() { for _ in $(seq 1 "${2:-30}"); do probe "$1" && return 0; sleep 1; done; return 1; }
# 포트를 잡고 있는 프로세스 종료 (pid 파일 + lsof/fuser 양쪽)
free_port() {
  { [ -f .server.pid ] && kill "$(cat .server.pid)" 2>/dev/null; } || true; rm -f .server.pid
  if has lsof; then lsof -ti "tcp:$1" 2>/dev/null | xargs kill 2>/dev/null || true
  elif has fuser; then fuser -k "$1/tcp" >/dev/null 2>&1 || true; fi
  sleep 1
}

banner

# ── 1. OS ────────────────────────────────────────────────────────────────
step "OS 감지"
case "$(uname -s)" in
  Darwin*) OS=mac ;; Linux*) OS=linux ;; MINGW*|MSYS*|CYGWIN*) OS=windows ;;
  *) die "지원하지 않는 OS: $(uname -s)" ;;
esac
ok "$OS ($(uname -m))"

# ── 2. 레포 ──────────────────────────────────────────────────────────────
step "패키지 확보"
if [ -f "$(dirname "${BASH_SOURCE[0]:-.}")/app.py" ]; then
  cd "$(dirname "${BASH_SOURCE[0]}")"; ok "이미 있음 → $(pwd)"
elif [ -f ./sql-local/app.py ]; then
  cd sql-local; ok "이미 있음 → $(pwd)"
else
  has git || die "git이 없습니다. 폐쇄망이면 zip을 풀고 그 폴더 안에서 bash setup.sh 를 실행하세요."
  spin "git clone $REPO" git clone -q "$REPO" sql-local || die "클론 실패 (인터넷 연결 확인)"
  cd sql-local; ok "클론 완료 → $(pwd)"
fi

# ── 3. Python ────────────────────────────────────────────────────────────
step "Python 3.9+"
PY=""
for c in python3 python py; do
  if has "$c" && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then PY=$c; break; fi
done
[ -n "$PY" ] || case "$OS" in
  mac)   die "python3가 없습니다: xcode-select --install 또는 brew install python" ;;
  linux) die "python3 (3.9+)가 없습니다: sudo apt install python3 / sudo dnf install python3" ;;
  *)     die "Python 3.9+가 없습니다: winget install Python.Python.3.12 후 터미널을 다시 열고 재실행" ;;
esac
ok "$($PY --version 2>&1) ($PY)"

if [ "${1:-}" = "stop" ]; then
  if probe "http://localhost:$PORT/api/models"; then free_port "$PORT"; ok "웹 서버 종료 (:$PORT)"; else skip "실행 중인 서버 없음"; fi
  exit 0
fi

# ── 4. LLM 서버 ──────────────────────────────────────────────────────────
step "LLM 서버 탐색"
export LLM_API="${LLM_API:-}" LLM_BASE_URL="${LLM_BASE_URL:-}"
if [ -n "$LLM_BASE_URL" ]; then
  [ -n "$LLM_API" ] || { case "$LLM_BASE_URL" in *11434*) LLM_API=ollama ;; *) LLM_API=openai ;; esac; }
  ok "지정됨 → $LLM_API $LLM_BASE_URL"
elif probe http://localhost:11434/api/tags; then
  LLM_API=ollama LLM_BASE_URL=http://localhost:11434; ok "Ollama 실행 중 (:11434)"
else
  for p in 8000 1234 8080; do  # vLLM · LM Studio · llama.cpp
    if probe "http://localhost:$p/v1/models"; then LLM_API=openai LLM_BASE_URL="http://localhost:$p/v1"; ok "OpenAI 호환 서버 실행 중 (:$p)"; break; fi
  done
fi
if [ -z "$LLM_BASE_URL" ]; then
  if has ollama; then skip "서버 없음 → Ollama 기동"
  else
    skip "서버 없음 → Ollama 설치"
    curl -fsS -m 5 https://ollama.com >/dev/null 2>&1 || die "LLM 서버가 없고 인터넷도 없습니다. 서버 담당자에게 주소를 받아  LLM_BASE_URL=http://gpu:8000/v1 bash setup.sh  로 실행하세요."
    case "$OS" in
      mac)     if has brew; then spin "brew install ollama" brew install --quiet ollama
               else spin "Ollama.app 다운로드" bash -c 'curl -fsSL https://ollama.com/download/Ollama-darwin.zip -o /tmp/ollama.zip && unzip -qo /tmp/ollama.zip -d /Applications' && ln -sf /Applications/Ollama.app/Contents/Resources/ollama /usr/local/bin/ollama 2>/dev/null || true; fi ;;
      linux)   spin "ollama.com/install.sh (sudo 필요)" bash -c 'curl -fsSL https://ollama.com/install.sh | sh' ;;
      windows) has winget || die "winget이 없습니다. https://ollama.com/download 에서 설치 후 재실행"
               spin "winget install Ollama" winget install -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements ;;
    esac || die "Ollama 설치 실패"
    has ollama || export PATH="$PATH:/usr/local/bin:/opt/homebrew/bin:${LOCALAPPDATA:-}/Programs/Ollama"
    has ollama || die "설치 후에도 ollama 명령을 찾지 못했습니다. 터미널을 다시 열고 재실행하세요."
    printf '  %s              ✔ Ollama 설치 완료%s\n' "$G" "$N"
  fi
  nohup ollama serve >/dev/null 2>&1 &
  spin "ollama serve 기동 대기" wait_for http://localhost:11434/api/tags 30 || die "Ollama가 뜨지 않습니다. 'ollama serve' 를 직접 실행해 보세요."
  LLM_API=ollama LLM_BASE_URL=http://localhost:11434
  printf '  %s              ✔ Ollama 실행 중 (:11434)%s\n' "$G" "$N"
fi

# ── 5. 모델 ──────────────────────────────────────────────────────────────
step "모델"
if [ "$LLM_API" = ollama ]; then
  if ! curl -fsS "$LLM_BASE_URL/api/tags" | grep -q '"name"'; then
    skip "설치된 모델 없음 → pull (수 GB, 시간 걸림)"
    ollama pull "$MODEL" || die "모델 pull 실패. 폐쇄망이면 모델 파일을 별도 반입해 'ollama create' 하세요."
    printf '  %s              ' "$D"
  fi
  curl -fsS "$LLM_BASE_URL/api/tags" | grep -q "\"name\":\"$MODEL\"" || MODEL=$(curl -fsS "$LLM_BASE_URL/api/tags" | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["models"][0]["name"])')
else
  MODEL=$(curl -fsS "$LLM_BASE_URL/models" | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["data"][0]["id"])')
fi
ok "$MODEL"

# ── 6. 자가검증 ──────────────────────────────────────────────────────────
step "자가검증"
[ -f sample/research.db ] || "$PY" seed.py >/dev/null; spin "안전필터·차트·재생성 검증" env -u WORKSPACE "$PY" selftest.py || die "selftest 실패"
ok "안전필터 · LIMIT · 재생성 · SVG 차트 · CSV 통과"

# ── 7. 웹 서버 ───────────────────────────────────────────────────────────
step "웹 서버"
probe "http://localhost:$PORT/api/models" && free_port "$PORT"
LLM_API=$LLM_API LLM_BASE_URL=$LLM_BASE_URL LLM_MODEL=$MODEL PORT=$PORT nohup "$PY" app.py > server.log 2>&1 &
echo $! > .server.pid
spin "기동 대기 (:$PORT)" wait_for "http://localhost:$PORT/api/models" 20 || { cat server.log; die "웹 서버가 뜨지 않았습니다 (server.log 확인)"; }
URL="http://localhost:$PORT"
ok "$URL"

case "$OS" in mac) open "$URL" ;; linux) has xdg-open && xdg-open "$URL" >/dev/null 2>&1 || true ;; windows) start "$URL" ;; esac
printf '\n  %s┃%s  %s준비 완료%s   브라우저에서  %s%s%s  를 여세요.\n' "$G" "$N" "$B" "$N" "$C" "$URL" "$N"
printf '  %s┃%s  LLM  %s%s%s · %s\n' "$G" "$N" "$D" "$LLM_API" "$N" "$MODEL"
printf '  %s┃%s  종료  bash setup.sh stop     로그  server.log\n\n' "$G" "$N"
