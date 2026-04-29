"""
valuechain_map.json 매핑 적정성 LLM 검증 (Step 매핑 위생 점검)

대상: stocks.sector 키워드 매칭 실패 종목 (false-positive 가능)
LLM: qwen3:14b (배치 모드, keep_alive=0)

각 종목에 대해:
1. 세그먼트 정의 + 종목명 + sector + valuechain segment 의도 prompt
2. LLM 응답: VALID / INVALID / PARTIAL + 한 줄 근거
3. 결과 JSON 저장 → 사용자 검토 후 fix_valuechain_step3.py에 반영
"""

import json, sqlite3, time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

VC_PATH = Path(__file__).parent.parent / 'data' / 'valuechain_map.json'
DB_PATH = Path(__file__).parent.parent / 'db' / 'dashboard.db'
RESULT_PATH = Path(__file__).parent.parent / 'data' / 'valuechain_llm_validation.json'

OLLAMA_URL = 'http://localhost:11434/api/generate'
# phi4-mini: 2.5GB, thinking 없음, 빠른 응답 (qwen3:14b는 thinking 모드라 num_predict 부족)
MODEL = 'phi4-mini:latest'

# 세그먼트별 의도 정의 (prompt 강화용)
SEGMENT_INTENT = {
    'cloud_idc':           '클라우드 데이터센터 인프라 (서버, 네트워크 장비, 코로케이션)',
    'cooling_immersion':   '데이터센터 냉각/액침 솔루션 (공조, 액체냉각)',
    'front_end_equipment': '반도체 전공정 장비 (식각, 증착, 노광, CMP)',
    'back_end_equipment':  '반도체 후공정 장비 (테스트, 패키징, 본딩)',
    'inspection_cleaning': '반도체 검사/세정 장비 또는 소재',
    'specialty_gas':       '반도체용 특수가스 (NF3, WF6 등 산업가스)',
    'materials_chemicals': '반도체용 화학소재 (포토레지스트, CMP slurry, 전구체)',
    'fc_bga_mlb':          '패키징기판 FC-BGA, MLB 다층기판',
    'silicon_design':      '실리콘 설계 (팹리스, IP)',
    'npu_asic':            'AI 추론용 NPU/ASIC 설계 (자체칩 또는 자회사)',
    'game_creative_ai':    '게임/창작 분야 AI 응용 (생성형 AI 게임)',
    'submarine_cable':     '해저케이블 시공/제조',
    'cathode':             '배터리 양극재',
    'anode':               '배터리 음극재',
    'electrolyte_separator': '배터리 전해질/분리막',
    'ncm_cell':            'NCM 배터리 셀 제조',
    'bms_module':          'BMS / 배터리 모듈 시스템',
    'lng_cargo_insulation': 'LNG선 화물창 단열재',
    'ship_paint':          '선박용 도료',
    'gas_turbine':         '가스터빈 발전 기기',
    'nuclear_smr':         '원전 / SMR (소형 모듈 원자로)',
    'ammunition_shell':    '탄약/포탄/총포',
    'optics_eo':           '광학/EO (전자광학) 정밀 광학기기',
    'uhv_transformer':     '초고압 변압기 765kV급',
    'transformer_cable':   '변압기/전력케이블',
    'switchgear_meter':    '배전반/계량기',
    'silicon_wafer':       '실리콘 웨이퍼',
    'hbm_dram':            'HBM 고대역폭 메모리',
    'cxl_pim':             'CXL/PIM 메모리 솔루션',
    'naval_ship':          '함정/잠수함',
    'ship_propulsion':     '선박 추진/엔진',
    'shipbuilding':        '조선 본업',
    'scrubber_eco':        '환경설비/스크러버',
    'fc_aerospace':        '항공/우주',
    'guided_missile':      '유도미사일/무인기',
    'radar_avionics':      '레이더/항전',
    'transmission':        '송배전망',
    'distribution':        '배전',
    'energy_storage':      '에너지 저장 시스템 (ESS)',
}


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def call_ollama(prompt: str, timeout: int = 60) -> str:
    """qwen3:14b 호출 (배치 모드, keep_alive=0)."""
    payload = {
        'model': MODEL,
        'prompt': prompt,
        'stream': False,
        'keep_alive': 0,
        'options': {
            'temperature': 0.1,
            'num_predict': 400,
        },
    }
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(OLLAMA_URL, data=data,
                                  headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode('utf-8'))
        return resp.get('response', '').strip()
    except (urllib.error.URLError, TimeoutError) as e:
        return f'[ERROR: {e}]'


def build_prompt(stock_name: str, sector: str, segment_id: str,
                 segment_name_kr: str, theme_id: str) -> str:
    intent = SEGMENT_INTENT.get(segment_id, segment_name_kr)
    return f"""당신은 한국 주식 산업 분류 전문가입니다.

다음 매핑이 적절한지 평가하세요:

[종목]
- 회사명: {stock_name}
- 사업 섹터: {sector}

[제안된 매핑]
- 테마: {theme_id}
- 세그먼트: {segment_id} ({segment_name_kr})
- 세그먼트 의도: {intent}

[질문]
이 회사가 위 세그먼트의 사업을 실제로 영위합니까?

[답변 형식 — 한국어, 정확히 이 형식만]
판정: VALID | PARTIAL | INVALID
근거: (한 줄, 50자 이내. 회사 사업 사실 기반)

판정 기준:
- VALID: 메인 또는 주요 사업 영역
- PARTIAL: 일부 사업 또는 자회사 통해 관련
- INVALID: 사업 자체와 무관"""


