"""
5년 밸류에이션 밴드 + 오버행 보정 자동 계산
Step 4-2-B

[수정사항 v2]
- ohlcv 테이블 컬럼명: code (NOT stock_code)
- shares_outstanding: financial_quarterly 모두 NULL이라 financial 테이블 폴백 사용
"""

import sqlite3
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Tuple

DB_PATH = Path(__file__).parent / 'db' / 'dashboard.db'


def _get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ============================================================
# 1. 분기 말일 종가 조회 (ohlcv.code 사용)
# ============================================================

def get_quarter_end_close(stock_code: str, year: int, quarter: int) -> Optional[float]:
    """분기 말일 종가 (없으면 그 이전 가장 가까운 거래일)"""
    quarter_end_dates = {
        1: f"{year}-03-31",
        2: f"{year}-06-30",
        3: f"{year}-09-30",
        4: f"{year}-12-31",
    }
    target_date = quarter_end_dates[quarter]

    conn = _get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT close FROM ohlcv
        WHERE code = ? AND date <= ?
        ORDER BY date DESC LIMIT 1
    """, (stock_code, target_date))
    row = cur.fetchone()
    conn.close()
    return row['close'] if row and row['close'] else None


def get_shares_outstanding(stock_code: str, year: int = None, quarter: int = None) -> Optional[float]:
    """발행주식수 — financial_quarterly 우선, 없으면 financial 테이블 폴백"""
    conn = _get_db()
    cur = conn.cursor()

    if year and quarter:
        cur.execute("""
            SELECT shares_outstanding FROM financial_quarterly
            WHERE stock_code = ? AND year = ? AND quarter = ? AND shares_outstanding IS NOT NULL
        """, (stock_code, year, quarter))
        row = cur.fetchone()
        if row and row['shares_outstanding']:
            conn.close()
            return float(row['shares_outstanding'])

    cur.execute("SELECT shares_outstanding FROM financial WHERE code = ?", (stock_code,))
    row = cur.fetchone()
    conn.close()
    if row and row['shares_outstanding']:
        try:
            return float(row['shares_outstanding'])
        except (TypeError, ValueError):
            return None
    return None


# ============================================================
# 2. TTM 계산 (Trailing Twelve Months)
# ============================================================

def get_ttm_metrics(stock_code: str, end_year: int, end_quarter: int) -> Optional[Dict]:
    """직전 4분기 합계 (revenue, op, ni, ebitda) — TTM"""
    quarters = []
    y, q = end_year, end_quarter
    for _ in range(4):
        quarters.append((y, q))
        q -= 1
        if q == 0:
            q = 4
            y -= 1

    conn = _get_db()
    cur = conn.cursor()
    placeholders = ','.join(['(?,?)'] * 4)
    flat_params = [stock_code] + [v for tup in quarters for v in tup]

    cur.execute(f"""
        SELECT year, quarter, revenue, operating_profit, net_income, ebitda
        FROM financial_quarterly
        WHERE stock_code = ? AND (year, quarter) IN ({placeholders})
    """, flat_params)

    rows = cur.fetchall()
    conn.close()

    if len(rows) < 4:
        return None

    return {
        'ttm_revenue': sum((r['revenue'] or 0) for r in rows),
        'ttm_op': sum((r['operating_profit'] or 0) for r in rows),
        'ttm_ni': sum((r['net_income'] or 0) for r in rows),
        'ttm_ebitda': sum((r['ebitda'] or 0) for r in rows),
    }


# ============================================================
# 3. PER 5년 밴드
# ============================================================

def calculate_per_band(stock_code: str) -> Optional[Dict]:
    """5년 PER 밴드: 각 분기 말일 시점의 (종가 × 발행주식수) / TTM 순이익"""
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT year, quarter FROM financial_quarterly
        WHERE stock_code = ? AND revenue IS NOT NULL
        ORDER BY year DESC, quarter DESC
    """, (stock_code,))
    quarters = [(r['year'], r['quarter']) for r in cur.fetchall()]
    conn.close()

    if len(quarters) < 8:
        return None

    per_history = []
    for y, q in quarters:
        ttm = get_ttm_metrics(stock_code, y, q)
        if not ttm or ttm['ttm_ni'] <= 0:
            continue
        close = get_quarter_end_close(stock_code, y, q)
        shares = get_shares_outstanding(stock_code, y, q)
        if not close or not shares:
            continue
        market_cap_at_q = close * shares
        per = market_cap_at_q / ttm['ttm_ni']
        if 0 < per < 200:
            per_history.append(per)

    if len(per_history) < 4:
        return None

    arr = np.array(per_history)
    return {
        'per_p10': float(np.percentile(arr, 10)),
        'per_p25': float(np.percentile(arr, 25)),
        'per_p50': float(np.percentile(arr, 50)),
        'per_p75': float(np.percentile(arr, 75)),
        'per_p90': float(np.percentile(arr, 90)),
        'per_min': float(arr.min()),
        'per_max': float(arr.max()),
        'quarters_covered': len(per_history),
    }


