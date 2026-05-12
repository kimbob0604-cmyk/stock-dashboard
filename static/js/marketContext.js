/* Step 4-5-2-B: 사이드바 시간 컨텍스트 배지
 *
 *   - /api/market/context 호출 → #market-context-badge 렌더
 *   - 자동 갱신: 장중 30초, 그 외 60초
 *   - 수동 갱신: window.refreshMarketContext()
 */
(function () {
  'use strict';

  let timer = null;
  const TARGET_ID = 'market-context-badge';
  const BASE_INTERVAL = { OPEN: 30000, BEFORE: 60000, AFTER: 60000, CLOSED: 60000 };

  function _esc(s) {
    if (s == null) return '';
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function _fmtNum(n) {
    if (n == null) return '-';
    return Number(n).toLocaleString('ko-KR', {
      minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
  }

  function _idxRow(name, idx) {
    if (!idx || idx.value == null) return '';
    const chg = idx.change;
    const sign = (chg != null && chg >= 0) ? '+' : '';
    const cls = (chg == null) ? 'mc-flat'
              : (chg > 0 ? 'mc-up' : chg < 0 ? 'mc-down' : 'mc-flat');
    const chgStr = (chg != null) ? `${sign}${chg.toFixed(2)}%` : '—';
    return `<div class="mc-idx ${cls}">`
      + `<span class="mc-idx-name">${_esc(name)}</span>`
      + `<span class="mc-idx-val">${_esc(_fmtNum(idx.value))}</span>`
      + `<span class="mc-idx-chg">${_esc(chgStr)}</span>`
      + `</div>`;
  }

  function buildHtml(ctx) {
    const showIndices = ['OPEN', 'AFTER', 'BEFORE'].includes(ctx.state);
    let html = ''
      + `<div class="mc-header">`
      +   `<span class="mc-emoji">${_esc(ctx.state_emoji || '⚪')}</span>`
      +   `<span class="mc-state">${_esc(ctx.state_label || ctx.state)}</span>`
      +   `<span class="mc-time">${_esc(ctx.now_kst)} ${_esc(ctx.weekday_kr || '')}</span>`
      + `</div>`;

    if (showIndices) {
      const rows = _idxRow('KOSPI', ctx.kospi) + _idxRow('KOSDAQ', ctx.kosdaq);
      if (rows) html += `<div class="mc-indices">${rows}</div>`;
    }

    if (ctx.state === 'BEFORE' && ctx.time_until_open) {
      html += `<div class="mc-countdown">개장까지 ${_esc(ctx.time_until_open)}</div>`;
    } else if (ctx.next_trading_label && ctx.state !== 'OPEN') {
      let line = `다음 ${_esc(ctx.next_trading_label)}`;
      if (ctx.time_until_next_trading) line += ` · ${_esc(ctx.time_until_next_trading)} 후`;
      html += `<div class="mc-next-trading">${line}</div>`;
    }
    return html;
  }

  function render(ctx) {
    const el = document.getElementById(TARGET_ID);
    if (!el) return;
    el.classList.remove('mc-empty', 'mc-open', 'mc-after', 'mc-before', 'mc-closed');
    el.classList.add('mc-' + (ctx.state || 'closed').toLowerCase());
    el.innerHTML = buildHtml(ctx);
  }

  function renderError() {
    const el = document.getElementById(TARGET_ID);
    if (!el) return;
    el.classList.add('mc-empty');
    el.innerHTML = '';  // CSS empty placeholder가 표시
  }

  async function fetchAndRender() {
    try {
      const r = await fetch('/api/market/context', { cache: 'no-store' });
      if (!r.ok) { renderError(); scheduleNext(null); return; }
      const ctx = await r.json();
      render(ctx);
      scheduleNext(ctx.state);
    } catch (e) {
      renderError();
      scheduleNext(null);
    }
  }

  function scheduleNext(state) {
    if (timer) clearTimeout(timer);
    const ms = BASE_INTERVAL[state] || 60000;
    timer = setTimeout(fetchAndRender, ms);
  }

  function start() {
    fetchAndRender();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }

  window.refreshMarketContext = fetchAndRender;
})();
