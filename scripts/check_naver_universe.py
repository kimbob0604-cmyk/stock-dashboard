#!/usr/bin/env python3
"""네이버 업종 유니버스가 종목을 흘리지 않는가 — server.py 의 진짜 함수를 돌려 본다.

2026-10-13 에 드러난 구멍 셋을 막는다.
  1. 업종 상세를 첫 페이지(100개)만 받았다 — 제약 175 · 반도체 172 등 9개 업종이 잘렸다.
  2. 종목코드를 `\\d{6}` 으로 걸렀다 — '0126Z0'(삼성에피스홀딩스) 같은 영숫자 코드
     88종목이 유니버스에서 통째로 빠졌다. 서버·프론트·수집기 곳곳이 같은 식이었다.
  3. 장중엔 목록 순서가 요청 사이에 바뀌어 페이지 경계에서 종목이 빠지거나 두 번 나온다.

server.py 는 Flask 앱이라 import 하지 않는다(check_etf_marking.py 와 같은 방식).
소스에서 함수만 꺼내 가짜 응답에 돌린다. --live 를 주면 진짜 네이버로 돈다 —
개발 컨테이너에선 막힐 수 있어 러너(collector-test.yml)에서 돌린다.

보는 것
  A. 175종목 업종을 끝까지 받는다 · 영숫자 코드가 남는다 · 중복이 없다
  B. 페이지가 밀려 한 바퀴에 못 채우면 다시 받아 채운다(앞서 받은 것은 버리지 않고,
     페이지 크기를 바꿔 경계를 옮긴다 — 경계에 붙은 종목은 같은 크기로는 계속 빠진다)
  C. 끝내 못 채우면 받은 만큼 돌려주고 수집 오류를 남긴다 · 첫 요청부터 실패면 error
  D. 국내 코드가 아닌 항목이 섞여도 완결 판정이 막히지 않는다(헛바퀴를 돌지 않는다)
  E. 코드 판정 — is_kr_code 와 SQLite GLOB 이 같은 답을 낸다
  F. `\\d{6}`·isdigit()·숫자 GLOB 판정이 다시 들어오지 않았다(살아 있는 코드 기준)
  H. ETF 표식 — 이름 패턴이 놓치는 운용사(IBK·DS…)도 업종 '기타' 로 잡힌다.
     영숫자 코드를 받기 시작하면서 이들이 유니버스에 들어오게 됐다
  G. (--live) 100개 넘는 업종이 totalCount 만큼 오고, 제약 업종에 0126Z0 이 있다
"""
import ast
import json
import os
import re
import sqlite3
import sys
import time as _real_time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import stock_master as sm                                     # noqa: E402

LIVE = "--live" in sys.argv
fails = []


def check(ok, msg):
    print(('  PASS ' if ok else '  FAIL ') + msg)
    if not ok:
        fails.append(msg)


SRC = open(os.path.join(ROOT, 'server.py'), encoding='utf-8').read()
TREE = ast.parse(SRC)
WANT = {'_parse_naver_flow_number', '_parse_pct', '_naver_json', '_scrape_naver_sector_detail'}
nodes = [n for n in TREE.body
         if (isinstance(n, ast.FunctionDef) and n.name in WANT)
         or (isinstance(n, ast.Assign) and any(getattr(t, 'id', '') == '_SECTOR_PAGES' for t in n.targets))]
found = {getattr(n, 'name', '_SECTOR_PAGES') for n in nodes}
assert found == WANT | {'_SECTOR_PAGES'}, f'server.py 에서 못 찾았다: {WANT | {"_SECTOR_PAGES"} - found}'


def load(naver_json=None, sleep=True):
    """server.py 의 함수들을 새 이름공간에 올린다. naver_json 을 주면 가짜로 바꾼다."""
    errors = []
    ns = {
        'json': json,
        'time': _real_time if sleep else types.SimpleNamespace(sleep=lambda s: None),
        '_is_kr_code': sm.is_kr_code,
        '_note_collect_error': lambda src, detail: errors.append((src, detail)),
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'server.py', 'exec'), ns)
    if naver_json:
        ns['_naver_json'] = naver_json
    return ns['_scrape_naver_sector_detail'], errors


