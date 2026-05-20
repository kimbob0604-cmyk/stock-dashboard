"""
Step 5-1-B: 컨센서스 스냅샷 수집기

매일 18:00 (장마감 후) 실행:
1. 확장 유니버스 350종목 선정 (시총 상위 비-ETF + valuechain KR, union)
2. 각 종목 fresh fetch:
   - collect_one(code)               → consensus_quarterly 갱신 (분기 추정)
   - collect_consensus_for_stock(code) → consensus_estimate 갱신 (forward EPS)
3. 갱신된 테이블에서 읽어 consensus_snapshot 에 INSERT OR IGNORE
   - QUARTERLY: consensus_quarterly (year/quarter, 매출/영업익/순익/EPS)
   - NTM:       consensus_estimate.fwd_eps_1y (period_year=올해, quarter=0)

설계 메모 (Step 5-1-B 확정):
- 기존 수집기는 "fetch→DB저장, 상태 반환" 구조 → 파싱 재구현 없이 테이블에서 read-back.
- KR 연간(매출/영업익/순익) 컨센서스는 수집되는 원천이 없어 ANNUAL 은 수집하지 않음.
  연간 계열 신호는 NTM forward EPS 로 대체.
- cross-connection 가시성 이슈 회피: 종목별로 새 커넥션을 열어 collector commit 이후 read.
"""

import sqlite3
import sys
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / 'db' / 'dashboard.db'

# 기존 수집기 재사용 (실제 함수명에 맞춰 alias)
sys.path.insert(0, str(BASE_DIR))
from consensus_quarterly_collector import collect_one as collect_quarterly_one
from consensus_collector import collect_consensus_for_stock as collect_forward_one

logger = logging.getLogger(__name__)

NULL_QUARTER_SENTINEL = 0  # 연간/NTM 은 quarter=0 으로 저장 (NULL 회피)


# ============================================================
# 1. 확장 유니버스 선정
# ============================================================

def get_extended_universe(target_size: int = 350) -> List[Dict]:
    """
    확장 유니버스 = 시총 상위(비-ETF) target_size + valuechain KR 매핑 종목 union.

    Returns:
        [{'code': '005930', 'name': '삼성전자', 'market_cap': ..., 'source': 'mcap_top'|'valuechain'|'both'}]
    """
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 1. 시총 상위 (ETF 제외)
    cur.execute("""
        SELECT code, name, market_cap
        FROM stocks
        WHERE code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND market_cap > 0
          AND name IS NOT NULL
          AND COALESCE(is_etf, 0) = 0
        ORDER BY market_cap DESC
        LIMIT ?
    """, (target_size,))
    mcap_stocks = [dict(r) for r in cur.fetchall()]

    # 2. valuechain 매핑 KR 종목 (헬퍼 사용)
    vc_codes = set()
    try:
        from valuechain import get_all_stocks_in_map
        kr_codes, _ = get_all_stocks_in_map()
        vc_codes = {c for c in kr_codes if isinstance(c, str) and len(c) == 6 and c.isdigit()}
    except Exception as e:
        logger.warning(f"[universe] valuechain 로드 실패: {e}")

    vc_stocks = []
    if vc_codes:
        placeholders = ','.join('?' * len(vc_codes))
        cur.execute(f"""
            SELECT code, name, market_cap
            FROM stocks
            WHERE code IN ({placeholders})
              AND code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        """, list(vc_codes))
        vc_stocks = [dict(r) for r in cur.fetchall()]

    conn.close()

    # 3. Union (중복 제거)
    universe_map: Dict[str, Dict] = {}
    for s in mcap_stocks:
        universe_map[s['code']] = {**s, 'source': 'mcap_top'}
    for s in vc_stocks:
        if s['code'] not in universe_map:
            universe_map[s['code']] = {**s, 'source': 'valuechain'}
        else:
            universe_map[s['code']]['source'] = 'both'

    universe = list(universe_map.values())
    logger.info(f"[universe] {len(mcap_stocks)} mcap + {len(vc_stocks)} vc → {len(universe)} unique")
    return universe


# ============================================================
# 2. 스냅샷 저장
# ============================================================

