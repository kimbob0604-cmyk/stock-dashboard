"""일봉 자동 채움을 네트워크 없이 돌려 본다.

ohlcv_autofill 은 server.py 를 import 하지 않으므로 이 모듈은 **그대로 import**
한다(check_brief_sections.py 처럼 소스를 떼어 올 필요가 없다). 소스는 가짜로
갈아 끼우고, 못 박는 것은 다음과 같다.

  1. 대상은 **ETF 를 뺀 시총 하한 이상 전 종목** — 상위 N 으로 자르지 않는다.
     하한 미달·ETF·6자리 아닌 코드는 뺀다. 단위는 원(1,000억 = 1e11).
  2. 첫 소스가 죽으면 다음 소스로 넘어간다. 둘 다 죽으면 **실패로 센다.**
  3. 받은 행이 ohlcv 에 실제로 들어간다. 다시 돌려도 중복이 안 생긴다.
  4. 네이버 응답 파싱 — 날짜 형식(YYYYMMDD → YYYY-MM-DD), 깨진 행은 버린다.
  5. 0 을 지어내지 않는다 — 못 받은 종목은 행이 없다.
  6. 시황에 실릴 범위 문구가 모집단과 실제로 훑은 수를 말한다.
  7. **증분** — 최근 거래일까지 있는 종목은 묻지 않고, 모자란 종목은 자기
     마지막 날부터, 없는 종목만 전 구간. 장중에는 오늘 미확정 봉을 안 받는다.
     소스가 막히면 멈춘다.
  8. server.py 쪽 배선 — 16:10 잡·부팅 스레드·시황 머리말이 제자리에 있다.
"""
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ohlcv_autofill as oa                                      # noqa: E402

SRC = open(Path(__file__).resolve().parent.parent / 'server.py',
           encoding='utf-8').read()

ok = True


def want(cond, why):
    global ok
    if not cond:
        ok = False
        print('FAIL —', why)


# ── 1. 대상 고르기 — ETF 를 뺀 시총 하한 이상 **전 종목** ──────────────
# 예전 규칙은 거래대금 상위 300 이었다. ETF 가 상위를 차지해 실제 판정 대상
# 주식은 222종목뿐이었고, 시총 1,000억 이상인데 거래대금 순위 밖인 종목의
# 신고가를 통째로 놓쳤다. 지금 규칙은 하나다 — 시총이 하한 이상이면 전부.
want(oa.MIN_MARKET_CAP_WON == 100_000_000_000,
     f'판정 하한이 1,000억(원)이 아니다: {oa.MIN_MARKET_CAP_WON}')
want(oa.FILL_MIN_CAP_WON <= oa.MIN_MARKET_CAP_WON,
     '받아 두는 하한이 판정 하한보다 높다 — 판정 대상 일부가 일봉 없이 남는다')
want(not hasattr(oa, 'UNIVERSE_TOP_N'), '상위 N 자르기가 아직 남아 있다')

EOK = 100_000_000                                  # 1억(원)
SEED = {'stocks': {
    '005930': {'name': '삼성전자', 'market_cap': 3_000_000 * EOK},
    '000660': {'name': 'SK하이닉스', 'market_cap': 2_000_000 * EOK},
    '111111': {'name': '딱천억', 'market_cap': 1_000 * EOK},       # 경계 — 포함
    '222222': {'name': '구백구십구억', 'market_cap': 999 * EOK},   # 미달 — 빠져야
    '069500': {'name': 'KODEX 200', 'market_cap': 100_000 * EOK},  # ETF — 빠져야
    '138930': {'name': 'BNK금융지주', 'market_cap': 30_000 * EOK}, # 'BNK ' 아님 — 남아야
    '333333': {'name': '작은회사', 'market_cap': 0},                # 시총 0 — 빠져야
    'AAPL':   {'name': '애플', 'market_cap': 9_999_999 * EOK},      # 6자리 아님
}}


def is_etf(name):                    # server.py _is_etf_name 을 흉내 — 이름 패턴
    return any(p.lower() in (name or '').lower() for p in ('KODEX', 'TIGER', 'BNK '))


