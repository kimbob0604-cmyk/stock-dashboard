"""
valuechain_map.json 3차 보정 (외부 검증 옵션 i 결과 반영)

A. 제거 5건 (잘못된 매핑):
  - 104830 원익머트리얼즈 from materials_chemicals
  - 104830 원익머트리얼즈 from inspection_cleaning
  - 011070 LG이노텍 from BATTERY_ESS > bms (FC-BGA는 유지)
  - 086960 MDS테크 from DEFENSE > optics_electro
  - 005070 코스모신소재 from AI_SEMI > materials_chemicals
    (BATTERY_ESS > cathode에 이미 있어 이동 불필요)

C. 자회사 메타데이터 5건 추가:
  - 017670 SKT @ AI_SEMI > npu_asic (사피온/리벨리온 26%)
  - 035420 NAVER @ AI_SEMI > cloud_idc (NAVER Cloud)
  - 030200 KT @ AI_SEMI > cloud_idc (KT Cloud)
  - 357780 솔브레인 @ BATTERY_ESS > electrolyte_separator (사이드)
  - 096770 SK이노베이션 @ BATTERY_ESS > ncm_cell (SK On)
"""

import json
import shutil
from pathlib import Path
from datetime import datetime

VC_PATH = Path(__file__).parent.parent / 'data' / 'valuechain_map.json'

# A. 제거: (stock_code, theme_id, layer_id, segment_id, 사유)
TO_REMOVE = [
    ('104830', 'AI_SEMI', 'L2_soboojang', 'materials_chemicals',
     '원익머트리얼즈 — 메인은 특수가스, 소재 매핑은 중복'),
    ('104830', 'AI_SEMI', 'L2_soboojang', 'inspection_cleaning',
     '원익머트리얼즈 — 검사/세정 사업 무관'),
    ('011070', 'BATTERY_ESS', 'L3_pack_bms', 'bms',
     'LG이노텍 — BMS 사업 무관 (광학/패키지/모빌리티). FC-BGA는 유지'),
    ('086960', 'DEFENSE', 'L2_subsystems', 'optics_electro',
     'MDS테크 — 임베디드SW 회사, 광학/EO 무관'),
    ('005070', 'AI_SEMI', 'L2_soboojang', 'materials_chemicals',
     '코스모신소재 — 양극활물질이 메인. BATTERY_ESS > cathode에 이미 매핑됨'),
]

# C. 자회사 메타데이터
SUBSIDIARY_META = {
    '017670': {  # SKT @ AI_SEMI > L1_silicon > npu_asic
        'theme': 'AI_SEMI', 'layer': 'L1_silicon', 'segment': 'npu_asic',
        'exposure_type': 'subsidiary',
        'exposure_via': '리벨리온 지분 + 사피온 매각',
        'primary_business': '통신/포털',
        'weight': 0.3,
    },
    '035420': {  # NAVER
        'theme': 'AI_SEMI', 'layer': 'L4_server_idc', 'segment': 'cloud_idc',
        'exposure_type': 'subsidiary',
        'exposure_via': 'NAVER Cloud',
        'primary_business': '검색/광고/커머스',
        'weight': 0.4,
    },
    '030200': {  # KT
        'theme': 'AI_SEMI', 'layer': 'L4_server_idc', 'segment': 'cloud_idc',
        'exposure_type': 'subsidiary',
        'exposure_via': 'KT Cloud',
        'primary_business': '통신/IPTV',
        'weight': 0.3,
    },
    '357780': {  # 솔브레인
        'theme': 'BATTERY_ESS', 'layer': 'L1_minerals', 'segment': 'electrolyte_separator',
        'exposure_type': 'side_business',
        'exposure_via': '2차전지 화학소재 (메인은 반도체)',
        'primary_business': '반도체 화학',
        'weight': 0.4,
    },
    '096770': {  # SK이노베이션
        'theme': 'BATTERY_ESS', 'layer': 'L2_cell', 'segment': 'ncm_cell',
        'exposure_type': 'subsidiary',
        'exposure_via': 'SK On',
        'primary_business': '정유/화학/LNG',
        'weight': 0.6,
    },
}


def fix():
    if not VC_PATH.exists():
        raise FileNotFoundError(VC_PATH)
    vc = json.loads(VC_PATH.read_text(encoding='utf-8'))

    backup = VC_PATH.with_suffix(f'.json.bak.v3.{datetime.now().strftime("%Y%m%d_%H%M%S")}')
    shutil.copy(VC_PATH, backup)
    print(f"📦 백업: {backup}")

    # === A. 제거 ===
    print("\n[A] 제거 작업:")
    removed = []
    for code, tid, lid, sid, reason in TO_REMOVE:
        try:
            seg = vc['themes'][tid]['layers'][lid]['segments'][sid]
            if code in seg.get('stocks_kr', []):
                seg['stocks_kr'] = [c for c in seg['stocks_kr'] if c != code]
                # stocks_metadata 있으면 같이 제거
                if seg.get('stocks_metadata', {}).get(code):
                    seg['stocks_metadata'].pop(code, None)
                removed.append((code, tid, lid, sid, reason))
                print(f"  ✅ {code} from {tid} > {sid}  ({reason[:40]})")
            else:
                print(f"  ⏭ {code} 이미 {sid}에 없음")
        except KeyError as e:
            print(f"  ❌ {code} 위치 못 찾음: {tid}>{lid}>{sid} ({e})")

    # === C. 자회사 메타데이터 ===
    print("\n[C] 자회사 메타데이터 추가:")
    meta_added = []
    for code, m in SUBSIDIARY_META.items():
        try:
            seg = vc['themes'][m['theme']]['layers'][m['layer']]['segments'][m['segment']]
            if code not in seg.get('stocks_kr', []):
                print(f"  ⚠ {code} 매핑 없음 ({m['theme']}>{m['segment']})")
                continue
            if 'stocks_metadata' not in seg:
                seg['stocks_metadata'] = {}
            seg['stocks_metadata'][code] = {
                'exposure_type':     m['exposure_type'],
                'exposure_via':      m['exposure_via'],
                'primary_business':  m['primary_business'],
                'weight':            m['weight'],
            }
            meta_added.append((code, m['theme'], m['segment']))
            print(f"  ✅ {code} @ {m['theme']} > {m['segment']}  "
                  f"({m['exposure_via']}, weight={m['weight']})")
        except KeyError as e:
            print(f"  ❌ {code} segment 없음: {e}")

    # === 저장 ===
    VC_PATH.write_text(json.dumps(vc, ensure_ascii=False, indent=2), encoding='utf-8')

    # === 통계 ===
    unique = set()
    for theme in vc['themes'].values():
        for layer in theme.get('layers', {}).values():
            for seg in layer.get('segments', {}).values():
                unique.update(seg.get('stocks_kr', []) or [])

    print(f"\n📊 결과:")
    print(f"  제거:           {len(removed)}건")
    print(f"  메타데이터 추가: {len(meta_added)}건")
    print(f"  KR 종목 (중복 제거): {len(unique)}")


if __name__ == '__main__':
    fix()
