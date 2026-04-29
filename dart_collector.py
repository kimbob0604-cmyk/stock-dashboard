"""
DART 5년 분기 재무 비동기 수집기
Step 4-2-A

특징:
- asyncio + aiohttp 동시 20개 병렬
- 사업보고서 우선, 분기보고서로 보완
- tenacity 자동 재시도 (지수 백오프)
- fetch_progress 테이블로 진행률 추적
"""

import asyncio
import aiohttp
import os
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# .env 자동 로드 (CLI 단독 실행 시) — server.py와 동일 방식 (python-dotenv 미의존)
def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    try:
        for raw in path.read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, val = line.partition('=')
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
    except Exception as exc:
        print(f"[.env 로드 실패] {exc}")


_load_dotenv(Path(__file__).parent / '.env')

DB_PATH = Path(__file__).parent / 'db' / 'dashboard.db'
DART_API_KEY = os.environ.get('DART_API_KEY', '')
DART_BASE_URL = 'https://opendart.fss.or.kr/api'

# Step 4-2-A 보강: 차단 회피 보수적 설정
CONCURRENCY = 10
RATE_LIMIT_DELAY = 0.15  # 시간당 ~6,400건

# 글로벌 백오프 (차단 자동 감지)
GLOBAL_BACKOFF_THRESHOLD = 5  # 연속 실패 5건 → 백오프
GLOBAL_BACKOFF_DURATION  = 60  # 정지 시간 (초)

# 모듈 전역 상태 (런타임 초기화)
_consecutive_failures = 0
_backoff_until = 0.0
_backoff_lock = None  # asyncio.Lock


# ============================================================
# DART 계정 매핑
# 1순위: account_id (IFRS 표준 코드 — 가장 정확)
# 2순위: sj_div + account_nm 정확 매칭 (폴백)
# ============================================================
# IFRS 표준 account_id → 표준 컬럼명
DART_ACCOUNT_ID_MAP = {
    # 손익계산서 (CIS / IS) — 누적값
    'ifrs-full_Revenue':                   'revenue',
    'dart_OperatingIncomeLoss':            'operating_profit',
    'ifrs-full_ProfitLoss':                'net_income',
    # 재무상태표 (BS) — 시점값
    'ifrs-full_Assets':                    'total_assets',
    'ifrs-full_Liabilities':               'total_liabilities',
    'ifrs-full_Equity':                    'total_equity',
    'ifrs-full_CashAndCashEquivalents':    'cash_and_equivalents',
    'ifrs-full_ShorttermBorrowings':       'short_term_debt',
    'dart_ShortTermBorrowings':            'short_term_debt',
    'ifrs-full_LongtermBorrowings':        'long_term_debt',
    'dart_LongTermBorrowings':             'long_term_debt',
}

# 폴백: sj_div + account_nm 정확 매칭 (account_id가 '-표준계정코드 미사용-'인 경우)
# 키: 표준 컬럼 → (sj_div 화이트리스트, 정확 일치 계정명 리스트)
DART_FALLBACK_MAP = {
    'revenue':              (('CIS', 'IS'), ['매출액', '영업수익', '수익(매출액)']),
    'operating_profit':     (('CIS', 'IS'), ['영업이익', '영업이익(손실)']),
    'net_income':           (('CIS', 'IS'), ['당기순이익', '당기순이익(손실)',
                                              '반기순이익', '분기순이익']),
    'total_assets':         (('BS',),       ['자산총계']),
    'total_liabilities':    (('BS',),       ['부채총계']),
    'total_equity':         (('BS',),       ['자본총계', '자본총계(자기자본)']),
    'cash_and_equivalents': (('BS',),       ['현금및현금성자산', '현금 및 현금성자산']),
    'short_term_debt':      (('BS',),       ['단기차입금', '단기성차입금']),
    'long_term_debt':       (('BS',),       ['장기차입금', '사채']),
}

# 보고서 코드: 1Q=11013, 반기=11012, 3Q=11014, 사업보고서=11011
REPORT_CODES = {
    1: '11013',
    2: '11012',
    3: '11014',
    4: '11011',
}


