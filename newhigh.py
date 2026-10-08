"""
newhigh.py — 국내 신고가 3축(역사적 · 52주 · 120일) 판정.

■ 정의 (사용자 요청 2026-10-08 — "특정일을 기준으로 하는 게 아닌, 당일 기준으로")

  축    | 비교 창 (모두 판정일 당일 기준, 당일 봉은 창에서 뺀다)
  ------+-----------------------------------------------------------------
  d120  | 판정일 직전 **120 거래일** — KRX 거래일 달력(krx_calendar)으로 센
        | 120번째 이전 거래일 ~ 전 거래일. 종목이 거래정지였던 날로 창이
        | 늘어나지 않는다: 경계는 시장 달력이 정하고 그 안의 그 종목 봉만 본다.
  w52   | **달력 52주** — 판정일 − 364일 ≤ 봉 날짜 < 판정일
  hist  | **상장 이후 전체**(판정일 전까지). '받아 둔 일봉의 첫날' 이 아니다.

  - **종가 기준**이다(이 저장소의 원래 기준 — 그대로 둔다). 오늘 종가 **>** 창 안
    최고 종가면 그 축의 신고가 — **엄격히 초과**다(ETF-Traker 원본도 '>').
    같은 가격을 신고가로 치면 거래가 없거나 상한가에 묶여 종가가 그대로인
    날마다 같은 종목이 신고가로 찍힌다. 예전 이 저장소는 '>=' 였다.
  - 라벨은 가장 센 축 하나: hist > w52 > d120. 한 종목은 한 줄에만 나온다.
  - 창을 다 채우지 못한 종목(상장이 창보다 늦다 등)은 그 축을 판정하지 않는다
    (None). 신규상장은 hist 로만 판정될 수 있다.
  - 60일 축은 없앴다(120일로 바꿨다).

■ 역사적 — 상장 이후 이력을 어디서 얻는가

ohlcv 는 52주 + 여유만 받아 둔다(ohlcv_autofill.LOOKBACK_CALENDAR_DAYS, Render
무료 인스턴스 사정). 그래서 '상장 이후 최고 종가' 는 따로 둔다 —
`alltime_high` 표. 채우는 대상은 **그날 역사적일 수 있는 몇 종목뿐**이다
(52주를 뚫었거나, 52주를 판정할 수 없는데 받아 둔 전 구간을 뚫은 종목).
처음 보는 종목은 상장 이후 전 구간을 한 번 받고(ohlcv_autofill 의 소스 순서
네이버 → pykrx), 그 뒤로는 ohlcv 의 새 봉으로 앞으로만 굴린다.

  - 표는 db_backup.CORE_TABLES 에 실어 Gist 로 백업한다(수천 행 이하·작다).
    Render 재시작으로 DB 가 비어도 복원되고, 복원이 안 되면 그날 후보만 다시 받는다.
  - 수정주가가 바뀌면(분할·병합) 저장해 둔 기준 종가(ref_close)가 ohlcv 의 같은
    날 종가와 어긋난다 — 그러면 다시 받는다.
  - **이력을 못 받으면 '역사적' 이라고 부르지 않는다.** 그 종목은 52주(또는
    120일)에 남고, 머리말이 '역사적 판정 보류 N종목' 을 적는다. 짧은 구간으로
    조용히 대신하지 않는다.
  - 시황 빌드에는 시간 예산이 있다(`HIST_BUDGET_BRIEF_S`). 넘으면 남은 종목은
    보류로 돌린다. 15:48 프리워밍이 더 긴 예산으로 먼저 채워 두므로 16:00
    시황은 대개 표만 읽는다.

  '상장일까지 닿았다' 의 판정: 받은 이력이 받아 둔 일봉보다 앞서야 하고(아니면
  보류), 그 첫날이 상장일이거나 **원천의 바닥(`HIST_SOURCE_FLOOR`)** 이면 닿은
  것으로 본다. 네이버 siseJson 은 1990-01-03 보다 이른 봉을 주지 않는다
  (2026-10-08 삼성전자로 확인 — 9,483봉, 한 번에 2초). 그래서 1990 년 이전 상장
  종목의 '역사적' 은 1990-01-03 이후 최고가이고, 그런 종목이 그날 역사적 줄에
  있으면 기준 줄이 그 사실을 한 번 적는다. 받은 이력의 첫날은 표의 first_date 에
  남는다.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta

import krx_calendar

log = logging.getLogger(__name__)

# ─────────────────────────── 설정 (바꾸려면 여기만) ───────────────────────────
D120_LOOKBACK_TRADING_DAYS = 120          # 120일 신고가 = 120 거래일
W52_LOOKBACK_CALENDAR_DAYS = 364          # 52주 신고가 = 달력 52주 (오늘 − 364일 ≤ 날짜)
# 창의 첫날 **이전**에 그 종목 봉이 있는지 보려고 조금 더 앞에서부터 훑는다.
SCAN_MARGIN_DAYS = 45

# 상장 이후 이력을 받을 때의 시작일 — 소스가 가진 첫날부터 돌려준다.
HIST_FETCH_START = "19800101"
# 원천(네이버 siseJson)이 주는 가장 이른 봉. 이력의 첫날이 이 날 이하면 상장일이
# 그보다 앞서더라도 '받을 수 있는 전부' 에 닿은 것으로 본다.
HIST_SOURCE_FLOOR = "1990-01-03"
HIST_FLOOR_NOTE = f"1990년 이전 상장은 {HIST_SOURCE_FLOOR} 이후 최고가"
HIST_FETCH_WORKERS = 4
HIST_BUDGET_BRIEF_S = 60                  # 16:00 시황 빌드 안에서 쓰는 예산
HIST_BUDGET_PREWARM_S = 240               # 15:48 프리워밍 예산
# 저장해 둔 기준 종가와 ohlcv 의 같은 날 종가가 이만큼 넘게 다르면 수정주가가
# 바뀐 것으로 보고 다시 받는다.
REF_CLOSE_TOL = 0.005

# (키, 이름, 아이콘) — 센 순서. 텔레그램 소제목과 기준 줄이 이 이름을 쓴다.
AXES = (("hist", "역사적", "🏔"), ("w52", "52주", "📈"), ("d120", "120일", "📊"))
BASIS_TEXT = ("종가 기준 · 오늘 종가 vs 전 거래일까지 · 역사적=상장 이후 · "
              "52주=달력 52주 · 120일=120거래일")

# 판정 모집단 — ETF 아님·시총 하한 이상·오늘 거래됨·1,000원 이상.
UNIVERSE_WHERE = """(s.market = '' OR s.market LIKE 'KOS%')
                  AND COALESCE(s.is_etf, 0) = 0
                  AND s.close >= 1000 AND s.change_pct IS NOT NULL
                  AND COALESCE(s.volume_mn, 0) > 0
                  AND COALESCE(s.market_cap, 0) >= :min_cap"""

SCAN_SQL = f"""
    SELECT s.code AS code, s.name AS name, s.sector AS sector,
           s.change_pct AS change_pct, s.close AS close,
           s.volume_mn AS volume_mn, s.market_cap AS market_cap,
           s.market_cap_updated AS market_cap_updated,
           MAX(CASE WHEN o.date >= :d120_start THEN o.close END) AS h120,
           MAX(CASE WHEN o.date >= :w52_start THEN o.close END) AS h52,
           MAX(o.close) AS h_scan,
           MIN(o.date) AS first_bar,
           MAX(o.date) AS last_bar
    FROM stocks s
    JOIN ohlcv o ON o.code = s.code
                AND o.date >= :scan_start AND o.date < :today
    WHERE {UNIVERSE_WHERE}
    GROUP BY s.code
