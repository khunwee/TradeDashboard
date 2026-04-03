# MT4/MT5 Professional Trading Dashboard

A production-grade, self-hosted trading analytics platform that connects directly
to MetaTrader 4 and MetaTrader 5 via a custom Expert Advisor. Real-time data,
25+ statistical metrics, multi-channel alerts, and mobile-ready UI.

---

## Features

- **Real-time data** — WebSocket streaming from MT4/MT5 EA every 5 seconds
- **25+ metrics** — Sharpe, Sortino, Calmar, Kelly %, VaR 95%, MAE/MFE, Drawdown, etc.
- **Multi-account** — Portfolio view, group management, per-account drill-down
- **Alerts** — Email, LINE Notify, Telegram, SMS (Twilio), Discord Webhook, in-app
- **Advanced charts** — Equity curve (TradingView-style), daily P/L, heatmaps, distribution
- **PDF reports** — Auto-generated daily/weekly/monthly performance reports
- **Secure** — JWT + refresh tokens, 2FA (TOTP), brute-force lockout, RBAC
- **Dark professional UI** — Alpine.js + Chart.js, fully mobile-responsive

---

## Architecture

```
MetaTrader Terminal
    │
    │  HTTP POST /api/v1/push  (every 5 seconds)
    ▼
FastAPI Backend (Python)
    ├── PostgreSQL (Supabase)     — all data storage
    ├── APScheduler               — background jobs
    ├── WebSocket Manager         — real-time browser push
    └── Static Frontend           — Alpine.js + Chart.js

Browser → wss://yourapp.railway.app/ws/account/{id}?token=JWT
```

---

## Prerequisites

