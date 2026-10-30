"""공공데이터포털 통계(public_stats.py) — 증시자금(금투협)·수출입(관세청)을 네트워크 없이 확인한다.

  1. 키 — 이름 목록 순서, 인코딩·디코딩 두 형태, 키 오류만 다음 형태로, **오류 문구에 키 값이 없다**.
  2. 금투협 — JSON 봉투(항목 1개면 dict) · 쉼표 숫자 · 기관 오류 · `endBasDt` 하루 더 · 단위 배수 판정 ·
     증분 갱신 · 화면(조 원·전일 대비·신용/시총 비율).
  3. 관세청 — XML · 같은 HS 행 우선(합계와 세부를 겹쳐 세지 않음) · 접두 합 · 총계 행 버림 · 앞자리 0 ·
     12개월씩 나눠 부름 · 최신이면 안 부름 · 키·활용신청 오류면 다른 품목도 멈춤 · YoY/MoM/단가.
  4. server.py 의 진짜 API(키 없음 상태) · 스케줄·부팅·DART 며칠 채움 · 화면 배선.
"""
import json
import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

os.environ["SERVER_NO_STARTUP"] = "1"
os.environ["USE_SQLITE"] = "1"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import public_stats as ps                                        # noqa: E402

for n_ in ps.KEY_ENV_NAMES:
    os.environ.pop(n_, None)

ok = True
n = 0


def want(cond, why):
    global ok, n
    n += 1
    if not cond:
        ok = False
        print('FAIL —', why)


KEY = 'abc+def/ghi=='                       # 디코딩 형태(가짜)
GW = lambda code: (f'<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg>'
                   f'<returnReasonCode>{code}</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>').encode()

# ── 1 ──
want(ps.find_key({}) == (None, None), '키가 없는데 찾았다')
want(ps.find_key({'DATAGO_KEY': ' k1 ', 'SERVICE_KEY': 'k2'}) == ('DATAGO_KEY', 'k1'), '이름 순서·공백')
forms = ps._key_forms(KEY)
want([f for f, _ in forms] == ['decoded', 'raw'] and forms[0][1] == 'abc%2Bdef%2Fghi%3D%3D', f'키 형태: {forms}')
want(len(ps._key_forms('abc%2Bdef%2Fghi%3D%3D')) == 1, '이미 인코딩된 키를 두 번 시도한다')
seen = []


def get_key30_then_ok(url):
    seen.append(url)
    return GW('30') if len(seen) == 1 else b'{"ok":1}'


want(ps.call('X', '/p', {'a': 1}, KEY, get_key30_then_ok) == b'{"ok":1}' and len(seen) == 2,
     '키 오류면 다음 형태로 넘어가야 한다')
seen.clear()
try:
    ps.call('15101609', '/p', {}, KEY, lambda u: seen.append(u) or GW('20'))
    want(False, '활용신청 오류를 삼켰다')
except ps.DatagoError as e:
    want(e.code == '20' and len(seen) == 1, f'활용신청 오류는 바로 올린다: {e.code} {len(seen)}')
    want(KEY not in str(e) and 'abc' not in str(e), '오류 문구에 키 값이 들어갔다')
want(ps.gateway_error(b'{"response":{}}') is None and ps.gateway_error(GW('22')) == '22', '게이트웨이 판정')

# ── 2 ──
def kofia_body(rows):
    item = rows[0] if len(rows) == 1 else rows
    return json.dumps({"response": {"header": {"resultCode": "00"},
                                    "body": {"items": {"item": item}, "totalCount": len(rows)}}}).encode()


cap_rows = [{"basDt": "20261028", "invrDpsgAmt": "65,000,000,000,000", "brkTrdUcolMnyVsOppsTrdAmt": "40000000000"},
            {"basDt": "20261029", "invrDpsgAmt": "66000000000000", "brkTrdUcolMnyVsOppsTrdAmt": ""},
            {"basDt": "bad", "invrDpsgAmt": "1"}]
pk = ps.parse_kofia(kofia_body(cap_rows), 'capital')
want([r['date'] for r in pk] == ['2026-10-28', '2026-10-29'] and pk[0]['deposit'] == 6.5e13
     and pk[1]['forced_sale'] is None, f'금투협 파싱: {pk}')
want(len(ps.parse_kofia(kofia_body(cap_rows[:1]), 'capital')) == 1, '항목 1개(dict)를 못 읽는다')
try:
    ps.parse_kofia(json.dumps({"response": {"header": {"resultCode": "99", "resultMsg": "필수 누락"}}}).encode(), 'capital')
    want(False, '기관 오류를 삼켰다')
except ps.DatagoError as e:
    want(e.code == '99', '기관 오류 코드')
