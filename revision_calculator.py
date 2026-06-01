"""
Step 5-1-C: 컨센서스 리비전 계산기

consensus_snapshot 시계열에서 (종목, 기간, 지표)별로 최근값 vs N일 전 값의
변화율(revision_pct)을 계산해 시그널로 분류하고 revision_alerts 에 dedup INSERT.

핵심 개념
- 베이스라인 = "현재 스냅샷일(Dc) 기준 최소 W일 이전"의 가장 최근 스냅샷
  (date <= Dc - W 중 가장 최신). 없으면 해당 윈도우 시그널 없음.
- revision_pct = (current - baseline) / |baseline| * 100
- 시그널: EPS/매출/영업익 추정치 상·하향 정도 (아래 _THRESHOLDS)
- dedup: 같은 (종목,기간,지표,윈도우,current_date) 1건만 — 재실행해도 중복 X,
         새 스냅샷일이 생기면 새 알림.

데이터 현황(2026-05): 스냅샷이 며칠 누적되어야 의미가 생김.
consensus_estimate 의 과거 fetched_at 으로 NTM 베이스라인 일부 백필 가능(--backfill).

CLI:
  python3 revision_calculator.py --verify          # 분류 로직 자체 테스트
  python3 revision_calculator.py --backfill        # consensus_estimate → NTM 스냅샷 시드
  python3 revision_calculator.py --single 005930   # 단일 종목
  python3 revision_calculator.py --all             # 전체
"""

import sqlite3
import sys
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Tuple

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / 'db' / 'dashboard.db'

logger = logging.getLogger(__name__)

# 리비전 윈도우 (일)
DEFAULT_WINDOWS = [7, 30]

# 지표 → consensus_snapshot 컬럼
METRIC_COLUMN = {
    'eps': 'eps_consensus',
    'revenue': 'revenue_consensus',
    'op_income': 'op_income_consensus',
    'net_income': 'net_income_consensus',
}

# period_type 별 계산 지표 (데이터 있는 것만)
METRICS_BY_PERIOD = {
    'NTM': ['eps'],                                  # KR 은 forward EPS 만 존재
    'QUARTERLY': ['eps', 'revenue', 'op_income'],
    'ANNUAL': ['eps', 'revenue', 'op_income'],
}

# 시그널 분류 임계치 (revision_pct 기준, 단위 %)
#   양수 = 상향(추정치 올림, 강세) / 음수 = 하향(약세)
#   DB 에는 영문 코드(로케일 독립·API 친화)를 저장하고, 표시용 한글은 SIGNAL_LABEL_KR 사용.
_THRESHOLDS = [
    (15.0,  'STRONG_UP',   1),
    (5.0,   'UP',          2),
    (-5.0,  'NEUTRAL',     3),   # -5 ~ +5
    (-15.0, 'DOWN',        2),
    (None,  'STRONG_DOWN', 1),
]

# 텔레그램/UI 표시용 한글 라벨
SIGNAL_LABEL_KR = {
    'STRONG_UP': '강한상향', 'UP': '상향', 'NEUTRAL': '중립',
    'DOWN': '하향', 'STRONG_DOWN': '강한하향',
}

# 중립(약한 변화)은 알림으로 저장하지 않음 (revision_alerts = 의미있는 알림)
NEUTRAL_SIGNAL = 'NEUTRAL'


def classify_signal(revision_pct: float) -> Tuple[str, int]:
    """revision_pct(%) → (signal_code, priority). 경계는 상위 구간 포함(>=)."""
    if revision_pct >= 15.0:
        return ('STRONG_UP', 1)
    if revision_pct >= 5.0:
        return ('UP', 2)
    if revision_pct > -5.0:
        return ('NEUTRAL', 3)
    if revision_pct > -15.0:
        return ('DOWN', 2)
    return ('STRONG_DOWN', 1)


def _conn():
    c = sqlite3.connect(str(DB_PATH), timeout=30)
    c.row_factory = sqlite3.Row
    return c


# ============================================================
# 1. 백필: consensus_estimate → consensus_snapshot NTM
# ============================================================

