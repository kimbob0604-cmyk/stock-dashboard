"""신고가 3축(역사적 · 52주 · 120일) 판정을 합성 DB 로 돌려 본다.

server.py 는 Flask 앱이라 import 하지 않는다. 판정은 newhigh.py 로 옮겼으므로
**그 모듈을 그대로** 합성 DB 위에 태우고, server.py 가 그 모듈을 쓰는지는
소스에서 확인한다(예전처럼 쿼리를 정규식으로 떼어 오지 않는다 — 떼어 온 쿼리와
실제 코드가 갈라질 틈이 없다).

정의(2026-10-08): 120일 = 120 거래일(KRX 달력), 52주 = 달력 52주(오늘 − 364일
≤ 날짜 < 오늘), 역사적 = 상장 이후 전체. 종가 기준. hist > w52 > d120.

모집단은 ETF 를 뺀 **시총 1,000억 이상**이다(ohlcv_autofill.MIN_MARKET_CAP_WON,
원 단위). 모집단 수(universe_n)를 같은 조건으로 따로 센다 — 일봉이 없어 못 본
종목 수를 머리말에 적기 위해서다.
"""
import re
import sqlite3
import sys
import datetime as dt

import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import krx_calendar                                              # noqa: E402
import newhigh as nh                                             # noqa: E402
import ohlcv_autofill as oa                                      # noqa: E402

SRC = open(f'{ROOT}/server.py', encoding='utf-8').read()
MIN_CAP = oa.MIN_MARKET_CAP_WON
assert MIN_CAP == 100_000_000_000, f'시총 하한이 1,000억(원)이 아니다: {MIN_CAP}'
assert 'COALESCE(s.market_cap, 0) >= :min_cap' in nh.UNIVERSE_WHERE, '신고가 모집단에 시총 하한이 없다'
assert nh.UNIVERSE_WHERE in nh.SCAN_SQL and nh.UNIVERSE_WHERE in nh.UNIVERSE_SQL, \
    '판정 쿼리와 모집단 수 쿼리의 조건이 갈라졌다'

# 기준을 못 박는다. 과거 고가와 견주면 기준이 섞이고(오늘은 종가, 과거는 장중
# 고가), 오늘 고가까지 보면 장중에 잠깐 뚫고 하락 마감한 날도 신고가가 된다.
assert 'MAX(o.close)' in nh.SCAN_SQL and 'THEN o.close END' in nh.SCAN_SQL, \
    '신고가 창 쿼리가 종가 기준이 아니다'
assert 'o.high' not in nh.SCAN_SQL, '고가가 아직 판정에 쓰인다'
assert 'o.date < :today' in nh.SCAN_SQL, '오늘 봉이 창에 섞인다'
# 비교는 엄격히 '>' — 같은 가격은 신고가가 아니다(ETF-Traker 원본과 같음).
import inspect                                                   # noqa: E402
_src_nh = inspect.getsource(nh)
assert 'out[key] = c > h' in _src_nh and 'r["close"] > hist_max' in _src_nh, \
    "신고가 비교가 엄격한 '>' 가 아니다"
assert 'c >= h' not in _src_nh and '>= hist_max' not in _src_nh, "'>=' 비교가 남아 있다"

# server.py 가 이 모듈로 판정한다 — 옛 '특정일' 경로가 남아 있지 않다.
blk = SRC[SRC.index('# ── 5-2. 신고가'):SRC.index('# ── 6. 수급')]
assert '_nh.compute(' in blk, 'server.py 시황이 newhigh.compute 를 안 쓴다'
assert 'first_day' not in blk and '역사적=일봉' not in blk, "'역사적=일봉 YYYY-MM-DD~' 표기가 남아 있다"
assert 'LIMIT 252' not in blk and 'days[59]' not in blk, '옛 전역 DISTINCT 날짜 창이 남아 있다'
assert 'd60' not in blk and '60일' not in blk, '60일 축이 남아 있다'

conn = sqlite3.connect(':memory:')
conn.row_factory = sqlite3.Row
conn.executescript("""
CREATE TABLE stocks (code TEXT PRIMARY KEY, name TEXT, market TEXT DEFAULT '',
  sector TEXT, market_cap REAL, close REAL, change_pct REAL, volume_mn REAL,
  is_etf INTEGER DEFAULT 0, market_cap_updated TEXT);
CREATE TABLE ohlcv (code TEXT, date TEXT, open REAL, high REAL, low REAL,
  close REAL, volume REAL, PRIMARY KEY (code, date));
""")

