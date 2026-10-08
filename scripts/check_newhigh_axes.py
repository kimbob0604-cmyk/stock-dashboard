"""신고가 3축의 경계 사례를 못 박는다 (정의 2026-10-08, newhigh.py).

  1. 120번째 이전 거래일은 창에 들고, 121번째는 안 든다 (KRX 거래일 달력)
  2. 정확히 364일 전 봉은 52주 창에 들고, 365일 전 봉은 안 든다
  3. 공휴일이 낀 주 — 달력이 휴장일을 건너뛰고, 받아 둔 일봉이 표를 바로잡는다
  4. 거래정지 기간이 창 안에 있어도 창이 늘어나지 않는다
  5. 상장 100거래일 신규상장 — 120일·52주는 판정 불가(None), 역사적은 가능
  6. 상장 이후 이력을 못 받으면 역사적이라 부르지 않는다 (보류 + 사유)
  7. 당일 봉이 창에 섞이지 않는다
  8. 우선순위 hist > w52 > d120 · 한 종목은 한 줄에만
  9. 역사적 표: 앞으로 굴리기 · 수정주가 바뀌면 다시 받기
 10. 비교는 엄격히 '>' — 창 최고와 같은 가격은 어느 축에서도 신고가가 아니다
 11. 원천 바닥(1990-01-03)에 닿은 이력은 역사적 판정 가능 + 기준 줄에 한 번 적는다
"""
import sqlite3
import sys
import time
import datetime as dt

import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import krx_calendar                                              # noqa: E402
import newhigh as nh                                             # noqa: E402

ok = True
n_checks = 0


def want(cond, msg):
    global ok, n_checks
    n_checks += 1
    if not cond:
        ok = False
        print('FAIL —', msg)


TODAY = '2026-09-15'
CAL = krx_calendar.Calendar()

# ── 설정이 한 곳에 숫자로 있다 ────────────────────────────────────────────
want(nh.D120_LOOKBACK_TRADING_DAYS == 120, '120일 축이 120거래일이 아니다')
want(nh.W52_LOOKBACK_CALENDAR_DAYS == 364, '52주 축이 달력 364일이 아니다')
want([k for k, _, _ in nh.AXES] == ['hist', 'w52', 'd120'], f'축/우선순위가 다르다: {nh.AXES}')
want([lbl for _, lbl, _ in nh.AXES] == ['역사적', '52주', '120일'], '축 이름이 다르다')

# ── 1. 120번째 / 121번째 이전 거래일 ───────────────────────────────────────
tds = CAL.trading_days_before(TODAY, 121)
want(tds[0] == '2026-09-14', f'전 거래일이 {tds[0]}')
# 기대값은 네이버 일봉(삼성전자 — 결측 없음)에서 2026-09-15 직전 120·121번째 봉의
# 날짜를 실제로 세어 얻은 값이다(2026-10-08 확인).
want(tds[119] == '2026-03-23', f'120번째 이전 거래일이 {tds[119]} (실제 일봉 기준 2026-03-23)')
want(tds[120] == '2026-03-20', f'121번째 이전 거래일이 {tds[120]} (실제 일봉 기준 2026-03-20)')
# 그 사이에 낀 휴장일(5/1·5/5·5/25·6/3·7/17·8/17)은 세지 않는다
for h in ('2026-05-01', '2026-05-05', '2026-05-25', '2026-06-03', '2026-07-17', '2026-08-17'):
    want(h not in tds, f'휴장일 {h} 을 거래일로 셌다')
W = nh.windows(TODAY, CAL)
want(W['d120_start'] == '2026-03-23' and W['w52_start'] == '2025-09-16', f'창 경계: {W}')

# ── 3. 공휴일이 낀 주 (2026 추석: 9/24·9/25 — 9/26 토요일이라 대체휴일 없음) ──
want(CAL.trading_days_before('2026-09-30', 4) == ['2026-09-29', '2026-09-28', '2026-09-23', '2026-09-22'],
     f"추석 주 전 거래일: {CAL.trading_days_before('2026-09-30', 4)}")
W930 = nh.windows('2026-09-30', CAL)
want(W930['d120_start'] == '2026-04-03' and W930['prev_day'] == '2026-09-29',
     f'추석 뒤 창: {W930}')
