#!/usr/bin/env python3
"""
새 수집기를 **실제 server.py 코드로** 러너에서 검증한다.

개발 컨테이너는 네이버가 프록시에 막혀 있어 여기서 돌릴 수 없다.
GitHub Actions 러너에서 돌린다. 배포 전에 깨진 파서를 걸러내는 관문이다.

SERVER_NO_STARTUP=1 로 스케줄러·백그라운드 스레드를 띄우지 않고 함수만 쓴다.
"""
import os
import sys

os.environ["SERVER_NO_STARTUP"] = "1"
os.environ.setdefault("USE_SQLITE", "0")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


print("=" * 62)
print("1. 수급 (_fetch_naver_trend)")
print("=" * 62)
t = server._fetch_naver_trend("005930", days=20)
check("응답 있음", t is not None)
if t:
    d, c = t["dates"], t["closes"]
    f, i, iv = t["foreign_net"], t["inst_net"], t["individual_net"]
    print(f"    거래일 {len(d)}일: {d[0]} ~ {d[-1]}")
    print(f"    종가   {c[0]:,} → {c[-1]:,}")
    print(f"    외국인 최신 {f[-1]:+,}주 / 기관 {i[-1]:+,}주 / 개인 {iv[-1]:+,}주")
    print(f"    외국인 보유율 {t['foreign_hold_ratio']}")
    check("거래일 10일 이상", len(d) >= 10, f"{len(d)}일")
    check("날짜 오름차순 (오래된→최신)", d == sorted(d), f"{d[0]} … {d[-1]}")
    check("길이 일치", len({len(d), len(c), len(f), len(i), len(iv)}) == 1)
    check("종가 전부 양수", all(x > 0 for x in c))
    check("순매수 부호 섞여 있음 (전부 0 아님)", any(x != 0 for x in f))
    # 제로섬: 외국인+기관+개인 ≈ 0 (기타법인·국가 등이 빠져 완전 0은 아니다)
    tot = f[-1] + i[-1] + iv[-1]
    scale = max(abs(f[-1]), abs(i[-1]), abs(iv[-1]), 1)
    check("3주체 합계가 거래량 규모 대비 작다", abs(tot) < scale * 2,
          f"합계 {tot:+,} vs 최대 {scale:,}")

print()
print("=" * 62)
print("2. 업종 목록 (_scrape_naver_sectors)")
print("=" * 62)
secs = server._scrape_naver_sectors()
check("업종 수집됨", bool(secs), f"{len(secs)}개")
if secs:
    s0 = secs[0]
    print(f"    첫 업종: {s0}")
    check("업종 70개 이상", len(secs) >= 70, f"{len(secs)}개")
    check("필드 완비", set(s0) == {"no", "name", "change_pct", "total", "up", "flat", "down"},
          str(sorted(s0)))
    check("no 가 숫자문자열", all(x["no"].isdigit() for x in secs))
    check("이름 비어있지 않음", all(x["name"] for x in secs))
    check("등락률이 전부 0은 아님", any(x["change_pct"] != 0 for x in secs))
    check("종목수 합이 1000 이상", sum(x["total"] for x in secs) >= 1000,
          str(sum(x["total"] for x in secs)))
    # up+flat+down == total 인지 (한 업종이라도 어긋나면 필드 매핑이 틀린 것)
    bad = [x for x in secs if x["up"] + x["flat"] + x["down"] != x["total"]]
    check("up+flat+down == total", not bad,
          f"어긋난 업종 {len(bad)}개" + (f" 예: {bad[0]}" if bad else ""))

print()
print("=" * 62)
print("3. 업종 상세 (_scrape_naver_sector_detail)")
print("=" * 62)
no = secs[0]["no"] if secs else "261"
det = server._scrape_naver_sector_detail(no)
check("상세 수집됨", bool(det.get("stocks")), f"{len(det.get('stocks') or [])}종목")
if det.get("stocks"):
    print(f"    업종명: {det['sector_name']}")
    print(f"    첫 종목: {det['stocks'][0]}")
    st = det["stocks"]
    check("종목코드 6자리", all(len(x["code"]) == 6 and x["code"].isdigit() for x in st))
    check("종가 양수", all(x["close"] > 0 for x in st),
          f"0 인 종목 {sum(1 for x in st if x['close'] <= 0)}개")
    check("등락률이 전부 0은 아님", any(x["change_pct"] != 0 for x in st),
          "전부 0이면 fluctuationsRatio 키가 없다는 뜻")
    check("거래량이 전부 0은 아님", any(x["volume"] != 0 for x in st),
          "전부 0이면 accumulatedTradingVolume 키가 없다는 뜻")

print()
print("=" * 62)
print("4. 부수 검증")
print("=" * 62)
print(f"    _get_trading_date() = {server._get_trading_date()}")
check("거래일이 8자리", len(server._get_trading_date()) == 8)
# data.json 이 멈춰도 2주 전을 돌려주면 안 된다. 러너는 cache/ 가 비어 있어서
# data.json 하나만 후보인데, 그게 낡으면 오늘 기준으로 대체돼야 한다.
import datetime as _dt  # noqa: E402
_td = server._get_trading_date()
_gap = (_dt.datetime.now(_dt.timezone(_dt.timedelta(hours=9))).replace(tzinfo=None)
        - _dt.datetime.strptime(_td, "%Y%m%d")).days
check("거래일이 7일 이내", _gap <= 7, f"{_td} ({_gap}일 전)")

masked = server._mask_secrets("api_key=abcd1234efgh&token=zzzz9999 bot123456789:AAbbCCddEEffGGhhIIjjKKllMMnnOO")
print(f"    마스킹: {masked}")
check("api_key 가려짐", "abcd1234efgh" not in masked)
check("봇 토큰 가려짐", "AAbbCCddEEffGGhhIIjjKKllMMnnOO" not in masked)

print()
print("=" * 62)
if FAILS:
    print(f"실패 {len(FAILS)}건: {FAILS}")
    sys.exit(1)
print("전부 통과")
