"""
자동 TAM 모델링 (Bear / Base / Bull)
Step 4-3-C — 완전 본체

EPS × PER → TP 자동 산출
입력: valuation_band(4-2-B) + consensus_estimate(4-3-B) + financial_quarterly(4-2-A)

산출 우선순위:
- Base EPS: 컨센서스 → TTM EPS
- Bear EPS: 컨센서스 ×0.7 → Base ×0.7
- Bull EPS: 컨센서스 high → Base ×1.2
- Bear PER: 5년 P25
- Base PER: 5년 P50
- Bull PER: 동종 평균 ×1.1 → 5년 P75
- 적자 종목: PBR 기반 폴백
- US 종목: yfinance trailing/forwardPE 폴백
"""

import sqlite3
import re
import json
from pathlib import Path
from typing import Optional, Dict, List

DB_PATH = Path(__file__).parent / 'db' / 'dashboard.db'


def _get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def is_kr_stock(code: str) -> bool:
    return bool(re.fullmatch(r'\d{6}', code))


# ============================================================
# 1. 데이터 페치
# ============================================================

def get_valuation_band(stock_code: str) -> Optional[Dict]:
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM valuation_band WHERE stock_code = ?", (stock_code,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_consensus(stock_code: str) -> Optional[Dict]:
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM consensus_estimate WHERE stock_code = ?", (stock_code,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_quarterly_metrics(stock_code: str) -> Optional[Dict]:
    """5년 OPM 통계 + TTM EPS + TTM Revenue"""
    conn = _get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT MIN(opm) as min_opm, AVG(opm) as avg_opm, MAX(opm) as max_opm,
               COUNT(*) as q_count
        FROM financial_quarterly
        WHERE stock_code = ? AND opm IS NOT NULL AND revenue > 0
    """, (stock_code,))
    stats = cur.fetchone()

    if not stats or stats['q_count'] == 0:
        conn.close()
        return None

    cur.execute("""
        SELECT eps, revenue, year, quarter
        FROM financial_quarterly
        WHERE stock_code = ? AND revenue > 0
        ORDER BY year DESC, quarter DESC
        LIMIT 4
    """, (stock_code,))
    last_4q = cur.fetchall()
    conn.close()

    result = {
        'stock_code': stock_code,
        'min_opm': stats['min_opm'],
        'avg_opm': stats['avg_opm'],
        'max_opm': stats['max_opm'],
        'q_count': stats['q_count'],
    }

    if last_4q and len(last_4q) >= 4:
        revenue_sum = sum(r['revenue'] for r in last_4q if r['revenue'])
        if revenue_sum > 0:
            result['ttm_revenue'] = revenue_sum

        eps_values = [r['eps'] for r in last_4q if r['eps'] is not None]
        if len(eps_values) == 4:
            ttm_eps = sum(eps_values)
            if ttm_eps > 0:
                result['ttm_eps'] = ttm_eps

    return result


def get_segment_avg_per(stock_code: str) -> Optional[Dict]:
    """이 종목이 속한 세그먼트의 동종 PER 시총 가중 평균"""
    try:
        from valuechain import get_all_segments, get_market_cap_vc as get_market_cap
    except ImportError:
        try:
            from valuechain import get_all_segments, get_market_cap
        except ImportError:
            return None

    matched_segments = []
    for theme_id, layer_id, seg_id, seg_data in get_all_segments():
        all_stocks = (seg_data.get('stocks_kr', []) or []) + \
                     (seg_data.get('stocks_us', []) or [])
        if stock_code in all_stocks:
            matched_segments.append((theme_id, layer_id, seg_id, all_stocks))

    if not matched_segments:
        return None

    peer_pers = []
    peer_caps = []

    for theme_id, layer_id, seg_id, peers in matched_segments:
        for peer in peers:
            if peer == stock_code:
                continue
            band = get_valuation_band(peer)
            if not band or not band.get('per_current'):
                continue
            per_val = band['per_current']
            if not (0 < per_val < 100):
                continue
            cap = get_market_cap(peer)
            if not cap or cap <= 0:
                continue
            peer_pers.append(per_val)
            peer_caps.append(cap)

    if not peer_pers:
        return None

    weighted_avg = sum(p * c for p, c in zip(peer_pers, peer_caps)) / sum(peer_caps)
    return {
        'avg_per': weighted_avg,
        'peer_count': len(peer_pers),
        'segments': [s[2] for s in matched_segments],
    }


# ============================================================
# 2. EPS 시나리오
# ============================================================

def calc_base_eps(quarterly: Optional[Dict], consensus: Optional[Dict]) -> Optional[Dict]:
    """1) 컨센서스 fwd_eps_1y → 2) TTM EPS"""
    if consensus and consensus.get('fwd_eps_1y'):
        try:
            v = float(consensus['fwd_eps_1y'])
            if v > 0:
                return {'value': v, 'source': 'CONSENSUS'}
        except (TypeError, ValueError):
            pass

    if quarterly and quarterly.get('ttm_eps'):
        try:
            v = float(quarterly['ttm_eps'])
            if v > 0:
                return {'value': v, 'source': 'TTM_EPS'}
        except (TypeError, ValueError):
            pass

    return None


def calc_bear_eps(quarterly: Optional[Dict], consensus: Optional[Dict],
                  base_eps_value: Optional[float]) -> Optional[Dict]:
    """1) 컨센서스 ×0.7 → 2) Base ×0.7"""
    if consensus and consensus.get('fwd_eps_1y'):
        try:
            v = float(consensus['fwd_eps_1y']) * 0.7
            if v > 0:
                return {'value': v, 'source': 'CONSENSUS_×0.7'}
        except (TypeError, ValueError):
            pass

    if base_eps_value:
        return {'value': base_eps_value * 0.7, 'source': 'BASE_×0.7'}

    return None


def calc_bull_eps(consensus: Optional[Dict],
                  base_eps_value: Optional[float]) -> Optional[Dict]:
    """1) 컨센서스 high → 2) Base ×1.2"""
    if consensus and consensus.get('fwd_eps_1y_high'):
        try:
            v = float(consensus['fwd_eps_1y_high'])
            if v > 0:
                return {'value': v, 'source': 'CONSENSUS_HIGH'}
        except (TypeError, ValueError):
            pass

    if base_eps_value:
        return {'value': base_eps_value * 1.2, 'source': 'BASE_×1.2'}

    return None


# ============================================================
# 3. PER 시나리오
# ============================================================

def calc_per_scenarios(band: Optional[Dict],
                       segment_avg: Optional[Dict]) -> Dict:
    result = {}

    if band:
        if band.get('per_p25') and band['per_p25'] > 0:
            result['bear'] = {'value': float(band['per_p25']), 'source': '5Y_P25'}
        if band.get('per_p50') and band['per_p50'] > 0:
            result['base'] = {'value': float(band['per_p50']), 'source': '5Y_P50'}

    if segment_avg and segment_avg.get('avg_per'):
        result['bull'] = {
            'value': float(segment_avg['avg_per']) * 1.1,
            'source': f"PEER_AVG×1.1 ({segment_avg['peer_count']}개)",
        }
    elif band and band.get('per_p75'):
        result['bull'] = {
            'value': float(band['per_p75']),
            'source': '5Y_P75 (peer N/A)',
        }

    return result


# ============================================================
# 4. PBR 폴백
# ============================================================

def calc_pbr_based_tp(stock_code: str, band: Optional[Dict]) -> Optional[Dict]:
    if not band or not band.get('pbr_p50'):
        return None

    conn = _get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT total_equity FROM financial_quarterly
        WHERE stock_code = ? AND total_equity > 0
        ORDER BY year DESC, quarter DESC LIMIT 1
    """, (stock_code,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    equity = row['total_equity']

    cur.execute("SELECT shares_outstanding FROM financial WHERE code = ?", (stock_code,))
    row = cur.fetchone()
    conn.close()

    if not row or not row['shares_outstanding']:
        return None

    try:
        shares = float(row['shares_outstanding'])
    except (TypeError, ValueError):
        return None

    if shares <= 0:
        return None

    bps = equity / shares

    return {
        'method': 'PBR_BASED',
        'bps': bps,
        'bear_tp': bps * (band.get('pbr_p25') or 0),
        'base_tp': bps * (band.get('pbr_p50') or 0),
        'bull_tp': bps * (band.get('pbr_p75') or 0),
        'note': '적자/EPS 부재 → PBR 기반 산출',
    }


# ============================================================
# 5. US 폴백
# ============================================================

def build_us_fallback_tam(stock_code: str, consensus: Optional[Dict]) -> Optional[Dict]:
    if not consensus or not consensus.get('fwd_eps_1y'):
        return None

    base_eps = float(consensus['fwd_eps_1y'])
    bull_eps_val = consensus.get('fwd_eps_1y_high')
    bull_eps = float(bull_eps_val) if bull_eps_val and bull_eps_val > 0 else base_eps * 1.2
    bear_eps = base_eps * 0.7

    trailing_pe = None
    forward_pe = None
    try:
        import yfinance as yf
        info = yf.Ticker(stock_code).info
        if info:
            tp = info.get('trailingPE')
            fp = info.get('forwardPE')
            if tp and 0 < tp < 200:
                trailing_pe = float(tp)
            if fp and 0 < fp < 200:
                forward_pe = float(fp)
    except Exception:
        pass

    base_per = forward_pe or trailing_pe
    if not base_per:
        return None

    bear_per = base_per * 0.7
    bull_per = base_per * 1.3
    per_label = 'fwdPER' if forward_pe else 'trailPER'

    bull_eps_source = 'CONSENSUS_HIGH' if (bull_eps_val and bull_eps_val > 0) else 'BASE_×1.2'

    return {
        'method': 'US_PER_FALLBACK',
        'bear': {
            'eps': bear_eps,
            'eps_source': 'CONSENSUS_×0.7',
            'per': bear_per,
            'per_source': f"{per_label}×0.7",
            'tp': bear_eps * bear_per,
        },
        'base': {
            'eps': base_eps,
            'eps_source': 'CONSENSUS',
            'per': base_per,
            'per_source': per_label,
            'tp': base_eps * base_per,
        },
        'bull': {
            'eps': bull_eps,
            'eps_source': bull_eps_source,
            'per': bull_per,
            'per_source': f"{per_label}×1.3",
            'tp': bull_eps * bull_per,
        },
        'note': 'US 5년 밴드 부재 → yfinance trailing/forward PER 기반',
    }


# ============================================================
# 6. 메인
# ============================================================

def build_auto_tam(stock_code: str) -> Dict:
    band = get_valuation_band(stock_code)
    consensus = get_consensus(stock_code)
    quarterly = get_quarterly_metrics(stock_code)
    segment_avg = get_segment_avg_per(stock_code)

    result = {
        'stock_code': stock_code,
        'data_availability': {
            'band': band is not None,
            'consensus': consensus is not None,
            'quarterly': quarterly is not None,
            'peer_avg': segment_avg is not None,
        },
        'consensus_tp': consensus.get('target_price_avg') if consensus else None,
    }

    # === US 폴백 분기 ===
    if not is_kr_stock(stock_code) and not band:
        us_tam = build_us_fallback_tam(stock_code, consensus)
        if us_tam:
            return {**result, **us_tam}
        else:
            return {**result, 'method': 'INSUFFICIENT_DATA',
                    'note': 'US 종목 PER 정보 부재'}

    # === Base EPS 산출 ===
    base_eps_data = calc_base_eps(quarterly, consensus)

    if not base_eps_data:
        pbr_result = calc_pbr_based_tp(stock_code, band)
        if pbr_result:
            return {**result, **pbr_result,
                    'bear': {'tp': pbr_result['bear_tp'], 'method': 'PBR'},
                    'base': {'tp': pbr_result['base_tp'], 'method': 'PBR',
                             'bps': pbr_result['bps']},
                    'bull': {'tp': pbr_result['bull_tp'], 'method': 'PBR'}}
        return {**result, 'method': 'INSUFFICIENT_DATA',
                'note': 'EPS 및 PBR 모두 산출 불가'}

    base_eps = base_eps_data['value']

    bear_eps_data = calc_bear_eps(quarterly, consensus, base_eps) or \
                    {'value': base_eps * 0.7, 'source': 'BASE_×0.7'}
    bull_eps_data = calc_bull_eps(consensus, base_eps) or \
                    {'value': base_eps * 1.2, 'source': 'BASE_×1.2'}

    # === PER 산출 ===
    per_scenarios = calc_per_scenarios(band, segment_avg)

    if 'base' not in per_scenarios:
        pbr_result = calc_pbr_based_tp(stock_code, band)
        if pbr_result:
            return {**result, **pbr_result,
                    'bear': {'tp': pbr_result['bear_tp'], 'method': 'PBR'},
                    'base': {'tp': pbr_result['base_tp'], 'method': 'PBR',
                             'bps': pbr_result['bps']},
                    'bull': {'tp': pbr_result['bull_tp'], 'method': 'PBR'}}
        return {**result, 'method': 'INSUFFICIENT_DATA',
                'note': '5년 PER 밴드 부재'}

    bear_per = per_scenarios.get('bear', per_scenarios['base'])
    base_per = per_scenarios['base']
    bull_per = per_scenarios.get('bull', per_scenarios['base'])

    return {
        **result,
        'method': 'PER_BASED',
        'bear': {
            'eps': bear_eps_data['value'],
            'eps_source': bear_eps_data['source'],
            'per': bear_per['value'],
            'per_source': bear_per['source'],
            'tp': bear_eps_data['value'] * bear_per['value'],
        },
        'base': {
            'eps': base_eps_data['value'],
            'eps_source': base_eps_data['source'],
            'per': base_per['value'],
            'per_source': base_per['source'],
            'tp': base_eps_data['value'] * base_per['value'],
        },
        'bull': {
            'eps': bull_eps_data['value'],
            'eps_source': bull_eps_data['source'],
            'per': bull_per['value'],
            'per_source': bull_per['source'],
            'tp': bull_eps_data['value'] * bull_per['value'],
        },
    }


def build_auto_tam_with_label(stock_code: str) -> Dict:
    """build_auto_tam + Upside 계산 + 컨센서스 갭"""
    tam = build_auto_tam(stock_code)

    if tam.get('method') == 'INSUFFICIENT_DATA':
        return tam

    # 현재가 (ohlcv.code)
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("SELECT close FROM ohlcv WHERE code = ? ORDER BY date DESC LIMIT 1",
                (stock_code,))
    row = cur.fetchone()
    conn.close()
    current_price = row['close'] if row else None

    # US 종목은 ohlcv 없음 → yfinance 폴백
    if not current_price and not is_kr_stock(stock_code):
        try:
            import yfinance as yf
            info = yf.Ticker(stock_code).info
            current_price = info.get('currentPrice') or info.get('regularMarketPrice')
        except Exception:
            pass

    tam['current_price'] = current_price

    if current_price:
        for scenario in ('bear', 'base', 'bull'):
            if scenario in tam and tam[scenario].get('tp'):
                tp = tam[scenario]['tp']
                tam[scenario]['upside_pct'] = (tp - current_price) / current_price * 100

        if tam.get('consensus_tp'):
            ct = tam['consensus_tp']
            tam['consensus_upside_pct'] = (ct - current_price) / current_price * 100
            if tam.get('base', {}).get('tp'):
                tam['gap_consensus_vs_base_pct'] = \
                    (tam['base']['tp'] - ct) / ct * 100

    return tam


# ============================================================
# 7. CLI
# ============================================================

if __name__ == '__main__':
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == 'all':
        from valuechain import get_all_stocks_in_map
        kr, us = get_all_stocks_in_map()
        all_codes = kr + us

        results = []
        for code in all_codes:
            tam = build_auto_tam_with_label(code)
            results.append(tam)

        per_based = sum(1 for r in results if r.get('method') == 'PER_BASED')
        pbr_based = sum(1 for r in results if r.get('method') == 'PBR_BASED')
        us_fallback = sum(1 for r in results if r.get('method') == 'US_PER_FALLBACK')
        insufficient = sum(1 for r in results if r.get('method') == 'INSUFFICIENT_DATA')

        print(f"\n📊 TAM 산출 결과 ({len(results)}종목)")
        print(f"  PER 기반 (KR): {per_based}")
        print(f"  PBR 기반 (적자): {pbr_based}")
        print(f"  US 폴백:        {us_fallback}")
        print(f"  데이터 부족:    {insufficient}")

        valid = [r for r in results
                 if r.get('method') in ('PER_BASED', 'US_PER_FALLBACK')
                 and r.get('base', {}).get('upside_pct') is not None]
        valid.sort(key=lambda x: -x['base']['upside_pct'])

        print(f"\n🔥 Base 시나리오 Upside TOP 10:")
        for r in valid[:10]:
            cp = r.get('current_price') or 0
            tp = r['base']['tp']
            up = r['base']['upside_pct']
            print(f"  {r['stock_code']}: {cp:.0f} → {tp:.0f} ({up:+.1f}%) [{r['method']}]")

        print(f"\n📉 Base 시나리오 과열 BOTTOM 10:")
        for r in valid[-10:]:
            cp = r.get('current_price') or 0
            tp = r['base']['tp']
            up = r['base']['upside_pct']
            print(f"  {r['stock_code']}: {cp:.0f} → {tp:.0f} ({up:+.1f}%)")

    elif len(sys.argv) > 1:
        code = sys.argv[1]
        tam = build_auto_tam_with_label(code)
        print(json.dumps(tam, indent=2, ensure_ascii=False, default=str))
    else:
        print("Usage:")
        print("  python3 tam_modeler.py 007660    # KR")
        print("  python3 tam_modeler.py NVDA      # US")
        print("  python3 tam_modeler.py all       # 전체 + TOP/BOTTOM")