# 받아 둔 일봉이 표를 바로잡는다 — 표엔 거래일인데 시장 전체가 빈 날은 휴장,
# 표엔 휴장인데 종목 여럿이 봉을 가진 날은 거래일.
obs = {d: 50 for d in CAL.trading_days_before('2026-09-15', 160)}
obs.pop('2026-07-15')                       # 수요일 — 시장 전체가 비었다
obs['2026-08-17'] = 50                      # 표엔 대체공휴일 — 실제로 장이 섰다
cal2 = krx_calendar.Calendar(obs)
want(not cal2.is_trading_day('2026-07-15'), '시장 전체가 빈 평일을 거래일로 본다')
want(cal2.is_trading_day('2026-08-17'), '종목 50개가 봉을 가진 날을 휴장으로 본다')
want(nh.windows(TODAY, cal2)['d120_start'] == '2026-03-23',
     '하루 빼고 하루 더했으니 창 첫날은 그대로여야 한다')
want(('2026-07-15', '거래→휴장') in cal2.corrections and
     ('2026-08-17', '휴장→거래') in cal2.corrections, f'바로잡은 날을 안 남긴다: {cal2.corrections}')
# 받아 둔 구간 밖은 표만 따른다 — 데이터가 없는 것을 휴장으로 읽지 않는다
want(cal2.is_trading_day('2025-03-04'), '받아 둔 구간 밖 평일을 휴장으로 봤다')


# ── 합성 DB ──────────────────────────────────────────────────────────────
def new_db():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.executescript("""
    CREATE TABLE stocks (code TEXT PRIMARY KEY, name TEXT, market TEXT DEFAULT '',
      sector TEXT, market_cap REAL, close REAL, change_pct REAL, volume_mn REAL,
      is_etf INTEGER DEFAULT 0, market_cap_updated TEXT);
    CREATE TABLE ohlcv (code TEXT, date TEXT, open REAL, high REAL, low REAL,
      close REAL, volume REAL, PRIMARY KEY (code, date));
    """)
    return c


_t = dt.date.fromisoformat(TODAY)
DAYS = [d.isoformat() for d in (_t - dt.timedelta(days=i) for i in range(424, 0, -1))
        if CAL.is_trading_day(d)]
HIST: dict = {}


def add(conn, code, name, close, closes: dict, extra_hist=(), volume=100):
    conn.execute("INSERT INTO stocks VALUES (?,?,'KOSPI','테스트',1e12,?,1.0,?,0,'20260915')",
                 (code, name, close, volume))
    rows = [(code, d, c, c, c, c, 1) for d, c in sorted(closes.items())]
    conn.executemany('INSERT INTO ohlcv VALUES (?,?,?,?,?,?,?)', rows)
    HIST[code] = list(extra_hist) + rows


def fetch_ok(code, start, end):
    end_iso = f'{end[:4]}-{end[4:6]}-{end[6:8]}'
    return 'naver', [r for r in HIST.get(code, []) if r[1] <= end_iso], []


def flat(v=9000, **over):
    s = {d: v for d in DAYS}
    s.update(over)
    return s


def run(conn, fetch=fetch_ok, today=TODAY, budget=10):
    r = nh.compute(conn, today, 1e11, fetch=fetch, budget_s=budget, cal=CAL)
    assert r['error'] is None, r['error']
    return r


def where(res, code):
    return [k for k, v in res['buckets'].items() if any(x['code'] == code for x in v)]


old_peak = [('x', '2015-01-05', 0, 0, 0, 30000, 1)]


def with_old_peak(code):
    return [(code, d, c, c, c, c, v) for _, d, _, _, _, c, v in old_peak]


