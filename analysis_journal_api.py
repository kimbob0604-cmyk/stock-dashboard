"""
Step 4-4: analysis_journal CRUD API

작업 단계:
- 4-4-B: CREATE
- 4-4-C: READ (단일 + 종목별 + 최근)
- 4-4-D: UPDATE
- 4-4-E: DELETE
- 4-4-F: KUVIC 임포트
- 4-4-G: Markdown export
- 4-4-H: Prefill (helper 모듈 분리)
- 4-4-I: 통계 (helper 모듈)

이 단계(4-4-B): create_journal() 함수 1개만.
"""

import sqlite3
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional

DB_PATH = Path(__file__).parent / 'db' / 'dashboard.db'


def _get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================
# 4-4-B. CREATE
# ============================================================

def create_journal(data: Dict) -> Dict:
    """신규 분석 1건 INSERT.

    필수: stock_code
    선택: 나머지 모두 (analysis_date 누락 시 today)
    """
    if 'stock_code' not in data or not data['stock_code']:
        return {'success': False, 'error': 'stock_code 필수'}

    now_iso = datetime.now().isoformat()
    today = datetime.now().strftime('%Y-%m-%d')

    stock_code = data['stock_code']
    analysis_date = data.get('analysis_date', today)

    # tags 처리 (list → JSON 문자열)
    tags = data.get('tags')
    if isinstance(tags, list):
        tags_json = json.dumps(tags, ensure_ascii=False)
    elif isinstance(tags, str):
        tags_json = tags
    else:
        tags_json = None

    conn = _get_db()
    cur = conn.cursor()

    # stock_name 자동 채움
    stock_name = data.get('stock_name')
    if not stock_name:
        cur.execute("SELECT name FROM stocks WHERE code = ?", (stock_code,))
        row = cur.fetchone()
        if row:
            stock_name = row['name']

    cur.execute("""
        INSERT INTO analysis_journal (
            stock_code, stock_name, analysis_date, analyst,
            theme_id, layer_id, segment_id, is_bottleneck,
            current_price, return_52w, fwd_per, fwd_per_band_pct,
            ev_ebitda, ev_ebitda_band_pct, market_cap,
            opm_estimate, opm_source,
            step3_peers_text,
            bear_eps, bear_eps_source, bear_per, bear_per_source, bear_tp,
            base_eps, base_eps_source, base_per, base_per_source, base_tp,
            bull_eps, bull_eps_source, bull_per, bull_per_source, bull_tp,
            reflection_score, reflection_label,
            memo, tags, conclusion, priority, thesis,
            created_at, updated_at
        ) VALUES (
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?,
            ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?
        )
    """, (
        stock_code, stock_name, analysis_date, data.get('analyst'),
        data.get('theme_id'), data.get('layer_id'), data.get('segment_id'),
        data.get('is_bottleneck', 0),
        data.get('current_price'), data.get('return_52w'),
        data.get('fwd_per'), data.get('fwd_per_band_pct'),
        data.get('ev_ebitda'), data.get('ev_ebitda_band_pct'),
        data.get('market_cap'),
        data.get('opm_estimate'), data.get('opm_source'),
        data.get('step3_peers_text'),
        data.get('bear_eps'), data.get('bear_eps_source'),
        data.get('bear_per'), data.get('bear_per_source'),
        data.get('bear_tp'),
        data.get('base_eps'), data.get('base_eps_source'),
        data.get('base_per'), data.get('base_per_source'),
        data.get('base_tp'),
        data.get('bull_eps'), data.get('bull_eps_source'),
        data.get('bull_per'), data.get('bull_per_source'),
        data.get('bull_tp'),
        data.get('reflection_score'), data.get('reflection_label'),
        data.get('memo'), tags_json,
        data.get('conclusion'), data.get('priority'),
        data.get('thesis'),
        now_iso, now_iso,
    ))

    new_id = cur.lastrowid
    conn.commit()
    conn.close()

    return {
        'success': True,
        'id': new_id,
        'stock_code': stock_code,
        'stock_name': stock_name,
        'analysis_date': analysis_date,
        'created_at': now_iso,
    }


# ============================================================
# 4-4-C. READ
# ============================================================

