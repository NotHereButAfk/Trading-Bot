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
    """In-memory exchange: serves a fixed candle set and a settable price.

    Also simulates the live-order surface (market_open/close, fill resolution,
    position tracking) so the LiveBroker path can be tested without a network.
    """

    def __init__(self, df=None, contract_size=0.001):
        self.df = df if df is not None else make_ohlcv(**UPTREND)
        self.price = float(self.df["close"].iloc[-1])
        self.contract_size = contract_size
        # live simulation state
        self._positions: dict[str, dict] = {}
        self._order_seq = 0
        self.fill_slippage = 0.0     # add to price to simulate a worse fill
        self.leave_open_on_close = False  # simulate a close that didn't flatten
        self.preexisting: dict[str, dict] = {}  # positions present before start

    def load_markets(self):
        pass

    def prepare_symbol(self, *args, **kwargs):
        return []

    def fetch_ohlcv(self, symbol, timeframe, limit=300):
        return self.df

    def fetch_last_price(self, symbol):
        return self.price

    def fetch_equity_usdt(self):
        return 10000.0

    def amount_to_contracts(self, symbol, base_amount):
        return round(base_amount / self.contract_size, 0)

    def contracts_to_base(self, symbol, contracts):
        return contracts * self.contract_size

    # ---- live order simulation ----

    def market_open(self, symbol, side, contracts, leverage):
        self._order_seq += 1
        self._positions[symbol] = {
            "side": side, "contracts": contracts,
            "entryPrice": self.price + self.fill_slippage,
        }
        return {"id": f"ord-{self._order_seq}",
                "average": self.price + self.fill_slippage,
                "filled": contracts, "status": "closed"}

    def market_close(self, symbol, side, contracts, leverage):
        self._order_seq += 1
        if not self.leave_open_on_close:
            self._positions.pop(symbol, None)
        return {"id": f"ord-{self._order_seq}",
                "average": self.price, "filled": contracts, "status": "closed"}

    def resolve_fill(self, symbol, order, fallback_price):
        price = float(order.get("average") or fallback_price)
        filled = float(order.get("filled") or 0.0)
        return price, filled

    def position_is_flat(self, symbol, retries=3):
        return symbol not in self._positions

    def fetch_position(self, symbol):
        return self.preexisting.get(symbol) or self._positions.get(symbol)


@pytest.fixture
def cfg():
    c = load_config("config.example.yaml")
    c["trading"]["symbols"] = ["BTC/USDT:USDT"]
    c["trading"]["cooldown_minutes"] = 0
    c["strategy"]["volume_filter"] = False  # synthetic volume is flat
    return c
