"""
규칙 기반 주식 추천 에이전트 파이프라인
5개 에이전트가 순차적으로 실행되어 최종 추천 종목을 산출
비용: $0 (LLM 없이 기존 대시보드 데이터만 활용)
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
import urllib.parse
from datetime import datetime
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent

# .env 로드 (CLI 직접 실행 대응)
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        if "=" in _line and not _line.startswith("#"):
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# ============================================================
# 테마 키워드 사전
# ============================================================
THEME_KEYWORDS = {
    "반도체": ["반도체", "HBM", "DRAM", "낸드", "파운드리", "메모리", "비메모리", "EUV", "웨이퍼"],
    "2차전지": ["2차전지", "배터리", "양극재", "음극재", "전해질", "분리막", "전고체"],
    "원전": ["원전", "SMR", "소형모듈원자로", "원자력", "핵융합", "우라늄"],
    "AI": ["AI", "인공지능", "GPT", "LLM", "데이터센터", "클라우드", "GPU"],
    "광통신": ["광통신", "광반도체", "CPO", "실리콘포토닉스", "광모듈", "광케이블"],
    "양자컴퓨터": ["양자", "양자컴퓨터", "양자암호", "큐비트"],
    "방산": ["방산", "무기", "미사일", "전투기", "방위산업", "K방산"],
    "조선": ["조선", "LNG선", "컨테이너선", "해운", "벌크선"],
    "로봇": ["로봇", "자율주행", "휴머노이드", "산업용로봇"],
    "바이오": ["바이오", "신약", "임상", "FDA", "항암제", "GLP-1", "비만치료제"],
    "우주": ["우주", "위성", "저궤도", "발사체", "스페이스"],
    "스테이블코인": ["스테이블코인", "디지털자산", "가상자산", "CBDC", "블록체인"],
    "전기차": ["전기차", "EV", "충전", "테슬라"],
    "건설": ["건설", "SOC", "인프라", "아파트", "부동산"],
    "금융": ["금융", "은행", "증권", "보험", "금리"],
    "엔터": ["엔터", "K팝", "드라마", "OTT", "콘텐츠"],
}

KEYWORD_TO_THEME = {}
for _theme, _keywords in THEME_KEYWORDS.items():
    for _kw in _keywords:
        KEYWORD_TO_THEME[_kw] = _theme


# ============================================================
# Agent 1: 뉴스 수집 + 키워드 추출
# ============================================================
def agent1_news():
    """오늘 주요 뉴스에서 테마 키워드 빈도 추출"""
    print("[Agent 1] 뉴스 키워드 추출 시작")

    results = {
        "agent": "news_collector",
        "timestamp": datetime.now().isoformat(),
        "theme_counts": {},
        "top_themes": [],
        "headlines": [],
        "total_articles": 0,
    }

    naver_id = os.getenv("NAVER_CLIENT_ID")
    naver_secret = os.getenv("NAVER_CLIENT_SECRET")
    if not naver_id or not naver_secret:
        print("[Agent 1] 네이버 API 키 없음")
        return results

    queries = ["증시", "주식 급등", "코스피", "반도체", "2차전지", "AI"]
    all_titles: list[str] = []

    for query in queries:
        try:
            params = urllib.parse.urlencode({"query": query, "display": 20, "sort": "date"})
            url = f"https://openapi.naver.com/v1/search/news.json?{params}"
            req = urllib.request.Request(url, headers={
                "X-Naver-Client-Id": naver_id,
                "X-Naver-Client-Secret": naver_secret,
            })
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for item in data.get("items", []):
                title = re.sub(r"<[^>]+>", "", item.get("title", ""))
                all_titles.append(title)
        except Exception:
            continue

    theme_counts: Counter = Counter()
    for title in all_titles:
        for keyword, theme in KEYWORD_TO_THEME.items():
            if keyword in title:
                theme_counts[theme] += 1

    top_themes = [t for t, c in theme_counts.most_common(5) if c >= 2]

    results["theme_counts"] = dict(theme_counts.most_common(10))
    results["top_themes"] = top_themes
    results["headlines"] = all_titles[:20]
    results["total_articles"] = len(all_titles)

    print(f"[Agent 1] 뉴스 {len(all_titles)}건, 핫 테마: {top_themes}")
    return results


# ============================================================
# Agent 2: 매크로 판단
# ============================================================
def agent2_macro():
    """매크로 지표 기반 시장 방향 판단"""
    print("[Agent 2] 매크로 분석 시작")

    results = {
        "agent": "macro_judge",
        "timestamp": datetime.now().isoformat(),
        "direction": "neutral",
        "confidence": 0.5,
        "factors": [],
        "scores": {"bullish": 0, "bearish": 0},
    }

    macro_path = BASE_DIR / "cache" / "macro_data.json"
    if not macro_path.exists():
        print("[Agent 2] 매크로 캐시 없음")
        return results

    try:
        macro = json.loads(macro_path.read_text(encoding="utf-8"))
    except Exception:
        return results

    bullish = 0
    bearish = 0
    factors = []

    for item in macro.get("items", []):
        name = item.get("name", "")
        pct = item.get("change_pct", 0) or 0
        value = item.get("value", 0) or 0

        if "VIX" in name:
            if value < 15:
                bullish += 2; factors.append(f"VIX {value:.1f} (극도 안정)")
            elif value < 20:
                bullish += 1; factors.append(f"VIX {value:.1f} (안정)")
            elif value > 30:
                bearish += 2; factors.append(f"VIX {value:.1f} (공포)")
            elif value > 25:
                bearish += 1; factors.append(f"VIX {value:.1f} (불안)")

        if "USD/KRW" in name:
            if pct < -0.5:
                bullish += 1; factors.append(f"원화 강세 ({pct:+.2f}%) → 외국인 유입 기대")
            elif pct > 0.5:
                bearish += 1; factors.append(f"원화 약세 ({pct:+.2f}%) → 외국인 이탈 우려")

        if "WTI" in name or "원유" in name:
            if pct > 3:
                bearish += 1; factors.append(f"유가 급등 ({pct:+.2f}%) → 인플레 우려")
            elif pct < -3:
                bullish += 1; factors.append(f"유가 급락 ({pct:+.2f}%) → 비용 완화")

        if "10년" in name:
            if pct > 2:
                bearish += 1; factors.append(f"금리 상승 ({pct:+.2f}%) → 성장주 약세")
            elif pct < -2:
                bullish += 1; factors.append(f"금리 하락 ({pct:+.2f}%) → 성장주 강세")

    net = bullish - bearish
    if net >= 3:
        direction, confidence = "strong_bull", 0.9
    elif net >= 1:
        direction, confidence = "mild_bull", 0.7
    elif net <= -3:
        direction, confidence = "strong_bear", 0.9
    elif net <= -1:
        direction, confidence = "mild_bear", 0.7
    else:
        direction, confidence = "neutral", 0.5

    results["direction"] = direction
    results["confidence"] = confidence
    results["factors"] = factors
    results["scores"] = {"bullish": bullish, "bearish": bearish}

    print(f"[Agent 2] 매크로: {direction} (신뢰도 {confidence})")
    return results


# ============================================================
# 동적 테마 → 종목 매핑 (DB 기반, 6시간 캐시)
# ============================================================

_KR_THEME_TO_SECTOR = {
    "반도체":      ["반도체", "반도체와반도체장비", "전자부품", "디스플레이"],
    "AI":          ["소프트웨어", "IT서비스", "반도체"],
    "광통신":      ["통신장비", "전자부품", "광"],
    "원전":        ["전기장비", "유틸리티", "에너지"],
    "2차전지":     ["2차전지", "화학", "전기장비"],
    "조선":        ["조선", "해운", "기계"],
    "방산":        ["항공우주", "방산", "기계"],
    "바이오":      ["제약", "바이오", "헬스케어", "의료"],
    "로봇":        ["로봇", "기계", "자동화"],
    "전기차":      ["자동차", "자동차부품"],
    "금융":        ["은행", "증권", "보험", "금융"],
    "엔터":        ["미디어", "엔터", "게임", "콘텐츠"],
    "건설":        ["건설", "건축"],
    "스테이블코인": ["금융", "IT서비스"],
    "우주":        ["항공우주"],
    "양자컴퓨터":   ["소프트웨어", "반도체"],
}

_US_THEME_TO_GICS = {
    "AI/Cloud":      ["Information Technology", "Communication Services"],
    "Semiconductor": ["Information Technology"],
    "EV/Battery":    ["Consumer Discretionary", "Industrials"],
    "Biotech":       ["Health Care"],
    "FinTech":       ["Financials", "Information Technology"],
    "Defense":       ["Industrials"],
    "Energy":        ["Energy", "Utilities"],
    "Cybersecurity": ["Information Technology"],
    "Space":         ["Industrials"],
    "Robotics":      ["Industrials", "Information Technology"],
}


def _get_kr_theme_stocks() -> dict:
    """DB에서 KR 섹터별 시총 상위 15종목 실시간 추출."""
    result: dict = {}
    try:
        from db.database import get_db
        with get_db() as conn:
            for theme, sectors in _KR_THEME_TO_SECTOR.items():
                conds = " OR ".join([f"sector LIKE ?" for _ in sectors])
                params = [f"%{s}%" for s in sectors]
                rows = conn.execute(f"""
                    SELECT code FROM stocks
                    WHERE (market = '' OR market LIKE 'KOS%')
                      AND COALESCE(is_etf, 0) = 0
                      AND ({conds})
                      AND close > 0
                    ORDER BY COALESCE(market_cap, 0) DESC, volume_mn DESC
                    LIMIT 15
                """, params).fetchall()
                codes = [r["code"] for r in rows]
                if codes:
                    result[theme] = codes
    except Exception as exc:
        print(f"[테마 동적] KR 조회 실패: {exc}")
    return result


def _get_us_theme_stocks() -> dict:
    """DB에서 US GICS 섹터별 상위 15종목 실시간 추출 (market_cap 없으면 code 알파벳순)."""
    result: dict = {}
    try:
        from db.database import get_db
        with get_db() as conn:
            for theme, sectors in _US_THEME_TO_GICS.items():
                placeholders = ",".join(["?" for _ in sectors])
                rows = conn.execute(f"""
                    SELECT code FROM stocks
                    WHERE (market = 'US' OR market LIKE 'NASD%' OR market LIKE 'NYS%' OR market LIKE 'AMEX%')
                      AND sector IN ({placeholders})
                    ORDER BY COALESCE(market_cap, 0) DESC, code ASC
                    LIMIT 15
                """, sectors).fetchall()
                codes = [r["code"] for r in rows]
                if codes:
                    result[theme] = codes
    except Exception as exc:
        print(f"[테마 동적] US 조회 실패: {exc}")
    return result


# 6시간 캐시
_kr_theme_cache: dict = {}
_kr_theme_ts: float = 0.0
_us_theme_cache: dict = {}
_us_theme_ts: float = 0.0
_THEME_CACHE_TTL = 6 * 3600


def get_kr_theme_stocks(theme: str) -> list[str]:
    """KR 테마 종목 조회 — DB 동적 우선, themes_mapping.json 폴백."""
    global _kr_theme_cache, _kr_theme_ts
    import time as _t
    if _t.time() - _kr_theme_ts > _THEME_CACHE_TTL:
        _kr_theme_cache = _get_kr_theme_stocks()
        _kr_theme_ts = _t.time()
        print(f"[테마 동적] KR {len(_kr_theme_cache)}개 테마 로드 "
              f"({sum(len(v) for v in _kr_theme_cache.values())}종목)")
    return _kr_theme_cache.get(theme, [])


def get_us_theme_stocks(theme: str) -> list[str]:
    """US 테마 종목 조회 — DB 동적."""
    global _us_theme_cache, _us_theme_ts
    import time as _t
    if _t.time() - _us_theme_ts > _THEME_CACHE_TTL:
        _us_theme_cache = _get_us_theme_stocks()
        _us_theme_ts = _t.time()
        print(f"[테마 동적] US {len(_us_theme_cache)}개 테마 로드 "
              f"({sum(len(v) for v in _us_theme_cache.values())}종목)")
    return _us_theme_cache.get(theme, [])


# ============================================================
# Agent 3: 섹터/종목 매칭
# ============================================================
def agent3_match(news_result, macro_result):
    """뉴스 핫 테마 → 해당 테마 종목 추출"""
    print("[Agent 3] 섹터/종목 매칭 시작")

    results = {
        "agent": "sector_matcher",
        "timestamp": datetime.now().isoformat(),
        "candidates": [],
        "matched_themes": [],
        "source": None,
    }

    hot_themes = news_result.get("top_themes", [])
    disc_path = BASE_DIR / "cache" / "discover_kr_stage2.json"

    if not disc_path.exists():
        print("[Agent 3] Stage 2 결과 없음")
        return results

    try:
        disc = json.loads(disc_path.read_text(encoding="utf-8"))
    except Exception:
        return results

    all_items = disc.get("items", [])

    if not hot_themes:
        print("[Agent 3] 핫 테마 없음 → 종목 발굴 상위 30")
        results["candidates"] = all_items[:30]
        results["source"] = "discover_fallback"
        return results

    # 테마 → 종목 매핑
    matched_codes: set = set()
    matched_themes = []

    # 1순위: DB 동적 매핑
    dynamic_hits = 0
    for theme in hot_themes:
        dyn_codes = get_kr_theme_stocks(theme)
        if dyn_codes:
            matched_codes.update(dyn_codes)
            matched_themes.append(f"{theme}(dynamic)")
            dynamic_hits += 1

    # 2순위: themes_mapping.json 큐레이티드 (동적에 없는 테마 보완)
    themes_path = BASE_DIR / "themes_mapping.json"
    themes_map: object = {}
    if themes_path.exists():
        try:
            themes_map = json.loads(themes_path.read_text(encoding="utf-8"))
        except Exception:
            themes_map = {}

    if themes_map:
        theme_iter = themes_map.items() if isinstance(themes_map, dict) else \
                     [(t.get("name", ""), t) for t in themes_map if isinstance(t, dict)]
        for theme in hot_themes:
            # 이미 동적에서 찾은 테마면 스킵
            if get_kr_theme_stocks(theme):
                continue
            for t_name, t_data in theme_iter:
                if theme in t_name or t_name in theme:
                    codes = t_data if isinstance(t_data, list) else t_data.get("stocks", [])
                    for code_item in codes:
                        code = code_item if isinstance(code_item, str) else code_item.get("code", "")
                        if code:
                            matched_codes.add(code)
                    matched_themes.append(f"{t_name}(curated)")

    # 3순위: 둘 다 실패 시 섹터 기반 자동 생성
    if not matched_codes:
        sector_groups: dict = {}
        for it in all_items:
            sect = (it.get("sector") or "").strip()
            if not sect:
                continue
            sector_groups.setdefault(sect, []).append(it.get("code"))
        for theme in hot_themes:
            for sect, codes in sector_groups.items():
                if theme in sect or sect in theme:
                    matched_codes.update(codes[:15])
                    matched_themes.append(f"{sect}(auto)")

    print(f"[Agent 3] 동적 매칭: {dynamic_hits}개 테마 / 총 {len(matched_codes)}종목")

    # 테마 매칭 종목
    theme_items = [i for i in all_items if i.get("code") in matched_codes]

    # 섹터명에 테마 키워드 포함 종목도 추가
    for item in all_items:
        sect = item.get("sector", "") or ""
        if any(theme in sect for theme in hot_themes):
            if item["code"] not in matched_codes:
                theme_items.append(item)
                matched_codes.add(item["code"])

    theme_items.sort(key=lambda x: -(x.get("total_score", 0) or 0))
    results["candidates"] = theme_items[:50]
    results["matched_themes"] = matched_themes
    results["matched_codes_count"] = len(matched_codes)
    results["source"] = "theme_match"

    if macro_result.get("direction", "").endswith("bear"):
        results["macro_warning"] = "매크로 약세 → 방어주 비중 확대 권장"

    print(f"[Agent 3] 핫 테마 {hot_themes} → {len(results['candidates'])}종목")
    return results


# ============================================================
# Agent 4: 기술적 분석 필터
# ============================================================
def agent4_technical(match_result):
    """기술적 분석으로 진입 적합 종목만 필터"""
    print("[Agent 4] 기술적 분석 시작")

    results = {
        "agent": "technical_filter",
        "timestamp": datetime.now().isoformat(),
        "passed": [],
        "rejected": [],
        "stats": {},
    }

    candidates = match_result.get("candidates", [])
    if not candidates:
        return results

    for item in candidates:
        details = item.get("details") or {}
        rsi_macd_tags = details.get("rsi_macd_tags") or []

        tech_score = 0
        tech_reasons = []

        # RSI 체크
        rsi = details.get("rsi")
        if rsi and 30 <= rsi <= 50:
            tech_score += 1
            tech_reasons.append(f"RSI {rsi:.0f} 과매도 회복")
        elif rsi and rsi < 30:
            tech_score += 0.5
            tech_reasons.append(f"RSI {rsi:.0f} 과매도")

        # MACD 강세
        if any("강세" in t or "골든" in t or "양전환" in t or "상승강화" in t
               for t in rsi_macd_tags):
            tech_score += 1
            tech_reasons.append("MACD 강세")

        # 불리시 다이버전스
        if any("불리시" in t for t in rsi_macd_tags):
            tech_score += 1
            tech_reasons.append("불리시 다이버전스")

        # 추세
        trend = details.get("trend_signal", "") or ""
        if "지지선 위" in trend or "저항선 돌파" in trend:
            tech_score += 1
            tech_reasons.append(f"추세: {trend}")

        # 거래량
        vol_sig = details.get("vol_signal", "") or ""
        if "급증" in vol_sig:
            tech_score += 1
            tech_reasons.append("거래량 급증")

        # 기술 점수 (스코어링 모듈 결과)
        scores = item.get("scores") or {}
        tech_module = scores.get("technical", 0) or 0
        if tech_module >= 10:
            tech_score += 1
            tech_reasons.append(f"기술 점수 {tech_module}/15")

        item["_tech_score"] = tech_score
        item["_tech_reasons"] = tech_reasons

        # 통과 기준: 3점 이상 (복수 신호 확보 종목만)
        if tech_score >= 3:
            results["passed"].append(item)
        else:
            results["rejected"].append({
                "code": item.get("code"),
                "name": item.get("name"),
                "score": tech_score,
            })

    results["stats"] = {
        "total": len(candidates),
        "passed": len(results["passed"]),
        "rejected": len(results["rejected"]),
        "pass_rate": f"{len(results['passed'])/max(len(candidates),1)*100:.0f}%",
    }

    print(f"[Agent 4] {len(candidates)} → {len(results['passed'])} 통과 ({results['stats']['pass_rate']})")
    return results


# ============================================================
# Agent 5: 펀더멘탈 필터
# ============================================================
def agent5_fundamental(tech_result):
    """펀더멘탈 분석으로 최종 추천 종목 선별"""
    print("[Agent 5] 펀더멘탈 분석 시작")

    results = {
        "agent": "fundamental_filter",
        "timestamp": datetime.now().isoformat(),
        "final_picks": [],
        "stats": {},
    }

    passed = tech_result.get("passed", [])
    if not passed:
        return results

    for item in passed:
        details = item.get("details") or {}
        scores = item.get("scores") or {}

        per = details.get("per") or 0
        pbr = details.get("pbr") or 0
        industry_per = details.get("industry_per") or 0

        fund_score = 0
        fund_reasons = []

        if per and per > 0:
            if industry_per and per < industry_per * 1.5:
                fund_score += 1
                fund_reasons.append(f"PER {per:.1f} (업종 {industry_per:.1f})")
            elif per < 20:
                fund_score += 1
                fund_reasons.append(f"PER {per:.1f} (적정)")

        if pbr and 0 < pbr < 3:
            fund_score += 1
            fund_reasons.append(f"PBR {pbr:.2f}")

        val_score = scores.get("valuation", 0) or 0
        if val_score >= 10:
            fund_score += 1
            fund_reasons.append(f"밸류 점수 {val_score}/20")

        flow_score = scores.get("flow", 0) or 0
        if flow_score >= 15:
            fund_score += 1
            fund_reasons.append(f"수급 점수 {flow_score}/30")

        # 외국인 수급 태그
        foreign_tags = [t for t in (details.get("rsi_macd_tags") or [])
                        if "외국인" in t or "쌍끌이" in t]
        if foreign_tags:
            fund_score += 1
            fund_reasons.append(f"{foreign_tags[0]}")

        if fund_score >= 1:
            item["_fund_score"] = fund_score
            item["_fund_reasons"] = fund_reasons
            item["_total_agent_score"] = (
                (item.get("_tech_score") or 0) + fund_score
            )
            results["final_picks"].append(item)

    # 정렬: 에이전트 종합 점수 + 기본 total_score
    results["final_picks"].sort(
        key=lambda x: -((x.get("_total_agent_score") or 0) * 10 + (x.get("total_score") or 0))
    )
    results["final_picks"] = results["final_picks"][:10]

    results["stats"] = {"input": len(passed), "output": len(results["final_picks"])}
    print(f"[Agent 5] {len(passed)} → 최종 {len(results['final_picks'])}종목")
    return results


# ============================================================
# US 전용 에이전트 (1~5)
# ============================================================

US_SECTOR_KEYWORDS = {
    "AI/Cloud": ["AI", "artificial intelligence", "GPT", "cloud", "data center",
                 "GPU", "NVIDIA", "Microsoft", "Azure", "AWS", "OpenAI"],
    "Semiconductor": ["semiconductor", "chip", "HBM", "TSMC", "Intel", "AMD",
                      "foundry", "EUV", "wafer"],
    "EV/Battery": ["electric vehicle", "EV", "Tesla", "battery", "lithium",
                   "charging"],
    "Biotech": ["biotech", "FDA", "clinical trial", "drug", "GLP-1", "obesity",
                "Eli Lilly", "Novo Nordisk", "weight loss"],
    "FinTech": ["fintech", "payment", "crypto", "blockchain", "stablecoin",
                "digital asset", "Bitcoin"],
    "Defense": ["defense", "military", "Lockheed", "Raytheon", "NATO"],
    "Energy": ["oil", "gas", "renewable", "solar", "wind", "nuclear", "uranium",
               "SMR"],
    "Cybersecurity": ["cybersecurity", "hacking", "CrowdStrike", "Palo Alto",
                      "ransomware"],
    "Space": ["space", "satellite", "SpaceX", "launch", "orbit"],
    "Robotics": ["robot", "humanoid", "automation", "autonomous"],
}

US_KEYWORD_TO_SECTOR = {}
for _sector, _kws in US_SECTOR_KEYWORDS.items():
    for _kw in _kws:
        US_KEYWORD_TO_SECTOR[_kw.lower()] = _sector


def agent1_us_news():
    """Finnhub general 뉴스에서 US 핫 섹터 추출."""
    print("[US Agent 1] 뉴스 수집 시작")

    results = {
        "agent": "us_news_collector",
        "timestamp": datetime.now().isoformat(),
        "sector_counts": {},
        "top_sectors": [],
        "headlines": [],
        "total_articles": 0,
    }

    finnhub_key = os.getenv("FINNHUB_API_KEY")
    if not finnhub_key:
        print("[US Agent 1] FINNHUB_API_KEY 없음")
        return results

    try:
        url = f"https://finnhub.io/api/v1/news?category=general&token={finnhub_key}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            news = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"[US Agent 1] 에러: {exc}")
        return results

    headlines = [n.get("headline", "") for n in news[:60] if isinstance(n, dict)]
    results["headlines"] = headlines[:20]
    results["total_articles"] = len(headlines)

    sector_counts: Counter = Counter()
    for h in headlines:
        hl = h.lower()
        for kw, sect in US_KEYWORD_TO_SECTOR.items():
            if kw in hl:
                sector_counts[sect] += 1

    top = [s for s, c in sector_counts.most_common(5) if c >= 2]
    results["sector_counts"] = dict(sector_counts.most_common(10))
    results["top_sectors"] = top

    print(f"[US Agent 1] 뉴스 {len(headlines)}건, 핫 섹터: {top}")
    return results


def agent3_us_match(news_result, macro_result):
    """US 핫 섹터 → S&P 종목 추출."""
    print("[US Agent 3] 섹터 매칭")

    results = {
        "agent": "us_sector_matcher",
        "timestamp": datetime.now().isoformat(),
        "candidates": [],
        "matched_sectors": [],
    }

    hot_sectors = news_result.get("top_sectors", [])

    # 섹터 키워드 → GICS 섹터
    gics_map = {
        "AI/Cloud": "Information Technology",
        "Semiconductor": "Information Technology",
        "EV/Battery": "Consumer Discretionary",
        "Biotech": "Health Care",
        "FinTech": "Financials",
        "Defense": "Industrials",
        "Energy": "Energy",
        "Cybersecurity": "Information Technology",
        "Space": "Industrials",
        "Robotics": "Industrials",
    }

    target_gics: set = set()
    for hot in hot_sectors:
        g = gics_map.get(hot)
        if g:
            target_gics.add(g)

    disc_path = BASE_DIR / "cache" / "discover_us_stage2.json"
    all_items: list = []
    if disc_path.exists():
        try:
            disc = json.loads(disc_path.read_text(encoding="utf-8"))
            all_items = disc.get("items", [])
        except Exception:
            all_items = []

    if not all_items:
        # Fallback: stocks 테이블 대형주 30종목
        try:
            from db.database import get_db
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT code, name, market, sector, market_cap, close as price, "
                    "change_pct FROM stocks WHERE market IN ('US','NASDAQ','NYSE','AMEX') "
                    "ORDER BY market_cap DESC LIMIT 30"
                ).fetchall()
            all_items = [dict(r) for r in rows]
            results["source"] = "db_fallback"
        except Exception as exc:
            print(f"[US Agent 3] DB 폴백 실패: {exc}")

    # 1순위: DB 동적 매칭 (테마명 직접)
    dynamic_codes: set = set()
    for hot in hot_sectors:
        dyn = get_us_theme_stocks(hot)
        if dyn:
            dynamic_codes.update(dyn)

    # 동적 매칭 + GICS 섹터 매칭 합집합
    matched_set: set = set()
    matched: list = []
    for i in all_items:
        code = i.get("code")
        if code in dynamic_codes or (target_gics and i.get("sector") in target_gics):
            if code not in matched_set:
                matched.append(i)
                matched_set.add(code)

    # 폴백: 전체 상위
    if not matched:
        matched = all_items[:50]

    matched.sort(key=lambda x: -(x.get("total_score") or 0))
    results["candidates"] = matched[:50]
    results["matched_sectors"] = list(target_gics)
    results["dynamic_codes_count"] = len(dynamic_codes)

    print(f"[US Agent 3] 핫 섹터 {hot_sectors} → 동적 {len(dynamic_codes)}종목, 최종 {len(results['candidates'])}종목")
    return results


def agent5_us_fundamental(tech_result):
    """US 펀더멘탈 — PER/시총 중심 (PBR 덜 중요)."""
    print("[US Agent 5] 밸류 분석")

    results = {
        "agent": "us_fundamental_filter",
        "timestamp": datetime.now().isoformat(),
        "final_picks": [],
        "stats": {},
    }

    passed = tech_result.get("passed", [])
    if not passed:
        return results

    for item in passed:
        details = item.get("details") or {}
        scores = item.get("scores") or {}

        per = details.get("per") or item.get("per") or 0
        market_cap = item.get("market_cap") or 0

        fund_score = 0
        fund_reasons = []

        # PER (미국은 30 이하 합리적, 50 이하는 성장주)
        if per and 0 < per < 30:
            fund_score += 1
            fund_reasons.append(f"PER {per:.1f}")
        elif per and 0 < per < 50:
            fund_score += 0.5
            fund_reasons.append(f"PER {per:.1f} (성장주)")

        # 시가총액 (대형주 선호)
        if market_cap and market_cap >= 10_000_000_000:
            fund_score += 1
            fund_reasons.append("대형주 $10B+")
        elif market_cap and market_cap >= 1_000_000_000:
            fund_score += 0.5
            fund_reasons.append("중형주 $1B+")

        # 밸류 점수
        val_score = scores.get("valuation", 0) or 0
        if val_score >= 8:
            fund_score += 1
            fund_reasons.append(f"밸류 {val_score}/20")

        # 기관 보유율
        inst_pct = details.get("inst_pct") or 0
        if inst_pct and inst_pct > 0.7:
            fund_score += 0.5
            fund_reasons.append(f"기관 {inst_pct*100:.0f}%")

        if fund_score >= 1:
            item["_fund_score"] = fund_score
            item["_fund_reasons"] = fund_reasons
            item["_total_agent_score"] = (item.get("_tech_score") or 0) + fund_score
            results["final_picks"].append(item)

    results["final_picks"].sort(
        key=lambda x: -((x.get("_total_agent_score") or 0) * 10 + (x.get("total_score") or 0))
    )
    results["final_picks"] = results["final_picks"][:10]
    results["stats"] = {"input": len(passed), "output": len(results["final_picks"])}
    print(f"[US Agent 5] {len(passed)} → 최종 {len(results['final_picks'])}종목")
    return results


def run_us_pipeline():
    """US 전용 에이전트 파이프라인."""
    print("\n" + "=" * 50)
    print(f"[US 파이프라인] 시작 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    start = datetime.now()

    news = agent1_us_news()
    macro = agent2_macro()  # KR과 공유 (VIX/금리/DXY 동일)
    matched = agent3_us_match(news, macro)
    tech = agent4_technical(matched)  # KR과 동일 로직
    final = agent5_us_fundamental(tech)

    elapsed = (datetime.now() - start).total_seconds()

    result = {
        "market": "us",
        "timestamp": datetime.now().isoformat(),
        "elapsed_seconds": round(elapsed, 1),
        "agents": {
            "news": {
                "hot_sectors": news.get("top_sectors", []),
                "hot_themes": news.get("top_sectors", []),
                "articles": news.get("total_articles", 0),
                "theme_counts": news.get("sector_counts", {}),
            },
            "macro": {
                "direction": macro.get("direction"),
                "confidence": macro.get("confidence"),
                "factors": macro.get("factors", []),
            },
            "match": {
                "sectors": matched.get("matched_sectors", []),
                "themes": matched.get("matched_sectors", []),
                "candidates": len(matched.get("candidates", [])),
                "source": matched.get("source"),
            },
            "technical": tech.get("stats", {}),
            "fundamental": final.get("stats", {}),
        },
        "final_picks": [],
    }

    for it in final.get("final_picks", []):
        result["final_picks"].append({
            "code": it.get("code") or it.get("symbol"),
            "name": it.get("name"),
            "sector": it.get("sector"),
            "price": it.get("price") or it.get("close"),
            "change_pct": it.get("change_pct"),
            "total_score": it.get("total_score"),
            "market": "us",
            "tech_reasons": it.get("_tech_reasons") or [],
            "fund_reasons": it.get("_fund_reasons") or [],
            "swing_tags": ((it.get("details") or {}).get("rsi_macd_tags") or [])[:5],
        })

    # 저장
    out = BASE_DIR / "cache" / "agent_result_us_latest.json"
    try:
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    print(f"[US 파이프라인] 완료 ({elapsed:.1f}초), 추천 {len(result['final_picks'])}종목")
    return result


# ============================================================
# 파이프라인 실행
# ============================================================
def run_pipeline(market: str = "kr"):
    """
    통합 파이프라인 실행.
    market: 'kr' | 'us' | 'all'
    """
    if market == "us":
        return run_us_pipeline()
    if market == "all":
        kr = run_kr_pipeline()
        us = run_us_pipeline()
        return {"kr": kr, "us": us}
    return run_kr_pipeline()


def run_kr_pipeline():
    """전체 에이전트 파이프라인 실행"""
    print("\n" + "=" * 50)
    print(f"[파이프라인] 시작 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    start = datetime.now()

    news = agent1_news()
    macro = agent2_macro()
    matched = agent3_match(news, macro)
    tech_filtered = agent4_technical(matched)
    final = agent5_fundamental(tech_filtered)

    elapsed = (datetime.now() - start).total_seconds()

    pipeline_result = {
        "timestamp": datetime.now().isoformat(),
        "elapsed_seconds": round(elapsed, 1),
        "agents": {
            "news": {
                "hot_themes": news.get("top_themes", []),
                "theme_counts": news.get("theme_counts", {}),
                "articles": news.get("total_articles", 0),
            },
            "macro": {
                "direction": macro.get("direction", "neutral"),
                "confidence": macro.get("confidence", 0.5),
                "factors": macro.get("factors", []),
            },
            "match": {
                "themes": matched.get("matched_themes", []),
                "candidates": len(matched.get("candidates", [])),
                "source": matched.get("source"),
            },
            "technical": tech_filtered.get("stats", {}),
            "fundamental": final.get("stats", {}),
        },
        "final_picks": [],
    }

    for item in final.get("final_picks", []):
        pipeline_result["final_picks"].append({
            "code": item.get("code"),
            "name": item.get("name"),
            "sector": item.get("sector"),
            "price": item.get("price"),
            "change_pct": item.get("change_pct"),
            "total_score": item.get("total_score"),
            "tech_reasons": item.get("_tech_reasons") or [],
            "fund_reasons": item.get("_fund_reasons") or [],
            "swing_tags": ((item.get("details") or {}).get("rsi_macd_tags") or [])[:5],
        })

    # 캐시 저장
    cache_dir = BASE_DIR / "cache"
    cache_dir.mkdir(exist_ok=True)
    latest_path = cache_dir / "agent_result_latest.json"
    try:
        latest_path.write_text(
            json.dumps(pipeline_result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

    print(f"\n[파이프라인] 완료 ({elapsed:.1f}초)")
    print(f"[파이프라인] 최종 추천: {len(pipeline_result['final_picks'])}종목")
    for p in pipeline_result["final_picks"]:
        reasons = (p.get("tech_reasons") or [])[:2]
        print(f"  {p['name']} ({p['code']}) {p.get('total_score',0)}점 | {', '.join(reasons)}")

    return pipeline_result


# ============================================================
# 텔레그램 추천 발송
# ============================================================
def send_agent_telegram(result):
    """에이전트 추천 결과 텔레그램 발송"""
    try:
        from server import send_telegram
    except ImportError:
        return

    picks = result.get("final_picks", [])
    if not picks:
        return

    macro = result.get("agents", {}).get("macro", {})
    news = result.get("agents", {}).get("news", {})

    macro_emoji = {
        "strong_bull": "🟢🟢", "mild_bull": "🟢",
        "neutral": "🟡",
        "mild_bear": "🔴", "strong_bear": "🔴🔴",
    }.get(macro.get("direction", "neutral"), "🟡")

    msg = "🤖 <b>AI 에이전트 추천</b>\n"
    msg += f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"

    msg += f"{macro_emoji} <b>매크로:</b> {macro.get('direction', 'neutral')}\n"
    for f in macro.get("factors", [])[:3]:
        msg += f"  • {f}\n"
    msg += "\n"

    themes = news.get("hot_themes", [])
    if themes:
        msg += f"🔥 <b>핫 테마:</b> {', '.join(themes)}\n\n"

    msg += f"📊 <b>추천 종목 ({len(picks)}개)</b>\n"
    for i, p in enumerate(picks[:5], 1):
        sign = "+" if (p.get("change_pct") or 0) >= 0 else ""
        msg += f"\n<b>{i}. {p['name']}</b> ({p['code']})\n"
        msg += f"   ₩{(p.get('price') or 0):,.0f} ({sign}{p.get('change_pct') or 0:.2f}%)\n"
        msg += f"   점수: {p.get('total_score', 0)}/105\n"
        reasons = (p.get("tech_reasons") or []) + (p.get("fund_reasons") or [])
        if reasons:
            msg += f"   사유: {', '.join(reasons[:3])}\n"

    msg += "\n⚠️ 본 추천은 기술적 지표 기반 참고용입니다."
    send_telegram(msg)


if __name__ == "__main__":
    result = run_pipeline()
    print(json.dumps(result, ensure_ascii=False, indent=2)[:2000])
