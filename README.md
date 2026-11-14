# ai-quant-agent

[![CI Pipeline](https://github.com/Bonvoyez/ai-quant-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/Bonvoyez/ai-quant-agent/actions)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Code Style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Testing: Pytest](https://img.shields.io/badge/pytest-passing-brightgreen.svg)](https://pytest.org)

An asynchronous quantitative trading and execution bot for US equities and crypto. Built with Python (`asyncio`, `aiohttp`), deterministic risk rules, momentum filters, and live broker integration (Toss Securities OpenAPI and Upbit).

---

## Features

- **Breakout Screening & Trailing Stops**: Closed-form mathematical scoring for small-cap breakouts combined with intraday trailing stop-loss management (`breakout_strategy.py`, `exit_manager.py`).
- **Deterministic Risk Rules**: ATR-based dynamic bounds, hard capital caps per ticker, and daily drawdown circuit breakers (`quant_rules.py`).
- **Multi-Broker Connectivity**:
  - **Toss Securities OpenAPI**: US equities market execution with automated OAuth2 token refresh and fractional/integer share fallback.
  - **Upbit REST/WebSocket**: Spot crypto trade support.
- **Async Architecture**: Non-blocking network I/O, background schedule reconciler, and token-authenticated local web dashboard.
- **Telegram Notifications**: Real-time alerts for orders, position updates, and trailing stops.

---

## Strategy Overview

### 1. Small-Cap Breakout & Momentum Strategy
- **Pre-Market Screening**: Scans liquid US small-caps before regular market hours using pre-trained closed-form momentum formulas (`derived_formula.py`).
- **Entry Rules**: Requires minimum relative volume (RVOL), ATR threshold, and absence of toxic corporate actions (dilution/CB announcements).
- **Exit Strategy**:
  - Hard stop-loss at -3.0% from entry price.
  - Dynamic trailing stop triggered once profit reaches +5.0%, trailing -1.5% from the intraday high.

### 2. Sector ETF Rotation Strategy
- Ranks major US sector ETFs based on multi-timeframe momentum and inverse volatility weighting.
- Rebalances on schedule with turnover and max drawdown constraints.

---

## Project Structure

```
ai-quant-agent/
├── .github/workflows/ci.yml   # GitHub Actions CI (Ruff + Pytest)
├── tests/                     # Unit test suite
├── breakout_strategy.py       # Breakout screening & trailing stop runner
├── derived_formula.py         # Pure-Python mathematical breakout formula
├── exit_manager.py            # Optimized trailing stop and stop-loss handler
├── toss_service.py            # Toss Securities OpenAPI client
├── market_data.py             # Price feed ingestion & technical indicators
├── quant_rules.py             # ATR calculations, position sizing, and stops
├── rotation_strategy.py       # Sector ETF momentum rotation
├── scheduler.py               # Main trading loop and schedule manager
├── cli.py                     # CLI entrypoint
├── dashboard_server.py        # Token-secured dashboard backend
├── dashboard.html             # Real-time monitoring UI
├── telegram_service.py        # Telegram alerting module
└── main.py                    # Application bootstrap
```

---

## Quick Start

### 1. Requirements
- Python 3.10+
- Linux (Oracle Cloud / Ubuntu / Debian) or macOS / Windows

### 2. Installation
```bash
git clone https://github.com/Bonvoyez/ai-quant-agent.git
cd ai-quant-agent

python3 -m venv venv
source venv/bin/activate  # On Windows: .\venv\Scripts\activate

pip install -r requirements.txt
```

### 3. Configuration
Copy `.env.example` to `.env` and fill in your credentials:
```bash
cp .env.example .env
```

Key environment variables:
```ini
# Brokerage Credentials (Toss Securities OpenAPI)
TOSS_APP_KEY=your_toss_app_key
TOSS_APP_SECRET=your_toss_app_secret
TOSS_ACCOUNT_NO=your_account_seq_or_number

# Telegram Notifications (Optional)
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Local Dashboard
DASHBOARD_TOKEN=choose_a_random_token
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=8899
```

### 4. Running the Bot

```bash
# Run CLI diagnostics and verify broker credentials
python cli.py --test-connection

# Run in paper trading (dry-run simulation)
python cli.py --mode paper --dry-run

# Run full bot with web dashboard
python main.py
```

---

## Testing & Quality Checks

Run the automated test suite and linter:
```bash
# Unit tests
pytest -v tests/

# Linter
ruff check .
```

---

## Disclaimer

This project is open-source software created for research and educational purposes. Trading financial markets carries risk of capital loss. Verify strategy logic with paper trading before using live capital.