def save_snapshot(
    cur: sqlite3.Cursor,
    stock_code: str,
    period_type: str,
    period_year: int,
    period_quarter: Optional[int],
    snapshot_date: str,
    data: Dict
) -> bool:
    """consensus_snapshot 에 INSERT OR IGNORE. 신규 삽입 시 True."""
    pq = NULL_QUARTER_SENTINEL if period_quarter is None else int(period_quarter)
    try:
        cur.execute("""
            INSERT OR IGNORE INTO consensus_snapshot (
                stock_code, period_type, period_year, period_quarter,
                snapshot_date,
                revenue_consensus, op_income_consensus,
                net_income_consensus, eps_consensus,
                analyst_count, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            stock_code, period_type, int(period_year), pq, snapshot_date,
            data.get('revenue'), data.get('op_income'),
            data.get('net_income'), data.get('eps'),
            data.get('analyst_count'), data.get('source', 'naver'),
        ))
        return cur.rowcount > 0
    except Exception as e:
        logger.error(f"[snapshot] {stock_code} {period_type} {period_year}-Q{pq} 저장 실패: {e}")
        return False


# ============================================================
# 3. 종목별 스냅샷 수집 (fresh fetch → read-back → snapshot)
# ============================================================

def collect_stock_snapshots(stock_code: str, snapshot_date: str) -> Dict:
    """
    한 종목: 분기/NTM 컨센서스 fresh fetch 후 테이블에서 읽어 스냅샷 저장.

    Returns:
        {'quarterly_new': int, 'ntm_new': int, 'errors': [str]}
    """
    stats = {'quarterly_new': 0, 'ntm_new': 0, 'errors': []}

    # ----- 1) fresh fetch (기존 수집기, 각자 별도 커넥션으로 commit) -----
    try:
        collect_quarterly_one(stock_code)
    except Exception as e:
        stats['errors'].append(f"quarterly_fetch: {str(e)[:80]}")
        logger.warning(f"[snapshot] {stock_code} 분기 수집 실패: {e}")

    fwd_eps_from_result = None
    try:
        res = collect_forward_one(stock_code) or {}
        fwd_eps_from_result = res.get('fwd_eps_1y')
    except Exception as e:
        stats['errors'].append(f"forward_fetch: {str(e)[:80]}")
        logger.warning(f"[snapshot] {stock_code} forward 수집 실패: {e}")

    # ----- 2) read-back + snapshot (collector commit 이후 새 커넥션) -----
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # QUARTERLY ← consensus_quarterly
    try:
        cur.execute("""
            SELECT year, quarter, revenue_consensus, op_consensus,
                   ni_consensus, eps_consensus, analyst_count
            FROM consensus_quarterly
            WHERE stock_code = ?
        """, (stock_code,))
        for row in cur.fetchall():
            data = {
                'revenue': row['revenue_consensus'],
                'op_income': row['op_consensus'],
                'net_income': row['ni_consensus'],
                'eps': row['eps_consensus'],
                'analyst_count': row['analyst_count'],
                'source': 'naver',
            }
            if save_snapshot(cur, stock_code, 'QUARTERLY',
                             row['year'], row['quarter'], snapshot_date, data):
                stats['quarterly_new'] += 1
    except Exception as e:
        stats['errors'].append(f"quarterly_snap: {str(e)[:80]}")

    # NTM ← consensus_estimate.fwd_eps_1y
    try:
        cur.execute("""
            SELECT fwd_eps_1y, fwd_eps_1y_count, fwd_revenue_1y, source
            FROM consensus_estimate
            WHERE stock_code = ?
        """, (stock_code,))
        er = cur.fetchone()
        fwd_eps = (er['fwd_eps_1y'] if er else None)
        if fwd_eps is None:
            fwd_eps = fwd_eps_from_result
        if fwd_eps is not None:
            data = {
                'revenue': er['fwd_revenue_1y'] if er else None,
                'op_income': None,
                'net_income': None,
                'eps': fwd_eps,
                'analyst_count': er['fwd_eps_1y_count'] if er else None,
                'source': (er['source'] if er and er['source'] else 'naver'),
            }
            if save_snapshot(cur, stock_code, 'NTM',
                             datetime.now().year, None, snapshot_date, data):
                stats['ntm_new'] += 1
    except Exception as e:
        stats['errors'].append(f"ntm_snap: {str(e)[:80]}")

    conn.commit()
    conn.close()
    return stats


# ============================================================
# 4. 전체 수집 (메인)
# ============================================================

def run_daily_snapshot(
    target_size: int = 350,
    sleep_per_stock: float = 0.5,
    verbose: bool = True,
    max_stocks: Optional[int] = None
) -> Dict:
    """매일 18:00 실행: 확장 유니버스 컨센서스 스냅샷.

    max_stocks: 지정 시 최종 유니버스를 앞에서부터 N개로 제한 (테스트용).
    """
    snapshot_date = datetime.now().strftime('%Y-%m-%d')
    started = time.time()

    if verbose:
        print(f"\n=== Step 5-1-B 컨센서스 스냅샷 ({snapshot_date}) ===\n")

    universe = get_extended_universe(target_size)
    if max_stocks:
        universe = universe[:max_stocks]

    if verbose:
        print(f"유니버스: {len(universe)}종목")
        src_dist: Dict[str, int] = {}
        for s in universe:
            src_dist[s['source']] = src_dist.get(s['source'], 0) + 1
        for src, cnt in src_dist.items():
            print(f"  - {src}: {cnt}")
        print()

    stats = {
        'total_stocks': len(universe),
        'success': 0,
        'failed': 0,
        'total_quarterly_new': 0,
        'total_ntm_new': 0,
        'errors': [],
    }

    for i, stock in enumerate(universe, 1):
        try:
            result = collect_stock_snapshots(stock['code'], snapshot_date)

            if not result['errors']:
                stats['success'] += 1
            else:
                stats['failed'] += 1
                stats['errors'].append({
                    'code': stock['code'], 'name': stock.get('name'),
                    'error': '; '.join(result['errors'])[:120],
                })

            stats['total_quarterly_new'] += result['quarterly_new']
            stats['total_ntm_new'] += result['ntm_new']

            if verbose and i % 50 == 0:
                print(f"  진행: {i}/{len(universe)} | "
                      f"신규 Q={stats['total_quarterly_new']} "
                      f"NTM={stats['total_ntm_new']} | 실패={stats['failed']}")

            time.sleep(sleep_per_stock)

        except Exception as e:
            stats['failed'] += 1
            logger.error(f"[snapshot] {stock['code']} 치명 오류: {e}")

    stats['elapsed_sec'] = round(time.time() - started, 1)

    if verbose:
        print(f"\n=== 완료 ({stats['elapsed_sec']}s) ===")
        print(f"  성공: {stats['success']}/{stats['total_stocks']}")
        print(f"  실패: {stats['failed']}")
        print(f"  신규 분기 스냅샷: {stats['total_quarterly_new']}")
        print(f"  신규 NTM 스냅샷: {stats['total_ntm_new']}")
        if stats['errors'][:5]:
            print("\n실패 샘플 (최대 5건):")
            for e in stats['errors'][:5]:
                print(f"  {e['code']} {e['name']}: {e['error'][:80]}")

    return stats


# ============================================================
# 5. CLI
# ============================================================

if __name__ == '__main__':
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument('--target', type=int, default=350, help='유니버스 크기')
    p.add_argument('--sleep', type=float, default=0.5, help='종목간 대기 (초)')
    p.add_argument('--test', action='store_true', help='5종목만 테스트')
    p.add_argument('--single', type=str, help='단일 종목 테스트 (code)')
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s [%(levelname)s] %(message)s')

    if args.single:
        snapshot_date = datetime.now().strftime('%Y-%m-%d')
        result = collect_stock_snapshots(args.single, snapshot_date)
        print(f"\n결과: {result}")
    elif args.test:
        run_daily_snapshot(target_size=5, max_stocks=5, sleep_per_stock=args.sleep, verbose=True)
    else:
        run_daily_snapshot(target_size=args.target, sleep_per_stock=args.sleep)
