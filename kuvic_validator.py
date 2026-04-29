"""
KUVIC 세션 수동 분석 vs Step 4-3-C 자동 산출 비교
Step 4-3-D — 검증 도구

용도:
1. 자동 산출 시스템 정확도 검증
2. 컨센서스 보수성 vs KUVIC 적극성 갭 자동 표시
3. Step 2.5 제약사항 4개 해결 검증
"""

import sqlite3
import json
from pathlib import Path
from typing import Dict, Optional

DB_PATH = Path(__file__).parent / 'db' / 'dashboard.db'


# ============================================================
# KUVIC 세션 수동 분석
# ============================================================

KUVIC_SESSION_ANALYSIS = {
    '007660': {
        'name': '이수페타시스',
        'session_date': '2026-04-26',
        'segment': 'AI/반도체 / L1 패키징기판 FC-BGA/MLB',
        'bear_eps': 5800, 'bear_per': 12.5, 'bear_tp': 87000,
        'base_eps': 7200, 'base_per': 17.0, 'base_tp': 122400,
        'bull_eps': 7500, 'bull_per': 20.0, 'bull_tp': 150000,
        'thesis': 'CPU 비율 전환 이중 수혜 미반영 — MLB 글로벌 3강',
        'tags': ['미반영', '병목', 'CPU전환', 'MLB'],
        'conclusion': 'BUY',
        'priority': '★★★',
    },
    '064290': {
        'name': '인텍플러스',
        'session_date': '2026-04-26',
        'segment': 'AI/반도체 / L2 후공정 검사',
        'bear_eps': 2000, 'bear_per': 20.0, 'bear_tp': 40000,
        'base_eps': 3250, 'base_per': 25.0, 'base_tp': 81250,
        'bull_eps': 6000, 'bull_per': 30.0, 'bull_tp': 180000,
        'thesis': '인텔 유리기판 공동개발 + TSMC CoWoS 직납',
        'tags': ['미반영', '인텔공동개발', '유리기판', '턴어라운드'],
        'conclusion': 'BUY',
        'priority': '★★',
    },
    '267260': {
        'name': 'HD현대일렉',
        'session_date': '2026-04-26',
        'segment': '데이터센터/전력 / L1 변압기',
        'thesis': '765kV 초고압 변압기 글로벌 병목 — cycle 정점 우려',
        'tags': ['병목', 'AI데이터센터', '리드타임', '정점우려'],
        'conclusion': 'HOLD',
        'note': 'KUVIC 세션 TP 미정 (cycle 정점 확인 필요)',
        'priority': '★★',
    },
    '403870': {
        'name': 'HPSP',
        'session_date': '2026-04-26',
        'segment': 'AI/반도체 / L2 전공정 장비',
        'thesis': 'HPA 장비 단독 양산, OPM 57% — 진입장벽 5~7년',
        'tags': ['미반영', '독점', '진입장벽', '신규진입불가'],
        'conclusion': 'BUY',
        'note': 'KUVIC 세션 TP 미정 (Upside 24.5%, 대신증권)',
        'priority': '★★★',
    },
    '017960': {
        'name': '한국카본',
        'session_date': '2026-04-26',
        'segment': '조선 / L2 LNG 화물창 단열재',
        'thesis': '5년 PER 26분위 미반영, LNG 슬롯 매진',
        'tags': ['미반영', '병목', 'LNG'],
        'conclusion': 'BUY',
        'priority': '★★',
    },
    '183300': {
        'name': '코미코',
        'session_date': '2026-04-27',
        'segment': 'AI/반도체 / L2 검사세정 + ESC',
        'bear_eps': 5500, 'bear_per': 12.0, 'bear_tp': 66000,
        'base_eps': 6500, 'base_per': 13.5, 'base_tp': 87750,
        'bull_eps': 8300, 'bull_per': 15.0, 'bull_tp': 124500,
        'bull_extreme_tp': 170000,  # 테라팹 진입 시
        'thesis': '에이전틱=CPU 르네상스=인텔 부활=코미코 더블 히트',
        'tags': ['미반영', 'CPU전환', '글로벌파운드리동행', 'ESC', '테라팹'],
        'conclusion': 'BUY',
        'priority': '★★★',
    },
}


def _get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================
# 1. KUVIC vs 자동 산출 비교
# ============================================================