want(ps.unit_multiplier(6.5e13) == 1 and ps.unit_multiplier(65_000_000) == 1_000_000
     and ps.unit_multiplier(None) is None and ps.unit_multiplier(3.0) is None, '단위 배수 판정')

calls = []


def kofia_get(url):
    q = parse_qs(urlparse(url).query)
    calls.append((urlparse(url).path.rsplit('/', 1)[-1], q['beginBasDt'][0], q['endBasDt'][0]))
    if 'Capital' in url:
        return kofia_body(cap_rows[:2])
    return kofia_body([{"basDt": "20261028", "crdTrFingWhl": "29000000000000", "crdTrFingScrs": "20000000000000",
                        "crdTrFingKosdaq": "9000000000000"},
                       {"basDt": "20261029", "crdTrFingWhl": "29500000000000", "crdTrFingScrs": "20300000000000",
                        "crdTrFingKosdaq": "9200000000000"}])


db = sqlite3.connect(':memory:')
r1 = ps.refresh_kofia(db, date(2026, 10, 30), KEY, kofia_get)
want(r1 == {'capital': 2, 'credit': 2}, f'금투협 갱신: {r1}')
want(calls[0][2] == '20261031', f"endBasDt 를 하루 더해 부르지 않는다: {calls[0]}")
want(calls[0][1] == '20231030' or calls[0][1] <= '20231101', f'첫 수집은 3년: {calls[0]}')
ps.refresh_kofia(db, date(2026, 10, 30), KEY, kofia_get)
want(calls[-1][1] == '20260930', f'증분은 30일: {calls[-1]}')
pay = ps.kofia_payload(db, '2026-10-30', 365, {'KOSPI': 3e15, 'KOSDAQ': 4e14})
want(pay['latest']['deposit'] == {'value': 66.0, 'date': '2026-10-29', 'change': 1.0}, f"예탁금 조 원: {pay['latest'].get('deposit')}")
want(pay['ratio']['KOSPI'] == round(20.3e12 / 3e15 * 100, 3) and pay['ratio']['ALL'] == round(29.5e12 / 3.4e15 * 100, 3),
     f"신용/시총: {pay['ratio']}")
want(pay['unit_multiplier'] == 1, '단위 배수')
want(ps.kofia_payload(sqlite3.connect(':memory:'), '2026-10-30', 365)['error'].startswith('아직'), '빈 표 안내')

# ── 3 ──
def xml(items, rc='00'):
    body = ''.join('<item>' + ''.join(f'<{k}>{v}</{k}>' for k, v in it.items()) + '</item>' for it in items)
    return (f'<?xml version="1.0" encoding="UTF-8"?><response><header><resultCode>{rc}</resultCode>'
            f'<resultMsg>m</resultMsg></header><body><items>{body}</items></body></response>').encode()


rows = [{"year": "2026.08", "hsCode": "8542321010", "expDlr": "100", "expWgt": "1", "impDlr": "5", "impWgt": "1"},
        {"year": "2026.08", "hsCode": "8542321020", "expDlr": "50", "expWgt": "1", "impDlr": "0", "impWgt": "0"},
        {"year": "2026.09", "hsCode": "854232", "expDlr": "999", "expWgt": "9", "impDlr": "0", "impWgt": "0"},
        {"year": "2026.09", "hsCode": "8542321010", "expDlr": "500", "expWgt": "5", "impDlr": "0", "impWgt": "0"},
        {"year": "총계", "hsCode": "854232", "expDlr": "1", "expWgt": "1", "impDlr": "1", "impWgt": "1"},
        {"year": "2026.08", "hsCode": "8541100000", "expDlr": "7", "expWgt": "1", "impDlr": "0", "impWgt": "0"}]
pc = ps.parse_customs(xml(rows), '854232')
want(pc['202608']['exp_usd'] == 150 and pc['202609']['exp_usd'] == 999 and set(pc) == {'202608', '202609'},
     f'관세청 파싱(정확 행 우선·접두 합·총계 버림·다른 HS 버림): {pc}')
pz = ps.parse_customs(xml([{"year": "2026.08", "hsCode": "8504401000", "expDlr": "3", "expWgt": "1",
                            "impDlr": "0", "impWgt": "0"}]), '0190')
want(pz == {}, '접두가 안 맞는 HS 를 더했다')
pl = ps.parse_customs(xml([{"year": "2026.08", "hsCode": "106191000", "expDlr": "3", "expWgt": "1",
                            "impDlr": "0", "impWgt": "0"}]), '0106')
want(pl.get('202608', {}).get('exp_usd') == 3, f'앞자리 0 이 빠진 HS 를 못 되살린다: {pl}')
try:
    ps.parse_customs(xml([], rc='99'), '8504')
    want(False, '관세청 기관 오류를 삼켰다')