TODAY = '2026-09-15'
CAL = krx_calendar.Calendar()
# 받아 둔 구간 — 424일(ohlcv_autofill.LOOKBACK_CALENDAR_DAYS)의 실제 거래일.
_t = dt.date.fromisoformat(TODAY)
days = [d for d in (_t - dt.timedelta(days=i) for i in range(oa.LOOKBACK_CALENDAR_DAYS, 0, -1))
        if CAL.is_trading_day(d)]
days = [d.isoformat() for d in days]
W = nh.windows(TODAY, CAL)
assert W['d120_start'] == '2026-03-23', W           # 실제 일봉으로 센 120번째 이전 거래일
assert W['w52_start'] == '2025-09-16', W

HISTORY = {}      # 상장 이후 전 구간 — 가짜 소스가 돌려준다
FETCHED = []      # 어떤 종목의 이력을 물었는가


def fake_fetch(code, start, end):
    FETCHED.append(code)
    rows = HISTORY.get(code)
    if rows is None:
        return None, [], [f'{code}: 이력 없음']
    end_iso = f'{end[:4]}-{end[4:6]}-{end[6:8]}'
    return 'naver', [r for r in rows if r[1] <= end_iso], []


def add(code, name, close, closes_by_date, cap=1e12, is_etf=0, older=()):
    """closes_by_date: 받아 둔 일봉 {날짜: 종가}. older: 그보다 앞선 상장 이후 이력."""
    conn.execute(
        "INSERT INTO stocks (code, name, market, sector, market_cap, close, "
        " change_pct, volume_mn, is_etf, market_cap_updated) "
        "VALUES (?,?,'KOSPI','테스트',?,?,1.0,100,?,'20260915')",
        (code, name, cap, close, is_etf))
    rows = [(code, d, c, c, c, c, 1) for d, c in sorted(closes_by_date.items())]
    conn.executemany("INSERT INTO ohlcv VALUES (?,?,?,?,?,?,?)", rows)
    HISTORY[code] = list(older) + rows


def series(fn):
    return {d: fn(d) for d in days}


OLD = [('x', f'20{y:02d}-06-01', 0, 0, 0, 0, 1) for y in range(10, 24)]


def older(code, close):
    return [(code, d, close, close, close, close, 1) for _, d, *_ in OLD]


# 상장 이후 전부 9,000 → 오늘 9,500 이면 역사적
add('000001', '역사적', 9500, series(lambda d: 9000), older=older('000001', 8000))
# 52주 전엔 20,000, 최근 52주는 9,000 → 52주만
add('000002', '오십이주', 9500,
    series(lambda d: 20000 if d < W['w52_start'] else 9000), older=older('000002', 7000))
# 120거래일 전엔 20,000, 최근 120거래일만 9,000 → 120일만
add('000003', '백이십일', 9500,
    series(lambda d: 20000 if d < W['d120_start'] else 9000), older=older('000003', 7000))
# 아무것도 못 뚫음
add('000004', '평범', 5000, series(lambda d: 20000), older=older('000004', 7000))
# 52주 최고와 **같은** 가격 — 엄격히 '>' 라 신고가가 아니다
add('000012', '같은값', 9000, series(lambda d: 9000), older=older('000012', 7000))
# 오늘 자기 행이 이미 들어와 있는 종목 — 그 행을 최고가에 넣으면 늘 신고가가 된다
add('000005', '오늘행', 5000, series(lambda d: 20000), older=older('000005', 7000))
conn.execute("INSERT INTO ohlcv VALUES ('000005',?,5000,5000,5000,5000,1)", (TODAY,))
# 시총 999억 — 뚫었어도 모집단 밖이다
add('000007', '작은회사', 9500, series(lambda d: 9000), cap=99_900_000_000)
# 시총 1,000억 딱 — 경계는 포함
add('000008', '경계회사', 9500,
    series(lambda d: 20000 if d < W['d120_start'] else 9000), cap=100_000_000_000,
    older=older('000008', 7000))
# ETF — 시총이 커도 빠진다
add('000009', 'KODEX 200', 9500, series(lambda d: 9000), cap=5e12, is_etf=1)
# 모집단 안인데 일봉이 없다(채우는 중·신규 상장) — 판정은 못 하고 수로만 센다
conn.execute(
    "INSERT INTO stocks (code, name, market, sector, market_cap, close, "
    " change_pct, volume_mn, is_etf, market_cap_updated) "
    "VALUES ('000010','일봉없음','KOSPI','테스트',2e11,9500,1.0,100,0,'20260915')")
