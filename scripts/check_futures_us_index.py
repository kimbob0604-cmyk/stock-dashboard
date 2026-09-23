"""장마감 시황의 미국 지수·코스피200 선물 — 실측 응답으로 돌려 본다.

2026-09-23 사용자 요청: 미국 지수 오류를 고치고, '🔮 옵션/선물' 을 빼고
코스피200 선물 근월물·원월물의 시가·고가·저가·종가·미결제약정을 싣는다.

server.py 는 Flask 앱이라 import 하지 않는다(check_etf_marking.py 와 같은 방식).
필요한 함수만 소스에서 꺼내 가짜 yfinance·KIS 에 붙여 돌린다. KIS 응답은
2026-09-23 16:17 KST 러너 실측(ETF-Traker board/tools/probe_kis_futures.py) 그대로다.

보는 것
  1. 옛 오류가 사라졌다 — data.json actual_date(한국 날짜)로 미국 값의 신선도를
     재던 us_stale_tag, '🔮 옵션/선물' 섹션, ^KS200 대용 '야간선물' 줄.
  2. 미국 지수 줄에 미국 거래일이 붙고, 장중 봉은 '종가' 로 적지 않는다.
  3. 미국 지수를 못 받으면 옛 값을 내지 않고 못 받았다고 적는다.
  4. 선물: 마스터에서 근월물(A01612)·원월물(A01703)을 고르고 실측 필드로 채운다.
  5. 'A' 를 뗀 코드처럼 rt_cd=0 에 빈 output1 이 오면 0 으로 채우지 않고 사유를 적는다.
"""
import ast, io, os, sys, types, zipfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
SRC = open(os.path.join(ROOT, 'server.py'), encoding='utf-8').read()
fails = []


def check(ok, msg):
    print(('  PASS ' if ok else '  FAIL ') + msg)
    if not ok:
        fails.append(msg)


def body(name):
    s = SRC[SRC.index(f'def build_{name}('):]
    return s[:s.index('\ndef ', 10)]


print('1. 옛 오류')
kr = body('market_summary')
check('us_stale_tag' not in SRC, 'us_stale_tag(한국 날짜로 미국 값 신선도 판정)가 없다')
check('"title": "🔮 옵션/선물"' not in kr, "장마감 시황에 '🔮 옵션/선물' 이 없다")
check('night_futures' not in kr and 'options_signal_' not in kr, '야간선물 대용값·SPY/QQQ 옵션 줄이 없다')
check('_kospi200_futures_section()' in kr, '코스피200 선물 섹션을 붙인다')
check('_us_index_lines(' in kr and '_us_index_lines(' in body('us_market_summary'),
      '국장·미장 시황 모두 미국 지수를 그 자리에서 받는다')

# ── server.py 에서 필요한 것만 꺼낸다 ──
tree = ast.parse(SRC)
want = {'_fetch_us_indices_live', '_us_index_lines', '_kospi200_futures_section'}
chunks = []
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in want:
        chunks.append(ast.get_source_segment(SRC, node))
    if isinstance(node, ast.Assign) and any(getattr(t, 'id', '') == 'US_INDEX_TICKERS' for t in node.targets):
        chunks.append(ast.get_source_segment(SRC, node))
check(len(chunks) == 4, f'함수 3개 + 티커 표를 꺼냈다 ({len(chunks)})')


class Frame:
    """yfinance history 의 흉내 — Close 열과 날짜 인덱스."""
    def __init__(self, closes, days):
        self._c, self.index = closes, days
        self.empty = not closes

    def __len__(self):
        return len(self._c)

    def __getitem__(self, k):
        if k == 'Close':
            return types.SimpleNamespace(iloc=self._c, notna=lambda: self)
        return self

    def notna(self):
        return self


def run_us(frames, now_ny):
    fake = types.ModuleType('yfinance')

    def Ticker(sym):
        def history(**_):
            f = frames.get(sym)
            if isinstance(f, Exception):
                raise f
            return f
        return types.SimpleNamespace(history=history)
    fake.Ticker = Ticker
    sys.modules['yfinance'] = fake

    class DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return now_ny.astimezone(tz) if tz else now_ny
    ns = {'datetime': DT, 'timedelta': timedelta, 'timezone': timezone}
    for c in chunks:
        exec(c, ns)
    return ns


def ny(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ZoneInfo('America/New_York'))


def days(*ds):
    return [datetime(2026, 9, d, tzinfo=ZoneInfo('America/New_York')) for d in ds]


print('2. 미국 지수 — 한국 16:00(뉴욕 03:00) 에는 전날 미국 종가')
frames = {'^GSPC': Frame([6600.0, 6633.0], days(21, 22)),
          '^IXIC': Frame([22000.0, 21890.0], days(21, 22))}
ns = run_us(frames, ny(2026, 9, 23, 3, 0))
lines = ns['_us_index_lines'](('S&P 500', 'NASDAQ'))
print('   ', lines)
check(lines[0] == 'S&P 500 6,633.00 +0.50% <i>(09/22 종가)</i>', 'S&P 500 줄에 미국 거래일과 종가 표기')
check(lines[1].startswith('NASDAQ 21,890.00 -0.50%'), 'NASDAQ 하락 부호')
check(not any('⚠️' in l for l in lines), '멀쩡한 값에 ⚠️ 가 붙지 않는다')

print('3. 뉴욕 장중이면 종가라 적지 않는다')
frames = {'^GSPC': Frame([6600.0, 6633.0], days(22, 23))}
ns = run_us(frames, ny(2026, 9, 23, 11, 0))
l = ns['_us_index_lines'](('S&P 500',))
check('09/23 현재' in l[0] and '종가' not in l[0], f'장중 봉은 "현재" ({l[0]})')

