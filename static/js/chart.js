// ===== static/js/chart.js — 차트 패널 · 캔들 · RSI/MACD · 크로스헤어 · 탭 =====
'use strict';

// ─────────────────────────────────────────────────────────────────────────────
// CHART PANEL (Phase 6)
// ─────────────────────────────────────────────────────────────────────────────
// 단일 진실 원천은 _chartRequestCode (가장 최근 요청 코드).
// 이전 fetch 는 AbortController 로 실제로 취소해 네트워크/파서 단계에서 사라지게 한다.
let _chartRequestCode = null;
let _chartAbortCtrl   = null;

async function openChartPanel(code, hintName, marketOverride) {
  // marketOverride: 종목 발굴(all 모드) 에서 KR/US 혼재 상태일 때 명시적 지정
  const effectiveMarket = marketOverride || APP.market;
  console.log('[openChartPanel] ▶ entry', {
    code, hintName, market: effectiveMarket, _chartRequestCode_prev: _chartRequestCode,
  });

  code = String(code || '').trim();
  const isUS = effectiveMarket === 'us';
  const valid = isUS
    ? /^[A-Z][A-Z0-9.\-]{0,6}$/.test(code)
    : /^\d{6}$/.test(code);
  if (!valid) {
    console.error('[openChartPanel] ❌ invalid code:', code, 'market:', effectiveMarket);
    return;
  }

  // 🛑 이전 요청 실제 취소 (응답 파싱까지 중단). stale 렌더 원천 차단.
  if (_chartAbortCtrl) {
    try { _chartAbortCtrl.abort(); } catch {}
  }
  _chartAbortCtrl    = new AbortController();
  const myAbort      = _chartAbortCtrl;
  _chartRequestCode  = code;

  const panel = document.getElementById('chart-panel');
  panel.dataset.code = code;                // 패널이 어떤 종목을 담당 중인지 명시
  _openChartModal();                        // Phase 18: fixed overlay 표시

  // ── 헤더는 "유저가 클릭한" code/name 을 단일 진실로 사용. 서버 응답을 그대로 붙이지 않는다. ──
  const safeCode = _escHtml(code);
  const safeName = _escHtml(hintName || code);

  panel.innerHTML = `
    <div class="chart-header" data-header-code="${safeCode}">
      <span class="chart-title">${safeName} (${safeCode})</span>
      <div style="display:flex;gap:6px;align-items:center">
        <button class="tp-toggle-btn" id="chart-tp-btn" title="매매 패널">⚡</button>
        <button class="chart-close" id="chart-close-btn">✕ 닫기</button>
      </div>
    </div>
    <div class="chart-loading"><div class="spin"></div>차트 데이터 로딩 중… (${safeCode})</div>`;
  document.getElementById('chart-close-btn').addEventListener('click', closeChartPanel);
  document.getElementById('chart-tp-btn').addEventListener('click', () =>
    _toggleTradePanel(code, hintName || code, isUS ? 'us' : 'kr')
  );
  panel.scrollTop = 0;

  // Phase 14: 시장별 엔드포인트 (marketOverride 존중)
  const fetchUrl = isUS
    ? `/api/us/chart/${code}`
    : `/api/chart/${code}`;
  console.log('[openChartPanel] 🌐 fetch', fetchUrl);

  let data;
  try {
    const res = await fetch(fetchUrl, { cache: 'no-store', signal: myAbort.signal });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    data = await res.json();
    if (data.error) throw new Error(data.error);
    console.log('[openChartPanel] ✅ response', {
      requested:     code,
      returned_code: data.code,
      returned_name: data.name,
      candles: Array.isArray(data.close) ? data.close.length : 0,
    });
  } catch (err) {
    if (err.name === 'AbortError') {
      console.log('[openChartPanel] 🚫 aborted (newer click):', code);
      return;
    }
    if (_chartRequestCode !== code) {
      console.log('[openChartPanel] ⚠  error but stale, drop', { code, latest: _chartRequestCode });
      return;
    }
    panel.innerHTML = `
      <div class="chart-header" data-header-code="${safeCode}">
        <span class="chart-title">${safeName} (${safeCode})</span>
        <button class="chart-close" id="chart-close-btn">✕ 닫기</button>
      </div>
      <div class="chart-error">차트 로드 실패: ${_escHtml(err.message)}</div>`;
    document.getElementById('chart-close-btn').addEventListener('click', closeChartPanel);
    return;
  }

  // 응답이 돌아오는 사이 사용자가 다른 종목을 눌렀다면 이 결과는 버린다.
  if (_chartRequestCode !== code) {
    console.log('[openChartPanel] ⚠  stale response discarded:', code, '→ latest:', _chartRequestCode);
    return;
  }
  // 서버가 돌려준 코드와 요청한 코드가 다르면 서버/캐시 문제 — 렌더 대신 에러 패널.
  if (data.code && String(data.code) !== code) {
    console.error('[openChartPanel] ❌ server returned different code',
                  { requested: code, got: data.code });
    panel.innerHTML = `
      <div class="chart-header" data-header-code="${safeCode}">
        <span class="chart-title">${safeName} (${safeCode})</span>
        <button class="chart-close" id="chart-close-btn">✕ 닫기</button>
      </div>
      <div class="chart-error">서버 응답 코드 불일치 (요청 ${safeCode}, 응답 ${_escHtml(String(data.code))}) — 캐시 문제일 수 있습니다. 새로고침 후 재시도해 주세요.</div>`;
    document.getElementById('chart-close-btn').addEventListener('click', closeChartPanel);
    return;
  }

  const W = 1152, HC = 400, HV = 80;
  // 🔒 헤더는 유저가 클릭한 code/name 을 고정. 서버 응답의 data.name 은 부가 정보로만 사용.
  const displayName = _escHtml(hintName || data.name || code);

  // Phase 11: 초기 현재가/등락 — 서버가 돌려준 close 배열에서 계산
  const closes = data.close || [];
  const curPrice = closes.length ? closes[closes.length - 1] : 0;
  const prevPrice = closes.length >= 2 ? closes[closes.length - 2] : curPrice;
  const dayChg   = curPrice - prevPrice;
  const dayChgPct= prevPrice ? (dayChg / prevPrice * 100) : 0;
  const dayCol   = dayChg > 0 ? '#FF3333' : dayChg < 0 ? '#33AA33' : 'var(--text-muted)';
  const daySign  = dayChg > 0 ? '+' : '';
  const lastDate = (data.dates && data.dates[data.dates.length - 1]) || '';

  const favStar = isInWatchlist(code) ? '★' : '☆';
  const favClass = isInWatchlist(code) ? 'star-active' : '';

  panel.innerHTML = `
    <div class="chart-header" data-header-code="${safeCode}">
      <div class="chart-header-left">
        <div class="chart-title-row">
          <button class="watchlist-star-lg ${favClass}" id="chart-star-btn"
                  title="관심종목 토글">${favStar}</button>
          <button class="chart-alert-btn" id="chart-alert-btn" title="알림 설정">⏰</button>
          <span class="chart-title">${displayName}${_marketBadge(isUS ? 'US' : 'kr')} (${safeCode})</span>
        </div>
        <div class="chart-live-price" id="chart-live-price">
          <span class="live-price-value">${_fmtPrice(curPrice, isUS ? 'us' : 'kr')}</span>
          <span class="live-price-change" style="color:${dayCol}">
            ${daySign}${dayChg.toLocaleString(isUS ? 'en-US' : 'ko-KR', { maximumFractionDigits: 2 })} (${daySign}${dayChgPct.toFixed(2)}%)
          </span>
          <span class="live-price-time" id="chart-price-time">기준: ${_escHtml(lastDate)}</span>
          <span class="chart-afterhours" id="chart-afterhours" style="display:none"></span>
        </div>
      </div>
      <button class="chart-close" id="chart-close-btn">✕ 닫기</button>
    </div>

    <!-- Phase 13: 봉 타입 + Phase 12-1: 기간 + 12-3: 오버레이 토글 -->
    <div class="chart-controls-row">
      ${isUS ? '' : `
      <div class="chart-type-bar" id="chart-type-bar">
        <button class="chart-type-btn" data-type="1">1분</button>
        <button class="chart-type-btn" data-type="5">5분</button>
        <button class="chart-type-btn" data-type="15">15분</button>
        <button class="chart-type-btn" data-type="30">30분</button>
        <button class="chart-type-btn" data-type="60">60분</button>
        <button class="chart-type-btn active" data-type="day">일봉</button>
      </div>
      <span class="chart-divider">|</span>`}
      <div class="chart-period-bar" id="chart-period-bar-daily">
        <button class="chart-period-btn" data-days="30">1M</button>
        <button class="chart-period-btn" data-days="90">3M</button>
        <button class="chart-period-btn active" data-days="180">6M</button>
        <button class="chart-period-btn" data-days="365">1Y</button>
        <button class="chart-period-btn" data-days="1095">3Y</button>
      </div>
      <span class="chart-divider">|</span>
      <div class="chart-period-bar" id="chart-tf-bar">
        <button class="chart-period-btn active" data-tf="D">일</button>
        <button class="chart-period-btn" data-tf="W">주</button>
        <button class="chart-period-btn" data-tf="M">월</button>
      </div>
      <div class="chart-period-bar" id="chart-period-bar-intraday" style="display:none;">
        <button class="chart-period-btn active" data-idays="1">당일</button>
        <button class="chart-period-btn" data-idays="3">3일</button>
        <button class="chart-period-btn" data-idays="5">5일</button>
        <button class="chart-period-btn" data-idays="10">10일</button>
      </div>
      ${isUS ? '' : `
      <div class="chart-overlay-toggles">
        <button class="overlay-toggle" id="toggle-flow">수급 OFF</button>
      </div>`}
    </div>

    <div class="chart-canvas-wrap chart-cv-wrap">
      <canvas id="cv-candle" width="${W}" height="${HC}"></canvas>
      <canvas id="cv-candle-overlay" width="${W}" height="${HC}" class="chart-overlay"></canvas>
      <div id="ohlcv-bar" class="ohlcv-bar" style="display:none;">
        <span class="ohlcv-date" id="ohlcv-date"></span>
        <span class="ohlcv-label">시</span><span class="ohlcv-value" id="ohlcv-open"></span>
        <span class="ohlcv-label">고</span><span class="ohlcv-value" id="ohlcv-high"></span>
        <span class="ohlcv-label">저</span><span class="ohlcv-value" id="ohlcv-low"></span>
        <span class="ohlcv-label">종</span><span class="ohlcv-value" id="ohlcv-close"></span>
        <span class="ohlcv-label ohlcv-chg-label">등락</span><span class="ohlcv-value" id="ohlcv-chg"></span>
        <span class="ohlcv-label">거래량</span><span class="ohlcv-value" id="ohlcv-vol"></span>
      </div>
      <div id="crosshair-price" class="crosshair-axis-label crosshair-price" style="display:none;"></div>
    </div>

    <div class="chart-canvas-wrap chart-cv-wrap">
      <canvas id="cv-volume" width="${W}" height="${HV}"></canvas>
      <canvas id="cv-volume-overlay" width="${W}" height="${HV}" class="chart-overlay"></canvas>
      <div id="crosshair-date" class="crosshair-axis-label crosshair-date" style="display:none;"></div>
    </div>

    <div class="chart-canvas-wrap chart-cv-wrap" id="cv-rsi-wrap">
      <canvas id="cv-rsi" width="${W}" height="${HV}"></canvas>
    </div>
    <div class="chart-canvas-wrap chart-cv-wrap" id="cv-macd-wrap">
      <canvas id="cv-macd" width="${W}" height="${HV}"></canvas>
    </div>

    <div class="chart-tab-bar" id="chart-tab-bar">
      <button class="chart-tab-btn active" data-ctab="analysis">${isUS ? 'Analysis' : '기술적 분석'}</button>
      ${isUS ? '' : `<button class="chart-tab-btn" data-ctab="orderbook">호가</button>
      <button class="chart-tab-btn" data-ctab="kisdetail">KIS상세</button>`}
      <button class="chart-tab-btn"        data-ctab="reports">${isUS ? 'Reports' : '리포트'}</button>
      <button class="chart-tab-btn"        data-ctab="news">${isUS ? 'News' : '뉴스'}</button>
      <button class="chart-tab-btn"        data-ctab="financial">${isUS ? 'Financial' : '재무'}</button>
      <button class="chart-tab-btn"        data-ctab="peers">${isUS ? 'Peers' : '동종비교'}</button>
    </div>
    <div id="chart-tab-content"></div>`;
  document.getElementById('chart-close-btn').addEventListener('click', closeChartPanel);
  document.getElementById('chart-star-btn').addEventListener('click', _toggleChartStar);
  document.getElementById('chart-alert-btn').addEventListener('click', () => {
    _showAlertModal(code, hintName || code, isUS ? 'us' : 'kr');
  });

  _drawCandles(document.getElementById('cv-candle'), data, W, HC);
  _drawVolume(document.getElementById('cv-volume'),  data, W, HV, null);
  console.log('[chart] rsi_macd in response:', !!data.rsi_macd,
    'rsi len:', data.rsi_macd?.rsi?.length,
    'cv-rsi:', !!document.getElementById('cv-rsi'));
  _drawRSI(document.getElementById('cv-rsi'), data, W, HV);
  _drawMACD(document.getElementById('cv-macd'), data, W, HV);
  // 크로스헤어 최적화: 초기 렌더 결과를 이미지로 저장 (매번 재그리기 방지)
  for (const cvId of ['cv-rsi', 'cv-macd']) {
    const cv = document.getElementById(cvId);
    if (cv) cv._savedImg = cv.getContext('2d').getImageData(0, 0, cv.width, cv.height);
  }

  // Phase 10/11: 차트 패널 상태 + 기본 '기술적 분석' + 크로스헤어 + 가격 polling
  CHART_STATE.code        = code;
  CHART_STATE.market      = isUS ? 'us' : 'kr';   // openChartPanel 로컬 isUS 기반
  CHART_STATE.data        = data;
  CHART_STATE.dataDaily   = data;
  CHART_STATE.tf          = 'D';
  CHART_STATE.tab         = 'analysis';
  CHART_STATE.days        = data.days || 180;
  CHART_STATE.flowVisible = false;
  CHART_STATE.flowData    = null;
  _wsSubscribeChart(code, isUS ? 'us' : 'kr');
  if (isUS) _loadUsExtended(code);
  else _loadAfterHours(code);
  // 공시 이벤트 (KR만) — 로드 후 캔들 재그리기
  CHART_STATE.disclosureEvents = null;
  CHART_STATE.disclosureCode = null;
  if (!isUS) {
    _loadDisclosureEvents(code).then(events => {
      if (CHART_STATE.code !== code) return;
      if (events && events.length) {
        _drawCandles(document.getElementById('cv-candle'), CHART_STATE.data, W, HC);
      }
    });
  }
  _renderAnalysis(document.getElementById('chart-tab-content'), data.analysis);

  // 기본 선택된 기간 버튼 하이라이트
  document.querySelectorAll('#chart-period-bar-daily .chart-period-btn').forEach(b => {
    b.classList.toggle('active', parseInt(b.dataset.days) === CHART_STATE.days);
  });
  // Phase 13: 봉 타입 초기화
  CHART_STATE.chartType    = 'day';
  CHART_STATE.intradayDays = 1;

  document.getElementById('chart-tab-bar').addEventListener('click', _chartTabClick);
  const typeBar = document.getElementById('chart-type-bar');
  if (typeBar) typeBar.addEventListener('click', _chartTypeClick);
  document.getElementById('chart-period-bar-daily').addEventListener('click', _chartPeriodClick);
  document.getElementById('chart-period-bar-intraday').addEventListener('click', _chartIntradayPeriodClick);
  const tfBar = document.getElementById('chart-tf-bar');
  if (tfBar) tfBar.addEventListener('click', _chartTfClick);
  const flowBtn = document.getElementById('toggle-flow');
  if (flowBtn) flowBtn.addEventListener('click', _toggleFlowOverlay);

  _setupCrosshair(data, W, HC, HV);
  _startChartPricePolling(code);
}

