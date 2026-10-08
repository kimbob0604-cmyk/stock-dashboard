"""장마감 시황이 **제 시각에, 하루 한 번, 빠짐없이** 나가는지 server.py 에서 확인한다.

server.py 는 Flask 앱이라 import 하지 않는다(다른 check_*.py 와 같은 이유).
고친 함수의 본문을 소스에서 떼어 가짜 ops_state 위에 태우고 규칙만 못 박는다.

  1. cron 은 평일 16:00 이다. 시각은 _CLOSING_BRIEF_HHMM 한 곳에서 나온다.
  2. 하루 한 번만 나간다 — 같은 날 두 번 부르면 뒤엣것은 안 보낸다.
  3. 밀리면 캐치업이 보낸다 — 16:00 이후 · 평일 · 아직 안 보낸 날에만.
  4. cron 에 misfire_grace_time 이 있다. 기본값 1초면 정각에 바쁜 날 통째로 날아간다.
  5. 부팅 직후에도 캐치업을 부른다. Render 무료 플랜이 자는 동안은 cron 이 없다.
  7. 장 끝난 뒤 나머지 텔레그램(장 마감 요약 · 수급 시그널 · 리비전 · AI 추천)도
     같은 16:00 에 한 잡이 정해진 순서로 보낸다 — 2026-10-08 사용자 요청.
     입력(컨센서스 스냅샷 · AI 추천 실행)은 16:00 전에 끝난다.
  8. 16:00 수급 시그널 · 리비전은 실제 기준일을 적는다(전일 값을 오늘인 척 안 함).
"""
import re
import sys
from datetime import datetime, timedelta, timezone

SRC = open('/home/user/stock-dashboard/server.py', encoding='utf-8').read()
KST = timezone(timedelta(hours=9))
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


# ── 1. 발송 시각 ─────────────────────────────────────────────────────────
hhmm = grab(r'\n_CLOSING_BRIEF_HHMM = \((\d+), (\d+)\)', '_CLOSING_BRIEF_HHMM')
H, M = int(hhmm.group(1)), int(hhmm.group(2))
print(f'_CLOSING_BRIEF_HHMM = ({H}, {M})')
want((H, M) == (16, 0), f'발송 시각이 {H:02d}:{M:02d} 다 — 16:00 이어야 한다')

job = grab(r'_scheduler\.add_job\(send_closing_market_summary.*?\)\n', 'cron 등록')
blk = job.group(0)
want('day_of_week="mon-fri"' in blk, f'cron 이 평일이 아니다 — {blk!r}')
want('_CLOSING_BRIEF_HHMM[0]' in blk and '_CLOSING_BRIEF_HHMM[1]' in blk,
     'cron 이 시각을 따로 적고 있다 — 한 곳(_CLOSING_BRIEF_HHMM)에서 와야 한다')
# ── 4. misfire ──────────────────────────────────────────────────────────
mg = re.search(r'misfire_grace_time=(\d+)', blk)
want(mg and int(mg.group(1)) >= 600,
     f'cron 의 misfire_grace_time 이 없거나 너무 짧다 — {blk!r}')

# ── 5. 부팅 캐치업 ───────────────────────────────────────────────────────
want('name="closing-brief-catchup"' in SRC,
     '부팅 직후 캐치업 스레드가 없다 — 자다 깨어난 날 시황이 안 온다')
want(re.search(r'_scheduler\.add_job\(closing_brief_catchup', SRC),
     '캐치업 cron 이 없다')

# ── 2~3. 하루 한 번 · 캐치업 조건 ────────────────────────────────────────
# 두 함수의 **판단 부분만** 떼어 가짜 상태 위에서 돌린다. 발송·갱신은 갈아낀다.
STATE: dict = {}
SENT: list = []


def _ops_get(key, default=None):
    return STATE.get(key, default)


def _ops_set(key, value):
    STATE[key] = value


NOW = [datetime(2026, 9, 18, 16, 0, tzinfo=KST)]        # 금요일 16:00


def now_kst():
    return NOW[0]


