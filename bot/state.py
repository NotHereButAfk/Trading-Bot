"""Thread-safe shared state between the trading loop and the GUI."""

import csv
import os
import threading
import time
from dataclasses import dataclass, field


@dataclass
class Trade:
    trade_id: str
    symbol: str
    side: str                  # "long" | "short"
    base_amount: float
    contracts: float
    entry_price: float
    stop_loss: float
    take_profit: float
    initial_stop: float
    opened_at: float
    leverage: int
    notional: float
    mark_price: float = 0.0
    unrealized_pnl: float = 0.0
    closed_at: float | None = None
    exit_price: float | None = None
    realized_pnl: float | None = None
    exit_reason: str | None = None

    @property
    def is_open(self) -> bool:
        return self.closed_at is None

    def update_mark(self, price: float):
        self.mark_price = price
        direction = 1.0 if self.side == "long" else -1.0
        self.unrealized_pnl = direction * (price - self.entry_price) * self.base_amount


@dataclass
class SignalEvent:
    timestamp: float
    symbol: str
    text: str


@dataclass
class PendingSignal:
    signal_id: str
    symbol: str
    direction: str            # "long" | "short"
    score: float
    price: float              # price when the signal fired
    atr: float
    adx: float
    reasons: list
    created_at: float
    expires_at: float
    status: str = "pending"   # pending | confirmed | dismissed | expired


class BotState:
    """All mutable state the GUI reads; every access goes through the lock."""

    def __init__(self, trade_log_csv: str | None = None):
        self._lock = threading.RLock()
        self.open_trades: dict[str, Trade] = {}
        self.closed_trades: list[Trade] = []
        self.signals: list[SignalEvent] = []
        self.pending_signals: dict[str, PendingSignal] = {}
        self.equity: float = 0.0
        self.starting_equity: float = 0.0
        self.status: str = "starting"
        self.mode: str = "paper"
        self.entry_mode: str = "auto"   # "auto" | "manual confirm"
        self.last_update: float = 0.0
        self.stop_requested = threading.Event()
        self.wake_trader = threading.Event()
        self._trade_seq = 0
        self._signal_seq = 0
        self._trade_log_csv = trade_log_csv

    # ------------------------------------------------------------- mutation

    def next_trade_id(self, symbol: str) -> str:
        with self._lock:
            self._trade_seq += 1
            return f"{symbol.split('/')[0]}-{self._trade_seq}"

    def add_trade(self, trade: Trade):
        with self._lock:
            self.open_trades[trade.trade_id] = trade

    def close_trade(self, trade_id: str, exit_price: float, realized_pnl: float, reason: str):
        with self._lock:
            trade = self.open_trades.pop(trade_id, None)
            if trade is None:
                return None
            trade.closed_at = time.time()
            trade.exit_price = exit_price
            trade.realized_pnl = realized_pnl
            trade.exit_reason = reason
            self.closed_trades.append(trade)
            self._append_trade_log(trade)
            return trade

    # ---------------------------------------------------- pending signals

    def add_pending_signal(
        self, symbol: str, direction: str, score: float, price: float,
        atr: float, adx: float, reasons: list, ttl_sec: float,
    ) -> PendingSignal:
        """Queue a signal for manual confirmation, replacing any older one
        for the same symbol so the panel always shows the freshest setup."""
        with self._lock:
            for sid, sig in list(self.pending_signals.items()):
                if sig.symbol == symbol:
                    del self.pending_signals[sid]
            self._signal_seq += 1
            now = time.time()
            pending = PendingSignal(
                signal_id=f"S{self._signal_seq}",
                symbol=symbol,
                direction=direction,
                score=score,
                price=price,
                atr=atr,
                adx=adx,
                reasons=list(reasons),
                created_at=now,
                expires_at=now + ttl_sec,
            )
            self.pending_signals[pending.signal_id] = pending
            return pending

    def confirm_signal(self, signal_id: str) -> bool:
        """Called from the GUI thread; the trader picks the signal up."""
        with self._lock:
            sig = self.pending_signals.get(signal_id)
            if sig is None or sig.status != "pending":
                return False
            if time.time() >= sig.expires_at:
                sig.status = "expired"
                del self.pending_signals[signal_id]
                return False
            sig.status = "confirmed"
        self.wake_trader.set()
        return True

    def dismiss_signal(self, signal_id: str) -> bool:
        with self._lock:
            sig = self.pending_signals.pop(signal_id, None)
            if sig is None:
                return False
            sig.status = "dismissed"
            return True

    def take_confirmed_signals(self) -> list[PendingSignal]:
        """Remove and return confirmed signals for the trader to execute."""
        with self._lock:
            taken = [s for s in self.pending_signals.values() if s.status == "confirmed"]
            for sig in taken:
                del self.pending_signals[sig.signal_id]
            return taken

    def prune_expired_signals(self) -> list[PendingSignal]:
        """Remove and return pending signals whose expiry has passed."""
        with self._lock:
            now = time.time()
            expired = [
                s for s in self.pending_signals.values()
                if s.status == "pending" and now >= s.expires_at
            ]
            for sig in expired:
                sig.status = "expired"
                del self.pending_signals[sig.signal_id]
            return expired

    def log_signal(self, symbol: str, text: str, max_keep: int = 200):
        with self._lock:
            self.signals.append(SignalEvent(time.time(), symbol, text))
            if len(self.signals) > max_keep:
                self.signals = self.signals[-max_keep:]

    def set_status(self, status: str):
        with self._lock:
            self.status = status
            self.last_update = time.time()

    def set_equity(self, equity: float):
        with self._lock:
            self.equity = equity
            if self.starting_equity == 0.0:
                self.starting_equity = equity

    # ------------------------------------------------------------- snapshot

    def snapshot(self) -> dict:
        """Copy of everything the GUI needs, taken under the lock."""
        with self._lock:
            return {
                "status": self.status,
                "mode": self.mode,
                "entry_mode": self.entry_mode,
                "equity": self.equity,
                "starting_equity": self.starting_equity,
                "last_update": self.last_update,
                "open_trades": [Trade(**vars(t)) for t in self.open_trades.values()],
                "closed_trades": [Trade(**vars(t)) for t in self.closed_trades[-50:]],
                "pending_signals": [
                    PendingSignal(**vars(s))
                    for s in self.pending_signals.values()
                    if s.status == "pending"
                ],
                "signals": list(self.signals[-100:]),
            }

    # ------------------------------------------------------------ persistence

    def _append_trade_log(self, trade: Trade):
        if not self._trade_log_csv:
            return
        header = [
            "trade_id", "symbol", "side", "base_amount", "entry_price", "exit_price",
            "realized_pnl", "exit_reason", "opened_at", "closed_at",
        ]
        exists = os.path.exists(self._trade_log_csv)
        try:
            with open(self._trade_log_csv, "a", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                if not exists:
                    writer.writerow(header)
                writer.writerow([
                    trade.trade_id, trade.symbol, trade.side,
                    f"{trade.base_amount:.8f}", f"{trade.entry_price:.6f}",
                    f"{trade.exit_price:.6f}", f"{trade.realized_pnl:.4f}",
                    trade.exit_reason,
                    time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(trade.opened_at)),
                    time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(trade.closed_at)),
                ])
        except OSError:
            pass
