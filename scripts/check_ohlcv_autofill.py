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

# ── 1-b. 거래대금이 아예 없을 때 시가총액으로 고른다 ──────────────────────
# Render 재배포 직후의 실제 상태다. cache/ 가 날아가 `_load_naver_universe()`
# 가 커밋된 시드로 떨어지는데, 시드에는 market_cap 만 있고 volume_mn 이 없다.
# 거래대금만 보던 옛 코드는 여기서 0종목을 돌려줬고 신고가가 하루 종일 비었다
# (2026-09-18). 그 회귀를 못 박는다.
SEED = {'stocks': {
    '005930': {'name': '삼성전자', 'market_cap': 300_000_000},
    '000660': {'name': 'SK하이닉스', 'market_cap': 200_000_000},
    '333333': {'name': '작은회사', 'market_cap': 0},     # 시총 0 → 빠져야
    'AAPL':   {'name': '애플', 'market_cap': 999_999_999},  # 6자리 아님 → 빠져야
}}
seed_codes, seed_basis = oa.select_universe(top_n=10, load_universe=lambda: SEED)
want(seed_codes == ['005930', '000660'],
     f'거래대금이 없을 때 시총으로 못 고른다: {seed_codes}')
want(seed_basis == 'market_cap', f'고른 기준을 잘못 말한다: {seed_basis}')

# 거래대금이 있으면 시총보다 거래대금이 우선이다.
_, vol_basis = oa.select_universe(top_n=10, load_universe=lambda: UNI)
want(vol_basis == 'volume', f'거래대금이 있는데 기준이 {vol_basis} 다')

# 둘 다 없으면 **지어내지 않는다.**
none_codes, none_basis = oa.select_universe(
    load_universe=lambda: {'stocks': {'005930': {'name': '삼성전자'}}})
want(none_codes == [] and none_basis == 'none',
     f'거래대금도 시총도 없는데 대상을 만들어 냈다: {none_codes}')

# ── 1-b2. **실제 시드 파일**로 재 본다 — Render 재배포 직후의 그 상태 ─────
# 가짜 dict 가 아니라 배포되는 파일 그대로여야 의미가 있다. 옛 코드는 여기서
# 0종목이었다.
import json as _json                                            # noqa: E402

_seed_path = (Path(__file__).resolve().parent.parent
              / 'data' / 'naver_universe_seed.json')
want(_seed_path.exists(), '유니버스 시드 파일이 없다 — Render 폴백이 사라졌다')
if _seed_path.exists():
    _seed = _json.loads(_seed_path.read_text(encoding='utf-8'))
    real_codes, real_basis = oa.select_universe(load_universe=lambda: _seed)
    want(len(real_codes) == oa.UNIVERSE_TOP_N,
         f'시드만 있는 상태에서 대상이 {len(real_codes)}종목이다 '
         f'(기대 {oa.UNIVERSE_TOP_N}) — 재배포 직후 일봉이 안 찬다')
    want(real_basis == 'market_cap',
         f'시드 기준이 market_cap 이 아니다: {real_basis}')
    want(all(c.isdigit() and len(c) == 6 for c in real_codes),
         '6자리 아닌 코드가 섞였다')


# ── 1-c. 주입된 실측 랭킹(stocks 표)이 유니버스보다 앞선다 ────────────────
inj, inj_basis = oa.select_universe(
    top_n=10, load_universe=lambda: SEED,
    load_ranked=lambda: ('volume', [(10.0, '111111'), (99.0, '222222')]))
want(inj == ['222222', '111111'], f'주입 랭킹을 안 쓴다: {inj}')
want(inj_basis == 'volume', f'주입 랭킹의 기준을 잘못 말한다: {inj_basis}')

# 주입이 비면 유니버스로 넘어간다 — 빈손으로 끝내지 않는다.
fb, fb_basis = oa.select_universe(top_n=10, load_universe=lambda: SEED,
                                  load_ranked=lambda: ('none', []))