def row(code, i):
    return {'itemCode': code, 'stockName': f'종목{i}', 'closePrice': f'{1000 + i:,}',
            'accumulatedTradingVolume': '1,000', 'fluctuationsRatio': '1.5'}


def fake_source(items, drift_passes=0, fail_first=False, never_complete=False, stuck_size=None):
    """네이버 업종 상세 흉내. drift_passes 바퀴 동안은 1쪽을 받은 뒤 2쪽 첫 종목이
    1쪽 끝으로 올라간다(장중 순서 변동) — 그 종목은 그 바퀴에 안 보인다.
    stuck_size 를 주면 그 페이지 크기로 받을 때마다 밀린다(경계에 붙은 종목).
    never_complete 면 totalCount 가 실제보다 하나 많다 — 목록에 끝내 안 나오는 종목."""
    state = {'calls': 0, 'pass': 0}

    def naver_json(url, source, timeout=10):
        state['calls'] += 1
        if fail_first:
            return None
        page = int(re.search(r'page=(\d+)', url).group(1))
        size = int(re.search(r'pageSize=(\d+)', url).group(1))
        if page == 1:
            state['pass'] += 1
        seq = list(items)
        moved = state['pass'] <= drift_passes or size == stuck_size
        if moved and page >= 2:
            # 1쪽을 받은 뒤 101번째가 1쪽으로 올라갔다 → 2쪽은 102번째부터 시작한다
            seq = seq[:size - 1] + [seq[size]] + [seq[size - 1]] + seq[size + 1:]
        chunk = seq[(page - 1) * size: page * size]
        return {'groupInfo': {'name': '제약'}, 'totalCount': len(items) + (1 if never_complete else 0),
                'stocks': [row(c, i) for i, c in enumerate(chunk)]}

    return naver_json, state


codes = [f'{i:06d}' for i in range(1, 174)] + ['0126Z0', '00680K']     # 175종목

print('── A. 끝 페이지까지 · 영숫자 코드')
nj, st = fake_source(codes)
fn, errs = load(nj, sleep=False)
out = fn('261')
got = [s['code'] for s in out['stocks']]
check(len(got) == 175, f'175종목 다 받았다 ({len(got)})')
check(len(set(got)) == len(got), '중복 없음')
check({'0126Z0', '00680K'} <= set(got), '영숫자 코드 0126Z0 · 00680K 가 남는다')
check(st['calls'] == 2, f'페이지 2번 요청 (실제 {st["calls"]})')
check(out['sector_name'] == '제약' and not errs, '업종 이름, 수집 오류 없음')

print('── B. 페이지가 밀린 바퀴는 다시 받아 채운다')
nj, st = fake_source(codes, drift_passes=1)
fn, errs = load(nj, sleep=False)
got = [s['code'] for s in fn('261')['stocks']]
check(len(set(got)) == 175, f'두 바퀴 합쳐 175종목 ({len(set(got))})')
check(st['pass'] == 2, f'두 바퀴 돌았다 (실제 {st["pass"]})')
check(not errs, '다 채웠으니 수집 오류 없음')

print('── B2. 경계에 붙은 종목 — 같은 크기로는 매번 빠진다, 크기를 바꿔 잡는다')
nj, st = fake_source(codes, stuck_size=100)
fn, errs = load(nj, sleep=False)
got = [s['code'] for s in fn('261')['stocks']]
check(len(set(got)) == 175 and not errs, f'페이지 크기를 바꾼 둘째 바퀴에 채운다 ({len(set(got))}, 바퀴 {st["pass"]})')

