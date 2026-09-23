"""장마감 시황에서 ETF 가 빠지는가 — server.py 의 패턴·쿼리를 그대로 돌려 본다.

2026-09-22 시황에 ETF 가 섞여 나갔다. 거래대금 상위에 KODEX 200 · KODEX 레버리지 ·
TIGER 200, 역사적 신고가 10종목 중 8종목이 KODEX CD금리액티브 · RISE 머니마켓액티브
같은 금리형 ETF 였다. 거르는 쿼리(`COALESCE(is_etf, 0) = 0`)도 패턴도 맞았는데
**표식이 낡아 있었다** — 표식을 붙이는 mark_etf_stocks 가 03:10 cron 에만 걸려
있었고 Render 무료 플랜은 그 시각에 잔다.

server.py 는 Flask 앱이라 import 하지 않는다(check_new_high_logic.py 와 같은 방식).
소스에서 패턴과 쿼리를 꺼내 같은 스키마에 던진다.

보는 것
  1. 시황 빌더가 쿼리보다 **먼저** 표식을 붙인다 — cron 이 안 돌아도 걸러진다.
  2. 09-22 에 실제로 섞여 나간 이름이 전부 걸러진다.
  3. 실제 종목은 남는다 — 특히 BNK금융지주('BNK' 가 오탐하던 은행주).
  4. 전 종목(data/naver_universe_seed.json)에서 오탐이 0 이다.
"""
import json, re, sqlite3, sys, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = open(os.path.join(ROOT, 'server.py'), encoding='utf-8').read()
fails = []


def check(ok, msg):
    print(('  PASS ' if ok else '  FAIL ') + msg)
    if not ok:
        fails.append(msg)


# 패턴은 모듈 수준 상수다 — 일봉 채움(_is_etf_name)도 같은 목록을 쓴다.
m = re.search(r'(?m)^ETF_PATTERNS = \((.*?)\n\)', SRC, re.S)
assert m, 'ETF_PATTERNS 를 server.py 에서 못 찾았다'
PATS = re.findall(r"'([^']*)'", re.sub(r'#.*', '', m.group(1)))
assert len(PATS) > 20, f'패턴이 {len(PATS)}개뿐이다 — 꺼내는 정규식이 틀렸다'

# 시황 특징주 쿼리 세 개(급등·급락·거래대금 상위)
FEAT = re.findall(r'conn\.execute\(f?"""(\s*SELECT[^"]*?FROM stocks[^"]*?)"""', SRC, re.S)
FEAT = [q for q in FEAT if 'is_etf' in q and ('change_pct > 5' in q or 'change_pct < -5' in q
                                              or 'ORDER BY volume_mn DESC LIMIT 5' in q)]
check(len(FEAT) == 3, f'특징주 쿼리 3개를 찾았다 (찾은 수 {len(FEAT)})')


