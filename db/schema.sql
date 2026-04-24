-- stock-dashboard SQLite 스키마
-- JSON 캐시 → SQLite 마이그레이션 (Tier 1)

-- 종목 마스터 (국내 + 미국 통합)
CREATE TABLE IF NOT EXISTS stocks (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    market TEXT NOT NULL DEFAULT '',
    sector TEXT,
    market_cap REAL,
    close REAL,
    change_pct REAL,
    volume_mn REAL,
    sectors_json TEXT,
    after_hours_price REAL,
    after_hours_change_pct REAL,
    after_hours_status TEXT,
    after_hours_time TEXT,
    is_etf INTEGER DEFAULT 0,
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_stocks_market ON stocks(market);
CREATE INDEX IF NOT EXISTS idx_stocks_sector ON stocks(sector);

-- 일봉 OHLCV 시계열
CREATE TABLE IF NOT EXISTS ohlcv (
    code TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    PRIMARY KEY (code, date)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_ohlcv_date ON ohlcv(date);

-- 기술적 지표 캐시 (차트 API 전체 결과)
-- 차트 캐시는 시계열 전체(rsi[], macd[] 등)를 JSON으로 보관
CREATE TABLE IF NOT EXISTS chart_cache (
    code TEXT NOT NULL,
    days INTEGER NOT NULL DEFAULT 180,
    cache_date TEXT NOT NULL,
    name TEXT,
    dates_json TEXT,
    open_json TEXT,
    high_json TEXT,
    low_json TEXT,
    close_json TEXT,
    volume_json TEXT,
    bollinger_json TEXT,
    fibonacci_json TEXT,
    trendlines_json TEXT,
    analysis_json TEXT,
    rsi_macd_json TEXT,
    adx_json TEXT,
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (code, days, cache_date)
) WITHOUT ROWID;

-- 재무 데이터
CREATE TABLE IF NOT EXISTS financial (
    code TEXT PRIMARY KEY,
    name TEXT,
    per REAL,
    eps REAL,
    estimate_per REAL,
    estimate_eps REAL,
    pbr REAL,
    bps REAL,
    dividend_yield REAL,
    market_cap REAL,
    market_cap_rank TEXT,
    industry_per REAL,
    shares_outstanding REAL,
    foreign_ratio REAL,
    annual_json TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

-- 수급 데이터 (캐시 단위: 종목별 20일 블록)
CREATE TABLE IF NOT EXISTS flow_cache (
    code TEXT PRIMARY KEY,
    name TEXT,
    dates_json TEXT,
    close_json TEXT,
    foreign_shares_json TEXT,
    inst_shares_json TEXT,
    foreign_value_json TEXT,
    inst_value_json TEXT,
    foreign_sum_20 REAL,
    inst_sum_20 REAL,
    source TEXT,
    fetched_at TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

-- yfinance 메타 (미국)
CREATE TABLE IF NOT EXISTS yinfo_cache (
    symbol TEXT PRIMARY KEY,
    info_json TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

-- 종목 발굴 결과
CREATE TABLE IF NOT EXISTS discover_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage INTEGER NOT NULL,
    market TEXT NOT NULL,
    total_scanned INTEGER,
    kospi_count INTEGER,
    kosdaq_count INTEGER,
    items_json TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_discover_market ON discover_results(market, updated_at DESC);

-- 알림 규칙
CREATE TABLE IF NOT EXISTS alert_rules (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    name TEXT,
    market TEXT,
    rule_type TEXT NOT NULL,
    value REAL,
    message TEXT,
    enabled INTEGER DEFAULT 1,
    triggered_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_alerts_code ON alert_rules(code);
CREATE INDEX IF NOT EXISTS idx_alerts_enabled ON alert_rules(enabled) WHERE enabled = 1;

-- DART 종목코드 → corp_code 매핑
CREATE TABLE IF NOT EXISTS dart_corp_map (
    stock_code TEXT PRIMARY KEY,
    corp_code TEXT NOT NULL,
    corp_name TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_dart_corp ON dart_corp_map(corp_code);

-- DART 공시 이력
CREATE TABLE IF NOT EXISTS disclosure_history (
    rcept_no TEXT PRIMARY KEY,
    corp_code TEXT,
    stock_code TEXT,
    corp_name TEXT,
    title TEXT,
    importance TEXT,
    keywords_json TEXT,
    rcept_dt TEXT,
    score INTEGER DEFAULT 0,
    alerted INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_disc_stock ON disclosure_history(stock_code);
CREATE INDEX IF NOT EXISTS idx_disc_date ON disclosure_history(rcept_dt DESC);
CREATE INDEX IF NOT EXISTS idx_disc_importance ON disclosure_history(importance, rcept_dt DESC);

-- 알림 발송 이력 (쿨다운 관리)
CREATE TABLE IF NOT EXISTS alert_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    detail TEXT,
    alerted_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_alert_hist_code ON alert_history(code, alert_type, alerted_at DESC);

-- 범용 캐시 (기타 JSON 파일 통합)
CREATE TABLE IF NOT EXISTS misc_cache (
    cache_key TEXT PRIMARY KEY,
    data_json TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);
