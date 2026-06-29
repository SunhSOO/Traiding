/**
 * 선정 (Selection Basket) — "어떤 종목을 살까".
 *
 * Middle layer: the cross-sectional alpha's ranked basket for the active
 * market, with conviction, predicted return, target price/band, equal
 * weight, and new/held/dropped diff vs the previous basket.
 */

function _convBadge(rankPct) {
  const p = Math.round((rankPct || 0) * 100);
  const cls = p >= 95 ? 'badge-success' : p >= 90 ? 'badge-secondary' : 'badge-muted';
  return `<span class="badge ${cls}">상위 ${(100 - p).toFixed(0)}%</span>`;
}

function _basketRow(n, i) {
  const pred = n.pred_ret_21d != null ? (n.pred_ret_21d * 100).toFixed(1) + '%' : '—';
  const predCls = (n.pred_ret_21d || 0) >= 0 ? 'text-success' : 'text-danger';
  const tgt = n.target_price != null ? Utils.formatNumber(n.target_price, 0) : '—';
  const band = (n.band_low != null && n.band_high != null)
    ? `${Utils.formatNumber(n.band_low, 0)}–${Utils.formatNumber(n.band_high, 0)}` : '—';
  const statusBadge = n.status === 'new'
    ? '<span class="badge badge-success">NEW</span>'
    : '<span class="badge badge-muted">보유</span>';
  return `
    <tr>
      <td class="text-muted">${i + 1}</td>
      <td><b>${Utils.escape(n.ticker)}</b></td>
      <td>${_convBadge(n.rank_pct)}</td>
      <td class="${predCls}">${pred}</td>
      <td class="text-right">${tgt}</td>
      <td class="text-right text-muted text-xs">${band}</td>
      <td class="text-right">${(n.target_weight * 100).toFixed(1)}%</td>
      <td>${statusBadge}</td>
    </tr>`;
}

async function renderBasket(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h2 class="text-xl">선정 바스켓</h2>
        <p class="text-sm text-muted">횡단면 알파가 고른 투자 종목 — 능동 인덱스(균등 비중). 매수 타이밍은 실행 단계에서 판단합니다</p>
      </div>
      <div class="page-header-actions" id="basket-tabs"></div>
    </div>
    <div class="ix-summary" id="basket-summary"></div>
    <div class="card animate-fade-in-up">
      <div class="card-header"><div class="card-title">바스켓 구성</div>
        <div class="card-subtitle text-xs text-muted" id="basket-asof">—</div></div>
      <div class="table-wrap">
        <table class="table table-compact">
          <thead><tr>
            <th>#</th><th>종목</th><th>확신도</th><th>예상수익 21d</th>
            <th class="text-right">목표가</th><th class="text-right">밴드</th>
            <th class="text-right">비중</th><th>상태</th>
          </tr></thead>
          <tbody id="basket-body"><tr><td colspan="8" class="empty-state text-muted">불러오는 중…</td></tr></tbody>
        </table>
      </div>
    </div>
    <div class="card" id="basket-dropped-card" style="display:none">
      <div class="card-header"><div class="card-title">제외된 종목 (전일 대비)</div></div>
      <div id="basket-dropped" class="ix-chips"></div>
    </div>`;

  async function load(market) {
    const body = container.querySelector('#basket-body');
    const summary = container.querySelector('#basket-summary');
    try {
      const data = await api.basket({ market });
      if (!data) {
        body.innerHTML = `<tr><td colspan="8" class="empty-state text-muted">바스켓이 아직 없습니다. integrated.daily 잡 실행 후 표시됩니다.</td></tr>`;
        summary.innerHTML = '';
        return;
      }
      container.querySelector('#basket-asof').textContent = `기준일 ${data.as_of}`;
      const expoPct = Math.round((data.target_exposure || 0) * 100);
      summary.innerHTML = `
        <div class="ix-summary-item"><div class="ix-summary-label">레짐</div><div class="ix-summary-val">${data.regime || '—'}</div></div>
        <div class="ix-summary-item"><div class="ix-summary-label">목표 노출</div><div class="ix-summary-val">${expoPct}%</div></div>
        <div class="ix-summary-item"><div class="ix-summary-label">바스켓 종목</div><div class="ix-summary-val">${data.names.length}</div></div>`;
      body.innerHTML = data.names.length
        ? data.names.map(_basketRow).join('')
        : `<tr><td colspan="8" class="empty-state text-muted">바스켓 비어 있음</td></tr>`;
      const dropCard = container.querySelector('#basket-dropped-card');
      if (data.dropped && data.dropped.length) {
        dropCard.style.display = '';
        container.querySelector('#basket-dropped').innerHTML =
          data.dropped.map(t => `<span class="ix-chip">${Utils.escape(t)}</span>`).join('');
      } else {
        dropCard.style.display = 'none';
      }
    } catch (e) {
      body.innerHTML = `<tr><td colspan="8" class="empty-state text-danger">불러오기 실패: ${Utils.escape(String(e.message || e))}</td></tr>`;
    }
  }

  MarketTab.mount({ parent: container.querySelector('#basket-tabs'), onChange: (m) => load(m) });
}
