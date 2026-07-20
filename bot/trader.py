"""Main trading engine: broker abstraction (paper/live) and the trading loop."""

import logging
import time
from dataclasses import dataclass

from . import indicators
from .exchange import HTXFutures
from .notifier import EmailNotifier
from .risk import RiskManager, TradePlan
from .state import BotState, Trade
from .strategy import MultiIndicatorStrategy

log = logging.getLogger("bot.trader")

TAKER_FEE = 0.0005  # HTX linear swap taker fee (0.05%)


class PositionNotFlatError(Exception):
    """Raised when a close order did not actually flatten the position."""


@dataclass
class Fill:
    price: float       # actual average fill price
    contracts: float   # contracts actually filled
    base: float        # filled size in base asset


class PaperBroker:
    """Simulates fills at market price so strategies can run risk-free."""

    def __init__(self, starting_balance: float):
        self.balance = starting_balance

    def equity(self, state: BotState) -> float:
        unrealized = sum(t.unrealized_pnl for t in state.open_trades.values())
        return self.balance + unrealized

    def open_trade(self, exchange: HTXFutures, symbol: str, plan: TradePlan, leverage: int) -> Fill:
        contracts = exchange.amount_to_contracts(symbol, plan.base_amount)
        base = exchange.contracts_to_base(symbol, contracts)
        fee = base * plan.entry_price * TAKER_FEE
        self.balance -= fee
        return Fill(price=plan.entry_price, contracts=contracts, base=base)

    def close_trade(self, exchange: HTXFutures, trade: Trade, exit_price: float) -> tuple[float, float]:
        direction = 1.0 if trade.side == "long" else -1.0
        gross = direction * (exit_price - trade.entry_price) * trade.base_amount
        fee = trade.base_amount * exit_price * TAKER_FEE
        pnl = gross - fee
        self.balance += pnl
        return pnl, exit_price


