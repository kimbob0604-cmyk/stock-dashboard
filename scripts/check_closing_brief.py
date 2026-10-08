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
  9. 16:00 에 잠들어 있었으면 캐치업이 시황 → 나머지 순으로 보낸다. 알림별로
     보냈는지 적어 두어 같은 날 두 번 안 보내고, 실패한 것만 다시 보낸다.
 10. 텔레그램 429 는 한 번 다시 보낸다 · 긴 본문 조각이 락에 막히지 않는다.
 11. 같은 기준일의 수급 시그널을 이틀 보내지 않는다 · 리비전은 보낸 것만 찍는다.
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

# ── 9. 순서 · 알림별 기록 · 캐치업 — server.py 본문을 그대로 돌린다 ───────
import threading  # noqa: E402
import types  # noqa: E402


class _Log:
    def __getattr__(self, _):
        return lambda *a, **k: None


pc_src = grab(r'\n_POST_CLOSE_KEY = .*?\n(?=\n\n# ── 데이터 정합성 워치독)',
              '_POST_CLOSE_KEY ~ send_post_close_alerts').group(0)
cb_src = grab(r'\ndef closing_brief_catchup\(.*?\n(?=\n\n)', 'closing_brief_catchup').group(0)
want('send_post_close_alerts(catchup=True)' in cb_src,
     '캐치업이 장 끝난 뒤 알림을 안 잡는다 — 16:00 에 자고 있었으면 넷이 통째로 사라진다')

EV: list = []                 # 텔레그램으로 나간 순서
PC_STATE: dict = {}
BRIEF_READY = [True]
STEP_OK = {'요약': True, '수급': True, '리비전': True, 'AI': True}
BACKUPS: list = []
sys.modules['db_backup'] = types.SimpleNamespace(
    backup_db=lambda: BACKUPS.append(1) or {'ok': True})


def _pc_brief(*, catchup=False):
    today = NOW[0].strftime('%Y-%m-%d')
    if PC_STATE.get('closing_brief_sent') == today or not BRIEF_READY[0]:
        return False
    EV.append('시황(지연)' if catchup else '시황')
    PC_STATE['closing_brief_sent'] = today
    return True


def _pc_step(name):
    def f(**kw):
        if name == '수급':
            want(kw == {'scheduled': True}, f'16:00 수급 시그널이 정기 발송 표시 없이 불렸다 — {kw}')
        r = STEP_OK[name]
        if r == 'boom':
            raise RuntimeError('일부러')
        if r:
            EV.append(name)
        return bool(r)
    return f


pns = {'threading': threading, 'log': _Log(), 'now_kst': now_kst,
       '_ops_get': lambda k, d=None: PC_STATE.get(k, d),
       '_ops_set': lambda k, v: PC_STATE.__setitem__(k, str(v)),
       '_closing_brief_key': lambda: 'closing_brief_sent',
       '_CLOSING_BRIEF_HHMM': (H, M),
       'send_closing_market_summary': _pc_brief,
       'alert_closing_summary': _pc_step('요약'), 'alert_flow_signals': _pc_step('수급'),
       'alert_revision_signals': _pc_step('리비전'), '_send_agent_close_picks': _pc_step('AI')}
exec(compile(pc_src + cb_src, 'server.py(발췌)', 'exec'), pns)
send_pc, catchup = pns['send_post_close_alerts'], pns['closing_brief_catchup']


def _reset(day=(2026, 10, 8, 16, 0)):
    EV.clear(); PC_STATE.clear(); BACKUPS.clear()
    BRIEF_READY[0] = True
    STEP_OK.update({'요약': True, '수급': True, '리비전': True, 'AI': True})
    NOW[0] = datetime(*day, tzinfo=KST)


ALL = ['요약', '수급', '리비전', 'AI']

# 16:00 cron — 시황이 먼저, 그다음 넷이 정해진 순서로
_reset()
want(send_pc() is True, '16:00 장 끝난 뒤 알림이 다 끝났다고 안 한다')
want(EV == ['시황'] + ALL, f'16:00 순서가 다르다 — 시황 먼저, 그다음 요약·수급·리비전·AI {EV}')
want(BACKUPS, '보낸 표시를 곧바로 백업하지 않는다 — 재배포 뒤 같은 알림이 또 나간다')
# 같은 날 다시 불려도(캐치업 · 수동 트리거) 아무것도 다시 안 나간다
EV.clear(); BACKUPS.clear()
want(send_pc() is True and EV == [], f'같은 날 두 번째 호출이 또 보냈다 — {EV}')
want(catchup() is False and EV == [], f'다 보낸 날 캐치업이 또 보냈다 — {EV}')
want(not BACKUPS, '새로 보낸 게 없는데 백업했다')

