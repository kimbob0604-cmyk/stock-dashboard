"""
Step 4-7-D-2: DART 잠정실적 본문 파서

처리 흐름:
1) DART list.json에서 잠정실적 키워드 매칭 공시 식별
2) document.xml zip → utf-8 디코딩
3) 정규식 파싱 (매출액/영업이익/당기순이익 + 당기/전기 값)
4) 4 분류: CONSOLIDATED_PRELIMINARY / STANDALONE_PRELIMINARY / SUBSIDIARY_PRELIMINARY / PRE_ANNOUNCE
5) earnings_actual (3종류) 또는 earnings_alert_queue (PRE_ANNOUNCE) 적재

단위: 백만원 (DART 표준, consensus_quarterly와 통일).
"""

import re
import os
import io
import json
import zipfile
import sqlite3
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict


def _load_dotenv(p: Path):
    if not p.exists(): return
    for raw in p.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        k, _, v = line.partition('=')
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(Path(__file__).parent / '.env')

DB_PATH = Path(__file__).parent / 'db' / 'dashboard.db'
DART_API_KEY = os.environ.get('DART_API_KEY', '')


# ============================================================
# 1. 키워드 분류
# ============================================================

EARNINGS_TYPES = {
    'CONSOLIDATED_PRELIMINARY': '연결재무제표기준영업(잠정)',
    'STANDALONE_PRELIMINARY':   '영업(잠정)실적',
    'SUBSIDIARY_PRELIMINARY':   '연결재무제표기준영업(잠정)실적(공정공시)(자회사',
    'PRE_ANNOUNCE':             '결산실적공시예고',
}


def classify_disclosure_type(title: str) -> Optional[str]:
    """공시 제목 → 타입. 자회사가 더 specific이라 우선 매칭.
    가이던스/실적등에대한전망은 None (제외)."""
    if not title:
        return None
    if '실적등에대한전망' in title:
        return None  # 가이던스 제외 (Q1 결정)
    # 우선순위: 자회사 → 결산예고 → 연결잠정 → 별도잠정
    if '자회사' in title and '잠정' in title:
        return 'SUBSIDIARY_PRELIMINARY'
    if '결산실적공시예고' in title:
        return 'PRE_ANNOUNCE'
    if '연결재무제표기준영업(잠정)' in title:
        return 'CONSOLIDATED_PRELIMINARY'
    if '영업(잠정)실적' in title:
        return 'STANDALONE_PRELIMINARY'
    return None


# ============================================================
# 2. 본문 다운로드
# ============================================================

def fetch_dart_document(rcept_no: str) -> Optional[str]:
    """document.xml zip → utf-8 디코딩 (가장 큰 .xml 파일)."""
    if not DART_API_KEY:
        return None
    url = f'https://opendart.fss.or.kr/api/document.xml?crtfc_key={DART_API_KEY}&rcept_no={rcept_no}'
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            raw = r.read()
    except (urllib.error.URLError, TimeoutError):
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            xmls = [n for n in z.namelist() if n.endswith('.xml')]
            if not xmls:
                return None
            xmls.sort(key=lambda n: z.getinfo(n).file_size, reverse=True)
            with z.open(xmls[0]) as f:
                content = f.read()
        for enc in ('utf-8', 'euc-kr', 'cp949'):
            try:
                return content.decode(enc)
            except UnicodeDecodeError:
                continue
        return content.decode('utf-8', errors='ignore')
    except zipfile.BadZipFile:
        return raw.decode('utf-8', errors='ignore')


# ============================================================
# 3. 본문 정규식 파싱
# ============================================================

def _parse_num(s: str) -> Optional[float]:
    """'69,144' / '-23,356' → float. '-' / '' → None."""
    if not s:
        return None
    text = s.strip().replace(',', '')
    if not text or text == '-':
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _strip_html(text: str) -> str:
    """HTML 태그 제거 + 공백 정규화."""
    text = re.sub(r'<[^>]+>', ' ', text)
    return re.sub(r'\s+', ' ', text)


def _detect_unit_multiplier(cleaned_text: str) -> float:
    """본문에서 단위 표기 추출 → 백만원 변환 배율.
    '단위 : 백만원' → 1.0
    '단위 : 억원'   → 100.0  (1억원 = 100백만원)
    '단위 : 천원'   → 0.001
    '단위 : 원'     → 1e-6
    표기 없으면 백만원 (default 1.0)."""
    m = re.search(r'단위\s*[:：]\s*([가-힣%원,\s]+)', cleaned_text[:3000])
    if not m:
        return 1.0
    unit_str = m.group(1)
    if '억원' in unit_str:
        return 100.0
    if '백만원' in unit_str:
        return 1.0
    if '천원' in unit_str:
        return 0.001
    if '원' in unit_str:
        return 1e-6
    return 1.0


