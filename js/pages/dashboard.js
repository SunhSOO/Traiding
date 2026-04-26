/**
 * SUPERRICH - Dashboard Page
 */
function renderDashboard(container) {
  const { account, positions, marketData, strategies, recentTrades, logs } = MockData;
  const totalPnl = positions.reduce((sum, p) => sum + p.pnl, 0);

  container.innerHTML = `
    <!-- Stats Row -->
    <div class="dashboard-stats stagger-children">
      <div class="card stat-card animate-fade-in-up">
        <div class="stat-glow blue"></div>
        <div class="stat-card-top">
          <span class="stat-card-label">Balance</span>
          <div class="card-icon card-icon-blue">${Utils.icon('dollar-sign')}</div>
        </div>
        <div class="stat-card-value">${Utils.formatMoney(account.balance)}</div>
        <div class="stat-card-footer">
          <span class="stat-change positive">
            ${Utils.icon('trending-up')} ${Utils.formatPercent(account.dailyPnlPct)}
          </span>
          <span class="text-xs text-muted">vs yesterday</span>
        </div>
      </div>

      <div class="card stat-card animate-fade-in-up">
        <div class="stat-glow purple"></div>
        <div class="stat-card-top">
          <span class="stat-card-label">Equity</span>
          <div class="card-icon card-icon-purple">${Utils.icon('activity')}</div>
        </div>
        <div class="stat-card-value">${Utils.formatMoney(account.equity)}</div>
        <div class="stat-card-footer">
          <span class="stat-change positive">
            ${Utils.icon('trending-up')} ${Utils.formatMoney(totalPnl)}
          </span>
          <span class="text-xs text-muted">unrealized</span>
        </div>
      </div>

      <div class="card stat-card animate-fade-in-up">
        <div class="stat-glow green"></div>
        <div class="stat-card-top">
          <span class="stat-card-label">Daily P&L</span>
          <div class="card-icon card-icon-green">${Utils.icon('trending-up')}</div>
        </div>
        <div class="stat-card-value text-profit">${Utils.formatMoney(account.dailyPnl)}</div>
        <div class="stat-card-footer">
          <span class="stat-change positive">
            ${Utils.icon('trending-up')} ${Utils.formatPercent(account.dailyPnlPct)}
          </span>
          <span class="text-xs text-muted">today</span>
        </div>
      </div>

      <div class="card stat-card animate-fade-in-up">
        <div class="stat-glow cyan"></div>
        <div class="stat-card-top">
          <span class="stat-card-label">Free Margin</span>
          <div class="card-icon card-icon-cyan">${Utils.icon('shield')}</div>
        </div>
        <div class="stat-card-value">${Utils.formatMoney(account.freeMargin)}</div>
        <div class="stat-card-footer">
          <span class="badge-info badge">${Utils.formatPercent(account.marginLevel, 0)} level</span>
        </div>
      </div>
    </div>

    <!-- Main Grid -->
    <div class="bento-grid bento-grid-4 stagger-children">
      <!-- Active Positions -->
      <div class="card col-span-2 animate-fade-in-up">
        <div class="card-header">
          <div>
            <div class="card-title">Active Positions</div>
            <div class="card-subtitle">${positions.length} open positions</div>
          </div>
          <span class="badge-info badge">${Utils.formatMoney(totalPnl)} total</span>
        </div>
        ${positions.map(p => `
          <div class="position-mini">
            <div class="position-dir ${p.type === 'BUY' ? 'buy' : 'sell'}"></div>
            <div class="position-mini-info">
              <div class="position-mini-symbol">${p.symbol} <span class="badge ${p.type === 'BUY' ? 'badge-profit' : 'badge-loss'}" style="font-size:9px">${p.type}</span></div>
              <div class="position-mini-detail">${p.volume} lots @ ${p.openPrice}</div>
            </div>
            <div class="position-mini-pnl ${p.pnl >= 0 ? 'text-profit' : 'text-loss'}">
              ${Utils.formatMoney(p.pnl)}<br>
              <span class="text-xs">${Utils.formatPercent(p.pnlPct)}</span>
            </div>
          </div>
        `).join('')}
      </div>

      <!-- Market Overview -->
      <div class="card col-span-2 animate-fade-in-up">
        <div class="card-header">
          <div>
            <div class="card-title">Market Overview</div>
            <div class="card-subtitle">Real-time quotes</div>
          </div>
          <button class="btn btn-ghost btn-sm">${Utils.icon('refresh-cw')} Refresh</button>
        </div>
        ${marketData.map(m => `
          <div class="market-ticker">
            <div class="ticker-symbol">
              <div class="ticker-icon">${m.symbol.slice(0, 2)}</div>
              <div>
                <div class="ticker-name">${m.symbol}</div>
                <div class="ticker-fullname">${m.name}</div>
              </div>
            </div>
            <div class="ticker-price">
              <div class="ticker-price-value font-mono">${m.bid.toFixed(m.symbol === 'NAS100' || m.symbol === 'US30' ? 1 : m.symbol === 'XAUUSD' ? 2 : 5)}</div>
              <div class="ticker-change ${m.change >= 0 ? 'text-profit' : 'text-loss'} font-mono">${m.change >= 0 ? '+' : ''}${m.change.toFixed(2)}%</div>
            </div>
          </div>
        `).join('')}
      </div>

      <!-- Strategy Status -->
      <div class="card col-span-2 animate-fade-in-up">
        <div class="card-header">
          <div>
            <div class="card-title">Strategy Status</div>
            <div class="card-subtitle">${strategies.filter(s => s.status === 'running').length} active</div>
          </div>
          <a href="#/strategy" class="btn btn-ghost btn-sm">View All</a>
        </div>
        ${strategies.map(s => `
          <div class="strategy-indicator">
            <div class="strategy-light ${s.status}"></div>
            <div class="strategy-info">
              <div class="strategy-name">${s.icon} ${s.name}</div>
              <div class="strategy-desc">${s.symbol} · ${s.timeframe} · ${s.trades} trades</div>
            </div>
            <div class="strategy-pnl ${s.pnl >= 0 ? 'text-profit' : 'text-loss'}">
              ${Utils.formatMoney(s.pnl)}
            </div>
          </div>
        `).join('')}
      </div>

      <!-- Recent Trades -->
      <div class="card animate-fade-in-up">
        <div class="card-header">
          <div>
            <div class="card-title">Recent Trades</div>
            <div class="card-subtitle">Last 5 trades</div>
          </div>
        </div>
        ${recentTrades.map(t => `
          <div class="trade-timeline-item">
            <div class="trade-timeline-dot ${t.type === 'BUY' ? 'buy' : 'sell'}"></div>
            <div class="trade-timeline-content">
              <div class="trade-timeline-header">
                <span class="trade-timeline-symbol">${t.symbol}</span>
                <span class="trade-timeline-time">${Utils.timeAgo(t.time)}</span>
              </div>
              <div class="trade-timeline-detail">
                ${t.type} ${t.volume} lots @ ${t.price}
                ${t.pnl !== null ? `<span class="${t.pnl >= 0 ? 'text-profit' : 'text-loss'} font-mono"> ${Utils.formatMoney(t.pnl)}</span>` : '<span class="badge-info badge" style="font-size:9px">OPEN</span>'}
              </div>
            </div>
          </div>
        `).join('')}
      </div>

      <!-- System Status -->
      <div class="card animate-fade-in-up">
        <div class="card-header">
          <div>
            <div class="card-title">System Status</div>
            <div class="card-subtitle">MT5 Connection</div>
          </div>
          <div class="status-dot connected"></div>
        </div>
        <div class="system-item">
          <span class="system-item-label">MT5 Server</span>
          <span class="system-item-value badge-profit badge">Connected</span>
        </div>
        <div class="system-item">
          <span class="system-item-label">Latency</span>
          <span class="system-item-value">12ms</span>
        </div>
        <div class="system-item">
          <span class="system-item-label">Server Time</span>
          <span class="system-item-value">${Utils.formatTime(new Date())}</span>
        </div>
        <div class="system-item">
          <span class="system-item-label">Leverage</span>
          <span class="system-item-value">1:${account.leverage}</span>
        </div>
        <div class="system-item">
          <span class="system-item-label">Margin Level</span>
          <span class="system-item-value text-profit">${account.marginLevel.toFixed(0)}%</span>
        </div>
        <div class="system-item">
          <span class="system-item-label">Active EAs</span>
          <span class="system-item-value">${strategies.filter(s => s.status === 'running').length}</span>
        </div>
        <div class="divider"></div>
        <div class="system-item">
          <span class="system-item-label">Last Update</span>
          <span class="system-item-value text-xs">Just now</span>
        </div>
      </div>

      <!-- Strategy Log -->
      <div class="card col-span-2 animate-fade-in-up">
        <div class="card-header">
          <div>
            <div class="card-title">Strategy Log</div>
            <div class="card-subtitle">Real-time execution log</div>
          </div>
          <button class="btn btn-ghost btn-sm">${Utils.icon('filter')} Filter</button>
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

  // Start live clock updates
  startDashboardUpdates();
}

function startDashboardUpdates() {
  if (window._dashboardInterval) clearInterval(window._dashboardInterval);
  window._dashboardInterval = setInterval(() => {
    const timeEls = document.querySelectorAll('.system-item-value');
    timeEls.forEach(el => {
      if (el.textContent.includes(':') && el.textContent.length === 8) {
        el.textContent = Utils.formatTime(new Date());
      }
    });
  }, 1000);
}

window.renderDashboard = renderDashboard;
