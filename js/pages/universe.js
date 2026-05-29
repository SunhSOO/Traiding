/**
 * Universe page — securities explorer with server-side filters.
 *
 * Two tabs:
 *   - 종목 목록: paged list with search + sector/index/exchange/cluster filters,
 *     each row shows the learned F/T/I weights for that ticker's cluster.
 *   - 지수 구성: per-index member list with date overlay.
 */

function renderUniverse(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h2 class="text-xl">종목 마스터</h2>
        <p class="text-sm text-muted">활성 종목 + 클러스터 / 가중치 + 지수 구성</p>
      </div>
      <div class="page-header-actions" id="univ-header-actions"></div>
    </div>

    <div class="card">
      <div class="card-header">
        <div class="tabs">
          <div class="tab active" data-univ-tab="securities">종목 목록</div>
          <div class="tab" data-univ-tab="membership">지수 구성</div>
        </div>
      </div>

      <div id="univ-filters" class="univ-filters">
        <input class="input" id="univ-search" placeholder="이름 또는 티커 검색" style="width:240px">
        <select class="input" id="univ-sector"><option value="">모든 섹터</option></select>
        <select class="input" id="univ-index"><option value="">모든 지수</option></select>
        <select class="input" id="univ-exchange"><option value="">모든 거래소</option></select>
        <select class="input" id="univ-cluster"><option value="">모든 클러스터</option></select>
        <button class="btn btn-secondary btn-sm" id="univ-clear-filters">${Utils.icon('x')} 초기화</button>
      </div>

      <div id="univ-pane"></div>
      <div class="univ-pager" id="univ-pager"></div>
    </div>
  `;

  let currentMarket = AppState.getMarket();
  let currentTab = 'securities';
  let offset = 0;
  const limit = 50;
  let facets = { sectors: [], indices: [], exchanges: [] };
  let knownClusters = [];

  MarketTab.mount({
    parent: container.querySelector('#univ-header-actions'),
    onChange: (m) => { currentMarket = m; offset = 0; loadFacets().then(render); },
  });

  container.querySelectorAll('.tabs .tab').forEach(t => {
    t.addEventListener('click', () => {
      container.querySelectorAll('.tabs .tab').forEach(x => x.classList.remove('active'));
      t.classList.add('active');
      currentTab = t.dataset.univTab;
      container.querySelector('#univ-filters').style.display = currentTab === 'securities' ? '' : 'none';
      container.querySelector('#univ-pager').style.display = currentTab === 'securities' ? '' : 'none';
      offset = 0;
      render();
    });
  });

  // Filter inputs trigger reload
  ['univ-search', 'univ-sector', 'univ-index', 'univ-exchange', 'univ-cluster'].forEach(id => {
    const el = container.querySelector('#' + id);
    const evt = el.tagName === 'INPUT' ? 'input' : 'change';
    const handler = () => { offset = 0; render(); };
    el.addEventListener(evt, Utils.debounce(handler, 250));
  });
  container.querySelector('#univ-clear-filters').addEventListener('click', () => {
    ['univ-search', 'univ-sector', 'univ-index', 'univ-exchange', 'univ-cluster'].forEach(id => {
      container.querySelector('#' + id).value = '';
    });
    offset = 0;
    render();
  });

  loadFacets().then(render);

  async function loadFacets() {
    try {
      const f = await Api.universeFacets({ market: currentMarket });
      facets = f;
      populateSelect(container.querySelector('#univ-sector'), f.sectors, '모든 섹터');
      populateSelect(container.querySelector('#univ-index'), f.indices, '모든 지수');
      populateSelect(container.querySelector('#univ-exchange'), f.exchanges, '모든 거래소');
    } catch (err) {
      // Non-fatal — keep working without facets
      console.warn('facets failed', err);
    }
    try {
      const clusters = await Api.trainingClusters();
      knownClusters = clusters;
      populateSelect(
        container.querySelector('#univ-cluster'),
        clusters.map(c => c.cluster_id),
        '모든 클러스터',
      );
    } catch (err) {
      console.warn('clusters failed', err);
    }
  }

  function populateSelect(el, values, placeholder) {
    const cur = el.value;
    el.innerHTML = `<option value="">${placeholder}</option>`
      + values.map(v => `<option value="${escape(v)}">${escape(v)}</option>`).join('');
    el.value = values.includes(cur) ? cur : '';
  }

  async function render() {
    const pane = container.querySelector('#univ-pane');
    const pager = container.querySelector('#univ-pager');
    pane.innerHTML = `<div class="empty-state"><div class="text-muted">불러오는 중…</div></div>`;
    pager.innerHTML = '';
    try {
      if (currentTab === 'securities') {
        const search = container.querySelector('#univ-search').value.trim() || null;
        const sector = container.querySelector('#univ-sector').value || null;
        const idx = container.querySelector('#univ-index').value || null;
        const exch = container.querySelector('#univ-exchange').value || null;
        const cluster = container.querySelector('#univ-cluster').value || null;

        const data = await Api.listSecurities({
          market: currentMarket, search, sector, indexCode: idx,
          exchange: exch, clusterId: cluster,
          limit, offset, withCluster: true,
        });
        renderSecurities(pane, data.rows);
        pager.innerHTML = renderPager(data.total, offset, limit);
        pager.querySelector('[data-action="prev"]')?.addEventListener('click', () => {
          offset = Math.max(0, offset - limit); render();
        });
        pager.querySelector('[data-action="next"]')?.addEventListener('click', () => {
          offset += limit; render();
        });
      } else {
        const indices = currentMarket === 'KR' ? ['KOSPI200', 'KOSDAQ150'] : ['SP500', 'NASDAQ100'];
        const groups = await Promise.all(
          indices.map(code => Api.listIndexMembership(code).then(rows => ({ code, rows })))
        );
        renderMembership(pane, groups);
      }
    } catch (err) {
      pane.innerHTML = `
        <div class="empty-state">
          <div class="text-loss">불러오지 못했습니다: ${escape(err.message)}</div>
          <div class="text-xs text-muted" style="margin-top:8px">universe 잡이 한 번 돌아야 데이터가 채워집니다.</div>
        </div>`;
    }
  }

  function renderSecurities(pane, rows) {
    if (!rows.length) {
      pane.innerHTML = `<div class="empty-state"><div class="text-muted">조건에 맞는 종목이 없습니다.</div></div>`;
      return;
    }
    pane.innerHTML = `
      <div style="overflow-x:auto">
        <table class="data-table">
          <thead>
            <tr>
              <th>티커</th>
              <th>이름</th>
              <th>거래소</th>
              <th>지수</th>
              <th>섹터</th>
              <th>클러스터</th>
              <th>F / T / I 가중치</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            ${rows.map(r => `
              <tr>
                <td>
                  <strong>${escape(r.ticker)}</strong>
                  <span class="badge badge-info" style="margin-left:6px">${escape(r.market)}</span>
                </td>
                <td>${escape(r.name || '')}</td>
                <td>${escape(r.exchange || '—')}</td>
                <td class="text-xs">${escape(r.index_membership || '—')}</td>
                <td>${escape(r.sector || '—')}</td>
                <td class="text-xs">${r.cluster_id ? escape(r.cluster_id) : '<span class="text-muted">—</span>'}</td>
                <td>${renderWeightCell(r)}</td>
                <td>
                  <a class="btn btn-ghost btn-sm" href="#${escape(AppRouter.pathWithQuery('/analysis', { market: r.market, ticker: r.ticker }))}">
                    ${Utils.icon('search')} 분석
                  </a>
                </td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `;
  }

  function renderWeightCell(r) {
    if (r.w_fundamental == null) {
      return '<span class="text-xs text-muted">학습 대기</span>';
    }
    const total = (r.w_fundamental + r.w_technical + r.w_information) || 1;
    const pf = (r.w_fundamental / total * 100).toFixed(0);
    const pt = (r.w_technical / total * 100).toFixed(0);
    const pi = (r.w_information / total * 100).toFixed(0);
    return `
      <div class="univ-wbar">
        <span class="univ-wseg seg-f" style="width:${pf}%" title="F ${pf}%"></span>
        <span class="univ-wseg seg-t" style="width:${pt}%" title="T ${pt}%"></span>
        <span class="univ-wseg seg-i" style="width:${pi}%" title="I ${pi}%"></span>
      </div>
      <div class="text-xs text-muted univ-wlabels">
        <span class="seg-f-text">${pf}%</span> ·
        <span class="seg-t-text">${pt}%</span> ·
        <span class="seg-i-text">${pi}%</span>
      </div>
    `;
  }

  function renderMembership(pane, groups) {
    pane.innerHTML = groups.map(({ code, rows }) => `
      <div class="univ-index-group">
        <div class="univ-index-header">
          <strong>${escape(code)}</strong>
          <span class="text-xs text-muted">현재 ${rows.length} 종목</span>
        </div>
        <div class="univ-index-grid">
          ${rows.map(r => `
            <div class="univ-index-cell">
              <div class="mono">${escape(r.ticker)}</div>
              <div class="text-xs text-muted">${escape(r.valid_from)}~</div>
            </div>
          `).join('')}
        </div>
      </div>
    `).join('');
  }

  function renderPager(total, offset, limit) {
    const page = Math.floor(offset / limit) + 1;
    const totalPages = Math.max(1, Math.ceil(total / limit));
    return `
      <div class="univ-pager-row">
        <button class="btn btn-secondary btn-sm" data-action="prev" ${offset <= 0 ? 'disabled' : ''}>← 이전</button>
        <span class="text-xs text-muted">${page} / ${totalPages} (총 ${total}개)</span>
        <button class="btn btn-secondary btn-sm" data-action="next" ${offset + limit >= total ? 'disabled' : ''}>다음 →</button>
      </div>
    `;
  }
}

function escape(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

window.renderUniverse = renderUniverse;
