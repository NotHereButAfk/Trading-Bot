# Going live with real money

This bot ships in **paper mode**. Switching it to trade your real HTX balance
takes three deliberate steps. Read this whole page first.

> ⚠️ Live futures trading with leverage can lose money fast, including more than
> you might expect once fees and slippage are added. Only trade funds you can
> afford to lose. Nobody — not this bot, not its author — can guarantee a
> profit. Start with the **smallest** size and lowest leverage you can.

## Never paste your API key into a chat or commit it

Your key is a password to your money. It goes in exactly one of two places:

- your local `config.yaml` (which is **gitignored** — it never gets committed), or
- environment variables on the machine that runs the bot.

Do **not** paste it into a chat window, a commit, a screenshot, or an issue.

## Step 1 — Create an HTX API key

1. Log in to HTX → **API Management** (https://www.htx.com/en-us/apikey/).
2. Create a key with **Trade** permission for **USDT-M futures/swap**.
3. **Do NOT enable Withdraw.** A trading bot never needs it; leaving it off
   means a leaked key still cannot move coins off your account.
4. If HTX lets you, **bind the key to your IP address** (the machine running
   the bot). This is the single best protection for the key.
5. Copy the **Access Key** (api key) and **Secret Key**. The secret is shown
   only once.

## Step 2 — Put the key into your config (not the chat)

If you don't have a `config.yaml` yet:

```bash
cp config.example.yaml config.yaml
```

Then either **(a)** edit `config.yaml`:

```yaml
exchange:
  api_key: "YOUR_ACCESS_KEY"
  api_secret: "YOUR_SECRET_KEY"
  confirm_live: false          # leave false for now
```

or **(b)** — cleaner — leave the config blank and export environment variables,
so the key never touches a file:

```bash
export HTX_API_KEY="YOUR_ACCESS_KEY"
export HTX_API_SECRET="YOUR_SECRET_KEY"
```

### Verify the key works while STILL in paper mode

Keep `paper_trading: true` and run a backtest/fetch that needs the exchange —
the easiest check is the backtester, which pulls live candles:

```bash
python backtest.py --symbol "BTC/USDT:USDT" --timeframe 15m --candles 500
```

If that connects and prints results, your network and (public) API access work.

## Step 3 — Flip the two live switches

Live trading is intentionally gated behind **two** flags so you can't start it
by accident. In `config.yaml`:

```yaml
trading:
  paper_trading: false     # switch 1
exchange:
  confirm_live: true       # switch 2 — "I understand this is real money"
```

Start conservatively in the same file:

```yaml
trading:
  leverage: 2              # low
  max_open_positions: 1    # one trade at a time while you build trust
  confirm_signals: true    # keep manual confirmation on at first
risk:
  risk_per_trade_pct: 0.5  # risk half a percent of equity per trade
  max_daily_loss_pct: 3.0  # hard stop for the day
```

Then run:

```bash
python run.py
```

You'll see `HTX Futures Bot — LIVE` in the control panel and get a startup
email. With `confirm_signals: true`, **no order is placed until you press
Confirm** on a signal — so the first real trade is still your decision.

## What the bot does to protect you on live

- **Two-switch gate** — refuses to start live unless both `paper_trading: false`
  and `confirm_live: true` are set, and API keys are present.
- **Startup preflight** — sets your leverage/margin mode and emails you if that
  fails; warns you about any position already open on a traded symbol (it will
  not touch positions it didn't open).
- **Real fills** — records the actual average fill price from the exchange and
  anchors the stop-loss / take-profit to it, preserving your intended risk.
- **Verified closes** — after every close it confirms with the exchange that the
  position is actually flat; if not, it keeps the trade tracked and sends an
  **URGENT** email so you can act, rather than falsely reporting it closed.
- **No stacking** — won't open a second position on a symbol the exchange
  already shows as open.
- **Automatic risk exits** — stop-loss, take-profit and trailing stops always
  run without confirmation; the daily-loss circuit breaker halts new entries.

## A sane first-week routine

1. Run **paper** for a few days; watch the signal log and paper trades.
2. Backtest your symbols over a couple thousand candles.
3. Go live with **leverage 2, risk 0.5%, one position, manual confirm**.
4. Confirm a few trades by hand and watch fills, stops and closes behave.
5. Only then consider raising size — slowly.

## If something looks wrong

Open the HTX app/website and look at your actual positions. The exchange is the
source of truth. You can always close a position there directly. If you get an
`URGENT: ... did not close` email, check and close it manually on HTX.