// Phase 12-1: 일봉 기간 버튼 클릭
async function _chartPeriodClick(e) {
  const btn = e.target.closest('.chart-period-btn');
  if (!btn) return;
  const days = parseInt(btn.dataset.days);
  if (!days || days === CHART_STATE.days) return;
  const code = CHART_STATE.code;
  if (!code) return;

  document.querySelectorAll('#chart-period-bar-daily .chart-period-btn').forEach(b => {
    b.classList.toggle('active', b === btn);
  });

  try {
    const base = CHART_STATE.market === 'us' ? '/api/us/chart' : '/api/chart';
    const r = await fetch(`${base}/${code}?days=${days}`, { cache: 'no-store' });
    const d = await r.json();
    if (!r.ok || d.error) throw new Error(d.error || ('HTTP ' + r.status));
    if (CHART_STATE.code !== code) return;

    CHART_STATE.data = d;
    CHART_STATE.dataDaily = d;
    CHART_STATE.tf = 'D';
    document.querySelectorAll('#chart-tf-bar .chart-period-btn').forEach(b => {
      b.classList.toggle('active', b.dataset.tf === 'D');
    });
    CHART_STATE.days = days;
    const W = 1152, HC = 400, HV = 80;
    _drawCandles(document.getElementById('cv-candle'), d, W, HC);
    _drawVolume(document.getElementById('cv-volume'), d, W, HV,
                CHART_STATE.flowVisible ? CHART_STATE.flowData : null);
    _setupCrosshair(d, W, HC, HV);
    if (CHART_STATE.tab === 'analysis') {
      _renderAnalysis(document.getElementById('chart-tab-content'), d.analysis);
    }
    console.log('[period:day]', code, days, '→', d.close.length);
  } catch (err) {
    console.error('[period:day] fetch failed', err);
  }
}

// ── 멀티 타임프레임 (일/주/월) 프론트 리샘플링 ──
function _chartTfClick(e) {
  const btn = e.target.closest('.chart-period-btn');
  if (!btn || !btn.dataset.tf) return;
  const tf = btn.dataset.tf;
  if (tf === CHART_STATE.tf) return;
  document.querySelectorAll('#chart-tf-bar .chart-period-btn').forEach(b => {
    b.classList.toggle('active', b === btn);
  });
  CHART_STATE.tf = tf;
  const base = CHART_STATE.dataDaily;
  if (!base) return;
  const resampled = tf === 'D' ? base : _resampleOHLCV(base, tf);
  CHART_STATE.data = resampled;
  const W = 1152, HC = 400, HV = 80;
  _drawCandles(document.getElementById('cv-candle'), resampled, W, HC);
  _drawVolume(document.getElementById('cv-volume'), resampled, W, HV, null);
  _setupCrosshair(resampled, W, HC, HV);
  if (CHART_STATE.tab === 'analysis') {
    _renderAnalysis(document.getElementById('chart-tab-content'), resampled.analysis);
  }
}

// daily OHLCV → 주봉/월봉 집계 (open=첫날 open, high=max, low=min, close=마지막 close, volume=sum)
function _resampleOHLCV(src, tf) {
  const dates = src.dates || [];
  const op = src.open || [], hi = src.high || [], lo = src.low || [],
        cl = src.close || [], vl = src.volume || [];
  const n = dates.length;
  if (!n) return src;
  const bucketKey = (dateStr) => {
    const [y, m, dd] = (dateStr || '').split('-').map(Number);
    if (!y) return dateStr;
    if (tf === 'M') return `${y}-${String(m).padStart(2, '0')}`;
    // 주: ISO week (월요일 기준)
    const d = new Date(Date.UTC(y, m - 1, dd));
    const day = d.getUTCDay() || 7;
    d.setUTCDate(d.getUTCDate() + 4 - day);
    const yStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
    const week = Math.ceil(((d - yStart) / 86400000 + 1) / 7);
    return `${d.getUTCFullYear()}-W${String(week).padStart(2, '0')}`;
  };
  const out = { dates: [], open: [], high: [], low: [], close: [], volume: [] };
  let curKey = null, bOpen, bHigh, bLow, bClose, bVol, bDate;
  const flush = () => {
    out.dates.push(bDate); out.open.push(bOpen); out.high.push(bHigh);
    out.low.push(bLow); out.close.push(bClose); out.volume.push(bVol);
  };
  for (let i = 0; i < n; i++) {
    const k = bucketKey(dates[i]);
    if (k !== curKey) {
      if (curKey !== null) flush();
      curKey = k;
      bOpen = op[i]; bHigh = hi[i]; bLow = lo[i]; bClose = cl[i]; bVol = vl[i] || 0;
      bDate = dates[i];
    } else {
      bHigh = Math.max(bHigh, hi[i] ?? bHigh);
      bLow = Math.min(bLow, lo[i] ?? bLow);
      bClose = cl[i];
      bVol += vl[i] || 0;
      bDate = dates[i];
    }
  }
  if (curKey !== null) flush();
  // 다른 필드는 유지 (analysis 등은 일봉 그대로 - 보조지표 재계산은 scope 外)
  out.days = src.days; out.name = src.name;
  out.analysis = src.analysis;
  return out;
}

// Phase 13: 봉 타입 (일봉/분봉) 전환
async function _chartTypeClick(e) {
  const btn = e.target.closest('.chart-type-btn');
  if (!btn) return;
  const type = btn.dataset.type;
  if (type === CHART_STATE.chartType) return;

  document.querySelectorAll('#chart-type-bar .chart-type-btn').forEach(b => {
    b.classList.toggle('active', b === btn);
  });
  CHART_STATE.chartType = type;

  // 기간 바 토글
  const dailyBar    = document.getElementById('chart-period-bar-daily');
  const intradayBar = document.getElementById('chart-period-bar-intraday');
  if (type === 'day') {
    dailyBar.style.display    = 'flex';
    intradayBar.style.display = 'none';
    _stopIntradayRefresh();
    await _loadDailyForCurrent();
  } else {
    dailyBar.style.display    = 'none';
    intradayBar.style.display = 'flex';
    await _loadIntradayForCurrent();
    _startIntradayRefresh();
  }
}

// Phase 13: 분봉 기간 버튼 (당일/3일/5일/10일)
async function _chartIntradayPeriodClick(e) {
  const btn = e.target.closest('.chart-period-btn');
  if (!btn) return;
  const idays = parseInt(btn.dataset.idays);
  if (!idays || idays === CHART_STATE.intradayDays) return;
  document.querySelectorAll('#chart-period-bar-intraday .chart-period-btn').forEach(b => {
    b.classList.toggle('active', b === btn);
  });
  CHART_STATE.intradayDays = idays;
  await _loadIntradayForCurrent();
}

async function _loadDailyForCurrent() {
  const code = CHART_STATE.code;
  if (!code) return;
  const days = CHART_STATE.days || 180;
  try {
    const base = CHART_STATE.market === 'us' ? '/api/us/chart' : '/api/chart';
    const r = await fetch(`${base}/${code}?days=${days}`, { cache: 'no-store' });
    const d = await r.json();
    if (CHART_STATE.code !== code) return;
    if (!r.ok || d.error) throw new Error(d.error || ('HTTP ' + r.status));
    CHART_STATE.data = d;
    const W = 1152, HC = 400, HV = 80;
    _drawCandles(document.getElementById('cv-candle'), d, W, HC);
    _drawVolume(document.getElementById('cv-volume'), d, W, HV,
                CHART_STATE.flowVisible ? CHART_STATE.flowData : null);
    _setupCrosshair(d, W, HC, HV);
    if (CHART_STATE.tab === 'analysis') {
      _renderAnalysis(document.getElementById('chart-tab-content'), d.analysis);
    }
  } catch (err) { console.error('[load:day]', err); }
}

async function _loadIntradayForCurrent() {
  const code = CHART_STATE.code;
  if (!code) return;
  const tf    = CHART_STATE.chartType;     // '1' | '5' | '15' | '30' | '60'
  const idays = CHART_STATE.intradayDays || 1;

  // 로딩 표시
  const cvc = document.getElementById('cv-candle');
  const ctx = cvc.getContext('2d');
  ctx.clearRect(0, 0, cvc.width, cvc.height);
  ctx.fillStyle = '#1C1C1E'; ctx.fillRect(0, 0, cvc.width, cvc.height);
  ctx.fillStyle = '#AEAEB2'; ctx.font = '14px Noto Sans KR, sans-serif';
  ctx.textAlign = 'center';
  ctx.fillText(`${tf}분봉 데이터 로딩 중…`, cvc.width / 2, cvc.height / 2);

  try {
    const r = await fetch(`/api/chart_intraday/${code}?timeframe=${tf}&days=${idays}`,
                          { cache: 'no-store' });
    const d = await r.json();
    if (CHART_STATE.code !== code || CHART_STATE.chartType !== tf) return;   // stale
    if (!r.ok || d.error) {
      ctx.clearRect(0, 0, cvc.width, cvc.height);
      ctx.fillStyle = '#1C1C1E'; ctx.fillRect(0, 0, cvc.width, cvc.height);
      ctx.fillStyle = '#FF6B6B';
      ctx.fillText(d.error || ('HTTP ' + r.status), cvc.width / 2, cvc.height / 2);
      return;
    }
    CHART_STATE.data = d;
    const W = 1152, HC = 400, HV = 80;
    _drawCandles(document.getElementById('cv-candle'), d, W, HC);
    _drawVolume(document.getElementById('cv-volume'), d, W, HV, null);
    _setupCrosshair(d, W, HC, HV);
    if (CHART_STATE.tab === 'analysis') {
      _renderAnalysis(document.getElementById('chart-tab-content'), d.analysis);
    }
    console.log('[load:intraday]', code, `${tf}분`, `${idays}d`, '→', d.close.length, 'candles');
  } catch (err) {
    console.error('[load:intraday]', err);
  }
}

// Phase 13: 분봉 자동 갱신 (장중에만)
let _intradayRefreshTimer = null;

function _isMarketHoursClient() {
  const now = new Date();
  const day = now.getDay();
  if (day === 0 || day === 6) return false;
  const t = now.getHours() * 100 + now.getMinutes();
  return t >= 900 && t <= 1530;
}

function _startIntradayRefresh() {
  _stopIntradayRefresh();
  const tf = CHART_STATE.chartType;
  if (tf === 'day') return;
  // 1분봉은 60s 마다, 나머지는 300s (5분) 마다
  const intervalMs = tf === '1' ? 60_000 : 300_000;
  _intradayRefreshTimer = setInterval(() => {
    if (!_isMarketHoursClient()) return;
    if (document.hidden) return;
    if (CHART_STATE.chartType === 'day') return;
    if (!CHART_STATE.code) return;
    _loadIntradayForCurrent();
  }, intervalMs);
  console.log('[intraday refresh]', tf, '분봉 자동 갱신 시작 (', intervalMs / 1000, 's)');
}

