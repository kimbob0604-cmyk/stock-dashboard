"""상대 시총 · 연도별 수익률(relcap.py)을 네트워크 없이 확인한다.

  1. 야후 일봉 파싱 — 빈 종가를 버리고, 현지 시각(gmtoffset)으로 날짜를 매긴다.
  2. 비율 — 현재 시총 × (종가_t ÷ 종가_now), 다른 통화는 그날 환율로 달러 환산, 묶음 합.
     휴장일은 직전 값. 시총·일봉·환율이 없으면 지어내지 않고 이유를 남긴다.
  3. 연도별 수익률 — 연말 종가끼리. 'max' 는 기간을 직접 줘서 부른다(range=max 는 월봉을
     석 달로 묶어 연말 값이 틀린다 — 2026-10-30).
  4. server.py 의 진짜 API(가짜 소스) · 화면 배선.
"""
import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

os.environ["SERVER_NO_STARTUP"] = "1"
os.environ["USE_SQLITE"] = "1"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import relcap as rc                                              # noqa: E402

ok = True
n = 0


def want(cond, why):
    global ok, n
    n += 1
    if not cond:
        ok = False
        print('FAIL —', why)


# ── 1 ── 2026-10-30 00:00 KST = 2026-10-29 15:00 UTC
body = {"chart": {"result": [{"meta": {"gmtoffset": 32400}, "timestamp": [1793286000, 1793372400, 1793458800],
                              "indicators": {"quote": [{"close": [100.0, None, 110.0]}]}}]}}
pc = rc.parse_chart(body)
want(pc == [('2026-10-30', 100.0), ('2026-11-01', 110.0)], f'일봉 파싱: {pc}')
s = [('2026-01-02', 1.0), ('2026-01-05', 2.0)]
want(rc._asof(s, '2026-01-04') == 1.0 and rc._asof(s, '2026-01-01') is None and rc._asof(s, '2026-02-01') == 2.0,
     '휴장일 값 맞춤')

# ── 2 ──
hist = {'A.KS': [('2026-01-02', 100.0), ('2026-01-05', 200.0)],
        'P.KS': [('2026-01-02', 50.0), ('2026-01-05', 50.0)],
        'B': [('2026-01-02', 10.0), ('2026-01-05', 10.0)]}
fx = {'KRW': [('2026-01-02', 1000.0), ('2026-01-05', 1250.0)]}
caps = {'A.KS': 2e12, 'P.KS': 1e12, 'B': 1e9}        # 원·원·달러
ser, err = rc.pair_series([('A.KS', 'x', 'KRW'), ('P.KS', 'y', 'KRW')], [('B', None, 'USD')], hist, caps, fx)
# 오늘: (2e12 + 1e12)/1250 = 2.4e9 달러 → 240%. 첫날: A 1e12 + P 1e12 = 2e12/1000 = 2e9 → 200%
want(err is None and ser == [{'date': '2026-01-02', 'value': 200.0}, {'date': '2026-01-05', 'value': 240.0}],
     f'비율 계산: {ser} {err}')
_, e2 = rc.pair_series([('A.KS', 'x', 'KRW')], [('B', None, 'USD')], hist, {'A.KS': 1}, fx)
want(e2 and '현재 시총 없음' in e2, f'시총 없이 지어냈다: {e2}')
_, e3 = rc.pair_series([('A.KS', 'x', 'KRW')], [('B', None, 'USD')], hist, caps, {})
want(e3 and '환율 없음' in e3, f'환율 없이 지어냈다: {e3}')
_, e4 = rc.pair_series([('Z', None, 'USD')], [('B', None, 'USD')], hist, {**caps, 'Z': 1}, fx)
want(e4 and '일봉 없음' in e4, f'일봉 없이 지어냈다: {e4}')
want(rc.foreign_symbols() == sorted({'MU', 'TSM', 'AVGO', 'NVDA', '6981.T', '4062.T'}),
     f'해외 심볼: {rc.foreign_symbols()}')


def fake_fetch(sym, rng, interval='1d'):
    if sym == 'MU':
        raise TimeoutError('yahoo')
    if interval == '1mo':
        return [('2023-12-01', 100.0), ('2024-06-01', 130.0), ('2024-12-01', 120.0), ('2025-12-01', 90.0),
                ('2026-10-01', 99.0)]
    if sym in ('KRW=X',):
        return [('2026-01-02', 1300.0)]
    if sym in ('JPY=X',):
        return [('2026-01-02', 150.0)]
    return [('2026-01-02', 10.0)]


