"""
krx_calendar.py — KRX 거래일 달력. 신고가 '120일(=120거래일)' 창의 경계를 정한다.

■ 왜 따로 있는가

120일 신고가의 창은 '판정일로부터 120번째 이전 거래일 ~ 전 거래일' 이다
(신고가 정의 2026-10-08, newhigh.py 머리말). 예전에는 ohlcv 에 있는 날짜를
전 종목에서 DISTINCT 로 모아 달력 대신 썼는데, 그건 **받아 둔 데이터의
날짜 목록**이지 시장의 달력이 아니다. 받아 둔 구간이 짧거나 비면 창이 그만큼
엉뚱한 날로 밀린다. 창의 경계는 시장이 정하고, 종목의 봉은 그 안에서만 본다.

■ 무엇으로 정하는가

  1. 평일 − KRX 휴장일 표(`KRX_HOLIDAYS`). 기본은 이것이다.
  2. **받아 둔 시장 전체 일봉으로 표를 바로잡는다**(`observed`). 표가 틀릴 수
     있어서다 — 해마다 임시공휴일·선거일·대체공휴일이 끼고, 다음 해 표는 KRX 가
     12월에야 낸다. 바로잡는 규칙은 둘뿐이고 둘 다 증거가 강할 때만 쓴다.
       - 표가 휴장이라는데 그날 종목 여럿(`min_codes`)이 봉을 가졌다 → 거래일.
       - 표가 거래일이라는데 앞뒤 거래일엔 종목 여럿이 봉을 가졌고 그날은
         **한 종목도** 없다 → 휴장. 일봉은 종목마다 구간째로 받으므로(증분도
         '자기 마지막 날부터') 한 종목의 공백이 아니라 시장 전체가 빈 날이다.
     받아 둔 구간 밖의 날은 표만 따른다 — 데이터가 없는 것을 휴장으로 읽지 않는다.

`server.py` 의 `_KR_HOLIDAYS_2026` 은 워치독·장 상태가 쓰는 별개의 표이고 이
표와 다르다. 실제 일봉과 맞춰 보면 그 표는 5/1·6/3·7/17·8/17·10/5 휴장이 빠져
있고, 거래일이었던 9/28 을 휴장으로 적고 있다(2026-10-08 확인 — 이 변경에서는
고치지 않았다).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

# KRX 휴장일(주말 제외). 연말 휴장(12/31) 포함.
# 2025-01-02 ~ 2026-10-07 은 네이버 일봉(삼성전자, 결측 없음)의 실제 거래일과
# 평일 하나하나 맞춰 봤다(2026-10-08) — 어긋나는 날이 없다.
# 2027 은 법정 공휴일·대체공휴일 규정으로 미리 적은 **잠정** 값이다 — KRX 공시가
# 나오면 맞춘다. 틀려도 받아 둔 일봉이 그 날을 바로잡는다(위 2).
KRX_HOLIDAYS: dict[int, frozenset[str]] = {
    2025: frozenset({
        "2025-01-01",
        "2025-01-27",                                # 임시공휴일
        "2025-01-28", "2025-01-29", "2025-01-30",    # 설날
        "2025-03-03",                                # 삼일절 대체
        "2025-05-01",                                # 근로자의날
        "2025-05-05", "2025-05-06",                  # 어린이날·부처님오신날 + 대체
        "2025-06-03",                                # 대통령 선거
        "2025-06-06",                                # 현충일
        "2025-08-15",                                # 광복절
        "2025-10-03",                                # 개천절
        "2025-10-06", "2025-10-07", "2025-10-08",    # 추석 + 대체
        "2025-10-09",                                # 한글날
        "2025-12-25",
        "2025-12-31",                                # 연말 휴장
    }),
    2026: frozenset({
        "2026-01-01",
        "2026-02-16", "2026-02-17", "2026-02-18",    # 설날
        "2026-03-02",                                # 삼일절 대체
        "2026-05-01",                                # 근로자의날(노동절)
        "2026-05-05",                                # 어린이날
        "2026-05-25",                                # 부처님오신날 대체
        "2026-06-03",                                # 전국동시지방선거
        "2026-07-17",                                # 제헌절 (2026 공휴일 재지정)
        "2026-08-17",                                # 광복절 대체
        # 추석 9/24~26 — 9/26 이 토요일이라 대체휴일이 없다(설·추석 대체휴일은
        # 일요일과 겹칠 때만). 9/28 은 거래일이었다.
        "2026-09-24", "2026-09-25",
        "2026-10-05",                                # 개천절 대체
        "2026-10-09",                                # 한글날
        "2026-12-25",
        "2026-12-31",                                # 연말 휴장
    }),
    2027: frozenset({                                # 잠정
        "2027-01-01",
        "2027-02-08", "2027-02-09",                  # 설날(2/7 일) + 대체
        "2027-03-01",
        "2027-05-05",
        "2027-05-13",                                # 부처님오신날
        # 제헌절 7/17 은 토요일 — 대체휴일 여부는 KRX 공시를 보고 맞춘다.
        "2027-08-16",                                # 광복절 대체
        "2027-09-14", "2027-09-15", "2027-09-16",    # 추석
        "2027-10-04",                                # 개천절 대체
        "2027-10-11",                                # 한글날 대체
        "2027-12-27",                                # 성탄절 대체
        "2027-12-31",                                # 연말 휴장
    }),
}

# 받아 둔 일봉으로 표를 바로잡을 때 '여럿' 의 하한.
OBSERVED_MIN_CODES = 20


def _d(x) -> date:
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    s = str(x).strip()
    if len(s) == 8 and s.isdigit():
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    return date.fromisoformat(s[:10])


def table_covers(year: int) -> bool:
    return year in KRX_HOLIDAYS


def is_table_trading_day(d) -> bool:
    """표만 보고 거래일인가. 표에 없는 해는 평일이면 거래일로 본다."""
    d = _d(d)
    if d.weekday() >= 5:
        return False
    return d.isoformat() not in KRX_HOLIDAYS.get(d.year, frozenset())


class Calendar:
    """표 + 받아 둔 일봉(observed: {'YYYY-MM-DD': 그날 봉을 가진 종목 수})."""

    def __init__(self, observed: dict | None = None,
                 min_codes: int = OBSERVED_MIN_CODES):
        self.min_codes = min_codes
        self.observed = {str(k)[:10]: int(v or 0) for k, v in (observed or {}).items()}
        strong = sorted(k for k, v in self.observed.items() if v >= min_codes)
        self._lo = strong[0] if strong else None
        self._hi = strong[-1] if strong else None
        self.corrections: list[tuple[str, str]] = []   # (날짜, '휴장→거래' | '거래→휴장')

    def is_trading_day(self, d) -> bool:
        d = _d(d)
        iso = d.isoformat()
        by_table = is_table_trading_day(d)
        n = self.observed.get(iso, 0)
        if n >= self.min_codes:
            if not by_table:
                self._note(iso, "휴장→거래")
            return True
        if by_table and n == 0 and self._lo and self._lo < iso < self._hi:
            # 받아 둔 구간 한가운데 평일인데 시장 전체가 비었다 → 휴장.
            self._note(iso, "거래→휴장")
            return False
        return by_table

    def _note(self, iso, what):
        if (iso, what) not in self.corrections:
            self.corrections.append((iso, what))

    def trading_days_before(self, today, n: int) -> list[str]:
        """`today` **전** 거래일 n개, 최근 → 과거 순 ('YYYY-MM-DD').

        [0] 은 전 거래일, [n-1] 은 n번째 이전 거래일(창의 첫날)이다.
        """
        out: list[str] = []
        d = _d(today) - timedelta(days=1)
        guard = 0
        while len(out) < n:
            if self.is_trading_day(d):
                out.append(d.isoformat())
            d -= timedelta(days=1)
            guard += 1
            if guard > n * 3 + 60:                   # 표가 망가져도 무한루프는 없다
                break
        return out

    def nth_trading_day_before(self, today, n: int) -> str | None:
        days = self.trading_days_before(today, n)
        return days[n - 1] if len(days) >= n else None