def compare_kuvic_vs_auto(stock_code: str) -> Dict:
    if stock_code not in KUVIC_SESSION_ANALYSIS:
        return {'error': f'KUVIC 세션 분석 없음: {stock_code}'}

    kuvic = KUVIC_SESSION_ANALYSIS[stock_code]

    try:
        from tam_modeler import build_auto_tam_with_label
        auto = build_auto_tam_with_label(stock_code)
    except Exception as e:
        return {'error': f'tam_modeler 실패: {e}'}

    if auto.get('method') == 'INSUFFICIENT_DATA':
        return {
            'stock_code': stock_code,
            'name': kuvic['name'],
            'priority': kuvic.get('priority', ''),
            'kuvic': kuvic,
            'auto_method': 'INSUFFICIENT_DATA',
            'auto_note': auto.get('note'),
        }

    comparison = {
        'method': auto['method'],
        'current_price': auto.get('current_price'),
    }

    for scenario in ('bear', 'base', 'bull'):
        kuvic_tp = kuvic.get(f'{scenario}_tp')
        auto_tp = auto.get(scenario, {}).get('tp')

        if kuvic_tp and auto_tp:
            gap_abs = auto_tp - kuvic_tp
            gap_pct = (gap_abs / kuvic_tp) * 100
            comparison[scenario] = {
                'kuvic_tp': kuvic_tp,
                'auto_tp': round(auto_tp),
                'gap_abs': round(gap_abs),
                'gap_pct': round(gap_pct, 1),
                'kuvic_eps': kuvic.get(f'{scenario}_eps'),
                'auto_eps': auto[scenario].get('eps'),
                'kuvic_per': kuvic.get(f'{scenario}_per'),
                'auto_per': auto[scenario].get('per'),
                'auto_eps_source': auto[scenario].get('eps_source'),
                'auto_per_source': auto[scenario].get('per_source'),
            }
        elif auto_tp:
            comparison[scenario] = {
                'auto_tp': round(auto_tp),
                'kuvic_tp': None,
                'note': 'KUVIC 세션 TP 미정',
            }

    return {
        'stock_code': stock_code,
        'name': kuvic['name'],
        'priority': kuvic.get('priority', ''),
        'session_date': kuvic['session_date'],
        'segment': kuvic['segment'],
        'thesis': kuvic['thesis'],
        'tags': kuvic.get('tags', []),
        'kuvic_conclusion': kuvic.get('conclusion'),
        'kuvic_note': kuvic.get('note'),
        'comparison': comparison,
        'consensus_tp': auto.get('consensus_tp'),
        'note': _generate_note(kuvic, auto, comparison),
    }


def _generate_note(kuvic: Dict, auto: Dict, comparison: Dict) -> str:
    notes = []

    if 'base' in comparison and 'gap_pct' in comparison['base']:
        gap_pct = comparison['base']['gap_pct']
        if abs(gap_pct) <= 10:
            notes.append(f"✅ Base TP 자동 산출이 KUVIC과 일치 ({gap_pct:+.1f}%)")
        elif gap_pct < -10:
            notes.append(f"⚠️ 자동 산출 Base가 KUVIC 대비 보수적 ({gap_pct:+.1f}%) — 컨센서스 미반영")
        elif gap_pct > 10:
            notes.append(f"⚠️ 자동 산출 Base가 KUVIC 대비 적극적 ({gap_pct:+.1f}%)")

    cons_tp = auto.get('consensus_tp')
    kuvic_base = kuvic.get('base_tp')
    if cons_tp and kuvic_base:
        cons_gap = (kuvic_base - cons_tp) / cons_tp * 100
        if cons_gap > 20:
            notes.append(f"💎 KUVIC 분석이 컨센서스보다 {cons_gap:+.1f}% 적극적")
        elif cons_gap < -20:
            notes.append(f"⚠️ KUVIC 분석이 컨센서스보다 {cons_gap:+.1f}% 보수적")

    return ' / '.join(notes) if notes else ''


def compare_all_kuvic_stocks():
    return [compare_kuvic_vs_auto(c) for c in KUVIC_SESSION_ANALYSIS.keys()]


# ============================================================
# 2. CLI 출력
# ============================================================

