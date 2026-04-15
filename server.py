"""
server.py  —  테마 트리맵 Flask 서버

의존성: pip install flask
실행:   python server.py
접속:   http://localhost:8080

동작 방식
  · 서버 시작 시 data.json 이 없거나 오늘 날짜가 아니면 data_fetcher.py 를 백그라운드에서 자동 실행
  · /            → index.html 즉시 반환 (수집 완료 전에도 더미 데이터로 동작)
  · /data.json   → 수집 완료된 data.json 반환 (미완료 시 503)
  · /api/status  → 수집 상태 JSON  {"state": "running"|"idle"|"error", ...}
  · /api/refresh → 수동 재수집 트리거 (GET/POST)
"""

from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from itertools import combinations as _comb
from pathlib import Path

# ── .env 파일 로더 (python-dotenv 미의존) ──────────────────────────────
# 로컬 개발 시 .env 파일이 있으면 os.environ 에 병합.
# Render/프로덕션은 서비스의 환경변수를 직접 사용하므로 .env 가 없어도 무해.
def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)  # 기존 환경변수 덮어쓰지 않음
    except Exception as exc:
        print(f"[.env 로드 실패] {exc}")

_load_dotenv(Path(__file__).parent / ".env")

# Render 등 UTC 서버에서도 KST 기준으로 날짜/시간 계산
KST = timezone(timedelta(hours=9))

def now_kst() -> datetime:
    return datetime.now(KST)

try:
    from flask import Flask, Response, jsonify, request, send_file
except ImportError:
    raise SystemExit(
        "Flask 설치 필요: pip install flask\n"
        "  또는: pip install -r requirements.txt"
    )

# APScheduler (선택 의존성 — pip install apscheduler)
try:
    from apscheduler.schedulers.background import BackgroundScheduler as _BgScheduler
    _SCHEDULER_OK = True
except ImportError:
    _BgScheduler  = None
    _SCHEDULER_OK = False

_scheduler = None

# ─────────────────────────────────────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────────────────────────────────────
BASE_DIR  = Path(__file__).parent
DATA_JSON = BASE_DIR / "data.json"
FETCHER   = BASE_DIR / "data_fetcher.py"
# Render 등 호스팅 환경은 PORT 환경변수를 주입하며 0.0.0.0 바인딩이 필요.
# 로컬 실행 시에는 127.0.0.1:8080 기본값 유지.
PORT      = int(os.environ.get("PORT", 8080))
HOST      = os.environ.get("HOST", "0.0.0.0" if "PORT" in os.environ else "127.0.0.1")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("server")

app = Flask(__name__, static_folder=str(BASE_DIR))
# 한글이 \uXXXX 로 이스케이프되지 않도록 (jsonify 응답)
app.config["JSON_AS_ASCII"] = False
try:
    app.json.ensure_ascii = False      # Flask >= 2.2
except Exception:
    pass


# ─────────────────────────────────────────────────────────────────────────────
# 수집 상태 (스레드 안전)
# ─────────────────────────────────────────────────────────────────────────────
_lock   = threading.Lock()
_status: dict = {
    "state":            "idle",   # "running" | "idle" | "error"
    "started_at":       None,
    "finished_at":      None,
    "error":            None,
    "interval_minutes": 5,
}

def _get() -> dict:
    with _lock:
        return dict(_status)

def _set(**kw):
    with _lock:
        _status.update(kw)


# ─────────────────────────────────────────────────────────────────────────────
# 장중 여부
# ─────────────────────────────────────────────────────────────────────────────
def is_market_hours() -> bool:
    """KST 기준 평일 09:00 ~ 15:30 여부"""
    now = now_kst()
    if now.weekday() >= 5:          # 토/일
        return False
    t = now.hour * 100 + now.minute
    return 900 <= t <= 1530


# ─────────────────────────────────────────────────────────────────────────────
# 데이터 신선도 확인
# ─────────────────────────────────────────────────────────────────────────────
def data_is_fresh() -> bool:
    """data.json 의 updated_at 이 KST 오늘 날짜인지 확인"""
    if not DATA_JSON.exists():
        return False
    try:
        data = json.loads(DATA_JSON.read_text(encoding="utf-8"))
        today = now_kst().strftime("%Y-%m-%d")
        return str(data.get("updated_at", "")).startswith(today)
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# 수집 실행 (별도 스레드)
# ─────────────────────────────────────────────────────────────────────────────
def _run_fetcher(force_market: bool = False):
    """data_fetcher.py 를 subprocess 로 실행하고 상태를 갱신한다."""
    if _get()["state"] == "running":
        log.info("data_fetcher 이미 실행 중 — 중복 실행 방지")
        return

    _set(state="running", started_at=now_kst().isoformat(), error=None)
    cmd = [sys.executable, str(FETCHER)]
    if force_market:
        cmd.append("--force-market")
    log.info("▶  data_fetcher.py 시작%s", "  [장중 갱신]" if force_market else "")

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=180,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()[-500:]
            raise RuntimeError(err)

        _set(state="idle", finished_at=now_kst().isoformat(), error=None)
        log.info("✓  data_fetcher.py 완료")

    except Exception as exc:
        _set(state="error", finished_at=now_kst().isoformat(), error=str(exc)[:500])
        log.error("✗  data_fetcher.py 실패: %s", exc)


def trigger_fetch(background: bool = True, force_market: bool = False) -> threading.Thread:
    t = threading.Thread(target=_run_fetcher, kwargs={"force_market": force_market},
                         daemon=True, name="fetcher")
    t.start()
    if not background:
        t.join()
    return t


# ─────────────────────────────────────────────────────────────────────────────
# 차트 계산 헬퍼
# ─────────────────────────────────────────────────────────────────────────────
def _get_trading_date() -> str:
    """data.json 의 actual_date 반환, 없으면 오늘 날짜 (YYYYMMDD)"""
    if DATA_JSON.exists():
        try:
            d = json.loads(DATA_JSON.read_text(encoding="utf-8"))
            ad = d.get("actual_date") or d.get("updated_at", "")[:10]
            if ad:
                return ad.replace("-", "")
        except Exception:
            pass
    return now_kst().strftime("%Y%m%d")


def _calc_bollinger(closes: list, period: int = 20, num_std: float = 2) -> dict:
    sma, upper, lower = [], [], []
    for i in range(len(closes)):
        if i < period - 1:
            sma.append(None); upper.append(None); lower.append(None)
        else:
            w = closes[i - period + 1 : i + 1]
            m = sum(w) / period
            s = math.sqrt(sum((x - m) ** 2 for x in w) / period)
            sma.append(round(m))
            upper.append(round(m + num_std * s))
            lower.append(round(m - num_std * s))
    return {"sma_20": sma, "upper": upper, "lower": lower}


def _calc_fibonacci(highs: list, lows: list) -> dict:
    h, lo = max(highs), min(lows)
    d = h - lo
    return {
        "0.0":   round(float(h)),
        "23.6":  round(float(h - d * 0.236)),
        "38.2":  round(float(h - d * 0.382)),
        "50.0":  round(float(h - d * 0.500)),
        "61.8":  round(float(h - d * 0.618)),
        "78.6":  round(float(h - d * 0.786)),
        "100.0": round(float(lo)),
    }


def _find_best_trendline(pivots: list, closes: list, direction: str):
    if len(pivots) < 2:
        return None
    best, best_score = None, -1
    for (i1, p1), (i2, p2) in _comb(pivots[-8:], 2):
        if i2 <= i1:
            continue
        slope = (p2 - p1) / (i2 - i1)
        intercept = p1 - slope * i1
        violations = 0
        for idx, c in enumerate(closes):
            proj = slope * idx + intercept
            tol  = proj * 0.005
            if direction == "support"    and c < proj - tol:
                violations += 1
            elif direction == "resistance" and c > proj + tol:
                violations += 1
        score = len(closes) - violations
        if score > best_score:
            best_score = score
            best = {"i1": i1, "p1": p1, "i2": i2, "p2": p2,
                    "slope": slope, "intercept": intercept}
    return best


def _calc_trendlines(highs: list, lows: list, closes: list, window: int = 10) -> dict:
    pivot_highs, pivot_lows = [], []
    for i in range(window, len(closes) - window):
        if highs[i] == max(highs[i - window : i + window + 1]):
            pivot_highs.append((i, highs[i]))
        if lows[i]  == min(lows[i  - window : i + window + 1]):
            pivot_lows.append((i, lows[i]))
    support    = _find_best_trendline(pivot_lows,  closes, "support")
    resistance = _find_best_trendline(pivot_highs, closes, "resistance")
    return {
        "support":     support,
        "resistance":  resistance,
        "pivot_lows":  [[i, p] for i, p in pivot_lows],
        "pivot_highs": [[i, p] for i, p in pivot_highs],
    }


def _generate_analysis(closes, volumes, bollinger, fibonacci, trendlines) -> dict:
    comments = []
    current  = closes[-1]

    # ── 볼린저 밴드 ──
    sma_val = bollinger["sma_20"][-1]
    if sma_val is not None:
        bb_upper = bollinger["upper"][-1]
        bb_lower = bollinger["lower"][-1]
        bb_width = (bb_upper - bb_lower) / sma_val * 100
        if current >= bb_upper:
            comments.append({"type": "bollinger", "signal": "과매수",
                "detail": f"현재가({current:,})가 볼린저 상단({bb_upper:,})을 돌파. 단기 과열 구간으로 차익실현 매물 출회 가능성."})
        elif current <= bb_lower:
            comments.append({"type": "bollinger", "signal": "과매도",
                "detail": f"현재가({current:,})가 볼린저 하단({bb_lower:,}) 이하. 기술적 반등 가능 구간이나 추세 하락 시 추가 하락 주의."})
        elif current > sma_val:
            comments.append({"type": "bollinger", "signal": "중립 상향",
                "detail": f"현재가({current:,})가 20일 이평선({sma_val:,}) 위에서 거래 중. 단기 상승 추세 유지."})
        else:
            comments.append({"type": "bollinger", "signal": "중립 하향",
                "detail": f"현재가({current:,})가 20일 이평선({sma_val:,}) 아래. 단기 약세 흐름."})
        if bb_width < 5:
            comments.append({"type": "bollinger", "signal": "스퀴즈",
                "detail": f"볼린저 밴드폭({bb_width:.1f}%)이 극도로 수축. 큰 변동성 확대 임박 가능성. 방향은 돌파 방향에 따라 결정."})
        elif bb_width > 20:
            comments.append({"type": "bollinger", "signal": "밴드 확장",
                "detail": f"볼린저 밴드폭({bb_width:.1f}%)이 크게 확장. 강한 추세 진행 중이나 추세 피로 누적 가능."})

    # ── 피보나치 ──
    fib_h   = fibonacci["0.0"]
    fib_236 = fibonacci["23.6"]
    fib_382 = fibonacci["38.2"]
    fib_500 = fibonacci["50.0"]
    fib_618 = fibonacci["61.8"]
    if current >= fib_h:
        comments.append({"type": "fibonacci", "signal": "신고가 근접",
            "detail": f"현재가({current:,})가 120일 고점({fib_h:,}) 이상. 신고가 돌파 시 추가 상승 모멘텀 기대."})
    elif current >= fib_236:
        comments.append({"type": "fibonacci", "signal": "약조정 구간",
            "detail": f"현재가({current:,})가 23.6% 되돌림({fib_236:,}) 위. 상승 추세 내 얕은 조정 수준으로 강세 유지."})
    elif current >= fib_382:
        comments.append({"type": "fibonacci", "signal": "일반 조정",
            "detail": f"현재가({current:,})가 38.2% 되돌림({fib_382:,}) 부근. 건전한 조정 구간. 이 레벨에서 지지 확인 시 재상승 가능."})
    elif current >= fib_500:
        comments.append({"type": "fibonacci", "signal": "중간 조정",
            "detail": f"현재가({current:,})가 50% 되돌림({fib_500:,}) 부근. 추세 전환 가능성이 높아지는 구간. 거래량 동반 여부 확인 필요."})
    elif current >= fib_618:
        comments.append({"type": "fibonacci", "signal": "깊은 조정",
            "detail": f"현재가({current:,})가 61.8% 되돌림({fib_618:,}) 부근. 황금 비율 지지선. 이탈 시 추세 전환으로 판단."})
    else:
        comments.append({"type": "fibonacci", "signal": "추세 전환",
            "detail": f"현재가({current:,})가 61.8% 되돌림({fib_618:,}) 하회. 기존 상승분 대부분 반납. 하락 추세 전환 가능성 높음."})

    # ── 추세선 ──
    if trendlines.get("support"):
        tl   = trendlines["support"]
        proj = round(tl["slope"] * (len(closes) - 1) + tl["intercept"])
        if current >= proj:
            comments.append({"type": "trendline", "signal": "지지선 위",
                "detail": f"현재가({current:,})가 상승 지지선({proj:,}) 위에 위치. 지지 구조 유효."})
        else:
            comments.append({"type": "trendline", "signal": "지지선 이탈",
                "detail": f"현재가({current:,})가 지지선({proj:,}) 하회. 추가 하락 압력 주의."})
    if trendlines.get("resistance"):
        tl   = trendlines["resistance"]
        proj = round(tl["slope"] * (len(closes) - 1) + tl["intercept"])
        if current < proj:
            comments.append({"type": "trendline", "signal": "저항선 하",
                "detail": f"현재가({current:,})가 저항선({proj:,}) 아래. 저항 돌파 시 추가 상승 가능."})
        else:
            comments.append({"type": "trendline", "signal": "저항선 돌파",
                "detail": f"현재가({current:,})가 저항선({proj:,})을 돌파. 강한 상승 신호."})

    # ── 거래량 ──
    if len(volumes) >= 20:
        avg   = sum(volumes[-20:]) / 20
        ratio = volumes[-1] / avg if avg > 0 else 0
        if ratio > 2.0:
            comments.append({"type": "volume", "signal": "거래량 급증",
                "detail": f"당일 거래량이 20일 평균 대비 {ratio:.1f}배. 세력 매집 또는 이벤트성 매매 가능성."})
        elif ratio < 0.3:
            comments.append({"type": "volume", "signal": "거래량 급감",
                "detail": f"당일 거래량이 20일 평균 대비 {ratio:.1f}배로 극도로 위축. 관망세 또는 바닥 다지기 구간."})

    # ── 종합 ──
    bull = {"과매도", "약조정 구간", "신고가 근접", "거래량 급증", "중립 상향", "지지선 위", "저항선 돌파"}
    bear = {"과매수", "깊은 조정", "추세 전환", "중립 하향", "지지선 이탈", "밴드 확장"}
    n_bull = sum(1 for c in comments if c["signal"] in bull)
    n_bear = sum(1 for c in comments if c["signal"] in bear)
    if n_bull > n_bear:
        summary = "종합: 기술적 지표가 단기 긍정적 신호를 시사. 다만 개별 지표 확인 필요."
    elif n_bear > n_bull:
        summary = "종합: 기술적 지표가 단기 부정적 신호 우세. 리스크 관리 필요."
    else:
        summary = "종합: 기술적 지표 혼조. 방향성 확인 후 대응 권장."

    return {"comments": comments, "summary": summary}


# ─────────────────────────────────────────────────────────────────────────────
# 라우트
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_file(BASE_DIR / "index.html")


@app.route("/data.json")
def route_data_json():
    if not DATA_JSON.exists():
        st = _get()
        return jsonify({
            "error":   "data.json 아직 준비 중입니다.",
            "state":   st["state"],
            "started": st["started_at"],
        }), 503
    return Response(
        DATA_JSON.read_text(encoding="utf-8"),
        content_type="application/json; charset=utf-8",
    )


@app.route("/api/status")
def api_status():
    st = _get()
    st["data_fresh"]  = data_is_fresh()
    st["data_exists"] = DATA_JSON.exists()
    st["market_open"] = is_market_hours()
    return jsonify(st)


@app.route("/api/refresh", methods=["GET", "POST"])
def api_refresh():
    if _get()["state"] == "running":
        return jsonify({"ok": False, "message": "이미 수집 중입니다."}), 409
    trigger_fetch(background=True, force_market=is_market_hours())
    return jsonify({"ok": True, "message": "수집을 시작했습니다."})


@app.route("/api/interval/<int:minutes>", methods=["POST"])
def api_set_interval(minutes: int):
    if not 1 <= minutes <= 60:
        return jsonify({"ok": False, "message": "1~60분 범위만 가능"}), 400
    _set(interval_minutes=minutes)
    if _scheduler is not None and _scheduler.running:
        _scheduler.reschedule_job("market_update", trigger="interval", minutes=minutes)
        log.info("갱신 주기 변경: %d분", minutes)
    return jsonify({"ok": True, "interval_minutes": minutes})


@app.route("/api/rank_change/<int:minutes_ago>")
def api_rank_change(minutes_ago: int):
    ranking_file = BASE_DIR / "cache" / "ranking_history.json"
    if not ranking_file.exists():
        return jsonify({})
    try:
        loaded = json.loads(ranking_file.read_text(encoding="utf-8"))
    except Exception:
        return jsonify({})

    # 구형식({previous,current})과 신형식(list) 모두 지원
    if isinstance(loaded, dict):
        curr = loaded.get("current",  {})
        prev = loaded.get("previous", {})
    elif isinstance(loaded, list) and loaded:
        curr = loaded[-1]["ranking"]
        target     = now_kst() - timedelta(minutes=minutes_ago)
        target_str = target.strftime("%Y-%m-%d %H:%M:%S")
        prev = next(
            (s["ranking"] for s in reversed(loaded) if s["timestamp"] <= target_str),
            loaded[0]["ranking"]
        )
    else:
        return jsonify({})

    changes = {}
    for name, cr in curr.items():
        pr = prev.get(name)
        changes[name] = (pr - cr) if pr else 0
    return jsonify(changes)


@app.route("/api/themes", methods=["GET"])
def api_themes_get():
    themes_file = BASE_DIR / "themes_mapping.json"
    if not themes_file.exists():
        return jsonify([])
    return Response(
        themes_file.read_text(encoding="utf-8"),
        content_type="application/json; charset=utf-8",
    )


@app.route("/api/themes", methods=["POST"])
def api_themes_post():
    data = request.get_json()
    if not isinstance(data, list):
        return jsonify({"error": "invalid data"}), 400
    themes_file = BASE_DIR / "themes_mapping.json"
    themes_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return jsonify({"ok": True})


@app.route("/api/stock_search")
def api_stock_search():
    """
    국내 종목 검색. 데이터 소스 우선순위:
      1) naver_universe (4,000+ 종목 전체) — Phase 10
      2) stock_master (테마 구성 134종목) — 폴백
    대소문자 무시 부분 일치. 정확 일치 → 접두 일치 → 부분 일치 순 정렬.
    """
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])
    ql = q.lower()

    def _score(code: str, name: str) -> int:
        """낮을수록 앞에 노출. 0=정확일치, 1=접두, 2=부분."""
        cl = code.lower()
        nl = (name or "").lower()
        if cl == ql or nl == ql: return 0
        if cl.startswith(ql) or nl.startswith(ql): return 1
        return 2

    results: list[tuple[int, str, str]] = []

    # 1) naver_universe 우선
    uni = _load_naver_universe()
    stocks = (uni or {}).get("stocks") or {}
    if stocks:
        for code, rec in stocks.items():
            name = rec.get("name") or ""
            if ql in code.lower() or ql in name.lower():
                results.append((_score(code, name), code, name))
    else:
        # 2) 폴백: 테마 기반 stock_master
        import glob as _glob
        masters = sorted(
            _glob.glob(str(BASE_DIR / "cache" / "stock_master_*.json")), reverse=True
        )
        if masters:
            try:
                master: dict = json.loads(open(masters[0], encoding="utf-8").read())
                for code, name in master.items():
                    if ql in code.lower() or ql in (name or "").lower():
                        results.append((_score(code, name), code, name))
            except Exception:
                pass

    results.sort(key=lambda x: (x[0], x[1]))
    return jsonify([{"code": c, "name": n} for _, c, n in results[:10]])


def _get_krx_all_stocks_cached() -> dict:
    """
    KRX Open API 로 KOSPI+KOSDAQ 전 종목 시세를 조회, 종목코드-레코드 dict 로 반환.
    일 1회 호출 → cache/krx_all_stocks_{today}.json.
    구독되지 않은 경우 {} 반환.

    각 레코드는 KRX 응답 필드를 그대로 보관 (ISU_SRT_CD, ISU_ABBRV, TDD_CLSPRC,
    FLUC_RT, ACC_TRDVAL, MKTCAP 등). 호출 측에서 필요 키만 추출.
    """
    today      = _get_trading_date()
    cache_file = BASE_DIR / "cache" / f"krx_all_stocks_{today}.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    try:
        import krx_api
    except ImportError:
        return {}

    if not krx_api.has_api_key():
        return {}

    rows: list = []
    k = krx_api.krx_all_stocks_kospi(today)
    if k: rows.extend(k)
    q = krx_api.krx_all_stocks_kosdaq(today)
    if q: rows.extend(q)

    if not rows:
        return {}

    # code → record
    out = {}
    for r in rows:
        code = r.get("ISU_SRT_CD") or r.get("ISU_CD") or r.get("isuSrtCd")
        if code:
            out[str(code).strip()] = r

    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def _krx_get_float(row: dict, *keys) -> float | None:
    """KRX 응답에서 숫자 필드 추출. 콤마 포함 문자열도 처리."""
    for k in keys:
        v = row.get(k)
        if v is None or v == "":
            continue
        try:
            return float(str(v).replace(",", ""))
        except ValueError:
            continue
    return None


def _get_stock_name(code: str) -> str | None:
    """
    종목 코드로부터 이름 조회. data.json 테마 → cache/stock_master_*.json 순서로 탐색.
    """
    code = (code or "").strip()
    if not code:
        return None
    if DATA_JSON.exists():
        try:
            data = json.loads(DATA_JSON.read_text(encoding="utf-8"))
            for theme in data.get("themes", []):
                for s in theme.get("stocks", []):
                    if s.get("code") == code:
                        return s.get("name") or code
        except Exception:
            pass
    # stock_master cache fallback
    import glob as _glob
    masters = sorted(
        _glob.glob(str(BASE_DIR / "cache" / "stock_master_*.json")), reverse=True
    )
    for m in masters:
        try:
            with open(m, encoding="utf-8") as f:
                master = json.load(f)
            if code in master:
                return master[code]
        except Exception:
            continue
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 뉴스 검색 (네이버 검색 API)  —  Phase 10
# ─────────────────────────────────────────────────────────────────────────────
def _format_time_ago(pub_date_str: str) -> str:
    """RFC 2822 (pubDate) → 'N분 전/N시간 전/N일 전' 한국어 표기."""
    try:
        from email.utils import parsedate_to_datetime
        pub_dt = parsedate_to_datetime(pub_date_str)
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        delta = now_kst() - pub_dt.astimezone(KST)
        mins  = int(delta.total_seconds() / 60)
        if mins < 1:   return "방금"
        if mins < 60:  return f"{mins}분 전"
        hrs = mins // 60
        if hrs < 24:   return f"{hrs}시간 전"
        days = hrs // 24
        return f"{days}일 전"
    except Exception:
        return ""


_NEWS_SOURCE_MAP = {
    # 주요 경제/종합지
    "hankyung.com": "한국경제",       "mk.co.kr":      "매일경제",
    "sedaily.com":  "서울경제",       "edaily.co.kr":  "이데일리",
    "mt.co.kr":     "머니투데이",     "news1.kr":      "뉴스1",
    "newsis.com":   "뉴시스",         "yna.co.kr":     "연합뉴스",
    "yonhapnewstv.co.kr": "연합뉴스TV",
    "chosun.com":   "조선일보",       "donga.com":     "동아일보",
    "joongang.co.kr": "중앙일보",     "khan.co.kr":    "경향신문",
    "heraldcorp.com": "헤럴드경제",   "fnnews.com":    "파이낸셜뉴스",
    "etnews.com":   "전자신문",       "thebell.co.kr": "더벨",
    "bloter.net":   "블로터",         "businesspost.co.kr": "비즈니스포스트",
    "infostock.co.kr": "인포스탁데일리", "etoday.co.kr":    "이투데이",
    "ajunews.com":  "아주경제",       "biz.chosun.com": "조선비즈",
    "dt.co.kr":     "디지털타임스",   "asiae.co.kr":   "아시아경제",
    "einfomax.co.kr": "연합인포맥스", "newspim.com":   "뉴스핌",
    "tf.co.kr":     "더팩트",         "ebn.co.kr":     "EBN",
    "smedaily.co.kr": "SME데일리",    "pinpointnews.co.kr": "핀포인트뉴스",
    "niceeconomy.co.kr": "나이스경제", "lcnews.co.kr": "로컬뉴스",
    "joongangenews.com": "중앙이뉴스",
}

def _news_source(url: str) -> str:
    u = (url or "").lower()
    for domain, name in _NEWS_SOURCE_MAP.items():
        if domain in u:
            return name
    return "기타"


def _strip_html(s: str) -> str:
    import re as _re
    return _re.sub(r"<[^>]+>", "", s or "")


# ─────────────────────────────────────────────────────────────────────────────
# US MARKET (Phase 14)  —  S&P 500 via yfinance + Wikipedia
# ─────────────────────────────────────────────────────────────────────────────
def _sp500_tickers() -> list[dict]:
    """
    S&P 500 구성 종목 리스트 (Wikipedia).
    캐시: cache/sp500_tickers.json, TTL 7일.
    Returns: [{symbol, name, sector, sub_industry}, ...]
    """
    cache_file = BASE_DIR / "cache" / "sp500_tickers.json"
    if cache_file.exists():
        try:
            age_days = (now_kst().timestamp() - cache_file.stat().st_mtime) / 86400
            if age_days < 7:
                return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    try:
        import pandas as _pd
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        tables = _pd.read_html(url, storage_options={"User-Agent": "Mozilla/5.0"})
    except Exception as exc:
        print(f"[S&P500] Wikipedia 파싱 실패: {exc}")
        return []

    if not tables:
        return []

    df = tables[0]
    out: list[dict] = []
    for _, row in df.iterrows():
        try:
            sym = str(row.get("Symbol", "")).replace(".", "-")  # BRK.B → BRK-B
            if not sym or sym == "nan":
                continue
            out.append({
                "symbol":       sym,
                "name":         str(row.get("Security", sym)),
                "sector":       str(row.get("GICS Sector", "")),
                "sub_industry": str(row.get("GICS Sub-Industry", "")),
            })
        except Exception:
            continue

    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    print(f"[S&P500] Wikipedia 에서 {len(out)} 종목 로드")
    return out


