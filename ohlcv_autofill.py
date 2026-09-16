"""
ohlcv_autofill.py — 일봉(ohlcv) 자동 채움. 신고가 판정의 입력을 만든다.

■ 왜 있는가

`ohlcv` 테이블은 server.py 가 **읽기만** 하고(15곳) 쓰는 곳이 없었다. 채우는
코드는 `ohlcv_5y_collector.py`(수동 CLI)와 `db/migrate_from_json.py`(일회성)
둘뿐이고 둘 다 자동으로 돌지 않는다. Render 무료플랜은 영속 디스크가 없어
재배포·재시작마다 `db/dashboard.db` 가 통째로 사라지므로, **재시작 한 번이면
신고가 섹션이 영구히 빈다.** 실제로 그렇게 됐다("일봉 거래일이 0일뿐").

`db_backup.py` 는 ohlcv 를 "재생성 가능(전부 재수집)" 으로 분류해 백업에서
뺐는데, 재수집하는 주체가 없었다. 이 모듈이 그 전제를 비로소 참으로 만든다.

■ 얼마나 받는가

5년이 아니라 **252거래일 + 여유**다. 이 데이터를 쓰는 판정의 최대 창이 52주
(252거래일)라 그 이상은 신고가 결과를 바꾸지 못한다. 부팅 때마다 5년치를
받으면 Render 무료 인스턴스가 못 버틴다. 역사적 신고가는 '보유 구간 전체의
최고' 라는 뜻이므로 구간이 짧아지면 정의도 같이 좁아지는데, 시황 메시지가
`역사적=일봉 {first_day}~` 로 기준 구간을 이미 밝히고 있어 오해가 생기지 않는다.

■ 어디서 받는가

`SOURCE_ORDER` 가 정한 순서로 시도한다. 기본값의 근거는 그 상수 옆에 적었다.
한 소스가 실패하면 다음 소스로 넘어가되, **실패를 삼키지 않는다** — 몇 종목을
어느 소스로 받았고 몇이 실패했는지 결과 dict 와 로그에 남긴다.

■ 무엇을 받는가

종목 범위는 `universe_codes()` 가 정한다(기본: 거래대금 상위 N). 전 종목이
아니므로 **시황 메시지가 그 범위를 밝혀야 한다** — 200종목만 훑고 "신고가
3종목" 이라고 쓰면 읽는 사람은 전 종목 기준으로 읽는다. `coverage_note()` 가
그 문장을 만들고 server.py 의 신고가 섹션이 머리에 싣는다.
"""
from __future__ import annotations

import ast
import logging
import sqlite3
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "db" / "dashboard.db"

# ─────────────────────────── 설정 ───────────────────────────
# 받아 둘 거래일 수. 52주 신고가(252거래일)가 이 데이터를 쓰는 최대 창이라
# 그보다 길게 받아도 판정이 달라지지 않는다. 여유 30일은 휴장·상장 공백으로
# 실제 거래일이 모자라는 것을 막는다.
LOOKBACK_TRADING_DAYS = 252
LOOKBACK_CALENDAR_DAYS = int((LOOKBACK_TRADING_DAYS + 30) * 1.5)

# 몇 종목을 받을 것인가.
#
# 전 종목(약 4,000)은 종목당 0.2~0.4초만 잡아도 20분이 넘는다. Render 무료
# 인스턴스에서 부팅마다 그걸 돌릴 수는 없다. 그래서 **거래대금 상위 N**으로
# 자른다 — 이 저장소가 이미 `_kr_new_highs_from_charts(top_by_volume=200)` 에서
# 쓰는 것과 같은 기준이라 두 화면이 같은 모집단을 본다.
#
# 300 으로 잡은 이유: 200 은 기존 신고가 화면의 값인데, 시황의 신고가는 그보다
# 넓게 보는 편이 낫다(거래대금 200위 밖에서 신고가가 서는 날이 있다).
#
# 실측 (2026-09-16, ohlcv-probe.yml 러너 · 252거래일 구간 · REQUEST_GAP 포함):
#   처리량 측정  20종목 20.7초 → 종목당 1.04초
#   실제 수집    15종목 13.3초 → 종목당 0.89초 (4,260행, 15/15 성공)
# 넉넉히 1.0초로 잡으면 200종목 ≈ 3.3분 · 300종목 ≈ 5분 · 500종목 ≈ 8.3분.
# 300 이면 5분 남짓이라 부팅(데몬 스레드라 Flask 를 막지 않는다)과 장마감 후
# 실행에 무리가 없고, 16:10 에 시작해도 19:00 시황까지 두 시간 반 넘게 남는다.
# 늘리려면 여기만 고친다. 늘린 만큼 메시지의 범위 문구도 자동으로 바뀐다.
UNIVERSE_TOP_N = 300
# 거래대금(백만원) 하한. 이보다 적게 거래된 종목은 신고가가 서도 못 산다.
UNIVERSE_MIN_VOLUME_MN = 50

