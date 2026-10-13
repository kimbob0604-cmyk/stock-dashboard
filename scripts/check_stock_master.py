#!/usr/bin/env python3
"""종목 마스터(data/master/stocks.csv)가 온전한가 — 네트워크 없이 본다.

master-check.yml 이 마스터를 건드리는 push·PR 마다 돌리고, master-refresh.yml
이 PR 을 열기 전에 돌린다. 개발 컨테이너에서도 그대로 돈다.

보는 것
  1. 스키마 — 열 이름·순서, 코드 형식(6자리 영숫자)·중복, 시장·유형·Tier 값
  2. 우선주 — 여섯째 자리가 '0' 이 아니고, 본주가 마스터에 보통주로 있다
  3. 빠져야 할 것이 없다 — 스팩·펀드 클래스. ETF 이름 패턴(server.py
     ETF_PATTERNS)에 걸리는 이름은 경고로만 보인다 — 원천(stockEndType)이
     이미 걸렀으니 걸리면 패턴 쪽 오탐일 가능성이 크다
  4. 업종 — 비어 있는 종목 1% 이하, 대분류가 industry_groups.json 과 맞는다
  5. 규모 — 2,000~4,000종목. 수집이 반쯤 된 파일을 잡는다
  6. meta.json 의 집계가 파일과 맞는다
  7. 네이버 테마 원본(있으면) — 테마 번호·쌍 중복 없음, 종목이 전부 마스터 안,
     테마별 종목 수가 in_master 와 맞는다
"""
import json
import os
import re
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import stock_master as sm                                     # noqa: E402

fails, warns = [], []


def check(ok, msg):
    print(('  PASS ' if ok else '  FAIL ') + msg)
    if not ok:
        fails.append(msg)


def warn(msg):
    print('  WARN ' + msg)
    warns.append(msg)


def sample(items, n=8):
    items = list(items)
    return ', '.join(map(str, items[:n])) + (f' …외 {len(items) - n}' if len(items) > n else '')


print('── 스키마')
with open(sm.STOCKS_CSV, encoding='utf-8') as f:
    header = f.readline().strip().split(',')
check(header == sm.COLUMNS, f'열 {header}')
rows = sm.read_rows()
by_code = {r['code']: r for r in rows}
check(len(by_code) == len(rows),
      f'코드 중복 없음 ({len(rows)}행, 중복 {sample(c for c, n in Counter(r["code"] for r in rows).items() if n > 1)})')
bad = [r['code'] for r in rows if not sm.CODE_RE.match(r['code'])]
check(not bad, f'코드 형식 6자리 영숫자 (어긋남 {sample(bad)})')
check(all(r['name'].strip() for r in rows), '이름이 비어 있지 않다')
for col, allowed in (('market', sm.MARKETS), ('type', sm.TYPES)):
    bad = [f"{r['code']}={r[col]}" for r in rows if r[col] not in allowed]
    check(not bad, f'{col} ∈ {allowed} (어긋남 {sample(bad)})')
bad = [r['code'] for r in rows if r['type'] == 'common' and r['tier'] not in sm.TIERS]
check(not bad, f'보통주 Tier ∈ {sm.TIERS} (어긋남 {sample(bad)})')
bad = [r['code'] for r in rows if r['type'] != 'common' and r['tier']]
check(not bad, f'보통주가 아니면 Tier 없음 (어긋남 {sample(bad)})')

print('── 우선주')
pref = [r for r in rows if r['type'] == 'preferred']
bad = [r['code'] for r in rows if (r['type'] == 'preferred') != (r['code'][5] != '0')]
check(not bad, f'우선주 ⇔ 여섯째 자리 ≠ 0 (어긋남 {sample(bad)})')
bad = [r['code'] for r in rows if r['parent_code'] != sm.parent_of(r['code'])]
check(not bad, f'parent_code = 앞 다섯 자리 + 0 (어긋남 {sample(bad)})')
orphan = [f"{r['code']} {r['name']}" for r in pref
          if (by_code.get(r['parent_code']) or {}).get('type') != 'common']
check(not orphan, f'우선주 {len(pref)}개 모두 본주가 보통주로 있다 (없음 {sample(orphan)})')

