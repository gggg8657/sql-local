#!/usr/bin/env python3
"""sample/research.db 더미 데이터 생성 (연구기관 느낌의 한국어 데이터). python3 seed.py"""
import datetime
import os
import random
import sqlite3

random.seed(7)
ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, "sample", "research.db")
DEPTS = ["원자로안전연구부", "핵연료개발부", "방사선응용연구부", "계측제어연구부", "AI데이터연구부", "경영지원부"]
RANK = ["연구원", "선임연구원", "책임연구원", "수석연구원"]
LAST = "김이박최정강조윤장임한오서신권황안송류홍"
FIRST = ["민준", "서연", "도윤", "지우", "하은", "예준", "수아", "시우", "지호", "하린", "준서", "유진", "현우", "서현", "지민", "태양", "은우", "채원", "승민", "다은"]
TOPICS = ["소형모듈원자로 안전해석", "사고저항성 핵연료 피복관", "방사성폐기물 분류 자동화", "디지털 트윈 기반 계측 진단", "중성자 영상 AI 복원",
          "열수력 해석 코드 고도화", "방사선 식품 조사 기술", "노심 설계 최적화", "원전 사이버보안 탐지", "핵종 분석 분광 데이터셋",
          "고온가스로 재료 평가", "해체 로봇 원격 조작", "LLM 기반 기술문서 검색", "피동안전계통 실험", "동위원소 생산 공정"]
ITEMS = ["인건비", "재료비", "장비비", "외주용역", "출장비", "간접비"]
JOURNALS = [("Nuclear Engineering and Design", 2.1), ("Annals of Nuclear Energy", 2.3), ("Journal of Nuclear Materials", 3.1),
            ("Nuclear Engineering and Technology", 2.6), ("Applied Radiation and Isotopes", 1.6), ("IEEE Transactions on Nuclear Science", 1.7)]
EQUIP = ["열수력 실험루프", "SEM 전자현미경", "감마 분광기", "고온 인장시험기", "GPU 서버", "중성자 조사장치", "유도결합 플라즈마", "X선 회절기", "진공 열처리로", "3D 프린터"]
STATUS = ["정상", "정상", "정상", "점검중", "고장", "폐기예정"]


def name():
    return random.choice(LAST) + random.choice(FIRST)


def d(y0, y1):
    return datetime.date(random.randint(y0, y1), random.randint(1, 12), random.randint(1, 28)).isoformat()


os.makedirs(os.path.dirname(DB), exist_ok=True)
if os.path.exists(DB):
    os.remove(DB)
c = sqlite3.connect(DB)
c.executescript("""
CREATE TABLE 인력(id INTEGER PRIMARY KEY, 이름 TEXT, 부서 TEXT, 직급 TEXT, 입사일 TEXT);
CREATE TABLE 연구과제(id INTEGER PRIMARY KEY, 과제명 TEXT, 부서 TEXT, 책임자 TEXT, 시작일 TEXT, 종료일 TEXT, 예산 INTEGER);
CREATE TABLE 집행(id INTEGER PRIMARY KEY, 과제id INTEGER REFERENCES 연구과제(id), 월 TEXT, 항목 TEXT, 금액 INTEGER);
CREATE TABLE 장비(id INTEGER PRIMARY KEY, 장비명 TEXT, 부서 TEXT, 취득일 TEXT, 금액 INTEGER, 상태 TEXT);
CREATE TABLE 논문(id INTEGER PRIMARY KEY, 과제id INTEGER REFERENCES 연구과제(id), 제목 TEXT, 연도 INTEGER, 저널 TEXT, IF REAL);
""")
people = [(name(), random.choice(DEPTS[:-1]), random.choices(RANK, [4, 3, 2, 1])[0], d(2005, 2025)) for _ in range(60)]
people += [(name(), "경영지원부", random.choice(RANK[:2]), d(2008, 2025)) for _ in range(8)]
c.executemany("INSERT INTO 인력(이름,부서,직급,입사일) VALUES(?,?,?,?)", people)
leads = [p for p in people if p[2] in ("책임연구원", "수석연구원")]
projects = []
for i, t in enumerate(TOPICS):
    lead = random.choice(leads)
    y = random.randint(2021, 2025)
    projects.append((t, lead[1], lead[0], f"{y}-01-01", f"{y + random.randint(1, 3)}-12-31", random.randint(3, 40) * 100_000_000))
c.executemany("INSERT INTO 연구과제(과제명,부서,책임자,시작일,종료일,예산) VALUES(?,?,?,?,?,?)", projects)
rows = []
for pid, p in enumerate(projects, 1):
    y0, y1 = int(p[3][:4]), min(int(p[4][:4]), 2026)
    monthly = p[5] / ((y1 - y0 + 1) * 12)
    for y in range(y0, y1 + 1):
        for m in range(1, 13):
            if (y, m) > (2026, 9):
                break
            for it in random.sample(ITEMS, 3):
                rows.append((pid, f"{y}-{m:02d}", it, int(monthly * random.uniform(0.1, 0.6))))
c.executemany("INSERT INTO 집행(과제id,월,항목,금액) VALUES(?,?,?,?)", rows)
c.executemany("INSERT INTO 장비(장비명,부서,취득일,금액,상태) VALUES(?,?,?,?,?)",
              [(e + ("" if k == 0 else f" #{k + 1}"), random.choice(DEPTS[:-1]), d(2012, 2025), random.randint(5, 300) * 10_000_000, random.choice(STATUS))
               for e in EQUIP for k in range(random.randint(1, 3))])
papers = []
for pid, p in enumerate(projects, 1):
    for _ in range(random.randint(0, 5)):
        j = random.choice(JOURNALS)
        papers.append((pid, f"{p[0]}에 관한 연구 ({random.choice(['실험', '해석', '검증', '설계'])})", random.randint(int(p[3][:4]), 2026), j[0], round(j[1] + random.uniform(-0.3, 0.5), 2)))
c.executemany("INSERT INTO 논문(과제id,제목,연도,저널,IF) VALUES(?,?,?,?,?)", papers)
c.commit()
print(f"{DB}: 인력 {len(people)} · 과제 {len(projects)} · 집행 {len(rows)} · 장비 {c.execute('select count(*) from 장비').fetchone()[0]} · 논문 {len(papers)}")
