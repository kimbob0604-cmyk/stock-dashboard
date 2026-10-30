/* disclosurepage.js — 📢 DART 공시 (카테고리 칩 · 카드/목록 · 시총 · 잠정실적 숫자)
 *
 * 데이터: /api/disclosures — 카테고리는 서버(disclosure_category.py)가 제목으로 붙인다.
 * `_disclosureFilter`(전체/중요만)는 pages.js 에 선언돼 있다.
 */

const _DISC_STATE = { category: '', view: 'grid' };

async function renderDisclosurePage() {
  const root = document.getElementById('disclosure-view');
  if (!root.dataset.built) {
    root.dataset.built = '1';
    root.innerHTML = `
      <div class="pg-wrap">
        <div class="pg-title">📢 DART 공시</div>
        <div class="pg-sub">최근 공시를 카테고리별로 — 제목 기준 분류, 원문은 DART 에서 확인</div>
        <div class="disc-toolbar">
          <div class="dc-market-toggle" id="disc-filter-bar">
            <button class="dc-market-btn active" data-filter="all">전체</button>
            <button class="dc-market-btn" data-filter="important">🔥 중요만</button>
          </div>
          <div class="disc-viewtoggle" id="disc-view">
            <button class="rk-btn active" data-view="grid" title="카드">▦</button>
            <button class="rk-btn" data-view="list" title="목록">☰</button>
          </div>
        </div>
        <div class="disc-chips" id="disc-chips"></div>
        <div id="disc-list" class="pg-empty"><div class="pg-empty-title">로딩 중…</div></div>
        <div class="rk-foot">DART 공시를 제목으로 분류·요약한 자료다. 반드시 원문을 확인하고, 투자 판단의 직접 근거로 쓰지 말 것.</div>
      </div>`;
    document.getElementById('disc-filter-bar').addEventListener('click', (e) => {
      const btn = e.target.closest('.dc-market-btn');
      if (!btn) return;
      _disclosureFilter = btn.dataset.filter;
      document.querySelectorAll('#disc-filter-bar .dc-market-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      _fetchDisclosures();
    });
    document.getElementById('disc-view').addEventListener('click', (e) => {
      const btn = e.target.closest('.rk-btn');
      if (!btn) return;
      _DISC_STATE.view = btn.dataset.view;
      document.querySelectorAll('#disc-view .rk-btn').forEach(b => b.classList.toggle('active', b === btn));
      _fetchDisclosures();
    });
    document.getElementById('disc-chips').addEventListener('click', (e) => {
      const chip = e.target.closest('.disc-chip');
      if (!chip) return;
      _DISC_STATE.category = chip.dataset.cat || '';
      _fetchDisclosures();
    });
  }
  _fetchDisclosures();
}

function _discCap(won) {
  if (!won) return '';
  const eok = won / 1e8;
  return eok >= 10000 ? (eok / 10000).toFixed(1) + '조' : Math.round(eok).toLocaleString() + '억';
}

function _discMn(mn) {               // 백만원 → 억/조
  if (mn == null || isNaN(mn)) return '—';
  const eok = mn / 100;
  if (Math.abs(eok) >= 10000) return (eok / 10000).toFixed(2) + '조';
  return Math.round(eok).toLocaleString() + '억';
}

function _discYoy(p) {
  if (p == null || isNaN(p)) return '';
  const col = p > 0 ? 'var(--color-up)' : p < 0 ? 'var(--color-down)' : 'var(--text-secondary)';
  return ` <span style="color:${col}">(YoY ${p > 0 ? '+' : ''}${Number(p).toFixed(1)}%)</span>`;
}

function _discDate(s) {
  s = String(s || '');
  return s.length === 8 ? `${s.slice(4, 6)}.${s.slice(6, 8)}` : s;
}

function _discChips(d) {
  const counts = d.category_counts || {};
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  let h = `<button class="disc-chip${_DISC_STATE.category ? '' : ' active'}" data-cat="">전체<b>${total}</b></button>`;
  for (const c of d.categories || []) {
    const n = counts[c] || 0;
    if (!n && _DISC_STATE.category !== c) continue;          // 없는 카테고리는 숨긴다
    h += `<button class="disc-chip${_DISC_STATE.category === c ? ' active' : ''}" data-cat="${_escHtml(c)}">${_escHtml(c)}<b>${n}</b></button>`;
  }
  return h;
}

async function _fetchDisclosures() {
  const box = document.getElementById('disc-list');
  const chips = document.getElementById('disc-chips');
  if (!box) return;
  try {
    const imp = _disclosureFilter === 'important' ? '&importance=critical,high' : '';
    const cat = _DISC_STATE.category ? `&category=${encodeURIComponent(_DISC_STATE.category)}` : '';
    const r = await fetch(`/api/disclosures?limit=150${imp}${cat}`);
    const d = await r.json();
    if (d.error) { box.innerHTML = `<div class="pg-empty">${_escHtml(d.error)}</div>`; return; }
    if (chips) chips.innerHTML = _discChips(d);
    const items = d.items || [];
    if (!items.length) {
      box.className = 'pg-empty';
      box.innerHTML = d.collector_enabled === false
        ? '<div class="pg-empty-title">공시 수집이 꺼져 있다</div>이 서버에 DART_API_KEY 가 없어 공시를 모으지 않는다 — Render 환경변수에 키를 넣으면 평일 08~18시 1분 간격으로 쌓인다.'
        : '<div class="pg-empty-title">해당 공시 없음</div>';
      return;
    }
    box.className = '';
    box.innerHTML = _DISC_STATE.view === 'list' ? _discListHtml(items) : _discGridHtml(items);
    box.querySelectorAll('.disc-code').forEach(el => {
      el.addEventListener('click', (ev) => {
        ev.preventDefault();
        const c = el.dataset.code;
        if (c && isKrCode(c)) openChartPanel(c);
      });
    });
  } catch (err) {
    box.innerHTML = `<div class="pg-empty">${_escHtml(err.message)}</div>`;
  }
}

function _discImpClass(it) {
  const imp = it.importance || 'low';
  return imp === 'critical' ? 'disc-critical' : imp === 'high' ? 'disc-high'
       : imp === 'medium' ? 'disc-medium' : '';
}

function _discFacts(it) {
  const f = [];
  const cap = _discCap(it.market_cap);
  if (cap) f.push(`시총 <b>${cap}</b>`);
  const e = it.earnings;
  if (e) {
    if (e.revenue != null) f.push(`매출 <b>${_discMn(e.revenue)}</b>${_discYoy(e.revenue_yoy_pct)}`);
    if (e.operating_profit != null) f.push(`영업익 <b>${_discMn(e.operating_profit)}</b>${_discYoy(e.op_yoy_pct)}`);
    if (e.net_income != null) f.push(`순이익 <b>${_discMn(e.net_income)}</b>`);
  }
  if (it.score) f.push(`중요도 ${it.score}점`);
  return f;
}

function _discGridHtml(items) {
  let h = '<div class="disc-cards">';
  for (const it of items) {
    const code = it.stock_code || '';
    const facts = _discFacts(it);
    const kw = (it.keywords || []).join(', ');
    h += `<a class="disc-card ${_discImpClass(it)}" href="${_escHtml(it.dart_url || '')}" target="_blank" rel="noopener">
      <div class="disc-card-head">
        <b>${_escHtml(it.corp_name || '')}</b>
        ${code ? `<span class="disc-code" data-code="${_escHtml(code)}">${_escHtml(code)}</span>` : ''}
        <span class="disc-date">${_escHtml(_discDate(it.rcept_dt))}</span>
        <span class="disc-cat">${_escHtml(it.category || '')}</span>
      </div>
      <div class="disc-card-title">${_escHtml(it.title || '')}</div>
      ${facts.length ? `<div class="disc-facts">${facts.map(x => `<span>${x}</span>`).join('')}</div>` : ''}
      ${kw ? `<div class="disc-kw">🔑 ${_escHtml(kw)}</div>` : ''}
    </a>`;
  }
  return h + '</div>';
}

function _discListHtml(items) {
  let h = '<table class="mv-table disc-table"><thead><tr><th class="disc-dt">날짜</th><th>회사</th><th>분류</th><th>공시</th><th class="mv-num mv-hide-m">시총</th></tr></thead><tbody>';
  for (const it of items) {
    const code = it.stock_code || '';
    h += `<tr class="${_discImpClass(it)}">
      <td class="disc-dt">${_escHtml(_discDate(it.rcept_dt))}</td>
      <td><span class="mv-name">${_escHtml(it.corp_name || '')}</span>
        ${code ? `<span class="disc-code mv-mkt" data-code="${_escHtml(code)}">${_escHtml(code)}</span>` : ''}</td>
      <td><span class="disc-cat">${_escHtml(it.category || '')}</span></td>
      <td><a href="${_escHtml(it.dart_url || '')}" target="_blank" rel="noopener">${_escHtml(it.title || '')}</a></td>
      <td class="mv-num mv-hide-m">${_discCap(it.market_cap) || '—'}</td>
    </tr>`;
  }
  return h + '</tbody></table>';
}
