# 🥇 XAUUSD/USD Macro News Bot

A professional Telegram bot that monitors financial news and sends **only high-impact macroeconomic alerts** that directly affect **Gold (XAUUSD)** and the **US Dollar (USD)**.

> Built for traders. Zero spam. Maximum signal.

---

## 🧠 How It Works

```
RSS Feeds + NewsAPI
       ↓
 Keyword Scoring (min score ≥ 3)
       ↓
 Deduplication Check (SHA-256 hash)
       ↓
 AI Analysis (Claude → OpenAI → Rule fallback)
       ↓
 Final Gate: Strength=High OR Tradable=Yes
       ↓
 Telegram Alert (max 5/hour)
```

---

## 📁 Project Structure

```
gold_news_bot/
├── main.py              # Orchestrator — runs the full pipeline
├── news_fetcher.py      # Async RSS + NewsAPI aggregator (9+ sources)
├── analyzer.py          # AI analysis via Claude / OpenAI / rule fallback
├── deduplicator.py      # SHA-256 fingerprint deduplication (persistent JSON)
├── telegram_sender.py   # Formatted Telegram alerts
├── storage.json         # Auto-generated deduplication database
├── requirements.txt     # Python dependencies
├── railway.toml         # Railway.app deployment config
├── .env.example         # Environment variable template
└── .github/
    └── workflows/
        └── bot.yml      # GitHub Actions scheduled runner
```

---

## 🚀 Quick Start

### 1. Prerequisites

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Create Your Bot

1. Open Telegram → message **@BotFather**
2. `/newbot` → follow prompts → copy the **bot token**
3. Message your new bot, then visit:
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
   Copy the `chat.id` from the response.

### 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
TELEGRAM_BOT_TOKEN=7123456789:AAH...
TELEGRAM_CHAT_ID=-1001234567890
ANTHROPIC_API_KEY=sk-ant-...
```

### 4. Run

```bash
python main.py
```

---

## 📊 Scoring System

| Event Type           | Score |
|----------------------|-------|
| FOMC / Rate Decision | +3    |
| NFP / Payrolls       | +3    |
| Nuclear threat       | +3    |
| Inflation (CPI/PPI)  | +2    |
| Powell / CB Speech   | +2    |
| Geopolitics          | +2    |
| Oil shock (OPEC)     | +2    |

**Minimum score to reach AI analysis: 3**

---

## 🤖 AI Analysis Fields

| Field       | Values                          |
|-------------|---------------------------------|
| USD Impact  | Bullish / Bearish / Neutral     |
| Gold Impact | Bullish / Bearish / Neutral     |
| Strength    | Low / Medium / **High** ← gate |
| Tradable    | **Yes** ← gate / No            |

Article is sent only if **Strength = High** OR **Tradable = Yes**.

---

## 📬 Message Format (Telegram)

```
🚨 HIGH IMPACT NEWS
🏦 Rate Decision

📌 Title: Fed Raises Interest Rates by 25bps

💵 USD: 🟢 Bullish
🥇 Gold: ⬇️ Bearish

🔥 Strength: High
✅ Tradable: Yes

📝 Summary: Rate hike signals Fed hawkishness; gold faces downward pressure.

⚡ Score: [●●●●○] 4/5+
🕐 14:32 UTC | 📡 Reuters
🔗 Read full article
```

---

## ☁️ Deployment

### Option A: Railway.app (Recommended — persistent bot)

1. Push code to GitHub
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Add environment variables in Railway dashboard
4. Bot runs 24/7 automatically

### Option B: GitHub Actions (Free — cron-based)

1. Push code to GitHub
2. Go to **Settings → Secrets → Actions** and add:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `ANTHROPIC_API_KEY`
3. The workflow runs every 15 min on weekdays (6–20 UTC)

> ⚠️ GitHub Actions caches `storage.json` between runs to maintain deduplication.

---

## ⚙️ Configuration Options

| Variable               | Default        | Description                        |
|------------------------|----------------|------------------------------------|
| `TELEGRAM_BOT_TOKEN`   | *(required)*   | Bot token from @BotFather          |
| `TELEGRAM_CHAT_ID`     | *(required)*   | Target chat ID(s), comma-separated |
| `ANTHROPIC_API_KEY`    | *(recommended)*| Claude AI analysis                 |
| `OPENAI_API_KEY`       | optional       | OpenAI fallback analysis           |
| `NEWSAPI_KEY`          | optional       | Extra news source (100 req/day free)|
| `POLL_INTERVAL`        | `600`          | Seconds between cycles             |
| `MAX_MSG_PER_HOUR`     | `5`            | Rate limit cap                     |
| `STORAGE_PATH`         | `storage.json` | Dedup database location            |
| `DEDUP_RETENTION_DAYS` | `7`            | Days to keep fingerprints          |
| `ONE_SHOT`             | `0`            | Set `1` for GitHub Actions mode    |

---

## 🔒 Security Notes

- Never commit your `.env` file
- Add `.env` to `.gitignore`
- Railway and GitHub Actions handle secrets securely via env vars

---

## 📡 News Sources

| Source                    | Type    |
|---------------------------|---------|
| Reuters (Top + Business)  | RSS     |
| MarketWatch (Economy)     | RSS     |
| Investing.com (Forex/Comm)| RSS     |
| FXStreet                  | RSS     |
| ForexLive                 | RSS     |
| CNBC (Economy + Markets)  | RSS     |
| Bloomberg Economics       | RSS     |
| NewsAPI (optional)        | REST API|

---

## 🧪 Testing

Test a single cycle without polling loop:

```bash
ONE_SHOT=1 python main.py
```

---

*Built for professional traders who want signal, not noise.*