want(fb == ['005930', '000660'] and fb_basis == 'market_cap',
     f'주입이 비었을 때 유니버스로 안 넘어간다: {fb} / {fb_basis}')

# 주입이 터져도 넘어간다.
def _boom():
    raise RuntimeError('DB 없음')


br, br_basis = oa.select_universe(top_n=10, load_universe=lambda: SEED,
                                  load_ranked=_boom)
want(br == ['005930', '000660'] and br_basis == 'market_cap',
     f'주입이 예외를 던지면 통째로 실패한다: {br} / {br_basis}')


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
# 시총으로 골랐으면 '거래대금 상위' 라고 쓰면 안 된다 — 그건 틀린 말이다.
want('거래대금' in oa.coverage_note(300, 'volume'),
     '거래대금 기준인데 문구가 그렇게 말하지 않는다')
want('시가총액' in oa.coverage_note(300, 'market_cap'),
     f"시총 기준인데 문구가 {oa.coverage_note(300, 'market_cap')} 다")
want('거래대금' not in oa.coverage_note(300, 'market_cap'),
     '시총으로 골라 놓고 거래대금이라고 말한다')


# ── 6-b. 건너뛰기 기준 — 매일 자기 자신을 건너뛰면 안 된다 ────────────────
# 16:10 잡이 도는 시점에 테이블 최신일은 늘 '전 거래일' 이다. 건너뛰기 기준을
# '며칠 이내' 로 두면 매일 건너뛰고 오늘 봉이 영영 안 들어온다. 실제로 그렇게
# 짰다가 고쳤다 — 회귀를 여기서 막는다.
_skip_blk = re.search(r'def _fill_ohlcv_job.*?(?=\n@app\.route)', SRC, re.S)
want(_skip_blk is not None, '_fill_ohlcv_job 을 못 찾았다')
if _skip_blk:
    blk = _skip_blk.group(0)
    want('_get_trading_date()' in blk,
         '건너뛰기 판정이 최근 거래일을 안 본다')
    want('.days <= ' not in blk,
         "건너뛰기 기준이 '며칠 이내' 다 — 16:10 잡이 매일 자기를 건너뛴다")
    want('st["last"] >= ' in blk or "st['last'] >= " in blk,
         '최신일 비교가 없다')

# ── 7. server.py 배선 ────────────────────────────────────────────────────
want('_fill_ohlcv_job' in SRC, 'server.py 에 채움 잡이 없다')
want('id="ohlcv_autofill"' in SRC, '16:10 스케줄 잡이 등록되지 않았다')
want('name="ohlcv-startup"' in SRC, '부팅 스레드가 없다')
want('_startup_ohlcv_fill' in SRC, '부팅 채움 함수가 없다')
want('_ohlcv_scope_note' in SRC, '시황에 범위 문구가 안 붙는다')
want('_ohlcv_fill_hint' in SRC, '일봉 부족 사유에 현황이 안 붙는다')
# 16:10 < 19:00 — 시황보다 먼저 돌아야 한다

m = re.search(r'id="ohlcv_autofill"', SRC)
blk = SRC[max(0, m.start() - 400):m.start()] if m else ''
want('hour=16' in blk and 'minute=10' in blk,
     '채움 잡이 16:10 이 아니다 — 19:00 시황보다 먼저여야 한다')
want('daemon=True, name="ohlcv-startup"' in SRC.replace('\n', ' ').replace('  ', ' ')
     or 'name="ohlcv-startup"' in SRC, '부팅 스레드가 데몬이 아니다')

# ── 8. 부팅 스레드가 **Render 에서도** 도는가 ─────────────────────────────
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

# ── 9. 시황이 덜 찼을 때 **직접 채우고** 다시 보는가 ──────────────────────
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

print(f"대상 선정 {codes} · 시드 폴백 {seed_codes}({seed_basis}) · "
      f"파싱 {len(rows)}행 · 소스 폴백 확인")
print('통과' if ok else '실패')
sys.exit(0 if ok else 1)
