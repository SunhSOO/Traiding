/**
 * Per-symbol analysis page.
 *
 * Entered as #/analysis?market=KR&ticker=005930 from the decision-
 * audit page or from search. If the URL has no ticker, shows a
 * ticker picker (loads the current market's universe).
 *
 * Renders:
 *  - Security card (name, sector, exchange, index membership)
 *  - Three score panels (F/T/I) with current value + sparkline
 *  - Recent decisions table (clickable → open audit modal)
 *  - Recent news mentions with their LLM classifications
 */

function renderAnalysis(container) {
  const q = AppRouter.getQuery();
  const initialMarket = q.market || AppState.getMarket();
  const initialTicker = q.ticker || '';

  container.innerHTML = `
    <div class="page-header">
      <div>
        <h2 class="text-xl">종목 분석</h2>
        <p class="text-sm text-muted">F/T/I 점수 추이 + 최근 결정 + 최근 뉴스 한눈에</p>
      </div>
      <div class="page-header-actions">
        <input class="input" id="ana-ticker-input"
               placeholder="티커 (005930, AAPL…)"
               value="${escape(initialTicker)}" style="width:200px">
        <button class="btn btn-primary btn-sm" id="ana-load-btn">조회</button>
      </div>
    </div>

    <div id="ana-pane">
      <div class="empty-state">
        <div class="text-muted">티커를 입력하고 조회를 눌러주세요.</div>
      </div>
    </div>
  `;

  const tickerInput = container.querySelector('#ana-ticker-input');
  const loadBtn = container.querySelector('#ana-load-btn');
  const pane = container.querySelector('#ana-pane');

  let currentMarket = initialMarket;

  loadBtn.addEventListener('click', load);
  tickerInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') load();
  });

  // Toggle market via global state (no in-page tab for this page — the
  // ticker IS the lookup key). Switching market just adjusts the
  // implicit default for the next blank lookup.
  window.addEventListener('state:market', (e) => {
    currentMarket = e.detail.market;
  });

  if (initialTicker) load();

  async function load() {
    const ticker = tickerInput.value.trim();
    if (!ticker) {
      pane.innerHTML = `<div class="empty-state text-muted">티커를 입력해주세요.</div>`;
      return;
    }
    // Heuristic: 6-digit numeric → KR, else US (user can override via global tab)
    const market = inferMarket(ticker) || currentMarket;
    AppState.setMarket(market);   // keep the global tab in sync

    pane.innerHTML = `<div class="empty-state text-muted">불러오는 중…</div>`;
    try {
      const data = await Api.analysisFor(market, ticker);
      pane.innerHTML = renderBody(data);
      wireDecisionLinks(pane, data.recent_decisions);
      wireOverlay(pane, market, ticker);
    } catch (err) {
      if (err.status === 404) {
        pane.innerHTML = `
          <div class="empty-state">
            <div class="text-loss">해당 종목을 찾지 못했습니다 (${market}:${escape(ticker)})</div>
            <div class="text-xs text-muted">universe 잡이 한 번 돌아야 등록됩니다.</div>
          </div>`;
      } else {
        pane.innerHTML = `
          <div class="empty-state">
            <div class="text-loss">불러오지 못했습니다: ${escape(err.message)}</div>
          </div>`;
      }
    }
  }
}

function inferMarket(ticker) {
  if (/^\d{6}$/.test(ticker)) return 'KR';
  if (/^[A-Za-z]{1,5}(\.[A-Za-z]{1,3})?$/.test(ticker)) return 'US';
  return null;
}

