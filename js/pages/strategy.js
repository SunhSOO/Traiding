/**
 * SUPERRICH - Strategy Page
 */
function renderStrategy(container) {
  const { strategies, logs } = MockData;

  container.innerHTML = `
    <!-- Header -->
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:var(--space-5)">
      <div>
        <h2 style="margin-bottom:var(--space-1)">Strategy Manager</h2>
        <p class="text-sm text-muted">${strategies.filter(s => s.status === 'running').length} of ${strategies.length} strategies active</p>
      </div>
      <div style="display:flex;gap:var(--space-3)">
        <button class="btn btn-secondary">${Utils.icon('refresh-cw')} Sync All</button>
        <button class="btn btn-primary">${Utils.icon('zap')} New Strategy</button>
      </div>
    </div>

    <!-- Strategy Cards -->
    <div class="strategy-grid stagger-children">
      ${strategies.map(s => `
        <div class="card strategy-card animate-fade-in-up">
          <div class="strategy-card-status">
            <span class="badge ${s.status === 'running' ? 'badge-profit' : s.status === 'error' ? 'badge-loss' : 'badge-neutral'}">
              ${s.status === 'running' ? '● Running' : s.status === 'error' ? '● Error' : '○ Stopped'}
            </span>
          </div>
          <div class="strategy-card-header">
            <div class="strategy-card-icon" style="background:var(--bg-surface-hover);font-size:var(--text-xl)">${s.icon}</div>
            <div>
              <div class="strategy-card-name">${s.name}</div>
              <div class="strategy-card-type">${s.type}</div>
            </div>
          </div>
          <div class="strategy-card-metrics">
            <div class="strategy-metric">
              <div class="strategy-metric-value ${s.pnl >= 0 ? 'text-profit' : 'text-loss'}">${Utils.formatMoney(s.pnl)}</div>
              <div class="strategy-metric-label">P&L</div>
            </div>
            <div class="strategy-metric">
              <div class="strategy-metric-value">${s.winRate}%</div>
              <div class="strategy-metric-label">Win Rate</div>
            </div>
            <div class="strategy-metric">
              <div class="strategy-metric-value">${s.trades}</div>
              <div class="strategy-metric-label">Trades</div>
            </div>
          </div>
          <div style="margin-bottom:var(--space-3)">
            <div style="display:flex;justify-content:space-between;margin-bottom:var(--space-1)">
              <span class="text-xs text-muted">Max Drawdown</span>
              <span class="text-xs font-mono text-loss">${s.maxDD}%</span>
            </div>
            <div class="progress-bar">
              <div class="progress-fill red" style="width:${Math.abs(s.maxDD) * 10}%"></div>
            </div>
          </div>
          <div class="strategy-card-footer">
            <div class="strategy-card-symbols">
              <span class="pill pill-active">${s.symbol}</span>
              <span class="pill">${s.timeframe}</span>
            </div>
            <div class="strategy-card-controls">
              ${s.status === 'running' 
                ? `<button class="btn btn-ghost btn-icon" title="Pause">${Utils.icon('pause')}</button>
                   <button class="btn btn-danger btn-sm">${Utils.icon('stop-circle')} Stop</button>`
                : `<button class="btn btn-success btn-sm">${Utils.icon('play')} Start</button>`}
              <button class="btn btn-ghost btn-icon" title="Settings">${Utils.icon('settings')}</button>
            </div>
          </div>
        </div>
      `).join('')}
    </div>

    <!-- Bottom Section -->
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:var(--space-5);margin-top:var(--space-5)">
      <!-- Backtest Summary -->
      <div class="card animate-fade-in-up">
        <div class="card-header">
          <div class="card-title">Backtest Results — Trend Follower</div>
          <div class="tabs">
            <div class="tab active">Summary</div>
            <div class="tab">Equity Curve</div>
          </div>
        </div>
        <div class="backtest-summary">
          <div class="backtest-stat">
            <div class="backtest-stat-value text-profit">+28.4%</div>
            <div class="backtest-stat-label">Total Return</div>
          </div>
          <div class="backtest-stat">
            <div class="backtest-stat-value">1.85</div>
            <div class="backtest-stat-label">Profit Factor</div>
          </div>
          <div class="backtest-stat">
            <div class="backtest-stat-value">62%</div>
            <div class="backtest-stat-label">Win Rate</div>
          </div>
          <div class="backtest-stat">
            <div class="backtest-stat-value text-loss">-8.2%</div>
            <div class="backtest-stat-label">Max Drawdown</div>
          </div>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:var(--space-3)">
          <div class="system-item"><span class="system-item-label">Sharpe Ratio</span><span class="system-item-value">1.42</span></div>
          <div class="system-item"><span class="system-item-label">Avg Trade</span><span class="system-item-value text-profit">+$26.40</span></div>
          <div class="system-item"><span class="system-item-label">Avg Win</span><span class="system-item-value text-profit">+$68.50</span></div>
          <div class="system-item"><span class="system-item-label">Avg Loss</span><span class="system-item-value text-loss">-$42.30</span></div>
          <div class="system-item"><span class="system-item-label">Total Trades</span><span class="system-item-value">248</span></div>
          <div class="system-item"><span class="system-item-label">Recovery Factor</span><span class="system-item-value">3.46</span></div>
        </div>
      </div>

      <!-- Execution Log -->
      <div class="card animate-fade-in-up">
        <div class="card-header">
          <div class="card-title">Execution Log</div>
          <div style="display:flex;gap:var(--space-2)">
            <button class="btn btn-ghost btn-sm pill-active">All</button>
            <button class="btn btn-ghost btn-sm">Info</button>
            <button class="btn btn-ghost btn-sm">Warn</button>
            <button class="btn btn-ghost btn-sm">Error</button>
          </div>
        </div>
        <div class="strategy-log-panel">
          ${logs.map(l => `
            <div class="log-entry">
              <span class="log-time">${Utils.formatTime(l.time)}</span>
              <span class="log-level ${l.level}">[${l.level.toUpperCase()}]</span>
              <span class="log-message"><strong>${l.strategy}</strong> — ${l.message}</span>
            </div>
          `).join('')}
        </div>
      </div>
    </div>
  `;
}

window.renderStrategy = renderStrategy;