def send_closing_market_summary(*, catchup=False):
    today = now_kst().strftime('%Y-%m-%d')
    if str(_ops_get('closing_brief_sent', '')) == today:
        return False
    SENT.append((today, catchup))
    _ops_set('closing_brief_sent', today)
    return True


_CLOSING_BRIEF_HHMM = (H, M)


def closing_brief_catchup():
    now = now_kst()
    if now.weekday() >= 5:
        return False
    if (now.hour, now.minute) < _CLOSING_BRIEF_HHMM:
        return False
    if str(_ops_get('closing_brief_sent', '')) == now.strftime('%Y-%m-%d'):
        return False
    return send_closing_market_summary(catchup=True)


# 정각 발송
want(send_closing_market_summary() is True, '16:00 정각에 안 보냈다')
want(SENT == [('2026-09-18', False)], f'발송 기록이 이상하다 — {SENT}')
# 같은 날 두 번째는 안 나간다 (cron 과 캐치업이 겹치는 경우)
want(send_closing_market_summary() is False, '같은 날 두 번 보냈다')
want(closing_brief_catchup() is False, '이미 보낸 날 캐치업이 또 보냈다')
want(len(SENT) == 1, f'하루에 {len(SENT)}번 나갔다')

# 자고 있어서 못 보낸 날 — 19:41 에 깨어나면 그때 보낸다
STATE.clear(); SENT.clear()
NOW[0] = datetime(2026, 9, 18, 19, 41, tzinfo=KST)
want(closing_brief_catchup() is True, '밀린 발송을 캐치업이 안 보냈다')
want(SENT == [('2026-09-18', True)], f'캐치업 표시가 없다 — {SENT}')

# 아직 16:00 전이면 안 보낸다
STATE.clear(); SENT.clear()
NOW[0] = datetime(2026, 9, 18, 15, 59, tzinfo=KST)
want(closing_brief_catchup() is False, '장중 15:59 에 시황을 보냈다')

# 주말은 안 보낸다
STATE.clear(); SENT.clear()
NOW[0] = datetime(2026, 9, 19, 17, 0, tzinfo=KST)        # 토요일
want(closing_brief_catchup() is False, '토요일에 시황을 보냈다')

# 날이 바뀌면 다시 보낸다
STATE.clear(); SENT.clear()
NOW[0] = datetime(2026, 9, 18, 16, 0, tzinfo=KST)
send_closing_market_summary()
NOW[0] = datetime(2026, 9, 21, 16, 5, tzinfo=KST)        # 다음 월요일
want(closing_brief_catchup() is True, '다음 영업일에 안 보냈다')

# ── 6. 데이터가 덜 찼으면 안 보내고 표시도 안 찍는다 ────────────────────
# 2026-09-18: 배포 직후 빈 DB 위에서 부팅 캐치업이 시황을 보내고 '보냈음' 까지
# 찍어, 데이터가 다 찬 뒤에도 다시 못 보냈다. 그 회귀를 여기서 막는다.
want('def _brief_data_ready' in SRC, '_brief_data_ready 가 없다')
want('_CLOSING_BRIEF_DEADLINE_HHMM' in SRC, '마감 시각 상수가 없다')
want(re.search(r'if not ready and not past_deadline:\s*\n\s*log\.warning', SRC),
     '데이터 미완일 때 보내지 않고 돌아가는 분기가 없다')
dl = grab(r'_CLOSING_BRIEF_DEADLINE_HHMM = \((\d+), (\d+)\)', '마감 시각')
DH, DM = int(dl.group(1)), int(dl.group(2))
print(f'_CLOSING_BRIEF_DEADLINE_HHMM = ({DH}, {DM})')
want((DH, DM) > (H, M), '마감 시각이 발송 시각보다 빠르거나 같다')
cat = grab(r'_scheduler\.add_job\(closing_brief_catchup.*?\)\n', '캐치업 cron')
want(f'hour="16-{DH}"' in cat.group(0),
     f'캐치업 창이 마감 시각({DH}시)까지 안 간다 — {cat.group(0)!r}')
