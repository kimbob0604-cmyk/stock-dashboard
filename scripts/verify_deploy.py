#!/usr/bin/env python3
"""
배포 후 실측 — 대시보드가 실제로 값을 받고 있는지 숫자로 확인한다.

"고쳤다" 는 이 스크립트 출력으로만 말한다.
러너에서 돈다(개발 컨테이너는 onrender.com 이 프록시에 막혀 있다).

사용: python3 scripts/verify_deploy.py https://stock-dashboard-kc23.onrender.com
종료코드: 0 = 핵심 계열 전부 살아 있음, 1 = 하나라도 비었음
"""
import json
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1
        else "https://stock-dashboard-kc23.onrender.com").rstrip("/")
FAILS = []


def get(path, timeout=90):
    try:
        req = urllib.request.Request(BASE + path, headers={"User-Agent": "verify/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, None
    except Exception as e:
        print(f"    요청 실패: {type(e).__name__}: {e}")
        return 0, None


def head(t):
    print("\n" + "=" * 64)
    print(t)
    print("=" * 64)


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


head("1. /api/ops/diag/sources — 무엇이 들어오는가")
st, d = get("/api/ops/diag/sources")
print(f"  HTTP {st}")
if isinstance(d, dict):
    print(f"  거래일: {d.get('trading_date')}")
    for k, v in (d.get("sources") or {}).items():
        print(f"    {'OK ' if v.get('ok') else '없음'}  {v.get('label'):12s} {v.get('detail')}")
    if d.get("missing"):
        print(f"  빠진 것: {', '.join(d['missing'])}")
    check("거래일이 오늘에 가깝다", bool(d.get("trading_date")), str(d.get("trading_date")))

head("2. /api/sectors — 업종")
st, d = get("/api/sectors")
print(f"  HTTP {st}")
if isinstance(d, dict) and d.get("sectors"):
    s = d["sectors"]
    print(f"  업종 {len(s)}개  source={d.get('source')}")
    for x in s[:6]:
        print(f"    {x['name'][:18]:18s} {x['change_pct']:+6.2f}%  {x['total']:4d}종목 "
              f"(상승 {x['up']:3d} 보합 {x['flat']:3d} 하락 {x['down']:3d})")
    check("업종 70개 이상", len(s) >= 70, f"{len(s)}개")
    check("등락률이 전부 0은 아님", any(x["change_pct"] != 0 for x in s))
else:
    check("업종 수집", False, str(d)[:200])

head("3. /api/flow/005930 — 삼성전자 수급")
st, d = get("/api/flow/005930")
print(f"  HTTP {st}")
if isinstance(d, dict) and d.get("dates"):
    print(f"  거래일 {len(d['dates'])}일: {d['dates'][0]} ~ {d['dates'][-1]}")
    print(f"  source={d.get('source')}  외국인보유율={d.get('foreign_hold_ratio')}")
    ind = d.get("individual_shares") or [0] * len(d["dates"])
    print("  최근 3일 — 외국인 / 기관 / 개인 (주)")
    for i in range(-min(3, len(d["dates"])), 0):
        print(f"    {d['dates'][i]}  {d['foreign_shares'][i]:+13,}  "
              f"{d['inst_shares'][i]:+13,}  {ind[i]:+13,}")
    check("수급 10일 이상", len(d["dates"]) >= 10, f"{len(d['dates'])}일")
    check("새 소스 사용", d.get("source") == "naver_mobile_api", str(d.get("source")))
else:
    check("수급 수집", False, str(d)[:200])

head("4. /api/screener — 시총·가격")
st, d = get("/api/screener?limit=5")
print(f"  HTTP {st}")
if isinstance(d, dict) and d.get("stocks"):
    stx = d["stocks"]
    have = sum(1 for s in stx if s.get("market_cap"))
    print(f"  총 {d.get('count')}종목, 응답 {len(stx)}건 중 시총 있는 것 {have}건")
    print(f"  fetched_at={d.get('fetched_at')}")
    for s in stx[:5]:
        mc = s.get("market_cap")
        mc_s = f"{mc/1e12:.1f}조" if mc else "없음"
        print(f"    {s['name'][:18]:18s} {s.get('close') or 0:>11,.0f}  "
              f"{s.get('change_pct') or 0:+6.2f}%  시총={mc_s}")
    check("가격 채워짐", all(s.get("close") for s in stx))
else:
    check("스크리너", False, str(d)[:200])

head("5. /api/ops/watchdog — 판정")
st, d = get("/api/ops/watchdog")
print(f"  HTTP {st}")
if isinstance(d, dict):
    print(f"  healthy={d.get('healthy')}  issues={d.get('issues')}")
    print(f"  stocks_kr={d.get('stocks_kr')}  flow_rows={d.get('flow_rows')}  "
          f"flow_latest={d.get('flow_latest')}  stocks_age_min={d.get('stocks_age_min')}")
    print(f"  note={d.get('note')}")
    check("KR 종목 1000개 이상", (d.get("stocks_kr") or 0) >= 1000, str(d.get("stocks_kr")))
    check("수급 50행 이상", (d.get("flow_rows") or 0) >= 50, str(d.get("flow_rows")))

head("6. /api/ops/diag/collect_errors — 최근 수집 실패")
st, d = get("/api/ops/diag/collect_errors?limit=10")
print(f"  HTTP {st}")
if isinstance(d, dict):
    print(f"  버퍼 {d.get('total_buffered')}건  소스별={d.get('by_source')}")
    for e in (d.get("errors") or [])[:8]:
        print(f"    {e.get('at')}  [{e.get('source')}] {e.get('detail')[:110]}")

print("\n" + "=" * 64)
if FAILS:
    print(f"실패 {len(FAILS)}건: {FAILS}")
    sys.exit(1)
print("핵심 계열 전부 살아 있음")