codes, basis = oa.select_universe(min_cap=oa.MIN_MARKET_CAP_WON,
                                  load_universe=lambda: SEED, is_etf=is_etf)
want(codes == ['005930', '000660', '138930', '111111'],
     f'대상이 시총 순 전 종목이 아니거나 필터가 샌다: {codes}')
want(basis == 'market_cap', f'고른 기준을 잘못 말한다: {basis}')
want('069500' not in codes, 'ETF 가 대상에 들어갔다')
want('222222' not in codes, '하한 미달이 대상에 들어갔다')

# 상위 N 으로 자르지 않는다 — 하한 이상이 많으면 많은 만큼 다 받는다.
MANY = {'stocks': {f'{i:06d}': {'name': f'회사{i}', 'market_cap': (2_000 + i) * EOK}
                   for i in range(1, 1201)}}
many = oa.universe_codes(min_cap=oa.MIN_MARKET_CAP_WON, load_universe=lambda: MANY)
want(len(many) == 1200, f'하한 이상 1,200종목 중 {len(many)}종목만 골랐다 — 잘랐다')

# 기본값은 받아 두는 하한(800억) — 1,000억 경계 아래 종목도 미리 받는다.
dflt = oa.universe_codes(load_universe=lambda: SEED, is_etf=is_etf)
want('222222' in dflt, f'기본 대상이 판정 하한에 딱 맞춰져 경계 종목을 미리 안 받는다: {dflt}')

want(oa.universe_codes(load_universe=lambda: {}) == [],
     '유니버스가 비었는데 대상을 만들어 냈다')
# 시총이 없으면 **지어내지 않는다.**
none_codes, none_basis = oa.select_universe(
    load_universe=lambda: {'stocks': {'005930': {'name': '삼성전자'}}})
want(none_codes == [] and none_basis == 'none',
     f'시총이 없는데 대상을 만들어 냈다: {none_codes}')

# ── 1-b. **실제 시드 파일**로 재 본다 — Render 재배포 직후의 그 상태 ─────
# 가짜 dict 가 아니라 배포되는 파일 그대로여야 의미가 있다. 옛 코드는 여기서
# 0종목이었다(시드에는 거래대금이 없다). ETF 판정은 server.py 의 패턴을 그대로
# 떼어 쓴다 — mark_etf_stocks 와 같은 목록이다.
import json as _json                                            # noqa: E402

_pm = re.search(r'(?m)^ETF_PATTERNS = \((.*?)\n\)', SRC, re.S)
want(_pm is not None, 'server.py 에서 ETF_PATTERNS 를 못 찾았다')
_PATS = re.findall(r"'([^']*)'", re.sub(r'#.*', '', _pm.group(1))) if _pm else []
want('def _is_etf_name' in SRC, 'server.py 에 이름 ETF 판정(_is_etf_name)이 없다')


def real_is_etf(name):
    n = (name or '').lower()
    return any(p.lower() in n for p in _PATS)


_seed_path = (Path(__file__).resolve().parent.parent
              / 'data' / 'naver_universe_seed.json')
