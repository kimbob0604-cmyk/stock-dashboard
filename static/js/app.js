// ===== static/js/app.js — DUMMY · APP state · 테마맵 렌더링 · 사이드바 · 라우터 · 초기화 =====
'use strict';

// ─────────────────────────────────────────────────────────────────────────────
// DUMMY DATA (data.json fetch 실패 시 사용)
// ─────────────────────────────────────────────────────────────────────────────
const DUMMY = {
  "updated_at": "2026-04-12 15:35:00",
  "kospi":  { "value": 2548.30, "change_pct": -0.72 },
  "kosdaq": { "value":  732.55, "change_pct": -1.05 },
  "new_high_sectors": [],
  "themes": [
    { "id":1,"name":"방산/항공우주","weighted_avg_pct":15.30,"stock_count":22,"active_count":20,
      "stocks":[
        {"code":"012450","name":"한화에어로스페이스","change_pct":18.50,"volume_mn":78000,
          "sparkline":[100,102,105,108,112,115,118,121,119,123,126,130,128,132,135,139,137,141,144,148]},
        {"code":"047810","name":"한국항공우주","change_pct":14.20,"volume_mn":35000,
          "sparkline":[100,101,104,106,109,112,114,117,115,118,121,124,122,125,128,131,129,132,135,138]},
        {"code":"064350","name":"현대로템","change_pct":12.80,"volume_mn":22000,
          "sparkline":[100,102,104,107,109,112,114,117,115,118,120,123,121,124,126,129,127,130,132,135]},
        {"code":"079550","name":"LIG넥스원","change_pct":13.50,"volume_mn":28000,
          "sparkline":[100,103,106,108,111,114,116,119,117,120,123,126,124,127,130,133,131,134,137,140]},
        {"code":"272210","name":"한화시스템","change_pct":11.20,"volume_mn":18000,
          "sparkline":[100,101,103,106,108,111,113,116,114,117,119,122,120,123,125,128,126,129,131,134]}
      ]},
    { "id":2,"name":"2차전지","weighted_avg_pct":8.42,"stock_count":35,"active_count":28,
      "stocks":[
        {"code":"373220","name":"LG에너지솔루션","change_pct":9.50,"volume_mn":45200,
          "sparkline":[100,102,104,103,106,108,107,110,112,110,113,115,113,116,119,117,120,122,121,124]},
        {"code":"006400","name":"삼성SDI","change_pct":7.80,"volume_mn":22100,
          "sparkline":[100,101,103,105,104,107,108,107,110,111,110,113,114,113,116,117,116,119,120,119]},
        {"code":"051910","name":"LG화학","change_pct":6.20,"volume_mn":18700,
          "sparkline":[100,102,101,103,105,104,106,108,107,109,111,110,112,114,113,115,117,116,118,120]},
        {"code":"096770","name":"SK이노베이션","change_pct":10.30,"volume_mn":31500,
          "sparkline":[100,103,102,105,107,106,109,111,110,113,115,114,117,119,118,121,123,122,125,127]},
        {"code":"247540","name":"에코프로비엠","change_pct":12.40,"volume_mn":28900,
          "sparkline":[100,104,103,107,109,108,112,114,113,117,119,118,122,124,123,127,129,128,132,134]},
        {"code":"086520","name":"에코프로","change_pct":14.20,"volume_mn":19800,
          "sparkline":[100,105,104,108,111,110,114,117,116,120,123,122,126,129,128,132,135,134,138,141]}
      ]},
    { "id":3,"name":"5G","weighted_avg_pct":3.60,"stock_count":18,"active_count":14,
      "stocks":[
        {"code":"030200","name":"KT","change_pct":3.80,"volume_mn":18500,
          "sparkline":[100,101,102,101,103,104,103,105,106,105,107,108,107,109,110,109,111,112,111,113]},
        {"code":"017670","name":"SK텔레콤","change_pct":3.50,"volume_mn":22000,
          "sparkline":[100,101,100,102,103,102,104,105,104,106,107,106,108,109,108,110,111,110,112,113]},
        {"code":"032640","name":"LG유플러스","change_pct":3.20,"volume_mn":12300,
          "sparkline":[100,100,101,100,101,102,101,103,104,103,105,104,106,107,106,108,109,108,110,111]}
      ]},
    { "id":4,"name":"조선","weighted_avg_pct":9.60,"stock_count":12,"active_count":10,
      "stocks":[
        {"code":"009540","name":"HD한국조선해양","change_pct":11.20,"volume_mn":55000,
          "sparkline":[100,103,106,108,111,114,116,119,117,120,123,126,124,127,130,133,131,134,137,140]},
        {"code":"010140","name":"삼성중공업","change_pct":8.50,"volume_mn":32000,
          "sparkline":[100,102,105,107,109,112,114,117,115,118,120,123,121,124,126,129,127,130,132,135]},
        {"code":"042660","name":"한화오션","change_pct":9.20,"volume_mn":28500,
          "sparkline":[100,102,104,107,109,112,115,117,115,118,121,123,121,124,127,129,127,130,133,135]}
      ]},
    { "id":5,"name":"반도체","weighted_avg_pct":1.85,"stock_count":42,"active_count":35,
      "stocks":[
        {"code":"005930","name":"삼성전자","change_pct":1.20,"volume_mn":285000,
          "sparkline":[100,101,100,101,102,101,103,102,103,104,103,105,104,105,106,105,107,106,107,108]},
        {"code":"000660","name":"SK하이닉스","change_pct":2.80,"volume_mn":95000,
          "sparkline":[100,101,103,102,104,106,105,107,109,108,110,112,111,113,115,114,116,118,117,119]},
        {"code":"042700","name":"한미반도체","change_pct":4.50,"volume_mn":18500,
          "sparkline":[100,102,101,104,106,105,108,110,109,112,114,113,116,118,117,120,122,121,124,126]}
      ]},
    { "id":6,"name":"AI/빅데이터","weighted_avg_pct":-3.25,"stock_count":20,"active_count":12,
      "stocks":[
        {"code":"035420","name":"NAVER","change_pct":-3.50,"volume_mn":52000,
          "sparkline":[100,99,98,97,99,98,96,95,97,96,94,93,95,94,92,91,93,92,90,89]},
        {"code":"035720","name":"카카오","change_pct":-4.20,"volume_mn":38000,
          "sparkline":[100,98,97,95,97,96,94,92,94,93,91,89,91,90,88,86,88,87,85,83]}
      ]},
    { "id":7,"name":"전기차","weighted_avg_pct":6.75,"stock_count":25,"active_count":18,
      "stocks":[
        {"code":"005380","name":"현대차","change_pct":7.50,"volume_mn":88000,
          "sparkline":[100,102,104,103,106,108,107,110,112,111,114,116,115,118,120,119,122,124,123,126]},
        {"code":"000270","name":"기아","change_pct":6.20,"volume_mn":65000,
          "sparkline":[100,101,103,102,105,107,106,109,111,110,113,115,114,117,119,118,121,123,122,125]}
      ]},
    { "id":8,"name":"바이오/제약","weighted_avg_pct":-5.60,"stock_count":48,"active_count":20,
      "stocks":[
        {"code":"068270","name":"셀트리온","change_pct":-6.20,"volume_mn":42000,
          "sparkline":[100,98,96,94,96,94,91,89,91,89,87,85,87,85,82,80,82,80,77,75]},
        {"code":"207940","name":"삼성바이오로직스","change_pct":-4.80,"volume_mn":38500,
          "sparkline":[100,99,97,95,97,95,92,90,92,90,88,86,88,86,83,81,83,81,79,77]}
      ]},
    { "id":9,"name":"콘텐츠/엔터","weighted_avg_pct":3.10,"stock_count":18,"active_count":14,
      "stocks":[
        {"code":"041510","name":"SM엔터테인먼트","change_pct":4.20,"volume_mn":18500,
          "sparkline":[100,101,103,102,104,106,105,108,107,109,111,110,113,115,114,117,116,118,120,119]},
        {"code":"035900","name":"JYP엔터테인먼트","change_pct":3.50,"volume_mn":14200,
          "sparkline":[100,101,102,104,103,105,107,106,108,110,109,111,113,112,114,116,115,117,119,118]},
        {"code":"352820","name":"하이브","change_pct":2.80,"volume_mn":22000,
          "sparkline":[100,100,102,101,103,105,104,106,108,107,109,111,110,112,114,113,115,117,116,118]},
        {"code":"122870","name":"YG엔터테인먼트","change_pct":2.10,"volume_mn":8900,
          "sparkline":[100,101,100,102,101,103,102,104,103,105,104,106,105,107,106,108,107,109,108,110]}
      ]},
    { "id":10,"name":"게임","weighted_avg_pct":0.42,"stock_count":16,"active_count":10,
      "stocks":[
        {"code":"259960","name":"크래프톤","change_pct":1.80,"volume_mn":12500,
          "sparkline":[100,100,101,100,102,101,103,102,104,103,105,104,106,105,107,106,108,107,109,108]},
        {"code":"036570","name":"엔씨소프트","change_pct":-1.20,"volume_mn":9800,
          "sparkline":[100,100,99,100,99,98,100,99,98,99,98,97,99,98,97,98,97,96,98,97]},
        {"code":"251270","name":"넷마블","change_pct":0.60,"volume_mn":7200,
          "sparkline":[100,100,101,100,101,100,102,101,102,101,103,102,103,102,104,103,104,103,105,104]}
      ]},
    { "id":11,"name":"화장품/뷰티","weighted_avg_pct":4.80,"stock_count":15,"active_count":12,
      "stocks":[
        {"code":"090430","name":"아모레퍼시픽","change_pct":5.50,"volume_mn":32000,
          "sparkline":[100,102,101,104,106,105,108,110,109,112,114,113,116,118,117,120,122,121,124,126]},
        {"code":"161390","name":"한국콜마","change_pct":4.20,"volume_mn":18000,
          "sparkline":[100,101,103,102,105,107,106,109,108,111,113,112,115,117,116,119,121,120,123,125]}
      ]},
    { "id":12,"name":"금융","weighted_avg_pct":-0.85,"stock_count":30,"active_count":25,
      "stocks":[
        {"code":"105560","name":"KB금융","change_pct":-1.20,"volume_mn":78000,
          "sparkline":[100,100,99,100,99,98,100,99,98,99,98,97,99,98,97,98,97,96,98,97]},
        {"code":"055550","name":"신한지주","change_pct":-0.80,"volume_mn":62000,
          "sparkline":[100,100,100,99,100,99,100,100,99,100,99,98,100,99,98,99,98,97,99,98]},
        {"code":"086790","name":"하나금융지주","change_pct":-0.50,"volume_mn":48000,
          "sparkline":[100,100,101,100,100,99,101,100,99,100,100,99,101,100,99,100,99,98,100,99]}
      ]},
    { "id":13,"name":"원전/에너지","weighted_avg_pct":7.20,"stock_count":10,"active_count":8,
      "stocks":[
        {"code":"034020","name":"두산에너빌리티","change_pct":8.90,"volume_mn":28000,
          "sparkline":[100,103,106,108,112,115,118,121,119,122,126,129,127,130,134,137,135,138,142,145]},
        {"code":"015760","name":"한국전력","change_pct":5.80,"volume_mn":42000,
          "sparkline":[100,101,104,106,108,111,114,116,114,117,120,122,120,123,126,128,126,129,132,134]}
      ]},
    { "id":14,"name":"수소/신재생","weighted_avg_pct":-1.42,"stock_count":18,"active_count":10,
      "stocks":[
        {"code":"298380","name":"에이치디현대일렉트릭","change_pct":-2.10,"volume_mn":8500,
          "sparkline":[100,99,100,99,98,99,98,97,98,97,96,97,96,95,96,95,94,95,94,93]},
        {"code":"196300","name":"한화파워시스템","change_pct":-1.50,"volume_mn":5200,
          "sparkline":[100,100,99,100,99,98,100,99,98,99,98,97,99,98,97,98,97,96,98,97]}
      ]},
    { "id":15,"name":"음식료","weighted_avg_pct":-2.10,"stock_count":22,"active_count":15,
      "stocks":[
        {"code":"097950","name":"CJ제일제당","change_pct":-2.80,"volume_mn":24000,
          "sparkline":[100,99,98,99,97,96,98,97,95,94,96,95,93,92,94,93,91,90,92,91]},
        {"code":"004370","name":"농심","change_pct":-1.50,"volume_mn":8500,
          "sparkline":[100,100,99,100,98,97,99,98,97,96,98,97,96,95,97,96,95,94,96,95]},
        {"code":"271560","name":"오리온","change_pct":-1.90,"volume_mn":11000,
          "sparkline":[100,99,100,99,98,99,97,96,97,96,95,96,95,94,95,94,93,94,93,92]}
      ]}
  ]
};

