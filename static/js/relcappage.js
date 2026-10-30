/* relcappage.js — 📐 상대 시총 · 📅 연도별 수익률 (데이터: /api/relcap · /api/annual_returns, 계산은 relcap.py)
 * 선 차트는 riskpage.js 의 _rkLineChart 를 같이 쓴다(그 파일이 먼저 실린다).
 */

const _RC_STATE = { period: '1Y' };

function renderRelcapPage() {
  const root = document.getElementById('relcap-view');
  if (!root) return;
  root.innerHTML = `<div class="pg-wrap">
    <div class="pg-title">📐 상대 시총</div>
    <div class="pg-sub">종목 간 시가총액 비율 추이 — 같은 업종 안에서 누가 더 비싸졌는지</div>
    <div class="rk-toolbar"><div class="rk-btns">${['6M', '1Y', '2Y'].map(p =>
      `<button class="rk-btn${p === _RC_STATE.period ? ' active' : ''}" data-p="${p}">${p}</button>`).join('')}</div></div>
    <div id="rc-body"><div class="bt-loading">⏳ 불러오는 중…</div></div>
    <div class="rk-foot">비율 = A 시총 ÷ B 시총(달러 환산). 과거 값은 현재 주식 수가 그대로였다고 보고 종가로 거슬러 계산했다 —
      그사이 자사주 소각·증자가 있었으면 그만큼 어긋난다. 출처: 네이버(국내 시총) · Yahoo Finance.<br>단순 정보 제공 목적이며, 투자 판단의 근거가 아닙니다.</div>
  </div>`;
  root.querySelectorAll('.rk-btn').forEach(b => b.addEventListener('click', () => {
    _RC_STATE.period = b.dataset.p; renderRelcapPage();
  }));
  _rkLoad(`/api/relcap?period=${_RC_STATE.period}`, document.getElementById('rc-body'), (d, box) => {
    let h = '<div class="rk-charts">';
    for (const p of d.pairs || []) {
      h += `<div class="rk-chart-box">
        <div class="rk-chart-title">${_escHtml(p.name)}
          <span class="rc-latest">${p.latest == null ? '—' : p.latest.toFixed(2) + '%'}</span>
          <span class="rk-date">${_escHtml(p.date || '')}</span></div>
        ${p.error ? `<div class="rk-empty">계산하지 못했다 — ${_escHtml(p.error)}</div>`
                  : _rkLineChart(p.series, { unit: '%', refs: [{ v: 100, label: '100' }] })}
        ${p.high != null ? `<div class="rk-note">기간 최고 ${p.high.toFixed(2)}% · 최저 ${p.low.toFixed(2)}%</div>` : ''}
      </div>`;
    }
    box.innerHTML = h + '</div>' + (d.updated_at ? `<div class="rk-note">갱신 ${_escHtml(d.updated_at)}</div>` : '');
  });
}

function renderAnnualPage() {
  const root = document.getElementById('annual-view');
  if (!root) return;
  root.innerHTML = `<div class="pg-wrap">
    <div class="pg-title">📅 주요 지수 연도별 수익률</div>
    <div class="pg-sub">S&amp;P 500 · NASDAQ · KOSPI · KOSDAQ — 연말 종가 기준, 올해는 작년 말 대비(YTD)</div>
    <div id="ar-body"><div class="bt-loading">⏳ 불러오는 중…</div></div>
    <div class="rk-foot">가격 지수라 배당은 빠져 있다. 출처: Yahoo Finance 월봉.<br>단순 정보 제공 목적이며, 투자 판단의 근거가 아닙니다.</div>
  </div>`;
  _rkLoad('/api/annual_returns', document.getElementById('ar-body'), (d, box) => {
    const rows = d.rows || [];
    const cell = v => {
      if (v == null) return '<td class="ar-cell">—</td>';
      const a = Math.min(Math.abs(v) / 40, 1) * 0.55;
      const bg = v > 0 ? `rgba(239,68,68,${a.toFixed(2)})` : `rgba(34,197,94,${a.toFixed(2)})`;
      return `<td class="ar-cell" style="background:${bg}">${v > 0 ? '+' : ''}${v.toFixed(1)}%</td>`;
    };
    let h = `<table class="mv-table ar-table"><thead><tr><th class="disc-dt">연도</th>${rows.map(r => `<th class="mv-num">${_escHtml(r.name)}</th>`).join('')}</tr></thead><tbody>`;
    const thisYear = (d.years || [])[0];
    for (const y of d.years || []) {
      h += `<tr><td class="disc-dt">${y}${y === thisYear ? ' <span class="rk-date">YTD</span>' : ''}</td>${rows.map(r => cell((r.returns || {})[y])).join('')}</tr>`;
    }
    box.innerHTML = h + '</tbody></table>';
  });
}