# 하나가 실패(False)·예외여도 나머지는 나가고, 실패한 것만 캐치업이 다시 보낸다
_reset()
STEP_OK['수급'] = 'boom'
STEP_OK['리비전'] = False
want(send_pc() is False, '실패가 있는데 다 끝났다고 한다')
want(EV == ['시황', '요약', 'AI'], f'하나가 실패해 뒤가 끊겼다 — {EV}')
want(PC_STATE.get('post_close_sent') == '2026-10-08:summary,agent',
     f'알림별 기록이 이상하다 — {PC_STATE.get("post_close_sent")!r}')
EV.clear()
STEP_OK['수급'] = True
NOW[0] = datetime(2026, 10, 8, 16, 35, tzinfo=KST)
want(catchup() is False, '시황은 이미 나갔는데 캐치업이 시황을 보냈다고 한다')
want(EV == ['수급'], f'캐치업이 실패한 것만 다시 보내지 않는다 — {EV}')
EV.clear()
STEP_OK['리비전'] = True
NOW[0] = datetime(2026, 10, 8, 17, 5, tzinfo=KST)
catchup()
want(EV == ['리비전'], f'두 번째 캐치업이 남은 것만 보내지 않는다 — {EV}')
EV.clear()
catchup()
want(EV == [], f'다 끝난 뒤에도 캐치업이 보냈다 — {EV}')

# 16:00 에 자고 있다 16:01 에 깨어났다 — 부팅 캐치업이 시황 → 나머지 순으로
_reset((2026, 10, 8, 16, 1))
want(catchup() is True, '부팅 캐치업이 밀린 시황을 보냈다고 안 한다')
want(EV == ['시황(지연)'] + ALL, f'깨어난 뒤 순서가 다르거나 빠졌다 — {EV}')

# 시황 데이터가 덜 찼으면 나머지도 안 보내고 기록도 안 찍는다 — 시황 다음에 보낸다
_reset((2026, 10, 8, 16, 1))
BRIEF_READY[0] = False
want(catchup() is False and EV == [], f'시황이 안 나갔는데 나머지를 보냈다 — {EV}')
want('post_close_sent' not in PC_STATE, '아무것도 안 보냈는데 기록을 찍었다')
BRIEF_READY[0] = True
NOW[0] = datetime(2026, 10, 8, 16, 35, tzinfo=KST)
want(catchup() is True and EV == ['시황(지연)'] + ALL,
     f'데이터가 찬 뒤 캐치업이 시황 → 나머지 순으로 안 보냈다 — {EV}')

# 장중 · 주말에는 캐치업이 아무것도 안 한다
_reset((2026, 10, 8, 15, 59))
want(catchup() is False and EV == [], f'16:00 전에 캐치업이 보냈다 — {EV}')
_reset((2026, 10, 10, 17, 0))                                   # 토요일
want(catchup() is False and EV == [], f'토요일에 캐치업이 보냈다 — {EV}')

# 어제 기록은 오늘을 막지 않는다
_reset((2026, 10, 8, 16, 0))
send_pc()
EV.clear()
NOW[0] = datetime(2026, 10, 9, 16, 5, tzinfo=KST)
catchup()
want(EV == ['시황(지연)'] + ALL, f'어제 기록 때문에 오늘 것이 막혔다 — {EV}')

# 다른 쪽이 보내는 중이면 겹쳐 보내지 않는다 (16:00 cron 이 리비전을 기다리는 동안 16:05 캐치업)
_reset()
pns['_POST_CLOSE_LOCK'].acquire()
try:
    want(send_pc() is False and EV == [], f'보내는 중인데 또 보냈다 — {EV}')
    want(catchup() is False and EV == [], f'보내는 중인데 캐치업이 또 보냈다 — {EV}')
finally:
    pns['_POST_CLOSE_LOCK'].release()

