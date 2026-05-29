/**
 * Global app state.
 *
 * Today this holds:
 *   - JWT access token (persisted to localStorage)
 *   - Cached current user info (refreshed on login + on /me success)
 *   - Currently-selected market tab ('KR' | 'US')
 *
 * State change emits a window event so pages can re-render without
 * needing a framework. We deliberately avoid a reactive lib — Vanilla
 * JS keeps the bundle tiny and the surface inspectable.
 *
 * Events fired:
 *   - state:auth     {user, token}    on login / clearAuth
 *   - state:market   {market}         when KR/US tab switches
 */

const STORAGE_KEYS = {
  token: 'woonam.access_token',
  user: 'woonam.user',
  market: 'woonam.market',
};

const AppState = {
  _emit(event, detail) {
    window.dispatchEvent(new CustomEvent(event, { detail }));
  },

  // ── Auth ──
  getToken() {
    return localStorage.getItem(STORAGE_KEYS.token);
  },

  isAuthenticated() {
    return Boolean(this.getToken());
  },

  getUser() {
    const raw = localStorage.getItem(STORAGE_KEYS.user);
    if (!raw) return null;
    try { return JSON.parse(raw); } catch { return null; }
  },

  setAuth({ token, user }) {
    if (token) localStorage.setItem(STORAGE_KEYS.token, token);
    if (user) localStorage.setItem(STORAGE_KEYS.user, JSON.stringify(user));
    this._emit('state:auth', { user, token });
  },

  clearAuth() {
    localStorage.removeItem(STORAGE_KEYS.token);
    localStorage.removeItem(STORAGE_KEYS.user);
    this._emit('state:auth', { user: null, token: null });
  },

  // ── Market tab (KR / US) ──
  getMarket() {
    return localStorage.getItem(STORAGE_KEYS.market) || 'KR';
  },

  setMarket(market) {
    if (market !== 'KR' && market !== 'US') {
      throw new Error(`Invalid market: ${market}`);
    }
    localStorage.setItem(STORAGE_KEYS.market, market);
    this._emit('state:market', { market });
  },

  toggleMarket() {
    this.setMarket(this.getMarket() === 'KR' ? 'US' : 'KR');
  },
};

window.AppState = AppState;
