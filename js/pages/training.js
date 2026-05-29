/**
 * Training / Models page.
 *
 * Visualises Phase 3 OLS output: per-cluster weights, in-sample +
 * walk-forward R², hit rate, ticker membership counts. Clicking a
 * cluster row opens a modal with the full weight history sparkline.
 *
 * This page does not have a KR/US tab — clusters are themselves
 * market-tagged (KR:TECH:LARGE, US:HEALTH:MID, ...). The table sort
 * already groups them visually.
 */

function renderTraining(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h2 class="text-xl">학습 결과</h2>
        <p class="text-sm text-muted">클러스터별 (F, T, I) 가중치 + walk-forward 평가 + 종목 멤버십</p>
      </div>
      <div class="page-header-actions">
        <button class="btn btn-secondary btn-sm" id="train-refresh">${Utils.icon('refresh-cw')} 새로고침</button>
        <button class="btn btn-primary btn-sm" id="train-run-now">${Utils.icon('zap')} 지금 학습</button>
      </div>
    </div>

    <div class="dashboard-stats stagger-children" id="train-summary">
      <div class="card text-muted">불러오는 중…</div>
    </div>

    <div class="card">
      <div class="card-header">
        <div class="card-title">클러스터별 가중치 (hit rate 내림차순)</div>
      </div>
      <div id="train-cluster-pane">
        <div class="empty-state text-muted">불러오는 중…</div>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <div>
          <div class="card-title">운영자 override</div>
          <div class="card-subtitle text-xs text-muted">
            학습된 weights를 클러스터 단위로 강제 변경합니다. 실제 결정 엔진이 다음 사이클부터 이 값을 사용합니다.
          </div>
        </div>
      </div>
      <div id="train-overrides-pane">
        <div class="empty-state text-muted">불러오는 중…</div>
      </div>
    </div>

    <div class="decision-modal" id="train-modal" hidden>
      <div class="decision-modal-backdrop" id="train-modal-backdrop"></div>
      <div class="decision-modal-body card" id="train-modal-body"></div>
    </div>
  `;

  const refreshBtn = container.querySelector('#train-refresh');
  const runNowBtn = container.querySelector('#train-run-now');
  const modalEl = container.querySelector('#train-modal');
  const modalBody = container.querySelector('#train-modal-body');
  const modalBackdrop = container.querySelector('#train-modal-backdrop');

  refreshBtn.addEventListener('click', reload);
  runNowBtn.addEventListener('click', runTrainingNow);
  modalBackdrop.addEventListener('click', closeModal);
  document.addEventListener('keydown', escListener);

  reload();

  async function reload() {
    await Promise.all([reloadSummary(), reloadClusters(), reloadOverrides()]);
  }

  async function reloadOverrides() {
    const pane = container.querySelector('#train-overrides-pane');
    try {
      const [overrides, clusters] = await Promise.all([
        Api.overridesList(),
        Api.trainingClusters(),
      ]);
      pane.innerHTML = renderOverridesPane(overrides, clusters);
      wireOverridesPane(pane);
    } catch (err) {
      pane.innerHTML = `<div class="empty-state text-loss">${escapeT(err.message)}</div>`;
    }
  }

  function wireOverridesPane(pane) {
    // Add new override
    pane.querySelector('#ov-add-btn')?.addEventListener('click', async () => {
      const clusterId = pane.querySelector('#ov-cluster').value;
      const f = Number(pane.querySelector('#ov-f').value);
      const t = Number(pane.querySelector('#ov-t').value);
      const i = Number(pane.querySelector('#ov-i').value);
      const reason = pane.querySelector('#ov-reason').value.trim();
      if (!clusterId) { alert('클러스터를 선택하세요.'); return; }
      if (!(f >= 0 && t >= 0 && i >= 0) || (f + t + i) <= 0) {
        alert('가중치는 0 이상이고 합이 양수여야 합니다.');
        return;
      }
      try {
        await Api.overridesSet(clusterId, {
          wFundamental: f, wTechnical: t, wInformation: i, reason: reason || null,
        });
        reloadOverrides();
      } catch (err) {
        alert('실패: ' + err.message);
      }
    });
    // Delete row
    pane.querySelectorAll('.ov-del-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        if (!confirm(`${btn.dataset.clusterId} override를 제거하시겠습니까?`)) return;
        try {
          await Api.overridesDelete(btn.dataset.clusterId);
          reloadOverrides();
        } catch (err) {
          alert('실패: ' + err.message);
        }
      });
    });
  }

  async function reloadSummary() {
    const pane = container.querySelector('#train-summary');
    try {
      const s = await Api.trainingSummary();
      if (!s.has_data) {
        pane.innerHTML = `
          <div class="card text-muted col-span-4">
            아직 학습된 모델이 없습니다. "지금 학습"을 눌러 첫 학습을 시작하거나
            매주 일요일 04:00 UTC의 자동 잡을 기다리세요.
          </div>`;
        return;
      }
      pane.innerHTML = `
        ${statCardT('마지막 학습', Utils.timeAgo(s.latest_learned_at) + ' 전',
          new Date(s.latest_learned_at).toLocaleString('ko-KR'), 'blue')}
        ${statCardT('학습된 클러스터', `${s.clusters_trained}`,
          '평균은 sector × size로 산출', 'purple')}
        ${statCardT('학습 샘플 수', Utils.formatNumber(s.total_samples),
          '(score × forward return) 쌍', 'cyan')}
        ${statCardT('현재 매핑 종목', `${s.total_tickers_now}`,
          s.model_versions.join(', '), 'green')}
      `;
    } catch (err) {
      pane.innerHTML = `<div class="card text-loss">${escapeT(err.message)}</div>`;
    }
  }

  async function reloadClusters() {
    const pane = container.querySelector('#train-cluster-pane');
    try {
      const rows = await Api.trainingClusters();
      if (!rows.length) {
        pane.innerHTML = `<div class="empty-state text-muted">학습된 클러스터가 없습니다.</div>`;
        return;
      }
      pane.innerHTML = `
        <table class="data-table train-cluster-table">
          <thead>
            <tr>
              <th>클러스터</th>
              <th>가중치 (F · T · I)</th>
              <th>샘플</th>
              <th>종목 (학습/현재)</th>
              <th>in-sample R²</th>
              <th>walk-forward R²</th>
              <th>hit rate</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            ${rows.map(r => `
              <tr class="train-cluster-row" data-cluster-id="${escapeT(r.cluster_id)}">
                <td><strong>${escapeT(r.cluster_id)}</strong>
                    <div class="text-xs text-muted">${Utils.timeAgo(r.learned_at)}</div></td>
                <td>${weightBars(r)}</td>
                <td class="mono">${Utils.formatNumber(r.n_samples)}</td>
                <td class="mono">${r.n_tickers_in_run} / ${r.n_tickers_now}</td>
                <td class="mono ${r2Class(r.r2_in_sample)}">${formatR2(r.r2_in_sample)}</td>
                <td class="mono ${r2Class(r.r2_walk_forward)}">${formatR2(r.r2_walk_forward)}</td>
                <td class="mono ${hitClass(r.hit_rate)}">${formatPct(r.hit_rate)}</td>
                <td><button class="btn btn-ghost btn-sm">히스토리</button></td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `;
      pane.querySelectorAll('.train-cluster-row').forEach(tr => {
        tr.style.cursor = 'pointer';
        tr.addEventListener('click', () => openHistory(tr.dataset.clusterId));
      });
    } catch (err) {
      pane.innerHTML = `<div class="text-loss">${escapeT(err.message)}</div>`;
    }
  }

  async function openHistory(clusterId) {
    modalEl.hidden = false;
    modalBody.innerHTML = `<div class="text-muted">불러오는 중…</div>`;
    try {
      const rows = await Api.trainingClusterHistory(clusterId, 50);
      modalBody.innerHTML = renderHistory(clusterId, rows);
      modalBody.querySelector('.train-modal-close').addEventListener('click', closeModal);
    } catch (err) {
      modalBody.innerHTML = `
        <div class="text-loss">${escapeT(err.message)}</div>
        <button class="btn btn-secondary train-modal-close" style="margin-top:12px">닫기</button>
      `;
      modalBody.querySelector('.train-modal-close').addEventListener('click', closeModal);
    }
  }

  function closeModal() {
    modalEl.hidden = true;
    modalBody.innerHTML = '';
  }

  function escListener(e) {
    if (e.key === 'Escape' && !modalEl.hidden) closeModal();
  }

  async function runTrainingNow() {
    if (!confirm('지금 학습을 트리거합니다 (수 분~수십 분 소요 가능). 계속할까요?')) return;
    runNowBtn.disabled = true;
    runNowBtn.textContent = '실행 중…';
    try {
      await Api.runJobNow('training.weekly');
      runNowBtn.textContent = '✓ 완료';
      setTimeout(reload, 1000);
    } catch (err) {
      runNowBtn.textContent = '✗ 실패';
      runNowBtn.title = err.message;
    } finally {
      setTimeout(() => {
        runNowBtn.disabled = false;
        runNowBtn.innerHTML = Utils.icon('zap') + ' 지금 학습';
      }, 2500);
    }
  }
}

