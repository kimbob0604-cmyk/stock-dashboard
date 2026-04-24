// ===== static/js/trading.js — 매매 실전 도구 (포지션 사이즈 계산기) =====
// 기존 체크리스트(_showChecklist)·분할매수 계획기(_renderSplitPlanner)는
// pages.js에 이미 구현되어 있음. 이 파일은 Position Sizer 전용.
'use strict';

// localStorage 키 — 계정 리스크 파라미터 영속화
const _PS_CAP_KEY  = 'ps_capital';
const _PS_RISK_KEY = 'ps_risk_pct';

function renderPositionSizer(container) {
  const savedCap  = localStorage.getItem(_PS_CAP_KEY)  || '';
  const savedRisk = localStorage.getItem(_PS_RISK_KEY) || '2';
  const html = `
    <div class="ps-card">
      <div class="ps-title">📐 포지션 사이즈 계산기</div>
      <div class="ps-desc">계좌 리스크(손실 허용) 기준으로 적정 매수 수량을 자동 계산합니다. 켈리 공식 변형.</div>

      <div class="ps-grid">
        <div class="ps-input-group">
          <label>총 투자자산 (₩)</label>
          <input type="number" id="ps-total-capital" class="ps-input"
                 value="${savedCap}" placeholder="30000000" oninput="_calcPositionSize()">
        </div>
        <div class="ps-input-group">
          <label>1회 최대 리스크 (%)</label>
          <input type="number" id="ps-risk-pct" class="ps-input"
                 value="${savedRisk}" step="0.5" min="0.5" max="10" oninput="_calcPositionSize()">
        </div>
        <div class="ps-input-group">
          <label>진입가</label>
          <input type="number" id="ps-entry-price" class="ps-input"
                 placeholder="55000" oninput="_calcPositionSize()">
        </div>
        <div class="ps-input-group">
          <label>손절가</label>
          <input type="number" id="ps-stop-price" class="ps-input"
                 placeholder="52000" oninput="_calcPositionSize()">
        </div>
        <div class="ps-input-group">
          <label>목표가 (선택)</label>
          <input type="number" id="ps-target-price" class="ps-input"
                 placeholder="62000" oninput="_calcPositionSize()">
        </div>
      </div>

      <div id="ps-result" class="ps-result">
        <div class="ps-placeholder">진입가·손절가를 입력하면 자동 계산됩니다.</div>
      </div>
    </div>`;
  container.insertAdjacentHTML('afterbegin', html);
  // 초기 호출 (저장된 값이 있으면)
  _calcPositionSize();
}

function _calcPositionSize() {
  const capital = parseFloat(document.getElementById('ps-total-capital')?.value) || 0;
  const riskPct = parseFloat(document.getElementById('ps-risk-pct')?.value)     || 2;
  const entry   = parseFloat(document.getElementById('ps-entry-price')?.value)  || 0;
  const stop    = parseFloat(document.getElementById('ps-stop-price')?.value)   || 0;
  const target  = parseFloat(document.getElementById('ps-target-price')?.value) || 0;

  const box = document.getElementById('ps-result');
  if (!box) return;

  // 영속화 (디바운스 없이 매 입력마다 저장 — 값이 가벼워 부담 없음)
  if (capital > 0) localStorage.setItem(_PS_CAP_KEY,  capital.toString());
  if (riskPct > 0) localStorage.setItem(_PS_RISK_KEY, riskPct.toString());

  if (!capital || !entry || !stop) {
    box.innerHTML = '<div class="ps-placeholder">총자산·진입가·손절가를 입력하면 계산됩니다.</div>';
    return;
  }

  const perShareRisk = Math.abs(entry - stop);
  if (perShareRisk <= 0) {
    box.innerHTML = '<div class="ps-warn">진입가와 손절가가 같습니다.</div>';
    return;
  }

  const maxRiskAmount = capital * (riskPct / 100);
  const maxShares     = Math.floor(maxRiskAmount / perShareRisk);
  const totalInvest   = maxShares * entry;
  const investPct     = capital > 0 ? (totalInvest / capital * 100) : 0;
  const stopLossPct   = ((stop - entry) / entry * 100);

  let rrRatio = null, targetPct = null, profit = null;
  if (target && target > entry) {
    rrRatio   = (target - entry) / perShareRisk;
    targetPct = ((target - entry) / entry * 100);
    profit    = maxShares * (target - entry);
  }

  const rrCol   = rrRatio == null ? '#888'
                 : rrRatio >= 2   ? '#FF3333'
                 : rrRatio >= 1.5 ? '#FFD700'
                 : '#FF6B6B';
  const rrEmoji = rrRatio == null ? ''
                 : rrRatio >= 2   ? '✅'
                 : rrRatio >= 1.5 ? '⚠️'
                 : '❌';

  box.innerHTML = `
    <div class="ps-result-grid">
      <div class="ps-result-item ps-highlight">
        <div class="ps-result-label">적정 매수 수량</div>
        <div class="ps-result-value">${maxShares.toLocaleString()}주</div>
      </div>
      <div class="ps-result-item">
        <div class="ps-result-label">총 투자금</div>
        <div class="ps-result-value">₩${totalInvest.toLocaleString()}</div>
        <div class="ps-result-sub">자산 대비 ${investPct.toFixed(1)}%</div>
      </div>
      <div class="ps-result-item">
        <div class="ps-result-label">최대 손실금</div>
        <div class="ps-result-value" style="color:#33AA33">₩${Math.round(maxRiskAmount).toLocaleString()}</div>
        <div class="ps-result-sub">손절 시 ${stopLossPct.toFixed(2)}%</div>
      </div>
      ${rrRatio != null ? `
        <div class="ps-result-item">
          <div class="ps-result-label">손익비 (R:R)</div>
          <div class="ps-result-value" style="color:${rrCol}">${rrEmoji} 1 : ${rrRatio.toFixed(2)}</div>
          <div class="ps-result-sub">${rrRatio >= 1.5 ? '적정' : '불리 — 재검토'}</div>
        </div>
        <div class="ps-result-item">
          <div class="ps-result-label">목표 수익금</div>
          <div class="ps-result-value" style="color:#FF3333">₩${Math.round(profit).toLocaleString()}</div>
          <div class="ps-result-sub">목표가 도달 시 +${targetPct.toFixed(2)}%</div>
        </div>
      ` : ''}
    </div>
    ${investPct > 30 ? '<div class="ps-warn">⚠️ 총자산 대비 30% 초과 — 집중 리스크</div>' : ''}
    ${rrRatio != null && rrRatio < 1.5 ? '<div class="ps-warn">⚠️ 손익비 1:1.5 미만 — 목표가/손절가 재검토 권장</div>' : ''}
  `;
}
