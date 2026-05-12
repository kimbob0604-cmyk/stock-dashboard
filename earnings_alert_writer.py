"""
Step 4-7-D-5: LLM 알림 메시지 작성기

기능:
- earnings_surprise (priority 1~3) → 텔레그램 본문 자동 생성
- qwen3:14b 사용 (한국어 학습 강도 높음)
- KUVIC 세션 thesis 통합 (kuvic_validator.KUVIC_SESSION_ANALYSIS)
- llm_cache.db로 캐시 (영구, hit_count 추적)
- LLM 실패 시 템플릿 기반 FALLBACK

CLI:
  python3 earnings_alert_writer.py test <code>  # 단일 메시지 테스트
  python3 earnings_alert_writer.py pending      # 미발송 P1~3 일괄
"""

import os
import re
import json
import time
import hashlib
import sqlite3
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional

DB_PATH = Path(__file__).parent / 'db' / 'dashboard.db'
CACHE_DB_PATH = Path(__file__).parent / 'db' / 'llm_cache.db'

OLLAMA_URL = 'http://localhost:11434/api/generate'
MODEL = 'qwen3:14b'
KEEP_ALIVE = '10m'  # 인터랙티브 (test/pending) — 10분 유지


# ============================================================
# 1. Cache DB
# ============================================================