// ─────────────────────────────────────────────────────────────────────────────
// STATE
// ─────────────────────────────────────────────────────────────────────────────
let APP = {
  data:          null,
  page:          'thememap',
  tab:           'map',
  sort:          'weighted_avg_pct',
  stockSort:     'change_pct',
  count:         20,
  selectedTheme: null,
  market:        'kr',   // Phase 14: 'kr' | 'us'
};

// Phase 14: 시장별 엔드포인트 prefix 와 통화 포맷
function _marketBase() { return APP.market === 'us' ? '/api/us' : '/api'; }
function _marketCurrency() { return APP.market === 'us' ? '$' : '₩'; }
function _fmtPrice(v, market) {
  const cur = (market || APP.market) === 'us' ? '$' : '₩';
  if (v == null) return '—';
  if (cur === '$') {
    return '$' + Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  return '₩' + Number(v).toLocaleString('ko-KR');
}

function switchMarket(market) {
  if (market !== 'kr' && market !== 'us') return;
  if (APP.market === market) return;
  APP.market = market;
  document.body.classList.toggle('market-us', market === 'us');
  document.querySelectorAll('#market-toggle .market-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.market === market);
  });
  console.log('[market] switched to', market);

  // 페이지 리다이렉트: 미국은 테마맵 없이 섹터맵이 기본
  if (market === 'us' && (APP.page === 'thememap' || APP.page === 'flow' || APP.page === 'short')) {
    navigateTo('sector');
  } else {
    // 현재 페이지를 다시 렌더 (데이터 소스가 바뀌므로)
    navigateTo(APP.page);
  }
  // 열려있는 차트 패널은 초기화 (다른 시장 종목이 그대로 남지 않게)
  closeChartPanel();
}

