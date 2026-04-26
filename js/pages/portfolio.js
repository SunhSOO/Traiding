/**
 * SUPERRICH - Portfolio Page
 */
function renderPortfolio(container) {
  const { account, positions } = MockData;
  const totalPnl = positions.reduce((s, p) => s + p.pnl, 0);
  const totalVolume = positions.reduce((s, p) => s + p.volume, 0);

  // Allocation data
  const allocationColors = ['#3b82f6', '#8b5cf6', '#06b6d4', '#10b981', '#f59e0b'];
  const allocations = positions.map((p, i) => ({
    symbol: p.symbol,
    pct: ((p.volume / totalVolume) * 100).toFixed(1),
    color: allocationColors[i % allocationColors.length],
    value: Math.abs(p.pnl) + p.volume * 1000
  }));

  container.innerHTML = `
    <!-- Header Cards -->
    <div class="portfolio-header-cards stagger-children">
      <div class="card animate-fade-in-up">
        <div class="stat-card-top">
          <span class="stat-card-label">Total Equity</span>
          <div class="card-icon card-icon-blue">${Utils.icon('layers')}</div>
        </div>
        <div class="stat-card-value">${Utils.formatMoney(account.equity)}</div>
        <div class="stat-card-footer">
          <span class="stat-change positive">${Utils.icon('trending-up')} ${Utils.formatPercent(account.dailyPnlPct)}</span>
        </div>
      </div>
      <div class="card animate-fade-in-up">
        <div class="stat-card-top">
          <span class="stat-card-label">Unrealized P&L</span>
          <div class="card-icon ${totalPnl >= 0 ? 'card-icon-green' : 'card-icon-red'}">${Utils.icon('activity')}</div>
        </div>
        <div class="stat-card-value ${totalPnl >= 0 ? 'text-profit' : 'text-loss'}">${Utils.formatMoney(totalPnl)}</div>
        <div class="stat-card-footer">
          <span class="text-xs text-muted">${positions.length} positions</span>
        </div>
      </div>
      <div class="card animate-fade-in-up">
        <div class="stat-card-top">
          <span class="stat-card-label">Margin Usage</span>
          <div class="card-icon card-icon-cyan">${Utils.icon('shield')}</div>
        </div>
        <div class="stat-card-value">${Utils.formatMoney(account.margin)}</div>
        <div class="margin-bar-wrapper">
          <div class="margin-bar">
            <div class="margin-fill green" style="width:${((account.margin / account.equity) * 100).toFixed(1)}%"></div>
          </div>
          <div class="margin-labels" style="margin-top:var(--space-1)">
            <span class="text-xs text-muted">Used: ${((account.margin / account.equity) * 100).toFixed(1)}%</span>
            <span class="text-xs text-muted">Free: ${Utils.formatMoney(account.freeMargin)}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Body -->
    <div class="portfolio-body">
      <!-- Allocation Chart -->
      <div class="card animate-fade-in-up">
        <div class="card-header">
          <div class="card-title">Asset Allocation</div>
        </div>
        <div class="donut-chart-wrapper">
          <svg viewBox="0 0 200 200" style="width:100%;height:100%;transform:rotate(-90deg)">
            ${(() => {
              let offset = 0;
              return allocations.map(a => {
                const pct = parseFloat(a.pct);
                const dashArray = (pct / 100) * (2 * Math.PI * 70);
                const gap = (2 * Math.PI * 70) - dashArray;
                const el = `<circle cx="100" cy="100" r="70" fill="none" stroke="${a.color}" stroke-width="25" 
                  stroke-dasharray="${dashArray} ${gap}" stroke-dashoffset="${-offset}" opacity="0.85"/>`;
                offset += dashArray;
                return el;
              }).join('');
            })()}
          </svg>
          <div class="donut-center-text">
            <div class="donut-center-value">${positions.length}</div>
            <div class="donut-center-label">Positions</div>
          </div>
        </div>
        <div class="allocation-legend">
          ${allocations.map(a => `
            <div class="legend-item">
              <div class="legend-dot" style="background:${a.color}"></div>
              <span class="legend-label">${a.symbol}</span>
              <span class="legend-value">${a.pct}%</span>
            </div>
          `).join('')}
        </div>
      </div>

      <!-- Position Details -->
      <div class="card animate-fade-in-up">
        <div class="card-header">
          <div class="card-title">Position Details</div>
          <div class="tabs">
            <div class="tab active">All</div>
            <div class="tab">Long</div>
            <div class="tab">Short</div>
          </div>
        </div>
        ${positions.map(p => `
          <div class="position-card">
            <div class="position-card-dir ${p.type === 'BUY' ? 'long' : 'short'}"></div>
            <div class="position-card-body">
              <div class="position-card-symbol">${p.symbol} <span class="badge ${p.type === 'BUY' ? 'badge-profit' : 'badge-loss'}" style="font-size:9px">${p.type}</span></div>
              <div class="position-card-meta">
                <span>${p.volume} lots</span>
                <span>Entry: ${p.openPrice}</span>
                <span>Current: ${p.currentPrice}</span>
                <span>${Utils.timeAgo(p.openTime)}</span>
              </div>
            </div>
            <div class="position-card-pnl">
              <div class="position-card-pnl-value ${p.pnl >= 0 ? 'text-profit' : 'text-loss'}">${Utils.formatMoney(p.pnl)}</div>
              <div class="position-card-pnl-pct ${p.pnlPct >= 0 ? 'text-profit' : 'text-loss'}">${Utils.formatPercent(p.pnlPct)}</div>
            </div>
          </div>
        `).join('')}
      </div>

      <!-- Risk Matrix -->
      <div class="card animate-fade-in-up">
        <div class="card-header">
          <div class="card-title">Risk Assessment</div>
        </div>
        <div class="risk-matrix">
          ${positions.map(p => {
            const riskLevel = Math.abs(p.pnlPct) < 0.2 ? 'low' : Math.abs(p.pnlPct) < 0.4 ? 'medium' : 'high';
            return `<div class="risk-cell risk-${riskLevel}">
              <div style="font-size:var(--text-sm);font-weight:600">${p.symbol}</div>
              <div style="font-size:var(--text-xs);margin-top:2px">${Math.abs(p.pnlPct).toFixed(1)}%</div>
            </div>`;
          }).join('')}
        </div>
        <div class="divider"></div>
        <div style="display:flex;gap:var(--space-4);justify-content:center">
          <div class="legend-item"><div class="legend-dot" style="background:rgba(16,185,129,0.3)"></div><span class="text-xs text-muted">Low Risk</span></div>
          <div class="legend-item"><div class="legend-dot" style="background:rgba(245,158,11,0.3)"></div><span class="text-xs text-muted">Medium</span></div>
          <div class="legend-item"><div class="legend-dot" style="background:rgba(239,68,68,0.3)"></div><span class="text-xs text-muted">High Risk</span></div>
        </div>
      </div>

      <!-- Correlation -->
      <div class="card animate-fade-in-up">
        <div class="card-header">
          <div class="card-title">Exposure Summary</div>
        </div>
        <div style="display:flex;flex-direction:column;gap:var(--space-3)">
          <div>
            <div style="display:flex;justify-content:space-between;margin-bottom:var(--space-1)">
              <span class="text-sm">Long Exposure</span>
              <span class="text-sm font-mono text-profit">${Utils.formatMoney(positions.filter(p => p.type === 'BUY').reduce((s, p) => s + p.pnl, 0))}</span>
            </div>
            <div class="progress-bar"><div class="progress-fill green" style="width:65%"></div></div>
          </div>
          <div>
            <div style="display:flex;justify-content:space-between;margin-bottom:var(--space-1)">
              <span class="text-sm">Short Exposure</span>
              <span class="text-sm font-mono text-loss">${Utils.formatMoney(positions.filter(p => p.type === 'SELL').reduce((s, p) => s + p.pnl, 0))}</span>
            </div>
            <div class="progress-bar"><div class="progress-fill red" style="width:35%"></div></div>
          </div>
          <div class="divider"></div>
          <div style="display:flex;justify-content:space-between">
            <span class="text-sm text-muted">Net Exposure</span>
            <span class="text-sm font-mono font-bold text-profit">Long Biased</span>
          </div>
        </div>
      </div>
    </div>
  `;
}

window.renderPortfolio = renderPortfolio;
