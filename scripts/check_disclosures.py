"""DART 공시 카테고리(disclosure_category.py)와 /api/disclosures 를 네트워크 없이 확인한다.

  1. 제목 → 카테고리 — 대표 제목 표. 정정 표시를 떼고, 규칙 순서(자기주식처분 → 주주환원,
     합병계약 → 회사구조)를 지킨다.
  2. **server.py 의 진짜 API** 를 Flask 테스트 클라이언트로 — 카테고리·건수(필터 전)·
     카테고리 필터·시총·잠정실적 숫자(같은 rcept_no)·수집 꺼짐 표시.
  3. 같은 방식으로 위험지표·신고가 화면 API 가 200 을 준다(빈 DB 에서 죽지 않는다).
  4. 화면 배선 — disclosurepage.js 가 pages.js 보다 먼저 실리고, 옛 함수가 남지 않았다.

SERVER_NO_STARTUP=1 로 import 한다(check_newhigh_brief.py 와 같은 방식).
"""
import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

os.environ["SERVER_NO_STARTUP"] = "1"
os.environ["USE_SQLITE"] = "1"
os.environ.pop("DART_API_KEY", None)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import disclosure_category as dc                                 # noqa: E402

ok = True
n = 0


def want(cond, why):
    global ok, n
    n += 1
    if not cond:
        ok = False
        print('FAIL —', why)


# ── 1 ──
TABLE = {
    "연결재무제표기준영업(잠정)실적(공정공시)": "실적",
    "[기재정정]연결재무제표기준영업(잠정)실적(공정공시)": "실적",
    "매출액또는손익구조30%(대규모법인은15%)이상변동": "실적",
    "영업실적등에대한전망(공정공시)": "실적",
    "분기보고서 (2026.09)": "실적",
    "단일판매ㆍ공급계약체결": "계약",
    "[기재정정]단일판매ㆍ공급계약체결(자율공시)": "계약",
    "현금ㆍ현물배당결정": "주주환원",
    "주요사항보고서(자기주식취득결정)": "주주환원",
    "자기주식처분결과보고서": "주주환원",
    "주식소각결정": "주주환원",
    "주요사항보고서(유상증자결정)": "자금조달",
    "주요사항보고서(전환사채권발행결정)": "자금조달",
    "증권신고서(지분증권)": "자금조달",
    "주요사항보고서(회사합병결정)": "회사구조",
    "합병등종료보고서(합병)": "회사구조",
    "주요사항보고서(회사분할결정)": "회사구조",
    "무상증자결정": "회사구조",
    "최대주주변경": "회사구조",
    "타법인주식및출자증권취득결정": "자산양수도",
    "주요사항보고서(유형자산양수결정)": "자산양수도",
    "타법인주식및출자증권처분결정": "자산양수도",
    "신규시설투자등": "신규시설투자",
    "주식등의대량보유상황보고서(일반)": "지분공시",
    "임원ㆍ주요주주특정증권등소유상황보고서": "지분공시",
    "최대주주등소유주식변동신고서": "지분공시",
    "기업가치제고계획(자율공시)": "경영계획",
    "기업설명회(IR)개최(안내공시)": "경영계획",
    "조회공시요구(풍문또는보도)에대한답변(미확정)": "해명",
    "풍문또는보도에대한해명(미확정)": "해명",
    "소송등의제기ㆍ신청(일정금액이상의청구)": "사건사고",
    "횡령ㆍ배임혐의발생": "사건사고",
    "주권매매거래정지": "사건사고",
    "주요경영사항(자율공시)": "주요경영사항",
    "투자판단관련주요경영사항(임상시험계획승인신청)": "주요경영사항",
    "임시주주총회결과": "기타",
    "": "기타",
}
for t, want_cat in TABLE.items():
    got = dc.categorize(t)
    want(got == want_cat, f'{t!r} → {got} (기대 {want_cat})')
want(dc.clean_title('[기재정정][첨부추가] 무상증자결정') == '무상증자결정', '정정 표시를 못 뗀다')
cnt = dc.counts(['무상증자결정', '현금ㆍ현물배당결정', 'x'])
want(list(cnt) == list(dc.CATEGORIES) and cnt['회사구조'] == 1 and cnt['기타'] == 1,
     f'건수 표: {cnt}')

# ── 2 ── 진짜 API
try:
    import server                                                # noqa: E402
except SystemExit:
    print('server.py 를 import 하지 못했다(Flask 없음) — API 검사는 CI 에서 돈다')
    print(f'검사 {n}개')
    print('통과' if ok else '실패')
    sys.exit(0 if ok else 1)

path = os.path.join(tempfile.mkdtemp(), 't.db')
c = sqlite3.connect(path)
c.executescript((ROOT / 'db' / 'schema.sql').read_text(encoding='utf-8'))
c.execute("""CREATE TABLE IF NOT EXISTS earnings_actual (stock_code TEXT, year INTEGER, quarter INTEGER,
  source_type TEXT, disclosure_id TEXT, disclosure_date TEXT, disclosure_title TEXT,
  revenue REAL, operating_profit REAL, net_income REAL, eps REAL,
  revenue_yoy_pct REAL, op_yoy_pct REAL)""")
