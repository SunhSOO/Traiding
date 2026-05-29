/**
 * Operations dashboard.
 *
 * Four tabs:
 *  1. 데이터 (Data)     — freshness per (source, market, scope)
 *  2. 잡 (Jobs)         — scheduler jobs + pause/resume + run-now
 *  3. Drift             — score drift per ticker + today vs history action mix
 *  4. 비상 (Emergency)  — kill switch (pause scheduler + close all open positions)
 *
 * Replaces the old "freshness" page. The operator's single port-of-call
 * for "what is the system doing right now, and can I stop it safely?".
 */

function renderFreshness(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h2 class="text-xl">운영 대시보드</h2>
        <p class="text-sm text-muted">데이터 / 잡 / 드리프트 / 백필 / 리스크 / 품질 / 비상 정지</p>
      </div>
      <div class="page-header-actions">
        <button class="btn btn-secondary btn-sm" id="ops-refresh">${Utils.icon('refresh-cw')} 새로고침</button>
      </div>
    </div>

    <div class="ops-tabs" role="tablist">
      <button class="ops-tab-btn active" data-tab="data"      role="tab">데이터</button>
      <button class="ops-tab-btn"         data-tab="jobs"      role="tab">잡</button>
      <button class="ops-tab-btn"         data-tab="drift"     role="tab">Drift</button>
      <button class="ops-tab-btn"         data-tab="backfill"  role="tab">백필</button>
      <button class="ops-tab-btn"         data-tab="risk"      role="tab">리스크</button>
      <button class="ops-tab-btn"         data-tab="quality"   role="tab">품질</button>
      <button class="ops-tab-btn ops-tab-emergency" data-tab="emergency" role="tab">비상 정지</button>
    </div>

    <section class="ops-tab-pane" data-pane="data">
      <div class="card">
        <div class="card-header"><div class="card-title">데이터 소스</div></div>
        <div id="ops-data-pane"><div class="empty-state text-muted">불러오는 중…</div></div>
      </div>
    </section>

    <section class="ops-tab-pane" data-pane="jobs" hidden>
      <div class="card">
        <div class="card-header">
          <div class="card-title">스케줄러 잡</div>
          <div class="ops-jobs-controls">
            <span class="ops-sched-state text-xs text-muted" id="ops-sched-state">상태 확인 중…</span>
            <button class="btn btn-secondary btn-sm" id="ops-sched-pause">${Utils.icon('pause')} 일시 정지</button>
            <button class="btn btn-secondary btn-sm" id="ops-sched-resume">${Utils.icon('play')} 재개</button>
          </div>
        </div>
        <div id="ops-jobs-pane"><div class="empty-state text-muted">불러오는 중…</div></div>
      </div>
    </section>

    <section class="ops-tab-pane" data-pane="drift" hidden>
      <div class="card">
        <div class="card-header"><div class="card-title">스코어 Drift (per ticker)</div></div>
        <div id="ops-drift-score-pane"><div class="empty-state text-muted">불러오는 중…</div></div>
      </div>
      <div class="card">
        <div class="card-header"><div class="card-title">액션 분포 Drift (오늘 vs 30일)</div></div>
        <div id="ops-drift-mix-pane"><div class="empty-state text-muted">불러오는 중…</div></div>
      </div>
    </section>

    <section class="ops-tab-pane" data-pane="backfill" hidden>
      <div class="card">
        <div class="card-header"><div class="card-title">뉴스 백필 (Historical)</div></div>
        <p class="text-sm text-muted">
          BIGKinds + GDELT에서 과거 뉴스를 월 단위로 가져와 <code>news_articles</code>에 적재합니다.
          체크포인트가 있어 중단해도 다음 실행에서 이어집니다.
        </p>

        <div class="bt-control-row">
          <div class="form-row">
            <label>Job ID</label>
            <input type="text" class="input" id="bf-job-id" placeholder="2026-backfill-v1">
          </div>
          <div class="form-row">
            <label>시작일</label>
            <input type="date" class="input" id="bf-start">
          </div>
          <div class="form-row">
            <label>종료일</label>
            <input type="date" class="input" id="bf-end">
          </div>
          <div class="form-row">
            <label>시장</label>
            <select class="input" id="bf-market">
              <option value="">전체</option>
              <option value="KR">KR</option>
              <option value="US">US</option>
            </select>
          </div>
          <div class="form-row">
            <label>한 번에 처리</label>
            <input type="number" class="input" id="bf-max" value="20" min="1" max="500">
          </div>
          <div class="form-row bt-run-cell">
            <label>&nbsp;</label>
            <button class="btn btn-primary" id="bf-run">${Utils.icon('play')} 시작 / 재개</button>
          </div>
        </div>

        <div id="bf-result" style="margin-top: var(--space-3)"></div>
      </div>

      <div class="card">
        <div class="card-header">
          <div class="card-title">진행 상황</div>
          <button class="btn btn-secondary btn-sm" id="bf-status-refresh">${Utils.icon('refresh-cw')} 조회</button>
        </div>
        <div id="bf-status-pane"><div class="empty-state text-muted">Job ID 입력 후 조회</div></div>
        <div id="bf-errors-pane"></div>
      </div>

      <div class="card">
        <div class="card-header"><div class="card-title">매크로 historical backfill</div></div>
        <p class="text-sm text-muted">
          FRED + BOK ECOS에서 헤드라인 매크로 시리즈 (금리·VIX·DXY·USDKRW·KOSPI)를 backfill합니다.
          로더는 idempotent이므로 중복 실행해도 안전합니다.
        </p>
        <div class="bt-control-row">
          <div class="form-row">
            <label>시작일</label>
            <input type="date" class="input" id="bf-macro-start">
          </div>
          <div class="form-row">
            <label>종료일</label>
            <input type="date" class="input" id="bf-macro-end">
          </div>
          <div class="form-row bt-run-cell">
            <label>&nbsp;</label>
            <button class="btn btn-primary" id="bf-macro-run">${Utils.icon('play')} 매크로 backfill</button>
          </div>
        </div>
        <div id="bf-macro-result" style="margin-top: var(--space-3)"></div>
      </div>
    </section>

    <section class="ops-tab-pane" data-pane="risk" hidden>
      <div class="card">
        <div class="card-header">
          <div>
            <div class="card-title">리스크 게이트 통계 (최근 7일)</div>
            <div class="card-subtitle text-xs text-muted" id="ops-risk-sub">—</div>
          </div>
        </div>
        <div id="ops-risk-summary-pane"><div class="empty-state text-muted">불러오는 중…</div></div>
      </div>

      <div class="card">
        <div class="card-header"><div class="card-title">최근 실패한 리스크 검사</div></div>
        <div id="ops-risk-failures-pane"><div class="empty-state text-muted">불러오는 중…</div></div>
      </div>
    </section>

    <section class="ops-tab-pane" data-pane="quality" hidden>
      <div class="card">
        <div class="card-header">
          <div>
            <div class="card-title">티커별 가격 데이터 freshness</div>
            <div class="card-subtitle text-xs text-muted" id="ops-dq-fresh-sub">—</div>
          </div>
        </div>
        <div id="ops-dq-fresh-pane"><div class="empty-state text-muted">불러오는 중…</div></div>
      </div>

      <div class="card">
        <div class="card-header">
          <div>
            <div class="card-title">가격 데이터 갭 (최근 30일)</div>
            <div class="card-subtitle text-xs text-muted" id="ops-dq-gaps-sub">—</div>
          </div>
        </div>
        <div id="ops-dq-gaps-pane"><div class="empty-state text-muted">불러오는 중…</div></div>
      </div>
    </section>

    <section class="ops-tab-pane" data-pane="emergency" hidden>
      <div class="card ops-emergency-card">
        <div class="card-header"><div class="card-title text-loss">비상 정지 (Kill Switch)</div></div>
        <p class="text-sm text-muted">
          이 버튼은 <strong>스케줄러를 일시 정지</strong>하고 <strong>모든 열린 페이퍼 포지션을 시장가로 청산</strong>합니다.
          되돌릴 수 없으며, 실행 직후 후속 결정이 자동으로 진행되지 않습니다.
        </p>
        <button class="btn btn-loss btn-lg" id="ops-kill">${Utils.icon('alert-triangle')} 즉시 정지 + 전 포지션 청산</button>
        <div id="ops-kill-result"></div>
      </div>
    </section>
  `;

  // ── Tab switching ─────────────────────────────────────────────
  const tabs = container.querySelectorAll('.ops-tab-btn');
  const panes = container.querySelectorAll('.ops-tab-pane');
  tabs.forEach(btn => {
    btn.addEventListener('click', () => {
      tabs.forEach(b => b.classList.toggle('active', b === btn));
      const target = btn.dataset.tab;
      panes.forEach(p => { p.hidden = p.dataset.pane !== target; });
      reloadTab(target);
    });
  });

  container.querySelector('#ops-refresh').addEventListener('click', () => {
    const active = container.querySelector('.ops-tab-btn.active').dataset.tab;
    reloadTab(active);
  });

  // ── Scheduler pause/resume buttons ────────────────────────────
  container.querySelector('#ops-sched-pause').addEventListener('click', async () => {
    try {
      await Api.pauseScheduler();
      await reloadJobs();
    } catch (err) {
      alert('일시 정지 실패: ' + err.message);
    }
  });
  container.querySelector('#ops-sched-resume').addEventListener('click', async () => {
    try {
      await Api.resumeScheduler();
      await reloadJobs();
    } catch (err) {
      alert('재개 실패: ' + err.message);
    }
  });

  // ── Backfill ──────────────────────────────────────────────────
  container.querySelector('#bf-run').addEventListener('click', async () => {
    const jobId = container.querySelector('#bf-job-id').value.trim();
    const startVal = container.querySelector('#bf-start').value;
    const endVal = container.querySelector('#bf-end').value;
    const marketVal = container.querySelector('#bf-market').value || null;
    const maxChunks = Number(container.querySelector('#bf-max').value);
    if (!jobId || !startVal || !endVal) {
      alert('Job ID + 시작일 + 종료일이 필요합니다.');
      return;
    }
    const result = container.querySelector('#bf-result');
    const btn = container.querySelector('#bf-run');
    btn.disabled = true; btn.textContent = '실행 중…';
    result.innerHTML = '';
    try {
      const r = await Api.backfillNewsStart({
        jobId, start: startVal, end: endVal,
        market: marketVal, maxChunks,
      });
      result.innerHTML = `
        <div class="ops-kill-result-ok">
          <div><strong>완료:</strong> 처리 ${r.ran}건 · 성공 ${r.succeeded} · 실패 ${r.failed}</div>
          <div>적재 행수: <strong>${r.rows_inserted}</strong> · 남은 청크: <strong>${r.next_chunks_remaining}</strong></div>
        </div>
      `;
      // Auto-refresh status
      reloadBackfillStatus(jobId);
    } catch (err) {
      result.innerHTML = `<div class="text-loss">실패: ${escape(err.message)}</div>`;
    } finally {
      btn.disabled = false;
      btn.innerHTML = Utils.icon('play') + ' 시작 / 재개';
    }
  });

  container.querySelector('#bf-macro-run').addEventListener('click', async () => {
    const startVal = container.querySelector('#bf-macro-start').value;
    const endVal = container.querySelector('#bf-macro-end').value;
    if (!startVal || !endVal) {
      alert('시작일과 종료일을 모두 입력해주세요.');
      return;
    }
    const result = container.querySelector('#bf-macro-result');
    const btn = container.querySelector('#bf-macro-run');
    btn.disabled = true; btn.textContent = '실행 중…';
    result.innerHTML = '<div class="text-muted">동기 호출이라 완료까지 1~5분 걸릴 수 있습니다…</div>';
    try {
      const r = await Api.backfillMacroRun({ start: startVal, end: endVal });
      result.innerHTML = `
        <div class="ops-kill-result-ok">
          <div><strong>완료:</strong> ${r.window_start} → ${r.window_end}</div>
          <div>시리즈: 처리 <strong>${r.series_processed}</strong> · 실패 <strong>${r.series_failed}</strong> · 행 적재 <strong>${r.rows_upserted}</strong></div>
          ${r.errors.length ? `<div class="text-loss text-xs">에러: ${r.errors.map(escape).join('; ')}</div>` : ''}
        </div>
      `;
    } catch (err) {
      result.innerHTML = `<div class="text-loss">실패: ${escape(err.message)}</div>`;
    } finally {
      btn.disabled = false;
      btn.innerHTML = Utils.icon('play') + ' 매크로 backfill';
    }
  });

  container.querySelector('#bf-status-refresh').addEventListener('click', () => {
    const jobId = container.querySelector('#bf-job-id').value.trim();
    if (!jobId) {
      alert('Job ID를 먼저 입력하세요.');
      return;
    }
    reloadBackfillStatus(jobId);
  });

  async function reloadRisk() {
    const summaryPane = container.querySelector('#ops-risk-summary-pane');
    const failuresPane = container.querySelector('#ops-risk-failures-pane');
    const sub = container.querySelector('#ops-risk-sub');
    const market = (window.State && State.getMarket && State.getMarket()) || null;
    try {
      const data = await Api.riskSummary({ windowHours: 168, market });
      sub.textContent = `${data.total_snapshots}건 스냅샷 · 전체 통과율 ${(data.overall_pass_rate * 100).toFixed(1)}%`;
      if (data.total_snapshots === 0) {
        summaryPane.innerHTML = `<div class="empty-state text-muted">최근 7일 리스크 검사 기록이 없습니다.</div>`;
      } else {
        summaryPane.innerHTML = `
          <table class="data-table">
            <thead><tr><th>제한</th><th>통과</th><th>실패</th><th>실패율</th><th></th></tr></thead>
            <tbody>
              ${data.limits.map(l => `
                <tr>
                  <td><strong>${escape(l.name)}</strong></td>
                  <td class="mono">${l.pass_count}</td>
                  <td class="mono ${l.fail_count > 0 ? 'text-loss' : ''}">${l.fail_count}</td>
                  <td class="mono ${l.fail_rate > 0.1 ? 'text-loss' : ''}">${(l.fail_rate * 100).toFixed(1)}%</td>
                  <td><div class="risk-fail-bar"><div class="risk-fail-fill" style="width:${(l.fail_rate * 100).toFixed(1)}%"></div></div></td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        `;
      }
    } catch (err) {
      summaryPane.innerHTML = `<div class="empty-state text-loss">${escape(err.message)}</div>`;
    }

    try {
      const rows = await Api.riskRecentFailures({ limit: 30, market });
      if (!rows.length) {
        failuresPane.innerHTML = `<div class="empty-state text-muted">최근 실패 없음.</div>`;
      } else {
        failuresPane.innerHTML = `
          <table class="data-table">
            <thead><tr><th>시각</th><th>종목</th><th>실패 제한</th></tr></thead>
            <tbody>
              ${rows.map(r => `
                <tr>
                  <td class="text-xs text-muted">${Utils.timeAgo(r.snapshot_ts)} 전</td>
                  <td><span class="badge badge-info">${escape(r.market)}</span> <span class="mono">${escape(r.ticker || '—')}</span></td>
                  <td>${r.failed_limits.map(l => `<span class="badge badge-loss" style="margin-right:4px">${escape(l)}</span>`).join('')}</td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        `;
      }
    } catch (err) {
      failuresPane.innerHTML = `<div class="empty-state text-loss">${escape(err.message)}</div>`;
    }
  }

  async function reloadQuality() {
    const freshPane = container.querySelector('#ops-dq-fresh-pane');
    const gapsPane = container.querySelector('#ops-dq-gaps-pane');
    const freshSub = container.querySelector('#ops-dq-fresh-sub');
    const gapsSub = container.querySelector('#ops-dq-gaps-sub');
    const market = (window.State && State.getMarket && State.getMarket()) || null;

    // Freshness
    try {
      const data = await Api.dqFreshness({ market });
      const s = data.summary;
      freshSub.textContent = `${s.total}개 종목 · 신선 ${s.fresh_count} · 지연 ${s.stale_count} · 사망 ${s.dead_count}`;
      const problemRows = data.rows.filter(r => r.tier !== 'FRESH').slice(0, 100);
      if (!problemRows.length) {
        freshPane.innerHTML = `<div class="empty-state text-profit">모든 종목이 신선 상태입니다.</div>`;
      } else {
        freshPane.innerHTML = `
          <table class="data-table">
            <thead><tr><th>종목</th><th>티어</th><th>마지막 가격일</th><th>경과일</th></tr></thead>
            <tbody>
              ${problemRows.map(r => `
                <tr class="dq-tier-${r.tier.toLowerCase()}">
                  <td>
                    <strong>${escape(r.market)}:${escape(r.ticker)}</strong>
                    <div class="text-xs text-muted">${escape(r.name || '')}</div>
                  </td>
                  <td><span class="badge ${tierBadge(r.tier)}">${r.tier}</span></td>
                  <td class="text-xs">${escape(r.last_price_date || '—')}</td>
                  <td class="mono">${r.days_since_last == null ? '∞' : r.days_since_last + 'd'}</td>
                </tr>
              `).join('')}
            </tbody>
          </table>
          ${data.rows.length > 100 ? `<div class="text-xs text-muted" style="margin-top:8px">처음 100건만 표시 — 총 문제 ${data.rows.filter(r => r.tier !== 'FRESH').length}건</div>` : ''}
        `;
      }
    } catch (err) {
      freshPane.innerHTML = `<div class="empty-state text-loss">${escape(err.message)}</div>`;
    }

    // Gaps
    try {
      const data = await Api.dqGaps({ market, days: 30 });
      gapsSub.textContent = `${data.total_tickers_checked}개 검사 · ${data.tickers_with_gaps}개 종목에 갭`;
      if (!data.windows.length) {
        gapsPane.innerHTML = `<div class="empty-state text-profit">최근 30일 갭 없음.</div>`;
      } else {
        gapsPane.innerHTML = `
          <table class="data-table">
            <thead><tr><th>종목</th><th>갭 시작</th><th>갭 끝</th><th>일수</th></tr></thead>
            <tbody>
              ${data.windows.slice(0, 200).map(w => `
                <tr>
                  <td><strong>${escape(w.market)}:${escape(w.ticker)}</strong></td>
                  <td class="text-xs">${escape(w.start)}</td>
                  <td class="text-xs">${escape(w.end)}</td>
                  <td class="mono ${w.days >= 5 ? 'text-loss' : 'text-warn'}">${w.days}d</td>
                </tr>
              `).join('')}
            </tbody>
          </table>
          ${data.windows.length > 200 ? `<div class="text-xs text-muted" style="margin-top:8px">처음 200건 표시 — 총 ${data.windows.length}건</div>` : ''}
        `;
      }
    } catch (err) {
      gapsPane.innerHTML = `<div class="empty-state text-loss">${escape(err.message)}</div>`;
    }
  }

  function tierBadge(tier) {
    if (tier === 'FRESH') return 'badge-profit';
    if (tier === 'STALE') return 'badge-info';
    return 'badge-loss';
  }

  async function reloadBackfill() {
    // Pre-fill sensible defaults for both news and macro
    const startInput = container.querySelector('#bf-start');
    const endInput = container.querySelector('#bf-end');
    if (!startInput.value) {
      const y = new Date(); y.setFullYear(y.getFullYear() - 1);
      startInput.value = y.toISOString().slice(0, 10);
    }
    if (!endInput.value) {
      endInput.value = new Date().toISOString().slice(0, 10);
    }
    // Macro defaults — 10-year window for historical depth
    const macroStart = container.querySelector('#bf-macro-start');
    const macroEnd = container.querySelector('#bf-macro-end');
    if (macroStart && !macroStart.value) {
      const tenYrs = new Date(); tenYrs.setFullYear(tenYrs.getFullYear() - 10);
      macroStart.value = tenYrs.toISOString().slice(0, 10);
    }
    if (macroEnd && !macroEnd.value) {
      macroEnd.value = new Date().toISOString().slice(0, 10);
    }
  }

  async function reloadBackfillStatus(jobId) {
    const pane = container.querySelector('#bf-status-pane');
    const errPane = container.querySelector('#bf-errors-pane');
    pane.innerHTML = `<div class="empty-state text-muted">불러오는 중…</div>`;
    try {
      const status = await Api.backfillJobStatus(jobId);
      const c = status.counts;
      const total = status.total || 1;
      pane.innerHTML = `
        <div class="bf-status-grid">
          <div class="bf-status-cell"><div class="num">${c.done}</div><div class="lbl">완료</div></div>
          <div class="bf-status-cell warn"><div class="num">${c.in_progress}</div><div class="lbl">진행중</div></div>
          <div class="bf-status-cell"><div class="num">${c.pending}</div><div class="lbl">대기</div></div>
          <div class="bf-status-cell err"><div class="num">${c.error}</div><div class="lbl">에러</div></div>
        </div>
        <div class="bf-progress-bar">
          <div class="bf-progress-fill" style="width: ${(c.done / total * 100).toFixed(1)}%"></div>
        </div>
        <div class="text-xs text-muted" style="margin-top:4px">
          총 ${status.total}개 청크 · ${(c.done / total * 100).toFixed(1)}% 완료
        </div>
      `;
      if (c.error > 0) {
        try {
          const errors = await Api.backfillJobErrors(jobId, 50);
          errPane.innerHTML = `
            <div class="card-subtitle text-loss" style="margin-top:var(--space-3)">에러 ${errors.length}건</div>
            <table class="data-table">
              <thead><tr><th>소스</th><th>티커</th><th>기간</th><th>에러</th></tr></thead>
              <tbody>
                ${errors.map(e => `
                  <tr>
                    <td><span class="badge badge-loss">${escape(e.source)}</span></td>
                    <td class="mono">${escape(e.market)}:${escape(e.ticker)}</td>
                    <td class="mono text-xs">${escape(e.period)}</td>
                    <td class="text-xs text-loss">${escape(e.error)}</td>
                  </tr>
                `).join('')}
              </tbody>
            </table>
          `;
        } catch (err) {
          errPane.innerHTML = `<div class="text-loss">에러 목록 조회 실패: ${escape(err.message)}</div>`;
        }
      } else {
        errPane.innerHTML = '';
      }
    } catch (err) {
      pane.innerHTML = `<div class="empty-state text-loss">${escape(err.message)}</div>`;
    }
  }

  // ── Kill switch ───────────────────────────────────────────────
  container.querySelector('#ops-kill').addEventListener('click', async () => {
    const ok = confirm('정말 비상 정지하시겠습니까? 모든 열린 포지션이 즉시 청산됩니다.');
    if (!ok) return;
    const btn = container.querySelector('#ops-kill');
    btn.disabled = true; btn.textContent = '정지 중…';
    const resultEl = container.querySelector('#ops-kill-result');
    try {
      const r = await Api.killSwitch();
      resultEl.innerHTML = `
        <div class="ops-kill-result-ok">
          <div><strong>완료:</strong> ${Utils.formatDateTime(r.triggered_at)} (${escape(r.triggered_by)})</div>
          <div>잡 일시정지: <strong>${r.scheduler_jobs_paused}</strong> · 청산 포지션: <strong>${r.positions_closed}</strong></div>
          ${r.close_errors && r.close_errors.length
            ? `<div class="text-loss text-xs">에러 ${r.close_errors.length}건: ${r.close_errors.map(escape).join('; ')}</div>`
            : ''}
        </div>
      `;
      reloadJobs();
    } catch (err) {
      resultEl.innerHTML = `<div class="text-loss">실패: ${escape(err.message)}</div>`;
    } finally {
      btn.disabled = false;
      btn.innerHTML = Utils.icon('alert-triangle') + ' 즉시 정지 + 전 포지션 청산';
    }
  });

  reloadTab('data');
  // Light auto-refresh for the data tab only (most likely active)
  const intervalId = setInterval(() => {
    const active = container.querySelector('.ops-tab-btn.active').dataset.tab;
    if (active === 'data' || active === 'jobs') reloadTab(active);
  }, 30_000);
  AppRouter.beforeEach(() => { clearInterval(intervalId); return true; });

  // ── Reload dispatch ───────────────────────────────────────────
  function reloadTab(tab) {
    if (tab === 'data')      return reloadData();
    if (tab === 'jobs')      return reloadJobs();
    if (tab === 'drift')     return reloadDrift();
    if (tab === 'backfill')  return reloadBackfill();
    if (tab === 'risk')      return reloadRisk();
    if (tab === 'quality')   return reloadQuality();
    if (tab === 'emergency') return;  // static
  }

  async function reloadData() {
    const pane = container.querySelector('#ops-data-pane');
    try {
      const rows = await Api.ingestionFreshness();
      renderData(pane, rows);
    } catch (err) {
      pane.innerHTML = `<div class="empty-state text-loss">${escape(err.message)}</div>`;
    }
  }

  async function reloadJobs() {
    const pane = container.querySelector('#ops-jobs-pane');
    const stateEl = container.querySelector('#ops-sched-state');
    try {
      const state = await Api.schedulerState();
      stateEl.textContent = state.paused
        ? `일시정지 (${state.job_count}개)`
        : `구동 중 (${state.job_count}개)`;
      stateEl.classList.toggle('text-loss', state.paused);
      renderJobs(pane, state.jobs || []);
    } catch (err) {
      pane.innerHTML = `<div class="empty-state text-loss">${escape(err.message)}</div>`;
    }
  }

  async function reloadDrift() {
    const scorePane = container.querySelector('#ops-drift-score-pane');
    const mixPane = container.querySelector('#ops-drift-mix-pane');
    const market = (window.State && State.getMarket && State.getMarket()) || null;

    try {
      const rows = await Api.driftScore({ market });
      renderDriftScore(scorePane, rows);
    } catch (err) {
      scorePane.innerHTML = `<div class="empty-state text-loss">${escape(err.message)}</div>`;
    }

    try {
      const mix = await Api.driftActionMix({ market });
      renderDriftMix(mixPane, mix);
    } catch (err) {
      mixPane.innerHTML = `<div class="empty-state text-loss">${escape(err.message)}</div>`;
    }
  }

  // ── Renderers ─────────────────────────────────────────────────

  function renderData(pane, rows) {
    if (!rows.length) {
      pane.innerHTML = `<div class="empty-state text-muted">아직 등록된 데이터 소스가 없습니다.</div>`;
      return;
    }
    pane.innerHTML = `
      <table class="data-table">
        <thead>
          <tr>
            <th>소스</th><th>시장</th><th>scope</th>
            <th>마지막 성공</th><th>마지막 시도</th>
            <th>최근 행수</th><th>에러</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map(r => `
            <tr>
              <td><strong>${escape(r.source)}</strong></td>
              <td>${r.market ? `<span class="badge badge-info">${r.market}</span>` : '—'}</td>
              <td>${escape(r.scope)}</td>
              <td class="text-xs ${freshnessClass(r.last_success_ts)}">
                ${r.last_success_ts ? Utils.timeAgo(r.last_success_ts) : '—'}
              </td>
              <td class="text-xs text-muted">${r.last_attempt_ts ? Utils.timeAgo(r.last_attempt_ts) : '—'}</td>
              <td class="mono">${r.rows_last_run ?? '—'}</td>
              <td class="text-xs ${r.last_error ? 'text-loss' : 'text-muted'}">${escape(r.last_error || '—')}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
  }

  function renderJobs(pane, jobs) {
    if (!jobs.length) {
      pane.innerHTML = `<div class="empty-state text-muted">잡이 등록되지 않았습니다.</div>`;
      return;
    }
    pane.innerHTML = `
      <table class="data-table">
        <thead>
          <tr><th>잡 ID</th><th>다음 실행</th><th></th></tr>
        </thead>
        <tbody>
          ${jobs.map(j => `
            <tr>
              <td><strong>${escape(j.id)}</strong></td>
              <td class="text-xs text-muted">${j.next_run ? Utils.formatDateTime(j.next_run) : '—'}</td>
              <td>
                <button class="btn btn-secondary btn-sm" data-job-id="${escape(j.id)}">${Utils.icon('play')} 지금 실행</button>
              </td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
    pane.querySelectorAll('button[data-job-id]').forEach(btn => {
      btn.addEventListener('click', async () => {
        btn.disabled = true; btn.textContent = '실행 중…';
        try {
          await Api.runJobNow(btn.dataset.jobId);
          btn.textContent = '✓ 완료';
        } catch (err) {
          btn.textContent = '✗ 실패';
          btn.title = err.message;
        } finally {
          setTimeout(() => { btn.disabled = false; btn.innerHTML = Utils.icon('play') + ' 지금 실행'; }, 2000);
        }
      });
    });
  }

  function renderDriftScore(pane, rows) {
    if (!rows.length) {
      pane.innerHTML = `<div class="empty-state text-muted">최근 결정이 충분치 않습니다.</div>`;
      return;
    }
    pane.innerHTML = `
      <table class="data-table">
        <thead>
          <tr>
            <th>시장</th><th>티커</th><th>현재 스코어</th>
            <th>평균</th><th>표준편차</th><th>|z|</th>
            <th>n</th><th>이상</th><th>최근 결정</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map(r => `
            <tr class="${r.is_anomaly ? 'row-anomaly' : ''}">
              <td><span class="badge badge-info">${escape(r.market)}</span></td>
              <td class="mono">${escape(r.ticker)}</td>
              <td class="mono">${fmt(r.current_score)}</td>
              <td class="mono">${fmt(r.mean)}</td>
              <td class="mono">${fmt(r.stdev)}</td>
              <td class="mono ${r.is_anomaly ? 'text-loss' : ''}">${fmt(r.z_score)}</td>
              <td class="mono">${r.n_history}</td>
              <td>${r.is_anomaly ? '<span class="badge badge-loss">⚠</span>' : ''}</td>
              <td class="text-xs text-muted">${Utils.timeAgo(r.last_decision_ts)}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
  }

  function renderDriftMix(pane, mix) {
    if (!mix || (mix.today_count === 0 && mix.history_count === 0)) {
      pane.innerHTML = `<div class="empty-state text-muted">아직 결정 기록이 없습니다.</div>`;
      return;
    }
    const actions = Array.from(new Set([
      ...Object.keys(mix.today_distribution || {}),
      ...Object.keys(mix.history_distribution || {}),
    ])).sort();
    pane.innerHTML = `
      <div class="drift-mix-card ${mix.is_anomaly ? 'drift-mix-anomaly' : ''}">
        <div class="drift-mix-summary">
          <div>TV distance: <strong class="${mix.is_anomaly ? 'text-loss' : ''}">${mix.total_variation_distance.toFixed(3)}</strong></div>
          <div class="text-xs text-muted">오늘 ${mix.today_count}건 · 기준 ${mix.history_count}건</div>
        </div>
        <table class="data-table drift-mix-table">
          <thead><tr><th>액션</th><th>오늘</th><th>30일</th><th>차이</th></tr></thead>
          <tbody>
            ${actions.map(a => {
              const t = mix.today_distribution[a] || 0;
              const h = mix.history_distribution[a] || 0;
              const diff = t - h;
              return `<tr>
                <td><strong>${escape(a)}</strong></td>
                <td class="mono">${(t * 100).toFixed(1)}%</td>
                <td class="mono">${(h * 100).toFixed(1)}%</td>
                <td class="mono ${Math.abs(diff) > 0.1 ? (diff > 0 ? 'text-profit' : 'text-loss') : 'text-muted'}">
                  ${diff > 0 ? '+' : ''}${(diff * 100).toFixed(1)}%
                </td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>
      </div>
    `;
  }

  // ── Utilities ─────────────────────────────────────────────────
  function freshnessClass(ts) {
    if (!ts) return 'text-muted';
    const age_hours = (Date.now() - new Date(ts).getTime()) / 3600_000;
    if (age_hours < 2) return 'text-profit';
    if (age_hours < 24) return '';
    return 'text-loss';
  }
  function fmt(v) {
    if (v === null || v === undefined) return '—';
    return Number(v).toFixed(3);
  }
}

function escape(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

window.renderFreshness = renderFreshness;