except ps.DatagoError:
    pass
cc = []


def customs_get(url):
    q = parse_qs(urlparse(url).query)
    cc.append((q['hsSgn'][0], q['strtYymm'][0], q['endYymm'][0]))
    return xml([{"year": f"{q['endYymm'][0][:4]}.{q['endYymm'][0][4:]}", "hsCode": q['hsSgn'][0],
                 "expDlr": "10", "expWgt": "2", "impDlr": "0", "impWgt": "0"}])


got = ps.fetch_customs('8504', '202409', '202609', KEY, customs_get)
want([c[1:] for c in cc] == [('202409', '202508'), ('202509', '202608'), ('202609', '202609')],
     f'12개월씩 나눠 부르지 않는다: {cc}')
db2 = sqlite3.connect(':memory:')
cc.clear()
res = ps.refresh_customs(db2, date(2026, 10, 30), KEY, customs_get)
want(len({c[0] for c in cc}) == len(ps.TRADE_ITEMS) and all(isinstance(v, int) for v in res.values()),
     f'품목 전부를 받지 않았다: {res}')
want(cc[0][1] == '202409' and cc[-1][2] == '202609', f'기간(전월까지 25개월): {cc[0]} {cc[-1]}')
# 최신이면 다시 묻지 않는다 — 25개월을 다 채워 둔다
for _c, _nm, hs in ps.TRADE_ITEMS:
    for k in range(25):
        db2.execute("INSERT OR REPLACE INTO customs_monthly VALUES (?,?,?,?,?,?)",
                    (hs, ps._ym_add('202609', -k), 100.0 + k, 10.0, 0, 0))
cc.clear()
res2 = ps.refresh_customs(db2, date(2026, 10, 30), KEY, customs_get)
want(cc == [] and set(res2.values()) == {'최신'}, f'최신인데 또 부른다: {cc[:2]}')
cc.clear()
res3 = ps.refresh_customs(sqlite3.connect(':memory:'), date(2026, 10, 30), KEY,
                          lambda u: cc.append(u) or GW('30'))
want(len(res3) == 1 and len(cc) == 2, f'키 오류인데 다른 품목도 부른다: {len(res3)}품목 {len(cc)}호출')
cp = ps.customs_payload(db2)
it = cp['items'][0]
L = it['latest']
want(L['ym'] == '202609' and L['exp_usd'] == 100.0 and L['unit_price'] == 10.0
     and L['yoy_pct'] == round((100 / 112 - 1) * 100, 1) and L['mom_pct'] == round((100 / 101 - 1) * 100, 1),
     f'YoY·MoM·단가: {L}')

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
    st = server._public_stats_refresh()
    want(st.get('key_name') is None and '키가 없다' in (st.get('error') or ''), f'키 없음 상태: {st}')
    cl = server.app.test_client()
    r = cl.get('/api/fund_flow?period=6M')
    d = r.get_json()
    want(r.status_code == 200 and d.get('error') and '키가 없다' in (d['status'].get('error') or ''),
         f"증시자금 API(키 없음): {r.status_code} {str(d)[:200]}")
    r2 = cl.get('/api/trade_stats')
    d2 = r2.get_json()
    want(r2.status_code == 200 and len(d2['items']) == len(ps.TRADE_ITEMS), f'수출입 API: {str(d2)[:200]}')
    for page in ('/fund-flow', '/trade'):
        want(cl.get(page).status_code == 200, f'{page} 가 안 열린다')

SRC = (ROOT / 'server.py').read_text(encoding='utf-8')
want('def poll_dart_disclosures(days_back: int = 0)' in SRC and '"bgn_de": bgn_de' in SRC, 'DART 며칠 채움')
want('poll_dart_disclosures(days_back=4)' in SRC and 'target=_startup_public_stats' in SRC, '부팅 스레드')
want('id="public_stats"' in SRC, '14:10 갱신 잡')
HTML = (ROOT / 'index.html').read_text(encoding='utf-8')
PJ = (ROOT / 'static/js/pages.js').read_text(encoding='utf-8')
want('data-page="fundflow"' in HTML and 'data-page="trade"' in HTML
     and HTML.index('/static/js/riskpage.js') < HTML.index('/static/js/fundtradepage.js') < HTML.index('/static/js/pages.js'),
     'index.html 등록')
want("fundflow: '/fund-flow'" in PJ and "trade: '/trade'" in PJ and "'page-trade'" in PJ, 'pages.js 등록')

print(f'검사 {n}개')
print('통과' if ok else '실패')
sys.exit(0 if ok else 1)