def backfill_ntm_from_consensus_estimate(verbose: bool = True) -> Dict:
    """consensus_estimate 의 fwd_eps_1y 를 fetched_at 날짜 기준 NTM 스냅샷으로 시드.
    KR(6자리)만. 이미 있으면 INSERT OR IGNORE 로 건너뜀.
    과거 fetched_at(예: 04-27)이 베이스라인으로 들어가 리비전 계산 가능해짐."""
    conn = _conn()
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT stock_code, fwd_eps_1y, fwd_eps_1y_count, source,
               substr(fetched_at, 1, 10) AS snap_date
        FROM consensus_estimate
        WHERE fwd_eps_1y IS NOT NULL
          AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND fetched_at IS NOT NULL
    """).fetchall()

    seeded = 0
    for r in rows:
        snap_date = r['snap_date']
        try:
            year = int(snap_date[:4])
        except (ValueError, TypeError):
            continue
        cur.execute("""
            INSERT OR IGNORE INTO consensus_snapshot
                (stock_code, period_type, period_year, period_quarter, snapshot_date,
                 eps_consensus, analyst_count, source)
            VALUES (?, 'NTM', ?, 0, ?, ?, ?, ?)
        """, (r['stock_code'], year, snap_date, r['fwd_eps_1y'],
              r['fwd_eps_1y_count'], (r['source'] or 'consensus_estimate')))
        if cur.rowcount > 0:
            seeded += 1
    conn.commit()
    conn.close()
    if verbose:
        print(f"[backfill] consensus_estimate {len(rows)}행 → NTM 스냅샷 신규 {seeded}건")
    return {'scanned': len(rows), 'seeded': seeded}


# ============================================================
# 2. 리비전 계산
# ============================================================

def _save_alert(cur: sqlite3.Cursor, a: Dict) -> bool:
    """revision_alerts dedup INSERT.
    같은 (종목,기간,지표,윈도우,current_date) 가 이미 있으면 skip."""
    # 주의: 컬럼명 current_date 는 SQLite 예약 키워드 CURRENT_DATE 와 충돌 →
    #       반드시 큰따옴표로 식별자임을 명시해야 컬럼으로 해석됨 (미인용 시 dedup 깨짐).
    dup = cur.execute("""
        SELECT 1 FROM revision_alerts
        WHERE stock_code=? AND period_type=? AND period_year=? AND period_quarter=?
          AND metric=? AND window_days=? AND "current_date"=?
        LIMIT 1
    """, (a['stock_code'], a['period_type'], a['period_year'], a['period_quarter'],
          a['metric'], a['window_days'], a['current_date'])).fetchone()
    if dup:
        return False
    cur.execute("""
        INSERT INTO revision_alerts
            (stock_code, period_type, period_year, period_quarter,
             metric, revision_pct, window_days,
             baseline_value, current_value, baseline_date, current_date,
             signal, priority)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (a['stock_code'], a['period_type'], a['period_year'], a['period_quarter'],
          a['metric'], a['revision_pct'], a['window_days'],
          a['baseline_value'], a['current_value'], a['baseline_date'], a['current_date'],
          a['signal'], a['priority']))
    return True


def compute_revisions_for_stock(
    stock_code: str,
    windows: Optional[List[int]] = None,
    conn: Optional[sqlite3.Connection] = None,
    save: bool = True,
) -> Dict:
    """한 종목의 모든 (기간,지표,윈도우) 리비전 계산 → revision_alerts 저장.
    Returns: {'computed': n, 'alerts_new': n, 'neutral': n, 'rows': [...]}"""
    windows = windows or DEFAULT_WINDOWS
    own = conn is None
    if own:
        conn = _conn()
    cur = conn.cursor()

    # 이 종목이 가진 (기간) 그룹
    periods = cur.execute("""
        SELECT DISTINCT period_type, period_year, period_quarter
        FROM consensus_snapshot WHERE stock_code = ?
    """, (stock_code,)).fetchall()

    stats = {'computed': 0, 'alerts_new': 0, 'neutral': 0, 'rows': []}

    for p in periods:
        ptype, pyear, pq = p['period_type'], p['period_year'], p['period_quarter']
        metrics = METRICS_BY_PERIOD.get(ptype, ['eps'])
        for metric in metrics:
            col = METRIC_COLUMN[metric]
            # 해당 키의 (날짜, 값) 시계열 (값 not null)
            series = cur.execute(f"""
                SELECT snapshot_date, {col} AS val
                FROM consensus_snapshot
                WHERE stock_code=? AND period_type=? AND period_year=? AND period_quarter=?
                  AND {col} IS NOT NULL
                ORDER BY snapshot_date
            """, (stock_code, ptype, pyear, pq)).fetchall()
            if len(series) < 2:
                continue

            current = series[-1]
            cur_date, cur_val = current['snapshot_date'], current['val']
            if cur_val is None:
                continue

            for w in windows:
                # 베이스라인: cur_date - w 이전(<=) 중 가장 최신
                base = None
                for row in reversed(series[:-1]):
                    # date 문자열 비교 (YYYY-MM-DD 사전순 == 시간순)
                    if _days_between(row['snapshot_date'], cur_date) >= w:
                        base = row
                        break
                if base is None:
                    continue
                base_val = base['val']
                if base_val is None or base_val == 0:
                    continue

                revision_pct = round((cur_val - base_val) / abs(base_val) * 100, 2)
                signal, priority = classify_signal(revision_pct)
                stats['computed'] += 1

                rec = {
                    'stock_code': stock_code, 'period_type': ptype,
                    'period_year': pyear, 'period_quarter': pq,
                    'metric': metric, 'revision_pct': revision_pct, 'window_days': w,
                    'baseline_value': base_val, 'current_value': cur_val,
                    'baseline_date': base['snapshot_date'], 'current_date': cur_date,
                    'signal': signal, 'priority': priority,
                }
                stats['rows'].append(rec)

                if signal == NEUTRAL_SIGNAL:
                    stats['neutral'] += 1
                    continue  # 중립은 알림 저장 안 함
                if save and _save_alert(cur, rec):
                    stats['alerts_new'] += 1

    if own:
        conn.commit()
        conn.close()
    return stats


