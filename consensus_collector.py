"""
컨센서스 추정 수집기 (옵션 A — 단순화)
Step 4-3-B

소스:
1) KR: 네이버 main.naver 페이지 인라인 파싱 (estimate_per/eps만)
2) US: yfinance forwardEps + targetMeanPrice
3) 폴백: financial 테이블 (KR 신선도 낮지만 즉시 사용)

KR 목표주가는 main.naver에 없음 → consensus_estimate.target_price_avg는 US만 채움
KR TP는 4-3-D 검증 단계에서 별도 애널리스트 리포트 모듈로 분리
"""

import sqlite3
import re
import json
import time
import requests
from bs4 import BeautifulSoup
from pathlib import Path
from typing import Optional, Dict
from datetime import datetime

DB_PATH = Path(__file__).parent / 'db' / 'dashboard.db'
NAVER_DELAY = 1.0

NAVER_HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/120.0.0.0 Safari/537.36'),
    'Accept-Language': 'ko-KR,ko;q=0.9',
    'Referer': 'https://finance.naver.com/',
}


def _get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def is_kr_stock(code: str) -> bool:
    return bool(re.fullmatch(r'\d{6}', code))


def _is_valid(v, max_val=1e10):
    """이상값 자동 필터링: NULL, 0, 음수, ±998/±999 sentinel, 극단치"""
    if v is None:
        return False
    try:
        v = float(v)
    except (TypeError, ValueError):
        return False
    if v <= 0:
        return False
    if abs(v) in (998, 999):
        return False
    if v > max_val:
        return False
    return True


def _parse_num(s: str) -> Optional[float]:
    """문자열에서 첫 번째 숫자 추출 → float.
    '43.00배' → 43.0, '3,578원' → 3578.0, '(123)' → -123.
    server.py:3140 _num 패턴 (정규식 기반)."""
    if not s:
        return None
    text = str(s).replace('\n', '').replace(' ', '')
    if not text or text in ('-', 'N/A', '없음'):
        return None
    is_neg = text.startswith('(') and ')' in text
    m = re.search(r'[-+]?[\d,]+\.?\d*', text)
    if not m:
        return None
    try:
        v = float(m.group(0).replace(',', ''))
        return -abs(v) if is_neg else v
    except ValueError:
        return None


# ============================================================
# 1. KR 네이버 main.naver 컨센서스
# ============================================================
# server.py:3120-3270 패턴 복제 (table.per_table 파싱)
# - th '추정PER' → estimate_per, estimate_eps  (td는 'X배lY원' 형태 → 'l'로 분리)
# - th 'PER'     → per, eps
# - th 'PBR'     → pbr, bps

def fetch_naver_consensus(stock_code: str) -> Optional[Dict]:
    url = "https://finance.naver.com/item/main.naver"

    try:
        resp = requests.get(url, params={'code': stock_code},
                            headers=NAVER_HEADERS, timeout=10)
        resp.encoding = 'utf-8'
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, 'lxml')
        table = soup.select_one('table.per_table')
        if not table:
            for t in soup.find_all('table'):
                if '추정PER' in t.get_text() or '추정 PER' in t.get_text():
                    table = t
                    break
        if not table:
            return None

        result = {}
        for tr in table.select('tr'):
            th = tr.select_one('th')
            td = tr.select_one('td')
            if not th or not td:
                continue
            th_text = th.get_text(strip=True)
            td_text = td.get_text(strip=True)
            # td 형식: 'X배lY원' (l = ASCII l, 구분자)
            parts = td_text.split('l')

            if '추정' in th_text and 'PER' in th_text:
                v0 = _parse_num(parts[0]) if len(parts) > 0 else None
                v1 = _parse_num(parts[1]) if len(parts) > 1 else None
                if _is_valid(v0, max_val=500):
                    result['estimate_per'] = v0
                if _is_valid(v1, max_val=1e7):
                    result['estimate_eps'] = v1
            elif th_text == 'PER' or (th_text.startswith('PER') and '추정' not in th_text):
                v0 = _parse_num(parts[0]) if len(parts) > 0 else None
                v1 = _parse_num(parts[1]) if len(parts) > 1 else None
                if _is_valid(v0, max_val=500):
                    result['per'] = v0
                if _is_valid(v1, max_val=1e7):
                    result['eps'] = v1

        normalized = {'source': 'NAVER'}
        if 'estimate_eps' in result:
            normalized['fwd_eps_1y'] = result['estimate_eps']
        elif 'eps' in result:
            normalized['fwd_eps_1y'] = result['eps']

        return normalized if 'fwd_eps_1y' in normalized else None
    except Exception as e:
        print(f"  ⚠ {stock_code} 네이버 실패: {e}")
        return None


# ============================================================
# 2. US yfinance 컨센서스
# ============================================================

def fetch_yfinance_consensus(ticker: str) -> Optional[Dict]:
    """server.py:1840-1880 패턴 복제"""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        if not info:
            return None

        result = {'source': 'YFINANCE'}

        if _is_valid(info.get('forwardEps'), max_val=10000):
            result['fwd_eps_1y'] = float(info['forwardEps'])
        if _is_valid(info.get('targetMeanPrice'), max_val=1e7):
            result['target_price_avg'] = float(info['targetMeanPrice'])
        if _is_valid(info.get('targetHighPrice'), max_val=1e7):
            result['target_price_high'] = float(info['targetHighPrice'])
        if _is_valid(info.get('targetLowPrice'), max_val=1e7):
            result['target_price_low'] = float(info['targetLowPrice'])
        if _is_valid(info.get('numberOfAnalystOpinions'), max_val=100):
            count = int(info['numberOfAnalystOpinions'])
            result['target_price_count'] = count
            result['fwd_eps_1y_count'] = count

        rec_mean = info.get('recommendationMean')
        if _is_valid(rec_mean, max_val=10) and 'target_price_count' in result:
            count = result['target_price_count']
            mean = float(rec_mean)
            if mean < 2.5:
                result['rec_buy_count'] = int(count * 0.7)
                result['rec_hold_count'] = int(count * 0.25)
            elif mean < 3.5:
                result['rec_buy_count'] = int(count * 0.4)
                result['rec_hold_count'] = int(count * 0.5)
            else:
                result['rec_hold_count'] = int(count * 0.6)
                result['rec_sell_count'] = int(count * 0.3)

        return result if any(k in result for k in ('fwd_eps_1y', 'target_price_avg')) else None
    except Exception as e:
        print(f"  ⚠ {ticker} yfinance 실패: {e}")
        return None