def parse_preliminary_earnings(html_text: str) -> Dict:
    """잠정실적 본문 → {revenue, operating_profit, net_income, eps}.
    단위 자동 감지 후 백만원으로 통일 (consensus_quarterly와 동일 단위)."""
    if not html_text:
        return {}

    cleaned = _strip_html(html_text)
    multiplier = _detect_unit_multiplier(cleaned)

    LABEL_MAP = [
        ('매출액',     'revenue'),
        ('영업이익',   'operating_profit'),
        ('당기순이익', 'net_income'),
        ('주당순이익', 'eps'),  # EPS 본문은 별도 단위(원). 매핑되면 multiplier 적용 X
    ]

    result = {}
    for label, key in LABEL_MAP:
        m = re.search(
            rf'{label}\s+당해실적\s+([\-\d,]+)\s+([\-\d,]+)',
            cleaned
        )
        if m:
            current = _parse_num(m.group(1))
            previous = _parse_num(m.group(2))
            # EPS는 단위 변환 X (이미 원 단위)
            if key != 'eps' and multiplier != 1.0:
                if current is not None: current = current * multiplier
                if previous is not None: previous = previous * multiplier
            if current is not None or previous is not None:
                result[key] = {'current': current, 'previous': previous}

    return result


# ============================================================
# 4. DB 저장
# ============================================================

def _get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _infer_quarter(rcept_dt: str) -> tuple:
    """공시일자(YYYYMMDD) → (year, quarter).
    잠정실적 발표 시점 기준 직전 분기 추정:
      - 1~4월 발표 → 직전 4Q (전년)
      - 4~7월 발표 → 1Q (당해)
      - 7~10월 발표 → 2Q (당해)
      - 10~1월 발표 → 3Q (당해)
    실제로는 4월말은 1Q 발표, 7월말은 2Q 발표 등."""
    if not rcept_dt or len(rcept_dt) != 8:
        return None, None
    y = int(rcept_dt[:4])
    m = int(rcept_dt[4:6])
    if m <= 3:
        return y - 1, 4
    elif m <= 6:
        return y, 1
    elif m <= 9:
        return y, 2
    else:
        return y, 3


def save_earnings_actual(stock_code: str, rcept_no: str, rcept_dt: str,
                         disclosure_title: str, doc_type: str,
                         parsed: Dict) -> bool:
    """earnings_actual에 INSERT. 분기는 rcept_dt에서 추정."""
    if not parsed:
        return False
    year, quarter = _infer_quarter(rcept_dt)
    if not year:
        return False

    rev = (parsed.get('revenue') or {}).get('current')
    op  = (parsed.get('operating_profit') or {}).get('current')
    ni  = (parsed.get('net_income') or {}).get('current')
    eps = (parsed.get('eps') or {}).get('current')

    # YoY/QoQ 계산용 직전값 (단순화: 그냥 두 번째 숫자가 전기값으로 가정)
    # 정확한 YoY는 financial_quarterly의 직전 4Q 데이터로 별도 계산
    prev_rev = (parsed.get('revenue') or {}).get('previous')
    prev_op  = (parsed.get('operating_profit') or {}).get('previous')

    rev_yoy = None
    op_yoy = None
    if rev and prev_rev and prev_rev != 0:
        rev_yoy = (rev - prev_rev) / abs(prev_rev) * 100
    if op and prev_op and prev_op != 0:
        op_yoy = (op - prev_op) / abs(prev_op) * 100

    is_consolidated = 1 if doc_type == 'CONSOLIDATED_PRELIMINARY' else (
        0 if doc_type == 'STANDALONE_PRELIMINARY' else 1
    )

    source_type = {
        'CONSOLIDATED_PRELIMINARY': 'DART_PRELIMINARY',
        'STANDALONE_PRELIMINARY':   'DART_PRELIMINARY',
        'SUBSIDIARY_PRELIMINARY':   'DART_PRELIMINARY_SUB',
    }.get(doc_type, 'DART_PRELIMINARY')

    conn = _get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO earnings_actual (
            stock_code, year, quarter, source_type,
            disclosure_id, disclosure_date, disclosure_title,
            revenue, operating_profit, net_income, eps,
            revenue_yoy_pct, op_yoy_pct,
            is_consolidated, parsed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        stock_code, year, quarter, source_type,
        rcept_no, rcept_dt, disclosure_title,
        rev, op, ni, eps,
        rev_yoy, op_yoy,
        is_consolidated,
        datetime.now().isoformat(),
    ))
    conn.commit()
    conn.close()
    return True


