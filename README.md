# 💎 SUPERRICH — MT5 System Trading Dashboard

Professional web-based dashboard for monitoring and controlling MetaTrader 5 automated trading systems.

## Quick Start

### Frontend Only (Demo Mode)
```bash
cd superrich
python -m http.server 8080
# Open http://localhost:8080
```

### Full Stack (with MT5 connection)
```bash
# Install backend dependencies
cd backend
pip install -r requirements.txt

# Start the server (requires MT5 terminal running)
python main.py
# Open http://localhost:8000
```

## Features

- 📊 **Dashboard** — Real-time account overview, P&L, positions
- 📈 **Live Trading** — Chart, order panel, position management
- 💼 **Portfolio** — Asset allocation, risk assessment
- 🤖 **Strategy** — Automated strategy management, backtesting
- 📋 **History** — Complete trade history with filters
- 📉 **Analytics** — Performance charts, calendar heatmap
- ⚙️ **Settings** — MT5 connection, risk limits, notifications

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | HTML5 + Vanilla CSS + JavaScript |
| Backend | Python + FastAPI |
| MT5 Bridge | MetaTrader5 Python Package |
| Real-time | WebSocket |

## Requirements

- Python 3.10+
- MetaTrader 5 terminal (for live trading)
- Modern web browser
