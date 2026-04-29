"""
Step 4-4-H + 4-4-I

분석 일지 보조 기능:
- build_prefill_data: 종목 코드 → 자동 산출 데이터로 초안 작성
- get_journal_stats: 분석 메타 통계
- get_stocks_without_analysis: 활성 유니버스 중 분석 없는 종목

검증 시트(4-5)에서 활용:
- 종목 클릭 → /api/journal/prefill/<code> → 자동 채움
- 모니터링 페이지 → /api/journal/stats
"""

import sqlite3
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List

DB_PATH = Path(__file__).parent / 'db' / 'dashboard.db'


def _get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================
# 4-4-H. Prefill — 자동 산출 데이터로 분석 초안 작성
# ============================================================

def build_prefill_data(stock_code: str) -> Dict:
    """종목 코드로 분석 일지 초안 자동 작성.
    실패해도 errors에 누적하고 다음 단계 계속 진행."""
    result = {
        'stock_code': stock_code,
        'analysis_date': datetime.now().strftime('%Y-%m-%d'),
        'analyst': '범준',
        'data_sources': [],
        'errors': [],
    }

    conn = _get_db()
    cur = conn.cursor()

    # 1. 종목 기본 정보
    try:
        cur.execute("SELECT name, market FROM stocks WHERE code = ?", (stock_code,))
        row = cur.fetchone()
        if row:
            result['stock_name'] = row['name']
            result['market'] = row['market']
            result['data_sources'].append('stocks')
        else:
            result['errors'].append('stocks 테이블에 종목 없음')
    except Exception as e:
        result['errors'].append(f'stocks: {e}')

    # 2. STEP 1: 밸류체인 매칭 segment
    try:
        from valuechain import get_all_segments
        matched = []
        for tid, lid, sid, seg in get_all_segments():
            stocks_kr = seg.get('stocks_kr', []) or []
            if stock_code in stocks_kr:
                matched.append({
                    'theme_id': tid,
                    'layer_id': lid,
                    'segment_id': sid,
                    'segment_name_kr': seg.get('name_kr', sid),
                    'is_bottleneck': bool(seg.get('is_bottleneck', False)),
                })
        if matched:
            primary = matched[0]
            result['theme_id'] = primary['theme_id']
            result['layer_id'] = primary['layer_id']
            result['segment_id'] = primary['segment_id']
            result['is_bottleneck'] = 1 if primary['is_bottleneck'] else 0
            result['matched_segments'] = matched
            result['data_sources'].append('valuechain')
    except Exception as e:
        result['errors'].append(f'valuechain: {e}')

    # 3. STEP 2: 5년 분기 OPM 통계
    try:
        cur.execute("""
            SELECT COUNT(*) as q_count, AVG(opm) as avg_opm,
                   MIN(opm) as min_opm, MAX(opm) as max_opm
            FROM financial_quarterly
            WHERE stock_code = ? AND opm IS NOT NULL
        """, (stock_code,))
        fq_row = cur.fetchone()
        if fq_row and fq_row['q_count'] > 0:
            result['opm_estimate'] = round(fq_row['avg_opm'], 2)
            result['opm_source'] = f'5년 분기 평균 ({fq_row["q_count"]}분기)'
            result['_opm_range'] = (round(fq_row['min_opm'], 1),
                                     round(fq_row['max_opm'], 1))
            result['data_sources'].append('financial_quarterly')
    except Exception as e:
        result['errors'].append(f'financial_quarterly: {e}')

    # 4. STEP 2: 현재가 + 시총 + 52주 수익률
    # ⚠ ohlcv 컬럼은 'code' (사용자 코드의 stock_code 아님)
    try:
        cur.execute("""
            SELECT close, date FROM ohlcv
            WHERE code = ?
            ORDER BY date DESC LIMIT 1
        """, (stock_code,))
        price_row = cur.fetchone()
        if price_row:
            result['current_price'] = price_row['close']
            cur.execute("""
                SELECT close FROM ohlcv
                WHERE code = ? AND date <= date(?, '-365 days')
                ORDER BY date DESC LIMIT 1
            """, (stock_code, price_row['date']))
            year_ago = cur.fetchone()
            if year_ago:
                ret = (price_row['close'] - year_ago['close']) / year_ago['close'] * 100
                result['return_52w'] = round(ret, 2)

        # 시총 (financial.shares_outstanding × close)
        cur.execute("SELECT shares_outstanding FROM financial WHERE code = ?",
                    (stock_code,))
        sh_row = cur.fetchone()
        if sh_row and sh_row['shares_outstanding'] and price_row:
            try:
                shares = float(sh_row['shares_outstanding'])
                market_cap_won = price_row['close'] * shares
                result['market_cap'] = round(market_cap_won / 1_000_000)  # 백만원
            except (TypeError, ValueError):
                pass
    except Exception as e:
        result['errors'].append(f'price/marketcap: {e}')

    # 5. STEP 4: 자동 TAM
    try:
        from tam_modeler import build_auto_tam_with_label
        tam = build_auto_tam_with_label(stock_code)

        if tam.get('method') in ('PER_BASED', 'US_PER_FALLBACK'):
            for scenario in ['bear', 'base', 'bull']:
                s = tam.get(scenario, {})
                result[f'{scenario}_eps'] = s.get('eps')
                result[f'{scenario}_per'] = s.get('per')
                result[f'{scenario}_tp'] = round(s.get('tp')) if s.get('tp') else None
                result[f'{scenario}_eps_source'] = s.get('eps_source')
                result[f'{scenario}_per_source'] = s.get('per_source')
            result['_tam_method'] = tam['method']
            result['_consensus_tp'] = tam.get('consensus_tp')
            result['data_sources'].append('tam_modeler')
        elif tam.get('method') == 'PBR_BASED':
            for scenario in ['bear', 'base', 'bull']:
                s = tam.get(scenario, {})
                result[f'{scenario}_tp'] = round(s.get('tp')) if s.get('tp') else None
                result[f'{scenario}_per_source'] = 'PBR'
            result['_tam_method'] = 'PBR_BASED'
            result['data_sources'].append('tam_modeler_pbr')
    except Exception as e:
        result['errors'].append(f'tam_modeler: {e}')

    # 6. STEP 2: Fwd PER + 5년 백분위
    try:
        cur.execute("""
            SELECT per_p25, per_p50, per_p75, per_current, quarters_covered
            FROM valuation_band WHERE stock_code = ?
        """, (stock_code,))
        band_row = cur.fetchone()
        if band_row and band_row['per_current']:
            result['fwd_per'] = round(band_row['per_current'], 2)
            cur_per = band_row['per_current']
            p25 = band_row['per_p25'] or 0
            p50 = band_row['per_p50'] or 0
            p75 = band_row['per_p75'] or 0
            if cur_per <= p25: pct = 12
            elif cur_per <= p50: pct = 37
            elif cur_per <= p75: pct = 62
            else: pct = 87
            result['fwd_per_band_pct'] = pct
            result['_per_band'] = {
                'p25': p25, 'p50': p50, 'p75': p75,
                'quarters': band_row['quarters_covered'],
            }
            result['data_sources'].append('valuation_band')
    except Exception as e:
        result['errors'].append(f'valuation_band: {e}')

    # 7. STEP 5: 반영도 점수
    # ⚠ calculate_reflection_score 반환 키: score / label / badge / breakdown
    try:
        from valuechain import calculate_reflection_score
        ref = calculate_reflection_score(stock_code, segment_heat=50)
        if ref and ref.get('score') is not None:
            result['reflection_score'] = round(ref['score'])
            result['reflection_label'] = ref.get('label', '')
            result['data_sources'].append('reflection_score')
    except Exception as e:
        result['errors'].append(f'reflection_score: {e}')

    # 8. KUVIC 세션
    try:
        from kuvic_validator import KUVIC_SESSION_ANALYSIS
        if stock_code in KUVIC_SESSION_ANALYSIS:
            kuvic = KUVIC_SESSION_ANALYSIS[stock_code]
            result['kuvic_session'] = {
                'thesis': kuvic.get('thesis'),
                'tags': kuvic.get('tags', []),
                'conclusion': kuvic.get('conclusion'),
                'priority': kuvic.get('priority'),
                'session_date': kuvic.get('session_date'),
                'has_manual_tp': bool(kuvic.get('base_tp')),
            }
            result['suggested_thesis'] = kuvic.get('thesis')
            result['suggested_tags'] = kuvic.get('tags', [])
            result['suggested_conclusion'] = kuvic.get('conclusion')
            result['suggested_priority'] = kuvic.get('priority')
            result['data_sources'].append('kuvic_session')
    except ImportError:
        pass
    except Exception as e:
        result['errors'].append(f'kuvic: {e}')

    # 9. 최근 어닝 시그널
    try:
        cur.execute("""
            SELECT year, quarter, signal, priority,
                   revenue_surprise_pct, op_surprise_pct
            FROM earnings_surprise
            WHERE stock_code = ?
            ORDER BY year DESC, quarter DESC LIMIT 4
        """, (stock_code,))
        earnings = [dict(r) for r in cur.fetchall()]
        if earnings:
            result['recent_earnings'] = earnings
            result['data_sources'].append('earnings_surprise')
    except Exception as e:
        result['errors'].append(f'earnings: {e}')

    conn.close()
    return result


