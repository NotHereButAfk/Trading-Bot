"""HTX (former Huobi) USDT-margined perpetual futures connection via ccxt."""

import logging
import time

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

    def top_symbols_by_volume(self, n: int, quote: str = "USDT") -> list:
        """Return the `n` most liquid linear `quote`-margined perpetual swaps,
        ranked by 24h quote (USDT) volume. One tickers call, not one per market."""
        self.load_markets()
        candidates = [
            m["symbol"]
            for m in self.client.markets.values()
            if m.get("swap") and m.get("linear") and m.get("active", True)
            and m.get("settle") == quote
        ]
        if not candidates:
            return []
        try:
            tickers = self.client.fetch_tickers(candidates)
        except Exception as exc:  # some venues reject a long symbol list
            log.warning("fetch_tickers(subset) failed (%s); fetching all", exc)
            tickers = self.client.fetch_tickers()

        def volume(sym: str) -> float:
            t = tickers.get(sym) or {}
            qv = t.get("quoteVolume")
            if qv is None:  # derive from base volume x price when absent
                base = t.get("baseVolume") or 0.0
                last = t.get("last") or 0.0
                qv = base * last
            return float(qv or 0.0)

        ranked = sorted(candidates, key=volume, reverse=True)
        return ranked[:n]

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

    def prepare_symbol(self, symbol: str, leverage: int, margin_mode: str) -> list:
        """Set margin mode and leverage.

        HTX rejects a no-op margin/leverage change with an error even though
        nothing is wrong, so those specific "unchanged" errors are downgraded
        to debug. Any *other* failure is returned as a warning string so the
        caller can surface it — on real money, silently trading at the wrong
        leverage is exactly the kind of thing you want shouted about.
        """
        self.load_markets()
        warnings = []
        try:
            self.client.set_margin_mode(margin_mode, symbol, {"leverage": leverage})
        except Exception as exc:
            if _is_benign_setup_error(exc):
                log.debug("set_margin_mode(%s): %s", symbol, exc)
            else:
                warnings.append(f"could not set {margin_mode} margin on {symbol}: {exc}")
        try:
            self.client.set_leverage(leverage, symbol)
        except Exception as exc:
            if _is_benign_setup_error(exc):
                log.debug("set_leverage(%s): %s", symbol, exc)
            else:
                warnings.append(f"could not set {leverage}x leverage on {symbol}: {exc}")
        return warnings

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

    def resolve_fill(self, symbol: str, order: dict, fallback_price: float) -> tuple[float, float]:
        """Return the (average_fill_price, filled_contracts) of a market order.

        The create response may not carry fill details yet, so poll fetch_order
        briefly until the order is closed. Falls back to the pre-order ticker
        price only if the exchange never reports an average — better an
        approximate stop than none.
        """
        order_id = order.get("id")
        avg = order.get("average")
        filled = order.get("filled")
        for _ in range(5):
            if avg and filled:
                break
            if not order_id:
                break
            try:
                fetched = self.client.fetch_order(order_id, symbol)
            except Exception as exc:
                log.warning("fetch_order(%s) failed: %s", order_id, exc)
                break
            avg = fetched.get("average") or avg
            filled = fetched.get("filled") or filled
            if fetched.get("status") == "closed" and avg and filled:
                break
            time.sleep(0.4)
        price = float(avg) if avg else float(fallback_price)
        contracts = float(filled) if filled else 0.0
        return price, contracts

    def fetch_position(self, symbol: str) -> dict | None:
        positions = self.client.fetch_positions([symbol])
        for pos in positions:
            if pos.get("contracts") and float(pos["contracts"]) > 0:
                return pos
        return None

    def fetch_all_positions(self) -> list:
        """All currently-open positions in one call (used at live startup so we
        don't poll 100 symbols individually)."""
        return [
            pos for pos in self.client.fetch_positions()
            if pos.get("contracts") and float(pos["contracts"]) > 0
        ]

    def position_is_flat(self, symbol: str, retries: int = 3) -> bool:
        """True once the exchange reports no open position for `symbol`.

        Retried because a close fill can take a moment to propagate to the
        positions endpoint; a false 'still open' here would wrongly alarm.
        """
        for attempt in range(retries):
            try:
                if self.fetch_position(symbol) is None:
                    return True
            except Exception as exc:
                log.warning("position check for %s failed: %s", symbol, exc)
            if attempt < retries - 1:
                time.sleep(1.0)
        return False


def _is_benign_setup_error(exc: Exception) -> bool:
    """HTX raises on setting margin/leverage to what it already is."""
    text = str(exc).lower()
    benign = ("not modified", "no change", "already", "repeat", "1051", "1050")
    return any(token in text for token in benign)