print('── C. 끝내 못 채우거나 처음부터 실패')
nj, st = fake_source(codes, never_complete=True)
fn, errs = load(nj, sleep=False)
out = fn('261', tries=3)
check(len(out['stocks']) == 175 and 'error' not in out, f'받은 만큼(175) 돌려준다 ({len(out["stocks"])})')
check(st['pass'] == 3, f'3바퀴에서 멈춘다 (실제 {st["pass"]})')
check(any('다 못 받았다' in d for _, d in errs), '수집 오류에 남긴다')
nj, st = fake_source(codes, fail_first=True)
fn, errs = load(nj, sleep=False)
out = fn('261')
check(out.get('error') and out['stocks'] == [], '첫 요청부터 실패면 error')

print('── D. 국내 코드가 아닌 항목이 섞여도 헛바퀴 없음')
nj, st = fake_source([f'{i:06d}' for i in range(1, 50)] + ['Q50001', 'ABCDEF'])
fn, errs = load(nj, sleep=False)
out = fn('999')
check(len(out['stocks']) == 49, f'국내 코드만 남긴다 ({len(out["stocks"])})')
check(st['pass'] == 1 and not errs, f'한 바퀴로 끝난다 (실제 {st["pass"]})')

print('── E. 코드 판정')
cases = {'005930': True, '0126Z0': True, '00680K': True, '0220WL': True,
         'NVDA': False, 'BRK.B': False, '5930': False, '0126z0': False, '005930\n': False, '': False}
bad = [c for c, want in cases.items() if sm.is_kr_code(c) != want]
check(not bad, f'is_kr_code ({bad or "전부 맞다"})')
cx = sqlite3.connect(':memory:')
cx.execute('CREATE TABLE t (c TEXT)')
cx.executemany('INSERT INTO t VALUES (?)', [(c,) for c in cases])
glob_hits = {r[0] for r in cx.execute('SELECT c FROM t WHERE c GLOB ?', (sm.KR_CODE_GLOB,))}
check(glob_hits == {c for c, w in cases.items() if w}, f'KR_CODE_GLOB 이 같은 답 ({sorted(glob_hits)})')

print('── F. 숫자 전용 판정이 다시 들어오지 않았다')
PATS = [re.compile(r'\\d\{6\}'), re.compile(r'isdigit\(\)\s*(and|or)\s*len\('),
        re.compile(r"GLOB '\[0-9\]\[0-9\]\[0-9\]\[0-9\]\[0-9\]\[0-9\]'"),
        re.compile(r'len\([^)]*\)\s*==\s*6\s+and\s+[^ ]*isdigit')]
# 일부러 남긴 것: 동작하지 않는 예전 HTML 스크레이퍼(*_legacy_unused), 날짜(8자리) 판정,
# 이 규칙을 정의·설명하는 파일, 서버를 거치지 않는 옛 테마 크롤러(theme_crawler.py)
SKIP_FILES = {'stock_master.py', 'theme_crawler.py'}
hits = []
for base, _, files in os.walk(ROOT):
    if any(part in base for part in ('.git', 'scripts', 'node_modules', 'cache')):
        continue
    for f in files:
        if not f.endswith(('.py', '.js')) or f in SKIP_FILES:
            continue
        path = os.path.join(base, f)
        text = open(path, encoding='utf-8').read()
        legacy = []
        if f.endswith('.py'):
            for n in ast.walk(ast.parse(text)):
                if isinstance(n, ast.FunctionDef) and n.name.endswith('_legacy_unused'):
                    legacy.append((n.lineno, n.end_lineno))
        for i, line in enumerate(text.splitlines(), 1):
            s = line.strip()
            if s.startswith(('#', '//')) or any(a <= i <= b for a, b in legacy):
                continue
            if any(p.search(line) for p in PATS) or re.search(r'/\^\\d\{6\}\$/', line):
                hits.append(f'{os.path.relpath(path, ROOT)}:{i}: {s[:90]}')
check(not hits, '살아 있는 코드에 숫자 6자리 전용 판정 없음' + ('' if not hits else ':\n         ' + '\n         '.join(hits)))

print("── H. ETF 표식 — 업종 '기타' 도 ETF")
mark = [n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == 'mark_etf_stocks']
pats = [n for n in TREE.body if isinstance(n, ast.Assign)
        and any(getattr(t, 'id', '') == 'ETF_PATTERNS' for t in n.targets)]
