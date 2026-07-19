"""Trading engine: auto entries, confirmation flow, manual close, exits."""

import time

from bot.state import BotState
from bot.trader import TradingBot
from tests.conftest import FakeExchange, make_ohlcv, UPTREND


def _auto_bot(cfg):
    cfg["trading"]["confirm_signals"] = False
    state = BotState()
    bot = TradingBot(cfg, state, exchange=FakeExchange())
    bot.state.set_equity(bot._equity())
    return bot, state


def test_auto_entry_opens_trade(cfg):
    bot, state = _auto_bot(cfg)
    bot._tick(cfg["trading"]["symbols"])
    assert len(state.open_trades) == 1
    trade = next(iter(state.open_trades.values()))
    assert trade.side == "long"


def test_stop_loss_closes_and_loses(cfg):
    bot, state = _auto_bot(cfg)
    bot._tick(cfg["trading"]["symbols"])
    equity_before = bot._equity()
    bot.exchange.price = bot.exchange.price * 0.90  # crash through the stop
    bot._tick(cfg["trading"]["symbols"])
    assert len(state.open_trades) == 0
    closed = state.closed_trades[-1]
    assert closed.exit_reason == "stop loss"
    assert closed.realized_pnl < 0
    assert bot._equity() < equity_before


def test_confirm_mode_queues_then_executes(cfg):
    cfg["trading"]["confirm_signals"] = True
    state = BotState()
    bot = TradingBot(cfg, state, exchange=FakeExchange())
    assert state.entry_mode == "manual confirm"
    bot.state.set_equity(bot._equity())

    bot._tick(cfg["trading"]["symbols"])
    assert len(state.open_trades) == 0
    assert len(state.pending_signals) == 1

    pending = next(iter(state.pending_signals.values()))
    assert state.confirm_signal(pending.signal_id)
    assert state.wake_trader.is_set()
    bot._tick(cfg["trading"]["symbols"])
    assert len(state.open_trades) == 1
    assert len(state.pending_signals) == 0


def test_dismiss_removes_without_trading(cfg):
    cfg["trading"]["confirm_signals"] = True
    state = BotState()
    bot = TradingBot(cfg, state, exchange=FakeExchange())
    bot.state.set_equity(bot._equity())
    bot._tick(cfg["trading"]["symbols"])
    pending = next(iter(state.pending_signals.values()))
    assert state.dismiss_signal(pending.signal_id)
    bot._tick(cfg["trading"]["symbols"])
    assert len(state.open_trades) == 0
    assert len(state.pending_signals) == 0


def test_expired_signal_cannot_confirm(cfg):
    state = BotState()
    sig = state.add_pending_signal(
        "BTC/USDT:USDT", "long", 3.5, 50000.0, 500.0, 40.0, ["x"], ttl_sec=0.05
    )
    time.sleep(0.1)
    assert not state.confirm_signal(sig.signal_id)
    assert len(state.pending_signals) == 0


def test_manual_close_request(cfg):
    bot, state = _auto_bot(cfg)
    bot._tick(cfg["trading"]["symbols"])
    trade = next(iter(state.open_trades.values()))
    assert state.request_close(trade.trade_id)
    assert state.wake_trader.is_set()
    bot._tick(cfg["trading"]["symbols"])
    assert len(state.open_trades) == 0
    assert state.closed_trades[-1].exit_reason == "manual close"


def test_close_request_unknown_trade_is_noop(cfg):
    state = BotState()
    assert not state.request_close("does-not-exist")


def test_confirm_skips_when_position_open(cfg):
    """A confirmed signal must not double-open if a position already exists."""
    cfg["trading"]["confirm_signals"] = True
    state = BotState()
    bot = TradingBot(cfg, state, exchange=FakeExchange())
    bot.state.set_equity(bot._equity())
    bot._tick(cfg["trading"]["symbols"])
    pending = next(iter(state.pending_signals.values()))
    state.confirm_signal(pending.signal_id)
    bot._tick(cfg["trading"]["symbols"])          # opens the trade
    assert len(state.open_trades) == 1
    # Force a second confirmed signal for the same symbol.
    sig = state.add_pending_signal(
        "BTC/USDT:USDT", "long", 3.5, bot.exchange.price, 500.0, 40.0, ["x"], ttl_sec=60
    )
    state.confirm_signal(sig.signal_id)
    bot._execute_confirmed_signals()
    assert len(state.open_trades) == 1  # still just one
