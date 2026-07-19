#!/usr/bin/env python3
"""Entry point: starts the trading loop and (optionally) the GUI dashboard.

Usage:
    python run.py                  # uses config.yaml, opens the GUI
    python run.py --config my.yaml
    python run.py --no-gui         # headless (server) mode
"""

import argparse
import logging
import signal
import sys
import threading

from bot.config import load_config
from bot.state import BotState
from bot.trader import TradingBot


def main():
    parser = argparse.ArgumentParser(description="HTX futures trading bot")
    parser.add_argument("--config", default="config.yaml", help="path to config file")
    parser.add_argument("--no-gui", action="store_true", help="run without the GUI")
    args = parser.parse_args()

    cfg = load_config(args.config)

    logging.basicConfig(
        level=getattr(logging, cfg["logging"]["level"].upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    log = logging.getLogger("bot")

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
                state, refresh_ms=cfg["gui"]["refresh_ms"], on_close=bot.stop
            )
            dashboard.run()
            bot.stop()
            trader_thread.join(timeout=10)
            return

    if not use_gui:
        stop = threading.Event()

        def handle_sig(_signum, _frame):
            log.info("shutdown requested")
            bot.stop()
            stop.set()

        signal.signal(signal.SIGINT, handle_sig)
        signal.signal(signal.SIGTERM, handle_sig)
        while trader_thread.is_alive() and not stop.is_set():
            trader_thread.join(timeout=1)


if __name__ == "__main__":
    sys.exit(main())
