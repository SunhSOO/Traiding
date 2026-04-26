/**
 * SUPERRICH - Main Application
 * MT5 System Trading Dashboard
 */
document.addEventListener('DOMContentLoaded', () => {
  // Register routes
  AppRouter
    .register('/dashboard', renderDashboard)
    .register('/trading', renderTrading)
    .register('/portfolio', renderPortfolio)
    .register('/strategy', renderStrategy)
    .register('/history', renderHistory)
    .register('/analytics', renderAnalytics)
    .register('/settings', renderSettings);

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