def _get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def get_corp_code(stock_code: str) -> Optional[str]:
    """stock_code → DART corp_code"""
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("SELECT corp_code FROM dart_corp_map WHERE stock_code = ?", (stock_code,))
    row = cur.fetchone()
    conn.close()
    return row['corp_code'] if row else None


def _norm_nm(s: str) -> str:
    return (s or '').strip().replace(' ', '')


def _parse_amount(text) -> Optional[float]:
    """DART 금액 문자열 → float (음수 괄호 처리)"""
    if text is None or text == '' or text == '-':
        return None
    s = str(text).strip().replace(',', '').replace(' ', '')
    is_negative = s.startswith('(') and s.endswith(')')
    if is_negative:
        s = s[1:-1]
    try:
        v = float(s)
        return -v if is_negative else v
    except ValueError:
        return None


# ============================================================
# DART API 비동기 호출
# ============================================================

class DartFetchError(Exception):
    pass


@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=3, max=30),
    retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError,
                                    DartFetchError, ConnectionResetError))
)
async def _fetch_dart_json(session: aiohttp.ClientSession, endpoint: str, params: dict) -> dict:
    """DART API 호출 (재시도 + 글로벌 백오프)."""
    global _consecutive_failures, _backoff_until, _backoff_lock

    if _backoff_lock is None:
        _backoff_lock = asyncio.Lock()

    # 글로벌 백오프 대기 (다른 코루틴이 차단 감지 시 모두 대기)
    async with _backoff_lock:
        wait_time = _backoff_until - time.time()
    if wait_time > 0:
        await asyncio.sleep(wait_time)

    url = f'{DART_BASE_URL}/{endpoint}'
    full_params = {'crtfc_key': DART_API_KEY, **params}

    try:
        async with session.get(url, params=full_params,
                               timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                raise DartFetchError(f"HTTP {resp.status}")
            data = await resp.json(content_type=None)

            status = data.get('status')
            if status == '013':
                async with _backoff_lock:
                    _consecutive_failures = 0
                return {'list': []}
            if status not in ('000', None):
                raise DartFetchError(f"DART {status}: {data.get('message')}")

            # 성공 → 카운터 리셋
            async with _backoff_lock:
                _consecutive_failures = 0
            return data

    except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionResetError, DartFetchError):
        # 차단 의심 카운트
        async with _backoff_lock:
            _consecutive_failures += 1
            if _consecutive_failures >= GLOBAL_BACKOFF_THRESHOLD:
                _backoff_until = time.time() + GLOBAL_BACKOFF_DURATION
                print(f"  ⏸ 연속 실패 {_consecutive_failures}건 → {GLOBAL_BACKOFF_DURATION}초 백오프")
                _consecutive_failures = 0
        raise


async def fetch_financial_report(
    session: aiohttp.ClientSession,
    corp_code: str,
    year: int,
    quarter: int,
    sem: asyncio.Semaphore
) -> Optional[Dict]:
    """단일 분기 재무제표 조회 (CFS 우선 → OFS 폴백)"""
    async with sem:
        await asyncio.sleep(RATE_LIMIT_DELAY)

        report_code = REPORT_CODES[quarter]
        params = {
            'corp_code': corp_code,
            'bsns_year': str(year),
            'reprt_code': report_code,
            'fs_div': 'CFS',
        }

        try:
            data = await _fetch_dart_json(session, 'fnlttSinglAcntAll.json', params)
            items = data.get('list', [])

            if not items:
                params['fs_div'] = 'OFS'
                data = await _fetch_dart_json(session, 'fnlttSinglAcntAll.json', params)
                items = data.get('list', [])

            if not items:
                return None

            return {
                'corp_code': corp_code,
                'year': year,
                'quarter': quarter,
                'report_code': report_code,
                'items': items,
                'rcept_no': items[0].get('rcept_no') if items else None,
            }
        except Exception as e:
            print(f"  ⚠ {corp_code} {year}Q{quarter} 실패: {e}")
            return None


# ============================================================
# 누적 → 분기별 분리 + 파싱
# ============================================================

