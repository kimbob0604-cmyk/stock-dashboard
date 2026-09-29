#!/usr/bin/env python3
"""
네이버 금융 PC HTML 이 Next.js SPA 로 바뀌어 table 스크레이핑이 전부 죽었다.
대체할 JSON 엔드포인트를 찾는다. 후보를 쭉 쳐 보고 쓸 수 있는 것만 고른다.

읽기만 한다.
"""
import json
import urllib.error
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
      "Referer": "https://m.stock.naver.com/"}

CODE = "005930"

CANDIDATES = [
    # ── 수급 (투자자별 매매동향) ───────────────────────────────
    ("수급", f"https://m.stock.naver.com/api/stock/{CODE}/trend"),
    ("수급", f"https://m.stock.naver.com/api/stock/{CODE}/investor"),
    ("수급", f"https://api.stock.naver.com/stock/{CODE}/trend"),
    ("수급", f"https://api.stock.naver.com/stock/{CODE}/investor"),
    ("수급", f"https://m.stock.naver.com/api/stock/{CODE}/frgn"),
    ("수급", f"https://m.stock.naver.com/api/stock/{CODE}/trend?pageSize=20&page=1"),
    # ── 일별시세 (종가 — 수급 금액 환산에 필요) ────────────────
    ("일별시세", f"https://m.stock.naver.com/api/stock/{CODE}/price?pageSize=20&page=1"),
    ("일별시세", f"https://api.stock.naver.com/chart/domestic/item/{CODE}/day"),
    # ── 업종 ──────────────────────────────────────────────────
    ("업종목록", "https://m.stock.naver.com/api/stocks/industry"),
    ("업종목록", "https://api.stock.naver.com/industry/home"),
    ("업종목록", "https://m.stock.naver.com/api/industry"),
    ("업종목록", "https://m.stock.naver.com/api/stocks/industry?page=1&pageSize=100"),
    ("업종상세", "https://m.stock.naver.com/api/stocks/industry/261"),
    ("업종상세", "https://m.stock.naver.com/api/stocks/industry/261?page=1&pageSize=20"),
]


def shape(o, depth=0, maxd=2):
    """응답 구조를 짧게 요약."""
    pad = "  " * depth
    if isinstance(o, dict):
        if depth >= maxd:
            return f"{{{', '.join(list(o)[:8])}}}"
        out = []
        for k in list(o)[:12]:
            out.append(f"{pad}  {k}: {shape(o[k], depth+1, maxd)}")
        return "{\n" + "\n".join(out) + f"\n{pad}}}"
    if isinstance(o, list):
        if not o:
            return "[] (빈 배열)"
        return f"[{len(o)}개] 첫 원소 → {shape(o[0], depth+1, maxd)}"
    s = repr(o)
    return s[:60] + ("…" if len(s) > 60 else "")


for label, url in CANDIDATES:
    print("=" * 66)
    print(f"[{label}] {url}")
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read().decode("utf-8", errors="replace")
            print(f"  HTTP {r.status}  bytes={len(body)}")
            if not body.strip():
                print("  (빈 응답)")
                continue
            try:
                d = json.loads(body)
            except json.JSONDecodeError:
                print(f"  JSON 아님 — 앞 160자: {body[:160]!r}")
                continue
            print("  구조:")
            print("   " + shape(d).replace("\n", "\n   "))
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code} {e.reason}")
        try:
            print(f"  본문: {e.read().decode('utf-8', 'replace')[:160]!r}")
        except Exception:
            pass
    except Exception as e:
        print(f"  실패: {type(e).__name__}: {e}")

print("\n끝")