# AI 추천 — 15:45 준비분을 16:00 에 보낸다 · 실행 실패면 안 보내고 '못 끝냄'
ag_src = grab(r'\n_AGENT_RUN_LOCK = .*?\ndef _send_agent_close_picks\(.*?\n(?=\n\n)',
              '_AGENT_RUN_LOCK ~ _send_agent_close_picks').group(0)
AG_SENT: list = []
AG_RUNS: list = []
AG_NEXT: list = []


def _fake_run_pipeline():
    AG_RUNS.append(1)
    r = AG_NEXT.pop(0) if AG_NEXT else None
    if isinstance(r, Exception):
        raise r
    return r


sys.modules['agents'] = types.SimpleNamespace()
sys.modules['agents.pipeline'] = types.SimpleNamespace(
    run_pipeline=_fake_run_pipeline,
    send_agent_telegram=lambda res: AG_SENT.append(res['tag']) or True)


class _P:
    def __truediv__(self, _):
        return self

    def exists(self):
        return True


ans = {'threading': threading, 'time': __import__('time'), 'log': _Log(), 'now_kst': now_kst,
       'BASE_DIR': _P()}
exec(compile(ag_src, 'server.py(발췌)', 'exec'), ans)
NOW[0] = datetime(2026, 10, 8, 15, 45, tzinfo=KST)
AG_NEXT[:] = [{'tag': '15:45', 'final_picks': [{'code': '005930'}]}]
ans['agent_prepare_close']()
want(AG_SENT == [], f'15:45 준비가 텔레그램을 보냈다 — {AG_SENT}')
NOW[0] = datetime(2026, 10, 8, 16, 0, tzinfo=KST)
want(ans['_send_agent_close_picks']() is True and AG_SENT == ['15:45'] and len(AG_RUNS) == 1,
     f'16:00 에 15:45 준비분을 안 보냈거나 다시 돌렸다 — 보냄 {AG_SENT} · 실행 {len(AG_RUNS)}')
# 다음 날 — 어제 준비분은 안 쓴다. 15:45 를 놓쳤으면 지금 돌리고, 실패하면 아무것도 안 보낸다
AG_SENT.clear(); AG_RUNS.clear()
NOW[0] = datetime(2026, 10, 9, 16, 1, tzinfo=KST)
AG_NEXT[:] = [RuntimeError('일부러')]
want(ans['_send_agent_close_picks']() is False and AG_SENT == [],
     f'실행이 실패했는데 보냈거나 끝냈다고 한다 — {AG_SENT}')
AG_NEXT[:] = [{'tag': '재실행', 'final_picks': [{'code': '000660'}]}]
want(ans['_send_agent_close_picks']() is True and AG_SENT == ['재실행'],
     f'캐치업 재시도가 새로 돌려 보내지 않았다 — {AG_SENT}')
AG_NEXT[:] = [{'tag': '0종목', 'final_picks': []}]
AG_SENT.clear()
NOW[0] = datetime(2026, 10, 12, 16, 0, tzinfo=KST)
want(ans['_send_agent_close_picks']() is True and AG_SENT == [],
     '추천 0종목인 날을 못 끝냈다고 한다 — 캐치업이 계속 다시 돌린다')

# wake.yml 은 15:35 준비 잡 전에 깨운다
WAKE = open('/home/user/stock-dashboard/.github/workflows/wake.yml', encoding='utf-8').read()
want(re.search(r"cron: '30,[^']* 6 \* \* 1-5'", WAKE),
     'wake.yml 이 KST 15:30(UTC 06:30)부터 안 깨운다 — 15:35~15:48 준비 잡이 잠든 채 지나간다')

# ── 8. 16:00 메시지의 기준일 ────────────────────────────────────────────
fs = grab(r'\n_FLOW_SIG_BASIS_KEY = .*?\ndef alert_flow_signals\(.*?\n(?=\n\n)',
          'alert_flow_signals').group(0)
OUT: list = []
SIG = {'date': '2026-10-07', 'dual_buy': [{'name': 'A', 'foreign': 120.0, 'inst': 80.0}],
       'dual_sell': [], 'streak_buy': [], 'streak_sell': [], 'reversal': []}
FS_STATE: dict = {}
TG_OK = [True]


def _out(msg):
    OUT.append(msg)
    return TG_OK[0]


