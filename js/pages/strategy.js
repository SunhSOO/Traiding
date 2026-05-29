/**
 * Legacy Red-Green strategy page (MT5 FX/gold only).
 *
 * Red-Green is now a single Technical signal inside the woonam
 * composite engine (technical/signals/red_green_signal.py); this
 * page is the standalone runtime monitor for the original
 * XAUUSD-only state machine. Independent from the KR/US equity
 * decision pipeline.
 */
function renderStrategy(container) {
  container.innerHTML = `
    <div class="legacy-banner">
      ⚠ <strong>Legacy Red-Green 전략</strong> — XAUUSD 전용 BB-ICHI+Supertrend 상태머신. woonam KR/US
      자동매매와 독립. KR/US 자동매매의 결정은 <a href="#/decisions">결정 감사</a> 페이지에서 확인하세요.
    </div>
    <div class="strategy-page">
      <div class="strategy-header">
        <div>
          <h2>Red-Green 자동매매</h2>
          <p class="text-sm text-muted">BB-ICHI + Supertrend + SL/TP1/Trailing 상태 머신</p>
        </div>
        <div class="strategy-actions">
          <button class="btn btn-secondary" id="strategy-refresh">${Utils.icon('refresh-cw')} 새로고침</button>
          <button class="btn btn-secondary" id="strategy-run-once">${Utils.icon('zap')} 1회 실행</button>
          <button class="btn btn-success" id="strategy-start">${Utils.icon('play')} 시작</button>
          <button class="btn btn-danger" id="strategy-stop">${Utils.icon('stop-circle')} 중지</button>
        </div>
      </div>

      <div id="strategy-content" class="strategy-content">
        <div class="card">
          <div class="card-body text-muted">전략 상태를 불러오는 중...</div>
        </div>
      </div>
    </div>
  `;

  const content = container.querySelector('#strategy-content');
  const refreshBtn = container.querySelector('#strategy-refresh');
  const runOnceBtn = container.querySelector('#strategy-run-once');
  const startBtn = container.querySelector('#strategy-start');
  const stopBtn = container.querySelector('#strategy-stop');

  async function api(path, options = {}) {
    const res = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
    return res.json();
  }

  function price(value) {
    if (value === null || value === undefined) return '-';
    return Number(value).toFixed(3);
  }

  function modeBadge(mode) {
    const cls = mode === 'live' ? 'badge-loss' : mode === 'demo' ? 'badge-neutral' : 'badge-profit';
    return `<span class="badge ${cls}">${mode}</span>`;
  }

  function stateLabel(state) {
    if (state === 1) return '<span class="text-profit">롱 보유</span>';
    if (state === -1) return '<span class="text-loss">숏 보유</span>';
    return '<span class="text-muted">대기</span>';
  }

  function renderStatus(payload) {
    if (!payload.success) {
      content.innerHTML = `<div class="card"><div class="card-body text-loss">${payload.error || '전략 상태를 불러오지 못했습니다.'}</div></div>`;
      return;
    }

    const status = payload.status;
    const cfg = status.config;
    const state = status.state;
    const events = status.events || [];

    content.innerHTML = `
      <div class="strategy-main-grid">
        <div class="card strategy-live-card">
          <div class="card-header">
            <div>
              <div class="card-title">${status.name}</div>
              <div class="text-xs text-muted">${cfg.symbol} · ${cfg.timeframe} · magic ${cfg.magic}</div>
            </div>
            <div class="strategy-badges">
              <span class="badge ${status.status === 'running' ? 'badge-profit' : 'badge-neutral'}">${status.status === 'running' ? 'running' : 'stopped'}</span>
              ${modeBadge(status.mode)}
            </div>
          </div>

          <div class="strategy-condition-grid">
            <div class="strategy-condition">
              <span>포지션</span>
              <strong>${stateLabel(state.pos_state)}</strong>
            </div>
            <div class="strategy-condition">
              <span>진입가</span>
              <strong>${price(state.pos_entry)}</strong>
            </div>
            <div class="strategy-condition danger">
              <span>SL</span>
              <strong>${price(state.pos_sl)}</strong>
            </div>
            <div class="strategy-condition info">
              <span>TP1</span>
              <strong>${price(state.pos_tp1)}</strong>
            </div>
            <div class="strategy-condition warn">
              <span>Trail</span>
              <strong>${state.trail_active ? '활성' : state.show_trail ? '대기' : '비활성'}</strong>
            </div>
            <div class="strategy-condition">
              <span>TP1 도달</span>
              <strong>${state.tp1_hit ? '예' : '아니오'}</strong>
            </div>
          </div>

          <div class="strategy-param-grid">
            <div><span>BB</span><strong>${cfg.short_period}/${cfg.mid_period}/${cfg.long_period} · ${cfg.mult}σ</strong></div>
            <div><span>Supertrend</span><strong>ATR ${cfg.atr_periods} · x${cfg.atr_multiplier}</strong></div>
            <div><span>TP1 R:R</span><strong>${cfg.tp1_mult}</strong></div>
            <div><span>Lot</span><strong>${cfg.volume}</strong></div>
            <div><span>Dry Run</span><strong>${cfg.dry_run ? 'ON' : 'OFF'}</strong></div>
            <div><span>Live</span><strong>${cfg.live_enabled ? 'ON' : 'OFF'}</strong></div>
          </div>
        </div>

        <div class="card">
          <div class="card-header">
            <div class="card-title">안전 상태</div>
            <span class="badge ${cfg.live_enabled ? 'badge-loss' : 'badge-profit'}">${cfg.live_enabled ? '실거래 주의' : '주문 차단 기본값'}</span>
          </div>
          <div class="safety-list">
            <div>${Utils.icon('shield')} <span>기본 모드는 dry_run이며, 주문 의도만 기록합니다.</span></div>
            <div>${Utils.icon('target')} <span>반대 신호는 기존 포지션 청산 후 신규 진입을 검토합니다.</span></div>
            <div>${Utils.icon('activity')} <span>마지막 처리 봉: ${status.last_processed_time || '-'}</span></div>
            <div>${Utils.icon('wifi')} <span>오류: ${status.last_error || '없음'}</span></div>
          </div>
          <div class="live-warning">
            live 모드는 API 설정에서 <code>dry_run=false</code>, <code>live_enabled=true</code>일 때만 주문을 보냅니다.
          </div>
        </div>
      </div>

      <div class="card strategy-log-card">
        <div class="card-header">
          <div class="card-title">전략 이벤트 로그</div>
          <span class="text-xs text-muted">최근 ${events.length}개</span>
        </div>
        <div class="strategy-log-panel">
          ${events.length ? events.map(event => `
            <div class="log-entry">
              <span class="log-time">${Utils.formatTime(event.time)}</span>
              <span class="log-level ${event.level}">[${event.level.toUpperCase()}]</span>
              <span class="log-message"><strong>${event.event}</strong> · ${event.message}</span>
            </div>
          `).join('') : '<div class="empty-log text-muted">아직 기록된 전략 이벤트가 없습니다.</div>'}
        </div>
      </div>
    `;
  }

  async function loadStatus() {
    try {
      renderStatus(await api('/api/strategy/red-green/status'));
    } catch (error) {
      content.innerHTML = `<div class="card"><div class="card-body text-loss">API 연결 실패: ${error.message}</div></div>`;
    }
  }

  refreshBtn.addEventListener('click', loadStatus);
  runOnceBtn.addEventListener('click', async () => {
    await api('/api/strategy/red-green/run-once', { method: 'POST' });
    await loadStatus();
  });
  startBtn.addEventListener('click', async () => {
    await api('/api/strategy/red-green/start', { method: 'POST' });
    await loadStatus();
  });
  stopBtn.addEventListener('click', async () => {
    await api('/api/strategy/red-green/stop', { method: 'POST' });
    await loadStatus();
  });

  loadStatus();
}

window.renderStrategy = renderStrategy;