db = new_db()
# 1. 120번째 이전 거래일(3/23)의 20,000 은 창 안 → 9,500 은 120일 신고가가 아니다
add(db, '100120', '경계120안', 9500, flat(**{'2026-03-23': 20000}), with_old_peak('100120'))
# 121번째(3/20)의 20,000 은 창 밖 → 120일 신고가
add(db, '100121', '경계121밖', 9500, flat(**{'2026-03-20': 20000}), with_old_peak('100121'))
# 2. 정확히 364일 전(2025-09-16) 20,000 → 창 안 → 52주 아님 (120일은 뚫음)
add(db, '200364', '경계364안', 9500, flat(**{'2025-09-16': 20000}), with_old_peak('200364'))
# 365일 전(2025-09-15) 20,000 → 창 밖 → 52주 신고가 (상장 이후로는 아님)
add(db, '200365', '경계365밖', 9500, flat(**{'2025-09-15': 20000}), with_old_peak('200365'))
# 4. 거래정지: 125번째 이전 거래일에 20,000, 창 안 30거래일 정지. 창은 늘어나지
#    않으므로 20,000 은 여전히 창 밖 → 120일 신고가. (종목 봉 120개를 세면 20,000
#    이 창에 들어와 '아님' 이 된다 — 옛 방식이 틀리는 자리)
tds125 = CAL.trading_days_before(TODAY, 125)
susp = set(CAL.trading_days_before(TODAY, 100)[60:90])
s4 = {d: v for d, v in flat(**{tds125[124]: 20000}).items() if d not in susp}
add(db, '300000', '정지창안', 9500, s4, with_old_peak('300000'))
# 창 첫날 무렵(111~130번째 이전 거래일) 거래정지였던 종목도 그 전부터 있었으면
# 120일을 판정한다. 52주 안(2025-12-01)의 20,000 때문에 52주는 아니다.
susp2 = set(CAL.trading_days_before(TODAY, 130)[110:130])
add(db, '300001', '정지창첫날', 9500,
    {d: v for d, v in flat(**{'2025-12-01': 20000}).items() if d not in susp2},
    with_old_peak('300001'))
# 5. 상장 100거래일 신규상장 — 상장 이후 최고 9,000 → 9,500 이면 역사적
new100 = CAL.trading_days_before(TODAY, 100)
add(db, '400000', '신규상장', 9500, {d: 9000 for d in new100})
# 신규상장인데 상장 직후 고점이 더 높다 → 아무 줄에도 안 든다 (지어내지 않는다)
add(db, '400001', '신규하락', 9500, {d: (15000 if d == new100[-1] else 9000) for d in new100})
# 7. 당일 봉이 이미 들어와 있다(30,000) — 창에 섞이면 신고가가 아니게 된다
add(db, '500000', '당일봉', 9500, flat(), with_old_peak('500000'))
db.execute(f"INSERT INTO ohlcv VALUES ('500000','{TODAY}',30000,30000,30000,30000,1)")
# 8. 상장 이후 최고 → 역사적. 52주·120일도 뚫었지만 한 줄에만
add(db, '600000', '세축모두', 9500, flat(), [('600000', '2012-01-02', 1, 1, 1, 5000, 1)])

res = run(db)
want(where(res, '100120') == [], f"120번째 거래일 봉이 창 밖으로 샜다: {where(res, '100120')}")
want(where(res, '100121') == ['d120'], f"121번째 거래일 봉이 창에 들었다: {where(res, '100121')}")
want(where(res, '200364') == ['d120'], f"364일 전 봉이 52주 창 밖으로 샜다: {where(res, '200364')}")
want(where(res, '200365') == ['w52'], f"365일 전 봉이 52주 창에 들었다: {where(res, '200365')}")
want(where(res, '300000') == ['d120'], f"거래정지로 창이 늘어났다: {where(res, '300000')}")
want(where(res, '300001') == ['d120'], f"창 첫날 정지였던 종목을 판정 못 했다: {where(res, '300001')}")
rows = {r['code']: r for v in res['buckets'].values() for r in v}
nl = rows.get('400000')
want(where(res, '400000') == ['hist'], f"신규상장이 역사적이 아니다: {where(res, '400000')}")
want(nl is not None and nl['d120'] is None and nl['w52'] is None,
     f"상장 100거래일인데 120일·52주를 판정했다: {nl and (nl['d120'], nl['w52'])}")
want(where(res, '400001') == [], f"상장 직후 고점 아래인데 신고가로 잡혔다: {where(res, '400001')}")
want(where(res, '500000') == ['w52'], f"당일 봉이 창에 섞였다: {where(res, '500000')}")
want(where(res, '600000') == ['hist'], f"세 축 모두 뚫은 종목: {where(res, '600000')}")
codes_all = [r['code'] for v in res['buckets'].values() for r in v]
want(len(codes_all) == len(set(codes_all)), f'한 종목이 두 줄에 나왔다: {codes_all}')
want(res['hist_pending'] == [], f"보류가 생겼다: {res['hist_pending']}")

