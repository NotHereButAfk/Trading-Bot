"""Persistent and resettable paper-account behavior."""

import json

from bot.state import BotState
from bot.trader import TradingBot
from tests.conftest import FakeExchange


def test_default_paper_balance_is_30(cfg):
    bot = TradingBot(cfg, BotState(), exchange=FakeExchange())
    assert bot.paper_broker.balance == 30.0


def test_paper_cash_and_open_trades_survive_restart(cfg):
    cfg["trading"]["confirm_signals"] = False
    first = TradingBot(cfg, BotState(), exchange=FakeExchange())
    first._tick(cfg["trading"]["symbols"])
    saved_balance = first.paper_broker.balance
    saved_trade = next(iter(first.state.open_trades.values()))

    second = TradingBot(cfg, BotState(), exchange=FakeExchange())
    assert second.paper_broker.balance == saved_balance
    assert list(second.state.open_trades) == [saved_trade.trade_id]
    restored = second.state.open_trades[saved_trade.trade_id]
    assert restored.entry_price == saved_trade.entry_price
    assert restored.stop_loss == saved_trade.stop_loss


def test_reset_paper_balance_persists(cfg):
    bot = TradingBot(cfg, BotState(), exchange=FakeExchange())
    ok, message = bot.reset_paper_account(42.50)
    assert ok
    assert "42.50" in message
    assert bot._equity() == 42.50

    restarted = TradingBot(cfg, BotState(), exchange=FakeExchange())
    assert restarted.paper_broker.balance == 42.50


def test_reset_refuses_while_paper_trade_is_open(cfg):
    cfg["trading"]["confirm_signals"] = False
    bot = TradingBot(cfg, BotState(), exchange=FakeExchange())
    bot._tick(cfg["trading"]["symbols"])

    ok, message = bot.reset_paper_account(100)
    assert not ok
    assert "Close all paper positions" in message


def test_corrupt_paper_state_falls_back_to_30(cfg):
    state_path = cfg["trading"]["paper_state_file"]
    with open(state_path, "w", encoding="utf-8") as fh:
        fh.write("{not-json")

    bot = TradingBot(cfg, BotState(), exchange=FakeExchange())
    assert bot.paper_broker.balance == 30.0


def test_paper_state_file_contains_balance_and_positions(cfg):
    bot = TradingBot(cfg, BotState(), exchange=FakeExchange())
    ok, _ = bot.reset_paper_account(55)
    assert ok
    with open(cfg["trading"]["paper_state_file"], "r", encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["balance"] == 55.0
    assert data["open_trades"] == []
