"""16:00 수급에 KIS 오늘 행(장 마감 잠정)이 붙는지 가짜 응답으로 돌려 본다.

2026-10-14 사용자: "수급 데이터가 전일(10/13) 데이터가 날라온다 — 당일 데이터가
오게." 네이버 일별 매매동향은 당일 행이 ~18:30 에야 생겨 16:00 엔 전일까지다.
15:40 배치가 KIS FHKST01010900 의 오늘 행을 네이버 시계열 끝에 붙이고, 메시지는
그 행이 '잠정(KIS)' 임을 적는다. 진짜 server 모듈의 함수를 그대로 부른다
(SERVER_NO_STARTUP=1). **네트워크는 쓰지 않는다** — 네이버·KIS 둘 다 가짜다.

  a. KIS 최신 행이 오늘 → 오늘 행이 KIS 로 붙고, 문구가 '잠정(KIS)'
  b. KIS 최신 행이 어제 → 네이버만, 문구 '당일 확정 전'
  c. KIS 실패·키 없음 → 네이버만(예전 그대로) + 경고 기록, 키 없으면 1회만 시도
  d. 시그널(쌍끌이·연속·반전) 계산이 그대로 돈다 — 오늘 행이 들어가면 연속일이 하루 는다
  e. 16:00 보충은 오늘 행이 없는 종목만 · 시간 상한을 지킨다
"""
import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

os.environ["SERVER_NO_STARTUP"] = "1"
os.environ["USE_SQLITE"] = "1"
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
import server  # noqa: E402

FAIL = []


def want(cond, why):
    print(("PASS " if cond else "FAIL ") + why)
    if not cond:
        FAIL.append(why)


KST = timezone(timedelta(hours=9))
NOW = [datetime(2026, 10, 14, 15, 40, tzinfo=KST)]          # 수요일 · 거래일
server.now_kst = lambda: NOW[0]
TODAY = "2026-10-14"

# ── 임시 DB · 캐시 폴더 ────────────────────────────────────────────────
TMP = tempfile.mkdtemp(prefix="flowkis_")
DB = os.path.join(TMP, "t.db")
cx = sqlite3.connect(DB)
cx.executescript(open(os.path.join(ROOT, "db", "schema.sql"), encoding="utf-8").read())
cx.close()


@contextmanager
def gdb():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    try:
        yield c
    finally:
        c.close()


server._get_db = gdb
server.USE_SQLITE = True
server._SQLITE_OK = True
server.BASE_DIR = __import__("pathlib").Path(TMP)
server._get_stock_name = lambda code: f"종목{code}"


def bizdays(n, last="2026-10-13"):
    """last 까지 평일 n 개(오래된→최신)."""
    out, d = [], datetime.strptime(last, "%Y-%m-%d")
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.strftime("%Y-%m-%d"))
        d -= timedelta(days=1)
    return out[::-1]


NAVER_DATES = bizdays(20)


def naver_series(foreign, inst=None, close=10000):
    """네이버 _fetch_naver_trend 와 같은 모양(20행, 10/13 까지)."""
    inst = inst if inst is not None else [0] * 20
    return {"dates": list(NAVER_DATES), "closes": [close] * 20,
            "foreign_net": list(foreign), "inst_net": list(inst),
            "individual_net": [0] * 20, "foreign_hold_ratio": "50.0"}


NAVER = {}
server._fetch_naver_trend = lambda code, days=20: (
    {k: (list(v) if isinstance(v, list) else v) for k, v in NAVER[code].items()}
    if code in NAVER else None)

KIS = {}
KIS_CALLS = []


def fake_kis(code, ttl=0):
    KIS_CALLS.append(code)
    v = KIS.get(code)
    if callable(v):
        return v()
    return v or {"ok": True, "error": None, "rows": []}


server._kis_investor_daily = fake_kis


def kis_rows(date, f, i, p=0, close=10500):
    return {"ok": True, "error": None, "rows": [
        {"date": date.replace("-", ""), "foreign_net": f, "inst_net": i,
         "retail_net": p, "close": close,
         "blank": all(v in (None, 0) for v in (f, i, p))},
        {"date": "20261013", "foreign_net": 1, "inst_net": 1, "retail_net": 1,
         "close": 10000, "blank": False}]}


def flow_row(code):
    with gdb() as c:
        r = c.execute("SELECT * FROM flow_cache WHERE code=?", (code,)).fetchone()
    return dict(r) if r else None


def J(s):
    return server._parse_json_list(s)


def errors(source):
    return [e for e in server._COLLECT_ERRORS if e.get("source") == source]


