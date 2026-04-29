"""
Step 4-7-D-3 + 4-7-D-4
- Sanity check (컨센서스 자체 신뢰도 검증)
- 시그널 분류 (12개 시그널 + 우선순위)
- earnings_surprise 테이블 INSERT

워크플로우:
1. earnings_actual + consensus_quarterly 조회
2. Sanity check (Gate A/B/C)
3. 시그널 분류 (12개 중 1개)
4. earnings_surprise INSERT
5. priority 1~3 → 텔레그램 큐 (4-7-D-5에서 처리)
"""

import sqlite3
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, List

DB_PATH = Path(__file__).parent / 'db' / 'dashboard.db'


def _get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================
# 1. Sanity Check
# ============================================================

def sanity_check_consensus(stock_code: str, year: int, quarter: int,
                           consensus: Dict) -> Dict:
    """컨센서스 자체 신뢰도 검증 (3-Gate)."""
    conn = _get_db()
    cur = conn.cursor()

    rev_consensus = consensus.get('revenue_consensus')
    op_consensus = consensus.get('op_consensus')

    if rev_consensus is None or rev_consensus <= 0:
        conn.close()
        return {
            'is_valid': False, 'confidence': 0.0,
            'gates': {'A': 'fail', 'B': 'skip', 'C': 'skip'},
            'reasons': ['매출 컨센서스 누락 또는 0'],
            'recommendation': 'skip',
        }

    gates = {}
    reasons = []
    confidence = 1.0

    # Gate A: 직전 4분기 평균 매출 비율
    # financial_quarterly: 원 단위. consensus_quarterly: 백만원 단위 → 변환 필요
    cur.execute("""
        SELECT revenue FROM financial_quarterly
        WHERE stock_code = ? AND revenue > 0
        ORDER BY year DESC, quarter DESC LIMIT 4
    """, (stock_code,))
    last_4q = [row['revenue'] / 1_000_000 for row in cur.fetchall()]  # 원 → 백만원

    if len(last_4q) >= 3:
        avg_4q_rev = sum(last_4q) / len(last_4q)
        ratio_a = rev_consensus / avg_4q_rev if avg_4q_rev > 0 else 0
        if 0.5 <= ratio_a <= 2.0:
            gates['A'] = 'pass'
        elif 0.3 <= ratio_a < 0.5 or 2.0 < ratio_a <= 3.0:
            gates['A'] = 'warn'
            reasons.append(f"매출 컨센이 직전 4분기 평균의 {ratio_a:.2f}배")
            confidence *= 0.7
        else:
            gates['A'] = 'fail'
            reasons.append(f"매출 컨센이 직전 4분기 평균의 {ratio_a:.2f}배 — 이상치")
            confidence *= 0.3
    else:
        gates['A'] = 'skip'
        reasons.append("직전 4분기 데이터 부족")
        confidence *= 0.8

    # Gate B: OPM vs 5년 평균
    if op_consensus is not None and rev_consensus > 0:
        consensus_opm = (op_consensus / rev_consensus) * 100
        cur.execute("""
            SELECT AVG(opm) as avg_opm, COUNT(*) as cnt
            FROM financial_quarterly
            WHERE stock_code = ? AND opm IS NOT NULL AND revenue > 0
        """, (stock_code,))
        row = cur.fetchone()
        if row and row['cnt'] >= 4:
            historical_opm = row['avg_opm']
            opm_diff = abs(consensus_opm - historical_opm)
            if opm_diff <= 15:
                gates['B'] = 'pass'
            elif opm_diff <= 25:
                gates['B'] = 'warn'
                reasons.append(f"OPM 컨센 {consensus_opm:.1f}% vs 평균 {historical_opm:.1f}%")
                confidence *= 0.8
            else:
                gates['B'] = 'fail'
                reasons.append(f"OPM 컨센 {consensus_opm:.1f}% vs 평균 {historical_opm:.1f}% — 이상")
                confidence *= 0.4
        else:
            gates['B'] = 'skip'
    else:
        gates['B'] = 'skip'

    # Gate C: 시총 대비 매출
    cur.execute("SELECT close FROM ohlcv WHERE code = ? ORDER BY date DESC LIMIT 1",
                (stock_code,))
    price_row = cur.fetchone()
    cur.execute("SELECT shares_outstanding FROM financial WHERE code = ?", (stock_code,))
    shares_row = cur.fetchone()

    if price_row and shares_row and shares_row['shares_outstanding']:
        try:
            # close (원) × shares (주) = market_cap (원)
            market_cap_won = float(price_row['close']) * float(shares_row['shares_outstanding'])
            market_cap_million = market_cap_won / 1_000_000  # 원 → 백만원
            # rev_consensus는 이미 백만원 단위 → 동일 단위 비교
            ratio_c = rev_consensus / market_cap_million if market_cap_million > 0 else 0
            if 0.005 <= ratio_c <= 1.0:
                gates['C'] = 'pass'
            elif 0.001 <= ratio_c < 0.005 or 1.0 < ratio_c <= 5.0:
                gates['C'] = 'warn'
                reasons.append(f"매출/시총 {ratio_c*100:.2f}%")
                confidence *= 0.8
            else:
                gates['C'] = 'fail'
                reasons.append(f"매출/시총 {ratio_c*100:.4f}% — 이상")
                confidence *= 0.3
        except (TypeError, ValueError):
            gates['C'] = 'skip'
    else:
        gates['C'] = 'skip'

    conn.close()

    fail_count = sum(1 for v in gates.values() if v == 'fail')
    warn_count = sum(1 for v in gates.values() if v == 'warn')

    if fail_count >= 2:
        recommendation = 'skip'; is_valid = False
    elif fail_count == 1:
        recommendation = 'use_with_caution'; is_valid = False
    elif warn_count >= 2:
        recommendation = 'use_with_caution'; is_valid = True
    else:
        recommendation = 'use'; is_valid = True

    return {
        'is_valid': is_valid,
        'confidence': round(confidence, 2),
        'gates': gates,
        'reasons': reasons,
        'recommendation': recommendation,
    }


