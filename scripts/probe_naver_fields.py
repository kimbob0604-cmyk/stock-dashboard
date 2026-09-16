#!/usr/bin/env python3
"""
네이버 응답에 시가총액 필드가 실제로 있는지 확인한다. 일회성 점검이다.

왜 필요한가. `_refresh_prices_from_naver` 는 폴링 응답의 `marketValueFullRaw`
로 시총을 갱신하는데, 저장소 안에 그 응답 샘플이 없어 **그 이름이 지금도 맞는지
아무도 모른다.** 이름이 틀렸다면 시총은 한 번도 갱신된 적이 없고 계속
`data/naver_universe_seed.json`(2026-06-02) 값이었다는 뜻이 된다.

개발 환경에서는 네이버가 조직 프록시에 막혀 있어 이 점검을 못 돌린다.
러너에서 돌린다. **아무것도 고치지 않는다 — 받아서 필드 이름만 찍는다.**
"""
import json
import sys
import urllib.request

CODES = ['005930', '000660', '055550', '105560']     # 삼성전자·SK하이닉스·신한지주·KB금융
POLL = 'https://polling.finance.naver.com/api/realtime/domestic/stock/{codes}'
LIST = 'https://m.stock.naver.com/api/stocks/marketValue/{market}?page=1&pageSize=5'
UA = {'User-Agent': 'Mozilla/5.0'}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode('utf-8'))


def probe_polling():
    print('── polling.finance.naver.com (지금 시총을 받는 곳)')
    try:
        data = get(POLL.format(codes=','.join(CODES)))
    except Exception as e:                                  # noqa: BLE001
        print(f'  실패: {type(e).__name__}: {e}')
        return
    rows = data.get('datas') or []
    print(f'  응답 {len(rows)}종목')
    if not rows:
        print(f'  최상위 키: {sorted(data)}')
        return
    keys = sorted(rows[0])
    print(f'  키 {len(keys)}개: {keys}')
    cap_keys = [k for k in keys if 'market' in k.lower() or 'value' in k.lower()]
    print(f'  시총 후보 키: {cap_keys or "없음"}')
    for r in rows:
        vals = {k: r.get(k) for k in cap_keys}
        print(f'  {r.get("itemCode")} {r.get("stockName")}: '
              f'close={r.get("closePrice")} · {vals}')
    print(f'  >>> marketValueFullRaw 존재: '
          f'{"예" if "marketValueFullRaw" in keys else "**아니오**"}')


def probe_list():
    print('\n── m.stock.naver.com 시가총액 목록 (ETF-Traker 가 쓰는 경로)')
    for market in ('KOSPI', 'KOSDAQ'):
        try:
            data = get(LIST.format(market=market))
        except Exception as e:                              # noqa: BLE001
            print(f'  {market} 실패: {type(e).__name__}: {e}')
            continue
        rows = data if isinstance(data, list) else (data.get('stocks') or [])
        print(f'  {market} {len(rows)}종목')
        if rows:
            print(f'  키: {sorted(rows[0])}')
            for r in rows[:3]:
                print(f'    {r.get("itemCode") or r.get("cd")} '
                      f'{r.get("stockName") or r.get("nm")}: '
                      f'marketValue={r.get("marketValue")}')


if __name__ == '__main__':
    probe_polling()
    probe_list()
    sys.exit(0)
