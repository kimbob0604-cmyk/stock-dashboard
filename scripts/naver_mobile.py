"""네이버 모바일 JSON API(m.stock.naver.com) 공통 수집 — 마스터·테마 빌더가 같이 쓴다.

표준 라이브러리만 쓴다. 서버(server.py)는 이 모듈을 쓰지 않는다 — 빌더는
Actions 러너에서 돌고, 서버는 빌더가 커밋한 파일만 읽는다.

**페이지가 밀린다.** 목록은 100개씩 나눠 주는데(200 이상은 HTTP 400) 장중엔
정렬 순서(시총·등락률)가 요청 사이에 바뀐다. 한 종목이 앞 페이지로 올라가면
빠지고, 뒤로 밀리면 두 번 나온다. 2026-10-13 장중 첫 수집에서 반도체 업종
172종목 중 원익홀딩스·제우스가 그렇게 빠졌다. 그래서 fetch_complete 는 고유
종목 수가 totalCount 와 맞을 때까지 그 목록을 다시 받는다.
"""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Callable

BASE = "https://m.stock.naver.com/api/stocks"
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.stock.naver.com/"}
PAGE = 100


def get_json(url: str, tries: int = 4) -> dict:
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as exc:                              # noqa: BLE001
            if i == tries - 1:
                raise RuntimeError(f"{url} — {type(exc).__name__}: {exc}") from exc
            time.sleep(2 ** i)
    raise AssertionError("unreachable")


def _collect(path: str, list_key: str, id_key: str, label: str, tries: int,
             on_page: Callable[[dict], None] | None) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for attempt in range(1, tries + 1):
        page, total = 1, 0
        while True:
            d = get_json(f"{BASE}/{path}?page={page}&pageSize={PAGE}")
            if on_page:
                on_page(d)
            batch = d.get(list_key) or []
            total = int(d.get("totalCount") or 0)
            for r in batch:
                rows[str(r.get(id_key) or "")] = r
            if len(batch) < PAGE or page * PAGE >= total:
                break
            page += 1
            time.sleep(0.15)
        if len(rows) == total:
            return rows
        print(f"  {label}: 고유 {len(rows)} / totalCount {total} — 순서가 밀렸다, "
              f"다시 받는다 ({attempt}/{tries})")
        time.sleep(1.5 * attempt)
    raise RuntimeError(f"{label}: {tries}번 받아도 totalCount 와 안 맞는다")


def fetch_groups(kind: str, tries: int = 5) -> list[dict]:
    """업종(industry)·테마(theme) 목록 전체. [{no, name, totalCount, …}]

    목록도 등락률 순이라 장중엔 밀린다 — fetch_complete 와 같이 다시 받는다.
    """
    return list(_collect(kind, "groups", "no", f"{kind} 목록", tries, None).values())


def fetch_complete(path: str, label: str, tries: int = 5,
                   on_page: Callable[[dict], None] | None = None) -> dict[str, dict]:
    """`{BASE}/{path}` 를 끝까지 받아 code → 행.

    고유 종목 수가 totalCount 와 맞을 때까지 다시 받는다. 다시 받을 때 앞서
    받은 것을 버리지 않는다 — 밀린 종목은 다음 바퀴에 잡히니 합집합이 금방
    다 찬다. 몇 분 사이에 상장폐지되는 종목은 없다. on_page 는 받은 페이지
    응답을 그대로 넘겨받는다(테마 설명·편입 사유처럼 stocks 밖에 있는 것용).
    """
    return _collect(path, "stocks", "itemCode", label, tries, on_page)
