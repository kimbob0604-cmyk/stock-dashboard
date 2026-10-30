"""
market_risk.py — 시장 체력(ADR 등락비율)과 위험지표(이격도 · VIX · 장단기 금리차).

server.py 는 Flask 앱이라 검사에서 import 하기 무겁다. 계산과 저장은 이 모듈이
하고, server.py 는 API · 스케줄 · 화면 배선만 한다(newhigh.py 와 같은 방식).

■ ADR(Advance-Decline Ratio, 등락비율)

  ADR = 최근 20거래일 상승 종목 수 합 ÷ 하락 종목 수 합 × 100.
  120% 이상이면 과열, 80% 이하면 바닥권으로 흔히 읽는다(판단 근거가 아니라 참고선).

  종목 범위는 종목 마스터(data/master/stocks.csv — 코스피·코스닥 보통주·우선주·
  리츠·인프라, ETF·ETN·스팩 없음)다. `stocks` 표의 KR 행은 market 칸이 비어
  있어(''), 시장 구분을 마스터에서 가져온다.

  하루치 집계는 `market_breadth` 표에 쌓는다. 출처가 둘이다.
    snapshot  장 마감 뒤 `stocks` 의 등락률로 센 **전 종목** 집계 (정식)
    ohlcv     받아 둔 일봉(시총 800억 이상 ~1,500종목)으로 센 **근사** — 표가
              비었을 때 과거를 채우고, 스냅샷을 못 찍은 날의 구멍을 메운다.
              스냅샷을 덮지 않는다(INSERT OR IGNORE).
  근사 구간은 화면에 날짜로 밝힌다(`approx_until`). 섞어 놓고 한 정의인 척하지 않는다.

■ 이격도 = 지수 종가 ÷ N일 이동평균 × 100 (N = 25 · 50). 네이버 지수 일봉.
■ VIX(VIXCLS) · 미국 10년−2년 금리차(T10Y2Y) — FRED 공개 CSV(키 불필요, 1영업일 지연).
  둘 다 `risk_series` 표에 쌓는다(재배포로 비면 다시 받는다 — 백업 대상 아님).
"""
from __future__ import annotations

import csv
import io
import json
import logging
import urllib.request
from datetime import date, datetime, timedelta

from stock_master import KR_CODE_GLOB

log = logging.getLogger(__name__)

# ─────────────────────────── 설정 (바꾸려면 여기만) ───────────────────────────
ADR_WINDOW = 20
ADR_HOT, ADR_COLD = 120.0, 80.0
DISPARITY_MAS = (25, 50)
MARKETS = ("KOSPI", "KOSDAQ")
PERIOD_DAYS = {"6M": 183, "1Y": 365, "3Y": 1095, "5Y": 1826}
DEFAULT_PERIOD = "1Y"
# 근사 집계 하루를 받아들이는 최소 종목 비율 — 일봉을 채우다 만 날(몇십 종목)이
# ADR 을 흔들지 않게. 그 시장의 가장 많이 센 날 대비.
BACKFILL_MIN_COVERAGE = 0.5
# 스냅샷을 찍을 때 `stocks` 가 이만큼 안에 갱신돼 있어야 한다(오늘 등락률이어야).
SNAPSHOT_MAX_AGE_H = 4
# 스냅샷 하루의 최소 종목 수 — 가격 갱신이 반쯤 실패한 날을 정식으로 남기지 않는다.
SNAPSHOT_MIN_TOTAL = {"KOSPI": 500, "KOSDAQ": 1000}

NAVER_INDEX = {"KOSPI": "KOSPI", "KOSDAQ": "KOSDAQ",
               "KOSPI200": "KPI200"}         # 코스피200 — 글로벌 프록시가 종가 비교에 쓴다
