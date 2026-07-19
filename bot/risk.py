"""Risk management: position sizing, stop-loss / take-profit, trailing stops."""

import logging
from dataclasses import dataclass

log = logging.getLogger("bot.risk")


@dataclass
class TradePlan:
    side: str            # "long" | "short"
    entry_price: float
    stop_loss: float
    take_profit: float
    base_amount: float   # size in base asset (BTC, ETH, ...)
    notional: float      # size in USDT
    risk_amount: float   # USDT at risk if the stop is hit


class RiskManager:
    def __init__(self, cfg: dict):
        self.cfg = cfg["risk"]
        self.leverage = cfg["trading"]["leverage"]

    def build_plan(
        self, side: str, price: float, atr_value: float, equity: float
    ) -> TradePlan | None:
        """Size a trade so that hitting the stop loses `risk_per_trade_pct` of equity."""
        if equity <= 0 or price <= 0 or atr_value <= 0:
            return None

        stop_distance = self.cfg["atr_stop_multiplier"] * atr_value
        tp_distance = stop_distance * self.cfg["take_profit_rr"]
        if side == "long":
            stop_loss = price - stop_distance
            take_profit = price + tp_distance
        else:
            stop_loss = price + stop_distance
            take_profit = price - tp_distance
        if stop_loss <= 0:
            return None

        risk_amount = equity * self.cfg["risk_per_trade_pct"] / 100.0
        base_amount = risk_amount / stop_distance

        # Cap the position so required margin stays inside the account.
        max_notional = equity * self.leverage * self.cfg["max_notional_pct_of_equity"] / 100.0
        notional = base_amount * price
        if notional > max_notional:
            base_amount = max_notional / price
            notional = max_notional
            risk_amount = base_amount * stop_distance
            log.info("position capped by max notional (%.2f USDT)", max_notional)

        return TradePlan(
            side=side,
            entry_price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            base_amount=base_amount,
            notional=notional,
            risk_amount=risk_amount,
        )

    def update_trailing_stop(self, trade, current_price: float, atr_value: float) -> float:
        """Return a (possibly improved) stop for an open trade.

        Once price has moved `breakeven_at_rr` R in our favor the stop moves to
        entry; after that it trails price by `trailing_atr_multiplier` * ATR.
        Stops only ever tighten, never loosen.
        """
        stop = trade.stop_loss
        risk_per_unit = abs(trade.entry_price - trade.initial_stop)
        if risk_per_unit <= 0:
            return stop

        if trade.side == "long":
            gain = current_price - trade.entry_price
            if gain >= self.cfg["breakeven_at_rr"] * risk_per_unit:
                stop = max(stop, trade.entry_price)
            if self.cfg["trailing_stop"]:
                trail = current_price - self.cfg["trailing_atr_multiplier"] * atr_value
                stop = max(stop, trail) if gain > 0 else stop
        else:
            gain = trade.entry_price - current_price
            if gain >= self.cfg["breakeven_at_rr"] * risk_per_unit:
                stop = min(stop, trade.entry_price)
            if self.cfg["trailing_stop"]:
                trail = current_price + self.cfg["trailing_atr_multiplier"] * atr_value
                stop = min(stop, trail) if gain > 0 else stop
        return stop

    def stop_hit(self, trade, price: float) -> bool:
        if trade.side == "long":
            return price <= trade.stop_loss
        return price >= trade.stop_loss

    def take_profit_hit(self, trade, price: float) -> bool:
        if trade.side == "long":
            return price >= trade.take_profit
        return price <= trade.take_profit
