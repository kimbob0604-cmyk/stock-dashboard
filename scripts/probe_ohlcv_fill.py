#!/usr/bin/env python3
"""
실제 수집을 러너에서 한 번 돌려 본다. `ohlcv_autofill.fill()` 을 그대로 쓴다.

개발 환경은 네이버가 조직 프록시에 막혀 있어 이 검증을 못 한다. 러너에서
**진짜 응답으로** 돌려, 몇 종목 × 몇 행이 실제로 DB 에 들어가는지 세고
신고가 쿼리가 그 데이터로 성립하는지까지 확인한다.

임시 DB 에 쓴다 — 레포의 db/dashboard.db 를 건드리지 않는다.
"""
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ohlcv_autofill as oa                                      # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 15

# 거래대금 상위를 흉내 낼 유니버스가 없으므로, 네이버 시총 목록에서 상위 N 을
# 받아 그대로 쓴다. 대상 선정 로직(universe_codes)은 별도 시험이 덮는다.
import json                                                       # noqa: E402
import urllib.request                                             # noqa: E402

UA = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.naver.com/'}
req = urllib.request.Request(
    f'https://m.stock.naver.com/api/stocks/marketValue/KOSPI?page=1&pageSize={N}',
    headers=UA)
with urllib.request.urlopen(req, timeout=20) as r:
    js = json.loads(r.read().decode('utf-8'))
codes = [s.get('itemCode') for s in (js.get('stocks') or [])][:N]
names = {s.get('itemCode'): s.get('stockName') for s in (js.get('stocks') or [])}
print(f'대상 {len(codes)}종목: {", ".join(names.get(c, c) for c in codes[:6])} …')

with tempfile.TemporaryDirectory() as td:
    db = Path(td) / 'probe.db'
    cx = sqlite3.connect(db)
    cx.executescript("""
        CREATE TABLE ohlcv (code TEXT NOT NULL, date TEXT NOT NULL,
          open REAL, high REAL, low REAL, close REAL, volume REAL,
          PRIMARY KEY (code, date)) WITHOUT ROWID;""")
    cx.commit(); cx.close()
    oa.DB_PATH = db

    res = oa.fill(codes=codes, now=datetime.now())

    print()
    print('── 수집 결과')
    print(f"  성공 {res['ok']}/{res['codes']}종목 · 실패 {res['failed']}")
    print(f"  저장 {res['rows']:,}행 · {res['elapsed']}초 "
          f"(종목당 {res['elapsed'] / max(res['codes'], 1):.2f}초)")
    print(f"  소스별: {res['by_source']}")
    print(f"  구간 요청: {res['start']} ~ {res['end']}")
    if res['errors']:
        print(f"  오류 표본: {res['errors'][:3]}")
    st = res.get('status') or {}
    print(f"  테이블: {st.get('rows', 0):,}행 / {st.get('codes', 0)}종목 "
          f"· {st.get('first')} ~ {st.get('last')}")

    # 신고가 쿼리가 이 데이터로 성립하는가 — 거래일이 60일 넘게 잡히는지.
    cx = sqlite3.connect(db)
    today = datetime.now().strftime('%Y-%m-%d')
    days = [r[0] for r in cx.execute(
        "SELECT DISTINCT date FROM ohlcv WHERE code GLOB "
        "'[0-9][0-9][0-9][0-9][0-9][0-9]' AND date < ? "
        "ORDER BY date DESC LIMIT 252", (today,)).fetchall()]
    sample = cx.execute(
        "SELECT code, date, close FROM ohlcv ORDER BY date DESC LIMIT 3").fetchall()
    cx.close()

    print()
    print('── 신고가 쿼리 성립 여부')
    print(f"  거래일 {len(days)}일 (60일 이상이어야 섹션이 산다)")
    if days:
        print(f"  최근 {days[0]} · 252번째 {days[-1]}")
    print(f"  표본: {sample}")
    good = len(days) >= 60 and res['ok'] > 0
    print()
    print('판정:', '성립 — 신고가 섹션이 채워진다' if good
          else '불성립 — 이 데이터로는 섹션이 빈다')
    sys.exit(0 if good else 1)