function _stopIntradayRefresh() {
  if (_intradayRefreshTimer) {
    clearInterval(_intradayRefreshTimer);
    _intradayRefreshTimer = null;
  }
}

// Phase 12-2: 차트 헤더의 ☆ 버튼 토글
function _toggleChartStar() {
  const code = CHART_STATE.code;
  if (!code) return;
  const name = (CHART_STATE.data && CHART_STATE.data.name) || code;
  const added = toggleWatchlist(code, name);
  const btn = document.getElementById('chart-star-btn');
  if (btn) {
    btn.textContent = added ? '★' : '☆';
    btn.classList.toggle('star-active', added);
  }
}

// Phase 12-3: 수급 오버레이 토글
async function _toggleFlowOverlay() {
  const btn = document.getElementById('toggle-flow');
  if (!btn) return;
  CHART_STATE.flowVisible = !CHART_STATE.flowVisible;

  if (CHART_STATE.flowVisible && !CHART_STATE.flowData) {
    btn.textContent = '수급 로딩…';
    btn.disabled = true;
    try {
      const r = await fetch(`/api/flow/${CHART_STATE.code}`);
      const d = await r.json();
      if (!r.ok || d.error) throw new Error(d.error || ('HTTP ' + r.status));
      CHART_STATE.flowData = d;
    } catch (err) {
      CHART_STATE.flowVisible = false;
      btn.textContent = '수급 없음';
      btn.disabled = false;
      setTimeout(() => { btn.textContent = '수급 OFF'; }, 1500);
      return;
    }
    btn.disabled = false;
  }
  btn.textContent = CHART_STATE.flowVisible ? '수급 ON' : '수급 OFF';
  btn.classList.toggle('active', CHART_STATE.flowVisible);

  const W = 1152, HV = 80;
  _drawVolume(document.getElementById('cv-volume'), CHART_STATE.data, W, HV,
              CHART_STATE.flowVisible ? CHART_STATE.flowData : null);
}

// Phase 10: chart panel 3-tab (analysis/reports/news) controller
const CHART_STATE = { code: null, market: 'kr', data: null, dataDaily: null, tab: 'analysis', days: 180, tf: 'D', flowVisible: false, flowData: null, disclosureEvents: null, disclosureCode: null };

function _chartTabClick(e) {
  const btn = e.target.closest('.chart-tab-btn');
  if (!btn) return;
  const tab = btn.dataset.ctab;
  if (tab === CHART_STATE.tab) return;
  document.querySelectorAll('#chart-tab-bar .chart-tab-btn')
    .forEach(b => b.classList.toggle('active', b === btn));
  CHART_STATE.tab = tab;
  const container = document.getElementById('chart-tab-content');
  // 호가 폴링은 호가 탭 떠날 때마다 종료
  if (tab !== 'orderbook') _stopOrderbookPolling();

  if (tab === 'analysis') {
    _renderAnalysis(container, CHART_STATE.data && CHART_STATE.data.analysis);
  } else if (tab === 'orderbook') {
    _startOrderbookPolling(CHART_STATE.code, container);
  } else if (tab === 'kisdetail') {
    _loadKisDetail(CHART_STATE.code, container);
  } else if (tab === 'reports') {
    _loadReports(CHART_STATE.code, container);
  } else if (tab === 'news') {
    _loadNews(CHART_STATE.code, container);
  } else if (tab === 'financial') {
    _loadFinancial(CHART_STATE.code, container);
  } else if (tab === 'peers') {
    _loadPeers(CHART_STATE.code, container);
  }
}

// ── KIS 호가 (5초 폴링) ────────────────────────
let _obInterval = null;
let _obAbort = null;

function _stopOrderbookPolling() {
  if (_obInterval) { clearInterval(_obInterval); _obInterval = null; }
  if (_obAbort) { try { _obAbort.abort(); } catch {} _obAbort = null; }
}

function _startOrderbookPolling(code, container) {
  _stopOrderbookPolling();
  container.innerHTML = '<div class="chart-loading"><div class="spin"></div>호가 로딩 중…</div>';
  _loadOrderbookOnce(code, container);
  _obInterval = setInterval(() => _loadOrderbookOnce(code, container), 5000);
}

async function _loadOrderbookOnce(code, container) {
  if (CHART_STATE.code !== code) { _stopOrderbookPolling(); return; }
  try {
    if (_obAbort) try { _obAbort.abort(); } catch {}
    _obAbort = new AbortController();
    const r = await fetch(`/api/kis/orderbook/${code}`, { signal: _obAbort.signal });
    const d = await r.json();
    if (CHART_STATE.code !== code) return;
    if (d.error) {
      container.innerHTML = `<div class="chart-empty"><div class="chart-empty-title">호가 로드 실패</div>${_escHtml(d.error)}</div>`;
      _stopOrderbookPolling();
      return;
    }
    _renderOrderbook(d, container);
  } catch (e) {
    if (e.name !== 'AbortError') {
      container.innerHTML = `<div class="chart-empty">호가 요청 실패: ${_escHtml(e.message)}</div>`;
    }
  }
}

function _renderOrderbook(d, container) {
  const asks = d.asks || [];
  const bids = d.bids || [];
  const maxQty = Math.max(...asks.map(a => a.qty || 0), ...bids.map(b => b.qty || 0), 1);
  const fmt = (n) => Number(n || 0).toLocaleString();

  let html = `<div class="ob-container">
    <div class="ob-summary">
      <span class="ob-summary-ask">매도 ${fmt(d.total_ask_qty)}</span>
      <span class="ob-summary-time">${_escHtml(d.timestamp || '')} <span class="ws-live-dot"></span> LIVE</span>
      <span class="ob-summary-bid">매수 ${fmt(d.total_bid_qty)}</span>
    </div>`;

  // 매도 (역순으로 가격 높은 것부터)
  [...asks].reverse().forEach(a => {
    const pct = (a.qty / maxQty * 100).toFixed(1);
    html += `<div class="ob-row">
      <div class="ob-bar-cell"><div class="ob-bar ask" style="width:${pct}%"></div></div>
      <div class="ob-qty ask">${fmt(a.qty)}</div>
      <div class="ob-price ask">₩${fmt(a.price)}</div>
      <div class="ob-qty"></div>
      <div class="ob-bar-cell"></div>
    </div>`;
  });
  html += '<div class="ob-divider"></div>';
  // 매수 (가격 높은 것부터)
  bids.forEach(b => {
    const pct = (b.qty / maxQty * 100).toFixed(1);
    html += `<div class="ob-row">
      <div class="ob-bar-cell"></div>
      <div class="ob-qty"></div>
      <div class="ob-price bid">₩${fmt(b.price)}</div>
      <div class="ob-qty bid">${fmt(b.qty)}</div>
      <div class="ob-bar-cell"><div class="ob-bar bid" style="width:${pct}%"></div></div>
    </div>`;
  });
  html += '</div>';
  container.innerHTML = html;
}

// ── KIS 현재가 상세 + 투자자 매매동향 ───────────────
async function _loadKisDetail(code, container) {
  container.innerHTML = '<div class="chart-loading"><div class="spin"></div>KIS 데이터 로딩 중…</div>';
  try {
    const [pr, iv] = await Promise.all([
      fetch(`/api/kis/price/${code}`).then(r => r.json()),
      fetch(`/api/kis/investor/${code}`).then(r => r.json()),
    ]);
    if (CHART_STATE.code !== code) return;
    if (pr.error) {
      container.innerHTML = `<div class="chart-empty">${_escHtml(pr.error)}</div>`;
      return;
    }
    const fmt = (n) => Number(n || 0).toLocaleString();
    const sign = (n) => (n >= 0 ? '+' : '');
    const pnlCol = (n) => n >= 0 ? '#ef4444' : '#22c55e';

    let html = `<div class="kisd-wrap">
      <div class="kisd-section">
        <div class="kisd-title">📊 시세 상세</div>
        <div class="kisd-grid">
          <div class="kisd-item"><span>현재가</span><b>₩${fmt(pr.price)}</b><i style="color:${pnlCol(pr.change)}">${sign(pr.change_pct)}${pr.change_pct}%</i></div>
          <div class="kisd-item"><span>시가</span><b>₩${fmt(pr.open)}</b></div>
          <div class="kisd-item"><span>고가</span><b style="color:#ef4444">₩${fmt(pr.high)}</b></div>
          <div class="kisd-item"><span>저가</span><b style="color:#22c55e">₩${fmt(pr.low)}</b></div>
          <div class="kisd-item"><span>거래량</span><b>${fmt(pr.volume)}</b></div>
          <div class="kisd-item"><span>거래대금</span><b>${fmt(Math.round((pr.trade_amount||0)/1e8))}억</b></div>
          <div class="kisd-item"><span>PER</span><b>${pr.per || '—'}</b></div>
          <div class="kisd-item"><span>PBR</span><b>${pr.pbr || '—'}</b></div>
          <div class="kisd-item"><span>EPS</span><b>${fmt(pr.eps)}</b></div>
          <div class="kisd-item"><span>시총</span><b>${fmt(pr.market_cap)}억</b></div>
          <div class="kisd-item"><span>52주 고가</span><b>₩${fmt(pr.high_52w)}</b></div>
          <div class="kisd-item"><span>52주 저가</span><b>₩${fmt(pr.low_52w)}</b></div>
        </div>
      </div>`;

    const inv = (iv && iv.data) || [];
    if (inv.length) {
      html += `<div class="kisd-section">
        <div class="kisd-title">💰 투자자별 순매수 (최근 5일, 단위: 주)</div>
        <table class="kisd-inv-table">
          <thead><tr><th>일자</th><th class="r">외국인</th><th class="r">기관</th><th class="r">개인</th></tr></thead>
          <tbody>`;
      inv.slice(0, 5).forEach(d => {
        const dt = (d.date || '').replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3');
        html += `<tr>
          <td>${_escHtml(dt)}</td>
          <td class="r" style="color:${pnlCol(d.foreign_net)}">${sign(d.foreign_net)}${fmt(d.foreign_net)}</td>
          <td class="r" style="color:${pnlCol(d.inst_net)}">${sign(d.inst_net)}${fmt(d.inst_net)}</td>
          <td class="r" style="color:${pnlCol(d.retail_net)}">${sign(d.retail_net)}${fmt(d.retail_net)}</td>
        </tr>`;
      });
      html += '</tbody></table></div>';
    }
    html += '</div>';
    container.innerHTML = html;
  } catch (e) {
    container.innerHTML = `<div class="chart-empty">KIS 로드 실패: ${_escHtml(e.message)}</div>`;
  }
}

async function _loadPeers(code, container) {
  const isUS = CHART_STATE.market === 'us';
  container.innerHTML = '<div class="chart-loading"><div class="spin"></div>동종 업계 데이터 로딩 중…</div>';
  try {
    const url = isUS ? `/api/us/peers/${code}` : `/api/peers/${code}`;
    const r = await fetch(url);
    const d = await r.json();
    if (CHART_STATE.tab !== 'peers' || CHART_STATE.code !== code) return;
    if (d.error || !(d.peers || []).length) {
      container.innerHTML = `<div class="chart-empty">
        <div class="chart-empty-title">동종 업계 데이터 없음</div>${_escHtml(d.error || '—')}
      </div>`;
      return;
    }
    const cur = isUS ? '$' : '₩';
    const avgPer = d.sector_avg?.per;
    const avgPbr = d.sector_avg?.pbr;

    const rows = (d.peers || []).map(p => {
      const isTarget = (p.symbol === code) || (p.code === code);
      const chg = p.change_pct || 0;
      const chgCol = chg > 0 ? '#FF3333' : chg < 0 ? '#33AA33' : 'var(--text-muted)';
      const sign = chg > 0 ? '+' : '';
      const per = p.per;
      const pbr = p.pbr;
      const perUnder = (avgPer && per && per > 0 && per < avgPer * 0.9);
      const pbrUnder = (avgPbr && pbr && pbr > 0 && pbr < avgPbr * 0.9);
      const priceStr = p.price != null
        ? (isUS ? '$' + Number(p.price).toFixed(2) : '₩' + Number(p.price).toLocaleString())
        : '—';
      const capStr = (() => {
        const mc = p.market_cap;
        if (!mc) return '—';
        if (isUS) {
          if (mc >= 1e12) return '$' + (mc / 1e12).toFixed(2) + 'T';
          if (mc >= 1e9)  return '$' + (mc / 1e9).toFixed(0) + 'B';
          return '$' + Math.round(mc / 1e6) + 'M';
        }
        return '—';
      })();

      return `<tr class="peer-row ${isTarget ? 'peer-row-target' : ''}"
                  data-code="${_escHtml(p.code || p.symbol || '')}"
                  data-name="${_escHtml(p.name || '')}">
        <td class="peer-name-cell">
          ${isTarget ? '<span class="peer-target-mark">▶</span> ' : ''}
          <span class="peer-name">${_escHtml(p.name || '')}</span>
          <span class="peer-code">${_escHtml(p.code || p.symbol || '')}</span>
        </td>
        <td class="r">${priceStr}</td>
        <td class="r" style="color:${chgCol};font-weight:700">${sign}${chg.toFixed(2)}%</td>
        ${isUS ? `<td class="r">${capStr}</td>` : ''}
        <td class="r ${perUnder ? 'peer-undervalued' : ''}">${per != null ? per : '—'}</td>
        <td class="r ${pbrUnder ? 'peer-undervalued' : ''}">${pbr != null ? pbr : '—'}</td>
        ${isUS ? `<td class="r">${p.roe != null ? p.roe + '%' : '—'}</td>` : ''}
      </tr>`;
    }).join('');

    const avgBar = `<div class="peer-avg-bar">
      섹터 평균 — PER <b>${avgPer ?? '—'}</b> · PBR <b>${avgPbr ?? '—'}</b>
      ${avgPer ? `<span class="peer-avg-note">(종목 PER 이 평균의 90% 미만이면 <b class="peer-undervalued">저평가</b> 금색 강조)</span>` : ''}
    </div>`;

    container.innerHTML = `
      <div class="peer-wrap">
        <div class="peer-header">
          <span class="peer-sector">${_escHtml(d.sector || '—')}</span>
          <span class="peer-count">${d.peer_count}종목 (${isUS ? '시총' : '거래대금'} 상위)</span>
        </div>
        ${avgBar}
        <div class="peer-table-scroll">
          <table class="peer-table">
            <thead><tr>
              <th>종목</th>
              <th class="r">현재가</th>
              <th class="r">등락률</th>
              ${isUS ? '<th class="r">시총</th>' : ''}
              <th class="r">PER</th>
              <th class="r">PBR</th>
              ${isUS ? '<th class="r">ROE</th>' : ''}
            </tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
        ${!isUS ? '<div class="peer-note">⚠ KR PER/PBR 은 사용자가 상세 조회한 종목만 표시됩니다 (cache/financial_*.json 재사용).</div>' : ''}
      </div>`;

    container.querySelector('tbody').addEventListener('click', (e) => {
      const tr = e.target.closest('tr.peer-row');
      if (!tr || !tr.dataset.code) return;
      openChartPanel(tr.dataset.code, tr.dataset.name, isUS ? 'us' : 'kr');
    });
  } catch (err) {
    container.innerHTML = `<div class="chart-empty">동종비교 로드 실패: ${_escHtml(err.message)}</div>`;
  }
}