// ─────────────────────────────────────────────────────────────────────────────
// WATCHLIST (Phase 12-2) — localStorage 기반, 서버 불필요
// ─────────────────────────────────────────────────────────────────────────────
const _WATCHLIST_KEY = 'watchlist';
let _wlCache = null;

function getWatchlist() {
  if (_wlCache) return _wlCache;
  try { _wlCache = JSON.parse(localStorage.getItem(_WATCHLIST_KEY) || '[]'); }
  catch { _wlCache = []; }
  return _wlCache;
}
function _saveWatchlist(list) {
  _wlCache = list;
  try { localStorage.setItem(_WATCHLIST_KEY, JSON.stringify(list)); }
  catch (e) { console.warn('[watchlist] save failed', e); }
  // Phase 23: 서버에 동기화 (텔레그램 알림용). 실패해도 무해.
  try {
    fetch('/api/watchlist/sync', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: list }),
    }).catch(() => {});
  } catch {}
}
function toggleWatchlist(code, name) {
  let list = getWatchlist();
  // 같은 code 라도 다른 시장이면 별도 엔트리 (예: AAPL vs 가상의 KR 코드)
  const idx = list.findIndex(i => i.code === code && (i.market || 'kr') === APP.market);
  const added = idx < 0;
  if (added) list.push({
    code, name,
    market:  APP.market,
    addedAt: new Date().toISOString(),
  });
  else list.splice(idx, 1);
  _saveWatchlist(list);
  updateWatchlistBadge();
  console.log('[watchlist]', added ? '+' : '-', code, name, APP.market, 'total=', list.length);
  return added;
}
function isInWatchlist(code) {
  return getWatchlist().some(i => i.code === code && (i.market || 'kr') === APP.market);
}
function updateWatchlistBadge() {
  const el = document.getElementById('sidebar-watchlist-badge');
  if (!el) return;
  const n = getWatchlist().length;
  el.textContent = n > 0 ? n : '';
  el.style.display = n > 0 ? 'inline-block' : 'none';
}

function getSortedThemes() {
  if (!APP.data) return [];
  const themes = APP.data.themes.map(t => ({
    ...t,
    _volSum: t.stocks.reduce((s, st) => s + (st.volume_mn || 0), 0)
  }));

  const abs = t => Math.abs(t.weighted_avg_pct);
  if (APP.sort === 'weighted_avg_pct') {
    themes.sort((a, b) => abs(b) - abs(a));
  } else if (APP.sort === 'volume') {
    themes.sort((a, b) => b._volSum - a._volSum);
  } else {
    themes.sort((a, b) => b.stock_count - a.stock_count);
  }
  return themes.slice(0, APP.count);
}

// ─────────────────────────────────────────────────────────────────────────────
// INDICES
// ─────────────────────────────────────────────────────────────────────────────
function renderIndices() {
  const { kospi, kosdaq, updated_at, market_overview } = APP.data;
  const set = (valId, chgId, info) => {
    const valEl = document.getElementById(valId);
    const chgEl = document.getElementById(chgId);
    if (!valEl || !chgEl) return;
    if (!info || info.value == null) {
      valEl.textContent = '—';
      chgEl.textContent = '—';
      chgEl.className = 'idx-chg flat';
      return;
    }
    valEl.textContent =
      info.value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    chgEl.textContent = fmtPct(info.change_pct);
    chgEl.className = 'idx-chg ' + (info.change_pct > 0 ? 'up' : info.change_pct < 0 ? 'dn' : 'flat');
  };
  set('kospi-val', 'kospi-chg', kospi);
  set('kosdaq-val', 'kosdaq-chg', kosdaq);
  const mo = market_overview || {};
  set('sp500-val',  'sp500-chg',  mo.sp500);
  set('nasdaq-val', 'nasdaq-chg', mo.nasdaq);
  document.getElementById('updated-at').textContent = '기준: ' + updated_at;
}

