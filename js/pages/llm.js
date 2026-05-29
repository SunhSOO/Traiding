/**
 * LLM provider status + classification queue.
 *
 * Polls /api/llm/health on a 60s timer. Shows:
 *   - Provider strip: Ollama / Groq / Gemini cards (reachable / latency / models)
 *   - Queue card: pending vs classified, 24h/7d throughput, error rate, median latency
 *   - Model-version mix: which models are doing the work right now
 */

function renderLLM(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h2 class="text-xl">LLM 상태</h2>
        <p class="text-sm text-muted">분류 처리량 + provider 연결 헬스 + 모델 버전 분포</p>
      </div>
      <div class="page-header-actions">
        <button class="btn btn-secondary btn-sm" id="llm-refresh">${Utils.icon('refresh-cw')} 새로고침</button>
      </div>
    </div>

    <div class="card animate-fade-in-up">
      <div class="card-header"><div class="card-title">LLM Provider</div></div>
      <div id="llm-providers-pane"><div class="empty-state text-muted">불러오는 중…</div></div>
    </div>

    <div class="card animate-fade-in-up">
      <div class="card-header">
        <div>
          <div class="card-title">분류 큐</div>
          <div class="card-subtitle text-xs text-muted" id="llm-queue-sub">7일 윈도</div>
        </div>
      </div>
      <div id="llm-queue-pane"><div class="empty-state text-muted">불러오는 중…</div></div>
    </div>

    <div class="card animate-fade-in-up">
      <div class="card-header"><div class="card-title">모델 버전 분포 (최근 7일)</div></div>
      <div id="llm-versions-pane"><div class="empty-state text-muted">불러오는 중…</div></div>
    </div>
  `;

  container.querySelector('#llm-refresh').addEventListener('click', reload);
  reload();
  const intervalId = setInterval(reload, 60_000);
  AppRouter.beforeEach(() => { clearInterval(intervalId); return true; });

  async function reload() {
    try {
      const data = await Api.llmHealth({ windowDays: 7 });
      renderProviders(container.querySelector('#llm-providers-pane'), data.providers);
      renderQueue(container.querySelector('#llm-queue-pane'), data.queue, data.default_model);
      renderVersions(container.querySelector('#llm-versions-pane'), data.model_versions);
    } catch (err) {
      container.querySelector('#llm-providers-pane').innerHTML =
        `<div class="empty-state text-loss">${escape(err.message)}</div>`;
    }
  }

  function renderProviders(pane, providers) {
    pane.innerHTML = `
      <div class="llm-provider-grid">
        ${providers.map(providerCard).join('')}
      </div>
    `;
  }

  function providerCard(p) {
    let badge;
    if (!p.configured) {
      badge = `<span class="badge badge-neutral">미설정</span>`;
    } else if (p.reachable === true) {
      badge = `<span class="badge badge-profit">정상</span>`;
    } else if (p.reachable === false) {
      badge = `<span class="badge badge-loss">연결 실패</span>`;
    } else {
      badge = `<span class="badge badge-info">설정됨</span>`;
    }
    const latencyChip = p.latency_ms != null
      ? `<span class="text-xs text-muted">${p.latency_ms.toFixed(0)}ms</span>`
      : '';
    return `
      <div class="llm-provider-card">
        <div class="llm-provider-header">
          <strong>${escape(p.name.toUpperCase())}</strong>
          ${badge}
        </div>
        <div class="text-xs text-muted">${escape(p.detail || '—')} ${latencyChip}</div>
        ${p.models && p.models.length ? `
          <div class="llm-provider-models">
            ${p.models.slice(0, 8).map(m => `<span class="badge badge-info">${escape(m)}</span>`).join('')}
            ${p.models.length > 8 ? `<span class="text-xs text-muted">+${p.models.length - 8}</span>` : ''}
          </div>
        ` : ''}
        ${p.error ? `<div class="text-xs text-loss" style="margin-top:6px">${escape(p.error)}</div>` : ''}
      </div>
    `;
  }

  function renderQueue(pane, q, defaultModel) {
    const progressPct = q.articles_total
      ? (q.articles_classified / q.articles_total * 100).toFixed(1)
      : '0.0';
    const errPct = (q.error_rate_24h * 100).toFixed(1);
    pane.innerHTML = `
      <div class="llm-queue-grid">
        ${queueStat('분류 완료', q.articles_classified, `${progressPct}%`, 'green')}
        ${queueStat('분류 대기', q.articles_pending, '큐 깊이', 'blue')}
        ${queueStat('24h 처리량', q.classified_24h, `7d ${q.classified_7d}`, 'purple')}
        ${queueStat('24h 에러', q.errors_24h, `에러율 ${errPct}%`, q.errors_24h > 0 ? 'red' : 'green')}
      </div>
      <div class="llm-progress-bar">
        <div class="llm-progress-fill" style="width:${progressPct}%"></div>
      </div>
      <div class="text-xs text-muted" style="margin-top:6px">
        ${q.articles_classified} / ${q.articles_total} (${progressPct}%) 분류됨
        ${q.median_latency_seconds != null
          ? ` · 중앙 latency ${formatLatency(q.median_latency_seconds)}`
          : ''}
        · 기본 모델 <strong>${escape(defaultModel)}</strong>
      </div>
    `;
  }

  function renderVersions(pane, rows) {
    if (!rows.length) {
      pane.innerHTML = `<div class="empty-state text-muted">최근 분류 기록이 없습니다.</div>`;
      return;
    }
    const total = rows.reduce((s, r) => s + r.count, 0);
    pane.innerHTML = `
      <table class="data-table">
        <thead><tr><th>모델 버전</th><th>분류 수</th><th>비중</th><th>마지막 사용</th></tr></thead>
        <tbody>
          ${rows.map(r => {
            const share = r.count / total * 100;
            return `
              <tr>
                <td><strong>${escape(r.model_version)}</strong></td>
                <td class="mono">${r.count}</td>
                <td>
                  <div class="llm-share-bar"><div class="llm-share-fill" style="width:${share.toFixed(1)}%"></div></div>
                  <span class="text-xs text-muted">${share.toFixed(1)}%</span>
                </td>
                <td class="text-xs text-muted">${r.last_used_ts ? Utils.timeAgo(r.last_used_ts) + ' 전' : '—'}</td>
              </tr>
            `;
          }).join('')}
        </tbody>
      </table>
    `;
  }
}

function queueStat(label, value, footer, glow) {
  return `
    <div class="card stat-card">
      <div class="stat-glow ${glow}"></div>
      <div class="stat-card-top">
        <span class="stat-card-label">${label}</span>
      </div>
      <div class="stat-card-value">${typeof value === 'number' ? Utils.formatNumber(value) : value}</div>
      <div class="stat-card-footer"><span class="text-xs text-muted">${footer}</span></div>
    </div>
  `;
}

function formatLatency(s) {
  if (s < 60) return s.toFixed(1) + 's';
  if (s < 3600) return (s / 60).toFixed(1) + 'm';
  if (s < 86400) return (s / 3600).toFixed(1) + 'h';
  return (s / 86400).toFixed(1) + 'd';
}

function escape(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

window.renderLLM = renderLLM;
