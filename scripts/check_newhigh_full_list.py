"""신고가 전 종목 표기 + 시총 신선도 + 메시지 분할을 server.py 에서 뽑아 돌려 본다.

server.py 는 Flask 앱이라 import 하지 않는다(check_brief_sections.py 와 같은 이유).
고친 함수의 **본문을 소스에서 그대로 떼어** 같은 입력에 태우고, 규칙만 못 박는다.

  1. 세 등급 모두 '외 N종목' 으로 접지 않는다 (전 종목).
  2. 접기 코드 자체는 살아 있다 — 상한을 넣으면 접히고 접은 수를 적는다.
  3. 시총은 언제 것인지 모르거나 묵었으면 `*` 가 붙는다.
  4. 본문이 길어지면 **줄 경계에서** 나뉜다. 종목 줄은 잘리지도 사라지지도 않는다.
"""
import re
import sys
from datetime import date, datetime, timedelta

SRC = open('/home/user/stock-dashboard/server.py', encoding='utf-8').read()

ok = True


def want(cond, why):
    global ok
    if not cond:
        ok = False
        print('FAIL —', why)


def grab(pattern, what):
    m = re.search(pattern, SRC, re.S)
    assert m, f'{what} 를 server.py 에서 못 찾았다'
    return m


# ── server.py 에서 떼어 오기 ──────────────────────────────────────────────
ns: dict = {'datetime': datetime, 're': re}

# now_kst 는 앱 전역이라 여기서는 '오늘' 만 흉내 낸다.
_TODAY = date(2026, 9, 15)


class _FakeNow:
    @staticmethod
    def date():
        return _TODAY


ns['now_kst'] = lambda: _FakeNow()

for pat, what in (
    (r'^_CAP_STALE_DAYS = \d+', '_CAP_STALE_DAYS'),
    (r'^_NH_LIST_MAX = \{[^}]*\}', '_NH_LIST_MAX'),
    (r'^_TG_LIMIT = \d+', '_TG_LIMIT'),
    (r'^_TG_CHUNK = \d+', '_TG_CHUNK'),
    (r'^def _fmt_cap\(.*?\n(?=\n\ndef |\n\n# )', '_fmt_cap'),
    (r'^def _cap_is_fresh\(.*?\n(?=\n\ndef |\n\n# )', '_cap_is_fresh'),
    (r'^def _split_telegram_lines\(.*?\n(?=\n\ndef )', '_split_telegram_lines'),
):
    src = grab('(?m)' + pat, what).group(0)
    exec(compile(src, 'server.py', 'exec'), ns)

_NH_LIST_MAX = ns['_NH_LIST_MAX']
_CAP_STALE_DAYS = ns['_CAP_STALE_DAYS']
_fmt_cap = ns['_fmt_cap']
_split = ns['_split_telegram_lines']

print(f'_NH_LIST_MAX = {_NH_LIST_MAX} · _CAP_STALE_DAYS = {_CAP_STALE_DAYS}')

# ── 1~2. 등급별 접기 규칙 ────────────────────────────────────────────────
TODAY_STR = _TODAY.strftime('%Y%m%d')


def build_items(key, n):
    """server.py 의 신고가 조립과 같은 순서로 items 를 만든다."""
    got = [{'name': f'종목{i:03d}', 'change_pct': 1.0 + i / 100,
            'market_cap': 5_000_000_000_000, 'market_cap_updated': TODAY_STR,
            'sector': '은행'} for i in range(n)]
    cap_n = _NH_LIST_MAX.get(key)
    shown = got if cap_n is None else got[:cap_n]
    items = [
        f"  {g['name']} {(g['change_pct'] or 0):+.1f}%"
        f"{_fmt_cap(g['market_cap'], g['market_cap_updated'])}"
        f" — {g['sector'] or '?'}"
        for g in shown
    ]
    if len(got) > len(shown):
        items.append(f'  … 외 {len(got) - len(shown)}종목')
    return got, items


# 세 등급 모두 전 종목이다. 60일은 2026-09-18 에 접기를 풀었다 — 접힌 이름은
# 메시지 어디에서도 볼 수 없었고, 길이는 줄 경계 분할이 이미 감당한다.
for key, label in (('hist', '역사적'), ('w52', '52주'), ('d60', '60일')):
    want(_NH_LIST_MAX.get(key) is None,
         f'{label}: 상한이 {_NH_LIST_MAX.get(key)} 다 — 전 종목이어야 한다')
    got, items = build_items(key, 37)
    want(len(items) == 37, f'{label}: 37종목인데 {len(items)}줄만 나왔다')
    want(not any('외 ' in it and '종목' in it for it in items),
         f"{label} 가 '외 N종목' 으로 접혔다 — 전 종목이어야 한다")
    want(all(f'종목{i:03d}' in '\n'.join(items) for i in range(37)),
         f'{label}: 빠진 종목이 있다')

