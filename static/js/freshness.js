/* Step 4-5-1-D: 신선도/출처 배지 컴포넌트 (vanilla JS)
 *
 * API:
 *   freshnessBadge(label, opts?)        → <span> HTML
 *   sourceBadge(result, opts?)          → <span> HTML
 *   freshnessLabelText(label)           → 라벨 한글
 *   renderFreshness(targetEl, source)   → fetch → 자동 렌더
 *   refreshAllBadges(rootEl?)           → DOM 내 data-freshness 속성 모두 갱신
 *
 * 라벨 사양 (data_freshness.py와 일치):
 *   LIVE     🟢 green   — 임계값 이내
 *   DELAY    🟡 yellow  — 정상 범위 + 50%
 *   ARCHIVE  ⚪ gray    — 그 이상 (의도된 정적 데이터 포함)
 *   MANUAL   👤 blue    — 사용자 수동 입력 (라벨 미적용)
 *   NO_DATA  🔴 red     — 데이터 없음
 */

(function (global) {
  'use strict';

  const LABEL_META = {
    LIVE:    { emoji: '🟢', text: 'LIVE',    cls: 'frb-live'    },
    DELAY:   { emoji: '🟡', text: 'DELAY',   cls: 'frb-delay'   },
    ARCHIVE: { emoji: '⚪', text: 'ARCHIVE', cls: 'frb-archive' },
    MANUAL:  { emoji: '👤', text: 'MANUAL',  cls: 'frb-manual'  },
    NO_DATA: { emoji: '🔴', text: 'NO_DATA', cls: 'frb-nodata'  },
  };

  function _esc(s) {
    if (s == null) return '';
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  /** 라벨만으로 배지 HTML 생성. opts.compact = 점만, opts.title = tooltip */
  function freshnessBadge(label, opts) {
    opts = opts || {};
    const m = LABEL_META[label] || LABEL_META.NO_DATA;
    const compact = !!opts.compact;
    const title = opts.title ? ` title="${_esc(opts.title)}"` : '';
    const text = compact ? '' : ' ' + m.text;
    return `<span class="frb ${m.cls}"${title}>${m.emoji}${text}</span>`;
  }

  /** API 응답 객체 → 출처 배지 + 신선도 배지 합성 */
  function sourceBadge(result, opts) {
    opts = opts || {};
    if (!result || !result.source) {
      return '<span class="src-badge src-empty">[출처 미상]</span>';
    }
    const name = _esc(result.name_kr || result.source);
    const tsRaw = result.last_updated_kst || result.last_updated || '—';
    const ts = _esc(String(tsRaw).replace(' KST', ''));
    const tooltip = _esc(result.description || result.source);
    const showFresh = opts.withFreshness !== false;
    const fresh = showFresh
      ? ' ' + freshnessBadge(result.label, { compact: true, title: result.age_human })
      : '';
    return `<span class="src-badge" title="${tooltip}">`
      + `<span class="src-name">${name}</span>`
      + `<span class="src-sep"> · </span>`
      + `<span class="src-time">${ts}</span>`
      + fresh
      + `</span>`;
  }

  function freshnessLabelText(label) {
    return (LABEL_META[label] || LABEL_META.NO_DATA).text;
  }

  /** API에서 단일 소스 fetch → targetEl.innerHTML로 렌더.
   *  targetEl이 string이면 querySelector로 처리.  */
  async function renderFreshness(targetEl, source, opts) {
    const el = (typeof targetEl === 'string')
      ? document.querySelector(targetEl) : targetEl;
    if (!el) return null;
    el.innerHTML = '<span class="src-badge src-loading">…</span>';
    try {
      const r = await fetch(`/api/freshness/source/${encodeURIComponent(source)}`);
      if (!r.ok) {
        el.innerHTML = `<span class="src-badge src-error">출처 조회 실패 (${r.status})</span>`;
        return null;
      }
      const data = await r.json();
      el.innerHTML = sourceBadge(data, opts);
      return data;
    } catch (e) {
      el.innerHTML = `<span class="src-badge src-error">네트워크 오류</span>`;
      return null;
    }
  }

  /** DOM 내부의 모든 <span data-freshness="<source>"> 자동 렌더. */
  async function refreshAllBadges(rootEl) {
    const root = rootEl || document;
    const els = root.querySelectorAll('[data-freshness]');
    if (!els.length) return 0;
    // /api/freshness/all 한 번 호출 → 매핑
    let map = {};
    try {
      const r = await fetch('/api/freshness/all');
      if (r.ok) {
        const j = await r.json();
        for (const s of (j.sources || [])) map[s.source] = s;
      }
    } catch (e) { /* 무시 — 개별 fetch 폴백 */ }

    let n = 0;
    for (const el of els) {
      const src = el.getAttribute('data-freshness');
      if (!src) continue;
      const opts = {
        withFreshness: el.getAttribute('data-show-fresh') !== 'false',
        compact: el.hasAttribute('data-compact'),
      };
      if (map[src]) {
        if (opts.compact && !el.hasAttribute('data-source-name')) {
          el.innerHTML = freshnessBadge(map[src].label, {
            compact: true, title: map[src].age_human || ''
          });
        } else {
          el.innerHTML = sourceBadge(map[src], opts);
        }
        n++;
      } else {
        await renderFreshness(el, src, opts);
        n++;
      }
    }
    return n;
  }

  // 글로벌 등록
  global.freshnessBadge = freshnessBadge;
  global.sourceBadge = sourceBadge;
  global.freshnessLabelText = freshnessLabelText;
  global.renderFreshness = renderFreshness;
  global.refreshAllBadges = refreshAllBadges;
  global.FRESHNESS_LABEL_META = LABEL_META;
})(window);
