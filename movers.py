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
