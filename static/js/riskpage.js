/* riskpage.js — 🧭 위험지표 페이지 (ADR 등락비율 · 이격도 · VIX · 장단기 금리차 · 공포탐욕)
 *
 * 데이터: /api/market/breadth · /api/market/risk · /api/fear_greed
 * 계산은 서버(market_risk.py)가 한다. 여기는 그리기만 한다.
 */

const _RISK_STATE = { period: '1Y', ma: 50 };

async function renderRiskPage() {
  const root = document.getElementById('risk-view');
  if (!root) return;
  root.innerHTML = `<div class="pg-wrap">
    <div class="pg-title">🧭 위험지표</div>
    <div class="pg-sub">시장의 체력(등락비율)·과열(이격도)·변동성·경기 신호를 한 화면에서</div>
    <div class="rk-toolbar">
      <div class="rk-btns" id="rk-period">${['6M', '1Y', '3Y'].map(p =>
        `<button class="rk-btn${p === _RISK_STATE.period ? ' active' : ''}" data-p="${p}">${p}</button>`).join('')}</div>
      <div class="rk-btns" id="rk-ma">${[25, 50].map(n =>
        `<button class="rk-btn${n === _RISK_STATE.ma ? ' active' : ''}" data-ma="${n}">이격도 ${n}일</button>`).join('')}</div>
    </div>
    <div id="rk-adr"><div class="bt-loading">⏳ 등락비율 불러오는 중…</div></div>
    <div id="rk-risk"><div class="bt-loading">⏳ 위험지표 불러오는 중…</div></div>
    <div class="rk-foot">단순 정보 제공 목적이며, 투자 판단의 근거가 아닙니다.</div>
  </div>`;
  root.querySelectorAll('#rk-period .rk-btn').forEach(b => b.addEventListener('click', () => {
    _RISK_STATE.period = b.dataset.p; renderRiskPage();
  }));
  root.querySelectorAll('#rk-ma .rk-btn').forEach(b => b.addEventListener('click', () => {
    _RISK_STATE.ma = parseInt(b.dataset.ma, 10); renderRiskPage();
  }));
  const q = `period=${encodeURIComponent(_RISK_STATE.period)}`;
  _rkLoad(`/api/market/breadth?${q}`, document.getElementById('rk-adr'), _rkRenderAdr);
  _rkLoad(`/api/market/risk?${q}&ma=${_RISK_STATE.ma}`, document.getElementById('rk-risk'),
          async (d, box) => {
            let fg = null;
            try { fg = await (await fetch('/api/fear_greed')).json(); } catch (e) { fg = null; }
            _rkRenderRisk(d, fg, box);
          });
}

async function _rkLoad(url, box, render) {
  try {
    const r = await fetch(url);
    const d = await r.json();
    if (!r.ok || d.error) {
      box.innerHTML = `<div class="bt-error">${_escHtml(d.error || ('HTTP ' + r.status))}</div>`;
      return;
    }
    await render(d, box);
  } catch (e) {
    box.innerHTML = `<div class="bt-error">로드 실패: ${_escHtml(e.message)}</div>`;
  }
}

function _rkFmt(v, digits) {
  return (v == null || isNaN(v)) ? '—' : Number(v).toFixed(digits == null ? 2 : digits);
}

function _rkChange(v, digits, unit) {
  if (v == null || isNaN(v)) return '';
  const col = v > 0 ? 'var(--color-up)' : v < 0 ? 'var(--color-down)' : 'var(--text-secondary)';
  return `<span style="color:${col}">${v > 0 ? '▲' : v < 0 ? '▼' : ''} ${Math.abs(v).toFixed(digits == null ? 2 : digits)}${unit || ''}</span>`;
}

const _RK_MARKET_KR = { KOSPI: '코스피', KOSDAQ: '코스닥' };

