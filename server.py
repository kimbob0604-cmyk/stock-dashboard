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
import sys
import threading
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
    import glob as _glob
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    masters = sorted(
        _glob.glob(str(BASE_DIR / "cache" / "stock_master_*.json")), reverse=True
    )
    if not masters:
        return jsonify([])
    with open(masters[0], encoding="utf-8") as f:
        master: dict = json.load(f)
    results = []
    for code, name in master.items():
        if q in code or q in name:
            results.append({"code": code, "name": name})
        if len(results) >= 10:
            break
    return jsonify(results)


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


def _tabula_extract_financial_tables(pdf_bytes: bytes) -> list[dict]:
    """
    tabula-py 로 PDF 앞 3페이지에서 '매출/영업이익/순이익/EPS' 키워드가 포함된
    행을 뽑아 연도별(또는 분기별) 구조화된 테이블로 반환.

    Returns:
      [{
        "headers":  ["1Q25","2Q25","3Q25",...,"2025P","2026E","2027E"],
        "rows":    [
          {"label": "매출액", "values": ["79,141","74,566",...]},
          {"label": "영업이익", "values": [...]},
          ...
        ]
      }]
      (여러 서로 다른 테이블이 있을 수 있어 list 로 반환)

    Java 미설치 / tabula 미설치 / 파싱 실패 시 빈 리스트 반환 (graceful).
    """
    try:
        import tabula
    except ImportError:
        return []

    # 로컬 dev 에서 brew openjdk 가 PATH 에 없을 수 있어 선제적으로 추가
    import os as _os
    brew_openjdk = "/opt/homebrew/opt/openjdk/bin"
    if _os.path.exists(brew_openjdk) and brew_openjdk not in _os.environ.get("PATH", ""):
        _os.environ["PATH"] = brew_openjdk + ":" + _os.environ.get("PATH", "")

    import tempfile
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
            tf.write(pdf_bytes)
            tmp_path = tf.name
        dfs = tabula.read_pdf(
            tmp_path,
            pages="1-3",
            stream=True,
            multiple_tables=True,
            pandas_options={"dtype": str},
        )
    except Exception as exc:
        print(f"[tabula] read_pdf 실패: {exc!r}")
        return []
    finally:
        if tmp_path:
            try:
                import os as _os2; _os2.unlink(tmp_path)
            except Exception:
                pass

    if not dfs:
        return []

    import re as _re
    # "2025","1Q25","2026E","FY25","2024.12","24.12(E)" 등 연/분기 헤더로 보이는 셀 탐지
    HDR_PAT = _re.compile(r"^\s*(?:\d{4}|\d{1,2}[A-Z0-9\.]{1,6})[A-Z()P]*\s*$", _re.IGNORECASE)
    KEYWORDS = ["매출", "영업이익", "순이익", "EPS", "DRAM"]   # DRAM 행은 섹터 예시용

    out: list[dict] = []
    for df in dfs:
        if df is None or df.empty or df.shape[1] < 2:
            continue
        # 헤더 후보: DataFrame 컬럼명 자체가 연/분기 형태인 경우
        col_headers = [str(c) for c in df.columns]
        header_cols = [
            c for c in col_headers
            if c and not c.startswith("Unnamed") and HDR_PAT.match(c.strip())
        ]
        # 또는 첫 행이 헤더인 경우 (tabula 가 헤더를 데이터로 잡아놓음)
        header_from_row0: list[str] | None = None
        if len(header_cols) < 2 and len(df) > 0:
            row0 = [str(v).strip() if v is not None else "" for v in df.iloc[0].tolist()]
            hdr_hits = [v for v in row0 if HDR_PAT.match(v)]
            if len(hdr_hits) >= 2:
                header_from_row0 = row0

        # 라벨/값 후보 행 추출
        rows: list[dict] = []
        data_iter = df.iloc[1:].iterrows() if header_from_row0 else df.iterrows()
        for _, row in data_iter:
            cells = [("" if v is None else str(v).strip()) for v in row.tolist()]
            # 라벨은 보통 맨 앞 non-empty 셀
            label = None
            value_start = 0
            for j, c in enumerate(cells):
                if c:
                    label = c
                    value_start = j + 1
                    break
            if not label:
                continue
            if not any(k in label for k in KEYWORDS):
                continue
            values = [c for c in cells[value_start:] if c]
            # 최소 2개 이상의 숫자형 값이 있어야 의미 있는 재무 행
            numeric_ct = sum(1 for v in values if _re.fullmatch(r"[-+]?[\d,\.]+", v))
            if numeric_ct < 2:
                continue
            rows.append({"label": label, "values": values})

        if not rows:
            continue

        headers = header_from_row0 or header_cols
        # 헤더가 없으면 값 길이에 맞춰 가짜 컬럼 이름 생성
        if not headers:
            max_len = max(len(r["values"]) for r in rows)
            headers = [f"col{i+1}" for i in range(max_len)]

        # 헤더/값 길이 정규화
        max_cols = max(len(headers), max(len(r["values"]) for r in rows))
        headers = (headers + [""] * max_cols)[:max_cols]
        for r in rows:
            r["values"] = (r["values"] + [""] * max_cols)[:max_cols]

        out.append({"headers": headers, "rows": rows})

    return out