NAVER_PAGE_SIZE = 60                      # 네이버 지수 일봉 API 의 최대(100 은 오류)
NAVER_FULL_PAGES = 15                     # 첫 수집 — 60×15 = 900거래일 ≈ 3.6년
FRED_SERIES = {"VIX": "VIXCLS", "T10Y2Y": "T10Y2Y"}
FRED_FULL_YEARS = 5
HTTP_TIMEOUT = 20
UA = "Mozilla/5.0 (stock-dashboard market_risk)"   # 네이버용

BREADTH_DDL = """
CREATE TABLE IF NOT EXISTS market_breadth (
    date       TEXT NOT NULL,          -- YYYY-MM-DD
    market     TEXT NOT NULL,          -- KOSPI | KOSDAQ
    up         INTEGER NOT NULL,
    down       INTEGER NOT NULL,
    flat       INTEGER NOT NULL,
    total      INTEGER NOT NULL,
    source     TEXT NOT NULL,          -- snapshot(전 종목) | ohlcv(근사)
    updated_at TEXT,
    PRIMARY KEY (date, market)
)"""

SERIES_DDL = """
CREATE TABLE IF NOT EXISTS risk_series (
    series TEXT NOT NULL,              -- idx:KOSPI · idx:KOSDAQ · VIX · T10Y2Y
    date   TEXT NOT NULL,
    value  REAL NOT NULL,
    PRIMARY KEY (series, date)
)"""


def ensure_tables(conn) -> None:
    conn.execute(BREADTH_DDL)
    conn.execute(SERIES_DDL)


def master_markets(load=None) -> dict[str, str]:
    """종목코드 → 'KOSPI' | 'KOSDAQ'. 마스터를 못 읽으면 빈 dict(지어내지 않는다)."""
    if load is None:
        import stock_master
        load = stock_master.load_master
    try:
        rows = load() or {}
    except Exception as exc:                               # noqa: BLE001
        log.warning("[위험지표] 종목 마스터를 못 읽었다: %s", exc)
        return {}
    return {c: r.get("market") for c, r in rows.items() if r.get("market") in MARKETS}


# ─────────────────────────────── ADR ───────────────────────────────

def count_today(conn, markets: dict[str, str]) -> dict[str, dict]:
    """`stocks` 의 지금 등락률로 시장별 상승·하락·보합 수."""
    out = {m: {"up": 0, "down": 0, "flat": 0, "total": 0} for m in MARKETS}
    rows = conn.execute(
        "SELECT code, change_pct FROM stocks "
        f"WHERE code GLOB '{KR_CODE_GLOB}' AND change_pct IS NOT NULL").fetchall()
    for code, chg in rows:
        m = markets.get(code)
        if m is None:
            continue
        c = out[m]
        c["total"] += 1
        if chg > 0:
            c["up"] += 1
        elif chg < 0:
            c["down"] += 1
        else:
            c["flat"] += 1
    return out


