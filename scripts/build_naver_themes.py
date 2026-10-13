#!/usr/bin/env python3
"""
네이버 테마 원본 수집 — 테마 매핑의 1차 자동 태깅 재료.

    python3 scripts/build_naver_themes.py               # 갱신
    python3 scripts/build_naver_themes.py --dry-run     # 쓰지 않고 바뀔 내용만

만드는 것 (data/master/)
  naver_themes.json         테마 목록 — 번호·이름·설명·전체 종목 수·마스터 안 종목 수
  naver_theme_members.csv   테마 × 종목 — 편입 사유 글(themeItemInfoMap) 포함

네이버 테마(260여 개)는 **그대로 쓰지 않는다.** 우리 대테마로 옮기는 변환표를
거친다 — 같은 테마가 잘게 쪼개져 있고("HBM", "반도체 재료/부품", "CXL"…),
MOU·지분투자 수준의 종목도 섞여 있다. 편입 사유 글은 노출도(core/beneficiary/
side/speculative)를 가를 때 근거로 쓴다.

종목은 마스터(stocks.csv)에 있는 것만 남긴다 — ETF·ETN·스팩·코넥스는 빠진다.
그래서 마스터를 먼저 만든다(master-refresh.yml 이 그 순서로 돈다).

**열화된 파일로 덮지 않는다.** 테마가 200개 미만이거나 테마×종목 수가 기존보다
15% 넘게 변하면 쓰지 않고 실패로 끝난다(--force 로 넘길 수 있다).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import stock_master as sm                                     # noqa: E402
from naver_mobile import fetch_complete, fetch_groups         # noqa: E402

THEMES_JSON = sm.MASTER_DIR / "naver_themes.json"
MEMBERS_CSV = sm.MASTER_DIR / "naver_theme_members.csv"
MEMBER_COLS = ["theme_no", "code", "reason"]

MIN_THEMES = 200
MAX_MEMBER_DRIFT = 0.15


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def fetch(master: dict) -> tuple[list[dict], list[dict]]:
    groups = fetch_groups("theme")
    if len(groups) < MIN_THEMES:
        raise RuntimeError(f"테마 목록이 {len(groups)}개뿐이다 (평소 260여 개)")
    themes, members = [], []
    for i, g in enumerate(sorted(groups, key=lambda g: int(g["no"])), 1):
        extra = {"desc": "", "reasons": {}}

        def on_page(d, extra=extra):
            extra["desc"] = extra["desc"] or clean(d.get("themeDescription"))
            extra["reasons"].update(d.get("themeItemInfoMap") or {})

        rows = fetch_complete(f"theme/{g['no']}", f"테마 {g.get('name')}", on_page=on_page)
        kept = sorted(c for c in rows if c in master)
        themes.append({"no": int(g["no"]), "name": clean(g.get("name")),
                       "description": extra["desc"],
                       "count": len(rows), "in_master": len(kept)})
        members += [{"theme_no": int(g["no"]), "code": c,
                     "reason": clean(extra["reasons"].get(c))} for c in kept]
        if i % 50 == 0:
            print(f"  … {i}/{len(groups)} 테마 (종목 {len(members)})")
        time.sleep(0.1)
    return themes, members


def read_prev() -> tuple[dict, list[dict]]:
    themes = {}
    if THEMES_JSON.exists():
        themes = {t["no"]: t for t in json.loads(THEMES_JSON.read_text(encoding="utf-8"))["themes"]}
    members = []
    if MEMBERS_CSV.exists():
        with MEMBERS_CSV.open(encoding="utf-8", newline="") as f:
            members = [{**r, "theme_no": int(r["theme_no"])} for r in csv.DictReader(f)]
    return themes, members


def report(themes, members, prev_themes, prev_members) -> list[str]:
    out = [f"- 테마 **{len(themes)}개** · 테마×종목 **{len(members)}건** "
           f"(종목 {len({m['code'] for m in members})}개)"]
    if not prev_themes:
        return out + ["- 첫 수집이다."]
    cur = {t["no"]: t for t in themes}
    added = [cur[n]["name"] for n in sorted(cur.keys() - prev_themes.keys())]
    gone = [prev_themes[n]["name"] for n in sorted(prev_themes.keys() - cur.keys())]
    renamed = [f"{prev_themes[n]['name']} → {cur[n]['name']}"
               for n in sorted(cur.keys() & prev_themes.keys())
               if prev_themes[n]["name"] != cur[n]["name"]]
    pairs = {(m["theme_no"], m["code"]) for m in members}
    prev_pairs = {(m["theme_no"], m["code"]) for m in prev_members}
    names = sm.load_master()
    for title, items in (("새 테마", added), ("없어진 테마", gone), ("이름 바뀐 테마", renamed)):
        if items:
            out.append(f"- {title} {len(items)}: " + ", ".join(items))
    for title, diff in (("편입", pairs - prev_pairs), ("편출", prev_pairs - pairs)):
        if not diff:
            continue
        out.append(f"\n### 테마 {title} ({len(diff)})\n")
        out.append("| 테마 | 종목 |")
        out.append("|---|---|")
        tname = {**{n: t["name"] for n, t in prev_themes.items()}, **{n: t["name"] for n, t in cur.items()}}
        for no, code in sorted(diff)[:300]:
            out.append(f"| {tname.get(no, no)} | {code} {(names.get(code) or {}).get('name', '')} |")
        if len(diff) > 300:
            out.append(f"\n…외 {len(diff) - 300}건")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="네이버 테마 원본 수집")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="안전 검사에 걸려도 쓴다")
    ap.add_argument("--report", type=Path, help="바뀐 내용을 마크다운으로 덧붙여 저장")
    args = ap.parse_args()

    master = sm.load_master()
    if not master:
        print("stocks.csv 가 없다 — build_stock_master.py 를 먼저 돌린다")
        return 1
    prev_themes, prev_members = read_prev()

    t0 = time.time()
    print("▶ 네이버 테마")
    themes, members = fetch(master)

    problems = []
    if prev_members and abs(len(members) - len(prev_members)) > len(prev_members) * MAX_MEMBER_DRIFT:
        problems.append(f"테마×종목 {len(prev_members)} → {len(members)} (허용 ±{MAX_MEMBER_DRIFT:.0%})")
    lines = ["", f"## 네이버 테마 — 수집 {time.time() - t0:.0f}초", ""]
    lines += report(themes, members, prev_themes, prev_members)
    if problems:
        lines += ["", "### ❌ 안전 검사 실패 — 쓰지 않았다"] + [f"- {p}" for p in problems]
    text = "\n".join(lines)
    print(text)
    if args.report:
        with args.report.open("a", encoding="utf-8") as f:
            f.write(text + "\n")

    if problems and not args.force:
        return 1
    if args.dry_run:
        print("\n(--dry-run — 쓰지 않았다)")
        return 0

    THEMES_JSON.write_text(json.dumps({
        "_note": "scripts/build_naver_themes.py 가 만든다. 손으로 고치지 않는다. "
                 "count 는 네이버 테마 전체 종목 수(ETF·코넥스 포함), in_master 는 마스터 안 종목 수.",
        "themes": themes,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp = MEMBERS_CSV.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MEMBER_COLS, lineterminator="\n")
        w.writeheader()
        w.writerows(sorted(members, key=lambda m: (m["theme_no"], m["code"])))
    tmp.replace(MEMBERS_CSV)
    print(f"\n✓ 테마 {len(themes)}개 · 테마×종목 {len(members)}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