def _is_us_market_hours() -> bool:
    """미국 장중 여부 (대략 KST 22:30~06:00)."""
    now = now_kst()
    if now.weekday() >= 5 and now.weekday() != 0:  # 월~금의 장이 KST 기준 토요일까지 걸침
        pass
    t = now.hour * 100 + now.minute
    return (t >= 2230) or (t <= 600)


def _format_usd_cap(value) -> str:
    """시가총액 $ 단위 포맷."""
    if not value:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if v >= 1e12: return f"${v/1e12:.2f}T"
    if v >= 1e9:  return f"${v/1e9:.1f}B"
    if v >= 1e6:  return f"${v/1e6:.0f}M"
    return f"${v:,.0f}"


def _fetch_us_market_data(force: bool = False) -> dict:
    """
    S&P 500 전 종목 당일 시세 batch 수집 + 섹터별 집계.
    캐시: cache/us_market_{YYYYMMDD_KST}.json
    TTL: 미국 장중 15분 / 장외 24시간.
    부분 빌드(< 400종목) 감지 시 자동 재빌드.
    """
    today = now_kst().strftime("%Y%m%d")
    cache_file = BASE_DIR / "cache" / f"us_market_{today}.json"
    MIN_STOCKS = 400   # S&P500 정상 빌드 최소 기준 (yfinance 실패 허용치 ~100)
    if cache_file.exists() and not force:
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            n_cached = len(cached.get("all_stocks") or [])
            age_min = (now_kst().timestamp() - cache_file.stat().st_mtime) / 60
            ttl = 15 if _is_us_market_hours() else 1440
            if n_cached >= MIN_STOCKS and age_min < ttl:
                return cached
            if n_cached < MIN_STOCKS:
                print(f"[US] 부분 빌드 감지 ({n_cached}종목) — 재빌드")
        except Exception:
            pass

    try:
        import yfinance as _yf
    except ImportError:
        return {"error": "yfinance 미설치", "sectors": [], "all_stocks": []}

    tickers = _sp500_tickers()
    if not tickers:
        return {"error": "S&P 500 리스트 없음", "sectors": [], "all_stocks": []}

    symbols = [t["symbol"] for t in tickers]
    by_sym = {t["symbol"]: t for t in tickers}

    stocks: list[dict] = []
    print(f"[US] yfinance batch download, {len(symbols)} 종목…")

    # 50 종목씩 청크 (rate-limit 완화) + 청크간 sleep
    chunk_size = 50
    failed_chunks: list[list[str]] = []
    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i:i + chunk_size]
        try:
            df = _yf.download(
                " ".join(chunk),
                period="5d",
                interval="1d",
                group_by="ticker",
                threads=True,
                progress=False,
                auto_adjust=True,
            )
        except Exception as exc:
            print(f"[US chunk {i}] fail: {exc}")
            failed_chunks.append(chunk)
            continue
        if df is None or df.empty:
            print(f"[US chunk {i}] empty result — retry later")
            failed_chunks.append(chunk)
            time.sleep(0.3)
            continue

        for sym in chunk:
            try:
                if sym not in df.columns.get_level_values(0):
                    continue
                t_df = df[sym].dropna(how="all")
                if len(t_df) < 1:
                    continue
                cur = float(t_df["Close"].iloc[-1])
                prev = float(t_df["Close"].iloc[-2]) if len(t_df) >= 2 else cur
                chg_pct = round((cur / prev - 1) * 100, 2) if prev else 0.0
                vol = int(t_df["Volume"].iloc[-1] or 0)
                info = by_sym[sym]
                stocks.append({
                    "symbol":     sym,
                    "name":       info["name"],
                    "sector":     info["sector"],
                    "price":      round(cur, 2),
                    "prev_close": round(prev, 2),
                    "change_pct": chg_pct,
                    "volume":     vol,
                    "volume_mn":  round(vol * cur / 1_000_000, 1),    # $M traded
                })
            except Exception:
                continue
        time.sleep(0.3)   # 청크 간 rate-limit 완화

    # 실패 청크 개별 재시도 (1회)
    if failed_chunks:
        retry_syms = [s for chunk in failed_chunks for s in chunk]
        print(f"[US] 실패 청크 재시도: {len(retry_syms)} 종목 (개별)")
        for sym in retry_syms:
            try:
                t_df = _yf.Ticker(sym).history(period="5d", auto_adjust=True)
                if t_df is None or t_df.empty:
                    continue
                cur = float(t_df["Close"].iloc[-1])
                prev = float(t_df["Close"].iloc[-2]) if len(t_df) >= 2 else cur
                chg_pct = round((cur / prev - 1) * 100, 2) if prev else 0.0
                vol = int(t_df["Volume"].iloc[-1] or 0)
                info = by_sym[sym]
                stocks.append({
                    "symbol":     sym,
                    "name":       info["name"],
                    "sector":     info["sector"],
                    "price":      round(cur, 2),
                    "prev_close": round(prev, 2),
                    "change_pct": chg_pct,
                    "volume":     vol,
                    "volume_mn":  round(vol * cur / 1_000_000, 1),
                })
            except Exception as exc:
                print(f"[US retry] {sym}: {exc}")
            time.sleep(0.05)

    if not stocks:
        return {"error": "yfinance 응답 없음", "sectors": [], "all_stocks": []}

    print(f"[US] yfinance 최종: {len(stocks)}/{len(symbols)} 종목 수집")

    # 섹터별 그룹
    sectors_map: dict = {}
    for s in stocks:
        sec = s["sector"] or "Unknown"
        bucket = sectors_map.setdefault(sec, {
            "name":             sec,
            "stocks":           [],
            "weighted_avg_pct": 0.0,
            "stock_count":      0,
        })
        bucket["stocks"].append(s)

    for bucket in sectors_map.values():
        tot = sum(s["volume_mn"] for s in bucket["stocks"]) or 1
        bucket["weighted_avg_pct"] = round(
            sum(s["change_pct"] * s["volume_mn"] for s in bucket["stocks"]) / tot, 2
        )
        bucket["stock_count"] = len(bucket["stocks"])
        bucket["stocks"].sort(key=lambda x: abs(x["change_pct"]), reverse=True)

    # 섹터 리스트 (등락률 내림차순)
    sector_list = sorted(
        sectors_map.values(),
        key=lambda b: abs(b["weighted_avg_pct"]),
        reverse=True,
    )

    result = {
        "updated_at":  now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "market":      "US",
        "total_stocks": len(stocks),
        "sectors":     sector_list,
        "all_stocks":  stocks,
    }
    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(result, ensure_ascii=False),
                          encoding="utf-8")
    print(f"[US] 완료: {len(stocks)} stocks, {len(sector_list)} sectors")
    return result


def _build_us_market_background():
    """
    서버 부팅 시 백그라운드로 S&P 500 전 종목 batch 를 돌린다.
    초회 빌드 ~180s 소요 (yfinance). 정상 캐시(≥400종목) 존재 시 스킵.
    부분 빌드는 감지하여 재실행.
    """
    today = now_kst().strftime("%Y%m%d")
    out_file = BASE_DIR / "cache" / f"us_market_{today}.json"
    if out_file.exists():
        try:
            cached = json.loads(out_file.read_text(encoding="utf-8"))
            n = len(cached.get("all_stocks") or [])
            if n >= 400:
                log.info("us_market 캐시 정상 (%d종목) — 스킵 (%s)", n, out_file.name)
                return
            log.warning("us_market 부분 빌드 감지 (%d종목) — 재빌드 강제", n)
        except Exception as exc:
            log.warning("us_market 캐시 읽기 실패 — 재빌드: %s", exc)
    lock = out_file.with_suffix(".lock")
    if lock.exists():
        return
    try:
        lock.touch()
        log.info("▶  S&P 500 market 백그라운드 빌드 시작 (~3분)")
        _fetch_us_market_data(force=True)
        log.info("✓  S&P 500 market 빌드 완료")
    except Exception as exc:
        log.error("S&P 500 빌드 실패: %s", exc)
    finally:
        try: lock.unlink()
        except Exception: pass


@app.route("/api/us/market")
def api_us_market():
    """
    S&P 500 전 종목 시세 + 섹터 집계. 캐시 우선. 캐시 없으면 빌드 중 표시
    (프론트가 polling 으로 재시도).
    """
    today = now_kst().strftime("%Y%m%d")
    out_file = BASE_DIR / "cache" / f"us_market_{today}.json"
    if out_file.exists():
        try:
            return Response(
                out_file.read_text(encoding="utf-8"),
                content_type="application/json; charset=utf-8",
            )
        except Exception:
            pass
    # 캐시 없음 → 백그라운드 빌드 킥
    threading.Thread(target=_build_us_market_background,
                     daemon=True, name="us-market-build").start()
    return jsonify({
        "building":    True,
        "message":     "S&P 500 batch 빌드 중입니다. 약 2~3분 후 다시 시도하세요.",
        "sectors":     [],
        "all_stocks":  [],
        "total_stocks": 0,
    }), 202


@app.route("/api/us/search")
def api_us_search():
    """S&P 500 종목 검색 (심볼/이름 부분 일치)."""
    q = (request.args.get("q") or "").strip()
    if len(q) < 1:
        return jsonify([])
    tickers = _sp500_tickers()
    q_up = q.upper()
    q_lo = q.lower()
    results = []
    for t in tickers:
        if q_up in t["symbol"].upper() or q_lo in t["name"].lower():
            results.append({
                "code":   t["symbol"],
                "name":   t["name"],
                "sector": t["sector"],
            })
        if len(results) >= 10:
            break
    return jsonify(results)


@app.route("/api/us/chart/<symbol>")
def api_us_chart(symbol: str):
    """
    미국 종목 차트 (yfinance history).
    응답 스키마는 /api/chart 와 호환 → 프론트의 _drawCandles 등 무수정 재사용.
    """
    import re as _re
    if not _re.fullmatch(r"[A-Z][A-Z0-9\-\.]{0,9}", symbol.upper()):
        return jsonify({"error": "잘못된 심볼"}), 400
    symbol = symbol.upper()

    try:
        days = int(request.args.get("days", "180"))
    except ValueError:
        days = 180
    days = max(7, min(3650, days))

    today_kst = now_kst().strftime("%Y%m%d")
    cache_file = BASE_DIR / "cache" / f"us_chart_{symbol}_{days}d_{today_kst}.json"
    if cache_file.exists():
        try:
            return Response(
                cache_file.read_text(encoding="utf-8"),
                content_type="application/json; charset=utf-8",
            )
        except Exception:
            pass

    try:
        import yfinance as _yf
    except ImportError:
        return jsonify({"error": "yfinance 미설치"}), 500

    period_map = {30: "1mo", 90: "3mo", 180: "6mo",
                  365: "1y", 730: "2y", 1095: "5y", 1825: "5y", 3650: "10y"}
    period = "6mo"
    for d in sorted(period_map):
        if days <= d:
            period = period_map[d]; break

    try:
        t = _yf.Ticker(symbol)
        df = t.history(period=period, auto_adjust=True)
    except Exception as exc:
        return jsonify({"error": f"yfinance 실패: {exc}"}), 502

    if df is None or df.empty:
        return jsonify({"error": "데이터 없음"}), 404

    dates   = [d.strftime("%Y-%m-%d") for d in df.index]
    opens   = [round(float(v), 2) for v in df["Open"].tolist()]
    highs   = [round(float(v), 2) for v in df["High"].tolist()]
    lows    = [round(float(v), 2) for v in df["Low"].tolist()]
    closes  = [round(float(v), 2) for v in df["Close"].tolist()]
    volumes = [int(v) for v in df["Volume"].tolist()]

    bollinger  = _calc_bollinger(closes)
    fibonacci  = _calc_fibonacci(highs, lows)
    trendlines = _calc_trendlines(highs, lows, closes)
    analysis   = _generate_analysis(closes, volumes, bollinger, fibonacci, trendlines)

    name = symbol
    try:
        info = t.info or {}
        name = info.get("shortName") or info.get("longName") or symbol
    except Exception:
        pass

    result = {
        "code":       symbol,
        "name":       name,
        "market":     "US",
        "currency":   "USD",
        "days":       days,
        "dates":      dates,
        "open":       opens, "high": highs, "low": lows, "close": closes,
        "volume":     volumes,
        "bollinger":  bollinger,
        "fibonacci":  fibonacci,
        "trendlines": trendlines,
        "analysis":   analysis,
    }
    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(result, ensure_ascii=False),
                          encoding="utf-8")
    return jsonify(result)


@app.route("/api/us/news/<symbol>")
def api_us_news(symbol: str):
    """미국 종목 뉴스 (yfinance Ticker.news, 영어 원문). 30분 캐시."""
    import re as _re
    if not _re.fullmatch(r"[A-Z][A-Z0-9\-\.]{0,9}", symbol.upper()):
        return jsonify({"error": "잘못된 심볼"}), 400
    symbol = symbol.upper()

    cache_file = BASE_DIR / "cache" / f"us_news_{symbol}.json"
    if cache_file.exists():
        try:
            age_min = (now_kst().timestamp() - cache_file.stat().st_mtime) / 60
            if age_min < 30:
                return Response(
                    cache_file.read_text(encoding="utf-8"),
                    content_type="application/json; charset=utf-8",
                )
        except Exception:
            pass

    try:
        import yfinance as _yf
    except ImportError:
        return jsonify({"error": "yfinance 미설치"}), 500

    try:
        news = _yf.Ticker(symbol).news or []
    except Exception as exc:
        return jsonify({"error": f"yfinance 뉴스 실패: {exc}", "items": []}), 502

    def _time_ago_utc(ts: int) -> str:
        if not ts:
            return ""
        from datetime import timezone as _tz
        dt = datetime.fromtimestamp(ts, tz=_tz.utc)
        delta = datetime.now(_tz.utc) - dt
        mins = int(delta.total_seconds() / 60)
        if mins < 1:  return "방금"
        if mins < 60: return f"{mins}분 전"
        hrs = mins // 60
        if hrs < 24:  return f"{hrs}시간 전"
        return f"{hrs // 24}일 전"

    items = []
    for a in news[:15]:
        # yfinance 뉴스 스키마가 최근 버전에서 {content: {...}} 로 감싸짐
        c = a.get("content") or a
        title = c.get("title") or ""
        link  = (c.get("clickThroughUrl") or {}).get("url") if isinstance(c.get("clickThroughUrl"), dict) else c.get("link", "")
        if not link:
            link = (c.get("canonicalUrl") or {}).get("url", "") if isinstance(c.get("canonicalUrl"), dict) else ""
        provider = c.get("provider") or {}
        source = provider.get("displayName") if isinstance(provider, dict) else c.get("publisher", "")
        pub_date = c.get("pubDate") or ""
        # providerPublishTime (legacy) 또는 pubDate (new ISO)
        time_ago = ""
        if c.get("providerPublishTime"):
            time_ago = _time_ago_utc(c["providerPublishTime"])
        elif pub_date:
            try:
                from datetime import timezone as _tz
                dt = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                delta = datetime.now(_tz.utc) - dt
                mins = int(delta.total_seconds() / 60)
                if mins < 60: time_ago = f"{mins}분 전"
                elif mins < 1440: time_ago = f"{mins // 60}시간 전"
                else: time_ago = f"{mins // 1440}일 전"
            except Exception:
                pass

        thumbnail = ""
        thumb = c.get("thumbnail")
        if isinstance(thumb, dict):
            res = thumb.get("resolutions") or []
            if res and isinstance(res, list):
                thumbnail = res[0].get("url", "")
            elif thumb.get("originalUrl"):
                thumbnail = thumb["originalUrl"]

        if title and link:
            items.append({
                "title":     title,
                "link":      link,
                "source":    source or "",
                "thumbnail": thumbnail,
                "pubDate":   pub_date,
                "timeAgo":   time_ago,
            })

    result = {"symbol": symbol, "count": len(items), "items": items}
    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return jsonify(result)


@app.route("/api/us/financial/<symbol>")
def api_us_financial(symbol: str):
    """미국 종목 재무 요약 (yfinance Ticker.info). 24시간 캐시."""
    import re as _re
    if not _re.fullmatch(r"[A-Z][A-Z0-9\-\.]{0,9}", symbol.upper()):
        return jsonify({"error": "잘못된 심볼"}), 400
    symbol = symbol.upper()

    cache_file = BASE_DIR / "cache" / f"us_fin_{symbol}.json"
    if cache_file.exists():
        try:
            age_hr = (now_kst().timestamp() - cache_file.stat().st_mtime) / 3600
            if age_hr < 24:
                return Response(
                    cache_file.read_text(encoding="utf-8"),
                    content_type="application/json; charset=utf-8",
                )
        except Exception:
            pass

    try:
        import yfinance as _yf
    except ImportError:
        return jsonify({"error": "yfinance 미설치"}), 500

    try:
        info = _yf.Ticker(symbol).info or {}
    except Exception as exc:
        return jsonify({"error": f"yfinance info 실패: {exc}"}), 502

    def _pct(v):
        if v is None: return None
        try: return round(float(v) * 100, 2)
        except (TypeError, ValueError): return None

    result = {
        "symbol":         symbol,
        "name":           info.get("shortName") or info.get("longName") or symbol,
        "sector":         info.get("sector"),
        "industry":       info.get("industry"),
        "per":            info.get("trailingPE"),
        "forward_per":    info.get("forwardPE"),
        "pbr":            info.get("priceToBook"),
        "roe":            _pct(info.get("returnOnEquity")),
        "profit_margin":  _pct(info.get("profitMargins")),
        "dividend_yield": info.get("dividendYield"),  # yfinance returns percent already
        "market_cap":     info.get("marketCap"),
        "market_cap_str": _format_usd_cap(info.get("marketCap")),
        "revenue":        info.get("totalRevenue"),
        "revenue_str":    _format_usd_cap(info.get("totalRevenue")),
        "target_price":   info.get("targetMeanPrice"),
        "recommendation": info.get("recommendationKey"),
        "beta":           info.get("beta"),
        "w52_high":       info.get("fiftyTwoWeekHigh"),
        "w52_low":        info.get("fiftyTwoWeekLow"),
        "current_price":  info.get("currentPrice") or info.get("regularMarketPrice"),
    }
    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return jsonify(result)


@app.route("/api/us/screener")
def api_us_screener():
    """미국 스크리너 — _fetch_us_market_data 결과에서 서버-사이드 필터."""
    args = request.args
    min_change = float(args.get("min_change", "-100") or -100)
    max_change = float(args.get("max_change", "100")  or 100)
    min_volume = float(args.get("min_volume", "0")    or 0)   # $M
    sector     = (args.get("sector") or "").strip()
    q          = (args.get("q") or "").strip()

    data = _fetch_us_market_data()
    if "error" in data:
        return jsonify({"error": data["error"], "count": 0, "stocks": []}), 503

    results = []
    q_up = q.upper()
    q_lo = q.lower()
    for s in data.get("all_stocks", []):
        if s["change_pct"] < min_change or s["change_pct"] > max_change: continue
        if s["volume_mn"] < min_volume: continue
        if sector and s.get("sector") != sector: continue
        if q and (q_up not in s["symbol"].upper() and q_lo not in s["name"].lower()):
            continue
        results.append(s)

    results.sort(key=lambda r: r["volume_mn"], reverse=True)
    return jsonify({
        "count":           len(results),
        "stocks":          results[:200],
        "universe_source": "sp500_yfinance",
        "universe_size":   data.get("total_stocks", 0),
        "fetched_at":      data.get("updated_at"),
    })


@app.route("/api/us/price/<symbol>")
def api_us_price(symbol: str):
    """미국 종목 현재가 — us_market 캐시에서 조회."""
    import re as _re
    if not _re.fullmatch(r"[A-Z][A-Z0-9\-\.]{0,9}", symbol.upper()):
        return jsonify({"error": "잘못된 심볼"}), 400
    symbol = symbol.upper()
    data = _fetch_us_market_data()
    for s in data.get("all_stocks", []):
        if s["symbol"] == symbol:
            chg = (s["price"] - s["prev_close"]) if s.get("prev_close") else None
            return jsonify({
                "code":       symbol,
                "name":       s["name"],
                "price":      s["price"],
                "prev_close": s.get("prev_close"),
                "change":     round(chg, 2) if chg is not None else None,
                "change_pct": s["change_pct"],
                "volume_mn":  s["volume_mn"],
                "source":     "us_market_cache",
                "fetched_at": data.get("updated_at"),
            })
    return jsonify({"error": "종목 없음"}), 404


@app.route("/api/news/<code>")
def api_news(code: str):
    """
    종목 관련 뉴스 검색 (네이버 검색 API).
    캐시: cache/news_{code}.json, TTL 1시간.
    NAVER_CLIENT_ID/SECRET 미설정 시 503 + hint.
    """
    import re as _re
    if not _re.fullmatch(r"\d{6}", code):
        return jsonify({"error": "잘못된 종목코드"}), 400

    stock_name = _get_stock_name(code)
    if not stock_name:
        return jsonify({"error": "종목 없음"}), 404

    cache_file = BASE_DIR / "cache" / f"news_{code}.json"
    if cache_file.exists():
        try:
            age_min = (now_kst().timestamp() - cache_file.stat().st_mtime) / 60
            if age_min < 60:
                return Response(
                    cache_file.read_text(encoding="utf-8"),
                    content_type="application/json; charset=utf-8",
                )
        except Exception:
            pass

    client_id     = os.environ.get("NAVER_CLIENT_ID", "")
    client_secret = os.environ.get("NAVER_CLIENT_SECRET", "")
    if not (client_id and client_secret):
        return jsonify({
            "error": "네이버 검색 API 키 미설정",
            "hint":  ".env 또는 환경변수에 NAVER_CLIENT_ID, NAVER_CLIENT_SECRET 추가",
        }), 503

    try:
        import requests as _rq
        res = _rq.get(
            "https://openapi.naver.com/v1/search/news.json",
            headers={
                "X-Naver-Client-Id":     client_id,
                "X-Naver-Client-Secret": client_secret,
            },
            params={
                "query":   f"{stock_name} 주가",
                "display": 15,
                "sort":    "date",
            },
            timeout=8,
        )
        res.raise_for_status()
        data = res.json()
    except Exception as exc:
        return jsonify({"error": f"네이버 API 호출 실패: {exc}"}), 502

    items_in = data.get("items", []) or []
    items = []
    for it in items_in:
        link = it.get("originallink") or it.get("link") or ""
        items.append({
            "title":       _strip_html(it.get("title", "")),
            "description": _strip_html(it.get("description", "")),
            "link":        link,
            "source":      _news_source(link),
            "pubDate":     it.get("pubDate", ""),
            "timeAgo":     _format_time_ago(it.get("pubDate", "")),
        })

    result = {
        "code":  code,
        "name":  stock_name,
        "count": len(items),
        "items": items,
    }
    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify(result)


# ─────────────────────────────────────────────────────────────────────────────
# 애널 리포트 마이닝 (네이버 금융 리서치 + PyPDF2)  —  Phase 10
# ─────────────────────────────────────────────────────────────────────────────
def _crawl_naver_research(code: str) -> list[dict]:
    """네이버 금융 리서치 페이지에서 해당 종목의 리포트 목록 스크랩."""
    import requests as _rq
    from bs4 import BeautifulSoup

    url = "https://finance.naver.com/research/company_list.naver"
    res = _rq.get(
        url,
        params={"searchType": "itemCode", "itemCode": code},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10,
    )
    res.encoding = "euc-kr"
    soup = BeautifulSoup(res.text, "html.parser")

    table = soup.select_one("table.type_1")
    if table is None:
        return []

    out: list[dict] = []
    for row in table.select("tr"):
        cols = row.select("td")
        if len(cols) < 5:
            continue
        title_tag = cols[1].select_one("a")
        if title_tag is None:
            continue
        title       = title_tag.text.strip()
        detail_link = title_tag.get("href", "")

        pdf_url = ""
        pdf_tag = cols[1].select_one('a[href$=".pdf"]')
        if pdf_tag is None:
            # 일부 레이아웃은 별도 셀(다운로드 아이콘)에 pdf 링크가 있음
            for a in row.select('a[href$=".pdf"]'):
                pdf_url = a.get("href", "")
                break
        else:
            pdf_url = pdf_tag.get("href", "")

        broker = cols[2].text.strip() if len(cols) > 2 else ""
        date   = cols[3].text.strip() if len(cols) > 3 else ""
        out.append({
            "title":       title,
            "broker":      broker,
            "date":        date,
            "pdf_url":     pdf_url,
            "detail_link": (
                f"https://finance.naver.com/research/{detail_link}"
                if detail_link else ""
            ),
        })
    return out


