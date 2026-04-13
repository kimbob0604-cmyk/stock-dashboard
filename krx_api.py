"""
krx_api.py  —  KRX Open API 경량 래퍼

설계 원칙:
- API 키는 **반드시** 환경변수 KRX_API_KEY 로만 관리. 코드 하드코딩 금지.
- 네트워크/인증 실패 시 None 반환 → 호출 측에서 pykrx fallback 등으로 전환 가능.
- KRX Open API 엔드포인트 스펙은 openapi.krx.co.kr 에서 확인 필요.
  현재 모듈은 엔드포인트에 무관한 범용 GET 호출기만 제공한다.

사용 예:
    from krx_api import krx_api_call, has_api_key

    if has_api_key():
        data = krx_api_call("sto/stk_bydd_trd", {"basDd": "20260413"})
        if data:
            ...
    else:
        # pykrx fallback
        ...
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

KRX_API_BASE = os.environ.get("KRX_API_BASE", "http://data-dbg.krx.co.kr/svc/apis")
KRX_API_KEY  = os.environ.get("KRX_API_KEY", "")


def has_api_key() -> bool:
    """KRX_API_KEY 환경변수가 설정되어 있는지 여부."""
    return bool(KRX_API_KEY)


def krx_api_call(endpoint: str, params: dict | None = None,
                 max_retries: int = 3, timeout: int = 30) -> dict | None:
    """
    KRX Open API GET 호출. 성공 시 응답 JSON(dict), 실패 시 None.

    - AUTH_KEY 헤더로 인증
    - 지수 백오프 재시도
    - 호출 간 200ms 슬립 (속도 제한 회피)
    """
    if not KRX_API_KEY:
        return None

    url = f"{KRX_API_BASE}/{endpoint.lstrip('/')}"
    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(url, headers={"AUTH_KEY": KRX_API_KEY})

    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
            data = json.loads(body)
            time.sleep(0.2)
            return data
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            print(f"[KRX API 실패] {endpoint}: {exc!r}")
            return None
    return None