def parse_dart_items(items: List[dict]) -> Dict[str, float]:
    """DART 응답 → 표준 컬럼 dict.
    1순위: account_id (IFRS 표준)로 매핑.
    2순위: account_id가 미사용인 경우, sj_div + 정확 계정명 매칭.
    """
    result = {}

    # 1순위: account_id 직접 매핑
    for item in items:
        aid = item.get('account_id', '')
        if aid in DART_ACCOUNT_ID_MAP:
            std_key = DART_ACCOUNT_ID_MAP[aid]
            if std_key in result:
                continue
            amount = _parse_amount(item.get('thstrm_amount'))
            if amount is not None:
                result[std_key] = amount

    # 2순위: 폴백 (account_id 미사용 + sj_div 화이트리스트 + 정확 계정명)
    for item in items:
        aid = item.get('account_id', '')
        if aid in DART_ACCOUNT_ID_MAP:
            continue  # 이미 1순위에서 처리
        sj  = item.get('sj_div', '')
        nm_norm = _norm_nm(item.get('account_nm', ''))
        for std_key, (sj_allow, name_list) in DART_FALLBACK_MAP.items():
            if std_key in result:
                continue
            if sj not in sj_allow:
                continue
            if any(_norm_nm(n) == nm_norm for n in name_list):
                amount = _parse_amount(item.get('thstrm_amount'))
                if amount is not None:
                    result[std_key] = amount
                break

    return result


