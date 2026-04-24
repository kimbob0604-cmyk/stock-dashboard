"""
kis_api.py — 한국투자증권 REST API 클라이언트 (데이터 조회 전용)

매매 기능 없음. 토큰·메모리·파일 3중 캐시로 호출 최소화.
초당 20회 제한 → 18회 임계값에서 자동 sleep.
"""
from __future__ import annotations
import os
import json
import time
import threading
import logging
from pathlib import Path
from datetime import datetime

import requests

log = logging.getLogger(__name__)

KIS_BASE = "https://openapi.koreainvestment.com:9443"
BASE_DIR = Path(__file__).parent
CACHE_DIR = BASE_DIR / "cache"

# ── 토큰 (24시간 유효, 발급 빈도 제한 1분/회) ─────────────
_token_lock = threading.Lock()
_token_cache: dict = {"token": None, "expires": 0}
_TOKEN_FILE = CACHE_DIR / "kis_token.json"


def _load_token_from_disk():
    if not _TOKEN_FILE.exists():
        return
    try:
        d = json.loads(_TOKEN_FILE.read_text(encoding="utf-8"))
        if d.get("token") and time.time() < d.get("expires", 0) - 300:
            _token_cache["token"] = d["token"]
            _token_cache["expires"] = d["expires"]
    except Exception:
        pass


def _save_token_to_disk():
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        _TOKEN_FILE.write_text(json.dumps(_token_cache), encoding="utf-8")
    except Exception:
        pass


def _get_token() -> str | None:
    with _token_lock:
        if _token_cache["token"] and time.time() < _token_cache["expires"] - 300:
            return _token_cache["token"]
        if _token_cache["token"] is None:
            _load_token_from_disk()
            if _token_cache["token"] and time.time() < _token_cache["expires"] - 300:
                return _token_cache["token"]
        key = os.getenv("KIS_APP_KEY")
        secret = os.getenv("KIS_APP_SECRET")
        if not key or not secret:
            log.warning("[KIS] APP_KEY/SECRET 미설정")
            return None
        try:
            r = requests.post(f"{KIS_BASE}/oauth2/tokenP", json={
                "grant_type": "client_credentials",
                "appkey": key, "appsecret": secret,
            }, timeout=10)
            d = r.json()
            tok = d.get("access_token")
            if tok:
                _token_cache["token"] = tok
                _token_cache["expires"] = time.time() + int(d.get("expires_in", 86400))
                _save_token_to_disk()
                log.info("[KIS] 토큰 발급 (만료까지 %d초)", d.get("expires_in", 0))
                return tok
            log.warning("[KIS] 토큰 응답 이상: %s", d)
        except Exception as exc:
            log.warning("[KIS] 토큰 발급 실패: %s", exc)
        return None


def _headers(tr_id: str) -> dict | None:
    tok = _get_token()
    if not tok:
        return None
    return {
        "Content-Type": "application/json; charset=utf-8",
        "authorization": f"Bearer {tok}",
        "appkey": os.getenv("KIS_APP_KEY", ""),
        "appsecret": os.getenv("KIS_APP_SECRET", ""),
        "tr_id": tr_id,
    }


# ── 레이트 리밋 (초당 20회 제한, 18회 임계값) ─────────────
_rate_lock = threading.Lock()
_rate_calls: list = []


def _rate_limit():
    with _rate_lock:
        now = time.time()
        _rate_calls[:] = [t for t in _rate_calls if now - t < 1.0]
        if len(_rate_calls) >= 18:
            sleep_t = 1.0 - (now - _rate_calls[0]) + 0.05
            if sleep_t > 0:
                time.sleep(sleep_t)
        _rate_calls.append(time.time())


# ── 메모리 + 파일 캐시 ─────────────────────────────
_mem_cache: dict = {}


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"kis_{key}.json"


def _get_cache(key: str, ttl: int):
    e = _mem_cache.get(key)
    now = time.time()
    if e and now - e["ts"] < ttl:
        return e["data"]
    p = _cache_path(key)
    if p.exists():
        mt = p.stat().st_mtime
        if now - mt < ttl:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                _mem_cache[key] = {"data": d, "ts": mt}
                return d
            except Exception:
                pass
    return None


def _set_cache(key: str, data):
    _mem_cache[key] = {"data": data, "ts": time.time()}
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        _cache_path(key).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _is_kr_market_hours() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 100 + now.minute
    return 900 <= t <= 1530


# ── 분봉 (장중 60s, 장외 1h) ─────────────────────
def get_minute_chart(code: str, interval: int = 1) -> list:
    key = f"minute_{code}_{interval}"
    ttl = 60 if _is_kr_market_hours() else 3600
    c = _get_cache(key, ttl)
    if c is not None:
        return c
    h = _headers("FHKST03010200")
    if not h:
        return []
    _rate_limit()
    p = {
        "FID_ETC_CLS_CODE": "",
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_HOUR_1": datetime.now().strftime("%H%M%S"),
        "FID_PW_DATA_INCU_YN": "Y",
    }
    try:
        r = requests.get(
            f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
            headers=h, params=p, timeout=10,
        )
        d = r.json()
        if d.get("rt_cd") != "0":
            log.warning("[KIS] 분봉 %s: %s", code, d.get("msg1"))
            return []
        out = []
        for it in d.get("output2", []):
            try:
                o = int(it.get("stck_oprc") or 0)
                hi = int(it.get("stck_hgpr") or 0)
                lo = int(it.get("stck_lwpr") or 0)
                cl = int(it.get("stck_prpr") or 0)
                v = int(it.get("cntg_vol") or 0)
                if o <= 0 or cl <= 0:
                    continue
                out.append({
                    "time": it.get("stck_cntg_hour", ""),
                    "date": it.get("stck_bsop_date", ""),
                    "open": o, "high": hi, "low": lo, "close": cl,
                    "volume": v,
                })
            except Exception:
                continue
        # 한투는 최신순 → 오래된순으로 뒤집어 차트 친화 형태
        out.reverse()
        if out:
            _set_cache(key, out)
        return out
    except Exception as exc:
        log.warning("[KIS] 분봉 %s 호출실패: %s", code, exc)
        return []


