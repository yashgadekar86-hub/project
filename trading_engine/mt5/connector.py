"""
MetaTrader 5 connector.

This module provides a safe, typed wrapper around the `MetaTrader5` Python package
(native on Windows; via Wine on Linux is experimental).

It:
  * Lazily imports MT5 so the rest of the system works on machines without MT5.
  * Never stores raw credentials in plaintext (password is passed in-memory only).
  * Detects terminal, login, account info, symbol specs, tick data, OHLCV, orders, positions.
  * Returns typed dataclasses rather than raw namedtuples.
  * Raises typed MT5ConnectionError / MT5Error so callers can fail safely.
"""
from __future__ import annotations

import logging
import os
import platform
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("aifx.mt5")


# ----- Exceptions -----

class MT5Error(RuntimeError):
    """Generic MT5 error."""


class MT5ConnectionError(MT5Error):
    """Raised when MT5 cannot be initialized / connected."""


class MT5AuthError(MT5ConnectionError):
    """Authentication failed."""


class MT5SymbolError(MT5Error):
    """Symbol unavailable or invalid."""


class MT5OrderError(MT5Error):
    """Order placement / modification / close failed."""


# ----- Optional import of MetaTrader5 -----

_MT5_MODULE: Optional[Any] = None
_MT5_IMPORT_ERROR: Optional[str] = None


def _load_mt5():
    global _MT5_MODULE, _MT5_IMPORT_ERROR
    if _MT5_MODULE is not None:
        return _MT5_MODULE
    if _MT5_IMPORT_ERROR is not None:
        return None
    try:
        import MetaTrader5 as mt5  # type: ignore
        _MT5_MODULE = mt5
        return _MT5_MODULE
    except ImportError as e:
        _MT5_IMPORT_ERROR = f"MetaTrader5 package not available: {e}"
        logger.warning(_MT5_IMPORT_ERROR)
        _MT5_MODULE = None
        return None


def is_mt5_available() -> bool:
    """Return True if the MetaTrader5 Python package is importable."""
    return _load_mt5() is not None


def platform_supported() -> bool:
    """MT5 native is Windows only; Linux needs Wine. We allow Linux users to try."""
    return platform.system() in ("Windows", "Linux", "Darwin")


# ----- Data classes -----

@dataclass
class MT5AccountInfo:
    login: int
    server: str
    broker: str
    name: str
    currency: str
    leverage: int
    balance: float
    equity: float
    margin: float
    margin_free: float
    margin_level: Optional[float]
    account_type: str  # demo / contest / real
    trade_mode: int
    limit_orders: int
    margin_so_mode: int
    margin_so_call: float
    margin_so_so: float

    @property
    def is_live(self) -> bool:
        return self.account_type.lower() in ("real", "live")


@dataclass
class MT5SymbolSpec:
    name: str
    broker_name: str
    path: str
    description: str
    digits: int
    point: float
    pip_size: float
    contract_size: float
    lot_min: float
    lot_max: float
    lot_step: float
    tick_size: float
    tick_value: float
    currency_base: str
    currency_profit: str
    currency_margin: str
    spread: float
    spread_float: bool
    stops_level: int
    freeze_level: int
    trade_mode: int
    trade_exemode: int
    trade_calc_mode: int
    swap_mode: int
    swap_long: float
    swap_short: float
    visible: bool


@dataclass
class MT5Tick:
    symbol: str
    time: datetime
    bid: float
    ask: float
    last: float
    volume: int
    flags: int

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass
class MT5Candle:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    spread: int
    volume: int


@dataclass
class MT5Position:
    ticket: int
    symbol: str
    direction: str  # BUY / SELL (0=buy, 1=sell)
    volume: float
    open_price: float
    current_price: float
    sl: float
    tp: float
    profit: float
    swap: float
    commission: float
    open_time: datetime
    comment: str
    magic: int


@dataclass
class MT5OrderResult:
    retcode: int
    deal: Optional[int]
    order: Optional[int]
    volume: float
    price: float
    bid: float
    ask: float
    comment: str
    request_id: int
    retcode_external: int

    @property
    def successful(self) -> bool:
        # MT5 retcode in [10009, 10025) indicates successful trade server response
        return 10009 <= self.retcode < 10025


