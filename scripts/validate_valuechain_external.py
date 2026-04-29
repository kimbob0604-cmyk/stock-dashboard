"""
valuechain_map.json 외부 데이터 검증 (옵션 D)

데이터 소스: 네이버 coinfo.naver?target=coinfo 페이지의 "기업개요" 섹션
LLM 사용 X — 정규식 + 키워드 매칭으로 객관적 판정.

판정 로직:
  - 세그먼트 핵심 키워드 사전 매칭
  - 2+ 키워드 매칭 → VALID
  - 1 키워드 매칭 → PARTIAL
  - 0 키워드 매칭 → INVALID
"""

import json, re, time, sqlite3
import urllib.request, urllib.error
from pathlib import Path
from datetime import datetime

VC_PATH = Path(__file__).parent.parent / 'data' / 'valuechain_map.json'
DB_PATH = Path(__file__).parent.parent / 'db' / 'dashboard.db'
RESULT_PATH = Path(__file__).parent.parent / 'data' / 'valuechain_external_validation.json'
PROFILE_CACHE = Path(__file__).parent.parent / 'cache' / 'naver_company_profiles.json'

# 세그먼트 핵심 키워드 (네이버 기업개요 텍스트 매칭용)
# 키 = segment_id, 값 = (메인 키워드 리스트, 보조 키워드 리스트)
SEGMENT_KEYWORDS = {
    # AI / 반도체
    'materials_chemicals':   (['반도체 소재', '디스플레이 소재', '포토레지스트', 'CMP', 'slurry', '전구체', '전자재료', 'photoresist'],
                              ['반도체', '디스플레이', '소재', '화학']),
    'specialty_gas':         (['특수가스', 'NF3', 'WF6', '고순도 가스', '산업가스', 'N2O', 'NH3', 'Si2H6'],
                              ['반도체', '디스플레이', '가스']),
    'inspection_cleaning':   (['세정', '코팅', '검사', '재생', '세라믹 부품', '웨이퍼 검사'],
                              ['반도체', '공정 장비', '부품']),
    'back_end_equipment':    ['후공정', '패키징', '본딩', '테스트 장비', '프로브', '핸들러'],
    'front_end_equipment':   ['전공정', '식각', '증착', '노광', 'CVD', 'PVD', 'CMP 장비'],
    'cooling':               ['냉각', '공조', '액침', 'HVAC', '데이터센터'],
    'cooling_immersion':     ['냉각', '공조', '액침', 'HVAC', '데이터센터'],
    'cloud_idc':             ['클라우드', '데이터센터', 'IDC', '코로케이션'],
    'npu_asic':              ['NPU', 'ASIC', 'AI 반도체', 'AI 칩', '신경망', '자체칩'],
    'game_creative':         ['게임', '게임 개발', '엔터테인먼트'],
    'game_creative_ai':      ['게임', '엔터테인먼트'],
    'submarine_cable':       ['해저', '케이블', '시공', '해양 케이블', '광케이블'],
    'fc_bga_mlb':            ['FC-BGA', '패키지 기판', 'MLB', '인쇄회로기판', '다층기판', 'PCB'],
    'silicon_wafer':         ['웨이퍼', '실리콘 웨이퍼', 'silicon wafer'],
    'hbm_dram':              ['HBM', 'DRAM', '메모리'],
    'silicon_design':        ['팹리스', '반도체 설계', 'IP'],
    # 배터리
    'cathode':               ['양극재', 'NCM', 'NCA', 'cathode'],
    'anode':                 ['음극재', '실리콘 음극재', '천연 흑연', '인조 흑연', 'anode'],
    'electrolyte_separator': ['전해질', '분리막', '전해액', 'separator'],
    'ncm_cell':              ['NCM', '배터리 셀', '이차전지', '리튬이온'],
    'bms':                   ['BMS', '배터리 관리', '배터리 모듈'],
    'bms_module':            ['BMS', '배터리 관리', '배터리 모듈'],
    # 조선
    'lng_insulation':        ['LNG', '단열재', 'CCS', '폴리우레탄', '화물창'],
    'lng_cargo_insulation':  ['LNG', '단열재', 'CCS', '폴리우레탄', '화물창'],
    'marine_paint':          ['도료', '선박 도료', '코팅', '페인트', '방오제'],
    'ship_paint':            ['도료', '선박 도료', '코팅', '페인트'],
    'shipbuilding':          ['선박', '조선', '특수선'],
    'naval_ship':            ['함정', '잠수함', '특수선'],
    'ship_propulsion':       ['선박 엔진', '추진'],
    'scrubber_eco':          ['스크러버', '환경설비', '배출 저감'],
    # 방산
    'optics_electro':        ['광학', 'EO', '전자광학', '센서'],
    'optics_eo':             ['광학', 'EO', '전자광학', '센서'],
    'ammunition':            ['탄약', '포탄', '추진화약', '정밀 단조'],
    'ammunition_shell':      ['탄약', '포탄', '추진화약'],
    'guided_missile':        ['유도미사일', '미사일', '무인기'],
    'radar_avionics':        ['레이더', '항전', '전자장비'],
    'fc_aerospace':          ['항공', '우주', '엔진', '우주발사체'],
    # 데이터센터/전력
    'gas_turbine':           ['가스터빈', '발전', '발전 설비'],
    'nuclear_smr':           ['SMR', '원자로', '원전', '소형 모듈'],
    'transmission':          ['송전', '송배전', '변전'],
    'distribution':          ['배전', '배전반', '계량기'],
    'switchgear_meter':      ['배전반', '계량기', '스위치기어'],
    'transformer_cable':     ['변압기', '전력케이블', '송전케이블'],
    'uhv_transformer':       ['초고압 변압기', '765kV', '대형 변압기', '변압기'],
    'energy_storage':        ['ESS', '에너지 저장'],
}


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def fetch_naver_profile(stock_code: str) -> str:
    """coinfo.naver?target=coinfo 페이지의 기업개요 섹션 텍스트 추출."""
    url = f"https://finance.naver.com/item/coinfo.naver?code={stock_code}&target=coinfo"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
    except (urllib.error.URLError, TimeoutError):
        return ''
    text = ''
    for enc in ('utf-8', 'euc-kr', 'cp949'):
        try: text = raw.decode(enc); break
        except UnicodeDecodeError: continue
    if not text:
        return ''
    # 기업개요 섹션 추출 (있으면 사용)
    for kw in ['기업개요', '회사개요', '주요사업']:
        idx = text.find(kw)
        if idx > 0:
            section = text[idx:idx+1500]
            cleaned = re.sub(r'<[^>]+>', ' ', section)
            cleaned = re.sub(r'\s+', ' ', cleaned)
            return cleaned[:1200]
    return ''


