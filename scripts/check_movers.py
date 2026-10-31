"""신고가·등락률 화면(movers.py)을 네트워크 없이 합성 DB 로 돌려 본다.

  1. 모집단 = 신고가와 같다 — ETF·리츠·인프라·스팩·시총 미달·거래 없음은 빠진다.
  2. 시장 필터 — 마스터의 시장으로 가른다(stocks.market 은 '').
  3. 일간 — 상승은 큰 순, 하락은 작은 순, 보합은 어느 쪽에도 없다.
  4. 주간 — 일봉 마지막 날 vs 5거래일 전 종가. 기준일 봉이 없는 종목은 뺀다.
  5. 신고가 목록 — 네트워크 없이(budget_s=0) 부르고, 축 안은 거래대금 순, 시장 필터.
  6. server.py · 화면 배선.
"""
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import movers as mv                                              # noqa: E402
import newhigh as nh                                             # noqa: E402

ok = True
n = 0


def want(cond, why):
    global ok, n
    n += 1
    if not cond:
        ok = False
        print('FAIL —', why)


conn = sqlite3.connect(':memory:')
conn.executescript("""
CREATE TABLE stocks (code TEXT PRIMARY KEY, name TEXT, market TEXT DEFAULT '', sector TEXT,
  market_cap REAL, close REAL, change_pct REAL, volume_mn REAL, is_etf INTEGER DEFAULT 0,
  market_cap_updated TEXT);
CREATE TABLE ohlcv (code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL,
  volume REAL, PRIMARY KEY (code, date));
""")
CAP = 1e11
rows = [
    # code, name, cap, close, chg, vol, etf
    ('005930', '삼성전자', 3e14, 70000, 2.5, 900000, 0),
    ('000660', 'SK하이닉스', 2e14, 200000, -1.5, 800000, 0),
    ('247540', '에코프로비엠', 2e13, 150000, 7.0, 500000, 0),
    ('086520', '에코프로', 1.5e13, 90000, -4.0, 400000, 0),
    ('035720', '카카오', 2e13, 40000, 0.0, 300000, 0),            # 보합
    ('069500', 'KODEX 200', 9e12, 40000, 3.0, 999999, 1),        # ETF
    ('094800', '맵스리얼티', 5e11, 5000, 9.0, 1000, 0),           # 리츠(마스터)
    ('900001', '대신밸런스제99호스팩', 2e11, 2100, 29.9, 1000, 0),   # 스팩
    ('111111', '작은회사', 5e10, 3000, 15.0, 1000, 0),            # 시총 미달
    ('222222', '거래없음', 5e11, 3000, 5.0, 0, 0),                # 거래 없음
]
conn.executemany("INSERT INTO stocks (code, name, market_cap, close, change_pct, volume_mn, is_etf, "
                 "sector) VALUES (?,?,?,?,?,?,?,'테스트')", rows)
MARKETS = {'005930': 'KOSPI', '000660': 'KOSPI', '035720': 'KOSPI',
           '247540': 'KOSDAQ', '086520': 'KOSDAQ', '094800': 'KOSPI'}

# ── 1·2 ──
uni = mv.universe(conn, CAP)
codes = sorted(r['code'] for r in uni)
want(codes == ['000660', '005930', '035720', '086520', '247540'], f'모집단이 신고가와 다르다: {codes}')
kq = mv.tag_market(uni, MARKETS, 'KOSDAQ')
want(sorted(r['code'] for r in kq) == ['086520', '247540'] and all(r['market'] == 'KOSDAQ' for r in kq),
     f'코스닥 필터: {[r["code"] for r in kq]}')
want(len(mv.tag_market(uni, MARKETS, 'ALL')) == 5, '전체가 전부가 아니다')

# ── 3 ──
d = mv.daily_movers(mv.tag_market(uni, MARKETS, 'ALL'))
want([x['code'] for x in d['up']] == ['247540', '005930'], f"상승 순서: {[x['code'] for x in d['up']]}")
want([x['code'] for x in d['down']] == ['086520', '000660'], f"하락 순서: {[x['code'] for x in d['down']]}")
want(d['n_up'] == 2 and d['n_down'] == 2, '보합을 어느 쪽에 셌다')
want(d['up'][0]['market'] == 'KOSDAQ' and 'is_etf' not in d['up'][0], f"행 모양: {d['up'][0]}")

# ── 4 ── 2026-10-30(금) 기준 5거래일 전 = 2026-10-23(금)
days = ['2026-10-22', '2026-10-23', '2026-10-26', '2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30']
for code, a, b in (('005930', 60000, 66000), ('000660', 200000, 180000), ('247540', 100000, 150000)):
    for dd in days:
        c = a if dd <= '2026-10-23' else b
        conn.execute("INSERT INTO ohlcv VALUES (?,?,0,0,0,?,1)", (code, dd, c))