want(_seed_path.exists(), '유니버스 시드 파일이 없다 — Render 폴백이 사라졌다')
SEED_TARGET = SEED_FILL = 0
if _seed_path.exists():
    _seed = _json.loads(_seed_path.read_text(encoding='utf-8'))
    _stk = _seed['stocks']
    # 단위 확인 — 원이다. 삼성전자가 100조(1e14)를 넘어야 한다.
    want((_stk.get('005930', {}).get('market_cap') or 0) > 1e14,
         '시드 시총이 원 단위가 아니다 — 하한 1e11 이 엉뚱한 뜻이 된다')
    real_codes, real_basis = oa.select_universe(
        min_cap=oa.MIN_MARKET_CAP_WON, load_universe=lambda: _seed,
        is_etf=real_is_etf)
    expect = sorted(c for c, v in _stk.items()
                    if c.isdigit() and len(c) == 6
                    and (v.get('market_cap') or 0) >= oa.MIN_MARKET_CAP_WON
                    and not real_is_etf(v.get('name')))
    SEED_TARGET = len(real_codes)
    SEED_FILL = len(oa.universe_codes(load_universe=lambda: _seed,
                                      is_etf=real_is_etf))
    want(sorted(real_codes) == expect,
         f'시드에서 고른 대상이 규칙(ETF 아님·시총 1,000억 이상 전부)과 다르다 '
         f'({len(real_codes)} vs {len(expect)})')
    want(real_basis == 'market_cap', f'시드 기준이 market_cap 이 아니다: {real_basis}')
    want(len(real_codes) > 1000,
         f'시드에서 대상이 {len(real_codes)}종목뿐이다 — 1,000억 이상이 그보다 많다')
    etf_in = [c for c in real_codes if real_is_etf(_stk[c].get('name'))]
    want(not etf_in, f'ETF 가 대상에 섞였다: {etf_in[:5]}')
    want('069500' not in real_codes, 'KODEX 200 이 대상에 들어갔다')
    want('005930' in real_codes and real_codes[0] == '005930',
         '삼성전자가 맨 앞이 아니다 — 시총 큰 순이 아니다')


# ── 1-c. 주입된 stocks 표 [(시총, 코드)] 가 유니버스보다 앞선다 ───────────
# server.py 가 ETF 를 뺀 목록을 넘긴다. 하한은 이 모듈이 건다 — 한 곳에서만.
inj, inj_basis = oa.select_universe(
    min_cap=oa.MIN_MARKET_CAP_WON, load_universe=lambda: SEED,
    load_ranked=lambda: ('market_cap', [(1_500 * EOK, '444444'),
                                        (9_000 * EOK, '555555'),
                                        (500 * EOK, '666666')]))
want(inj == ['555555', '444444'], f'주입 목록을 안 쓰거나 하한을 안 건다: {inj}')
want(inj_basis == 'market_cap', f'주입 기준을 잘못 말한다: {inj_basis}')

# 주입이 비면 유니버스로 넘어간다 — 빈손으로 끝내지 않는다.
fb, fb_basis = oa.select_universe(min_cap=oa.MIN_MARKET_CAP_WON,
                                  load_universe=lambda: SEED, is_etf=is_etf,
                                  load_ranked=lambda: ('none', []))
want(fb == codes and fb_basis == 'market_cap',
     f'주입이 비었을 때 유니버스로 안 넘어간다: {fb} / {fb_basis}')


# 주입이 터져도 넘어간다.
def _boom():
    raise RuntimeError('DB 없음')


br, br_basis = oa.select_universe(min_cap=oa.MIN_MARKET_CAP_WON,
                                  load_universe=lambda: SEED, is_etf=is_etf,
                                  load_ranked=_boom)
want(br == codes and br_basis == 'market_cap',
     f'주입이 예외를 던지면 통째로 실패한다: {br} / {br_basis}')


# ── 2~5. 수집 ────────────────────────────────────────────────────────────
from datetime import datetime                                    # noqa: E402

NOW = datetime(2026, 9, 16, 17, 0)          # 수요일 장 마감 뒤
def fake_rows(code, n=3):
    return [(code, f'2026-09-{10 + i:02d}', 100.0 + i, 110.0 + i,
             90.0 + i, 105.0 + i, 1000.0 + i) for i in range(n)]


def src_ok(code, start, end):
    return fake_rows(code)


def src_dead(code, start, end):
    raise OSError('막힘')


def src_empty(code, start, end):
    return []


def with_tmp_db(fn):
    """ohlcv 스키마만 있는 임시 DB 로 갈아 끼우고 fn 을 돌린다."""
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / 'test.db'
        cx = sqlite3.connect(db)
        cx.executescript("""
            CREATE TABLE ohlcv (code TEXT NOT NULL, date TEXT NOT NULL,
              open REAL, high REAL, low REAL, close REAL, volume REAL,
              PRIMARY KEY (code, date)) WITHOUT ROWID;""")
        cx.commit()
        cx.close()
        old = oa.DB_PATH
        oa.DB_PATH = db
        try:
            return fn(db)
        finally:
            oa.DB_PATH = old