# ============================================================
# 4. EV/EBITDA 5년 밴드 (오버행 보정)
# ============================================================

def get_active_overhang(stock_code: str, ref_date: str = None) -> float:
    """활성 오버행 물량 가치"""
    if ref_date is None:
        ref_date = datetime.now().strftime('%Y-%m-%d')

    conn = _get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT potential_shares, conversion_price
        FROM dart_disclosure_overhang
        WHERE stock_code = ? AND status = 'ACTIVE'
              AND (exercise_end_date IS NULL OR exercise_end_date >= ?)
    """, (stock_code, ref_date))
    rows = cur.fetchall()
    conn.close()

    total_value = 0
    for r in rows:
        if r['potential_shares'] and r['conversion_price']:
            total_value += r['potential_shares'] * r['conversion_price']
    return total_value


def calculate_ev_ebitda_band(stock_code: str) -> Optional[Dict]:
    """5년 EV/EBITDA 밴드 (오버행 포함)"""
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT year, quarter, net_debt FROM financial_quarterly
        WHERE stock_code = ? AND ebitda IS NOT NULL
        ORDER BY year DESC, quarter DESC
    """, (stock_code,))
    quarters = [(r['year'], r['quarter'], r['net_debt']) for r in cur.fetchall()]
    conn.close()

    if len(quarters) < 8:
        return None

    overhang = get_active_overhang(stock_code)
    ev_ebitda_history = []

    for y, q, net_debt in quarters:
        ttm = get_ttm_metrics(stock_code, y, q)
        if not ttm or ttm['ttm_ebitda'] <= 0:
            continue
        close = get_quarter_end_close(stock_code, y, q)
        shares = get_shares_outstanding(stock_code, y, q)
        if not close or not shares:
            continue
        market_cap_at_q = close * shares
        ev = market_cap_at_q + (net_debt or 0) + overhang
        ev_ebitda = ev / ttm['ttm_ebitda']
        if 0 < ev_ebitda < 100:
            ev_ebitda_history.append(ev_ebitda)

    if len(ev_ebitda_history) < 4:
        return None

    arr = np.array(ev_ebitda_history)
    return {
        'ev_ebitda_p10': float(np.percentile(arr, 10)),
        'ev_ebitda_p25': float(np.percentile(arr, 25)),
        'ev_ebitda_p50': float(np.percentile(arr, 50)),
        'ev_ebitda_p75': float(np.percentile(arr, 75)),
        'ev_ebitda_p90': float(np.percentile(arr, 90)),
        'ev_ebitda_min': float(arr.min()),
        'ev_ebitda_max': float(arr.max()),
    }


# ============================================================
# 5. PBR 5년 밴드
# ============================================================