def parse_response(text: str) -> dict:
    """LLM 응답 → {verdict, reason}"""
    if text.startswith('[ERROR'):
        return {'verdict': 'ERROR', 'reason': text}
    verdict = 'UNKNOWN'
    reason = text.strip()
    # 첫 50자 안에 VALID/PARTIAL/INVALID 검색
    for v in ('INVALID', 'PARTIAL', 'VALID'):
        if v in text.upper()[:200]:
            verdict = v
            break
    # 근거 추출
    import re
    m = re.search(r'근거\s*[:：]\s*(.+?)(?:\n|$)', text)
    if m:
        reason = m.group(1).strip()[:100]
    return {'verdict': verdict, 'reason': reason, 'raw': text[:300]}


def collect_suspicious() -> list:
    """sector 키워드 매칭 실패 종목 + valuechain 매핑 정보 수집."""
    THEME_KEYWORDS = {
        'AI_SEMI':         ['반도체','전자','전기','디스플레이','IT','테크','정보','통신장비'],
        'BATTERY_ESS':     ['전지','배터리','화학','전기','자동차부품','이차전지','소재'],
        'SHIPBUILDING':    ['조선','해양','기계','중공업','부품','강관','철강'],
        'DEFENSE':         ['방산','항공','우주','중공업','기계'],
        'DATACENTER_POWER':['전력','전기','중전기기','케이블','에너지','변압','발전'],
    }
    vc = json.loads(VC_PATH.read_text(encoding='utf-8'))
    conn = _get_db()
    suspicious = []
    for tid, theme in vc.get('themes', {}).items():
        for lid, layer in theme.get('layers', {}).items():
            for sid, seg in layer.get('segments', {}).items():
                for code in (seg.get('stocks_kr', []) or []):
                    row = conn.execute("SELECT name, sector, sectors_json FROM stocks WHERE code=?", (code,)).fetchone()
                    if not row: continue
                    name = row['name']
                    sector = row['sector']
                    sectors = sector
                    if row['sectors_json']:
                        try:
                            sj = json.loads(row['sectors_json'])
                            if sj: sectors = ' / '.join(sj[:3])
                        except: pass
                    kw_list = THEME_KEYWORDS.get(tid, [])
                    is_match = any(kw in (sectors or '') for kw in kw_list)
                    if not is_match:
                        suspicious.append({
                            'code': code, 'name': name,
                            'sector': sectors,
                            'theme': tid, 'layer': lid,
                            'segment': sid, 'segment_name': seg.get('name_kr',''),
                        })
    conn.close()
    return suspicious


def main():
    suspicious = collect_suspicious()
    print(f"📋 LLM 검증 대상: {len(suspicious)}건\n")

    # qwen3:14b 콜드 스타트 워밍업
    print("⏳ qwen3:14b 워밍업...")
    _ = call_ollama("안녕")
    print("✅ 워밍업 완료\n")

    results = []
    t0 = time.time()
    for i, s in enumerate(suspicious, 1):
        prompt = build_prompt(s['name'], s['sector'], s['segment'],
                              s['segment_name'], s['theme'])
        elapsed_s = time.time()
        text = call_ollama(prompt, timeout=60)
        elapsed = time.time() - elapsed_s

        parsed = parse_response(text)
        result = {**s, **parsed, 'elapsed_sec': round(elapsed, 1)}
        results.append(result)

        verdict_emoji = {'VALID': '✅', 'PARTIAL': '🟡',
                         'INVALID': '❌', 'UNKNOWN': '❓', 'ERROR': '⚠'}.get(parsed['verdict'], '?')
        print(f"  [{i:2d}/{len(suspicious)}] {verdict_emoji} {s['code']} {s['name'][:12]:12s} → "
              f"{s['theme']} > {s['segment']:20s} | {parsed['verdict']:8s} | "
              f"{parsed['reason'][:50]} ({elapsed:.1f}s)")

    total = time.time() - t0
    print(f"\n⏱  총 소요: {total:.1f}초 (평균 {total/len(suspicious):.1f}초/건)")

    # 결과 저장
    RESULT_PATH.write_text(
        json.dumps({
            'validated_at': datetime.now().isoformat(),
            'model': MODEL,
            'count': len(results),
            'results': results,
        }, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )
    print(f"\n📁 결과 저장: {RESULT_PATH}")

    # 요약
    by_verdict = {}
    for r in results:
        by_verdict.setdefault(r['verdict'], []).append(r)
    print(f"\n📊 판정 분포:")
    for v, items in by_verdict.items():
        print(f"  {v}: {len(items)}")

    # INVALID 종목 강조
    invalid = by_verdict.get('INVALID', [])
    if invalid:
        print(f"\n❌ 제거 권장 (INVALID {len(invalid)}건):")
        for r in invalid:
            print(f"  {r['code']} {r['name']:14s} → {r['theme']} > {r['segment_name']}")
            print(f"           이유: {r['reason']}")

    return results


if __name__ == '__main__':
    main()
