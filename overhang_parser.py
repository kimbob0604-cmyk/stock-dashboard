"""
DART 오버행 공시 본문 파싱
- 전환사채(CB), 신주인수권부사채(BW), 유상증자, 교환사채(EB)
Step 4-2-B
"""

import re
import os
import sys
import io
import zipfile
import sqlite3
import asyncio
import aiohttp
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List


# .env 자동 로드
def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    try:
        for raw in path.read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, _, v = line.partition('=')
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except Exception as exc:
        print(f"[.env 로드 실패] {exc}")


_load_dotenv(Path(__file__).parent / '.env')

DB_PATH = Path(__file__).parent / 'db' / 'dashboard.db'
DART_API_KEY = os.environ.get('DART_API_KEY', '')

OVERHANG_REPORT_PATTERNS = {
    'CB': ['전환사채권발행결정', '전환사채발행결정'],
    'BW': ['신주인수권부사채권발행결정', '신주인수권부사채발행결정'],
    'PAID_IN': ['유상증자결정'],
    'EB': ['교환사채권발행결정'],
}


def _get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def classify_overhang_type(report_nm: str) -> Optional[str]:
    for otype, patterns in OVERHANG_REPORT_PATTERNS.items():
        for pat in patterns:
            if pat in report_nm:
                return otype
    return None


# ============================================================
# DART 공시 목록 조회
# ============================================================