rows = [
    ('20261030000001', '005930', '삼성전자', '연결재무제표기준영업(잠정)실적(공정공시)', 'high', '20261030', 8),
    ('20261030000002', '000660', 'SK하이닉스', '현금ㆍ현물배당결정', 'medium', '20261030', 5),
    ('20261030000003', '247540', '에코프로비엠', '단일판매ㆍ공급계약체결', 'high', '20261029', 7),
    ('20261030000004', '247540', '에코프로비엠', '[기재정정]단일판매ㆍ공급계약체결', 'low', '20261029', 2),
    ('20261030000005', '', '어떤비상장사', '임시주주총회결과', 'low', '20261029', 0),
]
c.executemany("INSERT INTO disclosure_history (rcept_no, stock_code, corp_name, title, importance, "
              "rcept_dt, score) VALUES (?,?,?,?,?,?,?)", rows)
c.execute("INSERT INTO stocks (code, name, market, market_cap) VALUES ('005930','삼성전자','',3e14)")
c.execute("INSERT INTO earnings_actual (stock_code, year, quarter, source_type, disclosure_id, "
          "revenue, operating_profit, net_income, revenue_yoy_pct, op_yoy_pct) "
          "VALUES ('005930', 2026, 3, 'DART_PRELIMINARY', '20261030000001', 86000000, 12100000, 10000000, 8.5, 32.1)")
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
cl = server.app.test_client()
r = cl.get('/api/disclosures?limit=50')
d = r.get_json()
want(r.status_code == 200 and d['count'] == 5, f"API 응답: {r.status_code} {d.get('count')} {d.get('error')}")
cats = {it['rcept_no']: it['category'] for it in d['items']}
want(cats['20261030000001'] == '실적' and cats['20261030000002'] == '주주환원'
     and cats['20261030000004'] == '계약' and cats['20261030000005'] == '기타', f'카테고리: {cats}')
want(d['category_counts']['계약'] == 2 and d['category_counts']['실적'] == 1, f"건수: {d['category_counts']}")
want(d['collector_enabled'] is False, 'DART 키가 없는데 수집이 켜졌다고 한다')
sam = next(it for it in d['items'] if it['rcept_no'] == '20261030000001')
want(sam['market_cap'] == 3e14, f"시총: {sam.get('market_cap')}")
want(sam.get('earnings', {}).get('operating_profit') == 12100000 and sam['earnings']['op_yoy_pct'] == 32.1,
     f"잠정실적 숫자: {sam.get('earnings')}")
want('earnings' not in next(it for it in d['items'] if it['rcept_no'] == '20261030000002'),
     '실적이 아닌 공시에 실적 숫자를 붙였다')
want(sam['dart_url'].endswith('20261030000001') and 'keywords_json' not in sam, '링크·필드 모양')
r2 = cl.get('/api/disclosures?category=%EA%B3%84%EC%95%BD')         # 계약
d2 = r2.get_json()
want(d2['count'] == 2 and all(it['category'] == '계약' for it in d2['items']), f"카테고리 필터: {d2.get('count')}")
want(d2['category_counts'] == d['category_counts'], '필터를 걸었더니 칩 건수가 바뀌었다(필터 전 기준이어야)')
r3 = cl.get('/api/disclosures?importance=high')
want(r3.get_json()['count'] == 2, '중요도 필터가 깨졌다')
want(cl.get('/api/disclosures?limit=abc').status_code == 200, 'limit 이 숫자가 아니면 500 이 난다')

# ── 3 ── 다른 새 API 들이 빈 DB 에서 죽지 않는다
for url in ('/api/market/breadth', '/api/market/risk?period=6M&ma=25',
            '/api/movers?view=change&period=day', '/api/movers?view=change&period=week&market=KOSDAQ',
            '/api/movers?view=newhigh'):
    rr = cl.get(url)
    want(rr.status_code == 200, f'{url} → {rr.status_code} {rr.get_data(as_text=True)[:200]}')
want(cl.get('/api/movers?view=bad').status_code == 400, '잘못된 view 를 받았다')
# 화면이 쓰는 고정 주소(pages.js _SIMPLE_URLS)는 서버에서도 열려야 한다 — 새로고침·링크 진입
import re as _re                                                  # noqa: E402
_pj = (ROOT / 'static/js/pages.js').read_text(encoding='utf-8')
_m = _re.search(r'const _SIMPLE_URLS = \{(.*?)\};', _pj, _re.S)
want(_m is not None, 'pages.js 에 _SIMPLE_URLS 가 없다')
_urls = _re.findall(r"'(/[a-z]+)'", _m.group(1)) if _m else []
want(len(_urls) >= 9, f'고정 주소가 너무 적다: {_urls}')
for page in _urls:
    want(cl.get(page).status_code == 200, f'{page} 가 서버에서 안 열린다(spa_route 에 없다)')

# ── 4 ──
HTML = (ROOT / 'index.html').read_text(encoding='utf-8')
PJ = (ROOT / 'static/js/pages.js').read_text(encoding='utf-8')
want('/static/js/disclosurepage.js' in HTML
     and HTML.index('/static/js/disclosurepage.js') < HTML.index('/static/js/pages.js'),
     'disclosurepage.js 가 pages.js 보다 먼저 실리지 않는다')
want('function renderDisclosurePage' not in PJ and 'function _fetchDisclosures' not in PJ,
     'pages.js 에 옛 공시 함수가 남아 새 것을 덮는다')
want('let _disclosureFilter' in PJ, '전체/중요만 상태 변수가 사라졌다')

print(f'검사 {n}개')
print('통과' if ok else '실패')
sys.exit(0 if ok else 1)
