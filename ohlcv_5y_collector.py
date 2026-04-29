"""
5년 OHLCV 수집기 (KR 종목)
Step 4-2-B 전제 — PER/EV/EBITDA 5년 밴드 계산용

pykrx → ohlcv 테이블 (INSERT OR REPLACE)
- 5년 일봉 (약 종목당 1,250거래일)
- 한글 컬럼 → 영문 매핑
"""
import sys
import time
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

from pykrx import stock

DB_PATH = Path(__file__).parent / 'db' / 'dashboard.db'

# 한글 → 영문 매핑
COL_MAP = {'시가': 'open', '고가': 'high', '저가': 'low',
           '종가': 'close', '거래량': 'volume'}


def _get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def fetch_5y_ohlcv(stock_code: str, end_date: str | None = None) -> int:
    """단일 종목 5년 OHLCV 수집 + DB 저장.
    end_date: YYYYMMDD (None=오늘)
    Returns: 저장된 행 수
    """
    if end_date is None:
        end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.strptime(end_date, '%Y%m%d') - timedelta(days=5 * 365 + 30)).strftime('%Y%m%d')

    df = stock.get_market_ohlcv(start_date, end_date, stock_code)
    if df is None or df.empty:
        return 0

    # 한글 컬럼 → 영문
    df = df.rename(columns=COL_MAP)
    # 날짜 인덱스 → ISO 문자열
    df.index = df.index.strftime('%Y-%m-%d')

    rows = []
    for date, r in df.iterrows():
        rows.append((stock_code, date,
                     float(r['open']), float(r['high']), float(r['low']),
                     float(r['close']), float(r['volume'])))

    if not rows:
        return 0

    conn = _get_db()
    cur = conn.cursor()
    cur.executemany("""
        INSERT OR REPLACE INTO ohlcv (code, date, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, rows)
    conn.commit()
    conn.close()
    return len(rows)


def fetch_all_kr(codes: list[str] | None = None) -> dict:
    """전체 KR 종목 5년 수집 (직렬 — pykrx는 비동기 X)."""
    if codes is None:
        from valuechain import get_all_stocks_in_map
        codes, _ = get_all_stocks_in_map()

    print(f"📦 {len(codes)}종목 5년 OHLCV 수집 시작")
    t0 = time.time()
    success, failed = 0, 0
    total_rows = 0
    failures = []

    for i, code in enumerate(codes, 1):
        try:
            n = fetch_5y_ohlcv(code)
            total_rows += n
            if n > 0:
                success += 1
            else:
                failed += 1
                failures.append((code, 'no data'))
        except Exception as e:
            failed += 1
            failures.append((code, str(e)[:80]))

        if i % 10 == 0 or i == len(codes):
            elapsed = time.time() - t0
            print(f"  진행률: {i}/{len(codes)}  성공 {success}  실패 {failed}  ({elapsed:.1f}s)")

    elapsed = time.time() - t0
    print(f"\n✅ 완료: {success}/{len(codes)} 성공, {failed} 실패")
    print(f"📊 총 {total_rows:,}행 저장, {elapsed:.1f}초")
    if failures:
        print("❌ 실패 종목:")
        for c, msg in failures[:10]:
            print(f"  {c}: {msg}")

    return {'success': success, 'failed': failed, 'rows': total_rows, 'elapsed': elapsed}


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'all':
        fetch_all_kr()
    elif len(sys.argv) > 1:
        for code in sys.argv[1:]:
            print(f"테스트: {code}")
            n = fetch_5y_ohlcv(code)
            print(f"  → {n}행 저장")
    else:
        print("Usage:")
        print("  python3 ohlcv_5y_collector.py 007660    # 단일 테스트")
        print("  python3 ohlcv_5y_collector.py all       # 전체 KR 82종목")