# ── a. KIS 최신 행이 오늘 ───────────────────────────────────────────────
# 외국인 10/13 까지 3일 연속 순매수 + 오늘(KIS)도 순매수 → 연속 4일
NAVER["000001"] = naver_series([-5] * 17 + [8000, 9000, 9000], [100] * 20)
KIS["000001"] = kis_rows(TODAY, 1_000_000, 700_000, -1_700_000, close=10500)
want(server._fetch_and_save_flow("000001") is True, "a. 저장 성공")
r = flow_row("000001")
d = J(r["dates_json"])
want(d[-1] == TODAY and len(d) == 20, f"a. 오늘 행이 붙고 20행 유지 — 마지막 {d[-1]}, {len(d)}행")
want(d[0] == NAVER_DATES[1], "a. 가장 오래된 하루가 밀려났다(20일 누적 뜻 유지)")
want(J(r["foreign_shares_json"])[-1] == 1_000_000, "a. 오늘 외인 수량이 KIS 값")
want(J(r["foreign_value_json"])[-1] == 1_000_000 * 10500,
     "a. 오늘 금액 = KIS 수량 × KIS 종가(네이버 행과 같은 근사)")
want(J(r["foreign_shares_json"])[:-1] == NAVER["000001"]["foreign_net"][1:],
     "a. 과거 행은 네이버 그대로")
want(server._flow_last_is_kis(r["source"], d), f"a. source 꼬리표가 KIS 잠정 — {r['source']}")
import json as _json  # noqa: E402
cj = _json.loads(open(os.path.join(TMP, "cache", "flow_000001.json"), encoding="utf-8").read())
want(cj.get("row_source", [])[-1:] == ["KIS"] and cj.get("provisional", [])[-1:] == [True]
     and not any(cj["provisional"][:-1]),
     "a. 행별 표시 — 마지막만 source=KIS · provisional=True")
with gdb() as c:
    fm, latest = server._newhigh_flow(c, ["000001"])
want(latest == TODAY and fm["000001"][3] is True, f"a. 신고가 수급이 KIS 행을 안다 — {fm}")
want("외인 +105억" in server._fmt_nh_flow(fm["000001"], latest),
     f"a. 신고가 꼬리 값 — {server._fmt_nh_flow(fm['000001'], latest)!r}")
ph = server._flow_basis_phrase(TODAY, [(v[2], v[3]) for v in fm.values()])
want(ph == "수급은 10/14 장 마감 잠정(KIS)", f"a. 머리말 문구 — {ph!r}")

# ── b. KIS 최신 행이 어제(오늘 행 아직 없음) ────────────────────────────
NAVER["000002"] = naver_series([100] * 20)
KIS["000002"] = kis_rows("2026-10-13", 5, 5)
KIS["000002"]["rows"] = KIS["000002"]["rows"][1:]
server._fetch_and_save_flow("000002")
r2 = flow_row("000002")
want(J(r2["dates_json"])[-1] == "2026-10-13" and r2["source"] == "naver_mobile_api",
     f"b. 네이버만 — {J(r2['dates_json'])[-1]} / {r2['source']}")
with gdb() as c:
    fm2, latest2 = server._newhigh_flow(c, ["000002"])
ph2 = server._flow_basis_phrase(TODAY, [(v[2], v[3]) for v in fm2.values()])
want(ph2 == "수급은 10/13 기준(당일 확정 전)", f"b. 문구 '당일 확정 전' — {ph2!r}")
# 오늘 행은 있지만 세 값이 빈칸/0 → 집계 전으로 보고 안 붙인다
NAVER["000003"] = naver_series([100] * 20)
KIS["000003"] = kis_rows(TODAY, None, None, None)
server._fetch_and_save_flow("000003")
want(J(flow_row("000003")["dates_json"])[-1] == "2026-10-13", "b. 빈 오늘 행은 붙이지 않는다")
# 섞인 경우 — 둘 다 적는다
mix = server._flow_basis_phrase(TODAY, [(TODAY, True), (TODAY, True), ("2026-10-13", False)])
want("10/14 장 마감 잠정(KIS) 2종목" in mix and "1종목은 10/13 기준(당일 확정 전)" in mix,
     f"b. 섞이면 둘 다 — {mix!r}")
want(server._flow_basis_phrase(TODAY, [(TODAY, False)]) == "",
     "b. 오늘 확정치(네이버)뿐이면 덧붙일 말 없음")

# 네이버가 이미 오늘 행을 가진 저녁 — KIS 를 부르지도, 덮지도 않는다
NOW[0] = datetime(2026, 10, 14, 19, 0, tzinfo=KST)
NAVER["000004"] = naver_series([100] * 20)
NAVER["000004"]["dates"] = bizdays(20, TODAY)
KIS["000004"] = kis_rows(TODAY, 999, 999)
KIS_CALLS.clear()
server._fetch_and_save_flow("000004")
want(not KIS_CALLS and flow_row("000004")["source"] == "naver_mobile_api",
     "b. 네이버 오늘(확정) 행이 있으면 KIS 를 안 부른다")