# ── 호가 10단계 (장중 5s, 장외 1h) ────────────────
def get_orderbook(code: str) -> dict | None:
    key = f"orderbook_{code}"
    ttl = 5 if _is_kr_market_hours() else 3600
    c = _get_cache(key, ttl)
    if c is not None:
        return c
    h = _headers("FHKST01010200")
    if not h:
        return None
    _rate_limit()
    p = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
    try:
        r = requests.get(
            f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
            headers=h, params=p, timeout=10,
        )
        d = r.json()
        if d.get("rt_cd") != "0":
            log.warning("[KIS] 호가 %s: %s", code, d.get("msg1"))
            return None
        out1 = d.get("output1", {})
        asks, bids = [], []
        for i in range(1, 11):
            ap = int(out1.get(f"askp{i}") or 0)
            aq = int(out1.get(f"askp_rsqn{i}") or 0)
            if ap > 0:
                asks.append({"price": ap, "qty": aq})
            bp = int(out1.get(f"bidp{i}") or 0)
            bq = int(out1.get(f"bidp_rsqn{i}") or 0)
            if bp > 0:
                bids.append({"price": bp, "qty": bq})
        result = {
            "asks": asks, "bids": bids,
            "total_ask_qty": int(out1.get("total_askp_rsqn") or 0),
            "total_bid_qty": int(out1.get("total_bidp_rsqn") or 0),
            "timestamp": datetime.now().strftime("%H:%M:%S"),
        }
        if asks or bids:
            _set_cache(key, result)
        return result
    except Exception as exc:
        log.warning("[KIS] 호가 %s 호출실패: %s", code, exc)
        return None


# ── 투자자별 매매동향 (10분 캐시) ────────────────
def get_investor_trading(code: str) -> list:
    key = f"investor_{code}"
    c = _get_cache(key, 600)
    if c is not None:
        return c
    h = _headers("FHKST01010900")
    if not h:
        return []
    _rate_limit()
    p = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
    try:
        r = requests.get(
            f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-investor",
            headers=h, params=p, timeout=10,
        )
        d = r.json()
        if d.get("rt_cd") != "0":
            log.warning("[KIS] 투자자 %s: %s", code, d.get("msg1"))
            return []
        out = []
        for it in d.get("output", [])[:10]:
            try:
                out.append({
                    "date": it.get("stck_bsop_date", ""),
                    "foreign_net": int(it.get("frgn_ntby_qty") or 0),
                    "inst_net": int(it.get("orgn_ntby_qty") or 0),
                    "retail_net": int(it.get("prsn_ntby_qty") or 0),
                })
            except Exception:
                continue
        if out:
            _set_cache(key, out)
        return out
    except Exception as exc:
        log.warning("[KIS] 투자자 %s 호출실패: %s", code, exc)
        return []


# ── 현재가 상세 (장중 30s, 장외 1h) ──────────────
def get_price_detail(code: str) -> dict | None:
    key = f"price_{code}"
    ttl = 30 if _is_kr_market_hours() else 3600
    c = _get_cache(key, ttl)
    if c is not None:
        return c
    h = _headers("FHKST01010100")
    if not h:
        return None
    _rate_limit()
    p = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
    try:
        r = requests.get(
            f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers=h, params=p, timeout=10,
        )
        d = r.json()
        if d.get("rt_cd") != "0":
            log.warning("[KIS] 현재가 %s: %s", code, d.get("msg1"))
            return None
        o = d.get("output", {})

        def _i(k):
            try: return int(o.get(k) or 0)
            except Exception: return 0
        def _f(k):
            try: return float(o.get(k) or 0)
            except Exception: return 0.0

        result = {
            "price": _i("stck_prpr"),
            "change": _i("prdy_vrss"),
            "change_pct": _f("prdy_ctrt"),
            "open": _i("stck_oprc"),
            "high": _i("stck_hgpr"),
            "low": _i("stck_lwpr"),
            "volume": _i("acml_vol"),
            "trade_amount": _i("acml_tr_pbmn"),
            "per": _f("per"),
            "pbr": _f("pbr"),
            "eps": _f("eps"),
            "market_cap": _i("hts_avls"),  # 단위: 억
            "high_52w": _i("stck_dryc_hgpr"),
            "low_52w": _i("stck_dryc_lwpr"),
            "high_52w_date": o.get("dryy_hgpr_date", ""),
            "low_52w_date": o.get("dryy_lwpr_date", ""),
        }
        _set_cache(key, result)
        return result
    except Exception as exc:
        log.warning("[KIS] 현재가 %s 호출실패: %s", code, exc)
        return None
