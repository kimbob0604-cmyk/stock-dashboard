"""쓰기·개인 데이터 경로가 인증 없이는 막히는지 server.py 를 실제로 띄워 본다.

SERVER_NO_STARTUP=1 로 import 하면 스케줄러·백그라운드 스레드 없이 Flask 앱만
생긴다. test_client 로 부르고, 핸들러 본문까지 들어가는 요청은 부작용이 없는
것(/api/discover/reset · /api/alerts/list)만 쓴다.

못 박는 것:
  1. OPS_TOKEN 이 없으면 쓰기·개인 데이터·운영 경로가 전부 403 (fail-closed)
  2. /api 아래 GET 이 아닌 경로는 _AUTH_OPEN_ENDPOINTS 말고 전부 401 —
     url_map 을 훑으므로 새로 생긴 쓰기 경로도 여기 걸린다
  3. 개인 데이터 GET · GET 으로도 받는 수집 트리거도 401, 공개 GET 은 열려 있다
  4. 틀린 X-Ops-Token 401, 맞으면 통과 (워크플로 경로)
  5. 로그인 → HttpOnly · SameSite=Strict 쿠키, 그 쿠키로 쓰기·읽기 통과
  6. 쿠키로 들어온 쓰기라도 다른 출처(Sec-Fetch-Site / Origin)면 403
  7. 위조 서명·30일 지난 쿠키·OPS_TOKEN 교체 후 쿠키는 무효, 하루 넘은 쿠키는 연장
  8. 틀린 토큰 10번이면 429 (헤더·로그인 공통)
  9. 텔레그램 webhook 은 게이트가 아니라 자체 시크릿으로 막힌다
 10. 프론트: 토큰 원문 저장이 없고, fetch 래퍼가 401 에 로그인 창을 띄우며,
     사람 없이 나가는 요청은 창을 띄우지 않는다
 11. verify-deploy.yml 의 수급 배치 POST 가 X-Ops-Token 을 보낸다
"""
import logging
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["SERVER_NO_STARTUP"] = "1"
os.environ.pop("OPS_TOKEN", None)
sys.path.insert(0, ROOT)

import server  # noqa: E402

logging.getLogger("server").setLevel(logging.ERROR)
app = server.app
TOKEN = "check-token-0123456789abcdef"

# 개인 데이터 GET · GET 으로도 받는 수집 트리거 · 운영 GET — 라우트에 @require_ops_token
GATED_GET = [
    "/api/refresh", "/api/refresh_all", "/api/us/sync_db",
    "/api/alerts/list", "/api/journal/list", "/api/journal/summary",
    "/api/journal/recent", "/api/journal/stock/005930", "/api/journal/1",
    "/api/journal/1/export/markdown", "/api/journal/stats", "/api/correlation",
    "/api/verification/005930/composite", "/api/verification/005930/gap-analysis",
    "/api/db/backup", "/api/test_telegram", "/api/ops/diag/stocks_schema",
]
PUBLIC_GET = ["/api/health", "/api/auth/status", "/api/status"]

FAILS = []


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def concrete(rule):
    """/api/journal/<int:journal_id> → /api/journal/1"""
    path = re.sub(r"<int:[^>]+>", "1", rule.rule)
    return re.sub(r"<[^>]+>", "x", path)


def mutating_routes():
    out = []
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith("/api/"):
            continue
        for m in sorted((rule.methods or set()) - {"GET", "HEAD", "OPTIONS"}):
            out.append((m, concrete(rule), rule.endpoint))
    return out


def call(c, method, path, **kw):
    return c.open(path, method=method, **kw)


def reset():
    server._auth_fails.clear()


MUT = mutating_routes()
OPEN = server._AUTH_OPEN_ENDPOINTS
c = app.test_client()

print(f"쓰기 경로 {len(MUT)}개 (인증 예외 {sorted(OPEN)})")
check(len(MUT) >= 40, f"url_map 에서 쓰기 경로를 충분히 찾았다 ({len(MUT)})")

print("1. OPS_TOKEN 미설정 — 전부 403")
bad = [(m, p) for m, p, ep in MUT if ep not in OPEN and call(c, m, p).status_code != 403]
check(not bad, f"쓰기 경로 403 (어긋남 {bad[:5]})")
bad = [p for p in GATED_GET if c.get(p).status_code != 403]
check(not bad, f"개인 데이터·트리거 GET 403 (어긋남 {bad[:5]})")
check(c.post("/api/auth/login", json={"token": "x"}).status_code == 403, "로그인도 403")

os.environ["OPS_TOKEN"] = TOKEN
print("2. 인증 없음 — 쓰기 경로 전부 401 + X-Auth-Required")
bad = []
for m, p, ep in MUT:
    if ep in OPEN:
        continue
    r = call(c, m, p)
    if r.status_code != 401 or r.headers.get("X-Auth-Required") != "1":
        bad.append((m, p, r.status_code))
