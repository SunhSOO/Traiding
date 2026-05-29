/**
 * Decision Audit page.
 *
 * Lists recent decisions (filtered by current KR/US market), each
 * clickable for a detail modal that shows:
 *   - per-module F/T/I scores + confidences + weights used
 *   - which gates failed (if HOLD)
 *   - risk snapshot (which of the 6 limits passed)
 *   - execution result (paper broker fill, or rejection reason)
 *
 * This is the operator's primary tool for understanding "why did the
 * system trade / not trade ticker X at time T".
 */

function renderDecisionAudit(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h2 class="text-xl">결정 감사 로그</h2>
        <p class="text-sm text-muted">composite 점수, 게이트, 리스크 검사, 실행 결과까지 한 결정의 전체 추적</p>
      </div>
      <div class="page-header-actions" id="dec-header-actions"></div>
    </div>

    <div class="card">
      <div class="card-header">
        <div class="filter-row">
          <select class="select" id="dec-filter-action">
            <option value="">모든 액션</option>
            <option value="BUY">BUY</option>
            <option value="SELL">SELL</option>
            <option value="HOLD">HOLD</option>
            <option value="REJECTED">REJECTED</option>
          </select>
          <input class="input" id="dec-filter-ticker" placeholder="종목 (예: 005930, AAPL)" style="width:200px">
          <button class="btn btn-secondary btn-sm" id="dec-refresh">${Utils.icon('refresh-cw')} 새로고침</button>
        </div>
      </div>
      <div class="decision-list" id="dec-list">
        <div class="empty-state"><div class="text-muted">결정을 불러오는 중...</div></div>
      </div>
    </div>

    <!-- Detail modal -->
    <div class="decision-modal" id="dec-modal" hidden>
      <div class="decision-modal-backdrop" id="dec-modal-backdrop"></div>
      <div class="decision-modal-body card" id="dec-modal-body"></div>
    </div>
  `;

  // Mount KR/US tab into the page header
  MarketTab.mount({
    parent: container.querySelector('#dec-header-actions'),
    onChange: () => reload(),
  });

  const listEl = container.querySelector('#dec-list');
  const actionFilter = container.querySelector('#dec-filter-action');
  const tickerFilter = container.querySelector('#dec-filter-ticker');
  const refreshBtn = container.querySelector('#dec-refresh');
  const modalEl = container.querySelector('#dec-modal');
  const modalBody = container.querySelector('#dec-modal-body');
  const modalBackdrop = container.querySelector('#dec-modal-backdrop');

  refreshBtn.addEventListener('click', reload);
  actionFilter.addEventListener('change', reload);
  tickerFilter.addEventListener('input', Utils.debounce(reload, 400));
  modalBackdrop.addEventListener('click', closeModal);
  document.addEventListener('keydown', escListener);

  reload();

  async function reload() {
    listEl.innerHTML = `<div class="empty-state"><div class="text-muted">불러오는 중…</div></div>`;
    try {
      const rows = await Api.recentDecisions({
        market: AppState.getMarket(),
        ticker: tickerFilter.value.trim() || undefined,
        action: actionFilter.value || undefined,
        limit: 100,
      });
      renderList(rows);
    } catch (err) {
      listEl.innerHTML = `
        <div class="empty-state">
          <div class="text-loss">결정을 불러오지 못했습니다: ${escape(err.message)}</div>
        </div>`;
    }
  }

  function renderList(rows) {
    if (!rows.length) {
      listEl.innerHTML = `
        <div class="empty-state">
          <div style="font-size:36px;margin-bottom:8px">📋</div>
          <div class="empty-state-title">결정이 없습니다</div>
          <div class="text-xs text-muted">스케줄러의 decisions.daily 잡이 한 번 돌면 채워집니다.</div>
        </div>`;
      return;
    }
    listEl.innerHTML = `
      <table class="data-table decision-table">
        <thead>
          <tr>
            <th>시각</th>
            <th>종목</th>
            <th>액션</th>
            <th>composite</th>
            <th>confidence</th>
            <th>size</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${rows.map(r => `
            <tr data-decision-id="${escape(r.id)}" data-market="${escape(r.market)}" data-ticker="${escape(r.ticker)}" class="decision-row">
              <td class="text-xs">${escape(Utils.formatDateTime(r.decision_ts))}</td>
              <td>
                <a class="decision-ticker-link" href="#${escape(AppRouter.pathWithQuery('/analysis', { market: r.market, ticker: r.ticker }))}"
                   onclick="event.stopPropagation()">
                  ${r.market}:${escape(r.ticker)}
                </a>
              </td>
              <td><span class="badge ${actionBadgeClass(r.action)}">${r.action}</span></td>
              <td class="mono ${(r.composite_score ?? 0) >= 0 ? 'text-profit' : 'text-loss'}">
                ${r.composite_score == null ? '—' : r.composite_score.toFixed(1)}
              </td>
              <td class="mono">${r.composite_confidence == null ? '—' : (r.composite_confidence * 100).toFixed(0) + '%'}</td>
              <td class="mono">${r.size_value == null ? '—' : Utils.formatNumber(r.size_value)}</td>
              <td><button class="btn btn-ghost btn-sm">상세</button></td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
    listEl.querySelectorAll('.decision-row').forEach(tr => {
      tr.addEventListener('click', () => openDetail(tr.dataset.decisionId));
    });
  }

  async function openDetail(id) {
    modalEl.hidden = false;
    modalBody.innerHTML = `<div class="text-muted">불러오는 중…</div>`;
    try {
      const d = await Api.decisionDetail(id);
      modalBody.innerHTML = renderDetail(d);
      modalBody.querySelector('.decision-modal-close')
        .addEventListener('click', closeModal);
    } catch (err) {
      modalBody.innerHTML = `
        <div class="text-loss">상세를 불러오지 못했습니다: ${escape(err.message)}</div>
        <button class="btn btn-secondary decision-modal-close" style="margin-top:12px">닫기</button>
      `;
      modalBody.querySelector('.decision-modal-close').addEventListener('click', closeModal);
    }
  }

  function closeModal() {
    modalEl.hidden = true;
    modalBody.innerHTML = '';
  }

  function escListener(e) {
    if (e.key === 'Escape' && !modalEl.hidden) closeModal();
  }
}

// ── helpers ──

function actionBadgeClass(action) {
  switch (action) {
    case 'BUY': return 'badge-profit';
    case 'SELL': return 'badge-loss';
    case 'REJECTED': return 'badge-loss';
    case 'HOLD': return 'badge-neutral';
    default: return 'badge-info';
  }
}

function renderDetail(d) {
  const moduleBlock = (label, score, conf) => `
    <div class="dec-module-card">
      <div class="dec-module-label">${label}</div>
      <div class="dec-module-score ${(score ?? 0) >= 0 ? 'text-profit' : 'text-loss'}">
        ${score == null ? '—' : score.toFixed(1)}
      </div>
      <div class="dec-module-conf">conf ${conf == null ? '—' : (conf * 100).toFixed(0) + '%'}</div>
    </div>
  `;

  return `
    <div class="dec-detail">
      <div class="dec-detail-header">
        <div>
          <div class="text-xl"><strong>${d.market}:${escape(d.ticker)}</strong>
            <span class="badge ${actionBadgeClass(d.action)}" style="margin-left:8px">${d.action}</span>
          </div>
          <div class="text-xs text-muted">${escape(Utils.formatDateTime(d.decision_ts))}</div>
        </div>
        <button class="btn btn-ghost btn-sm decision-modal-close">✕ 닫기</button>
      </div>

      ${d.reason ? `
        <div class="dec-reason">
          <strong>왜 이 결정?</strong>
          <span>${escape(d.reason)}</span>
        </div>
      ` : ''}

      <div class="dec-modules">
        ${moduleBlock('기본 (F)', d.fundamental_score, d.fundamental_confidence)}
        ${moduleBlock('기술 (T)', d.technical_score, d.technical_confidence)}
        ${moduleBlock('정보 (I)', d.information_score, d.information_confidence)}
        ${moduleBlock('Composite', d.composite_score, d.composite_confidence)}
      </div>

      ${renderContribBar(d.contributions, d.composite_score)}

      <div class="dec-section">
        <h4>가중치 ${d.model_version ? `<span class="text-xs text-muted">· ${escape(d.model_version)}</span>` : ''}</h4>
        ${renderWeights(d.weights)}
      </div>

      <div class="dec-section">
        <h4>게이트 결과</h4>
        ${renderGates(d.gate_results)}
      </div>

      ${d.risk_snapshot ? `
        <div class="dec-section">
          <h4>리스크 검사</h4>
          <div class="dec-risk-grid">
            ${riskRow('최대 lot', d.risk_snapshot.max_lot_pass)}
            ${riskRow('일 손실', d.risk_snapshot.daily_loss_pass)}
            ${riskRow('연속 손실', d.risk_snapshot.consecutive_loss_pass)}
            ${riskRow('최대 포지션', d.risk_snapshot.max_positions_pass)}
            ${riskRow('스프레드', d.risk_snapshot.spread_pass)}
            ${riskRow('심볼 허용', d.risk_snapshot.symbol_allowed_pass)}
          </div>
          ${d.risk_snapshot.failures && d.risk_snapshot.failures.length
            ? `<pre class="dec-pre">${escape(JSON.stringify(d.risk_snapshot.failures, null, 2))}</pre>`
            : ''}
        </div>
      ` : ''}

      <div class="dec-section">
        <h4>사이즈</h4>
        <div>${d.size_value == null ? '—' : `${Utils.formatNumber(d.size_value)} ${d.size_currency || ''}`}</div>
      </div>

      ${d.inputs_snapshot ? `
        <details class="dec-section">
          <summary><h4 style="display:inline-block; margin:0">입력 스냅샷 (raw inputs)</h4></summary>
          <pre class="dec-pre">${escape(JSON.stringify(d.inputs_snapshot, null, 2))}</pre>
        </details>
      ` : ''}

      ${d.execution_result ? `
        <div class="dec-section">
          <h4>실행 결과</h4>
          <pre class="dec-pre">${escape(JSON.stringify(d.execution_result, null, 2))}</pre>
        </div>
      ` : ''}

      ${d.error ? `
        <div class="dec-section">
          <h4 class="text-loss">에러</h4>
          <pre class="dec-pre">${escape(d.error)}</pre>
        </div>
      ` : ''}
    </div>
  `;
}

function riskRow(label, passed) {
  return `<div class="dec-risk-row ${passed ? 'pass' : 'fail'}">
    ${passed ? '✓' : '✗'} <span>${label}</span>
  </div>`;
}

function renderWeights(weights) {
  if (!weights || typeof weights !== 'object') {
    return `<div class="text-xs text-muted">가중치 정보 없음</div>`;
  }
  const order = ['F', 'T', 'I'];
  const labels = { F: '기본 (F)', T: '기술 (T)', I: '정보 (I)' };
  const total = order.reduce((s, k) => s + (Number(weights[k]) || 0), 0) || 1;
  return `
    <div class="dec-weights-grid">
      ${order.map(k => {
        const w = Number(weights[k]) || 0;
        const pct = (w / total * 100).toFixed(1);
        return `
          <div class="dec-weight-cell weight-${k.toLowerCase()}">
            <div class="dec-weight-bar">
              <div class="dec-weight-bar-fill" style="width:${pct}%"></div>
            </div>
            <div class="text-xs"><strong>${labels[k]}</strong> ${pct}%</div>
          </div>
        `;
      }).join('')}
    </div>
  `;
}

function renderGates(gates) {
  if (!gates || typeof gates !== 'object' || !Object.keys(gates).length) {
    return `<div class="text-xs text-muted">게이트 정보 없음</div>`;
  }
  const rows = Object.entries(gates).map(([name, info]) => {
    if (info && typeof info === 'object' && 'passed' in info) {
      return { name, passed: info.passed, detail: info.reason || info.message || null };
    }
    return { name, passed: null, detail: typeof info === 'string' ? info : null };
  });
  return `
    <div class="dec-gates-list">
      ${rows.map(r => `
        <div class="dec-gate-row ${r.passed === true ? 'pass' : r.passed === false ? 'fail' : ''}">
          <span>${r.passed === true ? '✓' : r.passed === false ? '✗' : '·'}</span>
          <strong>${escape(r.name)}</strong>
          ${r.detail ? `<span class="text-xs text-muted">${escape(r.detail)}</span>` : ''}
        </div>
      `).join('')}
    </div>
  `;
}

function renderContribBar(contributions, compositeScore) {
  if (!contributions || !contributions.length) return '';
  // Show each module's contribution = score × weight, side-by-side.
  // Positive contributions sit above the zero line, negative below.
  const W = 700, H = 130, PAD_L = 40, PAD_R = 12, PAD_T = 10, PAD_B = 26;
  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;

  // Y scale is fixed to [-100, 100] (max possible contribution = 100·1.0).
  const yMin = -100, yMax = 100;
  const scaleY = (v) => PAD_T + innerH * (1 - (v - yMin) / (yMax - yMin));
  const barWidth = innerW / contributions.length * 0.55;
  const cellWidth = innerW / contributions.length;
  const zeroY = scaleY(0);

  const labels = { F: '기본', T: '기술', I: '정보' };
  const colors = { F: 'var(--accent-blue)', T: 'var(--accent-purple)', I: '#06b6d4' };

  const bars = contributions.map((c, i) => {
    const x = PAD_L + i * cellWidth + (cellWidth - barWidth) / 2;
    const y0 = scaleY(0);
    const yV = scaleY(c.contribution);
    const top = Math.min(y0, yV);
    const height = Math.abs(yV - y0);
    return `
      <rect x="${x.toFixed(1)}" y="${top.toFixed(1)}" width="${barWidth.toFixed(1)}" height="${height.toFixed(1)}"
            fill="${colors[c.module] || '#888'}" opacity="0.85"/>
      <text x="${(x + barWidth / 2).toFixed(1)}" y="${(top - 4).toFixed(1)}"
            text-anchor="middle" class="contrib-value">
        ${c.contribution >= 0 ? '+' : ''}${c.contribution.toFixed(1)}
      </text>
      <text x="${(x + barWidth / 2).toFixed(1)}" y="${H - 8}"
            text-anchor="middle" class="contrib-label">
        ${labels[c.module] || c.module} (${c.score.toFixed(0)}×${c.weight.toFixed(2)})
      </text>
    `;
  }).join('');

  // Zero axis + y ticks at -50, 0, 50
  const yticks = [-100, -50, 0, 50, 100].map(v => {
    const y = scaleY(v);
    return `
      <line x1="${PAD_L}" x2="${W - PAD_R}" y1="${y.toFixed(1)}" y2="${y.toFixed(1)}"
            class="contrib-gridline ${v === 0 ? 'contrib-zero' : ''}"/>
      <text x="${PAD_L - 6}" y="${(y + 3).toFixed(1)}" text-anchor="end" class="contrib-ytick">${v}</text>
    `;
  }).join('');

  return `
    <div class="dec-section">
      <h4>모듈별 기여 (score × weight)</h4>
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" class="contrib-chart">
        ${yticks}
        ${bars}
      </svg>
      ${compositeScore != null ? `
        <div class="text-xs text-muted" style="text-align:center; margin-top:4px">
          합 = composite ${compositeScore >= 0 ? '+' : ''}${compositeScore.toFixed(1)}
        </div>
      ` : ''}
    </div>
  `;
}

function escape(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

window.renderDecisionAudit = renderDecisionAudit;
