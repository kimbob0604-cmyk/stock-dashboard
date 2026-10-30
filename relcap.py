"""
relcap.py — 상대 시총(종목 간 시가총액 비율 추이) · 주요 지수 연도별 수익률.

■ 상대 시총
  비율 = A 묶음 시총 합 ÷ B 묶음 시총 합(달러 환산). 묶음은 여러 종목의 합일 수 있다
  (예: 삼성전자 + 삼성전자우, 무라타 + 이비덴).

  현재 시총은 국내는 `stocks.market_cap`(네이버, 원), 해외는 server 가 넘겨 주는 값
  (yfinance — Render 에만 있다)이다. 과거 시총은 **현재 주식 수가 그대로였다고 보고**
  야후 일봉(분할 반영 종가)으로 거슬러 계산한다:
      시총_t = 시총_now × 종가_t ÷ 종가_now   (같은 야후 시계열 안에서 비율만 쓴다)
  자사주 소각·증자로 주식 수가 바뀐 구간은 그만큼 어긋난다 — 화면에 적는다.
  통화가 다르면 그날 환율로 달러 환산(KRW=X · JPY=X 일봉, 1달러당 현지통화).

■ 연도별 수익률 — 야후 월봉(range=max) 연말 종가끼리의 변화율. 가격 지수라 배당 제외.
  올해는 작년 말 대비 현재(YTD).
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

log = logging.getLogger(__name__)

HTTP_TIMEOUT = 15
UA = "Mozilla/5.0"
FX_SYMBOL = {"KRW": "KRW=X", "JPY": "JPY=X"}           # 1달러당 현지통화
PERIOD_RANGE = {"6M": "6mo", "1Y": "1y", "2Y": "2y"}

# 비교 쌍 — (이름, A 묶음, B 묶음). 묶음 원소 = (야후 심볼, 국내 코드 또는 None, 통화)
PAIRS: tuple = (
    ("SK하이닉스 / 삼성전자+우", [("000660.KS", "000660", "KRW")],
     [("005930.KS", "005930", "KRW"), ("005935.KS", "005935", "KRW")]),
    ("SK하이닉스 / 삼성전자", [("000660.KS", "000660", "KRW")], [("005930.KS", "005930", "KRW")]),
    ("SK하이닉스 / 마이크론", [("000660.KS", "000660", "KRW")], [("MU", None, "USD")]),
    ("삼성전기 / 무라타", [("009150.KS", "009150", "KRW")], [("6981.T", None, "JPY")]),
    ("삼성전기 / 무라타+이비덴", [("009150.KS", "009150", "KRW")],
     [("6981.T", None, "JPY"), ("4062.T", None, "JPY")]),
    ("삼성전자 / TSMC", [("005930.KS", "005930", "KRW")], [("TSM", None, "USD")]),
    ("브로드컴 / 엔비디아", [("AVGO", None, "USD")], [("NVDA", None, "USD")]),
)
INDICES = (("^GSPC", "S&P 500"), ("^IXIC", "NASDAQ"), ("^KS11", "KOSPI"), ("^KQ11", "KOSDAQ"))


def foreign_symbols() -> list[str]:
    """국내 코드가 없는(= server 가 yfinance 로 현재 시총을 넘겨야 하는) 심볼."""
    return sorted({s for _, a, b in PAIRS for s, code, _ in a + b if code is None})


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:   # noqa: S310
        return json.loads(r.read())


def parse_chart(body: dict) -> list[tuple[str, float]]:
    """야후 v8 chart → [(YYYY-MM-DD, 종가)] 날짜 순. 종가가 비는 날은 버린다."""
    r = body["chart"]["result"][0]
    ts = r.get("timestamp") or []
    closes = (r.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    gmt = (r.get("meta") or {}).get("gmtoffset") or 0
    out = {}
    for t, c in zip(ts, closes):
        if c is None or c <= 0:
            continue
        d = datetime.fromtimestamp(t + gmt, tz=timezone.utc).strftime("%Y-%m-%d")
        out[d] = float(c)
    return sorted(out.items())


def fetch_chart(symbol: str, rng: str, interval: str = "1d", get=_get) -> list[tuple[str, float]]:
    """rng='max' 는 기간을 직접 준다 — range=max 로 부르면 야후가 월봉을 석 달 단위로 묶어
    '12월' 봉의 종가가 2월 말 값이 된다(2026-10-30 확인: S&P 2022 가 −9% 로 나왔다)."""
    q = urllib.parse.quote(symbol)
    span = "period1=0&period2=9999999999" if rng == "max" else f"range={rng}"
    return parse_chart(get(f"https://query1.finance.yahoo.com/v8/finance/chart/{q}"
                           f"?{span}&interval={interval}"))


def _asof(series: list[tuple[str, float]], d: str):
    """d 이하 마지막 값(휴장일 맞춤). 없으면 None. series 는 날짜 순."""
    lo, hi, best = 0, len(series) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if series[mid][0] <= d:
            best = series[mid][1]
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def pair_series(a, b, hist: dict, caps_now: dict, fx: dict) -> tuple[list[dict], str | None]:
    """한 쌍의 [{date, value(%)}] 와 못 만든 이유. hist={심볼: 일봉}, caps_now={심볼: 현지통화 시총},
    fx={통화: 일봉(1달러당)}. 날짜는 A·B 첫 심볼의 거래일 합집합."""
    legs = a + b
    for s, _, cur in legs:
        if not hist.get(s):
            return [], f"{s} 일봉 없음"
        if not caps_now.get(s):
            return [], f"{s} 현재 시총 없음"
        if cur != "USD" and not fx.get(cur):
            return [], f"{cur} 환율 없음"
    dates = sorted({d for s, _, _ in legs for d, _ in hist[s]})

    def leg_usd(leg, d):
        total = 0.0
        for s, _, cur in leg:
            p_t, p_now = _asof(hist[s], d), hist[s][-1][1]
            if p_t is None:
                return None
            local = caps_now[s] * p_t / p_now
            rate = 1.0 if cur == "USD" else _asof(fx[cur], d)
            if not rate:
                return None
            total += local / rate
        return total

    out = []
    for d in dates:
        va, vb = leg_usd(a, d), leg_usd(b, d)
        if va and vb:
            out.append({"date": d, "value": round(va / vb * 100, 2)})
    return out, None


def relative_caps(caps_now: dict, period: str = "1Y", fetch=None) -> dict:
    """화면 한 벌. caps_now = {야후 심볼: 현지통화 시총} (국내·해외 모두 server 가 채운다).
    fetch 를 안 주면 **부르는 시점의** fetch_chart 를 쓴다(기본값에 묶지 않는다 — 검사가 바꿔 끼운다)."""
    fetch = fetch or fetch_chart
    rng = PERIOD_RANGE.get(period, PERIOD_RANGE["1Y"])
    symbols = sorted({s for _, a, b in PAIRS for s, _, _ in a + b} | set(FX_SYMBOL.values()))
    hist: dict = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {s: ex.submit(fetch, s, rng) for s in symbols}
        for s, f in futs.items():
            try:
                hist[s] = f.result()
            except Exception as exc:                       # noqa: BLE001
                log.warning("[상대 시총] %s 일봉 실패: %s", s, exc)
                hist[s] = []
    fx = {cur: hist.get(sym) or [] for cur, sym in FX_SYMBOL.items()}
    pairs = []
    for name, a, b in PAIRS:
        series, err = pair_series(a, b, hist, caps_now, fx)
        cur = series[-1]["value"] if series else None
        vals = [p["value"] for p in series]
        pairs.append({"name": name, "series": series, "error": err, "latest": cur,
                      "date": series[-1]["date"] if series else None,
                      "high": max(vals) if vals else None, "low": min(vals) if vals else None,
                      "legs": [[s for s, _, _ in a], [s for s, _, _ in b]]})
    return {"period": period if period in PERIOD_RANGE else "1Y", "pairs": pairs}


def annual_returns(fetch=None) -> dict:
    """{years: [...최근 순], rows: [{name, returns: {연도: %}}]}. 연말 종가 기준, 올해는 YTD."""
    fetch = fetch or fetch_chart
    rows, years = [], set()
    for sym, name in INDICES:
        try:
            m = fetch(sym, "max", "1mo")
        except Exception as exc:                           # noqa: BLE001
            rows.append({"symbol": sym, "name": name, "returns": {}, "error": str(exc)[:120]})
            continue
        last_by_year: dict[int, float] = {}
        for d, c in m:
            last_by_year[int(d[:4])] = c                   # 날짜 순이라 마지막 값이 연말
        rets = {}
        for y in sorted(last_by_year):
            if y - 1 in last_by_year:
                rets[y] = round((last_by_year[y] / last_by_year[y - 1] - 1) * 100, 2)
        years |= set(rets)
        rows.append({"symbol": sym, "name": name, "returns": rets,
                     "asof": m[-1][0] if m else None})
    return {"years": sorted(years, reverse=True), "rows": rows}
