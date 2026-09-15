"""워치독이 '언제 따지는지' 를 server.py 원본 그대로 돌려 본다.

server.py 는 Flask 앱이라 import 하지 않는다. 대신 판정에 쓰이는 순수 함수와
상수를 원문에서 뽑아 실행한다 — 여기 숫자를 따로 적어 두면 코드와 갈라진다.

못 박는 것:
  1. 평일 08:00 은 정체를 따지지 않는다  ← 매일 아침 오탐이 나던 자리
  2. 장중에 2시간 넘게 안 바뀌면 정체로 따진다
  3. 15:40 배치 전에는 수급 0행을 따지지 않는다
  4. 휴장일·주말은 둘 다 따지지 않는다
  5. 건강도 함수가 실제로 이 게이트를 거쳐 간다
"""
import re
import datetime as _dt

SRC = open('/home/user/stock-dashboard/server.py', encoding='utf-8').read()

def grab(pattern, what):
    m = re.search(pattern, SRC, re.S | re.M)
    assert m, f'{what} 를 server.py 에서 못 찾았다'
    return m.group(0)

src = '\n\n'.join([
    grab(r'^_WD_STALE_FROM.*?^_WD_FLOW_FROM = \d+.*?$', '워치독 시간대 상수'),
    grab(r'^def _watchdog_checks_due\(.*?\n    \}\n', '_watchdog_checks_due'),
    grab(r'^_KR_HOLIDAYS_2026 = \{.*?^\}\n', '휴장일 표'),
    grab(r'^def _is_kr_holiday\(.*?return dt\.strftime\("%Y-%m-%d"\) in _KR_HOLIDAYS_2026\n',
         '_is_kr_holiday'),
])
ns = {'datetime': _dt.datetime, 'now_kst': lambda: None}
exec(compile(src, 'server.py(발췌)', 'exec'), ns)
due = ns['_watchdog_checks_due']

def at(y, m, d, hh, mm):
    return due(_dt.datetime(y, m, d, hh, mm))

# 1. 평일 아침 — 전날 17:55 이 마지막이라 항상 몇 시간씩 지나 있다
assert at(2026, 9, 15, 8, 0)['stocks_stale'] is False, '평일 08:00 이 아직 정체를 따진다'
assert at(2026, 9, 15, 8, 30)['stocks_stale'] is False
assert at(2026, 9, 15, 9, 30)['stocks_stale'] is False   # 첫 동기화(09:05) 직후는 봐준다

# 2. 갱신이 돌고 있어야 할 시간대
assert at(2026, 9, 15, 10, 0)['stocks_stale'] is True
assert at(2026, 9, 15, 14, 0)['stocks_stale'] is True
assert at(2026, 9, 15, 18, 0)['stocks_stale'] is True
assert at(2026, 9, 15, 18, 30)['stocks_stale'] is False  # 시간외(17:55)가 끝났다
assert at(2026, 9, 15, 20, 0)['stocks_stale'] is False

# 3. 수급은 15:40 배치가 채운다 — 그 전 0행은 재시작 직후의 정상 모습
assert at(2026, 9, 15, 10, 0)['flow_rows'] is False
assert at(2026, 9, 15, 15, 30)['flow_rows'] is False
assert at(2026, 9, 15, 16, 0)['flow_rows'] is False      # 배치가 아직 도는 중
assert at(2026, 9, 15, 16, 30)['flow_rows'] is True
assert at(2026, 9, 15, 19, 0)['flow_rows'] is True

# 4. 휴장일·주말엔 갱신 자체가 없다
for day in ((2026, 9, 24), (2026, 10, 9), (2026, 9, 19), (2026, 9, 20)):
    d = at(*day, 16, 30)
    assert d['trading_day'] is False, f'{day} 를 거래일로 본다'
    assert d['stocks_stale'] is False and d['flow_rows'] is False

# 5. 건강도 함수가 이 게이트를 실제로 쓴다
health = grab(r'^def _check_market_data_health\(.*?\n    return out\n', '_check_market_data_health')
assert 'due = _watchdog_checks_due()' in health, '건강도 함수가 게이트를 부르지 않는다'
assert 'due["flow_rows"] and out["flow_rows"] < 50' in health, '수급 판정이 게이트 밖에 있다'
assert 'due["stocks_stale"] and out["stocks_age_min"]' in health, '정체 판정이 게이트 밖에 있다'
assert 'now_kst().weekday() < 5' not in health, '옛 평일-6시간 판정이 남아 있다'

print('정체 판정 시간대:', ns['_WD_STALE_FROM'], '~', ns['_WD_STALE_TO'],
      f"· {ns['_WD_STALE_MAX_MIN']}분")
print('수급 판정 시작:', ns['_WD_FLOW_FROM'])
print('통과')
