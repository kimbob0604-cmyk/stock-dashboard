#!/usr/bin/env python3
"""시황 신고가 섹션을 **server.py 의 진짜 build_market_summary 로** 돌려 본다.

check_new_high_logic.py · check_newhigh_axes.py 는 newhigh.py 를 직접 시험한다.
이 스크립트는 server.py 가 그 모듈을 제대로 꿰었는지 — 소제목·기준 줄·보류 표기 —
를 임시 DB 위에서 끝까지 확인한다. 네트워크는 쓰지 않는다(상장 이후 이력 조회는
가짜 소스로 바꾼다). test_data_json.py 처럼 SERVER_NO_STARTUP=1 로 import 한다.
"""
import os
import re
import sqlite3
import sys
import tempfile
import datetime as dt
from contextlib import contextmanager

os.environ["SERVER_NO_STARTUP"] = "1"
os.environ["USE_SQLITE"] = "1"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import server                                                    # noqa: E402
import newhigh as nh                                             # noqa: E402
import krx_calendar                                              # noqa: E402
from db.database import _ensure_stocks_columns                   # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


TODAY = server.now_kst().date()
CAL = krx_calendar.Calendar()
W = nh.windows(TODAY.isoformat(), CAL)


def make_db(depth_days):
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    c = sqlite3.connect(path)
    c.executescript(open(os.path.join(ROOT, "db", "schema.sql"), encoding="utf-8").read())
    _ensure_stocks_columns(c)
    days = [d.isoformat() for d in (TODAY - dt.timedelta(days=i)
                                    for i in range(depth_days, 0, -1))
            if CAL.is_trading_day(d)]

    def add(code, name, close, fn):
        c.execute("INSERT INTO stocks (code, name, market, sector, market_cap, "
                  "market_cap_updated, close, change_pct, volume_mn, is_etf) "
                  "VALUES (?,?,'KOSPI','테스트',1e12,?,?,1.0,100,0)",
                  (code, name, TODAY.strftime("%Y%m%d"), close))
        c.executemany("INSERT INTO ohlcv VALUES (?,?,?,?,?,?,?)",
                      [(code, d, fn(d), fn(d), fn(d), fn(d), 1) for d in days])

    add("111111", "상장후최고", 9500, lambda d: 9000)
    add("222222", "이력실패", 9500, lambda d: 20000 if d < W["w52_start"] else 9000)
    add("333333", "백이십일", 9500, lambda d: 20000 if d < W["d120_start"] else 9000)
    add("444444", "평범", 5000, lambda d: 9000)
    c.commit()
    c.close()
    return path


def use_db(path):
    @contextmanager
    def gdb():
        cx = sqlite3.connect(path)
        cx.row_factory = sqlite3.Row
        try:
            yield cx
        finally:
            cx.close()
    server._get_db = gdb


def fake_fetch(code, start, end):
    if code == "222222":
        raise TimeoutError("naver 20s")
    path = CURRENT[0]
    cx = sqlite3.connect(path)
    rows = cx.execute("SELECT code, date, open, high, low, close, volume FROM ohlcv "
                      "WHERE code = ?", (code,)).fetchall()
    cx.close()
    return "naver", rows, []


nh._default_fetch = fake_fetch
CURRENT = [None]


def section(out):
    return next(s for s in out["sections"] if s.get("title") == "🏔 신고가")


print("1. 정상 — 일봉 424일")
CURRENT[0] = make_db(424)
use_db(CURRENT[0])
out = server.build_market_summary(dry_run=True)
sec = section(out)
subs = {s["subtitle"]: s["items"] for s in sec["subsections"]}
print("   ", list(subs))
check("소제목 세 줄 · 역사적/52주/120일",
      list(subs) == ["🏔 역사적 신고가 1종목", "📈 52주 신고가 1종목", "📊 120일 신고가 1종목"],
      str(list(subs)))
check("역사적 = 상장 이후 최고", any("상장후최고" in x for x in subs.get("🏔 역사적 신고가 1종목", [])))
check("이력 실패 종목은 52주에 남는다",
      any("이력실패" in x for x in subs.get("📈 52주 신고가 1종목", [])))
check("120일", any("백이십일" in x for x in subs.get("📊 120일 신고가 1종목", [])))
basis = (sec.get("items") or [""])[0]
print("   ", basis)
check("기준 줄 — 세 창의 뜻", "역사적=상장 이후 · 52주=달력 52주 · 120일=120거래일" in basis)
check("기준 줄 — 특정일 없음", not re.search(r"\d{4}-\d{2}-\d{2}~", basis) and "일봉 " not in basis)
check("기준 줄 — 보류 수", "역사적 판정 보류 1종목" in basis)
check("평범은 없다", "평범" not in str(sec))
cx = sqlite3.connect(CURRENT[0])
cached = [r[0] for r in cx.execute("SELECT code FROM alltime_high")]
cx.close()
check("상장 이후 이력은 후보만 받아 표에 둔다", cached == ["111111"], str(cached))

print("2. 일봉이 얕다 — 60일치뿐")
CURRENT[0] = make_db(60)
use_db(CURRENT[0])
sec = section(server.build_market_summary(dry_run=True))
print("   ", sec.get("error"))
check("120거래일 창을 못 채운다고 밝힌다",
      bool(sec.get("error")) and "120거래일 창" in sec["error"] and not sec["subsections"])

print()
print("전부 통과" if not FAILS else f"실패 {len(FAILS)}: {FAILS}")
sys.exit(1 if FAILS else 0)
