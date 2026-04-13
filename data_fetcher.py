"""
data_fetcher.py  —  한국 주식 테마 데이터 수집기
의존성: pip install pykrx
실행:   python data_fetcher.py  →  data.json 생성

── 캐싱 전략 ──────────────────────────────────────────────────────
  cache/ticker_data_{YYYYMMDD}.json     — 테마 종목 스파크라인 + 시세 (일 1회)
  cache/index_{YYYYMMDD}.json           — 지수 (일 1회)
  cache/new_high_{YYYYMMDD_금요일}.json  — 52주 신고가 (주 1회)
  cache/ticker_names.json               — 종목코드→이름 (자주 안 바뀜)
  cache/cache_meta.json                 — 각 캐시의 생성 시각·날짜

── pykrx 호출 전략 ────────────────────────────────────────────────
  KRX 서버 이슈로 get_market_ohlcv_by_ticker (전 종목 일괄) API 응답 없음.
  get_market_ohlcv_by_date (단일종목 + 날짜범위) 는 정상 작동.

  · 종목 시세 + 스파크라인: get_market_ohlcv_by_date 1회/종목
    거래대금 = 종가 × 거래량 으로 근사 (get_market_trading_value API 응답 없음)
  · 지수: get_index_ohlcv_by_date("1001"/"2001") 로 실제 지수 조회
          실패 시 069500/229200 ETF로 change_pct 대체
  · 52주 신고가: get_market_ohlcv_by_date 1회/종목, 직전주 월~금 내 52주 최고가 경신 여부

── 거래일 탐색 전략 ───────────────────────────────────────────────
  get_market_ohlcv_by_date 의 반환 DataFrame 마지막 행 날짜로 실제 거래일 확정.
"""

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── 한국 시간대 ─────────────────────────────────────────────────────
# Render 등 UTC 서버에서도 KST 기준으로 날짜가 계산되도록 명시.
KST = timezone(timedelta(hours=9))

def now_kst() -> datetime:
    return datetime.now(KST)

try:
    from pykrx import stock
except ImportError:
    raise SystemExit("pykrx 설치 필요: pip install pykrx")


# ─────────────────────────────────────────────────────────────────────────────
# 경로 설정
# ─────────────────────────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent
CACHE_DIR     = BASE_DIR / "cache"
META_FILE     = CACHE_DIR / "cache_meta.json"
NAMES_FILE    = CACHE_DIR / "ticker_names.json"
MAPPING       = BASE_DIR / "themes_mapping.json"
OUT_FILE      = BASE_DIR / "data.json"
RANKING_FILE  = CACHE_DIR / "ranking_history.json"

# 지수 대리 ETF  (지수 API 미작동 시 change_pct 대체용)
INDEX_ETF = {
    "kospi":  ("069500", "KODEX 200"),
    "kosdaq": ("229200", "KODEX KOSDAQ150"),
}

MAX_LOOKBACK = 10


# ─────────────────────────────────────────────────────────────────────────────
# 날짜 헬퍼
# ─────────────────────────────────────────────────────────────────────────────
def today_str() -> str:
    """KST 기준 오늘 (YYYYMMDD). 거래일 판별은 API 응답으로 결정."""
    return now_kst().strftime("%Y%m%d")


def date_str_minus(date_str: str, days: int) -> str:
    d = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=KST) - timedelta(days=days)
    return d.strftime("%Y%m%d")


