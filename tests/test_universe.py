"""Top-N-by-volume universe selection and the scan/manage split."""

import time

from bot.config import TIMEFRAME_SECONDS
from bot.exchange import HTXFutures
from bot.state import BotState
from bot.trader import TradingBot
from tests.conftest import FakeExchange


class FakeCCXT:
    """Minimal stand-in for a ccxt client for ranking tests (no network)."""

    def __init__(self):
        self.markets = {
            "A/USDT:USDT": {"symbol": "A/USDT:USDT", "swap": True, "linear": True,
                            "active": True, "settle": "USDT"},
            "B/USDT:USDT": {"symbol": "B/USDT:USDT", "swap": True, "linear": True,
                            "active": True, "settle": "USDT"},
            "C/USDT:USDT": {"symbol": "C/USDT:USDT", "swap": True, "linear": True,
                            "active": True, "settle": "USDT"},
            "SPOT/USDT": {"symbol": "SPOT/USDT", "swap": False, "linear": True,
                          "active": True, "settle": "USDT"},        # not a swap
            "X/USD:BTC": {"symbol": "X/USD:BTC", "swap": True, "linear": False,
                          "active": True, "settle": "BTC"},         # inverse, wrong settle
        }

    def fetch_tickers(self, symbols=None):
        return {
            "A/USDT:USDT": {"quoteVolume": 100.0},
            "B/USDT:USDT": {"quoteVolume": 300.0},
            "C/USDT:USDT": {"quoteVolume": 200.0},
        }


def _htx_with_fake_client(cfg):
    ex = HTXFutures(cfg)          # constructing ccxt.htx does no network
    ex.client = FakeCCXT()
    ex._markets_loaded = True
    return ex


def test_top_symbols_ranked_by_volume_and_filtered(cfg):
    ex = _htx_with_fake_client(cfg)
    top = ex.top_symbols_by_volume(2)
    assert top == ["B/USDT:USDT", "C/USDT:USDT"]  # highest volume first
    # spot and inverse markets are excluded entirely
    assert ex.top_symbols_by_volume(10) == ["B/USDT:USDT", "C/USDT:USDT", "A/USDT:USDT"]


def test_resolve_universe_uses_top_volume(cfg):
    cfg["trading"]["universe"] = "top_volume"
    cfg["trading"]["universe_size"] = 3
    ex = FakeExchange()
    ex.top_symbols = ["X/USDT:USDT", "Y/USDT:USDT", "Z/USDT:USDT"]
    bot = TradingBot(cfg, BotState(), exchange=ex)
    assert bot._resolve_universe() == ["X/USDT:USDT", "Y/USDT:USDT", "Z/USDT:USDT"]


def test_resolve_universe_falls_back_to_list_when_empty(cfg):
    cfg["trading"]["universe"] = "top_volume"
    ex = FakeExchange()
    ex.top_symbols = []  # nothing returned
    bot = TradingBot(cfg, BotState(), exchange=ex)
    assert bot._resolve_universe() == list(cfg["trading"]["symbols"])


def test_list_universe_unchanged(cfg):
    bot = TradingBot(cfg, BotState(), exchange=FakeExchange())
    assert bot._resolve_universe() == list(cfg["trading"]["symbols"])


def test_entry_scan_due_once_per_candle(cfg):
    bot = TradingBot(cfg, BotState(), exchange=FakeExchange())
    assert bot._entry_scan_due() is True    # first call → new bucket
    assert bot._entry_scan_due() is False   # same candle → skip
    bot._last_scan_bucket -= 1              # pretend a new candle closed
    assert bot._entry_scan_due() is True


def test_open_position_managed_even_when_outside_universe(cfg):
    """A position must keep being managed (stopped out) even if its symbol is
    no longer in the scan universe."""
    cfg["trading"]["confirm_signals"] = False
    ex = FakeExchange()
    bot = TradingBot(cfg, BotState(), exchange=ex)
    bot.state.set_equity(bot._equity())
    bot._execute_entry("BTC/USDT:USDT", "long", ex.price, 500.0, 3.5, ["x"])
    assert len(bot.state.open_trades) == 1

    # Universe no longer contains BTC, and pretend this candle is already scanned
    # so the tick does pure position management.
    bot.symbols = ["OTHER/USDT:USDT"]
    tf = TIMEFRAME_SECONDS[cfg["trading"]["timeframe"]]
    bot._last_scan_bucket = int(time.time() // tf)
    ex.price = ex.price * 0.90  # crash through the stop

    bot._tick()
    assert len(bot.state.open_trades) == 0            # BTC still stopped out
    assert bot.state.closed_trades[-1].exit_reason == "stop loss"
