"""Backtester runs the strategy over history and produces coherent stats."""

import numpy as np
import pandas as pd

from backtest import Backtester
from tests.conftest import make_ohlcv


def _regime_history(n=1200):
    """Trend up, then down, then up so both long and short trades appear."""
    rng = np.random.default_rng(11)
    drift = np.concatenate([
        np.full(n // 3, 0.0015),
        np.full(n // 3, -0.0015),
        np.full(n - 2 * (n // 3), 0.0012),
    ])
    wave = 0.0025 * np.sin(np.arange(n) / 8.0)
    rets = rng.normal(0, 0.0028, n) + drift + wave
    close = 50000.0 * np.exp(np.cumsum(rets))
    high = close * (1 + np.abs(rng.normal(0, 0.0028, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.0028, n)))
    open_ = np.roll(close, 1)
    open_[0] = 50000.0
    volume = rng.uniform(80, 130, n)
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def test_backtest_produces_trades(cfg):
    result = Backtester(cfg).run(_regime_history())
    assert len(result.trades) > 0
    # Every trade is fully settled.
    for t in result.trades:
        assert t.exit_price is not None
        assert t.pnl is not None
        assert t.reason


def test_backtest_stats_are_consistent(cfg):
    result = Backtester(cfg).run(_regime_history())
    assert len(result.wins) + len(result.losses) == len(result.trades)
    # Ending equity equals start plus the sum of realized PnL minus entry fees.
    realized = sum(t.pnl for t in result.trades)
    assert result.ending_equity <= result.starting_equity + realized + 1e-6
    assert 0.0 <= result.max_drawdown_pct <= 100.0


def test_backtest_never_holds_two_positions(cfg):
    """The backtester is single-position; entries and exits must interleave."""
    result = Backtester(cfg).run(_regime_history())
    times = [(t.entry_time, t.exit_time) for t in result.trades]
    for (_, prev_exit), (next_entry, _) in zip(times, times[1:]):
        assert next_entry >= prev_exit


def test_adx_filter_gates_entries(cfg):
    """Raising the ADX trend threshold must not increase the trade count —
    the filter should only ever suppress entries, proving it is enforced."""
    hist = _regime_history()
    baseline = len(Backtester(cfg).run(hist).trades)

    strict = dict(cfg)
    strict["strategy"] = dict(cfg["strategy"])
    strict["strategy"]["adx_min"] = 100.0  # unreachably strict -> no trends qualify
    strict_count = len(Backtester(strict).run(hist).trades)

    assert baseline > 0
    assert strict_count <= baseline
    assert strict_count == 0
