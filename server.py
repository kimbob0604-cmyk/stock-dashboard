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
from concurrent.futures import ThreadPoolExecutor, as_completed
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

# ── SQLite 캐시 (JSON 폴백 유지) ──
USE_SQLITE = os.environ.get("USE_SQLITE", "1") == "1"
try:
    from db.database import (
        get_db as _get_db, dict_from_row as _db_row, init_db as _init_db,
        read_chart_db as _read_chart_db,
        read_financial_db as _read_financial_db,
        read_flow_db as _read_flow_db,
        read_yinfo_db as _read_yinfo_db,
    )
    _SQLITE_OK = True
except ImportError:
    _SQLITE_OK = False
    USE_SQLITE = False
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

app = Flask(__name__, static_folder=str(BASE_DIR / "static"), static_url_path="/static")
# 한글이 \uXXXX 로 이스케이프되지 않도록 (jsonify 응답)
app.config["JSON_AS_ASCII"] = False
try:
    app.json.ensure_ascii = False      # Flask >= 2.2
except Exception:
    pass

# ── WebSocket (Flask-SocketIO) ──
try:
    from flask_socketio import SocketIO, emit, join_room, leave_room
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
    _SOCKETIO_OK = True
except ImportError:
    socketio = None
    _SOCKETIO_OK = False


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
# pykrx 타임아웃 래퍼 (KRX 서버 무한 대기 방지)
# ─────────────────────────────────────────────────────────────────────────────
_pykrx_pool = ThreadPoolExecutor(max_workers=5, thread_name_prefix="pykrx")


def _pykrx_call(func, *args, timeout: int | None = None, **kwargs):
    """pykrx 함수를 타임아웃 감싸서 호출. 장외 시간에는 5초로 단축."""
    if timeout is None:
        timeout = 15 if is_market_hours() else 5
    try:
        future = _pykrx_pool.submit(func, *args, **kwargs)
        return future.result(timeout=timeout)
    except Exception as exc:
        log.debug("[pykrx] %s(%s) timeout/fail (%ds): %s",
                  getattr(func, "__name__", "?"), args[:2], timeout, exc)
        return None


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


def _calc_rsi_macd(closes: list) -> dict:
    """RSI(14) + MACD(12,26,9) 순수 Python 계산. pandas 미사용."""
    n = len(closes)
    # RSI 14
    rsi = [0.0] * n
    if n >= 15:
        gains = [0.0] * n
        losses = [0.0] * n
        for i in range(1, n):
            diff = closes[i] - closes[i - 1]
            if diff > 0:
                gains[i] = diff
            else:
                losses[i] = -diff
        avg_gain = sum(gains[1:15]) / 14
        avg_loss = sum(losses[1:15]) / 14
        for i in range(14, n):
            if i > 14:
                avg_gain = (avg_gain * 13 + gains[i]) / 14
                avg_loss = (avg_loss * 13 + losses[i]) / 14
            if avg_loss == 0:
                rsi[i] = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi[i] = round(100 - 100 / (1 + rs), 2)

    # MACD (12, 26, 9) — EMA 계산
    def _ema(data: list, span: int) -> list:
        out = [0.0] * len(data)
        if not data:
            return out
        k = 2 / (span + 1)
        out[0] = data[0]
        for i in range(1, len(data)):
            out[i] = data[i] * k + out[i - 1] * (1 - k)
        return out

    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line = [round(ema12[i] - ema26[i], 3) for i in range(n)]
    macd_signal = _ema(macd_line, 9)
    macd_signal = [round(v, 3) for v in macd_signal]
    macd_hist = [round(macd_line[i] - macd_signal[i], 3) for i in range(n)]

    # 다이버전스 감지 (최근 40봉)
    divergences = _detect_divergences(closes, rsi, macd_line)

    return {
        "rsi":         rsi,
        "macd":        macd_line,
        "macd_signal": macd_signal,
        "macd_hist":   macd_hist,
        "divergences": divergences,
    }


def _detect_divergences(closes: list, rsi: list, macd: list) -> list:
    """
    RSI/MACD 기반 다이버전스 감지.
    베어리시: 가격 신고가 but 지표 저하
    불리시: 가격 신저가 but 지표 상승
    최근 40봉 내 2개 피크/밸리 비교.
    """
    n = len(closes)
    if n < 40:
        return []
    lookback = min(40, n)
    window = slice(n - lookback, n)
    cs = closes[window]
    rs = rsi[window]
    mc = macd[window]
    divs: list[dict] = []

    def _find_peaks(arr: list, is_max: bool = True) -> list[int]:
        """단순 피크/밸리 인덱스 (3봉 기준 로컬 극값)."""
        peaks = []
        for i in range(2, len(arr) - 2):
            if arr[i] == 0:
                continue
            if is_max and arr[i] >= arr[i-1] and arr[i] >= arr[i-2] and arr[i] >= arr[i+1] and arr[i] >= arr[i+2]:
                peaks.append(i)
            elif not is_max and arr[i] <= arr[i-1] and arr[i] <= arr[i-2] and arr[i] <= arr[i+1] and arr[i] <= arr[i+2]:
                peaks.append(i)
        return peaks

    # 가격 고점들
    price_highs = _find_peaks(cs, True)
    price_lows  = _find_peaks(cs, False)

    # 베어리시 다이버전스: 가격 신고가 but RSI/MACD 저하
    if len(price_highs) >= 2:
        p1, p2 = price_highs[-2], price_highs[-1]
        if cs[p2] > cs[p1]:
            for label, ind in [("RSI", rs), ("MACD", mc)]:
                if ind[p2] < ind[p1] and ind[p2] != 0 and ind[p1] != 0:
                    divs.append({
                        "type": "bearish",
                        "indicator": label,
                        "idx1": p1 + (n - lookback),
                        "idx2": p2 + (n - lookback),
                    })

    # 불리시 다이버전스: 가격 신저가 but RSI/MACD 상승
    if len(price_lows) >= 2:
        p1, p2 = price_lows[-2], price_lows[-1]
        if cs[p2] < cs[p1]:
            for label, ind in [("RSI", rs), ("MACD", mc)]:
                if ind[p2] > ind[p1] and ind[p2] != 0 and ind[p1] != 0:
                    divs.append({
                        "type": "bullish",
                        "indicator": label,
                        "idx1": p1 + (n - lookback),
                        "idx2": p2 + (n - lookback),
                    })

    return divs


def _calc_adx(highs: list, lows: list, closes: list, period: int = 14) -> dict | None:
    """ADX(14) 순수 Python 계산. 추세 강도 지표."""
    n = len(closes)
    if n < period * 2 + 1:
        return None
    adx_arr = [0.0] * n
    plus_di_arr = [0.0] * n
    minus_di_arr = [0.0] * n

    # True Range + DM
    tr = [0.0] * n
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        h_diff = highs[i] - highs[i - 1]
        l_diff = lows[i - 1] - lows[i]
        plus_dm[i]  = h_diff if h_diff > l_diff and h_diff > 0 else 0
        minus_dm[i] = l_diff if l_diff > h_diff and l_diff > 0 else 0
        tr[i] = max(highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i]  - closes[i - 1]))

    # Wilder smoothing (SMA 초기 → EMA)
    def _smooth(arr, p):
        out = [0.0] * len(arr)
        out[p] = sum(arr[1:p + 1]) / p
        for i in range(p + 1, len(arr)):
            out[i] = (out[i - 1] * (p - 1) + arr[i]) / p
        return out

    sm_tr = _smooth(tr, period)
    sm_pdm = _smooth(plus_dm, period)
    sm_mdm = _smooth(minus_dm, period)

    dx = [0.0] * n
    for i in range(period, n):
        if sm_tr[i] == 0:
            continue
        pdi = sm_pdm[i] / sm_tr[i] * 100
        mdi = sm_mdm[i] / sm_tr[i] * 100
        plus_di_arr[i] = round(pdi, 2)
        minus_di_arr[i] = round(mdi, 2)
        denom = pdi + mdi
        dx[i] = abs(pdi - mdi) / denom * 100 if denom > 0 else 0

    adx_smoothed = _smooth(dx, period)
    for i in range(period * 2, n):
        adx_arr[i] = round(adx_smoothed[i], 2)

    return {
        "adx":       adx_arr,
        "plus_di":   plus_di_arr,
        "minus_di":  minus_di_arr,
    }


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


def _overlay_live_prices_on_data(data: dict) -> dict:
    """data.json 의 themes.stocks[].change_pct 와 weighted_avg_pct 를
    naver_universe 실시간 값으로 덮어씀. 장중 실시간 반영용.
    kospi/kosdaq 값도 정리."""
    uni = _load_naver_universe()
    stocks_live = (uni or {}).get("stocks") or {}
    if not stocks_live:
        return data  # 실시간 소스 없으면 원본 그대로

    updated_stocks = 0
    for theme in (data.get("themes") or []):
        total_vol = 0.0
        weighted_chg = 0.0
        active = 0
        for s in (theme.get("stocks") or []):
            code = s.get("code")
            live = stocks_live.get(code)
            if live and live.get("change_pct") is not None and live.get("close"):
                s["change_pct"] = round(float(live["change_pct"]), 2)
                s["volume_mn"] = live.get("volume_mn") or s.get("volume_mn", 0)
                s["close"] = live.get("close")
                updated_stocks += 1
            # 가중평균 계산용
            chg = s.get("change_pct") or 0
            vol = s.get("volume_mn") or 0
            if vol > 0:
                total_vol += vol
                weighted_chg += chg * vol
            if abs(chg) > 0.01:
                active += 1
        if total_vol > 0:
            theme["weighted_avg_pct"] = round(weighted_chg / total_vol, 2)
            theme["active_count"] = active

    # KOSPI/KOSDAQ 지수는 macro_data.json 또는 naver_universe 에서 집계된 현 값 유지
    # → naver_universe 에는 없으니 기존 값 그대로 두되, updated_at 갱신
    data["updated_at"] = now_kst().strftime("%Y-%m-%d %H:%M:%S")
    data["_overlay"] = {"live_stocks_matched": updated_stocks,
                        "overlay_at": data["updated_at"]}
    return data


@app.route("/data.json")
def route_data_json():
    if not DATA_JSON.exists():
        st = _get()
        return jsonify({
            "error":   "data.json 아직 준비 중입니다.",
            "state":   st["state"],
            "started": st["started_at"],
        }), 503
    try:
        data = json.loads(DATA_JSON.read_text(encoding="utf-8"))
        data = _overlay_live_prices_on_data(data)
        return Response(json.dumps(data, ensure_ascii=False),
                        content_type="application/json; charset=utf-8")
    except Exception as exc:
        log.debug("[data.json overlay] %s", exc)
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
    S&P 500 + S&P 400 MidCap + S&P 600 SmallCap 구성 종목 (Wikipedia).
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
    except ImportError:
        return []

    wiki_sources = [
        ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", "S&P500"),
        ("https://en.wikipedia.org/wiki/List_of_S%26P_400_companies", "S&P400"),
        ("https://en.wikipedia.org/wiki/List_of_S%26P_600_companies", "S&P600"),
    ]

    seen: set[str] = set()
    out: list[dict] = []

    for url, label in wiki_sources:
        try:
            tables = _pd.read_html(url, storage_options={"User-Agent": "Mozilla/5.0"})
            if not tables:
                continue
            df = tables[0]
            added = 0
            for _, row in df.iterrows():
                sym = str(row.get("Symbol") or row.get("Ticker symbol") or "").strip()
                sym = sym.replace(".", "-")
                if not sym or sym == "nan" or sym in seen:
                    continue
                seen.add(sym)
                name = str(row.get("Security") or row.get("Company") or sym)
                sector = str(row.get("GICS Sector") or row.get("GICS sector") or "")
                sub = str(row.get("GICS Sub-Industry") or row.get("GICS sub-industry") or "")
                out.append({
                    "symbol": sym, "name": name,
                    "sector": sector, "sub_industry": sub,
                })
                added += 1
            log.info("[US Universe] %s: %d종목", label, added)
        except Exception as exc:
            log.debug("[US Universe] %s 파싱 실패: %s", label, exc)

    # 소형주/테마주 보충 (S&P 1500 밖 hot small caps)
    try:
        additional = _get_additional_us_tickers()
        seen_syms = {t["symbol"] for t in out}
        for sym in additional:
            if sym not in seen_syms:
                out.append({"symbol": sym, "name": sym, "sector": "",
                            "sub_industry": "", "source": "additional"})
                seen_syms.add(sym)
        log.info("[US Universe] 소형주 보충: %d종목", len(additional))
    except Exception as exc:
        log.debug("[US Universe] additional 실패: %s", exc)

    if out:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    log.info("[US Universe] 총 %d종목 로드 (S&P 500+400+600 + 추가)", len(out))
    return out


# S&P 1500 밖 핵심 소형주·테마주 (고정 리스트, 자동 보충 실패 시 백업)
_US_HOT_SMALL_CAPS = [
    # 우주
    "PL", "RKLB", "LUNR", "ASTS", "ASTR", "RDW", "MNTS", "BKSY", "SPCE",
    # 양자컴퓨터
    "IONQ", "RGTI", "QUBT", "QBTS", "ARQQ",
    # AI 소형주
    "SOUN", "BBAI", "PLTR",
    # 원전/SMR
    "OKLO", "SMR", "NNE", "LEU",
    # 반도체 소형주
    "AXTI", "ACLS", "FORM", "CAMT", "ONTO", "IMMR",
    # eVTOL/로봇
    "JOBY", "LILM", "ACHR",
    # 크립토 마이닝
    "RIOT", "IREN", "CIFR", "CLSK", "BTBT", "HUT",
]


def _get_additional_us_tickers() -> set:
    """S&P 1500 밖 추가 종목: 고정 hot_small_caps + watchlist/portfolio US + 테마."""
    additional: set = set(_US_HOT_SMALL_CAPS)
    try:
        # 관심종목/포트폴리오에 있는 US 종목
        for path in (BASE_DIR / "cache" / "server_watchlist.json",
                     BASE_DIR / "cache" / "server_portfolio.json"):
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                items = data.get("items") or data.get("positions") or data or []
                if isinstance(items, dict):
                    items = items.get("items") or items.get("positions") or []
                for it in items:
                    code = (it.get("code") or "").strip()
                    mkt = (it.get("market") or "").lower()
                    if code and mkt == "us" and code.isalpha() and 1 <= len(code) <= 5:
                        additional.add(code)
            except Exception:
                pass

        # US agent 파이프라인 테마 종목
        try:
            from agents.pipeline import _US_THEME_TO_GICS, get_us_theme_stocks
            for theme in (_US_THEME_TO_GICS or {}).keys():
                for code in (get_us_theme_stocks(theme) or []):
                    if code and len(code) <= 5:
                        additional.add(code)
        except Exception:
            pass
    except Exception as exc:
        log.debug("[US additional] %s", exc)
    return additional


def add_us_stocks_now(tickers: list) -> dict:
    """누락된 US 종목을 yfinance로 즉시 조회 → stocks 테이블 INSERT."""
    if not (_SQLITE_OK and USE_SQLITE):
        return {"error": "SQLite 비활성"}
    try:
        import yfinance as _yf
    except ImportError:
        return {"error": "yfinance 미설치"}

    added = 0; failed = 0; skipped = 0
    details: list = []
    with _get_db() as conn:
        for raw in tickers:
            sym = (raw or "").strip().upper()
            if not sym:
                continue
            try:
                t = _yf.Ticker(sym)
                info = t.info or {}
                price = info.get("regularMarketPrice") or info.get("currentPrice") or 0
                prev = info.get("regularMarketPreviousClose") or info.get("previousClose") or 0
                if not price:
                    details.append(f"{sym}: no price")
                    skipped += 1
                    continue
                chg = round((price / prev - 1) * 100, 2) if prev else 0
                name = info.get("shortName") or info.get("longName") or sym
                sector = info.get("sector") or ""
                cap = info.get("marketCap") or 0
                conn.execute(
                    "INSERT OR REPLACE INTO stocks "
                    "(code, name, market, sector, market_cap, close, change_pct, "
                    "volume_mn, sectors_json, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?, datetime('now'))",
                    (sym, name, "US", sector, cap, price, chg,
                     (info.get("regularMarketVolume") or 0) / 1e6,
                     json.dumps([sector] if sector else [], ensure_ascii=False))
                )
                added += 1
                details.append(f"{sym}: ✓ {name} ${price} ({sector})")
            except Exception as exc:
                failed += 1
                details.append(f"{sym}: err {exc}")
        conn.commit()
    log.info("[US add] +%d, skip %d, fail %d", added, skipped, failed)
    return {"added": added, "skipped": skipped, "failed": failed, "details": details}


@app.route("/api/us/add_stocks", methods=["POST"])
def api_us_add_stocks():
    """수동으로 US 종목 추가 (쉼표 구분 또는 body.tickers 배열).
    기본: _US_HOT_SMALL_CAPS 전체."""
    data = request.get_json(silent=True) or {}
    tickers = data.get("tickers")
    if not tickers:
        q = request.args.get("tickers", "")
        tickers = [t.strip() for t in q.split(",") if t.strip()] if q else _US_HOT_SMALL_CAPS
    return jsonify(add_us_stocks_now(tickers))


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
    MIN_STOCKS = 400   # 정상 빌드 최소 기준 (S&P1500 중 일부 실패 허용)
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
    """US 종목 검색.
    데이터 소스: 1) DB stocks(market='US') 우선 (authoritative, 시총순 정렬)
                 2) _sp500_tickers() 캐시 폴백 (DB 아직 비어있을 때)."""
    q = (request.args.get("q") or "").strip()
    if len(q) < 1:
        return jsonify([])
    ql = q.lower()
    ql_like = f"%{ql}%"

    results: list = []
    seen: set = set()

    # 1) DB 우선 — 정확 일치 > 접두 > 부분 순
    if _SQLITE_OK and USE_SQLITE:
        try:
            with _get_db() as conn:
                rows = conn.execute(
                    "SELECT code, name, market, sector, close, market_cap "
                    "FROM stocks WHERE market = 'US' "
                    "AND (LOWER(code) LIKE ? OR LOWER(name) LIKE ?) "
                    "ORDER BY "
                    "  CASE WHEN LOWER(code) = ? OR LOWER(name) = ? THEN 0 "
                    "       WHEN LOWER(code) LIKE ? OR LOWER(name) LIKE ? THEN 1 "
                    "       ELSE 2 END, "
                    "  COALESCE(market_cap, 0) DESC "
                    "LIMIT 20",
                    (ql_like, ql_like, ql, ql, f"{ql}%", f"{ql}%")
                ).fetchall()
                for r in rows:
                    code = r["code"]
                    if code in seen:
                        continue
                    seen.add(code)
                    results.append({
                        "code":       code,
                        "name":       r["name"],
                        "sector":     r["sector"] or "",
                        "price":      r["close"],
                        "market_cap": r["market_cap"],
                    })
        except Exception as exc:
            log.debug("[us/search] DB: %s", exc)

    # 2) 캐시 폴백 — DB에 없는 종목만 보충
    if len(results) < 10:
        try:
            tickers = _sp500_tickers()
            for t in tickers:
                sym = t["symbol"]
                if sym in seen:
                    continue
                if ql in sym.lower() or ql in (t.get("name") or "").lower():
                    results.append({
                        "code":   sym,
                        "name":   t.get("name") or sym,
                        "sector": t.get("sector") or "",
                    })
                    seen.add(sym)
                    if len(results) >= 20:
                        break
        except Exception:
            pass

    return jsonify(results[:20])


@app.route("/api/us/extended/<symbol>")
def api_us_extended(symbol: str):
    """미국 프리마켓/애프터마켓 가격. yfinance info 기반."""
    import re as _re
    if not _re.fullmatch(r"[A-Z][A-Z0-9\-\.]{0,9}", symbol.upper()):
        return jsonify({"error": "잘못된 심볼"}), 400
    symbol = symbol.upper()

    try:
        import yfinance as _yf
    except ImportError:
        return jsonify({"error": "yfinance 미설치"}), 500

    try:
        info = _yf.Ticker(symbol).info or {}
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    market_state = info.get("marketState", "")
    regular_price = info.get("regularMarketPrice")
    regular_chg = info.get("regularMarketChangePercent")

    result = {
        "symbol": symbol,
        "market_state": market_state,
        "regular_price": regular_price,
        "regular_change_pct": regular_chg,
        "extended_price": None,
        "extended_change_pct": None,
        "extended_label": None,
    }

    # PRE: 프리마켓 진행 중
    # POST/CLOSED: 애프터마켓 또는 장 마감 후
    if market_state == "PRE" and info.get("preMarketPrice"):
        result["extended_price"] = info.get("preMarketPrice")
        result["extended_change_pct"] = info.get("preMarketChangePercent")
        result["extended_label"] = "프리마켓"
    elif market_state in ("POST", "POSTPOST", "CLOSED") and info.get("postMarketPrice"):
        result["extended_price"] = info.get("postMarketPrice")
        result["extended_change_pct"] = info.get("postMarketChangePercent")
        result["extended_label"] = "애프터마켓"
    elif info.get("preMarketPrice"):
        # fallback: marketState가 애매해도 preMarketPrice가 있으면 표시
        result["extended_price"] = info.get("preMarketPrice")
        result["extended_change_pct"] = info.get("preMarketChangePercent")
        result["extended_label"] = "프리마켓"
    elif info.get("postMarketPrice"):
        result["extended_price"] = info.get("postMarketPrice")
        result["extended_change_pct"] = info.get("postMarketChangePercent")
        result["extended_label"] = "애프터마켓"

    return jsonify(result)


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

    # ── SQLite 캐시 우선 조회 ──
    if USE_SQLITE and _SQLITE_OK:
        try:
            with _get_db() as _conn:
                row = _conn.execute(
                    "SELECT * FROM chart_cache WHERE code=? AND days=? AND cache_date=?",
                    (symbol, days, today_kst),
                ).fetchone()
                if row:
                    d = _db_row(row)
                    if d and d.get("rsi_macd"):
                        result = {
                            "code": d["code"], "name": d.get("name", symbol),
                            "days": d["days"], "market": "US", "currency": "USD",
                            "dates": d.get("dates", []),
                            "open": d.get("open", []), "high": d.get("high", []),
                            "low": d.get("low", []), "close": d.get("close", []),
                            "volume": d.get("volume", []),
                            "bollinger": d.get("bollinger", {}),
                            "fibonacci": d.get("fibonacci", {}),
                            "trendlines": d.get("trendlines", {}),
                            "analysis": d.get("analysis", {}),
                            "rsi_macd": d.get("rsi_macd", {}),
                            "adx": d.get("adx", {}),
                        }
                        return jsonify(result)
        except Exception as exc:
            log.debug("[SQLite] us_chart read fail %s: %s", symbol, exc)

    # ── JSON 파일 캐시 폴백 ──
    if cache_file.exists():
        try:
            _cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if "rsi_macd" in _cached and _cached["rsi_macd"] is not None:
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
        "rsi_macd":   _calc_rsi_macd(closes),
        "adx":        _calc_adx(highs, lows, closes),
    }
    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(result, ensure_ascii=False),
                          encoding="utf-8")
    _save_chart_to_sqlite(symbol, days, today_kst, result)
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
    """미국 종목 현재가.
    데이터 소스: 1) DB stocks(market='US') 우선 (소형주 포함)
                 2) us_market_*.json 캐시 폴백 (prev_close 등 부가 필드)."""
    import re as _re
    if not _re.fullmatch(r"[A-Z][A-Z0-9\-\.]{0,9}", symbol.upper()):
        return jsonify({"error": "잘못된 심볼"}), 400
    symbol = symbol.upper()

    # 1) DB 우선
    if _SQLITE_OK and USE_SQLITE:
        try:
            with _get_db() as conn:
                r = conn.execute(
                    "SELECT code, name, close, change_pct, volume_mn, updated_at "
                    "FROM stocks WHERE code = ? AND market = 'US'",
                    (symbol,)
                ).fetchone()
            if r and r["close"] is not None:
                return jsonify({
                    "code":       r["code"],
                    "name":       r["name"],
                    "price":      r["close"],
                    "prev_close": None,
                    "change":     None,
                    "change_pct": r["change_pct"],
                    "volume_mn":  r["volume_mn"],
                    "source":     "db",
                    "fetched_at": r["updated_at"],
                })
        except Exception as exc:
            log.debug("[us/price] DB: %s", exc)

    # 2) us_market 캐시 폴백
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

    # ── SQLite 캐시 우선 ──
    if USE_SQLITE and _SQLITE_OK:
        try:
            with _get_db() as _conn:
                row = _conn.execute("SELECT * FROM flow_cache WHERE code=?", (code,)).fetchone()
                if row:
                    d = _db_row(row)
                    if d and d.get("updated_at"):
                        from datetime import datetime as _dt
                        try:
                            upd = _dt.fromisoformat(d["updated_at"])
                            age_min = (now_kst().replace(tzinfo=None) - upd).total_seconds() / 60
                            if age_min < 60:
                                return jsonify(d)
                        except Exception:
                            return jsonify(d)
        except Exception as exc:
            log.debug("[SQLite] flow read fail %s: %s", code, exc)

    # ── JSON 파일 캐시 폴백 ──
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

    # SQLite 동시 기록
    if USE_SQLITE and _SQLITE_OK:
        try:
            with _get_db() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO flow_cache "
                    "(code, name, dates_json, close_json, foreign_shares_json, "
                    "inst_shares_json, foreign_value_json, inst_value_json, "
                    "foreign_sum_20, inst_sum_20, source, fetched_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                    (
                        code, stock_name,
                        json.dumps(dates, ensure_ascii=False),
                        json.dumps(closes, ensure_ascii=False),
                        json.dumps(foreign_net, ensure_ascii=False),
                        json.dumps(inst_net, ensure_ascii=False),
                        json.dumps(foreign_value, ensure_ascii=False),
                        json.dumps(inst_value, ensure_ascii=False),
                        sum(foreign_value), sum(inst_value),
                        "naver_finance", result["fetched_at"],
                    ),
                )
                conn.commit()
        except Exception as exc:
            log.debug("[SQLite] flow write fail: %s", exc)

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


_UNI_CACHE: dict = {"data": None, "mtime": 0, "path": None}


def _load_naver_universe() -> dict:
    """
    naver_universe 캐시 로드 (메모리 캐싱).
    파일 mtime 비교해서 변경됐을 때만 재로드. KST 오늘자 우선 → 거래일 → 최근 파일 fallback.
    """
    today_kst = now_kst().strftime("%Y%m%d")
    trading_date = _get_trading_date()

    # 후보 파일 결정: KST 오늘 > pykrx 거래일 > 가장 최근 파일
    target: Path | None = None
    for cand in (today_kst, trading_date):
        f = BASE_DIR / "cache" / f"naver_universe_{cand}.json"
        if f.exists():
            target = f
            break
    if target is None:
        try:
            import glob as _glob
            files = sorted(
                _glob.glob(str(BASE_DIR / "cache" / "naver_universe_*.json")),
                reverse=True,
            )
            if files:
                target = Path(files[0])
        except Exception:
            pass

    if target is None:
        return {}

    # mtime 비교 — 변경 없으면 메모리 캐시 반환
    try:
        mt = target.stat().st_mtime
    except Exception:
        return _UNI_CACHE["data"] or {}

    if _UNI_CACHE["path"] == str(target) and _UNI_CACHE["mtime"] == mt and _UNI_CACHE["data"]:
        return _UNI_CACHE["data"]

    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        _UNI_CACHE.update({"data": data, "mtime": mt, "path": str(target)})
        return data
    except Exception:
        return _UNI_CACHE["data"] or {}


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

    df1 = _pykrx_call(_stock.get_market_ohlcv_by_date, start, today, code1, timeout=15)
    df2 = _pykrx_call(_stock.get_market_ohlcv_by_date, start, today, code2, timeout=15)

    if df1 is None or df2 is None or (hasattr(df1, "empty") and df1.empty) or (hasattr(df2, "empty") and df2.empty):
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
        name1 = _pykrx_call(_stock.get_market_ticker_name, code1, timeout=5) or code1
    except Exception:
        name1 = code1
    try:
        name2 = _pykrx_call(_stock.get_market_ticker_name, code2, timeout=5) or code2
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

    # ── SQLite 캐시 우선 ──
    if USE_SQLITE and _SQLITE_OK:
        try:
            with _get_db() as _conn:
                row = _conn.execute("SELECT * FROM financial WHERE code=?", (code,)).fetchone()
                if row:
                    d = _db_row(row)
                    # updated_at로 TTL 체크 (24h)
                    if d and d.get("updated_at"):
                        from datetime import datetime as _dt
                        try:
                            upd = _dt.fromisoformat(d["updated_at"])
                            age_hr = (now_kst().replace(tzinfo=None) - upd).total_seconds() / 3600
                            if age_hr < 24:
                                return jsonify(d)
                        except Exception:
                            return jsonify(d)
        except Exception as exc:
            log.debug("[SQLite] financial read fail %s: %s", code, exc)

    # ── JSON 파일 캐시 폴백 ──
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

    # SQLite 동시 기록
    if USE_SQLITE and _SQLITE_OK:
        try:
            with _get_db() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO financial "
                    "(code, name, per, eps, estimate_per, estimate_eps, pbr, bps, "
                    "dividend_yield, market_cap, market_cap_rank, industry_per, "
                    "shares_outstanding, foreign_ratio, annual_json, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                    (
                        result.get("code"), result.get("name"),
                        result.get("per"), result.get("eps"),
                        result.get("estimate_per"), result.get("estimate_eps"),
                        result.get("pbr"), result.get("bps"),
                        result.get("dividend_yield"), result.get("market_cap"),
                        result.get("market_cap_rank"), result.get("industry_per"),
                        result.get("shares_outstanding"), result.get("foreign_ratio"),
                        json.dumps(result.get("annual") or [], ensure_ascii=False),
                    ),
                )
                conn.commit()
        except Exception as exc:
            log.debug("[SQLite] financial write fail: %s", exc)

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
        "rsi_macd":    _calc_rsi_macd(closes),
        "adx":         _calc_adx(highs, lows, closes),
        "source":      "naver_finance",
        "fetched_at":  now_kst().strftime("%Y-%m-%d %H:%M:%S"),
    }
    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return jsonify(result)


def _append_today_candle_kr(result: dict, code: str) -> bool:
    """차트 결과에 오늘 실시간 캔들을 추가하고 지표 재계산.
    return True if appended, False if not applicable."""
    try:
        today_kst = now_kst().strftime("%Y-%m-%d")
        # 주말이면 추가 안 함
        if now_kst().weekday() >= 5:
            return False
        dates = result.get("dates") or []
        if dates and dates[-1] >= today_kst:
            return False  # 이미 오늘 데이터 있음

        import urllib.request
        url = f"https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            live = json.loads(resp.read().decode("utf-8"))
        s = (live.get("datas") or [None])[0]
        if not s:
            return False

        def _num(v):
            try: return float(str(v).replace(",", ""))
            except Exception: return 0

        close_p = _num(s.get("closePrice"))
        open_p  = _num(s.get("openPrice"))  or close_p
        high_p  = _num(s.get("highPrice"))  or close_p
        low_p   = _num(s.get("lowPrice"))   or close_p
        vol     = int(_num(s.get("accumulatedTradingVolume")))
        if close_p <= 0:
            return False

        result["dates"].append(today_kst)
        result["open"].append(int(open_p))
        result["high"].append(int(high_p))
        result["low"].append(int(low_p))
        result["close"].append(int(close_p))
        result["volume"].append(vol)

        closes = result["close"]; highs = result["high"]; lows = result["low"]
        result["rsi_macd"]  = _calc_rsi_macd(closes)
        result["adx"]       = _calc_adx(highs, lows, closes)
        result["bollinger"] = _calc_bollinger(closes)
        # 피보나치/추세선/분석은 재계산 비용 대비 의미 작아 스킵
        return True
    except Exception as exc:
        log.debug("[chart append today] %s: %s", code, exc)
        return False


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

    # ── SQLite 캐시 우선 조회 ──
    if USE_SQLITE and _SQLITE_OK:
        try:
            with _get_db() as _conn:
                row = _conn.execute(
                    "SELECT * FROM chart_cache WHERE code=? AND days=? AND cache_date=?",
                    (code, days, today),
                ).fetchone()
                if row:
                    d = _db_row(row)
                    if d and d.get("rsi_macd"):
                        result = {
                            "code": d["code"], "name": d.get("name", code),
                            "days": d["days"],
                            "dates": d.get("dates", []),
                            "open": d.get("open", []), "high": d.get("high", []),
                            "low": d.get("low", []), "close": d.get("close", []),
                            "volume": d.get("volume", []),
                            "bollinger": d.get("bollinger", {}),
                            "fibonacci": d.get("fibonacci", {}),
                            "trendlines": d.get("trendlines", {}),
                            "analysis": d.get("analysis", {}),
                            "rsi_macd": d.get("rsi_macd", {}),
                            "adx": d.get("adx", {}),
                        }
                        _append_today_candle_kr(result, code)
                        return jsonify(result)
        except Exception as exc:
            log.debug("[SQLite] chart read fail %s: %s → JSON 폴백", code, exc)

    # ── JSON 파일 캐시 폴백 ──
    def _chart_cache_valid(path):
        if not path.exists():
            return False
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            return "rsi_macd" in d and d["rsi_macd"] is not None
        except Exception:
            return False

    if _chart_cache_valid(cache_file):
        try:
            result = json.loads(cache_file.read_text(encoding="utf-8"))
            _append_today_candle_kr(result, code)
            return jsonify(result)
        except Exception:
            pass
    if days == 180:
        legacy = BASE_DIR / "cache" / f"chart_{code}_{today}.json"
        if _chart_cache_valid(legacy):
            try:
                result = json.loads(legacy.read_text(encoding="utf-8"))
                _append_today_candle_kr(result, code)
                return jsonify(result)
            except Exception:
                pass

    try:
        from pykrx import stock as _stock
    except ImportError:
        return jsonify({"error": "pykrx 미설치"}), 500

    start = (datetime.strptime(today, "%Y%m%d").replace(tzinfo=KST) - timedelta(days=days)).strftime("%Y%m%d")
    df = _pykrx_call(_stock.get_market_ohlcv_by_date, start, today, code, timeout=15)

    if df is None or (hasattr(df, "empty") and df.empty):
        return jsonify({"error": "데이터 없음 (타임아웃 또는 KRX 미응답)"}), 404

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

    name = _pykrx_call(_stock.get_market_ticker_name, code, timeout=5) or code

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
        "rsi_macd": _calc_rsi_macd(closes),
        "adx":      _calc_adx(highs, lows, closes),
    }
    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # SQLite 동시 기록 (pykrx 기반 원본만 저장, 실시간 오늘 candle은 response에만 추가)
    _save_chart_to_sqlite(code, days, today, result)

    _append_today_candle_kr(result, code)
    return jsonify(result)