# 소스 우선순위. 앞의 것이 실패하면 뒤로 넘어간다.
#
# 러너(데이터센터 IP — Render 와 같은 조건)에서 둘을 같은 종목·같은 구간으로
# 실제로 재 봤다 (2026-09-16, ohlcv-probe.yml). **둘 다 됐다.**
#   pykrx  3/3 성공 (0.8~1.2초/종목)
#   네이버 3/3 성공 (0.9~1.5초/종목) — 같은 종가를 돌려줬다(삼성전자 253,500)
#
# 그런데도 네이버를 앞에 두는 이유:
#   1) pykrx 는 실행 중 "KRX 로그인 실패: KRX_ID/KRX_PW 환경 변수가 설정되지
#      않았습니다" 를 찍는다. 지금은 비인증 경로로 넘어가 동작하지만, 자격증명
#      없이 남의 집 뒷문으로 드나드는 셈이라 KRX 정책이 바뀌면 먼저 막힌다.
#      ETF-Traker 쪽에서 KRX 통계 화면이 러너 IP 를 막는 것을 이미 확인했다.
#   2) 네이버는 이 저장소가 가격·유니버스·시총에 이미 의존하는 소스다. 새 집을
#      늘리지 않고 되는 것이 확인된 곳을 쓴다.
#   3) 의존성이 줄어든다 — 네이버 경로는 표준 라이브러리만 쓴다. pykrx 는
#      pandas 를 끌고 오고 응답을 DataFrame 으로 감싼다.
# pykrx 를 폴백으로 남기는 이유는 네이버가 막혔을 때 빈손으로 끝나지 않게
# 하려는 것이다. 순서를 뒤집고 싶으면 이 상수만 고친다.
SOURCE_ORDER = ("naver", "pykrx")

# 종목 사이 간격(초). 네이버에 예의를 지키고 차단을 피한다.
REQUEST_GAP = 0.15

_UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}
_SISE = ("https://api.finance.naver.com/siseJson.naver"
         "?symbol={code}&requestType=1&startTime={start}&endTime={end}"
         "&timeframe=day")


# ─────────────────────────── DB ───────────────────────────
def _get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _save(conn, rows) -> int:
    """(code, date, o, h, l, c, v) 들을 넣는다. 반환 저장 행 수."""
    if not rows:
        return 0
    conn.executemany(
        "INSERT OR REPLACE INTO ohlcv (code, date, open, high, low, close, volume) "
        "VALUES (?,?,?,?,?,?,?)", rows)
    return len(rows)


def status() -> dict:
    """지금 ohlcv 가 어떤 상태인지. 로그와 시황 머리말이 같이 쓴다."""
    try:
        with _get_db() as conn:
            r = conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT code), MIN(date), MAX(date) "
                "FROM ohlcv WHERE code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'"
            ).fetchone()
        return {"rows": r[0] or 0, "codes": r[1] or 0,
                "first": r[2], "last": r[3]}
    except Exception as exc:                               # noqa: BLE001
        return {"rows": 0, "codes": 0, "first": None, "last": None,
                "error": f"{type(exc).__name__}: {exc}"}


