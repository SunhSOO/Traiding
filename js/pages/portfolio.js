/**
 * Portfolio — paper-broker open positions for the selected market.
 *
 * Three blocks:
 *  - Top: equity / cash / unrealised P&L summary cards
 *  - Allocation donut by ticker
 *  - Position table with per-row P&L %
 */

function renderPortfolio(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h2 class="text-xl">포트폴리오</h2>
        <p class="text-sm text-muted">페이퍼 계좌 오픈 포지션</p>
      </div>
      <div class="page-header-actions" id="port-header-actions"></div>
    </div>

    <div class="dashboard-stats stagger-children" id="port-stats">
      <div class="card text-muted">불러오는 중…</div>
    </div>

    <div class="portfolio-body">
      <div class="card animate-fade-in-up">
        <div class="card-header">
          <div class="card-title">자산 배분</div>
        </div>
        <div id="port-donut-pane">
          <div class="text-muted">불러오는 중…</div>
        </div>
      </div>
      <div class="card animate-fade-in-up">
        <div class="card-header">
          <div class="card-title">포지션 상세</div>
        </div>
        <div id="port-positions-pane">
          <div class="text-muted">불러오는 중…</div>
        </div>
      </div>
    </div>
  `;

  let currentMarket = AppState.getMarket();
  MarketTab.mount({
    parent: container.querySelector('#port-header-actions'),
    onChange: (m) => { currentMarket = m; reload(); },
  });

  reload();
  async function reload() {
    await Promise.all([reloadStats(), reloadPositions()]);
  }

  async function reloadStats() {
    const pane = container.querySelector('#port-stats');
    try {
      const acc = await Api.paperAccount({ market: currentMarket });
      const ccy = acc.base_currency;
      pane.innerHTML = `
        ${statCardP('Equity', formatMoneyP(acc.current_balance, ccy), `${ccy} 기준 현금`, 'blue')}
        ${statCardP('초기 자본', formatMoneyP(acc.initial_balance, ccy), '대비 ' + pctP(acc.current_balance, acc.initial_balance), 'purple')}
        ${statCardP('오픈 포지션', `${acc.open_positions}`, `KR ${acc.open_positions_kr} · US ${acc.open_positions_us}`, 'cyan')}
        ${statCardP('실현 P&L', formatMoneyP(acc.realised_pnl_total, ccy),
            `오늘 ${formatMoneyP(acc.realised_pnl_today, ccy)}`,
            acc.realised_pnl_total >= 0 ? 'green' : 'red')}
      `;
    } catch (err) {
      pane.innerHTML = `<div class="card text-loss">${escapeP(err.message)}</div>`;
    }
  }

  async function reloadPositions() {
    const donutPane = container.querySelector('#port-donut-pane');
    const posPane = container.querySelector('#port-positions-pane');
    try {
      const positions = await Api.paperPositions({ market: currentMarket });
      if (!positions.length) {
        donutPane.innerHTML = `<div class="empty-state text-muted">오픈 포지션이 없습니다.</div>`;
        posPane.innerHTML = `<div class="empty-state text-muted">결정 엔진이 ${currentMarket} 종목 BUY를 실행하면 채워집니다.</div>`;
        return;
      }

      // Allocation donut (by notional)
      const totals = positions.map(p => ({
        ticker: p.ticker, name: p.name,
        notional: p.entry_price * p.volume,
        pnl: p.unrealised_pnl ?? 0,
      }));
      const totalNotional = totals.reduce((s, x) => s + x.notional, 0);
      const colors = ['#3b82f6', '#8b5cf6', '#06b6d4', '#10b981', '#f59e0b', '#ef4444', '#ec4899'];
      const slices = totals.map((t, i) => ({
        ...t, pct: totalNotional > 0 ? (t.notional / totalNotional) * 100 : 0,
        color: colors[i % colors.length],
      }));

      donutPane.innerHTML = `
        <div class="donut-chart-wrapper">
          <svg viewBox="0 0 200 200" style="width:100%;height:200px;transform:rotate(-90deg)">
            ${(() => {
              let offset = 0;
              return slices.map(s => {
                const dash = (s.pct / 100) * (2 * Math.PI * 70);
                const gap = (2 * Math.PI * 70) - dash;
                const el = `<circle cx="100" cy="100" r="70" fill="none" stroke="${s.color}" stroke-width="25"
                  stroke-dasharray="${dash} ${gap}" stroke-dashoffset="${-offset}" opacity="0.9"/>`;
                offset += dash;
                return el;
              }).join('');
            })()}
          </svg>
          <div class="donut-center-text">
            <div class="donut-center-value">${positions.length}</div>
            <div class="donut-center-label">positions</div>
          </div>
        </div>
        <div class="allocation-legend">
          ${slices.map(s => `
            <div class="legend-item">
              <div class="legend-dot" style="background:${s.color}"></div>
              <span class="legend-label">${escapeP(s.ticker)}</span>
              <span class="legend-value">${s.pct.toFixed(1)}%</span>
            </div>
          `).join('')}
        </div>
      `;

      // Positions table
      posPane.innerHTML = `
        ${positions.map(p => `
          <a class="position-card" href="#${escapeP(AppRouter.pathWithQuery('/analysis', { market: p.market, ticker: p.ticker }))}">
            <div class="position-card-dir ${p.side === 'BUY' ? 'long' : 'short'}"></div>
            <div class="position-card-body">
              <div class="position-card-symbol">
                ${escapeP(p.ticker)}
                <span class="badge ${p.side === 'BUY' ? 'badge-profit' : 'badge-loss'}" style="font-size:9px">${p.side}</span>
              </div>
              <div class="position-card-meta">
                <span>${p.volume.toFixed(2)} 주</span>
                <span>Entry: ${p.entry_price.toFixed(2)}</span>
                <span>Current: ${p.current_price != null ? p.current_price.toFixed(2) : '—'}</span>
                <span>${Utils.timeAgo(p.entry_ts)}</span>
              </div>
            </div>
            <div class="position-card-pnl">
              <div class="position-card-pnl-value ${(p.unrealised_pnl ?? 0) >= 0 ? 'text-profit' : 'text-loss'}">
                ${p.unrealised_pnl == null ? '—' : formatMoneyP(p.unrealised_pnl, currentMarket === 'KR' ? 'KRW' : 'USD')}
              </div>
              <div class="position-card-pnl-pct ${(p.unrealised_pnl_pct ?? 0) >= 0 ? 'text-profit' : 'text-loss'}">
                ${p.unrealised_pnl_pct == null ? '' : (p.unrealised_pnl_pct >= 0 ? '+' : '') + p.unrealised_pnl_pct.toFixed(2) + '%'}
              </div>
            </div>
          </a>
        `).join('')}
      `;
    } catch (err) {
      donutPane.innerHTML = `<div class="text-loss">${escapeP(err.message)}</div>`;
      posPane.innerHTML = '';
    }
  }
}

// ── helpers ──
function statCardP(label, value, footer, glow) {
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
function formatMoneyP(value, currency) {
  if (currency === 'KRW') return '₩' + Math.round(value).toLocaleString('ko-KR');
  const sign = value < 0 ? '-' : '';
  return sign + '$' + Math.abs(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function pctP(curr, init) {
  if (!init || init === 0) return '—';
  const p = (curr - init) / init * 100;
  return (p >= 0 ? '+' : '') + p.toFixed(2) + '%';
}
function escapeP(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

window.renderPortfolio = renderPortfolio;
