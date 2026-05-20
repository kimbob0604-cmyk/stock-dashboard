"""
Step 4-5-1: 데이터 신선도 메타 시스템

설계 원칙:
1. 단일 진실 — DATA_SOURCE_CONFIG가 모든 신선도 정의
2. KST 정규화 — 모든 시간은 KST 기준
3. 카테고리별 다른 임계값 — realtime은 초, archive는 일
4. 거래일 인식 — 휴장이면 OHLCV 신선도 OK
5. 카테고리: realtime, frequent, hourly, daily, weekly, monthly, quarterly,
            ohlcv, manual, static
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, List, Dict
from dataclasses import dataclass

DB_PATH = Path(__file__).parent / "db" / "dashboard.db"
KST = timezone(timedelta(hours=9))

# DB 컨벤션:
#   stocks.market: KR = '' (빈 문자열), US = 'US'
#   discover_results.market: 'kr' / 'us' / 'all' (소문자)
#   ohlcv: market 컬럼 없음 → code GLOB '[0-9]{6}' 로 KR 식별
KR_CODE_GLOB = "[0-9][0-9][0-9][0-9][0-9][0-9]"


# ============================================================
# 1. 데이터 소스 설정 (단일 진실)
# ============================================================

DATA_SOURCE_CONFIG: Dict[str, dict] = {
    # ─── REALTIME ───
    "kis_realtime_price": {
        "category": "realtime",
        "name_kr": "한투 실시간 시세",
        "expected_seconds": 60,
        "thresholds": {"live": 60, "delay": 300},
        "source_module": "kis_api.py",
        "cron": "on-demand",
        "description": "한투 API 실시간 (호출 시)",
        "sql": None,
    },
    "naver_price": {
        "category": "realtime",
        "name_kr": "네이버 가격 (KR)",
        "expected_seconds": 1800,  # 9-15시 30분 간격
        "thresholds": {"live": 600, "delay": 3600},
        "source_module": "data_fetcher.py",
        "cron": "mon-fri 9-15:5,35 (장중 30분)",
        "description": "stocks 테이블 KR 가격",
        "sql": (
            "SELECT MAX(updated_at) FROM stocks "
            f"WHERE code GLOB '{KR_CODE_GLOB}'"
        ),
        "time_col": "updated_at",
    },

    # ─── FREQUENT ───
    "flow_cache_naver": {
        "category": "frequent",
        "name_kr": "수급 캐시 (Naver)",
        "expected_seconds": 300,
        "thresholds": {"live": 600, "delay": 1800},
        "source_module": "server.py:/api/flow",
        "cron": "장중 5분 (Stage2 KR)",
        "description": "fetched_at 기준 (Naver 응답 시각)",
        "sql": "SELECT MAX(fetched_at) FROM flow_cache",
        "time_col": "fetched_at",
    },
    "dart_disclosure": {
        "category": "frequent",
        "name_kr": "DART 공시 폴링",
        "expected_seconds": 60,
        "thresholds": {"live": 300, "delay": 1800},
        "source_module": "server.py:poll_dart_disclosures",
        "cron": "mon-fri 8-17 매분",
        "description": "disclosure_history (1분 폴링)",
        "sql": "SELECT MAX(created_at) FROM disclosure_history",
        "time_col": "created_at",
    },
    "earnings_pipeline": {
        "category": "frequent",
        "name_kr": "어닝 자동 파이프라인",
        "expected_seconds": 300,
        "thresholds": {"live": 600, "delay": 1800},
        "source_module": "earnings_signal_classifier.py",
        "cron": "interval 5분",
        "description": "earnings_actual.parsed_at",
        "sql": "SELECT MAX(parsed_at) FROM earnings_actual",
        "time_col": "parsed_at",
    },

    # ─── HOURLY ───
    "stocks_us_price": {
        "category": "hourly",
        "name_kr": "US 주가 (yfinance)",
        "expected_seconds": 3600,
        "thresholds": {"live": 4200, "delay": 14400},
        "source_module": "server.py:sync_us_market_to_db_from_cache",
        "cron": "interval 60분",
        "description": "stocks 테이블 US 가격",
        "sql": "SELECT MAX(updated_at) FROM stocks WHERE market = 'US'",
        "time_col": "updated_at",
    },
    "options_signal_us": {
        "category": "hourly",
        "name_kr": "미국 옵션 시그널",
        "expected_seconds": 86400,
        "thresholds": {"live": 90000, "delay": 172800},
        "source_module": "server.py:refresh_us_options_signal",
        "cron": "mon-fri 22:00",
        "description": "options_signal_*.json 파일 mtime",
        "sql": None,
        "file_glob": "cache/options_signal_*.json",
    },

    # ─── DAILY ───
    "discover_kr": {
        "category": "daily",
        "name_kr": "KR 종목 발굴 (Stage2)",
        "expected_seconds": 8 * 3600,
        "thresholds": {"live": 9 * 3600, "delay": 24 * 3600},
        "source_module": "server.py:_run_stage2_kr",
        "cron": "mon-fri 8:00, 16:00 + 장중 5분",
        "description": "discover_results market='kr'",
        "sql": "SELECT MAX(updated_at) FROM discover_results WHERE market = 'kr'",
        "time_col": "updated_at",
    },
    "discover_us": {
        "category": "daily",
        "name_kr": "US 종목 발굴 (Stage2)",
        "expected_seconds": 24 * 3600,
        "thresholds": {"live": 25 * 3600, "delay": 48 * 3600},
        "source_module": "server.py:_run_stage2_us",
        "cron": "tue-sat 5:50",
        "description": "discover_results market='us'",
        "sql": "SELECT MAX(updated_at) FROM discover_results WHERE market = 'us'",
        "time_col": "updated_at",
    },
    "naver_universe": {
        "category": "daily",
        "name_kr": "네이버 유니버스",
        "expected_seconds": 24 * 3600,
        "thresholds": {"live": 25 * 3600, "delay": 48 * 3600},
        "source_module": "server.py:_daily_naver_universe_build",
        "cron": "every day 8:00",
        "description": "cache/naver_universe_*.json 파일",
        "sql": None,
        "file_glob": "cache/naver_universe_2[0-9][0-9][0-9][0-9][0-9][0-9][0-9].json",
    },
    "us_market": {
        "category": "daily",
        "name_kr": "US 마켓 데이터",
        "expected_seconds": 24 * 3600,
        "thresholds": {"live": 25 * 3600, "delay": 48 * 3600},
        "source_module": "server.py:_daily_us_market_build",
        "cron": "tue-sat 5:50",
        "description": "cache/us_market_*.json 파일",
        "sql": None,
        "file_glob": "cache/us_market_2[0-9][0-9][0-9][0-9][0-9][0-9][0-9].json",
    },

    # ─── WEEKLY ───
    "consensus_quarterly": {
        "category": "weekly",
        "name_kr": "분기 컨센서스",
        "expected_seconds": 7 * 86400,
        "thresholds": {"live": 8 * 86400, "delay": 14 * 86400},
        "source_module": "consensus_quarterly_collector.py",
        "cron": "mon 6:00",
        "description": "consensus_quarterly.updated_at",
        "sql": "SELECT MAX(updated_at) FROM consensus_quarterly",
        "time_col": "updated_at",
    },

    # ─── MONTHLY ───
    "consensus_annual": {
        "category": "monthly",
        "name_kr": "연간 컨센서스",
        "expected_seconds": 30 * 86400,
        "thresholds": {"live": 35 * 86400, "delay": 60 * 86400},
        "source_module": "consensus_collector.py",
        "cron": "manual",
        "description": "consensus_estimate.fetched_at",
        "sql": "SELECT MAX(fetched_at) FROM consensus_estimate",
        "time_col": "fetched_at",
    },
    "valuation_band": {
        "category": "monthly",
        "name_kr": "5년 valuation 백분위",
        "expected_seconds": 30 * 86400,
        "thresholds": {"live": 35 * 86400, "delay": 60 * 86400},
        "source_module": "ohlcv_5y_collector.py",
        "cron": "manual",
        "description": "valuation_band.calculated_at",
        "sql": "SELECT MAX(calculated_at) FROM valuation_band",
        "time_col": "calculated_at",
    },

    # ─── QUARTERLY ───
    "financial_quarterly": {
        "category": "quarterly",
        "name_kr": "DART 5년 분기 재무",
        "expected_seconds": 90 * 86400,
        "thresholds": {"live": 100 * 86400, "delay": 120 * 86400},
        "source_module": "dart_collector.py",
        "cron": "manual (분기 발표 후)",
        "description": "financial_quarterly.fetched_at",
        "sql": "SELECT MAX(fetched_at) FROM financial_quarterly",
        "time_col": "fetched_at",
    },

    # ─── OHLCV (특수: MAX(date) + 거래일 인식) ───
    "ohlcv_kr": {
        "category": "ohlcv",
        "name_kr": "KR 일봉",
        "expected_seconds": 86400,
        "thresholds": {"live": 90000, "delay": 172800},
        "source_module": "krx_api.py / pykrx",
        "cron": "mon-fri 15:35",
        "description": "ohlcv MAX(date) — KR 코드(6자리 숫자)",
        "sql": f"SELECT MAX(date) FROM ohlcv WHERE code GLOB '{KR_CODE_GLOB}'",
        "special": "ohlcv_max_date",
    },
    "ohlcv_us": {
        "category": "ohlcv",
        "name_kr": "US 일봉",
        "expected_seconds": 86400,
        "thresholds": {"live": 90000, "delay": 172800},
        "source_module": "yfinance",
        "cron": "tue-sat 5:50",
        "description": "ohlcv MAX(date) — US 코드(영문 티커)",
        "sql": f"SELECT MAX(date) FROM ohlcv WHERE NOT (code GLOB '{KR_CODE_GLOB}')",
        "special": "ohlcv_max_date",
    },

    # ─── MANUAL (사용자 입력) ───
    "analysis_journal": {
        "category": "manual",
        "name_kr": "검토 분석 일지",
        "expected_seconds": None,
        "source_module": "analysis_journal_api.py",
        "cron": "user",
        "description": "사용자 수동 입력 — 라벨 X, 작성 시각만 표시",
        "sql": "SELECT MAX(updated_at) FROM analysis_journal",
        "time_col": "updated_at",
    },
    "trade_journal": {
        "category": "manual",
        "name_kr": "매매 일지",
        "expected_seconds": None,
        "source_module": "(legacy)",
        "cron": "user",
        "description": "사용자 매매 기록",
        "sql": "SELECT MAX(created_at) FROM trade_journal",
        "time_col": "created_at",
    },

    # ─── STATIC (변경 거의 없음) ───
    "dart_corp_map": {
        "category": "static",
        "name_kr": "DART 기업 매핑",
        "expected_seconds": 24 * 3600,
        "thresholds": {"live": 48 * 3600, "delay": 7 * 86400},
        "source_module": "server.py:init_dart_corp_map_db",
        "cron": "every day 3:00",
        "description": "기업명/코드 매핑 (거의 변경 없음)",
        "sql": "SELECT MAX(updated_at) FROM dart_corp_map",
        "time_col": "updated_at",
    },
}


# ============================================================
# 2. 신선도 결과 데이터 클래스
# ============================================================

@dataclass
class FreshnessResult:
    source: str
    label: str          # LIVE | DELAY | ARCHIVE | MANUAL | NO_DATA
    color: str          # green | yellow | gray | blue | red
    age_seconds: Optional[int]
    age_human: str
    last_updated: Optional[str]
    last_updated_kst: Optional[str]
    expected_next: Optional[str]
    category: str
    name_kr: str
    description: str

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "label": self.label,
            "color": self.color,
            "age_seconds": self.age_seconds,
            "age_human": self.age_human,
            "last_updated": self.last_updated,
            "last_updated_kst": self.last_updated_kst,
            "expected_next": self.expected_next,
            "category": self.category,
            "name_kr": self.name_kr,
            "description": self.description,
        }


# ============================================================
# 3. 헬퍼
# ============================================================

def _now_kst() -> datetime:
    return datetime.now(KST)


def _parse_time(time_str: Any) -> Optional[datetime]:
    """ISO 문자열 / YYYY-MM-DD / datetime → KST datetime"""
    if not time_str:
        return None
    if isinstance(time_str, datetime):
        return time_str.replace(tzinfo=KST) if time_str.tzinfo is None \
            else time_str.astimezone(KST)
    s = str(time_str).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return dt.astimezone(KST)
    except (ValueError, TypeError):
        pass
    # YYYY-MM-DD or YYYYMMDD
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            dt = datetime.strptime(s[: len(fmt.replace("%", ""))], fmt)
            return dt.replace(tzinfo=KST)
        except ValueError:
            continue
    return None


def _humanize_age(seconds: Optional[int]) -> str:
    if seconds is None:
        return "알 수 없음"
    if seconds < 60:
        return f"{seconds}초 전"
    if seconds < 3600:
        return f"{seconds // 60}분 전"
    if seconds < 86400:
        return f"{seconds // 3600}시간 전"
    days = seconds // 86400
    if days < 30:
        return f"{days}일 전"
    if days < 365:
        return f"{days // 30}개월 전"
    return f"{days // 365}년 전"


def _ohlcv_label(last_dt: datetime, now: datetime) -> tuple[str, str]:
    """OHLCV용 거래일 인식 라벨.
    last_dt = 마지막 일봉 date (자정 KST).
    KR/US 공통: 평일 어제 데이터까지 LIVE, 주말이면 금요일 데이터도 LIVE.
    """
    today = now.date()
    last_date = last_dt.date()
    days_diff = (today - last_date).days
    weekday = today.weekday()  # 0=월 ... 6=일

    # 토요일: 금요일(어제) 데이터 = 1일 차이 → LIVE
    # 일요일: 금요일 데이터 = 2일 차이 → LIVE
    # 월요일: 금요일 데이터 = 3일 차이 → LIVE (주말 후 첫 평일, 장 시작 전)
    if weekday == 5 and days_diff <= 1:
        return "LIVE", "green"
    if weekday == 6 and days_diff <= 2:
        return "LIVE", "green"
    if weekday == 0 and days_diff <= 3:
        return "LIVE", "green"

    # 평일: 어제까지 LIVE
    if days_diff <= 1:
        return "LIVE", "green"
    if days_diff <= 3:
        return "DELAY", "yellow"
    return "ARCHIVE", "gray"


def _query_db_max_time(sql: str, conn: sqlite3.Connection) -> Optional[str]:
    try:
        row = conn.execute(sql).fetchone()
        return row[0] if row and row[0] is not None else None
    except sqlite3.Error:
        return None


def _query_file_max_mtime(file_glob: str) -> Optional[str]:
    """cache/<glob> 파일 중 가장 최근 mtime → ISO 문자열"""
    base = Path(__file__).parent
    try:
        files = sorted(base.glob(file_glob), reverse=True)
        if not files:
            return None
        latest = max(files, key=lambda p: p.stat().st_mtime)
        ts = latest.stat().st_mtime
        return datetime.fromtimestamp(ts, KST).isoformat()
    except Exception:
        return None


# ============================================================
# 4. 핵심 함수
# ============================================================

def get_freshness(source: str,
                  conn: Optional[sqlite3.Connection] = None) -> FreshnessResult:
    """단일 소스의 신선도."""
    config = DATA_SOURCE_CONFIG.get(source)
    if not config:
        return FreshnessResult(
            source=source, label="NO_DATA", color="red",
            age_seconds=None, age_human="등록 안 됨",
            last_updated=None, last_updated_kst=None,
            expected_next=None, category="unknown",
            name_kr=source, description="등록되지 않은 소스",
        )

    close_conn = False
    if conn is None:
        conn = sqlite3.connect(str(DB_PATH), timeout=30)
        close_conn = True

    try:
        # 1) 마지막 갱신 raw 값
        last_raw: Optional[str] = None
        if config.get("sql"):
            last_raw = _query_db_max_time(config["sql"], conn)
        elif config.get("file_glob"):
            last_raw = _query_file_max_mtime(config["file_glob"])

        last_dt = _parse_time(last_raw)
        category = config["category"]
        name_kr = config.get("name_kr", source)
        description = config.get("description", "")

        # 2) 데이터 없음
        if last_dt is None:
            return FreshnessResult(
                source=source, label="NO_DATA", color="red",
                age_seconds=None, age_human="데이터 없음",
                last_updated=None, last_updated_kst=None,
                expected_next=None, category=category,
                name_kr=name_kr, description=description,
            )

        now = _now_kst()
        age_seconds = max(0, int((now - last_dt).total_seconds()))

        # 3) MANUAL 카테고리
        if category == "manual":
            return FreshnessResult(
                source=source, label="MANUAL", color="blue",
                age_seconds=age_seconds,
                age_human=_humanize_age(age_seconds),
                last_updated=last_raw,
                last_updated_kst=last_dt.strftime("%Y-%m-%d %H:%M:%S KST"),
                expected_next=None,
                category=category, name_kr=name_kr, description=description,
            )

        # 4) OHLCV 거래일 인식
        if config.get("special") == "ohlcv_max_date":
            label, color = _ohlcv_label(last_dt, now)
            return FreshnessResult(
                source=source, label=label, color=color,
                age_seconds=age_seconds,
                age_human=_humanize_age(age_seconds),
                last_updated=last_raw,
                last_updated_kst=last_dt.strftime("%Y-%m-%d (date)"),
                expected_next=None,
                category=category, name_kr=name_kr, description=description,
            )

        # 5) 일반 임계값
        thresholds = config.get("thresholds", {"live": 3600, "delay": 86400})
        if age_seconds <= thresholds["live"]:
            label, color = "LIVE", "green"
        elif age_seconds <= thresholds["delay"]:
            label, color = "DELAY", "yellow"
        else:
            label, color = "ARCHIVE", "gray"

        # 6) 다음 예상 갱신
        expected_next = None
        exp = config.get("expected_seconds")
        if exp:
            next_dt = last_dt + timedelta(seconds=exp)
            expected_next = next_dt.strftime("%Y-%m-%d %H:%M KST")

        return FreshnessResult(
            source=source, label=label, color=color,
            age_seconds=age_seconds,
            age_human=_humanize_age(age_seconds),
            last_updated=last_raw,
            last_updated_kst=last_dt.strftime("%Y-%m-%d %H:%M:%S KST"),
            expected_next=expected_next,
            category=category, name_kr=name_kr, description=description,
        )
    finally:
        if close_conn:
            conn.close()


def get_all_sources_status() -> List[FreshnessResult]:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    try:
        return [get_freshness(s, conn) for s in DATA_SOURCE_CONFIG.keys()]
    finally:
        conn.close()


def get_freshness_summary() -> dict:
    all_status = get_all_sources_status()
    by_label = {"LIVE": 0, "DELAY": 0, "ARCHIVE": 0, "MANUAL": 0, "NO_DATA": 0}
    by_category: dict = {}
    issues: list = []

    for r in all_status:
        by_label[r.label] = by_label.get(r.label, 0) + 1
        by_category.setdefault(r.category, []).append(r.to_dict())
        if r.label in ("DELAY", "ARCHIVE", "NO_DATA") and r.category != "manual":
            issues.append({
                "source": r.source,
                "name_kr": r.name_kr,
                "label": r.label,
                "age_human": r.age_human,
                "category": r.category,
            })

    total = sum(by_label.values())
    if total > 0:
        live_or_manual = by_label["LIVE"] + by_label["MANUAL"]
        health_score = int(round(live_or_manual / total * 100))
    else:
        health_score = 0

    return {
        "health_score": health_score,
        "by_label": by_label,
        "by_category": by_category,
        "issues": issues,
        "total_sources": total,
        "checked_at": _now_kst().strftime("%Y-%m-%d %H:%M:%S KST"),
    }


# ============================================================
# 5. CLI
# ============================================================

if __name__ == "__main__":
    import sys
    import json

    args = sys.argv[1:]
    if not args:
        print("Usage:")
        print("  python3 data_freshness.py keys              # 등록된 소스 목록")
        print("  python3 data_freshness.py source <key>      # 단일 소스 상세")
        print("  python3 data_freshness.py all               # 전체 표")
        print("  python3 data_freshness.py summary           # 헬스 요약")
        print("  python3 data_freshness.py issues            # 문제 소스만")
        print("  python3 data_freshness.py json              # 전체 JSON")
        sys.exit(0)

    cmd = args[0]
    EMOJI = {"LIVE": "🟢", "DELAY": "🟡", "ARCHIVE": "⚪", "MANUAL": "👤", "NO_DATA": "🔴"}
    CAT_ORDER = ["realtime", "frequent", "hourly", "daily", "weekly",
                 "monthly", "quarterly", "ohlcv", "manual", "static"]

    if cmd == "keys":
        for k, v in DATA_SOURCE_CONFIG.items():
            print(f"  {k:<28} [{v['category']:<10}] {v.get('name_kr', '')}")
        print(f"\n총 {len(DATA_SOURCE_CONFIG)}개")

    elif cmd == "source":
        if len(args) < 2:
            print("Usage: python3 data_freshness.py source <key>")
            sys.exit(1)
        result = get_freshness(args[1])
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))

    elif cmd == "all":
        results = get_all_sources_status()
        results_sorted = sorted(
            results,
            key=lambda r: (
                CAT_ORDER.index(r.category) if r.category in CAT_ORDER else 99,
                r.source,
            ),
        )
        print(f"\n=== 데이터 소스 신선도 ({len(results)}) ===\n")
        current_cat = None
        for r in results_sorted:
            if r.category != current_cat:
                print(f"\n[{r.category.upper()}]")
                current_cat = r.category
            label_str = f"{EMOJI.get(r.label, '?')} {r.label}"
            last = r.last_updated_kst[:19] if r.last_updated_kst else "N/A"
            print(f"  {r.source:<26} {label_str:<14} {r.age_human:<14} "
                  f"{last:<22} {r.name_kr}")

    elif cmd == "summary":
        s = get_freshness_summary()
        print(f"\n=== 헬스 요약 ===\n")
        print(f"전체 점수: {s['health_score']}/100")
        print(f"검사 시각: {s['checked_at']}")
        print(f"\n[라벨 분포]")
        for label, count in s["by_label"].items():
            if count > 0:
                print(f"  {EMOJI.get(label, '?')} {label:<10}: {count}")
        print(f"\n[문제 ({len(s['issues'])}건)]")
        for it in s["issues"]:
            print(f"  ⚠ {it['name_kr']} ({it['source']}) "
                  f"[{it['category']}]: {it['label']} - {it['age_human']}")

    elif cmd == "issues":
        s = get_freshness_summary()
        if not s["issues"]:
            print("✅ 모든 소스 정상")
        else:
            print(f"\n=== 문제 발견 ({len(s['issues'])}건) ===\n")
            for it in s["issues"]:
                print(f"⚠ {it['name_kr']}")
                print(f"   source:   {it['source']}")
                print(f"   category: {it['category']}")
                print(f"   label:    {it['label']}")
                print(f"   age:      {it['age_human']}")
                print()

    elif cmd == "json":
        data = [r.to_dict() for r in get_all_sources_status()]
        print(json.dumps(data, indent=2, ensure_ascii=False))

    else:
        print(f"알 수 없는 명령: {cmd}")
        sys.exit(1)
