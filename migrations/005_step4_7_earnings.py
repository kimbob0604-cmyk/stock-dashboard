"""
Step 4-7-A: 어닝 서프라이즈 알림 시스템 DB 스키마 (최종)

신규 테이블 5개 + alter 1개:
  1. index_universe         — 동적 유니버스 (출발: valuechain KR 82)
  2. consensus_quarterly    — 분기 컨센서스 (Naver tb_type1)
  3. earnings_actual        — 실제 발표 (Naver + DART 보정)
  4. earnings_surprise      — 서프라이즈 + 사후 알림
  5. earnings_alert_queue   — 발표 임박 사전 알림 큐
  6. alert_history_v2       — performance_3d/5d/7d + tracking_completed 컬럼 추가
"""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / 'db' / 'dashboard.db'


def migrate():
    if not DB_PATH.exists():
        print(f"❌ DB 파일 없음: {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()

    print(f"📂 DB: {DB_PATH}")
    print(f"📊 DB 크기: {DB_PATH.stat().st_size / 1024 / 1024:.1f} MB")

    # ================================================================
    # 1. index_universe
    # ================================================================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS index_universe (
            stock_code TEXT NOT NULL,
            source TEXT NOT NULL,
                -- 'VALUECHAIN_V2' (현재 사용)
                -- 'TOP_MARKET_CAP_KOSPI' (향후)
                -- 'TOP_MARKET_CAP_KOSDAQ' (향후)
            rank INTEGER,
            market_cap REAL,

            added_date TEXT NOT NULL,
            removed_date TEXT,
            is_active INTEGER DEFAULT 1,

            PRIMARY KEY (stock_code, source)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_iu_active ON index_universe(is_active, source)")
    print("✅ index_universe 생성")

    # ================================================================
    # 2. consensus_quarterly
    # ================================================================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS consensus_quarterly (
            stock_code TEXT NOT NULL,
            year INTEGER NOT NULL,
            quarter INTEGER NOT NULL,

            -- 매출액 (단위: 백만원)
            revenue_consensus REAL,
            revenue_high REAL,
            revenue_low REAL,

            -- 영업이익 (단위: 백만원)
            op_consensus REAL,
            op_high REAL,
            op_low REAL,

            -- 순이익 + EPS
            ni_consensus REAL,
            eps_consensus REAL,

            -- 메타
            analyst_count INTEGER,
            source TEXT,                   -- 'NAVER_TB_TYPE1' / 'INFERRED'
            collected_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,

            PRIMARY KEY (stock_code, year, quarter)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cq_yq ON consensus_quarterly(year, quarter)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cq_collected ON consensus_quarterly(collected_at DESC)")
    print("✅ consensus_quarterly 생성")

    # ================================================================
    # 3. earnings_actual
    # ================================================================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS earnings_actual (
            stock_code TEXT NOT NULL,
            year INTEGER NOT NULL,
            quarter INTEGER NOT NULL,

            source_type TEXT NOT NULL,
                -- 'DART_PRELIMINARY' (잠정실적)
                -- 'DART_QUARTERLY' (분기보고서)
                -- 'NAVER_TB_TYPE1' (네이버 표)
                -- 'CHANGE_30PCT' (30% 변동 공시)

            -- 출처 정보
            disclosure_id TEXT,            -- DART rcept_no (있을 때)
            disclosure_date TEXT,
            disclosure_title TEXT,

            -- 실적 (단위: 백만원)
            revenue REAL,
            operating_profit REAL,
            net_income REAL,
            eps REAL,

            -- 전년 동기 대비
            revenue_yoy_pct REAL,
            op_yoy_pct REAL,

            -- 메타
            is_consolidated INTEGER DEFAULT 1,
            parsed_at TEXT NOT NULL,
            raw_html_path TEXT,

            PRIMARY KEY (stock_code, year, quarter, source_type)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ea_yq ON earnings_actual(year, quarter)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ea_date ON earnings_actual(disclosure_date DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ea_source ON earnings_actual(source_type, disclosure_date DESC)")
    print("✅ earnings_actual 생성")

    # ================================================================
    # 4. earnings_surprise
    # ================================================================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS earnings_surprise (
            stock_code TEXT NOT NULL,
            year INTEGER NOT NULL,
            quarter INTEGER NOT NULL,

            -- 컨센서스
            revenue_consensus REAL,
            op_consensus REAL,

            -- 실제
            revenue_actual REAL,
            op_actual REAL,

            -- 서프라이즈 (%)
            revenue_surprise_pct REAL,
            op_surprise_pct REAL,

            -- 시그널
            signal TEXT,
                -- 'BEAT_BIG' / 'BEAT' / 'INLINE' / 'MISS' / 'MISS_BIG'
                -- 'TURNAROUND' / 'SHOCK' / 'REVENUE_SURPRISE'
            priority INTEGER,              -- 1(최고)~5(낮음)

            -- 알림
            alert_sent INTEGER DEFAULT 0,
            alert_sent_at TEXT,
            alert_message_preview TEXT,

            -- 분석 메타
            triggers TEXT,                 -- JSON: ['REVENUE_BEAT', 'OP_BEAT']
            note TEXT,

            calculated_at TEXT NOT NULL,

            PRIMARY KEY (stock_code, year, quarter)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_es_signal ON earnings_surprise(signal, priority)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_es_alert ON earnings_surprise(alert_sent, alert_sent_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_es_calculated ON earnings_surprise(calculated_at DESC)")
    print("✅ earnings_surprise 생성")

    # ================================================================
    # 5. earnings_alert_queue
    # ================================================================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS earnings_alert_queue (
            stock_code TEXT NOT NULL,
            year INTEGER NOT NULL,
            quarter INTEGER NOT NULL,

            -- 발표 일정
            expected_announce_date TEXT,    -- 예상 발표일 (이전 분기 패턴 기반)
            confidence TEXT,                 -- 'HIGH' / 'MEDIUM' / 'LOW'

            -- 사전 알림 (D-3, D-1)
            pre_alert_d3_sent INTEGER DEFAULT 0,
            pre_alert_d3_sent_at TEXT,
            pre_alert_d1_sent INTEGER DEFAULT 0,
            pre_alert_d1_sent_at TEXT,

            -- 사후 처리
            actual_announce_date TEXT,
            queue_resolved INTEGER DEFAULT 0,    -- earnings_actual 도착 시 1
            resolved_at TEXT,

            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,

            PRIMARY KEY (stock_code, year, quarter)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_eaq_pending ON earnings_alert_queue(queue_resolved, expected_announce_date)")
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_eaq_d1
        ON earnings_alert_queue(pre_alert_d1_sent, expected_announce_date)
        WHERE queue_resolved = 0
    """)
    print("✅ earnings_alert_queue 생성")

    # ================================================================
    # 6. alert_history_v2 — 컬럼 추가
    # ================================================================
    cur.execute("PRAGMA table_info(alert_history_v2)")
    existing_cols = {row[1] for row in cur.fetchall()}

    new_cols = {
        'performance_3d': 'REAL',
        'performance_5d': 'REAL',
        'performance_7d': 'REAL',
        'tracking_completed': 'INTEGER DEFAULT 0',
    }

    for col_name, col_type in new_cols.items():
        if col_name not in existing_cols:
            cur.execute(f"ALTER TABLE alert_history_v2 ADD COLUMN {col_name} {col_type}")
            print(f"✅ alert_history_v2.{col_name} 추가")
        else:
            print(f"⏭️ alert_history_v2.{col_name} 이미 존재")

    cur.execute("CREATE INDEX IF NOT EXISTS idx_ahv2_tracking ON alert_history_v2(tracking_completed, sent_at)")

    conn.commit()

    # ================================================================
    # 검증
    # ================================================================
    print("\n=== 마이그레이션 검증 ===")
    expected = ['index_universe', 'consensus_quarterly', 'earnings_actual',
                'earnings_surprise', 'earnings_alert_queue', 'alert_history_v2']
    for table in expected:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            n = cur.fetchone()[0]
            print(f"  {table}: {n} rows")
        except sqlite3.OperationalError as e:
            print(f"  {table}: ❌ {e}")

    cur.execute("PRAGMA table_info(alert_history_v2)")
    final_cols = [row[1] for row in cur.fetchall()]
    perf_cols = [c for c in final_cols if c.startswith('performance_') or c == 'tracking_completed']
    print(f"\n  alert_history_v2 컬럼 총 {len(final_cols)}개 (신규 {perf_cols})")

    # 인덱스 확인
    cur.execute("""
        SELECT COUNT(*) FROM sqlite_master
        WHERE type='index' AND (name LIKE 'idx_iu_%' OR name LIKE 'idx_cq_%'
                              OR name LIKE 'idx_ea_%' OR name LIKE 'idx_es_%'
                              OR name LIKE 'idx_eaq_%' OR name LIKE 'idx_ahv2_%')
    """)
    n_idx = cur.fetchone()[0]
    print(f"  Step 4-7-A 신규 인덱스: {n_idx}개")

    # 기존 데이터 보존 확인
    print("\n=== 기존 데이터 보존 ===")
    for t in ['alert_history_v2', 'consensus_estimate', 'financial_quarterly',
              'valuation_band', 'analysis_journal']:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            print(f"  {t}: {cur.fetchone()[0]} rows")
        except sqlite3.OperationalError:
            pass

    conn.close()


if __name__ == '__main__':
    try:
        migrate()
        print("\n✅ Step 4-7-A 마이그레이션 완료")
    except Exception as e:
        print(f"\n❌ 마이그레이션 실패: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
