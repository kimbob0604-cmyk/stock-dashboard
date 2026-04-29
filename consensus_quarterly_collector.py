"""
Step 4-7-C: 분기 컨센서스 수집기

Naver `main.naver?code=XXX` 페이지의 tb_type1 표 (3번째)에서
추정 마커 `(E)`가 있는 분기 컨센서스만 추출 → consensus_quarterly 저장.

발표 실적 추출 책임은 Step 4-7-D (DART 1순위 + Naver 보조).

소스: NAVER_TB_TYPE1
유니버스: get_active_universe() (현재 valuechain_v2 KR 82)
빈도: 매주 월요일 06:00 (server.py 스케줄러)
"""

import re
import html as html_lib
import time
import json
import sqlite3
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

DB_PATH = Path(__file__).parent / 'db' / 'dashboard.db'

# 페이지 로드 안정성: 종목당 1초 (네이버 부담 최소)
REQUEST_DELAY = 1.0

NAVER_HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/120.0.0.0 Safari/537.36'),
    'Accept-Language': 'ko-KR,ko;q=0.9',
    'Referer': 'https://finance.naver.com/',
}

# 추출 대상 라벨 → 표준 필드 (5개)
TARGET_LABELS = {
    '매출액':       'revenue',         # 억원 → 백만원 (×100)
    '영업이익':     'operating_profit', # 억원 → 백만원 (×100)
    '당기순이익':   'net_income',       # 억원 → 백만원 (×100)
    'EPS(원)':      'eps',              # 원 (변환 X)
    '영업이익률':   'opm',              # % (변환 X)
}


def _get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_num(s: str) -> Optional[float]:
    """문자열 → float. 음수는 '-123' 형식 (네이버는 괄호 안 씀).
    빈 값/하이픈은 None."""
    if not s:
        return None
    text = str(s).replace('\xa0', ' ').replace(',', '').strip()
    if not text or text in ('-', 'N/A', '', '–'):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_quarter_header(header_text: str) -> Optional[Dict]:
    """'2025.03' / '2026.03(E)' → {year, quarter, is_estimate}
    분기 매핑: 03→1, 06→2, 09→3, 12→4."""
    text = html_lib.unescape(header_text or '').replace('\xa0', ' ').strip()
    is_estimate = '(E)' in text or '(P)' in text or '추정' in text
    m = re.match(r'(\d{4})\.(\d{2})', text)
    if not m:
        return None
    year = int(m.group(1))
    month = int(m.group(2))
    quarter_map = {3: 1, 6: 2, 9: 3, 12: 4}
    quarter = quarter_map.get(month)
    if quarter is None:
        return None
    return {'year': year, 'quarter': quarter, 'is_estimate': is_estimate, 'raw': text}


# ============================================================
# 1. 페이지 페치 + 파싱
# ============================================================

def fetch_naver_main(stock_code: str) -> Optional[str]:
    """main.naver UTF-8 디코딩 후 HTML 반환."""
    url = f'https://finance.naver.com/item/main.naver?code={stock_code}'
    req = urllib.request.Request(url, headers=NAVER_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
        for enc in ('utf-8', 'euc-kr', 'cp949'):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode('utf-8', errors='ignore')
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"  ⚠ {stock_code} fetch 실패: {e}")
        return None


def parse_tb_type1_table(html_text: str) -> Optional[Dict]:
    """tb_type1 표 4개 중 3번째 (주요재무정보) 파싱.
    Returns: {'columns': [{year, quarter, is_estimate}, ...], 'data': {label: [val, ...]}}
    분기 6컬럼 (idx 4~9 of cells[1:])만 처리. 연간 4컬럼은 무시.
    """
    tables = re.findall(
        r'<table[^>]*class="[^"]*tb_type1[^"]*"[^>]*>(.*?)</table>',
        html_text, re.DOTALL
    )
    if len(tables) < 3:
        return None

    table3 = tables[2]
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table3, re.DOTALL)
    if len(rows) < 4:  # 헤더 3 + 데이터 1 이상
        return None

    # 행 1 = 날짜 헤더
    h_cells = re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', rows[1], re.DOTALL)
    headers = []
    for c in h_cells:
        txt = re.sub(r'<[^>]+>', '', c)
        txt = html_lib.unescape(txt).replace('\xa0', ' ').strip()
        headers.append(txt)

    # 분기 6컬럼: 연간 4개(idx 0~3) 다음 분기 6개(idx 4~9)
    # 헤더 길이가 10이어야 정상
    if len(headers) < 10:
        return None

    quarter_columns = []
    for i in range(4, min(10, len(headers))):
        q = _parse_quarter_header(headers[i])
        if q:
            q['col_idx'] = i  # 데이터 행에서 cells[1:][i] 위치
            quarter_columns.append(q)

    if not quarter_columns:
        return None

    # 데이터 행 (행 2~) — 라벨별 값 추출
    data: Dict[str, List[Optional[float]]] = {}
    for row in rows[2:]:
        cells = re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', row, re.DOTALL)
        if len(cells) < 2:
            continue
        label = re.sub(r'<[^>]+>', '', cells[0])
        label = html_lib.unescape(label).replace('\xa0', ' ').strip()
        if label not in TARGET_LABELS:
            continue
        # 데이터 cells = cells[1:]
        values = []
        for v_cell in cells[1:]:
            v_text = re.sub(r'<[^>]+>', '', v_cell)
            v_text = html_lib.unescape(v_text).replace('\xa0', ' ').strip()
            values.append(_parse_num(v_text))
        data[TARGET_LABELS[label]] = values

    return {
        'quarter_columns': quarter_columns,
        'data': data,
    }


# ============================================================
# 2. DB 저장
# ============================================================