def save_pre_announce_to_queue(stock_code: str, rcept_no: str, rcept_dt: str,
                               disclosure_title: str, html_text: str) -> bool:
    """결산실적공시예고 → earnings_alert_queue 적재.
    예상 발표일은 본문에서 추출 (예: '발표예정일 : 2026.05.10')."""
    year, quarter = _infer_quarter(rcept_dt)
    if not year:
        return False

    # 본문에서 발표예정일 추출
    expected_date = None
    if html_text:
        cleaned = _strip_html(html_text)
        # DART 결산실적공시예고 표준 패턴: '결산실적 공시예정일 YYYY-MM-DD'
        patterns = [
            r'결산실적\s*공시예정일\s*(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})',
            r'(?:발표\s*예정일|예상\s*발표일|실적발표\s*예정일|공시예정일)\s*[:：]?\s*'
            r'(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})',
        ]
        for pat in patterns:
            m = re.search(pat, cleaned)
            if m:
                expected_date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
                break

    confidence = 'HIGH' if expected_date else 'MEDIUM'
    now_iso = datetime.now().isoformat()

    conn = _get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO earnings_alert_queue (
            stock_code, year, quarter,
            expected_announce_date, confidence,
            queue_resolved, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
    """, (stock_code, year, quarter, expected_date, confidence, now_iso, now_iso))
    conn.commit()
    conn.close()
    return True


# ============================================================
# 5. 메인 진입점
# ============================================================

def process_disclosure(rcept_no: str, stock_code: str, rcept_dt: str,
                      title: str) -> Dict:
    """단일 공시 처리: 분류 → 본문 fetch → 파싱 → DB 저장."""
    doc_type = classify_disclosure_type(title)
    if not doc_type:
        return {'rcept_no': rcept_no, 'status': 'IGNORED',
                'reason': '잠정실적 키워드 미매칭'}

    html_text = fetch_dart_document(rcept_no)
    if not html_text:
        return {'rcept_no': rcept_no, 'status': 'FETCH_FAIL', 'doc_type': doc_type}

    if doc_type == 'PRE_ANNOUNCE':
        ok = save_pre_announce_to_queue(stock_code, rcept_no, rcept_dt,
                                        title, html_text)
        return {'rcept_no': rcept_no, 'stock_code': stock_code,
                'doc_type': doc_type, 'status': 'OK' if ok else 'SAVE_FAIL'}

    # CONSOLIDATED / STANDALONE / SUBSIDIARY → earnings_actual
    parsed = parse_preliminary_earnings(html_text)
    if not parsed:
        return {'rcept_no': rcept_no, 'stock_code': stock_code,
                'doc_type': doc_type, 'status': 'PARSE_FAIL'}

    ok = save_earnings_actual(stock_code, rcept_no, rcept_dt, title,
                              doc_type, parsed)
    return {
        'rcept_no': rcept_no,
        'stock_code': stock_code,
        'doc_type': doc_type,
        'status': 'OK' if ok else 'SAVE_FAIL',
        'parsed': {k: v.get('current') for k, v in parsed.items()},
    }


# ============================================================
# 6. 유니버스 종목 일괄 처리 (검증용)
# ============================================================

def collect_universe_recent(days_back: int = 30) -> Dict:
    """활성 유니버스 종목 중 최근 N일 잠정실적 공시 일괄 처리."""
    from universe_manager import get_active_universe
    codes = set(get_active_universe())

    today = datetime.now().strftime('%Y%m%d')
    bgn_dt = (datetime.now() - __import__('datetime').timedelta(days=days_back)).strftime('%Y%m%d')

    # disclosure_history에서 매칭 공시 추출
    conn = _get_db()
    cur = conn.cursor()
    placeholders = ','.join('?' * len(codes))
    cur.execute(f"""
        SELECT rcept_no, stock_code, corp_name, title, rcept_dt
        FROM disclosure_history
        WHERE stock_code IN ({placeholders})
          AND rcept_dt >= ?
          AND (title LIKE '%잠정%' OR title LIKE '%결산실적%')
        ORDER BY rcept_dt DESC
    """, (*codes, bgn_dt))
    targets = cur.fetchall()
    conn.close()

    print(f"📊 활성 유니버스 {len(codes)}종목 / 최근 {days_back}일 매칭 공시 {len(targets)}건")
    results = []
    counts = {'OK': 0, 'IGNORED': 0, 'PARSE_FAIL': 0, 'FETCH_FAIL': 0, 'SAVE_FAIL': 0}

    for row in targets:
        r = process_disclosure(row['rcept_no'], row['stock_code'],
                               row['rcept_dt'], row['title'])
        results.append({**r, 'corp_name': row['corp_name']})
        counts[r['status']] = counts.get(r['status'], 0) + 1

    print(f"\n결과:")
    for k, v in counts.items():
        if v: print(f"  {k}: {v}")
    return {'results': results, 'counts': counts}


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'all':
        collect_universe_recent(days_back=30)
    elif len(sys.argv) > 1:
        rcept_no = sys.argv[1]
        # rcept_no 단독 → disclosure_history 조회 후 처리
        conn = _get_db()
        cur = conn.cursor()
        cur.execute("""SELECT stock_code, rcept_dt, title, corp_name
                       FROM disclosure_history WHERE rcept_no=?""",
                    (rcept_no,))
        row = cur.fetchone()
        conn.close()
        if not row:
            print(f"disclosure_history에 {rcept_no} 없음")
            sys.exit(1)
        r = process_disclosure(rcept_no, row['stock_code'],
                              row['rcept_dt'], row['title'])
        print(json.dumps(r, indent=2, ensure_ascii=False, default=str))
    else:
        print("Usage:")
        print("  python3 earnings_parser.py 20260427800331  # 단일 rcept_no")
        print("  python3 earnings_parser.py all             # 유니버스 30일 일괄")