function renderBody(data) {
  const sec = data.security;
  return `
    <div class="ana-grid">
      <!-- Security card -->
      <div class="card ana-security-card">
        <div class="card-header">
          <div>
            <div class="text-xl"><strong>${sec.market}:${escape(sec.ticker)}</strong></div>
            <div class="text-sm text-muted">${escape(sec.name || '—')}</div>
          </div>
          <span class="badge ${sec.is_active ? 'badge-profit' : 'badge-loss'}">${sec.is_active ? '활성' : '상폐'}</span>
        </div>
        <div class="ana-meta-row">
          <div><span class="text-xs text-muted">거래소</span><div>${escape(sec.exchange || '—')}</div></div>
          <div><span class="text-xs text-muted">섹터</span><div>${escape(sec.sector || '—')}</div></div>
          <div><span class="text-xs text-muted">통화</span><div>${escape(sec.currency)}</div></div>
          <div><span class="text-xs text-muted">지수</span>
            <div>${data.memberships.length
              ? data.memberships.map(m => `<span class="badge badge-info" style="margin-right:4px">${escape(m.index_code)}</span>`).join('')
              : '—'}</div>
          </div>
        </div>
      </div>

      <!-- Three score panels -->
      <div class="ana-scores-row">
        ${scorePanel('기본 (F)', data.current_scores.F, data.score_history.F)}
        ${scorePanel('기술 (T)', data.current_scores.T, data.score_history.T)}
        ${scorePanel('정보 (I)', data.current_scores.I, data.score_history.I)}
      </div>

      <!-- Score overlay (longer history, all three modules + decisions) -->
      <div class="card ana-overlay-card">
        <div class="card-header">
          <div>
            <div class="card-title">점수 시계열 (F / T / I + 결정)</div>
            <div class="card-subtitle text-xs text-muted" id="ana-overlay-sub">불러오는 중…</div>
          </div>
          <div class="tabs" id="ana-overlay-tabs">
            <div class="tab" data-overlay-days="90">90D</div>
            <div class="tab active" data-overlay-days="365">1Y</div>
            <div class="tab" data-overlay-days="1095">3Y</div>
          </div>
        </div>
        <div id="ana-overlay-pane" data-market="${escape(sec.market)}" data-ticker="${escape(sec.ticker)}">
          <div class="empty-state text-muted">불러오는 중…</div>
        </div>
      </div>

      <!-- Recent decisions -->
      <div class="card">
        <div class="card-header">
          <div class="card-title">최근 결정</div>
          <span class="text-xs text-muted">${data.recent_decisions.length}건</span>
        </div>
        ${data.recent_decisions.length === 0
          ? '<div class="empty-state text-muted">아직 기록된 결정이 없습니다.</div>'
          : `<table class="data-table">
              <thead>
                <tr><th>시각</th><th>액션</th><th>composite</th><th>confidence</th></tr>
              </thead>
              <tbody>
                ${data.recent_decisions.map(d => `
                  <tr class="ana-decision-row" data-decision-id="${escape(d.id)}">
                    <td class="text-xs">${escape(Utils.formatDateTime(d.decision_ts))}</td>
                    <td><span class="badge ${actionBadgeClassAna(d.action)}">${d.action}</span></td>
                    <td class="mono ${(d.composite_score ?? 0) >= 0 ? 'text-profit' : 'text-loss'}">
                      ${d.composite_score == null ? '—' : d.composite_score.toFixed(1)}
                    </td>
                    <td class="mono">${d.composite_confidence == null ? '—' : (d.composite_confidence * 100).toFixed(0) + '%'}</td>
                  </tr>
                `).join('')}
              </tbody>
            </table>`}
      </div>

      <!-- Recent news -->
      <div class="card">
        <div class="card-header">
          <div class="card-title">최근 뉴스 (관련도순)</div>
          <span class="text-xs text-muted">${data.recent_news.length}건</span>
        </div>
        ${data.recent_news.length === 0
          ? '<div class="empty-state text-muted">아직 매핑된 뉴스가 없습니다.</div>'
          : `<div class="ana-news-list">
              ${data.recent_news.map(n => renderNewsCard(n)).join('')}
            </div>`}
      </div>
    </div>
  `;
}

function scorePanel(label, current, history) {
  const score = current.score;
  const conf = current.confidence;
  const sparkSvg = renderSparkline(history);
  return `
    <div class="card ana-score-panel">
      <div class="ana-score-label">${label}</div>
      <div class="ana-score-value ${(score ?? 0) >= 0 ? 'text-profit' : 'text-loss'}">
        ${score == null ? '—' : score.toFixed(1)}
      </div>
      <div class="ana-score-meta">
        ${conf == null ? '—' : `confidence ${(conf * 100).toFixed(0)}%`}
        ${current.computed_ts ? ` · ${Utils.timeAgo(current.computed_ts)}` : ''}
      </div>
      <div class="ana-spark">${sparkSvg}</div>
    </div>
  `;
}

