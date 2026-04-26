/**
 * SUPERRICH - Utility Functions
 */
const Utils = {
  /** Format currency with locale */
  formatMoney(value, currency = 'USD', decimals = 2) {
    const sign = value >= 0 ? '' : '-';
    const abs = Math.abs(value);
    return sign + '$' + abs.toFixed(decimals).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  },

  /** Format percentage */
  formatPercent(value, decimals = 2) {
    const sign = value > 0 ? '+' : '';
    return sign + value.toFixed(decimals) + '%';
  },

  /** Format number with commas */
  formatNumber(value, decimals = 0) {
    return value.toFixed(decimals).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  },

  /** Format date/time */
  formatTime(date) {
    if (typeof date === 'string') date = new Date(date);
    return date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  },

  formatDate(date) {
    if (typeof date === 'string') date = new Date(date);
    return date.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
  },

  formatDateTime(date) {
    return this.formatDate(date) + ' ' + this.formatTime(date);
  },

  /** Relative time (e.g., "2m ago") */
  timeAgo(date) {
    if (typeof date === 'string') date = new Date(date);
    const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
    if (seconds < 60) return seconds + 's ago';
    if (seconds < 3600) return Math.floor(seconds / 60) + 'm ago';
    if (seconds < 86400) return Math.floor(seconds / 3600) + 'h ago';
    return Math.floor(seconds / 86400) + 'd ago';
  },

  /** Create element with attributes */
  createElement(tag, attrs = {}, children = []) {
    const el = document.createElement(tag);
    for (const [key, val] of Object.entries(attrs)) {
      if (key === 'className') el.className = val;
      else if (key === 'innerHTML') el.innerHTML = val;
      else if (key === 'textContent') el.textContent = val;
      else if (key.startsWith('on')) el.addEventListener(key.slice(2).toLowerCase(), val);
      else if (key === 'style' && typeof val === 'object') Object.assign(el.style, val);
      else if (key === 'dataset') Object.assign(el.dataset, val);
      else el.setAttribute(key, val);
    }
    children.forEach(child => {
      if (typeof child === 'string') el.appendChild(document.createTextNode(child));
      else if (child) el.appendChild(child);
    });
    return el;
  },

  /** Debounce */
  debounce(fn, delay = 300) {
    let timer;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), delay);
    };
  },

  /** Generate random number in range */
  random(min, max) {
    return Math.random() * (max - min) + min;
  },

  randomInt(min, max) {
    return Math.floor(this.random(min, max + 1));
  },

  /** Generate a random ID */
  uid() {
    return Math.random().toString(36).slice(2, 10);
  },

  /** SVG Icon helper (Lucide-style) */
  icon(name) {
    const icons = {
      'layout-dashboard': '<path d="M3 3h7v7H3zM14 3h7v4H14zM14 10h7v11H14zM3 13h7v8H3z"/>',
      'chart-candlestick': '<path d="M9 5v4M9 13v4M15 5v4M15 13v4"/><rect x="7" y="9" width="4" height="4" rx="1"/><rect x="13" y="9" width="4" height="4" rx="1"/>',
      'briefcase': '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2"/>',
      'bot': '<path d="M12 8V4H8"/><rect x="4" y="8" width="16" height="12" rx="2"/><path d="M2 14h2M20 14h2M15 13v2M9 13v2"/>',
      'history': '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>',
      'bar-chart-3': '<path d="M12 20V10M18 20V4M6 20v-4"/>',
      'settings': '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
      'bell': '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
      'search': '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
      'refresh-cw': '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
      'trending-up': '<polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/>',
      'trending-down': '<polyline points="22 17 13.5 8.5 8.5 13.5 2 7"/><polyline points="16 17 22 17 22 11"/>',
      'arrow-up': '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
      'arrow-down': '<path d="M12 5v14"/><path d="m19 12-7 7-7-7"/>',
      'x': '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
      'check': '<polyline points="20 6 9 17 4 12"/>',
      'play': '<polygon points="5 3 19 12 5 21 5 3"/>',
      'pause': '<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>',
      'stop-circle': '<circle cx="12" cy="12" r="10"/><rect x="9" y="9" width="6" height="6"/>',
      'download': '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
      'filter': '<polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>',
      'wifi': '<path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><circle cx="12" cy="20" r="1"/>',
      'dollar-sign': '<line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>',
      'shield': '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
      'key': '<path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/>',
      'moon': '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
      'sun': '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>',
      'activity': '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
      'zap': '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
      'target': '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
      'layers': '<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>',
      'copy': '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
      'eye': '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
      'eye-off': '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>'
    };

    const svgContent = icons[name] || '';
    return `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${svgContent}</svg>`;
  }
};

window.Utils = Utils;
