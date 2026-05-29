/**
 * History — closed paper trades for the selected market.
 *
 * Filters: ticker (text), date range (client-side after fetch since
 * the backend already orders by exit_ts DESC).
 *
 * Stats strip on top: total / win rate / profit factor / net P&L,
 * computed client-side from the loaded slice.
 */

function renderHistory(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h2 class="text-xl">거래 내역</h2>
        <p class="text-sm text-muted">페이퍼 브로커 청산 거래</p>
      </div>
      <div class="page-header-actions" id="hist-header-actions"></div>
    </div>

    <div class="dashboard-stats stagger-children" id="hist-stats">
      <div class="card text-muted">불러오는 중…</div>
    </div>

    <div class="card">
      <div class="card-header">
        <div class="filter-row">
          <input class="input" id="hist-filter-ticker" placeholder="티커 (예: 005930)" style="width:200px">
          <select class="select" id="hist-limit">
            <option value="50">최근 50건</option>
            <option value="100" selected>최근 100건</option>
            <option value="500">최근 500건</option>
            <option value="1000">최근 1000건</option>
          </select>
          <button class="btn btn-secondary btn-sm" id="hist-refresh">${Utils.icon('refresh-cw')} 새로고침</button>
        </div>
      </div>
      <div id="hist-table-pane">
        <div class="empty-state text-muted">불러오는 중…</div>
      </div>
    </div>
  `;

  let currentMarket = AppState.getMarket();
  MarketTab.mount({
    parent: container.querySelector('#hist-header-actions'),
    onChange: (m) => { currentMarket = m; reload(); },
  });

  const tickerInput = container.querySelector('#hist-filter-ticker');
  const limitSel = container.querySelector('#hist-limit');
  const refreshBtn = container.querySelector('#hist-refresh');
  tickerInput.addEventListener('input', Utils.debounce(reload, 400));
  limitSel.addEventListener('change', reload);
  refreshBtn.addEventListener('click', reload);

  reload();

  async function reload() {
    const tablePane = container.querySelector('#hist-table-pane');
    const statsPane = container.querySelector('#hist-stats');
    tablePane.innerHTML = `<div class="empty-state text-muted">불러오는 중…</div>`;
    try {
      const trades = await Api.paperTrades({
        market: currentMarket,
        ticker: tickerInput.value.trim() || undefined,
        limit: parseInt(limitSel.value, 10) || 100,
      });
      renderStats(statsPane, trades);
      renderTable(tablePane, trades);
    } catch (err) {
      statsPane.innerHTML = `<div class="card text-loss">${escapeH(err.message)}</div>`;
      tablePane.innerHTML = '';
    }
  }

  function renderStats(pane, trades) {
    if (!trades.length) {
      pane.innerHTML = `<div class="card text-muted">청산 거래가 없습니다.</div>`;
      return;
    }
    const total = trades.length;
    const wins = trades.filter(t => t.pnl > 0);
    const losses = trades.filter(t => t.pnl < 0);
    const netPnl = trades.reduce((s, t) => s + t.pnl, 0);
    const winRate = (wins.length / total) * 100;
    const grossW = wins.reduce((s, t) => s + t.pnl, 0);
    const grossL = Math.abs(losses.reduce((s, t) => s + t.pnl, 0));
    const pf = grossL > 0 ? grossW / grossL : (wins.length > 0 ? Infinity : 0);
    const ccy = currentMarket === 'KR' ? 'KRW' : 'USD';
    pane.innerHTML = `
      ${statCardH('총 거래', `${total}`, `최근 ${trades.length}건`, 'blue')}
      ${statCardH('승률', `${winRate.toFixed(1)}%`, `${wins.length}W / ${losses.length}L`, 'purple')}
      ${statCardH('Profit Factor', isFinite(pf) ? pf.toFixed(2) : '∞', '총이익 / 총손실', pf >= 1.5 ? 'green' : 'red')}
      ${statCardH('순 P&L', formatMoneyH(netPnl, ccy), '수수료·세금 제외 후', netPnl >= 0 ? 'green' : 'red')}
    `;
  }

  function renderTable(pane, trades) {
    if (!trades.length) {
      pane.innerHTML = `
        <div class="empty-state">
          <div style="font-size:36px;margin-bottom:8px">📭</div>
          <div class="empty-state-title">거래가 없습니다</div>
          <div class="text-xs text-muted">결정 엔진이 ${currentMarket} 종목을 실제로 청산하기 시작하면 채워집니다.</div>
        </div>`;
      return;
    }
    pane.innerHTML = `
      <div class="history-table-wrapper">
        <table class="history-table">
          <thead>
            <tr>
              <th>#</th><th>종목</th><th>방향</th><th>수량</th>
              <th>진입가</th><th>청산가</th>
              <th>진입</th><th>청산</th>
              <th>수수료</th><th>세금</th>
              <th>P&amp;L</th><th>%</th>
            </tr>
          </thead>
          <tbody>
            ${trades.map(t => `
              <tr>
                <td class="text-muted">${t.id}</td>
                <td><a class="decision-ticker-link" href="#${escapeH(AppRouter.pathWithQuery('/analysis', { market: t.market, ticker: t.ticker }))}">
                      ${t.market}:${escapeH(t.ticker)}
                    </a></td>
                <td><span class="badge ${t.side === 'BUY' ? 'badge-profit' : 'badge-loss'}">${t.side}</span></td>
                <td class="col-mono">${t.volume.toFixed(2)}</td>
                <td class="col-mono">${t.entry_price.toFixed(2)}</td>
                <td class="col-mono">${t.exit_price.toFixed(2)}</td>
                <td class="text-xs">${escapeH(Utils.formatDateTime(t.entry_ts))}</td>
                <td class="text-xs">${escapeH(Utils.formatDateTime(t.exit_ts))}</td>
                <td class="col-mono text-loss">${t.commission.toFixed(2)}</td>
                <td class="col-mono text-loss">${t.tax.toFixed(2)}</td>
                <td class="col-mono ${t.pnl >= 0 ? 'text-profit' : 'text-loss'}">
                  <strong>${formatMoneyH(t.pnl, currentMarket === 'KR' ? 'KRW' : 'USD')}</strong>
                </td>
                <td class="col-mono ${t.pnl_pct >= 0 ? 'text-profit' : 'text-loss'}">${t.pnl_pct.toFixed(2)}%</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `;
  }
}

// ── helpers ──
function statCardH(label, value, footer, glow) {
  return `
    <div class="card stat-card animate-fade-in-up">
      <div class="stat-glow ${glow}"></div>
      <div class="stat-card-top">
        <span class="stat-card-label">${label}</span>
      </div>
      <div class="stat-card-value">${value}</div>
      <div class="stat-card-footer"><span class="text-xs text-muted">${footer || ''}</span></div>
    </div>
  `;
}
function formatMoneyH(value, currency) {
  if (currency === 'KRW') return (value < 0 ? '-' : '') + '₩' + Math.abs(Math.round(value)).toLocaleString('ko-KR');
  return (value < 0 ? '-' : '') + '$' + Math.abs(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function escapeH(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

window.renderHistory = renderHistory;
