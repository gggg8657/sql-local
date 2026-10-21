#!/usr/bin/env python3
"""LLM 없이 검증: 안전 필터 · LIMIT 강제 · 실행 · 오류 재생성 경로 · 차트 SVG · CSV.  python3 selftest.py"""
import json
import os
import app

assert os.path.exists(app.DB_URL), "python3 seed.py 먼저"

# 1) 안전 필터
for bad in ("DROP TABLE 인력", "UPDATE 인력 SET 직급='x'", "SELECT 1; SELECT 2", "PRAGMA table_info(인력)", "select * from 인력; drop table 인력",
            "ATTACH DATABASE 'x' AS y", "INSERT INTO 인력 VALUES(1)", "select load_file('/etc/passwd')"):
    try:
        app.sanitize(bad); raise AssertionError("통과하면 안 됨: " + bad)
    except ValueError:
        pass
assert app.sanitize("SELECT 부서, COUNT(*) FROM 인력 GROUP BY 부서").endswith("LIMIT 500")
assert app.sanitize("select * from 인력 limit 5;") == "select * from 인력 limit 5"
assert app.sanitize("-- 주석\nWITH t AS (SELECT 1 AS a) SELECT a FROM t").startswith("WITH")

# 2) 실행 + 잘림
cols, rows, trunc = app.db().query(app.sanitize("SELECT * FROM 집행"))
assert cols[:2] == ["id", "과제id"] and len(rows) == 500 and trunc

# 3) 가짜 LLM: 정상 JSON → bar 차트
calls = []
def fake(system, user, model, on_token=None, **kw):
    calls.append(user)
    return FAKE[min(len(calls) - 1, len(FAKE) - 1)]
app.ollama = fake
FAKE = ['```json\n{"sql": "SELECT 부서, SUM(예산) AS \\"예산합계\\" FROM 연구과제 GROUP BY 부서 ORDER BY 2 DESC", "explanation": "부서별 예산", "chart": {"type": "bar", "x": "부서", "y": "예산합계"}}\n```']
r = app.ask("부서별 예산 합계", [])
assert r["error"] is None and r["columns"] == ["부서", "예산합계"] and r["sql"].endswith("LIMIT 500"), r
assert r["svg"] and "<rect" in r["svg"] and "스키마" in calls[0] or "TABLE" in app.system_prompt()
open(os.path.join(app.WS, "selftest_bar.svg"), "w").write(r["svg"])

# 4) 오류 → 재생성 1회 (잘못된 열 → 고친 SQL), line 차트, 맥락 전달
calls.clear()
FAKE = ['{"sql": "SELECT 월, SUM(없는열) AS \\"합계\\" FROM 집행 GROUP BY 월", "explanation": "x", "chart": {"type": "line", "x": "월", "y": "합계"}}',
        '{"sql": "SELECT 월, SUM(금액) AS \\"합계\\" FROM 집행 WHERE 월 LIKE \'2024-%\' GROUP BY 월 ORDER BY 월", "explanation": "월별 집행", "chart": {"type": "line", "x": "월", "y": "합계"}}']
r2 = app.ask("2024년 월별 집행", [{"question": "부서별 예산 합계", "sql": r["sql"], "head": "부서,예산합계"}])
assert r2["repaired"] and r2["error"] is None and len(r2["rows"]) == 12 and "[실행 오류]" in calls[1] and "[이전 SQL]" in calls[0], (r2["error"], calls)
assert "<path" in r2["svg"] and "<circle" in r2["svg"]

# 5) pie + CSV + 재실행(수동 SQL) + 거부 JSON
calls.clear()
FAKE = ['{"sql": "SELECT 상태, COUNT(*) AS \\"대수\\" FROM 장비 GROUP BY 상태", "explanation": "상태 비중", "chart": {"type": "pie", "x": "상태", "y": "대수"}}']
r3 = app.ask("장비 상태 비중", [])
assert "<path" in r3["svg"] and "%" in r3["svg"]
csv = app.to_csv(r3["columns"], r3["rows"])
assert csv.startswith("﻿상태,대수") and csv.count("\n") == len(r3["rows"]) + 1
r4 = app.rerun("SELECT 직급, COUNT(*) AS n FROM 인력 GROUP BY 직급", {"type": "bar", "x": "직급", "y": "n"})
assert len(r4["rows"]) == 4 and r4["svg"]
FAKE = ['{"sql": null, "explanation": "급여 정보는 스키마에 없음", "chart": {"type":"table","x":null,"y":null}}']
r5 = app.ask("평균 급여는?", [])
assert r5["error"] == "급여 정보는 스키마에 없음" and r5["sql"] is None
# 6) 두 번 연속 실패 → error 유지, 예외 없음
FAKE = ['{"sql": "SELECT 없음 FROM 인력", "explanation": "", "chart": {}}']
r6 = app.ask("x", [])
assert r6["error"] and r6["repaired"]
assert any(x["run_id"] == r3["run_id"] for x in app.list_runs())
# 7) 보안: 접속 문자열 감추기 · 위험 함수 차단 · 스키마 설명 주인 규칙
assert app.db_label("postgresql://u:pw@10.0.0.1:5432/research") == "research" and app.db_label("/a/b/x.db") == "x.db"
_old = app.DB_URL
app.DB_URL = "postgresql://scott:tiger9@dbhost:5432/research"
_m = app.scrub("OperationalError: connection to dbhost failed for user scott (postgresql://scott:tiger9@dbhost:5432/research)")
assert "tiger9" not in _m and "scott" not in _m and "dbhost" not in _m, _m
app.DB_URL = _old
for bad in ("SELECT pg_read_file('/etc/passwd')", "SELECT set_config('default_transaction_read_only','off',false)",
            "WITH x AS (MERGE INTO t USING s ON 1=1 WHEN MATCHED THEN DELETE) SELECT 1", "SELECT * INTO 새표 FROM 인력", "SELECT pg_sleep(100)",
            "SELECT sleep(100)", "SELECT pg_terminate_backend(1)", "SELECT query_to_xml('delete from t', true, true, '')"):
    try:
        app.sanitize(bad); raise AssertionError("통과하면 안 됨: " + bad)
    except ValueError:
        pass
