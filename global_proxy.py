"""
global_proxy.py — 🌐 글로벌 프록시: 한국 장이 닫힌 동안 한국 주식을 비춰 보는 해외 가격들.

  24시간 선물   Hyperliquid(xyz dex) 무기한 선물 — 삼성전자·SK하이닉스·SK하이닉스 ADR·현대차·
                코스피200. 가격은 달러(코스피200 은 지수 포인트). 직전 국내 정규장 종가와
                견준다(환율은 같은 순간의 USD/KRW). 펀딩률은 1시간 값 × 8 = 8시간 기준.
  해외 상장     SK하이닉스 나스닥 ADR(SKHY, 1 ADS = 보통주 1/10주) · 삼성전자 런던 GDR(SMSN,
                1 GDR = 보통주 25주) · SK하이닉스 프랑크푸르트(HY9H, 1주, 유로). 국내 종가를
                환산한 값 대비 괴리율.
  지수선물·매크로  나스닥100·S&P500 선물, 미국 10년물, WTI, USD/KRW.
  미국 ETF      DRAM(메모리) · EWY(한국) · SOXX(반도체).

야후 차트 API(range=1d → chartPreviousClose 가 직전 세션 종가)와 Hyperliquid info API 를
쓴다. 둘 다 키가 필요 없다. 하나가 실패해도 나머지는 보여 주고, 실패한 칸은 error 로 남긴다.
환산 비율은 발행사 공시로 확인한 값이다(2026-10-30) — 바뀌면 여기 한 곳만 고친다.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger(__name__)

HTTP_TIMEOUT = 12
UA = "Mozilla/5.0"
HL_URL = "https://api.hyperliquid.xyz/info"
HL_DEX = "xyz"

# (Hyperliquid 이름, 화면 이름, 국내 비교 대상) — 비교 대상: ('stock', 코드) 원화 종가 /
# ('index', 'KPI200') 지수 / ('yahoo', 심볼) 달러 종가
PERPS = (
    ("xyz:SMSN", "삼성전자", ("stock", "005930")),
    ("xyz:SKHX", "SK하이닉스", ("stock", "000660")),
    ("xyz:SKHY", "SK하이닉스 ADR", ("yahoo", "SKHY")),
    ("xyz:HYUNDAI", "현대차", ("stock", "005380")),
    ("xyz:KR200", "코스피200", ("index", "KPI200")),
)
# (야후 심볼, 화면 이름, 국내 코드, 1 증서 = 보통주 몇 주, 통화)
LISTINGS = (
    ("SKHY", "SK하이닉스 나스닥 ADR", "000660", 0.1, "USD"),
    ("SMSN.IL", "삼성전자 런던 GDR", "005930", 25.0, "USD"),
    ("HY9H.F", "SK하이닉스 프랑크푸르트", "000660", 1.0, "EUR"),
)
FUTURES = (("NQ=F", "나스닥100 선물", ""), ("ES=F", "S&P500 선물", ""))
MACRO = (("^TNX", "미국 10년물", "%"), ("CL=F", "WTI 원유", "$"), ("KRW=X", "USD/KRW", "원"))
US_ETFS = (("DRAM", "DRAM · 메모리", "$"), ("EWY", "EWY · 한국", "$"), ("SOXX", "SOXX · 반도체", "$"))
FX = ("KRW=X", "EURUSD=X")


def _get_json(url: str, body: dict | None = None) -> dict | list:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": UA, **({"Content-Type": "application/json"} if data else {})})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:   # noqa: S310
        return json.loads(r.read())


def parse_yahoo(body: dict) -> dict:
    """야후 v8 chart(range=1d) → {price, prev, time, currency, name}. 값이 없으면 ValueError."""
    m = body["chart"]["result"][0]["meta"]
    price, prev = m.get("regularMarketPrice"), m.get("chartPreviousClose")
    if not price:
        raise ValueError("가격 없음")
    return {"price": float(price), "prev": float(prev) if prev else None,
            "time": m.get("regularMarketTime"), "currency": m.get("currency"),
            "name": m.get("longName") or m.get("shortName")}


def fetch_yahoo(symbol: str, get=_get_json) -> dict:
    return parse_yahoo(get(f"https://query1.finance.yahoo.com/v8/finance/chart/"
                           f"{urllib.request.quote(symbol)}?range=1d&interval=1d"))


def parse_hyperliquid(body: list, names) -> dict:
    """metaAndAssetCtxs → {이름: {mark, prev_day, funding_8h_pct, oi, vol_24h}}."""
    meta, ctxs = body
    out = {}
    for a, c in zip(meta.get("universe") or [], ctxs or []):
        n = a.get("name")
        if n not in names:
            continue
        try:
            mark = float(c.get("markPx") or 0)
        except (TypeError, ValueError):
            continue
        if mark <= 0:
            continue
        f = c.get("funding")
        out[n] = {"mark": mark,
                  "prev_day": float(c["prevDayPx"]) if c.get("prevDayPx") else None,
                  "funding_8h_pct": round(float(f) * 8 * 100, 4) if f not in (None, "") else None,
                  "oi": float(c.get("openInterest") or 0),
                  "vol_24h": float(c.get("dayNtlVlm") or 0)}
    return out


def fetch_hyperliquid(get=_get_json) -> dict:
    return parse_hyperliquid(get(HL_URL, {"type": "metaAndAssetCtxs", "dex": HL_DEX}),
                             {p[0] for p in PERPS})


def _pct(a, b):
    return round((a / b - 1) * 100, 2) if a and b else None


def build(domestic: dict, index_close: dict, *, yahoo=fetch_yahoo, hl=fetch_hyperliquid) -> dict:
    """화면 한 벌. `domestic` = {코드: 원화 종가}(stocks), `index_close` = {'KPI200': 종가}.

    네트워크 호출은 동시에 하고(야후 심볼마다 하나 + Hyperliquid 하나), 실패는 칸마다 남긴다.
    """
    symbols = sorted({s for s, *_ in LISTINGS + FUTURES + MACRO + US_ETFS} | set(FX)
                     | {t[2][1] for t in PERPS if t[2][0] == "yahoo"})
    res: dict = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {s: ex.submit(yahoo, s) for s in symbols}
        hl_fut = ex.submit(hl)
        for s, f in futs.items():
            try:
                res[s] = f.result()
            except Exception as exc:                       # noqa: BLE001
                res[s] = {"error": str(exc)[:120]}
        try:
            perps = hl_fut.result()
            hl_err = None
        except Exception as exc:                           # noqa: BLE001
            perps, hl_err = {}, str(exc)[:120]
    usdkrw = (res.get("KRW=X") or {}).get("price")
    eurusd = (res.get("EURUSD=X") or {}).get("price")

    def quote(sym, name, unit=""):
        r = res.get(sym) or {}
        if "error" in r or "price" not in r:
            return {"symbol": sym, "name": name, "error": r.get("error", "값 없음")}
        return {"symbol": sym, "name": name, "price": r["price"], "unit": unit,
                "change_pct": _pct(r["price"], r.get("prev")), "time": r.get("time")}

    perp_rows = []
    for hn, name, (kind, ref) in PERPS:
        p = perps.get(hn)
        if not p:
            perp_rows.append({"symbol": hn, "name": name, "error": hl_err or "목록에 없음"})
            continue
        base, base_label = None, ""
        if kind == "stock" and domestic.get(ref) and usdkrw:
            base, base_label = domestic[ref] / usdkrw, "국내 종가(달러 환산)"
        elif kind == "index" and index_close.get(ref):
            base, base_label = index_close[ref], "코스피200 종가"
        elif kind == "yahoo" and (res.get(ref) or {}).get("price"):
            base, base_label = res[ref]["price"], "나스닥 종가"
        perp_rows.append({"symbol": hn, "name": name, "price": p["mark"],
                          "unit": "pt" if kind == "index" else "$",
                          "vs_close_pct": _pct(p["mark"], base), "vs_label": base_label,
                          "change_24h_pct": _pct(p["mark"], p["prev_day"]),
                          "funding_8h_pct": p["funding_8h_pct"], "vol_24h": p["vol_24h"]})

    list_rows = []
    for sym, name, code, shares, cur in LISTINGS:
        q = quote(sym, name, "€" if cur == "EUR" else "$")
        if "error" not in q:
            # 1 단위 통화가 몇 원인가 — 1유로 = EURUSD 달러 = EURUSD × USDKRW 원
            fx = usdkrw if cur == "USD" else (usdkrw * eurusd if usdkrw and eurusd else None)
            krw = domestic.get(code)
            fair = krw * shares / fx if krw and fx else None
            q.update({"fair": round(fair, 2) if fair else None, "premium_pct": _pct(q["price"], fair),
                      "ratio": shares, "code": code})
        list_rows.append(q)

    return {
        "perps": perp_rows,
        "listings": list_rows,
        "futures": [quote(s, n, u) for s, n, u in FUTURES],
        "macro": [quote(s, n, u) for s, n, u in MACRO],
        "us_etfs": [quote(s, n, u) for s, n, u in US_ETFS],
        "usdkrw": usdkrw,
    }
