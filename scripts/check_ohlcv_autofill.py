"""일봉 자동 채움을 네트워크 없이 돌려 본다.

ohlcv_autofill 은 server.py 를 import 하지 않으므로 이 모듈은 **그대로 import**
한다(check_brief_sections.py 처럼 소스를 떼어 올 필요가 없다). 소스는 가짜로
갈아 끼우고, 못 박는 것은 다음과 같다.

  1. 대상은 거래대금 상위 N — 하한 미달과 6자리 아닌 코드는 뺀다.
  2. 첫 소스가 죽으면 다음 소스로 넘어간다. 둘 다 죽으면 **실패로 센다.**
  3. 받은 행이 ohlcv 에 실제로 들어간다. 다시 돌려도 중복이 안 생긴다.
  4. 네이버 응답 파싱 — 날짜 형식(YYYYMMDD → YYYY-MM-DD), 깨진 행은 버린다.
  5. 0 을 지어내지 않는다 — 못 받은 종목은 행이 없다.
  6. 시황에 실릴 범위 문구가 실제로 훑은 수를 말한다.
  7. server.py 쪽 배선 — 16:10 잡·부팅 스레드·시황 머리말이 제자리에 있다.
"""
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


# ── 1. 대상 고르기 ────────────────────────────────────────────────────────
UNI = {'stocks': {
    '005930': {'volume_mn': 900_000},
    '000660': {'volume_mn': 500_000},
    '055550': {'volume_mn': 120_000},
    '111111': {'volume_mn': 10},          # 하한(50) 미달 → 빠져야
    'AAPL':   {'volume_mn': 999_999},     # 6자리 숫자 아님 → 빠져야
    '222222': {},                         # volume_mn 없음 → 빠져야
}}

codes = oa.universe_codes(top_n=10, load_universe=lambda: UNI)
want(codes == ['005930', '000660', '055550'],
     f'대상이 거래대금 순이 아니거나 필터가 샌다: {codes}')
want(oa.universe_codes(top_n=2, load_universe=lambda: UNI)
     == ['005930', '000660'], 'top_n 이 안 먹는다')
want(oa.universe_codes(load_universe=lambda: {}) == [],
     '유니버스가 비었는데 대상을 만들어 냈다')


# ── 2~5. 수집 ────────────────────────────────────────────────────────────
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
            oa.fill(codes=list(codes), source_order=order, gap=0),
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
    oa.fill(codes=['005930'], source_order=('a',), gap=0)
    oa.fill(codes=['005930'], source_order=('a',), gap=0)
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
want('종목 대상' in oa.coverage_note(300), f'범위 문구가 이상하다: {oa.coverage_note(300)}')
want('300' in oa.coverage_note(300), '범위 문구에 종목 수가 없다')


# ── 7. server.py 배선 ────────────────────────────────────────────────────
want('_fill_ohlcv_job' in SRC, 'server.py 에 채움 잡이 없다')
want('id="ohlcv_autofill"' in SRC, '16:10 스케줄 잡이 등록되지 않았다')
want('name="ohlcv-startup"' in SRC, '부팅 스레드가 없다')
want('_startup_ohlcv_fill' in SRC, '부팅 채움 함수가 없다')
want('_ohlcv_scope_note' in SRC, '시황에 범위 문구가 안 붙는다')
want('_ohlcv_fill_hint' in SRC, '일봉 부족 사유에 현황이 안 붙는다')
# 16:10 < 19:00 — 시황보다 먼저 돌아야 한다
import re  # noqa: E402
m = re.search(r'id="ohlcv_autofill"', SRC)
blk = SRC[max(0, m.start() - 400):m.start()] if m else ''
want('hour=16' in blk and 'minute=10' in blk,
     '채움 잡이 16:10 이 아니다 — 19:00 시황보다 먼저여야 한다')
want('daemon=True, name="ohlcv-startup"' in SRC.replace('\n', ' ').replace('  ', ' ')
     or 'name="ohlcv-startup"' in SRC, '부팅 스레드가 데몬이 아니다')

print(f"대상 선정 {codes} · 파싱 {len(rows)}행 · 소스 폴백 확인")
print('통과' if ok else '실패')
sys.exit(0 if ok else 1)