# 장중(15:30 전)에는 KIS 를 붙이지 않는다(부분치)
NOW[0] = datetime(2026, 10, 14, 11, 0, tzinfo=KST)
KIS_CALLS.clear()
server._fetch_and_save_flow("000001")
want(not KIS_CALLS and J(flow_row("000001")["dates_json"])[-1] == "2026-10-13",
     "b. 15:30 전에는 KIS 를 안 부른다")
NOW[0] = datetime(2026, 10, 14, 15, 40, tzinfo=KST)

# ── c. KIS 실패 · 키 없음 ───────────────────────────────────────────────
NAVER["000005"] = naver_series([100] * 20)
KIS["000005"] = {"ok": False, "error": "rt_cd=1 초당 거래건수 초과", "rows": []}
n_err = len(errors("kis_investor"))
want(server._fetch_and_save_flow("000005") is True, "c. KIS 실패해도 네이버 저장은 성공")
r5 = flow_row("000005")
want(J(r5["dates_json"])[-1] == "2026-10-13" and r5["source"] == "naver_mobile_api",
     "c. KIS 실패 → 예전 그대로(네이버만)")
want(len(errors("kis_investor")) == n_err + 1, "c. 실패를 collect_errors 에 남긴다")

# 배치 — 키가 없으면 첫 종목에서 KIS 를 접는다(200번 두드리지 않는다)
with gdb() as c:
    for i, code in enumerate(["000011", "000012", "000013"]):
        c.execute("INSERT INTO stocks (code, name, market, market_cap, close, is_etf) "
                  "VALUES (?,?,?,?,?,0)", (code, f"종목{code}", "KOSPI", 1e12 - i, 10000))
        NAVER[code] = naver_series([100] * 20)
        KIS[code] = {"ok": False, "error": "no_key", "rows": []}
    c.commit()
KIS_CALLS.clear()
n_err = len(errors("kis_investor"))
res = server._refresh_flow_batch(top_n=3)
want(res.get("ok") and res.get("success") == 3, f"c. 키 없어도 배치는 네이버로 성공 — {res}")
want(KIS_CALLS == ["000011"], f"c. 키 없음 → KIS 1회만 시도 — {KIS_CALLS}")
want(len(errors("kis_investor")) == n_err + 1, "c. 배치 KIS 실패는 한 줄로 남긴다")
want(all(J(flow_row(c)["dates_json"])[-1] == "2026-10-13"
         for c in ("000011", "000012", "000013")), "c. 키 없음 → 전부 전일까지(예전 동작)")

# 배치 — 정상 KIS 면 오늘 행이 붙는다
for code in ("000011", "000012", "000013"):
    KIS[code] = kis_rows(TODAY, 10, 10)
KIS["000013"] = kis_rows("2026-10-13", 10, 10)
KIS["000013"]["rows"] = KIS["000013"]["rows"][1:]
res = server._refresh_flow_batch(top_n=3)
want(res.get("kis_today") == 2 and res.get("kis_fail") == 0, f"c'. 배치 KIS 집계 — {res}")

# ── d. 시그널 계산 ──────────────────────────────────────────────────────
# 시그널은 '공통 최신일' 종목만 본다. 여기서 최신일은 오늘(KIS)이다.
SENT = []
server.send_telegram = lambda m: SENT.append(m) or True
OPS = {}
server._ops_get = lambda k, dflt=None: OPS.get(k, dflt)
server._ops_set = lambda k, v: OPS.__setitem__(k, str(v))
# 기존 행 정리 후 시나리오 3종목
with gdb() as c:
    c.execute("DELETE FROM flow_cache")
    c.commit()
# 쌍끌이 + 연속: 10/13 까지 외인 3일 순매수, 오늘 KIS 외인·기관 강한 순매수
NAVER["100001"] = naver_series([-1] * 17 + [1_000_000] * 3, [0] * 20)
KIS["100001"] = kis_rows(TODAY, 1_000_000, 800_000, close=10000)   # 100억 · 80억
# 반전: 직전 3일 순매도 → 오늘 강한 순매수
NAVER["100002"] = naver_series([0] * 17 + [-100_000] * 3)
KIS["100002"] = kis_rows(TODAY, 900_000, 0, -900_000, close=10000)  # 90억
# 오늘 행을 못 받은 종목 — 시그널에서 빠지고 문구에 적힌다
NAVER["100003"] = naver_series([100_000] * 20, [100_000] * 20)
KIS["100003"] = {"ok": False, "error": "timeout", "rows": []}
for code in ("100001", "100002", "100003"):
    server._fetch_and_save_flow(code)
