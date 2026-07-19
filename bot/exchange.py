"""HTX (former Huobi) USDT-margined perpetual futures connection via ccxt."""

import logging

import ccxt
import pandas as pd

log = logging.getLogger("bot.exchange")


class HTXFutures:
    """Thin wrapper around ccxt's htx exchange for linear perpetual swaps."""

    def __init__(self, cfg: dict):
        ex_cfg = cfg["exchange"]
        self.trading_cfg = cfg["trading"]
        self.client = ccxt.htx(
            {
                "apiKey": ex_cfg["api_key"],
                "secret": ex_cfg["api_secret"],
                "enableRateLimit": True,
                "options": {"defaultType": "swap", "defaultSubType": "linear"},
            }
        )
        if ex_cfg.get("testnet"):
            self.client.set_sandbox_mode(True)
        self._markets_loaded = False

    def load_markets(self):
        if not self._markets_loaded:
            self.client.load_markets()
            self._markets_loaded = True

    # ------------------------------------------------------------------ data

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
        self.load_markets()
        raw = self.client.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(
            raw, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df.set_index("timestamp")

    def fetch_last_price(self, symbol: str) -> float:
        ticker = self.client.fetch_ticker(symbol)
        return float(ticker["last"])

    # --------------------------------------------------------------- account

    def fetch_equity_usdt(self) -> float:
        balance = self.client.fetch_balance()
        usdt = balance.get("USDT", {})
        total = usdt.get("total")
        if total is None:
            free = usdt.get("free") or 0.0
            used = usdt.get("used") or 0.0
            total = free + used
        return float(total or 0.0)

    def prepare_symbol(self, symbol: str, leverage: int, margin_mode: str):
        """Set margin mode and leverage; HTX rejects no-op changes, ignore those."""
        self.load_markets()
        try:
            self.client.set_margin_mode(margin_mode, symbol, {"leverage": leverage})
        except Exception as exc:  # already set / not required on this account
            log.debug("set_margin_mode(%s): %s", symbol, exc)
        try:
            self.client.set_leverage(leverage, symbol)
        except Exception as exc:
            log.debug("set_leverage(%s): %s", symbol, exc)

    # ---------------------------------------------------------------- orders

    def amount_to_contracts(self, symbol: str, base_amount: float) -> float:
        """Convert a base-asset quantity (e.g. BTC) into HTX contract units."""
        self.load_markets()
        market = self.client.market(symbol)
        contract_size = market.get("contractSize") or 1.0
        contracts = base_amount / contract_size
        return float(self.client.amount_to_precision(symbol, contracts))

    def contracts_to_base(self, symbol: str, contracts: float) -> float:
        self.load_markets()
        market = self.client.market(symbol)
        return contracts * (market.get("contractSize") or 1.0)

    def market_open(self, symbol: str, side: str, contracts: float, leverage: int) -> dict:
        """Open a position with a market order. side: 'long' -> buy, 'short' -> sell."""
        order_side = "buy" if side == "long" else "sell"
        params = {"offset": "open", "lever_rate": leverage}
        order = self.client.create_order(symbol, "market", order_side, contracts, None, params)
        log.info("opened %s %s x%s contracts: order %s", side, symbol, contracts, order.get("id"))
        return order

    def market_close(self, symbol: str, side: str, contracts: float, leverage: int) -> dict:
        """Close a position: closing a long sells, closing a short buys."""
        order_side = "sell" if side == "long" else "buy"
        params = {"offset": "close", "lever_rate": leverage, "reduceOnly": True}
        order = self.client.create_order(symbol, "market", order_side, contracts, None, params)
        log.info("closed %s %s x%s contracts: order %s", side, symbol, contracts, order.get("id"))
        return order

    def fetch_position(self, symbol: str) -> dict | None:
        positions = self.client.fetch_positions([symbol])
        for pos in positions:
            if pos.get("contracts") and float(pos["contracts"]) > 0:
                return pos
        return None
