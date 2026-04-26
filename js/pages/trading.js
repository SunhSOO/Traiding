/**
 * SUPERRICH - Live Trading Page
 */
function renderTrading(container) {
  const { marketData, positions } = MockData;
  const selectedSymbol = marketData[0];

  container.innerHTML = `
    <div class="trading-layout">
      <!-- Chart Area -->
      <div class="trading-chart-area">
        <div class="chart-container">
          <div class="chart-toolbar">
            <div class="chart-toolbar-left">
              <div class="chart-symbol-selector" id="symbol-selector">
                <strong>${selectedSymbol.symbol}</strong>
                <span class="text-sm text-muted" style="margin-left:4px">${selectedSymbol.name}</span>
                ${Utils.icon('arrow-down')}
              </div>
              <span class="font-mono text-md" style="color:var(--text-primary)">${selectedSymbol.bid.toFixed(5)}</span>
              <span class="font-mono text-sm ${selectedSymbol.change >= 0 ? 'text-profit' : 'text-loss'}">${selectedSymbol.change >= 0 ? '+' : ''}${selectedSymbol.change.toFixed(2)}%</span>
              <span class="text-xs text-muted">Spread: ${selectedSymbol.spread}</span>
            </div>
            <div class="chart-timeframes">
              ${['M1','M5','M15','M30','H1','H4','D1','W1'].map((tf, i) => 
                `<button class="tf-btn ${i === 4 ? 'active' : ''}" data-tf="${tf}">${tf}</button>`
              ).join('')}
            </div>
          </div>
          <div class="chart-body" id="trading-chart">
            <div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-muted);flex-direction:column;gap:var(--space-3)">
              <div style="font-size:48px;opacity:0.3">📊</div>
              <div>TradingView Lightweight Charts</div>
              <div class="text-xs">Connect to MT5 for live data</div>
            </div>
          </div>
        </div>

        <!-- Positions Panel -->
        <div class="positions-panel">
          <div class="card">
            <div class="card-header">
              <div class="tabs">
                <div class="tab active" data-tab="positions">Positions (${positions.length})</div>
                <div class="tab" data-tab="orders">Pending Orders (2)</div>
                <div class="tab" data-tab="history">Deal History</div>
              </div>
            </div>
            <div style="overflow-x:auto">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Type</th>
                    <th>Volume</th>
                    <th>Open Price</th>
                    <th>Current</th>
                    <th>S/L</th>
                    <th>T/P</th>
                    <th>Swap</th>
                    <th>Profit</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  ${positions.map(p => `
                    <tr>
                      <td><strong>${p.symbol}</strong></td>
                      <td><span class="badge ${p.type === 'BUY' ? 'badge-profit' : 'badge-loss'}">${p.type}</span></td>
                      <td class="mono">${p.volume.toFixed(2)}</td>
                      <td class="mono">${p.openPrice}</td>
                      <td class="mono">${p.currentPrice}</td>
                      <td class="mono text-loss">${p.sl}</td>
                      <td class="mono text-profit">${p.tp}</td>
                      <td class="mono">${p.swap.toFixed(2)}</td>
                      <td class="mono ${p.pnl >= 0 ? 'text-profit' : 'text-loss'}"><strong>${Utils.formatMoney(p.pnl)}</strong></td>
                      <td>
                        <div class="position-actions">
                          <button class="btn btn-ghost btn-sm" title="Modify">${Utils.icon('settings')}</button>
                          <button class="btn btn-danger btn-sm">Close</button>
                        </div>
                      </td>
                    </tr>
                  `).join('')}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>

      <!-- Right Panel -->
      <div class="trading-right-panel">
        <!-- Order Panel -->
        <div class="card">
          <div class="card-header">
            <div class="card-title">New Order</div>
          </div>
          <div class="order-panel">
            <div class="order-type-tabs">
              <div class="order-type-tab active">Market</div>
              <div class="order-type-tab">Limit</div>
              <div class="order-type-tab">Stop</div>
            </div>
            <div class="order-direction">
              <button class="order-buy-btn active" id="btn-buy">
                BUY<br><span class="font-mono text-sm">${selectedSymbol.ask.toFixed(5)}</span>
              </button>
              <button class="order-sell-btn" id="btn-sell">
                SELL<br><span class="font-mono text-sm">${selectedSymbol.bid.toFixed(5)}</span>
              </button>
            </div>
            <div class="order-fields">
              <div class="order-field">
                <label>Volume (Lots)</label>
                <input type="number" value="0.10" step="0.01" min="0.01" max="100">
              </div>
              <div class="order-field">
                <label>Stop Loss</label>
                <input type="number" placeholder="0.00000" step="0.00001">
              </div>
              <div class="order-field">
                <label>Take Profit</label>
                <input type="number" placeholder="0.00000" step="0.00001">
              </div>
              <div class="order-field">
                <label>Comment</label>
                <input type="text" placeholder="Optional comment">
              </div>
            </div>
            <button class="btn btn-success order-submit" id="btn-place-order">
              Place BUY Order
            </button>
          </div>
        </div>

        <!-- Watchlist -->
        <div class="card" style="flex:1;overflow-y:auto">
          <div class="card-header">
            <div class="card-title">Watchlist</div>
            <button class="btn btn-ghost btn-sm">${Utils.icon('search')}</button>
          </div>
          ${marketData.map((m, i) => `
            <div class="watchlist-item ${i === 0 ? 'selected' : ''}">
              <div>
                <div class="font-semibold text-sm">${m.symbol}</div>
                <div class="text-xs text-muted">${m.name}</div>
              </div>
              <div class="text-right">
                <div class="font-mono text-sm">${m.bid.toFixed(m.symbol === 'NAS100' || m.symbol === 'US30' ? 1 : m.symbol === 'XAUUSD' ? 2 : 5)}</div>
                <div class="font-mono text-xs ${m.change >= 0 ? 'text-profit' : 'text-loss'}">${m.change >= 0 ? '+' : ''}${m.change.toFixed(2)}%</div>
              </div>
            </div>
          `).join('')}
        </div>
      </div>
    </div>
  `;

  // Timeframe tab interaction
  container.querySelectorAll('.tf-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      container.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
    });
  });

  // Order type tabs
  container.querySelectorAll('.order-type-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      container.querySelectorAll('.order-type-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
    });
  });

  // Buy/Sell toggle
  const buyBtn = container.querySelector('#btn-buy');
  const sellBtn = container.querySelector('#btn-sell');
  const submitBtn = container.querySelector('#btn-place-order');
  
  buyBtn.addEventListener('click', () => {
    buyBtn.classList.add('active');
    sellBtn.classList.remove('active');
    submitBtn.textContent = 'Place BUY Order';
    submitBtn.className = 'btn btn-success order-submit';
  });
  
  sellBtn.addEventListener('click', () => {
    sellBtn.classList.add('active');
    buyBtn.classList.remove('active');
    submitBtn.textContent = 'Place SELL Order';
    submitBtn.className = 'btn btn-danger order-submit';
  });

  // Position tabs
  container.querySelectorAll('.tabs .tab').forEach(tab => {
    tab.addEventListener('click', () => {
      container.querySelectorAll('.tabs .tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
    });
  });
}

window.renderTrading = renderTrading;