// ── helpers ──

function weightBars(r) {
  const f = r.w_fundamental, t = r.w_technical, i = r.w_information;
  // Render as a stacked horizontal bar with three coloured segments
  // sized by their weight (sum = 1).
  return `
    <div class="train-weight-bar" title="F ${(f*100).toFixed(1)}% · T ${(t*100).toFixed(1)}% · I ${(i*100).toFixed(1)}%">
      <div class="train-weight-seg seg-f" style="width:${(f*100).toFixed(2)}%"></div>
      <div class="train-weight-seg seg-t" style="width:${(t*100).toFixed(2)}%"></div>
      <div class="train-weight-seg seg-i" style="width:${(i*100).toFixed(2)}%"></div>
    </div>
    <div class="train-weight-labels">
      <span class="seg-label seg-f-text">F ${(f*100).toFixed(0)}%</span>
      <span class="seg-label seg-t-text">T ${(t*100).toFixed(0)}%</span>
      <span class="seg-label seg-i-text">I ${(i*100).toFixed(0)}%</span>
    </div>
  `;
}

function formatR2(v) {
  if (v == null) return '—';
  return v.toFixed(3);
}
function formatPct(v) {
  if (v == null) return '—';
  return (v * 100).toFixed(1) + '%';
}
function r2Class(v) {
  if (v == null) return 'text-muted';
  if (v > 0.05) return 'text-profit';
  if (v > 0) return '';
  return 'text-loss';
}
function hitClass(v) {
  if (v == null) return 'text-muted';
  if (v > 0.55) return 'text-profit';
  if (v < 0.45) return 'text-loss';
  return '';
}

