/**
 * 실행 (Execution Timing) — "바스켓 종목을 언제 살까/팔까".
 *
 * Bottom layer: per-stock technical timing gate (D1 = timing only, never a
 * veto). Shows the latest integrated decision batch — each name's action
 * (BUY/WAIT/SELL/REJECTED), conviction, technical score, timing reason.
 */

function _actionBadge(action) {
  const map = {
    BUY: ['badge-success', '매수'],
    SELL: ['badge-danger', '매도'],
    WAIT: ['badge-warning', '대기'],
    REJECTED: ['badge-muted', '거절'],
  };
  const [cls, label] = map[action] || ['badge-secondary', action];
  return `<span class="badge ${cls}">${label}</span>`;
}

function _timingLabel(t) {
  const map = {
    enter_ok: '진입 가능', wait_tech: '기술적 약세 → 진입 지연',
    exit_basket_drop: '바스켓 이탈', exit_tech_breakdown: '기술적 붕괴',
    exit_defensive: '방어 청산',
  };
  return map[t] || (t || '—');
}

function _techCell(score) {
  if (score == null) return '<span class="text-muted">—</span>';
  const s = Math.round(score);
  const cls = s >= 0 ? 'text-success' : 'text-danger';
  return `<span class="${cls}">${s > 0 ? '+' : ''}${s}</span>`;
}

function _execRow(r) {
  const conv = r.rank_pct != null ? `상위 ${(100 - r.rank_pct * 100).toFixed(0)}%` : '—';
  const size = r.size_value != null ? Utils.formatNumber(r.size_value, 0) : '—';
  const filled = r.filled === true ? '✅' : r.filled === false ? '❌' : '—';
  return `
    <tr>
      <td><b>${Utils.escape(r.ticker)}</b></td>
      <td>${_actionBadge(r.action)}</td>
      <td class="text-muted">${conv}</td>
      <td class="text-center">${_techCell(r.tech_score)}</td>
      <td class="text-muted text-xs">${_timingLabel(r.timing)}</td>
      <td class="text-right">${size}</td>
      <td class="text-center">${filled}</td>
    </tr>`;
}

async function renderExecution(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h2 class="text-xl">실행 타이밍</h2>
        <p class="text-sm text-muted">기술적 지표(추세·모멘텀·평균회귀·레드그린)가 바스켓 종목의 진입/청산 시점만 판단 — 종목 선정은 거부하지 않습니다(D1)</p>
      </div>
      <div class="page-header-actions" id="exec-tabs"></div>
    </div>
    <div class="ix-legend text-xs text-muted">
      <span class="badge badge-success">매수</span> 진입
      <span class="badge badge-warning">대기</span> 기술적 약세로 진입 지연
      <span class="badge badge-danger">매도</span> 청산
      <span class="badge badge-muted">거절</span> 리스크 한도
    </div>
    <div class="card animate-fade-in-up">
      <div class="card-header"><div class="card-title">최근 실행 배치</div>
        <div class="card-subtitle text-xs text-muted" id="exec-asof">—</div></div>
      <div class="table-wrap">
        <table class="table table-compact">
          <thead><tr>
            <th>종목</th><th>판정</th><th>확신도</th><th class="text-center">기술점수</th>
            <th>타이밍 사유</th><th class="text-right">규모</th><th class="text-center">체결</th>
          </tr></thead>
          <tbody id="exec-body"><tr><td colspan="7" class="empty-state text-muted">불러오는 중…</td></tr></tbody>
        </table>
      </div>
    </div>`;

  async function load(market) {
    const body = container.querySelector('#exec-body');
    try {
      const data = await api.execution({ market });
      container.querySelector('#exec-asof').textContent = data.as_of ? `기준 ${data.as_of}` : '';
      body.innerHTML = (data.rows && data.rows.length)
        ? data.rows.map(_execRow).join('')
        : `<tr><td colspan="7" class="empty-state text-muted">실행 기록이 아직 없습니다. integrated.daily 잡 실행 후 표시됩니다.</td></tr>`;
    } catch (e) {
      body.innerHTML = `<tr><td colspan="7" class="empty-state text-danger">불러오기 실패: ${Utils.escape(String(e.message || e))}</td></tr>`;
    }
  }

  MarketTab.mount({ parent: container.querySelector('#exec-tabs'), onChange: (m) => load(m) });
}
