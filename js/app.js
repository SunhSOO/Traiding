/**
 * SUPERRICH - Main Application
 * MT5 System Trading Dashboard
 */
document.addEventListener('DOMContentLoaded', () => {
  // Register routes
  AppRouter
    .register('/login', renderLogin)
    .register('/dashboard', renderDashboard)
    .register('/trading', renderTrading)
    .register('/portfolio', renderPortfolio)
    .register('/strategy', renderStrategy)
    .register('/history', renderHistory)
    .register('/analytics', renderAnalytics)
    .register('/settings', renderSettings)
    .register('/universe', renderUniverse)
    .register('/decisions', renderDecisionAudit)
    .register('/freshness', renderFreshness)
    .register('/analysis', renderAnalysis)
    .register('/training', renderTraining)
    .register('/backtest', renderBacktest)
    .register('/news', renderNews)
    .register('/macro', renderMacro)
    .register('/market', renderMarketView)
    .register('/basket', renderBasket)
    .register('/execution', renderExecution)
    .register('/core-portfolio', renderCorePortfolio)
    .register('/scan', renderScan)
    .register('/llm', renderLLM)
    .register('/config', renderConfig);

  // Reflect logged-in user in the sidebar footer
  function updateAuthChip() {
    const chip = document.getElementById('sidebar-user');
    if (!chip) return;
    const user = AppState.getUser();
    if (user) {
      chip.innerHTML = `
        <div class="status-text">
          <strong>${user.full_name || user.username}</strong><br>
          <span class="text-xs">${user.is_live_enabled ? '🟢 live 권한' : '🔒 paper 전용'}</span>
        </div>
        <button class="btn btn-ghost btn-sm" id="logout-btn" title="로그아웃">✕</button>
      `;
      chip.querySelector('#logout-btn').addEventListener('click', () => {
        AppState.clearAuth();
        AppRouter.navigate('/login');
      });
    } else {
      chip.innerHTML = `
        <div class="status-text">
          <strong>로그인 필요</strong>
        </div>`;
    }
  }
  updateAuthChip();
  window.addEventListener('state:auth', updateAuthChip);

  // Start the toast watcher whenever a user is logged in.
  function syncToastWatcher() {
    if (AppState.getUser()) {
      ToastWatcher.start();
    } else {
      ToastWatcher.stop();
    }
  }
  syncToastWatcher();
  window.addEventListener('state:auth', syncToastWatcher);

  // Clean up intervals on route change
  AppRouter.beforeEach((to, from) => {
    if (window._dashboardInterval) {
      clearInterval(window._dashboardInterval);
      window._dashboardInterval = null;
    }
    return true;
  });

  // Setup sidebar nav clicks
  document.querySelectorAll('.nav-item[data-route]').forEach(item => {
    item.addEventListener('click', (e) => {
      e.preventDefault();
      AppRouter.navigate(item.dataset.route);
    });
  });

  // Update header clock
  function updateClock() {
    const el = document.getElementById('header-time');
    if (el) {
      const now = new Date();
      el.textContent = now.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
    }
  }
  setInterval(updateClock, 1000);
  updateClock();

  // Start router
  AppRouter.start();
});