async function _loadNews(code, container) {
  const isUS = CHART_STATE.market === 'us';
  container.innerHTML = '<div class="chart-loading"><div class="spin"></div>뉴스 로딩 중…</div>';
  try {
    const base = isUS ? '/api/us/news' : '/api/news';
    const r = await fetch(`${base}/${code}`);
    const d = await r.json();
    if (CHART_STATE.tab !== 'news' || CHART_STATE.code !== code) return;
    if (!r.ok || d.error) {
      container.innerHTML = `<div class="chart-empty">
        <div class="chart-empty-title">뉴스를 불러올 수 없음</div>
        ${d.error || ('HTTP ' + r.status)}
        ${d.hint ? `<br><span style="color:var(--text-sub)">${d.hint}</span>` : ''}
      </div>`;
      return;
    }
    if (!d.items || !d.items.length) {
      container.innerHTML = '<div class="chart-empty">관련 뉴스가 없습니다.</div>';
      return;
    }
    // US 뉴스는 description 이 없고 thumbnail 이 있음. KR 뉴스는 그 반대.
    const rows = d.items.map(it => {
      const thumb = it.thumbnail
        ? `<img class="news-thumb" src="${_escHtml(it.thumbnail)}" alt="" loading="lazy">`
        : '';
      const desc = it.description
        ? `<div class="news-desc">${_escHtml(it.description)}</div>`
        : '';
      return `<a class="news-item" href="${_escHtml(it.link)}" target="_blank" rel="noopener">
        <div class="news-item-row">
          ${thumb}
          <div class="news-item-content">
            <div class="news-title">${_escHtml(it.title)}</div>
            ${desc}
            <div class="news-meta">
              <span class="news-source">${_escHtml(it.source || '')}</span>
              <span class="news-time">${_escHtml(it.timeAgo || '')}</span>
            </div>
          </div>
        </div>
      </a>`;
    }).join('');
    const headerName = d.name || d.symbol || code;
    container.innerHTML = `
      <div class="news-list">
        <div class="news-header">${_escHtml(headerName)} ${isUS ? 'News' : '관련 뉴스'} (${d.count}건)</div>
        ${rows}
      </div>`;
  } catch (err) {
    container.innerHTML = `<div class="chart-empty">뉴스 로드 실패: ${err.message}</div>`;
  }
}

async function _loadReports(code, container) {
  if (CHART_STATE.market === 'us') {
    container.innerHTML = `
      <div class="chart-empty">
        <div class="chart-empty-title">📑 미국 종목 리포트 미제공</div>
        미국 종목의 증권사 리포트는 현재 수집 대상이 아닙니다.<br>
        <span style="color:var(--text-sub)">
          사이드바의 <b>📑 리서치 → ⭐ AI 추천 → 🇺🇸 미국</b> 탭에서
          Finnhub 애널리스트 추천 트렌드를 확인하실 수 있습니다.
        </span>
      </div>`;
    return;
  }
  container.innerHTML = '<div class="chart-loading"><div class="spin"></div>리포트 로딩 중… (PDF 파싱 시간이 걸릴 수 있습니다)</div>';
  try {
    const r = await fetch(`/api/reports/${code}`);
    const d = await r.json();
    if (CHART_STATE.tab !== 'reports' || CHART_STATE.code !== code) return;
    if (!r.ok || d.error) {
      container.innerHTML = `<div class="chart-empty">
        <div class="chart-empty-title">리포트를 불러올 수 없음</div>
        ${d.error || ('HTTP ' + r.status)}
      </div>`;
      return;
    }
    if (!d.items || !d.items.length) {
      container.innerHTML = '<div class="chart-empty">관련 증권사 리포트가 없습니다.</div>';
      return;
    }
    const opinionColor = (op) => ({
      '매수': '#FF3333', 'Trading Buy': '#FF7F50',
      '중립': '#FFD700', '매도': '#33AA33',
    })[op] || 'var(--text-sub)';

    const rows = d.items.map(item => {
      const target = item.target_price != null
        ? `₩${item.target_price.toLocaleString()}`
        : '—';
      const upStr = item.upside != null
        ? `${item.upside >= 0 ? '+' : ''}${item.upside}%`
        : '—';
      const upCol = item.upside != null && item.upside < 0 ? '#33AA33' : '#FF3333';
      const estimates = [
        item.revenue_estimate ? `<span class="estimate-tag">매출 ${item.revenue_estimate}</span>` : '',
        item.op_estimate      ? `<span class="estimate-tag">영업이익 ${item.op_estimate}</span>`   : '',
        item.eps_estimate     ? `<span class="estimate-tag">EPS ${item.eps_estimate}</span>`       : '',
      ].filter(Boolean).join('');
      const keyPts = (item.key_points || []).map(p => `<div class="keypoint">• ${_escHtml(p)}</div>`).join('');

      // Phase 13: tabula 가 추출한 재무 테이블 (financial_tables: [{headers, rows}])
      const finTables = (item.financial_tables || []).map((t, ti) => {
        const headHTML = t.headers.map(h => `<th class="r">${_escHtml(h || '')}</th>`).join('');
        const bodyHTML = t.rows.map(r => {
          const vals = r.values.map(v => `<td class="r">${_escHtml(v || '')}</td>`).join('');
          return `<tr><th class="lbl">${_escHtml(r.label)}</th>${vals}</tr>`;
        }).join('');
        return `<div class="report-fin-table-wrap">
          <div class="report-fin-table-title">재무 추정 테이블 #${ti + 1} · ${t.rows.length}개 항목 · ${t.headers.length}개 기간</div>
          <div class="report-fin-table-scroll">
            <table class="report-fin-table">
              <thead><tr><th></th>${headHTML}</tr></thead>
              <tbody>${bodyHTML}</tbody>
            </table>
          </div>
        </div>`;
      }).join('');

      return `
        <div class="report-item">
          <div class="report-top">
            <div class="report-title-row">
              <span class="report-broker">${_escHtml(item.broker || '—')}</span>
              <span class="report-date">${_escHtml(item.date || '')}</span>
            </div>
            <div class="report-title">${_escHtml(item.title)}</div>
          </div>
          <div class="report-metrics">
            <div class="report-metric">
              <span class="metric-label">투자의견</span>
              <span class="metric-value" style="color:${opinionColor(item.opinion)}">${_escHtml(item.opinion || '—')}</span>
            </div>
            <div class="report-metric">
              <span class="metric-label">목표주가</span>
              <span class="metric-value">${target}</span>
            </div>
            <div class="report-metric">
              <span class="metric-label">상승여력</span>
              <span class="metric-value" style="color:${upCol}">${upStr}</span>
            </div>
          </div>
          ${keyPts ? `<div class="report-keypoints"><div class="keypoints-label">핵심 포인트</div>${keyPts}</div>` : ''}
          ${estimates ? `<div class="report-estimates">${estimates}</div>` : ''}
          ${finTables}
          ${item.pdf_url ? `<a class="report-pdf-link" href="${item.pdf_url}" target="_blank" rel="noopener">📄 PDF 원문 보기</a>` : ''}
        </div>`;
    }).join('');
    container.innerHTML = `
      <div class="report-list">
        <div class="report-header">${d.name} 증권사 리포트 (최근 ${d.count}건)</div>
        ${rows}
      </div>`;
  } catch (err) {
    container.innerHTML = `<div class="chart-empty">리포트 로드 실패: ${err.message}</div>`;
  }
}

async function _loadFinancial(code, container) {
  const isUS = CHART_STATE.market === 'us';
  container.innerHTML = '<div class="chart-loading"><div class="spin"></div>재무 데이터 로딩 중…</div>';
  try {
    const base = isUS ? '/api/us/financial' : '/api/financial';
    const r = await fetch(`${base}/${code}`);
    const d = await r.json();
    if (CHART_STATE.tab !== 'financial' || CHART_STATE.code !== code) return;
    if (!r.ok || d.error) {
      container.innerHTML = `<div class="chart-empty">
        <div class="chart-empty-title">재무 데이터 불러오기 실패</div>${d.error || ('HTTP ' + r.status)}
      </div>`;
      return;
    }

    const fmt = (v, suf) => v == null ? '—' : (Math.round(v * 100) / 100).toLocaleString() + (suf || '');
    const fmtBign = (v) => {
      if (v == null) return '—';
      if (Math.abs(v) >= 10000) return (v / 10000).toFixed(1) + '조';
      return v.toLocaleString() + '억';
    };

    // Phase 14: 시장별 다른 카드 구성
    let cards;
    if (isUS) {
      cards = [
        { label: 'PER',         value: fmt(d.per, 'x'),         desc: d.forward_per != null ? `Forward ${d.forward_per.toFixed(1)}x` : 'Trailing P/E' },
        { label: 'PBR',         value: fmt(d.pbr, 'x'),         desc: 'Price / Book' },
        { label: 'ROE',         value: fmt(d.roe, '%'),         desc: d.profit_margin != null ? `Margin ${d.profit_margin}%` : '' },
        { label: 'Market Cap',  value: d.market_cap_str || '—', desc: d.sector || '' },
        { label: '52W Range',   value: (d.w52_low != null && d.w52_high != null) ? `$${d.w52_low}–$${d.w52_high}` : '—', desc: d.beta != null ? `β ${d.beta.toFixed(2)}` : '' },
        { label: 'Analyst Tgt', value: d.target_price != null ? `$${Number(d.target_price).toFixed(2)}` : '—', desc: (d.recommendation || '').toUpperCase() },
      ];
    } else {
      cards = [
        { label: 'PER',         value: fmt(d.per, '배'),         desc: d.eps != null ? `EPS ${d.eps.toLocaleString()}원` : '주가수익비율' },
        { label: '추정 PER',    value: fmt(d.estimate_per, '배'), desc: d.estimate_eps != null ? `추정 EPS ${d.estimate_eps.toLocaleString()}원` : '컨센서스 추정' },
        { label: 'PBR',         value: fmt(d.pbr, '배'),         desc: d.bps != null ? `BPS ${d.bps.toLocaleString()}원` : '주가순자산비율' },
        { label: '동일업종 PER', value: fmt(d.industry_per, '배'), desc: '업종 평균 비교' },
        { label: '시가총액',    value: d.market_cap || '—',      desc: d.market_cap_rank || '' },
        { label: '배당수익률',  value: fmt(d.dividend_yield, '%'), desc: '연 환산 시가배당률' },
      ];
    }

    const cardHTML = cards.map(c => `
      <div class="financial-card">
        <div class="fin-label">${_escHtml(c.label)}</div>
        <div class="fin-value">${_escHtml(c.value)}</div>
        ${c.desc ? `<div class="fin-desc">${_escHtml(c.desc)}</div>` : ''}
      </div>`).join('');

    let annualHTML = '';
    if (!isUS && d.annual && d.annual.length) {
      const headers = d.annual.map(a => `<th class="r">${_escHtml(a.period)}</th>`).join('');
      const rev    = d.annual.map(a => `<td class="r">${fmtBign(a.revenue)}</td>`).join('');
      const op     = d.annual.map(a => `<td class="r">${fmtBign(a.op_profit)}</td>`).join('');
      const np     = d.annual.map(a => `<td class="r">${fmtBign(a.net_profit)}</td>`).join('');
      const opM    = d.annual.map(a => `<td class="r">${a.op_margin == null ? '—' : a.op_margin.toFixed(1) + '%'}</td>`).join('');
      annualHTML = `
        <div class="financial-table-title">기업실적 분석 (단위: 억원, IFRS 연결)</div>
        <table class="pg-table financial-table">
          <thead><tr><th></th>${headers}</tr></thead>
          <tbody>
            <tr><th>매출액</th>${rev}</tr>
            <tr><th>영업이익</th>${op}</tr>
            <tr><th>당기순이익</th>${np}</tr>
            <tr><th>영업이익률</th>${opM}</tr>
          </tbody>
        </table>`;
    }

    const sourceText = isUS
      ? 'Source: Yahoo Finance (24h cache)'
      : '출처: 네이버 금융 (24시간 캐시)';
    container.innerHTML = `
      <div class="financial-wrap">
        <div class="financial-grid">${cardHTML}</div>
        ${annualHTML}
        <div class="financial-source">${sourceText}</div>
        ${!isUS ? '<div id="dart-financial-section"><div class="dart-loading">📊 DART 12분기 손익 분석 로딩 중…</div></div>' : ''}
      </div>`;
    // KR 전용: DART 12분기 섹션 비동기 로딩
    if (!isUS) {
      _loadDartFinancial(code);
    }
  } catch (err) {
    container.innerHTML = `<div class="chart-empty">로드 실패: ${_escHtml(err.message)}</div>`;
  }
}