def stocks_fresh(conn, now_utc: datetime, max_age_h: float = SNAPSHOT_MAX_AGE_H) -> bool:
    """`stocks` KR 행이 최근 `max_age_h` 시간 안에 갱신됐는가(updated_at 은 UTC)."""
    row = conn.execute(
        f"SELECT MAX(updated_at) FROM stocks WHERE code GLOB '{KR_CODE_GLOB}'").fetchone()
    if not row or not row[0]:
        return False
    try:
        last = datetime.strptime(str(row[0])[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False
    return now_utc.replace(tzinfo=None) - last <= timedelta(hours=max_age_h)


def snapshot_ok(counts: dict[str, dict]) -> tuple[bool, str]:
    """스냅샷으로 남겨도 될 만큼 셌는가."""
    for m in MARKETS:
        n = (counts.get(m) or {}).get("total", 0)
        if n < SNAPSHOT_MIN_TOTAL[m]:
            return False, f"{m} {n}종목뿐 — 가격 갱신이 덜 됐다"
    return True, ""


def record_snapshot(conn, day: str, counts: dict[str, dict], now_iso: str) -> int:
    """오늘 전 종목 집계를 정식으로 남긴다(근사 행이 있으면 덮는다)."""
    n = 0
    for m in MARKETS:
        c = counts[m]
        conn.execute(
            "INSERT OR REPLACE INTO market_breadth "
            "(date, market, up, down, flat, total, source, updated_at) "
            "VALUES (?,?,?,?,?,?, 'snapshot', ?)",
            (day, m, c["up"], c["down"], c["flat"], c["total"], now_iso))
        n += 1
    return n


def backfill_from_ohlcv(conn, markets: dict[str, str], now_iso: str,
                        min_coverage: float = BACKFILL_MIN_COVERAGE) -> int:
    """받아 둔 일봉으로 날마다 상승·하락 수를 세어 **빈 날만** 채운다(근사).

    전일 종가가 있어야 그날 등락을 셀 수 있다 — 종목마다 첫 봉은 세지 않는다.
    그 시장의 가장 많이 센 날의 `min_coverage` 에 못 미치는 날은 버린다.
    """
    if not markets:
        return 0
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS _mr_mkt "
                 "(code TEXT PRIMARY KEY, market TEXT NOT NULL)")
    conn.execute("DELETE FROM _mr_mkt")
    conn.executemany("INSERT INTO _mr_mkt VALUES (?,?)", list(markets.items()))
    rows = conn.execute("""
        WITH x AS (
            SELECT o.code, o.date, o.close,
                   LAG(o.close) OVER (PARTITION BY o.code ORDER BY o.date) AS prev
            FROM ohlcv o JOIN _mr_mkt k ON k.code = o.code
            WHERE o.close > 0
        )
        SELECT x.date, k.market,
               SUM(CASE WHEN x.close > x.prev THEN 1 ELSE 0 END),
               SUM(CASE WHEN x.close < x.prev THEN 1 ELSE 0 END),
               SUM(CASE WHEN x.close = x.prev THEN 1 ELSE 0 END),
               COUNT(*)
        FROM x JOIN _mr_mkt k ON k.code = x.code
        WHERE x.prev IS NOT NULL AND x.prev > 0
        GROUP BY x.date, k.market
    """).fetchall()
    peak: dict[str, int] = {}
    for _, m, _, _, _, total in rows:
        peak[m] = max(peak.get(m, 0), total)
    n = 0
    for d, m, up, down, flat, total in rows:
        if total < min_coverage * peak.get(m, 0):
            continue
        cur = conn.execute(
            "INSERT OR IGNORE INTO market_breadth "
            "(date, market, up, down, flat, total, source, updated_at) "
            "VALUES (?,?,?,?,?,?, 'ohlcv', ?)", (d, m, up, down, flat, total, now_iso))
        n += cur.rowcount or 0
    return n


def adr_series(rows, window: int = ADR_WINDOW) -> list[dict]:
    """[(date, up, down), ...] 날짜 순 → 창이 다 찬 날부터 [{date, adr, up, down}]."""
    out = []
    ups: list[int] = []
    downs: list[int] = []
    for d, up, down in rows:
        ups.append(up)
        downs.append(down)
        if len(ups) > window:
            ups.pop(0)
            downs.pop(0)
        if len(ups) < window:
            continue
        su, sd = sum(ups), sum(downs)
        out.append({"date": d, "adr": round(su / sd * 100, 2) if sd else None,
                    "up": su, "down": sd})
    return out


def _cut(series: list[dict], today_iso: str, period: str) -> list[dict]:
    days = PERIOD_DAYS.get(period, PERIOD_DAYS[DEFAULT_PERIOD])
    start = (date.fromisoformat(today_iso) - timedelta(days=days)).isoformat()
    return [p for p in series if p["date"] >= start]


def breadth_payload(conn, today_iso: str, live: dict[str, dict] | None,
                    period: str = DEFAULT_PERIOD, live_label: str = "") -> dict:
    """ADR 화면 한 벌. `live` 는 오늘 지금 센 값(장중이면 실시간, 없으면 None).

    오늘 행이 아직 표에 없으면 live 를 오늘 행으로 붙여 ADR 을 계산한다 — 장중
    ADR 이 어제 값에 머물지 않게. 표에 남기는 것은 장 마감 스냅샷뿐이다.
    """
    out = {"window": ADR_WINDOW, "hot": ADR_HOT, "cold": ADR_COLD,
           "period": period if period in PERIOD_DAYS else DEFAULT_PERIOD,
           "markets": {}}
    for m in MARKETS:
        rows = conn.execute(
            "SELECT date, up, down, flat, total, source FROM market_breadth "
            "WHERE market = ? ORDER BY date", (m,)).fetchall()
        rows = [tuple(r) for r in rows]
        today = (live or {}).get(m)
        used_live = False
        if today and today.get("total") and (not rows or rows[-1][0] < today_iso):
            rows.append((today_iso, today["up"], today["down"], today["flat"],
                         today["total"], "live"))
            used_live = True
        series = adr_series([(d, u, dn) for d, u, dn, *_ in rows])
        approx = [d for d, *_, src in rows if src == "ohlcv"]
        last = rows[-1] if rows else None
        card = None
        if last:
            card = {"date": last[0], "up": last[1], "down": last[2], "flat": last[3],
                    "total": last[4], "source": last[5],
                    "label": live_label if used_live else ("장 마감" if last[5] == "snapshot"
                                                           else "일봉 근사")}
        latest = series[-1] if series else None
        prev = series[-2] if len(series) > 1 else None
        out["markets"][m] = {
            "today": card,
            "adr": latest["adr"] if latest else None,
            "adr_change": (round(latest["adr"] - prev["adr"], 2)
                           if latest and prev and latest["adr"] is not None
                           and prev["adr"] is not None else None),
            "series": [{"date": p["date"], "value": p["adr"]}
                       for p in _cut(series, today_iso, out["period"])],
            "approx_until": max(approx) if approx else None,
            "days": len(rows),
        }
    return out


# ─────────────────────────────── 이격도 · FRED ───────────────────────────────

def disparity(closes: list[tuple[str, float]], n: int) -> list[dict]:
    """[(date, close)] 날짜 순 → 이격도 = 종가 ÷ 최근 n일(그날 포함) 평균 × 100."""
    out = []
    win: list[float] = []
    total = 0.0
    for d, c in closes:
        win.append(c)
        total += c
        if len(win) > n:
            total -= win.pop(0)
        if len(win) == n and total > 0:
            out.append({"date": d, "value": round(c / (total / n) * 100, 2)})
    return out


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:   # noqa: S310
        return r.read()


def _http_get_plain(url: str) -> bytes:
    """User-Agent 를 따로 달지 않는다(urllib 기본값). FRED 는 브라우저 흉내·임의 UA 를
    응답 없이 붙잡아 두고(타임아웃), 기본 UA 에만 바로 답한다(2026-10-30 확인)."""
    with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as r:   # noqa: S310
        return r.read()


def parse_naver_index(body: bytes | str) -> list[tuple[str, float]]:
    """네이버 지수 일봉 JSON → [(YYYY-MM-DD, 종가)]. 깨진 행은 버린다."""
    data = json.loads(body)
    out = []
    for it in data if isinstance(data, list) else []:
        d = str(it.get("localTradedAt") or "")[:10]
        try:
            v = float(str(it.get("closePrice") or "").replace(",", ""))
        except ValueError:
            continue
        if len(d) == 10 and v > 0:
            out.append((d, v))
    return out


def fetch_naver_index(code: str, pages: int, get=_http_get) -> list[tuple[str, float]]:
    out: dict[str, float] = {}
    for p in range(1, pages + 1):
        url = (f"https://m.stock.naver.com/api/index/{code}/price"
               f"?pageSize={NAVER_PAGE_SIZE}&page={p}")
        rows = parse_naver_index(get(url))
        if not rows:
            break
        out.update(rows)
        if len(rows) < NAVER_PAGE_SIZE:
            break
    return sorted(out.items())


def parse_fred_csv(body: bytes | str) -> list[tuple[str, float]]:
    """FRED fredgraph.csv → [(date, value)]. 결측('.')·빈칸은 버린다."""
    text = body.decode("utf-8") if isinstance(body, bytes) else body
    out = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2 or not row[0][:4].isdigit():
            continue
        try:
            out.append((row[0][:10], float(row[1])))
        except ValueError:
            continue
    return out


def fetch_fred(series_id: str, start: str, get=_http_get_plain) -> list[tuple[str, float]]:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start}"
    return parse_fred_csv(get(url))


