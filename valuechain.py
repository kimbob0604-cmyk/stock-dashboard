"""
valuechain.py — 산업별 밸류체인 맵 + 실시간 뉴스 heat 엔진

테마: AI/반도체, 2차전지/ESS, 조선, 방산, 데이터센터·전력
실시간 데이터 소스:
  - 네이버 뉴스 API (국내 헤드라인 키워드 매칭)
  - Finnhub 뉴스 (글로벌 헤드라인)
  - SQLite stocks (현재가)
  - SQLite flow_cache (외국인 순매수)

heat = (키워드 등장 횟수 × 5) + (병목 키워드 × 15), 0-100 클램프
"""
from __future__ import annotations
import os
import json
import time
import logging
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime

log = logging.getLogger(__name__)
BASE_DIR = Path(__file__).parent
CACHE_DIR = BASE_DIR / "cache"


# ============================================================
# 밸류체인 맵 — 5개 테마 (큐레이션 데이터)
# ★ 핵심·● 국내·bottleneck=병목 표시
# ============================================================
VALUECHAIN_MAP = {

    # ──── AI × 반도체 ─────────────────────────
    "ai_semiconductor": {
        "theme": "AI × 반도체",
        "icon": "🧠",
        "layers": [
            {"id": "L1", "title": "L1 · 실리콘 컴퓨트",
             "subtitle": "GPU · CPU · NPU · ASIC — AI 연산의 시작점",
             "color": "#6366f1",
             "segments": [
                 {"name": "GPU", "companies": [
                     {"code": "NVDA", "name": "NVIDIA", "market": "us"},
                     {"code": "AMD", "name": "AMD", "market": "us"},
                 ], "bottleneck": False},
                 {"name": "CPU ★", "companies": [
                     {"code": "INTC", "name": "Intel", "market": "us"},
                 ], "bottleneck": True},
                 {"name": "ASIC", "companies": [
                     {"code": "AVGO", "name": "Broadcom", "market": "us"},
                     {"code": "MRVL", "name": "Marvell", "market": "us"},
                 ], "bottleneck": False},
                 {"name": "파운드리", "companies": [
                     {"code": "TSM", "name": "TSMC", "market": "us"},
                     {"code": "005930", "name": "삼성전자", "market": "kr"},
                 ], "bottleneck": False},
             ],
             "keywords": ["GPU", "NPU", "ASIC", "AI칩", "Blackwell", "B300", "테이프아웃", "팹리스"],
             "bottleneck_words": ["공급부족", "리드타임", "수율", "shortage"]},

            {"id": "L2", "title": "L2 · 패키징 · 기판",
             "subtitle": "FC-BGA · MLB · MLCC — CPU 수요 4배 연동 병목",
             "color": "#8b5cf6",
             "segments": [
                 {"name": "FC-BGA ★●", "companies": [
                     {"code": "009150", "name": "삼성전기", "market": "kr"},
                 ], "bottleneck": True},
                 {"name": "MLB ★●", "companies": [
                     {"code": "007660", "name": "이수페타시스", "market": "kr"},
                     {"code": "353200", "name": "대덕전자", "market": "kr"},
                 ], "bottleneck": True},
                 {"name": "MLCC ●", "companies": [
                     {"code": "009150", "name": "삼성전기", "market": "kr"},
                 ], "bottleneck": False},
                 {"name": "메모리기판 ●", "companies": [
                     {"code": "222800", "name": "심텍", "market": "kr"},
                     {"code": "417200", "name": "티엘비", "market": "kr"},
                 ], "bottleneck": False},
             ],
             "keywords": ["FC-BGA", "MLB", "MLCC", "기판", "패키징", "CoWoS", "ABF", "서브스트레이트"],
             "bottleneck_words": ["부족", "증설", "리드타임", "4배", "병목"]},

            {"id": "L3", "title": "L3 · 메모리 · HBM",
             "subtitle": "에이전틱 AI KV캐시 수요 — DRAM 추가 15~45 엑사바이트",
             "color": "#3b82f6",
             "segments": [
                 {"name": "HBM ★●", "companies": [
                     {"code": "000660", "name": "SK하이닉스", "market": "kr"},
                     {"code": "005930", "name": "삼성전자", "market": "kr"},
                 ], "bottleneck": True},
                 {"name": "DRAM ●", "companies": [
                     {"code": "000660", "name": "SK하이닉스", "market": "kr"},
                     {"code": "MU", "name": "Micron", "market": "us"},
                 ], "bottleneck": False},
                 {"name": "AI 스토리지 ★", "companies": [
                     {"code": "PSTG", "name": "Pure Storage", "market": "us"},
                 ], "bottleneck": True},
                 {"name": "NAND", "companies": [
                     {"code": "005930", "name": "삼성전자", "market": "kr"},
                 ], "bottleneck": False},
             ],
             "keywords": ["HBM", "HBM3E", "HBM4", "DRAM", "메모리", "CXL", "KV캐시"],
             "bottleneck_words": ["CAPA", "증설", "양산지연", "수율"]},

            {"id": "L4", "title": "L4 · 소부장",
             "subtitle": "공정 미세화·3D 적층 → 세정·코팅·검사·소재 구조적 수요",
             "color": "#22c55e",
             "segments": [
                 {"name": "후공정장비 ★●", "companies": [
                     {"code": "042700", "name": "한미반도체", "market": "kr"},
                 ], "bottleneck": True},
                 {"name": "전공정장비 ★●", "companies": [
                     {"code": "403870", "name": "HPSP", "market": "kr"},
                     {"code": "240810", "name": "원익IPS", "market": "kr"},
                 ], "bottleneck": True},
                 {"name": "세정·코팅 ★●", "companies": [
                     {"code": "183300", "name": "코미코", "market": "kr"},
                 ], "bottleneck": True},
                 {"name": "검사장비 ★●", "companies": [
                     {"code": "064290", "name": "인텍플러스", "market": "kr"},
                 ], "bottleneck": True},
                 {"name": "소재·부품 ●", "companies": [
                     {"code": "064760", "name": "티씨케이", "market": "kr"},
                 ], "bottleneck": False},
             ],
             "keywords": ["소부장", "후공정", "세정", "검사장비", "소재", "HPA", "ALD"],
             "bottleneck_words": ["TSMC향", "퀄", "신규진입", "독점"]},

            {"id": "L5", "title": "L5 · 광통신 · 인터커넥트",
             "subtitle": "GPU-to-GPU 연결 → 800G 출하 2배·2027 추가 2배",
             "color": "#06b6d4",
             "segments": [
                 {"name": "EML 레이저 ★", "companies": [
                     {"code": "LITE", "name": "Lumentum", "market": "us"},
                     {"code": "COHR", "name": "Coherent", "market": "us"},
                 ], "bottleneck": True},
                 {"name": "광섬유 모재 ★●", "companies": [
                     {"code": "010170", "name": "대한광통신", "market": "kr"},
                 ], "bottleneck": True},
                 {"name": "광섬유 케이블", "companies": [
                     {"code": "GLW", "name": "Corning", "market": "us"},
                 ], "bottleneck": False},
                 {"name": "스위치·NVLink", "companies": [
                     {"code": "ANET", "name": "Arista", "market": "us"},
                     {"code": "CSCO", "name": "Cisco", "market": "us"},
                 ], "bottleneck": False},
                 {"name": "해저케이블 ●", "companies": [
                     {"code": "042600", "name": "KT서브마린", "market": "kr"},
                 ], "bottleneck": False},
             ],
             "keywords": ["광통신", "CPO", "800G", "1.6T", "광모듈", "인터커넥트", "NVLink", "EML"],
             "bottleneck_words": ["부족", "shortage", "증설지연", "수요초과"]},

            {"id": "L6", "title": "L6 · AI 서버 · 데이터센터",
             "subtitle": "하이퍼스케일러 CapEx 2026 $6,000억+",
             "color": "#f59e0b",
             "segments": [
                 {"name": "하이퍼스케일러", "companies": [
                     {"code": "AMZN", "name": "AWS", "market": "us"},
                     {"code": "MSFT", "name": "Azure", "market": "us"},
                     {"code": "GOOGL", "name": "GCP", "market": "us"},
                 ], "bottleneck": False},
                 {"name": "국내 클라우드 ★●", "companies": [
                     {"code": "018260", "name": "삼성SDS", "market": "kr"},
                 ], "bottleneck": True},
                 {"name": "AI 서버 OEM", "companies": [
                     {"code": "SMCI", "name": "Super Micro", "market": "us"},
                     {"code": "DELL", "name": "Dell", "market": "us"},
                 ], "bottleneck": False},
                 {"name": "전력·냉각", "companies": [
                     {"code": "VRT", "name": "Vertiv", "market": "us"},
                     {"code": "ETN", "name": "Eaton", "market": "us"},
                 ], "bottleneck": False},
             ],
             "keywords": ["데이터센터", "클라우드", "CapEx", "서버", "냉각", "전력", "AI인프라"],
             "bottleneck_words": ["전력부족", "부지", "가동률", "증설"]},
        ],
    },

    # ──── 2차전지 · ESS ─────────────────────────
    "battery_ess": {
        "theme": "2차전지 · ESS",
        "icon": "🔋",
        "layers": [
            {"id": "B1", "title": "B1 · 광물 · 원재료",
             "subtitle": "리튬·니켈·코발트·흑연 — 자원 확보 경쟁",
             "color": "#78716c",
             "segments": [
                 {"name": "리튬 ★", "companies": [
                     {"code": "ALB", "name": "Albemarle", "market": "us"},
                     {"code": "SQM", "name": "SQM", "market": "us"},
                 ], "bottleneck": True},
                 {"name": "흑연·음극활물질 ●", "companies": [
                     {"code": "117580", "name": "포스코퓨처엠 (자회사)", "market": "kr"},
                 ], "bottleneck": False},
             ],
             "keywords": ["리튬", "니켈", "코발트", "흑연", "광물", "자원", "탄산리튬"],
             "bottleneck_words": ["가격급등", "공급부족", "광산", "제재"]},

            {"id": "B2", "title": "B2 · 4대 핵심 소재",
             "subtitle": "양극재·음극재·분리막·전해질",
             "color": "#f97316",
             "segments": [
                 {"name": "양극재 ★●", "companies": [
                     {"code": "247540", "name": "에코프로비엠", "market": "kr"},
                     {"code": "003670", "name": "포스코퓨처엠", "market": "kr"},
                     {"code": "051910", "name": "LG화학", "market": "kr"},
                 ], "bottleneck": True},
                 {"name": "음극재 ●", "companies": [
                     {"code": "003670", "name": "포스코퓨처엠", "market": "kr"},
                 ], "bottleneck": False},
                 {"name": "분리막 ●", "companies": [
                     {"code": "361610", "name": "SK아이이테크놀로지", "market": "kr"},
                 ], "bottleneck": False},
                 {"name": "전해질 ★●", "companies": [
                     {"code": "086520", "name": "에코프로", "market": "kr"},
                     {"code": "131290", "name": "티에스이", "market": "kr"},
                 ], "bottleneck": True},
             ],
             "keywords": ["양극재", "음극재", "분리막", "전해질", "NCM", "LFP", "하이니켈"],
             "bottleneck_words": ["가동률", "증설", "가격", "원가"]},

            {"id": "B3", "title": "B3 · 셀 제조",
             "subtitle": "배터리 셀 — 파우치·각형·원통형",
             "color": "#ef4444",
             "segments": [
                 {"name": "LG에너지솔루션 ●", "companies": [
                     {"code": "373220", "name": "LG에너지솔루션", "market": "kr"},
                 ], "bottleneck": False},
                 {"name": "삼성SDI ●", "companies": [
                     {"code": "006400", "name": "삼성SDI", "market": "kr"},
                 ], "bottleneck": False},
                 {"name": "SK온 (모회사)", "companies": [
                     {"code": "096770", "name": "SK이노베이션", "market": "kr"},
                 ], "bottleneck": False},
             ],
             "keywords": ["배터리셀", "파우치", "각형", "원통형", "46xx", "4680"],
             "bottleneck_words": ["가동률", "수율", "양산", "수주"]},

            {"id": "B4", "title": "B4 · 전고체 · 차세대",
             "subtitle": "2027~28 상용화 목표 — 게임체인저",
             "color": "#a855f7",
             "segments": [
                 {"name": "전고체 ★", "companies": [
                     {"code": "006400", "name": "삼성SDI", "market": "kr"},
                     {"code": "QS", "name": "QuantumScape", "market": "us"},
                 ], "bottleneck": True},
             ],
             "keywords": ["전고체", "고체전해질", "황화물", "산화물", "리튬메탈"],
             "bottleneck_words": ["양산", "파일럿", "수율", "상용화"]},

            {"id": "B5", "title": "B5 · ESS · 전력저장",
             "subtitle": "재생에너지 연계 ESS 수요 폭증",
             "color": "#22c55e",
             "segments": [
                 {"name": "ESS 시스템 ●", "companies": [
                     {"code": "373220", "name": "LG에너지솔루션", "market": "kr"},
                     {"code": "006400", "name": "삼성SDI", "market": "kr"},
                 ], "bottleneck": False},
                 {"name": "PCS·인버터 ●", "companies": [
                     {"code": "267260", "name": "HD현대일렉트릭", "market": "kr"},
                 ], "bottleneck": False},
                 {"name": "미국 ESS", "companies": [
                     {"code": "FLNC", "name": "Fluence", "market": "us"},
                 ], "bottleneck": False},
             ],
             "keywords": ["ESS", "에너지저장", "전력저장", "그리드", "BESS", "PCS"],
             "bottleneck_words": ["화재", "안전", "인증", "수주"]},
        ],
    },

    # ──── 조선 ─────────────────────────
    "shipbuilding": {
        "theme": "조선",
        "icon": "🚢",
        "layers": [
            {"id": "S1", "title": "S1 · 선박 설계 · 수주",
             "subtitle": "LNG선·컨테이너선·VLCC — 슈퍼사이클",
             "color": "#3b82f6",
             "segments": [
                 {"name": "HD현대중공업 ●", "companies": [
                     {"code": "329180", "name": "HD현대중공업", "market": "kr"},
                 ], "bottleneck": False},
                 {"name": "삼성중공업 ●", "companies": [
                     {"code": "010140", "name": "삼성중공업", "market": "kr"},
                 ], "bottleneck": False},
                 {"name": "한화오션 ●", "companies": [
                     {"code": "042660", "name": "한화오션", "market": "kr"},
                 ], "bottleneck": False},
             ],
             "keywords": ["수주", "LNG선", "컨테이너선", "VLCC", "조선", "클락슨"],
             "bottleneck_words": ["수주잔고", "도크", "인도지연"]},

            {"id": "S2", "title": "S2 · 엔진 · 추진",
             "subtitle": "선박 엔진 + DC 전력 엔진 신시장",
             "color": "#0ea5e9",
             "segments": [
                 {"name": "한화엔진 ●", "companies": [
                     {"code": "082740", "name": "한화엔진", "market": "kr"},
                 ], "bottleneck": False},
                 {"name": "STX엔진 ★●", "companies": [
                     {"code": "077970", "name": "STX엔진", "market": "kr"},
                 ], "bottleneck": True},
                 {"name": "HD현대마린엔진 ●", "companies": [
                     {"code": "071970", "name": "HD현대마린엔진", "market": "kr"},
                 ], "bottleneck": False},
             ],
             "keywords": ["선박엔진", "엔진", "추진", "LNG추진", "데이터센터엔진"],
             "bottleneck_words": ["수주잔고", "납기", "CAPA"]},

            {"id": "S3", "title": "S3 · 기자재 · 부품",
             "subtitle": "밸브·펌프·배관 — 수주 후행",
             "color": "#14b8a6",
             "segments": [
                 {"name": "HD현대미포 ●", "companies": [
                     {"code": "010620", "name": "HD현대미포", "market": "kr"},
                 ], "bottleneck": False},
                 {"name": "기자재 ●", "companies": [
                     {"code": "092200", "name": "디아이씨", "market": "kr"},
                 ], "bottleneck": False},
             ],
             "keywords": ["기자재", "밸브", "펌프", "배관", "LNG탱크"],
             "bottleneck_words": ["납기지연", "인력부족"]},
        ],
    },

    # ──── 방산 ─────────────────────────
    "defense": {
        "theme": "방산",
        "icon": "🛡️",
        "layers": [
            {"id": "D1", "title": "D1 · 완제품 · 시스템",
             "subtitle": "K방산 — 수출 급성장",
             "color": "#64748b",
             "segments": [
                 {"name": "항공 ●", "companies": [
                     {"code": "012450", "name": "한화에어로스페이스", "market": "kr"},
                     {"code": "047810", "name": "한국항공우주", "market": "kr"},
                 ], "bottleneck": False},
                 {"name": "유도무기 ●", "companies": [
                     {"code": "079550", "name": "LIG넥스원", "market": "kr"},
                 ], "bottleneck": False},
                 {"name": "지상 ●", "companies": [
                     {"code": "064350", "name": "현대로템", "market": "kr"},
                 ], "bottleneck": False},
                 {"name": "미국 방산", "companies": [
                     {"code": "LMT", "name": "Lockheed Martin", "market": "us"},
                     {"code": "RTX", "name": "RTX", "market": "us"},
                 ], "bottleneck": False},
             ],
             "keywords": ["K방산", "방산수출", "K9", "K2전차", "미사일", "KF-21"],
             "bottleneck_words": ["수주", "수출계약", "NATO", "폴란드"]},

            {"id": "D2", "title": "D2 · 탄약 · 화약 · 시스템",
             "subtitle": "우크라 전쟁 → 글로벌 탄약 재고 고갈",
             "color": "#dc2626",
             "segments": [
                 {"name": "탄약 ★●", "companies": [
                     {"code": "012450", "name": "한화에어로스페이스", "market": "kr"},
                 ], "bottleneck": True},
                 {"name": "방산시스템 ●", "companies": [
                     {"code": "272210", "name": "한화시스템", "market": "kr"},
                 ], "bottleneck": False},
             ],
             "keywords": ["탄약", "포탄", "화약", "155mm", "재고"],
             "bottleneck_words": ["재고고갈", "증산", "24시간가동"]},

            {"id": "D3", "title": "D3 · 우주 · 위성",
             "subtitle": "저궤도 위성 + 발사체",
             "color": "#1e3a5f",
             "segments": [
                 {"name": "위성 ●", "companies": [
                     {"code": "272110", "name": "쎄트렉아이", "market": "kr"},
                     {"code": "PL", "name": "Planet Labs", "market": "us"},
                 ], "bottleneck": False},
                 {"name": "발사체", "companies": [
                     {"code": "RKLB", "name": "Rocket Lab", "market": "us"},
                 ], "bottleneck": False},
                 {"name": "위성통신", "companies": [
                     {"code": "089030", "name": "AP위성", "market": "kr"},
                     {"code": "ASTS", "name": "AST SpaceMobile", "market": "us"},
                 ], "bottleneck": False},
             ],
             "keywords": ["위성", "저궤도", "LEO", "발사체", "스타링크", "우주"],
             "bottleneck_words": ["발사", "궤도", "주파수"]},
        ],
    },

    # ──── 데이터센터 · 전력 ─────────────────────────
    "datacenter_power": {
        "theme": "데이터센터 · 전력",
        "icon": "⚡",
        "layers": [
            {"id": "P1", "title": "P1 · 전력 생산",
             "subtitle": "원전 · SMR · 가스터빈",
             "color": "#f59e0b",
             "segments": [
                 {"name": "원전 ★●", "companies": [
                     {"code": "034020", "name": "두산에너빌리티", "market": "kr"},
                 ], "bottleneck": True},
                 {"name": "SMR", "companies": [
                     {"code": "SMR", "name": "NuScale", "market": "us"},
                     {"code": "OKLO", "name": "Oklo", "market": "us"},
                 ], "bottleneck": False},
                 {"name": "가스터빈 ●", "companies": [
                     {"code": "082740", "name": "한화엔진", "market": "kr"},
                 ], "bottleneck": False},
                 {"name": "우라늄", "companies": [
                     {"code": "CCJ", "name": "Cameco", "market": "us"},
                     {"code": "LEU", "name": "Centrus", "market": "us"},
                 ], "bottleneck": False},
             ],
             "keywords": ["원전", "SMR", "가스터빈", "우라늄", "HALEU", "APR1400"],
             "bottleneck_words": ["체코", "수주", "인허가", "연료"]},

            {"id": "P2", "title": "P2 · 송변전 · 전력기기",
             "subtitle": "변압기·차단기·케이블 — 수주잔고 역대 최고",
             "color": "#eab308",
             "segments": [
                 {"name": "변압기 ★●", "companies": [
                     {"code": "267260", "name": "HD현대일렉트릭", "market": "kr"},
                 ], "bottleneck": True},
                 {"name": "차단기 ●", "companies": [
                     {"code": "010120", "name": "LS ELECTRIC", "market": "kr"},
                 ], "bottleneck": False},
                 {"name": "전선·케이블 ●", "companies": [
                     {"code": "006260", "name": "LS", "market": "kr"},
                 ], "bottleneck": False},
                 {"name": "미국 전력", "companies": [
                     {"code": "ETN", "name": "Eaton", "market": "us"},
                     {"code": "GEV", "name": "GE Vernova", "market": "us"},
                 ], "bottleneck": False},
             ],
             "keywords": ["변압기", "차단기", "전력기기", "송전", "배전", "그리드"],
             "bottleneck_words": ["수주잔고", "리드타임", "3년", "납기"]},

            {"id": "P3", "title": "P3 · 냉각 · DC 인프라",
             "subtitle": "액침냉각·공조 — DC 전력의 30~40%",
             "color": "#06b6d4",
             "segments": [
                 {"name": "냉각 ★", "companies": [
                     {"code": "VRT", "name": "Vertiv", "market": "us"},
                 ], "bottleneck": True},
                 {"name": "액침냉각 ●", "companies": [
                     {"code": "281740", "name": "레이크머티리얼즈", "market": "kr"},
                 ], "bottleneck": False},
                 {"name": "UPS·전원", "companies": [
                     {"code": "ETN", "name": "Eaton", "market": "us"},
                 ], "bottleneck": False},
             ],
             "keywords": ["냉각", "액침냉각", "공조", "CRAC", "CRAH", "PUE"],
             "bottleneck_words": ["전력효율", "과열", "PUE"]},
        ],
    },
}


