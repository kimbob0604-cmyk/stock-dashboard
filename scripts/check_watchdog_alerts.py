"""워치독 알림 정책을 server.py 원문 그대로 돌려 본다.

_market_watchdog 본문을 뽑아 가짜 주변부(텔레그램·복구·상태저장) 위에서
실행한다. 상태 저장소는 프로세스 밖(sqlite 파일)에 두고, '재시작' 은
메모리를 비우고 같은 저장소로 다시 도는 것으로 흉내 낸다.

못 박는 것:
  1. 비정상 + 복구 실패 → 알림 1회
  2. 계속 비정상이어도 다시 알리지 않는다
  3. **재시작해도** 다시 알리지 않는다  ← 알림이 계속 오던 자리
  4. 복구되면 복구 알림 1회, 같은 날 다시 깨져도 알림은 없다(하루 1회 상한)
  5. 다음 날 깨지면 다시 알린다
"""
import re
import sqlite3
import datetime as _dt

SRC = open('/home/user/stock-dashboard/server.py', encoding='utf-8').read()
m = re.search(r'^def _market_watchdog\(.*?^    _WATCHDOG_STATE\["last_summary"\] = after\n',
              SRC, re.S | re.M)
assert m, '_market_watchdog 를 server.py 에서 못 찾았다'
BODY = m.group(0)
assert '_ops_get("watchdog_alerted"' in BODY, '알림 상태가 저장소를 거치지 않는다'
assert '_ops_get("watchdog_alert_date")' in BODY, '하루 1회 상한이 없다'

DB = sqlite3.connect(':memory:')       # 재시작해도 살아남는 자리
DB.execute("CREATE TABLE ops_state (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")

sent: list[str] = []
NOW = _dt.datetime(2026, 9, 15, 16, 30)
HEALTH = {"healthy": False, "issues": ["수급 데이터 부족 (0행)"], "stocks_kr": 2400,
          "flow_rows": 0, "due": {"flow_rows": True, "stocks_stale": True}}


def boot():
    """프로세스 하나를 세운다 — 메모리는 새것, ops_state 는 그대로."""
    mem = {"last_summary": None}

    def ops_get(key, default=None):
        row = DB.execute("SELECT value FROM ops_state WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def ops_set(key, value):
        mem[key] = value
        DB.execute("INSERT INTO ops_state (key, value, updated_at) VALUES (?,?,?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, str(value), ""))
        DB.commit()

    class _Log:
        def __getattr__(self, _):
            return lambda *a, **k: None

    ns = {
        "_check_market_data_health": lambda: dict(HEALTH),
        "send_telegram": lambda msg: sent.append(msg),
        "_refresh_prices_from_naver": lambda: 0,
        "_refresh_flow_batch": lambda top_n=200: {"success": 0},
        "_ops_get": ops_get, "_ops_set": ops_set,
        "_WATCHDOG_STATE": mem, "now_kst": lambda: NOW, "log": _Log(),
    }
    exec(compile(BODY, 'server.py(발췌)', 'exec'), ns)
    return ns["_market_watchdog"]


def head(msg):
    return msg.split('\n')[0]


# 1. 첫 사고 — 알림 1회
watchdog = boot()
watchdog()
assert len(sent) == 1 and '자동복구 실패' in head(sent[0]), sent

# 2. 같은 프로세스에서 계속 비정상 — 조용
watchdog(); watchdog()
assert len(sent) == 1, f'비정상 지속에 또 알렸다: {[head(x) for x in sent]}'

# 3. 재시작 — 여전히 조용해야 한다 (예전엔 여기서 매번 다시 알렸다)
watchdog = boot()
watchdog()
assert len(sent) == 1, f'재시작 후 같은 사고를 다시 알렸다: {[head(x) for x in sent]}'

# 4. 복구 → 복구 알림 1회. 같은 날 또 깨져도 상한에 걸린다
HEALTH.update(healthy=True, issues=[], flow_rows=180)
watchdog()
assert len(sent) == 2 and '복구' in head(sent[1]), [head(x) for x in sent]
watchdog()
assert len(sent) == 2, '건강한데 또 보냈다'

HEALTH.update(healthy=False, issues=["stocks 갱신 정체 (200분 전)"], flow_rows=180)
watchdog()
assert len(sent) == 2, f'하루 1회 상한이 안 걸렸다: {[head(x) for x in sent]}'

# 5. 날이 바뀌면 다시 알린다
NOW = _dt.datetime(2026, 9, 16, 16, 30)
watchdog = boot()
watchdog()
assert len(sent) == 3 and '자동복구 실패' in head(sent[2]), [head(x) for x in sent]

print('보낸 알림:', [head(x).replace('🛠 <b>', '').replace('</b>', '') for x in sent])
print('통과')