def _init_cache_db():
    CACHE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(CACHE_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_cache (
            cache_key TEXT PRIMARY KEY,
            model TEXT NOT NULL,
            prompt_excerpt TEXT,
            response TEXT,
            hit_count INTEGER DEFAULT 0,
            created_at TEXT,
            last_hit_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def _cache_key(model: str, prompt: str) -> str:
    return hashlib.sha256(f"{model}::{prompt}".encode('utf-8')).hexdigest()


def cache_get(model: str, prompt: str) -> Optional[str]:
    _init_cache_db()
    key = _cache_key(model, prompt)
    conn = sqlite3.connect(str(CACHE_DB_PATH))
    cur = conn.cursor()
    row = cur.execute("SELECT response FROM llm_cache WHERE cache_key=?", (key,)).fetchone()
    if row:
        cur.execute("""UPDATE llm_cache SET hit_count=hit_count+1,
                       last_hit_at=? WHERE cache_key=?""",
                    (datetime.now().isoformat(), key))
        conn.commit()
    conn.close()
    return row[0] if row else None


def cache_set(model: str, prompt: str, response: str):
    _init_cache_db()
    key = _cache_key(model, prompt)
    conn = sqlite3.connect(str(CACHE_DB_PATH))
    now = datetime.now().isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO llm_cache
          (cache_key, model, prompt_excerpt, response, hit_count, created_at, last_hit_at)
        VALUES (?, ?, ?, ?, 0, ?, ?)
    """, (key, model, prompt[:200], response, now, now))
    conn.commit()
    conn.close()


# ============================================================
# 2. Ollama 호출 (캐시 + 재시도)
# ============================================================

def call_ollama(prompt: str, *, keep_alive: str = KEEP_ALIVE,
                num_predict: int = 1500, use_cache: bool = True,
                timeout: int = 120) -> Dict:
    """qwen3:14b 호출. 응답 + 메타.
    Returns: {'text': str, 'method': 'CACHE'|'LLM'|'FAIL', 'elapsed': float}"""
    if use_cache:
        cached = cache_get(MODEL, prompt)
        if cached:
            return {'text': cached, 'method': 'CACHE', 'elapsed': 0.0}

    payload = {
        'model': MODEL,
        'prompt': prompt,
        'stream': False,
        'keep_alive': keep_alive,
        'options': {'temperature': 0.3, 'num_predict': num_predict},
    }
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(OLLAMA_URL, data=data,
                                  headers={'Content-Type': 'application/json'})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode('utf-8'))
        text = resp.get('response', '').strip()
        elapsed = time.time() - t0
        # qwen3 thinking 마커 제거
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
        if not text:
            return {'text': '', 'method': 'FAIL', 'elapsed': elapsed,
                    'error': '빈 응답'}
        if use_cache:
            cache_set(MODEL, prompt, text)
        return {'text': text, 'method': 'LLM', 'elapsed': elapsed}
    except (urllib.error.URLError, TimeoutError) as e:
        return {'text': '', 'method': 'FAIL',
                'elapsed': time.time() - t0, 'error': str(e)}


# ============================================================
# 3. 종목 컨텍스트 수집
# ============================================================

def _get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_context(stock_code: str, year: int, quarter: int) -> Dict:
    """알림 작성에 필요한 모든 컨텍스트."""
    conn = _get_db()
    cur = conn.cursor()

    surprise = cur.execute("""
        SELECT * FROM earnings_surprise
        WHERE stock_code=? AND year=? AND quarter=?
    """, (stock_code, year, quarter)).fetchone()
    if not surprise:
        conn.close()
        return {}

    name_row = cur.execute("SELECT name FROM stocks WHERE code=?", (stock_code,)).fetchone()
    name = name_row['name'] if name_row else stock_code

    # 매핑된 segment
    cur_price_row = cur.execute(
        "SELECT close FROM ohlcv WHERE code=? ORDER BY date DESC LIMIT 1",
        (stock_code,)).fetchone()
    current_price = cur_price_row['close'] if cur_price_row else None

    # YoY 비교용 직전 4분기
    cur.execute("""
        SELECT year, quarter, revenue, operating_profit
        FROM financial_quarterly
        WHERE stock_code=? AND year=? AND quarter=?
    """, (stock_code, year - 1, quarter))
    prev_yoy = cur.fetchone()

    conn.close()

    # KUVIC thesis (있으면)
    kuvic_thesis = None
    try:
        from kuvic_validator import KUVIC_SESSION_ANALYSIS
        if stock_code in KUVIC_SESSION_ANALYSIS:
            k = KUVIC_SESSION_ANALYSIS[stock_code]
            kuvic_thesis = {
                'thesis': k.get('thesis'),
                'tags': k.get('tags', []),
                'segment': k.get('segment'),
            }
    except Exception:
        pass

    # valuechain 매핑
    segments = []
    try:
        vc_path = Path(__file__).parent / 'data' / 'valuechain_map.json'
        vc = json.loads(vc_path.read_text(encoding='utf-8'))
        for tid, theme in vc['themes'].items():
            for lid, layer in theme.get('layers', {}).items():
                for sid, seg in layer.get('segments', {}).items():
                    if stock_code in (seg.get('stocks_kr', []) or []):
                        segments.append(f"{tid} > {seg.get('name_kr', sid)}")
    except Exception:
        pass

    return {
        'stock_code': stock_code,
        'name': name,
        'year': year,
        'quarter': quarter,
        'signal': surprise['signal'],
        'priority': surprise['priority'],
        'rev_pct': surprise['revenue_surprise_pct'],
        'op_pct': surprise['op_surprise_pct'],
        'rev_consensus': surprise['revenue_consensus'],
        'op_consensus': surprise['op_consensus'],
        'rev_actual': surprise['revenue_actual'],
        'op_actual': surprise['op_actual'],
        'note': surprise['note'],
        'triggers': surprise['triggers'],
        'current_price': current_price,
        'prev_yoy_revenue': prev_yoy['revenue'] if prev_yoy else None,
        'prev_yoy_op': prev_yoy['operating_profit'] if prev_yoy else None,
        'kuvic_thesis': kuvic_thesis,
        'segments': segments,
    }


# ============================================================
# 4. Prompt 생성
# ============================================================

def build_prompt(ctx: Dict) -> str:
    name = ctx['name']
    code = ctx['stock_code']
    signal = ctx['signal']
    rev_pct = ctx['rev_pct']
    op_pct = ctx['op_pct']
    rev_a_eok = (ctx['rev_actual'] or 0) / 100  # 백만원 → 억원
    op_a_eok = (ctx['op_actual'] or 0) / 100
    rev_c_eok = (ctx['rev_consensus'] or 0) / 100 if ctx.get('rev_consensus') else None
    op_c_eok = (ctx['op_consensus'] or 0) / 100 if ctx.get('op_consensus') else None
    rev_pct_str = f"{rev_pct:+.1f}%" if rev_pct is not None else 'N/A'
    op_pct_str = f"{op_pct:+.1f}%" if op_pct is not None else 'N/A'
    rev_c_str = f"{rev_c_eok:,.0f}억" if rev_c_eok is not None else '컨센 없음'
    op_c_str = f"{op_c_eok:,.0f}억" if op_c_eok is not None else '컨센 없음'

    kuvic_section = ''
    if ctx.get('kuvic_thesis'):
        kt = ctx['kuvic_thesis']
        tags = ' '.join(f"#{t}" for t in (kt.get('tags') or [])[:4])
        kuvic_section = f"""
[KUVIC 분석가 thesis]
- 세그먼트: {kt.get('segment','')}
- 태그: {tags}
- 논리: {kt.get('thesis','')}
"""

    segments_str = '\n'.join(f"  - {s}" for s in ctx.get('segments', [])[:3]) or '  (밸류체인 매핑 없음)'

    prompt = f"""당신은 한국 주식 시장 어닝 알림 작성가입니다. 한국 투자자에게 보내는 텔레그램 메시지를 작성하세요.

[종목]
- 회사: {name} ({code})
- 분기: {ctx['year']}Q{ctx['quarter']}
- 현재가: {ctx.get('current_price', '-')}원

[시그널]
- 분류: {signal} (Priority {ctx['priority']})
- 메모: {ctx['note']}

[실적 vs 컨센서스]
- 매출: 실제 {rev_a_eok:,.0f}억 vs 컨센 {rev_c_str} ({rev_pct_str})
- 영업익: 실제 {op_a_eok:,.0f}억 vs 컨센 {op_c_str} ({op_pct_str})

[밸류체인]
{segments_str}
{kuvic_section}
[작성 규칙]
1. 한국어 5~8줄 (HTML 태그 X, 마크다운 X, 일반 텍스트만).
2. 제목 1줄 (이모지 1개 + 종목명 + 시그널)
3. 핵심 숫자 1줄 (매출/영업익 갭 %)
4. 의미 해석 2~3줄 (왜 BEAT/MISS? 단순 숫자가 아닌 사업 맥락)
5. KUVIC thesis 있으면 한 줄로 연계
6. 마지막에 액션 힌트 (관찰/매수 검토/주의)

[톤]
- 단정적이되 과장 X
- 숫자는 콤마 있는 정수 (예: 5,534억)
- "주가에 긍정적/부정적" 같은 단정 X (해석만)

[출력]
바로 메시지 본문만. 설명/사족 X."""

    return prompt


# ============================================================
# 5. FALLBACK 메시지 (LLM 실패 시)
# ============================================================

def build_fallback(ctx: Dict) -> str:
    emoji = {
        'BEAT_BIG': '🔥', 'BEAT': '📈',
        'MISS_BIG': '🚨', 'MISS': '📉',
        'TURNAROUND_FULL': '💎', 'TURNAROUND_PARTIAL': '✨',
        'SHOCK_FULL': '⚠️',
        'REVENUE_BEAT_OP_INLINE': '⚖️', 'REVENUE_MISS_OP_BEAT': '💪',
        'INLINE': '·',
    }.get(ctx['signal'], '📊')

    rev_pct = ctx.get('rev_pct')
    op_pct = ctx.get('op_pct')
    rev_a_eok = (ctx['rev_actual'] or 0) / 100
    op_a_eok = (ctx['op_actual'] or 0) / 100
    rev_pct_s = f"{rev_pct:+.1f}%" if rev_pct is not None else 'YoY 기준'
    op_pct_s = f"{op_pct:+.1f}%" if op_pct is not None else 'YoY 기준'

    lines = [
        f"{emoji} {ctx['name']} ({ctx['stock_code']}) — {ctx['signal']}",
        f"{ctx['year']}Q{ctx['quarter']} 잠정실적 발표",
        f"매출 {rev_a_eok:,.0f}억 ({rev_pct_s}) / 영업익 {op_a_eok:,.0f}억 ({op_pct_s})",
        ctx['note'],
    ]
    if ctx.get('kuvic_thesis'):
        kt = ctx['kuvic_thesis']
        lines.append(f"KUVIC thesis: {kt.get('thesis', '')[:80]}")
    return '\n'.join(lines)


# ============================================================
# 6. 메시지 작성 (메인)
# ============================================================

# 호환 alias (earnings_telegram_sender에서 호출)
def generate_alert_for_surprise(stock_code: str, year: int, quarter: int,
                                use_cache: bool = True) -> Dict:
    """earnings_telegram_sender 호환 alias"""
    return write_alert_message(stock_code, year, quarter, use_cache=use_cache)


def write_alert_message(stock_code: str, year: int, quarter: int,
                        use_cache: bool = True) -> Dict:
    """
    Returns: {
      'message': str,
      'method': 'LLM' | 'CACHE' | 'FALLBACK',
      'elapsed': float,
      'context': dict,
    }
    """
    ctx = fetch_context(stock_code, year, quarter)
    if not ctx:
        return {'error': 'earnings_surprise 없음', 'stock_code': stock_code}

    prompt = build_prompt(ctx)
    result = call_ollama(prompt, use_cache=use_cache)

    if result['method'] in ('LLM', 'CACHE'):
        return {
            'stock_code': stock_code,
            'name': ctx['name'],
            'message': result['text'],
            'method': result['method'],
            'elapsed': result['elapsed'],
            'priority': ctx['priority'],
            'signal': ctx['signal'],
        }
    else:
        return {
            'stock_code': stock_code,
            'name': ctx['name'],
            'message': build_fallback(ctx),
            'method': 'FALLBACK',
            'elapsed': result.get('elapsed', 0),
            'error': result.get('error'),
            'priority': ctx['priority'],
            'signal': ctx['signal'],
        }


# ============================================================
# 7. CLI
# ============================================================

def cmd_test(stock_code: str, year: int = 2026, quarter: int = 1):
    print(f"=== {stock_code} {year}Q{quarter} ===")
    r = write_alert_message(stock_code, year, quarter)
    if 'error' in r and 'message' not in r:
        print(f"ERROR: {r['error']}")
        return
    print(f"[method: {r['method']}, elapsed: {r['elapsed']:.1f}s, P{r['priority']}, {r['signal']}]")
    print()
    print(r['message'])


def cmd_pending():
    """미발송 priority 1~3 일괄 처리"""
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT stock_code, year, quarter, signal, priority
        FROM earnings_surprise
        WHERE alert_sent = 0 AND priority <= 3
        ORDER BY priority, calculated_at DESC
    """)
    targets = cur.fetchall()
    conn.close()

    print(f"📨 미발송 Priority 1~3: {len(targets)}건\n")
    counts = {'LLM': 0, 'CACHE': 0, 'FALLBACK': 0}
    total_elapsed = 0
    for row in targets:
        r = write_alert_message(row['stock_code'], row['year'], row['quarter'])
        if 'message' not in r: continue
        method = r['method']
        counts[method] = counts.get(method, 0) + 1
        total_elapsed += r['elapsed']
        print(f"--- [{method:8s} {r['elapsed']:.1f}s P{r['priority']}] "
              f"{r['name']} ({r['stock_code']}) {r['signal']} ---")
        print(r['message'])
        print()

    print(f"📊 method 분포: {counts}")
    print(f"⏱  총 소요: {total_elapsed:.1f}초")


def cmd_cache_stats():
    _init_cache_db()
    conn = sqlite3.connect(str(CACHE_DB_PATH))
    rows = conn.execute("""
        SELECT model, COUNT(*) as cnt, SUM(hit_count) as total_hits
        FROM llm_cache GROUP BY model
    """).fetchall()
    print("📦 LLM 캐시 통계:")
    for r in rows:
        print(f"  {r[0]}: {r[1]}건, hit {r[2]}회")


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 earnings_alert_writer.py test <code> [year] [quarter]")
        print("  python3 earnings_alert_writer.py pending")
        print("  python3 earnings_alert_writer.py cache_stats")
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd == 'test':
        code = sys.argv[2]
        year = int(sys.argv[3]) if len(sys.argv) > 3 else 2026
        quarter = int(sys.argv[4]) if len(sys.argv) > 4 else 1
        cmd_test(code, year, quarter)
    elif cmd == 'pending':
        cmd_pending()
    elif cmd == 'cache_stats':
        cmd_cache_stats()
    else:
        # 단축: code 직접 (test 생략)
        cmd_test(cmd)
