/**
 * Backtest replay page.
 *
 * Lets the operator pick a date window + market filter + tickers and
 * replays the system's past BUY/SELL decisions through the long-only
 * simulator. Returns equity curve + headline metrics + per-trade list.
 *
 * This is signal-quality validation: "given the decisions the system
 * made, would it have actually made money?" Slippage / commission are
 * intentionally NOT modelled — those are execution concerns.
 */

function renderBacktest(container) {
  const today = new Date().toISOString().slice(0, 10);
  const oneYearAgo = (() => {
    const d = new Date(); d.setFullYear(d.getFullYear() - 1);
    return d.toISOString().slice(0, 10);
  })();

  container.innerHTML = `
    <div class="page-header">
      <div>
        <h2 class="text-xl">백테스트</h2>
        <p class="text-sm text-muted" id="bt-subtitle">과거 결정을 그대로 재생합니다 · 슬리피지·수수료 미포함</p>
      </div>
      <div class="tabs" id="bt-mode-tabs">
        <div class="tab active" data-mode="replay">Replay (과거 결정 그대로)</div>
        <div class="tab" data-mode="rescoring">Re-scoring (현재 가중치 적용)</div>
      </div>
    </div>

    <div class="card bt-controls">
      <div class="bt-control-row">
        <div class="form-row">
          <label>시작일</label>
          <input type="date" class="input" id="bt-start" value="${oneYearAgo}">
        </div>
        <div class="form-row">
          <label>종료일</label>
          <input type="date" class="input" id="bt-end" value="${today}">
        </div>
        <div class="form-row">
          <label>시장</label>
          <select class="input" id="bt-market">
            <option value="">전체</option>
            <option value="KR">KR</option>
            <option value="US">US</option>
          </select>
        </div>
        <div class="form-row">
          <label>초기 잔고</label>
          <input type="number" class="input" id="bt-balance" value="100000" min="1000">
        </div>
        <div class="form-row">
          <label>포지션 비율 (%)</label>
          <input type="number" class="input" id="bt-pos-pct" value="5" min="0.1" max="100" step="0.1">
        </div>
      </div>
      <div class="bt-control-row">
        <div class="form-row" style="flex: 1">
          <label>티커 (쉼표 구분 — 비우면 전체)</label>
          <input type="text" class="input" id="bt-tickers" placeholder="예: 005930, AAPL, NVDA">
        </div>
        <div class="form-row">
          <label>
            <input type="checkbox" id="bt-persist">
            결과 저장
          </label>
        </div>
        <div class="form-row" id="bt-label-row" hidden>
          <label>라벨</label>
          <input type="text" class="input" id="bt-label" placeholder="비교용 이름 (선택)">
        </div>
        <div class="form-row bt-run-cell">
          <label>&nbsp;</label>
          <button class="btn btn-primary" id="bt-run">${Utils.icon('play')} 실행</button>
        </div>
      </div>

      <!-- Re-scoring-only knobs -->
      <div class="bt-rescoring-section" id="bt-rescoring-knobs" hidden>
        <div class="bt-control-row">
          <div class="form-row">
            <label>BUY 임계값</label>
            <input type="number" class="input" id="bt-buy" value="25" step="0.1">
          </div>
          <div class="form-row">
            <label>SELL 임계값</label>
            <input type="number" class="input" id="bt-sell" value="-25" step="0.1">
          </div>
          <div class="form-row">
            <label>최소 confidence</label>
            <input type="number" class="input" id="bt-conf" value="0.40" min="0" max="1" step="0.01">
          </div>
          <div class="form-row">
            <label>쿨다운 (일)</label>
            <input type="number" class="input" id="bt-cooldown" value="1" min="0" max="30">
          </div>
          <div class="form-row">
            <label>스코어 신선도 (시간)</label>
            <input type="number" class="input" id="bt-stale" value="48" min="1" max="720">
          </div>
        </div>
        <div class="bt-control-row">
          <div class="form-row">
            <label>
              <input type="checkbox" id="bt-use-learned" checked>
              학습된 cluster weights 사용
            </label>
          </div>
          <div class="form-row">
            <label>또는 override (F,T,I 합 1.0)</label>
            <input type="text" class="input" id="bt-weight-override"
                   placeholder='{"F":0.4,"T":0.4,"I":0.2}'>
          </div>
        </div>
      </div>
    </div>

    <div id="bt-result"></div>

    <div class="card animate-fade-in-up" id="bt-runs-card">
      <div class="card-header">
        <div>
          <div class="card-title">저장된 실행 (비교)</div>
          <div class="card-subtitle text-xs text-muted" id="bt-runs-sub">—</div>
        </div>
        <div class="page-header-actions">
          <button class="btn btn-secondary btn-sm" id="bt-runs-refresh">${Utils.icon('refresh-cw')} 새로고침</button>
          <button class="btn btn-secondary btn-sm" id="bt-runs-compare" disabled>비교 차트 그리기</button>
        </div>
      </div>
      <div id="bt-runs-pane"><div class="text-muted">불러오는 중…</div></div>
      <div id="bt-runs-compare-pane"></div>
    </div>
  `;

  container.querySelector('#bt-run').addEventListener('click', run);

  // Persist UI: show label only when persist is checked.
  container.querySelector('#bt-persist').addEventListener('change', (e) => {
    container.querySelector('#bt-label-row').hidden = !e.target.checked;
  });

  let currentMode = 'replay';
  container.querySelectorAll('#bt-mode-tabs .tab').forEach(t => {
    t.addEventListener('click', () => {
      container.querySelectorAll('#bt-mode-tabs .tab').forEach(x => x.classList.remove('active'));
      t.classList.add('active');
      currentMode = t.dataset.mode;
      const knobs = container.querySelector('#bt-rescoring-knobs');
      knobs.hidden = currentMode !== 'rescoring';
      container.querySelector('#bt-subtitle').textContent = currentMode === 'replay'
        ? '과거 결정을 그대로 재생합니다 · 슬리피지·수수료 미포함'
        : '현재 학습된 가중치를 과거 module_scores에 적용해 결정을 재생성합니다';
    });
  });

  async function run() {
    const startVal = container.querySelector('#bt-start').value;
    const endVal = container.querySelector('#bt-end').value;
    const marketVal = container.querySelector('#bt-market').value || null;
    const balanceVal = Number(container.querySelector('#bt-balance').value);
    const posPctVal = Number(container.querySelector('#bt-pos-pct').value);
    const tickersStr = container.querySelector('#bt-tickers').value.trim();
    const tickers = tickersStr
      ? tickersStr.split(',').map(s => s.trim()).filter(Boolean)
      : null;

    if (!startVal || !endVal) {
      alert('시작일과 종료일을 모두 입력해주세요.');
      return;
    }
    if (!(balanceVal > 0)) {
      alert('초기 잔고는 양수여야 합니다.');
      return;
    }
    if (!(posPctVal > 0 && posPctVal <= 100)) {
      alert('포지션 비율은 0보다 크고 100 이하여야 합니다.');
      return;
    }

    const result = container.querySelector('#bt-result');
    const btn = container.querySelector('#bt-run');
    btn.disabled = true; btn.textContent = '실행 중…';
    result.innerHTML = `<div class="card empty-state text-muted">시뮬레이션 중…</div>`;

    const persistVal = container.querySelector('#bt-persist').checked;
    const labelVal = container.querySelector('#bt-label').value.trim() || null;

    try {
      const baseParams = {
        start: new Date(startVal + 'T00:00:00Z').toISOString(),
        end: new Date(endVal + 'T23:59:59Z').toISOString(),
        market: marketVal,
        tickers,
        initialBalance: balanceVal,
        positionFraction: posPctVal / 100,
        persist: persistVal,
        label: labelVal,
      };
      let data;
      if (currentMode === 'replay') {
        data = await Api.backtestRun(baseParams);
      } else {
        const buyVal = Number(container.querySelector('#bt-buy').value);
        const sellVal = Number(container.querySelector('#bt-sell').value);
        const confVal = Number(container.querySelector('#bt-conf').value);
        const cooldownVal = Number(container.querySelector('#bt-cooldown').value);
        const staleVal = Number(container.querySelector('#bt-stale').value);
        const useLearned = container.querySelector('#bt-use-learned').checked;
        const overrideStr = container.querySelector('#bt-weight-override').value.trim();
        let weightOverride = null;
        if (overrideStr) {
          try {
            weightOverride = JSON.parse(overrideStr);
          } catch (e) {
            alert('weight override JSON 파싱 실패: ' + e.message);
            return;
          }
        }
        data = await Api.backtestRescoring({
          ...baseParams,
          buyThreshold: buyVal,
          sellThreshold: sellVal,
          minOverallConfidence: confVal,
          decisionCooldownDays: cooldownVal,
          scoreStalenessHours: staleVal,
          useLearnedWeights: useLearned,
          weightOverride,
        });
      }
      renderResult(result, data);
      if (persistVal) {
        // Refresh the saved-runs list so the new row appears immediately.
        reloadRuns();
      }
    } catch (err) {
      result.innerHTML = `<div class="card empty-state text-loss">${escape(err.message)}</div>`;
    } finally {
      btn.disabled = false;
      btn.innerHTML = Utils.icon('play') + ' 실행';
    }
  }

  function renderResult(pane, data) {
    if (!data.closed_trades.length && !data.open_positions.length) {
      pane.innerHTML = `
        <div class="card empty-state">
          <div class="text-muted">이 기간에 시뮬레이션 가능한 결정이 없습니다.</div>
          <div class="text-xs text-muted">스킵된 시그널: ${data.skipped_signals}건</div>
        </div>`;
      return;
    }

    pane.innerHTML = `
      <div class="card animate-fade-in-up">
        <div class="card-header"><div class="card-title">자본 곡선 + 요약</div></div>
        <div class="equity-curve-row">
          <div class="equity-chart-cell">
            ${renderBtSparkline(data.points, data.initial_balance)}
          </div>
          <div class="equity-stats-cell">
            ${btStat('총 수익률', fmtPctBt(data.summary.return_pct),
              data.summary.return_pct >= 0 ? 'profit' : 'loss')}
            ${btStat('Final Equity', fmtMoney(data.final_equity),
              data.final_equity >= data.initial_balance ? 'profit' : 'loss')}
            ${btStat('실현 P&L', fmtMoney(data.summary.total_realized_pnl),
              data.summary.total_realized_pnl >= 0 ? 'profit' : 'loss')}
            ${btStat('Max Drawdown', fmtPctBt(data.summary.max_drawdown), 'loss')}
            ${btStat('Sharpe-like', data.summary.sharpe_like.toFixed(2),
              data.summary.sharpe_like >= 0 ? 'profit' : 'loss')}
            ${btStat('총 거래 / 승률',
              `${data.summary.total_trades} / ${(data.summary.win_rate * 100).toFixed(1)}%`, null)}
            ${btStat('베스트 / 워스트',
              `${fmtMoney(data.summary.best_trade_pnl)} / ${fmtMoney(data.summary.worst_trade_pnl)}`, null)}
            ${btStat('스킵 시그널 / 오픈 잔량',
              `${data.skipped_signals} / ${data.open_positions.length}`, null)}
          </div>
        </div>
      </div>

      <div class="card animate-fade-in-up">
        <div class="card-header">
          <div class="card-title">청산된 거래 (${data.closed_trades.length})</div>
        </div>
        ${renderTradesTable(data.closed_trades)}
      </div>

      ${data.open_positions.length ? `
        <div class="card animate-fade-in-up">
          <div class="card-header"><div class="card-title">백테스트 종료 시점 미청산 포지션 (${data.open_positions.length})</div></div>
          ${renderOpenTable(data.open_positions)}
        </div>` : ''}
    `;
  }

  function renderTradesTable(trades) {
    if (!trades.length) return `<div class="empty-state text-muted">청산된 거래가 없습니다.</div>`;
    const sorted = trades.slice().sort((a, b) => new Date(b.exit_ts) - new Date(a.exit_ts));
    return `
      <table class="data-table">
        <thead>
          <tr>
            <th>시장</th><th>티커</th>
            <th>진입가 → 청산가</th>
            <th>수량</th>
            <th>P&L</th>
            <th>보유 (일)</th>
            <th>청산 시각</th>
          </tr>
        </thead>
        <tbody>
          ${sorted.map(t => {
            const holdDays = ((new Date(t.exit_ts) - new Date(t.entry_ts)) / 86400000).toFixed(0);
            return `
              <tr>
                <td><span class="badge badge-info">${escape(t.market)}</span></td>
                <td class="mono">${escape(t.ticker)}</td>
                <td class="mono">${t.entry_price.toFixed(2)} → ${t.exit_price.toFixed(2)}</td>
                <td class="mono">${t.volume.toFixed(2)}</td>
                <td class="mono ${t.pnl >= 0 ? 'text-profit' : 'text-loss'}">${fmtMoney(t.pnl)}</td>
                <td class="mono">${holdDays}</td>
                <td class="text-xs text-muted">${Utils.formatDateTime(t.exit_ts)}</td>
              </tr>
            `;
          }).join('')}
        </tbody>
      </table>
    `;
  }

  function renderOpenTable(opens) {
    return `
      <table class="data-table">
        <thead>
          <tr><th>시장</th><th>티커</th><th>수량</th><th>진입가</th><th>진입 시각</th></tr>
        </thead>
        <tbody>
          ${opens.map(p => `
            <tr>
              <td><span class="badge badge-info">${escape(p.market)}</span></td>
              <td class="mono">${escape(p.ticker)}</td>
              <td class="mono">${p.volume.toFixed(2)}</td>
              <td class="mono">${p.entry_price.toFixed(2)}</td>
              <td class="text-xs text-muted">${Utils.formatDateTime(p.entry_ts)}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
  }

  function renderBtSparkline(points, initialBalance) {
    if (!points.length) return '';
    const W = 800, H = 220, PAD_L = 36, PAD_R = 12, PAD_T = 12, PAD_B = 24;
    const innerW = W - PAD_L - PAD_R, innerH = H - PAD_T - PAD_B;
    const eq = points.map(p => p.equity);
    const lo = Math.min(initialBalance, ...eq);
    const hi = Math.max(initialBalance, ...eq);
    const range = (hi - lo) || 1;
    const x = (i) => PAD_L + innerW * (i / Math.max(points.length - 1, 1));
    const y = (v) => PAD_T + innerH * (1 - (v - lo) / range);
    const linePath = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${x(i).toFixed(1)} ${y(p.equity).toFixed(1)}`).join(' ');
    const baseY = y(initialBalance);
    const final = points[points.length - 1].equity;
    const profitable = final >= initialBalance;
    const areaPath = `${linePath} L ${x(points.length - 1).toFixed(1)} ${baseY.toFixed(1)} L ${x(0).toFixed(1)} ${baseY.toFixed(1)} Z`;

    // 3 y-tick labels
    const yTicks = [lo, (lo + hi) / 2, hi].map(v => {
      const yy = y(v);
      return `<text x="${PAD_L - 4}" y="${(yy + 3).toFixed(1)}" class="eq-xlabel" text-anchor="end">${fmtMoneyShort(v)}</text>`;
    }).join('');

    const xLabels = [0, Math.floor(points.length / 2), points.length - 1].map(i => {
      return `<text x="${x(i).toFixed(1)}" y="${H - 8}" class="eq-xlabel" text-anchor="middle">${points[i].date.slice(5)}</text>`;
    }).join('');

    return `
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="equity-sparkline" style="height:220px">
        <path d="${areaPath}" class="eq-area ${profitable ? 'eq-area-profit' : 'eq-area-loss'}"/>
        <line x1="${PAD_L}" x2="${W - PAD_R}" y1="${baseY.toFixed(1)}" y2="${baseY.toFixed(1)}" class="eq-baseline"/>
        <path d="${linePath}" class="eq-line ${profitable ? 'eq-line-profit' : 'eq-line-loss'}" fill="none"/>
        ${yTicks}
        ${xLabels}
      </svg>
    `;
  }

  // ── Saved runs (comparison) ────────────────────────────────────
  let savedRunsCache = [];

  container.querySelector('#bt-runs-refresh').addEventListener('click', reloadRuns);
  container.querySelector('#bt-runs-compare').addEventListener('click', drawComparison);

  reloadRuns();

  async function reloadRuns() {
    const pane = container.querySelector('#bt-runs-pane');
    const sub = container.querySelector('#bt-runs-sub');
    pane.innerHTML = `<div class="text-muted">불러오는 중…</div>`;
    container.querySelector('#bt-runs-compare-pane').innerHTML = '';
    try {
      const runs = await Api.backtestListRuns({ limit: 200 });
      savedRunsCache = runs;
      sub.textContent = `${runs.length}건 저장됨`;
      if (!runs.length) {
        pane.innerHTML = `
          <div class="empty-state text-muted">
            저장된 실행이 없습니다. 위에서 "결과 저장" 체크 후 실행하면 여기에 누적됩니다.
          </div>`;
        container.querySelector('#bt-runs-compare').disabled = true;
        return;
      }
      pane.innerHTML = `
        <table class="data-table">
          <thead>
            <tr>
              <th><input type="checkbox" id="bt-runs-all"></th>
              <th>라벨</th>
              <th>모드</th>
              <th>시장</th>
              <th>윈도</th>
              <th>수익률</th>
              <th>거래</th>
              <th>승률</th>
              <th>Max DD</th>
              <th>Sharpe</th>
              <th>실행자</th>
              <th>저장 시각</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            ${runs.map(r => `
              <tr>
                <td><input type="checkbox" class="bt-run-pick" data-id="${escape(r.id)}"></td>
                <td><strong>${escape(r.label)}</strong>${r.notes ? `<div class="text-xs text-muted">${escape(r.notes)}</div>` : ''}</td>
                <td><span class="badge ${r.mode === 'rescoring' ? 'badge-info' : 'badge-neutral'}">${escape(r.mode)}</span></td>
                <td>${r.market ? `<span class="badge badge-info">${escape(r.market)}</span>` : '<span class="text-muted">ALL</span>'}</td>
                <td class="text-xs text-muted">${r.window_start.slice(0,10)} ~ ${r.window_end.slice(0,10)}</td>
                <td class="mono ${(r.return_pct ?? 0) >= 0 ? 'text-profit' : 'text-loss'}">${fmtPctBt(r.return_pct)}</td>
                <td class="mono">${r.total_trades}</td>
                <td class="mono">${r.win_rate != null ? (r.win_rate * 100).toFixed(1) + '%' : '—'}</td>
                <td class="mono ${(r.max_drawdown ?? 0) > 0.10 ? 'text-loss' : ''}">${r.max_drawdown != null ? (r.max_drawdown * 100).toFixed(2) + '%' : '—'}</td>
                <td class="mono">${r.sharpe_like != null ? r.sharpe_like.toFixed(2) : '—'}</td>
                <td class="text-xs">${escape(r.triggered_by)}</td>
                <td class="text-xs text-muted">${Utils.timeAgo(r.created_at)} 전</td>
                <td><button class="btn btn-secondary btn-sm bt-run-del" data-id="${escape(r.id)}">${Utils.icon('x')}</button></td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `;

      // Checkbox plumbing
      const compareBtn = container.querySelector('#bt-runs-compare');
      const updateCompareBtn = () => {
        const picked = pane.querySelectorAll('.bt-run-pick:checked').length;
        compareBtn.disabled = picked < 1;
        compareBtn.textContent = picked
          ? `비교 차트 (${picked}건)`
          : '비교 차트 그리기';
      };
      pane.querySelectorAll('.bt-run-pick').forEach(cb => {
        cb.addEventListener('change', updateCompareBtn);
      });
      const allBox = pane.querySelector('#bt-runs-all');
      if (allBox) {
        allBox.addEventListener('change', () => {
          pane.querySelectorAll('.bt-run-pick').forEach(cb => { cb.checked = allBox.checked; });
          updateCompareBtn();
        });
      }
      // Delete buttons
      pane.querySelectorAll('.bt-run-del').forEach(btn => {
        btn.addEventListener('click', async () => {
          if (!confirm('이 실행 결과를 삭제할까요?')) return;
          try {
            await Api.backtestDeleteRun(btn.dataset.id);
            reloadRuns();
          } catch (err) {
            alert('삭제 실패: ' + err.message);
          }
        });
      });
      updateCompareBtn();
    } catch (err) {
      pane.innerHTML = `<div class="empty-state text-loss">${escape(err.message)}</div>`;
    }
  }

  async function drawComparison() {
    const ids = Array.from(container.querySelectorAll('.bt-run-pick:checked'))
      .map(cb => cb.dataset.id);
    if (!ids.length) return;
    const pane = container.querySelector('#bt-runs-compare-pane');
    pane.innerHTML = `<div class="text-muted">비교 데이터 불러오는 중…</div>`;
    try {
      const details = await Promise.all(ids.map(id => Api.backtestGetRun(id)));
      pane.innerHTML = renderComparisonChart(details);
    } catch (err) {
      pane.innerHTML = `<div class="text-loss">${escape(err.message)}</div>`;
    }
  }

  function renderComparisonChart(details) {
    // Each detail has equity_points = [{date, equity, drawdown}].
    // Normalise to per-run % return so different starting balances
    // line up visually.
    const W = 900, H = 320, PAD_L = 56, PAD_R = 16, PAD_T = 16, PAD_B = 32;
    const innerW = W - PAD_L - PAD_R;
    const innerH = H - PAD_T - PAD_B;

    const series = details.map(d => {
      const pts = (d.equity_points || []).filter(p => p && p.equity != null);
      if (!pts.length) return null;
      const ib = d.initial_balance || 1;
      return {
        label: d.label,
        market: d.market,
        mode: d.mode,
        points: pts.map(p => ({
          ts: new Date(p.date).getTime(),
          ret: (p.equity - ib) / ib,
        })),
      };
    }).filter(Boolean);
    if (!series.length) {
      return `<div class="empty-state text-muted">선택된 실행에 자본곡선 데이터가 없습니다.</div>`;
    }

    const allTs = series.flatMap(s => s.points.map(p => p.ts));
    const allRet = series.flatMap(s => s.points.map(p => p.ret));
    const tMin = Math.min(...allTs), tMax = Math.max(...allTs);
    const tRange = (tMax - tMin) || 1;
    const yMin = Math.min(0, ...allRet);
    const yMax = Math.max(0, ...allRet);
    const yRange = (yMax - yMin) || 1;
    const x = (t) => PAD_L + innerW * (t - tMin) / tRange;
    const y = (r) => PAD_T + innerH * (1 - (r - yMin) / yRange);

    const colors = ['#3b82f6', '#8b5cf6', '#06b6d4', '#10b981', '#f59e0b', '#ef4444', '#ec4899', '#14b8a6'];

    const paths = series.map((s, i) => {
      const color = colors[i % colors.length];
      const d = s.points.map((p, j) => `${j === 0 ? 'M' : 'L'} ${x(p.ts).toFixed(1)} ${y(p.ret).toFixed(1)}`).join(' ');
      return `<path d="${d}" stroke="${color}" stroke-width="1.8" fill="none"/>`;
    }).join('');

    // Zero line
    const zeroY = y(0);
    const zeroLine = `<line x1="${PAD_L}" x2="${W - PAD_R}" y1="${zeroY.toFixed(1)}" y2="${zeroY.toFixed(1)}" class="overlay-zero overlay-gridline"/>`;

    // Y ticks: yMin, 0, yMax
    const yTicks = [yMin, 0, yMax].map(v => {
      const yy = y(v);
      return `
        <line x1="${PAD_L}" x2="${W - PAD_R}" y1="${yy.toFixed(1)}" y2="${yy.toFixed(1)}" class="overlay-gridline"/>
        <text x="${PAD_L - 8}" y="${(yy + 3).toFixed(1)}" text-anchor="end" class="overlay-ytick">${fmtPctBt(v)}</text>
      `;
    }).join('');

    // X ticks
    const xTicks = [tMin, (tMin + tMax) / 2, tMax].map(t => {
      const d = new Date(t).toISOString().slice(0, 10);
      return `<text x="${x(t).toFixed(1)}" y="${H - 10}" text-anchor="middle" class="overlay-xlabel">${d}</text>`;
    }).join('');

    const legend = series.map((s, i) => `
      <span class="bt-compare-legend-item">
        <span class="bt-compare-swatch" style="background:${colors[i % colors.length]}"></span>
        <strong>${escape(s.label)}</strong>
        <span class="text-xs text-muted">${escape(s.mode)}${s.market ? ' · ' + s.market : ''}</span>
      </span>
    `).join('');

    return `
      <div style="margin-top: var(--space-3)">
        <svg viewBox="0 0 ${W} ${H}" class="bt-compare-chart" preserveAspectRatio="xMidYMid meet">
          ${yTicks}
          ${zeroLine}
          ${paths}
          ${xTicks}
        </svg>
        <div class="bt-compare-legend">${legend}</div>
      </div>
    `;
  }
}

function btStat(label, value, tone) {
  const cls = tone === 'profit' ? 'text-profit'
            : tone === 'loss'   ? 'text-loss'
            : '';
  return `
    <div class="equity-stat">
      <div class="equity-stat-label">${label}</div>
      <div class="equity-stat-value ${cls}">${value}</div>
    </div>
  `;
}

function fmtPctBt(v) {
  if (v === null || v === undefined || isNaN(v)) return '—';
  const sign = v > 0 ? '+' : '';
  return sign + (v * 100).toFixed(2) + '%';
}

function fmtMoney(v) {
  return (v >= 0 ? '+' : '') + v.toLocaleString('en-US', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  });
}

function fmtMoneyShort(v) {
  if (Math.abs(v) >= 1_000_000) return (v / 1_000_000).toFixed(1) + 'M';
  if (Math.abs(v) >= 1_000)     return (v / 1_000).toFixed(0) + 'K';
  return v.toFixed(0);
}

function escape(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

window.renderBacktest = renderBacktest;
