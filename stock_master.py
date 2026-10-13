"""
stock_master.py — 국내 상장 종목 마스터 (KOSPI·KOSDAQ 보통주·우선주)

테마·밸류체인 매핑의 바닥이다. 어떤 종목이 매핑 대상인지, 업종이 무엇인지,
얼마나 손을 들여 검수할지(Tier)를 여기서 정한다.

원본은 git 의 data/master/stocks.csv 다. 서버는 요청 시점에 외부를 부르지
않고 이 파일을 한 번 읽어 메모리에 둔다. 파일은 scripts/build_stock_master.py
가 GitHub Actions 에서 주 1회 만들고 PR 로 올린다.

대상
  - ETF·ETN 은 **처음부터 들어오지 않는다.** 네이버 시총 목록의 stockEndType
    이 'stock' 인 것만 받는다 — 이름 패턴(server.py ETF_PATTERNS)으로 거르면
    새 운용사 브랜드가 나올 때마다 빠진다(2026-09-23 보강, 'IBK 200' 은 아직 샌다).
  - 스팩은 뺀다. 합병 전엔 사업이 없고, 합병하면 사명이 바뀌어 다음 갱신 때
    보통주로 다시 들어온다.
  - 상장 펀드 클래스('… X클래스')는 뺀다. stockEndType 이 'stock' 으로 오지만
    업종이 '기타'(ETF 와 같은 칸)인 펀드다.
  - 리츠·인프라펀드는 넣되 업종만 붙인다 — 테마 대상이 아니다.

종목 유형
  common     보통주. 테마·밸류체인 매핑 대상.
  preferred  우선주. 본주(parent_code) 매핑을 물려받는다. 테마 수익률엔 안 넣는다.
  reit       리츠. 업종만.
  infra      인프라펀드(맥쿼리인프라 등). 업종만.

종목코드는 6자리 영숫자다. 2024년부터 '0126Z0'(삼성에피스홀딩스) 같은 코드가
나온다 — `\\d{6}` 으로 거르면 신규 상장이 통째로 빠진다. 여섯째 자리가 '0' 이면
보통주, 아니면(5·7·9·K·L…) 같은 앞 다섯 자리 보통주의 우선주다.
"""
from __future__ import annotations

import csv
import json
import re
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).parent
MASTER_DIR = BASE_DIR / "data" / "master"
STOCKS_CSV = MASTER_DIR / "stocks.csv"
META_JSON = MASTER_DIR / "meta.json"
INDUSTRY_GROUPS_JSON = MASTER_DIR / "industry_groups.json"
TYPE_OVERRIDES_CSV = MASTER_DIR / "type_overrides.csv"

COLUMNS = ["code", "name", "market", "type", "parent_code",
           "industry", "industry_group", "tier", "former_names"]

MARKETS = ("KOSPI", "KOSDAQ")
TYPES = ("common", "preferred", "reit", "infra")
THEME_TYPES = ("common",)            # 테마를 직접 붙이는 유형 (우선주는 본주 상속)

# Tier — 검수에 손을 얼마나 들일지. 보통주만 매긴다. 경계에서 매주 들락날락
# 하지 않도록 평소엔 기존 Tier 를 유지하고 분기 1회(--retier)만 다시 매긴다.
#   A ≥ 5,000억 : 사람이 검수 (시총 약 97%)
#   B ≥ 1,000억 : 자동 제안 + 표본 검수
#   C  < 1,000억 : 자동만
TIER_THRESHOLDS = (("A", 5_000e8), ("B", 1_000e8))
TIERS = ("A", "B", "C")

CODE_RE = re.compile(r"^[0-9A-Z]{6}$")
UNCLASSIFIED_GROUP = "unclassified"


def parent_of(code: str) -> str:
    """우선주의 본주 코드. 보통주면 빈 문자열."""
    return "" if code[5] == "0" else code[:5] + "0"


def tier_for(market_cap: float | None) -> str:
    cap = float(market_cap or 0)
    for tier, floor in TIER_THRESHOLDS:
        if cap >= floor:
            return tier
    return "C"


def exclusion_reason(name: str, end_type: str, industry: str) -> str | None:
    """마스터에 넣지 않을 종목이면 사유, 넣을 종목이면 None."""
    if end_type != "stock":
        return end_type or "unknown"          # etf · etn
    if "스팩" in name:
        return "spac"
    if name.endswith("X클래스") or industry == "기타":   # 상장 펀드 클래스
        return "fund_class"
    return None


def classify(code: str, name: str, industry: str, overrides: dict[str, str]) -> str:
    """마스터에 들어온 종목의 유형."""
    if code in overrides:
        return overrides[code]
    if parent_of(code):
        return "preferred"
    if industry == "부동산" and "리츠" in name:
        return "reit"
    return "common"


def load_industry_groups(path: Path = INDUSTRY_GROUPS_JSON) -> tuple[dict, dict]:
    """(업종 → 대분류 id, 대분류 id → 표시명)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    ind2grp, names = {}, {}
    for g in data["groups"]:
        names[g["id"]] = g["name"]
        for ind in g["industries"]:
            ind2grp[ind] = g["id"]
    names.setdefault(UNCLASSIFIED_GROUP, "미분류")
    return ind2grp, names


def load_type_overrides(path: Path = TYPE_OVERRIDES_CSV) -> dict[str, str]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        return {r["code"]: r["type"] for r in csv.DictReader(f)}


def read_rows(path: Path = STOCKS_CSV) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_rows(rows: list[dict], path: Path = STOCKS_CSV) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in sorted(rows, key=lambda r: r["code"]):
            w.writerow({k: r.get(k, "") for k in COLUMNS})
    tmp.replace(path)


@lru_cache(maxsize=1)
def load_master() -> dict[str, dict]:
    """code → 행. 프로세스당 한 번 읽는다. 파일이 없으면 빈 dict."""
    if not STOCKS_CSV.exists():
        return {}
    return {r["code"]: r for r in read_rows()}