def _upsert_series(conn, name: str, rows) -> int:
    """쌓고 곧바로 커밋한다 — 다음 시리즈를 받는(네트워크) 동안 쓰기 잠금을 쥐지 않게."""
    conn.executemany("INSERT OR REPLACE INTO risk_series (series, date, value) VALUES (?,?,?)",
                     [(name, d, v) for d, v in rows])
    conn.commit()
    return len(rows)


def _last_date(conn, name: str) -> str | None:
    row = conn.execute("SELECT MAX(date) FROM risk_series WHERE series = ?", (name,)).fetchone()
    return row[0] if row else None


def refresh_series(conn, today_iso: str, *, fetch_index=fetch_naver_index,
                   fetch_fred_fn=fetch_fred) -> dict:
    """지수 일봉·VIX·금리차를 받아 쌓는다. 이미 있으면 최근 것만. 하나가 실패해도 나머지는 한다."""
    res: dict[str, object] = {}
    for m, code in NAVER_INDEX.items():
        name = f"idx:{m}"
        try:
            pages = 1 if _last_date(conn, name) else NAVER_FULL_PAGES
            res[name] = _upsert_series(conn, name, fetch_index(code, pages))
        except Exception as exc:                           # noqa: BLE001
            res[name] = f"실패: {exc}"
    for name, sid in FRED_SERIES.items():
        try:
            last = _last_date(conn, name)
            start = ((date.fromisoformat(last) - timedelta(days=30)).isoformat() if last else
                     (date.fromisoformat(today_iso)
                      - timedelta(days=365 * FRED_FULL_YEARS)).isoformat())
            res[name] = _upsert_series(conn, name, fetch_fred_fn(sid, start))
        except Exception as exc:                           # noqa: BLE001
            res[name] = f"실패: {exc}"
    return res


