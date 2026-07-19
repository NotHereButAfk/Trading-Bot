"""Strategy signal generation and indicator sanity."""

import numpy as np

from bot import indicators
from bot.strategy import MultiIndicatorStrategy
from tests.conftest import make_ohlcv, UPTREND, DOWNTREND


def test_uptrend_signals_long(cfg):
    sig = MultiIndicatorStrategy(cfg).evaluate(make_ohlcv(**UPTREND))
    assert sig.direction == "long"
    assert sig.score >= cfg["strategy"]["entry_score"]
    assert sig.adx >= cfg["strategy"]["adx_min"]


def test_downtrend_signals_short(cfg):
    sig = MultiIndicatorStrategy(cfg).evaluate(make_ohlcv(**DOWNTREND))
    assert sig.direction == "short"
    assert sig.score <= -cfg["strategy"]["entry_score"]


def test_flat_market_no_signal(cfg):
    # No trend -> ADX filter should veto any entry.
    sig = MultiIndicatorStrategy(cfg).evaluate(make_ohlcv(drift=0.0, vol=0.0005))
    assert sig.direction == "none"


def test_evaluate_and_evaluate_at_agree(cfg):
    """The live path (evaluate) and backtest path (evaluate_at) must match."""
    strat = MultiIndicatorStrategy(cfg)
    df = make_ohlcv(**UPTREND)
    live = strat.evaluate(df)
    data = indicators.compute_all(df, strat.params)
    at = strat.evaluate_at(data, len(data) - 1)
    assert live.direction == at.direction
    assert live.score == at.score


def test_rsi_bounds():
    close = make_ohlcv(seed=3)["close"]
    rsi = indicators.rsi(close, 14)
    assert rsi.min() >= 0.0 and rsi.max() <= 100.0


def test_atr_positive():
    df = make_ohlcv(seed=4)
    atr = indicators.atr(df, 14).dropna()
    assert (atr > 0).all()


def test_exit_flips_against_position(cfg):
    strat = MultiIndicatorStrategy(cfg)
    # A long held into a strong downtrend should be told to exit.
    flipped, reason = strat.should_exit(make_ohlcv(**DOWNTREND), "long")
    assert flipped and reason
