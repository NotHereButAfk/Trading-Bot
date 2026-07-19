"""Main trading engine: broker abstraction (paper/live) and the trading loop."""

import logging
import time

from . import indicators
from .exchange import HTXFutures
from .notifier import EmailNotifier
from .risk import RiskManager, TradePlan
from .state import BotState, Trade
from .strategy import MultiIndicatorStrategy

log = logging.getLogger("bot.trader")

TAKER_FEE = 0.0005  # HTX linear swap taker fee (0.05%)


class PaperBroker:
    """Simulates fills at market price so strategies can run risk-free."""

    def __init__(self, starting_balance: float):
        self.balance = starting_balance

    def equity(self, state: BotState) -> float:
        unrealized = sum(t.unrealized_pnl for t in state.open_trades.values())
        return self.balance + unrealized

    def open_trade(self, exchange: HTXFutures, symbol: str, plan: TradePlan, leverage: int) -> tuple[float, float]:
        contracts = exchange.amount_to_contracts(symbol, plan.base_amount)
        base = exchange.contracts_to_base(symbol, contracts)
        fee = base * plan.entry_price * TAKER_FEE
        self.balance -= fee
        return contracts, base

    def close_trade(self, exchange: HTXFutures, trade: Trade, exit_price: float) -> float:
        direction = 1.0 if trade.side == "long" else -1.0
        gross = direction * (exit_price - trade.entry_price) * trade.base_amount
        fee = trade.base_amount * exit_price * TAKER_FEE
        pnl = gross - fee
        self.balance += pnl
        return pnl


class LiveBroker:
    """Routes orders to HTX for real."""

    def open_trade(self, exchange: HTXFutures, symbol: str, plan: TradePlan, leverage: int) -> tuple[float, float]:
        contracts = exchange.amount_to_contracts(symbol, plan.base_amount)
        if contracts <= 0:
            raise ValueError(
                f"position size {plan.base_amount} too small for {symbol} contract size"
            )
        exchange.market_open(symbol, plan.side, contracts, leverage)
        base = exchange.contracts_to_base(symbol, contracts)
        return contracts, base

    def close_trade(self, exchange: HTXFutures, trade: Trade, exit_price: float) -> float:
        exchange.market_close(trade.symbol, trade.side, trade.contracts, trade.leverage)
        direction = 1.0 if trade.side == "long" else -1.0
        gross = direction * (exit_price - trade.entry_price) * trade.base_amount
        fee = trade.base_amount * exit_price * TAKER_FEE
        return gross - fee

    def equity(self, exchange: HTXFutures) -> float:
        return exchange.fetch_equity_usdt()


