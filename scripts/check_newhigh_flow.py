"""52주 신고가 상위 5종목에 수급이 붙는지 server.py 에서 뽑아 돌려 본다.

server.py 는 Flask 앱이라 import 하지 않는다(다른 check_*.py 와 같은 이유).
고친 함수의 본문을 소스에서 떼어 가짜 flow_cache 위에 태우고 규칙만 못 박는다.

  1. 52주는 상위 5종목에만 붙는다 — 6번째 줄부터는 안 붙는다.
  2. '상위' 는 목록과 같은 거래대금 순이다. 화면의 1~5번째 줄과 겹쳐야 한다.
  3. 수급을 못 받은 종목은 '수급 없음' 이라 적는다 — 0 으로 채우지 않는다.
  4. 수급 기준일이 오늘이 아니면 머리말이 그 사실을 적는다.
  5. 역사적·60일은 기본으로 안 붙는다(_NH_FLOW_MAX 가 0).
"""
import re
import sys

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


# ── 설정 ────────────────────────────────────────────────────────────────
ns: dict = {}
exec(grab(r'\n_NH_FLOW_MAX = \{[^}]*\}', '_NH_FLOW_MAX').group(0), ns)
_NH_FLOW_MAX = ns['_NH_FLOW_MAX']
print(f'_NH_FLOW_MAX = {_NH_FLOW_MAX}')
want(_NH_FLOW_MAX.get('w52') == 5, f"52주가 {_NH_FLOW_MAX.get('w52')}종목이다 — 5 여야 한다")
want(not _NH_FLOW_MAX.get('hist'), '역사적에 기본으로 수급이 붙는다')
want(not _NH_FLOW_MAX.get('d60'), '60일에 기본으로 수급이 붙는다')

# ── _fmt_nh_flow 본문을 그대로 떼어 온다 ────────────────────────────────
fmt = grab(r'\ndef _fmt_nh_flow\(.*?\n(?=\ndef )', '_fmt_nh_flow')
exec(fmt.group(0), ns)
_fmt_nh_flow = ns['_fmt_nh_flow']

# 3. 못 받은 종목
want('수급 없음' in _fmt_nh_flow(None, '2026-09-18'),
     "수급을 못 받았는데 '수급 없음' 이라 안 적는다")
want('0억' not in _fmt_nh_flow(None, '2026-09-18'),
     '못 받은 것을 0 으로 채웠다')

# 값 표기
line = _fmt_nh_flow((12.3, -4.5, '2026-09-18'), '2026-09-18')
want('외인 +12억' in line, f'외인 표기가 이상하다 — {line!r}')
want('기관 -5억' in line or '기관 -4억' in line, f'기관 표기가 이상하다 — {line!r}')
# 한쪽만 있는 경우
half = _fmt_nh_flow((7.0, None, '2026-09-18'), '2026-09-18')
want('기관 —' in half, f'없는 쪽을 0 으로 적었다 — {half!r}')
# 그 종목만 날짜가 다르면 줄에 적는다
odd = _fmt_nh_flow((1.0, 2.0, '2026-09-17'), '2026-09-18')
want('09/17' in odd, f'종목별 날짜 차이를 안 적었다 — {odd!r}')

# ── 1~2. 상위 N 만, 목록과 같은 순서 ────────────────────────────────────
blk = grab(r'n_flow = _NH_FLOW_MAX\.get\(key\).*?items\.append\(line\)', '조립부').group(0)
want('shown[:n_flow]' in blk, '수급을 목록과 다른 집합에서 뽑는다')
want('for i, g in enumerate(shown)' in blk and 'if i < n_flow:' in blk,
     '상위 N 줄에만 붙이는 분기가 없다')
want('sorted(buckets[key]' in SRC and 'volume_mn' in SRC,
     '목록 정렬 기준(거래대금)이 사라졌다')

# 조립부를 가짜 데이터로 돌려 본다
rows = [{'code': f'{i:06d}', 'name': f'종목{i}', 'change_pct': 1.0,
         'market_cap': 5e12, 'market_cap_updated': '20260918',
         'sector': '은행', 'volume_mn': 1000 - i} for i in range(8)]
flow = {'000000': (10.0, 1.0, '2026-09-18'),
        '000001': (-3.0, 2.0, '2026-09-18')}
n_flow = _NH_FLOW_MAX['w52']
items = []
for i, g in enumerate(rows):
    line = f"  {g['name']} {g['change_pct']:+.1f}% — {g['sector']}"
    if i < n_flow:
        line += _fmt_nh_flow(flow.get(g['code']), '2026-09-18')
    items.append(line)
want(sum(1 for it in items if '외인' in it or '수급 없음' in it) == 5,
     f"수급이 붙은 줄이 5개가 아니다 — {[it for it in items if '외인' in it or '수급 없음' in it]}")
want('외인' not in items[5] and '수급 없음' not in items[5],
     f'6번째 줄에도 수급이 붙었다 — {items[5]!r}')
want('외인 +10억' in items[0], f'1번째 줄 수급이 이상하다 — {items[0]!r}')
want('수급 없음' in items[2], f'값 없는 종목을 0 으로 채웠다 — {items[2]!r}')

# ── 4. 머리말의 기준일 ──────────────────────────────────────────────────
want('수급은' in SRC and '당일 확정 전' in SRC,
     '수급 기준일이 오늘이 아닐 때 머리말에 적는 코드가 없다')
want('nh_flow_dates' in SRC, '수급 기준일을 모으는 곳이 없다')

print('---')
print('\n'.join(items[:6]))
print('---')
print('통과' if ok else '실패')
sys.exit(0 if ok else 1)