// ─────────────────────────────────────────────────────────────────────────────
// TREEMAP
// ─────────────────────────────────────────────────────────────────────────────
function renderTreemap() {
  document.getElementById('tm-loading').style.display = 'none';
  const svg = document.getElementById('treemap-svg');
  svg.style.display = 'block';
  d3.select(svg).selectAll('*').remove();

  const themes = getSortedThemes();
  const W = 1192, H = 560;
  svg.setAttribute('width', W);
  svg.setAttribute('height', H);
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);

  const root = d3.hierarchy({ children: themes })
    .sum(d => Math.max(Math.abs(d.weighted_avg_pct), 0.1))
    .sort((a, b) => b.value - a.value);
  d3.treemap().size([W, H]).padding(3).paddingInner(3).round(true)(root);

  const cells = d3.select(svg).selectAll('g.t-cell')
    .data(root.leaves())
    .enter()
    .append('g')
    .attr('class', 't-cell')
    .attr('transform', d => `translate(${d.x0},${d.y0})`)
    .on('click',      (e, d) => showDetail(d.data))
    .on('mousemove',  (e, d) => showTooltip(e, d.data))
    .on('mouseleave', hideTooltip);

  // background rect
  cells.append('rect')
    .attr('width',  d => Math.max(0, d.x1 - d.x0))
    .attr('height', d => Math.max(0, d.y1 - d.y0))
    .attr('rx', 6).attr('ry', 6)
    .attr('fill', d => pctBgColor(d.data.weighted_avg_pct));

  // clip path + text
  cells.each(function(d) {
    const w = d.x1 - d.x0, h = d.y1 - d.y0;
    if (w < 32 || h < 24) return;

    const cid = `c${d.data.id}`;
    const g = d3.select(this);
    g.append('clipPath').attr('id', cid)
      .append('rect').attr('width', w).attr('height', h).attr('rx', 6);

    const tg = g.append('g').attr('clip-path', `url(#${cid})`);
    const nameSize = w < 90 ? 10 : w < 150 ? 11 : 13;
    const pctSize  = nameSize + 1;
    const fill     = cellTextColor(d.data.weighted_avg_pct);
    const pct      = fmtPct(d.data.weighted_avg_pct);
    const name     = truncText(d.data.name, w, nameSize);

    if (h >= 52) {
      const cy = h / 2;
      tg.append('text')
        .attr('x', w / 2).attr('y', cy - pctSize * 0.7)
        .attr('text-anchor', 'middle').attr('dominant-baseline', 'middle')
        .attr('font-size', nameSize).attr('font-weight', '600').attr('fill', fill)
        .text(name);
      tg.append('text')
        .attr('x', w / 2).attr('y', cy + pctSize * 1.1)
        .attr('text-anchor', 'middle').attr('dominant-baseline', 'middle')
        .attr('font-size', pctSize).attr('fill', 'rgba(255,255,255,0.80)')
        .text(pct);
    } else {
      const line = h > 36
        ? name + '  ' + pct
        : (w > 80 ? pct : '');
      if (!line) return;
      tg.append('text')
        .attr('x', 7).attr('y', h / 2)
        .attr('dominant-baseline', 'middle')
        .attr('font-size', nameSize).attr('font-weight', '500').attr('fill', fill)
        .text(line);
    }
  });
}

function truncText(text, maxW, fs) {
  const cw = fs * 0.64;
  const max = Math.floor((maxW - 14) / cw);
  return text.length <= max ? text : text.slice(0, Math.max(2, max - 1)) + '…';
}

// ─────────────────────────────────────────────────────────────────────────────
// SORT HELPERS
// ─────────────────────────────────────────────────────────────────────────────
function sortStocks(stocks) {
  const s = [...stocks];
  if (APP.stockSort === 'volume_mn') return s.sort((a, b) => b.volume_mn  - a.volume_mn);
  if (APP.stockSort === 'name')      return s.sort((a, b) => a.name.localeCompare(b.name, 'ko'));
  return s.sort((a, b) => b.change_pct - a.change_pct);
}

function rankBadge(rc) {
  if (!rc) return '<span class="rank-flat">−</span>';
  if (rc > 0) return `<span class="rank-up">▲${rc}</span>`;
  return `<span class="rank-dn">▼${Math.abs(rc)}</span>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// LIST
// ─────────────────────────────────────────────────────────────────────────────
function renderList() {
  const themes = getSortedThemes();
  const tbody = document.getElementById('list-tbody');
  tbody.innerHTML = '';

  themes.forEach((t, i) => {
    const pct = t.weighted_avg_pct;
    const vol = t._volSum || t.stocks.reduce((s, st) => s + (st.volume_mn || 0), 0);
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><span class="rank">${i + 1}</span>${rankBadge(t.rank_change)}</td>
      <td><span class="theme-name">${t.name}</span></td>
      <td class="r">
        <span class="badge" style="background:${pctBgColor(pct)};color:#fff">${fmtPct(pct)}</span>
      </td>
      <td class="r" style="color:var(--text-sub)">${t.active_count} / ${t.stock_count}</td>
      <td class="r" style="color:var(--text-sub)">${fmtVol(vol)}</td>
    `;
    tr.addEventListener('click', () => showDetail(t));
    tbody.appendChild(tr);
  });
}

// ─────────────────────────────────────────────────────────────────────────────
function renderStockGrid(theme) {
  const stocks = sortStocks(theme.stocks);
  const grid   = document.getElementById('stock-grid');
  grid.innerHTML = '';

  console.log('[renderStockGrid] theme=', theme.name, 'id=', theme.id,
              'stocks=', stocks.length,
              'codes=', stocks.map(s => s.code));

  stocks.forEach((s, i) => {
    const card = document.createElement('div');
    card.className = 's-card';
    // ── 단일 진실 원천: dataset.code / dataset.name ──
    // 클릭 이벤트가 이 속성만 읽도록 해 index/closure 의존을 완전히 제거.
    card.dataset.code  = s.code;
    card.dataset.name  = s.name;
    card.dataset.index = String(i);                 // 디버그 전용 (매칭에는 사용 X)

    const pct = s.change_pct;
    const safeName = _escHtml(s.name);
    const safeCode = _escHtml(s.code);
    const fav = isInWatchlist(s.code);
    card.innerHTML = `
      <div class="s-info">
        <div class="s-name" title="${safeName}">${safeName}</div>
        <div class="s-code">${safeCode}</div>
      </div>
      <div class="s-spark" id="spark-${theme.id}-${safeCode}"></div>
      <div class="s-right">
        <div class="s-pct" style="color:${pctTextColor(pct)}">${fmtPct(pct)}</div>
        <div class="s-vol">${(s.volume_mn || 0).toLocaleString()}M</div>
      </div>
      <button class="watchlist-star ${fav ? 'star-active' : ''}"
              title="관심종목 토글">${fav ? '★' : '☆'}</button>
    `;
    grid.appendChild(card);
  });
  requestAnimationFrame(() => {
    stocks.forEach(s => {
      const el = document.getElementById(`spark-${theme.id}-${s.code}`);
      if (el) createSparkline(el, s.sparkline, s.change_pct);
    });
  });
}