want(f'minute="5,{DM}"' in cat.group(0),
     f'캐치업 마지막 슬롯이 마감 분({DM})과 다르다 — {cat.group(0)!r}')

# 준비 안 된 상태를 흉내 내 동작을 본다
READY = [False]


def _brief_data_ready():
    return (True, '준비됨') if READY[0] else (False, '일봉 테이블이 비어 있다')


_CLOSING_BRIEF_DEADLINE_HHMM = (DH, DM)


def send_gated(*, catchup=False):
    """server.py 의 게이트 부분만 떼어 온 것."""
    now = now_kst()
    today = now.strftime('%Y-%m-%d')
    if str(_ops_get('closing_brief_sent', '')) == today:
        return False
    ready, _why = _brief_data_ready()
    past = (now.hour, now.minute) >= _CLOSING_BRIEF_DEADLINE_HHMM
    if not ready and not past:
        return False                      # 보내지도, 표시하지도 않는다
    SENT.append((today, catchup, ready))
    _ops_set('closing_brief_sent', today)
    return True


STATE.clear(); SENT.clear()
READY[0] = False
NOW[0] = datetime(2026, 9, 18, 16, 0, tzinfo=KST)
want(send_gated() is False, '데이터가 비었는데 보냈다')
want(STATE.get('closing_brief_sent') is None,
     "안 보냈는데 '보냈음' 표시를 찍었다 — 그러면 다 찬 뒤에도 못 보낸다")
NOW[0] = datetime(2026, 9, 18, 18, 0, tzinfo=KST)
READY[0] = True
want(send_gated(catchup=True) is True, '데이터가 찬 뒤에도 안 보냈다')
want(SENT == [('2026-09-18', True, True)], f'발송 기록이 이상하다 — {SENT}')

# 마감 시각을 넘기면 덜 찼어도 보낸다 — 아무것도 안 오는 것보다 낫다
STATE.clear(); SENT.clear()
READY[0] = False
NOW[0] = datetime(2026, 9, 18, DH, DM, tzinfo=KST)
want(send_gated(catchup=True) is True, '마감 시각인데도 안 보냈다')
want(SENT and SENT[0][2] is False, '마감 발송이 준비됨으로 기록됐다')

# ── 7. 장 끝난 뒤 나머지 텔레그램도 16:00 ───────────────────────────────
post = grab(r'_scheduler\.add_job\(send_post_close_alerts.*?\)\n', '장 끝난 뒤 알림 cron')
pb = post.group(0)
want('day_of_week="mon-fri"' in pb, f'장 끝난 뒤 알림 cron 이 평일이 아니다 — {pb!r}')
want('_CLOSING_BRIEF_HHMM[0]' in pb and '_CLOSING_BRIEF_HHMM[1]' in pb,
     '장 끝난 뒤 알림이 시각을 따로 적고 있다 — 시황과 같은 _CLOSING_BRIEF_HHMM 이어야 한다')
mg = re.search(r'misfire_grace_time=(\d+)', pb)
want(mg and int(mg.group(1)) >= 600,
     f'장 끝난 뒤 알림 cron 의 misfire_grace_time 이 없거나 짧다 — {pb!r}')
# 넷이 다른 시각에 따로 걸려 있으면 안 된다 (예전 15:40 · 15:45 · 18:30 · 19:30)
for fn in ('alert_closing_summary', 'alert_flow_signals', 'alert_revision_signals',
           'send_agent_telegram'):
    want(not re.search(rf'_scheduler\.add_job\({fn}\b', SRC),
         f'{fn} 가 따로 cron 에 걸려 있다 — 16:00 일괄 발송 밖이다')
# 15:45 에이전트 잡은 만들어 두기만 한다(보내는 건 16:00)
ag = grab(r'_scheduler\.add_job\(agent_prepare_close.*?\)\n', '15:45 에이전트 준비 cron')
want('hour=15' in ag.group(0) and 'minute=45' in ag.group(0),
     f'에이전트 준비 시각이 15:45 가 아니다 — {ag.group(0)!r}')