function renderSparkline(history) {
  if (!history || history.length < 2) {
    return `<div class="text-xs text-muted">히스토리 부족</div>`;
  }
  const w = 240, h = 50, pad = 4;
  const xs = history.map(p => new Date(p.ts).getTime());
  const ys = history.map(p => p.score);
  const xMin = xs[0], xMax = xs[xs.length - 1];
  const yMin = Math.min(...ys), yMax = Math.max(...ys);
  const yRange = (yMax - yMin) || 1;
  const xRange = (xMax - xMin) || 1;
  const px = (x) => pad + (x - xMin) / xRange * (w - 2 * pad);
  const py = (y) => h - pad - (y - yMin) / yRange * (h - 2 * pad);
  const points = history.map(p => `${px(new Date(p.ts).getTime()).toFixed(1)},${py(p.score).toFixed(1)}`).join(' ');
  const lastY = py(ys[ys.length - 1]);
  const color = ys[ys.length - 1] >= 0 ? 'var(--profit)' : 'var(--loss)';
  // Zero-line
  const zeroY = (yMin <= 0 && yMax >= 0) ? py(0) : null;
  return `
    <svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" preserveAspectRatio="none">
      ${zeroY != null ? `<line x1="0" x2="${w}" y1="${zeroY}" y2="${zeroY}" stroke="var(--border)" stroke-dasharray="2,3"/>` : ''}
      <polyline fill="none" stroke="${color}" stroke-width="1.6" points="${points}"/>
      <circle cx="${px(xs[xs.length - 1]).toFixed(1)}" cy="${lastY.toFixed(1)}" r="2.5" fill="${color}"/>
    </svg>
  `;
}

function renderNewsCard(n) {
  const cls = n.classification;
  const clsBadge = cls
    ? `<span class="badge ${sentimentBadgeAna(cls.sentiment)}">${cls.event_type} · ${cls.sentiment} · ${cls.impact}</span>`
    : `<span class="badge badge-neutral text-xs">미분류</span>`;
  return `
    <a class="ana-news-card" ${n.url ? `href="${escape(n.url)}" target="_blank" rel="noopener"` : ''}>
      <div class="ana-news-head">
        <span class="text-xs text-muted">${escape(Utils.formatDateTime(n.published_ts))}</span>
        <span class="text-xs text-muted">${escape(n.publisher || n.source)}</span>
        <span class="text-xs text-muted">관련도 ${(n.relevance * 100).toFixed(0)}%</span>
      </div>
      <div class="ana-news-title">${escape(n.title)}</div>
      <div class="ana-news-foot">${clsBadge}</div>
    </a>
  `;
}

function actionBadgeClassAna(action) {
  switch (action) {
    case 'BUY': return 'badge-profit';
    case 'SELL': return 'badge-loss';
    case 'REJECTED': return 'badge-loss';
    case 'HOLD': return 'badge-neutral';
    default: return 'badge-info';
  }
}

function sentimentBadgeAna(s) {
  switch (s) {
    case 'POSITIVE': return 'badge-profit';
    case 'NEGATIVE': return 'badge-loss';
    default: return 'badge-neutral';
  }
}

function wireDecisionLinks(root, decisions) {
  root.querySelectorAll('.ana-decision-row').forEach(tr => {
    tr.addEventListener('click', () => {
      // Reuse the decision audit modal by navigating with a query
      AppRouter.navigate(AppRouter.pathWithQuery('/decisions', { open: tr.dataset.decisionId }));
    });
    tr.style.cursor = 'pointer';
  });
}

function wireOverlay(root, market, ticker) {
  const pane = root.querySelector('#ana-overlay-pane');
  const sub = root.querySelector('#ana-overlay-sub');
  const tabsEl = root.querySelector('#ana-overlay-tabs');
  if (!pane || !tabsEl) return;

  let currentDays = 365;

  tabsEl.querySelectorAll('.tab').forEach(t => {
    t.addEventListener('click', () => {
      tabsEl.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
      t.classList.add('active');
      currentDays = Number(t.dataset.overlayDays);
      load();
    });
  });

  load();

  async function load() {
    pane.innerHTML = `<div class="empty-state text-muted">불러오는 중…</div>`;
    try {
      const data = await Api.analysisScoreHistory(market, ticker, { days: currentDays });
      const totalPoints = (data.series.F?.length || 0)
                        + (data.series.T?.length || 0)
                        + (data.series.I?.length || 0);
      sub.textContent = `${currentDays}일 · ${totalPoints} 포인트 · 결정 ${data.decision_markers.length}건`;
      if (totalPoints === 0) {
        pane.innerHTML = `<div class="empty-state text-muted">이 기간에 기록된 점수가 없습니다.</div>`;
        return;
      }
      pane.innerHTML = renderOverlayChart(data);
    } catch (err) {
      pane.innerHTML = `<div class="empty-state text-loss">${escape(err.message)}</div>`;
    }
  }
}

