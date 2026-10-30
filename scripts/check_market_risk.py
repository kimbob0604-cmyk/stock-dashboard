"""위험지표·ADR(market_risk.py)을 네트워크 없이 합성 DB 로 돌려 본다.

못 박는 것
  1. 오늘 집계 — 마스터로 시장을 가르고, 마스터에 없는 코드(ETF 등)는 세지 않는다.
  2. 근사 채움 — 일봉 전일 대비로 세고, 종목마다 첫 봉은 안 센다. 덜 찬 날은 버린다.
     스냅샷을 덮지 않는다. 다시 돌려도 늘지 않는다.
  3. ADR — 20일 창이 다 찬 날부터, 상승 합 ÷ 하락 합 × 100.
  4. 화면 한 벌 — 장중 live 를 오늘 행으로 붙이고, 근사 구간 끝 날짜를 밝힌다. 기간 자르기.
  5. 스냅샷 관문 — 덜 센 날·오래된 stocks 는 남기지 않는다.
  6. 이격도 계산 · 네이버/FRED 파싱(깨진 행·'.' 결측 버림) · 갱신(첫 수집 vs 증분, 한쪽 실패 격리).
  7. server.py 배선 — API 두 개·스케줄·부팅 스레드·/risk 경로·백업 표·화면 등록.
"""
import json
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import market_risk as mr                                         # noqa: E402

ok = True
n_checks = 0


def want(cond, why):
    global ok, n_checks
    n_checks += 1
    if not cond:
        ok = False
        print('FAIL —', why)


def new_db():
    c = sqlite3.connect(':memory:')
    c.executescript("""
    CREATE TABLE stocks (code TEXT PRIMARY KEY, name TEXT, market TEXT DEFAULT '',
      change_pct REAL, updated_at TEXT);
    CREATE TABLE ohlcv (code TEXT, date TEXT, open REAL, high REAL, low REAL,
      close REAL, volume REAL, PRIMARY KEY (code, date));
    """)
    mr.ensure_tables(c)
    return c


MARKETS = {'005930': 'KOSPI', '000660': 'KOSPI', '035720': 'KOSPI',
           '247540': 'KOSDAQ', '086520': 'KOSDAQ'}

# ── 1. 오늘 집계 ─────────────────────────────────────────────────────────
db = new_db()
db.executemany('INSERT INTO stocks (code, name, change_pct, updated_at) VALUES (?,?,?,?)', [
    ('005930', 'a', 1.2, '2026-10-30 06:00:00'), ('000660', 'b', -0.5, '2026-10-30 06:00:00'),
    ('035720', 'c', 0.0, '2026-10-30 06:00:00'), ('247540', 'd', 3.0, '2026-10-30 06:00:00'),
    ('086520', 'e', None, '2026-10-30 06:00:00'),            # 등락률 없음 — 안 센다
    ('069500', 'KODEX 200', 2.0, '2026-10-30 06:00:00'),     # 마스터에 없음(ETF) — 안 센다
    ('AAPL', 'apple', 5.0, '2026-10-30 06:00:00'),           # 국내 코드 아님
])
cnt = mr.count_today(db, MARKETS)
want(cnt['KOSPI'] == {'up': 1, 'down': 1, 'flat': 1, 'total': 3}, f'코스피 집계: {cnt}')
want(cnt['KOSDAQ'] == {'up': 1, 'down': 0, 'flat': 0, 'total': 1}, f'코스닥 집계: {cnt}')
want(mr.master_markets(load=lambda: {'005930': {'market': 'KOSPI'}, '1': {'market': 'KONEX'}})
     == {'005930': 'KOSPI'}, '마스터에서 코스피·코스닥 아닌 시장을 걸러 내지 않는다')
want(mr.master_markets(load=lambda: (_ for _ in ()).throw(OSError('x'))) == {},
     '마스터를 못 읽었는데 시장표를 지어냈다')