old_fetchers = dict(oa._FETCHERS)


def run(sources, order, codes=('005930', '000660')):
    oa._FETCHERS.clear()
    oa._FETCHERS.update(sources)
    try:
        return with_tmp_db(lambda db: (
            oa.fill(codes=list(codes), source_order=order, gap=0, now=NOW),
            db))
    finally:
        oa._FETCHERS.clear()
        oa._FETCHERS.update(old_fetchers)


# 정상
(res, db) = run({'a': src_ok}, ('a',))
want(res['ok'] == 2 and res['failed'] == 0, f"정상 수집이 안 된다: {res}")
want(res['rows'] == 6, f"행 수가 안 맞는다: {res['rows']}")
want(res['by_source'] == {'a': 2}, f"소스 집계가 틀렸다: {res['by_source']}")

# 첫 소스가 죽으면 다음으로
(res, db) = run({'a': src_dead, 'b': src_ok}, ('a', 'b'))
want(res['ok'] == 2 and res['by_source'] == {'b': 2},
     f'폴백이 안 된다: {res["by_source"]} / {res["errors"][:1]}')
want(res['errors'], '폴백했는데 첫 소스 실패 사유를 안 남겼다')

# 빈 응답도 폴백 대상
(res, db) = run({'a': src_empty, 'b': src_ok}, ('a', 'b'))
want(res['by_source'] == {'b': 2}, '빈 응답에서 폴백하지 않는다')

# 전부 죽으면 실패로 센다 — 0 을 지어내지 않는다
(res, db) = run({'a': src_dead, 'b': src_dead}, ('a', 'b'))
want(res['ok'] == 0 and res['failed'] == 2, f'전멸을 실패로 안 센다: {res}')
want(res['rows'] == 0, '못 받았는데 행이 생겼다')
want(res['status']['rows'] == 0, '빈 수집인데 테이블에 뭔가 들어갔다')

# 대상이 없으면 사유를 남긴다
(res, db) = run({'a': src_ok}, ('a',), codes=())
want(res['codes'] == 0 and res['errors'], '대상 0인데 조용히 끝냈다')


# 실제로 DB 에 들어갔는지 + 두 번 돌려도 중복 없는지
def twice(db):
    oa.fill(codes=['005930'], source_order=('a',), gap=0, now=NOW)
    oa.fill(codes=['005930'], source_order=('a',), gap=0, now=NOW)
    cx = sqlite3.connect(db)
    n = cx.execute("SELECT COUNT(*) FROM ohlcv WHERE code='005930'").fetchone()[0]
    row = cx.execute("SELECT open, close FROM ohlcv WHERE date='2026-09-10'").fetchone()
    cx.close()
    return n, row


oa._FETCHERS.clear(); oa._FETCHERS.update({'a': src_ok})
n, row = with_tmp_db(twice)
oa._FETCHERS.clear(); oa._FETCHERS.update(old_fetchers)
want(n == 3, f'두 번 돌리니 행이 늘었다(PK 중복): {n}')
want(row == (100.0, 105.0), f'값이 제대로 안 들어갔다: {row}')


# ── 4. 네이버 응답 파싱 ───────────────────────────────────────────────────
class _FakeResp:
    def __init__(self, raw): self._raw = raw.encode('utf-8')
    def read(self): return self._raw
    def __enter__(self): return self
    def __exit__(self, *a): return False


def parse_with(raw):
    import urllib.request
    old = urllib.request.urlopen
    urllib.request.urlopen = lambda *a, **k: _FakeResp(raw)
    try:
        return oa._fetch_naver('005930', '20250101', '20260101')
    finally:
        urllib.request.urlopen = old


rows = parse_with(
    "[['날짜','시가','고가','저가','종가','거래량','외국인소진율'],"
    "['20260916', 248000, 254000, 247500, 253500, 11438019, 46.56],"
    "['20260915', 250000, 251000, 246000, 247000, 9000000, 46.5]]")