print('── 빠져야 할 것')
spac = [f"{r['code']} {r['name']}" for r in rows if '스팩' in r['name']]
check(not spac, f'스팩 없음 ({sample(spac)})')
fund = [f"{r['code']} {r['name']}" for r in rows if r['name'].endswith('X클래스') or r['industry'] == '기타']
check(not fund, f'펀드 클래스 없음 ({sample(fund)})')
src = open(os.path.join(ROOT, 'server.py'), encoding='utf-8').read()
m = re.search(r'(?m)^ETF_PATTERNS = \((.*?)\n\)', src, re.S)
if m:
    pats = re.findall(r"'([^']*)'", re.sub(r'#.*', '', m.group(1)))
    # server.py _is_etf_name 과 같은 판정 — SQLite LIKE '%p%', 뒤에 공백을 붙이지 않는다
    hits = [f"{r['code']} {r['name']}" for r in rows
            if any(p.lower() in r['name'].lower() for p in pats)]
    if hits:
        warn(f'ETF 이름 패턴에 걸리는 종목 {len(hits)}개 — 패턴 오탐인지 본다: {sample(hits)}')
    else:
        print(f'  PASS ETF 이름 패턴({len(pats)}개)에 걸리는 종목 없음')
else:
    warn('server.py 에서 ETF_PATTERNS 를 못 찾았다 — 교차 검사를 건너뛴다')

print('── 업종')
ind2grp, grp_names = sm.load_industry_groups()
no_ind = [f"{r['code']} {r['name']}" for r in rows if not r['industry']]
check(len(no_ind) <= len(rows) * 0.01, f'업종 없는 종목 {len(no_ind)}개 ≤ 1% ({sample(no_ind)})')
if no_ind:
    warn(f'업종 없음 {len(no_ind)}개: {sample(no_ind)}')
bad = [r['code'] for r in rows
       if r['industry_group'] != ind2grp.get(r['industry'], sm.UNCLASSIFIED_GROUP)]
check(not bad, f'대분류가 industry_groups.json 과 맞는다 (어긋남 {sample(bad)})')
unmapped = sorted({r['industry'] for r in rows
                   if r['industry'] and r['industry'] not in ind2grp})
if unmapped:
    warn(f'대분류에 없는 업종 {unmapped} — data/master/industry_groups.json 에 넣는다')
dup_ind = [i for i, n in Counter(i for g in json.load(open(sm.INDUSTRY_GROUPS_JSON, encoding='utf-8'))['groups']
                                 for i in g['industries']).items() if n > 1]
check(not dup_ind, f'한 업종이 대분류 두 곳에 있지 않다 ({sample(dup_ind)})')

print('── 규모·집계')
check(2000 <= len(rows) <= 4000, f'종목 수 {len(rows)} (2,000~4,000)')
for mk in sm.MARKETS:
    n = sum(1 for r in rows if r['market'] == mk)
    check(n >= 500, f'{mk} {n}종목 (≥ 500)')
if os.path.exists(sm.META_JSON):
    meta = json.load(open(sm.META_JSON, encoding='utf-8'))
    check(meta.get('total') == len(rows), f"meta.total {meta.get('total')} = {len(rows)}")
    check(meta.get('by_type') == dict(Counter(r['type'] for r in rows)), 'meta.by_type 일치')
    check(meta.get('by_tier') == dict(sorted(Counter(r['tier'] for r in rows if r['tier']).items())),
          'meta.by_tier 일치')
else:
    check(False, 'meta.json 이 있다')

themes_json = os.path.join(sm.MASTER_DIR, 'naver_themes.json')
members_csv = os.path.join(sm.MASTER_DIR, 'naver_theme_members.csv')
if os.path.exists(themes_json) or os.path.exists(members_csv):
    print('── 네이버 테마')
    import csv
    themes = json.load(open(themes_json, encoding='utf-8'))['themes']
    with open(members_csv, encoding='utf-8', newline='') as f:
        members = list(csv.DictReader(f))
    by_no = {t['no']: t for t in themes}
    check(len(by_no) == len(themes), f'테마 번호 중복 없음 ({len(themes)}개)')
    check(len(themes) >= 200, f'테마 {len(themes)}개 (≥ 200)')
    pairs = Counter((m['theme_no'], m['code']) for m in members)
    check(all(n == 1 for n in pairs.values()), f'테마×종목 중복 없음 ({len(members)}건)')
    bad = sorted({m['theme_no'] for m in members if int(m['theme_no']) not in by_no})
    check(not bad, f'모든 테마 번호가 목록에 있다 (없음 {sample(bad)})')
    bad = sorted({m['code'] for m in members if m['code'] not in by_code})
    check(not bad, f'모든 종목이 마스터 안 (밖 {sample(bad)})')
    per = Counter(int(m['theme_no']) for m in members)
    bad = [t['name'] for t in themes if per.get(t['no'], 0) != t['in_master']]
    check(not bad, f'테마별 종목 수 = in_master (어긋남 {sample(bad)})')

print(f"\n{len(rows)}종목 · 유형 {dict(Counter(r['type'] for r in rows))} · "
      f"Tier {dict(sorted(Counter(r['tier'] for r in rows if r['tier']).items()))}")
print(f'FAIL {len(fails)} · WARN {len(warns)}')
sys.exit(1 if fails else 0)
