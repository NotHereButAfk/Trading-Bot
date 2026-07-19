"""Tkinter dashboard: open trades, PnL, trade history and the signal log.

Runs on the main thread; the trading loop runs on a daemon thread and the GUI
polls BotState.snapshot() on a timer, so no Tk calls ever cross threads.
"""

import time
import tkinter as tk
from tkinter import ttk

from .state import BotState

BG = "#12141a"
PANEL = "#1b1e27"
FG = "#e6e6e6"
DIM = "#8a8f9e"
GREEN = "#2ecc71"
RED = "#e74c3c"
ACCENT = "#3498db"


class Dashboard:
    def __init__(self, state: BotState, refresh_ms: int = 2000, on_close=None):
        self.state = state
        self.refresh_ms = refresh_ms
        self.on_close = on_close

        self.root = tk.Tk()
        self.root.title("HTX Futures Bot")
        self.root.geometry("1040x680")
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._handle_close)
        self._build_styles()
        self._build_layout()

    # ------------------------------------------------------------------ ui

    def _build_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Treeview",
            background=PANEL, fieldbackground=PANEL, foreground=FG,
            rowheight=26, borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            background="#232734", foreground=FG, borderwidth=0, relief="flat",
        )
        style.map("Treeview", background=[("selected", "#2c3e50")])

    def _build_layout(self):
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=12, pady=(12, 6))

        self.status_var = tk.StringVar(value="starting...")
        self.mode_var = tk.StringVar(value="")
        self.equity_var = tk.StringVar(value="--")
        self.pnl_var = tk.StringVar(value="--")

        tk.Label(header, textvariable=self.mode_var, bg=BG, fg=ACCENT,
                 font=("TkDefaultFont", 14, "bold")).pack(side="left")
        tk.Label(header, textvariable=self.status_var, bg=BG, fg=DIM,
                 font=("TkDefaultFont", 11)).pack(side="left", padx=16)
        self.pnl_label = tk.Label(header, textvariable=self.pnl_var, bg=BG, fg=FG,
                                  font=("TkDefaultFont", 13, "bold"))
        self.pnl_label.pack(side="right")
        tk.Label(header, textvariable=self.equity_var, bg=BG, fg=FG,
                 font=("TkDefaultFont", 13, "bold")).pack(side="right", padx=16)

        # Open trades table
        tk.Label(self.root, text="Open trades", bg=BG, fg=FG, anchor="w",
                 font=("TkDefaultFont", 12, "bold")).pack(fill="x", padx=12)
        open_cols = ("id", "symbol", "side", "size", "entry", "mark", "upnl",
                     "sl", "tp", "opened")
        self.open_tree = ttk.Treeview(self.root, columns=open_cols, show="headings", height=8)
        headings = {
            "id": ("ID", 90), "symbol": ("Symbol", 130), "side": ("Side", 70),
            "size": ("Size", 110), "entry": ("Entry", 110), "mark": ("Mark", 110),
            "upnl": ("uPnL (USDT)", 110), "sl": ("Stop", 110), "tp": ("Target", 110),
            "opened": ("Opened (UTC)", 140),
        }
        for col, (label, width) in headings.items():
            self.open_tree.heading(col, text=label)
            self.open_tree.column(col, width=width, anchor="center")
        self.open_tree.tag_configure("profit", foreground=GREEN)
        self.open_tree.tag_configure("loss", foreground=RED)
        self.open_tree.pack(fill="x", padx=12, pady=(4, 10))

        # Bottom: closed trades + signal log side by side
        bottom = tk.Frame(self.root, bg=BG)
        bottom.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        left = tk.Frame(bottom, bg=BG)
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))
        tk.Label(left, text="Closed trades", bg=BG, fg=FG, anchor="w",
                 font=("TkDefaultFont", 12, "bold")).pack(fill="x")
        closed_cols = ("symbol", "side", "entry", "exit", "pnl", "reason")
        self.closed_tree = ttk.Treeview(left, columns=closed_cols, show="headings")
        for col, label, width in (
            ("symbol", "Symbol", 110), ("side", "Side", 60), ("entry", "Entry", 90),
            ("exit", "Exit", 90), ("pnl", "PnL", 90), ("reason", "Reason", 150),
        ):
            self.closed_tree.heading(col, text=label)
            self.closed_tree.column(col, width=width, anchor="center")
        self.closed_tree.tag_configure("profit", foreground=GREEN)
        self.closed_tree.tag_configure("loss", foreground=RED)
        self.closed_tree.pack(fill="both", expand=True, pady=(4, 0))

        right = tk.Frame(bottom, bg=BG)
        right.pack(side="right", fill="both", expand=True, padx=(6, 0))
        tk.Label(right, text="Signal log", bg=BG, fg=FG, anchor="w",
                 font=("TkDefaultFont", 12, "bold")).pack(fill="x")
        self.log_text = tk.Text(right, bg=PANEL, fg=DIM, relief="flat",
                                state="disabled", wrap="none",
                                font=("TkFixedFont", 9))
        self.log_text.pack(fill="both", expand=True, pady=(4, 0))

    # -------------------------------------------------------------- refresh

    def _refresh(self):
        snap = self.state.snapshot()

        self.mode_var.set(f"HTX Futures Bot — {snap['mode'].upper()}")
        self.status_var.set(f"status: {snap['status']}")
        self.equity_var.set(f"Equity: {snap['equity']:.2f} USDT")
        total_pnl = snap["equity"] - snap["starting_equity"]
        self.pnl_var.set(f"Session PnL: {total_pnl:+.2f} USDT")
        self.pnl_label.configure(fg=GREEN if total_pnl >= 0 else RED)

        self.open_tree.delete(*self.open_tree.get_children())
        for t in snap["open_trades"]:
            tag = "profit" if t.unrealized_pnl >= 0 else "loss"
            self.open_tree.insert("", "end", values=(
                t.trade_id, t.symbol, t.side.upper(),
                f"{t.base_amount:.6g}", f"{t.entry_price:.6g}", f"{t.mark_price:.6g}",
                f"{t.unrealized_pnl:+.2f}", f"{t.stop_loss:.6g}", f"{t.take_profit:.6g}",
                time.strftime("%m-%d %H:%M", time.gmtime(t.opened_at)),
            ), tags=(tag,))

        self.closed_tree.delete(*self.closed_tree.get_children())
        for t in reversed(snap["closed_trades"]):
            pnl = t.realized_pnl or 0.0
            tag = "profit" if pnl >= 0 else "loss"
            self.closed_tree.insert("", "end", values=(
                t.symbol, t.side.upper(), f"{t.entry_price:.6g}",
                f"{t.exit_price:.6g}", f"{pnl:+.2f}", t.exit_reason,
            ), tags=(tag,))

        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        for event in reversed(snap["signals"]):
            stamp = time.strftime("%H:%M:%S", time.gmtime(event.timestamp))
            self.log_text.insert("end", f"{stamp}  {event.symbol:<14} {event.text}\n")
        self.log_text.configure(state="disabled")

        self.root.after(self.refresh_ms, self._refresh)

    # ------------------------------------------------------------------ run

    def _handle_close(self):
        if self.on_close:
            self.on_close()
        self.root.destroy()

    def run(self):
        self.root.after(200, self._refresh)
        self.root.mainloop()