# ============================================================
# 2. 시그널 분류
# ============================================================

THRESHOLDS = {
    'BIG': 20.0, 'STANDARD': 10.0, 'INLINE': 5.0,
    'TURNAROUND_PARTIAL_RATIO': 0.5,
}


def classify_signal(consensus: Dict, actual: Dict) -> Dict:
    rev_c = consensus.get('revenue_consensus')
    op_c = consensus.get('op_consensus')
    rev_a = actual.get('revenue')
    op_a = actual.get('operating_profit')

    rev_pct = ((rev_a - rev_c) / abs(rev_c) * 100) if (rev_c and rev_c != 0 and rev_a is not None) else None
    op_pct = ((op_a - op_c) / abs(op_c) * 100) if (op_c and op_c != 0 and op_a is not None) else None

    # TURNAROUND/SHOCK 우선
    if op_c is not None and op_a is not None:
        if op_c < 0 and op_a > 0:
            return _build_signal('TURNAROUND_FULL', 1, rev_pct, op_pct,
                                 ['OP_TURNAROUND'],
                                 f'컨센 적자({op_c:,.0f}) → 실제 흑자({op_a:,.0f})')
        if op_c > 0 and op_a < 0:
            return _build_signal('SHOCK_FULL', 1, rev_pct, op_pct,
                                 ['OP_SHOCK'],
                                 f'컨센 흑자({op_c:,.0f}) → 실제 적자({op_a:,.0f})')
        if op_c < 0 and op_a < 0:
            ratio = abs(op_a) / abs(op_c)
            if ratio <= (1 - THRESHOLDS['TURNAROUND_PARTIAL_RATIO']):
                return _build_signal('TURNAROUND_PARTIAL', 2, rev_pct, op_pct,
                                     ['OP_LOSS_NARROWING'],
                                     f'적자폭 {(1-ratio)*100:.0f}% 축소 ({op_c:,.0f}→{op_a:,.0f})')

    # 매출/영업익 둘 다
    if rev_pct is not None and op_pct is not None:
        if op_pct >= THRESHOLDS['BIG']:
            return _build_signal('BEAT_BIG', 1, rev_pct, op_pct,
                                 ['OP_BEAT_BIG'] + (['REV_BEAT'] if rev_pct >= THRESHOLDS['STANDARD'] else []),
                                 f"영업익 {op_pct:+.1f}% (BIG)")
        if op_pct <= -THRESHOLDS['BIG']:
            return _build_signal('MISS_BIG', 1, rev_pct, op_pct,
                                 ['OP_MISS_BIG'] + (['REV_MISS'] if rev_pct <= -THRESHOLDS['STANDARD'] else []),
                                 f"영업익 {op_pct:+.1f}% (BIG)")
        if rev_pct <= -THRESHOLDS['STANDARD'] and op_pct >= THRESHOLDS['STANDARD']:
            return _build_signal('REVENUE_MISS_OP_BEAT', 2, rev_pct, op_pct,
                                 ['REV_MISS', 'OP_BEAT', 'PRICING_POWER'],
                                 f"매출 {rev_pct:+.1f}%, 영업익 {op_pct:+.1f}% — 가격 결정력")
        if rev_pct >= THRESHOLDS['STANDARD'] and abs(op_pct) <= THRESHOLDS['INLINE']:
            return _build_signal('REVENUE_BEAT_OP_INLINE', 3, rev_pct, op_pct,
                                 ['REV_BEAT', 'OP_INLINE', 'MARGIN_PRESSURE'],
                                 f"매출 {rev_pct:+.1f}%, 영업익 {op_pct:+.1f}% — 마진 압박")
        if THRESHOLDS['STANDARD'] <= op_pct < THRESHOLDS['BIG']:
            return _build_signal('BEAT', 2, rev_pct, op_pct,
                                 ['OP_BEAT'], f"영업익 {op_pct:+.1f}%")
        if -THRESHOLDS['BIG'] < op_pct <= -THRESHOLDS['STANDARD']:
            return _build_signal('MISS', 2, rev_pct, op_pct,
                                 ['OP_MISS'], f"영업익 {op_pct:+.1f}%")
        if rev_pct >= THRESHOLDS['STANDARD'] and abs(op_pct) < THRESHOLDS['STANDARD']:
            return _build_signal('REVENUE_BEAT', 3, rev_pct, op_pct,
                                 ['REV_BEAT'], f"매출만 {rev_pct:+.1f}%")
        if rev_pct <= -THRESHOLDS['STANDARD'] and abs(op_pct) < THRESHOLDS['STANDARD']:
            return _build_signal('REVENUE_MISS', 3, rev_pct, op_pct,
                                 ['REV_MISS'], f"매출만 {rev_pct:+.1f}%")
        return _build_signal('INLINE', 5, rev_pct, op_pct, [],
                             f"매출 {rev_pct:+.1f}%, 영업익 {op_pct:+.1f}%")

    # 영업익만
    if op_pct is not None:
        if op_pct >= THRESHOLDS['BIG']:
            return _build_signal('BEAT_BIG', 1, None, op_pct, ['OP_BEAT_BIG'], f"영업익 {op_pct:+.1f}%")
        if op_pct <= -THRESHOLDS['BIG']:
            return _build_signal('MISS_BIG', 1, None, op_pct, ['OP_MISS_BIG'], f"영업익 {op_pct:+.1f}%")
        if op_pct >= THRESHOLDS['STANDARD']:
            return _build_signal('BEAT', 2, None, op_pct, ['OP_BEAT'], f"영업익 {op_pct:+.1f}%")
        if op_pct <= -THRESHOLDS['STANDARD']:
            return _build_signal('MISS', 2, None, op_pct, ['OP_MISS'], f"영업익 {op_pct:+.1f}%")

    # 매출만
    if rev_pct is not None:
        if rev_pct >= THRESHOLDS['STANDARD']:
            return _build_signal('REVENUE_BEAT', 3, rev_pct, None, ['REV_BEAT'], f"매출 {rev_pct:+.1f}%")
        if rev_pct <= -THRESHOLDS['STANDARD']:
            return _build_signal('REVENUE_MISS', 3, rev_pct, None, ['REV_MISS'], f"매출 {rev_pct:+.1f}%")

    return _build_signal('INLINE', 5, rev_pct, op_pct, [], '데이터 부족 또는 인라인')


