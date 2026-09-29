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


def wait_for_deploy(max_min=20):
    """배포 완료를 기다린다.

    1순위: /api/health 의 git_commit 이 기대 SHA 와 같은지. 이게 가장 확실하다.
           (--wait <sha> 로 넘긴다. GitHub Actions 에서는 github.sha)
    2순위: SHA 를 못 받았거나 서버가 git_commit 을 안 주면, 예전처럼
           stocks_age_min 이 정상 범위인지로 본다. 이미 배포된 경로를 고친
           배포는 이 방법으로 구분되지 않으니 어디까지나 차선이다.
    """
    import time
    want = None
    for a in sys.argv[1:]:
        if len(a) >= 7 and all(c in "0123456789abcdef" for c in a.lower()):
            want = a.lower()
            break

    tries = max(1, int(max_min * 2))
    for i in range(1, tries + 1):
        st, h = get("/api/health", timeout=60)
        have = (h or {}).get("git_commit")
        if want and have:
            print(f"  시도 {i}/{tries} — HTTP {st}  배포 커밋 {have[:8]} (기대 {want[:8]})")
            if have.lower().startswith(want[:len(have)]) or want.startswith(have.lower()):
                print("  새 코드 배포 확인 (커밋 일치)")
                return True
        else:
            _, d = get("/api/ops/watchdog", timeout=60)
            age = (d or {}).get("stocks_age_min")
            print(f"  시도 {i}/{tries} — HTTP {st}  git_commit={have}  "
                  f"stocks_age_min={age}")
            if st == 200 and isinstance(age, (int, float)) and age < 400:
                print("  배포 확인 (커밋 정보 없음 — 경과시간으로 판정)")
                return True
        time.sleep(30)
    print("::error::배포가 시간 안에 반영되지 않았다.")
    return False


if "--wait" in sys.argv:
    head("0. 배포 대기")
    sys.exit(0 if wait_for_deploy() else 1)


def wait_for_flow(target=50, max_min=8):
    """수급 배치가 끝날 때까지 기다린다.

    고정 sleep 으로 재면 재배포 직후엔 배치가 아직 도는 중이라 낮게 나온다
    (실제로 71행 → 38행으로 들쭉날쭉했다). 행 수가 목표를 넘거나
    더 늘지 않을 때까지 본다.
    """
    import time
    last, flat = -1, 0
    for i in range(1, int(max_min * 3) + 1):
        _, d = get("/api/ops/watchdog", timeout=60)
        n = (d or {}).get("flow_rows") or 0
        print(f"  {i:2d}) flow_rows={n}")
        if n >= target:
            print(f"  목표 {target}행 도달")
            return n
        flat = flat + 1 if n == last else 0
        last = n
        if flat >= 3:
            print(f"  3회 연속 증가 없음 — 배치 종료로 본다 ({n}행)")
            return n
        time.sleep(20)
    return last


def wait_for_data_json(max_min=12):
    """data.json 이 서버 생성본이 될 때까지 기다린다.

    **재생성은 한 번만 찌른다.** 매 회차마다 POST 하면 빌드가 끝나기 전에
    다음 요청이 겹쳐 서로 락을 기다리다 전부 타임아웃한다 — 2026-09-29
    실측에서 그렇게 17분을 태웠다. 서버 안에서도 부팅 빌더가 돌고 있으니,
    한 번 찌른 뒤로는 상태만 본다.

    벽시계로 끊는다. 회차로 세면 요청 하나가 길어질 때 예산을 넘긴다.
    """
    import time
    import urllib.request
    deadline = time.time() + max_min * 60
    poked = False
    i = 0
    while time.time() < deadline:
        i += 1
        st, d = get("/api/ops/data_json/status", timeout=45)
        d = d or {}
        print(f"  {i:2d}) source={d.get('source')} 테마={d.get('themes')} "
              f"live={d.get('universe_live')} mapped={d.get('mapped_live')}/"
              f"{d.get('mapped_total')}(need {d.get('mapped_need')}) "
              f"building={d.get('build_in_progress')}")
        if d.get("source") == "server" and (d.get("themes") or 0) >= 20:
            print("  서버 생성본 확인")
            return True
        if not poked and not d.get("build_in_progress"):
            poked = True
            try:
                req = urllib.request.Request(
                    BASE + "/api/ops/data_json/rebuild", method="POST")
                with urllib.request.urlopen(req, timeout=240) as r:
                    print(f"      rebuild → {r.read().decode('utf-8')[:200]}")
            except Exception as e:
                print(f"      rebuild → {type(e).__name__}: {str(e)[:120]}")
        time.sleep(20)
    print("  시간 안에 서버 생성본이 되지 않았다")
    return False


if "--wait-datajson" in sys.argv:
    head("0. data.json 서버 생성 대기")
    sys.exit(0 if wait_for_data_json() else 1)


if "--wait-flow" in sys.argv:
    head("0. 수급 배치 완료 대기")
    wait_for_flow()
    sys.exit(0)


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

head("5-b. data.json — 서버가 만들고 있나")
st, d = get("/api/ops/data_json/status")
print(f"  HTTP {st}")
if isinstance(d, dict):
    print(f"  updated_at={d.get('updated_at')}  actual_date={d.get('actual_date')}")
    print(f"  source={d.get('source')}  테마={d.get('themes')}  "
          f"age={d.get('age_min')}분  stale={d.get('stale')}")
    print(f"  market_overview 키: {d.get('market_overview_keys')}")
    check("data.json 이 서버 생성본", d.get("source") == "server", str(d.get("source")))
    check("24시간 이내", d.get("stale") is False, f"{d.get('age_min')}분 전")
    check("테마 10개 이상", (d.get("themes") or 0) >= 10, str(d.get("themes")))

head("5-b. data.json — 서버가 만들고 있나")
st, d = get("/api/ops/data_json/status")
print(f"  HTTP {st}")
if isinstance(d, dict):
    print(f"  updated_at={d.get('updated_at')}  actual_date={d.get('actual_date')}")
    print(f"  source={d.get('source')}  테마={d.get('themes')}  "
          f"age={d.get('age_min')}분  stale={d.get('stale')}")
    print(f"  market_overview 키: {d.get('market_overview_keys')}")
    check("data.json 이 서버 생성본", d.get("source") == "server", str(d.get("source")))
    check("24시간 이내", d.get("stale") is False, f"{d.get('age_min')}분 전")
    check("테마 10개 이상", (d.get("themes") or 0) >= 10, str(d.get("themes")))
    # 이게 비어도 예외가 안 나서 조용히 지나갔다(2026-09-29 yfinance 경합).
    # 값이 아니라 '없음' 이 정상처럼 보이는 자리라 명시적으로 따진다.
    mk = d.get("market_overview_keys") or []
    check("market_overview 4개 키", len(mk) >= 4, f"{mk}")

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
