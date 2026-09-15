"""텔레그램 시황 본문 조립을 server.py 에서 뽑아 그대로 돌려 본다.

server.py 는 Flask 앱이라 import 하지 않는다. send_market_summary_telegram 의
조립 규칙(SKIP_TITLES · items/subsections · 빈 섹션 경고)을 같은 입력에 태워
'무엇이 메시지에 실리는가' 만 못 박는다.
"""
import re
import sys

SRC = open('/home/user/stock-dashboard/server.py', encoding='utf-8').read()

m = re.search(r'SKIP_TITLES = \{([^}]*)\}', SRC)
assert m, 'SKIP_TITLES 를 못 찾았다'
SKIP_TITLES = set(eval('{' + m.group(1) + '}'))


def render(sections):
    """send_market_summary_telegram 의 본문 조립과 같은 순서."""
    lines, empty_titles = [], []
    for sec in sections:
        if sec.get('title') in SKIP_TITLES:
            continue
        lines.append(sec['title'])
        items = sec.get('items') or []
        subs = sec.get('subsections') or []
        lines.extend(items)
        for sub in subs:
            lines.append('')
            lines.append(sub['subtitle'])
            lines.extend(sub.get('items', []))
        if not items and not subs:
            lines.append('  ⚠️ 데이터 없음 (점검 필요)')
            empty_titles.append(sec.get('title', '?'))
        lines.append('')
    if empty_titles:
        lines.append(f"⚠️ 빈 섹션: {', '.join(empty_titles)}")
    return '\n'.join(lines), empty_titles


SECTIONS = [
    {'title': '⚡ 특징주', 'subsections': [
        {'subtitle': '🔺 급등 (+5%↑) TOP 10', 'items': ['  A +30.0% — IT']}], 'error': None},
    {'title': '🏔 신고가',
     'items': ['  <i>종가 기준 · 오늘 종가 vs 2026-09-14까지 종가 · '
               '역사적=일봉 2021-09-16~</i>'],
     'subsections': [
        {'subtitle': '🏔 역사적 신고가 2종목', 'items': ['  삼성전자 +3.5% — 반도체']},
        {'subtitle': '📈 52주 신고가 5종목', 'items': ['  B +2.0% — 은행']},
        {'subtitle': '📊 60일 신고가 9종목', 'items': ['  C +1.0% — 화학', '  … 외 4종목']},
    ], 'error': None},
    # 지금 운영에서 비어 나오던 그 섹션
    {'title': '💰 수급 동향', 'subsections': [], 'error': None},
    {'title': '📋 주요 공시', 'items': ['  오늘 중요 공시 없음']},
    {'title': '🤖 AI 추천 요약', 'items': ['  핫 테마: 반도체']},
]

msg, empty = render(SECTIONS)
print(msg)
print('---')

ok = True


def want(cond, why):
    global ok
    if not cond:
        ok = False
        print('FAIL —', why)


want('💰 수급 동향' not in msg, '수급 동향이 메시지에 남아 있다')
want('데이터 없음 (점검 필요)' not in msg, "비어 있던 수급이 '점검 필요' 로 나간다")
want(empty == [], f'빈 섹션 경고가 떴다: {empty}')
want('🏔 신고가' in msg, '신고가 섹션이 없다')
for icon, label in (('🏔', '역사적'), ('📈', '52주'), ('📊', '60일')):
    want(f'{icon} {label} 신고가' in msg, f'{label} 줄이 없다')
want('일봉 2021-09-16~' in msg, "'역사적' 의 기준 구간 표기가 빠졌다")
want(msg.count('일봉 2021-09-16~') == 1, '기준 표기가 여러 줄에 반복된다')
want('종가 기준' in msg, '어느 기준인지 적지 않는다')
want('🤖 AI 추천 요약' not in msg and '📋 주요 공시' not in msg,
     '기존에 빼던 섹션이 다시 들어왔다')

print('통과' if ok else '실패')
sys.exit(0 if ok else 1)
