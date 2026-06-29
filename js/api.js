/**
 * REST client for woonam-auto-trading backend.
 *
 * - Reads JWT from AppState (localStorage-backed) and attaches it to
 *   every request as `Authorization: Bearer <token>`.
 * - Throws `ApiError` (a subclass of Error) on non-2xx so callers can
 *   distinguish auth/server/network failures.
 * - On 401, clears the stored token and emits an `auth:expired` event
 *   that the router uses to redirect to /login.
 *
 * This file has no DOM dependencies — it's pure fetch + Promise so
 * page modules and tests can both use it.
 */

class ApiError extends Error {
  constructor(message, { status, code, body } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.body = body;
  }

  get isAuthFailure() {
    return this.status === 401 || this.status === 403;
  }
}

const Api = {
  BASE: '',  // same-origin; override if backend on different host

  _emit(event, detail) {
    window.dispatchEvent(new CustomEvent(event, { detail }));
  },

  async _request(method, path, { body, query, raw } = {}) {
    const url = new URL(this.BASE + path, window.location.origin);
    if (query) {
      Object.entries(query).forEach(([k, v]) => {
        if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v);
      });
    }

    const headers = { 'Accept': 'application/json' };
    const token = window.AppState && AppState.getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;

    const init = { method, headers };
    if (body !== undefined) {
      if (body instanceof FormData || body instanceof URLSearchParams) {
        init.body = body;
      } else {
        init.body = JSON.stringify(body);
        headers['Content-Type'] = 'application/json';
      }
    }

    let response;
    try {
      response = await fetch(url.toString(), init);
    } catch (err) {
      throw new ApiError(`Network error: ${err.message}`, { status: 0 });
    }

    let payload = null;
    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      try { payload = await response.json(); } catch { /* swallow */ }
    } else if (raw) {
      payload = await response.text();
    }

    if (!response.ok) {
      if (response.status === 401) {
        if (window.AppState) AppState.clearAuth();
        this._emit('auth:expired', { path });
      }
      const msg = (payload && payload.detail) || response.statusText || 'Request failed';
      throw new ApiError(msg, {
        status: response.status,
        code: payload && payload.code,
        body: payload,
      });
    }
    return payload;
  },

  get(path, opts) { return this._request('GET', path, opts); },
  post(path, body, opts = {}) { return this._request('POST', path, { ...opts, body }); },
  patch(path, body, opts = {}) { return this._request('PATCH', path, { ...opts, body }); },
  del(path, opts) { return this._request('DELETE', path, opts); },

  // ── Auth ──
  async login(username, password) {
    // OAuth2 password-flow expects form-encoded body
    const body = new URLSearchParams({ username, password });
    return this._request('POST', '/api/auth/login', { body });
  },
  me() { return this.get('/api/auth/me'); },

  // ── Universe ──
  listSecurities({
    market, isActive = true,
    search = null, sector = null, indexCode = null,
    exchange = null, clusterId = null,
    limit = 200, offset = 0, withCluster = true,
  } = {}) {
    return this.get('/api/universe/securities', {
      query: {
        market, is_active: isActive,
        search, sector, index_code: indexCode,
        exchange, cluster_id: clusterId,
        limit, offset, with_cluster: withCluster,
      },
    });
  },
  universeFacets({ market = null } = {}) {
    return this.get('/api/universe/facets', { query: { market } });
  },
  listIndexMembership(indexCode, asOf) {
    return this.get(`/api/universe/membership/${indexCode}`, {
      query: { as_of: asOf },
    });
  },

  // ── Decisions ──
  recentDecisions({ market, ticker, action, limit = 100 } = {}) {
    return this.get('/api/decision/recent', { query: { market, ticker, action, limit } });
  },
  decisionDetail(id) { return this.get(`/api/decision/${encodeURIComponent(id)}`); },

  // ── Analysis (per-symbol drill-down) ──
  analysisFor(market, ticker, { historyDays = 30, newsLimit = 20, decisionsLimit = 20 } = {}) {
    return this.get(
      `/api/analysis/${encodeURIComponent(market)}/${encodeURIComponent(ticker)}`,
      { query: { history_days: historyDays, news_limit: newsLimit, decisions_limit: decisionsLimit } }
    );
  },
  analysisScoreHistory(market, ticker, { days = 365, maxPoints = 400 } = {}) {
    return this.get(
      `/api/analysis/${encodeURIComponent(market)}/${encodeURIComponent(ticker)}/score-history`,
      { query: { days, max_points: maxPoints } }
    );
  },

  // ── Ingestion / freshness ──
  ingestionFreshness() { return this.get('/api/ingestion/freshness'); },
  ingestionJobs() { return this.get('/api/ingestion/jobs'); },
  runJobNow(jobId) { return this.post(`/api/ingestion/jobs/${encodeURIComponent(jobId)}/run`); },

  // ── Paper trading (real data) ──
  // Backend resolves the per-market account (default-kr / default-us)
  // from the `market` query when accountName is omitted. Pages that
  // already know which market they're on just pass `market`; pages
  // that need a specific operator-renamed account pass `accountName`.
  paperAccount({ name = null, market = null } = {}) {
    return this.get('/api/paper/account', { query: { name, market } });
  },
  paperPositions({ market, accountName = null } = {}) {
    return this.get('/api/paper/positions', { query: { market, account_name: accountName } });
  },
  paperTrades({ market, ticker, limit = 100, accountName = null } = {}) {
    return this.get('/api/paper/trades', { query: { market, ticker, limit, account_name: accountName } });
  },
  topMovers({ market, limit = 10, direction = 'both' }) {
    return this.get('/api/paper/movers', { query: { market, limit, direction } });
  },
  paperEquityCurve({ days = 90, accountName = null, market = null } = {}) {
    return this.get('/api/paper/equity-curve', {
      query: { days, account_name: accountName, market },
    });
  },

  // ── Integrated strategy (selection→execution) ──
  marketRead() { return this.get('/api/integrated/market-read'); },
  basket({ market = 'KR' } = {}) { return this.get('/api/integrated/basket', { query: { market } }); },
  execution({ market = 'KR' } = {}) { return this.get('/api/integrated/execution', { query: { market } }); },

  // ── System health summary ──
  healthSummary() { return this.get('/api/health/summary'); },

  // ── System config inspector ──
  systemConfig() { return this.get('/api/config/system'); },

  // ── LLM health ──
  llmHealth({ windowDays = 7 } = {}) {
    return this.get('/api/llm/health', { query: { window_days: windowDays } });
  },

  // ── Data quality ──
  dqFreshness({ market = null, tier = null, limit = 1000 } = {}) {
    return this.get('/api/data-quality/freshness-by-ticker', {
      query: { market, tier_filter: tier, limit },
    });
  },
  dqGaps({ market = null, days = 30, minGapDays = 2, limitTickers = 500 } = {}) {
    return this.get('/api/data-quality/gaps', {
      query: { market, days, min_gap_days: minGapDays, limit_tickers: limitTickers },
    });
  },

  // ── Signal scan ──
  scanPreview({ market = null, buyThreshold = 25, sellThreshold = -25,
                minOverallConfidence = 0.40, useLearnedWeights = true,
                actionFilter = null, limit = 500 } = {}) {
    return this.get('/api/scan/preview', {
      query: {
        market, buy_threshold: buyThreshold, sell_threshold: sellThreshold,
        min_overall_confidence: minOverallConfidence,
        use_learned_weights: useLearnedWeights,
        action_filter: actionFilter, limit,
      },
    });
  },

  // ── Market regime ──
  regimeCurrent() { return this.get('/api/regime/current'); },
  regimeHistory({ market, days = 180 }) {
    return this.get('/api/regime/history', { query: { market, days } });
  },
  regimeRun() { return this.post('/api/regime/run'); },

  // ── Macro indicators ──
  macroSnapshot() { return this.get('/api/macro/snapshot'); },
  macroSeriesList() { return this.get('/api/macro/series'); },
  macroSeriesDetail(code, { days = 365 } = {}) {
    return this.get(`/api/macro/series/${encodeURIComponent(code)}`, { query: { days } });
  },

  // ── Performance attribution ──
  attributionSummary({ days = 180, market = null, accountName = 'default' } = {}) {
    return this.get('/api/attribution/summary', {
      query: { days, market, account_name: accountName },
    });
  },

  // ── News explorer ──
  newsArticles({ market = null, ticker = null, source = null,
                 sentiment = null, eventType = null, impact = null,
                 days = 7, limit = 50, offset = 0 } = {}) {
    return this.get('/api/news/articles', {
      query: {
        market, ticker, source, sentiment,
        event_type: eventType, impact,
        days, limit, offset,
      },
    });
  },
  newsSentimentTimeline({ market = null, ticker = null, days = 30 } = {}) {
    return this.get('/api/news/sentiment-timeline', {
      query: { market, ticker, days },
    });
  },
  newsEventMix({ market = null, days = 7 } = {}) {
    return this.get('/api/news/event-mix', { query: { market, days } });
  },

  // ── Risk dashboard ──
  riskSummary({ windowHours = 168, market = null } = {}) {
    return this.get('/api/risk/summary', { query: { window_hours: windowHours, market } });
  },
  riskRecentFailures({ limit = 50, market = null } = {}) {
    return this.get('/api/risk/recent-failures', { query: { limit, market } });
  },

  // ── Historical news backfill ──
  backfillNewsStart({ jobId, start, end, market = null, maxChunks = 20, sources = ['bigkinds', 'gdelt'] }) {
    return this.post('/api/backfill/news/start', {
      job_id: jobId, start, end, market, sources, max_chunks: maxChunks,
    });
  },
  backfillJobStatus(jobId) {
    return this.get(`/api/backfill/${encodeURIComponent(jobId)}/status`);
  },
  backfillJobErrors(jobId, limit = 200) {
    return this.get(
      `/api/backfill/${encodeURIComponent(jobId)}/errors`,
      { query: { limit } },
    );
  },
  backfillMacroRun({ start, end, seriesCodes = null } = {}) {
    return this.post('/api/backfill/macro/run', {
      start, end, series_codes: seriesCodes,
    });
  },

  // ── Persisted backtest runs ──
  backtestListRuns({ mode = null, market = null, limit = 100 } = {}) {
    return this.get('/api/backtest/runs', { query: { mode, market, limit } });
  },
  backtestGetRun(runId) {
    return this.get(`/api/backtest/runs/${encodeURIComponent(runId)}`);
  },
  backtestDeleteRun(runId) {
    return this._request('DELETE', `/api/backtest/runs/${encodeURIComponent(runId)}`);
  },

  // ── Backtest replay ──
  backtestRun({ start, end, market = null, tickers = null,
                initialBalance = 100_000, positionFraction = 0.05,
                persist = false, label = null, notes = null }) {
    return this.post('/api/backtest/run', {
      start, end, market, tickers,
      initial_balance: initialBalance,
      position_fraction: positionFraction,
      persist, label, notes,
    });
  },
  backtestRescoring({
    start, end, market = null, tickers = null,
    initialBalance = 100_000, positionFraction = 0.05,
    buyThreshold = 25, sellThreshold = -25,
    minOverallConfidence = 0.40,
    decisionCooldownDays = 1, scoreStalenessHours = 48,
    useLearnedWeights = true, weightOverride = null,
    restrictToIndices = null,
    persist = false, label = null, notes = null,
  }) {
    return this.post('/api/backtest/rescoring', {
      start, end, market, tickers,
      initial_balance: initialBalance,
      position_fraction: positionFraction,
      buy_threshold: buyThreshold,
      sell_threshold: sellThreshold,
      min_overall_confidence: minOverallConfidence,
      decision_cooldown_days: decisionCooldownDays,
      score_staleness_hours: scoreStalenessHours,
      use_learned_weights: useLearnedWeights,
      weight_override: weightOverride,
      restrict_to_indices: restrictToIndices,
      persist, label, notes,
    });
  },

  // ── Cluster weight overrides ──
  overridesList() { return this.get('/api/overrides/'); },
  overridesSet(clusterId, { wFundamental, wTechnical, wInformation, reason = null }) {
    return this._request('PUT', `/api/overrides/${encodeURIComponent(clusterId)}`, {
      body: {
        w_fundamental: wFundamental, w_technical: wTechnical,
        w_information: wInformation, reason,
      },
    });
  },
  overridesDelete(clusterId) {
    return this._request('DELETE', `/api/overrides/${encodeURIComponent(clusterId)}`);
  },

  // ── Training (Phase 3 cluster weights) ──
  trainingSummary() { return this.get('/api/training/summary'); },
  trainingClusters() { return this.get('/api/training/clusters'); },
  trainingClusterHistory(clusterId, limit = 50) {
    return this.get(
      `/api/training/clusters/${encodeURIComponent(clusterId)}/history`,
      { query: { limit } },
    );
  },

  // ── Account / strategy (legacy MT5 endpoints, kept for the existing pages) ──
  health() { return this.get('/api/health'); },
  accountInfo() { return this.get('/api/account/info'); },
  accountPositions() { return this.get('/api/account/positions'); },
  redGreenStatus() { return this.get('/api/strategy/red-green/status'); },
};

window.ApiError = ApiError;
window.Api = Api;
