// ===== static/js/utils.js — 공용 포매팅/색상/배지/스파크라인 =====
'use strict';

// ─────────────────────────────────────────────────────────────────────────────
// COLOR HELPERS
// ─────────────────────────────────────────────────────────────────────────────
function pctBgColor(v) {
  if (v >= 5)  return '#FF0000';
  if (v >= 3)  return '#CC3333';
  if (v >= 1)  return '#DD6666';
  if (v > 0)   return '#EE9999';
  if (v === 0) return '#333333';
  if (v >= -1) return '#99DD99';
  if (v >= -3) return '#66CC66';
  if (v >= -5) return '#33AA33';
  return '#008800';
}

function cellTextColor(v) {
  // 아주 연한 배경(0~±1%)은 흰 글씨도 읽기 어려울 수 있으므로 밝게
  return 'rgba(255,255,255,0.92)';
}

function pctTextColor(v) {
  if (v > 0) return '#FF3333';
  if (v < 0) return '#33AA33';
  return '#AEAEB2';
}

function fmtPct(v, alwaysSign = true) {
  const s = alwaysSign && v > 0 ? '+' : '';
  return s + v.toFixed(2) + '%';
}

function fmtVol(v) {
  if (v >= 1000000) return (v / 1000000).toFixed(1) + 'T';
  if (v >= 1000)    return (v / 1000).toFixed(0) + 'B';
  return v.toLocaleString() + 'M';
}

// ─────────────────────────────────────────────────────────────────────────────
// SVG SPARKLINE
// ─────────────────────────────────────────────────────────────────────────────
function createSparkline(container, data, changePct, width = 88, height = 36) {
  container.innerHTML = '';
  if (!data || data.length < 2) {
    const blank = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    blank.setAttribute('width', width); blank.setAttribute('height', height);
    container.appendChild(blank);
    return;
  }
  const max = Math.max(...data);
  const min = Math.min(...data);
  const range = max - min || 1;
  const pad = 2;
  const W = width, H = height - pad * 2;

  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * W;
    const y = pad + H - ((v - min) / range) * H;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');

  const color = changePct >= 0 ? '#FF3333' : '#33AA33';
  const fillColor = changePct >= 0 ? 'rgba(255,51,51,0.12)' : 'rgba(51,170,51,0.12)';

  // area fill
  const firstX = 0, lastX = W;
  const firstY = pad + H - ((data[0] - min) / range) * H;
  const lastY  = pad + H - ((data[data.length - 1] - min) / range) * H;
  const areaPoints = `${firstX},${pad + H} ${pts} ${lastX},${pad + H}`;

  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('width', width);
  svg.setAttribute('height', height);
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.innerHTML = `
    <polygon points="${areaPoints}" fill="${fillColor}" stroke="none"/>
    <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>
  `;
  container.appendChild(svg);
}


// DETAIL
// ─────────────────────────────────────────────────────────────────────────────
// 종목 이름의 HTML 안전 이스케이프 (innerHTML 템플릿 인젝션 방지)
function _escHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// 전역 시장 배지 헬퍼
function _marketBadge(market) {
  const b = {
    'KOSPI': '<span class="mkt-badge mkt-kp">KP</span>',
    'KOSDAQ': '<span class="mkt-badge mkt-kq">KQ</span>',
    'NASDAQ': '<span class="mkt-badge mkt-us">US</span>',
    'NYSE': '<span class="mkt-badge mkt-us">US</span>',
    'AMEX': '<span class="mkt-badge mkt-us">US</span>',
    'US': '<span class="mkt-badge mkt-us">US</span>',
    'kr': '<span class="mkt-badge mkt-kr">KR</span>',
    'us': '<span class="mkt-badge mkt-us">US</span>',
  };
  return b[market] || '';
}

function _marketBadgeFromItem(item) {
  if (!item) return '';
  const mt = item.market_type || item.market || '';
  if (mt === 'KOSPI' || mt === 'KOSDAQ') return _marketBadge(mt);
  if (mt === 'NASDAQ' || mt === 'NYSE' || mt === 'AMEX' || mt === 'US') return _marketBadge('US');
  if (mt === 'us') return _marketBadge('US');
  // 코드 패턴 기반 추정
  const code = item.code || item.symbol || '';
  if (/^\d{6}$/.test(code)) return _marketBadge('kr');
  if (/^[A-Z][A-Z0-9.\-]{0,6}$/.test(code)) return _marketBadge('US');
  return '';
}

