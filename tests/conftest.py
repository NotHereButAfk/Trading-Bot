"""Shared test fixtures: synthetic market data and a fake exchange."""

import numpy as np
import pandas as pd
import pytest

from bot.config import load_config


def make_ohlcv(n=300, start=50000.0, drift=0.0, vol=0.002, seed=1, wave=0.0, phase=0.0):
    """Deterministic OHLCV; `wave` adds pullback cycles so oscillators breathe."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    if wave:
        rets += wave * np.sin(np.arange(n) / 8.0 + phase)
    close = start * np.exp(np.cumsum(rets))
    high = close * (1 + np.abs(rng.normal(0, vol, n)))
    low = close * (1 - np.abs(rng.normal(0, vol, n)))
    open_ = np.roll(close, 1)
    open_[0] = start
    volume = rng.uniform(90, 110, n)
    idx = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


# Regimes that reliably produce each signal direction with the default config.
UPTREND = dict(drift=0.002, vol=0.003, wave=0.003, seed=1)
DOWNTREND = dict(drift=-0.0015, vol=0.0025, wave=0.0025, seed=2, phase=3.2)


class FakeExchange:
    """In-memory exchange: serves a fixed candle set and a settable price."""

    def __init__(self, df=None, contract_size=0.001):
        self.df = df if df is not None else make_ohlcv(**UPTREND)
        self.price = float(self.df["close"].iloc[-1])
        self.contract_size = contract_size

    def load_markets(self):
        pass

    def prepare_symbol(self, *args, **kwargs):
        pass

    def fetch_ohlcv(self, symbol, timeframe, limit=300):
        return self.df

    def fetch_last_price(self, symbol):
        return self.price

    def amount_to_contracts(self, symbol, base_amount):
        return round(base_amount / self.contract_size, 0)

    def contracts_to_base(self, symbol, contracts):
        return contracts * self.contract_size


@pytest.fixture
def cfg():
    c = load_config("config.example.yaml")
    c["trading"]["symbols"] = ["BTC/USDT:USDT"]
    c["trading"]["cooldown_minutes"] = 0
    c["strategy"]["volume_filter"] = False  # synthetic volume is flat
    return c