class TradingBot:
    def __init__(self, cfg: dict, state: BotState, exchange: HTXFutures | None = None):
        self.cfg = cfg
        self.trading = cfg["trading"]
        self.state = state
        self.exchange = exchange or HTXFutures(cfg)
        self.strategy = MultiIndicatorStrategy(cfg)
        self.risk = RiskManager(cfg)
        self.notifier = EmailNotifier(cfg)
        self.paper = bool(self.trading["paper_trading"])
        self.paper_broker = PaperBroker(self.trading["paper_starting_balance"])
        self.live_broker = LiveBroker()
        self.state.mode = "paper" if self.paper else "LIVE"
        self._last_candle_ts: dict[str, object] = {}
        self._cooldown_until: dict[str, float] = {}
        self._day_start_equity: float | None = None
        self._day_stamp: str = ""
        self._halted_for_day = False

    # ------------------------------------------------------------------ run

    def run(self):
        symbols = self.trading["symbols"]
        self.state.set_status("connecting to HTX")
        try:
            self.exchange.load_markets()
            if not self.paper:
                for symbol in symbols:
                    self.exchange.prepare_symbol(
                        symbol, self.trading["leverage"], self.trading["margin_mode"]
                    )
            equity = self._equity()
            self.state.set_equity(equity)
        except Exception as exc:
            log.exception("failed to initialize exchange")
            self.state.set_status(f"init error: {exc}")
            self.notifier.notify_error(f"Bot failed to start: {exc}")
            return

        self.state.set_status("running")
        self.notifier.notify_startup(
            self.state.mode, symbols, self.trading["timeframe"], equity
        )
        self.state.log_signal("*", f"Bot started in {self.state.mode} mode")
        log.info("bot running (%s) on %s", self.state.mode, symbols)

        while not self.state.stop_requested.is_set():
            started = time.time()
            try:
                self._tick(symbols)
            except Exception as exc:
                log.exception("error in trading loop")
                self.state.set_status(f"error: {exc}")
                self.notifier.notify_error(f"Trading loop error: {exc}")
            elapsed = time.time() - started
            wait = max(1.0, self.trading["poll_interval_sec"] - elapsed)
            self.state.stop_requested.wait(wait)

        self.state.set_status("stopped")
        log.info("bot stopped")

    def stop(self):
        self.state.stop_requested.set()

    # ----------------------------------------------------------------- tick

    def _tick(self, symbols: list):
        self._check_daily_loss_limit()
        for symbol in symbols:
            price = self.exchange.fetch_last_price(symbol)
            self._manage_open_trades(symbol, price)
            if not self._halted_for_day:
                self._maybe_enter(symbol, price)
        self.state.set_equity(self._equity())
        self.state.set_status("halted (daily loss limit)" if self._halted_for_day else "running")

    def _manage_open_trades(self, symbol: str, price: float):
        open_here = [
            t for t in list(self.state.open_trades.values()) if t.symbol == symbol
        ]
        if not open_here:
            return

        df = self.exchange.fetch_ohlcv(
            symbol, self.trading["timeframe"], self.trading["candle_history"]
        )
        closed = df.iloc[:-1]  # never act on the still-forming candle
        atr_value = float(
            indicators.atr(closed, self.cfg["strategy"]["indicators"]["atr_period"]).iloc[-1]
        )

        for trade in open_here:
            trade.update_mark(price)
            new_stop = self.risk.update_trailing_stop(trade, price, atr_value)
            if new_stop != trade.stop_loss:
                log.info("%s stop moved %.6g -> %.6g", trade.trade_id, trade.stop_loss, new_stop)
                trade.stop_loss = new_stop

            reason = None
            if self.risk.stop_hit(trade, price):
                reason = "stop loss"
            elif self.risk.take_profit_hit(trade, price):
                reason = "take profit"
            else:
                flipped, why = self.strategy.should_exit(closed, trade.side)
                if flipped:
                    reason = why
            if reason:
                self._close_trade(trade, price, reason)

    def _maybe_enter(self, symbol: str, price: float):
        if any(t.symbol == symbol for t in self.state.open_trades.values()):
            return
        if len(self.state.open_trades) >= self.trading["max_open_positions"]:
            return
        if time.time() < self._cooldown_until.get(symbol, 0.0):
            return

        df = self.exchange.fetch_ohlcv(
            symbol, self.trading["timeframe"], self.trading["candle_history"]
        )
        closed = df.iloc[:-1]
        if len(closed) < 60:
            return

        # Only evaluate once per closed candle per symbol.
        last_ts = closed.index[-1]
        if self._last_candle_ts.get(symbol) == last_ts:
            return
        self._last_candle_ts[symbol] = last_ts

        signal = self.strategy.evaluate(closed)
        self.state.log_signal(
            symbol,
            f"score {signal.score:+.1f} adx {signal.adx:.0f} -> {signal.direction}",
        )
        if not signal.is_entry:
            return

        equity = self._equity()
        plan = self.risk.build_plan(signal.direction, price, signal.atr, equity)
        if plan is None:
            log.info("%s: no valid trade plan (equity %.2f)", symbol, equity)
            return

        broker = self.paper_broker if self.paper else self.live_broker
        try:
            contracts, base = broker.open_trade(
                self.exchange, symbol, plan, self.trading["leverage"]
            )
        except Exception as exc:
            log.exception("failed to open %s %s", plan.side, symbol)
            self.state.log_signal(symbol, f"OPEN FAILED: {exc}")
            self.notifier.notify_error(f"Failed to open {plan.side} {symbol}: {exc}")
            return

        trade = Trade(
            trade_id=self.state.next_trade_id(symbol),
            symbol=symbol,
            side=plan.side,
            base_amount=base,
            contracts=contracts,
            entry_price=price,
            stop_loss=plan.stop_loss,
            take_profit=plan.take_profit,
            initial_stop=plan.stop_loss,
            opened_at=time.time(),
            leverage=self.trading["leverage"],
            notional=base * price,
        )
        trade.update_mark(price)
        self.state.add_trade(trade)
        self.state.log_signal(
            symbol, f"OPENED {plan.side} @ {price:.6g} (score {signal.score:+.1f})"
        )
        self.notifier.notify_open(trade, signal.score, signal.reasons)
        log.info("opened %s %s @ %.6g sl %.6g tp %.6g", plan.side, symbol, price,
                 plan.stop_loss, plan.take_profit)

    def _close_trade(self, trade: Trade, price: float, reason: str):
        broker = self.paper_broker if self.paper else self.live_broker
        try:
            pnl = broker.close_trade(self.exchange, trade, price)
        except Exception as exc:
            log.exception("failed to close %s", trade.trade_id)
            self.state.log_signal(trade.symbol, f"CLOSE FAILED: {exc}")
            self.notifier.notify_error(f"Failed to close {trade.trade_id}: {exc}")
            return
        closed = self.state.close_trade(trade.trade_id, price, pnl, reason)
        if closed:
            self.state.log_signal(
                trade.symbol, f"CLOSED {trade.side} @ {price:.6g} pnl {pnl:+.2f} ({reason})"
            )
            self.notifier.notify_close(closed)
        self._cooldown_until[trade.symbol] = (
            time.time() + self.trading["cooldown_minutes"] * 60.0
        )

    # ------------------------------------------------------------- accounting

    def _equity(self) -> float:
        if self.paper:
            return self.paper_broker.equity(self.state)
        return self.live_broker.equity(self.exchange)

    def _check_daily_loss_limit(self):
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if today != self._day_stamp:
            self._day_stamp = today
            self._day_start_equity = self._equity()
            self._halted_for_day = False
        if self._halted_for_day or not self._day_start_equity:
            return
        equity = self._equity()
        loss_pct = (self._day_start_equity - equity) / self._day_start_equity * 100.0
        if loss_pct >= self.cfg["risk"]["max_daily_loss_pct"]:
            self._halted_for_day = True
            msg = (
                f"Daily loss limit reached ({loss_pct:.1f}% >= "
                f"{self.cfg['risk']['max_daily_loss_pct']}%). No new entries until tomorrow."
            )
            log.warning(msg)
            self.state.log_signal("*", msg)
            self.notifier.notify_error(msg)
