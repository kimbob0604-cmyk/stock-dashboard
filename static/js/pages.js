// ===== static/js/pages.js — 페이지 렌더러 전체 (Market/Screener/Flow/Portfolio/Alert/Sector/Agent/Backtest/Correlation/RecPerf/Disclosure/Discover/Calendar/Research/NewHighs/ETFMap/Dividend) =====
'use strict';

// ─────────────────────────────────────────────────────────────────────────────
// MARKET OVERVIEW (Phase 8-2)
// ─────────────────────────────────────────────────────────────────────────────
function renderMarketOverview() {
  const container = document.getElementById('market-view');
  if (!APP.data) { container.innerHTML = ''; return; }
  const d  = APP.data;
  const mo = d.market_overview || {};

  // 장마감 자동 시황 요약 (최상단)
  _loadMarketSummary(container);

  const cards = [
    { label: 'KOSPI',          value: d.kospi?.value,           pct: d.kospi?.change_pct,           src: 'kospi'  },
    { label: 'KOSDAQ',         value: d.kosdaq?.value,          pct: d.kosdaq?.change_pct,          src: 'kosdaq' },
    { label: 'S&P 500',        value: mo.sp500?.value,          pct: mo.sp500?.change_pct,          src: 'sp500'  },
    { label: 'NASDAQ',         value: mo.nasdaq?.value,         pct: mo.nasdaq?.change_pct,         src: 'nasdaq' },
    { label: 'USD/KRW',        value: mo.usd_krw?.value,        pct: mo.usd_krw?.change_pct,        src: 'usd'    , prefix: '₩' },
    { label: '나스닥100 선물', value: mo.nasdaq_futures?.value, pct: mo.nasdaq_futures?.change_pct, src: 'nqf'    },
  ];

  const cardsHTML = cards.map(c => {
    if (c.value == null && c.pct == null) {
      return `<div class="ov-card">
        <div class="ov-card-label">${c.label}</div>
        <div class="ov-card-value" style="color:var(--text-muted)">—</div>
        <div class="ov-missing">데이터 없음 (yfinance 미설치 또는 수집 실패)</div>
      </div>`;
    }
    const val  = c.value != null
      ? Number(c.value).toLocaleString('ko-KR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
      : '—';
    const pct  = c.pct != null ? Number(c.pct) : 0;
    const col  = pct > 0 ? '#FF3333' : pct < 0 ? '#33AA33' : 'var(--text-muted)';
    const sign = pct > 0 ? '+' : '';
    const pre  = c.prefix || '';
    return `<div class="ov-card">
      <div class="ov-card-label">${c.label}</div>
      <div class="ov-card-value">${pre}${val}</div>
      <div class="ov-card-pct" style="color:${col}">${sign}${pct.toFixed(2)}%</div>
    </div>`;
  }).join('');

  container.innerHTML = `
    <div class="ov-wrap">
      <div class="ov-title">시황 분석</div>
      <div class="ov-sub">주요 지수 · 환율</div>
      <div class="ov-grid">${cardsHTML}</div>
      <div id="ov-macro"><div class="ov-section-loading">🌐 글로벌 매크로 로딩 중…</div></div>
      <div id="ov-options"><div class="ov-section-loading">🔮 옵션 시그널 로딩 중…</div></div>
      <div id="ov-kr-futures"><div class="ov-section-loading">🇰🇷 국내 선물/옵션 로딩 중…</div></div>
      <div id="ov-rotation"><div class="ov-section-loading">🔄 섹터 로테이션 로딩 중…</div></div>
      <div id="ov-upcoming"><div class="ov-upcoming-loading">향후 주요 일정 로딩 중…</div></div>
    </div>`;
  _renderMacroCards();
  _renderOptionsSignal();
  _renderKRFuturesSignal();
  _renderSectorRotation();
  _renderUpcomingEvents();
}

async function _renderKRFuturesSignal() {
  const box = document.getElementById('ov-kr-futures');
  if (!box) return;
  try {
    const [nf, krOpt] = await Promise.all([
      fetch('/api/night_futures').then(r => r.json()),
      fetch('/api/kr_options').then(r => r.json()),
    ]);

    const nfClose = nf?.night_close;
    const nfPct = nf?.change_pct;
    const nfCol = nfPct == null ? 'var(--text-muted)'
                : nfPct >= 0 ? '#FF3333' : '#33AA33';
    const nfSign = nfPct == null ? '' : nfPct >= 0 ? '+' : '';
    const nfValueStr = nfClose != null ? nfClose.toLocaleString() : '—';
    const nfChgStr = (nfPct == null)
      ? '<span class="kr-fut-unavailable">데이터 없음</span>'
      : `<span style="color:${nfCol}">${nfSign}${nfPct.toFixed(2)}%</span>`;
    const nfSrcBadge = nf?.source
      ? `<span class="kr-fut-badge">${_escHtml(nf.source)}</span>`
      : '';

    // attempted 목록 (모든 소스 실패 시 상세 표시)
    const attemptedHTML = (nfClose == null && (nf?.attempted || []).length)
      ? `<div class="kr-fut-attempted">
          시도한 소스:
          ${(nf.attempted || []).map(a => {
            const ico = a.ok ? '<span style="color:#33AA33">✓</span>'
                             : '<span style="color:#FF3333">✗</span>';
            const err = a.error ? ` <span class="kr-fut-err">${_escHtml(a.error.slice(0,60))}</span>` : '';
            return `<div>${ico} ${_escHtml(a.source || '?')}${err}</div>`;
          }).join('')}
        </div>`
      : '';

    const krOptUnavail = !krOpt?.pcr_volume;

    box.innerHTML = `
      <div class="kr-fut-section">
        <div class="kr-fut-title">🇰🇷 국내 선물/옵션 시그널
          <span class="kr-fut-sub">익일 시장 방향 참고</span>
        </div>
        <div class="kr-fut-grid">
          <div class="kr-fut-card">
            <div class="kr-fut-card-label">코스피200 야간선물 ${nfSrcBadge}</div>
            <div class="kr-fut-card-value">${nfValueStr}</div>
            <div class="kr-fut-card-sub">${nfChgStr}</div>
            <div class="kr-fut-card-signal">${_escHtml(nf?.signal || '데이터 없음')}</div>
            ${attemptedHTML}
          </div>
          <div class="kr-fut-card ${krOptUnavail ? 'unavail' : ''}">
            <div class="kr-fut-card-label">코스피200 옵션 PCR</div>
            <div class="kr-fut-card-value">${krOpt?.pcr_volume ?? '—'}</div>
            <div class="kr-fut-card-sub">OI: ${krOpt?.pcr_oi ?? '—'}</div>
            <div class="kr-fut-card-signal">${_escHtml(krOpt?.signal || '데이터 없음')}</div>
          </div>
        </div>
      </div>`;
  } catch (err) {
    box.innerHTML = `<div class="ov-section-empty">국내 선물/옵션 로드 실패: ${_escHtml(err.message)}</div>`;
  }
}

// ─────────────────────────────────────────────────────────────────────────
// OPTIONS SIGNAL (Phase 24) — SPY/QQQ PCR + MaxPain + GEX
// ─────────────────────────────────────────────────────────────────────────
const _OPT_STATE = { symbol: 'SPY', data: {} };

async function _renderOptionsSignal() {
  const box = document.getElementById('ov-options');
  if (!box) return;
  try {
    const [spy, qqq] = await Promise.all([
      fetch('/api/options_signal?symbol=SPY').then(r => r.json()),
      fetch('/api/options_signal?symbol=QQQ').then(r => r.json()),
    ]);
    _OPT_STATE.data.SPY = spy;
    _OPT_STATE.data.QQQ = qqq;

    if (spy.error && qqq.error) {
      box.innerHTML = `<div class="ov-section-empty">옵션 시그널 로드 실패: ${_escHtml(spy.error || qqq.error)}</div>`;
      return;
    }

    box.innerHTML = `
      <div class="opt-section">
        <div class="opt-title">🔮 옵션 시그널
          <span class="opt-sub">SPY/QQQ 옵션 체인 기반 PCR · 맥스페인 · GEX 분석</span>
        </div>
        <div class="opt-tabs" id="opt-tabs">
          <button class="opt-tab active" data-sym="SPY">SPY (S&amp;P500)</button>
          <button class="opt-tab"        data-sym="QQQ">QQQ (나스닥100)</button>
        </div>
        <div id="opt-body"></div>
        <div class="opt-disclaimer">
          ⚠ yfinance 옵션 데이터는 장 마감 기준 지연. GEX는 근사치 (정밀 감마 미사용).
          투자 추천이 아닌 시장 심리 참고용입니다.
        </div>
      </div>`;

    document.getElementById('opt-tabs').addEventListener('click', (e) => {
      const btn = e.target.closest('.opt-tab');
      if (!btn || btn.dataset.sym === _OPT_STATE.symbol) return;
      _OPT_STATE.symbol = btn.dataset.sym;
      document.querySelectorAll('#opt-tabs .opt-tab').forEach(b =>
        b.classList.toggle('active', b.dataset.sym === _OPT_STATE.symbol));
      _renderOptionsBody();
    });
    _renderOptionsBody();
  } catch (err) {
    box.innerHTML = `<div class="ov-section-empty">옵션 시그널 오류: ${_escHtml(err.message)}</div>`;
  }
}

function _renderOptionsBody() {
  const host = document.getElementById('opt-body');
  if (!host) return;
  const data = _OPT_STATE.data[_OPT_STATE.symbol];
  if (!data || data.error) {
    host.innerHTML = `<div class="ov-section-empty">${_escHtml(data?.error || '데이터 없음')}</div>`;
    return;
  }
  const pcr = data.pcr || {};
  const mp  = data.max_pain || {};
  const gex = data.gex || {};
  const o   = data.overall || {};
  const dirClass = o.direction === '강세' ? 'bullish' : o.direction === '약세' ? 'bearish' : 'neutral';

  const reasonsHTML = (o.reasons || []).map(r => `<li>${_escHtml(r)}</li>`).join('');

  host.innerHTML = `
    <div class="opt-overall ${dirClass}">
      <div class="opt-overall-emoji">${o.emoji || '🟡'}</div>
      <div class="opt-overall-text">
        <div class="opt-overall-direction">${_OPT_STATE.symbol} 종합: <b>${_escHtml(o.direction || '중립')}</b></div>
        <div class="opt-overall-meta">
          현재가 <b>$${data.spot_price}</b> · 만기 <b>${_escHtml(data.expiry || '')}</b>
          · 점수 🟢${o.bullish_score || 0} 🔴${o.bearish_score || 0}
        </div>
      </div>
      <ul class="opt-overall-reasons">${reasonsHTML}</ul>
    </div>

    <div class="opt-card-grid">
      <div class="opt-card">
        <div class="opt-card-label">풋/콜 비율 (PCR)</div>
        <div class="opt-card-value">${pcr.volume ?? '—'}</div>
        <div class="opt-card-sub">거래량 기준 · OI ${pcr.open_interest ?? '—'}</div>
        <div class="opt-card-detail">
          풋 ${Number(pcr.total_put_vol || 0).toLocaleString()}<br>
          콜 ${Number(pcr.total_call_vol || 0).toLocaleString()}
        </div>
        <div class="opt-card-signal">${_escHtml(pcr.signal || '')}</div>
      </div>

      <div class="opt-card">
        <div class="opt-card-label">맥스페인</div>
        <div class="opt-card-value">$${mp.strike ?? '—'}</div>
        <div class="opt-card-sub" style="color:${(mp.diff_pct || 0) >= 0 ? '#FF3333' : '#33AA33'}">
          ${(mp.diff_pct || 0) >= 0 ? '+' : ''}${mp.diff_pct || 0}% from spot
        </div>
        <div class="opt-card-signal">${_escHtml(mp.signal || '')}</div>
      </div>

      <div class="opt-card">
        <div class="opt-card-label">감마 익스포저 (GEX, 근사)</div>
        <div class="opt-card-value" style="color:${gex.regime === 'positive' ? '#33AA33' : '#FF6B6B'}">
          ${gex.regime === 'positive' ? '양수' : '음수'}
        </div>
        <div class="opt-card-sub">${gex.total != null ? Number(gex.total).toLocaleString() : '—'}</div>
        <div class="opt-card-detail">
          Call Wall: <b style="color:#33AA33">$${gex.call_wall?.strike ?? '—'}</b>
          (OI ${Number(gex.call_wall?.oi || 0).toLocaleString()})<br>
          Put Wall: <b style="color:#FF6B6B">$${gex.put_wall?.strike ?? '—'}</b>
          (OI ${Number(gex.put_wall?.oi || 0).toLocaleString()})
        </div>
        <div class="opt-card-signal">${_escHtml(gex.signal || '')}</div>
      </div>
    </div>

    <div class="opt-chart-title">행사가별 콜/풋 미결제약정 (OI)</div>
    <div id="opt-oi-chart"></div>
    <div class="opt-chart-title">맥스페인 곡선</div>
    <div id="opt-mp-chart"></div>
  `;
  _drawOIChart(data);
  _drawMaxPainChart(data);
}

function _drawOIChart(data) {
  const host = document.getElementById('opt-oi-chart');
  if (!host) return;
  const rows = (data.gex?.by_strike || [])
    .filter(d => d.strike >= data.spot_price * 0.92 && d.strike <= data.spot_price * 1.08)
    .sort((a, b) => a.strike - b.strike);
  if (!rows.length) { host.innerHTML = '<div class="ov-section-empty">OI 데이터 없음</div>'; return; }

  const W = Math.max(host.clientWidth || 900, 600);
  const H = 280;
  const pad = { top: 24, right: 20, bottom: 40, left: 56 };
  const plotH = H - pad.top - pad.bottom;
  const mid = pad.top + plotH / 2;

  const maxOI = Math.max(...rows.map(r => Math.max(r.call_oi, r.put_oi)));
  if (maxOI <= 0) { host.innerHTML = '<div class="ov-section-empty">OI 0</div>'; return; }

  const innerW = W - pad.left - pad.right;
  const groupW = innerW / rows.length;
  const barW = Math.max(2, groupW * 0.38);

  let svg = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" class="opt-svg">`;
  svg += `<rect width="${W}" height="${H}" fill="#151516" rx="8"/>`;
  svg += `<line x1="${pad.left}" y1="${mid}" x2="${W - pad.right}" y2="${mid}" stroke="#3a3a3c" stroke-width="1"/>`;

  rows.forEach((r, i) => {
    const cx = pad.left + (i + 0.5) * groupW;
    const callH = (r.call_oi / maxOI) * (plotH / 2 - 4);
    const putH  = (r.put_oi  / maxOI) * (plotH / 2 - 4);
    svg += `<rect x="${cx - barW - 1}" y="${mid - callH}" width="${barW}" height="${callH}" fill="#33AA33" opacity="0.75"><title>${r.strike} Call OI ${r.call_oi.toLocaleString()}</title></rect>`;
    svg += `<rect x="${cx + 1}"        y="${mid}"         width="${barW}" height="${putH}"  fill="#FF6B6B" opacity="0.75"><title>${r.strike} Put OI ${r.put_oi.toLocaleString()}</title></rect>`;
  });

  // 현재가 라인
  const spot = data.spot_price;
  const spotIdx = rows.findIndex(r => r.strike >= spot);
  if (spotIdx >= 0) {
    const x = pad.left + (spotIdx + 0.5) * groupW;
    svg += `<line x1="${x}" y1="${pad.top}" x2="${x}" y2="${H - pad.bottom}" stroke="#FFD700" stroke-width="2" stroke-dasharray="4,4"/>`;
    svg += `<text x="${x}" y="${pad.top - 6}" fill="#FFD700" font-size="10" text-anchor="middle">Spot $${spot}</text>`;
  }
  // 행사가 라벨 (약 10개 간격)
  const step = Math.max(1, Math.floor(rows.length / 10));
  rows.forEach((r, i) => {
    if (i % step !== 0) return;
    const x = pad.left + (i + 0.5) * groupW;
    svg += `<text x="${x}" y="${H - pad.bottom + 14}" fill="#888" font-size="9" text-anchor="middle">${r.strike}</text>`;
  });
  // 범례
  svg += `<rect x="${W - 150}" y="8" width="10" height="10" fill="#33AA33"/>`;
  svg += `<text x="${W - 135}" y="17" fill="#888" font-size="10">Call OI</text>`;
  svg += `<rect x="${W - 80}"  y="8" width="10" height="10" fill="#FF6B6B"/>`;
  svg += `<text x="${W - 65}"  y="17" fill="#888" font-size="10">Put OI</text>`;
  // Y축 레이블
  svg += `<text x="8" y="${pad.top + 12}" fill="#888" font-size="9">Call</text>`;
  svg += `<text x="8" y="${H - pad.bottom - 2}" fill="#888" font-size="9">Put</text>`;

  svg += '</svg>';
  host.innerHTML = svg;
}

function _drawMaxPainChart(data) {
  const host = document.getElementById('opt-mp-chart');
  if (!host) return;
  const rows = (data.max_pain?.pain_by_strike || []).slice().sort((a, b) => a.strike - b.strike);
  if (rows.length < 2) { host.innerHTML = '<div class="ov-section-empty">맥스페인 데이터 부족</div>'; return; }

  const W = Math.max(host.clientWidth || 900, 600);
  const H = 220;
  const pad = { top: 20, right: 20, bottom: 36, left: 60 };
  const plotW = W - pad.left - pad.right;
  const plotH = H - pad.top - pad.bottom;

  const xs = rows.map(r => r.strike);
  const ys = rows.map(r => r.total_pain);
  const xMin = Math.min(...xs), xMax = Math.max(...xs);
  const yMin = Math.min(...ys), yMax = Math.max(...ys);
  const sx = v => pad.left + ((v - xMin) / (xMax - xMin || 1)) * plotW;
  const sy = v => pad.top + plotH - ((v - yMin) / (yMax - yMin || 1)) * plotH;

  let svg = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" class="opt-svg">`;
  svg += `<rect width="${W}" height="${H}" fill="#151516" rx="8"/>`;

  // Pain curve
  const pathD = rows.map((r, i) =>
    `${i === 0 ? 'M' : 'L'}${sx(r.strike).toFixed(1)},${sy(r.total_pain).toFixed(1)}`).join(' ');
  svg += `<path d="${pathD}" fill="none" stroke="#FFD700" stroke-width="2"/>`;

  // MaxPain strike (minimum) + spot line
  const mpStrike = data.max_pain?.strike;
  const spot = data.spot_price;
  if (mpStrike != null) {
    const x = sx(mpStrike);
    svg += `<line x1="${x}" y1="${pad.top}" x2="${x}" y2="${H - pad.bottom}" stroke="#33AA33" stroke-width="2" stroke-dasharray="4,4"/>`;
    svg += `<text x="${x}" y="${pad.top - 6}" fill="#33AA33" font-size="10" text-anchor="middle">Max Pain $${mpStrike}</text>`;
  }
  if (spot != null && spot >= xMin && spot <= xMax) {
    const x = sx(spot);
    svg += `<line x1="${x}" y1="${pad.top}" x2="${x}" y2="${H - pad.bottom}" stroke="#FF9500" stroke-width="2" stroke-dasharray="2,3"/>`;
    svg += `<text x="${x}" y="${H - pad.bottom + 14}" fill="#FF9500" font-size="10" text-anchor="middle">Spot $${spot}</text>`;
  }
  // X-axis ticks (5개)
  for (let i = 0; i < 5; i++) {
    const v = xMin + (xMax - xMin) * (i / 4);
    svg += `<text x="${sx(v)}" y="${H - pad.bottom + 28}" fill="#666" font-size="9" text-anchor="middle">${v.toFixed(0)}</text>`;
  }
  svg += `<text x="${pad.left}" y="${pad.top - 6}" fill="#888" font-size="9">Total Pain (optcost)</text>`;
  svg += '</svg>';
  host.innerHTML = svg;
}

async function _renderMacroCards() {
  const box = document.getElementById('ov-macro');
  if (!box) return;
  try {
    const r = await fetch('/api/macro');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    const items = d.items || [];
    if (!items.length) { box.innerHTML = ''; return; }

    const groupDef = [
      { cat: 'currency',   title: '💱 환율' },
      { cat: 'commodity',  title: '🛢️ 원자재' },
      { cat: 'bond',       title: '📊 채권/금리' },
      { cat: 'volatility', title: '📈 변동성' },
      { cat: 'crypto',     title: '₿ 암호화폐' },
    ];
    const cardsHtml = items.map(it => {
      const col = it.change_pct > 0 ? '#FF3333' : it.change_pct < 0 ? '#33AA33' : 'var(--text-muted)';
      const sign = it.change_pct > 0 ? '+' : '';
      const arrow = it.change_pct > 0 ? '▲' : it.change_pct < 0 ? '▼' : '•';
      const valStr = Number(it.value).toLocaleString('en-US', {
        maximumFractionDigits: Math.abs(it.value) < 10 ? 4 : 2,
      });
      return `<div class="macro-card" data-cat="${it.category}">
        <div class="macro-card-name">${_escHtml(it.name)}</div>
        <div class="macro-card-value">${valStr}${it.unit ? ' <span class="macro-card-unit">' + _escHtml(it.unit) + '</span>' : ''}</div>
        <div class="macro-card-change" style="color:${col}">${arrow} ${sign}${it.change_pct.toFixed(2)}%</div>
      </div>`;
    }).join('');

    box.innerHTML = `
      <div class="macro-section">
        <div class="macro-head">🌐 글로벌 매크로 <span class="macro-sub">${d.updated_at || ''} 기준</span></div>
        <div class="macro-grid">${cardsHtml}</div>
      </div>`;
  } catch (err) {
    box.innerHTML = `<div class="ov-section-empty">매크로 로드 실패: ${_escHtml(err.message)}</div>`;
  }
}

let _rotationData = null;
let _rotationMode = '1w_1m';

async function _renderSectorRotation() {
  const box = document.getElementById('ov-rotation');
  if (!box) return;
  try {
    const [r1, r2] = await Promise.all([
      fetch('/api/sector_rotation'),
      fetch('/api/sector_rotation/phase'),
    ]);
    if (!r1.ok) throw new Error('HTTP ' + r1.status);
    _rotationData = await r1.json();
    const phaseData = r2.ok ? await r2.json() : null;
    const sectors = _rotationData.sectors || [];
    if (!sectors.length) { box.innerHTML = ''; return; }

    let phaseHtml = '';
    if (phaseData && !phaseData.error) {
      const ph = phaseData.market_phase || {};
      const phColor = {
        '위험선호': '#33AA33', '중립': '#FFD700',
        '경계': '#FFA500', '위험회피': '#FF3333',
      }[ph.phase] || '#888';
      const recHtml = (phaseData.recommended || []).map(s =>
        `<div class="pr-chip pr-rec" data-sector="${_escHtml(s.name)}">${_escHtml(s.name)}<small>+${s.ret_1m}% (1M)</small></div>`
      ).join('');
      const avoHtml = (phaseData.avoid || []).map(s =>
        `<div class="pr-chip pr-avoid">${_escHtml(s.name)}<small>${s.ret_1m}% (1M)</small></div>`
      ).join('');
      phaseHtml = `
        <div class="pr-banner">
          <div class="pr-phase">
            <span class="pr-phase-dot" style="background:${phColor}"></span>
            <span class="pr-phase-label">시장 국면: <b>${_escHtml(ph.phase || '—')}</b></span>
            <span class="pr-phase-reason">${_escHtml((ph.reasons||[]).join(' · '))}</span>
          </div>
          <div class="pr-cols">
            <div class="pr-col">
              <div class="pr-col-title">✅ 추천 섹터 (모멘텀 상위)</div>
              <div class="pr-chips">${recHtml || '<span class="pr-empty">해당 없음</span>'}</div>
            </div>
            <div class="pr-col">
              <div class="pr-col-title">⚠️ 회피 섹터 (모멘텀 하위)</div>
              <div class="pr-chips">${avoHtml || '<span class="pr-empty">해당 없음</span>'}</div>
            </div>
          </div>
        </div>`;
    }

    box.innerHTML = `
      ${phaseHtml}
      <div class="rotation-section">
        <div class="rotation-head">🔄 섹터 로테이션 <span class="rotation-sub">
          X축: 1개월 · Y축: 1주 · 버블 크기: 당일 등락률 절대값 · ${_rotationData.updated_at}
        </span></div>
        <div id="rotation-chart-area"></div>
        <div class="rotation-table-wrap">
          <div class="rotation-table-title">섹터별 수익률 히트맵 (상위 20)</div>
          <div id="rotation-heat-table"></div>
        </div>
      </div>`;
    _drawRotationBubble();
    _drawRotationHeatmap();
  } catch (err) {
    box.innerHTML = `<div class="ov-section-empty">섹터 로테이션 로드 실패: ${_escHtml(err.message)}</div>`;
  }
}

function _drawRotationBubble() {
  const area = document.getElementById('rotation-chart-area');
  if (!area || !_rotationData) return;
  const sectors = (_rotationData.sectors || []).filter(s => s.ret_1m != null && s.ret_1w != null);
  const w = Math.max(area.clientWidth || 800, 600);
  const h = 460;
  const pad = { top: 24, right: 24, bottom: 40, left: 56 };

  const xs = sectors.map(s => s.ret_1m);
  const ys = sectors.map(s => s.ret_1w);
  const xMin = Math.min(-5, ...xs);
  const xMax = Math.max( 5, ...xs);
  const yMin = Math.min(-5, ...ys);
  const yMax = Math.max( 5, ...ys);
  const sX = v => pad.left + ((v - xMin) / (xMax - xMin || 1)) * (w - pad.left - pad.right);
  const sY = v => h - pad.bottom - ((v - yMin) / (yMax - yMin || 1)) * (h - pad.top - pad.bottom);

  const x0 = sX(0), y0 = sY(0);
  let svg = `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" xmlns="http://www.w3.org/2000/svg" class="rotation-svg">`;
  svg += `<rect width="${w}" height="${h}" fill="#151516" rx="8"/>`;
  // 4사분면 배경 (Leading/Improving/Lagging/Weakening)
  svg += `<rect x="${x0}" y="${pad.top}"       width="${w-pad.right-x0}" height="${y0-pad.top}"        fill="rgba(255,51,51,0.04)"/>`;
  svg += `<rect x="${pad.left}" y="${pad.top}" width="${x0-pad.left}"    height="${y0-pad.top}"        fill="rgba(68,136,255,0.04)"/>`;
  svg += `<rect x="${pad.left}" y="${y0}"      width="${x0-pad.left}"    height="${h-pad.bottom-y0}"   fill="rgba(51,170,51,0.04)"/>`;
  svg += `<rect x="${x0}" y="${y0}"            width="${w-pad.right-x0}" height="${h-pad.bottom-y0}"   fill="rgba(255,149,0,0.04)"/>`;
  // 0선
  svg += `<line x1="${pad.left}" y1="${y0}" x2="${w-pad.right}" y2="${y0}" stroke="#3a3a3c" stroke-dasharray="3,3"/>`;
  svg += `<line x1="${x0}" y1="${pad.top}"  x2="${x0}"          y2="${h-pad.bottom}" stroke="#3a3a3c" stroke-dasharray="3,3"/>`;
  // 축 라벨
  svg += `<text x="${w/2}" y="${h-8}" fill="#888" font-size="11" text-anchor="middle">1개월 수익률 (%)</text>`;
  svg += `<text x="16" y="${h/2}" fill="#888" font-size="11" text-anchor="middle" transform="rotate(-90, 16, ${h/2})">1주 수익률 (%)</text>`;
  // 사분면 라벨
  svg += `<text x="${w-pad.right-8}" y="${pad.top+14}"   fill="#FF3333" font-size="10" text-anchor="end">Leading (주도)</text>`;
  svg += `<text x="${pad.left+8}"    y="${pad.top+14}"   fill="#4488FF" font-size="10">Improving (개선)</text>`;
  svg += `<text x="${pad.left+8}"    y="${h-pad.bottom-6}" fill="#33AA33" font-size="10">Lagging (부진)</text>`;
  svg += `<text x="${w-pad.right-8}" y="${h-pad.bottom-6}" fill="#FF9500" font-size="10" text-anchor="end">Weakening (둔화)</text>`;

  sectors.forEach(s => {
    const cx = sX(s.ret_1m);
    const cy = sY(s.ret_1w);
    const r  = Math.max(6, Math.min(24, Math.abs(s.change_today || 0) * 4 + 6));
    const up = (s.ret_1m || 0) >= 0;
    const fill = up ? 'rgba(255,51,51,0.45)' : 'rgba(51,170,51,0.45)';
    const stroke = up ? '#FF3333' : '#33AA33';
    svg += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${fill}" stroke="${stroke}" stroke-width="1">
      <title>${_escHtml(s.name)} · 1w ${s.ret_1w}% · 1m ${s.ret_1m}% · today ${s.change_today}% (${s.stock_count}종목)</title>
    </circle>`;
    if (r >= 10) {
      const short = s.name.length > 5 ? s.name.slice(0, 5) + '…' : s.name;
      svg += `<text x="${cx}" y="${cy+3}" fill="#fff" font-size="9" text-anchor="middle" pointer-events="none">${_escHtml(short)}</text>`;
    }
  });
  svg += '</svg>';
  area.innerHTML = svg;
}

function _drawRotationHeatmap() {
  const box = document.getElementById('rotation-heat-table');
  if (!box || !_rotationData) return;
  const sectors = (_rotationData.sectors || []).slice(0, 20);
  const heat = (v) => {
    if (v == null) return 'var(--text-muted)';
    if (v >=  5) return '#FF3333';
    if (v >=  2) return '#FF6B6B';
    if (v >=  0) return '#FFA3A3';
    if (v >= -2) return '#A3D9A3';
    if (v >= -5) return '#33AA33';
    return '#2A8A2A';
  };
  const fmtPct = v => v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
  const rows = sectors.map(s => `
    <tr>
      <td class="rotation-name">${_escHtml(s.name)}</td>
      <td class="r" style="color:${heat(s.change_today)}">${fmtPct(s.change_today)}</td>
      <td class="r" style="color:${heat(s.ret_1w)}">${fmtPct(s.ret_1w)}</td>
      <td class="r" style="color:${heat(s.ret_1m)};font-weight:700">${fmtPct(s.ret_1m)}</td>
      <td class="r rotation-count">${s.stock_count}</td>
    </tr>
  `).join('');
  box.innerHTML = `<table class="rotation-table">
    <thead><tr>
      <th>섹터</th><th class="r">당일</th><th class="r">1주</th><th class="r">1개월</th><th class="r">종목수</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

async function _renderUpcomingEvents() {
  const box = document.getElementById('ov-upcoming');
  if (!box) return;
  try {
    const r = await fetch('/api/calendar/economic');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    const today = now_kst_date();
    const todayT = new Date(today + 'T00:00:00').getTime();
    const upcoming = (d.events || []).filter(e => {
      if (e.impact !== 'high') return false;
      const et = new Date(e.date + 'T00:00:00').getTime();
      const diffDays = (et - todayT) / (24 * 3600 * 1000);
      return diffDays >= 0 && diffDays <= 3;
    });
    if (!upcoming.length) {
      box.innerHTML = `
        <div class="ov-upcoming-wrap">
          <div class="ov-upcoming-head">📢 향후 3일 주요 일정</div>
          <div class="ov-upcoming-empty">향후 3일 내 high 임팩트 지표 없음</div>
        </div>`;
      return;
    }
    const rows = upcoming.map(e => `
      <div class="ov-upcoming-row">
        <span class="ov-u-impact">${e.impact_emoji}</span>
        <span class="ov-u-date">${_calFormatDateKR(e.date)}</span>
        <span class="ov-u-country">${e.country_kr}</span>
        <span class="ov-u-event">${_escHtml(e.event_kr || e.event || '')}</span>
        ${e.estimate != null ? `<span class="ov-u-est">예상 ${e.estimate}${e.unit || ''}</span>` : ''}
      </div>`).join('');
    box.innerHTML = `
      <div class="ov-upcoming-wrap">
        <div class="ov-upcoming-head">📢 향후 3일 주요 일정 <span class="ov-u-hint">(전체는 📅 캘린더 메뉴에서)</span></div>
        ${rows}
      </div>`;
  } catch (err) {
    box.innerHTML = `<div class="ov-upcoming-empty">캘린더 로드 실패: ${_escHtml(err.message)}</div>`;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// COMPARE PANEL (Phase 8-1)
// ─────────────────────────────────────────────────────────────────────────────
const CMP_STATE = { code1: null, name1: null, code2: null, name2: null, period: '1M', reqToken: 0 };

function openComparePanel(initialCode, initialName) {
  const panel = document.getElementById('compare-panel');
  panel.style.display = '';

  // 최초 열 때만 마크업 주입. 재열림 시 기존 상태 유지.
  if (!panel.dataset.built) {
    panel.dataset.built = '1';
    panel.innerHTML = `
      <div class="cmp-header">
        <span class="cmp-title">종목 비교</span>
        <button class="dp-close" id="cmp-close-btn">✕ 닫기</button>
      </div>
      <div class="cmp-inputs">
        <div class="cmp-input-group">
          <label>기준 종목</label>
          <div class="cmp-input-row">
            <input class="cmp-search-input" type="text" id="cmp-input-1"
                   placeholder="종목명 또는 6자리 코드" autocomplete="off">
            <button class="cmp-clear-btn" id="cmp-clear-1">✕</button>
          </div>
          <div class="cmp-search-dropdown" id="cmp-dd-1"></div>
        </div>
        <div class="cmp-input-group">
          <label>비교 종목</label>
          <div class="cmp-input-row">
            <input class="cmp-search-input" type="text" id="cmp-input-2"
                   placeholder="종목명 또는 6자리 코드" autocomplete="off">
            <button class="cmp-clear-btn" id="cmp-clear-2">✕</button>
          </div>
          <div class="cmp-search-dropdown" id="cmp-dd-2"></div>
        </div>
      </div>
      <div class="cmp-cards" id="cmp-cards"></div>
      <div class="cmp-period-row" id="cmp-period-row">
        <button class="cmp-period-btn active" data-p="1M">1M</button>
        <button class="cmp-period-btn"        data-p="3M">3M</button>
        <button class="cmp-period-btn"        data-p="6M">6M</button>
        <button class="cmp-period-btn"        data-p="1Y">1Y</button>
        <button class="cmp-period-btn"        data-p="3Y">3Y</button>
        <button class="cmp-period-btn"        data-p="5Y">5Y</button>
      </div>
      <div class="cmp-canvas-wrap">
        <canvas id="cmp-canvas" width="1152" height="320"></canvas>
      </div>
      <div class="cmp-legend" id="cmp-legend"></div>`;

    document.getElementById('cmp-close-btn').addEventListener('click', closeComparePanel);

    _bindCompareSearch('cmp-input-1', 'cmp-dd-1', 1);
    _bindCompareSearch('cmp-input-2', 'cmp-dd-2', 2);

    document.getElementById('cmp-clear-1').addEventListener('click', () => {
      CMP_STATE.code1 = CMP_STATE.name1 = null;
      document.getElementById('cmp-input-1').value = '';
      _clearCompareChart();
    });
    document.getElementById('cmp-clear-2').addEventListener('click', () => {
      CMP_STATE.code2 = CMP_STATE.name2 = null;
      document.getElementById('cmp-input-2').value = '';
      _clearCompareChart();
    });

    document.getElementById('cmp-period-row').addEventListener('click', (e) => {
      const btn = e.target.closest('.cmp-period-btn');
      if (!btn) return;
      document.querySelectorAll('#cmp-period-row .cmp-period-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      CMP_STATE.period = btn.dataset.p;
      fetchCompare();
    });
  }

  // 차트 패널에서 넘어온 경우 기준 종목 자동 세팅
  if (initialCode) {
    CMP_STATE.code1 = initialCode;
    CMP_STATE.name1 = initialName || initialCode;
    document.getElementById('cmp-input-1').value = `${initialName || initialCode} (${initialCode})`;
    if (CMP_STATE.code2) fetchCompare();
  }

  panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function closeComparePanel() {
  document.getElementById('compare-panel').style.display = 'none';
}

function _bindCompareSearch(inputId, ddId, slot) {
  const inp = document.getElementById(inputId);
  const dd  = document.getElementById(ddId);
  let timer;

  inp.addEventListener('input', () => {
    clearTimeout(timer);
    const q = inp.value.trim();
    if (q.length < 2) { dd.innerHTML = ''; dd.style.display = 'none'; return; }
    timer = setTimeout(async () => {
      try {
        const r = await fetch(`${APP.market === "us" ? "/api/us/search" : "/api/stock_search"}?q=${encodeURIComponent(q)}`);
        const res = await r.json();
        if (!res.length) { dd.innerHTML = ''; dd.style.display = 'none'; return; }
        dd.innerHTML = res.map(it =>
          `<div class="cmp-search-result" data-code="${it.code}" data-name="${it.name}">
            <span class="cmp-search-result-code">${it.code}</span>${it.name}
          </div>`
        ).join('');
        dd.style.display = 'block';
      } catch { dd.innerHTML = ''; dd.style.display = 'none'; }
    }, 280);
  });

  inp.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const first = dd.querySelector('.cmp-search-result');
      if (first) first.click();
      else if (/^\d{6}$/.test(inp.value.trim())) _pickCompare(slot, inp.value.trim(), inp.value.trim());
    }
  });

  dd.addEventListener('click', (e) => {
    const row = e.target.closest('.cmp-search-result');
    if (!row) return;
    _pickCompare(slot, row.dataset.code, row.dataset.name);
    inp.value = `${row.dataset.name} (${row.dataset.code})`;
    dd.innerHTML = ''; dd.style.display = 'none';
  });
}

function _pickCompare(slot, code, name) {
  if (slot === 1) { CMP_STATE.code1 = code; CMP_STATE.name1 = name; }
  else            { CMP_STATE.code2 = code; CMP_STATE.name2 = name; }
  console.log('[compare] pick slot', slot, code, name);
  fetchCompare();
}

async function fetchCompare() {
  const { code1, code2, period } = CMP_STATE;
  if (!code1 || !code2) return;
  const token = ++CMP_STATE.reqToken;
  const legend = document.getElementById('cmp-legend');
  const cards  = document.getElementById('cmp-cards');
  cards.innerHTML  = '<div class="cmp-loading"><div class="spin"></div>비교 데이터 로딩 중…</div>';
  legend.textContent = '';

  try {
    const r   = await fetch(`/api/compare?code1=${code1}&code2=${code2}&period=${period}`,
                            { cache: 'no-store' });
    const res = await r.json();
    if (token !== CMP_STATE.reqToken) return;   // 더 최근 요청이 있으면 버림
    if (!r.ok || res.error) throw new Error(res.error || ('HTTP ' + r.status));

    const s1 = res.stock1, s2 = res.stock2;
    const col1 = '#30D158', col2 = '#FF9F0A';
    const pctHTML = (v) => {
      const c = v > 0 ? '#FF3333' : v < 0 ? '#33AA33' : 'var(--text-muted)';
      const s = v > 0 ? '+' : '';
      return `<span style="color:${c}">${s}${v.toFixed(2)}%</span>`;
    };
    cards.innerHTML = `
      <div class="cmp-card">
        <div class="cmp-card-code"><span class="cmp-card-dot" style="background:${col1}"></span>${s1.code}</div>
        <div class="cmp-card-name">${s1.name}</div>
        <div class="cmp-card-price">₩${s1.current_price.toLocaleString()}</div>
        <div class="cmp-card-pct">${pctHTML(s1.change_pct)} <span style="color:var(--text-muted);font-weight:400;font-size:11px">(${period} 수익률)</span></div>
      </div>
      <div class="cmp-card">
        <div class="cmp-card-code"><span class="cmp-card-dot" style="background:${col2}"></span>${s2.code}</div>
        <div class="cmp-card-name">${s2.name}</div>
        <div class="cmp-card-price">₩${s2.current_price.toLocaleString()}</div>
        <div class="cmp-card-pct">${pctHTML(s2.change_pct)} <span style="color:var(--text-muted);font-weight:400;font-size:11px">(${period} 수익률)</span></div>
      </div>`;
    legend.innerHTML = `
      <span><span class="cmp-card-dot" style="background:${col1}"></span>${s1.name}</span>
      <span><span class="cmp-card-dot" style="background:${col2}"></span>${s2.name}</span>`;

    _drawCompareChart(document.getElementById('cmp-canvas'), res, col1, col2);
  } catch (err) {
    if (token !== CMP_STATE.reqToken) return;
    cards.innerHTML = `<div class="cmp-error">로드 실패: ${err.message}</div>`;
    _clearCompareChart();
  }
}

function _clearCompareChart() {
  const canvas = document.getElementById('cmp-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#1C1C1E';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
}

function _drawCompareChart(canvas, data, color1, color2) {
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  const PAD = { top: 20, right: 22, bottom: 28, left: 64 };
  const cw = W - PAD.left - PAD.right;
  const ch = H - PAD.top  - PAD.bottom;
  const len = data.dates.length;
  const r1  = data.stock1.returns;
  const r2  = data.stock2.returns;

  const all = r1.concat(r2).concat([0]);   // 0%선 포함
  let maxR  = Math.max(...all);
  let minR  = Math.min(...all);
  const pad = Math.max((maxR - minR) * 0.06, 0.2);
  maxR += pad; minR -= pad;
  const rng = maxR - minR || 1;

  const toX = i => PAD.left + (len === 1 ? cw / 2 : (i / (len - 1)) * cw);
  const toY = v => PAD.top  + (1 - (v - minR) / rng) * ch;

  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#1C1C1E';
  ctx.fillRect(0, 0, W, H);

  // Y축 그리드 + 라벨
  ctx.strokeStyle = 'rgba(255,255,255,0.06)'; ctx.lineWidth = 1;
  ctx.fillStyle = '#636366'; ctx.font = '11px Noto Sans KR, sans-serif';
  ctx.textAlign = 'right';
  for (let i = 0; i <= 4; i++) {
    const v = minR + (rng * i / 4);
    const y = toY(v);
    ctx.beginPath(); ctx.moveTo(PAD.left, y); ctx.lineTo(W - PAD.right, y); ctx.stroke();
    ctx.fillText(`${v >= 0 ? '+' : ''}${v.toFixed(1)}%`, PAD.left - 6, y + 4);
  }

  // 0% 기준선
  const zeroY = toY(0);
  if (zeroY >= PAD.top && zeroY <= H - PAD.bottom) {
    ctx.strokeStyle = 'rgba(255,255,255,0.25)'; ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(PAD.left, zeroY); ctx.lineTo(W - PAD.right, zeroY); ctx.stroke();
    ctx.setLineDash([]);
  }

  // X축 날짜 라벨 (최대 6개)
  ctx.fillStyle = '#636366'; ctx.textAlign = 'center';
  const stepsX = Math.min(6, len);
  for (let i = 0; i < stepsX; i++) {
    const idx = Math.floor((len - 1) * i / (stepsX - 1 || 1));
    ctx.fillText(data.dates[idx].slice(5), toX(idx), H - PAD.bottom + 16);
  }

  const drawLine = (arr, color) => {
    ctx.strokeStyle = color; ctx.lineWidth = 2;
    ctx.beginPath();
    arr.forEach((v, i) => i === 0 ? ctx.moveTo(toX(i), toY(v)) : ctx.lineTo(toX(i), toY(v)));
    ctx.stroke();
  };
  drawLine(r1, color1);
  drawLine(r2, color2);
}

// ─────────────────────────────────────────────────────────────────────────────
// SIDEBAR ROUTER (Phase 9)
// ─────────────────────────────────────────────────────────────────────────────
const PAGE_RENDERERS = {
  thememap:  () => { renderCurrentView(); if (APP.data && APP.data.new_high_sectors) renderNewHighSectors(); },
  watchlist: () => { renderWatchlistPage(); },
  market:    () => { renderMarketOverview(); },
  compare:   () => { openComparePanel(); },
  screener:  () => { renderScreener(); },
  flow:      () => { renderFlowPage(); },
  sector:    () => { renderSectorPage(); },
  discover:  () => { renderDiscoverPage(); },
  agent:     () => { renderAgentPage(); },
  calendar:  () => { renderCalendarPage(); },
  research:  () => { renderResearchPage(); },
  newhighs:  () => { renderNewHighsPage(); },
  portfolio: () => { renderPortfolioPage(); },
  // journal: 분석 일지로 재할당 (Phase 4-5-2-A) — 매매일지는 portfolio로 통합됨
  journal:   () => { renderJournalPlaceholder(); },
  etfmap:    () => { renderETFMapPage(); },
  dividend:  () => { renderDividendPage(); },
  disclosure: () => { renderDisclosurePage(); },
  backtest:   () => { renderBacktestPage(); },
  correlation:() => { renderCorrelationPage(); },
  recperf:    () => { renderRecPerfPage(); },
  dashboard:  () => { renderDashboardHome(); },
  pnljournal: () => { renderPnlJournalPage(); },
  globalmacro:() => { renderGlobalMacroPage(); },
  valuechain: () => { renderValuechain2Page(); },  // Step 3: v2 (롤백 시 renderValuechainPage()로 복구)
};

// ─────────────────────────────────────────────────────────────────────────────
// 4-5-2-C: SPA URL path 라우팅
// 페이지 <-> URL 양방향 매핑. 직접 URL 진입 / 뒤로가기 / 북마크 모두 지원.
// ─────────────────────────────────────────────────────────────────────────────
const _URL_ROUTED_PAGES = new Set([
  'verification', 'journal',
  'ops-freshness', 'ops-cron', 'ops-health',
]);

/** page + params → pathname. URL 라우팅 대상이 아닌 페이지는 '/' 반환. */
function pageToUrl(page, params) {
  params = params || {};
  if (page === 'verification') {
    return params.code ? `/verification/${encodeURIComponent(params.code)}` : '/verification';
  }
  if (page === 'journal') {
    return params.id ? `/journal/${encodeURIComponent(params.id)}` : '/journal';
  }
  if (page === 'ops-freshness') return '/ops/freshness';
  if (page === 'ops-cron')      return '/ops/cron';
  if (page === 'ops-health')    return '/ops/health';
  return '/';
}

/** location.pathname → { page, params }. 인식 못 하면 page=null. */
function parseUrlPath(pathname) {
  pathname = pathname || window.location.pathname;
  // /verification 또는 /verification/<code>
  let m = pathname.match(/^\/verification(?:\/([^\/]+))?\/?$/);
  if (m) return { page: 'verification', params: m[1] ? { code: decodeURIComponent(m[1]) } : {} };
  // /journal 또는 /journal/<id>
  m = pathname.match(/^\/journal(?:\/(\d+))?\/?$/);
  if (m) return { page: 'journal', params: m[1] ? { id: parseInt(m[1], 10) } : {} };
  // /ops/<page>
  m = pathname.match(/^\/ops\/([a-z\-]+)\/?$/);
  if (m) {
    const sub = m[1];
    if (['freshness', 'cron', 'health'].includes(sub)) {
      return { page: `ops-${sub}`, params: {} };
    }
  }
  return { page: null, params: {} };
}

/** 현재 URL을 보고 해당 페이지 렌더 (초기 진입 + popstate 핸들러용).
 *  URL 라우팅 대상이 아니면 기본 페이지(thememap) 렌더. */
function applyUrlRouting() {
  const parsed = parseUrlPath();
  if (parsed.page) {
    navigateTo(parsed.page, parsed.params, { fromUrl: true });
  } else {
    // 알 수 없는 / 라우팅 안 되는 path → 기본 페이지
    navigateTo('thememap', {}, { fromUrl: true });
  }
}

function navigateTo(page, params, opts) {
  params = params || {};
  opts = opts || {};
  // 삭제/통합된 메뉴 리다이렉트 (기존 URL/북마크 호환)
  const _REDIRECTS = {
    screener: 'discover',
    newhighs: 'discover',
    flow: 'market',
    calendar: 'market',
    sector: 'thememap',
    // 4-5-2-A: journal 키는 분석 일지로 재할당. 매매일지는 portfolio로 통합돼 자체 메뉴 없음.
  };
  if (_REDIRECTS[page]) page = _REDIRECTS[page];
  if (!(page in PAGE_RENDERERS)) page = 'thememap';

  // 4-5-12: ops 페이지 이탈 시 자동 갱신 타이머 정리
  const _prevPage = APP.page;
  if (_prevPage && _prevPage !== page && _prevPage.startsWith('ops-')) {
    const key = _prevPage.replace('ops-', '');
    if (typeof _opsClearTimer === 'function') _opsClearTimer(key);
  }
  APP.page = page;
  APP.pageParams = params;

  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  const pageEl = document.getElementById(`page-${page}`);
  if (pageEl) {
    pageEl.classList.add('active');
    // URL 파라미터가 바뀐 경우 강제 재렌더
    if (params && Object.keys(params).length > 0) {
      delete pageEl.dataset.inited;
    }
  }

  document.querySelectorAll('#sidebar-menu .sidebar-item').forEach(el => {
    el.classList.toggle('active', el.dataset.page === page);
  });

  document.body.classList.remove('page-thememap','page-market',
                                  'page-screener','page-flow','page-sector','page-discover','page-calendar','page-research','page-newhighs','page-portfolio','page-journal','page-etfmap','page-dividend','page-disclosure','page-agent','page-backtest','page-correlation','page-recperf','page-dashboard','page-pnljournal','page-globalmacro','page-valuechain',
                                  'page-verification','page-ops-freshness','page-ops-cron','page-ops-health');
  document.body.classList.add(`page-${page}`);

  try { PAGE_RENDERERS[page](params); }
  catch (err) { console.error('[navigateTo] render error for', page, err); }

  // 4-5-2-C: URL 동기화 — popstate / 초기 진입 / 시장 토글 재렌더는 제외
  if (!opts.fromUrl && _URL_ROUTED_PAGES.has(page)) {
    const newPath = pageToUrl(page, params);
    if (newPath !== window.location.pathname + window.location.search) {
      try { history.pushState({ page, params }, '', newPath); }
      catch (e) { /* iframe/sandbox 환경 무시 */ }
    }
  } else if (!opts.fromUrl && !_URL_ROUTED_PAGES.has(page)) {
    // URL 라우팅 대상이 아닌 페이지로 이동할 때 URL이 /verification 등에 머물러 있다면 / 로 정리
    if (window.location.pathname !== '/') {
      try { history.pushState({ page }, '', '/'); } catch (e) {}
    }
  }

  // 모바일이면 사이드바 자동 닫기
  if (window.innerWidth <= 768 && typeof closeMobileSidebar === 'function') {
    closeMobileSidebar();
  }
}

// popstate 핸들러 — 뒤로가기/앞으로가기
window.addEventListener('popstate', () => {
  applyUrlRouting();
});

// 초기 진입 시 URL 반영 (DOM 준비 후, pages.js 로드 시점이면 이미 충족)
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    if (window.location.pathname !== '/') applyUrlRouting();
  });
} else {
  // pages.js 가 deferred 로딩되었거나 readyState='complete'
  if (window.location.pathname !== '/') applyUrlRouting();
}

document.getElementById('sidebar-menu').addEventListener('click', (e) => {
  // 더보기 토글
  const toggle = e.target.closest('#sidebar-more-toggle');
  if (toggle) {
    const items = document.querySelectorAll('.sidebar-more-item');
    const arrow = document.getElementById('sidebar-more-arrow');
    const hidden = items[0]?.style.display === 'none';
    items.forEach(it => it.style.display = hidden ? '' : 'none');
    if (arrow) arrow.textContent = hidden ? '▲' : '▼';
    return;
  }
  const item = e.target.closest('.sidebar-item');
  if (!item || !item.dataset.page) return;
  navigateTo(item.dataset.page);
});

document.getElementById('sidebar-settings').addEventListener('click', () => {
  openSettingsModal();
});

// Phase 14: 시장 전환 토글
document.getElementById('market-toggle').addEventListener('click', (e) => {
  const btn = e.target.closest('.market-btn');
  if (!btn) return;
  switchMarket(btn.dataset.market);
});

// ─────────────────────────────────────────────────────────────────────────────
// WATCHLIST PAGE (Phase 12-2)  —  localStorage 기반, 사이드바에서 진입
// ─────────────────────────────────────────────────────────────────────────────
function _findStockInThemes(code) {
  if (!APP.data || !APP.data.themes) return null;
  for (const t of APP.data.themes) {
    for (const s of (t.stocks || [])) {
      if (s.code === code) return { ...s, theme: t.name };
    }
  }
  return null;
}

async function renderWatchlistPage() {
  const root = document.getElementById('watchlist-view');
  // Phase 14: 현재 시장의 관심종목만 표시
  const list = getWatchlist().filter(i => (i.market || 'kr') === APP.market);

  if (!list.length) {
    const isUS = APP.market === 'us';
    root.innerHTML = `
      <div class="pg-wrap">
        <div class="pg-title">${isUS ? 'Watchlist' : '관심종목'}</div>
        <div class="pg-sub">${isUS ? 'Your favorite US stocks' : '자주 보는 종목을 모아보세요'}</div>
        <div class="pg-empty">
          <div class="pg-empty-title">${isUS ? 'No watchlist items' : '관심종목이 없습니다'}</div>
          ${isUS ? 'Click the ☆ button on any stock card to add it.' : '종목 카드의 ☆ 버튼을 눌러 추가하세요.'}
        </div>
      </div>`;
    return;
  }

  // 캐시에 없는 종목은 /api/price 로 보강
  const cards = await Promise.all(list.map(async (item) => {
    let stock = _findStockInThemes(item.code);
    let priceInfo = null;
    if (!stock) {
      try {
        const base = (item.market || 'kr') === 'us' ? '/api/us' : '/api';
        const r = await fetch(`${base}/price/${item.code}`);
        if (r.ok) {
          const p = await r.json();
          if (!p.error) priceInfo = p;
        }
      } catch {}
    }
    return { item, stock, priceInfo };
  }));

  const rows = cards.map(({ item, stock, priceInfo }) => {
    const sc = _escHtml(item.code);
    const sn = _escHtml(item.name || (stock && stock.name) || (priceInfo && priceInfo.name) || item.code);
    const chg = (stock && stock.change_pct) ?? (priceInfo && priceInfo.change_pct) ?? null;
    const price = (priceInfo && priceInfo.price) ?? null;
    const vol = (stock && stock.volume_mn) ?? (priceInfo && priceInfo.volume_mn) ?? null;
    const col = chg == null ? 'var(--text-muted)' : chg > 0 ? '#FF3333' : chg < 0 ? '#33AA33' : 'var(--text-muted)';
    const sign = chg == null ? '' : chg > 0 ? '+' : '';
    const themeStr = stock && stock.theme ? `<span class="wl-theme">${_escHtml(stock.theme)}</span>` : '';
    return `<div class="watchlist-card" data-code="${sc}" data-name="${sn}">
      <button class="watchlist-star star-active wl-card-star" title="제거">★</button>
      <div class="wl-name">${sn}${_marketBadgeFromItem({code: item.code, market: item.market})}</div>
      <div class="wl-code">${sc} ${themeStr}</div>
      <div class="wl-card-bottom">
        <span class="wl-pct" style="color:${col}">${chg == null ? '—' : sign + chg.toFixed(2) + '%'}</span>
        <span class="wl-price">${price != null ? '₩' + price.toLocaleString() : ''}</span>
      </div>
      <div class="wl-vol">${vol != null ? vol.toLocaleString() + 'M' : ''}</div>
    </div>`;
  }).join('');

  root.innerHTML = `
    <div class="pg-wrap">
      <div class="pg-title">관심종목</div>
      <div class="pg-sub">${list.length}종목 · 클릭 → 차트, ★ → 제거</div>
      <div class="watchlist-grid">${rows}</div>
    </div>`;

  // 카드 클릭(차트) / 별 클릭(제거) 델리게이션
  root.querySelector('.watchlist-grid').addEventListener('click', (e) => {
    const card = e.target.closest('.watchlist-card');
    if (!card) return;
    const code = card.dataset.code;
    const name = card.dataset.name;
    if (e.target.closest('.wl-card-star')) {
      e.stopPropagation();
      toggleWatchlist(code, name);
      renderWatchlistPage();
      return;
    }
    openChartPanel(code, name);
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// SCREENER PAGE (Phase 9)
// ─────────────────────────────────────────────────────────────────────────────
const SCR_STATE = { market: 'ALL', min_change: '', min_volume: '', max_per: '', max_pbr: '', q: '' };

function renderScreener() {
  const root = document.getElementById('screener-view');
  const isUS = APP.market === 'us';
  // 시장 전환 시 재빌드 (드롭다운 옵션이 다르므로)
  if (root.dataset.built === APP.market) return;
  root.dataset.built = APP.market;

  const marketOptions = isUS
    ? `<option value="ALL">All</option>
       <option value="SP500">S&amp;P 500</option>`
    : `<option value="ALL">전체</option>
       <option value="KOSPI">KOSPI</option>
       <option value="KOSDAQ">KOSDAQ</option>`;

  root.innerHTML = `
    <div class="pg-wrap">
      <div class="pg-title">${isUS ? 'Stock Screener' : '종목 스크리너'}</div>
      <div class="pg-sub">${isUS ? 'Filter S&P 500 from cached data' : '수집된 캐시 데이터에서 필터링 · API 호출 0회'}</div>

      <div class="scr-form">
        <div class="scr-field">
          <label>${isUS ? 'Market' : '시장'}</label>
          <select id="scr-market">
            ${marketOptions}
          </select>
        </div>
        <div class="scr-field">
          <label>등락률 ≥ (%)</label>
          <input type="number" step="0.1" id="scr-min-change" placeholder="예: 3">
        </div>
        <div class="scr-field">
          <label>거래대금 ≥ (백만원)</label>
          <input type="number" step="100" id="scr-min-volume" placeholder="예: 10000">
        </div>
        <div class="scr-field">
          <label>PER ≤</label>
          <input type="number" step="0.1" id="scr-max-per" placeholder="예: 15">
        </div>
        <div class="scr-field">
          <label>PBR ≤</label>
          <input type="number" step="0.1" id="scr-max-pbr" placeholder="예: 1.5">
        </div>
        <div class="scr-field" style="grid-column: span 2;">
          <label>검색 (종목명 또는 코드)</label>
          <input type="text" id="scr-q" placeholder="예: 삼성 또는 005930">
        </div>
        <div class="scr-actions">
          <button class="scr-btn" id="scr-run">검색</button>
          <button class="scr-btn secondary" id="scr-reset">초기화</button>
          <span class="scr-count" id="scr-count"></span>
        </div>
      </div>

      <div id="scr-results"></div>
    </div>`;

  document.getElementById('scr-run').addEventListener('click', _scrFetch);
  document.getElementById('scr-reset').addEventListener('click', () => {
    ['scr-min-change','scr-min-volume','scr-max-per','scr-max-pbr','scr-q'].forEach(id => {
      document.getElementById(id).value = '';
    });
    document.getElementById('scr-market').value = 'ALL';
    _scrFetch();
  });
  root.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.target.tagName === 'INPUT') _scrFetch();
  });

  _scrFetch();  // 초기 로드
}

async function _scrFetch() {
  const params = new URLSearchParams({
    market:     document.getElementById('scr-market').value || 'ALL',
    min_change: document.getElementById('scr-min-change').value || '-100',
    min_volume: document.getElementById('scr-min-volume').value || '0',
    max_per:    document.getElementById('scr-max-per').value || '9999',
    max_pbr:    document.getElementById('scr-max-pbr').value || '9999',
    q:          document.getElementById('scr-q').value || '',
  });
  const results = document.getElementById('scr-results');
  const countEl = document.getElementById('scr-count');
  results.innerHTML = '<div class="pg-empty">조회 중…</div>';

  try {
    const base = APP.market === 'us' ? '/api/us/screener' : '/api/screener';
    const r = await fetch(`${base}?${params}`);
    const d = await r.json();
    if (!r.ok || d.error) throw new Error(d.error || ('HTTP ' + r.status));

    countEl.textContent = `결과: ${d.count}종목` + (d.has_fundamentals ? '' : '  (PER/PBR 데이터 없음)');

    if (!d.stocks.length) {
      results.innerHTML = '<div class="pg-empty"><div class="pg-empty-title">조건에 맞는 종목 없음</div>필터를 완화해 보세요.</div>';
      return;
    }

    console.log('[screener] rendered', d.count, 'stocks, first 5=',
                d.stocks.slice(0, 5).map(s => `${s.code}:${s.name}`));

    const rows = d.stocks.map((s, i) => {
      const pctCol = s.change_pct > 0 ? '#FF3333' : s.change_pct < 0 ? '#33AA33' : 'var(--text-muted)';
      const sign   = s.change_pct > 0 ? '+' : '';
      const per    = s.per != null ? s.per.toFixed(1) : '—';
      const pbr    = s.pbr != null ? s.pbr.toFixed(1) : '—';
      const cap    = s.market_cap != null ? _fmtMarketCap(s.market_cap) : '—';
      const vol    = (s.volume_mn || 0).toLocaleString();
      const safeCode  = _escHtml(s.code);
      const safeName  = _escHtml(s.name);
      const safeTheme = _escHtml(s.theme || '');
      // data-code/data-name 은 HTML 속성 이스케이프, 셀 내용도 동일 소스로 렌더.
      return `<tr data-code="${safeCode}" data-name="${safeName}">
        <td><span class="rank">${i + 1}</span></td>
        <td style="color:var(--text-muted);font-size:11px">${safeCode}</td>
        <td style="font-weight:600">${safeName}</td>
        <td style="font-size:11px;color:var(--text-muted)">${safeTheme}</td>
        <td class="r" style="color:${pctCol};font-weight:700">${sign}${s.change_pct.toFixed(2)}%</td>
        <td class="r" style="color:var(--text-sub)">${vol}M</td>
        <td class="r" style="color:var(--text-sub)">${cap}</td>
        <td class="r" style="color:var(--text-sub)">${per}</td>
        <td class="r" style="color:var(--text-sub)">${pbr}</td>
      </tr>`;
    }).join('');

    results.innerHTML = `
      <table class="pg-table">
        <thead><tr>
          <th style="width:52px">#</th>
          <th>코드</th>
          <th>종목명</th>
          <th>테마</th>
          <th class="r">등락률</th>
          <th class="r">거래대금</th>
          <th class="r">시총</th>
          <th class="r">PER</th>
          <th class="r">PBR</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;

    results.querySelector('tbody').addEventListener('click', (e) => {
      const tr = e.target.closest('tr');
      if (!tr) return;
      const clickedCode = tr.dataset.code;
      const clickedName = tr.dataset.name;
      // 화면에 표시된 code 셀 (2번째 td) 과 dataset 이 일치하는지 교차검증
      const shownCode = tr.querySelector('td:nth-child(2)')?.textContent?.trim();
      const shownName = tr.querySelector('td:nth-child(3)')?.textContent?.trim();
      console.log('[screener click]', {
        dataset_code: clickedCode, dataset_name: clickedName,
        shown_code:   shownCode,   shown_name:   shownName,
      });
      if (shownCode && shownCode !== clickedCode) {
        console.error('[screener click] ❌ row DOM drift — shown vs dataset mismatch',
                      { shown: shownCode, dataset: clickedCode });
        openChartPanel(shownCode, shownName || clickedName);
        return;
      }
      openChartPanel(clickedCode, clickedName);
    });
  } catch (err) {
    countEl.textContent = '';
    results.innerHTML = `<div class="pg-empty"><div class="pg-empty-title">조회 실패</div>${err.message}</div>`;
  }
}

function _fmtMarketCap(mn) {
  // mn 은 백만원
  if (mn >= 10_000_000) return (mn / 10_000_000).toFixed(1) + '조';
  if (mn >= 10_000)     return (mn / 10_000).toFixed(0)     + '억';
  return mn.toLocaleString() + 'M';
}

// ─────────────────────────────────────────────────────────────────────────────
// 장마감 시황 자동 요약 — /api/market_summary
// ─────────────────────────────────────────────────────────────────────────────
let _MS_MARKET = 'kr';
async function _loadMarketSummary(container) {
  // 기존 holder 있으면 재사용, 없으면 생성
  let holder = container.querySelector('.ms-holder');
  if (!holder) {
    holder = document.createElement('div');
    holder.className = 'ms-holder';
    container.insertBefore(holder, container.firstChild);
  }
  holder.innerHTML = `
    <div class="ms-market-tabs">
      <button class="ms-tab ${_MS_MARKET==='kr'?'active':''}" data-ms-mkt="kr">🇰🇷 국내</button>
      <button class="ms-tab ${_MS_MARKET==='us'?'active':''}" data-ms-mkt="us">🇺🇸 미국</button>
    </div>
    <div class="ms-card ms-loading">⏳ 시황 요약 생성 중…</div>`;
  holder.querySelector('.ms-market-tabs').addEventListener('click', (e) => {
    const btn = e.target.closest('.ms-tab');
    if (!btn) return;
    const mkt = btn.dataset.msMkt;
    if (mkt === _MS_MARKET) return;
    _MS_MARKET = mkt;
    _loadMarketSummary(container);
  });
  const cardHolder = holder.querySelector('.ms-card');
  try {
    const url = _MS_MARKET === 'us' ? '/api/market_summary/us' : '/api/market_summary';
    const r = await fetch(url);
    const d = await r.json();
    _renderMarketSummary(cardHolder.parentNode, d, cardHolder);
  } catch(e) {
    cardHolder.outerHTML = `<div class="ms-card ms-err">시황 로드 실패: ${_escHtml(e.message)}</div>`;
  }
}

function _renderMarketSummary(holder, data, targetEl) {
  const sections = data.sections || [];
  if (!sections.length) {
    if (targetEl) targetEl.outerHTML = '';
    else holder.innerHTML = '';
    return;
  }
  const colorizeItem = (txt) => {
    // +n.nn% → 상승색(빨강), -n.nn% → 하락색(초록)
    const plus = /[+][0-9]/.test(txt);
    const minus = /[-][0-9]/.test(txt) && !/\b(TOP|[0-9]{1,2})\b/.test(txt.slice(0, 8));
    if (plus && !minus) return 'color:#FF6B6B';
    if (minus && !plus) return 'color:#66CC66';
    return 'color:var(--text-sub)';
  };
  const mktLabel = data.market === 'us' ? '🇺🇸 미국 장마감' : '📊 장마감';
  let html = `<div class="ms-card">
    <div class="ms-header">${mktLabel} ${_escHtml((data.generated_at || '').slice(5, 16))} 시황 요약</div>
    <div class="ms-body">`;
  for (const sec of sections) {
    html += `<div class="ms-section"><div class="ms-section-title">${_escHtml(sec.title || '')}</div>`;
    for (const it of (sec.items || [])) {
      html += `<div class="ms-item" style="${colorizeItem(it)}">${_escHtml(it)}</div>`;
    }
    for (const sub of (sec.subsections || [])) {
      html += `<div class="ms-sub-title">${_escHtml(sub.subtitle || '')}</div>`;
      for (const it of (sub.items || [])) {
        html += `<div class="ms-item" style="${colorizeItem(it)}">${_escHtml(it)}</div>`;
      }
    }
    html += '</div>';
  }
  html += '</div></div>';
  if (targetEl) targetEl.outerHTML = html;
  else holder.innerHTML = html;
}

// ─────────────────────────────────────────────────────────────────────────────
// FLOW PAGE  —  종목별 최근 20 거래일 외국인/기관 순매수 (네이버 금융 크롤)
// ─────────────────────────────────────────────────────────────────────────────
const FLOW_STATE = { code: null, name: null, reqToken: 0 };

function renderFlowPage() {
  const root = document.getElementById('flow-view');
  if (!root.dataset.built) {
    root.dataset.built = '1';
    root.innerHTML = `
      <div class="pg-wrap">
        <div class="pg-title">종목별 수급동향</div>
        <div class="pg-sub">최근 20 거래일 외국인·기관 순매수 · 네이버 금융 · 1시간 캐시</div>

        <div class="cmp-input-group" style="max-width:460px; margin-bottom:16px;">
          <label>종목 선택</label>
          <div class="cmp-input-row">
            <input class="cmp-search-input" type="text" id="flow-search"
                   placeholder="종목명 또는 6자리 코드" autocomplete="off">
            <button class="cmp-clear-btn" id="flow-clear">✕</button>
          </div>
          <div class="cmp-search-dropdown" id="flow-search-dd"></div>
        </div>

        <div id="flow-summary"></div>
        <div id="flow-results"></div>
      </div>`;

    _bindFlowSearch();

    document.getElementById('flow-clear').addEventListener('click', () => {
      FLOW_STATE.code = FLOW_STATE.name = null;
      document.getElementById('flow-search').value = '';
      document.getElementById('flow-summary').innerHTML = '';
      document.getElementById('flow-results').innerHTML =
        '<div class="pg-empty">종목을 검색해 주세요.</div>';
    });
  }

  if (!FLOW_STATE.code) {
    // 테마맵에서 선택된 종목이 있으면 자동으로 띄움
    if (APP.selectedTheme && APP.selectedTheme.stocks && APP.selectedTheme.stocks.length) {
      const first = APP.selectedTheme.stocks[0];
      FLOW_STATE.code = first.code;
      FLOW_STATE.name = first.name;
      document.getElementById('flow-search').value = `${first.name} (${first.code})`;
      _flowFetch();
    } else {
      document.getElementById('flow-results').innerHTML =
        '<div class="pg-empty">종목을 검색해 주세요.</div>';
    }
  } else {
    _flowFetch();
  }
}

function _bindFlowSearch() {
  const inp = document.getElementById('flow-search');
  const dd  = document.getElementById('flow-search-dd');
  let timer;
  inp.addEventListener('input', () => {
    clearTimeout(timer);
    const q = inp.value.trim();
    if (q.length < 2) { dd.innerHTML = ''; dd.style.display = 'none'; return; }
    timer = setTimeout(async () => {
      try {
        const r   = await fetch(`${APP.market === "us" ? "/api/us/search" : "/api/stock_search"}?q=${encodeURIComponent(q)}`);
        const res = await r.json();
        if (!res.length) { dd.innerHTML = ''; dd.style.display = 'none'; return; }
        dd.innerHTML = res.map(it =>
          `<div class="cmp-search-result" data-code="${it.code}" data-name="${it.name}">
            <span class="cmp-search-result-code">${it.code}</span>${it.name}
          </div>`).join('');
        dd.style.display = 'block';
      } catch { dd.innerHTML = ''; dd.style.display = 'none'; }
    }, 280);
  });
  inp.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const first = dd.querySelector('.cmp-search-result');
      if (first) first.click();
      else if (/^\d{6}$/.test(inp.value.trim())) {
        _flowPick(inp.value.trim(), inp.value.trim());
      }
    }
  });
  dd.addEventListener('click', (e) => {
    const row = e.target.closest('.cmp-search-result');
    if (!row) return;
    _flowPick(row.dataset.code, row.dataset.name);
    inp.value = `${row.dataset.name} (${row.dataset.code})`;
    dd.innerHTML = ''; dd.style.display = 'none';
  });
}

function _flowPick(code, name) {
  FLOW_STATE.code = code;
  FLOW_STATE.name = name;
  _flowFetch();
}

async function _flowFetch() {
  const { code, name } = FLOW_STATE;
  if (!code) return;
  const token = ++FLOW_STATE.reqToken;
  const summary = document.getElementById('flow-summary');
  const results = document.getElementById('flow-results');
  summary.innerHTML = '';
  results.innerHTML = '<div class="pg-empty">조회 중…</div>';

  try {
    const r = await fetch(`/api/flow/${code}`);
    const d = await r.json();
    if (token !== FLOW_STATE.reqToken) return;
    if (!r.ok || d.error) throw new Error(d.error || ('HTTP ' + r.status));

    // ── 요약 카드: 20일 누적 ──
    const fmtEok = (v) => {
      const eok = v / 1e8;
      const sign = eok > 0 ? '+' : '';
      return `${sign}${eok.toLocaleString('ko-KR', { maximumFractionDigits: 0 })}억`;
    };
    const col = (v) => v > 0 ? '#FF3333' : v < 0 ? '#33AA33' : 'var(--text-muted)';
    summary.innerHTML = `
      <div class="cmp-cards" style="margin-bottom:14px;">
        <div class="cmp-card">
          <div class="cmp-card-code">${d.code}  ·  20거래일 외국인 순매수</div>
          <div class="cmp-card-name">${d.name}</div>
          <div class="cmp-card-price" style="color:${col(d.foreign_sum_20)}">
            ${fmtEok(d.foreign_sum_20)}
          </div>
        </div>
        <div class="cmp-card">
          <div class="cmp-card-code">20거래일 기관 순매수</div>
          <div class="cmp-card-name">${d.name}</div>
          <div class="cmp-card-price" style="color:${col(d.inst_sum_20)}">
            ${fmtEok(d.inst_sum_20)}
          </div>
        </div>
      </div>
      <div class="cmp-canvas-wrap">
        <canvas id="flow-canvas" width="1152" height="260"></canvas>
      </div>`;

    _drawFlowChart(document.getElementById('flow-canvas'), d);

    // ── 일별 테이블 ──
    const rows = d.dates.map((date, i) => {
      const f = d.foreign_value[i];
      const g = d.inst_value[i];
      const fc = f > 0 ? '#FF3333' : f < 0 ? '#33AA33' : 'var(--text-muted)';
      const gc = g > 0 ? '#FF3333' : g < 0 ? '#33AA33' : 'var(--text-muted)';
      return `<tr>
        <td><span class="rank">${i + 1}</span></td>
        <td>${date}</td>
        <td class="r">${d.close[i].toLocaleString()}</td>
        <td class="r" style="color:${fc};font-weight:600">${fmtEok(f)}</td>
        <td class="r" style="color:${gc};font-weight:600">${fmtEok(g)}</td>
        <td class="r" style="color:var(--text-muted);font-size:11px">${d.foreign_shares[i].toLocaleString()}</td>
        <td class="r" style="color:var(--text-muted);font-size:11px">${d.inst_shares[i].toLocaleString()}</td>
      </tr>`;
    }).reverse().join('');   // 최신이 위로

    results.innerHTML = `
      <table class="pg-table">
        <thead><tr>
          <th style="width:42px">#</th>
          <th>날짜</th>
          <th class="r">종가</th>
          <th class="r">외국인 순매수</th>
          <th class="r">기관 순매수</th>
          <th class="r">외국인 주식</th>
          <th class="r">기관 주식</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  } catch (err) {
    if (token !== FLOW_STATE.reqToken) return;
    results.innerHTML = `<div class="pg-empty">
      <div class="pg-empty-title">데이터 없음</div>
      ${err.message}<br><br>
      <span style="color:var(--text-sub)">네이버 금융 크롤링이 실패했습니다. 잠시 후 다시 시도해 주세요.</span>
    </div>`;
  }
}

function _drawFlowChart(canvas, d) {
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  const PAD = { top: 18, right: 20, bottom: 26, left: 72 };
  const cw = W - PAD.left - PAD.right;
  const ch = H - PAD.top  - PAD.bottom;
  const len = d.dates.length;
  if (!len) return;

  const fVals = d.foreign_value;
  const iVals = d.inst_value;
  const all = fVals.concat(iVals).concat([0]);
  const maxV = Math.max(...all);
  const minV = Math.min(...all);
  const pad = Math.max((maxV - minV) * 0.08, 1);
  const top = maxV + pad, bot = minV - pad;
  const rng = top - bot || 1;

  const slot = cw / len;
  const barW = Math.max(3, slot * 0.35);
  const toY  = v => PAD.top + (1 - (v - bot) / rng) * ch;

  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#1C1C1E';
  ctx.fillRect(0, 0, W, H);

  // Y축 그리드 + 라벨 (억원)
  ctx.strokeStyle = 'rgba(255,255,255,0.06)'; ctx.lineWidth = 1;
  ctx.fillStyle = '#636366'; ctx.font = '11px Noto Sans KR, sans-serif';
  ctx.textAlign = 'right';
  for (let k = 0; k <= 4; k++) {
    const v = bot + (rng * k / 4);
    const y = toY(v);
    ctx.beginPath(); ctx.moveTo(PAD.left, y); ctx.lineTo(W - PAD.right, y); ctx.stroke();
    const eok = v / 1e8;
    ctx.fillText(`${eok >= 0 ? '+' : ''}${eok.toFixed(0)}억`, PAD.left - 6, y + 4);
  }

  // 0 기준선 강조
  const zero = toY(0);
  ctx.strokeStyle = 'rgba(255,255,255,0.25)'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(PAD.left, zero); ctx.lineTo(W - PAD.right, zero); ctx.stroke();

  // X축 날짜 라벨 (앞/중간/끝)
  ctx.fillStyle = '#636366'; ctx.textAlign = 'center';
  for (let k = 0; k < 6; k++) {
    const idx = Math.floor((len - 1) * k / 5);
    const x = PAD.left + slot * (idx + 0.5);
    ctx.fillText(d.dates[idx].slice(5), x, H - 8);
  }

  // 막대 (외국인=빨강/초록, 기관=노랑 계열)
  for (let i = 0; i < len; i++) {
    const cx = PAD.left + slot * (i + 0.5);
    // 외국인 (좌측)
    const fv = fVals[i];
    const fColor = fv >= 0 ? '#FF3333' : '#33AA33';
    ctx.fillStyle = fColor;
    const fY0 = toY(0), fY1 = toY(fv);
    ctx.fillRect(cx - barW, Math.min(fY0, fY1), barW, Math.abs(fY1 - fY0) || 1);
    // 기관 (우측)
    const iv = iVals[i];
    const iColor = iv >= 0 ? '#FF9F0A' : '#5AC8FA';
    ctx.fillStyle = iColor;
    const iY0 = toY(0), iY1 = toY(iv);
    ctx.fillRect(cx, Math.min(iY0, iY1), barW, Math.abs(iY1 - iY0) || 1);
  }

  // 범례 (우상단)
  ctx.font = 'bold 11px Noto Sans KR, sans-serif';
  ctx.textAlign = 'left';
  const lx = W - PAD.right - 180, ly = PAD.top + 4;
  ctx.fillStyle = '#FF3333'; ctx.fillRect(lx,        ly, 10, 10);
  ctx.fillStyle = '#33AA33'; ctx.fillRect(lx + 56,   ly, 10, 10);
  ctx.fillStyle = '#FF9F0A'; ctx.fillRect(lx + 104,  ly, 10, 10);
  ctx.fillStyle = '#5AC8FA'; ctx.fillRect(lx + 154,  ly, 10, 10);
  ctx.fillStyle = '#AEAEB2';
  ctx.fillText('외국인+', lx + 14,  ly + 9);
  ctx.fillText('외국인−', lx + 70,  ly + 9);
  ctx.fillText('기관+',   lx + 118, ly + 9);
  ctx.fillText('기관−',   lx + 168, ly + 9);
}

// ─────────────────────────────────────────────────────────────────────────────
// SHORT-SELLING PAGE (Phase 9)
// ─────────────────────────────────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────
// PORTFOLIO + JOURNAL (Phase 21) — localStorage 기반, 서버 API 불필요
// ─────────────────────────────────────────────────────────────────────────────
const _PF_KEY = 'portfolio';
const _JN_KEY = 'trade_journal';
let _pfModalMarket = 'kr';
let _journalType   = 'buy';

// localStorage 메모리 캐시 — 매번 JSON.parse 하지 않고 변경 시에만 쓰기
let _pfCache = null;
let _jnCache = null;

function _getPortfolio() {
  if (_pfCache) return _pfCache;
  try { _pfCache = JSON.parse(localStorage.getItem(_PF_KEY) || '{"positions":[]}'); }
  catch { _pfCache = { positions: [] }; }
  return _pfCache;
}
function _savePortfolio(d) {
  _pfCache = d;
  try { localStorage.setItem(_PF_KEY, JSON.stringify(d)); }
  catch (e) { console.warn('[portfolio] save failed', e); }
  // 서버 동기화 (트레일링 스톱 체크용)
  try {
    fetch('/api/portfolio/sync', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ positions: d.positions || [] }),
    }).catch(() => {});
  } catch {}
}
function _getJournal() {
  if (_jnCache) return _jnCache;
  try { _jnCache = JSON.parse(localStorage.getItem(_JN_KEY) || '{"entries":[]}'); }
  catch { _jnCache = { entries: [] }; }
  return _jnCache;
}
function _saveJournal(d) {
  _jnCache = d;
  try { localStorage.setItem(_JN_KEY, JSON.stringify(d)); }
  catch (e) { console.warn('[journal] save failed', e); }
}

function _fmtMoney(val, market) {
  if (val == null) return '—';
  const abs = Math.abs(val);
  const sign = val < 0 ? '-' : '';
  if (market === 'us') {
    if (abs >= 1e6) return sign + '$' + (abs / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return sign + '$' + (abs / 1e3).toFixed(1) + 'K';
    return sign + '$' + abs.toFixed(2);
  }
  if (abs >= 1e8) return sign + (abs / 1e8).toFixed(1) + '억';
  if (abs >= 1e4) return sign + (abs / 1e4).toFixed(0) + '만';
  return sign + '₩' + Math.round(abs).toLocaleString();
}

async function _fetchCurrentPrice(code, market) {
  try {
    const base = market === 'us' ? '/api/us/price' : '/api/price';
    const r = await fetch(`${base}/${code}`);
    if (!r.ok) return null;
    const d = await r.json();
    return d.error ? null : (d.price || null);
  } catch { return null; }
}

// ── 포트폴리오 페이지 ──
async function renderPortfolioPage() {
  const root = document.getElementById('portfolio-view');
  const portfolio = _getPortfolio();
  const positions = portfolio.positions || [];

  root.innerHTML = `<div class="pg-wrap">
    <div class="pg-title">💼 포트폴리오</div>
    <div class="pg-sub">localStorage 기반 · 현재가는 서버 시세 캐시에서 자동 매칭</div>
    <div id="pf-content"><div class="pg-empty">현재가 매칭 중…</div></div>
  </div>`;

  // 현재가 병렬 매칭
  const enriched = await Promise.all(positions.map(async (pos) => {
    const cur = await _fetchCurrentPrice(pos.code, pos.market) ?? pos.buy_price;
    const invested = pos.buy_price * pos.quantity;
    const current  = cur * pos.quantity;
    const pnl      = current - invested;
    const pnlPct   = pos.buy_price > 0 ? (cur / pos.buy_price - 1) * 100 : 0;
    return { ...pos, currentPrice: cur, invested, current, pnl, pnlPct };
  }));

  let krInv = 0, krCur = 0, usInv = 0, usCur = 0;
  enriched.forEach(p => {
    if (p.market === 'us') { usInv += p.invested; usCur += p.current; }
    else                   { krInv += p.invested; krCur += p.current; }
  });

  const krPnl    = krCur - krInv;
  const krPnlPct = krInv > 0 ? (krCur / krInv - 1) * 100 : 0;
  const usPnl    = usCur - usInv;
  const usPnlPct = usInv > 0 ? (usCur / usInv - 1) * 100 : 0;

  const summaryHTML = `
    <div class="pf-summary-grid">
      <div class="pf-summary-card">
        <div class="pf-summary-label">🇰🇷 국내</div>
        <div class="pf-summary-value">${_fmtMoney(krCur, 'kr')}</div>
        <div class="pf-summary-pct" style="color:${krPnl >= 0 ? '#FF3333' : '#33AA33'}">
          ${krPnl >= 0 ? '+' : ''}${_fmtMoney(krPnl, 'kr')} (${krPnlPct >= 0 ? '+' : ''}${krPnlPct.toFixed(2)}%)
        </div>
      </div>
      <div class="pf-summary-card">
        <div class="pf-summary-label">🇺🇸 미국</div>
        <div class="pf-summary-value">${_fmtMoney(usCur, 'us')}</div>
        <div class="pf-summary-pct" style="color:${usPnl >= 0 ? '#FF3333' : '#33AA33'}">
          ${usPnl >= 0 ? '+' : ''}${_fmtMoney(usPnl, 'us')} (${usPnlPct >= 0 ? '+' : ''}${usPnlPct.toFixed(2)}%)
        </div>
      </div>
      <div class="pf-summary-card">
        <div class="pf-summary-label">종목 수</div>
        <div class="pf-summary-value">${enriched.length}개</div>
        <div class="pf-summary-sub">
          🇰🇷 ${enriched.filter(p => p.market !== 'us').length} · 🇺🇸 ${enriched.filter(p => p.market === 'us').length}
        </div>
      </div>
    </div>`;

  // 섹터 비중 (시장별 통화 환산 X — 시장별로 분리해 표시)
  const sectorWeights = {};
  enriched.forEach(p => {
    const key = p.sector || (p.market === 'us' ? 'US (기타)' : '국내 (기타)');
    sectorWeights[key] = (sectorWeights[key] || 0) + p.current * (p.market === 'us' ? 1300 : 1);  // 환율 근사
  });
  const totalSector = Object.values(sectorWeights).reduce((s, v) => s + v, 0);
  const colors = ['#FF6B6B','#4488FF','#00CC66','#FFD700','#FF9500','#9B59B6','#1ABC9C','#E74C3C','#3498DB','#2ECC71'];
  const sortedSectors = Object.entries(sectorWeights).sort((a, b) => b[1] - a[1]);

  let sectorHTML = '';
  if (sortedSectors.length > 0 && totalSector > 0) {
    const segments = sortedSectors.map(([s, v], i) => {
      const pct = (v / totalSector * 100).toFixed(1);
      return `<div class="pf-sector-segment" style="width:${pct}%;background:${colors[i % colors.length]}" title="${_escHtml(s)} ${pct}%"></div>`;
    }).join('');
    const legend = sortedSectors.map(([s, v], i) => {
      const pct = (v / totalSector * 100).toFixed(1);
      return `<span class="pf-legend-item"><span class="pf-legend-dot" style="background:${colors[i % colors.length]}"></span>${_escHtml(s)} ${pct}%</span>`;
    }).join('');
    sectorHTML = `
      <div class="pf-section-title">섹터 비중 (KRW 환산 근사 ₩1300/$)</div>
      <div class="pf-sector-bar">${segments}</div>
      <div class="pf-sector-legend">${legend}</div>`;
  }

  // 보유 종목 테이블
  let tableHTML = '';
  if (enriched.length === 0) {
    tableHTML = `<div class="pf-empty">보유 종목이 없습니다.<br>아래 + 버튼을 눌러 종목을 추가하세요.</div>`;
  } else {
    enriched.sort((a, b) => b.current * (b.market === 'us' ? 1300 : 1)
                         - a.current * (a.market === 'us' ? 1300 : 1));
    const rows = enriched.map(p => {
      const flag = p.market === 'us' ? '🇺🇸' : '🇰🇷';
      const cur = p.market === 'us' ? '$' : '₩';
      const pnlCol = p.pnl >= 0 ? '#FF3333' : '#33AA33';
      const sign = p.pnl >= 0 ? '+' : '';
      const pricePrec = p.market === 'us' ? 2 : 0;
      return `<tr class="pf-row" data-code="${_escHtml(p.code)}" data-name="${_escHtml(p.name)}" data-market="${p.market}">
        <td class="pf-flag-cell">${flag}</td>
        <td>
          <div class="pf-stock-name">${_escHtml(p.name)}${_marketBadgeFromItem(p)}</div>
          <div class="pf-stock-code">${_escHtml(p.code)}</div>
        </td>
        <td class="r">${cur}${p.buy_price.toLocaleString(undefined,{minimumFractionDigits:pricePrec,maximumFractionDigits:pricePrec})}</td>
        <td class="r">${cur}${p.currentPrice.toLocaleString(undefined,{minimumFractionDigits:pricePrec,maximumFractionDigits:pricePrec})}</td>
        <td class="r">${p.quantity.toLocaleString()}</td>
        <td class="r">${_fmtMoney(p.invested, p.market)}</td>
        <td class="r">${_fmtMoney(p.current, p.market)}</td>
        <td class="r" style="color:${pnlCol}">${sign}${_fmtMoney(p.pnl, p.market)}</td>
        <td class="r" style="color:${pnlCol};font-weight:700">${sign}${p.pnlPct.toFixed(2)}%</td>
        <td class="r">${(() => {
          const tr = p.trailing || {};
          if (!tr.enabled) return '<span class="trail-off">—</span>';
          const stopStr = tr.current_stop ? `${cur}${Number(tr.current_stop).toLocaleString(undefined,{minimumFractionDigits:pricePrec,maximumFractionDigits:pricePrec})}` : '계산중';
          return `<span class="trail-on">${stopStr}</span>`;
        })()}</td>
        <td class="pf-memo-cell" title="${_escHtml(p.memo || '')}">${_escHtml(p.memo || '')}</td>
        <td class="pf-action-cell">
          <button class="pf-edit-btn" data-action="edit" data-id="${_escHtml(p.id)}">✏️</button>
          <button class="pf-edit-btn" data-action="trailing" data-id="${_escHtml(p.id)}" title="트레일링 스톱">⚙</button>
          <button class="pf-delete-btn" data-action="delete" data-id="${_escHtml(p.id)}">🗑️</button>
        </td>
      </tr>`;
    }).join('');
    tableHTML = `<table class="pf-table">
      <thead><tr>
        <th></th><th>종목</th>
        <th class="r">매수가</th><th class="r">현재가</th><th class="r">수량</th>
        <th class="r">손익</th><th class="r">수익률</th>
        <th class="r">🎯 손절가</th>
        <th>메모</th><th></th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  }

  document.getElementById('pf-content').innerHTML = `
    ${summaryHTML}
    ${sectorHTML}
    <div id="pf-position-sizer-mount"></div>
    <div class="pf-actions">
      <button class="pf-add-btn" id="pf-add-btn">+ 종목 추가</button>
    </div>
    <div class="pf-section-title">보유 종목</div>
    ${tableHTML}`;

  // 포지션 사이즈 계산기 마운트 (trading.js 로드 확인)
  const sizerMount = document.getElementById('pf-position-sizer-mount');
  if (sizerMount && typeof renderPositionSizer === 'function') {
    renderPositionSizer(sizerMount);
  }

  document.getElementById('pf-add-btn').addEventListener('click', () => _showPfModal());
  const tbody = document.querySelector('#pf-content tbody');
  if (tbody) {
    tbody.addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-action]');
      if (btn) {
        e.stopPropagation();
        const id = btn.dataset.id;
        if (btn.dataset.action === 'edit')     _showPfModal(id);
        if (btn.dataset.action === 'delete')   _deletePosition(id);
        if (btn.dataset.action === 'trailing') _showTrailingModal(id);
        return;
      }
      const tr = e.target.closest('tr.pf-row');
      if (tr) openChartPanel(tr.dataset.code, tr.dataset.name, tr.dataset.market);
    });
  }
}

// ── 포트폴리오 모달 ──
function _showPfModal(editId) {
  const existing = editId ? (_getPortfolio().positions || []).find(p => p.id === editId) : null;
  _pfModalMarket = existing?.market || 'kr';
  const today = new Date().toISOString().slice(0, 10);

  const html = `
    <div class="pf-modal-overlay" id="pf-modal-overlay">
      <div class="pf-modal">
        <div class="pf-modal-title">${existing ? '종목 수정' : '종목 추가'}</div>
        <div class="pf-modal-row">
          <label>시장</label>
          <div class="pf-market-toggle" id="pf-modal-market">
            <button class="pf-market-btn ${_pfModalMarket === 'kr' ? 'active' : ''}" data-market="kr">🇰🇷 국내</button>
            <button class="pf-market-btn ${_pfModalMarket === 'us' ? 'active' : ''}" data-market="us">🇺🇸 미국</button>
          </div>
        </div>
        <div class="pf-modal-row">
          <label>종목 검색</label>
          <input type="text" id="pf-search" class="pf-input" placeholder="종목명 또는 코드 입력"
                 value="${_escHtml(existing?.name || '')}" autocomplete="off">
          <div id="pf-search-results" class="pf-search-results"></div>
          <input type="hidden" id="pf-code" value="${_escHtml(existing?.code || '')}">
          <input type="hidden" id="pf-name" value="${_escHtml(existing?.name || '')}">
        </div>
        <div class="pf-modal-row">
          <label>매수가</label>
          <input type="number" id="pf-buy-price" class="pf-input" step="any" placeholder="매수 단가" value="${existing?.buy_price ?? ''}">
        </div>
        <div class="pf-modal-row">
          <label>수량</label>
          <input type="number" id="pf-quantity" class="pf-input" placeholder="보유 수량" value="${existing?.quantity ?? ''}">
        </div>
        <div class="pf-modal-row">
          <label>매수일</label>
          <input type="date" id="pf-buy-date" class="pf-input" value="${existing?.buy_date || today}">
        </div>
        <div class="pf-modal-row">
          <label>메모</label>
          <input type="text" id="pf-memo" class="pf-input" placeholder="투자 근거 한 줄" value="${_escHtml(existing?.memo || '')}">
        </div>
        <div class="pf-modal-actions">
          <button class="pf-cancel-btn" id="pf-cancel">취소</button>
          <button class="pf-save-btn" id="pf-save">${existing ? '수정' : '추가'}</button>
        </div>
      </div>
    </div>`;
  document.body.insertAdjacentHTML('beforeend', html);

  const overlay = document.getElementById('pf-modal-overlay');
  overlay.addEventListener('click', (e) => { if (e.target === overlay) _closePfModal(); });
  document.getElementById('pf-cancel').addEventListener('click', _closePfModal);
  document.getElementById('pf-save').addEventListener('click', () => _savePosition(editId));

  document.getElementById('pf-modal-market').addEventListener('click', (e) => {
    const b = e.target.closest('.pf-market-btn');
    if (!b) return;
    _pfModalMarket = b.dataset.market;
    document.querySelectorAll('#pf-modal-market .pf-market-btn').forEach(x =>
      x.classList.toggle('active', x.dataset.market === _pfModalMarket));
    document.getElementById('pf-search').value = '';
    document.getElementById('pf-code').value = '';
    document.getElementById('pf-name').value = '';
    document.getElementById('pf-search-results').innerHTML = '';
  });

  const searchEl = document.getElementById('pf-search');
  let _t;
  searchEl.addEventListener('input', () => {
    clearTimeout(_t);
    const q = searchEl.value.trim();
    if (q.length < 1) {
      document.getElementById('pf-search-results').innerHTML = '';
      return;
    }
    _t = setTimeout(async () => {
      try {
        const url = _pfModalMarket === 'us' ? `/api/us/search?q=${encodeURIComponent(q)}`
                                            : `/api/stock_search?q=${encodeURIComponent(q)}`;
        const r = await fetch(url);
        const arr = await r.json();
        const items = (Array.isArray(arr) ? arr : []).slice(0, 8);
        const dd = document.getElementById('pf-search-results');
        dd.innerHTML = items.map(it => {
          const code = _escHtml(it.code || it.symbol || '');
          const name = _escHtml(it.name || '');
          return `<div class="pf-search-item" data-code="${code}" data-name="${name}">${name} (${code})</div>`;
        }).join('');
      } catch {}
    }, 200);
  });
  document.getElementById('pf-search-results').addEventListener('click', (e) => {
    const it = e.target.closest('.pf-search-item');
    if (!it) return;
    document.getElementById('pf-search').value = it.dataset.name;
    document.getElementById('pf-code').value = it.dataset.code;
    document.getElementById('pf-name').value = it.dataset.name;
    document.getElementById('pf-search-results').innerHTML = '';
  });
}

function _closePfModal() {
  const m = document.getElementById('pf-modal-overlay');
  if (m) m.remove();
}

function _savePosition(editId) {
  const code = document.getElementById('pf-code').value;
  const name = document.getElementById('pf-name').value;
  const buyPrice = parseFloat(document.getElementById('pf-buy-price').value);
  const quantity = parseInt(document.getElementById('pf-quantity').value);
  const buyDate  = document.getElementById('pf-buy-date').value;
  const memo     = document.getElementById('pf-memo').value;

  if (!code || !buyPrice || !quantity) {
    alert('종목, 매수가, 수량을 모두 입력해주세요.');
    return;
  }
  const pf = _getPortfolio();
  if (editId) {
    const idx = pf.positions.findIndex(p => p.id === editId);
    if (idx >= 0) pf.positions[idx] = {
      ...pf.positions[idx], code, name, market: _pfModalMarket,
      buy_price: buyPrice, quantity, buy_date: buyDate, memo,
    };
  } else {
    pf.positions.push({
      id: 'pos_' + Date.now(),
      code, name, market: _pfModalMarket,
      buy_price: buyPrice, quantity, buy_date: buyDate, memo,
    });
  }
  _savePortfolio(pf);
  _closePfModal();
  renderPortfolioPage();
}

function _deletePosition(id) {
  if (!confirm('이 종목을 포트폴리오에서 삭제하시겠습니까?')) return;
  const pf = _getPortfolio();
  pf.positions = (pf.positions || []).filter(p => p.id !== id);
  _savePortfolio(pf);
  renderPortfolioPage();
}

// ── 매매일지 페이지 ──
let _journalTab = 'timeline';

function renderJournalPage() {
  const root = document.getElementById('journal-view');
  root.innerHTML = `<div class="pg-wrap">
    <div class="pg-title">📝 매매일지</div>
    <div class="jn-tabs" id="jn-tabs">
      <button class="jn-tab ${_journalTab === 'timeline' ? 'active' : ''}" data-tab="timeline">타임라인</button>
      <button class="jn-tab ${_journalTab === 'review' ? 'active' : ''}" data-tab="review">📊 복기 분석</button>
    </div>
    <div id="jn-tab-content"></div>
  </div>`;
  document.getElementById('jn-tabs').addEventListener('click', (e) => {
    const btn = e.target.closest('.jn-tab');
    if (!btn) return;
    _journalTab = btn.dataset.tab;
    document.querySelectorAll('#jn-tabs .jn-tab').forEach(b => b.classList.toggle('active', b.dataset.tab === _journalTab));
    if (_journalTab === 'review') _renderJournalReview();
    else _renderJournalTimeline();
  });
  if (_journalTab === 'review') _renderJournalReview();
  else _renderJournalTimeline();
}

function _renderJournalTimeline() {
  const root = document.getElementById('jn-tab-content');
  const j = _getJournal();
  const entries = j.entries || [];

  const buyN  = entries.filter(e => e.type === 'buy').length;
  const sellN = entries.filter(e => e.type === 'sell').length;
  const buyAmt = entries.filter(e => e.type === 'buy')
                        .reduce((s, e) => s + (e.market === 'us' ? (e.total || 0) * 1300 : (e.total || 0)), 0);
  const sellAmt = entries.filter(e => e.type === 'sell')
                         .reduce((s, e) => s + (e.market === 'us' ? (e.total || 0) * 1300 : (e.total || 0)), 0);

  // 월별 그룹핑
  const monthly = {};
  entries.forEach(e => {
    const m = (e.date || '').slice(0, 7) || '미정';
    (monthly[m] = monthly[m] || []).push(e);
  });
  const months = Object.keys(monthly).sort().reverse();

  const summaryHTML = `
    <div class="journal-summary">
      <div class="journal-stat">
        <div class="journal-stat-label">총 매매</div>
        <div class="journal-stat-value">${entries.length}회</div>
      </div>
      <div class="journal-stat">
        <div class="journal-stat-label">매수</div>
        <div class="journal-stat-value" style="color:#FF3333">${buyN}회</div>
      </div>
      <div class="journal-stat">
        <div class="journal-stat-label">매도</div>
        <div class="journal-stat-value" style="color:#4488FF">${sellN}회</div>
      </div>
      <div class="journal-stat">
        <div class="journal-stat-label">총 매수금 (KRW환산)</div>
        <div class="journal-stat-value">${_fmtMoney(buyAmt, 'kr')}</div>
      </div>
      <div class="journal-stat">
        <div class="journal-stat-label">총 매도금 (KRW환산)</div>
        <div class="journal-stat-value">${_fmtMoney(sellAmt, 'kr')}</div>
      </div>
    </div>`;

  let timelineHTML = '';
  if (entries.length === 0) {
    timelineHTML = `<div class="pf-empty">매매 기록이 없습니다.<br>+ 버튼을 눌러 매매를 기록하세요.</div>`;
  } else {
    months.forEach(month => {
      const list = monthly[month].slice().sort((a, b) => (b.date || '').localeCompare(a.date || ''));
      const itemsHTML = list.map(e => {
        const isBuy = e.type === 'buy';
        const typeLabel = isBuy ? '매수' : '매도';
        const typeCol = isBuy ? '#FF3333' : '#4488FF';
        const flag = e.market === 'us' ? '🇺🇸' : '🇰🇷';
        const cur = e.market === 'us' ? '$' : '₩';
        const tagsHTML = (e.tags && e.tags.length)
          ? `<div class="journal-tags">${e.tags.map(t => `<span class="journal-tag">#${_escHtml(t)}</span>`).join('')}</div>` : '';
        return `<div class="journal-entry" data-code="${_escHtml(e.code)}" data-name="${_escHtml(e.name)}" data-market="${e.market || 'kr'}">
          <div class="journal-date-col">
            <div class="journal-date">${_escHtml((e.date || '').slice(5))}</div>
            <div class="journal-type" style="color:${typeCol}">${typeLabel}</div>
          </div>
          <div class="journal-main">
            <div class="journal-stock">
              ${flag} <span class="journal-stock-name">${_escHtml(e.name || '')}</span>
              <span class="journal-stock-code">${_escHtml(e.code || '')}</span>
            </div>
            <div class="journal-details">
              ${cur}${(e.price || 0).toLocaleString()} × ${(e.quantity || 0).toLocaleString()}
              = <b>${cur}${(e.total || 0).toLocaleString()}</b>
            </div>
            ${e.memo ? `<div class="journal-memo">${_escHtml(e.memo)}</div>` : ''}
            ${tagsHTML}
          </div>
          <div class="journal-actions-col">
            <button class="pf-edit-btn" data-action="edit-j" data-id="${_escHtml(e.id)}">✏️</button>
            <button class="pf-delete-btn" data-action="delete-j" data-id="${_escHtml(e.id)}">🗑️</button>
          </div>
        </div>`;
      }).join('');
      timelineHTML += `<div class="journal-month-group">
        <div class="journal-month-header">${_escHtml(month)}</div>
        ${itemsHTML}
      </div>`;
    });
  }

  root.innerHTML = `
    ${summaryHTML}
    <div class="journal-actions">
      <button class="journal-add-btn" id="j-add-btn">+ 매매 기록</button>
    </div>
    <div id="j-timeline">${timelineHTML}</div>`;

  document.getElementById('j-add-btn').addEventListener('click', () => _showJournalModal());
  document.getElementById('j-timeline').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-action]');
    if (btn) {
      e.stopPropagation();
      const id = btn.dataset.id;
      if (btn.dataset.action === 'edit-j')   _showJournalModal(id);
      if (btn.dataset.action === 'delete-j') _deleteJournal(id);
      return;
    }
    const card = e.target.closest('.journal-entry');
    if (card) openChartPanel(card.dataset.code, card.dataset.name, card.dataset.market);
  });
}

function _showJournalModal(editId) {
  const existing = editId ? (_getJournal().entries || []).find(x => x.id === editId) : null;
  _pfModalMarket = existing?.market || 'kr';
  _journalType   = existing?.type   || 'buy';
  const today = new Date().toISOString().slice(0, 10);

  const html = `
    <div class="pf-modal-overlay" id="j-modal-overlay">
      <div class="pf-modal">
        <div class="pf-modal-title">${existing ? '매매 수정' : '매매 기록'}</div>
        <div class="pf-modal-row">
          <label>유형</label>
          <div class="pf-market-toggle" id="j-type-toggle">
            <button class="journal-type-btn ${_journalType === 'buy' ? 'active buy' : ''}" data-type="buy">매수</button>
            <button class="journal-type-btn ${_journalType === 'sell' ? 'active sell' : ''}" data-type="sell">매도</button>
          </div>
        </div>
        <div class="pf-modal-row">
          <label>시장</label>
          <div class="pf-market-toggle" id="j-market-toggle">
            <button class="pf-market-btn ${_pfModalMarket === 'kr' ? 'active' : ''}" data-market="kr">🇰🇷 국내</button>
            <button class="pf-market-btn ${_pfModalMarket === 'us' ? 'active' : ''}" data-market="us">🇺🇸 미국</button>
          </div>
        </div>
        <div class="pf-modal-row">
          <label>종목</label>
          <input type="text" id="j-search" class="pf-input" placeholder="종목명 또는 코드"
                 value="${_escHtml(existing?.name || '')}" autocomplete="off">
          <div id="j-search-results" class="pf-search-results"></div>
          <input type="hidden" id="j-code" value="${_escHtml(existing?.code || '')}">
          <input type="hidden" id="j-name" value="${_escHtml(existing?.name || '')}">
        </div>
        <div class="pf-modal-row">
          <label>단가</label>
          <input type="number" id="j-price" class="pf-input" step="any" value="${existing?.price ?? ''}" placeholder="체결 단가">
        </div>
        <div class="pf-modal-row">
          <label>수량</label>
          <input type="number" id="j-quantity" class="pf-input" value="${existing?.quantity ?? ''}" placeholder="체결 수량">
        </div>
        <div class="pf-modal-row">
          <label>총액</label>
          <div id="j-total" class="pf-total-display">—</div>
        </div>
        <div class="pf-modal-row">
          <label>날짜</label>
          <input type="date" id="j-date" class="pf-input" value="${existing?.date || today}">
        </div>
        <div class="pf-modal-row">
          <label>메모</label>
          <input type="text" id="j-memo" class="pf-input" placeholder="매매 근거 한 줄" value="${_escHtml(existing?.memo || '')}">
        </div>
        <div class="pf-modal-row">
          <label>태그 (쉼표 구분)</label>
          <input type="text" id="j-tags" class="pf-input" placeholder="예: 분할매수, AI, 실적"
                 value="${_escHtml((existing?.tags || []).join(', '))}">
        </div>
        <div class="pf-modal-actions">
          <button class="pf-cancel-btn" id="j-cancel">취소</button>
          <button class="pf-save-btn" id="j-save">${existing ? '수정' : '기록'}</button>
        </div>
      </div>
    </div>`;
  document.body.insertAdjacentHTML('beforeend', html);

  const overlay = document.getElementById('j-modal-overlay');
  overlay.addEventListener('click', (e) => { if (e.target === overlay) _closeJournalModal(); });
  document.getElementById('j-cancel').addEventListener('click', _closeJournalModal);
  document.getElementById('j-save').addEventListener('click', () => _saveJournal2(editId));

  document.getElementById('j-type-toggle').addEventListener('click', (e) => {
    const b = e.target.closest('.journal-type-btn');
    if (!b) return;
    _journalType = b.dataset.type;
    document.querySelectorAll('#j-type-toggle .journal-type-btn').forEach(x => {
      x.classList.remove('active', 'buy', 'sell');
      if (x.dataset.type === _journalType) x.classList.add('active', _journalType);
    });
  });
  document.getElementById('j-market-toggle').addEventListener('click', (e) => {
    const b = e.target.closest('.pf-market-btn');
    if (!b) return;
    _pfModalMarket = b.dataset.market;
    document.querySelectorAll('#j-market-toggle .pf-market-btn').forEach(x =>
      x.classList.toggle('active', x.dataset.market === _pfModalMarket));
    document.getElementById('j-search').value = '';
    document.getElementById('j-code').value = '';
    document.getElementById('j-name').value = '';
    document.getElementById('j-search-results').innerHTML = '';
    _calcJTotal();
  });

  const _calcJTotal = () => {
    const p = parseFloat(document.getElementById('j-price').value) || 0;
    const q = parseInt(document.getElementById('j-quantity').value) || 0;
    const total = p * q;
    const cur = _pfModalMarket === 'us' ? '$' : '₩';
    document.getElementById('j-total').textContent = total > 0 ? `${cur}${total.toLocaleString()}` : '—';
  };
  document.getElementById('j-price').addEventListener('input', _calcJTotal);
  document.getElementById('j-quantity').addEventListener('input', _calcJTotal);
  if (existing) _calcJTotal();

  const searchEl = document.getElementById('j-search');
  let _t;
  searchEl.addEventListener('input', () => {
    clearTimeout(_t);
    const q = searchEl.value.trim();
    if (q.length < 1) {
      document.getElementById('j-search-results').innerHTML = '';
      return;
    }
    _t = setTimeout(async () => {
      try {
        const url = _pfModalMarket === 'us' ? `/api/us/search?q=${encodeURIComponent(q)}`
                                            : `/api/stock_search?q=${encodeURIComponent(q)}`;
        const r = await fetch(url);
        const arr = await r.json();
        const items = (Array.isArray(arr) ? arr : []).slice(0, 8);
        const dd = document.getElementById('j-search-results');
        dd.innerHTML = items.map(it => {
          const code = _escHtml(it.code || it.symbol || '');
          const name = _escHtml(it.name || '');
          return `<div class="pf-search-item" data-code="${code}" data-name="${name}">${name} (${code})</div>`;
        }).join('');
      } catch {}
    }, 200);
  });
  document.getElementById('j-search-results').addEventListener('click', (e) => {
    const it = e.target.closest('.pf-search-item');
    if (!it) return;
    document.getElementById('j-search').value = it.dataset.name;
    document.getElementById('j-code').value = it.dataset.code;
    document.getElementById('j-name').value = it.dataset.name;
    document.getElementById('j-search-results').innerHTML = '';
  });
}

function _closeJournalModal() {
  const m = document.getElementById('j-modal-overlay');
  if (m) m.remove();
}

function _saveJournal2(editId) {
  const code = document.getElementById('j-code').value;
  const name = document.getElementById('j-name').value;
  const price = parseFloat(document.getElementById('j-price').value);
  const quantity = parseInt(document.getElementById('j-quantity').value);
  const date = document.getElementById('j-date').value;
  const memo = document.getElementById('j-memo').value;
  const tagsStr = document.getElementById('j-tags').value;
  const tags = tagsStr.split(',').map(t => t.trim()).filter(Boolean);

  if (!code || !price || !quantity) {
    alert('종목, 단가, 수량을 모두 입력해주세요.');
    return;
  }
  const j = _getJournal();
  const entry = {
    id: editId || ('tr_' + Date.now()),
    date, code, name, market: _pfModalMarket,
    type: _journalType,
    price, quantity, total: price * quantity,
    memo, tags,
  };
  if (editId) {
    const idx = j.entries.findIndex(e => e.id === editId);
    if (idx >= 0) j.entries[idx] = entry;
  } else {
    j.entries.push(entry);
  }
  _saveJournal(j);
  _closeJournalModal();
  renderJournalPage();
}

function _deleteJournal(id) {
  if (!confirm('이 매매 기록을 삭제하시겠습니까?')) return;
  const j = _getJournal();
  j.entries = (j.entries || []).filter(e => e.id !== id);
  _saveJournal(j);
  renderJournalPage();
}

// ── 매매 복기 시스템 ──
function _pairBuySell(entries) {
  const sorted = [...entries].sort((a, b) => (a.date || '').localeCompare(b.date || ''));
  const queues = {};   // code → [{...entry, rem: qty}]
  const pairs = [];
  for (const e of sorted) {
    const code = e.code;
    if (!queues[code]) queues[code] = [];
    if (e.type === 'buy') {
      queues[code].push({ ...e, rem: e.quantity || 0 });
    } else if (e.type === 'sell') {
      let rem = e.quantity || 0;
      while (rem > 0 && queues[code].length > 0) {
        const buy = queues[code][0];
        const matched = Math.min(buy.rem, rem);
        const pnl = (e.price - buy.price) * matched;
        const pnlPct = buy.price ? ((e.price / buy.price - 1) * 100) : 0;
        const d1 = new Date(buy.date), d2 = new Date(e.date);
        const holdDays = Math.round((d2 - d1) / 86400000);
        pairs.push({
          id: `p_${buy.id}_${e.id}`,
          code, name: e.name, market: e.market || 'kr',
          buy_id: buy.id, sell_id: e.id,
          buy_date: buy.date, sell_date: e.date,
          buy_price: buy.price, sell_price: e.price,
          quantity: matched, pnl: Math.round(pnl),
          pnl_pct: Math.round(pnlPct * 100) / 100,
          hold_days: holdDays,
          buy_memo: buy.memo, sell_memo: e.memo,
        });
        buy.rem -= matched;
        rem -= matched;
        if (buy.rem <= 0) queues[code].shift();
      }
    }
  }
  return pairs;
}

function _renderJournalReview() {
  const root = document.getElementById('jn-tab-content');
  const j = _getJournal();
  const pairs = _pairBuySell(j.entries || []);

  if (!pairs.length) {
    root.innerHTML = '<div class="pg-empty">완료된 매매(매수+매도)가 없습니다.<br>매수 후 매도를 기록하면 자동으로 FIFO 페어링됩니다.</div>';
    return;
  }

  const wins = pairs.filter(p => p.pnl > 0);
  const losses = pairs.filter(p => p.pnl < 0);
  const winRate = (wins.length / pairs.length * 100).toFixed(1);
  const totalPnl = pairs.reduce((s, p) => s + p.pnl, 0);
  const avgWin = wins.length ? wins.reduce((s, p) => s + p.pnl, 0) / wins.length : 0;
  const avgLoss = losses.length ? Math.abs(losses.reduce((s, p) => s + p.pnl, 0) / losses.length) : 0;
  const pf = avgLoss > 0 ? (avgWin / avgLoss) : 0;
  const avgWinPct = wins.length ? (wins.reduce((s, p) => s + p.pnl_pct, 0) / wins.length) : 0;
  const avgLossPct = losses.length ? (losses.reduce((s, p) => s + p.pnl_pct, 0) / losses.length) : 0;
  const avgHold = pairs.reduce((s, p) => s + (p.hold_days || 0), 0) / pairs.length;

  // 통계 카드
  let html = `
    <div class="rev-stat-grid">
      <div class="rev-stat"><div class="rev-lbl">총 거래</div><div class="rev-val">${pairs.length}회</div>
        <div class="rev-sub2">승${wins.length} 패${losses.length}</div></div>
      <div class="rev-stat hl"><div class="rev-lbl">승률</div>
        <div class="rev-val" style="color:${winRate >= 50 ? '#FF3333' : '#33AA33'}">${winRate}%</div></div>
      <div class="rev-stat"><div class="rev-lbl">총 손익</div>
        <div class="rev-val" style="color:${totalPnl >= 0 ? '#FF3333' : '#33AA33'}">${totalPnl >= 0 ? '+' : ''}${_fmtMoney(totalPnl, 'kr')}</div></div>
      <div class="rev-stat hl"><div class="rev-lbl">손익비</div>
        <div class="rev-val" style="color:${pf >= 1.5 ? '#FFD700' : '#FF6B6B'}">${pf.toFixed(2)}</div>
        <div class="rev-sub2">목표 ≥ 1.5</div></div>
      <div class="rev-stat"><div class="rev-lbl">평균 수익</div>
        <div class="rev-val" style="color:#FF3333">+${avgWinPct.toFixed(2)}%</div></div>
      <div class="rev-stat"><div class="rev-lbl">평균 손실</div>
        <div class="rev-val" style="color:#33AA33">${avgLossPct.toFixed(2)}%</div></div>
      <div class="rev-stat"><div class="rev-lbl">평균 보유</div>
        <div class="rev-val">${avgHold.toFixed(1)}일</div></div>
    </div>`;

  // 페어 리스트
  html += '<div style="color:var(--text);font-size:13px;font-weight:700;margin:14px 0 8px">🔍 매매 페어 (클릭 → 복기)</div>';
  html += '<div class="rev-pair-list" id="rev-pairs">';
  [...pairs].reverse().forEach(p => {
    const col = p.pnl >= 0 ? '#FF3333' : '#33AA33';
    const sign = p.pnl >= 0 ? '+' : '';
    const cur = p.market === 'us' ? '$' : '₩';
    html += `<div class="rev-pair" data-pid="${_escHtml(p.id)}">
      <div class="rev-pair-head">
        <b>${_escHtml(p.name)}</b> <span class="rev-pair-code">${_escHtml(p.code)}</span>
      </div>
      <div class="rev-pair-period">${_escHtml(p.buy_date||'')} → ${_escHtml(p.sell_date||'')} · ${p.hold_days}일</div>
      <div class="rev-pair-prices">${cur}${p.buy_price?.toLocaleString()} → ${cur}${p.sell_price?.toLocaleString()} × ${p.quantity}</div>
      <div class="rev-pair-pnl" style="color:${col}">${sign}${_fmtMoney(p.pnl, p.market)} (${sign}${p.pnl_pct}%)</div>
    </div>`;
  });
  html += '</div>';

  // 교훈 태그 클라우드
  const lessonCounts = {};
  (j.entries || []).forEach(e => {
    ((e.review || {}).lessons || []).forEach(t => lessonCounts[t] = (lessonCounts[t] || 0) + 1);
  });
  if (Object.keys(lessonCounts).length) {
    html += '<div style="color:#FFD700;font-size:13px;font-weight:700;margin:14px 0 8px">🏷 교훈 태그</div>';
    html += '<div class="rev-lesson-cloud">';
    Object.entries(lessonCounts).sort((a, b) => b[1] - a[1]).forEach(([tag, cnt]) => {
      const neg = ['과신매수','뇌동매매','손절못함','너무일찍팔음','너무오래보유','타이밍부족'].includes(tag);
      html += `<span class="rev-lesson-item" style="color:${neg ? '#FF6B6B' : '#FFD700'}">${_escHtml(tag)} <small>${cnt}</small></span>`;
    });
    html += '</div>';
  }

  root.innerHTML = html;

  // 페어 캐시 + 클릭 이벤트
  window._reviewPairs = {};
  pairs.forEach(p => window._reviewPairs[p.id] = p);
  document.getElementById('rev-pairs').addEventListener('click', (e) => {
    const el = e.target.closest('.rev-pair');
    if (!el) return;
    _showReviewModal(el.dataset.pid);
  });
}

function _showReviewModal(pairId) {
  const p = (window._reviewPairs || {})[pairId];
  if (!p) return;
  const j = _getJournal();
  const buyEntry = j.entries.find(e => e.id === p.buy_id);
  const rv = buyEntry?.review || {};
  const col = p.pnl >= 0 ? '#FF3333' : '#33AA33';
  const sign = p.pnl >= 0 ? '+' : '';
  const cur = p.market === 'us' ? '$' : '₩';

  const lessonSuggest = ['익절성공','손절성공','수익보존','분할매수효과','과신매수','뇌동매매','손절못함','너무일찍팔음','너무오래보유','타이밍부족']
    .map(t => `<span class="rev-ls-chip" data-tag="${_escHtml(t)}">${_escHtml(t)}</span>`).join('');

  const html = `
    <div class="pf-modal-overlay" id="rev-modal-ov">
      <div class="pf-modal" style="max-width:580px">
        <div class="pf-modal-title">🔍 매매 복기</div>
        <div class="rev-summary">
          <b style="color:#FFD700">${_escHtml(p.name)}</b> (${_escHtml(p.code)}) · ${p.hold_days}일 보유<br>
          ${cur}${p.buy_price?.toLocaleString()} → ${cur}${p.sell_price?.toLocaleString()} × ${p.quantity}<br>
          <span style="color:${col};font-size:16px;font-weight:700">${sign}${_fmtMoney(p.pnl, p.market)} (${sign}${p.pnl_pct}%)</span>
        </div>
        <div class="pf-modal-row"><label>매수 메모</label><div class="rev-ro">${_escHtml(p.buy_memo || '없음')}</div></div>
        <div class="pf-modal-row"><label>매도 메모</label><div class="rev-ro">${_escHtml(p.sell_memo || '없음')}</div></div>
        <div class="pf-modal-row"><label>목표가 도달?</label>
          <select id="rv-target" class="pf-input"><option value="true" ${rv.target_reached ? 'selected' : ''}>예</option><option value="false" ${rv.target_reached === false ? 'selected' : ''}>아니오</option></select></div>
        <div class="pf-modal-row"><label>손절 발동?</label>
          <select id="rv-stop" class="pf-input"><option value="false" ${!rv.stop_triggered ? 'selected' : ''}>아니오</option><option value="true" ${rv.stop_triggered ? 'selected' : ''}>예</option></select></div>
        <div class="pf-modal-row"><label>후회도 (1=만족 ~ 5=후회)</label>
          <input type="range" id="rv-regret" min="1" max="5" value="${rv.regret_score || 3}" style="flex:1">
          <span id="rv-regret-disp" style="color:var(--text);width:20px;text-align:center">${rv.regret_score || 3}</span></div>
        <div class="pf-modal-row"><label>교훈 태그 (콤마 구분)</label>
          <input type="text" id="rv-lessons" class="pf-input" value="${_escHtml((rv.lessons || []).join(', '))}" placeholder="익절성공, 뇌동매매 등">
          <div class="rev-suggest" id="rv-suggest">${lessonSuggest}</div></div>
        <div class="pf-modal-row"><label>복기 메모</label>
          <textarea id="rv-memo" class="pf-input" rows="2" placeholder="이 거래의 핵심 교훈">${_escHtml(rv.review_memo || '')}</textarea></div>
        <div class="pf-modal-actions">
          <button class="pf-cancel-btn" id="rv-cancel">취소</button>
          <button class="pf-save-btn" id="rv-save">저장</button>
        </div>
      </div>
    </div>`;
  document.body.insertAdjacentHTML('beforeend', html);

  const overlay = document.getElementById('rev-modal-ov');
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
  document.getElementById('rv-cancel').addEventListener('click', () => overlay.remove());
  document.getElementById('rv-regret').addEventListener('input', (e) => {
    document.getElementById('rv-regret-disp').textContent = e.target.value;
  });
  document.getElementById('rv-suggest').addEventListener('click', (e) => {
    const chip = e.target.closest('.rev-ls-chip');
    if (!chip) return;
    const input = document.getElementById('rv-lessons');
    const cur = input.value.split(',').map(s => s.trim()).filter(Boolean);
    const tag = chip.dataset.tag;
    if (!cur.includes(tag)) { cur.push(tag); input.value = cur.join(', '); }
  });
  document.getElementById('rv-save').addEventListener('click', () => {
    const j2 = _getJournal();
    const idx = j2.entries.findIndex(e => e.id === p.buy_id);
    if (idx >= 0) {
      const lessonsRaw = document.getElementById('rv-lessons').value;
      j2.entries[idx].review = {
        matched_pair_id: p.id,
        actual_outcome: p.pnl > 0 ? 'win' : p.pnl < 0 ? 'loss' : 'breakeven',
        realized_pnl: p.pnl, realized_pnl_pct: p.pnl_pct, hold_days: p.hold_days,
        target_reached: document.getElementById('rv-target').value === 'true',
        stop_triggered: document.getElementById('rv-stop').value === 'true',
        regret_score: parseInt(document.getElementById('rv-regret').value),
        lessons: lessonsRaw.split(',').map(s => s.trim()).filter(Boolean),
        review_memo: document.getElementById('rv-memo').value,
        reviewed_at: new Date().toISOString(),
      };
      _saveJournal(j2);
    }
    overlay.remove();
    _renderJournalReview();
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// GLOBAL SEARCH (Ctrl+K / 🔍)
// ─────────────────────────────────────────────────────────────────────────────
(function setupGlobalSearch() {
  const overlay = document.getElementById('gsearch-overlay');
  const input   = document.getElementById('gsearch-input');
  const results  = document.getElementById('gsearch-results');
  if (!overlay || !input) return;
  let _timer = null;
  let _idx = -1;    // keyboard selection index
  let _items = [];   // current result items

  function openSearch() {
    overlay.style.display = 'flex';
    input.value = '';
    results.innerHTML = '';
    _idx = -1;
    _items = [];
    setTimeout(() => input.focus(), 50);
  }
  function closeSearch() {
    overlay.style.display = 'none';
    input.blur();
    _items = [];
    _idx = -1;
  }

  // 돋보기 버튼
  document.getElementById('gsearch-btn').addEventListener('click', openSearch);

  // Ctrl+K 또는 / 키
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
      e.preventDefault();
      openSearch();
    }
    if (e.key === '/' && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)) {
      e.preventDefault();
      openSearch();
    }
  });

  // overlay backdrop 클릭 닫기
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) closeSearch();
  });

  // ESC 닫기
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { closeSearch(); return; }
    // 방향키 + Enter
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      _idx = Math.min(_idx + 1, _items.length - 1);
      _highlightItem();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      _idx = Math.max(_idx - 1, 0);
      _highlightItem();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (_idx >= 0 && _idx < _items.length) _selectItem(_items[_idx]);
    }
  });

  // 입력 debounce
  input.addEventListener('input', () => {
    clearTimeout(_timer);
    const q = input.value.trim();
    if (q.length < 1) { results.innerHTML = ''; _items = []; _idx = -1; return; }
    _timer = setTimeout(() => _doSearch(q), 150);
  });

  async function _doSearch(q) {
    try {
      // KR + US 병렬
      const [krRes, usRes] = await Promise.all([
        fetch(`/api/stock_search?q=${encodeURIComponent(q)}`).then(r => r.json()).catch(() => []),
        fetch(`/api/us/search?q=${encodeURIComponent(q)}`).then(r => r.json()).catch(() => []),
      ]);
      const kr = (Array.isArray(krRes) ? krRes : []).map(it => ({ ...it, market: 'kr' }));
      const us = (Array.isArray(usRes) ? usRes : []).map(it => ({ ...it, market: 'us' }));
      _items = [...kr, ...us].slice(0, 10);
      _idx = -1;
      _renderResults();
    } catch { _items = []; }
  }

  function _renderResults() {
    if (!_items.length) {
      results.innerHTML = '<div class="gsearch-empty">결과 없음</div>';
      return;
    }
    results.innerHTML = _items.map((it, i) => {
      const flag = it.market === 'us' ? '🇺🇸' : '🇰🇷';
      const code = _escHtml(it.code || it.symbol || '');
      const name = _escHtml(it.name || '');
      const sector = _escHtml(it.sector || '');
      return `<div class="gsearch-item ${i === _idx ? 'active' : ''}" data-i="${i}">
        <span class="gsearch-flag">${flag}</span>
        <span class="gsearch-name">${name}</span>
        <span class="gsearch-code">${code}</span>
        ${sector ? `<span class="gsearch-sector">${sector}</span>` : ''}
      </div>`;
    }).join('');
  }

  function _highlightItem() {
    results.querySelectorAll('.gsearch-item').forEach((el, i) => {
      el.classList.toggle('active', i === _idx);
      if (i === _idx) el.scrollIntoView({ block: 'nearest' });
    });
  }

  function _selectItem(item) {
    closeSearch();
    const code = item.code || item.symbol;
    const name = item.name || code;
    const market = item.market || 'kr';
    openChartPanel(code, name, market);
  }

  // 결과 클릭
  results.addEventListener('click', (e) => {
    const el = e.target.closest('.gsearch-item');
    if (!el) return;
    const i = parseInt(el.dataset.i);
    if (i >= 0 && i < _items.length) _selectItem(_items[i]);
  });

  // 결과 호버
  results.addEventListener('mousemove', (e) => {
    const el = e.target.closest('.gsearch-item');
    if (!el) return;
    _idx = parseInt(el.dataset.i);
    _highlightItem();
  });
})();

// ─────────────────────────────────────────────────────────────────────────────
// ALERT RULES + CHECKLIST + SPLIT PLANNER
// ─────────────────────────────────────────────────────────────────────────────
let _alertCache = null;
function _getAlerts() {
  if (_alertCache) return _alertCache;
  try { _alertCache = JSON.parse(localStorage.getItem('alert_rules') || '{"rules":[]}'); }
  catch { _alertCache = { rules: [] }; }
  return _alertCache;
}
function _saveAlerts(d) {
  _alertCache = d;
  try { localStorage.setItem('alert_rules', JSON.stringify(d)); }
  catch {}
  fetch('/api/alerts/sync', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rules: d.rules }),
  }).catch(() => {});
}

const _ALERT_TYPES = [
  { value: 'price_above',    label: '가격 이상 도달',      unit: '원', group: '가격' },
  { value: 'price_below',    label: '가격 이하 하락',      unit: '원', group: '가격' },
  { value: 'change_up',      label: '당일 상승률 이상',    unit: '%',  group: '가격' },
  { value: 'change_down',    label: '당일 하락률 이하',    unit: '%',  group: '가격' },
  { value: 'rsi_oversold',   label: 'RSI 과매도 (≤)',     unit: '',   group: '기술' },
  { value: 'rsi_overbought', label: 'RSI 과매수 (≥)',     unit: '',   group: '기술' },
  { value: 'macd_golden',    label: 'MACD 골든크로스',     unit: null, group: '기술' },
  { value: 'macd_dead',      label: 'MACD 데드크로스',     unit: null, group: '기술' },
  { value: 'volume_spike',   label: '거래량 급증 (≥ N배)', unit: '배', group: '기술' },
  { value: 'foreign_strong_buy', label: '외국인 연속 순매수 (≥ N일)', unit: '일', group: '수급' },
  { value: 'foreign_cum_buy',    label: '외국인 5일 누적 매수 (≥ N억)', unit: '억', group: '수급' },
];

function _showAlertModal(code, name, market) {
  const cur = market === 'us' ? '$' : '₩';
  const options = _ALERT_TYPES.map(t =>
    `<option value="${t.value}">${t.label}</option>`
  ).join('');
  const existing = (_getAlerts().rules || []).filter(r => r.code === code);

  const existingHTML = existing.length
    ? existing.map(r => {
        const label = _ALERT_TYPES.find(t => t.value === r.type)?.label || r.type;
        const val = r.value != null ? ` (${r.value})` : '';
        const status = r.triggered_at ? '✅' : (r.enabled ? '⏳' : '⏸');
        return `<div class="alert-item" data-id="${_escHtml(r.id)}">
          <span>${status} ${_escHtml(label)}${val}</span>
          ${r.message ? `<span class="alert-memo">${_escHtml(r.message)}</span>` : ''}
          <button class="alert-del-btn" data-action="del-alert" data-id="${_escHtml(r.id)}">🗑</button>
        </div>`;
      }).join('')
    : '<div class="alert-empty">설정된 알림 없음</div>';

  const html = `
    <div class="pf-modal-overlay" id="alert-overlay">
      <div class="pf-modal" style="max-width:520px">
        <div class="pf-modal-title">⏰ ${_escHtml(name)} (${_escHtml(code)}) 알림</div>
        <div class="pf-modal-row">
          <label>조건</label>
          <select id="alert-type" class="pf-input">${options}</select>
        </div>
        <div class="pf-modal-row" id="alert-val-row">
          <label>값</label>
          <input type="number" id="alert-value" class="pf-input" step="any" placeholder="예: 75000">
        </div>
        <div class="pf-modal-row">
          <label>메모</label>
          <input type="text" id="alert-memo" class="pf-input" placeholder="분할매수 1차 타점 등">
        </div>
        <div class="pf-modal-actions">
          <button class="pf-cancel-btn" id="alert-cancel">취소</button>
          <button class="pf-save-btn" id="alert-save">저장</button>
        </div>
        <div style="margin-top:14px;padding-top:10px;border-top:1px solid var(--border)">
          <div style="color:var(--text-muted);font-size:11px;margin-bottom:6px">이 종목 기존 알림</div>
          <div id="alert-existing">${existingHTML}</div>
        </div>
      </div>
    </div>`;
  document.body.insertAdjacentHTML('beforeend', html);

  const overlay = document.getElementById('alert-overlay');
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
  document.getElementById('alert-cancel').addEventListener('click', () => overlay.remove());
  document.getElementById('alert-save').addEventListener('click', () => {
    const type = document.getElementById('alert-type').value;
    const value = parseFloat(document.getElementById('alert-value').value) || null;
    const memo = document.getElementById('alert-memo').value;
    const alerts = _getAlerts();
    alerts.rules.push({
      id: 'alert_' + Date.now(), code, name, market,
      type, value, message: memo,
      enabled: true, triggered_at: null,
      created_at: new Date().toISOString(),
    });
    _saveAlerts(alerts);
    overlay.remove();
  });

  // 타입 변경 시 값 입력 필드 토글
  const typeEl = document.getElementById('alert-type');
  const valRow = document.getElementById('alert-val-row');
  typeEl.addEventListener('change', () => {
    const t = _ALERT_TYPES.find(x => x.value === typeEl.value);
    valRow.style.display = t?.unit === null ? 'none' : 'flex';
  });

  // 기존 알림 삭제
  document.getElementById('alert-existing').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-action="del-alert"]');
    if (!btn) return;
    const id = btn.dataset.id;
    const alerts = _getAlerts();
    alerts.rules = alerts.rules.filter(r => r.id !== id);
    _saveAlerts(alerts);
    btn.closest('.alert-item')?.remove();
  });
}

// ── 트레일링 스톱 모달 ──
function _showTrailingModal(posId) {
  const pf = _getPortfolio();
  const pos = (pf.positions || []).find(p => p.id === posId);
  if (!pos) return;
  const tr = pos.trailing || {};
  const cur = pos.market === 'us' ? '$' : '₩';

  const histHTML = (tr.stop_history || []).slice(-10).reverse().map(h =>
    `<div class="trail-hist">${_escHtml(h.date)} · 현재가 ${cur}${Number(h.price).toLocaleString()} → 손절 ${cur}${Number(h.stop).toLocaleString()}</div>`
  ).join('') || '<div class="trail-hist" style="color:var(--text-muted)">이력 없음</div>';

  const html = `
    <div class="pf-modal-overlay" id="trail-overlay">
      <div class="pf-modal" style="max-width:480px">
        <div class="pf-modal-title">🎯 트레일링 스톱 설정</div>
        <div style="color:#FFD700;font-size:13px;margin-bottom:12px">${_escHtml(pos.name)} (${_escHtml(pos.code)})</div>
        <div class="trail-info">
          매수가 ${cur}${pos.buy_price?.toLocaleString()} ·
          현재 손절가 ${tr.current_stop ? cur + Number(tr.current_stop).toLocaleString() : '미설정'} ·
          최고가 ${tr.highest_since_entry ? cur + Number(tr.highest_since_entry).toLocaleString() : '—'}
        </div>
        <div class="pf-modal-row">
          <label>활성화</label>
          <input type="checkbox" id="trail-enabled" ${tr.enabled ? 'checked' : ''} style="width:18px;height:18px">
        </div>
        <div class="pf-modal-row">
          <label>방식</label>
          <select id="trail-type" class="pf-input">
            <option value="fixed_pct" ${(tr.type || 'fixed_pct') === 'fixed_pct' ? 'selected' : ''}>고정 비율 (-X%)</option>
            <option value="atr" ${tr.type === 'atr' ? 'selected' : ''}>ATR × N (변동성)</option>
            <option value="chandelier" ${tr.type === 'chandelier' ? 'selected' : ''}>샹들리에</option>
          </select>
        </div>
        <div class="pf-modal-row" id="trail-pct-row">
          <label>하락 허용</label>
          <input type="number" id="trail-pct" class="pf-input" value="${tr.fixed_pct || 5}" step="0.5" min="1" max="20" style="width:80px">
          <span style="color:var(--text-muted);font-size:11px">% (고점 대비)</span>
        </div>
        <div class="pf-modal-row" id="trail-atr-row" style="display:none">
          <label>ATR 배수</label>
          <input type="number" id="trail-mult" class="pf-input" value="${tr.atr_multiplier || 2}" step="0.5" min="1" max="5" style="width:80px">
          <span style="color:var(--text-muted);font-size:11px">× ATR</span>
        </div>
        <div class="pf-modal-row">
          <label>텔레그램 알림</label>
          <input type="checkbox" id="trail-alert" ${tr.alert_on_trigger !== false ? 'checked' : ''} style="width:18px;height:18px">
        </div>
        <div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--border)">
          <div style="color:var(--text-muted);font-size:11px;margin-bottom:4px">손절가 이력 (최근 10일)</div>
          ${histHTML}
        </div>
        <div class="pf-modal-actions">
          <button class="pf-cancel-btn" id="trail-cancel">취소</button>
          <button class="pf-save-btn" id="trail-save">저장</button>
        </div>
      </div>
    </div>`;
  document.body.insertAdjacentHTML('beforeend', html);

  const overlay = document.getElementById('trail-overlay');
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
  document.getElementById('trail-cancel').addEventListener('click', () => overlay.remove());

  // 방식 변경 시 입력 토글
  const typeEl = document.getElementById('trail-type');
  const toggle = () => {
    document.getElementById('trail-pct-row').style.display = typeEl.value === 'fixed_pct' ? 'flex' : 'none';
    document.getElementById('trail-atr-row').style.display = typeEl.value === 'atr' ? 'flex' : 'none';
  };
  typeEl.addEventListener('change', toggle);
  toggle();

  document.getElementById('trail-save').addEventListener('click', () => {
    const pf2 = _getPortfolio();
    const idx = pf2.positions.findIndex(p => p.id === posId);
    if (idx < 0) return;
    const oldTrail = pf2.positions[idx].trailing || {};
    pf2.positions[idx].trailing = {
      ...oldTrail,
      enabled: document.getElementById('trail-enabled').checked,
      type: document.getElementById('trail-type').value,
      fixed_pct: parseFloat(document.getElementById('trail-pct').value) || 5,
      atr_multiplier: parseFloat(document.getElementById('trail-mult').value) || 2,
      alert_on_trigger: document.getElementById('trail-alert').checked,
    };
    // 초기 손절가 설정 (아직 없으면)
    if (!oldTrail.current_stop && pf2.positions[idx].trailing.enabled) {
      const bp = pf2.positions[idx].buy_price || 0;
      const pct = pf2.positions[idx].trailing.fixed_pct || 5;
      pf2.positions[idx].trailing.current_stop = Math.round(bp * (1 - pct / 100));
      pf2.positions[idx].trailing.highest_since_entry = bp;
    }
    _savePortfolio(pf2);
    overlay.remove();
    renderPortfolioPage();
  });
}

// ── 통합 매매 패널 (우측 슬라이드) ──
function _toggleTradePanel(code, name, market) {
  const existing = document.getElementById('trade-panel');
  if (existing) { existing.remove(); return; }

  const html = `<div class="trade-panel" id="trade-panel">
    <div class="tp-header">
      <span class="tp-title">⚡ 매매 패널</span>
      <button class="tp-close" id="tp-close">×</button>
    </div>
    <div class="tp-body" id="tp-body"><div class="pg-empty">로딩 중…</div></div>
  </div>`;
  document.body.insertAdjacentHTML('beforeend', html);
  document.getElementById('tp-close').addEventListener('click', () =>
    document.getElementById('trade-panel')?.remove()
  );
  _loadTradePanel(code, name, market);
}

async function _loadTradePanel(code, name, market) {
  const body = document.getElementById('tp-body');
  if (!body) return;
  const cur = market === 'us' ? '$' : '₩';
  const isUS = market === 'us';

  // 기존 데이터 수집 (병렬)
  let vpData = null;
  try {
    const vpUrl = `/api/volume_profile/${code}?market=${market}`;
    const r = await fetch(vpUrl);
    if (r.ok) vpData = await r.json();
  } catch {}

  // CHART_STATE에서 기술 지표
  const chartData = CHART_STATE.data || {};
  const rm = chartData.rsi_macd || {};
  const adxData = chartData.adx || {};
  const rsiCur = (rm.rsi || []).slice(-1)[0];
  const adxCur = (adxData.adx || []).slice(-1)[0];
  const pdi = (adxData.plus_di || []).slice(-1)[0];
  const mdi = (adxData.minus_di || []).slice(-1)[0];
  const macdCur = (rm.macd || []).slice(-1)[0];
  const sigCur = (rm.macd_signal || []).slice(-1)[0];
  const histCur = (rm.macd_hist || []).slice(-1)[0];
  const divs = rm.divergences || [];
  const closes = chartData.close || [];
  const curPrice = closes[closes.length - 1] || 0;

  // 종합 판단 (간이)
  let bullPts = 0, bearPts = 0;
  const reasons = [];
  if (rsiCur != null) {
    if (rsiCur > 70) { bearPts++; reasons.push(`RSI ${rsiCur.toFixed(0)} 과매수`); }
    else if (rsiCur < 30) { bullPts++; reasons.push(`RSI ${rsiCur.toFixed(0)} 과매도 반등`); }
    else reasons.push(`RSI ${rsiCur.toFixed(0)}`);
  }
  if (macdCur != null && sigCur != null) {
    if (macdCur > sigCur) { bullPts++; reasons.push('MACD > Signal'); }
    else { bearPts++; reasons.push('MACD < Signal'); }
  }
  if (adxCur != null) {
    if (adxCur > 25 && pdi > mdi) { bullPts++; reasons.push('ADX 추세 상승'); }
    else if (adxCur > 25 && mdi > pdi) { bearPts++; reasons.push('ADX 추세 하락'); }
    else reasons.push('ADX 횡보');
  }
  divs.forEach(d => {
    if (d.type === 'bearish') { bearPts++; reasons.push(`${d.indicator} 베어리시 다이버전스`); }
    if (d.type === 'bullish') { bullPts++; reasons.push(`${d.indicator} 불리시 다이버전스`); }
  });

  const verdict = bullPts > bearPts ? '강세' : bearPts > bullPts ? '약세' : '중립';
  const verdictEmoji = verdict === '강세' ? '🟢' : verdict === '약세' ? '🔴' : '🟡';
  const verdictCol = verdict === '강세' ? '#FF3333' : verdict === '약세' ? '#33AA33' : '#FFD700';

  // 포트폴리오 포지션
  const pf = _getPortfolio();
  const myPos = (pf.positions || []).find(p => p.code === code);

  // 알림
  const alerts = (_getAlerts().rules || []).filter(r => r.code === code && r.enabled);

  let h = '';
  // 종목 헤더
  h += `<div class="tp-stock">
    <div class="tp-sname">${_escHtml(name)}</div>
    <div class="tp-scode">${_escHtml(code)} · ${isUS ? '🇺🇸' : '🇰🇷'}</div>
    <div class="tp-sprice">${cur}${curPrice.toLocaleString()}</div>
  </div>`;

  // 종합 판단
  h += `<div class="tp-verdict" style="border-left:4px solid ${verdictCol}">
    <div class="tp-v-head">${verdictEmoji} <b>${verdict}</b> (🟢${bullPts} 🔴${bearPts})</div>
    <div class="tp-v-reasons">${reasons.map(r => `<div>• ${_escHtml(r)}</div>`).join('')}</div>
  </div>`;

  // 지표 그리드
  h += `<div class="tp-ind-grid">
    <div class="tp-ind"><span>RSI</span><b style="color:${rsiCur > 70 ? '#FF3333' : rsiCur < 30 ? '#33AA33' : 'var(--text)'}">${rsiCur?.toFixed(0) ?? '—'}</b></div>
    <div class="tp-ind"><span>MACD</span><b>${macdCur?.toFixed(0) ?? '—'}</b></div>
    <div class="tp-ind"><span>ADX</span><b style="color:${adxCur > 25 ? '#FFD700' : '#888'}">${adxCur?.toFixed(0) ?? '—'}</b></div>
    <div class="tp-ind"><span>Hist</span><b style="color:${(histCur||0) >= 0 ? '#FF3333' : '#33AA33'}">${histCur?.toFixed(0) ?? '—'}</b></div>
  </div>`;

  // 매물대 (volume_profile)
  if (vpData && !vpData.error) {
    const vp = vpData.volume_profile || {};
    h += `<div class="tp-section">
      <div class="tp-sec-title">🎯 매물대</div>
      <div class="tp-row"><span>POC</span><b style="color:#FFD700">${cur}${vp.poc?.toLocaleString()}</b></div>
      <div class="tp-row"><span>VWAP</span><b style="color:#FF9500">${cur}${vpData.vwap_current?.toLocaleString()}</b></div>
      <div class="tp-row"><span>Value Area</span><b>${cur}${vp.va_low?.toLocaleString()} ~ ${cur}${vp.va_high?.toLocaleString()}</b></div>
      ${(vpData.resistance || []).map(r => `<div class="tp-row"><span style="color:#FF3333">저항 (${r.touches}x)</span><b>${cur}${r.price.toLocaleString()}</b></div>`).join('')}
      ${(vpData.support || []).map(s => `<div class="tp-row"><span style="color:#33AA33">지지 (${s.touches}x)</span><b>${cur}${s.price.toLocaleString()}</b></div>`).join('')}
    </div>`;
  }

  // 외국인/기관 수급 강도 (KR only)
  if (!isUS) {
    try {
      const flowResp = await fetch(`/api/flow/${code}`);
      if (flowResp.ok) {
        const flowData = await flowResp.json();
        if (flowData && !flowData.error) {
          const fv = flowData.foreign_value || [];
          const iv = flowData.inst_value || [];
          if (fv.length) {
            let fStreak = 0;
            for (let i = fv.length - 1; i >= 0; i--) { if (fv[i] > 0) fStreak++; else break; }
            let iStreak = 0;
            for (let i = iv.length - 1; i >= 0; i--) { if (iv[i] > 0) iStreak++; else break; }
            const fToday = fv[fv.length - 1] || 0;
            const iToday = iv.length ? iv[iv.length - 1] : 0;
            const fCum5 = fv.slice(-5).reduce((a, b) => a + b, 0);
            const fCum10 = fv.slice(-10).reduce((a, b) => a + b, 0);
            const fmtEok = (v) => { const e = v / 1e8; return (e >= 0 ? '+' : '') + e.toFixed(0) + '억'; };
            const fCol = fToday >= 0 ? '#FF3333' : '#33AA33';
            const iCol = iToday >= 0 ? '#FF3333' : '#33AA33';
            h += `<div class="tp-section">
              <div class="tp-sec-title">🌊 외국인/기관 수급</div>
              <div class="tp-row"><span>외국인 당일</span><b style="color:${fCol}">${fmtEok(fToday)}</b></div>
              <div class="tp-row"><span>외국인 5일 누적</span><b style="color:${fCum5 >= 0 ? '#FF3333' : '#33AA33'}">${fmtEok(fCum5)}</b></div>
              <div class="tp-row"><span>외국인 10일 누적</span><b style="color:${fCum10 >= 0 ? '#FF3333' : '#33AA33'}">${fmtEok(fCum10)}</b></div>
              <div class="tp-row"><span>외국인 연속</span><b style="color:${fStreak >= 3 ? '#FFD700' : 'var(--text)'}">${fStreak}일</b></div>
              <div class="tp-row"><span>기관 당일</span><b style="color:${iCol}">${fmtEok(iToday)}</b></div>
              <div class="tp-row"><span>기관 연속</span><b style="color:${iStreak >= 3 ? '#FFD700' : 'var(--text)'}">${iStreak}일</b></div>
              ${fStreak >= 3 && iStreak >= 2 ? '<div class="tp-row" style="color:#FFD700;font-weight:bold">⚡ 외국인+기관 쌍끌이 매수</div>' : ''}
            </div>`;
          }
        }
      }
    } catch {}
  }

  // 내 포지션
  if (myPos) {
    const pnl = (curPrice - myPos.buy_price) * myPos.quantity;
    const pnlPct = myPos.buy_price ? ((curPrice / myPos.buy_price - 1) * 100) : 0;
    const pnlCol = pnl >= 0 ? '#FF3333' : '#33AA33';
    const sign = pnl >= 0 ? '+' : '';
    const tr = myPos.trailing || {};
    h += `<div class="tp-section tp-mypos">
      <div class="tp-sec-title">💼 내 포지션</div>
      <div class="tp-row"><span>매수가</span><b>${cur}${myPos.buy_price?.toLocaleString()}</b></div>
      <div class="tp-row"><span>수량</span><b>${myPos.quantity}주</b></div>
      <div class="tp-row"><span>손익</span><b style="color:${pnlCol}">${sign}${_fmtMoney(pnl, market)} (${sign}${pnlPct.toFixed(2)}%)</b></div>
      ${tr.enabled ? `<div class="tp-row"><span>트레일링 손절</span><b style="color:#FFD700">${cur}${Number(tr.current_stop || 0).toLocaleString()}</b></div>` : ''}
    </div>`;
  }

  // 활성 알림
  if (alerts.length) {
    h += `<div class="tp-section">
      <div class="tp-sec-title">⏰ 활성 알림 (${alerts.length})</div>
      ${alerts.map(a => {
        const t = _ALERT_TYPES.find(x => x.value === a.type);
        return `<div class="tp-alert-chip">${t?.label || a.type}${a.value ? ' ' + a.value : ''}</div>`;
      }).join('')}
    </div>`;
  }

  // 액션 버튼
  h += `<div class="tp-actions">
    ${myPos
      ? `<button class="tp-btn sell" data-act="sell">💰 매도 기록</button>
         <button class="tp-btn manage" data-act="trailing">🎯 트레일링</button>`
      : `<button class="tp-btn buy" data-act="buy">🛒 매수 (체크리스트)</button>`}
    <button class="tp-btn alert" data-act="alert">⏰ 알림 설정</button>
  </div>`;

  body.innerHTML = h;

  // 액션 버튼 이벤트
  body.querySelector('.tp-actions')?.addEventListener('click', (e) => {
    const btn = e.target.closest('.tp-btn');
    if (!btn) return;
    const act = btn.dataset.act;
    if (act === 'buy') _showChecklist(code, name, market, 'buy', () => _showPfModal());
    if (act === 'sell') _showJournalModal();
    if (act === 'trailing' && myPos) _showTrailingModal(myPos.id);
    if (act === 'alert') _showAlertModal(code, name, market);
  });
}

// ── 매매 체크리스트 ──
function _showChecklist(code, name, market, action, onProceed) {
  const items = [
    ['시장 방향성 확인', 'KOSPI/S&P500 일봉이 20MA 위 상승 추세인가?'],
    ['섹터 모멘텀 확인', '이 종목의 섹터가 최근 1주 수익률 상위인가?'],
    ['개별 종목 추세', '20/60/120MA 정배열 또는 지지선 위에 있는가?'],
    ['진입가·손절가·목표가 명확', '세 가격이 사전에 결정되어 있는가?'],
    ['손익비 1:1.5 이상', '(목표가-진입가) ≥ (진입가-손절가) × 1.5 인가?'],
    ['포지션 사이즈 계산', '계좌 2% 이내 손실 허용선으로 수량을 결정했는가?'],
    ['분할매수 계획 수립', '1차/2차/3차 비중과 가격을 정했는가?'],
  ];
  const itemsHTML = items.map(([main, sub]) => `
    <label class="cl-item">
      <input type="checkbox" class="cl-cb">
      <div><div class="cl-main">${_escHtml(main)}</div><div class="cl-sub">${_escHtml(sub)}</div></div>
    </label>
  `).join('');

  const html = `
    <div class="pf-modal-overlay" id="cl-overlay">
      <div class="pf-modal" style="max-width:620px">
        <div class="pf-modal-title">✅ ${action === 'buy' ? '매수' : '매도'} 체크리스트</div>
        <div style="color:var(--text-muted);font-size:11px;margin-bottom:14px;line-height:1.6">
          충동매매를 방지합니다. 7개 중 <b style="color:#FFD700">5개 이상</b>(70%) 체크해야 진행 가능.
        </div>
        <div class="cl-items" id="cl-items">${itemsHTML}</div>
        <div class="cl-score">
          체크율: <span id="cl-pct">0%</span>
          <div class="cl-track"><div id="cl-fill" class="cl-fill"></div></div>
        </div>
        <div class="pf-modal-actions">
          <button class="pf-cancel-btn" id="cl-cancel">취소</button>
          <button class="pf-save-btn cl-proceed" id="cl-proceed" disabled>매매 진행</button>
        </div>
      </div>
    </div>`;
  document.body.insertAdjacentHTML('beforeend', html);

  const overlay = document.getElementById('cl-overlay');
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
  document.getElementById('cl-cancel').addEventListener('click', () => overlay.remove());

  document.getElementById('cl-items').addEventListener('change', () => {
    const cbs = document.querySelectorAll('#cl-items .cl-cb');
    const checked = Array.from(cbs).filter(c => c.checked).length;
    const pct = Math.round(checked / cbs.length * 100);
    document.getElementById('cl-pct').textContent = pct + '%';
    document.getElementById('cl-fill').style.width = pct + '%';
    const btn = document.getElementById('cl-proceed');
    btn.disabled = pct < 70;
    btn.classList.toggle('enabled', pct >= 70);
  });

  document.getElementById('cl-proceed').addEventListener('click', () => {
    overlay.remove();
    if (onProceed) onProceed();
  });
}

// ── 분할매수 계획기 ──
function _renderSplitPlanner(container, code, name, market) {
  const cur = market === 'us' ? '$' : '₩';
  const html = `
    <div class="split-section">
      <div class="split-title">📋 분할매수 계획기</div>
      <div class="split-grid">
        <div class="split-row"><label>총 수량</label><input type="number" id="sp-qty" class="split-inp" placeholder="100" oninput="_calcSplit('${market}')"><span>주</span></div>
        <div class="split-row"><label>1차 가격</label><input type="number" id="sp-p1" class="split-inp" placeholder="${cur}" oninput="_calcSplit('${market}')"><span>${cur}</span></div>
        <div class="split-row"><label>1차 비중</label><input type="number" id="sp-w1" class="split-inp" value="40" oninput="_calcSplit('${market}')"><span>%</span></div>
        <div class="split-row"><label>2차 조정</label><input type="number" id="sp-d2" class="split-inp" value="-2" step="0.5" oninput="_calcSplit('${market}')"><span>%</span></div>
        <div class="split-row"><label>2차 비중</label><input type="number" id="sp-w2" class="split-inp" value="35" oninput="_calcSplit('${market}')"><span>%</span></div>
        <div class="split-row"><label>3차 조정</label><input type="number" id="sp-d3" class="split-inp" value="-5" step="0.5" oninput="_calcSplit('${market}')"><span>%</span></div>
        <div class="split-row"><label>3차 비중</label><input type="number" id="sp-w3" class="split-inp" value="25" oninput="_calcSplit('${market}')"><span>%</span></div>
      </div>
      <div id="sp-result" class="sp-result">수량·가격 입력 시 계산됩니다.</div>
      <button class="split-save" id="sp-save-btn" style="display:none">💾 계획 저장</button>
    </div>`;
  container.insertAdjacentHTML('beforeend', html);
  document.getElementById('sp-save-btn').addEventListener('click', () => {
    const plans = JSON.parse(localStorage.getItem('split_plans') || '{"items":[]}');
    const qty = parseInt(document.getElementById('sp-qty').value) || 0;
    const p1 = parseFloat(document.getElementById('sp-p1').value) || 0;
    plans.items.push({
      id: 'plan_' + Date.now(), code, name, market,
      total_qty: qty, price1: p1,
      w1: parseFloat(document.getElementById('sp-w1').value),
      w2: parseFloat(document.getElementById('sp-w2').value),
      w3: parseFloat(document.getElementById('sp-w3').value),
      d2: parseFloat(document.getElementById('sp-d2').value),
      d3: parseFloat(document.getElementById('sp-d3').value),
      created_at: new Date().toISOString(),
    });
    localStorage.setItem('split_plans', JSON.stringify(plans));
    alert('분할매수 계획이 저장되었습니다.');
  });
}

function _calcSplit(market) {
  const qty = parseInt(document.getElementById('sp-qty')?.value) || 0;
  const p1 = parseFloat(document.getElementById('sp-p1')?.value) || 0;
  const w1 = parseFloat(document.getElementById('sp-w1')?.value) || 0;
  const w2 = parseFloat(document.getElementById('sp-w2')?.value) || 0;
  const w3 = parseFloat(document.getElementById('sp-w3')?.value) || 0;
  const d2 = parseFloat(document.getElementById('sp-d2')?.value) || 0;
  const d3 = parseFloat(document.getElementById('sp-d3')?.value) || 0;
  const box = document.getElementById('sp-result');
  const saveBtn = document.getElementById('sp-save-btn');
  if (!qty || !p1) { box.textContent = '수량·가격을 입력하세요.'; saveBtn.style.display = 'none'; return; }
  const total = w1 + w2 + w3;
  if (Math.abs(total - 100) > 0.5) {
    box.innerHTML = `<span style="color:#FF6B6B">비중 합계 ${total}% (100% 필요)</span>`;
    saveBtn.style.display = 'none'; return;
  }
  const q1 = Math.floor(qty * w1 / 100);
  const q2 = Math.floor(qty * w2 / 100);
  const q3 = qty - q1 - q2;
  const p2 = Math.round(p1 * (1 + d2 / 100));
  const p3 = Math.round(p1 * (1 + d3 / 100));
  const inv1 = q1 * p1, inv2 = q2 * p2, inv3 = q3 * p3;
  const total_inv = inv1 + inv2 + inv3;
  const avg = qty > 0 ? Math.round(total_inv / qty) : 0;
  const cur = market === 'us' ? '$' : '₩';
  const fmt = v => `${cur}${Math.round(v).toLocaleString()}`;
  box.innerHTML = `
    <table class="sp-table"><thead><tr>
      <th>단계</th><th>가격</th><th>비중</th><th>수량</th><th>투자금</th>
    </tr></thead><tbody>
      <tr><td class="sp-stage">1차</td><td>${fmt(p1)}</td><td>${w1}%</td><td>${q1}주</td><td>${fmt(inv1)}</td></tr>
      <tr><td class="sp-stage">2차 (${d2}%)</td><td>${fmt(p2)}</td><td>${w2}%</td><td>${q2}주</td><td>${fmt(inv2)}</td></tr>
      <tr><td class="sp-stage">3차 (${d3}%)</td><td>${fmt(p3)}</td><td>${w3}%</td><td>${q3}주</td><td>${fmt(inv3)}</td></tr>
      <tr class="sp-avg"><td colspan="2">평균 매수가</td><td colspan="3"><b>${fmt(avg)}</b> · 총 ${fmt(total_inv)}</td></tr>
    </tbody></table>
    <div class="sp-tip">💡 2차 ${fmt(p2)}, 3차 ${fmt(p3)}에 ⏰ 알림을 설정하면 자동 텔레그램 알림</div>`;
  saveBtn.style.display = '';
}

// ─────────────────────────────────────────────────────────────────────────────
// ETF MAP (Phase 23) — 국내 주요 ETF 테마별 트리맵
// ─────────────────────────────────────────────────────────────────────────────
async function renderETFMapPage() {
  const root = document.getElementById('etfmap-view');
  root.innerHTML = `<div class="pg-wrap">
    <div class="pg-title">📦 ETF 히트맵</div>
    <div class="pg-sub">국내 주요 ETF 테마별 등락률 · 30분 캐시</div>
    <div id="etf-meta" class="etf-meta">로딩 중…</div>
    <div id="etf-canvas"><svg id="etf-svg"></svg></div>
    <div id="etf-members"><div class="pg-empty">테마를 클릭하면 소속 ETF 가 표시됩니다.</div></div>
  </div>`;
  try {
    const r = await fetch('/api/etf_map');
    const d = await r.json();
    if (!r.ok || d.error) throw new Error(d.error || ('HTTP ' + r.status));
    document.getElementById('etf-meta').textContent =
      `${d.updated_at} 기준 · ${d.theme_count}개 테마`;
    _drawETFTreemap(d.themes || []);
  } catch (err) {
    document.getElementById('etf-meta').innerHTML =
      `<div class="pg-empty"><div class="pg-empty-title">로딩 실패</div>${_escHtml(err.message)}</div>`;
  }
}

function _drawETFTreemap(themes) {
  const svgEl = document.getElementById('etf-svg');
  if (!svgEl || !themes.length) return;
  const W = 1152, H = 520;
  svgEl.setAttribute('width', W);
  svgEl.setAttribute('height', H);
  svgEl.setAttribute('viewBox', `0 0 ${W} ${H}`);
  d3.select(svgEl).selectAll('*').remove();

  const root = d3.hierarchy({ children: themes })
    .sum(d => Math.max(d.stock_count || 1, 1));
  d3.treemap().size([W, H]).paddingInner(3).round(true)(root);

  const cells = d3.select(svgEl).selectAll('g.etf-cell')
    .data(root.leaves())
    .enter().append('g')
    .attr('class', 'etf-cell')
    .attr('transform', d => `translate(${d.x0},${d.y0})`)
    .style('cursor', 'pointer')
    .on('click', (e, d) => _showETFMembers(d.data));

  cells.append('rect')
    .attr('width',  d => Math.max(0, d.x1 - d.x0))
    .attr('height', d => Math.max(0, d.y1 - d.y0))
    .attr('rx', 6).attr('ry', 6)
    .attr('fill', d => pctBgColor(d.data.weighted_avg_pct || 0))
    .attr('stroke', 'var(--bg)').attr('stroke-width', 2);

  cells.each(function(d) {
    const w = d.x1 - d.x0, h = d.y1 - d.y0;
    if (w < 40 || h < 24) return;
    const g = d3.select(this);
    const nameSize = w < 100 ? 11 : 13;
    g.append('text')
      .attr('x', w / 2).attr('y', h / 2 - 4)
      .attr('text-anchor', 'middle').attr('dominant-baseline', 'middle')
      .attr('font-size', nameSize).attr('font-weight', 600)
      .attr('fill', 'rgba(255,255,255,0.95)')
      .text(truncText(d.data.name, w, nameSize));
    if (h >= 40) {
      g.append('text')
        .attr('x', w / 2).attr('y', h / 2 + nameSize)
        .attr('text-anchor', 'middle').attr('dominant-baseline', 'middle')
        .attr('font-size', 11).attr('fill', 'rgba(255,255,255,0.82)')
        .text(fmtPct(d.data.weighted_avg_pct || 0));
    }
  });
}

function _showETFMembers(theme) {
  const box = document.getElementById('etf-members');
  const stocks = theme.stocks || [];
  if (!stocks.length) {
    box.innerHTML = '<div class="pg-empty">ETF 없음</div>';
    return;
  }
  const rows = stocks.map((s, i) => {
    const chg = s.change_pct || 0;
    const col = chg > 0 ? '#FF3333' : chg < 0 ? '#33AA33' : 'var(--text-muted)';
    const sign = chg > 0 ? '+' : '';
    const price = s.price != null ? '₩' + Number(s.price).toLocaleString() : '—';
    return `<tr data-code="${_escHtml(s.code)}" data-name="${_escHtml(s.name)}">
      <td><span class="rank">${i + 1}</span></td>
      <td style="color:var(--text-muted);font-size:11px">${_escHtml(s.code)}</td>
      <td style="font-weight:600">${_escHtml(s.name)}</td>
      <td class="r">${price}</td>
      <td class="r" style="color:${col};font-weight:700">${sign}${chg.toFixed(2)}%</td>
    </tr>`;
  }).join('');
  const headColor = theme.weighted_avg_pct > 0 ? '#FF3333'
                  : theme.weighted_avg_pct < 0 ? '#33AA33'
                  : 'var(--text-muted)';
  box.innerHTML = `
    <div class="pg-wrap" style="margin-top:0;">
      <div class="pg-title" style="font-size:16px;">
        ${_escHtml(theme.name)}
        <span style="color:${headColor};font-size:14px;margin-left:12px">
          ${fmtPct(theme.weighted_avg_pct || 0)}
        </span>
        <span style="color:var(--text-muted);font-size:12px;margin-left:8px">${stocks.length}개 ETF</span>
      </div>
      <table class="pg-table">
        <thead><tr>
          <th style="width:42px">#</th><th>코드</th><th>ETF명</th>
          <th class="r">현재가</th><th class="r">등락률</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
  box.querySelector('tbody').addEventListener('click', (e) => {
    const tr = e.target.closest('tr');
    if (!tr) return;
    openChartPanel(tr.dataset.code, tr.dataset.name, 'kr');
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// DIVIDEND SCREENER (Phase 23)
// ─────────────────────────────────────────────────────────────────────────────
async function renderDividendPage() {
  const root = document.getElementById('dividend-view');
  root.innerHTML = `<div class="pg-wrap">
    <div class="pg-title">💰 배당 스크리너</div>
    <div class="pg-sub">고배당 종목 · PER 동시 확인 · 일 1회 캐시</div>
    <div id="div-content"><div class="pg-empty">로딩 중…</div></div>
  </div>`;

  try {
    const r = await fetch('/api/dividend');
    const d = await r.json();
    if (!r.ok || d.error) throw new Error(d.error || ('HTTP ' + r.status));
    const items = d.items || [];
    if (!items.length) {
      document.getElementById('div-content').innerHTML =
        '<div class="pg-empty">배당 데이터 없음</div>';
      return;
    }

    const yields = items.map(it => it.dividend_yield || 0);
    const avg = (yields.reduce((a, b) => a + b, 0) / yields.length).toFixed(2);
    const maxY = Math.max(...yields);

    const summaryHTML = `
      <div class="div-summary">
        <div class="div-summary-card">
          <div class="div-summary-label">종목 수</div>
          <div class="div-summary-value">${items.length}개</div>
        </div>
        <div class="div-summary-card">
          <div class="div-summary-label">평균 배당수익률</div>
          <div class="div-summary-value">${avg}%</div>
        </div>
        <div class="div-summary-card">
          <div class="div-summary-label">최고 배당수익률</div>
          <div class="div-summary-value" style="color:#FFD700">${maxY.toFixed(2)}%</div>
        </div>
      </div>`;

    const rows = items.map((it, i) => {
      const barW = Math.min((it.dividend_yield / maxY) * 100, 100);
      const yCol = it.dividend_yield >= 5 ? '#FFD700'
                 : it.dividend_yield >= 3 ? '#FF9500'
                 : 'var(--text)';
      const chg = it.change_pct || 0;
      const chgCol = chg > 0 ? '#FF3333' : chg < 0 ? '#33AA33' : 'var(--text-muted)';
      const sign = chg > 0 ? '+' : '';
      return `<tr class="div-row" data-code="${_escHtml(it.code)}" data-name="${_escHtml(it.name)}">
        <td class="r">${i + 1}</td>
        <td>
          <div class="div-name">${_escHtml(it.name)}</div>
          <div class="div-code">${_escHtml(it.code)}</div>
        </td>
        <td class="r">₩${(it.price || 0).toLocaleString()}</td>
        <td class="r" style="color:${chgCol}">${sign}${chg.toFixed(2)}%</td>
        <td class="r">
          <div class="div-yield-cell">
            <span style="color:${yCol};font-weight:700">${it.dividend_yield.toFixed(2)}%</span>
            <div class="div-yield-bar"><div class="div-yield-fill" style="width:${barW}%;background:${yCol}"></div></div>
          </div>
        </td>
        <td class="r">${it.per != null ? it.per : '—'}</td>
        <td class="r">${it.pbr != null ? it.pbr : '—'}</td>
        <td class="div-sector-cell">${_escHtml(it.sector || '—')}</td>
      </tr>`;
    }).join('');

    document.getElementById('div-content').innerHTML = `
      ${summaryHTML}
      <div class="div-meta">${d.updated_at} 기준</div>
      <table class="div-table">
        <thead><tr>
          <th class="r">#</th><th>종목</th>
          <th class="r">현재가</th><th class="r">등락률</th>
          <th class="r">배당수익률</th>
          <th class="r">PER</th><th class="r">PBR</th>
          <th>섹터</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
    document.querySelector('#div-content tbody').addEventListener('click', (e) => {
      const tr = e.target.closest('tr.div-row');
      if (!tr) return;
      openChartPanel(tr.dataset.code, tr.dataset.name, 'kr');
    });
  } catch (err) {
    document.getElementById('div-content').innerHTML =
      `<div class="pg-empty"><div class="pg-empty-title">로딩 실패</div>${_escHtml(err.message)}</div>`;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// SECTOR MAP PAGE (Phase 10) — Naver KRX 업종 실시간 스크랩
// ─────────────────────────────────────────────────────────────────────────────
const SECTOR_STATE = { sectors: null, selected: null, market: 'kr' };

async function renderSectorPage() {
  const root = document.getElementById('sector-view');
  const isUS = APP.market === 'us';

  // 시장 전환 시 캐시 무효화
  if (SECTOR_STATE.market !== APP.market) {
    SECTOR_STATE.sectors = null;
    SECTOR_STATE.selected = null;
    SECTOR_STATE.market = APP.market;
    root.dataset.built = '';
  }

  if (!root.dataset.built) {
    root.dataset.built = '1';
    const title = isUS ? 'S&P 500 Sector Map' : '업종별 히트맵';
    const sub   = isUS
      ? 'GICS 11 sectors · yfinance · 박스 크기 = 소속 종목수 · 색 = 섹터 가중평균 등락률'
      : 'KRX 79 업종 · 네이버 금융 실시간 · 박스 크기 = 소속 종목수 · 색 = 등락률';
    root.innerHTML = `
      <div class="pg-wrap">
        <div class="pg-title">${title}</div>
        <div class="pg-sub">${sub}</div>
        <div id="sector-canvas"
             style="background:var(--surface2);border-radius:10px;padding:6px;min-height:520px;">
          <svg id="sector-svg"></svg>
        </div>
        <div id="sector-members" style="margin-top:14px;"></div>
      </div>`;
  }

  if (!SECTOR_STATE.sectors) {
    document.getElementById('sector-members').innerHTML =
      isUS ? '<div class="pg-empty">S&P 500 섹터 데이터 로딩 중… (최초 부팅 후 약 3분 소요)</div>'
           : '<div class="pg-empty">업종 데이터 로딩 중…</div>';
    try {
      const url = isUS ? '/api/us/market' : '/api/sectors';
      const r = await fetch(url);
      const d = await r.json();
      if (r.status === 202 && d.building) {
        document.getElementById('sector-members').innerHTML =
          `<div class="pg-empty"><div class="pg-empty-title">S&P 500 데이터 빌드 중</div>${_escHtml(d.message)}<br><br><span style="color:var(--text-sub)">잠시 후 이 페이지를 다시 열어주세요.</span></div>`;
        return;
      }
      if (!r.ok || d.error) throw new Error(d.error || ('HTTP ' + r.status));

      if (isUS) {
        // US: /api/us/market 의 sectors 를 트리맵용으로 매핑
        SECTOR_STATE.sectors = (d.sectors || []).map(s => ({
          name:       s.name,
          total:      s.stock_count,
          change_pct: s.weighted_avg_pct,
          stocks:     s.stocks,   // members 를 같이 끌고감 (클릭 시 재요청 불필요)
          up:   s.stocks.filter(x => x.change_pct > 0).length,
          flat: s.stocks.filter(x => x.change_pct === 0).length,
          down: s.stocks.filter(x => x.change_pct < 0).length,
        }));
      } else {
        SECTOR_STATE.sectors = d.sectors || [];
      }
    } catch (err) {
      document.getElementById('sector-members').innerHTML =
        `<div class="pg-empty"><div class="pg-empty-title">섹터 데이터 로딩 실패</div>${_escHtml(err.message)}</div>`;
      return;
    }
  }
  _drawSectorTreemap(SECTOR_STATE.sectors);
  if (!SECTOR_STATE.selected) {
    document.getElementById('sector-members').innerHTML =
      `<div class="pg-empty" style="padding:16px;">${isUS ? 'Click a sector to see its constituents.' : '업종 박스를 클릭하면 소속 종목이 표시됩니다.'}</div>`;
  }
}

function _drawSectorTreemap(sectors) {
  const svgEl = document.getElementById('sector-svg');
  if (!svgEl || !sectors || !sectors.length) return;
  const W = 1152, H = 560;
  svgEl.setAttribute('width', W);
  svgEl.setAttribute('height', H);
  svgEl.setAttribute('viewBox', `0 0 ${W} ${H}`);
  d3.select(svgEl).selectAll('*').remove();

  const root = d3.hierarchy({ children: sectors })
    .sum(d => Math.max(d.total || 1, 1));
  d3.treemap().size([W, H]).paddingInner(3).round(true)(root);

  const cells = d3.select(svgEl).selectAll('g.sc-cell')
    .data(root.leaves())
    .enter().append('g')
    .attr('class', 'sc-cell')
    .attr('transform', d => `translate(${d.x0},${d.y0})`)
    .style('cursor', 'pointer')
    .on('click', (e, d) => _selectSector(d.data));

  cells.append('rect')
    .attr('width',  d => Math.max(0, d.x1 - d.x0))
    .attr('height', d => Math.max(0, d.y1 - d.y0))
    .attr('rx', 6).attr('ry', 6)
    .attr('fill', d => pctBgColor(d.data.change_pct || 0))
    .attr('stroke', 'var(--bg)').attr('stroke-width', 2);

  cells.each(function(d) {
    const w = d.x1 - d.x0, h = d.y1 - d.y0;
    if (w < 40 || h < 24) return;
    const g = d3.select(this);
    const nameSize = w < 100 ? 11 : 13;
    g.append('text')
      .attr('x', w / 2).attr('y', h / 2 - 4)
      .attr('text-anchor', 'middle').attr('dominant-baseline', 'middle')
      .attr('font-size', nameSize).attr('font-weight', 600)
      .attr('fill', 'rgba(255,255,255,0.95)')
      .text(truncText(d.data.name, w, nameSize));
    if (h >= 40) {
      g.append('text')
        .attr('x', w / 2).attr('y', h / 2 + nameSize)
        .attr('text-anchor', 'middle').attr('dominant-baseline', 'middle')
        .attr('font-size', 11).attr('fill', 'rgba(255,255,255,0.82)')
        .text(fmtPct(d.data.change_pct || 0));
    }
  });
}

async function _selectSector(sector) {
  SECTOR_STATE.selected = sector;
  const box = document.getElementById('sector-members');
  box.innerHTML = `<div class="pg-empty">${_escHtml(sector.name)} 소속 종목 로딩 중…</div>`;

  try {
    let stocks;
    if (APP.market === 'us') {
      // US 는 이미 랜딩 fetch 에서 members 를 함께 가져와 sector.stocks 에 박혀 있음.
      stocks = (sector.stocks || []).map(s => ({
        code:       s.symbol,
        name:       s.name,
        close:      s.price,
        change_pct: s.change_pct,
        volume_mn:  s.volume_mn,
      }));
    } else {
      const r = await fetch(`/api/sector/${sector.no}`);
      const d = await r.json();
      if (!r.ok || d.error) throw new Error(d.error || ('HTTP ' + r.status));
      if (SECTOR_STATE.selected !== sector) return;
      stocks = d.stocks || [];
    }
    _renderSectorMembers(sector, stocks);
  } catch (err) {
    box.innerHTML = `<div class="pg-empty"><div class="pg-empty-title">로드 실패</div>${err.message}</div>`;
  }
}

function _renderSectorMembers(sector, stocks) {
  const box = document.getElementById('sector-members');
  if (!stocks.length) {
    box.innerHTML = '<div class="pg-empty">소속 종목이 없습니다.</div>';
    return;
  }
  const isUS = APP.market === 'us';
  const rows = stocks.map((s, i) => {
    const sc = _escHtml(s.code);
    const sn = _escHtml(s.name);
    const col = s.change_pct > 0 ? '#FF3333' : s.change_pct < 0 ? '#33AA33' : 'var(--text-muted)';
    const sign = s.change_pct > 0 ? '+' : '';
    const priceStr = isUS
      ? '$' + Number(s.close || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
      : (s.close || 0).toLocaleString();
    const volStr = isUS
      ? '$' + (s.volume_mn || 0).toLocaleString('en-US', { maximumFractionDigits: 0 }) + 'M'
      : (s.volume_mn || 0).toLocaleString() + 'M';
    return `<tr data-code="${sc}" data-name="${sn}">
      <td><span class="rank">${i + 1}</span></td>
      <td style="color:var(--text-muted);font-size:11px">${sc}</td>
      <td style="font-weight:600">${sn}</td>
      <td class="r">${priceStr}</td>
      <td class="r" style="color:${col};font-weight:700">${sign}${(s.change_pct || 0).toFixed(2)}%</td>
      <td class="r" style="color:var(--text-sub)">${volStr}</td>
    </tr>`;
  }).join('');

  const headColor = sector.change_pct > 0 ? '#FF3333' : sector.change_pct < 0 ? '#33AA33' : 'var(--text-muted)';
  const breakdown = (sector.up != null)
    ? `${stocks.length}${isUS ? ' stocks' : '종목'} · ${isUS ? 'up' : '상승'} ${sector.up} / ${isUS ? 'flat' : '보합'} ${sector.flat} / ${isUS ? 'down' : '하락'} ${sector.down}`
    : `${stocks.length}${isUS ? ' stocks' : '종목'}`;
  box.innerHTML = `
    <div class="pg-wrap" style="margin-top:0;">
      <div class="pg-title" style="font-size:16px;">
        ${_escHtml(sector.name)}
        <span style="color:${headColor};font-size:14px;margin-left:12px">
          ${sector.change_pct > 0 ? '+' : ''}${(sector.change_pct || 0).toFixed(2)}%
        </span>
        <span style="color:var(--text-muted);font-size:12px;margin-left:8px">${breakdown}</span>
      </div>
      <table class="pg-table">
        <thead><tr>
          <th style="width:42px">#</th><th>${isUS ? 'Symbol' : '코드'}</th><th>${isUS ? 'Name' : '종목명'}</th>
          <th class="r">${isUS ? 'Price' : '현재가'}</th><th class="r">${isUS ? 'Change' : '등락률'}</th><th class="r">${isUS ? 'Volume' : '거래대금'}</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
  box.querySelector('tbody').addEventListener('click', (e) => {
    const tr = e.target.closest('tr');
    if (!tr) return;
    console.log('[sector member click]', { code: tr.dataset.code, name: tr.dataset.name });
    openChartPanel(tr.dataset.code, tr.dataset.name);
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// DISCOVER PAGE (Phase 15 — 국내 종목 발굴, Stage 1 + Stage 2)
// ─────────────────────────────────────────────────────────────────────────────
const DISCOVER_STATE = {
  data: null,
  loading: false,
  pollTimer: null,
  market: 'all',
  activeTags: [],         // 활성화된 태그 필터 (AND 조건)
  sortKey: 'total_score', // 정렬 기준
  sortDir: 'desc',        // asc | desc
};

// ─────────────────────────────────────────────────────────────────────────────
// 📢 공시 페이지
// ─────────────────────────────────────────────────────────────────────────────
let _disclosureFilter = 'all'; // all, important, watchlist

// ─────────────────────────────────────────────────────────────────────────────
// 🤖 AI 에이전트 추천 페이지
// ─────────────────────────────────────────────────────────────────────────────
let _agentMarket = 'kr';

async function renderAgentPage() {
  _loadAgentMarket(_agentMarket);
}

async function _loadAgentMarket(market) {
  _agentMarket = market;
  const root = document.getElementById('agent-view');
  root.innerHTML = '<div class="pg-empty"><div class="pg-empty-title">로딩 중…</div></div>';

  try {
    const r = await fetch(`/api/agent/result?market=${market}`);
    if (r.status === 404) {
      root.innerHTML = `
        <div class="pg-wrap">
          <div class="pg-title">🤖 AI 에이전트 추천</div>
          <div class="pg-sub">규칙 기반 5개 에이전트가 뉴스·매크로·기술적·펀더멘탈을 순차 분석합니다.</div>
          <div class="agent-market-tabs">
            <button class="agent-tab ${market==='kr'?'active':''}" data-agent-mkt="kr">🇰🇷 국내</button>
            <button class="agent-tab ${market==='us'?'active':''}" data-agent-mkt="us">🇺🇸 미국</button>
          </div>
          <div class="pg-empty">
            <div class="pg-empty-title">${market==='us'?'US':'KR'} 결과 없음</div>
            <button class="agent-run-btn" id="agent-run-btn">🚀 지금 실행</button>
          </div>
        </div>`;
      document.getElementById('agent-run-btn').addEventListener('click', _runAgent);
      _bindAgentTabs();
      return;
    }
    const d = await r.json();
    _renderAgentResult(d);
  } catch(e) {
    root.innerHTML = `<div class="pg-empty">로딩 실패: ${_escHtml(e.message)}</div>`;
  }
}

function _bindAgentTabs() {
  document.querySelectorAll('.agent-tab[data-agent-mkt]').forEach(btn => {
    btn.addEventListener('click', () => _loadAgentMarket(btn.dataset.agentMkt));
  });
}

function _renderAgentResult(d) {
  const root = document.getElementById('agent-view');
  const agents = d.agents || {};
  const picks = d.final_picks || [];
  const macroMap = {'strong_bull':'🟢🟢 강한 상승','mild_bull':'🟢 약한 상승','neutral':'🟡 중립','mild_bear':'🔴 약한 하락','strong_bear':'🔴🔴 강한 하락'};
  const macroLabel = macroMap[agents.macro?.direction] || '🟡 중립';

  const mkt = d.market || _agentMarket || 'kr';
  let html = `<div class="pg-wrap">
    <div class="pg-title">🤖 AI 에이전트 추천 ${mkt==='us'?'🇺🇸':'🇰🇷'}</div>
    <div class="pg-sub">마지막 실행: ${_escHtml((d.timestamp || '').replace('T', ' ').slice(0,19))} · ${d.elapsed_seconds}초 소요</div>

    <div class="agent-market-tabs">
      <button class="agent-tab ${mkt==='kr'?'active':''}" data-agent-mkt="kr">🇰🇷 국내</button>
      <button class="agent-tab ${mkt==='us'?'active':''}" data-agent-mkt="us">🇺🇸 미국</button>
    </div>

    <div class="agent-pipeline-summary">
      <div class="agent-step">
        <div class="step-icon">📰</div>
        <div class="step-title">뉴스</div>
        <div class="step-detail">${agents.news?.articles || 0}건</div>
        <div class="step-result">${(agents.news?.hot_themes || []).join(', ') || '-'}</div>
      </div>
      <div class="agent-step">
        <div class="step-icon">📊</div>
        <div class="step-title">매크로</div>
        <div class="step-detail">${_escHtml(macroLabel)}</div>
        <div class="step-result">${(agents.macro?.factors || []).slice(0,2).map(_escHtml).join(' · ') || '-'}</div>
      </div>
      <div class="agent-step">
        <div class="step-icon">🎯</div>
        <div class="step-title">매칭</div>
        <div class="step-detail">${agents.match?.candidates || 0}종목</div>
        <div class="step-result">${(agents.match?.themes || []).slice(0,2).join(', ') || '-'}</div>
      </div>
      <div class="agent-step">
        <div class="step-icon">📈</div>
        <div class="step-title">기술</div>
        <div class="step-detail">${agents.technical?.passed || 0} 통과</div>
        <div class="step-result">${agents.technical?.pass_rate || '-'}</div>
      </div>
      <div class="agent-step">
        <div class="step-icon">💎</div>
        <div class="step-title">펀더멘탈</div>
        <div class="step-detail">${agents.fundamental?.output || 0}종목</div>
        <div class="step-result">최종 추천</div>
      </div>
    </div>`;

  if (picks.length) {
    html += '<div class="agent-picks-title">💡 최종 추천 종목</div><div class="agent-picks">';
    picks.forEach((p, idx) => {
      const chg = p.change_pct || 0;
      const sign = chg >= 0 ? '+' : '';
      const col = chg >= 0 ? '#FF3333' : '#33AA33';
      const reasons = [...(p.tech_reasons||[]), ...(p.fund_reasons||[])].slice(0,3);
      const tags = (p.swing_tags || []).slice(0,3);
      html += `<div class="agent-pick-card" data-code="${_escHtml(p.code)}" data-name="${_escHtml(p.name)}">
        <div class="pick-rank">#${idx+1}</div>
        <div class="pick-info">
          <div class="pick-name">${_escHtml(p.name)}${_marketBadgeFromItem(p)} <span class="pick-code">${_escHtml(p.code)}</span></div>
          <div class="pick-price" style="color:${col}">₩${(p.price||0).toLocaleString()} (${sign}${chg.toFixed(2)}%)</div>
          <div class="pick-score">점수: ${p.total_score||0}/105 · ${_escHtml(p.sector||'-')}</div>
          ${reasons.length ? `<div class="pick-reasons">${reasons.map(_escHtml).join(' · ')}</div>` : ''}
          ${tags.length ? `<div class="pick-tags">${tags.map(t => `<span class="pick-tag">${_escHtml(t)}</span>`).join('')}</div>` : ''}
        </div>
      </div>`;
    });
    html += '</div>';
  } else {
    html += '<div class="pg-empty"><div class="pg-empty-title">최종 추천 없음</div>조건에 맞는 종목이 없습니다.</div>';
  }

  html += '<button class="agent-run-btn" id="agent-run-btn">🚀 다시 실행</button>';
  html += '<div class="agent-disclaimer">⚠️ 규칙 기반 자동 분석. 투자 판단은 본인 책임.</div></div>';

  root.innerHTML = html;

  document.getElementById('agent-run-btn').addEventListener('click', _runAgent);
  _bindAgentTabs();
  root.querySelectorAll('.agent-pick-card').forEach(card => {
    card.addEventListener('click', () => openChartPanel(card.dataset.code, card.dataset.name, mkt));
  });
}

async function _runAgent() {
  const btn = document.getElementById('agent-run-btn');
  if (btn) { btn.textContent = '실행 중...'; btn.disabled = true; }
  const mkt = _agentMarket || 'kr';
  try {
    await fetch(`/api/agent/run?market=${mkt}`, { method: 'POST' });
  } catch {}
  const poll = setInterval(async () => {
    try {
      const s = await fetch('/api/agent/status').then(r => r.json());
      if (!s.running) {
        clearInterval(poll);
        _loadAgentMarket(mkt);
      }
    } catch {}
  }, 5000);
}

// ─────────────────────────────────────────────────────────────────────────────
// 📊 백테스트 페이지
// ─────────────────────────────────────────────────────────────────────────────
function renderBacktestPage() {
  const root = document.getElementById('backtest-view');
  root.innerHTML = `
    <div class="pg-wrap">
      <div class="pg-title">📊 백테스트</div>
      <div class="pg-sub">Stage 2 발굴 스냅샷(discover_results)을 재사용 — 실제 사용된 태그/점수를 그대로 검증합니다.</div>

      <div class="bt-config">
        <div class="bt-row">
          <label>전략</label>
          <select id="bt-strategy" class="bt-input">
            <option value="score">스코어 기반 (총점 N점 이상)</option>
            <option value="tag">태그 기반 (특정 신호 매칭)</option>
            <option value="combined">결합 (스코어 + 태그)</option>
          </select>
        </div>
        <div class="bt-row">
          <label>태그 선택 (Stage 2에서 실제 부여된 태그)</label>
          <div id="bt-tags-box" class="bt-tags-box">⏳ 태그 로딩 중…</div>
        </div>
        <div class="bt-row">
          <label>태그 로직</label>
          <select id="bt-tag-logic" class="bt-input">
            <option value="OR">OR (하나라도)</option>
            <option value="AND">AND (전부)</option>
          </select>
        </div>

        <div class="bt-grid">
          <div class="bt-row"><label>최소 스코어</label><input id="bt-min-score" type="number" class="bt-input" value="70"></div>
          <div class="bt-row"><label>보유 기간 (일)</label><input id="bt-hold" type="number" class="bt-input" value="5"></div>
          <div class="bt-row"><label>손절 (%)</label><input id="bt-stop" type="number" class="bt-input" value="-5"></div>
          <div class="bt-row"><label>익절 (%)</label><input id="bt-tp" type="number" class="bt-input" value="10"></div>
          <div class="bt-row"><label>조회 기간 (일)</label><input id="bt-lookback" type="number" class="bt-input" value="60"></div>
          <div class="bt-row"><label>최대 종목 수</label><input id="bt-max" type="number" class="bt-input" value="300"></div>
        </div>

        <button class="bt-run-btn" id="bt-run-btn">▶ 백테스트 실행</button>
      </div>
      <div id="bt-result"></div>
    </div>`;
  document.getElementById('bt-run-btn').addEventListener('click', _runBacktest);
  _loadBacktestTags();
}

async function _loadBacktestTags() {
  const box = document.getElementById('bt-tags-box');
  if (!box) return;
  try {
    const r = await fetch('/api/backtest/available_tags');
    const d = await r.json();
    const tags = d.tags || [];
    if (!tags.length) { box.innerHTML = '<span class="bt-empty">태그 없음</span>'; return; }
    box.innerHTML = tags.map(t =>
      `<label class="bt-tag-chip"><input type="checkbox" value="${_escHtml(t)}"> ${_escHtml(t)}</label>`
    ).join('');
  } catch(e) {
    box.innerHTML = `<span class="bt-empty">로드 실패: ${_escHtml(e.message)}</span>`;
  }
}

async function _runBacktest() {
  const resultDiv = document.getElementById('bt-result');
  const btn = document.getElementById('bt-run-btn');
  btn.disabled = true; btn.textContent = '⏳ 실행 중...';
  resultDiv.innerHTML = '<div class="bt-loading">⏳ 백테스트 실행 중... (최대 60초)</div>';

  const tags = Array.from(document.querySelectorAll('#bt-tags-box input:checked'))
    .map(cb => cb.value);

  const body = {
    strategy: document.getElementById('bt-strategy').value,
    tags: tags,
    tag_logic: document.getElementById('bt-tag-logic').value,
    min_score: parseInt(document.getElementById('bt-min-score').value) || 70,
    hold_days: parseInt(document.getElementById('bt-hold').value) || 5,
    stop_loss: parseFloat(document.getElementById('bt-stop').value) || -5,
    take_profit: parseFloat(document.getElementById('bt-tp').value) || 10,
    lookback_days: parseInt(document.getElementById('bt-lookback').value) || 60,
    max_stocks: parseInt(document.getElementById('bt-max').value) || 300,
  };

  try {
    const res = await fetch('/api/backtest', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (data.error) {
      resultDiv.innerHTML = `<div class="bt-error">에러: ${_escHtml(data.error)}</div>`;
    } else {
      _renderBacktestResult(data, resultDiv);
    }
  } catch(e) {
    resultDiv.innerHTML = `<div class="bt-error">요청 실패: ${_escHtml(e.message)}</div>`;
  } finally {
    btn.disabled = false; btn.textContent = '▶ 백테스트 실행';
  }
}

function _renderBacktestResult(data, container) {
  const s = data.summary || {};
  if (!s.total_trades) {
    container.innerHTML = '<div class="bt-error">조건에 맞는 매매 없음 — 조건을 완화해보세요.</div>';
    return;
  }
  const avgCol = s.avg_pnl >= 0 ? '#FF3333' : '#33AA33';
  const winCol = s.win_rate >= 50 ? '#FF3333' : '#33AA33';

  const srcLabel = data.source === 'discover_history'
    ? '🗂 Stage 2 스냅샷 재사용'
    : '📈 OHLCV 룰 폴백';
  let html = `
    <div class="bt-summary">
      <div class="bt-summary-title">📊 백테스트 결과 <span class="bt-source">${srcLabel}</span></div>
      <div class="bt-stat-grid">
        <div class="bt-stat"><div class="bt-stat-label">총 매매</div><div class="bt-stat-value">${s.total_trades}건</div></div>
        <div class="bt-stat"><div class="bt-stat-label">승률</div><div class="bt-stat-value" style="color:${winCol}">${s.win_rate}%</div></div>
        <div class="bt-stat"><div class="bt-stat-label">평균 수익</div><div class="bt-stat-value" style="color:${avgCol}">${s.avg_pnl >= 0 ? '+' : ''}${s.avg_pnl}%</div></div>
        <div class="bt-stat"><div class="bt-stat-label">중간값</div><div class="bt-stat-value">${s.median_pnl >= 0 ? '+' : ''}${s.median_pnl}%</div></div>
        <div class="bt-stat"><div class="bt-stat-label">최대 수익</div><div class="bt-stat-value" style="color:#FF3333">+${s.max_win}%</div></div>
        <div class="bt-stat"><div class="bt-stat-label">최대 손실</div><div class="bt-stat-value" style="color:#33AA33">${s.max_loss}%</div></div>
        <div class="bt-stat"><div class="bt-stat-label">손익비</div><div class="bt-stat-value">${s.profit_factor}</div></div>
        <div class="bt-stat"><div class="bt-stat-label">익절/손절/보유</div><div class="bt-stat-value" style="font-size:14px">${s.take_profit_count}/${s.stop_loss_count}/${s.hold_count}</div></div>
      </div>
    </div>`;

  const trades = data.trades || [];
  if (trades.length) {
    html += `<div class="bt-trades-title">최근 매매 내역 (${trades.length}건 중 50건 표시)</div>`;
    html += '<div class="bt-trades-table"><table><thead><tr>';
    html += '<th>종목</th><th>진입일</th><th>진입가</th><th>청산일</th><th>청산가</th><th>수익</th><th>사유</th>';
    html += '</tr></thead><tbody>';
    const reasonMap = {stop_loss: '🔻 손절', take_profit: '🔺 익절', hold: '⏹ 만기'};
    trades.slice(0, 50).forEach(t => {
      const col = t.pnl_pct >= 0 ? '#FF3333' : '#33AA33';
      html += `<tr data-code="${_escHtml(t.code)}" data-name="${_escHtml(t.name)}">
        <td>${_escHtml(t.name)}</td>
        <td>${_escHtml(t.entry_date)}</td>
        <td>₩${(t.entry_price || 0).toLocaleString()}</td>
        <td>${_escHtml(t.exit_date)}</td>
        <td>₩${(t.exit_price || 0).toLocaleString()}</td>
        <td style="color:${col};font-weight:700">${t.pnl_pct >= 0 ? '+' : ''}${t.pnl_pct}%</td>
        <td>${reasonMap[t.exit_reason] || t.exit_reason}</td>
      </tr>`;
    });
    html += '</tbody></table></div>';
  }
  container.innerHTML = html;
  container.querySelectorAll('.bt-trades-table tr[data-code]').forEach(tr => {
    tr.addEventListener('click', () => openChartPanel(tr.dataset.code, tr.dataset.name, 'kr'));
  });
}

// ── 상관관계 매트릭스 ──
function renderCorrelationPage() {
  const root = document.getElementById('correlation-view');
  root.innerHTML = `
    <div class="pg-wrap">
      <div class="pg-title">🔗 상관관계 매트릭스</div>
      <div class="pg-sub">포트폴리오·관심종목의 일간 수익률 상관관계 (최근 60일 OHLCV · Pearson).</div>
      <div class="bt-config">
        <div class="bt-row">
          <label>대상</label>
          <select id="corr-source" class="bt-input">
            <option value="both">포트폴리오 + 관심종목</option>
            <option value="portfolio">포트폴리오만</option>
            <option value="watchlist">관심종목만</option>
          </select>
        </div>
        <div class="bt-row">
          <label>기간 (일)</label>
          <input id="corr-days" type="number" class="bt-input" value="60">
        </div>
        <button class="bt-run-btn" id="corr-run-btn">▶ 상관관계 계산</button>
      </div>
      <div id="corr-result"></div>
    </div>`;
  document.getElementById('corr-run-btn').addEventListener('click', _runCorrelation);
  _runCorrelation();
}

async function _runCorrelation() {
  const box = document.getElementById('corr-result');
  const btn = document.getElementById('corr-run-btn');
  btn.disabled = true;
  box.innerHTML = '<div class="bt-loading">⏳ 계산 중…</div>';
  const source = document.getElementById('corr-source').value;
  const days = parseInt(document.getElementById('corr-days').value) || 60;
  try {
    const r = await fetch(`/api/correlation?source=${source}&days=${days}`);
    const d = await r.json();
    if (d.error) {
      box.innerHTML = `<div class="bt-error">${_escHtml(d.error)}</div>`;
    } else {
      _renderCorrMatrix(d, box);
    }
  } catch(e) {
    box.innerHTML = `<div class="bt-error">요청 실패: ${_escHtml(e.message)}</div>`;
  } finally {
    btn.disabled = false;
  }
}

function _renderCorrMatrix(data, container) {
  const codes = data.codes || [];
  const names = data.names || codes;
  const mat = data.matrix || [];
  if (!codes.length || !mat.length) {
    container.innerHTML = '<div class="bt-error">데이터 부족.</div>';
    return;
  }
  // 색상: -1 (녹) ~ 0 (중립) ~ +1 (적)
  const col = v => {
    if (v >= 0) {
      const a = Math.min(1, v);
      return `rgba(255,51,51,${0.15 + a * 0.7})`;
    }
    const a = Math.min(1, -v);
    return `rgba(51,170,51,${0.15 + a * 0.7})`;
  };
  let html = '<div class="corr-legend">' +
    '<span class="corr-leg-box" style="background:rgba(51,170,51,0.85)"></span> 음(-) 분산효과 有 ' +
    '<span class="corr-leg-box" style="background:rgba(128,128,128,0.2);margin-left:12px"></span> 0 ' +
    '<span class="corr-leg-box" style="background:rgba(255,51,51,0.85);margin-left:12px"></span> 양(+) 동반등락</div>';
  html += '<div class="corr-table-wrap"><table class="corr-table"><thead><tr><th></th>';
  codes.forEach((c, i) => {
    html += `<th title="${_escHtml(names[i])}">${_escHtml(names[i].slice(0, 8))}</th>`;
  });
  html += '</tr></thead><tbody>';
  mat.forEach((row, i) => {
    html += `<tr><th title="${_escHtml(names[i])}">${_escHtml(names[i].slice(0, 10))}</th>`;
    row.forEach((v, j) => {
      const style = `background:${col(v)};color:${Math.abs(v) > 0.55 ? '#fff' : 'var(--text)'}`;
      html += `<td style="${style}" title="${_escHtml(names[i])} ↔ ${_escHtml(names[j])}: ${v}">${v}</td>`;
    });
    html += '</tr>';
  });
  html += '</tbody></table></div>';

  // 상위/하위 페어
  const pairs = [];
  for (let i = 0; i < codes.length; i++) {
    for (let j = i + 1; j < codes.length; j++) {
      pairs.push({a: names[i], b: names[j], v: mat[i][j]});
    }
  }
  pairs.sort((x, y) => y.v - x.v);
  const top = pairs.slice(0, 5);
  const bot = pairs.slice(-5).reverse();
  html += '<div class="corr-pairs">';
  html += '<div class="corr-pair-col"><div class="corr-pair-title">🔴 동반등락 Top 5</div>';
  top.forEach(p => { html += `<div class="corr-pair-row"><span>${_escHtml(p.a)} ↔ ${_escHtml(p.b)}</span><b style="color:#FF3333">${p.v}</b></div>`; });
  html += '</div><div class="corr-pair-col"><div class="corr-pair-title">🟢 분산효과 Top 5</div>';
  bot.forEach(p => { html += `<div class="corr-pair-row"><span>${_escHtml(p.a)} ↔ ${_escHtml(p.b)}</span><b style="color:#33AA33">${p.v}</b></div>`; });
  html += '</div></div>';

  container.innerHTML = html;
}

// ── 추천 성과 검증 ──
let _RP_SOURCE = 'discover_kr';
let _RP_DAYS = 7;

function renderRecPerfPage() {
  const root = document.getElementById('recperf-view');
  root.innerHTML = `
    <div class="pg-wrap">
      <div class="pg-title">🎯 추천 성과 검증</div>
      <div class="pg-sub">종목발굴·AI추천 종목의 추천일 대비 실제 수익률을 OHLCV로 자동 검증합니다.</div>
      <div class="rp-tabs" id="rp-src-tabs">
        <button class="rp-tab active" data-src="discover_kr">🔍 발굴 KR</button>
        <button class="rp-tab" data-src="discover_us">🔍 발굴 US</button>
        <button class="rp-tab" data-src="agent_kr">🤖 AI KR</button>
        <button class="rp-tab" data-src="agent_us">🤖 AI US</button>
      </div>
      <div class="rp-period-tabs" id="rp-period-tabs">
        <button class="rp-period active" data-days="7">1주</button>
        <button class="rp-period" data-days="14">2주</button>
        <button class="rp-period" data-days="30">1개월</button>
      </div>
      <div id="rp-result"></div>
    </div>`;
  document.getElementById('rp-src-tabs').addEventListener('click', (e) => {
    const b = e.target.closest('.rp-tab'); if (!b) return;
    _RP_SOURCE = b.dataset.src;
    document.querySelectorAll('#rp-src-tabs .rp-tab').forEach(x => x.classList.toggle('active', x === b));
    _fetchRecPerf();
  });
  document.getElementById('rp-period-tabs').addEventListener('click', (e) => {
    const b = e.target.closest('.rp-period'); if (!b) return;
    _RP_DAYS = parseInt(b.dataset.days) || 7;
    document.querySelectorAll('#rp-period-tabs .rp-period').forEach(x => x.classList.toggle('active', x === b));
    _fetchRecPerf();
  });
  _fetchRecPerf();
}

async function _fetchRecPerf() {
  const box = document.getElementById('rp-result');
  box.innerHTML = '<div class="bt-loading">⏳ 성과 계산 중…</div>';
  try {
    const r = await fetch(`/api/recommendation/performance?source=${_RP_SOURCE}&days=${_RP_DAYS}`);
    const d = await r.json();
    if (d.error) { box.innerHTML = `<div class="bt-error">${_escHtml(d.error)}</div>`; return; }
    _renderRecPerf(d, box);
  } catch(e) {
    box.innerHTML = `<div class="bt-error">요청 실패: ${_escHtml(e.message)}</div>`;
  }
}

function _renderRecPerf(data, container) {
  const o = data.overall || {};
  let html = '';
  if (o.total_picks) {
    const avgCol = o.avg_pnl >= 0 ? '#FF3333' : '#33AA33';
    const totCol = o.total_return >= 0 ? '#FF3333' : '#33AA33';
    html += `
      <div class="rp-summary">
        <div class="rp-summary-title">전체 성과 (${data.lookback_days}일 누적)</div>
        <div class="rp-stat-grid">
          <div class="rp-stat"><div class="rp-stat-label">평균 수익</div><div class="rp-stat-value" style="color:${avgCol}">${o.avg_pnl >= 0 ? '+' : ''}${o.avg_pnl}%</div></div>
          <div class="rp-stat"><div class="rp-stat-label">승률</div><div class="rp-stat-value">${o.win_rate}%</div></div>
          <div class="rp-stat"><div class="rp-stat-label">총 추천</div><div class="rp-stat-value">${o.total_picks}건</div></div>
          <div class="rp-stat"><div class="rp-stat-label">최대 수익</div><div class="rp-stat-value" style="color:#FF3333">+${o.max_win}%</div></div>
          <div class="rp-stat"><div class="rp-stat-label">최대 손실</div><div class="rp-stat-value" style="color:#33AA33">${o.max_loss}%</div></div>
          <div class="rp-stat"><div class="rp-stat-label">누적 수익</div><div class="rp-stat-value" style="color:${totCol}">${o.total_return >= 0 ? '+' : ''}${o.total_return}%</div></div>
        </div>
      </div>`;
  }
  const snaps = data.daily_snapshots || [];
  if (!snaps.length) {
    html += `<div class="pg-empty"><div class="pg-empty-title">추천 이력 없음</div>
      Stage 2 또는 AI추천이 실행되면 상위 10종목이 자동 저장됩니다.</div>`;
    container.innerHTML = html; return;
  }
  for (const snap of snaps) {
    const dayCol = snap.avg_pnl >= 0 ? '#FF3333' : '#33AA33';
    html += `<div class="rp-day">
      <div class="rp-day-header">
        <span class="rp-day-date">📅 ${_escHtml(snap.date)}</span>
        <span class="rp-day-avg" style="color:${dayCol}">평균 ${snap.avg_pnl >= 0 ? '+' : ''}${snap.avg_pnl}% · 승 ${snap.win_count}/${snap.total_count}</span>
      </div>
      <div class="rp-picks">`;
    for (const p of (snap.picks || [])) {
      const pnlCol = p.final_pnl >= 0 ? '#FF3333' : '#33AA33';
      const d1Col = (p.d1_pnl || 0) >= 0 ? '#FF3333' : '#33AA33';
      const spark = _recSparkline(p.sparkline || []);
      const cur = p.market === 'us' ? '$' : '₩';
      const recP = p.rec_price ? p.rec_price.toLocaleString() : '—';
      const curP = p.current_price ? p.current_price.toLocaleString() : '—';
      const mktBadge = _marketBadge(p.market);
      html += `<div class="rp-pick" data-code="${_escHtml(p.code)}" data-name="${_escHtml(p.name)}" data-mkt="${_escHtml(p.market||'kr')}">
        <div class="rp-pick-rank">#${p.rank}</div>
        <div class="rp-pick-info">
          <div class="rp-pick-name">${_escHtml(p.name)} ${mktBadge}</div>
          <div class="rp-pick-score">${p.score || 0}점</div>
        </div>
        <div class="rp-pick-prices">
          <div class="rp-pick-price">추천 ${cur}${recP}</div>
          <div class="rp-pick-price">현재 ${cur}${curP}</div>
        </div>
        <div class="rp-pick-pnl">
          <div class="rp-pick-d1" style="color:${d1Col}">D+1 ${p.d1_pnl != null ? (p.d1_pnl >= 0 ? '+' : '') + p.d1_pnl + '%' : '—'}</div>
          <div class="rp-pick-final" style="color:${pnlCol}">${p.final_pnl >= 0 ? '+' : ''}${p.final_pnl}%</div>
        </div>
        <div class="rp-pick-spark">${spark}</div>
      </div>`;
    }
    html += '</div></div>';
  }
  container.innerHTML = html;
  container.querySelectorAll('.rp-pick').forEach(el => {
    el.addEventListener('click', () => openChartPanel(el.dataset.code, el.dataset.name, el.dataset.mkt));
  });
}

function _recSparkline(values) {
  if (!values.length) return '';
  const w = 80, h = 24;
  const min = Math.min(...values, 0), max = Math.max(...values, 0);
  const range = (max - min) || 1;
  const points = values.map((v, i) => {
    const x = (i / Math.max(values.length - 1, 1)) * w;
    const y = h - ((v - min) / range) * h;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const zeroY = h - ((0 - min) / range) * h;
  const color = values[values.length - 1] >= 0 ? '#FF3333' : '#33AA33';
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
    <line x1="0" y1="${zeroY.toFixed(1)}" x2="${w}" y2="${zeroY.toFixed(1)}" stroke="#444" stroke-width="0.5"/>
    <polyline points="${points}" fill="none" stroke="${color}" stroke-width="1.5"/>
  </svg>`;
}


async function renderDisclosurePage() {
  const root = document.getElementById('disclosure-view');
  if (!root.dataset.built) {
    root.dataset.built = '1';
    root.innerHTML = `
      <div class="pg-wrap">
        <div class="pg-title">📢 DART 공시</div>
        <div class="pg-sub">실시간 중요 공시 모니터링 (1분 간격 자동 갱신)</div>
        <div class="dc-market-toggle" id="disc-filter-bar">
          <button class="dc-market-btn active" data-filter="all">전체</button>
          <button class="dc-market-btn" data-filter="important">🔥 중요만</button>
        </div>
        <div id="disc-list" class="pg-empty"><div class="pg-empty-title">로딩 중…</div></div>
      </div>`;
    document.getElementById('disc-filter-bar').addEventListener('click', (e) => {
      const btn = e.target.closest('.dc-market-btn');
      if (!btn) return;
      _disclosureFilter = btn.dataset.filter;
      document.querySelectorAll('#disc-filter-bar .dc-market-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      _fetchDisclosures();
    });
  }
  _fetchDisclosures();
}

async function _fetchDisclosures() {
  const box = document.getElementById('disc-list');
  if (!box) return;
  try {
    const imp = _disclosureFilter === 'important' ? '&importance=critical,high' : '';
    const r = await fetch(`/api/disclosures?limit=100${imp}`);
    const d = await r.json();
    if (d.error) { box.innerHTML = `<div class="pg-empty">${_escHtml(d.error)}</div>`; return; }
    const items = d.items || [];
    if (!items.length) {
      box.innerHTML = '<div class="pg-empty"><div class="pg-empty-title">오늘 공시 없음</div></div>';
      return;
    }
    let html = '<div class="disc-grid">';
    for (const it of items) {
      const imp = it.importance || 'low';
      const score = it.score || 0;
      const emoji = score >= 10 ? '🚨🚨' : score >= 8 ? '🚨' : score >= 6 ? '📢' : '📄';
      const impClass = imp === 'critical' ? 'disc-critical'
                     : imp === 'high' ? 'disc-high'
                     : imp === 'medium' ? 'disc-medium' : '';
      const kw = (it.keywords || []).join(', ');
      const code = it.stock_code || '';
      const codeLink = code ? `<span class="disc-code" data-code="${_escHtml(code)}" style="cursor:pointer;color:#FFD700">${_escHtml(code)}</span>` : '';
      const scoreCls = score >= 10 ? 'score-critical' : score >= 6 ? 'score-high'
                     : score >= 4 ? 'score-medium' : 'score-low';
      html += `<div class="disc-item ${impClass}">
        <div class="disc-head">
          <span>${emoji}</span>
          <b>${_escHtml(it.corp_name || '')}</b>
          ${codeLink}
          <span class="disc-score ${scoreCls}">${score}점</span>
          <span class="disc-date">${_escHtml(it.rcept_dt || '')}</span>
        </div>
        <div class="disc-title"><a href="${_escHtml(it.dart_url || '')}" target="_blank" rel="noopener">${_escHtml(it.title || '')}</a></div>
        ${kw ? `<div class="disc-kw">🔑 ${_escHtml(kw)}</div>` : ''}
      </div>`;
    }
    html += '</div>';
    box.innerHTML = html;

    // 종목코드 클릭 → 차트 모달
    box.querySelectorAll('.disc-code').forEach(el => {
      el.addEventListener('click', () => {
        const c = el.dataset.code;
        if (c && /^\d{6}$/.test(c)) openChartPanel(c);
      });
    });
  } catch (err) {
    box.innerHTML = `<div class="pg-empty">${_escHtml(err.message)}</div>`;
  }
}

// 태그 매핑: details.*_signal → 태그명 + 색상 카테고리
const _TAG_MAP = {
  // bb_signal
  '과매수':     { color: 'bull',    buy: false },
  '과매도':     { color: 'warn',    buy: true  },  // 반등 기회
  '중립 상향':  { color: 'neutral', buy: false },
  '중립 하향':  { color: 'neutral', buy: false },
  '스퀴즈':     { color: 'warn',    buy: true  },  // 돌파 임박
  '밴드 확장':  { color: 'neutral', buy: false },
  // trend_signal
  '지지선 위':  { color: 'bull',    buy: true  },
  '저항선 돌파': { color: 'bull',   buy: true  },
  '저항선 하':  { color: 'neutral', buy: false },
  '지지선 이탈': { color: 'bear',   buy: false },
  // fib_signal
  '깊은 조정':  { color: 'bear',   buy: false },
  '중간 조정':  { color: 'warn',   buy: false },
  '일반 조정':  { color: 'neutral', buy: false },
  '약조정 구간': { color: 'warn',   buy: false },
  '신고가 근접': { color: 'bull',   buy: true  },
  '추세 전환':  { color: 'bear',   buy: false },
  // vol_signal
  '거래량 급증': { color: 'bull',   buy: true  },
  '거래량 급감': { color: 'bear',   buy: false },
  // RSI / MACD 태그
  '과매수_RSI':     { color: 'bull',    buy: false },
  '상승진행_RSI':   { color: 'bull',    buy: true  },
  '과매도_RSI':     { color: 'warn',    buy: true  },
  '과매도회복_RSI': { color: 'warn',    buy: true  },
  'MACD_골든크로스': { color: 'bull',   buy: true  },
  'MACD_데드크로스': { color: 'bear',   buy: false },
  'MACD_양전환':    { color: 'warn',   buy: true  },
  'MACD_상승강화':  { color: 'bull',   buy: true  },
  'MACD_양수유지':  { color: 'neutral', buy: false },
  'MACD_강세구간':  { color: 'bull',   buy: true  },
  // 다이버전스 태그
  '베어리시_RSI_다이버전스':  { color: 'bear', buy: false },
  '베어리시_MACD_다이버전스': { color: 'bear', buy: false },
  '불리시_RSI_다이버전스':   { color: 'warn', buy: true  },
  '불리시_MACD_다이버전스':  { color: 'warn', buy: true  },
  // 외국인 수급 태그
  '외국인_3일연속': { color: 'bull',   buy: true  },
  '외국인_5일연속': { color: 'bull',   buy: true  },
  '외국인_집중매수': { color: 'bull',  buy: true  },
  '외국인_대량매수': { color: 'bull',  buy: true  },
  '쌍끌이_매수':    { color: 'bull',   buy: true  },
  // 공시 이벤트 태그
  '공시_핵심이벤트': { color: 'gold',   buy: false },
  '공시_주요이벤트': { color: 'gold',   buy: true  },
  '공시_참고이벤트': { color: 'gold',   buy: false },
};

// 복합 필터 프리셋
const _TAG_PRESETS = [
  { emoji: '🎯', label: '스윙 매수 후보',  tags: ['지지선 위', '거래량 급증', 'MACD_강세구간'] },
  { emoji: '🚀', label: '강한 반등',       tags: ['MACD_골든크로스', '과매도회복_RSI', '거래량 급증'] },
  { emoji: '💪', label: '상승 가속',       tags: ['MACD_상승강화', '외국인_3일연속'] },
  { emoji: '🔥', label: '강세 추세',       tags: ['상승진행_RSI', 'MACD_강세구간'] },
  { emoji: '💎', label: '과매도 반등',      tags: ['과매도회복_RSI', '과매도_RSI', 'MACD_양전환'] },
  { emoji: '🌊', label: '외국인 집중 매수', tags: ['외국인_집중매수', '외국인_5일연속'] },
  { emoji: '⚡', label: '스마트머니',      tags: ['쌍끌이_매수', '외국인_3일연속'] },
];
const _TAG_COLORS = {
  bull:    { bg: '#FF333322', color: '#FF3333', border: '#FF333366' },
  bear:    { bg: '#33AA3322', color: '#33AA33', border: '#33AA3366' },
  neutral: { bg: '#3C3C3E',   color: '#aaa',    border: '#4C4C4E' },
  warn:    { bg: '#FFD70022', color: '#FFD700', border: '#FFD70066' },
  gold:    { bg: 'rgba(245,158,11,0.15)', color: '#f59e0b', border: 'rgba(245,158,11,0.3)' },
};

function _computeTags(it) {
  const tags = [];
  const d = it.details || {};
  for (const key of ['bb_signal', 'trend_signal', 'fib_signal', 'vol_signal']) {
    const sig = d[key];
    if (sig && _TAG_MAP[sig]) tags.push(sig);
  }
  // RSI/MACD 태그 (백엔드 Stage 2가 생성)
  for (const t of (d.rsi_macd_tags || [])) {
    if (_TAG_MAP[t]) tags.push(t);
  }
  return tags;
}

function _tagStyle(tag) {
  const cat = (_TAG_MAP[tag] || {}).color || 'neutral';
  const c = _TAG_COLORS[cat];
  return `background:${c.bg};color:${c.color};border:1px solid ${c.border}`;
}

async function renderDiscoverPage() {
  console.log('[발굴] renderDiscoverPage 진입');
  const root = document.getElementById('discover-view');
  if (!root.dataset.built) {
    root.dataset.built = '1';
    root.innerHTML = `
      <div class="pg-wrap">
        <div class="pg-title">종목 발굴 <span class="dc-stage-tag" id="dc-stage-tag">Stage 1</span></div>
        <div class="pg-sub">모멘텀·수급·밸류·기술적 분석·섹터를 종합 스코어링</div>

        <div class="dc-market-toggle" id="dc-market-toggle">
          <button class="dc-market-btn active" data-market="all">🌏 전체</button>
          <button class="dc-market-btn"        data-market="kr">🇰🇷 국내</button>
          <button class="dc-market-btn"        data-market="us">🇺🇸 미국</button>
        </div>

        <div class="dc-actions">
          <button class="dc-scan-btn" id="dc-scan-btn">🔬 Stage 2 상세 스캔</button>
          <button class="dc-refresh-btn" id="dc-refresh-btn" title="재로딩">↻</button>
          <span class="dc-status" id="dc-status">—</span>
        </div>

        <div class="dc-legend">
          <b>덜오른 보너스</b>란? 같은 섹터의 다른 종목들은 많이 올랐는데
          이 종목은 상대적으로 덜 올라, <b>따라잡기(catch-up) 가능성</b>이 있는
          종목에 최대 +10점을 부여합니다. 섹터 20일 평균 대비 격차가 클수록 높은 점수.
        </div>

        <div class="dc-search-bar">
          <span class="dc-search-icon">🔍</span>
          <input type="text" id="dc-search-input" class="dc-search-input"
                 placeholder="종목명 또는 코드 검색 (Stage 2 결과 내)" autocomplete="off">
          <span id="dc-search-count" class="dc-search-count"></span>
        </div>

        <div class="dc-filter-bar" id="dc-filter-bar">
          <span class="dc-filter-label">태그 필터</span>
          <button class="dc-tag-chip active" data-tag="">전체</button>
        </div>

        <div class="dc-sort-bar" id="dc-sort-bar">
          <span class="dc-sort-label">정렬</span>
          <button class="dc-sort-btn active" data-sort="total_score">총점 ▼</button>
          <button class="dc-sort-btn" data-sort="momentum">모멘텀</button>
          <button class="dc-sort-btn" data-sort="flow">수급</button>
          <button class="dc-sort-btn" data-sort="valuation">밸류</button>
          <button class="dc-sort-btn" data-sort="technical">기술</button>
          <button class="dc-sort-btn" data-sort="sector">섹터</button>
          <button class="dc-sort-btn" data-sort="undervalued_bonus">덜오른</button>
        </div>

        <div id="dc-progress-wrap" class="dc-progress-wrap" style="display:none;">
          <div class="dc-progress-msg" id="dc-progress-msg">시작 중…</div>
          <div class="dc-progress-track"><div class="dc-progress-fill" id="dc-progress-fill"></div></div>
          <div class="dc-progress-meta" id="dc-progress-meta">0 / 0</div>
        </div>

        <div id="dc-results"></div>

        <div class="dc-disclaimer">
          ※ 본 스코어링은 기술적 지표 기반 참고 자료이며 투자 추천이 아닙니다.
          Stage 2 최초 스캔은 200종목 상세 수집으로 2~3분 소요되며, 이후 1시간 캐시됩니다.
        </div>
      </div>`;
    document.getElementById('dc-scan-btn').addEventListener('click', _dcStartStage2);
    document.getElementById('dc-refresh-btn').addEventListener('click', () => _dcFetch(true));

    document.getElementById('dc-market-toggle').addEventListener('click', (e) => {
      const btn = e.target.closest('.dc-market-btn');
      if (!btn) return;
      const newMarket = btn.dataset.market;
      if (newMarket === DISCOVER_STATE.market) return;
      DISCOVER_STATE.market = newMarket;
      document.querySelectorAll('#dc-market-toggle .dc-market-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.market === newMarket);
      });
      DISCOVER_STATE.data = null;
      _dcFetch(true);
    });

    document.getElementById('dc-results').addEventListener('click', (e) => {
      // 상세 토글 버튼 먼저 처리 (openChartPanel 로 넘어가지 않도록 stop)
      const detailsBtn = e.target.closest('.dc-details-btn');
      if (detailsBtn) {
        e.stopPropagation();
        const card = detailsBtn.closest('.dc-card');
        if (card) {
          const panel = card.querySelector('.dc-details-panel');
          const open = panel.classList.toggle('open');
          detailsBtn.textContent = open ? '접기' : '상세';
        }
        return;
      }
      const card = e.target.closest('.dc-card');
      if (!card) return;
      // data-market 기반으로 차트 panel 시장 override (all 모드에서 KR/US 혼재)
      openChartPanel(card.dataset.code, card.dataset.name, card.dataset.market);
    });

    // 검색 입력
    document.getElementById('dc-search-input').addEventListener('input', (e) => {
      _dcSearchFilter(e.target.value);
    });

    // 태그 필터 바 클릭
    document.getElementById('dc-filter-bar').addEventListener('click', (e) => {
      const chip = e.target.closest('.dc-tag-chip');
      if (!chip) return;
      const tag = chip.dataset.tag;
      if (tag === '') {
        // "전체" → 모든 필터 초기화
        DISCOVER_STATE.activeTags = [];
      } else {
        const idx = DISCOVER_STATE.activeTags.indexOf(tag);
        if (idx >= 0) DISCOVER_STATE.activeTags.splice(idx, 1);
        else DISCOVER_STATE.activeTags.push(tag);
      }
      _dcUpdateFilterUI();
      if (DISCOVER_STATE.data) _dcRenderResults(DISCOVER_STATE.data);
    });

    // 정렬 바 클릭
    document.getElementById('dc-sort-bar').addEventListener('click', (e) => {
      const btn = e.target.closest('.dc-sort-btn');
      if (!btn) return;
      const key = btn.dataset.sort;
      if (DISCOVER_STATE.sortKey === key) {
        DISCOVER_STATE.sortDir = DISCOVER_STATE.sortDir === 'desc' ? 'asc' : 'desc';
      } else {
        DISCOVER_STATE.sortKey = key;
        DISCOVER_STATE.sortDir = 'desc';
      }
      _dcUpdateSortUI();
      if (DISCOVER_STATE.data) _dcRenderResults(DISCOVER_STATE.data);
    });

    // URL에서 초기 필터/정렬 복원
    _dcRestoreFromURL();
  }

  // 진행 중인 스캔이 있는지 폴링 재개
  _dcCheckProgressOnce();

  if (DISCOVER_STATE.data) {
    _dcRenderResults(DISCOVER_STATE.data);
  } else {
    _dcFetch(true);
  }
}

async function _dcFetch(showLoading) {
  if (DISCOVER_STATE.loading) return;
  DISCOVER_STATE.loading = true;
  const statusEl = document.getElementById('dc-status');
  if (showLoading) statusEl.textContent = '로딩 중…';
  console.log('[발굴] _dcFetch 시작, market=', DISCOVER_STATE.market);
  try {
    const r = await fetch(`/api/discover?market=${DISCOVER_STATE.market}`);
    console.log('[발굴] fetch 응답:', r.status);
    const d = await r.json();
    console.log('[발굴] 데이터:', d.stage, d.items?.length, '종목');
    if (!r.ok || d.error) throw new Error(d.error || ('HTTP ' + r.status));
    DISCOVER_STATE.data = d;
    _dcRenderResults(d);
    console.log('[발굴] 렌더 완료');
  } catch (err) {
    console.error('[발굴] 에러:', err);
    document.getElementById('dc-results').innerHTML =
      `<div class="pg-empty"><div class="pg-empty-title">로딩 실패</div>${_escHtml(err.message)}</div>`;
    statusEl.textContent = '실패';
  } finally {
    DISCOVER_STATE.loading = false;
  }
}

async function _dcStartStage2() {
  const btn = document.getElementById('dc-scan-btn');
  btn.disabled = true;
  try {
    const r = await fetch(`/api/discover/scan?market=${DISCOVER_STATE.market}`, { method: 'POST' });
    if (r.status === 404) {
      throw new Error('Stage 2 엔드포인트 없음 — 서버 재시작이 필요합니다.');
    }
    const raw = await r.text();
    let d;
    try { d = JSON.parse(raw); }
    catch { throw new Error(`서버 응답이 JSON 이 아님 (HTTP ${r.status})`); }
    if (!r.ok || d.error) throw new Error(d.error || ('HTTP ' + r.status));
    _dcShowProgress();
    _dcStartPolling();
  } catch (err) {
    alert('Stage 2 스캔 시작 실패: ' + err.message);
    btn.disabled = false;
  }
}

function _dcCheckProgressOnce() {
  // 캐시된 Stage 2 결과가 이미 있으면 진행바 불필요 → 폴링 재개 스킵
  if (DISCOVER_STATE.data && DISCOVER_STATE.data.stage === 2) return;
  fetch('/api/discover/progress').then(r => r.json()).then(s => {
    console.log('[발굴] progress 체크:', s.status);
    if (s.status === 'running' || s.status === 'starting') {
      _dcShowProgress();
      _dcStartPolling();
    }
  }).catch(() => {});
}

function _dcStartPolling() {
  if (DISCOVER_STATE.pollTimer) return;
  DISCOVER_STATE.pollTimer = setInterval(async () => {
    try {
      const r = await fetch('/api/discover/progress');
      const s = await r.json();
      _dcUpdateProgress(s);
      if (s.status === 'done' || s.status === 'error' || s.status === 'idle') {
        _dcStopPolling();
        document.getElementById('dc-scan-btn').disabled = false;
        if (s.status === 'done') {
          // 완료 → 결과 재로딩
          setTimeout(() => {
            _dcHideProgress();
            _dcFetch(false);
          }, 400);
        } else if (s.status === 'error') {
          document.getElementById('dc-progress-msg').textContent =
            '오류: ' + (s.error || '알 수 없는 오류');
        }
      }
    } catch {}
  }, 3000);
}

function _dcStopPolling() {
  if (DISCOVER_STATE.pollTimer) {
    clearInterval(DISCOVER_STATE.pollTimer);
    DISCOVER_STATE.pollTimer = null;
  }
}

function _dcShowProgress() {
  document.getElementById('dc-progress-wrap').style.display = 'block';
}
function _dcHideProgress() {
  document.getElementById('dc-progress-wrap').style.display = 'none';
}

function _dcUpdateProgress(s) {
  const msg = document.getElementById('dc-progress-msg');
  const fill = document.getElementById('dc-progress-fill');
  const meta = document.getElementById('dc-progress-meta');
  msg.textContent = s.message || s.status || '—';
  const total = s.total || 0;
  const prog = s.progress || 0;
  const pct = total > 0 ? (prog / total) * 100 : (s.status === 'running' ? 5 : 0);
  fill.style.width = pct + '%';
  const phaseMap = {
    kr_fetch: '🇰🇷 수집', kr_scoring: '🇰🇷 스코어링',
    us_fetch: '🇺🇸 수집', us_scoring: '🇺🇸 스코어링',
    kr_stage1: '🇰🇷 Stage1', us_stage1: '🇺🇸 Stage1',
  };
  const phaseLabel = phaseMap[s.phase] || s.phase || '';
  meta.textContent = total ? `${phaseLabel} ${prog} / ${total}` : (phaseLabel || '—');
}

function _dcRenderResults(data) {
  const statusEl = document.getElementById('dc-status');
  const stage = data.stage || 1;
  document.getElementById('dc-stage-tag').textContent = 'Stage ' + stage;
  const stageNote = stage === 1
    ? ' · ⚠ 수급/밸류/기술 점수는 Stage 2 스캔 후 산출'
    : '';
  const coverageInfo = data.kospi_count != null
    ? ` (KOSPI ${data.kospi_count} + KOSDAQ ${data.kosdaq_count})`
    : '';
  statusEl.textContent = `${data.updated_at} 기준 · ${data.total_scanned}종목 스캔${coverageInfo} → 상위 ${data.items.length}${stageNote}`;

  // Stage 2 가 이미 신선하면 버튼 비활성화(=재스캔 불필요)
  const btn = document.getElementById('dc-scan-btn');
  if (stage === 2) {
    btn.textContent = '🔬 Stage 2 (캐시됨, 강제 재스캔)';
  } else {
    btn.textContent = '🔬 Stage 2 상세 스캔';
  }
  btn.disabled = false;

  const box = document.getElementById('dc-results');
  let items = (data.items || []).map(it => ({
    ...it,
    _tags: _computeTags(it),
  }));

  // 태그 필터 바 동적 칩 생성 (모든 종목의 태그를 수집)
  _dcBuildTagChips(items);

  // 태그 필터 적용
  // 프리셋: OR 조건 (하나라도 포함), 개별 태그: AND 조건 (모두 포함)
  const activeTags = DISCOVER_STATE.activeTags;
  if (activeTags.length > 0) {
    const isPreset = _TAG_PRESETS.some(p =>
      p.tags.length === activeTags.length &&
      p.tags.every(t => activeTags.includes(t))
    );
    if (isPreset) {
      items = items.filter(it => activeTags.some(t => it._tags.includes(t)));
    } else {
      items = items.filter(it => activeTags.every(t => it._tags.includes(t)));
    }
  }

  // 정렬
  const sortKey = DISCOVER_STATE.sortKey;
  const sortDir = DISCOVER_STATE.sortDir === 'asc' ? 1 : -1;
  items.sort((a, b) => {
    let av, bv;
    if (sortKey === 'total_score') {
      av = a.total_score || 0; bv = b.total_score || 0;
    } else {
      av = (a.scores || {})[sortKey] ?? -999;
      bv = (b.scores || {})[sortKey] ?? -999;
    }
    return (bv - av) * sortDir;
  });

  // URL 업데이트
  _dcSaveToURL();

  const statusSuffix = activeTags.length
    ? ` · 필터: ${activeTags.join(', ')} (${items.length}건)`
    : '';
  statusEl.textContent += statusSuffix;

  if (!items.length) {
    box.innerHTML = '<div class="pg-empty">조건에 맞는 종목 없음 — 태그 필터를 확인하세요.</div>';
    return;
  }

  const barW = (v, max) => `${Math.min(Math.max(v || 0, 0) / max * 100, 100)}%`;
  const scoreText = v => (v == null ? '—' : String(v));
  const maxTotal = stage === 2 ? 105 : 35;

  // 렌더 성능: 상위 200개만 표시 (2,600개 전체 DOM 생성 방지)
  const displayItems = items.slice(0, 200);
  const hiddenCount = items.length - displayItems.length;

  let cardsHTML = displayItems.map((it, i) => {
    const sc = _escHtml(it.code);
    const sn = _escHtml(it.name || '');
    const sect = _escHtml(it.sector || '');
    const mk = (it.market || 'kr') === 'us' ? 'us' : 'kr';
    const flag = mk === 'us' ? '🇺🇸' : '🇰🇷';
    const chg = it.change_pct || 0;
    const col = chg > 0 ? '#FF3333' : chg < 0 ? '#33AA33' : 'var(--text-muted)';
    const sign = chg > 0 ? '+' : '';
    const price = mk === 'us'
      ? `$${Number(it.price || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
      : `₩${Number(it.price || 0).toLocaleString()}`;
    const vol = mk === 'us'
      ? `$${Number(it.volume_mn || 0).toLocaleString('en-US', { maximumFractionDigits: 0 })}M`
      : `${Number(it.volume_mn || 0).toLocaleString()}M`;
    const s = it.scores || {};
    const d = it.details || {};

    const bars = [];
    bars.push(`<div class="dc-bar-row"><span class="dc-bar-label">모멘텀</span>
      <div class="dc-bar"><div class="dc-bar-fill momentum" style="width:${barW(s.momentum, 20)}"></div></div>
      <span class="dc-bar-val">${scoreText(s.momentum)}</span></div>`);
    if (stage === 2) {
      bars.push(`<div class="dc-bar-row"><span class="dc-bar-label">수급</span>
        <div class="dc-bar"><div class="dc-bar-fill flow" style="width:${barW(s.flow, 25)}"></div></div>
        <span class="dc-bar-val">${scoreText(s.flow)}</span></div>`);
      bars.push(`<div class="dc-bar-row"><span class="dc-bar-label">밸류</span>
        <div class="dc-bar"><div class="dc-bar-fill valuation" style="width:${barW(s.valuation, 20)}"></div></div>
        <span class="dc-bar-val">${scoreText(s.valuation)}</span></div>`);
      bars.push(`<div class="dc-bar-row"><span class="dc-bar-label">기술</span>
        <div class="dc-bar"><div class="dc-bar-fill technical" style="width:${barW(s.technical, 15)}"></div></div>
        <span class="dc-bar-val">${scoreText(s.technical)}</span></div>`);
    }
    bars.push(`<div class="dc-bar-row"><span class="dc-bar-label">섹터</span>
      <div class="dc-bar"><div class="dc-bar-fill sector" style="width:${barW(s.sector, 15)}"></div></div>
      <span class="dc-bar-val">${scoreText(s.sector)}</span></div>`);
    if (stage === 2 && (s.undervalued_bonus || 0) > 0) {
      bars.push(`<div class="dc-bar-row"><span class="dc-bar-label bonus">덜오른</span>
        <div class="dc-bar"><div class="dc-bar-fill bonus" style="width:${barW(s.undervalued_bonus, 10)}"></div></div>
        <span class="dc-bar-val bonus">+${s.undervalued_bonus}</span></div>`);
    }

    const tags = [];
    if (stage === 2) {
      if (d.per != null) tags.push(`<span class="dc-tag">PER ${d.per}</span>`);
      if (d.pbr != null) tags.push(`<span class="dc-tag">PBR ${d.pbr}</span>`);
    }
    // 시그널 기반 스타일 태그 (색상 카테고리 반영)
    for (const t of (it._tags || [])) {
      tags.push(`<span class="dc-tag" style="${_tagStyle(t)}">${_escHtml(t)}</span>`);
    }

    const detailsPanelHtml = _dcBuildDetailsPanel(it, stage);

    return `<div class="dc-card" data-code="${sc}" data-name="${sn}" data-market="${mk}">
      <div class="dc-card-row">
        <div class="dc-rank">#${i + 1}</div>
        <div class="dc-main">
          <div class="dc-head">
            <span class="dc-flag">${flag}</span>
            <span class="dc-name">${sn}</span>${_marketBadgeFromItem(it)}
            <span class="dc-code">${sc}</span>
            ${sect ? `<span class="dc-sector">${sect}</span>` : ''}
          </div>
          <div class="dc-price">
            <span class="dc-price-val">${price}</span>
            <span class="dc-chg" style="color:${col}">${sign}${chg.toFixed(2)}%</span>
            <span class="dc-vol">${vol}</span>
          </div>
          ${tags.length ? `<div class="dc-tags">${tags.join('')}</div>` : ''}
        </div>
        <div class="dc-score">
          <div class="dc-score-val">${it.total_score}</div>
          <div class="dc-score-label">/ ${maxTotal}</div>
        </div>
        <div class="dc-bars">
          ${bars.join('')}
        </div>
        <button class="dc-details-btn" title="점수 상세 설명">상세</button>
      </div>
      <div class="dc-details-panel">${detailsPanelHtml}</div>
    </div>`;
  }).join('');

  if (hiddenCount > 0) {
    cardsHTML += `<div class="pg-empty" style="margin-top:12px;font-size:13px">
      상위 200종목 표시 중 (전체 ${items.length}종목 중 ${hiddenCount}개 생략)
    </div>`;
  }
  box.innerHTML = cardsHTML;
}

function _dcBuildDetailsPanel(it, stage) {
  const expl = it.explanations || {};
  const sc = it.scores || {};
  const sections = [
    { key: 'momentum',  label: '모멘텀',     max: 20, color: '#FF6B6B' },
    { key: 'sector',    label: '섹터',       max: 15, color: '#FF9500' },
  ];
  if (stage === 2) {
    sections.splice(1, 0,
      { key: 'flow',       label: '수급',     max: 25, color: '#4488FF' },
      { key: 'valuation',  label: '밸류',     max: 20, color: '#00CC66' },
      { key: 'technical',  label: '기술',     max: 15, color: '#FFD700' },
    );
    sections.push({ key: 'bonus',  label: '덜오른 보너스', max: 10, color: '#E066FF',
                    scoreKey: 'undervalued_bonus' });
  }

  let html = '';
  for (const sec of sections) {
    const rows = expl[sec.key];
    const scoreKey = sec.scoreKey || sec.key;
    const pts = sc[scoreKey] || 0;
    html += `<div class="dc-dp-section">
      <div class="dc-dp-header">
        <span class="dc-dp-dot" style="background:${sec.color}"></span>
        <span class="dc-dp-title">${sec.label}</span>
        <span class="dc-dp-total">${pts} / ${sec.max}</span>
      </div>`;
    if (Array.isArray(rows) && rows.length) {
      html += '<div class="dc-dp-rows">';
      for (const r of rows) {
        html += `<div class="dc-dp-row">
          <span class="dc-dp-label">${_escHtml(r.label)}</span>
          <span class="dc-dp-detail">${_escHtml(r.detail)}</span>
          <span class="dc-dp-pts">${r.pts}/${r.max}</span>
        </div>`;
      }
      html += '</div>';
    } else {
      html += `<div class="dc-dp-empty">${
        stage === 2 && sec.key !== 'momentum' && sec.key !== 'sector'
          ? 'Stage 2 재스캔을 실행하면 상세 내역이 표시됩니다.'
          : '상세 정보 없음'
      }</div>`;
    }
    html += '</div>';
  }
  return html;
}

// ── 태그 필터 헬퍼 ──
let _showAllTags = false;

function _dcBuildTagChips(items) {
  const bar = document.getElementById('dc-filter-bar');
  if (!bar) return;
  const freq = {};
  items.forEach(it => (it._tags || []).forEach(t => freq[t] = (freq[t] || 0) + 1));
  const sorted = Object.entries(freq).sort((a, b) => b[1] - a[1]);

  // 매수 vs 기타 분류
  const buyTags = sorted.filter(([t]) => (_TAG_MAP[t] || {}).buy);
  const otherTags = sorted.filter(([t]) => !(_TAG_MAP[t] || {}).buy);

  let html = '';

  // 프리셋 버튼
  html += '<div class="preset-bar">';
  for (const p of _TAG_PRESETS) {
    const isActive = p.tags.every(t => DISCOVER_STATE.activeTags.includes(t))
                     && DISCOVER_STATE.activeTags.length === p.tags.length;
    // 프리셋 매칭 종목 수 표시
    const presetCount = items.filter(it =>
      p.tags.some(t => (it._tags || []).includes(t))
    ).length;
    html += `<button class="preset-btn ${isActive ? 'active' : ''}" data-preset="${_escHtml(p.label)}">${p.emoji} ${_escHtml(p.label)} <span class="dc-chip-count">${presetCount}</span></button>`;
  }
  html += `<button class="dc-tag-chip ${DISCOVER_STATE.activeTags.length === 0 ? 'active' : ''}" data-tag="" style="margin-left:auto">전체 초기화</button>`;
  html += '</div>';

  // 📈 매수 신호 섹션
  html += '<div class="tag-section"><span class="tag-section-label">📈 매수 신호</span>';
  for (const [tag, cnt] of buyTags) {
    const isActive = DISCOVER_STATE.activeTags.includes(tag);
    html += `<button class="dc-tag-chip ${isActive ? 'active' : ''}" data-tag="${_escHtml(tag)}" style="${isActive ? '' : _tagStyle(tag)}">${_escHtml(tag)} <span class="dc-chip-count">${cnt}</span></button>`;
  }
  html += '</div>';

  // ▼ 전체 태그 (토글)
  if (otherTags.length) {
    html += `<button class="tag-toggle-all" id="tag-toggle-all">${_showAllTags ? '▲ 접기' : '▼ 전체 태그 보기 (' + otherTags.length + ')'}</button>`;
    html += `<div class="tag-section tag-other" style="display:${_showAllTags ? 'flex' : 'none'}" id="tag-other-section">`;
    html += '<span class="tag-section-label bearish">📉 기타</span>';
    for (const [tag, cnt] of otherTags) {
      const isActive = DISCOVER_STATE.activeTags.includes(tag);
      html += `<button class="dc-tag-chip ${isActive ? 'active' : ''}" data-tag="${_escHtml(tag)}" style="${isActive ? '' : _tagStyle(tag)}">${_escHtml(tag)} <span class="dc-chip-count">${cnt}</span></button>`;
    }
    html += '</div>';
  }

  bar.innerHTML = html;

  // 토글 이벤트
  document.getElementById('tag-toggle-all')?.addEventListener('click', () => {
    _showAllTags = !_showAllTags;
    const sec = document.getElementById('tag-other-section');
    const btn = document.getElementById('tag-toggle-all');
    if (sec) sec.style.display = _showAllTags ? 'flex' : 'none';
    if (btn) btn.textContent = _showAllTags ? '▲ 접기' : `▼ 전체 태그 보기 (${otherTags.length})`;
  });

  // 프리셋 이벤트
  bar.querySelector('.preset-bar')?.addEventListener('click', (e) => {
    const btn = e.target.closest('.preset-btn');
    if (!btn) return;
    const presetLabel = btn.dataset.preset;
    const preset = _TAG_PRESETS.find(p => p.label === presetLabel);
    if (!preset) return;
    // 현재 프리셋과 동일하면 해제, 아니면 적용
    const isAlreadyActive = preset.tags.every(t => DISCOVER_STATE.activeTags.includes(t))
                            && DISCOVER_STATE.activeTags.length === preset.tags.length;
    if (isAlreadyActive) {
      DISCOVER_STATE.activeTags = [];
    } else {
      DISCOVER_STATE.activeTags = [...preset.tags];
    }
    if (DISCOVER_STATE.data) _dcRenderResults(DISCOVER_STATE.data);
  });
}

function _dcUpdateFilterUI() {
  const bar = document.getElementById('dc-filter-bar');
  if (!bar) return;
  bar.querySelectorAll('.dc-tag-chip').forEach(chip => {
    const tag = chip.dataset.tag;
    const isActive = tag === '' ? DISCOVER_STATE.activeTags.length === 0
                                : DISCOVER_STATE.activeTags.includes(tag);
    chip.classList.toggle('active', isActive);
  });
}

function _dcSearchFilter(query) {
  const q = (query || '').trim().toLowerCase();
  const cards = document.querySelectorAll('#dc-results .dc-card');
  let visible = 0;
  cards.forEach(card => {
    if (!q) { card.style.display = ''; visible++; return; }
    const name = (card.dataset.name || '').toLowerCase();
    const code = (card.dataset.code || '').toLowerCase();
    if (name.includes(q) || code.includes(q)) {
      card.style.display = '';
      visible++;
    } else {
      card.style.display = 'none';
    }
  });
  const el = document.getElementById('dc-search-count');
  if (el) el.textContent = q ? `${visible}개 일치` : '';
}

function _dcUpdateSortUI() {
  const bar = document.getElementById('dc-sort-bar');
  if (!bar) return;
  bar.querySelectorAll('.dc-sort-btn').forEach(btn => {
    const key = btn.dataset.sort;
    const isActive = key === DISCOVER_STATE.sortKey;
    btn.classList.toggle('active', isActive);
    if (isActive) {
      const arrow = DISCOVER_STATE.sortDir === 'desc' ? ' ▼' : ' ▲';
      const label = btn.textContent.replace(/\s*[▼▲]$/, '');
      btn.textContent = label + arrow;
    } else {
      btn.textContent = btn.textContent.replace(/\s*[▼▲]$/, '');
    }
  });
}

function _dcSaveToURL() {
  const params = new URLSearchParams(window.location.search);
  if (DISCOVER_STATE.activeTags.length) params.set('tags', DISCOVER_STATE.activeTags.join(','));
  else params.delete('tags');
  if (DISCOVER_STATE.sortKey !== 'total_score') params.set('sort', DISCOVER_STATE.sortKey);
  else params.delete('sort');
  if (DISCOVER_STATE.sortDir !== 'desc') params.set('order', DISCOVER_STATE.sortDir);
  else params.delete('order');
  const qs = params.toString();
  const newUrl = window.location.pathname + (qs ? '?' + qs : '');
  history.replaceState(null, '', newUrl);
}

function _dcRestoreFromURL() {
  const params = new URLSearchParams(window.location.search);
  const tags = params.get('tags');
  if (tags) DISCOVER_STATE.activeTags = tags.split(',').filter(Boolean);
  const sort = params.get('sort');
  if (sort) DISCOVER_STATE.sortKey = sort;
  const order = params.get('order');
  if (order === 'asc') DISCOVER_STATE.sortDir = 'asc';
  _dcUpdateSortUI();
}

// ─────────────────────────────────────────────────────────────────────────────
// CALENDAR PAGE (Phase 16 — 경제지표 + 실적발표)
// ─────────────────────────────────────────────────────────────────────────────
const CAL_STATE = { tab: 'economic', econ: null, earn: null };

async function renderCalendarPage() {
  const root = document.getElementById('calendar-view');
  if (!root.dataset.built) {
    root.dataset.built = '1';
    root.innerHTML = `
      <div class="pg-wrap">
        <div class="pg-title">📅 경제지표 · 실적 캘린더</div>
        <div class="pg-sub">이번 주 월요일 ~ 다음 주 금요일 (2주) · 6시간 캐시</div>

        <div class="cal-tabs" id="cal-tabs">
          <button class="cal-tab active" data-tab="economic">📊 경제지표</button>
          <button class="cal-tab"        data-tab="earnings">💼 실적발표</button>
          <button class="cal-tab"        data-tab="all">🌐 전체</button>
        </div>

        <div id="cal-content"><div class="pg-empty">로딩 중…</div></div>
      </div>`;
    document.getElementById('cal-tabs').addEventListener('click', (e) => {
      const btn = e.target.closest('.cal-tab');
      if (!btn) return;
      CAL_STATE.tab = btn.dataset.tab;
      document.querySelectorAll('#cal-tabs .cal-tab').forEach(b =>
        b.classList.toggle('active', b.dataset.tab === CAL_STATE.tab));
      _calRender();
    });
    document.getElementById('cal-content').addEventListener('click', (e) => {
      const row = e.target.closest('.cal-earning-row');
      if (!row) return;
      if (e.target.closest('.cal-dart-link')) return;  // DART 링크는 별도
      const code = row.dataset.code;
      const market = row.dataset.market;
      if (code) openChartPanel(code, row.dataset.name || code, market);
    });
  }
  _calRender();
}

async function _calRender() {
  const box = document.getElementById('cal-content');
  try {
    if (CAL_STATE.tab === 'economic') {
      if (!CAL_STATE.econ) {
        box.innerHTML = '<div class="pg-empty">경제지표 로딩 중…</div>';
        const r = await fetch('/api/calendar/economic');
        const d = await r.json();
        if (!r.ok || d.error) throw new Error(d.error || ('HTTP ' + r.status));
        CAL_STATE.econ = d;
      }
      box.innerHTML = _calRenderEconomic(CAL_STATE.econ);
    } else if (CAL_STATE.tab === 'earnings') {
      if (!CAL_STATE.earn) {
        box.innerHTML = '<div class="pg-empty">실적 캘린더 로딩 중…</div>';
        const r = await fetch('/api/calendar/earnings');
        const d = await r.json();
        if (!r.ok || d.error) throw new Error(d.error || ('HTTP ' + r.status));
        CAL_STATE.earn = d;
      }
      box.innerHTML = _calRenderEarnings(CAL_STATE.earn);
    } else {
      // all: 두 소스 모두 로드 후 통합
      if (!CAL_STATE.econ || !CAL_STATE.earn) {
        box.innerHTML = '<div class="pg-empty">로딩 중…</div>';
        const [re, rf] = await Promise.all([
          fetch('/api/calendar/economic').then(x => x.json()),
          fetch('/api/calendar/earnings').then(x => x.json()),
        ]);
        CAL_STATE.econ = re;
        CAL_STATE.earn = rf;
      }
      box.innerHTML = _calRenderAll(CAL_STATE.econ, CAL_STATE.earn);
    }
  } catch (err) {
    box.innerHTML = `<div class="pg-empty"><div class="pg-empty-title">로딩 실패</div>${_escHtml(err.message)}</div>`;
  }
}

function _calFormatDateKR(dateStr) {
  if (!dateStr) return '미정';
  const d = new Date(dateStr + 'T00:00:00');
  if (isNaN(d)) return dateStr;
  const days = ['일', '월', '화', '수', '목', '금', '토'];
  return `${d.getMonth() + 1}/${d.getDate()} (${days[d.getDay()]})`;
}

function _calGroupByDate(items) {
  const grouped = {};
  items.forEach(e => {
    const date = e.date || '미정';
    (grouped[date] = grouped[date] || []).push(e);
  });
  return Object.entries(grouped).sort((a, b) => a[0].localeCompare(b[0]));
}

function _calRenderEconomic(data) {
  const events = data.events || [];
  if (!events.length) {
    return '<div class="pg-empty">예정된 경제지표 발표가 없습니다.</div>';
  }
  const today = now_kst_date();
  let html = `<div class="cal-meta">${data.from} ~ ${data.to} · ${data.count}건 (전체 ${data.raw_count}건 중 주요국+중요도 필터)</div>`;
  for (const [date, evs] of _calGroupByDate(events)) {
    const isToday = date === today;
    html += `<div class="cal-date-group${isToday ? ' today' : ''}">
      <div class="cal-date-header">
        <span class="cal-date">${_calFormatDateKR(date)}</span>
        ${isToday ? '<span class="cal-today-badge">오늘</span>' : ''}
        <span class="cal-event-count">${evs.length}건</span>
      </div>`;
    for (const e of evs) {
      const hasActual = e.actual != null;
      const surprise = hasActual && e.estimate != null
        ? (e.actual > e.estimate ? 'positive' : e.actual < e.estimate ? 'negative' : 'neutral')
        : '';
      html += `<div class="cal-event-row ${surprise}">
        <span class="cal-impact">${e.impact_emoji}</span>
        <span class="cal-time">${e.time || '—'}</span>
        <span class="cal-country">${e.country_kr}</span>
        <span class="cal-event-name">${_escHtml(e.event_kr || e.event || '')}</span>
        <div class="cal-values">
          ${hasActual ? `<span class="cal-actual ${surprise}">실제 ${e.actual}${e.unit || ''}</span>` : ''}
          ${e.estimate != null ? `<span class="cal-estimate">예상 ${e.estimate}${e.unit || ''}</span>` : ''}
          ${e.prev != null ? `<span class="cal-prev">이전 ${e.prev}${e.unit || ''}</span>` : ''}
        </div>
      </div>`;
    }
    html += '</div>';
  }
  return html;
}

function _calRenderEarnings(data) {
  const earnings = data.earnings || [];
  if (!earnings.length) {
    return '<div class="pg-empty">예정된 실적발표가 없습니다.</div>';
  }
  const today = now_kst_date();
  const us = earnings.filter(x => x.market === 'us').length;
  const kr = earnings.filter(x => x.market === 'kr').length;
  let html = `<div class="cal-meta">${data.from} ~ ${data.to} · 🇺🇸 ${us}건 + 🇰🇷 ${kr}건 = ${data.count}건</div>`;
  for (const [date, items] of _calGroupByDate(earnings)) {
    const isToday = date === today;
    html += `<div class="cal-date-group${isToday ? ' today' : ''}">
      <div class="cal-date-header">
        <span class="cal-date">${_calFormatDateKR(date)}</span>
        ${isToday ? '<span class="cal-today-badge">오늘</span>' : ''}
        <span class="cal-event-count">${items.length}건</span>
      </div>`;
    for (const e of items) {
      const safeName = _escHtml(e.name || '');
      const safeSym = _escHtml(e.symbol || '');
      const epsStr = e.eps_actual != null
        ? `EPS 실제 $${e.eps_actual}`
        : (e.eps_estimate != null ? `EPS 예상 $${e.eps_estimate}` : '');
      const revStr = e.revenue_estimate != null
        ? `매출 예상 $${(e.revenue_estimate / 1e9).toFixed(1)}B`
        : '';
      const reportTag = e.report_type ? `<span class="cal-report-type">${_escHtml(e.report_type)}</span>` : '';
      const dartLink = e.dart_link
        ? `<a href="${_escHtml(e.dart_link)}" target="_blank" rel="noopener" class="cal-dart-link">DART</a>`
        : '';
      html += `<div class="cal-earning-row" data-code="${safeSym}" data-name="${safeName}" data-market="${e.market}">
        <span class="cal-flag">${e.flag}</span>
        <div class="cal-earning-info">
          <span class="cal-earning-name">${safeName}</span>
          <span class="cal-earning-symbol">${safeSym}</span>
          ${e.time ? `<span class="cal-earning-time">${_escHtml(e.time)}</span>` : ''}
        </div>
        <div class="cal-earning-est">
          ${epsStr ? `<div>${epsStr}</div>` : ''}
          ${revStr ? `<div>${revStr}</div>` : ''}
        </div>
        ${reportTag}
        ${dartLink}
      </div>`;
    }
    html += '</div>';
  }
  return html;
}

function _calRenderAll(econData, earnData) {
  // 날짜를 기준으로 두 종류의 항목을 병합
  const events = (econData.events || []).map(e => ({ __kind: 'econ', ...e }));
  const earnings = (earnData.earnings || []).map(e => ({ __kind: 'earn', ...e }));
  const combined = [...events, ...earnings];
  if (!combined.length) return '<div class="pg-empty">표시할 일정이 없습니다.</div>';

  const today = now_kst_date();
  let html = `<div class="cal-meta">경제지표 ${events.length}건 + 실적 ${earnings.length}건</div>`;
  for (const [date, items] of _calGroupByDate(combined)) {
    const isToday = date === today;
    html += `<div class="cal-date-group${isToday ? ' today' : ''}">
      <div class="cal-date-header">
        <span class="cal-date">${_calFormatDateKR(date)}</span>
        ${isToday ? '<span class="cal-today-badge">오늘</span>' : ''}
        <span class="cal-event-count">${items.length}건</span>
      </div>`;
    for (const e of items) {
      if (e.__kind === 'econ') {
        html += `<div class="cal-event-row">
          <span class="cal-impact">${e.impact_emoji}</span>
          <span class="cal-time">${e.time || '—'}</span>
          <span class="cal-country">${e.country_kr}</span>
          <span class="cal-event-name">${_escHtml(e.event_kr || e.event || '')}</span>
          <div class="cal-values">
            ${e.estimate != null ? `<span class="cal-estimate">예상 ${e.estimate}${e.unit || ''}</span>` : ''}
          </div>
        </div>`;
      } else {
        const safeName = _escHtml(e.name || '');
        const safeSym = _escHtml(e.symbol || '');
        const epsStr = e.eps_estimate != null ? `EPS 예상 $${e.eps_estimate}` : '';
        const reportTag = e.report_type ? `<span class="cal-report-type">${_escHtml(e.report_type)}</span>` : '';
        html += `<div class="cal-earning-row" data-code="${safeSym}" data-name="${safeName}" data-market="${e.market}">
          <span class="cal-flag">${e.flag}</span>
          <div class="cal-earning-info">
            <span class="cal-earning-name">${safeName}</span>
            <span class="cal-earning-symbol">${safeSym}</span>
            ${e.time ? `<span class="cal-earning-time">${_escHtml(e.time)}</span>` : ''}
          </div>
          <div class="cal-earning-est">${epsStr}</div>
          ${reportTag}
        </div>`;
      }
    }
    html += '</div>';
  }
  return html;
}

function now_kst_date() {
  // 브라우저 로컬 → KST (UTC+9) 로 환산해 YYYY-MM-DD 반환
  const d = new Date();
  const utc = d.getTime() + d.getTimezoneOffset() * 60000;
  const kst = new Date(utc + 9 * 3600 * 1000);
  return kst.toISOString().slice(0, 10);
}

// ─────────────────────────────────────────────────────────────────────────────
// RESEARCH PAGE (Phase 17)
// ─────────────────────────────────────────────────────────────────────────────
const RES_STATE = { tab: 'sector', recMarket: 'kr', cache: {} };

async function renderResearchPage() {
  const root = document.getElementById('research-view');
  if (!root.dataset.built) {
    root.dataset.built = '1';
    root.innerHTML = `
      <div class="pg-wrap">
        <div class="pg-title">📑 리서치</div>
        <div class="pg-sub">네이버 금융 증권사 리포트 수집 + 컨센서스 기반 추천</div>

        <div class="res-tabs" id="res-tabs">
          <button class="res-tab active" data-tab="sector">🏭 섹터 리포트</button>
          <button class="res-tab"        data-tab="company">🏢 기업 리포트</button>
          <button class="res-tab"        data-tab="recommend">⭐ AI 추천</button>
        </div>

        <div id="res-content"><div class="pg-empty">로딩 중…</div></div>
      </div>`;
    document.getElementById('res-tabs').addEventListener('click', (e) => {
      const btn = e.target.closest('.res-tab');
      if (!btn) return;
      RES_STATE.tab = btn.dataset.tab;
      document.querySelectorAll('#res-tabs .res-tab').forEach(b =>
        b.classList.toggle('active', b.dataset.tab === RES_STATE.tab));
      _resRender();
    });
    document.getElementById('res-content').addEventListener('click', (e) => {
      // PDF / 외부링크는 그대로 통과
      if (e.target.closest('a')) return;
      // 시장 토글
      const mBtn = e.target.closest('.res-market-btn');
      if (mBtn) {
        RES_STATE.recMarket = mBtn.dataset.market;
        _resRender();
        return;
      }
      // 카드 클릭 → 차트 패널
      const card = e.target.closest('[data-code]');
      if (card && card.dataset.code) {
        openChartPanel(card.dataset.code, card.dataset.name || '',
                       card.dataset.market || undefined);
      }
    });
  }
  _resRender();
}

async function _resRender() {
  const box = document.getElementById('res-content');
  try {
    if (RES_STATE.tab === 'sector') {
      const d = RES_STATE.cache.sector
        || (RES_STATE.cache.sector = await fetch('/api/research/sectors').then(r => r.json()));
      box.innerHTML = _resRenderSectors(d);
    } else if (RES_STATE.tab === 'company') {
      const d = RES_STATE.cache.company
        || (RES_STATE.cache.company = await fetch('/api/research/companies').then(r => r.json()));
      box.innerHTML = _resRenderCompanies(d);
    } else {
      const mkt = RES_STATE.recMarket;
      const key = 'rec_' + mkt;
      if (!RES_STATE.cache[key]) {
        box.innerHTML = '<div class="pg-empty">분석 중… (최초 호출은 5~10초 소요)</div>';
        const url = mkt === 'us' ? '/api/research/us_recommend' : '/api/research/recommend';
        RES_STATE.cache[key] = await fetch(url).then(r => r.json());
      }
      box.innerHTML = _resRenderRecommend(RES_STATE.cache[key], mkt);
    }
  } catch (err) {
    box.innerHTML = `<div class="pg-empty"><div class="pg-empty-title">로딩 실패</div>${_escHtml(err.message)}</div>`;
  }
}

function _resRenderSectors(d) {
  const reports = d.reports || [];
  const freq = d.sector_frequency || [];
  if (!reports.length) return '<div class="pg-empty">섹터 리포트 없음</div>';

  const maxFreq = Math.max(...freq.map(x => x[1]), 1);
  let html = `<div class="res-meta">${d.updated_at} 기준 · ${d.count}건</div>`;
  html += '<div class="res-section-title">섹터별 리포트 발행 빈도 (최근 3페이지)</div>';
  html += '<div class="res-freq-chart">';
  for (const [sector, count] of freq.slice(0, 12)) {
    const w = (count / maxFreq * 100);
    html += `<div class="res-freq-row">
      <span class="res-freq-label">${_escHtml(sector)}</span>
      <div class="res-freq-bar"><div class="res-freq-fill" style="width:${w}%"></div></div>
      <span class="res-freq-count">${count}건</span>
    </div>`;
  }
  html += '</div>';

  html += '<div class="res-section-title" style="margin-top:20px">최신 섹터 리포트</div>';
  for (const r of reports.slice(0, 40)) {
    const pdf = r.pdf_url
      ? `<a href="${_escHtml(r.pdf_url)}" target="_blank" rel="noopener" class="res-pdf">PDF</a>` : '';
    const detail = r.detail_link
      ? `<a href="${_escHtml(r.detail_link)}" target="_blank" rel="noopener" class="res-title-link">${_escHtml(r.title || '')}</a>`
      : _escHtml(r.title || '');
    html += `<div class="res-row">
      <span class="res-tag">${_escHtml(r.sector || '기타')}</span>
      <span class="res-title">${detail}</span>
      <span class="res-broker">${_escHtml(r.broker || '')}</span>
      <span class="res-date">${_escHtml(r.date || '')}</span>
      ${pdf}
    </div>`;
  }
  return html;
}

function _resRenderCompanies(d) {
  const reports = d.reports || [];
  const consensus = d.consensus || [];
  if (!reports.length) return '<div class="pg-empty">기업 리포트 없음</div>';

  let html = `<div class="res-meta">${d.updated_at} 기준 · ${d.report_count}건 · ${consensus.length}종목</div>`;
  html += '<div class="res-section-title">리포트 발행 상위 종목</div>';
  html += '<div class="res-consensus-grid">';
  for (const c of consensus.slice(0, 16)) {
    html += `<div class="res-cons-card" data-code="${_escHtml(c.code)}" data-name="${_escHtml(c.name)}" data-market="kr">
      <div class="res-cons-head">
        <span class="res-cons-name">${_escHtml(c.name || '')}</span>
        <span class="res-cons-code">${_escHtml(c.code || '')}</span>
      </div>
      <div class="res-cons-count">📄 ${c.report_count}건</div>
      <div class="res-cons-brokers">${c.brokers.slice(0, 4).map(_escHtml).join(' · ')}</div>
      <div class="res-cons-latest">${_escHtml(c.latest_date || '')} · ${_escHtml((c.latest_title || '').slice(0, 30))}</div>
    </div>`;
  }
  html += '</div>';

  html += '<div class="res-section-title" style="margin-top:20px">최신 기업 리포트</div>';
  for (const r of reports.slice(0, 40)) {
    const pdf = r.pdf_url
      ? `<a href="${_escHtml(r.pdf_url)}" target="_blank" rel="noopener" class="res-pdf">PDF</a>` : '';
    const detail = r.detail_link
      ? `<a href="${_escHtml(r.detail_link)}" target="_blank" rel="noopener" class="res-title-link">${_escHtml(r.title || '')}</a>`
      : _escHtml(r.title || '');
    const codeAttr = r.stock_code
      ? `data-code="${_escHtml(r.stock_code)}" data-name="${_escHtml(r.stock_name || '')}" data-market="kr"`
      : '';
    html += `<div class="res-row" ${codeAttr} style="${r.stock_code ? 'cursor:pointer' : ''}">
      <span class="res-stock-name">${_escHtml(r.stock_name || '')}</span>
      <span class="res-title">${detail}</span>
      <span class="res-broker">${_escHtml(r.broker || '')}</span>
      <span class="res-date">${_escHtml(r.date || '')}</span>
      ${pdf}
    </div>`;
  }
  return html;
}

function _resRenderRecommend(d, mkt) {
  const items = d.items || [];
  let html = `
    <div class="res-market-toggle">
      <button class="res-market-btn ${mkt === 'kr' ? 'active' : ''}" data-market="kr">🇰🇷 국내</button>
      <button class="res-market-btn ${mkt === 'us' ? 'active' : ''}" data-market="us">🇺🇸 미국</button>
    </div>
    <div class="res-meta">${d.updated_at || ''} 기준 · 애널리스트 관심 상위 ${items.length}종목</div>`;
  if (!items.length) return html + '<div class="pg-empty">데이터 없음</div>';

  for (let i = 0; i < items.length; i++) {
    const it = items[i];
    const isKR = (it.market || mkt) === 'kr';
    const flag = isKR ? '🇰🇷' : '🇺🇸';
    const code = it.code || it.symbol;
    const scorePct = Math.min(it.score, 100);
    const detailHTML = isKR
      ? `<div>리포트 ${it.report_count}건</div>
         ${it.opinion ? `<div>최신의견: <b>${_escHtml(it.opinion)}</b></div>` : ''}
         ${it.target_price ? `<div>목표가 ₩${Number(it.target_price).toLocaleString()}</div>` : ''}
         ${it.upside != null ? `<div class="res-upside" style="color:${it.upside >= 0 ? '#FF3333' : '#33AA33'}">괴리율 ${it.upside >= 0 ? '+' : ''}${it.upside}%</div>` : ''}`
      : `<div>Buy ${it.buy} · Hold ${it.hold} · Sell ${it.sell}</div>
         <div>매수비율 ${it.buy_ratio}% ${it.direction === 'up' ? '↑' : it.direction === 'down' ? '↓' : '→'}</div>
         ${it.target_mean ? `<div>목표 $${it.target_mean}</div>` : ''}`;
    html += `<div class="res-rec-card" data-code="${_escHtml(code)}" data-name="${_escHtml(it.name || '')}" data-market="${isKR ? 'kr' : 'us'}">
      <div class="res-rec-rank">#${i + 1}</div>
      <div class="res-rec-main">
        <div class="res-rec-head">
          <span class="res-rec-flag">${flag}</span>
          <span class="res-rec-name">${_escHtml(it.name || '')}</span>
          <span class="res-rec-code">${_escHtml(code)}</span>
        </div>
        <div class="res-rec-bar"><div class="res-rec-fill" style="width:${scorePct}%"></div></div>
      </div>
      <div class="res-rec-score">${it.score}<span class="res-rec-unit">점</span></div>
      <div class="res-rec-details">${detailHTML}</div>
      <div class="res-rec-expl">${(it.explanation || []).map(_escHtml).join(' · ')}</div>
    </div>`;
  }
  return html;
}

// ─────────────────────────────────────────────────────────────────────────────
// NEW HIGHS PAGE (Phase 19 — 52주 신고가)
// ─────────────────────────────────────────────────────────────────────────────
const NH_STATE = {
  market: 'kr',
  data: { kr: null, us: null },
  sort: 'w52_ratio',
  dir: 'desc',   // asc | desc
};

async function renderNewHighsPage() {
  const root = document.getElementById('newhighs-view');
  if (!root.dataset.built) {
    root.dataset.built = '1';
    root.innerHTML = `
      <div class="pg-wrap">
        <div class="pg-title">📈 52주 신고가 근접 종목</div>
        <div class="pg-sub">52주 고점 대비 95% 이상 · 일 1회 갱신</div>

        <details class="nh-guide">
          <summary>ℹ 고점대비(%) 지표 해석</summary>
          <div class="nh-guide-body">
            <div class="nh-guide-intro">
              <b>고점대비</b> = 현재가 ÷ 52주 최고가 × 100.
              값이 높을수록 추세 강함, 낮을수록 조정 중.
            </div>
            <table class="nh-guide-table">
              <thead><tr><th>값</th><th>의미</th></tr></thead>
              <tbody>
                <tr><td><b>100.0%</b></td><td>오늘이 52주 신고가 — 과거 1년 내 최고가 (🔴 TODAY 배지)</td></tr>
                <tr class="nh-guide-hot"><td><b>98~99.9%</b></td><td>신고가 턱밑 — 소폭 조정만 받고 강세 유지, 돌파 임박 가능성 (행 배경 빨강)</td></tr>
                <tr><td><b>95~97%</b></td><td>신고가 근접 — 이 페이지 기본 커트라인</td></tr>
                <tr><td>90%</td><td>고점에서 10% 하락 — 약조정</td></tr>
                <tr><td>80%</td><td>고점에서 20% 하락 — 일반 조정</td></tr>
                <tr><td>70%</td><td>고점에서 30% 하락 — 깊은 조정</td></tr>
                <tr><td>50%</td><td>고점의 절반 — 강한 추세 하락</td></tr>
              </tbody>
            </table>
            <div class="nh-guide-why">
              <b>왜 중요한가:</b> 52주 고점 돌파는 월스트리트 모멘텀 전략의 1차 필터.
              95%+ 종목은 "추세 유지 중", 98%+는 "돌파 대기", 100%는 "돌파 확정" 신호.
              정렬 기본값은 고점대비 내림차순 — 가장 뜨거운 종목부터 표시됩니다.
            </div>
          </div>
        </details>

        <div class="nh-market-toggle" id="nh-market-toggle">
          <button class="nh-market-btn active" data-market="kr">🇰🇷 국내</button>
          <button class="nh-market-btn"        data-market="us">🇺🇸 미국</button>
        </div>
        <div class="nh-meta" id="nh-meta">—</div>
        <div id="nh-results"><div class="pg-empty">로딩 중…</div></div>
      </div>`;

    document.getElementById('nh-market-toggle').addEventListener('click', (e) => {
      const btn = e.target.closest('.nh-market-btn');
      if (!btn || btn.dataset.market === NH_STATE.market) return;
      NH_STATE.market = btn.dataset.market;
      document.querySelectorAll('#nh-market-toggle .nh-market-btn').forEach(b =>
        b.classList.toggle('active', b.dataset.market === NH_STATE.market));
      _nhFetchAndRender();
    });

    document.getElementById('nh-results').addEventListener('click', (e) => {
      // 헤더 클릭 → 정렬
      const th = e.target.closest('.nh-th.sortable');
      if (th) {
        const col = th.dataset.sort;
        if (NH_STATE.sort === col) {
          NH_STATE.dir = NH_STATE.dir === 'desc' ? 'asc' : 'desc';
        } else {
          NH_STATE.sort = col;
          NH_STATE.dir = 'desc';
        }
        _nhRender(NH_STATE.data[NH_STATE.market]);
        return;
      }
      // 행 클릭 → 차트 모달
      const tr = e.target.closest('tr.nh-row');
      if (tr && tr.dataset.code) {
        openChartPanel(tr.dataset.code, tr.dataset.name || '', NH_STATE.market);
      }
    });
  }
  _nhFetchAndRender();
}

async function _nhFetchAndRender() {
  const mkt = NH_STATE.market;
  const box = document.getElementById('nh-results');
  const meta = document.getElementById('nh-meta');
  if (NH_STATE.data[mkt]) {
    _nhRender(NH_STATE.data[mkt]);
    return;
  }
  box.innerHTML = `<div class="pg-empty">스캔 중… (최초 호출은 ${mkt === 'us' ? '2~3분' : '1~2분'} 소요, 이후 24h 캐시)</div>`;
  meta.textContent = '';
  try {
    const url = mkt === 'us' ? '/api/us/new_highs' : '/api/new_highs';
    const r = await fetch(url);
    const d = await r.json();
    if (!r.ok || d.error) throw new Error(d.error || ('HTTP ' + r.status));
    NH_STATE.data[mkt] = d;
    _nhRender(d);
  } catch (err) {
    box.innerHTML = `<div class="pg-empty"><div class="pg-empty-title">로딩 실패</div>${_escHtml(err.message)}</div>`;
  }
}

function _nhRender(data) {
  const meta = document.getElementById('nh-meta');
  const box = document.getElementById('nh-results');
  const items = (data && data.items) || [];
  meta.textContent = `${data.updated_at || ''} 기준 · ${items.length}종목 (상위 ${NH_STATE.market === 'kr' ? 200 : 503}개 스캔)`;

  if (!items.length) {
    box.innerHTML = '<div class="pg-empty">52주 고점 대비 95% 이상 종목이 없습니다.</div>';
    return;
  }

  // 정렬
  const sorted = [...items].sort((a, b) => {
    const av = a[NH_STATE.sort];
    const bv = b[NH_STATE.sort];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    if (typeof av === 'string') return NH_STATE.dir === 'desc' ? bv.localeCompare(av) : av.localeCompare(bv);
    return NH_STATE.dir === 'desc' ? bv - av : av - bv;
  });

  const isKR = NH_STATE.market === 'kr';
  const curSym = isKR ? '₩' : '$';
  const fmtCap = (v) => {
    if (v == null) return '—';
    if (isKR) return Math.round(v / 1e8).toLocaleString() + '억';
    if (v >= 1e12) return '$' + (v / 1e12).toFixed(2) + 'T';
    if (v >= 1e9)  return '$' + (v / 1e9).toFixed(1) + 'B';
    return '$' + Math.round(v / 1e6).toLocaleString() + 'M';
  };
  const fmtVol = (v) => {
    if (v == null) return '—';
    return isKR
      ? Math.round(v).toLocaleString() + '백만'
      : '$' + Math.round(v).toLocaleString() + 'M';
  };
  const fmtPrice = (v) => {
    if (v == null) return '—';
    return isKR
      ? '₩' + Math.round(v).toLocaleString()
      : '$' + Number(v).toFixed(2);
  };
  const fmtPct = (v) => v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%';

  const arrow = (col) => NH_STATE.sort === col
    ? (NH_STATE.dir === 'desc' ? ' ▼' : ' ▲') : '';
  const thCls = (col) => 'nh-th sortable' + (NH_STATE.sort === col ? ' active' : '');

  const headerCols = isKR
    ? [
        { key: 'sector',     label: '섹터',      r: false, sortable: true },
        { key: 'name',       label: '종목명',    r: false, sortable: true },
        { key: 'volume_mn',  label: '거래대금',  r: true,  sortable: true },
        { key: 'price',      label: '현재가',    r: true,  sortable: true },
        { key: 'w52_high',   label: '52주 고점', r: true,  sortable: true },
        { key: 'w52_low',    label: '52주 저점', r: true,  sortable: true },
        { key: 'w52_ratio',  label: '고점대비',  r: true,  sortable: true },
        { key: 'change_pct', label: '등락률',    r: true,  sortable: true },
      ]
    : [
        { key: 'sector',     label: 'Sector',     r: false, sortable: true },
        { key: 'name',       label: 'Company',    r: false, sortable: true },
        { key: 'market_cap', label: 'Mkt Cap',    r: true,  sortable: true },
        { key: 'volume_mn',  label: '$Volume',    r: true,  sortable: true },
        { key: 'turnover',   label: '회전율%',    r: true,  sortable: true },
        { key: 'per',        label: 'PER',        r: true,  sortable: true },
        { key: 'price',      label: 'Price',      r: true,  sortable: true },
        { key: 'w52_high',   label: '52W High',   r: true,  sortable: true },
        { key: 'w52_ratio',  label: '고점대비',   r: true,  sortable: true },
        { key: 'change_pct', label: 'Change',     r: true,  sortable: true },
      ];

  const headerHTML = headerCols.map(c =>
    `<th class="${thCls(c.key)}${c.r ? ' r' : ''}" data-sort="${c.key}">${_escHtml(c.label)}${arrow(c.key)}</th>`
  ).join('');

  const rowsHTML = sorted.map(it => {
    const sc = _escHtml(it.code || it.symbol || '');
    const sn = _escHtml(it.name || '');
    const chg = it.change_pct || 0;
    const chgCol = chg > 0 ? '#FF3333' : chg < 0 ? '#33AA33' : 'var(--text-muted)';
    const w52r = it.w52_ratio || 0;
    const ratioCol = w52r >= 99 ? '#FF3333' : w52r >= 97 ? '#FF9500' : 'var(--text)';
    const isToday = it.is_today;
    const hotRow = w52r >= 98;
    const newBadge = isToday ? '<span class="nh-new-badge" title="오늘 신고가">🔴</span>' : '';

    const cells = isKR ? `
      <td class="nh-sector">${_escHtml(it.sector || '')}</td>
      <td class="nh-name">${newBadge}${sn}<span class="nh-code">${sc}</span></td>
      <td class="r">${fmtVol(it.volume_mn)}</td>
      <td class="r nh-price">${fmtPrice(it.price)}</td>
      <td class="r">${fmtPrice(it.w52_high)}</td>
      <td class="r" style="color:var(--text-sub)">${fmtPrice(it.w52_low)}</td>
      <td class="r" style="color:${ratioCol};font-weight:700">${w52r.toFixed(1)}%</td>
      <td class="r" style="color:${chgCol};font-weight:700">${fmtPct(chg)}</td>
    ` : `
      <td class="nh-sector">${_escHtml(it.sector || '')}</td>
      <td class="nh-name">${newBadge}${sn}<span class="nh-code">${sc}</span></td>
      <td class="r">${fmtCap(it.market_cap)}</td>
      <td class="r">${fmtVol(it.volume_mn)}</td>
      <td class="r">${it.turnover != null ? it.turnover.toFixed(2) + '%' : '—'}</td>
      <td class="r">${it.per != null ? it.per.toFixed(1) : '—'}</td>
      <td class="r nh-price">${fmtPrice(it.price)}</td>
      <td class="r">${fmtPrice(it.w52_high)}</td>
      <td class="r" style="color:${ratioCol};font-weight:700">${w52r.toFixed(1)}%</td>
      <td class="r" style="color:${chgCol};font-weight:700">${fmtPct(chg)}</td>
    `;

    return `<tr class="nh-row${hotRow ? ' hot' : ''}" data-code="${sc}" data-name="${sn}">${cells}</tr>`;
  }).join('');

  box.innerHTML = `
    <table class="nh-table">
      <thead><tr>${headerHTML}</tr></thead>
      <tbody>${rowsHTML}</tbody>
    </table>`;
}



// ============================================================
// 📊 대시보드 홈 (공포탐욕 게이지 + 매크로 + AI추천 + 옵션 시그널)
// ============================================================
async function renderDashboardHome() {
  const root = document.getElementById('dashboard-view');
  root.innerHTML = `<div class="pg-wrap">
    <div class="pg-title">📊 대시보드</div>
    <div class="pg-sub">시장 전체 현황 한 화면 — 공포탐욕·지수·매크로·AI추천·옵션</div>
    <div id="dash-content"><div class="bt-loading">⏳ 로딩 중…</div></div>
  </div>`;
  const box = document.getElementById('dash-content');

  let macro = null, fg = null, summary = null, agent = null;
  try {
    const [r1, r2, r3, r4] = await Promise.all([
      fetch('/api/macro').catch(() => null),
      fetch('/api/fear_greed').catch(() => null),
      fetch('/api/market_summary').catch(() => null),
      fetch('/api/agent/result?market=kr').catch(() => null),
    ]);
    macro = r1 && r1.ok ? await r1.json() : {};
    fg = r2 && r2.ok ? await r2.json() : {};
    summary = r3 && r3.ok ? await r3.json() : {};
    agent = r4 && r4.ok ? await r4.json() : {};
  } catch(e) {
    box.innerHTML = `<div class="bt-error">로드 실패: ${_escHtml(e.message)}</div>`;
    return;
  }

  const m = {};
  for (const it of (macro.items || [])) m[it.name] = it;
  // KOSPI/KOSDAQ/S&P/NASDAQ 는 data.json에서
  let dj = APP.data || {};
  if (!dj.themes) {
    try { dj = await (await fetch('/data.json')).json(); } catch {}
  }
  const mo = dj.market_overview || {};
  const idxMap = {
    'KOSPI':   { value: dj.kospi?.value, change_pct: dj.kospi?.change_pct },
    'KOSDAQ':  { value: dj.kosdaq?.value, change_pct: dj.kosdaq?.change_pct },
    'S&P 500': { value: mo.sp500?.value, change_pct: mo.sp500?.change_pct },
    'NASDAQ':  { value: mo.nasdaq?.value, change_pct: mo.nasdaq?.change_pct },
    'VIX':     { value: m['VIX']?.value, change_pct: m['VIX']?.change_pct },
  };

  const upCol = 'var(--color-up)';
  const dnCol = 'var(--color-down)';
  let html = '<div class="dash-home">';

  // ── Row 1: 공포탐욕 + 주요 지수 ──
  html += '<div class="dash-row">';
  if (fg && fg.score != null) {
    const s = fg.score;
    const c = s <= 25 ? dnCol : s <= 45 ? '#f97316'
            : s <= 55 ? 'var(--text-secondary)' : s <= 75 ? 'var(--color-gold)' : upCol;
    html += `<div class="dash-card dash-fg">
      <div class="dash-card-label">공포 & 탐욕 지수</div>
      <div class="dash-fg-score" style="color:${c}">${s}</div>
      <div class="dash-fg-rating" style="color:${c}">${_escHtml(fg.rating_kr || fg.rating || '')}</div>
      <div class="dash-fg-bar">
        <div class="dash-fg-fill" style="width:${s}%;background:${c}"></div>
        <div class="dash-fg-marker" style="left:${s}%"></div>
      </div>
      <div class="dash-fg-labels"><span>극단공포 0</span><span>50</span><span>100 극단탐욕</span></div>
      <div class="dash-fg-source">source: ${_escHtml(fg.source || '')}</div>
    </div>`;
  }
  for (const name of ['KOSPI','KOSDAQ','S&P 500','NASDAQ','VIX']) {
    const d = idxMap[name];
    if (!d || d.value == null) continue;
    const p = d.change_pct || 0;
    const col = p >= 0 ? upCol : dnCol;
    const sign = p >= 0 ? '+' : '';
    let extra = '';
    if (name === 'VIX') {
      const v = d.value;
      extra = v < 15 ? '안정' : v < 20 ? '보통' : v < 25 ? '경계' : v < 35 ? '공포' : '패닉';
    }
    html += `<div class="dash-card">
      <div class="dash-card-label">${_escHtml(name)}</div>
      <div class="dash-card-value">${Number(d.value).toLocaleString(undefined,{maximumFractionDigits:2})}</div>
      <div class="dash-card-change" style="color:${col}">${sign}${p.toFixed(2)}%</div>
      ${extra ? `<div class="dash-card-extra">${extra}</div>` : ''}
    </div>`;
  }
  html += '</div>';

  // ── Row 2: 환율·원자재·금리·BTC ──
  html += '<div class="dash-row">';
  for (const name of ['USD/KRW','WTI 원유','금','미국 10년물','BTC','구리']) {
    const d = m[name];
    if (!d || d.value == null) continue;
    const p = d.change_pct || 0;
    const col = p >= 0 ? upCol : dnCol;
    const sign = p >= 0 ? '+' : '';
    html += `<div class="dash-card dash-card-sm">
      <div class="dash-card-label">${_escHtml(name)}</div>
      <div class="dash-card-value">${Number(d.value).toLocaleString(undefined,{maximumFractionDigits:2})}</div>
      <div class="dash-card-change" style="color:${col}">${sign}${p.toFixed(2)}%</div>
    </div>`;
  }
  html += '</div>';

  // ── Row 3: 옵션 시그널 ──
  const sections = (summary && summary.sections) || [];
  const optSec = sections.find(s => (s.title || '').includes('옵션') || (s.title || '').includes('선물'));
  if (optSec) {
    html += `<div class="dash-card dash-wide">
      <div class="dash-card-label">${_escHtml(optSec.title)}</div>`;
    for (const it of (optSec.items || [])) {
      html += `<div class="dash-opt-item">${_escHtml(it)}</div>`;
    }
    html += '</div>';
  }

  // ── Row 4: 핫테마 + AI추천 TOP 5 ──
  const picks = (agent && agent.final_picks) || [];
  const hot = (agent && agent.agents && agent.agents.news && agent.agents.news.hot_themes) || [];
  if (picks.length || hot.length) {
    html += '<div class="dash-row">';
    if (hot.length) {
      html += `<div class="dash-card">
        <div class="dash-card-label">🔥 핫 테마</div>
        <div class="dash-hot-themes">${
          hot.slice(0, 8).map(t => `<span class="dash-theme-chip">${_escHtml(t)}</span>`).join('')
        }</div>
      </div>`;
    }
    if (picks.length) {
      html += `<div class="dash-card dash-wide">
        <div class="dash-card-label">🤖 AI추천 TOP 5</div>`;
      picks.slice(0, 5).forEach((p, i) => {
        const pc = p.change_pct;
        const pCol = (pc != null && pc >= 0) ? upCol : dnCol;
        const sign = (pc != null && pc >= 0) ? '+' : '';
        html += `<div class="dash-pick" data-code="${_escHtml(p.code)}" data-name="${_escHtml(p.name)}">
          <span class="dash-pick-rank">#${i+1}</span>
          <span class="dash-pick-name">${_escHtml(p.name)}</span>
          ${p.price ? `<span class="dash-pick-price">₩${Number(p.price).toLocaleString()}</span>` : ''}
          ${pc != null ? `<span class="dash-pick-pct" style="color:${pCol}">${sign}${pc.toFixed(2)}%</span>` : ''}
          <span class="dash-pick-score">${p.total_score || 0}점</span>
        </div>`;
      });
      html += '</div>';
    }
    html += '</div>';
  }

  html += '</div>';
  box.innerHTML = html;
  box.querySelectorAll('.dash-pick').forEach(el => {
    el.addEventListener('click', () => openChartPanel(el.dataset.code, el.dataset.name, 'kr'));
  });
}


// ============================================================
// 📈 수익률 저널 (DB 기반 trade_journal)
// ============================================================
let _PJ_GROUP = 'daily';
let _PJ_PERIOD = 30;

function renderPnlJournalPage() {
  const root = document.getElementById('pnljournal-view');
  root.innerHTML = `<div class="pg-wrap">
    <div class="pg-title">📈 수익률 저널</div>
    <div class="pg-sub">매수/매도 기록 + FIFO 매칭으로 실현 손익·승률·손익비 자동 집계</div>
    <div class="pj-tabs" id="pj-group-tabs">
      <button class="pj-tab active" data-grp="daily">일별</button>
      <button class="pj-tab" data-grp="monthly">월별</button>
      <button class="pj-tab" data-grp="strategy">전략별</button>
    </div>
    <div class="pj-period-tabs" id="pj-period-tabs">
      <button class="pj-period active" data-days="30">1개월</button>
      <button class="pj-period" data-days="90">3개월</button>
      <button class="pj-period" data-days="365">1년</button>
      <button class="pj-period" data-days="9999">전체</button>
    </div>
    <div class="pj-actions">
      <button class="pj-add-btn" id="pj-add-btn">＋ 매매 기록 추가</button>
    </div>
    <div id="pj-result"></div>
    <div id="pj-trades"></div>
  </div>`;

  document.getElementById('pj-group-tabs').addEventListener('click', (e) => {
    const b = e.target.closest('.pj-tab'); if (!b) return;
    _PJ_GROUP = b.dataset.grp;
    document.querySelectorAll('#pj-group-tabs .pj-tab').forEach(x => x.classList.toggle('active', x === b));
    _fetchPnlJournal();
  });
  document.getElementById('pj-period-tabs').addEventListener('click', (e) => {
    const b = e.target.closest('.pj-period'); if (!b) return;
    _PJ_PERIOD = parseInt(b.dataset.days) || 30;
    document.querySelectorAll('#pj-period-tabs .pj-period').forEach(x => x.classList.toggle('active', x === b));
    _fetchPnlJournal();
  });
  document.getElementById('pj-add-btn').addEventListener('click', _showPnlJournalAdd);
  _fetchPnlJournal();
}

async function _fetchPnlJournal() {
  const sumBox = document.getElementById('pj-result');
  const trBox = document.getElementById('pj-trades');
  sumBox.innerHTML = '<div class="bt-loading">⏳ 집계 중…</div>';
  trBox.innerHTML = '';
  try {
    const [s, l] = await Promise.all([
      fetch(`/api/journal/summary?period=${_PJ_PERIOD}&group=${_PJ_GROUP}`).then(r => r.json()),
      fetch(`/api/journal/list?period=${_PJ_PERIOD}`).then(r => r.json()),
    ]);
    if (s.error) { sumBox.innerHTML = `<div class="bt-error">${_escHtml(s.error)}</div>`; return; }
    _renderPnlSummary(s, sumBox);
    _renderPnlTrades(l.trades || [], trBox);
  } catch(e) {
    sumBox.innerHTML = `<div class="bt-error">요청 실패: ${_escHtml(e.message)}</div>`;
  }
}

function _renderPnlSummary(d, box) {
  const o = d.overall || {};
  if (!o.total_trades) {
    box.innerHTML = `<div class="pg-empty"><div class="pg-empty-title">매매 이력 없음</div>
      "+ 매매 기록 추가" 버튼으로 첫 거래를 입력하세요.</div>`;
    return;
  }
  const upCol = 'var(--color-up)', dnCol = 'var(--color-down)';
  const pnlCol = o.total_pnl >= 0 ? upCol : dnCol;
  const fmt = (n) => Number(n || 0).toLocaleString();
  let html = `<div class="pj-summary">
    <div class="pj-stat-grid">
      <div class="pj-stat"><div class="pj-stat-label">총 실현손익</div><div class="pj-stat-value" style="color:${pnlCol}">₩${fmt(o.total_pnl)}</div></div>
      <div class="pj-stat"><div class="pj-stat-label">총 매매</div><div class="pj-stat-value">${o.total_trades}건</div></div>
      <div class="pj-stat"><div class="pj-stat-label">승률</div><div class="pj-stat-value">${o.win_rate}%</div></div>
      <div class="pj-stat"><div class="pj-stat-label">평균 수익률</div><div class="pj-stat-value" style="color:${o.avg_pnl_pct>=0?upCol:dnCol}">${o.avg_pnl_pct>=0?'+':''}${o.avg_pnl_pct}%</div></div>
      <div class="pj-stat"><div class="pj-stat-label">손익비</div><div class="pj-stat-value">${o.profit_factor}</div></div>
      <div class="pj-stat"><div class="pj-stat-label">평균 보유</div><div class="pj-stat-value">${o.avg_hold_days}일</div></div>
    </div>
  </div>`;

  // 누적 수익 곡선
  const curve = d.equity_curve || [];
  if (curve.length >= 2) {
    const vals = curve.map(c => c.cumulative);
    const w = 700, h = 80;
    const min = Math.min(...vals, 0), max = Math.max(...vals, 0);
    const range = (max - min) || 1;
    const pts = vals.map((v, i) => {
      const x = (i / (vals.length - 1)) * w;
      const y = h - ((v - min) / range) * h;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
    const zeroY = h - ((0 - min) / range) * h;
    const lastV = vals[vals.length - 1];
    const cCol = lastV >= 0 ? upCol : dnCol;
    html += `<div class="pj-curve">
      <div class="pj-curve-title">누적 손익 곡선</div>
      <svg width="100%" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="height:80px">
        <line x1="0" y1="${zeroY}" x2="${w}" y2="${zeroY}" stroke="var(--border-secondary)" stroke-width="0.5" stroke-dasharray="4"/>
        <polyline points="${pts}" fill="none" stroke="${cCol}" stroke-width="2"/>
      </svg>
    </div>`;
  }

  // 그룹별
  const groups = d.groups || [];
  if (groups.length) {
    html += '<div class="pj-groups">';
    for (const g of groups) {
      const c = (g.pnl || 0) >= 0 ? upCol : dnCol;
      html += `<div class="pj-group-row">
        <span class="pj-group-label">${_escHtml(g.label)}</span>
        <span class="pj-group-trades">${g.trades}건</span>
        <span class="pj-group-wr">${g.win_rate}%</span>
        <span class="pj-group-pnl" style="color:${c}">${(g.pnl||0)>=0?'+':''}₩${fmt(g.pnl)}</span>
      </div>`;
    }
    html += '</div>';
  }
  box.innerHTML = html;
}

function _renderPnlTrades(trades, box) {
  if (!trades.length) { box.innerHTML = ''; return; }
  const upCol = 'var(--color-up)', dnCol = 'var(--color-down)';
  const fmt = (n) => Number(n || 0).toLocaleString();
  let html = `<div class="pj-trades-title">매매 이력 (${trades.length}건)</div>
    <div class="pj-table-wrap"><table class="pj-table"><thead><tr>
      <th>일자</th><th>종목</th><th class="r">매매</th><th class="r">가격</th><th class="r">수량</th>
      <th class="r">금액</th><th class="r">실현손익</th><th>전략</th><th></th>
    </tr></thead><tbody>`;
  for (const t of trades) {
    const ab = t.action === 'buy'
      ? `<span style="color:${upCol};font-weight:600">매수</span>`
      : `<span style="color:${dnCol};font-weight:600">매도</span>`;
    let pnlS = '—';
    if (t.realized_pnl != null) {
      const pCol = t.realized_pnl >= 0 ? upCol : dnCol;
      const sign = t.realized_pnl >= 0 ? '+' : '';
      pnlS = `<span style="color:${pCol}">${sign}₩${fmt(Math.round(t.realized_pnl))} (${(t.realized_pnl_pct||0)>=0?'+':''}${t.realized_pnl_pct}%)</span>`;
    }
    html += `<tr>
      <td>${_escHtml(t.trade_date)}</td>
      <td>${_escHtml(t.name || t.code)} <small style="color:var(--text-tertiary)">${_escHtml(t.code)}</small></td>
      <td class="r">${ab}</td>
      <td class="r">₩${fmt(Math.round(t.price))}</td>
      <td class="r">${fmt(t.qty)}</td>
      <td class="r">₩${fmt(Math.round(t.total_amount))}</td>
      <td class="r">${pnlS}</td>
      <td>${_escHtml(t.strategy || '')}</td>
      <td><button class="pj-del" data-id="${t.id}" title="삭제">×</button></td>
    </tr>`;
  }
  html += '</tbody></table></div>';
  box.innerHTML = html;
  box.querySelectorAll('.pj-del').forEach(b => {
    b.addEventListener('click', async () => {
      if (!confirm('이 매매 기록을 삭제할까요?')) return;
      await fetch(`/api/journal/delete/${b.dataset.id}`, { method: 'DELETE' });
      _fetchPnlJournal();
    });
  });
}

function _showPnlJournalAdd() {
  document.getElementById('pj-add-modal')?.remove();
  const today = new Date().toISOString().slice(0, 10);
  const ov = document.createElement('div');
  ov.className = 'cl-overlay'; ov.id = 'pj-add-modal';
  ov.addEventListener('click', (e) => { if (e.target === ov) ov.remove(); });
  ov.innerHTML = `<div class="cl-modal" style="max-width:460px" onclick="event.stopPropagation()">
    <div class="cl-title">📝 매매 기록 추가</div>
    <div class="pj-form">
      <div class="pj-field"><label>종목코드</label><input id="pj-i-code" class="bt-input" placeholder="005930"></div>
      <div class="pj-field-row">
        <div class="pj-field"><label>매매구분</label>
          <select id="pj-i-action" class="bt-input">
            <option value="buy">매수</option><option value="sell">매도 (FIFO)</option>
          </select>
        </div>
        <div class="pj-field"><label>전략 태그</label><input id="pj-i-strategy" class="bt-input" placeholder="스윙·단타 등"></div>
      </div>
      <div class="pj-field-row">
        <div class="pj-field"><label>가격(₩)</label><input id="pj-i-price" type="number" class="bt-input" placeholder="55000"></div>
        <div class="pj-field"><label>수량</label><input id="pj-i-qty" type="number" class="bt-input" placeholder="100"></div>
      </div>
      <div class="pj-field"><label>매매일</label><input id="pj-i-date" type="date" class="bt-input" value="${today}"></div>
      <div class="pj-field"><label>메모 (선택)</label><input id="pj-i-memo" class="bt-input" placeholder="매매 근거"></div>
    </div>
    <div class="cl-actions">
      <button class="cl-cancel" id="pj-i-cancel">취소</button>
      <button class="pj-add-btn" id="pj-i-submit">추가</button>
    </div>
  </div>`;
  document.body.appendChild(ov);
  document.getElementById('pj-i-cancel').addEventListener('click', () => ov.remove());
  document.getElementById('pj-i-submit').addEventListener('click', async () => {
    const body = {
      code: document.getElementById('pj-i-code').value.trim(),
      action: document.getElementById('pj-i-action').value,
      price: parseFloat(document.getElementById('pj-i-price').value) || 0,
      qty: parseInt(document.getElementById('pj-i-qty').value) || 0,
      trade_date: document.getElementById('pj-i-date').value,
      strategy: document.getElementById('pj-i-strategy').value.trim(),
      memo: document.getElementById('pj-i-memo').value.trim(),
    };
    if (!body.code || body.price <= 0 || body.qty <= 0) {
      alert('종목·가격·수량을 입력하세요'); return;
    }
    const r = await fetch('/api/journal/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (d.error) { alert(d.error); return; }
    ov.remove();
    _fetchPnlJournal();
  });
}


// ============================================================
// 🌍 글로벌 매크로 페이지
// ============================================================
async function renderGlobalMacroPage() {
  const root = document.getElementById('globalmacro-view');
  root.innerHTML = `<div class="pg-wrap">
    <div class="pg-title">🌍 글로벌 매크로</div>
    <div class="pg-sub">글로벌 지수·금리·환율·원자재·심리·크립토 한 화면 (30분 캐시)</div>
    <div id="gm-content"><div class="bt-loading">⏳ 매크로 로딩 중…</div></div>
  </div>`;
  const box = document.getElementById('gm-content');
  try {
    const r = await fetch('/api/global_macro');
    const d = await r.json();
    if (d.error) { box.innerHTML = `<div class="bt-error">${_escHtml(d.error)}</div>`; return; }
    _renderGlobalMacro(d, box);
  } catch(e) {
    box.innerHTML = `<div class="bt-error">로드 실패: ${_escHtml(e.message)}</div>`;
  }
}

function _renderGlobalMacro(data, box) {
  const sections = data.sections || {};
  const upCol = 'var(--color-up)', dnCol = 'var(--color-down)';
  const order = ['indices', 'bonds', 'currencies', 'commodities', 'sentiment', 'crypto'];
  let html = `<div class="gm-updated">기준: ${_escHtml(data.updated_at || '')}</div>`;
  for (const key of order) {
    const sec = sections[key];
    if (!sec || !(sec.items || []).length) continue;
    html += `<div class="gm-section">
      <div class="gm-section-title">${_escHtml(sec.title)}</div>
      <div class="gm-grid">`;
    for (const it of sec.items) {
      const pct = it.change_pct;
      const col = pct == null ? 'var(--text-secondary)'
                : pct > 0 ? upCol : pct < 0 ? dnCol : 'var(--text-secondary)';
      const sign = pct != null && pct > 0 ? '+' : '';
      const unit = it.unit || '';
      let valStr = '';
      if (typeof it.value === 'number') {
        valStr = it.value >= 1000
          ? it.value.toLocaleString(undefined, {maximumFractionDigits: 2})
          : it.value.toFixed(2);
      } else {
        valStr = (it.value == null) ? '—' : String(it.value);
      }
      let cls = 'gm-card';
      if (it.is_inverted) cls += ' gm-card-warn';
      if (it.signal === '패닉' || it.signal === '공포') cls += ' gm-card-danger';
      html += `<div class="${cls}">
        <div class="gm-card-name">${_escHtml(it.name)}</div>
        <div class="gm-card-value">${_escHtml(valStr)}${_escHtml(unit)}</div>
        ${(pct != null) ? `<div class="gm-card-change" style="color:${col}">${sign}${pct.toFixed(2)}%</div>` : ''}
        ${it.signal ? `<div class="gm-card-signal">${_escHtml(it.signal)}</div>` : ''}
      </div>`;
    }
    html += '</div></div>';
  }
  box.innerHTML = html;
}


// ============================================================
// 📱 모바일 햄버거 메뉴
// ============================================================
function initMobileMenu() {
  // 햄버거 버튼 (탑바 좌측 prepend, 한 번만 생성)
  const topbar = document.getElementById('topbar');
  if (topbar && !document.querySelector('.mobile-menu-btn')) {
    const btn = document.createElement('button');
    btn.className = 'mobile-menu-btn';
    btn.type = 'button';
    btn.setAttribute('aria-label', 'menu');
    btn.innerHTML = '☰';
    btn.addEventListener('click', toggleMobileSidebar);
    topbar.prepend(btn);
  }
  // 오버레이
  if (!document.querySelector('.mobile-overlay')) {
    const ov = document.createElement('div');
    ov.className = 'mobile-overlay';
    ov.addEventListener('click', closeMobileSidebar);
    document.body.appendChild(ov);
  }
}

function toggleMobileSidebar() {
  const sb = document.getElementById('sidebar');
  const ov = document.querySelector('.mobile-overlay');
  if (sb) sb.classList.toggle('mobile-open');
  if (ov) ov.classList.toggle('show');
}

function closeMobileSidebar() {
  const sb = document.getElementById('sidebar');
  const ov = document.querySelector('.mobile-overlay');
  if (sb) sb.classList.remove('mobile-open');
  if (ov) ov.classList.remove('show');
}

// DOM 준비 후 초기화
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initMobileMenu);
} else {
  initMobileMenu();
}


// ============================================================
// 🔗 밸류체인 맵
// ============================================================
let _VC_THEME = 'ai_semiconductor';

async function renderValuechainPage() {
  const root = document.getElementById('valuechain-view');
  root.innerHTML = `<div class="pg-wrap">
    <div class="pg-title">🔗 밸류체인 맵</div>
    <div class="pg-sub">5개 산업 × 레이어 종목 매핑 + 뉴스 키워드 기반 실시간 heat (30분 캐시)</div>
    <div id="vc-content"><div class="bt-loading">⏳ 밸류체인 + 뉴스 분석 중…</div></div>
  </div>`;
  const box = document.getElementById('vc-content');
  try {
    const r = await fetch('/api/valuechain');
    const d = await r.json();
    if (d.error) { box.innerHTML = `<div class="bt-error">${_escHtml(d.error)}</div>`; return; }
    _renderValuechain(d, box);
  } catch(e) {
    box.innerHTML = `<div class="bt-error">로드 실패: ${_escHtml(e.message)}</div>`;
  }
}

function _renderValuechain(data, box) {
  const meta = data._meta || {};
  delete data._meta;
  const themes = Object.entries(data);
  if (!themes.length) { box.innerHTML = '<div class="pg-empty">데이터 없음</div>'; return; }

  // 첫 진입 시 _VC_THEME 초기화
  if (!themes.find(([id]) => id === _VC_THEME)) _VC_THEME = themes[0][0];

  let html = `<div class="vc-meta">📰 뉴스 ${meta.total_headlines || 0}건 분석 · ${_escHtml(meta.calculated_at || '')}</div>`;

  // 테마 탭
  html += '<div class="vc-theme-tabs" id="vc-theme-tabs">';
  for (const [id, t] of themes) {
    const active = id === _VC_THEME ? 'active' : '';
    html += `<button class="vc-theme-tab ${active}" data-vct="${_escHtml(id)}">
      ${_escHtml(t.icon)} ${_escHtml(t.theme)}
      <span class="vc-heat-badge">${t.total_heat}</span>
    </button>`;
  }
  html += '</div>';

  // 선택된 테마 컨텐츠
  const cur = data[_VC_THEME] || themes[0][1];
  html += '<div class="vc-theme-body">';
  for (let li = 0; li < cur.layers.length; li++) {
    const layer = cur.layers[li];
    const heatPct = Math.min(100, layer.heat || 0);
    const heatCol = heatPct >= 70 ? 'var(--color-up)'
                   : heatPct >= 40 ? 'var(--color-gold)'
                   : 'var(--text-tertiary)';
    const alertCls = layer.bottleneck_alert ? 'vc-layer-alert' : '';

    html += `<div class="vc-layer ${alertCls}" style="--vc-color:${_escHtml(layer.color || '#888')}">
      <div class="vc-layer-header">
        <div class="vc-layer-text">
          <div class="vc-layer-title">${_escHtml(layer.title)}</div>
          <div class="vc-layer-subtitle">${_escHtml(layer.subtitle)}</div>
        </div>
        <div class="vc-heat-gauge">
          <div class="vc-heat-bar"><div class="vc-heat-fill" style="width:${heatPct}%;background:${heatCol}"></div></div>
          <span class="vc-heat-score" style="color:${heatCol}">${layer.heat}</span>
          ${layer.bottleneck_alert ? `<span class="vc-alert">⚠ 병목</span>` : ''}
        </div>
      </div>`;

    // 매칭 키워드 칩
    if ((layer.matched_keywords || []).length) {
      html += '<div class="vc-keywords">';
      for (const kw of layer.matched_keywords) {
        html += `<span class="vc-kw">${_escHtml(kw)}</span>`;
      }
      html += '</div>';
    }

    // 매칭 헤드라인 (drill-through)
    if ((layer.top_headlines || []).length) {
      html += '<div class="vc-headlines">';
      for (const h of layer.top_headlines.slice(0, 3)) {
        html += `<div class="vc-headline">📰 ${_escHtml(h)}</div>`;
      }
      html += '</div>';
    }

    // 세그먼트 카드들
    html += '<div class="vc-segments">';
    for (const seg of (layer.segments || [])) {
      const segCls = seg.bottleneck ? 'vc-seg vc-seg-bottleneck' : 'vc-seg';
      html += `<div class="${segCls}">
        <div class="vc-seg-name">${_escHtml(seg.name)}</div>`;
      // 외국인 net 배지
      if (seg.foreign_today_eok) {
        const fc = seg.foreign_today_eok > 0 ? 'var(--color-up)' : 'var(--color-down)';
        const fs = seg.foreign_today_eok > 0 ? '+' : '';
        html += `<div class="vc-seg-flow" style="color:${fc}">외인 ${fs}${seg.foreign_today_eok}억</div>`;
      }
      for (const c of (seg.companies || [])) {
        const badge = _marketBadge(c.market || 'kr');
        const pct = c.change_pct;
        const pCol = pct == null ? 'var(--text-tertiary)'
                    : pct >= 0 ? 'var(--color-up)' : 'var(--color-down)';
        const sign = pct != null && pct >= 0 ? '+' : '';
        const priceStr = c.price ? (c.market === 'us'
          ? '$' + Number(c.price).toFixed(2)
          : '₩' + Number(c.price).toLocaleString()) : '—';
        html += `<div class="vc-company" data-code="${_escHtml(c.code)}" data-name="${_escHtml(c.name_db || c.name)}" data-mkt="${_escHtml(c.market || 'kr')}">
          <span class="vc-comp-name">${_escHtml(c.name)} ${badge}</span>
          <span class="vc-comp-price">${priceStr}</span>
          <span class="vc-comp-pct" style="color:${pCol}">${pct != null ? sign + pct.toFixed(2) + '%' : ''}</span>
        </div>`;
      }
      html += '</div>';
    }
    html += '</div>';

    // 레이어 사이 화살표
    if (li < cur.layers.length - 1) {
      html += '<div class="vc-arrow">↓</div>';
    }
    html += '</div>';
  }
  html += '</div>';
  box.innerHTML = html;

  // 이벤트
  document.getElementById('vc-theme-tabs').addEventListener('click', (e) => {
    const b = e.target.closest('.vc-theme-tab'); if (!b) return;
    _VC_THEME = b.dataset.vct;
    renderValuechainPage();
  });
  box.querySelectorAll('.vc-company').forEach(el => {
    el.addEventListener('click', () => {
      const c = el.dataset.code, n = el.dataset.name, m = el.dataset.mkt;
      if (c) openChartPanel(c, n, m);
    });
  });
}

// ============================================================
// 밸류체인 v2 — 3패널 드릴다운 (Step 3)
// ============================================================
const VC2_STATE = {
  themes: [],
  currentTheme: null,
  currentLayer: null,
  currentSegment: null,
  layerData: null,
  segmentData: null,
  loading: false,
  filterReflection: 'all',
  filterMarket: 'all',
};

function vc2BadgeColor(score) {
  if (score < 30) return '#3b82f6';
  if (score < 50) return '#06b6d4';
  if (score < 70) return '#f59e0b';
  if (score < 85) return '#f97316';
  return '#ef4444';
}

function vc2BadgeLabel(score) {
  if (score < 30) return { label: '미반영', emoji: '🔥' };
  if (score < 50) return { label: '부분 미반영', emoji: '💎' };
  if (score < 70) return { label: '부분 반영', emoji: '⚡' };
  if (score < 85) return { label: '상당 반영', emoji: '🟠' };
  return { label: '과열', emoji: '🔴' };
}

function vc2HeatBar(heat) {
  const pct = Math.max(0, Math.min(100, heat || 0));
  return `
    <div class="vc2-heat-bar">
      <div class="vc2-heat-fill" style="width:${pct}%"></div>
    </div>
    <span class="vc2-heat-text">heat ${pct.toFixed(0)}</span>
  `;
}

async function renderValuechain2Page() {
  const root = document.getElementById('valuechain-view');
  if (!root) return;

  root.innerHTML = `
    <div class="vc2-container">
      <div class="vc2-header">
        <div class="vc2-toolbar">
          <div class="vc2-theme-tabs" id="vc2-theme-tabs"></div>
          <div class="vc2-filters">
            <select id="vc2-filter-reflection" class="vc2-select" title="반영도 필터">
              <option value="all">전체</option>
              <option value="unreflected">🔥 미반영만</option>
              <option value="bottleneck">★ 병목만</option>
            </select>
            <select id="vc2-filter-market" class="vc2-select" title="시장 필터">
              <option value="all">KR + US</option>
              <option value="kr">KR만</option>
              <option value="us">US만</option>
            </select>
            <button class="vc2-refresh-btn" onclick="vc2Refresh()" title="캐시 갱신">↻</button>
          </div>
        </div>
      </div>

      <div class="vc2-three-panel">
        <div class="vc2-panel vc2-panel-layers" id="vc2-panel-layers">
          <div class="vc2-panel-title">레이어</div>
          <div class="vc2-loading">테마 로딩 중...</div>
        </div>
        <div class="vc2-panel vc2-panel-segments" id="vc2-panel-segments">
          <div class="vc2-panel-title">세그먼트</div>
          <div class="vc2-empty">레이어를 선택하세요</div>
        </div>
        <div class="vc2-panel vc2-panel-stocks" id="vc2-panel-stocks">
          <div class="vc2-panel-title">종목 · 반영도</div>
          <div class="vc2-empty">세그먼트를 선택하세요</div>
        </div>
      </div>
    </div>
  `;

  document.getElementById('vc2-filter-reflection')?.addEventListener('change', (e) => {
    VC2_STATE.filterReflection = e.target.value;
    vc2RenderSegmentsPanel();
    vc2RenderStocksPanel();
  });
  document.getElementById('vc2-filter-market')?.addEventListener('change', (e) => {
    VC2_STATE.filterMarket = e.target.value;
    vc2RenderStocksPanel();
  });

  await vc2LoadThemes();
}

async function vc2LoadThemes() {
  try {
    const resp = await fetch('/api/valuechain2/themes');
    const data = await resp.json();
    VC2_STATE.themes = data.themes || [];
    vc2RenderThemeTabs();
    if (VC2_STATE.themes.length > 0) {
      await vc2SelectTheme(VC2_STATE.themes[0].theme_id);
    }
  } catch (e) {
    console.error('vc2 themes load error:', e);
    const tabs = document.getElementById('vc2-theme-tabs');
    if (tabs) tabs.innerHTML = '<div class="vc2-error">테마 로딩 실패</div>';
  }
}

function vc2RenderThemeTabs() {
  const tabs = document.getElementById('vc2-theme-tabs');
  if (!tabs) return;
  tabs.innerHTML = VC2_STATE.themes.map(t => `
    <button class="vc2-theme-tab ${VC2_STATE.currentTheme === t.theme_id ? 'active' : ''}"
            data-theme="${_escHtml(t.theme_id)}"
            style="--theme-color:${_escHtml(t.color || '#3b82f6')}"
            onclick="vc2SelectTheme('${_escHtml(t.theme_id)}')">
      <span class="vc2-tab-dot" style="background:${_escHtml(t.color || '#3b82f6')}"></span>
      <span>${_escHtml(t.name || t.theme_id)}</span>
      <span class="vc2-tab-count">${t.layer_count || 0}</span>
    </button>
  `).join('');
}

async function vc2SelectTheme(themeId) {
  if (VC2_STATE.loading) return;
  VC2_STATE.loading = true;
  VC2_STATE.currentTheme = themeId;
  VC2_STATE.currentLayer = null;
  VC2_STATE.currentSegment = null;
  VC2_STATE.layerData = null;
  VC2_STATE.segmentData = null;

  vc2RenderThemeTabs();
  const lp = document.getElementById('vc2-panel-layers');
  if (lp) lp.innerHTML = `<div class="vc2-panel-title">레이어</div><div class="vc2-loading">로딩 중...</div>`;
  const sp = document.getElementById('vc2-panel-segments');
  if (sp) sp.innerHTML = `<div class="vc2-panel-title">세그먼트</div><div class="vc2-empty">레이어를 선택하세요</div>`;
  const tp = document.getElementById('vc2-panel-stocks');
  if (tp) tp.innerHTML = `<div class="vc2-panel-title">종목 · 반영도</div><div class="vc2-empty">세그먼트를 선택하세요</div>`;

  try {
    const resp = await fetch(`/api/valuechain2/layers/${themeId}`);
    const data = await resp.json();
    VC2_STATE.layerData = data;
    vc2RenderLayersPanel();
    const layerIds = Object.keys(data.layers || {});
    if (layerIds.length > 0) {
      vc2SelectLayer(layerIds[0]);
    }
  } catch (e) {
    console.error('vc2 layers load error:', e);
    const lp2 = document.getElementById('vc2-panel-layers');
    if (lp2) lp2.innerHTML = `<div class="vc2-panel-title">레이어</div><div class="vc2-error">레이어 로딩 실패</div>`;
  } finally {
    VC2_STATE.loading = false;
  }
}

function vc2RenderLayersPanel() {
  const panel = document.getElementById('vc2-panel-layers');
  if (!panel || !VC2_STATE.layerData) return;
  const layers = VC2_STATE.layerData.layers || {};
  const sortedIds = Object.keys(layers).sort((a, b) => (layers[a].order || 0) - (layers[b].order || 0));

  panel.innerHTML = `
    <div class="vc2-panel-title">레이어 <span class="vc2-count">${sortedIds.length}</span></div>
    <div class="vc2-layer-list">
      ${sortedIds.map(lid => {
        const l = layers[lid];
        const score = l.layer_score || 50;
        const badgeColor = vc2BadgeColor(score);
        const isActive = VC2_STATE.currentLayer === lid;
        const segs = l.segments_brief || [];
        const avgHeat = segs.length ? (segs.reduce((sum, s) => sum + (s.heat || 0), 0) / segs.length) : 0;
        return `
          <div class="vc2-layer-card ${isActive ? 'active' : ''}"
               onclick="vc2SelectLayer('${_escHtml(lid)}')">
            <div class="vc2-layer-head">
              <div class="vc2-layer-name">
                <span class="vc2-layer-en">${_escHtml(l.name_en || '')}</span>
                <span class="vc2-layer-kr">${_escHtml(l.name_kr || lid)}${l.has_bottleneck ? ' <span class="vc2-bottleneck">★</span>' : ''}</span>
              </div>
              <div class="vc2-score-badge" style="background:${badgeColor}">
                ${score.toFixed(0)}
              </div>
            </div>
            ${vc2HeatBar(avgHeat)}
            <div class="vc2-layer-meta">세그먼트 ${l.segment_count || 0}</div>
          </div>
        `;
      }).join('')}
    </div>
  `;
}

function vc2SelectLayer(layerId) {
  VC2_STATE.currentLayer = layerId;
  VC2_STATE.currentSegment = null;
  vc2RenderLayersPanel();
  vc2RenderSegmentsPanel();

  if (!VC2_STATE.layerData) return;
  const layer = VC2_STATE.layerData.layers[layerId];
  if (layer && layer.segments_brief && layer.segments_brief.length > 0) {
    const filtered = vc2FilterSegments(layer.segments_brief);
    if (filtered.length > 0) {
      vc2SelectSegment(filtered[0].segment_id);
    } else {
      const sp = document.getElementById('vc2-panel-stocks');
      if (sp) sp.innerHTML = `<div class="vc2-panel-title">종목 · 반영도</div><div class="vc2-empty">필터에 해당하는 세그먼트가 없습니다</div>`;
    }
  }
}

function vc2FilterSegments(segments) {
  let result = segments.slice();
  if (VC2_STATE.filterReflection === 'unreflected') {
    result = result.filter(s => (s.segment_score || 50) < 50);
  } else if (VC2_STATE.filterReflection === 'bottleneck') {
    result = result.filter(s => s.is_bottleneck);
  }
  return result;
}

function vc2RenderSegmentsPanel() {
  const panel = document.getElementById('vc2-panel-segments');
  if (!panel) return;
  if (!VC2_STATE.layerData || !VC2_STATE.currentLayer) {
    panel.innerHTML = `<div class="vc2-panel-title">세그먼트</div><div class="vc2-empty">레이어를 선택하세요</div>`;
    return;
  }
  const layer = VC2_STATE.layerData.layers[VC2_STATE.currentLayer];
  if (!layer) return;
  const segments = vc2FilterSegments(layer.segments_brief || []);

  if (segments.length === 0) {
    panel.innerHTML = `
      <div class="vc2-panel-title">세그먼트 · ${_escHtml(layer.name_kr || '')}</div>
      <div class="vc2-empty">필터 조건에 맞는 세그먼트 없음</div>
    `;
    return;
  }

  panel.innerHTML = `
    <div class="vc2-panel-title">세그먼트 <span class="vc2-count">${segments.length}</span></div>
    <div class="vc2-layer-context">${_escHtml(layer.name_en || '')} / ${_escHtml(layer.name_kr || '')}</div>
    <div class="vc2-segment-list">
      ${segments.map(s => {
        const score = s.segment_score || 50;
        const badgeColor = vc2BadgeColor(score);
        const isActive = VC2_STATE.currentSegment === s.segment_id;
        const labelInfo = vc2BadgeLabel(score);
        return `
          <div class="vc2-segment-card ${isActive ? 'active' : ''}"
               onclick="vc2SelectSegment('${_escHtml(s.segment_id)}')">
            <div class="vc2-segment-head">
              <div class="vc2-segment-name">
                <span class="vc2-segment-en">${_escHtml(s.name_en || '')}</span>
                <span class="vc2-segment-kr">${_escHtml(s.name_kr || s.segment_id)}${s.is_bottleneck ? ' <span class="vc2-bottleneck">★ 병목</span>' : ''}</span>
              </div>
              <div class="vc2-score-badge" style="background:${badgeColor}">
                ${labelInfo.emoji} ${score.toFixed(0)}
              </div>
            </div>
            ${vc2HeatBar(s.heat || 0)}
            <div class="vc2-segment-meta">${labelInfo.label} · 종목 ${s.stock_count || 0}</div>
          </div>
        `;
      }).join('')}
    </div>
  `;
}

async function vc2SelectSegment(segmentId) {
  VC2_STATE.currentSegment = segmentId;
  vc2RenderSegmentsPanel();

  const panel = document.getElementById('vc2-panel-stocks');
  if (panel) panel.innerHTML = `<div class="vc2-panel-title">종목 · 반영도</div><div class="vc2-loading">로딩 중...</div>`;

  try {
    const url = `/api/valuechain2/segment/${VC2_STATE.currentTheme}/${VC2_STATE.currentLayer}/${segmentId}`;
    const resp = await fetch(url);
    const data = await resp.json();
    VC2_STATE.segmentData = data;
    vc2RenderStocksPanel();
  } catch (e) {
    console.error('vc2 segment load error:', e);
    if (panel) panel.innerHTML = `<div class="vc2-panel-title">종목 · 반영도</div><div class="vc2-error">로딩 실패</div>`;
  }
}

function vc2RenderStocksPanel() {
  const panel = document.getElementById('vc2-panel-stocks');
  if (!panel) return;
  if (!VC2_STATE.segmentData) {
    panel.innerHTML = `<div class="vc2-panel-title">종목 · 반영도</div><div class="vc2-empty">세그먼트를 선택하세요</div>`;
    return;
  }
  const sd = VC2_STATE.segmentData;
  let stocks = (sd.stocks || []).slice();

  if (VC2_STATE.filterMarket === 'kr') {
    stocks = stocks.filter(s => /^\d{6}$/.test(s.code));
  } else if (VC2_STATE.filterMarket === 'us') {
    stocks = stocks.filter(s => !/^\d{6}$/.test(s.code));
  }

  const segScore = sd.segment_score || 50;
  const segColor = vc2BadgeColor(segScore);

  const summaryHtml = `
    <div class="vc2-segment-summary">
      <div class="vc2-segment-summary-row">
        <div>
          <div class="vc2-segment-summary-name">
            ${_escHtml(sd.name_kr || '')}
            ${sd.is_bottleneck ? '<span class="vc2-bottleneck">★ 병목</span>' : ''}
          </div>
          <div class="vc2-segment-summary-score" style="background:${segColor}">
            세그먼트 ${segScore.toFixed(0)} · 시총 가중 평균
          </div>
          <div class="vc2-segment-summary-meta">
            heat ${(sd.heat || 0).toFixed(0)} · 키워드 ${sd.keyword_count || 0}개 · 종목 ${sd.stock_count || 0}
          </div>
        </div>
        <button class="vc2-add-btn" onclick="vc2PromptAddStock()" title="종목 편입">＋ 편입</button>
      </div>
      <div class="vc2-score-legend">
        <span class="vc2-score-legend-title">점수 구성:</span>
        <span class="vc2-legend-item"><b>52주(50%)</b> 주가 상승 = 시장 반영도 ↑</span>
        <span class="vc2-legend-item"><b>밸류에이션(25%)</b> PER/EV-EBITDA 낮음 = 미반영 ↑</span>
        <span class="vc2-legend-item"><b>heat(25%)</b> 뉴스 열기 = 시장 반영 가속</span>
      </div>
    </div>
  `;

  if (stocks.length === 0) {
    panel.innerHTML = `
      <div class="vc2-panel-title">종목 · ${_escHtml(sd.name_kr || '')}</div>
      ${summaryHtml}
      <div class="vc2-empty">필터 조건에 맞는 종목 없음</div>
    `;
    return;
  }

  panel.innerHTML = `
    <div class="vc2-panel-title">종목 <span class="vc2-count">${stocks.length}</span></div>
    ${summaryHtml}
    <div class="vc2-stock-list">
      ${stocks.map(s => vc2RenderStockCard(s)).join('')}
    </div>
  `;
}

function vc2RenderStockCard(s) {
  const score = s.score || 50;
  const badgeColor = vc2BadgeColor(score);
  const isUS = !/^\d{6}$/.test(s.code);
  const marketBadge = isUS ? 'US' : 'KR';
  const market = isUS ? 'us' : 'kr';
  const bd = s.breakdown || {};
  const ret52w = bd.return_52w_pct;
  const ret52wText = (ret52w !== null && ret52w !== undefined)
    ? `${ret52w >= 0 ? '+' : ''}${Number(ret52w).toFixed(1)}%`
    : '–';
  const ret52wColor = (ret52w || 0) >= 0 ? 'var(--color-up)' : 'var(--color-down)';
  const valMethod = bd.valuation_method || 'VAL';
  const valScore = bd.score_valuation;
  const valRaw = bd.valuation_raw;
  const score52w = bd.score_52w;
  const scoreHeat = bd.score_heat;
  const cap = s.market_cap || 0;
  const capText = cap > 1e12 ? `${(cap / 1e12).toFixed(1)}조`
                : cap > 1e8  ? `${(cap / 1e8).toFixed(0)}억` : '-';

  // 가중치 분해 — title 툴팁 + expandable 섹션
  const fmtScore = v => (v !== undefined && v !== null) ? Number(v).toFixed(0) : '-';
  const valRawText = (valRaw !== undefined && valRaw !== null) ? Number(valRaw).toFixed(1) : '-';
  const w52 = score52w !== undefined ? (score52w * 0.5).toFixed(1) : '?';
  const wVal = valScore !== undefined ? (valScore * 0.25).toFixed(1) : '?';
  const wHeat = scoreHeat !== undefined ? (scoreHeat * 0.25).toFixed(1) : '?';

  return `
    <div class="vc2-stock-card" data-code="${_escHtml(s.code)}" data-name="${_escHtml(s.name || '')}" data-market="${market}">
      <div class="vc2-stock-head" onclick="vc2OpenStockChart('${_escHtml(s.code)}', '${_escHtml(s.name || '')}', '${market}')">
        <div class="vc2-stock-id">
          <span class="vc2-market-badge ${market}">${marketBadge}</span>
          <span class="vc2-stock-name">${_escHtml(s.name || s.code)}</span>
          <span class="vc2-stock-code">${_escHtml(s.code)}</span>
        </div>
        <div class="vc2-stock-actions">
          <div class="vc2-score-badge" style="background:${badgeColor}"
               title="반영도 ${score.toFixed(1)} = 52주(${w52}) + ${valMethod}(${wVal}) + heat(${wHeat})">
            ${s.badge || ''} ${score.toFixed(0)}
          </div>
          <button class="vc2-toggle-btn" onclick="event.stopPropagation(); vc2ToggleBreakdown(this)" title="점수 분해">▾</button>
          <button class="vc2-remove-btn" onclick="event.stopPropagation(); vc2RemoveStock('${_escHtml(s.code)}', '${market}', '${_escHtml(s.name || s.code)}')" title="세그먼트에서 편출">✕</button>
        </div>
      </div>
      <div class="vc2-stock-grid" onclick="vc2OpenStockChart('${_escHtml(s.code)}', '${_escHtml(s.name || '')}', '${market}')">
        <div class="vc2-stock-cell" title="최근 52주 주가 수익률 (가중치 50%) — 높을수록 시장이 이미 반영">
          <div class="vc2-cell-label">52주</div>
          <div class="vc2-cell-value" style="color:${ret52wColor}">${ret52wText}</div>
        </div>
        <div class="vc2-stock-cell" title="${valMethod} 원시값 = ${valRawText} / 점수 ${fmtScore(valScore)} (가중치 25%) — 낮을수록 저평가">
          <div class="vc2-cell-label">${_escHtml(valMethod)}</div>
          <div class="vc2-cell-value">${valRawText}</div>
        </div>
        <div class="vc2-stock-cell" title="시가총액 (시총 가중 평균에 사용)">
          <div class="vc2-cell-label">시총</div>
          <div class="vc2-cell-value">${capText}</div>
        </div>
        <div class="vc2-stock-cell" title="반영도 라벨 — 미반영(<30) / 부분 미반영(<50) / 부분 반영(<70) / 상당 반영(<85) / 과열(≥85)">
          <div class="vc2-cell-label">반영</div>
          <div class="vc2-cell-value">${_escHtml(s.label || '-')}</div>
        </div>
      </div>
      <div class="vc2-breakdown" style="display:none">
        <div class="vc2-bd-row">
          <span class="vc2-bd-label">52주 수익률</span>
          <span class="vc2-bd-raw">${ret52wText}</span>
          <div class="vc2-bd-bar"><div class="vc2-bd-fill" style="width:${fmtScore(score52w)}%;background:#3b82f6"></div></div>
          <span class="vc2-bd-score">${fmtScore(score52w)} × 0.5 = <b>${w52}</b></span>
        </div>
        <div class="vc2-bd-row">
          <span class="vc2-bd-label">${_escHtml(valMethod)}</span>
          <span class="vc2-bd-raw">${valRawText}</span>
          <div class="vc2-bd-bar"><div class="vc2-bd-fill" style="width:${fmtScore(valScore)}%;background:#06b6d4"></div></div>
          <span class="vc2-bd-score">${fmtScore(valScore)} × 0.25 = <b>${wVal}</b></span>
        </div>
        <div class="vc2-bd-row">
          <span class="vc2-bd-label">heat</span>
          <span class="vc2-bd-raw">${fmtScore(scoreHeat)}</span>
          <div class="vc2-bd-bar"><div class="vc2-bd-fill" style="width:${fmtScore(scoreHeat)}%;background:#f59e0b"></div></div>
          <span class="vc2-bd-score">${fmtScore(scoreHeat)} × 0.25 = <b>${wHeat}</b></span>
        </div>
        <div class="vc2-bd-total">합계 = ${w52} + ${wVal} + ${wHeat} = <b>${score.toFixed(1)}</b></div>
      </div>
    </div>
  `;
}

function vc2ToggleBreakdown(btn) {
  const card = btn.closest('.vc2-stock-card');
  if (!card) return;
  const bd = card.querySelector('.vc2-breakdown');
  if (!bd) return;
  const open = bd.style.display !== 'none';
  bd.style.display = open ? 'none' : 'block';
  btn.textContent = open ? '▾' : '▴';
}

async function vc2RemoveStock(code, market, name) {
  if (!VC2_STATE.currentTheme || !VC2_STATE.currentLayer || !VC2_STATE.currentSegment) return;
  if (!confirm(`「${name} (${code})」을(를) 이 세그먼트에서 편출하시겠습니까?`)) return;
  try {
    const url = `/api/valuechain2/segment/${VC2_STATE.currentTheme}/${VC2_STATE.currentLayer}/${VC2_STATE.currentSegment}/manage`;
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'remove', code, market }),
    });
    const result = await resp.json();
    if (result.error) {
      alert('편출 실패: ' + result.error);
      return;
    }
    // 현재 테마 다시 로드 (점수 재계산)
    await vc2SelectTheme(VC2_STATE.currentTheme);
    // 같은 세그먼트로 복귀
    if (VC2_STATE.layerData) {
      vc2SelectLayer(VC2_STATE.currentLayer);
    }
  } catch (e) {
    alert('편출 실패: ' + e.message);
  }
}

async function vc2PromptAddStock() {
  if (!VC2_STATE.currentTheme || !VC2_STATE.currentLayer || !VC2_STATE.currentSegment) {
    alert('세그먼트를 먼저 선택하세요');
    return;
  }
  const input = prompt('편입할 종목 코드를 입력하세요\n(KR: 6자리 숫자, 예: 005930 / US: 티커, 예: NVDA)');
  if (!input) return;
  const code = input.trim().toUpperCase();
  if (!code) return;
  const market = (/^\d{6}$/.test(code)) ? 'kr' : 'us';
  try {
    const url = `/api/valuechain2/segment/${VC2_STATE.currentTheme}/${VC2_STATE.currentLayer}/${VC2_STATE.currentSegment}/manage`;
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'add', code, market }),
    });
    const result = await resp.json();
    if (result.error) {
      alert('편입 실패: ' + result.error);
      return;
    }
    if (result.status === 'noop') {
      alert(result.message || '이미 편입되어 있습니다');
      return;
    }
    await vc2SelectTheme(VC2_STATE.currentTheme);
    if (VC2_STATE.layerData) {
      vc2SelectLayer(VC2_STATE.currentLayer);
    }
  } catch (e) {
    alert('편입 실패: ' + e.message);
  }
}

function vc2OpenStockChart(code, name, market) {
  if (typeof openChartPanel === 'function') {
    openChartPanel(code, name || null, market || (/^\d{6}$/.test(code) ? 'kr' : 'us'));
  } else {
    console.warn('openChartPanel not found:', code);
  }
}

async function vc2Refresh() {
  try {
    await fetch('/api/valuechain2/refresh', { method: 'POST' });
    if (VC2_STATE.currentTheme) {
      await vc2SelectTheme(VC2_STATE.currentTheme);
    }
  } catch (e) {
    console.error('vc2 refresh error:', e);
  }
}

// 전역 노출 (onclick 핸들러)
window.renderValuechain2Page = renderValuechain2Page;
window.vc2SelectTheme   = vc2SelectTheme;
window.vc2SelectLayer   = vc2SelectLayer;
window.vc2SelectSegment = vc2SelectSegment;
window.vc2OpenStockChart = vc2OpenStockChart;
window.vc2Refresh       = vc2Refresh;
window.vc2ToggleBreakdown = vc2ToggleBreakdown;
window.vc2RemoveStock   = vc2RemoveStock;
window.vc2PromptAddStock = vc2PromptAddStock;

// ============================================================
// Phase 4-5-2-A: 신규 페이지 placeholder 5종
// (verification, journal, ops-freshness, ops-cron, ops-health)
// ============================================================

function _phEsc(s) {
  if (s == null) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

// ============================================================
// Phase 4-5-3: 검증 시트 (renderVerification)
// 4-5-2-C 호환: params.code 있으면 종목 화면, 없으면 검색 화면
// ============================================================

function _verifFmtNum(n) {
  if (n == null) return '0';
  return Math.round(n).toLocaleString('ko-KR');
}

function _verifMcapDisplay(mcap) {
  if (!mcap) return '—';
  if (mcap >= 1e12) return (mcap / 1e12).toFixed(2) + '조원';
  if (mcap >= 1e8)  return Math.round(mcap / 1e8) + '억원';
  return _verifFmtNum(mcap) + '원';
}

function renderVerificationPlaceholder(params) {
  params = params || (APP && APP.pageParams) || {};
  const code = params.code || null;
  const c = document.getElementById('page-verification');
  if (!c) return;
  if (code) {
    renderVerificationStockView(c, code);
  } else {
    renderVerificationSearchView(c);
  }
}

// 검색 화면 — params.code 없을 때
function renderVerificationSearchView(container) {
  container.innerHTML = `
    <div class="page-header">
      <h2>✅ 검증 시트</h2>
      <p class="page-desc">KUVIC 5단계 분석 + 자동 데이터 통합</p>
    </div>
    <div class="verif-search-container">
      <div class="verif-search-icon">🔍</div>
      <div class="verif-search-title">종목명 또는 코드 입력</div>
      <div class="verif-search-hint">예: 코미코, 183300, 이수페타시스</div>
      <div class="verif-search-box">
        <input type="text" id="verif-search-input" class="verif-search-input"
               placeholder="2글자 이상 입력 시 자동완성" autocomplete="off"/>
        <div id="verif-search-dropdown" class="verif-search-dropdown"></div>
      </div>
      <div class="verif-search-info">
        KR 종목 + 활성 유니버스 검색 가능
      </div>
    </div>`;

  const input = document.getElementById('verif-search-input');
  const dropdown = document.getElementById('verif-search-dropdown');
  let searchTimer = null;

  input.addEventListener('input', (e) => {
    const q = e.target.value.trim();
    if (searchTimer) clearTimeout(searchTimer);
    if (q.length < 2) {
      dropdown.innerHTML = '';
      dropdown.classList.remove('active');
      return;
    }
    searchTimer = setTimeout(() => _verifDoSearch(q), 200);
  });

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const first = dropdown.querySelector('.verif-search-item');
      if (first) first.click();
    } else if (e.key === 'Escape') {
      dropdown.innerHTML = '';
      dropdown.classList.remove('active');
    }
  });

  // 외부 클릭 시 닫기 (한 번만 등록)
  if (!document._verifOutsideBound) {
    document._verifOutsideBound = true;
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.verif-search-box')) {
        const dd = document.getElementById('verif-search-dropdown');
        if (dd) dd.classList.remove('active');
      }
    });
  }

  setTimeout(() => input.focus(), 100);
}

async function _verifDoSearch(query) {
  const dropdown = document.getElementById('verif-search-dropdown');
  if (!dropdown) return;
  try {
    const r = await fetch(`/api/stock_search?q=${encodeURIComponent(query)}`);
    if (!r.ok) {
      dropdown.innerHTML = '<div class="verif-search-empty">검색 실패</div>';
      dropdown.classList.add('active');
      return;
    }
    const data = await r.json();
    // /api/stock_search 응답: bare list [{code, name}, ...]
    const results = Array.isArray(data) ? data
                  : (data.results || data.items || data.stocks || []);
    if (!results.length) {
      dropdown.innerHTML = '<div class="verif-search-empty">검색 결과 없음</div>';
      dropdown.classList.add('active');
      return;
    }
    const items = results.slice(0, 15);
    dropdown.innerHTML = items.map(s => {
      const code = s.code || s.stock_code || '';
      const name = s.name || s.stock_name || '';
      // KR 6자리는 KR, 그 외(US 티커)는 US
      const marketTag = /^\d{6}$/.test(code) ? 'KR' : 'US';
      return `
        <div class="verif-search-item" data-code="${_phEsc(code)}">
          <span class="verif-item-code">${_phEsc(code)}</span>
          <span class="verif-item-name">${_phEsc(name)}</span>
          <span class="verif-item-market">${_phEsc(marketTag)}</span>
        </div>`;
    }).join('');
    dropdown.classList.add('active');
    dropdown.querySelectorAll('.verif-search-item').forEach(el => {
      el.addEventListener('click', () => {
        const c = el.dataset.code;
        if (c) navigateTo('verification', { code: c });
      });
    });
  } catch (e) {
    console.error('[verification] search error:', e);
    dropdown.innerHTML = '<div class="verif-search-empty">검색 오류</div>';
    dropdown.classList.add('active');
  }
}

// 종목 정보 화면 — params.code 있을 때. stock + prefill 병렬 fetch.
async function renderVerificationStockView(container, code) {
  container.innerHTML = `
    <div class="page-header">
      <h2>✅ 검증 시트 — ${_phEsc(code)}</h2>
      <a href="/verification" class="verif-back-link"
         onclick="event.preventDefault(); navigateTo('verification');">← 다른 종목 검색</a>
    </div>
    <div class="verif-loading">종목 정보 + 5단계 prefill 로딩 중…</div>`;
  try {
    const [stockResp, prefillResp] = await Promise.all([
      fetch(`/api/verification/stock/${encodeURIComponent(code)}`),
      fetch(`/api/verification/${encodeURIComponent(code)}/prefill`),
    ]);
    const data = await stockResp.json();
    if (!data.found) {
      _verifRenderNotFound(container, code, data.error);
      return;
    }
    // prefill 은 실패해도 stock 카드는 그림
    let prefill = null;
    if (prefillResp.ok) {
      try { prefill = await prefillResp.json(); }
      catch (e) { console.warn('[verification] prefill parse failed:', e); }
    } else {
      console.warn('[verification] prefill HTTP', prefillResp.status);
    }
    _verifRenderStockCard(container, data, prefill);
  } catch (e) {
    console.error('[verification] stock fetch failed:', e);
    _verifRenderError(container, code, e.message);
  }
}

function _verifRenderStockCard(container, data, prefill) {
  const cp = data.change_pct || 0;
  const upDown = cp > 0 ? 'up' : cp < 0 ? 'down' : 'flat';
  const arrow  = cp > 0 ? '↑' : cp < 0 ? '↓' : '·';
  const sign   = cp > 0 ? '+' : '';

  let badgeHtml = '';
  if (typeof freshnessBadge === 'function' && data.price_meta) {
    badgeHtml = freshnessBadge(data.price_meta.freshness_label, {
      title: data.price_meta.age_human,
    });
  }
  const mcap = _verifMcapDisplay(data.market_cap);
  const errs = (prefill && prefill.errors) || [];

  container.innerHTML = `
    <div class="page-header">
      <h2>✅ 검증 시트 — ${_phEsc(data.name)}</h2>
      <a href="/verification" class="verif-back-link"
         onclick="event.preventDefault(); navigateTo('verification');">← 다른 종목 검색</a>
    </div>

    <div class="verif-stock-card">
      <div class="verif-stock-header">
        <div class="verif-stock-title">
          <span class="verif-stock-name">${_phEsc(data.name)}</span>
          <span class="verif-stock-code">(${_phEsc(data.code)})</span>
        </div>
        <span class="verif-stock-market">${_phEsc(data.market)}</span>
      </div>
      ${data.sector ? `<div class="verif-stock-sector">${_phEsc(data.sector)}</div>` : ''}
      <div class="verif-stock-grid">
        <div class="verif-stock-field">
          <div class="verif-field-label">현재가</div>
          <div class="verif-field-value verif-price">
            ${_verifFmtNum(data.current_price)}원 ${badgeHtml}
          </div>
        </div>
        <div class="verif-stock-field verif-${upDown}">
          <div class="verif-field-label">변동률</div>
          <div class="verif-field-value verif-change">
            ${sign}${cp.toFixed(2)}% ${arrow}${_verifFmtNum(Math.abs(data.change_amount || 0))}
          </div>
        </div>
        <div class="verif-stock-field">
          <div class="verif-field-label">시가총액</div>
          <div class="verif-field-value">${_phEsc(mcap)}</div>
        </div>
        <div class="verif-stock-field">
          <div class="verif-field-label">52주 범위 (${data.week52_days || 0}일)</div>
          <div class="verif-field-value verif-range">
            ${data.week52_low ? _verifFmtNum(data.week52_low) : '—'} ~
            ${data.week52_high ? _verifFmtNum(data.week52_high) : '—'}
          </div>
        </div>
      </div>
    </div>

    ${errs.length ? `
    <div class="verif-errors-banner">
      ⚠️ 일부 데이터 불완전: ${errs.map(_phEsc).join(' · ')}
    </div>` : ''}

    <div class="verif-steps">
      ${prefill ? _verifRenderStep1(prefill.step1) : _verifStepLoadingRow('STEP 1', '밸류체인 유추')}
      ${prefill ? _verifRenderStep2(prefill.step2) : _verifStepLoadingRow('STEP 2', '개별 요인')}
      ${_verifRenderStep3()}
      ${prefill ? _verifRenderStep4(prefill.step4, prefill.current_price) : _verifStepLoadingRow('STEP 4', 'TAM 모델링')}
      ${prefill ? _verifRenderStep5(prefill.step5) : _verifStepLoadingRow('STEP 5', '주가 검증')}
    </div>`;
}

// ============================================================
// 검증 시트 — Step 카드 렌더러 (4-5-4)
// ============================================================

function _verifStepLoadingRow(num, name) {
  return `
    <div class="verif-step-card">
      <div class="verif-step-head">
        <span class="verif-step-num">${_phEsc(num)}</span>
        <span class="verif-step-name">${_phEsc(name)}</span>
        <span class="verif-step-mode">⏳ 로딩 중</span>
      </div>
    </div>`;
}

function _verifBadgeFresh(fresh) {
  if (!fresh) return '';
  if (typeof freshnessBadge !== 'function') return '';
  return freshnessBadge(fresh.label, { title: fresh.age_human });
}

function _verifModeBadge(mode) {
  // mode: 'auto' | 'manual' | 'mixed'
  const map = {
    auto:   { cls: 'verif-mode-auto',   icon: '🟢', label: '자동' },
    manual: { cls: 'verif-mode-manual', icon: '👤', label: '수동' },
    mixed:  { cls: 'verif-mode-mixed',  icon: '🔵', label: '혼합' },
  };
  const m = map[mode] || map.auto;
  return `<span class="verif-step-mode ${m.cls}">${m.icon} ${m.label}</span>`;
}

function _verifSrcLine(label, fresh) {
  if (!fresh) return '';
  return `<span class="verif-src">${_phEsc(label)} ${_verifBadgeFresh(fresh)}</span>`;
}

// STEP 1: 밸류체인 유추 (자동 + KUVIC)
function _verifRenderStep1(step1) {
  if (!step1) return '';
  const vc = step1.valuechain || {};
  const ku = step1.kuvic_analysis;
  const hasKuvic = !!ku;
  const mode = hasKuvic ? 'mixed' : 'auto';

  const segments = (vc.segments || []);
  let vcHtml = '';
  if (segments.length) {
    vcHtml = segments.map(s => `
      <div class="verif-vc-row">
        <span class="verif-vc-theme">${_phEsc(s.theme_id || '—')}</span>
        <span class="verif-vc-arrow">›</span>
        <span class="verif-vc-layer">${_phEsc(s.layer_id || '—')}</span>
        <span class="verif-vc-arrow">›</span>
        <span class="verif-vc-seg">${_phEsc(s.segment_name_kr || s.segment_id || '—')}</span>
        ${s.is_bottleneck ? '<span class="verif-bottleneck">⚡ 병목</span>' : ''}
      </div>`).join('');
  } else {
    vcHtml = '<div class="verif-vc-empty">매핑된 segment 없음</div>';
  }

  let kuvicHtml = '';
  if (hasKuvic) {
    const tags = (ku.tags || []).map(t => `<span class="verif-tag">${_phEsc(t)}</span>`).join('');
    kuvicHtml = `
      <div class="verif-kuvic-block">
        <div class="verif-kuvic-thesis">${_phEsc(ku.thesis || '')}</div>
        <div class="verif-kuvic-meta">
          ${ku.conclusion ? `<span class="verif-kuvic-tag">결론 ${_phEsc(ku.conclusion)}</span>` : ''}
          ${ku.priority ? `<span class="verif-kuvic-tag">${_phEsc(ku.priority)}</span>` : ''}
          ${ku.session_date ? `<span class="verif-kuvic-date">${_phEsc(ku.session_date)}</span>` : ''}
        </div>
        ${tags ? `<div class="verif-tags">${tags}</div>` : ''}
        <div class="verif-step-srcline">
          ${_verifSrcLine('KUVIC 일지', ku.freshness)}
        </div>
      </div>`;
  } else {
    kuvicHtml = `
      <div class="verif-kuvic-empty">
        📝 KUVIC 분석 일지 없음 — Phase 4-5-9 에서 추가 입력 가능
      </div>`;
  }

  return `
    <div class="verif-step-card">
      <div class="verif-step-head">
        <span class="verif-step-num">STEP 1</span>
        <span class="verif-step-name">밸류체인 유추</span>
        ${_verifModeBadge(mode)}
      </div>
      <div class="verif-step-body">
        <div class="verif-step-subtitle">🤖 자동 발견</div>
        ${vcHtml}
        <div class="verif-step-srcline">
          ${_verifSrcLine('valuechain_map', vc.freshness)}
        </div>
        <div class="verif-step-divider"></div>
        <div class="verif-step-subtitle">📝 KUVIC 분석</div>
        ${kuvicHtml}
      </div>
    </div>`;
}

// STEP 2: 개별 요인 (재무 + 밸류에이션 + 어닝)
function _verifRenderStep2(step2) {
  if (!step2) return '';
  const fin = step2.financial;
  const val = step2.valuation || {};
  const earn = step2.earnings;

  // 재무
  let finHtml;
  if (fin) {
    const arrowMap = { up: '↑', down: '↓', flat: '→' };
    const arrow = arrowMap[fin.opm_trend] || '→';
    const arrowCls = fin.opm_trend === 'up' ? 'verif-trend-up'
                   : fin.opm_trend === 'down' ? 'verif-trend-down' : 'verif-trend-flat';
    // 매출 단위: 원 → 억원
    const revEok = fin.avg_revenue_4q ? (fin.avg_revenue_4q / 1e8) : null;
    finHtml = `
      <div class="verif-kv-grid">
        <div class="verif-kv"><span class="verif-k">최근 4Q 매출 평균</span>
          <span class="verif-v">${revEok ? _verifFmtNum(revEok) + '억' : '—'}</span></div>
        <div class="verif-kv"><span class="verif-k">OPM 평균</span>
          <span class="verif-v">${fin.avg_opm_4q != null ? fin.avg_opm_4q.toFixed(1) + '%' : '—'}
            <span class="${arrowCls}">${arrow}</span></span></div>
      </div>
      <div class="verif-step-srcline">
        ${_verifSrcLine('financial_quarterly', fin.freshness)}
      </div>`;
  } else {
    finHtml = '<div class="verif-empty">분기 재무 데이터 없음</div>';
  }

  // 밸류에이션
  const fper = val.fwd_per != null ? val.fwd_per.toFixed(1) + 'x' : '—';
  const fperPct = val.fwd_per_band_pct != null ? `P${val.fwd_per_band_pct}` : '—';
  const fperPctCls = val.fwd_per_band_pct == null ? '' :
                     (val.fwd_per_band_pct <= 35 ? 'verif-cheap' :
                      val.fwd_per_band_pct >= 65 ? 'verif-expensive' : '');
  const fperHint = val.fwd_per_band_pct == null ? '' :
                   (val.fwd_per_band_pct <= 35 ? '저평가' :
                    val.fwd_per_band_pct >= 65 ? '고평가' : '중앙');
  const valHtml = `
    <div class="verif-kv-grid">
      <div class="verif-kv"><span class="verif-k">Fwd PER</span>
        <span class="verif-v">${fper} <span class="${fperPctCls}">${fperPct}${fperHint ? ' — ' + fperHint : ''}</span></span></div>
      <div class="verif-kv"><span class="verif-k">OPM 추정</span>
        <span class="verif-v">${val.opm_estimate != null ? val.opm_estimate.toFixed(1) + '%' : '—'}
          ${val.opm_source ? `<span class="verif-hint">${_phEsc(val.opm_source)}</span>` : ''}</span></div>
      ${val.per_band ? `
      <div class="verif-kv"><span class="verif-k">5년 PER 분위</span>
        <span class="verif-v">P25 ${val.per_band.p25?.toFixed(1)} · P50 ${val.per_band.p50?.toFixed(1)} · P75 ${val.per_band.p75?.toFixed(1)} (${val.per_band.quarters}Q)</span></div>` : ''}
    </div>
    <div class="verif-step-srcline">
      ${_verifSrcLine('valuation_band', val.freshness)}
    </div>`;

  // 어닝
  let earnHtml;
  if (earn) {
    const sigCls = earn.signal && earn.signal.includes('BEAT') ? 'verif-sig-beat'
                 : earn.signal && earn.signal.includes('MISS') ? 'verif-sig-miss'
                 : 'verif-sig-neutral';
    earnHtml = `
      <div class="verif-kv-grid">
        <div class="verif-kv"><span class="verif-k">${earn.year}Q${earn.quarter} 시그널</span>
          <span class="verif-v"><span class="verif-earn-sig ${sigCls}">${_phEsc(earn.signal || '—')}</span>
          (P${earn.priority || '—'})</span></div>
        ${earn.revenue_surprise_pct != null || earn.op_surprise_pct != null ? `
        <div class="verif-kv"><span class="verif-k">매출/영업익 서프</span>
          <span class="verif-v">매출 ${earn.revenue_surprise_pct != null ? earn.revenue_surprise_pct.toFixed(1) + '%' : '—'}
            / 영업익 ${earn.op_surprise_pct != null ? earn.op_surprise_pct.toFixed(1) + '%' : '—'}</span></div>` : ''}
        ${earn.note ? `<div class="verif-kv verif-kv-full"><span class="verif-k">노트</span>
          <span class="verif-v">${_phEsc(earn.note)}</span></div>` : ''}
      </div>
      <div class="verif-step-srcline">
        ${_verifSrcLine('earnings_surprise', earn.freshness)}
      </div>`;
  } else {
    earnHtml = '<div class="verif-empty">최근 어닝 시그널 없음</div>';
  }

  return `
    <div class="verif-step-card">
      <div class="verif-step-head">
        <span class="verif-step-num">STEP 2</span>
        <span class="verif-step-name">개별 요인</span>
        ${_verifModeBadge('auto')}
      </div>
      <div class="verif-step-body">
        <div class="verif-step-subtitle">📊 매출/이익 구조 (분기)</div>
        ${finHtml}
        <div class="verif-step-divider"></div>
        <div class="verif-step-subtitle">📐 5년 밸류에이션 위치</div>
        ${valHtml}
        <div class="verif-step-divider"></div>
        <div class="verif-step-subtitle">💎 최근 어닝</div>
        ${earnHtml}
      </div>
    </div>`;
}

// STEP 3: 동종 비교 — 4-5-5 자리만
function _verifRenderStep3() {
  return `
    <div class="verif-step-card">
      <div class="verif-step-head">
        <span class="verif-step-num">STEP 3</span>
        <span class="verif-step-name">동종 비교</span>
        ${_verifModeBadge('manual')}
      </div>
      <div class="verif-step-body">
        <div class="verif-step-placeholder">
          ⏳ Phase 4-5-5 에서 KUVIC 수동 입력 폼 추가
          <div class="verif-step-placeholder-hint">동종 기업, 비교 포인트, 차별화 요소</div>
        </div>
      </div>
    </div>`;
}

// STEP 4: TAM 모델링 (자동)
function _verifRenderStep4(step4, currentPrice) {
  if (!step4 || !step4.tam) return '';
  const tam = step4.tam;
  const cp = currentPrice || tam.current_price;

  const cell = (sc, key) => {
    const v = tam[sc] && tam[sc][key];
    return (v == null) ? '—' : v;
  };
  const tpCell = sc => {
    const tp = tam[sc] && tam[sc].tp;
    const up = tam[sc] && tam[sc].upside_pct;
    if (tp == null) return '—';
    const upCls = up == null ? '' : (up >= 0 ? 'verif-up-cell' : 'verif-down-cell');
    const upStr = up == null ? '' : ` (${up > 0 ? '+' : ''}${up.toFixed(1)}%)`;
    return `${_verifFmtNum(tp)} <span class="${upCls}">${upStr}</span>`;
  };

  return `
    <div class="verif-step-card">
      <div class="verif-step-head">
        <span class="verif-step-num">STEP 4</span>
        <span class="verif-step-name">TAM 모델링</span>
        ${_verifModeBadge('auto')}
      </div>
      <div class="verif-step-body">
        <div class="verif-step-subtitle">🎯 시나리오별 목표가</div>
        <table class="verif-tam-table">
          <thead><tr><th></th><th>Bear</th><th>Base</th><th>Bull</th></tr></thead>
          <tbody>
            <tr><td>EPS</td>
              <td>${cell('bear','eps') != null ? _verifFmtNum(cell('bear','eps')) : '—'}</td>
              <td>${cell('base','eps') != null ? _verifFmtNum(cell('base','eps')) : '—'}</td>
              <td>${cell('bull','eps') != null ? _verifFmtNum(cell('bull','eps')) : '—'}</td></tr>
            <tr><td>PER</td>
              <td>${cell('bear','per') != null ? cell('bear','per').toFixed(2) + 'x' : '—'}</td>
              <td>${cell('base','per') != null ? cell('base','per').toFixed(2) + 'x' : '—'}</td>
              <td>${cell('bull','per') != null ? cell('bull','per').toFixed(2) + 'x' : '—'}</td></tr>
            <tr><td>TP</td>
              <td>${tpCell('bear')}</td>
              <td>${tpCell('base')}</td>
              <td>${tpCell('bull')}</td></tr>
          </tbody>
        </table>
        <div class="verif-tam-meta">
          현재가 <strong>${_verifFmtNum(cp)}원</strong> 기준
          · method: ${_phEsc(tam.method || '—')}
          ${tam.consensus_tp ? ` · consensus TP ${_verifFmtNum(tam.consensus_tp)}` : ''}
        </div>
        <div class="verif-tam-srcgrid">
          <div class="verif-tam-srcline">
            <span class="verif-src-tag">Bear EPS</span> ${_phEsc(cell('bear','eps_source') || '—')}
            · <span class="verif-src-tag">PER</span> ${_phEsc(cell('bear','per_source') || '—')}
          </div>
          <div class="verif-tam-srcline">
            <span class="verif-src-tag">Base EPS</span> ${_phEsc(cell('base','eps_source') || '—')}
            · <span class="verif-src-tag">PER</span> ${_phEsc(cell('base','per_source') || '—')}
          </div>
          <div class="verif-tam-srcline">
            <span class="verif-src-tag">Bull EPS</span> ${_phEsc(cell('bull','eps_source') || '—')}
            · <span class="verif-src-tag">PER</span> ${_phEsc(cell('bull','per_source') || '—')}
          </div>
        </div>
        <div class="verif-step-srcline">
          ${_verifSrcLine('tam_modeler (밸류에이션 밴드+컨센)', tam.freshness)}
        </div>
      </div>
    </div>`;
}

// STEP 5: 주가 검증 (52주 + 반영도)
function _verifRenderStep5(step5) {
  if (!step5) return '';
  const pos = step5.price_position;
  const ret = step5.returns || {};
  const refl = step5.reflection || {};
  const km = step5.kuvic_match;

  // 52주 슬라이더 (text 기반)
  let sliderHtml = '<div class="verif-empty">52주 데이터 없음</div>';
  if (pos && pos.week52_low && pos.week52_high) {
    const pct = pos.position_pct || 50;
    sliderHtml = `
      <div class="verif-slider-row">
        <span class="verif-slider-low">${_verifFmtNum(pos.week52_low)}</span>
        <div class="verif-slider-track">
          <div class="verif-slider-fill" style="left:${pct}%"></div>
          <div class="verif-slider-current" style="left:${pct}%">
            <div class="verif-slider-pin">▼</div>
            <div class="verif-slider-pin-label">${_verifFmtNum(pos.current)}</div>
          </div>
        </div>
        <span class="verif-slider-high">${_verifFmtNum(pos.week52_high)}</span>
      </div>
      <div class="verif-slider-meta">위치 ${pct.toFixed(1)}% — ${
        pct < 30 ? '하단 (저가권)'
        : pct < 70 ? '중간'
        : '상단 (고가권)'
      }</div>
      <div class="verif-step-srcline">
        ${_verifSrcLine('ohlcv 365일', pos.freshness)}
      </div>`;
  }

  // 수익률
  const retCell = (v) => v == null ? '—' :
    `<span class="${v > 0 ? 'verif-up-cell' : v < 0 ? 'verif-down-cell' : ''}">${v > 0 ? '+' : ''}${v.toFixed(1)}%</span>`;
  const retsHtml = `
    <div class="verif-kv-grid">
      <div class="verif-kv"><span class="verif-k">52주 수익률</span><span class="verif-v">${retCell(ret.return_52w)}</span></div>
      <div class="verif-kv"><span class="verif-k">6개월</span><span class="verif-v">${retCell(ret.return_6m)}</span></div>
      <div class="verif-kv"><span class="verif-k">3개월</span><span class="verif-v">${retCell(ret.return_3m)}</span></div>
    </div>`;

  // 자동 반영도
  const labelCls = ({
    '미반영': 'verif-refl-undervalued',
    '부분반영': 'verif-refl-mid',
    '반영완료': 'verif-refl-priced',
    '과열': 'verif-refl-overheated',
    'UNKNOWN': 'verif-refl-unknown',
  })[refl.auto_label] || 'verif-refl-mid';
  const reflIcon = ({
    '미반영': '🔥', '부분반영': '🟡', '반영완료': '⚪', '과열': '🔴',
  })[refl.auto_label] || '⚪';
  const reasonsHtml = (refl.reasons || []).map(r => `<li>${_phEsc(r)}</li>`).join('');

  // KUVIC 비교
  let kmHtml = '';
  if (km && km.auto_label) {
    const matches = (km.kuvic_conclusion === 'BUY' && (km.auto_label === '미반영' || km.auto_label === '부분반영'))
                 || (km.kuvic_conclusion === 'SELL' && (km.auto_label === '반영완료' || km.auto_label === '과열'))
                 || (km.kuvic_conclusion === 'HOLD' && km.auto_label === '부분반영');
    kmHtml = `
      <div class="verif-step-divider"></div>
      <div class="verif-step-subtitle">🤝 KUVIC 비교</div>
      <div class="verif-kuvic-match ${matches ? 'verif-match-ok' : 'verif-match-gap'}">
        <div class="verif-kuvic-match-row">
          <span class="verif-k">자동 라벨</span>
          <span class="verif-v">${reflIcon} ${_phEsc(km.auto_label)}</span>
        </div>
        <div class="verif-kuvic-match-row">
          <span class="verif-k">KUVIC 결론</span>
          <span class="verif-v">${_phEsc(km.kuvic_conclusion || '—')}</span>
        </div>
        <div class="verif-kuvic-match-row">
          <span class="verif-k">일치 여부</span>
          <span class="verif-v">${matches ? '✅ 일치' : '⚠️ 불일치 — 재검토'}</span>
        </div>
      </div>`;
  }

  return `
    <div class="verif-step-card">
      <div class="verif-step-head">
        <span class="verif-step-num">STEP 5</span>
        <span class="verif-step-name">주가 검증</span>
        ${_verifModeBadge('auto')}
      </div>
      <div class="verif-step-body">
        <div class="verif-step-subtitle">📍 52주 위치</div>
        ${sliderHtml}
        <div class="verif-step-divider"></div>
        <div class="verif-step-subtitle">📈 수익률</div>
        ${retsHtml}
        <div class="verif-step-divider"></div>
        <div class="verif-step-subtitle">🎯 자동 반영도</div>
        <div class="verif-reflection ${labelCls}">
          <div class="verif-refl-label">${reflIcon} ${_phEsc(refl.auto_label || '—')}</div>
          ${refl.vc_score != null ? `<div class="verif-refl-score">vc_score ${refl.vc_score}</div>` : ''}
          <ul class="verif-refl-reasons">${reasonsHtml}</ul>
        </div>
        ${kmHtml}
      </div>
    </div>`;
}

function _verifRenderNotFound(container, code, errorMsg) {
  container.innerHTML = `
    <div class="page-header">
      <h2>✅ 검증 시트</h2>
      <a href="/verification" class="verif-back-link"
         onclick="event.preventDefault(); navigateTo('verification');">← 다른 종목 검색</a>
    </div>
    <div class="verif-error-card">
      <div class="verif-error-icon">🔍</div>
      <div class="verif-error-title">종목 ${_phEsc(code)}을(를) 찾을 수 없습니다</div>
      <div class="verif-error-desc">${_phEsc(errorMsg || '데이터베이스에 등록되지 않은 종목')}</div>
      <div style="margin-top: 20px;">
        <a href="/verification" class="verif-back-button"
           onclick="event.preventDefault(); navigateTo('verification');">🔍 다른 종목 검색</a>
      </div>
    </div>`;
}

function _verifRenderError(container, code, errorMsg) {
  container.innerHTML = `
    <div class="page-header">
      <h2>✅ 검증 시트 — ${_phEsc(code)}</h2>
      <a href="/verification" class="verif-back-link"
         onclick="event.preventDefault(); navigateTo('verification');">← 다른 종목 검색</a>
    </div>
    <div class="verif-error-card">
      <div class="verif-error-icon">⚠️</div>
      <div class="verif-error-title">데이터 로드 실패</div>
      <div class="verif-error-desc">${_phEsc(errorMsg)}</div>
    </div>`;
}

function renderJournalPlaceholder(params) {
  params = params || (APP && APP.pageParams) || {};
  const journalId = params.id || null;
  const c = document.getElementById('page-journal');
  if (!c) return;
  const idBanner = journalId ? `
    <div style="margin: 12px 0; padding: 10px 14px; background: rgba(34,197,94,0.08);
                border-left: 3px solid #22c55e; border-radius: 6px; font-size: 13px;">
      📓 일지 ID: <strong>${_phEsc(journalId)}</strong>
      <span style="font-size:11px;color:var(--text-muted,#888);margin-left:8px;">
        (Phase 4-5-9 구축 시 단일 조회 화면 대상)
      </span>
    </div>` : '';
  c.innerHTML = `
    <div class="page-header">
      <h2>📓 분석 일지</h2>
      <p class="page-desc">analysis_journal CRUD — KUVIC 분석 보관/검색</p>
    </div>
    ${idBanner}
    <div class="placeholder-card">
      <div class="placeholder-icon">🚧</div>
      <div class="placeholder-title">Phase 4-5-9에서 구축 예정</div>
      <div class="placeholder-desc">
        <p>현재 데이터: <strong id="journal-count-target">…</strong>건</p>
        <p style="font-size:12px;color:var(--text-secondary,#94a3b8);">
          임시 확인: <code>curl http://localhost:8080/api/journal/recent</code>
        </p>
      </div>
    </div>`;
  // 카운트 fetch (실패 시 무시)
  fetch('/api/journal/recent?limit=1')
    .then(r => r.ok ? r.json() : null)
    .then(d => {
      const t = document.getElementById('journal-count-target');
      if (!t || !d) return;
      const n = (d.count != null) ? d.count
              : (Array.isArray(d) ? d.length
              : (d.items ? d.items.length : '?'));
      t.textContent = String(n);
    })
    .catch(() => {
      const t = document.getElementById('journal-count-target');
      if (t) t.textContent = '?';
    });
}

// ============================================================
// Phase 4-5-12: 운영 대시보드 — 공통 헬퍼
// ============================================================
const _OPS_REFRESH_INTERVAL = 30000;  // 30초
const _opsTimers = {};

function _opsClearTimer(key) {
  if (_opsTimers[key]) { clearTimeout(_opsTimers[key]); _opsTimers[key] = null; }
}
function _opsScheduleNext(key, fn) {
  _opsClearTimer(key);
  _opsTimers[key] = setTimeout(fn, _OPS_REFRESH_INTERVAL);
}

function _opsScoreClass(score) {
  if (score == null) return '';
  if (score >= 80) return 'ok';
  if (score >= 50) return 'warn';
  return 'bad';
}
function _opsHumanSec(sec) {
  if (sec == null) return '—';
  if (sec < 0) return `${Math.abs(sec)}초 지남`;
  if (sec < 60) return `${sec}초 후`;
  if (sec < 3600) return `${Math.floor(sec/60)}분 후`;
  if (sec < 86400) return `${Math.floor(sec/3600)}시간 후`;
  return `${Math.floor(sec/86400)}일 후`;
}

// ============================================================
// ops-freshness — 데이터 신선도 모니터
// ============================================================
async function renderOpsFreshnessPlaceholder() {
  const c = document.getElementById('page-ops-freshness');
  if (!c) return;
  if (!c.dataset.inited) {
    c.dataset.inited = '1';
    c.innerHTML = `
      <div class="ops-header">
        <h2>🌡 데이터 신선도</h2>
        <div class="ops-meta">
          <span id="ops-freshness-ts">로딩…</span>
          <button class="ops-btn" id="ops-freshness-refresh">↻ 새로고침</button>
        </div>
      </div>
      <div class="ops-score-grid" id="ops-freshness-cards"></div>
      <div class="ops-filter-bar" id="ops-freshness-filters"></div>
      <table class="ops-table">
        <thead><tr>
          <th>소스</th><th>카테고리</th><th>라벨</th>
          <th>나이</th><th>마지막 갱신</th><th>다음 예상</th>
        </tr></thead>
        <tbody id="ops-freshness-rows">
          <tr><td colspan="6" class="ops-loading">…</td></tr>
        </tbody>
      </table>`;
    document.getElementById('ops-freshness-refresh')
      .addEventListener('click', renderOpsFreshnessPlaceholder);
  }
  try {
    const r = await fetch('/api/freshness/all', { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    const sources = d.sources || [];
    const sum = d.summary || {};
    const bl = sum.by_label || {};

    document.getElementById('ops-freshness-ts').textContent =
      `검사: ${sum.checked_at || '—'}`;

    const score = sum.health_score ?? 0;
    document.getElementById('ops-freshness-cards').innerHTML = `
      <div class="ops-score-card">
        <div class="ops-score-label">헬스 점수</div>
        <div class="ops-score-value ${_opsScoreClass(score)}">${score}<span style="font-size:14px;color:var(--text-muted,#888);">/100</span></div>
      </div>
      <div class="ops-score-card">
        <div class="ops-score-label">LIVE</div>
        <div class="ops-score-value ok">🟢 ${bl.LIVE || 0}</div>
        <div class="ops-score-sub">정상</div>
      </div>
      <div class="ops-score-card">
        <div class="ops-score-label">DELAY</div>
        <div class="ops-score-value warn">🟡 ${bl.DELAY || 0}</div>
        <div class="ops-score-sub">지연</div>
      </div>
      <div class="ops-score-card">
        <div class="ops-score-label">ARCHIVE / NO_DATA</div>
        <div class="ops-score-value bad">⚪ ${bl.ARCHIVE || 0} / 🔴 ${bl.NO_DATA || 0}</div>
        <div class="ops-score-sub">갱신 필요</div>
      </div>
      <div class="ops-score-card">
        <div class="ops-score-label">MANUAL</div>
        <div class="ops-score-value" style="color:#3b82f6;">👤 ${bl.MANUAL || 0}</div>
        <div class="ops-score-sub">사용자 입력</div>
      </div>`;

    const cats = {};
    sources.forEach(s => { cats[s.category] = (cats[s.category] || 0) + 1; });
    const activeCat = c._activeCat || 'all';
    const filterRow = ['all', ...Object.keys(cats)].map(k => {
      const cnt = k === 'all' ? sources.length : cats[k];
      const cls = (activeCat === k) ? 'ops-filter active' : 'ops-filter';
      return `<button class="${cls}" data-cat="${_phEsc(k)}">${_phEsc(k)}<span class="count">${cnt}</span></button>`;
    }).join('');
    const fb = document.getElementById('ops-freshness-filters');
    fb.innerHTML = filterRow;
    fb.querySelectorAll('.ops-filter').forEach(b => {
      b.addEventListener('click', () => {
        c._activeCat = b.getAttribute('data-cat');
        renderOpsFreshnessPlaceholder();
      });
    });

    const filtered = activeCat === 'all'
      ? sources
      : sources.filter(s => s.category === activeCat);
    const order = { NO_DATA: 0, ARCHIVE: 1, DELAY: 2, MANUAL: 3, LIVE: 4 };
    const sorted = filtered.slice().sort(
      (a, b) => (order[a.label] ?? 9) - (order[b.label] ?? 9));
    if (!sorted.length) {
      document.getElementById('ops-freshness-rows').innerHTML =
        '<tr><td colspan="6" class="ops-empty">소스 없음</td></tr>';
    } else {
      document.getElementById('ops-freshness-rows').innerHTML =
        sorted.map(s => {
          const isIssue = ['ARCHIVE','DELAY','NO_DATA'].includes(s.label) && s.category !== 'manual';
          const cls = isIssue ? 'ops-row-issue' : '';
          const badge = (typeof freshnessBadge === 'function')
            ? freshnessBadge(s.label, { title: s.age_human })
            : `<span>${_phEsc(s.label)}</span>`;
          return `<tr class="${cls}">
            <td><strong>${_phEsc(s.name_kr)}</strong>
                <div style="font-size:10.5px;color:var(--text-muted,#888);">${_phEsc(s.source)}</div></td>
            <td><span class="ops-cat-tag">${_phEsc(s.category)}</span></td>
            <td>${badge}</td>
            <td>${_phEsc(s.age_human || '—')}</td>
            <td class="ops-when">${_phEsc(s.last_updated_kst || '—')}</td>
            <td class="ops-when">${_phEsc(s.expected_next || '—')}</td>
          </tr>`;
        }).join('');
    }
  } catch (e) {
    document.getElementById('ops-freshness-rows').innerHTML =
      `<tr><td colspan="6" class="ops-empty" style="color:#ef4444;">로드 실패: ${_phEsc(e.message)}</td></tr>`;
  }
  _opsScheduleNext('freshness', renderOpsFreshnessPlaceholder);
}

// ============================================================
// ops-cron — APScheduler 잡 모니터
// ============================================================
async function renderOpsCronPlaceholder() {
  const c = document.getElementById('page-ops-cron');
  if (!c) return;
  if (!c.dataset.inited) {
    c.dataset.inited = '1';
    c.innerHTML = `
      <div class="ops-header">
        <h2>⏱ Cron 모니터</h2>
        <div class="ops-meta">
          <span id="ops-cron-ts">로딩…</span>
          <button class="ops-btn" id="ops-cron-refresh">↻ 새로고침</button>
        </div>
      </div>
      <div class="ops-score-grid" id="ops-cron-cards"></div>
      <div class="ops-filter-bar" id="ops-cron-filters"></div>
      <table class="ops-table">
        <thead><tr>
          <th>잡 ID</th><th>카테고리</th><th>트리거</th>
          <th>다음 실행</th><th>남은 시간</th><th>액션</th>
        </tr></thead>
        <tbody id="ops-cron-rows">
          <tr><td colspan="6" class="ops-loading">…</td></tr>
        </tbody>
      </table>`;
    document.getElementById('ops-cron-refresh')
      .addEventListener('click', renderOpsCronPlaceholder);
  }
  try {
    const r = await fetch('/api/ops/cron/jobs', { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    const jobs = d.jobs || [];
    const cats = d.categories || {};

    document.getElementById('ops-cron-ts').textContent =
      `검사: ${d.checked_at || '—'} · 스케줄러: ${d.scheduler_running ? '✅ 실행 중' : '❌ 정지'}`;

    const dueCount = jobs.filter(j => (j.next_run_in_sec ?? 999) <= 60).length;
    const pausedCount = jobs.filter(j => j.status === 'paused').length;
    document.getElementById('ops-cron-cards').innerHTML = `
      <div class="ops-score-card">
        <div class="ops-score-label">등록된 잡</div>
        <div class="ops-score-value">${jobs.length}</div>
        <div class="ops-score-sub">${d.scheduler_running ? '실행 중' : '정지'}</div>
      </div>
      <div class="ops-score-card">
        <div class="ops-score-label">1분 내 실행</div>
        <div class="ops-score-value ${dueCount ? 'ok' : ''}">${dueCount}</div>
      </div>
      <div class="ops-score-card">
        <div class="ops-score-label">일시 정지</div>
        <div class="ops-score-value ${pausedCount ? 'warn' : ''}">${pausedCount}</div>
      </div>`;

    const activeCat = c._activeCat || 'all';
    const filterRow = ['all', ...Object.keys(cats)].map(k => {
      const cnt = k === 'all' ? jobs.length : cats[k];
      const cls = (activeCat === k) ? 'ops-filter active' : 'ops-filter';
      return `<button class="${cls}" data-cat="${_phEsc(k)}">${_phEsc(k)}<span class="count">${cnt}</span></button>`;
    }).join('');
    const fb = document.getElementById('ops-cron-filters');
    fb.innerHTML = filterRow;
    fb.querySelectorAll('.ops-filter').forEach(b => {
      b.addEventListener('click', () => {
        c._activeCat = b.getAttribute('data-cat');
        renderOpsCronPlaceholder();
      });
    });

    const filtered = activeCat === 'all' ? jobs : jobs.filter(j => j.category === activeCat);
    if (!filtered.length) {
      document.getElementById('ops-cron-rows').innerHTML =
        '<tr><td colspan="6" class="ops-empty">잡 없음</td></tr>';
    } else {
      document.getElementById('ops-cron-rows').innerHTML =
        filtered.map(j => {
          const sec = j.next_run_in_sec;
          let whenCls = 'ops-when';
          if (sec != null && sec <= 0) whenCls = 'ops-when ops-when-due';
          else if (sec != null && sec <= 300) whenCls = 'ops-when ops-when-soon';
          return `<tr>
            <td><strong>${_phEsc(j.id)}</strong>
                <div style="font-size:10.5px;color:var(--text-muted,#888);">${_phEsc(j.func || '')}</div></td>
            <td><span class="ops-cat-tag">${_phEsc(j.category_label || j.category)}</span></td>
            <td style="font-family:ui-monospace,monospace;font-size:11px;">${_phEsc(j.trigger)}</td>
            <td class="ops-when">${_phEsc(j.next_run_at || '—')}</td>
            <td class="${whenCls}">${_phEsc(_opsHumanSec(sec))}</td>
            <td>
              <button class="ops-btn" data-trigger-id="${_phEsc(j.id)}">즉시 실행</button>
            </td>
          </tr>`;
        }).join('');
      document.querySelectorAll('[data-trigger-id]').forEach(btn => {
        btn.addEventListener('click', async (ev) => {
          const id = ev.target.getAttribute('data-trigger-id');
          if (!confirm(`'${id}' 잡을 지금 즉시 실행할까요?`)) return;
          ev.target.disabled = true;
          ev.target.textContent = '실행 중…';
          try {
            const rr = await fetch(`/api/ops/cron/trigger/${encodeURIComponent(id)}`,
                                   { method: 'POST' });
            const jd = await rr.json();
            if (jd.ok) {
              ev.target.textContent = '✓ 발행';
              setTimeout(renderOpsCronPlaceholder, 2000);
            } else {
              ev.target.textContent = '실패';
              alert('트리거 실패: ' + (jd.error || 'unknown'));
              ev.target.disabled = false;
              ev.target.textContent = '즉시 실행';
            }
          } catch (e) {
            alert('네트워크 오류');
            ev.target.disabled = false;
            ev.target.textContent = '즉시 실행';
          }
        });
      });
    }
  } catch (e) {
    document.getElementById('ops-cron-rows').innerHTML =
      `<tr><td colspan="6" class="ops-empty" style="color:#ef4444;">로드 실패: ${_phEsc(e.message)}</td></tr>`;
  }
  _opsScheduleNext('cron', renderOpsCronPlaceholder);
}

// ============================================================
// ops-health — 종합 헬스 대시보드
// ============================================================
async function renderOpsHealthPlaceholder() {
  const c = document.getElementById('page-ops-health');
  if (!c) return;
  if (!c.dataset.inited) {
    c.dataset.inited = '1';
    c.innerHTML = `
      <div class="ops-header">
        <h2>📊 헬스 대시보드</h2>
        <div class="ops-meta">
          <span id="ops-health-ts">로딩…</span>
          <button class="ops-btn" id="ops-health-refresh">↻ 새로고침</button>
        </div>
      </div>
      <div class="ops-score-grid" id="ops-health-cards"></div>
      <h3 style="margin:20px 0 8px;font-size:14px;">🔍 데이터 신선도 문제</h3>
      <div id="ops-health-issues"></div>
      <h3 style="margin:20px 0 8px;font-size:14px;">💾 DB 통계</h3>
      <table class="ops-table"><tbody id="ops-health-db"></tbody></table>
      <h3 style="margin:20px 0 8px;font-size:14px;">📨 텔레그램 + 🤖 LLM 캐시</h3>
      <table class="ops-table"><tbody id="ops-health-tg"></tbody></table>`;
    document.getElementById('ops-health-refresh')
      .addEventListener('click', renderOpsHealthPlaceholder);
  }
  try {
    const r = await fetch('/api/ops/health', { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    document.getElementById('ops-health-ts').textContent =
      `검사: ${d.checked_at || '—'} · 가동: ${Math.floor((d.uptime_sec || 0) / 60)}분`;

    const score = d.overall_score ?? 0;
    const fr = d.freshness || {};
    const sc = d.scheduler || {};
    document.getElementById('ops-health-cards').innerHTML = `
      <div class="ops-score-card">
        <div class="ops-score-label">종합 점수</div>
        <div class="ops-score-value ${_opsScoreClass(score)}">${score}<span style="font-size:14px;color:var(--text-muted,#888);">/100</span></div>
        <div class="ops-score-sub">freshness 60% · scheduler 30% · db 10%</div>
      </div>
      <div class="ops-score-card">
        <div class="ops-score-label">신선도</div>
        <div class="ops-score-value ${_opsScoreClass(fr.health_score)}">${fr.health_score ?? '—'}<span style="font-size:14px;color:var(--text-muted,#888);">/100</span></div>
        <div class="ops-score-sub">문제 ${fr.issues_count ?? 0}건 / 전체 ${fr.total ?? 0}</div>
      </div>
      <div class="ops-score-card">
        <div class="ops-score-label">스케줄러</div>
        <div class="ops-score-value ${sc.running ? 'ok' : 'bad'}">${sc.running ? '실행' : '정지'}</div>
        <div class="ops-score-sub">활성 ${sc.jobs_active ?? 0} / 정지 ${sc.jobs_paused ?? 0}</div>
      </div>`;

    const issues = (fr.issues_top || []);
    document.getElementById('ops-health-issues').innerHTML = issues.length
      ? `<table class="ops-table"><tbody>${issues.map(i => `
          <tr class="ops-row-issue">
            <td><strong>${_phEsc(i.name_kr || i.source)}</strong></td>
            <td><span class="ops-cat-tag">${_phEsc(i.category)}</span></td>
            <td>${(typeof freshnessBadge === 'function')
                ? freshnessBadge(i.label) : _phEsc(i.label)}</td>
            <td>${_phEsc(i.age_human)}</td>
          </tr>`).join('')}</tbody></table>`
      : '<div class="ops-empty">문제 소스 없음 ✅</div>';

    const db = d.db || {};
    const rows = db.rows || {};
    document.getElementById('ops-health-db').innerHTML = `
      <tr><td><strong>크기</strong></td><td>${db.size_mb ?? '—'} MB</td></tr>
      ${Object.entries(rows).map(([t, n]) =>
        `<tr><td>${_phEsc(t)}</td><td>${(n != null ? n.toLocaleString() : '—')} rows</td></tr>`
      ).join('')}`;

    const tg = d.telegram || {};
    const llm = d.llm_cache || {};
    document.getElementById('ops-health-tg').innerHTML = `
      <tr><td><strong>텔레그램 최근 24h</strong></td><td>${tg.last_24h ?? 0} 건</td></tr>
      <tr><td>텔레그램 최근 7d</td><td>${tg.last_7d ?? 0} 건</td></tr>
      <tr><td>텔레그램 누적</td><td>전송 ${tg.sent_total ?? 0} / 실패 ${tg.fail_total ?? 0}</td></tr>
      <tr><td><strong>LLM 캐시</strong></td>
          <td>엔트리 ${llm.entries ?? 0} · 히트 누적 ${llm.total_hits ?? 0} · ${llm.size_mb ?? 0} MB</td></tr>`;
  } catch (e) {
    document.getElementById('ops-health-cards').innerHTML =
      `<div class="ops-empty" style="color:#ef4444;">로드 실패: ${_phEsc(e.message)}</div>`;
  }
  _opsScheduleNext('health', renderOpsHealthPlaceholder);
}

// PAGE_RENDERERS에 신규 placeholder 등록
if (typeof PAGE_RENDERERS !== 'undefined') {
  PAGE_RENDERERS.verification     = renderVerificationPlaceholder;
  // PAGE_RENDERERS.journal 은 위에서 const 정의 시점에 이미 placeholder로 교체됨
  PAGE_RENDERERS['ops-freshness'] = renderOpsFreshnessPlaceholder;
  PAGE_RENDERERS['ops-cron']      = renderOpsCronPlaceholder;
  PAGE_RENDERERS['ops-health']    = renderOpsHealthPlaceholder;
}

window.renderVerificationPlaceholder  = renderVerificationPlaceholder;
window.renderJournalPlaceholder       = renderJournalPlaceholder;
window.renderOpsFreshnessPlaceholder  = renderOpsFreshnessPlaceholder;
window.renderOpsCronPlaceholder       = renderOpsCronPlaceholder;
window.renderOpsHealthPlaceholder     = renderOpsHealthPlaceholder;
