/**
 * Toast notification system.
 *
 * Two layers:
 *   - Toast.show({...}) — explicit one-off toast for any caller
 *   - ToastWatcher — polls /api/health/summary and /api/decision/recent
 *     once a minute, surfaces NEW critical events as toasts.
 *
 * "New" means "didn't appear in the previous poll" — we hash event
 * identities into a Set kept in memory. Survives navigation (no
 * persistence) — operator only sees what happened during the session.
 *
 * Toast types:
 *   - error  (red, sticky until dismissed)
 *   - warn   (yellow, 15s)
 *   - info   (blue, 8s)
 *   - success (green, 5s)
 */

const Toast = {
  _containerId: 'toast-container',

  _ensureContainer() {
    let el = document.getElementById(this._containerId);
    if (!el) {
      el = document.createElement('div');
      el.id = this._containerId;
      el.className = 'toast-container';
      document.body.appendChild(el);
    }
    return el;
  },

  show({ title, body, type = 'info', sticky = false, durationMs = null, key = null }) {
    const container = this._ensureContainer();
    // Dedup: if the same key is already active, skip
    if (key && container.querySelector(`[data-toast-key="${CSS.escape(key)}"]`)) return;

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    if (key) toast.dataset.toastKey = key;
    toast.innerHTML = `
      <div class="toast-icon">${iconFor(type)}</div>
      <div class="toast-body">
        <div class="toast-title">${escape(title || '')}</div>
        ${body ? `<div class="toast-text">${escape(body)}</div>` : ''}
      </div>
      <button class="toast-close" title="닫기">✕</button>
    `;
    container.appendChild(toast);

    const close = () => {
      toast.classList.add('toast-leaving');
      setTimeout(() => toast.remove(), 250);
    };
    toast.querySelector('.toast-close').addEventListener('click', close);

    if (!sticky) {
      const d = durationMs || defaultDuration(type);
      setTimeout(close, d);
    }
  },

  success(title, body, opts = {}) { this.show({ ...opts, title, body, type: 'success' }); },
  info(title, body, opts = {})    { this.show({ ...opts, title, body, type: 'info' }); },
  warn(title, body, opts = {})    { this.show({ ...opts, title, body, type: 'warn' }); },
  error(title, body, opts = {})   { this.show({ ...opts, title, body, type: 'error', sticky: true }); },
};

function defaultDuration(type) {
  if (type === 'error') return 30_000;
  if (type === 'warn') return 15_000;
  if (type === 'success') return 5_000;
  return 8_000;
}

function iconFor(type) {
  if (type === 'success') return '✓';
  if (type === 'warn') return '⚠';
  if (type === 'error') return '✗';
  return 'ⓘ';
}

function escape(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ───── ToastWatcher ─────────────────────────────────────────────

const ToastWatcher = {
  _intervalId: null,
  _lastDecisionIds: new Set(),
  _lastTierByArea: {},
  _started: false,

  start({ pollIntervalMs = 60_000 } = {}) {
    if (this._started) return;
    this._started = true;
    // First poll seeds state without firing toasts ("baseline").
    this._poll(true);
    this._intervalId = setInterval(() => this._poll(false), pollIntervalMs);
  },

  stop() {
    if (this._intervalId) clearInterval(this._intervalId);
    this._intervalId = null;
    this._started = false;
  },

  async _poll(isBaseline) {
    if (!window.Api || !window.AppState || !AppState.getUser()) return;
    try {
      const [health, decisions] = await Promise.all([
        Api.healthSummary().catch(() => null),
        Api.recentDecisions({ limit: 20 }).catch(() => []),
      ]);

      // Health tier transitions
      if (health) {
        for (const a of (health.areas || [])) {
          const prev = this._lastTierByArea[a.name];
          this._lastTierByArea[a.name] = a.tier;
          if (isBaseline) continue;
          if (prev && prev !== a.tier) {
            const key = `health-${a.name}-${a.tier}`;
            if (a.tier === 'ERROR') {
              Toast.error(`${a.name}: ${a.headline}`, a.detail || '', { key });
            } else if (a.tier === 'WARN' && prev === 'OK') {
              Toast.warn(`${a.name} 경고`, a.headline, { key });
            } else if (a.tier === 'OK' && prev !== 'OK') {
              Toast.success(`${a.name} 정상 복귀`, a.headline, { key });
            }
          }
        }
      }

      // New BUY / SELL decisions
      const cur = new Set();
      for (const d of decisions) {
        cur.add(d.id);
        if (isBaseline) continue;
        if (this._lastDecisionIds.has(d.id)) continue;
        if (d.action === 'BUY' || d.action === 'SELL') {
          const score = d.composite_score == null ? '?' : d.composite_score.toFixed(1);
          Toast.info(
            `${d.action} ${d.market}:${d.ticker}`,
            `composite ${score}`,
            { key: `dec-${d.id}` },
          );
        }
      }
      this._lastDecisionIds = cur;
    } catch (e) {
      // Watcher must never throw — silent fail is fine
      console.warn('toast-watcher poll failed', e);
    }
  },
};

window.Toast = Toast;
window.ToastWatcher = ToastWatcher;