# ----- Main connector class -----

class MT5Connector:
    """
    Safe MT5 connector. One instance per (login, server). Use as a context manager
    where possible to ensure proper shutdown.
    """

    def __init__(
        self,
        login: Optional[int] = None,
        password: Optional[str] = None,
        server: Optional[str] = None,
        terminal_path: Optional[str] = None,
    ) -> None:
        self.login = login
        self.password = password
        self.server = server
        self.terminal_path = terminal_path
        self._connected = False
        self._mt5 = _load_mt5()

    # ----- Lifecycle -----

    def initialize(self) -> bool:
        if self._mt5 is None:
            raise MT5ConnectionError(
                "MetaTrader5 package not available. Install it on Windows with "
                "`pip install MetaTrader5`, or run on a Windows machine with MT5 terminal."
            )
        kwargs: Dict[str, Any] = {}
        if self.terminal_path:
            kwargs["path"] = self.terminal_path
        if self.login:
            kwargs["login"] = self.login
        if self.password:
            kwargs["password"] = self.password
        if self.server:
            kwargs["server"] = self.server

        # `shutdown` any previous session first to be safe
        try:
            self._mt5.shutdown()
        except Exception:
            pass

        ok = self._mt5.initialize(**kwargs)
        if not ok:
            code, msg = self.last_error()
            raise MT5ConnectionError(f"MT5 initialize failed: code={code}, msg={msg}")

        # If we have credentials, explicitly login (otherwise rely on terminal autologin).
        if self.login and self.password and self.server:
            login_ok = self._mt5.login(
                login=self.login, password=self.password, server=self.server
            )
            if not login_ok:
                code, msg = self.last_error()
                self._mt5.shutdown()
                raise MT5AuthError(f"MT5 login failed: code={code}, msg={msg}")

        self._connected = True
        logger.info(
            "MT5 initialized successfully%s",
            f" (login={self.login}, server={self.server})" if self.login else "",
        )
        return True

    def shutdown(self) -> None:
        if self._mt5 is not None:
            try:
                self._mt5.shutdown()
            except Exception:
                pass
        self._connected = False

    def __enter__(self) -> "MT5Connector":
        self.initialize()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.shutdown()

    def last_error(self) -> Tuple[int, str]:
        if self._mt5 is None:
            return (-1, "mt5 module not loaded")
        code, msg = self._mt5.last_error()
        return int(code), str(msg)

    @property
    def connected(self) -> bool:
        return self._connected

    # ----- Account info -----

    def get_account_info(self) -> MT5AccountInfo:
        self._ensure_connected()
        ai = self._mt5.account_info()
        if ai is None:
            code, msg = self.last_error()
            raise MT5ConnectionError(f"account_info() failed: code={code}, msg={msg}")
        # Map trade_mode to human account_type
        # MT5: ACCOUNT_TRADE_MODE_DEMO=0, ACCOUNT_TRADE_MODE_CONTEST=1, ACCOUNT_TRADE_MODE_REAL=2
        trade_mode = int(getattr(ai, "trade_mode", -1))
        type_map = {0: "demo", 1: "contest", 2: "real"}
        return MT5AccountInfo(
            login=int(ai.login),
            server=str(getattr(ai, "server", "")),
            broker=str(getattr(ai, "company", "")),
            name=str(getattr(ai, "name", "")),
            currency=str(ai.currency),
            leverage=int(ai.leverage),
            balance=float(ai.balance),
            equity=float(ai.equity),
            margin=float(ai.margin),
            margin_free=float(ai.margin_free),
            margin_level=float(ai.margin_level) if ai.margin_level is not None else None,
            account_type=type_map.get(trade_mode, "unknown"),
            trade_mode=trade_mode,
            limit_orders=int(getattr(ai, "limit_orders", 0)),
            margin_so_mode=int(getattr(ai, "margin_so_mode", 0)),
            margin_so_call=float(getattr(ai, "margin_so_call", 0)),
            margin_so_so=float(getattr(ai, "margin_so_so", 0)),
        )

    # ----- Symbol discovery & specs -----

    def list_symbols(self, group: Optional[str] = None) -> List[str]:
        self._ensure_connected()
        if group:
            syms = self._mt5.symbols_get(group=group)
        else:
            syms = self._mt5.symbols_get()
        if syms is None:
            return []
        return [s.name for s in syms]

    def get_symbol_spec(self, symbol: str) -> MT5SymbolSpec:
        self._ensure_connected()
        # Make sure the symbol is selected / visible in MarketWatch
        if not self._mt5.symbol_select(symbol, True):
            code, msg = self.last_error()
            raise MT5SymbolError(f"symbol_select({symbol}) failed: code={code}, msg={msg}")
        info = self._mt5.symbol_info(symbol)
        if info is None:
            code, msg = self.last_error()
            raise MT5SymbolError(f"symbol_info({symbol}) failed: code={code}, msg={msg}")

        digits = int(info.digits)
        point = float(info.point)
        # Pip size heuristic: for 5-digit / 3-digit pairs pip = 10 points;
        # for 3/2-digit JPY pairs pip = 10 points as well (standard pip).
        pip_size = point * (10 if digits in (3, 5) else 1)

        return MT5SymbolSpec(
            name=str(info.name),
            broker_name=str(getattr(info, "broker", info.name)),
            path=str(getattr(info, "path", "")),
            description=str(getattr(info, "description", "")),
            digits=digits,
            point=point,
            pip_size=pip_size,
            contract_size=float(getattr(info, "trade_contract_size", 100000)),
            lot_min=float(info.volume_min),
            lot_max=float(info.volume_max),
            lot_step=float(info.volume_step),
            tick_size=float(getattr(info, "trade_tick_size", point)),
            tick_value=float(getattr(info, "trade_tick_value", 0)),
            currency_base=str(getattr(info, "currency_base", "")),
            currency_profit=str(getattr(info, "currency_profit", "")),
            currency_margin=str(getattr(info, "currency_margin", "")),
            spread=float(getattr(info, "spread", 0)) * point,
            spread_float=bool(getattr(info, "spread_float", False)),
            stops_level=int(getattr(info, "stops_level", 0)),
            freeze_level=int(getattr(info, "freeze_level", 0)),
            trade_mode=int(getattr(info, "trade_mode", 0)),
            trade_exemode=int(getattr(info, "trade_exemode", 0)),
            trade_calc_mode=int(getattr(info, "trade_calc_mode", 0)),
            swap_mode=int(getattr(info, "swap_mode", 0)),
            swap_long=float(getattr(info, "swap_long", 0)),
            swap_short=float(getattr(info, "swap_short", 0)),
            visible=bool(getattr(info, "visible", True)),
        )

    def symbol_exists(self, symbol: str) -> bool:
        try:
            self.get_symbol_spec(symbol)
            return True
        except MT5SymbolError:
            return False

    # ----- Tick / candle data -----

    def get_tick(self, symbol: str) -> MT5Tick:
        self._ensure_connected()
        t = self._mt5.symbol_info_tick(symbol)
        if t is None:
            code, msg = self.last_error()
            raise MT5SymbolError(f"symbol_info_tick({symbol}) failed: code={code}, msg={msg}")
        return MT5Tick(
            symbol=symbol,
            time=datetime.fromtimestamp(int(t.time), tz=timezone.utc),
            bid=float(t.bid),
            ask=float(t.ask),
            last=float(getattr(t, "last", 0.0) or 0.0),
            volume=int(getattr(t, "volume", 0) or 0),
            flags=int(getattr(t, "flags", 0) or 0),
        )

    def get_candles(
        self,
        symbol: str,
        timeframe: str = "H1",
        count: int = 500,
        start_pos: int = 0,
    ) -> List[MT5Candle]:
        """Get OHLCV candles. `timeframe` is a string ('M1','M5','M15','M30','H1','H4','D1')."""
        self._ensure_connected()
        tf = self._timeframe_to_mt5(timeframe)
        rates = self._mt5.copy_rates_from_pos(symbol, tf, start_pos, count)
        if rates is None:
            code, msg = self.last_error()
            raise MT5Error(f"copy_rates_from_pos({symbol},{timeframe}) failed: code={code}, msg={msg}")
        out: List[MT5Candle] = []
        for r in rates:
            out.append(MT5Candle(
                time=datetime.fromtimestamp(int(r["time"]), tz=timezone.utc),
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                tick_volume=int(r.get("tick_volume", 0)),
                spread=int(r.get("spread", 0)),
                volume=int(r.get("real_volume", 0)),
            ))
        return out

    # ----- Positions -----

    def get_positions(self, symbol: Optional[str] = None) -> List[MT5Position]:
        self._ensure_connected()
        if symbol:
            positions = self._mt5.positions_get(symbol=symbol)
        else:
            positions = self._mt5.positions_get()
        if positions is None:
            return []
        out: List[MT5Position] = []
        for p in positions:
            out.append(MT5Position(
                ticket=int(p.ticket),
                symbol=str(p.symbol),
                direction="BUY" if int(p.type) == 0 else "SELL",
                volume=float(p.volume),
                open_price=float(p.price_open),
                current_price=float(p.price_current),
                sl=float(p.sl) if p.sl else 0.0,
                tp=float(p.tp) if p.tp else 0.0,
                profit=float(p.profit),
                swap=float(p.swap),
                commission=float(getattr(p, "commission", 0.0)),
                open_time=datetime.fromtimestamp(int(p.time), tz=timezone.utc),
                comment=str(getattr(p, "comment", "")),
                magic=int(getattr(p, "magic", 0)),
            ))
        return out

    # ----- Order execution -----

    def order_send(self, request: Dict[str, Any]) -> MT5OrderResult:
        self._ensure_connected()
        result = self._mt5.order_send(request)
        if result is None:
            code, msg = self.last_error()
            raise MT5OrderError(f"order_send failed: code={code}, msg={msg}, request={request}")
        return MT5OrderResult(
            retcode=int(result.retcode),
            deal=int(result.deal) if getattr(result, "deal", None) else None,
            order=int(result.order) if getattr(result, "order", None) else None,
            volume=float(getattr(result, "volume", 0)),
            price=float(getattr(result, "price", 0)),
            bid=float(getattr(result, "bid", 0)),
            ask=float(getattr(result, "ask", 0)),
            comment=str(getattr(result, "comment", "")),
            request_id=int(getattr(result, "request_id", 0)),
            retcode_external=int(getattr(result, "retcode_external", 0)),
        )

    # ----- Helpers -----

    def _ensure_connected(self) -> None:
        if self._mt5 is None:
            raise MT5ConnectionError("MT5 module not loaded.")
        if not self._connected:
            # Try auto-init without creds (terminal autologin) if possible
            self.initialize()

    def _timeframe_to_mt5(self, tf: str):
        self._ensure_connected()
        mapping = {
            "M1":  self._mt5.TIMEFRAME_M1,
            "M5":  self._mt5.TIMEFRAME_M5,
            "M15": self._mt5.TIMEFRAME_M15,
            "M30": self._mt5.TIMEFRAME_M30,
            "H1":  self._mt5.TIMEFRAME_H1,
            "H4":  self._mt5.TIMEFRAME_H4,
            "D1":  self._mt5.TIMEFRAME_D1,
            "W1":  self._mt5.TIMEFRAME_W1,
            "MN1": self._mt5.TIMEFRAME_MN1,
        }
        if tf not in mapping:
            raise MT5Error(f"Unknown timeframe: {tf}")
        return mapping[tf]


# ----- Convenience helpers -----

@contextmanager
def mt5_session(
    login: Optional[int] = None,
    password: Optional[str] = None,
    server: Optional[str] = None,
    terminal_path: Optional[str] = None,
):
    conn = MT5Connector(login=login, password=password, server=server, terminal_path=terminal_path)
    try:
        conn.initialize()
        yield conn
    finally:
        conn.shutdown()


def discover_terminal_path() -> Optional[str]:
    """Best-effort detection of installed MT5 terminal."""
    if platform.system() == "Windows":
        candidates = [
            r"C:\Program Files\MetaTrader 5\terminal64.exe",
            r"C:\Program Files (x86)\MetaTrader 5\terminal.exe",
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
    elif platform.system() == "Linux":
        candidates = [
            os.path.expanduser("~/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe"),
            os.path.expanduser("~/.mt5/drive_c/Program Files/MetaTrader 5/terminal64.exe"),
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
    return None