def print_comparison_table():
    results = compare_all_kuvic_stocks()

    print("\n" + "=" * 100)
    print("KUVIC 세션 분석 vs Step 4-3-C 자동 산출 비교")
    print("=" * 100)
    print(f"{'★':<3} {'종목':<14} {'KUVIC Base':>13} {'자동 Base':>13} {'갭':>9} {'컨센서스':>12} {'KUVIC결론':<8}")
    print("-" * 100)

    for r in results:
        if 'error' in r:
            print(f"{r['stock_code']}: {r['error']}")
            continue

        priority = r.get('priority', '')[:3]
        comp = r.get('comparison', {})
        cons_tp = r.get('consensus_tp')
        conclusion = r.get('kuvic_conclusion', '')

        if 'base' not in comp:
            method = r.get('auto_method', '?')
            print(f"{priority:<3} {r['name']:<14} {'N/A':>13} {method:>13}")
            continue

        base = comp['base']
        kuvic_str = f"{base['kuvic_tp']:,}" if base.get('kuvic_tp') else 'N/A'
        auto_str = f"{base['auto_tp']:,}" if base.get('auto_tp') else 'N/A'
        gap_str = f"{base.get('gap_pct', 0):+.1f}%" if base.get('gap_pct') is not None else '-'
        cons_str = f"{cons_tp:,.0f}" if cons_tp else 'N/A'

        print(f"{priority:<3} {r['name']:<14} {kuvic_str:>13} {auto_str:>13} {gap_str:>9} {cons_str:>12} {conclusion:<8}")

    print("=" * 100)

    print("\n📋 핵심 노트:")
    for r in results:
        note = r.get('note')
        if note:
            print(f"  {r.get('name', r.get('stock_code'))}: {note}")

    print("\n💡 투자 논리 (Thesis):")
    for r in results:
        if 'thesis' in r:
            tags_str = ' '.join(f"#{t}" for t in r.get('tags', [])[:3])
            print(f"  {r['name']:<14} {tags_str}")
            print(f"    → {r['thesis']}")


# ============================================================
# 3. Step 2.5 제약사항 자동 검증
# ============================================================

def verify_step25_constraints():
    print("\n" + "=" * 70)
    print("Step 2.5 제약사항 자동 해결 검증")
    print("=" * 70)

    conn = _get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT COUNT(*), AVG(quarters_covered)
        FROM valuation_band
        WHERE per_p50 IS NOT NULL
    """)
    n, avg_q = cur.fetchone()
    avg_q = avg_q or 0
    print(f"\n[1] PER 5년 밴드 (Step 2.5: ❌ → Step 4-2-B: ✅)")
    print(f"    종목 {n}개, 평균 {avg_q:.1f}분기 데이터 보유")
    print(f"    → calculate_per_percentile()이 자기 5년 밴드 백분위 사용")

    try:
        cur.execute("""SELECT COUNT(DISTINCT stock_code), COUNT(*)
                       FROM dart_disclosure_overhang WHERE status='ACTIVE'""")
        n_stocks, n_total = cur.fetchone()
    except sqlite3.OperationalError:
        n_stocks, n_total = 0, 0

    print(f"\n[2] 오버행 보정 (Step 2.5: ❌ → Step 4-2-B: 🟡)")
    if n_stocks > 0:
        print(f"    {n_stocks}종목, 활성 오버행 {n_total}건")
    else:
        print(f"    아직 미수집 (DART 차단 해제 후 overhang_parser.py all 실행 필요)")

    print(f"\n[3] Heat 통합 (Step 2.5: 🟡 → Step 4-2-B: 🟡)")
    print(f"    log scale + bottleneck +10 보너스 적용")

    print(f"\n[4] US 환율 (Step 2.5: ❌ → 미해결)")
    print(f"    여전히 1,300원 또는 1,476원 고정")

    cur.execute("SELECT source, COUNT(*) FROM consensus_estimate GROUP BY source")
    by_source = dict(cur.fetchall())
    print(f"\n[5] 컨센서스 통합 (Step 4-3-B 신규)")
    for src, cnt in by_source.items():
        print(f"    {src}: {cnt}종목")
    total_cons = sum(by_source.values())
    print(f"    합계: {total_cons}종목 (4-3-D 검증 시 활용)")

    print(f"\n[6] 자동 TAM 모델링 (Step 4-3-C 신규)")
    print(f"    Bear/Base/Bull EPS×PER → TP 자동 산출")
    print(f"    KUVIC 세션 종목 검증: print_comparison_table() 참조")

    conn.close()


# ============================================================
# 4. CLI
# ============================================================

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'all':
        print_comparison_table()
        verify_step25_constraints()
    elif len(sys.argv) > 1 and sys.argv[1] == 'constraints':
        verify_step25_constraints()
    elif len(sys.argv) > 1:
        code = sys.argv[1]
        result = compare_kuvic_vs_auto(code)
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    else:
        print("Usage:")
        print("  python3 kuvic_validator.py 007660           # 단일 비교")
        print("  python3 kuvic_validator.py all              # 전체 비교 + 제약 검증")
        print("  python3 kuvic_validator.py constraints      # 제약 검증만")
