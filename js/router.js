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
    window.addEventListener('hashchange', () => this.handleRoute());
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
    return window.location.hash.slice(1) || '/dashboard';
  }

  async handleRoute() {
    const path = this.getCurrentPath();
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
        '/settings': 'Settings'
      };
      pageTitle.textContent = titles[path] || 'Dashboard';
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
