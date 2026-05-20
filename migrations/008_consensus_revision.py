"""
Step 5-1-A: 컨센서스 리비전 트래커 DB 스키마
- consensus_snapshot: 컨센서스 시계열 스냅샷
- revision_alerts: 리비전 시그널 알림 이력

(번호 008 — 007 은 kuvic→review 리네임 마이그레이션이 선점)

실행: python3 migrations/008_consensus_revision.py
"""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / 'db' / 'dashboard.db'


def migrate():
    if not DB_PATH.exists():
        print(f"❌ DB 파일 없음: {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    cur = conn.cursor()

    print(f"=== Step 5-1-A 마이그레이션 시작 ===\n")

    # 기존 테이블 확인 (충돌 회피)
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing = [r[0] for r in cur.fetchall()]

    for tbl in ['consensus_snapshot', 'revision_alerts']:
        status = '⚠️ 이미 존재' if tbl in existing else '✅ 신규 생성'
        print(f"  {tbl}: {status}")
    print()

    # ============================================================
    # 1. consensus_snapshot — 컨센서스 시계열 스냅샷
    # ============================================================
    cur.execute("""
    CREATE TABLE IF NOT EXISTS consensus_snapshot (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stock_code TEXT NOT NULL,
        period_type TEXT NOT NULL,
        period_year INTEGER NOT NULL,
        period_quarter INTEGER,

        snapshot_date TEXT NOT NULL,

        revenue_consensus REAL,
        op_income_consensus REAL,
        net_income_consensus REAL,
        eps_consensus REAL,

        analyst_count INTEGER,
        source TEXT DEFAULT 'naver',

        created_at TEXT DEFAULT CURRENT_TIMESTAMP,

        UNIQUE(stock_code, period_type, period_year, period_quarter, snapshot_date)
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_csnap_lookup
    ON consensus_snapshot(stock_code, period_type, period_year, period_quarter, snapshot_date DESC)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_csnap_recent
    ON consensus_snapshot(snapshot_date DESC)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_csnap_stock_date
    ON consensus_snapshot(stock_code, snapshot_date DESC)
    """)

    print("✅ consensus_snapshot + 3 indexes")

    # ============================================================
    # 2. revision_alerts — 리비전 시그널 알림 이력
    # ============================================================
    cur.execute("""
    CREATE TABLE IF NOT EXISTS revision_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stock_code TEXT NOT NULL,
        period_type TEXT NOT NULL,
        period_year INTEGER NOT NULL,
        period_quarter INTEGER,

        metric TEXT NOT NULL,
        revision_pct REAL NOT NULL,
        window_days INTEGER NOT NULL,

        baseline_value REAL,
        current_value REAL,
        baseline_date TEXT,
        current_date TEXT,

        signal TEXT NOT NULL,
        priority INTEGER DEFAULT 3,

        alert_sent INTEGER DEFAULT 0,
        sent_at TEXT,

        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_ralert_recent
    ON revision_alerts(created_at DESC)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_ralert_stock
    ON revision_alerts(stock_code, created_at DESC)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_ralert_signal
    ON revision_alerts(signal, alert_sent, created_at DESC)
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_ralert_dedupe
    ON revision_alerts(stock_code, period_type, period_year, period_quarter, metric, window_days, created_at DESC)
    """)

    print("✅ revision_alerts + 4 indexes")

    conn.commit()

    # ============================================================
    # 3. 검증
    # ============================================================
    print("\n=== 검증 ===\n")

    for tbl in ['consensus_snapshot', 'revision_alerts']:
        cur.execute(f"SELECT COUNT(*) FROM {tbl}")
        count = cur.fetchone()[0]

        cur.execute(f"PRAGMA index_list({tbl})")
        indexes = [r[1] for r in cur.fetchall() if not r[1].startswith('sqlite_')]

        print(f"  {tbl}:")
        print(f"    rows: {count}")
        print(f"    indexes: {len(indexes)} ({', '.join(indexes)})")

    conn.close()
    print("\n✅ Step 5-1-A 마이그레이션 완료\n")


if __name__ == '__main__':
    migrate()
