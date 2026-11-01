"""
movers.py — '📈 신고가 · 등락률' 화면의 데이터.

모집단은 신고가 판정과 **같다**(newhigh.UNIVERSE_WHERE — 코스피·코스닥, 시총 하한 이상,
ETF·ETN·리츠·인프라·스팩 제외, 오늘 거래됨). 한 화면 안에서 신고가와 등락률이 서로
다른 종목 집합을 보지 않게 한다.

  신고가 · 일간   newhigh.compute(budget_s=0) — 네트워크로 상장 이후 이력을 받지 않는다
                  (표 alltime_high 에 있는 것만 '역사적'. 없으면 보류로 남고 52주 이하에 선다).
                  16:00 시황·15:48 프리워밍이 표를 채운다.
  등락률 · 일간   stocks.change_pct — 장중이면 지금 값.
  등락률 · 주간   받아 둔 일봉 마지막 날(D) 종가 ÷ D 의 5거래일 전 종가 − 1.
                  일봉 기준이라 장중에는 어제까지다 — 화면에 기준일을 적는다.

시장(코스피·코스닥) 구분은 종목 마스터에서 가져온다(stocks.market 은 KR 행이 '').
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, timedelta

import krx_calendar
import newhigh as nh

log = logging.getLogger(__name__)

WEEK_TRADING_DAYS = 5
TOP_N = 50
MARKETS = ("KOSPI", "KOSDAQ")
AXIS_LABEL = {k: lbl for k, lbl, _ in nh.AXES}

_COLS = ("s.code AS code, s.name AS name, s.sector AS sector, s.close AS close, "
         "s.change_pct AS change_pct, s.volume_mn AS volume_mn, s.market_cap AS market_cap")


def universe(conn, min_cap: float) -> list[dict]:
    """신고가와 같은 모집단의 오늘 행들."""
    nh.ensure_exclusions(conn)
    cur = conn.execute(f"SELECT {_COLS} FROM stocks s WHERE {nh.UNIVERSE_WHERE}",
                       {"min_cap": min_cap})
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def tag_market(rows: list[dict], markets: dict[str, str], market: str) -> list[dict]:
    out = []
    for r in rows:
        m = markets.get(r["code"])
        if market in MARKETS and m != market:
            continue
        out.append({**r, "market": m or ""})
    return out


def _slim(r: dict, **extra) -> dict:
    keep = ("code", "name", "market", "sector", "close", "change_pct", "volume_mn", "market_cap")
    return {**{k: r.get(k) for k in keep}, **extra}


def daily_movers(rows: list[dict], n: int = TOP_N) -> dict:
    ups = sorted((r for r in rows if (r["change_pct"] or 0) > 0),
                 key=lambda r: (-r["change_pct"], -(r["volume_mn"] or 0)))
    downs = sorted((r for r in rows if (r["change_pct"] or 0) < 0),
                   key=lambda r: (r["change_pct"], -(r["volume_mn"] or 0)))
    return {"up": [_slim(r) for r in ups[:n]], "down": [_slim(r) for r in downs[:n]],
            "n_up": len(ups), "n_down": len(downs)}


def weekly_changes(conn, codes: list[str], today_iso: str, cal=None) -> tuple[dict, str | None, str | None]:
    """{코드: (D 종가, 기준일 종가, 등락률%)} · D · 기준일. 일봉이 없으면 ({}, None, None)."""
    if not codes:
        return {}, None, None
    row = conn.execute("SELECT MAX(date) FROM ohlcv WHERE date <= ?", (today_iso,)).fetchone()
    last = row[0] if row else None
    if not last:
        return {}, None, None
    if cal is None:
        t = date.fromisoformat(last)
        cal = krx_calendar.Calendar(nh.observed_counts(
            conn, (t - timedelta(days=40)).isoformat(), t.isoformat()))
    base = cal.nth_trading_day_before(date.fromisoformat(last), WEEK_TRADING_DAYS)
    if not base:
        return {}, last, None
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS _mv_codes (code TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM temp._mv_codes")
    conn.executemany("INSERT OR IGNORE INTO temp._mv_codes VALUES (?)", [(c,) for c in codes])
    got = conn.execute("""
        SELECT a.code, a.close, b.close
        FROM ohlcv a JOIN ohlcv b ON b.code = a.code AND b.date = ?
        WHERE a.date = ? AND a.code IN (SELECT code FROM temp._mv_codes)
          AND a.close > 0 AND b.close > 0
    """, (base, last)).fetchall()
    return ({c: (now, then, round((now / then - 1) * 100, 2)) for c, now, then in got},
            last, base)


def weekly_movers(conn, rows: list[dict], today_iso: str, n: int = TOP_N, cal=None) -> dict:
    ch, last, base = weekly_changes(conn, [r["code"] for r in rows], today_iso, cal)
    tagged = [(r, ch[r["code"]]) for r in rows if r["code"] in ch]
    ups = sorted((x for x in tagged if x[1][2] > 0), key=lambda x: -x[1][2])
    downs = sorted((x for x in tagged if x[1][2] < 0), key=lambda x: x[1][2])

    def one(x):
        r, (now, _then, pct) = x
        return _slim(r, close=now, change_pct=pct)
    return {"up": [one(x) for x in ups[:n]], "down": [one(x) for x in downs[:n]],
            "n_up": len(ups), "n_down": len(downs), "asof": last, "base": base,
            "scanned": len(tagged)}


MARKET_OPEN_MIN = 9 * 60


def judge_date(now, cal=None) -> str:
    """신고가 판정일 — stocks 의 종가·등락률이 가리키는 거래일.

    거래일 09:00 이후면 오늘. 주말·휴장일·개장 전이면 직전 거래일이다. 판정은 일봉을
    판정일 **전날까지** 보므로, 토요일을 판정일로 두면 금요일 봉(=stocks 종가 자신)이
    고점에 들어가 어떤 종목도 신고가가 될 수 없다(2026-10-31 운영에서 4축 모두 0종목).
    """
    cal = cal or krx_calendar.Calendar()
    d = now.date()
    if cal.is_trading_day(d) and now.hour * 60 + now.minute >= MARKET_OPEN_MIN:
        return d.isoformat()
    return cal.nth_trading_day_before(d, 1) or d.isoformat()


FP_RECENT_DAYS = 14


def data_fingerprint(conn, judge_iso: str) -> tuple:
    """판정 결과를 바꿀 수 있는 입력의 지문 — 싸게(날짜 색인 범위만) 읽는다.

    일봉: 최근 2주 봉 수·최신 날짜(ohlcv 는 WITHOUT ROWID — 재배포 뒤 비어 있던 종목을
    채우면 그 종목의 최근 봉도 같이 들어와 수가 바뀐다). 역사적 이력 표. 종목 표(가격·시총).
    """
    since = (date.fromisoformat(judge_iso) - timedelta(days=FP_RECENT_DAYS)).isoformat()
    o = tuple(conn.execute("SELECT COUNT(*), MAX(date) FROM ohlcv WHERE date >= ?",
                           (since,)).fetchone())
    try:
        a = tuple(conn.execute("SELECT COUNT(*), MAX(updated_at) FROM alltime_high").fetchone())
    except Exception:                                    # noqa: BLE001 — 표가 아직 없다
        a = (0, None)
    s = tuple(conn.execute("SELECT COUNT(*), TOTAL(close), TOTAL(market_cap), "
                           "TOTAL(change_pct) FROM stocks").fetchone())
    return (o, a, s)


def slim_result(res: dict) -> dict:
    """캐시에 둘 만큼만 — newhigh_list 가 읽는 키."""
    keep = ("error", "scanned", "universe_n", "hist_pending", "last_bar", "buckets")
    return {k: res.get(k) for k in keep}


class NewhighCache:
    """신고가 판정 결과를 판정일별로 들고 있다 — 화면이 열릴 때마다 16~18초씩 다시 계산하지
    않게(2026-10-31 운영 실측, 배포 직후엔 31초로 502).

      입력 지문이 같으면 그대로 쓴다(장 마감 뒤·주말엔 사실상 계속).
      지문이 바뀌었으면 들고 있던 값을 바로 돌려주고 뒤에서 다시 계산한다 — 단,
        마지막 계산 뒤 MIN_REFRESH_S 가 지나야(장중엔 가격이 계속 바뀐다).
      MAX_AGE_S 를 넘으면 지문이 같아도 다시 계산한다(안전망).
      그 판정일의 값이 아예 없으면 그 자리에서 계산한다(동시에 여럿이 와도 한 번만).
    """
    MIN_REFRESH_S = 120
    MAX_AGE_S = 6 * 3600

    def __init__(self, compute, clock=time.time):
        self._compute = compute            # (judge) -> 판정 결과 dict
        self._clock = clock
        self._entry: dict | None = None    # {'judge', 'fp', 'ts', 'res'}
        self._lock = threading.Lock()      # 계산은 한 번에 하나
        self._bg: threading.Thread | None = None

    def _run(self, judge: str, fp) -> dict:
        res = slim_result(self._compute(judge))
        self._entry = {"judge": judge, "fp": fp, "ts": self._clock(), "res": res}
        return res

    def refresh(self, judge: str, fp) -> dict:
        """지금 계산해 넣는다(미리 데우기·처음 요청)."""
        with self._lock:
            e = self._entry
            if e and e["judge"] == judge and e["fp"] == fp:
                return e["res"]            # 기다리는 사이 다른 쪽이 막 채웠다
            return self._run(judge, fp)

    def _refresh_bg(self, judge: str, fp) -> bool:
        if self._bg is not None and self._bg.is_alive():
            return False
        if not self._lock.acquire(blocking=False):
            return False

        def work():
            try:
                self._run(judge, fp)
            except Exception as exc:                     # noqa: BLE001
                log.warning("[신고가 캐시] 뒤에서 다시 계산 실패: %s", exc)
            finally:
                self._lock.release()

        self._bg = threading.Thread(target=work, daemon=True, name="newhigh-cache")
        self._bg.start()
        return True

    def get(self, judge: str, fp) -> tuple[dict, str]:
        """(결과, 상태) — 상태는 hit | stale(뒤에서 다시 계산) | computed."""
        e = self._entry
        if e and e["judge"] == judge:
            age = self._clock() - e["ts"]
            changed = e["fp"] != fp
            if (changed and age >= self.MIN_REFRESH_S) or age >= self.MAX_AGE_S:
                self._refresh_bg(judge, fp)
                return e["res"], "stale"
            return e["res"], "hit"
        return self.refresh(judge, fp), "computed"


def newhigh_list(conn, today_iso: str, min_cap: float, markets: dict[str, str],
                 market: str, compute=None) -> dict:
    """신고가 4축 — 네트워크 없이(budget_s=0). 가장 센 축 하나에만, 축 안에서는 거래대금 순."""
    compute = compute or nh.compute
    res = compute(conn, today_iso, min_cap, budget_s=0)
    out = {"error": res.get("error"), "axes": [], "scanned": res.get("scanned"),
           "universe_n": res.get("universe_n"),
           "hist_pending": len(res.get("hist_pending") or []),
           "last_bar": res.get("last_bar")}
    if res.get("error"):
        return out
    for key, label, icon in nh.AXES:
        got = tag_market(res["buckets"].get(key) or [], markets, market)
        got.sort(key=lambda r: -(r.get("volume_mn") or 0))
        out["axes"].append({"key": key, "label": label, "icon": icon,
                            "items": [_slim(r, axis=key) for r in got]})
    return out
