"""
Step 4-7-D-6: 어닝 알림 텔레그램 발송 + 성과 추적

워크플로우:
1. priority 1~3 미발송 어닝 조회
2. earnings_alert_writer로 메시지 생성
3. 텔레그램 발송 (시간대별 우선순위 큐)
4. earnings_surprise.alert_sent + alert_history_v2 기록
5. 3/5/7일 성과 백필 (별도 cron)
"""

import sqlite3
import os
import json
import time
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Optional


# .env 자동 로드
def _load_dotenv(p: Path):
    if not p.exists(): return
    for raw in p.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        k, _, v = line.partition('=')
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(Path(__file__).parent / '.env')

DB_PATH = Path(__file__).parent / 'db' / 'dashboard.db'
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')


def _get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================
# 1. 텔레그램 발송 (urllib만, 가벼움)
# ============================================================

def send_telegram_message(text: str, parse_mode: str = '',
                          disable_preview: bool = True,
                          timeout: int = 30, max_retries: int = 2) -> Dict:
    """텔레그램 발송. timeout 30s + 지수 백오프 재시도.
    실패해도 raise 안 하고 결과 dict."""
    if os.environ.get('TELEGRAM_ENABLED', '1').strip().lower() in ('0', 'false', 'no', 'off'):
        return {'success': False, 'error': 'TELEGRAM_ENABLED=0 (알림 OFF)'}
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return {'success': False, 'error': 'TELEGRAM_BOT_TOKEN/CHAT_ID 미설정'}

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': text,
        'disable_web_page_preview': disable_preview,
    }
    if parse_mode:
        payload['parse_mode'] = parse_mode

    last_error = None
    for attempt in range(max_retries + 1):
        if attempt > 0:
            backoff = 2 ** attempt  # 2초, 4초
            time.sleep(backoff)

        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data,
                                      headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.loads(r.read().decode('utf-8'))
            if resp.get('ok'):
                return {'success': True,
                        'message_id': resp['result'].get('message_id'),
                        'attempt': attempt + 1}
            last_error = resp.get('description', 'unknown')
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', errors='ignore')
            if parse_mode and 'parse' in body.lower():
                payload.pop('parse_mode', None)
                continue  # plain text로 재시도
            last_error = f"HTTP {e.code}: {body[:200]}"
        except (urllib.error.URLError, TimeoutError) as e:
            last_error = str(e)
            continue  # 네트워크 timeout → backoff 후 재시도
        except Exception as e:
            last_error = str(e)
            break

    return {'success': False, 'error': last_error,
            'attempts': max_retries + 1}


# ============================================================
# 2. 어닝 알림 발송 + 기록
# ============================================================

def send_earnings_alert(stock_code: str, year: int, quarter: int,
                        force: bool = False) -> Dict:
    """1건 어닝 알림 발송 + DB 기록."""
    conn = _get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT signal, priority, alert_sent, alert_sent_at
        FROM earnings_surprise
        WHERE stock_code = ? AND year = ? AND quarter = ?
    """, (stock_code, year, quarter))
    surprise = cur.fetchone()

    if not surprise:
        conn.close()
        return {'success': False, 'error': 'earnings_surprise 없음'}
    if surprise['alert_sent'] and not force:
        conn.close()
        return {'success': False, 'error': '이미 발송됨',
                'sent_at': surprise['alert_sent_at']}
    if surprise['priority'] > 3:
        conn.close()
        return {'success': False,
                'error': f"priority {surprise['priority']} (1~3만 발송)"}

    # 종목명 (alert_history_v2.stock_name 채움)
    name_row = cur.execute("SELECT name FROM stocks WHERE code=?",
                           (stock_code,)).fetchone()
    stock_name = name_row['name'] if name_row else stock_code
    conn.close()

    # 메시지 생성
    from earnings_alert_writer import generate_alert_for_surprise
    alert_data = generate_alert_for_surprise(stock_code, year, quarter)

    if not alert_data or not alert_data.get('message'):
        return {'success': False, 'error': '메시지 생성 실패'}
    message = alert_data['message']

    # 텔레그램 발송
    send_result = send_telegram_message(message, parse_mode='')
    if not send_result['success']:
        return {'success': False, 'error': send_result.get('error', 'send fail'),
                'method': alert_data.get('method')}

    # DB 기록
    now_iso = datetime.now().isoformat()
    conn = _get_db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE earnings_surprise
        SET alert_sent = 1, alert_sent_at = ?, alert_message_preview = ?
        WHERE stock_code = ? AND year = ? AND quarter = ?
    """, (now_iso, message[:500], stock_code, year, quarter))

    # 발송 시점 가격 (성과 추적 baseline = price_at_alert)
    cur.execute("SELECT close FROM ohlcv WHERE code = ? ORDER BY date DESC LIMIT 1",
                (stock_code,))
    price_row = cur.fetchone()
    baseline_price = price_row['close'] if price_row else None

    cur.execute("""
        INSERT INTO alert_history_v2 (
            stock_code, stock_name, alert_type, signal, priority,
            sent_at, sent_status, telegram_message_id,
            price_at_alert, message_preview,
            performance_3d, performance_5d, performance_7d,
            tracking_completed
        ) VALUES (?, ?, 'EARNINGS_SURPRISE', ?, ?, ?, 'SENT', ?, ?, ?, NULL, NULL, NULL, 0)
    """, (
        stock_code, stock_name, surprise['signal'], surprise['priority'],
        now_iso, str(send_result.get('message_id') or ''),
        baseline_price, message[:500],
    ))
    conn.commit()
    conn.close()

    return {
        'success': True,
        'stock_code': stock_code,
        'signal': surprise['signal'],
        'priority': surprise['priority'],
        'method': alert_data.get('method'),
        'message_id': send_result.get('message_id'),
        'sent_at': now_iso,
    }


