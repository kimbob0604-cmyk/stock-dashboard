"""JSON 캐시 → SQLite 마이그레이션 스크립트."""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

# 프로젝트 루트를 path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))
from db.database import get_db, init_db

CACHE_DIR = Path(__file__).parent.parent / "cache"

_errors: list[str] = []


def _safe_load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        _errors.append(f"{path.name}: {e}")
        return None


# ── 1. stocks ──────────────────────────────────────────────────────
def migrate_stocks():
    """naver_universe + us_market → stocks 테이블."""
    rows = []

    # 국내: 최신 naver_universe
    uni_files = sorted(CACHE_DIR.glob("naver_universe_*.json"), reverse=True)
    if uni_files:
        data = _safe_load(uni_files[0])
        if data:
            for code, info in (data.get("stocks") or {}).items():
                rows.append((
                    code,
                    info.get("name", ""),
                    "",  # market (KRX 분류 없음)
                    (info.get("sectors") or [None])[0],
                    None,  # market_cap
                    info.get("close"),
                    info.get("change_pct"),
                    info.get("volume_mn"),
                    json.dumps(info.get("sectors") or [], ensure_ascii=False),
                ))

    # 미국: 최신 us_market
    us_files = sorted(CACHE_DIR.glob("us_market_*.json"), reverse=True)
    if us_files:
        data = _safe_load(us_files[0])
        if data:
            for s in data.get("all_stocks") or []:
                rows.append((
                    s.get("symbol", ""),
                    s.get("name", ""),
                    "US",
                    s.get("sector"),
                    s.get("market_cap"),
                    s.get("price"),
                    s.get("change_pct"),
                    s.get("volume_mn"),
                    json.dumps([s.get("sector")] if s.get("sector") else [], ensure_ascii=False),
                ))

    with get_db() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO stocks "
            "(code, name, market, sector, market_cap, close, change_pct, volume_mn, sectors_json) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
    print(f"  stocks: {len(rows):,}개")
    return len(rows)


# ── 2. chart_cache ─────────────────────────────────────────────────
def migrate_charts():
    """chart_* + us_chart_* → chart_cache 테이블."""
    kr_files = list(CACHE_DIR.glob("chart_*_*d_*.json"))
    us_files = list(CACHE_DIR.glob("us_chart_*_*d_*.json"))
    all_files = kr_files + us_files
    rows = []

    for path in all_files:
        data = _safe_load(path)
        if not data:
            continue

        code = data.get("code", "")
        days = data.get("days", 180)

        # cache_date: 파일명에서 추출 (마지막 _YYYYMMDD)
        m = re.search(r"_(\d{8})\.json$", path.name)
        cache_date = m.group(1) if m else ""

        # rsi_macd 없는 stale 캐시 건너뛰기
        if not data.get("rsi_macd"):
            continue

        rows.append((
            code, days, cache_date,
            data.get("name"),
            json.dumps(data.get("dates") or [], ensure_ascii=False),
            json.dumps(data.get("open") or [], ensure_ascii=False),
            json.dumps(data.get("high") or [], ensure_ascii=False),
            json.dumps(data.get("low") or [], ensure_ascii=False),
            json.dumps(data.get("close") or [], ensure_ascii=False),
            json.dumps(data.get("volume") or [], ensure_ascii=False),
            json.dumps(data.get("bollinger") or {}, ensure_ascii=False),
            json.dumps(data.get("fibonacci") or {}, ensure_ascii=False),
            json.dumps(data.get("trendlines") or {}, ensure_ascii=False),
            json.dumps(data.get("analysis") or {}, ensure_ascii=False),
            json.dumps(data.get("rsi_macd") or {}, ensure_ascii=False),
            json.dumps(data.get("adx") or {}, ensure_ascii=False),
        ))

    with get_db() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO chart_cache "
            "(code, days, cache_date, name, dates_json, open_json, high_json, "
            "low_json, close_json, volume_json, bollinger_json, fibonacci_json, "
            "trendlines_json, analysis_json, rsi_macd_json, adx_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
    print(f"  chart_cache: {len(rows):,}개 (스킵 {len(all_files) - len(rows)}개 stale)")
    return len(rows)