def _extract_report_pdf(report_info: dict) -> dict:
    """리포트 PDF 텍스트 추출 + 정규식 규칙 기반 핵심 수치 추출 + tabula 재무 테이블."""
    import re as _re
    import requests as _rq
    from io import BytesIO

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
    }
    pdf_url = result["pdf_url"]
    if not pdf_url:
        return result

    try:
        pdf_res = _rq.get(pdf_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if pdf_res.status_code != 200:
            return result
        pdf_bytes = pdf_res.content
        from PyPDF2 import PdfReader
        reader = PdfReader(BytesIO(pdf_bytes))
        text = ""
        for i, page in enumerate(reader.pages):
            if i >= 3:
                break
            t = page.extract_text() or ""
            text += t + "\n"
    except Exception as exc:
        print(f"[리포트 PDF 추출 실패] {report_info.get('title','')}: {exc}")
        return result

    # tabula 테이블 추출 (Java 없으면 빈 리스트, 나머지 처리는 계속)
    try:
        result["financial_tables"] = _tabula_extract_financial_tables(pdf_bytes)
    except Exception as exc:
        print(f"[tabula 추출 실패] {report_info.get('title','')}: {exc}")

    if not text:
        return result

    # ── 목표주가 ──
    for pat in (
        r"목표주가[:\s]*([0-9,]+)\s*원",
        r"목표가[:\s]*([0-9,]+)\s*원",
        r"Target\s*Price[:\s]*([0-9,]+)",
        r"TP[:\s]*([0-9,]+)\s*원",
        r"목표주가\s*\(원\)[:\s]*([0-9,]+)",
        r"([0-9,]+)\s*원\s*\(목표주가\)",
    ):
        m = _re.search(pat, text)
        if m:
            try:
                result["target_price"] = int(m.group(1).replace(",", ""))
                break
            except ValueError:
                continue

    # ── 투자의견 ──
    opinion_map = {
        "buy": "매수", "strong buy": "매수", "outperform": "매수", "비중확대": "매수",
        "trading buy": "Trading Buy",
        "neutral": "중립", "hold": "중립", "시장수익률": "중립",
        "sell": "매도",  "underperform": "매도",  "비중축소": "매도",
        "매수": "매수", "중립": "중립", "매도": "매도",
    }
    for pat in (
        r"투자의견[:\s]*(매수|Buy|Strong Buy|Outperform|비중확대|중립|Neutral|Hold|시장수익률|매도|Sell|Underperform|비중축소|Trading Buy|Not Rated)",
        r"Rating[:\s]*(Buy|Strong Buy|Outperform|Neutral|Hold|Sell|Underperform)",
        r"(매수|중립|매도|비중확대|비중축소|Trading Buy)\s*\(유지\)",
        r"(매수|중립|매도|비중확대|비중축소|Trading Buy)\s*\(상향\)",
        r"(매수|중립|매도|비중확대|비중축소|Trading Buy)\s*\(하향\)",
        r"(매수|중립|매도|비중확대|비중축소|Trading Buy)\s*\(신규\)",
    ):
        m = _re.search(pat, text, _re.IGNORECASE)
        if m:
            raw = m.group(1).strip()
            result["opinion"] = opinion_map.get(raw.lower(), raw)
            break

    # ── 상승여력 ──
    for pat in (
        r"상승여력[:\s]*([0-9.]+)\s*%",
        r"Upside[:\s]*([0-9.]+)\s*%",
        r"괴리율[:\s]*([0-9.]+)\s*%",
    ):
        m = _re.search(pat, text)
        if m:
            try:
                result["upside"] = float(m.group(1))
                break
            except ValueError:
                continue

    # 목표주가 - 현재가 로 상승여력 역산
    if result["upside"] is None and result["target_price"]:
        for pat in (
            r"현재주가[:\s]*([0-9,]+)\s*원",
            r"현재가[:\s]*([0-9,]+)\s*원",
            r"주가[:\s]*([0-9,]+)\s*원",
        ):
            m = _re.search(pat, text)
            if m:
                try:
                    cur = int(m.group(1).replace(",", ""))
                    result["current_price"] = cur
                    if cur > 0:
                        result["upside"] = round((result["target_price"] / cur - 1) * 100, 1)
                    break
                except ValueError:
                    continue

    # ── 매출·영업이익·EPS 추정 ──
    m = _re.search(r"매출(?:액)?[:\s]*([0-9,.]+)\s*(조|억|백만)?", text)
    if m:
        result["revenue_estimate"] = m.group(1) + (m.group(2) or "")
    m = _re.search(r"영업이익[:\s]*([0-9,.]+)\s*(조|억|백만)?", text)
    if m:
        result["op_estimate"] = m.group(1) + (m.group(2) or "")
    m = _re.search(r"(?:EPS|주당순이익)[:\s]*([0-9,]+)\s*원?", text)
    if m:
        result["eps_estimate"] = m.group(1) + "원"

    # ── 핵심 포인트 (글머리 기호) ──
    bullets: list[str] = []
    for pat in (
        r"[•·▶►■□○●➜➤\-]\s*(.{15,80})",
        r"\d\)\s*(.{15,80})",
        r"\d\.\s*(.{15,80})",
    ):
        for m in _re.findall(pat, text):
            cleaned = m.strip()
            if len(cleaned) > 15 and _re.search(r"[가-힣]", cleaned):
                if cleaned not in bullets:
                    bullets.append(cleaned)
            if len(bullets) >= 5:
                break
        if len(bullets) >= 5:
            break
    result["key_points"] = bullets[:5]
    return result


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
            extracted = _extract_report_pdf(info)
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


@app.route("/api/short")
def api_short():
    """
    공매도 현황 — pykrx get_shorting_balance_by_ticker 시도.
    Cache: cache/short_{sort}_{date}.json
    """
    sort_by = (request.args.get("sort") or "ratio").strip()
    if sort_by not in ("ratio", "balance"):
        sort_by = "ratio"

    today      = _get_trading_date()
    cache_file = BASE_DIR / "cache" / f"short_{sort_by}_{today}.json"
    if cache_file.exists():
        return Response(
            cache_file.read_text(encoding="utf-8"),
            content_type="application/json; charset=utf-8",
        )

    try:
        from pykrx import stock as _stock
    except ImportError:
        return jsonify({"error": "pykrx 미설치"}), 500

    try:
        df = _stock.get_shorting_balance_by_ticker(today, "KOSPI")
    except Exception as exc:
        return jsonify({
            "error": f"pykrx 공매도 API 호출 실패 ({exc})",
            "hint":  "KRX_API_KEY 환경변수 설정 후 사용 가능합니다.",
        }), 503

    if df is None or df.empty:
        return jsonify({"error": "데이터 없음"}), 503

    ratio_col   = next((c for c in df.columns if "비중"    in c), None)
    balance_col = next((c for c in df.columns if "잔고금액" in c or "금액" in c), None)
    key_col     = ratio_col if sort_by == "ratio" else (balance_col or ratio_col)
    if key_col is None:
        return jsonify({"error": "공매도 컬럼 없음"}), 503

    df_sorted = df.sort_values(key_col, ascending=False).head(30)
    stocks = []
    for code, row in df_sorted.iterrows():
        stocks.append({
            "code":    str(code),
            "name":    row.get("종목명", ""),
            "ratio":   float(row[ratio_col])   if ratio_col   else None,
            "balance": int(row[balance_col])   if balance_col else None,
        })

    result = {"sort": sort_by, "stocks": stocks}
    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify(result)


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