def _extract_report_html(report_info: dict) -> dict:
    """
    네이버 금융 리서치 *상세 페이지* (company_read.naver?nid=XXX) 를 BeautifulSoup
    으로 직접 파싱해 목표주가/투자의견/본문 요약을 추출.
    PDF 다운로드/PyPDF2 사용 안 함.

    응답 스키마는 기존 _extract_report_pdf 와 동일하게 유지 (프론트 무수정).
    PDF 파싱이 사라졌으므로 financial_tables 는 항상 [], current_price 도 None.
    """
    import re as _re
    import requests as _rq
    from bs4 import BeautifulSoup

    result = {
        "title":            report_info.get("title", ""),
        "broker":           report_info.get("broker", ""),
        "date":             report_info.get("date", ""),
        "pdf_url":          report_info.get("pdf_url", ""),
        "target_price":     None,
        "opinion":          None,
        "current_price":    None,
        "upside":           None,
        "key_points":       [],
        "revenue_estimate": None,
        "op_estimate":      None,
        "eps_estimate":     None,
        "financial_tables": [],
        "summary":          "",
    }

    detail_link = (report_info.get("detail_link") or "").strip()
    if not detail_link:
        return result

    # nid 추출 → 정규 URL 재구성 (detail_link 가 상대경로/쿼리 변형 어느 쪽이든 안전)
    m = _re.search(r"nid=(\d+)", detail_link)
    if not m:
        return result
    nid = m.group(1)

    try:
        res = _rq.get(
            "https://finance.naver.com/research/company_read.naver",
            params={"nid": nid},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        res.encoding = "euc-kr"
        soup = BeautifulSoup(res.text, "html.parser")
    except Exception as exc:
        print(f"[리포트 상세 요청 실패] nid={nid}: {exc}")
        return result

    table = soup.select_one("table.type_1")
    if table is None:
        return result

    # ── 헤더 셀 (th.view_sbj): 종목명·제목·증권사·날짜·조회수가 한 줄로 ──
    sbj = table.select_one("th.view_sbj")
    if sbj:
        # 종목명 em 은 list 페이지에 이미 있으므로 무시. 나머지 텍스트를 ' | ' 로 분할해
        # title / broker / date 를 보강. list 페이지에서 받은 값이 비어있을 때만 채움.
        full_txt = " ".join(sbj.get_text(" ", strip=True).split())
        # '삼성전자 심각한 숏티지, ... 한화투자증권 | 2026.04.08 | 조회 25559'
        parts = [p.strip() for p in full_txt.split("|")]
        # parts[0] = '종목명 제목 ... 증권사', parts[1] = 날짜, parts[2] = 조회
        if len(parts) >= 2 and not result["date"]:
            result["date"] = parts[1]
        if parts and not result["broker"]:
            # 종목명 em 텍스트 제거 → 제목 + 증권사
            stock_em = sbj.select_one("em")
            stock_name = stock_em.get_text(strip=True) if stock_em else ""
            head = parts[0]
            if stock_name and head.startswith(stock_name):
                head = head[len(stock_name):].strip()
            # 마지막 단어를 증권사로 가정 (정확하진 않지만 list 가 비었을 때만 fallback)
            tokens = head.rsplit(" ", 1)
            if len(tokens) == 2:
                if not result["title"]:
                    result["title"] = tokens[0]
                result["broker"] = tokens[1]

    # ── 목표가/투자의견 셀: <em class="money"><strong> + <em class="coment"> ──
    money_strong = table.select_one('em.money strong, td em.money strong')
    if money_strong:
        try:
            result["target_price"] = int(money_strong.get_text(strip=True).replace(",", ""))
        except ValueError:
            pass

    coment = table.select_one('em.coment')
    if coment:
        opinion_map = {
            "buy": "매수", "strong buy": "매수", "outperform": "매수", "비중확대": "매수",
            "trading buy": "Trading Buy",
            "neutral": "중립", "hold": "중립", "시장수익률": "중립",
            "sell": "매도", "underperform": "매도", "비중축소": "매도",
            "매수": "매수", "중립": "중립", "매도": "매도",
            "not rated": "Not Rated",
        }
        raw = coment.get_text(strip=True)
        # 의견 없음 / N/A → None 처리
        if raw and raw not in ("없음", "-", "N/A", "n/a", "NR"):
            result["opinion"] = opinion_map.get(raw.lower(), raw)

    # ── 본문 요약 (td.view_cnt) ──
    view_cnt = table.select_one("td.view_cnt")
    if view_cnt:
        # img 노드(다운로드 아이콘)는 빼고 텍스트만 추출
        for img in view_cnt.find_all("img"):
            img.decompose()
        # '리포트원문보기' / 'PDF 다운로드' 등의 버튼 텍스트 제거
        body = view_cnt.get_text(" ", strip=True)
        body = " ".join(body.split())
        # 본문 끝에 종종 붙는 '...2025030587.pdf' 같은 잔여 파일명 제거
        body = _re.sub(r"\s*\d{6,}\.pdf\s*$", "", body)
        # '투자 포인트' 머리말 제거
        body = _re.sub(r"^투자\s*포인트\s*", "", body)
        result["summary"] = body

        # ── 핵심 포인트: 본문을 문장 단위로 쪼개 의미 있는 5개 추출 ──
        sentences = _re.split(r"(?<=[.!?다요음])\s+(?=[가-힣A-Z0-9])", body)
        bullets: list[str] = []
        for s in sentences:
            s = s.strip()
            if 15 <= len(s) <= 200 and _re.search(r"[가-힣]", s):
                bullets.append(s)
            if len(bullets) >= 5:
                break
        result["key_points"] = bullets

        # ── 본문에서 매출/영업이익/EPS 추정 패턴 빠르게 스캔 ──
        m = _re.search(r"매출액?\s*(?:은|이|는|를|=|:)?\s*([0-9,.]+)\s*(조|억|백만)?", body)
        if m: result["revenue_estimate"] = m.group(1) + (m.group(2) or "")
        m = _re.search(r"영업이익\s*(?:은|이|는|를|=|:)?\s*([0-9,.]+)\s*(조|억|백만)?", body)
        if m: result["op_estimate"] = m.group(1) + (m.group(2) or "")
        m = _re.search(r"(?:EPS|주당순이익)\s*(?:은|이|는|=|:)?\s*([0-9,]+)\s*원?", body)
        if m: result["eps_estimate"] = m.group(1) + "원"

    return result


# 하위 호환: 기존 함수명을 호출하는 경로가 남아있을 수 있어 alias 유지
_extract_report_pdf = _extract_report_html


@app.route("/api/reports/<code>")
def api_reports(code: str):
    """
    종목 관련 증권사 리포트 크롤링 + 규칙 기반 핵심 정보 추출.
    캐시: cache/reports_{code}.json, TTL 6시간.
    """
    import re as _re
    if not _re.fullmatch(r"\d{6}", code):
        return jsonify({"error": "잘못된 종목코드"}), 400

    stock_name = _get_stock_name(code)
    if not stock_name:
        return jsonify({"error": "종목 없음"}), 404

    cache_file = BASE_DIR / "cache" / f"reports_{code}.json"
    if cache_file.exists():
        try:
            age_hr = (now_kst().timestamp() - cache_file.stat().st_mtime) / 3600
            if age_hr < 6:
                return Response(
                    cache_file.read_text(encoding="utf-8"),
                    content_type="application/json; charset=utf-8",
                )
        except Exception:
            pass

    try:
        report_list = _crawl_naver_research(code)
    except Exception as exc:
        return jsonify({"error": f"리서치 크롤링 실패: {exc}", "items": []}), 502

    reports: list[dict] = []
    for info in report_list[:5]:
        try:
            extracted = _extract_report_html(info)
            if extracted:
                reports.append(extracted)
        except Exception as exc:
            print(f"[리포트 추출 실패] {info.get('title','')}: {exc}")
            continue

    result = {
        "code":  code,
        "name":  stock_name,
        "count": len(reports),
        "items": reports,
    }
    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify(result)


@app.route("/api/screener")
def api_screener():
    """
    종목 스크리너 — data.json 테마 종목을 기본 소스로 하고,
    KRX Open API 가 구독되어 있으면 전 종목 시세로 보강(시총·등락률 등).
    API 호출은 일 1회만 발생 (캐시).
    """
    args = request.args
    market     = (args.get("market") or "ALL").upper()
    min_change = float(args.get("min_change", "-100") or -100)
    min_volume = float(args.get("min_volume", "0")    or 0)
    max_per    = float(args.get("max_per",    "9999") or 9999)
    max_pbr    = float(args.get("max_pbr",    "9999") or 9999)
    q          = (args.get("q") or "").strip()

    krx_stocks  = _get_krx_all_stocks_cached()   # code → KRX 레코드 (빈 dict면 미구독)
    naver_uni   = _load_naver_universe()         # Phase 10: Naver 전 종목 유니버스
    universe_src = bool(naver_uni.get("stocks"))

    today       = _get_trading_date()
    fund_file   = BASE_DIR / "cache" / f"fundamental_{today}.json"
    fundamentals: dict = {}
    if fund_file.exists():
        try:
            fundamentals = json.loads(fund_file.read_text(encoding="utf-8"))
        except Exception:
            fundamentals = {}

    # ─── 1순위 소스: Naver universe (2,500+ 종목) ───
    if universe_src:
        stocks_map = naver_uni["stocks"]   # {code: {code,name,change_pct,volume_mn,close,sectors}}
        results = []
        for code, s in stocks_map.items():
            name   = s.get("name") or code
            chg    = float(s.get("change_pct", 0.0))
            vol_mn = float(s.get("volume_mn",  0.0))    # Naver 는 이미 백만원 단위
            # 시장 정보는 Naver universe 에 없음 — 필터 "ALL" 아니면 패스
            if market != "ALL":
                continue
            if chg < min_change:                     continue
            if vol_mn < min_volume:                  continue
            # PER/PBR 은 Naver 에 없음 — fundamentals 캐시에 있으면 사용
            f = fundamentals.get(code, {})
            per = f.get("per")
            pbr = f.get("pbr")
            if per is not None and per > max_per:    continue
            if pbr is not None and pbr > max_pbr:    continue
            if q and q not in code and q not in (name or ''):
                continue
            results.append({
                "code": code, "name": name,
                "change_pct": round(chg, 2),
                "volume_mn":  int(vol_mn),
                "close":      s.get("close"),
                "per": per, "pbr": pbr,
                "market_cap": f.get("market_cap"),
                "market":     "",
                "sectors":    s.get("sectors", []),
                "theme":      (s.get("sectors") or [None])[0],
            })

        results.sort(key=lambda r: r["volume_mn"], reverse=True)
        return jsonify({
            "count":             len(results),
            "stocks":            results[:200],
            "has_fundamentals":  bool(fundamentals),
            "krx_api_enriched":  bool(krx_stocks),
            "universe_source":   "naver_finance",
            "universe_size":     naver_uni.get("stock_count", 0),
            "fetched_at":        naver_uni.get("fetched_at"),
        })

    # ─── 2순위 폴백: data.json 테마 (기존 134종목) ───
    if not DATA_JSON.exists():
        return jsonify({"error": "data.json 미수집"}), 503
    try:
        data = json.loads(DATA_JSON.read_text(encoding="utf-8"))
    except Exception:
        return jsonify({"error": "data.json 로드 실패"}), 500

    seen = set()
    results = []
    for theme in data.get("themes", []):
        for s in theme.get("stocks", []):
            code = s.get("code")
            if not code or code in seen:
                continue
            seen.add(code)
            name   = s.get("name", code)
            chg    = float(s.get("change_pct", 0.0))
            vol_mn = float(s.get("volume_mn",  0.0))

            # KRX 보강 (가능하면 KRX 수치 우선)
            krx_row = krx_stocks.get(code) or {}
            mkt     = ""
            market_cap = None
            if krx_row:
                krx_chg = _krx_get_float(krx_row, "FLUC_RT", "flucRt", "CHG_RT")
                if krx_chg is not None:
                    chg = krx_chg
                krx_vol = _krx_get_float(krx_row, "ACC_TRDVAL", "accTrdVal", "TRDVAL")
                if krx_vol is not None:
                    vol_mn = krx_vol / 1_000_000
                market_cap = _krx_get_float(krx_row, "MKTCAP", "mktCap")
                if market_cap is not None:
                    market_cap = market_cap / 1_000_000
                mkt = krx_row.get("MKT_NM") or krx_row.get("mktNm") or ""
                if "KOSPI" in mkt.upper() or "유가증권" in mkt:
                    mkt = "KOSPI"
                elif "KOSDAQ" in mkt.upper() or "코스닥" in mkt:
                    mkt = "KOSDAQ"

            f = fundamentals.get(code, {})
            per = f.get("per")
            pbr = f.get("pbr")
            if market_cap is None:
                market_cap = f.get("market_cap")
            if not mkt:
                mkt = f.get("market", "")

            if market != "ALL" and mkt and mkt != market: continue
            if chg < min_change:                          continue
            if vol_mn < min_volume:                       continue
            if per is not None and per > max_per:         continue
            if pbr is not None and pbr > max_pbr:         continue
            if q and q not in code and q not in name:     continue

            results.append({
                "code": code, "name": name,
                "change_pct": round(chg, 2),
                "volume_mn":  int(vol_mn),
                "per": per, "pbr": pbr,
                "market_cap": market_cap,
                "market": mkt,
                "theme": theme.get("name"),
            })

    results.sort(key=lambda r: r["volume_mn"], reverse=True)
    return jsonify({
        "count":             len(results),
        "stocks":            results[:200],
        "has_fundamentals":  bool(fundamentals),
        "krx_api_enriched":  bool(krx_stocks),
        "universe_source":   "themes_fallback",
        "universe_size":     len(results),
    })


def _parse_naver_flow_number(s: str) -> int:
    """
    네이버 frgn.naver 의 순매매량 셀 파싱.
    예: '+465,171' → 465171, '-13,418,579' → -13418579, '' → 0
    """
    if not s:
        return 0
    s = s.replace(",", "").replace(" ", "").strip()
    if not s or s in ("-", "—"):
        return 0
    try:
        return int(s)
    except ValueError:
        # 붙어있는 부호 제거 후 재시도
        s = s.lstrip("+")
        try:
            return int(s)
        except ValueError:
            return 0


@app.route("/api/flow/<code>")
def api_flow(code: str):
    """
    종목별 최근 20 거래일 외국인/기관 순매수 시계열.
    데이터 소스: https://finance.naver.com/item/frgn.naver?code={code}
    Cache: cache/flow_{code}.json, TTL 1시간.
    실패 시 {"error":"데이터 없음"} 반환.
    """
    import re as _re
    if not _re.fullmatch(r"\d{6}", code):
        return jsonify({"error": "잘못된 종목코드"}), 400

    stock_name = _get_stock_name(code) or code
    cache_file = BASE_DIR / "cache" / f"flow_{code}.json"
    if cache_file.exists():
        try:
            age_min = (now_kst().timestamp() - cache_file.stat().st_mtime) / 60
            if age_min < 60:
                return Response(
                    cache_file.read_text(encoding="utf-8"),
                    content_type="application/json; charset=utf-8",
                )
        except Exception:
            pass

    try:
        import requests as _rq
        from bs4 import BeautifulSoup
    except ImportError:
        return jsonify({"error": "bs4/requests 미설치"}), 500

    url = "https://finance.naver.com/item/frgn.naver"
    try:
        res = _rq.get(
            url,
            params={"code": code},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8,
        )
        res.encoding = "euc-kr"
        soup = BeautifulSoup(res.text, "html.parser")
    except Exception as exc:
        return jsonify({"error": "데이터 없음", "detail": f"네이버 금융 요청 실패: {exc}"}), 502

    table = soup.select_one('table.type2[summary*="외국인"]')
    if table is None:
        table = soup.select_one("table.type2")
    if table is None:
        return jsonify({"error": "데이터 없음", "detail": "테이블 파싱 실패"}), 502

    dates:        list[str] = []
    closes:       list[int] = []
    foreign_net:  list[int] = []   # 주식 수
    inst_net:     list[int] = []   # 주식 수

    for tr in table.select("tr"):
        tds = tr.find_all("td")
        if len(tds) < 7:
            continue   # 헤더/구분선 스킵
        # [0] 날짜  [1] 종가  [2] 전일비  [3] 등락률  [4] 거래량  [5] 기관  [6] 외국인
        date_txt = tds[0].get_text(strip=True)
        if not _re.match(r"\d{4}\.\d{2}\.\d{2}", date_txt):
            continue
        try:
            close = int(tds[1].get_text(strip=True).replace(",", ""))
        except ValueError:
            continue
        inst_shares = _parse_naver_flow_number(tds[5].get_text(strip=True))
        for_shares  = _parse_naver_flow_number(tds[6].get_text(strip=True))

        dates.append(date_txt.replace(".", "-"))
        closes.append(close)
        inst_net.append(inst_shares)
        foreign_net.append(for_shares)

    if not dates:
        return jsonify({"error": "데이터 없음", "detail": "행 추출 실패"}), 502

    # 오래된 → 최신 순으로 정렬 (네이버는 최신이 위)
    dates.reverse(); closes.reverse()
    foreign_net.reverse(); inst_net.reverse()

    # 대략적인 순매수 '원' 금액 = 주식수 × 종가 (추정치)
    foreign_value = [f * c for f, c in zip(foreign_net, closes)]
    inst_value    = [i * c for i, c in zip(inst_net,    closes)]

    result = {
        "code":           code,
        "name":           stock_name,
        "dates":          dates,
        "close":          closes,
        "foreign_shares": foreign_net,
        "inst_shares":    inst_net,
        "foreign_value":  foreign_value,    # 원 (추정)
        "inst_value":     inst_value,       # 원 (추정)
        "foreign_sum_20": sum(foreign_value),
        "inst_sum_20":    sum(inst_value),
        "source":         "naver_finance",
        "fetched_at":     now_kst().strftime("%Y-%m-%d %H:%M:%S"),
    }
    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify(result)


def _fetch_krx_shorting(kind: str) -> dict:
    """
    공매도 상위 종목 조회. kind: 'ratio' (거래 상위) | 'balance' (잔고 상위).

    3-tier fallback:
      1) data.krx.co.kr getJsonData (pykrx 와 동일 bld, 세션 쿠키)
      2) pykrx 공매도_거래상위_50종목 / 공매도_잔고상위_50종목 클래스 직접 호출
      3) pykrx get_shorting_balance_by_ticker (전종목 fetch 후 정렬)

    어느 단계든 KOSPI + KOSDAQ 를 합쳐 상위 30 반환.
    모든 소스 실패 시 status='blocked' + attempted 소스 목록 반환.
    """
    assert kind in ("ratio", "balance")
    bld = ("dbms/MDC/STAT/srt/MDCSTAT30401" if kind == "ratio"
           else "dbms/MDC/STAT/srt/MDCSTAT30801")
    attempted: list[dict] = []

    # 직전 영업일 후보 (오늘 데이터는 보통 T+1 에 공개)
    today_dt = now_kst().date()
    candidates = []
    for back in range(1, 8):
        d = today_dt - timedelta(days=back)
        if d.weekday() < 5:
            candidates.append(d.strftime("%Y%m%d"))

    # ── Tier 1: data.krx.co.kr getJsonData ──
    try:
        import requests as _rq
        sess = _rq.Session()
        sess.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) "
                          "AppleWebKit/537.36 Chrome/120",
            "Referer": "http://data.krx.co.kr/",
            "X-Requested-With": "XMLHttpRequest",
        })
        # 초기 쿠키 획득
        try:
            sess.get("http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd"
                     "?menuId=MDC0201020502", timeout=8)
        except Exception:
            pass
        url = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"

        rows_combined: list[dict] = []
        err_msgs: list[str] = []
        for trdDd in candidates[:3]:
            for mktTpCd in (1, 2):  # 1=KOSPI, 2=KOSDAQ
                body = {"bld": bld, "trdDd": trdDd, "mktTpCd": mktTpCd}
                try:
                    resp = sess.post(url, data=body, timeout=10)
                    if resp.status_code != 200:
                        err_msgs.append(f"HTTP {resp.status_code} ({trdDd}/mkt{mktTpCd})")
                        continue
                    if not resp.text or not resp.text.strip():
                        err_msgs.append(f"empty body ({trdDd}/mkt{mktTpCd})")
                        continue
                    try:
                        j = resp.json()
                    except ValueError:
                        err_msgs.append(f"non-JSON ({trdDd}/mkt{mktTpCd})")
                        continue
                    out = j.get("OutBlock_1") or j.get("output") or []
                    if out:
                        rows_combined.extend(out)
                except Exception as exc:
                    err_msgs.append(f"{type(exc).__name__}: {str(exc)[:60]}")
            if rows_combined:
                break
        attempted.append({
            "source": "data.krx.co.kr getJsonData",
            "bld":    bld,
            "ok":     bool(rows_combined),
            "errors": err_msgs[:4],
        })
        if rows_combined:
            return _normalize_shorting_krx_rows(rows_combined, kind)
    except Exception as exc:
        attempted.append({"source": "data.krx.co.kr", "ok": False,
                          "errors": [f"{type(exc).__name__}: {str(exc)[:80]}"]})

    # ── Tier 2: pykrx 클래스 직접 호출 ──
    try:
        from pykrx.website.krx.market.core import (
            공매도_거래상위_50종목, 공매도_잔고상위_50종목,
        )
        Cls = 공매도_거래상위_50종목 if kind == "ratio" else 공매도_잔고상위_50종목
        rows_combined = []
        err_msgs = []
        for trdDd in candidates[:3]:
            for mktTpCd in (1, 2):
                try:
                    df = Cls().fetch(trdDd, mktTpCd)
                    if df is not None and len(df):
                        rows_combined.extend(df.to_dict(orient="records"))
                except Exception as exc:
                    err_msgs.append(f"{trdDd}/mkt{mktTpCd}: {str(exc)[:60]}")
            if rows_combined:
                break
        attempted.append({
            "source": "pykrx direct",
            "ok":     bool(rows_combined),
            "errors": err_msgs[:4],
        })
        if rows_combined:
            return _normalize_shorting_krx_rows(rows_combined, kind)
    except ImportError:
        attempted.append({"source": "pykrx direct", "ok": False,
                          "errors": ["pykrx 미설치"]})
    except Exception as exc:
        attempted.append({"source": "pykrx direct", "ok": False,
                          "errors": [f"{type(exc).__name__}: {str(exc)[:80]}"]})

    # ── Tier 3: pykrx 고수준 wrapper ──
    try:
        from pykrx import stock as _stock
        dfs = []
        errs = []
        for trdDd in candidates[:2]:
            for market in ("KOSPI", "KOSDAQ"):
                try:
                    df = _stock.get_shorting_balance_by_ticker(trdDd, market)
                    if df is not None and len(df):
                        dfs.append(df)
                except Exception as exc:
                    errs.append(f"{trdDd}/{market}: {str(exc)[:60]}")
            if dfs:
                break
        attempted.append({
            "source": "pykrx wrapper",
            "ok":     bool(dfs),
            "errors": errs[:4],
        })
        if dfs:
            import pandas as _pd
            df_all = _pd.concat(dfs)
            return _normalize_shorting_pykrx_df(df_all, kind)
    except Exception as exc:
        attempted.append({"source": "pykrx wrapper", "ok": False,
                          "errors": [f"{type(exc).__name__}: {str(exc)[:80]}"]})

    return {
        "status":   "blocked",
        "kind":     kind,
        "stocks":   [],
        "attempted": attempted,
        "message": (
            "공매도 데이터 소스에 접근할 수 없습니다. "
            "KRX 공매도 통계 API (data.krx.co.kr) 가 외부 접근을 차단한 상태이며, "
            "네이버 금융도 공매도 종합 페이지를 제공하지 않습니다. "
            "KRX Open API 의 공매도 엔드포인트 구독 또는 벤더 데이터(Bloomberg/Refinitiv) "
            "연동이 필요합니다."
        ),
    }


def _normalize_shorting_krx_rows(rows: list, kind: str) -> dict:
    """KRX getJsonData / pykrx.core 응답을 공통 스키마로 변환."""
    out: list[dict] = []
    for row in rows:
        code = (row.get("ISU_SRT_CD") or row.get("ISU_CD") or "").strip()
        name = (row.get("ISU_ABBRV") or row.get("ISU_NM") or "").strip()
        if not code or not name:
            continue
        def _num(v) -> float | None:
            if v is None or v == "":
                return None
            try:
                return float(str(v).replace(",", ""))
            except ValueError:
                return None
        ratio   = _num(row.get("TDD_SRTSELL_WT") or row.get("BAL_RTO"))
        balance = _num(row.get("BAL_AMT") or row.get("CVSRTSELL_TRDVAL"))
        volume  = _num(row.get("CVSRTSELL_TRDVOL") or row.get("BAL_QTY"))
        mktcap  = _num(row.get("MKTCAP"))
        out.append({
            "code": code, "name": name,
            "ratio":   ratio,
            "balance": int(balance) if balance is not None else None,
            "volume":  int(volume)  if volume  is not None else None,
            "mktcap":  int(mktcap)  if mktcap  is not None else None,
        })
    # 상위 30 정렬
    key = "ratio" if kind == "ratio" else "balance"
    out.sort(key=lambda x: (x.get(key) or 0), reverse=True)
    return {"status": "ok", "kind": kind, "count": len(out), "stocks": out[:30]}


def _normalize_shorting_pykrx_df(df, kind: str) -> dict:
    """pykrx get_shorting_balance_by_ticker DataFrame → 공통 스키마."""
    ratio_col   = next((c for c in df.columns if "비중"    in c), None)
    balance_col = next((c for c in df.columns if "잔고금액" in c or "금액" in c), None)
    volume_col  = next((c for c in df.columns if "잔고수량" in c or "수량" in c), None)
    key_col = ratio_col if kind == "ratio" else (balance_col or ratio_col)
    if key_col is None:
        return {"status": "blocked", "kind": kind, "stocks": [],
                "message": "공매도 컬럼 없음"}
    df2 = df.sort_values(key_col, ascending=False).head(30)
    out = []
    for code, row in df2.iterrows():
        out.append({
            "code": str(code),
            "name": row.get("종목명", ""),
            "ratio":   float(row[ratio_col])   if ratio_col   else None,
            "balance": int(row[balance_col])   if balance_col else None,
            "volume":  int(row[volume_col])    if volume_col  else None,
            "mktcap":  None,
        })
    return {"status": "ok", "kind": kind, "count": len(out), "stocks": out}


def _cached_short(kind: str) -> dict:
    """캐시 확인 + fetch + 저장. blocked 는 15분, ok 는 24시간 캐시."""
    today = _get_trading_date()
    cache_file = BASE_DIR / "cache" / f"short_{kind}_{today}.json"
    if cache_file.exists():
        try:
            age_min = (now_kst().timestamp() - cache_file.stat().st_mtime) / 60
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            ttl = 1440 if cached.get("status") == "ok" else 15
            if age_min < ttl:
                return cached
        except Exception:
            pass
    result = _fetch_krx_shorting(kind)
    result["updated_at"] = now_kst().strftime("%Y-%m-%d %H:%M:%S")
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return result


@app.route("/api/short/ratio")
def api_short_ratio():
    """공매도 비중(거래) 상위 종목."""
    return jsonify(_cached_short("ratio"))


@app.route("/api/short/balance")
def api_short_balance():
    """공매도 잔고 상위 종목."""
    return jsonify(_cached_short("balance"))


@app.route("/api/short")
def api_short():
    """레거시 라우트 — /api/short?sort=ratio|balance → 신규 엔드포인트로 위임."""
    sort_by = (request.args.get("sort") or "ratio").strip()
    if sort_by not in ("ratio", "balance"):
        sort_by = "ratio"
    return jsonify(_cached_short(sort_by))