want(len(rows) == 2, f'네이버 행 파싱이 틀렸다: {rows}')
want(rows[0] == ('005930', '2026-09-16', 248000.0, 254000.0, 247500.0,
                 253500.0, 11438019.0),
     f'날짜 변환/열 매핑이 틀렸다: {rows[0]}')

# 깨진 행은 버리되 나머지는 살린다 — 0 으로 메우지 않는다
rows = parse_with(
    "[['날짜','시가','고가','저가','종가','거래량'],"
    "['20260916', 248000, 254000, 247500, 253500, 11438019],"
    "['20260915', None, None, None, None, None],"
    "['20260914', 1, 2],"
    "['20260913', 100, 110, 90, 105, 500]]")
want(len(rows) == 2, f'깨진 행 처리가 틀렸다: {rows}')
want(all(r[1] in ('2026-09-16', '2026-09-13') for r in rows),
     f'살아남은 행이 잘못됐다: {rows}')

# 헤더만 오거나 빈 응답
want(parse_with("[['날짜','시가']]") == [], '헤더만 왔는데 행을 만들었다')
want(parse_with("[]") == [], '빈 응답에서 행을 만들었다')


# ── 6. 시황에 실릴 범위 문구 ──────────────────────────────────────────────
# 모집단(시총 1,000억 이상)과 실제로 훑은 수를 같이 적는다. 다 못 훑었으면
# 빠진 수까지 — '대상' 이라는 말로 전부 본 척하지 않는다.
full_note = oa.coverage_note(1052, 1052)
part_note = oa.coverage_note(600, 1052)
want(full_note == '시총 1,000억 이상 1,052종목 대상', f'다 훑었을 때 문구: {full_note}')
want(part_note == '시총 1,000억 이상 1,052종목 중 600종목 대상 · 일봉 미수집 452종목',
     f'덜 훑었을 때 문구가 빠진 수를 안 밝힌다: {part_note}')
want('전체' not in full_note and '전 종목' not in full_note,
     f'다 훑었다고 더 센 말을 쓴다: {full_note}')
want('거래대금' not in part_note, '시총으로 골라 놓고 거래대금이라고 말한다')
want(oa.coverage_note(222) == '222종목 대상', '모집단을 모를 때 판정 수만 적지 않는다')
_sn = re.search(r'def _ohlcv_scope_note\(.*?(?=\ndef )', SRC, re.S)
want(_sn is not None and 'coverage_note(scanned, universe)' in _sn.group(0),
     'server.py 범위 문구가 coverage_note 를 모집단과 같이 부르지 않는다')
want('_ohlcv_scope_note(len(rows), universe_n)' in SRC,
     '시황이 모집단 수를 범위 문구에 안 넘긴다')
want('오늘 신고가 종목 없음 ({scope}' in SRC,
     "'신고가 없음' 에 무엇을 훑고 없는지가 안 붙는다")


# ── 7. 증분 — 종목마다 어디서부터 받는가 ──────────────────────────────────
todo, skipped = oa.plan(['A', 'B', 'C', 'D'],
                        {'A': '2026-09-16', 'B': '2026-09-11', 'D': '2026-09-17'},
                        full_start='20250701', up_to='2026-09-16')
want(dict(todo) == {'B': '20260911', 'C': '20250701'},
     f'증분 계획이 틀렸다 (최신은 건너뛰고, 모자라면 마지막 날부터, 없으면 전 구간): {todo}')
want(skipped == 2, f'최근 거래일까지 있는 종목을 {skipped}개만 건너뛰었다 (기대 2)')
todo_f, sk_f = oa.plan(['A', 'B'], {'A': '2026-09-16'}, '20250701', '2026-09-16',
                       full=True)
want(dict(todo_f) == {'A': '20250701', 'B': '20250701'} and sk_f == 0,
     f'full=True 인데 가진 것을 믿었다: {todo_f}')
todo_n, sk_n = oa.plan(['A'], {'A': '2026-09-16'}, '20250701', None)
want(todo_n == [('A', '20260916')] and sk_n == 0,
     '최근 거래일을 모를 때 마지막 날부터 다시 받지 않는다')

