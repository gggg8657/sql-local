#!/usr/bin/env python3
"""sql-local — 자연어 질문 → 로컬 LLM 이 SQL 생성 → 안전 필터 → 실행 → 표 + SVG 차트. stdlib 만.

  python3 seed.py && python3 app.py                   # http://localhost:8772 (sample/research.db)
  DB_URL=/path/to/file.db python3 app.py              # 다른 sqlite
  DB_URL=postgresql://u:p@host/db python3 app.py      # psycopg 설치 시 (선택)
  DB_URL=mysql://u:p@host/db python3 app.py           # pymysql 설치 시 (선택)
  LLM_API=openai LLM_BASE_URL=http://gpu:8000/v1 LLM_MODEL=Qwen3-32B python3 app.py
  python3 app.py --cli "부서별 과제 예산 합계"

파이프라인: 스키마 요약(캐시) + 사용자 설명 → LLM 1콜(JSON: sql·explanation·chart)
  → 안전 필터(단일 SELECT, 금지어, LIMIT 강제, 10초) → 실행 → 실패 시 오류를 넣어 1회 재생성 → 표·SVG 차트·CSV
"""
import csv
import datetime
import html
import io
import json
import math
import os
import re
import secrets
import sqlite3
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
WS = os.environ.get("WORKSPACE") or os.path.join(ROOT, "_workspace")  # 포털이 AGENT_DATA/<도구> 로 모아 줌
LLM_API = os.environ.get("LLM_API", "ollama")            # ollama | openai (vLLM·LM Studio·llama.cpp·TGI 등)
LLM_BASE = os.environ.get("LLM_BASE_URL", "http://localhost:8000/v1" if LLM_API == "openai" else "http://localhost:11434").rstrip("/")
MODEL = os.environ.get("LLM_MODEL", "qwen3:8b")
LLM_KEY = os.environ.get("LLM_API_KEY", "")
PORT = int(os.environ.get("PORT", "8772"))
NUM_CTX = int(os.environ.get("NUM_CTX", "16384"))
DB_URL = os.environ.get("DB_URL", os.path.join(ROOT, "sample", "research.db"))
DEMO = os.path.abspath(DB_URL) == os.path.join(ROOT, "sample", "research.db")  # seed.py 가 만든 가상 데이터 → 화면에 demoDB 표시
MAX_ROWS = 500
TIMEOUT_S = 10
FORBID = re.compile(r"\b(insert|update|delete|drop|alter|create|replace|truncate|pragma|attach|detach|vacuum|grant|revoke|exec|execute|copy|into\s+outfile|load_file)\b", re.I)


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def write(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)


# ── LLM (kordoc-local 과 동일) ───────────────────────────────────────────
def _clean(out):
    out = re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip()
    out = re.sub(r"^```\w*\s*\n", "", out)
    out = re.sub(r"\n?```\s*$", "", out)
    return out.strip()


def openai_chat(system, user, model, on_token=None):
    body = {"model": model, "stream": True, "temperature": 0.1,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    hdr = {"Content-Type": "application/json", **({"Authorization": f"Bearer {LLM_KEY}"} if LLM_KEY else {})}
    req = urllib.request.Request(LLM_BASE + "/chat/completions", json.dumps(body).encode(), hdr)
    buf = []
    try:
        with urllib.request.urlopen(req, timeout=3600) as r:
            for line in r:
                line = line.decode().strip()
                if not line.startswith("data:") or line == "data: [DONE]":
                    continue
                tok = (json.loads(line[5:])["choices"][0].get("delta") or {}).get("content") or ""
                if tok:
                    buf.append(tok)
                    if on_token:
                        on_token(tok)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"LLM HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")
    return "".join(buf)


