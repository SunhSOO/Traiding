/**
 * SUPERRICH - History Page
 */
function renderHistory(container) {
  const history = MockData.generateHistory(100);
  const pageSize = 15;
  let currentPage = 1;
  const totalPages = Math.ceil(history.length / pageSize);

  function renderTable() {
    const start = (currentPage - 1) * pageSize;
    const pageData = history.slice(start, start + pageSize);
    const totalPnl = history.reduce((s, t) => s + t.pnl, 0);
    const wins = history.filter(t => t.pnl > 0).length;
    
    return `
      <!-- Quick Stats -->
      <div class="dashboard-stats" style="grid-template-columns:repeat(4,1fr);margin-bottom:var(--space-5)">
        <div class="card">
          <div class="stat-card-label">Total Trades</div>
          <div class="stat-card-value" style="font-size:var(--text-xl)">${history.length}</div>
        </div>
        <div class="card">
          <div class="stat-card-label">Net P&L</div>
          <div class="stat-card-value ${totalPnl >= 0 ? 'text-profit' : 'text-loss'}" style="font-size:var(--text-xl)">${Utils.formatMoney(totalPnl)}</div>
        </div>
        <div class="card">
          <div class="stat-card-label">Win Rate</div>
          <div class="stat-card-value" style="font-size:var(--text-xl)">${((wins/history.length)*100).toFixed(1)}%</div>
        </div>
        <div class="card">
          <div class="stat-card-label">Profit Factor</div>
          <div class="stat-card-value" style="font-size:var(--text-xl)">${(history.filter(t=>t.pnl>0).reduce((s,t)=>s+t.pnl,0) / Math.abs(history.filter(t=>t.pnl<0).reduce((s,t)=>s+t.pnl,0))).toFixed(2)}</div>
        </div>
      </div>

      <!-- Toolbar -->
      <div class="history-toolbar">
        <div class="history-filters">
          <div class="date-range-picker">
            <input type="date" value="2026-03-27">
            <span class="date-separator">→</span>
            <input type="date" value="2026-04-26">
          </div>
          <select class="select">
            <option>All Symbols</option>
            <option>EURUSD</option>
            <option>XAUUSD</option>
            <option>USDJPY</option>
            <option>GBPUSD</option>
            <option>NAS100</option>
          </select>
          <select class="select">
            <option>All Strategies</option>
            <option>Trend Follower</option>
            <option>Gold Scalper</option>
            <option>Mean Reversion</option>
            <option>Manual</option>
          </select>
          <select class="select">
            <option>All Types</option>
            <option>BUY</option>
            <option>SELL</option>
          </select>
        </div>
        <div class="history-actions">
          <button class="btn btn-secondary btn-sm">${Utils.icon('filter')} Apply</button>
          <button class="btn btn-secondary btn-sm">${Utils.icon('download')} Export CSV</button>
        </div>
      </div>

      <!-- Table -->
      <div class="history-table-wrapper">
        <table class="history-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Symbol</th>
              <th>Type</th>
              <th>Volume</th>
              <th>Open Price</th>
              <th>Close Price</th>
              <th>Open Time</th>
              <th>Close Time</th>
              <th>Strategy</th>
              <th>Commission</th>
              <th>Swap</th>
              <th>Profit</th>
            </tr>
          </thead>
          <tbody>
            ${pageData.map(t => `
              <tr>
                <td class="text-muted">${t.id}</td>
                <td><strong style="color:var(--text-primary)">${t.symbol}</strong></td>
                <td><span class="badge ${t.type === 'BUY' ? 'badge-profit' : 'badge-loss'}">${t.type}</span></td>
                <td class="col-mono">${t.volume.toFixed(2)}</td>
                <td class="col-mono">${typeof t.openPrice === 'number' ? t.openPrice.toFixed(t.symbol.includes('JPY') ? 3 : t.symbol === 'XAUUSD' ? 2 : t.symbol === 'NAS100' || t.symbol === 'US30' ? 1 : 5) : t.openPrice}</td>
                <td class="col-mono">${typeof t.closePrice === 'number' ? t.closePrice.toFixed(t.symbol.includes('JPY') ? 3 : t.symbol === 'XAUUSD' ? 2 : t.symbol === 'NAS100' || t.symbol === 'US30' ? 1 : 5) : t.closePrice}</td>
                <td class="text-xs">${Utils.formatDateTime(t.openTime)}</td>
                <td class="text-xs">${Utils.formatDateTime(t.closeTime)}</td>
                <td><span class="pill">${t.strategy}</span></td>
                <td class="col-mono text-loss">${t.commission.toFixed(2)}</td>
                <td class="col-mono">${t.swap.toFixed(2)}</td>
                <td class="col-mono ${t.pnl >= 0 ? 'text-profit' : 'text-loss'}"><strong>${Utils.formatMoney(t.pnl)}</strong></td>
              </tr>
            `).join('')}
          </tbody>
        </table>
        <div class="history-pagination">
          <div class="pagination-info">
            Showing ${start + 1}-${Math.min(start + pageSize, history.length)} of ${history.length} trades
          </div>
          <div class="pagination-controls">
            <button class="page-btn" data-page="prev">${Utils.icon('arrow-up')}</button>
            ${Array.from({length: Math.min(totalPages, 5)}, (_, i) => {
              const p = i + 1;
              return `<button class="page-btn ${p === currentPage ? 'active' : ''}" data-page="${p}">${p}</button>`;
            }).join('')}
            ${totalPages > 5 ? '<span class="text-muted" style="padding:0 4px">...</span><button class="page-btn" data-page="' + totalPages + '">' + totalPages + '</button>' : ''}
            <button class="page-btn" data-page="next">${Utils.icon('arrow-down')}</button>
          </div>
        </div>
      </div>
    `;
  }

  container.innerHTML = renderTable();

  // Pagination
  container.addEventListener('click', (e) => {
    const btn = e.target.closest('.page-btn');
    if (!btn) return;
    const page = btn.dataset.page;
    if (page === 'prev' && currentPage > 1) currentPage--;
    else if (page === 'next' && currentPage < totalPages) currentPage++;
    else if (page !== 'prev' && page !== 'next') currentPage = parseInt(page);
    container.innerHTML = renderTable();
  });
}

window.renderHistory = renderHistory;
