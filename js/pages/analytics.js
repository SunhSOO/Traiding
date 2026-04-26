/**
 * SUPERRICH - Analytics Page
 */
function renderAnalytics(container) {
  const dailyPnl = MockData.generateDailyPnl(30);
  const totalPnl = dailyPnl.reduce((s, d) => s + d.pnl, 0);
  const wins = dailyPnl.filter(d => d.pnl > 0).length;
  const losses = dailyPnl.filter(d => d.pnl <= 0).length;
  const avgWin = dailyPnl.filter(d => d.pnl > 0).reduce((s, d) => s + d.pnl, 0) / Math.max(wins, 1);
  const avgLoss = Math.abs(dailyPnl.filter(d => d.pnl <= 0).reduce((s, d) => s + d.pnl, 0)) / Math.max(losses, 1);
  const maxDD = -8.2;

  // Generate bar chart with CSS
  const maxAbsPnl = Math.max(...dailyPnl.map(d => Math.abs(d.pnl)));

  container.innerHTML = `
    <!-- KPI Row -->
    <div class="analytics-kpi-row stagger-children">
      <div class="card kpi-card animate-fade-in-up">
        <div class="kpi-value ${totalPnl >= 0 ? 'text-profit' : 'text-loss'}">${Utils.formatMoney(totalPnl)}</div>
        <div class="kpi-label">Net P&L (30d)</div>
        <div class="kpi-sub ${totalPnl >= 0 ? 'text-profit' : 'text-loss'}">${Utils.formatPercent(totalPnl / 528)}</div>
      </div>
      <div class="card kpi-card animate-fade-in-up">
        <div class="kpi-value">${((wins / dailyPnl.length) * 100).toFixed(0)}%</div>
        <div class="kpi-label">Win Rate</div>
        <div class="kpi-sub text-muted">${wins}W / ${losses}L</div>
      </div>
      <div class="card kpi-card animate-fade-in-up">
        <div class="kpi-value">${(avgWin / Math.max(avgLoss, 1)).toFixed(2)}</div>
        <div class="kpi-label">Risk/Reward</div>
        <div class="kpi-sub text-muted">Avg W: $${avgWin.toFixed(0)} / L: $${avgLoss.toFixed(0)}</div>
      </div>
      <div class="card kpi-card animate-fade-in-up">
        <div class="kpi-value text-loss">${maxDD}%</div>
        <div class="kpi-label">Max Drawdown</div>
        <div class="kpi-sub text-warning">Recovery: 3.46</div>
      </div>
      <div class="card kpi-card animate-fade-in-up">
        <div class="kpi-value">1.85</div>
        <div class="kpi-label">Profit Factor</div>
        <div class="kpi-sub text-profit">Above Target</div>
      </div>
    </div>

    <!-- Charts Row -->
    <div class="analytics-charts">
      <!-- Daily P&L Bar Chart -->
      <div class="card chart-card animate-fade-in-up">
        <div class="card-header">
          <div class="card-title">Daily P&L</div>
          <div class="tabs">
            <div class="tab active">Daily</div>
            <div class="tab">Weekly</div>
            <div class="tab">Monthly</div>
          </div>
        </div>
        <div style="display:flex;align-items:flex-end;gap:2px;height:200px;padding:var(--space-4) 0">
          ${dailyPnl.map(d => {
            const height = (Math.abs(d.pnl) / maxAbsPnl) * 100;
            const color = d.pnl >= 0 ? 'var(--profit)' : 'var(--loss)';
            return `<div class="tooltip" data-tooltip="${Utils.formatDate(d.date)}: ${Utils.formatMoney(d.pnl)}" style="flex:1;display:flex;flex-direction:column;justify-content:flex-end;height:100%">
              <div style="height:${height}%;background:${color};border-radius:2px 2px 0 0;min-height:2px;opacity:0.8;transition:opacity var(--transition-fast)" onmouseenter="this.style.opacity='1'" onmouseleave="this.style.opacity='0.8'"></div>
            </div>`;
          }).join('')}
        </div>
        <div style="display:flex;justify-content:space-between;font-size:var(--text-xs);color:var(--text-muted);padding:0 var(--space-2)">
          <span>${Utils.formatDate(dailyPnl[0].date)}</span>
          <span>${Utils.formatDate(dailyPnl[dailyPnl.length - 1].date)}</span>
        </div>
      </div>

      <!-- Win/Loss Distribution -->
      <div class="card animate-fade-in-up">
        <div class="card-header">
          <div class="card-title">Win/Loss Distribution</div>
        </div>
        <div style="display:flex;flex-direction:column;gap:var(--space-4);padding:var(--space-4) 0">
          <div>
            <div style="display:flex;justify-content:space-between;margin-bottom:var(--space-2)">
              <span class="text-sm text-profit">Winning Days</span>
              <span class="font-mono text-sm font-bold">${wins}</span>
            </div>
            <div class="progress-bar" style="height:12px">
              <div class="progress-fill green" style="width:${(wins/dailyPnl.length*100).toFixed(0)}%"></div>
            </div>
          </div>
          <div>
            <div style="display:flex;justify-content:space-between;margin-bottom:var(--space-2)">
              <span class="text-sm text-loss">Losing Days</span>
              <span class="font-mono text-sm font-bold">${losses}</span>
            </div>
            <div class="progress-bar" style="height:12px">
              <div class="progress-fill red" style="width:${(losses/dailyPnl.length*100).toFixed(0)}%"></div>
            </div>
          </div>
          <div class="divider"></div>
          <div class="system-item">
            <span class="system-item-label">Avg Winning Day</span>
            <span class="system-item-value text-profit">+${Utils.formatMoney(avgWin)}</span>
          </div>
          <div class="system-item">
            <span class="system-item-label">Avg Losing Day</span>
            <span class="system-item-value text-loss">-${Utils.formatMoney(avgLoss)}</span>
          </div>
          <div class="system-item">
            <span class="system-item-label">Best Day</span>
            <span class="system-item-value text-profit">${Utils.formatMoney(Math.max(...dailyPnl.map(d => d.pnl)))}</span>
          </div>
          <div class="system-item">
            <span class="system-item-label">Worst Day</span>
            <span class="system-item-value text-loss">${Utils.formatMoney(Math.min(...dailyPnl.map(d => d.pnl)))}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Bottom Row -->
    <div class="analytics-bottom">
      <!-- Performance by Symbol -->
      <div class="card animate-fade-in-up">
        <div class="card-header">
          <div class="card-title">Performance by Symbol</div>
        </div>
        <div class="perf-heatmap">
          ${[
            { symbol: 'EURUSD', pnl: 1245.80 },
            { symbol: 'XAUUSD', pnl: 892.30 },
            { symbol: 'USDJPY', pnl: -380.50 },
            { symbol: 'GBPUSD', pnl: 567.40 },
            { symbol: 'NAS100', pnl: -145.20 },
            { symbol: 'US30', pnl: 320.00 }
          ].map(s => `
            <div class="perf-cell" style="background:${s.pnl >= 0 ? `rgba(16,185,129,${Math.min(Math.abs(s.pnl)/1500, 0.3)})` : `rgba(239,68,68,${Math.min(Math.abs(s.pnl)/1500, 0.3)})`}">
              <div class="perf-cell-symbol">${s.symbol}</div>
              <div class="perf-cell-value ${s.pnl >= 0 ? 'text-profit' : 'text-loss'}">${Utils.formatMoney(s.pnl)}</div>
            </div>
          `).join('')}
        </div>
      </div>

      <!-- Monthly Calendar -->
      <div class="card animate-fade-in-up">
        <div class="card-header">
          <div class="card-title">April 2026 Calendar</div>
        </div>
        <div class="calendar-header">
          ${['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].map(d => `<div class="calendar-header-day">${d}</div>`).join('')}
        </div>
        <div class="calendar-heatmap">
          ${(() => {
            const days = [];
            // April 2026 starts on Wednesday (offset 2)
            for (let i = 0; i < 2; i++) days.push('<div class="calendar-day empty"></div>');
            for (let d = 1; d <= 26; d++) {
              const pnl = Utils.random(-500, 700);
              let cls = 'neutral';
              if (pnl > 300) cls = 'profit-3';
              else if (pnl > 100) cls = 'profit-2';
              else if (pnl > 0) cls = 'profit-1';
              else if (pnl > -100) cls = 'loss-1';
              else if (pnl > -300) cls = 'loss-2';
              else cls = 'loss-3';
              days.push(`<div class="calendar-day ${cls} tooltip" data-tooltip="Apr ${d}: ${Utils.formatMoney(pnl)}">${d}</div>`);
            }
            return days.join('');
          })()}
        </div>
        <div class="divider"></div>
        <div style="display:flex;gap:var(--space-3);justify-content:center;flex-wrap:wrap">
          <span class="text-xs text-muted">Loss</span>
          <div style="display:flex;gap:2px">
            <div style="width:14px;height:14px;border-radius:3px;background:rgba(239,68,68,0.5)"></div>
            <div style="width:14px;height:14px;border-radius:3px;background:rgba(239,68,68,0.3)"></div>
            <div style="width:14px;height:14px;border-radius:3px;background:rgba(239,68,68,0.15)"></div>
            <div style="width:14px;height:14px;border-radius:3px;background:var(--bg-surface-hover)"></div>
            <div style="width:14px;height:14px;border-radius:3px;background:rgba(16,185,129,0.15)"></div>
            <div style="width:14px;height:14px;border-radius:3px;background:rgba(16,185,129,0.3)"></div>
            <div style="width:14px;height:14px;border-radius:3px;background:rgba(16,185,129,0.5)"></div>
          </div>
          <span class="text-xs text-muted">Profit</span>
        </div>
      </div>
    </div>
  `;
}

window.renderAnalytics = renderAnalytics;
