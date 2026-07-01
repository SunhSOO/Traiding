/**
 * 통합 포트폴리오 — the integrated strategy's two accounts (core-kr / core-us)
 * in one view. (KRW/USD aren't FX-summed; each market is shown side by side.)
 *
 * Closes the design's §9.4 "통합 포트폴리오·감사" as a purpose-built page that
 * ties the selection→execution narrative together: per-account equity/return +
 * open holdings + shortcuts into 시황/선정/실행.
 */

const CORE_ACCOUNTS = [
  { market: 'KR', account: 'core-kr', ccy: 'KRW', flag: '🇰🇷 한국' },
  { market: 'US', account: 'core-us', ccy: 'USD', flag: '🇺🇸 미국' },
];

function _pnlCls(v) { return (v || 0) >= 0 ? 'text-success' : 'text-danger'; }

function _acctCard(cfg, eq) {
  const s = (eq && eq.summary) || {};
  const equity = eq ? eq.current_equity : 0;
  const ret = s.return_pct || 0;
  const upnl = eq ? eq.unrealised_pnl : 0;
  return `
    <div class="card animate-fade-in-up">
      <div class="card-header">
        <div><div class="card-title">${cfg.flag}</div>
          <div class="card-subtitle text-xs text-muted">${cfg.account}</div></div>
        <span class="badge ${_pnlCls(ret)}">${ret >= 0 ? '+' : ''}${ret.toFixed(2)}%</span>
      </div>
      <div class="ix-expo">
        <div class="ix-expo-num">${Utils.formatNumber(equity, 0)}<span class="text-lg text-muted"> ${cfg.ccy}</span></div>
        <div class="text-sm text-muted">현재 평가금액 (equity)</div>
      </div>
      <div class="ix-metric-grid">
        <div class="ix-metric"><div class="ix-metric-label">실현 손익</div>
          <div class="ix-metric-val ${_pnlCls(s.total_realized_pnl)}">${Utils.formatNumber(s.total_realized_pnl || 0, 0)}</div></div>
        <div class="ix-metric"><div class="ix-metric-label">미실현 손익</div>
          <div class="ix-metric-val ${_pnlCls(upnl)}">${Utils.formatNumber(upnl || 0, 0)}</div></div>
        <div class="ix-metric"><div class="ix-metric-label">승률 / 거래</div>
          <div class="ix-metric-val">${((s.win_rate || 0) * 100).toFixed(0)}% <span class="text-xs text-muted">/ ${s.total_trades || 0}</span></div></div>
        <div class="ix-metric"><div class="ix-metric-label">최대 낙폭 (MDD)</div>
          <div class="ix-metric-val text-danger">${((s.max_drawdown || 0) * 100).toFixed(1)}%</div></div>
      </div>
      <div class="ix-foot text-xs text-muted">보유 ${eq ? eq.open_positions : 0}종목 · 개시자본 ${Utils.formatNumber(eq ? eq.initial_balance : 0, 0)} ${cfg.ccy}</div>
    </div>`;
}

function _posRow(p, ccy) {
  const upnl = p.unrealised_pnl;
  const pct = p.unrealised_pnl_pct;
  return `
    <tr>
      <td><span class="badge badge-muted">${p.market}</span></td>
      <td><b>${Utils.escape(p.ticker)}</b>${p.name ? ` <span class="text-xs text-muted">${Utils.escape(p.name)}</span>` : ''}</td>
      <td class="text-right">${Utils.formatNumber(p.volume, 2)}</td>
      <td class="text-right">${Utils.formatNumber(p.entry_price, 0)}</td>
      <td class="text-right">${p.current_price != null ? Utils.formatNumber(p.current_price, 0) : '—'}</td>
      <td class="text-right ${_pnlCls(upnl)}">${upnl != null ? Utils.formatNumber(upnl, 0) : '—'}</td>
      <td class="text-right ${_pnlCls(pct)}">${pct != null ? (pct >= 0 ? '+' : '') + pct.toFixed(1) + '%' : '—'}</td>
    </tr>`;
}

async function renderCorePortfolio(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h2 class="text-xl">통합 포트폴리오</h2>
        <p class="text-sm text-muted">선정→실행 전략 계정(core-kr / core-us)의 평가금액·손익·보유. 시황이 얼마나, 알파가 무엇을, 기술적이 언제를 결정한 결과</p>
      </div>
      <div class="page-header-actions">
        <button class="btn btn-secondary btn-sm" id="cp-refresh">${Utils.icon('refresh-cw')} 새로고침</button>
      </div>
    </div>
    <div class="ix-legend text-xs text-muted">
      <a href="#/market" class="ix-chip">시황 읽기 →</a>
      <a href="#/basket" class="ix-chip">선정 바스켓 →</a>
      <a href="#/execution" class="ix-chip">실행 타이밍 →</a>
    </div>
    <div class="ix-card-row" id="cp-cards"><div class="empty-state text-muted">불러오는 중…</div></div>
    <div class="card animate-fade-in-up">
      <div class="card-header"><div class="card-title">보유 종목 (전 계정)</div></div>
      <div class="table-wrap">
        <table class="table table-compact">
          <thead><tr><th>시장</th><th>종목</th><th class="text-right">수량</th>
            <th class="text-right">진입가</th><th class="text-right">현재가</th>
            <th class="text-right">미실현</th><th class="text-right">%</th></tr></thead>
          <tbody id="cp-positions"><tr><td colspan="7" class="empty-state text-muted">불러오는 중…</td></tr></tbody>
        </table>
      </div>
    </div>`;

  async function load() {
    const cards = container.querySelector('#cp-cards');
    const posBody = container.querySelector('#cp-positions');
    try {
      const results = await Promise.all(CORE_ACCOUNTS.map(async (cfg) => {
        const [eq, pos] = await Promise.all([
          api.paperEquityCurve({ accountName: cfg.account, days: 180 }).catch(() => null),
          api.paperPositions({ market: cfg.market, accountName: cfg.account }).catch(() => []),
        ]);
        return { cfg, eq, pos: pos || [] };
      }));
      cards.innerHTML = results.map(r => _acctCard(r.cfg, r.eq)).join('');
      const allPos = results.flatMap(r => r.pos.map(p => ({ ...p, _ccy: r.cfg.ccy })));
      posBody.innerHTML = allPos.length
        ? allPos.map(p => _posRow(p, p._ccy)).join('')
        : `<tr><td colspan="7" class="empty-state text-muted">보유 종목이 없습니다. integrated.daily 실행 후 표시됩니다.</td></tr>`;
    } catch (e) {
      cards.innerHTML = `<div class="empty-state text-danger">불러오기 실패: ${Utils.escape(String(e.message || e))}</div>`;
    }
  }
  container.querySelector('#cp-refresh').addEventListener('click', load);
  await load();
}