print('4. 못 받으면 옛 값을 내지 않고 사유를 적는다')
frames = {'^GSPC': RuntimeError('boom'), '^IXIC': Frame([1.0], days(22))}
ns = run_us(frames, ny(2026, 9, 23, 3, 0))
l = ns['_us_index_lines'](('S&P 500', 'NASDAQ'))
print('   ', l)
check(len(l) == 1 and '미국 지수 수신 실패' in l[0] and 'S&P 500' in l[0] and 'NASDAQ' in l[0],
      '두 지수 모두 실패 사유 한 줄')

print('5. 코스피200 선물 — 실측 응답')
import kis_api as K

MASTER = ('1|A01612|KR4A016C0004|F 202612| |00000.00|1|2001|KOSPI200\n'
          '1|A01703|KR4A01730006|F 202703| |00000.00|2|2001|KOSPI200\n'
          '1|A01706|KR4A01760003|F 202706| |00000.00|3|2001|KOSPI200\n'
          '7|A04610|KR4A046A0000|변동성F 202610| |00000.00|1|0503|VKOSPI\n'
          'B|A05610|KR4A056A0007|미니F 202610| |00000.00|1|2001|KOSPI200\n')
buf = io.BytesIO()
with zipfile.ZipFile(buf, 'w') as z:
    z.writestr('fo_idx_code_mts.mst', MASTER.encode('cp949'))
OUT = {
    'A01612': {'hts_kor_isnm': 'F 202612', 'futs_prpr': '1127.75', 'futs_prdy_vrss': '15.75',
               'futs_prdy_ctrt': '1.42', 'acml_vol': '81070', 'hts_otst_stpl_qty': '134791',
               'otst_stpl_qty_icdc': '2073', 'futs_oprc': '1139.40', 'futs_hgpr': '1141.20',
               'futs_lwpr': '1115.65', 'futs_last_tr_date': '20261210'},
    'A01703': {'hts_kor_isnm': 'F 202703', 'futs_prpr': '1112.65', 'futs_prdy_vrss': '7.65',
               'futs_prdy_ctrt': '0.69', 'acml_vol': '19', 'hts_otst_stpl_qty': '4409',
               'otst_stpl_qty_icdc': '-5', 'futs_oprc': '1117.75', 'futs_hgpr': '1120.20',
               'futs_lwpr': '1106.70', 'futs_last_tr_date': '20270311'},
}


def install(outputs):
    def get(url, headers=None, params=None, timeout=None):
        if url == K.FO_MASTER_URL:
            return types.SimpleNamespace(content=buf.getvalue(), raise_for_status=lambda: None)
        code = params['FID_INPUT_ISCD']
        js = {'rt_cd': '0', 'msg1': '정상처리 되었습니다.', 'output1': outputs.get(code, {})}
        return types.SimpleNamespace(json=lambda: js)
    K.requests.get = get
    K._headers = lambda tr: {'tr_id': tr}
    K._get_cache = lambda k, ttl: None
    K._set_cache = lambda k, d: None
    K._fut_master_cache.update(date=None, contracts=None)


install(OUT)
check([c[1] for c in K._kospi200_futures_contracts()] == ['A01612', 'A01703', 'A01706'],
      '마스터에서 코스피200 선물만 월물 순서대로 (미니·변동성 제외)')
fut = K.get_kospi200_futures(2)
near, far = fut['contracts']
check(near['label'] == '근월물' and near['code'] == 'A01612', '근월물 = A01612')
check(far['label'] == '원월물' and far['code'] == 'A01703', '원월물 = A01703')
check((near['open'], near['high'], near['low'], near['close']) == (1139.40, 1141.20, 1115.65, 1127.75),
      '근월물 시·고·저·종')
check((near['oi'], near['oi_change'], far['oi_change']) == (134791, 2073, -5), '미결제약정·증감')
check(fut['error'] is None, '오류 없음')

sys.modules['kis_api'] = K


def section_at(hh, mm):
    ns = run_us({}, ny(2026, 9, 23, 3, 0))
    ns['now_kst'] = lambda: datetime(2026, 9, 23, hh, mm, tzinfo=timezone(timedelta(hours=9)))
    return ns['_kospi200_futures_section']()


sec = section_at(16, 5)
print('   ' + '\n    '.join(sec['items']))
check(sec['title'] == '🧭 코스피200 선물', '섹션 제목')
check(sec['items'][0] == '<b>근월물</b> F 202612 · 종가 1,127.75 +1.42%', '근월물 첫 줄')
check(sec['items'][1] == '  시 1,139.40 · 고 1,141.20 · 저 1,115.65', '시·고·저 줄')
check(sec['items'][2] == '  미결제약정 134,791 (+2,073)', '미결제약정 줄')
check(sec['items'][5] == '  미결제약정 4,409 (-5)', '원월물 미결제약정 감소')
check('현재 ' in section_at(10, 0)['items'][0], '정규장 중에는 종가가 아니라 현재')

print("6. 빈 output1 (rt_cd=0) 은 0 으로 채우지 않는다")
install({'A01612': OUT['A01612']})
fut = K.get_kospi200_futures(2)
check(len(fut['contracts']) == 1 and 'A01703 응답 없음' in (fut['error'] or ''),
      f"원월물 빈 응답은 빠지고 사유가 남는다 ({fut['error']})")
install({})
sec = section_at(16, 5)
check(not sec['items'] and 'A01612' in sec.get('error', ''), '전부 비면 섹션 error 로 드러난다')

print()
print('FAIL %d' % len(fails) if fails else 'ALL PASS')
sys.exit(1 if fails else 0)