# 접기 코드는 지웠지 않고 쉬고 있다. 상한을 다시 넣으면 접히고 접은 수를 적어야
# 한다 — 안 그러면 '다시 접으면 된다' 는 주석이 거짓말이 된다.
_saved = _NH_LIST_MAX.get('d60')
_NH_LIST_MAX['d60'] = 5
try:
    got, items = build_items('d60', 30)
    want(len(items) == 6, f'상한 5를 넣었는데 {len(items)}줄이다')
    want(items[-1] == '  … 외 25종목', f'접은 수를 안 적었다 — {items[-1]!r}')
    _, few = build_items('d60', 3)
    want(len(few) == 3 and not any('외 ' in it for it in few),
         '3종목뿐인데 요약 줄이 붙었다')
finally:
    _NH_LIST_MAX['d60'] = _saved

# ── 3. 시총 신선도 ───────────────────────────────────────────────────────
fresh = TODAY_STR
stale = (_TODAY - timedelta(days=_CAP_STALE_DAYS + 1)).strftime('%Y%m%d')
edge = (_TODAY - timedelta(days=_CAP_STALE_DAYS)).strftime('%Y%m%d')

want(_fmt_cap(45_756_680_400_400, fresh) == ' [45.8조]',
     f'오늘 시총에 군더더기가 붙었다: {_fmt_cap(45_756_680_400_400, fresh)!r}')
want(_fmt_cap(45_756_680_400_400, stale) == ' [45.8조*]',
     f'묵은 시총에 * 가 없다: {_fmt_cap(45_756_680_400_400, stale)!r}')
want(_fmt_cap(45_756_680_400_400, edge) == ' [45.8조]',
     f'{_CAP_STALE_DAYS}일째는 아직 낡지 않았다: {_fmt_cap(45_756_680_400_400, edge)!r}')
want(_fmt_cap(45_756_680_400_400, None) == ' [45.8조*]',
     '언제 것인지 모르는 시총을 오늘 값으로 쳤다')
want(_fmt_cap(45_756_680_400_400, '') == ' [45.8조*]', '빈 날짜를 오늘로 쳤다')
want(_fmt_cap(45_756_680_400_400, 'garbage') == ' [45.8조*]',
     '형식이 깨진 날짜를 오늘로 쳤다')
# 값이 없으면 지어내지 않는다
want(_fmt_cap(0, fresh) == '' and _fmt_cap(None, fresh) == '',
     '시총이 없는데 무언가를 적었다')
# 단위 분기 — 입력은 **원**이다. 1조 미만은 억, 이상은 조.
want(_fmt_cap(329_500_000_000, fresh) == ' [3295억]',
     f'3,295억이 억으로 안 나온다: {_fmt_cap(329_500_000_000, fresh)!r}')
want(_fmt_cap(174_600_000_000, fresh) == ' [1746억]',
     f'1,746억이 억으로 안 나온다: {_fmt_cap(174_600_000_000, fresh)!r}')
want(_fmt_cap(999_900_000_000, fresh).endswith('억]'), '1조 미만인데 조로 적었다')
want(_fmt_cap(1_000_000_000_000, fresh) == ' [1.0조]', '정확히 1조가 조로 안 나온다')
want(_fmt_cap(2_015_366_372_700, fresh) == ' [2.0조]',
     f'원 단위 환산이 틀렸다: {_fmt_cap(2_015_366_372_700, fresh)!r}')
# 억 단위 값(KIS hts_avls)이 실수로 흘러들면 눈에 띄게 작게 나온다는 것을 못 박는다.
# 45.8조를 '억' 으로 넣으면(457,566) 1e8 미만이라 아무것도 안 찍힌다 — 조용히
# 틀린 숫자가 나가는 것보다 낫다.
want(_fmt_cap(457_566, fresh) == '',
     '억 단위 값이 원 단위인 척 통과했다 — 1e8 배 어긋난 시총이 나간다')

# ── 4. 분할 ─────────────────────────────────────────────────────────────
head = '<b>09/15 🌙 장마감 확정 시황</b>\n'
body = '\n'.join(
    f'  종목{i:03d} +1.5% [45.8조] — 은행' for i in range(400))
msg = head + body
chunks = _split(msg)

want(len(chunks) > 1, f'{len(msg)}자인데 나뉘지 않았다')
want(all(len(c) <= 3900 for c in chunks),
     f'조각이 한도를 넘었다: {[len(c) for c in chunks]}')

