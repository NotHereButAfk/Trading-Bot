# Running the bot 24/7 on Windows

This guide gets the bot running on your own Windows PC and keeps it running
around the clock. It runs on **your** computer (that you control), not on any
cloud or on the assistant's machine — your API keys never leave your PC.

> The bot only trades while your PC is on and running it. "24/7" means *your
> machine stays on 24/7*. A laptop that sleeps or a PC that shuts down stops the
> bot. See "Keep the PC awake" and "Start automatically on boot" below.

---

## 1. Download the project from GitHub

**Option A — Git (recommended, easy updates later):**

1. Install Git from https://git-scm.com/download/win (accept the defaults).
2. Open **Command Prompt** and run:
   ```
   cd %USERPROFILE%\Documents
   git clone https://github.com/NotHereButAfk/Trading-Bot.git
   cd Trading-Bot
   git checkout claude/crypto-trading-bot-htx-dxfo29
   ```
   Later, get updates with `git pull`.

**Option B — ZIP (no Git):**

1. On the GitHub page, switch to the branch
   `claude/crypto-trading-bot-htx-dxfo29`.
2. Click **Code ▸ Download ZIP**.
3. Right-click the downloaded ZIP ▸ **Extract All** to e.g.
   `Documents\Trading-Bot`.

---

## 2. Install Python

1. Download Python 3.10+ from https://www.python.org/downloads/.
2. Run the installer and **tick "Add python.exe to PATH"** on the first screen,
   then click Install. (Tkinter, used by the GUI, is included automatically.)

---

## 3. One-time setup

In the project folder, **double-click `setup.bat`**. It will:

- create an isolated virtual environment (`.venv`),
- install the dependencies,
- create your `config.yaml` from the template.

If Windows SmartScreen warns about a `.bat`, click **More info ▸ Run anyway**
(you can open the file in Notepad first to see it's just the commands above).

---

## 4. Configure the bot

**Start in paper mode** — it is the default, so you can just run the bot first
and configure it from inside the app.

To add your HTX API key, the easiest way is **in the app**: start the bot, click
**⚙ Settings / API Key** in the top bar, paste your key and secret, and Save.
It's stored locally in `credentials.json` (gitignored, owner-only) — you never
have to edit a file. You can also edit `config.yaml` in Notepad if you prefer.
Either way, **never paste your key into a chat or share it**. To trade real
money, follow **[GO_LIVE.md](GO_LIVE.md)** (tick the LIVE box in Settings, or set
`paper_trading: false` + `confirm_live: true`).

For **unattended 24/7** operation, decide how entries happen:

- **Fully automatic:** set `confirm_signals: false`. The bot opens trades on its
  own — no clicking needed. Required if you won't be at the PC to confirm.
- **Manual confirm (safer):** keep `confirm_signals: true`, but then you must
  keep the GUI open and press **Confirm** on each signal, so it isn't truly
  unattended.

---

## 5. Start the bot

**Double-click `run_bot.bat`.** The control-panel window opens and the bot
starts trading (paper by default). This launcher **auto-restarts the bot if it
ever crashes**, so a temporary network glitch won't stop it for good. Close the
window (or press Ctrl+C, then choose to stop) to shut it down cleanly.

For a headless, no-window run (e.g. a dedicated always-on PC), double-click
**`run_bot_headless.bat`** instead — but only with `confirm_signals: false`.

Logs are written to `bot.log` (rotating), and every closed trade to
`trades.csv`, so you can review what happened while you were away.

---

## Keep the PC awake

Stop Windows from sleeping, or the bot pauses:

1. **Settings ▸ System ▸ Power & battery ▸ Screen and sleep**.
2. Set **"When plugged in, put my device to sleep after"** to **Never**.
3. On a laptop, do the same and keep it plugged in.

---

## Start automatically on boot (optional, for true 24/7)

So the bot comes back by itself after a power cut or Windows Update reboot:

1. Press Start, type **Task Scheduler**, open it.
2. **Create Task** (not "Basic Task").
3. **General** tab: name it `Trading Bot`. For the GUI launcher, leave
   **"Run only when user is logged on"** selected (a GUI needs your desktop).
   For the headless launcher you may choose "Run whether logged on or not".
4. **Triggers** tab ▸ **New** ▸ Begin the task: **At log on** (or **At startup**
   for the headless option) ▸ OK.
5. **Actions** tab ▸ **New** ▸ Program/script: **Browse** to `run_bot.bat`
   (or `run_bot_headless.bat`) in the project folder ▸ OK.
6. **Settings** tab: tick **"If the task fails, restart every"** 1 minute, and
   untick **"Stop the task if it runs longer than…"**.
7. OK. The bot now launches on boot and the `.bat` keeps it alive after that.

To stop it, disable or delete the task in Task Scheduler (and close the window).

---

## Updating later

- **Git:** `git pull` in the project folder, then re-run `setup.bat` if
  `requirements.txt` changed. Your `config.yaml` is left untouched.
- **ZIP:** download the new ZIP, then copy your existing `config.yaml` into the
  new folder before running it.

---

## Troubleshooting

- **"Python was not found"** — reinstall Python with "Add to PATH" ticked, or
  reboot so PATH updates.
- **The window opens and closes instantly** — run `run_bot.bat` from a Command
  Prompt so you can read the error, or check `bot.log`.
- **Config error, not restarting** — the launcher stops on purpose for bad
  config (exit code 2). Fix `config.yaml` and start again.
- **GUI didn't appear** — it falls back to headless if the desktop is
  unavailable; check `bot.log`. Under Task Scheduler, GUI mode needs
  "Run only when user is logged on".
- **The exchange is the source of truth** — if anything looks off, open the HTX
  app and check/close positions there directly.