async def fetch_overhang_disclosures(stock_code: str, days_back: int = 1825) -> List[Dict]:
    """종목의 N일 내 오버행 관련 공시 목록"""
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("SELECT corp_code FROM dart_corp_map WHERE stock_code = ?", (stock_code,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return []
    corp_code = row['corp_code']

    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y%m%d')

    overhang_disclosures = []
    page_no = 1

    async with aiohttp.ClientSession() as session:
        while page_no <= 10:
            params = {
                'crtfc_key': DART_API_KEY,
                'corp_code': corp_code,
                'bgn_de': start_date,
                'end_de': end_date,
                'page_no': page_no,
                'page_count': 100,
            }
            try:
                async with session.get(
                    'https://opendart.fss.or.kr/api/list.json',
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    data = await resp.json(content_type=None)
            except Exception as e:
                print(f"  ⚠ {stock_code} list.json 실패: {e}")
                break

            if data.get('status') == '013':
                break  # 데이터 없음
            if data.get('status') != '000':
                break

            items = data.get('list', [])
            if not items:
                break

            for item in items:
                otype = classify_overhang_type(item.get('report_nm', ''))
                if otype:
                    overhang_disclosures.append({
                        'rcept_no': item['rcept_no'],
                        'report_nm': item['report_nm'],
                        'overhang_type': otype,
                        'rcept_dt': item['rcept_dt'],
                    })

            if len(items) < 100:
                break
            page_no += 1
            await asyncio.sleep(0.3)

    return overhang_disclosures


# ============================================================
# 공시 본문 다운로드
# ============================================================

async def fetch_disclosure_document(rcept_no: str, session: aiohttp.ClientSession) -> Optional[str]:
    """공시 원본 (zip 안의 XML) 다운로드 + 디코드"""
    url = 'https://opendart.fss.or.kr/api/document.xml'
    params = {'crtfc_key': DART_API_KEY, 'rcept_no': rcept_no}

    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                return None
            content = await resp.read()
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as z:
                    # 가장 큰 .xml 파일 (본문)
                    candidates = [n for n in z.namelist() if n.endswith('.xml')]
                    if not candidates:
                        return None
                    candidates.sort(key=lambda n: z.getinfo(n).file_size, reverse=True)
                    with z.open(candidates[0]) as f:
                        # DART는 보통 EUC-KR이지만 UTF-8로도 시도
                        raw = f.read()
                        for enc in ('utf-8', 'euc-kr', 'cp949'):
                            try:
                                return raw.decode(enc)
                            except UnicodeDecodeError:
                                continue
                        return raw.decode('utf-8', errors='ignore')
            except zipfile.BadZipFile:
                return content.decode('utf-8', errors='ignore')
    except Exception:
        return None


# ============================================================
# 본문 파싱 (정규식)
# ============================================================

def _parse_amount(s: str) -> Optional[float]:
    if not s:
        return None
    cleaned = re.sub(r'[^\d.]', '', s)
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _parse_date(s: str) -> Optional[str]:
    if not s:
        return None
    m = re.search(r'(\d{4})[-./년\s]*(\d{1,2})[-./월\s]*(\d{1,2})', s)
    if m:
        try:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        except (ValueError, TypeError):
            return None
    return None


def parse_cb_bw_content(html: str) -> Dict:
    result = {}

    # 전환가/행사가
    m = re.search(r'전환가\s*[액]?\s*[:：]?\s*([\d,]+)\s*원', html)
    if not m:
        m = re.search(r'행사가\s*[액]?\s*[:：]?\s*([\d,]+)\s*원', html)
    if m:
        result['conversion_price'] = _parse_amount(m.group(1))

    # 발행할 주식수
    m = re.search(r'(?:전환|행사)에?\s*따라\s*발행할\s*주식\s*[의총수]*\s*[:：]?\s*([\d,]+)\s*주', html)
    if not m:
        m = re.search(r'발행\s*주식\s*수\s*[:：]?\s*([\d,]+)\s*주', html)
    if m:
        result['potential_shares'] = _parse_amount(m.group(1))

    # 액면 총액
    m = re.search(r'사채의?\s*액면\s*총액\s*[:：]?\s*([\d,]+)\s*원', html)
    if not m:
        m = re.search(r'(?:발행)?총액\s*[:：]?\s*([\d,]+)\s*원', html)
    if m:
        result['face_value'] = _parse_amount(m.group(1))

    # 행사 기간
    m = re.search(
        r'(?:전환|행사)\s*청구\s*기간\s*[:：]?\s*'
        r'(\d{4}[-./년\s]+\d{1,2}[-./월\s]+\d{1,2}[일\s]*)\s*'
        r'[~∼\-]\s*'
        r'(\d{4}[-./년\s]+\d{1,2}[-./월\s]+\d{1,2}[일\s]*)',
        html
    )
    if m:
        result['exercise_start_date'] = _parse_date(m.group(1))
        result['exercise_end_date'] = _parse_date(m.group(2))

    # 만기일
    m = re.search(r'사채\s*만기일\s*[:：]?\s*(\d{4}[-./년\s]+\d{1,2}[-./월\s]+\d{1,2})', html)
    if m:
        result['maturity_date'] = _parse_date(m.group(1))

    return result


def parse_paid_in_content(html: str) -> Dict:
    result = {}

    m = re.search(r'신주\s*[의]?\s*수\s*[:：]?\s*([\d,]+)\s*주', html)
    if m:
        result['potential_shares'] = _parse_amount(m.group(1))

    m = re.search(r'1주당\s*발행가\s*[액]?\s*[:：]?\s*([\d,]+)\s*원', html)
    if not m:
        m = re.search(r'발행가\s*액?\s*[:：]?\s*([\d,]+)\s*원', html)
    if m:
        result['conversion_price'] = _parse_amount(m.group(1))

    m = re.search(r'신주\s*상장\s*예정일\s*[:：]?\s*(\d{4}[-./년\s]+\d{1,2}[-./월\s]+\d{1,2})', html)
    if m:
        result['exercise_start_date'] = _parse_date(m.group(1))

    return result


# ============================================================
# 메인: 종목 단위 수집
# ============================================================

async def collect_overhang_for_stock(stock_code: str) -> int:
    disclosures = await fetch_overhang_disclosures(stock_code)
    if not disclosures:
        # progress만 업데이트
        conn = _get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO fetch_progress (stock_code, overhang_status, overhang_last_fetch)
            VALUES (?, 'DONE', datetime('now', 'localtime'))
            ON CONFLICT(stock_code) DO UPDATE SET
                overhang_status = 'DONE',
                overhang_last_fetch = datetime('now', 'localtime')
        """, (stock_code,))
        conn.commit()
        conn.close()
        return 0

    today = datetime.now().strftime('%Y-%m-%d')
    saved = 0

    async with aiohttp.ClientSession() as session:
        for d in disclosures:
            await asyncio.sleep(0.4)
            content = await fetch_disclosure_document(d['rcept_no'], session)
            if not content:
                continue

            if d['overhang_type'] in ('CB', 'BW', 'EB'):
                parsed = parse_cb_bw_content(content)
            elif d['overhang_type'] == 'PAID_IN':
                parsed = parse_paid_in_content(content)
            else:
                continue

            end_date = parsed.get('exercise_end_date')
            status = 'ACTIVE'
            if end_date and end_date < today:
                status = 'EXPIRED'
            if status != 'ACTIVE':
                continue

            shares_total = None
            try:
                from valuation_calculator import get_shares_outstanding
                shares_total = get_shares_outstanding(stock_code)
            except Exception:
                pass

            dilution_pct = None
            if shares_total and parsed.get('potential_shares'):
                dilution_pct = (parsed['potential_shares'] / shares_total) * 100

            conn = _get_db()
            cur = conn.cursor()
            try:
                cur.execute("""
                    INSERT OR REPLACE INTO dart_disclosure_overhang (
                        stock_code, rcept_no, report_nm, overhang_type,
                        issue_date, maturity_date,
                        exercise_start_date, exercise_end_date,
                        face_value, conversion_price,
                        potential_shares, potential_dilution_pct,
                        status, fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
                """, (
                    stock_code, d['rcept_no'], d['report_nm'], d['overhang_type'],
                    _parse_date(d['rcept_dt']),
                    parsed.get('maturity_date'),
                    parsed.get('exercise_start_date'),
                    parsed.get('exercise_end_date'),
                    parsed.get('face_value'),
                    parsed.get('conversion_price'),
                    parsed.get('potential_shares'),
                    dilution_pct,
                    status
                ))
                conn.commit()
                saved += 1
            except sqlite3.IntegrityError:
                pass
            conn.close()

    conn = _get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO fetch_progress (stock_code, overhang_status, overhang_last_fetch)
        VALUES (?, 'DONE', datetime('now', 'localtime'))
        ON CONFLICT(stock_code) DO UPDATE SET
            overhang_status = 'DONE',
            overhang_last_fetch = datetime('now', 'localtime')
    """, (stock_code,))
    conn.commit()
    conn.close()
    return saved


async def collect_overhang_all():
    from valuechain import get_all_stocks_in_map
    kr_stocks, _ = get_all_stocks_in_map()

    print(f"🔍 {len(kr_stocks)}종목 오버행 수집 시작")
    sem = asyncio.Semaphore(3)

    async def with_sem(code):
        async with sem:
            try:
                return code, await collect_overhang_for_stock(code)
            except Exception as e:
                return code, -1

    tasks = [with_sem(c) for c in kr_stocks]
    total_saved = 0
    completed = 0
    for coro in asyncio.as_completed(tasks):
        code, saved = await coro
        completed += 1
        if saved > 0:
            print(f"  ✅ {code}: 활성 오버행 {saved}건")
        elif saved < 0:
            print(f"  ⚠ {code}: 에러")
        if completed % 10 == 0:
            print(f"  진행: {completed}/{len(kr_stocks)}")
        total_saved += max(0, saved)

    print(f"\n📊 총 활성 오버행 {total_saved}건 저장")


if __name__ == '__main__':
    if not DART_API_KEY:
        print("❌ DART_API_KEY 환경변수 없음")
        sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1] == 'all':
        asyncio.run(collect_overhang_all())
    elif len(sys.argv) > 1:
        code = sys.argv[1]
        result = asyncio.run(collect_overhang_for_stock(code))
        print(f"{code}: 활성 오버행 {result}건 저장")
        conn = _get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT overhang_type, conversion_price, potential_shares,
                   potential_dilution_pct, exercise_end_date, report_nm
            FROM dart_disclosure_overhang WHERE stock_code = ? AND status = 'ACTIVE'
        """, (code,))
        for r in cur.fetchall():
            dil = f"{r['potential_dilution_pct']:.2f}%" if r['potential_dilution_pct'] else '-'
            shares = f"{int(r['potential_shares']):,}" if r['potential_shares'] else '-'
            price = f"{int(r['conversion_price']):,}" if r['conversion_price'] else '-'
            print(f"  [{r['overhang_type']}] {r['report_nm']}: "
                  f"{shares}주 × {price}원, 만기 {r['exercise_end_date']}, 희석 {dil}")
        conn.close()
    else:
        print("Usage:")
        print("  python3 overhang_parser.py 007660")
        print("  python3 overhang_parser.py all")