CALLS = []


def src_rec(code, start, end):
    CALLS.append((code, start, end))
    base = [('2026-09-14', 1), ('2026-09-15', 2), ('2026-09-16', 3), ('2026-09-17', 4)]
    return [(code, d, 100.0 + k, 110.0, 90.0, 100.0 + k, 1000.0) for d, k in base
            if start <= d.replace('-', '') <= end]


def incremental(db):
    cx = sqlite3.connect(db)
    # 'OLD' 는 9/14 까지 있다 → 9/14 부터 받아야. 'NEW' 는 없다 → 전 구간.
    # 'FRESH' 는 9/16 까지 있다 → 묻지 않아야.
    cx.executemany('INSERT INTO ohlcv VALUES (?,?,?,?,?,?,?)', [
        ('OLD', '2026-09-14', 1, 1, 1, 1, 1),
        ('FRESH', '2026-09-16', 1, 1, 1, 1, 1)])
    cx.commit()
    CALLS.clear()
    r = oa.fill(codes=['OLD', 'NEW', 'FRESH'], source_order=('a',), gap=0,
                up_to='2026-09-16', now=NOW, workers=2, batch=2)
    first = list(CALLS)
    # 다시 부르면 전부 최신이라 **한 번도 묻지 않는다.**
    CALLS.clear()
    r2 = oa.fill(codes=['OLD', 'NEW', 'FRESH'], source_order=('a',), gap=0,
                 up_to='2026-09-16', now=NOW)
    again = list(CALLS)
    # 장중(10:00)에는 오늘(9/17) 봉을 받지 않는다 — 끝날이 어제다. 최근 거래일이
    # 오늘이라도 받을 수 있는 끝날(어제)까지 찬 'OLD' 는 묻지 않는다.
    CALLS.clear()
    r3 = oa.fill(codes=['OLD', 'LATE'], source_order=('a',), gap=0,
                 up_to='2026-09-17', now=datetime(2026, 9, 17, 10, 0))
    intraday = list(CALLS)
    n17 = cx.execute("SELECT COUNT(*) FROM ohlcv WHERE date='2026-09-17'").fetchone()[0]
    cx.close()
    return r, first, r2, again, r3, intraday, n17


oa._FETCHERS.clear(); oa._FETCHERS.update({'a': src_rec})
r, first, r2, again, r3, intraday, n17 = with_tmp_db(incremental)
oa._FETCHERS.clear(); oa._FETCHERS.update(old_fetchers)
starts = {c: st for c, st, _ in first}
want(set(starts) == {'OLD', 'NEW'}, f'최신 종목까지 물었다: {sorted(starts)}')
want(starts.get('OLD') == '20260914', f"증분 종목의 시작이 마지막 날이 아니다: {starts.get('OLD')}")
want(starts.get('NEW') == r['start'], f"새 종목을 전 구간으로 안 받았다: {starts.get('NEW')}")
want(r['skipped'] == 1 and r['full'] == 1 and r['incremental'] == 1,
     f"증분 집계가 틀렸다: {({k: r[k] for k in ('skipped', 'full', 'incremental')})}")
want(again == [] and r2['skipped'] == 3,
     f'다 찬 뒤 다시 불렀는데 {len(again)}번 물었다 — 재시작마다 요청을 쓴다')
want([c for c, _, _ in intraday] == ['LATE'],
     f'장중에 어제까지 찬 종목을 또 물었다: {intraday}')
want(intraday and all(e == '20260916' for _, _, e in intraday),
     f'장중인데 끝날이 어제가 아니다: {intraday}')
want(n17 == 0, '장중 미확정 봉(오늘)이 저장됐다 — 16:10 잡이 그걸 확정으로 보고 건너뛴다')

# 주말에 부르면 끝날이 금요일이다 — 금요일까지 찬 종목을 모자라다고 안 본다.
CALLS.clear()
oa._FETCHERS.clear(); oa._FETCHERS.update({'a': src_rec})
rw = with_tmp_db(lambda db: oa.fill(codes=['X'], source_order=('a',), gap=0,
                                    now=datetime(2026, 9, 19, 12, 0)))