def db(rows):
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE stocks (code TEXT PRIMARY KEY, name TEXT, market TEXT DEFAULT '',
        sector TEXT, market_cap REAL, market_cap_updated TEXT, close REAL, change_pct REAL,
        volume_mn REAL, is_etf INTEGER DEFAULT 0)""")
    # **표식이 낡은 상태**로 넣는다 — 전부 0. 09-22 가 이랬다.
    c.executemany("""INSERT INTO stocks (code, name, market, sector, close, change_pct, volume_mn, is_etf)
        VALUES (?,?,'KOSPI','기타',?,?,?,0)""", rows)
    return c


def mark(c):
    """server.py mark_etf_stocks 와 같은 일."""
    c.execute('UPDATE stocks SET is_etf = 0')
    for p in PATS:
        c.execute('UPDATE stocks SET is_etf = 1 WHERE name LIKE ?', (f'%{p}%',))


def shown(c):
    out = set()
    for q in FEAT:
        out |= {r['name'] for r in c.execute(re.sub(r'\{_NOISE_WHERE\}',
            'close >= 1000 AND change_pct IS NOT NULL AND ABS(change_pct) <= 30', q))}
    return out


print('1. 시황 빌더가 표식을 먼저 붙이는가')
i = SRC.index('def build_market_summary(')
body = SRC[i:SRC.index('\ndef ', i + 10)]
at_mark = body.find('mark_etf_stocks()')
at_feat = body.find('COALESCE(is_etf, 0) = 0')
check(at_mark != -1, '시황 빌더 안에서 mark_etf_stocks() 를 부른다')
check(at_mark != -1 and at_mark < at_feat, '특징주 쿼리보다 먼저 부른다')
check('ETF 표식 갱신 실패' in body, '표식이 실패해도 삼키지 않고 로그를 남긴다')

print('2. 09-22 에 섞여 나간 ETF 가 걸러지는가')
# 실제로 시황에 실린 이름·등락·거래대금 모양. 거래대금 상위 ±5% · 급등 +5% 에 들 값.
LEAKED = [
    ('069500', 'KODEX 200', 40000, 0.2, 27218), ('122630', 'KODEX 레버리지', 20000, 0.3, 18247),
    ('102110', 'TIGER 200', 40000, 0.2, 15386), ('459580', 'KODEX CD금리액티브(합성)', 1000000, 0.0, 9000),
    ('488770', 'KODEX 머니마켓액티브', 100000, 0.0, 8000), ('477080', 'RISE 머니마켓액티브', 100000, 0.0, 7000),
    ('357870', 'TIGER CD금리투자KIS(합성)', 50000, 0.0, 6500), ('472870', 'SOL 초단기채권액티브', 100000, 0.0, 6000),
    ('379810', 'KODEX 미국나스닥100(H)', 20000, 6.1, 5000), ('381170', 'TIGER 미국테크TOP10 INDXX(H)', 20000, 6.0, 4800),
    # 브랜드가 목록에 없어 새던 것
    ('069660', 'KIWOOM 200', 40000, 5.5, 4600), ('385720', 'TIME 코스피액티브', 20000, 5.4, 4500),
    ('487130', 'KoAct AI인프라액티브', 20000, 5.3, 4400),
]
REAL = [
    ('000660', 'SK하이닉스', 300000, -2.0, 54847), ('005930', '삼성전자', 80000, 0.4, 46405),
    ('003490', '대한항공', 25000, 8.2, 3000), ('064290', '인텍플러스', 20000, 9.4, 2500),
    ('138930', 'BNK금융지주', 12000, 5.2, 2400),   # 'BNK' 가 오탐하던 은행주
    ('195940', 'HK이노엔', 40000, 5.1, 2300),       # 'HK ' 와 붙여 쓰는 회사
    ('047310', '파워로직스', 8000, 6.0, 2200),      # '파워 ' 와 붙여 쓰는 회사
]
c = db(LEAKED + REAL)
before = shown(c)
check(any(n.startswith('KODEX') for n in before), '고치기 전(표식 낡음)에는 ETF 가 섞인다 — 재현')
mark(c)
after = shown(c)
leak = sorted(n for _, n, *_ in LEAKED if n in after)
check(not leak, f'표식을 붙이면 ETF 가 안 나온다 (남은 것: {leak or "없음"})')
lost = sorted(n for _, n, *_ in REAL if n not in after)
check(not lost, f'실제 종목은 남는다 (빠진 것: {lost or "없음"})')

print('3. 전 종목에서 오탐')
seed = os.path.join(ROOT, 'data', 'naver_universe_seed.json')
if os.path.exists(seed):
    names = {k: v['name'] for k, v in json.load(open(seed, encoding='utf-8'))['stocks'].items()
             if isinstance(v, dict) and v.get('name')}
    c = db([(k, n, 10000, 0.0, 0) for k, n in names.items()])
    mark(c)
    etf = {r['code']: r['name'] for r in c.execute('SELECT code, name FROM stocks WHERE is_etf = 1')}
    # 운용사 브랜드로 시작하지 않는데 ETF 로 찍힌 것 = 오탐 후보
    brand = tuple(p.strip().upper() for p in PATS if p.strip() and p[0] != ' ' and
                  not any('가' <= ch <= '힣' for ch in p)) + ('ETN',)
    odd = sorted(f'{k} {n}' for k, n in etf.items()
                 if not n.upper().startswith(brand) and not re.search(r'ETN|레버리지|인버스|선물|마이티|에셋플러스|파워 ', n))
    check('138930' not in etf, 'BNK금융지주는 ETF 가 아니다')
    check(not odd, f'브랜드로 시작하지 않는데 ETF 로 찍힌 것 {len(odd)}개 {odd[:5]}')
    print(f'       전 종목 {len(names):,} 중 ETF {len(etf):,}')
else:
    print('  SKIP 전 종목 목록이 없다')

print()
print('실패 %d건' % len(fails) if fails else '전부 통과')
sys.exit(1 if fails else 0)