def _save_chart_to_sqlite(code, days, cache_date, result):
    """차트 결과를 SQLite에 동시 기록 (best-effort)."""
    if not (USE_SQLITE and _SQLITE_OK):
        return
    try:
        with _get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO chart_cache "
                "(code, days, cache_date, name, dates_json, open_json, high_json, "
                "low_json, close_json, volume_json, bollinger_json, fibonacci_json, "
                "trendlines_json, analysis_json, rsi_macd_json, adx_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    code, days, cache_date, result.get("name"),
                    json.dumps(result.get("dates", []), ensure_ascii=False),
                    json.dumps(result.get("open", []), ensure_ascii=False),
                    json.dumps(result.get("high", []), ensure_ascii=False),
                    json.dumps(result.get("low", []), ensure_ascii=False),
                    json.dumps(result.get("close", []), ensure_ascii=False),
                    json.dumps(result.get("volume", []), ensure_ascii=False),
                    json.dumps(result.get("bollinger", {}), ensure_ascii=False),
                    json.dumps(result.get("fibonacci", {}), ensure_ascii=False),
                    json.dumps(result.get("trendlines", {}), ensure_ascii=False),
                    json.dumps(result.get("analysis", {}), ensure_ascii=False),
                    json.dumps(result.get("rsi_macd", {}), ensure_ascii=False),
                    json.dumps(result.get("adx", {}), ensure_ascii=False),
                ),
            )
            conn.commit()
    except Exception as exc:
        log.debug("[SQLite] chart write fail %s: %s", code, exc)


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

# ─────────────────────────────────────────────────────────────────────────
# PHASE 24 Step 2 — 국내 야간선물 + 옵션 PCR (best-effort)
# ─────────────────────────────────────────────────────────────────────────
def _fetch_night_futures() -> dict:
    """
    코스피200 야간선물 종가 조회. 3-tier fallback:
      1) yfinance ^KS200 (코스피200 현물 지수, 가장 신뢰도 높음)
      2) esignal.co.kr 스크랩 (페이지 구조 변경 시 실패)
      3) investing.com 스크랩 (봇 차단 가능성)
    전부 실패 시 structured empty state.
    """
    result: dict = {
        "day_close":   None,
        "night_close": None,
        "change":      None,
        "change_pct":  None,
        "signal":      None,
        "source":      None,
        "attempted":   [],
        "updated_at":  now_kst().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # Tier 1: yfinance ^KS200 — 최근 2일 종가 비교로 "야간선물 프록시"
    try:
        import yfinance as _yf
        h = _yf.Ticker("^KS200").history(period="5d")
        if h is not None and not h.empty and len(h) >= 2:
            closes = [float(v) for v in h["Close"].tolist() if v == v]
            if len(closes) >= 2:
                result["day_close"]   = round(closes[-2], 2)
                result["night_close"] = round(closes[-1], 2)
                result["source"] = "yfinance:^KS200"
                result["attempted"].append({"source": "yfinance ^KS200", "ok": True})
    except Exception as exc:
        result["attempted"].append({
            "source": "yfinance ^KS200", "ok": False, "error": str(exc)[:80]
        })

    # Tier 1b: pykrx 로 day_close 보강 (night_close 가 다른 소스에서 확보된 경우)
    # 코스피200 지수 (1028) 최근 영업일 종가 조회
    if result["day_close"] is None:
        try:
            from pykrx import stock as _pykrx_stock
            from datetime import timedelta as _td
            # 최근 7일 내에서 데이터가 있는 영업일 찾기
            today_dt = now_kst().date()
            day_close_val = None
            for back in range(1, 8):
                d_str = (today_dt - _td(days=back)).strftime("%Y%m%d")
                if (today_dt - _td(days=back)).weekday() >= 5:
                    continue
                try:
                    df = _pykrx_call(_pykrx_stock.get_index_ohlcv_by_date, d_str, d_str, "1028", timeout=10)
                    if df is not None and not df.empty and "종가" in df.columns:
                        val = float(df["종가"].iloc[-1])
                        if val > 0:
                            day_close_val = val
                            break
                except Exception:
                    continue
            if day_close_val is not None:
                result["day_close"] = round(day_close_val, 2)
                result["attempted"].append({
                    "source": "pykrx 코스피200(1028)", "ok": True
                })
            else:
                result["attempted"].append({
                    "source": "pykrx 코스피200(1028)", "ok": False,
                    "error": "최근 7일간 OHLCV 없음"
                })
        except ImportError:
            result["attempted"].append({
                "source": "pykrx 코스피200(1028)", "ok": False, "error": "pykrx 미설치"
            })
        except Exception as exc:
            result["attempted"].append({
                "source": "pykrx 코스피200(1028)", "ok": False, "error": str(exc)[:80]
            })

    # Tier 2: esignal.co.kr
    if result["night_close"] is None:
        try:
            import requests as _rq
            from bs4 import BeautifulSoup
            r = _rq.get(
                "https://esignal.co.kr/kospi200-futures-night/",
                headers={"User-Agent": "Mozilla/5.0"}, timeout=8,
            )
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                price_tag = (soup.select_one(".current_price")
                             or soup.select_one(".price")
                             or soup.select_one("[class*='price']"))
                if price_tag:
                    txt = price_tag.get_text(strip=True).replace(",", "")
                    try:
                        result["night_close"] = float(txt)
                        result["source"] = "esignal.co.kr"
                        result["attempted"].append({"source": "esignal.co.kr", "ok": True})
                    except ValueError:
                        result["attempted"].append({
                            "source": "esignal.co.kr", "ok": False,
                            "error": f"파싱 실패: '{txt[:30]}'"
                        })
                else:
                    result["attempted"].append({
                        "source": "esignal.co.kr", "ok": False,
                        "error": "price 셀렉터 매칭 실패"
                    })
            else:
                result["attempted"].append({
                    "source": "esignal.co.kr", "ok": False,
                    "error": f"HTTP {r.status_code}"
                })
        except Exception as exc:
            result["attempted"].append({
                "source": "esignal.co.kr", "ok": False, "error": str(exc)[:80]
            })

    # Tier 3: investing.com
    if result["night_close"] is None:
        try:
            import requests as _rq
            from bs4 import BeautifulSoup
            r = _rq.get(
                "https://kr.investing.com/indices/korea-200-futures",
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                        "AppleWebKit/537.36 Chrome/120"},
                timeout=8,
            )
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                pt = (soup.select_one('[data-test="instrument-price-last"]')
                      or soup.select_one(".text-5xl"))
                if pt:
                    txt = pt.get_text(strip=True).replace(",", "")
                    try:
                        result["night_close"] = float(txt)
                        result["source"] = "investing.com"
                        result["attempted"].append({"source": "investing.com", "ok": True})
                    except ValueError:
                        result["attempted"].append({
                            "source": "investing.com", "ok": False,
                            "error": f"파싱 실패: '{txt[:30]}'"
                        })
            else:
                result["attempted"].append({
                    "source": "investing.com", "ok": False,
                    "error": f"HTTP {r.status_code}"
                })
        except Exception as exc:
            result["attempted"].append({
                "source": "investing.com", "ok": False, "error": str(exc)[:80]
            })

    # 등락률 계산 (day_close 있을 때만)
    if result["night_close"] and result["day_close"]:
        result["change"] = round(result["night_close"] - result["day_close"], 2)
        if result["day_close"]:
            result["change_pct"] = round(
                (result["night_close"] / result["day_close"] - 1) * 100, 2
            )

    # 시그널 매핑
    pct = result.get("change_pct")
    if pct is not None:
        if   pct >  1.5: result["signal"] = "야간선물 강세 → 익일 갭업 출발 예상"
        elif pct >  0.5: result["signal"] = "야간선물 소폭 강세 → 소폭 상승 출발"
        elif pct > -0.5: result["signal"] = "야간선물 보합 → 횡보 출발"
        elif pct > -1.5: result["signal"] = "야간선물 소폭 약세 → 소폭 하락 출발"
        else:            result["signal"] = "야간선물 약세 → 갭다운 출발 예상"
    elif result["night_close"] is not None:
        result["signal"] = f"현재가 {result['night_close']} (전일 종가 불명 — 등락률 계산 불가)"
    else:
        result["signal"] = ("모든 소스 차단 — yfinance·esignal·investing 순서로 시도 실패. "
                            "한투/키움 API 연동 시 활성화")
    return result


@app.route("/api/night_futures")
def api_night_futures():
    """코스피200 야간선물 — 30분 캐시."""
    cache_file = BASE_DIR / "cache" / "night_futures.json"
    cached = _read_fresh_json(cache_file, 30)
    if cached:
        return jsonify(cached)
    result = _fetch_night_futures()
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