/** Stacked overlay chart: three score series + decision markers.
 *  - x-axis = time (linear by ms)
 *  - y-axis = score [-100..+100], zero centred
 *  - decision markers drawn as small triangles along the bottom rail
 */
function renderOverlayChart(data) {
  const W = 900, H = 320, PAD_L = 36, PAD_R = 12, PAD_T = 16, PAD_B = 28;
  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;

  // Compute time range
  const all = [...data.series.F, ...data.series.T, ...data.series.I];
  if (all.length === 0) return '';
  const xs = all.map(p => new Date(p.ts).getTime());
  const tMin = Math.min(...xs);
  const tMax = Math.max(...xs);
  const tRange = (tMax - tMin) || 1;

  // Score axis fixed to -100..+100 (the score domain)
  const yMin = -100, yMax = 100;
  const scaleX = (t) => PAD_L + innerW * (t - tMin) / tRange;
  const scaleY = (s) => PAD_T + innerH * (1 - (s - yMin) / (yMax - yMin));

  function pathFor(series) {
    if (!series.length) return '';
    return series.map((p, i) => {
      const x = scaleX(new Date(p.ts).getTime());
      const y = scaleY(p.score);
      return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`;
    }).join(' ');
  }

  // Reference lines
  const zeroY = scaleY(0);
  const yLabels = [-100, -50, 0, 50, 100].map(v => {
    const y = scaleY(v);
    return `
      <line x1="${PAD_L}" x2="${W - PAD_R}" y1="${y.toFixed(1)}" y2="${y.toFixed(1)}"
            class="overlay-gridline ${v === 0 ? 'overlay-zero' : ''}"/>
      <text x="${PAD_L - 6}" y="${(y + 3).toFixed(1)}" class="overlay-ylabel" text-anchor="end">${v}</text>
    `;
  }).join('');

  // X-axis ticks — first, mid, last
  const xTickIdxs = [tMin, (tMin + tMax) / 2, tMax];
  const xLabels = xTickIdxs.map(t => {
    const d = new Date(t).toISOString().slice(0, 10);
    return `<text x="${scaleX(t).toFixed(1)}" y="${H - 8}" class="overlay-xlabel" text-anchor="middle">${d}</text>`;
  }).join('');

  // Decision markers along the bottom rail
  const markerY = H - PAD_B + 4;
  const markers = data.decision_markers.map(m => {
    const x = scaleX(new Date(m.ts).getTime());
    const cls = m.action === 'BUY'  ? 'overlay-marker-buy'
              : m.action === 'SELL' ? 'overlay-marker-sell'
              : 'overlay-marker-other';
    return `<polygon points="${x.toFixed(1)},${markerY - 5} ${(x - 4).toFixed(1)},${markerY + 4} ${(x + 4).toFixed(1)},${markerY + 4}"
            class="${cls}"><title>${m.action} @ ${m.ts}${m.composite_score != null ? ` · ${m.composite_score.toFixed(1)}` : ''}</title></polygon>`;
  }).join('');

  return `
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" class="overlay-chart">
      ${yLabels}
      <path d="${pathFor(data.series.F)}" class="overlay-line overlay-line-f" fill="none"/>
      <path d="${pathFor(data.series.T)}" class="overlay-line overlay-line-t" fill="none"/>
      <path d="${pathFor(data.series.I)}" class="overlay-line overlay-line-i" fill="none"/>
      ${markers}
      ${xLabels}
    </svg>
    <div class="overlay-legend">
      <span class="overlay-legend-item"><span class="dot dot-f"></span>F (기본)</span>
      <span class="overlay-legend-item"><span class="dot dot-t"></span>T (기술)</span>
      <span class="overlay-legend-item"><span class="dot dot-i"></span>I (정보)</span>
      <span class="overlay-legend-item"><span class="tri tri-buy"></span>BUY</span>
      <span class="overlay-legend-item"><span class="tri tri-sell"></span>SELL</span>
    </div>
  `;
}

function escape(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

window.renderAnalysis = renderAnalysis;
