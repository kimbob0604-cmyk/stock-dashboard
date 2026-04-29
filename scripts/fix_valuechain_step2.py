"""
valuechain_map.json 2차 보정 — 명백한 매핑 오류 3건 제거

- 095660 네오위즈 (게임엔터테인먼트) → AI_SEMI > 후공정장비 ❌
- 123010 알엔티엑스 (핸드셋)        → AI_SEMI > 전공정장비 ❌
- 288330 파라택시스코리아 (제약)     → AI_SEMI > 검사/세정 ❌ (이미 4-2-A에서 매출 0 발견)

이전 fix_valuechain_map.py와 동일한 패턴.
"""

import json, shutil
from pathlib import Path
from datetime import datetime

VC_PATH = Path(__file__).parent.parent / 'data' / 'valuechain_map.json'

TO_REMOVE = {
    '095660': '네오위즈 — 게임 회사, 반도체 후공정 무관',
    '123010': '알엔티엑스 — 핸드셋, 반도체 전공정 무관',
    '288330': '파라택시스코리아 — 제약, 반도체 무관 + 매출 0',
}


def fix():
    if not VC_PATH.exists():
        raise FileNotFoundError(VC_PATH)
    vc = json.loads(VC_PATH.read_text(encoding='utf-8'))

    backup = VC_PATH.with_suffix(f'.json.bak.{datetime.now().strftime("%Y%m%d_%H%M%S")}')
    shutil.copy(VC_PATH, backup)
    print(f"📦 백업: {backup}")

    removed = []
    for tid, theme in vc.get('themes', {}).items():
        for lid, layer in theme.get('layers', {}).items():
            for sid, seg in layer.get('segments', {}).items():
                kr = seg.get('stocks_kr', []) or []
                if not kr: continue
                new = []
                for code in kr:
                    if code in TO_REMOVE:
                        removed.append((code, tid, lid, sid, seg.get('name_kr')))
                    else:
                        new.append(code)
                seg['stocks_kr'] = new

    VC_PATH.write_text(json.dumps(vc, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f"\n✅ 제거 완료: {len(removed)}건")
    for code, tid, lid, sid, sname in removed:
        print(f"  {code}  →  {tid} > {lid} > {sid} ({sname})  — {TO_REMOVE[code]}")

    # 통계
    unique = set()
    for theme in vc['themes'].values():
        for layer in theme.get('layers', {}).values():
            for seg in layer.get('segments', {}).values():
                unique.update(seg.get('stocks_kr', []) or [])
    print(f"\n📊 정리 후 valuechain KR 종목 (중복 제거): {len(unique)}")


if __name__ == '__main__':
    fix()
