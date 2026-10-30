/* moverspage.js — 📈 신고가 · 등락률 (데이터: /api/movers, 계산은 movers.py) */

const _MV_STATE = { view: 'newhigh', period: 'day', market: 'ALL' };

function renderMoversPage() {
  const root = document.getElementById('movers-view');
  if (!root) return;
  const btn = (group, val, label, cur) =>
    `<button class="rk-btn${val === cur ? ' active' : ''}" data-g="${group}" data-v="${val}">${label}</button>`;
  root.innerHTML = `<div class="pg-wrap">
    <div class="pg-title">📈 신고가 · 등락률</div>
    <div class="pg-sub">시장의 주도주 — 종가 기준 신고가(역사적·52주·120일·60일)와 등락률 상·하위</div>
    <div class="rk-toolbar">
      <div class="rk-btns">${btn('view', 'newhigh', '신고가', _MV_STATE.view)}${btn('view', 'change', '등락률', _MV_STATE.view)}</div>
      <div class="rk-btns">${btn('market', 'ALL', '전체', _MV_STATE.market)}${btn('market', 'KOSPI', '코스피', _MV_STATE.market)}${btn('market', 'KOSDAQ', '코스닥', _MV_STATE.market)}</div>
      ${_MV_STATE.view === 'change'
        ? `<div class="rk-btns">${btn('period', 'day', '일간', _MV_STATE.period)}${btn('period', 'week', '주간', _MV_STATE.period)}</div>` : ''}
    </div>
    <div id="mv-body"><div class="bt-loading">⏳ 불러오는 중…</div></div>
    <div class="rk-foot" id="mv-foot"></div>
  </div>`;
  root.querySelectorAll('.rk-btn').forEach(b => b.addEventListener('click', () => {
    _MV_STATE[b.dataset.g] = b.dataset.v;
    renderMoversPage();
  }));
  _mvLoad();
}

async function _mvLoad() {
  const box = document.getElementById('mv-body');
  const foot = document.getElementById('mv-foot');
  const q = new URLSearchParams(_MV_STATE).toString();
  try {
    const r = await fetch('/api/movers?' + q);
    const d = await r.json();
    if (!r.ok || d.error) {
      box.innerHTML = `<div class="bt-error">${_escHtml(d.error || ('HTTP ' + r.status))}</div>`;
      return;
    }
    foot.innerHTML = `${_escHtml(d.basis || '')} · 갱신 ${_escHtml(d.updated_at || '')}<br>단순 정보 제공 목적이며, 투자 판단의 근거가 아닙니다.`;
    if (d.view === 'newhigh') _mvRenderNewHigh(d.data, box);
    else _mvRenderChange(d.data, d.period, box);
    box.querySelectorAll('tr[data-code]').forEach(tr => tr.addEventListener('click', () => {
      if (typeof openChartPanel === 'function') openChartPanel(tr.dataset.code, tr.dataset.name);
    }));
  } catch (e) {
    box.innerHTML = `<div class="bt-error">로드 실패: ${_escHtml(e.message)}</div>`;
  }
}

const _MV_MKT = { KOSPI: '코스피', KOSDAQ: '코스닥' };

function _mvCap(won) {
  if (!won) return '—';
  const eok = won / 1e8;
  return eok >= 10000 ? (eok / 10000).toFixed(1) + '조' : Math.round(eok).toLocaleString() + '억';
}

function _mvTurnover(mn) {           // volume_mn = 거래대금(백만원)
  if (!mn) return '—';
  const eok = mn / 100;
  return eok >= 1 ? Math.round(eok).toLocaleString() + '억' : '<1억';
}

function _mvRows(items, opts) {
  opts = opts || {};
  if (!items.length) return '<div class="rk-empty">해당 종목 없음</div>';
  let h = `<table class="mv-table"><thead><tr><th>#</th><th>종목</th><th class="mv-hide-m">업종</th>
    <th class="mv-num">${opts.pctLabel || '등락률'}</th><th class="mv-num">종가</th>
    <th class="mv-num mv-hide-m">거래대금</th><th class="mv-num mv-hide-m">시총</th></tr></thead><tbody>`;
  items.forEach((it, i) => {
    const p = it.change_pct;
    const col = p > 0 ? 'var(--color-up)' : p < 0 ? 'var(--color-down)' : 'var(--text-secondary)';
    h += `<tr data-code="${_escHtml(it.code)}" data-name="${_escHtml(it.name)}">
      <td class="mv-rank">${i + 1}</td>
      <td><span class="mv-name">${_escHtml(it.name)}</span>
        <span class="mv-mkt">${_escHtml(_MV_MKT[it.market] || '')}</span></td>
      <td class="mv-hide-m mv-sector">${_escHtml(it.sector || '')}</td>
      <td class="mv-num" style="color:${col}">${p == null ? '—' : (p > 0 ? '+' : '') + Number(p).toFixed(2) + '%'}</td>
      <td class="mv-num">${it.close ? Number(it.close).toLocaleString() : '—'}</td>
      <td class="mv-num mv-hide-m">${_mvTurnover(it.volume_mn)}</td>
      <td class="mv-num mv-hide-m">${_mvCap(it.market_cap)}</td>
    </tr>`;
  });
  return h + '</tbody></table>';
}

function _mvRenderNewHigh(d, box) {
  if (d.error) {
    box.innerHTML = `<div class="bt-error">신고가를 판정하지 못했다: ${_escHtml(d.error)}</div>`;
    return;
  }
  let h = `<div class="rk-desc">오늘 종가가 창 안 최고 종가를 넘은 종목. 한 종목은 가장 센 줄에만 —
    역사적(상장 이후) > 52주(달력 52주) > 120일 > 60일(거래일). 판정 ${d.scanned ?? '—'}종목 / 모집단 ${d.universe_n ?? '—'}종목`
    + (d.hist_pending ? ` · 역사적 판정 보류 ${d.hist_pending}종목(상장 이후 이력 미확인 — 52주 이하에 표시)` : '')
    + `</div>`;
  for (const ax of d.axes || []) {
    h += `<div class="gm-section"><div class="gm-section-title">${ax.icon} ${_escHtml(ax.label)} 신고가 ${ax.items.length}종목</div>
      ${_mvRows(ax.items)}</div>`;
  }
  box.innerHTML = h;
}

function _mvRenderChange(d, period, box) {
  const week = period === 'week';
  const note = week
    ? (d.asof ? `${_escHtml(d.asof)} 종가 vs ${_escHtml(d.base || '?')} 종가 (5거래일) · 일봉 기준이라 장중에는 전일까지 · ${d.scanned}종목`
              : '일봉이 아직 없다 — 16:10 일봉 채움 뒤에 보인다')
    : `오늘 등락률(장중이면 지금 값) · 상승 ${d.n_up}종목 · 하락 ${d.n_down}종목`;
  box.innerHTML = `<div class="rk-desc">${note}</div>
    <div class="mv-two">
      <div class="gm-section"><div class="gm-section-title">🔺 상승 상위</div>${_mvRows(d.up || [], { pctLabel: week ? '주간' : '등락률' })}</div>
      <div class="gm-section"><div class="gm-section-title">🔻 하락 상위</div>${_mvRows(d.down || [], { pctLabel: week ? '주간' : '등락률' })}</div>
    </div>`;
}