// Event delegation — 한 번만 바인딩되므로 재렌더 중복 리스너/메모리 누수 없음.
// 클릭 시 읽는 것은 card.dataset.code 하나뿐 (index/closure 의존 0).
document.getElementById('stock-grid').addEventListener('click', (e) => {
  const card = e.target.closest('.s-card');
  if (!card) return;

  // Phase 12-2: 카드 내부 ☆ 버튼 클릭은 차트 패널 열지 말고 watchlist 토글만
  const star = e.target.closest('.watchlist-star');
  if (star) {
    e.stopPropagation();
    const added = toggleWatchlist(card.dataset.code, card.dataset.name);
    star.textContent = added ? '★' : '☆';
    star.classList.toggle('star-active', added);
    return;
  }

  const code      = card.dataset.code;
  const name      = card.dataset.name;
  const debugIdx  = card.dataset.index;

  // 🔎 sanity check: card 안에 실제로 표시된 code 문자열과 dataset.code 가 일치해야 한다.
  //   (렌더 드리프트/부분 업데이트가 있었다면 여기서 mismatch 가 드러난다.)
  const shownCode = card.querySelector('.s-code')?.textContent?.trim();
  const shownName = card.querySelector('.s-name')?.textContent?.trim();

  console.log('[stock-card click]', {
    dataset_code: code,
    dataset_name: name,
    dataset_index: debugIdx,
    shown_code:   shownCode,
    shown_name:   shownName,
    card_element: card,
  });

  if (!code) {
    console.error('[stock-card click] ❌ dataset.code 누락', card);
    return;
  }
  if (shownCode && shownCode !== code) {
    console.error('[stock-card click] ❌ 카드 DOM 드리프트 감지',
                  'dataset.code=', code, 'shown=', shownCode,
                  '→ shown 쪽을 신뢰');
    openChartPanel(shownCode, shownName || code);
    return;
  }

  openChartPanel(code, name);
});

function showDetail(theme) {
  APP.selectedTheme = theme;

  document.getElementById('dp-name').textContent = theme.name;
  document.getElementById('dp-meta').textContent =
    `활성 ${theme.active_count}종목 / 전체 ${theme.stock_count}종목`;

  const badge = document.getElementById('dp-badge');
  badge.textContent = fmtPct(theme.weighted_avg_pct);
  badge.style.background = pctBgColor(theme.weighted_avg_pct);
  badge.style.color = '#fff';
  badge.style.padding = '3px 10px';
  badge.style.borderRadius = '7px';

  renderStockGrid(theme);
  document.getElementById('detail-panel').classList.add('visible');
  requestAnimationFrame(() => {
    document.getElementById('detail-panel').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  });
}

function hideDetail() {
  APP.selectedTheme = null;
  document.getElementById('detail-panel').classList.remove('visible');
}