# ============================================================
# 3. financial 테이블 폴백
# ============================================================

def fetch_financial_table_fallback(stock_code: str) -> Optional[Dict]:
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT estimate_per, estimate_eps, per, eps
        FROM financial WHERE code = ?
    """, (stock_code,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None

    result = {'source': 'FINANCIAL_TABLE'}
    if _is_valid(row['estimate_eps'], max_val=1e7):
        result['fwd_eps_1y'] = float(row['estimate_eps'])
    elif _is_valid(row['eps'], max_val=1e7):
        result['fwd_eps_1y'] = float(row['eps'])

    return result if 'fwd_eps_1y' in result else None


# ============================================================
# 4. DB 저장
# ============================================================

def save_consensus(stock_code: str, data: Dict) -> bool:
    if not data:
        return False

    conn = _get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO consensus_estimate (
            stock_code,
            fwd_eps_1y, fwd_eps_1y_high, fwd_eps_1y_low, fwd_eps_1y_count,
            fwd_eps_2y, fwd_revenue_1y,
            target_price_avg, target_price_high, target_price_low, target_price_count,
            rec_buy_count, rec_hold_count, rec_sell_count,
            source, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
    """, (
        stock_code,
        data.get('fwd_eps_1y'), data.get('fwd_eps_1y_high'), data.get('fwd_eps_1y_low'),
        data.get('fwd_eps_1y_count'),
        data.get('fwd_eps_2y'), data.get('fwd_revenue_1y'),
        data.get('target_price_avg'), data.get('target_price_high'), data.get('target_price_low'),
        data.get('target_price_count'),
        data.get('rec_buy_count'), data.get('rec_hold_count'), data.get('rec_sell_count'),
        data.get('source')
    ))

    cur.execute("""
        INSERT INTO fetch_progress (stock_code, consensus_status, consensus_last_fetch)
        VALUES (?, 'DONE', datetime('now', 'localtime'))
        ON CONFLICT(stock_code) DO UPDATE SET
            consensus_status = 'DONE',
            consensus_last_fetch = datetime('now', 'localtime')
    """, (stock_code,))

    conn.commit()
    conn.close()
    return True


# ============================================================
# 5. 메인
# ============================================================

def collect_consensus_for_stock(stock_code: str) -> Dict:
    result_data = None
    if is_kr_stock(stock_code):
        result_data = fetch_naver_consensus(stock_code)
        if not result_data:
            result_data = fetch_financial_table_fallback(stock_code)
    else:
        result_data = fetch_yfinance_consensus(stock_code)

    if not result_data:
        return {'code': stock_code, 'status': 'NO_DATA'}

    saved = save_consensus(stock_code, result_data)
    return {
        'code': stock_code,
        'status': 'DONE' if saved else 'FAILED',
        'source': result_data.get('source'),
        'fwd_eps_1y': result_data.get('fwd_eps_1y'),
        'target_price_avg': result_data.get('target_price_avg'),
    }


def collect_consensus_all():
    from valuechain import get_all_stocks_in_map
    kr_stocks, us_stocks = get_all_stocks_in_map()
    all_stocks = kr_stocks + us_stocks

    print(f"📊 {len(all_stocks)}종목 컨센서스 수집 (KR {len(kr_stocks)} + US {len(us_stocks)})")

    results = []
    counts = {'NAVER': 0, 'YFINANCE': 0, 'FINANCIAL_TABLE': 0, 'NO_DATA': 0, 'FAILED': 0}

    for i, code in enumerate(all_stocks):
        if i > 0 and is_kr_stock(code):
            time.sleep(NAVER_DELAY)
        r = collect_consensus_for_stock(code)
        results.append(r)
        if r['status'] == 'DONE':
            src = r.get('source', '')
            counts[src] = counts.get(src, 0) + 1
        elif r['status'] == 'NO_DATA':
            counts['NO_DATA'] += 1
        else:
            counts['FAILED'] += 1
        if (i + 1) % 10 == 0:
            done = sum(v for k, v in counts.items() if k not in ('NO_DATA', 'FAILED'))
            print(f"  진행: {i+1}/{len(all_stocks)} (성공 {done})")

    print(f"\n✅ 컨센서스 수집 완료")
    for src, n in counts.items():
        if n > 0:
            print(f"  {src}: {n}")
    return results


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'all':
        collect_consensus_all()
    elif len(sys.argv) > 1:
        code = sys.argv[1]
        result = collect_consensus_for_stock(code)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        conn = _get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM consensus_estimate WHERE stock_code = ?", (code,))
        row = cur.fetchone()
        if row:
            print("\n--- DB 저장 결과 ---")
            for k in row.keys():
                if row[k] is not None:
                    print(f"  {k}: {row[k]}")
        conn.close()
    else:
        print("Usage:")
        print("  python3 consensus_collector.py 007660    # KR")
        print("  python3 consensus_collector.py NVDA      # US")
        print("  python3 consensus_collector.py all")
