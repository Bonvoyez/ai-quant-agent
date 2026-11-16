# ai-quant-agent

[![CI Pipeline](https://github.com/Bonvoyez/ai-quant-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/Bonvoyez/ai-quant-agent/actions)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Architecture: Clean](https://img.shields.io/badge/architecture-broker--agnostic-informational.svg)](#system-architecture)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Code Style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Testing: Pytest](https://img.shields.io/badge/pytest-passing-brightgreen.svg)](https://pytest.org)

An asynchronous, broker-agnostic quantitative trading and portfolio management platform for US equities. Designed with **Clean Architecture** and **Dependency Inversion** principles, featuring closed-form mathematical momentum formulas, deterministic volatility filters, dynamic intraday trailing stop risk management, US market holiday & DST scheduling, and an offline mock execution engine.

---

## Key Highlights

- **Broker-Agnostic Pluggable Architecture**:
  Core trading strategies interact strictly with the abstract `BaseBroker` interface. Out of the box, it includes:
  - `MockBroker`: Zero-credential virtual execution environment for reproducible backtesting and paper simulation.
  - `TossInvestService`: Production driver adapter for Toss Securities US Equities OpenAPI with OAuth2 token caching and rate-limit guardrails.
  - Easily extensible to institutional/retail gateways (Interactive Brokers, Alpaca, FIX protocols) without altering trading logic.
- **Closed-Form Momentum & Toxic Disclosure Filter**:
  Pre-market nanocap scoring engine evaluating opening gap, relative volume (RVOL), and ATR volatility, coupled with automated rejection of toxic corporate dilution (CB/toxic financing filings).
- **Multi-Tier AI Intelligence Pipeline (Deep Pro Audit)**:
  Configurable 4-tier LLM intelligence system (`off`, `lite`, `standard`, `deep`) powered by OpenAI-compatible reasoning models (e.g., `sonar-reasoning-pro` with real-time web search):
  - `deep` (Deep Pro): Audits top candidates against real-time SEC EDGAR filings (8-K, S-3 dilution/warrant detection), generates 3-bullet Telegram intelligence briefings, monitors intraday breaking news, and conducts post-trade autopsies.
  - `standard` / `lite`: Lightweight fact checking for cost-conscious users.
  - `off`: 100% pure mathematical execution with zero API token cost.
- **Asymmetric Risk Management & State Persistence**:
  Enforces a strict -3.0% hard stop-loss and a dynamic -2.0% trailing stop after profit expansion. Positions are atomically persisted to disk to guarantee crash recovery without position loss.
- **US Calendar & Daylight Saving Time (DST) Awareness**:
  Built-in pure-Python market calendar calculating official NYSE/NASDAQ holidays (including Meeus/Butcher Easter algorithm for Good Friday) and automatic EST/EDT transitions via `zoneinfo.ZoneInfo("America/New_York")`.
- **Decoupled Mobile Web Dashboard**:
  Single Source of Truth (SSOT) architecture using atomic state files (`settings.json`, `breakout_positions.json`) decoupled from the execution loop, accessible via reverse proxy (Caddy / Nginx).

---

## System Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                   AI-QUANT-AGENT CORE TRADING ENGINE                   │
│                                                                        │
│  • derived_formula.py    (Closed-form scoring: Gap 0-4%, RVOL, ATR)    │
│  • exit_manager.py       (Deterministic risk: -3% stop, -2% trailing)  │
│  • breakout_strategy.py  (Daily Top 1 runner with crash recovery)       │
│  • market_calendar.py    (NYSE/NASDAQ 10 official holidays & DST engine)│
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
         ┌─────────────────────────┼─────────────────────────┐
         ▼                         ▼                         ▼
   [Broker Layer]            [AI Reasoning Layer]      [Telemetry & UI]
   (BaseBroker Interface)    (OpenAI-Compatible Spec)  (Decoupled REST)
         │                         │                         │
 ┌───────┴───────┐         ┌───────┴───────┐         ┌───────┴───────┐
 │               │         │               │         │               │
 ▼               ▼         ▼               ▼         ▼               ▼
[MockBroker]  [Toss Broker [OpenAI /      [FactChat   [Telegram]  [Mobile Web]
(Zero-cred     Adapter]     Perplexity /   / Custom    (Alerting)  (Caddy HTTPS
 sandbox)      (Live API)   Ollama]        Gateway]                Reverse Proxy)
```

---

## Strategy Overview

### 1. Nanocap Breakout & Momentum Strategy (`breakout_strategy.py`)
- **Pre-Market Screening**: Identifies liquid US equities exhibiting clean volatility expansion (Gap: 0.0% to +4.0%, RVOL > 1.2, ATR >= 7.0%).
- **Toxic Material Sieve**: Immediate disqualification upon detection of dilutive corporate events or convertible debt.
- **Risk & Trade Management**:
  - Capital allocation: Concentrated single-ticker daily position (`Daily Top 1`).
  - Hard Stop-Loss: Immediate exit upon -3.0% drawdown from entry price.
  - Dynamic Trailing Stop: Activates on profit surges, trailing -2.0% below the peak high price.
  - State Persistence: Crash-resilient state serialization preventing orphaned positions upon process restarts.

### 2. Sector ETF Momentum Rotation (`rotation_strategy.py`)
- Multi-timeframe relative momentum ranking across 10 major US sector ETFs.
- Inverse-volatility risk budgeting with scheduled rebalancing and turnover caps.

---

## Project Structure

```
ai-quant-agent/
├── .github/workflows/ci.yml   # Automated CI Pipeline (Ruff + Pytest)
├── tests/                     # Comprehensive unit test suite
│   ├── test_breakout_strategy.py
│   ├── test_derived_formula.py
│   ├── test_exit_manager.py
│   ├── test_market_calendar.py
│   └── test_quant_rules.py
├── broker.py                  # Abstract BaseBroker interface & MockBroker implementation
├── toss_service.py            # Pluggable live Toss OpenAPI driver adapter
├── breakout_strategy.py       # Nanocap breakout strategy & position manager
├── derived_formula.py         # Pure-Python mathematical breakout formula
├── exit_manager.py            # Hard stop-loss and trailing stop state machine
├── market_calendar.py         # US market holiday & DST calculator (NYSE/NASDAQ)
├── market_data.py             # Market quote ingestion and technical indicators
├── quant_rules.py             # Volatility models and risk budgeting
├── rotation_strategy.py       # Sector ETF momentum rotation strategy
├── scheduler.py               # Main trading loop and execution orchestrator
├── doctor.py                  # End-to-end 6-subsystem health diagnostic tool
├── cli.py                     # CLI entrypoint
├── dashboard_server.py        # Token-authenticated REST backend
├── dashboard.html             # Responsive mobile/desktop monitoring UI
├── telegram_service.py        # Telegram alerting and notification service
├── ai_client.py               # OpenAI-compatible multi-LLM reasoning client
└── main.py                    # Application bootstrap and daemon runner
```

---

## Quick Start

### 1. Requirements
- Python 3.10+
- Linux (Ubuntu, Debian, Oracle Linux), macOS, or Windows

### 2. Installation
```bash
git clone https://github.com/Bonvoyez/ai-quant-agent.git
cd ai-quant-agent

python3 -m venv venv
source venv/bin/activate  # On Windows: .\venv\Scripts\activate

pip install -r requirements.txt
```

### 3. Configuration
Copy the template and configure your environment:
```bash
cp .env.example .env
```
*(Running with empty broker credentials will automatically trigger `MockBroker` paper trading mode, allowing instant offline evaluation without any external accounts).*

### 4. Running the Platform

```bash
# 1. End-to-End System Doctor (Live health check across all 6 subsystems in < 5s)
python cli.py --doctor

# 2. Paper Trading Simulation (Zero credentials required, 100% offline demonstration)
python cli.py --mode paper --dry-run

# 3. Live Breakout Strategy Execution
python cli.py --mode breakout --live-orders

# 4. Run Full Autonomous Trading Daemon with Web Dashboard
python main.py
```

---

## Testing & Quality Assurance

All core modules are verified through automated unit tests and strict static analysis:

```bash
# Run full unit test suite
pytest -v tests/

# Run Ruff linter
ruff check .
```

---

## License

This project is licensed under the [MIT License](LICENSE).