- Python 3.11+
- PostgreSQL 14+ (or [Supabase](https://supabase.com) free tier)
- MetaTrader 4 or MetaTrader 5 terminal
- (Optional) Railway.app account for deployment

---

## Quick Start (Local)

### 1. Clone and install

```bash
git clone https://github.com/youruser/trading-dashboard
cd trading-dashboard
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` — minimum required:

```env
DATABASE_URL=postgresql://user:password@localhost/trading_dashboard
SECRET_KEY=generate-with-openssl-rand-hex-32
```

### 3. Create the database

```bash
# Create PostgreSQL database
createdb trading_dashboard

# Run migrations (creates all tables)
python -c "from database import create_tables; create_tables()"
```

### 4. Start the server

```bash
python main.py
# or:
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Open `http://localhost:8000` — the login page will appear.

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | ✅ | — | PostgreSQL connection string |
| `SECRET_KEY` | ✅ | random | JWT signing key — **must be set in production** |
| `DEBUG` | | `false` | Enable debug logging and SQL echo |
| `ALLOWED_ORIGINS` | | `http://localhost:3000` | CORS origins (comma-separated) |
| `SMTP_HOST` | | `smtp.gmail.com` | Email server host |
| `SMTP_PORT` | | `587` | Email server port |
| `SMTP_USER` | | — | Email username |
| `SMTP_PASSWORD` | | — | Email password or app password |
| `SMTP_FROM` | | — | From address for alerts |
| `LINE_NOTIFY_TOKEN` | | — | LINE Notify API token |
| `TELEGRAM_BOT_TOKEN` | | — | Telegram bot token from @BotFather |
| `TWILIO_ACCOUNT_SID` | | — | Twilio Account SID for SMS |
| `TWILIO_AUTH_TOKEN` | | — | Twilio Auth Token |
| `TWILIO_FROM_NUMBER` | | — | Twilio sender phone number |
| `FRONTEND_URL` | | `http://localhost:8000` | Public URL (for password reset links) |
| `RISK_FREE_RATE` | | `0.0` | Annual risk-free rate for Sharpe calculation |
| `TICK_DATA_RETENTION_DAYS` | | `90` | Days to keep snapshot data |

---

## MT4 EA Installation

### Step 1 — Copy EA file

1. Open MetaTrader 4
2. Click **File → Open Data Folder**
3. Navigate to `MQL4/Experts/`
4. Copy `TradingDashboardEA.mq4` into this folder
5. In MT4, open the **Navigator** panel (Ctrl+N)
6. Expand **Expert Advisors** and find `TradingDashboardEA`

### Step 2 — Configure EA inputs

Drag the EA onto any chart. In the **Inputs** tab, set:

| Input | Value | Description |
|-------|-------|-------------|
| `DashboardURL` | `https://yourapp.railway.app/api/v1/push` | Your server URL |
| `ApiKey` | _(from dashboard)_ | Generated per-account API key |
| `PushIntervalSec` | `5` | How often to send data (seconds) |
| `PushOpenTrades` | `true` | Send open position data |
| `PushClosedTrades` | `true` | Send closed trade data |
| `MaxClosedPerPush` | `50` | Closed trades to include per push |

### Step 3 — Allow web requests

In MetaTrader 4:
1. **Tools → Options → Expert Advisors**
2. Check **"Allow WebRequest for listed URL"**
3. Add your server URL: `https://yourapp.railway.app`

### Step 4 — Enable EA

- Ensure **AutoTrading** is enabled (F7 shortcut or toolbar button)
- The EA status bar at the bottom should show no errors
- Check the **Journal** tab for connection confirmations

---

## MT5 EA Installation

Same process but:
1. Copy `TradingDashboardEA.mq5` to `MQL5/Experts/`
2. The URL allowlist is in **Tools → Options → Expert Advisors** (same location)

---

## Dashboard Setup

### Create your account

1. Navigate to `http://yourapp/static/login.html`
2. Click **Create Account** (or use the `/api/v1/auth/register` endpoint)
3. Log in

### Add a trading account

1. Go to **Portfolio Overview** → **+ Add Account**
2. Enter your MT4/MT5 account number and broker server name
3. Click **Generate API Key** — copy the key immediately (shown only once)
4. Paste this key into the EA's `ApiKey` input

### Verify connection

After starting the EA, the dashboard status should change to **LIVE** within 10 seconds.
You'll see the green pulsing dot in the account header.

---

## Deployment — Railway.app

### One-click deploy

1. Push your code to a GitHub repository
2. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub Repo**
3. Select your repository

Railway will auto-detect the `railway.toml` configuration.

### Add environment variables

In Railway's project settings → **Variables**, add:

```
DATABASE_URL         = postgresql://... (Railway PostgreSQL plugin or Supabase)
SECRET_KEY           = <openssl rand -hex 32>
ALLOWED_ORIGINS      = https://yourapp.railway.app
FRONTEND_URL         = https://yourapp.railway.app
SMTP_USER            = your@gmail.com
SMTP_PASSWORD        = your-app-password
LINE_NOTIFY_TOKEN    = (optional)
TELEGRAM_BOT_TOKEN   = (optional)
```

### Add PostgreSQL

In Railway: **New** → **Database** → **PostgreSQL**

The `DATABASE_URL` is automatically set in Railway's environment when you link the plugin.

### Custom domain

In Railway project → **Settings** → **Domains** → add your domain.
Then update `ALLOWED_ORIGINS` and `FRONTEND_URL`.

---

## Deployment — Docker

```bash
# Build image
docker build -t trading-dashboard .

# Run with environment file
docker run -d \
  --name trading-dashboard \
  -p 8000:8000 \
  --env-file .env \
  trading-dashboard
```

---

## Supabase (PostgreSQL) Setup

1. Create a [Supabase](https://supabase.com) project (free tier works)
2. Go to **Project Settings → Database → Connection string**
3. Copy the **URI** format connection string
4. Set `DATABASE_URL` in your `.env`:

```env
DATABASE_URL=postgresql://postgres:[password]@db.[project].supabase.co:5432/postgres
```

---

## API Reference

The full interactive API documentation is available at:

```
https://yourapp.railway.app/api/docs       (Swagger UI)
https://yourapp.railway.app/api/redoc      (ReDoc)
```

### Key Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/auth/register` | Register new user |
| `POST` | `/api/v1/auth/login` | Login (returns JWT) |
| `POST` | `/api/v1/auth/refresh` | Refresh access token |
| `GET`  | `/api/v1/accounts` | List accounts |
| `POST` | `/api/v1/accounts` | Create account |
| `POST` | `/api/v1/accounts/{id}/api-key` | Generate EA API key |
| `POST` | `/api/v1/push` | EA push endpoint (authenticated with API key) |
| `GET`  | `/api/v1/trades/open/{id}` | Get open positions |
| `GET`  | `/api/v1/trades/history/{id}` | Get trade history |
| `GET`  | `/api/v1/stats/{id}/equity-curve` | Equity curve data |
| `GET`  | `/api/v1/stats/{id}/summary` | All metrics summary |
| `POST` | `/api/v1/alerts/rules` | Create alert rule |
| `GET`  | `/health` | Health check |

---

## Metrics Reference

| Metric | Formula | Description |
|--------|---------|-------------|
| **Growth %** | Modified Dietz | Total return adjusted for deposits/withdrawals |
| **Sharpe Ratio** | (R - Rf) / σ × √252 | Risk-adjusted return (annualized) |
| **Sortino Ratio** | (R - Rf) / σ_down × √252 | Like Sharpe but penalizes downside only |
| **Calmar Ratio** | Annual Return / Max Drawdown % | Return per unit of max drawdown risk |
| **Profit Factor** | Gross Win / Gross Loss | Every $1 lost, how much gained |
| **Win Rate** | Winners / Total Trades × 100 | Percentage of profitable trades |
| **Expectancy** | (WR × Avg Win) - (LR × Avg Loss) | Average expected profit per trade |
| **Recovery Factor** | Net Profit / Max Drawdown $ | How many times drawdown recovered |
| **Kelly %** | WR - (LR / Win-Loss Ratio) × 100 | Optimal position sizing percentage |
| **VaR 95%** | 5th percentile of daily returns | Daily loss not exceeded 95% of time |
| **MAE** | Maximum Adverse Excursion | Worst floating loss during a trade |
| **MFE** | Maximum Favorable Excursion | Best floating profit during a trade |
| **Max Drawdown** | Peak-to-trough decline | Largest equity drop from any peak |

---

## Alert Conditions

| Condition | Trigger |
|-----------|---------|
| `profit_daily_target` | Today's profit ≥ threshold |
| `profit_weekly_target` | Week's profit ≥ threshold |
| `new_equity_high` | Equity exceeds all-time high |
| `drawdown_warning` | Drawdown % ≥ threshold (warning) |
| `drawdown_critical` | Drawdown % ≥ threshold (critical) |
| `daily_loss_limit` | Today's loss ≥ threshold |
| `ea_disconnected` | No push received within N minutes |
| `ea_version_changed` | EA version number changed |
| `max_open_orders` | Open orders ≥ threshold |
| `margin_level_low` | Margin level % ≤ threshold |
| `trade_opened` | New position opened |
| `trade_closed_profit` | Position closed with profit |
| `trade_closed_loss` | Position closed with loss |

---

## Running Tests

```bash
# Install test dependencies
pip install pytest pytest-asyncio httpx

# Run all tests
pytest tests/ -v

# Run unit tests only
pytest tests/test_calculations.py -v

# Run API tests only
pytest tests/test_api.py -v

# Run with coverage
pip install pytest-cov
pytest tests/ --cov=. --cov-report=html
```

---

## Project Structure

```
trading-dashboard/
├── main.py                     # FastAPI app entry point + WebSocket manager
├── config.py                   # Pydantic settings (reads .env)
├── database.py                 # SQLAlchemy engine and session management
├── models.py                   # All database models (20+ tables)
├── auth.py                     # JWT, 2FA, brute-force protection
├── calculations.py             # 25+ financial metric calculations
├── alerts.py                   # Alert rule engine + multi-channel delivery
├── scheduler.py                # APScheduler background jobs
├── reports.py                  # ReportLab PDF generation
├── requirements.txt
├── Dockerfile
├── railway.toml
├── .env.example
│
├── routers/
│   ├── auth_router.py          # /api/v1/auth/*
│   ├── accounts.py             # /api/v1/accounts/*
│   ├── push.py                 # /api/v1/push (EA endpoint)
│   ├── stats.py                # /api/v1/stats/*
│   ├── trades.py               # /api/v1/trades/*
│   ├── alerts_router.py        # /api/v1/alerts/*
│   └── admin.py                # /api/v1/admin/*
│
├── static/
│   ├── login.html              # Login page with 2FA
│   ├── index.html              # Portfolio dashboard
│   ├── account.html            # Single account detail page
│   ├── css/
│   │   └── dashboard.css       # Dark professional theme
│   └── js/
│       ├── dashboard.js        # Alpine.js state + API client
│       ├── charts.js           # Chart.js rendering helpers
│       └── websocket.js        # WebSocket connection manager
│
├── tests/
│   ├── conftest.py
│   ├── test_calculations.py    # Unit tests — all financial metrics
│   └── test_api.py             # Integration tests — all API endpoints
│
├── TradingDashboardEA.mq4      # MetaTrader 4 Expert Advisor
└── TradingDashboardEA.mq5      # MetaTrader 5 Expert Advisor
```

---

## Security Considerations

- **API keys** are stored as SHA-256 hashes — shown to user only once
- **Passwords** use bcrypt with cost factor 12
- **JWT access tokens** expire in 15 minutes; refresh tokens rotate on use
- **2FA (TOTP)** supported with backup codes
- **Brute-force protection** — 5 failed attempts locks account for 15 minutes
- **Constant-time comparison** for API key validation (prevents timing attacks)
- **CORS** restricted to configured origins
- **IP whitelist** optional per-account for EA push endpoint
- **Rate limiting** on all public endpoints via SlowAPI

---

## Troubleshooting

### EA shows "Connection failed"

1. Ensure the dashboard URL is reachable from the trading terminal's server
2. In MT4/MT5: Tools → Options → Expert Advisors → check URL is in allowlist
3. Verify the API key was generated for the correct account number
4. Check the Journal tab in MetaTrader for error messages

### EA connected but data not updating

1. Check the EA is attached to a chart that is streaming prices
2. Verify `PushIntervalSec` is set correctly
3. Check the dashboard's WebSocket indicator — should be green/Live

### "Invalid API key" error

The API key is shown only once at generation time. If lost:
1. Go to Account Settings → Generate New Key
2. Copy the new key immediately
3. Update the EA's `ApiKey` input parameter

### Charts not loading

1. Open browser developer tools (F12) → Console tab
2. Check for network errors
3. Ensure the account has trade data (push at least one snapshot from EA)

---

## License

MIT License — see LICENSE file for details.

## Support

For issues and feature requests, open a GitHub issue.

---

*Built with FastAPI, SQLAlchemy, Alpine.js, Chart.js, and ReportLab.*