# ============================================================
# 4-4-I. 통계
# ============================================================

def get_journal_stats() -> Dict:
    conn = _get_db()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM analysis_journal")
    total = cur.fetchone()[0]

    if total == 0:
        conn.close()
        return {
            'total': 0,
            'by_conclusion': {}, 'by_analyst': {}, 'by_priority': {},
            'activity': {'last_7d': 0, 'last_30d': 0},
            'top_tags': [],
            'buy_stats': {'count': 0, 'avg_base_tp': None},
            'top_stocks': [], 'theme_distribution': {},
        }

    cur.execute("""SELECT conclusion, COUNT(*) as cnt FROM analysis_journal
                   WHERE conclusion IS NOT NULL GROUP BY conclusion ORDER BY cnt DESC""")
    by_conclusion = {row['conclusion']: row['cnt'] for row in cur.fetchall()}

    cur.execute("""SELECT analyst, COUNT(*) as cnt FROM analysis_journal
                   WHERE analyst IS NOT NULL GROUP BY analyst ORDER BY cnt DESC""")
    by_analyst = {row['analyst']: row['cnt'] for row in cur.fetchall()}

    cur.execute("""SELECT priority, COUNT(*) as cnt FROM analysis_journal
                   WHERE priority IS NOT NULL GROUP BY priority ORDER BY priority DESC""")
    by_priority = {row['priority']: row['cnt'] for row in cur.fetchall()}

    cur.execute("""
        SELECT
            COUNT(CASE WHEN analysis_date >= date('now', '-7 days') THEN 1 END) as last_7d,
            COUNT(CASE WHEN analysis_date >= date('now', '-30 days') THEN 1 END) as last_30d
        FROM analysis_journal
    """)
    activity = dict(cur.fetchone())

    cur.execute("SELECT tags FROM analysis_journal WHERE tags IS NOT NULL")
    tag_counter = {}
    for row in cur.fetchall():
        try:
            tags = json.loads(row['tags']) if row['tags'] else []
            for t in tags:
                tag_counter[t] = tag_counter.get(t, 0) + 1
        except (json.JSONDecodeError, TypeError):
            pass
    top_tags = [{'tag': t, 'count': c}
                for t, c in sorted(tag_counter.items(), key=lambda x: -x[1])[:15]]

    cur.execute("""
        SELECT COUNT(*) as cnt, AVG(base_tp) as avg_tp
        FROM analysis_journal
        WHERE conclusion = 'BUY' AND base_tp IS NOT NULL
    """)
    buy_row = cur.fetchone()
    buy_stats = {
        'count': buy_row['cnt'] or 0,
        'avg_base_tp': round(buy_row['avg_tp'], 0) if buy_row['avg_tp'] else None,
    }

    cur.execute("""
        SELECT j.stock_code,
               COALESCE(j.stock_name, s.name) as stock_name,
               COUNT(*) as cnt,
               MAX(j.analysis_date) as latest_date,
               GROUP_CONCAT(DISTINCT j.conclusion) as conclusions
        FROM analysis_journal j
        LEFT JOIN stocks s ON j.stock_code = s.code
        GROUP BY j.stock_code
        ORDER BY cnt DESC, latest_date DESC LIMIT 10
    """)
    top_stocks = [dict(row) for row in cur.fetchall()]

    cur.execute("""SELECT theme_id, COUNT(*) as cnt FROM analysis_journal
                   WHERE theme_id IS NOT NULL GROUP BY theme_id ORDER BY cnt DESC""")
    theme_distribution = {row['theme_id']: row['cnt'] for row in cur.fetchall()}

    conn.close()

    return {
        'total': total,
        'by_conclusion': by_conclusion,
        'by_analyst': by_analyst,
        'by_priority': by_priority,
        'activity': activity,
        'top_tags': top_tags,
        'buy_stats': buy_stats,
        'top_stocks': top_stocks,
        'theme_distribution': theme_distribution,
    }


