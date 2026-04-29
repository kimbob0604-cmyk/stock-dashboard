"""
Step 4-7-B: 동적 유니버스 관리

source 종류:
- VALUECHAIN_V2: valuechain_map.json의 KR 매핑 종목 (현재 사용)
- TOP_MARKET_CAP_KOSPI: 향후 추가 (네이버 시총 페이지 크롤링)
- TOP_MARKET_CAP_KOSDAQ: 향후 추가

각 source 별로 독립적으로 갱신, 합집합이 어닝 알림 대상.
"""

import sqlite3
from pathlib import Path
from datetime import datetime
from typing import List, Dict

DB_PATH = Path(__file__).parent / 'db' / 'dashboard.db'


def _get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================
# 1. valuechain v2 동기화
# ============================================================

def sync_valuechain_to_universe(verbose: bool = True) -> Dict:
    """valuechain_map.json의 KR 매핑 종목을 index_universe에 동기화."""
    try:
        from valuechain import get_all_stocks_in_map
    except ImportError as e:
        return {'error': f'valuechain.py 임포트 실패: {e}'}

    kr_codes, _ = get_all_stocks_in_map()
    kr_codes_set = set(kr_codes)

    today_iso = datetime.now().isoformat()

    conn = _get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT stock_code FROM index_universe
        WHERE source = 'VALUECHAIN_V2' AND is_active = 1
    """)
    existing_active = {row['stock_code'] for row in cur.fetchall()}

    new_codes = kr_codes_set - existing_active
    removed_codes = existing_active - kr_codes_set
    kept_codes = existing_active & kr_codes_set

    # 신규 등록 또는 부활
    for code in new_codes:
        cur.execute("""
            INSERT OR REPLACE INTO index_universe
              (stock_code, source, added_date, removed_date, is_active)
            VALUES (?, 'VALUECHAIN_V2', ?, NULL, 1)
        """, (code, today_iso))

    # 제거 종목 비활성화 (히스토리 유지)
    for code in removed_codes:
        cur.execute("""
            UPDATE index_universe
            SET is_active = 0, removed_date = ?
            WHERE stock_code = ? AND source = 'VALUECHAIN_V2'
        """, (today_iso, code))

    conn.commit()
    conn.close()

    result = {
        'source': 'VALUECHAIN_V2',
        'total_active': len(kr_codes),
        'newly_added': len(new_codes),
        'removed': len(removed_codes),
        'kept': len(kept_codes),
        'sync_date': today_iso,
    }

    if verbose:
        print(f"\n📊 VALUECHAIN_V2 동기화 결과")
        print(f"  활성 종목 수: {result['total_active']}")
        print(f"  신규 추가:    {result['newly_added']}")
        print(f"  제거:         {result['removed']}")
        print(f"  유지:         {result['kept']}")
        if new_codes:
            preview = sorted(new_codes)[:10]
            print(f"  추가된 종목: {preview}{'...' if len(new_codes) > 10 else ''}")
        if removed_codes:
            preview = sorted(removed_codes)[:10]
            print(f"  제거된 종목: {preview}{'...' if len(removed_codes) > 10 else ''}")

    return result


# ============================================================
# 2. 통합 유니버스 조회
# ============================================================

def get_active_universe(sources: List[str] = None) -> List[str]:
    """활성 유니버스 종목 코드 리스트 (어닝 알림 대상)."""
    conn = _get_db()
    cur = conn.cursor()

    if sources:
        placeholders = ','.join('?' * len(sources))
        cur.execute(f"""
            SELECT DISTINCT stock_code FROM index_universe
            WHERE is_active = 1 AND source IN ({placeholders})
        """, sources)
    else:
        cur.execute("""
            SELECT DISTINCT stock_code FROM index_universe
            WHERE is_active = 1
        """)

    codes = sorted({row['stock_code'] for row in cur.fetchall()})
    conn.close()
    return codes


def get_universe_with_metadata() -> List[Dict]:
    """소스별 메타데이터 포함 유니버스 조회."""
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT u.stock_code, u.source, u.rank, u.market_cap, u.added_date,
               s.name
        FROM index_universe u
        LEFT JOIN stocks s ON u.stock_code = s.code
        WHERE u.is_active = 1
        ORDER BY u.source, u.rank, u.stock_code
    """)
    results = [dict(row) for row in cur.fetchall()]
    conn.close()
    return results