@app.route("/api/kr_options")
def api_kr_options():
    """
    코스피200 옵션 PCR — KRX data.krx.co.kr 차단 상태라 현재 전부 unavailable.
    엔드포인트는 유지해서 소스 복구 시 즉시 활성화. 6시간 캐시.
    """
    cache_file = BASE_DIR / "cache" / "kr_options_pcr.json"
    cached = _read_fresh_json(cache_file, 360)
    if cached:
        return jsonify(cached)

    result = {
        "pcr_volume":     None,
        "pcr_oi":         None,
        "signal":         ("KRX data.krx.co.kr 가 외부 접근을 차단한 상태이며 "
                           "공매도와 동일하게 옵션 PCR 도 현재 무료 소스가 없습니다. "
                           "한투 OpenAPI / KIS 증권사 API 연동 시 활성화 가능."),
        "source":         "unavailable",
        "attempted":      [
            {"source": "pykrx option PCR", "ok": False, "error": "KRX blocked"},
            {"source": "data.krx.co.kr getJsonData", "ok": False, "error": "HTTP 400 blocked"},
            {"source": "investing.com kospi200 options", "ok": False, "error": "봇 차단"},
        ],
        "updated_at":     now_kst().strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


# ─────────────────────────────────────────────────────────────────────────
# PHASE 24 — 미국 옵션 시그널 (SPY/QQQ): PCR + MaxPain + GEX 근사
# ─────────────────────────────────────────────────────────────────────────
def _nan_int(v) -> int:
    """NaN/None/빈값을 0으로 변환."""
    try:
        if v is None:
            return 0
        f = float(v)
        if f != f:   # NaN check
            return 0
        return int(f)
    except (TypeError, ValueError):
        return 0


def _nan_float(v) -> float:
    try:
        if v is None:
            return 0.0
        f = float(v)
        return 0.0 if f != f else f
    except (TypeError, ValueError):
        return 0.0


def _compute_options_signal(symbol: str) -> dict:
    """
    SPY/QQQ yfinance 옵션 체인에서 PCR/MaxPain/GEX 근사 계산.
    - 가장 가까운 2개 만기 수집
    - 맥스페인: ATM ±20% 행사가 범위에서 총 옵션 가치 최소 지점
    - GEX 근사: (C_OI − P_OI) × (1 - moneyness*10) × 100 × spot
    """
    try:
        import yfinance as _yf
    except ImportError:
        return {"error": "yfinance 미설치"}

    try:
        t = _yf.Ticker(symbol)
        # 미국 장 마감 후 period='1d' 가 빈 DF 가 되는 경우가 있어 5d 로 폴백
        hist = t.history(period="5d")
        if hist is None or hist.empty:
            return {"error": "현재가 조회 실패 (5d 기간에 데이터 없음)"}
        # NaN 제거 후 마지막 종가
        closes = [c for c in hist["Close"].tolist() if c == c]
        if not closes:
            return {"error": "현재가 NaN"}
        spot = _nan_float(closes[-1])

        exps = list(t.options or [])
        if not exps:
            return {"error": "옵션 만기 없음"}
        target_exps = exps[:2]

        all_calls: list[dict] = []
        all_puts:  list[dict] = []
        for exp in target_exps:
            try:
                chain = t.option_chain(exp)
            except Exception as exc:
                log.debug("chain %s %s fail: %s", symbol, exp, exc)
                continue
            for _, r in chain.calls.iterrows():
                all_calls.append({
                    "expiry":       exp,
                    "strike":       _nan_float(r.get("strike")),
                    "volume":       _nan_int(r.get("volume")),
                    "openInterest": _nan_int(r.get("openInterest")),
                    "iv":           _nan_float(r.get("impliedVolatility")),
                })
            for _, r in chain.puts.iterrows():
                all_puts.append({
                    "expiry":       exp,
                    "strike":       _nan_float(r.get("strike")),
                    "volume":       _nan_int(r.get("volume")),
                    "openInterest": _nan_int(r.get("openInterest")),
                    "iv":           _nan_float(r.get("impliedVolatility")),
                })

        if not all_calls or not all_puts:
            return {"error": "옵션 체인 비어 있음"}

        # ── PCR ──
        tot_pv = sum(p["volume"] for p in all_puts)
        tot_cv = sum(c["volume"] for c in all_calls)
        tot_pi = sum(p["openInterest"] for p in all_puts)
        tot_ci = sum(c["openInterest"] for c in all_calls)
        pcr_vol = round(tot_pv / tot_cv, 3) if tot_cv > 0 else None
        pcr_oi  = round(tot_pi / tot_ci, 3) if tot_ci > 0 else None

        if pcr_vol is None:
            pcr_signal = "거래량 부족"
        elif pcr_vol > 1.2:
            pcr_signal = "극도의 공포 → 역발상 매수 시그널"
        elif pcr_vol > 1.0:
            pcr_signal = "약한 공포 → 주의"
        elif pcr_vol > 0.7:
            pcr_signal = "중립"
        elif pcr_vol > 0.5:
            pcr_signal = "낙관 → 과매수 주의"
        else:
            pcr_signal = "극도의 낙관 → 역발상 매도 시그널"

        # ── 맥스페인 (가장 가까운 만기만) ──
        near_calls = [c for c in all_calls if c["expiry"] == target_exps[0]]
        near_puts  = [p for p in all_puts  if p["expiry"] == target_exps[0]]

        strikes_set = sorted(set(
            [c["strike"] for c in near_calls] + [p["strike"] for p in near_puts]
        ))
        # ATM ±20% 범위로 클램프
        strikes = [s for s in strikes_set if spot * 0.8 <= s <= spot * 1.2]
        if not strikes:
            strikes = strikes_set[:30]

        call_oi_map = {c["strike"]: c["openInterest"] for c in near_calls}
        put_oi_map  = {p["strike"]: p["openInterest"] for p in near_puts}

        pain_by_strike: list[dict] = []
        for test in strikes:
            total = 0.0
            for k in strikes:
                c_oi = call_oi_map.get(k, 0)
                p_oi = put_oi_map.get(k, 0)
                total += max(test - k, 0) * c_oi * 100
                total += max(k - test, 0) * p_oi * 100
            pain_by_strike.append({"strike": test, "total_pain": int(total)})

        if pain_by_strike:
            min_item = min(pain_by_strike, key=lambda x: x["total_pain"])
            max_pain_strike = min_item["strike"]
            mp_diff = round((max_pain_strike / spot - 1) * 100, 2) if spot else 0
            if mp_diff > 1:
                mp_signal = f"현재가 < 맥스페인 → 상방 수렴 압력 (+{mp_diff}%)"
            elif mp_diff < -1:
                mp_signal = f"현재가 > 맥스페인 → 하방 수렴 압력 ({mp_diff}%)"
            else:
                mp_signal = f"맥스페인 근접 → 횡보/레인지 예상 ({mp_diff}%)"
        else:
            max_pain_strike = None
            mp_diff = 0
            mp_signal = "계산 불가"

        # ── GEX 근사 ──
        gex_by_strike: list[dict] = []
        total_gex = 0.0
        for k in strikes:
            c_oi = call_oi_map.get(k, 0)
            p_oi = put_oi_map.get(k, 0)
            moneyness = abs(k - spot) / spot if spot else 1.0
            approx_gamma = max(0.0, 1.0 - moneyness * 10) * 0.01
            strike_gex = (c_oi - p_oi) * approx_gamma * 100 * spot
            gex_by_strike.append({
                "strike": k,
                "call_oi": c_oi,
                "put_oi":  p_oi,
                "net_gex": int(round(strike_gex)),
            })
            total_gex += strike_gex

        if total_gex > 0:
            gex_signal = "양수 GEX → 마켓메이커 롱감마 · 변동성 축소, 레인지바운드 예상"
            gex_regime = "positive"
        else:
            gex_signal = "음수 GEX → 마켓메이커 숏감마 · 변동성 확대, 추세 지속 예상"
            gex_regime = "negative"

        call_wall = max(near_calls, key=lambda x: x["openInterest"]) if near_calls else None
        put_wall  = max(near_puts,  key=lambda x: x["openInterest"]) if near_puts  else None

        # ── 종합 판단 ──
        bullish = 0
        bearish = 0
        reasons = []
        if pcr_vol is not None:
            if pcr_vol > 1.0:
                bullish += 2
                reasons.append(f"PCR {pcr_vol} (공포 → 역발상 매수)")
            elif pcr_vol < 0.7:
                bearish += 2
                reasons.append(f"PCR {pcr_vol} (낙관 → 과매수 경고)")
            else:
                reasons.append(f"PCR {pcr_vol} (중립)")
        if mp_diff > 1:
            bullish += 1
            reasons.append(f"맥스페인 ${max_pain_strike} → 상방 수렴")
        elif mp_diff < -1:
            bearish += 1
            reasons.append(f"맥스페인 ${max_pain_strike} → 하방 수렴")
        if gex_regime == "positive":
            reasons.append("양수 GEX → 안정적 레인지")
        else:
            reasons.append("음수 GEX → 변동성 확대 주의")
            bearish += 1

        if bullish > bearish:
            overall = "강세"; emoji = "🟢"
        elif bearish > bullish:
            overall = "약세"; emoji = "🔴"
        else:
            overall = "중립"; emoji = "🟡"

        return {
            "symbol":     symbol,
            "spot_price": round(spot, 2),
            "expiry":     target_exps[0],
            "expiry_count": len(target_exps),
            "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
            "pcr": {
                "volume":         pcr_vol,
                "open_interest":  pcr_oi,
                "total_put_vol":  tot_pv,
                "total_call_vol": tot_cv,
                "total_put_oi":   tot_pi,
                "total_call_oi":  tot_ci,
                "signal":         pcr_signal,
            },
            "max_pain": {
                "strike":   max_pain_strike,
                "diff_pct": mp_diff,
                "signal":   mp_signal,
                "pain_by_strike": sorted(pain_by_strike, key=lambda x: x["strike"])[:60],
            },
            "gex": {
                "total":     int(round(total_gex)),
                "regime":    gex_regime,
                "signal":    gex_signal,
                "by_strike": gex_by_strike,
                "call_wall": {"strike": call_wall["strike"], "oi": call_wall["openInterest"]} if call_wall else None,
                "put_wall":  {"strike": put_wall["strike"],  "oi": put_wall["openInterest"]}  if put_wall  else None,
            },
            "overall": {
                "direction":      overall,
                "emoji":          emoji,
                "bullish_score":  bullish,
                "bearish_score":  bearish,
                "reasons":        reasons,
            },
        }
    except Exception as exc:
        log.debug("options_signal %s fail: %s", symbol, exc)
        return {"error": str(exc)}


@app.route("/api/options_signal")
def api_options_signal():
    symbol = (request.args.get("symbol") or "SPY").upper()
    if symbol not in ("SPY", "QQQ", "IWM", "DIA"):
        return jsonify({"error": "지원 심볼: SPY/QQQ/IWM/DIA"}), 400
    cache_file = BASE_DIR / "cache" / f"options_signal_{symbol}.json"
    cached = _read_fresh_json(cache_file, 30)
    if cached:
        return jsonify(cached)
    result = _compute_options_signal(symbol)
    if "error" not in result:
        try:
            cache_file.parent.mkdir(exist_ok=True)
            cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    return jsonify(result)


# ─────────────────────────────────────────────────────────────────────────
# PHASE 23 — 텔레그램 알림 / ETF 히트맵 / 배당 스크리너
# ─────────────────────────────────────────────────────────────────────────
def send_telegram(message: str, parse_mode: str = "HTML") -> bool:
    """텔레그램 메시지 전송. 토큰 미설정 시 silently skip."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    try:
        import requests as _rq
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        r = _rq.post(url, json={
            "chat_id": chat_id, "text": message,
            "parse_mode": parse_mode, "disable_web_page_preview": True,
        }, timeout=10)
        if r.status_code != 200:
            log.warning("[텔레그램] send failed: %s %s", r.status_code, r.text[:200])
            return False
        return True
    except Exception as exc:
        log.warning("[텔레그램] error: %s", exc)
        return False


@app.route("/api/volume_profile/<code>")
def api_volume_profile(code: str):
    """거래량 프로파일 (POC + Value Area) + VWAP + 저항/지지선. 4시간 캐시."""
    market = (request.args.get("market") or "kr").lower()
    cache_file = BASE_DIR / "cache" / f"vp_{market}_{code}.json"
    cached = _read_fresh_json(cache_file, 240)
    if cached:
        return jsonify(cached)

    # 차트 데이터 로드 (in-process)
    chart_url = f"/api/us/chart/{code}" if market == "us" else f"/api/chart/{code}"
    chart = _call_api_internal(chart_url) or {}
    if chart.get("error") or not chart.get("close"):
        return jsonify({"error": chart.get("error") or "차트 데이터 없음"}), 404

    highs  = chart.get("high")  or []
    lows   = chart.get("low")   or []
    closes = chart.get("close") or []
    volumes = chart.get("volume") or []
    n = len(closes)
    if n < 20:
        return jsonify({"error": "데이터 부족"}), 404

    # 거래량 프로파일 (30 bins)
    price_max = max(highs)
    price_min = min(lows)
    num_bins = 30
    bin_size = (price_max - price_min) / num_bins if price_max > price_min else 1
    bins: list[dict] = []
    for bi in range(num_bins):
        bl = price_min + bi * bin_size
        bh = bl + bin_size
        vol_in = 0.0
        for i in range(n):
            if lows[i] <= bh and highs[i] >= bl:
                overlap = min(highs[i], bh) - max(lows[i], bl)
                bar_range = highs[i] - lows[i]
                if bar_range > 0:
                    vol_in += volumes[i] * (overlap / bar_range)
        bins.append({
            "price_mid": round((bl + bh) / 2, 2),
            "volume":    int(round(vol_in)),
        })

    # POC
    poc_bin = max(bins, key=lambda b: b["volume"])
    poc = poc_bin["price_mid"]

    # Value Area (70%)
    sorted_bins = sorted(bins, key=lambda b: b["volume"], reverse=True)
    total_vol = sum(b["volume"] for b in bins)
    target = total_vol * 0.7
    acc = 0
    va_prices = []
    for b in sorted_bins:
        acc += b["volume"]
        va_prices.append(b["price_mid"])
        if acc >= target:
            break
    va_high = max(va_prices) + bin_size / 2 if va_prices else price_max
    va_low  = min(va_prices) - bin_size / 2 if va_prices else price_min

    # VWAP (최근 60봉)
    vwap_list = []
    cum_tpv = 0.0
    cum_vol = 0.0
    start = max(0, n - 60)
    for i in range(start, n):
        tp = (highs[i] + lows[i] + closes[i]) / 3
        cum_tpv += tp * volumes[i]
        cum_vol += volumes[i]
        vwap_list.append(round(cum_tpv / cum_vol, 2) if cum_vol else 0)

    # 저항/지지 (클러스터링)
    extremes = sorted(highs[-60:] + lows[-60:])
    clusters: list[dict] = []
    if extremes:
        cur_cluster = [extremes[0]]
        thresh = 0.02
        for p in extremes[1:]:
            if (p - cur_cluster[-1]) / cur_cluster[-1] < thresh:
                cur_cluster.append(p)
            else:
                if len(cur_cluster) >= 3:
                    clusters.append({
                        "price": round(sum(cur_cluster) / len(cur_cluster), 2),
                        "touches": len(cur_cluster),
                    })
                cur_cluster = [p]
        if len(cur_cluster) >= 3:
            clusters.append({
                "price": round(sum(cur_cluster) / len(cur_cluster), 2),
                "touches": len(cur_cluster),
            })

    cur_price = closes[-1]
    resistance = sorted(
        [c for c in clusters if c["price"] > cur_price],
        key=lambda c: c["price"],
    )[:3]
    support = sorted(
        [c for c in clusters if c["price"] < cur_price],
        key=lambda c: -c["price"],
    )[:3]

    result = {
        "code":       code,
        "market":     market,
        "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "volume_profile": {
            "bins": bins,
            "poc":     round(poc, 2),
            "va_high": round(va_high, 2),
            "va_low":  round(va_low, 2),
        },
        "vwap_current":   vwap_list[-1] if vwap_list else None,
        "vwap_series":    vwap_list,
        "resistance":     resistance,
        "support":        support,
    }
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


@app.route("/api/portfolio/sync", methods=["POST"])
def api_portfolio_sync():
    """프론트 포트폴리오 → 서버 파일 동기화 (트레일링 스톱 체크용)."""
    try:
        data = request.get_json(force=True, silent=True) or {}
    except Exception:
        data = {}
    positions = data.get("positions") or []
    out = BASE_DIR / "cache" / "server_portfolio.json"
    try:
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps({"positions": positions}, ensure_ascii=False),
                        encoding="utf-8")
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"status": "ok", "count": len(positions)})


def _check_trailing_stops():
    """포트폴리오 포지션의 트레일링 스톱 갱신 + 트리거 체크. 장중 30분."""
    pf_file = BASE_DIR / "cache" / "server_portfolio.json"
    if not pf_file.exists():
        return
    try:
        pf = json.loads(pf_file.read_text(encoding="utf-8"))
    except Exception:
        return
    positions = pf.get("positions") or []
    uni = _load_naver_universe()
    stocks = (uni or {}).get("stocks") or {}
    updated = False
    today_str = now_kst().strftime("%Y-%m-%d")

    for pos in positions:
        tr = pos.get("trailing") or {}
        if not tr.get("enabled"):
            continue
        code = pos.get("code", "")
        market = pos.get("market", "kr")
        buy_price = pos.get("buy_price") or 0

        # 현재가
        if market == "kr":
            st = stocks.get(code) or {}
            cur_price = st.get("close") or 0
        else:
            us_data = _fetch_us_market_data()
            sym_map = {s["symbol"]: s for s in (us_data.get("all_stocks") or [])}
            cur_price = (sym_map.get(code) or {}).get("price") or 0
        if not cur_price:
            continue

        highest = max(tr.get("highest_since_entry") or buy_price, cur_price)
        old_stop = tr.get("current_stop") or (buy_price * 0.97)

        # 새 손절가 계산
        trail_type = tr.get("type", "fixed_pct")
        if trail_type == "fixed_pct":
            pct = tr.get("fixed_pct") or 5
            new_stop = highest * (1 - pct / 100)
        elif trail_type == "atr":
            # ATR 근사: 최근 change_pct 절대값의 평균 × buy_price
            # 정확한 ATR은 chart OHLCV가 필요하지만 비용이 큼. 단순 근사.
            mult = tr.get("atr_multiplier") or 2
            new_stop = highest - (buy_price * 0.02 * mult)  # 2% × mult 근사
        else:
            new_stop = highest * 0.95  # chandelier 근사

        new_stop = max(new_stop, buy_price * 0.93)  # 최대 -7% 손절
        new_stop = max(new_stop, old_stop)           # 절대 내려가지 않음
        new_stop = round(new_stop, 2)

        if new_stop != old_stop:
            tr["current_stop"] = new_stop
            tr["highest_since_entry"] = round(highest, 2)
            hist = tr.setdefault("stop_history", [])
            if not hist or not hist[-1].get("date", "").startswith(today_str):
                hist.append({"date": today_str, "stop": new_stop, "price": cur_price})
                if len(hist) > 30:
                    tr["stop_history"] = hist[-30:]
            updated = True

        # 트리거 체크
        if cur_price <= new_stop and tr.get("alert_on_trigger", True):
            last_alert = tr.get("last_alert_date", "")
            if not last_alert.startswith(today_str):
                flag = "🇺🇸" if market == "us" else "🇰🇷"
                cur_sym = "$" if market == "us" else "₩"
                pnl = (cur_price - buy_price) * (pos.get("quantity") or 0)
                pnl_pct = ((cur_price / buy_price - 1) * 100) if buy_price else 0
                sign = "+" if pnl >= 0 else ""
                msg = (
                    f"🛑 <b>트레일링 스톱 도달</b>\n"
                    f"{flag} <b>{pos.get('name', code)}</b> ({code})\n\n"
                    f"현재가: {cur_sym}{cur_price:,.0f}\n"
                    f"손절가: {cur_sym}{new_stop:,.0f}\n"
                    f"매수가: {cur_sym}{buy_price:,.0f}\n"
                    f"예상 손익: {sign}{cur_sym}{pnl:,.0f} ({sign}{pnl_pct:.2f}%)\n\n"
                    f"⚠️ 자동 청산 없음 — 수동 매도 필요"
                )
                send_telegram(msg)
                tr["last_alert_date"] = now_kst().strftime("%Y-%m-%dT%H:%M:%S")
                updated = True

    if updated:
        try:
            pf_file.write_text(json.dumps(pf, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass


@app.route("/api/alerts/sync", methods=["POST"])
def api_alerts_sync():
    """프론트 알림 규칙 → 서버 파일 동기화."""
    try:
        data = request.get_json(force=True, silent=True) or {}
    except Exception:
        data = {}
    rules = data.get("rules") or []
    out = BASE_DIR / "cache" / "alert_rules.json"
    try:
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps({"rules": rules}, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"status": "ok", "count": len(rules)})


@app.route("/api/alerts/list")
def api_alerts_list():
    f = BASE_DIR / "cache" / "alert_rules.json"
    if not f.exists():
        return jsonify({"rules": []})
    try:
        return Response(f.read_text(encoding="utf-8"),
                        content_type="application/json; charset=utf-8")
    except Exception:
        return jsonify({"rules": []})


def _format_rule_label(rtype: str, value) -> str:
    m = {
        "price_above":    f"₩{int(value or 0):,} 이상",
        "price_below":    f"₩{int(value or 0):,} 이하",
        "change_up":      f"당일 +{value}% 이상",
        "change_down":    f"당일 {value}% 이하",
        "rsi_oversold":   f"RSI {value or 30} 이하",
        "rsi_overbought": f"RSI {value or 70} 이상",
        "macd_golden":    "MACD 골든크로스",
        "macd_dead":      "MACD 데드크로스",
        "bb_upper":       "볼밴 상단 터치",
        "bb_lower":       "볼밴 하단 터치",
        "volume_spike":   f"거래량 {value or 2}배 이상",
        "foreign_strong_buy": f"외국인 {int(value or 3)}일 연속 순매수",
        "foreign_cum_buy":    f"외국인 5일 누적 {int(value or 100)}억 이상",
    }
    return m.get(rtype, rtype)


def check_alert_rules():
    """활성 알림 규칙 체크 — 10분 간격 스케줄러 호출."""
    f = BASE_DIR / "cache" / "alert_rules.json"
    if not f.exists():
        return
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return
    rules = data.get("rules") or []
    if not rules:
        return

    today_str = now_kst().strftime("%Y-%m-%d")
    uni = _load_naver_universe()
    stocks = (uni or {}).get("stocks") or {}
    updated = False

    for rule in rules:
        if not rule.get("enabled", True):
            continue
        if (rule.get("triggered_at") or "").startswith(today_str):
            continue

        code = rule.get("code", "")
        market = rule.get("market", "kr")
        rtype = rule.get("type", "")
        value = rule.get("value")

        triggered = False
        detail = ""

        try:
            if market == "kr":
                st = stocks.get(code) or {}
                price = st.get("close") or 0
                chg = st.get("change_pct") or 0
            else:
                us_data = _fetch_us_market_data()
                sym_map = {s["symbol"]: s for s in (us_data.get("all_stocks") or [])}
                st = sym_map.get(code, {})
                price = st.get("price") or 0
                chg = st.get("change_pct") or 0

            if not price:
                continue
            cur = "$" if market == "us" else "₩"

            if rtype == "price_above" and value and price >= value:
                triggered = True
                detail = f"현재가 {cur}{price:,}"
            elif rtype == "price_below" and value and price <= value:
                triggered = True
                detail = f"현재가 {cur}{price:,}"
            elif rtype == "change_up" and value and chg >= value:
                triggered = True
                detail = f"당일 +{chg}%"
            elif rtype == "change_down" and value and chg <= value:
                triggered = True
                detail = f"당일 {chg}%"
            elif rtype in ("rsi_oversold", "rsi_overbought", "macd_golden", "macd_dead"):
                # 차트 캐시에서 RSI/MACD 읽기
                chart_data = None
                if market == "kr":
                    chart_data = _call_api_internal(f"/api/chart/{code}") or {}
                else:
                    chart_data = _call_api_internal(f"/api/us/chart/{code}") or {}
                rm = chart_data.get("rsi_macd") if isinstance(chart_data, dict) else None
                if rm:
                    rsi_vals = rm.get("rsi") or []
                    rsi_cur = rsi_vals[-1] if rsi_vals else 50
                    if rtype == "rsi_oversold" and rsi_cur <= (value or 30):
                        triggered = True
                        detail = f"RSI {rsi_cur}"
                    elif rtype == "rsi_overbought" and rsi_cur >= (value or 70):
                        triggered = True
                        detail = f"RSI {rsi_cur}"
                    macd_v = rm.get("macd") or []
                    macd_s = rm.get("macd_signal") or []
                    if len(macd_v) >= 2 and len(macd_s) >= 2:
                        if rtype == "macd_golden" and macd_v[-2] <= macd_s[-2] and macd_v[-1] > macd_s[-1]:
                            triggered = True
                            detail = "MACD 골든크로스"
                        elif rtype == "macd_dead" and macd_v[-2] >= macd_s[-2] and macd_v[-1] < macd_s[-1]:
                            triggered = True
                            detail = "MACD 데드크로스"
            elif rtype in ("foreign_strong_buy", "foreign_cum_buy") and market == "kr":
                flow_data = _call_api_internal(f"/api/flow/{code}") or {}
                fv = (flow_data.get("foreign_value") or []) if not flow_data.get("error") else []
                if fv:
                    if rtype == "foreign_strong_buy":
                        streak = 0
                        for v in reversed(fv):
                            if v > 0: streak += 1
                            else: break
                        threshold = int(value or 3)
                        if streak >= threshold:
                            triggered = True
                            detail = f"외국인 {streak}일 연속 순매수"
                    elif rtype == "foreign_cum_buy":
                        cum5_eok = sum(fv[-5:]) / 1e8
                        threshold = float(value or 100)
                        if cum5_eok >= threshold:
                            triggered = True
                            detail = f"외국인 5일 누적 {cum5_eok:,.0f}억원"
        except Exception as exc:
            log.debug("alert eval %s %s: %s", code, rtype, exc)
            continue

        if triggered:
            flag = "🇺🇸" if market == "us" else "🇰🇷"
            msg = (f"🔔 <b>알림 트리거</b>\n"
                   f"{flag} <b>{rule.get('name', code)}</b> ({code})\n"
                   f"조건: {_format_rule_label(rtype, value)}\n"
                   f"{detail}")
            if rule.get("message"):
                msg += f"\n메모: {rule['message']}"
            send_telegram(msg)
            rule["triggered_at"] = now_kst().strftime("%Y-%m-%dT%H:%M:%S")
            updated = True

    if updated:
        try:
            f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass


@app.route("/api/telegram/test", methods=["POST"])
def api_telegram_test():
    """테스트 메시지 전송."""
    ok = send_telegram(
        "🔔 <b>테스트</b>\n"
        "stock-dashboard 텔레그램 연동 성공!\n"
        f"시각: {now_kst().strftime('%Y-%m-%d %H:%M:%S')} KST"
    )
    return jsonify({"ok": ok})


@app.route("/api/telegram/briefing_test", methods=["POST"])
def api_briefing_test():
    """새벽 브리핑 수동 테스트. 데이터 갱신 후 브리핑 발송."""
    try:
        _refresh_briefing_data()
        alert_overnight_prediction()
        return jsonify({"ok": True, "message": "브리핑 발송 완료"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/watchlist/sync", methods=["POST"])
def api_watchlist_sync():
    """프론트 localStorage 관심종목 → 서버 파일 동기화 (알림용)."""
    try:
        data = request.get_json(force=True, silent=True) or {}
    except Exception:
        data = {}
    items = data.get("items") or []
    out = BASE_DIR / "cache" / "server_watchlist.json"
    try:
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        return jsonify({"status": "error", "error": str(exc)}), 500
    return jsonify({"status": "ok", "count": len(items)})


def _load_server_watchlist() -> list:
    f = BASE_DIR / "cache" / "server_watchlist.json"
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text(encoding="utf-8")) or []
    except Exception:
        return []


# ── 알림 1: 장 시작 전 브리핑 (평일 08:30) ──
def alert_morning_briefing():
    try:
        today_kst = now_kst().strftime("%Y-%m-%d")
        lines = [f"📊 <b>오늘의 시황 브리핑</b> ({today_kst})", ""]

        # 매크로: USD/KRW, WTI, VIX
        macro = _read_fresh_json(BASE_DIR / "cache" / "macro_data.json", 24 * 60) or {}
        for it in macro.get("items") or []:
            if it["name"] in ("USD/KRW", "WTI 원유", "VIX", "미국 10년물"):
                arrow = "🔴" if it["change_pct"] >= 0 else "🟢"
                sign  = "+" if it["change_pct"] >= 0 else ""
                lines.append(f"{arrow} {it['name']}: {it['value']} ({sign}{it['change_pct']}%)")
        lines.append("")

        # 오늘 경제 일정 (high impact)
        import glob as _glob
        econ_files = sorted(_glob.glob(str(BASE_DIR / "cache" / "economic_calendar_*.json")), reverse=True)
        high_events = []
        if econ_files:
            try:
                econ = json.loads(open(econ_files[0], encoding="utf-8").read())
                for e in econ.get("events") or []:
                    if e.get("date") == today_kst and e.get("impact") == "high":
                        high_events.append(e)
            except Exception:
                pass
        if high_events:
            lines.append("📅 <b>오늘 주요 일정</b>")
            for e in high_events[:5]:
                lines.append(f"  🔴 {e.get('time') or '—'} {e.get('country_kr','')} {e.get('event_kr') or e.get('event') or ''}")
        else:
            lines.append("📅 오늘 high impact 지표 없음")
        send_telegram("\n".join(lines))
    except Exception as exc:
        log.warning("alert_morning_briefing: %s", exc)


# ── 알림 2: 종목 발굴 Stage 2 신규 진입 ──
def _alert_cooldown_ok(code: str, alert_type: str, cooldown_min: int = 60) -> bool:
    """쿨다운 체크: 최근 N분 내 같은 종목+타입 알림이 없으면 True."""
    if not (_SQLITE_OK and USE_SQLITE):
        return True
    try:
        with _get_db() as conn:
            row = conn.execute(
                "SELECT alerted_at FROM alert_history "
                "WHERE code=? AND alert_type=? ORDER BY alerted_at DESC LIMIT 1",
                (code, alert_type),
            ).fetchone()
            if not row:
                return True
            from datetime import datetime as _dt
            last = _dt.fromisoformat(row[0])
            elapsed = (now_kst().replace(tzinfo=None) - last).total_seconds() / 60
            return elapsed >= cooldown_min
    except Exception:
        return True


def _record_alert(code: str, alert_type: str, detail: str = ""):
    """알림 발송 이력 기록."""
    if not (_SQLITE_OK and USE_SQLITE):
        return
    try:
        with _get_db() as conn:
            conn.execute(
                "INSERT INTO alert_history (code, alert_type, detail, alerted_at) "
                "VALUES (?, ?, ?, ?)",
                (code, alert_type, detail, now_kst().strftime("%Y-%m-%d %H:%M:%S")),
            )
            # 7일 이전 이력 정리
            conn.execute(
                "DELETE FROM alert_history WHERE alerted_at < datetime('now', '-7 days')"
            )
            conn.commit()
    except Exception:
        pass


def alert_discovery_new_entries(market: str = "kr"):
    """Stage 2 완료 후 호출: 신규 진입 + 스코어 급상승 감지 → 텔레그램."""
    try:
        cur_file = BASE_DIR / "cache" / f"discover_{market}_stage2.json"
        prev_file = BASE_DIR / "cache" / f"discover_{market}_stage2_prev.json"
        if not cur_file.exists():
            return
        cur = json.loads(cur_file.read_text(encoding="utf-8"))
        cur_items = cur.get("items") or []
        cur_top = cur_items[:20]
        cur_set = {it["code"] for it in cur_top}

        # 이전 결과 로드
        prev_items = []
        if prev_file.exists():
            try:
                prev = json.loads(prev_file.read_text(encoding="utf-8"))
                prev_items = prev.get("items") or []
            except Exception:
                pass
        prev_top_set = {it["code"] for it in prev_items[:20]}
        prev_score_map = {it["code"]: it.get("total_score", 0) for it in prev_items}

        flag = "🇺🇸" if market == "us" else "🇰🇷"
        cur_symbol = "$" if market == "us" else "₩"
        msgs_sent = 0

        # ── 1. 신규 진입 TOP 20 ──
        new_codes = cur_set - prev_top_set
        if new_codes:
            alert_entries = []
            for it in cur_top:
                if it["code"] not in new_codes:
                    continue
                if not _alert_cooldown_ok(it["code"], "new_entry", 60):
                    continue
                alert_entries.append(it)

            if alert_entries:
                lines = [f"🔬 <b>{flag} 종목 발굴 TOP20 신규 진입</b>", ""]
                for it in alert_entries[:5]:
                    chg = it.get("change_pct") or 0
                    sign = "+" if chg >= 0 else ""
                    price = it.get("price") or 0
                    lines.append(
                        f"<b>{it['name']}</b> ({it['code']}) · "
                        f"{it['total_score']}점 · {cur_symbol}{price:,.0f} ({sign}{chg}%)"
                    )
                    s = it.get("scores") or {}
                    lines.append(
                        f"   mom={s.get('momentum',0)} flow={s.get('flow',0)} "
                        f"val={s.get('valuation',0)} tech={s.get('technical',0)} "
                        f"sect={s.get('sector',0)}"
                    )
                    # 태그 상위 3개
                    tags = (it.get("details") or {}).get("rsi_macd_tags") or []
                    if tags:
                        lines.append(f"   🏷 {', '.join(tags[:3])}")
                    _record_alert(it["code"], "new_entry", f"top20 진입 {it['total_score']}점")
                lines.append(f"\n⏰ {now_kst().strftime('%H:%M')}")
                send_telegram("\n".join(lines))
                msgs_sent += 1

        # ── 2. 스코어 급상승 (±15점 이상) ──
        if prev_score_map:
            surges = []
            for it in cur_items[:100]:  # 상위 100종목만 체크
                prev_sc = prev_score_map.get(it["code"])
                if prev_sc is None:
                    continue
                diff = it.get("total_score", 0) - prev_sc
                if diff >= 15 and _alert_cooldown_ok(it["code"], "score_surge", 60):
                    surges.append((it, diff, prev_sc))

            if surges:
                surges.sort(key=lambda x: -x[1])
                lines = [f"📈 <b>{flag} 스코어 급상승</b>", ""]
                for it, diff, prev_sc in surges[:5]:
                    chg = it.get("change_pct") or 0
                    sign = "+" if chg >= 0 else ""
                    lines.append(
                        f"<b>{it['name']}</b> ({it['code']}) "
                        f"{prev_sc} → {it['total_score']} (<b>+{diff}</b>)"
                    )
                    lines.append(f"   {cur_symbol}{it.get('price',0):,.0f} ({sign}{chg}%)")
                    tags = (it.get("details") or {}).get("rsi_macd_tags") or []
                    if tags:
                        lines.append(f"   🏷 {', '.join(tags[:3])}")
                    _record_alert(it["code"], "score_surge", f"+{diff}점 ({prev_sc}→{it['total_score']})")
                lines.append(f"\n⏰ {now_kst().strftime('%H:%M')}")
                send_telegram("\n".join(lines))
                msgs_sent += 1

        # prev 저장 (다음 비교용)
        import shutil
        shutil.copy2(cur_file, prev_file)

        if msgs_sent:
            log.info("[발굴 알림] %s: 신규진입 %d + 급상승 %d → 메시지 %d건",
                     market, len(new_codes), len(surges) if prev_score_map else 0, msgs_sent)
    except Exception as exc:
        log.warning("alert_discovery_new_entries: %s", exc)


# ── 알림 3: 관심종목 급등/급락 (±5%) ──
def alert_watchlist_price():
    try:
        watchlist = _load_server_watchlist()
        if not watchlist:
            return
        uni = _load_naver_universe()
        stocks = (uni or {}).get("stocks") or {}
        alerts = []
        for item in watchlist:
            code = item.get("code") or ""
            if (item.get("market") or "kr") != "kr":
                continue
            st = stocks.get(code)
            if not st:
                continue
            chg = st.get("change_pct") or 0
            if abs(chg) >= 5:
                alerts.append({
                    "code": code, "name": st.get("name", code),
                    "price": st.get("close", 0), "chg": chg,
                })
        if alerts:
            lines = ["⚠️ <b>관심종목 급등/급락</b>", ""]
            for a in alerts:
                emoji = "🔴" if a["chg"] >= 0 else "🟢"
                sign = "+" if a["chg"] >= 0 else ""
                lines.append(f"{emoji} <b>{a['name']}</b> ({a['code']})")
                lines.append(f"   ₩{a['price']:,} ({sign}{a['chg']}%)")
            send_telegram("\n".join(lines))
    except Exception as exc:
        log.warning("alert_watchlist_price: %s", exc)


# ── 알림 4: 관심종목 신규 리포트 ──
def alert_new_reports():
    try:
        watchlist = _load_server_watchlist()
        if not watchlist:
            return
        wl_codes = set(it.get("code") for it in watchlist if it.get("code"))
        reports_file = BASE_DIR / "cache" / "company_reports.json"
        if not reports_file.exists():
            return
        data = json.loads(reports_file.read_text(encoding="utf-8"))
        today_prefix = now_kst().strftime("%y.%m")  # 26.04
        matched = []
        for r in data.get("reports") or []:
            if r.get("stock_code") in wl_codes:
                d = r.get("date") or ""
                if d.startswith(today_prefix):
                    matched.append(r)
        if matched:
            lines = ["📑 <b>관심종목 신규 리포트</b>", ""]
            for r in matched[:5]:
                lines.append(f"• <b>{r.get('stock_name')}</b> — {r.get('title','')}")
                lines.append(f"   {r.get('broker','')} · {r.get('date','')}")
            send_telegram("\n".join(lines))
    except Exception as exc:
        log.warning("alert_new_reports: %s", exc)


# ── 알림 6: 새벽 5:30 익일 시장 예측 (미국 옵션 + 야간선물 + 매크로) ──
def alert_overnight_prediction():
    """
    새벽 5:30 (미국장 마감 직후) 종합 예측 브리핑.
    SPY/QQQ 옵션 시그널 + 코스피200 야간선물 + 매크로 + 오늘 주요 일정.
    """
    try:
        lines = [f"🔮 <b>익일 시장 예측 브리핑</b>",
                 f"{now_kst().strftime('%Y-%m-%d %H:%M')} KST", ""]

        # SPY/QQQ 옵션 시그널 (캐시 우선, 없으면 재계산)
        for sym in ("SPY", "QQQ"):
            try:
                cache_file = BASE_DIR / "cache" / f"options_signal_{sym}.json"
                data = _read_fresh_json(cache_file, 180)   # 3h 허용
                if not data:
                    data = _compute_options_signal(sym)
                if not data or "error" in data:
                    continue
                flag = "🇺🇸"
                o = data.get("overall") or {}
                p = data.get("pcr") or {}
                m = data.get("max_pain") or {}
                g = data.get("gex") or {}
                lines.append(f"{flag} <b>{sym}</b> ${data.get('spot_price')} · {o.get('emoji','🟡')} {o.get('direction','중립')}")
                lines.append(f"  PCR {p.get('volume','—')} · MaxPain ${m.get('strike','—')} ({m.get('diff_pct',0):+.1f}%) · GEX {g.get('regime','—')}")
                cw = g.get("call_wall") or {}
                pw = g.get("put_wall") or {}
                if cw.get("strike") or pw.get("strike"):
                    lines.append(f"  저항 ${cw.get('strike','—')} / 지지 ${pw.get('strike','—')}")
            except Exception as exc:
                log.debug("overnight SPY/QQQ %s: %s", sym, exc)
        lines.append("")

        # 코스피200 야간선물
        try:
            nf = _read_fresh_json(BASE_DIR / "cache" / "night_futures.json", 180)
            if not nf:
                nf = _fetch_night_futures()
            if nf and nf.get("night_close"):
                sign = "+" if (nf.get("change_pct") or 0) >= 0 else ""
                lines.append(f"🇰🇷 <b>코스피200 (^KS200)</b>")
                lines.append(f"  종가 {nf['night_close']} ({sign}{nf.get('change_pct','—')}%)")
                if nf.get("signal"):
                    lines.append(f"  {nf['signal']}")
                lines.append("")
        except Exception as exc:
            log.debug("overnight kr futures: %s", exc)

        # 매크로 핵심 4개
        try:
            macro = _read_fresh_json(BASE_DIR / "cache" / "macro_data.json", 6 * 60)
            if macro and macro.get("items"):
                lines.append("📊 <b>주요 지표</b>")
                for it in macro["items"]:
                    if it["name"] in ("USD/KRW", "WTI 원유", "VIX", "미국 10년물", "BTC"):
                        sign = "+" if it["change_pct"] >= 0 else ""
                        lines.append(f"  {it['name']}: {it['value']} ({sign}{it['change_pct']}%)")
                lines.append("")
        except Exception as exc:
            log.debug("overnight macro: %s", exc)

        # 오늘 high impact 경제 일정
        try:
            today_kst = now_kst().strftime("%Y-%m-%d")
            import glob as _glob
            econ_files = sorted(
                _glob.glob(str(BASE_DIR / "cache" / "economic_calendar_*.json")),
                reverse=True,
            )
            if econ_files:
                econ = json.loads(open(econ_files[0], encoding="utf-8").read())
                today_evs = [
                    e for e in (econ.get("events") or [])
                    if e.get("date") == today_kst and e.get("impact") == "high"
                ]
                if today_evs:
                    lines.append("📅 <b>오늘 주요 일정</b>")
                    for e in today_evs[:5]:
                        lines.append(
                            f"  🔴 {e.get('time') or '—'} "
                            f"{e.get('country_kr','')} "
                            f"{e.get('event_kr') or e.get('event','')}"
                        )
        except Exception as exc:
            log.debug("overnight calendar: %s", exc)

        send_telegram("\n".join(lines))
    except Exception as exc:
        log.warning("alert_overnight_prediction: %s", exc)


def _refresh_briefing_data():
    """새벽 5:00 — 브리핑 30분 전 옵션/야간선물/매크로 캐시 강제 갱신."""
    for sym in ("SPY", "QQQ"):
        f = BASE_DIR / "cache" / f"options_signal_{sym}.json"
        try:
            if f.exists():
                f.unlink()
        except Exception:
            pass
        try:
            _call_api_internal(f"/api/options_signal?symbol={sym}")
        except Exception as exc:
            log.debug("briefing refresh %s: %s", sym, exc)
    try:
        _call_api_internal("/api/night_futures")
    except Exception:
        pass
    try:
        _call_api_internal("/api/macro")
    except Exception:
        pass
    log.info("[새벽 브리핑] 데이터 갱신 완료")


def refresh_us_options_signal():
    """미국장 시작 전(KST 22:00) 옵션 시그널 캐시 무효화 — 다음 조회 시 재수집."""
    for sym in ("SPY", "QQQ"):
        f = BASE_DIR / "cache" / f"options_signal_{sym}.json"
        try:
            if f.exists():
                f.unlink()
                log.info("[옵션] %s 캐시 삭제", sym)
        except Exception as exc:
            log.debug("refresh_us_options %s: %s", sym, exc)


# ── 알림 5: 장 마감 요약 (평일 15:40) ──
def alert_closing_summary():
    try:
        lines = ["🔔 <b>장 마감 요약</b>", ""]

        # KOSPI/KOSDAQ
        if DATA_JSON.exists():
            try:
                d = json.loads(DATA_JSON.read_text(encoding="utf-8"))
                for label, key in (("KOSPI", "kospi"), ("KOSDAQ", "kosdaq")):
                    idx = d.get(key) or {}
                    val = idx.get("value")
                    chg = idx.get("change_pct")
                    if val is not None:
                        sign = "+" if (chg or 0) >= 0 else ""
                        lines.append(f"{label} {val:,.2f} ({sign}{chg}%)")
            except Exception:
                pass
        lines.append("")

        # 종목 발굴 TOP5 (KR stage2)
        ds = BASE_DIR / "cache" / "discover_kr_stage2.json"
        if ds.exists():
            try:
                dd = json.loads(ds.read_text(encoding="utf-8"))
                lines.append("🔬 <b>종목 발굴 TOP5</b>")
                for it in (dd.get("items") or [])[:5]:
                    chg = it.get("change_pct") or 0
                    sign = "+" if chg >= 0 else ""
                    lines.append(f"  🇰🇷 {it['name']} {it['total_score']}점 ({sign}{chg}%)")
            except Exception:
                pass
        send_telegram("\n".join(lines))
    except Exception as exc:
        log.warning("alert_closing_summary: %s", exc)


# ─────────────────────────────────────────────────────────────────────────
# PHASE 23 — ETF 히트맵
# ─────────────────────────────────────────────────────────────────────────
_ETF_THEMES = {
    "반도체/AI": [
        ("091160", "KODEX 반도체"),
        ("395160", "TIGER AI반도체핵심공정"),
        ("466920", "TIGER 코리아AI전력핵심설비"),
        ("139260", "TIGER 200 IT"),
    ],
    "2차전지": [
        ("305720", "KODEX 2차전지산업"),
        ("364960", "TIGER 2차전지테마"),
    ],
    "바이오/헬스": [
        ("244580", "KODEX 바이오"),
        ("227540", "TIGER 헬스케어"),
    ],
    "방산/조선": [
        ("464510", "TIGER 우주방산"),
    ],
    "레버리지/인버스": [
        ("122630", "KODEX 레버리지"),
        ("114800", "KODEX 인버스"),
        ("252670", "KODEX 200선물인버스2X"),
    ],
    "미국 지수": [
        ("379800", "KODEX 미국S&P500TR"),
        ("381170", "TIGER 미국나스닥100"),
        ("453810", "TIGER 미국필라델피아반도체나스닥"),
    ],
    "배당": [
        ("211900", "KODEX 배당가치"),
        ("161510", "TIGER 배당성장"),
    ],
    "에너지/화학": [
        ("117460", "KODEX 에너지화학"),
    ],
}


def _scrape_etf_price(code: str) -> dict | None:
    """
    네이버 금융 종목 페이지에서 ETF 시세 + 등락률 추출.
    HTML 구조:
      .no_today .blind        → 현재가
      .no_exday em (2개)      → [전일대비 금액, 등락률 숫자(%제외)]
      .no_exday .ico.plus/minus 또는 .no_up/.no_down → 부호
    """
    try:
        import requests as _rq
        from bs4 import BeautifulSoup
        r = _rq.get(
            "https://finance.naver.com/item/main.naver",
            params={"code": code},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=8,
        )
        r.encoding = "euc-kr"
        soup = BeautifulSoup(r.text, "html.parser")

        price = None
        pt = soup.select_one(".no_today .blind")
        if pt:
            try:
                price = int(pt.get_text(strip=True).replace(",", ""))
            except Exception:
                pass

        change_pct = None
        no_exday = soup.select_one(".no_exday")
        if no_exday:
            ems = no_exday.select("em")
            if len(ems) >= 2:
                # 마지막 em = 등락률, 그 안 .blind = 숫자
                rate_em = ems[-1]
                rate_blind = rate_em.select_one(".blind")
                if rate_blind:
                    try:
                        change_pct = float(rate_blind.get_text(strip=True).replace(",", ""))
                    except ValueError:
                        pass
            # 부호 판정: .ico.minus / .ico.plus / no_down / no_up
            if change_pct is not None:
                html = str(no_exday)
                if "ico minus" in html or "no_down" in html:
                    change_pct = -abs(change_pct)
                elif "ico plus" in html or "no_up" in html:
                    change_pct = abs(change_pct)
        return {"code": code, "price": price, "change_pct": change_pct}
    except Exception as exc:
        log.debug("etf scrape %s fail: %s", code, exc)
        return None


@app.route("/api/etf_map")
def api_etf_map():
    """국내 주요 ETF 히트맵 — 테마별 가중평균 등락률. 30분 캐시."""
    today = now_kst().strftime("%Y%m%d")
    cache_file = BASE_DIR / "cache" / f"etf_map_{today}.json"
    cached = _read_fresh_json(cache_file, 30)
    if cached:
        return jsonify(cached)

    # 고유 ETF 코드 집합
    unique_codes = {}
    for theme, etfs in _ETF_THEMES.items():
        for code, name in etfs:
            unique_codes[code] = name

    # 시세 스크랩
    price_map: dict[str, dict] = {}
    for code, name in unique_codes.items():
        p = _scrape_etf_price(code)
        if p:
            p["name"] = name
            price_map[code] = p
        time.sleep(0.12)

    # 테마 조립
    themes = []
    for theme_name, etfs in _ETF_THEMES.items():
        stocks = []
        for code, name in etfs:
            info = price_map.get(code) or {"code": code, "name": name, "price": None, "change_pct": None}
            stocks.append({
                "code":       code,
                "name":       name,
                "price":      info.get("price"),
                "change_pct": info.get("change_pct") or 0,
                "volume_mn":  0,   # 거래대금 크롤링은 생략 (트리맵 가중은 동일 가중 fallback)
            })
        # 단순 평균 등락률
        vals = [s["change_pct"] for s in stocks if s.get("change_pct") is not None]
        avg = round(sum(vals) / len(vals), 2) if vals else 0
        themes.append({
            "name":             theme_name,
            "weighted_avg_pct": avg,
            "stock_count":      len(stocks),
            "stocks":           stocks,
        })
    # 가중평균 절댓값 기준 정렬
    themes.sort(key=lambda t: abs(t.get("weighted_avg_pct") or 0), reverse=True)

    result = {
        "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "theme_count": len(themes),
        "themes": themes,
    }
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


# ─────────────────────────────────────────────────────────────────────────
# PHASE 23 — 배당 스크리너
# ─────────────────────────────────────────────────────────────────────────
_DIV_SEED_CODES = [
    "017670",  # SK텔레콤
    "030200",  # KT
    "032640",  # LG유플러스
    "086790",  # 하나금융지주
    "105560",  # KB금융
    "055550",  # 신한지주
    "316140",  # 우리금융지주
    "000270",  # 기아
    "005490",  # POSCO홀딩스
    "051910",  # LG화학
    "006400",  # 삼성SDI
    "003550",  # LG
    "000810",  # 삼성화재
    "010130",  # 고려아연
    "033780",  # KT&G
    "005380",  # 현대차
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "138930",  # BNK금융지주
    "175330",  # JB금융지주
    "139130",  # DGB금융지주
    "006260",  # LS
    "097950",  # CJ제일제당
    "011780",  # 금호석유
    "010950",  # S-Oil
    "003490",  # 대한항공
    "090430",  # 아모레퍼시픽
    "009540",  # HD한국조선해양
    "024110",  # 기업은행
    "034730",  # SK
]


@app.route("/api/dividend")
def api_dividend():
    """
    배당수익률 상위 종목. 기존 /api/financial 캐시 재사용 우선,
    없으면 네이버 main.naver 스크랩. 일 1회 캐시.
    """
    today = now_kst().strftime("%Y%m%d")
    cache_file = BASE_DIR / "cache" / f"dividend_{today}.json"
    cached = _read_fresh_json(cache_file, 1440)
    if cached:
        return jsonify(cached)

    uni = _load_naver_universe()
    stocks = (uni or {}).get("stocks") or {}
    items: list[dict] = []
    seen = set()

    for code in _DIV_SEED_CODES:
        try:
            fin = _call_api_internal(f"/api/financial/{code}") or {}
            dy = fin.get("dividend_yield")
            if dy is None or dy <= 0:
                continue
            if code in seen:
                continue
            seen.add(code)
            uni_stock = stocks.get(code) or {}
            items.append({
                "code":           code,
                "name":           uni_stock.get("name") or fin.get("name") or code,
                "price":          uni_stock.get("close") or 0,
                "change_pct":     uni_stock.get("change_pct") or 0,
                "dividend_yield": round(dy, 2),
                "per":            fin.get("per"),
                "pbr":            fin.get("pbr"),
                "sector":         (uni_stock.get("sectors") or [None])[0] or "—",
            })
            time.sleep(0.05)
        except Exception as exc:
            log.debug("div fetch %s fail: %s", code, exc)
            continue

    items.sort(key=lambda x: x["dividend_yield"], reverse=True)

    result = {
        "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "count":      len(items),
        "items":      items,
    }
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


def _cleanup_old_cache(max_days: int = 7):
    """서버 시작 시 max_days 이상 된 캐시 JSON 파일 자동 삭제."""
    cache_dir = BASE_DIR / "cache"
    if not cache_dir.exists():
        return
    import glob as _glob
    cutoff = time.time() - max_days * 86400
    removed = 0
    # 날짜가 포함된 일별 캐시만 삭제 (매핑 파일은 보존)
    preserve = {"dart_corp_codes.json", "sp500_tickers.json",
                "sectors_naver_landing.json", "server_watchlist.json"}
    for f in _glob.glob(str(cache_dir / "*.json")):
        fname = Path(f).name
        if fname in preserve:
            continue
        try:
            if os.path.getmtime(f) < cutoff:
                os.remove(f)
                removed += 1
        except Exception:
            pass
    if removed:
        log.info("캐시 클린업: %d개 파일 삭제 (>%d일)", removed, max_days)


# ─────────────────────────────────────────────────────────────────────────
# WebSocket 실시간 가격 시스템
# ─────────────────────────────────────────────────────────────────────────
_ws_subscriptions: dict[str, list[str]] = {}   # {sid: [codes]}
_ws_subscribed_codes: set[str] = set()         # 전체 구독 종목
_ws_last_prices: dict[str, float] = {}         # {code: last_close} 변경 감지용


def _ws_recompute_codes():
    global _ws_subscribed_codes
    codes = set()
    for cl in _ws_subscriptions.values():
        codes.update(cl)
    _ws_subscribed_codes = codes


if _SOCKETIO_OK:
    @socketio.on("connect")
    def _ws_connect():
        sid = request.sid
        _ws_subscriptions[sid] = []
        emit("connected", {"status": "ok", "sid": sid})

    @socketio.on("disconnect")
    def _ws_disconnect():
        sid = request.sid
        for code in _ws_subscriptions.pop(sid, []):
            leave_room(f"s_{code}")
        _ws_recompute_codes()

    @socketio.on("subscribe")
    def _ws_subscribe(data):
        sid = request.sid
        codes = data.get("codes") or []
        if isinstance(codes, str):
            codes = [codes]
        codes = [c for c in codes if c and len(c) <= 10]
        for code in codes:
            join_room(f"s_{code}")
            if code not in _ws_subscriptions.get(sid, []):
                _ws_subscriptions.setdefault(sid, []).append(code)
        _ws_recompute_codes()
        emit("subscribed", {"codes": codes})

    @socketio.on("unsubscribe")
    def _ws_unsubscribe(data):
        sid = request.sid
        codes = data.get("codes") or []
        if isinstance(codes, str):
            codes = [codes]
        for code in codes:
            leave_room(f"s_{code}")
            try:
                _ws_subscriptions.get(sid, []).remove(code)
            except ValueError:
                pass
        _ws_recompute_codes()
        emit("unsubscribed", {"codes": codes})


def _price_broadcaster():
    """2초마다 구독 종목 네이버 실시간 조회 → 변경분만 브로드캐스트."""
    import urllib.request as _ur
    while True:
        try:
            if not _ws_subscribed_codes or not _SOCKETIO_OK:
                time.sleep(3)
                continue
            if not is_market_hours():
                time.sleep(10)
                continue

            codes = list(_ws_subscribed_codes)
            for i in range(0, len(codes), 100):
                batch = codes[i:i + 100]
                try:
                    url = f"https://polling.finance.naver.com/api/realtime/domestic/stock/{','.join(batch)}"
                    req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    with _ur.urlopen(req, timeout=5) as resp:
                        data = json.loads(resp.read().decode("utf-8"))

                    for s in data.get("datas") or []:
                        code = s.get("itemCode")
                        if not code:
                            continue
                        try:
                            close = float(str(s.get("closePrice", "0")).replace(",", ""))
                        except (ValueError, TypeError):
                            continue
                        prev = _ws_last_prices.get(code)
                        if prev == close:
                            continue
                        _ws_last_prices[code] = close
                        payload = {
                            "code": code,
                            "close": close,
                            "change_pct": float(s.get("fluctuationsRatio", 0)),
                            "volume": int(s.get("accumulatedTradingVolume", 0)),
                            "high": float(str(s.get("highPrice", "0")).replace(",", "") or "0"),
                            "low": float(str(s.get("lowPrice", "0")).replace(",", "") or "0"),
                            "ts": int(time.time() * 1000),
                        }
                        socketio.emit("price_update", payload, room=f"s_{code}")
                except Exception as exc:
                    log.debug("[WS] 배치 실패: %s", exc)
                time.sleep(0.1)
            time.sleep(2)
        except Exception as exc:
            log.debug("[WS] broadcaster error: %s", exc)
            time.sleep(5)


def _startup():
    global _startup_done, _scheduler
    if _startup_done:
        return
    _startup_done = True

    _cleanup_old_cache(7)

    # SQLite DB 초기화
    if _SQLITE_OK and USE_SQLITE:
        try:
            _init_db()
            log.info("[SQLite] DB 초기화 완료")
        except Exception as exc:
            log.warning("[SQLite] DB 초기화 실패: %s → JSON 폴백", exc)
        # 추천 이력 테이블 + 과거 discover 스냅샷 소급 (최초 1회)
        try:
            _init_recommendation_history()
            migrate_discover_to_recommendations()
            _init_trade_journal()
        except Exception as exc:
            log.debug("[추천이력] init 실패: %s", exc)

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

        # Phase 23: 텔레그램 알림 스케줄 (토큰 있을 때만)
        if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
            _scheduler.add_job(alert_morning_briefing, "cron",
                               day_of_week="mon-fri", hour=8, minute=30,
                               id="tg_morning")
            _scheduler.add_job(alert_watchlist_price, "cron",
                               day_of_week="mon-fri", hour="9-14", minute="0,30",
                               id="tg_watchlist")
            _scheduler.add_job(alert_new_reports, "cron",
                               day_of_week="mon-fri", hour=10, minute=0,
                               id="tg_reports")
            _scheduler.add_job(alert_closing_summary, "cron",
                               day_of_week="mon-fri", hour=15, minute=40,
                               id="tg_closing")
            # 장마감 자동 시황 요약 (15:42 — closing summary 직후)
            _scheduler.add_job(send_market_summary_telegram, "cron",
                               day_of_week="mon-fri", hour=15, minute=42,
                               id="tg_market_summary", max_instances=1)
            # 미국 장마감 시황 (KST 06:10 — 미국 월~금 마감 = KST 화~토)
            _scheduler.add_job(send_us_market_summary_telegram, "cron",
                               day_of_week="tue-sat", hour=6, minute=10,
                               id="tg_us_market_summary", max_instances=1)
            # Phase 24 Step 3: 새벽 5:00 브리핑 데이터 갱신
            _scheduler.add_job(_refresh_briefing_data, "cron",
                               day_of_week="tue-sat", hour=5, minute=0,
                               id="tg_briefing_refresh", max_instances=1)
            # Phase 24 Step 3: 새벽 5:30 익일 예측 브리핑 (화~토, 월요일 제외)
            _scheduler.add_job(alert_overnight_prediction, "cron",
                               day_of_week="tue-sat", hour=5, minute=30,
                               id="tg_overnight")
            # Phase 24 Step 3: 밤 22:00 미국장 시작 전 옵션 캐시 무효화
            _scheduler.add_job(refresh_us_options_signal, "cron",
                               day_of_week="mon-fri", hour=22, minute=0,
                               id="refresh_options")
            # 종목별 맞춤 알림 — 장중 10분 간격
            _scheduler.add_job(check_alert_rules, "cron",
                               day_of_week="mon-fri", hour="9-15", minute="*/10",
                               id="tg_custom_alerts", max_instances=1)
            # 트레일링 스톱 — 장중 30분 간격
            _scheduler.add_job(_check_trailing_stops, "cron",
                               day_of_week="mon-fri", hour="9-15", minute="0,30",
                               id="tg_trailing", max_instances=1)
            log.info("[텔레그램] 알림 스케줄 8개 등록")
        else:
            log.info("[텔레그램] 토큰 미설정 — 알림 비활성화")

        # ── 가격 동기화 ──
        # 장중 30분마다 + 장마감 직후(15:35) 가격 갱신
        _scheduler.add_job(_refresh_prices_from_naver, "cron",
                           day_of_week="mon-fri", hour="9-15", minute="5,35",
                           id="price_sync_intraday", max_instances=1)
        _scheduler.add_job(_refresh_prices_from_naver, "cron",
                           day_of_week="mon-fri", hour=15, minute=35,
                           id="price_sync_close", max_instances=1)
        # 시간외 단일가: 16:00~18:00 5분 간격
        _scheduler.add_job(_refresh_prices_from_naver, "cron",
                           day_of_week="mon-fri", hour="16-17", minute="*/5",
                           id="price_sync_afterhours", max_instances=1)
        log.info("[가격 동기화] 장중 30분 + 장마감 + 시간외 스케줄 등록")

        # ── Stage 2 자동 스캔 ──
        # 1) 장중 5분 간격 KR 실시간 스캔 (SQLite 기반 ~1초)
        def _stage2_realtime_kr():
            # 장외 시간이면 스킵 (cron만으로 부족 — 공휴일 체크)
            if not is_market_hours():
                log.debug("[Stage 2 realtime] 장외 시간 — 스킵")
                return
            with _discover_lock:
                if _discover_state["status"] in ("starting", "running"):
                    return
            _discover_set(status="running", phase="kr_stage1",
                          message="장중 자동 스캔…",
                          started_at=now_kst().strftime("%Y-%m-%d %H:%M:%S"))
            try:
                _run_stage2_kr()
            except Exception as exc:
                log.debug("stage2 realtime kr fail: %s", exc)
            finally:
                _discover_set(status="done", phase=None,
                              finished_at=now_kst().strftime("%Y-%m-%d %H:%M:%S"),
                              message="장중 자동 스캔 완료")

        _scheduler.add_job(_stage2_realtime_kr, "cron",
                           day_of_week="mon-fri", hour="9-15", minute="*/5",
                           id="stage2_realtime_kr", max_instances=1)

        # 2) 장 시작 전(08:00) + 장 마감 후(16:00) KR 스캔
        #    (US는 yfinance 1,500종목 빌드가 수 분 걸려서 별도 분리)
        def _auto_stage2_scan():
            # 주말/공휴일 보조 가드
            wd = now_kst().weekday()
            if wd >= 5:
                log.debug("[자동 스캔] 주말 — 스킵")
                return
            with _discover_lock:
                if _discover_state["status"] in ("starting", "running"):
                    log.info("[자동 스캔] 이미 실행 중 — 스킵")
                    return
            log.info("⏰  자동 Stage 2 스캔 시작 (kr)")
            _stage2_scoring_worker("kr")
        _scheduler.add_job(_auto_stage2_scan, "cron",
                           day_of_week="mon-fri", hour="8,16", minute=0,
                           id="stage2_auto", max_instances=1)

        # ── AI 에이전트 파이프라인 (장 시작 전 08:45 + 장 마감 후 15:45) ──
        def _auto_agent_run():
            try:
                from agents.pipeline import run_pipeline, send_agent_telegram
                result = run_pipeline()
                send_agent_telegram(result)
            except Exception as exc:
                log.debug("[Agent] 자동 실행 실패: %s", exc)
        _scheduler.add_job(_auto_agent_run, "cron",
                           day_of_week="mon-fri", hour="8,15", minute=45,
                           id="agent_pipeline", max_instances=1)
        log.info("[Agent] 파이프라인 자동 실행 스케줄 등록 (08:45, 15:45)")

        # ── 데이터 유지보수 cron ──
        _scheduler.add_job(mark_etf_stocks, "cron",
                           hour=3, minute=10,
                           id="mark_etf_stocks", max_instances=1)
        _scheduler.add_job(generate_themes_mapping, "cron",
                           day_of_week="sun", hour=3, minute=20,
                           id="gen_themes_mapping", max_instances=1)
        _scheduler.add_job(refresh_us_universe_if_stale, "cron",
                           day_of_week="sun", hour=4, minute=0,
                           id="refresh_us_universe", max_instances=1)
        log.info("[Maint] ETF 마킹/테마 매핑/US 유니버스 자동화 등록")

        # ── DART 공시 실시간 감지 ──
        if os.getenv("DART_API_KEY"):
            # 평일 08:00~18:00 1분 간격 공시 폴링
            _scheduler.add_job(poll_dart_disclosures, "cron",
                               day_of_week="mon-fri", hour="8-17", minute="*",
                               id="dart_poll", max_instances=1)
            # 매일 새벽 3시 corp_code 매핑 갱신
            _scheduler.add_job(init_dart_corp_map_db, "cron",
                               hour=3, minute=0,
                               id="dart_corp_map_update", max_instances=1)
            log.info("[DART] 공시 폴링 + corp_map 갱신 스케줄 등록")

        _scheduler.start()
        log.info("APScheduler 시작 — %d분 간격", interval)
    else:
        log.info("APScheduler 미설치 — 자동 갱신 비활성  (pip install apscheduler)")

    # ── WebSocket 가격 브로드캐스터 시작 ──
    if _SOCKETIO_OK:
        threading.Thread(target=_price_broadcaster, daemon=True,
                         name="ws-broadcaster").start()
        log.info("[WS] 가격 브로드캐스터 시작")


# ─────────────────────────────────────────────────────────────────────────
# 실시간 가격 동기화 — naver_universe + stocks 테이블 갱신
# ─────────────────────────────────────────────────────────────────────────

def mark_etf_stocks():
    """ETF/ETN 종목 자동 마킹. 매일 03:10 cron."""
    if not (_SQLITE_OK and USE_SQLITE):
        return 0
    ETF_PATTERNS = (
        'KODEX', 'TIGER', 'KBSTAR', 'KOSEF', 'HANARO',
        'ARIRANG', 'KINDEX', 'TREX', 'ACE ', 'SOL ',
        ' ETF', ' ETN', 'TRF', '레버리지', '인버스', '선물',
        'TIMEFOLIO', 'BNK', 'FOCUS', 'WON ', 'SMART',
        'PLUS ', 'RISE ', 'WOORI',
    )
    with _get_db() as conn:
        conn.execute("UPDATE stocks SET is_etf = 0")
        for p in ETF_PATTERNS:
            conn.execute("UPDATE stocks SET is_etf = 1 WHERE name LIKE ?", (f'%{p}%',))
        cnt = conn.execute("SELECT COUNT(*) FROM stocks WHERE is_etf = 1").fetchone()[0]
        conn.commit()
    log.info("[ETF 마킹] %d종목", cnt)
    return cnt


def generate_themes_mapping(force: bool = False):
    """DB 섹터 기반 themes_mapping.json 생성.
    force=False(기본): 파일 없을 때만 생성 (큐레이티드 파일 보존).
    force=True: 덮어쓰기 (자동 cron은 사용 안 함).
    """
    path_check = BASE_DIR / "themes_mapping.json"
    if not force and path_check.exists() and path_check.stat().st_size > 100:
        log.info("[테마 매핑] 기존 큐레이티드 파일 존재 — 스킵 (force=True로 덮어쓰기)")
        return 0
    if not (_SQLITE_OK and USE_SQLITE):
        return 0
    with _get_db() as conn:
        sectors = conn.execute("""
            SELECT sector, COUNT(*) AS cnt
            FROM stocks
            WHERE (market = '' OR market LIKE 'KOS%')
              AND COALESCE(is_etf, 0) = 0
              AND sector IS NOT NULL AND sector != ''
            GROUP BY sector HAVING cnt >= 3
            ORDER BY cnt DESC
        """).fetchall()

        themes_list = []
        tid = 1
        for s in sectors:
            sect_name = s["sector"]
            top = conn.execute("""
                SELECT code, name FROM stocks
                WHERE sector = ? AND (market = '' OR market LIKE 'KOS%')
                  AND COALESCE(is_etf, 0) = 0
                ORDER BY COALESCE(market_cap, 0) DESC, volume_mn DESC LIMIT 15
            """, (sect_name,)).fetchall()
            stocks_list = [{"code": r["code"], "name": r["name"]} for r in top]
            if stocks_list:
                themes_list.append({
                    "id": tid, "name": sect_name, "stocks": stocks_list,
                })
                tid += 1

    path = BASE_DIR / "themes_mapping.json"
    if path.exists():
        try:
            import shutil
            shutil.copy(str(path), str(path) + ".bak")
        except Exception:
            pass
    path.write_text(json.dumps(themes_list, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("[테마 매핑] %d개 섹터 자동 생성", len(themes_list))
    return len(themes_list)


def refresh_us_universe_if_stale(max_days: int = 7):
    """US 유니버스가 N일 이상 오래됐으면 재수집. 일요일 04:00 cron."""
    if not (_SQLITE_OK and USE_SQLITE):
        return False
    try:
        with _get_db() as conn:
            r = conn.execute(
                "SELECT MAX(updated_at) AS upd FROM stocks WHERE market='US' OR market LIKE 'NASD%' OR market LIKE 'NYS%'"
            ).fetchone()
        if r and r["upd"]:
            from datetime import datetime as _dt
            last = _dt.fromisoformat(r["upd"])
            age_days = (now_kst().replace(tzinfo=None) - last).total_seconds() / 86400
            if age_days < max_days:
                log.info("[US Universe] 최근 갱신 %.1f일 전 — 스킵", age_days)
                return False
    except Exception:
        pass
    # 재빌드 트리거
    try:
        (BASE_DIR / "cache" / "sp500_tickers.json").unlink(missing_ok=True)
        _fetch_us_market_data(force=True)
        log.info("[US Universe] 재수집 완료")
        return True
    except Exception as exc:
        log.warning("[US Universe] 재수집 실패: %s", exc)
        return False


def _refresh_prices_from_naver():
    """
    네이버 실시간 API로 naver_universe 캐시 + SQLite stocks 가격 일괄 갱신.
    장마감 후 호출하면 종가 반영. 장중에도 호출 가능.
    """
    import urllib.request
    uni = _load_naver_universe()
    if not uni or not uni.get("stocks"):
        log.warning("[가격 갱신] naver_universe 없음")
        return 0

    stocks_map = uni["stocks"]
    codes = list(stocks_map.keys())
    updated = 0

    _fail = 0
    for i in range(0, len(codes), 100):
        batch = codes[i:i + 100]
        codes_str = ",".join(batch)
        data = None
        # 최대 2회 재시도
        for _attempt in range(2):
            try:
                url = f"https://polling.finance.naver.com/api/realtime/domestic/stock/{codes_str}"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=20) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                break
            except Exception as exc:
                if _attempt == 1:
                    _fail += 1
                    log.warning("[가격 갱신] 배치 %d 실패 (%d 재시도 후): %s",
                                i, _attempt + 1, exc)
                    data = None
                else:
                    time.sleep(0.5)
        if data is None:
            continue
        try:
            datas_list = data.get("datas") or []
            _not_in_uni = 0; _zero_price = 0; _parse_err = 0; _ok = 0
            sample_miss = []

            for s in datas_list:
                code = s.get("itemCode")
                if not code or code not in stocks_map:
                    _not_in_uni += 1
                    if len(sample_miss) < 2: sample_miss.append(code)
                    continue
                try:
                    price = float(str(s.get("closePrice", "0")).replace(",", ""))
                    chg = float(str(s.get("fluctuationsRatio", "0")).replace(",", ""))
                    vol = int(float(str(s.get("accumulatedTradingVolume", "0")).replace(",", "")))
                except (ValueError, TypeError):
                    _parse_err += 1
                    continue
                if price <= 0:
                    _zero_price += 1
                    continue
                _ok += 1
                # naver_universe 메모리 캐시 갱신 (변경 여부 무관하게 항상 덮어쓰기)
                stocks_map[code]["close"] = price
                stocks_map[code]["change_pct"] = chg
                stocks_map[code]["volume"] = vol
                stocks_map[code]["volume_mn"] = int(vol * price / 1_000_000)

                # 시간외 단일가 (overMarketPriceInfo)
                # tradingSessionType=REGULAR_MARKET 은 정규장 데이터 → 시간외로 쓰지 않음
                over = s.get("overMarketPriceInfo") or {}
                sess = (over.get("tradingSessionType") or "").upper() if isinstance(over, dict) else ""
                is_real_after_hours = isinstance(over, dict) and over.get("overPrice") and \
                                      sess and sess != "REGULAR_MARKET"
                if is_real_after_hours:
                    try:
                        ap = float(str(over.get("overPrice", "0")).replace(",", ""))
                        apc = float(str(over.get("fluctuationsRatio", "0")).replace(",", ""))
                        stocks_map[code]["after_hours_price"] = ap
                        stocks_map[code]["after_hours_change_pct"] = apc
                        stocks_map[code]["after_hours_status"] = over.get("overMarketStatus")
                        stocks_map[code]["after_hours_time"] = over.get("localTradedAt")
                    except (ValueError, TypeError):
                        pass
                else:
                    # 정규장이거나 시간외 데이터 없음 → 이전 기록 초기화
                    stocks_map[code]["after_hours_price"] = None
                    stocks_map[code]["after_hours_change_pct"] = None
                    stocks_map[code]["after_hours_status"] = None
                    stocks_map[code]["after_hours_time"] = None

                updated += 1
            if i == 0:
                log.info("[가격 갱신] batch0 상세: ok=%d, not_in_uni=%d, zero=%d, parse_err=%d, sample_miss=%s",
                         _ok, _not_in_uni, _zero_price, _parse_err, sample_miss)
        except Exception as exc:
            log.warning("[가격 갱신] 배치 %d 파싱 실패: %s", i, exc)
        time.sleep(0.2)

    # naver_universe JSON 파일 덮어쓰기
    if updated > 0:
        # 네이버 실시간 API 기준 "실제 거래된 날짜"를 우선 사용 (pykrx 지연 영향 배제)
        today_kst = now_kst().strftime("%Y%m%d")
        out_file = BASE_DIR / "cache" / f"naver_universe_{today_kst}.json"
        uni["fetched_at"] = now_kst().strftime("%Y-%m-%d %H:%M:%S")
        try:
            out_file.write_text(json.dumps(uni, ensure_ascii=False), encoding="utf-8")
            # 메모리 캐시 무효화 → 다음 _load_naver_universe() 시 재로드
            _UNI_CACHE["mtime"] = 0
        except Exception as exc:
            log.debug("[가격 갱신] universe 저장 실패: %s", exc)

        # SQLite stocks 테이블도 갱신 (시간외 포함)
        if _SQLITE_OK and USE_SQLITE:
            try:
                with _get_db() as conn:
                    for code, info in stocks_map.items():
                        conn.execute(
                            "UPDATE stocks SET close=?, change_pct=?, volume_mn=?, "
                            "after_hours_price=?, after_hours_change_pct=?, "
                            "after_hours_status=?, after_hours_time=?, "
                            "updated_at=datetime('now') WHERE code=?",
                            (info.get("close"), info.get("change_pct"),
                             info.get("volume_mn"),
                             info.get("after_hours_price"),
                             info.get("after_hours_change_pct"),
                             info.get("after_hours_status"),
                             info.get("after_hours_time"),
                             code),
                        )
                    conn.commit()
            except Exception as exc:
                log.debug("[가격 갱신] stocks DB 갱신 실패: %s", exc)

    log.info("[가격 갱신] %d종목 반영, 실패 배치 %d개", updated, _fail)
    return updated


@app.route("/api/refresh_prices", methods=["POST"])
def api_refresh_prices():
    """가격 데이터 수동 갱신 (백그라운드 실행)."""
    def _bg():
        _refresh_prices_from_naver()
    threading.Thread(target=_bg, daemon=True, name="price-refresh").start()
    return jsonify({"ok": True, "message": "백그라운드 갱신 시작"})


@app.route("/api/after_hours/<code>")
def api_after_hours(code: str):
    """단일 종목의 시간외 단일가 조회."""
    if not (_SQLITE_OK and USE_SQLITE):
        return jsonify({"error": "SQLite 비활성"}), 503
    try:
        with _get_db() as conn:
            row = conn.execute(
                "SELECT code, name, close, change_pct, "
                "after_hours_price, after_hours_change_pct, "
                "after_hours_status, after_hours_time "
                "FROM stocks WHERE code = ?", (code,)
            ).fetchone()
            if not row:
                return jsonify({"error": "종목 없음"}), 404
            d = dict(row)
            return jsonify({
                "code": d["code"],
                "name": d["name"],
                "close": d["close"],
                "change_pct": d["change_pct"],
                "after_hours": {
                    "price": d.get("after_hours_price"),
                    "change_pct": d.get("after_hours_change_pct"),
                    "status": d.get("after_hours_status"),
                    "traded_at": d.get("after_hours_time"),
                } if d.get("after_hours_price") else None,
            })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


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

    # v2 보너스: 볼밴 수축 (sparkline 변동성 축소 → 돌파 임박)
    if sparkline and len(sparkline) >= 20:
        try:
            mean_all = sum(sparkline) / len(sparkline)
            std_all = (sum((v - mean_all) ** 2 for v in sparkline) / len(sparkline)) ** 0.5
            recent5 = sparkline[-5:]
            mean5 = sum(recent5) / 5
            std5 = (sum((v - mean5) ** 2 for v in recent5) / 5) ** 0.5
            if std_all > 0 and std5 < std_all * 0.6:
                sub["squeeze"] = 3
                expl.append({
                    "label": "볼밴 수축 보너스",
                    "detail": f"5일 변동성 {std5/std_all*100:.0f}% (돌파 임박 가능)",
                    "pts": 3, "max": 3,
                })
        except Exception:
            pass

    # v2 보너스: 돌파 임박 (20일 박스권 <10% + 상단 95% 근접)
    if sparkline and len(sparkline) >= 20:
        try:
            hi20 = max(sparkline[-20:])
            lo20 = min(sparkline[-20:])
            avg20 = sum(sparkline[-20:]) / 20
            box_pct = (hi20 - lo20) / avg20 * 100 if avg20 > 0 else 999
            near_top = sparkline[-1] >= hi20 * 0.95
            if box_pct < 10 and near_top:
                sub["breakout"] = 5
                expl.append({
                    "label": "돌파 임박 보너스",
                    "detail": f"20일 박스 {box_pct:.1f}% + 상단 근접",
                    "pts": 5, "max": 5,
                })
        except Exception:
            pass

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


def _stage1_prefilter_us(min_volume_mn: float = 1, top_k: int = 1500) -> dict:
    """미국 S&P 1500 에서 모멘텀+섹터 점수로 상위 top_k 추출 (캐시 데이터만 사용)."""
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


def _stage1_prefilter_kr(min_volume_mn: int = 1, top_k: int = 2600) -> dict:
    """국내 전 종목에서 모멘텀+섹터 점수로 상위 top_k 추출.
    v3: 거래대금 1백만 이상 전종목 커버 (~3,500). KOSPI/KOSDAQ 분리 선발.
    """
    universe = _load_naver_universe()
    stocks_map = (universe or {}).get("stocks") or {}
    if not stocks_map:
        return {"error": "naver_universe 캐시 없음", "items": [], "total_scanned": 0}

    sparklines = _load_ticker_sparklines_kr()
    sectors = _load_naver_sector_aggregates()

    # KRX 마켓 분류 로드 (KOSPI/KOSDAQ 분리용)
    krx_stocks = _get_krx_all_stocks_cached()

    # 거래대금 필터 적용 후 남은 종목만 랭킹
    eligible = [s for s in stocks_map.values()
                if (s.get("volume_mn") or 0) >= min_volume_mn]

    # ETF/ETN/펀드/선물 제외: DB is_etf 컬럼 우선, 없으면 이름 패턴
    etf_codes: set = set()
    if _SQLITE_OK and USE_SQLITE:
        try:
            with _get_db() as _conn:
                rows = _conn.execute(
                    "SELECT code FROM stocks WHERE is_etf = 1"
                ).fetchall()
                etf_codes = {r["code"] for r in rows}
        except Exception:
            etf_codes = set()
    _ETF_PATTERNS = (
        'KODEX', 'TIGER', 'KBSTAR', 'KOSEF', 'HANARO',
        'ARIRANG', 'KINDEX', 'TREX', 'ACE ', 'SOL ',
        ' ETF', ' ETN', 'TRF', '레버리지', '인버스', '선물',
        'TIMEFOLIO', 'BNK', 'FOCUS', 'WON ', 'SMART',
        'PLUS ', 'RISE ', 'WOORI',
    )
    def _is_etf(s):
        if s.get("code") in etf_codes:
            return True
        name = s.get("name", "")
        return any(p in name for p in _ETF_PATTERNS)
    before_etf = len(eligible)
    eligible = [s for s in eligible if not _is_etf(s)]
    log.info("[Stage1 KR] ETF 제외: %d → %d (DB: %d, 패턴 보완)",
             before_etf, len(eligible), len(etf_codes))

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
        # KRX 마켓 분류
        krx_row = krx_stocks.get(code) or {}
        mkt = krx_row.get("MKT_NM") or krx_row.get("mktNm") or ""
        if "KOSPI" in mkt.upper() or "유가증권" in mkt:
            mkt_label = "KOSPI"
        elif "KOSDAQ" in mkt.upper() or "코스닥" in mkt:
            mkt_label = "KOSDAQ"
        else:
            mkt_label = ""

        # KRX 시가총액 (있으면 활용)
        mkt_cap = _krx_get_float(krx_row, "MKTCAP", "mktCap")

        results.append({
            "code": code,
            "name": s.get("name"),
            "market": "kr",
            "market_type": mkt_label,
            "market_cap": mkt_cap,
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

    # ── KOSPI/KOSDAQ 분리 선발 (v2) ──
    kospi_items = [r for r in results if r.get("market_type") == "KOSPI"]
    kosdaq_items = [r for r in results if r.get("market_type") == "KOSDAQ"]
    unknown_items = [r for r in results if not r.get("market_type")]

    half = top_k // 2  # 1300

    if kospi_items and kosdaq_items:
        # KOSPI 200 + KOSDAQ 200 분리 선발
        selected = kospi_items[:half] + kosdaq_items[:half]
        # 잔여 슬롯을 나머지에서 보충
        selected_codes = {r["code"] for r in selected}
        remainder = [r for r in results if r["code"] not in selected_codes]
        fill = top_k - len(selected)
        if fill > 0:
            selected += remainder[:fill]
        # 다시 score 순 정렬
        selected.sort(key=lambda x: x["total_score"], reverse=True)
        log.info("Stage 1 KR v2: KOSPI %d + KOSDAQ %d + 보충 %d = %d",
                 min(len(kospi_items), half), min(len(kosdaq_items), half),
                 max(0, fill), len(selected))
    else:
        # KRX 데이터 없으면 통합 상위 top_k
        selected = results[:top_k]
        log.info("Stage 1 KR v2: KRX 마켓 분류 없음, 통합 상위 %d", len(selected))

    # 분류 카운트: KRX 분류 불가 시 None (프론트가 'null' 체크)
    kospi_n = len(kospi_items) if kospi_items else None
    kosdaq_n = len(kosdaq_items) if kosdaq_items else None

    return {
        "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "market": "kr",
        "stage": 1,
        "total_scanned": total,
        "kospi_count": kospi_n,
        "kosdaq_count": kosdaq_n,
        "unclassified_count": sum(1 for r in results if not r.get("market_type")),
        "items": selected,
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
# PHASE 22 — 섹터 로테이션 / 동종비교 / 매크로
# ─────────────────────────────────────────────────────────────────────────
@app.route("/api/sector_rotation")
def api_sector_rotation():
    """
    KR 업종별 1주(5d) / 1개월(20d) 수익률. pykrx 지수 API 차단 상태이므로
    ticker_data sparkline + naver_universe 섹터로 거래대금 가중평균 집계.
    6시간 캐시.
    """
    today = _get_trading_date()
    cache_file = BASE_DIR / "cache" / f"sector_rotation_{today}.json"
    cached = _read_fresh_json(cache_file, 360)
    if cached:
        return jsonify(cached)

    sparklines = _load_ticker_sparklines_kr()
    uni = _load_naver_universe()
    stocks = (uni or {}).get("stocks") or {}
    if not sparklines or not stocks:
        return jsonify({"error": "캐시 데이터 부족", "sectors": []}), 503

    sectors_raw: dict[str, list[dict]] = {}
    for code, st in stocks.items():
        sp = sparklines.get(code)
        if not sp or len(sp) < 20 or not sp[0]:
            continue
        sec_list = st.get("sectors") or []
        if not sec_list:
            continue
        sector = sec_list[0]
        try:
            ret_5d  = (sp[-1] / sp[-5] - 1) * 100 if sp[-5] else None
            ret_20d = (sp[-1] / sp[0]  - 1) * 100
        except Exception:
            continue
        sectors_raw.setdefault(sector, []).append({
            "code":      code,
            "volume_mn": st.get("volume_mn") or 0,
            "chg_today": st.get("change_pct") or 0,
            "ret_5d":    ret_5d,
            "ret_20d":   ret_20d,
        })

    def _weighted_avg(rows: list, key: str) -> float | None:
        vals = [r for r in rows if r.get(key) is not None]
        if not vals:
            return None
        total_w = sum(r["volume_mn"] for r in vals) or 0
        if total_w > 0:
            return round(
                sum((r[key] or 0) * (r["volume_mn"] or 0) for r in vals) / total_w, 2
            )
        return round(sum((r[key] or 0) for r in vals) / len(vals), 2)

    sectors_out: list[dict] = []
    for name, rows in sectors_raw.items():
        if len(rows) < 2:
            continue
        sectors_out.append({
            "name":         name,
            "stock_count":  len(rows),
            "ret_1w":       _weighted_avg(rows, "ret_5d"),
            "ret_1m":       _weighted_avg(rows, "ret_20d"),
            "change_today": _weighted_avg(rows, "chg_today"),
        })
    sectors_out.sort(key=lambda x: (x.get("ret_1m") or 0), reverse=True)

    result = {
        "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "count":      len(sectors_out),
        "sectors":    sectors_out,
        "note":       "1주=5영업일, 1개월=20영업일 sparkline 가중평균. 3개월 데이터 없음.",
    }
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


@app.route("/api/peers/<code>")
def api_peers_kr(code: str):
    """KR 동종 업계 비교. naver_universe 같은 섹터 → 거래대금 상위 20, 12h 캐시."""
    if not re.fullmatch(r"\d{6}", code):
        return jsonify({"error": "잘못된 종목코드"}), 400
    cache_file = BASE_DIR / "cache" / f"peers_{code}.json"
    cached = _read_fresh_json(cache_file, 720)
    if cached:
        return jsonify(cached)

    uni = _load_naver_universe()
    stocks = (uni or {}).get("stocks") or {}
    target = stocks.get(code)
    if not target:
        return jsonify({"error": "종목 유니버스에 없음", "peers": []}), 404

    target_sector = (target.get("sectors") or [None])[0]
    if not target_sector:
        return jsonify({"error": "섹터 정보 없음", "peers": []}), 404

    same_sector = []
    for c, s in stocks.items():
        if (s.get("sectors") or [None])[0] != target_sector:
            continue
        same_sector.append({
            "code":       c,
            "name":       s.get("name"),
            "price":      s.get("close"),
            "change_pct": s.get("change_pct"),
            "volume_mn":  s.get("volume_mn") or 0,
        })
    same_sector.sort(key=lambda x: x["volume_mn"], reverse=True)
    top_peers = same_sector[:20]

    # 기존 cache/financial_*.json 만 재사용 (신규 호출 없음)
    for p in top_peers:
        fin_cache = BASE_DIR / "cache" / f"financial_{p['code']}.json"
        p["per"] = None
        p["pbr"] = None
        p["industry_per"] = None
        if fin_cache.exists():
            try:
                age_hr = (now_kst().timestamp() - fin_cache.stat().st_mtime) / 3600
                if age_hr < 24:
                    fin = json.loads(fin_cache.read_text(encoding="utf-8"))
                    p["per"] = fin.get("per")
                    p["pbr"] = fin.get("pbr")
                    p["industry_per"] = fin.get("industry_per")
            except Exception:
                pass

    per_vals = [p["per"] for p in top_peers if isinstance(p.get("per"), (int, float)) and p["per"] > 0]
    pbr_vals = [p["pbr"] for p in top_peers if isinstance(p.get("pbr"), (int, float)) and p["pbr"] > 0]
    sector_avg = {
        "per": round(sum(per_vals) / len(per_vals), 1) if per_vals else None,
        "pbr": round(sum(pbr_vals) / len(pbr_vals), 2) if pbr_vals else None,
    }
    target_rank = next((i for i, p in enumerate(top_peers) if p["code"] == code), -1)

    result = {
        "code":        code,
        "sector":      target_sector,
        "sector_avg":  sector_avg,
        "target_rank": target_rank,
        "peer_count":  len(top_peers),
        "peers":       top_peers,
    }
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


@app.route("/api/us/peers/<symbol>")
def api_peers_us(symbol: str):
    """US 동종비교. sp500_tickers + us_market + us_yinfo 캐시 조합, 12h 캐시."""
    symbol = symbol.upper()
    cache_file = BASE_DIR / "cache" / f"us_peers_{symbol}.json"
    cached = _read_fresh_json(cache_file, 720)
    if cached:
        return jsonify(cached)

    sp_file = BASE_DIR / "cache" / "sp500_tickers.json"
    if not sp_file.exists():
        return jsonify({"error": "sp500_tickers.json 없음", "peers": []}), 503
    try:
        sp500 = json.loads(sp_file.read_text(encoding="utf-8"))
    except Exception:
        return jsonify({"error": "sp500 파싱 실패", "peers": []}), 503

    target = next((t for t in sp500 if t.get("symbol") == symbol), None)
    if not target:
        return jsonify({"error": "S&P500 목록에 없음", "peers": []}), 404
    sector = target.get("sector")
    if not sector:
        return jsonify({"error": "섹터 정보 없음", "peers": []}), 404

    us_market = _fetch_us_market_data()
    stocks_by_sym = {s["symbol"]: s for s in (us_market.get("all_stocks") or [])}
    same_sector = [t for t in sp500 if t.get("sector") == sector]

    peers: list[dict] = []
    for t in same_sector:
        sym = t["symbol"]
        m = stocks_by_sym.get(sym, {})
        p = {
            "code":         sym,
            "symbol":       sym,
            "name":         t.get("name") or sym,
            "sub_industry": t.get("sub_industry"),
            "price":        m.get("price"),
            "change_pct":   m.get("change_pct"),
            "volume_mn":    m.get("volume_mn") or 0,
            "per":          None,
            "pbr":          None,
            "roe":          None,
            "market_cap":   None,
        }
        yinfo_cache = BASE_DIR / "cache" / f"us_yinfo_{sym}.json"
        if yinfo_cache.exists():
            try:
                age_hr = (now_kst().timestamp() - yinfo_cache.stat().st_mtime) / 3600
                if age_hr < 24:
                    info = json.loads(yinfo_cache.read_text(encoding="utf-8"))
                    tpe = info.get("trailingPE")
                    pb  = info.get("priceToBook")
                    roe = info.get("returnOnEquity")
                    p["per"]        = round(tpe, 1) if tpe else None
                    p["pbr"]        = round(pb, 2)  if pb  else None
                    p["roe"]        = round(roe * 100, 1) if roe else None
                    p["market_cap"] = info.get("marketCap")
            except Exception:
                pass
        peers.append(p)

    peers.sort(key=lambda x: (x.get("market_cap") or 0), reverse=True)
    peers = peers[:25]

    per_vals = [p["per"] for p in peers if isinstance(p.get("per"), (int, float)) and p["per"] > 0]
    pbr_vals = [p["pbr"] for p in peers if isinstance(p.get("pbr"), (int, float)) and p["pbr"] > 0]
    sector_avg = {
        "per": round(sum(per_vals) / len(per_vals), 1) if per_vals else None,
        "pbr": round(sum(pbr_vals) / len(pbr_vals), 2) if pbr_vals else None,
    }
    target_rank = next((i for i, p in enumerate(peers) if p["symbol"] == symbol), -1)

    result = {
        "symbol":      symbol,
        "sector":      sector,
        "sector_avg":  sector_avg,
        "target_rank": target_rank,
        "peer_count":  len(peers),
        "peers":       peers,
    }
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


_MACRO_TICKERS = [
    ("USD/KRW",     "KRW=X",    "currency",   ""),
    ("USD/JPY",     "JPY=X",    "currency",   ""),
    ("EUR/USD",     "EURUSD=X", "currency",   ""),
    ("USD/CNY",     "CNY=X",    "currency",   ""),
    ("WTI 원유",    "CL=F",     "commodity",  "$/bbl"),
    ("금",          "GC=F",     "commodity",  "$/oz"),
    ("은",          "SI=F",     "commodity",  "$/oz"),
    ("구리",        "HG=F",     "commodity",  "$/lb"),
    ("천연가스",    "NG=F",     "commodity",  "$"),
    ("미국 10년물", "^TNX",     "bond",       "%"),
    ("VIX",         "^VIX",     "volatility", "pt"),
    ("BTC",         "BTC-USD",  "crypto",     "$"),
]


@app.route("/api/macro")
def api_macro():
    """yfinance 기반 글로벌 매크로 12종. 30분 캐시."""
    cache_file = BASE_DIR / "cache" / "macro_data.json"
    cached = _read_fresh_json(cache_file, 30)
    if cached:
        return jsonify(cached)

    try:
        import yfinance as _yf
    except ImportError:
        return jsonify({"error": "yfinance 미설치", "items": []}), 500

    items: list[dict] = []
    for name, ticker, category, unit in _MACRO_TICKERS:
        try:
            t = _yf.Ticker(ticker)
            hist = t.history(period="5d")
            if hist is None or hist.empty:
                continue
            closes = [float(c) for c in hist["Close"].tolist() if c == c]
            if not closes:
                continue
            cur = round(closes[-1], 4)
            prev = round(closes[-2], 4) if len(closes) >= 2 else cur
            change = round(cur - prev, 4)
            change_pct = round((cur / prev - 1) * 100, 2) if prev else 0.0
            items.append({
                "name": name, "ticker": ticker, "category": category,
                "value": cur, "change": change, "change_pct": change_pct, "unit": unit,
            })
            time.sleep(0.05)
        except Exception as exc:
            log.debug("macro %s fail: %s", ticker, exc)
            continue

    result = {
        "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "count":      len(items),
        "items":      items,
    }
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


# ─────────────────────────────────────────────────────────────────────────
# DART 공시 실시간 감지
# ─────────────────────────────────────────────────────────────────────────

# 주가 영향 키워드 (제목에서 검색)
_DART_CRITICAL_KW = [
    "대규모", "최대주주변경", "최대주주 변경", "공개매수", "상장폐지", "거래정지",
    "관리종목", "경영권",
]
_DART_HIGH_KW = [
    "공급계약", "수주", "자기주식", "자사주", "유상증자", "무상증자",
    "감자", "분할", "합병", "흑자전환", "적자전환",
    "배당", "주식분할", "영업이익", "전환사채", "신주인수권",
    "주요사항", "타법인 주식", "자산양수", "특허",
]


# 공시 중요도 점수표 (규정 기반 하드코딩 — 각 카테고리별 대표 키워드)
_DISCLOSURE_SCORE_TABLE = {
    # 10점: 즉시 매매 영향
    "상장폐지": 10, "거래정지": 10, "최대주주변경": 10, "최대주주 변경": 10,
    "공개매수": 10, "회생": 10, "파산": 10, "경영권": 10,
    # 8점: 주가 급등락
    "대규모": 8, "공급계약": 8, "수주": 8,
    "합병": 8, "분할": 8, "영업양수": 8, "유상증자": 8,
    "감자": 8, "전환사채": 8, "신주인수권": 8,
    # 6점: 중요 재무
    "자기주식": 6, "자사주": 6, "배당": 6, "흑자전환": 6,
    "적자전환": 6, "실적": 6, "영업이익": 6,
    # 4점: 참고
    "주식분할": 4, "액면분할": 4, "대표이사": 4, "임원변경": 4,
    "소송": 4, "제재": 4, "처분": 4, "특허": 4,
    # 2점: 일반
    "정기주주총회": 2, "이사회": 2, "감사보고서": 2,
    "분기보고서": 2, "반기보고서": 2, "사업보고서": 2,
}


def _classify_disclosure(title: str) -> tuple[str, list[str]]:
    """레거시 호환 (importance, matched_keywords)."""
    info = score_disclosure(title, "")
    return info["importance"], info["matched_keywords"]


def _cap_bonus_for_code(stock_code: str) -> int:
    if not stock_code or not (_SQLITE_OK and USE_SQLITE):
        return 0
    try:
        with _get_db() as conn:
            r = conn.execute(
                "SELECT market_cap FROM stocks WHERE code = ?", (stock_code,)
            ).fetchone()
        if r and r["market_cap"]:
            cap = r["market_cap"]
            if cap > 10_000_000_000_000: return 3
            if cap > 1_000_000_000_000:  return 2
            if cap > 100_000_000_000:    return 1
    except Exception:
        pass
    return 0


def _is_watchlist_code(stock_code: str) -> bool:
    if not stock_code:
        return False
    try:
        wl = _load_server_watchlist() or []
        return any(w.get("code") == stock_code for w in wl)
    except Exception:
        return False


def score_disclosure(title: str, stock_code: str = "") -> dict:
    """공시 중요도 점수 계산 (키워드 + 관심종목 + 시총).
    returns: {total_score, keyword_score, watchlist_bonus, cap_bonus,
              matched_keywords, importance}
    """
    t = (title or "").replace(" ", "")

    keyword_score = 0
    matched_keywords: list[str] = []
    for kw, score in _DISCLOSURE_SCORE_TABLE.items():
        if kw.replace(" ", "") in t:
            matched_keywords.append(kw)
            if score > keyword_score:
                keyword_score = score

    watchlist_bonus = 3 if _is_watchlist_code(stock_code) else 0
    cap_bonus = _cap_bonus_for_code(stock_code)
    total = keyword_score + watchlist_bonus + cap_bonus

    if total >= 10:
        importance = "critical"
    elif total >= 6:
        importance = "high"
    elif total >= 4:
        importance = "medium"
    else:
        importance = "low"

    return {
        "total_score": total,
        "keyword_score": keyword_score,
        "watchlist_bonus": watchlist_bonus,
        "cap_bonus": cap_bonus,
        "matched_keywords": matched_keywords,
        "importance": importance,
    }


def recalc_disclosure_scores(only_zero: bool = True) -> dict:
    """기존 공시 행들의 score/importance/keywords 재계산.
    only_zero=True: score=0 또는 NULL 인 행만. False: 전체 재계산."""
    if not (_SQLITE_OK and USE_SQLITE):
        return {"error": "SQLite 비활성"}
    updated = 0
    scanned = 0
    with _get_db() as conn:
        if only_zero:
            rows = conn.execute(
                "SELECT rcept_no, title, stock_code FROM disclosure_history "
                "WHERE COALESCE(score, 0) = 0"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT rcept_no, title, stock_code FROM disclosure_history"
            ).fetchall()
        scanned = len(rows)
        for r in rows:
            info = score_disclosure(r["title"] or "", r["stock_code"] or "")
            conn.execute(
                "UPDATE disclosure_history "
                "SET score = ?, importance = ?, keywords_json = ? "
                "WHERE rcept_no = ?",
                (info["total_score"], info["importance"],
                 json.dumps(info["matched_keywords"], ensure_ascii=False),
                 r["rcept_no"])
            )
            if info["total_score"] > 0:
                updated += 1
        conn.commit()
    log.info("[공시 점수 재계산] %d/%d 업데이트", updated, scanned)
    return {"scanned": scanned, "updated": updated}


@app.route("/api/disclosures/recalc", methods=["POST"])
def api_disclosures_recalc():
    only_zero = request.args.get("all", "0") != "1"
    return jsonify(recalc_disclosure_scores(only_zero=only_zero))


def init_dart_corp_map_db():
    """기존 _load_dart_corp_code_map() 결과를 SQLite dart_corp_map에 동기화."""
    if not (_SQLITE_OK and USE_SQLITE):
        return 0
    mapping = _load_dart_corp_code_map()
    if not mapping:
        return 0
    # 종목명은 naver_universe에서 보충
    uni = _load_naver_universe()
    stocks_map = (uni or {}).get("stocks") or {}
    rows = []
    for stock_code, corp_code in mapping.items():
        name = (stocks_map.get(stock_code) or {}).get("name", "")
        rows.append((stock_code, corp_code, name))
    try:
        with _get_db() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO dart_corp_map (stock_code, corp_code, corp_name) "
                "VALUES (?,?,?)", rows,
            )
            conn.commit()
        log.info("[DART] corp_code 매핑 DB 동기화: %d개", len(rows))
    except Exception as exc:
        log.warning("[DART] corp_map DB sync fail: %s", exc)
    return len(rows)


def poll_dart_disclosures():
    """DART 최근 공시 조회 → 중요 공시 텔레그램 알림. 1분 간격 호출."""
    dart_key = os.getenv("DART_API_KEY")
    if not dart_key:
        return
    if not (_SQLITE_OK and USE_SQLITE):
        return

    today = now_kst().strftime("%Y%m%d")
    try:
        import requests as _rq
        r = _rq.get(
            "https://opendart.fss.or.kr/api/list.json",
            params={
                "crtfc_key": dart_key,
                "bgn_de": today,
                "end_de": today,
                "page_count": 100,
                "page_no": 1,
            },
            timeout=10,
        )
        data = r.json()
        if data.get("status") != "000":
            return
        items = data.get("list") or []
    except Exception as exc:
        log.debug("[DART] poll fail: %s", exc)
        return

    if not items:
        return

    # 관심종목 세트 (빠른 조회용)
    wl = _load_server_watchlist()
    wl_codes = {it.get("code") for it in wl if it.get("code")}

    new_alerts = 0
    try:
        with _get_db() as conn:
            for disc in items:
                rcept_no = disc.get("rcept_no")
                if not rcept_no:
                    continue
                # 이미 처리한 공시 스킵
                exists = conn.execute(
                    "SELECT 1 FROM disclosure_history WHERE rcept_no=?", (rcept_no,)
                ).fetchone()
                if exists:
                    continue

                stock_code = (disc.get("stock_code") or "").strip()
                corp_name = disc.get("corp_name") or ""
                title = disc.get("report_nm") or ""
                rcept_dt = disc.get("rcept_dt") or today

                # 점수 기반 중요도
                score_info = score_disclosure(title, stock_code)
                total = score_info["total_score"]
                importance = score_info["importance"]
                keywords = score_info["matched_keywords"]

                conn.execute(
                    "INSERT OR IGNORE INTO disclosure_history "
                    "(rcept_no, corp_code, stock_code, corp_name, title, "
                    "importance, keywords_json, rcept_dt, score, alerted) "
                    "VALUES (?,?,?,?,?,?,?,?,?,0)",
                    (rcept_no, disc.get("corp_code"), stock_code, corp_name,
                     title, importance, json.dumps(keywords, ensure_ascii=False),
                     rcept_dt, total),
                )

                # 점수 6점 이상만 알림 (high / critical)
                if total >= 6:
                    if total >= 10:
                        emoji, urgency = "🚨🚨", "긴급"
                    elif total >= 8:
                        emoji, urgency = "🚨", "중요"
                    else:
                        emoji, urgency = "📢", "참고"

                    star = "⭐ <b>[관심종목]</b>\n" if score_info["watchlist_bonus"] else ""

                    msg = f"{emoji} <b>{urgency} 공시</b> (점수 {total})\n\n{star}"
                    msg += f"🏢 <b>{corp_name}</b>"
                    if stock_code:
                        msg += f" ({stock_code})"
                    msg += f"\n\n📄 {title}\n"
                    if keywords:
                        msg += f"🔑 {', '.join(keywords[:4])}\n"

                    # 점수 상세
                    breakdown = f"📊 키워드 {score_info['keyword_score']}"
                    if score_info["watchlist_bonus"]:
                        breakdown += f" + 관심 +{score_info['watchlist_bonus']}"
                    if score_info["cap_bonus"]:
                        breakdown += f" + 대형주 +{score_info['cap_bonus']}"
                    msg += breakdown + "\n"
                    msg += f"\n🔗 https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
                    msg += f"\n⏰ {now_kst().strftime('%H:%M')}"

                    send_telegram(msg)
                    conn.execute(
                        "UPDATE disclosure_history SET alerted=1 WHERE rcept_no=?",
                        (rcept_no,),
                    )
                    new_alerts += 1

            # 30일 이전 이력 정리
            conn.execute(
                "DELETE FROM disclosure_history WHERE rcept_dt < ?",
                ((now_kst() - timedelta(days=30)).strftime("%Y%m%d"),),
            )
            conn.commit()
    except Exception as exc:
        log.warning("[DART] poll_disclosures DB fail: %s", exc)

    if new_alerts:
        log.info("[DART] 중요 공시 %d건 알림", new_alerts)


@app.route("/api/disclosures")
def api_disclosures():
    """최근 공시 리스트. ?importance=critical,high&code=005930&limit=50"""
    if not (_SQLITE_OK and USE_SQLITE):
        return jsonify({"error": "SQLite 비활성화"}), 503

    importance = request.args.get("importance", "").lower()
    code = request.args.get("code", "").strip()
    limit = min(int(request.args.get("limit", "50")), 200)

    sql = "SELECT * FROM disclosure_history WHERE 1=1"
    params: list = []

    if importance:
        levels = [x.strip() for x in importance.split(",") if x.strip()]
        if levels:
            sql += f" AND importance IN ({','.join('?' * len(levels))})"
            params.extend(levels)
    if code:
        sql += " AND stock_code = ?"
        params.append(code)

    sql += " ORDER BY rcept_dt DESC, rcept_no DESC LIMIT ?"
    params.append(limit)

    try:
        with _get_db() as conn:
            rows = conn.execute(sql, params).fetchall()
            items = []
            for r in rows:
                d = dict(r)
                if d.get("keywords_json"):
                    try:
                        d["keywords"] = json.loads(d["keywords_json"])
                    except Exception:
                        d["keywords"] = []
                    del d["keywords_json"]
                d["dart_url"] = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={d.get('rcept_no', '')}"
                items.append(d)
        return jsonify({
            "count": len(items),
            "items": items,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ─────────────────────────────────────────────────────────────────────────
# PHASE 20 — DART 12분기 손익계산서 (슬림)
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


def _apply_live_prices_to_items(items: list) -> int:
    """items 배열의 각 원소 price/change_pct/volume_mn 을 실시간 값으로 패치.
    KR: naver_universe live, US: SQLite stocks.close. 주중이면 실행, 주말이면 skip."""
    if not items:
        return 0
    # 주말에는 기존 전일 종가 유지 (user 요구: 주중만 실시간 반영)
    if now_kst().weekday() >= 5:
        return 0
    uni = _load_naver_universe()
    kr_live = (uni or {}).get("stocks") or {}
    patched = 0
    # US codes 를 DB 한번에 조회
    us_codes = [it.get("code") for it in items
                if it.get("code") and not (it.get("code") or "").isdigit()]
    us_map: dict = {}
    if us_codes and _SQLITE_OK and USE_SQLITE:
        try:
            with _get_db() as conn:
                qmarks = ",".join(["?"] * len(us_codes))
                rows = conn.execute(
                    f"SELECT code, close, change_pct, volume_mn FROM stocks "
                    f"WHERE market='US' AND code IN ({qmarks})", us_codes
                ).fetchall()
                us_map = {r["code"]: dict(r) for r in rows}
        except Exception:
            pass
    for it in items:
        code = it.get("code", "")
        if not code:
            continue
        if code.isdigit() and len(code) == 6:  # KR
            live = kr_live.get(code)
            if live and live.get("close"):
                it["price"] = live["close"]
                it["change_pct"] = live.get("change_pct", it.get("change_pct"))
                it["volume_mn"] = live.get("volume_mn", it.get("volume_mn"))
                patched += 1
        else:  # US
            live = us_map.get(code)
            if live and live.get("close"):
                it["price"] = live["close"]
                it["change_pct"] = live.get("change_pct", it.get("change_pct"))
                it["volume_mn"] = live.get("volume_mn", it.get("volume_mn"))
                patched += 1
    return patched


def _patch_discover_prices(data: dict) -> dict:
    """종목 발굴 결과의 price/change_pct 를 실시간 값으로 패치."""
    items = data.get("items")
    if items:
        _apply_live_prices_to_items(items)
    return data


@app.route("/api/discover")
def api_discover():
    """
    Phase 15 종목 발굴. market ∈ {kr, us, all}.
    Stage 2 결과 (6시간 캐시) 우선, 없으면 Stage 1 (15분 캐시) 폴백.
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
            return jsonify(_patch_discover_prices(d))
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
        d = (_read_fresh_json(cache_dir / "discover_us_stage2.json", 360)
             or _read_fresh_json(cache_dir / "discover_us_stage1.json", 15))
        if d:
            return jsonify(_patch_discover_prices(d))
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
    all_fresh = _read_fresh_json(cache_dir / "discover_all_stage2.json", 360)
    if all_fresh:
        return jsonify(_patch_discover_prices(all_fresh))

    kr_data = (_read_fresh_json(cache_dir / "discover_kr_stage2.json", 360)
               or _read_fresh_json(cache_dir / "discover_kr_stage1.json", 15))
    us_data = (_read_fresh_json(cache_dir / "discover_us_stage2.json", 360)
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
    "last_tick":   None,      # 마지막 progress/phase 변경 시각 (stall 감지용)
}


def _discover_get_state() -> dict:
    with _discover_lock:
        return dict(_discover_state)


def _discover_set(**kw):
    """상태 업데이트. progress/phase가 바뀌면 last_tick도 갱신."""
    with _discover_lock:
        # progress 또는 phase 가 바뀌면 tick 갱신 → stall 감지 타이머 리셋
        prev_prog = _discover_state.get("progress")
        prev_phase = _discover_state.get("phase")
        _discover_state.update(kw)
        new_prog = _discover_state.get("progress")
        new_phase = _discover_state.get("phase")
        if ("progress" in kw and new_prog != prev_prog) or \
           ("phase" in kw and new_phase != prev_phase) or \
           ("status" in kw and kw["status"] in ("starting", "running")):
            _discover_state["last_tick"] = now_kst().strftime("%Y-%m-%d %H:%M:%S")


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

    # v2 보너스: 외국인+기관 동반 매수 (최근 5일 양수 모두 양수)
    if fv and iv:
        frgn_5d = sum(fv[-5:])
        inst_5d = sum(iv[-5:])
        if frgn_5d > 0 and inst_5d > 0:
            sub["alignment"] = 5
            expl.append({
                "label":  "외국인+기관 동반 매수",
                "detail": f"외국인 5일 {_fmt_eok(frgn_5d)} · 기관 {_fmt_eok(inst_5d)}",
                "pts":    5, "max": 5,
            })
        elif frgn_5d < 0 and inst_5d < 0:
            sub["alignment"] = -5
            expl.append({
                "label":  "외국인+기관 동반 매도",
                "detail": f"외국인 5일 {_fmt_eok(frgn_5d)} · 기관 {_fmt_eok(inst_5d)}",
                "pts":    -5, "max": 5,
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
                            sector_avg_ret_20d: float | None,
                            stock_ret_5d: float | None = None,
                            sector_avg_ret_5d: float | None = None) -> tuple[int, list]:
    """
    섹터가 올랐는데 본인은 덜 오른 경우 가산 (0~10).
    v2 안전장치: 5일 수익률이 음수이면 절반.
    v3 안전장치: 5일 RS(종목-섹터)가 -2%p 이하이면 보너스 해제.
    """
    if sector_avg_ret_20d is None or stock_ret_20d is None:
        return 0, [{"label": "덜오른 보너스",
                    "detail": "20일 수익률 데이터 없음",
                    "pts": 0, "max": 10}]

    # v3 안전장치: 5일 RS < -2%p → 보너스 해제 (개별 약세 방치 방지)
    if stock_ret_5d is not None and sector_avg_ret_5d is not None:
        rs_5d = stock_ret_5d - sector_avg_ret_5d
        if rs_5d < -2:
            return 0, [{
                "label":  "덜오른 보너스",
                "detail": f"5일 RS {rs_5d:+.1f}%p (섹터 대비 약세) → 보너스 해제",
                "pts":    0, "max": 10,
            }]

    if sector_avg_ret_20d > 5 and stock_ret_20d < sector_avg_ret_20d * 0.5:
        gap = sector_avg_ret_20d - stock_ret_20d
        pts = (10 if gap > 15 else 7 if gap > 10 else
               5  if gap >  5 else 3 if gap >  3 else 0)
        # v2 안전장치: 5일 모멘텀이 음수 → 아직 반등 안 함 → 절반만
        safety_note = ""
        if stock_ret_5d is not None and stock_ret_5d < 0:
            pts = max(0, pts // 2)
            safety_note = f" (5일 {stock_ret_5d:+.1f}% 약세 → 절반)"
        return pts, [{
            "label":  "덜오른 보너스",
            "detail": (f"섹터 20일 평균 {sector_avg_ret_20d:+.1f}%인데 "
                       f"이 종목은 {stock_ret_20d:+.1f}%. 차이 {gap:.1f}%p{safety_note}"),
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


def _generate_macd_tags(macd_vals: list, macd_sig: list,
                        macd_hist: list, lookback: int = 5) -> list[str]:
    """MACD 기반 태그 세분화 생성.

    - 골든크로스/데드크로스: 최근 lookback일 내 교차 발생 여부
    - 상태 태그: 현재 MACD 위치 기반 지속 조건
    """
    tags: list[str] = []
    n_m = len(macd_vals)
    n_s = len(macd_sig)
    n_h = len(macd_hist)

    # ── 최근 N일 내 골든/데드 크로스 ──
    if n_m >= 2 and n_s >= 2:
        recent_golden = False
        recent_dead = False
        scan = min(lookback, n_m - 1, n_s - 1)
        for i in range(1, scan + 1):
            m_cur, m_prev = macd_vals[-i], macd_vals[-i - 1]
            s_cur, s_prev = macd_sig[-i],  macd_sig[-i - 1]
            if m_prev <= s_prev and m_cur > s_cur:
                recent_golden = True
            if m_prev >= s_prev and m_cur < s_cur:
                recent_dead = True

        if recent_golden:
            tags.append("MACD_골든크로스")
        if recent_dead:
            tags.append("MACD_데드크로스")

    # ── MACD 상태 태그 (지속 조건) ──
    if n_h >= 2:
        h_cur  = macd_hist[-1]
        h_prev = macd_hist[-2]

        # 양전환: 히스토그램이 음→양
        if h_cur > 0 and h_prev <= 0:
            tags.append("MACD_양전환")

        # 양수 구간 세분화
        if h_cur > 0:
            if h_cur > h_prev:
                tags.append("MACD_상승강화")   # 양수 + 확대 중
            else:
                tags.append("MACD_양수유지")   # 양수지만 축소 중

    # 강세구간: MACD > Signal AND MACD > 0
    if n_m >= 1 and n_s >= 1:
        if macd_vals[-1] > 0 and macd_vals[-1] > macd_sig[-1]:
            tags.append("MACD_강세구간")

    return tags


def _stage2_scoring_worker(market: str):
    """백그라운드 진입점. finally 블록으로 상태 고착 절대 방지."""
    _discover_set(
        status="running", phase="stage1", market=market,
        progress=0, total=0, error=None,
        started_at=now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        finished_at=None, message="시작 중…",
    )
    kr_items = None
    us_items = None
    try:
        if market in ("kr", "all"):
            try:
                kr_items = _run_stage2_kr()
            except Exception as exc:
                log.exception("stage2 KR failed")

        if market in ("us", "all"):
            try:
                us_items = _run_stage2_us()
            except Exception as exc:
                log.exception("stage2 US failed")

        if market == "all" and (kr_items or us_items):
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
            try:
                (BASE_DIR / "cache" / "discover_all_stage2.json").write_text(
                    json.dumps(out, ensure_ascii=False), encoding="utf-8"
                )
            except Exception:
                pass

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
    finally:
        # 어떤 상황이든 running 상태 해제 보장
        with _discover_lock:
            if _discover_state["status"] == "running":
                _discover_state["status"] = "done"
                _discover_state["finished_at"] = now_kst().strftime("%Y-%m-%d %H:%M:%S")
                _discover_state["message"] = "완료 (finally)"
                log.warning("[discover] finally 블록에서 running → done 강제 전환")


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

    # 섹터별 20일/5일 평균 수익률 (덜 오른 종목 보너스용)
    sector_rets: dict[str, list[float]] = {}
    sector_rets_5d: dict[str, list[float]] = {}
    for it in candidates:
        sp = sparklines.get(it["code"]) or []
        sect = it["sector"] or "_"
        if len(sp) >= 20 and sp[0]:
            sector_rets.setdefault(sect, []).append(
                (sp[-1] / sp[0] - 1) * 100
            )
        if len(sp) >= 5 and sp[-5]:
            sector_rets_5d.setdefault(sect, []).append(
                (sp[-1] / sp[-5] - 1) * 100
            )
    sector_avg_ret = {k: sum(v) / len(v) for k, v in sector_rets.items() if v}
    sector_avg_ret_5d = {k: sum(v) / len(v) for k, v in sector_rets_5d.items() if v}

    # ── Phase A: 상세 데이터 수집 (SQLite 우선 → HTTP 폴백) ──
    _discover_set(phase="kr_fetch", progress=0, total=total,
                  message=f"🇰🇷 상세 데이터 수집 중 (0/{total})")
    financials: dict[str, dict] = {}
    flows:      dict[str, dict] = {}
    charts:     dict[str, dict] = {}

    today_date = _get_trading_date()
    _fetch_done = [0]  # mutable counter for progress
    _db_hits = [0]
    _http_falls = [0]

    _market_open = is_market_hours()

    def _fetch_kr_single(it):
        code = it["code"]
        fin = flow = chart = {}
        try:
            # SQLite 직접 조회 (HTTP 오버헤드 제거)
            if USE_SQLITE and _SQLITE_OK:
                fin   = _read_financial_db(code) or {}
                flow  = _read_flow_db(code) or {}
                chart = _read_chart_db(code, 180, today_date) or {}
                if chart:
                    _db_hits[0] += 1

            # DB에 없으면 HTTP 폴백 — 장외에는 pykrx 호출 스킵
            if not chart.get("rsi_macd"):
                if _market_open:
                    chart = _call_api_internal(f"/api/chart/{code}") or {}
                _http_falls[0] += 1
            if not fin.get("per") and not fin.get("pbr"):
                if _market_open:
                    fin = _call_api_internal(f"/api/financial/{code}") or {}
            if not flow.get("foreign_value"):
                if _market_open:
                    flow = _call_api_internal(f"/api/flow/{code}") or {}
        except Exception as exc:
            log.debug("stage2 kr fetch fail %s: %s", code, exc)
        _fetch_done[0] += 1
        if _fetch_done[0] % 20 == 0 or _fetch_done[0] == total:
            _discover_set(progress=_fetch_done[0],
                          message=f"🇰🇷 상세 데이터 수집 중 ({_fetch_done[0]}/{total})")
        return code, fin, flow, chart

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_fetch_kr_single, it) for it in candidates]
        for future in as_completed(futures):
            try:
                code, fin, flow, chart = future.result(timeout=30)
                financials[code] = fin
                flows[code] = flow
                charts[code] = chart
            except Exception as exc:
                log.debug("stage2 kr future fail: %s", exc)

    log.info("[Stage2 KR] DB hits=%d, HTTP fallbacks=%d / total=%d",
             _db_hits[0], _http_falls[0], total)

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
    _scoring_total = len(candidates)
    for idx, it in enumerate(candidates):
        # 스코어링 진행률도 tick 갱신 (stall 감지 방어)
        if idx % 100 == 0 or idx == _scoring_total - 1:
            _discover_set(progress=idx + 1, total=_scoring_total,
                          message=f"🇰🇷 스코어링 중 ({idx + 1}/{_scoring_total})")
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
        stock_ret_5d = ((sp[-1] / sp[-5] - 1) * 100) if (len(sp) >= 5 and sp[-5]) else None
        sect_key = it.get("sector") or "_"
        bonus, bonus_expl = _calc_undervalued_bonus(
            stock_ret_20d, sector_avg_ret.get(sect_key),
            stock_ret_5d, sector_avg_ret_5d.get(sect_key),
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

        # ── 외국인 수급 강도 분석 ──
        foreign_tags = []
        if flow and not flow.get("error"):
            fv_arr = flow.get("foreign_value") or []
            iv_arr = flow.get("inst_value") or []
            if fv_arr:
                # 연속 순매수 일수
                f_streak = 0
                for v in reversed(fv_arr):
                    if v > 0:
                        f_streak += 1
                    else:
                        break
                it["details"]["foreign_streak"] = f_streak
                it["details"]["foreign_cum_5d"] = sum(fv_arr[-5:])
                it["details"]["foreign_cum_10d"] = sum(fv_arr[-10:]) if len(fv_arr) >= 10 else sum(fv_arr)
                it["details"]["foreign_today"] = fv_arr[-1] if fv_arr else 0

                # 기관 연속 순매수 일수
                i_streak = 0
                for v in reversed(iv_arr):
                    if v > 0:
                        i_streak += 1
                    else:
                        break
                it["details"]["inst_streak"] = i_streak
                it["details"]["inst_today"] = iv_arr[-1] if iv_arr else 0

                # 태그 생성
                if f_streak >= 5:
                    foreign_tags.append("외국인_5일연속")
                elif f_streak >= 3:
                    foreign_tags.append("외국인_3일연속")

                cum5 = sum(fv_arr[-5:])
                if cum5 > 50_000_000_000:       # 500억 이상
                    foreign_tags.append("외국인_대량매수")
                elif cum5 > 20_000_000_000:      # 200억 이상
                    foreign_tags.append("외국인_집중매수")

                # 외국인 + 기관 동시 매수 (스마트머니)
                if fv_arr[-1] > 0 and iv_arr and iv_arr[-1] > 0:
                    if f_streak >= 2 and i_streak >= 2:
                        foreign_tags.append("쌍끌이_매수")

                it["details"]["foreign_tags"] = foreign_tags

        # RSI/MACD 태그 자동 생성
        rsi_macd_tags = []
        rm = chart.get("rsi_macd") if isinstance(chart, dict) else None
        if rm:
            rsi_vals = rm.get("rsi") or []
            macd_vals = rm.get("macd") or []
            macd_sig  = rm.get("macd_signal") or []
            macd_hist_vals = rm.get("macd_hist") or []
            if rsi_vals:
                rsi_cur = rsi_vals[-1]
                if rsi_cur >= 70:   rsi_macd_tags.append("과매수_RSI")
                elif rsi_cur >= 60: rsi_macd_tags.append("상승진행_RSI")
                elif rsi_cur <= 30: rsi_macd_tags.append("과매도_RSI")
                elif rsi_cur <= 40: rsi_macd_tags.append("과매도회복_RSI")
                it["details"]["rsi"] = round(rsi_cur, 2)
            rsi_macd_tags.extend(_generate_macd_tags(macd_vals, macd_sig, macd_hist_vals))
            # 다이버전스 태그
            divs = rm.get("divergences") or []
            for dv in divs:
                dtype = dv.get("type")
                ind = dv.get("indicator", "")
                if dtype == "bearish":
                    rsi_macd_tags.append(f"베어리시_{ind}_다이버전스")
                elif dtype == "bullish":
                    rsi_macd_tags.append(f"불리시_{ind}_다이버전스")
            it["details"]["divergences"] = divs
        it["details"]["rsi_macd_tags"] = rsi_macd_tags + foreign_tags
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
        "kospi_count":   stage1.get("kospi_count"),
        "kosdaq_count":  stage1.get("kosdaq_count"),
        "items":         candidates,
    }

    out = BASE_DIR / "cache" / "discover_kr_stage2.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    # 추천 이력 스냅샷
    try:
        top10 = sorted(candidates, key=lambda x: x.get("total_score") or 0, reverse=True)[:10]
        save_recommendation_snapshot("discover_kr", top10, market="kr")
    except Exception as exc:
        log.debug("[추천이력] kr 저장 실패: %s", exc)

    # Phase 23: Stage 2 완료 시 텔레그램으로 신규 진입 종목 알림 (silent fail)
    try:
        alert_discovery_new_entries()
    except Exception as exc:
        log.debug("alert_discovery_new_entries failed: %s", exc)

    return candidates


def _run_stage2_us() -> list | None:
    """US Stage 2: S&P500 에서 상위 200종목 yfinance 상세 스코어링."""
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

    today_kst_str = now_kst().strftime("%Y%m%d")
    _us_done = [0]
    _us_db_hits = [0]
    _us_http_falls = [0]

    def _fetch_us_single(it):
        sym = it["code"]
        info = None
        chart = None

        # yinfo: SQLite → JSON file → yfinance API
        if USE_SQLITE and _SQLITE_OK:
            info = _read_yinfo_db(sym)
        if not info:
            info_cache = BASE_DIR / "cache" / f"us_yinfo_{sym}.json"
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
                info = {k: v for k, v in raw.items()
                        if isinstance(v, (int, float, str, bool, type(None)))}
                info_cache = BASE_DIR / "cache" / f"us_yinfo_{sym}.json"
                info_cache.parent.mkdir(exist_ok=True)
                info_cache.write_text(json.dumps(info, ensure_ascii=False),
                                      encoding="utf-8")
                # SQLite 동시 기록
                if USE_SQLITE and _SQLITE_OK:
                    try:
                        with _get_db() as _conn:
                            _conn.execute(
                                "INSERT OR REPLACE INTO yinfo_cache (symbol, info_json) VALUES (?,?)",
                                (sym, json.dumps(info, ensure_ascii=False)),
                            )
                            _conn.commit()
                    except Exception:
                        pass
            except Exception as exc:
                log.debug("us yfinance info fail %s: %s", sym, exc)
                info = {}

        # chart: SQLite → HTTP 폴백 (장외 시 API 호출 스킵)
        if USE_SQLITE and _SQLITE_OK:
            chart = _read_chart_db(sym, 180, today_kst_str)
            if chart:
                _us_db_hits[0] += 1
        if not chart or not chart.get("rsi_macd"):
            if _is_us_market_hours():
                chart = _call_api_internal(f"/api/us/chart/{sym}") or {}
            else:
                chart = chart or {}
            _us_http_falls[0] += 1

        closes = (chart.get("close") if isinstance(chart, dict) else None) or []
        r20 = None
        if len(closes) >= 20 and closes[-20]:
            r20 = (closes[-1] / closes[-20] - 1) * 100

        _us_done[0] += 1
        if _us_done[0] % 20 == 0 or _us_done[0] == total:
            _discover_set(progress=_us_done[0],
                          message=f"🇺🇸 yfinance 상세 수집 중 ({_us_done[0]}/{total})")
        return sym, info, chart, r20

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_fetch_us_single, it) for it in candidates]
        for future in as_completed(futures):
            try:
                sym, info, chart, r20 = future.result(timeout=30)
                infos[sym] = info
                charts[sym] = chart
                if r20 is not None:
                    ret_20d[sym] = r20
            except Exception as exc:
                log.debug("stage2 us future fail: %s", exc)

    log.info("[Stage2 US] DB hits=%d, HTTP fallbacks=%d / total=%d",
             _us_db_hits[0], _us_http_falls[0], total)

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
        # US RSI/MACD 태그
        us_rsi_tags = []
        rm = chart.get("rsi_macd") if isinstance(chart, dict) else None
        if rm:
            rsi_vals = rm.get("rsi") or []
            macd_vals = rm.get("macd") or []
            macd_sig  = rm.get("macd_signal") or []
            macd_hist_vals = rm.get("macd_hist") or []
            if rsi_vals:
                rsi_cur = rsi_vals[-1]
                if rsi_cur >= 70:   us_rsi_tags.append("과매수_RSI")
                elif rsi_cur >= 60: us_rsi_tags.append("상승진행_RSI")
                elif rsi_cur <= 30: us_rsi_tags.append("과매도_RSI")
                elif rsi_cur <= 40: us_rsi_tags.append("과매도회복_RSI")
                it["details"]["rsi"] = round(rsi_cur, 2)
            us_rsi_tags.extend(_generate_macd_tags(macd_vals, macd_sig, macd_hist_vals))
            divs = rm.get("divergences") or []
            for dv in divs:
                dtype = dv.get("type")
                ind = dv.get("indicator", "")
                if dtype == "bearish":
                    us_rsi_tags.append(f"베어리시_{ind}_다이버전스")
                elif dtype == "bullish":
                    us_rsi_tags.append(f"불리시_{ind}_다이버전스")
            it["details"]["divergences"] = divs
        it["details"]["rsi_macd_tags"] = us_rsi_tags

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

    # 추천 이력 스냅샷
    try:
        top10 = sorted(candidates, key=lambda x: x.get("total_score") or 0, reverse=True)[:10]
        save_recommendation_snapshot("discover_us", top10, market="us")
    except Exception as exc:
        log.debug("[추천이력] us 저장 실패: %s", exc)

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
    """현재 Stage 2 스캔 진행 상태.
    Stall detection: 마지막 progress/phase 변경 이후 120초 무변화 시 고착 판정.
    절대 시간 제한: 15분 (fetch/scoring 합산 worst case)."""
    state = _discover_get_state()
    if state["status"] in ("starting", "running"):
        try:
            from datetime import datetime as _dt
            now_naive = now_kst().replace(tzinfo=None)

            # 1) 절대 시간 제한 (15분 — US yfinance 최악 케이스 고려)
            started = _dt.strptime(state["started_at"], "%Y-%m-%d %H:%M:%S") \
                if state.get("started_at") else None
            elapsed = (now_naive - started).total_seconds() if started else 0

            # 2) stall 감지 (마지막 진행 이후 120초 무변화)
            tick_str = state.get("last_tick") or state.get("started_at")
            tick = _dt.strptime(tick_str, "%Y-%m-%d %H:%M:%S") if tick_str else None
            stall = (now_naive - tick).total_seconds() if tick else 0

            if elapsed > 900:
                _discover_set(
                    status="error", phase=None,
                    error=f"15분 초과 (총 {int(elapsed)}초) — 자동 리셋",
                    finished_at=now_kst().strftime("%Y-%m-%d %H:%M:%S"),
                )
                state = _discover_get_state()
                log.warning("[discover] 15분 초과 → 자동 리셋")
            elif stall > 120:
                _discover_set(
                    status="error", phase=None,
                    error=f"진행 정체 감지 ({int(stall)}초 무변화) — 자동 리셋",
                    finished_at=now_kst().strftime("%Y-%m-%d %H:%M:%S"),
                )
                state = _discover_get_state()
                log.warning("[discover] stall 감지 (%d초) → 자동 리셋", int(stall))
        except Exception as exc:
            log.debug("[discover] progress timeout check: %s", exc)
    return jsonify(state)


@app.route("/api/discover/reset", methods=["POST"])
def api_discover_reset():
    """Stage 2 상태 강제 리셋."""
    _discover_set(
        status="idle", phase=None, progress=0, total=0,
        error=None, message=None,
        finished_at=now_kst().strftime("%Y-%m-%d %H:%M:%S"),
    )
    log.info("[discover] 수동 강제 리셋")
    return jsonify({"status": "reset"})


# ─────────────────────────────────────────────────────────────────────────
# AI 에이전트 파이프라인 (규칙 기반, 비용 $0)
# ─────────────────────────────────────────────────────────────────────────
_agent_running = [False]


@app.route("/api/agent/run", methods=["POST"])
def api_agent_run():
    """에이전트 파이프라인 백그라운드 실행. market=kr|us|all"""
    if _agent_running[0]:
        return jsonify({"status": "already_running"})
    market = request.args.get("market", "kr")
    if request.is_json:
        market = (request.get_json(silent=True) or {}).get("market", market)
    if market not in ("kr", "us", "all"):
        market = "kr"

    def _run():
        _agent_running[0] = True
        try:
            from agents.pipeline import run_pipeline, send_agent_telegram
            result = run_pipeline(market=market)
            # 추천 이력 스냅샷
            try:
                if market == "all" and isinstance(result, dict) and "kr" in result:
                    save_recommendation_snapshot(
                        "agent_kr", (result["kr"] or {}).get("final_picks") or [],
                        market="kr")
                    save_recommendation_snapshot(
                        "agent_us", (result["us"] or {}).get("final_picks") or [],
                        market="us")
                elif isinstance(result, dict):
                    src = f"agent_{market}"
                    save_recommendation_snapshot(
                        src, result.get("final_picks") or [], market=market)
            except Exception as exc:
                log.debug("[추천이력] agent 저장 실패: %s", exc)
            try:
                # all 결과는 kr/us 중첩이라 각각 발송
                if market == "all" and isinstance(result, dict) and "kr" in result:
                    send_agent_telegram(result["kr"])
                    send_agent_telegram(result["us"])
                else:
                    send_agent_telegram(result)
            except Exception as exc:
                log.debug("[Agent] 텔레그램 발송 실패: %s", exc)
        except Exception as exc:
            log.exception("[Agent] 파이프라인 실패: %s", exc)
        finally:
            _agent_running[0] = False

    threading.Thread(target=_run, daemon=True, name="agent-pipeline").start()
    return jsonify({"status": "started", "market": market})


@app.route("/api/agent/result")
def api_agent_result():
    """최신 에이전트 결과 조회. ?market=kr|us.
    주중 조회 시 final_picks의 price/change_pct를 실시간 값으로 overlay."""
    market = (request.args.get("market") or "kr").lower()
    fname = "agent_result_us_latest.json" if market == "us" else "agent_result_latest.json"
    path = BASE_DIR / "cache" / fname
    if not path.exists():
        fallback = BASE_DIR / "cache" / "agent_result_latest.json"
        if fallback.exists() and market != "us":
            path = fallback
        else:
            return jsonify({"error": "결과 없음 — 파이프라인을 먼저 실행하세요"}), 404
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # final_picks 실시간 overlay (주중만)
        patched = _apply_live_prices_to_items(data.get("final_picks") or [])
        if patched:
            data["_live_overlay"] = {"patched": patched,
                                     "at": now_kst().strftime("%Y-%m-%d %H:%M:%S")}
        return jsonify(data)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/agent/status")
def api_agent_status():
    """에이전트 실행 상태."""
    return jsonify({"running": _agent_running[0]})


# ─────────────────────────────────────────────────────────────────────────
# 백테스트 엔진 (규칙 기반, OHLCV 기반)
# ─────────────────────────────────────────────────────────────────────────

def _bt_calc_rsi(closes: list, period: int = 14):
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    recent = deltas[-period:]
    gains = [d for d in recent if d > 0]
    losses = [-d for d in recent if d < 0]
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0.001
    rs = avg_gain / avg_loss if avg_loss else 999
    return 100 - 100 / (1 + rs)


def _bt_check_entry(ohlcv_slice, strategy, tags, tag_logic, min_score):
    """과거 OHLCV로 진입 신호 판단."""
    if len(ohlcv_slice) < 30:
        return None
    closes = [r["close"] for r in ohlcv_slice if r.get("close")]
    volumes = [r["volume"] or 0 for r in ohlcv_slice]
    if len(closes) < 30:
        return None

    rsi = _bt_calc_rsi(closes)
    ma20 = sum(closes[-20:]) / 20
    ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else ma20
    avg_vol = sum(volumes[-20:]) / 20 if volumes else 1
    vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1
    cur = closes[-1]
    prev = closes[-2] if len(closes) > 1 else cur

    tag_matches: set = set()
    if cur > ma20 and prev <= ma20:
        tag_matches.add("MA20돌파")
    if rsi is not None and rsi < 30:
        tag_matches.add("RSI과매도")
    if rsi is not None and rsi > 70:
        tag_matches.add("RSI과매수")
    if vol_ratio >= 2:
        tag_matches.add("거래량급증")
    if cur > ma20 > ma60:
        tag_matches.add("정배열")
    if abs(cur - ma20) / ma20 < 0.02 and cur > ma20:
        tag_matches.add("돌파임박")

    if strategy == "tag" and tags:
        if tag_logic == "AND":
            if all(t in tag_matches for t in tags):
                return {"tags": list(tag_matches)}
        else:
            if any(t in tag_matches for t in tags):
                return {"tags": list(tag_matches)}

    elif strategy == "score":
        score = 0
        if cur > ma20:                      score += 20
        if ma20 > ma60:                     score += 20
        if rsi is not None and 30 < rsi < 65: score += 20
        if vol_ratio > 1.2:                 score += 20
        if cur > prev:                      score += 20
        if score >= min_score:
            return {"score": score, "tags": list(tag_matches)}

    elif strategy == "combined":
        score = 0
        if cur > ma20:                      score += 15
        if ma20 > ma60:                     score += 15
        if rsi is not None and 30 < rsi < 65: score += 15
        if vol_ratio > 1.2:                 score += 15
        tag_hit = any(t in tag_matches for t in tags) if tags else True
        if score >= min_score and tag_hit:
            return {"score": score, "tags": list(tag_matches)}

    return None


@app.route("/api/backtest/available_tags")
def api_backtest_available_tags():
    """discover_results에서 실제 사용된 Stage 2 태그 목록 반환."""
    tags: set = set()
    if _SQLITE_OK and USE_SQLITE:
        try:
            with _get_db() as conn:
                rows = conn.execute(
                    "SELECT items_json FROM discover_results "
                    "WHERE items_json IS NOT NULL ORDER BY updated_at DESC LIMIT 10"
                ).fetchall()
            for r in rows:
                try:
                    items = json.loads(r["items_json"] or "[]")
                    for it in items:
                        for t in (it.get("details") or {}).get("rsi_macd_tags") or []:
                            tags.add(t)
                except Exception:
                    pass
        except Exception:
            pass

    # 코드 기반 폴백 (discover 이력 없을 때)
    if not tags:
        tags = {
            "MACD_골든크로스", "MACD_데드크로스", "MACD_양전환", "MACD_강세구간",
            "MACD_상승강화", "MACD_양수유지",
            "과매수_RSI", "과매도_RSI", "상승진행_RSI", "과매도회복_RSI",
            "외국인_3일연속", "외국인_5일연속", "외국인_집중매수", "외국인_대량매수",
            "쌍끌이_매수",
            "베어리시_RSI_다이버전스", "베어리시_MACD_다이버전스",
            "불리시_RSI_다이버전스", "불리시_MACD_다이버전스",
        }
    return jsonify({"tags": sorted(tags)})


@app.route("/api/backtest", methods=["POST"])
def _bt_check_stage2_entry(item, strategy, tags, tag_logic, min_score):
    """Stage 2 item(discover_results) 기반 진입 신호 판단."""
    score = item.get("total_score") or 0
    item_tags = (item.get("details") or {}).get("rsi_macd_tags") or []

    if strategy == "tag" and tags:
        if tag_logic == "AND":
            return all(t in item_tags for t in tags)
        return any(t in item_tags for t in tags)
    if strategy == "score":
        return score >= min_score
    if strategy == "combined":
        tag_ok = any(t in item_tags for t in tags) if tags else True
        return score >= min_score and tag_ok
    return False


def _bt_simulate_trade(conn, code, name, entry_date_str, hold_days, stop_loss, take_profit):
    """OHLCV에서 진입일 이후 hold_days 수익률 시뮬."""
    rows = conn.execute(
        "SELECT date, open, high, low, close FROM ohlcv "
        "WHERE code = ? AND date >= ? ORDER BY date ASC LIMIT ?",
        (code, entry_date_str, hold_days + 2),
    ).fetchall()
    if len(rows) < 2:
        return None
    entry_price = rows[1]["open"] or rows[1]["close"] or 0
    entry_date = rows[1]["date"]
    if entry_price <= 0:
        return None

    exit_price = entry_price
    exit_date = entry_date
    exit_reason = "hold"
    for row in rows[1:]:
        high, low, close = row["high"], row["low"], row["close"]
        if not all([high, low, close]):
            continue
        low_pct = (low / entry_price - 1) * 100
        high_pct = (high / entry_price - 1) * 100
        if low_pct <= stop_loss:
            exit_price = entry_price * (1 + stop_loss / 100)
            exit_date = row["date"]; exit_reason = "stop_loss"; break
        if high_pct >= take_profit:
            exit_price = entry_price * (1 + take_profit / 100)
            exit_date = row["date"]; exit_reason = "take_profit"; break
        exit_price = close
        exit_date = row["date"]

    pnl_pct = round((exit_price / entry_price - 1) * 100, 2)
    return {
        "code": code, "name": name,
        "entry_date": entry_date, "entry_price": round(entry_price),
        "exit_date": exit_date, "exit_price": round(exit_price),
        "pnl_pct": pnl_pct, "exit_reason": exit_reason,
    }


def api_backtest():
    """태그/스코어 기반 전략 백테스트.
    우선순위: discover_results 과거 스냅샷 → OHLCV 룰 기반 폴백.
    """
    if not (_SQLITE_OK and USE_SQLITE):
        return jsonify({"error": "SQLite 비활성"}), 503

    data = request.get_json(silent=True) or {}
    strategy = data.get("strategy", "score")
    tags = data.get("tags") or []
    tag_logic = data.get("tag_logic", "OR")
    min_score = int(data.get("min_score", 70))
    hold_days = int(data.get("hold_days", 5))
    lookback_days = int(data.get("lookback_days", 60))
    stop_loss = float(data.get("stop_loss", -5))
    take_profit = float(data.get("take_profit", 10))
    max_stocks = int(data.get("max_stocks", 500))

    from datetime import timedelta as _td
    start_date = (now_kst() - _td(days=lookback_days)).strftime("%Y-%m-%d")

    trades: list = []
    source_used = "ohlcv_rules"

    # 1순위: discover_results 과거 스냅샷 재사용 (Stage 2 실제 태그/점수)
    try:
        with _get_db() as conn:
            snapshots = conn.execute(
                "SELECT updated_at, items_json FROM discover_results "
                "WHERE market='kr' AND items_json IS NOT NULL "
                "AND DATE(updated_at) >= DATE(?) "
                "ORDER BY updated_at ASC", (start_date,)
            ).fetchall()

            snap_count = len(snapshots)
            if snap_count >= 1:
                source_used = "discover_history"
                seen: set = set()  # (code, date) 중복 방지
                for snap in snapshots:
                    snap_date = (snap["updated_at"] or "")[:10]
                    try:
                        items = json.loads(snap["items_json"] or "[]")
                    except Exception:
                        continue
                    for it in items:
                        code = it.get("code")
                        name = it.get("name")
                        if not code or (code, snap_date) in seen:
                            continue
                        if not _bt_check_stage2_entry(it, strategy, tags, tag_logic, min_score):
                            continue
                        seen.add((code, snap_date))
                        t = _bt_simulate_trade(conn, code, name, snap_date,
                                               hold_days, stop_loss, take_profit)
                        if t:
                            t["signal"] = {
                                "score": it.get("total_score"),
                                "tags": (it.get("details") or {}).get("rsi_macd_tags", [])[:5],
                            }
                            trades.append(t)
                log.info("[Backtest] discover 스냅샷 %d개 → %d trades",
                         snap_count, len(trades))
    except Exception as exc:
        log.debug("[Backtest] discover 방식 실패: %s", exc)

    # 2순위: OHLCV 룰 기반 폴백 (discover_results 없거나 trades 0건일 때)
    if not trades:
        try:
            with _get_db() as conn:
                # 대상: KR 일반 종목, ETF 제외
                codes_rows = conn.execute(
                    "SELECT code, name FROM stocks "
                    "WHERE (market = '' OR market LIKE 'KOS%') "
                    "AND COALESCE(is_etf, 0) = 0 "
                    "ORDER BY COALESCE(market_cap, 0) DESC, volume_mn DESC "
                    "LIMIT ?", (max_stocks,)
                ).fetchall()
                codes_map = {r["code"]: r["name"] for r in codes_rows}

                for code, name in codes_map.items():
                    rows = conn.execute(
                        "SELECT date, open, high, low, close, volume "
                        "FROM ohlcv WHERE code=? ORDER BY date ASC", (code,)
                    ).fetchall()
                    ohlcv = [dict(r) for r in rows]
                    if len(ohlcv) < 60:
                        continue

                    for i in range(30, len(ohlcv) - hold_days):
                        date_str = ohlcv[i]["date"]
                        if date_str < start_date:
                            continue

                        signal = _bt_check_entry(
                            ohlcv[:i + 1], strategy, tags, tag_logic, min_score
                        )
                        if not signal:
                            continue

                        entry_price = ohlcv[i + 1]["open"] or ohlcv[i + 1]["close"]
                        if not entry_price or entry_price <= 0:
                            continue
                        entry_date = ohlcv[i + 1]["date"]

                        exit_price = entry_price
                        exit_date = entry_date
                        exit_reason = "hold"

                        for j in range(i + 1, min(i + 1 + hold_days, len(ohlcv))):
                            bar = ohlcv[j]
                            high, low, close = bar.get("high"), bar.get("low"), bar.get("close")
                            if not all([high, low, close]):
                                continue
                            low_pct = (low / entry_price - 1) * 100
                            high_pct = (high / entry_price - 1) * 100

                            if low_pct <= stop_loss:
                                exit_price = entry_price * (1 + stop_loss / 100)
                                exit_date = bar["date"]
                                exit_reason = "stop_loss"
                                break
                            if high_pct >= take_profit:
                                exit_price = entry_price * (1 + take_profit / 100)
                                exit_date = bar["date"]
                                exit_reason = "take_profit"
                                break
                            exit_price = close
                            exit_date = bar["date"]

                        pnl_pct = round((exit_price / entry_price - 1) * 100, 2)
                        trades.append({
                            "code": code, "name": name,
                            "entry_date": entry_date, "entry_price": round(entry_price),
                            "exit_date": exit_date, "exit_price": round(exit_price),
                            "pnl_pct": pnl_pct, "exit_reason": exit_reason,
                            "signal": signal,
                        })
        except Exception as exc:
            log.exception("backtest failed")
            return jsonify({"error": str(exc)}), 500

    # 요약
    summary = {}
    if trades:
        pnls = [t["pnl_pct"] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        summary = {
            "total_trades": len(trades),
            "win_rate": round(len(wins) / len(trades) * 100, 1),
            "avg_pnl": round(sum(pnls) / len(pnls), 2),
            "median_pnl": round(sorted(pnls)[len(pnls) // 2], 2),
            "max_win": round(max(pnls), 2),
            "max_loss": round(min(pnls), 2),
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
            "profit_factor": round(abs(sum(wins) / sum(losses)), 2) if losses and sum(losses) else 999,
            "stop_loss_count": len([t for t in trades if t["exit_reason"] == "stop_loss"]),
            "take_profit_count": len([t for t in trades if t["exit_reason"] == "take_profit"]),
            "hold_count": len([t for t in trades if t["exit_reason"] == "hold"]),
        }

    trades.sort(key=lambda x: x["entry_date"], reverse=True)
    return jsonify({
        "strategy": strategy, "tags": tags, "tag_logic": tag_logic,
        "min_score": min_score, "hold_days": hold_days,
        "lookback_days": lookback_days,
        "stop_loss": stop_loss, "take_profit": take_profit,
        "source": source_used,
        "trades": trades[:200], "summary": summary,
    })


# ── 상관관계 매트릭스 ────────────────────────────────────
def _load_server_portfolio_codes() -> list:
    """포트폴리오 종목 코드 목록 반환 (market 무관)."""
    f = BASE_DIR / "cache" / "server_portfolio.json"
    if not f.exists():
        return []
    try:
        pf = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for p in pf.get("positions") or []:
        c = p.get("code")
        if c:
            out.append({"code": c, "name": p.get("name") or c,
                        "market": p.get("market", "kr")})
    return out


def _load_watchlist_codes() -> list:
    items = _load_server_watchlist() or []
    out = []
    for w in items:
        c = w.get("code")
        if c:
            out.append({"code": c, "name": w.get("name") or c,
                        "market": w.get("market", "kr")})
    return out


def _compute_correlation(closes_by_code: dict, codes: list) -> dict:
    """closes_by_code = {code: [close_1, close_2, ...]} (길이 정렬 필수).
    Pearson correlation of daily returns."""
    import math
    # 일간 수익률 계산
    rets = {}
    for c in codes:
        cl = closes_by_code.get(c) or []
        if len(cl) < 2:
            continue
        r = []
        for i in range(1, len(cl)):
            if cl[i - 1] and cl[i - 1] > 0:
                r.append((cl[i] / cl[i - 1]) - 1)
            else:
                r.append(0.0)
        rets[c] = r

    valid = [c for c in codes if c in rets]
    min_len = min((len(rets[c]) for c in valid), default=0)
    if min_len < 10:
        return {"codes": valid, "matrix": []}
    for c in valid:
        rets[c] = rets[c][-min_len:]

    # Pearson
    matrix = []
    for a in valid:
        row = []
        ra = rets[a]; ma = sum(ra) / len(ra)
        for b in valid:
            if a == b:
                row.append(1.0); continue
            rb = rets[b]; mb = sum(rb) / len(rb)
            num = sum((ra[i] - ma) * (rb[i] - mb) for i in range(min_len))
            dena = math.sqrt(sum((ra[i] - ma) ** 2 for i in range(min_len)))
            denb = math.sqrt(sum((rb[i] - mb) ** 2 for i in range(min_len)))
            den = dena * denb
            row.append(round(num / den, 3) if den > 0 else 0.0)
        matrix.append(row)
    return {"codes": valid, "matrix": matrix}


@app.route("/api/correlation")
def api_correlation():
    """포트폴리오 + 관심종목 기반 상관관계 매트릭스 (최근 60일 OHLCV).
    source=portfolio|watchlist|both (기본 both)."""
    if not (_SQLITE_OK and USE_SQLITE):
        return jsonify({"error": "SQLite 비활성"}), 503

    source = request.args.get("source", "both")
    days = int(request.args.get("days", 60))

    items: list = []
    seen: set = set()
    if source in ("portfolio", "both"):
        for it in _load_server_portfolio_codes():
            if it["code"] not in seen:
                items.append(it); seen.add(it["code"])
    if source in ("watchlist", "both"):
        for it in _load_watchlist_codes():
            if it["code"] not in seen:
                items.append(it); seen.add(it["code"])

    if len(items) < 2:
        return jsonify({"error": "분석할 종목이 2개 미만입니다",
                        "codes": [c["code"] for c in items]}), 400

    # OHLCV 종가 로드 (SQLite 우선, 미국 종목은 fallback)
    closes_by_code: dict = {}
    name_by_code: dict = {}
    try:
        with _get_db() as conn:
            for it in items:
                code = it["code"]
                rows = conn.execute(
                    "SELECT close FROM ohlcv WHERE code=? "
                    "ORDER BY date DESC LIMIT ?", (code, days)
                ).fetchall()
                closes = [r["close"] for r in rows if r["close"]]
                if closes:
                    closes_by_code[code] = list(reversed(closes))
                    name_by_code[code] = it["name"]
    except Exception as exc:
        log.exception("correlation ohlcv load")
        return jsonify({"error": str(exc)}), 500

    codes = list(closes_by_code.keys())
    if len(codes) < 2:
        return jsonify({"error": "OHLCV 데이터가 부족한 종목이 많습니다",
                        "loaded": codes}), 400

    result = _compute_correlation(closes_by_code, codes)
    result["names"] = [name_by_code.get(c, c) for c in result["codes"]]
    result["source"] = source
    result["days"] = days
    return jsonify(result)


# ── 추천 성과 검증 (recommendation_history) ────────────────────────────────────
def _init_recommendation_history():
    """추천 이력 테이블 생성. _startup에서 호출."""
    if not (_SQLITE_OK and USE_SQLITE):
        return
    try:
        with _get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS recommendation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    source TEXT NOT NULL,
                    rank INTEGER,
                    code TEXT NOT NULL,
                    name TEXT,
                    market TEXT,
                    score REAL,
                    price_at_rec REAL,
                    tags_json TEXT,
                    created_at TEXT DEFAULT (datetime('now', 'localtime')),
                    UNIQUE(date, source, code)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rec_date ON recommendation_history(date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rec_source ON recommendation_history(source, date DESC)")
            conn.commit()
        log.info("[추천이력] 테이블 초기화")
    except Exception as exc:
        log.warning("[추천이력] 테이블 생성 실패: %s", exc)


def save_recommendation_snapshot(source: str, picks: list, market: str = "kr",
                                 limit: int = 10) -> int:
    """추천 결과 상위 limit 종목을 recommendation_history에 저장.
    source: discover_kr | discover_us | agent_kr | agent_us"""
    if not (_SQLITE_OK and USE_SQLITE) or not picks:
        return 0
    today = now_kst().strftime("%Y-%m-%d")
    saved = 0
    try:
        with _get_db() as conn:
            for rank, pick in enumerate((picks or [])[:limit], 1):
                code = (pick.get("code") or "").strip()
                if not code:
                    continue
                name = pick.get("name") or code
                score = pick.get("total_score") or pick.get("score") or 0
                tags = pick.get("tags") or pick.get("swing_tags") or []
                if not tags:
                    det = pick.get("details") or {}
                    tags = det.get("rsi_macd_tags") or []
                price = pick.get("price") or pick.get("close") or 0
                if not price:
                    r = conn.execute(
                        "SELECT close FROM stocks WHERE code = ?", (code,)
                    ).fetchone()
                    if r:
                        price = r["close"] or 0
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO recommendation_history "
                        "(date, source, rank, code, name, market, score, price_at_rec, tags_json) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (today, source, rank, code, name, market, score, price,
                         json.dumps(tags, ensure_ascii=False))
                    )
                    if conn.total_changes > 0:
                        saved += 1
                except Exception:
                    pass
            conn.commit()
        log.info("[추천이력] %s (%s) %d/%d 저장", source, today, saved, min(len(picks), limit))
    except Exception as exc:
        log.debug("[추천이력] save 실패: %s", exc)
    return saved


def migrate_discover_to_recommendations() -> dict:
    """discover_results 에 있는 과거 스냅샷을 recommendation_history로 소급 저장."""
    if not (_SQLITE_OK and USE_SQLITE):
        return {"error": "SQLite 비활성"}
    total = 0; snapshots = 0
    try:
        with _get_db() as conn:
            rows = conn.execute("""
                SELECT market, items_json, DATE(updated_at) as day
                FROM discover_results
                WHERE items_json IS NOT NULL
                ORDER BY updated_at ASC
            """).fetchall()
            for row in rows:
                day = row["day"]
                mkt = (row["market"] or "").lower()
                if mkt not in ("kr", "us"):
                    continue
                source = f"discover_{mkt}"
                try:
                    items = json.loads(row["items_json"] or "[]")
                except Exception:
                    continue
                items.sort(key=lambda x: x.get("total_score") or 0, reverse=True)
                for rank, it in enumerate(items[:10], 1):
                    code = (it.get("code") or "").strip()
                    if not code:
                        continue
                    name = it.get("name") or code
                    score = it.get("total_score") or 0
                    tags = (it.get("details") or {}).get("rsi_macd_tags") or []
                    price = it.get("price") or 0
                    if not price:
                        pr = conn.execute(
                            "SELECT close FROM ohlcv WHERE code = ? AND date <= ? "
                            "ORDER BY date DESC LIMIT 1", (code, day)
                        ).fetchone()
                        if pr:
                            price = pr["close"] or 0
                    try:
                        conn.execute(
                            "INSERT OR IGNORE INTO recommendation_history "
                            "(date, source, rank, code, name, market, score, price_at_rec, tags_json) "
                            "VALUES (?,?,?,?,?,?,?,?,?)",
                            (day, source, rank, code, name, mkt, score, price,
                             json.dumps(tags, ensure_ascii=False))
                        )
                        if conn.total_changes > 0:
                            total += 1
                    except Exception:
                        pass
                snapshots += 1
            conn.commit()
    except Exception as exc:
        log.warning("[마이그레이션] %s", exc)
    log.info("[추천이력] discover 스냅샷 %d개 → %d건 소급", snapshots, total)
    return {"snapshots": snapshots, "inserted": total}


@app.route("/api/recommendation/performance")
def api_recommendation_performance():
    """추천 시점 대비 D+1 ~ D+7 수익률.
    쿼리: source=discover_kr|discover_us|agent_kr|agent_us, days=7"""
    if not (_SQLITE_OK and USE_SQLITE):
        return jsonify({"error": "SQLite 비활성"}), 503
    source = request.args.get("source", "discover_kr")
    days = int(request.args.get("days", 7))
    from datetime import timedelta as _td
    cutoff = (now_kst() - _td(days=days)).strftime("%Y-%m-%d")

    out = {
        "source": source, "lookback_days": days,
        "daily_snapshots": [], "overall": {},
    }
    all_pnls: list = []
    try:
        with _get_db() as conn:
            dates = conn.execute(
                "SELECT DISTINCT date FROM recommendation_history "
                "WHERE source = ? AND date >= ? ORDER BY date DESC",
                (source, cutoff)
            ).fetchall()
            for dr in dates:
                rec_date = dr["date"]
                picks = conn.execute(
                    "SELECT rank, code, name, market, score, price_at_rec, tags_json "
                    "FROM recommendation_history "
                    "WHERE source = ? AND date = ? ORDER BY rank ASC",
                    (source, rec_date)
                ).fetchall()
                day = {"date": rec_date, "picks": []}
                day_pnls: list = []
                for p in picks:
                    code = p["code"]; rec_price = p["price_at_rec"] or 0
                    if rec_price <= 0:
                        continue
                    ohlcv = conn.execute(
                        "SELECT date, close FROM ohlcv "
                        "WHERE code = ? AND date > ? ORDER BY date ASC LIMIT 7",
                        (code, rec_date)
                    ).fetchall()
                    cur_row = conn.execute(
                        "SELECT close FROM stocks WHERE code = ?", (code,)
                    ).fetchone()
                    cur_price = (cur_row["close"] if cur_row else 0) or 0
                    daily_pnl: list = []
                    for o in ohlcv:
                        if o["close"] and rec_price > 0:
                            pct = round((o["close"] / rec_price - 1) * 100, 2)
                            daily_pnl.append({"date": o["date"],
                                              "close": o["close"], "pnl_pct": pct})
                    if daily_pnl:
                        final_pnl = daily_pnl[-1]["pnl_pct"]
                    elif cur_price > 0:
                        final_pnl = round((cur_price / rec_price - 1) * 100, 2)
                    else:
                        final_pnl = 0.0
                    try:
                        tags = json.loads(p["tags_json"] or "[]")
                    except Exception:
                        tags = []
                    day["picks"].append({
                        "rank": p["rank"], "code": code, "name": p["name"],
                        "market": p["market"], "score": p["score"],
                        "rec_price": rec_price, "current_price": cur_price,
                        "final_pnl": final_pnl,
                        "d1_pnl": daily_pnl[0]["pnl_pct"] if daily_pnl else None,
                        "sparkline": [d["pnl_pct"] for d in daily_pnl],
                        "daily_pnl": daily_pnl, "tags": tags,
                    })
                    day_pnls.append(final_pnl)
                    all_pnls.append(final_pnl)
                day["avg_pnl"] = round(sum(day_pnls) / len(day_pnls), 2) if day_pnls else 0
                day["win_count"] = sum(1 for p in day_pnls if p > 0)
                day["total_count"] = len(day_pnls)
                out["daily_snapshots"].append(day)
        if all_pnls:
            wins = [p for p in all_pnls if p > 0]
            out["overall"] = {
                "total_picks": len(all_pnls),
                "avg_pnl": round(sum(all_pnls) / len(all_pnls), 2),
                "win_rate": round(len(wins) / len(all_pnls) * 100, 1),
                "max_win": round(max(all_pnls), 2),
                "max_loss": round(min(all_pnls), 2),
                "total_return": round(sum(all_pnls), 2),
            }
    except Exception as exc:
        log.exception("recommendation performance")
        return jsonify({"error": str(exc)}), 500
    return jsonify(out)


@app.route("/api/recommendation/migrate", methods=["POST"])
def api_recommendation_migrate():
    return jsonify(migrate_discover_to_recommendations())


# ── 장마감 시황 자동 요약 (순수 데이터 기반) ────────────────────────────────────
def _fmt_cap(cap):
    if not cap or cap <= 0:
        return ""
    try:
        if cap >= 1e12:
            return f" [{cap / 1e12:.1f}조]"
        if cap >= 1e8:
            return f" [{cap / 1e8:.0f}억]"
    except Exception:
        pass
    return ""


def _parse_json_list(s):
    if not s:
        return []
    try:
        return json.loads(s)
    except Exception:
        return []


def build_market_summary() -> dict:
    """매크로·섹터·특징주·수급·공시·AI 섹션을 DB/캐시에서 집계."""
    summary = {
        "generated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "sections": [],
    }

    # ── 1. 지수 ──
    idx_section = {"title": "📈 지수", "items": []}
    try:
        dj = json.loads((BASE_DIR / "data.json").read_text(encoding="utf-8")) \
            if (BASE_DIR / "data.json").exists() else {}
    except Exception:
        dj = {}
    mo = (dj.get("market_overview") or {})
    for name, obj in (
        ("KOSPI", dj.get("kospi")),
        ("KOSDAQ", dj.get("kosdaq")),
        ("S&P 500", mo.get("sp500")),
        ("NASDAQ", mo.get("nasdaq")),
    ):
        if isinstance(obj, dict) and obj.get("value") is not None:
            v = obj["value"]; p = obj.get("change_pct") or 0
            sign = "+" if p >= 0 else ""
            idx_section["items"].append(f"{name} {v:,.2f} {sign}{p:.2f}%")
    summary["sections"].append(idx_section)

    # ── 2. 매크로 ──
    macro_section = {"title": "🌍 매크로", "items": []}
    try:
        md = json.loads((BASE_DIR / "cache" / "macro_data.json").read_text(encoding="utf-8"))
    except Exception:
        md = {}
    targets = ["VIX", "USD/KRW", "WTI 원유", "미국 10년물", "BTC", "금"]
    for it in md.get("items", []):
        if it.get("name") in targets:
            v = it.get("value") or 0
            p = it.get("change_pct") or 0
            sign = "+" if p >= 0 else ""
            note = ""
            if it["name"] == "VIX":
                if v < 15: note = " (안정)"
                elif v < 20: note = " (보통)"
                elif v < 25: note = " (경계)"
                elif v < 35: note = " (공포)"
                else: note = " (패닉)"
            macro_section["items"].append(f"{it['name']} {v:,.2f} {sign}{p:.2f}%{note}")
    summary["sections"].append(macro_section)

    # ── 3. 옵션/선물 ──
    opts_section = {"title": "🔮 옵션/선물", "items": []}
    for sym in ("SPY", "QQQ"):
        try:
            f = BASE_DIR / "cache" / f"options_signal_{sym}.json"
            if not f.exists(): continue
            od = json.loads(f.read_text(encoding="utf-8"))
            pcr = od.get("pcr", {})
            mp = od.get("max_pain", {})
            gex = od.get("gex", {})
            ovr = od.get("overall", {})
            opts_section["items"].append(
                f"{sym} ${od.get('spot_price', 0)} | PCR {pcr.get('volume', '—')} | "
                f"MaxPain ${mp.get('strike', '—')} ({mp.get('diff_pct', 0):+.1f}%) | "
                f"GEX {gex.get('regime', '—')} → {ovr.get('emoji', '')} {ovr.get('direction', '—')}"
            )
        except Exception:
            pass
    try:
        nf = json.loads((BASE_DIR / "cache" / "night_futures.json").read_text(encoding="utf-8"))
        if nf.get("night_close"):
            p = nf.get("change_pct") or 0
            sign = "+" if p >= 0 else ""
            opts_section["items"].append(
                f"코스피200 야간선물 {nf['night_close']} {sign}{p}% — {nf.get('signal', '')}"
            )
    except Exception:
        pass
    summary["sections"].append(opts_section)

    # ── 4. 섹터 ──
    sector_section = {"title": "🏭 섹터 등락", "subsections": []}
    if _SQLITE_OK and USE_SQLITE:
        try:
            with _get_db() as conn:
                sectors = conn.execute("""
                    SELECT sector,
                           ROUND(AVG(change_pct), 2) as avg_chg,
                           COUNT(*) as cnt
                    FROM stocks
                    WHERE (market = '' OR market LIKE 'KOS%')
                      AND COALESCE(is_etf, 0) = 0
                      AND sector IS NOT NULL AND sector != ''
                      AND change_pct IS NOT NULL
                    GROUP BY sector HAVING cnt >= 5
                    ORDER BY avg_chg DESC
                """).fetchall()
                if sectors:
                    top = [dict(s) for s in sectors[:5]]
                    top_items = []
                    for i, s in enumerate(top):
                        ldr = conn.execute("""
                            SELECT name, change_pct FROM stocks
                            WHERE sector = ? AND (market = '' OR market LIKE 'KOS%')
                              AND COALESCE(is_etf, 0) = 0
                            ORDER BY change_pct DESC LIMIT 1
                        """, (s["sector"],)).fetchone()
                        lead_str = (f" (대장: {ldr['name']} {ldr['change_pct']:+.1f}%)"
                                    if ldr and ldr["change_pct"] is not None else "")
                        top_items.append(f"  {i+1}. {s['sector']} {s['avg_chg']:+.2f}%{lead_str}")
                    sector_section["subsections"].append(
                        {"subtitle": "🟢 강세 TOP 5", "items": top_items}
                    )
                    bot = [dict(s) for s in sectors[-3:]]; bot.reverse()
                    bot_items = [f"  {i+1}. {s['sector']} {s['avg_chg']:+.2f}%"
                                 for i, s in enumerate(bot)]
                    sector_section["subsections"].append(
                        {"subtitle": "🔴 약세 TOP 3", "items": bot_items}
                    )
        except Exception as exc:
            log.debug("[summary] sector: %s", exc)
    summary["sections"].append(sector_section)

    # ── 5. 특징주 ──
    feat_section = {"title": "⚡ 특징주", "subsections": []}
    if _SQLITE_OK and USE_SQLITE:
        try:
            with _get_db() as conn:
                risers = conn.execute("""
                    SELECT code, name, change_pct, volume_mn, sector, market_cap
                    FROM stocks
                    WHERE (market = '' OR market LIKE 'KOS%')
                      AND COALESCE(is_etf, 0) = 0 AND change_pct > 5
                    ORDER BY change_pct DESC LIMIT 10
                """).fetchall()
                if risers:
                    items = [
                        f"  {r['name']} {r['change_pct']:+.1f}%{_fmt_cap(r['market_cap'])} — {r['sector'] or '?'}"
                        for r in risers
                    ]
                    feat_section["subsections"].append(
                        {"subtitle": "🔺 급등 (+5%↑) TOP 10", "items": items}
                    )
                fallers = conn.execute("""
                    SELECT name, change_pct, sector
                    FROM stocks
                    WHERE (market = '' OR market LIKE 'KOS%')
                      AND COALESCE(is_etf, 0) = 0 AND change_pct < -5
                    ORDER BY change_pct ASC LIMIT 5
                """).fetchall()
                if fallers:
                    items = [f"  {f['name']} {f['change_pct']:+.1f}% — {f['sector'] or '?'}"
                             for f in fallers]
                    feat_section["subsections"].append(
                        {"subtitle": "🔻 급락 (-5%↓) TOP 5", "items": items}
                    )
                vols = conn.execute("""
                    SELECT name, change_pct, volume_mn, sector
                    FROM stocks
                    WHERE (market = '' OR market LIKE 'KOS%')
                      AND COALESCE(is_etf, 0) = 0
                      AND ABS(COALESCE(change_pct, 0)) <= 5
                      AND COALESCE(volume_mn, 0) > 0
                    ORDER BY volume_mn DESC LIMIT 5
                """).fetchall()
                if vols:
                    items = [
                        f"  {v['name']} {v['change_pct']:+.1f}% (거래대금 {v['volume_mn']:,.0f}M) — {v['sector'] or '?'}"
                        for v in vols
                    ]
                    feat_section["subsections"].append(
                        {"subtitle": "📊 거래대금 상위 (±5% 이내)", "items": items}
                    )
        except Exception as exc:
            log.debug("[summary] feat: %s", exc)
    summary["sections"].append(feat_section)

    # ── 6. 수급 (flow_cache에서 오늘 순매수 집계) ──
    flow_section = {"title": "💰 수급 동향", "subsections": []}
    if _SQLITE_OK and USE_SQLITE:
        try:
            with _get_db() as conn:
                # 오늘 순매수 = foreign_value_json / inst_value_json 의 마지막 값
                rows = conn.execute("""
                    SELECT code, name, foreign_value_json, inst_value_json
                    FROM flow_cache
                    WHERE foreign_value_json IS NOT NULL
                """).fetchall()
                foreign_today: list = []
                inst_today: list = []
                for r in rows:
                    fv = _parse_json_list(r["foreign_value_json"])
                    iv = _parse_json_list(r["inst_value_json"])
                    if fv:
                        foreign_today.append({"code": r["code"], "name": r["name"], "net": fv[-1]})
                    if iv:
                        inst_today.append({"code": r["code"], "name": r["name"], "net": iv[-1]})
                foreign_top = sorted(foreign_today, key=lambda x: x["net"], reverse=True)[:5]
                inst_top = sorted(inst_today, key=lambda x: x["net"], reverse=True)[:5]
                if foreign_top:
                    flow_section["subsections"].append({
                        "subtitle": "🌐 외국인 순매수 TOP 5 (오늘)",
                        "items": [f"  {r['name']} +{r['net']/1e8:,.0f}억" for r in foreign_top]
                    })
                if inst_top:
                    flow_section["subsections"].append({
                        "subtitle": "🏛 기관 순매수 TOP 5 (오늘)",
                        "items": [f"  {r['name']} +{r['net']/1e8:,.0f}억" for r in inst_top]
                    })
                # 20일 누적 매수 — 장기 수급 트렌드
                agg = conn.execute("""
                    SELECT name, foreign_sum_20 FROM flow_cache
                    WHERE foreign_sum_20 IS NOT NULL
                    ORDER BY foreign_sum_20 DESC LIMIT 5
                """).fetchall()
                if agg:
                    flow_section["subsections"].append({
                        "subtitle": "🌐 외국인 20일 누적 TOP 5",
                        "items": [f"  {a['name']} +{a['foreign_sum_20']/1e8:,.0f}억" for a in agg]
                    })
        except Exception as exc:
            log.debug("[summary] flow: %s", exc)
    summary["sections"].append(flow_section)

    # ── 7. DART 공시 ──
    disc_section = {"title": "📋 주요 공시", "items": []}
    if _SQLITE_OK and USE_SQLITE:
        try:
            today_str = now_kst().strftime("%Y%m%d")
            with _get_db() as conn:
                discs = conn.execute("""
                    SELECT corp_name, title, score FROM disclosure_history
                    WHERE rcept_dt = ? AND score >= 6
                    ORDER BY score DESC LIMIT 5
                """, (today_str,)).fetchall()
            for d in discs:
                emoji = "🚨" if (d["score"] or 0) >= 10 else "📢"
                disc_section["items"].append(
                    f"  {emoji} [{d['score']}점] {d['corp_name']}: {(d['title'] or '')[:42]}"
                )
        except Exception as exc:
            log.debug("[summary] disc: %s", exc)
    if not disc_section["items"]:
        disc_section["items"].append("  오늘 중요 공시 없음")
    summary["sections"].append(disc_section)

    # ── 8. AI 추천 ──
    ai_section = {"title": "🤖 AI 추천 요약", "items": []}
    for cand in ("agent_result_kr_latest.json", "agent_result_latest.json"):
        p = BASE_DIR / "cache" / cand
        if p.exists():
            try:
                agent = json.loads(p.read_text(encoding="utf-8"))
                hot = (agent.get("agents", {}).get("news", {}).get("hot_themes") or [])
                if hot:
                    ai_section["items"].append(f"  핫 테마: {', '.join(hot[:5])}")
                picks = (agent.get("final_picks") or [])[:5]
                if picks:
                    ai_section["items"].append(f"  추천 {len(picks)}종목:")
                    for pk in picks:
                        ai_section["items"].append(
                            f"    {pk.get('name')} ({pk.get('code')}) {pk.get('total_score', 0)}점"
                        )
                break
            except Exception:
                pass
    if not ai_section["items"]:
        ai_section["items"].append("  AI 추천 데이터 없음")
    summary["sections"].append(ai_section)

    # 캐시 저장
    try:
        out = BASE_DIR / "cache" / f"market_summary_{now_kst().strftime('%Y%m%d')}.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return summary


@app.route("/api/market_summary")
def api_market_summary():
    return jsonify(build_market_summary())


def build_us_market_summary() -> dict:
    """미국 장마감 시황 요약 (DB market='US' + 매크로 + 옵션)."""
    summary = {
        "market": "us",
        "generated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "sections": [],
    }

    # ── 1. 미국 지수 (data.json.market_overview) ──
    idx_section = {"title": "🇺🇸 미국 지수", "items": []}
    try:
        dj = json.loads((BASE_DIR / "data.json").read_text(encoding="utf-8")) \
            if (BASE_DIR / "data.json").exists() else {}
    except Exception:
        dj = {}
    mo = (dj.get("market_overview") or {})
    for name, obj in (
        ("S&P 500", mo.get("sp500")),
        ("NASDAQ", mo.get("nasdaq")),
        ("DOW", mo.get("dow")),
        ("Russell 2000", mo.get("russell")),
        ("SOX (반도체)", mo.get("sox")),
        ("나스닥100 선물", mo.get("nasdaq_futures")),
    ):
        if isinstance(obj, dict) and obj.get("value") is not None:
            v = obj["value"]; p = obj.get("change_pct") or 0
            sign = "+" if p >= 0 else ""
            idx_section["items"].append(f"{name} {v:,.2f} {sign}{p:.2f}%")
    summary["sections"].append(idx_section)

    # ── 2. 옵션/변동성 ──
    opts_section = {"title": "🔮 옵션·변동성", "items": []}
    try:
        md = json.loads((BASE_DIR / "cache" / "macro_data.json").read_text(encoding="utf-8"))
    except Exception:
        md = {}
    for it in md.get("items", []):
        if it.get("name") == "VIX":
            v = it.get("value") or 0; p = it.get("change_pct") or 0
            sign = "+" if p >= 0 else ""
            if v < 15: note = "안정"
            elif v < 20: note = "보통"
            elif v < 25: note = "경계"
            elif v < 35: note = "공포"
            else: note = "패닉"
            opts_section["items"].append(f"VIX {v:.2f} {sign}{p:.2f}% ({note})")
            break
    for sym in ("SPY", "QQQ"):
        try:
            f = BASE_DIR / "cache" / f"options_signal_{sym}.json"
            if not f.exists(): continue
            od = json.loads(f.read_text(encoding="utf-8"))
            pcr = od.get("pcr", {})
            mp = od.get("max_pain", {})
            gex = od.get("gex", {})
            ovr = od.get("overall", {})
            opts_section["items"].append(
                f"{sym} ${od.get('spot_price', 0)} | PCR {pcr.get('volume', '—')} | "
                f"MaxPain ${mp.get('strike', '—')} ({mp.get('diff_pct', 0):+.1f}%) | "
                f"GEX {gex.get('regime', '—')} → {ovr.get('emoji', '')} {ovr.get('direction', '—')}"
            )
            cw = gex.get("call_wall"); pw = gex.get("put_wall")
            if cw and isinstance(cw, dict):
                put_str = f" | 풋벽 ${pw.get('strike')}" if pw and isinstance(pw, dict) else ""
                opts_section["items"].append(
                    f"  {sym} 콜벽(저항) ${cw.get('strike')}{put_str}"
                )
        except Exception:
            pass
    summary["sections"].append(opts_section)

    # ── 3. 매크로 (US 관점) ──
    macro_section = {"title": "🌍 매크로", "items": []}
    targets = ["미국 10년물", "USD/KRW", "USD/JPY", "EUR/USD", "WTI 원유", "금", "BTC"]
    for it in md.get("items", []):
        if it.get("name") in targets:
            v = it.get("value") or 0; p = it.get("change_pct") or 0
            sign = "+" if p >= 0 else ""
            note = ""
            if it["name"] == "미국 10년물":
                if v > 4.5: note = " (고금리)"
                elif v > 4.0: note = " (보통)"
                else: note = " (저금리)"
            elif it["name"] == "USD/KRW":
                if v > 1400: note = " (원화 약세)"
                elif v < 1300: note = " (원화 강세)"
            macro_section["items"].append(f"{it['name']} {v:,.2f} {sign}{p:.2f}%{note}")
    summary["sections"].append(macro_section)

    # ── 4. GICS 섹터 ──
    sector_section = {"title": "🏭 GICS 섹터", "subsections": []}
    if _SQLITE_OK and USE_SQLITE:
        try:
            with _get_db() as conn:
                sectors = conn.execute("""
                    SELECT sector, ROUND(AVG(change_pct), 2) as avg_chg, COUNT(*) as cnt
                    FROM stocks
                    WHERE market = 'US' AND COALESCE(is_etf, 0) = 0
                      AND sector IS NOT NULL AND sector != ''
                      AND change_pct IS NOT NULL
                    GROUP BY sector HAVING cnt >= 5
                    ORDER BY avg_chg DESC
                """).fetchall()
                if sectors:
                    top_items = []
                    for i, s in enumerate(sectors[:5]):
                        ldr = conn.execute("""
                            SELECT name, code, change_pct FROM stocks
                            WHERE market = 'US' AND sector = ?
                              AND COALESCE(is_etf, 0) = 0
                            ORDER BY change_pct DESC LIMIT 1
                        """, (s["sector"],)).fetchone()
                        lead_str = (f" (대장: {ldr['name']} {ldr['change_pct']:+.1f}%)"
                                    if ldr and ldr["change_pct"] is not None else "")
                        top_items.append(f"  {i+1}. {s['sector']} {s['avg_chg']:+.2f}%{lead_str}")
                    sector_section["subsections"].append(
                        {"subtitle": "🟢 강세 TOP 5", "items": top_items}
                    )
                    bot = list(reversed(sectors[-3:]))
                    bot_items = [f"  {i+1}. {s['sector']} {s['avg_chg']:+.2f}%"
                                 for i, s in enumerate(bot)]
                    sector_section["subsections"].append(
                        {"subtitle": "🔴 약세 TOP 3", "items": bot_items}
                    )
        except Exception as exc:
            log.debug("[us_summary] sector: %s", exc)
    summary["sections"].append(sector_section)

    # ── 5. 특징주 ──
    feat_section = {"title": "⚡ 특징주", "subsections": []}
    if _SQLITE_OK and USE_SQLITE:
        def _cap_us(cap):
            if not cap or cap <= 0:
                return ""
            try:
                if cap >= 1e12: return f" [${cap/1e12:.1f}T]"
                if cap >= 1e9: return f" [${cap/1e9:.1f}B]"
                if cap >= 1e6: return f" [${cap/1e6:.0f}M]"
            except Exception:
                pass
            return ""
        try:
            with _get_db() as conn:
                risers = conn.execute("""
                    SELECT code, name, change_pct, sector, market_cap
                    FROM stocks
                    WHERE market = 'US' AND COALESCE(is_etf, 0) = 0
                      AND change_pct > 3
                    ORDER BY change_pct DESC LIMIT 10
                """).fetchall()
                if risers:
                    items = [
                        f"  {r['name']} ({r['code']}) {r['change_pct']:+.1f}%{_cap_us(r['market_cap'])} — {r['sector'] or '?'}"
                        for r in risers
                    ]
                    feat_section["subsections"].append(
                        {"subtitle": "🔺 급등 (+3%↑) TOP 10", "items": items}
                    )
                fallers = conn.execute("""
                    SELECT code, name, change_pct, sector
                    FROM stocks
                    WHERE market = 'US' AND COALESCE(is_etf, 0) = 0
                      AND change_pct < -3
                    ORDER BY change_pct ASC LIMIT 10
                """).fetchall()
                if fallers:
                    items = [f"  {f['name']} ({f['code']}) {f['change_pct']:+.1f}% — {f['sector'] or '?'}"
                             for f in fallers]
                    feat_section["subsections"].append(
                        {"subtitle": "🔻 급락 (-3%↓) TOP 10", "items": items}
                    )
        except Exception as exc:
            log.debug("[us_summary] feat: %s", exc)
    summary["sections"].append(feat_section)

    # ── 6. Mag7 ──
    mag7_section = {"title": "🏆 Mag7", "items": []}
    if _SQLITE_OK and USE_SQLITE:
        try:
            with _get_db() as conn:
                for sym in ("AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"):
                    r = conn.execute(
                        "SELECT name, close, change_pct, market_cap FROM stocks "
                        "WHERE code = ? AND market = 'US'", (sym,)
                    ).fetchone()
                    if not r:
                        continue
                    cap = r["market_cap"] or 0
                    cap_str = f" ${cap/1e12:.1f}T" if cap >= 1e12 else (
                        f" ${cap/1e9:.0f}B" if cap >= 1e9 else ""
                    )
                    p = r["close"] or 0; c = r["change_pct"] or 0
                    sign = "+" if c >= 0 else ""
                    dot = "🔴" if c >= 0 else "🟢"
                    mag7_section["items"].append(
                        f"  {dot} {sym} ${p:,.2f} {sign}{c:.2f}%{cap_str}"
                    )
        except Exception as exc:
            log.debug("[us_summary] mag7: %s", exc)
    summary["sections"].append(mag7_section)

    # ── 7. 내일 국내 영향 ──
    dom_section = {"title": "🇰🇷 내일 국내 영향", "items": []}
    try:
        nf = json.loads((BASE_DIR / "cache" / "night_futures.json").read_text(encoding="utf-8"))
        if nf.get("night_close"):
            p = nf.get("change_pct") or 0
            sign = "+" if p >= 0 else ""
            dom_section["items"].append(
                f"코스피200 야간선물 {nf['night_close']} {sign}{p}% — {nf.get('signal', '')}"
            )
    except Exception:
        pass
    for it in md.get("items", []):
        if it.get("name") == "USD/KRW":
            p = it.get("change_pct") or 0
            if p > 0.5:
                dom_section["items"].append(
                    f"원화 약세 ({p:+.2f}%) → 수출주(반도체·조선) 유리, 내수주 부담"
                )
            elif p < -0.5:
                dom_section["items"].append(
                    f"원화 강세 ({p:+.2f}%) → 내수주 유리, 수출주 부담"
                )
            else:
                dom_section["items"].append(f"환율 보합 ({p:+.2f}%)")
            break
    # 미국 반도체 평균 → 국내 반도체 연동 시그널
    if _SQLITE_OK and USE_SQLITE:
        try:
            with _get_db() as conn:
                rows = conn.execute("""
                    SELECT AVG(change_pct) as avg_chg
                    FROM stocks
                    WHERE market = 'US'
                      AND code IN ('NVDA','AMD','AVGO','QCOM','MU','INTC','TSM','ASML','LRCX','AMAT')
                      AND change_pct IS NOT NULL
                """).fetchone()
                if rows and rows["avg_chg"] is not None:
                    avg = rows["avg_chg"]
                    if abs(avg) >= 1:
                        sign = "+" if avg >= 0 else ""
                        dir_ = "상승" if avg > 0 else "하락"
                        dom_section["items"].append(
                            f"미국 반도체 평균 {sign}{avg:.2f}% → 내일 삼성전자/SK하이닉스 {dir_} 압력"
                        )
        except Exception:
            pass
    summary["sections"].append(dom_section)

    # ── 8. US AI 추천 ──
    ai_section = {"title": "🤖 US AI 추천", "items": []}
    p = BASE_DIR / "cache" / "agent_result_us_latest.json"
    if p.exists():
        try:
            agent = json.loads(p.read_text(encoding="utf-8"))
            news = agent.get("agents", {}).get("news", {}) or {}
            hot = news.get("hot_sectors") or news.get("hot_themes") or []
            if hot:
                ai_section["items"].append(f"  핫 섹터: {', '.join(hot[:5])}")
            picks = (agent.get("final_picks") or [])[:5]
            if picks:
                ai_section["items"].append(f"  추천 {len(picks)}종목:")
                for pk in picks:
                    ai_section["items"].append(
                        f"    {pk.get('name')} ({pk.get('code')}) {pk.get('total_score', 0)}점"
                    )
        except Exception:
            pass
    if not ai_section["items"]:
        ai_section["items"].append("  US AI 추천 데이터 없음")
    summary["sections"].append(ai_section)

    # 캐시 저장
    try:
        out = BASE_DIR / "cache" / f"us_market_summary_{now_kst().strftime('%Y%m%d')}.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return summary


@app.route("/api/market_summary/us")
def api_market_summary_us():
    return jsonify(build_us_market_summary())


def send_us_market_summary_telegram():
    """미국 장마감 시황 텔레그램 발송 (KST 06:10 cron)."""
    try:
        data = build_us_market_summary()
    except Exception:
        log.exception("send_us_market_summary build")
        return
    lines = [f"🇺🇸 <b>{now_kst().strftime('%m/%d')} 미국 장마감 시황</b>", ""]
    for sec in data.get("sections", []):
        lines.append(f"<b>{sec['title']}</b>")
        for it in sec.get("items", []):
            lines.append(it)
        for sub in sec.get("subsections", []):
            lines.append("")
            lines.append(sub["subtitle"])
            for it in sub.get("items", []):
                lines.append(it)
        lines.append("")
    lines.append(f"⏰ {now_kst().strftime('%H:%M')} KST")
    msg = "\n".join(lines)
    if len(msg) > 4000:
        msg = msg[:3990] + "\n…(생략)"
    send_telegram(msg)


def send_market_summary_telegram():
    """장마감 시황 텔레그램 발송 (15:40 cron)."""
    try:
        data = build_market_summary()
    except Exception as exc:
        log.exception("send_market_summary build")
        return
    lines = [f"📊 <b>{now_kst().strftime('%m/%d')} 장마감 시황</b>", ""]
    for sec in data.get("sections", []):
        lines.append(f"<b>{sec['title']}</b>")
        for it in sec.get("items", []):
            lines.append(it)
        for sub in sec.get("subsections", []):
            lines.append("")
            lines.append(sub["subtitle"])
            for it in sub.get("items", []):
                lines.append(it)
        lines.append("")
    lines.append(f"⏰ {now_kst().strftime('%H:%M')} KST")
    msg = "\n".join(lines)
    if len(msg) > 4000:
        msg = msg[:3990] + "\n…(생략)"
    send_telegram(msg)


# ── 섹터 로테이션 2.0: DB 기반 동적 추천/회피 + 시장 국면 ────────────────────────────────────
def _detect_market_phase() -> dict:
    """VIX + USD/KRW 실제 값으로 시장 국면 동적 판정. 하드코딩 없음."""
    macro = _read_fresh_json(BASE_DIR / "cache" / "macro_data.json", 24 * 60) or {}
    items = {it.get("name"): it for it in (macro.get("items") or [])}
    vix = items.get("VIX", {}).get("price")
    usdkrw = items.get("USD/KRW", {}).get("price")
    usdkrw_chg = items.get("USD/KRW", {}).get("change_pct") or 0
    vix_chg = items.get("VIX", {}).get("change_pct") or 0

    # 국면 판정 (동적 임계치 — 최근값 기반)
    phase = "중립"
    reasons: list = []
    if vix is None:
        phase = "데이터부족"
    elif vix >= 30:
        phase = "위험회피"
        reasons.append(f"VIX {vix:.1f} 높음")
    elif vix >= 22:
        phase = "경계"
        reasons.append(f"VIX {vix:.1f} 평균 이상")
    elif vix <= 15 and vix_chg < 0:
        phase = "위험선호"
        reasons.append(f"VIX {vix:.1f} 낮음·하락")
    else:
        phase = "중립"
        reasons.append(f"VIX {vix:.1f}")

    if usdkrw and usdkrw >= 1400:
        reasons.append(f"원화 약세 (USD/KRW {usdkrw:.0f})")
        if phase in ("중립", "위험선호"):
            phase = "경계"
    elif usdkrw and usdkrw <= 1250:
        reasons.append(f"원화 강세 (USD/KRW {usdkrw:.0f})")

    return {
        "phase": phase,
        "vix": vix,
        "vix_change_pct": vix_chg,
        "usdkrw": usdkrw,
        "usdkrw_change_pct": usdkrw_chg,
        "reasons": reasons,
    }


@app.route("/api/sector_rotation/phase")
def api_sector_rotation_phase():
    """실시간 DB 기반 섹터 로테이션 + 추천/회피 섹터 + 시장 국면.
    하드코딩 없음 — sectors_raw는 /api/sector_rotation 와 동일 집계 재사용."""
    try:
        # 기존 섹터 로테이션 데이터 재사용 (HTTP 순환 호출 대신 직접 계산)
        sparklines = _load_ticker_sparklines_kr()
        uni = _load_naver_universe()
        stocks = (uni or {}).get("stocks") or {}
        if not sparklines or not stocks:
            return jsonify({"error": "캐시 데이터 부족"}), 503

        sectors_raw: dict = {}
        for code, st in stocks.items():
            sp = sparklines.get(code)
            if not sp or len(sp) < 20 or not sp[0]:
                continue
            sec_list = st.get("sectors") or []
            if not sec_list:
                continue
            sector = sec_list[0]
            try:
                ret_5d = (sp[-1] / sp[-5] - 1) * 100 if sp[-5] else None
                ret_20d = (sp[-1] / sp[0] - 1) * 100
            except Exception:
                continue
            sectors_raw.setdefault(sector, []).append({
                "code": code,
                "volume_mn": st.get("volume_mn") or 0,
                "chg_today": st.get("change_pct") or 0,
                "ret_5d": ret_5d, "ret_20d": ret_20d,
            })

        def _wavg(rows, key):
            vals = [r for r in rows if r.get(key) is not None]
            if not vals:
                return None
            tw = sum(r["volume_mn"] for r in vals) or 0
            if tw > 0:
                return round(sum((r[key] or 0) * (r["volume_mn"] or 0)
                                 for r in vals) / tw, 2)
            return round(sum(r[key] or 0 for r in vals) / len(vals), 2)

        sectors: list = []
        for name, rows in sectors_raw.items():
            if len(rows) < 3:
                continue
            # 모멘텀 스코어 = 1주 가중 + 1개월 × 2
            r5 = _wavg(rows, "ret_5d") or 0
            r20 = _wavg(rows, "ret_20d") or 0
            score = round(r5 + r20 * 2, 2)
            sectors.append({
                "name": name,
                "stock_count": len(rows),
                "ret_1w": r5, "ret_1m": r20,
                "change_today": _wavg(rows, "chg_today"),
                "momentum_score": score,
            })
        sectors.sort(key=lambda x: x["momentum_score"], reverse=True)

        # 추천/회피: 상위 3 + 양수 / 하위 3 + 음수 (조건 동적)
        recommended = [s for s in sectors[:5] if s["momentum_score"] > 0][:3]
        avoid = [s for s in sectors[-5:] if s["momentum_score"] < 0][-3:]
        avoid.reverse()

        phase_info = _detect_market_phase()

        # 국면별 상위 종목 추천 (각 추천 섹터에서 1주 모멘텀 상위 5종목)
        top_stocks_by_sector: dict = {}
        for sec in recommended:
            rows = sectors_raw.get(sec["name"]) or []
            rows_sorted = sorted(
                [r for r in rows if r.get("ret_5d") is not None],
                key=lambda x: x["ret_5d"], reverse=True
            )[:5]
            top_stocks_by_sector[sec["name"]] = [
                {
                    "code": r["code"],
                    "name": stocks.get(r["code"], {}).get("name") or r["code"],
                    "ret_5d": r["ret_5d"],
                    "ret_20d": r["ret_20d"],
                    "volume_mn": r["volume_mn"],
                }
                for r in rows_sorted
            ]

        return jsonify({
            "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
            "market_phase": phase_info,
            "recommended": recommended,
            "avoid": avoid,
            "top_stocks_by_sector": top_stocks_by_sector,
            "total_sectors": len(sectors),
            "all_sectors": sectors,
        })
    except Exception as exc:
        log.exception("sector rotation phase")
        return jsonify({"error": str(exc)}), 500


# ── 소셜 센티먼트 (네이버 토론방) ────────────────────────────────────
_SENTIMENT_CACHE: dict = {}  # {code: {"data": {...}, "fetched_at": float}}
_SENTIMENT_TTL = 30 * 60  # 30분


def _load_sentiment_dict() -> dict:
    f = BASE_DIR / "cache" / "sentiment_dict.json"
    if not f.exists():
        return {"positive": {}, "negative": {}, "neutral_but_meaningful": {}}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {"positive": {}, "negative": {}, "neutral_but_meaningful": {}}


def _fetch_naver_board_titles(code: str, pages: int = 3) -> list:
    """네이버 금융 토론방 제목 수집. code: 6자리 KR. 최근 pages 페이지."""
    import re as _re
    import urllib.request
    titles: list = []
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}
    for p in range(1, pages + 1):
        url = f"https://finance.naver.com/item/board.naver?code={code}&page={p}"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                raw = resp.read()
        except Exception as exc:
            log.debug("[sentiment] naver fetch %s p%d: %s", code, p, exc)
            continue
        # 네이버 페이지는 UTF-8 (과거 EUC-KR에서 변경됨)
        html = raw.decode("utf-8", errors="ignore")
        # <a ... onclick="clickcr(...);" title="...">...</a>  제목 추출
        matches = _re.findall(
            r'<td class="title"[^>]*>\s*<a[^>]*title="([^"]+)"[^>]*>',
            html
        )
        if not matches:
            matches = _re.findall(
                r'href="/item/board_read\.naver\?[^"]+"[^>]*>([^<]+)</a>',
                html
            )
        for t in matches:
            t = t.strip()
            if t and t not in titles:
                titles.append(t)
        if not matches:
            break
    return titles[:120]


def _score_sentiment_titles(titles: list, sdict: dict) -> dict:
    pos_dict = sdict.get("positive") or {}
    neg_dict = sdict.get("negative") or {}
    pos_score = 0; neg_score = 0
    hit_pos: dict = {}; hit_neg: dict = {}
    for t in titles:
        for kw, w in pos_dict.items():
            if kw and kw in t:
                pos_score += w
                hit_pos[kw] = hit_pos.get(kw, 0) + 1
        for kw, w in neg_dict.items():
            if kw and kw in t:
                neg_score += w
                hit_neg[kw] = hit_neg.get(kw, 0) + 1
    total = pos_score + neg_score
    ratio = round(pos_score / total, 3) if total > 0 else 0.5
    label = "중립"
    if total >= 5:
        if ratio >= 0.65:
            label = "긍정"
        elif ratio <= 0.35:
            label = "부정"
    return {
        "posts": len(titles),
        "positive_score": pos_score,
        "negative_score": neg_score,
        "ratio": ratio,
        "label": label,
        "top_positive": sorted(hit_pos.items(), key=lambda x: -x[1])[:8],
        "top_negative": sorted(hit_neg.items(), key=lambda x: -x[1])[:8],
    }


# ── 수익률 저널 (trade_journal) ─────────────
def _init_trade_journal():
    if not (_SQLITE_OK and USE_SQLITE):
        return
    try:
        with _get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trade_journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL,
                    name TEXT,
                    market TEXT,
                    action TEXT NOT NULL,
                    price REAL NOT NULL,
                    qty INTEGER NOT NULL,
                    total_amount REAL,
                    fee REAL DEFAULT 0,
                    tax REAL DEFAULT 0,
                    strategy TEXT,
                    memo TEXT,
                    trade_date TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now', 'localtime')),
                    linked_buy_id INTEGER,
                    realized_pnl REAL,
                    realized_pnl_pct REAL,
                    hold_days INTEGER
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tj_date ON trade_journal(trade_date DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tj_code ON trade_journal(code)")
            conn.commit()
        log.info("[저널] trade_journal 테이블 초기화")
    except Exception as exc:
        log.warning("[저널] 초기화 실패: %s", exc)


@app.route("/api/journal/add", methods=["POST"])
def api_journal_add():
    """매매 기록 추가 — 매도 시 FIFO로 매수 연결 + 실현손익 자동 계산."""
    if not (_SQLITE_OK and USE_SQLITE):
        return jsonify({"error": "SQLite 비활성"}), 503
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()
    action = (data.get("action") or "").strip().lower()
    try:
        price = float(data.get("price") or 0)
        qty = int(data.get("qty") or 0)
    except Exception:
        price, qty = 0, 0
    if not code or action not in ("buy", "sell") or price <= 0 or qty <= 0:
        return jsonify({"error": "필수값 누락 (code/action/price/qty)"}), 400

    trade_date = data.get("trade_date") or now_kst().strftime("%Y-%m-%d")
    total = price * qty
    # 한국 주식 수수료(0.015%) + 매도 시 거래세(0.18%)
    fee = round(total * 0.00015)
    tax = round(total * 0.0018) if action == "sell" else 0

    realized_pnl = None
    realized_pnl_pct = None
    hold_days = None
    linked_buy_id = None
    name = data.get("name") or ""
    market = data.get("market") or "kr"

    try:
        with _get_db() as conn:
            if not name:
                row = conn.execute("SELECT name, market FROM stocks WHERE code = ?", (code,)).fetchone()
                if row:
                    name = row["name"] or code
                    market = row["market"] or market
            name = name or code

            if action == "sell":
                # FIFO: 가장 오래된 미연결 매수 1건과 매칭
                buy_row = conn.execute(
                    "SELECT id, price, trade_date FROM trade_journal "
                    "WHERE code = ? AND action = 'buy' "
                    "AND id NOT IN (SELECT linked_buy_id FROM trade_journal "
                    "                WHERE linked_buy_id IS NOT NULL) "
                    "ORDER BY trade_date ASC, id ASC LIMIT 1",
                    (code,)
                ).fetchone()
                if buy_row:
                    linked_buy_id = buy_row["id"]
                    bp = buy_row["price"] or 0
                    realized_pnl = round((price - bp) * qty - fee - tax)
                    realized_pnl_pct = round((price / bp - 1) * 100, 2) if bp else 0
                    try:
                        from datetime import datetime as _dt
                        bd = _dt.strptime(buy_row["trade_date"], "%Y-%m-%d")
                        sd = _dt.strptime(trade_date, "%Y-%m-%d")
                        hold_days = (sd - bd).days
                    except Exception:
                        pass

            cur = conn.execute(
                "INSERT INTO trade_journal "
                "(code, name, market, action, price, qty, total_amount, fee, tax, "
                " strategy, memo, trade_date, linked_buy_id, realized_pnl, "
                " realized_pnl_pct, hold_days) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (code, name, market, action, price, qty, total, fee, tax,
                 (data.get("strategy") or "").strip(),
                 (data.get("memo") or "").strip(),
                 trade_date, linked_buy_id, realized_pnl, realized_pnl_pct, hold_days)
            )
            conn.commit()
            new_id = cur.lastrowid
        return jsonify({"status": "ok", "id": new_id, "realized_pnl": realized_pnl,
                        "realized_pnl_pct": realized_pnl_pct, "hold_days": hold_days})
    except Exception as exc:
        log.exception("journal/add")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/journal/list")
def api_journal_list():
    """매매 이력 조회 — period 일수 (기본 30)."""
    if not (_SQLITE_OK and USE_SQLITE):
        return jsonify({"trades": []})
    try:
        period = max(1, int(request.args.get("period", 30)))
    except Exception:
        period = 30
    from datetime import timedelta as _td
    cutoff = (now_kst() - _td(days=period)).strftime("%Y-%m-%d")
    try:
        with _get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM trade_journal WHERE trade_date >= ? "
                "ORDER BY trade_date DESC, id DESC", (cutoff,)
            ).fetchall()
        return jsonify({"trades": [dict(r) for r in rows]})
    except Exception as exc:
        log.exception("journal/list")
        return jsonify({"trades": [], "error": str(exc)}), 500


@app.route("/api/journal/summary")
def api_journal_summary():
    """수익률 요약 (overall / 그룹별 / 누적 곡선)."""
    if not (_SQLITE_OK and USE_SQLITE):
        return jsonify({"overall": {"total_trades": 0}})
    try:
        period = max(1, int(request.args.get("period", 30)))
    except Exception:
        period = 30
    group_by = request.args.get("group", "daily")
    from datetime import timedelta as _td
    cutoff = (now_kst() - _td(days=period)).strftime("%Y-%m-%d")
    try:
        with _get_db() as conn:
            rows = conn.execute(
                "SELECT realized_pnl, realized_pnl_pct, hold_days, trade_date, "
                "       strategy, name, code FROM trade_journal "
                "WHERE action='sell' AND trade_date >= ? "
                "AND realized_pnl IS NOT NULL "
                "ORDER BY trade_date ASC, id ASC", (cutoff,)
            ).fetchall()
        data = [dict(r) for r in rows]
        if not data:
            return jsonify({"overall": {"total_trades": 0},
                            "groups": [], "equity_curve": []})

        pnls = [r["realized_pnl"] or 0 for r in data]
        pcts = [r["realized_pnl_pct"] or 0 for r in data]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        sum_losses = sum(losses)
        overall = {
            "total_trades": len(pnls),
            "total_pnl":    round(sum(pnls)),
            "avg_pnl":      round(sum(pnls) / len(pnls)),
            "avg_pnl_pct":  round(sum(pcts) / len(pcts), 2),
            "win_rate":     round(len(wins) / len(pnls) * 100, 1),
            "max_win":      round(max(pnls)),
            "max_loss":     round(min(pnls)),
            "avg_win":      round(sum(wins) / len(wins)) if wins else 0,
            "avg_loss":     round(sum_losses / len(losses)) if losses else 0,
            "profit_factor": round(abs(sum(wins) / sum_losses), 2) if sum_losses else 999,
            "avg_hold_days": round(
                sum((r["hold_days"] or 0) for r in data) / len(data), 1
            ),
        }

        from collections import defaultdict
        bucket: dict = defaultdict(list)
        for r in data:
            if group_by == "monthly":
                key = (r["trade_date"] or "")[:7]
            elif group_by == "strategy":
                key = (r["strategy"] or "미분류")
            else:
                key = r["trade_date"] or ""
            bucket[key].append(r)

        groups = []
        keys = sorted(bucket.keys()) if group_by != "strategy" else \
            sorted(bucket.keys(), key=lambda k: -sum((t["realized_pnl"] or 0) for t in bucket[k]))
        for k in keys:
            ts = bucket[k]
            tp = sum((t["realized_pnl"] or 0) for t in ts)
            tw = sum(1 for t in ts if (t["realized_pnl"] or 0) > 0)
            groups.append({
                "label": k, "trades": len(ts),
                "pnl": round(tp), "wins": tw,
                "win_rate": round(tw / len(ts) * 100, 1),
            })

        cum = 0
        curve = []
        for r in data:
            cum += r["realized_pnl"] or 0
            curve.append({"date": r["trade_date"], "cumulative": round(cum)})

        return jsonify({"overall": overall, "groups": groups, "equity_curve": curve})
    except Exception as exc:
        log.exception("journal/summary")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/journal/delete/<int:trade_id>", methods=["DELETE"])
def api_journal_delete(trade_id):
    if not (_SQLITE_OK and USE_SQLITE):
        return jsonify({"error": "SQLite 비활성"}), 503
    try:
        with _get_db() as conn:
            # 이 매수에 연결된 매도가 있는지 확인 → 함께 정리
            conn.execute("UPDATE trade_journal SET linked_buy_id = NULL "
                         "WHERE linked_buy_id = ?", (trade_id,))
            conn.execute("DELETE FROM trade_journal WHERE id = ?", (trade_id,))
            conn.commit()
        return jsonify({"status": "deleted", "id": trade_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── 🌍 글로벌 매크로 대시보드 ──────────────
@app.route("/api/global_macro")
def api_global_macro():
    """기존 macro_data.json + data.json + cache 통합. 30분 캐시."""
    cache_file = BASE_DIR / "cache" / "global_macro.json"
    try:
        if cache_file.exists():
            age_min = (now_kst().timestamp() - cache_file.stat().st_mtime) / 60
            if age_min < 30:
                return Response(cache_file.read_text(encoding="utf-8"),
                                content_type="application/json; charset=utf-8")
    except Exception:
        pass

    macro: dict = {}
    try:
        md = json.loads((BASE_DIR / "cache" / "macro_data.json").read_text(encoding="utf-8"))
        for it in md.get("items", []):
            macro[it.get("name")] = {
                "value":      it.get("value"),
                "change_pct": it.get("change_pct"),
                "change":     it.get("change"),
            }
    except Exception:
        pass

    dj: dict = {}
    try:
        if DATA_JSON.exists():
            dj = json.loads(DATA_JSON.read_text(encoding="utf-8"))
    except Exception:
        pass
    mo = (dj.get("market_overview") or {})

    sections: dict = {}

    # 1) 글로벌 주요 지수 (data.json + macro)
    idx_items = []
    for label, src in (
        ("🇰🇷 KOSPI",   dj.get("kospi")),
        ("🇰🇷 KOSDAQ",  dj.get("kosdaq")),
        ("🇺🇸 S&P 500", mo.get("sp500")),
        ("🇺🇸 NASDAQ",  mo.get("nasdaq")),
        ("🇺🇸 DOW",     mo.get("dow")),
    ):
        if isinstance(src, dict) and src.get("value") is not None:
            idx_items.append({"name": label, "value": src["value"],
                              "change_pct": src.get("change_pct")})
    sections["indices"] = {"title": "글로벌 주요 지수", "items": idx_items}

    # 2) 채권/금리 (장단기 금리차 자동 계산)
    bond_items = []
    for k, label in (("미국 10년물", "🇺🇸 10년물"),
                     ("US 2Y", "🇺🇸 2년물"),
                     ("US 10Y", "🇺🇸 10년물 (US 10Y)")):
        if k in macro and macro[k].get("value") is not None:
            bond_items.append({"name": label, "value": macro[k]["value"],
                               "change_pct": macro[k].get("change_pct"),
                               "unit": "%"})
    us10 = macro.get("미국 10년물", {}).get("value") or macro.get("US 10Y", {}).get("value")
    us2  = macro.get("US 2Y", {}).get("value")
    if us10 is not None and us2 is not None:
        sp = round(us10 - us2, 3)
        bond_items.append({
            "name": "📐 장단기 금리차 (10Y-2Y)",
            "value": sp, "unit": "%p",
            "signal": "역전 (경기침체 경고)" if sp < 0 else "정상",
            "is_inverted": sp < 0,
        })
    sections["bonds"] = {"title": "채권 & 금리", "items": bond_items}

    # 3) 통화/환율
    cur_items = []
    for k, label in (("DXY", "💵 달러인덱스"),
                     ("USD/KRW", "🇰🇷 원/달러"),
                     ("USD/JPY", "🇯🇵 엔/달러"),
                     ("USD/CNY", "🇨🇳 위안/달러"),
                     ("EUR/USD", "🇪🇺 유로/달러")):
        if k in macro and macro[k].get("value") is not None:
            cur_items.append({"name": label, "value": macro[k]["value"],
                              "change_pct": macro[k].get("change_pct")})
    sections["currencies"] = {"title": "통화 & 환율", "items": cur_items}

    # 4) 원자재
    com_items = []
    for k, label in (("WTI 원유", "🛢️ WTI 원유"),
                     ("브렌트유", "🛢️ 브렌트유"),
                     ("금",       "🥇 금"),
                     ("은",       "🥈 은"),
                     ("구리",     "🔶 구리"),
                     ("천연가스", "⛽ 천연가스")):
        if k in macro and macro[k].get("value") is not None:
            com_items.append({"name": label, "value": macro[k]["value"],
                              "change_pct": macro[k].get("change_pct")})
    sections["commodities"] = {"title": "원자재", "items": com_items}

    # 5) 변동성/심리
    sent_items = []
    if "VIX" in macro and macro["VIX"].get("value") is not None:
        v = macro["VIX"]["value"]
        regime = ("안정" if v < 15 else "보통" if v < 20
                  else "경계" if v < 25 else "공포" if v < 35 else "패닉")
        sent_items.append({"name": "😰 VIX", "value": v,
                           "change_pct": macro["VIX"].get("change_pct"),
                           "signal": regime})
    try:
        fg = json.loads((BASE_DIR / "cache" / "fear_greed.json").read_text(encoding="utf-8"))
        if fg.get("score") is not None:
            sent_items.append({"name": "🎭 공포탐욕", "value": fg["score"],
                               "signal": fg.get("rating_kr") or fg.get("rating", "")})
    except Exception:
        pass
    for sym in ("SPY", "QQQ"):
        try:
            opt = json.loads((BASE_DIR / "cache" / f"options_signal_{sym}.json")
                             .read_text(encoding="utf-8"))
            pcr = (opt.get("pcr") or {}).get("volume")
            ovr = opt.get("overall") or {}
            if pcr is not None:
                sent_items.append({"name": f"📊 {sym} PCR", "value": pcr,
                                   "signal": f"{ovr.get('emoji','')} {ovr.get('direction','')}"})
        except Exception:
            pass
    try:
        nf = json.loads((BASE_DIR / "cache" / "night_futures.json").read_text(encoding="utf-8"))
        if nf.get("night_close"):
            sent_items.append({"name": "🌙 코스피200 야간선물",
                               "value": nf["night_close"],
                               "change_pct": nf.get("change_pct"),
                               "signal": nf.get("signal", "")})
    except Exception:
        pass
    sections["sentiment"] = {"title": "변동성 & 심리", "items": sent_items}

    # 6) 디지털 자산
    crypto_items = []
    for k, label in (("BTC", "₿ 비트코인"), ("ETH", "Ξ 이더리움")):
        if k in macro and macro[k].get("value") is not None:
            crypto_items.append({"name": label, "value": macro[k]["value"],
                                 "change_pct": macro[k].get("change_pct")})
    sections["crypto"] = {"title": "디지털 자산", "items": crypto_items}

    result = {
        "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "sections": sections,
    }
    try:
        cache_file.parent.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    except Exception:
        pass
    return jsonify(result)


# ── CNN 공포탐욕지수 (Fear & Greed) ──────────────
@app.route("/api/fear_greed")
def api_fear_greed():
    """CNN F&G 우선, 실패 시 alternative.me 폴백. 30분 캐시."""
    cache_file = BASE_DIR / "cache" / "fear_greed.json"
    try:
        if cache_file.exists():
            age_min = (now_kst().timestamp() - cache_file.stat().st_mtime) / 60
            if age_min < 30:
                return Response(cache_file.read_text(encoding="utf-8"),
                                content_type="application/json; charset=utf-8")
    except Exception:
        pass

    rating_kr = {
        "Extreme Fear": "극단적 공포", "Fear": "공포",
        "Neutral": "중립", "Greed": "탐욕", "Extreme Greed": "극단적 탐욕",
    }
    result: dict = {"score": None, "rating": None, "source": None}

    # 1) CNN
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/120.0 Safari/537.36",
                     "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode("utf-8"))
        fg = d.get("fear_and_greed", {}) or {}
        score = fg.get("score")
        if score is not None:
            result = {
                "score": round(float(score)),
                "rating": fg.get("rating"),
                "rating_kr": rating_kr.get(fg.get("rating"), fg.get("rating", "")),
                "previous_close": round(float(fg.get("previous_close") or 0)),
                "previous_1_week": round(float(fg.get("previous_1_week") or 0)),
                "previous_1_month": round(float(fg.get("previous_1_month") or 0)),
                "source": "cnn",
                "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
            }
    except Exception as exc:
        log.debug("[fear_greed] CNN: %s", exc)

    # 2) alternative.me (크립토 F&G — CNN 실패 폴백)
    if result.get("score") is None:
        try:
            import urllib.request
            req = urllib.request.Request("https://api.alternative.me/fng/?limit=1",
                                         headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read().decode("utf-8"))
            v = (d.get("data") or [{}])[0]
            score = int(v.get("value", 50))
            cls = v.get("value_classification", "")
            result = {
                "score": score,
                "rating": cls,
                "rating_kr": rating_kr.get(cls, cls),
                "source": "alternative.me (crypto fallback)",
                "updated_at": now_kst().strftime("%Y-%m-%d %H:%M:%S"),
            }
        except Exception as exc:
            log.debug("[fear_greed] alt: %s", exc)

    if result.get("score") is not None:
        try:
            cache_file.parent.mkdir(exist_ok=True)
            cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    return jsonify(result)


# ── 한국투자증권 API (데이터 조회 전용 — 매매 X) ─────────────
import re as _re_kis


def _kis_valid_code(code: str) -> bool:
    return bool(_re_kis.fullmatch(r"\d{6}", code or ""))


@app.route("/api/kis/minute/<code>")
def api_kis_minute(code):
    """KIS 분봉 (1·3·5·10·15·30·60). 캐시: 장중 60s, 장외 1h."""
    if not _kis_valid_code(code):
        return jsonify({"error": "국내 종목만 지원", "candles": []}), 400
    interval = max(1, min(60, int(request.args.get("interval", 1))))
    try:
        from kis_api import get_minute_chart
        candles = get_minute_chart(code, interval)
        return jsonify({"code": code, "interval": interval,
                        "candles": candles, "count": len(candles)})
    except ImportError as exc:
        return jsonify({"error": f"kis_api 미설치: {exc}", "candles": []}), 500
    except Exception as exc:
        log.exception("kis minute %s", code)
        return jsonify({"error": str(exc), "candles": []}), 500


@app.route("/api/kis/orderbook/<code>")
def api_kis_orderbook(code):
    """KIS 호가 10단계. 캐시: 장중 5s, 장외 1h."""
    if not _kis_valid_code(code):
        return jsonify({"error": "국내 종목만 지원"}), 400
    try:
        from kis_api import get_orderbook
        d = get_orderbook(code)
        if not d:
            return jsonify({"error": "API 실패", "code": code}), 502
        d["code"] = code
        return jsonify(d)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/kis/investor/<code>")
def api_kis_investor(code):
    """KIS 외국인/기관/개인 일별 순매수. 캐시: 10분."""
    if not _kis_valid_code(code):
        return jsonify({"error": "국내 종목만 지원"}), 400
    try:
        from kis_api import get_investor_trading
        return jsonify({"code": code, "data": get_investor_trading(code) or []})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/kis/price/<code>")
def api_kis_price(code):
    """KIS 현재가 상세 (PER/PBR/52주/거래대금 등). 캐시: 장중 30s, 장외 1h."""
    if not _kis_valid_code(code):
        return jsonify({"error": "국내 종목만 지원"}), 400
    try:
        from kis_api import get_price_detail
        d = get_price_detail(code)
        if not d:
            return jsonify({"error": "API 실패"}), 502
        d["code"] = code
        return jsonify(d)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/sentiment/<code>")
def api_sentiment(code):
    """네이버 토론방 제목 기반 소셜 센티먼트 (KR만).
    키워드 사전: cache/sentiment_dict.json (편집 가능)."""
    import time as _t
    code = (code or "").zfill(6)
    now_ts = _t.time()
    cached = _SENTIMENT_CACHE.get(code)
    if cached and (now_ts - cached["fetched_at"] < _SENTIMENT_TTL):
        return jsonify({**cached["data"], "cached": True})

    sdict = _load_sentiment_dict()
    titles = _fetch_naver_board_titles(code, pages=3)
    if not titles:
        return jsonify({
            "code": code, "error": "네이버 토론방 수집 실패 또는 글 없음",
            "posts": 0, "label": "중립",
        })
    score = _score_sentiment_titles(titles, sdict)
    out = {
        "code": code,
        "source": "naver_board",
        "dict_version": sdict.get("version", 1),
        "sample_titles": titles[:10],
        **score,
    }
    _SENTIMENT_CACHE[code] = {"data": out, "fetched_at": now_ts}
    return jsonify({**out, "cached": False})


# ── 멀티 타임프레임 OHLCV ────────────────────────────────────
@app.route("/api/ohlcv/<code>")
def api_ohlcv_raw(code):
    """멀티 타임프레임 프론트 리샘플링용 raw 일봉 OHLCV.
    frontend에서 주/월 집계하므로 서버는 일봉만 리턴."""
    if not (_SQLITE_OK and USE_SQLITE):
        return jsonify({"error": "SQLite 비활성"}), 503
    days = int(request.args.get("days", 365))
    try:
        with _get_db() as conn:
            rows = conn.execute(
                "SELECT date, open, high, low, close, volume FROM ohlcv "
                "WHERE code=? ORDER BY date DESC LIMIT ?", (code, days)
            ).fetchall()
        data = [dict(r) for r in reversed(rows)]
        return jsonify({"code": code, "days": days, "rows": data})
    except Exception as exc:
        log.exception("ohlcv raw")
        return jsonify({"error": str(exc)}), 500


# gunicorn 이 모듈을 import 하는 시점에 자동 실행
_startup()


if __name__ == "__main__":
    ws_tag = " + WebSocket" if _SOCKETIO_OK else ""
    print(f"""
  ┌─────────────────────────────────────────┐
  │   테마 트리맵 서버{ws_tag:16s}        │
  │   http://{HOST}:{PORT}                   │
  └─────────────────────────────────────────┘
""")
    if _SOCKETIO_OK:
        socketio.run(app, host=HOST, port=PORT, debug=False,
                     use_reloader=False, allow_unsafe_werkzeug=True)
    else:
        app.run(host=HOST, port=PORT, debug=False, use_reloader=False)
