/**
 * KR / US market tab — re-usable, declarative.
 *
 * Usage in a page:
 *
 *     const tab = MarketTab.mount({
 *       parent: container.querySelector('.page-header'),
 *       onChange: (market) => renderForMarket(market),
 *     });
 *
 * `onChange` is called immediately with the current market so the
 * page can do its initial render without duplicating logic.
 *
 * The component listens to `state:market` so toggling on ANY page
 * toggles globally — a "KR everywhere or US everywhere" UX rule the
 * project requires (no mixed-market views).
 */

const MarketTab = {
  /**
   * Create the tab DOM, wire events, return a small handle with
   * `current()` and `destroy()` methods.
   */
  mount({ parent, onChange }) {
    if (!parent) throw new Error('MarketTab.mount: parent is required');

    const root = document.createElement('div');
    root.className = 'market-tab';
    root.innerHTML = `
      <button class="market-tab-btn" data-market="KR" type="button">
        <span class="market-tab-flag">🇰🇷</span>
        <span class="market-tab-label">한국 (KOSPI200 + KOSDAQ150)</span>
      </button>
      <button class="market-tab-btn" data-market="US" type="button">
        <span class="market-tab-flag">🇺🇸</span>
        <span class="market-tab-label">US (S&P 500 + NASDAQ-100)</span>
      </button>
    `;
    parent.appendChild(root);

    const buttons = root.querySelectorAll('.market-tab-btn');

    function setActive(market) {
      buttons.forEach(btn => {
        btn.classList.toggle('active', btn.dataset.market === market);
      });
    }

    setActive(AppState.getMarket());

    function handleClick(e) {
      const btn = e.currentTarget;
      if (btn.dataset.market === AppState.getMarket()) return;
      AppState.setMarket(btn.dataset.market);
    }

    buttons.forEach(btn => btn.addEventListener('click', handleClick));

    // Listen to global market change (could come from another page that
    // also has a tab mounted, although usually only one page is visible)
    function onMarketEvent(e) {
      const market = e.detail.market;
      setActive(market);
      if (typeof onChange === 'function') onChange(market);
    }
    window.addEventListener('state:market', onMarketEvent);

    // Fire initial onChange so the caller's first render runs
    if (typeof onChange === 'function') onChange(AppState.getMarket());

    return {
      current: () => AppState.getMarket(),
      destroy() {
        buttons.forEach(btn => btn.removeEventListener('click', handleClick));
        window.removeEventListener('state:market', onMarketEvent);
        root.remove();
      },
    };
  },
};

window.MarketTab = MarketTab;