class LiveBroker:
    """Routes orders to HTX for real."""

    def open_trade(self, exchange: HTXFutures, symbol: str, plan: TradePlan, leverage: int) -> Fill:
        contracts = exchange.amount_to_contracts(symbol, plan.base_amount)
        if contracts <= 0:
            raise ValueError(
                f"position size {plan.base_amount} too small for {symbol} contract size"
            )
        order = exchange.market_open(symbol, plan.side, contracts, leverage)
        # Record the *actual* fill, not the pre-order ticker price, so the
        # stop/target are anchored where the position really opened.
        fill_price, filled = exchange.resolve_fill(symbol, order, plan.entry_price)
        if filled <= 0:
            filled = contracts  # exchange gave no count; trust the requested size
        base = exchange.contracts_to_base(symbol, filled)
        return Fill(price=fill_price, contracts=filled, base=base)

    def close_trade(self, exchange: HTXFutures, trade: Trade, exit_price: float) -> tuple[float, float]:
        order = exchange.market_close(trade.symbol, trade.side, trade.contracts, trade.leverage)
        fill_price, _ = exchange.resolve_fill(trade.symbol, order, exit_price)
        # Never report a close as done until the exchange confirms we are flat.
        if not exchange.position_is_flat(trade.symbol):
            raise PositionNotFlatError(
                f"{trade.symbol} still shows an open position after close order "
                f"{order.get('id')} — check the exchange immediately"
            )
        direction = 1.0 if trade.side == "long" else -1.0
        gross = direction * (fill_price - trade.entry_price) * trade.base_amount
        fee = trade.base_amount * fill_price * TAKER_FEE
        return gross - fee, fill_price

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
        self.has_key = bool(cfg["exchange"]["api_key"]) and bool(cfg["exchange"]["api_secret"])
        self.confirm_mode = bool(self.trading["confirm_signals"])
        self.state.mode = "paper" if self.paper else "LIVE"
        self.state.entry_mode = "manual confirm" if self.confirm_mode else "auto"
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
                self._live_preflight(symbols)
            self._detect_balance()
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
            # wake_trader is set by GUI confirmations so they execute promptly
            self.state.wake_trader.wait(wait)
            self.state.wake_trader.clear()

        self.state.set_status("stopped")
        log.info("bot stopped")

    def stop(self):
        self.state.stop_requested.set()
        self.state.wake_trader.set()

    def _live_preflight(self, symbols: list):
        """Real-money startup checks: confirm leverage/margin took effect and
        warn about positions the bot isn't tracking."""
        for symbol in symbols:
            warnings = self.exchange.prepare_symbol(
                symbol, self.trading["leverage"], self.trading["margin_mode"]
            )
            for warning in warnings:
                log.warning(warning)
                self.state.log_signal(symbol, f"SETUP WARNING: {warning}")
                self.notifier.notify_error(f"Live setup warning: {warning}")

        for symbol in symbols:
            try:
                existing = self.exchange.fetch_position(symbol)
            except Exception as exc:
                log.warning("could not check existing position for %s: %s", symbol, exc)
                continue
            if existing is not None:
                contracts = existing.get("contracts")
                side = existing.get("side")
                msg = (
                    f"{symbol}: an open {side} position ({contracts} contracts) already "
                    "exists on HTX. The bot will NOT manage or close it and will skip "
                    "new entries on this symbol until it is gone."
                )
                log.warning(msg)
                self.state.log_signal(symbol, f"PRE-EXISTING POSITION: {msg}")
                self.notifier.notify_error(msg)

    def _detect_balance(self):
        """Read the real USDT account balance whenever a key is available.

        - Live: sizing already uses the live balance every tick; this just logs
          the detected figure at startup.
        - Paper WITH a key (practice mode): seed the simulated balance from the
          real account so practice reflects your actual account size, unless
          trading.use_real_balance is turned off.
        """
        if not self.has_key:
            log.info("no API key — paper balance is %.2f USDT (configured)",
                     self.paper_broker.balance)
            return
        try:
            detected = self.exchange.fetch_equity_usdt()
        except Exception as exc:
            log.warning("could not detect account balance: %s", exc)
            self.state.log_signal("*", f"could not detect balance: {exc}")
            return
        self.state.log_signal("*", f"detected HTX balance: {detected:.2f} USDT")
        log.info("detected HTX USDT balance: %.2f", detected)
        if self.paper and self.cfg["trading"].get("use_real_balance", True):
            self.paper_broker.balance = detected
            self.state.log_signal(
                "*", f"seeding paper mode with your real balance ({detected:.2f} USDT)"
            )

    # ----------------------------------------------------------------- tick

    def _tick(self, symbols: list):
        self._check_daily_loss_limit()
        for expired in self.state.prune_expired_signals():
            self.state.log_signal(
                expired.symbol, f"signal {expired.signal_id} expired unconfirmed"
            )
        self._execute_confirmed_signals()
        self._execute_close_requests()
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

        if self.confirm_mode:
            pending = self.state.add_pending_signal(
                symbol=symbol,
                direction=signal.direction,
                score=signal.score,
                price=price,
                atr=signal.atr,
                adx=signal.adx,
                reasons=signal.reasons,
                ttl_sec=self.trading["signal_expiry_minutes"] * 60.0,
            )
            self.state.log_signal(
                symbol,
                f"signal {pending.signal_id}: {signal.direction} @ {price:.6g} "
                f"(score {signal.score:+.1f}) — WAITING FOR CONFIRMATION",
            )
            self.notifier.notify_signal(pending)
            log.info("queued %s signal %s for confirmation", signal.direction, symbol)
            return

        self._execute_entry(symbol, signal.direction, price, signal.atr,
                            signal.score, signal.reasons)

    def _execute_confirmed_signals(self):
        for sig in self.state.take_confirmed_signals():
            if self._halted_for_day:
                self.state.log_signal(
                    sig.symbol, f"signal {sig.signal_id} skipped: daily loss halt"
                )
                continue
            if any(t.symbol == sig.symbol for t in self.state.open_trades.values()):
                self.state.log_signal(
                    sig.symbol, f"signal {sig.signal_id} skipped: position already open"
                )
                continue
            if len(self.state.open_trades) >= self.trading["max_open_positions"]:
                self.state.log_signal(
                    sig.symbol, f"signal {sig.signal_id} skipped: max positions reached"
                )
                continue
            # Execute at the CURRENT market price, not the stale signal price.
            price = self.exchange.fetch_last_price(sig.symbol)
            self.state.log_signal(
                sig.symbol, f"signal {sig.signal_id} CONFIRMED — executing {sig.direction}"
            )
            self._execute_entry(sig.symbol, sig.direction, price, sig.atr,
                                sig.score, sig.reasons)

    def _execute_close_requests(self):
        for trade_id in self.state.take_close_requests():
            trade = self.state.open_trades.get(trade_id)
            if trade is None:
                continue
            price = self.exchange.fetch_last_price(trade.symbol)
            self.state.log_signal(
                trade.symbol, f"manual close requested for {trade_id}"
            )
            self._close_trade(trade, price, "manual close")

    def _execute_entry(self, symbol: str, direction: str, price: float,
                       atr_value: float, score: float, reasons: list):
        equity = self._equity()
        plan = self.risk.build_plan(direction, price, atr_value, equity)
        if plan is None:
            log.info("%s: no valid trade plan (equity %.2f)", symbol, equity)
            return

        # For real money, never stack onto a position the exchange already
        # holds (e.g. one opened manually or left over from a previous run).
        if not self.paper:
            try:
                if self.exchange.fetch_position(symbol) is not None:
                    msg = f"{symbol}: exchange already has an open position, skipping entry"
                    log.warning(msg)
                    self.state.log_signal(symbol, msg)
                    return
            except Exception as exc:
                log.warning("could not verify existing position for %s: %s", symbol, exc)

        broker = self.paper_broker if self.paper else self.live_broker
        try:
            fill = broker.open_trade(
                self.exchange, symbol, plan, self.trading["leverage"]
            )
        except Exception as exc:
            log.exception("failed to open %s %s", plan.side, symbol)
            self.state.log_signal(symbol, f"OPEN FAILED: {exc}")
            self.notifier.notify_error(f"Failed to open {plan.side} {symbol}: {exc}")
            return

        # Re-anchor the stop and target to the real fill so the risk *distance*
        # is preserved even if the market moved between signal and fill.
        slippage = fill.price - plan.entry_price
        stop_loss = plan.stop_loss + slippage
        take_profit = plan.take_profit + slippage

        trade = Trade(
            trade_id=self.state.next_trade_id(symbol),
            symbol=symbol,
            side=plan.side,
            base_amount=fill.base,
            contracts=fill.contracts,
            entry_price=fill.price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            initial_stop=stop_loss,
            opened_at=time.time(),
            leverage=self.trading["leverage"],
            notional=fill.base * fill.price,
        )
        trade.update_mark(fill.price)
        self.state.add_trade(trade)
        self.state.log_signal(
            symbol, f"OPENED {plan.side} @ {fill.price:.6g} (score {score:+.1f})"
        )
        self.notifier.notify_open(trade, score, reasons)
        log.info("opened %s %s @ %.6g sl %.6g tp %.6g", plan.side, symbol, fill.price,
                 stop_loss, take_profit)

    def _close_trade(self, trade: Trade, price: float, reason: str):
        broker = self.paper_broker if self.paper else self.live_broker
        try:
            pnl, exit_price = broker.close_trade(self.exchange, trade, price)
        except PositionNotFlatError as exc:
            # The trade is still open and unprotected — keep it in state so the
            # bot retries the close next tick, and shout about it.
            log.critical("close did not flatten %s: %s", trade.trade_id, exc)
            self.state.log_signal(trade.symbol, f"CLOSE INCOMPLETE — STILL OPEN: {exc}")
            self.notifier.notify_error(
                f"URGENT: {trade.trade_id} did not close — position may still be "
                f"open on HTX. {exc}"
            )
            return
        except Exception as exc:
            log.exception("failed to close %s", trade.trade_id)
            self.state.log_signal(trade.symbol, f"CLOSE FAILED: {exc}")
            self.notifier.notify_error(f"Failed to close {trade.trade_id}: {exc}")
            return
        closed = self.state.close_trade(trade.trade_id, exit_price, pnl, reason)
        if closed:
            self.state.log_signal(
                trade.symbol,
                f"CLOSED {trade.side} @ {exit_price:.6g} pnl {pnl:+.2f} ({reason})",
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
