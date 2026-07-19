#!/usr/bin/env python3
"""Entry point: starts the trading loop and (optionally) the GUI dashboard.

Usage:
    python run.py                  # uses config.yaml, opens the GUI
    python run.py --config my.yaml
    python run.py --no-gui         # headless (server) mode

Exit codes (so a supervisor / .bat wrapper can decide whether to restart):
    0  clean shutdown (you closed the window or pressed Ctrl+C)
    2  configuration/setup error — do NOT auto-restart, fix the config first
    1  unexpected crash — safe to auto-restart
"""

import argparse
import logging
import logging.handlers
import os
import signal
import sys
import threading

from bot.config import load_config
from bot.state import BotState
from bot.trader import TradingBot

CONFIG_ERROR = 2


def _make_connection_tester(cfg: dict):
    """Return a function the GUI can call to verify API keys against HTX."""
    import copy

    def test(api_key: str, api_secret: str):
        from bot.exchange import HTXFutures
        probe_cfg = copy.deepcopy(cfg)
        probe_cfg["exchange"]["api_key"] = api_key
        probe_cfg["exchange"]["api_secret"] = api_secret
        exchange = HTXFutures(probe_cfg)
        exchange.load_markets()
        equity = exchange.fetch_equity_usdt()  # needs valid auth
        return True, f"Connected. USDT balance: {equity:.2f}"

    return test


def _setup_logging(level_name: str, log_file: str | None):
    level = getattr(logging, level_name.upper(), logging.INFO)
    handlers = [logging.StreamHandler()]
    if log_file:
        # Rotate so a bot left running for weeks can't fill the disk.
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_file, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
            )
        )
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="HTX futures trading bot")
    parser.add_argument("--config", default="config.yaml", help="path to config file")
    parser.add_argument("--no-gui", action="store_true", help="run without the GUI")
    parser.add_argument("--log-file", default="bot.log",
                        help="rotating log file (empty string to disable)")
    args = parser.parse_args()

    try:
        cfg = load_config(args.config)
    except FileNotFoundError:
        print(f"Config file not found: {args.config}\n"
              f"Copy config.example.yaml to config.yaml and edit it first.",
              file=sys.stderr)
        return CONFIG_ERROR
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return CONFIG_ERROR

    _setup_logging(cfg["logging"]["level"], args.log_file or None)
    log = logging.getLogger("bot")

    if not os.path.exists(args.config):
        log.warning("no %s found — running on built-in defaults (paper mode)",
                    args.config)

    if not cfg["trading"]["paper_trading"]:
        log.warning("LIVE TRADING is enabled — real orders will be sent to HTX")

    state = BotState(trade_log_csv=cfg["logging"]["trade_log_csv"])
    bot = TradingBot(cfg, state)

    trader_thread = threading.Thread(target=bot.run, name="trader", daemon=True)
    trader_thread.start()

    use_gui = cfg["gui"]["enabled"] and not args.no_gui
    if use_gui:
        try:
            from bot.gui import Dashboard
        except Exception as exc:  # no display / tkinter missing
            log.warning("GUI unavailable (%s), falling back to headless mode", exc)
            use_gui = False
        else:
            dashboard = Dashboard(
                state, refresh_ms=cfg["gui"]["refresh_ms"], on_close=bot.stop,
                cfg=cfg, test_connection=_make_connection_tester(cfg),
                on_restart=True,
            )
            dashboard.run()
            bot.stop()
            trader_thread.join(timeout=10)
            if dashboard.restart_requested:
                log.info("restarting to apply new settings")
                # Clean full restart: re-exec the same command so the new key
                # and mode are picked up from scratch (preflight runs again).
                os.execv(sys.executable, [sys.executable] + sys.argv)
            return 0

    if not use_gui:
        stop = threading.Event()

        def handle_sig(_signum, _frame):
            log.info("shutdown requested")
            bot.stop()
            stop.set()

        signal.signal(signal.SIGINT, handle_sig)
        try:
            signal.signal(signal.SIGTERM, handle_sig)
        except (ValueError, AttributeError):
            pass  # SIGTERM not available on some Windows setups
        while trader_thread.is_alive() and not stop.is_set():
            trader_thread.join(timeout=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
