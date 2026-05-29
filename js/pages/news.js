/**
 * News explorer.
 *
 * Three panels:
 *   1. Sentiment timeline (per-day pos/neutral/neg counts) — stacked bar
 *   2. Event-type mix (last 7d, doughnut-ish horizontal bars)
 *   3. Article list with filters (market/ticker/source/sentiment/event/impact)
 *
 * Sources data from /api/news/* endpoints. Classification rows
 * (sentiment / event_type / impact) join best-effort — older articles
 * not yet classified appear with — placeholders.
 */

function renderNews(container) {
  container.innerHTML = `
    <div class="page-header">
      <div>
        <h2 class="text-xl">뉴스 탐색</h2>
        <p class="text-sm text-muted">기사 + LLM 분류 (sentiment / event / impact) 통합 탐색</p>
      </div>
      <div class="page-header-actions" id="news-header-actions"></div>
    </div>

    <div class="news-top-row">
      <div class="card animate-fade-in-up">
        <div class="card-header">
          <div class="card-title">Sentiment 흐름 (30일)</div>
          <span class="text-xs text-muted" id="news-sent-sub">—</span>
        </div>
        <div id="news-sent-pane"><div class="empty-state text-muted">불러오는 중…</div></div>
      </div>
      <div class="card animate-fade-in-up">
        <div class="card-header">
          <div class="card-title">Event-type 분포 (7일)</div>
          <span class="text-xs text-muted" id="news-evt-sub">—</span>
        </div>
        <div id="news-evt-pane"><div class="empty-state text-muted">불러오는 중…</div></div>
      </div>
    </div>

    <div class="card animate-fade-in-up">
      <div class="card-header">
        <div class="card-title">기사 목록</div>
        <div class="filter-row">
          <input class="input" id="news-ticker" placeholder="티커 (선택)" style="width:140px">
          <select class="input" id="news-sent">
            <option value="">모든 sentiment</option>
            <option value="POSITIVE">POSITIVE</option>
            <option value="NEUTRAL">NEUTRAL</option>
            <option value="NEGATIVE">NEGATIVE</option>
          </select>
          <select class="input" id="news-evt">
            <option value="">모든 event</option>
            <option value="EARNINGS">EARNINGS</option>
            <option value="GUIDANCE">GUIDANCE</option>
            <option value="M_AND_A">M&A</option>
            <option value="REGULATORY">REGULATORY</option>
            <option value="MANAGEMENT">MANAGEMENT</option>
            <option value="PRODUCT">PRODUCT</option>
            <option value="MACRO">MACRO</option>
            <option value="INSIDER_TX">INSIDER_TX</option>
            <option value="OTHER">OTHER</option>
          </select>
          <select class="input" id="news-imp">
            <option value="">모든 impact</option>
            <option value="HIGH">HIGH</option>
            <option value="MEDIUM">MEDIUM</option>
            <option value="LOW">LOW</option>
          </select>
          <select class="input" id="news-days">
            <option value="1">1일</option>
            <option value="7" selected>7일</option>
            <option value="30">30일</option>
            <option value="90">90일</option>
          </select>
          <button class="btn btn-secondary btn-sm" id="news-refresh">${Utils.icon('refresh-cw')} 적용</button>
        </div>
      </div>
      <div id="news-articles-pane"><div class="empty-state text-muted">불러오는 중…</div></div>
      <div class="news-pager" id="news-pager"></div>
    </div>
  `;

  let currentMarket = AppState.getMarket();
  let offset = 0;
  const limit = 30;

  MarketTab.mount({
    parent: container.querySelector('#news-header-actions'),
    onChange: (m) => { currentMarket = m; offset = 0; reloadAll(); },
  });

  container.querySelector('#news-refresh').addEventListener('click', () => {
    offset = 0;
    reloadArticles();
  });
  ['news-ticker', 'news-sent', 'news-evt', 'news-imp', 'news-days'].forEach(id => {
    container.querySelector('#' + id).addEventListener('change', () => {
      offset = 0;
      reloadArticles();
    });
  });

  reloadAll();

  async function reloadAll() {
    await Promise.all([reloadSentiment(), reloadEvent(), reloadArticles()]);
  }

  async function reloadSentiment() {
    const pane = container.querySelector('#news-sent-pane');
    const sub = container.querySelector('#news-sent-sub');
    const ticker = container.querySelector('#news-ticker').value.trim() || null;
    try {
      const data = await Api.newsSentimentTimeline({
        market: currentMarket, ticker, days: 30,
      });
      const totalDays = data.days.length;
      const totalArticles = data.days.reduce((s, d) => s + d.positive + d.neutral + d.negative, 0);
      sub.textContent = `${totalDays}일 · ${totalArticles}건 분류됨${ticker ? ' · ' + ticker : ''}`;
      if (!data.days.length) {
        pane.innerHTML = `<div class="empty-state text-muted">분류된 기사가 없습니다.</div>`;
        return;
      }
      pane.innerHTML = renderSentChart(data.days);
    } catch (err) {
      pane.innerHTML = `<div class="empty-state text-loss">${escape(err.message)}</div>`;
    }
  }

  async function reloadEvent() {
    const pane = container.querySelector('#news-evt-pane');
    const sub = container.querySelector('#news-evt-sub');
    try {
      const data = await Api.newsEventMix({ market: currentMarket, days: 7 });
      sub.textContent = `${data.total_classifications}건 분류됨`;
      if (!data.buckets.length) {
        pane.innerHTML = `<div class="empty-state text-muted">분류된 이벤트가 없습니다.</div>`;
        return;
      }
      pane.innerHTML = renderEventMix(data.buckets);
    } catch (err) {
      pane.innerHTML = `<div class="empty-state text-loss">${escape(err.message)}</div>`;
    }
  }

  async function reloadArticles() {
    const pane = container.querySelector('#news-articles-pane');
    const pager = container.querySelector('#news-pager');
    const ticker = container.querySelector('#news-ticker').value.trim() || null;
    const sentiment = container.querySelector('#news-sent').value || null;
    const eventType = container.querySelector('#news-evt').value || null;
    const impact = container.querySelector('#news-imp').value || null;
    const days = Number(container.querySelector('#news-days').value);
    try {
      const data = await Api.newsArticles({
        market: currentMarket, ticker, sentiment,
        eventType, impact, days, limit, offset,
      });
      pane.innerHTML = renderArticleList(data.rows);
      pager.innerHTML = renderPager(data.total, offset, limit);
      pager.querySelector('[data-action="prev"]')?.addEventListener('click', () => {
        offset = Math.max(0, offset - limit);
        reloadArticles();
      });
      pager.querySelector('[data-action="next"]')?.addEventListener('click', () => {
        offset += limit;
        reloadArticles();
      });
    } catch (err) {
      pane.innerHTML = `<div class="empty-state text-loss">${escape(err.message)}</div>`;
    }
  }
}