all_caps = {s: 1e12 for _, a, b in rc.PAIRS for s, _, _ in a + b}
out = rc.relative_caps(all_caps, '1Y', fetch=fake_fetch)
P = {p['name']: p for p in out['pairs']}
want(P['SK하이닉스 / 마이크론']['error'] == 'MU 일봉 없음' and P['SK하이닉스 / 마이크론']['series'] == [],
     '한 심볼 실패가 그 쌍만 비워야 한다')
want(P['브로드컴 / 엔비디아']['latest'] == 100.0 and P['SK하이닉스 / 삼성전자']['latest'] == 100.0,
     f"같은 시총이면 100%: {P['브로드컴 / 엔비디아']['latest']}")
want(out['period'] == '1Y' and rc.relative_caps({}, 'XX', fetch=fake_fetch)['period'] == '1Y', '기간 검증')

# ── 3 ──
ar = rc.annual_returns(fetch=fake_fetch)
row = ar['rows'][0]
want(row['returns'] == {2024: 20.0, 2025: -25.0, 2026: 10.0}, f"연말 종가 기준이 아니다: {row['returns']}")
want(ar['years'] == [2026, 2025, 2024], f"연도 순서: {ar['years']}")
urls = []
rc.fetch_chart('^GSPC', 'max', '1mo', get=lambda u: urls.append(u) or
               {"chart": {"result": [{"meta": {}, "timestamp": [], "indicators": {"quote": [{"close": []}]}}]}})
rc.fetch_chart('MU', '1y', get=lambda u: urls.append(u) or
               {"chart": {"result": [{"meta": {}, "timestamp": [], "indicators": {"quote": [{"close": []}]}}]}})
want('period1=0' in urls[0] and 'range=max' not in urls[0], f"max 를 range=max 로 부른다(월봉이 석 달로 묶인다): {urls[0]}")
want('range=1y' in urls[1], f'기간 지정: {urls[1]}')

# ── 4 ──
try:
    import server                                                # noqa: E402
except SystemExit:
    print('server.py 를 import 하지 못했다(Flask 없음) — API 검사는 CI 에서 돈다')
    server = None
if server is not None:
    path = os.path.join(tempfile.mkdtemp(), 't.db')
    c = sqlite3.connect(path)
    c.executescript((ROOT / 'db' / 'schema.sql').read_text(encoding='utf-8'))
    c.executemany("INSERT INTO stocks (code, name, market, market_cap) VALUES (?,?,'',?)",
                  [('000660', 'SK하이닉스', 1e15), ('005930', '삼성전자', 1.3e15),
                   ('005935', '삼성전자우', 1.4e14), ('009150', '삼성전기', 9e13)])
    c.commit()
    c.close()

    @contextmanager
    def gdb():
        cx = sqlite3.connect(path)
        cx.row_factory = sqlite3.Row
        try:
            yield cx
        finally:
            cx.close()

    server._get_db = gdb
    server._foreign_caps = lambda syms: {s: 1e12 for s in syms}
    rc.fetch_chart = fake_fetch
    cl = server.app.test_client()
    r = cl.get('/api/relcap?period=6M')
    d = r.get_json()
    want(r.status_code == 200 and len(d.get('pairs', [])) == len(rc.PAIRS), f"API: {r.status_code} {str(d)[:200]}")
    if d.get('pairs'):
        DP = {p['name']: p for p in d['pairs']}
        # 하이닉스 1e15원/1300 vs 삼성 1.3e15원/1300 → 76.92%
        want(DP['SK하이닉스 / 삼성전자']['latest'] == round(1e15 / 1.3e15 * 100, 2),
             f"API 가 stocks 시총을 안 쓴다: {DP['SK하이닉스 / 삼성전자']['latest']}")
    r2 = cl.get('/api/annual_returns')
    want(r2.status_code == 200 and r2.get_json()['rows'][0]['returns'].get('2025') == -25.0, '연도별 API')   # JSON 키는 문자열
    for page in ('/relcap', '/annual-returns'):
        want(cl.get(page).status_code == 200, f'{page} 가 안 열린다')

HTML = (ROOT / 'index.html').read_text(encoding='utf-8')
PJ = (ROOT / 'static/js/pages.js').read_text(encoding='utf-8')
want('data-page="relcap"' in HTML and 'data-page="annual"' in HTML and 'id="relcap-view"' in HTML
     and 'id="annual-view"' in HTML, 'index.html 메뉴·화면')
want(HTML.index('/static/js/riskpage.js') < HTML.index('/static/js/relcappage.js') < HTML.index('/static/js/pages.js'),
     'relcappage.js 는 riskpage.js(선 차트) 뒤, pages.js 앞에 실려야 한다')
want("relcap: '/relcap'" in PJ and "annual: '/annual-returns'" in PJ and "'page-annual'" in PJ, 'pages.js 등록')

print(f'검사 {n}개')
print('통과' if ok else '실패')
sys.exit(0 if ok else 1)