# ── 3. ohlcv (chart_cache에서 정규화) ──────────────────────────────
def migrate_ohlcv():
    """chart 파일에서 OHLCV 시계열을 정규화해서 ohlcv 테이블에 저장."""
    files = list(CACHE_DIR.glob("chart_*_180d_*.json")) + \
            list(CACHE_DIR.glob("us_chart_*_180d_*.json"))
    rows = []
    seen = set()

    for path in files:
        data = _safe_load(path)
        if not data:
            continue
        code = data.get("code", "")
        dates = data.get("dates") or []
        opens = data.get("open") or []
        highs = data.get("high") or []
        lows = data.get("low") or []
        closes = data.get("close") or []
        volumes = data.get("volume") or []

        for i, dt in enumerate(dates):
            key = (code, dt)
            if key in seen:
                continue
            seen.add(key)
            rows.append((
                code, dt,
                opens[i] if i < len(opens) else None,
                highs[i] if i < len(highs) else None,
                lows[i] if i < len(lows) else None,
                closes[i] if i < len(closes) else None,
                volumes[i] if i < len(volumes) else None,
            ))

    # 배치 insert (10000행 단위)
    with get_db() as conn:
        for i in range(0, len(rows), 10000):
            conn.executemany(
                "INSERT OR REPLACE INTO ohlcv (code, date, open, high, low, close, volume) "
                "VALUES (?,?,?,?,?,?,?)",
                rows[i:i + 10000],
            )
        conn.commit()
    print(f"  ohlcv: {len(rows):,}개 ({len(seen)} unique)")
    return len(rows)


# ── 4. financial ───────────────────────────────────────────────────
def migrate_financial():
    """financial_*.json → financial 테이블."""
    files = list(CACHE_DIR.glob("financial_*.json"))
    rows = []

    for path in files:
        data = _safe_load(path)
        if not data:
            continue
        code = data.get("code") or path.stem.replace("financial_", "")
        rows.append((
            code,
            data.get("name"),
            data.get("per"),
            data.get("eps"),
            data.get("estimate_per"),
            data.get("estimate_eps"),
            data.get("pbr"),
            data.get("bps"),
            data.get("dividend_yield"),
            data.get("market_cap"),
            data.get("market_cap_rank"),
            data.get("industry_per"),
            data.get("shares_outstanding"),
            data.get("foreign_ratio"),
            json.dumps(data.get("annual") or [], ensure_ascii=False),
        ))

    with get_db() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO financial "
            "(code, name, per, eps, estimate_per, estimate_eps, pbr, bps, "
            "dividend_yield, market_cap, market_cap_rank, industry_per, "
            "shares_outstanding, foreign_ratio, annual_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
    print(f"  financial: {len(rows):,}개")
    return len(rows)


# ── 5. flow_cache ──────────────────────────────────────────────────
def migrate_flow():
    """flow_*.json → flow_cache 테이블 (종목별 20일 블록 보존)."""
    files = list(CACHE_DIR.glob("flow_*.json"))
    rows = []

    for path in files:
        data = _safe_load(path)
        if not data:
            continue
        code = data.get("code") or path.stem.replace("flow_", "")
        rows.append((
            code,
            data.get("name"),
            json.dumps(data.get("dates") or [], ensure_ascii=False),
            json.dumps(data.get("close") or [], ensure_ascii=False),
            json.dumps(data.get("foreign_shares") or [], ensure_ascii=False),
            json.dumps(data.get("inst_shares") or [], ensure_ascii=False),
            json.dumps(data.get("foreign_value") or [], ensure_ascii=False),
            json.dumps(data.get("inst_value") or [], ensure_ascii=False),
            data.get("foreign_sum_20"),
            data.get("inst_sum_20"),
            data.get("source"),
            data.get("fetched_at"),
        ))

    with get_db() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO flow_cache "
            "(code, name, dates_json, close_json, foreign_shares_json, "
            "inst_shares_json, foreign_value_json, inst_value_json, "
            "foreign_sum_20, inst_sum_20, source, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
    print(f"  flow_cache: {len(rows):,}개")
    return len(rows)


