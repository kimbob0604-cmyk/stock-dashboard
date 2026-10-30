/* fundtradepage.js — 💰 증시자금 · 🚢 수출입 (데이터: /api/fund_flow · /api/trade_stats, 계산은 public_stats.py)
 * 선 차트는 riskpage.js 의 _rkLineChart 를 같이 쓴다(그 파일이 먼저 실린다).
 */

const _FF_STATE = { period: '1Y' };

function _ffStatusNote(st, part) {
  if (!st) return '';
  if (st.error) return `<div class="bt-error">${_escHtml(st.error)}</div>`;
  const r = st[part];
  if (r && typeof r === 'object') {
    const bad = Object.entries(r).filter(([, v]) => typeof v === 'string' && v.startsWith('실패'));
    if (bad.length) return `<div class="bt-error">${bad.map(([k, v]) => _escHtml(k + ': ' + v)).join('<br>')}</div>`;
  }
  return '';
}

function _ffChange(v, unit) {
  if (v == null || isNaN(v)) return '';
  const col = v > 0 ? 'var(--color-up)' : v < 0 ? 'var(--color-down)' : 'var(--text-secondary)';
  return `<div class="gm-card-change" style="color:${col}">${v > 0 ? '▲' : v < 0 ? '▼' : ''} ${Math.abs(v).toFixed(2)}${unit || ''} <span class="px-lbl">전 영업일 대비</span></div>`;
}

function renderFundFlowPage() {
  const root = document.getElementById('fundflow-view');
  if (!root) return;
  root.innerHTML = `<div class="pg-wrap">
    <div class="pg-title">💰 증시자금</div>
    <div class="pg-sub">투자자예탁금 · 신용거래융자 · 반대매매 — 증시로 들어오고 빠지는 돈</div>
    <div class="rk-toolbar"><div class="rk-btns">${['6M', '1Y', '3Y'].map(p =>
      `<button class="rk-btn${p === _FF_STATE.period ? ' active' : ''}" data-p="${p}">${p}</button>`).join('')}</div></div>
    <div id="ff-body"><div class="bt-loading">⏳ 불러오는 중…</div></div>
    <div class="rk-foot">출처: 금융위원회 금융투자협회종합통계(공공데이터포털 15094809). 금투협이 영업일 다음 날 13시 이후 공개해
      최근 값이 1~2거래일 늦다. 신용/시총 비율은 최근 신용융자 ÷ 오늘 시총 합(코스피·코스닥 보통주·우선주·리츠).<br>단순 정보 제공 목적이며, 투자 판단의 근거가 아닙니다.</div>
  </div>`;
  root.querySelectorAll('.rk-btn').forEach(b => b.addEventListener('click', () => {
    _FF_STATE.period = b.dataset.p; renderFundFlowPage();
  }));
  _rkLoad(`/api/fund_flow?period=${_FF_STATE.period}`, document.getElementById('ff-body'), (d, box) => {
    const note = _ffStatusNote(d.status, 'kofia');
    if (d.error) {
      box.innerHTML = note + `<div class="rk-empty">${_escHtml(d.error)}</div>`;
      return;
    }
    const L = d.latest || {};
    const card = (k, name) => {
      const x = L[k];
      return `<div class="gm-card"><div class="gm-card-name">${name} <span class="rk-date">${_escHtml(x ? x.date : '')}</span></div>
        <div class="gm-card-value">${x ? x.value.toFixed(2) + '조' : '—'}</div>${x ? _ffChange(x.change, '조') : ''}</div>`;
    };
    const ratio = (m, name) => `<div class="gm-card"><div class="gm-card-name">신용/시총 · ${name}</div>
      <div class="gm-card-value">${(d.ratio || {})[m] == null ? '—' : d.ratio[m].toFixed(2) + '%'}</div></div>`;
    const S = d.series || {};
    box.innerHTML = note + `
      <div class="gm-section"><div class="gm-section-title">최근 값</div>
        <div class="gm-grid rk-grid2">${card('deposit', '투자자예탁금')}${card('credit_total', '신용융자 전체')}
          ${card('credit_kospi', '신용융자 코스피')}${card('credit_kosdaq', '신용융자 코스닥')}${card('forced_sale', '반대매매')}</div>
        <div class="gm-grid rk-grid2" style="margin-top:10px">${ratio('ALL', '전체')}${ratio('KOSPI', '코스피')}${ratio('KOSDAQ', '코스닥')}</div>
      </div>
      <div class="rk-charts">
        <div class="rk-chart-box"><div class="rk-chart-title">투자자예탁금 (조원)</div>${_rkLineChart(S.deposit || [], { unit: '조' })}</div>
        <div class="rk-chart-box"><div class="rk-chart-title">신용거래융자 전체 (조원)</div>${_rkLineChart(S.credit_total || [], { unit: '조' })}</div>
        <div class="rk-chart-box"><div class="rk-chart-title">신용융자 코스피 (조원)</div>${_rkLineChart(S.credit_kospi || [], { unit: '조' })}</div>
        <div class="rk-chart-box"><div class="rk-chart-title">신용융자 코스닥 (조원)</div>${_rkLineChart(S.credit_kosdaq || [], { unit: '조' })}</div>
      </div>
      ${d.unit_multiplier && d.unit_multiplier !== 1 ? `<div class="rk-note">원본 값에 ×${d.unit_multiplier.toLocaleString()} 을 곱해 원으로 바꿨다(값 크기로 판정).</div>` : ''}`;
  });
}