def ollama(system, user, model, on_token=None):
    if LLM_API == "openai":
        return openai_chat(system, user, model, on_token)
    body = {"model": model, "stream": True, "think": False,
            "options": {"temperature": 0.1, "num_ctx": NUM_CTX},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    for attempt in (0, 1):
        try:
            req = urllib.request.Request(LLM_BASE + "/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
            buf = []
            with urllib.request.urlopen(req, timeout=3600) as r:
                for line in r:
                    if not line.strip():
                        continue
                    j = json.loads(line)
                    if "error" in j:
                        raise RuntimeError(j["error"])
                    tok = j.get("message", {}).get("content", "")
                    if tok:
                        buf.append(tok)
                        if on_token:
                            on_token(tok)
                    if j.get("done"):
                        break
            return "".join(buf)
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")
            if attempt == 0 and "think" in msg:
                body.pop("think")
                continue
            raise RuntimeError(f"Ollama HTTP {e.code}: {msg[:300]}")


def models():
    if LLM_API == "openai":
        req = urllib.request.Request(LLM_BASE + "/models", headers={"Authorization": f"Bearer {LLM_KEY}"} if LLM_KEY else {})
        with urllib.request.urlopen(req, timeout=10) as r:
            return [m["id"] for m in json.load(r)["data"]]
    with urllib.request.urlopen(LLM_BASE + "/api/tags", timeout=10) as r:
        return [m["name"] for m in json.load(r)["models"]]


# ── DB ──────────────────────────────────────────────────────────────────
class DB:
    """sqlite 기본. postgresql:// mysql:// 은 드라이버가 깔려 있을 때만 (선택)."""

    def __init__(self, url):
        self.url = url
        if url.startswith("postgres"):
            import psycopg  # noqa  (pip install psycopg[binary])
            self.kind, self.dialect = "postgresql", "PostgreSQL. 날짜 함수는 to_char/EXTRACT. 식별자 큰따옴표."
            self._connect = lambda: psycopg.connect(url, autocommit=True)
        elif url.startswith("mysql"):
            import pymysql  # noqa  (pip install pymysql)
            u = urllib.parse.urlparse(url)
            self.kind, self.dialect = "mysql", "MySQL. 식별자는 백틱(`). 날짜 함수는 DATE_FORMAT/YEAR."
            self._connect = lambda: pymysql.connect(host=u.hostname, port=u.port or 3306, user=u.username, password=u.password or "", database=u.path.lstrip("/"))
        else:
            if not os.path.exists(url):
                raise FileNotFoundError(f"sqlite 파일 없음: {url} (python3 seed.py 로 샘플 생성)")
            self.kind, self.dialect = "sqlite", "SQLite. 날짜는 TEXT(YYYY-MM-DD) 라 substr/strftime 사용. 식별자 큰따옴표. ILIKE 없음(LIKE 는 대소문자 무시)."
            self._connect = lambda: sqlite3.connect(f"file:{url}?mode=ro", uri=True, check_same_thread=False)

    def tables(self):
        c = self._connect()
        try:
            if self.kind == "sqlite":
                names = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
                out = []
                for t in names:
                    cols = [(r[1], r[2]) for r in c.execute(f'PRAGMA table_info("{t}")')]
                    fks = [f'{r[3]} → {r[2]}.{r[4]}' for r in c.execute(f'PRAGMA foreign_key_list("{t}")')]
                    sample = c.execute(f'SELECT * FROM "{t}" LIMIT 3').fetchall()
                    out.append({"name": t, "cols": cols, "fks": fks, "sample": sample})
                return out
            cur = c.cursor()
            q = ("SELECT table_name FROM information_schema.tables WHERE table_schema='public'" if self.kind == "postgresql"
                 else "SELECT table_name FROM information_schema.tables WHERE table_schema=DATABASE()")
            cur.execute(q)
            out = []
            qi = '"' if self.kind == "postgresql" else "`"
            for (t,) in cur.fetchall():
                cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name=%s ORDER BY ordinal_position", (t,))
                cols = cur.fetchall()
                cur.execute(f"SELECT * FROM {qi}{t}{qi} LIMIT 3")
                out.append({"name": t, "cols": cols, "fks": [], "sample": cur.fetchall()})
            return out
        finally:
            c.close()

    def query(self, sql):
        """읽기 전용 실행, TIMEOUT_S 초 제한. (columns, rows)"""
        c = self._connect()
        done = threading.Event()
        if self.kind == "sqlite":
            c.set_progress_handler(lambda: 1 if done.is_set() else 0, 10000)
        t = threading.Timer(TIMEOUT_S, done.set)
        t.start()
        try:
            cur = c.cursor()
            cur.execute(sql)
            rows = cur.fetchmany(MAX_ROWS + 1)
            cols = [d[0] for d in cur.description]
            return cols, [list(r) for r in rows[:MAX_ROWS]], len(rows) >= MAX_ROWS  # ponytail: LIMIT 500 강제라 500행이면 "더 있을 수 있음"으로 표시
        except sqlite3.OperationalError as e:
            if "interrupted" in str(e):
                raise RuntimeError(f"쿼리가 {TIMEOUT_S}초를 넘겨 중단됨")
            raise
        finally:
            t.cancel()
            c.close()


def schema_text(db, notes):
    """LLM 에 넣는 압축 스키마. 사용자 설명(notes)이 있으면 테이블/열 옆에 붙인다."""
    lines = [f"[{db.kind}]"]
    for t in db.tables():
        n = notes.get(t["name"], {})
        lines.append(f'TABLE "{t["name"]}"' + (f'  -- {n["_"]}' if n.get("_") else ""))
        for col, typ in t["cols"]:
            lines.append(f'  "{col}" {typ}' + (f'  -- {n[col]}' if n.get(col) else ""))
        for fk in t["fks"]:
            lines.append(f"  FK {fk}")
        for r in t["sample"]:
            lines.append("  예) " + ", ".join(str(v)[:30] for v in r))
    return "\n".join(lines)


# ── 안전 필터 ───────────────────────────────────────────────────────────
def sanitize(sql):
    """단일 SELECT/WITH 만 통과. 금지어·다중문장 차단, LIMIT 강제. 위반 시 ValueError."""
    if not sql or not sql.strip():
        raise ValueError("SQL 없음")
    s = re.sub(r"--[^\n]*|/\*.*?\*/", " ", sql, flags=re.S).strip().rstrip(";").strip()
    if ";" in s:
        raise ValueError("문장 하나만 실행할 수 있습니다")
    if not re.match(r"^\s*(select|with)\b", s, re.I):
        raise ValueError("SELECT/WITH 로 시작하는 조회만 허용")
    m = FORBID.search(s)
    if m:
        raise ValueError(f"허용되지 않는 키워드: {m.group(0)}")
    if not re.search(r"\blimit\s+\d+", s, re.I):
        s += f" LIMIT {MAX_ROWS}"
    return s


# ── SVG 차트 (서버 생성, 라이브러리 0) ──────────────────────────────────
PALETTE = ["#2f6fed", "#e8743b", "#19a979", "#945ecf", "#13a4b4", "#f5a623", "#d0021b", "#7b8794", "#bd10e0", "#50e3c2"]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def chart_svg(kind, cols, rows, x, y):
    """bar/line/pie SVG. x·y 열이 없거나 숫자가 아니면 None (표만)."""
    if kind not in ("bar", "line", "pie") or x not in cols or y not in cols or not rows:
        return None
    xi, yi = cols.index(x), cols.index(y)
    pts = [(str(r[xi]), _num(r[yi])) for r in rows if _num(r[yi]) is not None][:40]
    if not pts:
        return None
    W, H, L, B, T, R = 720, 340, 70, 70, 20, 20
    e = html.escape
    if kind == "pie":
        tot = sum(abs(v) for _, v in pts) or 1
        cx, cy, rad, a0, out = 200, 170, 130, -math.pi / 2, []
        for i, (k, v) in enumerate(pts):
            a1 = a0 + 2 * math.pi * abs(v) / tot
            x0, y0, x1, y1 = cx + rad * math.cos(a0), cy + rad * math.sin(a0), cx + rad * math.cos(a1), cy + rad * math.sin(a1)
            big = 1 if a1 - a0 > math.pi else 0
            out.append(f'<path d="M{cx},{cy} L{x0:.1f},{y0:.1f} A{rad},{rad} 0 {big},1 {x1:.1f},{y1:.1f} Z" fill="{PALETTE[i % 10]}"><title>{e(k)}: {v:g} ({abs(v) / tot:.1%})</title></path>')
            out.append(f'<rect x="370" y="{30 + i * 20}" width="12" height="12" fill="{PALETTE[i % 10]}"/><text x="388" y="{41 + i * 20}" font-size="12">{e(k[:28])} ({abs(v) / tot:.1%})</text>')
            a0 = a1
        return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="sans-serif">' + "".join(out) + "</svg>"
    vals = [v for _, v in pts]
    lo, hi = min(0, min(vals)), max(0, max(vals))
    if hi == lo:
        hi = lo + 1
    pw, ph = W - L - R, H - T - B
    sy = lambda v: T + ph - (v - lo) / (hi - lo) * ph
    out = []
    for i in range(5):  # 눈금
        v = lo + (hi - lo) * i / 4
        out.append(f'<line x1="{L}" y1="{sy(v):.1f}" x2="{W - R}" y2="{sy(v):.1f}" stroke="#eee"/><text x="{L - 6}" y="{sy(v) + 4:.1f}" font-size="11" text-anchor="end">{_fmt(v)}</text>')
    n = len(pts)
    step = pw / n
    for i, (k, v) in enumerate(pts):
        cx = L + step * (i + 0.5)
        if kind == "bar":
            bw = step * 0.6
            y0, y1 = sy(max(v, 0)), sy(min(v, 0))
            out.append(f'<rect x="{cx - bw / 2:.1f}" y="{y0:.1f}" width="{bw:.1f}" height="{max(y1 - y0, 1):.1f}" fill="{PALETTE[0]}"><title>{e(k)}: {v:g}</title></rect>')
        lab = k if len(k) <= 10 else k[:9] + "…"
        rot = f' transform="rotate(-35 {cx:.1f} {H - B + 14})"' if n > 8 else ""
        out.append(f'<text x="{cx:.1f}" y="{H - B + 14}" font-size="11" text-anchor="{"end" if n > 8 else "middle"}"{rot}>{e(lab)}</text>')
    if kind == "line":
        path = " ".join(f'{"M" if i == 0 else "L"}{L + step * (i + 0.5):.1f},{sy(v):.1f}' for i, (_, v) in enumerate(pts))
        out.append(f'<path d="{path}" fill="none" stroke="{PALETTE[0]}" stroke-width="2"/>')
        out += [f'<circle cx="{L + step * (i + 0.5):.1f}" cy="{sy(v):.1f}" r="3.5" fill="{PALETTE[0]}"><title>{e(k)}: {v:g}</title></circle>' for i, (k, v) in enumerate(pts)]
    out.append(f'<line x1="{L}" y1="{sy(0):.1f}" x2="{W - R}" y2="{sy(0):.1f}" stroke="#999"/>')
    out.append(f'<text x="{W / 2}" y="{H - 4}" font-size="12" text-anchor="middle" fill="#555">{e(x)}</text>')
    out.append(f'<text x="14" y="{H / 2}" font-size="12" text-anchor="middle" fill="#555" transform="rotate(-90 14 {H / 2})">{e(y)}</text>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="sans-serif">' + "".join(out) + "</svg>"


def _fmt(v):
    for unit, s in ((1e12, "조"), (1e8, "억"), (1e4, "만")):
        if abs(v) >= unit:
            return f"{v / unit:.3g}{s}"
    return f"{v:g}"


def to_csv(cols, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    w.writerows(rows)
    return "﻿" + buf.getvalue()


# ── 파이프라인 ──────────────────────────────────────────────────────────
_db = None
_schema_cache = {}


def db():
    global _db
    if _db is None:
        _db = DB(DB_URL)
    return _db


def load_notes():
    p = os.path.join(WS, "schema_notes.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def system_prompt():
    notes = load_notes()
    key = json.dumps(notes, sort_keys=True, ensure_ascii=False)
    if key not in _schema_cache:
        _schema_cache.clear()
        _schema_cache[key] = read(os.path.join(ROOT, "goal-prompt.md")).replace("{dialect}", db().dialect).replace("{schema}", schema_text(db(), notes))
    return _schema_cache[key]


def parse_json(txt):
    txt = _clean(txt)
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        raise ValueError("LLM 이 JSON 을 내지 않음: " + txt[:200])
    j = json.loads(m.group(0))
    j.setdefault("chart", {})
    if not isinstance(j["chart"], dict):
        j["chart"] = {}
    return j


def ask(question, history=None, model=MODEL, emit=lambda ev: None):
    """질문 → SQL(JSON) → 필터 → 실행(실패 시 1회 재생성) → 결과 dict."""
    run_id = f"{datetime.date.today()}-{secrets.token_hex(2)}"
    system = system_prompt()
    user = ""
    for h in (history or [])[-3:]:
        user += f"[이전 질문] {h['question']}\n[이전 SQL] {h['sql']}\n[이전 결과 머리] {h.get('head', '')}\n\n"
    user += f"[질문]\n{question}"
    emit({"stage": "llm", "msg": f"{model} SQL 생성"})
    raw = ollama(system, user, model, on_token=lambda t: emit({"token": t}))
    j = parse_json(raw)
    result = {"run_id": run_id, "question": question, "model": model, "explanation": j.get("explanation", ""),
              "chart": j.get("chart", {}), "sql": None, "error": None, "repaired": False, "columns": [], "rows": [], "truncated": False, "svg": None,
              "ts": datetime.datetime.now().isoformat(timespec="seconds")}
    if not j.get("sql"):
        result["error"] = result["explanation"] or "스키마로 답할 수 없는 질문"
        return _save(result)
    for attempt in (0, 1):
        try:
            sql = sanitize(j["sql"])
            emit({"stage": "run", "msg": "쿼리 실행"})
            cols, rows, trunc = db().query(sql)
            result.update(sql=sql, columns=cols, rows=rows, truncated=trunc, error=None)
            break
        except Exception as e:  # noqa — 어떤 실패든 1회 재생성
            result.update(sql=j.get("sql"), error=f"{type(e).__name__}: {e}")
            if attempt == 1:
                break
            emit({"stage": "repair", "msg": f"오류 수정 재생성: {e}"})
            raw = ollama(system, user + f"\n\n[직전 SQL]\n{j['sql']}\n[실행 오류]\n{e}\n위 오류를 고친 JSON 만 다시 출력.", model, on_token=lambda t: emit({"token": t}))
            j = parse_json(raw)
            result["explanation"] = j.get("explanation", result["explanation"])
            result["chart"] = j.get("chart", result["chart"])
            result["repaired"] = True
    c = result["chart"]
    result["svg"] = chart_svg(c.get("type"), result["columns"], result["rows"], c.get("x"), c.get("y")) if result["rows"] else None
    return _save(result)


def rerun(sql, chart=None):
    """사용자가 고친 SQL 재실행 (LLM 0콜)."""
    s = sanitize(sql)
    cols, rows, trunc = db().query(s)
    c = chart or {}
    r = {"run_id": f"{datetime.date.today()}-{secrets.token_hex(2)}", "question": "(수동 SQL)", "model": None, "explanation": "", "chart": c,
         "sql": s, "error": None, "repaired": False, "columns": cols, "rows": rows, "truncated": trunc,
         "svg": chart_svg(c.get("type"), cols, rows, c.get("x"), c.get("y")), "ts": datetime.datetime.now().isoformat(timespec="seconds")}
    return _save(r)


def _save(r):
    write(os.path.join(WS, "runs", r["run_id"] + ".json"), json.dumps(r, ensure_ascii=False, default=str))
    return r


def list_runs():
    d = os.path.join(WS, "runs")
    out = []
    if os.path.isdir(d):
        for f in sorted(os.listdir(d), reverse=True)[:50]:
            try:
                j = json.load(open(os.path.join(d, f), encoding="utf-8"))
                out.append({k: j.get(k) for k in ("run_id", "question", "ts", "error")} | {"n": len(j.get("rows", []))})
            except Exception:
                pass
    return out


# ── HTTP ───────────────────────────────────────────────────────────────
HTML = read(os.path.join(ROOT, "ui.html")) if os.path.exists(os.path.join(ROOT, "ui.html")) else "ui.html 없음"

# ── 저작권 표기 (LICENSE·NOTICE 참고) ─────────────────────────────────────
_SIG = __import__("base64").b64decode("wqkgMjAyNiBnZ2dnODY1NyDCtyBkb25nanVraW0uZGV2QGdtYWlsLmNvbQ==").decode()
_SIG_A = __import__("base64").b64decode("Z2dnZzg2NTcgPGRvbmdqdWtpbS5kZXZAZ21haWwuY29tPg==").decode()


def signed(html):
    """화면에 저작권 표기를 붙인다. ui.html 에서 지워져도 서버가 내보낼 때 다시 붙는다."""
    name, mail = _SIG.split(" · ")
    if 'name="author"' not in html:
        meta = f'<meta name="author" content="{name[7:]} <{mail}>">'
        html = html.replace("<head>", "<head>" + meta, 1) if "<head>" in html else meta + html
    if "data-sig" not in html:
        tag = (f'<!-- {_SIG} --><div data-sig title="{mail}" style="text-align:center;font-size:11px;color:#9aa0a6;'
               f'opacity:.55;margin:28px 0 8px">{name}</div>')
        html = html.replace("</body>", tag + "</body>", 1) if "</body>" in html else html + tag
    return html


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        if "/api/ask" in (a[0] if a else ""):
            super().log_message(fmt, *a)

    def _send(self, body, ctype="application/json", code=200, name=None):
        b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("X-Author", _SIG_A)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        if name:
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{urllib.request.quote(name)}")
        self.end_headers()
        self.wfile.write(b)

    def _json(self):
        return json.loads(self.rfile.read(int(self.headers["Content-Length"])))

    def do_GET(self):
        try:
            if self.path == "/api/models":
                return self._send(models())
            if self.path == "/api/schema":
                return self._send({"db": DB_URL, "demo": DEMO, "kind": db().kind, "tables": [{"name": t["name"], "cols": t["cols"], "fks": t["fks"]} for t in db().tables()], "notes": load_notes()})
            if self.path == "/api/runs":
                return self._send(list_runs())
            m = re.fullmatch(r"/api/runs/([\w-]+)(\.csv)?", self.path)
            if m:
                j = json.load(open(os.path.join(WS, "runs", m.group(1) + ".json"), encoding="utf-8"))
                if m.group(2):
                    return self._send(to_csv(j["columns"], j["rows"]).encode("utf-8"), "text/csv; charset=utf-8", name=m.group(1) + ".csv")
                return self._send(j)
            self._send(signed(HTML.replace("%MODEL%", json.dumps(MODEL))).encode(), "text/html; charset=utf-8")
        except FileNotFoundError:
            self._send({"error": "없음"}, code=404)
        except Exception as e:
            self._send({"error": f"{type(e).__name__}: {e}"}, code=500)

    def do_POST(self):
        try:
            if self.path == "/api/notes":
                write(os.path.join(WS, "schema_notes.json"), json.dumps(self._json(), ensure_ascii=False, indent=1))
                return self._send({"ok": True})
            if self.path == "/api/rerun":
                req = self._json()
                return self._send(rerun(req.get("sql", ""), req.get("chart")))
            if self.path == "/api/ask":
                req = self._json()
                q = (req.get("question") or "").strip()
                if not q:
                    return self._send({"error": "질문이 비어 있음"}, code=400)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()

                def emit(ev):
                    self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n".encode())
                    self.wfile.flush()
                try:
                    emit({"done": ask(q, req.get("history") or [], req.get("model") or MODEL, emit)})
                except Exception as e:
                    emit({"error": f"{type(e).__name__}: {e}"})
                return
            self._send({"error": "없는 경로"}, code=404)
        except Exception as e:
            self._send({"error": f"{type(e).__name__}: {e}"}, code=500)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--cli":
        r = ask(sys.argv[2], emit=lambda ev: print(f"[{ev['stage']}] {ev['msg']}", file=sys.stderr) if "stage" in ev else None)
        print("SQL:", r["sql"])
        print("설명:", r["explanation"])
        if r["error"]:
            print("오류:", r["error"])
            sys.exit(2)
        w = [max(len(str(c)), *(len(str(row[i])) for row in r["rows"][:40])) for i, c in enumerate(r["columns"])]
        print(" | ".join(str(c).ljust(w[i]) for i, c in enumerate(r["columns"])))
        for row in r["rows"][:40]:
            print(" | ".join(str(v).ljust(w[i]) for i, v in enumerate(row)))
        print(f"({len(r['rows'])}행{' · 잘림' if r['truncated'] else ''}, 차트 {r['chart'].get('type')})")
        sys.exit(0)
    db()
    print(f"sql-local → http://localhost:{PORT}  (db={DB_URL}, model={MODEL}, llm={LLM_API} {LLM_BASE})  {_SIG}")
    ThreadingHTTPServer(("", PORT), H).serve_forever()