rejoined = '\n'.join(chunks)
# 종목 줄이 하나도 사라지지 않았는가
for i in range(400):
    if f'  종목{i:03d} +1.5% [45.8조] — 은행' not in rejoined:
        want(False, f'종목{i:03d} 줄이 분할에서 사라졌거나 깨졌다')
        break
want(rejoined == msg, '분할 후 이어 붙이면 원본과 같아야 한다')

# 줄 중간에서 자르지 않았는가 — 모든 조각의 모든 줄이 온전한 종목 줄이어야
for ci, c in enumerate(chunks):
    for ln in c.split('\n'):
        if ln.startswith('  종목'):
            want(re.fullmatch(r'  종목\d{3} \+1\.5% \[45\.8조\] — 은행', ln) is not None,
                 f'{ci + 1}번째 조각에 깨진 줄이 있다: {ln!r}')

# 짧은 본문은 그대로 한 건
want(_split('한 줄') == ['한 줄'], '짧은 본문을 괜히 건드렸다')

# 한 줄이 통째로 한도를 넘는 병적인 경우에도 죽지 않는다
huge = _split('x' * 9000)
want(len(huge) >= 2 and ''.join(huge) == 'x' * 9000, '초장문 한 줄 처리가 깨졌다')

# ── 5. 시총 UPSERT — 낡은 값이 새 값을 덮지 못한다 ───────────────────────
# Render 는 영속 디스크가 없어 재시작마다 cache/ 가 비고 시드(6월)로 되돌아간다.
# 그때 시드 시총이 폴링으로 받아 둔 오늘 시총을 덮으면 원점이다.
import sqlite3  # noqa: E402

_m = re.search(
    r'"INSERT INTO stocks "\s*\n\s*"\(code, name, market, sector, market_cap, '
    r'market_cap_updated, ".*?"  updated_at = datetime\(\'now\'\)"', SRC, re.S)
want(_m is not None, '시총 UPSERT 문을 server.py 에서 못 찾았다')
if _m:
    UPSERT = eval('(' + _m.group(0) + ')')
    cx = sqlite3.connect(':memory:')
    cx.execute("""CREATE TABLE stocks (code TEXT PRIMARY KEY, name TEXT, market TEXT,
      sector TEXT, market_cap REAL, market_cap_updated TEXT, close REAL,
      change_pct REAL, volume_mn REAL, sectors_json TEXT, after_hours_price REAL,
      after_hours_change_pct REAL, after_hours_status TEXT, after_hours_time TEXT,
      updated_at TEXT)""")

    def _up(cap, asof, close):
        cx.execute(UPSERT, ('005930', '삼성전자', '', '반도체', cap, asof,
                            close, 1.0, 100, '[]', None, None, None, None))

    def _row():
        return cx.execute(
            'SELECT market_cap, market_cap_updated, close FROM stocks').fetchone()

    SEED_CAP, POLL_CAP = 2_107_583_438_184_000, 500_000_000_000_000
    _up(SEED_CAP, '20260602', 70000)          # 빈 DB → 시드로 시드
    want(_row()[0] == SEED_CAP, '빈 테이블에 시드 시총이 안 들어갔다')

    _up(POLL_CAP, '20260915', 85000)          # 오늘 폴링이 진짜 값을 준다
    want(_row()[:2] == (POLL_CAP, '20260915'), '오늘 폴링 값으로 갱신되지 않는다')

    _up(SEED_CAP, '20260602', 86000)          # 재시작 → 시드가 되돌리려 시도
    want(_row()[:2] == (POLL_CAP, '20260915'),
         f'6월 시드가 9월 시총을 덮었다 — {_row()}')
    want(_row()[2] == 86000, '시총은 지켰는데 가격까지 안 바뀌면 안 된다')

    _up(None, None, 87000)                    # 폴링이 시총을 못 줌
    want(_row()[:2] == (POLL_CAP, '20260915'),
         f'시총 NULL 이 기존 값을 지웠다 — {_row()}')
    want(_row()[2] == 87000, '시총이 없을 때 가격 갱신이 막혔다')


# ── 실제 메시지 모양 한 번 찍어 본다 ──────────────────────────────────────
print('---')
_, hist_items = build_items('hist', 3)
_, w52_items = build_items('w52', 11)
_, d60_items = build_items('d60', 27)
print('🏔 역사적 신고가 3종목');  print('\n'.join(hist_items))
print('📈 52주 신고가 11종목');   print('\n'.join(w52_items[:3] + ['  …(이하 생략, 실제로는 11줄 전부)']))
print('📊 60일 신고가 27종목');   print('\n'.join(d60_items))
print('---')

print('통과' if ok else '실패')
sys.exit(0 if ok else 1)
