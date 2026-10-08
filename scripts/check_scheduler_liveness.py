"""server.py 의 스케줄러 생존 감시(_scheduler_liveness_check)를 가짜 스케줄러로 돌려 본다.

2026-10-08 운영에서 APScheduler 가 running=True 인 채로 10:55 부터 잡을 하나도
돌리지 않았다. 감시는 '다음 실행 시각을 5분 넘게 지난 잡' 또는 '죽은 스케줄러
스레드' 를 1분마다 보고, 3번 연속이면 스케줄러가 도는 프로세스를 끝내 다시 뜨게 한다.
운영은 gunicorn preload 라 스케줄러가 마스터에 있다 — 워커는 마스터의 심장박동 파일을 읽는다(8번).
진짜 server 모듈의 함수를 그대로 부른다(SERVER_NO_STARTUP=1 — 스케줄러·스레드 없음).
"""
import os
import re
import sys
import time
from datetime import datetime, timezone

os.environ["SERVER_NO_STARTUP"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import server  # noqa: E402

FAIL = []


def want(cond, why):
    print(("PASS " if cond else "FAIL ") + why)
    if not cond:
        FAIL.append(why)


class Job:
    def __init__(self, jid, lag_sec):
        self.id = jid
        self.next_run_time = (None if lag_sec is None else
                              datetime.fromtimestamp(time.time() - lag_sec, tz=timezone.utc))


class Thread:
    def __init__(self, alive=True):
        self.alive = alive

    def is_alive(self):
        return self.alive


class FakeScheduler:
    def __init__(self, jobs, alive=True, running=True):
        self.jobs, self.running, self._thread, self.wakes = jobs, running, Thread(alive), 0

    def get_jobs(self):
        return list(self.jobs)

    def wakeup(self):
        self.wakes += 1


class Exit(Exception):
    pass


def exit_fn(code):
    raise Exit(code)


def run(sched, strikes=0):
    server._scheduler = sched
    try:
        return server._scheduler_liveness_check(strikes, exit_fn=exit_fn), None
    except Exit as e:
        return None, e.args[0]


# 1) 정상 — 곧 돌 잡·막 지난 잡(4분 59초)·일시정지 잡
ok = FakeScheduler([Job("a", -60), Job("b", 299), Job("paused", None)])
want(run(ok) == (0, None), "정상이면 의심 0, 종료 없음")
want(ok.wakes == 0, "정상이면 깨우지 않는다")
want(server._scheduler_overdue() == [], "4분 59초 지난 잡·일시정지 잡은 밀린 잡이 아니다")

# 2) 밀린 잡 — 1·2번째는 깨우기만, 3번째에 종료(코드 1)
stuck = FakeScheduler([Job("tg_closing_summary", 2785), Job("stage2_realtime_kr", 21085)])
r1 = run(stuck, 0)
r2 = run(stuck, r1[0])
r3 = run(stuck, r2[0])
want(r1 == (1, None) and r2 == (2, None), "밀린 잡: 1·2번째는 종료하지 않는다")
want(r3 == (None, 1), "밀린 잡: 3번째 연속이면 프로세스를 끝낸다(코드 1 — 다시 떠야 스케줄러가 산다)")
want(stuck.wakes == 3, "의심할 때마다 먼저 wakeup 을 부른다")
want([j for j, _ in server._scheduler_overdue()] == ["stage2_realtime_kr", "tg_closing_summary"],
     "밀린 잡은 오래 밀린 순")

# 3) 스레드가 죽었으면 밀린 잡이 없어도 의심
dead = FakeScheduler([Job("a", -60)], alive=False)
want(run(dead, 0) == (1, None), "스케줄러 스레드가 죽었으면 의심 1")

# 4) 회복하면 0 으로 돌아간다
want(run(ok, 2) == (0, None), "의심 2번 뒤 회복하면 0")

# 5) 스케줄러가 없거나 멈춘(running=False — 기존 _check_scheduler_health 가 맡음) 상태는 건드리지 않는다
want(run(None, 2) == (0, None), "스케줄러 없음 → 0")
want(run(FakeScheduler([Job("a", 9999)], running=False), 0) == (0, None), "running=False 는 이 감시의 몫이 아니다")

# 6) 배선 — _startup 이 스케줄러 시작 직후 감시 스레드를 띄우고, /api/health 가 밀린 잡 수를 싣는다
src = open(server.__file__, encoding="utf-8").read()
start = src.index('log.info("APScheduler 시작')
want(re.search(r"target=_scheduler_liveness_loop", src[start:start + 600]) is not None,
     "_startup: 스케줄러 시작 직후 scheduler-liveness 스레드를 띄운다")
server._scheduler = stuck
with server.app.test_client() as c:
    body = c.get("/api/health").get_json()
want(body.get("scheduler_overdue") == 2, "/api/health 에 scheduler_overdue(밀린 잡 수)")
server._scheduler = None

# 7) 진단 — 잡히지 않은 스레드 예외를 기억하고, /api/ops/diag/threads 가 스레드 위치·메모리를 보인다
import threading  # noqa: E402


def _boom():
    raise MemoryError("시험")


t = threading.Thread(target=_boom, name="boom-test")
t.start(); t.join()
errs = [e for e in server._THREAD_ERRORS if e["thread"] == "boom-test"]
want(len(errs) == 1 and errs[0]["type"] == "MemoryError" and errs[0]["where"],
     "스레드에서 잡히지 않은 예외(이름·종류·위치)를 기억한다")
server._scheduler = dead
with server.app.test_client() as c:
    d = c.get("/api/ops/diag/threads").get_json()
    h = c.get("/api/health").get_json()
want(d["scheduler"]["thread_alive"] is False and any(e["thread"] == "boom-test" for e in d["thread_errors"]),
     "/api/ops/diag/threads: 스케줄러 스레드 생존·스레드 예외")
want(any(th["name"] == "MainThread" and th["stack"] for th in d["threads"]), "/api/ops/diag/threads: 스레드별 코드 위치")
want(h.get("scheduler_thread_alive") is False and "rss_mb" in h, "/api/health: 스케줄러 스레드 생존·메모리")
server._scheduler = None

# 8) 심장박동 — 운영 gunicorn 은 preload 라 스케줄러는 마스터에서 돌고 워커에는 fork 사본만 있다.
#    마스터가 적은 상태를 워커가 읽어야 한다. 진짜 fork 로 확인한다.
import json  # noqa: E402
import sqlite3  # noqa: E402
import tempfile  # noqa: E402
from contextlib import contextmanager  # noqa: E402

server._SCHED_HEARTBEAT = server.Path(tempfile.mkdtemp()) / "hb.json"
Job.name, Job.trigger, Job.max_instances = None, "interval[0:01:00]", 1


class ForkCopy(FakeScheduler):
    """워커의 fork 사본. 잡 저장소 잠금이 fork 때 잡혀 있었다면 get_jobs 는 영원히 멈춘다 —
    여기서는 부르면 바로 실패시켜, 워커가 사본을 건드리지 않는지 본다."""
    started = 0
    touched = 0

    def start(self):
        ForkCopy.started += 1

    def get_jobs(self):
        ForkCopy.touched += 1
        raise RuntimeError("fork 사본의 잡 저장소를 건드렸다")


server._SCHED_PID = None
server._scheduler = stuck
want(server._scheduler_view()["process"] == "this" and len(server._scheduler_view()["overdue"]) == 2,
     "단일 프로세스(_SCHED_PID 없음)면 예전처럼 직접 본다")

master = FakeScheduler([Job("a", -60), Job("b", 299), Job("paused", None)])
server._scheduler, server._SCHED_PID = master, os.getpid()
server._scheduler_heartbeat_write()
hb = json.loads(server._SCHED_HEARTBEAT.read_text(encoding="utf-8"))
want(hb["pid"] == os.getpid() and hb["boot"] == server._start_time and hb["overdue"] == []
     and set(hb["jobs"]) == {"a", "b", "paused"} and hb["jobs"]["paused"]["next"] is None
     and hb["jobs"]["a"]["trigger"] == "interval[0:01:00]",
     "마스터가 심장박동(pid·부팅·밀린 잡·잡별 다음 시각과 표시 정보)을 적는다")
want(not list(server._SCHED_HEARTBEAT.parent.glob("*.tmp")), "임시 파일을 남기지 않는다(원자적 교체)")

r_fd, w_fd = os.pipe()
pid = os.fork()
if pid == 0:                                   # ── 워커 역할(자식) ──
    os.close(r_fd)
    out = {}
    try:
        # fork 사본: 같은 잡이지만 다음 실행 시각이 부팅 때 값에 멈춰 있고 스레드는 없다
        copy = ForkCopy([Job("a", 9999), Job("b", 9999), Job("paused", None)], alive=False)
        server._scheduler = copy
        with server.app.test_client() as c:
            out["health"] = c.get("/api/health").get_json()
            out["cron"] = c.get("/api/ops/cron/jobs").get_json()
            out["diag"] = c.get("/api/ops/diag/threads").get_json()["scheduler"]
            copy.running = False                   # 사본이 '멈춤' 으로 보여도
            server._last_scheduler_check = 0.0
            c.get("/api/health")
            out["started"] = ForkCopy.started       # 워커에서 두 번째 스케줄러를 띄우면 안 된다
            out["overview"] = (c.get("/api/ops/health").get_json() or {}).get("scheduler")
            out["touched"] = ForkCopy.touched
            stale = json.loads(server._SCHED_HEARTBEAT.read_text(encoding="utf-8"))
            stale["at"] -= 400
            server._SCHED_HEARTBEAT.write_text(json.dumps(stale), encoding="utf-8")
            out["stale"] = c.get("/api/health").get_json()
            stale["at"] += 400
            stale["pid"] = 1
            server._SCHED_HEARTBEAT.write_text(json.dumps(stale), encoding="utf-8")
            out["other_pid"] = server._scheduler_view()
            stale["pid"], stale["boot"] = server._SCHED_PID, stale["boot"] - 1   # pid 재사용
            server._SCHED_HEARTBEAT.write_text(json.dumps(stale), encoding="utf-8")
            out["other_boot"] = server._scheduler_view()
            out["cron_none"] = c.get("/api/ops/cron/jobs").get_json()
    except Exception as e:                     # noqa: BLE001
        out["error"] = repr(e)
    os.write(w_fd, json.dumps(out, default=str).encode())
    os._exit(0)
os.close(w_fd)
buf = b""
while chunk := os.read(r_fd, 65536):
    buf += chunk
os.waitpid(pid, 0)
w = json.loads(buf or b"{}")
want("error" not in w, f"워커 역할 실행 오류 없음 {w.get('error', '')}")
h = w.get("health") or {}
want(h.get("scheduler_process") == "other" and h.get("scheduler_overdue") == 0
     and h.get("scheduler_thread_alive") is True and h.get("scheduler_running") is True
     and h.get("scheduler_jobs") == 3,
     "/api/health(워커): fork 사본이 아니라 마스터 심장박동으로 — 밀린 잡 0·스레드 살아 있음")
rows = {r["id"]: r for r in (w.get("cron") or {}).get("jobs", [])}
want((w.get("cron") or {}).get("next_run_source") == "heartbeat"
     and rows.get("a", {}).get("status") == "scheduled" and rows.get("a", {}).get("next_run_in_sec", -1) > 0
     and rows.get("paused", {}).get("status") == "paused"
     and rows.get("a", {}).get("trigger") == "interval[0:01:00]",
     "/api/ops/cron/jobs(워커): 잡 목록·다음 실행 시각은 마스터 것")
ov = w.get("overview") or {}
want(ov.get("process") == "other" and ov.get("running") is True and ov.get("jobs_total") == 3
     and ov.get("jobs_paused") == 1, "/api/ops/health(워커): 스케줄러 점수도 마스터 기준")
want(w.get("touched") == 0, "워커는 fork 사본의 잡 저장소를 건드리지 않는다(fork 때 잡힌 잠금에 멈추지 않게)")
want((w.get("diag") or {}).get("process") == "other" and (w.get("diag") or {}).get("overdue") == [],
     "/api/ops/diag/threads(워커): 마스터 기준")
want(w.get("started") == 0, "워커는 사본이 멈춰 보여도 스케줄러를 새로 띄우지 않는다(이중 발송 방지)")
st = w.get("stale") or {}
want(st.get("scheduler_overdue") is None and st.get("scheduler_running") is None
     and st.get("scheduler_heartbeat_age_sec", 0) > server._SCHED_HEARTBEAT_STALE_SEC,
     "심장박동이 3분 넘게 묵었으면 '모름'(None)과 나이를 싣는다")
for k, why in (("other_pid", "pid"), ("other_boot", "같은 pid·다른 부팅")):
    ob = w.get(k) or {}
    want(ob.get("overdue") is None and ob.get("heartbeat_age_sec") is None,
         f"다른 부팅({why})의 심장박동은 쓰지 않는다")
cn = w.get("cron_none") or {}
want(cn.get("next_run_source") == "unavailable" and cn.get("jobs") == [] and cn.get("scheduler_running") is None,
     "/api/ops/cron/jobs(워커·심장박동 없음): 사본 대신 '모름'")

server._scheduler, server._SCHED_PID = master, None

# 배선 — 스케줄러를 띄운 직후 그 pid 를 적고, 감시 루프가 심장박동을 적는다
want(re.search(r"_scheduler\.start\(\)\n\s*_SCHED_PID = os\.getpid\(\)", src) is not None,
     "_startup: _scheduler.start() 바로 다음에 _SCHED_PID = os.getpid()")


class StopLoop(Exception):
    pass


def _stop_sleep(_sec):
    raise StopLoop


server._SCHED_HEARTBEAT.unlink()
_real_sleep, server.time.sleep = server.time.sleep, _stop_sleep
try:
    server._scheduler_liveness_loop()
except StopLoop:
    pass
finally:
    server.time.sleep = _real_sleep
want(server._SCHED_HEARTBEAT.exists(), "감시 루프가 (잠들기 전에) 심장박동을 적는다")

# 9) /api/ops/post_close/status — 오늘 16:00 텔레그램을 어디까지 보냈는지(ops_state)
dbp = os.path.join(tempfile.mkdtemp(), "t.db")
cx = sqlite3.connect(dbp)
cx.execute("CREATE TABLE ops_state (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
cx.commit(); cx.close()


@contextmanager
def gdb():
    c = sqlite3.connect(dbp)
    c.row_factory = sqlite3.Row
    try:
        yield c
    finally:
        c.close()


server._get_db = gdb
today = server.now_kst().strftime("%Y-%m-%d")
server._ops_set(server._closing_brief_key(), today)
server._ops_set(server._POST_CLOSE_KEY, f"{today}:summary,flow")
with server.app.test_client() as c:
    ps = c.get("/api/ops/post_close/status").get_json()
want(ps["closing_brief_sent"] is True and ps["post_close_done"] == ["summary", "flow"]
     and ps["post_close_left"] == ["revision", "agent"]
     and ps["marks"][server._POST_CLOSE_KEY]["updated_at"],
     "/api/ops/post_close/status: 시황 보냄·알림 끝낸 것/남은 것·기록 시각")
server._ops_set(server._POST_CLOSE_KEY, "2026-01-02:summary,flow,revision,agent")
with server.app.test_client() as c:
    ps = c.get("/api/ops/post_close/status").get_json()
want(ps["post_close_done"] == [] and len(ps["post_close_left"]) == 4, "다른 날 표시는 오늘 것으로 치지 않는다")
server._scheduler = None

print(f"\n{'실패 ' + str(len(FAIL)) + '개' if FAIL else '모두 통과'}")
sys.exit(1 if FAIL else 0)