def get_stocks_without_analysis(limit: int = 50) -> List[Dict]:
    conn = _get_db()
    cur = conn.execute("""
        SELECT u.stock_code, s.name
        FROM index_universe u
        LEFT JOIN stocks s ON u.stock_code = s.code
        LEFT JOIN (
            SELECT DISTINCT stock_code FROM analysis_journal
        ) j ON u.stock_code = j.stock_code
        WHERE u.is_active = 1 AND j.stock_code IS NULL
        ORDER BY u.stock_code LIMIT ?
    """, (limit,))
    results = [dict(row) for row in cur.fetchall()]
    conn.close()
    return results


# ============================================================
# CLI
# ============================================================

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 analysis_journal_helper.py prefill <code>")
        print("  python3 analysis_journal_helper.py stats")
        print("  python3 analysis_journal_helper.py missing [limit]")
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd == 'prefill':
        if len(sys.argv) < 3:
            print("Usage: prefill <code>"); sys.exit(1)
        code = sys.argv[2]
        data = build_prefill_data(code)
        print(json.dumps(data, indent=2, ensure_ascii=False, default=str))

    elif cmd == 'stats':
        stats = get_journal_stats()
        print(f"\n=== 분석 일지 통계 ===\n")
        print(f"전체: {stats['total']}건")
        print(f"\n[결론별]")
        for c, n in stats['by_conclusion'].items():
            print(f"  {c}: {n}")
        print(f"\n[분석가별]")
        for a, n in stats['by_analyst'].items():
            print(f"  {a}: {n}")
        print(f"\n[우선순위별]")
        for p, n in stats['by_priority'].items():
            print(f"  {p}: {n}")
        print(f"\n[활동]")
        print(f"  최근 7일: {stats['activity']['last_7d']}건")
        print(f"  최근 30일: {stats['activity']['last_30d']}건")
        print(f"\n[BUY 통계]")
        bs = stats['buy_stats']
        print(f"  BUY 종목: {bs['count']}건")
        if bs.get('avg_base_tp'):
            print(f"  평균 Base TP: {bs['avg_base_tp']:,.0f}원")
        print(f"\n[TOP 태그]")
        for t in stats['top_tags']:
            print(f"  #{t['tag']}: {t['count']}")
        print(f"\n[테마별 분포]")
        for t, n in stats['theme_distribution'].items():
            print(f"  {t}: {n}")
        print(f"\n[분석 많은 종목 TOP 10]")
        for s in stats['top_stocks']:
            name = s.get('stock_name') or s['stock_code']
            print(f"  {name} ({s['stock_code']}): {s['cnt']}건 (최신 {s['latest_date']}, 결론 {s['conclusions']})")

    elif cmd == 'missing':
        try:
            limit = int(sys.argv[2]) if len(sys.argv) > 2 else 50
        except ValueError:
            limit = 50
        results = get_stocks_without_analysis(limit)
        print(f"\n=== 활성 유니버스 중 분석 없는 종목 ({len(results)}개) ===\n")
        for r in results:
            print(f"  {r['stock_code']:<8} {r.get('name', '?')}")