// ── Renderers ────────────────────────────────────────────────────

function renderSentChart(days) {
  if (!days.length) return '';
  const W = 800, H = 180, PAD_L = 24, PAD_R = 12, PAD_T = 12, PAD_B = 24;
  const innerW = W - PAD_L - PAD_R, innerH = H - PAD_T - PAD_B;
  const maxTotal = Math.max(1, ...days.map(d => d.positive + d.neutral + d.negative));
  const colW = innerW / days.length * 0.7;
  const cellW = innerW / days.length;
  const y = (v) => PAD_T + innerH * (1 - v / maxTotal);

  const bars = days.map((d, i) => {
    const x = PAD_L + i * cellW + (cellW - colW) / 2;
    const total = d.positive + d.neutral + d.negative;
    let yCursor = PAD_T + innerH;
    const segments = [
      { v: d.negative, cls: 'sent-neg' },
      { v: d.neutral,  cls: 'sent-neu' },
      { v: d.positive, cls: 'sent-pos' },
    ];
    return segments.map(s => {
      if (s.v === 0) return '';
      const h = innerH * (s.v / maxTotal);
      const top = yCursor - h;
      const rect = `<rect x="${x.toFixed(1)}" y="${top.toFixed(1)}" width="${colW.toFixed(1)}" height="${h.toFixed(1)}" class="${s.cls}"/>`;
      yCursor = top;
      return rect;
    }).join('');
  }).join('');

  const labels = [0, Math.floor(days.length / 2), days.length - 1].map(i => {
    const x = PAD_L + i * cellW + cellW / 2;
    return `<text x="${x.toFixed(1)}" y="${H - 8}" class="news-xlabel" text-anchor="middle">${days[i].date.slice(5)}</text>`;
  }).join('');

  return `
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="news-sent-chart">
      ${bars}
      ${labels}
    </svg>
    <div class="news-sent-legend">
      <span><span class="dot sent-pos"></span> POSITIVE</span>
      <span><span class="dot sent-neu"></span> NEUTRAL</span>
      <span><span class="dot sent-neg"></span> NEGATIVE</span>
    </div>
  `;
}