def _parse_pct(s: str) -> float:
    """'+7.94%', '-1.32%' → float"""
    if not s:
        return 0.0
    s = s.replace("%", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _scrape_naver_sectors() -> list[dict]:
    """
    네이버 금융 업종 랜딩 페이지에서 79개 KRX 업종 요약을 스크랩.
    URL: https://finance.naver.com/sise/sise_group.naver?type=upjong
    Returns: [{no, name, change_pct, total, up, flat, down}]
    """
    import re as _re
    import requests as _rq
    from bs4 import BeautifulSoup

    url = "https://finance.naver.com/sise/sise_group.naver"
    try:
        res = _rq.get(url, params={"type": "upjong"},
                      headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        res.encoding = "euc-kr"
    except Exception as exc:
        print(f"[Naver 업종 랜딩 실패] {exc}")
        return []

    soup = BeautifulSoup(res.text, "html.parser")
    table = soup.select_one("table.type_1")
    if table is None:
        return []

    out: list[dict] = []
    for tr in table.select("tr"):
        tds = tr.find_all("td")
        if len(tds) < 6:
            continue
        link = tds[0].select_one("a[href*='no=']")
        if link is None:
            continue
        href = link.get("href", "")
        m = _re.search(r"no=(\d+)", href)
        if not m:
            continue
        no = m.group(1)
        name = link.get_text(strip=True)
        change_pct = _parse_pct(tds[1].get_text())
        try:
            total = int(tds[2].get_text(strip=True))
            up    = int(tds[3].get_text(strip=True))
            flat  = int(tds[4].get_text(strip=True))
            down  = int(tds[5].get_text(strip=True))
        except ValueError:
            total = up = flat = down = 0
        out.append({
            "no": no, "name": name, "change_pct": change_pct,
            "total": total, "up": up, "flat": flat, "down": down,
        })
    return out


def _scrape_naver_sector_detail(no: str) -> dict:
    """
    네이버 업종 상세 페이지에서 해당 업종 소속 종목 리스트 추출.
    URL: sise_group_detail.naver?type=upjong&no=<no>
    Returns: {sector_name, stocks: [{code, name, close, change_pct, volume, volume_mn}]}
    """
    import re as _re
    import requests as _rq
    from bs4 import BeautifulSoup

    url = "https://finance.naver.com/sise/sise_group_detail.naver"
    try:
        res = _rq.get(url, params={"type": "upjong", "no": no},
                      headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        res.encoding = "euc-kr"
    except Exception as exc:
        return {"error": f"네이버 요청 실패: {exc}"}

    soup = BeautifulSoup(res.text, "html.parser")

    # 업종명은 페이지 상단 'em' 또는 h3
    sector_name = ""
    h3_em = soup.select_one("h3 em, div.h_sub em")
    if h3_em:
        sector_name = h3_em.get_text(strip=True)

    # 종목 테이블: type_5
    table = None
    for t in soup.select("table"):
        ths = [th.get_text(strip=True) for th in t.find_all("th")]
        if "종목명" in ths and "현재가" in ths:
            table = t
            break
    if table is None:
        return {"sector_name": sector_name, "stocks": []}

    code_pat = _re.compile(r"code=(\d{6})")
    stocks: list[dict] = []
    seen_codes: set = set()

    for tr in table.select("tr"):
        tds = tr.find_all("td")
        if len(tds) < 8:
            continue
        # 종목명 링크에서 code 추출
        name_a = tds[0].select_one("a[href*='code=']")
        if name_a is None:
            continue
        m = code_pat.search(name_a.get("href", ""))
        if not m:
            continue
        code = m.group(1)
        if code in seen_codes:
            continue
        seen_codes.add(code)
        name = name_a.get_text(strip=True).rstrip("*").strip()

        def _int(cell_txt):
            try:
                return int(cell_txt.replace(",", "").strip())
            except ValueError:
                return 0

        close        = _int(tds[1].get_text(strip=True))
        change_pct   = _parse_pct(tds[3].get_text())
        volume       = _int(tds[6].get_text(strip=True))
        trd_value    = _int(tds[7].get_text(strip=True))  # 거래대금 (백만원 단위)

        stocks.append({
            "code":       code,
            "name":       name,
            "close":      close,
            "change_pct": change_pct,
            "volume":     volume,
            "volume_mn":  trd_value,
        })

    return {"sector_name": sector_name, "stocks": stocks}


def _build_naver_universe_background():
    """
    모든 79개 업종 상세 페이지를 순차 스크랩해 전 종목 딕셔너리를 구축.
    결과는 cache/naver_universe_{date}.json 에 저장.
    스크리너가 구독된 KRX API 없이도 2,500+ 커버 가능.
    서버 부팅 시 백그라운드 스레드로 실행.
    """
    today = _get_trading_date()
    out_file = BASE_DIR / "cache" / f"naver_universe_{today}.json"
    if out_file.exists():
        log.info("naver_universe 캐시 이미 존재 — 스킵 (%s)", out_file.name)
        return
    lock_file = out_file.with_suffix(".lock")
    if lock_file.exists():
        return
    try:
        lock_file.touch()
    except Exception:
        pass

    try:
        log.info("▶  Naver 업종 유니버스 빌드 시작")
        sectors = _scrape_naver_sectors()
        if not sectors:
            log.warning("Naver 유니버스: 랜딩 스크랩 실패")
            return

        import time as _time
        universe: dict = {}
        stock_to_sector: dict = {}
        for i, sec in enumerate(sectors):
            no = sec["no"]
            try:
                detail = _scrape_naver_sector_detail(no)
            except Exception as exc:
                log.warning("섹터 %s 스크랩 예외: %s", no, exc)
                continue
            stocks = detail.get("stocks", []) if isinstance(detail, dict) else []
            for s in stocks:
                code = s["code"]
                # 최초 출현 섹터를 기본으로, 추가 섹터는 리스트에 누적
                if code not in universe:
                    universe[code] = {**s, "sectors": [sec["name"]]}
                    stock_to_sector[code] = sec["name"]
                else:
                    if sec["name"] not in universe[code]["sectors"]:
                        universe[code]["sectors"].append(sec["name"])
            _time.sleep(0.25)    # 네이버 rate limit 회피
            if (i + 1) % 10 == 0:
                log.info("  ... %d/%d 섹터 (누적 %d 종목)",
                         i + 1, len(sectors), len(universe))

        result = {
            "fetched_at":  now_kst().strftime("%Y-%m-%d %H:%M:%S"),
            "sector_count": len(sectors),
            "stock_count":  len(universe),
            "stocks":       universe,
        }
        out_file.parent.mkdir(exist_ok=True)
        out_file.write_text(json.dumps(result, ensure_ascii=False),
                            encoding="utf-8")
        log.info("✓  Naver 유니버스 빌드 완료: %d 종목 → %s",
                 len(universe), out_file.name)
    finally:
        try: lock_file.unlink()
        except Exception: pass


def _load_naver_universe() -> dict:
    """오늘자 naver_universe 캐시 로드 (없으면 빈 dict)."""
    today = _get_trading_date()
    f = BASE_DIR / "cache" / f"naver_universe_{today}.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


@app.route("/api/sectors")
def api_sectors():
    """
    KRX 업종 랜딩 데이터 — 네이버 금융 업종 페이지 스크랩.
    79개 업종의 이름·등락률·종목수 요약. 1시간 캐시.
    """
    cache_file = BASE_DIR / "cache" / "sectors_naver_landing.json"
    if cache_file.exists():
        try:
            age_min = (now_kst().timestamp() - cache_file.stat().st_mtime) / 60
            if age_min < 60:
                return Response(
                    cache_file.read_text(encoding="utf-8"),
                    content_type="application/json; charset=utf-8",
                )
        except Exception:
            pass

    sectors = _scrape_naver_sectors()
    if not sectors:
        return jsonify({
            "error": "Naver 업종 페이지 파싱 실패",
            "source": "naver_finance",
        }), 502

    result = {
        "source":       "naver_finance",
        "count":        len(sectors),
        "sectors":      sectors,
        "fetched_at":   now_kst().strftime("%Y-%m-%d %H:%M:%S"),
    }
    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return jsonify(result)


@app.route("/api/sector/<no>")
def api_sector_detail(no: str):
    """
    특정 업종 상세 — 해당 업종 소속 종목 리스트 (등락률/거래대금/현재가 포함).
    6시간 캐시.
    """
    import re as _re
    if not _re.fullmatch(r"\d+", no):
        return jsonify({"error": "잘못된 업종 번호"}), 400

    cache_file = BASE_DIR / "cache" / f"sector_detail_{no}.json"
    if cache_file.exists():
        try:
            age_hr = (now_kst().timestamp() - cache_file.stat().st_mtime) / 3600
            if age_hr < 6:
                return Response(
                    cache_file.read_text(encoding="utf-8"),
                    content_type="application/json; charset=utf-8",
                )
        except Exception:
            pass

    detail = _scrape_naver_sector_detail(no)
    if "error" in detail:
        return jsonify(detail), 502
    if not detail.get("stocks"):
        return jsonify({"error": "종목 없음", **detail}), 502

    result = {
        "source":   "naver_finance",
        "no":       no,
        **detail,
        "count":    len(detail["stocks"]),
        "fetched_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
    }
    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return jsonify(result)


@app.route("/api/krx_status")
def api_krx_status():
    """KRX Open API 구독 상태 진단 — 각 엔드포인트에 실제 호출해서 성공 여부 리포트."""
    try:
        import krx_api
    except ImportError:
        return jsonify({"error": "krx_api 모듈 로드 실패"}), 500
    return jsonify({
        "has_api_key": krx_api.has_api_key(),
        "base_url":    krx_api.KRX_API_BASE,
        "probed_date": _get_trading_date(),
        "subscriptions": krx_api.probe_subscriptions(_get_trading_date()),
        "note": ("각 엔드포인트별로 openapi.krx.co.kr 마이페이지에서 별도 구독이 필요합니다. "
                 "ok=false 인 항목은 '활용 신청' 후 다시 호출하세요."),
    })


@app.route("/api/compare")
def api_compare():
    import re as _re
    code1  = (request.args.get("code1")  or "").strip()
    code2  = (request.args.get("code2")  or "").strip()
    period = (request.args.get("period") or "1M").strip()
    if not (_re.fullmatch(r"\d{6}", code1) and _re.fullmatch(r"\d{6}", code2)):
        return jsonify({"error": "잘못된 종목코드"}), 400

    PERIOD_DAYS = {"1M": 30, "3M": 90, "6M": 180, "1Y": 365, "3Y": 365 * 3, "5Y": 365 * 5}
    if period not in PERIOD_DAYS:
        period = "1M"

    today      = _get_trading_date()
    cache_file = BASE_DIR / "cache" / f"compare_{code1}_{code2}_{period}_{today}.json"
    if cache_file.exists():
        return Response(
            cache_file.read_text(encoding="utf-8"),
            content_type="application/json; charset=utf-8",
        )

    try:
        from pykrx import stock as _stock
    except ImportError:
        return jsonify({"error": "pykrx 미설치"}), 500

    start_dt = datetime.strptime(today, "%Y%m%d").replace(tzinfo=KST) \
               - timedelta(days=PERIOD_DAYS[period] + 10)  # 버퍼 10일
    start    = start_dt.strftime("%Y%m%d")

    try:
        df1 = _stock.get_market_ohlcv_by_date(start, today, code1)
        df2 = _stock.get_market_ohlcv_by_date(start, today, code2)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    if df1 is None or df2 is None or df1.empty or df2.empty:
        return jsonify({"error": "데이터 없음"}), 404

    close_col = next((c for c in df1.columns if "종가" in c), None)
    if close_col is None:
        return jsonify({"error": "종가 컬럼 없음"}), 500

    # 공통 거래일만 사용
    common = df1.index.intersection(df2.index)
    if len(common) < 2:
        return jsonify({"error": "공통 거래일 부족"}), 404
    df1 = df1.loc[common]
    df2 = df2.loc[common]

    c1 = df1[close_col].tolist()
    c2 = df2[close_col].tolist()
    b1 = float(c1[0]) or 1.0
    b2 = float(c2[0]) or 1.0
    returns1 = [round((float(v) / b1 - 1) * 100, 2) for v in c1]
    returns2 = [round((float(v) / b2 - 1) * 100, 2) for v in c2]
    dates    = [d.strftime("%Y-%m-%d") for d in common]

    try:
        name1 = _stock.get_market_ticker_name(code1) or code1
    except Exception:
        name1 = code1
    try:
        name2 = _stock.get_market_ticker_name(code2) or code2
    except Exception:
        name2 = code2

    result = {
        "period": period,
        "dates":  dates,
        "stock1": {
            "code":          code1,
            "name":          name1,
            "current_price": int(c1[-1]),
            "change_pct":    returns1[-1],
            "returns":       returns1,
        },
        "stock2": {
            "code":          code2,
            "name":          name2,
            "current_price": int(c2[-1]),
            "change_pct":    returns2[-1],
            "returns":       returns2,
        },
    }
    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify(result)


@app.route("/api/financial/<code>")
def api_financial(code: str):
    """
    종목 재무 요약 — 네이버 금융 main.naver 페이지 스크랩.
    24h 캐시: cache/financial_{code}.json
    추출 항목: PER, PBR, ROE, EPS, BPS, 시가총액, 시가총액 순위, 동일업종 PER,
              배당수익률, 추정PER, 매출/영업이익/순이익 (3년 + 추정).
    """
    import re as _re
    if not _re.fullmatch(r"\d{6}", code):
        return jsonify({"error": "잘못된 종목코드"}), 400

    cache_file = BASE_DIR / "cache" / f"financial_{code}.json"
    if cache_file.exists():
        try:
            age_hr = (now_kst().timestamp() - cache_file.stat().st_mtime) / 3600
            if age_hr < 24:
                return Response(
                    cache_file.read_text(encoding="utf-8"),
                    content_type="application/json; charset=utf-8",
                )
        except Exception:
            pass

    try:
        import requests as _rq
        from bs4 import BeautifulSoup
    except ImportError:
        return jsonify({"error": "bs4/requests 미설치"}), 500

    try:
        res = _rq.get(
            "https://finance.naver.com/item/main.naver",
            params={"code": code},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8,
        )
        # main.naver 는 UTF-8 (frgn.naver / company_list.naver 와 다름!)
        res.encoding = "utf-8"
        soup = BeautifulSoup(res.text, "html.parser")
    except Exception as exc:
        return jsonify({"error": f"네이버 요청 실패: {exc}"}), 502

    def _num(s):
        if not s:
            return None
        m = _re.search(r"[-+]?[\d,]+\.?\d*", s.replace("\n", "").replace(" ", ""))
        if not m:
            return None
        try:
            return float(m.group(0).replace(",", ""))
        except ValueError:
            return None

    result: dict = {
        "code":       code,
        "name":       _get_stock_name(code) or code,
        "per":        None, "eps": None,
        "estimate_per": None, "estimate_eps": None,
        "pbr":        None, "bps": None,
        "dividend_yield": None,
        "market_cap":     None,
        "market_cap_rank": None,
        "industry_per":   None,
        "shares_outstanding": None,
        "foreign_ratio":  None,
        "annual": [],   # [{period, revenue, op_profit, net_profit, op_margin, net_margin}]
    }

    # ── 시가총액 + 순위 ──
    cap = soup.select_one("#_market_sum")
    if cap:
        # 부모 노드까지 포함해야 '억원' 단위까지 잡힘
        parent_txt = " ".join(cap.parent.get_text(" ", strip=True).split())
        # '1,189조 8,472 억원' → '1,189조 8,472억원'
        parent_txt = parent_txt.replace("억 원", "억원").replace(" 억원", "억원")
        result["market_cap"] = parent_txt
    # 시가총액 순위는 별도 td 에 들어있음 ('코스피1위')
    for th in soup.find_all("th"):
        if "시가총액순위" in th.get_text(strip=True):
            td = th.find_next("td")
            if td:
                result["market_cap_rank"] = td.get_text(strip=True)
            break

    # ── PER / PBR / 배당 / 추정PER ──
    per_tbl = soup.select_one("table.per_table")
    if per_tbl:
        for tr in per_tbl.select("tr"):
            th_text = tr.select_one("th").get_text(strip=True) if tr.select_one("th") else ""
            td_text = tr.select_one("td").get_text(strip=True) if tr.select_one("td") else ""
            # td 가 'X배lY원' 형태 → '|' 로 분리
            if "추정PER" in th_text:
                parts = td_text.split("l")
                result["estimate_per"] = _num(parts[0]) if len(parts) > 0 else None
                result["estimate_eps"] = _num(parts[1]) if len(parts) > 1 else None
            elif "PER" in th_text:
                parts = td_text.split("l")
                result["per"] = _num(parts[0]) if len(parts) > 0 else None
                result["eps"] = _num(parts[1]) if len(parts) > 1 else None
            elif "PBR" in th_text:
                parts = td_text.split("l")
                result["pbr"] = _num(parts[0]) if len(parts) > 0 else None
                result["bps"] = _num(parts[1]) if len(parts) > 1 else None
            elif "배당수익률" in th_text:
                result["dividend_yield"] = _num(td_text)

    # ── 동일업종 PER ──
    same_per_tbl = soup.find("table", {"summary": _re.compile("동일업종 PER")})
    if same_per_tbl:
        td = same_per_tbl.select_one("td")
        if td:
            result["industry_per"] = _num(td.get_text(strip=True))

    # ── 외국인 보유 + 발행주식수 (#tab_con1 영역) ──
    body_text = soup.get_text("\n", strip=True)
    m = _re.search(r"외국인소진율[\s]*([\d.]+)\s*%", body_text)
    if m: result["foreign_ratio"] = float(m.group(1))
    m = _re.search(r"상장주식수[\s\(\)\w]*?\n?([\d,]+)", body_text)
    if m:
        try: result["shares_outstanding"] = int(m.group(1).replace(",", ""))
        except ValueError: pass

    # ── 매출/영업이익/순이익 (cop_analysis 표) ──
    cop = soup.select_one("section.cop_analysis, .section.cop_analysis")
    if cop:
        # 헤더에서 기간 추출
        periods: list[str] = []
        first_thead_tr = cop.select_one("thead tr:nth-of-type(2)")
        if first_thead_tr:
            for th in first_thead_tr.select("th"):
                txt = th.get_text(strip=True)
                if _re.match(r"\d{4}\.\d{2}", txt):
                    periods.append(txt)

        # tbody 행에서 매출액 / 영업이익 / 당기순이익 추출
        rows_map: dict[str, list[float | None]] = {}
        for tr in cop.select("tbody tr"):
            th = tr.select_one("th")
            if not th: continue
            label = th.get_text(strip=True)
            if label in ("매출액", "영업이익", "당기순이익", "영업이익률", "순이익률"):
                rows_map[label] = [_num(td.get_text(strip=True)) for td in tr.select("td")]

        # 기간별로 묶기
        n = min(len(periods),
                len(rows_map.get("매출액", []) or []),
                len(rows_map.get("영업이익", []) or []) if rows_map.get("영업이익") else 0,
                len(rows_map.get("당기순이익", []) or []) if rows_map.get("당기순이익") else 0)
        for k in range(n):
            result["annual"].append({
                "period":     periods[k],
                "revenue":    rows_map["매출액"][k]    if "매출액"    in rows_map else None,
                "op_profit":  rows_map["영업이익"][k]  if "영업이익"  in rows_map else None,
                "net_profit": rows_map["당기순이익"][k] if "당기순이익" in rows_map else None,
                "op_margin":  rows_map["영업이익률"][k] if "영업이익률" in rows_map and k < len(rows_map["영업이익률"]) else None,
                "net_margin": rows_map["순이익률"][k]   if "순이익률"   in rows_map and k < len(rows_map["순이익률"])   else None,
            })

    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return jsonify(result)


@app.route("/api/price/<code>")
def api_price(code: str):
    """
    단일 종목 현재가. 기존 캐시에서만 조회 (pykrx/외부 호출 없음).
    우선순위:
      1) naver_universe 캐시 (일 1회 빌드, 4,000+ 종목, Phase 10)
      2) data.json 테마 종목 (134)
      3) cache/chart_{code}_{date}.json (온디맨드 차트 캐시)
    """
    import re as _re
    if not _re.fullmatch(r"\d{6}", code):
        return jsonify({"error": "잘못된 종목코드"}), 400

    # 1) Naver universe
    uni = _load_naver_universe()
    if uni and code in uni.get("stocks", {}):
        s = uni["stocks"][code]
        close = s.get("close") or 0
        chg   = float(s.get("change_pct", 0.0))
        prev  = round(close / (1 + chg / 100)) if chg not in (0.0, None) and close else close
        return jsonify({
            "code":       code,
            "name":       s.get("name", code),
            "price":      int(close),
            "prev_close": int(prev),
            "change":     int(close - prev),
            "change_pct": round(chg, 2),
            "volume_mn":  int(s.get("volume_mn", 0)),
            "source":     "naver_universe",
            "fetched_at": uni.get("fetched_at"),
        })

    # 2) data.json themes
    if DATA_JSON.exists():
        try:
            data = json.loads(DATA_JSON.read_text(encoding="utf-8"))
        except Exception:
            data = None
        if data:
            for theme in data.get("themes", []):
                for s in theme.get("stocks", []):
                    if s.get("code") != code:
                        continue
                    # 테마 엔트리에는 price 가 없고 change_pct 만 있음.
                    # 가격은 chart 캐시에서 보강 시도.
                    chg = float(s.get("change_pct", 0.0))
                    price = None
                    prev  = None
                    today = _get_trading_date()
                    cf = BASE_DIR / "cache" / f"chart_{code}_{today}.json"
                    if cf.exists():
                        try:
                            chart = json.loads(cf.read_text(encoding="utf-8"))
                            closes = chart.get("close", [])
                            if len(closes) >= 2:
                                price = int(closes[-1])
                                prev  = int(closes[-2])
                                chg   = round((price / prev - 1) * 100, 2) if prev else chg
                        except Exception:
                            pass
                    return jsonify({
                        "code":       code,
                        "name":       s.get("name", code),
                        "price":      price,
                        "prev_close": prev,
                        "change":     (price - prev) if (price is not None and prev is not None) else None,
                        "change_pct": round(chg, 2),
                        "volume_mn":  int(s.get("volume_mn", 0)),
                        "source":     "data_json_themes",
                        "fetched_at": data.get("updated_at"),
                    })

    # 3) Chart cache (last resort)
    import glob as _glob
    for cf in sorted(_glob.glob(str(BASE_DIR / "cache" / f"chart_{code}_*.json")), reverse=True):
        try:
            chart = json.loads(open(cf, encoding="utf-8").read())
            closes = chart.get("close", [])
            if len(closes) >= 2:
                price = int(closes[-1])
                prev  = int(closes[-2])
                return jsonify({
                    "code":       code,
                    "name":       chart.get("name", code),
                    "price":      price,
                    "prev_close": prev,
                    "change":     price - prev,
                    "change_pct": round((price / prev - 1) * 100, 2) if prev else 0,
                    "source":     "chart_cache",
                    "fetched_at": chart.get("dates", [None])[-1],
                })
        except Exception:
            continue

    return jsonify({"error": "종목 없음"}), 404


def _fetch_naver_minute_candles(code: str, start_date: str, end_date: str) -> list[dict] | None:
    """
    네이버 금융 분봉 API 호출 — 1분봉 raw 데이터 반환.
    URL: https://api.stock.naver.com/chart/domestic/item/{code}/minute
         ?startDateTime=YYYYMMDD&endDateTime=YYYYMMDD
    Returns: [{localDateTime, openPrice, highPrice, lowPrice, currentPrice,
               accumulatedTradingVolume}, ...] or None on failure
    """
    import urllib.request, urllib.error
    url = (f"https://api.stock.naver.com/chart/domestic/item/{code}/minute"
           f"?startDateTime={start_date}&endDateTime={end_date}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
        data = json.loads(body)
        return data if isinstance(data, list) else None
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
        print(f"[Naver 분봉 실패] {code}: {exc!r}")
        return None


def _aggregate_minute_candles(raw: list[dict], interval: int) -> dict:
    """
    1분봉 raw 리스트를 N분봉으로 집계.
    interval: 1, 5, 15, 30, 60
    Returns: {dates, open, high, low, close, volume} (분봉 차트용)

    버킷팅: (HHMM 분 // interval) * interval 로 분 단위 바닥.
    날짜+버킷분 조합이 키. 같은 키 안에서 open=첫, high=max, low=min, close=마지막,
    volume=합.
    """
    if not raw or interval < 1:
        return {"dates": [], "open": [], "high": [], "low": [], "close": [], "volume": []}

    buckets: dict[str, dict] = {}
    order: list[str] = []
    for c in raw:
        dt = c.get("localDateTime", "")
        if len(dt) < 12:
            continue
        # YYYYMMDDHHMMSS → date YYYYMMDD, HH, MM
        date  = dt[:8]
        hour  = int(dt[8:10])
        minu  = int(dt[10:12])
        bucket_min = (hour * 60 + minu) // interval * interval
        bh = bucket_min // 60
        bm = bucket_min % 60
        key = f"{date}{bh:02d}{bm:02d}"

        try:
            o = float(c.get("openPrice", 0))
            h = float(c.get("highPrice", 0))
            l = float(c.get("lowPrice", 0))
            cl = float(c.get("currentPrice", 0) or c.get("closePrice", 0))
            v = float(c.get("accumulatedTradingVolume", 0) or 0)
        except (TypeError, ValueError):
            continue

        b = buckets.get(key)
        if b is None:
            buckets[key] = {"o": o, "h": h, "l": l, "c": cl, "v": v}
            order.append(key)
        else:
            if h > b["h"]: b["h"] = h
            if l < b["l"]: b["l"] = l
            b["c"] = cl     # raw 가 시간 순이라 마지막이 close
            b["v"] += v

    dates, opens, highs, lows, closes, volumes = [], [], [], [], [], []
    for key in order:
        b = buckets[key]
        # key = YYYYMMDDHHMM → 'MM-DD HH:MM' 표시
        label = f"{key[4:6]}-{key[6:8]} {key[8:10]}:{key[10:12]}"
        dates.append(label)
        opens.append(int(b["o"]))
        highs.append(int(b["h"]))
        lows.append(int(b["l"]))
        closes.append(int(b["c"]))
        volumes.append(int(b["v"]))

    return {"dates": dates, "open": opens, "high": highs, "low": lows,
            "close": closes, "volume": volumes}


@app.route("/api/chart_intraday/<code>")
def api_chart_intraday(code: str):
    """
    분봉 차트 — 네이버 금융 분봉 API 에서 1분봉을 받아 N분봉으로 집계.
    Query params:
      - timeframe: 1, 5, 15, 30, 60  (default 5)
      - days:      1 (당일), 3, 5, 10  (default 1)
    캐시: cache/intraday_{code}_{tf}m_{days}d_{date}.json
      장중 5분 / 장외 24h TTL
    응답 스키마는 /api/chart 와 동일하므로 프론트의 _drawCandles 등 재사용 가능.
    """
    import re as _re
    if not _re.fullmatch(r"\d{6}", code):
        return jsonify({"error": "잘못된 종목코드"}), 400

    try:
        timeframe = int(request.args.get("timeframe", "5"))
    except ValueError:
        timeframe = 5
    if timeframe not in (1, 5, 15, 30, 60):
        timeframe = 5

    try:
        days = int(request.args.get("days", "1"))
    except ValueError:
        days = 1
    days = max(1, min(10, days))

    today = _get_trading_date()
    cache_file = BASE_DIR / "cache" / f"intraday_{code}_{timeframe}m_{days}d_{today}.json"
    if cache_file.exists():
        try:
            age_min = (now_kst().timestamp() - cache_file.stat().st_mtime) / 60
            ttl = 5 if is_market_hours() else 1440
            if age_min < ttl:
                return Response(
                    cache_file.read_text(encoding="utf-8"),
                    content_type="application/json; charset=utf-8",
                )
        except Exception:
            pass

    # 시작일 — 거래일 기준 N일을 캘린더 기준 약 1.6배로 여유 잡음 (주말/공휴일)
    start_dt = datetime.strptime(today, "%Y%m%d").replace(tzinfo=KST) \
               - timedelta(days=int(days * 1.6) + 2)
    start_str = start_dt.strftime("%Y%m%d")

    raw = _fetch_naver_minute_candles(code, start_str, today)
    if not raw:
        return jsonify({"error": "분봉 데이터 없음", "source": "naver_finance"}), 502

    bars = _aggregate_minute_candles(raw, timeframe)
    if not bars["close"]:
        return jsonify({"error": "집계 결과 비어있음"}), 502

    # 요청한 days 만큼만 trim (raw 가 더 많은 날을 줄 수 있음)
    distinct_dates = []
    for d in bars["dates"]:
        ymd = d[:5]   # 'MM-DD'
        if ymd not in distinct_dates:
            distinct_dates.append(ymd)
    if len(distinct_dates) > days:
        keep_dates = set(distinct_dates[-days:])
        keep_idx = [i for i, d in enumerate(bars["dates"]) if d[:5] in keep_dates]
        for k in ("dates", "open", "high", "low", "close", "volume"):
            bars[k] = [bars[k][i] for i in keep_idx]

    closes  = bars["close"]
    highs   = bars["high"]
    lows    = bars["low"]
    volumes = bars["volume"]

    # 보조지표 — 일봉과 동일한 헬퍼 재사용
    bollinger  = _calc_bollinger(closes)
    fibonacci  = _calc_fibonacci(highs, lows)
    trendlines = _calc_trendlines(highs, lows, closes)
    analysis   = _generate_analysis(closes, volumes, bollinger, fibonacci, trendlines)

    result = {
        "code":        code,
        "name":        _get_stock_name(code) or code,
        "chart_type":  "intraday",
        "timeframe":   timeframe,
        "days":        days,
        "dates":       bars["dates"],
        "open":        bars["open"],
        "high":        highs,
        "low":         lows,
        "close":       closes,
        "volume":      volumes,
        "bollinger":   bollinger,
        "fibonacci":   fibonacci,
        "trendlines":  trendlines,
        "analysis":    analysis,
        "source":      "naver_finance",
        "fetched_at":  now_kst().strftime("%Y-%m-%d %H:%M:%S"),
    }
    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return jsonify(result)


@app.route("/api/chart/<code>")
def api_chart(code: str):
    import re as _re
    if not _re.fullmatch(r"\d{6}", code):
        return jsonify({"error": "잘못된 종목코드"}), 400

    # Phase 12-1: 기간 파라미터 (기본 180일). 클램프 7~3650.
    try:
        days = int(request.args.get("days", "180"))
    except ValueError:
        days = 180
    days = max(7, min(3650, days))

    today      = _get_trading_date()
    cache_file = BASE_DIR / "cache" / f"chart_{code}_{days}d_{today}.json"
    if cache_file.exists():
        return Response(
            cache_file.read_text(encoding="utf-8"),
            content_type="application/json; charset=utf-8",
        )
    # 하위 호환: 기존 이름(180일 기본) 캐시가 있으면 그대로 사용
    if days == 180:
        legacy = BASE_DIR / "cache" / f"chart_{code}_{today}.json"
        if legacy.exists():
            return Response(
                legacy.read_text(encoding="utf-8"),
                content_type="application/json; charset=utf-8",
            )

    try:
        from pykrx import stock as _stock
    except ImportError:
        return jsonify({"error": "pykrx 미설치"}), 500

    start = (datetime.strptime(today, "%Y%m%d").replace(tzinfo=KST) - timedelta(days=days)).strftime("%Y%m%d")
    try:
        df = _stock.get_market_ohlcv_by_date(start, today, code)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    if df is None or df.empty:
        return jsonify({"error": "데이터 없음"}), 404

    dates   = [d.strftime("%Y-%m-%d") for d in df.index]
    opens   = [int(v) for v in df["시가"].tolist()]
    highs   = [int(v) for v in df["고가"].tolist()]
    lows    = [int(v) for v in df["저가"].tolist()]
    closes  = [int(v) for v in df["종가"].tolist()]
    volumes = [int(v) for v in df["거래량"].tolist()]

    bollinger  = _calc_bollinger(closes)
    fibonacci  = _calc_fibonacci(highs, lows)
    trendlines = _calc_trendlines(highs, lows, closes)
    analysis   = _generate_analysis(closes, volumes, bollinger, fibonacci, trendlines)

    try:
        name = _stock.get_market_ticker_name(code)
    except Exception:
        name = code

    result = {
        "code": code, "name": name,
        "days":  days,
        "dates": dates,
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": volumes,
        "bollinger": bollinger,
        "fibonacci": fibonacci,
        "trendlines": trendlines,
        "analysis": analysis,
    }
    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify(result)


# 정적 파일 (themes_mapping.json, cache/ 등) 서빙
@app.route("/<path:filename>")
def static_file(filename: str):
    target = BASE_DIR / filename
    if not target.exists() or not target.is_file():
        return Response("Not Found", status=404)
    # cache/ 폴더 직접 접근은 보안상 차단
    if filename.startswith("cache/") or filename.startswith("cache\\"):
        return Response("Forbidden", status=403)
    return send_file(target)


# ─────────────────────────────────────────────────────────────────────────────
# 시작 루틴 (gunicorn import / 직접 실행 양쪽 모두에서 호출)
# ─────────────────────────────────────────────────────────────────────────────
_startup_done = False

def _startup():
    global _startup_done, _scheduler
    if _startup_done:
        return
    _startup_done = True

    if not FETCHER.exists():
        log.error("data_fetcher.py 를 찾을 수 없습니다: %s", FETCHER)
        return

    # data.json 이 없거나 오래됐으면 자동 수집
    if data_is_fresh():
        log.info("data.json 최신 상태 — 수집 생략")
    else:
        reason = "없음" if not DATA_JSON.exists() else "오늘 날짜 아님"
        log.info("data.json %s → data_fetcher.py 백그라운드 실행", reason)
        trigger_fetch(background=True)

    # Phase 10: Naver 업종 유니버스 백그라운드 빌드 (비차단)
    #   일 1회, 약 79 섹터 × 0.25s ≈ 20 초 소요.
    threading.Thread(target=_build_naver_universe_background,
                     daemon=True, name="naver-universe").start()

    # Phase 14: S&P 500 market 백그라운드 빌드
    #   일 1회, ~180 초 소요. 사용자가 [🇺🇸 미국] 토글 누르기 전에 완료되도록.
    threading.Thread(target=_build_us_market_background,
                     daemon=True, name="us-market-build").start()

    # APScheduler: 장중 자동 갱신
    if _SCHEDULER_OK:
        def _scheduled_update():
            if is_market_hours():
                log.info("⏰  장중 자동 갱신 트리거")
                trigger_fetch(background=True, force_market=True)
            else:
                log.debug("장외 — 자동 갱신 스킵")

        _scheduler = _BgScheduler(daemon=True)
        interval = _get()["interval_minutes"]
        _scheduler.add_job(
            _scheduled_update, "interval", minutes=interval,
            id="market_update", max_instances=1,
        )
        _scheduler.start()
        log.info("APScheduler 시작 — %d분 간격", interval)
    else:
        log.info("APScheduler 미설치 — 자동 갱신 비활성  (pip install apscheduler)")


# ─────────────────────────────────────────────────────────────────────────
# PHASE 15 — 종목 발굴 스코어링 (Stage 1 MVP: 국내, 모멘텀 + 섹터)
# ─────────────────────────────────────────────────────────────────────────
def _load_ticker_sparklines_kr() -> dict:
    """오늘자 ticker_data 캐시에서 sparklines 맵만 추출 ({code: [20 normalized prices]})."""
    today = _get_trading_date()
    f = BASE_DIR / "cache" / f"ticker_data_{today}.json"
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data.get("sparklines") or {}
    except Exception:
        return {}


def _load_naver_sector_aggregates() -> list:
    """sectors_naver_landing.json 의 sectors 배열 반환 (stale 허용)."""
    f = BASE_DIR / "cache" / "sectors_naver_landing.json"
    if not f.exists():
        return []
    try:
        return (json.loads(f.read_text(encoding="utf-8")) or {}).get("sectors", [])
    except Exception:
        return []


def _calc_momentum_score_kr(stock: dict, rank_change: int, rank_vol: int,
                            total: int, sparkline: list) -> tuple[int, dict, list]:
    """
    모멘텀 점수 (0~20). 반환: (score, sub_dict, explanation_list).
    서브: 등락률 순위(5) + 5d(5) + 20d(5) + 거래대금 순위(5).
    """
    sub = {"chg_rank": 0, "ret_5d": 0, "ret_20d": 0, "vol_rank": 0}
    expl: list = []

    if total > 0:
        pct = (rank_change / total) * 100
        sub["chg_rank"] = max(0, 5 - int(pct / 10))
        chg = stock.get("change_pct") or 0
        expl.append({
            "label":  "당일 등락률 순위",
            "detail": f"{chg:+.2f}% · 전체 중 상위 {pct:.0f}%",
            "pts":    sub["chg_rank"], "max": 5,
        })
    else:
        expl.append({"label": "당일 등락률 순위", "detail": "데이터 없음", "pts": 0, "max": 5})

    if sparkline and len(sparkline) >= 5 and sparkline[-5]:
        r5 = (sparkline[-1] / sparkline[-5] - 1) * 100
        if   r5 > 10: sub["ret_5d"] = 5
        elif r5 >  5: sub["ret_5d"] = 4
        elif r5 >  2: sub["ret_5d"] = 3
        elif r5 >  0: sub["ret_5d"] = 2
        elif r5 > -2: sub["ret_5d"] = 1
        expl.append({
            "label":  "5일 수익률",
            "detail": f"{r5:+.2f}%",
            "pts":    sub["ret_5d"], "max": 5,
        })
    else:
        expl.append({"label": "5일 수익률", "detail": "데이터 없음", "pts": 0, "max": 5})

    if sparkline and len(sparkline) >= 20 and sparkline[0]:
        r20 = (sparkline[-1] / sparkline[0] - 1) * 100
        if   r20 > 20: sub["ret_20d"] = 5
        elif r20 > 10: sub["ret_20d"] = 4
        elif r20 >  5: sub["ret_20d"] = 3
        elif r20 >  0: sub["ret_20d"] = 2
        elif r20 > -5: sub["ret_20d"] = 1
        expl.append({
            "label":  "20일 수익률",
            "detail": f"{r20:+.2f}%",
            "pts":    sub["ret_20d"], "max": 5,
        })
    else:
        expl.append({"label": "20일 수익률", "detail": "데이터 없음", "pts": 0, "max": 5})

    if total > 0:
        vpct = (rank_vol / total) * 100
        sub["vol_rank"] = max(0, 5 - int(vpct / 10))
        expl.append({
            "label":  "거래대금 순위",
            "detail": f"상위 {vpct:.0f}% · {(stock.get('volume_mn') or 0):,}백만원",
            "pts":    sub["vol_rank"], "max": 5,
        })

    return sum(sub.values()), sub, expl


def _calc_sector_score_kr(stock: dict, sector_by_name: dict,
                          sector_rank_by_name: dict, total_sectors: int) -> tuple[int, dict, list]:
    """섹터 점수 (0~15): 섹터 등락률 순위(5) + 절대 수준(5) + up/total 비율(5)."""
    sub = {"sect_rank": 0, "sect_level": 0, "sect_up_ratio": 0}
    expl: list = []
    names = stock.get("sectors") or []
    if not names or not sector_by_name:
        expl.append({"label": "섹터 정보", "detail": "섹터 집계 없음", "pts": 0, "max": 15})
        return 0, sub, expl

    best_score = -1
    best_sub = sub
    best_expl: list = []
    for name in names:
        info = sector_by_name.get(name)
        if not info:
            continue
        s = {"sect_rank": 0, "sect_level": 0, "sect_up_ratio": 0}
        e: list = []

        if total_sectors:
            rk = sector_rank_by_name.get(name, total_sectors)
            pct = (rk / total_sectors) * 100
            s["sect_rank"] = max(0, 5 - int(pct / 10))
            e.append({
                "label":  "섹터 등락률 순위",
                "detail": f"{name} · 전체 {total_sectors}개 섹터 중 상위 {pct:.0f}%",
                "pts":    s["sect_rank"], "max": 5,
            })

        chg = info.get("change_pct") or 0
        if   chg >  3: s["sect_level"] = 5
        elif chg >  2: s["sect_level"] = 4
        elif chg >  1: s["sect_level"] = 3
        elif chg >  0: s["sect_level"] = 2
        elif chg > -1: s["sect_level"] = 1
        e.append({
            "label":  "섹터 당일 등락률",
            "detail": f"{chg:+.2f}%",
            "pts":    s["sect_level"], "max": 5,
        })

        up = info.get("up") or 0
        tot = info.get("total") or 0
        if tot:
            ratio = up / tot
            if   ratio > 0.8: s["sect_up_ratio"] = 5
            elif ratio > 0.6: s["sect_up_ratio"] = 4
            elif ratio > 0.5: s["sect_up_ratio"] = 3
            elif ratio > 0.4: s["sect_up_ratio"] = 2
            elif ratio > 0.3: s["sect_up_ratio"] = 1
            e.append({
                "label":  "섹터 내 상승 비율",
                "detail": f"{up}/{tot} 종목 상승 ({ratio*100:.0f}%)",
                "pts":    s["sect_up_ratio"], "max": 5,
            })

        total_s = sum(s.values())
        if total_s > best_score:
            best_score = total_s
            best_sub = s
            best_expl = e

    if best_score < 0:
        return 0, sub, [{"label": "섹터 정보", "detail": "매칭된 섹터 없음", "pts": 0, "max": 15}]
    return best_score, best_sub, best_expl


def _calc_momentum_score_us(stock: dict, rank_change: int, rank_vol: int,
                            total: int) -> tuple[int, dict, list]:
    """
    US 모멘텀 (0~20). 이력 없이 당일 지표만으로 구성.
    서브: 등락률 순위(5) + 거래대금 순위(5) + 등락률 절대 수준(5) + 거래대금 절대 수준(5).
    """
    sub = {"chg_rank": 0, "vol_rank": 0, "chg_level": 0, "vol_level": 0}
    expl: list = []

    if total > 0:
        pct = (rank_change / total) * 100
        sub["chg_rank"] = max(0, 5 - int(pct / 10))
        chg = stock.get("change_pct") or 0
        expl.append({
            "label":  "당일 등락률 순위",
            "detail": f"{chg:+.2f}% · S&P500 내 상위 {pct:.0f}%",
            "pts":    sub["chg_rank"], "max": 5,
        })

    chg = stock.get("change_pct") or 0
    if   chg >  5: sub["chg_level"] = 5
    elif chg >  3: sub["chg_level"] = 4
    elif chg >  2: sub["chg_level"] = 3
    elif chg >  1: sub["chg_level"] = 2
    elif chg >  0: sub["chg_level"] = 1
    expl.append({
        "label":  "등락률 절대 수준",
        "detail": f"{chg:+.2f}%",
        "pts":    sub["chg_level"], "max": 5,
    })

    if total > 0:
        vpct = (rank_vol / total) * 100
        sub["vol_rank"] = max(0, 5 - int(vpct / 10))
        expl.append({
            "label":  "거래대금 순위",
            "detail": f"상위 {vpct:.0f}% · ${(stock.get('volume_mn') or 0):,.0f}M",
            "pts":    sub["vol_rank"], "max": 5,
        })

    vol_mn = stock.get("volume_mn") or 0
    if   vol_mn > 10_000: sub["vol_level"] = 5
    elif vol_mn >  5_000: sub["vol_level"] = 4
    elif vol_mn >  2_000: sub["vol_level"] = 3
    elif vol_mn >  1_000: sub["vol_level"] = 2
    elif vol_mn >    500: sub["vol_level"] = 1
    expl.append({
        "label":  "거래대금 절대 수준",
        "detail": f"${vol_mn:,.0f}M",
        "pts":    sub["vol_level"], "max": 5,
    })

    return sum(sub.values()), sub, expl


def _calc_sector_score_us(stock: dict, sector_by_name: dict,
                          sector_rank_by_name: dict, total_sectors: int) -> tuple[int, dict, list]:
    """US 섹터 점수 (0~15). us_market.json 의 sectors 구조 기준."""
    sub = {"sect_rank": 0, "sect_level": 0, "sect_up_ratio": 0}
    expl: list = []
    name = stock.get("sector") or ""
    info = sector_by_name.get(name)
    if not info:
        expl.append({"label": "섹터", "detail": "섹터 미매칭", "pts": 0, "max": 15})
        return 0, sub, expl

    if total_sectors:
        rk = sector_rank_by_name.get(name, total_sectors)
        pct = (rk / total_sectors) * 100
        sub["sect_rank"] = max(0, 5 - int(pct / 10))
        expl.append({
            "label":  "섹터 등락률 순위",
            "detail": f"{name} · GICS {total_sectors}개 섹터 중 상위 {pct:.0f}%",
            "pts":    sub["sect_rank"], "max": 5,
        })

    chg = info.get("weighted_avg_pct") or 0
    if   chg >  3: sub["sect_level"] = 5
    elif chg >  2: sub["sect_level"] = 4
    elif chg >  1: sub["sect_level"] = 3
    elif chg >  0: sub["sect_level"] = 2
    elif chg > -1: sub["sect_level"] = 1
    expl.append({
        "label":  "섹터 당일 등락률",
        "detail": f"{chg:+.2f}% (가중평균)",
        "pts":    sub["sect_level"], "max": 5,
    })

    stocks_in = info.get("stocks") or []
    if stocks_in:
        up = sum(1 for s in stocks_in if (s.get("change_pct") or 0) > 0)
        tot = len(stocks_in)
        ratio = up / tot
        if   ratio > 0.8: sub["sect_up_ratio"] = 5
        elif ratio > 0.6: sub["sect_up_ratio"] = 4
        elif ratio > 0.5: sub["sect_up_ratio"] = 3
        elif ratio > 0.4: sub["sect_up_ratio"] = 2
        elif ratio > 0.3: sub["sect_up_ratio"] = 1
        expl.append({
            "label":  "섹터 내 상승 비율",
            "detail": f"{up}/{tot} 종목 상승 ({ratio*100:.0f}%)",
            "pts":    sub["sect_up_ratio"], "max": 5,
        })

    return sum(sub.values()), sub, expl


def _calc_flow_score_us(info: dict) -> tuple[int, dict, list]:
    """US 수급 점수 (0~25): 기관 보유(10) + 내부자 보유(5) + 애널리스트 의견(10)."""
    sub = {"institutions": 0, "insiders": 0, "analyst": 0}
    expl: list = []
    if not info:
        expl.append({"label": "yfinance info", "detail": "데이터 없음", "pts": 0, "max": 25})
        return 0, sub, expl

    inst = info.get("heldPercentInstitutions")
    if inst is not None:
        if   inst >= 0.90: sub["institutions"] = 10
        elif inst >= 0.80: sub["institutions"] = 8
        elif inst >= 0.70: sub["institutions"] = 6
        elif inst >= 0.50: sub["institutions"] = 4
        elif inst >= 0.30: sub["institutions"] = 2
        expl.append({
            "label":  "기관 보유 비중",
            "detail": f"{inst*100:.1f}%",
            "pts":    sub["institutions"], "max": 10,
        })
    else:
        expl.append({"label": "기관 보유 비중", "detail": "데이터 없음", "pts": 0, "max": 10})

    ins = info.get("heldPercentInsiders")
    if ins is not None:
        if   ins >= 0.10: sub["insiders"] = 5
        elif ins >= 0.05: sub["insiders"] = 3
        elif ins >= 0.01: sub["insiders"] = 1
        expl.append({
            "label":  "내부자 보유 비중",
            "detail": f"{ins*100:.2f}%",
            "pts":    sub["insiders"], "max": 5,
        })
    else:
        expl.append({"label": "내부자 보유 비중", "detail": "데이터 없음", "pts": 0, "max": 5})

    rec = info.get("recommendationKey")
    rec_map = {"strong_buy": 10, "buy": 8, "hold": 4, "sell": 0, "strong_sell": 0}
    if rec:
        sub["analyst"] = rec_map.get(str(rec).lower(), 0)
        n_analysts = info.get("numberOfAnalystOpinions")
        detail = str(rec).replace("_", " ").title()
        if n_analysts:
            detail += f" ({n_analysts}명)"
        expl.append({
            "label":  "애널리스트 의견",
            "detail": detail,
            "pts":    sub["analyst"], "max": 10,
        })
    else:
        expl.append({"label": "애널리스트 의견", "detail": "데이터 없음", "pts": 0, "max": 10})

    return sum(sub.values()), sub, expl


def _calc_valuation_score_us(info: dict, high_52w: float | None,
                             current_price: float | None) -> tuple[int, dict, list]:
    """US 밸류 (0~20): Trailing PER(7) + fwd<trail 보너스(3) + PBR(5) + 52w 괴리(5)."""
    sub = {"per": 0, "fwd_bonus": 0, "pbr": 0, "high_gap": 0}
    expl: list = []
    if not info:
        expl.append({"label": "yfinance info", "detail": "데이터 없음", "pts": 0, "max": 20})
        return 0, sub, expl

    tpe = info.get("trailingPE")
    fpe = info.get("forwardPE")

    if tpe and tpe > 0:
        if   tpe < 10: sub["per"] = 7
        elif tpe < 15: sub["per"] = 5
        elif tpe < 20: sub["per"] = 3
        elif tpe < 25: sub["per"] = 1
        expl.append({
            "label":  "Trailing PER",
            "detail": f"PER {tpe:.2f}",
            "pts":    sub["per"], "max": 7,
        })
    else:
        expl.append({"label": "Trailing PER", "detail": "미제공 또는 적자", "pts": 0, "max": 7})

    if tpe and fpe and tpe > 0 and fpe > 0 and fpe < tpe:
        sub["fwd_bonus"] = 3
        expl.append({
            "label":  "Forward vs Trailing",
            "detail": f"Forward {fpe:.2f} < Trailing {tpe:.2f} (실적 개선 기대)",
            "pts":    3, "max": 3,
        })
    else:
        expl.append({
            "label":  "Forward vs Trailing",
            "detail": "개선 기대 없음 또는 데이터 부족",
            "pts":    0, "max": 3,
        })

    pbr = info.get("priceToBook")
    if pbr and pbr > 0:
        if   pbr < 1: sub["pbr"] = 5
        elif pbr < 2: sub["pbr"] = 3
        elif pbr < 3: sub["pbr"] = 1
        expl.append({
            "label":  "PBR",
            "detail": f"PBR {pbr:.2f}",
            "pts":    sub["pbr"], "max": 5,
        })
    else:
        expl.append({"label": "PBR", "detail": "미제공", "pts": 0, "max": 5})

    if high_52w and current_price and high_52w > 0:
        gap = (high_52w - current_price) / high_52w * 100
        if   gap > 40: sub["high_gap"] = 5
        elif gap > 30: sub["high_gap"] = 4
        elif gap > 20: sub["high_gap"] = 3
        elif gap > 10: sub["high_gap"] = 2
        expl.append({
            "label":  "52주 고점 대비",
            "detail": f"-{gap:.1f}% · 고점 ${high_52w:.2f}",
            "pts":    sub["high_gap"], "max": 5,
        })
    else:
        expl.append({"label": "52주 고점 대비", "detail": "데이터 없음", "pts": 0, "max": 5})

    return sum(sub.values()), sub, expl


def _stage1_prefilter_us(min_volume_mn: float = 10, top_k: int = 100) -> dict:
    """미국 S&P 500 에서 모멘텀+섹터 점수로 상위 top_k 추출 (캐시 데이터만 사용)."""
    us_data = _fetch_us_market_data()
    if "error" in us_data:
        return {"error": us_data["error"], "items": [], "total_scanned": 0}

    all_stocks = us_data.get("all_stocks") or []
    sectors = us_data.get("sectors") or []
    eligible = [s for s in all_stocks if (s.get("volume_mn") or 0) >= min_volume_mn]
    total = len(eligible)
    if not total:
        return {"error": "US 조건 통과 종목 없음", "items": [], "total_scanned": 0}

    # 랭킹
    sorted_by_chg = sorted(eligible, key=lambda x: x.get("change_pct") or 0, reverse=True)
    rank_change = {s["symbol"]: i for i, s in enumerate(sorted_by_chg)}
    sorted_by_vol = sorted(eligible, key=lambda x: x.get("volume_mn") or 0, reverse=True)
    rank_vol = {s["symbol"]: i for i, s in enumerate(sorted_by_vol)}

    sector_by_name = {x["name"]: x for x in sectors}
    sorted_sectors = sorted(sectors, key=lambda x: x.get("weighted_avg_pct") or 0, reverse=True)
    sector_rank_by_name = {x["name"]: i for i, x in enumerate(sorted_sectors)}

    results = []
    for s in eligible:
        sym = s["symbol"]
        mom, mom_sub, mom_expl = _calc_momentum_score_us(
            s, rank_change[sym], rank_vol[sym], total
        )
        sect, sect_sub, sect_expl = _calc_sector_score_us(
            s, sector_by_name, sector_rank_by_name, len(sectors)
        )
        results.append({
            "code":       sym,
            "name":       s.get("name"),
            "market":     "us",
            "sector":     s.get("sector"),
            "price":      s.get("price"),
            "change_pct": s.get("change_pct"),
            "volume_mn":  s.get("volume_mn"),
            "total_score": mom + sect,
            "scores": {
                "momentum": mom,
                "sector":   sect,
                "flow":     None,
                "valuation": None,
                "technical": None,
                "undervalued_bonus": 0,
            },
            "sub_scores":   {"momentum": mom_sub,  "sector": sect_sub},
            "explanations": {"momentum": mom_expl, "sector": sect_expl},
        })

    results.sort(key=lambda x: x["total_score"], reverse=True)
    return {
        "updated_at":    now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "market":        "us",
        "stage":         1,
        "total_scanned": total,
        "items":         results[:top_k],
    }


def _stage1_prefilter_kr(min_volume_mn: int = 100, top_k: int = 200) -> dict:
    """국내 전 종목에서 모멘텀+섹터 점수로 상위 top_k 추출."""
    universe = _load_naver_universe()
    stocks_map = (universe or {}).get("stocks") or {}
    if not stocks_map:
        return {"error": "naver_universe 캐시 없음", "items": [], "total_scanned": 0}

    sparklines = _load_ticker_sparklines_kr()
    sectors = _load_naver_sector_aggregates()

    # 거래대금 필터 적용 후 남은 종목만 랭킹
    eligible = [s for s in stocks_map.values()
                if (s.get("volume_mn") or 0) >= min_volume_mn]
    total = len(eligible)
    if not total:
        return {"error": "조건 통과 종목 없음", "items": [], "total_scanned": 0}

    sorted_by_chg = sorted(eligible, key=lambda x: x.get("change_pct") or 0, reverse=True)
    rank_change = {s["code"]: i for i, s in enumerate(sorted_by_chg)}
    sorted_by_vol = sorted(eligible, key=lambda x: x.get("volume_mn") or 0, reverse=True)
    rank_vol = {s["code"]: i for i, s in enumerate(sorted_by_vol)}

    # 섹터 랭킹 (|change_pct| 내림차순)
    sector_by_name = {x["name"]: x for x in sectors}
    sorted_sectors = sorted(sectors, key=lambda x: abs(x.get("change_pct") or 0), reverse=True)
    sector_rank_by_name = {x["name"]: i for i, x in enumerate(sorted_sectors)}

    results = []
    for s in eligible:
        code = s["code"]
        spark = sparklines.get(code) or []
        mom, mom_sub, mom_expl = _calc_momentum_score_kr(
            s, rank_change[code], rank_vol[code], total, spark
        )
        sect, sect_sub, sect_expl = _calc_sector_score_kr(
            s, sector_by_name, sector_rank_by_name, len(sectors)
        )
        results.append({
            "code": code,
            "name": s.get("name"),
            "market": "kr",
            "sector": (s.get("sectors") or [None])[0],
            "price": s.get("close"),
            "change_pct": s.get("change_pct"),
            "volume_mn": s.get("volume_mn"),
            "total_score": mom + sect,
            "scores": {
                "momentum": mom,
                "sector": sect,
                "flow": None,
                "valuation": None,
                "technical": None,
                "undervalued_bonus": 0,
            },
            "sub_scores":   {"momentum": mom_sub,  "sector": sect_sub},
            "explanations": {"momentum": mom_expl, "sector": sect_expl},
        })

    results.sort(key=lambda x: x["total_score"], reverse=True)
    return {
        "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "market": "kr",
        "stage": 1,
        "total_scanned": total,
        "items": results[:top_k],
    }


def _read_fresh_json(path, ttl_min: float) -> dict | None:
    if not path.exists():
        return None
    try:
        age_min = (now_kst().timestamp() - path.stat().st_mtime) / 60
        if age_min < ttl_min:
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────
# PHASE 17 — 리서치: 섹터/기업 리포트 수집 + 추천 스코어링
# ─────────────────────────────────────────────────────────────────────────
_BUY_WORDS  = {"매수", "buy", "strong buy", "outperform", "비중확대", "trading buy"}
_HOLD_WORDS = {"중립", "hold", "neutral", "시장수익률", "marketperform"}
_SELL_WORDS = {"매도", "sell", "strong sell", "underperform", "비중축소"}


def _scrape_naver_research_list(kind: str, pages: int) -> list[dict]:
    """
    네이버 리서치 목록 페이지 스크래핑.
    kind: 'industry' (산업분석) 또는 'company' (기업분석).
    반환: list of dict. 각 item 의 필드는 아래 주석 참고.
    """
    import requests as _rq
    from bs4 import BeautifulSoup
    out: list[dict] = []
    for page in range(1, pages + 1):
        url = f"https://finance.naver.com/research/{kind}_list.naver"
        try:
            res = _rq.get(
                url, params={"page": page},
                headers={"User-Agent": "Mozilla/5.0"}, timeout=10,
            )
            res.encoding = "euc-kr"
            soup = BeautifulSoup(res.text, "html.parser")
        except Exception as exc:
            log.debug("research %s page %d fail: %s", kind, page, exc)
            continue

        table = soup.select_one("table.type_1")
        if not table:
            continue
        for row in table.select("tr"):
            cols = row.select("td")
            if len(cols) < 5:
                continue
            name_tag  = cols[0].select_one("a")
            title_tag = cols[1].select_one("a")
            if not title_tag:
                continue

            entry: dict = {
                "title":  title_tag.get_text(strip=True),
                "broker": cols[2].get_text(strip=True) if len(cols) > 2 else "",
                "date":   cols[4].get_text(strip=True) if len(cols) > 4 else "",
            }
            detail_href = title_tag.get("href", "")
            if detail_href:
                entry["detail_link"] = f"https://finance.naver.com/research/{detail_href}"
                m = re.search(r"nid=(\d+)", detail_href)
                if m:
                    entry["nid"] = m.group(1)

            # PDF 링크 (컬럼 3 또는 row 전체에서)
            pdf_a = cols[3].select_one('a[href$=".pdf"]') if len(cols) > 3 else None
            if not pdf_a:
                for a in row.select('a[href$=".pdf"]'):
                    pdf_a = a; break
            entry["pdf_url"] = pdf_a.get("href", "") if pdf_a else ""

            if kind == "industry":
                entry["sector"] = cols[0].get_text(strip=True)
            else:
                entry["stock_name"] = name_tag.get_text(strip=True) if name_tag else cols[0].get_text(strip=True)
                code_href = (name_tag.get("href") or "") if name_tag else ""
                m = re.search(r"code=(\d{6})", code_href)
                entry["stock_code"] = m.group(1) if m else ""

            out.append(entry)
        time.sleep(0.3)
    return out


@app.route("/api/research/sectors")
def api_research_sectors():
    """네이버 산업분석 리스트 + 섹터별 빈도. 6시간 캐시."""
    cache_file = BASE_DIR / "cache" / "sector_reports.json"
    cached = _read_fresh_json(cache_file, 360)
    if cached:
        return jsonify(cached)

    reports = _scrape_naver_research_list("industry", pages=3)
    freq: dict[str, int] = {}
    for r in reports:
        s = r.get("sector") or "기타"
        freq[s] = freq.get(s, 0) + 1
    freq_sorted = sorted(freq.items(), key=lambda x: x[1], reverse=True)

    result = {
        "updated_at":       now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "count":            len(reports),
        "reports":          reports,
        "sector_frequency": freq_sorted,
    }
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


@app.route("/api/research/companies")
def api_research_companies():
    """네이버 기업분석 리스트 + 종목별 리포트 수. 3시간 캐시."""
    cache_file = BASE_DIR / "cache" / "company_reports.json"
    cached = _read_fresh_json(cache_file, 180)
    if cached:
        return jsonify(cached)

    reports = _scrape_naver_research_list("company", pages=5)

    # 종목 단위 집계 (목표가/의견은 list 에 없으므로 None → recommend 엔드포인트에서 보강)
    consensus: dict[str, dict] = {}
    for r in reports:
        code = r.get("stock_code")
        if not code:
            continue
        c = consensus.setdefault(code, {
            "code":         code,
            "name":         r.get("stock_name", ""),
            "report_count": 0,
            "brokers":      set(),
            "latest_date":  "",
            "latest_title": "",
        })
        c["report_count"] += 1
        if r.get("broker"):
            c["brokers"].add(r["broker"])
        if r.get("date") and (not c["latest_date"] or r["date"] > c["latest_date"]):
            c["latest_date"]  = r["date"]
            c["latest_title"] = r.get("title", "")

    consensus_list = []
    for c in consensus.values():
        c["brokers"] = sorted(c["brokers"])
        consensus_list.append(c)
    consensus_list.sort(key=lambda x: x["report_count"], reverse=True)

    result = {
        "updated_at":   now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "report_count": len(reports),
        "reports":      reports,
        "consensus":    consensus_list,
    }
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


def _kr_current_prices() -> dict:
    """naver_universe 에서 {code: close} 맵 구성."""
    uni = _load_naver_universe()
    stocks = (uni or {}).get("stocks") or {}
    return {code: (s.get("close") or 0) for code, s in stocks.items() if s.get("close")}


@app.route("/api/research/recommend")
def api_research_recommend():
    """
    기업 리포트 기반 종목 추천.
    상위 20 종목에 대해 최신 리포트 detail 을 파싱해 목표가/의견 보강.
    점수 = 리포트 모멘텀(30) + 컨센서스(30) + 괴리율(40).
    3시간 캐시.
    """
    cache_file = BASE_DIR / "cache" / "research_recommend.json"
    cached = _read_fresh_json(cache_file, 180)
    if cached:
        return jsonify(cached)

    # 기업 리포트 데이터 로드 (없으면 즉시 스크래핑)
    company_cache = BASE_DIR / "cache" / "company_reports.json"
    company_data = _read_fresh_json(company_cache, 180)
    if not company_data:
        # 캐시가 없으면 스크래핑
        reports = _scrape_naver_research_list("company", pages=5)
        company_data = {"reports": reports, "consensus": []}
        consensus_map: dict[str, dict] = {}
        for r in reports:
            code = r.get("stock_code")
            if not code:
                continue
            c = consensus_map.setdefault(code, {
                "code": code, "name": r.get("stock_name", ""),
                "report_count": 0, "brokers": set(),
                "latest_date": "", "latest_title": "",
            })
            c["report_count"] += 1
            if r.get("broker"):
                c["brokers"].add(r["broker"])
            if r.get("date") and (not c["latest_date"] or r["date"] > c["latest_date"]):
                c["latest_date"]  = r["date"]
                c["latest_title"] = r.get("title", "")
        for c in consensus_map.values():
            c["brokers"] = sorted(c["brokers"])
        company_data["consensus"] = sorted(consensus_map.values(),
                                           key=lambda x: x["report_count"], reverse=True)

    consensus_list = company_data.get("consensus") or []
    all_reports    = company_data.get("reports") or []
    prices         = _kr_current_prices()

    # 상위 20 종목에 대해 최신 리포트의 detail 파싱
    TOP_N = 20
    top_codes = [c["code"] for c in consensus_list[:TOP_N]]
    reports_by_code: dict[str, list[dict]] = {}
    for r in all_reports:
        code = r.get("stock_code")
        if code in top_codes:
            reports_by_code.setdefault(code, []).append(r)

    detail_by_code: dict[str, dict] = {}
    for code in top_codes:
        rlist = reports_by_code.get(code) or []
        if not rlist:
            continue
        rlist.sort(key=lambda x: x.get("date", ""), reverse=True)
        latest = rlist[0]
        if not latest.get("nid"):
            continue
        try:
            detail = _extract_report_html({
                "title":       latest.get("title", ""),
                "broker":      latest.get("broker", ""),
                "date":        latest.get("date", ""),
                "pdf_url":     latest.get("pdf_url", ""),
                "detail_link": latest.get("detail_link", ""),
            })
            detail_by_code[code] = detail
        except Exception as exc:
            log.debug("detail parse fail %s: %s", code, exc)
        time.sleep(0.3)

    # 전체 consensus 를 돌며 의견 집계 (모든 리포트 detail 을 파싱하지 않고 list-only 는 의견 불명)
    # 상위 20 중 detail 파싱된 것만 target_price/opinion 활용
    scored: list[dict] = []
    for c in consensus_list:
        code = c["code"]
        detail = detail_by_code.get(code) or {}
        target_price = detail.get("target_price")
        opinion      = detail.get("opinion")

        score = 0
        expl: list[str] = []

        # 1. 리포트 모멘텀 (30)
        rc = c.get("report_count", 0)
        if   rc >= 5: score += 30; expl.append(f"리포트 {rc}건 (관심 집중)")
        elif rc >= 3: score += 20; expl.append(f"리포트 {rc}건")
        elif rc >= 2: score += 10; expl.append(f"리포트 {rc}건")
        else:         score += 5;  expl.append(f"리포트 {rc}건")

        # 2. 컨센서스 방향 (30) — detail 파싱된 종목만 가능
        op_norm = (opinion or "").lower()
        if op_norm in _BUY_WORDS:
            score += 25
            expl.append(f"최신 의견: {opinion}")
        elif op_norm in _HOLD_WORDS:
            score += 10
            expl.append(f"최신 의견: {opinion}")
        elif op_norm in _SELL_WORDS:
            expl.append(f"최신 의견: {opinion}")
        else:
            expl.append("의견 데이터 없음")

        # 3. 목표주가 괴리율 (40)
        current_price = prices.get(code)
        upside = None
        if target_price and current_price and current_price > 0:
            upside = round((target_price / current_price - 1) * 100, 1)
            if   upside >= 50: score += 40; expl.append(f"목표가 +{upside}% (대폭 저평가)")
            elif upside >= 30: score += 30; expl.append(f"목표가 +{upside}%")
            elif upside >= 15: score += 20; expl.append(f"목표가 +{upside}%")
            elif upside >=  5: score += 10; expl.append(f"목표가 +{upside}%")
            elif upside >=  0: score += 5;  expl.append(f"목표가 근접 ({upside:+.1f}%)")
            else:              expl.append(f"목표가 하회 ({upside}%)")
        elif code in top_codes:
            expl.append("목표가 미제공")

        scored.append({
            "code":          code,
            "name":          c.get("name"),
            "market":        "kr",
            "score":         score,
            "explanation":   expl,
            "report_count":  rc,
            "opinion":       opinion,
            "target_price":  target_price,
            "current_price": current_price,
            "upside":        upside,
            "brokers":       c.get("brokers", []),
            "latest_date":   c.get("latest_date", ""),
            "latest_title":  c.get("latest_title", ""),
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    result = {
        "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "count":      len(scored),
        "items":      scored[:20],
    }
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


@app.route("/api/research/us_recommend")
def api_research_us_recommend():
    """Finnhub 애널리스트 추천 트렌드 + 목표가. 상위 50 심볼. 6시간 캐시."""
    finn = os.getenv("FINNHUB_API_KEY")
    if not finn:
        return jsonify({"error": "FINNHUB_API_KEY 미설정", "items": []}), 503

    cache_file = BASE_DIR / "cache" / "us_research_recommend.json"
    cached = _read_fresh_json(cache_file, 360)
    if cached:
        return jsonify(cached)

    sp500_syms, sp500_names = _load_sp500_symbols()
    if not sp500_syms:
        return jsonify({"error": "S&P500 리스트 없음", "items": []}), 503

    # 시총 상위는 sp500_tickers.json 순서상 앞쪽에 있지 않으므로 모두 시도
    symbols = sorted(sp500_syms)[:50]
    try:
        import requests as _rq
    except ImportError:
        return jsonify({"error": "requests 미설치"}), 500

    results = []
    for sym in symbols:
        try:
            r1 = _rq.get(
                "https://finnhub.io/api/v1/stock/recommendation",
                params={"symbol": sym, "token": finn}, timeout=5,
            )
            data = r1.json() if r1.status_code == 200 else []
            if not isinstance(data, list) or not data:
                continue
            latest = data[0]
            buy = (latest.get("buy") or 0) + (latest.get("strongBuy") or 0)
            hold = latest.get("hold") or 0
            sell = (latest.get("sell") or 0) + (latest.get("strongSell") or 0)
            total = buy + hold + sell
            if total == 0:
                continue
            buy_ratio = round(buy / total * 100)

            prev = data[1] if len(data) > 1 else {}
            prev_buy = (prev.get("buy") or 0) + (prev.get("strongBuy") or 0)
            prev_total = prev_buy + (prev.get("hold") or 0) + (prev.get("sell") or 0) + (prev.get("strongSell") or 0)
            prev_ratio = round(prev_buy / prev_total * 100) if prev_total > 0 else buy_ratio
            direction = "up" if buy_ratio > prev_ratio else "down" if buy_ratio < prev_ratio else "flat"

            # 목표가 (실패해도 계속)
            target_mean = target_high = target_low = None
            try:
                r2 = _rq.get(
                    "https://finnhub.io/api/v1/stock/price-target",
                    params={"symbol": sym, "token": finn}, timeout=5,
                )
                if r2.status_code == 200:
                    pt = r2.json() or {}
                    target_mean = pt.get("targetMean")
                    target_high = pt.get("targetHigh")
                    target_low  = pt.get("targetLow")
            except Exception:
                pass

            score = 0
            expl = []
            if   buy_ratio >= 80: score += 40; expl.append(f"매수비율 {buy_ratio}% (강한 공감대)")
            elif buy_ratio >= 60: score += 30; expl.append(f"매수비율 {buy_ratio}%")
            elif buy_ratio >= 40: score += 20; expl.append(f"매수비율 {buy_ratio}%")
            else:                 score += 10; expl.append(f"매수비율 {buy_ratio}%")
            if   direction == "up":   score += 20; expl.append(f"추세 상승 ({prev_ratio}% → {buy_ratio}%)")
            elif direction == "flat": score += 10; expl.append("추세 유지")
            else:                     expl.append(f"추세 하락 ({prev_ratio}% → {buy_ratio}%)")

            results.append({
                "code":            sym,
                "symbol":          sym,
                "name":            sp500_names.get(sym, sym),
                "market":          "us",
                "score":           score,
                "explanation":     expl,
                "buy":             buy, "hold": hold, "sell": sell,
                "buy_ratio":       buy_ratio,
                "prev_buy_ratio":  prev_ratio,
                "direction":       direction,
                "target_mean":     target_mean,
                "target_high":     target_high,
                "target_low":      target_low,
                "period":          latest.get("period", ""),
            })
            time.sleep(0.05)
        except Exception as exc:
            log.debug("finnhub rec %s fail: %s", sym, exc)
            continue

    results.sort(key=lambda x: x["score"], reverse=True)
    result = {
        "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "count":      len(results),
        "items":      results[:20],
    }
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


# ─────────────────────────────────────────────────────────────────────────
# PHASE 16 — 경제지표 + 실적발표 캘린더 (Finnhub / DART)
# ─────────────────────────────────────────────────────────────────────────
_COUNTRY_KR = {
    "US": "🇺🇸 미국", "KR": "🇰🇷 한국", "CN": "🇨🇳 중국",
    "JP": "🇯🇵 일본", "EU": "🇪🇺 유럽", "GB": "🇬🇧 영국", "DE": "🇩🇪 독일",
}
_EVENT_KR = {
    "CPI MoM": "소비자물가지수(CPI) 전월대비",
    "CPI YoY": "소비자물가지수(CPI) 전년대비",
    "Core CPI MoM": "근원 CPI 전월대비",
    "Core CPI YoY": "근원 CPI 전년대비",
    "GDP Growth Rate QoQ": "GDP 성장률 전분기대비",
    "GDP Growth Rate YoY": "GDP 성장률 전년대비",
    "Unemployment Rate": "실업률",
    "Non Farm Payrolls": "비농업 고용",
    "Interest Rate Decision": "기준금리 결정",
    "Retail Sales MoM": "소매판매 전월대비",
    "PPI MoM": "생산자물가지수 전월대비",
    "PPI YoY": "생산자물가지수 전년대비",
    "ISM Manufacturing PMI": "ISM 제조업 PMI",
    "ISM Services PMI": "ISM 서비스업 PMI",
    "Consumer Confidence": "소비자신뢰지수",
    "Initial Jobless Claims": "신규 실업수당청구건수",
    "Trade Balance": "무역수지",
    "Industrial Production MoM": "산업생산 전월대비",
    "Industrial Production YoY": "산업생산 전년대비",
    "Manufacturing PMI": "제조업 PMI",
    "Services PMI": "서비스업 PMI",
    "Housing Starts": "주택착공건수",
    "Building Permits": "건축허가건수",
    "Durable Goods Orders": "내구재 주문",
    "Fed Interest Rate Decision": "Fed 금리 결정",
    "BOJ Interest Rate Decision": "BOJ 금리 결정",
    "ECB Interest Rate Decision": "ECB 금리 결정",
    "BOK Interest Rate Decision": "한국은행 기준금리 결정",
}


def _load_sp500_symbols() -> tuple[set, dict]:
    """S&P500 심볼 세트 + {symbol: name} 맵. _sp500_tickers 캐시 재사용."""
    cache_file = BASE_DIR / "cache" / "sp500_tickers.json"
    if not cache_file.exists():
        return set(), {}
    try:
        tickers = json.loads(cache_file.read_text(encoding="utf-8"))
    except Exception:
        return set(), {}
    if not isinstance(tickers, list):
        return set(), {}
    syms = set()
    names = {}
    for t in tickers:
        sym = t.get("symbol")
        if sym:
            syms.add(sym)
            names[sym] = t.get("name") or sym
    return syms, names


def _calendar_date_range() -> tuple[str, str]:
    """이번 주 월요일 ~ 다음 주 금요일 (2주)."""
    today = now_kst().date()
    monday = today - timedelta(days=today.weekday())
    friday = monday + timedelta(days=11)
    return monday.strftime("%Y-%m-%d"), friday.strftime("%Y-%m-%d")


def _extract_econ_date(event: dict) -> str:
    """Finnhub 이벤트의 time 필드('2026-04-13 00:00:00')에서 날짜만 추출."""
    t = event.get("time") or ""
    if isinstance(t, str) and len(t) >= 10 and t[4] == "-":
        return t[:10]
    return ""


# ─────────────────────────────────────────────────────────────────────────
# PHASE 20 — DART 12분기 손익계산서 (슬림)
# 실제 DART API 는 집계 항목(매출액/원가/판관비/영업이익/순이익)만 반환하므로
# 세부 비용 분류(Tier 2) 는 불가능. 공헌이익률은 GPM 근사 + 수동 변동비율 입력으로 대체.
# ─────────────────────────────────────────────────────────────────────────
_DART_CORP_MAP_CACHE: dict | None = None


def _load_dart_corp_code_map() -> dict:
    """종목코드(6자리) → corp_code(8자리) 매핑. 1회 다운로드 후 파일+메모리 캐시."""
    global _DART_CORP_MAP_CACHE
    if _DART_CORP_MAP_CACHE is not None:
        return _DART_CORP_MAP_CACHE
    cache_file = BASE_DIR / "cache" / "dart_corp_codes.json"
    if cache_file.exists():
        try:
            _DART_CORP_MAP_CACHE = json.loads(cache_file.read_text(encoding="utf-8"))
            return _DART_CORP_MAP_CACHE
        except Exception:
            pass
    dart_key = os.getenv("DART_API_KEY")
    if not dart_key:
        _DART_CORP_MAP_CACHE = {}
        return _DART_CORP_MAP_CACHE
    try:
        import requests as _rq, io as _io, zipfile as _zf
        import xml.etree.ElementTree as _ET
        r = _rq.get("https://opendart.fss.or.kr/api/corpCode.xml",
                    params={"crtfc_key": dart_key}, timeout=30)
        if r.status_code != 200 or len(r.content) < 1000:
            _DART_CORP_MAP_CACHE = {}
            return _DART_CORP_MAP_CACHE
        z = _zf.ZipFile(_io.BytesIO(r.content))
        xml_data = z.read(z.namelist()[0])
        root = _ET.fromstring(xml_data)
        mapping: dict[str, str] = {}
        for corp in root.findall(".//list"):
            sc = (corp.findtext("stock_code") or "").strip()
            cc = (corp.findtext("corp_code") or "").strip()
            if sc and cc:
                mapping[sc] = cc
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(mapping, ensure_ascii=False),
                              encoding="utf-8")
        _DART_CORP_MAP_CACHE = mapping
        return mapping
    except Exception as exc:
        log.debug("dart corp code load fail: %s", exc)
        _DART_CORP_MAP_CACHE = {}
        return _DART_CORP_MAP_CACHE


_DART_IS_KEYWORDS = {
    "revenue":      ["매출액"],
    "cogs":         ["매출원가"],
    "gross_profit": ["매출총이익"],
    "sga":          ["판매비와관리비", "판관비"],
    "op_income":    ["영업이익"],
    "net_income":   ["당기순이익", "분기순이익", "반기순이익"],
}


def _extract_is_aggregates(is_items: list) -> dict:
    """
    DART IS/CIS 리스트에서 6개 집계 항목만 추출.
    thstrm_amount (당기금액) 를 그대로 사용 — 검증 결과 이미 개별 분기값이며 누적 아님.
    """
    out = {k: None for k in _DART_IS_KEYWORDS}
    for item in is_items:
        nm = (item.get("account_nm") or "").strip()
        amt_str = (item.get("thstrm_amount") or "").replace(",", "").strip()
        if not amt_str:
            continue
        try:
            amt = int(amt_str)
        except ValueError:
            try:
                amt = int(float(amt_str))
            except ValueError:
                continue
        for key, kws in _DART_IS_KEYWORDS.items():
            if out[key] is not None:
                continue
            # 특별 처리: '매출액' 은 '매출원가', '총매출' 등을 피해야 함
            if key == "revenue":
                if "매출액" in nm and "원가" not in nm and "총매" not in nm and "차감" not in nm:
                    out[key] = amt
                    break
            elif key == "op_income":
                if "영업이익" in nm and "영업외" not in nm and "조정" not in nm:
                    out[key] = amt
                    break
            elif key == "net_income":
                # '지배기업' / '비지배' 수식이 붙은 것은 제외, 순수 '당기순이익' 우선
                if any(k in nm for k in kws) and "지배" not in nm and "비지배" not in nm:
                    out[key] = amt
                    break
            else:
                if any(k in nm for k in kws):
                    out[key] = amt
                    break
    # 매출총이익 역산
    if out["gross_profit"] is None and out["revenue"] and out["cogs"]:
        out["gross_profit"] = out["revenue"] - out["cogs"]
    return out


def _fetch_dart_quarter(dart_key: str, corp_code: str, year: int,
                        reprt_code: str) -> dict | None:
    """한 분기 조회. CFS 우선, 없으면 OFS fallback."""
    try:
        import requests as _rq
        for fs_div in ("CFS", "OFS"):
            r = _rq.get(
                "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
                params={
                    "crtfc_key": dart_key, "corp_code": corp_code,
                    "bsns_year": str(year), "reprt_code": reprt_code,
                    "fs_div": fs_div,
                },
                timeout=10,
            )
            d = r.json()
            if d.get("status") == "000":
                is_items = [i for i in (d.get("list") or [])
                            if i.get("sj_div") in ("IS", "CIS")]
                if is_items:
                    agg = _extract_is_aggregates(is_items)
                    agg["fs_div"] = fs_div
                    return agg
    except Exception as exc:
        log.debug("dart quarter fetch fail %s %d %s: %s",
                  corp_code, year, reprt_code, exc)
    return None


def _try_dart_segment_revenue(dart_key: str, corp_code: str, year: int) -> list | None:
    """
    사업보고서 원문 HTML 에서 '매출실적' / '부문별' 테이블 추출 시도.
    Best-effort: 회사별 포맷이 제각각이라 실패 잦음. 실패 시 None.
    """
    try:
        import requests as _rq
        from bs4 import BeautifulSoup
        r = _rq.get(
            "https://opendart.fss.or.kr/api/list.json",
            params={
                "crtfc_key": dart_key, "corp_code": corp_code,
                "bgn_de": f"{year}0101", "end_de": f"{year}1231",
                "pblntf_ty": "A", "page_count": 10,
            }, timeout=10,
        )
        d = r.json()
        if d.get("status") != "000":
            return None
        rcept_no = None
        for item in d.get("list") or []:
            if "사업보고서" in (item.get("report_nm") or ""):
                rcept_no = item.get("rcept_no")
                break
        if not rcept_no:
            return None

        doc_r = _rq.get(
            "https://opendart.fss.or.kr/api/document.xml",
            params={"crtfc_key": dart_key, "rcept_no": rcept_no},
            timeout=20,
        )
        if doc_r.status_code != 200 or len(doc_r.content) < 1000:
            return None
        # document.xml 은 ZIP 일 수도 있음
        content = doc_r.content
        if content[:2] == b"PK":
            import io as _io, zipfile as _zf
            z = _zf.ZipFile(_io.BytesIO(content))
            content = z.read(z.namelist()[0])
        text = content.decode("utf-8", errors="replace")
        soup = BeautifulSoup(text, "html.parser")

        segments: list[dict] = []
        KW = ("매출실적", "매출현황", "부문별", "사업부문별", "제품별 매출", "제품별매출")
        for table in soup.find_all("table"):
            # 테이블 직전의 제목 텍스트
            prev = table.find_previous(["p", "div", "h3", "h4", "span", "title"])
            head_txt = prev.get_text(strip=True) if prev else ""
            if not any(k in head_txt for k in KW):
                continue
            for tr in table.find_all("tr"):
                cells = tr.find_all(["td", "th"])
                if len(cells) < 2:
                    continue
                name = cells[0].get_text(" ", strip=True)
                amounts = []
                for cell in cells[1:]:
                    v = cell.get_text(" ", strip=True).replace(",", "").replace(" ", "")
                    try:
                        amounts.append(int(v))
                    except ValueError:
                        amounts.append(v)
                if name and any(isinstance(a, int) for a in amounts):
                    segments.append({"segment": name, "amounts": amounts})
            if segments:
                return segments  # 첫 매칭 테이블만
        return None
    except Exception as exc:
        log.debug("dart segment parse fail %s: %s", corp_code, exc)
        return None


@app.route("/api/dart_financial/<code>")
def api_dart_financial(code: str):
    """
    국내 종목 12분기 손익 + 마진 + (best-effort) 사업부별 매출.
    24h 캐시: cache/dart_fin_{code}.json
    """
    if not re.fullmatch(r"\d{6}", code):
        return jsonify({"error": "잘못된 종목코드"}), 400

    dart_key = os.getenv("DART_API_KEY")
    if not dart_key:
        return jsonify({"error": "DART_API_KEY 미설정"}), 503

    cache_file = BASE_DIR / "cache" / f"dart_fin_{code}.json"
    cached = _read_fresh_json(cache_file, 1440)
    if cached:
        return jsonify(cached)

    corp_map = _load_dart_corp_code_map()
    corp_code = corp_map.get(code)
    if not corp_code:
        return jsonify({"error": "corp_code 매핑 실패 (비상장 또는 신규 상장)"}), 404

    # 가장 최근 완결 사업연도 기준 3년 × 4분기
    current_year = now_kst().year
    # 현재 연도 1Q/반기 데이터도 있으면 포함
    years = [current_year, current_year - 1, current_year - 2, current_year - 3]
    quarters = [
        ("11013", "1Q"),
        ("11012", "2Q"),
        ("11014", "3Q"),
        ("11011", "4Q"),
    ]

    # 연도별로 raw 값 수집 후 Q4 는 (연간 - Q1+Q2+Q3) 로 역산
    raw_by_year: dict[int, dict] = {}
    for year in sorted(years):
        raw_by_year[year] = {}
        for reprt_code, q_label in quarters:
            agg = _fetch_dart_quarter(dart_key, corp_code, year, reprt_code)
            time.sleep(0.15)
            if agg and agg.get("revenue") is not None:
                raw_by_year[year][q_label] = agg

    all_quarters: list[dict] = []
    FIELDS = ("revenue", "cogs", "gross_profit", "sga", "op_income", "net_income")

    def _sub_dicts(base: dict, *subtracts: dict) -> dict:
        """base - subtracts (필드별 null-safe 감산)."""
        out = {}
        for f in FIELDS:
            v = base.get(f)
            if v is None:
                out[f] = None; continue
            total = v
            for s in subtracts:
                sv = (s or {}).get(f)
                if sv is None:
                    total = None; break
                total -= sv
            out[f] = total
        return out

    for year in sorted(years):
        year_data = raw_by_year.get(year) or {}
        q_individuals: dict[str, dict] = {}

        # 1Q/2Q/3Q: thstrm_amount 가 이미 개별값 (Samsung 실측 확인)
        for q in ("1Q", "2Q", "3Q"):
            if q in year_data:
                q_individuals[q] = {f: year_data[q].get(f) for f in FIELDS}
                q_individuals[q]["fs_div"] = year_data[q].get("fs_div")

        # 4Q: 연간 - (Q1+Q2+Q3). Q1~Q3 중 하나라도 없으면 Q4 역산 불가 → 스킵.
        annual = year_data.get("4Q")   # 실제로는 11011 = 연간
        if annual and all(q in q_individuals for q in ("1Q", "2Q", "3Q")):
            q4 = _sub_dicts(annual, q_individuals["1Q"],
                            q_individuals["2Q"], q_individuals["3Q"])
            # 음수 revenue 는 비정상 → 스킵
            if q4.get("revenue") and q4["revenue"] > 0:
                q4["fs_div"] = annual.get("fs_div")
                q_individuals["4Q"] = q4

        for q_label in ("1Q", "2Q", "3Q", "4Q"):
            if q_label not in q_individuals:
                continue
            d = q_individuals[q_label]
            rev = d.get("revenue") or 0
            gp  = d.get("gross_profit")
            op  = d.get("op_income")
            ni  = d.get("net_income")
            sga = d.get("sga")
            cogs = d.get("cogs")
            if gp is None and rev and cogs is not None:
                gp = rev - cogs
            gpm = round(gp / rev * 100, 1) if (gp is not None and rev) else None
            opm = round(op / rev * 100, 1) if (op is not None and rev) else None
            npm = round(ni / rev * 100, 1) if (ni is not None and rev) else None
            bep = round(sga / (gpm / 100)) if (sga and gpm and gpm > 0) else None
            all_quarters.append({
                "year":    year,
                "quarter": q_label,
                "period":  f"{str(year)[-2:]}.{q_label}",
                "fs_div":  d.get("fs_div"),
                "summary": {
                    "revenue":      rev,
                    "cogs":         cogs,
                    "gross_profit": gp,
                    "sga":          sga,
                    "op_income":    op,
                    "net_income":   ni,
                },
                "margins": {
                    "gpm": gpm, "opm": opm, "npm": npm,
                    "cm_ratio_approx": gpm,
                    "bep_revenue":     bep,
                },
            })

    # 이상치 방어: 직전 분기 대비 10배 초과 또는 음수 매출 → 해당 분기 값 전부 null 처리
    # (프론트 _dartFmt(null) → '—'. 12분기 레이아웃은 유지하되 anomaly 플래그 표시.)
    ANOMALY_RATIO = 10.0
    prev_rev: float | None = None
    for q in all_quarters:
        rev = q["summary"].get("revenue")
        is_anomaly = False
        if rev is None or rev <= 0:
            is_anomaly = rev is not None and rev < 0
        elif prev_rev and prev_rev > 0 and rev > prev_rev * ANOMALY_RATIO:
            is_anomaly = True
        if is_anomaly:
            for f in FIELDS:
                q["summary"][f] = None
            q["margins"] = {"gpm": None, "opm": None, "npm": None,
                            "cm_ratio_approx": None, "bep_revenue": None}
            q["anomaly"] = True
            # prev_rev 는 업데이트하지 않음 — 다음 분기는 직전의 '정상' 분기와 비교
        else:
            q["anomaly"] = False
            if rev and rev > 0:
                prev_rev = rev

    # 최근 12분기만
    all_quarters = all_quarters[-12:]

    # best-effort 사업부별 매출
    segment = None
    if all_quarters:
        latest_year = max(q["year"] for q in all_quarters)
        segment = _try_dart_segment_revenue(dart_key, corp_code, latest_year - 1)

    result = {
        "code":       code,
        "corp_code":  corp_code,
        "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "quarter_count": len(all_quarters),
        "quarters":   all_quarters,
        "segment_revenue": segment,
        "note": "DART API 는 집계 항목만 제공. 세부 비용 분류는 불가하며 GPM 을 공헌이익률 근사로 사용.",
    }
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False),
                              encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


def _extract_econ_time(event: dict) -> str:
    """time 필드에서 HH:MM 부분만 추출 (UTC 기준, 00:00 은 '미정')."""
    t = event.get("time") or ""
    if isinstance(t, str) and len(t) >= 16:
        hm = t[11:16]
        return "" if hm == "00:00" else hm
    return ""


# ─────────────────────────────────────────────────────────────────────────
# PHASE 19 — 52주 신고가 (KR/US)
# ─────────────────────────────────────────────────────────────────────────
def _kr_new_highs_from_charts(top_by_volume: int = 200,
                              ratio_threshold: float = 0.95) -> list[dict]:
    """
    거래대금 상위 top_by_volume 종목에 대해 /api/chart?days=252 호출,
    52주(≈252영업일) 고점 대비 현재가 비율이 threshold 이상인 종목 반환.
    """
    uni = _load_naver_universe()
    stocks_map = (uni or {}).get("stocks") or {}
    if not stocks_map:
        return []

    # 거래대금 기준 정렬
    eligible = sorted(
        [s for s in stocks_map.values() if (s.get("volume_mn") or 0) >= 50],
        key=lambda x: x.get("volume_mn") or 0,
        reverse=True,
    )[:top_by_volume]

    out: list[dict] = []
    today_str = now_kst().strftime("%Y-%m-%d")

    for st in eligible:
        code = st.get("code")
        if not code:
            continue
        try:
            chart = _call_api_internal(f"/api/chart/{code}?days=252")
            if not chart or chart.get("error"):
                continue
            highs  = chart.get("high")  or []
            lows   = chart.get("low")   or []
            closes = chart.get("close") or []
            dates  = chart.get("dates") or []
            if not highs or not closes:
                continue
            hi_52w = max(highs)
            lo_52w = min(lows) if lows else None
            current = closes[-1]
            if not hi_52w or not current:
                continue
            ratio = current / hi_52w
            if ratio < ratio_threshold:
                continue
            # 52주 고점 발생일 (오늘과 같으면 TODAY)
            hi_idx = highs.index(hi_52w)
            hi_date = dates[hi_idx] if hi_idx < len(dates) else ""
            is_today = (dates[-1] == today_str and highs[-1] == hi_52w)

            out.append({
                "code":       code,
                "name":       st.get("name"),
                "sector":     (st.get("sectors") or [None])[0],
                "market":     "kr",
                "market_cap": None,                           # naver_universe 는 시총 미포함
                "volume_mn":  st.get("volume_mn"),
                "per":        None,                            # 비용 큰 조회라 생략
                "price":      current,
                "change_pct": st.get("change_pct"),
                "w52_high":   hi_52w,
                "w52_low":    lo_52w,
                "w52_ratio":  round(ratio * 100, 1),
                "hi_date":    hi_date,
                "is_today":   is_today,
            })
        except Exception as exc:
            log.debug("new_high chart %s fail: %s", code, exc)
            continue

    # 회전율 = 거래대금/시총 은 시총 없어 계산 불가. 프론트에서 표시 생략.
    out.sort(key=lambda x: x["w52_ratio"], reverse=True)
    return out


@app.route("/api/new_highs")
def api_new_highs_kr():
    """KR 52주 신고가 근접 종목. 거래대금 상위 200 스캔, 일 1회 캐시."""
    today = _get_trading_date()
    cache_file = BASE_DIR / "cache" / f"new_highs_kr_{today}.json"
    cached = _read_fresh_json(cache_file, 1440)   # 24h
    if cached:
        return jsonify(cached)

    items = _kr_new_highs_from_charts()
    result = {
        "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "market":     "kr",
        "count":      len(items),
        "items":      items,
    }
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


def _us_new_highs_from_yinfo(ratio_threshold: float = 0.95) -> list[dict]:
    """
    S&P500 전 종목에 대해 us_yinfo 캐시 + yfinance info.fiftyTwoWeekHigh 로
    52주 고점 비율 계산 후 필터.
    """
    us_data = _fetch_us_market_data()
    all_stocks = us_data.get("all_stocks") or []
    if not all_stocks:
        return []

    try:
        import yfinance as _yf
    except ImportError:
        return []

    out: list[dict] = []
    for s in all_stocks:
        sym = s.get("symbol")
        if not sym:
            continue
        info = None
        cache = BASE_DIR / "cache" / f"us_yinfo_{sym}.json"
        if cache.exists():
            try:
                age_hr = (now_kst().timestamp() - cache.stat().st_mtime) / 3600
                if age_hr < 24:
                    info = json.loads(cache.read_text(encoding="utf-8"))
            except Exception:
                info = None
        if info is None:
            try:
                raw = _yf.Ticker(sym).info or {}
                info = {k: v for k, v in raw.items()
                        if isinstance(v, (int, float, str, bool, type(None)))}
                cache.parent.mkdir(exist_ok=True)
                cache.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
            except Exception as exc:
                log.debug("us_new_highs yinfo %s fail: %s", sym, exc)
                continue
            time.sleep(0.05)

        hi = info.get("fiftyTwoWeekHigh")
        lo = info.get("fiftyTwoWeekLow")
        current = info.get("currentPrice") or info.get("regularMarketPrice") or s.get("price")
        if not hi or not current:
            continue
        ratio = current / hi
        if ratio < ratio_threshold:
            continue

        market_cap = info.get("marketCap")
        per        = info.get("trailingPE")
        vol_mn     = s.get("volume_mn") or 0    # US 기준: $M traded
        turnover = (vol_mn * 1e6 / market_cap * 100) if market_cap else None

        out.append({
            "symbol":     sym,
            "code":       sym,
            "name":       s.get("name"),
            "sector":     s.get("sector"),
            "market":     "us",
            "market_cap": market_cap,
            "volume_mn":  vol_mn,
            "turnover":   round(turnover, 3) if turnover is not None else None,
            "per":        round(per, 2) if isinstance(per, (int, float)) else None,
            "price":      round(current, 2),
            "change_pct": s.get("change_pct"),
            "w52_high":   round(hi, 2),
            "w52_low":    round(lo, 2) if lo else None,
            "w52_ratio":  round(ratio * 100, 1),
            "hi_date":    "",   # yfinance 는 고점 발생일 미제공
            "is_today":   ratio >= 0.998,
        })

    out.sort(key=lambda x: x["w52_ratio"], reverse=True)
    return out


@app.route("/api/us/new_highs")
def api_new_highs_us():
    """US 52주 신고가 근접 종목. S&P500 전체 스캔, 일 1회 캐시."""
    today = _get_trading_date()
    cache_file = BASE_DIR / "cache" / f"new_highs_us_{today}.json"
    cached = _read_fresh_json(cache_file, 1440)
    if cached:
        return jsonify(cached)

    items = _us_new_highs_from_yinfo()
    result = {
        "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "market":     "us",
        "count":      len(items),
        "items":      items,
    }
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


@app.route("/api/calendar/economic")
def api_calendar_economic():
    """경제지표 발표 일정 (Finnhub). 6시간 캐시."""
    finnhub_key = os.getenv("FINNHUB_API_KEY")
    if not finnhub_key:
        return jsonify({"error": "FINNHUB_API_KEY 미설정", "events": []}), 503

    from_date, to_date = _calendar_date_range()
    cache_file = BASE_DIR / "cache" / f"economic_calendar_{from_date}.json"
    cached = _read_fresh_json(cache_file, 360)  # 6h
    if cached:
        return jsonify(cached)

    try:
        import requests as _rq
        res = _rq.get(
            "https://finnhub.io/api/v1/calendar/economic",
            params={"token": finnhub_key, "from": from_date, "to": to_date},
            timeout=10,
        )
        if res.status_code != 200:
            return jsonify({"error": f"Finnhub HTTP {res.status_code}", "events": []}), 502
        data = res.json()
    except Exception as exc:
        return jsonify({"error": f"Finnhub 요청 실패: {exc}", "events": []}), 502

    raw_events = data.get("economicCalendar") or []

    # 필터: 주요국 + medium/high 임팩트만
    keep_countries = {"US", "KR", "CN", "JP", "EU", "GB", "DE"}
    keep_impacts = {"high", "medium"}
    filtered = []
    for e in raw_events:
        if e.get("country") not in keep_countries:
            continue
        if (e.get("impact") or "").lower() not in keep_impacts:
            continue
        date = _extract_econ_date(e)
        if not date:
            continue
        impact = (e.get("impact") or "low").lower()
        filtered.append({
            "date":         date,
            "time":         _extract_econ_time(e),
            "country":      e.get("country"),
            "country_kr":   _COUNTRY_KR.get(e.get("country"), e.get("country")),
            "event":        e.get("event"),
            "event_kr":     _EVENT_KR.get(e.get("event"), e.get("event")),
            "impact":       impact,
            "impact_emoji": "🔴" if impact == "high" else "🟡" if impact == "medium" else "⚪",
            "actual":       e.get("actual"),
            "estimate":     e.get("estimate"),
            "prev":         e.get("prev"),
            "unit":         e.get("unit") or "",
        })

    impact_order = {"high": 0, "medium": 1, "low": 2}
    filtered.sort(key=lambda x: (x["date"], impact_order.get(x["impact"], 2), x["time"] or "99:99"))

    result = {
        "from":   from_date,
        "to":     to_date,
        "count":  len(filtered),
        "raw_count": len(raw_events),
        "events": filtered,
    }
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


def _fetch_us_earnings(from_date: str, to_date: str) -> list:
    """Finnhub 미국 실적 — S&P500 종목만 필터."""
    finnhub_key = os.getenv("FINNHUB_API_KEY")
    if not finnhub_key:
        return []
    try:
        import requests as _rq
        res = _rq.get(
            "https://finnhub.io/api/v1/calendar/earnings",
            params={"token": finnhub_key, "from": from_date, "to": to_date},
            timeout=10,
        )
        if res.status_code != 200:
            return []
        data = res.json()
    except Exception as exc:
        log.debug("finnhub earnings fail: %s", exc)
        return []

    raw = data.get("earningsCalendar") or []
    sp500_syms, sp500_names = _load_sp500_symbols()
    out = []
    hour_label = {"bmo": "장전", "amc": "장후", "dmh": "장중"}
    for e in raw:
        sym = e.get("symbol")
        if not sym or sym not in sp500_syms:
            continue
        out.append({
            "date":             e.get("date") or "",
            "market":           "us",
            "symbol":           sym,
            "name":             sp500_names.get(sym, sym),
            "time":             hour_label.get(e.get("hour") or "", ""),
            "eps_estimate":     e.get("epsEstimate"),
            "eps_actual":       e.get("epsActual"),
            "revenue_estimate": e.get("revenueEstimate"),
            "revenue_actual":   e.get("revenueActual"),
            "flag":             "🇺🇸",
        })
    return out


def _fetch_kr_earnings(from_date: str, to_date: str) -> list:
    """DART 정기공시 (사업/반기/분기 보고서) 조회."""
    dart_key = os.getenv("DART_API_KEY")
    if not dart_key:
        return []
    try:
        import requests as _rq
        res = _rq.get(
            "https://opendart.fss.or.kr/api/list.json",
            params={
                "crtfc_key":  dart_key,
                "bgn_de":     from_date.replace("-", ""),
                "end_de":     to_date.replace("-", ""),
                "pblntf_ty":  "A",   # 정기공시
                "page_count": 100,
            },
            timeout=10,
        )
        data = res.json()
    except Exception as exc:
        log.debug("DART fail: %s", exc)
        return []

    if data.get("status") != "000":
        return []

    earnings = []
    kw = ("분기보고서", "반기보고서", "사업보고서")
    for item in data.get("list") or []:
        report_nm = item.get("report_nm") or ""
        if not any(k in report_nm for k in kw):
            continue
        stock_code = item.get("stock_code") or ""
        if not stock_code:   # 상장사만
            continue
        rcept_dt = item.get("rcept_dt") or ""
        if len(rcept_dt) != 8:
            continue
        date_str = f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:8]}"
        earnings.append({
            "date":             date_str,
            "market":           "kr",
            "symbol":           stock_code,
            "name":             item.get("corp_name") or "",
            "time":             "",
            "report_type":      report_nm,
            "eps_estimate":     None,
            "eps_actual":       None,
            "revenue_estimate": None,
            "revenue_actual":   None,
            "flag":             "🇰🇷",
            "dart_link":        f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={item.get('rcept_no', '')}",
        })
    return earnings


@app.route("/api/calendar/earnings")
def api_calendar_earnings():
    """실적발표 일정: Finnhub(미국 S&P500) + DART(한국). 6시간 캐시."""
    from_date, to_date = _calendar_date_range()
    cache_file = BASE_DIR / "cache" / f"earnings_calendar_{from_date}.json"
    cached = _read_fresh_json(cache_file, 360)
    if cached:
        return jsonify(cached)

    all_earn = []
    all_earn.extend(_fetch_us_earnings(from_date, to_date))
    all_earn.extend(_fetch_kr_earnings(from_date, to_date))
    all_earn.sort(key=lambda x: (x.get("date") or "", x.get("market"), x.get("symbol")))

    result = {
        "from":     from_date,
        "to":       to_date,
        "count":    len(all_earn),
        "earnings": all_earn,
    }
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


@app.route("/api/discover")
def api_discover():
    """
    Phase 15 종목 발굴. market ∈ {kr, us, all}.
    Stage 2 결과 (1시간 캐시) 우선, 없으면 Stage 1 (15분 캐시) 폴백.
    all: kr+us Stage 2 를 읽어 병합 (있는 쪽만이라도 반환).
    """
    market = (request.args.get("market") or "kr").lower()
    if market not in ("kr", "us", "all"):
        return jsonify({"error": "market 파라미터는 kr/us/all"}), 400

    cache_dir = BASE_DIR / "cache"

    if market == "kr":
        d = (_read_fresh_json(cache_dir / "discover_kr_stage2.json", 360)
             or _read_fresh_json(cache_dir / "discover_kr_stage1.json", 15))
        if d:
            return jsonify(d)
        result = _stage1_prefilter_kr()
        if "error" not in result:
            try:
                cache_dir.mkdir(exist_ok=True)
                (cache_dir / "discover_kr_stage1.json").write_text(
                    json.dumps(result, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass
        return jsonify(result)

    if market == "us":
        d = (_read_fresh_json(cache_dir / "discover_us_stage2.json", 60)
             or _read_fresh_json(cache_dir / "discover_us_stage1.json", 15))
        if d:
            return jsonify(d)
        result = _stage1_prefilter_us()
        if "error" not in result:
            try:
                cache_dir.mkdir(exist_ok=True)
                (cache_dir / "discover_us_stage1.json").write_text(
                    json.dumps(result, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass
        return jsonify(result)

    # market == "all" — 두 시장을 개별로 읽어 병합
    all_fresh = _read_fresh_json(cache_dir / "discover_all_stage2.json", 60)
    if all_fresh:
        return jsonify(all_fresh)

    kr_data = (_read_fresh_json(cache_dir / "discover_kr_stage2.json", 60)
               or _read_fresh_json(cache_dir / "discover_kr_stage1.json", 15))
    us_data = (_read_fresh_json(cache_dir / "discover_us_stage2.json", 60)
               or _read_fresh_json(cache_dir / "discover_us_stage1.json", 15))

    kr_items = (kr_data or {}).get("items", [])
    us_items = (us_data or {}).get("items", [])
    kr_total = (kr_data or {}).get("total_scanned", 0)
    us_total = (us_data or {}).get("total_scanned", 0)
    if not kr_items:
        kr_result = _stage1_prefilter_kr()
        if "error" not in kr_result:
            kr_items = kr_result["items"]
            kr_total = kr_result["total_scanned"]
            try:
                (cache_dir / "discover_kr_stage1.json").write_text(
                    json.dumps(kr_result, ensure_ascii=False), encoding="utf-8")
            except Exception: pass
    if not us_items:
        us_result = _stage1_prefilter_us()
        if "error" not in us_result:
            us_items = us_result["items"]
            us_total = us_result["total_scanned"]
            try:
                (cache_dir / "discover_us_stage1.json").write_text(
                    json.dumps(us_result, ensure_ascii=False), encoding="utf-8")
            except Exception: pass

    merged = sorted(kr_items + us_items,
                    key=lambda x: x.get("total_score", 0), reverse=True)
    kr_stage = (kr_data or {}).get("stage", 1)
    us_stage = (us_data or {}).get("stage", 1)
    return jsonify({
        "updated_at":    now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "market":        "all",
        "stage":         min(kr_stage, us_stage) if (kr_items and us_items) else max(kr_stage, us_stage),
        "total_scanned": kr_total + us_total,
        "items":         merged,
    })


# ─────────────────────────────────────────────────────────────────────────
# PHASE 15 Stage 2 — 상위 200종목 상세 스코어링 (수급/밸류/기술 + 보너스)
# ─────────────────────────────────────────────────────────────────────────
_discover_lock = threading.Lock()
_discover_state: dict = {
    "status":      "idle",    # idle | starting | running | done | error
    "phase":       None,      # stage1 | fetch | scoring
    "market":      None,
    "progress":    0,
    "total":       0,
    "started_at":  None,
    "finished_at": None,
    "error":       None,
    "message":     None,
}


def _discover_get_state() -> dict:
    with _discover_lock:
        return dict(_discover_state)


def _discover_set(**kw):
    with _discover_lock:
        _discover_state.update(kw)


def _call_api_internal(path: str) -> dict | None:
    """Flask test_client 로 in-process route 호출. worker thread 안전."""
    try:
        with app.test_client() as c:
            r = c.get(path)
            if r.status_code == 200:
                return r.get_json()
    except Exception as exc:
        log.debug("internal call %s failed: %s", path, exc)
    return None


def _fmt_eok(won: float) -> str:
    """원 → 억 단위 표기."""
    if won is None:
        return "—"
    eok = won / 1e8
    sign = "+" if eok > 0 else ""
    return f"{sign}{eok:,.0f}억원"


def _calc_flow_score_kr(flow: dict) -> tuple[int, dict, list]:
    """수급 점수 (0~25). foreign_value/inst_value 단위는 원(KRW)."""
    sub = {"today": 0, "cum5d": 0, "inst": 0, "streak": 0}
    expl: list = []
    if not flow or "error" in flow:
        expl.append({"label": "수급 데이터", "detail": "데이터 없음", "pts": 0, "max": 25})
        return 0, sub, expl

    fv = flow.get("foreign_value") or []
    iv = flow.get("inst_value") or []
    if not fv:
        expl.append({"label": "수급 데이터", "detail": "시계열 없음", "pts": 0, "max": 25})
        return 0, sub, expl

    today = fv[-1]
    if   today > 10_000_000_000: sub["today"] = 8
    elif today >  5_000_000_000: sub["today"] = 6
    elif today >  1_000_000_000: sub["today"] = 4
    elif today >  0:             sub["today"] = 2
    expl.append({
        "label":  "외국인 당일 순매수",
        "detail": _fmt_eok(today),
        "pts":    sub["today"], "max": 8,
    })

    cum5 = sum(fv[-5:])
    if   cum5 > 30_000_000_000: sub["cum5d"] = 8
    elif cum5 > 10_000_000_000: sub["cum5d"] = 6
    elif cum5 >  0:             sub["cum5d"] = 4
    elif cum5 > -5_000_000_000: sub["cum5d"] = 1
    expl.append({
        "label":  "외국인 5일 누적",
        "detail": _fmt_eok(cum5),
        "pts":    sub["cum5d"], "max": 8,
    })

    if iv:
        inst_today = iv[-1]
        if   inst_today > 5_000_000_000: sub["inst"] = 5
        elif inst_today > 1_000_000_000: sub["inst"] = 3
        elif inst_today > 0:             sub["inst"] = 1
        expl.append({
            "label":  "기관 당일 순매수",
            "detail": _fmt_eok(inst_today),
            "pts":    sub["inst"], "max": 5,
        })
    else:
        expl.append({"label": "기관 당일 순매수", "detail": "데이터 없음", "pts": 0, "max": 5})

    streak = 0
    for v in reversed(fv):
        if v > 0: streak += 1
        else: break
    if   streak >= 5: sub["streak"] = 4
    elif streak >= 3: sub["streak"] = 3
    elif streak >= 2: sub["streak"] = 2
    elif streak >= 1: sub["streak"] = 1
    expl.append({
        "label":  "외국인 연속 순매수",
        "detail": f"{streak}일 연속" if streak > 0 else "순매도 전환",
        "pts":    sub["streak"], "max": 4,
    })

    return sum(sub.values()), sub, expl


def _calc_valuation_score_kr(fin: dict, current_price: float | None,
                             high_180d: float | None,
                             sector_per_rank_pct: float | None) -> tuple[int, dict, list]:
    """밸류 점수 (0~20). per/pbr 음수/None 은 0점."""
    sub = {"per_vs_ind": 0, "pbr": 0, "high_gap": 0, "sector_rank": 0}
    expl: list = []
    if not fin or "error" in fin:
        expl.append({"label": "재무 데이터", "detail": "데이터 없음", "pts": 0, "max": 20})
        return 0, sub, expl

    per = fin.get("per")
    pbr = fin.get("pbr")
    ind = fin.get("industry_per")

    if per and per > 0 and ind and ind > 0:
        r = per / ind
        if   r < 0.3: sub["per_vs_ind"] = 7
        elif r < 0.5: sub["per_vs_ind"] = 6
        elif r < 0.7: sub["per_vs_ind"] = 5
        elif r < 0.9: sub["per_vs_ind"] = 3
        elif r < 1.0: sub["per_vs_ind"] = 1
        expl.append({
            "label":  "PER 업종 대비",
            "detail": f"PER {per} · 업종평균 {ind} · 비율 {r*100:.0f}%",
            "pts":    sub["per_vs_ind"], "max": 7,
        })
    elif per and per > 0:
        if   per <  5: sub["per_vs_ind"] = 7
        elif per < 10: sub["per_vs_ind"] = 5
        elif per < 15: sub["per_vs_ind"] = 3
        elif per < 20: sub["per_vs_ind"] = 1
        expl.append({
            "label":  "PER 업종 대비",
            "detail": f"PER {per} · 업종평균 없음 (절대 기준 적용)",
            "pts":    sub["per_vs_ind"], "max": 7,
        })
    else:
        expl.append({
            "label":  "PER 업종 대비",
            "detail": "PER 미제공 또는 적자",
            "pts":    0, "max": 7,
        })

    if pbr and pbr > 0:
        if   pbr < 0.5: sub["pbr"] = 5
        elif pbr < 0.8: sub["pbr"] = 4
        elif pbr < 1.0: sub["pbr"] = 3
        elif pbr < 1.5: sub["pbr"] = 2
        elif pbr < 2.0: sub["pbr"] = 1
        expl.append({
            "label":  "PBR",
            "detail": f"PBR {pbr}",
            "pts":    sub["pbr"], "max": 5,
        })
    else:
        expl.append({"label": "PBR", "detail": "PBR 미제공", "pts": 0, "max": 5})

    if high_180d and current_price and high_180d > 0:
        gap = (high_180d - current_price) / high_180d * 100
        if   gap > 40: sub["high_gap"] = 4
        elif gap > 30: sub["high_gap"] = 3
        elif gap > 20: sub["high_gap"] = 2
        elif gap > 10: sub["high_gap"] = 1
        expl.append({
            "label":  "180일 고점 대비",
            "detail": f"-{gap:.1f}% · 고점 {int(high_180d):,}원",
            "pts":    sub["high_gap"], "max": 4,
        })
    else:
        expl.append({"label": "180일 고점 대비", "detail": "차트 데이터 없음", "pts": 0, "max": 4})

    if sector_per_rank_pct is not None:
        pct = sector_per_rank_pct * 100
        if   sector_per_rank_pct < 0.10: sub["sector_rank"] = 4
        elif sector_per_rank_pct < 0.25: sub["sector_rank"] = 3
        elif sector_per_rank_pct < 0.40: sub["sector_rank"] = 2
        elif sector_per_rank_pct < 0.50: sub["sector_rank"] = 1
        expl.append({
            "label":  "섹터 내 PER 랭크",
            "detail": f"섹터 내 하위 {pct:.0f}% (저평가일수록 높은 점수)",
            "pts":    sub["sector_rank"], "max": 4,
        })
    else:
        expl.append({"label": "섹터 내 PER 랭크", "detail": "비교 가능 데이터 없음", "pts": 0, "max": 4})

    return sum(sub.values()), sub, expl


# _generate_analysis 의 실제 signal 문자열 기준 (server.py L279-376)
_TECH_BB    = {"과매도": 4, "중립 상향": 3, "스퀴즈": 2, "중립 하향": 1,
               "밴드 확장": 0, "과매수": 0}
_TECH_TREND = {"저항선 돌파": 4, "지지선 위": 3, "저항선 하": 1, "지지선 이탈": 0}
_TECH_FIB   = {"깊은 조정": 4, "중간 조정": 3, "일반 조정": 3,
               "약조정 구간": 2, "신고가 근접": 1, "추세 전환": 0}
_TECH_VOL   = {"거래량 급증": 3, "거래량 급감": 0}


def _calc_technical_score_kr(analysis: dict | None) -> tuple[int, dict, list]:
    """기술 점수 (0~15). comments 배열에서 type별 최고 점수 합산."""
    sub = {"bb": 0, "trend": 0, "fib": 0, "volume": 0}
    expl: list = []
    if not analysis:
        expl.append({"label": "차트 분석", "detail": "차트 데이터 없음", "pts": 0, "max": 15})
        return 0, sub, expl

    sigs = {"bollinger": None, "trendline": None, "fibonacci": None, "volume": None}
    for c in analysis.get("comments") or []:
        t = c.get("type")
        sig = c.get("signal", "") or ""
        if   t == "bollinger":
            pts = _TECH_BB.get(sig, 0)
            if pts >= sub["bb"]: sub["bb"] = pts; sigs["bollinger"] = sig
        elif t == "trendline":
            pts = _TECH_TREND.get(sig, 0)
            if pts >= sub["trend"]: sub["trend"] = pts; sigs["trendline"] = sig
        elif t == "fibonacci":
            pts = _TECH_FIB.get(sig, 0)
            if pts >= sub["fib"]: sub["fib"] = pts; sigs["fibonacci"] = sig
        elif t == "volume":
            pts = _TECH_VOL.get(sig, 0)
            if pts >= sub["volume"]: sub["volume"] = pts; sigs["volume"] = sig

    expl.append({"label": "볼린저밴드", "detail": sigs["bollinger"] or "신호 없음",
                 "pts": sub["bb"],     "max": 4})
    expl.append({"label": "추세선",     "detail": sigs["trendline"] or "신호 없음",
                 "pts": sub["trend"],  "max": 4})
    expl.append({"label": "피보나치",   "detail": sigs["fibonacci"] or "신호 없음",
                 "pts": sub["fib"],    "max": 4})
    expl.append({"label": "거래량",     "detail": sigs["volume"] or "신호 없음",
                 "pts": sub["volume"], "max": 3})
    return sum(sub.values()), sub, expl


def _calc_undervalued_bonus(stock_ret_20d: float | None,
                            sector_avg_ret_20d: float | None) -> tuple[int, list]:
    """섹터가 올랐는데 본인은 덜 오른 경우 가산 (0~10). 반환: (점수, 설명 리스트)."""
    if sector_avg_ret_20d is None or stock_ret_20d is None:
        return 0, [{"label": "덜오른 보너스",
                    "detail": "20일 수익률 데이터 없음",
                    "pts": 0, "max": 10}]
    if sector_avg_ret_20d > 5 and stock_ret_20d < sector_avg_ret_20d * 0.5:
        gap = sector_avg_ret_20d - stock_ret_20d
        pts = (10 if gap > 15 else 7 if gap > 10 else
               5  if gap >  5 else 3 if gap >  3 else 0)
        return pts, [{
            "label":  "덜오른 보너스",
            "detail": (f"섹터 20일 평균 {sector_avg_ret_20d:+.1f}%인데 "
                       f"이 종목은 {stock_ret_20d:+.1f}%. 차이 {gap:.1f}%p"),
            "pts":    pts, "max": 10,
        }]
    return 0, [{
        "label":  "덜오른 보너스",
        "detail": ("섹터 평균과 비슷하거나 더 많이 올라 가산점 없음"
                   if sector_avg_ret_20d is not None else "섹터 평균 계산 불가"),
        "pts":    0, "max": 10,
    }]


def _find_signal(comments: list, ctype: str) -> str | None:
    for c in comments or []:
        if c.get("type") == ctype:
            return c.get("signal")
    return None


def _stage2_scoring_worker(market: str):
    """백그라운드 진입점. market ∈ {kr, us, all}. all: KR → US 순차 실행 후 병합."""
    try:
        _discover_set(
            status="running", phase="stage1", market=market,
            progress=0, total=0, error=None,
            started_at=now_kst().strftime("%Y-%m-%d %H:%M:%S"),
            finished_at=None, message="시작 중…",
        )

        kr_items = None
        us_items = None

        if market in ("kr", "all"):
            kr_items = _run_stage2_kr()
            if kr_items is None:
                return  # 에러 상태 내부에서 설정됨
        if market in ("us", "all"):
            us_items = _run_stage2_us()
            if us_items is None:
                return

        if market == "all":
            merged = sorted(
                (kr_items or []) + (us_items or []),
                key=lambda x: x["total_score"], reverse=True,
            )
            out = {
                "updated_at":    now_kst().strftime("%Y-%m-%d %H:%M:%S"),
                "market":        "all",
                "stage":         2,
                "total_scanned": len(kr_items or []) + len(us_items or []),
                "items":         merged,
            }
            (BASE_DIR / "cache" / "discover_all_stage2.json").write_text(
                json.dumps(out, ensure_ascii=False), encoding="utf-8"
            )

        kr_n = len(kr_items or [])
        us_n = len(us_items or [])
        total_msg = (f"완료 · 국내 {kr_n} + 미국 {us_n}종목"
                     if market == "all" else f"완료 · {kr_n or us_n}종목")
        _discover_set(
            status="done", phase=None,
            finished_at=now_kst().strftime("%Y-%m-%d %H:%M:%S"),
            message=total_msg,
        )
        log.info("✓  Stage 2 스코어링 완료: market=%s kr=%d us=%d",
                 market, kr_n, us_n)
    except Exception as exc:
        log.exception("stage2 worker failed")
        _discover_set(
            status="error", phase=None, error=str(exc),
            finished_at=now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        )


def _run_stage2_kr() -> list | None:
    """KR Stage 2 실행. 성공 시 items 리스트, 실패 시 None (state error 설정)."""
    _discover_set(phase="kr_stage1", message="🇰🇷 Stage 1 프리필터 실행 중…")
    stage1 = _stage1_prefilter_kr()
    if "error" in stage1:
        _discover_set(status="error", error=stage1["error"],
                      finished_at=now_kst().strftime("%Y-%m-%d %H:%M:%S"))
        return None

    candidates = stage1["items"]
    total = len(candidates)
    if not total:
        _discover_set(status="error", error="KR Stage 1 결과 없음",
                      finished_at=now_kst().strftime("%Y-%m-%d %H:%M:%S"))
        return None

    sparklines = _load_ticker_sparklines_kr()

    # 섹터별 20일 평균 수익률 (덜 오른 종목 보너스용)
    sector_rets: dict[str, list[float]] = {}
    for it in candidates:
        sp = sparklines.get(it["code"]) or []
        if len(sp) >= 20 and sp[0]:
            sector_rets.setdefault(it["sector"] or "_", []).append(
                (sp[-1] / sp[0] - 1) * 100
            )
    sector_avg_ret = {k: sum(v) / len(v) for k, v in sector_rets.items() if v}

    # ── Phase A: 상세 데이터 수집 (200 × 3 fetch) ──
    _discover_set(phase="kr_fetch", progress=0, total=total,
                  message=f"🇰🇷 상세 데이터 수집 중 (0/{total})")
    financials: dict[str, dict] = {}
    flows:      dict[str, dict] = {}
    charts:     dict[str, dict] = {}

    for i, it in enumerate(candidates):
        code = it["code"]
        try:
            financials[code] = _call_api_internal(f"/api/financial/{code}") or {}
            flows[code]      = _call_api_internal(f"/api/flow/{code}") or {}
            charts[code]     = _call_api_internal(f"/api/chart/{code}") or {}
        except Exception as exc:
            log.debug("stage2 kr fetch fail %s: %s", code, exc)
        _discover_set(progress=i + 1,
                      message=f"🇰🇷 상세 데이터 수집 중 ({i+1}/{total})")
        time.sleep(0.04)

    # 섹터별 PER 랭크 (오름차순 = 저평가 상위)
    sector_pers: dict[str, list[tuple[str, float]]] = {}
    sector_by_code = {it["code"]: (it["sector"] or "_") for it in candidates}
    for code, fin in financials.items():
        per = fin.get("per")
        if per and per > 0:
            sector_pers.setdefault(sector_by_code[code], []).append((code, per))
    sector_per_rank_pct: dict[str, float] = {}
    for sect, lst in sector_pers.items():
        lst.sort(key=lambda x: x[1])
        n = len(lst)
        for idx, (code, _) in enumerate(lst):
            sector_per_rank_pct[code] = (idx / n) if n > 0 else 0.5

    # ── Phase B: 스코어링 ──
    _discover_set(phase="kr_scoring", progress=0, message="🇰🇷 스코어링 중…")
    for idx, it in enumerate(candidates):
        code = it["code"]
        fin = financials.get(code) or {}
        flow = flows.get(code) or {}
        chart = charts.get(code) or {}
        analysis = chart.get("analysis") if isinstance(chart, dict) else None
        highs = (chart.get("high") if isinstance(chart, dict) else None) or []
        high_180d = max(highs) if highs else None

        flow_score, flow_sub, flow_expl = _calc_flow_score_kr(flow)
        val_score,  val_sub,  val_expl  = _calc_valuation_score_kr(
            fin, it.get("price"), high_180d, sector_per_rank_pct.get(code)
        )
        tech_score, tech_sub, tech_expl = _calc_technical_score_kr(analysis)

        sp = sparklines.get(code) or []
        stock_ret_20d = ((sp[-1] / sp[0] - 1) * 100) if (len(sp) >= 20 and sp[0]) else None
        bonus, bonus_expl = _calc_undervalued_bonus(
            stock_ret_20d, sector_avg_ret.get(it.get("sector") or "_")
        )

        it["scores"]["flow"] = flow_score
        it["scores"]["valuation"] = val_score
        it["scores"]["technical"] = tech_score
        it["scores"]["undervalued_bonus"] = bonus
        it["sub_scores"]["flow"] = flow_sub
        it["sub_scores"]["valuation"] = val_sub
        it["sub_scores"]["technical"] = tech_sub
        it.setdefault("explanations", {})
        it["explanations"]["flow"] = flow_expl
        it["explanations"]["valuation"] = val_expl
        it["explanations"]["technical"] = tech_expl
        it["explanations"]["bonus"] = bonus_expl

        comments = (analysis or {}).get("comments", []) if analysis else []
        it["details"] = {
            "per":          fin.get("per"),
            "pbr":          fin.get("pbr"),
            "industry_per": fin.get("industry_per"),
            "bb_signal":    _find_signal(comments, "bollinger"),
            "trend_signal": _find_signal(comments, "trendline"),
            "fib_signal":   _find_signal(comments, "fibonacci"),
            "vol_signal":   _find_signal(comments, "volume"),
            "foreign_5d":   sum((flow.get("foreign_value") or [])[-5:]) if flow else None,
        }
        it["total_score"] = (
            it["scores"]["momentum"] + it["scores"]["sector"] +
            flow_score + val_score + tech_score + bonus
        )
        _discover_set(progress=idx + 1)

    candidates.sort(key=lambda x: x["total_score"], reverse=True)

    result = {
        "updated_at":    now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "market":        "kr",
        "stage":         2,
        "total_scanned": stage1["total_scanned"],
        "items":         candidates,
    }

    out = BASE_DIR / "cache" / "discover_kr_stage2.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return candidates


def _run_stage2_us() -> list | None:
    """US Stage 2: S&P500 에서 상위 100종목 yfinance 상세 스코어링."""
    _discover_set(phase="us_stage1", message="🇺🇸 Stage 1 프리필터 실행 중…")
    stage1 = _stage1_prefilter_us()
    if "error" in stage1:
        _discover_set(status="error", error=f"US: {stage1['error']}",
                      finished_at=now_kst().strftime("%Y-%m-%d %H:%M:%S"))
        return None

    candidates = stage1["items"]
    total = len(candidates)
    if not total:
        _discover_set(status="error", error="US Stage 1 결과 없음",
                      finished_at=now_kst().strftime("%Y-%m-%d %H:%M:%S"))
        return None

    # 섹터 데이터 (us_market) — stocks 배열 포함
    us_data = _fetch_us_market_data()
    sector_by_name = {x["name"]: x for x in (us_data.get("sectors") or [])}

    try:
        import yfinance as _yf
    except ImportError:
        _discover_set(status="error", error="yfinance 미설치",
                      finished_at=now_kst().strftime("%Y-%m-%d %H:%M:%S"))
        return None

    _discover_set(phase="us_fetch", progress=0, total=total,
                  message=f"🇺🇸 yfinance 상세 수집 중 (0/{total})")

    infos:  dict[str, dict] = {}
    charts: dict[str, dict] = {}
    ret_20d: dict[str, float] = {}

    for i, it in enumerate(candidates):
        sym = it["code"]
        # 24h 캐시: cache/us_yinfo_{sym}.json
        info_cache = BASE_DIR / "cache" / f"us_yinfo_{sym}.json"
        info = None
        if info_cache.exists():
            try:
                age_hr = (now_kst().timestamp() - info_cache.stat().st_mtime) / 3600
                if age_hr < 24:
                    info = json.loads(info_cache.read_text(encoding="utf-8"))
            except Exception:
                info = None
        if info is None:
            try:
                raw = _yf.Ticker(sym).info or {}
                # JSON-직렬화 가능한 값만 선별
                info = {k: v for k, v in raw.items()
                        if isinstance(v, (int, float, str, bool, type(None)))}
                info_cache.parent.mkdir(exist_ok=True)
                info_cache.write_text(json.dumps(info, ensure_ascii=False),
                                      encoding="utf-8")
            except Exception as exc:
                log.debug("us yfinance info fail %s: %s", sym, exc)
                info = {}
        infos[sym] = info

        # 차트: /api/us/chart/<symbol> 재사용 (bollinger/fib/trend/analysis 포함)
        chart = _call_api_internal(f"/api/us/chart/{sym}") or {}
        charts[sym] = chart

        closes = (chart.get("close") if isinstance(chart, dict) else None) or []
        if len(closes) >= 20 and closes[-20]:
            ret_20d[sym] = (closes[-1] / closes[-20] - 1) * 100

        _discover_set(progress=i + 1,
                      message=f"🇺🇸 yfinance 상세 수집 중 ({i+1}/{total})")
        time.sleep(0.1)

    # 섹터별 20일 평균 수익률
    sector_rets: dict[str, list[float]] = {}
    for it in candidates:
        r = ret_20d.get(it["code"])
        if r is not None:
            sector_rets.setdefault(it.get("sector") or "_", []).append(r)
    sector_avg_ret = {k: sum(v) / len(v) for k, v in sector_rets.items() if v}

    # ── 스코어링 ──
    _discover_set(phase="us_scoring", progress=0, message="🇺🇸 스코어링 중…")
    for idx, it in enumerate(candidates):
        sym = it["code"]
        info = infos.get(sym) or {}
        chart = charts.get(sym) or {}
        analysis = chart.get("analysis") if isinstance(chart, dict) else None
        high_52w = info.get("fiftyTwoWeekHigh")
        current_price = info.get("currentPrice") or info.get("regularMarketPrice") or it.get("price")

        flow_score, flow_sub, flow_expl = _calc_flow_score_us(info)
        val_score,  val_sub,  val_expl  = _calc_valuation_score_us(
            info, high_52w, current_price
        )
        tech_score, tech_sub, tech_expl = _calc_technical_score_kr(analysis)

        bonus, bonus_expl = _calc_undervalued_bonus(
            ret_20d.get(sym), sector_avg_ret.get(it.get("sector") or "_")
        )

        it["scores"]["flow"] = flow_score
        it["scores"]["valuation"] = val_score
        it["scores"]["technical"] = tech_score
        it["scores"]["undervalued_bonus"] = bonus
        it["sub_scores"]["flow"] = flow_sub
        it["sub_scores"]["valuation"] = val_sub
        it["sub_scores"]["technical"] = tech_sub
        it.setdefault("explanations", {})
        it["explanations"]["flow"] = flow_expl
        it["explanations"]["valuation"] = val_expl
        it["explanations"]["technical"] = tech_expl
        it["explanations"]["bonus"] = bonus_expl

        comments = (analysis or {}).get("comments", []) if analysis else []
        it["details"] = {
            "per":          info.get("trailingPE"),
            "pbr":          info.get("priceToBook"),
            "forward_per":  info.get("forwardPE"),
            "recommendation": info.get("recommendationKey"),
            "inst_pct":     info.get("heldPercentInstitutions"),
            "insider_pct":  info.get("heldPercentInsiders"),
            "bb_signal":    _find_signal(comments, "bollinger"),
            "trend_signal": _find_signal(comments, "trendline"),
            "fib_signal":   _find_signal(comments, "fibonacci"),
            "vol_signal":   _find_signal(comments, "volume"),
        }
        it["total_score"] = (
            it["scores"]["momentum"] + it["scores"]["sector"] +
            flow_score + val_score + tech_score + bonus
        )
        _discover_set(progress=idx + 1)

    candidates.sort(key=lambda x: x["total_score"], reverse=True)

    result = {
        "updated_at":    now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "market":        "us",
        "stage":         2,
        "total_scanned": stage1["total_scanned"],
        "items":         candidates,
    }

    out = BASE_DIR / "cache" / "discover_us_stage2.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return candidates


@app.route("/api/discover/scan", methods=["POST"])
def api_discover_scan():
    """Stage 2 백그라운드 스캔 시작. market ∈ {kr, us, all}."""
    market = (request.args.get("market") or "kr").lower()
    if market not in ("kr", "us", "all"):
        return jsonify({"error": "market 파라미터는 kr/us/all"}), 400

    with _discover_lock:
        if _discover_state["status"] in ("starting", "running"):
            return jsonify({
                "status": "already_running",
                "state":  dict(_discover_state),
            })
        _discover_state.update({
            "status":     "starting",
            "phase":      None,
            "market":     market,
            "progress":   0,
            "total":      0,
            "error":      None,
            "message":    "시작 중…",
            "started_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": None,
        })

    threading.Thread(target=_stage2_scoring_worker, args=(market,),
                     daemon=True, name="discover-stage2").start()
    return jsonify({"status": "started", "state": _discover_get_state()})


@app.route("/api/discover/progress")
def api_discover_progress():
    """현재 Stage 2 스캔 진행 상태."""
    return jsonify(_discover_get_state())


# gunicorn 이 모듈을 import 하는 시점에 자동 실행
_startup()


if __name__ == "__main__":
    print(f"""
  ┌─────────────────────────────────────────┐
  │   테마 트리맵 서버                      │
  │   http://{HOST}:{PORT}                   │
  └─────────────────────────────────────────┘
""")
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False)
