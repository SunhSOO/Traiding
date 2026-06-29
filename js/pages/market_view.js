/**
 * 시황 (Market Read) — "지금 시장에 얼마나 투자할까".
 *
 * Top layer of the integrated pipeline: per-market regime + breadth +
 * conviction → recommended exposure overlay. Designed for the NEW direction
 * (index/market read drives HOW MUCH to be invested), not a copy of the
 * existing per-stock dashboard.
 */

function _regimeClass(regime) {
  const r = (regime || '').toUpperCase();
  if (r.includes('CRISIS') || r === 'RISK_OFF') return 'badge-danger';
  if (r === 'RISK_ON' || r === 'CALM_BULL') return 'badge-success';
  return 'badge-secondary';
}

function _gauge(pct, cls) {
  const w = Math.max(0, Math.min(100, pct));
  return `<div class="ix-gauge"><div class="ix-gauge-fill ${cls || ''}" style="width:${w}%"></div></div>`;
}

function _marketCard(m) {
  const expoPct = Math.round((m.target_exposure || 0) * 100);
  const breadthPct = Math.round((m.breadth || 0) * 100);
  const convPct = Math.round((m.avg_conviction || 0) * 200);   // 0..0.5 → 0..100
  const expoCls = expoPct >= 70 ? 'ix-fill-green' : expoPct >= 40 ? 'ix-fill-amber' : 'ix-fill-red';
  return `
    <div class="card animate-fade-in-up">
      <div class="card-header">
        <div>
          <div class="card-title">${m.market === 'KR' ? '🇰🇷 한국' : '🇺🇸 미국'}</div>
          <div class="card-subtitle text-xs text-muted">기준일 ${m.as_of}</div>
        </div>
        <span class="badge ${_regimeClass(m.regime)}">${m.regime}
          <span class="text-xs text-muted">· 신뢰 ${Math.round((m.regime_conf || 0) * 100)}%</span>
        </span>
      </div>
      <div class="ix-expo">
        <div class="ix-expo-num">${expoPct}<span class="text-lg text-muted">%</span></div>
        <div class="text-sm text-muted">권장 투자 비중 (목표 노출)</div>
        ${_gauge(expoPct, expoCls)}
      </div>
      <div class="ix-metric-grid">
        <div class="ix-metric">
          <div class="ix-metric-label">시장 폭 (Breadth)</div>
          ${_gauge(breadthPct, 'ix-fill-blue')}
          <div class="ix-metric-val">${breadthPct}% <span class="text-xs text-muted">상승 예상 종목</span></div>
        </div>
        <div class="ix-metric">
          <div class="ix-metric-label">선정 확신도</div>
          ${_gauge(convPct, 'ix-fill-blue')}
          <div class="ix-metric-val">${convPct}<span class="text-xs text-muted">/100</span></div>
        </div>
      </div>
      <div class="ix-foot text-xs text-muted">
        유니버스 ${m.n_universe ?? '—'}종목 → 바스켓 <b>${m.n_basket ?? '—'}</b>종목 선정
      </div>
    </div>`;
}

async function renderMarketView(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h2 class="text-xl">시황 읽기</h2>
        <p class="text-sm text-muted">레짐·시장폭·확신도 → 지금 시장에 얼마나 투자할지(목표 노출)를 결정합니다</p>
      </div>
      <div class="page-header-actions">
        <button class="btn btn-secondary btn-sm" id="mv-refresh">${Utils.icon('refresh-cw')} 새로고침</button>
      </div>
    </div>
    <div class="ix-card-row" id="mv-cards"><div class="empty-state text-muted">불러오는 중…</div></div>
    <div class="card">
      <div class="card-header"><div class="card-title">노출 규칙</div></div>
      <div class="text-sm text-muted ix-rule">
        레짐 기준 노출(위기 0% · 위험회피 40% · 중립 70% · 위험선호 100%)에
        시장폭이 얕으면 추가 축소(× min(1, 0.4+breadth)). 이 비중이 선정 바스켓 전체에 곱해집니다.
      </div>
    </div>`;

  async function load() {
    const pane = container.querySelector('#mv-cards');
    try {
      const rows = await api.marketRead();
      if (!rows || rows.length === 0) {
        pane.innerHTML = `<div class="empty-state text-muted">시황 데이터가 아직 없습니다. integrated.daily 잡 실행 후 표시됩니다.</div>`;
        return;
      }
      pane.innerHTML = rows.map(_marketCard).join('');
    } catch (e) {
      pane.innerHTML = `<div class="empty-state text-danger">불러오기 실패: ${Utils.escape(String(e.message || e))}</div>`;
    }
  }
  container.querySelector('#mv-refresh').addEventListener('click', load);
  await load();
}
