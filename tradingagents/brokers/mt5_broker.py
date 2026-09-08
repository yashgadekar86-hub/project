"""MetaTrader 5 broker integration for TradingAgents (gold trading).

This module wraps the official ``MetaTrader5`` Python package so the agent
team's decision can be turned into a real (or simulated) order on a Windows
MT5 terminal.

Design constraints:

* **Windows-only dependency.** The ``MetaTrader5`` package is published for
  Windows only. We therefore import it lazily inside functions so that the
  rest of the framework (and the test-suite on Linux CI) imports cleanly even
  when the package is not installed.

* **Safe by default.** Every entry point that would touch the account honours
  ``dry_run`` (default ``True``). In dry-run mode the broker computes the full
  order — symbol, direction, volume, price, SL, TP — and returns it *without*
  sending anything, so the pipeline can be exercised end-to-end against a live
  quote stream before a user ever risks capital.

* **No global state.** Each :class:`MT5Broker` instance owns its connection and
  closes it in :meth:`shutdown` / ``__exit__``.

Credentials are read from the environment (``.env``) rather than hard-coded:

    MT5_LOGIN=12345678
    MT5_PASSWORD=********
    MT5_SERVER=YourBroker-Live
    MT5_TERMINAL_PATH=C:/Program Files/MetaTrader 5/terminal64.exe  (optional)
    MT5_SYMBOL=XAUUSD          (optional; auto-detected otherwise)
    MT5_MAGIC=240801           (optional expert-advisor magic number)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class MT5NotAvailableError(RuntimeError):
    """Raised when the ``MetaTrader5`` package cannot be imported.

    Almost always means the code is running off-Windows or the package is not
    installed (``pip install MetaTrader5``).
    """


class MT5ConnectionError(RuntimeError):
    """Raised when the terminal cannot be initialized or logged into."""


class MT5SymbolError(RuntimeError):
    """Raised when the requested symbol cannot be resolved/selected."""


class MT5OrderError(RuntimeError):
    """Raised when an order is rejected by the server (non dry-run)."""


def _load_mt5():
    """Import the MetaTrader5 package lazily with a helpful error."""
    try:
        import MetaTrader5 as mt5  # type: ignore

        return mt5
    except ImportError as exc:  # pragma: no cover - platform specific
        raise MT5NotAvailableError(
            "The 'MetaTrader5' package is not installed. It is Windows-only: "
            "run 'pip install MetaTrader5' on the Windows machine where the "
            "MT5 terminal is installed."
        ) from exc


@dataclass
class MT5Config:
    """Connection + trading configuration, populated from the environment.

    All fields are optional; anything unset falls back to the terminal's
    currently-logged-in account and a sensible gold symbol.
    """

    login: int | None = None
    password: str | None = None
    server: str | None = None
    terminal_path: str | None = None
    symbol: str = "XAUUSD"
    magic: int = 240801
    deviation: int = 20  # max slippage in points
    # Risk sizing
    risk_percent: float = 1.0        # % of free margin / equity risked per trade
    fixed_lots: float | None = None  # override risk sizing with an explicit volume
    # Stops (in price distance). If None the executor derives them from the
    # Trader's plan or a default ATR-based distance.
    stop_loss_distance: float | None = None
    take_profit_distance: float | None = None
    dry_run: bool = True
    # Fallback gold symbols to try if ``symbol`` is not found, in order.
    symbol_candidates: list[str] = field(
        default_factory=lambda: [
            "XAUUSD", "GOLD", "XAUUSD+", "GOLDm", "XAUUSDm", "XAUUSD.",
        ]
    )

    @classmethod
    def from_env(cls, **overrides: Any) -> MT5Config:
        """Build a config from MT5_* environment variables plus overrides."""
        cfg = cls(
            login=_opt_int(os.getenv("MT5_LOGIN")),
            password=os.getenv("MT5_PASSWORD") or None,
            server=os.getenv("MT5_SERVER") or None,
            terminal_path=os.getenv("MT5_TERMINAL_PATH") or None,
            symbol=os.getenv("MT5_SYMBOL", "XAUUSD") or "XAUUSD",
            magic=_opt_int(os.getenv("MT5_MAGIC")) or 240801,
            deviation=_opt_int(os.getenv("MT5_DEVIATION")) or 20,
            risk_percent=float(os.getenv("MT5_RISK_PERCENT", "1.0") or 1.0),
            fixed_lots=_opt_float(os.getenv("MT5_FIXED_LOTS")),
            stop_loss_distance=_opt_float(os.getenv("MT5_SL_DISTANCE")),
            take_profit_distance=_opt_float(os.getenv("MT5_TP_DISTANCE")),
            dry_run=os.getenv("MT5_DRY_RUN", "true").strip().lower()
            in ("1", "true", "yes", "on"),
        )
        for key, value in overrides.items():
            if hasattr(cfg, key) and value is not None:
                setattr(cfg, key, value)
        return cfg


def _opt_int(value: str | None) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _opt_float(value: str | None) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class OrderResult:
    """Outcome of an (attempted) order placement."""

    dry_run: bool
    action: str               # "BUY" | "SELL" | "CLOSE_BUY" | "CLOSE_SELL" | "NONE"
    symbol: str
    volume: float | None = None
    price: float | None = None
    sl: float | None = None
    tp: float | None = None
    retcode: int | None = None
    comment: str = ""
    raw: dict | None = None

    @property
    def ok(self) -> bool:
        if self.dry_run:
            return self.action != "NONE"
        return self.retcode in (10009, 10010)  # DONE / DONE_PARTIAL

    def to_dict(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "action": self.action,
            "symbol": self.symbol,
            "volume": self.volume,
            "price": self.price,
            "sl": self.sl,
            "tp": self.tp,
            "retcode": self.retcode,
            "comment": self.comment,
        }


class MT5Broker:
    """Thin, testable wrapper around a MetaTrader 5 terminal connection."""

    def __init__(self, config: MT5Config | None = None):
        self.config = config or MT5Config.from_env()
        self._mt5 = None
        self._initialized = False
        self._resolved_symbol: str | None = None

    # -- connection lifecycle ------------------------------------------------

    def connect(self) -> MT5Broker:
        """Initialize and (optionally) log into the terminal.

        Raises :class:`MT5ConnectionError` when the terminal cannot start —
        the most common causes being MT5 not installed, the terminal path
        being wrong, or bad credentials.
        """
        mt5 = _load_mt5()
        self._mt5 = mt5

        kwargs: dict[str, Any] = {}
        if self.config.terminal_path:
            kwargs["path"] = self.config.terminal_path
        if self.config.login:
            kwargs["login"] = int(self.config.login)
        if self.config.password:
            kwargs["password"] = self.config.password
        if self.config.server:
            kwargs["server"] = self.config.server

        if not mt5.initialize(**kwargs):
            err = mt5.last_error()
            raise MT5ConnectionError(f"mt5.initialize() failed: {err}")
        self._initialized = True

        # If explicit credentials were given, ensure we are logged in (initialize
        # with login/password usually already does this, but login() is a no-op
        # safety net when it succeeded).
        if self.config.login and self.config.password and not mt5.login(
            int(self.config.login),
            password=self.config.password,
            server=self.config.server or "",
        ):
            err = mt5.last_error()
            mt5.shutdown()
            self._initialized = False
            raise MT5ConnectionError(f"mt5.login() failed: {err}")

        info = mt5.account_info()
        if info is not None:
            logger.info(
                "MT5 connected: login=%s server=%s balance=%.2f %s",
                getattr(info, "login", "?"),
                getattr(info, "server", "?"),
                getattr(info, "balance", 0.0),
                getattr(info, "currency", ""),
            )
        return self

    def shutdown(self) -> None:
        if self._mt5 is not None and self._initialized:
            self._mt5.shutdown()
            self._initialized = False

    def __enter__(self) -> MT5Broker:
        return self.connect()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.shutdown()

    # -- symbol handling -----------------------------------------------------

    def resolve_symbol(self, symbol: str | None = None) -> str:
        """Find a tradeable symbol, trying fallback candidates.

        Brokers name gold inconsistently (``XAUUSD`` vs ``GOLD`` vs
        ``XAUUSD+``...). We ask the terminal for each candidate and pick the
        first that exists; then we ensure it is visible in Market Watch via
        ``symbol_select``.
        """
        if self._mt5 is None:
            raise MT5ConnectionError("Not connected; call connect() first.")
        mt5 = self._mt5

        wanted = symbol or self.config.symbol
        candidates: list[str] = []
        if wanted:
            candidates.append(wanted)
        for cand in self.config.symbol_candidates:
            if cand not in candidates:
                candidates.append(cand)

        for cand in candidates:
            info = mt5.symbol_info(cand)
            if info is None:
                continue
            # Make sure it's selected in Market Watch; many servers hide
            # symbols until selected, which also blocks orders.
            if not getattr(info, "visible", True) and not mt5.symbol_select(cand, True):
                logger.debug("symbol_select(%s) failed: %s", cand, mt5.last_error())
            self._resolved_symbol = cand
            if cand != wanted:
                logger.info("Symbol %r not found; using %r instead.", wanted, cand)
            return cand

        raise MT5SymbolError(
            f"None of the gold symbols {candidates} exist on this MT5 server. "
            "Set MT5_SYMBOL to your broker's exact gold contract name."
        )

    def symbol_info(self, symbol: str | None = None):
        sym = symbol or self._require_symbol()
        info = self._mt5.symbol_info(sym)
        if info is None:
            raise MT5SymbolError(f"symbol_info({sym}) returned None.")
        return info

    def tick(self, symbol: str | None = None):
        """Return the latest tick (has .bid / .ask)."""
        sym = symbol or self._require_symbol()
        tick = self._mt5.symbol_info_tick(sym)
        if tick is None:
            raise MT5SymbolError(f"symbol_info_tick({sym}) returned None.")
        return tick

    # -- account / positions -------------------------------------------------

    def account_info(self):
        info = self._mt5.account_info()
        if info is None:
            raise MT5ConnectionError("account_info() returned None.")
        return info

    def positions(self, symbol: str | None = None):
        sym = symbol or self._require_symbol()
        return list(self._mt5.positions_get(symbol=sym) or [])

    def _require_symbol(self) -> str:
        if self._resolved_symbol is None:
            self._resolved_symbol = self.resolve_symbol()
        return self._resolved_symbol

    # -- sizing --------------------------------------------------------------

    def _normalize_volume(self, volume: float, info) -> float:
        """Clamp and snap a volume to the symbol's min/max/step."""
        vmin = float(getattr(info, "volume_min", 0.01) or 0.01)
        vmax = float(getattr(info, "volume_max", 100.0) or 100.0)
        vstep = float(getattr(info, "volume_step", 0.01) or 0.01)
        volume = max(vmin, min(volume, vmax))
        steps = round(volume / vstep)
        volume = round(steps * vstep, 8)
        return max(vmin, min(volume, vmax))

    def calculate_volume(
        self,
        stop_distance: float,
        symbol: str | None = None,
        risk_percent: float | None = None,
    ) -> float:
        """Risk-based position sizing.

        ``stop_distance`` is the SL distance in price units (e.g. 15.0 for a
        $15 stop on gold). We risk ``risk_percent`` of equity:

            risk_amount   = equity * risk_percent / 100
            loss_per_lot  = stop_distance * contract_size  (in quote currency)
            volume        = risk_amount / loss_per_lot

        For XAUUSD ``contract_size`` is typically 100 (oz per lot), so a $15
        stop costs $1500 per lot.
        """
        info = self.symbol_info(symbol)
        acct = self.account_info()
        equity = float(getattr(acct, "equity", 0.0) or getattr(acct, "balance", 0.0))
        risk_pct = risk_percent if risk_percent is not None else self.config.risk_percent
        contract_size = float(getattr(info, "trade_contract_size", 100.0) or 100.0)

        if stop_distance <= 0:
            raise ValueError("stop_distance must be positive for risk sizing.")

        risk_amount = equity * (risk_pct / 100.0)
        loss_per_lot = stop_distance * contract_size
        if loss_per_lot <= 0:
            raise ValueError("Computed loss per lot is non-positive; check contract size.")
        volume = risk_amount / loss_per_lot
        return self._normalize_volume(volume, info)

    # -- orders --------------------------------------------------------------

    def _filling_mode(self, info) -> int:
        """Pick an order-filling flag the symbol advertises as supported."""
        mt5 = self._mt5
        mode = int(getattr(info, "filling_mode", 0) or 0)
        # Bit flags: 1 = FOK, 2 = IOC. Prefer whichever the server lists.
        if mode & 2:
            return mt5.ORDER_FILLING_IOC
        if mode & 1:
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN

    def market_order(
        self,
        action: str,
        volume: float | None = None,
        sl: float | None = None,
        tp: float | None = None,
        price: float | None = None,
        symbol: str | None = None,
        comment: str = "TradingAgents",
    ) -> OrderResult:
        """Place a market BUY/SELL (or close an existing position).

        ``action`` is one of ``BUY``, ``SELL``, ``CLOSE_BUY``, ``CLOSE_SELL``.
        When ``volume`` is None for an open action we size from risk. Close
        actions read the volume from the open position.
        """
        mt5 = self._mt5
        if mt5 is None:
            raise MT5ConnectionError("Not connected; call connect() first.")

        sym = symbol or self._require_symbol()
        info = self.symbol_info(sym)
        tick = self.tick(sym)
        digits = int(getattr(info, "digits", 2) or 2)

        action_up = action.upper()
        is_close = action_up.startswith("CLOSE_")

        if is_close:
            return self._close_position(action_up, sym, info, tick, digits, comment)

        if action_up == "BUY":
            order_type = mt5.ORDER_TYPE_BUY
            exec_price = price if price is not None else float(tick.ask)
        elif action_up == "SELL":
            order_type = mt5.ORDER_TYPE_SELL
            exec_price = price if price is not None else float(tick.bid)
        else:
            raise MT5OrderError(f"Unknown action {action!r}.")

        # Phase 24 (order protection): a LIVE market order must never be sent
        # without a stop-loss. Dry-run previews may omit it for inspection.
        if not self.config.dry_run and sl is None:
            raise MT5OrderError(
                "REFUSED: live order without a stop-loss. Compute a valid SL "
                "before submitting; unprotected orders are not sent."
            )

        if volume is None:
            # Size from the SL distance (fall back to config / a sane default).
            stop_distance = self.config.stop_loss_distance or (self.config.take_profit_distance or 0.0)
            if not stop_distance:
                stop_distance = self._default_stop_distance(tick, info)
            volume = (
                self.config.fixed_lots
                if self.config.fixed_lots is not None
                else self.calculate_volume(stop_distance, sym)
            )
        volume = self._normalize_volume(float(volume), info)

        exec_price = round(float(exec_price), digits)
        if sl is not None:
            sl = round(float(sl), digits)
        if tp is not None:
            tp = round(float(tp), digits)

        result = OrderResult(
            dry_run=self.config.dry_run,
            action=action_up,
            symbol=sym,
            volume=volume,
            price=exec_price,
            sl=sl,
            tp=tp,
        )

        if self.config.dry_run:
            result.comment = "DRY-RUN: order not sent."
            logger.info("[DRY-RUN] %s %s vol=%s price=%s sl=%s tp=%s",
                        action_up, sym, volume, exec_price, sl, tp)
            return result

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": sym,
            "volume": float(volume),
            "type": order_type,
            "price": exec_price,
            "deviation": int(self.config.deviation),
            "magic": int(self.config.magic),
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(info),
        }
        if sl is not None:
            request["sl"] = sl
        if tp is not None:
            request["tp"] = tp

        trade_result = mt5.orders_send(request)
        if trade_result is None:
            raise MT5OrderError(f"orders_send returned None: {mt5.last_error()}")

        result.retcode = int(trade_result.retcode)
        result.comment = str(getattr(trade_result, "comment", "") or "")
        result.raw = trade_result._asdict() if hasattr(trade_result, "_asdict") else None
        if not result.ok:
            logger.error("Order rejected: retcode=%s comment=%s",
                         result.retcode, result.comment)
        else:
            logger.info("Order placed: %s %s vol=%s retcode=%s",
                        action_up, sym, volume, result.retcode)
        return result

    def _close_position(self, action_up, sym, info, tick, digits, comment) -> OrderResult:
        """Close an open position by netting it with an opposite market order."""
        mt5 = self._mt5
        positions = self.positions(sym)
        if not positions:
            return OrderResult(dry_run=self.config.dry_run, action="NONE", symbol=sym,
                               comment="No open position to close.")

        # CLOSE_BUY closes a long (a BUY position) -> we must SELL to close.
        want_type = 0 if action_up == "CLOSE_BUY" else 1  # 0=BUY,1=SELL in positions
        target = next((p for p in positions if int(p.type) == want_type), None)
        if target is None:
            return OrderResult(dry_run=self.config.dry_run, action="NONE", symbol=sym,
                               comment="No matching open position to close.")

        volume = float(target.volume)
        close_type = mt5.ORDER_TYPE_SELL if want_type == 0 else mt5.ORDER_TYPE_BUY
        exec_price = round(float(tick.bid) if want_type == 0 else float(tick.ask), digits)

        result = OrderResult(
            dry_run=self.config.dry_run,
            action=action_up,
            symbol=sym,
            volume=volume,
            price=exec_price,
        )
        if self.config.dry_run:
            result.comment = "DRY-RUN: close not sent."
            logger.info("[DRY-RUN] %s %s vol=%s", action_up, sym, volume)
            return result

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": sym,
            "volume": volume,
            "type": close_type,
            "position": int(target.ticket),
            "price": exec_price,
            "deviation": int(self.config.deviation),
            "magic": int(self.config.magic),
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(info),
        }
        trade_result = mt5.orders_send(request)
        if trade_result is None:
            raise MT5OrderError(f"orders_send returned None: {mt5.last_error()}")
        result.retcode = int(trade_result.retcode)
        result.comment = str(getattr(trade_result, "comment", "") or "")
        return result

    def _default_stop_distance(self, tick, info) -> float:
        """Fallback SL distance when nothing was configured: ~0.5% of price."""
        price = float((tick.bid + tick.ask) / 2.0)
        return max(price * 0.005, float(getattr(info, "trade_stops_level", 0) or 0))

    # -- convenience ---------------------------------------------------------

    def snapshot(self) -> dict:
        """Return a small account+market snapshot for logging/reports."""
        try:
            acct = self.account_info()
            sym = self._require_symbol()
            tick = self.tick(sym)
            return {
                "login": getattr(acct, "login", None),
                "server": getattr(acct, "server", None),
                "currency": getattr(acct, "currency", None),
                "balance": getattr(acct, "balance", None),
                "equity": getattr(acct, "equity", None),
                "margin_free": getattr(acct, "margin_free", None),
                "symbol": sym,
                "bid": getattr(tick, "bid", None),
                "ask": getattr(tick, "ask", None),
            }
        except Exception as exc:  # pragma: no cover - defensive
            return {"error": str(exc)}
