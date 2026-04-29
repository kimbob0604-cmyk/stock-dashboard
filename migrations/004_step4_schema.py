"""
Step 4-1: 검증시트 + 알림 + 재무캐시 + 밸류에이션밴드 스키마
실행: python3 migrations/004_step4_schema.py

[수정사항 v2]
- DB 경로: db/dashboard.db
- alert_history 충돌 회피: alert_history_v2 사용
- consensus_estimate에 'FINANCIAL_TABLE' source 추가
"""
import sqlite3
from pathlib import Path
import sys

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

    # 사전 점검: 충돌 테이블 확인
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='alert_history'")
    if cur.fetchone():
        cur.execute("SELECT COUNT(*) FROM alert_history")
        legacy_count = cur.fetchone()[0]
        print(f"⚠️  기존 alert_history 보존 ({legacy_count} rows). 신규는 alert_history_v2로 생성.")

    # ============================================================
    # 1. analysis_journal — 검증 시트 (Gist 백업 대상)
    # ============================================================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS analysis_journal (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          stock_code TEXT NOT NULL,
          stock_name TEXT,
          analysis_date TEXT NOT NULL,

          -- Step 1: 밸류체인 위치 (자동)
          theme_id TEXT,
          layer_id TEXT,
          segment_id TEXT,
          is_bottleneck INTEGER DEFAULT 0,

          -- Step 2: 개별 요인 (자동 + 수동 덮어쓰기)
          current_price REAL,
          return_52w REAL,
          fwd_per REAL,
          fwd_per_band_pct REAL,
          ev_ebitda REAL,
          ev_ebitda_band_pct REAL,
          market_cap REAL,
          opm_estimate REAL,
          opm_source TEXT,

          -- Step 4: TAM 모델링
          bear_eps REAL, bear_eps_source TEXT,
          bear_per REAL, bear_per_source TEXT,
          bear_tp REAL,

          base_eps REAL, base_eps_source TEXT,
          base_per REAL, base_per_source TEXT,
          base_tp REAL,

          bull_eps REAL, bull_eps_source TEXT,
          bull_per REAL, bull_per_source TEXT,
          bull_tp REAL,

          -- Step 5: 반영도
          reflection_score REAL,
          reflection_label TEXT,

          -- 메모/결론
          memo TEXT,
          tags TEXT,
          conclusion TEXT,

          created_at TEXT DEFAULT (datetime('now', 'localtime')),
          updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_journal_stock ON analysis_journal(stock_code)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_journal_date ON analysis_journal(analysis_date DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_journal_stock_date ON analysis_journal(stock_code, analysis_date DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_journal_conclusion ON analysis_journal(conclusion)")
    print("✅ analysis_journal created")

    # ============================================================
    # 2. alert_history_v2 — Step 4 신규 알림 이력
    # ============================================================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS alert_history_v2 (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          stock_code TEXT NOT NULL,
          stock_name TEXT,
          alert_type TEXT NOT NULL,
          alert_score REAL,

          theme_id TEXT,
          layer_id TEXT,
          segment_id TEXT,
          is_bottleneck INTEGER DEFAULT 0,

          payload TEXT,

          sent_at TEXT DEFAULT (datetime('now', 'localtime')),
          sent_status TEXT DEFAULT 'PENDING',
          telegram_message_id TEXT,
          error_message TEXT,

          price_at_alert REAL,
          performance_7d REAL,
          performance_30d REAL,
          tracked_at TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_alert2_stock_type ON alert_history_v2(stock_code, alert_type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_alert2_sent ON alert_history_v2(sent_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_alert2_dedupe ON alert_history_v2(stock_code, alert_type, sent_at DESC)")
    print("✅ alert_history_v2 created (기존 alert_history와 분리)")

    # ============================================================
    # 3. financial_quarterly — DART 5년 분기 캐시
    # ============================================================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS financial_quarterly (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          stock_code TEXT NOT NULL,
          year INTEGER NOT NULL,
          quarter INTEGER NOT NULL,
          report_type TEXT,

          revenue REAL,
          operating_profit REAL,
          net_income REAL,

          depreciation REAL,
          amortization REAL,

          total_assets REAL,
          total_liabilities REAL,
          total_equity REAL,
          cash_and_equivalents REAL,
          short_term_debt REAL,
          long_term_debt REAL,

          shares_outstanding REAL,

          opm REAL,
          npm REAL,
          eps REAL,
          ebitda REAL,
          net_debt REAL,

          rcept_no TEXT,
          fetched_at TEXT DEFAULT (datetime('now', 'localtime')),
          UNIQUE(stock_code, year, quarter)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_finq_stock_yq ON financial_quarterly(stock_code, year DESC, quarter DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_finq_fetched ON financial_quarterly(fetched_at)")
    print("✅ financial_quarterly created")

    # ============================================================
    # 4. valuation_band — 5년 PER/EV-EBITDA 밴드
    # ============================================================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS valuation_band (
          stock_code TEXT PRIMARY KEY,

          per_p10 REAL, per_p25 REAL, per_p50 REAL, per_p75 REAL, per_p90 REAL,
          per_min REAL, per_max REAL,
          per_current REAL, per_current_pct REAL,

          ev_ebitda_p10 REAL, ev_ebitda_p25 REAL, ev_ebitda_p50 REAL,
          ev_ebitda_p75 REAL, ev_ebitda_p90 REAL,
          ev_ebitda_min REAL, ev_ebitda_max REAL,
          ev_ebitda_current REAL, ev_ebitda_current_pct REAL,

          pbr_p25 REAL, pbr_p50 REAL, pbr_p75 REAL,
          pbr_current REAL, pbr_current_pct REAL,

          quarters_covered INTEGER,
          calculated_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_valband_calculated ON valuation_band(calculated_at)")
    print("✅ valuation_band created")

    # ============================================================
    # 5. consensus_estimate — Fwd EPS / 목표주가
    # ============================================================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS consensus_estimate (
          stock_code TEXT PRIMARY KEY,

          fwd_eps_1y REAL,
          fwd_eps_1y_high REAL, fwd_eps_1y_low REAL,
          fwd_eps_1y_count INTEGER,

          fwd_eps_2y REAL,
          fwd_revenue_1y REAL,

          target_price_avg REAL,
          target_price_high REAL, target_price_low REAL,
          target_price_count INTEGER,

          rec_buy_count INTEGER,
          rec_hold_count INTEGER,
          rec_sell_count INTEGER,

          source TEXT,  -- 'NAVER' | 'YFINANCE' | 'FINNHUB' | 'FINANCIAL_TABLE'
          fetched_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_consensus_fetched ON consensus_estimate(fetched_at)")
    print("✅ consensus_estimate created")

    # ============================================================
    # 6. dart_disclosure_overhang — 오버행 물량
    # ============================================================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS dart_disclosure_overhang (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          stock_code TEXT NOT NULL,
          rcept_no TEXT NOT NULL,

          report_nm TEXT,
          overhang_type TEXT,

          issue_date TEXT,
          maturity_date TEXT,
          exercise_start_date TEXT,
          exercise_end_date TEXT,

          face_value REAL,
          conversion_price REAL,
          potential_shares REAL,
          potential_dilution_pct REAL,

          status TEXT DEFAULT 'ACTIVE',

          fetched_at TEXT DEFAULT (datetime('now', 'localtime')),
          UNIQUE(stock_code, rcept_no, overhang_type)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_overhang_stock ON dart_disclosure_overhang(stock_code, status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_overhang_active ON dart_disclosure_overhang(status, exercise_end_date)")
    print("✅ dart_disclosure_overhang created")

    # ============================================================
    # 7. fetch_progress — 수집 진행 상황
    # ============================================================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS fetch_progress (
          stock_code TEXT PRIMARY KEY,

          quarterly_last_year INTEGER,
          quarterly_last_quarter INTEGER,
          quarterly_fetched_count INTEGER DEFAULT 0,
          quarterly_target_count INTEGER DEFAULT 20,
          quarterly_status TEXT DEFAULT 'PENDING',
          quarterly_last_attempt TEXT,
          quarterly_error TEXT,

          valuation_band_status TEXT DEFAULT 'PENDING',
          valuation_band_calculated_at TEXT,

          consensus_status TEXT DEFAULT 'PENDING',
          consensus_last_fetch TEXT,

          overhang_status TEXT DEFAULT 'PENDING',
          overhang_last_fetch TEXT,

          updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_progress_quarterly ON fetch_progress(quarterly_status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_progress_valband ON fetch_progress(valuation_band_status)")
    print("✅ fetch_progress created")

    conn.commit()

    # ============================================================
    # 검증
    # ============================================================
    print("\n=== 검증 ===")
    expected = ['analysis_journal', 'alert_history_v2', 'financial_quarterly',
                'valuation_band', 'consensus_estimate', 'dart_disclosure_overhang',
                'fetch_progress']
    cur.execute(
        f"SELECT name FROM sqlite_master WHERE type='table' AND name IN ({','.join(['?']*len(expected))})",
        expected,
    )
    tables = [r[0] for r in cur.fetchall()]
    print(f"신규 테이블 7개 중 생성됨: {len(tables)}/7")
    for t in expected:
        if t in tables:
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            print(f"  ✅ {t}: {cur.fetchone()[0]} rows")
        else:
            print(f"  ❌ {t}: 누락")

    cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'")
    print(f"\n인덱스 총 {cur.fetchone()[0]}개 (기존 + 신규 통합)")

    # 기존 alert_history 영향 없는지 확인
    cur.execute("SELECT COUNT(*) FROM alert_history")
    print(f"\n기존 alert_history 보존: {cur.fetchone()[0]} rows ✅")

    conn.close()
    print("\n✅ 마이그레이션 완료")


if __name__ == '__main__':
    try:
        migrate()
    except Exception as e:
        print(f"❌ 마이그레이션 실패: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