def normalize_keywords(value):
    """SEGMENT_KEYWORDS는 list 또는 (main, sub) tuple. 통일."""
    if isinstance(value, tuple) and len(value) == 2:
        return list(value[0]), list(value[1])
    elif isinstance(value, list):
        return value, []
    return [], []


def match_keywords(profile_text: str, segment_id: str) -> dict:
    """기업개요 텍스트에 세그먼트 키워드 매칭."""
    if segment_id not in SEGMENT_KEYWORDS:
        return {'verdict': 'NO_KEYWORD_DICT', 'main_hits': [], 'sub_hits': []}
    main_kws, sub_kws = normalize_keywords(SEGMENT_KEYWORDS[segment_id])
    if not profile_text:
        return {'verdict': 'NO_PROFILE', 'main_hits': [], 'sub_hits': []}
    main_hits = [k for k in main_kws if k in profile_text]
    sub_hits = [k for k in sub_kws if k in profile_text]
    n_main = len(main_hits)
    n_sub = len(sub_hits)
    # 판정: 메인 2+ → VALID, 메인 1 또는 메인 1+서브 1 → PARTIAL, 둘 다 0 → INVALID
    if n_main >= 2:
        verdict = 'VALID'
    elif n_main >= 1 and n_sub >= 1:
        verdict = 'VALID'
    elif n_main == 1 or n_sub >= 2:
        verdict = 'PARTIAL'
    else:
        verdict = 'INVALID'
    return {'verdict': verdict, 'main_hits': main_hits, 'sub_hits': sub_hits}