def get_universe_stats() -> Dict:
    """유니버스 통계."""
    conn = _get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT source, COUNT(*) as count
        FROM index_universe
        WHERE is_active = 1
        GROUP BY source
    """)
    by_source = {row['source']: row['count'] for row in cur.fetchall()}

    cur.execute("""
        SELECT COUNT(DISTINCT stock_code) as total
        FROM index_universe WHERE is_active = 1
    """)
    total = cur.fetchone()['total']

    cur.execute("""
        SELECT COUNT(*) as historical
        FROM index_universe WHERE is_active = 0
    """)
    historical = cur.fetchone()['historical']

    conn.close()

    return {
        'by_source': by_source,
        'total_unique': total,
        'historical_removed': historical,
    }


# ============================================================
# 3. 향후 확장: 시총 상위 (네이버 크롤링)
# ============================================================

def sync_top_market_cap_kospi(top_n: int = 100) -> Dict:
    return {
        'error': '미구현. 4-7 시스템 검증 후 추가 (4-7-F)',
        'planned_source': 'TOP_MARKET_CAP_KOSPI',
        'planned_size': top_n,
    }


def sync_top_market_cap_kosdaq(top_n: int = 100) -> Dict:
    return {
        'error': '미구현. 4-7 시스템 검증 후 추가 (4-7-F)',
        'planned_source': 'TOP_MARKET_CAP_KOSDAQ',
        'planned_size': top_n,
    }


# ============================================================
# 4. CLI
# ============================================================

if __name__ == '__main__':
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == 'sync':
        sync_valuechain_to_universe()
        print("\n" + "=" * 50)
        stats = get_universe_stats()
        print("📊 유니버스 통계")
        print(f"  활성 (중복 제거): {stats['total_unique']}종목")
        print(f"  소스별:")
        for src, cnt in stats['by_source'].items():
            print(f"    {src}: {cnt}")
        if stats['historical_removed']:
            print(f"  이력 (제거됨): {stats['historical_removed']}")

    elif len(sys.argv) > 1 and sys.argv[1] == 'list':
        codes = get_active_universe()
        print(f"활성 유니버스 ({len(codes)}종목):")
        for i, code in enumerate(codes, 1):
            print(f"  {i:3d}. {code}")

    elif len(sys.argv) > 1 and sys.argv[1] == 'stats':
        stats = get_universe_stats()
        print(f"📊 유니버스 통계")
        print(f"  활성 (중복 제거): {stats['total_unique']}종목")
        print(f"  소스별:")
        for src, cnt in stats['by_source'].items():
            print(f"    {src}: {cnt}")
        if stats['historical_removed']:
            print(f"  이력 (제거됨): {stats['historical_removed']}")

    elif len(sys.argv) > 1 and sys.argv[1] == 'detail':
        meta = get_universe_with_metadata()
        print(f"활성 유니버스 상세 ({len(meta)}건):")
        print(f"{'#':>3}  {'코드':<8} {'종목명':<20} {'소스':<22} {'등록일'}")
        for i, m in enumerate(meta, 1):
            name = (m.get('name') or '-')[:18]
            added = m['added_date'][:10] if m.get('added_date') else '-'
            print(f"{i:>3}. {m['stock_code']:<8} {name:<20} {m['source']:<22} {added}")

    else:
        print("Usage:")
        print("  python3 universe_manager.py sync     # 동기화 + 통계")
        print("  python3 universe_manager.py list     # 활성 종목 코드만")
        print("  python3 universe_manager.py detail   # 상세 (이름 + 소스)")
        print("  python3 universe_manager.py stats    # 통계만")
