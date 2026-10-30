"""
public_stats.py — 공공데이터포털(data.go.kr) 공개 통계: 💰 증시자금(금투협) · 🚢 수출입(관세청).

KBJ 의 같은 수집기(kbj/data/datago.py · fsc_kofia_stats · customs)를 이 저장소에 맞게 줄여 옮겼다.
둘 다 **공개 등급**(이용허락 '제한 없음')이다. 운용사·KIS·KRX 같은 로그인 등급 자료는 이 대시보드에
싣지 않는다(2026-10-30 사용자 결정 — ETF 구성종목·흐름 제외).

■ 키 — 환경변수 이름을 하나로 못 박지 않고 `KEY_ENV_NAMES` 를 차례로 본다. 값은 어디에도 남기지
  않고(로그·응답·오류 문구) **이름만** 알린다. 포털은 같은 키를 Encoding·Decoding 두 형태로
  보여 준다 — '디코딩한 값을 한 번 인코딩' → '받은 그대로' 순서로 눌러 보고, 키 오류일 때만 다음
  형태로 넘어간다(KBJ 와 같은 방식).
■ 게이트웨이 오류 — 포털은 오류면 resultType 을 무시하고 XML(`OpenAPI_ServiceResponse/cmmMsgHeader/
  returnReasonCode`)을 준다. 30·31·21 키 / 20·32·33 활용신청 없음 / 22 일 한도 / 12 서비스 없음.

■ 💰 증시자금 — 금융위 금융투자협회종합통계(15094809)
    getSecuritiesMarketTotalCapitalInfo  invrDpsgAmt 투자자예탁금 · brkTrdUcolMnyVsOppsTrdAmt 반대매매
    getGrantingOfCreditBalanceInfo       crdTrFingWhl·Scrs·Kosdaq 신용거래융자(전체·유가·코스닥)
  `endBasDt` 는 '미만'이라 하루 더해 부른다. 공표는 D+1 13시 이후(2거래일 늦게 보일 수 있다).
  **단위는 원으로 추정되지만 실측 확인 전이다**(KBJ probe_results §3.1 [실측 필요]) — 예탁금이
  상식 범위(5조~2,000조 원)에 들도록 배수(1·천·백만·억)를 값의 크기로 고르고, 고른 배수를 화면에 적는다.

■ 🚢 수출입 — 관세청 품목별 수출입실적(15101609, XML 만). `hsSgn` 접두로 부르고, 같은 달 응답에
  그 HS 와 **정확히 같은** 행이 있으면 그 행만, 없으면 접두가 맞는 행을 모두 더한다(합계 행과
  세부 행을 겹쳐 세지 않게). 금액은 달러, 중량은 kg(단가 = 금액 ÷ 중량). 매월 15일경 전월까지.
  기간 한도가 문서에 없어 12개월씩 나눠 부른다.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from xml.etree import ElementTree as ET

log = logging.getLogger(__name__)

HTTP_TIMEOUT = 20
BASE = "https://apis.data.go.kr"
KEY_ENV_NAMES = ("DATA_GO_KR_KEY", "DATA_GO_KR_API_KEY", "DATAGO_KEY", "DATAGO_API_KEY",
                 "KBJ_DATAGO_KEY", "PUBLIC_DATA_KEY", "PUBLIC_DATA_API_KEY", "SERVICE_KEY")

KOFIA_DATASET = "15094809"
KOFIA_PATH = "/1160100/service/GetKofiaStatisticsInfoService"
KOFIA_OPS = {"capital": "getSecuritiesMarketTotalCapitalInfo",
             "credit": "getGrantingOfCreditBalanceInfo"}
KOFIA_FIELDS = {
    "capital": {"invrDpsgAmt": "deposit", "brkTrdUcolMnyVsOppsTrdAmt": "forced_sale"},
    "credit": {"crdTrFingWhl": "credit_total", "crdTrFingScrs": "credit_kospi",
               "crdTrFingKosdaq": "credit_kosdaq"},
}
KOFIA_FULL_DAYS = 365 * 3 + 30
KOFIA_INCR_DAYS = 30
# 단위 배수 후보와 '상식 범위'(원) — 예탁금으로 고른다
UNIT_CANDIDATES = (1, 1_000, 1_000_000, 100_000_000)
DEPOSIT_SANE = (5e12, 2e15)

CUSTOMS_DATASET = "15101609"
CUSTOMS_PATH = "/1220000/Itemtrade/getItemtradeList"
CUSTOMS_MONTHS = 25                 # 올해 달 + 작년 같은 달(YoY)
CUSTOMS_CHUNK = 12
# (분류, 이름, HS) — HS 는 4·6자리 접두. 품목은 jusikbot 수출입 화면과 KBJ 매핑의 대표만 골랐다
TRADE_ITEMS = (
    ("반도체", "메모리 반도체", "854232"),
    ("반도체", "프로세서·컨트롤러", "854231"),
    ("반도체", "SSD", "852351"),
    ("반도체", "인쇄회로기판(PCB)", "853400"),
    ("전력·인프라", "변압기·전력변환기", "8504"),
    ("전력·인프라", "전선·케이블", "8544"),
    ("2차전지", "리튬이온 축전지", "850760"),
    ("미용·의료", "화장품", "3304"),
    ("소비재", "라면", "190230"),
    ("자동차·조선", "승용차", "8703"),
    ("자동차·조선", "선박", "8901"),
    ("방산", "전차·장갑차", "8710"),
)

GW_REASONS = {
    "30": "키가 등록되지 않았다(SERVICE_KEY_IS_NOT_REGISTERED)", "31": "키 기한 만료",
    "21": "키를 쓸 수 없다", "20": "이 데이터셋 활용신청이 없다(접근 거부)",
    "32": "등록되지 않은 IP", "33": "서명되지 않은 호출", "22": "일 호출 한도 초과",
    "12": "서비스가 없다(폐기)", "23": "초당 호출 한도 초과", "05": "기관 응답 시간 초과",
}
KEY_ERRORS = {"30", "31", "21"}


class DatagoError(RuntimeError):
    """포털·기관 오류. 문구에 키 값이 들어가지 않는다."""

    def __init__(self, dataset: str, code: str, msg: str):
        super().__init__(f"{dataset}: {msg} (코드 {code})")
        self.dataset, self.code = dataset, code


def find_key(env=None) -> tuple[str | None, str | None]:
    """(환경변수 이름, 값). 없으면 (None, None)."""
    env = os.environ if env is None else env
    for name in KEY_ENV_NAMES:
        v = (env.get(name) or "").strip()
        if v:
            return name, v
    return None, None


def _key_forms(key: str) -> list[tuple[str, str]]:
    """(형태 이름, URL 에 그대로 붙일 문자열). 디코딩 → 한 번 인코딩, 그리고 받은 그대로."""
    decoded = urllib.parse.unquote(key)
    forms = [("decoded", urllib.parse.quote(decoded, safe=""))]
    if key != forms[0][1]:
        forms.append(("raw", key))
    return forms


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:   # noqa: S310
            return r.read()
    except urllib.error.HTTPError as e:            # 포털은 401·403 에도 XML 사유를 싣는다
        return e.read() or b""


def gateway_error(body: bytes) -> str | None:
    """포털 게이트웨이 오류 XML 이면 returnReasonCode, 아니면 None."""
    head = body[:2000].decode("utf-8", "ignore")
    if "OpenAPI_ServiceResponse" not in head and "returnReasonCode" not in head:
        return None
    m = re.search(r"<returnReasonCode>\s*(\d+)\s*</returnReasonCode>", head)
    return m.group(1) if m else "99"


def call(dataset: str, path: str, params: dict, key: str, get=_http_get) -> bytes:
    """포털 호출 — 키 형태를 바꿔 가며. 키 오류만 다음 형태로, 나머지는 바로 DatagoError."""
    last = None
    for _form, k in _key_forms(key):
        q = urllib.parse.urlencode(params)
        body = get(f"{BASE}{path}?serviceKey={k}&{q}")
        code = gateway_error(body)
        if code is None:
            return body
        last = code
        if code not in KEY_ERRORS:
            break
    raise DatagoError(dataset, last or "99", GW_REASONS.get(last or "", "포털 오류"))


# ─────────────────────────────── 💰 금투협 ───────────────────────────────

def parse_kofia(body: bytes, op: str) -> list[dict]:
    """JSON 봉투 → [{date, <필드>: float|None}]. 기관 오류(resultCode != 00)는 DatagoError."""
    js = json.loads(body)
    resp = js.get("response") or {}
    rc = str((resp.get("header") or {}).get("resultCode") or "00")
    if rc not in ("00", "03"):
        raise DatagoError(KOFIA_DATASET, rc, str((resp.get("header") or {}).get("resultMsg") or "기관 오류"))
    items = ((resp.get("body") or {}).get("items") or {})
    raw = items.get("item") if isinstance(items, dict) else None
    if isinstance(raw, dict):
        raw = [raw]
    out = []
    for x in raw or []:
        d = str(x.get("basDt") or "")
        if not re.fullmatch(r"\d{8}", d):
            continue
        row = {"date": f"{d[:4]}-{d[4:6]}-{d[6:]}"}
        for src, dst in KOFIA_FIELDS[op].items():
            v = str(x.get(src) if x.get(src) is not None else "").replace(",", "").strip()
            try:
                row[dst] = float(v) if v else None
            except ValueError:
                row[dst] = None
        out.append(row)
    return out


def fetch_kofia(op: str, begin: date, end: date, key: str, get=_http_get) -> list[dict]:
    params = {"beginBasDt": f"{begin:%Y%m%d}", "endBasDt": f"{end + timedelta(days=1):%Y%m%d}",
              "resultType": "json", "numOfRows": 10000, "pageNo": 1}
    rows = parse_kofia(call(KOFIA_DATASET, f"{KOFIA_PATH}/{KOFIA_OPS[op]}", params, key, get), op)
    lo, hi = begin.isoformat(), end.isoformat()
    return [r for r in rows if lo <= r["date"] <= hi]


def unit_multiplier(latest_deposit: float | None) -> int | None:
    """예탁금이 상식 범위(원)에 드는 배수. 어느 것도 안 맞으면 None(지어내지 않는다)."""
    if not latest_deposit:
        return None
    for m in UNIT_CANDIDATES:
        if DEPOSIT_SANE[0] <= latest_deposit * m <= DEPOSIT_SANE[1]:
            return m
    return None


KOFIA_DDL = """
CREATE TABLE IF NOT EXISTS kofia_daily (
    date          TEXT PRIMARY KEY,
    deposit       REAL,    -- 투자자예탁금 (원본 단위)
    forced_sale   REAL,    -- 실제 반대매매 금액
    credit_total  REAL,    -- 신용거래융자 전체
    credit_kospi  REAL,
    credit_kosdaq REAL,
    updated_at    TEXT
)"""


def refresh_kofia(conn, today: date, key: str, get=_http_get) -> dict:
    conn.execute(KOFIA_DDL)
    last = conn.execute("SELECT MAX(date) FROM kofia_daily").fetchone()[0]
    begin = today - timedelta(days=KOFIA_INCR_DAYS if last else KOFIA_FULL_DAYS)
    res = {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for op in ("capital", "credit"):
        try:
            rows = fetch_kofia(op, begin, today, key, get)
        except DatagoError as e:
            res[op] = f"실패: {e}"
            continue
        cols = list(KOFIA_FIELDS[op].values())
        for r in rows:
            conn.execute("INSERT OR IGNORE INTO kofia_daily (date) VALUES (?)", (r["date"],))
            conn.execute(f"UPDATE kofia_daily SET {', '.join(c + ' = ?' for c in cols)}, "
                         "updated_at = ? WHERE date = ?", [r[c] for c in cols] + [now, r["date"]])
        conn.commit()
        res[op] = len(rows)
    return res


def kofia_payload(conn, today_iso: str, period_days: int, caps: dict | None = None) -> dict:
    """화면 한 벌. caps = {'KOSPI': 원, 'KOSDAQ': 원} — 오늘 시총 합(신용/시총 비율, 최근 값만)."""
    conn.execute(KOFIA_DDL)
    rows = [dict(zip(("date", "deposit", "forced_sale", "credit_total", "credit_kospi", "credit_kosdaq"), r))
            for r in conn.execute("SELECT date, deposit, forced_sale, credit_total, credit_kospi, "
                                  "credit_kosdaq FROM kofia_daily ORDER BY date")]
    deps = [r for r in rows if r["deposit"]]
    mult = unit_multiplier(deps[-1]["deposit"] if deps else None)
    out = {"unit_multiplier": mult, "series": {}, "latest": {}, "ratio": {}}
    if not rows or mult is None:
        out["error"] = "아직 받은 값이 없다" if not rows else "단위를 판정하지 못했다(예탁금 값이 상식 범위 밖)"
        return out
    start = (date.fromisoformat(today_iso) - timedelta(days=period_days)).isoformat()
    for k in ("deposit", "credit_total", "credit_kospi", "credit_kosdaq", "forced_sale"):
        pts = [{"date": r["date"], "value": round(r[k] * mult / 1e12, 3)} for r in rows if r[k] is not None]
        out["series"][k] = [p for p in pts if p["date"] >= start]
        if pts:
            prev = pts[-2]["value"] if len(pts) > 1 else None
            out["latest"][k] = {"value": pts[-1]["value"], "date": pts[-1]["date"],
                                "change": round(pts[-1]["value"] - prev, 3) if prev is not None else None}
    for mkt, ck in (("KOSPI", "credit_kospi"), ("KOSDAQ", "credit_kosdaq")):
        cap, lat = (caps or {}).get(mkt), out["latest"].get(ck)
        if cap and lat:
            out["ratio"][mkt] = round(lat["value"] * 1e12 / cap * 100, 3)
    if caps and out["latest"].get("credit_total") and all(caps.get(m) for m in ("KOSPI", "KOSDAQ")):
        out["ratio"]["ALL"] = round(out["latest"]["credit_total"]["value"] * 1e12
                                    / (caps["KOSPI"] + caps["KOSDAQ"]) * 100, 3)
    return out


# ─────────────────────────────── 🚢 관세청 ───────────────────────────────

def _ym_add(ym: str, n: int) -> str:
    y, m = int(ym[:4]), int(ym[4:])
    i = y * 12 + m - 1 + n
    return f"{i // 12:04d}{i % 12 + 1:02d}"


def parse_customs(body: bytes, hs: str) -> dict[str, dict]:
    """XML → {YYYYMM: {exp_usd, exp_kg, imp_usd, imp_kg}}. 정확히 같은 HS 행이 있으면 그것만,
    없으면 접두가 맞는 행 합. 합계('총계')·형식이 다른 달 행은 버린다."""
    root = ET.fromstring(body)
    rc = (root.findtext("header/resultCode") or "00").strip()
    if rc not in ("00", "03"):
        raise DatagoError(CUSTOMS_DATASET, rc, (root.findtext("header/resultMsg") or "기관 오류").strip())
    by_month: dict[str, list[dict]] = {}
    for it in root.iter("item"):
        y = (it.findtext("year") or "").strip()
        m = re.fullmatch(r"(\d{4})\.(\d{2})", y)
        if not m:
            continue
        code = re.sub(r"\D", "", it.findtext("hsCode") or it.findtext("hsCd") or "")
        # number 타입이라 앞자리 0 이 빠진다 — 짝수 자리로 되살린다
        if len(code) % 2:
            code = "0" + code
        if not code.startswith(hs):
            continue

        def num(tag):
            v = (it.findtext(tag) or "").replace(",", "").strip()
            try:
                return float(v)
            except ValueError:
                return 0.0
        by_month.setdefault(m.group(1) + m.group(2), []).append(
            {"code": code, "exp_usd": num("expDlr"), "exp_kg": num("expWgt"),
             "imp_usd": num("impDlr"), "imp_kg": num("impWgt")})
    out = {}
    for ym, rows in by_month.items():
        exact = [r for r in rows if r["code"] == hs]
        use = exact or rows
        out[ym] = {k: sum(r[k] for r in use) for k in ("exp_usd", "exp_kg", "imp_usd", "imp_kg")}
    return out


def fetch_customs(hs: str, start_ym: str, end_ym: str, key: str, get=_http_get) -> dict[str, dict]:
    out: dict[str, dict] = {}
    s = start_ym
    while s <= end_ym:
        e = min(_ym_add(s, CUSTOMS_CHUNK - 1), end_ym)
        body = call(CUSTOMS_DATASET, CUSTOMS_PATH,
                    {"strtYymm": s, "endYymm": e, "hsSgn": hs}, key, get)
        out.update(parse_customs(body, hs))
        s = _ym_add(e, 1)
    return out


CUSTOMS_DDL = """
CREATE TABLE IF NOT EXISTS customs_monthly (
    hs       TEXT NOT NULL,
    ym       TEXT NOT NULL,     -- YYYYMM
    exp_usd  REAL, exp_kg REAL, imp_usd REAL, imp_kg REAL,
    PRIMARY KEY (hs, ym)
)"""


def refresh_customs(conn, today: date, key: str, get=_http_get) -> dict:
    """품목마다 최근 25개월. 전월까지 다 있는 품목은 묻지 않는다(전월 값은 15일경 나온다)."""
    conn.execute(CUSTOMS_DDL)
    end_ym = _ym_add(f"{today:%Y%m}", -1)
    start_ym = _ym_add(end_ym, -(CUSTOMS_MONTHS - 1))
    res = {}
    for _cat, name, hs in TRADE_ITEMS:
        have = conn.execute("SELECT MAX(ym), COUNT(*) FROM customs_monthly WHERE hs = ?", (hs,)).fetchone()
        if have[0] and have[0] >= end_ym and have[1] >= CUSTOMS_MONTHS - 1:
            res[hs] = "최신"
            continue
        try:
            data = fetch_customs(hs, start_ym if not have[0] else _ym_add(have[0], -2), end_ym, key, get)
        except DatagoError as e:
            res[hs] = f"실패: {e}"
            if e.code in KEY_ERRORS or e.code in ("20", "22"):
                break                       # 키·활용신청·한도 문제는 다른 품목도 같다
            continue
        for ym, v in data.items():
            conn.execute("INSERT OR REPLACE INTO customs_monthly VALUES (?,?,?,?,?,?)",
                         (hs, ym, v["exp_usd"], v["exp_kg"], v["imp_usd"], v["imp_kg"]))
        conn.commit()
        res[hs] = len(data)
    return res


def customs_payload(conn) -> dict:
    conn.execute(CUSTOMS_DDL)
    items = []
    for cat, name, hs in TRADE_ITEMS:
        rows = {r[0]: r for r in conn.execute(
            "SELECT ym, exp_usd, exp_kg, imp_usd, imp_kg FROM customs_monthly WHERE hs = ? ORDER BY ym", (hs,))}
        months = sorted(rows)
        series = []
        for ym in months:
            _, eu, ek, iu, ik = rows[ym]
            prev = rows.get(_ym_add(ym, -12))
            pm = rows.get(_ym_add(ym, -1))
            series.append({
                "ym": ym, "exp_usd": eu, "exp_kg": ek, "imp_usd": iu,
                "unit_price": round(eu / ek, 2) if ek else None,
                "yoy_pct": round((eu / prev[1] - 1) * 100, 1) if prev and prev[1] else None,
                "mom_pct": round((eu / pm[1] - 1) * 100, 1) if pm and pm[1] else None,
            })
        items.append({"category": cat, "name": name, "hs": hs, "series": series,
                      "latest": series[-1] if series else None})
    return {"items": items}