check(not bad, f"쓰기 경로 401 (어긋남 {bad[:5]})")
for name in ("api_themes_post", "api_portfolio_sync", "api_journal_add",
             "api_analysis_journal_update", "api_flow_refresh_batch", "api_set_interval",
             "api_vc2_segment_manage", "api_earnings_consensus_collect"):
    check(any(ep == name for _, _, ep in MUT), f"{name} 가 쓰기 경로 목록에 있다")

print("3. 개인 데이터 GET · 트리거 GET 401, 공개 GET 은 열림")
bad = [p for p in GATED_GET if c.get(p).status_code != 401]
check(not bad, f"막힐 GET 401 (어긋남 {bad})")
bad = [p for p in PUBLIC_GET if c.get(p).status_code in (401, 403)]
check(not bad, f"공개 GET 은 인증 없이 열림 (어긋남 {bad})")

print("4. X-Ops-Token")
reset()
r = c.post("/api/discover/reset", headers={"X-Ops-Token": "wrong"})
check(r.status_code == 401, f"틀린 토큰 401 ({r.status_code})")
r = c.post("/api/discover/reset", headers={"X-Ops-Token": TOKEN,
                                          "Sec-Fetch-Site": "cross-site"})
check(r.status_code == 200, f"맞는 토큰은 출처와 무관하게 통과 ({r.status_code})")
r = c.get("/api/alerts/list", headers={"X-Ops-Token": TOKEN})
check(r.status_code == 200, f"맞는 토큰으로 개인 데이터 GET ({r.status_code})")

print("5. 로그인 쿠키")
reset()
r = c.post("/api/auth/login", json={"token": "wrong"})
check(r.status_code == 401 and not r.headers.get("X-Auth-Required"),
      "틀린 토큰 로그인 401 (로그인 창을 다시 부르는 헤더 없음)")
check(c.get_cookie(server.AUTH_COOKIE) is None, "실패하면 쿠키 없음")
r = c.post("/api/auth/login", json={"token": TOKEN})
sc = r.headers.get("Set-Cookie") or ""
check(r.status_code == 200, f"맞는 토큰 로그인 200 ({r.status_code})")
check("HttpOnly" in sc and "SameSite=Strict" in sc, f"HttpOnly · SameSite=Strict ({sc[:120]})")
check(TOKEN not in sc, "쿠키에 토큰 원문이 없다")
check(c.get("/api/auth/status").get_json() == {"configured": True, "authenticated": True},
      "status 가 로그인됨")
r = c.post("/api/discover/reset", headers={"Sec-Fetch-Site": "same-origin"})
check(r.status_code == 200, f"쿠키로 쓰기 통과 ({r.status_code})")
check(c.get("/api/alerts/list").status_code == 200, "쿠키로 개인 데이터 GET 통과")
check("Set-Cookie" not in c.get("/api/alerts/list").headers, "갓 받은 쿠키는 다시 주지 않는다")

print("6. 다른 출처에서 온 쿠키 쓰기")
for hdrs, want in (({"Sec-Fetch-Site": "cross-site"}, 403),
                   ({"Sec-Fetch-Site": "same-site"}, 403),
                   ({"Origin": "https://evil.example"}, 403),
                   ({"Origin": "null"}, 403),
                   ({"Origin": "http://localhost"}, 200),
                   ({}, 200)):
    r = c.post("/api/discover/reset", headers=hdrs)
    check(r.status_code == want, f"{hdrs or '헤더 없음'} → {want} ({r.status_code})")
r = c.get("/api/alerts/list", headers={"Sec-Fetch-Site": "cross-site"})
check(r.status_code == 200, "읽기는 출처를 보지 않는다 (SameSite=Strict 가 쿠키를 안 보냄)")
r = c.post("/api/auth/logout", headers={"Sec-Fetch-Site": "cross-site"})
check(r.status_code == 403, "다른 출처의 로그아웃 거절")

print("7. 쿠키 유효성")
good = c.get_cookie(server.AUTH_COOKIE).value


def with_cookie(value):
    c.set_cookie(server.AUTH_COOKIE, value)
    return c.post("/api/discover/reset")


issued = int(good.split(".")[0])
check(with_cookie(f"{issued}.{'0' * 64}").status_code == 401, "위조 서명 401")
check(with_cookie(str(issued)).status_code == 401, "서명 없는 쿠키 401")
old = server._auth_cookie_value(TOKEN, int(time.time()) - 31 * 86400)
check(with_cookie(old).status_code == 401, "30일 지난 쿠키 401")
future = server._auth_cookie_value(TOKEN, int(time.time()) + 3600)
check(with_cookie(future).status_code == 401, "미래 발급 쿠키 401")
two_days = server._auth_cookie_value(TOKEN, int(time.time()) - 2 * 86400)
r = with_cookie(two_days)
check(r.status_code == 200 and server.AUTH_COOKIE in (r.headers.get("Set-Cookie") or ""),
      "하루 넘은 쿠키는 통과하면서 새로 발급 (만료 연장)")