def calculate_pbr_band(stock_code: str) -> Optional[Dict]:
    """5년 PBR 밴드"""
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT year, quarter, total_equity FROM financial_quarterly
        WHERE stock_code = ? AND total_equity > 0
        ORDER BY year DESC, quarter DESC
    """, (stock_code,))
    rows = cur.fetchall()
    conn.close()

    pbr_history = []
    for r in rows:
        close = get_quarter_end_close(stock_code, r['year'], r['quarter'])
        shares = get_shares_outstanding(stock_code, r['year'], r['quarter'])
        if not close or not shares:
            continue
        market_cap_at_q = close * shares
        pbr = market_cap_at_q / r['total_equity']
        if 0 < pbr < 50:
            pbr_history.append(pbr)

    if len(pbr_history) < 4:
        return None

    arr = np.array(pbr_history)
    return {
        'pbr_p25': float(np.percentile(arr, 25)),
        'pbr_p50': float(np.percentile(arr, 50)),
        'pbr_p75': float(np.percentile(arr, 75)),
    }


# ============================================================
# 6. 현재 밸류에이션 + 백분위
# ============================================================

def calculate_current_metrics(stock_code: str) -> Dict:
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT year, quarter FROM financial_quarterly
        WHERE stock_code = ? AND revenue IS NOT NULL
        ORDER BY year DESC, quarter DESC LIMIT 1
    """, (stock_code,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return {}
    latest_year, latest_quarter = row['year'], row['quarter']

    cur.execute("""
        SELECT close FROM ohlcv WHERE code = ? ORDER BY date DESC LIMIT 1
    """, (stock_code,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return {}
    current_price = row['close']

    cur.execute("""
        SELECT net_debt, total_equity FROM financial_quarterly
        WHERE stock_code = ? AND year = ? AND quarter = ?
    """, (stock_code, latest_year, latest_quarter))
    row = cur.fetchone()
    conn.close()

    net_debt = row['net_debt'] if row else 0
    equity = row['total_equity'] if row else None

    shares = get_shares_outstanding(stock_code)
    ttm = get_ttm_metrics(stock_code, latest_year, latest_quarter)
    overhang = get_active_overhang(stock_code)

    if not shares or not ttm:
        return {}

    market_cap = current_price * shares
    result = {'market_cap_current': market_cap}
    if ttm['ttm_ni'] and ttm['ttm_ni'] > 0:
        result['per_current'] = market_cap / ttm['ttm_ni']
    if ttm['ttm_ebitda'] and ttm['ttm_ebitda'] > 0:
        result['ev_ebitda_current'] = (market_cap + (net_debt or 0) + overhang) / ttm['ttm_ebitda']
    if equity and equity > 0:
        result['pbr_current'] = market_cap / equity
    return result


def percentile_rank(value: float, p10, p25, p50, p75, p90) -> float:
    """값이 백분위 기준 어느 위치인지 (선형 보간)"""
    if value is None or p50 is None:
        return 50.0
    if value <= p10:
        return max(0, value / p10 * 10) if p10 > 0 else 0
    if value <= p25:
        return 10 + (value - p10) / (p25 - p10) * 15
    if value <= p50:
        return 25 + (value - p25) / (p50 - p25) * 25
    if value <= p75:
        return 50 + (value - p50) / (p75 - p50) * 25
    if value <= p90:
        return 75 + (value - p75) / (p90 - p75) * 15
    return min(100, 90 + (value - p90) / (p90 * 0.5) * 10)


# ============================================================
# 7. 저장
# ============================================================

def save_valuation_band(stock_code: str) -> bool:
    per_band = calculate_per_band(stock_code)
    ev_band = calculate_ev_ebitda_band(stock_code)
    pbr_band = calculate_pbr_band(stock_code)
    current = calculate_current_metrics(stock_code)

    if not per_band and not ev_band and not pbr_band:
        return False

    per_pct = ev_pct = pbr_pct = None
    if per_band and current.get('per_current'):
        per_pct = percentile_rank(
            current['per_current'],
            per_band['per_p10'], per_band['per_p25'],
            per_band['per_p50'], per_band['per_p75'], per_band['per_p90']
        )
    if ev_band and current.get('ev_ebitda_current'):
        ev_pct = percentile_rank(
            current['ev_ebitda_current'],
            ev_band['ev_ebitda_p10'], ev_band['ev_ebitda_p25'],
            ev_band['ev_ebitda_p50'], ev_band['ev_ebitda_p75'], ev_band['ev_ebitda_p90']
        )
    if pbr_band and current.get('pbr_current'):
        if current['pbr_current'] <= pbr_band['pbr_p25']:
            pbr_pct = 25.0
        elif current['pbr_current'] <= pbr_band['pbr_p50']:
            pbr_pct = 50.0
        elif current['pbr_current'] <= pbr_band['pbr_p75']:
            pbr_pct = 75.0
        else:
            pbr_pct = 90.0

    quarters_covered = (per_band or ev_band or pbr_band or {}).get('quarters_covered', 0)

    conn = _get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO valuation_band (
            stock_code,
            per_p10, per_p25, per_p50, per_p75, per_p90, per_min, per_max,
            per_current, per_current_pct,
            ev_ebitda_p10, ev_ebitda_p25, ev_ebitda_p50, ev_ebitda_p75, ev_ebitda_p90,
            ev_ebitda_min, ev_ebitda_max,
            ev_ebitda_current, ev_ebitda_current_pct,
            pbr_p25, pbr_p50, pbr_p75,
            pbr_current, pbr_current_pct,
            quarters_covered, calculated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
    """, (
        stock_code,
        per_band.get('per_p10') if per_band else None,
        per_band.get('per_p25') if per_band else None,
        per_band.get('per_p50') if per_band else None,
        per_band.get('per_p75') if per_band else None,
        per_band.get('per_p90') if per_band else None,
        per_band.get('per_min') if per_band else None,
        per_band.get('per_max') if per_band else None,
        current.get('per_current'),
        per_pct,
        ev_band.get('ev_ebitda_p10') if ev_band else None,
        ev_band.get('ev_ebitda_p25') if ev_band else None,
        ev_band.get('ev_ebitda_p50') if ev_band else None,
        ev_band.get('ev_ebitda_p75') if ev_band else None,
        ev_band.get('ev_ebitda_p90') if ev_band else None,
        ev_band.get('ev_ebitda_min') if ev_band else None,
        ev_band.get('ev_ebitda_max') if ev_band else None,
        current.get('ev_ebitda_current'),
        ev_pct,
        pbr_band.get('pbr_p25') if pbr_band else None,
        pbr_band.get('pbr_p50') if pbr_band else None,
        pbr_band.get('pbr_p75') if pbr_band else None,
        current.get('pbr_current'),
        pbr_pct,
        quarters_covered
    ))

    cur.execute("""
        UPDATE fetch_progress
        SET valuation_band_status = 'DONE',
            valuation_band_calculated_at = datetime('now', 'localtime')
        WHERE stock_code = ?
    """, (stock_code,))
    if cur.rowcount == 0:
        cur.execute("""
            INSERT INTO fetch_progress (stock_code, valuation_band_status, valuation_band_calculated_at)
            VALUES (?, 'DONE', datetime('now', 'localtime'))
        """, (stock_code,))

    conn.commit()
    conn.close()
    return True


def calculate_all_bands():
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT stock_code FROM financial_quarterly")
    stocks = [r['stock_code'] for r in cur.fetchall()]
    conn.close()

    print(f"📊 {len(stocks)}종목 밸류에이션 밴드 계산 시작")
    success, fail, no_data = 0, 0, 0
    for code in stocks:
        try:
            if save_valuation_band(code):
                success += 1
            else:
                no_data += 1
        except Exception as e:
            print(f"  ⚠ {code} 실패: {e}")
            fail += 1
    print(f"\n✅ 완료: 성공 {success} / 데이터부족 {no_data} / 실패 {fail}")


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'all':
        calculate_all_bands()
    elif len(sys.argv) > 1:
        code = sys.argv[1]
        result = save_valuation_band(code)
        print(f"{code}: {'OK' if result else 'FAIL'}")
        conn = _get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM valuation_band WHERE stock_code = ?", (code,))
        row = cur.fetchone()
        if row:
            for k in row.keys():
                v = row[k]
                if isinstance(v, float):
                    print(f"  {k}: {v:.2f}")
                else:
                    print(f"  {k}: {v}")
        conn.close()
    else:
        print("Usage:")
        print("  python3 valuation_calculator.py 007660")
        print("  python3 valuation_calculator.py all")