# ── 5. 스냅샷 관문 ───────────────────────────────────────────────────────
now = datetime(2026, 10, 30, 7, 40, tzinfo=timezone.utc)   # KST 16:40
want(mr.stocks_fresh(db, now), 'stocks 가 1시간 40분 전 갱신인데 오래됐다고 본다')
want(not mr.stocks_fresh(db, now + timedelta(hours=5)), '5시간 넘게 지난 stocks 를 새것으로 본다')
small_ok, why = mr.snapshot_ok(cnt)
want(not small_ok and '종목뿐' in why, f'덜 센 날을 스냅샷으로 남기려 한다: {why}')
big = {'KOSPI': {'up': 300, 'down': 600, 'flat': 40, 'total': 940},
       'KOSDAQ': {'up': 500, 'down': 1200, 'flat': 50, 'total': 1750}}
want(mr.snapshot_ok(big)[0], '충분히 센 날을 거절한다')

# ── 2. 근사 채움 ─────────────────────────────────────────────────────────
db2 = new_db()
days = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(40)]
# 코스피 두 종목: 005930 은 매일 오름, 000660 은 매일 내림. 코스닥 247540 은 짝수 날 오름.
for i, d in enumerate(days):
    db2.execute("INSERT INTO ohlcv VALUES ('005930',?,0,0,0,?,1)", (d, 100 + i))
    db2.execute("INSERT INTO ohlcv VALUES ('000660',?,0,0,0,?,1)", (d, 500 - i))
    db2.execute("INSERT INTO ohlcv VALUES ('247540',?,0,0,0,?,1)", (d, 100 + (i % 2)))
# 마지막 날에만 들어온 종목 — 첫 봉이라 안 센다
db2.execute("INSERT INTO ohlcv VALUES ('035720',?,0,0,0,50,1)", (days[-1],))
# 마스터에 없는 종목 — 안 센다
db2.execute("INSERT INTO ohlcv VALUES ('069500',?,0,0,0,10,1)", (days[5],))
# 이미 스냅샷이 있는 날 — 덮지 않는다
mr.record_snapshot(db2, days[10], {'KOSPI': {'up': 9, 'down': 9, 'flat': 9, 'total': 27},
                                   'KOSDAQ': {'up': 1, 'down': 1, 'flat': 1, 'total': 3}}, 'x')
n = mr.backfill_from_ohlcv(db2, MARKETS, '2026-01-01 00:00:00')
want(n == (len(days) - 1) * 2 - 2, f'근사 채움 행 수 {n} (기대 {(len(days) - 1) * 2 - 2})')
want(db2.execute("SELECT COUNT(*) FROM market_breadth WHERE date = ?", (days[0],)).fetchone()[0] == 0,
     '첫날(전일 종가 없음)을 셌다')
row = db2.execute("SELECT up, down, flat, total, source FROM market_breadth "
                  "WHERE date = ? AND market = 'KOSPI'", (days[5],)).fetchone()
want(tuple(row) == (1, 1, 0, 2, 'ohlcv'), f'근사 하루치가 틀렸다: {tuple(row)}')
snap = db2.execute("SELECT up, source FROM market_breadth WHERE date = ? AND market = 'KOSPI'",
                   (days[10],)).fetchone()
want(tuple(snap) == (9, 'snapshot'), f'근사가 스냅샷을 덮었다: {tuple(snap)}')
want(mr.backfill_from_ohlcv(db2, MARKETS, 'y') == 0, '다시 돌렸더니 행이 또 늘었다')
want(mr.backfill_from_ohlcv(db2, {}, 'y') == 0, '마스터가 비었는데 채웠다')
# 덜 찬 날은 버린다 — 하루만 종목이 1개뿐인 시장
db3 = new_db()
for code in ('005930', '000660', '035720'):
    for i, d in enumerate(days[:5]):
        if code != '005930' and i == 3:
            continue                                   # 셋째 날 코스피 1종목만
        db3.execute("INSERT INTO ohlcv VALUES (?,?,0,0,0,?,1)", (code, d, 100 + i))
mr.backfill_from_ohlcv(db3, MARKETS, 'z')
want(db3.execute("SELECT COUNT(*) FROM market_breadth WHERE date = ?", (days[3],)).fetchone()[0] == 0,
     '종목이 절반도 안 찬 날을 근사로 남겼다')