conn.execute("INSERT INTO ohlcv VALUES ('086520','2026-10-30',0,0,0,90000,1)")   # 기준일 봉 없음
w = mv.weekly_movers(conn, uni, '2026-10-30')
want(w['asof'] == '2026-10-30' and w['base'] == '2026-10-23', f"주간 기준일: {w['asof']} vs {w['base']}")
want([x['code'] for x in w['up']] == ['247540', '005930'], f"주간 상승: {[(x['code'], x['change_pct']) for x in w['up']]}")
want(w['up'][0]['change_pct'] == 50.0 and w['up'][0]['close'] == 150000, f"주간 값: {w['up'][0]}")
want([x['code'] for x in w['down']] == ['000660'] and w['down'][0]['change_pct'] == -10.0,
     f"주간 하락: {w['down']}")
want(w['scanned'] == 3, f"기준일 봉 없는 종목을 셌다: {w['scanned']}")
w0 = mv.weekly_movers(conn, uni, '2020-01-01')
want(w0['up'] == [] and w0['asof'] is None, '일봉이 없는 날인데 주간을 지어냈다')

# ── 5 ──
calls = []


def fake_compute(c, today, min_cap, budget_s=None, **kw):
    calls.append(budget_s)
    r = lambda code, vol: {'code': code, 'name': code, 'sector': 's', 'close': 1, 'change_pct': 1,
                           'volume_mn': vol, 'market_cap': 1e12}
    return {'error': None, 'scanned': 5, 'universe_n': 5, 'hist_pending': [('x', 'y', 'z')],
            'last_bar': '2026-10-29',
            'buckets': {'hist': [], 'w52': [r('005930', 1), r('247540', 9)],
                        'd120': [r('000660', 5)], 'd60': []}}


nl = mv.newhigh_list(conn, '2026-10-30', CAP, MARKETS, 'ALL', compute=fake_compute)
want(calls == [0], f'신고가를 네트워크 예산을 주고 불렀다: {calls}')
want([a['key'] for a in nl['axes']] == [k for k, _, _ in nh.AXES], '축 순서가 newhigh.AXES 와 다르다')
w52 = nl['axes'][1]['items']
want([x['code'] for x in w52] == ['247540', '005930'], f'축 안 거래대금 순: {[x["code"] for x in w52]}')
want(nl['hist_pending'] == 1, '역사적 보류 수를 안 넘긴다')
nk = mv.newhigh_list(conn, '2026-10-30', CAP, MARKETS, 'KOSPI', compute=fake_compute)
want([x['code'] for x in nk['axes'][1]['items']] == ['005930'], '신고가 시장 필터')
ne = mv.newhigh_list(conn, '2026-10-30', CAP, MARKETS, 'ALL',
                     compute=lambda *a, **k: {'error': '일봉 없음', 'buckets': {}})
want(ne['error'] == '일봉 없음' and ne['axes'] == [], '판정 실패를 빈 목록인 척 넘긴다')

# 판정일 — 주말·개장 전엔 직전 거래일. 토요일을 판정일로 두면 금요일 봉이 고점에 들어가
# 4축이 모두 0종목이 된다(2026-10-31 운영)
from datetime import datetime as _dt                             # noqa: E402
for when, want_d in (('2026-10-31 10:00', '2026-10-30'),   # 토
                     ('2026-11-01 23:00', '2026-10-30'),   # 일
                     ('2026-11-02 08:30', '2026-10-30'),   # 월 개장 전
                     ('2026-11-02 09:00', '2026-11-02'),   # 월 개장
                     ('2026-10-30 20:00', '2026-10-30')):  # 금 장 마감 뒤
    got = mv.judge_date(_dt.strptime(when, '%Y-%m-%d %H:%M'))
    want(got == want_d, f'판정일 {when} → {got} (기대 {want_d})')

# ── 6 ──
SRC = (ROOT / 'server.py').read_text(encoding='utf-8')
want('@app.route("/api/movers")' in SRC and '@app.route("/movers")' in SRC, 'API·경로가 없다')
blk = SRC[SRC.index('def api_movers'):SRC.index('@app.route("/api/market/breadth")')]
want('budget_s' not in blk or 'budget_s=0' in blk, 'API 가 신고가 이력을 네트워크로 받을 수 있다')
want('_MOVERS_TTL_S' in blk, 'API 캐시가 없다')
want('_mv.judge_date(now)' in blk, 'API 가 신고가 판정일을 달력 날짜로 잡는다(주말엔 0종목이 된다)')
HTML = (ROOT / 'index.html').read_text(encoding='utf-8')
PJ = (ROOT / 'static/js/pages.js').read_text(encoding='utf-8')
want('data-page="movers"' in HTML and 'id="movers-view"' in HTML
     and HTML.index('/static/js/moverspage.js') < HTML.index('/static/js/pages.js'),
     'index.html 등록이 빠졌다')
want("movers:     () => { renderMoversPage(); }" in PJ and "movers: '/movers'" in PJ
     and "'page-movers'" in PJ, 'pages.js 등록이 빠졌다')

print(f'검사 {n}개')
print('통과' if ok else '실패')
sys.exit(0 if ok else 1)
