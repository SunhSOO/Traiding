/**
 * Macro indicator page.
 *
 * Top: headline snapshot strip — each series' latest value + 1d/1w/30d change
 * Bottom: clickable series list; selecting one renders a sparkline
 * with 1Y / 5Y / 10Y range toggles.
 */

function renderMacro(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h2 class="text-xl">매크로 지표</h2>
        <p class="text-sm text-muted">금리 / FX / 변동성 / 인덱스 — 결정 시점의 시장 환경</p>
      </div>
      <div class="page-header-actions">
        <button class="btn btn-secondary btn-sm" id="macro-refresh">${Utils.icon('refresh-cw')} 새로고침</button>
      </div>
    </div>

    <div class="card animate-fade-in-up" id="macro-regime-card">
      <div class="card-header">
        <div>
          <div class="card-title">시장 레짐</div>
          <div class="card-subtitle text-xs text-muted" id="macro-regime-sub">—</div>
        </div>
        <button class="btn btn-secondary btn-sm" id="macro-regime-run" title="현재 macro 입력으로 재분류">${Utils.icon('zap')} 재분류</button>
      </div>
      <div id="macro-regime-pane"><div class="empty-state text-muted">불러오는 중…</div></div>
    </div>

    <div class="card animate-fade-in-up">
      <div class="card-header"><div class="card-title">헤드라인</div></div>
      <div id="macro-snapshot-pane"><div class="empty-state text-muted">불러오는 중…</div></div>
    </div>

    <div class="macro-detail-row">
      <div class="card animate-fade-in-up">
        <div class="card-header">
          <div class="card-title">모든 시리즈</div>
          <span class="text-xs text-muted" id="macro-series-sub">—</span>
        </div>
        <div id="macro-series-pane"><div class="empty-state text-muted">불러오는 중…</div></div>
      </div>
      <div class="card animate-fade-in-up">
        <div class="card-header">
          <div class="card-title" id="macro-detail-title">시리즈를 선택하세요</div>
          <div class="tabs" id="macro-range-tabs">
            <div class="tab" data-range="90">3M</div>
            <div class="tab" data-range="365">1Y</div>
            <div class="tab active" data-range="1825">5Y</div>
            <div class="tab" data-range="3650">10Y</div>
          </div>
        </div>
        <div id="macro-detail-pane"><div class="empty-state text-muted">왼쪽에서 시리즈를 클릭하세요.</div></div>
      </div>
    </div>
  `;

  let currentRange = 1825;
  let currentSeries = null;

  container.querySelector('#macro-refresh').addEventListener('click', reloadAll);
  container.querySelectorAll('#macro-range-tabs .tab').forEach(t => {
    t.addEventListener('click', () => {
      container.querySelectorAll('#macro-range-tabs .tab').forEach(x => x.classList.remove('active'));
      t.classList.add('active');
      currentRange = Number(t.dataset.range);
      if (currentSeries) loadDetail(currentSeries);
    });
  });
  container.querySelector('#macro-regime-run').addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      await Api.regimeRun();
      await loadRegime();
    } catch (err) {
      alert('재분류 실패: ' + err.message);
    } finally {
      btn.disabled = false;
    }
  });

  reloadAll();

  async function reloadAll() {
    await Promise.all([loadRegime(), loadSnapshot(), loadSeries()]);
  }

  async function loadRegime() {
    const pane = container.querySelector('#macro-regime-pane');
    const sub = container.querySelector('#macro-regime-sub');
    try {
      const [current, krHist, usHist] = await Promise.all([
        Api.regimeCurrent(),
        Api.regimeHistory({ market: 'KR', days: 180 }).catch(() => ({ days: [] })),
        Api.regimeHistory({ market: 'US', days: 180 }).catch(() => ({ days: [] })),
      ]);
      if (!current.length) {
        sub.textContent = '레짐 데이터 없음';
        pane.innerHTML = `
          <div class="empty-state text-muted">
            아직 레짐 분류 결과가 없습니다. 우상단 "재분류" 버튼을 누르거나
            <code>regime.daily</code> 잡이 한 번 돌면 채워집니다.
          </div>`;
        return;
      }
      sub.textContent = '오늘 시장 분위기 + 최근 180일 흐름';
      pane.innerHTML = `
        <div class="regime-row">
          ${current.map(r => regimeCard(r)).join('')}
        </div>
        <div class="regime-ribbon-row">
          ${ribbonBlock('KR', krHist.days || [])}
          ${ribbonBlock('US', usHist.days || [])}
        </div>
      `;
    } catch (err) {
      pane.innerHTML = `<div class="empty-state text-loss">${escape(err.message)}</div>`;
    }
  }

  function regimeCard(r) {
    const cls = r.label === 'RISK_ON' ? 'regime-on'
              : r.label === 'RISK_OFF' ? 'regime-off'
              : 'regime-neutral';
    const votes = (r.votes || []).filter(v => v.vote !== 0);
    return `
      <div class="regime-card ${cls}">
        <div class="regime-card-top">
          <span class="badge badge-info">${escape(r.market)}</span>
          <strong class="regime-label">${escape(r.label)}</strong>
          <span class="text-xs text-muted">confidence ${(r.confidence * 100).toFixed(0)}%</span>
        </div>
        <div class="regime-card-date text-xs text-muted">기준일 ${escape(r.ts)}</div>
        <ul class="regime-votes">
          ${(r.votes || []).map(v => `
            <li class="regime-vote vote-${v.vote > 0 ? 'on' : v.vote < 0 ? 'off' : 'neutral'}">
              <span class="regime-vote-mark">${v.vote > 0 ? '▲' : v.vote < 0 ? '▼' : '·'}</span>
              <span class="regime-vote-signal">${escape(v.signal)}</span>
              <span class="text-xs text-muted">${escape(v.detail || '')}</span>
            </li>
          `).join('')}
        </ul>
      </div>
    `;
  }

  function ribbonBlock(label, days) {
    if (!days.length) {
      return `<div class="regime-ribbon-block">
        <div class="text-xs text-muted">${label}: 이력 없음</div>
      </div>`;
    }
    // Simple coloured ribbon — 1 px per day, capped to 600 days
    const cells = days.slice(-600).map(d => {
      const cls = d.label === 'RISK_ON' ? 'regime-on'
                : d.label === 'RISK_OFF' ? 'regime-off'
                : 'regime-neutral';
      const op = 0.5 + d.confidence * 0.5;
      return `<span class="regime-ribbon-cell ${cls}" style="opacity:${op.toFixed(2)}" title="${d.ts} · ${d.label} (${(d.confidence * 100).toFixed(0)}%)"></span>`;
    }).join('');
    return `
      <div class="regime-ribbon-block">
        <div class="text-xs text-muted">${label} · ${days.length}일</div>
        <div class="regime-ribbon">${cells}</div>
      </div>
    `;
  }

  async function loadSnapshot() {
    const pane = container.querySelector('#macro-snapshot-pane');
    try {
      const rows = await Api.macroSnapshot();
      if (!rows.length) {
        pane.innerHTML = `<div class="empty-state text-muted">매크로 데이터가 없습니다. macro.daily 잡이 돌면 채워집니다.</div>`;
        return;
      }
      pane.innerHTML = `
        <div class="macro-snapshot-grid">
          ${rows.map(r => snapshotCell(r)).join('')}
        </div>
      `;
    } catch (err) {
      pane.innerHTML = `<div class="empty-state text-loss">${escape(err.message)}</div>`;
    }
  }

  async function loadSeries() {
    const pane = container.querySelector('#macro-series-pane');
    const sub = container.querySelector('#macro-series-sub');
    try {
      const rows = await Api.macroSeriesList();
      sub.textContent = `${rows.length}개 시리즈`;
      if (!rows.length) {
        pane.innerHTML = `<div class="empty-state text-muted">등록된 시리즈가 없습니다.</div>`;
        return;
      }
      pane.innerHTML = `
        <table class="data-table macro-series-table">
          <thead>
            <tr><th>시리즈</th><th>최근 값</th><th>1d</th><th>30d</th></tr>
          </thead>
          <tbody>
            ${rows.map(r => `
              <tr class="macro-series-row" data-code="${escape(r.series_code)}">
                <td>
                  <strong>${escape(r.label)}</strong>
                  <div class="text-xs text-muted">${escape(r.series_code)} · ${escape(r.source)}</div>
                </td>
                <td class="mono">${r.latest_value == null ? '—' : Utils.formatNumber(r.latest_value) + escape(r.unit)}</td>
                <td class="mono ${changeClass(r.change_1d)}">${fmtChange(r.change_1d)}</td>
                <td class="mono ${changeClass(r.change_30d)}">${fmtChange(r.change_30d)}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `;
      pane.querySelectorAll('.macro-series-row').forEach(tr => {
        tr.addEventListener('click', () => {
          pane.querySelectorAll('.macro-series-row').forEach(x => x.classList.remove('selected'));
          tr.classList.add('selected');
          currentSeries = tr.dataset.code;
          loadDetail(currentSeries);
        });
      });
    } catch (err) {
      pane.innerHTML = `<div class="empty-state text-loss">${escape(err.message)}</div>`;
    }
  }

  async function loadDetail(code) {
    const pane = container.querySelector('#macro-detail-pane');
    const title = container.querySelector('#macro-detail-title');
    pane.innerHTML = `<div class="empty-state text-muted">불러오는 중…</div>`;
    try {
      const data = await Api.macroSeriesDetail(code, { days: currentRange });
      title.innerHTML = `<strong>${escape(data.label)}</strong> <span class="text-xs text-muted">${escape(data.series_code)} · ${escape(data.source)}</span>`;
      pane.innerHTML = renderSeriesChart(data);
    } catch (err) {
      pane.innerHTML = `<div class="empty-state text-loss">${escape(err.message)}</div>`;
    }
  }
}