# ============================================================
# 3. 일괄 발송
# ============================================================

def send_pending_alerts(max_count: int = 20, dry_run: bool = False) -> Dict:
    conn = _get_db()
    cur = conn.execute("""
        SELECT stock_code, year, quarter, signal, priority
        FROM earnings_surprise
        WHERE priority <= 3 AND alert_sent = 0
        ORDER BY priority, calculated_at DESC
        LIMIT ?
    """, (max_count,))
    targets = [dict(r) for r in cur.fetchall()]
    conn.close()

    print(f"\n=== 미발송 알림 {len(targets)}건 처리 ===\n")

    results = []
    for i, t in enumerate(targets, 1):
        print(f"[{i}/{len(targets)}] {t['stock_code']} ({t['signal']}, P{t['priority']})")

        if dry_run:
            print(f"  [DRY RUN] 발송 시뮬레이션만")
            results.append({'stock_code': t['stock_code'], 'dry_run': True})
            continue

        result = send_earnings_alert(t['stock_code'], t['year'], t['quarter'])
        results.append(result)

        if result['success']:
            print(f"  ✅ 발송 성공 ({result.get('method')}, msg_id={result.get('message_id')})")
        else:
            print(f"  ❌ 실패: {result.get('error')}")

        time.sleep(1)  # 텔레그램 30 msg/sec

    success = sum(1 for r in results if r.get('success'))
    failed = len(results) - success
    print(f"\n=== 결과 ===")
    print(f"  성공: {success}/{len(results)}")
    print(f"  실패: {failed}")
    return {'total': len(results), 'success': success,
            'failed': failed, 'results': results}


# ============================================================
# 4. 성과 백필
# ============================================================

def _get_price_on_or_after(cur, stock_code: str, target_date: str) -> Optional[float]:
    cur.execute("""
        SELECT close FROM ohlcv
        WHERE code = ? AND date >= ?
        ORDER BY date ASC LIMIT 1
    """, (stock_code, target_date))
    row = cur.fetchone()
    return row['close'] if row else None


