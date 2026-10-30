/* proxypage.js — 🌐 글로벌 프록시 (데이터: /api/global_proxy, 계산은 global_proxy.py)
 * 한국 장이 닫힌 동안 한국 주식을 비춰 보는 해외 가격들. 60초마다 다시 부른다(페이지에 있을 때만).
 */

let _PX_TIMER = null;

function renderProxyPage() {
  const root = document.getElementById('proxy-view');
  if (!root) return;
  root.innerHTML = `<div class="pg-wrap">
    <div class="pg-title">🌐 글로벌 프록시</div>
    <div class="pg-sub">한국 장이 닫힌 동안 — 24시간 선물·해외 상장·미국 선물·관련 ETF 로 한국 주식을 가늠한다</div>
    <div id="px-body"><div class="bt-loading">⏳ 불러오는 중…</div></div>
    <div class="rk-foot" id="px-foot"></div>
  </div>`;
  _pxLoad();
  if (_PX_TIMER) clearInterval(_PX_TIMER);
  _PX_TIMER = setInterval(() => {
    if (typeof APP !== 'undefined' && APP.page !== 'proxy') { clearInterval(_PX_TIMER); _PX_TIMER = null; return; }
    _pxLoad();
  }, 60000);
}

async function _pxLoad() {
  const box = document.getElementById('px-body');
  if (!box) return;
  try {
    const r = await fetch('/api/global_proxy');
    const d = await r.json();
    if (!r.ok || d.error) { box.innerHTML = `<div class="bt-error">${_escHtml(d.error || ('HTTP ' + r.status))}</div>`; return; }
    box.innerHTML = _pxRender(d);
    document.getElementById('px-foot').innerHTML =
      `갱신 ${_escHtml(d.updated_at || '')} · 환율 USD/KRW ${d.usdkrw ? Number(d.usdkrw).toFixed(2) : '—'} · 출처: Hyperliquid, Yahoo Finance<br>단순 정보 제공 목적이며, 투자 판단의 근거가 아닙니다.`;
  } catch (e) {
    box.innerHTML = `<div class="bt-error">로드 실패: ${_escHtml(e.message)}</div>`;
  }
}

function _pxPct(v, label) {
  if (v == null || isNaN(v)) return '';
  const col = v > 0 ? 'var(--color-up)' : v < 0 ? 'var(--color-down)' : 'var(--text-secondary)';
  return `<div class="gm-card-change" style="color:${col}">${v > 0 ? '+' : ''}${Number(v).toFixed(2)}%${label ? ` <span class="px-lbl">${_escHtml(label)}</span>` : ''}</div>`;
}

function _pxNum(v, unit) {
  if (v == null || isNaN(v)) return '—';
  const s = Math.abs(v) >= 1000 ? Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 })
                                : Number(v).toFixed(2);
  if (unit === '$') return '$' + s;
  if (unit === '€') return '€' + s;
  if (unit === '%') return s + '%';
  return s + (unit && unit !== 'pt' ? unit : '');
}

function _pxCard(it, inner) {
  if (it.error) {
    return `<div class="gm-card px-err"><div class="gm-card-name">${_escHtml(it.name)}</div>
      <div class="gm-card-value">—</div><div class="px-lbl">받지 못함</div></div>`;
  }
  return `<div class="gm-card"><div class="gm-card-name">${_escHtml(it.name)}</div>${inner}</div>`;
}

function _pxRender(d) {
  const sec = (title, desc, cards) => `<div class="gm-section"><div class="gm-section-title">${title}</div>
    ${desc ? `<div class="rk-desc">${desc}</div>` : ''}<div class="gm-grid rk-grid2">${cards}</div></div>`;
  const perps = (d.perps || []).map(it => _pxCard(it, `
    <div class="gm-card-value">${_pxNum(it.price, it.unit)}</div>
    ${_pxPct(it.vs_close_pct, 'vs ' + (it.vs_label || '국내'))}
    ${_pxPct(it.change_24h_pct, '24시간')}
    <div class="px-lbl">펀딩 ${it.funding_8h_pct == null ? '—' : (it.funding_8h_pct > 0 ? '+' : '') + Number(it.funding_8h_pct).toFixed(4) + '%'} / 8h</div>`)).join('');
  const lists = (d.listings || []).map(it => _pxCard(it, `
    <div class="gm-card-value">${_pxNum(it.price, it.unit)}</div>
    ${_pxPct(it.change_pct, '전일 대비')}
    <div class="px-prem">국내 대비 괴리 <b style="color:${(it.premium_pct || 0) >= 0 ? 'var(--color-up)' : 'var(--color-down)'}">${it.premium_pct == null ? '—' : (it.premium_pct > 0 ? '+' : '') + it.premium_pct.toFixed(2) + '%'}</b></div>
    <div class="px-lbl">1증서 = 보통주 ${it.ratio == null ? '?' : it.ratio}주 · 환산 ${_pxNum(it.fair, it.unit)}</div>`)).join('');
  const simple = arr => (arr || []).map(it => _pxCard(it, `
    <div class="gm-card-value">${_pxNum(it.price, it.unit)}</div>${_pxPct(it.change_pct, '전일 대비')}`)).join('');
  return sec('24시간 선물 · Hyperliquid', '무기한 선물(달러, 코스피200 은 지수). 국내 최근 가격(장중 현재가·마감 뒤 종가)과 같은 순간 환율로 견준다. 펀딩률은 8시간 기준.', perps)
       + sec('해외 상장 주식 · 괴리율', '국내 가격을 환율과 증서 비율로 환산한 값 대비. SK하이닉스 ADR 1주 = 보통주 1/10주, 삼성전자 GDR 1주 = 보통주 25주.', lists)
       + sec('미국 지수선물', '', simple(d.futures))
       + sec('매크로', '', simple(d.macro))
       + sec('미국 ETF 프록시', 'DRAM(메모리) · EWY(한국) · SOXX(반도체) — 직전 정규장 종가 대비.', simple(d.us_etfs));
}
