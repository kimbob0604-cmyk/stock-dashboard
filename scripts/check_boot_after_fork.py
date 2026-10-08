"""진짜 gunicorn 으로 '스케줄러·백그라운드 작업은 요청을 받는 워커에서 시작한다' 를 확인한다.

2026-10-08 운영 gunicorn 이 preload 로 마스터에서 앱을 불러왔고, 마스터에서 시작한
스레드가 SQLite 를 쓰는 도중에 워커가 fork 됐다. 잠긴 SQLite 내부 잠금이 워커로
복사돼 워커의 DB 접근이 전부 멈췄다(사이트 전체 무응답). server._boot 는 preload 면
시작을 fork 된 워커로 미룬다.

preload 와 아닌 경우 둘 다: 시작은 마스터가 아니라 요청에 답하는 워커 pid 에서 한 번,
워커를 죽이면 새 워커에서 다시 한 번. 네트워크·외부 서비스는 쓰지 않는다(_startup 은 가짜).
"""
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FAIL = []

PROBE = '''
import os, sys
sys.path.insert(0, {root!r})
os.environ["SERVER_NO_STARTUP"] = "1"
import server
def fake_startup():
    with open(os.environ["PROBE_OUT"], "a") as f:
        f.write("startup %d\\n" % os.getpid())
server._startup = fake_startup
del os.environ["SERVER_NO_STARTUP"]
server._boot()
def app(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [str(os.getpid()).encode()]
'''


def want(cond, why):
    print(("PASS " if cond else "FAIL ") + why)
    if not cond:
        FAIL.append(why)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(port, timeout=2.0):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=timeout) as r:
            return int(r.read())
    except Exception:                                      # noqa: BLE001
        return None


def wait_for(pred, sec=20.0):
    end = time.time() + sec
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.3)
    return None


def run(preload):
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "probe_app.py"), "w", encoding="utf-8") as f:
        f.write(PROBE.format(root=ROOT))
    out, port = os.path.join(d, "out.txt"), free_port()
    cmd = [sys.executable, "-m", "gunicorn", "--workers", "1", "--threads", "4",
           "--bind", f"127.0.0.1:{port}", "--chdir", d] + (["--preload"] if preload else []) + ["probe_app:app"]
    proc = subprocess.Popen(cmd, cwd=d, env={**os.environ, "PROBE_OUT": out},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        w1 = wait_for(lambda: get(port))
        wait_for(lambda: os.path.exists(out) and open(out).read().count("startup") >= 1, 10)
        os.kill(w1, signal.SIGTERM)
        w2 = wait_for(lambda: (lambda p: p if p and p != w1 else None)(get(port)))
        wait_for(lambda: open(out).read().count("startup") >= 2, 10)
        time.sleep(0.5)
        pids = [int(x.split()[1]) for x in open(out).read().split("\n") if x.startswith("startup")]
        return proc.pid, w1, w2, pids
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


for preload in (True, False):
    tag = "preload" if preload else "preload 없음"
    master, w1, w2, pids = run(preload)
    want(w1 is not None and w2 is not None, f"{tag}: 워커가 응답하고, 죽인 뒤 새 워커가 응답한다")
    want(master not in pids, f"{tag}: 마스터({master})에서는 시작하지 않는다 — 시작 pid {pids}")
    want(pids == [w1, w2], f"{tag}: 요청에 답하는 워커에서 한 번씩 시작한다 (워커 {w1}→{w2}, 시작 {pids})")

print(f"\n{'실패 ' + str(len(FAIL)) + '개' if FAIL else '모두 통과'}")
sys.exit(1 if FAIL else 0)
