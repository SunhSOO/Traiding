/**
 * SUPERRICH - SPA Router
 * Hash-based routing for single page application
 */
class Router {
  constructor() {
    this.routes = {};
    this.currentRoute = null;
    this.beforeHooks = [];
    this.afterHooks = [];
    this.publicRoutes = new Set(['/login']);
    window.addEventListener('hashchange', () => this.handleRoute());
    // Token expiry / 401 → log out + reroute to login
    window.addEventListener('auth:expired', () => {
      if (this.getCurrentPath() !== '/login') {
        sessionStorage.setItem('woonam.intended_route', this.getCurrentPath());
        this.navigate('/login');
      }
    });
  }

  register(path, handler) {
    this.routes[path] = handler;
    return this;
  }

  beforeEach(fn) {
    this.beforeHooks.push(fn);
    return this;
  }

  afterEach(fn) {
    this.afterHooks.push(fn);
    return this;
  }

  navigate(path) {
    window.location.hash = path;
  }

  getCurrentPath() {
    // Strip leading '#', drop query string for route matching
    const raw = window.location.hash.slice(1) || '/dashboard';
    return raw.split('?')[0];
  }

  /** Parse `?market=KR&ticker=005930` from the hash. */
  getQuery() {
    const raw = window.location.hash.slice(1);
    const q = raw.split('?')[1];
    if (!q) return {};
    const out = {};
    for (const part of q.split('&')) {
      const [k, v] = part.split('=');
      if (k) out[decodeURIComponent(k)] = v !== undefined ? decodeURIComponent(v) : '';
    }
    return out;
  }

  /** Build a hash URL with query params. Convenience for callers. */
  pathWithQuery(path, params = {}) {
    const filtered = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '');
    if (!filtered.length) return path;
    const qs = filtered.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join('&');
    return `${path}?${qs}`;
  }

  async handleRoute() {
    const path = this.getCurrentPath();

    // Auth guard: anything other than /login requires an auth token.
    // Pages opt out by listing in `publicRoutes`.
    if (!this.publicRoutes.has(path) && window.AppState && !AppState.isAuthenticated()) {
      sessionStorage.setItem('woonam.intended_route', path);
      this.navigate('/login');
      return;
    }

    const handler = this.routes[path];
    if (!handler) {
      this.navigate('/dashboard');
      return;
    }

    // Before hooks
    for (const hook of this.beforeHooks) {
      const proceed = await hook(path, this.currentRoute);
      if (proceed === false) return;
    }

    const previousRoute = this.currentRoute;
    this.currentRoute = path;

    // Update active nav
    document.querySelectorAll('.nav-item').forEach(item => {
      item.classList.toggle('active', item.dataset.route === path);
    });

    // Update page title
    const pageTitle = document.getElementById('page-title');
    if (pageTitle) {
      const titles = {
        '/dashboard': 'Dashboard',
        '/trading': 'Live Trading',
        '/portfolio': 'Portfolio',
        '/strategy': 'Strategy',
        '/history': 'History',
        '/analytics': 'Analytics',
        '/settings': 'Settings',
        '/login': 'Login',
        '/universe': 'Universe',
        '/decisions': 'Decision Audit',
        '/freshness': 'Data Freshness',
        '/analysis': '종목 분석',
        '/training': '학습 결과',
        '/backtest': '백테스트 (Replay)',
        '/news': '뉴스 탐색',
        '/macro': '매크로 지표',
        '/scan': '시그널 스캔',
        '/llm': 'LLM 상태',
        '/config': '시스템 설정',
      };
      pageTitle.textContent = titles[path] || 'woonam';
    }

    // Render page
    const content = document.getElementById('page-content');
    if (content) {
      content.style.opacity = '0';
      content.style.transform = 'translateY(10px)';
      
      await new Promise(r => setTimeout(r, 150));
      
      try {
        await handler(content);
      } catch (err) {
        console.error('Route error:', err);
        content.innerHTML = `<div class="empty-state"><h3>Error loading page</h3><p>${err.message}</p></div>`;
      }
      
      content.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
      content.style.opacity = '1';
      content.style.transform = 'translateY(0)';
    }

    // After hooks
    for (const hook of this.afterHooks) {
      await hook(path, previousRoute);
    }
  }

  start() {
    if (!window.location.hash) {
      window.location.hash = '/dashboard';
    } else {
      this.handleRoute();
    }
  }
}

window.AppRouter = new Router();
