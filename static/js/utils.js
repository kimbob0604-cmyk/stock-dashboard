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
// 로그인 — 쓰기 요청과 개인 데이터(매매일지·분석일지·알림·포트폴리오), 운영
// 경로는 서버가 인증을 요구한다. 운영 토큰(Render 환경변수 OPS_TOKEN)을 한 번
// 입력하면 서버가 HttpOnly 쿠키를 준다(쓸 때마다 30일 연장). 토큰 원문은 이
// 브라우저에 남기지 않는다.
//
// fetch 를 감싸서 /api/ 응답이 401 + X-Auth-Required 이면 로그인 창을 띄우고
// 한 번 다시 보낸다 — 호출부는 평소처럼 fetch 를 쓰면 된다. 사람이 누르지 않았는데
// 나가는 요청은 init.authPrompt 로 창을 띄우지 않게 한다.
//   authPrompt: false  — 창을 띄우지 않는다 (자동 갱신 타이머·자동 저장)
//   authPrompt: 'once' — 이 페이지에서 한 번 취소했으면 다시 묻지 않는다 (서버 동기화)
// ─────────────────────────────────────────────────────────────────────────────
// 예전 방식(토큰 원문을 localStorage 에 저장)의 흔적을 지운다
try { localStorage.removeItem('ops_token'); } catch {}

const _nativeFetch = window.fetch.bind(window);
let _loginPromise = null;
let _loginDeclined = false;

function _isOwnApi(input) {
  try {
    const raw = typeof input === 'string' ? input
              : (input instanceof URL ? input.href : input.url);
    const u = new URL(raw, location.href);
    return u.origin === location.origin && u.pathname.startsWith('/api/');
  } catch { return false; }
}

window.fetch = async function (input, init) {
  // Request 객체는 본문을 한 번만 읽을 수 있어 재시도용으로 복제해 둔다
  const retryInput = (input instanceof Request) ? input.clone() : input;
  const r = await _nativeFetch(input, init);
  if (r.status !== 401 || !r.headers.get('X-Auth-Required') || !_isOwnApi(input)) return r;
  const mode = init && init.authPrompt;
  if (mode === false || (mode === 'once' && _loginDeclined)) return r;
  if (!(await ensureLogin())) return r;
  return _nativeFetch(retryInput, init);
};

// 로그인 창. 동시에 여러 요청이 401 을 받아도 창은 하나 — 모두 같은 결과를 기다린다.
function ensureLogin() {
  if (!_loginPromise) {
    _loginPromise = _showLoginDialog().finally(() => { _loginPromise = null; });
  }
  return _loginPromise;
}

function _showLoginDialog() {
  return new Promise(resolve => {
    const ov = document.createElement('div');
    ov.className = 'pf-modal-overlay';
    ov.style.zIndex = '20000';   // 설정 모달 위에서도 보이게
    ov.innerHTML = `<form class="pf-modal" style="max-width:360px;width:100%" autocomplete="on">
      <div style="font-size:15px;font-weight:700;margin-bottom:6px">🔐 로그인</div>
      <div style="font-size:12px;color:var(--text-muted);margin-bottom:12px">
        운영 토큰(OPS_TOKEN)을 입력하세요. 이 브라우저는 30일 동안 로그인 상태로 남습니다.</div>
      <input type="password" class="bt-input" name="token" autocomplete="current-password"
             placeholder="OPS_TOKEN" style="width:100%;box-sizing:border-box" required>
      <div class="login-err" style="color:#FF3333;font-size:12px;min-height:16px;margin:8px 0"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <button type="button" class="sm-cancel-btn" data-act="cancel">취소</button>
        <button type="submit" class="pj-add-btn">로그인</button>
      </div>
    </form>`;
    const form = ov.querySelector('form');
    const input = ov.querySelector('input');
    const err = ov.querySelector('.login-err');
    const done = ok => {
      if (!ok) _loginDeclined = true;
      document.removeEventListener('keydown', onKey, true);
      ov.remove();
      resolve(ok);
    };
    const onKey = e => { if (e.key === 'Escape') { e.stopPropagation(); done(false); } };
    document.addEventListener('keydown', onKey, true);
    ov.querySelector('[data-act=cancel]').addEventListener('click', () => done(false));
    form.addEventListener('submit', async e => {
      e.preventDefault();
      err.textContent = '확인 중…';
      try {
        const r = await _nativeFetch('/api/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token: input.value }),
        });
        if (r.ok) { _loginDeclined = false; done(true); return; }
        const d = await r.json().catch(() => ({}));
        err.textContent = d.error || ('HTTP ' + r.status);
        input.select();
      } catch (ex) {
        err.textContent = '네트워크 오류: ' + ex.message;
      }
    });
    document.body.appendChild(ov);
    input.focus();
  });
}

async function logout() {
  await _nativeFetch('/api/auth/logout', { method: 'POST' }).catch(() => {});
}

// 인증 실패면 사람이 읽을 문구, 아니면 null
function opsAuthError(r) {
  if (r.status === 401) return '로그인이 필요합니다 — 운영 토큰을 입력해야 실행됩니다';
  if (r.status === 403) return '서버가 거절했습니다 (OPS_TOKEN 미설정이거나 다른 출처의 요청)';
  if (r.status === 429) return '로그인 실패가 너무 많습니다 — 10분 뒤 다시 시도하세요';
  return null;
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

