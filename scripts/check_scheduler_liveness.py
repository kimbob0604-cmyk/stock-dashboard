"""server.py 의 스케줄러 생존 감시(_scheduler_liveness_check)를 가짜 스케줄러로 돌려 본다.

2026-10-08 운영에서 APScheduler 가 running=True 인 채로 10:55 부터 잡을 하나도
돌리지 않았다. 감시는 '다음 실행 시각을 5분 넘게 지난 잡' 또는 '죽은 스케줄러
스레드' 를 1분마다 보고, 3번 연속이면 워커를 끝내 gunicorn 이 새로 띄우게 한다.
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
want(r3 == (None, 1), "밀린 잡: 3번째 연속이면 워커를 끝낸다(코드 1 — gunicorn 이 다시 띄움)")
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

print(f"\n{'실패 ' + str(len(FAIL)) + '개' if FAIL else '모두 통과'}")
sys.exit(1 if FAIL else 0)