fns = {'_analyze_flow_signals': lambda: SIG, 'send_telegram': _out,
       'now_kst': now_kst, 'log': _Log(),
       '_ops_get': lambda k, d=None: FS_STATE.get(k, d),
       '_ops_set': lambda k, v: FS_STATE.__setitem__(k, str(v))}
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

# ── 11. 같은 기준일을 이틀 보내지 않는다 (16:00 최신일이 오늘·전일로 오가는 날, 평일 공휴일)
af = fns['alert_flow_signals']
OUT.clear(); FS_STATE.clear()
NOW[0] = datetime(2026, 10, 8, 16, 0, tzinfo=KST)
SIG['date'] = '2026-10-08'                    # 오늘 16:00 — 오늘 잠정치가 있었다
want(af(scheduled=True) is True and len(OUT) == 1, '16:00 수급 시그널을 안 보냈다')
NOW[0] = datetime(2026, 10, 9, 16, 0, tzinfo=KST)  # 다음 날 16:00 — 아직 전일(10/08)뿐
want(af(scheduled=True) is True, '이미 보낸 기준일인데 못 끝냈다고 한다 — 캐치업이 계속 돈다')
want(len(OUT) == 1, f'10/08 시그널을 이틀 연달아 보냈다 — {len(OUT)}건')
SIG['date'] = '2026-10-09'
af(scheduled=True)
want(len(OUT) == 2, '새 기준일인데 안 보냈다')
af()                                          # /시그널 명령 — 묻는 대로 늘 보낸다
want(len(OUT) == 3, '/시그널 명령이 정기 발송 기록에 막혔다')
# 발송이 실패하면 기록을 안 찍고 False — 캐치업이 다시 보낸다
FS_STATE.clear(); TG_OK[0] = False
want(af(scheduled=True) is False and not FS_STATE, '발송 실패인데 보냈다고 기록했다')
TG_OK[0] = True
# 데이터가 아직 없으면(재시작 직후 flow_cache 빔) 끝낸 게 아니다
_sig_backup = dict(SIG)
SIG['date'] = None
want(af(scheduled=True) is False, '수급 데이터가 없는데 끝냈다고 한다 — 캐치업이 다시 안 본다')
SIG.update(_sig_backup)

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
RV_TG_OK = [True]


def _rv_out(msg):
    RV_OUT.append(msg)
    return RV_TG_OK[0]