// ─────────────────────────────────────────────────────────────────────────
// DART 12분기 손익 (Phase 20)
// ─────────────────────────────────────────────────────────────────────────
function _dartFmt(val) {
  if (val == null) return '—';
  const abs = Math.abs(val);
  const sign = val < 0 ? '-' : '';
  if (abs >= 1e12) return sign + (abs / 1e12).toFixed(1) + '조';
  if (abs >= 1e8)  return sign + Math.round(abs / 1e8).toLocaleString() + '억';
  return sign + abs.toLocaleString();
}

async function _loadDartFinancial(code) {
  const section = document.getElementById('dart-financial-section');
  if (!section) return;
  try {
    const r = await fetch(`/api/dart_financial/${code}`);
    if (CHART_STATE.tab !== 'financial' || CHART_STATE.code !== code) return;
    if (r.status === 404) {
      section.innerHTML = '<div class="dart-note">DART 매핑 없음 (비상장/신규 상장 종목)</div>';
      return;
    }
    const d = await r.json();
    if (CHART_STATE.tab !== 'financial' || CHART_STATE.code !== code) return;
    if (d.error) {
      section.innerHTML = `<div class="dart-note">${_escHtml(d.error)}</div>`;
      return;
    }
    const qs = d.quarters || [];
    if (!qs.length) {
      section.innerHTML = '<div class="dart-note">DART 분기 데이터 없음</div>';
      return;
    }

    // ── 12분기 손익 테이블 ──
    const periodCols = qs.map(q => {
      const isLatest = (q === qs[qs.length - 1]);
      return `<th class="${isLatest ? 'dart-th-latest' : ''}">${q.period}</th>`;
    }).join('');

    const row = (label, key, opts = {}) => {
      const cells = qs.map(q => {
        const v = q.summary?.[key];
        const color = opts.color || (v != null && v < 0 ? '#33AA33' : '');
        return `<td style="${color ? 'color:' + color : ''}">${_dartFmt(v)}</td>`;
      }).join('');
      return `<tr class="${opts.bold ? 'dart-row-bold' : ''}${opts.indent ? ' dart-row-indent' : ''}">
        <td class="dart-td-label">${label}</td>${cells}</tr>`;
    };
    const marginRow = (label, key, color) => {
      const cells = qs.map(q => {
        const v = q.margins?.[key];
        return `<td style="color:${color}">${v != null ? v.toFixed(1) + '%' : '—'}</td>`;
      }).join('');
      return `<tr class="dart-row-margin"><td class="dart-td-label">${label}</td>${cells}</tr>`;
    };

    const tableHTML = `
      <div class="dart-table-scroll">
        <table class="dart-table">
          <thead><tr><th class="dart-th-label">항목</th>${periodCols}</tr></thead>
          <tbody>
            ${row('매출액', 'revenue', { bold: true })}
            ${row('(매출원가)', 'cogs', { indent: true })}
            ${row('매출총이익', 'gross_profit', { bold: true, color: '#FFD700' })}
            ${row('(판관비)', 'sga', { indent: true })}
            ${row('영업이익', 'op_income', { bold: true, color: '#FF6B6B' })}
            ${row('순이익', 'net_income', { bold: true })}
            ${marginRow('GPM', 'gpm', '#FFD700')}
            ${marginRow('OPM', 'opm', '#FF6B6B')}
            ${marginRow('NPM', 'npm', '#6495ED')}
          </tbody>
        </table>
      </div>`;

    // ── 비용 구조 (최신 분기 기준) ──
    const latest = qs[qs.length - 1];
    const m = latest.margins || {};
    const s = latest.summary || {};
    const cogs = s.cogs || 0;
    const sga  = s.sga  || 0;
    const gpm  = m.gpm  || 0;
    const bep  = m.bep_revenue;

    const costHTML = `
      <div class="dart-cost-wrap">
        <div class="dart-cost-title">
          비용 구조 근사 (${latest.period})
          <span class="dart-tier-tag">Tier 1 (GPM 근사)</span>
        </div>
        <div class="dart-cost-grid">
          <div class="dart-cost-card">
            <div class="dart-cost-label">매출원가 (≈변동비)</div>
            <div class="dart-cost-val">${_dartFmt(cogs)}</div>
          </div>
          <div class="dart-cost-card">
            <div class="dart-cost-label">판관비 (≈고정비)</div>
            <div class="dart-cost-val">${_dartFmt(sga)}</div>
          </div>
          <div class="dart-cost-card highlight">
            <div class="dart-cost-label">공헌이익률 ≈</div>
            <div class="dart-cost-val">${gpm ? gpm.toFixed(1) + '%' : '—'}</div>
          </div>
          <div class="dart-cost-card">
            <div class="dart-cost-label">손익분기 매출 ≈</div>
            <div class="dart-cost-val">${bep != null ? _dartFmt(bep) : '—'}</div>
          </div>
        </div>

        <div class="dart-manual">
          <div class="dart-manual-note">
            ⚠ DART API 는 집계 항목만 제공하여 세부 비용 분류가 불가합니다.
            매출원가≈변동비, 판관비≈고정비로 근사 계산했습니다.
            정확한 계산을 위해 업종 특성에 맞는 변동비율을 직접 입력하세요:
          </div>
          <div class="dart-manual-input">
            <label>변동비율 (%)</label>
            <input type="number" id="dart-var-ratio" min="0" max="100" step="1"
                   value="${gpm ? (100 - gpm).toFixed(0) : 70}"
                   data-revenue="${s.revenue || 0}" data-sga="${sga}">
            <span class="dart-manual-sep">→</span>
            <span class="dart-manual-result" id="dart-manual-result"></span>
          </div>
        </div>
      </div>`;

    // ── 사업부별 매출 (있는 경우만) ──
    let segmentHTML = '';
    if (d.segment_revenue && d.segment_revenue.length) {
      const rows = d.segment_revenue.map(seg => {
        const cells = seg.amounts.slice(0, 4).map(a =>
          `<td>${typeof a === 'number' ? _dartFmt(a * 1e6) : _escHtml(String(a).slice(0, 30))}</td>`
        ).join('');
        return `<tr>
          <td class="dart-td-label">${_escHtml(seg.segment)}</td>
          ${cells}
        </tr>`;
      }).join('');
      segmentHTML = `
        <div class="dart-segment-wrap">
          <div class="dart-segment-title">사업부/제품별 매출 (사업보고서 원문 파싱)</div>
          <table class="dart-table dart-segment-table"><tbody>${rows}</tbody></table>
          <div class="dart-segment-note">⚠ 원문 HTML 파싱 기반이라 단위/포맷이 회사별로 다를 수 있습니다.</div>
        </div>`;
    }

    section.innerHTML = `
      <div class="dart-section-title">📊 DART 12분기 손익 분석
        <span class="dart-section-sub">(fs_div: ${latest.fs_div || '?'} · ${qs.length}분기)</span>
      </div>
      ${tableHTML}
      ${costHTML}
      ${segmentHTML}`;

    // 수동 변동비율 입력 재계산
    const input = document.getElementById('dart-var-ratio');
    const result = document.getElementById('dart-manual-result');
    const recalc = () => {
      const varRatio = parseFloat(input.value) || 0;
      const cmRatio = 100 - varRatio;
      const revenue = parseFloat(input.dataset.revenue) || 0;
      const sgaVal = parseFloat(input.dataset.sga) || 0;
      const bepRev = cmRatio > 0 ? sgaVal / (cmRatio / 100) : null;
      result.innerHTML = `공헌이익률 <b>${cmRatio.toFixed(1)}%</b> · 손익분기 매출 <b>${bepRev != null ? _dartFmt(bepRev) : '—'}</b>`;
    };
    if (input) { input.addEventListener('input', recalc); recalc(); }
  } catch (err) {
    section.innerHTML = `<div class="dart-note">DART 로드 실패: ${_escHtml(err.message)}</div>`;
  }
}

function _openChartModal() {
  const overlay = document.getElementById('chart-panel-overlay');
  if (!overlay) return;
  overlay.style.display = 'flex';
  document.body.classList.add('chart-modal-open');
  // backdrop 클릭 시 닫기 (최초 한 번만 바인딩)
  if (!overlay.dataset.bound) {
    overlay.dataset.bound = '1';
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) closeChartPanel();
    });
  }
  // Esc 닫기 (한 번만)
  if (!window._chartEscBound) {
    window._chartEscBound = true;
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        const ov = document.getElementById('chart-panel-overlay');
        if (ov && ov.style.display !== 'none') closeChartPanel();
      }
    });
  }
}

function closeChartPanel() {
  // 진행 중 fetch 취소 + 토큰/상태 리셋 → 뒤늦게 돌아오는 응답이 패널에 주입되지 않도록.
  if (_chartAbortCtrl) {
    try { _chartAbortCtrl.abort(); } catch {}
    _chartAbortCtrl = null;
  }
  _stopChartPricePolling();
  _stopIntradayRefresh();
  _stopOrderbookPolling();
  if (CHART_STATE.code) _wsUnsubscribeChart(CHART_STATE.code);
  _chartRequestCode      = null;
  CHART_STATE.code       = null;
  CHART_STATE.market     = 'kr';
  CHART_STATE.data       = null;
  CHART_STATE.disclosureEvents = null;
  CHART_STATE.disclosureCode   = null;
  CHART_STATE.tab        = 'analysis';
  CHART_STATE.chartType  = 'day';
  CHART_STATE.intradayDays = 1;
  const overlay = document.getElementById('chart-panel-overlay');
  if (overlay) overlay.style.display = 'none';
  const panel = document.getElementById('chart-panel');
  if (panel) {
    panel.innerHTML = '';
    delete panel.dataset.code;
  }
  document.body.classList.remove('chart-modal-open');
  console.log('[closeChartPanel] 🗙 reset');
}

// ─────────────────────────────────────────────────────────────────────────────
// CROSSHAIR + OHLCV TOOLTIP (Phase 11)
// ─────────────────────────────────────────────────────────────────────────────
function _fmtVolShort(v) {
  if (v >= 1e8) return (v / 1e8).toFixed(1) + '억';
  if (v >= 1e4) return (v / 1e4).toFixed(0) + '만';
  return v.toLocaleString();
}