def save_consensus_quarterly(stock_code: str, parsed: Dict) -> int:
    """parsed 결과에서 (E) 컬럼만 consensus_quarterly에 저장."""
    if not parsed:
        return 0

    quarter_cols = parsed.get('quarter_columns') or []
    data = parsed.get('data') or {}
    estimate_cols = [q for q in quarter_cols if q.get('is_estimate')]
    if not estimate_cols:
        return 0

    now_iso = datetime.now().isoformat()
    saved = 0

    conn = _get_db()
    cur = conn.cursor()

    for q in estimate_cols:
        # 헤더 cells[i] ↔ 데이터 cells[1+i] = data list[i] (cells[1:]가 data list)
        # 헤더에는 라벨이 없고(<th>만 추출), 데이터 행에선 cells[0]=라벨이 빠진 cells[1:]가 data
        # 따라서 두 인덱스는 동일. col_idx 그대로 사용.
        idx = q['col_idx']

        def _at(arr_key):
            arr = data.get(arr_key) or []
            return arr[idx] if idx < len(arr) else None

        rev_eok = _at('revenue')
        op_eok  = _at('operating_profit')
        ni_eok  = _at('net_income')
        eps     = _at('eps')

        # 단위: 억원 → 백만원 (× 100)
        rev_mn = rev_eok * 100 if rev_eok is not None else None
        op_mn  = op_eok * 100 if op_eok is not None else None
        ni_mn  = ni_eok * 100 if ni_eok is not None else None

        cur.execute("""
            INSERT INTO consensus_quarterly (
                stock_code, year, quarter,
                revenue_consensus, op_consensus, ni_consensus, eps_consensus,
                source, collected_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'NAVER_TB_TYPE1', ?, ?)
            ON CONFLICT(stock_code, year, quarter) DO UPDATE SET
                revenue_consensus = excluded.revenue_consensus,
                op_consensus      = excluded.op_consensus,
                ni_consensus      = excluded.ni_consensus,
                eps_consensus     = excluded.eps_consensus,
                source            = excluded.source,
                updated_at        = excluded.updated_at
        """, (stock_code, q['year'], q['quarter'],
              rev_mn, op_mn, ni_mn, eps,
              now_iso, now_iso))
        saved += 1

    conn.commit()
    conn.close()
    return saved


# ============================================================
# 3. 메인
# ============================================================

def collect_one(stock_code: str, verbose: bool = False) -> Dict:
    """단일 종목 컨센서스 수집."""
    html_text = fetch_naver_main(stock_code)
    if not html_text:
        return {'code': stock_code, 'status': 'FETCH_FAIL'}

    parsed = parse_tb_type1_table(html_text)
    if not parsed:
        return {'code': stock_code, 'status': 'PARSE_FAIL'}

    saved = save_consensus_quarterly(stock_code, parsed)
    estimate_cols = [q for q in parsed['quarter_columns'] if q.get('is_estimate')]

    if verbose:
        for q in estimate_cols:
            idx = q['col_idx']
            d = parsed['data']
            def _at(k):
                arr = d.get(k) or []
                return arr[idx] if idx < len(arr) else None
            print(f"  {q['raw']:<12s} 매출 {_at('revenue')}억 / 영업익 {_at('operating_profit')}억 / EPS {_at('eps')}원")

    return {
        'code': stock_code,
        'status': 'OK' if saved > 0 else 'NO_ESTIMATE',
        'saved': saved,
        'quarters': [q['raw'] for q in estimate_cols],
    }


def collect_all() -> List[Dict]:
    """활성 유니버스 전체 수집."""
    from universe_manager import get_active_universe
    codes = get_active_universe()

    print(f"📊 컨센서스 분기 수집 시작 ({len(codes)}종목)")
    t0 = time.time()
    results = []
    counts = {'OK': 0, 'NO_ESTIMATE': 0, 'FETCH_FAIL': 0, 'PARSE_FAIL': 0}

    for i, code in enumerate(codes, 1):
        if i > 1:
            time.sleep(REQUEST_DELAY)
        r = collect_one(code)
        results.append(r)
        counts[r['status']] = counts.get(r['status'], 0) + 1

        if i % 10 == 0 or i == len(codes):
            elapsed = time.time() - t0
            print(f"  진행: {i}/{len(codes)}  OK={counts['OK']} ({elapsed:.0f}s)")

    elapsed = time.time() - t0
    print(f"\n✅ 완료: {elapsed:.1f}초")
    for k, v in counts.items():
        if v > 0:
            print(f"  {k}: {v}")
    return results


# ============================================================
# 4. CLI
# ============================================================

if __name__ == '__main__':
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == 'all':
        collect_all()
    elif len(sys.argv) > 1:
        code = sys.argv[1]
        r = collect_one(code, verbose=True)
        print(json.dumps(r, indent=2, ensure_ascii=False))
        # DB 저장 결과 출력
        conn = _get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT year, quarter, revenue_consensus, op_consensus,
                   ni_consensus, eps_consensus, source, updated_at
            FROM consensus_quarterly WHERE stock_code = ?
            ORDER BY year DESC, quarter DESC
        """, (code,))
        rows = cur.fetchall()
        print(f"\n--- DB 저장 결과 ({len(rows)}건) ---")
        for r in rows:
            rev_mn = r['revenue_consensus']
            op_mn = r['op_consensus']
            print(f"  {r['year']}Q{r['quarter']}: 매출 {rev_mn/100 if rev_mn else None}억 / 영업익 {op_mn/100 if op_mn else None}억 / EPS {r['eps_consensus']}원")
        conn.close()
    else:
        print("Usage:")
        print("  python3 consensus_quarterly_collector.py 007660")
        print("  python3 consensus_quarterly_collector.py all")
