/**
 * Signal scan page — pre-trade preview.
 *
 * Runs the decision engine in dry-run mode and shows what BUY / SELL
 * signals are about to fire. KR/US tab-aware. Knobs let the operator
 * preview alternative thresholds without committing.
 */

function renderScan(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h2 class="text-xl">시그널 스캔</h2>
        <p class="text-sm text-muted">현재 가중치 + 최신 점수로 결정 엔진을 dry-run합니다 (실행되지 않음)</p>
      </div>
      <div class="page-header-actions" id="scan-header-actions"></div>
    </div>

    <div class="card scan-controls">
      <div class="bt-control-row">
        <div class="form-row">
          <label>BUY 임계값</label>
          <input type="number" class="input" id="scan-buy" value="25" step="0.5">
        </div>
        <div class="form-row">
          <label>SELL 임계값</label>
          <input type="number" class="input" id="scan-sell" value="-25" step="0.5">
        </div>
        <div class="form-row">
          <label>최소 confidence</label>
          <input type="number" class="input" id="scan-conf" value="0.40" min="0" max="1" step="0.05">
        </div>
        <div class="form-row">
          <label>액션 필터</label>
          <select class="input" id="scan-action">
            <option value="">전체</option>
            <option value="BUY">BUY</option>
            <option value="SELL">SELL</option>
            <option value="HOLD">HOLD</option>
          </select>
        </div>
        <div class="form-row">
          <label>
            <input type="checkbox" id="scan-learned" checked>
            학습된 가중치 사용
          </label>
        </div>
        <div class="form-row bt-run-cell">
          <label>&nbsp;</label>
          <button class="btn btn-primary" id="scan-run">${Utils.icon('zap')} 스캔</button>
        </div>
      </div>
    </div>

    <div class="dashboard-stats stagger-children" id="scan-summary">
      <div class="card stat-card text-muted">스캔 대기</div>
    </div>

    <div class="card">
      <div class="card-header">
        <div class="card-title" id="scan-result-title">결과</div>
      </div>
      <div id="scan-result-pane"><div class="empty-state text-muted">위에서 "스캔" 버튼을 눌러주세요.</div></div>
    </div>
  `;

  let currentMarket = AppState.getMarket();
  MarketTab.mount({
    parent: container.querySelector('#scan-header-actions'),
    onChange: (m) => { currentMarket = m; runScan(); },
  });

  container.querySelector('#scan-run').addEventListener('click', runScan);

  // Auto-run on first mount
  runScan();

  async function runScan() {
    const pane = container.querySelector('#scan-result-pane');
    const sumPane = container.querySelector('#scan-summary');
    const title = container.querySelector('#scan-result-title');
    pane.innerHTML = `<div class="empty-state text-muted">스캔 중…</div>`;
    sumPane.innerHTML = `<div class="card stat-card text-muted">계산 중…</div>`;
    const btn = container.querySelector('#scan-run');
    btn.disabled = true;
    try {
      const data = await Api.scanPreview({
        market: currentMarket,
        buyThreshold: Number(container.querySelector('#scan-buy').value),
        sellThreshold: Number(container.querySelector('#scan-sell').value),
        minOverallConfidence: Number(container.querySelector('#scan-conf').value),
        useLearnedWeights: container.querySelector('#scan-learned').checked,
        actionFilter: container.querySelector('#scan-action').value || null,
      });
      title.textContent = `결과 (as-of ${Utils.formatDateTime(data.as_of)})`;
      renderSummary(sumPane, data.summary, data.market);
      renderRows(pane, data.rows);
    } catch (err) {
      pane.innerHTML = `<div class="empty-state text-loss">${escape(err.message)}</div>`;
      sumPane.innerHTML = '';
    } finally {
      btn.disabled = false;
    }
  }

  function renderSummary(pane, s, market) {
    pane.innerHTML = `
      ${statCardS('BUY 후보', s.buy_count, market || '전체', 'green')}
      ${statCardS('SELL 후보', s.sell_count, market || '전체', 'red')}
      ${statCardS('HOLD', s.hold_count, '임계값 안 또는 confidence 부족', 'blue')}
      ${statCardS('시그널 없음', s.no_signal_count, '모듈 스코어 결손/stale', 'purple')}
    `;
  }

  function renderRows(pane, rows) {
    if (!rows.length) {
      pane.innerHTML = `<div class="empty-state text-muted">조건에 맞는 종목이 없습니다.</div>`;
      return;
    }
    pane.innerHTML = `
      <div style="overflow-x:auto">
        <table class="data-table">
          <thead>
            <tr>
              <th>액션</th><th>종목</th><th>composite</th>
              <th>conf</th><th>F</th><th>T</th><th>I</th>
              <th>클러스터</th><th>최신성</th><th>이유</th><th></th>
            </tr>
          </thead>
          <tbody>
            ${rows.map(r => `
              <tr class="scan-row ${r.action.toLowerCase()}">
                <td><span class="badge ${actBadge(r.action)}">${r.action}</span></td>
                <td>
                  <strong>${escape(r.market)}:${escape(r.ticker)}</strong>
                  <div class="text-xs text-muted">${escape(r.name || '')}</div>
                </td>
                <td class="mono ${r.composite_score >= 0 ? 'text-profit' : 'text-loss'}">
                  ${r.composite_score >= 0 ? '+' : ''}${r.composite_score.toFixed(1)}
                </td>
                <td class="mono text-xs">${(r.composite_confidence * 100).toFixed(0)}%</td>
                ${moduleCell(r.fundamental_score)}
                ${moduleCell(r.technical_score)}
                ${moduleCell(r.information_score)}
                <td class="text-xs">${r.cluster_id ? escape(r.cluster_id) : '<span class="text-muted">—</span>'}</td>
                <td class="text-xs ${staleClass(r.stalest_age_hours)}">
                  ${r.stalest_module ? `${escape(r.stalest_module)} ${(r.stalest_age_hours || 0).toFixed(0)}h` : '—'}
                </td>
                <td class="text-xs text-muted">${escape(r.reason)}</td>
                <td>
                  <a class="btn btn-ghost btn-sm" href="#${escape(AppRouter.pathWithQuery('/analysis', { market: r.market, ticker: r.ticker }))}">
                    ${Utils.icon('search')}
                  </a>
                </td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
      <div class="text-xs text-muted" style="margin-top:8px">총 ${rows.length}개 표시됨</div>
    `;
  }

  function moduleCell(v) {
    if (v == null) return '<td class="text-xs text-muted">—</td>';
    const cls = v >= 0 ? 'text-profit' : 'text-loss';
    return `<td class="mono ${cls}">${v >= 0 ? '+' : ''}${v.toFixed(0)}</td>`;
  }

  function staleClass(h) {
    if (h == null) return 'text-muted';
    if (h > 48) return 'text-loss';
    if (h > 24) return 'text-warn';
    return 'text-muted';
  }

  function actBadge(a) {
    if (a === 'BUY') return 'badge-profit';
    if (a === 'SELL') return 'badge-loss';
    return 'badge-neutral';
  }
}

function statCardS(label, value, footer, glow) {
  return `
    <div class="card stat-card animate-fade-in-up">
      <div class="stat-glow ${glow}"></div>
      <div class="stat-card-top">
        <span class="stat-card-label">${label}</span>
      </div>
      <div class="stat-card-value">${value}</div>
      <div class="stat-card-footer"><span class="text-xs text-muted">${footer}</span></div>
    </div>
  `;
}

function escape(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

window.renderScan = renderScan;
