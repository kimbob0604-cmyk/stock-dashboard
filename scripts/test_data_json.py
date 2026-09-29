#!/usr/bin/env python3
"""
data.json 서버 생성기를 **server.py 의 진짜 함수로** 러너에서 검증한다.

개발 컨테이너는 네이버·yfinance 가 프록시에 막혀 있어 여기서 못 돌린다.
배포 전 관문이다. 맥북 cron 산출물(git 에 있는 판)과 스키마가 같은지까지 본다.
"""
import json
import os
import shutil
import sys
import tempfile

os.environ["SERVER_NO_STARTUP"] = "1"
os.environ.setdefault("USE_SQLITE", "0")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 원본 data.json 은 절대 건드리지 않는다. 비교용으로 먼저 읽어 둔다.
with open(os.path.join(ROOT, "data.json"), encoding="utf-8") as f:
    REFERENCE = json.load(f)

import server  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def head(t):
    print("\n" + "=" * 64)
    print(t)
    print("=" * 64)


head("1. 가격 유니버스 확보 (빌더의 입력)")
# 러너에는 cache/ 가 없다. 빌더가 쓰는 naver_universe 를 실제 수집으로 채운다.
uni = server._load_naver_universe()
if not (uni or {}).get("stocks"):
    print("  universe 없음 → _build_naver_universe_background 로 생성 시도")
    try:
        server._build_naver_universe_background()
    except Exception as exc:
        print(f"  생성 실패: {type(exc).__name__}: {exc}")
    uni = server._load_naver_universe()
n_uni = len((uni or {}).get("stocks") or {})
check("universe 종목 1000개 이상", n_uni >= 1000, f"{n_uni}종목")
if n_uni < 1000:
    print("\n입력이 없으면 빌더를 검증할 수 없다. 중단.")
    sys.exit(1)

head("2. 빌더 실행 (파일은 임시 경로로)")
tmpdir = tempfile.mkdtemp(prefix="datajson_test_")
orig = server.DATA_JSON
server.DATA_JSON = type(orig)(os.path.join(tmpdir, "data.json"))
try:
    res = server._build_data_json(write=True)
    print(f"  결과: {json.dumps(res, ensure_ascii=False)}")
    check("생성 성공", res.get("ok") is True, str(res.get("error") or ""))
    if not res.get("ok"):
        sys.exit(1)

    with open(server.DATA_JSON, encoding="utf-8") as f:
        out = json.load(f)

    head("3. 스키마가 기존 data.json 과 같은가")
    ref_keys = set(REFERENCE)
    new_keys = set(out)
    missing = ref_keys - new_keys
    check("기존 최상위 키 전부 포함", not missing, f"누락 {sorted(missing)}")
    print(f"  추가된 키: {sorted(new_keys - ref_keys)}")

    rt, nt = REFERENCE["themes"][0], out["themes"][0]
    miss_t = set(rt) - set(nt)
    check("theme 키 동일", not miss_t, f"누락 {sorted(miss_t)}")
    miss_s = set(rt["stocks"][0]) - set(nt["stocks"][0])
    check("stock 키 동일", not miss_s, f"누락 {sorted(miss_s)}")

    head("4. 값이 말이 되는가")
    print(f"  거래일 {out['actual_date']}  updated_at {out['updated_at']}")
    print(f"  KOSPI  {out['kospi']}")
    print(f"  KOSDAQ {out['kosdaq']}")
    print(f"  테마 {len(out['themes'])}개 / 종목 {res['stocks']}개 "
          f"/ 스파크라인 {res['sparklines']}개")
    print(f"  market_overview: {json.dumps(out['market_overview'], ensure_ascii=False)}")
    print(f"  new_high_sectors {len(out['new_high_sectors'])}개")

    check("테마 10개 이상", len(out["themes"]) >= 10, f"{len(out['themes'])}개")
    check("KOSPI 지수가 양수", (out["kospi"].get("value") or 0) > 0, str(out["kospi"]))
    check("KOSDAQ 지수가 양수", (out["kosdaq"].get("value") or 0) > 0, str(out["kosdaq"]))
    check("actual_date 8자리", len(out["actual_date"]) == 8, out["actual_date"])

    chg = [s["change_pct"] for t in out["themes"] for s in t["stocks"]]
    check("등락률이 전부 0은 아님", any(c != 0 for c in chg),
          f"0 아닌 값 {sum(1 for c in chg if c != 0)}/{len(chg)}")
    vol = [s["volume_mn"] for t in out["themes"] for s in t["stocks"]]
    check("거래대금이 전부 0은 아님", any(v > 0 for v in vol),
          f"양수 {sum(1 for v in vol if v > 0)}/{len(vol)}")

    # 가중평균이 구성종목 등락률 범위 안에 있어야 한다
    bad = []
    for t in out["themes"]:
        cs = [s["change_pct"] for s in t["stocks"] if s["volume_mn"] > 0]
        if cs and not (min(cs) - 0.01 <= t["weighted_avg_pct"] <= max(cs) + 0.01):
            bad.append((t["name"], t["weighted_avg_pct"], min(cs), max(cs)))
    check("가중평균이 구성종목 범위 안", not bad, str(bad[:2]))

    check("market_overview 비어있지 않음", bool(out["market_overview"]),
          "yfinance 실패 시 빈 dict")

    # 스파크라인은 첫 값이 100 이어야 한다(정규화 규칙)
    sp = [s["sparkline"] for t in out["themes"] for s in t["stocks"] if s["sparkline"]]
    if sp:
        check("스파크라인 첫 값 100", all(abs(x[0] - 100.0) < 0.01 for x in sp),
              f"{len(sp)}개 중 위반 {sum(1 for x in sp if abs(x[0]-100.0)>=0.01)}")
    else:
        print("  (스파크라인 없음 — 러너에 ohlcv DB 가 없어서다. Render 에는 22만행 있다)")

    head("5. 실패해도 기존 파일을 덮지 않는가")
    before = server.DATA_JSON.read_text(encoding="utf-8")
    mp = os.path.join(ROOT, "themes_mapping.json")
    bak = mp + ".bak"
    shutil.move(mp, bak)
    try:
        r2 = server._build_data_json(write=True)
        check("매핑 없으면 실패 반환", r2.get("ok") is False, str(r2))
        check("기존 파일 보존됨",
              server.DATA_JSON.read_text(encoding="utf-8") == before)
    finally:
        shutil.move(bak, mp)
finally:
    server.DATA_JSON = orig
    shutil.rmtree(tmpdir, ignore_errors=True)

print("\n" + "=" * 64)
if FAILS:
    print(f"실패 {len(FAILS)}건: {FAILS}")
    sys.exit(1)
print("전부 통과")
