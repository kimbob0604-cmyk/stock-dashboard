#!/usr/bin/env python3
"""
종목 마스터(data/master/stocks.csv) 생성 — KOSPI·KOSDAQ 보통주·우선주.

GitHub Actions(master-refresh.yml)가 주 1회 돌리고, 바뀐 것이 있으면 PR 을
연다. 손으로도 돌릴 수 있다:

    python3 scripts/build_stock_master.py               # 갱신
    python3 scripts/build_stock_master.py --dry-run     # 쓰지 않고 바뀔 내용만
    python3 scripts/build_stock_master.py --retier      # Tier 다시 매기기 (분기 1회)

소스는 네이버 모바일 JSON API 둘이다. 표준 라이브러리만 쓴다.
  1. 시총 목록   /api/stocks/marketValue/{KOSPI|KOSDAQ}  — 전 종목 + stockEndType
                 (stock/etf/etn) + 시가총액(marketValueRaw, 원)
  2. 업종 상세   /api/stocks/industry/{no}                — 종목 → 업종(79개)

목록은 100개씩 나눠 받고, 장중에 순서가 밀려 빠지는 종목은 다시 받아 채운다
(scripts/naver_mobile.py 참조).

**열화된 파일로 덮지 않는다.** 아래 중 하나라도 걸리면 쓰지 않고 실패로 끝난다
(--force 로 넘길 수 있다).
  - 종목 수가 기존보다 5% 넘게 변했다 — 수집이 덜 됐을 가능성이 크다
  - 업종을 못 붙인 종목이 1% 를 넘는다
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import stock_master as sm                                     # noqa: E402
from naver_mobile import fetch_complete, fetch_groups         # noqa: E402

KST = timezone(timedelta(hours=9))

MAX_COUNT_DRIFT = 0.05
MAX_NO_INDUSTRY = 0.01


def fetch_industries() -> dict[str, str]:
    """code → 네이버 업종 이름."""
    groups = fetch_groups("industry")
    if len(groups) < 70:
        raise RuntimeError(f"업종 목록이 {len(groups)}개뿐이다 (평소 79개)")
    out: dict[str, str] = {}
    for g in groups:
        if g.get("name") == "기타":       # ETF·ETN 1,500여 개뿐이다. 마스터엔 안 쓴다
            continue
        for code in fetch_complete(f"industry/{g['no']}", f"업종 {g.get('name')}"):
            out.setdefault(code, g["name"])
        time.sleep(0.1)
    return out


def build(markets: dict[str, dict], industry: dict[str, str], prev: dict[str, dict],
          ind2grp: dict, overrides: dict, retier: bool) -> tuple[list[dict], Counter]:
    rows: dict[str, dict] = {}
    excluded: Counter = Counter()
    caps: dict[str, float] = {}

    for market, items in markets.items():
        for code, r in items.items():
            name = (r.get("stockName") or "").strip()
            ind = industry.get(code, "")
            reason = sm.exclusion_reason(name, r.get("stockEndType") or "", ind)
            if reason:
                excluded[reason] += 1
                continue
            if not sm.CODE_RE.match(code):
                raise RuntimeError(f"종목코드 형식이 낯설다: {code!r} {name}")
            old = prev.get(code) or {}
            former = [n for n in (old.get("former_names") or "").split(";") if n]
            if old.get("name") and old["name"] != name and old["name"] not in former:
                former.append(old["name"])
            rows[code] = {
                "code": code,
                "name": name,
                "market": market,
                "type": sm.classify(code, name, ind, overrides),
                "parent_code": sm.parent_of(code),
                "industry": ind or old.get("industry", ""),
                "former_names": ";".join(former),
            }
            caps[code] = float(r.get("marketValueRaw") or 0)

    for code, row in rows.items():
        parent = rows.get(row["parent_code"]) if row["parent_code"] else None
        if row["type"] == "preferred" and not row["industry"] and parent:
            row["industry"] = parent["industry"]
        row["industry_group"] = ind2grp.get(row["industry"], sm.UNCLASSIFIED_GROUP)
        if row["type"] == "common":
            kept = (prev.get(code) or {}).get("tier")
            row["tier"] = kept if (kept and not retier) else sm.tier_for(caps.get(code))
        else:
            row["tier"] = ""
    return list(rows.values()), excluded


def diff_report(new: list[dict], prev: dict[str, dict], retier: bool) -> list[str]:
    cur = {r["code"]: r for r in new}
    if not prev:
        return ["첫 생성이다 — 비교할 기존 마스터가 없다."]
    out: list[str] = []

    def table(title, head, rows_):
        if not rows_:
            return
        out.append(f"\n### {title} ({len(rows_)})\n")
        out.append("| " + " | ".join(head) + " |")
        out.append("|" + "---|" * len(head))
        out.extend("| " + " | ".join(map(str, x)) + " |" for x in rows_[:200])
        if len(rows_) > 200:
            out.append(f"\n…외 {len(rows_) - 200}건")

    added = [cur[c] for c in sorted(cur.keys() - prev.keys())]
    gone = [prev[c] for c in sorted(prev.keys() - cur.keys())]
    both = sorted(cur.keys() & prev.keys())
    table("신규 편입", ["코드", "종목", "시장", "유형", "업종", "Tier"],
          [(r["code"], r["name"], r["market"], r["type"], r["industry"], r["tier"]) for r in added])
    table("빠짐 (상장폐지·스팩 등)", ["코드", "종목", "시장"],
          [(r["code"], r["name"], r["market"]) for r in gone])
    for key, title in (("name", "사명 변경"), ("market", "시장 이전"),
                       ("industry", "업종 변경"), ("type", "유형 변경"), ("tier", "Tier 변경")):
        if key == "tier" and not retier:
            continue
        table(title, ["코드", "종목", "이전", "이후"],
              [(c, cur[c]["name"], prev[c].get(key, ""), cur[c][key])
               for c in both if prev[c].get(key, "") != cur[c][key]])
    return out or ["바뀐 것이 없다."]


def summary(rows: list[dict], excluded: Counter, grp_names: dict) -> dict:
    return {
        "total": len(rows),
        "by_market": dict(Counter(r["market"] for r in rows)),
        "by_type": dict(Counter(r["type"] for r in rows)),
        "by_tier": dict(sorted(Counter(r["tier"] for r in rows if r["tier"]).items())),
        "by_group": {grp_names.get(k, k): v for k, v in
                     Counter(r["industry_group"] for r in rows).most_common()},
        "excluded": dict(excluded),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--dry-run", action="store_true", help="쓰지 않고 바뀔 내용만 보인다")
    ap.add_argument("--retier", action="store_true", help="보통주 Tier 를 지금 시총으로 다시 매긴다")
    ap.add_argument("--force", action="store_true", help="안전 검사에 걸려도 쓴다")
    ap.add_argument("--report", type=Path, help="바뀐 내용을 마크다운으로 저장 (PR 본문용)")
    args = ap.parse_args()

    ind2grp, grp_names = sm.load_industry_groups()
    overrides = sm.load_type_overrides()
    prev = {r["code"]: r for r in sm.read_rows()} if sm.STOCKS_CSV.exists() else {}
    prev_meta = json.loads(sm.META_JSON.read_text(encoding="utf-8")) if sm.META_JSON.exists() else {}

    t0 = time.time()
    print("▶ 시총 목록")
    markets = {m: fetch_complete(f"marketValue/{m}", m) for m in sm.MARKETS}
    for m, items in markets.items():
        print(f"  {m}: {len(items)}종목 {dict(Counter(r.get('stockEndType') for r in items.values()))}")
    print("▶ 업종")
    industry = fetch_industries()
    print(f"  업종이 붙은 종목 {len(industry)}")

    retier = args.retier or not prev
    rows, excluded = build(markets, industry, prev, ind2grp, overrides, retier)
    stats = summary(rows, excluded, grp_names)
    cap_asof = max((r.get("localTradedAt") or "")[:10]
                   for items in markets.values() for r in items.values())

    problems = []
    if prev and abs(len(rows) - len(prev)) > len(prev) * MAX_COUNT_DRIFT:
        problems.append(f"종목 수 {len(prev)} → {len(rows)} (허용 ±{MAX_COUNT_DRIFT:.0%})")
    no_ind = [r for r in rows if not r["industry"]]
    if len(no_ind) > len(rows) * MAX_NO_INDUSTRY:
        problems.append(f"업종 없는 종목 {len(no_ind)}개 (허용 {MAX_NO_INDUSTRY:.0%})")
    unmapped = sorted({r["industry"] for r in rows
                       if r["industry"] and r["industry_group"] == sm.UNCLASSIFIED_GROUP})

    lines = [f"## 종목 마스터 갱신 — {datetime.now(KST):%Y-%m-%d %H:%M} KST",
             "",
             f"- 대상 **{stats['total']}종목** · 시장 {stats['by_market']} · 유형 {stats['by_type']}",
             f"- 보통주 Tier {stats['by_tier']}" + (" (다시 매김)" if retier else " (기존 유지)"),
             f"- 제외 {stats['excluded']}",
             f"- 시총 기준일 {cap_asof} · 수집 {time.time() - t0:.0f}초"]
    if no_ind:
        lines.append(f"- ⚠️ 업종 없음 {len(no_ind)}: " + ", ".join(f"{r['code']} {r['name']}" for r in no_ind[:20]))
    if unmapped:
        lines.append(f"- ⚠️ 대분류에 없는 업종: {unmapped} — data/master/industry_groups.json 에 넣어야 한다")
    lines += diff_report(rows, prev, retier)
    if problems:
        lines += ["", "### ❌ 안전 검사 실패 — 쓰지 않았다"] + [f"- {p}" for p in problems]
    report = "\n".join(lines)
    print("\n" + report)
    if args.report:
        args.report.write_text(report + "\n", encoding="utf-8")

    if problems and not args.force:
        return 1
    if args.dry_run:
        print("\n(--dry-run — 쓰지 않았다)")
        return 0

    sm.write_rows(rows)
    meta = {
        "_note": "scripts/build_stock_master.py 가 만든다. 손으로 고치지 않는다 — stocks.csv 와 함께 다시 생성된다.",
        "built_at": datetime.now(KST).isoformat(timespec="seconds"),
        "source": "m.stock.naver.com marketValue + industry",
        "cap_asof": cap_asof,
        "retiered_at": cap_asof if retier else prev_meta.get("retiered_at"),
        "tier_thresholds_krw": {t: int(v) for t, v in sm.TIER_THRESHOLDS},
        **stats,
    }
    sm.META_JSON.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n✓ {sm.STOCKS_CSV.relative_to(sm.BASE_DIR)} {len(rows)}종목")
    return 0


if __name__ == "__main__":
    sys.exit(main())