import tempfile
_ws = app.WS
app.WS = tempfile.mkdtemp()
app.save_notes({"인력": {"_": "사람"}}, "alice")
try:
    app.save_notes({"인력": {"_": "바꿈"}}, "bob"); raise AssertionError("남의 설명을 고치면 안 됨")
except PermissionError as e:
    assert e.args[0][0] == 403
app.save_notes({"인력": {"_": "사람"}, "장비": {"_": "기계"}}, "bob")  # 남의 것은 그대로 두고 자기 것 추가 → 허용
assert app.notes_locked("bob") == ["인력"] and app.notes_locked("alice") == ["장비"] and app.notes_locked("owner") == []
app.save_notes({"장비": {"_": "기계"}}, "owner")  # owner 는 지울 수 있음
assert list(app.load_notes()) == ["장비"]
app.WS = _ws

# 브라우저가 떠나도(첫 이벤트 뒤 쓰기가 BrokenPipeError) 질의는 끝까지 — 실행 기록에 남고 서버 오류·트레이스백 없음
import io, sys, tempfile, threading, urllib.request
from http.server import ThreadingHTTPServer
class Cut:
    def __init__(s, f, n): s.f, s.n = f, n
    def write(s, b):
        s.n -= 1
        if s.n < 0: raise BrokenPipeError(32, "Broken pipe")
        return s.f.write(b)
    def flush(s): pass
    def __getattr__(s, k): return getattr(s.f, k)
class CutH(app.H):
    def setup(s): super().setup(); s.wfile = Cut(s.wfile, 2)  # 응답 머리 + 첫 이벤트까지만 닿음
ERRS = []
class Srv(ThreadingHTTPServer):
    def handle_error(s, req, addr): ERRS.append(sys.exc_info()[1])
def cut_post(path, body, user="alice"):
    srv = Srv(("127.0.0.1", 0), CutH); threading.Thread(target=srv.serve_forever, daemon=True).start()
    err0, sys.stderr = sys.stderr, io.StringIO()
    try:
        r = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}{path}", json.dumps(body).encode(), {"Content-Type": "application/json", "X-Agent-User": user})
        got = urllib.request.urlopen(r).read().decode()
    finally:
        cap, sys.stderr = sys.stderr.getvalue(), err0
    srv.shutdown(); srv.server_close()
    assert got.count("data: ") == 1 and not ERRS and "Traceback" not in cap and cap.count("연결 끊김") == 1, (got, ERRS, cap)
    return got
ANS = '{"sql": "SELECT 부서, COUNT(*) AS \\"n\\" FROM 연구과제 GROUP BY 부서", "explanation": "부서별 과제 수", "chart": {"type": "bar", "x": "부서", "y": "n"}}'
def fake2(system, user, model, on_token=None, **kw):
    for part in (ANS[:30], ANS[30:]):
        on_token and on_token(part)
    return ANS
_ws, _ol = app.WS, app.ollama
app.WS, app.ollama = tempfile.mkdtemp(prefix="sql-cut-"), fake2
cut_post("/api/ask", {"question": "떠난 뒤 부서별 과제 수"})
runs = app.list_runs(os.path.join(app.WS, "u", "alice"))
assert len(runs) == 1 and runs[0]["question"] == "떠난 뒤 부서별 과제 수" and runs[0]["error"] is None and runs[0]["n"] > 0, runs
__import__("shutil").rmtree(app.WS); app.WS, app.ollama = _ws, _ol

# 저작권 표기: 서버가 화면에 붙이는 코드가 있어야 한다 (LICENSE·NOTICE)
_src = open(__import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)), "app.py"), encoding="utf-8").read()
assert "wqkgMjAyNiBnZ2dnODY1NyDCtyBkb25nanVraW0uZGV2QGdtYWlsLmNvbQ==" in _src and "signed(" in _src and "X-Author" in _src, "저작권 표기 누락"

print("selftest OK — 안전필터 8건 차단 · LIMIT 강제 · 재생성 · bar/line/pie SVG · CSV · 재실행 · 거부 JSON")