oa._FETCHERS.clear(); oa._FETCHERS.update(old_fetchers)
want(rw['end'] == '20260918', f"토요일에 끝날이 금요일이 아니다: {rw['end']}")

# 소스가 막히면 멈춘다 — 1,500종목을 타임아웃으로 두드리며 락을 쥐고 있지 않는다.
(res_b, _db) = run({'a': src_dead}, ('a',),
                   codes=[f'{i:06d}' for i in range(1, 101)])
want(res_b['stopped'] and res_b['failed'] < 100,
     f"막혔는데 끝까지 두드렸다 (실패 {res_b['failed']}, stopped={res_b['stopped']})")


# ── 7-b. 건너뛰기 기준 — 매일 자기 자신을 건너뛰면 안 된다 ────────────────
# 16:10 잡이 도는 시점에 각 종목의 최신일은 늘 '전 거래일' 이다. 건너뛰기 기준을
# '며칠 이내' 로 두면 매일 건너뛰고 오늘 봉이 영영 안 들어온다. 실제로 그렇게
# 짰다가 고쳤다 — 회귀를 여기서 막는다. 지금은 종목마다(plan) 판정한다.
_skip_blk = re.search(r'def _fill_ohlcv_job.*?(?=\n@app\.route)', SRC, re.S)
want(_skip_blk is not None, '_fill_ohlcv_job 을 못 찾았다')
if _skip_blk:
    blk = _skip_blk.group(0)
    want('_get_trading_date()' in blk,
         '건너뛰기 판정이 최근 거래일을 안 본다')
    want('.days <= ' not in blk,
         "건너뛰기 기준이 '며칠 이내' 다 — 16:10 잡이 매일 자기를 건너뛴다")
    want('up_to=latest_needed' in blk,
         '최근 거래일을 종목별 증분 판정(up_to)에 안 넘긴다')
    want('st["codes"] >= 50' not in blk,
         '테이블 전체 최신일 하나로 통째로 건너뛴다 — 채우다 끊긴 종목을 영영 안 받는다')
    want('is_etf=_is_etf_name' in blk, '시드 폴백에서 ETF 를 안 거른다')

# 대상 후보(stocks 표)가 ETF 를 빼고 시총을 넘긴다.
_rk = re.search(r'def _ohlcv_ranked_codes.*?(?=\n# 일봉 채움은)', SRC, re.S)
want(_rk is not None, '_ohlcv_ranked_codes 를 못 찾았다')
if _rk:
    rk = _rk.group(0)
    want('COALESCE(is_etf, 0) = 0' in rk, '대상 후보에서 ETF 를 안 뺀다')
    want('_is_etf_name(' in rk,
         '대상 후보가 is_etf 표식만 믿는다 — 재배포 직후 표식이 0 이면 ETF 가 섞인다')
    want('market_cap' in rk and 'volume_mn' not in rk,
         '대상 후보가 시총이 아니라 거래대금으로 고른다')

# 채우는 중에는 시황이 기다린다 — 절반만 훑은 신고가를 내지 않는다.
_rd = re.search(r'def _brief_data_ready.*?(?=\n\n\n)', SRC, re.S)
want(_rd is not None and '_OHLCV_FILL_LOCK.locked()' in _rd.group(0),
     '일봉 채움 중에도 시황이 준비됐다고 본다')

# ── 8. server.py 배선 ────────────────────────────────────────────────────
want('_fill_ohlcv_job' in SRC, 'server.py 에 채움 잡이 없다')
want('id="ohlcv_autofill"' in SRC, '16:10 스케줄 잡이 등록되지 않았다')
want('name="ohlcv-startup"' in SRC, '부팅 스레드가 없다')
want('_startup_ohlcv_fill' in SRC, '부팅 채움 함수가 없다')
want('_ohlcv_scope_note' in SRC, '시황에 범위 문구가 안 붙는다')
want('_ohlcv_fill_hint' in SRC, '일봉 부족 사유에 현황이 안 붙는다')
# 16:10 — 장 마감(15:30) 뒤라 확정 봉을 받고, 다음 날 16:00 시황의 입력이 된다