def _series(conn, name: str) -> list[tuple[str, float]]:
    return [tuple(r) for r in conn.execute(
        "SELECT date, value FROM risk_series WHERE series = ? ORDER BY date", (name,))]


def _summary(points: list[dict]) -> dict:
    last = points[-1] if points else None
    prev = points[-2] if len(points) > 1 else None
    return {"latest": last["value"] if last else None,
            "date": last["date"] if last else None,
            "change": (round(last["value"] - prev["value"], 2) if last and prev else None)}


def risk_payload(conn, today_iso: str, period: str = DEFAULT_PERIOD,
                 ma: int = DISPARITY_MAS[1]) -> dict:
    """위험지표 화면 한 벌 — 이격도(코스피·코스닥) · VIX · 장단기 금리차."""
    period = period if period in PERIOD_DAYS else DEFAULT_PERIOD
    ma = ma if ma in DISPARITY_MAS else DISPARITY_MAS[1]
    out: dict = {"period": period, "ma": ma, "mas": list(DISPARITY_MAS), "disparity": {}}
    for m in MARKETS:
        pts = disparity(_series(conn, f"idx:{m}"), ma)
        out["disparity"][m] = {**_summary(pts), "series": _cut(pts, today_iso, period)}
    for name in FRED_SERIES:
        pts = [{"date": d, "value": v} for d, v in _series(conn, name)]
        out[name.lower()] = {**_summary(pts), "series": _cut(pts, today_iso, period)}
    return out