def backfill_alert_performance(verbose: bool = True) -> Dict:
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, stock_code, signal, sent_at, price_at_alert,
               performance_3d, performance_5d, performance_7d
        FROM alert_history_v2
        WHERE alert_type = 'EARNINGS_SURPRISE'
          AND tracking_completed = 0
          AND sent_at < datetime('now', '-1 days')
        ORDER BY sent_at DESC LIMIT 100
    """)
    targets = [dict(r) for r in cur.fetchall()]
    if verbose:
        print(f"\n=== 성과 백필 대상 {len(targets)}건 ===\n")

    updated = 0
    completed = 0
    for t in targets:
        if not t['price_at_alert']:
            continue
        sent_dt = datetime.fromisoformat(t['sent_at'])
        days_since = (datetime.now() - sent_dt).days
        updates = {}
        for k_days, k_col in [(3, 'performance_3d'),
                              (5, 'performance_5d'),
                              (7, 'performance_7d')]:
            if t[k_col] is None and days_since >= k_days:
                target_date = (sent_dt + timedelta(days=k_days)).strftime('%Y-%m-%d')
                price = _get_price_on_or_after(cur, t['stock_code'], target_date)
                if price:
                    pct = (price - t['price_at_alert']) / t['price_at_alert'] * 100
                    updates[k_col] = round(pct, 2)
        if days_since >= 7 and (t['performance_7d'] is not None or 'performance_7d' in updates):
            updates['tracking_completed'] = 1
            completed += 1
        if updates:
            set_clauses = ', '.join(f"{k} = ?" for k in updates.keys())
            values = list(updates.values()) + [t['id']]
            cur.execute(f"UPDATE alert_history_v2 SET {set_clauses} WHERE id = ?", values)
            updated += 1
            if verbose:
                print(f"  {t['stock_code']} ({t['signal']}): {updates}")
    conn.commit()
    conn.close()
    if verbose:
        print(f"\n=== 백필 완료: 업데이트 {updated} / 완료 {completed} ===")
    return {'updated': updated, 'completed': completed, 'total_targets': len(targets)}


# ============================================================
# 5. 백테스트 통계
# ============================================================

def get_signal_performance_stats() -> Dict:
    conn = _get_db()
    cur = conn.execute("""
        SELECT signal, COUNT(*) as count,
               AVG(performance_3d) as avg_3d,
               AVG(performance_5d) as avg_5d,
               AVG(performance_7d) as avg_7d,
               SUM(CASE WHEN performance_7d > 0 THEN 1 ELSE 0 END) * 1.0 /
                 NULLIF(SUM(CASE WHEN performance_7d IS NOT NULL THEN 1 ELSE 0 END), 0) as hit_rate_7d
        FROM alert_history_v2
        WHERE alert_type = 'EARNINGS_SURPRISE' AND tracking_completed = 1
        GROUP BY signal ORDER BY hit_rate_7d DESC NULLS LAST
    """)
    results = [dict(r) for r in cur.fetchall()]
    conn.close()
    return {'signals': results}


# ============================================================
# 6. CLI
# ============================================================

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 earnings_telegram_sender.py send <code> <year> <quarter>")
        print("  python3 earnings_telegram_sender.py pending [max=20]")
        print("  python3 earnings_telegram_sender.py dryrun [max=10]")
        print("  python3 earnings_telegram_sender.py backfill")
        print("  python3 earnings_telegram_sender.py stats")
        print("  python3 earnings_telegram_sender.py force <code> [year] [quarter]")
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd == 'send':
        result = send_earnings_alert(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]))
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    elif cmd == 'pending':
        max_count = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        send_pending_alerts(max_count, dry_run=False)
    elif cmd == 'dryrun':
        max_count = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        send_pending_alerts(max_count, dry_run=True)
    elif cmd == 'backfill':
        backfill_alert_performance()
    elif cmd == 'stats':
        stats = get_signal_performance_stats()
        print("\n=== 시그널별 백테스트 성과 ===\n")
        if not stats['signals']:
            print("  (백필 완료 데이터 없음 — 발송 후 7일 후 stats 재실행)")
        else:
            print(f"{'시그널':<24} {'건수':>5} {'3일':>8} {'5일':>8} {'7일':>8} {'7일 적중':>10}")
            print("-" * 80)
            for s in stats['signals']:
                avg_3d = f"{s['avg_3d']:+.1f}%" if s['avg_3d'] is not None else 'N/A'
                avg_5d = f"{s['avg_5d']:+.1f}%" if s['avg_5d'] is not None else 'N/A'
                avg_7d = f"{s['avg_7d']:+.1f}%" if s['avg_7d'] is not None else 'N/A'
                hit = f"{s['hit_rate_7d']*100:.0f}%" if s['hit_rate_7d'] is not None else 'N/A'
                print(f"{s['signal']:<24} {s['count']:>5} {avg_3d:>8} {avg_5d:>8} {avg_7d:>8} {hit:>10}")
    elif cmd == 'force':
        code = sys.argv[2]
        year = int(sys.argv[3]) if len(sys.argv) > 3 else 2026
        quarter = int(sys.argv[4]) if len(sys.argv) > 4 else 1
        result = send_earnings_alert(code, year, quarter, force=True)
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    else:
        print(f"Unknown command: {cmd}")