function snapshotCell(r) {
  if (r.latest_value == null) {
    return `
      <div class="macro-snap-card">
        <div class="macro-snap-label">${escape(r.label)}</div>
        <div class="macro-snap-value text-muted">—</div>
        <div class="macro-snap-meta text-xs text-muted">데이터 없음</div>
      </div>
    `;
  }
  const change30 = r.change_30d;
  return `
    <div class="macro-snap-card">
      <div class="macro-snap-label">${escape(r.label)}</div>
      <div class="macro-snap-value">${Utils.formatNumber(r.latest_value)}${escape(r.unit)}</div>
      <div class="macro-snap-meta">
        <span class="text-xs ${changeClass(r.change_1d)}">1d ${fmtChange(r.change_1d)}</span>
        <span class="text-xs ${changeClass(r.change_1w)}">1w ${fmtChange(r.change_1w)}</span>
        <span class="text-xs ${changeClass(r.change_30d)}">30d ${fmtChange(r.change_30d)}</span>
      </div>
    </div>
  `;
}

function fmtChange(v) {
  if (v == null || isNaN(v)) return '—';
  const sign = v > 0 ? '+' : '';
  return sign + (v * 100).toFixed(2) + '%';
}

function changeClass(v) {
  if (v == null) return 'text-muted';
  return v > 0 ? 'text-profit' : v < 0 ? 'text-loss' : '';
}

