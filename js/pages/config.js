/**
 * System config inspector — read-only.
 *
 * Single screen showing the operative DecisionConfig, weight stats,
 * runtime mode + env, LLM providers, and the scheduler job catalog.
 * No mutation here — changes go through dedicated routes
 * (/operations for kill switch, /training for overrides, etc.).
 */

function renderConfig(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h2 class="text-xl">시스템 설정 (read-only)</h2>
        <p class="text-sm text-muted">현재 운영 중인 결정 엔진 + 가중치 + 스케줄러 카탈로그</p>
      </div>
      <div class="page-header-actions">
        <button class="btn btn-secondary btn-sm" id="cfg-refresh">${Utils.icon('refresh-cw')} 새로고침</button>
      </div>
    </div>

    <div class="cfg-row">
      <div class="card animate-fade-in-up">
        <div class="card-header"><div class="card-title">런타임</div></div>
        <div id="cfg-runtime-pane"><div class="text-muted">불러오는 중…</div></div>
      </div>
      <div class="card animate-fade-in-up">
        <div class="card-header"><div class="card-title">가중치 상태</div></div>
        <div id="cfg-weights-pane"><div class="text-muted">불러오는 중…</div></div>
      </div>
    </div>

    <div class="card animate-fade-in-up">
      <div class="card-header">
        <div>
          <div class="card-title">DecisionConfig</div>
          <div class="card-subtitle text-xs text-muted">기본 가중치 / 임계값 / 게이트 / sizer</div>
        </div>
      </div>
      <div id="cfg-decision-pane"><div class="text-muted">불러오는 중…</div></div>
    </div>

    <div class="card animate-fade-in-up">
      <div class="card-header">
        <div>
          <div class="card-title">스케줄러 잡 카탈로그</div>
          <div class="card-subtitle text-xs text-muted" id="cfg-sched-sub">—</div>
        </div>
      </div>
      <div id="cfg-sched-pane"><div class="text-muted">불러오는 중…</div></div>
    </div>
  `;

  container.querySelector('#cfg-refresh').addEventListener('click', reload);
  reload();

  async function reload() {
    try {
      const data = await Api.systemConfig();
      renderRuntime(container.querySelector('#cfg-runtime-pane'), data);
      renderWeights(container.querySelector('#cfg-weights-pane'), data);
      renderDecision(container.querySelector('#cfg-decision-pane'), data.decision);
      renderScheduler(
        container.querySelector('#cfg-sched-pane'),
        container.querySelector('#cfg-sched-sub'),
        data.scheduler,
      );
    } catch (err) {
      container.querySelector('#cfg-runtime-pane').innerHTML =
        `<div class="text-loss">${escape(err.message)}</div>`;
    }
  }

  function renderRuntime(pane, data) {
    const modeBadge = data.runtime_mode === 'live'
      ? `<span class="badge badge-loss">LIVE</span>`
      : `<span class="badge badge-profit">PAPER</span>`;
    pane.innerHTML = `
      <div class="cfg-kv">
        <div><div class="cfg-k">runtime_mode</div><div class="cfg-v">${modeBadge}</div></div>
        <div><div class="cfg-k">app_env</div><div class="cfg-v"><span class="badge badge-info">${escape(data.app_env)}</span></div></div>
        <div><div class="cfg-k">LLM providers</div><div class="cfg-v">${data.configured_llm_providers.length
          ? data.configured_llm_providers.map(p => `<span class="badge badge-info" style="margin-right:4px">${escape(p)}</span>`).join('')
          : '<span class="text-muted">없음</span>'}</div></div>
        <div><div class="cfg-k">DB 연결</div><div class="cfg-v">${data.db_connected ? '<span class="badge badge-profit">정상</span>' : '<span class="badge badge-loss">실패</span>'}</div></div>
      </div>
    `;
  }

  function renderWeights(pane, data) {
    const w = data.weights;
    pane.innerHTML = `
      <div class="cfg-kv">
        <div><div class="cfg-k">학습된 클러스터</div><div class="cfg-v mono">${w.learned_clusters}</div></div>
        <div><div class="cfg-k">운영자 override</div><div class="cfg-v mono ${w.operator_overrides > 0 ? 'text-warn' : ''}">${w.operator_overrides}</div></div>
        <div><div class="cfg-k">기본 F / T / I</div><div class="cfg-v mono">${data.decision.weight_fundamental.toFixed(2)} / ${data.decision.weight_technical.toFixed(2)} / ${data.decision.weight_information.toFixed(2)}</div></div>
      </div>
    `;
  }

  function renderDecision(pane, d) {
    pane.innerHTML = `
      <div class="cfg-section">
        <h4>가중치 (기본)</h4>
        <div class="cfg-kv">
          <div><div class="cfg-k">F (기본)</div><div class="cfg-v mono">${d.weight_fundamental.toFixed(3)}</div></div>
          <div><div class="cfg-k">T (기술)</div><div class="cfg-v mono">${d.weight_technical.toFixed(3)}</div></div>
          <div><div class="cfg-k">I (정보)</div><div class="cfg-v mono">${d.weight_information.toFixed(3)}</div></div>
        </div>
      </div>
      <div class="cfg-section">
        <h4>임계값</h4>
        <div class="cfg-kv">
          <div><div class="cfg-k">BUY 임계값</div><div class="cfg-v mono text-profit">+${d.buy_threshold.toFixed(1)}</div></div>
          <div><div class="cfg-k">SELL 임계값</div><div class="cfg-v mono text-loss">${d.sell_threshold.toFixed(1)}</div></div>
        </div>
      </div>
      <div class="cfg-section">
        <h4>게이트</h4>
        <div class="cfg-kv">
          <div><div class="cfg-k">min module confidence</div><div class="cfg-v mono">${(d.min_module_confidence * 100).toFixed(0)}%</div></div>
          <div><div class="cfg-k">min overall confidence</div><div class="cfg-v mono">${(d.min_overall_confidence * 100).toFixed(0)}%</div></div>
          <div><div class="cfg-k">churn (동일 종목 쿨다운)</div><div class="cfg-v mono">${d.churn_minutes}분</div></div>
          <div><div class="cfg-k">score staleness</div><div class="cfg-v mono">${d.score_staleness_hours}h</div></div>
        </div>
      </div>
      <div class="cfg-section">
        <h4>Sizer</h4>
        <div class="cfg-kv">
          <div><div class="cfg-k">base position fraction</div><div class="cfg-v mono">${(d.base_position_fraction * 100).toFixed(1)}%</div></div>
          <div><div class="cfg-k">target volatility</div><div class="cfg-v mono">${d.target_volatility_bps.toFixed(0)}bps</div></div>
          <div><div class="cfg-k">max position fraction</div><div class="cfg-v mono">${(d.max_position_fraction * 100).toFixed(1)}%</div></div>
        </div>
      </div>
    `;
  }

  function renderScheduler(pane, sub, sched) {
    sub.textContent = sched.started
      ? (sched.paused ? `일시정지 (${sched.job_count}개 잡)` : `구동 중 (${sched.job_count}개 잡)`)
      : '미구동';
    if (!sched.jobs.length) {
      pane.innerHTML = `<div class="empty-state text-muted">잡이 등록되지 않았습니다.</div>`;
      return;
    }
    const sorted = sched.jobs.slice().sort((a, b) => {
      if (!a.next_run) return 1;
      if (!b.next_run) return -1;
      return new Date(a.next_run) - new Date(b.next_run);
    });
    pane.innerHTML = `
      <table class="data-table">
        <thead><tr><th>잡 ID</th><th>다음 실행</th></tr></thead>
        <tbody>
          ${sorted.map(j => `
            <tr>
              <td><strong>${escape(j.id)}</strong></td>
              <td class="text-xs text-muted">${j.next_run ? Utils.formatDateTime(j.next_run) + ` (${Utils.timeAgo(j.next_run)} 후)` : '미정 / 비활성'}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
  }
}

function escape(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

window.renderConfig = renderConfig;