def _days_between(d_old: str, d_new: str) -> int:
    """'YYYY-MM-DD' 두 날짜 간 일수 (d_new - d_old)."""
    try:
        a = datetime.strptime(d_old[:10], '%Y-%m-%d')
        b = datetime.strptime(d_new[:10], '%Y-%m-%d')
        return (b - a).days
    except ValueError:
        return 0


def compute_all(windows: Optional[List[int]] = None, verbose: bool = True) -> Dict:
    """전 종목 리비전 계산."""
    windows = windows or DEFAULT_WINDOWS
    conn = _conn()
    cur = conn.cursor()
    codes = [r['stock_code'] for r in cur.execute(
        "SELECT DISTINCT stock_code FROM consensus_snapshot").fetchall()]

    total = {'stocks': len(codes), 'computed': 0, 'alerts_new': 0, 'neutral': 0,
             'by_signal': {}}
    for code in codes:
        s = compute_revisions_for_stock(code, windows, conn=conn, save=True)
        total['computed'] += s['computed']
        total['alerts_new'] += s['alerts_new']
        total['neutral'] += s['neutral']
        for r in s['rows']:
            total['by_signal'][r['signal']] = total['by_signal'].get(r['signal'], 0) + 1
    conn.commit()
    conn.close()

    if verbose:
        print(f"\n=== 리비전 계산 완료 ===")
        print(f"  대상 종목: {total['stocks']}")
        print(f"  계산 건수: {total['computed']} (윈도우 {windows})")
        print(f"  신규 알림: {total['alerts_new']}")
        print(f"  시그널 분포: {total['by_signal']}")
        print(f"  중립(미저장): {total['neutral']}")
    return total


# ============================================================
# 3. 분류 로직 자체 테스트
# ============================================================

def _self_test() -> bool:
    cases = [
        (30.0,  'STRONG_UP',   1),
        (15.0,  'STRONG_UP',   1),
        (14.99, 'UP',          2),
        (5.0,   'UP',          2),
        (4.99,  'NEUTRAL',     3),
        (0.0,   'NEUTRAL',     3),
        (-4.99, 'NEUTRAL',     3),
        (-5.0,  'DOWN',        2),
        (-14.99,'DOWN',        2),
        (-15.0, 'STRONG_DOWN', 1),
        (-40.0, 'STRONG_DOWN', 1),
    ]
    ok = True
    print("=== classify_signal 자체 테스트 ===")
    for pct, exp_sig, exp_pri in cases:
        sig, pri = classify_signal(pct)
        passed = (sig == exp_sig and pri == exp_pri)
        ok = ok and passed
        print(f"  {pct:>7.2f}% → {sig}/{pri}  기대 {exp_sig}/{exp_pri}  {'✅' if passed else '❌'}")
    print(f"결과: {'전체 통과 ✅' if ok else '실패 ❌'}")
    return ok


# ============================================================
# CLI
# ============================================================

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--backfill', action='store_true', help='consensus_estimate → NTM 스냅샷 시드')
    p.add_argument('--single', type=str, help='단일 종목 계산')
    p.add_argument('--all', action='store_true', help='전 종목 계산')
    p.add_argument('--verify', action='store_true', help='분류 로직 자체 테스트')
    p.add_argument('--windows', type=str, default=None, help='쉼표구분 윈도우(일), 예: 7,30,90')
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    windows = [int(x) for x in args.windows.split(',')] if args.windows else None

    if args.verify:
        _self_test()
    if args.backfill:
        backfill_ntm_from_consensus_estimate()
    if args.single:
        s = compute_revisions_for_stock(args.single, windows)
        print(f"\n[{args.single}] computed={s['computed']} alerts_new={s['alerts_new']} neutral={s['neutral']}")
        for r in s['rows']:
            print(f"  {r['period_type']} {r['period_year']}Q{r['period_quarter']} {r['metric']} "
                  f"w{r['window_days']}d: {r['baseline_value']}→{r['current_value']} "
                  f"({r['revision_pct']:+.2f}%) [{r['signal']}] "
                  f"{r['baseline_date']}→{r['current_date']}")
    if args.all:
        compute_all(windows)
    if not any([args.verify, args.backfill, args.single, args.all]):
        p.print_help()