function _setupCrosshair(data, W, HC, HV) {
  const candleOv = document.getElementById('cv-candle-overlay');
  const volOv    = document.getElementById('cv-volume-overlay');
  if (!candleOv || !volOv) return;

  const sc = _chartScale(data, W, HC);
  const { PAD, cw, ch, len, toX, toY, fromX } = sc;

  const ohlcvBar  = document.getElementById('ohlcv-bar');
  const priceLbl  = document.getElementById('crosshair-price');
  const dateLbl   = document.getElementById('crosshair-date');

  const { dates, open: O, high: Hi, low: Lo, close: C, volume: V } = data;

  const cCtx = candleOv.getContext('2d');
  const vCtx = volOv.getContext('2d');
  const volW = volOv.width;
  const volH = volOv.height;
  // 거래량 차트의 좌우 여백 (drawVolume 와 동일)
  const volPAD = { top: 6, right: 120, bottom: 16, left: 68 };

  function clear() {
    cCtx.clearRect(0, 0, W, HC);
    vCtx.clearRect(0, 0, volW, volH);
    ohlcvBar.style.display = 'none';
    priceLbl.style.display = 'none';
    dateLbl.style.display  = 'none';
  }

  function drawAt(idx, mouseY) {
    if (idx < 0 || idx >= len) return;
    const x = toX(idx);

    // ── 캔들 오버레이 ──
    cCtx.clearRect(0, 0, W, HC);
    cCtx.strokeStyle = 'rgba(255,255,255,0.35)';
    cCtx.lineWidth   = 1;
    cCtx.setLineDash([4, 4]);
    // 수직선
    cCtx.beginPath(); cCtx.moveTo(x, PAD.top); cCtx.lineTo(x, HC - PAD.bottom); cCtx.stroke();
    // 수평선 (mouseY 가 범위 내일 때만)
    if (mouseY != null && mouseY >= PAD.top && mouseY <= HC - PAD.bottom) {
      cCtx.beginPath();
      cCtx.moveTo(PAD.left, mouseY);
      cCtx.lineTo(W - PAD.right, mouseY);
      cCtx.stroke();
    }
    cCtx.setLineDash([]);
    // 캔들 하이라이트 박스
    const bt = toY(Math.max(O[idx], C[idx]));
    const bb2 = toY(Math.min(O[idx], C[idx]));
    cCtx.strokeStyle = 'rgba(255,255,255,0.7)';
    cCtx.lineWidth = 1;
    cCtx.strokeRect(x - sc.bw / 2 - 1, bt - 1, sc.bw + 2, Math.max(1, bb2 - bt + 2));

    // ── 거래량 오버레이 (수직선 동기화) ──
    vCtx.clearRect(0, 0, volW, volH);
    vCtx.strokeStyle = 'rgba(255,255,255,0.35)';
    vCtx.lineWidth = 1;
    vCtx.setLineDash([4, 4]);
    vCtx.beginPath();
    vCtx.moveTo(x, volPAD.top);
    vCtx.lineTo(x, volH - volPAD.bottom);
    vCtx.stroke();
    vCtx.setLineDash([]);

    // ── OHLCV 바 ──
    const isUp = C[idx] >= O[idx];
    const col  = isUp ? '#FF3333' : '#33AA33';
    const chg  = idx > 0 ? (C[idx] - C[idx - 1]) : 0;
    const chgPct = idx > 0 && C[idx - 1] ? (chg / C[idx - 1] * 100) : 0;
    const chgSign = chg > 0 ? '+' : '';
    const chgCol  = chg > 0 ? '#FF3333' : chg < 0 ? '#33AA33' : 'var(--text-muted)';

    document.getElementById('ohlcv-date').textContent  = dates[idx];
    document.getElementById('ohlcv-open').textContent  = O[idx].toLocaleString();
    document.getElementById('ohlcv-open').style.color  = col;
    document.getElementById('ohlcv-high').textContent  = Hi[idx].toLocaleString();
    document.getElementById('ohlcv-high').style.color  = col;
    document.getElementById('ohlcv-low').textContent   = Lo[idx].toLocaleString();
    document.getElementById('ohlcv-low').style.color   = col;
    document.getElementById('ohlcv-close').textContent = C[idx].toLocaleString();
    document.getElementById('ohlcv-close').style.color = col;
    document.getElementById('ohlcv-chg').innerHTML     =
      `<span style="color:${chgCol}">${chgSign}${chg.toLocaleString()} (${chgSign}${chgPct.toFixed(2)}%)</span>`;
    document.getElementById('ohlcv-vol').textContent   = _fmtVolShort(V[idx]);

    // RSI/MACD 값 표시 (Phase 26)
    const rm = data.rsi_macd;
    if (rm) {
      const rsiVal = (rm.rsi || [])[idx];
      const macdVal = (rm.macd || [])[idx];
      const rsiStr = rsiVal != null ? ` | RSI ${rsiVal.toFixed(0)}` : '';
      const macdStr = macdVal != null ? ` | MACD ${macdVal.toFixed(0)}` : '';
      document.getElementById('ohlcv-vol').textContent += rsiStr + macdStr;
    }

    // RSI/MACD 서브차트에 세로선만 그리기 (전체 재그리기 ❌ → 성능 최적화)
    // 이전 세로선을 지우기 위해 저장/복원 방식 사용
    const rsiCv = document.getElementById('cv-rsi');
    const macdCv = document.getElementById('cv-macd');
    for (const cv of [rsiCv, macdCv]) {
      if (!cv) continue;
      const c = cv.getContext('2d');
      // 저장된 이미지 복원 (첫 호출 시 현재 상태 저장)
      if (!cv._savedImg) cv._savedImg = c.getImageData(0, 0, cv.width, cv.height);
      c.putImageData(cv._savedImg, 0, 0);
      // 세로선만 그리기
      c.strokeStyle = 'rgba(255,255,255,0.35)';
      c.lineWidth = 1; c.setLineDash([4, 4]);
      c.beginPath(); c.moveTo(x, 6); c.lineTo(x, cv.height - 16); c.stroke();
      c.setLineDash([]);
    }

    ohlcvBar.style.display = 'flex';

    // ── 축 라벨 ──
    if (mouseY != null && mouseY >= PAD.top && mouseY <= HC - PAD.bottom) {
      const price = sc.fromY(mouseY);
      priceLbl.style.display = 'block';
      priceLbl.style.top     = (mouseY / HC * 100) + '%';
      priceLbl.textContent   = '₩' + Math.round(price).toLocaleString();
    } else {
      priceLbl.style.display = 'none';
    }
    dateLbl.style.display = 'block';
    dateLbl.style.left    = (x / volW * 100) + '%';
    dateLbl.textContent   = dates[idx];
  }

  function onMove(e, ownerCanvas, ownerH) {
    const rect = ownerCanvas.getBoundingClientRect();
    const scaleX = ownerCanvas.width  / rect.width;
    const mx = (e.clientX - rect.left) * scaleX;
    if (mx < PAD.left || mx > W - PAD.right) { clear(); return; }
    const idx = fromX(mx);
    // 캔들 오버레이 위일 때만 수평선(y 추적), 거래량 오버레이일 때는 수직선만
    let mouseY = null;
    if (ownerCanvas === candleOv) {
      const scaleY = ownerCanvas.height / rect.height;
      mouseY = (e.clientY - rect.top) * scaleY;
      if (mouseY < PAD.top || mouseY > HC - PAD.bottom) { mouseY = null; }
    }
    drawAt(idx, mouseY);
  }

  candleOv.addEventListener('mousemove', e => onMove(e, candleOv, HC));
  volOv.addEventListener('mousemove',    e => onMove(e, volOv,    volH));
  candleOv.addEventListener('mouseleave', clear);
  volOv.addEventListener('mouseleave',    clear);

  // RSI/MACD 캔버스에서도 마우스 이벤트 → 크로스헤어 동기화
  const rsiCv = document.getElementById('cv-rsi');
  const macdCv = document.getElementById('cv-macd');
  if (rsiCv) {
    rsiCv.addEventListener('mousemove', e => onMove(e, rsiCv, rsiCv.height));
    rsiCv.addEventListener('mouseleave', clear);
  }
  if (macdCv) {
    macdCv.addEventListener('mousemove', e => onMove(e, macdCv, macdCv.height));
    macdCv.addEventListener('mouseleave', clear);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// LIVE PRICE POLLING (Phase 11)  —  /api/price/<code> 5분 주기
// ─────────────────────────────────────────────────────────────────────────────
let _chartPriceTimer = null;
let _chartPriceCode  = null;

function _startChartPricePolling(code) {
  _stopChartPricePolling();
  _chartPriceCode = code;
  // 즉시 한 번 호출 (openChartPanel 진입 직후라도 최신 가격 시도)
  _fetchAndApplyLivePrice(code);
  _chartPriceTimer = setInterval(() => {
    if (_chartPriceCode !== code) { _stopChartPricePolling(); return; }
    if (document.hidden) return;   // 탭 비활성 시 스킵
    _fetchAndApplyLivePrice(code);
  }, 5 * 60 * 1000);
}

function _stopChartPricePolling() {
  if (_chartPriceTimer) {
    clearInterval(_chartPriceTimer);
    _chartPriceTimer = null;
  }
  _chartPriceCode = null;
}

async function _fetchAndApplyLivePrice(code) {
  try {
    const base = CHART_STATE.market === 'us' ? '/api/us/price' : '/api/price';
    const r = await fetch(`${base}/${code}`, { cache: 'no-store' });
    if (!r.ok) return;
    const p = await r.json();
    if (p.error || p.price == null) return;
    if (CHART_STATE.code !== code) return;   // 패널이 다른 종목으로 전환됨
    const el = document.getElementById('chart-live-price');
    if (!el) return;
    const valEl  = el.querySelector('.live-price-value');
    const chgEl  = el.querySelector('.live-price-change');
    const timeEl = document.getElementById('chart-price-time');
    if (!valEl || !chgEl) return;

    const prev = parseInt((valEl.textContent || '').replace(/[^\d]/g, ''), 10) || 0;
    valEl.textContent = _fmtPrice(p.price, CHART_STATE.market);

    const chg = p.change != null ? p.change : 0;
    const pct = p.change_pct != null ? p.change_pct : 0;
    const col = chg > 0 ? '#FF3333' : chg < 0 ? '#33AA33' : 'var(--text-muted)';
    const sign = chg > 0 ? '+' : '';
    chgEl.style.color = col;
    chgEl.textContent = `${sign}${Number(chg).toLocaleString()} (${sign}${Number(pct).toFixed(2)}%)`;

    if (timeEl) {
      const now = new Date();
      timeEl.textContent = '기준: ' + now.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
    }

    // 가격 변경 시 플래시
    if (prev && Number(p.price) !== prev) {
      valEl.classList.remove('price-flash');
      void valEl.offsetWidth;
      valEl.classList.add('price-flash');
    }
    console.log('[live-price]', code, p.source, '→', p.price, `${sign}${pct}%`);
  } catch (err) {
    console.warn('[live-price] fetch failed', err);
  }
}

// 캔들 차트 스케일 계산 — drawCandles 와 crosshair 가 공유해 오차 방지.
function _chartScale(data, W, H) {
  const PAD = { top: 20, right: 120, bottom: 28, left: 68 };
  const cw  = W - PAD.left - PAD.right;
  const ch  = H - PAD.top  - PAD.bottom;
  const len = data.dates.length;
  const bb  = data.bollinger || { upper: [], lower: [], sma_20: [] };

  const allHi = [...data.high];
  const allLo = [...data.low];
  (bb.upper || []).forEach(v => v !== null && allHi.push(v));
  (bb.lower || []).forEach(v => v !== null && allLo.push(v));
  const maxP = Math.max(...allHi);
  const minP = Math.min(...allLo);
  const rng  = maxP - minP || 1;

  return {
    PAD, cw, ch, len, minP, maxP, rng,
    toX: i => PAD.left + (len > 1 ? (i / (len - 1)) * cw : cw / 2),
    toY: p => PAD.top  + (1 - (p - minP) / rng) * ch,
    fromX: x => {
      if (len <= 1) return 0;
      const r = Math.round((x - PAD.left) / cw * (len - 1));
      return Math.max(0, Math.min(len - 1, r));
    },
    fromY: y => minP + (1 - (y - PAD.top) / ch) * rng,
    bw:    Math.max(2, (cw / len) * 0.65),
  };
}

function _drawCandles(canvas, data, W, H) {
  const ctx = canvas.getContext('2d');
  const { dates, open: O, high: Hi, low: Lo, close: C, bollinger: bb, fibonacci: fib, trendlines: tl } = data;
  const sc = _chartScale(data, W, H);
  const { PAD, cw, ch, len, minP, maxP, rng, toX, toY, bw } = sc;

  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#1C1C1E';
  ctx.fillRect(0, 0, W, H);

  // ── Y-axis grid + labels ──
  ctx.strokeStyle = 'rgba(255,255,255,0.06)';
  ctx.lineWidth   = 1;
  ctx.fillStyle   = '#636366';
  ctx.font        = '10px Noto Sans KR, sans-serif';
  ctx.textAlign   = 'right';
  for (let i = 0; i <= 4; i++) {
    const p = minP + (rng * i / 4);
    const y = toY(p);
    ctx.beginPath(); ctx.moveTo(PAD.left, y); ctx.lineTo(W - PAD.right, y); ctx.stroke();
    ctx.fillText(Math.round(p).toLocaleString(), PAD.left - 6, y + 3);
  }

  // ── X-axis date labels (every ~20 bars) ──
  ctx.fillStyle = '#636366';
  ctx.textAlign = 'center';
  const step = Math.max(1, Math.floor(len / 6));
  for (let i = 0; i < len; i += step) {
    const lbl = dates[i].slice(5);   // MM-DD
    ctx.fillText(lbl, toX(i), H - PAD.bottom + 14);
  }

  // ── Fibonacci lines ──
  const fibColors = { '0.0': '#FF6B6B', '23.6': '#FFD700', '38.2': '#FFD700',
                      '50.0': '#FFD700', '61.8': '#FFD700', '78.6': '#FFD700', '100.0': '#FF6B6B' };
  ctx.setLineDash([4, 4]);
  ctx.font      = '10px Noto Sans KR, sans-serif';
  ctx.textAlign = 'left';
  Object.entries(fib).forEach(([label, price]) => {
    if (price < minP || price > maxP) return;
    const y = toY(price);
    const col = fibColors[label] || '#FFD700';
    ctx.strokeStyle = col + '99';
    ctx.lineWidth   = 1;
    ctx.beginPath(); ctx.moveTo(PAD.left, y); ctx.lineTo(W - PAD.right, y); ctx.stroke();
    ctx.fillStyle = col;
    ctx.fillText(`${label}%  ${price.toLocaleString()}`, W - PAD.right + 4, y + 3);
  });
  ctx.setLineDash([]);

  // ── Bollinger band fill ──
  ctx.fillStyle = 'rgba(100,149,237,0.08)';
  ctx.beginPath();
  let started = false;
  for (let i = 0; i < len; i++) {
    if (bb.upper[i] !== null) {
      const x = toX(i), y = toY(bb.upper[i]);
      started ? ctx.lineTo(x, y) : (ctx.moveTo(x, y), started = true);
    }
  }
  for (let i = len - 1; i >= 0; i--) {
    if (bb.lower[i] !== null) ctx.lineTo(toX(i), toY(bb.lower[i]));
  }
  ctx.closePath(); ctx.fill();

  // ── Bollinger lines ──
  [['upper', '#6495ED'], ['lower', '#6495ED'], ['sma_20', 'rgba(255,255,255,0.4)']].forEach(([key, color]) => {
    ctx.strokeStyle = color; ctx.lineWidth = 1;
    ctx.beginPath();
    let f = true;
    for (let i = 0; i < len; i++) {
      if (bb[key][i] !== null) {
        f ? (ctx.moveTo(toX(i), toY(bb[key][i])), f = false) : ctx.lineTo(toX(i), toY(bb[key][i]));
      }
    }
    ctx.stroke();
  });

  // ── Trendlines ──
  const drawTL = (tline, color) => {
    if (!tline) return;
    const x1 = toX(tline.i1), y1 = toY(tline.p1);
    const x2 = toX(len - 1);
    const p2e = tline.slope * (len - 1) + tline.intercept;
    const y2e = toY(p2e);
    ctx.strokeStyle = color; ctx.lineWidth = 1.5;
    ctx.setLineDash([6, 3]);
    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2e); ctx.stroke();
    ctx.setLineDash([]);
  };
  drawTL(tl.support,    '#33AA33');
  drawTL(tl.resistance, '#FF3333');

  // ── Candlesticks ──
  for (let i = 0; i < len; i++) {
    const x    = toX(i);
    const isUp = C[i] >= O[i];
    const col  = isUp ? '#FF0000' : '#008800';
    // wick
    ctx.strokeStyle = col; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, toY(Hi[i])); ctx.lineTo(x, toY(Lo[i])); ctx.stroke();
    // body
    const bt = toY(Math.max(O[i], C[i]));
    const bb2 = toY(Math.min(O[i], C[i]));
    ctx.fillStyle = col;
    ctx.fillRect(x - bw / 2, bt, bw, Math.max(bb2 - bt, 1));
  }

  // ── 공시 이벤트 마커 (차트 상단) ──
  _drawDisclosureMarkers(ctx, data, sc);
}

