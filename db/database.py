"""SQLite 캐시 DB 헬퍼."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "dashboard.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# 스레드별 커넥션 풀 (Flask 멀티스레드 대응)
_local = threading.local()


def init_db():
    """DB 초기화 + 스키마 적용."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.commit()
    conn.close()
    print(f"[DB] 초기화 완료: {DB_PATH} ({DB_PATH.stat().st_size / 1024:.0f}KB)")


@contextmanager
def get_db():
    """커넥션 컨텍스트 매니저. WAL 모드로 동시 읽기 성능 향상."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
    finally:
        conn.close()


def get_thread_conn() -> sqlite3.Connection:
    """스레드별 재사용 커넥션. Flask 요청 사이클 내에서 사용."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn = conn
    return conn


def dict_from_row(row) -> dict | None:
    """sqlite3.Row → dict. *_json 필드 자동 파싱."""
    if row is None:
        return None
    d = dict(row)
    for key in list(d.keys()):
        if key.endswith("_json") and d[key] is not None:
            try:
                parsed = json.loads(d[key])
                short_key = key[:-5]  # _json 제거
                d[short_key] = parsed
                del d[key]
            except (json.JSONDecodeError, TypeError):
                pass
    return d


def bulk_upsert(table: str, rows: list[dict]) -> int:
    """INSERT OR REPLACE 배치."""
    if not rows:
        return 0
    columns = list(rows[0].keys())
    placeholders = ",".join(["?"] * len(columns))
    sql = f"INSERT OR REPLACE INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
    with get_db() as conn:
        conn.executemany(sql, [tuple(r.get(c) for c in columns) for r in rows])
        conn.commit()
    return len(rows)


def query_one(sql: str, params: tuple = ()) -> dict | None:
    """단일 행 조회 → dict."""
    conn = get_thread_conn()
    row = conn.execute(sql, params).fetchone()
    return dict_from_row(row)


def query_all(sql: str, params: tuple = ()) -> list[dict]:
    """다중 행 조회 → list[dict]."""
    conn = get_thread_conn()
    rows = conn.execute(sql, params).fetchall()
    return [dict_from_row(r) for r in rows]


# ── Stage 2 전용 배치 조회 ──────────────────────────────────────────

def read_chart_db(code: str, days: int, cache_date: str) -> dict | None:
    """chart_cache에서 차트 데이터 조회. API 응답 형식과 동일한 dict 반환."""
    conn = get_thread_conn()
    row = conn.execute(
        "SELECT * FROM chart_cache WHERE code=? AND days=? AND cache_date=?",
        (code, days, cache_date),
    ).fetchone()
    if not row:
        return None
    d = dict_from_row(row)
    if not d or not d.get("rsi_macd"):
        return None
    return {
        "code": d["code"], "name": d.get("name", code),
        "days": d["days"],
        "dates": d.get("dates", []),
        "open": d.get("open", []), "high": d.get("high", []),
        "low": d.get("low", []), "close": d.get("close", []),
        "volume": d.get("volume", []),
        "bollinger": d.get("bollinger", {}),
        "fibonacci": d.get("fibonacci", {}),
        "trendlines": d.get("trendlines", {}),
        "analysis": d.get("analysis", {}),
        "rsi_macd": d.get("rsi_macd", {}),
        "adx": d.get("adx", {}),
    }


def read_financial_db(code: str) -> dict | None:
    """financial 테이블에서 재무 데이터 조회. API 응답 형식과 동일."""
    conn = get_thread_conn()
    row = conn.execute("SELECT * FROM financial WHERE code=?", (code,)).fetchone()
    if not row:
        return None
    return dict_from_row(row)


def read_flow_db(code: str, latest_trading_date: str | None = None) -> dict | None:
    """flow_cache에서 수급 데이터 조회. API 응답 형식과 동일.

    latest_trading_date(YYYY-MM-DD)가 주어지면 마지막 dates 항목과 비교하여 stale 시 None 반환.
    호출자는 None을 받으면 HTTP 폴백으로 신선 데이터를 받아와야 함.
    """
    conn = get_thread_conn()
    row = conn.execute("SELECT * FROM flow_cache WHERE code=?", (code,)).fetchone()
    if not row:
        return None
    d = dict_from_row(row)
    if not d:
        return None
    if latest_trading_date:
        dates = d.get("dates") or []
        if dates and dates[-1] < latest_trading_date:
            return None
    return {
        "code": d.get("code", code),
        "name": d.get("name"),
        "dates": d.get("dates", []),
        "close": d.get("close", []),
        "foreign_shares": d.get("foreign_shares", []),
        "inst_shares": d.get("inst_shares", []),
        "foreign_value": d.get("foreign_value", []),
        "inst_value": d.get("inst_value", []),
        "foreign_sum_20": d.get("foreign_sum_20"),
        "inst_sum_20": d.get("inst_sum_20"),
        "source": d.get("source"),
        "fetched_at": d.get("fetched_at"),
    }


def read_yinfo_db(symbol: str) -> dict | None:
    """yinfo_cache에서 yfinance 메타 조회."""
    conn = get_thread_conn()
    row = conn.execute("SELECT * FROM yinfo_cache WHERE symbol=?", (symbol,)).fetchone()
    if not row:
        return None
    d = dict_from_row(row)
    return d.get("info") if d else None