# ── 3. ADR ───────────────────────────────────────────────────────────────
rows = [(f'd{i:02d}', 3, 1) for i in range(25)]
s = mr.adr_series(rows)
want(len(s) == 25 - mr.ADR_WINDOW + 1, f'창이 덜 찬 날까지 ADR 을 냈다: {len(s)}')
want(s[0]['adr'] == 300.0 and s[0]['up'] == 60 and s[0]['down'] == 20, f'ADR 계산: {s[0]}')
want(mr.adr_series([(f'd{i}', 1, 0) for i in range(20)])[0]['adr'] is None,
     '하락 0 인데 0 으로 나눴다')

# ── 4. 화면 한 벌 ────────────────────────────────────────────────────────
pay = mr.breadth_payload(db2, days[-1], None, '1Y')
k = pay['markets']['KOSPI']
want(k['approx_until'] == days[-1], f"근사 구간 끝 날짜: {k['approx_until']}")
want(k['adr'] is not None and k['series'], '근사 이력으로 ADR 을 못 냈다')
want(k['today']['label'] == '일봉 근사', f"마지막 행이 근사인데 이름표: {k['today']['label']}")
live_day = (date.fromisoformat(days[-1]) + timedelta(days=1)).isoformat()
pay2 = mr.breadth_payload(db2, live_day, big, '1Y', '실시간')
k2 = pay2['markets']['KOSPI']
want(k2['today']['date'] == live_day and k2['today']['label'] == '실시간'
     and k2['today']['up'] == 300, f"장중 값을 오늘 행으로 안 붙였다: {k2['today']}")
want(k2['series'][-1]['date'] == live_day, '장중 값이 ADR 추이에 안 들어갔다')
want(db2.execute("SELECT COUNT(*) FROM market_breadth WHERE date = ?", (live_day,)).fetchone()[0] == 0,
     '화면 조회가 장중 값을 표에 남겼다')
pay3 = mr.breadth_payload(db2, '2027-06-30', None, '6M')
want(pay3['markets']['KOSPI']['series'] == [], '6개월 밖 값을 자르지 않았다')
want(mr.breadth_payload(db2, days[-1], None, 'XX')['period'] == mr.DEFAULT_PERIOD,
     '알 수 없는 기간을 그대로 받았다')

# ── 6. 이격도 · 파싱 · 갱신 ───────────────────────────────────────────────
dp = mr.disparity([('a', 10.0), ('b', 20.0), ('c', 30.0)], 2)
want(dp == [{'date': 'b', 'value': 133.33}, {'date': 'c', 'value': 120.0}], f'이격도: {dp}')
nav = json.dumps([{"localTradedAt": "2026-10-30", "closePrice": "5,713.21"},
                  {"localTradedAt": "2026-10-29", "closePrice": "-"},
                  {"localTradedAt": "", "closePrice": "1"}])
want(mr.parse_naver_index(nav) == [('2026-10-30', 5713.21)], f'네이버 파싱: {mr.parse_naver_index(nav)}')
fred = "observation_date,VIXCLS\n2026-10-27,17.52\n2026-10-28,.\n2026-10-29,16.91\n"
want(mr.parse_fred_csv(fred) == [('2026-10-27', 17.52), ('2026-10-29', 16.91)],
     f'FRED 파싱: {mr.parse_fred_csv(fred)}')
# 페이지 넘김 — 덜 찬 페이지에서 멈춘다
calls = []


def fake_get(url):
    calls.append(url)
    p = int(re.search(r'page=(\d+)', url).group(1))
    n = mr.NAVER_PAGE_SIZE if p == 1 else 3
    base = date(2026, 10, 30) - timedelta(days=(p - 1) * 100)
    return json.dumps([{"localTradedAt": (base - timedelta(days=i)).isoformat(),
                        "closePrice": f"{1000 + i}"} for i in range(n)])


got = mr.fetch_naver_index('KOSPI', 5, get=fake_get)
want(len(calls) == 2 and len(got) == mr.NAVER_PAGE_SIZE + 3, f'페이지 넘김: {len(calls)}회 {len(got)}행')
want(got == sorted(got), '날짜 순으로 안 돌려준다')

db4 = new_db()
seen = {}


def f_index(code, pages):
    seen.setdefault(code, []).append(pages)
    return [((date(2026, 1, 1) + timedelta(days=i)).isoformat(), 100.0 + i) for i in range(80)]