// ── 공시 이벤트 마커 (차트 상단에 삼각형/원) ──
function _drawDisclosureMarkers(ctx, data, sc) {
  const events = CHART_STATE.disclosureEvents;
  if (!events || !events.length) return;
  const dates = data.dates || [];
  if (!dates.length) return;

  // 날짜 → 인덱스 맵
  const dateIdx = {};
  for (let i = 0; i < dates.length; i++) {
    const norm = String(dates[i]).replace(/-/g, '').slice(0, 8);
    dateIdx[norm] = i;
  }

  const markerY = sc.PAD.top + 6;  // 차트 상단
  for (const ev of events) {
    const key = (ev.date || '').replace(/-/g, '').slice(0, 8);
    const idx = dateIdx[key];
    if (idx === undefined) continue;
    const x = sc.toX(idx);

    // 점수별 색상/모양
    let color, size, shape;
    if (ev.score >= 10)      { color = '#ef4444'; size = 8; shape = 'tri'; }
    else if (ev.score >= 8)  { color = '#f59e0b'; size = 7; shape = 'tri'; }
    else if (ev.score >= 6)  { color = '#f59e0b'; size = 5; shape = 'tri'; }
    else                     { color = 'rgba(136,136,150,0.6)'; size = 4; shape = 'dot'; }

    ctx.save();
    if (shape === 'tri') {
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.moveTo(x, markerY + size);
      ctx.lineTo(x - size * 0.7, markerY);
      ctx.lineTo(x + size * 0.7, markerY);
      ctx.closePath();
      ctx.fill();
    } else {
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(x, markerY + size / 2, size / 2, 0, Math.PI * 2);
      ctx.fill();
    }
    // 점수 8+ 이벤트는 세로 점선
    if (ev.score >= 8) {
      ctx.strokeStyle = color.length === 7 ? color + '22' : color.replace(/[\d.]+\)$/, '0.13)');
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 4]);
      ctx.beginPath();
      ctx.moveTo(x, sc.PAD.top + size + 6);
      ctx.lineTo(x, sc.PAD.top + sc.ch);
      ctx.stroke();
      ctx.setLineDash([]);
    }
    ctx.restore();
  }
}

// ── 공시 이벤트 fetch + 캐시 + 재렌더 ──
async function _loadDisclosureEvents(code) {
  if (!code || !/^\d{6}$/.test(code)) return [];
  if (CHART_STATE.disclosureCode === code && CHART_STATE.disclosureEvents) {
    return CHART_STATE.disclosureEvents;
  }
  try {
    const r = await fetch(`/api/disclosure_events/${code}`);
    const d = await r.json();
    CHART_STATE.disclosureEvents = d.events || [];
    CHART_STATE.disclosureCode = code;
    return CHART_STATE.disclosureEvents;
  } catch {
    CHART_STATE.disclosureEvents = [];
    return [];
  }
}

// ── RSI 서브차트 ──
function _drawRSI(canvas, data, W, H) {
  if (!canvas) { console.warn('[RSI] canvas not found'); return; }
  const rm = data.rsi_macd;
  if (!rm || !rm.rsi) { console.warn('[RSI] rsi_macd missing from data'); return; }
  console.log('[RSI] drawing', rm.rsi.length, 'points, last=', rm.rsi[rm.rsi.length-1]);
  const ctx = canvas.getContext('2d');
  const rsi = rm.rsi;
  const len = rsi.length;
  const PAD = { top: 6, right: 120, bottom: 16, left: 68 };
  const cw = W - PAD.left - PAD.right;
  const ch = H - PAD.top - PAD.bottom;
  const toX = i => PAD.left + (len > 1 ? (i / (len - 1)) * cw : cw / 2);
  const toY = v => PAD.top + (1 - v / 100) * ch;

  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#1C1C1E'; ctx.fillRect(0, 0, W, H);

  // 가이드라인: 70 (빨강), 50 (회색), 30 (녹색)
  for (const [level, color] of [[70, '#FF333366'], [50, '#44444488'], [30, '#33AA3366']]) {
    const y = toY(level);
    ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(PAD.left, y); ctx.lineTo(W - PAD.right, y); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = color; ctx.font = '9px sans-serif';
    ctx.fillText(String(level), PAD.left - 22, y + 3);
  }

  // 과매수(>70) / 과매도(<30) 영역 채움
  ctx.globalAlpha = 0.08;
  ctx.fillStyle = '#FF3333';
  ctx.fillRect(PAD.left, PAD.top, cw, toY(70) - PAD.top);
  ctx.fillStyle = '#33AA33';
  ctx.fillRect(PAD.left, toY(30), cw, toY(0) - toY(30));
  ctx.globalAlpha = 1;

  // RSI 라인 — 초기 0/NaN/null 스킵, 유효값부터 시작
  ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 1.5;
  ctx.beginPath();
  let started = false;
  for (let i = 0; i < len; i++) {
    const v = rsi[i];
    if (v == null || !isFinite(v) || isNaN(v) || v === 0) continue;
    const x = toX(i), y = toY(v);
    if (!isFinite(x) || !isFinite(y)) continue;
    if (!started) { ctx.moveTo(x, y); started = true; }
    else ctx.lineTo(x, y);
  }
  if (started) ctx.stroke();

  // 현재값 라벨 (유효값 탐색)
  let cur = null;
  for (let i = len - 1; i >= 0; i--) {
    const v = rsi[i];
    if (v != null && isFinite(v) && !isNaN(v) && v !== 0) { cur = v; break; }
  }
  if (cur == null) return;
  const rsiCol = cur > 70 ? '#FF3333' : cur > 50 ? '#FF9999' : cur > 30 ? '#99CC99' : '#33AA33';
  ctx.fillStyle = rsiCol; ctx.font = 'bold 11px sans-serif';
  ctx.fillText(`RSI ${cur.toFixed(1)}`, W - PAD.right + 8, PAD.top + 14);

  // Y축 라벨
  ctx.fillStyle = '#888'; ctx.font = '9px sans-serif';
  ctx.fillText('RSI(14)', PAD.left, PAD.top - 1);
}

// ── MACD 서브차트 ──
function _drawMACD(canvas, data, W, H) {
  if (!canvas) { console.warn('[MACD] canvas not found'); return; }
  const rm = data.rsi_macd;
  if (!rm || !rm.macd) { console.warn('[MACD] rsi_macd missing from data'); return; }
  console.log('[MACD] drawing', rm.macd.length, 'points');
  const ctx = canvas.getContext('2d');
  const { macd, macd_signal, macd_hist } = rm;
  const len = macd.length;
  const PAD = { top: 6, right: 120, bottom: 16, left: 68 };
  const cw = W - PAD.left - PAD.right;
  const ch = H - PAD.top - PAD.bottom;
  const toX = i => PAD.left + (len > 1 ? (i / (len - 1)) * cw : cw / 2);

  // Y범위 자동 — 초기 수렴 제외한 유효 구간에서 계산
  const skipN2 = Math.min(26, Math.floor(len * 0.15));
  const validSlice = (arr) => arr.slice(skipN2).filter(v => v !== 0);
  const allVals = [...validSlice(macd), ...validSlice(macd_signal), ...validSlice(macd_hist)];
  const yMax = allVals.length ? Math.max(...allVals.map(Math.abs), 1) : 1;
  const toY = v => PAD.top + ch / 2 - (v / yMax) * (ch / 2);

  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#1C1C1E'; ctx.fillRect(0, 0, W, H);

  // 0 중심선
  const y0 = toY(0);
  ctx.strokeStyle = '#44444488'; ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
  ctx.beginPath(); ctx.moveTo(PAD.left, y0); ctx.lineTo(W - PAD.right, y0); ctx.stroke();
  ctx.setLineDash([]);

  // 히스토그램 바 (NaN/null 스킵)
  const bw = Math.max(2, cw / len * 0.6);
  for (let i = 0; i < len; i++) {
    const h = macd_hist[i];
    if (h == null || !isFinite(h) || isNaN(h) || h === 0) continue;
    const yH = toY(h);
    if (!isFinite(yH)) continue;
    const x = toX(i) - bw / 2;
    const barH = Math.abs(yH - y0);
    const prev = i > 0 ? macd_hist[i - 1] : 0;
    if (h > 0) {
      ctx.fillStyle = h > (prev || 0) ? 'rgba(255,51,51,0.8)' : 'rgba(255,51,51,0.35)';
      ctx.fillRect(x, yH, bw, barH);
    } else {
      ctx.fillStyle = h < (prev || 0) ? 'rgba(51,170,51,0.8)' : 'rgba(51,170,51,0.35)';
      ctx.fillRect(x, y0, bw, barH);
    }
  }

  // MACD/Signal 라인 (NaN/null 스킵)
  const skipN = Math.min(26, Math.floor(len * 0.15));
  function _drawLine(arr, color) {
    ctx.strokeStyle = color; ctx.lineWidth = 1.5;
    ctx.beginPath();
    let s = false;
    for (let i = skipN; i < len; i++) {
      const v = arr[i];
      if (v == null || !isFinite(v) || isNaN(v)) continue;
      const x = toX(i), y = toY(v);
      if (!isFinite(x) || !isFinite(y)) continue;
      if (!s) { ctx.moveTo(x, y); s = true; }
      else ctx.lineTo(x, y);
    }
    if (s) ctx.stroke();
  }
  _drawLine(macd, '#4488FF');
  _drawLine(macd_signal, '#FF9500');

  // 현재값 라벨 (유효값만)
  const _safe = (v, d = 1) => (v != null && isFinite(v) && !isNaN(v)) ? v.toFixed(d) : '—';
  const curM = macd[len - 1], curS = macd_signal[len - 1], curH = macd_hist[len - 1];
  ctx.font = '10px sans-serif';
  ctx.fillStyle = '#4488FF'; ctx.fillText(`MACD ${_safe(curM)}`, W - PAD.right + 8, PAD.top + 12);
  ctx.fillStyle = '#FF9500'; ctx.fillText(`Sig ${_safe(curS)}`, W - PAD.right + 8, PAD.top + 24);
  ctx.fillStyle = (curH || 0) >= 0 ? '#FF3333' : '#33AA33';
  const histStr = (curH != null && isFinite(curH)) ? `${curH >= 0 ? '+' : ''}${curH.toFixed(1)}` : '—';
  ctx.fillText(`Hist ${histStr}`, W - PAD.right + 8, PAD.top + 36);

  // Y축 라벨
  ctx.fillStyle = '#888'; ctx.font = '9px sans-serif';
  ctx.fillText('MACD(12,26,9)', PAD.left, PAD.top - 1);
}