function renderEventMix(buckets) {
  return `
    <div class="news-evt-list">
      ${buckets.map(b => `
        <div class="news-evt-row">
          <div class="news-evt-label">${escape(b.event_type)}</div>
          <div class="news-evt-bar">
            <div class="news-evt-bar-fill" style="width:${(b.share * 100).toFixed(1)}%"></div>
          </div>
          <div class="news-evt-num">${b.count}</div>
        </div>
      `).join('')}
    </div>
  `;
}

function renderArticleList(rows) {
  if (!rows.length) {
    return `<div class="empty-state text-muted">조건에 맞는 기사가 없습니다.</div>`;
  }
  return `
    <div class="news-article-list">
      ${rows.map(r => `
        <div class="news-article-card">
          <div class="news-article-head">
            <div class="news-article-title">
              ${r.url
                ? `<a href="${escape(r.url)}" target="_blank" rel="noopener">${escape(r.title)}</a>`
                : `<span>${escape(r.title)}</span>`}
            </div>
            ${badgeRow(r)}
          </div>
          <div class="news-article-meta">
            <span class="text-xs text-muted">${escape(r.publisher || r.source)} · ${Utils.timeAgo(r.published_ts)} 전</span>
            ${r.ticker ? `<span class="badge badge-info">${escape(r.market)}:${escape(r.ticker)}</span>` : ''}
            ${r.relevance != null ? `<span class="text-xs text-muted">관련도 ${(r.relevance * 100).toFixed(0)}%</span>` : ''}
          </div>
          ${r.summary ? `<div class="news-article-summary">${escape(r.summary)}</div>` : ''}
        </div>
      `).join('')}
    </div>
  `;
}

function badgeRow(r) {
  if (r.sentiment == null && r.event_type == null && r.impact == null) {
    return `<span class="badge badge-neutral">분류 대기</span>`;
  }
  const parts = [];
  if (r.sentiment) parts.push(`<span class="badge ${sentBadge(r.sentiment)}">${r.sentiment}</span>`);
  if (r.event_type) parts.push(`<span class="badge badge-info">${escape(r.event_type)}</span>`);
  if (r.impact) parts.push(`<span class="badge ${impactBadge(r.impact)}">${r.impact}</span>`);
  if (r.horizon) parts.push(`<span class="badge badge-neutral">${r.horizon}</span>`);
  return `<div class="news-badges">${parts.join('')}</div>`;
}

function sentBadge(s) {
  if (s === 'POSITIVE') return 'badge-profit';
  if (s === 'NEGATIVE') return 'badge-loss';
  return 'badge-neutral';
}

function impactBadge(i) {
  if (i === 'HIGH') return 'badge-loss';
  if (i === 'MEDIUM') return 'badge-info';
  return 'badge-neutral';
}

function renderPager(total, offset, limit) {
  const page = Math.floor(offset / limit) + 1;
  const totalPages = Math.max(1, Math.ceil(total / limit));
  return `
    <div class="news-pager-row">
      <button class="btn btn-secondary btn-sm" data-action="prev" ${offset <= 0 ? 'disabled' : ''}>← 이전</button>
      <span class="text-xs text-muted">${page} / ${totalPages} (총 ${total}건)</span>
      <button class="btn btn-secondary btn-sm" data-action="next" ${offset + limit >= total ? 'disabled' : ''}>다음 →</button>
    </div>
  `;
}

function escape(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

window.renderNews = renderNews;
