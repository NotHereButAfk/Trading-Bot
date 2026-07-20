"""Trading engine: auto entries, confirmation flow, manual close, exits."""

import time

import pytest

from bot.state import BotState
from bot.trader import TradingBot, LiveBroker, PositionNotFlatError
from bot.risk import RiskManager
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


# ------------------------------------------------------------- balance detection

def test_detect_balance_seeds_paper_when_key_present(cfg):
    cfg["trading"]["force_paper"] = True  # paper, but with a key available
    ex = FakeExchange()
    ex.equity_usdt = 137.42
    bot = TradingBot(cfg, BotState(), exchange=ex)
    bot.has_key = True  # simulate a configured key
    bot._detect_balance()
    assert bot.paper_broker.balance == 137.42
    assert bot._equity() == 137.42


def test_detect_balance_ignored_without_key(cfg):
    ex = FakeExchange()
    ex.equity_usdt = 999.0
    bot = TradingBot(cfg, BotState(), exchange=ex)
    bot.has_key = False
    start = bot.paper_broker.balance
    bot._detect_balance()
    assert bot.paper_broker.balance == start  # untouched -> configured starting balance


def test_use_real_balance_off_keeps_configured(cfg):
    cfg["trading"]["force_paper"] = True
    cfg["trading"]["use_real_balance"] = False
    ex = FakeExchange()
    ex.equity_usdt = 500.0
    bot = TradingBot(cfg, BotState(), exchange=ex)
    bot.has_key = True
    configured = bot.paper_broker.balance
    bot._detect_balance()
    assert bot.paper_broker.balance == configured  # opted out of real-balance seeding


def test_detect_balance_survives_fetch_error(cfg):
    class Boom(FakeExchange):
        def fetch_equity_usdt(self):
            raise RuntimeError("network down")
    bot = TradingBot(cfg, BotState(), exchange=Boom())
    bot.has_key = True
    bot._detect_balance()  # must not raise; keeps configured balance
    assert bot.paper_broker.balance == cfg["trading"]["paper_starting_balance"]


# --------------------------------------------------------------- live path

def _live_bot(cfg, exchange=None):
    cfg["trading"]["paper_trading"] = False  # bot reads the resolved flag directly
    cfg["trading"]["confirm_signals"] = False
    state = BotState()
    bot = TradingBot(cfg, state, exchange=exchange or FakeExchange())
    state.set_equity(10000.0)  # avoid a live balance call in these unit tests
    return bot, state


def test_live_entry_uses_actual_fill_and_reanchors_stops(cfg):
    ex = FakeExchange()
    ex.fill_slippage = 50.0  # filled 50 USDT worse than the signal price
    bot, state = _live_bot(cfg, ex)
    signal_price = ex.price
    bot._execute_entry("BTC/USDT:USDT", "long", signal_price, 500.0, 3.5, ["x"])
    assert len(state.open_trades) == 1
    trade = next(iter(state.open_trades.values()))
    # entry recorded at the real fill, not the pre-order price
    assert trade.entry_price == signal_price + 50.0
    # stop distance (risk) preserved despite the slippage
    assert abs((trade.entry_price - trade.stop_loss) - 2.0 * 500.0) < 1e-6


def test_live_close_verifies_flat(cfg):
    ex = FakeExchange()
    bot, state = _live_bot(cfg, ex)
    bot._execute_entry("BTC/USDT:USDT", "long", ex.price, 500.0, 3.5, ["x"])
    trade = next(iter(state.open_trades.values()))
    ex.price = ex.price * 1.05
    bot._close_trade(trade, ex.price, "manual close")
    assert len(state.open_trades) == 0
    assert state.closed_trades[-1].realized_pnl > 0  # closed in profit


def test_live_close_that_does_not_flatten_keeps_trade_open(cfg):
    ex = FakeExchange()
    bot, state = _live_bot(cfg, ex)
    bot._execute_entry("BTC/USDT:USDT", "long", ex.price, 500.0, 3.5, ["x"])
    trade = next(iter(state.open_trades.values()))
    ex.leave_open_on_close = True  # simulate a close order that didn't reduce
    bot._close_trade(trade, ex.price, "manual close")
    # Trade must stay tracked (still open and unprotected) so the bot retries.
    assert len(state.open_trades) == 1
    assert len(state.closed_trades) == 0


def test_live_broker_raises_when_not_flat(cfg):
    from bot.state import Trade
    ex = FakeExchange()
    ex.market_open("BTC/USDT:USDT", "long", 100, 5)
    ex.leave_open_on_close = True
    trade = Trade(
        trade_id="t", symbol="BTC/USDT:USDT", side="long", base_amount=0.1,
        contracts=100, entry_price=ex.price, stop_loss=0, take_profit=0,
        initial_stop=0, opened_at=0.0, leverage=5, notional=0,
    )
    with pytest.raises(PositionNotFlatError):
        LiveBroker().close_trade(ex, trade, ex.price)


def test_live_skips_entry_when_exchange_has_position(cfg):
    ex = FakeExchange()
    ex.preexisting["BTC/USDT:USDT"] = {"side": "long", "contracts": 10}
    bot, state = _live_bot(cfg, ex)
    bot._execute_entry("BTC/USDT:USDT", "long", ex.price, 500.0, 3.5, ["x"])
    assert len(state.open_trades) == 0  # refused to stack on an existing position