// ─────────────────────────────────────────────────────────────────────────────
// 52주 신고가 섹터
// ─────────────────────────────────────────────────────────────────────────────
function renderNewHighSectors() {
  const sectors = APP.data && APP.data.new_high_sectors;
  const section = document.getElementById('newhigh-section');
  if (!sectors || !sectors.length) {
    section.style.display = 'none';
    return;
  }

  section.style.display = '';
  document.getElementById('nh-sub').textContent = `${sectors.length}개 섹터`;

  const container = document.getElementById('nh-cards');
  container.innerHTML = '';

  sectors.forEach(sector => {
    const card = document.createElement('div');
    card.className = 'nh-card';

    const stockRows = sector.stocks.map(s => {
      const chgColor = s.change_pct > 0 ? '#FF3333' : s.change_pct < 0 ? '#33AA33' : '#AEAEB2';
      return `
        <div class="nh-stock-row">
          <span>
            <span class="nh-stock-name">${s.name}</span>
            <span class="nh-stock-date">${s.high_date}</span>
          </span>
          <span class="nh-stock-right">
            <span class="nh-stock-price">${s.high_price.toLocaleString()}</span>
            <span class="nh-stock-chg" style="color:${chgColor}">${fmtPct(s.change_pct)}</span>
          </span>
        </div>`;
    }).join('');

    card.innerHTML = `
      <div class="nh-card-head">
        <span class="nh-card-name">${sector.name}</span>
        <span class="nh-card-count">${sector.new_high_count} / ${sector.total_count}종목</span>
      </div>
      <div class="nh-stocks">${stockRows}</div>`;

    card.querySelector('.nh-card-head').addEventListener('click', () => {
      card.querySelector('.nh-stocks').classList.toggle('open');
    });

    container.appendChild(card);
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// SETTINGS MODAL (Phase 3)
// ─────────────────────────────────────────────────────────────────────────────
let MODAL_THEMES = [];

function openSettingsModal() {
  fetch('/api/themes')
    .then(r => r.json())
    .then(themes => {
      MODAL_THEMES = JSON.parse(JSON.stringify(themes));
      renderModalList();
      document.getElementById('settings-overlay').classList.add('open');
    })
    .catch(() => alert('테마 목록을 불러올 수 없습니다.\n서버가 실행 중인지 확인하세요.'));
}

function closeSettingsModal() {
  document.getElementById('settings-overlay').classList.remove('open');
}

function renderModalList() {
  const container = document.getElementById('mt-list');
  container.innerHTML = '';
  MODAL_THEMES.forEach((theme, ti) => container.appendChild(buildThemeItem(theme, ti)));
}

function buildThemeItem(theme, ti) {
  const wrap = document.createElement('div');
  wrap.className = 'mt-item';
  wrap.dataset.ti = ti;

  const stocksHTML = theme.stocks.map(s => {
    const code = typeof s === 'object' ? s.code : s;
    const name = typeof s === 'object' ? (s.name || code) : s;
    return `<div class="mt-stock-row">
      <span class="mt-stock-code">${code}</span>
      <span class="mt-stock-name">${name}</span>
      <button class="mt-stock-del" data-ti="${ti}" data-code="${code}">✕</button>
    </div>`;
  }).join('');

  wrap.innerHTML = `
    <div class="mt-head" data-ti="${ti}">
      <span class="mt-arrow">▶</span>
      <span class="mt-name">${theme.name}</span>
      <span class="mt-count">${theme.stocks.length}종목</span>
      <button class="mt-del" data-ti="${ti}">테마 삭제</button>
    </div>
    <div class="mt-body" style="display:none;">
      <div class="mt-stocks">${stocksHTML}</div>
      <div class="mt-search-wrap">
        <input class="mt-search-input" type="text"
               placeholder="종목코드(6자리) 또는 종목명 (2자 이상)" data-ti="${ti}">
        <button class="mt-search-add" data-ti="${ti}">추가</button>
        <div class="mt-search-dropdown" data-ti="${ti}"></div>
      </div>
    </div>`;
  return wrap;
}

function _reExpandTheme(ti) {
  const items = document.querySelectorAll('.mt-item');
  if (!items[ti]) return;
  const body  = items[ti].querySelector('.mt-body');
  const arrow = items[ti].querySelector('.mt-arrow');
  body.style.display  = '';
  arrow.textContent   = '▼';
}

// ── Event delegation for mt-list ──
document.getElementById('mt-list').addEventListener('click', e => {
  // Delete theme button (inside mt-head, must check before head)
  const delThemeBtn = e.target.closest('.mt-del');
  if (delThemeBtn) {
    e.stopPropagation();
    const ti = parseInt(delThemeBtn.dataset.ti);
    if (confirm(`"${MODAL_THEMES[ti].name}" 테마를 삭제하시겠습니까?`)) {
      MODAL_THEMES.splice(ti, 1);
      renderModalList();
    }
    return;
  }
  // Toggle accordion head
  const head = e.target.closest('.mt-head');
  if (head) {
    const ti   = parseInt(head.dataset.ti);
    const item = e.target.closest('.mt-item');
    const body  = item.querySelector('.mt-body');
    const arrow = item.querySelector('.mt-arrow');
    const isOpen = body.style.display !== 'none';
    body.style.display  = isOpen ? 'none' : '';
    arrow.textContent   = isOpen ? '▶' : '▼';
    return;
  }
  // Delete stock
  const delStock = e.target.closest('.mt-stock-del');
  if (delStock) {
    const ti   = parseInt(delStock.dataset.ti);
    const code = delStock.dataset.code;
    MODAL_THEMES[ti].stocks = MODAL_THEMES[ti].stocks.filter(s =>
      (typeof s === 'object' ? s.code : s) !== code
    );
    renderModalList();
    _reExpandTheme(ti);
    return;
  }
  // Add button
  const addBtn = e.target.closest('.mt-search-add');
  if (addBtn) {
    const ti = parseInt(addBtn.dataset.ti);
    _modalAddFromInput(ti);
    return;
  }
  // Dropdown result click
  const ddItem = e.target.closest('.mt-search-result');
  if (ddItem) {
    const ti   = parseInt(ddItem.dataset.ti);
    const code = ddItem.dataset.code;
    const name = ddItem.dataset.name;
    _modalAddStock(ti, code, name);
    const wrap = e.target.closest('.mt-search-wrap');
    if (wrap) {
      const inp = wrap.querySelector('.mt-search-input');
      const dd  = wrap.querySelector('.mt-search-dropdown');
      if (inp) inp.value = '';
      if (dd)  { dd.innerHTML = ''; dd.style.display = 'none'; }
    }
  }
});

// ── Debounced search input ──
document.getElementById('mt-list').addEventListener('input', e => {
  const inp = e.target.closest('.mt-search-input');
  if (!inp) return;
  const ti = parseInt(inp.dataset.ti);
  const q  = inp.value.trim();
  const dd = inp.closest('.mt-search-wrap').querySelector('.mt-search-dropdown');

  clearTimeout(inp._timer);
  if (q.length < 2) { dd.innerHTML = ''; dd.style.display = 'none'; return; }

  inp._timer = setTimeout(async () => {
    try {
      const r   = await fetch(`${APP.market === "us" ? "/api/us/search" : "/api/stock_search"}?q=${encodeURIComponent(q)}`);
      const res = await r.json();
      if (!res.length) { dd.innerHTML = ''; dd.style.display = 'none'; return; }
      dd.innerHTML = res.map(item =>
        `<div class="mt-search-result"
              data-ti="${ti}" data-code="${item.code}" data-name="${item.name}">
          <span class="mt-search-result-code">${item.code}</span>${item.name}
        </div>`
      ).join('');
      dd.style.display = 'block';
    } catch { dd.innerHTML = ''; dd.style.display = 'none'; }
  }, 300);
});

// ── Enter key selects first result ──
document.getElementById('mt-list').addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  const inp = e.target.closest('.mt-search-input');
  if (!inp) return;
  const dd    = inp.closest('.mt-search-wrap').querySelector('.mt-search-dropdown');
  const first = dd.querySelector('.mt-search-result');
  if (first) {
    first.click();
  } else {
    _modalAddFromInput(parseInt(inp.dataset.ti));
  }
});

function _modalAddFromInput(ti) {
  const items = document.querySelectorAll('.mt-item');
  const inp   = items[ti]?.querySelector('.mt-search-input');
  if (!inp) return;
  const q = inp.value.trim();
  if (!q) return;
  if (/^\d{6}$/.test(q)) {
    _modalAddStock(ti, q, q);
    inp.value = '';
    const dd = inp.closest('.mt-search-wrap')?.querySelector('.mt-search-dropdown');
    if (dd) { dd.innerHTML = ''; dd.style.display = 'none'; }
  }
}

function _modalAddStock(ti, code, name) {
  const theme  = MODAL_THEMES[ti];
  const exists = theme.stocks.some(s => (typeof s === 'object' ? s.code : s) === code);
  if (exists) return;
  theme.stocks.push({ code, name });
  renderModalList();
  _reExpandTheme(ti);
}

// ── Modal buttons ──
document.getElementById('sm-tg-test').addEventListener('click', async () => {
  const result = document.getElementById('sm-tg-result');
  result.textContent = '전송 중…';
  result.style.color = 'var(--text-muted)';
  try {
    const r = await fetch('/api/telegram/test', { method: 'POST' });
    const d = await r.json();
    if (d.ok) {
      result.textContent = '✅ 전송 성공 — 텔레그램을 확인하세요';
      result.style.color = '#33AA33';
    } else {
      result.textContent = '❌ 전송 실패 (토큰 미설정)';
      result.style.color = '#FF3333';
    }
  } catch (err) {
    result.textContent = '❌ 에러: ' + err.message;
    result.style.color = '#FF3333';
  }
});
document.getElementById('settings-btn').addEventListener('click', openSettingsModal);
document.getElementById('sm-close').addEventListener('click', closeSettingsModal);
document.getElementById('sm-cancel').addEventListener('click', closeSettingsModal);
document.getElementById('settings-overlay').addEventListener('click', e => {
  if (e.target.id === 'settings-overlay') closeSettingsModal();
});

document.getElementById('sm-add-theme').addEventListener('click', () => {
  const name = prompt('새 테마 이름을 입력하세요:');
  if (!name?.trim()) return;
  const maxId = MODAL_THEMES.reduce((m, t) => Math.max(m, t.id || 0), 0);
  MODAL_THEMES.push({ id: maxId + 1, name: name.trim(), stocks: [] });
  renderModalList();
  const items = document.querySelectorAll('.mt-item');
  const last  = items[items.length - 1];
  if (last) {
    last.querySelector('.mt-body').style.display  = '';
    last.querySelector('.mt-arrow').textContent   = '▼';
    last.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
});

document.getElementById('sm-save').addEventListener('click', async () => {
  try {
    const r = await fetch('/api/themes', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(MODAL_THEMES),
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    closeSettingsModal();
    // 저장 후 데이터 재수집 트리거
    try { await fetch('/api/refresh', { method: 'POST' }); } catch { /* ignore */ }
  } catch (err) {
    alert('저장 실패: ' + err.message);
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// TOOLTIP
// ─────────────────────────────────────────────────────────────────────────────
function showTooltip(e, t) {
  const tip = document.getElementById('tooltip');
  document.getElementById('tt-name').textContent = t.name;
  document.getElementById('tt-pct').textContent  = fmtPct(t.weighted_avg_pct);
  document.getElementById('tt-cnt').textContent  = `${t.active_count} / ${t.stock_count}`;
  const vol = t._volSum ?? t.stocks.reduce((s, st) => s + (st.volume_mn || 0), 0);
  document.getElementById('tt-vol').textContent  = fmtVol(vol);
  tip.style.display = 'block';
  tip.style.left = (e.clientX + 14) + 'px';
  tip.style.top  = (e.clientY - 8)  + 'px';
}

function hideTooltip() {
  document.getElementById('tooltip').style.display = 'none';
}

// ─────────────────────────────────────────────────────────────────────────────
// VIEW SWITCH
// ─────────────────────────────────────────────────────────────────────────────
function renderCurrentView() {
  // 테마맵 페이지 내부 Map/List 전환 전용 (시황·비교는 사이드바 라우터가 담당)
  const mapEl  = document.getElementById('map-view');
  const listEl = document.getElementById('list-view');
  if (APP.tab === 'map') {
    mapEl.style.display  = '';
    listEl.style.display = 'none';
    renderTreemap();
  } else {
    mapEl.style.display  = 'none';
    listEl.style.display = '';
    renderList();
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// EVENTS
// ─────────────────────────────────────────────────────────────────────────────
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    APP.tab = btn.dataset.tab;
    renderCurrentView();
  });
});

document.getElementById('sel-sort').addEventListener('change', e => {
  APP.sort = e.target.value;
  renderCurrentView();
  if (APP.selectedTheme) showDetail(APP.selectedTheme);
});

document.getElementById('sel-count').addEventListener('change', e => {
  APP.count = parseInt(e.target.value);
  renderCurrentView();
});

document.getElementById('dp-close').addEventListener('click', hideDetail);

document.getElementById('sel-stock-sort').addEventListener('change', e => {
  APP.stockSort = e.target.value;
  if (APP.selectedTheme) renderStockGrid(APP.selectedTheme);
});

// ─────────────────────────────────────────────────────────────────────────────
// INIT
// ─────────────────────────────────────────────────────────────────────────────
async function init() {
  console.log('[init] start');
  try {
    const res = await fetch('./data.json', { cache: 'no-store' });
    console.log('[init] fetch status:', res.status, res.headers.get('content-type'));
    if (!res.ok) throw new Error('HTTP ' + res.status);
    APP.data = await res.json();
    console.log('[init] themes:', APP.data.themes?.length, 'kospi:', APP.data.kospi);
  } catch (err) {
    console.warn('[init] data.json 로드 실패 — 더미 데이터 사용', err);
    APP.data = DUMMY;
  }
  try {
    renderIndices();
    renderCurrentView();
    renderNewHighSectors();
    updateWatchlistBadge();        // Phase 12-2
    const first = getSortedThemes()[0];
    if (first) showDetail(first);
    console.log('[init] render complete');
  } catch (err) {
    console.error('[init] render error:', err);
  }
}

init();

// ─────────────────────────────────────────────────────────────────────────────
// SERVER STATUS POLLING  (Flask 서버로 실행 중일 때만 동작)
// ─────────────────────────────────────────────────────────────────────────────
(function setupServerStatus() {
  const btn          = document.getElementById('refresh-btn');
  const badge        = document.getElementById('fetch-badge');
  const selInterval  = document.getElementById('sel-interval');
  const updatedEl    = document.getElementById('updated-at');
  const liveDot      = document.getElementById('live-dot');
  const liveLabel    = document.getElementById('live-label');
  let prevState      = null;
  let statusTimer    = null;
  let autoTimer      = null;
  let isFlaskEnv     = true;  // set to false if /api/status 404

  // ── LIVE 인디케이터 (클라이언트 시각 기반) ──
  function updateLive() {
    const now = new Date();
    const d   = now.getDay();        // 0=일, 6=토
    const t   = now.getHours() * 100 + now.getMinutes();
    const open = d >= 1 && d <= 5 && t >= 900 && t <= 1530;
    liveDot.className  = 'live-dot ' + (open ? 'live' : 'offline');
    liveLabel.textContent = open ? 'LIVE' : '장외';
  }
  updateLive();
  setInterval(updateLive, 30_000);

  // ── 갱신 완료 시 타임스탬프 깜빡임 ──
  function flashTimestamp() {
    updatedEl.classList.remove('flash');
    void updatedEl.offsetWidth;           // reflow to restart animation
    updatedEl.classList.add('flash');
  }

  // ── 수집 완료 후 data.json 재로드 ──
  async function reloadData() {
    try {
      const dr = await fetch('./data.json?' + Date.now());
      if (!dr.ok) return;
      APP.data = await dr.json();
      renderIndices();
      renderCurrentView();
      renderNewHighSectors();
      const first = getSortedThemes()[0];
      if (first && !APP.selectedTheme) showDetail(first);
      flashTimestamp();
    } catch { /* ignore */ }
  }

  // ── 배지 상태 ──
  function setBadge(state) {
    if (state === 'running') {
      badge.className = 'fetch-badge running';
      badge.textContent = '수집 중…';
      badge.style.display = '';
      btn.disabled = true;
      btn.classList.add('spinning');
    } else if (state === 'error') {
      badge.className = 'fetch-badge error';
      badge.textContent = '수집 오류';
      badge.style.display = '';
      btn.disabled = false;
      btn.classList.remove('spinning');
    } else {
      btn.disabled = false;
      btn.classList.remove('spinning');
      if (prevState === 'running') {
        badge.className = 'fetch-badge done';
        badge.textContent = '수집 완료';
        badge.style.display = '';
        setTimeout(() => { badge.style.display = 'none'; }, 3500);
      }
    }
  }

  // ── /api/status 폴링 ──
  async function poll() {
    if (!isFlaskEnv) return;
    try {
      const res = await fetch('/api/status', { signal: AbortSignal.timeout(3000) });
      if (!res.ok) return;
      const s = await res.json();

      setBadge(s.state);

      // 갱신 주기 드롭다운을 서버 값과 동기화
      if (s.interval_minutes && selInterval) {
        selInterval.value = String(s.interval_minutes);
      }

      // 수집이 방금 완료됐으면 data.json 재로드
      if (prevState === 'running' && s.state === 'idle' && s.data_exists) {
        await reloadData();
      }

      prevState = s.state;
      statusTimer = setTimeout(poll, s.state === 'running' ? 2000 : 30000);
    } catch {
      isFlaskEnv = false;
      btn.style.display = 'none';
      if (selInterval) selInterval.closest('.ctrl-left, .ctrl-right') && (selInterval.style.display = 'none');
    }
  }

  // ── 자동 polling (data.json 직접 비교) ──
  function startAutoRefresh(minutes) {
    clearTimeout(autoTimer);
    if (!minutes || !isFlaskEnv) return;
    autoTimer = setTimeout(async () => {
      if (!document.hidden) {
        try {
          const r = await fetch('/api/refresh', { method: 'POST' });
          if (r.ok) { clearTimeout(statusTimer); prevState = null; poll(); }
        } catch {}
      }
      startAutoRefresh(minutes);
    }, minutes * 60_000);
  }

  // ── 갱신주기 드롭다운 ──
  if (selInterval) {
    selInterval.addEventListener('change', async () => {
      const minutes = parseInt(selInterval.value);
      clearTimeout(autoTimer);
      if (isFlaskEnv && minutes > 0) {
        try { await fetch(`/api/interval/${minutes}`, { method: 'POST' }); } catch {}
      }
      startAutoRefresh(minutes);
    });
  }

  // ── 즉시갱신 버튼 ──
  btn.addEventListener('click', async () => {
    try {
      await fetch('/api/refresh', { method: 'POST' });
      clearTimeout(statusTimer);
      prevState = null;
      poll();
    } catch {}
  });

  // ── 탭 가시성 변경 시 polling 재개 ──
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && isFlaskEnv) {
      clearTimeout(statusTimer);
      poll();
      // Phase 11: 차트 패널이 열려있으면 즉시 한 번 가격 갱신 (5분 경과 기다리지 않음)
      if (CHART_STATE && CHART_STATE.code) {
        _fetchAndApplyLivePrice(CHART_STATE.code);
      }
    }
  });

  // 초기 폴링 시작 + 자동 갱신 초기화
  poll();
  startAutoRefresh(parseInt(selInterval?.value ?? '5'));
})();


async function _loadAfterHours(code) {
  try {
    const r = await fetch(`/api/after_hours/${code}`);
    if (!r.ok) return;
    const d = await r.json();
    const el = document.getElementById('chart-afterhours');
    if (!el || !d.after_hours) return;
    const ah = d.after_hours;
    const sign = (ah.change_pct || 0) >= 0 ? '+' : '';
    const status = ah.status === 'OPEN' ? '🔴' : '';
    el.innerHTML = `${status} 시간외 ₩${Number(ah.price).toLocaleString()} (${sign}${ah.change_pct}%)`;
    el.style.display = 'inline-block';
  } catch(e) { /* ignore */ }
}

async function _loadUsExtended(symbol) {
  try {
    const r = await fetch(`/api/us/extended/${symbol}`);
    if (!r.ok) return;
    const d = await r.json();
    const el = document.getElementById('chart-afterhours');
    if (!el || !d.extended_price) return;
    const pct = d.extended_change_pct != null ? (d.extended_change_pct).toFixed(2) : '—';
    const sign = (d.extended_change_pct || 0) >= 0 ? '+' : '';
    const live = d.market_state === 'PRE' || d.market_state === 'POST' ? '🔴 ' : '';
    el.innerHTML = `${live}${d.extended_label} $${Number(d.extended_price).toFixed(2)} (${sign}${pct}%)`;
    el.style.display = 'inline-block';
  } catch(e) { /* ignore */ }
}