"""

UNIVERSE_SQL = f"SELECT COUNT(*) FROM stocks s WHERE {UNIVERSE_WHERE}"

ALLTIME_DDL = """
CREATE TABLE IF NOT EXISTS alltime_high (
    code         TEXT PRIMARY KEY,
    max_close    REAL NOT NULL,   -- 상장 이후 through_date 까지 최고 종가
    max_date     TEXT,
    first_date   TEXT,            -- 받은 이력의 첫날 (상장일 또는 소스의 첫날)
    through_date TEXT NOT NULL,   -- 이 값이 반영한 마지막 봉 날짜
    ref_close    REAL,            -- through_date 의 종가 (수정주가 재조정 감지)
    source       TEXT,
    complete     INTEGER NOT NULL DEFAULT 0,
    updated_at   TEXT
)"""

_HIST_LOCK = threading.Lock()


def _iso(d) -> str:
    if isinstance(d, datetime):
        d = d.date()
    if isinstance(d, date):
        return d.isoformat()
    s = str(d).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10]


# ─────────────────────────── 창 ───────────────────────────
def observed_counts(conn, start: str, end: str) -> dict:
    """{날짜: 그날 봉을 가진 종목 수} — 달력을 바로잡는 증거(krx_calendar)."""
    try:
        return {r[0]: r[1] for r in conn.execute(
            "SELECT date, COUNT(*) FROM ohlcv "
            "WHERE code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]' "
            "AND date >= ? AND date < ? GROUP BY date", (start, end)).fetchall()}
    except Exception as exc:                               # noqa: BLE001
        log.debug("[신고가] 관측 거래일 조회 실패: %s", exc)
        return {}


def windows(today, cal: "krx_calendar.Calendar | None" = None) -> dict:
    """판정일 `today` 의 세 창 경계. 모두 'YYYY-MM-DD'.

    d120_start  120번째 이전 거래일 (창에 포함)
    w52_start   today − 364일 (창에 포함)
    w52_first   w52 창 안의 첫 거래일 — '창을 다 채웠는가' 판정에 쓴다
    prev_day    전 거래일
    """
    cal = cal or krx_calendar.Calendar()
    t = date.fromisoformat(_iso(today))
    w52 = t - timedelta(days=W52_LOOKBACK_CALENDAR_DAYS)
    d120 = cal.nth_trading_day_before(t, D120_LOOKBACK_TRADING_DAYS)
    prev = cal.nth_trading_day_before(t, 1)
    w52_first = w52
    while not cal.is_trading_day(w52_first) and w52_first < t:
        w52_first += timedelta(days=1)
    starts = [w52] + ([date.fromisoformat(d120)] if d120 else [])
    scan = min(starts) - timedelta(days=SCAN_MARGIN_DAYS)
    return {"today": t.isoformat(), "d120_start": d120, "w52_start": w52.isoformat(),
            "w52_first": w52_first.isoformat(), "prev_day": prev,
            "scan_start": scan.isoformat()}


def axis_flags(row, w: dict) -> dict:
    """한 종목의 d120·w52 판정. True/False/None(창을 다 못 채움)."""
    c = row["close"]
    first = row["first_bar"]
    out = {}
    for key, hk, start in (("d120", "h120", w["d120_start"]),
                           ("w52", "h52", w["w52_first"])):
        h = row[hk]
        if not c or not start or not first or first > start or h is None:
            out[key] = None
        else:
            out[key] = c > h           # 엄격히 초과 — 같은 가격은 신고가가 아니다
    return out


def is_hist_candidate(row, flags: dict) -> bool:
    """역사적일 수 있는가 — 상장 이후 이력을 확인할 가치가 있는 종목.

    52주를 뚫었으면 후보. 52주를 판정할 수 없으면(신규상장·받아 둔 구간이
    짧음) 받아 둔 전 구간 최고를 뚫었을 때만 후보. 52주를 못 뚫었으면 역사적일
    수 없다(52주 창 ⊂ 상장 이후).
    """
    if flags.get("w52") is True:
        return True
    if flags.get("w52") is None:
        return bool(row["close"] and row["h_scan"] is not None
                    and row["close"] > row["h_scan"])
    return False


# ─────────────────────────── 역사적 캐시 ───────────────────────────
def ensure_table(conn) -> None:
    conn.execute(ALLTIME_DDL)


def _default_fetch(code: str, start: str, end: str):
    """(소스, 행들, 오류들). ohlcv_autofill 의 소스 순서(네이버 → pykrx)를 그대로 쓴다."""
    import ohlcv_autofill as oa
    _c, src, rows, errs = oa._fetch_one(code, start, end, oa.SOURCE_ORDER, 0)
    return src, rows, errs


def _summarize(rows, today_iso: str):
    """받은 일봉 → (최고 종가, 그 날, 첫날, 마지막 날, 마지막 날 종가). 오늘 봉은 뺀다."""
    best = None
    first = last = None
    last_close = None
    for r in sorted(rows, key=lambda x: x[1]):
        d, c = r[1], r[5]
        if d >= today_iso or c is None or c <= 0:
            continue
        if first is None:
            first = d
        last, last_close = d, c
        if best is None or c > best[0]:
            best = (c, d)
    if best is None:
        return None
    return best[0], best[1], first, last, last_close


def resolve_hist(conn, cands: list[dict], today, *, fetch=None,
                 budget_s: float = HIST_BUDGET_BRIEF_S,
                 workers: int = HIST_FETCH_WORKERS) -> dict:
    """후보 종목들의 상장 이후 최고 종가. {코드: {'max': 값} | {'pending': 사유}}.

    `cands` 는 [{'code', 'first_bar'}] — first_bar 는 ohlcv 에 받아 둔 그 종목의
    첫날(훑은 구간 안). 소스가 그보다 짧은 이력을 주면 상장일까지 닿았다고 볼 수
    없으므로 보류한다.
    """
    today_iso = _iso(today)
    fetch = fetch or _default_fetch
    ensure_table(conn)
    out: dict = {}
    if not cands:
        return out
    codes = [c["code"] for c in cands]
    first_by = {c["code"]: c.get("first_bar") for c in cands}
    qs = ",".join("?" * len(codes))
    cached = {r[0]: r for r in conn.execute(
        f"SELECT code, max_close, max_date, first_date, through_date, ref_close, "
        f"source, complete FROM alltime_high WHERE code IN ({qs})", codes).fetchall()}

    need_fetch: list[str] = []
    updates: list[tuple] = []
    for code in codes:
        row = cached.get(code)
        if not row or not row[7]:
            need_fetch.append(code)
            continue
        _, mx, mxd, fd, thr, refc, src, _cp = row
        # 받아 둔 일봉이 저장된 이력보다 앞선다 → 저장된 이력이 상장일까지 안 닿는다.
        if first_by.get(code) and fd and fd > first_by[code]:
            need_fetch.append(code)
            continue
        # 수정주가 재조정 감지 — 같은 날 종가가 달라졌으면 처음부터 다시 받는다.
        r2 = conn.execute("SELECT close FROM ohlcv WHERE code = ? AND date = ?",
                          (code, thr)).fetchone()
        if r2 and refc and r2[0] and abs(r2[0] - refc) / refc > REF_CLOSE_TOL:
            need_fetch.append(code)
            continue
        # 앞으로 굴린다 — ohlcv 가 through_date 를 품고 있어야 그 사이가 빈틈없다.
        cover = conn.execute("SELECT MIN(date) FROM ohlcv WHERE code = ?",
                             (code,)).fetchone()[0]
        newer = conn.execute(
            "SELECT date, close FROM ohlcv WHERE code = ? AND date > ? AND date < ? "
            "ORDER BY date", (code, thr, today_iso)).fetchall()
        if newer and (cover is None or cover > thr):
            need_fetch.append(code)              # 빈틈 — 다시 받는다
            continue
        for d, c in newer:
            if c and c > mx:
                mx, mxd = c, d
        if newer:
            thr, refc = newer[-1][0], newer[-1][1]
            updates.append((code, mx, mxd, fd, thr, refc, src, 1))
        out[code] = {"max": mx, "max_date": mxd, "first_date": fd, "fetched": False}

    if need_fetch:
        # 예산은 락을 기다리는 시간까지 포함한다 — 합쳐서 budget_s 를 넘지 않는다.
        deadline = time.monotonic() + budget_s
        end = (date.fromisoformat(today_iso) - timedelta(days=1)).strftime("%Y%m%d")
        if not _HIST_LOCK.acquire(timeout=max(0.1, budget_s)):
            for code in need_fetch:
                out[code] = {"pending": "다른 이력 조회가 진행 중"}
        else:
            try:
                pool = ThreadPoolExecutor(max_workers=max(1, workers),
                                          thread_name_prefix="alltime")
                futs = {pool.submit(fetch, code, HIST_FETCH_START, end): code
                        for code in need_fetch}
                done, not_done = wait(futs, timeout=max(0.0, deadline - time.monotonic()))
                pool.shutdown(wait=False, cancel_futures=True)
                for f in not_done:
                    out[futs[f]] = {"pending": f"시간 예산 {budget_s:g}초 초과"}
                for f in done:
                    code = futs[f]
                    try:
                        src, rows, errs = f.result()
                    except Exception as exc:               # noqa: BLE001
                        out[code] = {"pending": f"{type(exc).__name__}: {str(exc)[:60]}"}
                        continue
                    s = _summarize(rows or [], today_iso)
                    if not src or s is None:
                        why = (errs or ["이력을 못 받음"])[0]
                        out[code] = {"pending": str(why)[:80]}
                        continue
                    mx, mxd, fd, last, lastc = s
                    fb = first_by.get(code)
                    if fb and fd > fb:
                        out[code] = {"pending": f"받은 이력({fd}~)이 받아 둔 일봉({fb}~)보다 짧다"}
                        continue
                    updates.append((code, mx, mxd, fd, last, lastc, src, 1))
                    out[code] = {"max": mx, "max_date": mxd, "first_date": fd,
                                 "fetched": True}
            finally:
                _HIST_LOCK.release()

    if updates:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.executemany(
            "INSERT OR REPLACE INTO alltime_high (code, max_close, max_date, "
            "first_date, through_date, ref_close, source, complete, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)", [u + (stamp,) for u in updates])
        conn.commit()
    return out


# ─────────────────────────── 본체 ───────────────────────────
def compute(conn, today, min_cap: float, *, fetch=None,
            budget_s: float = HIST_BUDGET_BRIEF_S, cal=None) -> dict:
    """오늘 신고가 3축. 반환 dict:

      error        판정을 못 한 이유 (있으면 나머지는 비어 있다)
      windows      창 경계
      buckets      {'hist': [행], 'w52': [...], 'd120': [...]} — 가장 센 축 하나에만
      scanned      판정한 종목 수(일봉이 있는 종목) · universe_n 모집단 수
      hist_pending [(코드, 이름, 사유)] — 역사적일 수 있었으나 이력을 못 확인
      last_bar     훑은 일봉의 최신 날짜
      calendar_corrections  받아 둔 일봉이 휴장일 표를 바로잡은 날들
    """
    t = date.fromisoformat(_iso(today))
    lookback = t - timedelta(days=W52_LOOKBACK_CALENDAR_DAYS + SCAN_MARGIN_DAYS + 200)
    cal = cal or krx_calendar.Calendar(observed_counts(conn, lookback.isoformat(),
                                                       t.isoformat()))
    w = windows(t, cal)
    res = {"error": None, "windows": w, "buckets": {k: [] for k, _, _ in AXES},
           "scanned": 0, "universe_n": 0, "hist_pending": [], "last_bar": None,
           "first_bar": None, "calendar_corrections": list(cal.corrections)}
    if not w["d120_start"]:
        res["error"] = "거래일 달력으로 120거래일 창을 못 만든다"
        return res
    params = {"d120_start": w["d120_start"], "w52_start": w["w52_start"],
              "scan_start": w["scan_start"], "today": w["today"], "min_cap": min_cap}
    rows = _as_dicts(conn, SCAN_SQL, params)
    res["universe_n"] = conn.execute(UNIVERSE_SQL, {"min_cap": min_cap}).fetchone()[0]
    res["scanned"] = len(rows)
    if rows:
        res["last_bar"] = max(r["last_bar"] for r in rows)
        res["first_bar"] = min(r["first_bar"] for r in rows)
    if not rows or res["first_bar"] > w["d120_start"]:
        res["error"] = (f"일봉이 {res['first_bar'] or '없음'}~ 뿐 — 120거래일 창"
                        f"({w['d120_start']}~)을 못 채운다")
        return res

    for r in rows:
        r.update(axis_flags(r, w))
        r["hist"] = None
    cands = [r for r in rows if is_hist_candidate(r, r)]
    resolved = resolve_hist(conn, [{"code": r["code"], "first_bar": r["first_bar"]}
                                   for r in cands], t, fetch=fetch, budget_s=budget_s)
    for r in cands:
        got = resolved.get(r["code"]) or {"pending": "조회되지 않음"}
        if "max" in got:
            # 받아 둔 일봉은 상장 이후 이력의 일부라 그 최고도 함께 본다.
            hist_max = max(got["max"], r["h_scan"] or 0)
            r["hist"] = r["close"] > hist_max
            r["hist_first_date"] = got.get("first_date")
        else:
            res["hist_pending"].append((r["code"], r["name"], got["pending"]))
    if cands:
        log.info("[신고가] 역사적 후보 %d종목 — 이력 새로 받음 %d · 표에서 %d · 보류 %d",
                 len(cands), sum(1 for v in resolved.values() if v.get("fetched")),
                 sum(1 for v in resolved.values() if v.get("fetched") is False),
                 len(res["hist_pending"]))

    for r in rows:
        if r["hist"] is True:
            res["buckets"]["hist"].append(r)
        elif r["w52"] is True:
            res["buckets"]["w52"].append(r)
        elif r["d120"] is True:
            res["buckets"]["d120"].append(r)
    res["calendar_corrections"] = list(cal.corrections)
    return res


def _as_dicts(conn, sql, params):
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def basis_line(scope: str, res: dict) -> str:
    """기준 줄 — 섹션 머리에 한 번. 특정일 표기('역사적=일봉 YYYY-MM-DD~')는 없다."""
    s = f"{scope} · {BASIS_TEXT}"
    w = res.get("windows") or {}
    # 일봉이 전 거래일까지 안 차 있으면 '전 거래일까지' 가 거짓이 된다. 그 날을 적는다.
    if res.get("last_bar") and w.get("prev_day") and res["last_bar"] < w["prev_day"]:
        s += f" · 일봉은 {res['last_bar']}까지"
    # 1990 년 이전 상장 종목이 그날 역사적 줄에 있을 때만, 한 번.
    if any((r.get("hist_first_date") or "9999") <= HIST_SOURCE_FLOOR
           for r in (res.get("buckets") or {}).get("hist") or []):
        s += f" · {HIST_FLOOR_NOTE}"
    pend = res.get("hist_pending") or []
    if pend:
        s += f" · 역사적 판정 보류 {len(pend)}종목(상장 이후 이력 미확인)"
    return s