# **받아 둔 일봉의 첫날 이전**에 더 높은 종가가 있다 — 예전 정의('역사적=일봉
# {first_day}~')로는 역사적이었다. 상장 이후로 보면 52주다. 사용자가 짚은 그 문제.
add('000011', '옛고점', 9500, series(lambda d: 9000), older=older('000011', 50000))

res = nh.compute(conn, TODAY, MIN_CAP, fetch=fake_fetch, budget_s=10, cal=CAL)
assert res['error'] is None, res['error']
b = {k: [r['name'] for r in v] for k, v in res['buckets'].items()}

# 종가 기준이면 '등락률 마이너스인데 신고가' 가 나올 수 없다. 장중 고가로만
# 뚫은 종목이 안 잡히는지 본다.
add('000006', '장중만뚫음', 8000, series(lambda d: 9000))
conn.execute("UPDATE ohlcv SET high = 12000 WHERE code = '000006' AND date = ?", (days[-1],))
res2 = nh.compute(conn, TODAY, MIN_CAP, fetch=fake_fetch, budget_s=10, cal=CAL)
intra = [r for k in res2['buckets'] for r in res2['buckets'][k] if r['code'] == '000006']
assert not intra, '장중 고가로만 뚫은 종목이 신고가로 잡힌다'

print('역사적:', b['hist'])
print('52주  :', b['w52'])
print('120일 :', b['d120'])

ok = True


def want(cond, msg):
    global ok
    if not cond:
        ok = False
        print('FAIL —', msg)


everyone = sum(b.values(), [])
want(b['hist'] == ['역사적'], f"역사적 줄이 {b['hist']}")
want(b['w52'] == ['오십이주', '옛고점'], f"52주 줄이 {b['w52']}")
want(b['d120'] == ['백이십일', '경계회사'], f"120일 줄이 {b['d120']}")
want('작은회사' not in everyone, '시총 1,000억 미만이 신고가에 들어갔다')
want('KODEX 200' not in everyone, 'ETF 가 신고가에 들어갔다')
# 모집단 = 1,000억 이상·ETF 아님·오늘 거래 — 일봉 없는 종목까지 센다.
# 000001~000005, 000008, 000010, 000011, 000012 → 9. 판정한 수는 일봉이 있는 8.
want(res['universe_n'] == 9, f"모집단 수가 {res['universe_n']} 이다 (기대 9)")
want(res['scanned'] == 8, f"판정한 수가 {res['scanned']} 이다 (기대 8)")
note = oa.coverage_note(res['scanned'], res['universe_n'])
print('범위 문구:', note)
want(note == '시총 1,000억 이상 9종목 중 8종목 대상 · 일봉 미수집 1종목',
     f'범위 문구가 모자란 수를 안 밝힌다: {note}')
want('평범' not in everyone, '못 뚫은 종목이 들어갔다')
want('같은값' not in everyone, "창 최고와 같은 가격을 신고가로 쳤다 — '>' 가 아니라 '>=' 다")
want('오늘행' not in everyone, '오늘 자기 행을 최고가에 넣어 제 고가와 비겼다')
# 상장 이후 이력은 52주를 뚫은 종목에만 묻는다 — 첫 판정에서 받고, 둘째 판정은
# 표(alltime_high)에서 읽어 다시 묻지 않는다.
want(sorted(set(FETCHED)) == ['000001', '000002', '000011'],
     f'상장 이후 이력을 52주 통과 종목에만 묻지 않았다: {sorted(set(FETCHED))}')
want(len(FETCHED) == 3, f'같은 종목 이력을 두 번 받았다(표를 안 읽는다): {FETCHED}')
cached = {r[0]: r[1] for r in conn.execute('SELECT code, max_close FROM alltime_high')}
want(cached.get('000011') == 50000, f'옛고점의 상장 이후 최고가 표에 없다: {cached}')
want(res['hist_pending'] == [], f"보류가 생겼다: {res['hist_pending']}")
line = nh.basis_line(note, res)
print('기준 줄:', line)
want('역사적=상장 이후' in line and '52주=달력 52주' in line and '120일=120거래일' in line,
     f'기준 줄이 세 창을 안 밝힌다: {line}')
want(not re.search(r'\d{4}-\d{2}-\d{2}~', line), f'기준 줄에 특정일이 남았다: {line}')
print('\n통과' if ok else '\n실패')
sys.exit(0 if ok else 1)
