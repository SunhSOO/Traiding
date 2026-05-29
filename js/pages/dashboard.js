/**
 * Dashboard — landing page after login.
 *
 * Five cards, all real data, all KR/US-tab-aware:
 *   - Paper account summary (cash + equity + open positions split KR/US)
 *   - Recent decisions (last 5; click → /analysis)
 *   - Top movers (highest |composite| in last 48h; click → /analysis)
 *   - Data freshness summary (OK / stale / errored, link → /freshness)
 *   - Scheduler next runs (link → /freshness)
 *
 * Graceful degradation — when any endpoint fails (DB not up yet,
 * or auth issues), the card shows an inline error message but the
 * page still renders.
 */

function renderDashboard(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h2 class="text-xl">대시보드</h2>
        <p class="text-sm text-muted">페이퍼 계좌, 최근 결정, 신선도, top movers 한눈에</p>
      </div>
      <div class="page-header-actions" id="dash-header-actions"></div>
    </div>

    <!-- System health summary -->
    <div class="card animate-fade-in-up" id="dash-health-card">
      <div class="card-header">
        <div>
          <div class="card-title">시스템 헬스</div>
          <div class="card-subtitle text-xs text-muted" id="dash-health-sub">—</div>
        </div>
        <a href="#/config" class="btn btn-ghost btn-sm">상세</a>
      </div>
      <div id="dash-health-pane"><div class="text-muted">불러오는 중…</div></div>
    </div>

    <!-- Account summary cards -->
    <div class="dashboard-stats stagger-children" id="dash-account-stats">
      <div class="card stat-card"><div class="text-muted">불러오는 중…</div></div>
    </div>

    <!-- Equity curve -->
    <div class="card animate-fade-in-up" id="dash-equity-card">
      <div class="card-header">
        <div>
          <div class="card-title">Equity Curve</div>
          <div class="card-subtitle" id="dash-equity-sub">최근 90일 실현 P&L 곡선</div>
        </div>
        <div class="tabs" id="dash-equity-tabs">
          <div class="tab" data-eq-days="30">30D</div>
          <div class="tab active" data-eq-days="90">90D</div>
          <div class="tab" data-eq-days="365">1Y</div>
        </div>
      </div>
      <div id="dash-equity"><div class="text-muted">불러오는 중…</div></div>
    </div>

    <!-- Main grid -->
    <div class="bento-grid bento-grid-4 stagger-children">
      <div class="card col-span-2 animate-fade-in-up">
        <div class="card-header">
          <div>
            <div class="card-title">최근 결정</div>
            <div class="card-subtitle" id="dash-decisions-sub">—</div>
          </div>
          <a href="#/decisions" class="btn btn-ghost btn-sm">전체보기</a>
        </div>
        <div id="dash-decisions">
          <div class="text-muted">불러오는 중…</div>
        </div>
      </div>

      <div class="card col-span-2 animate-fade-in-up">
        <div class="card-header">
          <div>
            <div class="card-title">Top Movers</div>
            <div class="card-subtitle" id="dash-movers-sub">|composite| 기준 상위 종목</div>
          </div>
          <div class="tabs" id="dash-movers-tabs">
            <div class="tab active" data-mover-dir="both">전체</div>
            <div class="tab" data-mover-dir="bull">상승</div>
            <div class="tab" data-mover-dir="bear">하락</div>
          </div>
        </div>
        <div id="dash-movers">
          <div class="text-muted">불러오는 중…</div>
        </div>
      </div>

      <div class="card animate-fade-in-up">
        <div class="card-header">
          <div>
            <div class="card-title">데이터 신선도</div>
            <div class="card-subtitle" id="dash-fresh-sub">—</div>
          </div>
          <a href="#/freshness" class="btn btn-ghost btn-sm">상세</a>
        </div>
        <div id="dash-fresh"><div class="text-muted">불러오는 중…</div></div>
      </div>

      <div class="card animate-fade-in-up">
        <div class="card-header">
          <div>
            <div class="card-title">스케줄러</div>
            <div class="card-subtitle" id="dash-sched-sub">다음 실행</div>
          </div>
          <a href="#/freshness" class="btn btn-ghost btn-sm">상세</a>
        </div>
        <div id="dash-sched"><div class="text-muted">불러오는 중…</div></div>
      </div>
    </div>
  `;

  let currentMarket = AppState.getMarket();
  let currentMoversDirection = 'both';
  let currentEquityDays = 90;

  MarketTab.mount({
    parent: container.querySelector('#dash-header-actions'),
    onChange: (m) => { currentMarket = m; reloadAll(); },
  });

  container.querySelectorAll('#dash-movers-tabs .tab').forEach(t => {
    t.addEventListener('click', () => {
      container.querySelectorAll('#dash-movers-tabs .tab').forEach(x => x.classList.remove('active'));
      t.classList.add('active');
      currentMoversDirection = t.dataset.moverDir;
      reloadMovers();
    });
  });

  container.querySelectorAll('#dash-equity-tabs .tab').forEach(t => {
    t.addEventListener('click', () => {
      container.querySelectorAll('#dash-equity-tabs .tab').forEach(x => x.classList.remove('active'));
      t.classList.add('active');
      currentEquityDays = Number(t.dataset.eqDays);
      reloadEquity();
    });
  });

  reloadAll();
  // Refresh every 60 s while page is mounted
  const intervalId = setInterval(reloadAll, 60_000);
  AppRouter.beforeEach(() => { clearInterval(intervalId); return true; });

  async function reloadAll() {
    await Promise.all([
      reloadHealth(),
      reloadAccount(),
      reloadEquity(),
      reloadDecisions(),
      reloadMovers(),
      reloadFreshness(),
      reloadJobs(),
    ]);
  }

  async function reloadHealth() {
    const pane = container.querySelector('#dash-health-pane');
    const sub = container.querySelector('#dash-health-sub');
    try {
      const data = await Api.healthSummary();
      sub.innerHTML = `전체: <strong class="${tierClass(data.overall_tier)}">${data.overall_tier}</strong> · ${Utils.timeAgo(data.checked_at)} 전`;
      pane.innerHTML = `
        <div class="health-grid">
          ${data.areas.map(a => `
            <div class="health-card tier-${a.tier.toLowerCase()}">
              <div class="health-card-top">
                <span class="health-card-name">${escape(a.name)}</span>
                <span class="health-card-tier ${tierClass(a.tier)}">${a.tier}</span>
              </div>
              <div class="health-card-headline">${escape(a.headline)}</div>
              ${a.detail ? `<div class="health-card-detail text-xs text-muted">${escape(a.detail)}</div>` : ''}
            </div>
          `).join('')}
        </div>
      `;
    } catch (err) {
      pane.innerHTML = `<div class="text-loss">${escape(err.message)}</div>`;
    }
  }

  function tierClass(tier) {
    if (tier === 'OK') return 'text-profit';
    if (tier === 'WARN') return 'text-warn';
    return 'text-loss';
  }

  async function reloadAccount() {
    const pane = container.querySelector('#dash-account-stats');
    try {
      const acc = await Api.paperAccount({ market: currentMarket });
      const ccy = acc.base_currency;
      const totalPnl = acc.realised_pnl_total;
      pane.innerHTML = `
        ${statCard('현금', formatMoney(acc.current_balance, ccy), null, 'blue', 'dollar-sign')}
        ${statCard('Equity',
          formatMoney(acc.current_balance + acc.realised_pnl_today, ccy),
          totalPnl >= 0
            ? `+${formatMoney(totalPnl, ccy)} 누적 실현`
            : `${formatMoney(totalPnl, ccy)} 누적 실현`,
          totalPnl >= 0 ? 'green' : 'red', 'activity')}
        ${statCard('오픈 포지션',
          `${acc.open_positions}`,
          `KR ${acc.open_positions_kr} · US ${acc.open_positions_us}`,
          'purple', 'layers')}
        ${statCard('오늘 실현 P&L',
          formatMoney(acc.realised_pnl_today, ccy),
          acc.closed_trades_total + ' 누적 거래',
          acc.realised_pnl_today >= 0 ? 'green' : 'red', 'trending-up')}
      `;
    } catch (err) {
      pane.innerHTML = `<div class="card text-loss">계좌 정보를 불러오지 못했습니다: ${escape(err.message)}</div>`;
    }
  }

  async function reloadEquity() {
    const pane = container.querySelector('#dash-equity');
    const sub = container.querySelector('#dash-equity-sub');
    try {
      const data = await Api.paperEquityCurve({ days: currentEquityDays, market: currentMarket });
      const ccy = data.base_currency || 'USD';
      sub.textContent = `${data.summary.total_trades}건 거래 · 승률 ${(data.summary.win_rate * 100).toFixed(1)}%`;

      if (!data.points.length) {
        pane.innerHTML = `<div class="text-muted">아직 청산된 거래가 없습니다. paper 결정이 누적되면 곡선이 그려집니다.</div>`;
        return;
      }
      pane.innerHTML = `
        <div class="equity-curve-row">
          <div class="equity-chart-cell">
            ${renderEquitySparkline(data.points)}
          </div>
          <div class="equity-stats-cell">
            ${equityStat('총 수익률', fmtPct(data.summary.return_pct),
              data.summary.return_pct >= 0 ? 'profit' : 'loss')}
            ${equityStat('실현 P&L', formatMoney(data.summary.total_realized_pnl, ccy),
              data.summary.total_realized_pnl >= 0 ? 'profit' : 'loss')}
            ${equityStat('미실현 P&L', formatMoney(data.unrealised_pnl, ccy),
              data.unrealised_pnl >= 0 ? 'profit' : 'loss')}
            ${equityStat('Max Drawdown', fmtPct(data.summary.max_drawdown), 'loss')}
            ${equityStat('Sharpe / Sortino',
              `${data.summary.sharpe_like.toFixed(2)} / ${(data.summary.sortino_like || 0).toFixed(2)}`,
              data.summary.sharpe_like >= 0 ? 'profit' : 'loss')}
            ${equityStat('Calmar / Profit Factor',
              `${(data.summary.calmar || 0).toFixed(2)} / ${(data.summary.profit_factor || 0).toFixed(2)}`,
              data.summary.calmar >= 0 ? 'profit' : 'loss')}
            ${equityStat('Expectancy / 거래당 평균',
              `${formatMoney(data.summary.expectancy || 0, ccy)} / ${formatMoney(data.summary.avg_trade_pnl, ccy)}`,
              (data.summary.expectancy || 0) >= 0 ? 'profit' : 'loss')}
            ${equityStat('베스트/워스트',
              `${formatMoney(data.summary.best_trade_pnl, ccy)} / ${formatMoney(data.summary.worst_trade_pnl, ccy)}`,
              null)}
          </div>
        </div>
      `;
    } catch (err) {
      pane.innerHTML = `<div class="text-loss">${escape(err.message)}</div>`;
    }
  }

  async function reloadDecisions() {
    const pane = container.querySelector('#dash-decisions');
    const sub = container.querySelector('#dash-decisions-sub');
    try {
      const rows = await Api.recentDecisions({ market: currentMarket, limit: 6 });
      sub.textContent = `${currentMarket} · 최근 ${rows.length}건`;
      if (!rows.length) {
        pane.innerHTML = `<div class="text-muted">최근 결정이 없습니다. 스케줄러의 decisions.daily 잡이 돌면 채워집니다.</div>`;
        return;
      }
      pane.innerHTML = `
        <table class="data-table" style="margin-top:4px">
          <tbody>
            ${rows.map(r => `
              <tr class="dash-decision-row" data-ticker="${escape(r.ticker)}" data-market="${escape(r.market)}">
                <td class="text-xs text-muted">${escape(Utils.timeAgo(r.decision_ts))}</td>
                <td>
                  <a class="decision-ticker-link"
                     href="#${escape(AppRouter.pathWithQuery('/analysis', { market: r.market, ticker: r.ticker }))}">
                    ${r.market}:${escape(r.ticker)}
                  </a>
                </td>
                <td><span class="badge ${decisionBadgeClass(r.action)}">${r.action}</span></td>
                <td class="mono ${(r.composite_score ?? 0) >= 0 ? 'text-profit' : 'text-loss'}">
                  ${r.composite_score == null ? '—' : r.composite_score.toFixed(1)}
                </td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `;
    } catch (err) {
      pane.innerHTML = `<div class="text-loss">${escape(err.message)}</div>`;
    }
  }

  async function reloadMovers() {
    const pane = container.querySelector('#dash-movers');
    try {
      const rows = await Api.topMovers({
        market: currentMarket, limit: 8, direction: currentMoversDirection,
      });
      if (!rows.length) {
        pane.innerHTML = `<div class="text-muted">최근 48시간 점수가 없습니다.</div>`;
        return;
      }
      pane.innerHTML = `
        <div class="dash-movers-list">
          ${rows.map(r => `
            <a class="dash-mover-row"
               href="#${escape(AppRouter.pathWithQuery('/analysis', { market: r.market, ticker: r.ticker }))}">
              <div class="dash-mover-ticker">
                <strong>${r.market}:${escape(r.ticker)}</strong>
                <span class="text-xs text-muted">${escape(r.name || '')}</span>
              </div>
              <div class="dash-mover-score ${r.composite_score >= 0 ? 'text-profit' : 'text-loss'}">
                ${r.composite_score >= 0 ? '+' : ''}${r.composite_score.toFixed(1)}
                <span class="text-xs text-muted">conf ${(r.composite_confidence * 100).toFixed(0)}%</span>
              </div>
            </a>
          `).join('')}
        </div>
      `;
    } catch (err) {
      pane.innerHTML = `<div class="text-loss">${escape(err.message)}</div>`;
    }
  }

  async function reloadFreshness() {
    const pane = container.querySelector('#dash-fresh');
    const sub = container.querySelector('#dash-fresh-sub');
    try {
      const rows = await Api.ingestionFreshness();
      const now = Date.now();
      let ok = 0, stale = 0, errored = 0;
      for (const r of rows) {
        if (r.last_error) errored++;
        else if (!r.last_success_ts ||
                 (now - new Date(r.last_success_ts).getTime()) > 24 * 3600_000) stale++;
        else ok++;
      }
      sub.textContent = `총 ${rows.length}개 소스`;
      pane.innerHTML = `
        <div class="dash-fresh-grid">
          <div class="dash-fresh-cell ok"><div class="num">${ok}</div><div class="lbl">최신</div></div>
          <div class="dash-fresh-cell stale"><div class="num">${stale}</div><div class="lbl">지연</div></div>
          <div class="dash-fresh-cell err"><div class="num">${errored}</div><div class="lbl">에러</div></div>
        </div>
      `;
    } catch (err) {
      pane.innerHTML = `<div class="text-loss">${escape(err.message)}</div>`;
    }
  }

  async function reloadJobs() {
    const pane = container.querySelector('#dash-sched');
    try {
      const jobs = await Api.ingestionJobs();
      // Sort by next_run ASC (nulls last)
      const sorted = jobs.slice().sort((a, b) => {
        if (!a.next_run) return 1;
        if (!b.next_run) return -1;
        return new Date(a.next_run) - new Date(b.next_run);
      }).slice(0, 5);

      if (!sorted.length) {
        pane.innerHTML = `<div class="text-muted">스케줄러가 시작되지 않았습니다.</div>`;
        return;
      }
      pane.innerHTML = `
        <ul class="dash-sched-list">
          ${sorted.map(j => `
            <li>
              <span class="dash-sched-id">${escape(j.id)}</span>
              <span class="text-xs text-muted">${j.next_run ? Utils.timeAgo(j.next_run) + ' 후' : '미정'}</span>
            </li>
          `).join('')}
        </ul>
      `;
    } catch (err) {
      pane.innerHTML = `<div class="text-loss">${escape(err.message)}</div>`;
    }
  }
}

