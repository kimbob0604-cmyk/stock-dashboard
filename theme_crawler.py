"""
theme_crawler.py  —  네이버 증권 테마 크롤러
https://finance.naver.com/sise/theme.naver 에서 테마-종목 매핑을 수집한다.

의존성: pip install requests beautifulsoup4
실행:   python theme_crawler.py  →  themes_mapping.json 생성

네이버 증권 페이지 구조 (2024~2025 기준)
  - 테마 목록 페이지: themeCode 파라미터가 있는 <a> 링크
  - 테마 상세 페이지: https://finance.naver.com/sise/themestock.naver?themeCode=XXX
    종목 테이블에 6자리 종목코드와 이름이 포함됨

페이지 구조가 변경됐을 경우 아래 SELECTORS 상수를 수정한다.
"""

import json
import re
import time
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    raise SystemExit("의존성 설치 필요: pip install requests beautifulsoup4")

BASE_URL     = "https://finance.naver.com"
THEME_LIST   = f"{BASE_URL}/sise/theme.naver"
THEME_DETAIL = f"{BASE_URL}/sise/themestock.naver"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": BASE_URL,
}

OUT_FILE = Path(__file__).parent / "themes_mapping.json"


# ─────────────────────────────────────────────────────────────────────────────
# HTTP 헬퍼
# ─────────────────────────────────────────────────────────────────────────────
def get_soup(url: str, params: dict | None = None, encoding: str = "euc-kr") -> BeautifulSoup | None:
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        resp.encoding = encoding
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"  ✗  요청 실패  {url}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 테마 목록 파싱
# ─────────────────────────────────────────────────────────────────────────────
def parse_theme_list(soup: BeautifulSoup) -> list[dict]:
    """themeCode 파라미터가 있는 링크를 모두 수집한다."""
    themes = []
    seen   = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        m    = re.search(r"themeCode=([A-Z0-9]+)", href)
        if not m:
            continue
        code = m.group(1)
        name = a.get_text(strip=True)
        if code in seen or not name or len(name) < 2:
            continue
        seen.add(code)
        themes.append({"name": name, "naver_code": code})

    return themes


# ─────────────────────────────────────────────────────────────────────────────
# 테마 상세 페이지에서 종목 파싱
# ─────────────────────────────────────────────────────────────────────────────
def parse_theme_stocks(soup: BeautifulSoup) -> list[dict]:
    """
    네이버 테마 상세 페이지 HTML 구조에서 종목코드와 이름을 추출한다.
    code= 파라미터가 포함된 링크를 모두 수집하므로 HTML 구조 변경에 어느 정도 강건하다.
    """
    stocks = []
    seen   = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        m    = re.search(r"code=([0-9]{6})", href)
        if not m:
            continue
        code = m.group(1)
        name = a.get_text(strip=True)
        if code in seen or not name or len(name) < 2:
            continue
        # 숫자만으로 이루어진 이름(테이블 번호 등) 제외
        if name.isdigit():
            continue
        seen.add(code)
        stocks.append({"code": code, "name": name})

    return stocks


# ─────────────────────────────────────────────────────────────────────────────
# 페이지 순회 (pagination 대응)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_all_theme_stocks(naver_code: str) -> list[dict]:
    """테마 상세 페이지를 순회하며 모든 종목을 수집한다."""
    all_stocks: list[dict] = []
    page = 1

    while True:
        soup = get_soup(THEME_DETAIL, params={"themeCode": naver_code, "page": page})
        if soup is None:
            break

        stocks = parse_theme_stocks(soup)
        if not stocks:
            break

        new = [s for s in stocks if s["code"] not in {x["code"] for x in all_stocks}]
        if not new:
            break

        all_stocks.extend(new)

        # 다음 페이지 링크 확인
        next_link = soup.find("a", string=re.compile(r"다음|Next|▶"))
        if not next_link:
            # 페이지 번호 방식 확인
            pager = soup.find("div", class_=re.compile("pager|pgRR"))
            if not pager:
                break
        page += 1
        time.sleep(0.3)

    return all_stocks


# ─────────────────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 52)
    print("  네이버 증권 테마 크롤링 시작")
    print("=" * 52)

    # 1. 테마 목록 조회
    print("\n[1/2] 테마 목록 조회…")
    soup = get_soup(THEME_LIST)
    if not soup:
        raise SystemExit("테마 목록 페이지 요청 실패")

    themes = parse_theme_list(soup)
    if not themes:
        print(
            "\n  ✗  테마 링크를 찾지 못했습니다.\n"
            "     네이버 HTML 구조가 변경됐을 수 있습니다.\n"
            "     브라우저로 확인 후 parse_theme_list()를 수정하세요.\n"
            "     또는 themes_mapping.json을 수동으로 작성하세요."
        )
        raise SystemExit(1)

    print(f"  테마 {len(themes)}개 발견")

    # 2. 각 테마의 종목 조회
    print("\n[2/2] 종목 수집 중…")
    mapping: list[dict] = []

    for i, theme in enumerate(themes, 1):
        print(f"  [{i:>3}/{len(themes)}] {theme['name']}")
        stocks = fetch_all_theme_stocks(theme["naver_code"])
        print(f"         → {len(stocks)}개")

        mapping.append({
            "id":          i,
            "name":        theme["name"],
            "naver_code":  theme["naver_code"],
            "stocks": [
                {"code": s["code"], "name": s["name"]}
                for s in stocks
            ],
        })
        time.sleep(0.3)

    # 종목이 없는 테마 제거
    mapping = [t for t in mapping if t["stocks"]]

    # 저장
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*52}")
    print(f"  완료  →  themes_mapping.json  (테마 {len(mapping)}개)")
    print(f"{'='*52}")


if __name__ == "__main__":
    main()