# ============================================================
# 뉴스 헤드라인 수집 (네이버 + Finnhub) + 매칭
# ============================================================
_heat_cache: dict = {"data": None, "ts": 0}
_HEAT_TTL = 1800   # 30분
_NEWS_QUERIES = ["반도체", "2차전지", "조선", "방산", "데이터센터",
                 "원전", "광통신", "AI", "HBM", "전력기기"]


def _fetch_naver_news() -> list:
    nid = os.getenv("NAVER_CLIENT_ID")
    nsec = os.getenv("NAVER_CLIENT_SECRET")
    if not nid or not nsec:
        return []
    out = []
    for q in _NEWS_QUERIES:
        try:
            qs = urllib.parse.urlencode({"query": q, "display": 10, "sort": "date"})
            req = urllib.request.Request(
                f"https://openapi.naver.com/v1/search/news.json?{qs}",
                headers={
                    "X-Naver-Client-Id": nid,
                    "X-Naver-Client-Secret": nsec,
                    "User-Agent": "Mozilla/5.0",
                }
            )
            with urllib.request.urlopen(req, timeout=6) as r:
                d = json.loads(r.read().decode("utf-8"))
            for it in d.get("items", []):
                title = (it.get("title") or "").replace("<b>", "").replace("</b>", "")
                title = title.replace("&quot;", '"').replace("&amp;", "&")
                if title:
                    out.append(title)
        except Exception as exc:
            log.debug("[밸류체인] Naver news q=%s: %s", q, exc)
    return out


