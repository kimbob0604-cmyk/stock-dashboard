"""
valuechain_map.json 1회용 보정 스크립트 (옵션 A)

제거 대상 (3종목):
- 036490: 네이버 응답 비정상 + DB 데이터 0 (상장폐지/변경 추정)
- 119860: 네이버 응답 비정상 + DB 데이터 0 (상장폐지/변경 추정)
- 371460: ETF (TIGER 차이나전기차)

원본은 .bak.{timestamp}로 백업 후 덮어쓰기.
"""

import json
import shutil
from pathlib import Path
from datetime import datetime

VC_PATH = Path(__file__).parent.parent / 'data' / 'valuechain_map.json'

TO_REMOVE = {
    '036490': '네이버 응답 비정상 + DB 데이터 0 (상장폐지 추정)',
    '119860': '네이버 응답 비정상 + DB 데이터 0 (상장폐지 추정)',
    '371460': 'ETF (TIGER 차이나전기차)',
}


def fix():
    if not VC_PATH.exists():
        raise FileNotFoundError(f'valuechain_map.json 없음: {VC_PATH}')

    vc = json.loads(VC_PATH.read_text(encoding='utf-8'))

    backup_path = VC_PATH.with_suffix(f'.json.bak.{datetime.now().strftime("%Y%m%d_%H%M%S")}')
    shutil.copy(VC_PATH, backup_path)
    print(f"📦 백업: {backup_path}")

    removed_count = 0
    removed_segments = []

    for theme_id, theme in vc.get('themes', {}).items():
        for layer_id, layer in theme.get('layers', {}).items():
            for seg_id, seg in layer.get('segments', {}).items():
                kr_list = seg.get('stocks_kr', []) or []
                if not kr_list:
                    continue
                new_kr = []
                for code in kr_list:
                    if code in TO_REMOVE:
                        removed_count += 1
                        removed_segments.append({
                            'code': code,
                            'theme': theme_id,
                            'layer': layer_id,
                            'segment': seg_id,
                            'segment_name': seg.get('name_kr'),
                            'reason': TO_REMOVE[code],
                        })
                    else:
                        new_kr.append(code)
                seg['stocks_kr'] = new_kr

    VC_PATH.write_text(
        json.dumps(vc, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )

    print(f"\n✅ 제거 완료: {removed_count}건\n")
    print("제거된 매핑:")
    for r in removed_segments:
        print(f"  {r['code']}  →  {r['theme']} > {r['layer']} > {r['segment']}")
        print(f"           ({r['segment_name']}) — {r['reason']}")

    total_kr = 0
    for theme in vc.get('themes', {}).values():
        for layer in theme.get('layers', {}).values():
            for seg in layer.get('segments', {}).values():
                total_kr += len(seg.get('stocks_kr', []) or [])
    print(f"\n📊 정리 후 valuechain KR 매핑 (중복 포함): {total_kr}개")


if __name__ == '__main__':
    fix()
