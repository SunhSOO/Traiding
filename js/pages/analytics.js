/**
 * Analytics — performance aggregates from paper_trades.
 *
 * All computed client-side from a single `paperTrades` fetch (top
 * 1000 most-recent) so a fresh DB and a year-old paper account both
 * render with the same code path.
 */

function renderAnalytics(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h2 class="text-xl">성과 분석</h2>
        <p class="text-sm text-muted">페이퍼 거래 집계</p>
      </div>
      <div class="page-header-actions" id="ana2-header-actions"></div>
    </div>

    <div class="analytics-kpi-row stagger-children" id="ana2-kpis">
      <div class="card kpi-card text-muted">불러오는 중…</div>
    </div>

    <div class="analytics-charts">
      <div class="card chart-card animate-fade-in-up">
        <div class="card-header"><div class="card-title">일간 P&amp;L (최근 30일)</div></div>
        <div id="ana2-daily-chart"><div class="text-muted">불러오는 중…</div></div>
      </div>
      <div class="card animate-fade-in-up">
        <div class="card-header"><div class="card-title">승/패 분포</div></div>
        <div id="ana2-winloss"><div class="text-muted">불러오는 중…</div></div>
      </div>
    </div>

    <div class="analytics-bottom">
      <div class="card animate-fade-in-up">
        <div class="card-header"><div class="card-title">종목별 P&amp;L</div></div>
        <div id="ana2-symbol-heatmap"><div class="text-muted">불러오는 중…</div></div>
      </div>
      <div class="card animate-fade-in-up">
        <div class="card-header"><div class="card-title">에쿼티 커브</div></div>
        <div id="ana2-equity-curve"><div class="text-muted">불러오는 중…</div></div>
      </div>
    </div>

    <div class="card animate-fade-in-up" id="ana2-attribution-card">
      <div class="card-header">
        <div>
          <div class="card-title">모듈 성과 기여 (F / T / I)</div>
          <div class="card-subtitle text-xs text-muted" id="ana2-attr-sub">최근 180일 거래에서 각 모듈의 점수가 forward return을 얼마나 예측했나</div>
        </div>
      </div>
      <div id="ana2-attribution-pane"><div class="text-muted">불러오는 중…</div></div>
    </div>
  `;

  let currentMarket = AppState.getMarket();
  MarketTab.mount({
    parent: container.querySelector('#ana2-header-actions'),
    onChange: (m) => { currentMarket = m; reload(); },
  });

  reload();

  async function reload() {
    try {
      const trades = await Api.paperTrades({ market: currentMarket, limit: 1000 });
      const acc = await Api.paperAccount({ market: currentMarket });
      renderKpis(container.querySelector('#ana2-kpis'), trades, acc);
      renderDaily(container.querySelector('#ana2-daily-chart'), trades);
      renderWinLoss(container.querySelector('#ana2-winloss'), trades);
      renderSymbolHeatmap(container.querySelector('#ana2-symbol-heatmap'), trades);
      renderEquityCurve(container.querySelector('#ana2-equity-curve'), trades, acc);
    } catch (err) {
      container.querySelector('#ana2-kpis').innerHTML =
        `<div class="card text-loss">${escapeA(err.message)}</div>`;
    }
    // Attribution loads from its own endpoint
    try {
      const attr = await Api.attributionSummary({ market: currentMarket, days: 180 });
      renderAttribution(container.querySelector('#ana2-attribution-pane'), attr);
    } catch (err) {
      container.querySelector('#ana2-attribution-pane').innerHTML =
        `<div class="text-loss">${escapeA(err.message)}</div>`;
    }
  }

  function renderAttribution(pane, attr) {
    if (!attr.total_samples) {
      pane.innerHTML = `<div class="empty-state text-muted">분석 가능한 (결정→체결) 페어가 없습니다. 페이퍼 거래가 누적되면 채워집니다.</div>`;
      return;
    }
    const labels = { F: '기본 (F)', T: '기술 (T)', I: '정보 (I)' };
    pane.innerHTML = `
      <div class="text-xs text-muted" style="margin-bottom: var(--space-3)">
        ${attr.total_samples}개 거래 분석됨 · 윈도 ${attr.window_days}일
      </div>
      <div class="attribution-grid">
        ${attr.modules.map(m => {
          const r = m.pearson_r;
          const acc = m.sign_accuracy;
          const share = m.attribution_share;
          const rCls = r == null ? '' : r >= 0 ? 'text-profit' : 'text-loss';
          return `
            <div class="attr-card">
              <div class="attr-card-label">${labels[m.module] || m.module}</div>
              <div class="attr-card-share">
                <div class="attr-card-share-bar" style="width:${(share * 100).toFixed(1)}%"></div>
                <div class="attr-card-share-text">${(share * 100).toFixed(1)}%</div>
              </div>
              <div class="attr-card-stats">
                <div>
                  <div class="text-xs text-muted">Pearson r</div>
                  <div class="mono ${rCls}">${r == null ? '—' : (r >= 0 ? '+' : '') + r.toFixed(3)}</div>
                </div>
                <div>
                  <div class="text-xs text-muted">Sign accuracy</div>
                  <div class="mono">${acc == null ? '—' : (acc * 100).toFixed(1) + '%'}</div>
                </div>
                <div>
                  <div class="text-xs text-muted">샘플 수</div>
                  <div class="mono">${m.n_samples}</div>
                </div>
              </div>
            </div>
          `;
        }).join('')}
      </div>
      <div class="text-xs text-muted" style="margin-top: var(--space-3); line-height: 1.5">
        <strong>해석:</strong> Pearson r은 점수와 실제 수익률의 선형 상관도 (-1..1).
        Sign accuracy는 점수 부호와 수익 부호가 일치한 비율.
        Share는 |r|을 모듈 간 정규화한 "예측력 지분"입니다.
      </div>
    `;
  }

  function renderKpis(pane, trades, acc) {
    const ccy = acc.base_currency;
    if (!trades.length) {
      pane.innerHTML = `<div class="card kpi-card text-muted">거래 데이터 없음</div>`;
      return;
    }
    const wins = trades.filter(t => t.pnl > 0);
    const losses = trades.filter(t => t.pnl <= 0);
    const net = trades.reduce((s, t) => s + t.pnl, 0);
    const avgW = wins.length ? wins.reduce((s, t) => s + t.pnl, 0) / wins.length : 0;
    const avgL = losses.length ? Math.abs(losses.reduce((s, t) => s + t.pnl, 0)) / losses.length : 0;
    const winRate = (wins.length / trades.length) * 100;
    const rr = avgL > 0 ? avgW / avgL : (avgW > 0 ? Infinity : 0);
    // Max drawdown from equity curve
    const initial = acc.initial_balance || 1;
    const equityPoints = computeEquityCurve(trades, initial);
    let peak = -Infinity, maxDD = 0;
    for (const p of equityPoints) {
      if (p.equity > peak) peak = p.equity;
      const dd = peak > 0 ? (p.equity - peak) / peak * 100 : 0;
      if (dd < maxDD) maxDD = dd;
    }
    pane.innerHTML = `
      ${kpiA('Net P&L', formatMoneyA(net, ccy), pctA(net, initial), net >= 0)}
      ${kpiA('승률', `${winRate.toFixed(1)}%`, `${wins.length}W / ${losses.length}L`, winRate >= 50)}
      ${kpiA('R/R', isFinite(rr) ? rr.toFixed(2) : '∞', `평균 W: ${formatMoneyA(avgW, ccy)} / L: ${formatMoneyA(avgL, ccy)}`, rr >= 1.5)}
      ${kpiA('Max Drawdown', `${maxDD.toFixed(2)}%`, '최고치 대비', maxDD > -10)}
      ${kpiA('거래 수', `${trades.length}`, '표시된 슬라이스', true)}
    `;
  }

  function renderDaily(pane, trades) {
    // Group by exit_ts date (UTC)
    const byDate = new Map();
    for (const t of trades) {
      const d = (t.exit_ts || '').slice(0, 10);
      byDate.set(d, (byDate.get(d) || 0) + t.pnl);
    }
    const entries = Array.from(byDate.entries()).sort((a, b) => a[0].localeCompare(b[0])).slice(-30);
    if (!entries.length) {
      pane.innerHTML = `<div class="text-muted">데이터 없음</div>`;
      return;
    }
    const maxAbs = Math.max(...entries.map(([, p]) => Math.abs(p))) || 1;
    pane.innerHTML = `
      <div style="display:flex;align-items:flex-end;gap:2px;height:200px;padding:var(--space-4) 0">
        ${entries.map(([d, pnl]) => {
          const h = (Math.abs(pnl) / maxAbs) * 100;
          const color = pnl >= 0 ? 'var(--profit)' : 'var(--loss)';
          return `<div class="tooltip" data-tooltip="${escapeA(d)}: ${formatMoneyA(pnl, currentMarket === 'KR' ? 'KRW' : 'USD')}"
                     style="flex:1;display:flex;flex-direction:column;justify-content:flex-end;height:100%">
            <div style="height:${h}%;background:${color};border-radius:2px 2px 0 0;min-height:2px;opacity:0.85"></div>
          </div>`;
        }).join('')}
      </div>
      <div style="display:flex;justify-content:space-between;font-size:var(--text-xs);color:var(--text-muted)">
        <span>${escapeA(entries[0][0])}</span>
        <span>${escapeA(entries[entries.length - 1][0])}</span>
      </div>
    `;
  }

  function renderWinLoss(pane, trades) {
    if (!trades.length) { pane.innerHTML = `<div class="text-muted">데이터 없음</div>`; return; }
    const wins = trades.filter(t => t.pnl > 0);
    const losses = trades.filter(t => t.pnl < 0);
    const ccy = currentMarket === 'KR' ? 'KRW' : 'USD';
    pane.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:var(--space-3);padding:var(--space-4) 0">
        <div>
          <div style="display:flex;justify-content:space-between;margin-bottom:6px">
            <span class="text-sm text-profit">승</span>
            <span class="font-mono text-sm">${wins.length}</span>
          </div>
          <div class="progress-bar" style="height:12px">
            <div class="progress-fill green" style="width:${trades.length ? (wins.length / trades.length * 100).toFixed(0) : 0}%"></div>
          </div>
        </div>
        <div>
          <div style="display:flex;justify-content:space-between;margin-bottom:6px">
            <span class="text-sm text-loss">패</span>
            <span class="font-mono text-sm">${losses.length}</span>
          </div>
          <div class="progress-bar" style="height:12px">
            <div class="progress-fill red" style="width:${trades.length ? (losses.length / trades.length * 100).toFixed(0) : 0}%"></div>
          </div>
        </div>
        <div class="system-item">
          <span class="system-item-label">최대 승</span>
          <span class="system-item-value text-profit">${formatMoneyA(Math.max(...trades.map(t => t.pnl)), ccy)}</span>
        </div>
        <div class="system-item">
          <span class="system-item-label">최대 패</span>
          <span class="system-item-value text-loss">${formatMoneyA(Math.min(...trades.map(t => t.pnl)), ccy)}</span>
        </div>
      </div>
    `;
  }

  function renderSymbolHeatmap(pane, trades) {
    const byTicker = new Map();
    for (const t of trades) {
      const key = t.ticker;
      const v = byTicker.get(key) || { ticker: key, pnl: 0, n: 0 };
      v.pnl += t.pnl; v.n += 1;
      byTicker.set(key, v);
    }
    const arr = Array.from(byTicker.values()).sort((a, b) => b.pnl - a.pnl);
    if (!arr.length) {
      pane.innerHTML = `<div class="text-muted">데이터 없음</div>`;
      return;
    }
    const maxAbs = Math.max(...arr.map(v => Math.abs(v.pnl))) || 1;
    const ccy = currentMarket === 'KR' ? 'KRW' : 'USD';
    pane.innerHTML = `
      <div class="perf-heatmap">
        ${arr.slice(0, 24).map(v => {
          const a = Math.min(Math.abs(v.pnl) / maxAbs, 1);
          const bg = v.pnl >= 0 ? `rgba(16,185,129,${0.15 + a * 0.4})` : `rgba(239,68,68,${0.15 + a * 0.4})`;
          return `<div class="perf-cell" style="background:${bg}">
            <div class="perf-cell-symbol">${escapeA(v.ticker)}</div>
            <div class="perf-cell-value ${v.pnl >= 0 ? 'text-profit' : 'text-loss'}">
              ${formatMoneyA(v.pnl, ccy)}
            </div>
            <div class="text-xs text-muted">${v.n}회</div>
          </div>`;
        }).join('')}
      </div>
    `;
  }

  function renderEquityCurve(pane, trades, acc) {
    if (!trades.length) {
      pane.innerHTML = `<div class="text-muted">데이터 없음</div>`;
      return;
    }
    const points = computeEquityCurve(trades, acc.initial_balance || 0);
    if (points.length < 2) {
      pane.innerHTML = `<div class="text-muted">거래가 부족합니다 (≥ 2건 필요).</div>`;
      return;
    }
    // SVG line
    const w = 600, h = 200, pad = 12;
    const xs = points.map(p => new Date(p.ts).getTime());
    const ys = points.map(p => p.equity);
    const xMin = xs[0], xMax = xs[xs.length - 1];
    const yMin = Math.min(...ys), yMax = Math.max(...ys);
    const yRange = (yMax - yMin) || 1;
    const xRange = (xMax - xMin) || 1;
    const px = x => pad + (x - xMin) / xRange * (w - 2 * pad);
    const py = y => h - pad - (y - yMin) / yRange * (h - 2 * pad);
    const path = points.map(p => `${px(new Date(p.ts).getTime()).toFixed(1)},${py(p.equity).toFixed(1)}`).join(' ');
    const initY = py(acc.initial_balance || ys[0]);
    pane.innerHTML = `
      <svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" preserveAspectRatio="none">
        <line x1="0" x2="${w}" y1="${initY}" y2="${initY}" stroke="var(--border)" stroke-dasharray="3,3"/>
        <polyline fill="none" stroke="var(--accent-blue)" stroke-width="2" points="${path}"/>
      </svg>
      <div style="display:flex;justify-content:space-between;font-size:var(--text-xs);color:var(--text-muted);margin-top:4px">
        <span>${escapeA(points[0].ts.slice(0, 10))}</span>
        <span>${escapeA(points[points.length - 1].ts.slice(0, 10))}</span>
      </div>
    `;
  }
}

function computeEquityCurve(trades, initial) {
  // Sort by exit_ts ASC for cumulative
  const sorted = trades.slice().sort((a, b) => a.exit_ts.localeCompare(b.exit_ts));
  let equity = initial;
  return sorted.map(t => {
    equity += t.pnl;
    return { ts: t.exit_ts, equity };
  });
}

// ── helpers ──
function kpiA(label, value, sub, positive) {
  return `
    <div class="card kpi-card animate-fade-in-up">
      <div class="kpi-value ${positive ? 'text-profit' : 'text-loss'}">${value}</div>
      <div class="kpi-label">${label}</div>
      <div class="kpi-sub text-muted">${sub}</div>
    </div>`;
}
function formatMoneyA(value, currency) {
  if (currency === 'KRW') return (value < 0 ? '-' : '') + '₩' + Math.abs(Math.round(value)).toLocaleString('ko-KR');
  return (value < 0 ? '-' : '') + '$' + Math.abs(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function pctA(val, base) {
  if (!base || base === 0) return '';
  const p = (val / base) * 100;
  return (p >= 0 ? '+' : '') + p.toFixed(2) + '%';
}
function escapeA(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

window.renderAnalytics = renderAnalytics;