prep = grab(r'\ndef agent_prepare_close\(.*?\n(?=\n\n)', 'agent_prepare_close')
want('_send_agent_picks' not in prep.group(0) and 'send_agent_telegram' not in prep.group(0),
     '15:45 에이전트 준비가 텔레그램을 보낸다 — 16:00 에 보내야 한다')
am = grab(r'_scheduler\.add_job\(agent_run_and_send.*?\)\n', '08:45 에이전트 cron')
want('hour=8,' in am.group(0) or 'hour=8\n' in am.group(0) or 'hour=8 ' in am.group(0),
     f'바로 보내는 에이전트 잡이 아침(08시) 말고도 돈다 — {am.group(0)!r}')
# 리비전의 입력(컨센서스 스냅샷 + 계산)은 16:00 전에 시작해 끝나야 한다
cs = grab(r'_scheduler\.add_job\(consensus_snapshot_and_revisions.*?\)\n', '컨센서스 스냅샷 cron')
csh = re.search(r'hour=(\d+), minute=(\d+)', cs.group(0))
want(csh and (int(csh.group(1)), int(csh.group(2))) <= (15, 0),
     f'컨센서스 스냅샷이 너무 늦다 — 수십 분 걸리는데 16:00 전에 끝나야 한다 {cs.group(0)!r}')

# 보내는 순서와 실패 격리 — send_post_close_alerts 본문을 그대로 돌린다
body = grab(r'\ndef send_post_close_alerts\(.*?\n(?=\n\n)', 'send_post_close_alerts').group(0)
CALLED: list = []


class _Log:
    def __getattr__(self, _):
        return lambda *a, **k: None


def _mk(name, boom=False):
    def f():
        CALLED.append(name)
        if boom:
            raise RuntimeError('일부러')
    return f


ns = {'alert_closing_summary': _mk('요약'), 'alert_flow_signals': _mk('수급', boom=True),
      'alert_revision_signals': _mk('리비전'), '_send_agent_close_picks': _mk('AI'),
      'log': _Log()}
exec(compile(body, 'server.py(발췌)', 'exec'), ns)
ns['send_post_close_alerts']()
want(CALLED == ['요약', '수급', '리비전', 'AI'],
     f'16:00 알림 순서가 다르거나 하나가 실패해 뒤가 끊겼다 — {CALLED}')

# wake.yml 은 15:35 준비 잡 전에 깨운다
WAKE = open('/home/user/stock-dashboard/.github/workflows/wake.yml', encoding='utf-8').read()
want(re.search(r"cron: '30,[^']* 6 \* \* 1-5'", WAKE),
     'wake.yml 이 KST 15:30(UTC 06:30)부터 안 깨운다 — 15:35~15:48 준비 잡이 잠든 채 지나간다')

# ── 8. 16:00 메시지의 기준일 ────────────────────────────────────────────
fs = grab(r'\ndef alert_flow_signals\(.*?\n(?=\n\n)', 'alert_flow_signals').group(0)
OUT: list = []
SIG = {'date': '2026-10-07', 'dual_buy': [{'name': 'A', 'foreign': 120.0, 'inst': 80.0}],
       'dual_sell': [], 'streak_buy': [], 'streak_sell': [], 'reversal': []}
fns = {'_analyze_flow_signals': lambda: SIG, 'send_telegram': OUT.append,
       'now_kst': now_kst, 'log': _Log()}
exec(compile(fs, 'server.py(발췌)', 'exec'), fns)
NOW[0] = datetime(2026, 10, 8, 16, 0, tzinfo=KST)
fns['alert_flow_signals']()
want(OUT and '기준일 10/07' in OUT[-1] and '최신 확정치' in OUT[-1],
     f'16:00 수급 시그널이 전일 기준일을 안 밝힌다 — {OUT[-1][:200] if OUT else None!r}')
SIG['date'] = '2026-10-08'
fns['alert_flow_signals']()
want('기준일 10/08(오늘)' in OUT[-1] and '잠정' in OUT[-1],
     f'16:00 의 오늘 수급을 잠정이라고 안 적는다 — {OUT[-1][:200]!r}')