check(len(mark) == 1 and len(pats) == 1, 'mark_etf_stocks · ETF_PATTERNS 를 찾았다')
db = sqlite3.connect(':memory:')
db.row_factory = sqlite3.Row
db.execute("CREATE TABLE stocks (code TEXT PRIMARY KEY, name TEXT, market TEXT, sector TEXT, is_etf INTEGER)")
db.executemany('INSERT INTO stocks VALUES (?,?,?,?,0)', [
    ('0135Y0', 'IBK 중기종합채권(AA-이상)액티브', '', '기타'),      # 패턴에 안 걸리는 ETF
    ('0220B0', 'DS 코스닥액티브', '', '기타'),
    ('069500', 'KODEX 200', '', '기타'),
    ('005930', '삼성전자', '', '반도체와반도체장비'),
    ('0126Z0', '삼성에피스홀딩스', '', '제약'),
    ('094820', '일진파워', '', '기계'),                          # '파워 ' 패턴 오탐 없음
    ('NVDA', 'NVIDIA', 'US', 'Technology'),
])


class _Conn:
    def __enter__(self):
        return db

    def __exit__(self, *a):
        return False


mns = {'_SQLITE_OK': True, 'USE_SQLITE': True, '_get_db': _Conn,
       'log': types.SimpleNamespace(info=lambda *a, **k: None)}
exec(compile(ast.Module(body=pats + mark, type_ignores=[]), 'server.py', 'exec'), mns)
mns['mark_etf_stocks']()
etf = {r['code'] for r in db.execute('SELECT code FROM stocks WHERE is_etf = 1')}
check(etf == {'0135Y0', '0220B0', '069500'}, f'ETF 로 찍힌 것 {sorted(etf)} (기대: 0135Y0 · 0220B0 · 069500)')

if LIVE:
    print('── G. 진짜 네이버 (러너)')
    fn, errs = load()
    import urllib.request
    for i in range(4):          # 목록 한 번 받는 데도 연결이 끊길 수 있다 — 몇 번 다시
        try:
            req = urllib.request.Request('https://m.stock.naver.com/api/stocks/industry?page=1&pageSize=100',
                                  headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://m.stock.naver.com/'})
            groups = json.loads(urllib.request.urlopen(req, timeout=20).read().decode('utf-8'))['groups']
            break
        except Exception as exc:                                  # noqa: BLE001
            print(f'  업종 목록 재시도 {i + 1}: {type(exc).__name__}: {exc}')
            _real_time.sleep(2 ** i)
    else:
        check(False, '업종 목록을 못 받았다')
        groups = []
    big = sorted((g for g in groups if int(g.get('totalCount') or 0) > 100 and g['name'] != '기타'),
                 key=lambda g: -int(g['totalCount']))
    check(len(big) >= 3, f'100개 넘는 업종 {len(big)}개: ' + ', '.join(f"{g['name']}({g['totalCount']})" for g in big))
    for g in big:
        out = fn(str(g['no']))
        n, total = len(out['stocks']), int(g['totalCount'])
        # 목록 요약과 상세 사이에도 몇 초가 흐른다 — 상장·폐지 한두 개 차이는 넘긴다
        check(abs(n - total) <= 1 and n > 100, f"{g['name']}: {n} / 목록 요약 {total}")
        if g['name'] == '제약':
            check('0126Z0' in {s['code'] for s in out['stocks']}, '제약 업종에 0126Z0 삼성에피스홀딩스가 있다')
    # 연결이 한 번 끊겼다가 다시 받아 채운 것은 실패가 아니다 — 다 못 받은 것만 실패다
    short = [d for _, d in errs if '다 못 받았다' in d]
    check(not short, f'다 못 받은 업종 없음 ({short[:2]})')
    if errs:
        print(f'  (참고) 일시 오류 {len(errs)}건 — 재시도로 채웠다: {errs[0][1][:80]}')

print(f'\nFAIL {len(fails)}')
sys.exit(1 if fails else 0)
