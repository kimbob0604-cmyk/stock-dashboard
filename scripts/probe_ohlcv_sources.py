#!/usr/bin/env python3
"""
일봉(ohlcv)을 어디서 받을지 러너에서 실제로 재 본다. 일회성 점검이다.

왜 필요한가. `ohlcv` 테이블이 비면 신고가·52주 밴드·상관관계 등 server.py 의
읽기 15곳이 통째로 멎는다. 지금 그 상태다(Render 무료플랜은 영속 디스크가 없어
재시작마다 db/dashboard.db 가 사라지는데, ohlcv 를 채우는 자동 경로가 없다).

자동 수집을 붙이기 전에 **어느 소스가 데이터센터 IP에서 실제로 되는지** 먼저
확인해야 한다. 짐작으로 고르면 Render 에서 또 조용히 실패한다.

  후보 A  pykrx — requirements.txt 에 있고 /api/chart/<code> 가 이미 쓴다.
          다만 KRX 를 긁는 방식이라 데이터센터 IP 차단에 걸릴 수 있다.
  후보 B  api.finance.naver.com/siseJson.naver — 네이버 일봉 JSON.

러너(ubuntu-latest)는 Render 와 같은 데이터센터 IP 대역이라 여기서의 성패가
Render 에서의 성패에 가장 가까운 대리 지표다. **아무것도 고치지 않는다 —
받아서 되는지 안 되는지와 걸린 시간만 찍는다.**
"""
import json
import time
import urllib.request
from datetime import datetime, timedelta

CODES = ['005930', '000660', '055550']       # 삼성전자·SK하이닉스·신한지주
UA = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.naver.com/'}
SISE = ('https://api.finance.naver.com/siseJson.naver'
        '?symbol={code}&requestType=1&startTime={start}&endTime={end}&timeframe=day')


def hr(t):
    print()
    print('─' * 68)
    print(t)
    print('─' * 68)


# ── 후보 A: pykrx ────────────────────────────────────────────────────────
def probe_pykrx():
    hr('후보 A — pykrx (KRX 스크랩). /api/chart/<code> 가 이미 쓰는 경로')
    try:
        from pykrx import stock
    except ImportError as e:
        print(f'  pykrx 미설치: {e}')
        return False
    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=400)).strftime('%Y%m%d')
    ok = 0
    for code in CODES:
        t0 = time.time()
        try:
            df = stock.get_market_ohlcv(start, end, code)
            dt = time.time() - t0
            if df is None or df.empty:
                print(f'  {code}: 빈 응답 ({dt:.1f}s) — 차단이면 대개 이 모양이다')
                continue
            print(f'  {code}: {len(df)}행 ({dt:.1f}s) '
                  f'{df.index[0].date()} ~ {df.index[-1].date()} '
                  f'종가 {int(df["종가"].iloc[-1]):,}')
            ok += 1
        except Exception as e:                              # noqa: BLE001
            print(f'  {code}: 실패 ({time.time() - t0:.1f}s) {type(e).__name__}: {str(e)[:120]}')
    print(f'  → {ok}/{len(CODES)} 성공')
    return ok == len(CODES)


# ── 후보 B: 네이버 일봉 JSON ─────────────────────────────────────────────
def _parse_sise(raw: str):
    """siseJson 응답은 작은따옴표 파이썬 리터럴이라 json 으로 안 읽힌다."""
    import ast
    rows = ast.literal_eval(raw.strip())
    return rows


def probe_naver():
    hr('후보 B — api.finance.naver.com/siseJson.naver (네이버 일봉 JSON)')
    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=400)).strftime('%Y%m%d')
    ok = 0
    for code in CODES:
        t0 = time.time()
        try:
            req = urllib.request.Request(
                SISE.format(code=code, start=start, end=end), headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read().decode('utf-8')
            rows = _parse_sise(raw)
            dt = time.time() - t0
            if len(rows) < 2:
                print(f'  {code}: 빈 응답 ({dt:.1f}s) raw={raw[:100]!r}')
                continue
            head, body = rows[0], rows[1:]
            print(f'  {code}: {len(body)}행 ({dt:.1f}s) {body[0][0]} ~ {body[-1][0]}')
            print(f'      헤더: {head}')
            print(f'      마지막: {body[-1]}')
            ok += 1
        except Exception as e:                              # noqa: BLE001
            print(f'  {code}: 실패 ({time.time() - t0:.1f}s) {type(e).__name__}: {str(e)[:120]}')
    print(f'  → {ok}/{len(CODES)} 성공')
    return ok == len(CODES)


# ── 몇 종목까지 감당되나 (부팅 때 돌릴 수 있는 규모인지) ──────────────────
def probe_throughput(n=20):
    hr(f'처리량 — 네이버 일봉을 {n}종목 연속으로 받아 본다 (252거래일 구간)')
    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=400)).strftime('%Y%m%d')
    try:
        req = urllib.request.Request(
            'https://m.stock.naver.com/api/stocks/marketValue/KOSPI'
            f'?page=1&pageSize={n}', headers=UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            js = json.loads(r.read().decode('utf-8'))
        codes = [s.get('itemCode') for s in (js.get('stocks') or [])][:n]
    except Exception as e:                                  # noqa: BLE001
        print(f'  종목 목록 실패: {type(e).__name__}: {e}')
        return
    if not codes:
        print('  종목 목록이 비었다')
        return
    t0 = time.time()
    ok = rows_total = 0
    for code in codes:
        try:
            req = urllib.request.Request(
                SISE.format(code=code, start=start, end=end), headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                rows = _parse_sise(r.read().decode('utf-8'))
            if len(rows) > 1:
                ok += 1
                rows_total += len(rows) - 1
        except Exception:                                   # noqa: BLE001
            pass
        time.sleep(0.15)
    dt = time.time() - t0
    print(f'  {ok}/{len(codes)}종목 · {rows_total:,}행 · {dt:.1f}초 '
          f'(종목당 {dt / max(len(codes), 1):.2f}초)')
    for scale in (200, 500, 1000):
        print(f'  → {scale}종목이면 약 {dt / max(len(codes), 1) * scale / 60:.1f}분')


if __name__ == '__main__':
    print(f'실행 시각 {datetime.now():%Y-%m-%d %H:%M:%S} (러너 UTC 기준)')
    a = probe_pykrx()
    b = probe_naver()
    probe_throughput()
    hr('결론')
    print(f'  pykrx      : {"된다" if a else "안 된다(또는 일부만)"}')
    print(f'  네이버 일봉 : {"된다" if b else "안 된다(또는 일부만)"}')
    print('  ※ 러너는 데이터센터 IP다. Render 와 같은 조건이라는 보장은 없지만')
    print('     개발 환경(프록시 차단)보다는 Render 에 훨씬 가깝다.')