function renderHistory(clusterId, rows) {
  // Render a stacked weight evolution chart + sortable history table
  if (!rows.length) {
    return `
      <div class="text-muted">기록이 없습니다.</div>
      <button class="btn btn-secondary train-modal-close" style="margin-top:12px">닫기</button>`;
  }
  const sorted = rows.slice().sort((a, b) => a.learned_at.localeCompare(b.learned_at));
  const chart = renderWeightChart(sorted);
  return `
    <div class="dec-detail-header">
      <div>
        <div class="text-xl"><strong>${escapeT(clusterId)}</strong></div>
        <div class="text-xs text-muted">최근 ${rows.length}회 학습</div>
      </div>
      <button class="btn btn-ghost btn-sm train-modal-close">✕ 닫기</button>
    </div>

    <div class="dec-section">
      <h4>가중치 추이</h4>
      ${chart}
      <div class="train-chart-legend">
        <span class="seg-label seg-f-text">■ F</span>
        <span class="seg-label seg-t-text">■ T</span>
        <span class="seg-label seg-i-text">■ I</span>
      </div>
    </div>

    <div class="dec-section">
      <h4>회차별 결과</h4>
      <table class="data-table">
        <thead>
          <tr><th>학습 시각</th><th>F</th><th>T</th><th>I</th>
              <th>샘플</th><th>R²(in)</th><th>R²(WF)</th><th>hit</th></tr>
        </thead>
        <tbody>
          ${rows.map(r => `
            <tr>
              <td class="text-xs">${escapeT(new Date(r.learned_at).toLocaleString('ko-KR'))}</td>
              <td class="mono">${(r.w_fundamental*100).toFixed(1)}%</td>
              <td class="mono">${(r.w_technical*100).toFixed(1)}%</td>
              <td class="mono">${(r.w_information*100).toFixed(1)}%</td>
              <td class="mono">${Utils.formatNumber(r.n_samples)}</td>
              <td class="mono ${r2Class((r.metrics||{}).r2_in_sample)}">${formatR2((r.metrics||{}).r2_in_sample)}</td>
              <td class="mono ${r2Class((r.metrics||{}).r2_walk_forward)}">${formatR2((r.metrics||{}).r2_walk_forward)}</td>
              <td class="mono ${hitClass((r.metrics||{}).hit_rate)}">${formatPct((r.metrics||{}).hit_rate)}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
}