# ── 6. yinfo_cache ─────────────────────────────────────────────────
def migrate_yinfo():
    """us_yinfo_*.json → yinfo_cache 테이블."""
    files = list(CACHE_DIR.glob("us_yinfo_*.json"))
    rows = []

    for path in files:
        data = _safe_load(path)
        if not data:
            continue
        symbol = path.stem.replace("us_yinfo_", "")
        rows.append((symbol, json.dumps(data, ensure_ascii=False)))

    with get_db() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO yinfo_cache (symbol, info_json) VALUES (?,?)",
            rows,
        )
        conn.commit()
    print(f"  yinfo_cache: {len(rows):,}개")
    return len(rows)


# ── 7. discover_results ────────────────────────────────────────────
def migrate_discover():
    """discover_*.json → discover_results 테이블."""
    files = list(CACHE_DIR.glob("discover_*stage*.json"))
    rows = []

    for path in files:
        data = _safe_load(path)
        if not data:
            continue
        stage = data.get("stage", 1)
        market = data.get("market", "kr")
        rows.append((
            stage,
            market,
            data.get("total_scanned"),
            data.get("kospi_count"),
            data.get("kosdaq_count"),
            json.dumps(data.get("items") or [], ensure_ascii=False),
            data.get("updated_at"),
        ))

    with get_db() as conn:
        conn.executemany(
            "INSERT INTO discover_results "
            "(stage, market, total_scanned, kospi_count, kosdaq_count, items_json, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
    print(f"  discover_results: {len(rows):,}개")
    return len(rows)


# ── 8. misc_cache (기타 파일) ──────────────────────────────────────
def migrate_misc():
    """기타 캐시 파일 → misc_cache 테이블."""
    # 이미 전용 테이블에 넣은 프리픽스 제외
    skip_prefixes = (
        "chart_", "us_chart_", "financial_", "flow_",
        "us_yinfo_", "naver_universe_", "us_market_",
        "discover_", "stock_master_",
    )
    files = [f for f in CACHE_DIR.glob("*.json")
             if not any(f.name.startswith(p) for p in skip_prefixes)]
    rows = []

    for path in files:
        data = _safe_load(path)
        if data is None:
            continue
        rows.append((path.stem, json.dumps(data, ensure_ascii=False)))

    with get_db() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO misc_cache (cache_key, data_json) VALUES (?,?)",
            rows,
        )
        conn.commit()
    print(f"  misc_cache: {len(rows):,}개")
    return len(rows)


# ── main ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print(" JSON → SQLite 마이그레이션")
    print("=" * 50)
    print()

    t0 = time.time()
    init_db()

    print("\n[1/8] stocks")
    migrate_stocks()

    print("[2/8] chart_cache")
    migrate_charts()

    print("[3/8] ohlcv")
    migrate_ohlcv()

    print("[4/8] financial")
    migrate_financial()

    print("[5/8] flow_cache")
    migrate_flow()

    print("[6/8] yinfo_cache")
    migrate_yinfo()

    print("[7/8] discover_results")
    migrate_discover()

    print("[8/8] misc_cache")
    migrate_misc()

    elapsed = time.time() - t0
    db_size = os.path.getsize(str(Path(__file__).parent / "dashboard.db")) / 1024 / 1024

    print()
    print("=" * 50)
    print(f" 완료: {elapsed:.1f}초, DB 크기 {db_size:.1f}MB")
    print("=" * 50)

    if _errors:
        print(f"\n⚠ 에러 {len(_errors)}건:")
        for e in _errors[:20]:
            print(f"  - {e}")