function renderSeriesChart(data) {
  if (!data.points.length) return `<div class="empty-state text-muted">데이터 없음</div>`;
  const W = 800, H = 280, PAD_L = 50, PAD_R = 12, PAD_T = 16, PAD_B = 28;
  const innerW = W - PAD_L - PAD_R, innerH = H - PAD_T - PAD_B;
  const vals = data.points.map(p => p.value);
  const minV = Math.min(...vals);
  const maxV = Math.max(...vals);
  const range = (maxV - minV) || 1;
  const x = (i) => PAD_L + innerW * (i / Math.max(data.points.length - 1, 1));
  const y = (v) => PAD_T + innerH * (1 - (v - minV) / range);
  const path = data.points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${x(i).toFixed(1)} ${y(p.value).toFixed(1)}`).join(' ');

  const yticks = [minV, (minV + maxV) / 2, maxV].map(v => {
    const yy = y(v);
    return `
      <line x1="${PAD_L}" x2="${W - PAD_R}" y1="${yy.toFixed(1)}" y2="${yy.toFixed(1)}" class="macro-gridline"/>
      <text x="${PAD_L - 6}" y="${(yy + 3).toFixed(1)}" text-anchor="end" class="macro-ytick">${Utils.formatNumber(v)}${escape(data.unit)}</text>
    `;
  }).join('');

  const xLabels = [0, Math.floor(data.points.length / 2), data.points.length - 1].map(i => {
    return `<text x="${x(i).toFixed(1)}" y="${H - 8}" text-anchor="middle" class="macro-xlabel">${data.points[i].ts}</text>`;
  }).join('');

  return `
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" class="macro-chart">
      ${yticks}
      <path d="${path}" class="macro-line" fill="none"/>
      ${xLabels}
    </svg>
    <div class="text-xs text-muted" style="text-align:center; margin-top:6px">
      ${data.points.length} 포인트 · ${data.points[0].ts} → ${data.points[data.points.length - 1].ts}
    </div>
  `;
}

function escape(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

window.renderMacro = renderMacro;
