"""server.py 의 신고가 SQL·분류를 합성 DB 로 그대로 돌려 본다.

server.py 는 Flask 앱이라 여기서 import 하지 않는다. 대신 같은 스키마에
같은 쿼리를 던져 '무엇이 어느 줄에 담기는가' 만 못 박는다.
"""
import sqlite3, datetime as dt, re, sys

SRC = open('/home/user/stock-dashboard/server.py', encoding='utf-8').read()
m = re.search(r'rows = conn\.execute\("""\s*(SELECT s\.code AS code.*?)"""', SRC, re.S)
assert m, '신고가 창(60·252일) 쿼리를 server.py 에서 못 찾았다'
QUERY = m.group(1)

m2 = re.search(r'f"""(SELECT code, MAX\(high\) FROM ohlcv.*?)"""', SRC, re.S)
assert m2, '전 구간 최고가 쿼리를 server.py 에서 못 찾았다'
HALL_QUERY = m2.group(1)

conn = sqlite3.connect(':memory:')
conn.row_factory = sqlite3.Row
conn.executescript("""
CREATE TABLE stocks (code TEXT PRIMARY KEY, name TEXT, market TEXT DEFAULT '',
  sector TEXT, market_cap REAL, close REAL, change_pct REAL, volume_mn REAL,
  is_etf INTEGER DEFAULT 0);
CREATE TABLE ohlcv (code TEXT, date TEXT, open REAL, high REAL, low REAL,
  close REAL, volume REAL, PRIMARY KEY (code, date));
""")

TODAY = '2026-09-15'
days = [(dt.date(2026, 9, 15) - dt.timedelta(days=i)).isoformat() for i in range(1, 400)]
days.reverse()                      # 오래된 → 최근 (전부 '거래일' 로 취급)

def add(code, name, close, highs):
    conn.execute("INSERT INTO stocks VALUES (?,?,'KOSPI','테스트',1e12,?,1.0,100,0)",
                 (code, name, close))
    for d, h in zip(days, highs):
        conn.execute("INSERT INTO ohlcv VALUES (?,?,?,?,?,?,?)", (code, d, h, h, h, h, 1))

n = len(days)
# 전 구간 최고 9,000 → 오늘 9,500 이면 역사적
add('000001', '역사적', 9500, [9000] * n)
# 옛날에 20,000 찍고 최근 1년은 9,000 → 오늘 9,500 이면 52주만
add('000002', '오십이주', 9500, [20000] * (n - 252) + [9000] * 252)
# 1년 전엔 20,000, 최근 60일만 9,000 → 오늘 9,500 이면 60일만
add('000003', '육십일', 9500, [20000] * (n - 60) + [9000] * 60)
# 아무것도 못 뚫음
add('000004', '평범', 5000, [20000] * n)
# 오늘 자기 행이 이미 들어와 있는 종목 — 그 행을 최고가에 넣으면 늘 신고가가 된다
conn.execute("INSERT INTO stocks VALUES ('000005','오늘행','KOSPI','테스트',1e12,5000,1.0,100,0)")
for d in days:
    conn.execute("INSERT INTO ohlcv VALUES ('000005',?,20000,20000,20000,20000,1)", (d,))
conn.execute("INSERT INTO ohlcv VALUES ('000005',?,5000,5000,5000,5000,1)", (TODAY,))

trading = [r[0] for r in conn.execute(
    "SELECT DISTINCT date FROM ohlcv WHERE date < ? ORDER BY date DESC LIMIT 252",
    (TODAY,)).fetchall()]
cut60, cut252 = trading[59], trading[-1]
rows = conn.execute(QUERY, (cut60, cut252, TODAY)).fetchall()

# server.py 와 같은 2단계: 52주를 뚫은 종목에만 전 구간 최고가를 묻는다.
over52 = [r for r in rows if r['close'] and r['h252'] and r['close'] >= r['h252']]
hall_of = {}
if over52:
    codes = [r['code'] for r in over52]
    qs = ','.join('?' * len(codes))
    hall_of = {x[0]: x[1] for x in conn.execute(
        HALL_QUERY.replace('{qs}', qs), (*codes, TODAY)).fetchall()}

buckets = {'hist': [], 'w52': [], 'd60': []}
for r in rows:
    c = r['close']
    if not c:
        continue
    hall = hall_of.get(r['code'])
    if hall and c >= hall:
        buckets['hist'].append(r['name'])
    elif r['h252'] and c >= r['h252']:
        buckets['w52'].append(r['name'])
    elif r['h60'] and c >= r['h60']:
        buckets['d60'].append(r['name'])

print('역사적:', buckets['hist'])
print('52주  :', buckets['w52'])
print('60일  :', buckets['d60'])

ok = True
def want(cond, msg):
    global ok
    if not cond:
        ok = False
        print('FAIL —', msg)

want(buckets['hist'] == ['역사적'], f"역사적 줄이 {buckets['hist']}")
want(buckets['w52'] == ['오십이주'], f"52주 줄이 {buckets['w52']}")
want(buckets['d60'] == ['육십일'], f"60일 줄이 {buckets['d60']}")
want('평범' not in sum(buckets.values(), []), '못 뚫은 종목이 들어갔다')
want('오늘행' not in sum(buckets.values(), []),
     '오늘 자기 행을 최고가에 넣어 제 고가와 비겼다')
want(len(hall_of) == len(over52),
     '전 구간 최고가를 52주 통과 종목에만 묻지 않았다')
print('\n통과' if ok else '\n실패')
sys.exit(0 if ok else 1)