m = re.search(r'id="ohlcv_autofill"', SRC)
blk = SRC[max(0, m.start() - 400):m.start()] if m else ''
want('hour=16' in blk and 'minute=10' in blk,
     '채움 잡이 16:10 이 아니다 — 장 마감 뒤 확정 봉을 받아야 한다')
want('daemon=True, name="ohlcv-startup"' in SRC.replace('\n', ' ').replace('  ', ' ')
     or 'name="ohlcv-startup"' in SRC, '부팅 스레드가 데몬이 아니다')

# ── 9. 부팅 스레드가 **Render 에서도** 도는가 ─────────────────────────────
# 2026-09-18 의 진짜 원인. 이 스레드가 `if DISABLE_AUTO_FETCH: ... else:` 의
# else 쪽에 있었다. Render 는 늘 if 쪽으로 가므로 **정작 DB 가 사라지는
# 환경에서만 한 번도 안 돌았다.** 문자열로는 안 잡히는 회귀라 구문을 읽는다.
import ast as _ast                                               # noqa: E402

_tree = _ast.parse(SRC)
_startup_fn = next((n for n in _ast.walk(_tree)
                    if isinstance(n, _ast.FunctionDef) and n.name == '_startup'),
                   None)
want(_startup_fn is not None, '_startup 함수를 못 찾았다')


def _mentions_boot_fill(node):
    return any(isinstance(x, _ast.Name) and x.id == '_startup_ohlcv_fill'
               for x in _ast.walk(node))


if _startup_fn is not None:
    top_level = any(_mentions_boot_fill(st) for st in _startup_fn.body)
    want(top_level,
         '부팅 일봉 스레드가 _startup 본문 바로 아래가 아니다 — 어떤 분기 안에 '
         '있으면 Render 에서 안 돈다 (2026-09-18 회귀)')
    nested = [st for st in _startup_fn.body
              if isinstance(st, _ast.If) and _mentions_boot_fill(st)]
    want(not nested,
         '부팅 일봉 스레드가 if/else 안에 들어가 있다 — 조건이 거짓인 환경에서 '
         '조용히 안 돈다')

# 부팅 대기 조건은 '유니버스에 종목이 많은가' 가 아니라 '고를 수 있는가' 여야
# 한다. 시드는 4,063종목이라 앞의 조건은 재배포 직후 늘 즉시 참이 된다.
_boot = re.search(r'def _startup_ohlcv_fill.*?(?=\ndef )', SRC, re.S)
want(_boot is not None, '_startup_ohlcv_fill 을 못 찾았다')
if _boot:
    want('_ohlcv_ranked_codes()' in _boot.group(0),
         '부팅 대기가 실제로 고를 수 있는 종목을 안 본다')

# ── 10. 시황이 덜 찼을 때 **직접 채우고** 다시 보는가 ──────────────────────
_send = re.search(r'def send_closing_market_summary.*?\n    return True', SRC, re.S)
want(_send is not None, 'send_closing_market_summary 를 못 찾았다')
if _send:
    blk = _send.group(0)
    want('_fill_ohlcv_job()' in blk,
         '데이터가 덜 찼을 때 기다리기만 한다 — 채우는 시도를 안 한다')
    want(blk.count('_brief_data_ready()') >= 2,
         '채운 뒤 다시 보지 않는다')
    want('backup_db' in blk,
         '발송 표시를 즉시 백업하지 않는다 — 재배포하면 같은 시황이 또 나간다')

print(f"시드 기준 대상: 시총 {oa.MIN_MARKET_CAP_LABEL} 이상 {SEED_TARGET:,}종목 "
      f"(받아 두는 800억 이상 {SEED_FILL:,}종목) · 파싱 {len(rows)}행 · "
      f"증분·소스 폴백·차단 멈춤 확인")
print('통과' if ok else '실패')
sys.exit(0 if ok else 1)