def f_fred(sid, start):
    seen.setdefault(sid, []).append(start)
    if sid == 'T10Y2Y':
        raise OSError('FRED 막힘')
    return [('2026-10-28', 16.9), ('2026-10-29', 17.2)]


r1 = mr.refresh_series(db4, '2026-10-30', fetch_index=f_index, fetch_fred_fn=f_fred)
want(seen['KOSPI'] == [mr.NAVER_FULL_PAGES], f"첫 수집이 전 구간이 아니다: {seen['KOSPI']}")
want(r1['idx:KOSPI'] == 80 and r1['VIX'] == 2, f'갱신 결과: {r1}')
want(str(r1['T10Y2Y']).startswith('실패'), f'한쪽 실패가 결과에 안 남는다: {r1}')
want(seen['VIXCLS'][0] == '2021-10-31', f"FRED 첫 수집 시작일: {seen['VIXCLS'][0]}")
mr.refresh_series(db4, '2026-10-30', fetch_index=f_index, fetch_fred_fn=f_fred)
want(seen['KOSPI'][-1] == 1, '이미 있는데 전 구간을 다시 받는다')
want(seen['VIXCLS'][-1] == '2026-09-29', f"FRED 증분 시작일: {seen['VIXCLS'][-1]}")
rp = mr.risk_payload(db4, '2026-10-30', '1Y', 25)
want(rp['ma'] == 25 and rp['disparity']['KOSPI']['latest'] is not None, f"이격도 화면: {rp['disparity']['KOSPI']}")
want(rp['vix']['latest'] == 17.2 and rp['vix']['change'] == 0.3, f"VIX 화면: {rp['vix']}")
want(rp['t10y2y']['latest'] is None and rp['t10y2y']['series'] == [], '못 받은 금리차를 지어냈다')
want(mr.risk_payload(db4, '2026-10-30', '1Y', 7)['ma'] == mr.DISPARITY_MAS[1], '알 수 없는 이평을 받았다')

# ── 7. server.py 배선 ────────────────────────────────────────────────────
SRC = (ROOT / 'server.py').read_text(encoding='utf-8')
want('@app.route("/api/market/breadth")' in SRC and '@app.route("/api/market/risk")' in SRC,
     'API 경로가 없다')
want(re.search(r'add_job\(_market_risk_refresh, "cron",\s*day_of_week="mon-fri", hour=16, minute=40',
               SRC) is not None, '16:40 갱신 잡이 없다')
want('target=_startup_market_risk' in SRC, '부팅 스레드가 없다')
want('@app.route("/risk")' in SRC, '/risk 경로가 SPA 로 안 열린다')
want('_mr.record_snapshot(' in SRC and '_mr.snapshot_ok(' in SRC and '_mr.stocks_fresh(' in SRC,
     '스냅샷 관문을 거치지 않고 남긴다')
blk = SRC[SRC.index('def api_market_breadth'):SRC.index('def api_market_risk')]
want('record_snapshot' not in blk and 'backfill' not in blk, '조회 API 가 표에 쓴다')
BK = (ROOT / 'db_backup.py').read_text(encoding='utf-8')
want('"market_breadth",' in BK, 'ADR 스냅샷 표가 백업 대상이 아니다 — 재배포하면 사라진다')
HTML = (ROOT / 'index.html').read_text(encoding='utf-8')
PJ = (ROOT / 'static/js/pages.js').read_text(encoding='utf-8')
want('data-page="risk"' in HTML and 'id="risk-view"' in HTML
     and '/static/js/riskpage.js' in HTML, 'index.html 에 위험지표 메뉴·화면·스크립트가 없다')
want(HTML.index('/static/js/riskpage.js') < HTML.index('/static/js/pages.js'),
     'riskpage.js 가 pages.js 보다 늦게 실린다')
want("risk:       () => { renderRiskPage(); }" in PJ and "'page-risk'" in PJ
     and "return '/risk'" in PJ, 'pages.js 에 위험지표 등록·URL 이 없다')

print(f'검사 {n_checks}개')
print('통과' if ok else '실패')
sys.exit(0 if ok else 1)