async def fetch_5y_quarterly(
    session: aiohttp.ClientSession,
    stock_code: str,
    sem: asyncio.Semaphore,
) -> Dict[tuple, dict]:
    """종목의 5년 × 4분기 = 20분기 재무 수집"""
    corp_code = get_corp_code(stock_code)
    if not corp_code:
        return {}

    current_year = datetime.now().year
    years = list(range(current_year - 5, current_year + 1))
    quarters = [1, 2, 3, 4]

    tasks = [
        fetch_financial_report(session, corp_code, y, q, sem)
        for y in years for q in quarters
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    quarterly = {}
    for r in results:
        if isinstance(r, Exception) or not r:
            continue
        key = (r['year'], r['quarter'])
        parsed = parse_dart_items(r['items'])
        if not parsed:
            continue
        parsed['rcept_no'] = r['rcept_no']
        parsed['report_type'] = r['report_code']
        quarterly[key] = parsed

    return quarterly


def split_cumulative_to_quarterly(quarterly: Dict[tuple, dict]) -> Dict[tuple, dict]:
    """DART의 thstrm_amount는 보고서 종류에 따라 의미가 다름:
      - 1Q/반기/3Q 보고서(11013/11012/11014): 이미 **분기 단독**값
      - 사업보고서(11011): **연간 누적**값
    → Q1, Q2, Q3는 그대로. Q4 = 연간 - (Q1 + Q2 + Q3)만 계산.
    BS 시점 항목(자산/부채/자본/현금/차입금)은 그대로 유지.
    """
    result = {}
    cumulative_keys = ['revenue', 'operating_profit', 'net_income']

    for (year, quarter), data in quarterly.items():
        if not data:
            continue
        new_data = data.copy()
        if quarter == 4:
            # 사업보고서 thstrm = 연간 누적 → 단독 = 연간 - (Q1+Q2+Q3)
            for k in cumulative_keys:
                annual = data.get(k)
                if annual is None:
                    continue
                vals = [(quarterly.get((year, q)) or {}).get(k) for q in (1, 2, 3)]
                if all(v is not None for v in vals):
                    new_data[k] = annual - sum(vals)
                # 일부 분기 누락 시 연간값 그대로 유지 (왜곡보다 차라리 명시적)
        result[(year, quarter)] = new_data

    return result


def calculate_derived_metrics(data: dict) -> dict:
    """OPM, NPM, EBITDA, net_debt"""
    revenue = data.get('revenue')
    op = data.get('operating_profit')
    ni = data.get('net_income')
    dep = data.get('depreciation', 0) or 0
    amort = data.get('amortization', 0) or 0
    cash = data.get('cash_and_equivalents', 0) or 0
    short_debt = data.get('short_term_debt', 0) or 0
    long_debt = data.get('long_term_debt', 0) or 0

    derived = {}
    if revenue and revenue > 0:
        if op is not None:
            derived['opm'] = (op / revenue) * 100
        if ni is not None:
            derived['npm'] = (ni / revenue) * 100

    if op is not None:
        if dep > 0 or amort > 0:
            derived['ebitda'] = op + dep + amort
        else:
            derived['ebitda'] = op * 1.15  # 폴백

    derived['net_debt'] = (short_debt + long_debt) - cash
    return derived


def save_quarterly_to_db(stock_code: str, quarterly: Dict[tuple, dict]) -> int:
    if not quarterly:
        return 0

    conn = _get_db()
    cur = conn.cursor()
    saved = 0

    for (year, quarter), data in quarterly.items():
        derived = calculate_derived_metrics(data)
        cur.execute("""
            INSERT OR REPLACE INTO financial_quarterly (
                stock_code, year, quarter, report_type,
                revenue, operating_profit, net_income,
                depreciation, amortization,
                total_assets, total_liabilities, total_equity,
                cash_and_equivalents, short_term_debt, long_term_debt,
                opm, npm, ebitda, net_debt,
                rcept_no, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
        """, (
            stock_code, year, quarter, data.get('report_type'),
            data.get('revenue'), data.get('operating_profit'), data.get('net_income'),
            data.get('depreciation'), data.get('amortization'),
            data.get('total_assets'), data.get('total_liabilities'), data.get('total_equity'),
            data.get('cash_and_equivalents'), data.get('short_term_debt'), data.get('long_term_debt'),
            derived.get('opm'), derived.get('npm'), derived.get('ebitda'), derived.get('net_debt'),
            data.get('rcept_no')
        ))
        saved += 1

    conn.commit()
    conn.close()
    return saved


def update_fetch_progress(stock_code: str, status: str, count: int = 0, error: str = None):
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO fetch_progress (stock_code, quarterly_status, quarterly_fetched_count, quarterly_last_attempt, quarterly_error)
        VALUES (?, ?, ?, datetime('now', 'localtime'), ?)
        ON CONFLICT(stock_code) DO UPDATE SET
            quarterly_status = excluded.quarterly_status,
            quarterly_fetched_count = excluded.quarterly_fetched_count,
            quarterly_last_attempt = excluded.quarterly_last_attempt,
            quarterly_error = excluded.quarterly_error,
            updated_at = datetime('now', 'localtime')
    """, (stock_code, status, count, error))
    conn.commit()
    conn.close()


# ============================================================
# 메인 진입점
# ============================================================

async def fetch_one_stock(
    session: aiohttp.ClientSession,
    stock_code: str,
    sem: asyncio.Semaphore,
) -> dict:
    t0 = time.time()
    update_fetch_progress(stock_code, 'IN_PROGRESS')

    try:
        quarterly = await fetch_5y_quarterly(session, stock_code, sem)
        if not quarterly:
            update_fetch_progress(stock_code, 'FAILED', 0, 'no data')
            return {'code': stock_code, 'status': 'FAILED', 'count': 0,
                    'elapsed': round(time.time() - t0, 2)}

        split = split_cumulative_to_quarterly(quarterly)
        saved = save_quarterly_to_db(stock_code, split)

        update_fetch_progress(stock_code, 'DONE', saved)
        return {'code': stock_code, 'status': 'DONE', 'count': saved,
                'elapsed': round(time.time() - t0, 2)}
    except Exception as e:
        update_fetch_progress(stock_code, 'FAILED', 0, str(e))
        return {'code': stock_code, 'status': 'FAILED', 'error': str(e),
                'elapsed': round(time.time() - t0, 2)}


async def fetch_all_stocks(stock_codes: List[str]) -> List[dict]:
    """다수 종목 동시 수집"""
    sem = asyncio.Semaphore(CONCURRENCY)

    print(f"📦 {len(stock_codes)}종목 수집 시작 (동시 {CONCURRENCY}개)")
    t0 = time.time()

    connector = aiohttp.TCPConnector(limit=CONCURRENCY * 2)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [fetch_one_stock(session, code, sem) for code in stock_codes]

        results = []
        completed = 0
        for coro in asyncio.as_completed(tasks):
            r = await coro
            results.append(r)
            completed += 1
            if completed % 10 == 0 or completed == len(stock_codes):
                elapsed = time.time() - t0
                print(f"  진행률: {completed}/{len(stock_codes)} ({elapsed:.1f}s)")

    elapsed = time.time() - t0
    success = sum(1 for r in results if r['status'] == 'DONE')
    failed = sum(1 for r in results if r['status'] == 'FAILED')
    total_quarters = sum(r.get('count', 0) for r in results)

    print(f"\n✅ 완료: {success}/{len(stock_codes)} 성공, {failed} 실패")
    print(f"📊 총 {total_quarters}분기 수집, {elapsed:.1f}초")
    return results


async def fetch_pending_only() -> List[dict]:
    """fetch_progress의 FAILED/PENDING + 미등록 종목만 재시도.
    DART 차단 해제 후 자동 회복용."""
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT stock_code FROM fetch_progress
        WHERE quarterly_status IN ('FAILED', 'PENDING')
        ORDER BY
            CASE WHEN quarterly_last_attempt IS NULL THEN 0 ELSE 1 END,
            quarterly_last_attempt ASC
    """)
    pending = [r['stock_code'] for r in cur.fetchall()]
    cur.execute("SELECT stock_code FROM fetch_progress")
    tracked = {r['stock_code'] for r in cur.fetchall()}
    conn.close()

    from valuechain import get_all_stocks_in_map
    kr_stocks, _ = get_all_stocks_in_map()
    untracked = [c for c in kr_stocks if c not in tracked]

    all_pending = list(set(pending + untracked))

    if not all_pending:
        print("✅ 재시도할 종목 없음 (모든 KR 종목 DONE)")
        return []

    print(f"🔄 재시도 대상: {len(all_pending)}종목")
    print(f"   - FAILED/PENDING: {len(pending)}")
    print(f"   - 미등록(untracked): {len(untracked)}")
    return await fetch_all_stocks(all_pending)