function _drawVolume(canvas, data, W, H, flowData) {
  const ctx = canvas.getContext('2d');
  const { open: O, close: C, volume: V, dates: D } = data;
  const len  = V.length;
  const PAD  = { top: 6, right: 120, bottom: 16, left: 68 };
  const cw   = W - PAD.left - PAD.right;
  const ch   = H - PAD.top  - PAD.bottom;
  const maxV = Math.max(...V) || 1;
  const bw   = Math.max(2, (cw / len) * 0.65);
  const flowOn = !!(flowData && flowData.dates && flowData.dates.length);

  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#1C1C1E';
  ctx.fillRect(0, 0, W, H);

  // grid line
  ctx.strokeStyle = 'rgba(255,255,255,0.06)'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(PAD.left, PAD.top); ctx.lineTo(W - PAD.right, PAD.top); ctx.stroke();

  // 볼륨 바 — flow 오버레이가 켜졌으면 투명도 낮춤
  for (let i = 0; i < len; i++) {
    const x   = PAD.left + (i / (len - 1)) * cw;
    const bh  = (V[i] / maxV) * ch;
    const upDown = C[i] >= O[i];
    const col = flowOn
      ? (upDown ? '#FF000033' : '#00880033')
      : (upDown ? '#FF000088' : '#00880088');
    ctx.fillStyle = col;
    ctx.fillRect(x - bw / 2, PAD.top + ch - bh, bw, bh);
  }

  // vol label
  ctx.fillStyle = '#636366'; ctx.font = '10px Noto Sans KR, sans-serif';
  ctx.textAlign = 'right';
  const maxLbl = maxV >= 1e6 ? (maxV / 1e6).toFixed(0) + 'M' : (maxV / 1e3).toFixed(0) + 'K';
  ctx.fillText(maxLbl, PAD.left - 6, PAD.top + 10);

  // ── Phase 12-3: 외국인/기관 수급 오버레이 ──
  if (flowOn) {
    // flowData: {dates, foreign_value[], inst_value[]}  (Naver 크롤, 20일)
    const flowMap = {};
    for (let k = 0; k < flowData.dates.length; k++) {
      flowMap[flowData.dates[k]] = {
        f: flowData.foreign_value ? flowData.foreign_value[k] : 0,
        i: flowData.inst_value    ? flowData.inst_value[k]    : 0,
      };
    }
    let maxAbs = 1;
    flowData.dates.forEach(d => {
      const r = flowMap[d] || {};
      maxAbs = Math.max(maxAbs, Math.abs(r.f || 0), Math.abs(r.i || 0));
    });
    const midY  = PAD.top + ch / 2;
    const halfH = ch / 2 - 1;
    const halfBw = Math.max(1, bw / 2);

    for (let i = 0; i < len; i++) {
      const date = D[i];
      const r = flowMap[date];
      if (!r) continue;
      const x = PAD.left + (i / (len - 1)) * cw;
      // 외국인 (좌측 절반) — 빨강(매수)/초록(매도)
      if (r.f) {
        const h = (Math.abs(r.f) / maxAbs) * halfH;
        ctx.fillStyle = r.f > 0 ? '#FF3333cc' : '#33AA33cc';
        ctx.fillRect(x - halfBw, r.f > 0 ? midY - h : midY, halfBw - 0.5, h);
      }
      // 기관 (우측 절반) — 주황(매수)/하늘(매도)
      if (r.i) {
        const h = (Math.abs(r.i) / maxAbs) * halfH;
        ctx.fillStyle = r.i > 0 ? '#FF9F0Acc' : '#5AC8FAcc';
        ctx.fillRect(x + 0.5, r.i > 0 ? midY - h : midY, halfBw - 0.5, h);
      }
    }
    // 0 기준선
    ctx.strokeStyle = 'rgba(255,255,255,0.25)'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(PAD.left, midY); ctx.lineTo(W - PAD.right, midY); ctx.stroke();

    // 범례
    ctx.fillStyle = '#FF3333'; ctx.fillRect(PAD.left + 30, PAD.top + 1, 8, 8);
    ctx.fillStyle = '#33AA33'; ctx.fillRect(PAD.left + 80, PAD.top + 1, 8, 8);
    ctx.fillStyle = '#FF9F0A'; ctx.fillRect(PAD.left + 130, PAD.top + 1, 8, 8);
    ctx.fillStyle = '#5AC8FA'; ctx.fillRect(PAD.left + 175, PAD.top + 1, 8, 8);
    ctx.fillStyle = '#AEAEB2'; ctx.font = '10px Noto Sans KR, sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText('외국인+', PAD.left + 41,  PAD.top + 9);
    ctx.fillText('외국인−', PAD.left + 91,  PAD.top + 9);
    ctx.fillText('기관+',   PAD.left + 141, PAD.top + 9);
    ctx.fillText('기관−',   PAD.left + 186, PAD.top + 9);
  }
}

function _renderAnalysis(container, analysis) {
  if (!analysis) { container.innerHTML = ''; return; }
  const typeInfo = {
    bollinger: { label: '볼린저', color: '#6495ED' },
    fibonacci: { label: '피보나치', color: '#FFD700' },
    trendline: { label: '추세선',  color: '#33AA33' },
    volume:    { label: '거래량',  color: '#FF6B6B' },
  };
  const items = analysis.comments.map(c => {
    const ti = typeInfo[c.type] || { label: c.type, color: '#AEAEB2' };
    return `<div class="analysis-item">
      <span class="analysis-tag" style="background:${ti.color}22;color:${ti.color};border:1px solid ${ti.color}55">${ti.label}</span>
      <span class="analysis-signal">${c.signal}</span>
      <p class="analysis-detail">${c.detail}</p>
    </div>`;
  }).join('');

  // 다이버전스 경고 (rsi_macd.divergences)
  let divHTML = '';
  const divs = (CHART_STATE.data?.rsi_macd?.divergences) || [];
  if (divs.length) {
    const bearish = divs.filter(d => d.type === 'bearish');
    const bullish = divs.filter(d => d.type === 'bullish');
    if (bearish.length >= 2) {
      divHTML += `<div class="div-warning bearish">⚠️ 다중 베어리시 다이버전스 (${bearish.map(d=>d.indicator).join('+')} — 추세 전환 가능)</div>`;
    } else if (bullish.length >= 2) {
      divHTML += `<div class="div-warning bullish">✨ 다중 불리시 다이버전스 (${bullish.map(d=>d.indicator).join('+')} — 반등 가능)</div>`;
    }
    divs.forEach(d => {
      const icon = d.type === 'bearish' ? '⚠️' : '✨';
      const col = d.type === 'bearish' ? '#FF6B6B' : '#33AA33';
      const label = d.type === 'bearish' ? '베어리시' : '불리시';
      divHTML += `<div class="div-item" style="border-left:3px solid ${col}">
        ${icon} <b>${d.indicator} ${label} 다이버전스</b>
        <span style="color:var(--text-muted);font-size:10px;margin-left:6px">
          가격 ${d.type === 'bearish' ? '신고가' : '신저가'} ↔ 지표 ${d.type === 'bearish' ? '저하' : '상승'}
        </span>
      </div>`;
    });
  }

  // ADX 지표
  let adxHTML = '';
  const adxData = CHART_STATE.data?.adx;
  if (adxData) {
    const adxVal = adxData.adx?.[adxData.adx.length - 1];
    const pdi = adxData.plus_di?.[adxData.plus_di.length - 1];
    const mdi = adxData.minus_di?.[adxData.minus_di.length - 1];
    if (adxVal) {
      const adxCol = adxVal > 40 ? '#FFD700' : adxVal > 25 ? '#FF6B6B' : '#888';
      const adxLabel = adxVal > 40 ? '강한 추세' : adxVal > 25 ? '추세 형성' : '횡보장';
      const diLabel = pdi > mdi ? '상승 우세 (+DI>-DI)' : '하락 우세 (-DI>+DI)';
      adxHTML = `<div class="analysis-item">
        <span class="analysis-tag" style="background:${adxCol}22;color:${adxCol};border:1px solid ${adxCol}55">ADX</span>
        <span class="analysis-signal">${adxLabel} (${adxVal.toFixed(1)})</span>
        <p class="analysis-detail">+DI ${pdi?.toFixed(1)} / -DI ${mdi?.toFixed(1)} · ${diLabel}</p>
      </div>`;
    }
  }

  container.innerHTML = `
    <div class="analysis-box">
      <div class="analysis-title">기술적 분석</div>
      ${divHTML}
      ${items}
      ${adxHTML}
      <div class="analysis-summary">${analysis.summary}</div>
      <div id="analysis-disclosure-section"></div>
      <div id="analysis-sentiment-section"></div>
      <div id="analysis-vp-section"></div>
    </div>`;

  // 공시 이력 섹션 (KR만 — 이미 로드된 CHART_STATE.disclosureEvents 사용 or fetch)
  const _code = CHART_STATE.code, _mkt = CHART_STATE.market || 'kr';
  if (_code && _mkt === 'kr') {
    const _renderDiscSec = (events) => {
      const box = document.getElementById('analysis-disclosure-section');
      if (!box) return;
      if (!events || !events.length) {
        box.innerHTML = '<div class="disc-sec-empty">📋 최근 6주 공시 없음</div>';
        return;
      }
      let h = '<div class="disc-sec-title">📋 최근 공시</div><div class="disc-sec-list">';
      for (const e of events.slice(0, 10)) {
        let col, emoji;
        if (e.score >= 10)      { col = '#ef4444'; emoji = '🚨'; }
        else if (e.score >= 8)  { col = '#ef4444'; emoji = '📢'; }
        else if (e.score >= 6)  { col = '#f59e0b'; emoji = '📋'; }
        else                    { col = 'var(--text-tertiary)'; emoji = '·'; }
        const title = (e.title || '').length > 42
          ? _escHtml((e.title || '').slice(0, 42)) + '…'
          : _escHtml(e.title || '');
        h += `<div class="disc-sec-row">
          <span class="disc-sec-date">${_escHtml(e.date || '')}</span>
          <span class="disc-sec-score" style="color:${col}">${emoji} ${e.score}점</span>
          <span class="disc-sec-title-text">${title}</span>
        </div>`;
      }
      h += '</div>';
      box.innerHTML = h;
    };
    if (CHART_STATE.disclosureEvents && CHART_STATE.disclosureCode === _code) {
      _renderDiscSec(CHART_STATE.disclosureEvents);
    } else {
      _loadDisclosureEvents(_code).then(ev => {
        if (CHART_STATE.code === _code) _renderDiscSec(ev);
      });
    }
  }

  // 센티먼트 비동기 로드 (KR만)
  if (_code && _mkt === 'kr') {
    fetch(`/api/sentiment/${_code}`).then(r => r.json()).then(sd => {
      if (CHART_STATE.code !== _code) return;
      const box = document.getElementById('analysis-sentiment-section');
      if (!box || !sd || sd.error) return;
      const labelCol = {'긍정': '#FF3333', '부정': '#33AA33', '중립': '#AEAEB2'}[sd.label] || '#888';
      const posKw = (sd.top_positive || []).slice(0, 5)
        .map(([k, c]) => `<span class="sent-kw pos">${_escHtml(k)} ×${c}</span>`).join('');
      const negKw = (sd.top_negative || []).slice(0, 5)
        .map(([k, c]) => `<span class="sent-kw neg">${_escHtml(k)} ×${c}</span>`).join('');
      box.innerHTML = `
        <div class="sent-section">
          <div class="sent-title">💬 소셜 센티먼트 (네이버 토론방)</div>
          <div class="sent-row">
            <span class="sent-label" style="background:${labelCol}22;color:${labelCol};border:1px solid ${labelCol}55">${sd.label}</span>
            <span class="sent-ratio">긍정 비율 ${Math.round((sd.ratio||0)*100)}%</span>
            <span class="sent-posts">· ${sd.posts}개 글 분석</span>
          </div>
          ${posKw ? `<div class="sent-kws"><small>🔴 긍정:</small> ${posKw}</div>` : ''}
          ${negKw ? `<div class="sent-kws"><small>🟢 부정:</small> ${negKw}</div>` : ''}
        </div>`;
    }).catch(() => {});
  }

  // 매물대 비동기 로드
  const code = CHART_STATE.code;
  const mkt = CHART_STATE.market || 'kr';
  if (code) {
    fetch(`/api/volume_profile/${code}?market=${mkt}`).then(r => r.json()).then(vp => {
      if (CHART_STATE.code !== code) return;
      const box = document.getElementById('analysis-vp-section');
      if (!box || !vp || vp.error) return;
      const cur = mkt === 'us' ? '$' : '₩';
      const profile = vp.volume_profile || {};
      const res = vp.resistance || [];
      const sup = vp.support || [];
      box.innerHTML = `
        <div class="vp-section">
          <div class="vp-title">🎯 매물대 분석</div>
          <div class="vp-grid">
            <div class="vp-item">
              <span class="vp-label">POC (최다 거래)</span>
              <b style="color:#FFD700">${cur}${(profile.poc||0).toLocaleString()}</b>
            </div>
            <div class="vp-item">
              <span class="vp-label">VWAP</span>
              <b style="color:#FF9500">${cur}${(vp.vwap_current||0).toLocaleString()}</b>
            </div>
            <div class="vp-item">
              <span class="vp-label">Value Area 하단</span>
              <b>${cur}${(profile.va_low||0).toLocaleString()}</b>
            </div>
            <div class="vp-item">
              <span class="vp-label">Value Area 상단</span>
              <b>${cur}${(profile.va_high||0).toLocaleString()}</b>
            </div>
          </div>
          ${res.length ? `<div class="vp-levels"><span class="vp-levels-title" style="color:#FF3333">저항</span>${res.map(r=>`<span class="vp-level">${cur}${r.price.toLocaleString()} (${r.touches}x)</span>`).join(' → ')}</div>` : ''}
          ${sup.length ? `<div class="vp-levels"><span class="vp-levels-title" style="color:#33AA33">지지</span>${sup.map(s=>`<span class="vp-level">${cur}${s.price.toLocaleString()} (${s.touches}x)</span>`).join(' → ')}</div>` : ''}
        </div>`;
    }).catch(() => {});
  }
}

