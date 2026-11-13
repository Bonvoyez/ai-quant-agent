# 🤖 AI Quant Agent

[![CI Pipeline](https://github.com/heo-juyoung0224/ai-quant-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/heo-juyoung0224/ai-quant-agent/actions)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Code Style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Testing: Pytest](https://img.shields.io/badge/pytest-passing-brightgreen.svg)](https://pytest.org)

An autonomous, multi-exchange quantitative trading and portfolio management agent for US equities, crypto, and futures. Engineered with asynchronous Python (`asyncio`, `aiohttp`), stateless multi-agent LLM reasoning, deterministic quant filters, and real-time execution brokers (Toss Securities OpenAPI, Upbit, and CME futures).

---

## 📑 Table of Contents
1. [Problem Statement & Architecture Design](#1-problem-statement--architecture-design)
2. [Multi-Stage Filtering & Execution Pipeline](#2-multi-stage-filtering--execution-pipeline)
3. [Project Structure](#3-project-structure)
4. [Quick Start & CLI Usage](#4-quick-start--cli-usage)
5. [Reliability & Risk Safeguards](#5-reliability--risk-safeguards)
6. [Testing & CI](#6-testing--ci)
7. [Disclaimer](#7-disclaimer)

---

## 1. Problem Statement & Architecture Design

### The Pitfalls of Naive LLM Trading (Context Pollution & History Bias)
Many LLM-assisted trading systems feed raw conversation transcripts, past trading logs, or unconstrained chat histories directly into the model's prompt. In practice, this causes severe architectural defects:
* **Context Pollution & Hallucination**: As chat histories grow, LLMs develop strong recency and confirmation biases, repeatedly rationalizing losing positions or hallucinating non-existent tickers.
* **Non-Deterministic Execution**: Prompts that ask an LLM to "decide target prices, order sizing, and execute" suffer from non-repeatable risk boundaries and arbitrary stop-losses.

### The Stateless Independent Evaluation Architecture
To eliminate context pollution and guarantee deterministic risk control, **AI Quant Agent** enforces strict separation of concerns:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       1. Deterministic Market Feed                          │
│   yfinance / Real-Time Orderbooks / VIX / Macro Data / News Feeds           │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                 2. Quantitative Screening (Deterministic)                   │
│   EMA 9/21/50 Trend + Volume Surges + Dynamic ATR Volatility Bounds         │
│   (Earnings Blacklist & Dashboard Exclusion Tickers Pruned Upfront)         │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ Top Qualified Candidates
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                 3. Stateless LLM Reasoner (Independent Step)                │
│   OpenAI-Compatible Spec (e.g., Perplexity / GPT-4o / DeepSeek / Ollama)    │
│   * Single-shot input: Macro regime + News + Real candidate metrics         │
│   * Strict JSON output schema: [Regime Hypothesis, Evidence, Invalidation]  │
│   * Zero persistent conversation memory -> Immune to Context Pollution     │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ Structured Hypothesis
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│               4. Risk & Order Execution Engine (Deterministic)              │
│   - Inverse Volatility Capital Sizing (LLM confidence does NOT size orders) │
│   - Trailing ATR Stops & Max Daily Drawdown Circuit Breakers                │
│   - Broker Integrations: Toss OpenAPI (Fractional / Integer Fallback),      │
│     Upbit (Crypto), CME Futures (LS Securities MNQZ26)                      │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│               5. Asynchronous Web Dashboard & Telegram Alerts                │
│   Real-time HMAC Token-Authenticated UI & Instant Execution Telemetry       │
└─────────────────────────────────────────────────────────────────────────────┘
```

* **Stateless Prompting**: Each evaluation cycle constructs an isolated, single-turn prompt containing ground-truth market metrics. No conversational chat state persists across cycles.
* **Deterministic Risk Sovereignty**: The LLM acts solely as a skeptical research analyst. All order sizing, stop-loss calculations, and execution decisions are strictly governed by Python quantitative algorithms.

---

## 2. Multi-Stage Filtering & Execution Pipeline

1. **Stage 1: Quantitative Trend & Liquidity Filter**
   * **Moving Average Alignment**: Evaluates short-to-medium trend alignment (EMA 9, EMA 21, SMA 50).
   * **Liquidity & Volatility Sieve**: Filters out illiquid small-caps and screens for relative volume surges ($RVOL > 1.2$).
   * **Earnings Blacklist**: Tickers with earnings announcements scheduled within 2 trading days are automatically pruned to prevent binary earnings gap risk.

2. **Stage 2: Independent LLM Hypothesis Validation**
   * Produces structured output containing `market_regime_hypothesis` (`RISK_ON`, `RISK_OFF`, `MIXED`), numerical evidence, counter-evidence, and an observable invalidation condition.
   * If the model output fails strict JSON schema parsing, execution falls back seamlessly to conservative rule-based ETF allocation without stalling the process.

3. **Stage 3: Multi-Exchange Connector Architecture**
   * **Toss Securities OpenAPI**: US equity market-order execution with automated OAuth2 token lifecycle management and automatic fallback from fractional orders to integer quantity orders when stock restrictions occur.
   * **Upbit API**: Low-latency REST & WebSocket interfaces for major crypto assets.
   * **CME Futures (LS Securities / CME MNQZ26)**: Micro E-mini Nasdaq-100 index futures connector for hedging macro downturns.

---

## 3. Project Structure

```
ai-quant-agent/
├── .github/
│   └── workflows/
│       └── ci.yml             # GitHub Actions CI (Ruff Lint + Pytest)
├── tests/
│   ├── __init__.py
│   ├── test_quant_rules.py    # Unit tests for quant filters & ATR boundaries
│   ├── test_risk_safeguards.py# Unit tests for settings, budget caps, and clamps
│   └── test_schema_validation.py # Unit tests for stateless LLM parsing
├── ai_client.py               # Async OpenAI-compatible LLM client
├── cli.py                     # Unified CLI entrypoint (argparse)
├── config.py                  # Global config & standardized environment specs
├── dashboard.html             # Responsive real-time monitoring dashboard
├── dashboard_server.py        # Asynchronous HMAC token-secured web server
├── history_manager.py         # Persistent execution logs & lesson tracking
├── main.py                    # Core event loop orchestration
├── market_data.py             # Real-time market metrics, technical indicators, VIX
├── news_service.py            # Macro and market news feed ingestion
├── performance_tracker.py     # Win-rate, profit factor, and Sharpe tracking
├── quant_rules.py             # Deterministic ATR stops & momentum algorithms
├── research_agents.py         # Multi-agent prompt synthesis & output schema
├── rotation_strategy.py       # Sector ETF rotation strategy engine
├── scheduler.py               # 24/7 trading cycle scheduler & reconciler
├── settings_manager.py        # Live parameter modification via atomic JSON
├── shadow_portfolio.py        # Paper trading sandbox & strategy leaderboard
├── telegram_service.py        # Instant telegram dispatch service
├── toss_service.py            # Toss Securities OpenAPI broker connector
├── utils.py                   # Atomic file I/O & math utilities
├── pyproject.toml             # Modern PEP 621 packaging & tool configs
├── requirements.txt           # Production and dev dependency manifests
├── .env.example               # Sanitized environment variable template
└── .gitignore                 # Strict isolation of keys, logs, and runtime data
```

---

## 4. Quick Start & CLI Usage

### Prerequisites
* Python 3.10 or higher
* Virtual environment (`venv` or `conda`)

### 1. Installation
```bash
# Clone the repository
git clone https://github.com/<YOUR_USERNAME>/ai-quant-agent.git
cd ai-quant-agent

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Environment Configuration
Copy the provided `.env.example` template to `.env` and configure your API keys:
```bash
cp .env.example .env
```

Key variables:
```ini
# Standardized OpenAI-Compatible LLM Spec
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=your_llm_api_key_here
LLM_MODEL=gpt-4o

# Brokerage Credentials (Toss Securities OpenAPI)
TOSS_APP_KEY=your_toss_app_key_here
TOSS_APP_SECRET=your_toss_app_secret_here
TOSS_ACCOUNT_NO=your_toss_account_no_or_account_seq

# Telegram Notifications
TELEGRAM_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_id_here

# Web Dashboard Security
DASHBOARD_TOKEN=your_secure_random_hex_token
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=8899
```

### 3. Running via CLI (`cli.py`)

The unified CLI provides full control over execution mode, risk caps, and services:

```bash
# Display help and options
python cli.py --help

# 1. Run in Paper Trading Mode (Dry-Run simulation)
python cli.py --mode paper --dry-run

# 2. Run Sector Rotation Strategy with $500 capital cap
python cli.py --mode rotation --capital-cap 500

# 3. Run Standalone Web Dashboard without triggering trading loops
python cli.py --dashboard-only --dashboard-port 8899

# 4. Run Brokerage and Network Connectivity Diagnostics
python cli.py --test-connection
```

Access the web dashboard in your browser:
`http://127.0.0.1:8899/?token=YOUR_DASHBOARD_TOKEN`

---

## 5. Reliability & Risk Safeguards

* **Hardcoded Dynamic ATR Stop-Loss**: Every trade automatically binds a stop-loss calculated directly from the asset's Average True Range ($ATR \times 1.5$), adjusted for market volatility ($VIX$).
* **Daily Drawdown Circuit Breaker**: If total realized + unrealized daily losses exceed `--max-daily-loss-pct` (default: 3.0%), all active buying is halted immediately for the remainder of the trading day.
* **Inverse Volatility Position Sizing**: Allocation weights are strictly inversely proportional to asset volatility ($\sigma_i^{-1} / \sum \sigma_j^{-1}$). Higher conviction from the LLM cannot override volatility-based sizing limits.
* **Atomic State Recovery**: The execution state and active orders are persisted via atomic JSON writes. Upon process restart, all previously managed positions are reconciled with the broker to prevent orphaned trades.
* **Reverse Proxy Isolation**: The web server binds strictly to `127.0.0.1`. Public exposure is intended to be routed through an HTTPS reverse proxy (Caddy / Nginx) with token authentication.

---

## 6. Testing & CI

Unit tests run entirely offline with mock fixtures and synthetic market data:

```bash
# Run all unit tests with pytest
pytest -v tests/

# Run lint checks with ruff
ruff check .
```

Every `push` and `pull_request` to `main` automatically triggers GitHub Actions CI verifying code style (`ruff`) and passing 100% of unit tests (`pytest`) across Python 3.10.

---

## 7. Disclaimer

This software is developed strictly for educational, academic, and algorithmic trading research purposes. Algorithmic trading carries substantial financial risk, including potential total loss of capital. No financial advice or profit guarantee is provided. Always thoroughly test strategies in dry-run/paper trading before committing capital.
