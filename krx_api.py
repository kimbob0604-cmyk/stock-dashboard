"""
krx_api.py  —  KRX Open API 경량 래퍼

설계 원칙:
- API 키는 **반드시** 환경변수 KRX_API_KEY 로만 관리. 코드 하드코딩 금지.
- 네트워크/인증 실패 시 None 반환 → 호출 측에서 pykrx fallback 등으로 전환 가능.
- 모든 응답은 KRX 가 반환하는 원형 dict 를 그대로 전달한다 (정규화는 호출 측에서).

KRX Open API 엔드포인트 레퍼런스 (openapi.krx.co.kr 카탈로그 기준):
    apis/sto/stk_bydd_trd         유가증권(KOSPI) 일별매매정보
    apis/sto/ksq_bydd_trd         코스닥 일별매매정보
    apis/sto/knx_bydd_trd         코넥스 일별매매정보
    apis/sto/stk_isu_base_info    유가증권 종목기본정보
    apis/sto/ksq_isu_base_info    코스닥 종목기본정보
    apis/idx/kospi_dd_trd         KOSPI 시리즈 일별시세정보 (업종지수 포함)
    apis/idx/kosdaq_dd_trd        KOSDAQ 시리즈 일별시세정보 (업종지수 포함)
    apis/idx/krx_dd_trd           KRX 시리즈 일별시세정보
    apis/etp/etf_bydd_trd         ETF 일별매매정보

주의:
    KRX Open API 는 **엔드포인트별로 별도 구독**이 필요하다.
    키가 유효해도 구독하지 않은 엔드포인트는 401 "Unauthorized API Call" 반환.
    구독은 https://openapi.krx.co.kr 마이페이지 → API 인증키 발급내역 에서 신청.

인증 헤더:
    AUTH_KEY: <발급받은 키>

사용 예:
    from krx_api import krx_api_call, has_api_key, krx_all_stocks_kospi

    data = krx_api_call("sto/stk_bydd_trd", {"basDd": "20260413"})
    if data is None:
        # 미구독 / 네트워크 실패 — 호출자에서 fallback
        ...
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

# Base URL은 환경변수로 오버라이드 가능하지만 기본값이 실제 KRX Open API 게이트웨이.
KRX_API_BASE = os.environ.get("KRX_API_BASE", "https://data-dbg.krx.co.kr/svc/apis")
KRX_API_KEY  = os.environ.get("KRX_API_KEY", "")

DEFAULT_TIMEOUT  = 30
THROTTLE_SECONDS = 0.2


def has_api_key() -> bool:
    """KRX_API_KEY 환경변수가 설정되어 있는지 여부."""
    return bool(KRX_API_KEY)


def krx_api_call(endpoint: str, params: dict | None = None,
                 max_retries: int = 3,
                 timeout: int = DEFAULT_TIMEOUT) -> dict | None:
    """
    KRX Open API GET 호출. 성공 시 응답 JSON(dict), 실패 시 None.

    - `endpoint` 는 `sto/stk_bydd_trd` 처럼 /svc/apis/ 이후 경로만.
    - AUTH_KEY 헤더 인증.
    - 401(미구독)은 재시도 없이 즉시 None 반환 (구독 추가 전까지 무한 재시도 무의미).
    - 네트워크 오류만 지수 백오프 재시도.
    - 호출 간 200ms 슬립 (속도 제한 회피).
    """
    if not KRX_API_KEY:
        return None

    url = f"{KRX_API_BASE.rstrip('/')}/{endpoint.lstrip('/')}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(url, headers={"AUTH_KEY": KRX_API_KEY})

    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
            data = json.loads(body)
            time.sleep(THROTTLE_SECONDS)
            return data
        except urllib.error.HTTPError as exc:
            # 401 은 미구독 — 재시도 무의미
            if exc.code == 401:
                try:
                    err = json.loads(exc.read().decode("utf-8"))
                    print(f"[KRX API 401] {endpoint}: {err}")
                except Exception:
                    print(f"[KRX API 401] {endpoint}: Unauthorized")
                return None
            # 5xx 는 재시도
            if attempt < max_retries - 1 and 500 <= exc.code < 600:
                time.sleep(2 ** attempt)
                continue
            print(f"[KRX API HTTP {exc.code}] {endpoint}")
            return None
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            print(f"[KRX API 실패] {endpoint}: {exc!r}")
            return None
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 정형화된 헬퍼 — 각 헬퍼는 "OutBlock_1" 행 리스트를 반환 (실패 시 None)
# ─────────────────────────────────────────────────────────────────────────────
def _extract_rows(payload: dict | None) -> list[dict] | None:
    """KRX 응답의 표준 OutBlock_1 리스트 추출."""
    if not isinstance(payload, dict):
        return None
    for key in ("OutBlock_1", "output", "OutBlock_0", "block1"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return rows
    return None


def krx_all_stocks_kospi(basDd: str) -> list[dict] | None:
    """유가증권(KOSPI) 전 종목 일별매매정보 — OHLCV + 시총 + 등락률."""
    return _extract_rows(krx_api_call("sto/stk_bydd_trd", {"basDd": basDd}))


def krx_all_stocks_kosdaq(basDd: str) -> list[dict] | None:
    """코스닥 전 종목 일별매매정보."""
    return _extract_rows(krx_api_call("sto/ksq_bydd_trd", {"basDd": basDd}))


def krx_kospi_series(basDd: str) -> list[dict] | None:
    """KOSPI 시리즈 일별시세정보 — 업종지수(코스피 대형주/중형주/소형주, 건설/금융 등) 포함."""
    return _extract_rows(krx_api_call("idx/kospi_dd_trd", {"basDd": basDd}))


def krx_kosdaq_series(basDd: str) -> list[dict] | None:
    """KOSDAQ 시리즈 일별시세정보 — 업종지수 포함."""
    return _extract_rows(krx_api_call("idx/kosdaq_dd_trd", {"basDd": basDd}))


def krx_stock_master_kospi() -> list[dict] | None:
    """유가증권 종목기본정보 (상장일, 액면가, 주식수 등)."""
    return _extract_rows(krx_api_call("sto/stk_isu_base_info", {}))


def krx_stock_master_kosdaq() -> list[dict] | None:
    """코스닥 종목기본정보."""
    return _extract_rows(krx_api_call("sto/ksq_isu_base_info", {}))


# ─────────────────────────────────────────────────────────────────────────────
# 구독 상태 진단 — 주요 엔드포인트에 대해 실제 HTTP 상태 확인
# ─────────────────────────────────────────────────────────────────────────────
def probe_subscriptions(basDd: str) -> dict:
    """
    주요 엔드포인트 호출 가능 여부를 진단. 각 엔드포인트에 1회 호출.
    Returns: {endpoint: {"ok": bool, "rows": int|None, "error": str|None}}
    """
    if not has_api_key():
        return {"_note": "KRX_API_KEY 환경변수 미설정 — .env 에 추가하거나 서버 환경변수로 주입"}

    endpoints = [
        ("sto/stk_bydd_trd",    {"basDd": basDd}),
        ("sto/ksq_bydd_trd",    {"basDd": basDd}),
        ("idx/kospi_dd_trd",    {"basDd": basDd}),
        ("idx/kosdaq_dd_trd",   {"basDd": basDd}),
        ("sto/stk_isu_base_info", {}),
        ("sto/ksq_isu_base_info", {}),
    ]
    result: dict = {}
    for ep, params in endpoints:
        payload = krx_api_call(ep, params)
        if payload is None:
            result[ep] = {"ok": False, "error": "401 또는 네트워크 실패 — 구독 필요"}
            continue
        rows = _extract_rows(payload)
        result[ep] = {
            "ok":    bool(rows),
            "rows":  len(rows) if rows is not None else None,
            "error": None if rows else "응답 OutBlock 없음",
        }
    return result