def _fetch_finnhub_news() -> list:
    key = os.getenv("FINNHUB_API_KEY")
    if not key:
        return []
    try:
        req = urllib.request.Request(
            f"https://finnhub.io/api/v1/news?category=general&token={key}",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        return [it.get("headline", "") for it in (data or [])[:50] if it.get("headline")]
    except Exception as exc:
        log.debug("[밸류체인] Finnhub: %s", exc)
        return []


def calculate_layer_heat() -> dict:
    """뉴스 헤드라인 → 각 레이어 키워드 매칭 → heat 점수 + 매칭 헤드라인 톱3."""
    now = time.time()
    if _heat_cache["data"] and now - _heat_cache["ts"] < _HEAT_TTL:
        return _heat_cache["data"]

    headlines = _fetch_naver_news() + _fetch_finnhub_news()
    if not headlines:
        log.info("[밸류체인] 뉴스 0건 — 빈 heat 반환")
        return {"_meta": {"total_headlines": 0,
                          "calculated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}}

    # 소문자 텍스트 (단순 contains 매칭)
    lc_headlines = [h.lower() for h in headlines]
    all_text = " ".join(lc_headlines)

    result: dict = {}
    for theme_id, theme in VALUECHAIN_MAP.items():
        layer_results = []
        total_heat = 0
        for layer in theme["layers"]:
            kw_count = 0
            kw_matched: list = []
            for kw in layer.get("keywords", []):
                c = all_text.count(kw.lower())
                if c > 0:
                    kw_count += c
                    kw_matched.append((kw, c))
            kw_matched.sort(key=lambda x: -x[1])

            bn_count = 0
            bn_matched: list = []
            for bw in layer.get("bottleneck_words", []):
                c = all_text.count(bw.lower())
                if c > 0:
                    bn_count += c
                    bn_matched.append(bw)

            # 매칭 헤드라인 톱 3 (키워드 또는 병목 단어 포함)
            relevant_lc = set(kw[0].lower() for kw in kw_matched) | set(bw.lower() for bw in bn_matched)
            top_headlines = []
            for orig, lc in zip(headlines, lc_headlines):
                if any(w in lc for w in relevant_lc):
                    top_headlines.append(orig)
                    if len(top_headlines) >= 3:
                        break

            heat = min(100, kw_count * 5 + bn_count * 15)
            layer_results.append({
                "layer_id": layer["id"],
                "heat": heat,
                "keyword_count": kw_count,
                "bottleneck_count": bn_count,
                "matched_keywords": [f"{k}({c})" for k, c in kw_matched[:5]],
                "bottleneck_alert": bn_count >= 2,
                "bottleneck_matched": bn_matched,
                "top_headlines": top_headlines,
            })
            total_heat += heat

        result[theme_id] = {
            "theme": theme["theme"], "icon": theme["icon"],
            "layers": layer_results, "total_heat": total_heat,
        }

    result["_meta"] = {
        "total_headlines": len(headlines),
        "calculated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "naver_count": len(_fetch_naver_news.__name__),  # placeholder
    }

    _heat_cache["data"] = result
    _heat_cache["ts"] = now

    try:
        CACHE_DIR.mkdir(exist_ok=True)
        (CACHE_DIR / "valuechain_heat.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass
    return result