function _tdUsd(v) {
  if (v == null || isNaN(v)) return '—';
  if (v >= 1e9) return '$' + (v / 1e9).toFixed(2) + 'B';
  if (v >= 1e6) return '$' + (v / 1e6).toFixed(1) + 'M';
  return '$' + Math.round(v / 1e3).toLocaleString() + 'K';
}

function _tdPct(v, label) {
  if (v == null || isNaN(v)) return '';
  const col = v > 0 ? 'var(--color-up)' : v < 0 ? 'var(--color-down)' : 'var(--text-secondary)';
  return `<span style="color:${col}">${label} ${v > 0 ? '+' : ''}${v.toFixed(1)}%</span>`;
}

function renderTradePage() {
  const root = document.getElementById('trade-view');
  if (!root) return;
  root.innerHTML = `<div class="pg-wrap">
    <div class="pg-title">🚢 수출입</div>
    <div class="pg-sub">관세청 품목별 월간 수출 — 반도체·전력·2차전지·소비재·자동차·조선·방산</div>
    <div id="td-body"><div class="bt-loading">⏳ 불러오는 중…</div></div>
    <div class="rk-foot">출처: 관세청 품목별 수출입실적(공공데이터포털 15101609). 매월 15일경 전월까지 확정. 단가 = 수출액 ÷ 중량($/kg).
      HS 코드 묶음이라 회사 실적과 1:1 로 맞지는 않는다.<br>단순 정보 제공 목적이며, 투자 판단의 근거가 아닙니다.</div>
  </div>`;
  _rkLoad('/api/trade_stats', document.getElementById('td-body'), (d, box) => {
    const note = _ffStatusNote(d.status, 'customs');
    const items = d.items || [];
    if (!items.some(it => it.series && it.series.length)) {
      box.innerHTML = note + '<div class="rk-empty">아직 받은 값이 없다 — 공공데이터포털 키·활용신청(15101609)을 확인한다</div>';
      return;
    }
    // 한 격자에 — 분류마다 따로 두면 품목이 하나뿐인 분류의 차트가 화면 폭만큼 커진다
    let h = note + '<div class="rk-charts">';
    for (const it of items) {
      const L = it.latest;
      const pts = (it.series || []).map(p => ({ date: `${p.ym.slice(0, 4)}-${p.ym.slice(4)}`, value: p.exp_usd / 1e6 }));
      h += `<div class="rk-chart-box">
        <div class="rk-chart-title"><span class="disc-cat">${_escHtml(it.category)}</span> ${_escHtml(it.name)}
          <span class="rk-date">HS ${_escHtml(it.hs)}</span>
          <span class="rc-latest">${L ? _tdUsd(L.exp_usd) : '—'}</span></div>
        ${L ? `<div class="td-meta">${L.ym.slice(0, 4)}.${L.ym.slice(4)} · ${_tdPct(L.yoy_pct, '전년비')} · ${_tdPct(L.mom_pct, '전월비')}
          · 단가 ${L.unit_price == null ? '—' : '$' + L.unit_price.toLocaleString() + '/kg'}</div>` : ''}
        ${pts.length ? _rkLineChart(pts, { unit: 'M$' }) : '<div class="rk-empty">값 없음</div>'}
      </div>`;
    }
    h += '</div>';
    box.innerHTML = h;
  });
}