# ─────────────────────────── 소스 ───────────────────────────
def _fetch_naver(code: str, start: str, end: str):
    """네이버 일봉 JSON. 반환 [(code, 'YYYY-MM-DD', o,h,l,c,v), ...].

    응답은 JSON 이 아니라 작은따옴표 파이썬 리터럴이라 `ast.literal_eval` 로
    읽는다. 첫 행은 헤더(['날짜','시가','고가','저가','종가','거래량','외국인소진율']).
    """
    req = urllib.request.Request(
        _SISE.format(code=code, start=start, end=end), headers=_UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read().decode("utf-8")
    rows = ast.literal_eval(raw.strip())
    if not isinstance(rows, list) or len(rows) < 2:
        return []
    out = []
    for x in rows[1:]:
        # 형식이 바뀌면 조용히 0 을 채우지 않고 그 행을 버린다.
        if not isinstance(x, (list, tuple)) or len(x) < 6:
            continue
        try:
            d = str(x[0]).strip()
            d = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 and d.isdigit() else d
            out.append((code, d, float(x[1]), float(x[2]), float(x[3]),
                        float(x[4]), float(x[5])))
        except (TypeError, ValueError):
            continue
    return out


def _fetch_pykrx(code: str, start: str, end: str):
    """pykrx 일봉. 네이버가 막혔을 때의 대체 경로."""
    from pykrx import stock
    df = stock.get_market_ohlcv(start, end, code)
    if df is None or df.empty:
        return []
    out = []
    for d, r in df.iterrows():
        try:
            out.append((code, d.strftime("%Y-%m-%d"),
                        float(r["시가"]), float(r["고가"]), float(r["저가"]),
                        float(r["종가"]), float(r["거래량"])))
        except (TypeError, ValueError, KeyError):
            continue
    return out


_FETCHERS = {"naver": _fetch_naver, "pykrx": _fetch_pykrx}


# ─────────────────────────── 종목 범위 ───────────────────────────
def universe_codes(top_n: int = UNIVERSE_TOP_N,
                   min_volume_mn: float = UNIVERSE_MIN_VOLUME_MN,
                   load_universe=None) -> list[str]:
    """거래대금 상위 top_n 종목코드. 기존 신고가 화면과 같은 기준으로 고른다.

    `load_universe` 는 server.py 의 `_load_naver_universe` 를 넣는 자리다
    (이 모듈이 server.py 를 import 하면 순환이 된다).
    """
    uni = (load_universe() if load_universe else None) or {}
    stocks = uni.get("stocks") or {}
    eligible = [
        (s.get("volume_mn") or 0, code)
        for code, s in stocks.items()
        if str(code).isdigit() and len(str(code)) == 6
        and (s.get("volume_mn") or 0) >= min_volume_mn
    ]
    eligible.sort(reverse=True)
    return [c for _, c in eligible[:top_n]]


def coverage_note(top_n: int = UNIVERSE_TOP_N) -> str:
    """시황 신고가 머리에 붙일 '무엇을 대상으로 했는가' 한 조각.

    전 종목이 아니라는 사실을 읽는 사람이 알아야 한다. 이 문장이 없으면
    상위 N 종목만 훑은 결과를 전 종목 기준으로 읽는다.
    """
    return f"거래대금 상위 {top_n:,}종목 대상"


# ─────────────────────────── 본체 ───────────────────────────
def fill(codes: list[str] | None = None,
         load_universe=None,
         lookback_days: int = LOOKBACK_CALENDAR_DAYS,
         source_order=SOURCE_ORDER,
         gap: float = REQUEST_GAP,
         now=None) -> dict:
    """일봉을 받아 ohlcv 에 넣는다. 반환: 무슨 일이 있었는지 담은 dict.

    예외를 올리지 않는다 — 부팅 스레드와 스케줄러 잡이 부르는 자리라 죽으면
    그대로 침묵이 된다. 대신 실패는 반환값과 로그에 남는다.
    """
    t0 = time.time()
    now = now or datetime.now()
    end = now.strftime("%Y%m%d")
    start = (now - timedelta(days=lookback_days)).strftime("%Y%m%d")

    if codes is None:
        codes = universe_codes(load_universe=load_universe)
    res = {"codes": len(codes), "ok": 0, "failed": 0, "rows": 0,
           "by_source": {}, "errors": [], "elapsed": 0.0,
           "start": start, "end": end}
    if not codes:
        res["errors"].append("대상 종목이 없다 — 유니버스가 비었다")
        log.warning("[일봉 채움] 대상 종목 0 — 유니버스가 비어 있다")
        return res

    try:
        conn = _get_db()
    except Exception as exc:                               # noqa: BLE001
        res["errors"].append(f"DB 열기 실패: {type(exc).__name__}: {exc}")
        log.warning("[일봉 채움] DB 열기 실패: %s", exc)
        return res

    try:
        for i, code in enumerate(codes, 1):
            got = None
            for src in source_order:
                fn = _FETCHERS.get(src)
                if fn is None:
                    continue
                try:
                    rows = fn(code, start, end)
                except Exception as exc:                   # noqa: BLE001
                    if len(res["errors"]) < 5:
                        res["errors"].append(
                            f"{code} {src}: {type(exc).__name__}: {str(exc)[:80]}")
                    continue
                if rows:
                    got = (src, rows)
                    break
            if got is None:
                res["failed"] += 1
            else:
                src, rows = got
                res["rows"] += _save(conn, rows)
                res["ok"] += 1
                res["by_source"][src] = res["by_source"].get(src, 0) + 1
            if i % 50 == 0:
                conn.commit()
            if gap:
                time.sleep(gap)
        conn.commit()
    finally:
        conn.close()

    res["elapsed"] = round(time.time() - t0, 1)
    st = status()
    res["status"] = st
    # 끝났으면 무엇이 들어왔는지 한 줄로 남긴다. 조용히 끝내지 않는다.
    log.info("[일봉 채움] %d/%d종목 · %s행 저장 · %.1f초 · 소스 %s · "
             "테이블 %s행/%s종목 최신 %s",
             res["ok"], res["codes"], f"{res['rows']:,}", res["elapsed"],
             res["by_source"] or "없음",
             f"{st['rows']:,}", st["codes"], st["last"])
    if res["failed"]:
        log.warning("[일봉 채움] %d종목 실패%s", res["failed"],
                    f" — 예: {res['errors'][0]}" if res["errors"] else "")
    if res["ok"] == 0:
        log.error("[일봉 채움] 한 종목도 못 받았다 — 신고가 섹션이 빈다. "
                  "소스가 전부 막혔는지 확인하라 (%s)", ", ".join(source_order))
    return res


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    argv = sys.argv[1:]
    if argv and argv[0] == "status":
        print(status())
    else:
        print(fill(codes=argv or ["005930", "000660", "055550"]))
