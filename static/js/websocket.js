// ===== static/js/websocket.js — Socket.IO 실시간 가격 시스템 =====
'use strict';

// ─────────────────────────────────────────────────────────────────────────
// WebSocket 실시간 가격 시스템
// ─────────────────────────────────────────────────────────────────────────
const WS = {
  socket: null,
  subs: new Set(),
  handlers: {},  // {code: [cb, ...]}

  connect() {
    if (this.socket?.connected) return;
    if (typeof io === 'undefined') return;
    try {
      this.socket = io(location.origin, {
        transports: ['websocket', 'polling'],
        reconnectionAttempts: 5,
        reconnectionDelay: 3000,
        timeout: 5000,
      });
      this.socket.on('connect', () => {
        console.log('[WS] connected');
        if (this.subs.size > 0) this.socket.emit('subscribe', { codes: [...this.subs] });
      });
      this.socket.on('price_update', (d) => {
        for (const cb of (this.handlers[d.code] || [])) {
          try { cb(d); } catch(e) { /* ignore */ }
        }
        if (d.code && d.close) WS._cache[d.code] = d;
      });
      this.socket.on('connect_error', (err) => {
        console.warn('[WS] 연결 실패, HTTP 모드:', err.message);
      });
      this.socket.on('disconnect', () => console.log('[WS] disconnected'));
    } catch (e) {
      console.warn('[WS] 초기화 실패:', e.message);
      this.socket = null;
    }
  },
  _cache: {},

  subscribe(codes, cb) {
    try { if (!this.socket) this.connect(); } catch { /* ignore */ }
    if (!Array.isArray(codes)) codes = [codes];
    codes = codes.filter(Boolean);
    for (const c of codes) {
      this.subs.add(c);
      if (cb) (this.handlers[c] = this.handlers[c] || []).push(cb);
    }
    try { if (this.socket?.connected) this.socket.emit('subscribe', { codes }); } catch { /* ignore */ }
  },

  unsubscribe(codes) {
    if (!Array.isArray(codes)) codes = [codes];
    for (const c of codes) {
      this.subs.delete(c);
      delete this.handlers[c];
    }
    if (this.socket?.connected) this.socket.emit('unsubscribe', { codes });
  },

  unsubscribeAll() {
    if (this.subs.size && this.socket?.connected) {
      this.socket.emit('unsubscribe', { codes: [...this.subs] });
    }
    this.subs.clear();
    this.handlers = {};
  }
};

// 자동 연결 (실패해도 무시 — 실시간 가격만 안 됨)
try { if (typeof io !== 'undefined') WS.connect(); } catch { /* ignore */ }

// ── 테마맵 실시간 연동 ──
function _wsSubscribeThemeStocks(stockCodes) {
  try { WS.unsubscribeAll(); } catch { /* ignore */ }
  WS.subscribe(stockCodes, (d) => {
    // 종목 셀 찾아서 가격 갱신
    const cell = document.querySelector(`[data-code="${d.code}"]`);
    if (!cell) return;
    const priceEl = cell.querySelector('.stock-price, .treemap-price');
    if (priceEl) priceEl.textContent = '₩' + d.close.toLocaleString();
    const chgEl = cell.querySelector('.stock-change, .treemap-change');
    if (chgEl) {
      const sign = d.change_pct >= 0 ? '+' : '';
      chgEl.textContent = `${sign}${d.change_pct.toFixed(2)}%`;
    }
    // 플래시 애니메이션
    cell.classList.remove('ws-flash-up', 'ws-flash-dn');
    void cell.offsetWidth; // reflow
    cell.classList.add(d.change_pct >= 0 ? 'ws-flash-up' : 'ws-flash-dn');
  });
}

// ── 차트 모달 실시간 연동 ──
function _wsSubscribeChart(code, market) {
  if (market === 'us') return; // US는 네이버 실시간 미지원
  WS.subscribe(code, (d) => {
    // 차트 헤더 가격 갱신
    const priceEl = document.getElementById('chart-price-current');
    if (priceEl) {
      priceEl.textContent = '₩' + d.close.toLocaleString();
      priceEl.style.color = d.change_pct >= 0 ? '#FF3333' : '#33AA33';
    }
    const chgEl = document.getElementById('chart-price-change');
    if (chgEl) {
      const sign = d.change_pct >= 0 ? '+' : '';
      chgEl.textContent = `${sign}${d.change_pct.toFixed(2)}%`;
      chgEl.style.color = d.change_pct >= 0 ? '#FF3333' : '#33AA33';
    }
    const timeEl = document.getElementById('chart-price-time');
    if (timeEl) {
      const now = new Date();
      timeEl.innerHTML = `<span class="ws-live-dot"></span>LIVE ${now.getHours()}:${String(now.getMinutes()).padStart(2,'0')}:${String(now.getSeconds()).padStart(2,'0')}`;
    }
  });
}

function _wsUnsubscribeChart(code) {
  WS.unsubscribe(code);
}