# ─────────────────────────────────────────────────────────────────────────────
# 캐시 유틸
# ─────────────────────────────────────────────────────────────────────────────
def ensure_cache_dir():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def load_meta() -> dict:
    if META_FILE.exists():
        with open(META_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_meta(meta: dict):
    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def cache_hit(key: str, date: str) -> bool:
    return load_meta().get(key, {}).get("date") == date


def write_cache(path: Path, data, key: str, date: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    meta = load_meta()
    meta[key] = {"date": date, "created_at": now_kst().strftime("%Y-%m-%d %H:%M:%S")}
    save_meta(meta)


def read_cache(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────────────────────
# 안전 호출 래퍼 (지수 백오프)
# ─────────────────────────────────────────────────────────────────────────────
def safe_call(func, *args, max_retries: int = 3, **kwargs):
    for attempt in range(max_retries):
        try:
            result = func(*args, **kwargs)
            time.sleep(0.1)
            return result
        except Exception as exc:
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"  ⚠  재시도 {attempt + 1}/{max_retries}: {exc!r}  ({wait}s 대기)")
                time.sleep(wait)
            else:
                print(f"  ✗  {func.__name__}{args} 최종 실패: {exc!r}")
                raise


# ─────────────────────────────────────────────────────────────────────────────
# 단일 종목 OHLCV 파싱 헬퍼
# ─────────────────────────────────────────────────────────────────────────────
def _parse_ohlcv_df(df):
    """
    get_market_ohlcv_by_date 결과에서 컬럼을 유연하게 탐지하여 반환.
    Returns: (close, volume, change_pct, last_date_str)  또는 None
    """
    if df is None or df.empty:
        return None
    chg_col   = next((c for c in df.columns if "등락률" in c), None)
    close_col = next((c for c in df.columns if "종가"   in c), None)
    vol_col   = next((c for c in df.columns if "거래량" in c), None)
    if close_col is None:
        return None
    last      = df.iloc[-1]
    close     = float(last[close_col])
    volume    = int(last[vol_col])   if vol_col  else 0
    chg       = float(last[chg_col]) if chg_col  else 0.0
    last_date = df.index[-1].strftime("%Y%m%d")
    return close, volume, chg, last_date


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — 테마 종목 시세 + 스파크라인 (per-ticker, 하나의 루프)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_ticker_data(codes: list[str], req_date: str,
                      force: bool = False) -> tuple[dict, dict, str]:
    """
    테마 종목 전체에 대해 get_market_ohlcv_by_date 를 1회/종목 호출,
    스파크라인(최근 20일 정규화 종가)과 시세(등락률·거래대금) 동시 수집.

    거래대금 = 종가 × 거래량  (bulk API 미작동으로 인한 근사치)

    force=True (--force-market):
      스파크라인은 기존 캐시 유지, 당일 시세만 단일 날짜 호출로 재조회.

    Returns:
        sparklines  : {code: [float, ...]}
        market_data : {code: {change_pct, volume_mn}}
        actual_date : 실제 수집된 마지막 거래일 (YYYYMMDD)
    """
    cache_key  = f"ticker_data_{req_date}"
    cache_file = CACHE_DIR / f"ticker_data_{req_date}.json"

    # ── 장중 강제 갱신: 스파크라인 캐시 유지 + 당일 시세만 재조회 ──
    if force and cache_file.exists():
        cached      = read_cache(cache_file)
        sparklines  = cached.get("sparklines",  {})
        market_data = dict(cached.get("market_data", {}))
        actual_date = cached.get("actual_date", req_date)
        total = len(codes)
        print(f"  [장중 갱신] 당일 시세 재조회 ({req_date}, {total}개)…")
        for i, code in enumerate(codes):
            if i > 0 and i % 50 == 0:
                print(f"    {i}/{total} 완료")
            try:
                df = safe_call(stock.get_market_ohlcv_by_date, req_date, req_date, code)
                parsed = _parse_ohlcv_df(df)
                if parsed is None:
                    continue
                close, volume, chg, last_date = parsed
                vol_mn = int(close * volume) // 1_000_000
                market_data[code] = {"change_pct": round(chg, 2), "volume_mn": vol_mn}
                if actual_date == req_date and last_date < req_date:
                    actual_date = last_date
            except Exception:
                pass
        payload = {"sparklines": sparklines, "market_data": market_data, "actual_date": actual_date}
        write_cache(cache_file, payload, cache_key, req_date)
        print(f"  [장중 갱신] 완료 ({len(market_data)}개,  실제거래일: {actual_date})")
        return sparklines, market_data, actual_date

    if cache_hit(cache_key, req_date) and cache_file.exists():
        cached = read_cache(cache_file)
        missing = [c for c in codes if c not in cached.get("market_data", {})]
        if not missing:
            print(f"  [캐시] 종목 데이터  ({req_date})")
            return cached["sparklines"], cached["market_data"], cached.get("actual_date", req_date)
        print(f"  종목 데이터 부분 갱신 ({len(missing)}개 신규)…")
        sparklines  = cached.get("sparklines",  {})
        market_data = cached.get("market_data", {})
        actual_date = cached.get("actual_date",  req_date)
        codes = missing
    else:
        sparklines  = {}
        market_data = {}
        actual_date = req_date

    start = date_str_minus(req_date, 40)   # 40일 전 → 영업일 약 28일 확보
    total = len(codes)
    print(f"  종목 데이터 조회 중 (총 {total}개, {req_date} 기준)…")

    for i, code in enumerate(codes):
        if i > 0 and i % 50 == 0:
            print(f"    {i}/{total} 완료  (실제거래일: {actual_date})")
        try:
            df = safe_call(stock.get_market_ohlcv_by_date, start, req_date, code)
            parsed = _parse_ohlcv_df(df)
            if parsed is None:
                sparklines[code]  = []
                market_data[code] = {"change_pct": 0.0, "volume_mn": 0}
                continue

            close, volume, chg, last_date = parsed

            # 첫 유효 응답으로 실제 거래일 확정
            if actual_date == req_date and last_date < req_date:
                actual_date = last_date

            # 거래대금 근사치 (원 → 백만원)
            vol_mn = int(close * volume) // 1_000_000

            market_data[code] = {"change_pct": round(chg, 2), "volume_mn": vol_mn}

            # 스파크라인: 최근 20일 종가 정규화
            close_col = next((c for c in df.columns if "종가" in c), None)
            closes = df[close_col].tail(20).tolist() if close_col else []
            if closes and closes[0] != 0:
                base   = closes[0]
                closes = [round(v / base * 100, 2) for v in closes]
            sparklines[code] = closes

        except Exception:
            sparklines[code]  = []
            market_data[code] = {"change_pct": 0.0, "volume_mn": 0}

    # 캐시 저장 (actual_date 기준 키로 저장)
    payload = {"sparklines": sparklines, "market_data": market_data, "actual_date": actual_date}
    write_cache(cache_file, payload, cache_key, req_date)

    # actual_date ≠ req_date 이면 actual_date 키로도 저장 (중복 실행 방지)
    if actual_date != req_date:
        alt_key  = f"ticker_data_{actual_date}"
        alt_file = CACHE_DIR / f"ticker_data_{actual_date}.json"
        if not alt_file.exists():
            write_cache(alt_file, payload, alt_key, actual_date)

    print(f"  종목 데이터 {len(market_data)}개 완료  (실제거래일: {actual_date})")
    return sparklines, market_data, actual_date


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — KOSPI / KOSDAQ 지수 (실제 지수 API, 실패 시 ETF 대리)
# ─────────────────────────────────────────────────────────────────────────────
INDEX_REAL = {
    "kospi":  ("1001", "KOSPI"),
    "kosdaq": ("2001", "KOSDAQ"),
}


def _fetch_naver_indices() -> dict:
    """
    Naver Finance 실시간 지수 API로 KOSPI/KOSDAQ 실제 지수값 조회.
    pykrx 의 get_index_ohlcv_by_date 가 KRX 응답 포맷 변경으로 실패하므로 대체 경로.
    nv 값은 원 지수 × 100 으로 들어오므로 /100 스케일링.

    Returns: {"kospi": {"value": ..., "change_pct": ...}, "kosdaq": {...}}
             실패 시 {}
    """
    import urllib.request
    import urllib.error
    url = ("https://polling.finance.naver.com/api/realtime"
           "?query=SERVICE_INDEX%3AKOSPI%2CKOSDAQ")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8")
        payload = json.loads(body)
        datas = payload["result"]["areas"][0]["datas"]
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError) as e:
        print(f"  ⚠  Naver 지수 API 실패: {e!r}")
        return {}

    out = {}
    for row in datas:
        cd = row.get("cd")
        nv = row.get("nv")
        cr = row.get("cr")
        if cd is None or nv is None:
            continue
        key = "kospi" if cd == "KOSPI" else "kosdaq" if cd == "KOSDAQ" else None
        if not key:
            continue
        out[key] = {"value": round(nv / 100, 2), "change_pct": round(float(cr or 0.0), 2)}
    return out


def fetch_indices(req_date: str, force: bool = False) -> tuple[dict, str]:
    """
    지수 조회 순서:
      1) Naver Finance 실시간 API (실제 지수값)
      2) pykrx get_index_ohlcv_by_date (현재 KRX 응답 변경으로 주로 실패)
      3) ETF 대리 (069500, 229200) — change_pct 만 근사

    force=True 이면 캐시를 무시하고 재조회.

    Returns: (indices_dict, actual_date)
    """
    cache_key  = f"index_{req_date}"
    cache_file = CACHE_DIR / f"index_{req_date}.json"

    if not force and cache_hit(cache_key, req_date) and cache_file.exists():
        cached = read_cache(cache_file)
        # 캐시가 ETF 스케일(수만 단위)이거나 0 이면 재조회
        ksp = cached.get("kospi", {}).get("value", 0)
        ksq = cached.get("kosdaq", {}).get("value", 0)
        looks_real = 1000 <= ksp <= 10000 and 400 <= ksq <= 3000
        if looks_real:
            print(f"  [캐시] 지수  ({req_date})")
            return cached, req_date
        print(f"  ⚠  캐시된 지수가 ETF 값으로 보임 — 재조회")

    print(f"  지수 조회 중 ({req_date})…")
    actual_date = req_date
    start       = date_str_minus(req_date, 7)

    # 1차: Naver 실시간 API (실제 지수 우선)
    result = _fetch_naver_indices()
    if result.get("kospi") and result.get("kosdaq"):
        ksp = result["kospi"]
        ksq = result["kosdaq"]
        print(f"    kospi:  {ksp['value']:,.2f}  {ksp['change_pct']:+.2f}%  (Naver 실시간)")
        print(f"    kosdaq: {ksq['value']:,.2f}  {ksq['change_pct']:+.2f}%  (Naver 실시간)")
        write_cache(cache_file, result, cache_key, req_date)
        return result, actual_date

    # 2차: pykrx 실제 지수 (현재 대부분 실패)
    print("  Naver 실패 → pykrx 지수 API 시도")
    result = {}
    for key, (idx_code, idx_name) in INDEX_REAL.items():
        try:
            df = safe_call(stock.get_index_ohlcv_by_date, start, req_date, idx_code)
            parsed = _parse_ohlcv_df(df)
            if parsed is None:
                raise ValueError("빈 응답")
            close, _, chg, last_date = parsed
            result[key] = {"value": round(close, 2), "change_pct": round(chg, 2)}
            if actual_date == req_date and last_date < req_date:
                actual_date = last_date
            print(f"    {key}: {idx_name}({idx_code}) {close:,.2f}  {chg:+.2f}%")
        except Exception as e:
            print(f"  ⚠  {key} pykrx 지수 API 실패: {e!r}")

    # 3차: ETF 대리 (change_pct 만 근사, value 는 ETF 가격이므로 지수와 무관)
    for key, (etf_code, etf_name) in INDEX_ETF.items():
        if key in result:
            continue
        try:
            df = safe_call(stock.get_market_ohlcv_by_date, start, req_date, etf_code)
            parsed = _parse_ohlcv_df(df)
            if parsed is None:
                result[key] = {"value": 0.0, "change_pct": 0.0}
                continue
            close, _, chg, last_date = parsed
            # ETF 가격을 그대로 value 로 넣지 않고 0 처리 → 프론트에서 "—" 표시
            result[key] = {"value": 0.0, "change_pct": round(chg, 2)}
            if actual_date == req_date and last_date < req_date:
                actual_date = last_date
            print(f"    {key}: {etf_name}({etf_code}) {chg:+.2f}%  (ETF 대리, value 미표시)")
        except Exception as e2:
            print(f"  ✗  {key} ETF 대리도 실패: {e2!r}")
            result[key] = {"value": 0.0, "change_pct": 0.0}

    write_cache(cache_file, result, cache_key, req_date)
    return result, actual_date


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — 52주 신고가 섹터 (직전 거래주 기준)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_new_highs(all_codes: list[str], mapping: list[dict],
                    req_date: str) -> list[dict]:
    """
    직전 거래주(월~금) 중 52주 신고가를 경신한 종목을 테마별로 집계.
    신고가 종목이 5개 이상인 테마만 반환.

    Cache: cache/new_high_{last_friday}.json  (주 단위 갱신)
    """
    req_dt = datetime.strptime(req_date, "%Y%m%d").replace(tzinfo=KST)
    # 직전 금요일 (오늘이 금요일이면 7일 전 금요일)
    days_back    = (req_dt.weekday() - 4) % 7 or 7
    last_friday  = req_dt - timedelta(days=days_back)
    last_monday  = last_friday - timedelta(days=4)
    year_ago     = last_monday - timedelta(weeks=52)

    last_fri_str = last_friday.strftime("%Y%m%d")
    last_mon_str = last_monday.strftime("%Y%m%d")
    year_ago_str = year_ago.strftime("%Y%m%d")

    cache_key  = f"new_high_{last_fri_str}"
    cache_file = CACHE_DIR / f"new_high_{last_fri_str}.json"

    if cache_hit(cache_key, last_fri_str) and cache_file.exists():
        print(f"  [캐시] 52주 신고가  ({last_mon_str}~{last_fri_str})")
        return read_cache(cache_file)

    print(f"  52주 신고가 조회 중 (직전주: {last_mon_str}~{last_fri_str})…")

    # 코드→이름, 코드→테마ID 맵 구성
    code_name:   dict[str, str]   = {}
    code_themes: dict[str, list]  = {}
    theme_info:  dict[int, dict]  = {}
    for t in mapping:
        theme_info[t["id"]] = {"name": t["name"], "total_count": len(t["stocks"])}
        for item in t["stocks"]:
            c = item["code"] if isinstance(item, dict) else item
            n = item.get("name", "") if isinstance(item, dict) else ""
            code_name[c] = n
            code_themes.setdefault(c, []).append(t["id"])

    new_high_stocks: list[dict] = []
    total = len(all_codes)

    for i, code in enumerate(all_codes):
        if i > 0 and i % 30 == 0:
            print(f"    {i}/{total} 완료…")
        try:
            df = safe_call(stock.get_market_ohlcv_by_date,
                           year_ago_str, last_fri_str, code)
            if df is None or df.empty:
                continue

            high_col  = next((c for c in df.columns if "고가"   in c), None)
            close_col = next((c for c in df.columns if "종가"   in c), None)
            chg_col   = next((c for c in df.columns if "등락률" in c), None)
            if not high_col or not close_col:
                continue

            year_high = float(df[high_col].max())

            # 직전주 행만 필터 (DatetimeIndex → 문자열 비교)
            lw = df[df.index.strftime("%Y%m%d") >= last_mon_str]
            if lw.empty:
                continue

            week_high = float(lw[high_col].max())
            if week_high < year_high:
                continue

            # 신고가 달성일
            high_idx   = lw[high_col].idxmax()
            high_date  = high_idx.strftime("%Y-%m-%d")
            last_close = float(df[close_col].iloc[-1])
            last_chg   = float(df[chg_col].iloc[-1]) if chg_col else 0.0

            new_high_stocks.append({
                "code":          code,
                "name":          code_name.get(code, code),
                "high_date":     high_date,
                "high_price":    int(week_high),
                "current_price": int(last_close),
                "change_pct":    round(last_chg, 2),
            })
            time.sleep(0.05)
        except Exception:
            continue

    print(f"  52주 신고가 종목: {len(new_high_stocks)}개")

    # 테마별 집계
    theme_bucket: dict[int, list] = {}
    for s in new_high_stocks:
        for tid in code_themes.get(s["code"], []):
            theme_bucket.setdefault(tid, []).append(s)

    result = []
    for tid, stocks in theme_bucket.items():
        if len(stocks) < 5:
            continue
        info = theme_info[tid]
        result.append({
            "name":           info["name"],
            "new_high_count": len(stocks),
            "total_count":    info["total_count"],
            "stocks":         sorted(stocks, key=lambda x: x["high_price"], reverse=True),
        })
    result.sort(key=lambda x: x["new_high_count"], reverse=True)

    write_cache(cache_file, result, cache_key, last_fri_str)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 종목명 조회
# ─────────────────────────────────────────────────────────────────────────────
def load_ticker_names(codes: list[str], date: str) -> dict[str, str]:
    names: dict[str, str] = {}

    if NAMES_FILE.exists():
        cached_names = read_cache(NAMES_FILE)
        names.update({k: v for k, v in cached_names.items() if k in codes})

    missing = [c for c in codes if c not in names]
    if not missing:
        return names

    print(f"  종목명 pykrx 조회 ({len(missing)}개)…")
    for market in ("KOSPI", "KOSDAQ"):
        try:
            tlist = safe_call(stock.get_market_ticker_list, date, market=market)
            for t in tlist:
                if t in missing:
                    try:
                        name = safe_call(stock.get_market_ticker_name, t)
                        names[t] = name or t
                    except Exception:
                        names[t] = t
        except Exception as e:
            print(f"  ✗  {market} 티커 목록 실패: {e}")

    for c in missing:
        names.setdefault(c, c)

    existing = read_cache(NAMES_FILE) if NAMES_FILE.exists() else {}
    existing.update(names)
    with open(NAMES_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    return names


# ─────────────────────────────────────────────────────────────────────────────
# 가중평균 등락률
# ─────────────────────────────────────────────────────────────────────────────
def weighted_avg(stocks_list: list[dict]) -> float:
    total_vol = sum(s["volume_mn"] for s in stocks_list if s["volume_mn"] > 0)
    if total_vol > 0:
        return sum(s["change_pct"] * s["volume_mn"] for s in stocks_list) / total_vol
    vals = [s["change_pct"] for s in stocks_list]
    return sum(vals) / len(vals) if vals else 0.0


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — 전 종목 마스터 (종목 검색 UI용, 일 1회)
# ─────────────────────────────────────────────────────────────────────────────
def build_stock_master(date: str) -> dict:
    """
    KOSPI + KOSDAQ 전 종목 코드→이름 매핑 생성.
    cache/stock_master_{YYYYMMDD}.json 에 저장, 이미 있으면 재사용.
    유효 티커 목록이 없으면 최대 10일 소급하여 재시도.
    """
    cache_file = CACHE_DIR / f"stock_master_{date}.json"
    if cache_file.exists():
        print(f"  [캐시] 전 종목 마스터  ({date})")
        return read_cache(cache_file)

    # 1차: pykrx 전 종목 티커 목록 (KRX 배치 API — 미작동 시 빈 리스트 반환)
    print(f"  전 종목 마스터 구축 중 ({date})…")
    master: dict[str, str] = {}
    for market in ("KOSPI", "KOSDAQ"):
        try:
            tickers = safe_call(stock.get_market_ticker_list, date, market=market)
            if tickers:
                print(f"    {market}: {len(tickers)}개 이름 조회…")
                for code in tickers:
                    try:
                        master[code] = stock.get_market_ticker_name(code) or code
                        time.sleep(0.02)
                    except Exception:
                        master[code] = code
        except Exception as e:
            print(f"  ⚠  {market} 티커 목록 실패: {e}")

    # 2차 fallback: themes_mapping.json + ticker_names.json 에서 알려진 종목 통합
    if not master:
        print("  ⚠  pykrx 티커 목록 없음 — themes_mapping + names 캐시로 대체")
        if MAPPING.exists():
            with open(MAPPING, encoding="utf-8") as f:
                mapping_data: list[dict] = json.load(f)
            for theme in mapping_data:
                for item in theme["stocks"]:
                    c = item["code"] if isinstance(item, dict) else item
                    n = item.get("name", c) if isinstance(item, dict) else c
                    master[c] = n
        if NAMES_FILE.exists():
            cached = read_cache(NAMES_FILE)
            master.update({k: v for k, v in cached.items() if k not in master})

    if not master:
        print("  ⚠  전 종목 마스터: 데이터 없음 — 스킵")
        return {}

    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(master, f, ensure_ascii=False)
    print(f"  전 종목 마스터 완료: {len(master)}개")
    return master


# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — 순위 히스토리 (이전 순위 대비 변동)
# ─────────────────────────────────────────────────────────────────────────────
def save_ranking_history(themes_out: list[dict]) -> list[dict]:
    """
    현재 순위를 타임스탬프와 함께 RANKING_FILE에 추가.
    최근 288개 스냅샷만 유지 (5분 간격 × 24h = 288).
    이전 형식({previous, current})은 자동 마이그레이션.
    """
    now_str = now_kst().strftime("%Y-%m-%d %H:%M:%S")
    current = {
        t["name"]: i + 1
        for i, t in enumerate(
            sorted(themes_out, key=lambda x: abs(x["weighted_avg_pct"]), reverse=True)
        )
    }
    history: list = []
    if RANKING_FILE.exists():
        try:
            loaded = json.loads(RANKING_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                history = loaded
            elif isinstance(loaded, dict) and "current" in loaded:
                # 이전 형식 마이그레이션
                history = [{"timestamp": now_str, "ranking": loaded["current"]}]
        except Exception:
            pass
    history.append({"timestamp": now_str, "ranking": current})
    history = history[-288:]
    RANKING_FILE.write_text(json.dumps(history, ensure_ascii=False), encoding="utf-8")
    return history


def apply_rank_changes(themes_out: list, history) -> list:
    """각 테마에 rank_change 필드 추가 (양수=상승, 음수=하락, 0=변동없음)."""
    if isinstance(history, list):
        if len(history) >= 2:
            curr = history[-1]["ranking"]
            prev = history[-2]["ranking"]
        elif len(history) == 1:
            curr = history[0]["ranking"]
            prev = {}
        else:
            curr, prev = {}, {}
    else:
        # 이전 형식 호환
        curr = history.get("current",  {})
        prev = history.get("previous", {})
    for theme in themes_out:
        name = theme["name"]
        c, p = curr.get(name), prev.get(name)
        theme["rank_change"] = (p - c) if (p and c) else 0
    return themes_out


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="테마 데이터 수집기")
    parser.add_argument("--force-market", action="store_true",
                        help="장중 갱신 모드: 당일 시세·지수 캐시 무시하고 재조회")
    args = parser.parse_args()
    force = args.force_market

    ensure_cache_dir()
    req = today_str()

    print(f"{'='*52}")
    if force:
        print(f"  테마 데이터 수집  —  [장중 갱신]  {req}")
    else:
        print(f"  테마 데이터 수집  —  요청일: {req}")
    print(f"{'='*52}")

    if not MAPPING.exists():
        raise SystemExit(
            "themes_mapping.json 없음.\n"
            "  python theme_crawler.py 를 먼저 실행하거나 수동으로 작성하세요."
        )
    with open(MAPPING, encoding="utf-8") as f:
        mapping: list[dict] = json.load(f)

    def _code(item):  return item["code"] if isinstance(item, dict) else item
    def _name(item):  return item.get("name") if isinstance(item, dict) else None

    all_codes = list({_code(s) for t in mapping for s in t["stocks"]})
    print(f"\n  테마 {len(mapping)}개 / 종목(유니크) {len(all_codes)}개\n")

    # ── 1. 종목 시세 + 스파크라인 (하나의 루프)
    print("[1/3] 종목 시세 + 스파크라인")
    sparklines, market_data, actual_date = fetch_ticker_data(all_codes, req, force=force)
    print(f"  → 실제 거래일: {actual_date}")

    # ── 2. 지수 (실제 지수 API, 실패 시 ETF 대리)
    print("\n[2/3] 지수")
    indices, _ = fetch_indices(req, force=force)

    # ── 3. 52주 신고가 섹터
    print("\n[3/3] 52주 신고가 섹터")
    new_high_sectors = fetch_new_highs(all_codes, mapping, req)
    if new_high_sectors:
        print(f"  → 신고가 5개 이상 섹터: {len(new_high_sectors)}개")
    else:
        print("  → 해당 섹터 없음")

    # ── 종목명
    print("\n[+]  종목명 확인")
    name_map: dict[str, str] = {}
    for t in mapping:
        for item in t["stocks"]:
            hint = _name(item)
            if hint:
                name_map[_code(item)] = hint

    need_lookup = [c for c in all_codes if c not in name_map]
    if need_lookup:
        pykrx_names = load_ticker_names(need_lookup, actual_date)
        name_map.update(pykrx_names)
    else:
        print("  themes_mapping.json 내 이름 모두 사용")

    # ── 순위 히스토리 준비 (기존 스냅샷 로드만 — 새 스냅샷은 themes_out 조합 후 저장)
    print("\n[+]  순위 히스토리")

    # ── 데이터 조합
    print("\n[+]  data.json 조합 중…")
    themes_out = []

    for theme in mapping:
        stocks_out = []
        for item in theme["stocks"]:
            code = _code(item)
            md   = market_data.get(code, {})
            stocks_out.append({
                "code":       code,
                "name":       name_map.get(code, code),
                "change_pct": md.get("change_pct", 0.0),
                "volume_mn":  md.get("volume_mn",  0),
                "sparkline":  sparklines.get(code, []),
            })

        active = [s for s in stocks_out if s["volume_mn"] > 0]
        if not active:
            continue

        themes_out.append({
            "id":               theme["id"],
            "name":             theme["name"],
            "weighted_avg_pct": round(weighted_avg(stocks_out), 2),
            "stock_count":      len(theme["stocks"]),
            "active_count":     len(active),
            "stocks":           stocks_out,
        })

    # 순위 변동 계산 (현재 themes_out 기준 현재 순위 저장 + 이전과 비교)
    current_history = save_ranking_history(themes_out)
    apply_rank_changes(themes_out, current_history)

    output = {
        "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "actual_date": actual_date,
        "kospi":  indices.get("kospi",  {"value": 0.0, "change_pct": 0.0}),
        "kosdaq": indices.get("kosdaq", {"value": 0.0, "change_pct": 0.0}),
        "themes": themes_out,
        "new_high_sectors": new_high_sectors,
    }

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*52}")
    print(f"  완료  →  data.json  (테마 {len(themes_out)}개, 거래일 {actual_date})")
    print(f"{'='*52}")

    # ── 전 종목 마스터 (종목 검색 UI용, 시간 걸리므로 data.json 저장 후 실행)
    print("\n[+]  전 종목 마스터")
    build_stock_master(actual_date)


if __name__ == "__main__":
    main()