sig = server._analyze_flow_signals()
want(sig["date"] == TODAY, f"d. 시그널 기준일이 오늘 — {sig['date']}")
want([c["code"] for c in sig["dual_buy"]] == ["100001"], f"d. 쌍끌이 — {sig['dual_buy']}")
want(sig["streak_buy"] and sig["streak_buy"][0]["fstreak_buy"] == 4,
     f"d. 연속 순매수가 오늘 포함 4일 — {sig['streak_buy']}")
want([c["code"] for c in sig["reversal"]] == ["100002"], f"d. 반전 — {sig['reversal']}")
want(sig["kis_n"] == 2 and sig["behind_n"] == 1 and sig["behind_date"] == "2026-10-13",
     f"d. KIS 2종목 · 빠진 1종목 — {sig.get('kis_n')}/{sig.get('behind_n')}")
NOW[0] = datetime(2026, 10, 14, 16, 0, tzinfo=KST)
want(server.alert_flow_signals(scheduled=True) is True and SENT, "d. 시그널 발송")
msg = SENT[-1] if SENT else ""
want("잠정(KIS)" in msg and "10/14" in msg, f"d. 시그널 문구 '잠정(KIS)' — {msg[:160]!r}")
want("1종목은 10/13 기준(당일 확정 전)" in msg, "d. 빠진 종목을 정직하게 적는다")

# 네이버만(예전) 데이터에서도 시그널이 예전처럼 돈다 — 기준일 문구도 예전 그대로
with gdb() as c:
    c.execute("DELETE FROM flow_cache")
    c.commit()
KIS["100001"] = KIS["100002"] = {"ok": True, "error": None, "rows": []}
NOW[0] = datetime(2026, 10, 14, 15, 40, tzinfo=KST)
for code in ("100001", "100002"):
    server._fetch_and_save_flow(code)
sig = server._analyze_flow_signals()
want(sig["date"] == "2026-10-13" and sig["kis_n"] == 0, "d. 네이버만 → 기준일 전일 · KIS 0")
want(sig["streak_buy"] and sig["streak_buy"][0]["fstreak_buy"] == 3,
     f"d. 네이버만 연속 3일(예전 그대로) — {sig['streak_buy']}")
NOW[0] = datetime(2026, 10, 14, 16, 0, tzinfo=KST)
OPS.clear()
server.alert_flow_signals(scheduled=True)
want("기준일 10/13" in SENT[-1] and "최신 확정치" in SENT[-1] and "KIS" not in SENT[-1],
     f"d. 네이버만이면 예전 문구 — {SENT[-1][:160]!r}")

# ── e. 16:00 보충 ───────────────────────────────────────────────────────
# 위 상태: 100001·100002 가 10/13 까지. 이제 KIS 오늘 행이 올라왔다.
KIS["100001"] = kis_rows(TODAY, 1_000_000, 800_000, close=10000)
KIS["100002"] = kis_rows(TODAY, 900_000, 0, close=10000)
KIS_CALLS.clear()
out = server._topup_flow_today_kis(budget_sec=60)
want(out["targets"] == 2 and out["merged"] == 2, f"e. 오늘 행 없는 2종목만 보충 — {out}")
want(J(flow_row("100001")["dates_json"])[-1] == TODAY, "e. 보충 후 오늘 행")
KIS_CALLS.clear()
out = server._topup_flow_today_kis(budget_sec=60)
want(out["targets"] == 0 and not KIS_CALLS, f"e. 이미 오늘 행이 있으면 안 부른다 — {out}")
# 시간 상한 — 0초면 아무것도 안 부르고 돌아간다
with gdb() as c:
    c.execute("DELETE FROM flow_cache")
    c.commit()
for code in ("100001", "100002"):
    KIS[code] = {"ok": True, "error": None, "rows": []}
    server._fetch_and_save_flow(code)
KIS_CALLS.clear()
out = server._topup_flow_today_kis(budget_sec=-1)
want(out["skipped_budget"] == 2 and not KIS_CALLS, f"e. 상한을 넘기면 멈춘다 — {out}")
# 15:40 배치가 오늘 돌았는지 — 16:00 은 보충만 한다
want(server._flow_batch_ran_today(min_rows=2) is True, "e. 오늘 15:30 이후 행이 있으면 배치가 돈 것")
want(server._flow_batch_ran_today(min_rows=50) is False, "e. 행이 모자라면 전체 재수집")

print("---")
print("통과" if not FAIL else f"실패 {len(FAIL)}건")
sys.exit(0 if not FAIL else 1)
