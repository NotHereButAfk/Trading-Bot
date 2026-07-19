"""Position sizing, stop/target and trailing-stop behavior."""

from bot.risk import RiskManager
from bot.state import Trade


def test_sizing_risks_fixed_fraction(cfg):
    risk = RiskManager(cfg)
    equity = 10000.0
    plan = risk.build_plan("long", 50000.0, 500.0, equity)
    assert plan is not None
    expected = equity * cfg["risk"]["risk_per_trade_pct"] / 100.0
    assert abs(plan.risk_amount - expected) < 1e-6


def test_stop_and_target_placement(cfg):
    risk = RiskManager(cfg)
    plan = risk.build_plan("long", 50000.0, 500.0, 10000.0)
    stop_distance = cfg["risk"]["atr_stop_multiplier"] * 500.0
    assert plan.stop_loss == 50000.0 - stop_distance
    assert plan.take_profit == 50000.0 + stop_distance * cfg["risk"]["take_profit_rr"]


def test_short_stop_above_entry(cfg):
    risk = RiskManager(cfg)
    plan = risk.build_plan("short", 50000.0, 500.0, 10000.0)
    assert plan.stop_loss > plan.entry_price
    assert plan.take_profit < plan.entry_price


def test_notional_cap(cfg):
    cfg["risk"]["max_notional_pct_of_equity"] = 10.0
    risk = RiskManager(cfg)
    plan = risk.build_plan("long", 50000.0, 5.0, 10000.0)  # tiny ATR -> huge size
    max_notional = 10000.0 * cfg["trading"]["leverage"] * 0.10
    assert plan.notional <= max_notional + 1e-6


def test_trailing_stop_only_tightens(cfg):
    risk = RiskManager(cfg)
    trade = Trade(
        trade_id="t", symbol="BTC/USDT:USDT", side="long", base_amount=0.1,
        contracts=100, entry_price=50000.0, stop_loss=49000.0, take_profit=52000.0,
        initial_stop=49000.0, opened_at=0.0, leverage=5, notional=5000.0,
    )
    # Price runs up: stop should trail up, never below the previous stop.
    higher = risk.update_trailing_stop(trade, 51500.0, 300.0)
    assert higher >= trade.stop_loss
    trade.stop_loss = higher
    # Price ticks back down a bit: stop must not loosen.
    same = risk.update_trailing_stop(trade, 51000.0, 300.0)
    assert same >= trade.stop_loss


def test_stop_and_tp_detection(cfg):
    risk = RiskManager(cfg)
    trade = Trade(
        trade_id="t", symbol="BTC/USDT:USDT", side="long", base_amount=0.1,
        contracts=100, entry_price=50000.0, stop_loss=49000.0, take_profit=52000.0,
        initial_stop=49000.0, opened_at=0.0, leverage=5, notional=5000.0,
    )
    assert risk.stop_hit(trade, 48999.0)
    assert not risk.stop_hit(trade, 49500.0)
    assert risk.take_profit_hit(trade, 52001.0)
