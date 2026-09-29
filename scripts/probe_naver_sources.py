#!/usr/bin/env python3
"""
서버가 실제로 쓰는 네이버 엔드포인트를 러너에서 그대로 쳐 보고,
파서가 기대하는 것이 아직 거기 있는지 확인한다.

읽기만 한다. 키·토큰을 쓰지 않는다.

  A. polling.finance.naver.com  — 가격 sync 가 쓰는 곳. marketValueFullRaw 유무.
  B. finance.naver.com/sise/sise_group.naver — 업종. table.type_1 유무.
  C. finance.naver.com/item/frgn.naver — 수급. table.type2 유무.
"""
import json
import sys
import urllib.request

UA = {"User-Agent": "Mozilla/5.0"}


def get(url, encoding=None, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return r.status, raw.decode(encoding or "utf-8", errors="replace")


def section(t):
    print("\n" + "=" * 62)
    print(t)
    print("=" * 62)


# ── A. 가격 폴링 ────────────────────────────────────────────────
section("A. polling.finance.naver.com (가격 sync 가 쓰는 곳)")
codes = "005930,000660,035420"
try:
    st, body = get(f"https://polling.finance.naver.com/api/realtime/domestic/stock/{codes}")
    print(f"HTTP {st}  bytes={len(body)}")
    d = json.loads(body)
    datas = d.get("datas") or []
    print(f"datas 개수: {len(datas)}")
    if datas:
        s = datas[0]
        print(f"\n[{s.get('itemCode')}] 응답 필드 전체 ({len(s)}개):")
        for k in sorted(s.keys()):
            v = s[k]
            v = (v[:40] + "…") if isinstance(v, str) and len(v) > 40 else v
            print(f"    {k} = {v!r}")
        print("\n파서가 읽는 필드:")
        for k in ("closePrice", "fluctuationsRatio", "accumulatedTradingVolume",
                  "marketValueFullRaw"):
            print(f"    {k:28s} {'있음' if k in s else '>>> 없음 <<<'}"
                  f"  {s.get(k)!r}")
        # 시총처럼 보이는 다른 이름이 있는지
        cand = [k for k in s if "market" in k.lower() or "cap" in k.lower()
                or "value" in k.lower()]
        print(f"\n  시총 후보 필드명: {cand}")
except Exception as exc:
    print(f"실패: {type(exc).__name__}: {exc}")

# ── B. 업종 ─────────────────────────────────────────────────────
section("B. finance.naver.com/sise/sise_group.naver (업종)")
try:
    st, body = get("https://finance.naver.com/sise/sise_group.naver?type=upjong",
                   encoding="euc-kr")
    print(f"HTTP {st}  bytes={len(body)}")
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(body, "html.parser")
        t1 = soup.select_one("table.type_1")
        print(f"table.type_1 : {'있음' if t1 else '>>> 없음 <<<'}")
        if not t1:
            tables = soup.find_all("table")
            print(f"  페이지의 table 개수: {len(tables)}")
            for i, t in enumerate(tables[:8]):
                print(f"    [{i}] class={t.get('class')} summary={t.get('summary')!r}")
            print("\n  본문 앞 500자:")
            print("  " + body[:500].replace("\n", "\n  "))
        else:
            rows = [tr for tr in t1.select("tr") if len(tr.find_all("td")) >= 6]
            print(f"  td>=6 인 행: {len(rows)}")
            if rows:
                tds = rows[0].find_all("td")
                print(f"  첫 행: {[td.get_text(strip=True)[:14] for td in tds[:7]]}")
    except ImportError:
        print("bs4 없음 — 원문만 확인")
        print(body[:400])
except Exception as exc:
    print(f"실패: {type(exc).__name__}: {exc}")

# ── C. 수급 ─────────────────────────────────────────────────────
section("C. finance.naver.com/item/frgn.naver (수급)")
try:
    st, body = get("https://finance.naver.com/item/frgn.naver?code=005930",
                   encoding="euc-kr")
    print(f"HTTP {st}  bytes={len(body)}")
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(body, "html.parser")
        t = soup.select_one('table.type2[summary*="외국인"]') or soup.select_one("table.type2")
        print(f"table.type2 : {'있음' if t else '>>> 없음 <<<'}")
        if t:
            rows = [tr for tr in t.select("tr") if len(tr.find_all("td")) >= 7]
            print(f"  td>=7 인 행: {len(rows)}")
            if rows:
                tds = rows[0].find_all("td")
                print(f"  첫 행: {[td.get_text(strip=True)[:12] for td in tds[:7]]}")
        else:
            tables = soup.find_all("table")
            print(f"  페이지의 table 개수: {len(tables)}")
            for i, tt in enumerate(tables[:8]):
                print(f"    [{i}] class={tt.get('class')} summary={tt.get('summary')!r}")
    except ImportError:
        print("bs4 없음")
except Exception as exc:
    print(f"실패: {type(exc).__name__}: {exc}")

print("\n끝")