def fetch_all_kr_stocks_sync():
    from valuechain import get_all_stocks_in_map
    kr_stocks, _ = get_all_stocks_in_map()
    if not DART_API_KEY:
        raise RuntimeError("DART_API_KEY 환경변수 없음")
    return asyncio.run(fetch_all_stocks(kr_stocks))


# ============================================================
# CLI
# ============================================================

if __name__ == '__main__':
    import sys

    if not DART_API_KEY:
        print("❌ DART_API_KEY 환경변수 없음 (.env 확인)")
        sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1] == 'all':
        results = fetch_all_kr_stocks_sync()
        failed = [r for r in results if r['status'] == 'FAILED']
        if failed:
            print("\n❌ 실패 종목:")
            for r in failed[:20]:
                print(f"  {r['code']}: {r.get('error', 'unknown')}")
    elif len(sys.argv) > 1 and sys.argv[1] == 'retry':
        # FAILED/PENDING/미등록 종목만 재시도 (차단 해제 후 회복)
        results = asyncio.run(fetch_pending_only())
        if results:
            success = sum(1 for r in results if r['status'] == 'DONE')
            print(f"\n📊 재시도 결과: {success}/{len(results)} 성공")
            failed = [r for r in results if r['status'] == 'FAILED']
            if failed:
                print("❌ 여전히 실패:")
                for r in failed[:20]:
                    print(f"  {r['code']}: {r.get('error', 'unknown')}")
    elif len(sys.argv) > 1:
        codes = sys.argv[1:]
        print(f"테스트: {codes}")
        results = asyncio.run(fetch_all_stocks(codes))
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        print("Usage:")
        print("  python3 dart_collector.py 007660           # 단일 테스트")
        print("  python3 dart_collector.py 007660 267260    # 다중 테스트")
        print("  python3 dart_collector.py all              # 전체 KR 수집")
        print("  python3 dart_collector.py retry            # FAILED/PENDING/미등록만 재시도")