function _rkRenderAdr(d, box) {
  const mk = d.markets || {};
  let cards = '';
  let charts = '';
  for (const m of ['KOSPI', 'KOSDAQ']) {
    const x = mk[m] || {};
    const t = x.today;
    const adr = x.adr;
    const zone = adr == null ? '' : adr >= d.hot ? ' rk-hot' : adr <= d.cold ? ' rk-cold' : '';
    cards += `<div class="gm-card${zone}">
      <div class="gm-card-name">${_RK_MARKET_KR[m]} · ${t ? _escHtml(t.label || '') : '데이터 없음'}
        ${t ? `<span class="rk-date">${_escHtml(t.date)}</span>` : ''}</div>
      <div class="gm-card-value">${adr == null ? '—' : _rkFmt(adr) + '%'}</div>
      <div class="gm-card-change">${_rkChange(x.adr_change, 2, '')}</div>
      ${t ? `<div class="rk-counts"><span style="color:var(--color-up)">상승 ${t.up.toLocaleString()}</span>
        · <span style="color:var(--color-down)">하락 ${t.down.toLocaleString()}</span>
        · 보합 ${t.flat.toLocaleString()}</div>` : ''}
    </div>`;
    const note = x.approx_until
      ? `<div class="rk-note">~${_escHtml(x.approx_until)} 은 받아 둔 일봉(시총 800억 이상)으로 센 근사값 — 그 뒤는 전 종목 장 마감 집계</div>`
      : '';
    charts += `<div class="rk-chart-box">
      <div class="rk-chart-title">${_RK_MARKET_KR[m]} ADR (${d.window}일)</div>
      ${_rkLineChart(x.series || [], {
        refs: [{ v: d.hot, label: `과열 ${d.hot}` }, { v: 100, label: '100' }, { v: d.cold, label: `바닥 ${d.cold}` }],
        unit: '%',
      })}
      ${note}
    </div>`;
  }
  box.innerHTML = `<div class="gm-section">
    <div class="gm-section-title">ADR — 코스피·코스닥 등락비율</div>
    <div class="rk-desc">최근 ${d.window}거래일 상승 종목 수 합 ÷ 하락 종목 수 합 × 100.
      ${d.hot}% 이상 과열, ${d.cold}% 이하 바닥권으로 흔히 읽는다. 대상: 코스피·코스닥 보통주·우선주·리츠(ETF·ETN·스팩 제외).</div>
    <div class="gm-grid rk-grid2">${cards}</div>
    <div class="rk-charts">${charts}</div>
  </div>`;
}

function _rkRenderRisk(d, fg, box) {
  let html = '';
  // 이격도
  let cards = '', charts = '';
  for (const m of ['KOSPI', 'KOSDAQ']) {
    const x = (d.disparity || {})[m] || {};
    cards += `<div class="gm-card">
      <div class="gm-card-name">${_RK_MARKET_KR[m]} ${d.ma}일 이격도 <span class="rk-date">${_escHtml(x.date || '')}</span></div>
      <div class="gm-card-value">${_rkFmt(x.latest)}</div>
      <div class="gm-card-change">${_rkChange(x.change)}</div>
    </div>`;
    charts += `<div class="rk-chart-box">
      <div class="rk-chart-title">${_RK_MARKET_KR[m]} ${d.ma}일 이격도</div>
      ${_rkLineChart(x.series || [], { refs: [{ v: 100, label: '100' }] })}
    </div>`;
  }
  html += `<div class="gm-section">
    <div class="gm-section-title">지수 이격도</div>
    <div class="rk-desc">이격도 = 종가 ÷ ${d.ma}일 이동평균 × 100. 100 위면 평균보다 높게, 아래면 낮게 거래 중. 출처: 네이버 지수 일봉.</div>
    <div class="gm-grid rk-grid2">${cards}</div>
    <div class="rk-charts">${charts}</div>
  </div>`;

  // VIX · 금리차
  const vix = d.vix || {}, sp = d.t10y2y || {};
  html += `<div class="gm-section">
    <div class="gm-section-title">변동성 · 경기 신호</div>
    <div class="rk-desc">VIX(미국 S&amp;P500 변동성 지수)와 미국 10년−2년 국채 금리차. 금리차가 0 아래(역전)면 경기 둔화 신호로 흔히 읽는다.
      출처: FRED(종가, 1영업일 지연).</div>
    <div class="gm-grid rk-grid2">
      <div class="gm-card"><div class="gm-card-name">VIX <span class="rk-date">${_escHtml(vix.date || '')}</span></div>
        <div class="gm-card-value">${_rkFmt(vix.latest)}</div><div class="gm-card-change">${_rkChange(vix.change)}</div></div>
      <div class="gm-card${sp.latest != null && sp.latest < 0 ? ' gm-card-warn' : ''}"><div class="gm-card-name">미국 10년−2년 금리차 <span class="rk-date">${_escHtml(sp.date || '')}</span></div>
        <div class="gm-card-value">${_rkFmt(sp.latest)}%p</div><div class="gm-card-change">${_rkChange(sp.change, 2, '%p')}</div></div>
    </div>
    <div class="rk-charts">
      <div class="rk-chart-box"><div class="rk-chart-title">VIX</div>
        ${_rkLineChart(vix.series || [], { refs: [{ v: 20, label: '20' }, { v: 30, label: '30' }] })}</div>
      <div class="rk-chart-box"><div class="rk-chart-title">미국 10년−2년 금리차</div>
        ${_rkLineChart(sp.series || [], { refs: [{ v: 0, label: '0 (역전)' }], unit: '%p' })}</div>
    </div>
  </div>`;

  // 공포·탐욕 — 주식(CNN) 이 아니면 그렇다고 적는다
  if (fg && fg.score != null) {
    const crypto = String(fg.source || '').indexOf('cnn') !== 0;
    html += `<div class="gm-section">
      <div class="gm-section-title">공포·탐욕 지수</div>
      <div class="gm-grid"><div class="gm-card">
        <div class="gm-card-name">${crypto ? '코인 공포·탐욕 (CNN 주식 지수 대신)' : 'CNN 공포·탐욕'}</div>
        <div class="gm-card-value">${_escHtml(String(fg.score))}</div>
        <div class="gm-card-signal">${_escHtml(fg.rating_kr || fg.rating || '')}</div>
      </div></div>
      ${crypto ? '<div class="rk-note">CNN 주식 공포·탐욕을 받지 못해 코인 지수(alternative.me)를 보여 준다 — 주식 심리와 다를 수 있다.</div>' : ''}
    </div>`;
  }
  box.innerHTML = html;
}