// ── helpers ──
function statCard(label, value, footer, glow, iconName) {
  const iconCls = `card-icon-${glow}`;
  return `
    <div class="card stat-card animate-fade-in-up">
      <div class="stat-glow ${glow}"></div>
      <div class="stat-card-top">
        <span class="stat-card-label">${label}</span>
        <div class="card-icon ${iconCls}">${Utils.icon(iconName)}</div>
      </div>
      <div class="stat-card-value">${value}</div>
      ${footer ? `<div class="stat-card-footer"><span class="text-xs text-muted">${footer}</span></div>` : ''}
    </div>
  `;
}

function formatMoney(value, currency) {
  if (currency === 'KRW') return '₩' + Math.round(value).toLocaleString('ko-KR');
  return '$' + value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtPct(v) {
  if (v === null || v === undefined || isNaN(v)) return '—';
  const sign = v > 0 ? '+' : '';
  return sign + (v * 100).toFixed(2) + '%';
}

function equityStat(label, value, tone) {
  const valueCls = tone === 'profit' ? 'text-profit'
                 : tone === 'loss'   ? 'text-loss'
                 : '';
  return `
    <div class="equity-stat">
      <div class="equity-stat-label">${label}</div>
      <div class="equity-stat-value ${valueCls}">${value}</div>
    </div>
  `;
}

/** Build a compact SVG equity sparkline.
 *  - Line = equity series scaled to viewbox.
 *  - Baseline (initial balance) drawn as a dotted reference.
 *  - Filled area below line tinted by terminal sign (profit/loss).
 */
function renderEquitySparkline(points) {
  if (!points.length) return '';
  const W = 800, H = 180, PAD_L = 12, PAD_R = 12, PAD_T = 12, PAD_B = 18;

  const equities = points.map(p => p.equity);
  const minEq = Math.min(...equities);
  const maxEq = Math.max(...equities);
  const baseline = points[0].equity - points[0].realized_pnl_cum; // initial balance
  const lo = Math.min(minEq, baseline);
  const hi = Math.max(maxEq, baseline);
  const range = (hi - lo) || 1;

  const x = (i) => PAD_L + (W - PAD_L - PAD_R) * (i / Math.max(points.length - 1, 1));
  const y = (v) => PAD_T + (H - PAD_T - PAD_B) * (1 - (v - lo) / range);

  const linePath = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${x(i).toFixed(1)} ${y(p.equity).toFixed(1)}`).join(' ');
  const areaPath =
    `${linePath} L ${x(points.length - 1).toFixed(1)} ${y(baseline).toFixed(1)} ` +
    `L ${x(0).toFixed(1)} ${y(baseline).toFixed(1)} Z`;

  const profitable = points[points.length - 1].equity >= baseline;
  const fillClass = profitable ? 'eq-area-profit' : 'eq-area-loss';
  const strokeClass = profitable ? 'eq-line-profit' : 'eq-line-loss';

  // Baseline reference line
  const baseY = y(baseline);
  const baselineLine =
    `<line x1="${PAD_L}" x2="${W - PAD_R}" y1="${baseY.toFixed(1)}" y2="${baseY.toFixed(1)}" class="eq-baseline"/>`;

  // X-axis labels — first, mid, last date
  const labels = [0, Math.floor(points.length / 2), points.length - 1].map(i => {
    const d = points[i].date.slice(5);  // MM-DD
    return `<text x="${x(i).toFixed(1)}" y="${H - 4}" class="eq-xlabel" text-anchor="middle">${d}</text>`;
  }).join('');

  return `
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="equity-sparkline">
      <path d="${areaPath}" class="eq-area ${fillClass}"/>
      ${baselineLine}
      <path d="${linePath}" class="eq-line ${strokeClass}" fill="none"/>
      ${labels}
    </svg>
  `;
}

function decisionBadgeClass(action) {
  switch (action) {
    case 'BUY': return 'badge-profit';
    case 'SELL': return 'badge-loss';
    case 'REJECTED': return 'badge-loss';
    case 'HOLD': return 'badge-neutral';
    default: return 'badge-info';
  }
}

function escape(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

window.renderDashboard = renderDashboard;