# ── 6. 상장 이후 이력을 못 받으면 역사적이 아니다 ───────────────────────────
db6 = new_db()
add(db6, '700000', '이력실패', 9500, flat())                  # 52주 뚫음 · 역사적일 수도
add(db6, '700001', '신규이력실패', 9500, {d: 9000 for d in new100})
add(db6, '700002', '이력짧음', 9500, flat())


def fetch_fail(code, start, end):
    if code == '700002':                                     # 받아 둔 일봉보다 짧은 이력
        return 'naver', [r for r in HIST[code] if r[1] >= '2026-01-02'], []
    raise TimeoutError('naver 20s')


r6 = run(db6, fetch=fetch_fail)
want(where(r6, '700000') == ['w52'], f"이력을 못 받았는데 역사적이 됐다/사라졌다: {where(r6, '700000')}")
want(where(r6, '700001') == [], f"신규상장 이력 실패인데 줄에 들었다: {where(r6, '700001')}")
want(where(r6, '700002') == ['w52'], f"상장일까지 안 닿는 이력으로 역사적이라 불렀다: {where(r6, '700002')}")
pend = {c: why for c, _, why in r6['hist_pending']}
want(set(pend) == {'700000', '700001', '700002'}, f'보류 목록: {pend}')
want('TimeoutError' in pend.get('700000', ''), f'보류 사유를 안 남긴다: {pend}')
want('짧다' in pend.get('700002', ''), f'짧은 이력 사유: {pend}')
line = nh.basis_line('시총 1,000억 이상 3종목 대상', r6)
want('역사적 판정 보류 3종목' in line, f'기준 줄에 보류 수가 없다: {line}')
want(db6.execute('SELECT COUNT(*) FROM alltime_high').fetchone()[0] == 0,
     '실패한 이력이 표에 들어갔다')
# 시간 예산을 넘기면 기다리지 않고 보류로 돌린다
db6b = new_db()
add(db6b, '710000', '느린소스', 9500, flat())
t0 = time.time()
r6b = run(db6b, fetch=lambda c, s, e: (time.sleep(3), fetch_ok(c, s, e))[1], budget=0.5)
want(time.time() - t0 < 2.5, f'시간 예산을 안 지켰다: {time.time() - t0:.1f}초')
want(where(r6b, '710000') == ['w52'] and '예산' in (r6b['hist_pending'] or [('', '', '')])[0][2],
     f"예산 초과 처리: {where(r6b, '710000')} {r6b['hist_pending']}")

# ── 9. 역사적 표 — 앞으로 굴리기 · 수정주가 재조정 ─────────────────────────
db9 = new_db()
add(db9, '800000', '굴리기', 9500, flat(), with_old_peak('800000'))   # 옛 고점 30,000
calls = []


def counting(code, s, e):
    calls.append(code)
    return fetch_ok(code, s, e)


run(db9, fetch=counting)
want(calls == ['800000'], f'처음엔 한 번 받아야 한다: {calls}')
row = db9.execute("SELECT max_close, through_date, ref_close FROM alltime_high").fetchone()
want(tuple(row) == (30000, DAYS[-1], 9000), f'표 값: {tuple(row)}')
# 다음 거래일(9/16)에 31,000 마감 → 9/17 판정에서 ohlcv 로만 굴린다(다시 안 받는다)
db9.execute("INSERT INTO ohlcv VALUES ('800000','2026-09-16',31000,31000,31000,31000,1)")
db9.execute("UPDATE stocks SET close = 31500 WHERE code = '800000'")
r9 = run(db9, fetch=counting, today='2026-09-17')
want(calls == ['800000'], f'굴리면 되는데 다시 받았다: {calls}')
want(where(r9, '800000') == ['hist'], f"31,500 은 상장 이후 최고(31,000) 이상이다: {where(r9, '800000')}")
row = db9.execute("SELECT max_close, through_date FROM alltime_high").fetchone()
want(tuple(row) == (31000, '2026-09-16'), f'굴린 뒤 표 값: {tuple(row)}')
# 수정주가가 바뀌었다(1:2 분할 — ohlcv 를 새로 받아 종가가 절반) → 다시 받는다
db9.execute("UPDATE ohlcv SET close = close / 2 WHERE code = '800000'")
HIST['800000'] = [(c, d, o / 2, h / 2, lo / 2, cl / 2, v) for c, d, o, h, lo, cl, v in HIST['800000']]
HIST['800000'].append(('800000', '2026-09-16', 15500, 15500, 15500, 15500, 1))
# 분할 뒤 16,000 마감 — 수정주가 기준 상장 이후 최고(15,500) 위다. 표의 옛 값
# (31,000)을 그대로 믿으면 52주로 떨어진다.
db9.execute("UPDATE stocks SET close = 16000 WHERE code = '800000'")
r9b = run(db9, fetch=counting, today='2026-09-17')
want(calls == ['800000', '800000'], f'수정주가가 바뀌었는데 다시 안 받았다: {calls}')
want(where(r9b, '800000') == ['hist'], f"분할 뒤 16,000 ≥ 15,500 인데 역사적이 아니다: {where(r9b, '800000')}")
row = db9.execute("SELECT max_close, ref_close FROM alltime_high").fetchone()
want(tuple(row) == (15500, 15500), f'다시 받은 뒤 표 값: {tuple(row)}')