def _build_signal(signal: str, priority: int, rev_pct: Optional[float],
                  op_pct: Optional[float], triggers: List[str], note: str) -> Dict:
    return {
        'signal': signal,
        'priority': priority,
        'revenue_surprise_pct': round(rev_pct, 2) if rev_pct is not None else None,
        'op_surprise_pct': round(op_pct, 2) if op_pct is not None else None,
        'triggers': triggers,
        'note': note,
    }


# ============================================================
# 3. 메인 진입
# ============================================================

def process_earnings_announcement(stock_code: str, year: int, quarter: int,
                                   verbose: bool = True) -> Dict:
    conn = _get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT * FROM earnings_actual
        WHERE stock_code = ? AND year = ? AND quarter = ?
        ORDER BY
          CASE source_type
            WHEN 'DART_QUARTERLY' THEN 1
            WHEN 'DART_PRELIMINARY' THEN 2
            WHEN 'NAVER_TB_TYPE1' THEN 3
            ELSE 4
          END, parsed_at DESC
        LIMIT 1
    """, (stock_code, year, quarter))
    actual_row = cur.fetchone()

    if not actual_row:
        conn.close()
        return {'error': 'earnings_actual 없음', 'stock_code': stock_code}

    actual = dict(actual_row)

    cur.execute("""
        SELECT * FROM consensus_quarterly
        WHERE stock_code = ? AND year = ? AND quarter = ?
    """, (stock_code, year, quarter))
    cons_row = cur.fetchone()

    if not cons_row:
        conn.close()
        return _process_yoy_only(stock_code, year, quarter, actual)

    consensus = dict(cons_row)
    sanity = sanity_check_consensus(stock_code, year, quarter, consensus)

    if sanity['recommendation'] == 'skip':
        conn.close()
        result = _process_yoy_only(stock_code, year, quarter, actual)
        result['sanity_check'] = sanity
        result['note'] = (result.get('note', '') + ' [컨센 sanity fail → YoY 폴백]').strip()
        return result

    signal_data = classify_signal(consensus, actual)

    if sanity['recommendation'] == 'use_with_caution':
        if signal_data['priority'] == 1:
            signal_data['priority'] = 2
            signal_data['note'] += f" [컨센 신뢰도 {sanity['confidence']:.0%}]"

    triggers_json = json.dumps(signal_data['triggers'], ensure_ascii=False)
    now = datetime.now().isoformat()

    cur.execute("""
        INSERT OR REPLACE INTO earnings_surprise (
            stock_code, year, quarter,
            revenue_consensus, op_consensus,
            revenue_actual, op_actual,
            revenue_surprise_pct, op_surprise_pct,
            signal, priority,
            alert_sent, triggers, note,
            calculated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
    """, (
        stock_code, year, quarter,
        consensus.get('revenue_consensus'),
        consensus.get('op_consensus'),
        actual.get('revenue'),
        actual.get('operating_profit'),
        signal_data['revenue_surprise_pct'],
        signal_data['op_surprise_pct'],
        signal_data['signal'],
        signal_data['priority'],
        triggers_json,
        signal_data['note'],
        now,
    ))
    conn.commit()
    conn.close()

    result = {
        'stock_code': stock_code, 'year': year, 'quarter': quarter,
        'signal': signal_data['signal'],
        'priority': signal_data['priority'],
        'revenue_surprise_pct': signal_data['revenue_surprise_pct'],
        'op_surprise_pct': signal_data['op_surprise_pct'],
        'note': signal_data['note'],
        'triggers': signal_data['triggers'],
        'sanity_check': sanity,
        'will_alert': signal_data['priority'] <= 3,
    }

    if verbose:
        emoji = {1: '🔥', 2: '📢', 3: '📌', 4: '·', 5: '·'}.get(signal_data['priority'], '?')
        print(f"  {emoji} {stock_code} {year}Q{quarter}: {signal_data['signal']} (P{signal_data['priority']}) — {signal_data['note']}")

    return result


def _process_yoy_only(stock_code: str, year: int, quarter: int, actual: Dict) -> Dict:
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT revenue, operating_profit FROM financial_quarterly
        WHERE stock_code = ? AND year = ? AND quarter = ?
    """, (stock_code, year - 1, quarter))
    prev_row = cur.fetchone()
    conn.close()

    if not prev_row:
        return {
            'stock_code': stock_code, 'signal': 'NO_DATA',
            'priority': 5,
            'note': '컨센서스 + 전년동기 모두 없음',
            'will_alert': False,
        }

    rev_yoy_pct = None
    op_yoy_pct = None
    if prev_row['revenue'] and prev_row['revenue'] > 0 and actual.get('revenue') is not None:
        rev_yoy_pct = (actual['revenue'] - prev_row['revenue']) / prev_row['revenue'] * 100
    if prev_row['operating_profit'] and actual.get('operating_profit') is not None:
        op_yoy_pct = (actual['operating_profit'] - prev_row['operating_profit']) / abs(prev_row['operating_profit']) * 100

    signal, priority = 'YOY_INLINE', 5
    triggers = []
    note_parts = []

    if op_yoy_pct is not None:
        if prev_row['operating_profit'] < 0 and actual['operating_profit'] > 0:
            signal, priority = 'YOY_TURNAROUND', 2
            triggers.append('YOY_TURNAROUND')
        elif prev_row['operating_profit'] > 0 and actual['operating_profit'] < 0:
            signal, priority = 'YOY_SHOCK', 2
            triggers.append('YOY_SHOCK')
        elif op_yoy_pct >= 50:
            signal, priority = 'YOY_SURGE', 3
            triggers.append('YOY_SURGE')
        elif op_yoy_pct <= -30:
            signal, priority = 'YOY_PLUNGE', 3
            triggers.append('YOY_PLUNGE')
        note_parts.append(f"영업익 YoY {op_yoy_pct:+.1f}%")

    if rev_yoy_pct is not None:
        note_parts.append(f"매출 YoY {rev_yoy_pct:+.1f}%")

    return {
        'stock_code': stock_code, 'year': year, 'quarter': quarter,
        'signal': signal, 'priority': priority,
        'revenue_yoy_pct': round(rev_yoy_pct, 2) if rev_yoy_pct else None,
        'op_yoy_pct': round(op_yoy_pct, 2) if op_yoy_pct else None,
        'triggers': triggers,
        'note': ' / '.join(note_parts) + ' [컨센 부재, YoY 비교]',
        'will_alert': priority <= 3,
    }