c.set_cookie(server.AUTH_COOKIE, good)
os.environ["OPS_TOKEN"] = TOKEN + "-rotated"
check(c.post("/api/discover/reset").status_code == 401, "OPS_TOKEN 을 바꾸면 기존 쿠키 401")
os.environ["OPS_TOKEN"] = TOKEN
check(c.post("/api/discover/reset").status_code == 200, "되돌리면 다시 통과")
r = c.post("/api/auth/logout")
check(r.status_code == 200 and c.get_cookie(server.AUTH_COOKIE) is None, "로그아웃이 쿠키를 지운다")
check(c.post("/api/discover/reset").status_code == 401, "로그아웃 후 401")

print("8. 틀린 토큰 연속 입력")
reset()
for _ in range(server._AUTH_FAIL_MAX):
    c.post("/api/discover/reset", headers={"X-Ops-Token": "wrong"})
r = c.post("/api/discover/reset", headers={"X-Ops-Token": TOKEN})
check(r.status_code == 429, f"헤더 실패 {server._AUTH_FAIL_MAX}번 뒤 맞는 토큰도 429 ({r.status_code})")
r = c.post("/api/auth/login", json={"token": TOKEN})
check(r.status_code == 429, f"로그인도 429 ({r.status_code})")
r = c.post("/api/discover/reset", headers={"X-Ops-Token": TOKEN,
                                          "X-Forwarded-For": "203.0.113.9"})
check(r.status_code == 200, "다른 IP 는 잠기지 않는다")
reset()
r = c.post("/api/discover/reset")
check(r.status_code == 401, "토큰 없이 부른 것은 실패로 세지 않는다 (401)")
check(not server._auth_fails, "실패 기록 없음")

print("9. 텔레그램 webhook")
r = c.post("/api/telegram/webhook", json={})
check(r.status_code == 403 and not r.headers.get("X-Auth-Required"),
      f"자체 시크릿으로 403, 게이트 401 이 아님 ({r.status_code})")

print("10. 프론트")
js = {n: open(os.path.join(ROOT, "static/js", n), encoding="utf-8").read()
      for n in os.listdir(os.path.join(ROOT, "static/js")) if n.endswith(".js")}
html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
utils = js["utils.js"]
check(not any("opsFetch" in s for s in js.values()), "opsFetch(토큰을 localStorage 에 두던 방식)가 없다")
check(not re.search(r"localStorage\.setItem\([^)]*(token|ops)", "\n".join(js.values()), re.I),
      "토큰을 localStorage 에 쓰지 않는다")
check("window.fetch = async function" in utils and "'X-Auth-Required'" in utils,
      "utils.js 의 fetch 래퍼가 X-Auth-Required 를 본다")
check(all("_nativeFetch" not in s for n, s in js.items() if n != "utils.js"),
      "래퍼를 우회하는 _nativeFetch 는 utils.js 밖에 없다")
scripts = re.findall(r'<script src="/static/js/([^"]+)"', html)
check(scripts and scripts[0] == "utils.js", f"utils.js 가 가장 먼저 로드된다 ({scripts[:2]})")
check(re.search(r"fetch\('/api/refresh', \{ method: 'POST', authPrompt: false \}\)", js["app.js"]),
      "자동 갱신 타이머는 로그인 창을 띄우지 않는다")
check("authPrompt: !!opts.verbose" in js["pages.js"], "분석일지 자동 저장은 로그인 창을 띄우지 않는다")
for path, name in (("/api/watchlist/sync", "app.js"), ("/api/portfolio/sync", "pages.js"),
                   ("/api/alerts/sync", "pages.js")):
    m = re.search(re.escape(path) + r"'.*?\.catch\(", js[name], re.S)
    check(m and "authPrompt: 'once'" in m.group(0), f"{path} 는 한 번 취소하면 다시 묻지 않는다")
check('id="sm-auth-btn"' in html, "설정 모달에 로그인/로그아웃 버튼")

print("11. verify-deploy.yml")
wf = open(os.path.join(ROOT, ".github/workflows/verify-deploy.yml"), encoding="utf-8").read()
m = re.search(r"curl[^\n]*(?:\\\n[^\n]*)*?/api/flow/refresh-batch", wf)
check(m and 'X-Ops-Token: $OPS_TOKEN' in m.group(0), "수급 배치 POST 가 X-Ops-Token 을 보낸다")
check("secrets.OPS_TOKEN" in wf, "워크플로 env 에 시크릿 OPS_TOKEN")

print()
if FAILS:
    print(f"실패 {len(FAILS)}건")
    sys.exit(1)
print("통과")