# ── 10. 같은 가격은 신고가가 아니다 (엄격히 '>') ─────────────────────────
db10 = new_db()
add(db10, '900000', '같은값52주', 9000, flat())                 # 52주·120일 최고 = 9,000
add(db10, '900001', '같은값120일', 9000, flat(**{'2025-12-01': 20000}))
add(db10, '900002', '같은값역사적', 9500, flat(),
    [('900002', '2012-01-02', 1, 1, 1, 9500, 1)])              # 상장 이후 최고 = 9,500
add(db10, '900003', '같은값신규', 9000, {d: 9000 for d in new100})
add(db10, '900004', '한틱위', 9001, flat())                     # 1원이라도 넘으면 신고가
r10 = run(db10)
want(where(r10, '900000') == [], f"52주 최고와 같은 가격이 신고가가 됐다: {where(r10, '900000')}")
want(where(r10, '900001') == [], f"120일 최고와 같은 가격이 신고가가 됐다: {where(r10, '900001')}")
want(where(r10, '900002') == ['w52'],
     f"상장 이후 최고와 같은 가격을 역사적이라 불렀다: {where(r10, '900002')}")
want(where(r10, '900003') == [], f"신규상장 최고와 같은 가격이 신고가가 됐다: {where(r10, '900003')}")
want(where(r10, '900004') == ['hist'], f"1원 위인데 신고가가 아니다: {where(r10, '900004')}")

# ── 11. 원천 바닥(1990-01-03) ──────────────────────────────────────────────
want(nh.HIST_SOURCE_FLOOR == '1990-01-03', f'원천 바닥: {nh.HIST_SOURCE_FLOOR}')
db11 = new_db()
# 1975 상장 종목 — 원천은 1990-01-03 부터만 준다. 바닥에 닿았으니 역사적 판정 가능.
add(db11, '920000', '구상장', 9500, flat(), [('920000', '1990-01-03', 1, 1, 1, 3000, 1)])
r11 = run(db11)
want(where(r11, '920000') == ['hist'], f"원천 바닥에 닿은 이력으로 역사적 판정을 못 했다: {where(r11, '920000')}")
l11 = nh.basis_line('범위', r11)
want(l11.count(nh.HIST_FLOOR_NOTE) == 1 and '1990년 이전 상장은 1990-01-03 이후 최고가' in l11,
     f'1990 이전 상장 표기가 한 번 있어야 한다: {l11}')
# 2000 년 상장 종목만 역사적이면 그 표기는 없다
db11b = new_db()
add(db11b, '920001', '신상장', 9500, flat(), [('920001', '2000-05-02', 1, 1, 1, 3000, 1)])
add(db11b, '920002', '구상장52주', 9500, flat(),               # 1990 이전 상장이지만 52주 줄
    [('920002', '1990-01-03', 1, 1, 1, 30000, 1)])
r11b = run(db11b)
want(where(r11b, '920001') == ['hist'] and where(r11b, '920002') == ['w52'], '바닥 표기 시험 준비')
want(nh.HIST_FLOOR_NOTE not in nh.basis_line('범위', r11b),
     '1990 이전 상장 종목이 역사적 줄에 없는데 표기가 붙었다')
# 표에서 다시 읽어도(이력을 새로 안 받아도) 같은 표기
r11c = run(db11)
want(nh.HIST_FLOOR_NOTE in nh.basis_line('범위', r11c), '표에서 읽은 이력은 바닥 표기를 잃는다')

print(f'검사 {n_checks}개')
print('통과' if ok else '실패')
sys.exit(0 if ok else 1)
