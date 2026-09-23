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

종목 범위는 `select_universe()` 가 정한다 — **ETF/ETN 을 뺀 시가총액 1,000억원
이상 전 종목**(시드 기준 1,394종목, 2026-06-02). 예전에는 거래대금 상위 300
이었는데, 그 300 가운데 상당수가 ETF 라(판정에서는 빠진다) 실제로 판정에
남은 주식은 222종목이었고 시황은 "222종목 대상" 이라고 적었다. 시총 1,000억
이상인데 그날 거래대금 순위 밖이라 아예 안 본 종목이 천 개가 넘었다는 뜻이다.

종목이 네다섯 배로 늘어도 버티게 하는 장치는 둘이다.
  - **증분** — 이미 받아 둔 종목은 그 종목의 마지막 날부터만 받는다. 최근
    거래일까지 있으면 아예 묻지 않는다. 전 구간(1년+)은 처음 보는 종목만.
  - **소수 동시 요청** — `FILL_WORKERS` 개만 동시에, 배치로 끊어 저장한다.
    받은 행을 배치마다 DB 에 넣고 버려 메모리가 배치 크기를 넘지 않는다.

그래도 모집단 전부가 늘 차 있다고 말할 수는 없다(재배포 직후 채우는 중이거나
일부가 실패한 날). 그래서 시황은 **실제로 판정한 수와 모집단 수를 같이**
적는다 — `coverage_note()` 가 그 문장을 만들고 server.py 의 신고가 섹션이
머리에 싣는다.
"""
from __future__ import annotations

import ast
import logging
import sqlite3
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
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

# 신고가 판정의 모집단 — **ETF/ETN 을 뺀 시가총액 1,000억원 이상 전 종목.**
#
# 단위는 **원**이다. stocks.market_cap 과 시드(data/naver_universe_seed.json)의
# market_cap 이 모두 네이버 marketValueFullRaw(원)다 — 삼성전자가 2.1e15.
# 억 단위로 착각해 1000 을 넣으면 사실상 전 종목이 되고, 1e11 을 억으로 읽으면
# 한 종목도 안 남는다. 문구에 쓰는 이름표(`MIN_MARKET_CAP_LABEL`)도 여기 같이 둔다.
#
# 시드로 잰 규모 (2026-06-02 시총, ETF 패턴 제외): 1,000억 이상 1,394종목.
MIN_MARKET_CAP_WON = 100_000_000_000
MIN_MARKET_CAP_LABEL = "1,000억"

# 일봉은 판정 기준보다 **조금 넓게**(80%, 800억 이상 — 시드 기준 1,556종목)
# 받아 둔다. 시총은 매일 움직이는데 일봉 1년치는 하루 만에 못 채운다(16:10
# 잡이 다음 날 16:00 시황보다 먼저 돌아도, 그 사이 1,000억을 넘어선 종목은
# 전날 대상이 아니었다). 경계 근처 종목을 미리 받아 두면 그런 종목이 '일봉
# 미수집' 으로 모집단에서 빠지는 일이 드물어진다. 판정은 1,000억으로 한다.
FILL_CAP_BUFFER = 0.8
FILL_MIN_CAP_WON = int(MIN_MARKET_CAP_WON * FILL_CAP_BUFFER)

# 동시에 몇 종목을 물을 것인가 · 몇 종목마다 저장할 것인가.
#
# 실측 (2026-09-16, ohlcv-probe.yml 러너 · 252거래일 구간 · REQUEST_GAP 포함):
#   처리량 측정  20종목 20.7초 → 종목당 1.04초
#   실제 수집    15종목 13.3초 → 종목당 0.89초 (4,260행, 15/15 성공)
# 한 줄로 받으면 1,556종목 ≈ 26분이다. 대부분이 응답 대기라 동시 4개면
# 이론상 ≈ 6.5분(Render 에서 잰 값이 아니다 — 러너 실측에서 나눈 추정).
# 네이버에 초당 4~5건 남짓이라 이 저장소가 이미 쓰는 가격 폴링(100종목씩
# 연달아)보다 무겁지 않다. 막히면 `_BREAKER_MIN` 이 멈춘다.
#
# 배치 크기는 메모리 상한이다. 받은 행은 배치마다 저장하고 버린다 —
# 1년치 1,556종목을 한꺼번에 들고 있으면 파이썬 튜플로 100MB 가 넘어
# 512MB 인스턴스에 부담이 된다. 40종목이면 ~3MB 다.
FILL_WORKERS = 4
FILL_BATCH = 40

# 한 배치가 이만큼 이상인데 **한 종목도** 못 받았으면 소스가 막힌 것으로 보고
# 멈춘다. 막힌 채 1,500종목을 두 소스 × 타임아웃 20초로 두드리면 한 시간이
# 넘게 락을 쥐고 시황까지 붙잡는다. 멈춘 사실은 결과와 로그에 남긴다.
_BREAKER_MIN = 10

# 오늘 봉은 이 시각(KST) 뒤에만 받는다. 장중에 받은 봉은 미확정 종가다.
# 그걸 저장하면 '최근 거래일까지 있다' 로 보여 16:10 잡이 건너뛰고, 다음 날
# 시황이 그 미확정 종가를 '전일 종가' 로 쓴다. 정규장 종가는 15:30 에 선다.
CLOSE_FINAL_HHMM = (15, 40)

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
def _rank_from_universe(uni, min_cap, is_etf=None):
    """유니버스 dict 에서 (기준, [(시총, 코드)]) 를 뽑는다. 시총 하한·ETF 제외.

    Render 재배포 직후 `stocks` 표가 아직 비었을 때의 폴백이다. 그 환경에서
    `_load_naver_universe()` 가 돌려주는 것은 커밋된 시드
    (`data/naver_universe_seed.json`)인데, 시드에는 `market_cap`(원)이 있다.
    거래대금만 보던 옛 코드는 시드에서 0종목을 돌려줬다(2026-09-18) — 시총으로
    고르는 지금은 그 문제가 없지만, 시총도 없으면 **지어내지 않고** 빈손을 돌려준다.

    `is_etf(name)` 을 주면 이름으로 ETF/ETN 을 거른다. 시드에는 is_etf 표식이
    없어서다 — 안 거르면 KODEX 200 같은 대형 ETF 수백 개를 헛되이 받는다.
    """
    stocks = (uni or {}).get("stocks") or {}
    out = []
    for code, s in stocks.items():
        if not (str(code).isdigit() and len(str(code)) == 6):
            continue
        cap = (s or {}).get("market_cap") or 0
        if cap < min_cap or cap <= 0:
            continue
        if is_etf is not None and is_etf((s or {}).get("name") or ""):
            continue
        out.append((cap, code))
    return ("market_cap" if out else "none"), out


def select_universe(min_cap: float = FILL_MIN_CAP_WON,
                    load_universe=None,
                    load_ranked=None,
                    is_etf=None) -> tuple[list[str], str]:
    """일봉을 받을 종목코드(시총 큰 순)와 **무슨 기준으로 골랐는지**.

    규칙은 하나다 — **ETF/ETN 이 아니고 시총이 `min_cap`(원) 이상인 전 종목.**
    상위 N 으로 자르지 않는다. 시총 큰 순으로 세우는 것은 채우다 끊겼을 때
    큰 종목부터 차 있게 하려는 것뿐이다.

    값을 어디서 읽느냐만 둘이다.

      1. `load_ranked`  server.py 가 넣어 주는 `stocks` 표의 [(시총, 코드)].
                        ETF 는 server 쪽이 is_etf 표식으로 이미 뺐다. 부팅 가격
                        갱신이 이 표를 채우고 시총도 폴링 값으로 바꾼다.
      2. 유니버스(시드)  위가 비었거나 터졌을 때. ETF 는 `is_etf(name)` 로 뺀다.

    기준 이름은 `"market_cap"` / `"none"` 둘 중 하나다.
    """
    if load_ranked is not None:
        try:
            basis, ranked = load_ranked()
        except Exception as exc:                           # noqa: BLE001
            log.warning("[일봉 채움] stocks 시총 조회 실패: %s — 유니버스로 넘어간다",
                        exc)
            basis, ranked = "none", []
        ranked = [(cap, c) for cap, c in (ranked or [])
                  if (cap or 0) >= min_cap and (cap or 0) > 0]
        if ranked:
            ranked.sort(reverse=True)
            return [c for _, c in ranked], "market_cap"

    uni = (load_universe() if load_universe else None) or {}
    basis, ranked = _rank_from_universe(uni, min_cap, is_etf)
    ranked.sort(reverse=True)
    return [c for _, c in ranked], basis


def universe_codes(min_cap: float = FILL_MIN_CAP_WON,
                   load_universe=None,
                   load_ranked=None,
                   is_etf=None) -> list[str]:
    """`select_universe` 의 종목코드만. 기준까지 필요하면 그쪽을 쓴다."""
    return select_universe(min_cap, load_universe, load_ranked, is_etf)[0]


def coverage_note(scanned: int, universe: int | None = None) -> str:
    """시황 신고가 머리에 붙일 '무엇을 대상으로 했는가' 한 조각.

    `scanned`  실제로 판정한 종목 수(일봉이 있는 종목). 설정값이 아니라 결과다.
    `universe` 모집단 — 시총 1,000억 이상·ETF 아님·오늘 거래된 종목 수.
               None 이면 모집단을 모르는 것이므로 판정 수만 적는다.

    **모집단을 다 못 훑었으면 그 사실과 빠진 수를 적는다.** 재배포 직후 일봉을
    채우는 중에 "시총 1,000억 이상 대상" 이라고만 쓰면, 600종목만 본 결과를
    1,400종목 기준으로 읽는다. 다 훑었을 때도 '전체' 라고 쓰지 않는다 —
    모집단을 밝히는 것으로 충분하고, 더 센 말은 틀릴 여지만 늘린다.
    """
    if universe is None:
        return f"{scanned:,}종목 대상"
    head = f"시총 {MIN_MARKET_CAP_LABEL} 이상"
    if scanned >= universe:
        return f"{head} {scanned:,}종목 대상"
    return (f"{head} {universe:,}종목 중 {scanned:,}종목 대상 · "
            f"일봉 미수집 {universe - scanned:,}종목")


# ─────────────────────────── 본체 ───────────────────────────
def _ymd(d: str) -> str:
    """'YYYY-MM-DD' / 'YYYYMMDD' → 'YYYYMMDD'."""
    return str(d).replace("-", "")[:8]


def _last_dates(conn, codes) -> dict:
    """{코드: 그 종목이 ohlcv 에 가진 마지막 날('YYYY-MM-DD')}. 없는 종목은 빠진다.

    SQLite 의 바인딩 변수 상한(옛 빌드 999)을 넘지 않게 끊어 묻는다.
    """
    out = {}
    codes = list(codes)
    for i in range(0, len(codes), 500):
        part = codes[i:i + 500]
        qs = ",".join("?" * len(part))
        for code, last in conn.execute(
                f"SELECT code, MAX(date) FROM ohlcv WHERE code IN ({qs}) "
                f"GROUP BY code", part).fetchall():
            if last:
                out[code] = last
    return out


def plan(codes, last_by_code: dict, full_start: str, up_to: str | None,
         full: bool = False):
    """종목마다 **어디서부터 받을지** 정한다. 반환 ([(코드, 시작 YYYYMMDD)], 건너뜀 수).

      - 일봉이 없는 종목        → `full_start` 부터 전 구간 (신규 상장이면
                                  소스가 상장일부터만 준다 — 그대로 둔다)
      - 마지막 날 ≥ `up_to`     → 묻지 않는다 (이미 최근 거래일까지 있다)
      - 그 밖                   → **자기 마지막 날부터** (그날도 다시 받는다 —
                                  덮어써도 PK 라 중복이 안 생기고, 혹시 남은
                                  미확정 봉이 확정 종가로 바뀐다)

    건너뛰는 기준이 '며칠 이내' 가 아니라 `up_to`(최근 거래일)인 이유는
    server.py `_fill_ohlcv_job_inner` 주석에 있다 — 16:10 잡이 매일 자기
    자신을 건너뛰지 않게 하려는 것이다. `full=True` 면 가진 것을 무시하고
    전부 전 구간으로 받는다(수동 `?force=1`).
    """
    todo, skipped = [], 0
    up = _ymd(up_to) if up_to else None
    for code in codes:
        last = None if full else last_by_code.get(code)
        if last is None:
            todo.append((code, full_start))
        elif up and _ymd(last) >= up:
            skipped += 1
        else:
            todo.append((code, _ymd(last)))
    return todo, skipped


def _fetch_one(code, start, end, source_order, gap):
    """한 종목. (코드, 소스|None, 행들, 오류들). 예외를 올리지 않는다."""
    errs = []
    got_src, got_rows = None, []
    for src in source_order:
        fn = _FETCHERS.get(src)
        if fn is None:
            continue
        try:
            rows = fn(code, start, end)
        except Exception as exc:                           # noqa: BLE001
            errs.append(f"{code} {src}: {type(exc).__name__}: {str(exc)[:80]}")
            continue
        # 요청한 끝날보다 뒤의 봉은 버린다 — 장중이면 끝날을 어제로 잡는데,
        # 소스가 그래도 오늘 미확정 봉을 끼워 주면 그걸 저장하지 않는다.
        end_iso = f"{end[:4]}-{end[4:6]}-{end[6:8]}"
        rows = [r for r in rows if r[1] <= end_iso]
        if rows:
            got_src, got_rows = src, rows
            break
    if gap:
        time.sleep(gap)
    return code, got_src, got_rows, errs


def fill(codes: list[str] | None = None,
         load_universe=None,
         load_ranked=None,
         is_etf=None,
         lookback_days: int = LOOKBACK_CALENDAR_DAYS,
         source_order=SOURCE_ORDER,
         gap: float = REQUEST_GAP,
         workers: int = FILL_WORKERS,
         batch: int = FILL_BATCH,
         up_to: str | None = None,
         full: bool = False,
         now=None) -> dict:
    """일봉을 받아 ohlcv 에 넣는다. 반환: 무슨 일이 있었는지 담은 dict.

    **증분이다** — `plan()` 이 종목마다 시작일을 정한다. 이미 최근 거래일
    (`up_to`)까지 있는 종목은 묻지도 않는다. 그래서 여러 번 불러도 싸고,
    중간에 끊겨도(재시작) 다음 호출이 남은 종목만 이어 받는다.

    예외를 올리지 않는다 — 부팅 스레드와 스케줄러 잡이 부르는 자리라 죽으면
    그대로 침묵이 된다. 대신 실패는 반환값과 로그에 남는다.
    """
    t0 = time.time()
    now = now or datetime.now()
    # 장 마감 전이면 끝날을 어제로 — 오늘 미확정 봉을 저장하지 않는다
    # (CLOSE_FINAL_HHMM 설명). 날짜만 넘기므로 주말·휴장이 끼어도 소스가
    # 있는 거래일만 돌려준다.
    end_dt = now if (now.hour, now.minute) >= CLOSE_FINAL_HHMM \
        else now - timedelta(days=1)
    # 주말이면 금요일로 — 그래야 금요일까지 찬 종목을 '모자라다' 로 안 본다.
    while end_dt.weekday() >= 5:
        end_dt -= timedelta(days=1)
    end = end_dt.strftime("%Y%m%d")
    start = (now - timedelta(days=lookback_days)).strftime("%Y%m%d")
    # 최근 거래일이 오늘이어도 아직 오늘 봉을 받을 수 없으면, 그걸 기준으로
    # '모자라다' 고 볼 수 없다. 받을 수 있는 끝날로 누른다.
    if up_to and _ymd(up_to) > end:
        up_to = end

    basis = "given"
    if codes is None:
        codes, basis = select_universe(load_universe=load_universe,
                                       load_ranked=load_ranked, is_etf=is_etf)
    res = {"codes": len(codes), "ok": 0, "failed": 0, "rows": 0,
           "skipped": 0, "full": 0, "incremental": 0, "stopped": None,
           "by_source": {}, "errors": [], "elapsed": 0.0,
           "start": start, "end": end, "basis": basis}
    if not codes:
        res["errors"].append("대상 종목이 없다 — 시가총액을 못 읽었다")
        log.error("[일봉 채움] 대상 종목 0 — stocks 표에도 유니버스에도 "
                  "시가총액이 없다. 신고가 섹션이 빈다")
        return res

    try:
        conn = _get_db()
    except Exception as exc:                               # noqa: BLE001
        res["errors"].append(f"DB 열기 실패: {type(exc).__name__}: {exc}")
        log.warning("[일봉 채움] DB 열기 실패: %s", exc)
        return res

    try:
        try:
            last_by_code = {} if full else _last_dates(conn, codes)
        except sqlite3.Error as exc:
            # 표가 아직 없으면 전부 처음 받는 것으로 본다.
            log.warning("[일봉 채움] 종목별 최신일 조회 실패: %s — 전 구간으로", exc)
            last_by_code = {}
        todo, res["skipped"] = plan(codes, last_by_code, start, up_to, full)
        res["full"] = sum(1 for _, st in todo if st == start)
        res["incremental"] = len(todo) - res["full"]
        log.info("[일봉 채움] 대상 %d종목 (시총 %s원 이상, ETF 제외) — "
                 "전 구간 %d · 증분 %d · 이미 최신 %d · 동시 %d",
                 len(codes), f"{FILL_MIN_CAP_WON:,}" if basis != "given" else "-",
                 res["full"], res["incremental"], res["skipped"], workers)

        with ThreadPoolExecutor(max_workers=max(1, workers),
                                thread_name_prefix="ohlcv") as pool:
            for i in range(0, len(todo), max(1, batch)):
                part = todo[i:i + batch]
                results = list(pool.map(
                    lambda cs: _fetch_one(cs[0], cs[1], end, source_order, gap),
                    part))
                # 저장은 이 스레드 하나만 한다 — SQLite 연결을 스레드끼리
                # 나눠 쓰지 않는다.
                got_any = False
                for code, src, rows, errs in results:
                    for e in errs:
                        if len(res["errors"]) < 5:
                            res["errors"].append(e)
                    if src is None:
                        res["failed"] += 1
                        continue
                    got_any = True
                    res["rows"] += _save(conn, rows)
                    res["ok"] += 1
                    res["by_source"][src] = res["by_source"].get(src, 0) + 1
                conn.commit()
                del results                    # 배치 행을 들고 있지 않는다
                if not got_any and len(part) >= _BREAKER_MIN:
                    left = len(todo) - (i + len(part))
                    res["stopped"] = (f"{len(part)}종목 배치에서 한 종목도 못 받아 "
                                      f"멈춤 — 남은 {left}종목은 다음 호출에")
                    log.error("[일봉 채움] %s (%s)", res["stopped"],
                              ", ".join(source_order))
                    break
                if (i // max(1, batch)) % 5 == 4:
                    log.info("[일봉 채움] 진행 %d/%d종목 · %.0f초",
                             i + len(part), len(todo), time.time() - t0)
    finally:
        conn.close()

    res["elapsed"] = round(time.time() - t0, 1)
    st = status()
    res["status"] = st
    # 끝났으면 무엇이 들어왔는지 한 줄로 남긴다. 조용히 끝내지 않는다.
    log.info("[일봉 채움] %d/%d종목 받음(이미 최신 %d) · %s행 저장 · %.1f초 · "
             "소스 %s · 테이블 %s행/%s종목 최신 %s",
             res["ok"], res["codes"] - res["skipped"], res["skipped"],
             f"{res['rows']:,}", res["elapsed"], res["by_source"] or "없음",
             f"{st['rows']:,}", st["codes"], st["last"])
    if res["failed"]:
        log.warning("[일봉 채움] %d종목 실패%s", res["failed"],
                    f" — 예: {res['errors'][0]}" if res["errors"] else "")
    if res["ok"] == 0 and len(todo) > 0:
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
