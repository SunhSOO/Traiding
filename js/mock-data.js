/**
 * SUPERRICH - Mock Data for Demo
 */
const MockData = {
  account: {
    balance: 52847.63,
    equity: 53412.18,
    margin: 4280.00,
    freeMargin: 49132.18,
    marginLevel: 1247.48,
    dailyPnl: 564.55,
    dailyPnlPct: 1.07,
    weeklyPnl: 2340.80,
    monthlyPnl: 8420.15,
    leverage: 100
  },

  positions: [
    { id: 1, symbol: 'EURUSD', type: 'BUY', volume: 1.00, openPrice: 1.08425, currentPrice: 1.08612, sl: 1.08200, tp: 1.09000, pnl: 187.00, pnlPct: 0.35, openTime: '2026-04-26T09:15:00', swap: -2.30 },
    { id: 2, symbol: 'XAUUSD', type: 'SELL', volume: 0.50, openPrice: 2348.50, currentPrice: 2342.80, sl: 2360.00, tp: 2320.00, pnl: 285.00, pnlPct: 0.54, openTime: '2026-04-26T08:42:00', swap: -1.50 },
    { id: 3, symbol: 'USDJPY', type: 'BUY', volume: 2.00, openPrice: 155.820, currentPrice: 155.650, sl: 155.400, tp: 156.500, pnl: -215.40, pnlPct: -0.40, openTime: '2026-04-26T07:30:00', swap: 0.80 },
    { id: 4, symbol: 'GBPUSD', type: 'BUY', volume: 0.30, openPrice: 1.29150, currentPrice: 1.29380, sl: 1.28900, tp: 1.29800, pnl: 69.00, pnlPct: 0.18, openTime: '2026-04-25T22:10:00', swap: -0.90 },
    { id: 5, symbol: 'NAS100', type: 'SELL', volume: 0.10, openPrice: 18245.0, currentPrice: 18190.5, sl: 18350.0, tp: 18050.0, pnl: 54.50, pnlPct: 0.10, openTime: '2026-04-26T10:05:00', swap: 0.00 }
  ],

  marketData: [
    { symbol: 'EURUSD', name: 'Euro/Dollar', bid: 1.08610, ask: 1.08614, change: 0.15, spread: 0.4 },
    { symbol: 'XAUUSD', name: 'Gold/Dollar', bid: 2342.50, ask: 2342.90, change: -0.24, spread: 0.40 },
    { symbol: 'USDJPY', name: 'Dollar/Yen', bid: 155.648, ask: 155.662, change: -0.11, spread: 1.4 },
    { symbol: 'GBPUSD', name: 'Pound/Dollar', bid: 1.29378, ask: 1.29386, change: 0.18, spread: 0.8 },
    { symbol: 'NAS100', name: 'Nasdaq 100', bid: 18190.2, ask: 18191.0, change: 0.42, spread: 0.8 },
    { symbol: 'US30', name: 'Dow Jones', bid: 40125.5, ask: 40127.0, change: 0.31, spread: 1.5 }
  ],

  strategies: [
    { id: 1, name: 'Trend Follower', type: 'Trend Following', status: 'running', symbol: 'EURUSD', timeframe: 'H1', pnl: 1245.80, pnlPct: 4.2, winRate: 62, trades: 48, maxDD: -3.8, icon: '📈' },
    { id: 2, name: 'Gold Scalper', type: 'Scalping', status: 'running', symbol: 'XAUUSD', timeframe: 'M15', pnl: 892.30, pnlPct: 3.1, winRate: 71, trades: 156, maxDD: -2.1, icon: '⚡' },
    { id: 3, name: 'Mean Reversion', type: 'Mean Reversion', status: 'stopped', symbol: 'USDJPY', timeframe: 'H4', pnl: -180.50, pnlPct: -0.6, winRate: 45, trades: 22, maxDD: -5.2, icon: '🔄' },
    { id: 4, name: 'Breakout Hunter', type: 'Breakout', status: 'running', symbol: 'GBPUSD', timeframe: 'H1', pnl: 567.40, pnlPct: 1.9, winRate: 55, trades: 34, maxDD: -4.1, icon: '🎯' },
    { id: 5, name: 'Grid Bot', type: 'Grid Trading', status: 'error', symbol: 'NAS100', timeframe: 'M5', pnl: -45.20, pnlPct: -0.2, winRate: 68, trades: 210, maxDD: -1.8, icon: '🤖' }
  ],

  recentTrades: [
    { id: 101, symbol: 'XAUUSD', type: 'SELL', volume: 0.50, price: 2348.50, pnl: null, time: '2026-04-26T10:15:00', status: 'open' },
    { id: 102, symbol: 'EURUSD', type: 'BUY', volume: 0.30, price: 1.08520, pnl: 42.60, time: '2026-04-26T09:48:00', status: 'closed' },
    { id: 103, symbol: 'USDJPY', type: 'SELL', volume: 1.00, price: 156.100, pnl: -85.20, time: '2026-04-26T08:30:00', status: 'closed' },
    { id: 104, symbol: 'GBPUSD', type: 'BUY', volume: 0.50, price: 1.29020, pnl: 115.00, time: '2026-04-26T07:15:00', status: 'closed' },
    { id: 105, symbol: 'NAS100', type: 'BUY', volume: 0.20, price: 18120.0, pnl: 250.00, time: '2026-04-25T21:45:00', status: 'closed' }
  ],

  tradeHistory: [],

  logs: [
    { time: '2026-04-26T10:15:32', level: 'info', strategy: 'Trend Follower', message: 'Signal detected: BUY EURUSD at 1.08612' },
    { time: '2026-04-26T10:14:18', level: 'success', strategy: 'Gold Scalper', message: 'Order filled: SELL XAUUSD 0.50 lots at 2348.50' },
    { time: '2026-04-26T10:12:05', level: 'warn', strategy: 'Grid Bot', message: 'Max drawdown threshold approaching: -1.6%' },
    { time: '2026-04-26T10:10:44', level: 'info', strategy: 'Breakout Hunter', message: 'Monitoring GBPUSD breakout level: 1.2950' },
    { time: '2026-04-26T10:08:30', level: 'error', strategy: 'Grid Bot', message: 'Connection timeout - retrying in 5s' },
    { time: '2026-04-26T10:05:15', level: 'success', strategy: 'Trend Follower', message: 'Take profit hit: EURUSD +$42.60' },
    { time: '2026-04-26T10:02:00', level: 'info', strategy: 'Gold Scalper', message: 'RSI oversold detected on M15' },
    { time: '2026-04-26T09:58:42', level: 'warn', strategy: 'Mean Reversion', message: 'Strategy paused: low volatility environment' }
  ],

  /** Generate trade history */
  generateHistory(count = 100) {
    if (this.tradeHistory.length > 0) return this.tradeHistory;
    const symbols = ['EURUSD', 'XAUUSD', 'USDJPY', 'GBPUSD', 'NAS100', 'US30'];
    const types = ['BUY', 'SELL'];
    const strategies = ['Trend Follower', 'Gold Scalper', 'Mean Reversion', 'Breakout Hunter', 'Grid Bot', 'Manual'];
    const now = Date.now();
    
    for (let i = 0; i < count; i++) {
      const isProfit = Math.random() > 0.42;
      const pnl = isProfit ? Utils.random(10, 800) : -Utils.random(10, 500);
      const symbol = symbols[Utils.randomInt(0, symbols.length - 1)];
      const openTime = new Date(now - Utils.randomInt(1, 30) * 86400000 - Utils.randomInt(0, 86400000));
      const closeTime = new Date(openTime.getTime() + Utils.randomInt(300, 86400) * 1000);
      
      this.tradeHistory.push({
        id: 1000 + i,
        symbol,
        type: types[Utils.randomInt(0, 1)],
        volume: [0.01, 0.05, 0.10, 0.20, 0.30, 0.50, 1.00, 2.00][Utils.randomInt(0, 7)],
        openPrice: symbol === 'XAUUSD' ? Utils.random(2300, 2400) : symbol === 'NAS100' ? Utils.random(17800, 18500) : symbol === 'US30' ? Utils.random(39500, 40500) : Utils.random(0.8, 1.6),
        closePrice: symbol === 'XAUUSD' ? Utils.random(2300, 2400) : symbol === 'NAS100' ? Utils.random(17800, 18500) : symbol === 'US30' ? Utils.random(39500, 40500) : Utils.random(0.8, 1.6),
        pnl: parseFloat(pnl.toFixed(2)),
        pnlPct: parseFloat((pnl / 528).toFixed(2)),
        openTime: openTime.toISOString(),
        closeTime: closeTime.toISOString(),
        strategy: strategies[Utils.randomInt(0, strategies.length - 1)],
        commission: -parseFloat(Utils.random(0.5, 5).toFixed(2)),
        swap: parseFloat(Utils.random(-3, 1).toFixed(2))
      });
    }
    
    this.tradeHistory.sort((a, b) => new Date(b.closeTime) - new Date(a.closeTime));
    return this.tradeHistory;
  },

  /** Generate equity curve data */
  generateEquityCurve(days = 90) {
    const data = [];
    let equity = 45000;
    const now = Date.now();
    for (let i = days; i >= 0; i--) {
      equity += Utils.random(-300, 400);
      equity = Math.max(equity, 30000);
      data.push({
        date: new Date(now - i * 86400000),
        value: parseFloat(equity.toFixed(2))
      });
    }
    return data;
  },

  /** Generate daily PnL data */
  generateDailyPnl(days = 30) {
    const data = [];
    const now = Date.now();
    for (let i = days; i >= 0; i--) {
      data.push({
        date: new Date(now - i * 86400000),
        pnl: parseFloat(Utils.random(-500, 700).toFixed(2))
      });
    }
    return data;
  }
};

window.MockData = MockData;