def collect_suspicious() -> list:
    """sector 키워드 매칭 실패 종목."""
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
                    sectors = row['sector']
                    if row['sectors_json']:
                        try:
                            sj = json.loads(row['sectors_json'])
                            if sj: sectors = ' / '.join(sj[:3])
                        except: pass
                    kw_list = THEME_KEYWORDS.get(tid, [])
                    if not any(kw in (sectors or '') for kw in kw_list):
                        suspicious.append({
                            'code': code, 'name': row['name'],
                            'sector': sectors, 'theme': tid, 'layer': lid,
                            'segment': sid, 'segment_name': seg.get('name_kr',''),
                        })
    conn.close()
    return suspicious


def main():
    suspicious = collect_suspicious()
    print(f"📋 외부 검증 대상: {len(suspicious)}건\n")

    # 프로필 캐시 (중복 종목 fetch 방지 — 원익머트리얼즈 3건 등)
    profile_cache = {}
    if PROFILE_CACHE.exists():
        try: profile_cache = json.loads(PROFILE_CACHE.read_text())
        except: profile_cache = {}

    results = []
    for i, s in enumerate(suspicious, 1):
        if s['code'] in profile_cache:
            profile = profile_cache[s['code']]
        else:
            profile = fetch_naver_profile(s['code'])
            profile_cache[s['code']] = profile
            time.sleep(0.5)  # 네이버 부담 최소

        m = match_keywords(profile, s['segment'])
        result = {**s, **m, 'profile_excerpt': profile[:200]}
        results.append(result)

        emoji = {'VALID': '✅', 'PARTIAL': '🟡', 'INVALID': '❌',
                 'NO_PROFILE': '⚠', 'NO_KEYWORD_DICT': '❓'}.get(m['verdict'], '?')
        hits = ', '.join(m['main_hits'][:3])
        sub_hits = ', '.join(m['sub_hits'][:3])
        hits_str = f"main: [{hits}] sub: [{sub_hits}]" if (m['main_hits'] or m['sub_hits']) else 'no hit'
        print(f"  [{i:2d}/{len(suspicious)}] {emoji} {s['code']} {s['name'][:12]:12s} → "
              f"{s['theme']} > {s['segment']:20s} | {m['verdict']:8s} | {hits_str[:80]}")

    # 캐시 저장
    PROFILE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_CACHE.write_text(json.dumps(profile_cache, ensure_ascii=False, indent=2),
                             encoding='utf-8')

    RESULT_PATH.write_text(
        json.dumps({'validated_at': datetime.now().isoformat(),
                    'method': 'NAVER_COINFO_KEYWORD_MATCH',
                    'count': len(results),
                    'results': results}, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )
    print(f"\n📁 결과: {RESULT_PATH}")

    by_v = {}
    for r in results:
        by_v.setdefault(r['verdict'], []).append(r)
    print(f"\n📊 판정:")
    for v, items in by_v.items():
        print(f"  {v}: {len(items)}")

    invalid = by_v.get('INVALID', [])
    if invalid:
        print(f"\n❌ 제거 권장 (INVALID {len(invalid)}건):")
        for r in invalid:
            print(f"  {r['code']} {r['name']:14s} → {r['theme']} > {r['segment_name']}")
            print(f"           sector: {r['sector']}")
            print(f"           profile: {r['profile_excerpt'][:120]}")

    no_profile = by_v.get('NO_PROFILE', [])
    if no_profile:
        print(f"\n⚠ 프로필 없음 ({len(no_profile)}건) — 수동 검토 필요:")
        for r in no_profile:
            print(f"  {r['code']} {r['name']} → {r['theme']} > {r['segment_name']}")

    return results


if __name__ == '__main__':
    main()
