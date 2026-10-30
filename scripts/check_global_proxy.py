"""글로벌 프록시(global_proxy.py)를 네트워크 없이 확인한다.

  1. 파싱 — 야후 chart(range=1d) · Hyperliquid metaAndAssetCtxs. 깨진 값은 버린다.
  2. 계산 — ADR/GDR 괴리율(증서 비율·환율), 유로 상장 환산(1유로 = EURUSD × USDKRW 원),
     24시간 선물의 국내 대비·24시간 등락·8시간 펀딩.
  3. 실패 격리 — 야후 한 심볼·Hyperliquid 전체가 죽어도 나머지는 나온다.
  4. server.py 의 진짜 API(Flask 테스트 클라이언트, 가짜 소스) · 화면 배선.
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
import global_proxy as gp                                        # noqa: E402

ok = True
n = 0


def want(cond, why):
    global ok, n
    n += 1
    if not cond:
        ok = False
        print('FAIL —', why)


# ── 1 ──
y = gp.parse_yahoo({"chart": {"result": [{"meta": {"regularMarketPrice": 161.83,
                    "chartPreviousClose": 165.91, "regularMarketTime": 1, "currency": "USD"}}]}})
want(y['price'] == 161.83 and y['prev'] == 165.91, f'야후 파싱: {y}')
try:
    gp.parse_yahoo({"chart": {"result": [{"meta": {"regularMarketPrice": None}}]}})
    want(False, '가격 없는 응답을 받아들였다')
except ValueError:
    pass
HL = [{"universe": [{"name": "xyz:SMSN"}, {"name": "xyz:AAPL"}, {"name": "xyz:SKHX"}, {"name": "xyz:KR200"}]},
      [{"markPx": "166.55", "prevDayPx": "172.39", "funding": "0.0000576704", "openInterest": "1", "dayNtlVlm": "2"},
       {"markPx": "200"},
       {"markPx": "0", "prevDayPx": "1"},                    # 0 가격 — 버린다
       {"markPx": "894.16", "prevDayPx": "933.66", "funding": "0.0000205359"}]]
hl = gp.parse_hyperliquid(HL, {"xyz:SMSN", "xyz:SKHX", "xyz:KR200"})
want(set(hl) == {"xyz:SMSN", "xyz:KR200"}, f'Hyperliquid 파싱 대상: {set(hl)}')
want(hl["xyz:SMSN"]["funding_8h_pct"] == round(0.0000576704 * 8 * 100, 4), f"펀딩 8시간: {hl['xyz:SMSN']}")

# ── 2·3 ──
YQ = {"KRW=X": 1334.8, "EURUSD=X": 1.1191, "SKHY": 162.09, "SMSN.IL": 4180.0, "HY9H.F": 946.0,
      "NQ=F": 28938.5, "ES=F": 7684.25, "^TNX": 4.989, "CL=F": 103.57, "DRAM": 51.75, "EWY": 161.83}


def fake_yahoo(sym):
    if sym == "SOXX":
        raise TimeoutError("yahoo 12s")
    if sym not in YQ:
        raise KeyError(sym)
    return {"price": YQ[sym], "prev": YQ[sym] / 1.02, "time": 1}


def fake_hl():
    return {"xyz:SMSN": {"mark": 166.59, "prev_day": 172.39, "funding_8h_pct": 0.045, "oi": 1, "vol_24h": 2},
            "xyz:SKHX": {"mark": 1055.8, "prev_day": 1136.7, "funding_8h_pct": 0.02, "oi": 1, "vol_24h": 2},
            "xyz:SKHY": {"mark": 150.5, "prev_day": 161.6, "funding_8h_pct": 0.005, "oi": 1, "vol_24h": 2},
            "xyz:KR200": {"mark": 893.95, "prev_day": 933.66, "funding_8h_pct": 0.0168, "oi": 1, "vol_24h": 2}}


DOM = {"005930": 222500.0, "000660": 1408000.0}            # 현대차 국내값 없음
out = gp.build(DOM, {"KPI200": 899.78}, yahoo=fake_yahoo, hl=fake_hl)
L = {r['symbol']: r for r in out['listings']}
want(L['SKHY']['premium_pct'] == 53.66 and L['SKHY']['fair'] == 105.48, f"ADR 괴리율: {L['SKHY']}")
want(L['SMSN.IL']['premium_pct'] == 0.3, f"GDR 괴리율: {L['SMSN.IL']}")
want(L['HY9H.F']['fair'] == round(1408000 / (1334.8 * 1.1191), 2) and abs(L['HY9H.F']['premium_pct']) < 1,
     f"유로 환산이 틀렸다: {L['HY9H.F']}")
P = {r['symbol']: r for r in out['perps']}
want(P['xyz:SMSN']['vs_close_pct'] == round((166.59 / (222500 / 1334.8) - 1) * 100, 2),
     f"선물 vs 국내: {P['xyz:SMSN']}")
want(P['xyz:KR200']['vs_close_pct'] == -0.65 and P['xyz:KR200']['unit'] == 'pt', f"코스피200: {P['xyz:KR200']}")
want(P['xyz:SKHY']['vs_label'] == '나스닥 종가' and P['xyz:SKHY']['vs_close_pct'] == round((150.5 / 162.09 - 1) * 100, 2),
     f"ADR 선물 vs 나스닥: {P['xyz:SKHY']}")
want(P['xyz:HYUNDAI'].get('error'), '목록에 없는 선물을 지어냈다')
want(P['xyz:SMSN']['change_24h_pct'] == round((166.59 / 172.39 - 1) * 100, 2), '24시간 등락')
E = {r['symbol']: r for r in out['us_etfs']}
want(E['SOXX'].get('error') and E['EWY']['change_pct'] == 2.0, f"한 심볼 실패 격리: {E}")
out2 = gp.build(DOM, {}, yahoo=fake_yahoo, hl=lambda: (_ for _ in ()).throw(OSError('HL down')))
want(all(r.get('error') == 'HL down' for r in out2['perps']) and out2['listings'][0].get('premium_pct'),
     'Hyperliquid 가 죽으면 나머지도 같이 죽는다')
want(out2['usdkrw'] == 1334.8, '환율을 못 넘긴다')

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
    c.executemany("INSERT INTO stocks (code, name, market, close) VALUES (?,?,'',?)",
                  [('005930', '삼성전자', 222500.0), ('000660', 'SK하이닉스', 1408000.0)])
    c.execute("CREATE TABLE risk_series (series TEXT, date TEXT, value REAL, PRIMARY KEY (series, date))")
    c.execute("INSERT INTO risk_series VALUES ('idx:KOSPI200','2026-10-30',899.78)")
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
    gp.fetch_yahoo = fake_yahoo
    gp.fetch_hyperliquid = fake_hl
    _orig = gp.build
    gp.build = lambda d, i: _orig(d, i, yahoo=fake_yahoo, hl=fake_hl)
    cl = server.app.test_client()
    r = cl.get('/api/global_proxy')
    d = r.get_json()
    want(r.status_code == 200 and 'perps' in d, f"API: {r.status_code} {str(d)[:200]}")
    if 'perps' in d:
        PP = {x['symbol']: x for x in d['perps']}
        want(PP['xyz:KR200']['vs_close_pct'] == -0.65, f"API 가 코스피200 종가를 risk_series 에서 못 읽는다: {PP['xyz:KR200']}")
        want({x['symbol']: x for x in d['listings']}['SKHY']['premium_pct'] == 53.66, 'API 가 stocks 종가를 못 읽는다')
        want(d.get('updated_at'), '갱신 시각이 없다')
    want(cl.get('/proxy').status_code == 200, '/proxy 가 안 열린다')
    gp.build = _orig

SRC = (ROOT / 'server.py').read_text(encoding='utf-8')
HTML = (ROOT / 'index.html').read_text(encoding='utf-8')
PJ = (ROOT / 'static/js/pages.js').read_text(encoding='utf-8')
MR = (ROOT / 'market_risk.py').read_text(encoding='utf-8')
want('"KOSPI200": "KPI200"' in MR, '코스피200 종가를 쌓지 않는다(market_risk.NAVER_INDEX)')
want('_GPROXY_TTL_S' in SRC, 'API 캐시가 없다')
want('data-page="proxy"' in HTML and 'id="proxy-view"' in HTML
     and HTML.index('/static/js/proxypage.js') < HTML.index('/static/js/pages.js'), 'index.html 등록')
want("proxy:      () => { renderProxyPage(); }" in PJ and "return '/proxy'" in PJ and "'page-proxy'" in PJ,
     'pages.js 등록')

print(f'검사 {n}개')
print('통과' if ok else '실패')
sys.exit(0 if ok else 1)