/* 간단한 SVG 선 차트. points=[{date, value}], opts.refs=[{v,label}] 참고선. */
function _rkLineChart(points, opts) {
  opts = opts || {};
  const pts = (points || []).filter(p => p && p.value != null && !isNaN(p.value));
  if (pts.length < 2) return '<div class="rk-empty">아직 쌓인 값이 없다 — 다음 갱신 뒤에 보인다</div>';
  const W = 640, H = 200, L = 44, R = 8, T = 10, B = 22;
  const vals = pts.map(p => p.value);
  const refs = (opts.refs || []).map(r => r.v);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  // 참고선이 값 범위 가까이 있을 때만 축에 넣는다(멀리 있는 선이 그림을 납작하게 만들지 않게)
  const span0 = (hi - lo) || Math.abs(hi) || 1;
  for (const v of refs) if (v >= lo - span0 * 0.5 && v <= hi + span0 * 0.5) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  const pad = (hi - lo) * 0.06 || 1;
  lo -= pad; hi += pad;
  const x = i => L + (W - L - R) * (pts.length === 1 ? 0 : i / (pts.length - 1));
  const y = v => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
  const path = pts.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join('');
  let refSvg = '';
  for (const r of opts.refs || []) {
    if (r.v < lo || r.v > hi) continue;
    const yy = y(r.v).toFixed(1);
    refSvg += `<line x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}" class="rk-ref"/>
      <text x="${W - R - 2}" y="${(yy - 3)}" text-anchor="end" class="rk-ref-label">${_escHtml(r.label)}</text>`;
  }
  const last = pts[pts.length - 1];
  const id = 'rk' + Math.random().toString(36).slice(2, 8);
  const svg = `<svg viewBox="0 0 ${W} ${H}" class="rk-svg" id="${id}"
      data-pts='${JSON.stringify(pts.map(p => [p.date, p.value])).replace(/'/g, '&#39;')}'
      data-geo='${JSON.stringify({ W, H, L, R, T, B, lo, hi })}' data-unit="${_escHtml(opts.unit || '')}">
    <text x="${L - 6}" y="${y(hi - pad) + 4}" text-anchor="end" class="rk-axis">${(hi - pad).toFixed(1)}</text>
    <text x="${L - 6}" y="${y(lo + pad) + 4}" text-anchor="end" class="rk-axis">${(lo + pad).toFixed(1)}</text>
    <text x="${L}" y="${H - 6}" class="rk-axis">${_escHtml(pts[0].date)}</text>
    <text x="${W - R}" y="${H - 6}" text-anchor="end" class="rk-axis">${_escHtml(last.date)}</text>
    ${refSvg}
    <path d="${path}" class="rk-line"/>
    <line class="rk-cross" x1="0" x2="0" y1="${T}" y2="${H - B}" style="display:none"/>
    <text class="rk-tip" x="${L + 4}" y="${T + 12}"></text>
  </svg>`;
  setTimeout(() => _rkBindHover(id), 0);
  return svg;
}

function _rkBindHover(id) {
  const el = document.getElementById(id);
  if (!el) return;
  const pts = JSON.parse(el.dataset.pts || '[]');
  const g = JSON.parse(el.dataset.geo || '{}');
  const unit = el.dataset.unit || '';
  const cross = el.querySelector('.rk-cross'), tip = el.querySelector('.rk-tip');
  el.addEventListener('mousemove', ev => {
    const rect = el.getBoundingClientRect();
    const fx = (ev.clientX - rect.left) / rect.width * g.W;
    const i = Math.max(0, Math.min(pts.length - 1,
      Math.round((fx - g.L) / (g.W - g.L - g.R) * (pts.length - 1))));
    const xx = g.L + (g.W - g.L - g.R) * (i / Math.max(1, pts.length - 1));
    cross.setAttribute('x1', xx); cross.setAttribute('x2', xx);
    cross.style.display = '';
    tip.textContent = `${pts[i][0]}  ${Number(pts[i][1]).toFixed(2)}${unit}`;
  });
  el.addEventListener('mouseleave', () => { cross.style.display = 'none'; tip.textContent = ''; });
}