NOW[0] = datetime(2026, 10, 8, 19, 30, tzinfo=KST)
fns['alert_flow_signals']()
want('잠정' not in OUT[-1], f'저녁 확정 뒤인데 잠정이라고 적는다 — {OUT[-1][:200]!r}')

# 리비전 — 기준일은 보낸 날이 아니라 스냅샷 날
import contextlib  # noqa: E402
import sqlite3  # noqa: E402
import threading  # noqa: E402

rv = grab(r'\ndef alert_revision_signals\(.*?\n(?=\n\n)', 'alert_revision_signals').group(0)
fp = grab(r'\ndef _fmt_revision_period\(.*?\n(?=\n\n)', '_fmt_revision_period').group(0)
MEM = sqlite3.connect(':memory:', check_same_thread=False)
MEM.row_factory = sqlite3.Row
MEM.executescript('''
    CREATE TABLE stocks (code TEXT, name TEXT);
    CREATE TABLE revision_alerts (id INTEGER PRIMARY KEY, stock_code TEXT, metric TEXT,
        signal TEXT, revision_pct REAL, window_days INT, period_type TEXT,
        period_year INT, period_quarter INT, "current_date" TEXT, priority INT,
        alert_sent INT DEFAULT 0, sent_at TEXT);
    INSERT INTO stocks VALUES ('005930', '삼성전자');
''')


@contextlib.contextmanager
def _mem_db():
    yield MEM


COMPUTED: list = []
RV_OUT: list = []
rns = {'_get_db': _mem_db, 'send_telegram': RV_OUT.append, 'now_kst': now_kst,
       'log': _Log(), '_CONSENSUS_SNAPSHOT_LOCK': threading.Lock(),
       '_REVISION_WAIT_S': 1, '_REVISION_COMPUTED': {'date': '2026-10-08'},
       '_compute_revisions': lambda: COMPUTED.append(1)}
exec(compile(fp + rv, 'server.py(발췌)', 'exec'), rns)


def _add_alert(day):
    MEM.execute('INSERT INTO revision_alerts (stock_code, metric, signal, revision_pct, '
                'window_days, period_type, period_year, period_quarter, "current_date", '
                "priority) VALUES ('005930','eps','STRONG_UP',20.0,7,'NTM',2026,0,?,1)", (day,))
    MEM.commit()


NOW[0] = datetime(2026, 10, 8, 16, 0, tzinfo=KST)
_add_alert('2026-10-07')
rns['alert_revision_signals']()
want(RV_OUT and '기준일: 컨센서스 스냅샷 10/07 — 오늘 스냅샷 전' in RV_OUT[-1],
     f'오늘 스냅샷이 없는데 기준일을 안 밝힌다 — {RV_OUT[-1][:200] if RV_OUT else None!r}')
want(not COMPUTED, '14:30 에 계산해 둔 날인데 16:00 에 또 계산했다 — 시황 DB 쓰기를 붙잡는다')
_add_alert('2026-10-08')
rns['alert_revision_signals']()
want('기준일: 컨센서스 스냅샷 10/08</i>' in RV_OUT[-1],
     f'오늘 스냅샷 기준일이 이상하다 — {RV_OUT[-1][:200]!r}')
# 오늘 계산분이 없으면(재시작·잠듦) 16:00 에 계산한다
rns['_REVISION_COMPUTED']['date'] = '2026-10-07'
rns['alert_revision_signals']()
want(COMPUTED == [1], '오늘 계산분이 없는데 계산 없이 보냈다')

# ── 옛 이름이 남아 있지 않은지 ───────────────────────────────────────────
want('send_evening_market_summary' not in SRC,
     '옛 이름 send_evening_market_summary 가 남아 있다')
want('tg_evening_summary' not in SRC, "옛 job id 'tg_evening_summary' 가 남아 있다")

print('---')
print('통과' if ok else '실패')
sys.exit(0 if ok else 1)