# ============================================================
# 4. CLI
# ============================================================

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 earnings_signal_classifier.py <code> <year> <quarter>")
        print("  python3 earnings_signal_classifier.py all 2026 1")
        print("  python3 earnings_signal_classifier.py recent")
        sys.exit(0)

    if sys.argv[1] == 'recent':
        conn = _get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT stock_code, year, quarter
            FROM earnings_actual
            WHERE parsed_at >= datetime('now', '-7 days')
            ORDER BY parsed_at DESC
        """)
        targets = [(row['stock_code'], row['year'], row['quarter']) for row in cur.fetchall()]
        conn.close()
        print(f"\n=== 최근 7일 어닝 {len(targets)}건 처리 ===\n")
        for code, y, q in targets:
            process_earnings_announcement(code, y, q)

    elif sys.argv[1] == 'all':
        year = int(sys.argv[2])
        quarter = int(sys.argv[3])
        from universe_manager import get_active_universe
        universe = get_active_universe()
        print(f"\n=== 유니버스 {len(universe)}종목 {year}Q{quarter} 처리 ===\n")
        results = []
        for code in universe:
            r = process_earnings_announcement(code, year, quarter, verbose=False)
            if 'error' not in r:
                results.append(r)
                if r.get('will_alert'):
                    print(f"  🚨 {code}: {r['signal']} (P{r['priority']}) — {r.get('note','')[:80]}")
        by_signal = {}
        for r in results:
            sig = r.get('signal', 'NONE')
            by_signal[sig] = by_signal.get(sig, 0) + 1
        print(f"\n=== 통계 ({len(results)}건) ===")
        for sig, cnt in sorted(by_signal.items(), key=lambda x: -x[1]):
            print(f"  {sig}: {cnt}")
    else:
        code = sys.argv[1]
        year = int(sys.argv[2]) if len(sys.argv) > 2 else 2026
        quarter = int(sys.argv[3]) if len(sys.argv) > 3 else 1
        result = process_earnings_announcement(code, year, quarter)
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