def read_journal(journal_id: int) -> Optional[Dict]:
    """id로 단일 분석 조회. tags JSON → list 자동 변환."""
    if not isinstance(journal_id, int) or journal_id <= 0:
        return None

    conn = _get_db()
    cur = conn.execute("SELECT * FROM analysis_journal WHERE id = ?", (journal_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    result = dict(row)
    if result.get('tags'):
        try:
            result['tags'] = json.loads(result['tags'])
        except (json.JSONDecodeError, TypeError):
            pass  # 원본 유지 (디버깅용)
    return result


def list_recent_journals(limit: int = 30) -> list:
    """전체 종목 통합 최근 분석 (analysis_date DESC, id DESC).
    stock_name 누락 시 stocks 테이블 LEFT JOIN으로 보강. tags 자동 파싱."""
    if not isinstance(limit, int) or limit < 1:
        limit = 30
    elif limit > 100:
        limit = 100

    conn = _get_db()
    cur = conn.execute("""
        SELECT j.*, s.name as joined_stock_name
        FROM analysis_journal j
        LEFT JOIN stocks s ON j.stock_code = s.code
        ORDER BY j.analysis_date DESC, j.id DESC
        LIMIT ?
    """, (limit,))

    results = []
    for row in cur.fetchall():
        d = dict(row)
        if not d.get('stock_name') and d.get('joined_stock_name'):
            d['stock_name'] = d['joined_stock_name']
        d.pop('joined_stock_name', None)
        if d.get('tags'):
            try:
                d['tags'] = json.loads(d['tags'])
            except (json.JSONDecodeError, TypeError):
                pass
        results.append(d)
    conn.close()
    return results


# ============================================================
# 4-4-D. UPDATE
# ============================================================

UPDATABLE_FIELDS = {
    'stock_name', 'analysis_date', 'analyst',
    'theme_id', 'layer_id', 'segment_id', 'is_bottleneck',
    'current_price', 'return_52w', 'fwd_per', 'fwd_per_band_pct',
    'ev_ebitda', 'ev_ebitda_band_pct', 'market_cap',
    'opm_estimate', 'opm_source',
    'step3_peers_text',
    'bear_eps', 'bear_eps_source', 'bear_per', 'bear_per_source', 'bear_tp',
    'base_eps', 'base_eps_source', 'base_per', 'base_per_source', 'base_tp',
    'bull_eps', 'bull_eps_source', 'bull_per', 'bull_per_source', 'bull_tp',
    'reflection_score', 'reflection_label',
    'memo', 'tags', 'conclusion', 'priority', 'thesis',
}


def update_journal(journal_id: int, updates: dict) -> dict:
    """분석 부분 수정 (PATCH). 허용 안 된 필드는 rejected_fields에 분리."""
    if not isinstance(journal_id, int) or journal_id <= 0:
        return {'success': False, 'error': '유효하지 않은 id'}
    if not isinstance(updates, dict) or not updates:
        return {'success': False, 'error': '업데이트 데이터 없음'}

    conn = _get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM analysis_journal WHERE id = ?", (journal_id,))
    if not cur.fetchone():
        conn.close()
        return {'success': False, 'error': f'id {journal_id} 없음'}

    set_clauses = []
    values = []
    accepted_fields = []
    rejected_fields = []

    for key, val in updates.items():
        if key not in UPDATABLE_FIELDS:
            rejected_fields.append(key)
            continue
        if key == 'tags':
            if isinstance(val, list):
                val = json.dumps(val, ensure_ascii=False)
            elif val is None:
                pass
            elif not isinstance(val, str):
                rejected_fields.append(key)
                continue
        set_clauses.append(f"{key} = ?")
        values.append(val)
        accepted_fields.append(key)

    if not accepted_fields:
        conn.close()
        return {
            'success': False,
            'error': '업데이트 가능한 필드 없음',
            'rejected_fields': rejected_fields,
        }

    now_iso = datetime.now().isoformat()
    set_clauses.append("updated_at = ?")
    values.append(now_iso)
    values.append(journal_id)

    cur.execute(f"""
        UPDATE analysis_journal SET {', '.join(set_clauses)}
        WHERE id = ?
    """, values)
    conn.commit()
    conn.close()

    return {
        'success': True,
        'id': journal_id,
        'updated_fields': accepted_fields,
        'rejected_fields': rejected_fields if rejected_fields else None,
        'updated_at': now_iso,
    }


# ============================================================
# 4-4-E. DELETE
# ============================================================

def delete_journal(journal_id: int) -> dict:
    """분석 hard delete."""
    if not isinstance(journal_id, int) or journal_id <= 0:
        return {'success': False, 'error': '유효하지 않은 id'}

    conn = _get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM analysis_journal WHERE id = ?", (journal_id,))
    affected = cur.rowcount
    conn.commit()
    conn.close()

    if affected == 0:
        return {'success': False, 'error': f'id {journal_id} 없음'}
    return {'success': True, 'id': journal_id, 'deleted_count': affected}


# ============================================================
# 4-4-F. KUVIC 세션 임포트
# ============================================================

def _parse_kuvic_segment(segment_str: str) -> dict:
    """KUVIC segment 문자열 → theme_id 추정."""
    if not segment_str:
        return {}
    s = segment_str.upper()
    theme_map = {
        'AI/반도체': 'AI_SEMI', 'AI/SEMI': 'AI_SEMI',
        '데이터센터': 'DATACENTER_POWER', '전력': 'DATACENTER_POWER',
        '조선': 'SHIPBUILDING',
        '방산': 'DEFENSE',
        '2차전지': 'BATTERY_ESS', 'ESS': 'BATTERY_ESS', '배터리': 'BATTERY_ESS',
    }
    theme_id = None
    for kr, tid in theme_map.items():
        if kr.upper() in s:
            theme_id = tid
            break
    return {'theme_id': theme_id}


def import_kuvic_session_analysis(skip_duplicates: bool = True) -> dict:
    """KUVIC_SESSION_ANALYSIS 6종목 일괄 INSERT.
    skip_duplicates=True: (stock_code, analysis_date, analyst='KUVIC') 동일 시 SKIP."""
    try:
        from kuvic_validator import KUVIC_SESSION_ANALYSIS
    except ImportError:
        return {'success': False, 'error': 'kuvic_validator 임포트 실패'}

    imported, skipped, failed = [], [], []

    conn = _get_db()
    cur = conn.cursor()

    for code, kuvic in KUVIC_SESSION_ANALYSIS.items():
        analysis_date = kuvic.get('session_date', '2026-04-26')

        if skip_duplicates:
            cur.execute("""
                SELECT id FROM analysis_journal
                WHERE stock_code = ? AND analysis_date = ? AND analyst = 'KUVIC'
            """, (code, analysis_date))
            if cur.fetchone():
                skipped.append(code)
                continue

        seg_info = _parse_kuvic_segment(kuvic.get('segment', ''))
        step3_text = kuvic.get('note', '') or ''
        if kuvic.get('segment'):
            step3_text = f"세그먼트: {kuvic['segment']}\n{step3_text}".strip()

        try:
            data = {
                'stock_code': code,
                'analyst': 'KUVIC',
                'analysis_date': analysis_date,
                'theme_id': seg_info.get('theme_id'),
                'step3_peers_text': step3_text if step3_text else None,
                'bear_eps': kuvic.get('bear_eps'),
                'bear_per': kuvic.get('bear_per'),
                'bear_tp': kuvic.get('bear_tp'),
                'base_eps': kuvic.get('base_eps'),
                'base_per': kuvic.get('base_per'),
                'base_tp': kuvic.get('base_tp'),
                'bull_eps': kuvic.get('bull_eps'),
                'bull_per': kuvic.get('bull_per'),
                'bull_tp': kuvic.get('bull_tp'),
                'thesis': kuvic.get('thesis'),
                'tags': kuvic.get('tags', []),
                'conclusion': kuvic.get('conclusion'),
                'priority': kuvic.get('priority'),
                'memo': kuvic.get('note'),
            }
            r = create_journal(data)
            if r.get('success'):
                imported.append(code)
            else:
                failed.append({'code': code, 'error': r.get('error')})
        except Exception as e:
            failed.append({'code': code, 'error': str(e)})

    conn.close()

    return {
        'success': True,
        'imported': imported,
        'skipped': skipped,
        'failed': failed,
        'total_imported': len(imported),
        'total_skipped': len(skipped),
        'total_failed': len(failed),
    }


# ============================================================
# 4-4-G. Markdown Export (5분 피치 형식)
# ============================================================

def export_to_markdown(journal_id: int) -> str:
    """단일 분석 → Markdown (5단계 프레임)."""
    j = read_journal(journal_id)
    if not j:
        return f"# 분석 ID {journal_id} 없음"

    name = j.get('stock_name') or j['stock_code']
    code = j['stock_code']

    lines = [f"# {name} ({code}) — 분석 시트"]

    meta = []
    if j.get('analyst'): meta.append(f"**분석가:** {j['analyst']}")
    if j.get('analysis_date'): meta.append(f"**날짜:** {j['analysis_date']}")
    if j.get('priority'): meta.append(f"**우선순위:** {j['priority']}")
    if meta:
        lines.append("\n" + " | ".join(meta))

    # 한 줄 결론
    lines.append("\n## 한 줄 결론")
    if j.get('conclusion'):
        lines.append(f"\n> **{j['conclusion']}**")
        if j.get('base_tp') and j.get('current_price'):
            try:
                upside = (j['base_tp'] - j['current_price']) / j['current_price'] * 100
                lines.append(f"> Base TP {j['base_tp']:,.0f}원 (현재 {j['current_price']:,.0f}원, Upside {upside:+.1f}%)")
            except (TypeError, ZeroDivisionError):
                pass
    if j.get('thesis'):
        lines.append(f"\n**핵심 논리:** {j['thesis']}")
    if j.get('tags') and isinstance(j['tags'], list):
        tags_str = ' '.join(f"`#{t}`" for t in j['tags'])
        lines.append(f"\n**태그:** {tags_str}")

    # STEP 1
    lines.append("\n## STEP 1. 밸류체인 위치")
    vc = []
    if j.get('theme_id'): vc.append(j['theme_id'])
    if j.get('layer_id'): vc.append(j['layer_id'])
    if j.get('segment_id'): vc.append(j['segment_id'])
    if vc:
        lines.append(f"\n- **매핑:** {' > '.join(vc)}")
        if j.get('is_bottleneck'):
            lines.append("- **★ 병목 포인트**")
    else:
        lines.append("\n_매핑 정보 없음_")

    # STEP 2
    lines.append("\n## STEP 2. 개별 요인 (정량)")
    metrics = []
    if j.get('current_price'):
        metrics.append(f"- 현재가: {j['current_price']:,.0f}원")
    if j.get('return_52w') is not None:
        metrics.append(f"- 52주 수익률: {j['return_52w']:+.1f}%")
    if j.get('market_cap'):
        cap_jo = j['market_cap'] / 1_000_000
        if cap_jo >= 1:
            metrics.append(f"- 시가총액: {cap_jo:.2f}조원")
        else:
            metrics.append(f"- 시가총액: {j['market_cap']/100:.0f}억원")
    if j.get('fwd_per'):
        bt = f" (5년 P{j['fwd_per_band_pct']:.0f})" if j.get('fwd_per_band_pct') is not None else ""
        metrics.append(f"- Fwd PER: {j['fwd_per']:.1f}x{bt}")
    if j.get('ev_ebitda'):
        bt = f" (5년 P{j['ev_ebitda_band_pct']:.0f})" if j.get('ev_ebitda_band_pct') is not None else ""
        metrics.append(f"- EV/EBITDA: {j['ev_ebitda']:.1f}x{bt}")
    if j.get('opm_estimate'):
        src = f" ({j['opm_source']})" if j.get('opm_source') else ""
        metrics.append(f"- OPM 추정: {j['opm_estimate']:.1f}%{src}")
    if metrics:
        lines.extend(['\n' + m for m in metrics])
    else:
        lines.append("\n_정량 데이터 없음_")

    # STEP 3
    lines.append("\n## STEP 3. 동종 비교")
    if j.get('step3_peers_text'):
        lines.append(f"\n{j['step3_peers_text']}")
    else:
        lines.append("\n_동종 비교 미작성_")

    # STEP 4
    lines.append("\n## STEP 4. TAM 모델 (Bear / Base / Bull)")
    has_tam = any([j.get('bear_tp'), j.get('base_tp'), j.get('bull_tp')])
    if has_tam:
        lines.append("\n| 시나리오 | EPS | PER | TP | 출처 |")
        lines.append("|---|---|---|---|---|")
        for scenario in ['bear', 'base', 'bull']:
            eps = j.get(f'{scenario}_eps')
            per = j.get(f'{scenario}_per')
            tp = j.get(f'{scenario}_tp')
            if not (eps and per and tp):
                continue
            eps_src = j.get(f'{scenario}_eps_source', '-') or '-'
            per_src = j.get(f'{scenario}_per_source', '-') or '-'
            sources = f"{eps_src} / {per_src}"
            label = scenario.capitalize()
            if scenario == 'base':
                row = f"| **{label}** | **{eps:,.0f}** | **{per:.1f}x** | **{tp:,.0f}원** | {sources} |"
            else:
                row = f"| {label} | {eps:,.0f} | {per:.1f}x | {tp:,.0f}원 | {sources} |"
            lines.append(row)
    else:
        lines.append("\n_TAM 모델 미작성_")

    # STEP 5
    lines.append("\n## STEP 5. 시장 반영도")
    if j.get('reflection_score') is not None:
        score = j['reflection_score']
        label = j.get('reflection_label', '')
        lines.append(f"\n- **반영도 점수:** {score}/100 ({label})")
    else:
        lines.append("\n_반영도 점수 미산출_")

    # 메모
    if j.get('memo'):
        lines.append("\n## 분석 메모")
        lines.append(f"\n{j['memo']}")

    # 푸터
    lines.append("\n---")
    timestamp = j.get('updated_at') or j.get('created_at') or '-'
    lines.append(f"\n*최종 수정: {timestamp[:19]}*")
    lines.append(f"*Analysis ID: {j['id']}*")

    return '\n'.join(lines)


def list_journals_by_stock(stock_code: str, limit: int = 10) -> list:
    """종목별 분석 이력 (최신순). tags JSON → list 자동 변환.
    정렬: analysis_date DESC, id DESC.
    limit 안전성: 1~100 범위 강제."""
    if not stock_code:
        return []

    if not isinstance(limit, int) or limit < 1:
        limit = 10
    elif limit > 100:
        limit = 100

    conn = _get_db()
    cur = conn.execute("""
        SELECT * FROM analysis_journal
        WHERE stock_code = ?
        ORDER BY analysis_date DESC, id DESC
        LIMIT ?
    """, (stock_code, limit))

    results = []
    for row in cur.fetchall():
        d = dict(row)
        if d.get('tags'):
            try:
                d['tags'] = json.loads(d['tags'])
            except (json.JSONDecodeError, TypeError):
                pass
        results.append(d)

    conn.close()
    return results


# ============================================================
# CLI
# ============================================================

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 analysis_journal_api.py test_create")
        print("  python3 analysis_journal_api.py read <id>")
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd == 'test_create':
        test_data = {
            'stock_code': '183300',
            'analyst': '범준',
            'theme_id': 'AI_SEMI',
            'layer_id': 'L2_soboojang',
            'segment_id': 'inspection_cleaning',
            'is_bottleneck': 0,
            'opm_estimate': 22.9,
            'opm_source': '미래에셋 추정',
            'step3_peers_text': '티씨케이 PER 14x / 원익QnC 10x / 한미반도체 25x — 코미코 9.7x 동종 대비 30% 할인',
            'bear_eps': 5500, 'bear_eps_source': 'CONSENSUS_×0.7',
            'bear_per': 12.0, 'bear_per_source': '5Y_P25',
            'bear_tp': 66000,
            'base_eps': 6500, 'base_eps_source': 'CONSENSUS',
            'base_per': 13.5, 'base_per_source': '5Y_P50',
            'base_tp': 87750,
            'bull_eps': 8300, 'bull_eps_source': 'CONSENSUS_HIGH',
            'bull_per': 15.0, 'bull_per_source': 'PEER_AVG×1.1',
            'bull_tp': 124500,
            'memo': '에이전틱 AI = CPU 르네상스 흐름에서 코미코 더블 히트',
            'tags': ['미반영', 'CPU전환', '글로벌파운드리동행', 'ESC'],
            'conclusion': 'BUY',
            'priority': '★★★',
            'thesis': '에이전틱 = CPU 르네상스 = 인텔 부활 = 코미코 더블 히트',
        }
        result = create_journal(test_data)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif cmd == 'read':
        if len(sys.argv) < 3:
            print("Usage: python3 analysis_journal_api.py read <id>")
            sys.exit(1)
        try:
            jid = int(sys.argv[2])
        except ValueError:
            print(f"id는 정수여야 합니다: {sys.argv[2]}")
            sys.exit(1)
        result = read_journal(jid)
        if result:
            print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        else:
            print(f"id {jid} 없음")
            sys.exit(1)

    elif cmd == 'stock':
        if len(sys.argv) < 3:
            print("Usage: python3 analysis_journal_api.py stock <code> [limit]")
            sys.exit(1)
        code = sys.argv[2]
        try:
            limit = int(sys.argv[3]) if len(sys.argv) > 3 else 10
        except ValueError:
            print(f"limit은 정수여야 합니다: {sys.argv[3]}")
            sys.exit(1)

        results = list_journals_by_stock(code, limit)
        if not results:
            print(f"{code}: 분석 이력 없음")
            sys.exit(0)

        print(f"\n=== {code} 분석 이력 {len(results)}건 ===\n")
        for j in results:
            tags_str = ''
            if j.get('tags') and isinstance(j['tags'], list):
                tags_str = ' '.join(f"#{t}" for t in j['tags'][:4])
            print(f"  [{j['id']}] {j.get('analysis_date', '?')} | "
                  f"{j.get('analyst', '?')} | "
                  f"{j.get('conclusion', '?')} | "
                  f"{j.get('priority', '')} | "
                  f"{tags_str}")
            if j.get('thesis'):
                print(f"      → {j['thesis'][:120]}")
            if j.get('base_tp'):
                bp = j.get('base_per') or 0
                be = j.get('base_eps') or 0
                print(f"      Base TP: {j['base_tp']:,.0f}원 ({bp:.1f}x × {be:,.0f})")
        print()

    elif cmd == 'recent':
        try:
            limit = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        except ValueError:
            print(f"limit은 정수: {sys.argv[2]}")
            sys.exit(1)
        results = list_recent_journals(limit)
        if not results:
            print("분석 이력 없음")
            sys.exit(0)
        print(f"\n=== 최근 분석 {len(results)}건 ===\n")
        for j in results:
            tags_str = ''
            if j.get('tags') and isinstance(j['tags'], list):
                tags_str = ' '.join(f"#{t}" for t in j['tags'][:3])
            name = j.get('stock_name') or j.get('stock_code')
            print(f"  [{j['id']}] {j.get('analysis_date', '?')} | "
                  f"{name} ({j['stock_code']}) | "
                  f"{j.get('conclusion', '?')} | "
                  f"{j.get('priority', '')} {tags_str}")
            if j.get('thesis'):
                print(f"      → {j['thesis'][:100]}")
        print()

    elif cmd == 'update':
        if len(sys.argv) < 4:
            print('Usage: python3 analysis_journal_api.py update <id> \'{"field":"value"}\'')
            sys.exit(1)
        try:
            jid = int(sys.argv[2])
            updates = json.loads(sys.argv[3])
        except ValueError:
            print(f"id는 정수: {sys.argv[2]}")
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"JSON 파싱 실패: {e}")
            sys.exit(1)
        result = update_journal(jid, updates)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if not result.get('success'):
            sys.exit(1)

    elif cmd == 'delete':
        if len(sys.argv) < 3:
            print("Usage: python3 analysis_journal_api.py delete <id>")
            sys.exit(1)
        try:
            jid = int(sys.argv[2])
        except ValueError:
            print(f"id는 정수: {sys.argv[2]}")
            sys.exit(1)
        existing = read_journal(jid)
        if not existing:
            print(f"id {jid} 없음")
            sys.exit(1)
        print(f"\n삭제 대상:")
        print(f"  [{jid}] {existing.get('stock_name', existing.get('stock_code'))} "
              f"| {existing.get('analysis_date')} | {existing.get('conclusion')}")
        try:
            confirm = input("\n삭제 확인 (yes 입력): ").strip().lower()
        except EOFError:
            confirm = ''
        if confirm != 'yes':
            print("취소됨")
            sys.exit(0)
        result = delete_journal(jid)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if not result.get('success'):
            sys.exit(1)

    elif cmd == 'import_kuvic':
        force = '--force' in sys.argv
        result = import_kuvic_session_analysis(skip_duplicates=not force)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if result.get('total_failed', 0) > 0:
            sys.exit(1)

    elif cmd == 'export':
        if len(sys.argv) < 3:
            print("Usage: python3 analysis_journal_api.py export <id> [--save <path>]")
            sys.exit(1)
        try:
            jid = int(sys.argv[2])
        except ValueError:
            print(f"id는 정수: {sys.argv[2]}")
            sys.exit(1)
        md = export_to_markdown(jid)
        if '--save' in sys.argv:
            try:
                save_idx = sys.argv.index('--save')
                save_path = sys.argv[save_idx + 1]
                Path(save_path).write_text(md, encoding='utf-8')
                print(f"✅ 저장됨: {save_path}")
            except (IndexError, IOError) as e:
                print(f"저장 실패: {e}")
                sys.exit(1)
        else:
            print(md)