rns = {'_get_db': _mem_db, 'send_telegram': _rv_out, 'now_kst': now_kst,
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
want(rns['alert_revision_signals']() is True, '보낼 게 없는 날을 끝냈다고 안 한다')
want(COMPUTED == [1], '오늘 계산분이 없는데 계산 없이 보냈다')
rns['_REVISION_COMPUTED']['date'] = '2026-10-08'

# 발송이 실패하면 alert_sent 를 찍지 않는다 — 찍으면 그 알림은 영영 안 나간다
_add_alert('2026-10-08')
RV_TG_OK[0] = False
want(rns['alert_revision_signals']() is False, '리비전 발송 실패인데 끝냈다고 한다')
left = MEM.execute('SELECT COUNT(*) FROM revision_alerts WHERE alert_sent=0').fetchone()[0]
want(left == 1, f'발송 실패인데 alert_sent 를 찍었다 — 남은 미발송 {left}')
RV_TG_OK[0] = True
want(rns['alert_revision_signals']() is True, '다시 보낼 때 못 보냈다')
left = MEM.execute('SELECT COUNT(*) FROM revision_alerts WHERE alert_sent=0').fetchone()[0]
want(left == 0, f'보냈는데 alert_sent 를 안 찍었다 — 남은 미발송 {left}')

# 오늘 스냅샷은 있는데 실린 게 전날 남은 몫뿐이면 '오늘 스냅샷 전' 이라고 하지 않는다
MEM.executescript("CREATE TABLE consensus_snapshot (snapshot_date TEXT);"
                  "INSERT INTO consensus_snapshot VALUES ('2026-10-08');")
_add_alert('2026-10-07')
rns['alert_revision_signals']()
want('오늘 스냅샷 전' not in RV_OUT[-1] and '앞서 못 보낸 몫' in RV_OUT[-1],
     f'오늘 스냅샷이 있는데 없다고 적는다 — {RV_OUT[-1][:200]!r}')
MEM.execute("DELETE FROM consensus_snapshot")
MEM.execute("INSERT INTO consensus_snapshot VALUES ('2026-10-07')")
_add_alert('2026-10-07')
rns['alert_revision_signals']()
want('오늘 스냅샷 전' in RV_OUT[-1], f'오늘 스냅샷이 없는데 안 밝힌다 — {RV_OUT[-1][:200]!r}')

# ── 10. 텔레그램 — 429 는 한 번 다시 · 긴 본문 조각은 락에 안 막힌다 ────────
import os  # noqa: E402

tg_src = grab(r'\n_TG_SEND_LOCK = .*?\ndef send_telegram\(.*?\n(?=\n\n)', 'send_telegram').group(0)
sp_src = grab(r'\n_TG_LIMIT = .*?\ndef send_telegram_long\(.*?\n(?=\n\n)',
              '_split_telegram_lines ~ send_telegram_long').group(0)
POSTS: list = []
SLEEPS: list = []
CODES: list = []


class _Resp:
    def __init__(self, code):
        self.status_code = code
        self.text = ''

    def json(self):
        return {'ok': False, 'parameters': {'retry_after': 2}} if self.status_code == 429 else {'ok': True}


class _FakeTime:
    t = [1000.0]

    @staticmethod
    def monotonic():
        return _FakeTime.t[0]

    @staticmethod
    def sleep(sec):
        SLEEPS.append(round(sec, 2))
        _FakeTime.t[0] += sec


def _post(url, json=None, timeout=None):
    POSTS.append(json['text'])
    return _Resp(CODES.pop(0) if CODES else 200)


sys.modules['requests'] = types.SimpleNamespace(post=_post)
os.environ.update({'TELEGRAM_BOT_TOKEN': 'test-token', 'TELEGRAM_CHAT_ID': '1',
                   'TELEGRAM_ENABLED': '1'})
tns = {'threading': threading, 'time': _FakeTime, 'os': os, 'log': _Log()}
exec(compile(tg_src + sp_src, 'server.py(발췌)', 'exec'), tns)
CODES[:] = [429, 200]
want(tns['send_telegram']('a') is True, '429 뒤 다시 보낸 게 성공인데 실패라고 한다')
want(POSTS == ['a', 'a'] and 2 in SLEEPS, f'429 를 retry_after 만큼 기다려 다시 안 보냈다 — {POSTS} {SLEEPS}')
POSTS.clear(); CODES[:] = [429, 429, 200]
want(tns['send_telegram']('b') is False and POSTS == ['b', 'b'],
     f'429 재시도가 한 번이 아니다 — {POSTS}')
POSTS.clear(); SLEEPS.clear(); CODES[:] = []
_FakeTime.t[0] += 100
tns['send_telegram']('c'); tns['send_telegram']('d')
want(SLEEPS and abs(SLEEPS[-1] - tns['_TG_MIN_GAP_S']) < 0.01,
     f'연달아 보낼 때 간격을 안 띄웠다 — {SLEEPS}')
# 긴 본문은 같은 락(RLock)을 쥔 채 조각을 보낸다 — 다른 스레드에서 돌려 막히지 않는지 본다
POSTS.clear()
long_msg = '\n'.join(f'줄 {i:04d} ' + 'x' * 60 for i in range(150))
th = threading.Thread(target=tns['send_telegram_long'], args=(long_msg,), daemon=True)
th.start(); th.join(5)
want(not th.is_alive(), '긴 본문 발송이 락에 막혀 끝나지 않는다 (재진입 교착)')
want(len(POSTS) >= 2 and all(p.startswith(f'<i>({i}/{len(POSTS)})</i>') for i, p in enumerate(POSTS, 1)),
     f'조각이 순서대로 안 나갔다 — {[p[:12] for p in POSTS]}')

# ── 옛 이름이 남아 있지 않은지 ───────────────────────────────────────────
want('send_evening_market_summary' not in SRC,
     '옛 이름 send_evening_market_summary 가 남아 있다')
want('tg_evening_summary' not in SRC, "옛 job id 'tg_evening_summary' 가 남아 있다")

print('---')
print('통과' if ok else '실패')
sys.exit(0 if ok else 1)