function renderWeightChart(sorted) {
  // Render a stacked area chart of F/T/I weights over time
  if (sorted.length < 2) {
    return `<div class="text-muted">기록이 2회 이상이어야 추이를 표시합니다.</div>`;
  }
  const w = 640, h = 160, pad = 8;
  const n = sorted.length;
  const xs = sorted.map((_, i) => pad + (i / (n - 1)) * (w - 2 * pad));
  // Build per-segment polygon points
  const ys = (top, bot) => sorted.map((r, i) => {
    return `${xs[i].toFixed(1)},${(h - pad - bot[i] * (h - 2 * pad)).toFixed(1)}`;
  });
  const fSeries = sorted.map(r => r.w_fundamental);
  const tSeries = sorted.map(r => r.w_technical);
  const iSeries = sorted.map(r => r.w_information);

  // Stacked tops (cumulative)
  const fTop = fSeries.map((v) => v);
  const tTop = fSeries.map((v, i) => v + tSeries[i]);
  const iTop = fSeries.map((v, i) => v + tSeries[i] + iSeries[i]);
  const base = sorted.map(() => 0);

  const poly = (top, bot, klass) => {
    const upper = sorted.map((_, i) =>
      `${xs[i].toFixed(1)},${(h - pad - top[i] * (h - 2 * pad)).toFixed(1)}`
    );
    const lower = sorted.map((_, i) =>
      `${xs[i].toFixed(1)},${(h - pad - bot[i] * (h - 2 * pad)).toFixed(1)}`
    ).reverse();
    return `<polygon class="${klass}" points="${[...upper, ...lower].join(' ')}"/>`;
  };

  return `
    <svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" class="train-stack-chart" preserveAspectRatio="none">
      ${poly(fTop, base, 'stack-f')}
      ${poly(tTop, fTop, 'stack-t')}
      ${poly(iTop, tTop, 'stack-i')}
    </svg>
  `;
}

function statCardT(label, value, footer, glow) {
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

function renderOverridesPane(overrides, clusters) {
  const overriddenIds = new Set(overrides.map(o => o.cluster_id));
  const candidates = clusters.filter(c => !overriddenIds.has(c.cluster_id));
  return `
    <div class="ov-add-row">
      <div class="form-row">
        <label>클러스터</label>
        <select class="input" id="ov-cluster">
          <option value="">선택…</option>
          ${candidates.map(c => `<option value="${escapeT(c.cluster_id)}">${escapeT(c.cluster_id)} (${c.n_tickers_now} 종목)</option>`).join('')}
        </select>
      </div>
      <div class="form-row"><label>F</label><input type="number" class="input" id="ov-f" min="0" max="1" step="0.05" value="0.35"></div>
      <div class="form-row"><label>T</label><input type="number" class="input" id="ov-t" min="0" max="1" step="0.05" value="0.40"></div>
      <div class="form-row"><label>I</label><input type="number" class="input" id="ov-i" min="0" max="1" step="0.05" value="0.25"></div>
      <div class="form-row" style="flex:1">
        <label>이유</label>
        <input type="text" class="input" id="ov-reason" placeholder="왜 override? (감사 기록)">
      </div>
      <div class="form-row">
        <label>&nbsp;</label>
        <button class="btn btn-primary" id="ov-add-btn">${Utils.icon('plus')} 추가/덮어쓰기</button>
      </div>
    </div>

    ${overrides.length ? `
      <table class="data-table" style="margin-top: var(--space-3)">
        <thead>
          <tr><th>클러스터</th><th>F / T / I</th><th>이유</th><th>설정자</th><th>설정 시각</th><th></th></tr>
        </thead>
        <tbody>
          ${overrides.map(o => `
            <tr>
              <td><strong>${escapeT(o.cluster_id)}</strong></td>
              <td class="mono">
                <span class="text-accent-blue">${(o.w_fundamental * 100).toFixed(0)}%</span> /
                <span class="text-accent-purple">${(o.w_technical * 100).toFixed(0)}%</span> /
                <span style="color:#06b6d4">${(o.w_information * 100).toFixed(0)}%</span>
              </td>
              <td class="text-xs text-muted">${escapeT(o.reason || '—')}</td>
              <td class="text-xs">${escapeT(o.set_by)}</td>
              <td class="text-xs text-muted">${Utils.timeAgo(o.set_ts)} 전</td>
              <td>
                <button class="btn btn-secondary btn-sm ov-del-btn" data-cluster-id="${escapeT(o.cluster_id)}">
                  ${Utils.icon('x')} 해제
                </button>
              </td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    ` : '<div class="empty-state text-muted" style="margin-top: var(--space-3)">현재 활성 override 없음 — 모든 클러스터가 학습된 weights 사용 중.</div>'}
  `;
}

function escapeT(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

window.renderTraining = renderTraining;
